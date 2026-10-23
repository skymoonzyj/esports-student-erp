#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
export PYTHONPATH="$repo_root/apps/api${PYTHONPATH:+:$PYTHONPATH}"

tool_bin_dir=""
for candidate in "$repo_root/.venv/bin" "$repo_root/../.venv/bin"; do
  if [[ -x "$candidate/python" ]]; then
    tool_bin_dir=$candidate
    break
  fi
done

if [[ -n "$tool_bin_dir" ]]; then
  python_cmd="$tool_bin_dir/python"
  ruff_cmd="$tool_bin_dir/ruff"
  mypy_cmd="$tool_bin_dir/mypy"
else
  python_cmd=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
  ruff_cmd=$(command -v ruff 2>/dev/null || true)
  mypy_cmd=$(command -v mypy 2>/dev/null || true)
fi

for tool_spec in \
  "Python|$python_cmd" \
  "Ruff|$ruff_cmd" \
  "Mypy|$mypy_cmd"; do
  tool_name=${tool_spec%%|*}
  tool_path=${tool_spec#*|}
  if [[ -z "$tool_path" || ! -x "$tool_path" ]]; then
    echo "缺少 $tool_name，请安装后再运行完整检查" >&2
    exit 3
  fi
done

if docker compose version >/dev/null 2>&1; then
  compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose=(docker-compose)
else
  echo "缺少 Docker Compose" >&2
  exit 3
fi

e2e_compose=(
  "${compose[@]}"
  --profile manual-scheduler
  -p esports-erp-e2e
  -f compose.yaml
  -f compose.e2e.yaml
)
backup_file=""
cleanup() {
  if [[ -n "$backup_file" ]]; then
    rm -f "$backup_file" "${backup_file}.sha256"
  fi
  "${e2e_compose[@]}" down --remove-orphans --volumes >/dev/null 2>&1 || true
}
trap cleanup EXIT

bash -n scripts/db-schema-manifest.sh
bash -n scripts/backup-db.sh
bash -n scripts/restore-db.sh

"${compose[@]}" -f compose.yaml config -q
prod_config=$("${compose[@]}" -f compose.yaml -f compose.prod.yaml config)
if grep -Eq '^[[:space:]]+(POSTGRES_PASSWORD|DATABASE_URL|FIELD_ENCRYPTION_MASTER_KEY|FIELD_LOOKUP_KEY|SESSION_TOKEN_PEPPER):' <<<"$prod_config"; then
  echo "生产配置不得通过普通环境变量注入秘密" >&2
  exit 4
fi
"${compose[@]}" -p esports-erp-e2e -f compose.yaml -f compose.e2e.yaml config -q

"${e2e_compose[@]}" up -d --wait db
export TEST_DATABASE_URL="postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:${E2E_DB_PORT:-55433}/erp_e2e"

"$python_cmd" -m pytest apps/api/tests -q
"$ruff_cmd" check apps/api/app apps/api/tests
"$mypy_cmd" --config-file apps/api/pyproject.toml apps/api/app
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint

# These fixed values belong only to the disposable browser-test runtime. Keep
# them out of the unit-test process, whose fixtures intentionally use other keys.
export FIELD_ENCRYPTION_MASTER_KEY="AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
export FIELD_LOOKUP_KEY="Hy4tLCsqKSgnJiUkIyIhIB8eHRwbGhkYFxYVFBMSERA="
export SESSION_TOKEN_PEPPER="EBESExQVFhcYGRobHB0eHyAhIiMkJSYnKCkqKywtLi8="
export REGISTRATION_VERIFICATION_PEPPER="AQIDBAUGBwgJCgsMDQ4PEBESExQVFhcYGRobHB0eHyA="
export REGISTRATION_SESSION_TOKEN_PEPPER="AgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4fICE="
export EVALUATION_TOOL_SERVICE_TOKEN="e2e-evaluation-v1-service-token-2026"
export EVALUATION_TOOL_WEB_SERVICE_TOKEN="e2e-evaluation-web-service-token-2026"
export EVALUATION_TOOL_WORKER_SERVICE_TOKEN="e2e-evaluation-worker-token-2026"

"${e2e_compose[@]}" up -d --build --wait
"${e2e_compose[@]}" exec -T api alembic upgrade head
"${e2e_compose[@]}" exec -T scheduler python -m app.cli.run_teaching_scheduler --once
curl --fail --silent --show-error \
  "http://127.0.0.1:${E2E_HTTP_PORT:-18080}/healthz" >/dev/null
curl --fail --silent --show-error \
  "http://127.0.0.1:${E2E_HTTP_PORT:-18080}/api/v1/health/live" >/dev/null

# The domain launchers rebuild the fixed fixtures before every viewport/spec
# pair. Stop all other writers first, but keep the isolated PostgreSQL service.
"${e2e_compose[@]}" stop proxy web api scheduler
export E2E_PYTHON_BIN="$python_cmd"

bash scripts/run-core-e2e.sh
bash scripts/run-esports-assessments-e2e.sh
bash scripts/run-housing-room-status-e2e.sh
bash scripts/run-housing-occupancy-e2e.sh
bash scripts/run-housing-personal-portal-e2e.sh
bash scripts/run-housing-reporting-e2e.sh
bash scripts/run-self-service-registration-e2e.sh
bash scripts/run-team-management-e2e.sh
bash scripts/run-evaluation-page-e2e.sh
SCORE_REPO="${STAGE2_SCORE_REPO:-$repo_root/../.worktrees/game-score-system-evaluation-processing}" \
  bash scripts/run-stage2-local-acceptance.sh

backup_file=$(mktemp "${TMPDIR:-/tmp}/esports-erp-e2e-backup.XXXXXX")
DATABASE_URL="$TEST_DATABASE_URL" bash scripts/backup-db.sh "$backup_file"
DATABASE_URL="$TEST_DATABASE_URL" bash scripts/restore-db.sh \
  "$backup_file" erp_e2e_restore_test
