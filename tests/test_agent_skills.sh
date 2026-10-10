#!/usr/bin/env bash
# 校验 Agent skills 配置（issue tracker / domain docs）齐全且可被技能消费。
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

# 合成反例的临时副本目录只在 $TMPDIR 下建（票 05）：探针中断也不在仓库根留残留目录。
PROBE_TMP="${TMPDIR:-/tmp}"
probe_mktemp() { mktemp -d "${PROBE_TMP%/}/.wb_probe.XXXXXX"; }
# 防再漏（票 05）：探针只许动 $TMPDIR。比对探针前后的 git 工作树快照，并检查仓库根
# 无 .wb_probe.* 残留——两者任一不满足即判红（挡「有人把探针目录改回仓库内」的回归）。
repo_tree_snapshot() { git status --porcelain --untracked-files=all 2>/dev/null | sort; }
probe_residue() { find "$PWD" -maxdepth 1 -name '.wb_probe.*' -print 2>/dev/null; }

check_file() { # $1=文件 $2=需包含的字面串 $3=描述
  if grep -qF "$2" "$1"; then
    echo "PASS: $1 含『$3』"
  else
    echo "FAIL: $1 缺少『$3』（未找到：$2）"
    fail=1
  fi
}

check_exists() { # $1=路径
  if [ -e "$1" ]; then
    echo "PASS: 存在 $1"
  else
    echo "FAIL: 缺少 $1"
    fail=1
  fi
}

# AGENTS.md 的 Agent skills 段
check_file AGENTS.md "## Agent skills" "Agent skills 段"
check_file AGENTS.md "### Issue tracker" "issue tracker 指针"
check_file AGENTS.md "### Domain docs" "domain docs 指针"
check_file AGENTS.md "docs/agents/issue-tracker.md" "tracker 文档路径"
check_file AGENTS.md "docs/agents/domain.md" "domain 文档路径"
if grep -q "### Triage labels" AGENTS.md; then
  echo "FAIL: 出现了 Triage labels 子段，但 triage 技能未安装"
  fail=1
else
  echo "PASS: 未出现 Triage labels 子段（triage 未安装）"
fi

# docs/agents/ 两份配置
check_exists docs/agents/issue-tracker.md
check_exists docs/agents/domain.md

# tracker 约定：本地 markdown 的关键契约
check_file docs/agents/issue-tracker.md ".scratch/" "工单根目录"
check_file docs/agents/issue-tracker.md "issues/<NN>-<slug>.md" "每票一文件命名"
check_file docs/agents/issue-tracker.md "spec.md" "规格文件"
check_file docs/agents/issue-tracker.md "Wayfinding 操作" "wayfinder 操作节"
check_file docs/agents/issue-tracker.md "Blocked by" "阻塞边"
check_file docs/agents/issue-tracker.md "Status: claimed" "认领状态"

# domain 规则：必须指向既有 ADR 表与开发文档
check_file docs/agents/domain.md "开发文档/" "稳定态设计文档"
check_file docs/agents/domain.md "决策记录（ADR）" "既有 ADR 表指针"
check_file docs/agents/domain.md "单上下文" "单上下文布局"
check_file docs/agents/domain.md "GLOSSARY.md" "术语表"
check_file docs/agents/domain.md "静默继续" "缺失文件时静默继续"

# coding standards（票 04）：评审期的判断题标准存在，且 AGENTS.md 只留导航指针。
check_exists CODING_STANDARDS.md
check_file AGENTS.md "### Coding standards" "coding standards 子段"
check_file AGENTS.md "CODING_STANDARDS.md" "coding standards 路径"
check_file CODING_STANDARDS.md "调用方" "落点要连调用方一起看"
check_file CODING_STANDARDS.md "_Avoid_" "术语表边界"

# 本地 tracker 工作产物不进版本库
check_exists .gitignore
check_file .gitignore ".scratch/" "忽略 .scratch"

# 工单 Status 约定（票 03）：`.scratch/*/issues/*.md` 必须有**恰好一行**规范
# `Status: open|claimed|resolved`，且不得再有别种 Status 写法（加粗、词表外、追加别的词）。
# `.scratch/` 不存在（干净 clone）时整段 skip，门禁在任何 clone 全绿。
STATUS_ALLOWED="open|claimed|resolved"
STATUS_RE="^Status: (${STATUS_ALLOWED})[[:space:]]*$"

out_is_red() { case "$1" in *FAIL*) return 0;; *) return 1;; esac; }

scan_issue_status() { # $1=仓库根；返回 1 = 有错
  local root="$1" errors=0 count=0 dirs=0 f strays canonical
  local files=()
  if [ -d "$root/.scratch" ]; then
    while IFS= read -r _d; do dirs=$((dirs+1)); done < <(
      find "$root/.scratch" -mindepth 2 -maxdepth 2 -type d -name issues 2>/dev/null
    )
    while IFS= read -r f; do files+=("$f"); done < <(
      find "$root/.scratch" -mindepth 3 -maxdepth 3 -type f -path '*/issues/*.md' 2>/dev/null | sort
    )
  fi
  if [ "$dirs" -eq 0 ]; then
    echo "PASS: 无 .scratch/*/issues/，工单 Status 检查 skip（干净 clone 全绿）"
    return 0
  fi
  # 覆盖元断言：有 issues 目录却一张票都没扫到 = find/glob 失效，须判红而非空转通过。
  if [ "${#files[@]}" -eq 0 ]; then
    echo "FAIL: 存在 .scratch/*/issues/ 却一张工单都没扫到（find/glob 失效？）"
    return 1
  fi
  for f in "${files[@]}"; do
    count=$((count+1))
    canonical="$(grep -c -E "$STATUS_RE" "$f" || true)"
    if [ "$canonical" -eq 0 ]; then
      echo "FAIL: ${f#"$root"/} 缺少规范『Status: open / claimed / resolved』行"
      errors=$((errors+1))
      continue
    fi
    if [ "$canonical" -ne 1 ]; then
      echo "FAIL: ${f#"$root"/} 有 $canonical 行规范 Status（应恰好一行）"
      errors=$((errors+1))
    fi
    # 除那一行外，任何 Status 写法（加粗 / 词表外 / 追加别的词）都判红。
    strays="$(grep -n -E '^\**Status:\**' "$f" \
      | grep -v -E "^[0-9]+:Status: (${STATUS_ALLOWED})[[:space:]]*$" || true)"
    if [ -n "$strays" ]; then
      echo "FAIL: ${f#"$root"/} 有非规范 Status 写法：$(printf '%s\n' "$strays" | head -1)"
      errors=$((errors+1))
    fi
  done
  if [ "$errors" -eq 0 ]; then
    echo "PASS: 工单 Status 约定全部符合（$count 张票）"
  fi
  [ "$errors" -eq 0 ]
}

# 合成反例：词表外的值、追加别的词、加粗写法必须变红；词表内（含行尾空格）必须变绿。
issue_status_probe() { # $1=工单正文 $2=red|green
  local probe out
  probe="$(probe_mktemp)" || return 1
  mkdir -p "$probe/.scratch/demo/issues"
  printf '%s\n' "$1" > "$probe/.scratch/demo/issues/01-demo.md"
  out="$(scan_issue_status "$probe" 2>&1 || true)"
  rm -rf "$probe"
  if [ "$2" = red ]; then out_is_red "$out"; else ! out_is_red "$out"; fi
}

# 覆盖元断言的反面：有 issues 目录、却没有任何票 ⇒ 必须判红（防 find 失效空转通过）。
issue_status_empty_dir_goes_red() {
  local probe out
  probe="$(probe_mktemp)" || return 1
  mkdir -p "$probe/.scratch/demo/issues"
  out="$(scan_issue_status "$probe" 2>&1 || true)"
  rm -rf "$probe"
  out_is_red "$out"
}

if ! scan_issue_status "$PWD"; then fail=1; fi
REPO_TREE_BEFORE="$(repo_tree_snapshot)"
if ! issue_status_probe $'Type: task\nStatus: done' red; then
  echo "FAIL: 合成反例未变红：词表外的 Status: done 未被判红"
  fail=1
fi
if ! issue_status_probe $'Type: task\nStatus: claimed 别的词' red; then
  echo "FAIL: 合成反例未变红：Status 行追加别的词未被判红"
  fail=1
fi
if ! issue_status_probe $'Type: task\n**Status:** resolved' red; then
  echo "FAIL: 合成反例未变红：加粗的 **Status:** 写法未被判红"
  fail=1
fi
if ! issue_status_probe $'Type: task\nStatus: claimed' green; then
  echo "FAIL: 合成反例未变绿：词表内的 Status: claimed 被判红"
  fail=1
fi
if ! issue_status_probe $'Type: task\nStatus: resolved  ' green; then
  echo "FAIL: 合成反例未变绿：规范 Status 行尾随空格被判红"
  fail=1
fi
if ! issue_status_empty_dir_goes_red; then
  echo "FAIL: 合成反例未变红：空 issues 目录未被判红（覆盖元断言失效）"
  fail=1
fi

# 防再漏（票 05）：探针不得改动仓库工作树，也不得在仓库根留 .wb_probe.* 残留。
if [ "$(repo_tree_snapshot)" != "$REPO_TREE_BEFORE" ]; then
  echo "FAIL: 合成反例探针改动了仓库工作树（探针目录必须只在 \$TMPDIR 下）"
  repo_tree_snapshot | head -20
  fail=1
fi
residue="$(probe_residue)"
if [ -n "$residue" ]; then
  echo "FAIL: 仓库根残留合成反例探针目录：$(printf '%s ' $residue)"
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "PASS: Agent skills 配置校验全部通过"
fi
exit "$fail"
