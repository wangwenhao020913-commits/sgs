#!/usr/bin/env bash
# 金录像命令（M5 retro）：把 spec 里那条**不可证伪**的
# `git diff --exit-code -- tests/replays` 换成两条真判据。
#
#   （a）47 条标准 108 金录像**逐字节不变**——直接跑
#        `tests/test_replays.py::test_the_47_standard_108_replays_keep_their_frozen_bytes`
#        （冻结表在测试里，命令不另抄一份 sha）；
#   （b）若传入基准 ref（`$1`）：相对它**没有既有** `.jsonl` 被修改
#        （`--diff-filter=M`；**新增允许**——M5 自己就新增了 8 条 160 档基准）。
#        删除由 (a) 的"基准文件缺失"兜住。
#
# 为什么旧命令是错的：工作树干净时 `git diff --exit-code -- tests/replays` 对任何改动都
# 空过（它只 diff 工作树），而新增录像又会被判成失败。门禁要的是"旧字节没变、新录像可以有"。
#
# 用法：
#   bash tools/check_goldens.sh              # 只跑 (a)
#   bash tools/check_goldens.sh <base-ref>   # 跑 (a) + (b)，例如 bash tools/check_goldens.sh master
#   GOLDENS_BASE_ONLY=1 bash tools/check_goldens.sh <base-ref>
#                                            # 只跑 (b)（测试合成反例用，不跑 pytest）
#
# 输出最后一行一定是 `PASS: ...` / `FAIL: ...`；任一判据失败即非零退出。
set -euo pipefail

# 本命令在 pre-commit hook 里也可能被调用：linked worktree 的 hook 会导出 GIT_DIR /
# GIT_WORK_TREE / GIT_INDEX_FILE。它们指向**调用者仓库**；先清掉，git 从 cwd 自行发现，
# 保证"相对 base ref 的 diff"永远作用于当前仓库（合成反例还要求它作用于临时仓库）。
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_OBJECT_DIRECTORY
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

BASE="${1:-}"
PYTEST="$ROOT/.venv/bin/pytest"
GOLDEN_TEST="tests/test_replays.py::test_the_47_standard_108_replays_keep_their_frozen_bytes"
fail=0

if [ "${GOLDENS_BASE_ONLY:-0}" != 1 ]; then
  if [ ! -x "$PYTEST" ]; then
    echo "FAIL: 找不到可执行的 $PYTEST（先建项目虚拟环境，见 AGENTS.md 注意事项 2）"
    exit 1
  fi
  if pytest_out="$("$PYTEST" -q "$GOLDEN_TEST" 2>&1)"; then
    :
  else
    printf '%s\n' "$pytest_out"
    echo "FAIL: 47 条标准 108 金录像的冻结字节不符（与 tests/test_replays.py 同一条判据）"
    fail=1
  fi
fi

if [ -n "$BASE" ]; then
  if ! git rev-parse --verify --quiet "$BASE^{commit}" >/dev/null; then
    echo "FAIL: 基准 ref 不存在或不是 commit：$BASE"
    fail=1
  elif ! git diff --quiet --diff-filter=M "$BASE" -- 'tests/replays/*.jsonl'; then
    echo "FAIL: 相对 $BASE 有既有 .jsonl 被修改（新增允许，改旧基准不许）："
    git diff --name-only --diff-filter=M "$BASE" -- 'tests/replays/*.jsonl' | sed 's/^/  - /'
    fail=1
  fi
fi

if [ "$fail" -ne 0 ]; then
  echo "FAIL: 金录像检查未通过"
  exit 1
fi

summary="PASS: 47 条标准 108 金录像逐字节不变"
if [ -n "$BASE" ]; then
  summary="$summary；相对 $BASE 无既有 .jsonl 被修改（新增允许）"
fi
echo "$summary"
