# -*- coding: utf-8 -*-
"""Single clean uv sync via tuna, then start service. PYTHONUNBUFFERED."""
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


def run(c, cmd, timeout=3600):
    print(f"$ {cmd[:240]}", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=timeout, get_pty=True)
    out = o.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        print(out[-6000:], flush=True)
    print(f"[exit {code}]", flush=True)
    return code, out


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)

    # Kill ALL competing uv sync / hung proxy sync
    run(c, "pkill -f '/uv-bin/uv sync' || true; pkill -f 'uv sync' || true; sleep 1; pgrep -af 'uv sync' || echo cleared")

    # Start one sync with nohup + log
    run(
        c,
        f"export PATH={ENV}/uv-bin:$PATH UV_INDEX_URL={INDEX} UV_HTTP_TIMEOUT=180; "
        f"cd {PROJ} && nohup {UV} sync --no-dev --index-url {INDEX} > {SYNC_LOG} 2>&1 & echo sync_pid=$!",
    )

    for i in range(90):  # up to ~30 min
        time.sleep(20)
        _i, o, e = c.exec_command(
            f"test -x {PROJ}/.venv/bin/uvicorn && echo DONE; "
            f"pgrep -c -f '/uv-bin/uv sync' || echo 0; "
            f"wc -l < {SYNC_LOG} 2>/dev/null || echo 0; "
            f"tail -n 3 {SYNC_LOG} 2>/dev/null || true",
            timeout=30,
            get_pty=True,
        )
        out = o.read().decode("utf-8", "replace")
        print(f"[{i}] {out.strip()[:500]}", flush=True)
        if "DONE" in out:
            break
        # if sync died without uvicorn, restart once
        if i > 0 and i % 6 == 0 and "DONE" not in out:
            _i, o2, _ = c.exec_command("pgrep -f '/uv-bin/uv sync' || true", timeout=15, get_pty=True)
            alive = o2.read().decode()
            if not alive.strip():
                print("sync died; restarting", flush=True)
                run(
                    c,
                    f"export PATH={ENV}/uv-bin:$PATH UV_INDEX_URL={INDEX}; "
                    f"cd {PROJ} && nohup {UV} sync --no-dev --index-url {INDEX} >> {SYNC_LOG} 2>&1 & echo restarted=$!",
                )

    _i, o, e = c.exec_command(f"test -x {PROJ}/.venv/bin/uvicorn && echo YES || echo NO", timeout=20, get_pty=True)
    ready = o.read().decode()
    print("uvicorn?", ready, flush=True)
    if "YES" not in ready:
        run(c, f"tail -n 80 {SYNC_LOG}")
        c.close()
        return 1

    run(c, f"ln -sfn {PROJ}/.venv {ENV}/seeed-arm-control")
    run(c, "pkill -f 'uvicorn backend.app:app' || true")
    time.sleep(1)
    start = (
        f"cd {PROJ} && set -a && . {ENV}/seeed-arm-control.env && set +a && "
        f"nohup {PROJ}/.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
        f"> {LOG} 2>&1 & echo pid=$!"
    )
    run(c, f"sg dialout -c {start!r}")
    time.sleep(8)
    run(c, "ss -lptn | grep 1882 || true")
    run(c, f"tail -n 60 {LOG}")
    run(
        c,
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print(urllib.request.urlopen('http://127.0.0.1:1882/api/health', timeout=8).read().decode())\n"
        "PY",
    )
    c.close()
    print("\nOK http://192.168.0.103:1882", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
