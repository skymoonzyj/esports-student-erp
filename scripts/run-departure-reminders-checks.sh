#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
: "${E2E_PYTHON_BIN:?Use the owned departure-reminders gate}"
cd "$repo_root/apps/api"
exec "$E2E_PYTHON_BIN" -m app.cli.departure_e2e_runtime --phase "${1:-api}"
