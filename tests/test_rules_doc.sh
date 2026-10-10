#!/usr/bin/env bash
# 校验《三国杀详细规则.txt》的迁移结果与新增章节完整性。
set -euo pipefail
cd "$(dirname "$0")/.."

DOC="三国杀详细规则.txt"
DOC_PATH="$DOC"   # 票 11 合成反例的注入点：可临时指向副本文件
fail=0

# 合成反例的临时副本目录只在 $TMPDIR 下建（票 05）：探针中断也不在仓库根留残留目录。
PROBE_TMP="${TMPDIR:-/tmp}"
probe_mktemp() { mktemp -d "${PROBE_TMP%/}/.wb_probe.XXXXXX"; }
# 防再漏（票 05）：探针只许动 $TMPDIR。比对探针前后的 git 工作树快照，并检查仓库根
# 无 .wb_probe.* 残留——两者任一不满足即判红（挡「有人把探针目录改回仓库内」的回归）。
repo_tree_snapshot() { git status --porcelain --untracked-files=all 2>/dev/null | sort; }
probe_residue() { find "$PWD" -maxdepth 1 -name '.wb_probe.*' -print 2>/dev/null; }

if [ ! -f "$DOC" ]; then
  echo "FAIL: 缺少 $DOC"
  exit 1
fi

check() { # $1=描述 $2=字面串 $3=absent 时断言“不得出现”，缺省断言“必须出现”
  local hit=0
  if grep -qF "$2" "$DOC_PATH"; then hit=1; fi
  if [ "${3:-present}" = absent ]; then
    if [ "$hit" -eq 1 ]; then
      echo "FAIL: $1（仍出现：$2）"
      fail=1
      return
    fi
  elif [ "$hit" -eq 0 ]; then
    echo "FAIL: $1（未找到：$2）"
    fail=1
    return
  fi
  echo "PASS: $1"
}

check "含『3.8 回合轮换规则』" "3.8 回合轮换规则"
check "含『3.9 回合与阶段的完整时点』" "3.9 回合与阶段的完整时点"
check "含『17.10 使用每一张牌所经历的全步骤』" "17.10 使用每一张牌所经历的全步骤"
check "含『17.11 使用与打出的流程差异』" "17.11 “使用”与“打出”的流程差异"
check "含使用前的合法性检测" "合法性检测（能否使用）"
check "含使用结算前六个时机" "使用结算前：六个时机"
check "含使用结算中五个时机" "使用结算中：五个时机"
check "含回合开始前翻面跳回合" "回合开始前"
check "含逆时针行动顺序" "逆时针方向"
check "含『4.13 数值变化事件的四种类型』" "4.13 数值变化事件的四种类型"
check "含『4.14 受到伤害的完整流程』" "4.14 受到伤害的完整流程"
check "含『4.16 结算的原则』" "4.16 结算的原则"
check "含『4.17 濒死事件的完整流程』" "4.17 濒死事件的完整流程"
check "含『4.18 死亡结算顺序与同时死亡』" "4.18 死亡结算顺序与同时死亡"
check "含『4.19 胜负判定与游戏结束』" "4.19 胜负判定与游戏结束"
check "含『6.16 无懈可击的响应窗口与嵌套』" "6.16 无懈可击的响应窗口与嵌套"
check "含『10.4 洗牌、摸牌与牌堆耗尽』" "10.4 洗牌、摸牌与牌堆耗尽"
check "含技能时点说明" "技能时点说明（程序化对照）"

# 票 01（M0 收尾）回写《三国杀详细规则.txt》的时机缺口：G-11 / G-12 / G-13
check "G-11 含游戏开始前亮出武将牌后" "游戏开始前亮出武将牌后"
check "G-11 含游戏开始前分发起始手牌时" "游戏开始前分发起始手牌时"
check "G-11 含游戏开始时" "游戏开始时（起始手牌分发完毕、第一个回合开始之前）"
check "G-12 含空闲时间点独立时机语义" "每一个空闲时间点都是一个独立的时机"
check "G-13 含额外回合改写下一轮顺序" "改写下一轮的角色回合顺序"
check "G-13 含额外回合顺序示例" "E → B → C → D → E → A"
# G-3…G-10：第四章的伤害 / 体力 / 濒死 / 死亡 / 调度
check "G-3 含伤害结算开始前" "伤害结算开始前"
check "G-3 含伤害结算结束后" "伤害结算结束后"
check "G-3 含伤害减为 0 即防止全部伤害" "伤害减为 0 即防止全部点数的伤害"
check "G-4 含扣减体力前" "扣减体力前"
check "G-4 含扣减体力时" "扣减体力时"
check "G-4 含扣减体力后" "扣减体力后"
check "G-5 含失去体力时" "失去体力时"
check "G-5 含失去体力后" "失去体力后"
check "G-6 含确定回复数值" "确定回复数值"
check "G-6 含回复体力后" "回复体力后"
check "G-7 含减体力上限前" "减体力上限前"
check "G-7 含减体力上限后" "减体力上限后"
check "G-7 含加体力上限前" "加体力上限前"
check "G-7 含加体力上限后" "加体力上限后"
check "G-7 含上限减为 0 不进入濒死" "不进入濒死"
check "G-8 含进入濒死状态时" "进入濒死状态时"
check "G-8 含处于濒死状态时" "处于濒死状态时"
check "G-8 含濒死结算结束后" "濒死结算结束后"
check "G-8 含新濒死回退到上家" "当前回合角色的上家"
check "G-9 含死亡结算开始前" "死亡结算开始前"
check "G-9 含亮出身份牌前" "亮出身份牌前"
check "G-9 含亮出身份牌后" "亮出身份牌后"
check "G-9 含死亡时" "死亡时（技能时机）"
check "G-9 含死亡后" "死亡后（死亡结算的最后一个时机）"
check "G-10 含后发生的事件先结算" "后发生的事件先结算"
check "G-10 含武将技能 > 装备技能 > 牌" "武将技能 > 装备技能"
check "G-10 含不属于任何角色的回合起点" "不属于任何角色的回合"
# G-1 / G-2 / G-14…G-17：打出与牌移动流程、基本牌与第十七章命名统一
check "G-14 含【闪】使用时机为生效前" "以你为目标的【杀】生效前"
check "G-14 含【闪】是使用事件" "【闪】属于**使用**事件"
check "G-17 含合法性检测非时机注记" "合法性检测是校验阶段，不是时机"
check "G-1 含打出事件的结算流程" "打出事件的结算流程"
check "G-1 含声明打出的牌名时" "声明打出的牌名时"
check "G-1 含声明即视为响应成功" "声明打出的牌名即视为响应成功"
check "G-2 含牌移动的结算流程" "牌移动的结算流程"
check "G-2 含移动到目标区域前" "移动到目标区域前"
check "G-2 含移动到目标区域后" "移动到目标区域后"
check "G-2 含移动的来源清单" "移动的**来源**"
check "G-2 含移动来源完整清单" "使用 / 打出 / 重铸 / 弃置 / 交给 / 拼点 / 交换 / 获得"
check "G-15 已消除『成为指定目标』" "成为指定目标" absent
check "G-16 17.10 已消除时机名『使用牌时』" "B2. 使用牌时" absent
check "G-16 已消除『使用牌时』类技能写法" "“使用牌时”类技能" absent

# 票 02（M2 文档回写）回写《三国杀详细规则.txt》的四处"保留原文 + 校订 / 程序化约定注"。
# 依据：.scratch/m2-doc-writeback/spec.md 逐行口径 #3 / #24 / #25 与追加行 A3。
check "6.16 校订注作废约定 A 的开关" "程序化约定注作废"
check "6.16 取约定 B 且不建配置开关" "本项目 M2 取 B，且**不建配置"
check "内奸条件重述为主公死亡瞬间的唯一存活者" "主公死亡瞬间的唯一存活者"
check "主公杀忠臣弃置范围取所有牌（含判定区）" "弃置范围 = 死者区域里的**全部牌**"
check "R:16.3 属性不继承原牌" "属性不继承原牌"
check "R:16.3 多牌转化颜色按来源同色派生" "颜色按来源牌同色派生"
check "R:16.3 保留属性旧读法的痕迹" "本节曾按社区主流读作"
check "R:16.3 旧属性口径已退役" "属性一并保留" absent
check "R:16.3 旧多牌结论已退役" "因此不被" absent

# 章节数量应为 21
n=$(grep -cE '^第[一二三四五六七八九十]+章  ' "$DOC" || true)
if [ "$n" -eq 21 ]; then
  echo "PASS: 章节数为 21"
else
  echo "FAIL: 章节数应为 21，实际 $n"
  fail=1
fi

# 参考资料迁移完整性
for f in \
  "参考资料/机制详解-官方规则集研究.md" \
  "参考资料/虚拟牌与特殊使用规则研究.txt" \
  "参考资料/界限突破-界标准包武将资料.md"
do
  if [ -f "$f" ]; then
    echo "PASS: 存在 $f"
  else
    echo "FAIL: 缺少 $f"
    fail=1
  fi
done

# 旧小写 sgs 目录应已迁移并删除
if [ -d "../sgs" ]; then
  echo "FAIL: 旧 sgs 目录仍存在，迁移未完成"
  fail=1
else
  echo "PASS: 旧 sgs 已迁移并删除"
fi

# ===========================================================================
# 回写纪律（票 11）：规则文档侧的两头钉 + 覆盖元断言 + 合成反例。
# 黑名单与 `tests/test_dev_docs.sh` 的同名段一致（spec「机械断言的形态」节）。
# ===========================================================================

# 每条 = "字面串|描述"。
# 黑名单只列**本轮真的从规则文档退役**的串；`开发文档/` 侧那批（配置留开关 / 存疑点开关 /
# 200 局 / 30 s / 47 dispatch 白名单 / （MVP 首期 5 人）/ 判定阶段空过 / 10 武器 …）由
# `tests/test_dev_docs.sh` 的黑名单统一钉住，不在这里对规则文档重复做空洞的 absent。
WRITEBACK_NEW=(
  '程序化约定注作废|6.16 作废约定 A 的程序化约定注'
  '本项目 M2 取 B，且**不建配置|6.16 取约定 B 且不建配置开关'
  '主公死亡瞬间的唯一存活者|内奸获胜条件重述'
  '弃置范围 = 死者区域里的**全部牌**|主公杀忠臣弃置范围取所有牌'
  '属性不继承原牌|16.3 属性不继承原牌'
  '颜色按来源牌同色派生|16.3 多牌转化颜色按来源派生'
  '本节曾按社区主流读作|16.3 保留属性旧读法的痕迹'
  '弃置范围按 4.8 的校订注|4.18 / 20.6 主公杀忠臣弃置范围的交叉引用注（A8）'
  '内奸条件已重述为|4.18 内奸同死句的交叉引用注（A8）'
)

WRITEBACK_OLD=(
  '属性一并保留|旧 16.3 属性社区读法（本轮从本文档退役）'
  '因此不被|旧 16.3 多牌转化结论（本轮从本文档退役）'
)

for claim in "${WRITEBACK_NEW[@]}"; do
  IFS='|' read -r literal desc <<< "$claim"
  check "$desc" "$literal"
done
for claim in "${WRITEBACK_OLD[@]}"; do
  IFS='|' read -r literal desc <<< "$claim"
  check "$desc" "$literal" absent
done

# 覆盖元断言：目标文件必须真实存在；本票的退役串清单不得被清空成空转。
if [ ! -f "$DOC_PATH" ]; then
  echo "FAIL: 回写纪律声明的目标文件不存在：$DOC_PATH"
  fail=1
fi
if [ "${#WRITEBACK_OLD[@]}" -lt 1 ]; then
  echo "FAIL: 规则文档侧没有任何退役串断言（覆盖元断言）"
  fail=1
fi
if [ "${#WRITEBACK_NEW[@]}" -lt 7 ]; then
  echo "FAIL: 规则文档侧的新串断言少于 7 条（覆盖元断言）"
  fail=1
fi

# 合成反例：把任一旧串临时写回副本，相同的 absent 断言必须变红。
writeback_absent_goes_red() { # $1=字面串；0 = 确实变红
  local probe path out
  probe="$(probe_mktemp)" || return 1
  path="$probe/$(basename "$DOC_PATH")"
  if ! cp "$DOC_PATH" "$path" 2>/dev/null; then
    rm -rf "$probe"
    return 1
  fi
  printf '\n%s\n' "$1" >> "$path"
  out="$(DOC_PATH="$path" check "合成反例" "$1" absent 2>&1 || true)"
  rm -rf "$probe"
  case "$out" in *FAIL*) return 0;; *) return 1;; esac
}

REPO_TREE_BEFORE="$(repo_tree_snapshot)"
for claim in "${WRITEBACK_OLD[@]}"; do
  IFS='|' read -r literal desc <<< "$claim"
  if ! writeback_absent_goes_red "$literal"; then
    echo "FAIL: 合成反例未变红：把『$literal』写回后 absent 断言仍通过"
    fail=1
  fi
done

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
  echo "PASS: 规则文档校验全部通过"
fi
exit "$fail"
