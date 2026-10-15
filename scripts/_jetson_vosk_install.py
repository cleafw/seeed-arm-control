# -*- coding: utf-8 -*-
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.0.103", username="seeed", password="seeed", timeout=25, allow_agent=False, look_for_keys=False)


def run(cmd, timeout=120):
    print("$", cmd[:200], flush=True)
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode("utf-8", "replace")
    err = e.read().decode("utf-8", "replace")
    code = o.channel.recv_exit_status()
    if out.strip():
        print(out[-5000:], flush=True)
    if err.strip():
        print("ERR", err[-2000:], flush=True)
    print("[exit", code, "]", flush=True)
    return code


run("ps aux | grep -E 'install_voice|uv pip|vosk|wget' | grep -v grep")
run("ls -lh /tmp/vosk* 2>/dev/null; ls -lh /home/seeed/seeed/Project/seeed-arm-control/models 2>/dev/null | head")
run(
    "cd /home/seeed/seeed/Project/seeed-arm-control && "
    "/home/seeed/seeed/ENV/uv-bin/uv pip install --python .venv/bin/python "
    "-i https://pypi.tuna.tsinghua.edu.cn/simple vosk",
    timeout=300,
)
c.close()
