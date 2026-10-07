#!/usr/bin/env bash
# 校验《三国杀详细规则.txt》的迁移结果与新增章节完整性。
set -euo pipefail
cd "$(dirname "$0")/.."

DOC="三国杀详细规则.txt"
fail=0

if [ ! -f "$DOC" ]; then
  echo "FAIL: 缺少 $DOC"
  exit 1
fi

check() { # $1=描述 $2=需包含的字面串
  if grep -qF "$2" "$DOC"; then
    echo "PASS: $1"
  else
    echo "FAIL: $1（未找到：$2）"
    fail=1
  fi
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

if [ "$fail" -eq 0 ]; then
  echo "PASS: 规则文档校验全部通过"
fi
exit "$fail"
