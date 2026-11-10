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
api_port=${E2E_API_PORT:-8014}
web_port=${E2E_WEB_PORT:-5188}
api_origin="http://127.0.0.1:${api_port}"
web_origin="http://127.0.0.1:${web_port}"
talent_artifact_dir=${TALENT_E2E_ARTIFACT_DIR:-$repo_root/.superpowers/sdd/2026-09-20-talent-tests/task-16-artifacts}
esports_artifact_dir=${ESPORTS_E2E_ARTIFACT_DIR:-$repo_root/.superpowers/sdd/2026-09-21-talent-assessment-2/task-18-artifacts}
reactionx_artifact_dir=${REACTIONX_E2E_ARTIFACT_DIR:-$repo_root/.superpowers/sdd/2026-10-31-reactionx-erp-integration/task-6-artifacts}
e2e_mode=${E2E_CORE_MODE:-core}
invited_artifact_dir=${INVITED_HOUSING_E2E_ARTIFACT_DIR:-$repo_root/.superpowers/sdd/2026-10-10-invited-housing-visibility/task-4-artifacts/real}
if [[ "$e2e_mode" == "invited-housing" ]]; then
  # Do not put this plan's artifacts in the older talent/esports task folders.
  talent_artifact_dir="$invited_artifact_dir"
  esports_artifact_dir="$invited_artifact_dir"
fi

case "$e2e_mode" in
  core|talent|esports|reactionx|invited-housing|all)
    ;;
  *)
    echo "E2E_CORE_MODE 只允许 core、talent、esports、reactionx、invited-housing 或 all" >&2
    exit 2
    ;;
esac

if [[ ${E2E_API_URL:-$api_origin} != "$api_origin" || \
      ${E2E_BASE_URL:-$web_origin} != "$web_origin" ]]; then
  echo "E2E origin 必须匹配当前 loopback 端口" >&2
  exit 2
fi

: "${TEST_DATABASE_URL:?TEST_DATABASE_URL 必须指向隔离测试库}"
: "${FIELD_ENCRYPTION_MASTER_KEY:?FIELD_ENCRYPTION_MASTER_KEY 未设置}"
: "${FIELD_LOOKUP_KEY:?FIELD_LOOKUP_KEY 未设置}"
: "${SESSION_TOKEN_PEPPER:?SESSION_TOKEN_PEPPER 未设置}"
: "${EVALUATION_TOOL_SERVICE_TOKEN:?EVALUATION_TOOL_SERVICE_TOKEN 未设置}"
: "${EVALUATION_TOOL_WEB_SERVICE_TOKEN:?EVALUATION_TOOL_WEB_SERVICE_TOKEN 未设置}"
: "${EVALUATION_TOOL_WORKER_SERVICE_TOKEN:?EVALUATION_TOOL_WORKER_SERVICE_TOKEN 未设置}"
if [[ ! -x "$python_bin" ]]; then
  echo "本地 Python 虚拟环境不可用；请设置 E2E_PYTHON_BIN" >&2
  exit 2
fi
cd "$repo_root/apps/api"
TEST_DATABASE_URL="$TEST_DATABASE_URL" "$python_bin" -m app.cli.e2e_database_safety

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

umask 077
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/core-e2e.XXXXXX")
pids=()
talent_log_suppression=0
esports_log_suppression=0
reactionx_log_suppression=0
if [[ "$e2e_mode" == reactionx || "$e2e_mode" == all ]]; then
  reactionx_log_suppression=1
fi

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

redact_log() {
  PYTHONPATH="$repo_root/apps/api" \
    "$python_bin" -m app.cli.redact_registration_e2e_output
}

cleanup() {
  local status=$?
  if (( status != 0 )); then
    if (( reactionx_log_suppression == 1 )); then
      echo "reactionx-sensitive-acceptance-failure: 原始服务与浏览器日志已隔离并删除" >&2
    elif (( esports_log_suppression == 1 )); then
      echo "esports-sensitive-browser-failure: 原始服务与浏览器日志已隔离并删除" >&2
    elif (( talent_log_suppression == 1 )); then
      echo "Talent E2E 失败；原始服务与浏览器日志已隔离并删除" >&2
    else
      for log in "$tmp_dir"/*.log; do
        [[ -f "$log" ]] || continue
        echo "服务日志: $(basename "$log")" >&2
        tail -80 "$log" | redact_log >&2 || true
      done
    fi
  fi
  terminate_children 2>/dev/null
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
    -u REACTIONX_SYNTHETIC_FORMAL_ENABLED_FILE \
    REACTIONX_SYNTHETIC_FORMAL_ENABLED=false \
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

start_api() {
  local synthetic=$1
  cd "$repo_root"
  env \
  -u REACTIONX_SYNTHETIC_FORMAL_ENABLED_FILE \
  REACTIONX_SYNTHETIC_FORMAL_ENABLED="$synthetic" \
  ERP_WEB_BASE_URL="$web_origin" \
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
}

stop_api() {
  local pid
  local -a remaining=()
  kill "$api_pid" >/dev/null 2>&1 || true
  for _ in {1..40}; do
    kill -0 "$api_pid" >/dev/null 2>&1 || break
    sleep 0.1
  done
  if kill -0 "$api_pid" >/dev/null 2>&1; then
    kill -KILL "$api_pid" >/dev/null 2>&1 || true
  fi
  wait "$api_pid" >/dev/null 2>&1 || true
  for pid in "${pids[@]}"; do
    [[ "$pid" == "$api_pid" ]] || remaining+=("$pid")
  done
  pids=("${remaining[@]}")
}

start_api false

cd "$repo_root/apps/web"
env \
  -u REACTIONX_SYNTHETIC_FORMAL_ENABLED \
  -u REACTIONX_SYNTHETIC_FORMAL_ENABLED_FILE \
  -u TEST_DATABASE_URL \
  -u DATABASE_URL \
  -u FIELD_ENCRYPTION_MASTER_KEY \
  -u FIELD_LOOKUP_KEY \
  -u REGISTRATION_VERIFICATION_PEPPER \
  -u REGISTRATION_SESSION_TOKEN_PEPPER \
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

run_playwright() {
  local spec=$1
  local project=$2
  local log_file="$tmp_dir/playwright-${project}-${spec%.spec.ts}.log"
  local report_file="$tmp_dir/playwright-${project}-${spec%.spec.ts}.json"
  local summary_file="$talent_artifact_dir/${project}-${spec%.spec.ts}-summary.json"
  local -a playwright_args=("$spec" "--project=$project")
  local -a playwright_env=(
    -u REACTIONX_SYNTHETIC_FORMAL_ENABLED
    -u REACTIONX_SYNTHETIC_FORMAL_ENABLED_FILE
    -u TEST_DATABASE_URL
    -u DATABASE_URL
    -u FIELD_ENCRYPTION_MASTER_KEY
    -u FIELD_LOOKUP_KEY
    -u REGISTRATION_VERIFICATION_PEPPER
    -u REGISTRATION_SESSION_TOKEN_PEPPER
    -u SESSION_TOKEN_PEPPER
    -u EVALUATION_TOOL_SERVICE_TOKEN
    -u EVALUATION_TOOL_WEB_SERVICE_TOKEN
    -u EVALUATION_TOOL_WORKER_SERVICE_TOKEN
    E2E_API_URL="$api_origin"
    E2E_BASE_URL="$web_origin"
    TALENT_E2E_ARTIFACT_DIR="$talent_artifact_dir"
    ESPORTS_E2E_ARTIFACT_DIR="$esports_artifact_dir"
    PLAYWRIGHT_OUTPUT_DIR="$tmp_dir/playwright-results"
    PLAYWRIGHT_HTML_REPORT_DIR="$tmp_dir/playwright-report"
  )
  local status
  local summary_status=0
  if [[ "$spec" == "invited-housing.spec.ts" ]]; then
    playwright_env+=(
      INVITED_HOUSING_REAL_E2E=1
      INVITED_HOUSING_E2E_ARTIFACT_DIR="$invited_artifact_dir"
      PLAYWRIGHT_OUTPUT_DIR="$invited_artifact_dir/${project}-results"
      PLAYWRIGHT_HTML_REPORT_DIR="$invited_artifact_dir/${project}-report"
    )
  fi
  if [[ "$spec" == talent-*.spec.ts ]]; then
    talent_log_suppression=1
    log_file="$tmp_dir/playwright-${project}-${spec%.spec.ts}.raw"
    playwright_env+=(PLAYWRIGHT_JSON_OUTPUT_NAME="$report_file")
    playwright_args+=("--reporter=json")
  elif [[ "$spec" == esports-*.spec.ts ]]; then
    esports_log_suppression=1
    log_file="$tmp_dir/playwright-${project}-${spec%.spec.ts}.raw"
    summary_file="$esports_artifact_dir/${project}-${spec%.spec.ts}-summary.json"
    playwright_env+=(PLAYWRIGHT_JSON_OUTPUT_NAME="$report_file")
    playwright_args+=("--reporter=json")
  elif [[ "$spec" == reactionx-integration.spec.ts ]]; then
    reactionx_log_suppression=1
    log_file="$tmp_dir/playwright-${project}-${spec%.spec.ts}.raw"
    summary_file="$reactionx_artifact_dir/${project}-summary.json"
    playwright_env+=(PLAYWRIGHT_JSON_OUTPUT_NAME="$report_file")
    playwright_args+=("--reporter=json")
  fi
  set +e
  env "${playwright_env[@]}" \
    npm --prefix apps/web run test:e2e -- "${playwright_args[@]}" \
      >"$log_file" 2>&1
  status=$?
  if [[ "$spec" == talent-*.spec.ts ]]; then
    mkdir -p "$talent_artifact_dir"
    PYTHONPATH="$repo_root/apps/api" "$python_bin" \
      -m app.cli.summarize_talent_playwright \
      --report "$report_file" \
      --output "$summary_file" \
      --spec "$spec" \
      --project "$project"
    summary_status=$?
  elif [[ "$spec" == esports-*.spec.ts ]]; then
    mkdir -p "$esports_artifact_dir"
    PYTHONPATH="$repo_root/apps/api" "$python_bin" \
      -m app.cli.summarize_esports_assessments_e2e \
      --report "$report_file" \
      --output "$summary_file" \
      --spec "$spec" \
      --project "$project"
    summary_status=$?
  elif [[ "$spec" == reactionx-integration.spec.ts ]]; then
    PYTHONPATH="$repo_root/apps/api" "$python_bin" \
      -m app.cli.summarize_reactionx_playwright \
      --report "$report_file" --output "$summary_file"
    summary_status=$?
  elif (( status == 0 )); then
    redact_log <"$log_file"
  else
    echo "Playwright 失败: $spec ($project)" >&2
    tail -160 "$log_file" | redact_log >&2 || true
  fi
  set -e
  if (( status != 0 )); then
    return "$status"
  fi
  if (( summary_status != 0 )); then
    return "$summary_status"
  fi
  return "$status"
}

projects=(desktop-1440 desktop-1024 tablet-768 mobile-360)
specs=(
  platform-foundation.spec.ts
  teaching-configuration.spec.ts
  admissions-enrollment.spec.ts
  enrollment-catalog-responsive.spec.ts
  coach-family-portal.spec.ts
  system-evaluation-responsive.spec.ts
  housing-filter-focus.spec.ts
)
cloud_authorization_projects=(desktop-1440 mobile-360 touch-mobile-390)
request_error_mock_projects=(desktop-1440 desktop-1024 tablet-768 mobile-360 touch-mobile-390)
asset_rental_mock_projects=(desktop-1440 desktop-1024 tablet-768 mobile-360 touch-mobile-390)
person_profile_mock_projects=(desktop-1440 mobile-360)
ui_theme_mock_projects=(desktop-1440 desktop-1024 tablet-768 mobile-360 touch-mobile-390)

esports_matrix=(
  "esports-pc.spec.ts|esports-desktop-1440|pc"
  "esports-touch.spec.ts|esports-touch-844x390|touch"
  "esports-recovery.spec.ts|esports-desktop-1440|recovery"
  "esports-capabilities.spec.ts|esports-desktop-1440|capabilities-desktop"
  "esports-capabilities.spec.ts|esports-touch-844x390|capabilities-touch"
  "esports-capabilities.spec.ts|esports-webkit-capabilities|capabilities-webkit"
)

run_seeded_spec() {
  local spec=$1
  local project=$2
  local seed_module=$3
  cd "$repo_root/apps/api"
  if [[ "$spec" == "talent-dynamic-vision.spec.ts" ]]; then
    TALENT_E2E_LEGACY_V1=1 run_erp_cli -m "$seed_module"
  else
    run_erp_cli -m "$seed_module"
  fi
  cd "$repo_root"
  run_playwright "$spec" "$project"
  cd "$repo_root/apps/api"
  if [[ "$spec" == "talent-dynamic-vision.spec.ts" ]]; then
    TALENT_E2E_VERIFY_MODE=dynamic run_erp_cli -m "$seed_module"
  elif [[ "$spec" == "talent-tests.spec.ts" ]]; then
    TALENT_E2E_VERIFY_MODE=double-full run_erp_cli -m "$seed_module"
  elif [[ "$spec" == "talent-item-ranking.spec.ts" ]]; then
    # Partial-round spec rereads persisted unique result IDs, live ranking
    # snapshots, and logout denials itself; full-round verification is inapplicable.
    :
  elif [[ "$spec" == talent-*.spec.ts ]]; then
    TALENT_E2E_VERIFY_MODE=full run_erp_cli -m "$seed_module"
  fi
  cd "$repo_root"
}

run_core_matrix() {
  local project
  local spec
  for project in "${projects[@]}"; do
    for spec in "${specs[@]}"; do
      cd "$repo_root/apps/api"
      run_erp_cli -m app.cli.seed_e2e
      cd "$repo_root"
      run_playwright "$spec" "$project"
    done
  done
}

run_cloud_authorization_specs() {
  local project
  for project in "${cloud_authorization_projects[@]}"; do
    run_playwright cloud-authorization.spec.ts "$project"
  done
}

run_request_error_mock_specs() {
  local project
  for project in "${request_error_mock_projects[@]}"; do
    run_playwright operations-request-errors.spec.ts "$project"
  done
}

run_asset_rental_mock_specs() {
  local project
  for project in "${asset_rental_mock_projects[@]}"; do
    run_playwright asset-rentals.spec.ts "$project"
    run_playwright asset-batch-hardware.spec.ts "$project"
  done
}

run_person_profile_mock_specs() {
  local project
  for project in "${person_profile_mock_projects[@]}"; do
    run_playwright person-detail-chunk.spec.ts "$project"
    run_playwright person-profile-assignments.spec.ts "$project"
    run_playwright personnel-documents.spec.ts "$project"
  done
}

run_ui_theme_mock_specs() {
  local project
  for project in "${ui_theme_mock_projects[@]}"; do
    run_playwright ui-themes.spec.ts "$project"
    run_playwright ui-theme-foundation.spec.ts "$project"
  done
}

run_talent_specs() {
  run_seeded_spec talent-item-ranking.spec.ts desktop-1440 app.cli.seed_talent_e2e
  run_seeded_spec talent-tests.spec.ts desktop-1440 app.cli.seed_talent_e2e
  run_seeded_spec talent-touch.spec.ts touch-mobile-390 app.cli.seed_talent_e2e
  local project
  for project in desktop-1440 desktop-1024 tablet-768 mobile-360 touch-mobile-390; do
    run_seeded_spec talent-dynamic-vision.spec.ts "$project" app.cli.seed_talent_e2e
  done
}

run_esports_specs() {
  local entry
  local spec
  local project
  local verify_mode
  for entry in "${esports_matrix[@]}"; do
    IFS='|' read -r spec project verify_mode <<<"$entry"
    cd "$repo_root/apps/api"
    run_erp_cli -m app.cli.seed_esports_assessments_e2e
    cd "$repo_root"
    run_playwright "$spec" "$project"
    cd "$repo_root/apps/api"
    ESPORTS_E2E_VERIFY_MODE="$verify_mode" \
      run_erp_cli -m app.cli.seed_esports_assessments_e2e
    cd "$repo_root"
  done
}

run_invited_housing_specs() {
  local project
  for project in desktop-1440 mobile-360; do
    cd "$repo_root/apps/api"
    run_erp_cli -m app.cli.seed_e2e
    run_erp_cli -m app.cli.seed_housing_portal_e2e
    run_erp_cli -m app.cli.seed_invited_housing_e2e
    cd "$repo_root"
    run_playwright invited-housing.spec.ts "$project"
  done
}

private_reactionx_cli() {
  local phase=seed
  if [[ "$*" == *--probe-closed* ]]; then
    phase=closed_probe
  elif [[ "$*" == *--verify* ]]; then
    phase=persisted_verify
  fi
  if ! run_erp_cli "$@" >"$tmp_dir/reactionx-cli.raw" 2>&1; then
    echo "reactionx-cli-failed:$phase: 原始异常已隔离并删除" >&2
    # These are fixed application diagnostics, never arbitrary exception text.
    while IFS= read -r diagnostic; do
      case "$diagnostic" in
        'ReactionX acceptance refused: closed_probe_login'|\
        'ReactionX acceptance refused: closed_probe_modes'|\
        'ReactionX acceptance refused: default_formal_open')
          echo "$diagnostic" >&2
          ;;
      esac
    done <"$tmp_dir/reactionx-cli.raw"
    return 1
  fi
}

run_reactionx_specs() {
  reactionx_log_suppression=1
  cd "$repo_root/apps/api"
  private_reactionx_cli -m app.cli.seed_reactionx_e2e
  private_reactionx_cli -m app.cli.seed_reactionx_e2e --probe-closed "$api_origin"
  stop_api
  start_api true
  wait_http "$api_origin/api/v1/health/live" "$api_pid" "ReactionX synthetic API" "$tmp_dir/api.log" \
    "Uvicorn running on http://127.0.0.1:${api_port} ("
  cd "$repo_root"
  run_playwright reactionx-integration.spec.ts reactionx-desktop-1440
  cd "$repo_root/apps/api"
  private_reactionx_cli -m app.cli.seed_reactionx_e2e --verify
  echo "reactionx-persisted-evidence-passed"
  stop_api
  start_api false
  wait_http "$api_origin/api/v1/health/live" "$api_pid" "API closed policy" "$tmp_dir/api.log" \
    "Uvicorn running on http://127.0.0.1:${api_port} ("
  cd "$repo_root"
}

case "$e2e_mode" in
  core)
    run_core_matrix
    run_cloud_authorization_specs
    run_request_error_mock_specs
    run_asset_rental_mock_specs
    run_person_profile_mock_specs
    run_ui_theme_mock_specs
    run_invited_housing_specs
    ;;
  talent)
    run_talent_specs
    ;;
  esports)
    run_esports_specs
    ;;
  reactionx)
    run_reactionx_specs
    ;;
  invited-housing)
    run_invited_housing_specs
    ;;
  all)
    run_core_matrix
    run_cloud_authorization_specs
    run_request_error_mock_specs
    run_asset_rental_mock_specs
    run_person_profile_mock_specs
    run_ui_theme_mock_specs
    run_invited_housing_specs
    run_talent_specs
    run_esports_specs
    run_reactionx_specs
    ;;
  *)
    echo "E2E_CORE_MODE 只允许 core、talent、esports、reactionx、invited-housing 或 all" >&2
    exit 2
    ;;
esac
