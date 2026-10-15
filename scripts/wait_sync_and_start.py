# -*- coding: utf-8 -*-
import sys
import time
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)

def run(cmd, timeout=60):
    print("$", cmd[:200])
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    print(o.read().decode("utf-8", "replace")[-4000:])
    return o.channel.recv_exit_status()

# Kill the hung Clash-proxy sync only (port 17897)
run("pkill -f 'http_proxy=http://127.0.0.1:17897' || true")
run("pgrep -af 'uv sync' || echo none")

# Wait up to ~15 min for tuna sync
for i in range(45):
    _i, o, e = c.exec_command(
        "test -x /home/seeed/seeed/Project/seeed-arm-control/.venv/bin/uvicorn && echo DONE || "
        "(pgrep -af 'uv sync' || echo NO_UV); "
        "ls /home/seeed/seeed/Project/seeed-arm-control/.venv/lib/python3.12/site-packages 2>/dev/null | wc -l",
        timeout=30,
        get_pty=True,
    )
    out = o.read().decode("utf-8", "replace").strip()
    print(f"[{i}] {out}")
    if "DONE" in out:
        break
    if "NO_UV" in out and "DONE" not in out:
        print("uv sync not running; restart tuna sync")
        run(
            "export PATH=/home/seeed/seeed/ENV/uv-bin:$PATH "
            "UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple; "
            "cd /home/seeed/seeed/Project/seeed-arm-control && "
            "nohup /home/seeed/seeed/ENV/uv-bin/uv sync --no-dev "
            "--index-url https://pypi.tuna.tsinghua.edu.cn/simple "
            "> /home/seeed/seeed/ENV/uv-sync.log 2>&1 & echo started",
            timeout=30,
        )
    time.sleep(20)

# If DONE, start service
_i, o, e = c.exec_command(
    "test -x /home/seeed/seeed/Project/seeed-arm-control/.venv/bin/uvicorn && echo YES || echo NO",
    timeout=20,
    get_pty=True,
)
ready = o.read().decode()
print("ready?", ready)
if "YES" in ready:
    run("pkill -f 'uvicorn backend.app:app' || true")
    time.sleep(1)
    start = (
        "cd /home/seeed/seeed/Project/seeed-arm-control && "
        "set -a && . /home/seeed/seeed/ENV/seeed-arm-control.env && set +a && "
        "nohup /home/seeed/seeed/Project/seeed-arm-control/.venv/bin/uvicorn backend.app:app "
        "--host 0.0.0.0 --port 1882 > /home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 & echo pid=$!"
    )
    run(f"sg dialout -c {start!r}")
    time.sleep(6)
    run("ss -lptn | grep 1882 || true")
    run("tail -n 40 /home/seeed/seeed/ENV/seeed-arm-control.log")
    run(
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print(urllib.request.urlopen('http://127.0.0.1:1882/api/health', timeout=8).read().decode())\n"
        "PY"
    )
else:
    run("tail -n 40 /home/seeed/seeed/ENV/uv-sync.log 2>/dev/null || true")

c.close()
