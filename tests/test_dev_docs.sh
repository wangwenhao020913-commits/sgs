#!/usr/bin/env bash
# 校验项目开发文档齐全且包含关键设计要点。
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="开发文档"
fail=0

check() { # $1=文件 $2=字面串 $3=描述 $4=absent 时断言「不得出现」，缺省断言「必须出现」
  local hit=0
  if grep -qF "$2" "$DIR/$1"; then hit=1; fi
  if [ "${4:-present}" = absent ]; then
    if [ "$hit" -eq 1 ]; then
      echo "FAIL: $1 仍含『$3』（$2）"
      fail=1
      return
    fi
    echo "PASS: $1 已不含『$3』"
    return
  fi
  if [ "$hit" -eq 0 ]; then
    echo "FAIL: $1 缺少『$3』（未找到：$2）"
    fail=1
    return
  fi
  echo "PASS: $1 含『$3』"
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

# 票 02（M0 收尾）回写《开发文档/02-规则引擎设计.md》§3 的时机系统：D-1…D-9。
# 依据：.scratch/m1-engine-skeleton/research/01-timing-enum-alignment.md §5.2；
# 权威表 = src/sgs/timing/enum.py 的 83 节点。
DOC02="02-规则引擎设计.md"
check "$DOC02" '| 合计 | **时机节点** | **83** |' "D-8 权威表恰 83 节点"
check "$DOC02" '`L6`–`L17`' "D-2 其它事件层 L6–L17"
check "$DOC02" '`GAME_HERO_REVEALED`' "D-7/D-8 游戏开始前亮出武将牌后"
check "$DOC02" '`GAME_INITIAL_HAND_DEALING`' "D-7/D-8 游戏开始前分发起始手牌时"
check "$DOC02" '`GAME_START`' "D-7/D-8 游戏开始时"
check "$DOC02" '`PHASE_PLAY_IDLE`' "D-7/D-8 出牌阶段空闲时间点枚举位"
check "$DOC02" "每一个空闲时间点都是一个独立时机" "D-7 空闲时间点独立时机语义"
check "$DOC02" '`TURN_AFTER_END`' "D-8 回合结束后标识符"

check "$DOC02" "### 3.0 使用前校验" "D-5 使用前校验小节"
check "$DOC02" "没有官方时机名" "D-5 使用前校验不是时机"
check "$DOC02" '`check_forbidden`' "D-5 禁止使用牌效果检测函数"
check "$DOC02" '`check_count`' "D-5 使用次数检测函数"
check "$DOC02" '`check_targets`' "D-5 额定目标合法性检测函数"
check "$DOC02" '`USE_CHECK_FORBIDDEN`' "D-5 研究 L3.1 标签"
check "$DOC02" '`USE_CHECK_COUNT`' "D-5 研究 L3.2 标签"
check "$DOC02" '`USE_CHECK_TARGETS`' "D-5 研究 L3.3 标签"

check "$DOC02" '`USE_ON_USE`' "D-8 使用时标识符（G-16 命名统一）"
check "$DOC02" '### 3.2 使用结算前（`L4`，六个，**牌级**）' "D-9 L4 牌级推进"
check "$DOC02" '### 3.3 使用结算中与使用结算后（`L5`，**逐目标**）' "D-9 L5 逐目标推进"
check "$DOC02" '`RESOLVE_AFTER_END`' "D-1 使用结算结束后（第 6 个节点）"
check "$DOC02" "牌级收尾（全部目标之后一次）" "D-1 L5.6 牌级收尾语义"
check "$DOC02" "登记待定，不静默取舍" "D-9 多目标推进口径登记为待定"

check "$DOC02" '`PLAY_DECLARE`' "D-2 打出事件两时机"
check "$DOC02" '`PLAY_ON_PLAY`' "D-2 打出事件两时机"
check "$DOC02" '`DAMAGE_BEFORE_START`' "D-2/D-3 伤害结算开始前（删『防止伤害』时机）"
check "$DOC02" '`DAMAGE_AFTER_END`' "D-2 伤害结算结束后"
check "$DOC02" '`LIFE_DECREASE_BEFORE`' "D-2 扣减体力三时机"
check "$DOC02" '`LIFE_LOSE_ON`' "D-2 失去体力两时机"
check "$DOC02" "确定回复数值" "D-3 回复体力第 1 时机改名"
check "$DOC02" '`MAXLIFE_DECREASE_BEFORE`' "D-2/D-3 减体力上限前"
check "$DOC02" '`MAXLIFE_INCREASE_AFTER`' "D-2/D-3 加体力上限后"
check "$DOC02" '`NEAR_DEATH_ENTER`' "D-2 濒死事件三时机"
check "$DOC02" '`DEATH_BEFORE_START`' "D-2 死亡结算开始前"
check "$DOC02" "主公主死则游戏结束" "D-4/G-9 主公主死在死亡结算开始前"
check "$DOC02" '`DEATH_REVEAL_IDENTITY_AFTER`' "D-4 胜负检测在亮出身份牌后"
check "$DOC02" "胜负判定点" "D-4 死亡行位置纠偏"
check "$DOC02" '`JUDGE_ON`' "D-2 判定事件三时机"
check "$DOC02" '`RANK_COMPARE_ON`' "D-2/D-3 拼点仅两时机"
check "$DOC02" '`MOVE_BEFORE`' "D-2 牌移动两时机"
check "$DOC02" '`STATE_FLIPPED`' "D-2 牌状态改变五时机"

check "$DOC02" "后发生的事件先结算" "D-6 L18.1 后发先至"
check "$DOC02" "武将技能 > 装备技能 > 使用牌" "D-6 L18.5 单角色技能优先级"
check "$DOC02" "不属于任何角色的回合" "D-6 L18.8 响应起点切换"

# 伪时机与已删节点不得作为时机行残留
check "$DOC02" "防止伤害 → 造成伤害时 → 扣减体力 → 濒死 → 造成伤害后 → 受到伤害后" "旧伤害行伪时机链" absent
check "$DOC02" "回复体力时 / 后" "旧『回复体力时 / 后』行" absent
check "$DOC02" "增减体力上限时 / 后" "旧『增减体力上限时 / 后』行" absent
check "$DOC02" "进行拼点时 → 亮出拼点牌后 → 赢/没赢后" "旧拼点三时机链" absent
check "$DOC02" "死亡时 → 死亡后（奖惩与胜负判定）" "旧死亡行" absent

# 票 03（M0 收尾）回写《开发文档/05-测试与验证策略.md》§2 / §5 的时机测试映射：T-1 / T-2。
# 依据：.scratch/m1-engine-skeleton/research/01-timing-enum-alignment.md §5.3；
# 落点 = tests/test_timing.py（83 节点、伪时机禁令）与 tests/test_timing_coverage.py（47/11/25 三分区）。
DOC05="05-测试与验证策略.md"
check "$DOC05" "时机表完整性" "T-1 映射表时机表完整性行"
check "$DOC05" "83 个节点的枚举值与顺序" "T-1 权威表的 83 节点与顺序"
check "$DOC05" "伪时机不得出现" "T-1 伪时机负测试行"
check "$DOC05" "tests/test_timing.py" "T-1 枚举与伪时机禁令的落点"
check "$DOC05" "时机覆盖清单" "T-1 映射表时机覆盖清单行"
check "$DOC05" "47 dispatch 白名单 / 11 空日志 / 25 枚举位" "T-1 三分区口径"
check "$DOC05" "tests/test_timing_coverage.py" "T-1 覆盖清单的判决落点"
check "$DOC05" "§M2 的验收不冲突" "T-1 与 06 §M2 的验收口径对齐"
check "$DOC05" "Golden Replay 用例 ≥ 30 条" "T-1 承接 06 §M2 的基准条数"
check "$DOC05" "不变量 fuzz 无失败" "T-1 承接 06 §M2 的 fuzz 口径"
check "$DOC05" "（T-1）" "T-1 回写逐处标注依据"

check "$DOC05" "插入结算顺序" "T-2 插入结算顺序"
check "$DOC05" "tests/test_use_and_resolve.py" "T-2 伤害 / 扣减体力嵌套落点"
check "$DOC05" "tests/test_near_death.py" "T-2 濒死 / 回复插入落点"
check "$DOC05" "tests/test_resolution_stack.py" "T-2 栈 / 恢复点落点"
check "$DOC05" "tests/test_turn_loop.py" "T-2 回合环顺序落点"
check "$DOC05" "不属于任何角色的回合起点" "T-2 L18.8 响应起点切换"
check "$DOC05" "从其下家起" "T-2 L18.8 两个起点都登记"
check "$DOC05" "登记为 M2" "T-2 实战走不到项显式登记 M2"
check "$DOC05" "（T-2）" "T-2 回写逐处标注依据"

# 票 04（M0 收尾）回写 ADR-007 的 M1 语义重述、`01` §1.2 的人数口径与 `06` §5 的检查清单。
# 依据：票 13 §3 的重述原文 / `G-10`；ADR 变更的连带同步（规则文档 / 配置项 / 相关测试）
# 逐条记录在 `06` §4。
DOC06="06-里程碑与风险.md"
DOC01="01-总体设计.md"

check "$DOC06" '检测点 ≠ 终止点' "ADR-007 重述：胜负检测点与终止点分离"
check "$DOC06" '不再插入任何新结算' "ADR-007 重述：满足后不再插入新结算"
check "$DOC06" '死亡结算**作为原子单元跑完' "ADR-007 重述：死亡结算作为原子单元跑完"
check "$DOC06" '清空结算栈、丢弃上层帧、宣布胜负' "ADR-007 重述：终止点动作"
check "$DOC06" '即时终止一切结算' "旧 ADR-007 字面已不再是当前决定" absent
check "$DOC06" '官方身份局在胜负检测点即中止结算' "ADR-007 冲突说明：与官方身份局的差异"
check "$DOC06" '**冲突说明（依据票 13 §3 的 `G-10`）**' "ADR-007 冲突说明：逐处标注依据"
check "$DOC06" '配置项 = **无**' "ADR-007 连带同步：配置项无改动"
check "$DOC06" '`tests/test_death.py`' "ADR-007 连带同步：相关测试落点"
check "$DOC06" '`G-9`（4.18 死亡五段）/ `G-10`（4.16 三条调度规则）' "ADR-007 连带同步：规则文档 G-9/G-10"
check "$DOC06" '与本节文字一致，无需订正' "GLOSSARY 回归：胜负检测点 / 终止点一致（无改动）"
check "$DOC06" '无身份 1v1（2 人）' "06 §M1 与 01 §1.2 的人数口径一致"

check "$DOC06" '无一项推后到 M2' "06 §5 收口：全部满足、无推后项"
check "$DOC06" '[x] 规则文档缺口已补' "06 §5 第 1 项已勾"
check "$DOC06" '[x] 开发文档 01–06 已评审通过' "06 §5 第 2 项已勾"
check "$DOC06" '[x] ADR-001～007 已确认' "06 §5 第 3 项已勾"
check "$DOC06" '[x] `tests/run_all.sh` 全绿' "06 §5 第 4 项已勾"
check "$DOC06" '[x] 已确定 M1 的“杀闪桃”最小对局验收用例' "06 §5 第 5 项已勾"

check "$DOC01" '最终形态 = 身份局 4～8 人（MVP 首期 5 人）' "01 §1.2 最终形态口径"
check "$DOC01" 'M1 = 无身份 1v1（2 人，已完成）' "01 §1.2 M1 里程碑口径"
check "$DOC01" 'M2 = 标准版身份局 4～8 人' "01 §1.2 M2 里程碑口径"
check "$DOC01" '身份局 4～8 人（首期 5 人）' "01 §1.2 旧人数口径已改写" absent

if [ "$fail" -eq 0 ]; then
  echo "PASS: 开发文档校验全部通过"
fi
exit "$fail"
