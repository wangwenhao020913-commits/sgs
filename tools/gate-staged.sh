#!/usr/bin/env bash
# 按**暂存区**路径分级跑门禁（W5-03 retro）：
#
# - src/ 或 tests/          → `bash tests/run_all.sh --quiet`（全量，约 3 分钟；只打摘要）
# - 开发文档/、GLOSSARY.md、AGENTS.md、CODING_STANDARDS.md、docs/
#                           → 快速文档检查（约 1 秒，不跑全量）
# - 其它                    → 跳过
#
# 为什么要有 docs 级：`test_dev_docs.sh` 与 `test_writeback_landing.py` 检查的正是
# `开发文档/`，而旧 hook 对纯文档提交直接跳过 ⇒ 坏账本可以不经门禁落地。
#
# 用法：
#   bash tools/gate-staged.sh --classify            # 打印 docs / full / skip
#   bash tools/gate-staged.sh --dry-run             # 打印将要跑的命令
#   bash tools/gate-staged.sh --run [标签]          # 真跑
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

staged="$(git -c core.quotePath=false diff --cached --name-only)"
PYTEST="$ROOT/.venv/bin/pytest"

classify() {
  local file
  while IFS= read -r file; do
    case "$file" in
      src/* | tests/*) echo full; return 0 ;;
    esac
  done <<<"$staged"
  while IFS= read -r file; do
    case "$file" in
      开发文档/* | GLOSSARY.md | AGENTS.md | CODING_STANDARDS.md | docs/*)
        echo docs
        return 0
        ;;
    esac
  done <<<"$staged"
  echo skip
}

docs_commands() {
  printf '%s\n' \
    "bash tests/test_dev_docs.sh" \
    "bash tests/test_agent_skills.sh" \
    "bash tests/test_agents_md.sh" \
    ".venv/bin/pytest -q tests/test_writeback_landing.py tests/test_dod_m3.py tests/test_dod_m4.py"
}

run_docs() {
  local label="$1" command
  if [ ! -x "$PYTEST" ]; then
    echo "$label：docs 快速检查失败——$PYTEST 不存在或不可执行。"
    echo "  先建项目虚拟环境（见 AGENTS.md 注意事项 2）。"
    return 1
  fi
  while IFS= read -r command; do
    if ! bash -c "$command"; then
      echo "$label：docs 快速检查失败：$command"
      return 1
    fi
  done <<<"$(docs_commands)"
  echo "$label：docs 快速检查通过。"
}

run_full() {
  local label="$1"
  echo "$label：跑 bash tests/run_all.sh --quiet（约 3 分钟）……"
  # --quiet 只收「全量输出」这一层：成功 = 每条检查一行 PASS + 最终结果，失败 = 失败项的
  # 完整输出照旧透出（run_all.sh 先打 `FAIL <测试>` 再 cat 捕获的日志）。门禁判据不变。
  if bash tests/run_all.sh --quiet; then
    echo "$label：通过。"
  else
    echo "$label：门禁失败，本次提交被拒。"
    echo "  修好之后再提交。确要提交未过门禁的状态，用 --no-verify。"
    return 1
  fi
}

mode="${1:---run}"
case "$mode" in
  --classify)
    classify
    ;;
  --dry-run)
    case "$(classify)" in
      full) echo "bash tests/run_all.sh --quiet" ;;
      docs) docs_commands ;;
      skip) echo "skip" ;;
    esac
    ;;
  --run)
    label="${2:-门禁 hook}"
    case "$(classify)" in
      full) run_full "$label" ;;
      docs) run_docs "$label" ;;
      skip) echo "$label：本次改动不需要门禁（跳过）。" ;;
    esac
    ;;
  *)
    echo "用法：bash tools/gate-staged.sh [--classify|--dry-run|--run [标签]]" >&2
    exit 2
    ;;
esac
