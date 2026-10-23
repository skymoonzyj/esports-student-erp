#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 || -z ${1:-} ]]; then
  echo "用法：DATABASE_URL=... $0 <备份输出文件>" >&2
  exit 2
fi

: "${DATABASE_URL:?必须通过 DATABASE_URL 明确指定来源数据库}"
command -v pg_dump >/dev/null || { echo "缺少 pg_dump" >&2; exit 3; }

output=$1
database_url=${DATABASE_URL/postgresql+psycopg:/postgresql:}
case "$database_url" in
  postgresql://*|postgres://*) ;;
  *) echo "DATABASE_URL 必须是 PostgreSQL URL" >&2; exit 2 ;;
esac

umask 077
mkdir -p "$(dirname "$output")"
temporary="${output}.tmp.$$"
trap 'rm -f "$temporary"' EXIT

pg_dump \
  --dbname="$database_url" \
  --format=custom \
  --no-owner \
  --file="$temporary"
mv "$temporary" "$output"

if command -v sha256sum >/dev/null; then
  checksum=$(sha256sum "$output" | awk '{print $1}')
elif command -v shasum >/dev/null; then
  checksum=$(shasum -a 256 "$output" | awk '{print $1}')
else
  echo "缺少 SHA-256 校验工具" >&2
  exit 3
fi

printf '%s  %s\n' "$checksum" "$(basename "$output")" > "${output}.sha256"
echo "数据库备份已完成：$output"
echo "SHA-256：${output}.sha256"
