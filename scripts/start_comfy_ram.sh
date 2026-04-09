#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-8188}"
COMFY_ROOT="${COMFY_ROOT:-/ComfyUI}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

RUNTIME_ROOT="${RUNTIME_ROOT:-/dev/shm/comfy-runtime}"
INPUT_DIR="${INPUT_DIR:-$RUNTIME_ROOT/input}"
OUTPUT_DIR="${OUTPUT_DIR:-$RUNTIME_ROOT/output}"
TEMP_DIR="${TEMP_DIR:-$RUNTIME_ROOT/temp}"
USER_DIR="${USER_DIR:-$RUNTIME_ROOT/user}"
COMFY_SILENT="${COMFY_SILENT:-1}"

mkdir -p "$INPUT_DIR" "$OUTPUT_DIR" "$TEMP_DIR" "$USER_DIR"

cd "$COMFY_ROOT"

if [[ "$COMFY_SILENT" == "1" ]]; then
  exec "$PYTHON_BIN" main.py \
    --listen 0.0.0.0 \
    --port "$PORT" \
    --input-directory "$INPUT_DIR" \
    --output-directory "$OUTPUT_DIR" \
    --temp-directory "$TEMP_DIR" \
    --user-directory "$USER_DIR" \
    >/dev/null 2>&1
else
  exec "$PYTHON_BIN" main.py \
    --listen 0.0.0.0 \
    --port "$PORT" \
    --input-directory "$INPUT_DIR" \
    --output-directory "$OUTPUT_DIR" \
    --temp-directory "$TEMP_DIR" \
    --user-directory "$USER_DIR"
fi
