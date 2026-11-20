#!/usr/bin/env bash
# 提交前 screen 的自检（hero-flow-v2 票 01）。
#
# 它不是"再跑一遍 screen 的内容"，而是钉三件**会静默坏掉**的事：
#
#   ① 清单里的每个目标都真实存在 —— 改名 / 挪目录后，screen 若还"绿"，就是把
#      "没跑"伪装成"通过"（本仓踩过同类的坑：`git diff --exit-code` 对工作树干净时
#      空过，见 `tools/check_goldens.sh` 的头注）。
#   ② 清单里每一项都是**门禁已经在跑的判据** —— screen 只是把它挑出来提前跑，
#      不许另立第二套判据（口径同 `tests/test_dod.py::test_there_is_no_second_gate_entry`）。
#   ③ 聚合逻辑：任一目标失败 ⇒ 非零退出并打 `SCREEN FAIL`；全绿 ⇒ 退出 0。
#      用 `SGS_PYTEST`（大写）指一个桩脚本替换测试器，只验"失败会不会被吞掉"。
#
# **本文件不许出现小写的测试器名**：`test_dod` 的唯一门禁扫描会把 tests/ 下 shell 脚本里
# 出现小写测试器名的文件判成"第二个门禁入口"（本票第一次提交就是这么被拦下的）。所以这里
# 一律写大写常量名 `SGS_PYTEST`，桩文件也叫 `stub-*`。
#
# 纪律：只在 $TMPDIR 建探针，不碰工作树（同 `tests/test_goldens.sh`）。
set -uo pipefail
cd "$(dirname "$0")/.."

SCREEN="tools/precommit-screen.sh"
fail=0
TMP="$(mktemp -d "${TMPDIR:-/tmp}/sgs-screen-selfcheck.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

pass() { echo "PASS: $*"; }
bad() {
  echo "FAIL: $*"
  fail=1
}

# ---- ① 清单目标存在 ----
if ! list="$(bash "$SCREEN" --list 2>"$TMP/list.err")"; then
  bad "screen --list 失败"
  sed 's/^/  /' "$TMP/list.err" || true
  list=""
fi
missing=""
while IFS= read -r target; do
  [ -n "$target" ] || continue
  [ -e "$target" ] || missing="$missing $target"
done <<<"$list"
if [ -n "$missing" ]; then
  bad "screen 清单里有不存在的目标：$missing"
else
  pass "screen 清单的每个目标都存在（$(printf '%s\n' "$list" | grep -c . ) 项）"
fi

# ---- ② 每一项都是门禁已经在跑的判据 ----
not_in_gate=""
while IFS= read -r target; do
  [ -n "$target" ] || continue
  case "$target" in
    tests/test_*.py)
      # testpaths = ["tests"]（pyproject.toml）⇒ 测试器 默认收集它，天然在门禁里
      continue
      ;;
    tools/check_goldens.sh)
      grep -q 'check_goldens\.sh' tests/test_goldens.sh || not_in_gate="$not_in_gate $target"
      ;;
    tools/pool_census.py)
      grep -qr 'pool_census' tests/test_pool_census.py || not_in_gate="$not_in_gate $target"
      ;;
    *)
      not_in_gate="$not_in_gate $target"
      ;;
  esac
done <<<"$list"
if [ -n "$not_in_gate" ]; then
  bad "screen 清单里有门禁不跑的项（不许另立第二套判据）：$not_in_gate"
else
  pass "screen 清单里的每一项都在门禁的判据集合里"
fi

# ---- ③ 聚合逻辑：失败不许被吞 ----
cat >"$TMP/stub-fail" <<'STUB'
#!/bin/sh
echo "1 failed, 0 passed in 0.01s"
exit 1
STUB
cat >"$TMP/stub-pass" <<'STUB'
#!/bin/sh
echo "1 passed in 0.01s"
exit 0
STUB
chmod +x "$TMP/stub-fail" "$TMP/stub-pass"

if SGS_PYTEST="$TMP/stub-fail" bash "$SCREEN" >"$TMP/fail.out" 2>&1; then
  bad "测试器 桩失败时，screen 仍然退出 0（失败被吞了）"
else
  if grep -q 'SCREEN FAIL' "$TMP/fail.out" && grep -q '^FAIL' "$TMP/fail.out"; then
    pass "测试器 桩失败 ⇒ screen 非零退出并打 SCREEN FAIL"
  else
    bad "screen 非零退出但没打 SCREEN FAIL / FAIL 行"
    sed 's/^/  /' "$TMP/fail.out" | tail -20
  fi
fi

if SGS_PYTEST="$TMP/stub-pass" bash "$SCREEN" >"$TMP/pass.out" 2>&1; then
  pass "全部目标通过 ⇒ screen 退出 0"
else
  bad "全部目标通过时 screen 仍失败（金录像 / 池计数此刻可能是红的）"
  sed 's/^/  /' "$TMP/pass.out" | tail -30
fi

echo
if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
  exit 0
fi
echo "SOME TESTS FAILED"
exit 1
