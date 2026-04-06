#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="${SCRIPT_PATH%/*}"
if [ "${SCRIPT_DIR}" = "${SCRIPT_PATH}" ]; then
  SCRIPT_DIR="."
fi
SCRIPT_DIR="$(cd "${SCRIPT_DIR}" && pwd)"
if ! PYTHON_BIN="$(command -v python3 2>/dev/null)"; then
  case "${1:-}" in
    preflight)
      printf '\n==> [preflight] Checking local readiness for the first real AWS integration run\n'
      printf "missing: tool 'python3' is not installed\n"
      printf 'Preflight failed with 1 missing item(s).\n' >&2
      exit 1
      ;;
    -h|--help)
      cat <<'EOF'
Usage:
  ./scripts/run-aws-integration.sh [plan|preflight|run|foundation-apply|bootstrap-publish|second-apply|verify|destroy]
EOF
      exit 0
      ;;
    *)
      printf "Required command not found: python3\n" >&2
      exit 1
      ;;
  esac
fi
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/run_aws_integration.py" "$@"
