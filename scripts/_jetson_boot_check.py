# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "192.168.0.103",
    username="seeed",
    password="seeed",
    timeout=20,
    allow_agent=False,
    look_for_keys=False,
)


def run(cmd, timeout=60):
    print("$", cmd[:200], flush=True)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if out.strip():
        print(out[-5000:], flush=True)
    if err.strip():
        print("ERR", err[-1500:], flush=True)


run(
    "systemctl is-enabled seeed-arm-control.service seeed-arm-voice.service 2>&1; "
    "systemctl is-active seeed-arm-control.service seeed-arm-voice.service 2>&1; "
    "ls -l /etc/systemd/system/seeed-arm-*.service 2>&1; "
    "ss -lptn | grep -E '1882|1883' || echo ports-down"
)
run("echo seeed | sudo -S systemctl cat seeed-arm-control.service 2>&1 | head -40")
run("echo seeed | sudo -S systemctl cat seeed-arm-voice.service 2>&1 | head -50")
run("grep -E '^VOICE_|^REBOT_CORE' /home/seeed/seeed/ENV/seeed-arm-control.env 2>&1 | head -20")
c.close()
