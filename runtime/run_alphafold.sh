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

# Prefer explicit python from conda env if available; fallback to python3
PY_ENV_ROOT="${VENV_PATH:-/opt/conda/envs/alphafold}"
if [[ -f "${PY_ENV_ROOT}/bin/activate" ]]; then
    # shellcheck disable=SC1090
    source "${PY_ENV_ROOT}/bin/activate" || true
fi
PY_BIN="${PY_BIN:-${PY_ENV_ROOT}/bin/python}"
if [[ ! -x "${PY_BIN}" ]]; then
    PY_BIN="python3"
fi

MODEL_FLAG=()

resolve_path() {
    local kind="$1"
    shift
    local IFS=$'\n'
    shopt -s nullglob
    for pattern in "$@"; do
        for candidate in ${pattern}; do  # glob expansion with nullglob
            if [[ "${kind}" == "file" && -f "${candidate}" ]]; then
                realpath "${candidate}"
                shopt -u nullglob
                return 0
            elif [[ "${kind}" == "dir" && -d "${candidate}" ]]; then
                realpath "${candidate}"
                shopt -u nullglob
                return 0
            elif [[ "${kind}" == "any" && -e "${candidate}" ]]; then
                realpath "${candidate}"
                shopt -u nullglob
                return 0
            fi
        done
    done
    shopt -u nullglob
    return 1
}

require_path() {
    local flag="$1"
    local value="$2"
    if [[ -z "${value}" ]]; then
        echo "Required Alphafold asset missing for ${flag}. Verify databases under ${ALPHAFOLD_DB_PATH} or set the corresponding environment variable." >&2
        exit 5
    fi
}

if [[ -f "${ALPHAFOLD_DIR}/run_alphafold.py" ]]; then
    if grep -q "DEFINE_string('model_dir'" "${ALPHAFOLD_DIR}/run_alphafold.py"; then
        MODEL_FLAG=(--model_dir="${ALPHAFOLD_MODELS_DIR}")
    elif grep -q "DEFINE_string('models_dir'" "${ALPHAFOLD_DIR}/run_alphafold.py"; then
        MODEL_FLAG=(--models_dir="${ALPHAFOLD_MODELS_DIR}")
    fi
fi

CMD=(
    "${PY_BIN}"
    "${ALPHAFOLD_DIR}/run_alphafold.py"
    "--fasta_paths=${FASTA_PATH}"
    "--output_dir=${OUTPUT_DIR}"
    "--data_dir=${ALPHAFOLD_DB_PATH}"
    "--model_preset=${MODEL_PRESET}"
    "--db_preset=${DB_PRESET}"
    "--max_template_date=${MAX_TEMPLATE_DATE}"
)

if [[ ${#MODEL_FLAG[@]} -gt 0 ]]; then
    CMD+=("${MODEL_FLAG[@]}")
fi

UNIREF90_PATH="${UNIREF90_DATABASE_PATH:-$(resolve_path file "${ALPHAFOLD_DB_PATH}/uniref90/uniref90.fasta" "${ALPHAFOLD_DB_PATH}/uniref90/"*.fasta)}"
require_path "--uniref90_database_path" "${UNIREF90_PATH}"
CMD+=("--uniref90_database_path=${UNIREF90_PATH}")

MGNIFY_PATH="${MGNIFY_DATABASE_PATH:-$(resolve_path file "${ALPHAFOLD_DB_PATH}/mgnify/"*.fa "${ALPHAFOLD_DB_PATH}/mgnify/"*.fasta)}"
require_path "--mgnify_database_path" "${MGNIFY_PATH}"
CMD+=("--mgnify_database_path=${MGNIFY_PATH}")

TEMPLATE_MMCIF_DIR="${TEMPLATE_MMCIF_DIR:-$(resolve_path dir "${ALPHAFOLD_DB_PATH}/pdb_mmcif/mmcif_files")}"
require_path "--template_mmcif_dir" "${TEMPLATE_MMCIF_DIR}"
CMD+=("--template_mmcif_dir=${TEMPLATE_MMCIF_DIR}")

OBSOLETE_PDBS_PATH="${OBSOLETE_PDBS_PATH:-$(resolve_path file "${ALPHAFOLD_DB_PATH}/pdb_mmcif/obsolete.dat")}"
require_path "--obsolete_pdbs_path" "${OBSOLETE_PDBS_PATH}"
CMD+=("--obsolete_pdbs_path=${OBSOLETE_PDBS_PATH}")

PDB70_PATH="${PDB70_DATABASE_PATH:-$(resolve_path dir "${ALPHAFOLD_DB_PATH}/pdb70/pdb70")}"
if [[ -n "${PDB70_PATH}" ]]; then
    CMD+=("--pdb70_database_path=${PDB70_PATH}")
fi

UNIPROT_PATH="${UNIPROT_DATABASE_PATH:-$(resolve_path file "${ALPHAFOLD_DB_PATH}/uniprot/uniprot.fasta" "${ALPHAFOLD_DB_PATH}/uniprot/"*.fasta)}"
if [[ -n "${UNIPROT_PATH}" ]]; then
    CMD+=("--uniprot_database_path=${UNIPROT_PATH}")
fi

PDB_SEQRES_PATH="${PDB_SEQRES_DATABASE_PATH:-$(resolve_path file "${ALPHAFOLD_DB_PATH}/pdb_seqres/pdb_seqres.txt")}"
if [[ -n "${PDB_SEQRES_PATH}" ]]; then
    CMD+=("--pdb_seqres_database_path=${PDB_SEQRES_PATH}")
fi

BFD_PATH="${BFD_DATABASE_PATH:-$(resolve_path dir "${ALPHAFOLD_DB_PATH}/bfd/bfd_metaclust_clu_complete_id30_c90_final_seq.sorted_opt" "${ALPHAFOLD_DB_PATH}/bfd/"*)}"
if [[ -n "${BFD_PATH}" ]]; then
    CMD+=("--bfd_database_path=${BFD_PATH}")
fi

SMALL_BFD_PATH="${SMALL_BFD_DATABASE_PATH:-$(resolve_path file "${ALPHAFOLD_DB_PATH}/small_bfd/bfd-first_non_consensus_sequences.fasta" "${ALPHAFOLD_DB_PATH}/small_bfd/"*.fasta)}"
if [[ -n "${SMALL_BFD_PATH}" ]]; then
    CMD+=("--small_bfd_database_path=${SMALL_BFD_PATH}")
fi

UNIREF30_PATH="${UNIREF30_DATABASE_PATH:-$(resolve_path dir "${ALPHAFOLD_DB_PATH}/uniref30/UniRef30_20"* "${ALPHAFOLD_DB_PATH}/uniref30/"*)}"
if [[ -n "${UNIREF30_PATH}" ]]; then
    CMD+=("--uniref30_database_path=${UNIREF30_PATH}")
fi

USE_GPU_RELAX="${USE_GPU_RELAX:-true}"
if [[ "${USE_GPU_RELAX}" == "1" ]]; then
    USE_GPU_RELAX="true"
elif [[ "${USE_GPU_RELAX}" == "0" ]]; then
    USE_GPU_RELAX="false"
fi
if [[ -z "${USE_GPU_RELAX}" ]]; then
    echo "USE_GPU_RELAX must be set to true or false." >&2
    exit 5
fi
CMD+=("--use_gpu_relax=${USE_GPU_RELAX}")

if [[ -n "${ALPHAFOLD_EXTRA_FLAGS}" ]]; then
    # shellcheck disable=SC2206
    EXTRA_ARGS=(${ALPHAFOLD_EXTRA_FLAGS})
    CMD+=("${EXTRA_ARGS[@]}")
fi

"${CMD[@]}"
