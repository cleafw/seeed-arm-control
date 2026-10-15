# -*- coding: utf-8 -*-
import paramiko
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=15)
cmd = r"""
for d in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyCH343USB0 /dev/ttyCH343USB1 /dev/ttyUSB0; do
  if [ -e "$d" ]; then
    echo "=== $d ==="
    udevadm info -q property -n "$d" 2>/dev/null | egrep 'ID_VENDOR|ID_MODEL|ID_SERIAL|ID_USB_VENDOR_ID|ID_USB_MODEL_ID|DEVNAME' || true
  fi
done
ls -l /dev/ttyACM* /dev/ttyCH343* /dev/ttyUSB* 2>/dev/null || true
"""
_in, out, err = c.exec_command(cmd, timeout=30)
print(out.read().decode("utf-8", "replace"))
print(err.read().decode("utf-8", "replace"))
c.close()
