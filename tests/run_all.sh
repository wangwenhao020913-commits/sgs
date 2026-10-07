#!/usr/bin/env bash
# 运行 SGS 项目的全部测试。
set -euo pipefail
cd "$(dirname "$0")"

fail=0
for t in test_*.sh; do
  echo "=== 运行 $t ==="
  if bash "$t"; then
    echo "--- $t 通过 ---"
  else
    echo "--- $t 失败 ---"
    fail=1
  fi
  echo
done

if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
else
  echo "SOME TESTS FAILED"
fi
exit "$fail"
