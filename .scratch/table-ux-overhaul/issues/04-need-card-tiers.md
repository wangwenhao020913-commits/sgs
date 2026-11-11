# 04: 需要牌窗口按档逐档问 + 提供者锁按事件复位

**What to build:** "需要/可以使用或打出牌"的窗口按**档位顺序逐档问**（武将档 → 装备档 → 牌档），
不再把不同档的候选混在一张面里；顺带修"提供者一辈子只问一次"的锁（【无双】里的【八卦阵】）。

**Blocked by:** 01（`view()` 的游标化；逐档问会让询问条数变多，先让每次请求便宜）
Status: open
Type: task
Touches: src/sgs/engine/steps.py, src/sgs/effects/actions.py, src/sgs/skills/catalog.py, src/sgs/effects/modifiers.py, src/sgs/agents/heuristic.py, src/sgs/state/protocol.py, tests/test_timing.py, tests/test_hero_response_window.py
规则原文: 三国杀详细规则.txt §4.16 L443–L466、§4.20 L541–L551
Ruling: spec §2 Q1 / Q13 / Q16 / Q17（用户 2026-11-11 签）

## 根因（spec §1 #9）

1. **不同档被混在一张面里**：`_dodge_window` 等四扇窗口把"提供者"与"真牌"一次发给玩家，
   玩家先点低档（如【朱雀羽扇】装备档 1）之后，高档（【渐营】武将档 0）被 `tier_not_downgraded`
   挡掉 ⇒ 表现为"选了一个另一个就没了"。这不是派发只问一轮、不是应答缓存、也不是条件被破坏。
2. **提供者锁按目标清、不按需要牌事件清**：`providers_used` 只在 `_PER_TARGET_FRAME_KEYS`
   （`steps.py:938-970`）里随目标推进清掉；`_accept` 只调 `reset_need_card_floor`（`actions.py:1414-1425`），
   没有清 `providers_used` ⇒ 【无双】要两张【闪】：八卦阵判定失败一次后，即使用真【闪】补上，
   第二张闪仍然不能再发动八卦阵。

## 要做什么

1. **档位表改成用户裁定（Q17）**：

   | 档 | 判据 | 谁 |
   |---|---|---|
   | 0 | 技能 ∧ **不转化** | 【护驾】标/界、【激将】标/界、【傲才】（尚未实现） |
   | 1 | 装备技 ∧ **不转化** | 【八卦阵】 |
   | 2 | **牌**（真牌）∧ **一切转化**（含转化型武将技/装备技） | 真【闪】/【桃】/【无懈】/【杀】、【蛊惑】、界【渐营】②、【龙胆】【武圣】【看破】【急救】【丈八蛇矛】… |

   **转化牌与真牌同档（第 2 档）**：官方 2018 规则集只有"武将技 > 装备技 > 使用牌"的原则，
   无一处裁定转化牌归哪档；移动版实际行为 = 同档 ⇒ 登记为**程序化约定**（账本行由票 06 落）。

2. **实现方式 = 按档迭代，不是把档混面**：`need_card_tier`（`actions.py:1352`）保留，但窗口的
   候选面按 `(档, 引擎既有顺序)` 分面，**先给最小档的面**；玩家"不发动/不出" ⇒ 进下一档；
   最后一档"不出" ⇒ 这次需要牌不满足。**同档内仍是"选一条 / 都不发动"的合并面**。
3. **【蛊惑】【渐营】② 落档 2**：它们是 `ActivateSkill` 提供者、载体是武将牌（现算 0 档），
   但产物是转化牌 ⇒ 给技能定义一个**可选覆盖位**（建议 `SkillDef.need_card_tier: int | None`，
   缺省仍按载体派生），只在这两条上写 2。**不写技能名名单**。
4. **交互形状**（Q13）：档 0 多个提供者时一张"选一条 / 都不发动"，单个时一击"是否发动"；
   档 1【八卦阵】**一击确认**（不要"先点技能、再点确认"两步）；档 2 一张面列此刻能用的牌
   （含转化清单入口），可"不出"。
5. **`providers_used` 复位点**：挪到与 `reset_need_card_floor` **同一个点**（一次需要牌事件成立处，
   `_dodge_window._accept` / 决斗的每收下一次响应），语义 = "一次杀的结算过程内，一旦以任何方式
   使用/打出了【闪】，封禁解除"。
6. **AI 适配**：`agents/heuristic.py` 的响应评分现在比较同一张面里的候选；分面后要保证它按
   "当前这一档"比较，且不因分面漏填（跑 `tests/test_heuristic_ai.py`、`tests/test_all_ai_games.py`）。

## 验收

- 新增：`test_jianying_is_asked_before_the_vermilion_feather_fan_at_the_same_timing`
  （真引擎：先问渐营、再问羽扇；两种顺序都不丢第二条）。
- 新增：`test_a_dodge_window_asks_hero_tier_then_equipment_tier_then_card_tier`
  （曹操场景：护驾 → 八卦阵 → 真闪，三次询问）。
- 新增：`test_wushuang_a_failed_bagua_judgment_still_allows_bagua_again_after_a_real_dodge`
  （四张闪的局面：判定失败 → 真闪 → 第二张闪仍可发动八卦阵）。
- 新增：`test_a_conversion_provider_is_tier_two`（蛊惑/渐营② 与真牌同档，不先问）。
- 改写：`tests/test_timing.py:1326`（"低档关掉高档"改为"按档迭代，高档先问"）与 `:1849`。
- 记录流会变（询问条数增加）⇒ **基准重录并入票 06**，本票先列清单。
