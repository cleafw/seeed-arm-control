# -*- coding: utf-8 -*-
"""Finish ENV bootstrap + start seeed-arm-control on seeed-desktop."""
from __future__ import annotations

import sys
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
REMOTE_PROJECT = "/home/seeed/seeed/Project/seeed-arm-control"
REMOTE_ENV = "/home/seeed/seeed/ENV"
VENV = f"{REMOTE_ENV}/seeed-arm-control"
UV_DIR = f"{REMOTE_ENV}/uv-bin"
UV = f"{UV_DIR}/uv"
ENV_FILE = f"{REMOTE_ENV}/seeed-arm-control.env"
LOG = f"{REMOTE_ENV}/seeed-arm-control.log"


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 900) -> tuple[int, str]:
    print(f"\n$ {cmd[:240]}{'...' if len(cmd) > 240 else ''}")
    _i, out, err = c.exec_command(cmd, timeout=timeout, get_pty=True)
    stdout = out.read().decode("utf-8", "replace")
    stderr = err.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    text = (stdout + ("\n" + stderr if stderr.strip() else "")).rstrip()
    if text:
        print(text[-5000:])
    print(f"[exit {code}]")
    return code, text


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=20)

    # Ensure groups (already done once, idempotent)
    run(c, f"echo {PWD} | sudo -S -p '' usermod -aG dialout,docker seeed")

    # Install uv via python urllib (no curl)
    install_uv = f"""
python3 - <<'PY'
import os, sys, urllib.request, tarfile, io, stat
from pathlib import Path
dest = Path("{UV_DIR}")
dest.mkdir(parents=True, exist_ok=True)
uv_bin = dest / "uv"
if uv_bin.exists():
    print("uv already present", uv_bin)
    sys.exit(0)
# aarch64 Linux uv release
url = "https://github.com/astral-sh/uv/releases/download/0.8.4/uv-aarch64-unknown-linux-gnu.tar.gz"
print("Downloading", url)
data = urllib.request.urlopen(url, timeout=120).read()
print("bytes", len(data))
bio = io.BytesIO(data)
with tarfile.open(fileobj=bio, mode="r:gz") as tar:
    members = [m for m in tar.getmembers() if m.name.endswith("/uv") or m.name == "uv"]
    if not members:
        # extract all and find
        tar.extractall(dest)
    else:
        for m in members:
            m.name = Path(m.name).name
            tar.extract(m, dest)
# locate
cands = list(dest.rglob("uv"))
print("candidates", cands)
src = None
for p in cands:
    if p.is_file() and p.name == "uv":
        src = p
        break
if src is None:
    raise SystemExit("uv binary not found in archive")
if src != uv_bin:
    uv_bin.write_bytes(src.read_bytes())
uv_bin.chmod(uv_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print("installed", uv_bin, "size", uv_bin.stat().st_size)
PY
{UV} --version
"""
    code, _ = run(c, install_uv, timeout=300)
    if code != 0:
        # fallback mirror or pip
        print("primary uv download failed; trying pip install uv into ENV")
        run(
            c,
            f"python3 -m pip install --user -U pip && "
            f"python3 -m pip install --user uv && "
            f"python3 -m uv --version || ~/.local/bin/uv --version",
            timeout=300,
        )
        # resolve UV path
        run(c, f"mkdir -p {UV_DIR}; ln -sf ~/.local/bin/uv {UV} || ln -sf $(python3 -c 'import shutil;print(shutil.which(\"uv\") or \"\")') {UV}; {UV} --version")

    # Write env file
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
    print("wrote", ENV_FILE)

    # Create venv + sync
    code, _ = run(
        c,
        f"export PATH={UV_DIR}:$HOME/.local/bin:$PATH; "
        f"cd {REMOTE_PROJECT} && "
        f"{UV} venv {VENV} --python 3.12 && "
        f"{UV} sync --python {VENV}/bin/python --no-dev",
        timeout=1800,
    )
    if code != 0:
        print("uv sync failed")
        return code

    # Stop previous
    run(c, "pkill -f 'uvicorn backend.app:app' || true")
    time.sleep(1)

    # Start with dialout group
    start = (
        f"cd {REMOTE_PROJECT} && set -a && . {ENV_FILE} && set +a && "
        f"nohup {VENV}/bin/uvicorn backend.app:app --host 0.0.0.0 --port 1882 "
        f"> {LOG} 2>&1 & echo started_pid=$!"
    )
    run(c, f"sg dialout -c {start!r}")
    time.sleep(6)

    run(c, f"ss -lptn | grep 1882 || true")
    run(c, f"tail -n 60 {LOG} || true")
    run(
        c,
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "try:\n"
        "  print(urllib.request.urlopen('http://127.0.0.1:1882/api/health', timeout=5).read().decode())\n"
        "except Exception as e:\n"
        "  print('health_error', e)\n"
        "PY",
    )

    c.close()
    print("\nUI: http://192.168.0.103:1882")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
