#!/usr/bin/env bash
# serve-dev.sh（M5 retro）：SGS Web 服务的本地生命周期。
#
# 用法: bash tools/serve-dev.sh <start|stop|status|restart> [port]
#   start   [port]  起服务并等到端口可连（缺省 8100）
#   stop    [port]  按 runs/serve-<port>.pid 停
#   status  [port]  报 pid / URL（在跑退出 0，没跑退出 1）
#   restart [port]  stop + start
#
# 为什么要这个脚本：实机验收要"服务在跑 / 服务停了 / 端口是谁的"三件事可重复；手敲
# `nohup ... &` 会把 pid 丢在 shell 里。状态只落 gitignored 的 `runs/`（pid + log），
# 不进版本库。真正的服务仍是 tools/sgs_web/serve.py（本脚本不改它的参数语义）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
RUNS="$ROOT/runs"
DEFAULT_PORT=8100
PY="$ROOT/.venv/bin/python"
SERVE="tools/sgs_web/serve.py"

usage() {
  cat >&2 <<'EOF'
用法: bash tools/serve-dev.sh <start|stop|status|restart> [port]
  start   [port]  起服务并等到端口可连（缺省 8100）
  stop    [port]  按 runs/serve-<port>.pid 停
  status  [port]  报 pid / URL（在跑退出 0，没跑退出 1）
  restart [port]  stop + start
EOF
}

cmd="${1:-}"
case "$cmd" in
  start | stop | status | restart) ;;
  *)
    usage
    exit 2
    ;;
esac

port="${2:-$DEFAULT_PORT}"
case "$port" in
  '' | *[!0-9]*)
    echo "FAIL: 端口必须是 1–65535 的整数：$port" >&2
    exit 2
    ;;
esac
port_num=$((10#$port))
if [ "$port_num" -lt 1 ] || [ "$port_num" -gt 65535 ]; then
  echo "FAIL: 端口必须是 1–65535 的整数：$port" >&2
  exit 2
fi

pid_file="$RUNS/serve-$port.pid"
log_file="$RUNS/serve-$port.log"
url="http://127.0.0.1:$port/"
log_rel="runs/serve-$port.log"
pid_rel="runs/serve-$port.pid"

# pid 文件里必须是**一串数字**；空 / 非数字 / 缺失都当没在跑。
read_pid() {
  [ -f "$pid_file" ] || return 1
  local pid
  pid="$(tr -d '[:space:]' <"$pid_file")"
  case "$pid" in
    '' | *[!0-9]*) return 1 ;;
  esac
  printf '%s\n' "$pid"
}

# pid 复用的护栏：只认 cmdline 里带 tools/sgs_web/serve.py 的进程，绝不对别人的进程发信号。
is_our_serve() {
  local pid="$1"
  kill -0 "$pid" 2>/dev/null || return 1
  if [ -r "/proc/$pid/cmdline" ]; then
    tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q "tools/sgs_web/serve.py"
  else
    ps -p "$pid" -o args= 2>/dev/null | grep -q "tools/sgs_web/serve.py"
  fi
}

# 端口可连 = 服务真的起来了（进程还活着但没监听，不算 start 成功）。
port_open() {
  "$PY" - "$port" <<'PY' >/dev/null 2>&1
import socket
import sys

sock = socket.socket()
sock.settimeout(0.3)
try:
    sock.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

start_server() {
  if [ ! -x "$PY" ]; then
    echo "FAIL: 找不到可执行的 $PY（先建项目虚拟环境，见 AGENTS.md 注意事项 2）" >&2
    return 1
  fi
  mkdir -p "$RUNS"
  local existing
  if existing="$(read_pid)" && is_our_serve "$existing"; then
    echo "already running: pid=$existing url=$url log=$log_rel"
    return 0
  fi
  rm -f "$pid_file"
  : >"$log_file" # 每次 start 清空 log：失败时 tail 到的就是这一次的输出
  nohup "$PY" "$SERVE" --host 0.0.0.0 --port "$port" >>"$log_file" 2>&1 &
  local pid=$!
  printf '%s\n' "$pid" >"$pid_file"

  local waited=0
  while [ "$waited" -lt 100 ]; do # 20 s（100 × 0.2 s）
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "FAIL: 服务进程启动即退出（pid=$pid）；log 末尾：" >&2
      tail -n 20 "$log_file" >&2 || true
      rm -f "$pid_file"
      return 1
    fi
    if port_open; then
      echo "started: pid=$pid url=$url port=$port log=$log_rel"
      return 0
    fi
    sleep 0.2
    waited=$((waited + 1))
  done

  echo "FAIL: 等待端口 $port 超时（20 s）；log 末尾：" >&2
  tail -n 20 "$log_file" >&2 || true
  kill "$pid" 2>/dev/null || true
  rm -f "$pid_file"
  return 1
}

stop_server() {
  local pid
  if ! pid="$(read_pid)"; then
    echo "not running: 没有 $pid_rel"
    return 0
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "not running: pid=$pid 已不在（清理 stale $pid_rel）"
    rm -f "$pid_file"
    return 0
  fi
  if ! is_our_serve "$pid"; then
    echo "WARN: pid=$pid 不是本仓的 $SERVE（pid 被复用？）；只清理 pid 文件，不发信号" >&2
    rm -f "$pid_file"
    return 1
  fi

  kill "$pid" 2>/dev/null || true
  local waited=0
  while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 25 ]; do # 5 s
    sleep 0.2
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "WARN: pid=$pid 5 s 内未退出，发送 SIGKILL" >&2
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$pid_file"
  echo "stopped: pid=$pid port=$port"
}

status_server() {
  local pid
  if pid="$(read_pid)" && is_our_serve "$pid"; then
    if port_open; then
      echo "running: pid=$pid url=$url port=$port log=$log_rel"
    else
      echo "running: pid=$pid（端口 $port 连不上：启动中 / 已挂？）url=$url log=$log_rel"
    fi
    return 0
  fi
  if pid="$(read_pid)"; then
    echo "not running: pid=$pid 已不在（stale $pid_rel）"
  else
    echo "not running: 没有 $pid_rel"
  fi
  return 1
}

case "$cmd" in
  start) start_server ;;
  stop) stop_server ;;
  status) status_server ;;
  restart)
    stop_server || true
    start_server
    ;;
esac
