# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=25, allow_agent=False, look_for_keys=False)


def run(cmd):
    print("$", cmd)
    _, o, e = c.exec_command(cmd, timeout=30)
    print(o.read().decode()[-3000:])
    err = e.read().decode()
    if err.strip():
        print("ERR", err[-1000:])


run("ls -la /home/seeed/seeed/Project/seeed-arm-control/.venv/bin/ | head -30")
run("ls /home/seeed/seeed/Project/seeed-arm-control/deploy/install_voice_jetson.sh; head -5 /home/seeed/seeed/Project/seeed-arm-control/deploy/install_voice_jetson.sh | od -c | head")
c.close()
