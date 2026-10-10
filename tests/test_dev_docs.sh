#!/usr/bin/env bash
# 校验项目开发文档齐全且包含关键设计要点。
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="开发文档"
DOC_DIR="$DIR"   # 票 11 合成反例的注入点：可临时指向副本目录
fail=0

# 合成反例探针纪律（票 05）：探针目录只在 $TMPDIR；跑完证明没动过仓库树。
# shellcheck source=tests/support/probe.sh
source tests/support/probe.sh

check() { # $1=文件 $2=字面串 $3=描述 $4=absent 时断言「不得出现」，缺省断言「必须出现」
  local hit=0
  if grep -qF "$2" "$DOC_DIR/$1"; then hit=1; fi
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
check "$DOC05" "53 dispatch 白名单 / 11 空日志 / 19 枚举位" "T-1 三分区口径（M2 定格）"
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

# 票 04（M0 收尾）回写 ADR-007 的 M1 语义重述、`01` §1.2 的人数口径与 `06` §6「进入实现前的检查清单」。
# 依据：票 13 §3 的重述原文 / `G-10`；ADR 变更的连带同步（规则文档 / 配置项 / 相关测试）
# 逐条记录在 `06` §4「决策记录（ADR）」。
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

check "$DOC06" '无一项推后到 M2' "06 §6「进入实现前的检查清单」收口：全部满足、无推后项"
check "$DOC06" '[x] 规则文档缺口已补' "06 §6「进入实现前的检查清单」第 1 项已勾"
check "$DOC06" '[x] 开发文档 01–06 已评审通过' "06 §6「进入实现前的检查清单」第 2 项已勾"
check "$DOC06" '[x] ADR-001～007 已确认' "06 §6「进入实现前的检查清单」第 3 项已勾"
check "$DOC06" '[x] `tests/run_all.sh` 全绿' "06 §6「进入实现前的检查清单」第 4 项已勾"
check "$DOC06" '[x] 已确定 M1 的“杀闪桃”最小对局验收用例' "06 §6「进入实现前的检查清单」第 5 项已勾"

check "$DOC01" '最终形态 = 身份局 **4～8 人**' "01 §1.2 最终形态口径（M2 人数统一）"
check "$DOC01" 'M1 = 无身份 1v1（2 人，已完成）' "01 §1.2 M1 里程碑口径"
check "$DOC01" 'M2 = 标准版身份局 4～8 人' "01 §1.2 M2 里程碑口径"
check "$DOC01" '身份局 4～8 人（首期 5 人）' "01 §1.2 旧人数口径已改写" absent

# 票 01（M2 文档回写）回写《开发文档/06-里程碑与风险.md》§4 的 ADR 表、表下变更块与新增 §5。
# 依据：.scratch/m2-doc-writeback/spec.md 逐行口径 #1 / #4 / #5 / #6 / #11 / #23 + A1。
check "$DOC06" '判定阶段进行判定之前的 `RESOLVE_BEFORE_EFFECT`' "ADR-001 改判 B（票 01 §9 + 票 02）"
check "$DOC06" 'ADR-001 变更说明' "ADR-001 表下变更块"
check "$DOC06" '**属性不由原牌继承**' "ADR-003 改判：属性不由原牌继承"
check "$DOC06" 'ADR-003 变更说明' "ADR-003 表下变更块"
check "$DOC06" '**颜色按来源同色派生**' "ADR-004 颜色按来源同色派生"
check "$DOC06" '点数恒无' "ADR-004 点数恒无"
check "$DOC06" 'ADR-004 变更说明' "ADR-004 表下变更块"
check "$DOC06" '宝物槽随界限突破一并延后，M2 预留不启用' "ADR-006 宝物槽延后说明"
check "$DOC06" '机械判据 = 源码纪律扫描' "风险表：技能硬编码的机械判据"
check "$DOC06" '在 `06` §4「决策记录（ADR）」与 §5「程序化约定与偏离一手」逐条裁定并落文档' "风险表：存疑点未决行改写"
check "$DOC06" '## 5. 程序化约定与偏离一手' "新增「程序化约定与偏离一手」一节"
check "$DOC06" '**程序化约定**——官方无一手条文' "三类来源：程序化约定定义"
check "$DOC06" '**偏离一手**——有官方明文' "三类来源：偏离一手定义"
check "$DOC06" '**无一手源**——本机没有一手源' "三类来源：无一手源定义"
check "$DOC06" '按模式分支' "胜负检测点 ≠ 终止点标为按模式分支"
check "$DOC06" '先旧后新' "P1：装备顶替的语义顺序 = 先旧后新"
check "$DOC06" '`horse_offense` / `horse_defense`' "P2：槽位拼写取数据层"
# 退役旧串（两头钉的 absent 侧；票 11 段集中钉全部黑名单）
check "$DOC06" '配置留开关' "旧 ADR-001『配置留开关』" absent
check "$DOC06" 'A 方案' "旧风险表『A 方案』" absent
check "$DOC06" '保留原牌属性' "旧 ADR-003『保留原牌属性』" absent
check "$DOC06" '待官方 FAQ 复核' "旧 ADR-003『待官方 FAQ 复核』" absent
check "$DOC06" '属性一并保留' "旧 ADR-003『属性一并保留』" absent

# 票 03（M2 文档回写）回写《开发文档/02-规则引擎设计.md》§2 / §3.5 / §5.1 / §6 / §8。
# 依据：.scratch/m2-doc-writeback/spec.md 逐行口径 #2 / #10 / #22 / #26 / #27 + A4 / A5。
check "$DOC02" 'priority_class{HERO, EQUIPMENT,' "§3.5(3) 优先级枚举（散文保留）"
check "$DOC02" 'SELECT_OPTION' "§5.1 Query 第 8 类 SELECT_OPTION"
check "$DOC02" '`PlayCard`' "§6 补 PlayCard（使用与打出分列）"
check "$DOC02" '`ActivateSkill`' "§6 补 ActivateSkill"
check "$DOC02" '`SelectCards`' "§6 补 SelectCards"
check "$DOC02" '规则层候选过滤' "§2.2 判定区同名归属"
check "$DOC02" '属性不继承' "§2.4 转化牌属性不继承"
check "$DOC02" '颜色按来源牌同色派生' "§2.4 多牌转化颜色按来源派生"
check "$DOC02" 'player_count == 2 ⇒ 无身份局' "§8 模式由座位数派生"
check "$DOC02" '不得预设' "§8 身份局 first_seat 必须保持默认"
check "$DOC02" '存疑点相关的配置开关' "§8 存疑点清单为空"
check "$DOC02" '无懈可击响应窗口 A / B' "旧无懈可击窗口配置行已删" absent

# 票 04（M2 文档回写）回写《开发文档/03-数据模型与技能描述.md》§1–§8。
# 依据：.scratch/m2-doc-writeback/spec.md 逐行口径 #7 / #8 / #9 / #12 / #13 / #14 / #19 / #20 + A5。
DOC03="03-数据模型与技能描述.md"
check "$DOC03" 'SkillDef{skill_id, locked, carrier, hooks, modifiers}' "§4 SkillDef 五元组"
check "$DOC03" '条件内联在回调' "§4 条件内联在回调"
check "$DOC03" '消耗不声明式' "§4 消耗不声明式"
check "$DOC03" '仍由 `CardBehavior`' "§1/§2 声明式未落实、由 CardBehavior 承载"
check "$DOC03" '主公技在 M2 **不可发动**' "§6 主公技不可发动"
check "$DOC03" '限定技 / 觉醒技**归 M3**' "§6 限定 / 觉醒归 M3"
check "$DOC03" '`CallOp`' "§5 CallOp（新增内容）"
check "$DOC03" 'sgs.effects.OPS' "§5 op 注册表（查表派发）"
check "$DOC03" '`ZoneRef(zone, seat)`' "§5 区域坐标 ZoneRef 形状"
check "$DOC03" '`PUT`' "§5 收编 PUT"
check "$DOC03" '`JUDGE`' "§5 收编 JUDGE"
check "$DOC03" '`LOSE`' "§5 收编 LOSE"
check "$DOC03" '身份过滤在 M2 无载体可过滤' "§6 身份过滤无载体"
check "$DOC03" 'build_initial' "§3 主公 +1 体力上限落点 = 初始化段"
check "$DOC03" '延时锦囊必须声明"置入判定区"' "§8 第 3 项 CardType.delayed + 加载期校验"
check "$DOC03" '防御马（+1 坐骑）/ 进攻马（−1 坐骑）/ 宝物' "§2 五槽全名"
check "$DOC03" '§1.1' "旧 §1.1 指针不再出现" absent
check "$DOC03" '§8.3' "旧 §8.3 指针不再出现" absent

# 票 09（M2 文档回写）#17：`03` §7「内容包与版本」显式补一次装备计数（"10 武器"悬案的正面表述）。
check "$DOC03" '武器 **9 名 / 10 实体张**（诸葛连弩 ×2）' "§7 装备计数：9 名 / 10 实体张"
check "$DOC03" '合计 **19 张**' "§7 装备计数：合计 19 张"

# 票 05（M2 文档回写）回写《开发文档/05-测试与验证策略.md》§2 / §4 / §5 / §7。
# 依据：.scratch/m2-doc-writeback/spec.md 逐行口径 #30 / #32 / #33 + A6 / A7。
check "$DOC05" '64 已实现 = 53 dispatch + 11 空日志' "§2 64 是派生量（派生式）"
check "$DOC05" '100 局 / 120 s' "§2 fuzz 预算 M2 口径"
check "$DOC05" '§7 第 3 项的门禁口径与之同源' "§2 悬空指针已改指 §7 第 3 项"
check "$DOC05" '规则层候选过滤' "§4 判定区唯一改述为规则层候选过滤"
check "$DOC05" '判定区同名唯一' "§4 fuzz 不变量保留"
check "$DOC05" '判定阶段不是空阶段' "§5 回合环：判定阶段走判定事件"
check "$DOC05" '必须同时更新测试与决策记录' "§7 第 5 条删『配置』"
check "$DOC05" '47 dispatch 白名单 / 11 空日志 / 25 枚举位' "旧三分区计数已退役" absent
check "$DOC05" '200 局 / 30 s' "旧 fuzz 预算已退役" absent
check "$DOC05" '判定阶段空过' "旧『判定阶段空过』已退役" absent
check "$DOC05" '§7.3' "悬空 §7.3 指针已退役" absent

# 票 06（M2 文档回写）回写《开发文档/01-总体设计.md》§1.2 / §2 原则 7 / §5 / §8 第 3 条。
# 依据：.scratch/m2-doc-writeback/spec.md 逐行口径 #21 / #31 + A2。
check "$DOC01" '只读依赖环 `data → effects → state → data`' "§5 data 依赖改为只读依赖环"
check "$DOC01" '存疑点在 `06` §4「决策记录（ADR）」与 §5' "§2 原则 7 窄改（存疑点落文档）"
check "$DOC01" '**存疑点裁定**：不建配置开关' "§8 第 3 条窄改（不建配置开关）"
check "$DOC01" '`standard` / `standard+ex`（军争）/ `boundary`' "§8 合法开关：规则版本仍在"
check "$DOC01" '**宝物槽位**随界限突破' "§8 合法开关：宝物槽仍在"
check "$DOC01" '**军争属性伤害 / 连环**随军争篇引入' "§8 合法开关：军争属性 / 连环仍在"
check "$DOC01" '（MVP 首期 5 人）' "旧『MVP 首期 5 人』已退役" absent
check "$DOC01" '首期 5 人' "旧『首期 5 人』已退役" absent
check "$DOC01" '存疑点开关' "旧『存疑点开关』已退役" absent

# 票 07（M2 文档回写）回写《开发文档/04-AI与交互设计.md》§1 参与者接口。
# 依据：.scratch/m2-doc-writeback/spec.md 逐行口径 #28。
check "04-AI与交互设计.md" '| 入（引擎 → 参与者） | Query（类型、候选行动、可见信息、时限） |' "§1 契约入方向形状未变"
check "04-AI与交互设计.md" '| 出（参与者 → 引擎） | Action（选择结果 / 放弃 / 超时） |' "§1 契约出方向形状未变"
check "04-AI与交互设计.md" '响应窗口是“收集式”' "§1 无懈窗口收集式语义"
check "04-AI与交互设计.md" '再一次性' "§1 收集后一次性推进"

# 票 08（M2 文档回写）：术语表 GLOSSARY.md 收口（非冻结文档，可就地改）。
# 依据：.scratch/m2-doc-writeback/spec.md 逐行口径 #18 / #29 与「新节与术语」。
GLOSSARY="../GLOSSARY.md"
check "$GLOSSARY" '**顶替**：' "术语表：顶替独立词条"
check "$GLOSSARY" '**防具**：' "术语表：防具独立词条"
check "$GLOSSARY" '失去装备 ≠ 弃置' "术语表：装备区条含『失去装备 ≠ 弃置』"
check "$GLOSSARY" '已有牌时走**顶替**' "术语表：装备区条点明顶替"
check "$GLOSSARY" '不约束已冻结的报文字段标识符' "术语表：_Avoid_ 让步纪律"
check "$GLOSSARY" '移动报文（`CardMoveDelta`）里的 **`owner`**' "术语表：归属条点明英文标识符 owner"
check "$GLOSSARY" '**程序化约定**：' "术语表：程序化约定词条"
check "$GLOSSARY" '**偏离一手**：' "术语表：偏离一手词条"
check "$GLOSSARY" '**无一手源**：' "术语表：无一手源词条"
check "$GLOSSARY" '**标识符冻结**：' "术语表：标识符冻结纪律词条"
check "$GLOSSARY" '存疑点开关' "术语表：旧『存疑点开关』说法已退役" absent

# A9（实施期追加）：「计数纪律」的范围句随本 effort 解冻窄改，两头钉。
check "$GLOSSARY" '本纪律**不扫** `开发文档/`' "术语表：计数纪律范围窄改（不扫开发文档）"
check "$GLOSSARY" '`开发文档/` 是冻结文档，按票 17 §8 R-2/R-3 不回写' "术语表：旧『冻结文档不回写』范围句已退役" absent

# 票 10（M2 文档回写）票 21 两条 ⚠ 升格裁定（P1 / P2）的文档落点。
# 依据：.scratch/m2-doc-writeback/spec.md「升格项」表 P1 / P2。
check "$DOC03" '`horse_offense` = 进攻马、`horse_defense` = 防御马' "P2：03 §2「卡牌数据模型」五槽拼写与数据层一致"
check "$DOC06" '装备**顶替的语义顺序 = 先旧后新**' "P1：06 §5「程序化约定与偏离一手」账本登记顶替顺序"
check "$DOC06" '装备区**撞槽即抛 = 编程错误兜底**' "P1：06 §5「程序化约定与偏离一手」账本登记撞槽即抛的归属"
check "$DOC06" '槽位拼写取数据层 `horse_offense` / `horse_defense`' "P2：06 §5「程序化约定与偏离一手」账本登记槽位拼写"

# ===========================================================================
# 回写纪律（票 11）：两头钉 —— 每条回写同时断言「新串出现」与「退役旧串不出现」。
# 黑名单来自 spec 的「机械断言的形态」节；旧串在**开发文档/ 全六份**里都不得出现。
# 覆盖元断言 + 合成反例照 tests/test_timing.py 的「术语表计数纪律」姿态：
#   ① 每条声明都必须在**真实文档**里匹配得上（文件不存在 = 空转，直接判红）；
#   ② 把任一旧串临时写回副本，同一条 absent 断言**必须变红**。
# ===========================================================================

# 每条 = "文件|字面串|描述"。
WRITEBACK_NEW=(
  '06-里程碑与风险.md|判定阶段进行判定之前的 `RESOLVE_BEFORE_EFFECT`|ADR-001 改判 B'
  '06-里程碑与风险.md|**程序化约定**——官方无一手条文|新节三类来源：程序化约定'
  '06-里程碑与风险.md|**偏离一手**——有官方明文|新节三类来源：偏离一手'
  '06-里程碑与风险.md|**无一手源**——本机没有一手源|新节三类来源：无一手源'
  '02-规则引擎设计.md|priority_class{HERO, EQUIPMENT,|§3.5(3) 优先级枚举'
  '02-规则引擎设计.md|player_count == 2 ⇒ 无身份局|§8 模式由座位数派生'
  '03-数据模型与技能描述.md|SkillDef{skill_id, locked, carrier, hooks, modifiers}|§4 SkillDef 五元组'
  '03-数据模型与技能描述.md|sgs.effects.OPS|§5 op 注册表'
  '05-测试与验证策略.md|53 dispatch 白名单 / 11 空日志 / 19 枚举位|M2 三分区口径'
  '05-测试与验证策略.md|100 局 / 120 s|M2 fuzz 预算'
  '01-总体设计.md|只读依赖环 `data → effects → state → data`|§5 data 只读依赖环'
  '04-AI与交互设计.md|响应窗口是“收集式”|§1 无懈窗口收集式语义'
  '../GLOSSARY.md|**标识符冻结**：|术语表：标识符冻结纪律'
)

WRITEBACK_OLD=(
  '06-里程碑与风险.md|配置留开关|旧 ADR-001 的配置开关说法'
  '06-里程碑与风险.md|A 方案并留开关|旧风险表的 A 方案说法'
  '01-总体设计.md|存疑点开关|旧 §8 的存疑点开关条目'
  '05-测试与验证策略.md|200 局 / 30 s|旧 fuzz 预算'
  '05-测试与验证策略.md|47 dispatch 白名单|旧三分区计数'
  '01-总体设计.md|（MVP 首期 5 人）|旧人数口径'
  '05-测试与验证策略.md|判定阶段空过|旧回合环口径'
  '06-里程碑与风险.md|属性一并保留|旧 ADR-003 社区读法'
  '06-里程碑与风险.md|保留原牌属性|旧 ADR-003 结论'
  '06-里程碑与风险.md|10 武器|从未存在的武器计数表述'
)

writeback_check_claim() { # $1="文件|字面串|描述" $2=present|absent
  local file literal desc
  IFS='|' read -r file literal desc <<< "$1"
  check "$file" "$literal" "$desc" "$2"
}

for claim in "${WRITEBACK_NEW[@]}"; do writeback_check_claim "$claim" present; done
for claim in "${WRITEBACK_OLD[@]}"; do writeback_check_claim "$claim" absent; done

# 覆盖元断言：每条声明的目标文件必须真实存在（否则 grep 落空 ⇒ absent 空转也算通过）。
for claim in "${WRITEBACK_NEW[@]}" "${WRITEBACK_OLD[@]}"; do
  IFS='|' read -r file literal desc <<< "$claim"
  if [ ! -f "$DOC_DIR/$file" ]; then
    echo "FAIL: 回写纪律声明指向不存在的文档（$desc）：$file"
    fail=1
  fi
done
# 黑名单只许指向 `开发文档/` 内的文件：合成反例会把字面串写进副本，
# 指向 `..` 的目标（如 GLOSSARY）会**改到真实文件**——这是必须挡住的路径逃逸。
for claim in "${WRITEBACK_OLD[@]}"; do
  IFS='|' read -r file literal desc <<< "$claim"
  case "$file" in
    *..*) echo "FAIL: 回写纪律黑名单不得指向文档目录之外：$file"; fail=1;;
  esac
done
if [ "${#WRITEBACK_OLD[@]}" -lt 10 ]; then
  echo "FAIL: 回写纪律黑名单少于 10 条（覆盖元断言）"
  fail=1
fi

# 合成反例：把任一旧串临时写回**副本**，相同的 absent 断言必须变红。
writeback_absent_goes_red() { # $1=文件 $2=字面串；0 = 确实变红
  local probe out
  case "$1" in *..*) return 1;; esac   # 双保险：绝不写 `开发文档/` 之外
  probe="$(probe_mktemp)" || return 1
  if ! cp "$DOC_DIR/$1" "$probe/$1" 2>/dev/null; then
    rm -rf "$probe"
    return 1
  fi
  printf '\n%s\n' "$2" >> "$probe/$1"
  out="$(DOC_DIR="$probe" check "$1" "$2" "合成反例" absent 2>&1 || true)"
  rm -rf "$probe"
  case "$out" in *FAIL*) return 0;; *) return 1;; esac
}

REPO_TREE_BEFORE="$(repo_tree_snapshot)"
for claim in "${WRITEBACK_OLD[@]}"; do
  IFS='|' read -r file literal desc <<< "$claim"
  if ! writeback_absent_goes_red "$file" "$literal"; then
    echo "FAIL: 合成反例未变红：把『$literal』写回 $file 后 absent 断言仍通过"
    fail=1
  fi
done

# ===========================================================================
# 票 01（retro-m2doc）：两类文档漂移的确定性检查。
#   ① 交叉引用标题锚定：引用写成 `NN` §N「标题」——解析该文档必须存在 `## N.` 标题、
#      且标题文本包含所引「标题」串。裸 `NN` §N 会随「插入一节」静默错位，一律判红；
#      `§N.M` 子节与 `§M<数字>` 里程碑两种**合理形态**显式列白名单，未登记即红（不静默放过）。
#   ② 退役串全树 absent：在既有 per-file 断言之外，对 开发文档/*.md + GLOSSARY.md
#      + 参考资料/三国杀详细规则.txt 再扫一遍（「另一份文档」漏网也挡得住）。
# 两条都带覆盖元断言 + 合成反例，姿态同上面的回写纪律段。
# 语法边界：本扫描只认 `NN` §N（NN = 01–06 的文档号）。不带文档号的裸 `§N` 是票内小节
# 自引用或本地指针（如「票 14 §4.2」「依据研究 §5.3」），不属该语法，故不在此检查。
# 注意：本段自身会被扫描，故注释里不写 `NN` §N 的字面示例（用 `NN` / 「标题」这类占位）。
# ===========================================================================

# 文档号 → 文件名（标题锚定的解析目标）。用 case 而非 declare -A：脚本保持 bash 3.2 可跑。
doc_file_for_num() {
  case "$1" in
    01) echo "01-总体设计.md" ;;
    02) echo "02-规则引擎设计.md" ;;
    03) echo "03-数据模型与技能描述.md" ;;
    04) echo "04-AI与交互设计.md" ;;
    05) echo "05-测试与验证策略.md" ;;
    06) echo "06-里程碑与风险.md" ;;
    *) return 1 ;;
  esac
}

# 白名单 = 「合理但不能标题锚定」的形态，以 `|键|` 定界串存整键（bash 3.2 无关联数组）。
# 键 = 文档号:子节号 / 文档号:里程碑。每条都必须真的被命中（死条目即判红）。
SUBSECTION_WHITELIST="|01:1.2|02:2.2|02:3.5|"
MILESTONE_WHITELIST="|06:M1|06:M2|"

# 绊线（tripwire）：锚定引用数塌到阈值以下即判红——它防的是「扫描失效 ⇒ 空转通过」，
# 不是覆盖目标；引用真的退役时随盘点下调。
ANCHOR_TRIPWIRE=20

# 引用扫描范围：承载「文档 §节」引用的仓库文本（不含 gitignored 的 .scratch/，任何 clone 都可跑）。
REF_FILES=()
while IFS= read -r f; do REF_FILES+=("$f"); done < <(
  find "$PWD/开发文档" "$PWD/docs" "$PWD/tests" "$PWD/src" -type f \
    \( -name '*.md' -o -name '*.py' -o -name '*.sh' \) -print 2>/dev/null | sort
  printf '%s\n' "$PWD/GLOSSARY.md" "$PWD/AGENTS.md" "$PWD/CODING_STANDARDS.md"
)

# 探针统一判定：输出里出现 FAIL 即「确实变红」。
output_has_fail() { case "$1" in *FAIL*) return 0;; *) return 1;; esac; }

scan_doc_refs() { # 标题锚定扫描；返回 1 = 有错
  local errors=0 anchored_total=0
  local f entry lineno line rest before after doc sec anchor docfile heading key trimmed
  local seen_sub="|" seen_mile="|"

  for f in "${REF_FILES[@]}"; do
    [ -f "$f" ] || continue
    while IFS= read -r entry; do
      lineno="${entry%%:*}"
      line="${entry#*:}"
      rest="$line"
      while [[ "$rest" =~ (0[1-6])\`?[[:space:]]*§[[:space:]]*([0-9]+|[A-Za-z][0-9]*) ]]; do
        doc="${BASH_REMATCH[1]}"
        sec="${BASH_REMATCH[2]}"
        local match="${BASH_REMATCH[0]}"
        # bash 不给匹配偏移：用字面串把 rest 切成 before / after（首次出现处）。
        before="${rest%%"$match"*}"
        after="${rest#"$before"}"
        after="${after#"$match"}"
        rest="$after"
        # 票号引用（票 NN 的 §M）不是文档交叉引用。
        # 这里用 glob + 参数展开而非 `=~`：C/POSIX locale 下多字节 `=~` 会失配（假红）。
        trimmed="$before"
        while [[ "$trimmed" == *[[:space:]] ]]; do trimmed="${trimmed%?}"; done
        if [[ "$trimmed" == *"票" ]]; then continue; fi
        if [[ "$sec" =~ ^[A-Za-z] ]]; then
          key="$doc:$sec"; seen_mile="$seen_mile$key|"
          if [[ "$MILESTONE_WHITELIST" != *"|$key|"* ]]; then
            echo "FAIL: 未登记的里程碑形态引用 \`$doc\`（§${sec}）（${f#"$PWD"/}:$lineno）"
            errors=$((errors+1))
          fi
          continue
        fi
        if [[ "$after" == .* ]]; then
          key="$doc:$sec"
          if [[ "$after" =~ ^\.([0-9]+) ]]; then key="$doc:$sec.${BASH_REMATCH[1]}"; fi
          seen_sub="$seen_sub$key|"
          if [[ "$SUBSECTION_WHITELIST" != *"|$key|"* ]]; then
            echo "FAIL: 未登记的子节形态引用 \`$doc\`（§${key#*:}）（${f#"$PWD"/}:$lineno）"
            errors=$((errors+1))
          fi
          continue
        fi
        # 标题锚定：`NN` §N「标题」。同样用 glob + 参数展开，避免 C locale 下多字节 `=~` 失配。
        if [[ "$after" == "「"* ]]; then
          if [[ "$after" != *"」"* ]]; then
            echo "FAIL: 锚点未闭合（缺 」）：\`$doc\`（§$sec）（${f#"$PWD"/}:$lineno）"
            errors=$((errors+1))
            continue
          fi
          anchor="${after#「}"
          anchor="${anchor%%」*}"
          anchored_total=$((anchored_total+1))
          if ! docfile="$(doc_file_for_num "$doc")"; then
            echo "FAIL: 锚定引用指向不存在的文档号 \`$doc\`（${f#"$PWD"/}:$lineno）"
            errors=$((errors+1))
            continue
          fi
          if [ ! -f "$DOC_DIR/$docfile" ]; then
            echo "FAIL: 锚定引用的目标文档不存在：$DOC_DIR/$docfile"
            errors=$((errors+1))
            continue
          fi
          heading="$(grep -m1 -E "^## ${sec}\." "$DOC_DIR/$docfile" || true)"
          if [ -z "$heading" ]; then
            echo "FAIL: \`$doc\` 没有 \`## $sec.\` 标题，引用却声称「$anchor」（${f#"$PWD"/}:$lineno）"
            errors=$((errors+1))
          elif [[ "$heading" != *"$anchor"* ]]; then
            echo "FAIL: \`$doc\` 的 \`## $sec.\` 标题不含「$anchor」（实际：$heading）"
            errors=$((errors+1))
          fi
          continue
        fi
        echo "FAIL: 交叉引用未标题锚定：\`$doc\`（§$sec）（${f#"$PWD"/}:$lineno）——请写成 \`$doc\`（§$sec「标题」）"
        errors=$((errors+1))
      done
    done < <(grep -nF '§' "$f" || true)
  done

  # 覆盖元断言：白名单条条命中（防死条目 / 扫描失效），锚定引用不得塌到绊线以下（防正则失效空转）。
  for key in ${SUBSECTION_WHITELIST//|/ }; do
    if [[ "$seen_sub" != *"|$key|"* ]]; then
      echo "FAIL: 子节白名单条目未被任何引用命中（白名单已死或扫描失效）：$key"
      errors=$((errors+1))
    fi
  done
  for key in ${MILESTONE_WHITELIST//|/ }; do
    if [[ "$seen_mile" != *"|$key|"* ]]; then
      echo "FAIL: 里程碑白名单条目未被任何引用命中（白名单已死或扫描失效）：$key"
      errors=$((errors+1))
    fi
  done
  if [ "$anchored_total" -lt "$ANCHOR_TRIPWIRE" ]; then
    echo "FAIL: 标题锚定引用只解析到 $anchored_total 处（< $ANCHOR_TRIPWIRE，扫描疑似失效）"
    errors=$((errors+1))
  fi
  if [ "$errors" -eq 0 ]; then
    echo "PASS: 交叉引用全部标题锚定（$anchored_total 处锚定 + 子节/里程碑白名单条条命中）"
  fi
  [ "$errors" -eq 0 ]
}

BLACKLIST_TREE_EXTRA=("$PWD/GLOSSARY.md" "$PWD/参考资料/三国杀详细规则.txt")

scan_blacklist_tree_absent() { # 退役串全树 absent；返回 1 = 有错
  local errors=0 claim file lit desc
  for claim in "${WRITEBACK_OLD[@]}"; do
    IFS='|' read -r _file lit desc <<< "$claim"
    for file in "$DOC_DIR"/*.md "${BLACKLIST_TREE_EXTRA[@]}"; do
      [ -f "$file" ] || continue
      if grep -qF -- "$lit" "$file"; then
        echo "FAIL: 退役串『$lit』（$desc）仍出现在 ${file#"$PWD"/}"
        errors=$((errors+1))
      fi
    done
  done
  if [ "$errors" -eq 0 ]; then
    echo "PASS: 退役串在 开发文档/ + GLOSSARY.md + 规则文档 全树 absent"
  fi
  [ "$errors" -eq 0 ]
}

# 合成反例①a：把 06 的 `## 6.` 挪成 `## 7.`（= 前面插了一节把清单挤走），
# 引用它的锚定断言必须变红——这正是 §5/§6 那次静默错位的机械复现。
anchor_rename_goes_red() {
  local probe out
  probe="$(probe_mktemp)" || return 1
  if ! cp "$DOC_DIR"/*.md "$probe/" 2>/dev/null; then rm -rf "$probe"; return 1; fi
  if ! grep -q '^## 6\.' "$probe/06-里程碑与风险.md"; then rm -rf "$probe"; return 1; fi
  sed -i 's/^## 6\./## 7./' "$probe/06-里程碑与风险.md"
  out="$(DOC_DIR="$probe" scan_doc_refs 2>&1 || true)"
  rm -rf "$probe"
  output_has_fail "$out"
}

# 合成反例①b：节号不动、只把 `## 6.` 的**标题文字**改掉。只查「节号存在」的纯存在性
# 检查抓不到这一半（正是那节让位新节后错位的情形），锚定断言必须同样变红。
anchor_retitle_goes_red() {
  local probe out
  probe="$(probe_mktemp)" || return 1
  if ! cp "$DOC_DIR"/*.md "$probe/" 2>/dev/null; then rm -rf "$probe"; return 1; fi
  if ! grep -q '^## 6\. 进入实现前的检查清单' "$probe/06-里程碑与风险.md"; then
    rm -rf "$probe"; return 1
  fi
  sed -i 's/^## 6\. 进入实现前的检查清单.*/## 6. 另一份清单/' "$probe/06-里程碑与风险.md"
  out="$(DOC_DIR="$probe" scan_doc_refs 2>&1 || true)"
  rm -rf "$probe"
  output_has_fail "$out"
}

# 合成反例②：把任一退役串写进**另一份**开发文档的副本，全树 absent 必须变红。
blacklist_tree_goes_red() {
  local probe out lit
  IFS='|' read -r _f lit _d <<< "${WRITEBACK_OLD[0]}"
  probe="$(probe_mktemp)" || return 1
  if ! cp "$DOC_DIR"/*.md "$probe/" 2>/dev/null; then rm -rf "$probe"; return 1; fi
  printf '\n%s\n' "$lit" >> "$probe/01-总体设计.md"
  out="$(DOC_DIR="$probe" scan_blacklist_tree_absent 2>&1 || true)"
  rm -rf "$probe"
  output_has_fail "$out"
}

if ! scan_doc_refs; then fail=1; fi
if ! scan_blacklist_tree_absent; then fail=1; fi
if ! anchor_rename_goes_red; then
  echo "FAIL: 合成反例未变红：把 06 的 \`## 6.\` 挪号后，引用它的锚定断言仍通过"
  fail=1
fi
if ! anchor_retitle_goes_red; then
  echo "FAIL: 合成反例未变红：只改 06 的 \`## 6.\` 标题文字后，引用它的锚定断言仍通过"
  fail=1
fi
if ! blacklist_tree_goes_red; then
  echo "FAIL: 合成反例未变红：把退役串写进另一份开发文档后，全树 absent 仍通过"
  fail=1
fi

# 防再漏（票 05）：探针不得改动仓库工作树，也不得在仓库根留 .wb_probe.* 残留。
# 断言本身也要有反面自检（覆盖元断言），防 find / 比较失效时空转通过。
if ! probe_discipline_check "$REPO_TREE_BEFORE"; then fail=1; fi
if ! probe_discipline_selftest; then fail=1; fi

if [ "$fail" -eq 0 ]; then
  echo "PASS: 开发文档校验全部通过"
fi
exit "$fail"
