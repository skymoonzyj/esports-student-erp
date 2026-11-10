#!/usr/bin/env bash
set -euo pipefail

# Publish a tested source snapshot to the existing NAS staging project.  The
# script deliberately refuses to deploy a migration graph older than the NAS.

usage() {
  cat <<'EOF'
Usage: scripts/deploy-nas.sh [--source <repository>] [--dry-run] [--from-hook]
                           [--reactionx-release-bundle <directory>]

Default source: .nas-release-sync under the launcher's repository when present;
otherwise the launcher's repository. An explicit --source always takes precedence.
ReactionX bundles are optional, verified fixed-file inputs. Without the option
the candidate exposes no package; it never inherits an older release's EXE.
Bundle input must live outside the source repository (and outside source Git).

Environment overrides:
  NAS_SSH_HOST             NAS host (default: 192.168.31.151)
  NAS_SSH_USER             SSH user (default: skymoonzyj)
  NAS_SSH_PORT             SSH port (default: 22)
  NAS_SSH_IDENTITY_FILE    Deployment SSH private key
  NAS_APP_DIR              Remote ERP directory
  NAS_COMPOSE_PROJECT      Compose project name
  NAS_PUBLIC_URL           HTTPS URL used for the final listener check
  NAS_WEB_ASSET_HISTORY    Explicit approved JSON [{release_id,image_id}] for
                           one-time legacy asset recovery (default: [])
  ERP_NAS_AUTO_DEPLOY      Set to 0 to disable the post-commit hook
EOF
}

source_root=""
dry_run=false
from_hook=false
reactionx_bundle=""
while (($#)); do
  case "$1" in
    --source)
      source_root=${2:?"--source requires a directory"}
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --reactionx-release-bundle)
      reactionx_bundle=${2:?"--reactionx-release-bundle requires a directory"}
      shift 2
      ;;
    --from-hook)
      from_hook=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

script_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
if [[ -n "$reactionx_bundle" ]]; then
  # Absolute but NOT realpath: validator must still reject symlink components.
  reactionx_bundle=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$reactionx_bundle")
fi
if [[ -n "$source_root" ]]; then
  repo_root=$source_root
elif [[ -d "$script_root/.nas-release-sync" ]]; then
  repo_root="$script_root/.nas-release-sync"
else
  repo_root=$script_root
fi
repo_root=$(cd "$repo_root" && pwd)
cd "$repo_root"

if [[ "$from_hook" == true && ${ERP_NAS_AUTO_DEPLOY:-1} != "1" ]]; then
  echo "NAS automatic deployment is disabled (ERP_NAS_AUTO_DEPLOY=0)."
  exit 0
fi

nas_host=${NAS_SSH_HOST:-192.168.31.151}
nas_user=${NAS_SSH_USER:-skymoonzyj}
nas_port=${NAS_SSH_PORT:-22}
nas_key=${NAS_SSH_IDENTITY_FILE:-"$HOME/.ssh/id_ed25519_esports_erp_nas"}
nas_app_dir=${NAS_APP_DIR:-/home/skymoonzyj/esports-erp}
nas_project=${NAS_COMPOSE_PROJECT:-esports-erp-staging}
nas_public_url=${NAS_PUBLIC_URL:-https://skymoonzyj.l.cd:18080}
nas_health_bind_address=${NAS_HEALTH_BIND_ADDRESS:-$nas_host}
nas_web_asset_history=${NAS_WEB_ASSET_HISTORY:-'[]'}
remote_target="${nas_user}@${nas_host}"

for command_name in git ssh rsync python3; do
  command -v "$command_name" >/dev/null || {
    echo "Missing required command: $command_name" >&2
    exit 3
  }
done
[[ -r "$nas_key" ]] || {
  echo "NAS deployment key is unavailable: $nas_key" >&2
  exit 3
}
[[ -f apps/api/alembic.ini ]] || {
  echo "The selected source directory is not an ERP repository: $repo_root" >&2
  exit 2
}
[[ -f scripts/nas-deploy-guard.py ]] || {
  echo "The selected source lacks the required NAS deployment guard: $repo_root" >&2
  exit 2
}
[[ -f scripts/nas-web-assets.py ]] || {
  echo "The selected source lacks the required web asset preservation helper." >&2
  exit 2
}

if [[ -x "$repo_root/.venv/bin/alembic" ]]; then
  alembic_cmd=("$repo_root/.venv/bin/alembic")
elif [[ -x "$script_root/.venv/bin/alembic" ]]; then
  # Linked worktrees share the main workspace virtual environment.
  alembic_cmd=("$script_root/.venv/bin/alembic")
elif [[ -x "$repo_root/apps/api/.venv312/bin/alembic" ]]; then
  alembic_cmd=("$repo_root/apps/api/.venv312/bin/alembic")
elif command -v alembic >/dev/null; then
  alembic_cmd=(alembic)
else
  echo "Alembic is required to check migration compatibility." >&2
  exit 3
fi

# Build and transfer only a verified, stable snapshot. rsync includes actual
# untracked source bytes; neither Git's filename-only status nor mutable input
# is used as the release identity.
if [[ -n "$reactionx_bundle" ]]; then
  python3 - "$repo_root" "$reactionx_bundle" <<'VERIFY_BUNDLE_LOCATION'
import os
import sys
from pathlib import Path

# Source is a trusted repository boundary: compare its physical identity even
# when --source uses a logical alias such as macOS /var -> /private/var.
# Keep the supplied bundle spelling so its separate no-symlink validator can
# reject aliases instead of silently dereferencing untrusted components.
source = Path(sys.argv[1]).resolve(strict=True)
bundle = Path(os.path.abspath(sys.argv[2]))
if bundle.is_relative_to(source):
    raise SystemExit("ReactionX bundle must be outside source repository/image context.")
VERIFY_BUNDLE_LOCATION
fi
git diff --check
source_commit=$(git rev-parse --short=12 HEAD)
snapshot=$(mktemp -d "${TMPDIR:-/tmp}/erp-release.XXXXXX")
bundle_snapshot=$(mktemp -d "${TMPDIR:-/tmp}/erp-reactionx.XXXXXX")
# macOS TMPDIR may contain the system /var alias. Only our freshly created
# private temporary directory is canonicalized; supplied paths stay untrusted.
bundle_snapshot=$(cd "$bundle_snapshot" && pwd -P)
trap 'rm -rf "$snapshot" "$bundle_snapshot"' EXIT
snapshot_filters=(
  --include='.env.example' --exclude='.git' --exclude='.githooks/'
  --exclude='.superpowers/' --exclude='.worktrees/' --exclude='.nas-release-sync/'
  --exclude='node_modules' --exclude='.venv' --exclude='.venv*'
  --exclude='__pycache__/' --exclude='.mypy_cache/' --exclude='.pytest_cache/'
  --exclude='.ruff_cache/' --exclude='dist/' --exclude='build/' --exclude='coverage/'
  --exclude='playwright-report/' --exclude='test-results/' --exclude='blob-report/'
  --exclude='outputs/' --exclude='*.log' --exclude='*.xcresult/'
  --exclude='runtime/' --exclude='uploads/' --exclude='data/' --exclude='backups/'
  --exclude='secrets/' --exclude='.env' --exclude='.env.*' --exclude='*.pyc'
)
rsync -a "${snapshot_filters[@]}" "$repo_root/" "$snapshot/"
# Release source links are unsupported: never dereference local private outputs
# or publish links that could resolve differently on the NAS.
python3 - "$snapshot" <<'VERIFY_SNAPSHOT_LINKS'
import sys
from pathlib import Path

if any(path.is_symlink() for path in Path(sys.argv[1]).rglob("*")):
    print("Unsupported source symlink in release snapshot; deployment stopped.", file=sys.stderr)
    raise SystemExit(4)
VERIFY_SNAPSHOT_LINKS
snapshot_changes=$(rsync -anrc --delete --itemize-changes "${snapshot_filters[@]}" "$repo_root/" "$snapshot/")
[[ -z "$snapshot_changes" ]] || {
  echo "Source changed while creating release snapshot; retry after edits finish." >&2
  exit 4
}
source_state=$(python3 "$snapshot/scripts/nas-deploy-guard.py" digest "$snapshot")
reactionx_digest=unavailable
if [[ -n "$reactionx_bundle" ]]; then
  # Freeze outside source identity and image build context, before NAS writes.
  reactionx_digest=$(python3 "$snapshot/scripts/reactionx-release-bundle.py" \
    freeze "$reactionx_bundle" "$bundle_snapshot/bundle")
  python3 "$snapshot/scripts/reactionx-release-bundle.py" \
    verify "$bundle_snapshot/bundle" "$reactionx_digest" >/dev/null
fi
cd "$snapshot"

local_heads=$("${alembic_cmd[@]}" -c apps/api/alembic.ini heads | awk '{print $1}')
local_head_count=$(printf '%s\n' "$local_heads" | sed '/^$/d' | wc -l | tr -d ' ')
[[ "$local_head_count" == "1" && -n "$local_heads" ]] || {
  echo "Expected exactly one local Alembic head; deployment stopped." >&2
  exit 4
}
local_head=$local_heads

ssh_options=(
  -i "$nas_key"
  -p "$nas_port"
  -o BatchMode=yes
  -o ConnectTimeout=15
  -o StrictHostKeyChecking=yes
)

remote_bash() {
  # OpenSSH joins command argv before the remote shell parses it. Encode once
  # so JSON, whitespace and shell metacharacters remain literal arguments.
  local remote_command
  printf -v remote_command '%q ' bash -s -- "$@"
  ssh "${ssh_options[@]}" "$remote_target" "$remote_command"
}

remote_bash "$nas_app_dir" "$nas_project" <<'REMOTE_PREFLIGHT'
set -euo pipefail
app_dir=$1
project=$2

test -d "$app_dir/current"
test -f "$app_dir/current/.env"
test -d "$app_dir/current/secrets"
test -f "$app_dir/current/compose.nas-staging.yaml"
test -f "$app_dir/current/compose.nas-lan-staging.yaml"
docker inspect "${project}-db-1" >/dev/null
docker inspect "${project}-api-1" >/dev/null
command -v rsync >/dev/null
command -v sha256sum >/dev/null
command -v python3 >/dev/null
command -v flock >/dev/null
command -v curl >/dev/null
docker compose version >/dev/null
REMOTE_PREFLIGHT

remote_revision=$(remote_bash "$nas_project" <<'REMOTE_REVISION'
set -euo pipefail
project=$1
docker exec "${project}-db-1" psql -U erp -d erp -Atc 'select version_num from alembic_version'
REMOTE_REVISION
)
[[ "$remote_revision" =~ ^[a-zA-Z0-9_]+$ ]] || {
  echo "The NAS returned an invalid Alembic revision; deployment stopped." >&2
  exit 4
}

local_history=$("${alembic_cmd[@]}" -c apps/api/alembic.ini history)
if ! grep -Fq -- "-> ${remote_revision}" <<<"$local_history"; then
  cat >&2 <<EOF
Deployment stopped: the local source does not contain the NAS database revision
${remote_revision}. This prevents an older or incompatible branch from replacing
the active NAS release. Merge/rebase onto the active release line first.
EOF
  exit 10
fi

release_id="$(date -u +%Y%m%dT%H%M%SZ)-${source_commit}-${source_state:0:12}"
if [[ "$reactionx_digest" != unavailable ]]; then
  release_id="${release_id}-rx${reactionx_digest:0:12}"
fi

printf 'NAS deployment preflight passed: source head %s, NAS revision %s.\n' \
  "$local_head" "$remote_revision"
if [[ "$dry_run" == true ]]; then
  printf 'Dry run only; no source or container was changed. Planned release: %s\n' "$release_id"
  exit 0
fi

remote_bash "$nas_app_dir" "$release_id" "$reactionx_digest" <<'REMOTE_INCOMING'
set -euo pipefail
app_dir=$1
release_id=$2
mkdir -p "$app_dir/incoming/$release_id" "$app_dir/releases" "$app_dir/backups"
if [[ "$3" != unavailable ]]; then
  mkdir -p "$app_dir/incoming/$release_id-reactionx-bundle"
fi
REMOTE_INCOMING

rsync_ssh=$(printf '%q ' ssh "${ssh_options[@]}")
rsync -a --delete --human-readable \
  --include='.env.example' \
  --exclude='.git' \
  --exclude='.githooks/' \
  --exclude='.superpowers/' \
  --exclude='.worktrees/' \
  --exclude='node_modules' \
  --exclude='.venv' \
  --exclude='.venv*' \
  --exclude='__pycache__/' \
  --exclude='.mypy_cache/' \
  --exclude='.pytest_cache/' \
  --exclude='.ruff_cache/' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='coverage/' \
  --exclude='playwright-report/' \
  --exclude='test-results/' \
  --exclude='blob-report/' \
  --exclude='outputs/' \
  --exclude='*.log' \
  --exclude='*.xcresult/' \
  --exclude='runtime/' \
  --exclude='uploads/' \
  --exclude='data/' \
  --exclude='backups/' \
  --exclude='secrets/' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='*.pyc' \
  -e "$rsync_ssh" \
  "$snapshot/" "$remote_target:$nas_app_dir/incoming/$release_id/"

if [[ "$reactionx_digest" != unavailable ]]; then
  python3 "$snapshot/scripts/reactionx-release-bundle.py" \
    verify "$bundle_snapshot/bundle" "$reactionx_digest" >/dev/null
  rsync -a --delete -e "$rsync_ssh" \
    "$bundle_snapshot/bundle/" "$remote_target:$nas_app_dir/incoming/$release_id-reactionx-bundle/"
fi

remote_bash "$nas_app_dir" "$release_id" "$nas_project" "$local_head" "$remote_revision" "$nas_public_url" "$nas_health_bind_address" "$source_state" "$nas_web_asset_history" "$reactionx_digest" <<'REMOTE_DEPLOY'
set -euo pipefail
app_dir=$1
release_id=$2
project=$3
expected_head=$4
baseline_revision=$5
public_url=$6
health_bind_address=$7
expected_digest=$8
web_asset_history=${9:-'[]'}
reactionx_digest=${10:-unavailable}
incoming="$app_dir/incoming/$release_id"
incoming_bundle="$app_dir/incoming/$release_id-reactionx-bundle"
release="$app_dir/releases/$release_id"
current="$app_dir/current"
db_container="${project}-db-1"
[[ "$public_url" == https://skymoonzyj.l.cd:18080 ]] || {
  echo "Public TLS gate must validate the established skymoonzyj.l.cd:18080 endpoint." >&2
  exit 17
}

exec 9>"$app_dir/.deploy.lock"
# Queue behind short shared backup/auth operations without interrupting them.
# A timeout remains a release blocker and occurs before candidate preparation.
flock -x -w 50 9 || {
  echo "Timed out waiting for the NAS deployment/backup lock (50 seconds); retry after the active operation completes." >&2
  exit 14
}

test -d "$incoming"
test ! -e "$release"
[[ "$expected_digest" =~ ^[a-f0-9]{64}$ ]] || { echo "Invalid expected snapshot digest." >&2; exit 17; }
actual_digest=$(python3 "$incoming/scripts/nas-deploy-guard.py" digest "$incoming")
[[ "$actual_digest" == "$expected_digest" ]] || { echo "Transferred release digest mismatch." >&2; exit 17; }
# Independent payload identity is verified under the same activation lock. An
# absent option cannot silently reuse a leftover incoming or previous package.
if [[ "$reactionx_digest" == unavailable ]]; then
  [[ ! -e "$incoming_bundle" && ! -L "$incoming_bundle" ]] || {
    echo "Unexpected ReactionX bundle while package is unavailable." >&2; exit 17;
  }
else
  [[ "$reactionx_digest" =~ ^[a-f0-9]{64}$ ]] || { echo "Invalid bundle digest." >&2; exit 17; }
  python3 "$incoming/scripts/reactionx-release-bundle.py" \
    verify "$incoming_bundle" "$reactionx_digest" >/dev/null
fi
test -L "$current" || { echo "current must be a symlink; refusing a non-atomic replacement." >&2; exit 17; }
previous_pointer=$(readlink -f "$current")
previous_active=$(cat "$app_dir/.active-release" 2>/dev/null || true)
previous_backup_eligible=false
if env -u ERP_RELEASE_ID -u EVALUATION_MEDIA_STORE_ID \
  python3 "$incoming/scripts/nas-deploy-guard.py" prior-backup "$app_dir" "$current" "$project" "$baseline_revision" >/dev/null 2>&1; then
  previous_backup_eligible=true
fi
switched=false
on_failure() {
  code=$?
  if (( code != 0 )); then
    # A candidate preparation error must not revoke backup eligibility from a
    # verified, untouched active release. Revalidate under the deployment lock;
    # the candidate's exported identity must not contaminate the prior config.
    if [[ "$switched" == false && "$previous_backup_eligible" == true ]] && \
      [[ "$(readlink -f "$current")" == "$previous_pointer" ]] && \
      [[ "$(cat "$app_dir/.active-release" 2>/dev/null)" == "$previous_pointer" ]] && \
      [[ "$(docker exec "$db_container" psql -U erp -d erp -Atc 'select version_num from alembic_version')" == "$baseline_revision" ]] && \
      env -u ERP_RELEASE_ID -u EVALUATION_MEDIA_STORE_ID \
        python3 "$incoming/scripts/nas-deploy-guard.py" runtime "$app_dir" "$current" "$project" >/dev/null 2>&1; then
      printf 'status=attempt_failed\nrelease=%s\nattempted_release=%s\nactive_unchanged=true\nprevious_current=%s\nprevious_active=%s\nexit_code=%s\n' \
        "$previous_pointer" "$release" "$previous_pointer" "$previous_active" "$code" > "$app_dir/.deployment-state"
      echo "Candidate deployment failed before activation; the verified prior release and its automatic backups remain eligible." >&2
    else
      printf 'status=failed\nrelease=%s\nprevious_current=%s\nprevious_active=%s\nexit_code=%s\n' \
        "$release" "$previous_pointer" "$previous_active" "$code" > "$app_dir/.deployment-state"
      echo "Release failed; inspect .deployment-state. Candidate current is retained after activation; previous pointer is diagnostic only. Containers/database were NOT rolled back. Forward recovery is required." >&2
    fi
    if [[ "$switched" == true ]]; then
      rm -f "$app_dir/.active-release"
    fi
  fi
}
trap on_failure EXIT
printf 'status=preparing\nrelease=%s\nprevious_current=%s\nprevious_active=%s\n' \
  "$release" "$previous_pointer" "$previous_active" > "$app_dir/.deployment-state"
mkdir -p "$release"
rsync -a --delete --exclude='.env' --exclude='secrets/' "$incoming/" "$release/"
mkdir -p "$release/runtime"
chmod 755 "$release/runtime"
if [[ "$reactionx_digest" == unavailable ]]; then
  mkdir "$release/runtime/reactionx-release"
  chmod 755 "$release/runtime/reactionx-release"
else
  python3 "$release/scripts/reactionx-release-bundle.py" \
    freeze "$incoming_bundle" "$release/runtime/reactionx-release" >/dev/null
  python3 "$release/scripts/reactionx-release-bundle.py" \
    verify "$release/runtime/reactionx-release" "$reactionx_digest" >/dev/null
fi
cp -a "$current/.env" "$release/.env"
cp -a "$current/secrets" "$release/secrets"

# NAS-specific HTTPS overlays are maintained on the NAS until they are added
# to the source tree. Preserve them for every immutable source release.
for file in compose.nas-staging.yaml compose.nas-lan-staging.yaml; do
  if [[ ! -f "$release/$file" ]]; then
    cp -a "$current/$file" "$release/$file"
  fi
done
if [[ ! -f "$release/infra/caddy/Caddyfile" ]]; then
  mkdir -p "$release/infra/caddy"
  cp -a "$current/infra/caddy/Caddyfile" "$release/infra/caddy/Caddyfile"
fi

printf 'source_release_id=%s\nsource_alembic_head=%s\nprepared_utc=%s\n' \
  "$release_id" "$expected_head" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$release/.release-metadata"
printf 'reactionx_bundle_digest=%s\n' "$reactionx_digest" >> "$release/.release-metadata"

cd "$release"
guard=(python3 "$release/scripts/nas-deploy-guard.py")
# Establish the old API volume as authoritative before any marker bootstrap.
"${guard[@]}" authority "$app_dir" "$release" "$project"
EVALUATION_MEDIA_STORE_ID=$("${guard[@]}" prepare-env "$app_dir" "$release" "$project")
export EVALUATION_MEDIA_STORE_ID
export ERP_RELEASE_ID="$release_id"
compose=(
  docker compose -p "$project"
  --project-directory "$release"
  -f compose.yaml
  -f compose.prod.yaml
  -f compose.nas-staging.yaml
  -f compose.nas-lan-staging.yaml
)
"${compose[@]}" config --quiet
"${guard[@]}" config "$app_dir" "$release" "$project"
if [[ -f scripts/erp-backup.py ]]; then
  operations_dir=$("${compose[@]}" config --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
paths = [v["source"] for v in config["services"]["api"].get("volumes", []) if v.get("target") == "/app/var" and v.get("type") == "bind"]
if len(paths) != 1:
    raise SystemExit("A host operations status directory must be mounted at /app/var")
print(paths[0])
')
  mkdir -p "$operations_dir"
  chmod 755 "$operations_dir"
  # Replace any legacy cron wrapper before the first migration/switch can fail.
  # The new consumer refuses preparing/verifying/failed deployment states.
  python3 "$release/scripts/erp-backup-install.py" \
    --app-dir "$app_dir" --project "$project" \
    --key-file "$HOME/.config/esports-erp/backup.key" \
    --backup-dir "$app_dir/backups/full" --status-file "$operations_dir/backup-status.json"
fi
"${compose[@]}" build api scheduler web
# Preserve complete previous asset groups before touching the active pointer or
# schema. The helper pins images, enforces bounded seven-day retirement, verifies
# rebuilt bytes and only then replaces the candidate web tag. No extra Compose
# overlays/volumes are introduced; the existing four-file guard stays authoritative.
python3 "$release/scripts/nas-web-assets.py" \
  --app-dir "$app_dir" --project "$project" --release-id "$release_id" \
  --history "$web_asset_history"
"${compose[@]}" run -T --rm --no-deps api python -m app.evaluations.media_store --initialize-store-identity </dev/null
"${compose[@]}" run -T --rm --no-deps api python -m app.evaluations.media_store --verify-store-identity </dev/null

previous_revision=$(docker exec "$db_container" psql -U erp -d erp -Atc \
  'select version_num from alembic_version')
[[ "$previous_revision" == "$baseline_revision" || "$previous_revision" == "$expected_head" ]] || {
  echo "NAS database revision changed after preflight; deployment was not switched." >&2
  exit 15
}
activate_candidate_pointer() {
  printf 'status=verifying\nrelease=%s\nprevious_current=%s\nprevious_active=%s\n' \
    "$release" "$previous_pointer" "$previous_active" > "$app_dir/.deployment-state"
  rm -f "$app_dir/.active-release"
  switched=true
  "${guard[@]}" pointer "$app_dir" "$release" "$project"
}
if [[ "$previous_revision" != "$expected_head" ]]; then
  umask 077
  backup_file="$app_dir/backups/${project}-${release_id}-before-${previous_revision}.dump"
  backup_tmp="${backup_file}.tmp"
  docker exec "$db_container" pg_dump -U erp -d erp --format=custom > "$backup_tmp"
  mv "$backup_tmp" "$backup_file"
  sha256sum "$backup_file" > "${backup_file}.sha256"
  printf 'Database backup created before migration: %s\n' "$backup_file"
  # Once schema changes may begin, the older entry point must not be executable
  # via current. A failure keeps the candidate and requires forward recovery.
  activate_candidate_pointer
  # The remote deployment body is delivered to bash over stdin. Disable the
  # compose run TTY/stdin attachment so it cannot consume the remaining steps.
  # Keep the remote deployment script's stdin for bash itself. Without an
  # explicit closed stdin, Compose can consume the remaining heredoc after
  # the migration command and silently skip the service switch/health checks.
  "${compose[@]}" run -T --rm --no-deps api alembic upgrade head </dev/null
  migrated_revision=$(docker exec "$db_container" psql -U erp -d erp -Atc \
    'select version_num from alembic_version')
  [[ "$migrated_revision" == "$expected_head" ]] || {
    echo "Migration did not reach the expected revision; candidate current is retained and service recreation was not attempted." >&2
    exit 12
  }
fi

# Use the stable logical path in Compose labels and fnOS's saved entry point.
# Replace the symlink atomically. Failed gates retain candidate current and
# diagnostics, never exposing the older executable configuration again.
if [[ "$switched" != true ]]; then
  activate_candidate_pointer
fi
compose=(docker compose -p "$project" --project-directory "$current")
for file in compose.yaml compose.prod.yaml compose.nas-staging.yaml compose.nas-lan-staging.yaml; do
  compose+=(-f "$current/$file")
done
"${compose[@]}" up -d --no-deps --force-recreate api scheduler web proxy caddy

deadline=$((SECONDS + 120))
while (( SECONDS < deadline )); do
  api_status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${project}-api-1")
  web_status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${project}-web-1")
  proxy_status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${project}-proxy-1")
  caddy_status=$(docker inspect -f '{{.State.Status}}' "${project}-caddy-1")
  if [[ "$api_status" == healthy && "$web_status" == healthy && "$proxy_status" == healthy && "$caddy_status" == running ]]; then
    break
  fi
  sleep 2
done

[[ ${api_status:-} == healthy && ${web_status:-} == healthy && ${proxy_status:-} == healthy && ${caddy_status:-} == running ]] || {
  docker ps --filter "name=$project" --format '{{.Names}} {{.Status}}' >&2
  echo "New ERP containers did not become healthy; release was retained for diagnosis." >&2
  exit 13
}

docker exec "${project}-proxy-1" wget -q -O - http://127.0.0.1:8080/api/v1/health/live >/dev/null
"${guard[@]}" runtime "$app_dir" "$current" "$project"
# Re-read mounted bytes inside API before claiming the candidate healthy.
if [[ "$reactionx_digest" == unavailable ]]; then
  docker exec "${project}-api-1" python -c 'import os; assert not os.listdir("/app/runtime/reactionx-release"), "Unexpected release package"'
else
  docker exec "${project}-api-1" python -c 'import sys; from pathlib import Path; from app.reactionx.release_bundle import bundle_digest; bundle_digest(Path("/app/runtime/reactionx-release"), sys.argv[1])' "$reactionx_digest"
fi
for service in api scheduler; do
  docker exec "${project}-${service}-1" python -m app.evaluations.media_store --verify-store-identity
  docker exec "${project}-${service}-1" python -c 'import os; from pathlib import Path; paths=[v for k,v in os.environ.items() if k.endswith("_FILE") and v]; assert paths and all(Path(p).is_file() and Path(p).stat().st_size > 0 for p in paths), "Required secret unavailable"'
done
# Readiness is stronger than the HTTP liveness endpoint: use the new API's
# own loaded credentials to execute a read-only query, then test HTTP routing.
docker exec "${project}-api-1" python -c 'from sqlalchemy import text; from app.core.database import SessionLocal; db=SessionLocal(); assert db.execute(text("SELECT 1")).scalar_one() == 1; db.close()'
actual_head=$(docker exec "$db_container" psql -U erp -d erp -Atc 'select version_num from alembic_version')
[[ "$actual_head" == "$expected_head" ]] || { echo "Runtime Alembic head mismatch." >&2; exit 18; }
scheduler_started=$(docker inspect -f '{{.State.StartedAt}}' "${project}-scheduler-1")
[[ "$scheduler_started" =~ ^[0-9T:.Z+-]+$ ]] || { echo "Invalid scheduler start time." >&2; exit 18; }
heartbeat_deadline=$((SECONDS + 120))
while (( SECONDS < heartbeat_deadline )); do
  heartbeat=$(docker exec "$db_container" psql -U erp -d erp -Atc "SELECT EXISTS (SELECT 1 FROM operations_service_heartbeats WHERE service_key = 'teaching_scheduler' AND status = 'healthy' AND consecutive_failures = 0 AND last_success_at >= '$scheduler_started'::timestamptz AND last_seen_at >= now() - interval '120 seconds')")
  [[ "$heartbeat" == t ]] && break
  sleep 2
done
[[ "${heartbeat:-}" == t ]] || { echo "Fresh scheduler heartbeat unavailable." >&2; exit 18; }
authority=${public_url#*://}
authority=${authority%%/*}
public_host=${authority%%:*}
public_port=443
if [[ "$authority" == *:* ]]; then
  public_port=${authority##*:}
fi
curl --fail --silent --show-error --connect-timeout 10 \
  --resolve "${public_host}:${public_port}:${health_bind_address}" \
  "${public_url%/}/api/v1/health/live" >/dev/null

printf '%s\n' "$release" > "$app_dir/.active-release.tmp"
mv -f "$app_dir/.active-release.tmp" "$app_dir/.active-release"
printf 'status=healthy\nrelease=%s\n' "$release" > "$app_dir/.deployment-state"
printf 'NAS release %s is active at %s.\n' "$release_id" "$public_url"
REMOTE_DEPLOY

# Run outside the deployment lock: the backup runner takes that lock in shared
# mode so migrations cannot race the exported database snapshot and recovery files.
remote_bash "$nas_app_dir" "$nas_project" <<'REMOTE_BACKUP'
set -euo pipefail
app_dir=$1
project=$2
exec 9>"$app_dir/.deploy.lock"
flock -s 9
release=$(cat "$app_dir/.active-release")
case "$release" in "$app_dir"/releases/*) ;; *) echo "Invalid active release path" >&2; exit 16 ;; esac
backup_failed() {
  code=$?
  if (( code != 0 )); then
    printf 'status=backup_failed\nrelease=%s\nexit_code=%s\n' "$release" "$code" > "$app_dir/.deployment-state"
    echo "Runtime is active, but required backup/restore verification failed; deployment is blocked." >&2
  fi
}
trap backup_failed EXIT
if [[ -f "$release/scripts/erp-backup.py" ]]; then
  key_file="$HOME/.config/esports-erp/backup.key"
  backup_dir="$app_dir/backups/full"
  operations_dir=$(docker inspect "$project-api-1" --format '{{range .Mounts}}{{if eq .Destination "/app/var"}}{{.Source}}{{end}}{{end}}')
  [[ -n "$operations_dir" && -d "$operations_dir" ]]
  if [[ ! -e "$key_file" ]]; then
    python3 "$release/scripts/erp-backup.py" init-key --key-file "$key_file"
  fi
  python3 "$release/scripts/erp-backup-install.py" \
    --app-dir "$app_dir" --project "$project" --key-file "$key_file" \
    --backup-dir "$backup_dir" --status-file "$operations_dir/backup-status.json"
  python3 "$app_dir/backup-tools/run.py"
fi
printf 'status=complete\nrelease=%s\n' "$release" > "$app_dir/.deployment-state"
REMOTE_BACKUP
