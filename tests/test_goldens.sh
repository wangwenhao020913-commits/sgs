#!/usr/bin/env bash
# 金录像命令的**门禁自检**（M5 retro：spec 里的 `git diff --exit-code -- tests/replays` 证明不了
# "47 条逐字节不变"——它在工作树干净时对**任何**改动都空过，且把新增录像也判错）。
#
# 两层判据：
#   ① 真 repo：`tools/check_goldens.sh` 跑 `tests/test_replays.py` 的 47 条冻结字节断言 → PASS；
#   ② 合成反例：临时 git 仓库里
#        - 新增一条 .jsonl（允许）→ PASS；
#        - 改动一条**既有** .jsonl → FAIL 且非零退出。
# 探针只建在 $TMPDIR（`probe.sh` 的纪律），不碰本工作树。
set -euo pipefail
cd "$(dirname "$0")/.."

# 门禁 hook 会导出 GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE（本仓是 linked worktree）。
# 它们指向**本仓库**；下面的临时 git 仓库探针若不隔离，`git init/add/commit` 会改到本仓库
# ——真踩过：把暂存区 reset 掉、并把合成 README / a.jsonl 提交到 retro 分支。先清掉。
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_OBJECT_DIRECTORY
unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE

# shellcheck source=tests/support/probe.sh
source tests/support/probe.sh

TOOL="$PWD/tools/check_goldens.sh"
fail=0
HEAD_BEFORE="$(git rev-parse HEAD)"
REPO_TREE_BEFORE="$(repo_tree_snapshot)"

if [ ! -f "$TOOL" ]; then
  echo "FAIL: 缺少金录像命令：tools/check_goldens.sh"
  exit 1
fi

# ① 真 repo：47 条冻结字节 + 输出一行 PASS。
real_out="$(bash "$TOOL" 2>&1)" || {
  printf '%s\n' "$real_out"
  echo "FAIL: tools/check_goldens.sh 在真 repo 上失败"
  exit 1
}
if ! printf '%s\n' "$real_out" | grep -q '^PASS: '; then
  printf '%s\n' "$real_out"
  echo "FAIL: tools/check_goldens.sh 没有打印 PASS 行"
  fail=1
fi

# ② 合成反例。$1=add|mutate；设 PROBE_OUT / PROBE_STATUS。
run_base_probe() { # $1=add|mutate
  local probe base
  # 双保险：调用方若从 hook 环境带进 GIT_*，这里再清一次（见文件头的事故注释）。
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_PREFIX GIT_OBJECT_DIRECTORY
  unset GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_NAMESPACE
  probe="$(probe_mktemp)" || return 1
  mkdir -p "$probe/tests/replays"
  set +e
  (
    cd "$probe" || exit 90
    git init -q || exit 91
    git config user.email probe@example.invalid || exit 92
    git config user.name probe || exit 93
    printf '{}\n' > tests/replays/a.jsonl
    git add tests/replays/a.jsonl || exit 94
    git commit -qm base || exit 95
    base="$(git rev-parse HEAD)" || exit 96
    case "$1" in
      add) printf '{}\n' > tests/replays/b.jsonl ;;
      mutate) printf '{"changed":true}\n' > tests/replays/a.jsonl ;;
    esac
    GOLDENS_BASE_ONLY=1 bash "$TOOL" "$base" 2>&1
  ) > "$probe/out" 2>&1
  PROBE_STATUS=$?
  set -e
  PROBE_OUT="$(cat "$probe/out")"
  rm -rf "$probe"
}

PROBE_STATUS=0
PROBE_OUT=""
run_base_probe add || true
if [ "$PROBE_STATUS" -ne 0 ] || ! printf '%s\n' "$PROBE_OUT" | grep -q '^PASS: '; then
  echo "FAIL: 合成反例未变绿：新增 .jsonl 应允许，命令却失败（exit $PROBE_STATUS）"
  printf '%s\n' "$PROBE_OUT"
  fail=1
fi

PROBE_STATUS=0
PROBE_OUT=""
run_base_probe mutate || true
if [ "$PROBE_STATUS" -eq 0 ] || ! printf '%s\n' "$PROBE_OUT" | grep -q '^FAIL: '; then
  echo "FAIL: 合成反例未变红：既有 .jsonl 被修改，命令却通过（exit $PROBE_STATUS）"
  printf '%s\n' "$PROBE_OUT"
  fail=1
fi

# ③ 回归：hook 环境（GIT_DIR / GIT_INDEX_FILE 指向本仓库）下，同一探针仍只动临时仓库。
# 这段曾经真炸过：linked worktree 的 pre-commit hook 导出 GIT_DIR + GIT_INDEX_FILE 后，
# 探针的 git init/add/commit 落到了本仓库，把暂存区 reset、并往当前分支提交了合成文件。
if ! (
  export GIT_DIR="$PWD/.git" GIT_WORK_TREE="$PWD" GIT_INDEX_FILE=.git/index GIT_PREFIX=
  PROBE_STATUS=0
  PROBE_OUT=""
  run_base_probe add || true
  [ "$PROBE_STATUS" -eq 0 ] || exit 1
  printf '%s\n' "$PROBE_OUT" | grep -q '^PASS: ' || exit 1
); then
  echo "FAIL: hook 环境（GIT_DIR / GIT_INDEX_FILE）下探针没有隔离临时仓库"
  fail=1
fi

if [ "$(git rev-parse HEAD)" != "$HEAD_BEFORE" ]; then
  echo "FAIL: 合成反例改动了本仓库的 HEAD（$HEAD_BEFORE → $(git rev-parse HEAD)）——GIT_DIR 隔离失效"
  fail=1
fi

if ! probe_discipline_check "$REPO_TREE_BEFORE"; then fail=1; fi
if ! probe_discipline_selftest; then fail=1; fi

if [ "$fail" -eq 0 ]; then
  echo "PASS: 金录像命令自检全部通过"
fi
exit "$fail"
