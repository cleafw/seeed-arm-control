# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
PWD = "seeed"
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
MODEL = f"{REMOTE}/models/vosk-model-small-cn-0.22"


def run(cmd, timeout=90):
    print("$", cmd[:220], flush=True)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        print(out[-6000:], flush=True)
    if err.strip():
        print("ERR", err[-2000:], flush=True)
    print(f"[exit {code}]", flush=True)


run(f"echo {PWD} | sudo -S systemctl status seeed-arm-voice.service --no-pager -l | head -40")
run(f"echo {PWD} | sudo -S journalctl -u seeed-arm-voice -n 40 --no-pager")
run(f"{REMOTE}/.venv/bin/python -c 'import vosk; print(\"vosk\", vosk.__version__)'")
run(f"test -d {MODEL} && ls {MODEL}")

# manual start with full env
run(
    f"cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
    f"export REBOT_CORE_URL=http://127.0.0.1:1882 VOICE_DEVICE_LISTEN=1 "
    f"VOICE_VOSK_MODEL={MODEL} VOICE_ALSA_DEVICE=plughw:CARD=ArrayUAC10,DEV=0 && "
    f"setsid -f {REMOTE}/.venv/bin/uvicorn voice.app:app --host 0.0.0.0 --port 1883 "
    f">/home/seeed/seeed/ENV/seeed-arm-voice.log 2>&1 </dev/null; sleep 6; "
    f"ss -lptn | grep 1883 || true; "
    f"tail -n 50 /home/seeed/seeed/ENV/seeed-arm-voice.log"
)

run(
    "python3 - <<'PY'\n"
    "import urllib.request\n"
    "print(urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=8).read().decode())\n"
    "print(urllib.request.urlopen('http://127.0.0.1:1882/api/voice/capability',timeout=8).read().decode())\n"
    "PY"
)
c.close()
