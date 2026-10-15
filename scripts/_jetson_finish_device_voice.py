# -*- coding: utf-8 -*-
"""Finish vosk model + systemd on Jetson (vosk wheel already installed)."""
from __future__ import annotations

import paramiko

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
ENV = "/home/seeed/seeed/ENV/seeed-arm-control.env"
MODEL = f"{REMOTE}/models/vosk-model-small-cn-0.22"
ZIP = "/tmp/vosk-model-small-cn-0.22.zip"
URL = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)

    def run(cmd: str, timeout: int = 600) -> int:
        print("$", cmd[:220], flush=True)
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
        if out.strip():
            print(out[-8000:], flush=True)
        if err.strip():
            print("ERR", err[-3000:], flush=True)
        print(f"[exit {code}]", flush=True)
        return code

    # Complete model download if needed
    run(
        f"set -e; "
        f"if [ ! -d {MODEL} ]; then "
        f"  mkdir -p {REMOTE}/models; "
        f"  if [ ! -f {ZIP} ] || [ $(stat -c%s {ZIP} 2>/dev/null || echo 0) -lt 40000000 ]; then "
        f"    echo downloading; rm -f {ZIP}; "
        f"    wget -O {ZIP} {URL} || curl -L -o {ZIP} {URL}; "
        f"  fi; "
        f"  unzip -qo {ZIP} -d {REMOTE}/models; "
        f"fi; "
        f"ls -lh {MODEL} | head; "
        f"test -d {MODEL}"
    , timeout=600)

    # Patch env + settings
    run(
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        f"p=Path('{ENV}')\n"
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
        f"vs=Path('{REMOTE}/recordings/voice_settings.json')\n"
        "vs.parent.mkdir(parents=True, exist_ok=True)\n"
        "vs.write_text('{\\n  \"enabled\": true,\\n  \"policy\": \"follow_first\"\\n}\\n', encoding='utf-8')\n"
        "print('env ok')\n"
        "PY"
    )

    # systemd
    run(
        f"echo {PWD} | sudo -S bash -lc '"
        f"cp {REMOTE}/deploy/systemd/seeed-arm-control.service /etc/systemd/system/ && "
        f"cp {REMOTE}/deploy/systemd/seeed-arm-voice.service /etc/systemd/system/ && "
        f"systemctl daemon-reload && "
        f"systemctl enable seeed-arm-control.service seeed-arm-voice.service && "
        f"pkill -f \"uvicorn backend.app:app\" || true; "
        f"pkill -f \"uvicorn voice.app:app\" || true; "
        f"sleep 1; "
        f"systemctl restart seeed-arm-control.service && sleep 5 && "
        f"systemctl restart seeed-arm-voice.service && sleep 4 && "
        f"systemctl --no-pager --full status seeed-arm-control.service seeed-arm-voice.service | head -60"
        f"'"
    , timeout=120)

    run("curl -sS http://127.0.0.1:1882/api/voice/capability; echo; curl -sS http://127.0.0.1:1883/health; echo")
    run("journalctl -u seeed-arm-voice -n 40 --no-pager || true")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
