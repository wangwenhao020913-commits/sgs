#!/usr/bin/env bash
# 校验项目开发文档齐全且包含关键设计要点。
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="开发文档"
fail=0

check() { # $1=文件 $2=需包含的字面串 $3=描述
  if grep -qF "$2" "$DIR/$1"; then
    echo "PASS: $1 含『$3』"
  else
    echo "FAIL: $1 缺少『$3』（未找到：$2）"
    fail=1
  fi
}

# 文件齐全
for f in README.md 01-总体设计.md 02-规则引擎设计.md 03-数据模型与技能描述.md \
         04-AI与交互设计.md 05-测试与验证策略.md 06-里程碑与风险.md; do
  if [ -f "$DIR/$f" ]; then
    echo "PASS: 存在 $DIR/$f"
  else
    echo "FAIL: 缺少 $DIR/$f"
    fail=1
  fi
done

# 每份文档应有实质内容（>= 30 行）
for f in "$DIR"/*.md; do
  n=$(wc -l < "$f")
  if [ "$n" -ge 30 ]; then
    echo "PASS: $f 有实质内容（$n 行）"
  else
    echo "FAIL: $f 内容过少（$n 行）"
    fail=1
  fi
done

# 关键设计要点
check "README.md" "文档清单与阅读顺序" "索引结构"
check "01-总体设计.md" "总体架构" "分层架构"
check "01-总体设计.md" "技术选型" "技术选型"
check "01-总体设计.md" "MVP 范围" "范围界定"
check "02-规则引擎设计.md" "时机系统" "时机系统"
check "02-规则引擎设计.md" "事件与结算栈" "结算栈"
check "02-规则引擎设计.md" "询问 / 响应模型" "询问响应"
check "02-规则引擎设计.md" "确定性" "确定性要求"
check "03-数据模型与技能描述.md" "技能描述模型" "技能四元组"
check "03-数据模型与技能描述.md" "效果原语" "效果原语"
check "03-数据模型与技能描述.md" "数据校验规则" "数据校验"
check "04-AI与交互设计.md" "信息可见性" "隐藏信息"
check "04-AI与交互设计.md" "AI 分层设计" "AI 分层"
check "04-AI与交互设计.md" "观战与回放" "回放"
check "05-测试与验证策略.md" "Golden Replay" "回放测试"
check "05-测试与验证策略.md" "不变量测试" "不变量"
check "05-测试与验证策略.md" "门禁与交付标准" "交付门禁"
check "06-里程碑与风险.md" "里程碑规划" "里程碑"
check "06-里程碑与风险.md" "风险清单与对策" "风险"
check "06-里程碑与风险.md" "决策记录（ADR）" "决策记录"
check "06-里程碑与风险.md" "ADR-001" "存疑点登记"

if [ "$fail" -eq 0 ]; then
  echo "PASS: 开发文档校验全部通过"
fi
exit "$fail"
