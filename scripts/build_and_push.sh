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

echo "[+] Building base image: ${BASE_TAG}"
BASE_ARGS=()
if [[ -n "${CUDA_IMAGE}" ]]; then
    BASE_ARGS+=("--build-arg" "CUDA_IMAGE=${CUDA_IMAGE}")
fi

docker build \
    "${BASE_ARGS[@]}" \
    ${NO_CACHE} \
    -f "${PROJECT_ROOT}/docker/Docker.base" \
    -t "${BASE_TAG}" \
    "${PROJECT_ROOT}"

echo "[+] Building runtime image: ${FINAL_TAG}"
docker build \
    --build-arg "BASE_IMAGE=${BASE_TAG}" \
    ${NO_CACHE} \
    -f "${PROJECT_ROOT}/docker/Docker" \
    -t "${FINAL_TAG}" \
    "${PROJECT_ROOT}"

if [[ "${PUSH}" == "1" ]]; then
    echo "[+] Pushing ${BASE_TAG}"
    docker push "${BASE_TAG}"

    echo "[+] Pushing ${FINAL_TAG}"
    docker push "${FINAL_TAG}"
else
    echo "[!] PUSH=0, skipping docker push."
fi

echo "[✓] Done."
