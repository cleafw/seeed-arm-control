# -*- coding: utf-8 -*-
import json
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
_, o, e = c.exec_command(
    "python3 - <<'PY'\n"
    "import urllib.request, json\n"
    "acts=json.loads(urllib.request.urlopen('http://127.0.0.1:1882/api/actions',timeout=8).read().decode())\n"
    "open('/tmp/acts.json','w',encoding='utf-8').write(json.dumps([a.get('name') for a in acts], ensure_ascii=False, indent=2))\n"
    "print('wrote', len(acts))\n"
    "PY",
    timeout=20,
)
print(o.read().decode())
sftp = c.open_sftp()
print(sftp.file("/tmp/acts.json").read().decode("utf-8"))
sftp.close()
c.close()
