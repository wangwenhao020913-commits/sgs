# 09: 自家手牌直选三例（雌雄 / 火攻 / 业炎花色）

**What to build:** 三处"本来是应答者自己的区，却被挤到弹窗/通用槽位面"的选择，改回**牌桌直选**：
① 被【雌雄双股剑】弃手牌；② 【火攻】展示自己手牌；③ 【业炎】四花色选择的**同花色置灰**。

**Blocked by:** None（③ 的完整动态置灰可由票 12 接手，但本票先做静态部分）
Status: resolved
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

## Answer

### 落了什么

**① 雌雄双股剑（spec §1 #7）— 引擎一行，前端零改动**

- `src/sgs/skills/catalog.py:_double_sword`：`host.selection_face(target, (Zone.HAND,))`
  补 `reveal_hand=True`。目标自己的手牌从此铺**可见槽**（顺序 = 手牌顺序、牌面连同展示字段），
  不再落进"隐藏槽只有位置 + 位置打乱"那条面。
- 前端**一个字没改**（`candidates.js:isOwnZonePick` / `card-face.js:cardFace` 本来就按
  "槽位可见"分派）：补后载荷是 `dialog: null` + `slots[].card` 可见 ⇒ `isOwnHandSinglePick`
  ⇒ 牌桌直选，点一张即提交。

**② 火攻（spec §1 #12）— 投影守卫 + 前端对称否决**

- `tools/sgs_web/projection.py`：新增 `_own_hand_face(query)`（每一槽都是 `ref.seat ==
  query.actor` 的手牌）；【火攻】亮牌步在它成立时**不再授予** `payload["revealed"]`——己方
  手牌不是"观看授予"（`view.self_hand` 已有同一批牌面）⇒ 下面的 `dialog` 派生自然给 `null`
  ⇒ 前端走既有的 own-hand-pick（单张点一张即提交）。守卫的另一半是 fail-open：面不是应答者
  自己手牌时照旧授予（`revealed` 那一档的语义不变）。
- `tools/sgs_web/static/candidates.js`：形状抽成**唯一**实现 `ownZoneSlotsVisible`（`isOwnZonePick`
  与亮牌否决共用；故意**不读** `stepDialogKind`，否则绕成死循环）+ `ownHandSlotsKnown`
  （面里的牌已在本人手牌快照里 ⇒ 不是观看授予）。
- `tools/sgs_web/static/modal.js:isRevealStep`：加对称否决 `ownHandSlotsKnown`（兜住旧服务端
  载荷 / 合成载荷），排在 `pending.dialog === "reveal"` **之前**。
- **刻意读数（为什么否决不写成票面那句"己方可见槽位 ⇒ 不是亮牌窗"）**：【遗计】/【从讨】
  的**观看授予**也把牌摆在 `ref: {seat: actor, zone: "hand"}` 的可见槽上（那几张牌是"授予看
  的牌堆顶两张"，还没进手牌）。照票面字面写会**把遗计/从讨的亮牌窗一起否掉**。真正的分界是
  "这几张牌在不在本人手牌快照里"——服务端守卫（`_own_hand_face`，有引擎可查手牌）与前端
  否决（`ownHandSlotsKnown`，读 `self_hand`）用的是同一条事实。合成载荷的反例见 U2-12
  （遗计仍开窗）与 M5-08a-b6（旧火攻载荷被否决）。

**③ 业炎花色（spec §1 #6-业炎 / §2 Q6）— own-zone 直选面置灰**

- `candidates.js:ownZoneSuitTaken`：读服务端声明 `spec.max_per_suit`，同花色计数**复用**
  `reveal.js:suitSlotState`（涉猎 / 两行观看手牌同一份，不另写第二套）。
- 两个消费者：`card-face.js:cardFace`（手牌置灰）+ `options.js:toggleSlotPick`（**拒点**，
  置灰不只在样式上）；`options.js:renderOwnZonePick` 的步骤提示把这条**声明**写出来
  （`每种花色至多 k 张，同花色其余牌已置灰`）。
- **止损（票面 §3 ③ 明写，这里再确认）：这是"前端把规则实现一遍"的第二份真源，只允许覆盖
  "每花色至多 k 张"这一条局部约束，不重算张数 / 区域 / 其它合法性。退役条件 = 票 11（步骤
  预览端点）/ 票 12 落地后，`ownZoneSuitTaken` 改读后端现算的预览结果并**删除**。**代码注释
  里写的是同一句**（`candidates.js` / `card-face.js` / `options.js` 三处），`test_web_static.py`
  另有一条静态断言钉住"退役条件必须写在判据旁边"。

**④ 回环测试（票面 §3 ④）**

- `tests/test_web.py::test_the_own_hand_direct_pick_payloads_start_from_a_real_engine_pending`
  走**完整回环**：真会话 → 真引擎询问 → `pending_payload` → 逐条核前端判据读的键。
  两个真实 pending：雌雄弃手牌步（人类座位 1 = 目标，先答 `SelectOption(discard_one)`）与
  火攻亮牌步（人类座位 0 对自己用火攻）。共用 `_assert_own_hand_direct_pick_payload`：
  `kind == SELECT_CARDS` ∧ `actor` ∧ `dialog is None` ∧ `revealed` 空 ∧ `provide_type is None`
  ∧ `arrange is None` ∧ 每槽 `ref.seat == actor` / `ref.zone == "hand"` / `card is not None`
  ∧ `spec.zones == ["hand"]` ∧ `spec.total == [1, 1]`（= `isOwnHandSinglePick` 的输入）。
  合成 Query 的那条老用例（`test_the_public_processing_slots_survive_foreign_masking...`）保留：
  它证的是抹牌面那半边，本票不替它。

### 验收条目 → 断言

| 票面验收 | 断言 |
|---|---|
| ① 雌雄选择面可见 + `reveal_hand` 生效 | `test_skills.py::test_double_sword_discard_face_is_visible_to_the_target`（真引擎记录流里的那条 `SELECT_CARDS`：每槽 `card is not None`、`ref.seat == 1`、槽位顺序 = 目标手牌顺序）；回环半边 = `test_web.py::test_the_own_hand_direct_pick_payloads_start_from_a_real_engine_pending` |
| ② 火攻载荷从 `dialog=="reveal"` 改为 `dialog is None` | `test_web.py::test_the_human_targets_fire_attack_reveal_step_is_a_table_direct_pick_without_leaking`（`dialog is None` / 无 `revealed` / 槽位带牌面且 = `view.self_hand` / 外人仍抹空） |
| ② 烟测四条改写为牌桌直选 | 烟测 `M5-08a-b1`（不开弹窗、不铺槽位面、两张手牌都可点）/ `b2`（悬停描述照常可读）/ `b3`（点第一张 = 面内位置 0 的 `SelectCards`）/ `b4`（点第二张 = 面内位置 1）；另加 `M5-08a-b6`（旧载荷被对称否决） |
| ③ 烟测 `U-YS-01`：点第 1 张红桃后其余红桃置灰、四花色齐才可提交（**票 15 起置灰由后端预览回包给**，`U-YS-01a..f` 同批次改写） | 烟测 `U-YS-01a`（五张初始都可点 + 提示写"每种花色至多 1 张" + 未齐不可提交）/ `b`（另一张红桃置灰、其余三色照常）/ `c`（置灰的牌点不动）/ `d`（四色齐 ⇒ 提交可点 + 载荷 = 候选那条升序 `SelectCards`）/ `e`（取消后置灰立刻解除） |
| ④ 烟测 `U-CX-01`：雌雄弃牌步直接在手牌区点选（本步此前没有前端用例） | 烟测 `U-CX-01a`（不开弹窗 / 不铺未知区域槽位面 / 两张都可点）/ `b`（点一张 = `SelectCards` 位置 0）/ `c`（点另一张 = 位置 1）/ `d`（**反面对照**：隐藏槽形状确实落回通用槽位面 ⇒ 可见槽是唯一判据） |
| 静态：判据只有一份、置灰与拒点同源、退役条件写在旁边 | `test_web_static.py::test_the_own_zone_suit_cap_reads_the_pick_preview_instead_of_counting_suits`（**票 15 改名**：原来的 `..._is_one_shared_predicate_reading_the_payload` 那份止损判据已退役，本行按当时的**现行名**回填）与 `::test_the_own_zone_direct_pick_shape_is_one_predicate_without_a_dialog_deadlock` |

### 会红的既有断言（已**刻意改写**）

1. `tests/test_web.py` 的火攻亮牌步载荷：`dialog == "reveal"` → `dialog is None`、`revealed`
   非空 → 为空、牌面断言从 `revealed` 挪到 `slots[].card`（函数名同步改为
   `..._is_a_table_direct_pick_without_leaking`）。
2. `ui_smoke.mjs` 的 `M5-08a-b1..b4`：从"亮牌弹窗只读 + 点窗内牌"改写为"牌桌直选 + 点手牌"，
   一条不多一条不少（`b1..b4`）。
3. **两处**借了"火攻亮牌步"当"弹窗步骤"夹具的**无关**断言被连带改写（刻意登记）：
   `M5-08b-e1`（铁索段的"弹窗步骤 ⇒ 牌桌只读 + 无重铸入口"）与
   `M5-08c-d1`（酒段的"弹窗步骤里点牌不设焦点"）。夹具换成**观看授予**形状（被亮的那张不在
   自己手里），被验证的行为一字未改；两处都写了夹具说明。
4. **`tests/support/web_client.py`（真 HTTP 的 tape 驱动，也是"前端读取方"的一份）**：
   - `_fire_choice` 的亮牌步判据从 `dialog == "reveal"` 换成分离出来的形状谓词
     `_fire_reveal_step` / `_is_own_hand_face`（与前端 `isOwnZonePick` 同一份形状），并新增
     "`target_self` 那一段必须认得出亮牌步"的断言（把原来**恒为假**的 `reveal_seen` 旗子
     变成活判据）；
   - `_fire_attack_fixed` 的亮牌步两段：`dialog is None` + `revealed` 为空（**正向钉住本票的
     载荷变更**），牌面改读 `slots[].card`。
5. `ui_smoke.mjs:14310`（火攻第二步"不是弹窗"的先例）= 今天的 `M5-08a-c1`，**保持绿**。
6. `tests/test_web.py` 的火攻弃牌步 / 放弃 / 无同花色三条
   （`test_fire_attack_same_suit_discard_deals_one_fire_damage` /
   `test_fire_attack_pass_with_a_same_suit_card_deals_no_damage` /
   `test_fire_attack_without_a_same_suit_card_has_no_discard_query_and_narrates_auto_pass`；
   票面写的 `9430/9500/9535` 是旧行号）不受影响，照旧绿。
7. `tests/test_web_static.py` 无既有断言变红（只**新增**两条）；`docs/agents/frontend-map.md`
   的 `candidates.js` / `modal.js` 两行补了新判据（导航图跟着改）；`开发文档/04-AI与交互设计.md`
   的 M5 火攻那条补了"票 09 起是牌桌直选、亮牌弹窗只留给观看授予"的后续口径（原句保留为
   "M5 当时的样子"）。后两项与 `tests/support/web_client.py` 是本次**额外的**改动面，
   一并登记在这里。

### 门禁结果

- `.venv/bin/pytest -q tests/test_skills.py tests/test_web.py` → **360 passed**。
- 子集：`tests/test_web_static.py` → **130 passed**（含新增 2 条）；`tests/test_action_contract.py`
  + `tests/test_ai_adaptation.py` → 31 passed；`tests/test_m5_bugfix_engine.py` +
  `tests/test_normal_tricks.py` → 42 passed；`tests/test_heuristic_ai.py` → 133 passed（雌雄 / 火攻的
  跨文件引用面逐块核过）。
- `bash tests/test_web.sh`（整个 Web 门禁：静态资源、409、脚本 `node --check`、烟测、三条
  固定 tape、酒 tape）→ **PASS: M4 端到端门禁全部通过**（`火攻 tape seed=4 / seed=16` 两段都
  PASS，含改写后的"亮牌步识别"断言）。
- `bash tests/smoke-web.sh` → **全部通过**（`--verbose` 下 **1635 条 `PASS`**、0 条 `FAIL`），
  含 `U-YS-01a..e` / `U-CX-01a..d` / `M5-08a-b1..b4` / `b6`。
- `bash tools/check_goldens.sh` → **PASS: 47 条标准 108 金录像逐字节不变**。票面的"不改字节"
  已核：`tests/replays/43-double-sword-opposite-gender.jsonl` 的 `SELECT_CARDS` 计数 = 0
  （该录像走的是 `draw_one` 分支）；投影是纯派生、不进记录流，火攻也不在标准 108 里 ⇒ 零重录。
- `.venv/bin/python tools/check_acceptance.py .scratch/table-ux-overhaul` → **PASS**（本 effort
  8 张票共 52 个引用的测试名都存在且绿）。
- 全量：`git commit` 的 pre-commit hook（`tests/run_all.sh --quiet`）→ **ALL TESTS PASSED**
  （10 段 shell 检查 + pytest 全绿；实现提交 `f340fa0`）。第一轮提交曾被 hook 拒（真 HTTP 的
  tape 驱动 `tests/support/web_client.py` 还按旧载荷读亮牌步），修完那份读取方后重提通过。
- `bsk`：**未驱动**（本环境 `browser_interact` 的 cooperate 握手失败，与票 07 同一实测结论），
  交互那半边由 `bash tests/smoke-web.sh`（同一批 DOM 行为的自动半边）+ 下面的配方兜。

### 复现配方（实机，票 09）

**环境**：`bash tools/check_env.sh` → PASS；起服务 `bash tools/serve-dev.sh start 8137`
（URL 以它打印的为准）。开局页：人数 4、时限 0、其余随机。

- **① 雌雄双股剑**（标准 108，种子 **63**，人类座位 0）：
  `.venv/bin/python tools/acceptance_seed.py --players 4 --card double_sword`
  → 起始手牌含【雌雄双股剑】+【杀】。点将：座位 0 = **男性**（如 关羽）、座位 1 = **女性**
  （如 貂蝉）。步骤：装备【雌雄双股剑】→ 对 1 号位使用【杀】→ 触发后**目标**（1 号位）看到
  「弃一张手牌 / 令你摸一张」→ 若人类就是目标（把人类座位配到目标侧，或对该 AI 目标看人类
  屏幕上的那一问），弃牌步应**直接在手牌区点选**（不再开"未知区域"窗）。
- **② 火攻**（军争 160，种子 **4**，人类座位 0）：
  `.venv/bin/python tools/acceptance_seed.py --players 4 --card fire_attack --deck-profile standard_ex_160`
  → 起始手牌含【火攻】。步骤：出牌阶段用【火攻】指定**自己**（有手牌即可，含自己）→ 亮牌步
  应在**自己的手牌区直接点一张**（不再弹亮牌窗）；点完即亮出，随后是"同花色弃牌 / 放弃"那一步
  （票面说的先例，本来就是牌桌直选）。
- **③ 业炎**（点将：神周瑜（移动版），人类座位 0）：先攒到**四花色各一张**手牌（打一两回合 /
  用掉或摸牌），出牌阶段发动【业炎】→ 分配弹窗里把某一座位计到 **2 点**（有 3 点时更明显）→
  确定 → 代价选牌面出现：点第 1 张红桃后，**其余红桃立刻置灰**，四花色各一张齐了「提交」才亮；
  再点已选的红桃可取消、置灰立刻解除。

### 给后来的票

- 票 10（武将牌区派生 / 空转重绘）：本票没碰 `session.js` / `applyLiveDelta`。
- 票 11（步骤预览端点）：`candidates.js:ownZoneSuitTaken` 是**该端点的自然第一个退役者**
  （见上面的止损条款）；接线后删掉它，把 `cardFace` / `toggleSlotPick` 改成读预览结果。
- 票 12（转化清单逐张置灰 / 渐营动态池）：同一份"后端现算合法集合"。
- 票 13（座位多选收口）/ 14（建局底稿）：与本票零交集。
- 任何"自己要看的牌面"以后都别再造一个 `revealed` 授予：先看 `_own_hand_face` 与
  `ownHandSlotsKnown` 这条分界（**牌在不在自己手里**），否则会把牌桌直选又挤回弹窗。
