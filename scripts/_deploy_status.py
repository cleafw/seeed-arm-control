# -*- coding: utf-8 -*-
import sys
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=15)

def run(cmd):
    print(f"\n$ {cmd}")
    _i, o, e = c.exec_command(cmd, timeout=60, get_pty=True)
    out = o.read().decode("utf-8", "replace")
    print(out[-3000:] if out else "")
    print("[exit", o.channel.recv_exit_status(), "]")

run("ls -la /home/seeed/seeed/ENV /home/seeed/seeed/Project/seeed-arm-control 2>&1 | head -40")
run("ls -la /home/seeed/seeed/ENV/uv-bin 2>&1; ls -la /home/seeed/seeed/ENV/seeed-arm-control/bin 2>&1 | head")
run("ss -lptn | grep 1882 || true; pgrep -af uvicorn || true")
run("tail -n 30 /home/seeed/seeed/ENV/seeed-arm-control.log 2>&1 || true")
run("test -f /home/seeed/seeed/Project/seeed-arm-control/frontend/dist/index.html && echo HAS_DIST || echo NO_DIST")
c.close()
