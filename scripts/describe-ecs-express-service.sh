#!/usr/bin/env bash
set -euo pipefail

INPUT="$(cat)"
SERVICE_ARN="$(jq -r '.service_arn // empty' <<<"${INPUT}")"
AWS_REGION="$(jq -r '.aws_region // empty' <<<"${INPUT}")"

if [ -z "${SERVICE_ARN}" ] || [ -z "${AWS_REGION}" ]; then
  echo "service_arn and aws_region are required" >&2
  exit 1
fi

set +e
OUTPUT="$(
  aws ecs describe-express-gateway-service \
    --service-arn "${SERVICE_ARN}" \
    --region "${AWS_REGION}" \
    --output json 2>&1
)"
STATUS=$?
set -e

if [ ${STATUS} -ne 0 ]; then
  if grep -qi "ResourceNotFoundException" <<<"${OUTPUT}"; then
    jq -n --arg endpoint "" '{"endpoint":$endpoint}'
    exit 0
  fi
  printf '%s\n' "${OUTPUT}" >&2
  exit "${STATUS}"
fi

ENDPOINT="$(
  jq -r '
    (.service.activeConfigurations[0].ingressPaths // [])
    | map(select(.accessType == "PUBLIC"))
    | .[0]?.endpoint // ""
  ' <<<"${OUTPUT}"
)"

jq -n --arg endpoint "${ENDPOINT}" '{"endpoint":$endpoint}'
