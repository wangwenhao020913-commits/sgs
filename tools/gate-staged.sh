#!/usr/bin/env bash
# 按**风险面**演进门禁分级（ADR-030，用户 2026-11-22 裁定 Q2 / Q3 / Q6）：
#
# - 引擎语义（`src/sgs` 的引擎面 + 登记进票档的 `tests/` 文件）→ `run_all.sh --tier engine`
# - 接入层 / 前端静态 / 内容与账本                              → `--tier web|ui|content`
# - 文档面（`开发文档/`、`GLOSSARY.md`、`AGENTS.md`、`docs/` …） → 快速文档检查（秒级）
# - **其余一律 full**（未登记的顶层目录、`tools/`、配置、`src/sgs` 下新子包）——fail-closed：
#   新目录的作者必须先在 `tests/run_all.sh` 的族表里登记它，否则它走全量档。
#   例外是与判据无关的仓根文件（README / 许可证 / 图片 / 打包 / `.github`）——它们仍 skip。
#
# 为什么不再用"碰了 `src/` 或 `tests/` 就是全量"：实测 9.1 天里那一条分级触发 ≈136 次全量级
# hook（≈28 h 墙钟），而 `ui-one-screen`（0 个 `src/` 提交）与 `qunying`（3 个 `src/` 提交）
# 被判成了同一种代价。族表与实测见 `开发文档/05-测试与验证策略.md` §7.1。
#
# 票档的族表**只住 `tests/run_all.sh` 一处**：本脚本判断"一个被改的测试文件属于哪档"时
# 调 `run_all.sh --list-tier <档>`，不抄第二份表。
#
# 用法：
#   bash tools/gate-staged.sh --classify            # 打印 docs / tier:<档>[,<档>] / full / skip
#   bash tools/gate-staged.sh --dry-run             # 打印将要跑的命令
#   bash tools/gate-staged.sh --run [标签]          # 真跑
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

staged="$(git -c core.quotePath=false diff --cached --name-only)"
PYTEST="$ROOT/.venv/bin/pytest"

# ---- 路径 → 风险面 -------------------------------------------------------
# 只有**登记过的**路径能给票档；认不出来的一律 full。
face_of() {
  case "$1" in
    src/sgs/data/*) echo content ;;
    src/sgs/engine/* | src/sgs/skills/* | src/sgs/state/* | src/sgs/effects/* | \
      src/sgs/resolution/* | src/sgs/timing/* | src/sgs/script/* | src/sgs/agents/* | \
      src/sgs/*.py)
      echo engine
      ;;
    tools/sgs_web/static/*) echo ui ;;
    tools/sgs_web/*.py | tools/ai_eval/* | tools/sgs_batch/*) echo web ;;
    tests/*) tier_of_test "$1" ;;
    开发文档/* | GLOSSARY.md | AGENTS.md | CODING_STANDARDS.md | docs/*) echo docs ;;
    # 与判据无关的仓根文件：跳过（旧行为；README / 许可证 / 图片 / 打包）。
    README.md | NOTICE | LICENSE | SECURITY.md | CONTRIBUTING.md | CODE_OF_CONDUCT.md | \
      .gitignore | .gitattributes | .github/* | *.md | pic/* | packaging/* | examples/* | \
      参考资料/*)
      echo skip
      ;;
    # 其余一律 fail-closed：`src/` `tools/` 下新增的子包、配置、新的顶层目录都走全量档。
    *) echo full ;;
  esac
}

# 被改的测试文件属于哪一票档：族表在 `tests/run_all.sh`（唯一真源）。哪档都不在 ⇒ full。
declare -A TIER_FILES=()
tier_files() {
  if [ -z "${TIER_FILES[$1]+set}" ]; then
    TIER_FILES[$1]="$(bash "$ROOT/tests/run_all.sh" --list-tier "$1" 2>/dev/null || true)"
  fi
  printf '%s\n' "${TIER_FILES[$1]}"
}

tier_of_test() {
  local file="$1" tier
  for tier in engine web ui content; do
    if tier_files "$tier" | grep -qxF "$file"; then
      echo "$tier"
      return 0
    fi
  done
  echo full
}

# ---- 汇总：full 压过一切；没有全量面就给票档（可并集）；只剩文档就给 docs ----
classify() {
  local file face
  local has_full=0 has_docs=0
  local code=()
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    face="$(face_of "$file")"
    case "$face" in
      full) has_full=1 ;;
      docs) has_docs=1 ;;
      skip) ;;
      *) code+=("$face") ;;
    esac
  done <<<"$staged"

  if [ "$has_full" -eq 1 ]; then
    echo full
    return 0
  fi
  if [ "${#code[@]}" -gt 0 ]; then
    echo "tier:$(printf '%s\n' "${code[@]}" | sort -u | paste -sd, -)"
    return 0
  fi
  if [ "$has_docs" -eq 1 ]; then
    echo docs
    return 0
  fi
  echo skip
}

docs_commands() {
  printf '%s\n' \
    "bash tests/test_dev_docs.sh" \
    "bash tests/test_agent_skills.sh" \
    "bash tests/test_agents_md.sh" \
    ".venv/bin/pytest -q tests/test_writeback_landing.py tests/test_dod_m3.py tests/test_dod_m4.py"
}

run_docs() {
  local label="$1" command
  if [ ! -x "$PYTEST" ]; then
    echo "$label：docs 快速检查失败——$PYTEST 不存在或不可执行。"
    echo "  先建项目虚拟环境（见 AGENTS.md 注意事项 2）。"
    return 1
  fi
  while IFS= read -r command; do
    if ! bash -c "$command"; then
      echo "$label：docs 快速检查失败：$command"
      return 1
    fi
  done <<<"$(docs_commands)"
  echo "$label：docs 快速检查通过。"
}

run_tiers() {
  local label="$1" tiers="$2" tier
  local args=()
  while IFS= read -r tier; do
    [ -n "$tier" ] || continue
    args+=(--tier "$tier")
  done <<<"$(printf '%s' "$tiers" | tr ',' '\n')"
  echo "$label：跑票档 $tiers（bash tests/run_all.sh --quiet ${args[*]}）……"
  if bash tests/run_all.sh --quiet "${args[@]}"; then
    echo "$label：票档通过（全量档在合并提交与收口时跑）。"
  else
    echo "$label：门禁失败（票档），本次提交被拒。"
    echo "  修好之后再提交。红了一定不能提交；绿了也只代表票档绿，合并前仍要全量。"
    echo "  确要提交未过门禁的状态，用 --no-verify（纪律见 AGENTS.md 注意 4）。"
    return 1
  fi
}

run_full() {
  local label="$1"
  echo "$label：跑 bash tests/run_all.sh --quiet（全量档，本机实测 ≈8 分钟）……"
  if bash tests/run_all.sh --quiet; then
    echo "$label：通过（全量档）。"
  else
    echo "$label：门禁失败（全量档），本次提交被拒。"
    echo "  修好之后再提交。确要提交未过门禁的状态，用 --no-verify。"
    return 1
  fi
}

mode="${1:---run}"
case "$mode" in
  --classify)
    classify
    ;;
  --dry-run)
    case "$(classify)" in
      full) echo "bash tests/run_all.sh --quiet" ;;
      docs) docs_commands ;;
      tier:*)
        tiers="$(classify)"
        tiers="${tiers#tier:}"
        printf 'bash tests/run_all.sh --quiet'
        for t in $(printf '%s' "$tiers" | tr ',' ' '); do
          printf ' --tier %s' "$t"
        done
        printf '\n'
        ;;
      skip) echo "skip" ;;
    esac
    ;;
  --run)
    label="${2:-门禁 hook}"
    verdict="$(classify)"
    case "$verdict" in
      full) run_full "$label" ;;
      docs) run_docs "$label" ;;
      tier:*) run_tiers "$label" "${verdict#tier:}" ;;
      skip) echo "$label：本次改动不需要门禁（跳过）。" ;;
      *)
        echo "内部错误：classify 返回了未知档 '$verdict'" >&2
        exit 2
        ;;
    esac
    ;;
  *)
    echo "用法：bash tools/gate-staged.sh [--classify|--dry-run|--run [标签]]" >&2
    exit 2
    ;;
esac
