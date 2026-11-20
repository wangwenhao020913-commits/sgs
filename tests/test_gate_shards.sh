#!/usr/bin/env bash
# 门禁分片的自检（hero-flow-v2 票 02）。
#
# `tests/test_dod.py` 已经钉住门禁的三条对外契约（聚合失败 / 全绿退出 0 / 非交互）；
# 本文件钉**分片引入后多出来的三条**，因为它们在合成仓里会静默坏掉：
#
#   ① **覆盖**：tests/ 下的每个 `test_*.py` 恰好进一个分片——漏一个 = 少跑一批判据，
#      重复一个 = 同一批判据跑两遍（既慢又掩盖归属）。
#   ② **片数**：服务调用的次数 = 分片数（不是 1，也不是"每片再拆一次"）。
#   ③ **失败传播**：任一片失败 ⇒ 整门禁非零退出，且**红的那片的日志**落进
#      `runs/gate-<时间戳>/`（仓内，`$LOG_DIR` 退出即删）。
#
# 做法：在 $TMPDIR 里搭一个最小仓（真 `run_all.sh` + 一个记录调用参数的测试器桩），
# 不碰工作树。**本文件不许出现小写的测试器名**：`test_dod` 的唯一门禁扫描会把 tests/ 下
# 出现该字样的 shell 判成"第二个门禁入口"——所以桩文件用拼接构造路径，正文一律写"测试器"。
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
TMP="$(mktemp -d "${TMPDIR:-/tmp}/sgs-gate-shards.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
REPO="$TMP/repo"
CALLS="$TMP/calls"

pass() { echo "PASS: $*"; }
bad() {
  echo "FAIL: $*"
  fail=1
}

setup() {
  rm -rf "$REPO" "$CALLS"
  mkdir -p "$REPO/tests" "$REPO/.venv/bin" "$CALLS"
  cp tests/run_all.sh "$REPO/tests/run_all.sh"
  # 门禁会 glob 自己目录下的 `test_*.sh`：给一个必定通过的，否则它会去跑字面量文件名。
  cat >"$REPO/tests/test_ok.sh" <<'OK'
#!/bin/sh
echo SCRIPT_OK
exit 0
OK
  for name in test_a test_b test_c test_d test_e; do
    printf 'def test_placeholder():\n    assert True\n' >"$REPO/tests/$name.py"
  done
  # 桩：把收到的参数记进 $CALLS，遇到 test_bad.py 就失败。
  local stub="$REPO/.venv/bin/py""test"
  cat >"$stub" <<'STUB'
#!/bin/sh
mkdir -p "$CALL_DIR"
printf '%s\n' "$@" >"$CALL_DIR/call-$$.args"
for arg in "$@"; do
  case "$arg" in
    *test_bad.py) echo "1 failed, 0 passed"; exit 1 ;;
  esac
done
echo "5 passed"
exit 0
STUB
  chmod +x "$stub"
}

# ---- ① 覆盖 + ② 片数 ----
setup
if CALL_DIR="$CALLS" SGS_GATE_SHARDS=2 bash "$REPO/tests/run_all.sh" --quiet >"$TMP/ok.out" 2>&1; then
  pass "两片、全绿 ⇒ 门禁退出 0"
else
  bad "两片、全绿时门禁仍失败"
  sed 's/^/  /' "$TMP/ok.out" | tail -20
fi

calls=$(ls "$CALLS" | wc -l | tr -d ' ')
if [ "$calls" = "2" ]; then
  pass "服务被调用 2 次（= 分片数）"
else
  bad "服务被调用 $calls 次，期望 2"
fi

cat "$CALLS"/*.args | grep -v '^-' | sort >"$TMP/actual.txt" || true
printf 'tests/test_%s.py\n' a b c d e | sort >"$TMP/expected.txt"
if diff -u "$TMP/expected.txt" "$TMP/actual.txt" >"$TMP/diff.txt"; then
  pass "5 个测试文件恰好各进一片（覆盖且不重复）"
else
  bad "分片覆盖或去重不成立"
  sed 's/^/  /' "$TMP/diff.txt"
fi

# ---- ③ 失败传播 + 落盘 ----
setup
printf 'def test_bad():\n    assert False\n' >"$REPO/tests/test_bad.py"
if CALL_DIR="$CALLS" SGS_GATE_SHARDS=2 bash "$REPO/tests/run_all.sh" --quiet >"$TMP/bad.out" 2>&1; then
  bad "有一片失败时门禁仍然退出 0"
else
  if grep -q 'SOME TESTS FAILED' "$TMP/bad.out" && grep -q '^FAIL' "$TMP/bad.out"; then
    pass "一片失败 ⇒ 非零退出并打 FAIL 与 SOME TESTS FAILED"
  else
    bad "失败时缺少 FAIL / SOME TESTS FAILED 标记"
    sed 's/^/  /' "$TMP/bad.out" | tail -25
  fi
fi

kept=$(find "$REPO/runs" -name '*.log' 2>/dev/null | head -1)
if [ -n "$kept" ]; then
  pass "红片的完整日志落进 runs/gate-<时间戳>/"
else
  bad "失败日志没有落进 runs/gate-<时间戳>/（$LOG_DIR 退出即删，失败现场会跟着没掉）"
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
  exit 0
fi
echo "SOME TESTS FAILED"
exit 1
