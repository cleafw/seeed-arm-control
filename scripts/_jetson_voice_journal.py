# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
_, o, e = c.exec_command(
    "echo seeed | sudo -S journalctl -u seeed-arm-voice -n 40 --no-pager; "
    "echo ---; systemctl is-active seeed-arm-control seeed-arm-voice; "
    "ss -lptn | grep -E '1882|1883' || true",
    timeout=40,
)
print(o.read().decode("utf-8", "replace")[-6000:])
print(e.read().decode("utf-8", "replace")[-1000:])
c.close()
