# -*- coding: utf-8 -*-
"""Sync voice UI debug panel + backend last_intent enrichment to Jetson."""
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
        for rel in (
            "backend/voice/service.py",
            "frontend/dist",
        ):
            tar.add(os.path.join(ROOT, rel), arcname=rel)
    data = buf.getvalue()
    print(f"packed {len(data)}", flush=True)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    rt = "/tmp/voice-debug-ui.tgz"
    with sftp.file(rt, "wb") as f:
        f.write(data)
    sftp.close()

    def run(cmd: str) -> None:
        print("$", cmd[:140], flush=True)
        _, o, e = c.exec_command(cmd, timeout=60)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        if out.strip():
            print(out[-2000:], flush=True)
        if err.strip():
            print("ERR", err[-500:], flush=True)

    run(f"tar -xzf {rt} -C {REMOTE} && rm -f {rt}")
    run("pkill -f 'uvicorn backend.app:app' || true")
    time.sleep(1.2)
    run(
        f"sg dialout -c 'cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
        f"setsid -f {REMOTE}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
        f">/home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 </dev/null'"
    )
    time.sleep(5)
    run("ss -lptn | grep 1882 || echo no1882")
    c.close()
    print("OK http://192.168.0.103:1882 — hard refresh", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
