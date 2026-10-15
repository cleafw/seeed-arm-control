# -*- coding: utf-8 -*-
"""Assume sshd forwarding already enabled; Clash reverse tunnel + uv sync + start."""
from __future__ import annotations

import socket
import sys
import threading
import time

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
REMOTE_PROJECT = "/home/seeed/seeed/Project/seeed-arm-control"
REMOTE_ENV = "/home/seeed/seeed/ENV"
UV = f"{REMOTE_ENV}/uv-bin/uv"
ENV_FILE = f"{REMOTE_ENV}/seeed-arm-control.env"
LOG = f"{REMOTE_ENV}/seeed-arm-control.log"
LOCAL_CLASH = ("127.0.0.1", 7897)
REMOTE_PROXY_PORT = 17897


class ReverseTunnel:
    def __init__(self, transport, remote_port, local_host, local_port):
        self.transport = transport
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port
        self._stop = threading.Event()

    def start(self):
        self.transport.request_port_forward("", self.remote_port)
        threading.Thread(target=self._serve, daemon=True).start()
        print(f"tunnel remote:{self.remote_port} -> {self.local_host}:{self.local_port}")

    def stop(self):
        self._stop.set()
        try:
            self.transport.cancel_port_forward("", self.remote_port)
        except Exception:
            pass

    def _serve(self):
        while not self._stop.is_set():
            try:
                chan = self.transport.accept(1.0)
            except Exception:
                continue
            if chan is None:
                continue
            threading.Thread(target=self._handle, args=(chan,), daemon=True).start()

    def _handle(self, chan):
        try:
            sock = socket.create_connection((self.local_host, self.local_port), timeout=10)
        except Exception as e:
            print("clash connect fail", e)
            chan.close()
            return

        def pipe(a, b):
            try:
                while True:
                    data = a.recv(65536)
                    if not data:
                        break
                    b.sendall(data)
            except Exception:
                pass
            for x in (a, b):
                try:
                    x.close()
                except Exception:
                    pass

        threading.Thread(target=pipe, args=(chan, sock), daemon=True).start()
        threading.Thread(target=pipe, args=(sock, chan), daemon=True).start()


def run(c, cmd, timeout=2400):
    print(f"\n$ {cmd[:300]}{'...' if len(cmd) > 300 else ''}")
    _i, out, err = c.exec_command(cmd, timeout=timeout, get_pty=True)
    stdout = out.read().decode("utf-8", "replace")
    stderr = err.read().decode("utf-8", "replace")
    code = out.channel.recv_exit_status()
    text = (stdout + ("\n" + stderr if stderr.strip() and stderr not in stdout else "")).rstrip()
    if text:
        print(text[-8000:])
    print(f"[exit {code}]")
    return code, text


def main():
    socket.create_connection(LOCAL_CLASH, timeout=2).close()
    print("Clash OK")

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)
    transport = c.get_transport()
    transport.set_keepalive(30)

    tunnel = ReverseTunnel(transport, REMOTE_PROXY_PORT, LOCAL_CLASH[0], LOCAL_CLASH[1])
    tunnel.start()
    time.sleep(0.5)

    proxy = f"http://127.0.0.1:{REMOTE_PROXY_PORT}"
    px = (
        f"export http_proxy={proxy} https_proxy={proxy} HTTP_PROXY={proxy} HTTPS_PROXY={proxy} "
        f"ALL_PROXY={proxy} all_proxy={proxy} PATH={REMOTE_ENV}/uv-bin:$PATH "
        f"UV_NATIVE_TLS=0"
    )

    code, _ = run(
        c,
        f"{px}; python3 - <<'PY'\n"
        "import urllib.request, os\n"
        "p=os.environ['https_proxy']\n"
        "op=urllib.request.build_opener(urllib.request.ProxyHandler({'http':p,'https':p}))\n"
        "print('pypi', op.open('https://pypi.org/simple/pip/', timeout=60).status)\n"
        "PY",
        timeout=90,
    )
    if code != 0:
        tunnel.stop()
        c.close()
        return code

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

    print("\n=== uv sync (may take several minutes via Clash) ===")
    code, _ = run(c, f"{px}; cd {REMOTE_PROJECT} && {UV} sync --no-dev", timeout=2400)
    if code != 0:
        print("retry uv sync…")
        code, _ = run(c, f"{px}; cd {REMOTE_PROJECT} && {UV} sync --no-dev", timeout=2400)
    if code != 0:
        tunnel.stop()
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

    tunnel.stop()
    c.close()
    print("\nOK -> http://192.168.0.103:1882")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
