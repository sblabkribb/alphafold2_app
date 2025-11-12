#!/usr/bin/env bash

set -euo pipefail

RUNPOD_VOLUME_ROOT="${RUNPOD_VOLUME_ROOT:-/runpod-volume}"
ALPHAFOLD_DB_PATH="${ALPHAFOLD_DB_PATH:-/data/alphafold}"

echo "--- [DIAG] Filesystems (df -h) ---"; df -h || true
echo "--- [DIAG] Root listing (ls -la /) ---"; ls -la / || true
echo "--- [DIAG] Volume listing (ls -la ${RUNPOD_VOLUME_ROOT}) ---"; ls -la "${RUNPOD_VOLUME_ROOT}" || true
echo "--- [DIAG] Alphafold DB path (ls -la ${ALPHAFOLD_DB_PATH}) ---"; ls -la "${ALPHAFOLD_DB_PATH}" || true
echo "--- [DIAG] Disk usage ---"
du -sh "${ALPHAFOLD_DB_PATH}" 2>/dev/null || true
du -sh "${ALPHAFOLD_DB_PATH}/models" 2>/dev/null || true

echo "[DIAG] Completed."

