#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"
export PYTHONPATH="$repo_root/apps/api${PYTHONPATH:+:$PYTHONPATH}"

if [[ -x .venv/bin/python ]]; then
  python_cmd=.venv/bin/python
  ruff_cmd=.venv/bin/ruff
  mypy_cmd=.venv/bin/mypy
else
  python_cmd=python
  ruff_cmd=ruff
  mypy_cmd=mypy
fi

if docker compose version >/dev/null 2>&1; then
  compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose=(docker-compose)
else
  echo "缺少 Docker Compose" >&2
  exit 3
fi

e2e_compose=("${compose[@]}" -p esports-erp-e2e -f compose.yaml -f compose.e2e.yaml)
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
"$mypy_cmd" apps/api/app
npm --prefix apps/web test
npm --prefix apps/web run build
npm --prefix apps/web run lint

"${e2e_compose[@]}" up -d --build --wait
"${e2e_compose[@]}" exec -T api alembic upgrade head
"${e2e_compose[@]}" exec -T scheduler python -m app.cli.run_teaching_scheduler --once
"${e2e_compose[@]}" exec -T api python -m app.cli.seed_e2e

export E2E_BASE_URL="http://127.0.0.1:${E2E_HTTP_PORT:-18080}"
npm --prefix apps/web run test:e2e

backup_file=$(mktemp "${TMPDIR:-/tmp}/esports-erp-e2e-backup.XXXXXX")
DATABASE_URL="$TEST_DATABASE_URL" bash scripts/backup-db.sh "$backup_file"
DATABASE_URL="$TEST_DATABASE_URL" bash scripts/restore-db.sh \
  "$backup_file" erp_e2e_restore_test
