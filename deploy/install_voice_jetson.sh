#!/usr/bin/env bash
# Install Core + Voice systemd units and Vosk CN model on Jetson (run as seeed with sudo).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/home/seeed/seeed/ENV/seeed-arm-control.env}"
MODEL_DIR="${ROOT}/models/vosk-model-small-cn-0.22"
MODEL_ZIP="/tmp/vosk-model-small-cn-0.22.zip"
MODEL_URL="https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"

echo "==> ensure vosk + model"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
UV_BIN="${UV_BIN:-}"
if [[ -z "$UV_BIN" ]]; then
  for c in "$ROOT/.venv/bin/uv" /home/seeed/seeed/ENV/uv-bin/uv "$HOME/.local/bin/uv" /usr/local/bin/uv; do
    if [[ -x "$c" ]]; then UV_BIN="$c"; break; fi
  done
fi
if [[ -n "$UV_BIN" ]]; then
  "$UV_BIN" pip install --python "$PY" -i https://pypi.tuna.tsinghua.edu.cn/simple vosk
elif "$PY" -c "import ensurepip" 2>/dev/null; then
  "$PY" -m ensurepip --upgrade
  "$PY" -m pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple vosk
else
  echo "Need uv to install vosk (pip missing in venv)" >&2
  exit 1
fi

if [[ ! -d "$MODEL_DIR" ]]; then
  mkdir -p "$ROOT/models"
  if [[ ! -f "$MODEL_ZIP" ]]; then
    echo "Downloading Vosk CN model..."
    wget -O "$MODEL_ZIP" "$MODEL_URL" || curl -L -o "$MODEL_ZIP" "$MODEL_URL"
  fi
  unzip -qo "$MODEL_ZIP" -d "$ROOT/models"
fi
test -d "$MODEL_DIR"

echo "==> patch env $ENV_FILE"
python3 - <<PY
from pathlib import Path
p = Path("${ENV_FILE}")
text = p.read_text(encoding="utf-8") if p.exists() else ""
keys = {
    "VOICE_ENABLED": "1",
    "VOICE_HEALTH_URL": "http://127.0.0.1:1883/health",
    "VOICE_POLICY": "follow_first",
    "VOICE_DEVICE_LISTEN": "1",
    "VOICE_VOSK_MODEL": "${MODEL_DIR}",
    "VOICE_ALSA_DEVICE": "plughw:CARD=ArrayUAC10,DEV=0",
    "REBOT_CORE_URL": "http://127.0.0.1:1882",
}
lines = []
seen = set()
for ln in text.splitlines():
    if not ln.strip() or ln.lstrip().startswith("#") or "=" not in ln:
        lines.append(ln)
        continue
    k = ln.split("=", 1)[0].strip()
    if k in keys:
        lines.append(f"{k}={keys[k]}")
        seen.add(k)
    else:
        lines.append(ln)
for k, v in keys.items():
    if k not in seen:
        lines.append(f"{k}={v}")
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text("\\n".join(lines).rstrip() + "\\n", encoding="utf-8")
vs = Path("${ROOT}/recordings/voice_settings.json")
vs.parent.mkdir(parents=True, exist_ok=True)
vs.write_text('{\\n  "enabled": true,\\n  "policy": "follow_first"\\n}\\n', encoding="utf-8")
print("env + voice_settings updated")
PY

echo "==> install systemd units"
sudo cp "$ROOT/deploy/systemd/seeed-arm-control.service" /etc/systemd/system/
sudo cp "$ROOT/deploy/systemd/seeed-arm-voice.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable seeed-arm-control.service seeed-arm-voice.service

# Stop ad-hoc uvicorn if any
pkill -f 'uvicorn backend.app:app' || true
pkill -f 'uvicorn voice.app:app' || true
sleep 1
sudo systemctl restart seeed-arm-control.service
sleep 4
sudo systemctl restart seeed-arm-voice.service
sleep 3
sudo systemctl --no-pager --full status seeed-arm-control.service seeed-arm-voice.service || true
curl -sS http://127.0.0.1:1882/api/voice/capability | head -c 400; echo
curl -sS http://127.0.0.1:1883/health | head -c 400; echo
echo "OK: boot services enabled. Speak to ReSpeaker after enabling voice on UI (already enabled)."
