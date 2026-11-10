#!/usr/bin/env bash
set -euo pipefail

# Fixed full-check delegate. The selected runner owns disposable-DB checks,
# synthetic reseeding, private mailbox/processes and financial postchecks.
if (( $# != 0 )); then
  echo "Intake role E2E refused: no caller selectors supported" >&2
  exit 2
fi
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
exec /bin/bash "$repo_root/scripts/run-assisted-personnel-e2e.sh" --suite intake-roles
