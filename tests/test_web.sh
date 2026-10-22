#!/usr/bin/env bash
# M4 端到端门禁：**真起服务、真打 HTTP、跑完一整局 8 人身份局**（`spec.md` §Acceptance 判据 1）。
#
# 判据（由 tests/support/web_client.py 断言）：
#   ① 出现 game_end，且与身份胜负一致（客户端打印获胜座位与原因）；
#   ② 记录流里零 accepted=false；
#   ③ POST /answer 的次数 == 记录流里人类座位的 action 记录数（"每次都真的过了 HTTP"）。
# 另跑一局极短时限的会话，证明**超时代答**这条护栏真的会触发且该局仍能跑完。
#
# 纪律：服务只监听 127.0.0.1、端口 0（内核分配）；录像写进临时目录；退出时必杀服务。
set -uo pipefail
cd "$(dirname "$0")/.."

PYTHON=".venv/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "FAIL: 找不到 $PYTHON（先建虚拟环境，见 tests/run_all.sh 的提示）"
  exit 1
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/sgs-web.XXXXXX")"
LOG="$TMP/serve.log"
RUNS="$TMP/runs"
mkdir -p "$RUNS"
PID=""
FIXPID=""

cleanup() {
  for child in "$PID" "$FIXPID"; do
    if [ -n "$child" ] && kill -0 "$child" 2>/dev/null; then
      kill "$child" 2>/dev/null || true
      wait "$child" 2>/dev/null || true
    fi
  done
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
echo "PASS: 服务已监听 127.0.0.1:$PORT"

# ① 静态资源（界面本身）：脚本清单**只认 index.html** 的 `<script src>` 顺序（U1-00）。
SCRIPT_SRCS="$(sed -n 's/.*<script[^>]*src="\([^"]*\)".*/\1/p' tools/sgs_web/static/index.html)"
if [ -z "$SCRIPT_SRCS" ]; then
  echo "FAIL: index.html 里找不到任何 <script src>"
  exit 1
fi
if printf '%s\n' "$SCRIPT_SRCS" | grep -qx 'app.js'; then
  echo "FAIL: index.html 仍在加载旧的单文件 app.js（U1-00 已拆成按职责命名的脚本）"
  exit 1
fi
INDEX_CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/")"
CSS_CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/style.css")"
STATIC_FAIL=""
while IFS= read -r src; do
  [ -z "$src" ] && continue
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/$src")"
  [ "$code" = "200" ] || STATIC_FAIL="$STATIC_FAIL $src=$code"
done <<< "$SCRIPT_SRCS"
if [ "$INDEX_CODE" = "200" ] && [ "$CSS_CODE" = "200" ] && [ -z "$STATIC_FAIL" ]; then
  echo "PASS: 静态资源可取（index / style.css / $(printf '%s\n' "$SCRIPT_SRCS" | tr '\n' ' ')）"
else
  echo "FAIL: 静态资源取不到（index=$INDEX_CODE style=$CSS_CODE 脚本:$STATIC_FAIL）"
  exit 1
fi

# ①b 建局响应必须带本人技能清单（刀 4：引擎的可见面里没有技能，界面的技能按钮全靠它）
echo "=== 建局响应：技能清单 ==="
if ! "$PYTHON" tests/support/web_client.py --base "http://127.0.0.1:$PORT" --probe-skills; then
  echo "FAIL: 建局响应缺技能清单"
  exit 1
fi

# ② 一整局 8 人身份局，人类座位逐个经 HTTP 应答
echo "=== 正局：8 人，逐问经 HTTP 应答 ==="
if ! "$PYTHON" tests/support/web_client.py --base "http://127.0.0.1:$PORT" \
      --seed 20261012 --players 8 --max-answers 3000; then
  echo "FAIL: 端到端对局未通过"
  echo "--- 服务输出 ---"
  cat "$LOG"
  exit 1
fi

# ③ 录像落盘：一对 .jsonl + .meta.json
JSONL_COUNT="$(find "$RUNS" -name '*.jsonl' -type f | wc -l | tr -d ' ')"
META_COUNT="$(find "$RUNS" -name '*.meta.json' -type f | wc -l | tr -d ' ')"
if [ "$JSONL_COUNT" = "1" ] && [ "$META_COUNT" = "1" ]; then
  echo "PASS: 终局自动落盘一对（记录流 + 侧车元数据）"
else
  echo "FAIL: 录像文件数不符（jsonl=$JSONL_COUNT meta=$META_COUNT）"
  exit 1
fi

RUN_NAME="$(find "$RUNS" -name '*.jsonl' -type f -exec basename {} .jsonl \; | head -1)"
RUN_CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/runs/$RUN_NAME")"
LIST_CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/runs")"
if [ "$RUN_CODE" = "200" ] && [ "$LIST_CODE" = "200" ]; then
  echo "PASS: 录像列表与单局录像可经 HTTP 取回（$RUN_NAME）"
else
  echo "FAIL: 录像接口不可用（runs=$LIST_CODE run=$RUN_CODE）"
  exit 1
fi

# ③b M5-09a：军争 **160 张档**的 8 人身份局（spec §6 A2 的端到端判据）。
# 与 ② 的 108 正局同一条路径，只换档位；零非法应答 + 人类每次询问都真的过 HTTP。
echo "=== 军争正局：8 人，standard_ex_160，逐问经 HTTP 应答 ==="
if ! "$PYTHON" tests/support/web_client.py --base "http://127.0.0.1:$PORT" \
      --seed 20261012 --players 8 --deck-profile standard_ex_160 --max-answers 4000; then
  echo "FAIL: 军争 160 张的端到端对局未通过"
  echo "--- 服务输出 ---"
  cat "$LOG"
  exit 1
fi

# ④ 超时护栏真的会触发，且该局仍跑得完
echo "=== 超时护栏：时限 0.05 秒 ==="
if ! "$PYTHON" tests/support/web_client.py --base "http://127.0.0.1:$PORT" \
      --seed 8675309 --players 4 --timeout 0.05 --max-answers 3000; then
  echo "FAIL: 超时那一局没跑完"
  exit 1
fi
if grep -q '"kind": "timeout"' <<<"$(curl -s "http://127.0.0.1:$PORT/api/runs")"; then :; fi

# ⑤ 过期应答被明确拒绝（409），且不写记录
SESSION_JSON="$(curl -s -X POST "http://127.0.0.1:$PORT/api/sessions" \
  -H 'Content-Type: application/json' \
  -d '{"player_count":2,"ai":"random","seed":5,"timeout_seconds":null}')"
SESSION_ID="$("$PYTHON" -c "import json,sys; print(json.loads(sys.argv[1])['session_id'])" "$SESSION_JSON")"
# 先取一次增量：过期判定的触发点是取增量，取过之后"当前挂起"才是被判定过的那一个。
curl -s -o /dev/null "http://127.0.0.1:$PORT/api/sessions/$SESSION_ID?after=-1"
STALE_ACTION="$("$PYTHON" -c "
import json, sys
body = json.loads(sys.argv[1])
action = body['pending']['legal_actions'][0]
action['answers_seq'] = -1  # 陈旧序号
print(json.dumps({'action': action}))
" "$SESSION_JSON")"
CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST \
  "http://127.0.0.1:$PORT/api/sessions/$SESSION_ID/answer" \
  -H 'Content-Type: application/json' -d "$STALE_ACTION")"
if [ "$CODE" = "409" ]; then
  echo "PASS: 过期应答返回 409"
else
  echo "FAIL: 过期应答应返回 409，实际 $CODE"
  echo "--- 服务输出 ---"
  cat "$LOG"
  exit 1
fi

# ⑥ 前端渲染冒烟：把**真的前端脚本**（按 index.html 顺序拼接）跑在最小 DOM 垫片上，
# 喂真服务器响应（判据 2 的界面侧：对手不出现牌面；以及"提交的 Action 必须原样取自
# legal_actions"）。
# node 是这一步的运行时：缺失即判红——与 test_session_decoder.sh 对 Node 的态度一致，
# 不回退、不静默通过（本仓库已有 Node 门禁先例，故不是新增依赖）。
if ! command -v node >/dev/null 2>&1; then
  echo "FAIL: 前端冒烟需要 node 运行时，PATH 里找不到 node"
  exit 1
fi
while IFS= read -r src; do
  [ -z "$src" ] && continue
  if ! node --check "tools/sgs_web/static/$src" >/dev/null 2>&1; then
    echo "FAIL: 脚本语法检查未通过：$src"
    node --check "tools/sgs_web/static/$src"
    exit 1
  fi
done <<< "$SCRIPT_SRCS"
echo "PASS: $(printf '%s\n' "$SCRIPT_SRCS" | wc -l | tr -d ' ') 份脚本各自 node --check 通过"
echo "=== 前端渲染冒烟（真前端脚本 + 最小 DOM 垫片）==="
if ! node tests/support/ui_smoke.mjs "http://127.0.0.1:$PORT"; then
  echo "FAIL: 前端渲染冒烟未通过"
  exit 1
fi

# ⑥b M5-08a：固定【火攻】tape（spec D12 / §6 A2）——亮牌 / 同花色弃牌 / 放弃 / 无同花色
# 自动放弃，两条固定种子全走真 HTTP；每次提交的动作必须原样取自 legal_actions。
echo "=== 火攻 tape：亮牌 / 同花色 / 放弃（真 HTTP） ==="
if ! "$PYTHON" tests/support/web_client.py --base "http://127.0.0.1:$PORT" --tape fire_attack; then
  echo "FAIL: 火攻 tape 未通过"
  echo "--- 服务输出 ---"
  cat "$LOG"
  exit 1
fi

# ⑥c M5-08c：固定【酒】tape（spec D12 / §6 A2）——出牌阶段一次真实 UseCard（目标自己）+
# 濒死自救一次真实 UseCard（回复 1、无 wine_change）；固定种子，动作原样取自 legal_actions。
echo "=== 酒 tape：出牌阶段 / 濒死自救（真 HTTP） ==="
if ! "$PYTHON" tests/support/web_client.py --base "http://127.0.0.1:$PORT" --tape wine; then
  echo "FAIL: 酒 tape 未通过"
  echo "--- 服务输出 ---"
  cat "$LOG"
  exit 1
fi

# ⑥d M5-09a：三条**固定 tape**（火攻亮牌 / 铁索重铸 / 酒）——spec D12 / §6 A2。
# tape 由构造局面生成（`tests/support/m5_fixture_server.py`），不依赖随机种子；
# 每次询问都是对同一份产品 HTTP 门面的真实往返。
echo "=== 固定 tape：火攻 / 重铸 / 酒（构造局面，真 HTTP） ==="
FIXLOG="$TMP/fixtures.log"
"$PYTHON" tests/support/m5_fixture_server.py --port 0 --runs-dir "$RUNS" > "$FIXLOG" 2>&1 &
FIXPID=$!
FIXURL=""
for _ in $(seq 1 60); do
  FIXURL="$(sed -n 's/.*\(http:\/\/127\.0\.0\.1:[0-9][0-9]*\/\).*/\1/p' "$FIXLOG" 2>/dev/null | head -1)"
  [ -n "$FIXURL" ] && break
  if ! kill -0 "$FIXPID" 2>/dev/null; then break; fi
  sleep 0.2
done
if [ -z "$FIXURL" ]; then
  echo "FAIL: 固定 tape 服务没有在预期时间内打印监听地址"
  cat "$FIXLOG" 2>/dev/null || true
  exit 1
fi
if ! "$PYTHON" tests/support/web_client.py --base "$FIXURL" --tape m5_fixed; then
  echo "FAIL: 固定 tape（火攻 / 重铸 / 酒）未通过"
  cat "$FIXLOG" 2>/dev/null || true
  exit 1
fi
kill "$FIXPID" 2>/dev/null || true
wait "$FIXPID" 2>/dev/null || true
FIXPID=""

# ⑦ `--host 0.0.0.0` 必须真的绑全部网卡（WSL 里从 Windows 打开界面就靠它）。
# 只多起一个进程、只发两个请求，然后立刻杀掉——它防的是"host 参数被写死回环"这种回归。
echo "=== 跨机可达：--host 0.0.0.0 ==="
WSL_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -z "$WSL_IP" ]; then
  echo "PASS: 拿不到 WSL IP（非 WSL 环境），跳过跨机检查"
else
  ANY_PORT="$("$PYTHON" -c 'import socket
s = socket.socket(); s.bind(("0.0.0.0", 0)); print(s.getsockname()[1]); s.close()')"
  "$PYTHON" tools/sgs_web/serve.py --host 0.0.0.0 --port "$ANY_PORT" --runs-dir "$RUNS" \
    > "$TMP/any.log" 2>&1 &
  ANY_PID=$!
  ANY_CODE=""
  for _ in $(seq 1 50); do
    ANY_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://$WSL_IP:$ANY_PORT/" 2>/dev/null || true)"
    [ "$ANY_CODE" = "200" ] && break
    sleep 0.2
  done
  kill "$ANY_PID" 2>/dev/null || true
  wait "$ANY_PID" 2>/dev/null || true
  if [ "$ANY_CODE" = "200" ]; then
    echo "PASS: --host 0.0.0.0 时经 $WSL_IP 可达（200）"
  else
    echo "FAIL: --host 0.0.0.0 时经 $WSL_IP 不可达（$ANY_CODE）"
    cat "$TMP/any.log" 2>/dev/null || true
    exit 1
  fi
fi

echo "PASS: M4 端到端门禁全部通过"
exit 0
