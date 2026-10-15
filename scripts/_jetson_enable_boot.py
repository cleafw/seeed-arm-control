# -*- coding: utf-8 -*-
"""Install/enable systemd boot services and hand ports to systemd."""
from __future__ import annotations

from pathlib import Path

import paramiko

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
ROOT = Path(__file__).resolve().parents[1]
REMOTE = "/home/seeed/seeed/Project/seeed-arm-control"


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    for rel in (
        "deploy/systemd/seeed-arm-control.service",
        "deploy/systemd/seeed-arm-voice.service",
    ):
        data = (ROOT / rel).read_bytes().replace(b"\r\n", b"\n")
        remote = f"{REMOTE}/{rel}"
        with sftp.file(remote, "wb") as f:
            f.write(data)
        print("put", rel, flush=True)
    sftp.close()

    def run(cmd: str, timeout: int = 120) -> None:
        print("$", cmd[:220], flush=True)
        _, o, e = c.exec_command(cmd, timeout=timeout)
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        code = o.channel.recv_exit_status()
        if out.strip():
            print(out[-6000:], flush=True)
        if err.strip():
            print("ERR", err[-2000:], flush=True)
        print(f"[exit {code}]", flush=True)

    # Install units, kill ad-hoc uvicorn, start via systemd
    run(
        f"echo {PWD} | sudo -S bash -lc '"
        f"cp {REMOTE}/deploy/systemd/seeed-arm-control.service /etc/systemd/system/ && "
        f"cp {REMOTE}/deploy/systemd/seeed-arm-voice.service /etc/systemd/system/ && "
        f"systemctl daemon-reload && "
        f"systemctl enable seeed-arm-control.service seeed-arm-voice.service && "
        f"systemctl stop seeed-arm-voice.service || true; "
        f"systemctl stop seeed-arm-control.service || true; "
        f"pkill -f \"uvicorn backend.app:app\" || true; "
        f"pkill -f \"uvicorn voice.app:app\" || true; "
        f"sleep 2; "
        f"systemctl start seeed-arm-control.service && "
        f"sleep 4; "
        f"systemctl start seeed-arm-voice.service && "
        f"sleep 8; "
        f"systemctl is-enabled seeed-arm-control seeed-arm-voice; "
        f"systemctl is-active seeed-arm-control seeed-arm-voice; "
        f"ss -lptn | grep -E \"1882|1883\" || true; "
        f"systemctl --no-pager --full status seeed-arm-control seeed-arm-voice | head -80"
        f"'",
        timeout=180,
    )
    run(
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "print('core', urllib.request.urlopen('http://127.0.0.1:1882/api/health',timeout=8).read().decode()[:220])\n"
        "print('voice', urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=8).read().decode())\n"
        "PY"
    )
    c.close()
    print(
        "Boot autostart: systemctl enable done. Reboot to verify cold start.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
