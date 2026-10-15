# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
_, o, e = c.exec_command(
    "grep -E 'Heard:|dispatched ok|Safety:|spike|→ follow|→ paused|mode =|Pause|pause\\(|Resume' "
    "/home/seeed/seeed/ENV/seeed-arm-control.log /home/seeed/seeed/ENV/seeed-arm-voice.log 2>/dev/null | tail -80",
    timeout=30,
)
print(o.read().decode("utf-8", "replace")[-8000:])
c.close()
