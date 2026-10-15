# -*- coding: utf-8 -*-
import sys
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
_i, o, e = c.exec_command("pgrep -af 'uv |sync' || true; ls -la /home/seeed/seeed/Project/seeed-arm-control/.venv/bin | head", timeout=30, get_pty=True)
print(o.read().decode())
c.close()
