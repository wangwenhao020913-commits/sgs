# 前端地图（Frontend Map）

`tools/sgs_web/static/` 的导航图：**只回答"改哪份文件"**，不复述产品行为。行为真源是
`开发文档/04-AI与交互设计.md`、各 effort 的 spec 与 `tests/`；脚本清单的**唯一真源**是
`index.html` 的 `<script src>` 顺序（页面与烟测共用）。下表按同一顺序解释职责，顺序判据
仍只在 `index.html` 与 `tests/test_web_static.py`。

## 脚本职责（按 `index.html` 顺序）

| 文件 | 职责 |
|---|---|
| `core.js` | 全局 `state`、`$`、`post` / `api`、`show` 导航、`bareCardRef`、牌名 / 描述 / 阶段 / 技能标签等共享纯函数。不含渲染与请求流程。 |
| `lobby.js` | 开局页表单（人数 / 点将 / 牌堆档位）、`POST /api/sessions`、`loadHeroCatalog` / `loadDeckCatalog`。 |
| `session.js` | 会话生命周期与派生：`applyView` / `commitView`、事件回放 `stepPlayback`、舞台三组、live view、轮询（见下节）。 |
| `candidates.js` | 候选索引的**只读**查询：`cardEntry` / `cardSelectable` / `isLegalCombo` / `autoCombo`；不重建索引。**自己区域的直选判据** `isOwnZonePick`（选自己的手牌/装备 ⇒ 牌面直选、不铺左下角面板；票 14）也在这里。 |
| `card-view.js` | 卡面绘制（花色 / 点数 / 牌名 / 类型行 / 悬停全文），纯展示。 |
| `card-face.js` | 卡面状态的纯函数：选中 / 置灰 / 槽位、`isProvide` / `provideSlotIndex`。 |
| `seat-view.js` | 座位卡的节点形状：武将块、判定 / 装备槽、状态类与悬停全文；**武将牌上的牌区**（`heroZoneRows`，标签带张数）与**标记行**（`markerRows`，与牌区同骨架异色、文本 = 名称×数量、悬停 = 来源 +（效果说明））都在 `fillHeroZones` 里铺。**被废除（封印）的槽**由 `sideBlockRows` 读投影字段 `players[].sealed_zones`（`PlayerPublic` 下发；**票 03 起是槽 id**：判定 `judge_1..3`（`judgeSlotLayout` 的 `sealKey`，写成 `data-slot-key`；`data-slot="judge"` 仍是 `anim.js` 的落位锚点）+ 装备四槽）**逐槽**叠红色禁止角标（`markSealedZone` → `.zone-sealed` + `.zone-ban`，视觉照 `.skill.suppressed` 的 `.skill-ban`；**每个被废除的槽各画一个**，判定行 / 装备格本身不再画，**区域形状不变**；lukang-mobile 票 03 取代 lei 票 05 的"整区一个"）。 |
| `table.js` | 环桌布局与表体渲染：`tableViewForRender`、`renderTable`、`renderProcessing`、`renderSkills`。 |
| `effects.js` | 动效层：目标线、行动者高亮、体力闪、技能名飘字 / 白光；寿命判定是纯函数。 |
| `preactivation.js` | 预发动清单、转化技来源模式、清单到 Action 的组装；选项步的**置灰不可选**（`preactivationDisabled` 读 `StepPool.disabled`，画 `.dis` + 不接 click；票 04 §3）；多来源步的**合法来源组合**（`preactivationGroups` 读 `Step.groups`：卡面 / 「下一步」/ 「提交」三处同一判据，凑不成一组的来源既选不出也交不出；huo-mobile-batch 票 10）。 |
| `interaction.js` | 点选：卡面芯片、点牌设焦点、点座位选目标、弃牌点选。 |
| `pending.js` | 询问按种类分派到专用面、提示条右端动作、取消 / 不支持面 fail-closed；涉猎的结果态门闸（`state.suitResult`）；**同一时机的激活面**（`renderActivationFace`：一条 `SKILL_ACTIVATION` 有 ≥2 条 `ActivateSkill` 候选时铺"选一条发动"的逐技能行 + 末条「都不发动」，退出判据是候选里的 `Pass`；选中哪条就按 `activation_preactivations[skill_id]` 进它自己的清单，没有清单则一击即提交；`skill-face` 只在这个形状出现，单候选面仍走直提态）；**两行观看手牌的出口**（票 93：非应答者也画两行位置、空选择面的"只能放弃"明话；「提交」只在 `picks` 凑满 `spec.total` 下限时可点，左端取消只清本地选择、右端「放弃」送候选里的 `Pass`）；**未知区域弹窗的两档**（`cap===1` 一击即提交 / `cap>1` 点选 +「提交」，判据 `isUnknownZoneMultipick`；票 06）。 |
| `options.js` | 选项按钮组、选择面槽位、「提供一张 B」、Action 文案与右端动作下标；**计数式分配面**（`renderDistribution`：逐座位计数 + 「重置」，本地计数住 `state.distribution`；huo-shenzhouyu-mobile 票 05）；**未知区域弹窗的两档**（`cap===1` 一击即提交 / `cap>1` 点选 +「提交」，判据 `isUnknownZoneMultipick`；票 06）。 |
| `reveal.js` | 亮牌模板（五谷 / 遗计复用）与目标条；**按花色取牌**（涉猎：一行式、同花色置灰、2.5 秒结果态 `renderSuitResult`）；**两行观看手牌**（票 93：`renderWatchRows` = 上行目标手牌 / 下行自己手牌，`cardFaceNode`（连 `card.tags`）画、同花色置灰（两行一起算、复用 `suitSlotState` 的 `spec.max_per_suit`）、点牌切换 `picks`（升序）；空选择面画 `revealed` 的整副手牌、只留「放弃」）；**带观看授予的选项步**（韬·刘备【义起】：`SELECT_OPTION` + `revealed` ⇒ 选择型弹窗里牌面只读、3 个类型按钮同窗，`options.js:renderOptionButtons` 画牌）；判据仍住 `modal.js`。 |
| `pindian.js` | 拼点弹窗（huo-taishici-xunyu 票 04）：`pindian` delta → **全场可见的只读结果面**（左 1 个固定槽 = 发起者的拼点牌，右 1..N 行 = 各被拼点者的牌 + 赢 / 没赢 / 都没赢）。判据只看 `delta_type`（不认技能 id / 座位）；`applyPindianDelta(record)` 由 `session.js:stepPlayback` 每条记录调一次，`PINDIAN_HOLD_MS = 3000` 排一次计时器；不进 `stepDialogKind`、不挡牌桌、没有提交按钮。**生效点数**（tao-caocao 票 03）：delta 带 `ranks` 时每个玩家块多画一行 `.pindian-point`（「点数 N」）——牌面点数被技能调过之后两者不同，前端**不比较、不自己加**，数字原样来自服务端；旧记录没有 `ranks` ⇒ 一行都不画。 |
| `modal.js` | 询问弹窗框架与武将详情壳：`selectionEnabled`、`stepDialogKind`、`DIALOG_KINDS`（含涉猎的 `suit`、计数式分配的 `distribution`、两行观看手牌的 `watchTwoRows`）、`dialogDisablesPass`（亮牌步带 `Pass` 时可放弃）；**两行观看手牌的判据**（票 93：`isWatchTwoRowsStep` 读载荷形状——`SELECT_CARDS` ∧ `spec.max_per_suit` ∧ 槽位恰好跨两个座位且含询问者；空选择面只认服务端 `dialog` 标记，`syncDialog` 对非应答者也开窗）；**展示武将牌的选项步**（票 08：`.hero-card` 正文段 = 副标题「第 k/m 张」+ 武将块 + 带描述的选项行，复用 `choice` 外壳，不新增第七种 kind；判据 `isShowHeroCardStep` 只看载荷 `hero_card`）；**同一时机的激活面**（`isActivationFace`：候选里 ≥2 条 `ActivateSkill` ⇒ 复用 `choice` 外壳、标题「选一条发动」；进入某条技能的清单后按本地态让位给牌桌表面）；**未知区域弹窗的两档**（`cap===1` 一击即提交 / `cap>1` 点选 +「提交」，判据 `isUnknownZoneMultipick`；票 06）。 |
| `hero-detail.js` | 武将详情正文（技能清单）与开合绑定。 |
| `arrange.js` | 排列面双行布局（观星自由切分 / 恂恂固定切分）：移动判据（插入 / 固定切分跨行互换）、拖拽落点（指针过牌面中点即"之后"）与落点预览（占位格让道 / 交换高亮）、提交载荷。 |
| `answer.js` | 倒计时三色、`buildAction`、`submitAction`（`POST …/answer`）、`clearSelection`；三个前端代答例外（观星 `ARRANGE` / 涉猎 `suit` / 计数式分配 `DISTRIBUTE` 到点提交当前选择）；涉猎提交后的 2.5 秒结果态。 |
| `log.js` | 事件流三档（简易 = 服务端叙事 / 完整重点 / 完整全部）。 |
| `replay.js` | 回放页：录像列表、按记录重建座位全区、询问面板与播放控制。 |
| `anim.js` | 飞行层（U4-01）：`(reason, to_zone, from_zone)` 分类表、起 / 落点锚点、`anim.ini` 时长表载入、飞行器生成；阶段 / 回合飘字（U4-04）的 `#t-anim-phase` 层与 `animPlanPhase` 也在这里；`durationOf(record)` 是节奏公式（票 02）的只读时长口。 |
| `main.js` | `wire()` 事件绑定、回放步长按钮、启动；必须是最后一份脚本。 |

`index.html` 是 DOM 骨架 + 覆盖层（`#t-target-lines` / `#t-skill-floats` / `#t-anim-flights`）+ 脚本顺序；
弃牌堆面板的模态挂载点 `#t-discard-modal`（含自己的 `#t-discard-backdrop`，正文由 `table.js:renderDiscardPanel` 画）
也在这里；`style.css` 是样式与设计单位，动效时长 / 尺寸的机械断言在 `tests/test_web_static.py`。

## 舞台 / live view / 轮询 / 动效入口

| 入口 | 文件 | 用途 |
|---|---|---|
| `applyEffectDelta(record, …)` | `session.js` | 一条记录 → 舞台项：处理中 / 展示 / 弃牌的落位、换组与淡出锚点。 |
| `applyRevealDelta(record, …)` | `session.js` | `card_reveal` / `pending.reveal` → 亮牌展示项。 |
| `mergeEffectCards(cards)` | `session.js` | 把 `snapshot.processing` 合并回舞台，保留承载帧 kind 与来源区。 |
| `stageLane(item, now)` | `session.js` | **纯函数**寿命判定：`visible` / `fading` / `gone`；测试直接喂 `now`。 |
| `tableViewForRender()` | `table.js` | 回放期读逐事件派生的 `state.liveView`（`pending` 收起），其余读权威视图。 |
| `stepPlayback(immediate)` | `session.js` | 回放一条记录：日志 → 效果 / 亮牌 → `applyLiveDelta` → 重绘 → 动效 → 技能事件。 |
| `applyLiveDelta(record, view)` | `session.js` | 把**可派生**的可见变化写进 live view（体力 / 手牌数 / 装备 / 阶段 / 标记数量 `marker_change` …）。 |
| `poll()` | `session.js` | 2s 静默轮询；idle 只更新时钟，不重绘、不清选择；失败静默。 |
| `drawTargetLines(actor, targets, now)` | `effects.js` | 每个目标一条金色 SVG 线（跳过自己 / 阵亡）；端点只读一次布局。 |
| `playSkillEvent(event, now)` | `effects.js` | 技能名飘字 + 白光；`targets` 非空才画线。**不判断"哪些技能该有特效"**：`event` 由服务端下发（见下一条），前端只画。 |
| `fireSkillEventsThrough(seq)` | `effects.js` | 回放到某条记录时释放 `state.skillEvents` 队列。 |
| `skill_events_after(records, after, state, activations)` | `projection.py` | **技能特效的唯一判据**（U3-04 / D8.2 + lei 票 06）：`{seq, seat, skill_id, name, targets}` 由两条公共事实合成——① 未裁剪记录里**已接受且点名了技能**的动作（`ActivateSkill` / `SubstituteJudgeCard`）；② 引擎的发动缓冲 `Engine.skill_activations`（**记录面没有技能归属**的锁定技与转化技）。跑 `bash tests/smoke-web.sh`，用例在 `tests/support/ui_smoke.mjs` 的 U3-04*（U3-04k 锁定技 / U3-04l 转化技）。 |
| `syncTransientEffects()` | `effects.js` | 座位重建后补回仍在播的高亮 / 体力闪 / 白光；顺手收掉过期目标线。 |
| `animPlanFlight(record)` | `anim.js` | 飞行层**起飞前**：查分类表、不飞 / 未登记判定、量一次源矩形（`stepPlayback` 在 `applyEffectDelta` 之后、重绘之前调）。 |
| `animLaunchFlight(plan)` | `anim.js` | 飞行层**重绘之后**：量落点并同步起飞；起 / 落点缺失静默跳过（层上留 `data-anim-missing`）。 |
| `animPlanPhase(record)` | `anim.js` | 阶段 / 回合飘字（U4-04）：`timing` 记录 → 判定 / 装备区上方的「XX阶段开始 / 轮到 X」；`durationOf` 对非牌移动恒 0，不占步长。 |
| `renderDiscardPanel()` | `table.js` | 弃牌堆面板（U4-05）正文：从 `tableViewForRender()` 的同一份视图按时间倒序画牌面，`renderTable` 末尾顺带刷新。 |
| `applyPindianDelta(record)` | `pindian.js` | 拼点弹窗（票 04）：一条 `pindian` delta → 弹窗开 3 秒；只读、全场可见（AI 打 AI 也弹），不读 `state.dialog`。 |
| `openDiscardPanel()` | `modal.js` | 顶栏「弃牌 N」的点击入口：开 `#t-discard-modal`（复用模态壳，点背景 / Esc 关）。 |

## 页面层

- **开局页** `#page-lobby`：`lobby.js` + `core.js` 的目录请求；建局成功后 `applyView`。
- **牌桌** `#page-table`：`session.js` 驱动数据，`table.js` / `seat-view.js` / `card-view.js`
  画表体，`pending.js` / `options.js` / `modal.js` / `reveal.js` 画询问面。
- **回放页** `#page-replay`：`replay.js` 单独重建座位全区；**不经过** `stepPlayback`，也不播
  目标线 / 技能名 / 白光 / 高亮 / 体力闪。

## 测试 / 烟测入口

- `tests/support/ui_smoke.mjs`：`node:vm` + 最小 DOM 垫片，按 `index.html` 顺序跑**真脚本**，
  喂真服务端响应与合成载荷（展示武将牌的 `heroCardPending` 与票 07 的投影载荷同形）。
  垫片的 `El.children` 是 **HTMLCollection 形状的活视图**
  （`length` / 下标 / `item` / 迭代，无数组方法）——测试遍历一律 `Array.from(...)` 或展开；
  集合断言用 `setEquals`，纯函数断言经 `ui.stageLane` 喂 `now`。U4-01 起垫片另有
  **确定性 `getBoundingClientRect`** 与登记式 `animate`（飞行层只读"矩形存在 / 动画参数"，
  不读真实布局与时钟）。
- `tests/support/ui_bridge.js`：把前端入口按名字挂到 `globalThis.__bridge`（烟测里叫 `ui`），
  缺项容错；`applyView` / `submitAction` 默认先把回放跑完，要看中间态用
  `applyViewRaw` / `submitActionRaw` 配 `stepPlayback`。
- `tests/smoke-web.sh`：临时服务 + `ui_smoke.mjs` + 收尾（实测 60–70 s）。**给人用的快回路**，
  不在 `tests/run_all.sh` 里。
- `tests/test_web_static.py`：静态清单（脚本顺序 / strict）、身份配比副本、CSS 设计单位，
  以及 `children` 数组方法禁令。
- `tests/test_web.py`：接入层纯函数与真会话 / HTTP 用例（不 `serve_forever`）。
- `tests/test_web.sh`：端到端门禁——真服务、完整 8 人局、超时代答、静态资源 `node --check`，
  并跑一遍 `ui_smoke.mjs`。

## 实机验收

改动 DOM API 的 UI 票在 `resolved` 前走一遍真实浏览器（`bsk`）：配方见
`docs/agents/acceptance-web.md`；评审的判断题口径见 `CODING_STANDARDS.md` §4 / §6。
