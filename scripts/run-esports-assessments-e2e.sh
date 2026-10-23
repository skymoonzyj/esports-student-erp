#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
api_port=${E2E_API_PORT:-8014}
web_port=${E2E_WEB_PORT:-5188}
api_origin="http://127.0.0.1:${api_port}"
web_origin="http://127.0.0.1:${web_port}"

if [[ ${E2E_API_URL:-$api_origin} != "$api_origin" || \
      ${E2E_BASE_URL:-$web_origin} != "$web_origin" ]]; then
  echo "天赋评测 2 E2E 只允许当前隔离 runner 的 loopback origin" >&2
  exit 2
fi

export E2E_API_URL="$api_origin"
export E2E_BASE_URL="$web_origin"
export E2E_CORE_MODE=esports
exec bash "$root/scripts/run-core-e2e.sh"
