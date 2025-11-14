#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: download_db.sh DEST_DIR [PRESET]

Download Alphafold databases into DEST_DIR according to PRESET.

Arguments:
  DEST_DIR   Destination root directory (e.g., /data/alphafold)
  PRESET     One of: reduced_dbs | full_dbs (default: reduced_dbs)

Notes:
  - full_dbs requires multiple terabytes and many hours; not recommended at serverless cold start.
  - This script delegates to /opt/alphafold/scripts/download_all_data.sh from the Alphafold repo.
EOF
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

DEST_ROOT="$(realpath "$1")"
PRESET="${2:-${DB_PRESET:-reduced_dbs}}"
ALPHAFOLD_DIR="${ALPHAFOLD_DIR:-/opt/alphafold}"

mkdir -p "${DEST_ROOT}"

if [[ ! -x "${ALPHAFOLD_DIR}/scripts/download_all_data.sh" ]]; then
  echo "[download_db] Alphafold download script not found: ${ALPHAFOLD_DIR}/scripts/download_all_data.sh" >&2
  exit 2
fi

echo "[download_db] Destination: ${DEST_ROOT}"
echo "[download_db] Preset: ${PRESET}"

if [[ "${PRESET}" == "full_dbs" ]]; then
  echo "[download_db] WARNING: full_dbs will download multi-TB and can take many hours." >&2
fi

"${ALPHAFOLD_DIR}/scripts/download_all_data.sh" "${DEST_ROOT}" "${PRESET}"

echo "[download_db] Completed: $(du -sh "${DEST_ROOT}" 2>/dev/null | awk '{print $1}')"

