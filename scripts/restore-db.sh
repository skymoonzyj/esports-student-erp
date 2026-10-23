#!/usr/bin/env bash
set -euo pipefail

script_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$script_directory/db-schema-manifest.sh"

if [[ $# -ne 2 || -z ${1:-} || -z ${2:-} ]]; then
  echo "用法：DATABASE_URL=... $0 <备份文件> <空测试数据库名>" >&2
  exit 2
fi

: "${DATABASE_URL:?必须通过 DATABASE_URL 明确指定来源服务器}"
for command_name in pg_restore psql createdb; do
  command -v "$command_name" >/dev/null || {
    echo "缺少 $command_name" >&2
    exit 3
  }
done

dump_file=$1
target_database=$2
checksum_file="${dump_file}.sha256"

[[ -f "$dump_file" ]] || { echo "备份文件不存在：$dump_file" >&2; exit 2; }
[[ -f "$checksum_file" ]] || { echo "校验文件不存在：$checksum_file" >&2; exit 2; }
[[ "$target_database" =~ ^[a-z][a-z0-9_]*_test$ ]] || {
  echo "恢复目标必须是以 _test 结尾的显式测试数据库名" >&2
  exit 2
}

database_url=${DATABASE_URL/postgresql+psycopg:/postgresql:}
case "$database_url" in
  postgresql://*|postgres://*) ;;
  *) echo "DATABASE_URL 必须是 PostgreSQL URL" >&2; exit 2 ;;
esac
base_url=${database_url%/*}
source_database=${database_url##*/}
source_database=${source_database%%\?*}
[[ "$target_database" != "$source_database" ]] || {
  echo "拒绝恢复到来源数据库" >&2
  exit 2
}
maintenance_url="${base_url}/postgres"
target_url="${base_url}/${target_database}"

expected_checksum=$(awk 'NR == 1 {print $1}' "$checksum_file")
if command -v sha256sum >/dev/null; then
  actual_checksum=$(sha256sum "$dump_file" | awk '{print $1}')
elif command -v shasum >/dev/null; then
  actual_checksum=$(shasum -a 256 "$dump_file" | awk '{print $1}')
else
  echo "缺少 SHA-256 校验工具" >&2
  exit 3
fi
[[ -n "$expected_checksum" && "$actual_checksum" == "$expected_checksum" ]] || {
  echo "备份文件 SHA-256 校验失败" >&2
  exit 4
}

exists=$(psql "$maintenance_url" -v ON_ERROR_STOP=1 -Atc \
  "SELECT 1 FROM pg_database WHERE datname = '$target_database'")
if [[ "$exists" != "1" ]]; then
  createdb --maintenance-db="$maintenance_url" "$target_database"
fi

user_table_count=$(psql "$target_url" -v ON_ERROR_STOP=1 -Atc \
  "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')")
[[ "$user_table_count" == "0" ]] || {
  echo "恢复目标不是空数据库，已拒绝覆盖：$target_database" >&2
  exit 5
}

pg_restore \
  --dbname="$target_url" \
  --clean \
  --if-exists \
  --no-owner \
  "$dump_file"

migration_version=$(psql "$target_url" -v ON_ERROR_STOP=1 -Atc \
  "SELECT version_num FROM alembic_version")
[[ "$migration_version" == "$EXPECTED_ALEMBIC_HEAD" ]] || {
  echo "恢复后的 Alembic 版本不符合预期：$migration_version" >&2
  exit 6
}

for table_name in "${REQUIRED_TABLES[@]}"; do
  table_exists=$(psql "$target_url" -v ON_ERROR_STOP=1 -Atc \
    "SELECT to_regclass('public.${table_name}') IS NOT NULL")
  [[ "$table_exists" == "t" ]] || {
    echo "恢复后缺少必要数据表：$table_name" >&2
    exit 6
  }
done

for sequence_name in "${REQUIRED_SEQUENCES[@]}"; do
  sequence_exists=$(psql "$target_url" -v ON_ERROR_STOP=1 -Atc \
    "SELECT EXISTS (SELECT 1 FROM pg_class WHERE relkind = 'S' AND relname = '${sequence_name}')")
  [[ "$sequence_exists" == "t" ]] || {
    echo "恢复后缺少必要序列：$sequence_name" >&2
    exit 6
  }
done

for table_name in "${ROW_COUNT_TABLES[@]}"; do
  source_count=$(psql "$database_url" -v ON_ERROR_STOP=1 -Atc "SELECT count(*) FROM $table_name")
  target_count=$(psql "$target_url" -v ON_ERROR_STOP=1 -Atc "SELECT count(*) FROM $table_name")
  [[ "$source_count" == "$target_count" ]] || {
    echo "恢复后的 $table_name 行数与来源不一致" >&2
    exit 6
  }
done

echo "数据库恢复演练完成：$target_database"
echo "Alembic 版本：${migration_version}；必要数据表：${#REQUIRED_TABLES[@]}"
