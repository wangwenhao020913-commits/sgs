#!/usr/bin/env bash
# 校验 Agent skills 配置（issue tracker / domain docs）齐全且可被技能消费。
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

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

# 本地 tracker 工作产物不进版本库
check_exists .gitignore
check_file .gitignore ".scratch/" "忽略 .scratch"

if [ "$fail" -eq 0 ]; then
  echo "PASS: Agent skills 配置校验全部通过"
fi
exit "$fail"
