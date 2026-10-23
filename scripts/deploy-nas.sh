#!/usr/bin/env bash
set -euo pipefail

# Publish a tested source snapshot to the existing NAS staging project.  The
# script deliberately refuses to deploy a migration graph older than the NAS.

usage() {
  cat <<'EOF'
Usage: scripts/deploy-nas.sh [--source <repository>] [--dry-run] [--from-hook]

Default source: .nas-release-sync under the launcher's repository when present;
otherwise the launcher's repository. An explicit --source always takes precedence.

Environment overrides:
  NAS_SSH_HOST             NAS host (default: 192.168.31.151)
  NAS_SSH_USER             SSH user (default: skymoonzyj)
  NAS_SSH_PORT             SSH port (default: 22)
  NAS_SSH_IDENTITY_FILE    Deployment SSH private key
  NAS_APP_DIR              Remote ERP directory
  NAS_COMPOSE_PROJECT      Compose project name
  NAS_PUBLIC_URL           HTTPS URL used for the final listener check
  ERP_NAS_AUTO_DEPLOY      Set to 0 to disable the post-commit hook
EOF
}

source_root=""
dry_run=false
from_hook=false
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
python3 scripts/nas-deploy-guard.py source-contract "$repo_root"

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
git diff --check
source_commit=$(git rev-parse --short=12 HEAD)
snapshot=$(mktemp -d "${TMPDIR:-/tmp}/erp-release.XXXXXX")
trap 'rm -rf "$snapshot"' EXIT
snapshot_filters=(
  --include='.env.example' --exclude='.git' --exclude='.githooks/'
  --exclude='.superpowers/' --exclude='.worktrees/' --exclude='.nas-release-sync/'
  --exclude='node_modules' --exclude='.venv' --exclude='.venv*'
  --exclude='__pycache__/' --exclude='.mypy_cache/' --exclude='.pytest_cache/'
  --exclude='.ruff_cache/' --exclude='dist/' --exclude='build/' --exclude='coverage/'
  --exclude='.build-metadata/' --exclude='.asset-history/' --exclude='.web-assets-scratch*'
  --exclude='.web-assets-compose.json'
  --exclude='/apps/web/dist-ios' --exclude='/apps/web/ios/App/App/public'
  --exclude='/apps/web/ios/App/App/SoundPreview'
  --exclude='/apps/web/ios/App/App/ERPBuildConfig.json'
  --exclude='/apps/web/ios/App/App/capacitor.config.json'
  --exclude='/apps/web/ios/App/App/config.xml'
  --exclude='DerivedData' --exclude='.build' --exclude='xcuserdata'
  --exclude='*.ipa' --exclude='*.xcarchive' --exclude='*.dSYM'
  --exclude='*.mobileprovision' --exclude='*.provisionprofile'
  --exclude='*.p12' --exclude='*.pfx'
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
  ssh "${ssh_options[@]}" "$remote_target" bash -s -- "$@"
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

printf 'NAS deployment preflight passed: source head %s, NAS revision %s.\n' \
  "$local_head" "$remote_revision"
if [[ "$dry_run" == true ]]; then
  printf 'Dry run only; no source or container was changed. Planned release: %s\n' "$release_id"
  exit 0
fi

remote_bash "$nas_app_dir" "$release_id" <<'REMOTE_INCOMING'
set -euo pipefail
app_dir=$1
release_id=$2
mkdir -p "$app_dir/incoming/$release_id" "$app_dir/releases" "$app_dir/backups"
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
  --exclude='.build-metadata/' \
  --exclude='.asset-history/' \
  --exclude='.web-assets-scratch*' \
  --exclude='.web-assets-compose.json' \
  --exclude='dist/' \
  --exclude='build/' \
  --exclude='/apps/web/dist-ios' \
  --exclude='/apps/web/ios/App/App/public' \
  --exclude='/apps/web/ios/App/App/SoundPreview' \
  --exclude='/apps/web/ios/App/App/ERPBuildConfig.json' \
  --exclude='/apps/web/ios/App/App/capacitor.config.json' \
  --exclude='/apps/web/ios/App/App/config.xml' \
  --exclude='DerivedData' \
  --exclude='.build' \
  --exclude='xcuserdata' \
  --exclude='*.ipa' \
  --exclude='*.xcarchive' \
  --exclude='*.dSYM' \
  --exclude='*.mobileprovision' \
  --exclude='*.provisionprofile' \
  --exclude='*.p12' \
  --exclude='*.pfx' \
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

remote_bash "$nas_app_dir" "$release_id" "$nas_project" "$local_head" "$remote_revision" "$nas_public_url" "$nas_health_bind_address" "$source_state" <<'REMOTE_DEPLOY'
set -euo pipefail
app_dir=$1
release_id=$2
project=$3
expected_head=$4
baseline_revision=$5
public_url=$6
health_bind_address=$7
expected_digest=$8
# Admission imports must not add bytecode to the already verified source tree.
export PYTHONDONTWRITEBYTECODE=1
incoming="$app_dir/incoming/$release_id"
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
test -L "$current" || { echo "current must be a symlink; refusing a non-atomic replacement." >&2; exit 17; }
previous_pointer=$(readlink -f "$current")
previous_active=$(cat "$app_dir/.active-release" 2>/dev/null || true)
previous_backup_eligible=false
if env -u ERP_RELEASE_ID -u EVALUATION_MEDIA_STORE_ID \
  python3 "$incoming/scripts/nas-deploy-guard.py" prior-backup "$app_dir" "$current" "$project" "$baseline_revision" >/dev/null 2>&1; then
  previous_backup_eligible=true
fi
switched=false
repairing=false
if [[ -z "$previous_active" ]]; then repairing=true; fi
web_scratch=""
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
    elif [[ "$repairing" == true && "$switched" == false ]]; then
      printf 'status=failed\nrelease=%s\nattempted_release=%s\nprevious_current=%s\nprevious_active=%s\nexit_code=%s\n' \
        "$previous_pointer" "$release" "$previous_pointer" "$previous_active" "$code" > "$app_dir/.deployment-state"
      echo "Forward repair failed before switching; current is retained for authenticated forward recovery." >&2
    else
      printf 'status=failed\nrelease=%s\nprevious_current=%s\nprevious_active=%s\nexit_code=%s\n' \
        "$release" "$previous_pointer" "$previous_active" "$code" > "$app_dir/.deployment-state"
      echo "Release failed; inspect .deployment-state. Candidate current is retained after activation; previous pointer is diagnostic only. Containers/database were NOT rolled back. Forward recovery is required." >&2
    fi
    if [[ "$switched" == true ]]; then
      rm -f "$app_dir/.active-release"
      python3 "$incoming/scripts/nas-deploy-guard.py" invalidate-web "$app_dir" "$release" "$project" || \
        echo "Failed activation receipt quarantined or requires operator inspection." >&2
    fi
  fi
  if [[ -n "$web_scratch" ]]; then rm -rf -- "$web_scratch"; fi
}
trap on_failure EXIT
export NAS_COMPOSE_PROJECT="$project"
# Bind the actual active authority before any candidate config needs new args.
authority=$(python3 "$incoming/scripts/nas-deploy-guard.py" old-authority "$app_dir" "$current" "$project")
authoritative_previous=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["configuration_release"])' <<<"$authority")
repairing=$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin)["mode"] == "forward" else "false")' <<<"$authority")
web_scratch=$(mktemp -d "$app_dir/.web-assets-scratch.XXXXXX")
printf '%s\n' "$authority" > "$web_scratch/authority.json"
asset_preparation=$(python3 "$incoming/scripts/nas-deploy-web-assets.py" prepare \
  --app-dir "$app_dir" --candidate-release "$release_id" --output-dir "$web_scratch/input")
printf '%s\n' "$asset_preparation" > "$web_scratch/prepare.json"
python3 "$incoming/scripts/nas-deploy-guard.py" record-predecessor "$app_dir" "$current" "$project" \
  "$web_scratch/prepare.json" "$web_scratch/authority.json"
export ERP_WEB_ASSETS_PROTOCOL=1 ERP_WEB_BUILD_ID="$release_id"
ERP_WEB_ASSET_INPUT_SHA256=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["input_sha256"])' <<<"$asset_preparation")
export ERP_WEB_ASSET_INPUT_SHA256
printf 'status=preparing\nrelease=%s\nprevious_current=%s\nprevious_active=%s\n' \
  "$release" "$previous_pointer" "$previous_active" > "$app_dir/.deployment-state"
mkdir -p "$release"
rsync -a --delete --exclude='.env' --exclude='secrets/' "$incoming/" "$release/"
cp -a "$authoritative_previous/.env" "$release/.env"
cp -a "$authoritative_previous/secrets" "$release/secrets"

# NAS-specific HTTPS overlays are maintained on the NAS until they are added
# to the source tree. Preserve them for every immutable source release.
for file in compose.nas-staging.yaml compose.nas-lan-staging.yaml; do
  if [[ ! -f "$release/$file" ]]; then
    cp -a "$authoritative_previous/$file" "$release/$file"
  fi
done
if [[ ! -f "$release/infra/caddy/Caddyfile" ]]; then
  mkdir -p "$release/infra/caddy"
  cp -a "$authoritative_previous/infra/caddy/Caddyfile" "$release/infra/caddy/Caddyfile"
fi

printf 'source_release_id=%s\nsource_alembic_head=%s\nprepared_utc=%s\n' \
  "$release_id" "$expected_head" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$release/.release-metadata"

cd "$release"
guard=(python3 "$release/scripts/nas-deploy-guard.py")
python3 - "$web_scratch/prepare.json" "$release/.web-assets-compose.json" "$project" "$release_id" <<'WEB_OVERRIDE'
import json, sys
from pathlib import Path
pre = json.loads(Path(sys.argv[1]).read_text())
value = {"services": {"web": {"image": sys.argv[3] + "-web:" + sys.argv[4],
    "build": {"additional_contexts": {"erp_retained_assets": pre["retained_context"]}}}}}
Path(sys.argv[2]).write_text(json.dumps(value) + "\n")
WEB_OVERRIDE
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
  -f "$release/.web-assets-compose.json"
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
candidate_image=$(docker image inspect "$project-web:$release_id" --format '{{.Id}}')
python3 "$release/scripts/nas-deploy-web-assets.py" verify-image \
  --image-id "$candidate_image" --input-manifest "$web_scratch/input/retained/manifest.json" > "$web_scratch/verified.json"
# Pin service recreation to the verified immutable ID, never a mutable tag.
python3 - "$release/.web-assets-compose.json" "$candidate_image" <<'WEB_PIN'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
value = json.loads(path.read_text())
value["services"]["web"]["image"] = sys.argv[2]
path.write_text(json.dumps(value) + "\n")
WEB_PIN
"${guard[@]}" admit-web "$app_dir" "$release" "$project" \
  "$web_scratch/prepare.json" "$web_scratch/verified.json" "$expected_digest" "$web_scratch/authority.json"
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
compose+=(-f "$current/.web-assets-compose.json")
"${compose[@]}" up -d --no-build --no-deps --force-recreate api scheduler web proxy caddy

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

"${guard[@]}" activate-web "$app_dir" "$release" "$project" \
  "$web_scratch/prepare.json" "$web_scratch/verified.json" "$expected_digest"
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
