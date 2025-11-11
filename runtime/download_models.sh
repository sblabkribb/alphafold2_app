#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: download_models.sh DEST_DIR

Download Alphafold model parameters into DEST_DIR/models.

Environment variables:
  MODEL_RELEASE_URL   Override download URL for model parameters archive.
EOF
}

if [[ $# -lt 1 ]]; then
    usage
    exit 1
fi

DEST_ROOT="$(realpath "$1")"
MODELS_DIR="${DEST_ROOT}/models"
MODEL_RELEASE_URL="${MODEL_RELEASE_URL:-https://storage.googleapis.com/alphafold/alphafold_params_2022-12-06.tar}"

mkdir -p "${MODELS_DIR}"

ARCHIVE_PATH="${MODELS_DIR}/$(basename "${MODEL_RELEASE_URL}")"

echo "[+] Downloading Alphafold model parameters"
echo "    URL: ${MODEL_RELEASE_URL}"
echo "    Target: ${ARCHIVE_PATH}"

aria2c --check-certificate=false -x 16 -s 16 -o "$(basename "${ARCHIVE_PATH}")" -d "${MODELS_DIR}" "${MODEL_RELEASE_URL}"

echo "[+] Extracting..."
tar -xf "${ARCHIVE_PATH}" -C "${MODELS_DIR}"

echo "[+] Done. Models available under ${MODELS_DIR}"
