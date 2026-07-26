#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 <unique-image-tag>" >&2
  exit 2
fi

IMAGE_TAG="$1"
PROJECT_NAME="${PROJECT_NAME:-shipment-event-platform}"
AWS_REGION="${AWS_REGION:-eu-north-1}"
BOOTSTRAP_STACK="${BOOTSTRAP_STACK:-${PROJECT_NAME}-bootstrap}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

API_REPOSITORY_URI="$(aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${BOOTSTRAP_STACK}" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiRepositoryUri'].OutputValue | [0]" \
  --output text)"
WORKER_REPOSITORY_URI="$(aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${BOOTSTRAP_STACK}" \
  --query "Stacks[0].Outputs[?OutputKey=='WorkerRepositoryUri'].OutputValue | [0]" \
  --output text)"

REGISTRY="${API_REPOSITORY_URI%%/*}"
aws ecr get-login-password --region "${AWS_REGION}" |
  docker login --username AWS --password-stdin "${REGISTRY}"

docker tag "${PROJECT_NAME}-api:${IMAGE_TAG}" "${API_REPOSITORY_URI}:${IMAGE_TAG}"
docker tag "${PROJECT_NAME}-worker:${IMAGE_TAG}" "${WORKER_REPOSITORY_URI}:${IMAGE_TAG}"
docker push "${API_REPOSITORY_URI}:${IMAGE_TAG}"
docker push "${WORKER_REPOSITORY_URI}:${IMAGE_TAG}"

API_DIGEST="$(aws ecr describe-images \
  --region "${AWS_REGION}" \
  --repository-name "${PROJECT_NAME}-api" \
  --image-ids "imageTag=${IMAGE_TAG}" \
  --query "imageDetails[0].imageDigest" \
  --output text)"
WORKER_DIGEST="$(aws ecr describe-images \
  --region "${AWS_REGION}" \
  --repository-name "${PROJECT_NAME}-worker" \
  --image-ids "imageTag=${IMAGE_TAG}" \
  --query "imageDetails[0].imageDigest" \
  --output text)"

DEPLOYMENT_FILE="${ROOT_DIR}/.deployment.env"
{
  echo "API_IMAGE_URI=${API_REPOSITORY_URI}@${API_DIGEST}"
  echo "WORKER_IMAGE_URI=${WORKER_REPOSITORY_URI}@${WORKER_DIGEST}"
} >"${DEPLOYMENT_FILE}"

echo "Wrote immutable image URIs to ${DEPLOYMENT_FILE} (gitignored)."

