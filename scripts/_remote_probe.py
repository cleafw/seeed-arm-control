# -*- coding: utf-8 -*-
import sys
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOST, USER, PWD = "192.168.0.103", "seeed", "seeed"


def main() -> None:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PWD, timeout=15)

    def run(cmd: str) -> tuple[int, str]:
        print(f"\n$ {cmd}")
        _stdin, stdout, stderr = c.exec_command(cmd, timeout=90)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print("STDERR:", err.rstrip())
        print(f"[exit {code}]")
        return code, out

    run("uname -a; hostname; whoami")
    run("ls -la /home/seeed/seeed/ 2>/dev/null || echo 'no /home/seeed/seeed'")
    run("mkdir -p /home/seeed/seeed/Project /home/seeed/seeed/ENV && ls -la /home/seeed/seeed/")
    run("ls -la /home/seeed/seeed/Project/; ls -la /home/seeed/seeed/ENV/")
    run(
        "command -v docker; command -v docker-compose; command -v uv; "
        "command -v npm; command -v node; command -v python3; "
        "docker --version 2>/dev/null; uv --version 2>/dev/null; "
        "node --version 2>/dev/null; python3 --version 2>/dev/null"
    )
    run("groups; id")
    run("ls -l /dev/ttyUSB* /dev/ttyACM* /dev/ttyCH343* /dev/rebot-* 2>/dev/null | head -30 || true")
    run("df -h /home | tail -3; free -h | head -2")
    run("docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null | head -20")
    c.close()


if __name__ == "__main__":
    main()
