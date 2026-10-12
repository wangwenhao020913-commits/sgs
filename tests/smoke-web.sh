#!/usr/bin/env bash
# 前端快速回路：起一个临时服务 → 跑**真的 app.js** 的渲染烟测 → 收尾。
#
# 为什么单独有这一条：`tests/test_web.sh` 是端到端门禁（起服务 + 跑完整整一局 + 409 判定 +
# 跨机可达），跑一次要几十秒；而改 `tools/sgs_web/static/` 或 `app.js` 时真正要的是
# "改一行、看一眼"。没有这个入口时，调试要么手工起 `serve.py`（要自己挑端口、自己收尾，
# 端口占用与"进程随 shell 一起死"会反复打断），要么每次付完整门禁的代价。
#
# **它不在 `bash tests/run_all.sh` 里**：`test_web.sh` 第 ⑥ 步已经跑同一个 `ui_smoke.mjs`，
# 再挂一条只会让门禁变慢。这是**给人用的快回路**，不是新增判据。
#
# 用法：
#   bash tests/smoke-web.sh              # 全套烟测
#   bash tests/smoke-web.sh --verbose    # 打印每个 PASS 行（默认只打印失败与总结）
#
# 纪律同 `test_web.sh`：只监听 127.0.0.1、端口 0（内核分配）、录像写临时目录、退出必杀服务。
set -uo pipefail
cd "$(dirname "$0")/.."

VERBOSE=0
for arg in "$@"; do
  case "$arg" in
    --verbose|-v) VERBOSE=1 ;;
    *) echo "未知参数：$arg（可用：--verbose）"; exit 2 ;;
  esac
done

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "FAIL: 找不到 $PYTHON（先建虚拟环境，见 tests/run_all.sh 的提示）"
  exit 1
fi
if ! command -v node >/dev/null 2>&1; then
  echo "FAIL: 前端冒烟需要 node 运行时，PATH 里找不到 node"
  exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/sgs-smoke.XXXXXX")"
LOG="$TMP/serve.log"
RUNS="$TMP/runs"
mkdir -p "$RUNS"
PID=""

cleanup() {
  if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

"$PYTHON" tools/sgs_web/serve.py --port 0 --runs-dir "$RUNS" > "$LOG" 2>&1 &
PID=$!

PORT=""
for _ in $(seq 1 60); do
  PORT="$(sed -n 's/.*127\.0\.0\.1:\([0-9][0-9]*\).*/\1/p' "$LOG" 2>/dev/null | head -1)"
  [ -n "$PORT" ] && break
  if ! kill -0 "$PID" 2>/dev/null; then break; fi
  sleep 0.2
done

if [ -z "$PORT" ]; then
  echo "FAIL: 服务没有在预期时间内打印监听端口"
  echo "--- 服务输出 ---"
  cat "$LOG" 2>/dev/null || true
  exit 1
fi
echo "服务已监听 127.0.0.1:$PORT（临时录像目录，退出即清理）"

OUT="$TMP/smoke.out"
if node tests/support/ui_smoke.mjs "http://127.0.0.1:$PORT" > "$OUT" 2>&1; then
  if [ "$VERBOSE" -eq 1 ]; then cat "$OUT"; else grep -E "FAIL|全部通过|项失败" "$OUT" || true; fi
  echo "PASS: 前端渲染冒烟"
  exit 0
fi

# 失败时把全部输出打出来（含每条断言的 detail），别让人再去猜
cat "$OUT"
echo "--- 服务输出（末 20 行）---"
tail -20 "$LOG" 2>/dev/null || true
exit 1
