# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from pathlib import Path

import paramiko

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()

    def put(rel: str) -> None:
        local = ROOT / rel
        remote = f"{REMOTE}/{rel}"
        # ensure parent
        parts = rel.replace("\\", "/").split("/")
        cur = REMOTE
        for p in parts[:-1]:
            cur = f"{cur}/{p}"
            try:
                sftp.stat(cur)
            except OSError:
                sftp.mkdir(cur)
        data = local.read_bytes()
        if rel.endswith(".sh") or rel.endswith(".service"):
            data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        with sftp.file(remote, "wb") as f:
            f.write(data)
        print("put", rel, len(data), flush=True)

    for rel in (
        "deploy/install_voice_jetson.sh",
        "deploy/systemd/seeed-arm-control.service",
        "deploy/systemd/seeed-arm-voice.service",
        "voice/app.py",
        "voice/device_listen.py",
        "voice/asr.py",
        "voice/core_client.py",
        "voice/tts.py",
        "voice/requirements.txt",
        "backend/voice/service.py",
        "backend/voice/nlu.py",
    ):
        put(rel)

    # frontend dist files
    dist = ROOT / "frontend" / "dist"
    for path in dist.rglob("*"):
        if path.is_file():
            rel = path.relative_to(ROOT).as_posix()
            put(rel)

    sftp.chmod(f"{REMOTE}/deploy/install_voice_jetson.sh", 0o755)
    sftp.close()

    def run(cmd: str, timeout: int = 900) -> int:
        print("$", cmd[:200], flush=True)
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
        if out.strip():
            print(out[-10000:], flush=True)
        if err.strip():
            print("ERR", err[-4000:], flush=True)
        print(f"[exit {code}]", flush=True)
        return code

    run(f"sed -n '1,20p' {REMOTE}/deploy/install_voice_jetson.sh")
    code = run(f"echo {PWD} | sudo -S bash {REMOTE}/deploy/install_voice_jetson.sh", timeout=900)
    c.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
