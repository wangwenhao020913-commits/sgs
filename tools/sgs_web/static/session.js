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

/** 寿命推进（**纯函数**，D5 / D15）：`item + now → visible | fading | gone`。
 *
 * 判定不看计时器，也不改任何状态；`setTimeout` 只在 hold / fade 结束时触发重绘
 * （测试直接喂 `now`，或把 `animScale` 置 0）。
 */
function stageLane(item, now) {
  const stage = item && item.stage;
  if (!stage) return "gone";
  const enteredAt = Number(stage.enteredAt) || 0;
  const fadeAt = stage.fadeAt === null || stage.fadeAt === undefined
    ? enteredAt + STAGE_MAX_MS
    : Number(stage.fadeAt);
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
    const enteredAt = Number(stage.enteredAt) || 0;
    const fadeAt = stage.fadeAt === null || stage.fadeAt === undefined
      ? enteredAt + STAGE_MAX_MS
      : Number(stage.fadeAt);
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
  }
  // U1-07：快照的处理区已经是**产物**（服务端把转化来源组换成了虚拟牌），回放期间也要
  // 按同一份映射把"来源牌进处理区"渲染成产物，否则用户会先看到两张源牌再被快照纠正。
  state.processingProducts = processingProductMap(
    body.snapshot ? body.snapshot.processing : [],
  );
  if (!incoming.length) {
    commitView(body);
    return;
  }
  const previous = viewStartsNewSession(body)
    ? []
    : (state.view && state.view.snapshot ? state.view.snapshot.processing || [] : []);
  state.effectCards = mergeEffectCards(previous);
  state.playback = { body, records: incoming, index: 0, querySeat: state.lastQuerySeat };
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
  renderProcessing();
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
  const held = (cards || []).filter((item) => stageLane(item, now) !== "gone");
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
  held.push({
    ...stageItem(card, {
      lane: "showcase",
      frameId,
      enteredAt: now,
      fromZone: { seat: record.seat, zone: "hand" },
    }),
    revealedBy: record.seat,
  });
  return held;
}

/** 舞台的逐事件派生（U3-01 / D2）：``card_move`` / ``card_reveal`` / 帧 ``pop``。
 *
 * - **处理中**：`snapshot.processing` + ``card_move`` 且 `to_zone.zone === "processing"`；
 * - **展示**：``card_reveal`` 进展示组（火攻；反间的转组是票 02 的范围）；
 * - **弃牌**：``card_move`` 且 `to_zone.zone === "discard"` 且原因 = 弃置 / 重铸 / 装备
 *   替换 / 拆顺；使用（USE）与判定（JUDGE）**不进弃牌组**，只从处理中淡出。
 *
 * **寿命**（D5）：有承载帧的展示 / 弃牌停在帧 ``pop`` 后 1s 淡出；处理中的牌离开处理区
 * 即 1s 淡出；无帧的弃牌入台 1s 停留 + 1s 淡出；任何项兜底 30s。换组沿用同一条舞台项
 * （不重新落位）；淡出期间同一张牌再入台 = 撤旧项、重新落位（D5.6）。
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
  let held = (effectCards || []).filter((item) => stageLane(item, now) !== "gone");
  if (!record) return held;
  if (record.kind === "timing") {
    applyUseTiming(record, held);
    return held;
  }
  if (record.kind === "frame") {
    if (record.op === "pop") stageStartFade(held, record.frame_id, now);
    return held;
  }
  if (record.kind !== "delta" || record.delta_type !== "card_move") return held;
  const cards = record.cards || [];
  const from = record.from_zone || {};
  const to = record.to_zone || {};
  const frameId = stageFrameOf(record);
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
        item.stage.lane = "discard";
        item.stage.frameId = frameId;
        item.stage.fadeAt = frameId > 0 ? null : item.stage.enteredAt + STAGE_HOLD_MS;
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
        existing.source = record.reason === "USE" ? record.owner : null;
        existing.targets = null;
        existing.currentTarget = null;
        continue;
      }
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
        existing.stage.lane = "discard";
        existing.stage.frameId = frameId;
        existing.stage.fadeAt = frameId > 0 ? null : existing.stage.enteredAt + STAGE_HOLD_MS;
        continue;
      }
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
 * 快照已没有的牌 = 它已经离开处理区——开始 1s 淡出（不再瞬删）；快照里新出现的牌补进来。
 * 同一 id 若还挂在展示 / 弃牌组又出现在处理区快照里（洗牌后再次使用），以快照为准落回
 * 处理中组。换会话的清空在 :func:`commitView` 里做，不在这里猜。
 */
function mergeEffectCards(cards) {
  const now = stageNow();
  const wanted = new Set((cards || []).map((card) => String(card.card_id)));
  const held = new Map();
  for (const item of state.effectCards || []) {
    if (!item || !item.stage) continue;
    if (stageLane(item, now) === "gone") continue;
    held.set(String(item.card_id), item);
  }
  const out = [];
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
    out.push(stageItem(card, { lane: "processing", enteredAt: now, fromZone: null }));
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
  }
  state.view = body;
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
    const body = await api(`/api/sessions/${state.sessionId}?after=${state.cursor}&logs=${state.logSeq}`);
    applyView(body);
  } catch (error) {
    showPageNotice(`取增量失败：${error.message}`);
  }
}

async function poll() {
  if (!state.sessionId) return;
  await refresh();
  window.setTimeout(poll, 1200);
}
