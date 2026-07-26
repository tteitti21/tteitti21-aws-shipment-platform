#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-shipment-event-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-eu-north-1}"
PLATFORM_STACK="${PLATFORM_STACK:-${PROJECT_NAME}-${ENVIRONMENT}}"
BOOTSTRAP_STACK="${BOOTSTRAP_STACK:-${PROJECT_NAME}-bootstrap}"

if [[ "${CONFIRM:-}" != "DELETE" ]]; then
  echo "Cleanup deletes DynamoDB data, queues, logs, and ECR images." >&2
  echo "Run with CONFIRM=DELETE to continue." >&2
  exit 2
fi

aws cloudformation delete-stack \
  --region "${AWS_REGION}" \
  --stack-name "${PLATFORM_STACK}"
aws cloudformation wait stack-delete-complete \
  --region "${AWS_REGION}" \
  --stack-name "${PLATFORM_STACK}"

aws cloudformation delete-stack \
  --region "${AWS_REGION}" \
  --stack-name "${BOOTSTRAP_STACK}"
aws cloudformation wait stack-delete-complete \
  --region "${AWS_REGION}" \
  --stack-name "${BOOTSTRAP_STACK}"

echo "Deleted ${PLATFORM_STACK} and ${BOOTSTRAP_STACK}."

