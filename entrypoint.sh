#!/usr/bin/env bash
set -euo pipefail

COMFY_PORT="${COMFY_PORT:-8188}"
START_COMFY_SCRIPT="${START_COMFY_SCRIPT:-/scripts/start_comfy_ram.sh}"

chmod +x /scripts/start_comfy_ram.sh /scripts/finish_cleanup.sh || true

if [[ -x "$START_COMFY_SCRIPT" ]]; then
  echo "Starting ComfyUI with RAM-backed runtime..."
  "$START_COMFY_SCRIPT" "$COMFY_PORT" &
else
  echo "Fallback: starting ComfyUI directly..."
  python /ComfyUI/main.py --listen 0.0.0.0 --port "$COMFY_PORT" &
fi

COMFY_PID=$!

cleanup() {
  if kill -0 "$COMFY_PID" >/dev/null 2>&1; then
    kill "$COMFY_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

echo "Waiting for ComfyUI to be ready..."
max_wait=120
wait_count=0
while [ $wait_count -lt $max_wait ]; do
    if curl -s "http://127.0.0.1:${COMFY_PORT}/" > /dev/null 2>&1; then
        echo "ComfyUI is ready!"
        break
    fi
    echo "Waiting for ComfyUI... ($wait_count/$max_wait)"
    sleep 2
    wait_count=$((wait_count + 2))
done

if [ $wait_count -ge $max_wait ]; then
    echo "Error: ComfyUI failed to start within $max_wait seconds"
    exit 1
fi

echo "Starting the handler..."
exec python handler.py
