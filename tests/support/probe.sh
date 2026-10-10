#!/usr/bin/env bash
# 合成反例探针的共用纪律（票 05）：临时副本目录只建在 $TMPDIR；跑完证明探针没动过仓库树。
#
# 被 tests/test_dev_docs.sh / test_rules_doc.sh / test_agent_skills.sh `source`。
# 不是门禁入口：tests/run_all.sh 只跑 `test_*.sh`，这份只做被引入的辅助。
#
# 用法：调用方在**第一个探针之前**取 `REPO_TREE_BEFORE="$(repo_tree_snapshot)"`，
# 在所有探针之后调用 `probe_discipline_check "$REPO_TREE_BEFORE"`。

# 临时副本目录只在 $TMPDIR（默认 /tmp）下建：探针中断也不在仓库根留残留目录。
PROBE_TMP="${TMPDIR:-/tmp}"
probe_mktemp() { mktemp -d "${PROBE_TMP%/}/.wb_probe.XXXXXX"; }

# git 工作树快照。git 不可用时返回空串——是否空转由 probe_discipline_check 的
# 覆盖元断言兜底（非 git 工作树直接判红）。
repo_tree_snapshot() { git status --porcelain --untracked-files=all 2>/dev/null | sort; }

# $1=待查根目录（缺省仓库根）下的探针残留。
probe_residue() { find "${1:-$PWD}" -maxdepth 1 -name '.wb_probe.*' -print 2>/dev/null; }

# 探针纪律断言：仓库工作树不得被探针改动，且仓库根不得留 .wb_probe.* 残留。
# $1 = 探针前快照。返回 1 = 有错（FAIL 已 echo）。
probe_discipline_check() {
  local before="$1" errors=0 residue
  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "FAIL: 探针防漏断言失效——不在 git 工作树内，git status 会空转通过"
    errors=$((errors+1))
  elif [ "$(repo_tree_snapshot)" != "$before" ]; then
    echo "FAIL: 合成反例探针改动了仓库工作树（探针目录必须只在 \$TMPDIR 下）"
    repo_tree_snapshot | head -20
    errors=$((errors+1))
  fi
  residue="$(probe_residue)"
  if [ -n "$residue" ]; then
    echo "FAIL: 仓库根残留合成反例探针目录：$(printf '%s ' $residue)"
    errors=$((errors+1))
  fi
  [ "$errors" -eq 0 ]
}

# 自检（覆盖元断言的反面）：两半都必须真的会变红。
#   ① probe_residue 必须报出伪造的残留（防 find 失效 ⇒ 空转通过）；
#   ② 换掉「探针前快照」后 probe_discipline_check 必须判红（防比较失效 ⇒ 空转通过）。
probe_discipline_selftest() {
  local tmp out ok=0
  tmp="$(mktemp -d "${PROBE_TMP%/}/.wb_probe_selftest.XXXXXX")" || return 1
  mkdir -p "$tmp/.wb_probe.FAKE"
  out="$(probe_residue "$tmp")"
  rm -rf "$tmp"
  case "$out" in
    *'.wb_probe.FAKE'*) ;;
    *) echo "FAIL: 探针残留自检失效——伪造残留未被报出（find 失效？）"; ok=1;;
  esac
  if probe_discipline_check "___selftest_bogus_before___" >/dev/null 2>&1; then
    echo "FAIL: 探针防漏自检失效——篡改前后快照仍判绿（比较失效？）"
    ok=1
  fi
  [ "$ok" -eq 0 ]
}
