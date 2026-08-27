#!/usr/bin/env bash
# Wait until Core health responds (seeed-arm-voice.service ExecStartPre).
set -euo pipefail
BASE="${REBOT_CORE_URL:-http://127.0.0.1:1882}"
URL="${BASE%/}/api/health"
for _ in $(seq 1 60); do
  if python3 -c "import urllib.request; urllib.request.urlopen('$URL', timeout=2).read()" 2>/dev/null; then
    exit 0
  fi
  sleep 1
done
echo "Core not ready: $URL" >&2
exit 1
