# -*- coding: utf-8 -*-
"""Finish Jetson deploy via temporary Clash reverse tunnel (Windows 7897)."""
from __future__ import annotations

import select
import socket
import sys
import threading
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
REMOTE_PROJECT = "/home/seeed/seeed/Project/seeed-arm-control"
REMOTE_ENV = "/home/seeed/seeed/ENV"
# Prefer project .venv if present (uv default), else ENV venv
UV = f"{REMOTE_ENV}/uv-bin/uv"
ENV_FILE = f"{REMOTE_ENV}/seeed-arm-control.env"
LOG = f"{REMOTE_ENV}/seeed-arm-control.log"
LOCAL_CLASH = ("127.0.0.1", 7897)
REMOTE_PROXY_PORT = 17897


class ReverseTunnel:
    def __init__(self, transport: paramiko.Transport, remote_port: int, local_host: str, local_port: int):
        self.transport = transport
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self.transport.request_port_forward("", self.remote_port)
        self._thread = threading.Thread(target=self._serve, name="rforward", daemon=True)
        self._thread.start()
        print(f"Reverse tunnel: remote :{self.remote_port} -> {self.local_host}:{self.local_port}")

    def stop(self) -> None:
        self._stop.set()
        try:
            self.transport.cancel_port_forward("", self.remote_port)
        except Exception:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                chan = self.transport.accept(1.0)
            except Exception:
                continue
            if chan is None:
                continue
            thr = threading.Thread(target=self._handle, args=(chan,), daemon=True)
            thr.start()

    def _handle(self, chan: paramiko.Channel) -> None:
        try:
            sock = socket.create_connection((self.local_host, self.local_port), timeout=10)
        except Exception as e:
            print(f"tunnel connect clash failed: {e}")
            chan.close()
            return

        def pipe(src, dst):
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except Exception:
                pass
            try:
                src.close()
            except Exception:
                pass
            try:
                dst.close()
            except Exception:
                pass

        t1 = threading.Thread(target=pipe, args=(chan, sock), daemon=True)
        t2 = threading.Thread(target=pipe, args=(sock, chan), daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()


def run(c: paramiko.SSHClient, cmd: str, timeout: int = 1800) -> tuple[int, str]:
    print(f"\n$ {cmd[:260]}{'...' if len(cmd) > 260 else ''}")
    _i, out, err = c.exec_command(cmd, timeout=timeout, get_pty=True)
    stdout = out.read().decode("utf-8", "replace")
    stderr = err.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    text = (stdout + ("\n" + stderr if stderr.strip() and stderr not in stdout else "")).rstrip()
    if text:
        print(text[-6000:])
    print(f"[exit {code}]")
    return code, text


def main() -> int:
    # Sanity: Clash listening
    try:
        s = socket.create_connection(LOCAL_CLASH, timeout=2)
        s.close()
        print(f"Clash OK at {LOCAL_CLASH[0]}:{LOCAL_CLASH[1]}")
    except OSError as e:
        print(f"Clash not reachable at {LOCAL_CLASH}: {e}")
        return 1

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=20, allow_agent=False, look_for_keys=False)
    transport = c.get_transport()
    assert transport is not None
    transport.set_keepalive(30)

    tunnel = ReverseTunnel(transport, REMOTE_PROXY_PORT, LOCAL_CLASH[0], LOCAL_CLASH[1])
    tunnel.start()
    time.sleep(0.5)

    proxy = f"http://127.0.0.1:{REMOTE_PROXY_PORT}"
    proxy_exports = (
        f"export http_proxy={proxy} https_proxy={proxy} HTTP_PROXY={proxy} HTTPS_PROXY={proxy} "
        f"ALL_PROXY={proxy} all_proxy={proxy} "
        f"UV_INDEX_URL=https://pypi.org/simple "
        f"PATH={REMOTE_ENV}/uv-bin:$PATH"
    )

    # Test proxy from Jetson
    run(
        c,
        f"{proxy_exports}; python3 - <<'PY'\n"
        "import urllib.request, os\n"
        "proxy=os.environ.get('https_proxy')\n"
        "print('proxy', proxy)\n"
        "opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':proxy,'https':proxy}))\n"
        "print(opener.open('https://pypi.org/simple/pip/', timeout=30).status)\n"
        "PY",
        timeout=60,
    )

    # Write env for runtime (no proxy needed at runtime)
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

    # Ensure dialout
    run(c, f"echo {PWD} | sudo -S -p '' usermod -aG dialout,docker seeed")

    # uv sync into project .venv (already partially created)
    code, _ = run(
        c,
        f"{proxy_exports}; cd {REMOTE_PROJECT} && "
        f"{UV} sync --no-dev",
        timeout=2400,
    )
    if code != 0:
        print("uv sync failed; retry once…")
        code, _ = run(
            c,
            f"{proxy_exports}; cd {REMOTE_PROJECT} && "
            f"{UV} sync --no-dev",
            timeout=2400,
        )
    if code != 0:
        tunnel.stop()
        c.close()
        return code

    # Also create ENV venv symlink convenience → project .venv
    run(
        c,
        f"rm -rf {REMOTE_ENV}/seeed-arm-control && "
        f"ln -sfn {REMOTE_PROJECT}/.venv {REMOTE_ENV}/seeed-arm-control && "
        f"ls -la {REMOTE_ENV}/seeed-arm-control/bin/uvicorn",
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
    run(c, f"tail -n 80 {LOG}")
    run(
        c,
        "python3 - <<'PY'\n"
        "import urllib.request\n"
        "try:\n"
        "  print(urllib.request.urlopen('http://127.0.0.1:1882/api/health', timeout=8).read().decode())\n"
        "except Exception as e:\n"
        "  print('health_error', e)\n"
        "PY",
    )

    tunnel.stop()
    c.close()
    print("\nDeploy finished. Open: http://192.168.0.103:1882")
    print("(Clash reverse tunnel closed; service keeps running without proxy.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
