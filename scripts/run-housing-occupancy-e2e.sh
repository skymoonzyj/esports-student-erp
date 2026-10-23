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
api_port=${E2E_API_PORT:-8002}
web_port=${E2E_WEB_PORT:-5177}
api_origin="http://127.0.0.1:${api_port}"
web_origin="http://127.0.0.1:${web_port}"

: "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"
if [[ ! -x "$python_bin" ]]; then
  echo "本地 Python 虚拟环境不可用；请设置 E2E_PYTHON_BIN" >&2
  exit 2
fi
cd "$repo_root/apps/api"
"$python_bin" -m app.cli.e2e_database_safety

: "${FIELD_ENCRYPTION_MASTER_KEY:?FIELD_ENCRYPTION_MASTER_KEY 未设置}"
: "${FIELD_LOOKUP_KEY:?FIELD_LOOKUP_KEY 未设置}"
: "${SESSION_TOKEN_PEPPER:?SESSION_TOKEN_PEPPER 未设置}"
: "${EVALUATION_TOOL_SERVICE_TOKEN:?EVALUATION_TOOL_SERVICE_TOKEN 未设置}"
if [[ ! -x "$vite_bin" ]]; then
  echo "本地 Vite 可执行文件不可用；请先安装 Web 依赖" >&2
  exit 2
fi

export APP_ENV=test
export DATABASE_URL="$TEST_DATABASE_URL"
export SESSION_COOKIE_SECURE=false
export FIELD_ENCRYPTION_MASTER_KEY
export FIELD_LOOKUP_KEY
export SESSION_TOKEN_PEPPER
export EVALUATION_TOOL_SERVICE_TOKEN

tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/housing-occupancy-e2e.XXXXXX")
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

"$python_bin" -m alembic upgrade head

cd "$repo_root"
NO_COLOR=1 FORCE_COLOR=0 \
"$python_bin" -m uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port "$api_port" \
  --no-use-colors \
  >"$tmp_dir/api.log" 2>&1 &
api_pid=$!
pids+=("$api_pid")

cd "$repo_root/apps/web"
NO_COLOR=1 FORCE_COLOR=0 ERP_API_PROXY_TARGET="$api_origin" \
"$vite_bin" --host 127.0.0.1 --port "$web_port" --strictPort --clearScreen false \
  >"$tmp_dir/web.log" 2>&1 &
web_pid=$!
pids+=("$web_pid")

wait_http "$api_origin/api/v1/health/live" "$api_pid" "API" "$tmp_dir/api.log" \
  "Uvicorn running on http://127.0.0.1:${api_port} ("
wait_http "$web_origin/login" "$web_pid" "Web" "$tmp_dir/web.log" \
  "Local:   http://127.0.0.1:${web_port}/"

for project in desktop-1440 desktop-1024 tablet-768 mobile-360; do
  for spec in housing-occupancy-lifecycle.spec.ts housing-occupancy-revocation.spec.ts profile-housing-edit.spec.ts person-housing.spec.ts housing-history-void.spec.ts; do
    cd "$repo_root/apps/api"
    if [[ "$spec" == housing-history-void.spec.ts ]]; then
      "$python_bin" -m app.cli.seed_e2e --housing-history-void
    else
      "$python_bin" -m app.cli.seed_e2e
    fi
    cd "$repo_root"
    E2E_PYTHON_BIN="$python_bin" E2E_API_URL="$api_origin" E2E_BASE_URL="$web_origin" \
      npm --prefix apps/web run test:e2e -- "$spec" --project="$project"
  done
done
