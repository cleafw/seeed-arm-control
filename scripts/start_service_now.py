# -*- coding: utf-8 -*-
"""Kill leftover syncs and start uvicorn now that deps are installed."""
from __future__ import annotations

import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stdout.reconfigure(line_buffering=True)

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
PROJ = "/home/seeed/seeed/Project/seeed-arm-control"
ENV = "/home/seeed/seeed/ENV"
LOG = f"{ENV}/seeed-arm-control.log"


def run(c, cmd, timeout=60):
    print(f"$ {cmd[:220]}", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    text = (out + err).strip()
    if text:
        print(text[-5000:], flush=True)
    print(f"[exit {code}]", flush=True)
    return code, out + err


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)

    run(c, "pkill -9 -f '/uv-bin/uv sync' || true; pkill -9 -f 'uv sync' || true; sleep 1; pgrep -af 'uv sync' || echo cleared")
    _code, out = run(c, f"test -x {PROJ}/.venv/bin/uvicorn && ls -la {PROJ}/.venv/bin/uvicorn || echo MISSING")
    if "MISSING" in out:
        c.close()
        return 1

    run(c, f"ln -sfn {PROJ}/.venv {ENV}/seeed-arm-control")
    run(c, "pkill -f 'uvicorn backend.app:app' || true")
    time.sleep(1)

    inner = (
        f"cd {PROJ} && set -a && . {ENV}/seeed-arm-control.env && set +a && "
        f"setsid -f {PROJ}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
        + ">"
        + LOG
        + " 2>&1 </dev/null"
    )
    run(c, f"sg dialout -c {inner!r}")
    time.sleep(8)
    run(c, "ss -lptn | grep 1882 || echo no1882")
    run(c, "pgrep -af 'uvicorn backend.app' || echo no_proc")
    run(c, f"tail -n 80 {LOG}")
    run(
        c,
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "try:\n"
        "  print(urllib.request.urlopen('http://127.0.0.1:1882/api/health', timeout=8).read().decode())\n"
        "except Exception as e:\n"
        "  print('health_fail', e)\n"
        "PY",
    )
    c.close()
    print("\nTry http://192.168.0.103:1882", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
