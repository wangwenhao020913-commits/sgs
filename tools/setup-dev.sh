#!/usr/bin/env bash
# 新机器一次性装配（**幂等**，重复跑无害）。装完之后，日常换机器只剩两条命令：
#
#   出发前：git add -A && git commit -m "wip: 换机" && git push
#   到达后：git pull
#
# 用法：
#   bash tools/setup-dev.sh
#
# 做四件事（每一条都是"clone 不会带过去、但门禁依赖它"的东西）：
#   1) 关掉换行转换：Git for Windows 默认 core.autocrlf=true，会把 tests/replays/*.jsonl
#      转成 CRLF，而金录像是**逐字节**判据 ⇒ 会红一片。`.gitattributes` 已兜底，这里再钉一次本地配置。
#   2) 装 git hook：`core.hooksPath` 是**本地配置**、不随 clone 走；不装 = 提交时门禁静默不跑
#      （提交时的唯一自动入口；CI 只调 run_all.sh，判据同一条）。
#   3) 缺 `.venv` 就建：门禁只认 `$ROOT/.venv/bin/pytest`，**不回退系统 pytest**。
#   4) 跑一次环境预检 `tools/check_env.sh`（共享 .git/config 护栏 + bsk + pytest 可用性）。
#
# 平台：**只在 WSL / Linux / macOS 里跑**。门禁是 bash + `.venv/bin/` Unix 路径，Windows 原生跑不了。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

case "$(uname -s 2>/dev/null || echo unknown)" in
  Linux | Darwin) ;;
  *)
    echo "FAIL: 本脚本只支持 WSL / Linux / macOS（门禁是 bash + .venv/bin/ 路径）。" >&2
    echo "      Windows 上请在 WSL 里执行：wsl -d <发行版> --cd <repo> bash tools/setup-dev.sh" >&2
    exit 1
    ;;
esac

echo "== 1/4 换行转换 =="
git config core.autocrlf false
echo "PASS: core.autocrlf=false（仓库内还有 .gitattributes 兜底）"

echo "== 2/4 git hook =="
bash tools/install-hooks.sh

echo "== 3/4 .venv =="
if [ -x .venv/bin/pytest ]; then
  echo "PASS: .venv/bin/pytest 已存在，跳过创建"
else
  PYTHON="${PYTHON:-python3}"
  if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "FAIL: 找不到 $PYTHON（可用 PYTHON=/path/to/python3.14 bash tools/setup-dev.sh 指定）" >&2
    exit 1
  fi
  if ! "$PYTHON" - <<'PY'
import sys

if sys.version_info < (3, 14):
    sys.exit("需要 Python >= 3.14，当前 %d.%d" % sys.version_info[:2])
PY
  then
    echo "FAIL: Python 版本不满足 pyproject.toml 的 requires-python（>=3.14）" >&2
    exit 1
  fi
  if ! "$PYTHON" -m venv .venv; then
    echo "FAIL: 建 .venv 失败——Debian/Ubuntu 需要先装 venv 模块：sudo apt install python3-venv" >&2
    exit 1
  fi
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -e ".[dev]"
  echo "PASS: .venv 建好（pydantic + pytest + pytest-timeout）"
fi

echo "== 4/4 环境预检 =="
bash tools/check_env.sh

cat <<'TIP'

装配完成。下一步只有一条：

  bash tests/run_all.sh        # 全量门禁（≈8 分钟；建议后台跑），确认基线绿再开工

之后换机器只需要：

  出发前  git add -A && git commit -m "wip: 换机" && git push
  到达后  git pull            # 若这台机器是新的，先跑一次本脚本

票面（跨机交接用）：读 .scratch/table-ux-overhaul/spec.md 与 map.md，按 issues/01…14 顺序。
TIP
