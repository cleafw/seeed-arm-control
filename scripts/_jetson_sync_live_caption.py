# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()

    def put(rel: str) -> None:
        local = ROOT / rel
        if local.is_dir():
            for p in local.rglob("*"):
                if p.is_file():
                    put(p.relative_to(ROOT).as_posix())
            return
        remote = f"{REMOTE}/{rel}"
        cur = REMOTE
        for part in rel.replace("\\", "/").split("/")[:-1]:
            cur = f"{cur}/{part}"
            try:
                sftp.stat(cur)
            except OSError:
                sftp.mkdir(cur)
        data = local.read_bytes()
        with sftp.file(remote, "wb") as f:
            f.write(data)
        print("put", rel, flush=True)

    for rel in (
        "backend/app.py",
        "backend/models.py",
        "backend/voice/service.py",
        "voice/app.py",
        "voice/device_listen.py",
        "voice/core_client.py",
        "frontend/dist",
    ):
        put(rel)
    sftp.close()

    def run(cmd: str, timeout: int = 90) -> None:
        print("$", cmd[:180], flush=True)
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        if out.strip():
            print(out[-4000:], flush=True)
        if err.strip():
            print("ERR", err[-1000:], flush=True)

    run("pkill -f 'uvicorn backend.app:app' || true")
    run("pkill -f 'uvicorn voice.app:app' || true")
    time.sleep(1.5)
    run(
        f"sg dialout -c 'cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
        f"setsid -f {REMOTE}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
        f">/home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 </dev/null'"
    )
    run(
        f"cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
        f"export REBOT_CORE_URL=http://127.0.0.1:1882 VOICE_DEVICE_LISTEN=1 && "
        f"setsid -f {REMOTE}/.venv/bin/uvicorn voice.app:app --host 0.0.0.0 --port 1883 "
        f">/home/seeed/seeed/ENV/seeed-arm-voice.log 2>&1 </dev/null"
    )
    time.sleep(7)
    run("ss -lptn | grep -E '1882|1883' || true")
    run(
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print(urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=8).read().decode())\n"
        "print(urllib.request.urlopen('http://127.0.0.1:1882/api/voice/capability',timeout=8).read().decode())\n"
        "PY"
    )
    run("tail -n 25 /home/seeed/seeed/ENV/seeed-arm-voice.log")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
