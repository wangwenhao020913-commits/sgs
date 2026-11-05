#!/usr/bin/env bash
# 校验 Agent skills 配置（issue tracker / domain docs）齐全且可被技能消费。
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

# 合成反例探针纪律（票 05）：探针目录只在 $TMPDIR；跑完证明没动过仓库树。
# shellcheck source=tests/support/probe.sh
source tests/support/probe.sh

check_file() { # $1=文件 $2=需包含的字面串 $3=描述
  if grep -qF "$2" "$1"; then
    echo "PASS: $1 含『$3』"
  else
    echo "FAIL: $1 缺少『$3』（未找到：$2）"
    fail=1
  fi
}

# 覆盖元断言的反面：check_file 对不存在的文件必须判红（防新增断言空转通过）。
# check_file 跑在 $(...) 子壳里，它置的 fail 不会污染父壳。
check_file_missing_goes_red() {
  local out
  out="$(check_file no-such-file-______.md "x" "合成反例" 2>&1 || true)"
  case "$out" in *FAIL*) return 0;; *) return 1;; esac
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
check_file docs/agents/issue-tracker.md "Answer contract: on" "Answer 契约的自选标记"
check_file docs/agents/issue-tracker.md "Touches:" "票面声明会改哪些文件"

# domain 规则：必须指向既有 ADR 表与开发文档
check_file docs/agents/domain.md "开发文档/" "稳定态设计文档"
check_file docs/agents/domain.md "决策记录（ADR）" "既有 ADR 表指针"
check_file docs/agents/domain.md "单上下文" "单上下文布局"
check_file docs/agents/domain.md "GLOSSARY.md" "术语表"
check_file docs/agents/domain.md "静默继续" "缺失文件时静默继续"

# 导航指针（票 06）：规则级账本 + 两份 gitignored 的票档 / 索引。
check_file docs/agents/domain.md "程序化约定与偏离一手" "规则级账本指针"
check_file docs/agents/domain.md "superseded-by" "账本锚的替身写法（retro S7）"
check_file docs/agents/domain.md ".scratch/m2-standard-identity/issues/" "M2 决策票档案指针"
check_file docs/agents/domain.md ".scratch/m2-standard-identity/writeback.md" "票级行索引指针"
check_file docs/agents/domain.md "存在时才读，缺则跳过" "gitignored 产物的读法"

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
out_has_warn() { case "$1" in *WARN:*) return 0;; *) return 1;; esac; }

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

# 工单 Answer 契约（retro S2）。两条规则：
#
# ① **全局**：写了 `## Answer` 的票，Answer 下必须有非空正文 —— "空 Answer" 形同没写。
#    这条对全部历史票也成立（实测 58 张有 Answer、**0 张空**），纯属防退化。
# ② **按 effort 自选**：在 effort 的 `spec.md` / `design.md` 里写一行 `Answer contract: on`，
#    该 effort 的每张 `resolved` 票就必须有非空 `## Answer`。
#
# 为什么要自选而不是一刀切：`.scratch/` 有 124 张 resolved 票，其中 33 张是旧 effort（只写
# `## Comments` 或干脆不写），而且像 `m2-standard-identity` 那样**前松后紧的混合** effort
# 也存在 —— 任何"按现状反推"的判据都会把历史判红。自选标记把"启用契约"变成 effort 作者
# 在**自己的裁定处**（spec / design）做的一次显式动作：机械可判、且不追溯。
# 动机：`/implement-spec` 的中断恢复 —— implementer 被中断时若没写 Answer，进度就只能靠
# `git diff` 猜（本轮 6 次派发里 4 次被中断，吃过这个亏）。
scan_issue_answers() { # $1=仓库根；返回 1 = 有错
  local root="$1" errors=0 checked=0 opted=0
  local dirs=() d f body marker
  if [ -d "$root/.scratch" ]; then
    while IFS= read -r d; do dirs+=("$d"); done < <(
      find "$root/.scratch" -mindepth 2 -maxdepth 2 -type d -name issues 2>/dev/null | sort
    )
  fi
  if [ "${#dirs[@]}" -eq 0 ]; then
    echo "PASS: 无 .scratch/*/issues/，工单 Answer 检查 skip（干净 clone 全绿）"
    return 0
  fi
  for d in "${dirs[@]}"; do
    local effort strict_here=0
    effort="$(dirname "$d")"
    for marker in "$effort/spec.md" "$effort/design.md"; do
      [ -f "$marker" ] || continue
      if grep -q '^Answer contract: on[[:space:]]*$' "$marker"; then strict_here=1; fi
    done
    [ "$strict_here" -eq 1 ] && opted=$((opted+1))
    for f in "$d"/*.md; do
      [ -e "$f" ] || continue
      # ① 全局：有 `## Answer` ⇒ 必须有非空正文（对历史也成立，防"空节"退化）。
      if grep -q '^## Answer' "$f"; then
        body="$(awk '/^## Answer/{f=1;next} /^## /{f=0} f && !/^[[:space:]]*$/{print; exit}' "$f")"
        if [ -z "$body" ]; then
          echo "FAIL: ${f#"$root"/} 的『## Answer』是空节（等于没写）"
          errors=$((errors+1))
        fi
      fi
      # ② 自选 effort：resolved 票必须收口。
      [ "$strict_here" -eq 1 ] || continue
      grep -qE '^Status: resolved[[:space:]]*$' "$f" || continue
      checked=$((checked+1))
      grep -q '^## Answer' "$f" && continue
      echo "FAIL: ${f#"$root"/} 是 resolved 但缺少『## Answer』（该 effort 声明了 Answer contract: on）"
      errors=$((errors+1))
    done
  done
  if [ "$errors" -eq 0 ]; then
    echo "PASS: 工单 Answer 契约符合（$opted 个 effort 启用契约，覆盖 $checked 张 resolved）"
  fi
  [ "$errors" -eq 0 ]
}

# 工单 `Touches:`（retro S8）：声明本票会改哪些文件，让"前沿能不能并行"成为机械判断
# （`/implement-spec` 的规则是"文件不重叠才并行"）。字段**可选**；写了就必须每条路径都
# 存在 —— 写错的声明比不写更坏（会让人据此做出错误的并行决定）。
#
# 票宽警告（M5 retro）：`open` / `claimed` 的票，`Touches:` 跨 >2 个顶层模块或 >10 个文件
# 时打一行**非失败** WARN。判据是"派发前该不该拆"，不是"声明合不合法"——历史 resolved
# 票（含很宽的重构票）保持安静；门禁只拦"路径不存在"，宽度只提醒。
scan_issue_touches() { # $1=仓库根；返回 1 = 有错
  local root="$1" errors=0 declared=0
  local files=() f line item status modules count
  if [ -d "$root/.scratch" ]; then
    while IFS= read -r f; do files+=("$f"); done < <(
      find "$root/.scratch" -mindepth 3 -maxdepth 3 -type f -path '*/issues/*.md' 2>/dev/null | sort
    )
  fi
  for f in "${files[@]}"; do
    line="$(grep -m1 '^Touches:' "$f" || true)"
    [ -n "$line" ] || continue
    declared=$((declared+1))
    item="${line#Touches:}"
    local items=()
    local IFS=','
    for item in $item; do
      item="$(printf '%s' "$item" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
      [ -n "$item" ] || continue
      items+=("$item")
      if [ ! -e "$root/$item" ]; then
        echo "FAIL: ${f#"$root"/} 的『Touches:』列了不存在的路径：$item"
        errors=$((errors+1))
      fi
    done
    # 票宽：只对**尚未收口**的票出声；`resolved` 票是历史事实，宽度已无派发意义。
    status="$(grep -m1 '^Status:' "$f" | sed -e 's/^Status:[[:space:]]*//' -e 's/[[:space:]]*$//')"
    case "$status" in
      open | claimed) ;;
      *) continue ;;
    esac
    [ "${#items[@]}" -gt 0 ] || continue
    count="${#items[@]}"
    modules="$(
      printf '%s\n' "${items[@]}" | sed -e 's#/.*$##' | sort -u | wc -l | tr -d '[:space:]'
    )"
    if [ "$modules" -gt 2 ] || [ "$count" -gt 10 ]; then
      echo "WARN: ticket ${f#"$root"/} spans $modules top-level modules / $count files — split before dispatch"
    fi
  done
  if [ "$errors" -eq 0 ]; then
    echo "PASS: 工单 Touches 声明全部可解析（$declared 张票声明了 Touches）"
  fi
  [ "$errors" -eq 0 ]
}

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

# 工单 Answer 契约的合成反例（正反两面 + grandfather 面）。
# $1 = 对照票正文（带 Answer），$2 = 被测的 resolved 票正文，$3 = red|green
answer_probe() { # $1=对照票 $2=被测 resolved 票 $3=red|green $4=on|off（是否写自选标记）
  local probe out
  probe="$(probe_mktemp)" || return 1
  mkdir -p "$probe/.scratch/demo/issues"
  if [ "$4" = on ]; then
    printf 'Answer contract: on\n' > "$probe/.scratch/demo/spec.md"
  else
    printf '（未启用契约）\n' > "$probe/.scratch/demo/spec.md"
  fi
  printf '%s\n' "$1" > "$probe/.scratch/demo/issues/01-a.md"
  printf '%s\n' "$2" > "$probe/.scratch/demo/issues/02-b.md"
  out="$(scan_issue_answers "$probe" 2>&1 || true)"
  rm -rf "$probe"
  if [ "$3" = red ]; then out_is_red "$out"; else ! out_is_red "$out"; fi
}
GOOD=$'Type: task\nStatus: resolved\n\n## Answer\n\n落地了。'
BAD_NO_ANSWER=$'Type: task\nStatus: resolved\n\n## Comments'
BAD_EMPTY_ANSWER=$'Type: task\nStatus: resolved\n\n## Answer\n'
if ! answer_probe "$GOOD" "$BAD_NO_ANSWER" red on; then
  echo "FAIL: 合成反例未变红：effort 声明了契约，另一张 resolved 却没有 Answer"
  fail=1
fi
if ! answer_probe "$GOOD" "$BAD_NO_ANSWER" green off; then
  echo "FAIL: grandfather 失效：没声明契约的 effort 却被判红"
  fail=1
fi
if ! answer_probe "$GOOD" "$BAD_EMPTY_ANSWER" red off; then
  echo "FAIL: 合成反例未变红：空的『## Answer』未被判红（全局规则，与契约标记无关）"
  fail=1
fi
if ! answer_probe "$GOOD" "$GOOD" green on; then
  echo "FAIL: 合成反例未变绿：两张都写了 Answer 仍被判红"
  fail=1
fi

if ! scan_issue_answers "$PWD"; then fail=1; fi

# `Touches:` 声明可解析的合成反例（正反两面）。
touches_probe() { # $1=red|green
  local probe out
  probe="$(probe_mktemp)" || return 1
  mkdir -p "$probe/.scratch/demo/issues" "$probe/src"
  : > "$probe/src/real.py"
  if [ "$1" = red ]; then
    printf 'Type: task\nStatus: open\nTouches: src/nope.py\n' \
      > "$probe/.scratch/demo/issues/01-a.md"
  else
    printf 'Type: task\nStatus: open\nTouches: src/real.py, src/real.py\n' \
      > "$probe/.scratch/demo/issues/01-a.md"
  fi
  out="$(scan_issue_touches "$probe" 2>&1 || true)"
  rm -rf "$probe"
  if [ "$1" = red ]; then out_is_red "$out"; else ! out_is_red "$out"; fi
}
if ! touches_probe red; then
  echo "FAIL: 合成反例未变红：Touches 列了不存在的路径未被判红"
  fail=1
fi
if ! touches_probe green; then
  echo "FAIL: 合成反例未变绿：Touches 全是存在的路径仍被判红"
  fail=1
fi

# 票宽警告的合成反例（M5 retro）：宽票要 WARN 但**不许** FAIL；窄票 / 历史 resolved 静默。
touches_width_probe() { # $1=Status $2=warn|quiet $3=Touches 值
  local probe out
  probe="$(probe_mktemp)" || return 1
  mkdir -p "$probe/.scratch/demo/issues" "$probe/src" "$probe/tests" "$probe/tools"
  : > "$probe/src/a.py"
  : > "$probe/src/a2.py"
  : > "$probe/tests/b.py"
  : > "$probe/tools/c.py"
  local i
  for i in $(seq 1 11); do : > "$probe/src/f$i.py"; done
  printf 'Type: task\nStatus: %s\nTouches: %s\n' "$1" "$3" \
    > "$probe/.scratch/demo/issues/01-a.md"
  out="$(scan_issue_touches "$probe" 2>&1 || true)"
  rm -rf "$probe"
  # 宽度只提醒：任何一档都不许出现 FAIL（那会把"建议拆票"变成门禁失败）。
  if out_is_red "$out"; then return 1; fi
  if [ "$2" = warn ]; then out_has_warn "$out"; else ! out_has_warn "$out"; fi
}
WIDE_FILES="$(printf 'src/f%d.py, ' $(seq 1 11) | sed 's/, $//')"
if ! touches_width_probe open warn "src/a.py, tests/b.py, tools/c.py"; then
  echo "FAIL: 合成反例未触发：open 票跨 3 个顶层模块没有 WARN（或误判成 FAIL）"
  fail=1
fi
if ! touches_width_probe claimed warn "$WIDE_FILES"; then
  echo "FAIL: 合成反例未触发：claimed 票同模块 11 个文件没有 WARN（或误判成 FAIL）"
  fail=1
fi
if ! touches_width_probe open quiet "src/a.py, src/a2.py"; then
  echo "FAIL: 合成反例未静默：open 票 1 个模块 2 个文件不应 WARN"
  fail=1
fi
if ! touches_width_probe resolved quiet "src/a.py, tests/b.py, tools/c.py"; then
  echo "FAIL: 历史票未静默：resolved 票跨 3 个顶层模块不应 WARN"
  fail=1
fi
if ! scan_issue_touches "$PWD"; then fail=1; fi

# AI 适配契约（票 17 / spec §4.1）：三份 AI 文档进仓，正文只放一份，指针各留一行。
# 这里核"文档在 + 四节正文在 + 指针在"；笔记式的判据正文不抄第二份（漂移的源头）。
for ai_doc in docs/agents/ai-adaptation.md docs/agents/ai-versions.md \
  docs/agents/acceptance-ai.md; do
  check_exists "$ai_doc"
done
check_file docs/agents/ai-adaptation.md "新机制票 checklist" "新机制票 checklist"
check_file docs/agents/ai-adaptation.md "新模式票 checklist" "新模式票 checklist"
check_file docs/agents/ai-adaptation.md "三张登记表" "三张登记表"
check_file docs/agents/ai-adaptation.md "维护责任表" "维护责任表"
check_file AGENTS.md "docs/agents/ai-adaptation.md" "AI 适配契约指针"
check_file AGENTS.md "docs/agents/ai-versions.md" "AI 版本台账指针"
check_file AGENTS.md "docs/agents/acceptance-ai.md" "AI 实机验收指针"
check_file docs/agents/skill-authoring/batch-ticket.md "docs/agents/ai-adaptation.md" "一将一票的适配指针"

# 合成反例：把指针 / 文档正文从副本里拿掉，上面的断言必须变红（防新增检查空转通过）。
pointer_removal_goes_red() { # $1=源文件 $2=字面串
  local probe out
  probe="$(probe_mktemp)" || return 1
  grep -vF "$2" "$1" > "$probe/stripped.md" || true
  out="$(check_file "$probe/stripped.md" "$2" "指针" 2>&1 || true)"
  rm -rf "$probe"
  out_is_red "$out"
}

missing_doc_goes_red() {
  local probe out
  probe="$(probe_mktemp)" || return 1
  out="$(check_exists "$probe/no-such-doc.md" 2>&1 || true)"
  rm -rf "$probe"
  out_is_red "$out"
}

if ! pointer_removal_goes_red AGENTS.md "docs/agents/ai-adaptation.md"; then
  echo "FAIL: 合成反例未变红：删掉 AGENTS.md 的 AI 适配指针未被判红"
  fail=1
fi
if ! pointer_removal_goes_red AGENTS.md "docs/agents/ai-versions.md"; then
  echo "FAIL: 合成反例未变红：删掉 AGENTS.md 的版本台账指针未被判红"
  fail=1
fi
if ! pointer_removal_goes_red docs/agents/skill-authoring/batch-ticket.md \
  "docs/agents/ai-adaptation.md"; then
  echo "FAIL: 合成反例未变红：删掉一将一票的适配指针未被判红"
  fail=1
fi
if ! pointer_removal_goes_red docs/agents/ai-adaptation.md "维护责任表"; then
  echo "FAIL: 合成反例未变红：删掉适配契约的维护责任表未被判红"
  fail=1
fi
if ! missing_doc_goes_red; then
  echo "FAIL: 合成反例未变红：缺少文档未被判红（check_exists 空转）"
  fail=1
fi

# 防再漏（票 05）：探针不得改动仓库工作树，也不得在仓库根留 .wb_probe.* 残留。
# 断言本身也要有反面自检（覆盖元断言），防 find / 比较失效时空转通过。
if ! probe_discipline_check "$REPO_TREE_BEFORE"; then fail=1; fi
if ! probe_discipline_selftest; then fail=1; fi
if ! check_file_missing_goes_red; then
  echo "FAIL: check_file 自检失效——对不存在的文件仍判绿"
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  echo "PASS: Agent skills 配置校验全部通过"
fi
exit "$fail"
