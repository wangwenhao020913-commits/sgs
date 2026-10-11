#!/usr/bin/env bash
# 装上本仓库自己的 git hook。每个 clone 跑一次（hook 不进版本库的 .git/hooks，
# 所以用 core.hooksPath 指向版本化的 .githooks/）。
#
#   bash tools/install-hooks.sh
#
# 装上之后：改动触及 src/ 或 tests/ 时，提交前会自动跑 `bash tests/run_all.sh`。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

git config core.hooksPath .githooks
chmod +x .githooks/*

echo "已装：core.hooksPath=$(git config --get core.hooksPath)"
echo "pre-commit 在改动触及 src/ 或 tests/ 时会跑 bash tests/run_all.sh"
