# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
_, o, e = c.exec_command(
    "grep -iE 'Safety|spike|pause|Resume|→ follow|→ paused|estop|Heard' "
    "/home/seeed/seeed/ENV/seeed-arm-control.log | tail -60",
    timeout=30,
)
print(o.read().decode("utf-8", "replace")[-6000:])
c.close()
