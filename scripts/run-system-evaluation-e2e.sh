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
default_score_repo=$(dirname "$repo_root")/game-score-system-evaluation-processing
score_candidates=(
  "$repo_root/.worktrees/game-score-system-evaluation-processing"
  "$(dirname "$repo_root")/.worktrees/game-score-system-evaluation-processing"
  "$(cd "$(dirname "$(dirname "$repo_root")")" && pwd -P)/game-score-tool"
)
if [[ ! -f "$default_score_repo/worker.py" ]]; then
  for score_candidate in "${score_candidates[@]}"; do
    if [[ -f "$score_candidate/worker.py" ]]; then
      default_score_repo=$score_candidate
      break
    fi
  done
fi
score_repo=${SCORE_REPO:-$default_score_repo}
score_python=${E2E_SCORE_PYTHON_BIN:-/Users/skymoonzyj/Documents/game-score-tool/.venv/bin/python}
api_port=${E2E_API_PORT:-8005}
web_port=${E2E_WEB_PORT:-5180}
score_port=${E2E_SCORE_PORT:-8011}
api_origin="http://127.0.0.1:${api_port}"
web_origin="http://127.0.0.1:${web_port}"
score_proxy_origin="http://127.0.0.1:${score_port}"
score_browser_origin="http://localhost:${score_port}"

: "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"
if [[ ! -x "$python_bin" ]]; then
  echo "本地 ERP Python 虚拟环境不可用；请设置 E2E_PYTHON_BIN" >&2
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
if [[ ! -x "$score_python" ]]; then
  echo "评分工具 Python 虚拟环境不可用；请设置 E2E_SCORE_PYTHON_BIN" >&2
  exit 2
fi
if [[ ! -f "$score_repo/worker.py" ]]; then
  echo "评分工具工作树不可用；请设置 SCORE_REPO" >&2
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
ensure_port_free "$score_port"

cd "$repo_root"
bash scripts/check-evaluation-v2-contract.sh "$repo_root" "$score_repo"

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/system-evaluation-e2e.XXXXXX")
media_root="$tmp_dir/media"
quarantine_root="$tmp_dir/quarantine"
worker_temp_root="$tmp_dir/worker"
mkdir -p "$media_root" "$quarantine_root" "$worker_temp_root"
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
      if [[ $(basename "$log") == api.log ]]; then
        grep --invert-match --fixed-strings -- "/worker/claims " "$log" \
          | tail -80 >&2 || true
      else
        tail -80 "$log" >&2 || true
      fi
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
    EVALUATION_MEDIA_ROOT="$media_root" \
    EVALUATION_MEDIA_QUARANTINE_ROOT="$quarantine_root" \
    EVALUATION_MEDIA_STORE_ID="$store_identity" \
    "$python_bin" "$@"
}

cd "$repo_root/apps/api"
store_identity=$("$python_bin" -c 'from uuid import uuid4; print(uuid4())')
run_erp_cli -m app.evaluations.media_store --initialize-store-identity
run_erp_cli -m alembic upgrade head

cd "$repo_root"
env \
  -u ERP_SERVICE_TOKEN \
  -u ERP_WEB_SERVICE_TOKEN \
  -u ERP_WORKER_SERVICE_TOKEN \
  APP_ENV=test \
  DATABASE_URL="$TEST_DATABASE_URL" \
  SESSION_COOKIE_SECURE=false \
  FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
  FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" \
  SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
  EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
  EVALUATION_TOOL_WEB_SERVICE_TOKEN="$EVALUATION_TOOL_WEB_SERVICE_TOKEN" \
  EVALUATION_TOOL_WORKER_SERVICE_TOKEN="$EVALUATION_TOOL_WORKER_SERVICE_TOKEN" \
  EVALUATION_MEDIA_ROOT="$media_root" \
  EVALUATION_MEDIA_QUARANTINE_ROOT="$quarantine_root" \
  EVALUATION_MEDIA_STORE_ID="$store_identity" \
  ERP_WEB_BASE_URL="$web_origin" \
  EVALUATION_TOOL_STANDALONE_BASE_URL="$score_browser_origin" \
  EVALUATION_TOOL_EMBEDDED_PATH=/evaluation-tool \
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
  -u ERP_SERVICE_TOKEN \
  -u ERP_WEB_SERVICE_TOKEN \
  -u ERP_WORKER_SERVICE_TOKEN \
  ERP_API_PROXY_TARGET="$api_origin" \
  EVALUATION_TOOL_PROXY_TARGET="$score_proxy_origin" \
  NO_COLOR=1 FORCE_COLOR=0 \
  "$vite_bin" --host 127.0.0.1 --port "$web_port" --strictPort --clearScreen false \
  >"$tmp_dir/web.log" 2>&1 &
web_pid=$!
pids+=("$web_pid")

cd "$score_repo"
env \
  -u FIELD_ENCRYPTION_MASTER_KEY \
  -u FIELD_LOOKUP_KEY \
  -u SESSION_TOKEN_PEPPER \
  -u EVALUATION_TOOL_SERVICE_TOKEN \
  -u EVALUATION_TOOL_WEB_SERVICE_TOKEN \
  -u EVALUATION_TOOL_WORKER_SERVICE_TOKEN \
  -u ERP_WORKER_SERVICE_TOKEN \
  ERP_API_BASE_URL="$api_origin/api/v1" \
  ERP_WEB_BASE_URL="$web_origin" \
  ERP_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
  ERP_WEB_SERVICE_TOKEN="$EVALUATION_TOOL_WEB_SERVICE_TOKEN" \
  SCORE_TOOL_SESSION_SECRET="${E2E_SCORE_SESSION_SECRET:-score-tool-session-secret-system-e2e}" \
  SCORE_TOOL_ENV=test \
  ERP_EMBEDDED_PATH=/evaluation-tool \
  ERP_ALLOWED_PARENT_ORIGIN="$web_origin" \
  SCORE_TOOL_COOKIE_SECURE=false \
  NO_COLOR=1 FORCE_COLOR=0 \
  "$score_python" -m uvicorn main:app --host 127.0.0.1 --port "$score_port" \
    --no-use-colors >"$tmp_dir/score-web.log" 2>&1 &
score_web_pid=$!
pids+=("$score_web_pid")

wait_http "$api_origin/api/v1/health/live" "$api_pid" "API" "$tmp_dir/api.log" \
  "Uvicorn running on http://127.0.0.1:${api_port} ("
wait_http "$web_origin/login" "$web_pid" "Web" "$tmp_dir/web.log" \
  "Local:   http://127.0.0.1:${web_port}/"
wait_http "$score_proxy_origin/health/live" "$score_web_pid" "评分 Web" \
  "$tmp_dir/score-web.log" "Uvicorn running on http://127.0.0.1:${score_port} ("

worker_environment=(
  -u FIELD_ENCRYPTION_MASTER_KEY
  -u FIELD_LOOKUP_KEY
  -u SESSION_TOKEN_PEPPER
  -u EVALUATION_TOOL_SERVICE_TOKEN
  -u EVALUATION_TOOL_WEB_SERVICE_TOKEN
  -u EVALUATION_TOOL_WORKER_SERVICE_TOKEN
  -u ERP_SERVICE_TOKEN
  -u ERP_WEB_SERVICE_TOKEN
  ERP_API_BASE_URL="$api_origin/api/v1"
  ERP_WORKER_SERVICE_TOKEN="$EVALUATION_TOOL_WORKER_SERVICE_TOKEN"
  SCORE_WORKER_IDENTITY=score-worker-system-e2e
  SCORE_WORKER_POLL_INTERVAL_SECONDS=0.1
  SCORE_WORKER_TEMP_ROOT="$worker_temp_root"
  SCORE_TOOL_ENV=test
  NO_COLOR=1
  FORCE_COLOR=0
)

env "${worker_environment[@]}" "$score_python" "$score_repo/worker.py" \
  >"$tmp_dir/worker.log" 2>&1 &
worker_pid=$!
pids+=("$worker_pid")

worker_check_log="$tmp_dir/worker-check.log"
if ! env "${worker_environment[@]}" "$score_python" "$score_repo/worker.py" \
  --check >"$worker_check_log" 2>&1; then
  echo "Worker capabilities 检查失败" >&2
  exit 1
fi
if [[ ! -f "$worker_check_log" || $(<"$worker_check_log") != "score-worker ready" ]]; then
  echo "Worker 未输出本次启动所需的精确 ready 标记" >&2
  exit 1
fi
if ! kill -0 "$worker_pid" >/dev/null 2>&1; then
  echo "Worker 进程在就绪前退出" >&2
  exit 1
fi

projects=(desktop-1440 desktop-1024 tablet-768 mobile-360)
specs=(system-evaluation-processing.spec.ts system-evaluation-privacy.spec.ts)
for project in "${projects[@]}"; do
  for spec in "${specs[@]}"; do
    cd "$repo_root/apps/api"
    run_erp_cli -m app.cli.seed_e2e
    run_erp_cli -m app.cli.seed_system_evaluation_e2e
    if ! kill -0 "$worker_pid" >/dev/null 2>&1; then
      echo "Worker 进程在浏览器测试前退出" >&2
      exit 1
    fi
    cd "$repo_root"
    env \
      -u FIELD_ENCRYPTION_MASTER_KEY \
      -u FIELD_LOOKUP_KEY \
      -u SESSION_TOKEN_PEPPER \
      -u EVALUATION_TOOL_SERVICE_TOKEN \
      -u EVALUATION_TOOL_WEB_SERVICE_TOKEN \
      -u EVALUATION_TOOL_WORKER_SERVICE_TOKEN \
      -u ERP_SERVICE_TOKEN \
      -u ERP_WEB_SERVICE_TOKEN \
      -u ERP_WORKER_SERVICE_TOKEN \
      E2E_PYTHON_BIN="$python_bin" \
      E2E_API_URL="$api_origin" \
      E2E_BASE_URL="$web_origin" \
      E2E_SCORE_ORIGIN="$score_browser_origin" \
      E2E_SYSTEM_EVALUATION_AUTOMATIC_PNG="$media_root/.system-evaluation-e2e/automatic.png" \
      E2E_SYSTEM_EVALUATION_MANUAL_PNG="$quarantine_root/.system-evaluation-e2e/manual.png" \
      npm --prefix apps/web run test:e2e -- "$spec" --project="$project"
  done
done
