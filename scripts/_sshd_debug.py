# -*- coding: utf-8 -*-
import sys
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)

def run(cmd):
    print("$", cmd)
    _i, o, e = c.exec_command(cmd, timeout=60, get_pty=True)
    print(o.read().decode("utf-8", "replace"))
    print("exit", o.channel.recv_exit_status())

run("echo seeed | sudo -S -p '' sshd -T | egrep 'allowtcpforwarding|gatewayports|permitopen|disableforwarding|permitlisten'")
run("echo seeed | sudo -S -p '' grep -nE 'Match |AllowTcpForwarding|DisableForwarding|PermitOpen' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/* 2>/dev/null")
run("echo seeed | sudo -S -p '' systemctl restart ssh; sleep 1; echo seeed | sudo -S -p '' sshd -T | egrep 'allowtcpforwarding|gatewayports'")
c.close()
