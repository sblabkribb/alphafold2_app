#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: build_and_push.sh [OPTIONS]

Builds Docker.base and Docker images for the RunPod Alphafold2 project and pushes them to a registry.

Environment variables:
  REGISTRY             Container registry (e.g., ghcr.io/my-org). Required.
  IMAGE_NAME           Base image name (default: alphafold-serverless).
  IMAGE_TAG            Image tag (default: timestamp, YYYYMMDDHHMM).
  CUDA_IMAGE           Override base CUDA image for Docker.base (default in Dockerfile).
  PUSH                 Set to 0 to skip docker push (default: 1).
  DOCKER_CLI           Override container CLI (docker or nerdctl). Auto-detect by default.
  CORP_CA_PATH         Optional path to a corporate CA cert to inject at build (uses BuildKit secret).

Options:
  -h, --help           Show this help.
  --no-cache           Build images without using cache.

Examples:
  REGISTRY=ghcr.io/my-org IMAGE_TAG=dev ./scripts/build_and_push.sh
  REGISTRY=docker.io/user IMAGE_NAME=alphafold ./scripts/build_and_push.sh --no-cache
EOF
}

NO_CACHE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --no-cache)
            NO_CACHE="--no-cache"
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            exit 1
            ;;
    esac
done

: "${REGISTRY:?Environment variable REGISTRY is required (e.g., ghcr.io/my-org)}"
IMAGE_NAME="${IMAGE_NAME:-alphafold-serverless}"
IMAGE_TAG="${IMAGE_TAG:-$(date +%Y%m%d%H%M)}"
CUDA_IMAGE="${CUDA_IMAGE:-}"
PUSH="${PUSH:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

BASE_TAG="${REGISTRY}/${IMAGE_NAME}-base:${IMAGE_TAG}"
FINAL_TAG="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"

# Pick container CLI (docker or nerdctl)
pick_cli() {
    if [[ -n "${DOCKER_CLI:-}" ]]; then
        echo "${DOCKER_CLI}"
        return
    fi
    if command -v docker >/dev/null 2>&1; then
        if docker version >/dev/null 2>&1; then
            echo docker
            return
        fi
    fi
    if command -v nerdctl >/dev/null 2>&1; then
        echo nerdctl
        return
    fi
    echo ""  # not found
}

DOCKER_BIN="$(pick_cli)"
if [[ -z "${DOCKER_BIN}" ]]; then
    echo "[!] No working container CLI found. Install Docker or nerdctl (Rancher Desktop)." >&2
    exit 10
fi
echo "[i] Using container CLI: ${DOCKER_BIN}"

echo "[+] Building base image: ${BASE_TAG}"
BASE_ARGS=()
if [[ -n "${CUDA_IMAGE}" ]]; then
    BASE_ARGS+=("--build-arg" "CUDA_IMAGE=${CUDA_IMAGE}")
fi
if [[ -n "${CORP_CA_PATH:-}" ]]; then
    if [[ -f "${CORP_CA_PATH}" ]]; then
        BASE_ARGS+=("--secret" "id=corpca,src=${CORP_CA_PATH}")
        echo "[i] Injecting corporate CA via BuildKit secret: ${CORP_CA_PATH}"
    else
        echo "[!] CORP_CA_PATH set but file not found: ${CORP_CA_PATH}" >&2
    fi
fi

"${DOCKER_BIN}" build \
    "${BASE_ARGS[@]}" \
    ${NO_CACHE} \
    -f "${PROJECT_ROOT}/docker/Docker.base" \
    -t "${BASE_TAG}" \
    "${PROJECT_ROOT}"

echo "[+] Building runtime image: ${FINAL_TAG}"
"${DOCKER_BIN}" build \
    --build-arg "BASE_IMAGE=${BASE_TAG}" \
    ${NO_CACHE} \
    -f "${PROJECT_ROOT}/docker/Docker" \
    -t "${FINAL_TAG}" \
    "${PROJECT_ROOT}"

if [[ "${PUSH}" == "1" ]]; then
    echo "[+] Pushing ${BASE_TAG}"
    "${DOCKER_BIN}" push "${BASE_TAG}"

    echo "[+] Pushing ${FINAL_TAG}"
    "${DOCKER_BIN}" push "${FINAL_TAG}"
else
    echo "[!] PUSH=0, skipping docker push."
fi

echo "[✓] Done."
