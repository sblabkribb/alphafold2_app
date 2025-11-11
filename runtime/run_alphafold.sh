#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: run_alphafold.sh FASTA_PATH OUTPUT_DIR

Runs Alphafold inference using the configured database and models.

Environment variables:
  ALPHAFOLD_DIR           Path to the cloned Alphafold repository (default: /opt/alphafold).
  ALPHAFOLD_DB_PATH       Root directory for Alphafold databases (required).
  ALPHAFOLD_MODELS_DIR    Directory containing model weights (default: ${ALPHAFOLD_DB_PATH}/models).
  MODEL_PRESET            Alphafold model preset (default: monomer).
  DB_PRESET               Database preset to use (default: full_dbs).
  MAX_TEMPLATE_DATE       Max template release date (default: 2020-05-14).
  ALPHAFOLD_EXTRA_FLAGS   Extra flags appended to run_alphafold.py invocation.
EOF
}

if [[ $# -lt 2 ]]; then
    usage
    exit 1
fi

FASTA_PATH="$(realpath "$1")"
OUTPUT_DIR="$(realpath "$2")"

ALPHAFOLD_DIR="${ALPHAFOLD_DIR:-/opt/alphafold}"
ALPHAFOLD_DB_PATH="${ALPHAFOLD_DB_PATH:-}"
ALPHAFOLD_MODELS_DIR="${ALPHAFOLD_MODELS_DIR:-${ALPHAFOLD_DB_PATH}/models}"
MODEL_PRESET="${MODEL_PRESET:-monomer}"
DB_PRESET="${DB_PRESET:-full_dbs}"
MAX_TEMPLATE_DATE="${MAX_TEMPLATE_DATE:-2020-05-14}"
ALPHAFOLD_EXTRA_FLAGS="${ALPHAFOLD_EXTRA_FLAGS:-}"

if [[ ! -f "${FASTA_PATH}" ]]; then
    echo "FASTA file not found: ${FASTA_PATH}" >&2
    exit 2
fi

if [[ -z "${ALPHAFOLD_DB_PATH}" ]]; then
    echo "Environment variable ALPHAFOLD_DB_PATH must be set." >&2
    exit 3
fi

if [[ ! -d "${ALPHAFOLD_DIR}" ]]; then
    echo "Alphafold directory not found: ${ALPHAFOLD_DIR}" >&2
    exit 4
fi

mkdir -p "${OUTPUT_DIR}"

echo "[+] Running Alphafold:"
echo "    FASTA_PATH=${FASTA_PATH}"
echo "    OUTPUT_DIR=${OUTPUT_DIR}"
echo "    MODEL_PRESET=${MODEL_PRESET}"
echo "    DB_PRESET=${DB_PRESET}"
echo "    MAX_TEMPLATE_DATE=${MAX_TEMPLATE_DATE}"

source "${VENV_PATH:-/opt/alphafold/venv}/bin/activate"

python "${ALPHAFOLD_DIR}/run_alphafold.py" \
    --fasta_paths="${FASTA_PATH}" \
    --output_dir="${OUTPUT_DIR}" \
    --data_dir="${ALPHAFOLD_DB_PATH}" \
    --model_preset="${MODEL_PRESET}" \
    --db_preset="${DB_PRESET}" \
    --max_template_date="${MAX_TEMPLATE_DATE}" \
    --model_dir="${ALPHAFOLD_MODELS_DIR}" \
    ${ALPHAFOLD_EXTRA_FLAGS}
