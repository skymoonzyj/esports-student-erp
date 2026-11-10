#!/usr/bin/env bash
set -euo pipefail

# No partial selection or caller commands; the delegate owns the full manifest,
# disposable database checks, synthetic mailbox, financial checks and cleanup.
if (( $# != 0 )); then
  echo "Intake identity E2E refused: no caller selectors supported" >&2
  exit 2
fi
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
exec /bin/bash "$repo_root/scripts/run-assisted-personnel-e2e.sh" --suite intake-identity
