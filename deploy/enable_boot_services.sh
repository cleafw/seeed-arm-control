#!/usr/bin/env bash
set -euo pipefail
REMOTE=/home/seeed/seeed/Project/seeed-arm-control
cp "$REMOTE/deploy/systemd/seeed-arm-control.service" /etc/systemd/system/
cp "$REMOTE/deploy/systemd/seeed-arm-voice.service" /etc/systemd/system/
chmod +x "$REMOTE/deploy/wait_core_health.sh" "$REMOTE/deploy/enable_boot_services.sh"
systemctl daemon-reload
systemctl enable seeed-arm-control.service seeed-arm-voice.service
systemctl stop seeed-arm-voice.service || true
systemctl stop seeed-arm-control.service || true
pkill -f 'uvicorn backend.app:app' || true
pkill -f 'uvicorn voice.app:app' || true
sleep 2
systemctl start seeed-arm-control.service
sleep 5
systemctl start seeed-arm-voice.service
sleep 12
echo "=== enabled ==="
systemctl is-enabled seeed-arm-control seeed-arm-voice
echo "=== active ==="
systemctl is-active seeed-arm-control seeed-arm-voice
echo "=== ports ==="
ss -lptn | grep -E '1882|1883' || true
echo "=== status ==="
systemctl --no-pager --full status seeed-arm-control.service seeed-arm-voice.service | head -100
