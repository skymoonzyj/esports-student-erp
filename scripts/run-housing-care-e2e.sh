#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
python_bin=${E2E_PYTHON_BIN:-$repo_root/.venv/bin/python}
if [[ ! -x "$python_bin" && -z ${E2E_PYTHON_BIN:-} ]]; then
  common=$(git -C "$repo_root" rev-parse --git-common-dir)
  [[ "$common" == /* ]] || common="$repo_root/$common"
  shared_root=$(cd "$(dirname "$common")" && pwd -P)
  python_bin="$shared_root/.venv/bin/python"
fi
vite_bin=${E2E_VITE_BIN:-$repo_root/apps/web/node_modules/.bin/vite}
api_port=${E2E_API_PORT:-18144}
web_port=${E2E_WEB_PORT:-15144}
api_origin="http://127.0.0.1:${api_port}"
web_origin="http://127.0.0.1:${web_port}"
: "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"
[[ -x "$python_bin" ]] || { echo "请设置可用的 E2E_PYTHON_BIN" >&2; exit 2; }
cd "$repo_root/apps/api"
"$python_bin" -m app.cli.e2e_database_safety
export E2E_HOUSING_CARE_RUNTIME_LEDGER=${E2E_HOUSING_CARE_RUNTIME_LEDGER:-$repo_root/.superpowers/sdd/2026-10-23-housing-cleaning-export-photos/runtime.json}
APP_ENV=test DATABASE_URL="$TEST_DATABASE_URL" \
  "$python_bin" -m app.cli.seed_housing_care_e2e --check-database
: "${FIELD_ENCRYPTION_MASTER_KEY:?FIELD_ENCRYPTION_MASTER_KEY 未设置}"
: "${FIELD_LOOKUP_KEY:?FIELD_LOOKUP_KEY 未设置}"
: "${SESSION_TOKEN_PEPPER:?SESSION_TOKEN_PEPPER 未设置}"
: "${EVALUATION_TOOL_SERVICE_TOKEN:?EVALUATION_TOOL_SERVICE_TOKEN 未设置}"
[[ -x "$vite_bin" ]] || { echo "本地 Vite 不可用" >&2; exit 2; }
export APP_ENV=test DATABASE_URL="$TEST_DATABASE_URL" SESSION_COOKIE_SECURE=false
export E2E_PYTHON_BIN="$python_bin"
export FIELD_ENCRYPTION_MASTER_KEY FIELD_LOOKUP_KEY SESSION_TOKEN_PEPPER EVALUATION_TOOL_SERVICE_TOKEN

umask 077
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/housing-care-e2e.XXXXXX")
pids=()
cleanup() {
  local status=$?
  # Credentials/cookies/downloads and service logs stay private and are removed.
  # Never print request bodies or restored values on an assertion failure.
  for pid in "${pids[@]:-}"; do kill "$pid" >/dev/null 2>&1 || true; done
  for _ in {1..40}; do
    alive=false
    for pid in "${pids[@]:-}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then alive=true; fi
    done
    [[ "$alive" == true ]] || break
    sleep 0.05
  done
  for pid in "${pids[@]:-}"; do kill -KILL "$pid" >/dev/null 2>&1 || true; done
  wait "${pids[@]:-}" >/dev/null 2>&1 || true
  rm -rf "$tmp_dir"
  return "$status"
}
trap cleanup EXIT
wait_http() {
  local url=$1 pid=$2 service=$3 log_file=$4 bind_marker=$5
  for _ in {1..120}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "$service 进程在就绪前退出" >&2; return 1
    fi
    if grep --fixed-strings --quiet -- "$bind_marker" "$log_file" && \
       kill -0 "$pid" >/dev/null 2>&1 && \
       curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      kill -0 "$pid" >/dev/null 2>&1 && return 0
      echo "$service 进程在就绪前退出" >&2; return 1
    fi
    sleep 0.25
  done
  echo "服务未就绪: $url" >&2; return 1
}
ensure_port_free() {
  if ! "$python_bin" -m app.cli.e2e_port_safety "$1"; then
    echo "本地端口已被其他进程占用: $1" >&2; return 1
  fi
}
ensure_port_free "$api_port"
ensure_port_free "$web_port"
"$python_bin" -m alembic upgrade head
cd "$repo_root"
NO_COLOR=1 FORCE_COLOR=0 \
"$python_bin" -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port "$api_port" \
  --no-use-colors >"$tmp_dir/api.log" 2>&1 &
api_pid=$!
pids+=("$api_pid")
cd "$repo_root/apps/web"
NO_COLOR=1 FORCE_COLOR=0 ERP_API_PROXY_TARGET="$api_origin" \
"$vite_bin" --host 127.0.0.1 --port "$web_port" --strictPort --clearScreen false \
  >"$tmp_dir/web.log" 2>&1 &
web_pid=$!
pids+=("$web_pid")
wait_http "$api_origin/api/v1/health/live" "$api_pid" API "$tmp_dir/api.log" \
  "Uvicorn running on http://127.0.0.1:${api_port} ("
wait_http "$web_origin/login" "$web_pid" Web "$tmp_dir/web.log" \
  "Local:   http://127.0.0.1:${web_port}/"
for project in desktop-1440 desktop-1024 tablet-768 mobile-360 touch-mobile-390; do
  cd "$repo_root/apps/api"
  "$python_bin" -m app.cli.seed_housing_care_e2e
  cd "$repo_root"
  E2E_BASE_URL="$web_origin" PLAYWRIGHT_OUTPUT_DIR="$tmp_dir/results" \
    PLAYWRIGHT_HTML_REPORT_DIR="$tmp_dir/report" \
    npm --prefix apps/web run test:e2e -- housing-cleaning-photos-export.spec.ts --project="$project"
done
