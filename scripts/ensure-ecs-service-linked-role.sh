#!/usr/bin/env bash
set -euo pipefail

ROLE_NAME="AWSServiceRoleForECS"

set +e
OUTPUT="$(
  aws iam get-role \
    --role-name "${ROLE_NAME}" \
    --output json 2>&1
)"
STATUS=$?
set -e

if [ ${STATUS} -eq 0 ]; then
  exit 0
fi

if ! grep -qi "NoSuchEntity" <<<"${OUTPUT}"; then
  printf 'ECS service-linked role ensure failed during get-role: %s\n' "${OUTPUT}" >&2
  exit "${STATUS}"
fi

set +e
CREATE_OUTPUT="$(
  aws iam create-service-linked-role \
    --aws-service-name ecs.amazonaws.com \
    --description "Service-linked role for ECS Express Mode bootstrap" 2>&1
)"
CREATE_STATUS=$?
set -e

if [ ${CREATE_STATUS} -ne 0 ]; then
  if grep -qi "InvalidInput" <<<"${CREATE_OUTPUT}" && grep -qi "has been taken in this account" <<<"${CREATE_OUTPUT}"; then
    exit 0
  fi
  printf 'ECS service-linked role ensure failed during create-service-linked-role: %s\n' "${CREATE_OUTPUT}" >&2
  exit "${CREATE_STATUS}"
fi
