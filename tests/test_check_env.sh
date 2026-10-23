#!/usr/bin/env bash
# 环境预检的 smoke：缺工具只 WARN、不阻塞；共享 .git/config 被污染必须**硬失败**。
# 反面自检在临时仓库里做（不碰真仓库的 config）：core.bare=true / probe 身份都要判红。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

fail=0
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

out="$(bash tools/check_env.sh)" || { echo "FAIL: 真仓库上 check_env 非零"; fail=1; }
grep -q 'bsk' <<<"$out" || { echo "FAIL: check_env 缺 bsk 提示"; fail=1; }
grep -q 'venv/bin' <<<"$out" || { echo "FAIL: check_env 缺 venv 提示"; fail=1; }
grep -q 'acceptance_seed.py' <<<"$out" || { echo "FAIL: check_env 缺 acceptance_seed 提示"; fail=1; }
grep -q 'PASS: 共享 .git/config 干净' <<<"$out" || { echo "FAIL: check_env 没报共享 config 干净"; fail=1; }

# 反面自检：脚本复制进临时仓库，污染共享 config 后必须判红并给出修复命令。
mkdir -p "$tmp/tools"
cp tools/check_env.sh "$tmp/tools/check_env.sh"
git -C "$tmp" init -q

assert_red() { # $1=场景名
  local name="$1" red_out
  if red_out="$(bash "$tmp/tools/check_env.sh" 2>&1)"; then
    echo "FAIL: $name 时 check_env 没有失败"; fail=1
  fi
  grep -q 'FAIL: 共享 .git/config 被污染' <<<"$red_out" \
    || { echo "FAIL: $name 时缺污染 FAIL 行"; fail=1; }
  grep -q 'core.bare false' <<<"$red_out" \
    || { echo "FAIL: $name 时缺修复命令"; fail=1; }
}

git -C "$tmp" config core.bare true
assert_red "core.bare=true"

git -C "$tmp" config core.bare false
git -C "$tmp" config user.name probe
git -C "$tmp" config user.email probe@example.invalid
assert_red "probe 探针身份"

git -C "$tmp" config user.name dsh-agent
git -C "$tmp" config user.email dsh-agent@localhost
if ! clean_out="$(bash "$tmp/tools/check_env.sh" 2>&1)"; then
  echo "FAIL: 干净临时仓库上 check_env 非零"; fail=1
fi
grep -q 'PASS: 共享 .git/config 干净' <<<"$clean_out" \
  || { echo "FAIL: 干净临时仓库没报共享 config 干净"; fail=1; }

if [ "$fail" -eq 0 ]; then
  echo "PASS: check_env 提示 bsk / venv / acceptance_seed，且共享 .git/config 污染判红"
fi
exit "$fail"
