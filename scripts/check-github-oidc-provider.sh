#!/usr/bin/env bash
set -euo pipefail

INPUT_JSON="$(cat)"
EXIT_PROBE_ERROR=2
EXIT_PROBE_OPTIONAL_FALLBACK=3

TARGET_URL="$(printf '%s' "${INPUT_JSON}" | jq -re '.url')" || {
  echo "OIDC provider check failed: could not parse input JSON" >&2
  exit "${EXIT_PROBE_ERROR}"
}
TARGET_AUDIENCE="$(printf '%s' "${INPUT_JSON}" | jq -re '.audience')" || {
  echo "OIDC provider check failed: could not parse audience from input JSON" >&2
  exit "${EXIT_PROBE_ERROR}"
}
TARGET_URL_HOST="${TARGET_URL#https://}"
TARGET_URL_HOST="${TARGET_URL_HOST#http://}"
TARGET_URL_HOST="${TARGET_URL_HOST%/}"

stderr_file="$(mktemp)"
trap 'rm -f "${stderr_file}"' EXIT

provider_arns_json="$(aws iam list-open-id-connect-providers --output json 2>"${stderr_file}")" || {
  stderr_output="$(cat "${stderr_file}")"
  echo "OIDC provider check failed: unable to determine whether the GitHub OIDC provider already exists. This probe requires iam:ListOpenIDConnectProviders and iam:GetOpenIDConnectProvider. AWS CLI error: ${stderr_output}" >&2
  exit "${EXIT_PROBE_OPTIONAL_FALLBACK}"
}

provider_arns="$(printf '%s' "${provider_arns_json}" | jq -r '.OpenIDConnectProviderList[].Arn')"

if [ -s "${stderr_file}" ]; then
  cat "${stderr_file}" >&2
  : > "${stderr_file}"
fi

while IFS= read -r provider_arn; do
  [ -z "${provider_arn}" ] && continue

  provider_output="$(aws iam get-open-id-connect-provider --open-id-connect-provider-arn "${provider_arn}" --output json 2>"${stderr_file}")" || {
    if printf '%s' "${provider_arn}" | grep -Fq "${TARGET_URL_HOST}"; then
      echo "OIDC provider check failed: unable to read the existing target provider ${provider_arn}. This probe requires iam:GetOpenIDConnectProvider. AWS CLI error: $(cat "${stderr_file}")" >&2
      exit "${EXIT_PROBE_ERROR}"
    fi
    echo "OIDC provider check warning for ${provider_arn}: $(cat "${stderr_file}")" >&2
    : > "${stderr_file}"
    continue
  }

  if [ -s "${stderr_file}" ]; then
    cat "${stderr_file}" >&2
    : > "${stderr_file}"
  fi

  provider_url="$(printf '%s' "${provider_output}" | jq -re '.Url')" || {
    echo "OIDC provider check warning for ${provider_arn}: provider response did not contain Url" >&2
    continue
  }
  provider_url="${provider_url#https://}"
  provider_url="${provider_url#http://}"
  provider_url="${provider_url%/}"

  if [ "${provider_url}" = "${TARGET_URL_HOST}" ]; then
    if ! printf '%s' "${provider_output}" | jq -e --arg audience "${TARGET_AUDIENCE}" '.ClientIDList | index($audience)' >/dev/null; then
      echo "OIDC provider check failed: existing provider ${provider_arn} matches ${TARGET_URL_HOST} but does not include required audience ${TARGET_AUDIENCE}" >&2
      exit "${EXIT_PROBE_ERROR}"
    fi
    jq -n --arg arn "${provider_arn}" '{"exists":"true","arn":$arn}'
    exit 0
  fi
done <<EOF
${provider_arns}
EOF

jq -n '{"exists":"false","arn":""}'
