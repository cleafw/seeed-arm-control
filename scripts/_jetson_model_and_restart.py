# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=25, allow_agent=False, look_for_keys=False)
PWD = "seeed"
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
MODEL = f"{REMOTE}/models/vosk-model-small-cn-0.22"
ZIP = "/tmp/vosk-model-small-cn-0.22.zip"
URL = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"


def run(cmd, timeout=600):
    print("$", cmd[:240], flush=True)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        print(out[-8000:], flush=True)
    if err.strip():
        print("ERR", err[-3000:], flush=True)
    print(f"[exit {code}]", flush=True)
    return code


# model as root then chown
run(
    f"echo {PWD} | sudo -S bash -lc '"
    f"set -e; "
    f"mkdir -p {REMOTE}/models; "
    f"rm -f {ZIP}; "
    f"wget -O {ZIP} {URL}; "
    f"rm -rf {MODEL}; "
    f"unzip -qo {ZIP} -d {REMOTE}/models; "
    f"chown -R seeed:seeed {REMOTE}/models; "
    f"ls -lh {MODEL} | head; "
    f"test -f {MODEL}/am/final.mdl || test -f {MODEL}/conf/model.conf || ls {MODEL}"
    f"'",
    timeout=600,
)

# restart services (pkill ignore fail)
run(
    f"echo {PWD} | sudo -S bash -lc '"
    f"systemctl stop seeed-arm-voice.service || true; "
    f"systemctl stop seeed-arm-control.service || true; "
    f"pkill -f \"uvicorn backend.app:app\" || true; "
    f"pkill -f \"uvicorn voice.app:app\" || true; "
    f"sleep 2; "
    f"systemctl start seeed-arm-control.service; "
    f"sleep 6; "
    f"systemctl start seeed-arm-voice.service; "
    f"sleep 4; "
    f"systemctl is-active seeed-arm-control.service; "
    f"systemctl is-active seeed-arm-voice.service; "
    f"ss -lptn | grep -E \"1882|1883\" || true; "
    f"journalctl -u seeed-arm-control -u seeed-arm-voice -n 50 --no-pager"
    f"'",
    timeout=120,
)

run(
    "python3 - <<'PY'\n"
    "import urllib.request\n"
    "for u in ('http://127.0.0.1:1882/api/voice/capability','http://127.0.0.1:1883/health'):\n"
    "  try:\n"
    "    print(u, urllib.request.urlopen(u,timeout=8).read().decode()[:500])\n"
    "  except Exception as e:\n"
    "    print(u, 'ERR', e)\n"
    "PY"
)

c.close()
