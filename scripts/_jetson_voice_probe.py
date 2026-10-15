# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "192.168.0.103",
    username="seeed",
    password="seeed",
    timeout=25,
    allow_agent=False,
    look_for_keys=False,
)


def run(cmd: str) -> None:
    print("$", cmd[:120], flush=True)
    _, o, e = c.exec_command(cmd, timeout=40)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if out.strip():
        print(out[-5000:], flush=True)
    if err.strip():
        print("ERR", err[-2000:], flush=True)


run("tail -n 50 /home/seeed/seeed/ENV/seeed-arm-voice.log 2>/dev/null || echo no-voice-log")
run("ss -lptn | grep -E '1882|1883' || echo none")
run(
    "cd /home/seeed/seeed/Project/seeed-arm-control && "
    ".venv/bin/python -c 'import voice.app; print(\"voice_ok\")' 2>&1 | tail -30"
)
run(
    "python3 - <<'PY'\n"
    "import json,urllib.request\n"
    "print(urllib.request.urlopen('http://127.0.0.1:1882/api/voice/capability',timeout=8).read().decode())\n"
    "body=json.dumps({'text':'estop-alias','source':'probe'}).encode()\n"
    "req=urllib.request.Request('http://127.0.0.1:1882/api/voice/utterance',"
    "data=json.dumps({'text':'ji ting','source':'probe'}).encode(),"
    "headers={'Content-Type':'application/json'},method='POST')\n"
    "PY"
)
# Proper Chinese via unicode escapes
run(
    "python3 - <<'PY'\n"
    "import json,urllib.request,urllib.error\n"
    "print('cap', urllib.request.urlopen('http://127.0.0.1:1882/api/voice/capability',timeout=8).read().decode())\n"
    "text='\\u6025\\u505c'\n"
    "data=json.dumps({'text':text,'source':'probe'}).encode()\n"
    "req=urllib.request.Request('http://127.0.0.1:1882/api/voice/utterance',data=data,"
    "headers={'Content-Type':'application/json'},method='POST')\n"
    "try:\n"
    "  print('utt', urllib.request.urlopen(req,timeout=8).read().decode()[:400])\n"
    "except urllib.error.HTTPError as e:\n"
    "  print('utt', e.code, e.read().decode()[:400])\n"
    "PY"
)

# Start voice if missing
run(
    "cd /home/seeed/seeed/Project/seeed-arm-control && "
    "export REBOT_CORE_URL=http://127.0.0.1:1882 && "
    "setsid -f .venv/bin/uvicorn voice.app:app --host 0.0.0.0 --port 1883 "
    ">/home/seeed/seeed/ENV/seeed-arm-voice.log 2>&1 </dev/null; sleep 2; "
    "ss -lptn | grep 1883 || (tail -n 30 /home/seeed/seeed/ENV/seeed-arm-voice.log)"
)
c.close()
