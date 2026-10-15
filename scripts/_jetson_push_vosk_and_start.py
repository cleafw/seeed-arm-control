# -*- coding: utf-8 -*-
"""SCP vosk model from PC → Jetson, unzip, restart voice with device listen."""
from __future__ import annotations

import os
import time

import paramiko

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOCAL_ZIP = os.path.join(ROOT, ".deploy-cache", "vosk-model-small-cn-0.22.zip")
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
MODEL = f"{REMOTE}/models/vosk-model-small-cn-0.22"
REMOTE_ZIP = "/tmp/vosk-model-small-cn-0.22-full.zip"


def main() -> int:
    assert os.path.getsize(LOCAL_ZIP) > 40_000_000, LOCAL_ZIP
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)

    def run(cmd: str, timeout: int = 180) -> None:
        print("$", cmd[:200], flush=True)
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
        if out.strip():
            print(out[-5000:], flush=True)
        if err.strip():
            print("ERR", err[-1500:], flush=True)
        print(f"[exit {code}]", flush=True)

    print(f"uploading {os.path.getsize(LOCAL_ZIP)} bytes…", flush=True)
    sftp = c.open_sftp()
    sftp.put(LOCAL_ZIP, REMOTE_ZIP)
    sftp.close()
    print("upload done", flush=True)

    run(
        f"echo {PWD} | sudo -S bash -lc '"
        f"mkdir -p {REMOTE}/models; "
        f"rm -rf {MODEL}; "
        f"unzip -qo {REMOTE_ZIP} -d {REMOTE}/models; "
        f"chown -R seeed:seeed {REMOTE}/models; "
        f"ls {MODEL} | head; "
        f"test -d {MODEL}"
        f"'"
    )

    # ensure env keys
    run(
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "p=Path('/home/seeed/seeed/ENV/seeed-arm-control.env')\n"
        "text=p.read_text(encoding='utf-8') if p.exists() else ''\n"
        "keys={\n"
        " 'VOICE_ENABLED':'1',\n"
        " 'VOICE_HEALTH_URL':'http://127.0.0.1:1883/health',\n"
        " 'VOICE_POLICY':'follow_first',\n"
        " 'VOICE_DEVICE_LISTEN':'1',\n"
        f" 'VOICE_VOSK_MODEL':'{MODEL}',\n"
        " 'VOICE_ALSA_DEVICE':'plughw:CARD=ArrayUAC10,DEV=0',\n"
        " 'REBOT_CORE_URL':'http://127.0.0.1:1882',\n"
        "}\n"
        "lines=[]; seen=set()\n"
        "for ln in text.splitlines():\n"
        "  if (not ln.strip()) or ln.lstrip().startswith('#') or '=' not in ln:\n"
        "    lines.append(ln); continue\n"
        "  k=ln.split('=',1)[0].strip()\n"
        "  if k in keys:\n"
        "    lines.append(f'{k}={keys[k]}'); seen.add(k)\n"
        "  else: lines.append(ln)\n"
        "for k,v in keys.items():\n"
        "  if k not in seen: lines.append(f'{k}={v}')\n"
        "p.write_text('\\n'.join(lines).rstrip()+'\\n', encoding='utf-8')\n"
        "print('env patched')\n"
        "PY"
    )

    # restart voice with env (systemd preferred)
    run(
        f"echo {PWD} | sudo -S bash -lc '"
        f"systemctl stop seeed-arm-voice.service || true; "
        f"pkill -f \"uvicorn voice.app:app\" || true; "
        f"sleep 1; "
        f"systemctl start seeed-arm-voice.service || true; "
        f"sleep 2; "
        f"if ! ss -lptn | grep -q 1883; then "
        f"  cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
        f"  export REBOT_CORE_URL=http://127.0.0.1:1882 VOICE_DEVICE_LISTEN=1 "
        f"  VOICE_VOSK_MODEL={MODEL} VOICE_ALSA_DEVICE=plughw:CARD=ArrayUAC10,DEV=0 && "
        f"  setsid -f {REMOTE}/.venv/bin/uvicorn voice.app:app --host 0.0.0.0 --port 1883 "
        f"  >/home/seeed/seeed/ENV/seeed-arm-voice.log 2>&1 </dev/null; "
        f"fi; "
        f"sleep 5; "
        f"ss -lptn | grep -E \"1882|1883\" || true; "
        f"systemctl is-active seeed-arm-control seeed-arm-voice || true"
        f"'"
    )

    run(
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print('health', urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=8).read().decode())\n"
        "print('cap', urllib.request.urlopen('http://127.0.0.1:1882/api/voice/capability',timeout=8).read().decode())\n"
        "PY"
    )
    run("tail -n 40 /home/seeed/seeed/ENV/seeed-arm-voice.log 2>/dev/null || journalctl -u seeed-arm-voice -n 40 --no-pager")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
