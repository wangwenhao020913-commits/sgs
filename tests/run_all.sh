#!/usr/bin/env bash
# 运行 SGS 项目的全部检查：全部 shell 校验 + Python 测试。
# 唯一门禁：任一阶段失败即整体失败；聚合全部失败、不 fail-fast。
#
# 用法：
#   bash tests/run_all.sh            # 全量输出（缺省：每条检查的 PASS 行与 pytest 输出都打）
#   bash tests/run_all.sh --quiet    # 只打每条检查的 PASS/FAIL 与最终结果；失败时补打失败项
#                                    # 的完整输出。hook（gate-staged.sh）用这个，免得逐条
#                                    # PASS 与逐请求访问日志把 agent 的上下文刷掉。
set -euo pipefail

TESTS_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$TESTS_DIR/.." && pwd)"
cd "$TESTS_DIR"

QUIET=0
for arg in "$@"; do
  case "$arg" in
    --quiet) QUIET=1 ;;
    *)
      echo "用法：bash tests/run_all.sh [--quiet]" >&2
      exit 2
      ;;
  esac
done

LOG_DIR=""
cleanup() {
  if [ -n "$LOG_DIR" ]; then
    rm -rf "$LOG_DIR"
  fi
}
trap cleanup EXIT
if [ "$QUIET" -eq 1 ]; then
  LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sgs-gate.XXXXXX")"
fi

# 门禁本身跑得动，但"提交 / 合并时自动跑"靠的是 core.hooksPath。没装 = 只有人手动跑时才跑，
# 而本仓没有 CI——"没人跑它等于零"（见 .githooks/pre-commit 的注释）。
# 放在**开头**：先提醒再跑，别让 agent 等完三分钟才看到。
if [ "$(git -C "$ROOT" config --get core.hooksPath || true)" != ".githooks" ]; then
  echo "⚠️  提醒：core.hooksPath 未指向 .githooks —— 改动提交 / 合并时不会自动触发本门禁。"
  echo "    跑一次：bash tools/install-hooks.sh"
  echo
fi

fail=0

for t in test_*.sh; do
  if [ "$QUIET" -eq 1 ]; then
    # 每条检查的输出先收进临时日志：成功只留一行状态，失败再把这一段原样打出来。
    if bash "$t" >"$LOG_DIR/$t.log" 2>&1; then
      echo "PASS  $t"
    else
      echo "FAIL  $t"
      echo "--- $t 输出（失败） ---"
      cat "$LOG_DIR/$t.log"
      fail=1
    fi
    continue
  fi
  echo "=== 运行 $t ==="
  if bash "$t"; then
    echo "--- $t 通过 ---"
  else
    echo "--- $t 失败 ---"
    fail=1
  fi
  echo
done

# Python 测试阶段。门禁只认项目虚拟环境里的 pytest：它缺失就是**失败**，
# 不回退到环境里碰巧存在的 pytest —— 那会把"没建 venv"悄悄放过去。
pytest_cmd="$ROOT/.venv/bin/pytest"

if [ "$QUIET" -eq 0 ]; then
  echo "=== 运行 pytest ==="
fi
if [ ! -x "$pytest_cmd" ]; then
  if [ "$QUIET" -eq 1 ]; then
    echo "FAIL  pytest"
  fi
  echo "--- pytest 失败：$pytest_cmd 不存在或不可执行 ---"
  echo "    先建立项目虚拟环境并安装 dev 依赖，例如："
  echo "    python3 -m venv --without-pip .venv"
  echo "    python3 -m pip --python .venv/bin/python install -e '.[dev]'"
  fail=1
elif [ "$QUIET" -eq 1 ]; then
  if (cd "$ROOT" && "$pytest_cmd" -q) >"$LOG_DIR/pytest.log" 2>&1; then
    echo "PASS  pytest"
  else
    echo "FAIL  pytest"
    echo "--- pytest 输出（失败） ---"
    cat "$LOG_DIR/pytest.log"
    fail=1
  fi
elif (cd "$ROOT" && "$pytest_cmd" -q); then
  echo "--- pytest 通过 ---"
else
  echo "--- pytest 失败 ---"
  fail=1
fi
echo

if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
else
  echo "SOME TESTS FAILED"
fi
exit "$fail"
