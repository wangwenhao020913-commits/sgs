#!/usr/bin/env bash
# 运行 SGS 项目的全部检查：全部 shell 校验 + Python 测试。
# 唯一门禁：任一阶段失败即整体失败；聚合全部失败、不 fail-fast。
#
# 用法：
#   bash tests/run_all.sh            # 全量输出（缺省：每条检查的 PASS 行与 pytest 输出都打）
#   bash tests/run_all.sh --quiet    # 只打每条检查的 PASS/FAIL 与最终结果；失败时只补打
#                                    # **失败摘要 + 末 40 行**，完整日志留在 `runs/gate-<ts>/`
#                                    # （`$LOG_DIR` 退出即删，失败现场不能跟着没掉）。
#                                    # hook（gate-staged.sh）用这个，免得逐条 PASS 把上下文刷掉。
#
# **pytest 分片并行**（hero-flow-v2 票 02）：判据一条不改，只是把同一批测试文件按体量
# 均分到 N 个进程。理由：4924 条串行实测 586–733 s，是门禁的唯一大头，而门禁挂在提交上
# （2026-11-20 那天 10 次全量 = 102 分钟）。
#   - 分片数：`SGS_GATE_SHARDS`（缺省 **2**）。实测（8 核 / 3.8 GB）：串行 733 s；
#     2 片 531 s 全绿；3 片 432 s 但 `test_the_heuristic_beats_random_on_the_same_seeds`
#     会撞 pytest-timeout 的 240 s 上限（并行争用）⇒ 缺省保守取 2，3 片只作实验档；
#   - 分组：按文件行数降序贪心（LPT），不按文件名——test_web.py 一个文件就 1.6 万行；
#   - 失败聚合不变：任一片红 ⇒ 整体红，且红的那几片日志各自落 `runs/gate-<ts>/pytest-<i>.log`。
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

# 分片日志总要一份临时目录（不只是 --quiet 档：非 quiet 档也要把并行输出错开，
# 否则 N 份 pytest 输出会交错成一团）。
LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sgs-gate.XXXXXX")"
cleanup() {
  rm -rf "$LOG_DIR"
}
trap cleanup EXIT

# 门禁本身跑得动，但"提交 / 合并时自动跑"靠的是 core.hooksPath。没装 = 只剩 CI 那一份
# 信号（2026-11-13 起 `.github/workflows/ci.yml` 跑的就是本脚本；见 .githooks/pre-commit）。
# 放在**开头**：先提醒再跑，别让 agent 等完三分钟才看到。
if [ "$(git -C "$ROOT" config --get core.hooksPath || true)" != ".githooks" ]; then
  echo "⚠️  提醒：core.hooksPath 未指向 .githooks —— 改动提交 / 合并时不会自动触发本门禁。"
  echo "    跑一次：bash tools/install-hooks.sh"
  echo
fi

fail=0

shards="${SGS_GATE_SHARDS:-2}"
if ! [[ "$shards" =~ ^[1-9][0-9]*$ ]]; then
  echo "SGS_GATE_SHARDS 必须是正整数，实际 '$shards'" >&2
  exit 2
fi

KEEP_DIR=""
keep_log() {
  # $1 = 临时日志，$2 = 落盘文件名。首次失败时建 `runs/gate-<时间戳>/`。
  if [ -z "$KEEP_DIR" ]; then
    KEEP_DIR="$ROOT/runs/gate-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$KEEP_DIR"
  fi
  cp -f "$1" "$KEEP_DIR/$2" 2>/dev/null || true
}

for t in test_*.sh; do
  if [ "$QUIET" -eq 1 ]; then
    # 每条检查的输出先收进临时日志：成功只留一行状态；失败只摘失败行 + 末 40 行，
    # 完整日志**复制到仓内** `runs/gate-<时间戳>/`（$LOG_DIR 退出即删）。
    if bash "$t" >"$LOG_DIR/$t.log" 2>&1; then
      echo "PASS  $t"
    else
      echo "FAIL  $t"
      keep_log "$LOG_DIR/$t.log" "$t.log"
      echo "--- $t 失败摘要（完整日志：${KEEP_DIR#"$ROOT"/}/$t.log） ---"
      grep -nE 'FAIL|失败|Error|error|Traceback|assert' "$LOG_DIR/$t.log" | tail -n 40 || true
      echo "--- $t 末 40 行 ---"
      tail -n 40 "$LOG_DIR/$t.log" || true
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
  echo "=== 运行 pytest（$shards 片并行） ==="
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
else
  # ---- 分片：按文件行数降序 + 贪心（LPT），均衡的是"行数"这个便宜代理 ----
  # 没有可枚举的文件（合成仓 / 旧布局）⇒ 退回"整目录一次调用"，与旧行为一致：
  # 门禁的契约是"pytest 阶段一定要跑到"，不是"一定要枚举出文件"。
  mapfile -t pytest_files < <(cd "$ROOT" && find tests -name 'test_*.py' | sort)
  if [ "${#pytest_files[@]}" -eq 0 ]; then
    shards=1
    group=("")
    load=(0)
  else
    [ "$shards" -le "${#pytest_files[@]}" ] || shards="${#pytest_files[@]}"
    group=()
    load=()
    for ((i = 0; i < shards; i++)); do
      group[i]=""
      load[i]=0
    done
    # 不用 `read`：门禁必须非交互（`test_dod.py::test_the_gate_is_non_interactive` 会扫）。
    mapfile -t weighted < <(
      for f in "${pytest_files[@]}"; do
        printf '%s %s\n' "$(wc -l <"$ROOT/$f")" "$f"
      done | sort -rn
    )
    for row in "${weighted[@]}"; do
      size="${row%% *}"
      file="${row#* }"
      pick=0
      least="${load[0]}"
      for ((j = 1; j < shards; j++)); do
        if [ "${load[j]}" -lt "$least" ]; then
          least="${load[j]}"
          pick="$j"
        fi
      done
      load[pick]=$((load[pick] + size))
      if [ -n "${group[pick]}" ]; then
        group[pick]="${group[pick]}"$'\n'"$file"
      else
        group[pick]="$file"
      fi
    done
  fi

  # ---- 起 N 片；每片自己的日志与退出码（退出码落文件，避免 wait 的期望码跨片串味） ----
  pids=()
  for ((i = 0; i < shards; i++)); do
    shard_args=()
    if [ -n "${group[i]}" ]; then
      mapfile -t shard_args <<<"${group[i]}"
    fi
    (
      cd "$ROOT"
      if "$pytest_cmd" -q ${shard_args[@]+"${shard_args[@]}"} >"$LOG_DIR/pytest-$i.log" 2>&1; then
        echo 0 >"$LOG_DIR/pytest-$i.code"
      else
        echo 1 >"$LOG_DIR/pytest-$i.code"
      fi
    ) &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "$pid" || true
  done

    red=()
    for ((i = 0; i < shards; i++)); do
      if [ "$(cat "$LOG_DIR/pytest-$i.code" 2>/dev/null || echo 1)" != "0" ]; then
        red+=("$i")
      fi
    done

    if [ "${#red[@]}" -eq 0 ]; then
      if [ "$QUIET" -eq 1 ]; then
        echo "PASS  pytest（$shards 片）"
      else
        for ((i = 0; i < shards; i++)); do
          echo "--- 第 $i 片（$(printf '%s' "${group[i]}" | wc -w) 个文件） ---"
          cat "$LOG_DIR/pytest-$i.log"
        done
        echo "--- pytest 通过 ---"
      fi
    else
      if [ "$QUIET" -eq 1 ]; then
        echo "FAIL  pytest（$shards 片，红 ${#red[@]} 片）"
      fi
      for i in "${red[@]}"; do
        keep_log "$LOG_DIR/pytest-$i.log" "pytest-$i.log"
        echo "--- 第 $i 片失败（$(printf '%s' "${group[i]}" | wc -w) 个文件；完整日志：${KEEP_DIR#"$ROOT"/}/pytest-$i.log） ---"
        grep -nE 'FAILED|ERROR' "$LOG_DIR/pytest-$i.log" | tail -n 20 || true
        echo "--- 第 $i 片末 40 行 ---"
        tail -n 40 "$LOG_DIR/pytest-$i.log" || true
      done
      [ "$QUIET" -eq 1 ] || echo "--- pytest 失败 ---"
      fail=1
    fi
  fi
echo

if [ "$fail" -eq 0 ]; then
  echo "ALL TESTS PASSED"
else
  echo "SOME TESTS FAILED"
fi
exit "$fail"
