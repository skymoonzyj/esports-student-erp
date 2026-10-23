#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
hook_dir="$repo_root/.githooks"
key_file=${NAS_SSH_IDENTITY_FILE:-"$HOME/.ssh/id_ed25519_esports_erp_nas"}

[[ -x "$repo_root/scripts/deploy-nas.sh" ]] || {
  echo "Missing deployment script: $repo_root/scripts/deploy-nas.sh" >&2
  exit 2
}
[[ -x "$hook_dir/post-commit" ]] || {
  echo "Missing post-commit hook: $hook_dir/post-commit" >&2
  exit 2
}
[[ -r "$key_file" ]] || {
  echo "NAS deployment key is unavailable: $key_file" >&2
  exit 3
}

git -C "$repo_root" config --local core.hooksPath "$hook_dir"
printf 'NAS automatic deployment is enabled for this repository.\n'
printf 'Post-commit hook: %s\n' "$hook_dir/post-commit"
