#!/usr/bin/env bash
# 要对照基线（"改前红不红"）时的**安全通道**：在仓外另开一棵 worktree。
#
# 为什么需要它：直接在当前工作树里 stash 做对照，等于把你未提交的活押在一次可回滚操作上
# —— 本仓真发生过一次（票 13 的实现者把 30 分钟的改动从工作树里清掉，靠 `git fsck`
# 找回游离对象才救回）。另开 worktree 则**一次都不碰**你手上的工作树。
#
# 用法：
#   bash tools/baseline-worktree.sh              # 以 HEAD 为基线
#   bash tools/baseline-worktree.sh <rev>        # 以某个提交 / 分支 / tag 为基线
#   收尾：git worktree remove <打印出来的路径>
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"
rev="${1:-HEAD}"
sha="$(git -C "$ROOT" rev-parse --short "$rev")"
dir="$(dirname "$ROOT")/$(basename "$ROOT")-baseline-$sha"

if [ -e "$dir" ]; then
  echo "基线 worktree 已存在：$dir"
else
  git -C "$ROOT" worktree add --detach "$dir" "$rev" >/dev/null
  echo "已建基线 worktree：$dir（rev=$sha）"
fi

echo "在那棵树里跑你的对照实验（在那里装的 hook / 建的 venv 与主树无关）；"
echo "收尾： git worktree remove '$dir'"
