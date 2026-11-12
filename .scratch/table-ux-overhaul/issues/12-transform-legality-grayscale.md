# 12: 转化清单逐张置灰 + 渐营动态池

**What to build:** 转化清单里**每一张源牌都按合法性置灰**（不合法的直接灰、合法的亮），
选定源牌后**逐产物置灰**；界沮授【渐营】的目标池改为按当前答案**现算**（不再逐组合预生成行）。

**Blocked by:** 11（预览端点）
Status: resolved
Type: task
Touches: src/sgs/skills/heroes.py, src/sgs/skills/virtual.py, tools/sgs_web/projection.py, tools/sgs_web/static/preactivation.js, tools/sgs_web/static/card-face.js, tests/test_hero_transform.py, tests/test_hero_jie_yijiang.py, tests/test_web.py
Ruling: spec §2 Q15 / Q20（三级置灰）、Q16（转化牌与真牌同档）

## 根因（spec §1 #6-渐营）

1. **转化候选不进 `candidate_index`**：`transform_candidates` 被从 `legal_actions` 摘进 `transform_actions`
   （不进序列化，`engine/game.py:5579-5590`）⇒ 前端对转化牌的目标合法性**完全没有判据**，
   唯一通道是预发动清单。
2. **渐营第 3 步（选目标）的动态池**：`JIE_JIANYING_FLOW`（`skills/active.py:7309-7336`）的
   `PickSeats(lo=_lo, hi=_hi, pool=_pool)` 是**可调用档**，池由 `_jie_jianying_targets` / `_jie_jianying_target_pool`
   按前序答案（牌名）现算——**服务端同源是做到的**；断点在清单渲染按"源牌 × 牌名"逐行下发，
   组合数超过 32 就整步退化（`flow.py:1318/1506-1527/1792-1811`）。实测 160 包满血 **9 张自有牌**即触发。
3. **转化清单没有逐张合法性**：源牌步只给"能作为来源"的选项，不判"这个产物此刻能不能用"
   （次数用尽 / 没有合法目标 / 被禁用）。

## 要做什么

1. **源牌级置灰**（Q20）：该源牌的**所有产物都不可用**才灰。
   "可用"读 `check_usable` 的前三条（时机 / 禁用 / 次数，`effects/actions.py:465-500`）。
   计算点：**询问时算一次**（不用预览请求——源牌是全集的、与已选无关），规模 ≤ 20 源 × 6 产物 = 120 次纯谓词，< 1 ms。
2. **产物级置灰**：选定源牌后，逐产物判"此刻能不能用"（含**有合法目标**这一条）：
   空城诸葛亮 ⇒ 【杀】灰、【桃】满血灰、【酒】/【无中】亮。
   **注意**：产物级用的是"**能否使用**"，不是"能否选目标"——目标是下一步的事。
3. **目标级**：进目标步，由票 11 的预览回包决定能点谁（空城 ⇒ 无座位可点 ⇒ 进不了下一步）。
4. **渐营的动态池走预览**：`MANIFEST_POOL_LIMIT` 与逐组合行随票 11 一起退役；目标步的池由
   `_jie_jianying_target_pool` 现算（**同一个函数**，不许另写一份）。
5. **灰色也要能"看见"**：置灰的源牌仍显示（`disabled`），悬停给一句"此刻没有可用的产物"；
   置灰的产物同理。**不要**直接从清单里删掉（玩家会以为清单漏牌）。
6. **合法性判据只许有一份**：源牌级 / 产物级都调用引擎侧已有函数（`check_usable` / `transform_candidates`），
   前端不重算。

## 验收

- 新增（`tests/test_hero_transform.py`）：
  - `test_a_source_card_with_no_usable_product_is_disabled_in_the_manifest`
    （【杀】次数用尽且手里只有能做【杀】的牌 ⇒ 全灰）。
  - `test_a_product_without_legal_targets_is_disabled_but_the_source_card_stays_enabled`
    （空城诸葛亮：源牌亮、【杀】灰、【桃】/【酒】按自身状态判）。
  - `test_every_transform_source_is_checked_against_the_engine_predicate`
    （清单里的 disabled 集合与引擎 `transform_candidates` 的差集必须为空——**同源断言**）。
- 新增（`tests/test_hero_jie_yijiang.py`）：`test_jianying_target_step_has_a_live_pool_with_many_cards`
  （20 张自有牌的局面下，目标步仍有池、且池 = `_jie_jianying_target_pool` 的结果）。
- 新增（烟测）：`U-TF-01` 转化清单里灰项画出来但点不动；`U-TF-02` 渐营选完源牌 + 牌名后可点座位。

## 会红的既有断言

- `tests/test_hero_jie_yijiang.py:2290-2310`（只测 2 张牌，永远不命中退化）——补一条真命中。
- `tests/support/ui_smoke.mjs:4647-4690`（PZ10–12）——票 11 已改写，本票确认无残留。
- 金录像：`tests/replays/29-serpent-spear-virtual-slash.jsonl` / `42-hero-transform.jsonl` 若清单形状变化
  （`disabled` 字段），**记录流不变**（清单整体 `exclude=True`）⇒ 预计 0 条重录；跑 `check_goldens.sh` 确认。

## Answer

### 落地摘要

**三级置灰的落点（Q15 / Q20）**

1. **源牌级**（`heroes.py`）：新增三件生产器，判据**只有一份**——引擎的 `check_usable`：
   - `conversion_product_types(skill_id)`：这条技能的**产物全集**（`CONVERSION_SKILLS` 的
     `target_type_id` 去重保序）。候选池只含"此刻真有合法目标"的那几条，拿它当全集会把
     "目标不合法"读成"没这个产物"；
   - `conversion_sources_by_product(state, owner, skill_id)`：`来源牌 → 它能的产物`，逐条走
     `conversion_offers`（`ConversionSkill.select` 那唯一一份源筛选）；
   - `unusable_transform_sources(state, owner, skill_id, context=…)`：**所有产物都不可用**的
     源牌 = 源牌级灰集。读 `check_usable` 的**前三条**（时机 / 禁用 / 次数），
     `require_targets=False` —— 目标维度不属于源牌级。
   - `conversion_preactivations(..., context=…)` 把灰集算一次（询问时，不走预览往返），
     减掉候选源，交给 `_manifest_steps`：**灰的进 `options` + 进 `StepPool.disabled`**，
     不删行（删了玩家会以为清单漏牌）。
   - `check_usable` 加 `require_targets: bool = True`（`effects/actions.py`）：源牌级读
     前三条、产物级读整条 ⇒ **同一个实现**，不新造第二条使用校验链。
   - `engine/game.py` 加 `_conversion_use_context(kind)` 一处映射（`PEACH → NEAR_DEATH`、
     `DODGE/RESPOND → RESPONSE`、其余 `PLAY_PHASE`）——语境是"时机"那一条的输入。
2. **产物级**：界沮授【渐营】② 的**牌名步**（本仓唯一有"产物步"的转化技）读 `check_usable`
   **整条**（含目标池）：`_jie_jianying_name_disabled`（票 18 已落地）——空城 ⇒【杀】灰、
   满血 ⇒【桃】灰、出牌阶段 ⇒【闪】灰、【酒】亮。本票把它与源牌级放进**同一条用例**钉住
   两级判据的分工，不改它的实现。
3. **目标级**：渐营第 3 步的可点座位仍由**票 11 的预览回包**回答（`preactivationOptions` 读
   `pa.preview.options`）；清单里的逐行池退为静态快照。空城 / 攻击范围外 ⇒ 回包不给那个
   座位 ⇒ 点不动、进不了下一步。

**界面（灰得看得见、点不动）**

- `preactivation.js`：新增 `PREACTIVATION_SOURCE_NO_PRODUCT`（「此刻没有可用的产物」）与
  `preactivationItemDisabled(pa, index, value)`；`preactivationCardPickable` /
  `togglePreativationCard` 对**选牌步**也读 `disabled`（第二道闸，键盘 / 陈旧界面同一条）。
- `card-face.js`：`cardFace` 的预发动支路把 `disabled` 算进 `selectable`，并回一格 `note`；
  `table.js` → `interaction.js:cardChip` → `card-view.js:cardFaceNode` 把 `note` 拼进悬停
  全文（不覆盖牌面全文）；`seat-view.js` 的装备 / 判定槽只在有 note 时补原生 `title`
  （常规槽位仍不挂）。
- `projection._mask_foreign_preactivations`：选牌步的 `disabled` 与 `options` 是**同一条泄露
  面**（源牌 id 同样是询问者的手牌）⇒ 非询问者看到两份都清空（本票补的泄露面，配套断言在
  `test_web.py::test_the_source_groups_are_masked_for_a_foreign_viewer`）。
- 语义升级写进三处 docstring：`StepPool.disabled`（两级语义 + `options − disabled` = 可点）、
  `flow._pool_rows`（`disabled` 不再只属于选项步）、`conversion_preactivations`（`context`）。

**刻意保留（没有被本票删掉的静态行，逐条给替代品）**

| 静态行 | 保留理由 | 谁在替代它 |
|---|---|---|
| `Step.pool`（转化清单的**目标步**逐来源行） | 转化清单**没有流水线** ⇒ `pending.preview` 不带这个键，目标级没有第二个来源；行还带 `StepPool.max`（方天式逐来源上限） | 无（它是唯一真源） |
| `Step.pool`（渐营目标步的逐组合行） | 它带 `StepPool.max = 0` = "这个牌名下没有要选的目标" ⇒ 界面**自动跳过空步**（票 18 的【桃】/【酒】）；预览回包的 `can_advance` 表达不了"这一步是 no-op" | 可点集合由预览回包取代；**形状 / 上限仍读行** |
| `Step.groups`（多来源步的合法来源组合） | 丈八 / 乱击的"哪几张能配成一组"仍在用；灰的源牌**不进** groups（不参与组合） | 无 |
| `StepPool.disabled` | **本票重定义**：选项步 = 产物级（整条 `check_usable`）、选牌步 = 源牌级（前三条） | 无（它自己就是新真源） |
| `StepPool.max` | 逐来源 / 逐牌名上限 + `max = 0` 的自动跳过 | 无 |
| `MANIFEST_POOL_LIMIT` / 退化档 | **票 11 已删**，本票确认无残留（`smoke` 的 `PZ10–12` 已是"拿不到候选 ⇒ 一个都不点"） | — |

**删除的东西**：无（本票没有删任何静态行或既有断言）。改写既有断言的只有两处：
`test_web.py::test_the_source_groups_are_masked_for_a_foreign_viewer`（补 `disabled` 的
抹除断言）与 `tests/test_hero_jie_yijiang.py` 末尾的两条**新增**用例（未改旧断言）。

### 验收条目 → 断言

| 票面验收 | 断言 |
|---|---|
| 源牌级：所有产物不可用才灰，且灰的仍可见 | `tests/test_hero_transform.py::test_a_source_card_with_no_usable_product_is_disabled_in_the_manifest`（龙胆：手里【杀】+【闪】、出牌阶段 ⇒ `options = {杀, 闪}`、`disabled = {杀}`；反面对照：**空城不算源牌级不可用**） |
| 产物级：没有合法目标 ⇒ 产物灰、源牌亮 | `tests/test_hero_jie_yijiang.py::test_a_product_without_legal_targets_is_disabled_but_the_source_card_stays_enabled`（渐营②：源牌步 `disabled == ()`；牌名步 `options ⊇ {闪, 桃, 杀}`、`disabled ⊇ {杀, 闪}`、`桃` 亮） |
| 同源（清单的 disabled = 引擎谓词） | `tests/test_hero_transform.py::test_every_transform_source_is_checked_against_the_engine_predicate`（灰 ∩ 候选 = ∅；每个灰源的每个产物都过不了 `check_usable` 前三条；每个没灰的源至少一个产物过得了） |
| 渐营目标池现算、20 张自有牌不退化 | `tests/test_hero_jie_yijiang.py::test_jianying_target_step_has_a_live_pool_with_many_cards`（20 张【杀】在手里 ⇒ 目标步仍有池；`preview_activation` 的 `options` 与清单行 = `_jie_jianying_target_pool` **同一个函数**的结果 = `("seat:1","seat:3")`） |
| 烟测 `U-TF-01` 灰项画出来但点不动 | `tests/support/ui_smoke.mjs` `㉿U-TF-01a`（灰源牌在手牌区带 `.dis` + 悬停「此刻没有可用的产物」）、`㉿U-TF-01`（点它不写答案、光标不动）、`㉿U-TF-01b`（对照组：亮的点得动并自动进目标步） |
| 烟测 `U-TF-02` 渐营选完源牌 + 牌名后可点座位 | `㉿U-TF-02a`（选完源牌进牌名步）、`㉿U-TF-02`（可点座位 = 预览回包的池：1/3 可点、2 不可点）、`㉿U-TF-02b`（池外座位点了没反应、不发请求）、`㉿U-TF-02c`（池内座位拼出 `ActivateSkill(choices=["杀","seat:1"])`） |
| 金录像 0 条重录 | `bash tools/check_goldens.sh` → 47 条标准 108 录像逐字节不变（清单整体 `exclude=True`） |

### 刻意读法（评审请逐条看）

1. **两级判据为什么必须分开**：`check_usable` 的第 4 条（目标池）在源牌级会制造假灰——空城
   把【杀】的目标清空，但那张牌本身仍能当【桃】/【酒】；反过来，"没有合法目标"正是**产物级**
   要回答的。故源牌级显式传 `require_targets=False`，产物级读整条。**一个函数两档**，不是
   两条规则。
2. **产物级只住在"有产物步"的转化技上**（渐营 / 蛊惑这类流式转化）：H 族转化清单**没有牌名
   步**，一张源牌在一条技能下只对应一个产物 ⇒ 在那里"产物级"必然退化成"源牌级"，再造一份
   没有读者的第二级只是多一条会漂移的判据。**为什么不在 H 族造"可见但产物灰"的源牌**：
   一条产物没有合法目标就不会有候选 ⇒ 那种源牌一旦可点，点完既没有目标步也没有可提交的
   动作（正好是 `spec §6 #5` 禁的死路），故它不出现，目标级（空城 ⇒ 没有座位）负责回答。
3. **缺口 #乙 的落法**（map.md）：11 退役的是 `MANIFEST_POOL_LIMIT` + 退化分支 + "静态行 =
   界面唯一候选"这条通路；`disabled` / `max` 由本票重新定义（见表）。本票**没有**删任何静态行
   ——每一行的替代品都写在上面那张表里，逐条可查。
4. **【决定 1】`candidates.js:ownZoneSuitTaken`（票 09 的 stopgap）**：**保留**。票 11 把预览
   端点收在**预发动清单**上（`issued_manifest` → 流式步骤），而业炎的花色上限住在
   `SELECT_CARDS` 的**自家区域面**（`isOwnZonePick`）——那种询问没有清单、`pending.preview`
   不带这个键，预览端点取不到 `spec.max_per_suit`，服务不了这条判据。删它会重开报障 #6 ②
   （手牌面整面无花色置灰）。**退役条件**：等 `SELECT_CARDS` 自家区域面也声明预览能力
   （`pending.preview = true` + 端点按 `spec.max_per_suit` 回逐槽位可点集）之后，卡面改读回包，
   这条 stopgap 与它的注释一起删。
5. **【决定 2】哪些 `StepPool` 静态行作废**：**零行**（逐条理由见上面的表）。真正作废的是两条
   **读法**：①"来源步的 `options` = 能点的来源"→ 现在是"这一步列出的来源"（可点 =
   `options − disabled`）；②"`disabled` 只属于选项步"→ 选牌步也有（源牌级）。
6. **`context` 由引擎映射**：源牌级要读"时机"，而语境只有 `_build_query` 知道。映射写一次
   （`_conversion_use_context`），清单派生不按询问种类猜——否则"清单说灰"与"候选说亮"会在
   响应窗口里各说一套。
7. **灰的源牌不进 `Step.groups`**：多来源步的组合表只按候选源算。灰的牌搭进去会让界面以为
   "这两张能一起转化"，而它们的组合一定不在候选池里 ⇒ 提交必被拒。
8. **成本（实测）**：源牌级灰集是 20 源 × 1 产物 ≈ **1.34 ms**（中位；20 张红牌的关羽清单，
   含整份清单派生，max 2.02 ms）；120 条纯谓词（20 源 × 6 产物）≈ 3.1 ms。它**每问算一次**
   （不是每次选择变化），远低于票面的 20 ms 关注线；票面写的"< 1 ms"偏乐观（`transformed_card`
   构造占了多数），但没有任何预览往返。渐营 20 张自有牌的清单渲染 ≈ 10.6 ms / 40 行——那是票
   11"永不退化"的既有代价，本票没有加码。
9. **`bsk` 在本环境不可用**（cooperate 握手失败）⇒ 实机验收按下面的配方人工走一遍；功能门禁
   是 `bash tests/smoke-web.sh`。

### 会红的既有断言（本票的处理）

- `tests/test_hero_jie_yijiang.py:2290-2310`（票面点名"只测 2 张牌、永远不命中退化"）：票 11
  已把退化档删掉，那条用具（现为 `test_jie_jianying_lists_targets_as_the_third_manifest_step`）
  仍绿、保留为小局面钉子；**真命中**由新增的
  `test_jianying_target_step_has_a_live_pool_with_many_cards` 补上（20 张自有牌）。
- `tests/support/ui_smoke.mjs:4647-4690`（PZ10–12）：确认**无残留**——票 11 已改写成
  "没有候选 ⇒ 一个都不点"，本节新增的 `U-TF-*` 不碰它们。
- 金录像 `29-serpent-spear-virtual-slash` / `42-hero-transform`：清单形状变了（源牌步多了
  `disabled` 与几行灰），但清单整体 `exclude=True` ⇒ **记录流不变、0 条重录**（已用
  `check_goldens.sh` 逐字节确认）。
- 票面 `Touches` 之外**实际也动了两处**，理由：`src/sgs/state/protocol.py`（`StepPool.disabled`
  的语义文档）、`src/sgs/effects/actions.py`（`check_usable` 的 `require_targets` 开关）、
  `src/sgs/engine/game.py`（`UseContext` 映射）、`tools/sgs_web/static/{card-view,interaction,
  seat-view,table}.js`（悬停说明的透传）。**没有**改 `map.md` / `acceptance-map.md` / 别的票。

### AI 适配契约（`docs/agents/ai-adaptation.md` §1 六条）

不适用（逐条）：①没有新增 `QueryKind`；②没有新增 / 改动作构造子，AI 自造的候选面
（`legal_actions` / `preactivations` / `transform_preactivations` 的**形状**）未变——只是清单
里多列了几行灰的源牌，而 AI 提交前仍做池成员校验（`_conversion_sources_within_pool`），灰的
源牌永远不在 `query.transform_actions` 里 ⇒ 决策不变；③没有新增 / 改动技能语义；④无新覆盖
场景；⑤没有新信息通道（`StepPool.disabled` 是既有字段，清单整体 `exclude=True`，不进
`PlayerView` / 记录流）；⑥`tests/test_ai_adaptation.py` 未改且跑过。

### 门禁

- `.venv/bin/pytest tests/test_hero_transform.py tests/test_hero_jie_yijiang.py` → **197 passed**
  （新增 4 条：108 + 89）
- `.venv/bin/pytest tests/test_web.py` → **315 passed**（含改写过的抹除断言）
- `bash tests/run_all.sh --quiet`（全量门禁，手动跑一次确认提交前的基线）→ **ALL TESTS PASSED**
- `.venv/bin/pytest tests/test_web.py tests/test_skill_flow.py tests/test_hero_transform.py
  tests/test_hero_jie_yijiang.py` → **558 passed**
- `.venv/bin/pytest tests/test_dod_m4.py tests/test_ai_cases.py tests/test_m5_no_leak.py` → **87 passed**
- `bash tests/smoke-web.sh --verbose` → **1683 条 PASS / 0 条 FAIL**（新增 7 条 `U-TF-*`）
- `bash tools/check_goldens.sh` → **47 条标准 108 金录像逐字节不变**
- `bash tests/test_dev_docs.sh` → 全部通过
- 全量门禁：随本次提交的 pre-commit hook（`tests/run_all.sh --quiet`）。**第一次提交被 hook
  拒了**——原因是与本票无关的并发：提交在跑门禁时另一个烟测任务也在同一个 checkout 里起服务，
  `test_web.sh` 在 CPU 争用下红了；单独重跑 `run_all.sh --quiet` 全绿，再提交即过。教训：
  hook 跑的时候别再并发跑长检查。

### 手工复现配方（`bsk` 不可用时的实机验收）

1. `bash tools/serve-dev.sh start 8100`（或 `.venv/bin/python tools/sgs_web/serve.py --port 8100`）。
2. 开局页：4 人局，**己方点将「赵云」**（龙胆），其余随便；进入自己的出牌阶段。
   手牌里要同时有【杀】与【闪】（可反复重开换种子，或用
   `.venv/bin/python tools/acceptance_seed.py --players 4 --card slash` 找种子）。
3. 点「龙胆」技能按钮 ⇒ 来源步列出**两张**牌：**【杀】是灰的**（出牌阶段没有把【杀】当
   【闪】的时机）、悬停显示「此刻没有可用的产物」，点它**没有反应**；**【闪】是亮的**，
   点它进目标步（这一步的座位 = 服务端回包）。
4. 界沮授：**己方点将「界沮授」**，给对手挂【空城】（或用界诸葛亮）、自己 2/3 体力；点
   【渐营】⇒ 选一张牌 ⇒ 牌名列表里【杀】灰（没有合法目标）、【桃】亮（2/3 体力）、
   【闪】灰（出牌阶段）；选【杀】之后**只有回包给的座位可点**，池外的武将点了没反应；
   选完点「提交」发起，`#t-target-hint` 写「清单已填完，可以确认发动」。
5. fail-closed：停掉服务后再动一次选择 ⇒ 「取不到候选，请重试」、不许提交、光标不动。

### 交接给 13 / 14

- **票 13（座位多选收口）**：`preactivationItemDisabled` 与 `preactivationDisabled` 已是
  "这一步这一项能不能点"的唯一界面判据（选项步 / 座位步 / 选牌步通用）；连营 / 大雾 / 突袭 /
  奋威 / 除疠 的可点集合仍读预览回包 `options`，不需要新判据。若要让灰座位也带悬停理由，
  座位节点目前**没有** `face.note` 通道（只有卡面与槽位有）——要加就加在 `seat-view.js`
  的 `seatNode`，别再各写一份。
- **票 14（L0 边界暂停 + 开局底稿）**：本票没有碰 `view()` / 快照 / delta；`transform_products`
  与 `preactivations` 的形状逐字未变（只多了 `disabled` 的内容），开局底稿那一路不受影响。
  注意 `stage` / `live` 派生里**不要**读清单的 `disabled`（它是询问期派生，回放期没有意义）。
