#!/usr/bin/env bash
# 环境预检（W5-03 retro）：浏览器自动化与 pytest 是否可用；共享 .git/config 是否干净。
# 缺工具只 WARN 不阻塞；共享 .git/config 被污染是**硬失败**（退出非零）——它会让全仓
# 所有 git 命令报 `fatal: this operation must be run in a work tree`，且会把你后续的
# 提交 / 门禁全部挡死，所以必须在预检这一层就喊出来。
#
# 为什么需要这条护栏：`SGS-retro` 是 SGS 仓的 **linked worktree**（`.git` 是 gitfile，
# 指向 `SGS/.git/worktrees/SGS-retro`），与主工作树**共用同一份 `.git/config`**。在那边
# 跑 `git init --bare` / `git config core.bare true`（或写 probe 身份）的探针会顺带写坏
# 主工作树的配置（本轮真踩过）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

bare="$(git -C "$ROOT" config --get core.bare 2>/dev/null || true)"
user_name="$(git -C "$ROOT" config --get user.name 2>/dev/null || true)"
user_email="$(git -C "$ROOT" config --get user.email 2>/dev/null || true)"
if [ "$bare" != "false" ] || [ "$user_name" = "probe" ] || [ "$user_email" = "probe@example.invalid" ]; then
  echo "FAIL: 共享 .git/config 被污染：core.bare=${bare:-<unset>} user.name=${user_name:-<unset>} user.email=${user_email:-<unset>}"
  echo "      SGS-retro 是 linked worktree，与 SGS 共用 .git/config；那边的探针会写坏主工作树。"
  echo "      修复："
  echo "        git -C \"$ROOT\" config core.bare false"
  echo "        git -C \"$ROOT\" config user.name dsh-agent"
  echo "        git -C \"$ROOT\" config user.email dsh-agent@localhost"
  exit 1
fi
echo "PASS: 共享 .git/config 干净（core.bare=false，身份不是 probe 探针）"

if command -v bsk >/dev/null 2>&1; then
  echo "PASS: bsk 可用（browser-skill 可驱动浏览器）"
else
  echo "WARN: bsk 未安装——browser-skill 不可用；实机验收由用户手动完成。"
  echo "      安装见 https://github.com/Tencent/BrowserSkill"
fi

if [ -x .venv/bin/pytest ]; then
  echo "PASS: .venv/bin/pytest 可用"
else
  echo "WARN: .venv/bin/pytest 不存在——门禁会失败；先按 AGENTS.md 注意事项 2 建 venv。"
fi

if [ -f tools/acceptance_seed.py ]; then
  echo "PASS: tools/acceptance_seed.py 可用（实机验收找种子）"
else
  echo "WARN: tools/acceptance_seed.py 不存在——实机验收找种子会退化为手翻。"
fi
