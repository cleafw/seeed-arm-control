# -*- coding: utf-8 -*-
import json
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)

def run(cmd, timeout=40):
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    print(out[-4000:])
    if err.strip():
        print("ERR", err[-1000:])

run("ss -lptn | grep 1882 || echo no1882")
run(
    "python3 - <<'PY'\n"
    "import json,urllib.request,urllib.error\n"
    "body=json.dumps({'text':'\\u7acb\\u8d77\\u6765','source':'test'}).encode()\n"
    "req=urllib.request.Request('http://127.0.0.1:1882/api/voice/utterance',data=body,"
    "headers={'Content-Type':'application/json'},method='POST')\n"
    "try:\n"
    "  r=urllib.request.urlopen(req,timeout=15)\n"
    "  print(r.read().decode()[:600])\n"
    "except urllib.error.HTTPError as ex:\n"
    "  print(ex.code, ex.read().decode()[:500])\n"
    "except Exception as ex:\n"
    "  print('ERR', ex)\n"
    "PY"
)
c.close()
