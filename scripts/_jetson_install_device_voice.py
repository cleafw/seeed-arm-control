# -*- coding: utf-8 -*-
"""Sync device-listen voice stack to Jetson and run install_voice_jetson.sh."""
from __future__ import annotations

import io
import os
import tarfile
import time

import paramiko

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"


def main() -> int:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in (
            "voice",
            "backend/voice",
            "backend/app.py",
            "backend/models.py",
            "deploy/systemd",
            "deploy/install_voice_jetson.sh",
            "docs/语音部署说明.md",
            "frontend/dist",
        ):
            full = os.path.join(ROOT, rel)
            if os.path.exists(full):
                tar.add(full, arcname=rel)
    data = buf.getvalue()
    print(f"packed {len(data)}", flush=True)

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    rt = "/tmp/voice-device.tgz"
    with sftp.file(rt, "wb") as f:
        f.write(data)
    sftp.close()

    def run(cmd: str, timeout: int = 600) -> int:
        print("$", cmd[:180], flush=True)
        _, o, e = c.exec_command(cmd, timeout=timeout)
        # stream-ish
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
        if out.strip():
            print(out[-8000:], flush=True)
        if err.strip():
            print("ERR", err[-3000:], flush=True)
        print(f"[exit {code}]", flush=True)
        return code

    run(f"tar -xzf {rt} -C {REMOTE} && rm -f {rt} && chmod +x {REMOTE}/deploy/install_voice_jetson.sh")
    # non-interactive sudo
    code = run(
        f"echo {PWD} | sudo -S bash {REMOTE}/deploy/install_voice_jetson.sh",
        timeout=900,
    )
    c.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
