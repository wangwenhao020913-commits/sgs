#!/usr/bin/env bash
# 装上本仓库自己的 git hook。每个 clone 跑一次（hook 不进版本库的 .git/hooks，
# 所以用 core.hooksPath 指向版本化的 .githooks/）。
#
#   bash tools/install-hooks.sh
#
# 装上之后（分级在 `tools/gate-staged.sh`）：`src/` / `tests/` 改动在提交与合并前跑全量
# `bash tests/run_all.sh`；`开发文档/` / `GLOSSARY.md` / `AGENTS.md` / `CODING_STANDARDS.md` / `docs/` 改动跑快速文档检查。
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

git config core.hooksPath .githooks
chmod +x .githooks/*

echo "已装：core.hooksPath=$(git config --get core.hooksPath)"
echo "pre-commit / pre-merge-commit：src/tests → run_all.sh；开发文档/GLOSSARY/AGENTS/CODING_STANDARDS/docs → 快速文档检查"
