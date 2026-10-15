# -*- coding: utf-8 -*-
from pathlib import Path
import paramiko

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
data = (ROOT / "backend/voice/service.py").read_bytes()
with sftp.file(f"{REMOTE}/backend/voice/service.py", "wb") as f:
    f.write(data)
sftp.close()
print("put service.py", len(data))
_, o, e = c.exec_command(
    "pkill -f 'uvicorn backend.app:app' || true; sleep 1; "
    f"sg dialout -c 'cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
    f"setsid -f {REMOTE}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
    f">/home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 </dev/null'; sleep 5; "
    "python3 - <<'PY'\n"
    "import json,urllib.request\n"
    "body=json.dumps({'text':'\\u7acb\\u8d77\\u6765','source':'test'},ensure_ascii=False).encode()\n"
    "req=urllib.request.Request('http://127.0.0.1:1882/api/voice/utterance',data=body,"
    "headers={'Content-Type':'application/json'},method='POST')\n"
    "try:\n"
    "  print(urllib.request.urlopen(req,timeout=15).read().decode()[:500])\n"
    "except Exception as ex:\n"
    "  import urllib.error\n"
    "  if isinstance(ex, urllib.error.HTTPError):\n"
    "    print(ex.code, ex.read().decode()[:400])\n"
    "  else:\n"
    "    print('ERR', ex)\n"
    "PY",
    timeout=40,
)
print(o.read().decode("utf-8", "replace")[-3000:])
print(e.read().decode("utf-8", "replace")[-800:])
c.close()
