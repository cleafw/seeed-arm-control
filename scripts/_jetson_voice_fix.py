# -*- coding: utf-8 -*-
"""Resync voice/ + restart voice sidecar on Jetson (Core already running)."""
from __future__ import annotations

import io
import os
import tarfile
import time

import paramiko

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"


def main() -> int:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(os.path.join(ROOT, "voice"), arcname="voice")
        tar.add(os.path.join(ROOT, "backend", "voice"), arcname="backend/voice")
        tar.add(os.path.join(ROOT, "frontend", "dist"), arcname="frontend/dist")
    data = buf.getvalue()
    print(f"packed {len(data)} bytes", flush=True)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    remote_tar = "/tmp/voice-fix.tgz"
    with sftp.file(remote_tar, "wb") as f:
        f.write(data)
    sftp.close()

    def run(cmd: str, timeout: int = 60) -> None:
        print(f"$ {cmd[:160]}", flush=True)
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        if out.strip():
            print(out[-3000:], flush=True)
        if err.strip():
            print(err[-800:], flush=True)

    run(f"tar -xzf {remote_tar} -C {REMOTE} && rm -f {remote_tar}")
    run("pkill -f 'uvicorn voice.app:app' || true")
    time.sleep(1)
    run(
        f"cd {REMOTE} && export REBOT_CORE_URL=http://127.0.0.1:1882 && "
        f"setsid -f {REMOTE}/.venv/bin/uvicorn voice.app:app --host 0.0.0.0 --port 1883 "
        f">/home/seeed/seeed/ENV/seeed-arm-voice.log 2>&1 </dev/null"
    )
    time.sleep(3)
    run("ss -lptn | grep -E '1882|1883' || true")
    run(
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print(urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=5).read().decode())\n"
        "print(urllib.request.urlopen('http://127.0.0.1:1882/api/voice/capability',timeout=5).read().decode())\n"
        "PY"
    )
    c.close()
    print("OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
