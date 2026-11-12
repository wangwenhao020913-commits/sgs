# 18: 五谷丰登亮牌面——目标条不再压住牌面；被拿走的牌当场更新

**What to build:** ① 亮牌面的**目标条**（座0 / 座1 …）压在亮出的牌上，牌面底部被挡；
② 有人拿走一张牌后，亮牌面**整段不变**（看不到谁拿走了哪张、也看不到目标条状态），
直到结算结束。修成：目标条不重叠、每一张被拿走的牌**当场**置灰 + 标出拿牌者、
目标条同一刻翻成"已拿"。

**Blocked by:** None
Status: resolved
Type: task
Touches: tools/sgs_web/projection.py, tools/sgs_web/serve.py, tools/sgs_web/session.py, tools/sgs_web/static/reveal.js, tools/sgs_web/static/modal.js, tools/sgs_web/static/session.js, tools/sgs_web/static/style.css, tests/support/ui_smoke.mjs, tests/support/ui_bridge.js, tests/test_web.py, tests/test_web_static.py, docs/agents/frontend-map.md
Ruling: 用户 2026-11-11 实机报障（截图）+ `.scratch/table-ux-overhaul/map.md` 缺口 #癸；数据侧已正确（`tests/test_web.py::test_a_real_session_derives_the_full_harvest_state_and_the_chain_key`）

## 根因（已核到代码）

- **数据没问题**：真会话载荷在目标 1 取牌后已经给出
  `revealed[*].taken_by == [0, null, null, null]`、`targets[*].status == ["taken","current",
  "pending","pending"]`（见上面那条既有断言）。所以这是**渲染 / 刷新**问题，不是投影问题。
- **重叠**：`style.css` 的 `.reveal-targets { position: absolute; left: 14px; bottom: 10px; }`
  —— 目标条被绝对定位在弹窗正文底部，只靠 `.dialog-body.reveal-body { padding-bottom: 52px }`
  预留高度。亮牌是"一排卡面 + 换行"的高度不定的内容：正文一滚动 / 一换行，绝对定位的条
  就压在牌面上（截图：5 张牌被目标条切去底部）。
- **不刷新**：亮牌在五谷整段结算里用**同一个弹窗链键**（`reveal:${harvest.key}`，
  `modal.js:dialogKeyFor`）跨 X 个目标，且持久展示路径（`applyDialogContentMode` 的
  `persistent` 分支）与普通正文路径（`#t-options` 搬进正文槽）**都只在某些时机重画**：
  请按下面的复现步骤定位到底是哪一路没重画（`renderPending` 是否每次新 pending 都调到
  `renderReveal`；持久展示分支是否只在开窗时跑过一次）。**修法以"每个新 pending 都重画一次"
  为准**，不要把"重画"做成第二条判据。


## 范围裁定（2026-11-12，实施期复现后改口径）

实施者按上面的"复现步骤"跑了两条探针（合成载荷 + 真会话 seed 238）后，原票的"每新 pending 重画"
**这一条本身不是缺口**：每一条带新 `harvest` 的 pending 到达后，卡面 / 目标条都已经与载荷逐字段
一致（persistent 与正常取牌两条路径都对）。真正的缺口是**投递**：AI 目标的取牌在服务端
`Session.drive()` 里被同步答完，**浏览器收不到那些 `taken_by` 状态**——人类手里没有无懈（或已用掉）
之后，从人类自己取牌到结算结束**一条 pending 都没有**，面板被冻结到结算结束（连自己的牌都不变色）。

用户据此裁定改口径（**D + CSS**，取代原票的"每新 pending 重画"）：

1. **服务端**：`view()` 额外下发**视图级** `harvest` —— 与 `pending.harvest` **同一份派生**
   （`projection.session_harvest_state` → 既有 `harvest_state`），只是挂在每一次视图下发上；
   只读派生字段，不新增记录 / seq，金录像逐字节不变。按票 01 的纪律用 `Session.harvest_cache`
   （键 = 记录条数 + 五谷帧 id）缓存两次全记录扫描，空转轮询不重扫。
2. **客户端**：每次视图提交若亮牌窗的链键 == `view.harvest.key`，就用**同一个** `renderReveal`
   按 `view.harvest` 重画（`applyIdleHarvest` → `repaintRevealFromHarvest`）；**禁止**前端从
   `card_move` 记录自算 `taken_by`（B 方案被否：会出现第二份真源）。重画不清本地选择。
3. **CSS**：目标条进正文流、删固定下内边距预留。
4. **测试**：真会话断言 `view.harvest` 带着 AI 的取牌；烟测 `U-HV-01`（无新 pending 的一拍
   当场置灰 + 徽标 + 目标条翻态）、`U-HV-01b`（同状态不重复变）、`U-HV-01c`（持久展示）、
   `U-HV-02`（静态）、`U-HV-03`（不清本地选择 / 不碰遗计多选）、`U-HV-04`（真会话视图通道）。
5. **AI 适配**（`docs/agents/ai-adaptation.md`）：**不适用**——`view.harvest` 是纯网页展示派生
   字段，不进记录流、不改引擎询问面，AI 读点表一个字段都没动（§1 checklist 的五条逐条不涉及）。

## 要做什么

1. **目标条进文档流**：`.reveal-targets` 不再 `position: absolute`（或改由 `.reveal` 的
   flex 布局承载），并删掉随之失去意义的 `padding-bottom: 52px` 预留；亮牌多时要能换行 /
   滚动而不遮住牌面。**不许**用更大的固定 `padding` 掩盖（高度不定 ⇒ 一定会再撞）。
2. **每个新 pending 重画亮牌节点**：同一链键下（五谷取牌段内、以及 AI 拿牌时的持久展示），
   只要 `pending.harvest` 的 `revealed` / `targets` / `current` 变了，就重画亮牌区
   （卡面 `taken` 置灰 + `座N` 徽标、目标条 `is-taken` ✓ / `is-current` ◀）。判据只读
   **服务端派生字段**（前端不自己算谁拿了牌），与既有 `renderReveal` 同一份渲染器。
3. **不许改**：`taken_by` / `status` 的服务端口径；"被拿走的牌留在原位、置灰、可读"
   这条设计（`reveal.js` 注释）；遗计式多选亮牌（`isPickRevealStep`）；折叠/展开。
4. 若重画会让"正在选牌的玩家"丢失本地选择（`state.picks`），保留它（与空转重绘同一条纪律：
   换内容不清选择）。

## 验收

- 新增（烟测 `tests/support/ui_smoke.mjs`，沿用 U2-11 组的合成载荷工厂）：
  - `U-HV-01` 同一链键的第二问（第 1 张 `taken_by=2`、目标 2 `taken`）⇒ 亮牌区**当场**
    出现 `座2` 徽标 + `.taken`、目标条第 2 枚翻成 `is-taken`；再问一次（无人新拿）不重复变。
  - `U-HV-02` 目标条与牌面**不重叠**：静态断言 `.reveal-targets` 不是 `position: absolute`
    （或断言它在 `.reveal` 的 flex 流里）；`reveal-body` 不再靠固定 padding 让位。
  - `U-HV-03` 重画不清本地选择：多选亮牌（遗计）已选两张时收到同链键的新 pending ⇒ 选中态保留。
- 静态断言（`tests/test_web_static.py`）：`renderReveal` 是亮牌唯一的渲染器（没有第二份
  "taken 徽标 / 目标条"实现）。
- 门禁：`bash tests/smoke-web.sh --verbose`、`.venv/bin/pytest tests/test_web_static.py tests/test_web.py`、
  全量 `tests/run_all.sh`；金录像 47 条逐字节不变（纯前端）。

## 会红的既有断言

- `ui_smoke.mjs` 的 U2-11 组（D3 / D4 / D10）里按旧 DOM 结构 / 绝对定位断言的用例；
  `tests/test_web_static.py` 里若有对 `.reveal-targets` 定位的断言。按新口径**改写**。

## Answer

**做法（D + CSS，按上面「范围裁定」）**

- **服务端（视图级派生通道）**：`tools/sgs_web/projection.py` 新增
  `session_harvest_state(session)` —— 与 `pending.harvest` **同一份**派生（既有 `harvest_state`），
  只是挂在每一次视图下发上；`tools/sgs_web/serve.py:view` 的 `harvest` 键（结算结束 ⇒ `None`，
  键恒在）。两次全记录扫描按票 01 的纪律缓存：`harvest_state(engine, cache=…)` +
  `Session.harvest_cache`（键 = 记录条数 + 五谷帧 id），`view` 与 `pending` 两处派生共用。
  只读派生字段：不新增记录 / seq，`taken_by` / `status` / `current` 的服务端口径一字未改。
- **客户端（同一渲染器重画）**：`reveal.js:revealData` 优先读视图级 `viewHarvestState(pending)`
  （链键不一致不认）；`reveal.js:harvestFingerprint` 判"这一拍要不要重画"；
  `modal.js:repaintRevealFromHarvest` 用**同一个** `renderReveal` 按 `view.harvest` 重画
  （持久展示写 `#t-dialog-reveal`、正常取牌写 `#t-options`）；`session.js:applyIdleHarvest`
  接在 `poll()` 的空转分支上，**不清本地选择**（不调 `resetSelection`，与 `applyIdleRepaint`
  同一条纪律）。**没有**前端从 `card_move` 自算 `taken_by`（B 方案被否）。
- **CSS（不重叠）**：`.reveal-targets` 从 `position: absolute` 改成 `.reveal` 里的普通弹性子项
  （换行 / 由外层正文槽滚动）；删掉 `.dialog-body.reveal-body { padding-bottom: 52px }` 与
  `.dialog-reveal` 的 52px 下内边距；`reveal-body` 类随绝对定位一起退役（`reveal.js` /
  `pending.js` / `modal.js` 三处接线删净）。**没有**用更大的固定 padding 掩盖。
- **AI 适配**（`docs/agents/ai-adaptation.md` §1）：**不适用** —— `view.harvest` 是纯网页展示
  派生字段，不进记录流、不改引擎询问面（`Query` / `legal_actions` 一个字段没动），AI 读点表
  不变；记牌守恒量（53 / 108 / 160）不受影响。
- **文档**：`docs/agents/frontend-map.md` 记了这个派生通道（`session_harvest_state`）与重画口
  （`applyIdleHarvest` / `repaintRevealFromHarvest` / `harvestFingerprint`），并更新了 `poll()`
  的空转三条出口。

**验证**

- `bash tests/smoke-web.sh --verbose`：全部通过（新增 `U-HV-01` / `U-HV-01b` / `U-HV-01c` /
  `U-HV-02` / `U-HV-03a` / `U-HV-03b` / `U-HV-04`；改写 `U2-11l` 的旧绝对定位断言）。
- `.venv/bin/pytest -q tests/test_web_static.py tests/test_web.py`：460 passed
  （新增 `test_the_view_payload_carries_the_active_harvest_state_with_an_ai_pick`、
  `test_the_view_harvest_derivation_is_cached_by_the_record_cursor`、
  `test_the_target_bar_sits_in_the_reveal_flow_instead_of_over_the_cards`、
  `test_render_reveal_is_the_only_painter_of_taken_badges_and_the_target_bar`、
  `test_the_view_level_harvest_channel_is_repainted_but_never_resets_the_selection`）。
- `bash tools/check_goldens.sh`：47 条标准 108 金录像逐字节不变。
- 空转轮询代价（354 条记录的构造局面，交错各 80 轮取中位数）：不带 harvest 8.135 ms /
  命中缓存 8.222 ms（**+87 µs**）/ 冷算（每拍重扫整条流）8.771 ms（+636 µs）⇒ 缓存把
  "视图级多一次派生"压到轮询噪声里。

**残余缺口（如实记录，建议另开票）**

"AI 目标取牌**当场**置灰"这一条**仍做不到**，原因是投递而不是渲染：AI 目标的五谷取牌在服务端
`Session.drive()` 内部被同步答完，浏览器只收到记录流、**收不到那些 `taken_by` 状态**；人类手里
没有无懈（或已用掉）之后，从自己取牌到结算结束**一条 pending 都没有**，而 `view.harvest` 在那一批
响应里已经是 `None`（五谷帧已出栈）。这次交付让它"每一拍视图都读同一份派生、不再依赖 pending
是不是亮牌步"，但一批里内联答完的那几次取牌仍然只在记录里。要真正兑现"当场"，只有两条路：
① 服务端在每个五谷取牌询问处让出（逐目标暴露 pending）；② 服务端按记录 seq 下发 harvest 快照
（在 drive 期捕获，不能事后派生——帧 data 不进记录流）。两条都超出本票给的口径，留待裁定。

**手工验收配方（4 人 / 种子 15 / 人类座位 0 / random AI）**

1. `bash tools/serve-dev.sh start`（或 `bash tools/serve-dev.sh restart`），打开打印出的 URL。
2. 开局页：人数 **4**、种子 **15**、座位 0、AI **random**、思考时限可留空 ⇒ 开始。
3. 起始手牌含**【五谷丰登】**（`dismantlement / eight_diagrams / dodge / harvest`）；等轮到自己
   的出牌阶段，点它并使用（目标 = 全体，直接确认）。
4. **看两件事**：① 中央亮牌弹窗里，**目标条（座0…座3）在牌面下方**、不再压住牌的下沿；
   牌多时正文自己滚动，条跟着牌面走；② 每次视图刷新后，**已被拿走的牌当场置灰 + 右下角 `座N`
   徽标**，目标条同一刻翻成 `座N✓` / 当前 `座N◀`。折叠 / 展开、遗计式多选亮牌照旧。
5. 残余缺口（见上）：你自己取牌之后、若其余目标都是 AI 且没有别的窗口再问你，面板会在结算结束
   时收起——那一批里 AI 的取牌仍看不到（另票）。

**门禁**：提交 hook（`tests/run_all.sh --quiet`）全量绿，见本次提交记录。
