#!/usr/bin/env bash
# 校验 AGENTS.md 存在且包含必守注意事项。
# 第 1 条「每次改动完成时，都必须创建一个对应的 git commit」已按
# `.scratch/m1-engine-skeleton/map.md` 的**零 commit 约定**删除；
# 本测试同时防止它被无意识地加回来。
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
if [ ! -f AGENTS.md ]; then
  echo "FAIL: AGENTS.md 不存在"
  fail=1
fi

if ! grep -q "## 注意事项" AGENTS.md; then
  echo "FAIL: 缺少“注意事项”段"
  fail=1
fi

if ! grep -q "测试" AGENTS.md; then
  echo "FAIL: 缺少“改动后必须编写/更新并跑通测试”的要求"
  fail=1
fi

if grep -q "git commit" AGENTS.md; then
  echo "FAIL: 出现了 git commit 要求——本图为零 commit 约定；如需恢复请同时更新本测试"
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "PASS: AGENTS.md 校验通过（文件存在、注意事项齐全、无 commit 要求）"
fi
exit "$fail"
