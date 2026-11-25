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
#   bash tests/run_all.sh --tier engine [--tier ui]...   # **票档**（ADR-030）：只跑族表里
#                                    # 登记给这些风险面的 pytest 文件；shell 档照跑、但跳过
#                                    # `test_web.sh`（HTTP 整局留全量档）。票档 ≈2 分钟，
#                                    # 由提交 hook 调；**全量档**仍是合并 / 收口时跑。
#   bash tests/run_all.sh --list-tier engine            # 只打印该票档展开后的文件（供
#                                    # gate-staged.sh 判断被改的测试文件属于哪档；不跑东西）
#
# **统计类单独一片**（ADR-030）：`test_heuristic_ai.py` / `test_all_ai_games.py` 验的是
# 概率、实测 217.97 s + 124 s，与别的重文件同片争用会被误判成超时（HEAD 上真红过）⇒
# 全量档里它们自成一 片、单测上限 600 s；票档里没有它们。
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
TIERS=()
LIST_TIER=""
usage() {
  echo "用法：bash tests/run_all.sh [--quiet] [--tier <engine|web|ui|content>]..." >&2
  echo "      bash tests/run_all.sh --list-tier <档名>    # 只打印该票档展开后的 pytest 文件（不跑任何东西）" >&2
  exit 2
}
while [ $# -gt 0 ]; do
  case "$1" in
    --quiet) QUIET=1 ;;
    --tier)
      [ $# -ge 2 ] || usage
      TIERS+=("$2")
      shift
      ;;
    --tier=*) TIERS+=("${1#--tier=}") ;;
    --list-tier)
      [ $# -ge 2 ] || usage
      LIST_TIER="$2"
      shift
      ;;
    *) usage ;;
  esac
  shift
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

# ---- 票档族表（ADR-030）--------------------------------------------------
# **唯一真源**：`tools/gate-staged.sh` 判断"一个被改的测试文件属于哪一票档"时直接调
# `bash tests/run_all.sh --list-tier <档>`——族表只住这一处，不许抄第二份。
# 档名是**风险面**，不是目录名：engine = 引擎语义 / web = 接入层 / ui = 前端静态 /
# content = 内容与账本。**没登记的一律回落到全量档**（gate-staged.sh 的兜底分支）。
# 统计类（`test_heuristic_ai.py` / `test_all_ai_games.py`）故意不在任何票档里：
# 它们留在全量档，且在全量档里独立成片、单测上限 600 s。
tier_patterns() {
  case "$1" in
    engine)
      printf '%s\n' \
        'tests/test_hero_*.py' \
        'tests/test_replays.py' \
        'tests/test_journal.py' \
        'tests/test_engine_*.py' \
        'tests/test_source_discipline.py' \
        'tests/test_ai_features.py' \
        'tests/test_skills.py' \
        'tests/test_virtual_ids.py' \
        'tests/test_card_family_sites.py' \
        'tests/test_m5_no_leak.py' \
        'tests/test_exchange_visibility.py' \
        'tests/test_public_surface.py' \
        'tests/test_public_view.py' \
        'tests/test_visibility_table.py' \
        'tests/test_action_contract.py' \
        'tests/test_timing*.py' \
        'tests/test_dod.py' \
        'tests/test_writeback_landing.py' \
        'tests/test_skill_authoring_docs.py' \
        'tests/test_web.py' \
        'tests/data/test_heroes_validation.py' \
        'tests/data/test_deck_snapshot.py'
      ;;
    web)
      printf '%s\n' \
        'tests/test_web.py' \
        'tests/test_exchange_visibility.py' \
        'tests/test_web_card_events.py' \
        'tests/test_web_damage_events.py' \
        'tests/test_web_script.py' \
        'tests/test_action_contract.py' \
        'tests/test_wire_contract.py' \
        'tests/test_portrait_serving.py'
      ;;
    ui)
      printf '%s\n' \
        'tests/test_web_static.py' \
        'tests/test_anim_registry.py' \
        'tests/test_action_contract.py' \
        'tests/test_portrait_serving.py'
      ;;
    content)
      printf '%s\n' \
        'tests/test_replays.py' \
        'tests/test_hero_catalog.py' \
        'tests/test_tags.py' \
        'tests/test_skill_authoring_docs.py' \
        'tests/test_script_content.py' \
        'tests/test_script_growth.py' \
        'tests/test_pool_census.py' \
        'tests/data/test_*.py'
      ;;
    *) return 1 ;;
  esac
}

expand_tier() {
  local tier="$1" pattern file matched
  local patterns=()
  # 用 mapfile 而不是 while-read 循环：门禁必须非交互（扫源码的纪律检查会判红）。
  mapfile -t patterns < <(tier_patterns "$tier")
  for pattern in ${patterns[@]+"${patterns[@]}"}; do
    matched=0
    shopt -s nullglob
    for file in $ROOT/$pattern; do
      printf '%s\n' "${file#"$ROOT"/}"
      matched=1
    done
    shopt -u nullglob
    if [ "$matched" -eq 0 ]; then
      echo "票档 $tier 的模式没匹配到文件：$pattern（族表写错了，fail-closed）" >&2
      return 1
    fi
  done
}

if [ -n "$LIST_TIER" ]; then
  tier_patterns "$LIST_TIER" >/dev/null || usage
  expand_tier "$LIST_TIER" | sort -u
  exit 0
fi

for tier in ${TIERS[@]+"${TIERS[@]}"}; do
  tier_patterns "$tier" >/dev/null || usage
done

for t in test_*.sh; do
  # 票档不跑 HTTP 整局：`test_web.sh` 要真起服务打完整局（数十秒），留在全量档。
  # 代价是票档少一条 `node --check` 静态语法扫描——它挡的是"页面当场白屏"，
  # 而那条缺陷在票档里也会被实机窗口立刻看见。
  if [ "${#TIERS[@]}" -gt 0 ] && [ "$t" = "test_web.sh" ]; then
    echo "SKIP  $t（HTTP 整局留全量档）"
    continue
  fi
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
  # ---- 选文件：全量档 = 全部；票档 = 族表并集（`--tier`，族表在文件顶部） ----
  if [ "${#TIERS[@]}" -eq 0 ]; then
    mapfile -t pytest_files < <(cd "$ROOT" && find tests -name 'test_*.py' | sort)
  else
    mapfile -t pytest_files < <(for tier in "${TIERS[@]}"; do expand_tier "$tier"; done | sort -u)
  fi

  # ---- 统计类单独一片（只在全量档）----
  # 它们验的是**概率**，实测 217.97 s + 124 s；与别的重文件同片争用会被误判成超时
  # （HEAD 上真红过：`runs/gate-20261122-161330/pytest-0.log` 的 `+++ Timeout +++`）。
  # 单独一片 ⇒ 无争用；并给它 600 s 的单测上限（全局 240 s 不动，其它测试照旧）。
  stats_present=()
  if [ "${#TIERS[@]}" -eq 0 ]; then
    for s in tests/test_heuristic_ai.py tests/test_all_ai_games.py; do
      [ -e "$ROOT/$s" ] && stats_present+=("$s")
    done
  fi

  group=()
  extra=()
  load=()
  nshard_total=0
  if [ "${#stats_present[@]}" -gt 0 ]; then
    group[0]="$(printf '%s\n' "${stats_present[@]}")"
    extra[0]="-o timeout=600"
    load[0]=0
    nshard_total=1
    keep=()
    for f in "${pytest_files[@]}"; do
      is_stats=0
      for s in "${stats_present[@]}"; do
        [ "$f" = "$s" ] && is_stats=1
      done
      [ "$is_stats" -eq 0 ] && keep+=("$f")
    done
    pytest_files=(${keep[@]+"${keep[@]}"})
  fi

  if [ "${#pytest_files[@]}" -eq 0 ]; then
    if [ "$nshard_total" -eq 0 ]; then
      # 没有可枚举的文件（合成仓 / 旧布局）⇒ 退回"整目录一次调用"，与旧行为一致：
      # 门禁的契约是"pytest 阶段一定要跑到"，不是"一定要枚举出文件"。
      shards=1
      group=("")
      extra=("")
      load=(0)
      nshard_total=1
    fi
  else
    # ---- 统计类已独占一片 ⇒ **总进程数维持 `SGS_GATE_SHARDS`**，其余文件少分一片 ----
    # 为什么不是"再加一片"：这台机是 8 核 / 3.8 GB，3 个 pytest 同时跑会互相拖慢，而
    # `test_fuzz.py` 的单局判据是 **wall-clock**（"超过墙上时钟兜底仍未收局"）。实测：
    # 3 片时它在 seed=5351297216392131677 / 8 人上判红，单独跑 80 s 全绿——那是被饿的，
    # 不是性能退化。故：统计类占一片，其余仍只分 $((shards-1)) 片。
    if [ "$nshard_total" -gt 0 ]; then
      shards=$((shards - 1))
      [ "$shards" -ge 1 ] || shards=1
    fi
    # ---- 余下的按文件行数降序 + 贪心（LPT），均衡的是"行数"这个便宜代理 ----
    [ "$shards" -le "${#pytest_files[@]}" ] || shards="${#pytest_files[@]}"
    [ "$shards" -ge 1 ] || shards=1
    base="$nshard_total"
    for ((i = 0; i < shards; i++)); do
      group[base + i]=""
      extra[base + i]=""
      load[base + i]=0
    done
    nshard_total=$((nshard_total + shards))
    # 不用 `read`：门禁必须非交互（`test_dod.py::test_the_gate_is_non_interactive` 会扫）。
    mapfile -t weighted < <(
      for f in "${pytest_files[@]}"; do
        printf '%s %s\n' "$(wc -l <"$ROOT/$f")" "$f"
      done | sort -rn
    )
    for row in "${weighted[@]}"; do
      size="${row%% *}"
      file="${row#* }"
      pick="$base"
      least="${load[base]}"
      for ((j = base + 1; j < nshard_total; j++)); do
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
  for ((i = 0; i < nshard_total; i++)); do
    shard_args=()
    if [ -n "${group[i]:-}" ]; then
      mapfile -t shard_args <<<"${group[i]}"
    fi
    extra_i="${extra[i]:-}"
    (
      cd "$ROOT"
      # extra_i 故意不加引号：统计类那片是 "-o timeout=600" 两个词。
      # shellcheck disable=SC2086
      if "$pytest_cmd" -q $extra_i ${shard_args[@]+"${shard_args[@]}"} >"$LOG_DIR/pytest-$i.log" 2>&1; then
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
  for ((i = 0; i < nshard_total; i++)); do
    if [ "$(cat "$LOG_DIR/pytest-$i.code" 2>/dev/null || echo 1)" != "0" ]; then
      red+=("$i")
    fi
  done

  if [ "${#red[@]}" -eq 0 ]; then
    if [ "$QUIET" -eq 1 ]; then
      echo "PASS  pytest（$nshard_total 片）"
    else
      for ((i = 0; i < nshard_total; i++)); do
        echo "--- 第 $i 片（$(printf '%s' "${group[i]}" | wc -w) 个文件） ---"
        cat "$LOG_DIR/pytest-$i.log"
      done
      echo "--- pytest 通过 ---"
    fi
  else
    if [ "$QUIET" -eq 1 ]; then
      echo "FAIL  pytest（$nshard_total 片，红 ${#red[@]} 片）"
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
