#!/usr/bin/env bash
set -euo pipefail

eval "$(jq -r '@sh "REPOSITORY_URL=\(.repository_url) IMAGE_TAG=\(.image_tag) AWS_REGION=\(.aws_region)"')"
REPOSITORY_NAME="${REPOSITORY_URL#*.amazonaws.com/}"

if aws ecr describe-images --repository-name "${REPOSITORY_NAME}" --image-ids imageTag="${IMAGE_TAG}" --region "${AWS_REGION}" >/dev/null 2>&1; then
  jq -n '{"exists":"true"}'
else
  jq -n '{"exists":"false"}'
fi
