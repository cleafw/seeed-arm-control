# -*- coding: utf-8 -*-
"""Push voice Core fix + restart Core/Voice; run acceptance; resume."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request

import paramiko

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
CORE = f"http://{HOST}:1882"


def main() -> int:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in (
            "backend/voice",
            "backend/app.py",
            "backend/models.py",
            "voice",
            "frontend/dist",
        ):
            tar.add(os.path.join(ROOT, rel), arcname=rel)
    data = buf.getvalue()
    print(f"packed {len(data)}", flush=True)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    rt = "/tmp/voice-final.tgz"
    with sftp.file(rt, "wb") as f:
        f.write(data)
    sftp.close()

    def run(cmd: str) -> None:
        print("$", cmd[:140], flush=True)
        _, o, e = c.exec_command(cmd, timeout=90)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        if out.strip():
            print(out[-2500:], flush=True)
        if err.strip():
            print("ERR", err[-600:], flush=True)

    run(f"tar -xzf {rt} -C {REMOTE} && rm -f {rt}")
    run("pkill -f 'uvicorn backend.app:app' || true")
    run("pkill -f 'uvicorn voice.app:app' || true")
    time.sleep(1.5)
    run(
        f"sg dialout -c 'cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
        f"setsid -f {REMOTE}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
        f">/home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 </dev/null'"
    )
    run(
        f"cd {REMOTE} && export REBOT_CORE_URL=http://127.0.0.1:1882 && "
        f"setsid -f {REMOTE}/.venv/bin/uvicorn voice.app:app --host 0.0.0.0 --port 1883 "
        f">/home/seeed/seeed/ENV/seeed-arm-voice.log 2>&1 </dev/null"
    )
    time.sleep(7)
    run("ss -lptn | grep -E '1882|1883' || true")
    run("tail -n 20 /home/seeed/seeed/ENV/seeed-arm-voice.log || true")
    run(
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print('v', urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=5).read().decode())\n"
        "print('c', urllib.request.urlopen('http://127.0.0.1:1882/api/voice/capability',timeout=5).read().decode())\n"
        "PY"
    )
    c.close()

    r = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "voice_acceptance.py")],
        env={**os.environ, "REBOT_CORE_URL": CORE, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        for body, path in (
            ({"enabled": True, "policy": "follow_first"}, "/api/voice/settings"),
            ({"intent": "resume", "slots": {}, "source": "deploy"}, "/api/voice/intent"),
        ):
            req = urllib.request.Request(
                CORE + path,
                data=json.dumps(body).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10).read()
        print("restored follow_first + resume", flush=True)
    except Exception as e:  # noqa: BLE001
        print("restore note:", e, flush=True)
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
