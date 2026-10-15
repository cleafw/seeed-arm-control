# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
_, o, e = c.exec_command(
    "tail -n 100 /home/seeed/seeed/ENV/seeed-arm-voice.log; echo =====; "
    "tail -n 200 /home/seeed/seeed/ENV/seeed-arm-control.log | "
    "grep -iE 'pause|resume|Heard|intent|Safety|spike|paused|voice|dispatch|解除|急停' | tail -50",
    timeout=30,
)
print(o.read().decode("utf-8", "replace")[-8000:])
print(e.read().decode("utf-8", "replace")[-1000:])
c.close()
