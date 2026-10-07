#!/usr/bin/env bash
# 校验 AGENTS.md 存在且包含两条必守注意事项。
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
if [ ! -f AGENTS.md ]; then
  echo "FAIL: AGENTS.md 不存在"
  fail=1
fi

if ! grep -q "git commit" AGENTS.md; then
  echo "FAIL: 缺少“每次改动必须创建 git commit”的要求"
  fail=1
fi

if ! grep -q "测试" AGENTS.md; then
  echo "FAIL: 缺少“改动后必须编写/更新并跑通测试”的要求"
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "PASS: AGENTS.md 校验通过（文件存在且两条注意事项齐全）"
fi
exit "$fail"
