#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bootstrap_db.sh [--diagnose]

Idempotently prepares Alphafold databases/models.

Behavior:
  - Prefers a persistent volume at ${RUNPOD_VOLUME_ROOT}/alphafold when present.
  - Ensures ${ALPHAFOLD_DB_PATH} points to the persistent location (makes a symlink if possible).
  - Downloads model parameters when missing.
  - Optionally populates DBs if missing using DB_SYNC_CMD or download_db.sh.

Environment variables:
  RUNPOD_VOLUME_ROOT      Root mount for Runpod volume (default: /runpod-volume)
  RUNPOD_DATA_DIR         Target dir on volume (default: ${RUNPOD_VOLUME_ROOT}/alphafold)
  ALPHAFOLD_DB_PATH       Runtime data dir inside container (default: /data/alphafold)
  MODEL_RELEASE_URL       Override model params tar URL
  DB_SYNC_CMD             Optional command to populate databases when missing
  ALLOW_DB_AUTO_DOWNLOAD  If '1', attempt to fetch DB when missing with download_db.sh (default: 0)
  DB_AUTO_PRESET          Preset for auto-download: reduced_dbs|full_dbs (default: reduced_dbs)
EOF
}

DIAGNOSE=0
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ "${1:-}" == "--diagnose" ]]; then
  DIAGNOSE=1
fi

RUNPOD_VOLUME_ROOT="${RUNPOD_VOLUME_ROOT:-/runpod-volume}"
RUNPOD_DATA_DIR="${RUNPOD_DATA_DIR:-${RUNPOD_VOLUME_ROOT}/alphafold}"
ALPHAFOLD_DB_PATH="${ALPHAFOLD_DB_PATH:-/data/alphafold}"
ALPHAFOLD_MODELS_DIR="${ALPHAFOLD_MODELS_DIR:-${ALPHAFOLD_DB_PATH}/models}"

echo "--- [BOOTSTRAP] Environment overview ---"
echo "RUNPOD_VOLUME_ROOT=${RUNPOD_VOLUME_ROOT}"
echo "RUNPOD_DATA_DIR=${RUNPOD_DATA_DIR}"
echo "ALPHAFOLD_DB_PATH=${ALPHAFOLD_DB_PATH}"
echo "ALPHAFOLD_MODELS_DIR=${ALPHAFOLD_MODELS_DIR}"

if [[ "${DIAGNOSE}" == "1" ]]; then
  echo "--- [DIAG] Filesystems (df -h) ---"; df -h || true
  echo "--- [DIAG] Root listing (ls -la /) ---"; ls -la / || true
  echo "--- [DIAG] Volume listing (ls -la ${RUNPOD_VOLUME_ROOT}) ---"; ls -la "${RUNPOD_VOLUME_ROOT}" || true
fi

# Prepare persistent location if the volume mount exists
if [[ -d "${RUNPOD_VOLUME_ROOT}" ]]; then
  mkdir -p "${RUNPOD_DATA_DIR}"
  if [[ ! -e "${ALPHAFOLD_DB_PATH}" ]]; then
    mkdir -p "$(dirname "${ALPHAFOLD_DB_PATH}")"
    ln -s "${RUNPOD_DATA_DIR}" "${ALPHAFOLD_DB_PATH}"
  else
    # If ALPHAFOLD_DB_PATH exists and is not a symlink to RUNPOD_DATA_DIR, try to reconcile.
    if [[ -L "${ALPHAFOLD_DB_PATH}" ]]; then
      current_target="$(readlink -f "${ALPHAFOLD_DB_PATH}")"
      if [[ "${current_target}" != "$(readlink -f "${RUNPOD_DATA_DIR}")" ]]; then
        echo "[BOOTSTRAP] Re-pointing symlink ${ALPHAFOLD_DB_PATH} -> ${RUNPOD_DATA_DIR}"
        rm -f "${ALPHAFOLD_DB_PATH}"
        ln -s "${RUNPOD_DATA_DIR}" "${ALPHAFOLD_DB_PATH}"
      fi
    elif [[ -d "${ALPHAFOLD_DB_PATH}" ]]; then
      # If it's a directory and empty, replace with a symlink
      if [[ -z "$(ls -A "${ALPHAFOLD_DB_PATH}" 2>/dev/null || true)" ]]; then
        rmdir "${ALPHAFOLD_DB_PATH}"
        ln -s "${RUNPOD_DATA_DIR}" "${ALPHAFOLD_DB_PATH}"
      else
        echo "[BOOTSTRAP] ${ALPHAFOLD_DB_PATH} is a non-empty directory; leaving in place."
      fi
    fi
  fi
else
  echo "[BOOTSTRAP] ${RUNPOD_VOLUME_ROOT} not present; using local path ${ALPHAFOLD_DB_PATH}."
  mkdir -p "${ALPHAFOLD_DB_PATH}"
fi

# Ensure models exist; download if missing
NEED_MODELS=1
if [[ -d "${ALPHAFOLD_MODELS_DIR}" ]]; then
  if find "${ALPHAFOLD_MODELS_DIR}" -type f \( -name "*.npz" -o -name "params*.bin" \) | head -n1 >/dev/null 2>&1; then
    NEED_MODELS=0
  fi
fi

if [[ "${NEED_MODELS}" == "1" ]]; then
  echo "[BOOTSTRAP] Alphafold model params not found; downloading..."
  "/app/download_models.sh" "${ALPHAFOLD_DB_PATH}"
else
  echo "[BOOTSTRAP] Alphafold model params already present."
fi

# Check core DB directories
DB_DIRS=(bfd uniref90 mgnify pdb70 pdb_mmcif)
MISSING_DB=0
for d in "${DB_DIRS[@]}"; do
  if [[ ! -d "${ALPHAFOLD_DB_PATH}/${d}" ]]; then
    MISSING_DB=1
  fi
done

if [[ "${MISSING_DB}" == "1" ]]; then
  if [[ -n "${DB_SYNC_CMD:-}" ]]; then
    echo "[BOOTSTRAP] Core databases missing; running DB_SYNC_CMD."
    echo "DB_SYNC_CMD=${DB_SYNC_CMD}"
    # shellcheck disable=SC2086
    bash -lc ${DB_SYNC_CMD}
  elif [[ "${ALLOW_DB_AUTO_DOWNLOAD:-0}" == "1" ]]; then
    PRESET="${DB_AUTO_PRESET:-reduced_dbs}"
    echo "[BOOTSTRAP] Core databases missing; auto-download enabled. Preset=${PRESET}"
    "/app/download_db.sh" "${ALPHAFOLD_DB_PATH}" "${PRESET}"
  else
    echo "[BOOTSTRAP] Core databases missing; no sync/download configured. Skipping."
  fi
fi

echo "--- [BOOTSTRAP] Sizes ---"
du -sh "${ALPHAFOLD_DB_PATH}" 2>/dev/null || true
du -sh "${ALPHAFOLD_MODELS_DIR}" 2>/dev/null || true
for d in "${DB_DIRS[@]}"; do
  du -sh "${ALPHAFOLD_DB_PATH}/${d}" 2>/dev/null || true
done

echo "[BOOTSTRAP] Completed."

