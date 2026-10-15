# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "192.168.0.103",
    username="seeed",
    password="seeed",
    timeout=25,
    allow_agent=False,
    look_for_keys=False,
)


def run(cmd: str) -> None:
    print("$", cmd[:160], flush=True)
    _, o, e = c.exec_command(cmd, timeout=60)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if out.strip():
        print(out[-6000:], flush=True)
    if err.strip():
        print("ERR", err[-1500:], flush=True)


run("arecord -l 2>&1 | head -40")
run("cat /proc/asound/cards 2>&1")
run("ls -l /dev/snd 2>&1 | head -30")
run("which arecord ffmpeg pactl 2>/dev/null; python3 -c 'import sounddevice' 2>&1 | head -5")
run(
    "cd /home/seeed/seeed/Project/seeed-arm-control && "
    ".venv/bin/python -c \"import sounddevice as sd; print([(i,d['name'],d['max_input_channels']) for i,d in enumerate(sd.query_devices()) if d['max_input_channels']>0][:20])\" 2>&1 | tail -20"
)
run("systemctl --user list-units --type=service 2>/dev/null | head -20; ls /etc/systemd/system/*rebot* /etc/systemd/system/*seeed* /home/seeed/.config/systemd/user/ 2>/dev/null | head -30")
run("crontab -l 2>/dev/null; ls /home/seeed/seeed/ENV/ 2>/dev/null | head -20")
run("ss -lptn | grep -E '1882|1883' || true")
c.close()
