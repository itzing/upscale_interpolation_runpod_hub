# UPSCALE-02 - Support secure-jobs paths and remove plaintext fallbacks

## Summary

Bring the upscale endpoint in line with the secure transport contract used by Engui and ZImage.

Current failure mode:
- secure media input points to `/runpod-volume/secure-jobs/...`
- on worker, the actual accessible path may be `/secure-jobs/...`
- endpoint currently opens `storage_path` literally and fails with `No such file or directory`

Required changes:
- accept secure media input paths under either `/runpod-volume/secure-jobs/...` or `/secure-jobs/...`
- resolve missing local secure-jobs paths by trying the equivalent alternate mount path
- accept transport output directories under either `/runpod-volume/secure-jobs/...` or `/secure-jobs/...`
- keep transport result encryption for secure flow
- avoid plaintext transport fallbacks for the secure path

## Acceptance Criteria

- secure input decrypt path works when the descriptor points at the alternate secure-jobs mount path
- secure result writing works to secure-jobs through the resolved local mount path
- endpoint preserves encrypted transport_result behavior
- no plaintext file-path fallback is used for secure transport jobs
- `python3 -m py_compile handler.py` passes
