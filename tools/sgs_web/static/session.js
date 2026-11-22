/* SGS 前端 · 会话：接收视图、事件回放、处理区派生。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------ 会话视图的接收与应用 */

let playbackTimer = null;

/* ------------------------------------------------ 中央舞台：三组与寿命（U3-01） */

/** 弃牌组的来源原因（D2 / D7）：弃置 / 重铸 / 装备替换 + 拆顺（LOSE）。
 *  使用（USE）与判定（JUDGE）**不算弃牌**：它们只从「处理中」淡出。 */
const DISCARD_REASONS = new Set(["DISCARD", "REFORGE", "LOSE"]);

/** 隐藏区（D2）：牌面不公开的落点。反间的展示是「处理区 → 隐藏区 + 承载帧 SKILL」
 *  那一条（D6）；当前能从处理区走进去的只有手牌（牌堆方向留作同一判据）。 */
const HIDDEN_ZONES = new Set(["hand", "deck"]);

/** 区域名 → 舞台底行的中文（D10）。只用来源区域；牌堆 / 弃牌堆没有座位，不写底行。 */
const STAGE_ZONE_LABELS = {
  hand: "手牌",
  equip: "装备",
  judge: "判定",
};

/** 舞台停留（D9 / U4-03）：`0.5 × (N+1)` 秒，N = **产生该舞台项的那条记录**的牌数。
 *
 * N 只由产生它的记录决定（记在 `item.stage.batch`）：换组 / 跨 `commit` 都不重算，
 * 依次发生的两条移动各按 N=1 计（第二次交牌不会并进第一批）。
 * 缺省 / 非法 N 按 1 计（= U3 的单张停留 1s，现行回归不破）。
 * 淡出时长不在这里：它固定 `STAGE_FADE_MS`，`animScale` 只缩淡出、不缩停留。
 */
function stageHoldMs(batch) {
  const count = Number(batch);
  return 500 * ((Number.isFinite(count) && count > 0 ? count : 1) + 1);
}

/** 淡出时长（D5 / D9）：停留结束后整体淡出 1s；任何舞台项兜底 30s（丢 pop 时不留幽灵）。 */
const STAGE_FADE_MS = 1000;
const STAGE_MAX_MS = 30000;

/** 动画倍率（D15）：测试里设 `state.animScale = 0`，淡出立即完成；缺省 1。
 *
 * 它只缩**淡出**时长，不缩停留：停留是"看得见多久"，不是动画。
 */
function animScale() {
  return typeof state.animScale === "number" ? state.animScale : 1;
}

/** 当前时刻的唯一读取口（测试用 `renderProcessing(now)` 直接喂时间，不等真实时钟）。 */
function stageNow() {
  return Date.now();
}

/** 舞台项 = 卡面 + 一条道 + 来源 + 决定它活多久的结算帧 / 批量。
 *
 * 字段住在 `item.stage` 里（不是散在卡面上）：判定是**纯函数**
 * :func:`stageLane`，只读 `enteredAt` / `fadeAt`；`batch` 只参与算停留
 * （:func:`stageHoldMs`），在**创建**时按产生它的那条记录写下、之后不重算。
 * ``fadeAt === null`` = 还在等承载帧的 `pop`；兜底由 `enteredAt + 30s` 接住。
 */
function stageItem(card, options = {}) {
  const now = options.enteredAt === undefined ? stageNow() : options.enteredAt;
  const frameId = Number(options.frameId) || 0;
  const lane = options.lane || "processing";
  // 没有产生记录（快照补项 / 询问自带亮牌）时按 N=1：与 U3 的单张停留一致。
  const batch = options.batch === undefined
    ? 1
    : Math.max(1, Math.floor(Number(options.batch) || 1));
  let fadeAt = options.fadeAt;
  if (fadeAt === undefined) {
    // 处理中：离开处理区 / 承载帧 pop 才起算；展示与弃牌：无帧从入台起停 0.5(N+1)，
    // 有帧等 pop（D9：pop 时刻 + 0.5(N+1)）。
    fadeAt = lane === "processing" || frameId > 0 ? null : now + stageHoldMs(batch);
  }
  return {
    ...card,
    source: options.source !== undefined ? options.source : null,
    targets: options.targets !== undefined ? options.targets : null,
    currentTarget: options.currentTarget !== undefined ? options.currentTarget : null,
    stage: {
      lane,
      frameId,
      batch,
      enteredAt: now,
      fadeAt,
      fromZone: options.fromZone || null,
    },
  };
}

/** 记录里的承载帧号；缺省 / 非正数 = 无帧（frame_id = 0）。
 *
 * 合成载荷 / 旧录像可能没有这个键：按"无帧"处理，落到 1s + 1s 的固定口径。
 */
function stageFrameOf(record) {
  const frameId = Number(record && record.frame_id);
  return Number.isFinite(frameId) && frameId > 0 ? frameId : 0;
}

/** 结算帧的 kind（D6）：`frame_kind` 只随 `FrameRecord(push)` 下发，是服务端已下发的
 *  事实（不是前端写的技能规则）。按 `frame_id` 记住它，供"处理区 → 隐藏区"那次移动
 *  判断承载帧是不是 `SKILL`。
 *
 * 记在**舞台项列表自己身上**（WeakMap 的键 = 列表）：牌桌与回放页各持一份列表、各读
 * 自己那本记录流；回放页拖进度条会重放同一本流，不能用模块级全局表把两本流的同号帧
 * 混成一张（帧号每局都从 1 起）。
 */
const stageFrameKinds = new WeakMap();

/** 取（没有就建）这份舞台项列表的 `frame_id → frame_kind` 表。 */
function stageFrameKindMap(cards) {
  let kinds = stageFrameKinds.get(cards);
  if (!kinds) {
    kinds = new Map();
    stageFrameKinds.set(cards, kinds);
  }
  return kinds;
}

/** 这份舞台项列表见过的 `frame_id` 的 kind；没见过（旧录像 / 合成载荷缺 push）返回空串。 */
function stageFrameKindOf(cards, frameId) {
  return stageFrameKindMap(cards).get(String(frameId)) || "";
}

/** 舞台项的**入台来源**（D2 / D10）：`card_id → from_zone`，同样记在**舞台项列表自己身上**
 *  （WeakMap 的键 = 列表，形状与 :data:`stageFrameKinds` 一致；牌桌与回放页各持一份）。
 *
 *  为什么需要它：:func:`mergeEffectCards` 会把新快照的处理区牌并进舞台，那种"从快照补
 *  出来"的项**没有产生它的那条 `card_move`**，`stage.fromZone` 会丢 ⇒ 底行（`座位·武将·区域`）
 *  空着（真机验收里的【火攻】就是这样）。记录流里见过的来源留在这份列表上，跨 `commit`
 *  带着走；快照项创建时再用它补回来。
 */
const stageFromZones = new WeakMap();

/** 取（没有就建）这份舞台项列表的 `card_id → from_zone` 表。 */
function stageFromZoneMap(cards) {
  let zones = stageFromZones.get(cards);
  if (!zones) {
    zones = new Map();
    stageFromZones.set(cards, zones);
  }
  return zones;
}

/** 记下一次入台的来源（D2 的"首次入台"= 这条项这次亮相的来源；重落位时覆盖旧值）。 */
function stageRememberFromZone(cards, cardId, fromZone) {
  if (!fromZone) return;
  stageFromZoneMap(cards).set(String(cardId), fromZone);
}

/** 这张牌在这份列表里见过的入台来源；没见过（刷新后第一次从快照补出来的牌）返回 `null`。 */
function stageFromZoneOf(cards, cardId) {
  return stageFromZoneMap(cards).get(String(cardId)) || null;
}

/** 舞台项开始淡出的时刻（D5 / D9）：有 `fadeAt` 用它，否则兜底 = 入台 + 30s。
 *
 *  判定（:func:`stageLane`）与计时器（:func:`nextStageDeadline`）共用这一处算式：
 *  两边各写一份，兜底口径一旦漂移，就会出现"判定说还在、计时器说该走了"。
 *  30s 兜底**只接丢 pop**（`fadeAt === null`）：已经算出的停留即使超过 30s（极大的 N）
 *  也照 D9 的 `0.5(N+1)` 走，不被这个兜底截断——它不是停留上限。
 */
function stageFadeAt(stage) {
  const enteredAt = Number(stage.enteredAt) || 0;
  return stage.fadeAt === null || stage.fadeAt === undefined
    ? enteredAt + STAGE_MAX_MS
    : Number(stage.fadeAt);
}

/** 寿命推进（**纯函数**，D5 / D15）：`item + now → visible | fading | gone`。
 *
 * 判定不看计时器，也不改任何状态；`setTimeout` 只在 hold / fade 结束时触发重绘
 * （测试直接喂 `now`，或把 `animScale` 置 0）。
 */
function stageLane(item, now) {
  const stage = item && item.stage;
  if (!stage) return "gone";
  const fadeAt = stageFadeAt(stage);
  const fadeMs = STAGE_FADE_MS * animScale();
  if (now >= fadeAt + fadeMs) return "gone";
  if (now >= fadeAt) return "fading";
  return "visible";
}

/** 承载帧 `pop` 到了（D9）：挂在这帧上的舞台项从 pop 起停 `0.5(N+1)` 秒，到点再 1s 淡出
 *  （已排定的保持原锚点）。停留按各舞台项**自己的 batch** 算：同一帧里来自不同记录的项
 *  可以停不一样久；同一条记录的 N 张同刻入台 ⇒ 同刻 pop ⇒ 同刻开始淡出。
 *
 *  两个入口共用（`applyEffectDelta` / `applyRevealDelta` 的 pop 分支）：两处各写一份
 *  停留公式迟早会漂移。
 */
function stageStartFade(items, frameId, now) {
  for (const item of items || []) {
    const stage = item && item.stage;
    if (!stage) continue;
    if (String(stage.frameId) !== String(frameId)) continue;
    if (stage.fadeAt === null || stage.fadeAt === undefined) {
      stage.fadeAt = now + stageHoldMs(stage.batch);
    }
  }
}

/** 把一条舞台项改挂到**弃牌组**（D2 / D5）：换组不重新落位，寿命改挂这次结算帧；
 *  无帧（弃牌阶段的弃置）从**入台时刻**起算 1s 停留。
 *
 *  两个入口共用：处理区 → 弃牌堆的换组，以及同一张牌已在台上又收到弃牌移动。
 *  两处写的是同一份状态，各写一份迟早会漂移（比如一处忘了无帧分支）。
 */
function stageAttachDiscard(item, frameId) {
  item.stage.lane = "discard";
  item.stage.frameId = frameId;
  // D9：batch 在创建时由产生该舞台项的那条记录写下；换组不重算（沿用首次入台的那一批）。
  item.stage.fadeAt = frameId > 0
    ? null
    : item.stage.enteredAt + stageHoldMs(item.stage.batch);
}

/** 舞台项的入台顺序（D3：三组按入台顺序排在**一个**换行流里，不按组分行）。 */
function stageCardItems(effectCards, revealCards, now) {
  const out = [];
  const seen = new Set();
  for (const item of (effectCards || []).concat(revealCards || [])) {
    if (!item || !item.stage) continue;
    const key = String(item.card_id);
    if (seen.has(key)) continue;  // 同一 card_id 同时只出现在一组里（D2）
    if (stageLane(item, now) === "gone") continue;
    seen.add(key);
    out.push(item);
  }
  out.sort((left, right) => Number(left.stage.enteredAt) - Number(right.stage.enteredAt));
  return out;
}

/** 下一次需要重绘的时刻（hold 结束 / 淡出结束 / 兜底 30s）；没有就 `null`。 */
function nextStageDeadline(items, now) {
  let next = null;
  const fadeMs = STAGE_FADE_MS * animScale();
  for (const item of items || []) {
    const stage = item && item.stage;
    if (!stage) continue;
    const fadeAt = stageFadeAt(stage);
    for (const at of [fadeAt, fadeAt + fadeMs]) {
      if (at > now && (next === null || at < next)) next = at;
    }
  }
  return next;
}

/** 舞台牌底行 = `座位号·武将名·区域`（D10）；无座位（牌堆 / 弃牌堆）或无名时省掉缺项。 */
function stageZoneLabel(item, heroNameOf) {
  const from = item && item.stage && item.stage.fromZone;
  if (!from || from.seat === null || from.seat === undefined) return "";
  const zone = STAGE_ZONE_LABELS[from.zone];
  if (!zone) return "";
  const hero = typeof heroNameOf === "function" ? heroNameOf(from.seat) : "";
  return hero ? `${from.seat}·${hero}·${zone}` : `${from.seat}·${zone}`;
}

/** 舞台三组的说明文本（`#t-context` 与回放页 `#r-effect` 共用，最多 2 行由 CSS 裁）。 */
function describeStageGroups(effectCards, revealCards, now) {
  const items = stageCardItems(effectCards, revealCards, now);
  const processing = items.filter(
    (item) => item.stage.lane === "processing" && stageLane(item, now) === "visible",
  );
  const showcase = items.filter((item) => item.stage.lane === "showcase");
  const discard = items.filter((item) => item.stage.lane === "discard");
  return [describeEffect(processing), describeReveals(showcase), describeDiscards(discard)]
    .filter(Boolean)
    .join(" / ");
}

/** 这份视图是不是**换会话**（开新局 / 从录像回到牌桌 / 首屏）。
 *
 * 询问序号 / 自动提交去重键 / 舞台项都只在**同一会话**里有效；缺 `session_id` 的合成
 * 载荷按同一会话处理（测试夹具 / 旧录像），不在每次刷新都清屏。
 */
function viewStartsNewSession(body) {
  if (!state.view) return true;
  if (!body || !body.session_id || !state.view.session_id) return false;
  return body.session_id !== state.view.session_id;
}

/** 新局开局的**回放底稿**：服务端下发的 `setup`（发牌前那一帧）。
 *
 * 建局响应止于"游戏开始时"（`GAME_START` 派发完、第一回合之前），带一份 `setup` 快照
 * ——座位 / 武将 / 技能 / 满体力 / 护甲 / 标记 / 手牌上限 / 武将块标签，手牌 0、各区空、
 * 无阶段无回合无询问。这份底稿是**服务端**从 `build_initial` 之后的引擎状态导出的
 * （`projection.py:setup_snapshot`），前端**只显示**：不再像旧实现那样从 post-init 快照
 * 倒推（`hp = hp_max ?? hp`、`hp_max` / `armor` / `markers` 原样继承、再扫
 * `hp_max_change.before` 反推开局上限）——那是"界面从记录猜规则"，也正是"init 没走完
 * AI 已经在操作"的观感来源（spec §1 #1）。
 *
 * 之后的一切仍逐条由记录派生（`identity_deal` / `deck_shuffle` / `card_move` /
 * `hp_change` / `hp_max_change` …，见 :func:`applyLiveDelta`），故"有没有上一局"不影响
 * 这一批的底稿与节奏。
 *
 * 没有 `setup` 的载荷（旧录像 / 合成视图 / 老服务端）**不猜**发牌前长什么样：直接以权威
 * 快照起播（少一段开局动画，多一个控制台提示）。
 */
function openingLiveView(body) {
  const setup = body && body.setup;
  if (!setup) {
    const fallback = cloneLiveView(body);
    fallback.pending = null;
    if (typeof console !== "undefined" && console.warn) {
      console.warn("[opening] 载荷没有 setup 底稿：开局批从权威快照起播（不发牌前动画）");
    }
    return fallback;
  }
  // `cloneLiveView` 深拷贝牌桌会改写的容器 ⇒ 回放不碰 `body.setup`（它是权威载荷的一部分）。
  const view = cloneLiveView({ ...body, snapshot: setup });
  view.pending = null;
  return view;
}

/* ------------------------------------------------ 回放期逐事件全表更新（U3-05 / D11） */

/** 技能状态的三条 delta（M6 票 04 的 `skill_gain` / `skill_lose`；ui-live-refresh 票 03
 * 补上 `skill_suppress`）：这三条都只改"技能清单里的某一格"（有没有 / 是否失效），
 * 前端拼不出来，故一律**批首装终态**（`adoptSkillLists`）。
 *
 * `skill_suppress` 是同一类漏登记：它进了 `REBUILD_ONLY_DELTAS`（要整桌重建），却没进
 * 这张表 ⇒ 整批回放期间按钮区不认识"被封锁"，与 [谦逊] 牌区回手那一类报障同形。
 */
const SKILL_DELTA_TYPES = new Set(["skill_gain", "skill_lose", "skill_suppress"]);

/** 这条可见记录是不是**技能增删**（`:func:`adoptSkillLists` 的触发判据）。 */
function isSkillDeltaRecord(record) {
  return Boolean(record) && record.kind === "delta" && SKILL_DELTA_TYPES.has(record.delta_type);
}

/** 起播前把**技能清单**换成这一批的终态（`body` 里的权威清单）。
 *
 * 为什么技能不走逐事件派生：`skill_gain` / `skill_lose` 只带 `skill_id`，而按钮区要
 * 名字 / 类别 / 徽标 / 可用性、详情要三态——全在快照里，客户端拼不出来第二条真源。
 * 于是回放期若继续按 live view 的旧 `skills` 画，整段回放都在显示**上一份**清单：
 * 韬·刘备【义起】实机报障（2026-11-01）"失去【从讨】之后按钮区还写着【从讨】、
 * 也没有【武圣】【咆哮】"就是这个窗口（详情页同样是旧的——两处都读 live view）。
 *
 * 只覆盖两处**纯清单**字段：按钮区顶层 `skills` 与每座位 `players[].skills`（详情用）。
 * 技能是明置的公开事实，且它们本来就不做逐张动画，故提前装终态不会提前泄露任何牌面；
 * 牌面 / 体力 / 区域仍逐事件走 `applyLiveDelta`。
 */
function adoptSkillLists(liveView, body) {
  if (!liveView || !body) return;
  liveView.skills = (body.skills || []).slice();
  const fresh = (body.snapshot && body.snapshot.players) || [];
  for (const player of (liveView.snapshot && liveView.snapshot.players) || []) {
    const same = fresh.find((item) => Number(item.seat) === Number(player.seat));
    if (same && Array.isArray(same.skills)) player.skills = same.skills.slice();
  }
}

/** 一份新的 **live view**：从最近一次提交的视图复制出牌桌会用到的可变容器。
 *
 * 它只属于回放期：每条记录把**可见**变化写进它（见 :func:`applyLiveDelta`），`renderTable`
 * 从它重绘；`commitView` 到时用权威快照整份覆盖。深拷贝的是牌桌会改写的容器
 * （`players` 及其装备 / 判定区 / 武将牌上的牌区、`self_hand`、`discard`、`processing`），
 * 故逐事件派生（含标签增删）**绝不改** `state.view` 的权威快照。
 */
function cloneLiveView(body) {
  const view = { ...body };
  // ui-live-refresh 票 02：顶层 `skills` 与快照里的 `relics` 也会被**显示事实**写
  // （技能外观组件整份替换 / 遗物栏），故它们同样要深一层拷贝——否则回放期写的是
  // `state.view`（权威提交视图）里的那一份，回退 / 重播都会看到被改过的"权威"。
  view.skills = Array.isArray(body.skills)
    ? body.skills.map((entry) => ({ ...entry }))
    : body.skills;
  const snapshot = body && body.snapshot;
  if (!snapshot) return view;
  view.snapshot = {
    ...snapshot,
    players: (snapshot.players || []).map((player) => ({
      ...player,
      equip: (player.equip || []).slice(),
      judge: (player.judge || []).slice(),
      hero_zones: (player.hero_zones || []).map((zone) => ({
        ...zone,
        cards: (zone.cards || []).slice(),
      })),
    })),
    self_hand: (snapshot.self_hand || []).slice(),
    discard: (snapshot.discard || []).slice(),
    processing: (snapshot.processing || []).slice(),
    relics: Array.isArray(snapshot.relics) ? snapshot.relics.slice() : snapshot.relics,
  };
  return view;
}

/** `PHASE_<名字>_…` 时机 → 投影里的 `Phase` 枚举值。
 *
 * 阶段值由**阶段体**写进引擎（`_phase_prepare` / `_phase_discard_start` 都先写 `state.phase`
 * 再发时机），故时机名里的那段就是新阶段；`_START` / `_END` / `_IDLE` 后缀不改变阶段。
 */
const PHASE_TIMING_VALUES = {
  PREPARE: "prepare",
  JUDGE: "judge",
  DRAW: "draw",
  PLAY: "play",
  DISCARD: "discard",
  FINISH: "finish",
};

/** 一条移动的张数：牌面可见时 = `cards.length`；被服务端裁掉（隐藏区）时读 `card_count`。
 *
 * 两个字段**互斥**（`visible_record` 只在抹掉 `cards` 时补 `card_count`），不会重复计数。
 */
function liveMoveCount(record) {
  const visible = (record.cards || []).length;
  if (visible) return visible;
  const hidden = Number(record.card_count);
  return Number.isFinite(hidden) && hidden > 0 ? hidden : 0;
}

/** 按牌 id 从一份公开牌表里撤掉这些牌；`cards` 为空（隐藏区移动）时原样复制。 */
function liveRemoveCards(list, cards) {
  if (!cards || !cards.length) return (list || []).slice();
  const gone = new Set(cards.map((card) => String(card.card_id)));
  return (list || []).filter((item) => !gone.has(String(item.card_id)));
}

/** 一个**武将牌上的牌区**的区键：`owner:skill_id`（票 10）。
 *
 * 记录里的区引用只有合起来的 `zone_ref.skill_instance`；投影下发的行只有 `skill_id` /
 * `owner` 两半（`projection.py:seat_hero_zones`）——两边是**同一个**派生
 * （`sgs.skills.catalog.hero_card_zone_key`），故这里按同一形状拼出来比对。行里将来若
 * 直接带 `skill_instance`，就用它（唯一真源仍是那个键）。
 */
function liveHeroZoneKey(zone) {
  if (!zone) return "";
  if (zone.skill_instance !== undefined && zone.skill_instance !== null) {
    return String(zone.skill_instance);
  }
  if (zone.skill_id === undefined || zone.skill_id === null) return "";
  if (zone.owner === undefined || zone.owner === null) return "";
  return `${Number(zone.owner)}:${String(zone.skill_id)}`;
}

/** `skill_instance`（`owner:skill_id`）拆回两半；不合形返回 `null`（不猜）。 */
function liveHeroZoneParts(skillInstance) {
  const text = String(skillInstance || "");
  const at = text.indexOf(":");
  if (at <= 0 || at === text.length - 1) return null;
  const owner = Number(text.slice(0, at));
  if (!Number.isFinite(owner)) return null;
  return { owner, skill_id: text.slice(at + 1) };
}

/** 武将牌上牌区的**展示名**：同一份快照里座位技能清单的中文名（票 10）。
 *
 * 放置 / 归还的 `card_move` 只带 `skill_instance`，不带中文名；技能清单是同一份快照里的
 * **公开事实**（`projection.py:seat_skill_display`），故按它翻。翻不到退回技能 id——与投影
 * 的 fail-visible 口径一致（`_skill_display_name(...) or zone.skill_id`），不编名字。
 */
function liveHeroZoneName(players, skillId) {
  const wanted = String(skillId || "");
  if (!wanted) return "";
  for (const player of players || []) {
    for (const skill of player.skills || []) {
      if (skill && skill.skill_id === wanted && skill.name) return String(skill.name);
    }
  }
  return wanted;
}

/** 一条 `card_move` 的**一侧**（`from_zone` / `to_zone` = `hero`）落到 live view（票 10）。
 *
 * 区按 `zone_ref.skill_instance` 认，`ref.seat` = 武将牌的主人（区可跨座位）。牌面只有宿主
 * 拿得到（`visibility.py:hero_zone_ref_visible`）：有牌面就按牌面增删，非宿主只有
 * `card_count` ⇒ 只改张数、不碰 `cards`。
 *
 * **区空了整行删掉**（不是留一行 0 张）：投影只画有牌的区（`projection.py:643`），界面留
 * 一行空框就是报障的"牌已回手、行还在"。反方向，空批移动不建区（引擎同样不建）。
 */
function liveHeroZoneMove(players, ref, cards, count, adding) {
  if (!ref) return;
  const holder = (players || []).find(
    (player) => Number(player.seat) === Number(ref.seat));
  if (!holder) return;
  // 区引用不带 `skill_instance` 就不是武将牌上的牌区（列表非空只在 HERO 区有，见
  // `models.py:_zone_ref_wire`）；不合形（没有 `owner:skill_id`）⇒ 不猜、零动作。
  const key = String(ref.skill_instance || "");
  if (key.indexOf(":") < 0) return;
  const zones = (holder.hero_zones || []).slice();
  const at = zones.findIndex((zone) => liveHeroZoneKey(zone) === key);
  const held = at >= 0 ? zones[at] : null;
  const faces = Array.isArray(cards) ? cards : [];
  let heldCards = held ? (held.cards || []).slice() : [];
  let heldCount = held ? Number(held.count) : 0;
  if (!Number.isFinite(heldCount)) heldCount = heldCards.length;
  if (adding) {
    if (faces.length) {
      heldCards = heldCards.concat(faces);
      heldCount = heldCards.length;
    } else {
      heldCount += count;
    }
    if (heldCount <= 0) return;
    const parts = liveHeroZoneParts(key);
    const row = held || {
      skill_id: parts ? parts.skill_id : key,
      skill_name: liveHeroZoneName(players, parts ? parts.skill_id : key),
      owner: parts ? parts.owner : Number(ref.seat),
      cards: [],
      count: 0,
    };
    const next = { ...row, cards: heldCards, count: heldCount };
    if (at >= 0) zones[at] = next;
    else zones.push(next);
  } else {
    if (!held) return;
    if (faces.length) {
      heldCards = liveRemoveCards(heldCards, faces);
      heldCount = heldCards.length;
    } else {
      heldCount = Math.max(0, heldCount - count);
    }
    if (heldCount <= 0) zones.splice(at, 1);
    else zones[at] = { ...held, cards: heldCards, count: heldCount };
  }
  holder.hero_zones = zones;
}

/** 把展示通道的标签 delta 与**可见记录**按 `seq` 合并，交给同一条逐事件回放。
 *
 * `live_deltas` 是服务端从被记录流裁掉（但牌面已公开）的 `tag_add` / `tag_remove`
 * 派生的网页载荷；它没有对应的 `records` 条目，故这里按 `seq` 插进回放序列，并打
 * `live_only`。`stepPlayback` 只对它们更新 live view，不写事件流 / 舞台 / 技能演出。
 */
function mergeLiveDeltas(records, liveDeltas) {
  const events = (liveDeltas || [])
    .filter((event) => event && Number.isFinite(Number(event.seq)))
    .slice()
    .sort((left, right) => Number(left.seq) - Number(right.seq));
  if (!events.length) return records || [];
  const out = [];
  let next = 0;
  for (const record of records || []) {
    const seq = Number(record && record.seq);
    while (next < events.length && Number.isFinite(seq)
      && Number(events[next].seq) <= seq) {
      out.push({ ...events[next], kind: "delta", live_only: true });
      next += 1;
    }
    out.push(record);
  }
  while (next < events.length) {
    out.push({ ...events[next], kind: "delta", live_only: true });
    next += 1;
  }
  return out;
}

/** live view 里所有可能带牌面的列表（手牌 / 弃牌堆 / 处理区 / 装备 / 判定 / 武将牌区）。 */
function liveCardLists(view) {
  const snapshot = view && view.snapshot;
  if (!snapshot) return [];
  const lists = [snapshot.self_hand, snapshot.discard, snapshot.processing];
  for (const player of snapshot.players || []) {
    lists.push(player.equip, player.judge);
    for (const zone of player.hero_zones || []) lists.push(zone.cards);
  }
  return lists.filter((list) => Array.isArray(list));
}

/** 在一张**已经下发牌面**的牌上就地加 / 撤一个标签（live view 用，替换卡片对象）。
 *
 * 标签值由服务端 `live_deltas.tag` 给出（`skill_id` + 派生好的 `label`）；前端不按
 * 技能 id 查中文名。找不到这张牌（隐藏 / 还没进表）= 零动作，不猜它属于谁。
 */
function liveUpdateCardTags(view, cardId, tag, adding) {
  if (!tag || !tag.skill_id) return;
  const wanted = String(cardId);
  const skillId = String(tag.skill_id);
  for (const list of liveCardLists(view)) {
    for (let index = 0; index < list.length; index += 1) {
      const card = list[index];
      if (!card || String(card.card_id) !== wanted) continue;
      let tags = Array.isArray(card.tags) ? card.tags.slice() : [];
      if (adding) {
        const at = tags.findIndex(
          (item) => item && String(item.skill_id) === skillId);
        if (at >= 0) tags[at] = tag;
        else tags.push(tag);
      } else {
        tags = tags.filter(
          (item) => !item || String(item.skill_id) !== skillId);
      }
      const next = { ...card };
      if (tags.length) next.tags = tags;
      else delete next.tags;
      list[index] = next;
    }
  }
}

/* ------------------------------------------------ 显示事实（ui-live-refresh 票 02/修复 2） */

/** 服务端的 `display_facts` → 规范化的事实行（缺 `seq` / 缺 `key` 的丢掉：fail-closed）。
 *
 * **事实不占回放步**（修复 2）：它们**挂到记录上**（`attachDisplayFacts`），在**那条记录之后**
 * 应用、同一次 `renderTable` 里生效。此前每个事实自己是一条 `live_only` 记录 ⇒ 一个事实
 * 白白多花一个回放步长（≈100 ms，一批几十条事实就是几秒的"操作变慢"），并且在同一条记录
 * 前后各重建一次整桌（先刷新标签、再刷同心跳，看起来就是"闪一下"）。
 */
function displayFactRecords(body) {
  const facts = body && Array.isArray(body.display_facts) ? body.display_facts : [];
  const out = [];
  for (const fact of facts) {
    if (!fact || typeof fact.key !== "string" || !fact.key) continue;
    if (!Number.isFinite(Number(fact.seq))) continue;
    out.push({
      seq: Number(fact.seq),
      scope: String(fact.scope || ""),
      seat: fact.seat === undefined || fact.seat === null ? null : Number(fact.seat),
      key: fact.key,
      value: fact.value,
    });
  }
  out.sort((left, right) => left.seq - right.seq);
  return out;
}

/** 把事实**挂到"最后一条 seq ≤ 事实 seq"的记录上**（事实采样于那一步之后，故应用在那条之后）。
 *
 * 服务端在每步结束采样，事实的 `seq` = 那一步记录流的末条；因此"事实属于这条记录之后"。
 * 找不到更早的记录（事实早于这一批）就挂到第一条上——位置只会偏差一条，不会丢。
 * 记录上没有对应事实的批次（绝大多数）一个字节都不变：`displayFacts` 是按需加的。
 */
function attachDisplayFacts(records, facts) {
  if (!Array.isArray(records) || !records.length || !facts.length) return;
  let index = 0;
  for (const fact of facts) {
    while (index + 1 < records.length && Number(records[index + 1].seq) <= fact.seq) {
      index += 1;
    }
    if (!records[index]) continue;
    // 浅拷贝再挂：`incoming` 里的对象就是响应体 `body.records` 的那几个（`mergeLiveDeltas`
    // 原样返回），挂上去等于把展示事实写进 `state.view.records`（权威载荷）。拷贝一份，
    // 让事实只活在回放流里。
    if (!records[index].displayFacts) {
      records[index] = { ...records[index], displayFacts: [] };
    }
    records[index].displayFacts.push(fact);
  }
}

/** 一条显示事实 → 视图里那一格（**通用**写入口：没有任何逐字段分支）。
 *
 * 落点由服务端声明的 `scope` 决定（闭集，与 `projection.PAYLOAD_KEYS` 的 level 同名）：
 *   - `player` → `snapshot.players[seat][key]`；
 *   - `snapshot` → `snapshot[key]`；
 *   - `view` → 视图顶层 `[key]`；
 *   - `skill` → **技能外观组件**：`value` 是 `{skill_id: 外观载荷}`，整份替换——表里没有的
 *     技能把 `appearance` 删掉（技能失去 / 化身库空了都是这个形状）。
 * 未登记 scope / 找不到座位 = 零动作（fail-closed，不猜落点）。
 */
function applyDisplayFact(fact, view = state.liveView) {
  if (!fact || !view || !view.snapshot) return false;
  const key = typeof fact.key === "string" ? fact.key : "";
  if (!key) return false;
  if (fact.scope === "player") {
    const players = view.snapshot.players || [];
    const player = players.find((item) => Number(item.seat) === Number(fact.seat));
    if (!player) return false;
    if (sameFactValue(player[key], fact.value)) return false;
    player[key] = fact.value;
    return true;
  }
  if (fact.scope === "snapshot") {
    if (sameFactValue(view.snapshot[key], fact.value)) return false;
    view.snapshot[key] = fact.value;
    return true;
  }
  if (fact.scope === "view") {
    if (sameFactValue(view[key], fact.value)) return false;
    view[key] = fact.value;
    return true;
  }
  if (fact.scope === "skill") {
    const wanted = fact.value && typeof fact.value === "object" ? fact.value : {};
    let changed = false;
    for (const entry of view.skills || []) {
      if (!entry || !entry.skill_id) continue;
      if (Object.prototype.hasOwnProperty.call(wanted, entry.skill_id)) {
        if (!sameFactValue(entry.appearance, wanted[entry.skill_id])) {
          entry.appearance = wanted[entry.skill_id];
          changed = true;
        }
      } else if (entry.appearance !== undefined) {
        delete entry.appearance;
        changed = true;
      }
    }
    return changed;
  }
  return false;
}

/** 显示事实的**同值判据**：值一样就不写、也就不必为它重建一次整桌（返回 `false`）。
 *
 * 走 JSON 文本比较：事实值都是服务端下发的 JSON（无环、无函数），一次 `JSON.stringify`
 * 在几十字节到几 KB 之间，比一次 `renderTable` 便宜两个数量级。
 */
function sameFactValue(current, next) {
  if (current === next) return true;
  try {
    return JSON.stringify(current) === JSON.stringify(next);
  } catch (error) {
    return false;
  }
}

/** 一条记录对 live view 的**可见**贡献（D11）：只写记录里真有的派生事实。
 *
 * 可派生：体力（`hp_change.after`）与体力上限（`hp_max_change.after`；票 10 补，座位卡的
 * 血条分母读它）、护甲（`armor_change.after`，W7 的第二条体力轴）、
 * 手牌数（`card_move` 的牌面 / `card_count`）、自己的手牌面、装备与判定区（公开区牌面）、
 * 武将牌上的牌区（`card_move` 的 `hero` 那一侧，按 `skill_instance` 认区、空区删行；票 10）、
 * 牌堆与弃牌数（含 `deck_shuffle` 的重洗）、横置、
 * 酒、亮明身份、阵亡（`DEATH_BEFORE_START` 的 `actor`）、回合 / 阶段（`TURN_*` / `PHASE_*`）。
 *
 * **不可派生的字段一律不猜**：技能可用性、当前询问、武将名与势力留在最近一次提交的视图里，
 * 由下一次 `commitView` 覆盖（D11）。`snapshot.processing` 也不在这里推：
 * 舞台的处理中组由 `applyEffectDelta` 单独派生（转化牌要显示产物，快照里的实体来源牌
 * 不是那个形状），表体渲染不读它。
 *
 * 就地改传入的 view（= `state.liveView`）；回放页与权威快照都不经过这里。
 */
function applyLiveDelta(record, view = state.liveView) {
  if (!record || !view || !view.snapshot) return view;
  const snapshot = view.snapshot;
  const players = snapshot.players || [];
  const playerAt = (seat) => players.find((player) => Number(player.seat) === Number(seat));
  if (record.kind === "delta") {
    if (record.delta_type === "tag_add" || record.delta_type === "tag_remove") {
      const cardId = record.card_id !== undefined && record.card_id !== null
        ? record.card_id
        : (record.card && record.card.card_id);
      const tag = record.tag || (
        record.skill_id
          ? { skill_id: record.skill_id, label: record.label || record.skill_id }
          : null
      );
      if (cardId !== undefined && cardId !== null && tag) {
        liveUpdateCardTags(view, cardId, tag, record.delta_type === "tag_add");
      }
      return view;
    }
    if (record.delta_type === "hp_change") {
      const player = playerAt(record.seat);
      if (player) player.hp = Number(record.after);
      return view;
    }
    if (record.delta_type === "hp_max_change") {
      // 体力上限与当前体力是**两条独立事实**（`state/deltas.py:HpMaxChangeDelta`）：觉醒技
      // 减上限、开局 init 期改上限都落这一条。座位卡的血条分母读它，故必须逐条派生——
      // 不派生时整批回放期间上限停在上一份快照值（spec §1 #1 的"开局就 5/5"是同一条缝；
      // 票 10 只落前端这一半，票 14 的服务端 `setup` 底稿靠它才不必倒推）。
      const player = playerAt(record.seat);
      if (player) player.hp_max = Number(record.after);
      return view;
    }
    if (record.delta_type === "armor_change") {
      // W7 的第二条体力轴：护甲值（绝对值，after 是唯一真源）。座位卡的护甲段读它。
      const player = playerAt(record.seat);
      if (player) player.armor = Number(record.after);
      return view;
    }
    if (record.delta_type === "chained_change") {
      const player = playerAt(record.seat);
      if (player) player.chained = Boolean(record.chained);
      return view;
    }
    if (record.delta_type === "flipped_change") {
      const player = playerAt(record.seat);
      if (player) player.flipped = Boolean(record.flipped);
      return view;
    }
    if (record.delta_type === "marker_change") {
      // 票 06：标记数量变化（绝对值；0 = 移除）。live view 只改数量 / 增删条目，
      // 来源标签与说明由下一次快照（服务端 seat_display）补齐。
      const player = playerAt(record.seat);
      if (!player) return view;
      const markers = (player.markers || []).slice();
      const at = markers.findIndex(
        (marker) => marker.name === record.name
          && Number(marker.source_seat) === Number(record.source_seat)
          && marker.source_skill_id === record.source_skill_id,
      );
      const count = Number(record.count) || 0;
      if (count <= 0) {
        if (at >= 0) markers.splice(at, 1);
      } else if (at >= 0) {
        markers[at] = { ...markers[at], count };
      } else {
        markers.push({
          name: record.name,
          count,
          source_seat: Number(record.source_seat),
          source_skill_id: record.source_skill_id,
          source_label: `座位 ${Number(record.source_seat) + 1}`,
          description: null,
        });
      }
      player.markers = markers;
      return view;
    }
    if (record.delta_type === "wine_change") {
      const player = playerAt(record.seat);
      if (player) player.wine_active = Boolean(record.active);
      return view;
    }
    if (record.delta_type === "turn_change") {
      // M6 票 11 的回合边界事实（票 19 起界面消费）：额外回合**不推进自然指针**，
      // 故轮次只在这里更新、不按 TURN_START 数（那样同一个座位连开两个回合会误进一轮）。
      // 公开事实（回合座位 + 轮次都在明面上），无需按可见性裁剪。
      snapshot.round = Number(record.round);
      snapshot.turn_extra = Boolean(record.extra);
      return view;
    }
    if (record.delta_type === "identity_deal") {
      // 开局发身份（M6 报障 2 的开局回放）：`identity` 为 null = 这名座位对你不可见。
      // 与下面的 `identity_reveal` 同一条写入口（翻译点仍只有 `identityLabel` 一处）。
      const player = playerAt(record.seat);
      if (player) {
        player.identity = record.identity ?? null;
        player.identity_label = identityLabel(record.identity);
      }
      return view;
    }
    if (record.delta_type === "identity_reveal") {
      const player = playerAt(record.seat);
      if (player) {
        player.identity = record.identity;
        // 座位卡优先读服务端 `identity_label`；记录里只有枚举值，故按**同一份**前端映射
        // 补出来（身份徽标的翻译点仍只有 `interaction.js:identityLabel` 一处）。
        player.identity_label = identityLabel(record.identity);
      }
      return view;
    }
    if (record.delta_type === "deck_shuffle") {
      // 重洗 = 弃牌堆并回牌堆。A3 下人类载荷只给数量（`cards` 抹空 + `card_count`），
      // 故张数与 card_move 走**同一个读取口** liveMoveCount，不读牌序。
      const from = record.from_zone || {};
      const to = record.to_zone || {};
      if (from.zone === "discard") {
        snapshot.discard = [];
        if (to.zone === "deck") {
          snapshot.deck_count = Number(snapshot.deck_count) + liveMoveCount(record);
        }
      } else if (!from.zone && to.zone === "deck") {
        // **开局洗牌**（`from_zone = null`）：牌堆张数就是这一条给的。开局底稿的
        // `deck_count` 是 0（发牌前），这里把它写回，后面的发牌 / 摸牌才会往下减。
        snapshot.deck_count = liveMoveCount(record);
      }
      return view;
    }
    if (record.delta_type !== "card_move") return view;
    const cards = record.cards || [];
    const from = record.from_zone || {};
    const to = record.to_zone || {};
    const count = liveMoveCount(record);
    // 牌堆只给数量。
    if (from.zone === "deck") snapshot.deck_count = Number(snapshot.deck_count) - count;
    if (to.zone === "deck") snapshot.deck_count = Number(snapshot.deck_count) + count;
    // 弃牌堆是公开区：按牌面增减。
    if (from.zone === "discard") snapshot.discard = liveRemoveCards(snapshot.discard, cards);
    if (to.zone === "discard") snapshot.discard = (snapshot.discard || []).concat(cards);
    // 手牌数：来源座位先扣、目标座位再加（隐藏区移动只有张数）。
    if (from.zone === "hand") {
      const player = playerAt(from.seat);
      if (player) player.hand_count = Math.max(0, Number(player.hand_count) - count);
    }
    if (to.zone === "hand") {
      const player = playerAt(to.seat);
      if (player) player.hand_count = Number(player.hand_count) + count;
    }
    // 自己的手牌面：服务端按同一可见性口径保证「查看者自己经手的移动牌面可见」。
    if (from.zone === "hand" && Number(from.seat) === Number(snapshot.seat) && cards.length) {
      snapshot.self_hand = liveRemoveCards(snapshot.self_hand, cards);
    }
    if (to.zone === "hand" && Number(to.seat) === Number(snapshot.seat) && cards.length) {
      snapshot.self_hand = (snapshot.self_hand || []).concat(cards);
    }
    // 装备 / 判定区：公开区牌面。装备区按槽位落位，列表序不派生（渲染读 `slot`）；
    // 判定区是 `extend` 置入序，追加即权威顺序。
    if (from.zone === "equip" || from.zone === "judge") {
      const player = playerAt(from.seat);
      if (player) player[from.zone] = liveRemoveCards(player[from.zone], cards);
    }
    if (to.zone === "equip" || to.zone === "judge") {
      const player = playerAt(to.seat);
      if (player) player[to.zone] = (player[to.zone] || []).concat(cards);
    }
    // 武将牌上的牌区（票 10 / spec §1 #11）：区键 = 记录里的 `zone_ref.skill_instance`，
    // 宿主 = `ref.seat`。**空区整行删掉**（投影只画有牌的区，`projection.py:643`）——
    // 这一条就是"【谦逊】的牌回手了、武将牌上的那一行还在"的修法：以前 `card_move` 的
    // 分支只派生 deck / discard / hand / equip / judge，而 `cloneLiveView` 又深拷贝
    // `hero_zones` ⇒ 整批回放期间那个区停在上一份快照值，只能等 `commitView` 整桌重建。
    // 手牌数 / 手牌面的两半由上面的 `to.zone === "hand"` 分支管，这里不重复加。
    if (from.zone === "hero") liveHeroZoneMove(players, from, cards, count, false);
    if (to.zone === "hero") liveHeroZoneMove(players, to, cards, count, true);
    return view;
  }
  if (record.kind !== "timing") return view;
  const timing = String(record.timing || "");
  if (timing === "DEATH_BEFORE_START") {
    const player = playerAt(record.actor);
    if (player) player.alive = false;
    return view;
  }
  if (timing === "TURN_BEFORE_START" || timing === "TURN_START") {
    // 回合轮转本身**没有记录**（`_step_turn` 末尾直接改 `state.turn_seat`）：这两条时机的
    // `actor` 就是权威回合座位（前者紧跟在轮转之后、后者属于当前回合）。
    if (record.actor !== null && record.actor !== undefined) {
      snapshot.turn_seat = Number(record.actor);
    }
    return view;
  }
  if (timing.startsWith("PHASE_")) {
    const phase = PHASE_TIMING_VALUES[timing.split("_")[1]];
    if (phase) snapshot.phase = phase;
  }
  return view;
}

/** 换到一份**新会话**的首屏视图：重置本地态 → 回放／提交 → 切到牌桌（M7 票 23）。
 *
 * 两个入口共用这一条路：开局页建局成功（`lobby.js:startGame`）与剧本战斗节点进牌桌
 * （`script-loadout.js:scriptBattleEnter`）。会话 id / 游标 / 日志先换掉，上一局还在播的
 * 事件先收尾——否则旧记录会漏进新一局的轮次计数与事件流。
 */
function startSessionView(body) {
  state.sessionId = body.session_id;
  state.cursor = -1;
  state.logSeq = 0;
  if (state.playback) flushPlayback();
  state.logRecords = [];
  state.narrative = [];
  state.narrativeShown = 0;
  state.lastQuerySeat = null;
  $("t-log").innerHTML = "";
  applyView(body);
  show("table");
  return body;
}

/** 收到一份新视图：**有事件就先回放**，播完再提交快照与询问（报障 3 的严格顺序）。
 *
 * 回放期间 ``state.view`` 仍是**上一条**视图、提交按钮是灰的——用户看到的是事件一条条
 * 发生，而不是"画面直接跳到结算完"。没有事件（纯刷新）就直接提交。
 */
function applyView(body) {
  if (state.playback) flushPlayback();
  // U1-05：叙事条目随视图增量下发（服务端已按查看者过滤）。先收进本地序列，
  // 再按记录回放的进度逐条铺——不在前端补事实 / 判断隐藏信息。
  for (const entry of body.narrative || []) state.narrative.push(entry);
  // 票 01：叙事序列也只留最近 `LOG_ITEM_LIMIT` 条（`narrativeShown` 跟着前移，
  // 与 `#t-log` 的 DOM 窗口是同一个上限）。
  trimStreamState();
  // `live_deltas` = 被人类记录流裁掉、但牌面已公开的标签增删（网页展示通道）。
  // 与可见记录按 seq 合并后走同一条逐事件回放；它们不写事件流 / 舞台。
  const incoming = mergeLiveDeltas(body.records || [], body.live_deltas || []);
  // `display_facts`（ui-live-refresh 票 02 修复 2）= **没有 delta 的派生显示字段**的逐步骤
  // 取值。它们**不占回放步**，而是挂到"它所属的那条记录"上（采样于那一步之后 ⇒ 应用在
  // 那条记录之后），与那条记录共用同一次重绘。
  attachDisplayFacts(incoming, displayFactRecords(body));
  // 显示事实的**投递计数**游标：下一次取增量原样带回，服务端据此只发新事实。
  state.displaySeq = Math.max(0, Number(body.display_seq) || 0);
  const newSession = viewStartsNewSession(body);
  // **开局批** = 换会话 + 带 `header`（无 seq 的那条只在 `after < 0` 的建局响应里下发）。
  // 用它认出"这一批从发牌前开始"，而不是看"这一页之前有没有打过一局"——两条路必须走
  // 同一份初始化、同一个节奏（M6 报障 2）。
  const opening = newSession && incoming.some((record) => record.kind === "header");
  // U3-01 / D5.5：换会话时舞台整块作废——上一局的牌不能在**回放新局记录之前**还留在台上。
  if (newSession) {
    state.effectCards = [];
    state.revealedCards = [];
    // U3-04：上一局还没触发的技能发动事件不属于新局（DOM 侧由 commit 的清屏兜底）。
    state.skillEvents = [];
    // damage-fx 票 05：伤害演出同理——上一局那一剑不能劈在新局的牌桌上（覆盖层与在播演出
    // 由 commitView 换会话时显式调 `clearDamageFx()` 摘掉，票 09 起不再经动效层转手）。
    state.damageEvents = [];
    // chain-death-voices 票 03：上一局还没到点的卡牌事实不属于新局（语音本身由 commitView
    // 换会话时 `SgsCardVoice.stopAll()` 收掉）。
    state.cardEvents = [];
    // vital-fx 票 01：失去体力事实同理——上一局那条墨不属于新局（消费它的是票 02 的
    // 独立排播层，本票只把事实落进 state）。
    state.lifeLossEvents = [];
    // U3-05：live view 是"上一份提交视图的派生副本"，换会话后没有上一份可派生。
    state.liveView = null;
    // 票 05：事件流的「无懈窗口」合并记账按帧号（`window_frame`）索引，帧号跨局会重复
    // ⇒ 换会话时清掉，否则新局第一扇窗口会并进上一局那条已经不该显示的条目里。
    resetWindowLogs($("t-log"));
  }
  // U3-04 / D8.2：技能发动是随视图下发的派生通道；先收进队列，回放走到对应的记录 seq
  // 才演出（飘名 / 白光 / 线），而不是响应一到就全部同时出现。
  for (const event of body.skill_events || []) state.skillEvents.push(event);
  // damage-fx 票 05：伤害事实是**第二条派生通道**（服务端在每次伤害时下发
  // `{seq, source, target, amount, nature}`），与 `skill_events` 同姿态——先收进队列，
  // 回放走到那条记录的 `seq` 才挥剑。默认空表由这里建（`state` 的其余字段在 core.js）。
  if (!Array.isArray(state.damageEvents)) state.damageEvents = [];
  for (const event of body.damage_events || []) state.damageEvents.push(event);
  // chain-death-voices 票 03：卡牌使用 / 打出事实是**第三条派生通道**（服务端在每次使用 /
  // 打出时下发 `{seq, seat, card_type, card_name, gender, virtual}`），与 `damage_events`
  // 同姿态——先收进队列，回放走到那条记录的 `seq` 才出声。
  if (!Array.isArray(state.cardEvents)) state.cardEvents = [];
  for (const event of body.card_events || []) state.cardEvents.push(event);
  // vital-fx 票 01：失去体力事实是**第四条派生通道**（服务端在每次失去体力时下发
  // `{seq, seat, amount}`），与 `damage_events` 同姿态——先收进队列。**本票不排播、不画**：
  // 只把事实落进 `state`，由票 02 的独立排播层按记录 `seq` 消费（伤害演出一个字不动）。
  if (!Array.isArray(state.lifeLossEvents)) state.lifeLossEvents = [];
  for (const event of body.life_loss_events || []) state.lifeLossEvents.push(event);
  // U1-07：快照的处理区已经是**产物**（服务端把转化来源组换成了虚拟牌），回放期间也要
  // 按同一份映射把"来源牌进处理区"渲染成产物，否则用户会先看到两张源牌再被快照纠正。
  state.processingProducts = processingProductMap(
    body.snapshot ? body.snapshot.processing : [],
  );
  if (!incoming.length) {
    // 纯刷新没有回放：技能发动的演出按响应立即触发（不等一个不存在的记录步）。
    fireSkillEventsThrough(Infinity);
    fireDamageEventsThrough(Infinity);
    // vital-fx 票 02：失去体力的事实与伤害事件同姿态——纯刷新也要按响应立即消费。
    fireHpLossEventsThrough(Infinity);
    commitView(body);
    return;
  }
  const previous = newSession
    ? []
    : (state.view && state.view.snapshot ? state.view.snapshot.processing || [] : []);
  state.effectCards = mergeEffectCards(previous);
  // U3-05（D11）：逐事件派生的 live view 从中途的上一份提交视图起算（深拷贝，绝不改权威
  // 快照）。**开局批**没有"中途底稿"：它的底稿是 :func:`openingLiveView` 倒推出来的发牌前
  // 骨架，故新局的开局批也 `live = true`——表体随记录逐条长出来，而不是等 commit 才出现。
  const live = opening || state.view !== null;
  if (live) state.liveView = opening ? openingLiveView(body) : cloneLiveView(state.view);
  if (live && incoming.some(isSkillDeltaRecord)) adoptSkillLists(state.liveView, body);
  state.playback = {
    body, records: incoming, index: 0, querySeat: state.lastQuerySeat, live, opening,
  };
  $("btn-submit").disabled = true;
  $("btn-cancel").disabled = true;
  // 回放期间倒计时与右端动作都收起：`state.view` 还停在上一条询问，继续画会误导。
  resetCountdown();
  resetRightActions();
  // 回放期间**整个询问面让位**（提示文本 / 技能串 / 目标提示 / 选项格 / 询问弹窗）：它们
  // 属于"下方询问界面"的上一问，等这一批播完、新询问提交后再画。只清一两样会让其余几样
  // 挂到下一次 `renderPending` 才消失（M6 实机报障）。描述文本不在这里清——它由
  // `renderProcessing` 按效果区逐事件改写；五谷的持久亮牌也留着，见 `blankAskSurface`。
  blankAskSurface();
  // 开局：先把新局的骨架画上（旧局的武将 / 手牌 / 判定装备一帧都不留在屏上），再按正常
  // 步长逐条演。顶栏的种子 / 座位也属于新局，不等 commit 再换。
  if (opening) {
    $("t-seed").textContent = String(body.seed);
    $("t-seat").textContent = String(body.human_seat);
    renderTable();
  }
  // 第一条也等满一个基准步长再出现；之后每条记录的间隔由**上一条**的 `delayOf` 算
  // （U4-02 / D7：base + 该条动画时长 × base/100）。
  // 票 07：首步同样过暂停门（与 `stepPlayback` 收尾同一个出口）。用户的口径是"3、4+ 伤害档
  // 动画**演出期间**游戏事件处理暂停"——窗口是整个演出寿命，不是"产生它的那一批记录"：
  // 上一批的演出还挂着近 2s 尾巴时（`finishPlayback` 之后轮询立刻送来新记录）新批照样要等。
  schedulePlaybackStep(state.eventDelayMs);
}

/** U4-02（spec D7 / D8）：一条记录占用的回放步长 = `base + dur(记录) × (base / 100)`。
 *
 * **纯函数**：`base` 由调用方给（缺省读当前档位），不读时钟、不改 `state.eventDelayMs`
 * ——原型在播放里改档位是 bug，档位只由按钮 / `anim.ini` 的初始档决定。
 * `dur` 来自 `anim.js:durationOf`（没有动画 / 不飞 / 未登记 = 0），故阶段 / 回合飘字这类
 * 非阻塞记录只吃 `base`；`base` 同时是下限与动画缩放因子（50ms 档 0.5×、300ms 档 3×）。
 * 回放页不接飞行层（D15），仍只用 `state.eventDelayMs`。
 */
function delayOf(record, base = state.eventDelayMs) {
  return Math.round(base + durationOf(record) * base / 100);
}

/* ---------------------------------- 回放暂停（damage-fx-natures 票 07） */

/** 暂停期间的轮询间隔（毫秒）：每唤醒一次重读判据，仍要暂停就再排一次。
 *
 * 与伤害演出的**错开** 120ms 只是同值巧合：一个是"相邻开演隔多久"，一个是"多久回头看
 * 一眼判据"，将来会各自调——别抽成同一个常量。
 */
const PLAYBACK_HOLD_POLL_MS = 120;

/** 回放暂停的**登记表**（票 07）：键 = 提供方名，值 = 只读判据 `() => 原因串`。
 *
 * 为什么是登记表、而不是在 `stepPlayback` 里直接问伤害层：3 / 4 档伤害只是**第一个**要冻住
 * 记录流的演出——回复体力 3 档、以后别的重演出同形，各自登记一条即可。`session.js` 因此
 * 不认识任何具体演出：它不认档位号、不认家族、也**不读** `damagePlays`。
 *
 * 判据必须是**只读**的（被唤醒时会被反复调用），且只回答"现在该不该冻住记录流"：
 * 推进、清理、排播那些事归各演出自己。**没有兜底上限**（票 09）：判据一直为真就一直等，
 * 故判据只能看会自然清空的状态。
 */
const playbackHoldProbes = {};

/** 登记一条「现在该暂停回放吗」的只读判据；同名就地替换（先登记者先命中）。 */
function registerPlaybackHold(name, probe) {
  if (!name || typeof probe !== "function") return;
  playbackHoldProbes[name] = probe;
}

/** 现在该不该暂停回放：返回原因串（`"damage:3"` 这种，写进 DOM 读数），没有则 `""`。
 *
 * 每次被问都**重新读**判据——演出可能被版式作废 / 换局提前收掉，也可能又起一条新的。
 * 单条判据抛错按"不暂停"处理：以后新登记一条坏判据时，页面不能跟着永久停下。
 */
function playbackHoldReason() {
  for (const name of Object.keys(playbackHoldProbes)) {
    let reason = "";
    try {
      reason = playbackHoldProbes[name]();
    } catch (error) {
      reason = "";
      if (typeof console !== "undefined" && console.warn) {
        console.warn(`[playback-hold] ${name} 的判据抛错，按不暂停处理：${error}`);
      }
    }
    if (reason) return String(reason);
  }
  return "";
}

/** 把"回放暂停"的读数写进页面根：`data-playback-hold` = 原因串或空（现在为什么冻住）。
 *
 * 与 `?audiotest=1` 的 `<html data-audio>` 同一姿态：实机 / 烟测一眼读，不必开 DevTools
 * 的作用域。**只写读数**，不驱动任何行为（判据才是行为落点）。
 */
function markPlaybackHold(reason) {
  if (typeof document === "undefined" || !document || !document.documentElement) return;
  const root = document.documentElement;
  if (typeof root.setAttribute !== "function") return;
  root.setAttribute("data-playback-hold", reason || "");
}

/** 收掉暂停读数（判据放空 / 冲刷 / 回放收尾）。 */
function resetPlaybackHold() {
  markPlaybackHold("");
}

/** 排下一步回放（票 07）：先等 `base`，**唤醒时**再问一句"该暂停吗"。
 *
 * 为什么问在唤醒而不是排程那一刻：演出随时可能起来（同批错开 120ms 的后续几条）或提前收掉
 * （版式作废 / 换局），排程时算一次就会把一个过期的答案用到几百毫秒之后。
 *
 * `immediate` 路径**不经过这里**（`stepPlayback(true)` 由 `flushPlayback` / 烟测直接连跑）：
 * 暂停只服务"人眼看"的回放，测试与收尾不该被演出拖住。
 */
function schedulePlaybackStep(base) {
  playbackTimer = window.setTimeout(stepPlaybackWhenFree, Math.max(0, Number(base) || 0));
}

/** `schedulePlaybackStep` 的唤醒体：判据为空就继续推进，否则再排一次轮询。
 *
 * **没有上限、也不读墙钟**（用户 2026-11-10 的裁定："把这个上限取消，3 档、4+ 档伤害时动画
 * 暂停，不允许动画期间进行任何事件"）：判据为真就一直每 `PLAYBACK_HOLD_POLL_MS` 重问一次，
 * 直到它自然放空。
 *
 * 安全性靠判据自身：它只读两张**会自然清空**的表——`damagePlays`（到点摘层 / 版式作废 /
 * 换局清场都摘）与 `damageQueue`（入队处有 cap、`clearDamageFx` 清空），没有任何"等够久
 * 就放行"的时钟分支。代价是判据若因 bug 永久为真，事件流会一直停——这是用户明确选的取舍
 * （宁可不放行，也不许动画期间插事件）。
 */
function stepPlaybackWhenFree() {
  playbackTimer = null;
  const reason = playbackHoldReason();
  if (!reason) {
    resetPlaybackHold();
    stepPlayback(false);
    return;
  }
  markPlaybackHold(reason);
  schedulePlaybackStep(PLAYBACK_HOLD_POLL_MS);
}

/** 这一条记录会不会改**表体**（座位 / 手牌 / 舞台以外的那一层）——票 01 的快速判据。
 *
 *  背景：回放每步都 `renderTable()` 会把整桌重建一遍（一局 4745 条记录 = 4745 次整桌
 *  重建）。判据只看这条记录**自己**能不能改快照：回放期 `tableViewForRender()` 已经把
 *  `pending` 收成 null，故询问 / 应答 / 结算帧 / 终局 / 大部分时机都改不了表体——它们只
 *  动事件流与舞台（舞台另由 `renderProcessing()` 重画）。
 *
 *  **拿不准的一律 `true`**：宁可多重建一次，也不许少画一帧。判据与 `applyLiveDelta` 的
 *  分支**同源**（那边没有分支的 delta 就改不了快照），新增一条 delta 分支时要同时看这里。
 *  不改 DOM 形状：跳过的只是"重建"，其余入口一个不动。
 */
const NON_TABLE_DELTAS = new Set([
  // 纯展示：亮牌进舞台展示组、拼点进拼点弹窗，都不写快照。
  "card_reveal",
  "pindian",
]);
/** 只在**表体**之外留痕的时机：其余的时机都会改快照（回合 / 阶段 / 阵亡），必须重建。 */
const TABLE_TIMINGS = ["TURN_BEFORE_START", "TURN_START", "DEATH_BEFORE_START"];

function recordTouchesTableView(record) {
  if (!record) return true;
  // 标签增删改的是手牌 / 舞台卡上的标签（`liveUpdateCardTags` 写 live view）⇒ 必须重建。
  if (record.live_only) return true;
  const kind = record.kind;
  // 询问 / 应答 / 结算帧 / 终局都不写快照：它们在回放期只进事件流，舞台走 `applyEffectDelta`。
  if (kind === "query" || kind === "action" || kind === "frame" || kind === "game_end") {
    return false;
  }
  if (kind === "timing") {
    const timing = String(record.timing || "");
    // `PHASE_*` 改顶栏阶段；三条具名时机改回合 / 阵亡（见 `applyLiveDelta`）。
    return timing.startsWith("PHASE_") || TABLE_TIMINGS.includes(timing);
  }
  if (kind === "delta") {
    return !NON_TABLE_DELTAS.has(String(record.delta_type || ""));
  }
  return true; // `header` / 未知 kind：交给整桌重建兜底
}

/** 回放一步：画一条事件（事件流 + 效果区），然后排下一步。
 *
 * ``immediate`` = 测试 / 批量冲刷用：不排计时器，由 :func:`flushPlayback` 直接连跑。
 */
function stepPlayback(immediate) {
  const playback = state.playback;
  if (!playback) return;
  if (playback.index >= playback.records.length) {
    finishPlayback();
    return;
  }
  const record = playback.records[playback.index];
  const liveOnly = Boolean(record.live_only);
  let flightPlan = null;
  if (!liveOnly) {
    if (record.kind === "query") {
      playback.querySeat = record.query.actor;
      state.lastQuerySeat = record.query.actor;
    }
    state.logRecords.push(record);
    // 票 01：本地记录序列只留最近 `LOG_ITEM_LIMIT` 条（与 `#t-log` 同一个上限）。
    trimStreamState();
    // 简易档铺服务端叙事（与记录同 seq 的条目在这一步出现）；完整两档铺记录。
    if (logMode() === "simple") appendNarrativeThrough(record.seq);
    else appendLog(record, playback.querySeat);
    // 同一条记录的两个舞台入口共用**一个** now：`pop` 那一批的 `fadeAt` 是
    // `now + 停留` 算出来的，两个入口各调一次 `Date.now()` 就可能跨毫秒 ⇒ 同一批
    // "同刻淡出"差 1ms（烟测 U3-01t 的 `fadeAt` 相等断言因此偶发红）。
    const stageAt = stageNow();
    state.effectCards = applyEffectDelta(record, state.effectCards, undefined, stageAt);
    state.revealedCards = applyRevealDelta(record, state.revealedCards, stageAt);
    // 拼点弹窗（huo-taishici-xunyu 票 04）：拼点是不使用不打出的对抗事件，结果只有
    // `pindian` delta 承载 ⇒ 弹窗跟这条记录走。只读、全场可见（AI 打 AI 也弹）；停留
    // 3 秒由 `pindian.js` 自己排，不进回放步长。
    applyPindianDelta(record);
    // M6 报障 1：亮牌弹窗跟**源结算帧**走——那个帧 pop 的这条记录一到就收起，不等整批
    // 播完（五谷的取牌步 / 无懈窗口跨多个响应，最后一批能有几十条记录）。
    syncPlaybackDialog(record);
    // U4-01（D1 / D2）飞行层 / U4-04（D10）阶段飘字：两条动效都在**重绘之前**规划。
    // 飞行器起飞前量**源**——这一步还没重绘，舞台卡 / 手牌 / 槽位都还在原处，落点等
    // 重绘之后再量并同步起飞（见本函数末尾）；阶段飘字只放画面：不排计时器、不改步长
    // （`durationOf` 对非牌移动恒为 0，见本函数末尾的 `delayOf`）。
    // D7：把当前档位作为**动画缩放因子**交给飞行层；`delayOf` 的等待在末尾用同一个 base，
    // 两处同源，50ms 档才会"动画与间隔一起缩"。
    flightPlan = animPlanFlight(record, state.eventDelayMs);
    animPlanPhase(record);
  }
  // U3-05（D11）：每条记录先把**可见**变化写进 live view，再**从它**重绘全表——手牌数 /
  // 自己的手牌面 / 体力 / 装备与判定区 / 牌堆与弃牌数 / 横置 / 酒 / 身份 / 阵亡 /
  // 回合与阶段都随事件走，不再等整批播完提交。标签 delta 只走这一条，不写事件流。
  if (playback.live && state.liveView) {
    applyLiveDelta(record, state.liveView);
    // 显示事实（ui-live-refresh 修复 2）：挂在这条记录上的事实在**同一次重绘**里生效——
    // 不再为每个事实排一个自己的回放步（那会多花一个步长、还多重建一次整桌）。
    // 值没变的事实返回 false（同值判据），不触发重建。
    let factChanged = false;
    for (const fact of record.displayFacts || []) {
      if (applyDisplayFact(fact, state.liveView)) factChanged = true;
    }
    // renderTable 末尾就是舞台重绘（`renderProcessing`），播放期也走同一条口。
    // 票 01：改不了表体的记录（询问 / 应答 / 结算帧 / 大部分时机）只重画舞台，不整桌重建。
    if (factChanged || recordTouchesTableView(record)) renderTable();
    else renderProcessing();
  } else if (!liveOnly) {
    // 开局批次没有上一份提交视图可派生：表体等 commit，舞台照旧逐事件派生。
    renderProcessing();
  }
  // U4-01（D1 / D2）：重绘之后才量**落点**（新槽位 / 新舞台卡这时才在 DOM 里），并同步
  // 起飞——不排计时器、不改 `state.eventDelayMs`；节奏见末尾的 `delayOf(record)`。
  if (flightPlan) animLaunchFlight(flightPlan);
  if (!liveOnly) {
    // U3-03（D8.1 / D13）：决策点高亮、使用牌目标线、体力闪。回放页不走这条路径（D14）。
    // 放在表体重绘**之后**：描边 / 体力闪要落在新座位节点上（`syncTransientEffects` 兜住
    // 未到寿命的那几个，这里补的是这一条记录新产生的那一个）。
    applyRecordEffects(record, playback.querySeat);
    // vital-fx 票 02：**回复体力**的事实源就是这条记录（`hp_change` 里 `after > before`），
    // 所以在 `applyRecordEffects` 之后（`flashHeroHp` 的 0.4s 闪保留作兜底）紧接着排播：
    // 判据只看记录、不认技能名，与本函数下一段的其它演出钩子同一个姿态。
    fireVitalEventsThrough(record);
    // A1：洛神连续判定——弹窗已开着时，用刚进处理区的判定牌续上同一个窗口。
    // 只读 JUDGE 帧 + `card_move(reason=JUDGE)` 这两条服务端事实，不认技能名；舞台已由
    // `applyEffectDelta` 照常派生（弹窗不往舞台上加卡）。
    const judgedCard = judgmentCardFromRecord(record);
    if (judgedCard) refreshJudgmentReveal(judgedCard);
    // U3-04（D9）：技能发动的演出挂在**产生它的那条记录**的 seq 上。
    fireSkillEventsThrough(record.seq);
    // damage-fx 票 05：伤害演出挂在**同一条 seq 游标**上（这是它唯一的触发点：不在
    // `applyLiveDelta` 里、也不在提交视图时触发）。
    fireDamageEventsThrough(record.seq);
    // vital-fx 票 02：**失去体力**的演出挂在**同一条 seq 游标**上（事件源是票 01 的
    // `state.lifeLossEvents` 缓冲，与 `damage_events` 并列）。**永不冻回放**：这里没有任何
    // 时停登记会因它变真（家族的 `holdsAt` 是 `Infinity`），演出与后续记录并行是口径。
    fireHpLossEventsThrough(record.seq);
    // chain-death-voices 票 03：卡牌语音挂在**同一条 seq 游标**上（到点的事实逐条播，其余
    // 留在 `state.cardEvents` 里等下一批）。**每条自起一路**、可重叠不打断，所以这里既不
    // 排播也不等待——出口只有 `SgsCardVoice.play`。
    fireCardEventsThrough(record.seq);
    // chain-death-voices 票 01：铁索连环的横置 / 重置演出**紧挨着**伤害演出的下一行。
    // 与伤害不同，它读的是**整条记录**（判据 `delta_type === "chained_change"`，座位在
    // `record.seat`）；首批用它自己的 `state.chainSeq` 游标压掉历史，打开一局已横置的对局
    // 不会补播入链。**无时停**：这里没有任何登记，演出与后续记录并行是口径。
    fireChainEventsThrough(record);
    // chain-death-voices 票 02：阵亡演出也挂在**同一条 seq 游标**上（触发判据只看记录：
    // `kind === "timing"` ∧ `timing === "DEATH_BEFORE_START"`，座位 = `record.actor`；
    // **不用** `hp_change` 到 0 去猜——护甲全吸收 / 濒死被救都会猜错）。**无时停**：这里没有
    // 任何登记，2.2s 里记录流照常推进是用户口径（后续事件画在"正在碎的牌"旁边是预期）。
    // 上面的 `renderTable()` 已经把这张牌画成"已阵亡"；演出层自己贴 `data-death-fx`
    // 把灰与「阵亡」标签压到收尾那一刻（三条覆盖规则，见 `style.css`）。
    fireDeathEventsThrough(record.seq);
    scrollLogToBottom();
  }
  playback.index += 1;
  // 票 07：下一步先过暂停门（登记表里有人要求暂停时记录流冻住，放行后再一次补齐）。
  // `immediate` 侧不排计时器、也不问判据——flushPlayback 的连跑与烟测的 step() 走那条。
  if (!immediate) schedulePlaybackStep(delayOf(record));
}

/** 把整批回放**立刻**跑完（测试与"收到新视图前先收尾"用）。 */
function flushPlayback() {
  if (playbackTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(playbackTimer);
    playbackTimer = null;
  }
  resetPlaybackHold();
  let guard = 0;
  while (state.playback && guard < 100000) {
    stepPlayback(true);
    guard += 1;
    if (playbackTimer !== null) {
      if (window.clearTimeout) window.clearTimeout(playbackTimer);
      playbackTimer = null;
    }
  }
}

function finishPlayback() {
  const playback = state.playback;
  if (!playback) return;
  state.playback = null;
  if (playbackTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(playbackTimer);
    playbackTimer = null;
  }
  // 票 07：整批播完就收暂停读数（演出自己的帧与声不受影响，见 `schedulePlaybackStep`）。
  resetPlaybackHold();
  // 兜底：旧录像 / 合成载荷若没有同 seq 的记录，播完立即触发，不让事件跨到下一批。
  fireSkillEventsThrough(Infinity);
  fireDamageEventsThrough(Infinity);
  fireHpLossEventsThrough(Infinity);
  commitView(playback.body);
  // 票 14：**开局批播完、还没有挂起询问** ⇒ 立刻再取一次增量，不等 2 s 轮询。
  // 建局响应止于"游戏开始时"（L0 边界），人类第一问 / AI 的第一回合在下一份增量里；
  // 等轮询会让玩家盯着"（没有待应答的询问）"空两秒，白等掉的正是这一票要收的那段观感。
  // 判据只看这份载荷自己：开局批 ∧ 没询问 ∧ 没终局（有询问 = 已经停在暂停点上，无事可拉）。
  if (playback.opening && !playback.body.pending && !playback.body.finished) {
    refresh();
  }
}

/** 逐目标结算的 C 段时机：它们的 ``targets`` 是**当前正在结算的那个目标**。
 *
 * 不含 ``RESOLVE_AFTER_END``：那一条带的还是整张牌的目标列表，且卡牌此时已在离开处理区，
 * 拿它更新"当前目标"会把下一张牌错认成目标。
 */
const RESOLVE_TARGET_TIMINGS = new Set([
  "RESOLVE_START",
  "RESOLVE_BEFORE_EFFECT",
  "RESOLVE_EFFECT",
  "RESOLVE_AFTER_EFFECT",
  "RESOLVE_END",
]);

/** 来源牌组的稳定键：按 id 排序后拼成 `"2,18"`（顺序不是判据，牌组才是）。
 *
 * 服务端 `display.py:_processing_display` 用同一条"整组来源"规则把实体牌换成产物；
 * JS 与 Python 没有共享常量的通道，这里是**唯一**的 JS 副本。改分组规则 = 两头一起改。
 */
function sourceGroupKey(cardIds) {
  return (cardIds || []).map(Number).sort((a, b) => a - b).join(",");
}

/** 处理区里的**产物**映射（U1-07）：`{来源牌组 → 虚拟 / 转化牌}`。
 *
 * 引擎的处理区只装实体牌；服务端展示层把正在结算的转化牌换成产物（带
 * `source_cards` 字段）。回放期间的 `card_move` 仍是来源实体牌，故用这份映射把
 * "进处理区"的那组来源牌画成产物。纯虚拟牌没有来源组，不在这张表里。
 */
function processingProductMap(cards) {
  const map = {};
  for (const card of cards || []) {
    const sources = card && card.virtual_sources;
    if (!sources || !sources.length) continue;
    map[sourceGroupKey(sources)] = card;
  }
  return map;
}

/** 这一批 `card_move` 是否正好是一组转化来源：是则返回它的产物，否则 `null`。 */
function processingProductFor(cards, products = state.processingProducts) {
  if (!products) return null;
  return products[sourceGroupKey((cards || []).map((card) => card.card_id))] || null;
}

/** 公开亮牌的逐事件派生（M5-08a / U3-01）：``card_reveal`` 进**展示组**。
 *
 * 亮牌不移动、也不写任何「已知」状态；它只在**中央舞台 + 事件流**留痕。展示项挂到
 * 承载帧（``frame_id``）：``pop`` 到达后开始 1s 淡出（D5），不再瞬删。来源取
 * ``{seat: 亮出者, zone: "hand"}``——底行因此写得出「1·周瑜·手牌」（D10）。
 * 服务端已按展示契约补好牌面字段（``visible_record``）。
 *
 * 牌组作为参数传入、返回值就是新牌组：牌桌与回放页各持一份。
 *
 * ``now`` 是**测试用的时钟注入**（缺省 ``stageNow()``）：两条列表的 pop / 入台要在同一
 * 个时刻上比较时，测试传同一个 ``now``，不读真实时钟、也不等计时器（D15 / 标准 §4）。
 */
function applyRevealDelta(record, cards = state.revealedCards, now = stageNow()) {
  const fromZones = stageFromZoneMap(cards || []);
  const held = (cards || []).filter((item) => stageLane(item, now) !== "gone");
  // 来源表跟着牌组走（D2 / D10）：展示项也要能在"从快照补项"时找回首次来源。
  stageFromZones.set(held, fromZones);
  if (!record) return held;
  if (record.kind === "frame") {
    if (record.op === "pop") stageStartFade(held, record.frame_id, now);
    return held;
  }
  if (record.kind !== "delta" || record.delta_type !== "card_reveal" || !record.card) return held;
  const card = record.card;
  const frameId = stageFrameOf(record);
  const existing = held.find((item) => String(item.card_id) === String(card.card_id));
  if (existing) {
    if (String(existing.stage && existing.stage.frameId) === String(frameId)) return held;
    // 同一张牌在另一帧再次亮出：撤旧项，新项重新落位（D5.6）。
    held.splice(held.indexOf(existing), 1);
  }
  const fromZone = { seat: record.seat, zone: "hand" };
  stageRememberFromZone(held, card.card_id, fromZone);
  // D9：展示项也记下产生它的这条亮牌记录的牌数（今天一条 `card_reveal` 记录一张），
  // 换组 / 跨 commit 不重算。
  const batch = Array.isArray(record.cards) && record.cards.length ? record.cards.length : 1;
  held.push({
    ...stageItem(card, {
      lane: "showcase",
      frameId,
      batch,
      enteredAt: now,
      fromZone,
    }),
    revealedBy: record.seat,
  });
  return held;
}

/** 舞台的逐事件派生（U3-01 / D2 / D6）：``card_move`` / ``card_reveal`` / 帧 ``push``、``pop``。
 *
 * - **处理中**：`snapshot.processing` + ``card_move`` 且 `to_zone.zone === "processing"`；
 * - **展示**：``card_reveal`` 进展示组（火攻）；或「处理区 → 隐藏区」且承载帧的
 *   ``frame_kind === "SKILL"``（反间，D6）；
 * - **弃牌**：``card_move`` 且 `to_zone.zone === "discard"` 且原因 = 弃置 / 重铸 / 装备
 *   替换 / 拆顺；使用（USE）与判定（JUDGE）**不进弃牌组**，只从处理中淡出。
 *
 * **寿命**（D5 / D9）：有承载帧的展示 / 弃牌停在帧 ``pop`` 后 0.5(N+1) 秒再 1s 淡出；
 * 处理中的牌离开处理区即 1s 淡出（U3 D5 的离区分支，D9 不改它）；无帧的弃牌入台
 * 0.5(N+1) 秒停留 + 1s 淡出；任何项兜底 30s。N 来自**产生该舞台项的那条记录**，
 * 记在 ``item.stage.batch`` 上，换组 / 跨 commit 都不重算。换组沿用同一条舞台项
 * （不重新落位）；淡出期间同一张牌再入台 = 撤旧项、重新落位（D5.6）。
 *
 * **承载帧的 kind 来自记录流**（D6）：`frame_kind` 只随 ``FrameRecord(push)`` 下发，
 * 这里把它记在**这份牌组自己**的帧表上（见 :func:`stageFrameKindOf`）——前端不写
 * "哪个技能该展示"的规则。
 *
 * **转化来源（U1-07）**：一组来源牌进处理区时画的是产物（`state.processingProducts`），
 * 不是来源牌本身；离场时也按同一组来源把产物移走。事件流仍是引擎原文。
 * 回放页传 `products = null`：录像里没有"当前快照产物"这份展示派生，按原始 `card_move` 画。
 *
 * **牌组作为参数传入、返回值就是新牌组**：牌桌与回放页各持一份，派生函数不读 / 不写
 * ``state.effectCards``。
 *
 * ``now`` 是**测试用的时钟注入**（缺省 ``stageNow()``）：两条列表的 pop / 入台要在同一
 * 个时刻上比较时，测试传同一个 ``now``，不读真实时钟、也不等计时器（D15 / 标准 §4）。
 */
function applyEffectDelta(
  record,
  effectCards = state.effectCards,
  products = state.processingProducts,
  now = stageNow(),
) {
  const previous = effectCards || [];
  const frameKinds = stageFrameKindMap(previous);
  const fromZones = stageFromZoneMap(previous);
  let held = previous.filter((item) => stageLane(item, now) !== "gone");
  stageFrameKinds.set(held, frameKinds);
  // 来源表跟着牌组走（D2 / D10）：入台移动与"从快照补项"可能隔着一次 commit。
  stageFromZones.set(held, fromZones);
  if (!record) return held;
  if (record.kind === "timing") {
    applyUseTiming(record, held);
    applyConvertedProduct(record, held);
    return held;
  }
  if (record.kind === "frame") {
    // D6：`frame_kind` 只在 push 上；记下来供离区那一步查（跨 commit / 跨批次有效）。
    if (record.op === "push" && record.frame_kind) {
      frameKinds.set(String(record.frame_id), String(record.frame_kind));
    }
    if (record.op === "pop") {
      // **JUDGE 帧不按 pop 淡出**：判定牌的每一次离区都有自己的 `card_move`（进弃牌堆 /
      // 改判者手牌 / 被【天妒】获得），帧 pop 时**还留在处理区**的那张是技能有意留着的
      // （【慧识】的连续判定：判定牌一直留在效果区到流程终止）。按 pop 淡出会让它们一张张
      // 消失（用户 2026-11-14 报障；【荐言】那种没有逐张帧 pop 的形态才是预期表现）。
      if (frameKinds.get(String(record.frame_id)) !== "JUDGE") {
        stageStartFade(held, record.frame_id, now);
      }
    }
    return held;
  }
  if (record.kind !== "delta" || record.delta_type !== "card_move") return held;
  const cards = record.cards || [];
  const from = record.from_zone || {};
  const to = record.to_zone || {};
  const frameId = stageFrameOf(record);
  // D9：同一条移动的 N 张一起入台 ⇒ 停留 0.5(N+1) 秒。N 与飞行层同一个口径
  // （可见牌 = `cards.length`，隐藏区 = `card_count`）；记在创建出的舞台项上，之后不重算。
  const batch = Math.max(1, liveMoveCount(record));
  const carryingFrameKind = stageFrameKindOf(held, frameId);
  const byId = (cardId) => held.find((item) => String(item.card_id) === String(cardId));
  // 转化牌：离场的是它的来源实体牌，台上那一条是**产物**。来源牌不再单独落位。
  const consumed = new Set();

  if (from.zone === "processing") {
    const ids = new Set(cards.map((card) => String(card.card_id)));
    for (const item of held) {
      const sources = item.virtual_sources || [];
      const matches = ids.has(String(item.card_id))
        || (sources.length > 0 && sources.every((source) => ids.has(String(source))));
      if (!matches) continue;
      for (const source of sources) consumed.add(String(source));
      if (to.zone === "discard" && DISCARD_REASONS.has(record.reason)) {
        // 换组沿用同一条舞台项：来源取**首次入台**的 from_zone，寿命改挂这次结算帧。
        stageAttachDiscard(item, frameId);
        continue;
      }
      if (HIDDEN_ZONES.has(to.zone) && carryingFrameKind === "SKILL") {
        // 反间（D6）：处理区 → 隐藏区且承载帧是 SKILL 帧 ⇒ 转进展示组。沿用同一条
        // 舞台项（入台时刻 / 首次来源都不动，不重新落位），活到该 SKILL 帧 pop 后淡出。
        item.stage.lane = "showcase";
        item.stage.frameId = frameId;
        item.stage.fadeAt = null;
        continue;
      }
      // 离场（使用 / 判定 / 交给别人 / 五谷不命中…）→ 1s 淡出，不再瞬删。
      if (item.stage.fadeAt === null || item.stage.fadeAt === undefined) {
        item.stage.fadeAt = now;
      }
    }
  }

  if (to.zone === "processing") {
    const product = processingProductFor(cards, products);
    const added = product ? [product] : cards;
    for (const card of added) {
      const existing = byId(card.card_id);
      if (existing) {
        // 淡出期间同一张牌再次入台：撤旧项、新项重新落位（D5.6）。
        existing.stage.lane = "processing";
        existing.stage.frameId = frameId;
        existing.stage.batch = batch;
        existing.stage.enteredAt = now;
        existing.stage.fadeAt = null;
        existing.stage.fromZone = from;
        stageRememberFromZone(held, card.card_id, from);
        existing.source = record.reason === "USE" ? record.owner : null;
        existing.targets = null;
        existing.currentTarget = null;
        continue;
      }
      stageRememberFromZone(held, card.card_id, from);
      held.push(stageItem(card, {
        lane: "processing",
        frameId,
        batch,
        enteredAt: now,
        fromZone: from,
        source: record.reason === "USE" ? record.owner : null,
      }));
    }
  }

  if (to.zone === "discard" && DISCARD_REASONS.has(record.reason)) {
    for (const card of cards) {
      if (consumed.has(String(card.card_id))) continue;
      const existing = byId(card.card_id);
      if (existing) {
        // 已从处理中组转来（上面的分支）：这里只补锚点，不重新落位。
        stageAttachDiscard(existing, frameId);
        continue;
      }
      stageRememberFromZone(held, card.card_id, from);
      held.push(stageItem(card, {
        lane: "discard",
        frameId,
        batch,
        enteredAt: now,
        fromZone: from,
        source: record.owner,
      }));
    }
  }
  return held;
}

/** 时机记录里的**转化产物**：这次使用结算时换成了哪张牌（朱雀羽扇 / 转化技）。
 *
 * 服务端在 `USE_ON_USE` 之后把帧里的牌换成产物，其后的每个时机记录都带 `cards = [产物]`
 * （`virtual_sources` 指回来源实体牌）。舞台项是在 `card_move` 时按**来源实体牌**建的，
 * 不换的话台上一直是原牌——用户实机：羽扇把普通【杀】转成火【杀】，舞台仍画普通【杀】。
 *
 * 判据只读记录（不读规则、不查技能表）：产物带 `virtual_sources`、且这些来源正是台上
 * 那张牌 ⇒ 只把**牌面**换成产物。位置 / 使用者 / 目标 / 承载帧仍跟着台上那一条；
 * 之后"离场"的 `card_move` 带的是来源实体牌，靠换过来的 `virtual_sources` 照样命中。
 */
function applyConvertedProduct(record, effectCards = state.effectCards) {
  const now = stageNow();
  const held = (effectCards || []).filter(
    (item) => item.stage && item.stage.lane === "processing" && stageLane(item, now) === "visible",
  );
  for (const product of record.cards || []) {
    if (!product || !Array.isArray(product.virtual_sources) || !product.virtual_sources.length) continue;
    const sources = product.virtual_sources.map(String);
    const item = held.find(
      (entry) => sources.includes(String(entry.card_id))
        || (entry.virtual_sources || []).some((source) => sources.includes(String(source))),
    );
    if (!item) continue;
    const stage = item.stage;
    Object.assign(item, product);
    item.stage = stage;
  }
}

/** 时机记录对描述文本的两个贡献：认领这次使用的目标、更新"当前结算到谁"。
 *
 * 认领只看**还在处理中组里的最后一张牌**（刚进来的那张）且要求使用者与时机 ``actor``
 * 相同、目标还没认领过；认领过一次就不再改，故一次使用的目标只认领自己那一份。
 * 淡出中的牌不属于处理中组（``stageLane`` 已不是 visible），不参与认领。
 */
function applyUseTiming(record, effectCards = state.effectCards) {
  const now = stageNow();
  const held = (effectCards || []).filter(
    (item) => item.stage && item.stage.lane === "processing" && stageLane(item, now) === "visible",
  );
  const card = held[held.length - 1];
  if (!card) return;
  if (record.timing === "USE_DESIGNATE_TARGET") {
    if (card.source === record.actor && (card.targets === null || card.targets === undefined)) {
      card.targets = (record.targets || []).slice();
    }
    return;
  }
  if (RESOLVE_TARGET_TIMINGS.has(record.timing) && (record.targets || []).length) {
    card.currentTarget = record.targets[0];
  }
}

/** 把新快照的处理区并进舞台：同一张牌保留回放期间标注的使用者 / 目标。
 *
 * **只动处理中组**（U3-01 / D5.5）：展示 / 弃牌两组跨 ``commit`` 保留；处理中组里
 * 快照已没有的牌 = 它已经离开处理区——开始 1s 淡出（不再瞬删）；快照里新出现的牌补进来，
 * 其来源从这份列表记着的 `card_id → fromZone` 里补（D2 / D10）——快照本身没有"从哪来"，
 * 不补的话底行会空着（真机验收的【火攻】）。
 * 同一 id 若还挂在展示 / 弃牌组又出现在处理区快照里（洗牌后再次使用），以快照为准落回
 * 处理中组。换会话的清空在 :func:`commitView` 里做，不在这里猜。
 */
function mergeEffectCards(cards) {
  const now = stageNow();
  const frameKinds = stageFrameKindMap(state.effectCards || []);
  const fromZones = stageFromZoneMap(state.effectCards || []);
  const wanted = new Set((cards || []).map((card) => String(card.card_id)));
  const held = new Map();
  for (const item of state.effectCards || []) {
    if (!item || !item.stage) continue;
    if (stageLane(item, now) === "gone") continue;
    held.set(String(item.card_id), item);
  }
  const out = [];
  // 承载帧的 kind / 入台来源跟着牌组走（D2 / D6）：记录与"从快照补项"可能隔着一次 commit。
  stageFrameKinds.set(out, frameKinds);
  stageFromZones.set(out, fromZones);
  for (const item of held.values()) {
    if (item.stage.lane !== "processing") {
      if (!wanted.has(String(item.card_id))) out.push(item);
      continue;
    }
    if (wanted.has(String(item.card_id))) {
      out.push(item);
      continue;
    }
    if (item.stage.fadeAt === null || item.stage.fadeAt === undefined) item.stage.fadeAt = now;
    out.push(item);
  }
  for (const card of cards || []) {
    if (out.some((item) => String(item.card_id) === String(card.card_id))) continue;
    out.push(stageItem(card, {
      lane: "processing",
      // 快照补出来的牌**没有产生它的记录**（D9）⇒ 按 N=1 计；跨 commit 不重算。
      batch: 1,
      enteredAt: now,
      fromZone: stageFromZoneOf(out, card.card_id),
    }));
  }
  return out;
}

/** 效果区（处理中组）的说明文本：有牌才有一行，牌全走了就**立即清空**（用户裁定）。 */
function describeEffect(cards) {
  if (!cards || !cards.length) return "";
  const parts = cards.map((card) => {
    const who = (card.source === null || card.source === undefined)
      ? ""
      : `（${useText(card)}）`;
    return `【${cardName(card)}】${who}`;
  });
  return `处理中：${parts.join("、")}`;
}

/** 展示组的说明（D3 的三段之一）：只写牌名——来源在卡面底行，不重复。 */
function describeReveals(cards) {
  if (!cards || !cards.length) return "";
  return `展示：${cards.map((card) => `【${cardName(card)}】`).join("、")}`;
}

/** 弃牌组的说明（D3 的三段之一）：只写牌名——来源在卡面底行，不重复。 */
function describeDiscards(cards) {
  if (!cards || !cards.length) return "";
  return `弃牌：${cards.map((card) => `【${cardName(card)}】`).join("、")}`;
}

/** 处理区里某张牌的"谁对谁"：逐目标结算时写**当前**目标；否则写全部（去重使用者本人）。 */
function useText(card) {
  const source = card.source;
  const current = card.currentTarget;
  if (current !== null && current !== undefined && current !== source) {
    return `${source}号位 对 ${current}号位 使用`;
  }
  const others = (card.targets || []).filter((seat) => seat !== source);
  return others.length
    ? `${source}号位 对 ${others.map((seat) => `${seat}号位`).join("、")} 使用`
    : `${source}号位 使用`;
}

/** 提交这份视图：装快照 / 游标，重画牌桌与询问（回放播完后的落点）。 */
function commitView(body) {
  // 换会话（开新局 / 从录像回到牌桌）时倒计时锚点必须作废：不同会话的询问序号会重复，
  // 沿用旧锚点会把上一局已经过去的秒数算到新局的第一个询问上。
  // `state.view === null` 也算换会话（首屏 / 测试重开一局）：自动提交的去重键不能跨局沿用。
  const sessionChanged = viewStartsNewSession(body);
  if (sessionChanged) {
    state.pendingClock.seq = null;
    state.pendingClock.receivedAt = 0;
    // U2-07：自动提交的去重键是询问序号；换会话后序号会重复，必须作废。
    state.arrangeAutoSeq = null;
    // qunying 票 11：自动展开的去重键同理（同一局的刷新则保留——玩家取消掉的清单不重开）。
    state.preactivationAutoSeq = null;
    // U1-06：换会话时详情窗口的座位号属于上一局，必须作废（同一局的刷新则保留）。
    state.modal.heroSeat = null;
    // U3-01 / D5.5：舞台三组跨 commit 保留，但换会话必须清空（上一局的牌不能留在台上）。
    state.effectCards = [];
    state.revealedCards = [];
    // U3-03 / D8.1：目标线与行动者描边 / 体力闪是**一次演出**，换会话后不属于新局。
    clearTransientEffects();
    // damage-fx 票 09：伤害演出（覆盖层 / 队列 / 在播 / 屏闪窗口 / 音效声道）是**另一层**的
    // 一次演出，同样不属于新局。这两行原来是 `clearTransientEffects` 内部调的反向钩子
    // （动效层 → 伤害层）；排播搬进 `damage-fx.js` 后改由换局的调用方显式说：
    // 上一局那一剑不能劈在新桌上，也不能在新桌上响完。
    clearDamageFx();
    state.damageEvents = [];
    // chain-death-voices 票 01：铁索的入链 / 解锁是**另一层**的一次演出（自己的覆盖层
    // `#t-chain-fx`、自己的队列 / 计时器 / 常态贴图），换局同样连声音一起收掉。`state.chainSeq`
    // 也在这里清回 `null`——新局首批的记录是历史，不该补播入链。
    clearChainFx();
    // chain-death-voices 票 02：阵亡演出同理（自己的覆盖层 `#t-death-fx`、自己的计时器、
    // 座位上的 `data-death-fx`）。**必须清属性**：覆盖规则留着会让上一局的座位在新桌上
    // 永远不灰（或永远不显示）——spec §硬约束。
    clearDeathFx();
    // ink-table-skin 票 03：技能墨字（自己的覆盖层 `#t-skill-ink`）同理——上一局那张墨
    // 不能留在新桌上。紧挨上面三个 clear*，同一处换局清场。
    if (typeof skillInkFxClear === "function") skillInkFxClear();
    // chain-death-voices 票 03：上一局的卡牌语音不拖进新局（每条语音自己一路 `Audio`，停掉
    // 全部存活的那几路并清表）；队列同时清空（`applyView` 的换会话分支也清一次）。
    SgsCardVoice.stopAll();
    state.cardEvents = [];
    // vital-fx 票 02：体力演出（自己的覆盖层 `#t-vital-fx`、自己的队列 / 计时器 / 音效声道）
    // 是**另一层**的一次演出，换局同样连声音一起收掉：上一局那条回复不能留在新桌上。
    clearVitalFx();
    // vital-fx 票 01：失去体力事实的队列同样属于上一局（与 `state.damageEvents` 同块清）。
    state.lifeLossEvents = [];
  }
  state.view = body;
  // U3-05（D11）：提交即权威——live view 用这份快照整份覆盖（下一批事件从它起算）。
  // 注意它是**副本**（`cloneLiveView`）：回放期逐事件改写的是副本，`state.view` 不动。
  state.liveView = cloneLiveView(body);
  state.cursor = body.cursor;
  state.logSeq = body.log_seq;
  $("t-seed").textContent = String(body.seed);
  $("t-seat").textContent = String(body.human_seat);
  $("btn-takeover").hidden = body.finished;
  // `state.effectCards` 不在这里重设：`renderTable` 会把快照的处理区并进来
  // （`mergeEffectCards`），从而保留回放期间标注的"谁在用"。
  // 新询问到达 ⇒ 未提交的选择一律作废（spec D14：不弹确认、不尝试提交）。
  resetSelection();
  renderTable();
  // 回放收尾 / 纯刷新：把这一批里还没铺的叙事条目补齐（简易档；其它档是空操作）。
  appendNarrativeThrough(Infinity);
  scrollLogToBottom();
  if (body.finished) {
    state.timer = null;
    // 票 07 A（调用点 ③）：这一支在 `renderPending` **之前** return，旧实现在这里只清了
    // `#t-options` / `#t-target-hint` 两格 ⇒ 技能串 / 页脚 / 描述停在上一问上（spec §1 #5
    // 的第三类缺欠）。现在走**同一个** `resetAskSurface`；`#t-prompt` 与页脚随后写的是
    // 终局文案（新事实），不是残留。
    resetAskSurface();
    resetCountdown();
    resetRightActions();
    syncDialog();
    $("t-prompt").textContent = "本局已结束。";
    $("btn-submit").disabled = true;
    $("btn-cancel").disabled = true;
    showPageNotice("已结束（录像已存）");
    loadRuns();
    return;
  }
  renderPending(body);
}

/** 选择态快照 / 恢复（U2-03）：右端动作点击前先留一份，提交被拒时还回点击前那一面。
 *
 * 只覆盖**本地选择**——候选 /  legality 始终以服务端最近一次下发为准。
 */
function selectionSnapshot() {
  return {
    focusCardId: state.focusCardId,
    selectedTargets: state.selectedTargets.slice(),
    selectedOption: state.selectedOption,
    picks: state.picks.slice(),
    preactivation: state.preactivation,
    lastRejectReason: state.lastRejectReason,
  };
}

function restoreSelectionState(snapshot) {
  if (!snapshot) return;
  state.focusCardId = snapshot.focusCardId;
  state.selectedTargets = snapshot.selectedTargets.slice();
  state.selectedOption = snapshot.selectedOption;
  state.picks = snapshot.picks.slice();
  state.preactivation = snapshot.preactivation;
  state.lastRejectReason = snapshot.lastRejectReason;
}

/** 清空全部未提交的选择（焦点 / 已选目标 / 旧路径的候选与槽位 / 预发动本地态 / 排列面）。 */
function resetSelection() {
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.picks = [];
  state.arrange = null;
  state.preactivation = null;
  state.convertSkill = null;
  state.lastRejectReason = "";
  // U2-06：询问弹窗的生命周期**只**跟"这一步的表面"走——`syncDialog()` 在每次渲染末尾
  // 按新询问开 / 关 / 换正文。这里不直接改窗口与折叠状态：同一问的轮询刷新必须保留折叠。
  // 武将详情（`state.modal.heroSeat`）属于用户，不因新询问被动关闭。
}

async function refresh(force = false) {
  if (!state.sessionId || state.playback) return;
  // 提交在飞时不取增量：轮询重绘会把"点击前那一面"换掉，失败后就没得恢复（U2-03）。
  // 409 的刷新走 `refresh(true)`——那是请求**结束后**才发生的，必须放行。
  if (state.submitBusy && !force) return;
  // 票 14：开局批播完时这里会自动跑一次（不等 2 s 轮询）⇒ "回包比换局慢"真的会撞到：
  // 玩家在这几秒里回到开局页又开一局，旧会话的回包不许覆盖新牌桌。发起时记下会话身份。
  const sessionId = state.sessionId;
  try {
    const body = await fetchView();
    if (sessionId !== state.sessionId) return;
    applyView(body);
  } catch (error) {
    showPageNotice(`取增量失败：${error.message}`);
  }
}

/* ------------------------------------------------ 静默轮询（U3-05 / D12） */

/** 轮询节奏与失败提示阈值（D12）：每 2 秒一拍；失败静默，连续 3 次才提示。 */
const POLL_INTERVAL_MS = 2000;
const POLL_NOTICE_AFTER = 3;

/** 连续失败次数：成功一次即归零，到第 3 次才在页面上说一句。 */
let pollFailures = 0;

/** 取一次增量（GET 的 URL / 游标口径只在这里一处；`refresh` 与 `poll` 共用）。
 *
 * 三个游标各管一条通道：`after` = 记录流、`logs` = 侧车日志、`display` = 显示事实的
 * **投递计数**（ui-live-refresh 票 02；见 `state.displaySeq` 的注释）。
 */
async function fetchView() {
  const display = Number(state.displaySeq) || 0;
  return api(`/api/sessions/${state.sessionId}?after=${state.cursor}&logs=${state.logSeq}`
    + `&display=${display}`);
}

/** 这份响应有没有**新事实**：游标与当前询问序号都没变 ⇒ 只回倒计时锚点（D12）。
 *
 * 两个判据缺一不可：只看游标会漏掉"同游标但换了询问"；只看询问序号会漏掉新记录
 * （AI 行动推着同一个询问走）。没有当前视图（首屏）时一律按"变了"处理。
 */
function pollIsIdle(body) {
  if (!body || state.view === null) return false;
  if (Number(body.cursor) !== Number(state.cursor)) return false;
  const incoming = body.pending ? body.pending.seq : null;
  const current = state.view.pending ? state.view.pending.seq : null;
  return incoming === current;
}

/** 静默分支：不换视图、不重绘、不清选择——只把服务端时刻与倒计时锚点并进来。
 *
 * `commitView` 无条件 `resetSelection()`，直接调它会每 2 秒清掉选到一半的牌；这里因此
 * **只**同步时间：同一询问序号`syncPendingClock` 不会重置本地锚点（`pending.js` 的唯一职责）。
 */
function applyIdlePoll(body) {
  if (!state.view) return;
  // 显示事实的投递游标照样要推进：空转这一拍的**载荷已经是最新态**（commit 过的视图 +
  // 这一次的快照），事实只用于批内落点，故这里只收下游标、不应用它们。
  if (body && body.display_seq !== undefined) {
    state.displaySeq = Math.max(0, Number(body.display_seq) || 0);
  }
  state.view.pending_since = body.pending_since;
  syncPendingClock(state.view);
}

/** **稳定序列化**：同一份内容恒得同一串（键名排序、递归），与对象键序无关。
 *
 * 只给指纹用：载荷是 JSON（没有环、没有 undefined 之外的怪值），故这里不处理 Date / Map。
 * `undefined` 会变成 `undefined` 这个字面量（不是 `"null"`）——两份载荷同样的"没有这一格"
 * 仍然同串，正是我们要的。
 */
function stableJson(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  const keys = Object.keys(value).sort();
  return `{${keys.map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
}

/** 视图指纹**跳过**的键：传输字段，或**自带刷新通道**的字段。
 *
 * 跳过的每一格都要有理由（它们的变化由别处负责，不能靠"空转重绘座位块"来兜）：
 *  - `records` / `narrative` / `session_log` / `live_deltas` / `skill_events` /
 *    `damage_events` / `card_events` / `life_loss_events`：增量通道，每次请求都变；
 *  - `cursor` / `log_seq` / `pending_since` / `timeout_seconds` / `finished`：游标与时钟；
 *  - `pending`：`pollIsIdle` 用询问序号单独判（它要的是"同一问"而不是"同一份"）；
 *  - `harvest`：五谷的视图级状态有**自己的**重画口（`applyIdleHarvest`，票 18）——
 *    在这里也认一遍会让空转每拍整桌重建，把亮牌窗的悬停 / 焦点抖掉。
 *  - `display_facts`（ui-live-refresh 票 02）：逐事件事实通道，由回放流消费；
 *  - `setup`：开局批一次性下发的"发牌前底稿"，只在 `after < 0` 那一份里有——它不随局内
 *    状态变化，留在视图里也不会再被改；不跳过的话，开局之后的每一拍都会因为"这一份没有它"
 *    被判成"变了"，变成每 2 秒无脑整桌重建。
 */
const VIEW_FINGERPRINT_SKIP = new Set([
  "records",
  "narrative",
  "session_log",
  "live_deltas",
  "skill_events",
  "damage_events",
  "card_events",
  "life_loss_events",
  "display_facts",
  "cursor",
  "log_seq",
  "pending_since",
  "timeout_seconds",
  "finished",
  "pending",
  "harvest",
  "setup",
]);

/** **视图指纹**（票 07 A 的第三条出口；ui-live-refresh 票 01 改成整份摘要）。
 *
 * 为什么需要它：`pollIsIdle` 只看游标与询问序号，`applyIdlePoll` 只同步时钟 ⇒ 任何
 * **"无新记录的状态变化"永远不会重绘**。
 *
 * 为什么是**整份摘要**而不是字段表：旧的实现手写列举"座位块会读的那些字段"，而载荷里
 * 每一拍都可能长出新的显示字段（`hero_tags` / `hand_limit` / `distance_bonus` / 顶层
 * `skills` 的化身组件 / `relics` / `round` / `turn_extra` 都是这么漏掉的），表里甚至还留着
 * 一个死项（`player.hero`——网页载荷只有 `hero_name`）。手写表的正确性靠"记得登记"，
 * 那正是这类 bug 的来源。摘要遍历**视图里除传输字段外的整份内容**：新增载荷键自动进摘要，
 * 不需要谁再登记一次（票 02 的载荷键门禁盯另一头：新键必须声明刷新通道）。
 *
 * 纯函数、不碰 DOM；同一份内容恒同串（`stableJson` 按键名排序）。
 */
function viewFingerprint(view) {
  if (!view || typeof view !== "object") return "";
  const parts = [];
  for (const key of Object.keys(view).sort()) {
    if (VIEW_FINGERPRINT_SKIP.has(key)) continue;
    parts.push(`${key}=${stableJson(view[key])}`);
  }
  return parts.join("|");
}

/** 空转期快照**变了**吗（票 07）：变了 ⇒ 座位块要重绘，但**不清本地选择**（没有新事实）。
 *
 * 为什么不像"有新记录"那样走 `applyView` → `commitView`：那条路无条件 `resetSelection()`，
 * 会把玩家选到一半的牌清掉。这里只把快照换成服务端那份、重绘表体，询问面仍是当前这一问。
 *
 * 并入的格子与 :func:`viewFingerprint` **同一张名单**（非传输键）：新增载荷键既进摘要，
 * 也自动被这一拍换上——否则摘要说"变了"、视图却没换，下一拍还会再判一次"变了"，
 * 变成每 2 秒无脑整桌重建。
 */
function applyIdleRepaint(body) {
  if (!state.view || !body) return false;
  for (const key of Object.keys(body)) {
    if (VIEW_FINGERPRINT_SKIP.has(key)) continue;
    state.view[key] = body[key];
  }
  state.view.pending_since = body.pending_since;
  if (body.display_seq !== undefined) {
    state.displaySeq = Math.max(0, Number(body.display_seq) || 0);
  }
  syncPendingClock(state.view);
  renderTable();
  return true;
}

/** 空转期的**亮牌重画**（table-ux-overhaul 票 18 / 服务端 D 方案）。
 *
 * 为什么需要它：亮牌面过去只在"下一次带 `harvest` 的询问"到达时重画；五谷的整段结算里
 * **不是每个目标都有一条 pending**（AI 目标的取牌在服务端 `Session.drive()` 内部同步答完），
 * 于是"谁拿走了哪张"永远等不到一次重画。服务端现在把**同一份**派生状态挂在每一次视图
 * 下发上（`view.harvest`），这一拍就把它并进视图，内容变了就重画亮牌节点。
 *
 * 与 `applyIdleRepaint` **同一条纪律**：换内容**不清选择**（`state.picks` / 焦点 / 目标原样），
 * 也没有新事实——不走 `commitView`（那条路无条件 `resetSelection()`）。折叠与只读判据不动。
 * 返回 `true` = 这一拍真的重画了亮牌。
 */
function applyIdleHarvest(body) {
  if (!state.view || !body) return false;
  const incoming = body.harvest && typeof body.harvest === "object" ? body.harvest : null;
  const previous = state.view.harvest && typeof state.view.harvest === "object"
    ? state.view.harvest
    : null;
  state.view.harvest = incoming;
  if (!incoming) return false;
  // 同一份状态不重画（空转每 2 秒一拍，重建节点会丢悬停 / 焦点）。
  if (harvestFingerprint(incoming) === harvestFingerprint(previous)) return false;
  return repaintRevealFromHarvest(incoming);
}

/** 一拍轮询：播放中 / 提交在飞时**暂停**（下一次 tick 自然恢复），弹窗打开时照常进行。
 *
 * - 播放中取增量会把半批事件换成"播放中快照"（提交按钮 / 倒计时都会跳），故让路；
 * - 提交在飞时同 `refresh`：不换掉点击前那一面（U2-03 的失败回滚依赖它）；
 * - 弹窗打开期间**继续**轮询：服务端的超时代答只在取增量时判定（`serve.py` 的
 *   `advance_clock`），停掉它就等于"开着弹窗永远不会超时"。
 *
 * 失败静默（D12）：前两次一个字都不写，连续第 3 次才提示一次；任何一次成功都清零。
 */
async function poll() {
  if (!state.sessionId || state.playback || state.submitBusy) return;
  try {
    const body = await fetchView();
    pollFailures = 0;
    if (pollIsIdle(body)) {
      // 票 18：空转期的**视图级五谷状态**（`view.harvest`）变了 ⇒ 重画亮牌节点（不清选择）。
      // 它与下面两条空转出口互不排斥，故不在这里 return。
      applyIdleHarvest(body);
      // 票 07：空转**不等于**"什么都没变"——视图摘要变了就重绘座位块（不清选择）。
      // 摘要没变才是纯时钟刷新（D12 的静默分支）。
      if (viewFingerprint(body) !== viewFingerprint(state.view)) {
        applyIdleRepaint(body);
        return;
      }
      applyIdlePoll(body);
      return;
    }
    applyView(body);
  } catch (error) {
    pollFailures += 1;
    if (pollFailures === POLL_NOTICE_AFTER) {
      showPageNotice(`取增量失败：${error.message}`);
    }
  }
}
