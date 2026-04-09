# Video and Image Upscale for RunPod Serverless
[한국어 README 보기](README_kr.md)

This repository provides a RunPod Serverless worker for:
- image upscale
- video upscale
- video upscale plus frame interpolation

It is adapted for Engui Studio and now supports the same secure request and result contract style used by `ZImage_runpod-zimage`.

## Features

- ComfyUI-based workflows
- image input and video input support
- base64, URL, or path inputs
- AES-256-GCM secure request envelope via `_secure`
- AES-256-GCM encrypted result payloads
- masked request logging
- prompt-id scoped cleanup hook
- RAM-backed ComfyUI runtime support

## Main files

- `handler.py` - RunPod worker entrypoint
- `entrypoint.sh` - container startup
- `scripts/start_comfy_ram.sh` - starts ComfyUI with RAM-backed runtime dirs
- `scripts/finish_cleanup.sh` - prompt history delete + runtime cleanup
- `workflow/image_upscale.json`
- `workflow/video_upscale_api.json`
- `workflow/video_upscale_interpolation_api.json`

## Input contract

The worker accepts either image or video input.

### Image input
Use one of:
- `image_path`
- `image_url`
- `image_base64`

### Video input
Use one of:
- `video_path`
- `video_url`
- `video_base64`

### Common fields
- `task_type`
  - for video: `upscale` or `upscale_and_interpolation`
  - image input always maps to image upscale
- `output`
  - `base64` returns inline result payload
  - `file_path` returns `image_path` or `video_path`

## Secure request contract

When secure mode is enabled, Engui sends sensitive fields inside `_secure`.

### `_secure` shape

```json
{
  "v": 1,
  "alg": "AES-256-GCM",
  "kid": "upscale-k1",
  "ts": 1712660000,
  "nonce": "...base64...",
  "ciphertext": "...base64..."
}
```

### Request AAD

```text
engui:upscale-interpolation:v1
```

### Recommended encrypted fields

- `image_base64`
- `video_base64`
- optionally `image_url`
- optionally `video_url`

### Required env vars for request decrypt

- `UPSCALE_FIELD_ENC_KEY_B64`
- or fallback `FIELD_ENC_KEY_B64`

The key must decode to exactly 32 bytes.

## Result contract

When `output=base64` and an encryption key is configured, the worker returns encrypted media blocks instead of plaintext media.

### Encrypted image result

```json
{
  "image_encrypted": {
    "v": 1,
    "alg": "AES-256-GCM",
    "kid": "upscale-k1",
    "nonce": "...base64...",
    "ciphertext": "...base64...",
    "mime": "image/png"
  }
}
```

AAD:

```text
engui:upscale-interpolation:image-result:v1
```

### Encrypted video result

```json
{
  "video_encrypted": {
    "v": 1,
    "alg": "AES-256-GCM",
    "kid": "upscale-k1",
    "nonce": "...base64...",
    "ciphertext": "...base64...",
    "mime": "video/mp4"
  }
}
```

AAD:

```text
engui:upscale-interpolation:video-result:v1
```

### Plaintext fallback

If no encryption key is configured and `output=base64`, the worker falls back to plaintext:
- `image`
- `video`

If `output=file_path`, the worker returns:
- `image_path`
- `video_path`

## Runtime layout

Recommended runtime env:

- `RUNTIME_ROOT=/dev/shm/comfy-runtime`
- `INPUT_DIR=/dev/shm/comfy-runtime/input`
- `OUTPUT_DIR=/dev/shm/comfy-runtime/output`
- `TEMP_DIR=/dev/shm/comfy-runtime/temp`
- `USER_DIR=/dev/shm/comfy-runtime/user`

`entrypoint.sh` starts ComfyUI through `scripts/start_comfy_ram.sh` by default.

## Cleanup behavior

After each job, the worker attempts to:
- delete current prompt history only
- clear runtime input/output/temp dirs
- request ComfyUI memory free / model unload
- log cleanup verification details

Cleanup script path can be overridden with:
- `FINISH_CLEANUP_SCRIPT`

## Example requests

### Image upscale via plaintext base64

```json
{
  "input": {
    "image_base64": "...",
    "output": "base64"
  }
}
```

### Video upscale via secure payload

```json
{
  "input": {
    "task_type": "upscale",
    "output": "base64",
    "_secure": {
      "v": 1,
      "alg": "AES-256-GCM",
      "kid": "upscale-k1",
      "ts": 1712660000,
      "nonce": "...",
      "ciphertext": "..."
    }
  }
}
```

### Video upscale plus interpolation via file path

```json
{
  "input": {
    "task_type": "upscale_and_interpolation",
    "video_path": "/runpod-volume/input.mp4",
    "output": "file_path"
  }
}
```

## Remaining notes

This repository is now aligned with the first secure Engui contract pass.
The next layer of hardening is operational validation in a real deployed RunPod endpoint with the shared key configured on both sides.
