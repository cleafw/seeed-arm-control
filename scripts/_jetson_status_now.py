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


def run(cmd, timeout=40):
    print("$", cmd[:160], flush=True)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        print(out[-4000:], flush=True)
    if err.strip():
        print("ERR", err[-800:], flush=True)
    print(f"[exit {code}]", flush=True)


run("ss -lptn | grep -E '1882|1883' || echo 'ports down'")
run("systemctl is-active seeed-arm-control.service seeed-arm-voice.service 2>&1 || true")
run("ls -lh /tmp/vosk-model-small-cn-0.22.zip 2>&1; ls -lh /home/seeed/seeed/Project/seeed-arm-control/models 2>&1 | head")
run("ps aux | grep -E 'wget|unzip|uvicorn|vosk' | grep -v grep | head -20")
run(
    "python3 - <<'PY'\n"
    "import urllib.request\n"
    "for u in ('http://127.0.0.1:1882/api/health','http://127.0.0.1:1882/api/voice/capability','http://127.0.0.1:1883/health'):\n"
    "  try:\n"
    "    print(u, urllib.request.urlopen(u,timeout=5).read().decode()[:350])\n"
    "  except Exception as e:\n"
    "    print(u, 'FAIL', e)\n"
    "PY"
)
run("tail -n 30 /home/seeed/seeed/ENV/seeed-arm-voice.log 2>/dev/null || echo no-voice-log")
run("journalctl -u seeed-arm-voice -n 25 --no-pager 2>/dev/null || true")
c.close()
