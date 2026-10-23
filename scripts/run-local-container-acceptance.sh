#!/usr/bin/env bash
set -euo pipefail
umask 077

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
cd "$repo_root"

bind_address=${ERP_BIND_ADDRESS:-127.0.0.1}
if [[ "$bind_address" != "127.0.0.1" ]]; then
  echo "本地容器验收只允许绑定 127.0.0.1" >&2
  exit 2
fi
export ERP_BIND_ADDRESS=127.0.0.1

docker_environment=(
  "PATH=$PATH"
  "HOME=${HOME:-}"
  "TMPDIR=${TMPDIR:-/tmp}"
  "COMPOSE_DISABLE_ENV_FILE=1"
)
if [[ -n ${DOCKER_HOST:-} ]]; then
  docker_environment+=("DOCKER_HOST=$DOCKER_HOST")
fi
if [[ -n ${DOCKER_CONTEXT:-} ]]; then
  docker_environment+=("DOCKER_CONTEXT=$DOCKER_CONTEXT")
fi
if [[ -n ${DOCKER_TLS_VERIFY:-} ]]; then
  docker_environment+=("DOCKER_TLS_VERIFY=$DOCKER_TLS_VERIFY")
fi
if [[ -n ${DOCKER_CERT_PATH:-} ]]; then
  docker_environment+=("DOCKER_CERT_PATH=$DOCKER_CERT_PATH")
fi
if [[ -n ${DOCKER_CONFIG:-} ]]; then
  docker_environment+=("DOCKER_CONFIG=$DOCKER_CONFIG")
fi
if [[ -n ${XDG_CONFIG_HOME:-} ]]; then
  docker_environment+=("XDG_CONFIG_HOME=$XDG_CONFIG_HOME")
fi
run_docker_client() {
  env -i "${docker_environment[@]}" "$@"
}

docker_bin=$(command -v docker || true)
if [[ -z "$docker_bin" ]] || \
  ! run_docker_client "$docker_bin" --version >/dev/null 2>&1; then
  echo "Docker client 不可用" >&2
  exit 3
fi

compose_version_is_v2() {
  local version=$1
  if [[ "$version" =~ [vV]?([0-9]+)\.[0-9]+ ]]; then
    (( BASH_REMATCH[1] >= 2 ))
    return
  fi
  return 1
}

compose=()
if compose_version=$(run_docker_client docker compose version --short 2>/dev/null); then
  if compose_version_is_v2 "$compose_version"; then
    compose=(docker compose)
  fi
fi

if (( ${#compose[@]} == 0 )); then
  docker_compose_bin=$(command -v docker-compose 2>/dev/null || true)
  if [[ -z "$docker_compose_bin" ]] && [[ -x /opt/homebrew/bin/docker-compose ]]; then
    docker_compose_bin=/opt/homebrew/bin/docker-compose
  fi
  if [[ -n "$docker_compose_bin" ]]; then
    if compose_version=$(run_docker_client "$docker_compose_bin" version --short 2>/dev/null) && \
      compose_version_is_v2 "$compose_version"; then
      compose=("$docker_compose_bin")
    else
      echo "本地容器验收要求 Docker Compose v2 或更高版本" >&2
      exit 3
    fi
  fi
fi

if (( ${#compose[@]} == 0 )); then
  echo "Docker Compose v2 client 不可用" >&2
  exit 3
fi

if ! run_docker_client "$docker_bin" info >/dev/null 2>&1; then
  echo "Docker daemon 不可用；未启动本地容器验收" >&2
  exit 3
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "缺少 curl，无法执行代理与 API 健康检查" >&2
  exit 3
fi

if [[ -n ${CONTAINER_ACCEPTANCE_PYTHON_BIN:-} ]]; then
  python_bin=$CONTAINER_ACCEPTANCE_PYTHON_BIN
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
  if [[ ! -x "$python_bin" ]]; then
    python_bin=$(command -v python3 || true)
  fi
fi
if [[ -z "$python_bin" ]] || [[ ! -x "$python_bin" ]]; then
  echo "缺少 Python 3，无法分配安全的回环空闲端口" >&2
  exit 3
fi
git_sha=$(git -C "$repo_root" rev-parse HEAD)
source_tree_sha256=$("$python_bin" "$repo_root/scripts/source-tree-digest.py" "$repo_root")

read -r http_port db_port < <(
  "$python_bin" - <<'PY'
from __future__ import annotations

import socket

sockets = [socket.socket(socket.AF_INET, socket.SOCK_STREAM) for _ in range(2)]
try:
    for probe in sockets:
        probe.bind(("127.0.0.1", 0))
    print(*(int(probe.getsockname()[1]) for probe in sockets))
finally:
    for probe in sockets:
        probe.close()
PY
)
if [[ -z "$http_port" || -z "$db_port" || "$http_port" == "$db_port" ]]; then
  echo "无法分配彼此独立的 HTTP/DB 回环空闲端口" >&2
  exit 3
fi
export E2E_HTTP_PORT=$http_port
export E2E_DB_PORT=$db_port

project_name="esports-erp-container-acceptance-$(date +%s)-$$-${RANDOM}"
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/esports-erp-container-acceptance.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT
chmod 700 "$tmp_dir"
empty_env_file="$tmp_dir/compose.env"
: >"$empty_env_file"
chmod 600 "$empty_env_file"

summary_path=${LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH:-$repo_root/runtime/container-acceptance/local-container-acceptance-summary.json}
if [[ "$summary_path" != /* ]]; then
  summary_path="$repo_root/$summary_path"
fi
summary_dir=$(dirname "$summary_path")
mkdir -p "$summary_dir"

acceptance_compose=(
  "${compose[@]}"
  --env-file "$empty_env_file"
  -p "$project_name"
  -f "$repo_root/compose.yaml"
  -f "$repo_root/compose.e2e.yaml"
)
compose_environment=(
  "${docker_environment[@]}"
  "ERP_BIND_ADDRESS=127.0.0.1"
  "E2E_HTTP_PORT=$E2E_HTTP_PORT"
  "E2E_DB_PORT=$E2E_DB_PORT"
)

check_names=(
  compose_config compose_up database_safety alembic_upgrade
  migration_chain proxy_health api_health schema_manifest
)
check_statuses=(
  pending pending pending pending pending pending pending pending
)

mark_check() {
  local check_name=$1
  local state=$2
  local index
  for index in "${!check_names[@]}"; do
    if [[ "${check_names[$index]}" == "$check_name" ]]; then
      check_statuses[$index]=$state
      return 0
    fi
  done
  echo "未知验收检查项" >&2
  return 2
}

checks_payload() {
  local payload=""
  local index
  for index in "${!check_names[@]}"; do
    if [[ -n "$payload" ]]; then
      payload+=,
    fi
    payload+="${check_names[$index]}=${check_statuses[$index]}"
  done
  printf '%s' "$payload"
}

write_summary() {
  local state=$1
  local exit_code=$2
  local cleanup_completed=$3
  local checks
  checks=$(checks_payload)
  "$python_bin" - "$summary_path" "$state" "$exit_code" \
    "$cleanup_completed" "$project_name" "$git_sha" \
    "$source_tree_sha256" "$checks" <<'PY'
from __future__ import annotations

import json
import os
import sys
from tempfile import NamedTemporaryFile
from datetime import UTC, datetime
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "schema_version": 1,
    "status": sys.argv[2],
    "exit_code": int(sys.argv[3]),
    "cleanup_completed": sys.argv[4] == "true",
    "project_name": sys.argv[5],
    "git_sha": sys.argv[6],
    "source_tree_sha256": sys.argv[7],
    "generated_at": datetime.now(UTC).isoformat(),
    "compose_files": ["compose.yaml", "compose.e2e.yaml"],
    "checks": dict(
        item.split("=", 1) for item in sys.argv[8].split(",") if item
    ),
}
path.parent.mkdir(parents=True, exist_ok=True)
with NamedTemporaryFile(
    mode="w",
    encoding="utf-8",
    dir=path.parent,
    prefix=f".{path.name}.",
    suffix=".tmp",
    delete=False,
) as handle:
    temporary = Path(handle.name)
    handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
try:
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if temporary.exists():
        temporary.unlink()
os.chmod(path, 0o600)
PY
  chmod 600 "$summary_path"
}

redact_log() {
  env -i \
    "PATH=$PATH" \
    "PYTHONPATH=$repo_root/apps/api" \
    "$python_bin" -m app.cli.redact_registration_e2e_output
}

emit_redacted_log() {
  local log_file=$1
  if [[ -s "$log_file" ]]; then
    tail -160 "$log_file" | redact_log >&2 || {
      echo "检查日志脱敏失败，原始日志已丢弃" >&2
    }
  fi
}

run_compose() {
  env -i "${compose_environment[@]}" "${acceptance_compose[@]}" "$@"
}

run_logged_step() {
  local check_name=$1
  shift
  local log_file="$tmp_dir/${check_name}.log"
  local status
  if "$@" >"$log_file" 2>&1; then
    mark_check "$check_name" passed
    return 0
  else
    status=$?
  fi
  mark_check "$check_name" failed
  echo "本地容器验收检查失败: $check_name" >&2
  emit_redacted_log "$log_file"
  return "$status"
}

cleanup() {
  local status=$?
  local down_status=0
  local remove_status=0
  local cleanup_completed=true
  local state=failed
  local summary_written=false
  local cleanup_log="$tmp_dir/cleanup.log"
  trap - EXIT

  set +e
  run_compose down --volumes --remove-orphans >"$cleanup_log" 2>&1
  down_status=$?
  if (( down_status != 0 )); then
    echo "本地容器验收清理失败" >&2
    emit_redacted_log "$cleanup_log"
  fi
  rm -rf "$tmp_dir"
  remove_status=$?
  set -e

  if (( down_status != 0 || remove_status != 0 )); then
    cleanup_completed=false
    if (( status == 0 )); then
      status=1
    fi
  fi
  if (( status == 0 )); then
    state=passed
  fi
  if write_summary "$state" "$status" "$cleanup_completed"; then
    summary_written=true
  else
    echo "无法写入本地容器验收摘要" >&2
    if (( status == 0 )); then
      status=1
    fi
  fi
  if (( status == 0 )); then
    echo "本地 disposable Compose 验收通过"
  else
    echo "本地 disposable Compose 验收失败" >&2
  fi
  if [[ "$summary_written" == true ]]; then
    echo "本地容器验收摘要已写入指定路径"
  fi
  exit "$status"
}
trap cleanup EXIT

source "$repo_root/scripts/db-schema-manifest.sh"

manifest_scalar() {
  local query=$1
  run_compose exec -T db \
    psql -U erp_e2e -d erp_e2e -v ON_ERROR_STOP=1 -Atc "$query" | tr -d '\r'
}

require_manifest_entry() {
  local category=$1
  local entry=$2
  local query=$3
  local present
  if ! present=$(manifest_scalar "$query"); then
    return 5
  fi
  if [[ "$present" != "t" ]]; then
    echo "schema manifest 缺少 ${category}: ${entry}" >&2
    return 5
  fi
}

verify_schema_manifest() {
  local actual_head
  local table_name
  local index_name
  local constraint_name
  local trigger_name
  local sequence_name
  local qualified_name
  local column_name
  local role_key
  local permission_key

  if ! actual_head=$(manifest_scalar "SELECT version_num FROM public.alembic_version"); then
    return 5
  fi
  if [[ "$actual_head" != "$EXPECTED_ALEMBIC_HEAD" ]]; then
    echo "Alembic head 不符合 schema manifest: $actual_head" >&2
    return 5
  fi

  for table_name in "${REQUIRED_TABLES[@]}"; do
    require_manifest_entry table "$table_name" \
      "SELECT to_regclass('public.${table_name}') IS NOT NULL" || return $?
  done
  for index_name in "${REQUIRED_INDEXES[@]}"; do
    require_manifest_entry index "$index_name" \
      "SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname = 'public' AND indexname = '${index_name}')" || return $?
  done
  for constraint_name in "${REQUIRED_CONSTRAINTS[@]}"; do
    require_manifest_entry constraint "$constraint_name" \
      "SELECT EXISTS (SELECT 1 FROM pg_constraint AS constraint_row JOIN pg_namespace AS namespace_row ON namespace_row.oid = constraint_row.connamespace WHERE namespace_row.nspname = 'public' AND constraint_row.conname = '${constraint_name}')" || return $?
  done
  for trigger_name in "${REQUIRED_TRIGGERS[@]-}"; do
    [[ -n "$trigger_name" ]] || continue
    require_manifest_entry trigger "$trigger_name" \
      "SELECT EXISTS (SELECT 1 FROM pg_trigger AS trigger_row JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid JOIN pg_namespace AS namespace_row ON namespace_row.oid = table_row.relnamespace WHERE namespace_row.nspname = 'public' AND trigger_row.tgname = '${trigger_name}' AND NOT trigger_row.tgisinternal)" || return $?
  done
  for sequence_name in "${REQUIRED_SEQUENCES[@]}"; do
    require_manifest_entry sequence "$sequence_name" \
      "SELECT EXISTS (SELECT 1 FROM pg_class AS sequence_row JOIN pg_namespace AS namespace_row ON namespace_row.oid = sequence_row.relnamespace WHERE namespace_row.nspname = 'public' AND sequence_row.relkind = 'S' AND sequence_row.relname = '${sequence_name}')" || return $?
  done
  for qualified_name in "${REQUIRED_SCOPE_COLUMNS[@]}"; do
    table_name=${qualified_name%%.*}
    column_name=${qualified_name#*.}
    require_manifest_entry scope-column "$qualified_name" \
      "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = '${table_name}' AND column_name = '${column_name}')" || return $?
  done
  for qualified_name in "${REQUIRED_ENCRYPTED_COLUMNS[@]}"; do
    table_name=${qualified_name%%.*}
    column_name=${qualified_name#*.}
    require_manifest_entry encrypted-column "$qualified_name" \
      "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = '${table_name}' AND column_name = '${column_name}')" || return $?
  done
  for qualified_name in "${REQUIRED_NULLABLE_LEGACY_COLUMNS[@]}"; do
    table_name=${qualified_name%%.*}
    column_name=${qualified_name#*.}
    require_manifest_entry nullable-column "$qualified_name" \
      "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' AND table_name = '${table_name}' AND column_name = '${column_name}' AND is_nullable = 'YES')" || return $?
  done
  for role_key in "${REQUIRED_ROLE_KEYS[@]}"; do
    require_manifest_entry role "$role_key" \
      "SELECT EXISTS (SELECT 1 FROM public.roles WHERE key = '${role_key}')" || return $?
  done
  for permission_key in "${REQUIRED_PERMISSION_KEYS[@]}"; do
    require_manifest_entry permission "$permission_key" \
      "SELECT EXISTS (SELECT 1 FROM public.permissions WHERE key = '${permission_key}')" || return $?
  done
}

write_summary running 0 false

internal_test_database_url="postgresql+psycopg://erp_e2e:erp-e2e-only@db:5432/erp_e2e"

run_logged_step compose_config run_compose config --quiet
run_logged_step compose_up run_compose up --build --wait
run_logged_step database_safety run_compose exec -T \
  -e "TEST_DATABASE_URL=$internal_test_database_url" \
  api python -m app.cli.e2e_database_safety
run_logged_step alembic_upgrade run_compose exec -T api alembic upgrade head

verify_migration_chain() {
  local revision
  for revision in "${MIGRATION_CHAIN[@]}"; do
    run_compose exec -T api \
      test -f "/app/migrations/versions/${revision}.py" || return $?
  done
}
run_logged_step migration_chain verify_migration_chain

run_logged_step proxy_health env -i "PATH=$PATH" curl \
  --fail --silent --show-error --connect-timeout 5 --max-time 15 \
  "http://127.0.0.1:${E2E_HTTP_PORT}/healthz"
run_logged_step api_health env -i "PATH=$PATH" curl \
  --fail --silent --show-error --connect-timeout 5 --max-time 15 \
  "http://127.0.0.1:${E2E_HTTP_PORT}/api/v1/health/live"

run_logged_step schema_manifest verify_schema_manifest
