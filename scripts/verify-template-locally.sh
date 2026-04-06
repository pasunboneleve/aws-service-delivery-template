#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA_DIR="${ROOT_DIR}/infra"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

run_optional() {
  local command_name="$1"
  shift

  if command -v "${command_name}" >/dev/null 2>&1; then
    "$@"
  else
    echo "Skipping optional check; ${command_name} is not installed."
  fi
}

require_command tofu
require_command python3

echo "==> tofu fmt -recursive -check"
(cd "${INFRA_DIR}" && tofu fmt -recursive -check)

echo "==> tofu init -backend=false"
(cd "${INFRA_DIR}" && tofu init -backend=false)

echo "==> tofu validate"
(cd "${INFRA_DIR}" && tofu validate)

echo "==> python3 -m unittest discover -s tests -p test_*.py -v"
(cd "${ROOT_DIR}" && python3 -m unittest discover -s tests -p 'test_*.py' -v)

echo "==> shellcheck scripts/*.sh"
run_optional shellcheck shellcheck "${ROOT_DIR}"/scripts/*.sh

echo "==> actionlint"
run_optional actionlint actionlint
