#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: CONFIRM=REPLAY $0 <start-time> <end-time>" >&2
  echo "Example times: 2026-07-27T12:00:00Z 2026-07-27T12:15:00Z" >&2
  exit 2
fi

if [[ "${CONFIRM:-}" != "REPLAY" ]]; then
  echo "Set CONFIRM=REPLAY to acknowledge that archived events will be reprocessed." >&2
  exit 2
fi

START_TIME="$1"
END_TIME="$2"
PROJECT_NAME="${PROJECT_NAME:-shipment-event-platform}"
ENVIRONMENT="${ENVIRONMENT:-dev}"
AWS_REGION="${AWS_REGION:-eu-north-1}"
PLATFORM_STACK="${PLATFORM_STACK:-${PROJECT_NAME}-${ENVIRONMENT}}"
REPLAY_NAME="${REPLAY_NAME:-shipment-requests-$(date -u +%Y%m%d-%H%M%S)}"

get_stack_output() {
  local output_key="$1"
  local value
  value="$(aws cloudformation describe-stacks \
    --region "${AWS_REGION}" \
    --stack-name "${PLATFORM_STACK}" \
    --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue | [0]" \
    --output text)"
  if [[ -z "${value}" || "${value}" == "None" ]]; then
    echo "Stack output ${output_key} is missing. Deploy the archive update first." >&2
    exit 2
  fi
  printf '%s' "${value}"
}

ARCHIVE_ARN="$(get_stack_output ShipmentRequestArchiveArn)"
EVENT_BUS_ARN="$(get_stack_output EventBusArn)"
REQUESTED_RULE_ARN="$(get_stack_output ShipmentRequestedRuleArn)"

echo "Replaying archived ShipmentRequested events from ${START_TIME} through ${END_TIME}." >&2
echo "Matching events will be sent to SQS and may repair incomplete processing." >&2

aws events start-replay \
  --region "${AWS_REGION}" \
  --replay-name "${REPLAY_NAME}" \
  --description "Controlled shipment request replay for ${PLATFORM_STACK}" \
  --event-source-arn "${ARCHIVE_ARN}" \
  --event-start-time "${START_TIME}" \
  --event-end-time "${END_TIME}" \
  --destination "Arn=${EVENT_BUS_ARN},FilterArns=${REQUESTED_RULE_ARN}" \
  --query '{ReplayArn:ReplayArn,State:State,StateReason:StateReason}' \
  --output table

echo "Replay started: ${REPLAY_NAME}"
echo "Inspect it with:"
echo "aws events describe-replay --region ${AWS_REGION} --replay-name ${REPLAY_NAME} --output table"
