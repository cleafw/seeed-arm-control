# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
_, o, e = c.exec_command(
    "python3 - <<'PY'\n"
    "import urllib.request, json\n"
    "acts=json.loads(urllib.request.urlopen('http://127.0.0.1:1882/api/actions',timeout=8).read().decode())\n"
    "print('actions', len(acts))\n"
    "for a in acts:\n"
    "  print('-', a.get('name'), a.get('id')[:8])\n"
    "try:\n"
    "  poses=json.loads(urllib.request.urlopen('http://127.0.0.1:1882/api/named_poses',timeout=8).read().decode())\n"
    "  print('poses', len(poses))\n"
    "  for p in poses:\n"
    "    print('-', p.get('name'), p.get('id')[:8])\n"
    "except Exception as ex:\n"
    "  print('poses err', ex)\n"
    "PY",
    timeout=30,
)
print(o.read().decode("utf-8", "replace"))
print(e.read().decode("utf-8", "replace"))
c.close()
