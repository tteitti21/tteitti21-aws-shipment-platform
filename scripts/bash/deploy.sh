#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-shipment-event-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-eu-north-1}"
PLATFORM_STACK="${PLATFORM_STACK:-${PROJECT_NAME}-${ENVIRONMENT}}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEPLOYMENT_FILE="${ROOT_DIR}/.deployment.env"

if [[ ! -f "${DEPLOYMENT_FILE}" ]]; then
  echo "Missing ${DEPLOYMENT_FILE}; run push.sh first." >&2
  exit 2
fi

# shellcheck disable=SC1090
source "${DEPLOYMENT_FILE}"
DIGEST_PATTERN='@sha256:[a-f0-9]{64}$'
if [[ ! "${API_IMAGE_URI}" =~ ${DIGEST_PATTERN} ]] ||
  [[ ! "${WORKER_IMAGE_URI}" =~ ${DIGEST_PATTERN} ]]; then
  echo "Both image URIs must use immutable sha256 digests." >&2
  exit 2
fi

aws cloudformation deploy \
  --region "${AWS_REGION}" \
  --stack-name "${PLATFORM_STACK}" \
  --template-file "${ROOT_DIR}/infra/platform.yaml" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    "ProjectName=${PROJECT_NAME}" \
    "Environment=${ENVIRONMENT}" \
    "ApiImageUri=${API_IMAGE_URI}" \
    "WorkerImageUri=${WORKER_IMAGE_URI}" \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${PLATFORM_STACK}" \
  --query "Stacks[0].Outputs" \
  --output table

