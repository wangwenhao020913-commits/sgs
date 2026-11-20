#!/usr/bin/env bash
# 提交前 screen（hero-flow-v2 票 T1）：**门禁的子集**，≈47 秒，用来把"红"提前到提交之前。
#
# 为什么有它：唯一门禁 `tests/run_all.sh` 一次 ≈10–12 分钟，而且它挂在**提交时**——
# 红一次 = 白等一次全量 + 修完再等一次。2026-11-20 那天 10 次全量里 4 次红，其中
# 3 次落在本 screen 覆盖的家族里（剧本可赢性 / 出货表 / 金录像与 PvE 基线），第 4 次
# （`test_fuzz`）在 `--full` 里。历史上按"一次红能炸多少用例"看，`test_replays.py` 是
# 唯一的大炸点（一处语义改动 ⇒ 65 条金录像 + 6 条 PvE 基线整片红）。
#
# **它不是门禁**：只跑 ≈190/4924 条。绿了不代表能提交（判据全集的入口仍是
# `tests/run_all.sh`），**红了一定不能提交**。这里列的每一项都是门禁已经在跑的
# **同一条断言**，不新增判据、不放松判据。
#
# 用法：
#   bash tools/precommit-screen.sh            # 快档（≈47 s）
#   bash tools/precommit-screen.sh --full     # 加 `test_fuzz`（≈2 min）：动 AI 随机流 /
#                                             # 共享原语（时机 / 结算 / 修饰点）时必跑
#   bash tools/precommit-screen.sh --list     # 只打印清单（`tests/test_screen_targets.sh` 用）
#
# 实测（2026-11-20，8 核 / 2.5 GB 可用）：
#   check_goldens.sh（47 条冻结）                                        1.1 s
#   测试器 190 条（replays + script_content + script_growth + skill_authoring_docs）32.5 s
#   测试器 test_hero_catalog + test_tags + test_dod                       ≈9 s
#   pool_census.py --check                                               0.6 s
#   [--full] pytest test_fuzz                                            76 s
#
# `SGS_PYTEST` 是**给自检用的桩**（`tests/test_screen_targets.sh` 拿它验"任一目标失败 ⇒
# 非零退出"的聚合逻辑），正常用法不要设置它。
set -uo pipefail

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

PYTEST="${SGS_PYTEST:-$ROOT/.venv/bin/pytest}"
PYTHON="$ROOT/.venv/bin/python"
GOLDENS="$ROOT/tools/check_goldens.sh"
CENSUS="$ROOT/tools/pool_census.py"

# 快档清单：既覆盖"加了武将必红"的家族，也覆盖"文档↔代码漂移"（skill_authoring_docs）。
FAST_TESTS=(
  tests/test_replays.py
  tests/test_script_content.py
  tests/test_script_growth.py
  tests/test_skill_authoring_docs.py
  tests/test_hero_catalog.py
  tests/test_tags.py
  tests/test_dod.py
)
# 全档追加：随机对局驱动的不变量，单价高（≈76 s），只在动 AI / 共享原语时值得付。
FULL_TESTS=(
  tests/test_fuzz.py
)

MODE="fast"
for arg in "$@"; do
  case "$arg" in
    --full) MODE="full" ;;
    --list) MODE="list" ;;
    -h | --help)
      sed -n '1,20p' "$0"
      exit 0
      ;;
    *)
      echo "未知参数：$arg（可用：--full / --list）" >&2
      exit 2
      ;;
  esac
done

if [ "$MODE" = "list" ]; then
  printf '%s\n' "${FAST_TESTS[@]}"
  printf '%s\n' "${FULL_TESTS[@]}"
  printf '%s\n' "tools/check_goldens.sh"
  printf '%s\n' "tools/pool_census.py"
  exit 0
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/sgs-screen.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

fail=0
started="$(date +%s)"

run_stage() {
  # $1 = 标签，其余 = 命令。成功打印 PASS 一行，失败打印 FAIL + 摘要并置 fail=1。
  local label="$1"
  shift
  local log="$TMP/${label// /_}.log" since
  since="$(date +%s)"
  if "$@" >"$log" 2>&1; then
    printf 'PASS  %-30s %3ss\n' "$label" "$(( $(date +%s) - since ))"
    return 0
  fi
  printf 'FAIL  %-30s %3ss\n' "$label" "$(( $(date +%s) - since ))"
  grep -nE 'FAILED|失败|Error|AssertionError' "$log" | tail -n 20 || true
  echo "  --- 末 30 行 ---"
  tail -n 30 "$log" | sed 's/^/  /'
  fail=1
  return 1
}

if [ ! -x "$PYTEST" ]; then
  echo "FAIL  pytest：$PYTEST 不存在或不可执行（先建项目虚拟环境，见 AGENTS.md 注意事项 2）"
  exit 1
fi

run_stage "goldens(47 冻结字节)" bash "$GOLDENS" || true

pytest_cmd=("$PYTEST" -q -p no:cacheprovider "${FAST_TESTS[@]}")
if [ "$MODE" = "full" ]; then
  pytest_cmd+=("${FULL_TESTS[@]}")
fi
run_stage "pytest(${#FAST_TESTS[@]} 族$([ "$MODE" = "full" ] && echo " + fuzz"))" "${pytest_cmd[@]}" || true

run_stage "pool_census --check" "$PYTHON" "$CENSUS" --check || true

printf '%s\n' '---'
if [ "$fail" -eq 0 ]; then
  echo "SCREEN PASS（$(( $(date +%s) - started ))s，门禁子集；提交仍由 hook 跑全量）"
  exit 0
fi
echo "SCREEN FAIL（$(( $(date +%s) - started ))s）——先修再提交；门禁同样会红。"
exit 1
