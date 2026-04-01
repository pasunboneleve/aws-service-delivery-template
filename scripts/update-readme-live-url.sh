#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
README_PATH="${ROOT_DIR}/README.md"
INFRA_DIR="${ROOT_DIR}/infra"

if ! command -v tofu >/dev/null 2>&1; then
  echo "tofu is required to render the live URL section." >&2
  exit 1
fi

LIVE_URL="$(cd "${INFRA_DIR}" && tofu output -raw service_url 2>/dev/null || true)"

if [ -n "${LIVE_URL}" ]; then
  LIVE_URL_LINE="- Service URL: \`${LIVE_URL}\`"
else
  LIVE_URL_LINE="- Service URL: \`TODO\`"
fi

python3 - "${README_PATH}" "${LIVE_URL_LINE}" <<'PY'
from pathlib import Path
import sys

readme_path = Path(sys.argv[1])
live_url_line = sys.argv[2]
start = "<!-- LIVE_URL_START -->"
end = "<!-- LIVE_URL_END -->"
content = readme_path.read_text(encoding="utf-8")

start_index = content.find(start)
end_index = content.find(end)

if start_index == -1 or end_index == -1 or end_index < start_index:
    raise SystemExit("README markers not found")

replacement = f"{start}\n{live_url_line}\n{end}"
updated = content[:start_index] + replacement + content[end_index + len(end):]
readme_path.write_text(updated, encoding="utf-8")
PY
