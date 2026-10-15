# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
_, o, e = c.exec_command(
    "ss -lptn | grep -E '1882|1883' || echo down; "
    "python3 - <<'PY'\n"
    "import urllib.request\n"
    "try:\n"
    "  print(urllib.request.urlopen('http://127.0.0.1:1882/api/health',timeout=5).read().decode()[:250])\n"
    "except Exception as ex:\n"
    "  print('FAIL', ex)\n"
    "PY",
    timeout=25,
)
print(o.read().decode())
print(e.read().decode())
c.close()
