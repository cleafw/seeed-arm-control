# -*- coding: utf-8 -*-
import json
import sys
import urllib.request

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Enable Clash allow-lan via API
req = urllib.request.Request(
    "http://127.0.0.1:9097/configs",
    data=json.dumps({"allow-lan": True}).encode(),
    headers={
        "Authorization": "Bearer set-your-secret",
        "Content-Type": "application/json",
    },
    method="PATCH",
)
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        print("clash patch", r.status)
except Exception as e:
    print("clash patch fail", e)

# Check listen (best-effort)
import subprocess

print(subprocess.check_output("netstat -an | findstr 7897", shell=True, text=True, errors="replace"))

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
for ip in ("192.168.0.100", "192.168.0.215"):
    cmd = (
        "python3 -c "
        f"\"import socket; s=socket.create_connection(('{ip}',7897),timeout=3); "
        f"print('REACH', '{ip}'); s.close()\""
    )
    _i, o, e = c.exec_command(cmd, timeout=15)
    out = o.read().decode() + e.read().decode()
    print(out.strip() or f"NO_REACH {ip}")
c.close()
