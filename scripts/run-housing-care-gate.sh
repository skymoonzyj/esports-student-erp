#!/usr/bin/env bash
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
python_bin=${E2E_PYTHON_BIN:-$repo_root/.venv/bin/python}
if [[ ! -x "$python_bin" ]]; then
  echo "housing care gate requires E2E_PYTHON_BIN" >&2
  exit 3
fi
exec env -i PATH="$PATH" HOME="${HOME:-$repo_root}" \
  DOCKER_HOST="${DOCKER_HOST:-}" DOCKER_CONTEXT="${DOCKER_CONTEXT:-}" \
  PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-}" \
  PLAYWRIGHT_EXECUTABLE_PATH="${PLAYWRIGHT_EXECUTABLE_PATH:-}" \
  "$python_bin" "$repo_root/scripts/housing-care-owned-gate.py" --python "$python_bin"
