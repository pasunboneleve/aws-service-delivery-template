#!/usr/bin/env bash
set -euo pipefail

eval "$(jq -r '@sh "REPOSITORY_NAME=\(.repository_name) IMAGE_TAG=\(.image_tag) AWS_REGION=\(.aws_region)"')"

if output="$(aws ecr describe-images --repository-name "${REPOSITORY_NAME}" --image-ids imageTag="${IMAGE_TAG}" --region "${AWS_REGION}" 2>&1)"; then
  jq -n '{"exists":"true"}'
elif printf '%s' "${output}" | grep -Eq 'ImageNotFoundException|RepositoryNotFoundException'; then
  jq -n '{"exists":"false"}'
else
  echo "ECR check failed: ${output}" >&2
  exit 1
fi
