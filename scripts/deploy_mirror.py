# -*- coding: utf-8 -*-
"""Deploy using China PyPI mirror (no Clash tunnel needed)."""
from __future__ import annotations

import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
REMOTE_PROJECT = "/home/seeed/seeed/Project/seeed-arm-control"
REMOTE_ENV = "/home/seeed/seeed/ENV"
UV = f"{REMOTE_ENV}/uv-bin/uv"
ENV_FILE = f"{REMOTE_ENV}/seeed-arm-control.env"
LOG = f"{REMOTE_ENV}/seeed-arm-control.log"
# Tsinghua + fallback
INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def run(c, cmd, timeout=2400):
    print(f"\n$ {cmd[:320]}{'...' if len(cmd) > 320 else ''}")
    _i, out, err = c.exec_command(cmd, timeout=timeout, get_pty=True)
    stdout = out.read().decode("utf-8", "replace")
    stderr = err.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    text = (stdout + ("\n" + stderr if stderr.strip() and stderr not in stdout else "")).rstrip()
    if text:
        print(text[-9000:])
    print(f"[exit {code}]")
    return code, text


def main():
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)

    env_body = f"""LEADER_PROFILE=so101_leader
FOLLOWER_PROFILE=so101_follower
MASTER_PORT=/dev/ttyACM0
SLAVE_PORT=/dev/ttyACM1
REBOT_RECORDINGS_DIR={REMOTE_PROJECT}/recordings
PYTHONUNBUFFERED=1
"""
    sftp = c.open_sftp()
    with sftp.file(ENV_FILE, "w") as f:
        f.write(env_body)
    sftp.close()

    run(c, f"echo {PWD} | sudo -S -p '' usermod -aG dialout,docker seeed")

    # Probe mirror
    run(
        c,
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        f"u='{INDEX}/pip/'\n"
        "print(urllib.request.urlopen(u, timeout=30).status, u)\n"
        "PY",
        timeout=60,
    )

    px = (
        f"export PATH={REMOTE_ENV}/uv-bin:$PATH "
        f"UV_INDEX_URL={INDEX} "
        f"PIP_INDEX_URL={INDEX} "
        f"UV_HTTP_TIMEOUT=120"
    )
    print("\n=== uv sync via tuna mirror ===")
    code, _ = run(c, f"{px}; cd {REMOTE_PROJECT} && {UV} sync --no-dev --index-url {INDEX}", timeout=2400)
    if code != 0:
        print("retry with aliyun…")
        alt = "https://mirrors.aliyun.com/pypi/simple"
        code, _ = run(
            c,
            f"export PATH={REMOTE_ENV}/uv-bin:$PATH UV_INDEX_URL={alt}; "
            f"cd {REMOTE_PROJECT} && {UV} sync --no-dev --index-url {alt}",
            timeout=2400,
        )
    if code != 0:
        c.close()
        return code

    run(
        c,
        f"rm -rf {REMOTE_ENV}/seeed-arm-control; "
        f"ln -sfn {REMOTE_PROJECT}/.venv {REMOTE_ENV}/seeed-arm-control; "
        f"test -x {REMOTE_PROJECT}/.venv/bin/uvicorn && echo UVICORN_OK",
    )
    run(c, "pkill -f 'uvicorn backend.app:app' || true")
    time.sleep(1)
    start = (
        f"cd {REMOTE_PROJECT} && set -a && . {ENV_FILE} && set +a && "
        f"nohup {REMOTE_PROJECT}/.venv/bin/uvicorn backend.app:app "
        f"--host 0.0.0.0 --port 1882 > {LOG} 2>&1 & echo pid=$!"
    )
    run(c, f"sg dialout -c {start!r}")
    time.sleep(8)
    run(c, "ss -lptn | grep 1882 || true")
    run(c, f"tail -n 120 {LOG}")
    run(
        c,
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print(urllib.request.urlopen('http://127.0.0.1:1882/api/health', timeout=8).read().decode())\n"
        "PY",
    )
    c.close()
    print("\nOK -> http://192.168.0.103:1882")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
