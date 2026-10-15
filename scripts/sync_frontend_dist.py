# -*- coding: utf-8 -*-
"""Sync frontend/dist to Jetson and soft-reload is not needed (static files)."""
from __future__ import annotations

import io
import os
import sys
import tarfile

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
LOCAL = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
REMOTE_DIST = "/home/seeed/seeed/Project/seeed-arm-control/frontend/dist"


def main():
    local = os.path.abspath(LOCAL)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(local, arcname="dist")
    data = buf.getvalue()
    print(f"packed {len(data)} bytes from {local}", flush=True)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    remote_tar = "/tmp/frontend-dist.tgz"
    with sftp.file(remote_tar, "wb") as f:
        f.write(data)
    sftp.close()

    cmd = (
        f"rm -rf {REMOTE_DIST} && mkdir -p $(dirname {REMOTE_DIST}) && "
        f"tar -xzf {remote_tar} -C $(dirname {REMOTE_DIST}) && "
        f"ls -la {REMOTE_DIST} | head -20 && rm -f {remote_tar}"
    )
    print(f"$ {cmd}", flush=True)
    _i, o, e = c.exec_command(cmd, timeout=60)
    print(o.read().decode("utf-8", "replace"), flush=True)
    err = e.read().decode("utf-8", "replace")
    if err.strip():
        print(err, flush=True)
    code = o.channel.recv_exit_status()
    c.close()
    print(f"[exit {code}]", flush=True)
    print("OK — hard-refresh http://192.168.0.103:1882", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
