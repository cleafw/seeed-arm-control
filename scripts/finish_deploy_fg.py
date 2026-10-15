# -*- coding: utf-8 -*-
"""Foreground uv sync (no broken nohup+pty), then start service."""
from __future__ import annotations

import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stdout.reconfigure(line_buffering=True)

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
PROJ = "/home/seeed/seeed/Project/seeed-arm-control"
ENV = "/home/seeed/seeed/ENV"
UV = f"{ENV}/uv-bin/uv"
INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"
LOG = f"{ENV}/seeed-arm-control.log"
SYNC_LOG = f"{ENV}/uv-sync.log"


def run(c, cmd, timeout=120, pty=False):
    print(f"$ {cmd[:240]}", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=pty)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace") if not pty else ""
    code = o.channel.recv_exit_status()
    text = (out + err).strip()
    if text:
        print(text[-8000:], flush=True)
    print(f"[exit {code}]", flush=True)
    return code, out + err


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)

    run(c, "pkill -9 -f '/uv-bin/uv sync' || true; pkill -9 -f 'uv sync' || true; sleep 2; pgrep -af 'uv sync' || echo cleared")
    run(c, f"rm -f {SYNC_LOG}; touch {SYNC_LOG}")

    # Stream sync in background via setsid (survives SSH), without get_pty
    start_sync = (
        f"cd {PROJ} && "
        f"export PATH={ENV}/uv-bin:$PATH UV_INDEX_URL={INDEX} UV_HTTP_TIMEOUT=300 "
        f"UV_NATIVE_TLS=1 && "
        f"setsid -f {UV} sync --no-dev --index-url {INDEX} >{SYNC_LOG} 2>&1 </dev/null; "
        f"sleep 1; pgrep -af '/uv-bin/uv sync' || echo START_FAIL"
    )
    run(c, start_sync, timeout=30)

    for i in range(120):  # ~40 min
        time.sleep(20)
        code, out = run(
            c,
            f"test -x {PROJ}/.venv/bin/uvicorn && echo DONE; "
            f"pgrep -c -f '/uv-bin/uv sync' || echo 0; "
            f"wc -c < {SYNC_LOG}; "
            f"tail -n 4 {SYNC_LOG}",
            timeout=30,
        )
        if "DONE" in out:
            break
        if i > 0 and i % 6 == 0 and "DONE" not in out:
            _c, alive = run(c, "pgrep -af '/uv-bin/uv sync' || echo DEAD", timeout=15)
            if "DEAD" in alive:
                print("sync died; restarting", flush=True)
                run(c, start_sync, timeout=30)

    code, ready = run(c, f"test -x {PROJ}/.venv/bin/uvicorn && echo YES || echo NO", timeout=20)
    if "YES" not in ready:
        run(c, f"tail -n 100 {SYNC_LOG}", timeout=30)
        c.close()
        return 1

    run(c, f"ln -sfn {PROJ}/.venv {ENV}/seeed-arm-control")
    run(c, "pkill -f 'uvicorn backend.app:app' || true")
    time.sleep(1)
    start = (
        f"cd {PROJ} && set -a && . {ENV}/seeed-arm-control.env && set +a && "
        f"setsid -f {PROJ}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
        f">{LOG} 2>&1 </dev/null; sleep 1; echo started; pgrep -af 'uvicorn backend.app' || true"
    )
    run(c, f"sg dialout -c {start!r}", timeout=30)
    time.sleep(8)
    run(c, "ss -lptn | grep 1882 || true", timeout=15)
    run(c, f"tail -n 80 {LOG}", timeout=15)
    run(
        c,
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print(urllib.request.urlopen('http://127.0.0.1:1882/api/health', timeout=8).read().decode())\n"
        "PY",
        timeout=20,
    )
    c.close()
    print("\nOK http://192.168.0.103:1882", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
