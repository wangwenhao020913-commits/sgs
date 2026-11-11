# 09: 自家手牌直选三例（雌雄 / 火攻 / 业炎花色）

**What to build:** 三处"本来是应答者自己的区，却被挤到弹窗/通用槽位面"的选择，改回**牌桌直选**：
① 被【雌雄双股剑】弃手牌；② 【火攻】展示自己手牌；③ 【业炎】四花色选择的**同花色置灰**。

**Blocked by:** None（③ 的完整动态置灰可由票 12 接手，但本票先做静态部分）
Status: open
Type: task
Touches: src/sgs/skills/catalog.py, tools/sgs_web/projection.py, tools/sgs_web/static/modal.js, tools/sgs_web/static/candidates.js, tools/sgs_web/static/card-face.js, tools/sgs_web/static/reveal.js, tests/test_skills.py, tests/test_web.py, tests/support/ui_smoke.mjs
Ruling: spec §2 Q6 / Q20

## 根因（spec §1 #7 / #12 / #6-业炎）

三者是同一族：`SELECT_CARDS` 的面本来属于**应答者自己的区**，却被一条前置判据挤出了 `own-zone` 类。

1. **雌雄**：`skills/catalog.py:2961` `build_face=lambda: host.selection_face(target, (Zone.HAND,))`
   **少传 `reveal_hand=True`** ⇒ 槽位 `card=None` + 顺序打乱（`engine/game.py:2525-2535,2554-2566`）
   ⇒ `candidates.js:138-142` 的 `Boolean(slot.card)` 为假 ⇒ 落到 `SELECT_CARDS → "slots"`
   （`pending.js:357-374`）的**通用槽位面**（`options.js:697-773`：文案"隐藏区域的槽位只有位置"+「?」牌面）
   —— 玩家看到的就是那个"未知区域窗口"。`game.py:2530-2532` 的注释**早写明**它是 `reveal_hand=True` 的消费者。
2. **火攻**：投影把**己方手牌**槽位塞进 `payload["revealed"]`（`tools/sgs_web/projection.py:2513-2523`）
   ⇒ `dialog="reveal"`（`:2553-2557`）⇒ `modal.js` 的 `isRevealStep` ⇒ `candidates.js:131` 的
   `isOwnZonePick` 被 `stepDialogKind !== null` 短路 ⇒ 亮牌弹窗。
   同一技能第二步（同花色弃牌）已是牌桌直选（先例 `ui_smoke.mjs:14310`）。
3. **业炎**：花色约束是 `SelectionSpec(zones=(HAND,), total=(4,4), max_per_suit=1)`（`skills/active.py:4799-4803`），
   前端花色审查只住在 `reveal.js:283-309`，**第一道门是"槽位全在处理区"**（`:293`）⇒ 手牌面整面没有花色置灰；
   非法四同花只在提交时被 `answer.js:481-490` 挡住。

## 要做什么

1. **雌雄**：`catalog.py:2961` 补 `reveal_hand=True`。前端**零改动**（已用探针验证：补后载荷变
   `dialog:null` + `slots[].card` 可见 ⇒ `isOwnZonePick` / `isOwnHandSinglePick` 命中 ⇒ 牌桌直选，
   `options.js:590` 一击提交）。
2. **火攻**：投影加守卫——**槽位全是 `ref.seat == query.actor` 的己方手牌时不做 `revealed` 授予**
   （它们不是"授予"，`view.self_hand` 已有）⇒ `dialog` 自动变 `null`，其余全自动走 own-hand-pick。
   前端可在 `isRevealStep`（`modal.js:373-389`）加一条对称否决（"己方可见槽位 ⇒ 不是亮牌窗"）作兜底。
3. **业炎**：把 `max_per_suit` 的置灰搬进 own-zone 直选判据——`isOwnZonePick` / `toggleSlotPick`
   读 `spec.max_per_suit`，选中 1 张后把同花色的其余牌置灰。
   **止损**：这是"前端实现一遍规则"的第二份真源，只允许覆盖**局部约束**（每花色至多 k 张）；
   完整的"每一步都由后端算"由票 11/12 接手（届时这条判据可退役）。
4. **补一条回环测试**：引擎真实 pending 的槽位可见性要与前端判据**同源**
   （现有 `tests/test_web.py:1665-1705` 用合成 Query，掩盖了这个断点）。

## 验收

- 新增（`tests/test_skills.py` 雌雄组）：`test_double_sword_discard_face_is_visible_to_the_target`
  （`spec.slots[*].card is not None`，且 `reveal_hand` 生效）。
- 改写（`tests/test_web.py:9361`）：火攻载荷从 `dialog=="reveal"` 改为 `dialog is None`；
  `ui_smoke.mjs:14230/14240/14248/14257`（M5-08a-b1~b4）改写为"牌桌直选"。
- 新增（烟测）：`U-YS-01` 业炎点第 1 张红桃后其余红桃置灰；四花色齐了才可提交。
- 新增（烟测）：`U-CX-01` 雌雄弃牌步直接在手牌区点选（本步现在**没有任何前端用例**）。

## 会红的既有断言

- `ui_smoke.mjs:14310`（火攻第二步的"不是弹窗"先例）保持绿；`14230-14257` 四条改写。
- `tests/test_web.py:9430/9500/9535`（火攻弃牌步 / 放弃 / 无同花色）不受影响。
- 金录像：雌雄修复**不改字节**（`tests/replays/43-double-sword-opposite-gender.jsonl` 该步答的是 `draw_one`、
  文件内 `SELECT_CARDS` 计数为 0）；火攻不在标准 108 且投影不进记录流 ⇒ 预计 0 条重录。
