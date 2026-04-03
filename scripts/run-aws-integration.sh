#!/usr/bin/env bash
set -euo pipefail

# This runner keeps all integration state isolated to a generated workdir and
# one run id. Failure handling is trap-based so cleanup can still attempt a
# destroy with the same backend config and tfvars even when a step exits early.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA_DIR="${ROOT_DIR}/infra"
REPO_NAME="$(basename "${ROOT_DIR}")"
RUN_ID_DEFAULT="$(date +%Y%m%d%H%M%S)-$$"
RUN_ID_RAW="${AWS_INTEGRATION_RUN_ID:-${RUN_ID_DEFAULT}}"
RUN_ID="$(printf '%s' "${RUN_ID_RAW}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9-' '-')"
RUN_ID_EXPLICIT=0
WORKDIR="${AWS_INTEGRATION_WORKDIR:-}"
KEEP_WORKDIR="${AWS_INTEGRATION_KEEP_WORKDIR:-0}"
MODE="${1:-plan}"
WORKDIR_CREATED=0
INTEGRATION_PREFIX=""
SERVICE_NAME=""
ECR_REPOSITORY_NAME=""
IMAGE_TAG=""
STATE_KEY=""
TFVARS_PATH=""
BACKEND_CONFIG_PATH=""
METADATA_PATH=""
FIXTURE_DIR="${ROOT_DIR}/integration-fixture"
REMOTE_IMAGE_URI=""
VERIFY_RESPONSE_PATH=""
CURRENT_STEP="startup"
PRIMARY_FAILURE_STEP=""
ORIGINAL_EXIT_CODE=0
CLEANUP_REQUIRED=0
CLEANUP_ATTEMPTED=0
CLEANUP_EXIT_CODE=0
CLEANUP_TIMEOUT_SECONDS="${AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS:-300}"
SIMULATED_FAILURE_STEPS="${AWS_INTEGRATION_SIMULATE_FAILURE_AT:-}"
TOFU_DESTROY_LOG_PATH=""
EXIT_CLEANUP_SKIPPED=98
CLEANUP_STATUS_PATH=""

usage() {
  cat <<'EOF'
Usage:
  ./scripts/run-aws-integration.sh [plan|preflight|run|foundation-apply|bootstrap-publish|second-apply|verify|destroy]

This is the Phase 2 AWS integration runner skeleton.
Current behavior:
  - creates an isolated temp workdir
  - optionally runs a local readiness preflight with no AWS calls
  - derives unique naming and state paths for an integration run
  - materializes isolated backend, tfvars, and metadata files
  - prints the intended command sequence and current TODO boundaries
  - optionally runs the end-to-end integration sequence with failure cleanup
  - optionally performs the first foundation apply
  - optionally builds and pushes the bootstrap fixture image
  - optionally performs the second apply and fetches the service URL
  - optionally verifies the public fixture response
  - optionally destroys isolated integration resources explicitly

Environment overrides:
  AWS_INTEGRATION_RUN_ID       Override the generated run id
  AWS_INTEGRATION_WORKDIR      Reuse a specific working directory
  AWS_INTEGRATION_KEEP_WORKDIR Keep the workdir after exit when set to 1
  AWS_INTEGRATION_AUTO_APPROVE Set to 0 to omit -auto-approve on apply
  AWS_INTEGRATION_AWS_ACCOUNT_ID
                               Override the AWS account id instead of querying STS
  AWS_INTEGRATION_CLEANUP_TIMEOUT_SECONDS
                               Timeout in seconds for cleanup destroy (default: 300)
  AWS_INTEGRATION_SIMULATE_FAILURE_AT
                               Comma-separated step ids to fail locally:
                               config-materialization,first-tofu-apply,
                               bootstrap-image-publish,second-tofu-apply,
                               url-fetch,verification,destroy
  AWS_INTEGRATION_VERIFY_PATH  HTTP path to verify (default: /)
EOF
}

log_step() {
  CURRENT_STEP="$1"
  echo >&2
  echo "==> [${CURRENT_STEP}] $2" >&2
}

note() {
  echo "-- ${CURRENT_STEP}: $1" >&2
}

fail_if_simulated() {
  local step_id="$1"

  if [ -z "${SIMULATED_FAILURE_STEPS}" ]; then
    return
  fi

  if printf ',%s,' "${SIMULATED_FAILURE_STEPS}" | grep -Fq ",${step_id},"; then
    echo "Simulated failure at step ${step_id}" >&2
    exit 97
  fi
}

cleanup_workdir() {
  local exit_code="${1:-0}"

  if [ "${WORKDIR_CREATED}" != "1" ]; then
    if [ -n "${WORKDIR}" ]; then
      echo "Leaving user-supplied integration workdir in place: ${WORKDIR}"
    fi
    return
  fi

  if [ "${exit_code}" -ne 0 ]; then
    echo "Preserving generated integration workdir after failure: ${WORKDIR}"
    return
  fi

  if [ "${KEEP_WORKDIR}" = "1" ]; then
    echo "Keeping integration workdir: ${WORKDIR}"
    return
  fi

  if [ -n "${WORKDIR}" ] && [ -d "${WORKDIR}" ]; then
    rm -rf "${WORKDIR}"
  fi
}

run_with_timeout() {
  local timeout_seconds="$1"
  local logfile="$2"
  shift 2

  if command -v timeout >/dev/null 2>&1; then
    timeout --foreground "${timeout_seconds}" "$@" >>"${logfile}" 2>&1
    return
  fi

  require_command python3
  python3 - "${timeout_seconds}" "${logfile}" "$@" <<'PY'
import subprocess
import sys
from pathlib import Path

timeout_seconds = int(sys.argv[1])
log_path = Path(sys.argv[2])
cmd = sys.argv[3:]

with log_path.open("a", encoding="utf-8") as log_file:
    try:
        completed = subprocess.run(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print(f"Timed out after {timeout_seconds}s: {' '.join(cmd)}", file=log_file)
        sys.exit(124)

sys.exit(completed.returncode)
PY
}

attempt_cleanup_destroy() {
  local cleanup_script=""
  local auto_approve="${AWS_INTEGRATION_AUTO_APPROVE:-1}"

  if [ "${CLEANUP_REQUIRED}" != "1" ]; then
    return 0
  fi

  CLEANUP_ATTEMPTED=1
  log_step "destroy" "Attempting failure cleanup with isolated destroy"

  if [ -z "${AWS_REGION:-}" ] || printf '%s' "${AWS_REGION:-}" | grep -q '^__SET_'; then
    echo "Cleanup destroy skipped: AWS_REGION is not materialized." >&2
    return "${EXIT_CLEANUP_SKIPPED}"
  fi
  if [ -z "${TF_STATE_BUCKET:-}" ] || printf '%s' "${TF_STATE_BUCKET:-}" | grep -q '^__SET_'; then
    echo "Cleanup destroy skipped: TF_STATE_BUCKET is not materialized." >&2
    return "${EXIT_CLEANUP_SKIPPED}"
  fi
  if [ -z "${GITHUB_OWNER:-}" ] || printf '%s' "${GITHUB_OWNER:-}" | grep -q '^__SET_'; then
    echo "Cleanup destroy skipped: GITHUB_OWNER is not materialized." >&2
    return "${EXIT_CLEANUP_SKIPPED}"
  fi

  if printf ',%s,' "${SIMULATED_FAILURE_STEPS}" | grep -Fq ',destroy,'; then
    echo "Simulated failure at step destroy" >&2
    return 97
  fi

  TOFU_DESTROY_LOG_PATH="${WORKDIR}/cleanup-destroy.log"
  cleanup_script="${WORKDIR}/cleanup-destroy.sh"

  cat > "${cleanup_script}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${INFRA_DIR}"
tofu init -backend-config="${BACKEND_CONFIG_PATH}"
EOF

  if [ "${auto_approve}" = "0" ]; then
    cat >> "${cleanup_script}" <<EOF
tofu destroy -var-file="${TFVARS_PATH}"
EOF
  else
    cat >> "${cleanup_script}" <<EOF
tofu destroy -auto-approve -var-file="${TFVARS_PATH}"
EOF
  fi

  chmod +x "${cleanup_script}"

  {
    echo "Cleanup run id: ${RUN_ID}"
    echo "Cleanup timeout seconds: ${CLEANUP_TIMEOUT_SECONDS}"
    echo "Backend config: ${BACKEND_CONFIG_PATH}"
    echo "Vars file: ${TFVARS_PATH}"
  } > "${TOFU_DESTROY_LOG_PATH}"

  run_with_timeout "${CLEANUP_TIMEOUT_SECONDS}" "${TOFU_DESTROY_LOG_PATH}" bash "${cleanup_script}"
}

write_cleanup_summary() {
  local cleanup_status="$1"

  if [ -z "${WORKDIR}" ] || [ ! -d "${WORKDIR}" ]; then
    return
  fi

  CLEANUP_STATUS_PATH="${WORKDIR}/cleanup-status.json"

  jq -n \
    --arg run_id "${RUN_ID}" \
    --arg workdir "${WORKDIR}" \
    --arg primary_failure_step "${PRIMARY_FAILURE_STEP}" \
    --arg state_key "${STATE_KEY}" \
    --arg tfvars_path "${TFVARS_PATH}" \
    --arg backend_config_path "${BACKEND_CONFIG_PATH}" \
    --arg destroy_log_path "${TOFU_DESTROY_LOG_PATH}" \
    --arg cleanup_status "${cleanup_status}" \
    --argjson exit_code "${ORIGINAL_EXIT_CODE}" \
    --argjson cleanup_exit_code "${CLEANUP_EXIT_CODE}" \
    --argjson cleanup_attempted "$([ "${CLEANUP_ATTEMPTED}" = "1" ] && printf 'true' || printf 'false')" \
    '{
      run_id: $run_id,
      workdir: $workdir,
      primary_step: $primary_failure_step,
      primary_exit_code: $exit_code,
      cleanup_attempted: $cleanup_attempted,
      cleanup_status: $cleanup_status,
      cleanup_exit_code: $cleanup_exit_code,
      state_key: $state_key,
      tfvars_path: $tfvars_path,
      backend_config_path: $backend_config_path,
      destroy_log_path: $destroy_log_path
    }' > "${CLEANUP_STATUS_PATH}"
}

finalize_run() {
  local exit_code="$1"
  local cleanup_status="not-needed"

  trap - EXIT
  set +e

  if [ "${exit_code}" -ne 0 ]; then
    PRIMARY_FAILURE_STEP="${CURRENT_STEP}"
    echo "Integration runner failed during step '${CURRENT_STEP}' with exit code ${exit_code}." >&2
    ORIGINAL_EXIT_CODE="${exit_code}"

    if [ "${CLEANUP_REQUIRED}" = "1" ]; then
      attempt_cleanup_destroy
      CLEANUP_EXIT_CODE=$?
      if [ "${CLEANUP_EXIT_CODE}" -eq 0 ]; then
        cleanup_status="succeeded"
        echo "Cleanup succeeded during step 'destroy'." >&2
      elif [ "${CLEANUP_EXIT_CODE}" -eq "${EXIT_CLEANUP_SKIPPED}" ]; then
        cleanup_status="skipped"
        echo "Cleanup skipped during step 'destroy' because required integration inputs were not materialized." >&2
      else
        cleanup_status="failed"
        echo "Cleanup also failed during step 'destroy' with exit code ${CLEANUP_EXIT_CODE}." >&2
        if [ -n "${TOFU_DESTROY_LOG_PATH}" ] && [ -f "${TOFU_DESTROY_LOG_PATH}" ]; then
          echo "Cleanup logs saved to ${TOFU_DESTROY_LOG_PATH}" >&2
        fi
      fi
    fi

    write_cleanup_summary "${cleanup_status}"
    if [ -n "${CLEANUP_STATUS_PATH}" ] && [ -f "${CLEANUP_STATUS_PATH}" ]; then
      echo "Cleanup summary saved to ${CLEANUP_STATUS_PATH}" >&2
    fi
  fi

  cleanup_workdir "${exit_code}"

  exit "${exit_code}"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

validate_optional_env() {
  local env_name="$1"
  local env_value="$2"
  local env_pattern="$3"

  if [ -n "${env_value}" ] && ! printf '%s' "${env_value}" | grep -Eq "${env_pattern}"; then
    echo "Invalid value for ${env_name}: ${env_value}" >&2
    exit 1
  fi
}

require_materialized_value() {
  local field_name="$1"
  local field_value="$2"

  if [ -z "${field_value}" ] || printf '%s' "${field_value}" | grep -q '^__SET_'; then
    echo "Missing required integration input: ${field_name}" >&2
    exit 1
  fi
}

check_tool() {
  local tool_name="$1"

  if command -v "${tool_name}" >/dev/null 2>&1; then
    echo "ready: tool '${tool_name}' is installed"
    return 0
  fi

  echo "missing: tool '${tool_name}' is not installed"
  return 1
}

check_env_value() {
  local env_name="$1"
  local env_value="${!env_name:-}"

  if [ -n "${env_value}" ]; then
    echo "ready: ${env_name} is set"
    return 0
  fi

  echo "missing: ${env_name} is not set"
  return 1
}

check_aws_credentials_source() {
  if [ -n "${AWS_PROFILE:-}" ]; then
    echo "ready: AWS credentials source is configured via AWS_PROFILE"
    return 0
  fi

  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    echo "ready: AWS credentials source is configured via AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY"
    return 0
  fi

  echo "missing: AWS credentials source is not configured (set AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)"
  return 1
}

check_github_auth_source() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    echo "ready: GitHub provider auth is configured via GITHUB_TOKEN"
    return 0
  fi

  if [ ! -f "${INFRA_DIR}/prod.tfvars" ]; then
    echo "note: GitHub provider auth via infra/prod.tfvars cannot be checked until ${INFRA_DIR}/prod.tfvars exists"
    return 2
  fi

  if grep -Eq '^[[:space:]]*github_token[[:space:]]*=' "${INFRA_DIR}/prod.tfvars"; then
    echo "ready: GitHub provider auth is configured via infra/prod.tfvars"
    return 0
  fi

  echo "missing: GitHub provider auth is not configured (set GITHUB_TOKEN or github_token in infra/prod.tfvars)"
  return 1
}

run_preflight() {
  local failures=0
  local github_auth_status=0

  log_step "preflight" "Checking local readiness for the first real AWS integration run"

  for tool_name in tofu aws docker jq git python3; do
    if ! check_tool "${tool_name}"; then
      failures=$((failures + 1))
    fi
  done

  if ! check_env_value "AWS_REGION"; then
    failures=$((failures + 1))
  fi
  if ! check_env_value "TF_STATE_BUCKET"; then
    failures=$((failures + 1))
  fi
  if ! check_env_value "GITHUB_OWNER"; then
    failures=$((failures + 1))
  fi

  if ! check_aws_credentials_source; then
    failures=$((failures + 1))
  fi
  if [ -f "${INFRA_DIR}/prod.tfvars" ]; then
    echo "ready: ${INFRA_DIR}/prod.tfvars exists"
  else
    echo "missing: ${INFRA_DIR}/prod.tfvars does not exist"
    failures=$((failures + 1))
  fi

  if check_github_auth_source; then
    :
  else
    github_auth_status=$?
    if [ "${github_auth_status}" -eq 1 ]; then
      failures=$((failures + 1))
    fi
  fi

  if [ "${github_auth_status}" -eq 2 ]; then
    echo "note: GitHub provider auth will be satisfied by setting GITHUB_TOKEN or adding github_token to ${INFRA_DIR}/prod.tfvars"
  fi

  if [ "${failures}" -ne 0 ]; then
    echo "Preflight failed with ${failures} missing item(s)." >&2
    return 1
  fi

  echo "Preflight passed: environment is ready for a real AWS integration run."
}

slugify() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9-' '-'
}

trim_name() {
  printf '%.40s' "$1"
}

prepare_workdir() {
  log_step "config-materialization" "Preparing isolated integration workspace"

  if [ -n "${WORKDIR}" ]; then
    mkdir -p "${WORKDIR}"
  else
    WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/${REPO_NAME}-${RUN_ID}-XXXXXX")"
    WORKDIR_CREATED=1
  fi
}

materialize_tfvars() {
  local aws_region_placeholder
  local github_owner_placeholder
  local tf_state_bucket_placeholder

  fail_if_simulated "config-materialization"

  INTEGRATION_PREFIX="$(slugify "${REPO_NAME}-${RUN_ID}")"
  SERVICE_NAME="$(trim_name "${INTEGRATION_PREFIX}")"
  ECR_REPOSITORY_NAME="${SERVICE_NAME}"
  IMAGE_TAG="integration-${RUN_ID}"
  STATE_KEY="${REPO_NAME}/integration/${RUN_ID}.tfstate"
  TFVARS_PATH="${WORKDIR}/integration.tfvars"
  BACKEND_CONFIG_PATH="${WORKDIR}/backend.hcl"
  METADATA_PATH="${WORKDIR}/integration-metadata.json"
  validate_optional_env "AWS_REGION" "${AWS_REGION:-}" '^[a-z]{2}-[a-z0-9-]+-[0-9]+$'
  validate_optional_env "GITHUB_OWNER" "${GITHUB_OWNER:-}" '^[A-Za-z0-9_.-]+$'
  validate_optional_env "TF_STATE_BUCKET" "${TF_STATE_BUCKET:-}" '^[A-Za-z0-9.-]+$'
  aws_region_placeholder="${AWS_REGION:-__SET_AWS_REGION__}"
  github_owner_placeholder="${GITHUB_OWNER:-__SET_GITHUB_OWNER__}"
  tf_state_bucket_placeholder="${TF_STATE_BUCKET:-__SET_TF_STATE_BUCKET__}"

  cat > "${TFVARS_PATH}" <<EOF
# Generated by scripts/run-aws-integration.sh
# This file is intentionally isolated from infra/prod.tfvars.

aws_region          = "${aws_region_placeholder}"
service_name        = "${SERVICE_NAME}"
apprunner_image_tag = "${IMAGE_TAG}"
github_owner        = "${github_owner_placeholder}"
github_repo         = "${REPO_NAME}"
github_branch       = "main"
EOF

  cat > "${BACKEND_CONFIG_PATH}" <<EOF
# Generated by scripts/run-aws-integration.sh
bucket       = "${tf_state_bucket_placeholder}"
key          = "${STATE_KEY}"
region       = "${aws_region_placeholder}"
use_lockfile = true
EOF

  jq -n \
    --arg run_id "${RUN_ID}" \
    --arg integration_prefix "${INTEGRATION_PREFIX}" \
    --arg service_name "${SERVICE_NAME}" \
    --arg ecr_repository_name "${ECR_REPOSITORY_NAME}" \
    --arg image_tag "${IMAGE_TAG}" \
    --arg state_key "${STATE_KEY}" \
    --arg tfvars_path "${TFVARS_PATH}" \
    --arg backend_config_path "${BACKEND_CONFIG_PATH}" \
    '{
      run_id: $run_id,
      integration_prefix: $integration_prefix,
      service_name: $service_name,
      ecr_repository_name: $ecr_repository_name,
      image_tag: $image_tag,
      state_key: $state_key,
      tfvars_path: $tfvars_path,
      backend_config_path: $backend_config_path
    }' > "${METADATA_PATH}"

  cat <<EOF
Run ID: ${RUN_ID}
Workdir: ${WORKDIR}
Integration prefix: ${INTEGRATION_PREFIX}
Integration tfvars: ${TFVARS_PATH}
Integration backend config: ${BACKEND_CONFIG_PATH}
Integration metadata: ${METADATA_PATH}
Suggested backend key: ${STATE_KEY}
Derived service name: ${SERVICE_NAME}
Derived ECR repository name: ${ECR_REPOSITORY_NAME}
Expected bootstrap image tag: ${IMAGE_TAG}
EOF
}

run_destroy() {
  local auto_approve="${AWS_INTEGRATION_AUTO_APPROVE:-1}"
  local destroy_reason="$1"
  local destroy_script=""

  require_materialized_value "AWS_REGION" "${AWS_REGION:-}"
  require_materialized_value "TF_STATE_BUCKET" "${TF_STATE_BUCKET:-}"
  require_materialized_value "GITHUB_OWNER" "${GITHUB_OWNER:-}"

  log_step "destroy" "Running isolated destroy (${destroy_reason})"
  note "Run id: ${RUN_ID}"
  note "Infra dir: ${INFRA_DIR}"
  note "Backend config: ${BACKEND_CONFIG_PATH}"
  note "Vars file: ${TFVARS_PATH}"

  if [ "${destroy_reason}" = "manual" ] && [ "${RUN_ID_EXPLICIT}" != "1" ]; then
    echo "Manual destroy requires an explicit AWS_INTEGRATION_RUN_ID so the runner does not guess which isolated stack to tear down." >&2
    exit 1
  fi

  if printf ',%s,' "${SIMULATED_FAILURE_STEPS}" | grep -Fq ',destroy,'; then
    echo "Simulated failure at step destroy" >&2
    exit 97
  fi

  TOFU_DESTROY_LOG_PATH="${WORKDIR}/destroy.log"
  destroy_script="${WORKDIR}/destroy.sh"
  {
    echo "Destroy run id: ${RUN_ID}"
    echo "Destroy reason: ${destroy_reason}"
    echo "Destroy timeout seconds: ${CLEANUP_TIMEOUT_SECONDS}"
    echo "Backend config: ${BACKEND_CONFIG_PATH}"
    echo "Vars file: ${TFVARS_PATH}"
  } > "${TOFU_DESTROY_LOG_PATH}"

  cat > "${destroy_script}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
cd "${INFRA_DIR}"
tofu init -backend-config="${BACKEND_CONFIG_PATH}"
EOF

  if [ "${auto_approve}" = "0" ]; then
    cat >> "${destroy_script}" <<EOF
tofu destroy -var-file="${TFVARS_PATH}"
EOF
  else
    cat >> "${destroy_script}" <<EOF
tofu destroy -auto-approve -var-file="${TFVARS_PATH}"
EOF
  fi

  chmod +x "${destroy_script}"
  run_with_timeout "${CLEANUP_TIMEOUT_SECONDS}" "${TOFU_DESTROY_LOG_PATH}" bash "${destroy_script}"

  note "Destroy log: ${TOFU_DESTROY_LOG_PATH}"
}

print_plan() {
  cat <<EOF

Planned AWS integration sequence
0. Run the end-to-end integration sequence with failure cleanup:
   ./scripts/run-aws-integration.sh run

Readiness check before any AWS call:
   ./scripts/run-aws-integration.sh preflight

1. Initialize OpenTofu with an isolated backend key:
   cd "${INFRA_DIR}"
   tofu init -backend-config="${BACKEND_CONFIG_PATH}"

2. Run the first foundation apply with isolated vars:
   tofu apply -var-file="${TFVARS_PATH}"

3. Publish the bootstrap image to ECR repository ${ECR_REPOSITORY_NAME} using tag ${IMAGE_TAG}.
   ./scripts/run-aws-integration.sh bootstrap-publish

4. Run the second apply so Terraform can create the App Runner service:
   ./scripts/run-aws-integration.sh second-apply

5. Fetch the App Runner service URL and verify the public fixture response.
   ./scripts/run-aws-integration.sh verify

6. Destroy the isolated integration stack and remove temp artifacts:
   The runner now destroys automatically at the end of a successful run.
   It also attempts destroy automatically on failure, bounded by a timeout.

7. Manually destroy a prior run by reusing the same isolated run id:
   AWS_INTEGRATION_RUN_ID=<previous-run-id> ./scripts/run-aws-integration.sh destroy

Current boundary:
- The preflight mode checks tools and required local inputs without
  contacting AWS.
- The run mode now preserves the original failing exit code.
- It reports destroy failures as secondary cleanup failures.
- It now attempts isolated cleanup destroy from an EXIT trap when a
  destructive step fails.
- It now destroys automatically at the end of a successful run.
- It now supports an explicit destroy mode for prior runs.
- This runner now supports the isolated foundation apply.
- It now supports publishing the bootstrap fixture image.
- It now supports the second apply and service URL fetch.
- It now supports public fixture-response verification.
EOF
}

run_foundation_apply() {
  local auto_approve="${AWS_INTEGRATION_AUTO_APPROVE:-1}"

  require_materialized_value "AWS_REGION" "${AWS_REGION:-}"
  require_materialized_value "TF_STATE_BUCKET" "${TF_STATE_BUCKET:-}"
  require_materialized_value "GITHUB_OWNER" "${GITHUB_OWNER:-}"

  log_step "first-tofu-apply" "Running isolated foundation apply"
  note "Infra dir: ${INFRA_DIR}"
  note "Backend config: ${BACKEND_CONFIG_PATH}"
  note "Vars file: ${TFVARS_PATH}"
  fail_if_simulated "first-tofu-apply"

  (
    cd "${INFRA_DIR}"
    tofu init -backend-config="${BACKEND_CONFIG_PATH}"

    if [ "${auto_approve}" = "0" ]; then
      tofu apply -var-file="${TFVARS_PATH}"
    else
      tofu apply -auto-approve -var-file="${TFVARS_PATH}"
    fi
  )
}

run_bootstrap_publish() {
  local aws_account_id="${AWS_INTEGRATION_AWS_ACCOUNT_ID:-}"
  local registry=""
  local local_image_tag=""

  require_materialized_value "AWS_REGION" "${AWS_REGION:-}"
  validate_optional_env "AWS_INTEGRATION_AWS_ACCOUNT_ID" "${aws_account_id}" '^[0-9]{12}$'
  require_command aws
  require_command docker

  if [ ! -f "${FIXTURE_DIR}/Dockerfile" ] || [ ! -f "${FIXTURE_DIR}/server.py" ]; then
    echo "Integration fixture is missing from ${FIXTURE_DIR}" >&2
    exit 1
  fi

  if [ -z "${aws_account_id}" ]; then
    aws_account_id="$(
      aws sts get-caller-identity \
        --query Account \
        --output text
    )"
  fi

  require_materialized_value "AWS account id" "${aws_account_id}"

  registry="${aws_account_id}.dkr.ecr.${AWS_REGION}.amazonaws.com"
  REMOTE_IMAGE_URI="${registry}/${ECR_REPOSITORY_NAME}:${IMAGE_TAG}"
  local_image_tag="${ECR_REPOSITORY_NAME}:${IMAGE_TAG}"

  log_step "bootstrap-image-publish" "Publishing bootstrap image"
  note "Fixture dir: ${FIXTURE_DIR}"
  note "ECR repository: ${ECR_REPOSITORY_NAME}"
  note "Remote image URI: ${REMOTE_IMAGE_URI}"
  fail_if_simulated "bootstrap-image-publish"

  aws ecr describe-repositories \
    --repository-names "${ECR_REPOSITORY_NAME}" \
    --region "${AWS_REGION}" >/dev/null

  aws ecr get-login-password --region "${AWS_REGION}" \
    | docker login \
        --username AWS \
        --password-stdin "${registry}"

  docker build \
    -t "${local_image_tag}" \
    "${FIXTURE_DIR}"

  docker tag "${local_image_tag}" "${REMOTE_IMAGE_URI}"
  docker push "${REMOTE_IMAGE_URI}"
}

fetch_service_url() {
  local service_url=""
  local tofu_error_log="${WORKDIR}/tofu-service-url.stderr.log"
  local aws_error_log="${WORKDIR}/aws-service-url.stderr.log"

  log_step "url-fetch" "Resolving App Runner service URL"
  fail_if_simulated "url-fetch"

  if service_url="$(
    cd "${INFRA_DIR}" && tofu output -raw service_url 2>"${tofu_error_log}"
  )"; then
    :
  else
    service_url=""
  fi

  if [ -n "${service_url}" ]; then
    printf '%s' "${service_url}"
    return 0
  fi

  require_command aws
  require_materialized_value "AWS_REGION" "${AWS_REGION:-}"

  if service_url="$(
    aws apprunner list-services \
      --region "${AWS_REGION}" \
      --query "ServiceSummaryList[?ServiceName=='${SERVICE_NAME}'].ServiceUrl | [0]" \
      --output text 2>"${aws_error_log}"
  )"; then
    :
  else
    service_url=""
  fi

  if [ -n "${service_url}" ] && [ "${service_url}" != "None" ]; then
    printf '%s' "${service_url}"
    return 0
  fi

  echo "Unable to determine App Runner service URL after second apply." >&2
  if [ -s "${tofu_error_log}" ]; then
    echo "tofu output stderr saved to ${tofu_error_log}" >&2
  fi
  if [ -s "${aws_error_log}" ]; then
    echo "aws apprunner list-services stderr saved to ${aws_error_log}" >&2
  fi
  return 1
}

run_second_apply() {
  local auto_approve="${AWS_INTEGRATION_AUTO_APPROVE:-1}"
  local service_url=""

  require_materialized_value "AWS_REGION" "${AWS_REGION:-}"
  require_materialized_value "TF_STATE_BUCKET" "${TF_STATE_BUCKET:-}"
  require_materialized_value "GITHUB_OWNER" "${GITHUB_OWNER:-}"

  log_step "second-tofu-apply" "Running isolated second apply"
  note "Infra dir: ${INFRA_DIR}"
  note "Backend config: ${BACKEND_CONFIG_PATH}"
  note "Vars file: ${TFVARS_PATH}"
  fail_if_simulated "second-tofu-apply"

  (
    cd "${INFRA_DIR}"
    tofu init -backend-config="${BACKEND_CONFIG_PATH}"

    if [ "${auto_approve}" = "0" ]; then
      tofu apply -var-file="${TFVARS_PATH}"
    else
      tofu apply -auto-approve -var-file="${TFVARS_PATH}"
    fi
  )

  service_url="$(fetch_service_url)"
  echo "Service URL: ${service_url}"
}

run_verify() {
  local service_url=""
  local verify_path="${AWS_INTEGRATION_VERIFY_PATH:-/}"

  require_command python3

  service_url="$(fetch_service_url)"
  VERIFY_RESPONSE_PATH="${WORKDIR}/verify-response.json"

  log_step "verification" "Verifying public fixture response"
  fail_if_simulated "verification"
  note "Service URL: ${service_url}"
  note "Verify path: ${verify_path}"
  note "Response capture: ${VERIFY_RESPONSE_PATH}"

  python3 - "${service_url}" "${verify_path}" "${VERIFY_RESPONSE_PATH}" <<'PY'
import json
import sys
from pathlib import Path
from urllib import error, parse, request

service_url = sys.argv[1]
verify_path = sys.argv[2]
response_path = Path(sys.argv[3])

if not verify_path.startswith("/"):
    verify_path = "/" + verify_path

target_url = parse.urljoin(service_url.rstrip("/") + "/", verify_path.lstrip("/"))

try:
    with request.urlopen(target_url, timeout=30) as response:
        status_code = response.getcode()
        body = response.read().decode("utf-8")
except error.HTTPError as exc:
    print(f"Fixture verification failed with HTTP {exc.code}: {target_url}", file=sys.stderr)
    sys.exit(1)
except error.URLError as exc:
    print(f"Fixture verification failed to reach {target_url}: {exc}", file=sys.stderr)
    sys.exit(1)

if status_code != 200:
    print(f"Fixture verification returned unexpected status {status_code}: {target_url}", file=sys.stderr)
    sys.exit(1)

try:
    payload = json.loads(body)
except json.JSONDecodeError as exc:
    print(f"Fixture verification returned invalid JSON from {target_url}: {exc}", file=sys.stderr)
    sys.exit(1)

expected_service = "minimal-aws-github-ci-template"
expected_status = "ok"
expected_path = verify_path

if payload.get("status") != expected_status:
    print(
        f"Fixture verification expected status={expected_status!r} but got {payload.get('status')!r}",
        file=sys.stderr,
    )
    sys.exit(1)

if payload.get("service") != expected_service:
    print(
        f"Fixture verification expected service={expected_service!r} but got {payload.get('service')!r}",
        file=sys.stderr,
    )
    sys.exit(1)

if payload.get("path") != expected_path:
    print(
        f"Fixture verification expected path={expected_path!r} but got {payload.get('path')!r}",
        file=sys.stderr,
    )
    sys.exit(1)

response_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"Fixture verification passed: {target_url}")
PY
}

run_full_sequence() {
  run_foundation_apply
  run_bootstrap_publish
  run_second_apply
  run_verify
  run_destroy "success"
}

main() {
  if [ -n "${AWS_INTEGRATION_RUN_ID:-}" ]; then
    RUN_ID_EXPLICIT=1
  fi

  if [ "${MODE}" = "--help" ] || [ "${MODE}" = "-h" ]; then
    usage
    exit 0
  fi

  if [ "${MODE}" != "plan" ] && [ "${MODE}" != "preflight" ] && [ "${MODE}" != "run" ] && [ "${MODE}" != "foundation-apply" ] && [ "${MODE}" != "bootstrap-publish" ] && [ "${MODE}" != "second-apply" ] && [ "${MODE}" != "verify" ] && [ "${MODE}" != "destroy" ]; then
    echo "Unsupported mode: ${MODE}" >&2
    usage >&2
    exit 1
  fi

  if [ "${MODE}" = "destroy" ] && [ "${RUN_ID_EXPLICIT}" != "1" ]; then
    echo "Destroy mode requires AWS_INTEGRATION_RUN_ID so the runner does not guess which isolated stack to tear down." >&2
    exit 1
  fi

  if [ "${MODE}" = "preflight" ]; then
    run_preflight
    exit $?
  fi

  require_command tofu
  require_command git
  require_command jq
  require_command mktemp

  trap 'finalize_run $?' EXIT

  prepare_workdir
  materialize_tfvars

  if [ "${MODE}" != "plan" ]; then
    CLEANUP_REQUIRED=1
  fi

  if [ "${MODE}" = "plan" ]; then
    print_plan
    return
  fi

  if [ "${MODE}" = "run" ]; then
    run_full_sequence
    return
  fi

  if [ "${MODE}" = "foundation-apply" ]; then
    run_foundation_apply
    return
  fi

  if [ "${MODE}" = "bootstrap-publish" ]; then
    run_bootstrap_publish
    return
  fi

  if [ "${MODE}" = "second-apply" ]; then
    run_second_apply
    return
  fi

  if [ "${MODE}" = "destroy" ]; then
    run_destroy "manual"
    return
  fi

  run_verify
}

main "$@"
