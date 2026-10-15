# -*- coding: utf-8 -*-
from pathlib import Path
import paramiko

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
PWD = "seeed"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
for rel in (
    "deploy/systemd/seeed-arm-control.service",
    "deploy/systemd/seeed-arm-voice.service",
    "deploy/enable_boot_services.sh",
    "deploy/wait_core_health.sh",
):
    data = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
    with sftp.file(f"{REMOTE}/{rel}", "wb") as f:
        f.write(data)
    print("put", rel)
sftp.chmod(f"{REMOTE}/deploy/enable_boot_services.sh", 0o755)
sftp.chmod(f"{REMOTE}/deploy/wait_core_health.sh", 0o755)
sftp.close()

cmd = f"echo {PWD} | sudo -S bash {REMOTE}/deploy/enable_boot_services.sh"
print("$", cmd)
_, o, e = c.exec_command(cmd, timeout=180)
print(o.read().decode("utf-8", "replace")[-8000:])
print(e.read().decode("utf-8", "replace")[-2000:])

_, o, e = c.exec_command(
    "python3 - <<'PY'\n"
    "import urllib.request\n"
    "print('core', urllib.request.urlopen('http://127.0.0.1:1882/api/health',timeout=8).read().decode()[:200])\n"
    "print('voice', urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=8).read().decode())\n"
    "PY",
    timeout=30,
)
print(o.read().decode("utf-8", "replace"))
print(e.read().decode("utf-8", "replace"))
c.close()
