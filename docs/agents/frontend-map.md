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
| `session.js` | 会话生命周期与派生：`applyView` / `commitView`、事件回放 `stepPlayback`、舞台三组、live view、轮询（见下节）。**回放暂停**（damage-fx-natures 票 07）：`playbackHoldProbes` 登记表 + `registerPlaybackHold(name, probe)` / `playbackHoldReason()`，排下一步只走 `schedulePlaybackStep(base)`（`stepPlayback` 的收尾与 `applyView` 的新批首步都归这个口）——演出在播时按 `PLAYBACK_HOLD_POLL_MS`（120ms）轮询判据、冻结记录推进。**没有兜底上限、暂停路径不读墙钟**（票 09 / 用户 2026-11-10："把这个上限取消，3 档、4+ 档伤害时动画暂停，不允许动画期间进行任何事件"）——判据为真就一直等，故判据只许读**会自然清空**的状态。读数 `<html data-playback-hold>` = 原因串。**排播侧不认识任何具体演出**（不读 `damagePlays`、不认档位）：以后 回复体力 3 档 之类的重演出各自登记一条即可。 |
| `candidates.js` | 候选索引的**只读**查询：`cardEntry` / `cardSelectable` / `isLegalCombo` / `autoCombo`；不重建索引。**自己区域的直选判据** `isOwnZonePick`（选自己的手牌/装备 ⇒ 牌面直选、不铺左下角面板；票 14）也在这里。 |
| `card-view.js` | 卡面绘制（花色 / 点数 / 牌名 / 类型行 / 悬停全文），纯展示；**跟随鼠标的卡牌预览**（`showCardPreview` / `moveCardPreview` / `hideCardPreview`，层 `#t-card-preview`；用户 2026-11-07：装备 / 判定槽与武将牌上的牌区共用这一口，贴边翻转）。 |
| `card-face.js` | 卡面状态的纯函数：选中 / 置灰 / 槽位、`isProvide` / `provideSlotIndex`。 |
| `seat-view.js` | 座位卡的节点形状：武将块、判定 / 装备槽、状态类与悬停全文；**判定 / 装备槽的简略牌面**（`miniSlotNode`：牌名 + 花色点数、转化牌右上角「转」；槽是否接点击由 `sideBlockRows` 的三条判据决定——自己的「提供一张 B」/「选牌面」/ 预发动 / **手选面里候选索引有的牌**（`equipCardPick`，改判窗口的装备区牌因此可点）；装备槽**有牌时不写**武器 / 防具 / 攻马 / 防马，判定槽常写；悬停走 `#t-card-preview` 的常规卡面），**武将牌上的牌区**（`heroZoneRows`，标签带张数与**悬停预览**——2026-11-07 起点击不再展开；非宿主悬停只给「牌面仅宿主可见」）与**标记行**（`markerRows`，与牌区同骨架异色、文本 = 名称×数量、悬停 = 来源 +（效果说明））都在 `fillHeroZones` 里铺。**护甲段**（W7 的第二条体力轴）：`hpBarNode` 读 `players[].armor`（`PlayerPublic` → `seat_display` 补下），`> 0` 时在血条右侧接 `.armor-segs` / `.armor-seg`；`0` 时**一个节点都不加**（DOM 与引入护甲之前逐字节同形，自己座位经 `table.js` 搬 `hp.children` 自动带上）。**被废除（封印）的槽**由 `sideBlockRows` 读投影字段 `players[].sealed_zones`（`PlayerPublic` 下发；**票 03 起是槽 id**：判定 `judge_1..3`（`judgeSlotLayout` 的 `sealKey`，写成 `data-slot-key`；`data-slot="judge"` 仍是 `anim.js` 的落位锚点）+ 装备四槽）**逐槽**叠红色禁止角标（`markSealedZone` → `.zone-sealed` + `.zone-ban`，视觉照 `.skill.suppressed` 的 `.skill-ban`；**每个被废除的槽各画一个**，判定行 / 装备格本身不再画，**区域形状不变**；lukang-mobile 票 03 取代 lei 票 05 的"整区一个"）。 |
| `table.js` | 环桌布局与表体渲染：`tableViewForRender`、`renderTable`、`renderProcessing`、`renderSkills`；座位交互态 `seatInteraction`（可指定 / 已选 / 必选，环上与底部面板共用）：手选目标、预发动座位步、**单选座位面**（`SELECT_OPTION` + `option_seats`），以及 **W1 的座位多选面**（`SELECT_OPTIONS` + `option_seats` 非空 ⇒ 池里的座位画可点、已选画 `tsel`；"能不能新选"的唯一判据 = `seatMultiPickable`，点击口也调它；已选 / 置灰集合每次渲染只算一份）。 |
| `effects.js` | 动效层：目标线、行动者高亮（`.acting`）、体力闪、技能名飘字 / 白光；寿命判定是纯函数。**伤害演出与音效都不在这份文件里**（票 08 / 09 已抽走）：它只提供三个「读真实布局」的小工具给 `damage-fx.js` 借（`rectOf` / `heroBlockFor` / `descendantWithClass`，`stageNow` 来自 `session.js`），反向**零调用**——版式重检由 `table.js:renderTable` 显式调 `damageDropStalePlays()`、换局清场由 `session.js:commitView` 显式调 `clearDamageFx()`（票 09 把这两个反向钩子挪到了调用方）。 |
| `damage-fx.js` | 伤害演出的**排播 + 座位几何 + 画布生命周期**（damage-fx 票 09 从 `effects.js` 抽出）：`state.damageEvents` 队列 + `fireDamageEventsThrough(seq)`（与技能事件同一条 seq 游标；`null` / `NORMAL` / `THUNDER` / 未登记属性都播，只有还没接的 `FIRE` 跳过）→ 排播闸门 `pumpDamageQueue`：**重演出（3 / 4 档，也就是会暂停回放的那些）串行且独占牌桌**——一条播满自己的 `total` 才接下一条，摘层时由 `finishDamagePlay` 续泵（票 08，用户口径"我的演出彻底结束后才继续下一个事件"）；**轻演出（1 / 2 档）**仍相邻错开 120ms、并按**该属性自己的 cap**（`damageFxCapFor(nature)`：票 02 起按属性读 `anim.ini` 的并发表 `damage_fx_concurrency` / `..._thunder_concurrency` / `..._fire_concurrency`，1~8、缺省 3，钳在读者侧）并发；cap 是**该家族的排队上限**（在播 + 排队，票 08 起判在入队处 `fireDamageEventsThrough` → `damageFxPendingOf`，超出丢掉——串行后"同时在播"恒为 1，按在播判 cap 等于失效）→ `startDamagePlay` 用 `heroBlockFor` 量两张武将块的矩形（`source == null` 时剑光把来源挪到画面外；雷电则交 `src: null` 走天降）、取 `.hp-bar`（自己的座位 `#t-self-hp`）的下沿当飘字硬线，按 `damageFxRendererFor(nature)` 选画面模块（剑 / 雷 / 火）交给它的 `create(…, {profile: "game", bare: true, amount, stage, view, numberKeepOut})`；`play.nature` 是下游唯一判据，在播上限走唯一的 cap 口 `damageFxCapFor(nature)`；**画布生命周期**也在这一层（`#t-damage-fx` 里只有一张可见 canvas、离屏舞台按槽复用且**数量随在播数增长**（票 02 起不再假定 3 张）、到 `duration` 摘层、版式变了（两张牌任一矩形变）作废、换局清队列）；无属性 3 / 4 点档给 `#table-shell` 挂灰阶滤镜窗口，火 / 雷改挂 `.fx-flash` 着色子层（`damageFxFlashSpec`，**不是** `filter`）。**依赖方向单向**：借 `effects.js` 的 `rectOf` / `heroBlockFor` / `descendantWithClass` 与 `session.js` 的 `stageNow`（调用时解析）⇒ 清单里排在 `effects.js` **之后**、`sfx-ui.js` **之前**（音效读它的纯读口）。**回放暂停**（damage-fx-natures 票 07）：3 / 4 档在播时文件末尾向 `session.js` 的登记表放一条只读判据 `damageFxPlaybackHold()`（`variant >= DAMAGE_FX_HOLD_MIN_VARIANT`，家族无关；返回原因串 `damage:<最高在播档>`），于是演出窗口里记录流冻住——**只借同一个方向的口**（本文件 → session.js 的 `registerPlaybackHold`），排播侧仍不读 `damagePlays`、不认档位。层上读数 `#t-damage-fx[data-damage-fx-hold]`（`"1"` / 空）。 |
| `preactivation.js` | 预发动清单、转化技来源模式、清单到 Action 的组装；**步类型**只读服务端下发的 `Step.kind`（`preactivationStepKind`：选牌 / 座位 / 选项是规则级事实，不按 `pool[0].options[0]` 猜——目标由牌名决定的空行排最前时会猜错；yijiang-2014-round2 评审 Fix 2）；选项步的**置灰不可选**（`preactivationDisabled` 读 `StepPool.disabled`，画 `.dis` + 不接 click；票 04 §3）；多来源步的**合法来源组合**（`preactivationGroups` 读 `Step.groups`：卡面 / 「下一步」/ 「提交」三处同一判据，凑不成一组的来源既选不出也交不出；huo-mobile-batch 票 10）。 |
| `interaction.js` | 点选：卡面芯片、点牌设焦点、点座位选目标、弃牌点选；**座位多选面的点选**（W1：`toggleSeatMultiPick` —— 点一下选中、再点取消，已选集合住 `state.selectedTargets`，因此重绘 / 轮询不丢；置灰与封顶判据走 `table.js:seatMultiPickable`，不在这里另抄一份）。 |
| `pending.js` | 询问按种类分派到专用面、提示条右端动作、取消 / 不支持面 fail-closed；涉猎的结果态门闸（`state.suitResult`）；**同一时机的激活面**（`renderActivationFace`：一条 `SKILL_ACTIVATION` 有 ≥2 条 `ActivateSkill` 候选时铺"选一条发动"的逐技能行 + 末条「都不发动」，退出判据是候选里的 `Pass`；选中哪条就按 `activation_preactivations[skill_id]` 进它自己的清单，没有清单则一击即提交；`skill-face` 只在这个形状出现，单候选面仍走直提态）；**两行观看手牌的出口**（票 93：非应答者也画两行位置、空选择面的"只能放弃"明话；「提交」只在 `picks` 凑满 `spec.total` 下限时可点，左端取消只清本地选择、右端「放弃」送候选里的 `Pass`）；**未知区域弹窗的两档**（`cap===1` 一击即提交 / `cap>1` 点选 +「提交」，判据 `isUnknownZoneMultipick`；票 06）；**座位多选面**（W1：`renderKind` 的 `seat-multi` 档 —— `SELECT_OPTIONS` ∧ `option_seats` 非空 ⇒ 牌桌上点多个座位、一次提交，本地集合 / 区间 / 置灰 / 提交可点态分别住 `seatMultiChosenSeats` / `seatMultiFloor` / `seatMultiCap` / `seatMultiHint`（置灰集合 = `seatMultiDisabledSet`）；排在同类的 `multi` 档**之前**，绝不能落到 `multiChoice` 弹窗）。 |
| `options.js` | 选项按钮组、选择面槽位、「提供一张 B」、Action 文案与右端动作下标；**多选勾选面**（`renderMultiSelect`：逐项勾选、置灰项 `.dis` 且点不动，本地勾选住 `state.multiSelect`，提交由 `answer.js:multiSelectAction` 拼出候选里那条 `SelectOptions`；jie-feng-batch2 票 01a；**座位**选项不走这一面，见 W1 的 `seat-multi`）；**计数式分配面**（`renderDistribution`：逐座位计数 + 「重置」，本地计数住 `state.distribution`；huo-shenzhouyu-mobile 票 05）；**未知区域弹窗的两档**（`cap===1` 一击即提交 / `cap>1` 点选 +「提交」，判据 `isUnknownZoneMultipick`；票 06）。 |
| `reveal.js` | 亮牌模板（五谷 / 遗计复用）与目标条；**按花色取牌**（涉猎：一行式、同花色置灰、2.5 秒结果态 `renderSuitResult`）；**两行观看手牌**（票 93：`renderWatchRows` = 上行目标手牌 / 下行自己手牌，`cardFaceNode`（连 `card.tags`）画、同花色置灰（两行一起算、复用 `suitSlotState` 的 `spec.max_per_suit`）、点牌切换 `picks`（升序）；空选择面画 `revealed` 的整副手牌、只留「放弃」）；**带观看授予的选项步**（韬·刘备【义起】：`SELECT_OPTION` + `revealed` ⇒ 选择型弹窗里牌面只读、3 个类型按钮同窗，`options.js:renderOptionButtons` 画牌）；判据仍住 `modal.js`。 |
| `pindian.js` | 拼点弹窗（huo-taishici-xunyu 票 04）：`pindian` delta → **全场可见的只读结果面**（左 1 个固定槽 = 发起者的拼点牌，右 1..N 行 = 各被拼点者的牌 + 赢 / 没赢 / 都没赢）。判据只看 `delta_type`（不认技能 id / 座位）；`applyPindianDelta(record)` 由 `session.js:stepPlayback` 每条记录调一次，`PINDIAN_HOLD_MS = 3000` 排一次计时器；不进 `stepDialogKind`、不挡牌桌、没有提交按钮。**生效点数**（tao-caocao 票 03）：delta 带 `ranks` 时每个玩家块多画一行 `.pindian-point`（「点数 N」）——牌面点数被技能调过之后两者不同，前端**不比较、不自己加**，数字原样来自服务端；旧记录没有 `ranks` ⇒ 一行都不画。 |
| `modal.js` | 询问弹窗框架与武将详情壳：`selectionEnabled`、`stepDialogKind`、`DIALOG_KINDS`（含涉猎的 `suit`、计数式分配的 `distribution`、多选的 `multiChoice`（jie-feng-batch2 票 01a：判据只看 `pending.kind === "SELECT_OPTIONS"`，但**排除座位多选面** —— `isSeatMultiStep` 为真时返回 `null`（牌桌表面），见 W1）、两行观看手牌的 `watchTwoRows`）、`dialogDisablesPass`（亮牌步带 `Pass` 时可放弃）；**两行观看手牌的判据**（票 93：`isWatchTwoRowsStep` 读载荷形状——`SELECT_CARDS` ∧ `spec.max_per_suit` ∧ 槽位恰好跨两个座位且含询问者；空选择面只认服务端 `dialog` 标记，`syncDialog` 对非应答者也开窗）；**展示武将牌的选项步**（票 08：`.hero-card` 正文段 = 副标题「第 k/m 张」+ 武将块 + 带描述的选项行，复用 `choice` 外壳，不新增第七种 kind；判据 `isShowHeroCardStep` 只看载荷 `hero_card`）；**同一时机的激活面**（`isActivationFace`：候选里 ≥2 条 `ActivateSkill` ⇒ 复用 `choice` 外壳、标题「选一条发动」；进入某条技能的清单后按本地态让位给牌桌表面）；**未知区域弹窗的两档**（`cap===1` 一击即提交 / `cap>1` 点选 +「提交」，判据 `isUnknownZoneMultipick`；票 06）。 |
| `hero-detail.js` | 武将详情正文（技能清单）与开合绑定。 |
| `arrange.js` | 排列面双行布局（观星自由切分 / 恂恂固定切分）：移动判据（插入 / 固定切分跨行互换）、拖拽落点（指针过牌面中点即"之后"）与落点预览（占位格让道 / 交换高亮）、提交载荷。 |
| `answer.js` | 倒计时三色、`buildAction`、`submitAction`（`POST …/answer`）、`clearSelection`；多选面共用的查找实现 `selectOptionsAction`（勾选 `multiSelectAction` / 座位 `seatMultiAction`，右端「放弃」仍是候选里那条 `Pass`）；三个前端代答例外（观星 `ARRANGE` / 涉猎 `suit` / 计数式分配 `DISTRIBUTE` 到点提交当前选择）；涉猎提交后的 2.5 秒结果态。 |
| `log.js` | 事件流三档（简易 = 服务端叙事 / 完整重点 / 完整全部）。 |
| `replay.js` | 回放页：录像列表、按记录重建座位全区（含 `armor_change` 的护甲值——从 0 起逐条 `after` 重建，`> 0` 才在座位行里写「护甲 N」）、询问面板与播放控制。 |
| `anim.js` | 飞行层（U4-01）：`(reason, to_zone, from_zone)` 分类表、起 / 落点锚点、`anim.ini` 时长表载入、飞行器生成；阶段 / 回合飘字（U4-04）的 `#t-anim-phase` 层与 `animPlanPhase` 也在这里；`durationOf(record)` 是节奏公式（票 02）的只读时长口。 |
| `fx-core.js` | 无属性伤害演出的**画面**：`SgsDamageFX`（纯 Canvas 2D，零依赖、零请求、不接管 DOM）。四档 `VARIANTS`（伤害值 / 剑光粗细配色 / 刀痕 / 屏闪 / 劈屏）；两档时间轴——`export`（素材库 12s，`?still` / `?export` / `renderAt(t)` 走这条）与 `game`（牌桌：19 个 `damage_fx_*` 键来自 `anim.ini`，缺键回退 `DEFAULT_TIMING`，四条钳制含 `total ≤ MAX_TOTAL_MS = 2200`；另导出由那张表的键生成的 `TIMING_FIELDS` / `TIMING_KEYS`——六个时长字段名的**唯一真源**，`damage-fx.js` 的读取面与 `test_web_static` 都从它派生，票 10）。入口 `create(canvas, {variant, profile: "game", timing, amount, stage, view, bare, numberKeepOut})` 与 `gameTiming(variant, raw)`；牌桌档 `bare: true` 只出剑光 / 刀痕 / 墨点 / 飘字（宣纸底与样例武将牌不画）；命中白闪的档位闸门是 `!bare || variant.dmg > 1`——牌桌 1 点档**没有**整屏白闪（票 06），导出档照旧，且它与 3 / 4 点档的**灰阶窗口**是两套效果。 |
| `fx-thunder.js` | **雷电**伤害演出的**画面**：`SgsThunderFX`（纯 Canvas 2D，零依赖、零请求、不接管 DOM）。与 `fx-core.js` 同形：`create(canvas, {variant, profile, timing, amount, stage, view, bare, numberKeepOut})` / `gameTiming(variant, raw)` / `DEFAULT_TIMING` / `TIMING_FIELDS` / `TIMING_KEYS`。`profile: "export"` 照素材页 5~6 秒链（node 探针逐帧导静帧），`"game"` 读 `anim.ini` 的 `damage_fx_thunder_<档>_<字段>`（20 键；`flash` 取代剑光的 `split`，同四条钳制 + `total ≤ 2200`）。`stage.src === null` ⇒ **无来源天降**（雷从画面上方进入、不画来源雷印）；`bare: true` 只出特效、不画宣纸底与样例牌，整屏白闪**不在画布内**画（由排播层挂 `.fx-flash` 着色子层）。 |
| `fx-fire.js` | **火焰**伤害演出的**画面**：`SgsFireFX`（纯 Canvas 2D，零依赖、零请求、不接管 DOM）。与 `fx-thunder.js` 逐字同形：`create(canvas, {variant, profile, timing, amount, stage, view, bare, numberKeepOut})` / `gameTiming(variant, raw)` / `DEFAULT_TIMING` / `TIMING_FIELDS` / `TIMING_KEYS`。`profile: "export"` 照素材页 4.9~6.2 秒链（火印 → 火羽连珠 → 火星 / 焦痕 → 3 档爆燃 / 4 档天陨；node 探针逐帧导静帧），`"game"` 读 `anim.ini` 的 `damage_fx_fire_<档>_<字段>`（20 键；`flash` 取代剑光的 `split`，同四条钳制 + `total ≤ 2200`）。牌桌档不是另画一套：`gameTiming` 把素材页的整条链按锚点（第一箭命中 / 最后一击 / 爆炸 / 飘字 / 焦痕淡出 / 收尾）线性压到 ini 的秒数上并返回 `warp`，`draw(t)` 先把演出自己的秒数映射回素材页秒数再画（spec「等比压下来」的机械化版本）。`src === null` ⇒ 沿用**剑光**先例（来源整块在画面外 200 单位），不做雷的「天降」——库里三个火焰生产者都带来源；`bare: true` 只出特效，整屏暖白闪**不在画布内**画（由排播层挂 `.fx-flash-fire` 着色子层）。 |
| `sfx.js` | 无属性伤害演出的**音效**：`SgsSfx`（Web Audio 现场合成，仓库里没有音频文件）。文件顶部的 `VOICE` 调音台是**唯一**调音处，六个声音 `riser` / `shing` / `whoosh` / `impact` / `strobe` / `split` 各一个独立函数；`create(timeline)` 返回的播放器由 `sfx-ui.js` 按演出驱动（`start` / `tick(t, v)` / `stop` / `mute`），`renderOffline` 只给 `?audiotest=1` 出电平。 |
| `sfx-thunder.js` | **雷电**伤害演出的**音效**：`SgsThunderSfx`（Web Audio 现场合成，仓库里没有音频文件）。接口与 `SgsSfx` 同形——`create(timeline)` 返回 `{start, stop, mute, tick(t, voice)}`，`tick` 吃演出自己的秒数、cue 时刻读声道里那份**可变**时间轴（CHARGE / LAUNCH / IMPACT / SKY_AT / EXPLODE）；几条声道共用一个模块级 `AudioContext` + 压缩器（素材页是单场播放器，同时三条雷电只响最后一条）。`renderOffline` 给 `?audiotest=1` / 导出。 |
| `sfx-fire.js` | **火焰**伤害演出的**音效**：`SgsFireSfx`（Web Audio 现场合成，仓库里没有音频文件）。接口与 `SgsThunderSfx` 同形——`create(timeline)` 返回 `{start, stop, mute, tick(t, voice)}`，`tick` 吃演出自己的秒数、cue 时刻读声道里那份**可变**时间轴。火的 cue 是**逐箭**摊开的：箭数 / 间距 / 飞行时长读时间轴的 `COUNT` / `GAP` / `FLIGHT`（画面模块的 `gameTiming` 算好的那一份），3 档加爆燃、4 档加火云低频 / 云内预闪 / 流星呼啸 / 大爆炸 + 冲击波；几条声道共用一个模块级 `AudioContext` + 压缩器。`renderOffline` 给 `?audiotest=1` / 导出。 |
| `sfx-ui.js` | 伤害演出的**音效 UI**（damage-fx 票 08 从 `effects.js` 抽出；票 01 改成按属性分池、票 02 起池会随 cap 增长）：顶栏「♪ 音效 开 / 关」的状态机（**缺省静音** `localStorage['sgs.sfx']` 只认 `'on'`、关着时连 `AudioContext` 都不建；**一次性手势解锁**：**初建**的声道（`damageSfxEnsureLanes`）只发生在真实 `pointerdown` 的同一个 tick，**已加载的**音效模块各建一套；票 02 的**补建**是开演时按 `need` 增长，不走手势——Safari 上新声道可能要到下一次手势才出声，代价写在 `damageSfxEnsureLanes` 的注记里）；声道池**按音效模块分组**（`{SgsSfx: [...], SgsThunderSfx: [...]}`，起始池长 = 该属性的 `damageFxCapFor`，惰性建——没有 `SgsFireSfx` 就跳过（票 03 起三份都在，一次手势就是三族各 cap 条）；槽号是跨家族的，超出池长时 `damageSfxStart` **就地补建**而不是静默无声）；每帧 `damageSfxTick(play, seconds)` 把**演出自己的** `seconds`（就是喂 `fx.draw` 的那个数）与该档整份时间轴（含火的 `COUNT` / `GAP` 逐箭节奏）交给该模块的 `tick(t, v)`；`?audiotest=1` 的离线四档电平（写 `<html data-audio>`，没有 `OfflineAudioContext` 时 `unavailable`）。**单向缝**：只接 `play` 对象（`nature` / `variant` / `fx.timing` / `fx.timeline` / `slot` / `flash`）、只收自己绑过的声道（`damageSfxBound`），**不读**伤害排播层的在播表；反方向只读三个**纯读**口：`DAMAGE_FX_MAX_VARIANT`（档位号钳到 1..4）、`damageFxCapFor(nature)`（声道池长 = 该属性的在播上限）与 `damageFxNatureKind`（属性分类器）——都不读运行状态、也不反向驱动排播，全部在 `damage-fx.js` 里、调用时解析 ⇒ 页面清单里排在 `damage-fx.js` 之后、`main.js` 之前。 |
| `main.js` | `wire()` 事件绑定、回放步长按钮、启动；必须是最后一份脚本。 |

`index.html` 是 DOM 骨架 + 覆盖层（`#t-target-lines` / `#t-skill-floats` / `#t-anim-flights` /
`#t-damage-fx` / `#t-card-preview`）+ 脚本顺序；
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
| `stepPlayback(immediate)` | `session.js` | 回放一条记录：日志 → 效果 / 亮牌 → `applyLiveDelta` → 重绘 → 动效 → 技能事件；收尾的下一步只经 `schedulePlaybackStep(delayOf(record))`（`immediate` 侧不过暂停门）。**暂停只停记录推进，不停演出自身的帧与声**。 |
| `schedulePlaybackStep(base)` | `session.js` | 排下一步回放（票 07）：先等 `base`，**唤醒时**才问 `playbackHoldReason()`；判据非空 ⇒ 不推进记录，改排 `PLAYBACK_HOLD_POLL_MS` 轮询，直到放空。**没有上限**（票 09）：判据一直为真就一直等（不许"等够久就放行"），故暂停路径里没有墙钟。`immediate` 侧不经过它。 |
| `registerPlaybackHold(name, probe)` | `session.js` | **回放暂停的通用口**（票 07）：登记一条只读判据 `() => 原因串`（空串 = 不暂停），同名就地替换。伤害家族在 `damage-fx.js` 文件末尾登记一条（3 / 4 档在播）；以后 回复体力 3 档 之类的重演出各登记一条即可——排播侧不认来源。 |
| `applyLiveDelta(record, view)` | `session.js` | 把**可派生**的可见变化写进 live view（体力 / 护甲 `armor_change` / 手牌数 / 装备 / 阶段 / 标记数量 `marker_change` …）。 |
| `poll()` | `session.js` | 2s 静默轮询；idle 只更新时钟，不重绘、不清选择；失败静默。`state.playback` 非空时**本来就不发**——回放暂停期间服务端超时代答不会插进窗口（票 07 的已知取舍）。 |
| `drawTargetLines(actor, targets, now)` | `effects.js` | 每个目标一条金色 SVG 线（跳过自己 / 阵亡）；端点只读一次布局。 |
| `playSkillEvent(event, now)` | `effects.js` | 技能名飘字 + 白光；`targets` 非空才画线。**不判断"哪些技能该有特效"**：`event` 由服务端下发（见下一条），前端只画。 |
| `fireSkillEventsThrough(seq)` | `effects.js` | 回放到某条记录时释放 `state.skillEvents` 队列。 |
| `fireDamageEventsThrough(seq)` | `damage-fx.js` | 伤害演出与 `fireSkillEventsThrough` **同一条 seq 游标**（`stepPlayback` 里紧挨它的下一行）：把 `state.damageEvents` 里到点的事件交给排播闸门 `pumpDamageQueue`（重演出 3 / 4 档串行、独占牌桌；轻演出 1 / 2 档相邻错开 120ms、按家族 cap 并发）。**入队处**按"该家族的排队上限"（`damageFxPendingOf` = 在播 + 排队）丢掉超出的条目（票 08）。属性口径走 `damageFxNaturePlayable`：**`null` / 缺字段 = 无属性（播）**；票 01 起 `THUNDER`（交 `SgsThunderFX`）、票 03 起 `FIRE`（交 `SgsFireFX`）也播，三个家族全开——三层文档都写 `None` ≡ 无属性（`game.py:note_damage` / `projection.py:damage_events_after` / `active.py:_quhu_burn`）。 |
| `startDamagePlay(event)` | `damage-fx.js` | 一条伤害开演：量出两张武将块的矩形（剑光在 `source == null` 时把来源挪到画面外 200 单位，不猜来源；雷电则交 `src: null` 走天降）→ 档位走唯一的换算口 `damageFxVariantOf(amount)`（1~4；开演 / 排播闸门 / 暂停判据三处同一个）→ 按 `damageFxRendererFor(nature)` 选模块，`create(…, {profile: "game", bare: true, amount, numberKeepOut})` 挂进 `#t-damage-fx` 里**唯一**那张 canvas → 按 `duration`（= `anim.ini` 的 `total`）定时摘层；无属性 3 / 4 点档给 `#table-shell` 挂灰阶滤镜窗口，火 / 雷挂着色子层 `.fx-flash`。 |
| `damageSfxTick(play, seconds)` | `sfx-ui.js` | 每帧把**演出自己的** `seconds`（就是喂 `fx.draw` 的那个数）交给该演出的音效声道——`sfx.js` 的 `tick(t, v)` 据此判 cue；档位参数（`flight` / `strobe` / `split`）取 `fx.timing`，不另写数字。 |
| `skill_events_after(records, after, state, activations)` | `projection.py` | **技能特效的唯一判据**（U3-04 / D8.2 + lei 票 06）：`{seq, seat, skill_id, name, targets}` 由两条公共事实合成——① 未裁剪记录里**已接受且点名了技能**的动作（`ActivateSkill` / `SubstituteJudgeCard`）；② 引擎的发动缓冲 `Engine.skill_activations`（**记录面没有技能归属**的锁定技与转化技）。跑 `bash tests/smoke-web.sh`，用例在 `tests/support/ui_smoke.mjs` 的 U3-04*（U3-04k 锁定技 / U3-04l 转化技）。 |
| `damage_events_after(records, after, state, events)` | `projection.py` | **伤害演出的唯一判据**（damage-fx）：与 `skill_events_after` 同姿态——共用 `after` 游标、`seq > after` 才下发，形状 `{seq, source, target, amount, nature}`；事实来源是 `Engine.damage_events`，`records` 只定本批上界，座位越界 fail-closed，不按视角过滤（伤害是公开事实）。会话载荷的 `damage_events` 键由它填，前端由 `fireDamageEventsThrough` 消费。 |
| `syncTransientEffects()` | `effects.js` | 座位重建后补回仍在播的高亮 / 体力闪 / 白光；顺手收掉过期目标线。**不**再调伤害层（票 09 把版式重检挪到 `table.js:renderTable`）。 |
| `damageFxRendererFor(nature)` | `damage-fx.js` | 属性 → 画面模块的**唯一**分派表：`NORMAL`（含 `null` / 未登记属性）→ `SgsDamageFX`、`THUNDER` → `SgsThunderFX`、`FIRE` → `SgsFireFX`（票 03 起它就是唯一的火画面模块）。未登记属性 fail-open 回落剑光；已接的雷 / 火缺件就**不播**（不把火杀演成无属性剑光）。字段表 `damageFxTiming(nature, variant)` 也从它派生（前缀 + `TIMING_FIELDS`）。 |
| `damageFlashSync()` | `damage-fx.js` | 属性伤害的整屏着色闪：在 `#table-shell` 里挂 / 摘 `.fx-flash`（`z-index: 20`、`mix-blend-mode: screen`、`pointer-events: none`），窗口 = 该档 `strobe`、强度 = `flash`（缺省 0.6）；雷 = 冷白、火 = 暖白（颜色住在 `style.css` 的两个 class 里）。**不是** `filter`——那会在表壳上建 containing block、把两个 `position: fixed` 的子孙重新锚定；无属性仍走灰阶窗口，两者互斥。 |
| `damageDropStalePlays()` | `damage-fx.js` | **版式重检**（票 09 起由 `table.js:renderTable` 在座位重建之后显式调）：中剑方与来源两张武将块的矩形（四舍五入到 CSS 像素）有一张变了就摘掉在播的演出；矩形没变的普通重绘（回放每步都重绘全表）不打断。 |
| `clearDamageFx()` | `damage-fx.js` | **换局清场**（票 09 起由 `session.js:commitView` 换会话时显式调）：队列清空、在播演出摘层并收声、屏闪窗口撤掉；覆盖层不在了时由 `damageDropStalePlays()` 兜底。 |
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
  不读真实布局与时钟）；damage-fx 再补 `<canvas>` + 记录型 2D 上下文（剑 / 雷的圆弧与火焰的贝塞尔火舌都在，票 03 起 `draw(t)` 走的是同一份真代码）、`Path2D`、
  **只排队不自动跑**的 `setTimeout` / `requestAnimationFrame`（演出寿命 / 错开按毫秒读，
  `U-DMG-*` 用 `runEarliest` 显式推进），以及 `localStorage` 与一次性的
  `removeEventListener`（`U-SFX-*` 的解锁与开关记忆）。
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
