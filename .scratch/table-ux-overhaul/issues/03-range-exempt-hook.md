# 03: 合法性分层——把"距离豁免"从 TARGET_POOL 拆出来

**What to build:** 新增"距离/攻击范围豁免"挂点，把 6 个"无距离限制"技能迁过去，
`TARGET_POOL` 只留**目标合法性增删**。目的：任何"放宽距离"的修饰器都不再能复活被判定非法的座位。

**Blocked by:** None
Status: open
Type: task
Touches: src/sgs/effects/modifiers.py, src/sgs/effects/actions.py, src/sgs/skills/active.py, src/sgs/skills/life_after.py, src/sgs/skills/response_window.py, src/sgs/skills/phase_body.py, tests/test_hero_lei.py, tests/test_hero_jie.py, tests/test_m5_equipment.py
Ruling: spec §2 Q2（用户 2026-11-11 签 A）

## 根因（spec §1 #2）

`check_targets`（`effects/actions.py:401-462`）的层次是：
① `TargetRule`（self_only / 目标任务数）→ ② 距离与攻击范围 → ③ 延时锦囊/封印规则 →
④ **附加谓词 `TARGET_FILTERS`**（`actions.py:445-451`，借刀 = "有武器 ∧ 攻击范围内有合法【杀】目标"）→
⑤ **`TARGET_POOL` 修饰器链**（`:452-462`）。

雄乱 `_xiongluan_distance_pool`（`skills/active.py:5233-5249`）只挡 `TargetRule.self_only`，
其余**无条件** `(*current, target)` ⇒ 它把 ④ 判为非法、以及别的技能在 ⑤ 里**删掉**的座位全部加回来：

- 【借刀杀人】⇒ 可指定无武器角色（实测池 `()`→`(1,)`；清单文案仍写"装备区有武器"；提交被接受；
  持刀者无武器 ⇒ 交给武器那步读 `None` ⇒ **整张牌空转**）；
- 【过河拆桥】/【顺手牵羊】⇒ 可指定无牌角色；
- 【乐不思蜀】⇒ 可指定判定区已封印的角色；
- 【空城】⇒ 复活被删座位（`TARGET_POOL` 链按 `skill_id` 排序，`hero.zhangxiu.xiongluan` 晚于
  `hero.jie_zhugeliang.kongcheng`；坑已登记于 `skills/phase_body.py:1209-1212`）。

根因是**一个挂点两用**：`TARGET_POOL` 既被当"补目标以豁免距离"，又被当"增删目标合法性"。

## 要做什么

1. **新挂点**：`ModifierPoint.TARGET_RANGE_EXEMPT`（命名由实现定），语义 = "本次使用可以无视距离/攻击范围"。
   求值点在 `check_targets` 的 ②，**在 ④⑤ 之前**：它只能让"仅仅被距离挡掉"的座位重新进入 ② 之后的
   ③④⑤ 全套判据，**不能直接产出池成员**。
2. **迁移 6 个消费者**（都是"无距离限制"语义，逐个确认没有顺带放宽别的）：
   - 【天义】`active.py:5006`、【雄乱】`active.py:5287`、【决堰】`active.py:6157`、
   - 【诈降】`life_after.py:208`、【烈弓】`response_window.py:775`、【神速】`phase_body.py:1301`。
3. **`TARGET_POOL` 只留合法性增删**：帷幕（`hero_lin.py:610`）、空城、方天画戟的"额外目标合法性同标准"
   （`catalog.py:3446`）、C 族剔自己（`target_pool.py:97`）、以及【雌雄】一类"删目标"。
4. **把"补目标"这个能力单列**（如果确实有合法用法，例如将来"可以对别人使用【无中生有】"）：
   给它一个**独立的挂点**，且它必须**在 ④ 之后仍被 ④ 约束**（实现上 = 补进来的座位要重跑 ③④）。
5. **写一条结构性纪律**：`TARGET_POOL` 的回调**不许**新增池成员，除非它能证明该成员通过了 ③④
   （用一条合成断言钉住）。

## 验收

- 新增（`tests/test_hero_lei.py`，雄乱组现只有 `self_only` 一例）：
  - `test_xiongluan_never_resurrects_a_borrowed_sword_target_without_a_weapon`
  - `test_xiongluan_never_resurrects_a_dismantle_target_with_no_cards`
  - `test_xiongluan_never_resurrects_a_sealed_judge_area_target`
  - `test_xiongluan_never_resurrects_a_kongcheng_seat`
  - 四条都断言**两处**：`check_targets` 与 `parse_phase` 的 `card_preactivations`（借刀那条）。
- 新增：`test_the_range_exempt_hook_is_evaluated_before_the_target_filters`（合成修饰器，证明层次）。
- 回归：6 个迁移技能的既有用例必须**结果不变**（`tests/test_hero_jie.py`、`tests/test_hero_feng.py`、
  `tests/test_hero_huo.py`、`tests/test_m5_equipment.py` 等）。

## 会红的既有断言

- 6 个技能里凡是用"池比原来大"来断言的用例；逐条核，**只允许池变小的方向**（本票只会让池更严）。
- 金录像：`TARGET_POOL` 的回调只在有该技能时改变行为 ⇒ 预计 0 条基准受影响（跑 `check_goldens.sh` 确认）。
