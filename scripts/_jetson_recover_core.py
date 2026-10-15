# -*- coding: utf-8 -*-
"""Kill stuck wget, restart Core ASAP, report model zip size."""
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
PWD, REMOTE = "seeed", "/home/seeed/seeed/Project/seeed-arm-control"


def run(cmd, timeout=90):
    print("$", cmd[:200], flush=True)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        print(out[-5000:], flush=True)
    if err.strip():
        print("ERR", err[-1500:], flush=True)
    print(f"[exit {code}]", flush=True)


# stop stuck download
run(f"echo {PWD} | sudo -S pkill -f 'wget.*vosk-model' || true")
run(f"echo {PWD} | sudo -S pkill -f 'bash -lc set -e; mkdir -p .*vosk' || true")

# start Core via systemd (or fallback)
run(
    f"echo {PWD} | sudo -S bash -lc '"
    f"systemctl start seeed-arm-control.service || true; "
    f"sleep 2; "
    f"if ! ss -lptn | grep -q 1882; then "
    f"  cd {REMOTE} && set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
    f"  setsid -f {REMOTE}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
    f"  >/home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 </dev/null; "
    f"fi; "
    f"sleep 4; ss -lptn | grep -E \"1882|1883\" || true; "
    f"systemctl is-active seeed-arm-control.service || true"
    f"'"
)

run(
    "python3 - <<'PY'\n"
    "import urllib.request\n"
    "try:\n"
    "  print(urllib.request.urlopen('http://127.0.0.1:1882/api/health',timeout=8).read().decode()[:300])\n"
    "except Exception as e:\n"
    "  print('core FAIL', e)\n"
    "PY"
)
run("ls -lh /tmp/vosk-model-small-cn-0.22.zip 2>&1 || true")
c.close()
print("DONE", flush=True)
