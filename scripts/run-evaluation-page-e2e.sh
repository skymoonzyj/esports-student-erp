#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=${E2E_PYTHON_BIN:-$repo_root/.venv/bin/python}
score_repo=${SCORE_REPO:-/Users/skymoonzyj/Documents/game-score-tool}
vite_bin=${E2E_VITE_BIN:-$repo_root/apps/web/node_modules/.bin/vite}
score_bin=${E2E_SCORE_BIN:-$score_repo/.venv/bin/uvicorn}
api_port=${E2E_API_PORT:-8000}
web_port=${E2E_WEB_PORT:-5175}
score_port=${E2E_SCORE_PORT:-8010}
api_origin="http://127.0.0.1:${api_port}"
web_origin="http://127.0.0.1:${web_port}"
score_proxy_origin="http://127.0.0.1:${score_port}"
score_browser_origin="http://localhost:${score_port}"
: "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"
if [[ ! -x "$python_bin" ]]; then
  echo "本地 Python 虚拟环境不可用；请设置 E2E_PYTHON_BIN" >&2
  exit 2
fi
cd "$repo_root/apps/api"
"$python_bin" -m app.cli.e2e_database_safety

: "${FIELD_ENCRYPTION_MASTER_KEY:?}"
: "${FIELD_LOOKUP_KEY:?}"
: "${SESSION_TOKEN_PEPPER:?}"
: "${EVALUATION_TOOL_SERVICE_TOKEN:?}"
if [[ ! -x "$vite_bin" ]]; then
  echo "本地 Vite 可执行文件不可用；请先安装 Web 依赖" >&2
  exit 2
fi
if [[ ! -x "$score_bin" ]]; then
  echo "评分工具虚拟环境不可用；请设置 E2E_SCORE_BIN" >&2
  exit 2
fi

export APP_ENV=test
export DATABASE_URL="$TEST_DATABASE_URL"
export SESSION_COOKIE_SECURE=false
export FIELD_ENCRYPTION_MASTER_KEY
export FIELD_LOOKUP_KEY
export SESSION_TOKEN_PEPPER
export EVALUATION_TOOL_SERVICE_TOKEN

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/evaluation-shell-e2e.XXXXXX")
pids=()
cleanup() {
  local status=$?
  if (( status != 0 )); then
    for log in "$tmp_dir"/*.log; do
      [[ -f "$log" ]] || continue
      echo "服务日志: $(basename "$log")" >&2
      tail -80 "$log" >&2 || true
    done
  fi
  for pid in "${pids[@]:-}"; do kill "$pid" >/dev/null 2>&1 || true; done
  wait "${pids[@]:-}" >/dev/null 2>&1 || true
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
      kill -0 "$pid" >/dev/null 2>&1 && \
      curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      if kill -0 "$pid" >/dev/null 2>&1; then return 0; fi
      echo "$service 进程在就绪前退出" >&2
      return 1
    fi
    sleep 0.25
  done
  echo "服务未就绪: $url" >&2
  return 1
}

ensure_port_free() {
  local port=$1
  if ! "$python_bin" -m app.cli.e2e_port_safety "$port"; then
    echo "本地端口已被其他进程占用: $port" >&2
    return 1
  fi
}

ensure_port_free "$api_port"
ensure_port_free "$web_port"
ensure_port_free "$score_port"

"$python_bin" -m alembic upgrade head
"$python_bin" -m app.cli.seed_e2e

cd "$repo_root"
APP_ENV=test DATABASE_URL="$TEST_DATABASE_URL" SESSION_COOKIE_SECURE=false \
ERP_WEB_BASE_URL="$web_origin" \
EVALUATION_TOOL_STANDALONE_BASE_URL="$score_browser_origin" \
EVALUATION_TOOL_EMBEDDED_PATH=/evaluation-tool \
EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
FIELD_ENCRYPTION_MASTER_KEY="$FIELD_ENCRYPTION_MASTER_KEY" \
FIELD_LOOKUP_KEY="$FIELD_LOOKUP_KEY" SESSION_TOKEN_PEPPER="$SESSION_TOKEN_PEPPER" \
NO_COLOR=1 FORCE_COLOR=0 \
"$python_bin" -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port "$api_port" \
  --no-use-colors \
  >"$tmp_dir/api.log" 2>&1 &
api_pid=$!
pids+=("$api_pid")

cd "$repo_root/apps/web"
NO_COLOR=1 FORCE_COLOR=0 ERP_API_PROXY_TARGET="$api_origin" \
EVALUATION_TOOL_PROXY_TARGET="$score_proxy_origin" \
"$vite_bin" --host 127.0.0.1 --port "$web_port" --strictPort --clearScreen false \
  >"$tmp_dir/web.log" 2>&1 &
web_pid=$!
pids+=("$web_pid")

cd "$score_repo"
ERP_API_BASE_URL="$api_origin/api/v1" \
ERP_WEB_BASE_URL="$web_origin" \
ERP_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
SCORE_TOOL_SESSION_SECRET=score-tool-session-secret-2026-change-me \
SCORE_TOOL_ENV=test \
ERP_EMBEDDED_PATH=/evaluation-tool \
ERP_ALLOWED_PARENT_ORIGIN="$web_origin" \
SCORE_TOOL_COOKIE_SECURE=false \
NO_COLOR=1 FORCE_COLOR=0 \
"$score_bin" main:app --host 127.0.0.1 --port "$score_port" \
  --no-use-colors \
  >"$tmp_dir/score.log" 2>&1 &
score_pid=$!
pids+=("$score_pid")

wait_http "$api_origin/api/v1/health/live" "$api_pid" "API" "$tmp_dir/api.log" \
  "Uvicorn running on http://127.0.0.1:${api_port} ("
wait_http "$web_origin/login" "$web_pid" "Web" "$tmp_dir/web.log" \
  "Local:   http://127.0.0.1:${web_port}/"
wait_http "$score_proxy_origin/health/live" "$score_pid" "评分工具" "$tmp_dir/score.log" \
  "Uvicorn running on http://127.0.0.1:${score_port} ("

cd "$repo_root"
cmp apps/api/tests/contracts/evaluation_integration_v1.json \
  "$score_repo/tests/contracts/evaluation_integration_v1.json"
E2E_BASE_URL="$web_origin" E2E_SCORE_ORIGIN="$score_browser_origin" \
npm --prefix apps/web run test:e2e -- \
  evaluation-page-integration.spec.ts \
  evaluation-data-media-foundation.spec.ts
