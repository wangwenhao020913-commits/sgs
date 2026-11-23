#!/usr/bin/env bash
# 票档族表（ADR-030）：票档必须是**全量档的真子集**，统计类**不进票档**，未登记的档名 /
# 未登记的路径一律 fail-closed。
#
# 判据：
#   1) 四个票档都能展开，且每个登记的文件真实存在、都在 `tests/` 下；
#   2) 票档 ⊆ 全量档：票档里的每个文件都在全量档的收集集合里（`find tests -name 'test_*.py'`）；
#   3) 统计类不在任何票档里（它们留在全量档、独立成片、上限 600 s）；
#   4) 未知档名 ⇒ 非零退出（**不许**静默按全量跑，也不许静默跑空）；
#   5) `gate-staged.sh` 真的按风险面给出票档，且认不出的路径回落到 full。
set -euo pipefail
# hook 会导出 GIT_*（linked worktree）；本文件用 `git -C "$tmp"`，不隔离就会打到本仓暂存区。
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_OBJECT_DIRECTORY
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ALL="$ROOT/tests/run_all.sh"
TIERS=(engine web ui content)
fail=0
bad() {
  echo "FAIL: $*"
  fail=1
}

# ---- 1) 展开 + 文件存在 + 都在 tests/ 下 ----
declare -A TIER_LIST=()
for tier in "${TIERS[@]}"; do
  if ! out="$(bash "$RUN_ALL" --list-tier "$tier")"; then
    bad "票档 $tier 展开失败"
    continue
  fi
  [ -n "$out" ] || bad "票档 $tier 是空的"
  TIER_LIST[$tier]="$out"
  while IFS= read -r f; do
    [ -f "$ROOT/$f" ] || bad "票档 $tier 登记了不存在的文件：$f"
    case "$f" in
      tests/*) ;;
      *) bad "票档 $tier 出现 tests/ 之外的文件：$f" ;;
    esac
  done <<<"$out"
done

# ---- 2) 票档 ⊆ 全量档 ----
mapfile -t all_files < <(cd "$ROOT" && find tests -name 'test_*.py' | sort)
[ "${#all_files[@]}" -gt 0 ] || bad "全量档一个测试文件都没枚举到"
# 成员判定读**一份**字符串而不是 `printf | grep -q`：grep 命中即关管道，`pipefail` 下
# printf 会拿到 SIGPIPE、整条管道因此报假失败（本票实跑撞到过一次）。
all_joined="$(printf '%s\n' "${all_files[@]}")"
for tier in "${TIERS[@]}"; do
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    grep -qxF "$f" <<<"$all_joined" ||
      bad "票档 $tier 的 $f 不在全量档的收集集合里（票档必须是子集，不许新造判据）"
  done <<<"${TIER_LIST[$tier]:-}"
done

# ---- 3) 统计类不进票档 ----
STATS=(tests/test_heuristic_ai.py tests/test_all_ai_games.py)
for heavy in "${STATS[@]}" tests/test_fuzz.py tests/test_determinism.py; do
  for tier in "${TIERS[@]}"; do
    if grep -qxF "$heavy" <<<"${TIER_LIST[$tier]:-}"; then
      bad "$heavy 出现在票档 $tier（统计类 / 慢族只留全量档）"
    fi
  done
done

# ---- 4) 未知档名 fail-closed ----
if bash "$RUN_ALL" --list-tier bogus >/dev/null 2>&1; then
  bad "未知档名 --list-tier bogus 没有失败"
fi
if bash "$RUN_ALL" --tier bogus --quiet >/dev/null 2>&1; then
  bad "未知档名 --tier bogus 没有失败（静默按全量 / 跑空都不行）"
fi

# ---- 5) gate-staged.sh 按风险面给票档（临时仓库；只跑 --classify，不跑门禁的 Python 阶段） ----
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/tools" "$tmp/tests" "$tmp/src/sgs/engine" "$tmp/src/sgs/data" \
  "$tmp/tools/sgs_web/static" "$tmp/开发文档"
cp "$ROOT/tools/gate-staged.sh" "$tmp/tools/gate-staged.sh"
cp "$RUN_ALL" "$tmp/tests/run_all.sh"
# 族表里登记的每个文件都造一个空壳（`--list-tier` 只展开、不跑，空文件足够）
for tier in "${TIERS[@]}"; do
  while IFS= read -r f; do
    mkdir -p "$tmp/$(dirname "$f")"
    : >"$tmp/$f"
  done <<<"${TIER_LIST[$tier]:-}"
done
git -C "$tmp" init -q
git -C "$tmp" config user.email test@example.com
git -C "$tmp" config user.name test

classify_of() { # $@ = 要暂存的路径
  # 清空暂存区：临时仓库还没有任何提交，`git reset` 会因没有 HEAD 而失败。
  git -C "$tmp" read-tree --empty
  for p in "$@"; do
    mkdir -p "$tmp/$(dirname "$p")"
    : >"$tmp/$p"
    git -C "$tmp" add "$p"
  done
  ( cd "$tmp" && bash tools/gate-staged.sh --classify )
}

expect() { # $1 = 期望，其余 = 路径
  local want="$1"
  shift
  local got
  got="$(classify_of "$@")"
  [ "$got" = "$want" ] || bad "$* 应分到 $want，实际 $got"
}

expect "tier:engine" src/sgs/engine/game.py
expect "tier:engine" src/sgs/skills/active.py
expect "tier:engine" tests/test_hero_catalog.py
expect "tier:content" src/sgs/data/heroes.json
expect "tier:ui" tools/sgs_web/static/table.js
expect "tier:web" tools/sgs_web/projection.py
expect "tier:ui" tests/test_web_static.py
expect "tier:engine,ui" src/sgs/engine/game.py tools/sgs_web/static/table.js
# 未登记的：工具脚本 / 新顶层目录 / 没进任何票档的测试文件 ⇒ 全量档
expect "full" tools/pool_census.py
expect "full" tests/test_fuzz.py
expect "full" some-new-top-dir/x.py
expect "docs" AGENTS.md
expect "skip" README.md

if [ "$fail" -eq 0 ]; then
  echo "PASS: 票档族表是子集、统计类不在票档、未知档名 fail-closed、分级按风险面"
fi
exit "$fail"
