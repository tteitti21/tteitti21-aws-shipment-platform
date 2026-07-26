#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="${PROJECT_NAME:-shipment-event-platform}"
AWS_REGION="${AWS_REGION:-eu-north-1}"
BOOTSTRAP_STACK="${BOOTSTRAP_STACK:-${PROJECT_NAME}-bootstrap}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

aws cloudformation deploy \
  --region "${AWS_REGION}" \
  --stack-name "${BOOTSTRAP_STACK}" \
  --template-file "${ROOT_DIR}/infra/bootstrap.yaml" \
  --parameter-overrides "ProjectName=${PROJECT_NAME}" \
  --no-fail-on-empty-changeset

aws cloudformation describe-stacks \
  --region "${AWS_REGION}" \
  --stack-name "${BOOTSTRAP_STACK}" \
  --query "Stacks[0].Outputs" \
  --output table

