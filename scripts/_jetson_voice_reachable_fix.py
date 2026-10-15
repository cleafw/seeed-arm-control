# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=20, allow_agent=False, look_for_keys=False)
PWD = "seeed"


def run(cmd, timeout=60):
    print("$", cmd[:200], flush=True)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if out.strip():
        print(out[-4000:], flush=True)
    if err.strip():
        print("ERR", err[-1000:], flush=True)


# force Core to re-ping voice health
run(
    "python3 - <<'PY'\n"
    "import json,urllib.request\n"
    "body=json.dumps({'enabled':True,'policy':'follow_first'}).encode()\n"
    "req=urllib.request.Request('http://127.0.0.1:1882/api/voice/settings',data=body,headers={'Content-Type':'application/json'},method='POST')\n"
    "print(urllib.request.urlopen(req,timeout=8).read().decode())\n"
    "print(urllib.request.urlopen('http://127.0.0.1:1883/health',timeout=5).read().decode())\n"
    "PY"
)

# Make systemd start the already-running style: fix Requires→After only, add EnvironmentFile vosk
run(
    f"echo {PWD} | sudo -S bash -lc '"
    "cat >/etc/systemd/system/seeed-arm-voice.service <<EOF\n"
    "[Unit]\n"
    "Description=seeed-arm-control Voice (ReSpeaker)\n"
    "After=network-online.target sound.target seeed-arm-control.service\n"
    "Wants=network-online.target seeed-arm-control.service\n"
    "\n"
    "[Service]\n"
    "Type=simple\n"
    "User=seeed\n"
    "Group=seeed\n"
    "SupplementaryGroups=audio dialout\n"
    "WorkingDirectory=/home/seeed/seeed/Project/seeed-arm-control\n"
    "EnvironmentFile=-/home/seeed/seeed/ENV/seeed-arm-control.env\n"
    "Environment=PYTHONUNBUFFERED=1\n"
    "Environment=REBOT_CORE_URL=http://127.0.0.1:1882\n"
    "Environment=VOICE_DEVICE_LISTEN=1\n"
    "Environment=VOICE_ALSA_DEVICE=plughw:CARD=ArrayUAC10,DEV=0\n"
    "ExecStart=/home/seeed/seeed/Project/seeed-arm-control/.venv/bin/uvicorn voice.app:app --host 0.0.0.0 --port 1883\n"
    "Restart=always\n"
    "RestartSec=3\n"
    "\n"
    "[Install]\n"
    "WantedBy=multi-user.target\n"
    "EOF\n"
    "systemctl daemon-reload\n"
    "systemctl enable seeed-arm-control.service seeed-arm-voice.service\n"
    "'"
)

run("ss -lptn | grep -E '1882|1883'")
c.close()
