# -*- coding: utf-8 -*-
"""Sync backend voice modules + restart uvicorn on Jetson."""
from __future__ import annotations

import io
import os
import sys
import tarfile
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
INCLUDE = [
    "backend/app.py",
    "backend/config.py",
    "backend/models.py",
    "backend/controller.py",
    "backend/voice",
    "voice",
    "scripts/voice_acceptance.py",
    "frontend/dist",
]


def main():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in INCLUDE:
            full = os.path.join(ROOT, rel)
            if os.path.isdir(full):
                tar.add(full, arcname=rel)
            elif os.path.isfile(full):
                tar.add(full, arcname=rel)
    data = buf.getvalue()
    print(f"packed {len(data)} bytes", flush=True)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    remote_tar = "/tmp/voice-ui-sync.tgz"
    with sftp.file(remote_tar, "wb") as f:
        f.write(data)
    sftp.close()

    def run(cmd, timeout=60):
        print(f"$ {cmd[:200]}", flush=True)
        _i, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
        if out.strip():
            print(out[-3000:], flush=True)
        if err.strip():
            print(err[-1000:], flush=True)
        print(f"[exit {code}]", flush=True)
        return code

    run(f"tar -xzf {remote_tar} -C {REMOTE} && rm -f {remote_tar}")
    # Ensure voice env defaults (text closed-loop; no sidecar required for reachable)
    run(
        "python3 - <<'PY'\n"
        "from pathlib import Path\n"
        "p = Path('/home/seeed/seeed/ENV/seeed-arm-control.env')\n"
        "text = p.read_text(encoding='utf-8') if p.exists() else ''\n"
        "lines = [ln for ln in text.splitlines() if not ln.startswith('VOICE_')]\n"
        "lines += [\n"
        "  'VOICE_ENABLED=1',\n"
        "  'VOICE_POLICY=follow_first',\n"
        "  # empty health → Core marks reachable when enabled (text NLU on Core)\n"
        "  'VOICE_HEALTH_URL=',\n"
        "]\n"
        "p.write_text('\\n'.join(lines).rstrip() + '\\n', encoding='utf-8')\n"
        "print('env voice keys updated')\n"
        "vs = Path('/home/seeed/seeed/Project/seeed-arm-control/recordings/voice_settings.json')\n"
        "vs.parent.mkdir(parents=True, exist_ok=True)\n"
        "vs.write_text('{\\n  \"enabled\": true,\\n  \"policy\": \"follow_first\"\\n}\\n', encoding='utf-8')\n"
        "print('voice_settings.json written')\n"
        "PY"
    )
    run("pkill -f 'uvicorn backend.app:app' || true")
    run("pkill -f 'uvicorn voice.app:app' || true")
    time.sleep(1)
    start = (
        f"cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
        f"setsid -f {REMOTE}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
        f">/home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 </dev/null"
    )
    run(f"sg dialout -c {start!r}")
    # Optional text-only Voice sidecar (health + /listen)
    voice_start = (
        f"cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
        f"export REBOT_CORE_URL=http://127.0.0.1:1882 && "
        f"setsid -f {REMOTE}/.venv/bin/uvicorn voice.app:app --host 0.0.0.0 --port 1883 "
        f">/home/seeed/seeed/ENV/seeed-arm-voice.log 2>&1 </dev/null"
    )
    run(voice_start)
    time.sleep(6)
    run("ss -lptn | grep -E '1882|1883' || echo no-ports")
# Proper Chinese via unicode escapes in remote smoke (avoid non-ASCII in heredoc)
    run(
        "python3 - <<'PY'\n"
        "import json,urllib.request,urllib.error\n"
        "print('health', urllib.request.urlopen('http://127.0.0.1:1882/api/health',timeout=8).read().decode()[:300])\n"
        "print('cap', urllib.request.urlopen('http://127.0.0.1:1882/api/voice/capability',timeout=8).read().decode())\n"
        "data=json.dumps({'text':'\\u6025\\u505c','source':'deploy'}).encode()\n"
        "req=urllib.request.Request('http://127.0.0.1:1882/api/voice/utterance',data=data,"
        "headers={'Content-Type':'application/json'},method='POST')\n"
        "try:\n"
        "  print('utt', urllib.request.urlopen(req,timeout=8).read().decode()[:300])\n"
        "except urllib.error.HTTPError as e:\n"
        "  print('utt', e.code, e.read().decode()[:300])\n"
        "try:\n"
        "  print('vhealth', urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=5).read().decode())\n"
        "except Exception as e:\n"
        "  print('vhealth_err', e)\n"
        "PY"
    )
    c.close()
    print("OK http://192.168.0.103:1882 — hard refresh", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
