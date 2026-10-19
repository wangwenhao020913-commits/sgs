#!/usr/bin/env bash
# 环境预检（W5-03 retro）：浏览器自动化与 pytest 是否可用。
# 只打印 WARN/PASS，不阻塞；验收/门禁前跑一次，别到用的时候才发现缺工具。
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

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
