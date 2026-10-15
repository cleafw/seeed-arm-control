# -*- coding: utf-8 -*-
from pathlib import Path
import paramiko

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
for rel in (
    "backend/voice/nlu.py",
    "backend/voice/service.py",
    "backend/controller.py",
):
    data = (ROOT / rel).read_bytes()
    with sftp.file(f"{REMOTE}/{rel}", "wb") as f:
        f.write(data)
    print("put", rel)
sftp.close()
_, o, e = c.exec_command(
    "pkill -f 'uvicorn backend.app:app' || true; sleep 1; "
    f"sg dialout -c 'cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
    f"setsid -f {REMOTE}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
    f">/home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 </dev/null'; sleep 5; "
    "ss -lptn | grep 1882 || true",
    timeout=40,
)
print(o.read().decode()[-2000:])
print(e.read().decode()[-500:])
c.close()
