# -*- coding: utf-8 -*-
import sys
import paramiko
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"
FIX = Path(__file__).with_name("_fix_sshd_forward.sh").read_text(encoding="utf-8")

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=25, allow_agent=False, look_for_keys=False)

sftp = c.open_sftp()
path = "/home/seeed/_fix_sshd_forward.sh"
with sftp.file(path, "w") as f:
    f.write(FIX)
sftp.chmod(path, 0o755)
sftp.close()

def run(cmd):
    print("$", cmd[:200])
    _i, o, e = c.exec_command(cmd, timeout=60, get_pty=True)
    print(o.read().decode("utf-8", "replace"))
    print("exit", o.channel.recv_exit_status())

run(f"ls -la {path}; head -5 {path}")
run(f"echo {PWD} | sudo -S -p '' bash {path}")
run("echo seeed | sudo -S -p '' sshd -T 2>/dev/null | egrep 'allowtcpforwarding|gatewayports' || true")
run("mount | grep ' /tmp ' || true")
c.close()
