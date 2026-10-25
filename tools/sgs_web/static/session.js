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

/** 寿命口径（D5）：无帧停留 1s、淡出 1s；任何舞台项兜底 30s。 */
const STAGE_HOLD_MS = 1000;
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

/** 舞台项 = 卡面 + 一条道 + 来源 + 决定它活多久的结算帧。
 *
 * 字段住在 `item.stage` 里（不是散在卡面上）：判定是**纯函数**
 * :func:`stageLane`，只读这三个数——`enteredAt` / `fadeAt` / 承载帧。
 * ``fadeAt === null`` = 还在等承载帧的 `pop`；兜底由 `enteredAt + 30s` 接住。
 */
function stageItem(card, options = {}) {
  const now = options.enteredAt === undefined ? stageNow() : options.enteredAt;
  const frameId = Number(options.frameId) || 0;
  const lane = options.lane || "processing";
  let fadeAt = options.fadeAt;
  if (fadeAt === undefined) {
    // 处理中：离开处理区 / 承载帧 pop 才起算；展示与弃牌：无帧固定 1s 停留，有帧等 pop。
    fadeAt = lane === "processing" || frameId > 0 ? null : now + STAGE_HOLD_MS;
  }
  return {
    ...card,
    source: options.source !== undefined ? options.source : null,
    targets: options.targets !== undefined ? options.targets : null,
    currentTarget: options.currentTarget !== undefined ? options.currentTarget : null,
    stage: {
      lane,
      frameId,
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

/** 舞台项开始淡出的时刻（D5）：有 `fadeAt` 用它，否则兜底 = 入台 + 30s。
 *
 *  判定（:func:`stageLane`）与计时器（:func:`nextStageDeadline`）共用这一处算式：
 *  两边各写一份，兜底口径一旦漂移，就会出现"判定说还在、计时器说该走了"。
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

/** 承载帧 `pop` 到了：把挂在这帧上的舞台项推进 1s 淡出（已开始的保持原锚点）。 */
function stageStartFade(items, frameId, now) {
  for (const item of items || []) {
    const stage = item && item.stage;
    if (!stage) continue;
    if (String(stage.frameId) !== String(frameId)) continue;
    if (stage.fadeAt === null || stage.fadeAt === undefined) stage.fadeAt = now;
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
  item.stage.fadeAt = frameId > 0 ? null : item.stage.enteredAt + STAGE_HOLD_MS;
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

/* ------------------------------------------------ 回放期逐事件全表更新（U3-05 / D11） */

/** 一份新的 **live view**：从最近一次提交的视图复制出牌桌会用到的可变容器。
 *
 * 它只属于回放期：每条记录把**可见**变化写进它（见 :func:`applyLiveDelta`），`renderTable`
 * 从它重绘；`commitView` 到时用权威快照整份覆盖。深拷贝的是牌桌会改写的容器
 * （`players` 及其装备 / 判定区、`self_hand`、`discard`、`processing`），故逐事件派生
 * **绝不改** `state.view` 的权威快照。
 */
function cloneLiveView(body) {
  const view = { ...body };
  const snapshot = body && body.snapshot;
  if (!snapshot) return view;
  view.snapshot = {
    ...snapshot,
    players: (snapshot.players || []).map((player) => ({
      ...player,
      equip: (player.equip || []).slice(),
      judge: (player.judge || []).slice(),
    })),
    self_hand: (snapshot.self_hand || []).slice(),
    discard: (snapshot.discard || []).slice(),
    processing: (snapshot.processing || []).slice(),
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

/** 一条记录对 live view 的**可见**贡献（D11）：只写记录里真有的派生事实。
 *
 * 可派生：体力（`hp_change.after`）、手牌数（`card_move` 的牌面 / `card_count`）、自己的
 * 手牌面、装备与判定区（公开区牌面）、牌堆与弃牌数（含 `deck_shuffle` 的重洗）、横置、
 * 酒、亮明身份、阵亡（`DEATH_BEFORE_START` 的 `actor`）、回合 / 阶段（`TURN_*` / `PHASE_*`）。
 *
 * **不可派生的字段一律不猜**：技能可用性、体力上限、当前询问、武将名与势力留在最近一次
 * 提交的视图里，由下一次 `commitView` 覆盖（D11）。`snapshot.processing` 也不在这里推：
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
    if (record.delta_type === "hp_change") {
      const player = playerAt(record.seat);
      if (player) player.hp = Number(record.after);
      return view;
    }
    if (record.delta_type === "chained_change") {
      const player = playerAt(record.seat);
      if (player) player.chained = Boolean(record.chained);
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
  const incoming = body.records || [];
  const first = state.view === null;
  // U3-01 / D5.5：换会话时舞台整块作废——上一局的牌不能在**回放新局记录之前**还留在台上。
  if (viewStartsNewSession(body)) {
    state.effectCards = [];
    state.revealedCards = [];
    // U3-04：上一局还没触发的技能发动事件不属于新局（DOM 侧由 commit 的清屏兜底）。
    state.skillEvents = [];
    // U3-05：live view 是"上一份提交视图的派生副本"，换会话后没有上一份可派生。
    state.liveView = null;
  }
  // U3-04 / D8.2：技能发动是随视图下发的派生通道；先收进队列，回放走到对应的记录 seq
  // 才演出（飘名 / 白光 / 线），而不是响应一到就全部同时出现。
  for (const event of body.skill_events || []) state.skillEvents.push(event);
  // U1-07：快照的处理区已经是**产物**（服务端把转化来源组换成了虚拟牌），回放期间也要
  // 按同一份映射把"来源牌进处理区"渲染成产物，否则用户会先看到两张源牌再被快照纠正。
  state.processingProducts = processingProductMap(
    body.snapshot ? body.snapshot.processing : [],
  );
  if (!incoming.length) {
    // 纯刷新没有回放：技能发动的演出按响应立即触发（不等一个不存在的记录步）。
    fireSkillEventsThrough(Infinity);
    commitView(body);
    return;
  }
  const previous = viewStartsNewSession(body)
    ? []
    : (state.view && state.view.snapshot ? state.view.snapshot.processing || [] : []);
  state.effectCards = mergeEffectCards(previous);
  // U3-05（D11）：逐事件派生的 live view 从**最近一次提交**的视图起算（深拷贝，绝不改权威
  // 快照）。开局那一批没有上一份提交视图（牌桌本来是空的、且整批即时冲刷），
  // `playback.live = false`：只逐事件派生舞台，表体等 `commitView`。
  const live = state.view !== null;
  if (live) state.liveView = cloneLiveView(state.view);
  state.playback = {
    body, records: incoming, index: 0, querySeat: state.lastQuerySeat, live,
  };
  $("btn-submit").disabled = true;
  $("btn-cancel").disabled = true;
  $("t-choice").textContent = "";
  // 回放期间倒计时与右端动作都收起：`state.view` 还停在上一条询问，继续画会误导。
  resetCountdown();
  resetRightActions();
  // 回放期间**响应文本清空**：它属于"下方询问界面"的那一问，等播完、新询问提交后再显示。
  // 描述文本不在这里清——它由 renderProcessing 按效果区逐事件改写。
  $("t-prompt").textContent = "";
  // 第一条也等满一个步长再出现：每个事件之间的间隔就是当前的步长档。
  playbackTimer = window.setTimeout(() => stepPlayback(false), state.eventDelayMs);
  // **开局**是唯一不等节奏的：此前牌桌上一片空白，逐条播开局事件只是空屏五秒。
  // 直接把记录走完（日志照收），让牌桌立刻有局面可看。
  if (first) flushPlayback();
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
  if (record.kind === "query") {
    playback.querySeat = record.query.actor;
    state.lastQuerySeat = record.query.actor;
  }
  state.logRecords.push(record);
  // 简易档铺服务端叙事（与记录同 seq 的条目在这一步出现）；完整两档铺记录。
  if (logMode() === "simple") appendNarrativeThrough(record.seq);
  else appendLog(record, playback.querySeat);
  state.effectCards = applyEffectDelta(record, state.effectCards);
  state.revealedCards = applyRevealDelta(record, state.revealedCards);
  // U3-05（D11）：每条记录先把**可见**变化写进 live view，再**从它**重绘全表——手牌数 /
  // 自己的手牌面 / 体力 / 装备与判定区 / 牌堆与弃牌数 / 横置 / 酒 / 身份 / 阵亡 /
  // 回合与阶段都随事件走，不再等整批播完提交。
  if (playback.live && state.liveView) {
    applyLiveDelta(record, state.liveView);
    // renderTable 末尾就是舞台重绘（`renderProcessing`），播放期也走同一条口。
    renderTable();
  } else {
    // 开局批次没有上一份提交视图可派生：表体等 commit，舞台照旧逐事件派生。
    renderProcessing();
  }
  // U3-03（D8.1 / D13）：决策点高亮、使用牌目标线、体力闪。回放页不走这条路径（D14）。
  // 放在表体重绘**之后**：描边 / 体力闪要落在新座位节点上（`syncTransientEffects` 兜住
  // 未到寿命的那几个，这里补的是这一条记录新产生的那一个）。
  applyRecordEffects(record, playback.querySeat);
  // A1：洛神连续判定——弹窗已开着时，用刚进处理区的判定牌续上同一个窗口。
  // 只读 JUDGE 帧 + `card_move(reason=JUDGE)` 这两条服务端事实，不认技能名；舞台已由
  // `applyEffectDelta` 照常派生（弹窗不往舞台上加卡）。
  const judgedCard = judgmentCardFromRecord(record);
  if (judgedCard) refreshJudgmentReveal(judgedCard);
  // U3-04（D9）：技能发动的演出挂在**产生它的那条记录**的 seq 上。
  fireSkillEventsThrough(record.seq);
  scrollLogToBottom();
  playback.index += 1;
  if (!immediate) playbackTimer = window.setTimeout(() => stepPlayback(false), state.eventDelayMs);
}

/** 把整批回放**立刻**跑完（测试与"收到新视图前先收尾"用）。 */
function flushPlayback() {
  if (playbackTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(playbackTimer);
    playbackTimer = null;
  }
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
  // 兜底：旧录像 / 合成载荷若没有同 seq 的记录，播完立即触发，不让事件跨到下一批。
  fireSkillEventsThrough(Infinity);
  commitView(playback.body);
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
 */
function applyRevealDelta(record, cards = state.revealedCards) {
  const now = stageNow();
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
  held.push({
    ...stageItem(card, {
      lane: "showcase",
      frameId,
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
 * **寿命**（D5）：有承载帧的展示 / 弃牌停在帧 ``pop`` 后 1s 淡出；处理中的牌离开处理区
 * 即 1s 淡出；无帧的弃牌入台 1s 停留 + 1s 淡出；任何项兜底 30s。换组沿用同一条舞台项
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
 */
function applyEffectDelta(record, effectCards = state.effectCards, products = state.processingProducts) {
  const now = stageNow();
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
    return held;
  }
  if (record.kind === "frame") {
    // D6：`frame_kind` 只在 push 上；记下来供离区那一步查（跨 commit / 跨批次有效）。
    if (record.op === "push" && record.frame_kind) {
      frameKinds.set(String(record.frame_id), String(record.frame_kind));
    }
    if (record.op === "pop") stageStartFade(held, record.frame_id, now);
    return held;
  }
  if (record.kind !== "delta" || record.delta_type !== "card_move") return held;
  const cards = record.cards || [];
  const from = record.from_zone || {};
  const to = record.to_zone || {};
  const frameId = stageFrameOf(record);
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
        enteredAt: now,
        fromZone: from,
        source: record.owner,
      }));
    }
  }
  return held;
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
    // U1-06：换会话时详情窗口的座位号属于上一局，必须作废（同一局的刷新则保留）。
    state.modal.heroSeat = null;
    // U3-01 / D5.5：舞台三组跨 commit 保留，但换会话必须清空（上一局的牌不能留在台上）。
    state.effectCards = [];
    state.revealedCards = [];
    // U3-03 / D8.1：目标线与行动者描边 / 体力闪是**一次演出**，换会话后不属于新局。
    clearTransientEffects();
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
    resetCountdown();
    resetRightActions();
    syncDialog();
    $("t-prompt").textContent = "本局已结束。";
    $("btn-submit").disabled = true;
    $("btn-cancel").disabled = true;
    $("t-options").innerHTML = "";
    $("t-target-hint").textContent = "";
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
  try {
    applyView(await fetchView());
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

/** 取一次增量（GET 的 URL / 游标口径只在这里一处；`refresh` 与 `poll` 共用）。 */
async function fetchView() {
  return api(`/api/sessions/${state.sessionId}?after=${state.cursor}&logs=${state.logSeq}`);
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
  state.view.pending_since = body.pending_since;
  syncPendingClock(state.view);
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
