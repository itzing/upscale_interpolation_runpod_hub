# UPSCALE-03 - Download secure media inputs from S3 when not mounted locally

## Summary

Secure upscale input currently fails when `media_inputs[].storage_path` points to `/runpod-volume/secure-jobs/...` or `/secure-jobs/...`, the ciphertext object exists in S3, but the worker does not expose that object as a mounted local file.

Current failure mode:
- Engui uploads encrypted ciphertext into S3 under `secure-jobs/...`
- endpoint resolves the local secure-jobs mount path and calls `open(...)`
- worker has no corresponding mounted file, so decrypt fails with `No such file or directory`

Required changes:
- keep the current local secure-jobs path resolution logic
- when the resolved local path does not exist, derive the S3 object key from the secure storage path
- download ciphertext from the configured S3 bucket using endpoint environment variables
- decrypt the downloaded ciphertext with the existing envelope flow
- preserve the secure-only behavior, with no plaintext fallback for secure jobs

## Acceptance Criteria

- secure media input succeeds when the ciphertext exists only in S3 and is not mounted locally
- local mounted secure-jobs files still work
- S3 key derivation from `/runpod-volume/secure-jobs/...` and `/secure-jobs/...` is consistent with Engui storage keys
- decryption still uses the existing envelope binding checks
- `python3 -m py_compile handler.py` passes
