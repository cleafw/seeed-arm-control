# -*- coding: utf-8 -*-
"""Push port-switch fix and restart Core via systemd."""
from pathlib import Path
import paramiko

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "192.168.0.103",
    username="seeed",
    password="seeed",
    timeout=20,
    allow_agent=False,
    look_for_keys=False,
)
sftp = c.open_sftp()
rel = "backend/controller.py"
data = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
with sftp.file(f"{REMOTE}/{rel}", "wb") as f:
    f.write(data)
print("put", rel, len(data))
sftp.close()

cmd = (
    "echo seeed | sudo -S systemctl restart seeed-arm-control.service; "
    "sleep 4; "
    "systemctl is-active seeed-arm-control.service; "
    "grep -n '不能切换端口\\|请在空闲状态下' "
    f"{REMOTE}/backend/controller.py; "
    "python3 - <<'PY'\n"
    "import urllib.request\n"
    "print(urllib.request.urlopen('http://127.0.0.1:1882/api/health', timeout=5).read()[:300].decode())\n"
    "PY"
)
_, o, e = c.exec_command(cmd, timeout=60)
print(o.read().decode(errors="replace"))
err = e.read().decode(errors="replace")
if err.strip():
    print(err[-500:])
c.close()
