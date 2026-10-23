#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
if [[ -n ${E2E_PYTHON_BIN:-} ]]; then
  python_bin=$E2E_PYTHON_BIN
else
  python_bin="$repo_root/.venv/bin/python"
  if [[ ! -x "$python_bin" ]]; then
    git_common_dir=$(git -C "$repo_root" rev-parse --git-common-dir 2>/dev/null || true)
    if [[ -n "$git_common_dir" ]]; then
      if [[ "$git_common_dir" != /* ]]; then
        git_common_dir="$repo_root/$git_common_dir"
      fi
      shared_root=$(cd "$(dirname "$git_common_dir")" && pwd -P)
      if [[ -x "$shared_root/.venv/bin/python" ]]; then
        python_bin="$shared_root/.venv/bin/python"
      fi
    fi
  fi
fi
vite_bin=${E2E_VITE_BIN:-$repo_root/apps/web/node_modules/.bin/vite}
api_port=${E2E_API_PORT:-8007}
web_port=${E2E_WEB_PORT:-5182}
api_origin="http://127.0.0.1:${api_port}"
web_origin="http://127.0.0.1:${web_port}"

: "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"
if [[ ! -x "$python_bin" ]]; then
  echo "本地 Python 虚拟环境不可用；请设置 E2E_PYTHON_BIN" >&2
  exit 2
fi
cd "$repo_root/apps/api"
TEST_DATABASE_URL="$TEST_DATABASE_URL" "$python_bin" -m app.cli.e2e_database_safety

: "${FIELD_ENCRYPTION_MASTER_KEY:?FIELD_ENCRYPTION_MASTER_KEY 未设置}"
: "${FIELD_LOOKUP_KEY:?FIELD_LOOKUP_KEY 未设置}"
: "${SESSION_TOKEN_PEPPER:?SESSION_TOKEN_PEPPER 未设置}"
: "${EVALUATION_TOOL_SERVICE_TOKEN:?EVALUATION_TOOL_SERVICE_TOKEN 未设置}"
: "${EVALUATION_TOOL_WEB_SERVICE_TOKEN:?EVALUATION_TOOL_WEB_SERVICE_TOKEN 未设置}"
: "${EVALUATION_TOOL_WORKER_SERVICE_TOKEN:?EVALUATION_TOOL_WORKER_SERVICE_TOKEN 未设置}"
if [[ "$EVALUATION_TOOL_SERVICE_TOKEN" == "$EVALUATION_TOOL_WEB_SERVICE_TOKEN" || \
      "$EVALUATION_TOOL_SERVICE_TOKEN" == "$EVALUATION_TOOL_WORKER_SERVICE_TOKEN" || \
      "$EVALUATION_TOOL_WEB_SERVICE_TOKEN" == "$EVALUATION_TOOL_WORKER_SERVICE_TOKEN" ]]; then
  echo "v1、Web 与 Worker 服务密钥必须彼此不同" >&2
  exit 2
fi
if [[ ! -x "$vite_bin" ]]; then
  echo "本地 Vite 可执行文件不可用；请先安装 Web 依赖" >&2
  exit 2
fi

ensure_port_free() {
  local port=$1
  if ! TEST_DATABASE_URL="$TEST_DATABASE_URL" \
    "$python_bin" -m app.cli.e2e_port_safety "$port"; then
    echo "本地端口已被其他进程占用: $port" >&2
    return 1
  fi
}

ensure_port_free "$api_port"
ensure_port_free "$web_port"

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/camp-surveys-e2e.XXXXXX")
pids=()

terminate_children() {
  local any_alive
  local pid
  if (( ${#pids[@]} == 0 )); then
    return 0
  fi
  for pid in "${pids[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  for _ in {1..20}; do
    any_alive=0
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" >/dev/null 2>&1; then
        any_alive=1
      fi
    done
    (( any_alive == 0 )) && break
    sleep 0.1
  done
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
  done
  wait "${pids[@]}" >/dev/null 2>&1 || true
}

cleanup() {
  local status=$?
  if (( status != 0 )); then
    for log in "$tmp_dir"/*.log; do
      [[ -f "$log" ]] || continue
      echo "服务日志: $(basename "$log")" >&2
      tail -80 "$log" >&2 || true
    done
  fi
  terminate_children
  rm -rf "$tmp_dir"
  return "$status"
}
trap cleanup EXIT

wait_http() {
  local url=$1
  local pid=$2
  local service=$3
  local log_file=$4
  local bind_marker=$5
  for _ in {1..120}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      echo "$service 进程在就绪前退出" >&2
      return 1
    fi
    if grep --fixed-strings --quiet -- "$bind_marker" "$log_file" && \
      curl --fail --silent --show-error "$url" >/dev/null 2>&1 && \
      kill -0 "$pid" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "$service 未在限定时间内就绪: $url" >&2
  return 1
}

run_erp_cli() {
  env \
    APP_ENV=test \
    DATABASE_URL="$TEST_DATABASE_URL" \
    SESSION_COOKIE_SECURE=false \
    FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
    FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" \
    SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
    EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
    EVALUATION_TOOL_WEB_SERVICE_TOKEN="$EVALUATION_TOOL_WEB_SERVICE_TOKEN" \
    EVALUATION_TOOL_WORKER_SERVICE_TOKEN="$EVALUATION_TOOL_WORKER_SERVICE_TOKEN" \
    "$python_bin" "$@"
}

cd "$repo_root/apps/api"
run_erp_cli -m alembic upgrade head

cd "$repo_root"
env \
  APP_ENV=test \
  DATABASE_URL="$TEST_DATABASE_URL" \
  SESSION_COOKIE_SECURE=false \
  FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
  FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" \
  SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
  EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
  EVALUATION_TOOL_WEB_SERVICE_TOKEN="$EVALUATION_TOOL_WEB_SERVICE_TOKEN" \
  EVALUATION_TOOL_WORKER_SERVICE_TOKEN="$EVALUATION_TOOL_WORKER_SERVICE_TOKEN" \
  NO_COLOR=1 FORCE_COLOR=0 \
  "$python_bin" -m uvicorn app.main:app --app-dir apps/api \
    --host 127.0.0.1 --port "$api_port" --no-use-colors \
    >"$tmp_dir/api.log" 2>&1 &
api_pid=$!
pids+=("$api_pid")

cd "$repo_root/apps/web"
env \
  -u FIELD_ENCRYPTION_MASTER_KEY \
  -u FIELD_LOOKUP_KEY \
  -u SESSION_TOKEN_PEPPER \
  -u EVALUATION_TOOL_SERVICE_TOKEN \
  -u EVALUATION_TOOL_WEB_SERVICE_TOKEN \
  -u EVALUATION_TOOL_WORKER_SERVICE_TOKEN \
  ERP_API_PROXY_TARGET="$api_origin" \
  NO_COLOR=1 FORCE_COLOR=0 \
  "$vite_bin" --host 127.0.0.1 --port "$web_port" --strictPort --clearScreen false \
    >"$tmp_dir/web.log" 2>&1 &
web_pid=$!
pids+=("$web_pid")

wait_http "$api_origin/api/v1/health/live" "$api_pid" "API" "$tmp_dir/api.log" \
  "Uvicorn running on http://127.0.0.1:${api_port} ("
wait_http "$web_origin/login" "$web_pid" "Web" "$tmp_dir/web.log" \
  "Local:   http://127.0.0.1:${web_port}/"

projects=(desktop-1440 desktop-1024 tablet-768 mobile-360)
for project in "${projects[@]}"; do
  cd "$repo_root/apps/api"
  run_erp_cli -m app.cli.seed_e2e
  run_erp_cli -m app.cli.seed_camp_surveys_e2e
  cd "$repo_root"
  env \
    -u FIELD_ENCRYPTION_MASTER_KEY \
    -u FIELD_LOOKUP_KEY \
    -u SESSION_TOKEN_PEPPER \
    -u EVALUATION_TOOL_SERVICE_TOKEN \
    -u EVALUATION_TOOL_WEB_SERVICE_TOKEN \
    -u EVALUATION_TOOL_WORKER_SERVICE_TOKEN \
    E2E_PYTHON_BIN="$python_bin" \
    E2E_API_URL="$api_origin" \
    E2E_BASE_URL="$web_origin" \
    npm --prefix apps/web run test:e2e -- camp-evaluation-surveys.spec.ts --project="$project"
done
