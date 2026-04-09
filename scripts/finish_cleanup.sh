#!/usr/bin/env bash
set -euo pipefail

PROMPT_ID="${1:-}"
COMFY_BASE_URL="${COMFY_BASE_URL:-http://127.0.0.1:8188}"

RUNTIME_ROOT="${RUNTIME_ROOT:-/dev/shm/comfy-runtime}"
INPUT_DIR="${INPUT_DIR:-$RUNTIME_ROOT/input}"
OUTPUT_DIR="${OUTPUT_DIR:-$RUNTIME_ROOT/output}"
TEMP_DIR="${TEMP_DIR:-$RUNTIME_ROOT/temp}"

post_json() {
  local endpoint="$1"
  local payload="$2"
  curl -fsS -X POST "$COMFY_BASE_URL/$endpoint" \
    -H 'Content-Type: application/json' \
    --data "$payload" >/dev/null || true
}

if [[ -n "$PROMPT_ID" ]]; then
  payload=$(printf '{"delete":["%s"]}' "$PROMPT_ID")
  post_json "history" "$payload"
else
  echo "[cleanup] prompt_id is empty; skip history delete"
fi

for dir in "$INPUT_DIR" "$OUTPUT_DIR" "$TEMP_DIR"; do
  if [[ -d "$dir" ]]; then
    find "$dir" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
  fi
done

post_json "free" '{"free_memory":true,"unload_models":true}'
