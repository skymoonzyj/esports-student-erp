#!/usr/bin/env bash
set -euo pipefail

if (( $# != 2 )) || [[ $1 != /* || $2 != /* ]]; then
  echo "必须提供两个绝对 Git 工作树根目录" >&2
  exit 2
fi

erp_root=$1
score_root=$2

require_git_root() {
  local root=$1
  local label=$2
  local physical_root
  local git_root
  if [[ ! -d "$root" ]]; then
    echo "${label}目录不是 Git 工作树根目录" >&2
    return 2
  fi
  physical_root=$(cd "$root" && pwd -P)
  git_root=$(git -C "$physical_root" rev-parse --show-toplevel 2>/dev/null) || {
    echo "${label}目录不是 Git 工作树根目录" >&2
    return 2
  }
  git_root=$(cd "$git_root" && pwd -P)
  if [[ "$git_root" != "$physical_root" ]]; then
    echo "${label}目录不是 Git 工作树根目录" >&2
    return 2
  fi
}

require_git_root "$erp_root" "ERP"
require_git_root "$score_root" "评分工具"

erp_contract="$erp_root/apps/api/tests/contracts/evaluation_integration_v2.json"
score_contract="$score_root/tests/contracts/evaluation_integration_v2.json"
if [[ ! -f "$erp_contract" || ! -f "$score_contract" ]]; then
  echo "v2 评价契约文件缺失" >&2
  exit 2
fi

if ! cmp --silent -- "$erp_contract" "$score_contract"; then
  echo "v2 评价契约不一致" >&2
  exit 1
fi

echo "v2 评价契约逐字节一致"
