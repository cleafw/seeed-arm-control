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


def run(cmd: str, timeout: int = 90) -> None:
    print("$", cmd[:200], flush=True)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    if out.strip():
        print(out[-5000:], flush=True)
    if err.strip():
        print("ERR", err[-2000:], flush=True)


run("arecord -l | grep -A2 Array || arecord -l")
run("cat /proc/asound/card2/stream0 2>/dev/null | head -40")
# 1s capture from ReSpeaker card 2 device 0
run(
    "arecord -D plughw:2,0 -f S16_LE -r 16000 -c 1 -d 1 /tmp/mic_test.wav 2>&1; "
    "ls -l /tmp/mic_test.wav; "
    "python3 - <<'PY'\n"
    "import wave,math,struct\n"
    "w=wave.open('/tmp/mic_test.wav'); n=w.getnframes(); fr=w.readframes(n); w.close()\n"
    "samples=struct.unpack('<'+str(n)+'h', fr)\n"
    "rms=(sum(x*x for x in samples)/max(1,n))**0.5\n"
    "peak=max(abs(x) for x in samples) if samples else 0\n"
    "print('frames',n,'rms',round(rms,1),'peak',peak)\n"
    "PY"
)
run(
    "cd /home/seeed/seeed/Project/seeed-arm-control && "
    ".venv/bin/pip show vosk faster-whisper openai-whisper 2>/dev/null | grep -E 'Name|Version' ; "
    "ls models 2>/dev/null; ls /home/seeed/seeed/models 2>/dev/null | head; "
    "df -h / | tail -1; free -h | head -2"
)
run("groups; id; ls -l /dev/snd/pcmC2* 2>/dev/null | head")
c.close()
