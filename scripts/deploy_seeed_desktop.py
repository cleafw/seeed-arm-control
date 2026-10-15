# -*- coding: utf-8 -*-
"""Sync seeed-arm-control to seeed-desktop and bootstrap ENV + service."""
from __future__ import annotations

import io
import os
import sys
import tarfile
import time
from pathlib import Path

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
REMOTE_PROJECT = "/home/seeed/seeed/Project/seeed-arm-control"
REMOTE_ENV = "/home/seeed/seeed/ENV"
LOCAL_ROOT = Path(__file__).resolve().parents[1]

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".idea",
    ".pytest_cache",
    "agent-transcripts",
    ".cursor",
}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def should_exclude(rel: str) -> bool:
    parts = Path(rel).parts
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if Path(rel).suffix in EXCLUDE_SUFFIXES:
        return True
    return False


def make_tar_bytes() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(LOCAL_ROOT):
            # prune
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in files:
                full = Path(root) / name
                rel = full.relative_to(LOCAL_ROOT).as_posix()
                if should_exclude(rel):
                    continue
                tar.add(full, arcname=f"seeed-arm-control/{rel}")
    buf.seek(0)
    data = buf.read()
    print(f"tarball size: {len(data) / 1e6:.1f} MB")
    return data


def ssh_connect() -> paramiko.SSHClient:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=20)
    return c


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 600, sudo: bool = False) -> tuple[int, str, str]:
    if sudo:
        # quote carefully
        cmd = f"echo {PWD!s} | sudo -S -p '' bash -lc {cmd!r}"
    print(f"\n$ {cmd[:200]}{'...' if len(cmd) > 200 else ''}")
    _in, out, err = c.exec_command(cmd, timeout=timeout, get_pty=True)
    stdout = out.read().decode("utf-8", "replace")
    stderr = err.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    if stdout.strip():
        print(stdout[-4000:].rstrip())
    if stderr.strip() and stderr.strip() not in stdout:
        print("STDERR:", stderr[-2000:].rstrip())
    print(f"[exit {code}]")
    return code, stdout, stderr


def main() -> int:
    print("Building tarball…")
    data = make_tar_bytes()

    c = ssh_connect()
    sftp = c.open_sftp()

    run(c, f"mkdir -p {REMOTE_ENV} /home/seeed/seeed/Project /tmp")
    remote_tar = "/tmp/seeed-arm-control-deploy.tar.gz"
    print(f"Uploading to {remote_tar} …")
    with sftp.file(remote_tar, "wb") as rf:
        rf.write(data)
    print("Upload done.")

    # Extract
    run(
        c,
        f"rm -rf {REMOTE_PROJECT} && mkdir -p /home/seeed/seeed/Project && "
        f"tar -xzf {remote_tar} -C /home/seeed/seeed/Project && "
        f"ls -la {REMOTE_PROJECT} | head",
    )

    # Groups for serial + docker
    run(c, "usermod -aG dialout,docker seeed", sudo=True)

    # Install uv into ENV
    run(
        c,
        f"export UV_INSTALL_DIR={REMOTE_ENV}/uv-bin; "
        f"curl -LsSf https://astral.sh/uv/install.sh | sh && "
        f"{REMOTE_ENV}/uv-bin/uv --version",
        timeout=180,
    )

    # Create venv + sync deps (Python 3.12 via uv)
    venv = f"{REMOTE_ENV}/seeed-arm-control"
    uv = f"{REMOTE_ENV}/uv-bin/uv"
    run(
        c,
        f"export PATH={REMOTE_ENV}/uv-bin:$PATH; "
        f"cd {REMOTE_PROJECT} && "
        f"{uv} venv {venv} --python 3.12 && "
        f"{uv} sync --python {venv}/bin/python --no-dev",
        timeout=1200,
    )

    # Env file for service
    env_file = f"{REMOTE_ENV}/seeed-arm-control.env"
    env_body = f"""# seeed-arm-control runtime on seeed-desktop
LEADER_PROFILE=so101_leader
FOLLOWER_PROFILE=so101_follower
# leave ports empty for auto-detect; override if needed:
# MASTER_PORT=/dev/ttyACM0
# SLAVE_PORT=/dev/ttyACM1
REBOT_RECORDINGS_DIR={REMOTE_PROJECT}/recordings
PYTHONUNBUFFERED=1
"""
    with sftp.file(env_file, "w") as ef:
        ef.write(env_body)

    # Stop old process if any
    run(c, "pkill -f 'uvicorn backend.app:app' || true")

    # Start under nohup with dialout via sg if needed
    start_cmd = (
        f"cd {REMOTE_PROJECT} && "
        f"set -a && . {env_file} && set +a && "
        f"nohup {venv}/bin/uvicorn backend.app:app "
        f"--host 0.0.0.0 --port 1882 "
        f"> {REMOTE_ENV}/seeed-arm-control.log 2>&1 & echo $!"
    )
    # Use newgrp/sg dialout so serial works without re-login
    run(
        c,
        f"sg dialout -c {start_cmd!r} || bash -lc {start_cmd!r}",
        timeout=60,
    )

    time.sleep(5)
    run(c, "ss -lptn 'sport = :1882' || netstat -lptn | grep 1882 || true")
    run(c, f"tail -n 40 {REMOTE_ENV}/seeed-arm-control.log || true")
    run(c, "curl -sS http://127.0.0.1:1882/api/health || true")

    sftp.close()
    c.close()
    print("\nDone. Open http://192.168.0.103:1882")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
