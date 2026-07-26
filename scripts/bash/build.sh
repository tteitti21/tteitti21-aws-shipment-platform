#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <unique-image-tag>" >&2
  exit 2
fi

IMAGE_TAG="$1"
PROJECT_NAME="${PROJECT_NAME:-shipment-event-platform}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

docker build \
  --file "${ROOT_DIR}/docker/api.Dockerfile" \
  --tag "${PROJECT_NAME}-api:${IMAGE_TAG}" \
  "${ROOT_DIR}"

docker build \
  --file "${ROOT_DIR}/docker/worker.Dockerfile" \
  --tag "${PROJECT_NAME}-worker:${IMAGE_TAG}" \
  "${ROOT_DIR}"

