"use strict";

/* ============================================================================
 * 飞行层（U4-01 / spec D1 / D2 / D3 / D5 / D6）
 *
 * 一个与舞台区**互不依赖**的覆盖层：每张移动的牌起一个牌面副本，从**起飞那一刻真实所在
 * 的位置**出发，落到这次移动 `to_zone` 的锚点。起点在 `applyEffectDelta` 之后、重绘之前
 * 量（源节点还在原处）；落点在重绘之后量并**同步**起飞——唯一入口是
 * `session.js:stepPlayback`，本文件不再 monkey-patch 任何全局函数。
 *
 * 分类表 :data:`ANIM_FLIGHT_RULES` 按 `(reason, to_zone, from_zone)` 首条命中给裁定：
 * ``kind: null`` = 不飞（D3 只有 ``processing -> discard`` 一条）。表由
 * ``tests/test_anim_registry.py`` 对着 ``MoveReason`` / ``Zone`` / 47 条标准录像核——
 * 引擎多出一个 reason / 去向区而没登记，测试就红；运行期遇到未登记的组合，层上留
 * ``data-anim-unregistered``。起 / 落点任一缺失：静默跳过 + ``data-anim-missing``。
 *
 * 时长表在同目录 ``anim.ini``：缺键 / 非法值退回内置默认，并在 ``#t-anim-note`` 留
 * **一行**提示。``state.eventDelayMs``（D7 的节奏公式）不是本文件的职责（票 02）。
 * ========================================================================== */

/* ------------------------------------------------------------ 时长表 + ini 载入 */

/** D8 的键与缺省值（``anim.ini`` 是同一份的磁盘副本；缺键时退回这里）。 */
const animDefaults = {
  base_step: 100,
  flight_use: 180,
  flight_judge_reveal: 180,
  flight_draw: 220,
  flight_gain: 300,
  flight_discard: 300,
  flight_place_equip: 320,
  flight_place_judge: 320,
  flight_replace_out: 280,
  flight_stage_out: 0,
  fan_ms: 120,
  fan_px: 30,
  phase_float_ms: 1200,
};

/** 生效中的时长表（``anim.ini`` 覆盖内置默认；票 02 / 04 读同一份）。 */
const animConfig = Object.assign({}, animDefaults);

/** 一次加载的所有问题（拼成**一行**提示；成功后清空）。 */
const animNotes = [];

/** ``anim.ini`` 生效后的订阅口：**只通知"表读完了"**，不碰播放节奏。
 *
 * 票 02 由 ``main.js`` 订阅它把 ``base_step`` 落成**初始**档位（玩家点过按钮之后由
 * ``main.js`` 自己挡住）；票 04 的飘字时长直接读 ``animConfig``，不需要订阅。
 * 订阅者的异常只影响它自己：既不阻塞 ini 载入，也不抹掉已经生效的表。
 */
const animConfigHandlers = [];

function animOnConfig(handler) {
  if (typeof handler === "function") animConfigHandlers.push(handler);
}

function animNotifyConfig() {
  for (const handler of animConfigHandlers) {
    try {
      handler(animConfig);
    } catch (error) {
      /* 订阅者出错不阻塞其余订阅者，也不影响 ini 已经生效的事实。 */
    }
  }
}

function animPushNote(text) {
  const line = String(text || "").trim();
  if (line && !animNotes.includes(line)) animNotes.push(line);
}

/** 页面提示只有一行：所有问题用「；」拼起来，换行一律压平。 */
function animShowNote(text) {
  const node = $("t-anim-note");
  if (!node) return;
  const line = String(text || "").replace(/\s*\n+\s*/g, "；").trim();
  node.textContent = line;
  node.hidden = line === "";
}

/** 静态服务可能把**缺失**的路径回落到 `index.html`（200 + HTML；`serve.py` 的单页应用兜底）。
 *
 * 这时 `fetch` 是成功的、正文却是整页 HTML：若照常逐行解析，一行提示会被几十条
 * "无法解析「…」" 撑成一堵墙。整份当"取不到"，回内置默认、只留一行。
 */
function animLooksLikeHtml(text) {
  return /^\s*(?:<!doctype\s+html\b|<html\b)/i.test(String(text || ""));
}

/** ``key = value`` 的极简解析（``#`` / ``;`` 注释；非法值记一条问题、该键退回默认）。 */
function animParseIni(text) {
  const out = {};
  for (const rawLine of String(text || "").split(/\r?\n/)) {
    const line = rawLine.replace(/\s*[#;].*$/, "").trim();
    if (!line) continue;
    const at = line.indexOf("=");
    if (at < 0) {
      animPushNote(`无法解析「${line}」`);
      continue;
    }
    const key = line.slice(0, at).trim();
    const rawValue = line.slice(at + 1).trim();
    // 空值 = 没写：`Number("") === 0` 会把"缺项"悄悄变成"不飞"，故显式判非法。
    const value = rawValue === "" ? Number.NaN : Number(rawValue);
    if (!Number.isFinite(value)) {
      animPushNote(`${key} 不是数字，已忽略`);
      continue;
    }
    out[key] = value;
  }
  return out;
}

/** 读 ``anim.ini`` 覆盖内置默认；缺键 / 非法值 / 取不到都退回默认，只留一行提示。 */
function animLoadConfig() {
  const apply = (text, failed) => {
    animNotes.length = 0;
    // 缺失的 anim.ini 可能被静态服务回落到 index.html（200 + HTML）：整份按取不到处理。
    const unavailable = failed || animLooksLikeHtml(text);
    const parsed = unavailable ? {} : animParseIni(text);
    for (const key of Object.keys(parsed)) {
      if (key in animDefaults) continue;
      delete parsed[key];
      animPushNote(`未知键 ${key}`);
    }
    Object.assign(animConfig, animDefaults, parsed);
    const missing = Object.keys(animDefaults).filter((key) => !(key in parsed));
    if (missing.length) animPushNote(`缺 ${missing.length} 项，用内置默认`);
    if (unavailable) animPushNote("anim.ini 取不到，用内置默认");
    animShowNote(animNotes.join("；"));
    animNotifyConfig();
    return animConfig;
  };
  if (typeof fetch !== "function") return Promise.resolve(apply("", true));
  let requested;
  try {
    requested = fetch("anim.ini", { cache: "no-store" });
  } catch (error) {
    return Promise.resolve(apply("", true));
  }
  return Promise.resolve(requested)
    .then((response) => {
      if (!response || !response.ok) return { text: "", failed: true };
      return Promise.resolve(response.text()).then((text) => ({ text, failed: false }));
    })
    .then((result) => apply(result.text, result.failed))
    .catch(() => apply("", true));
}

/* ------------------------------------------------------------ 分类表（spec D3） */

/** ``(reason, to_zone, from_zone)`` → 飞行 kind 的**有序**裁定表（首条命中）。
 *
 * 读法（票 01 的 Answer 有完整记账）：
 * - ``from`` 缺省 = 任意来源区；``reason: "*"`` = 任意原因（只用于
 *   ``processing -> discard`` 这一条"不飞"）。
 * - D3 有两行 ``LOSE -> discard``（拆 / 顶替旧装备）重叠：引擎只有
 *   ``(LOSE, equip -> discard)`` 这一根信号，分不出"拆装备"与"顶替"。这里按
 *   ``from_zone`` 细化：``equip`` 出发的旧装备用 ``replace_out``（多等半拍），其余
 *   来源（手牌 / 判定区 / 武将牌上）用 ``discard``；不为"拆装备"另造引擎信号。
 * - 引擎真记录里还有 D3 表没逐行列出的去向：置入手牌（交给他人 / 从牌堆 / 武将牌上）、
 *   置入武将牌上的放置区、放回牌堆顶（观星）、判定区 → 处理区（准备判定）。这里给它们
 *   显式裁定，不新增时长键（沿用最接近的一条 D8 时长）。
 */
const ANIM_FLIGHT_RULES = [
  /* 处理区 → 弃牌堆：使用 / 判定收尾**不飞**（D3 最后一行；舞台淡出已表达离场）。 */
  { reason: "*", to: "discard", from: "processing", kind: null },
  /* 顶替出去的旧装备：装备槽 → 弃牌堆另飞一只，起跑比落位晚半拍（见 replaceDelay）。 */
  { reason: "LOSE", to: "discard", from: "equip", kind: "replace_out" },
  /* 拆 / 顺 / 置入弃牌堆：判定区 / 手牌 / 装备槽（非 LOSE 的装备来源）→ 弃牌计数。 */
  { reason: "LOSE", to: "discard", kind: "discard" },
  { reason: "DISCARD", to: "discard", kind: "discard" },
  { reason: "REFORGE", to: "discard", kind: "discard" },
  { reason: "PUT", to: "discard", kind: "discard" },
  /* 摸牌：牌堆 → 手牌（对手摸牌走隐藏侧：牌背 + card_count）。 */
  { reason: "DRAW", to: "hand", kind: "draw" },
  /* 使用 / 判定亮牌：手牌 / 牌堆 / 判定区 → 处理区。 */
  { reason: "USE", to: "processing", kind: "use" },
  { reason: "PUT", to: "processing", kind: "use" },
  { reason: "JUDGE", to: "processing", kind: "judge_reveal" },
  { reason: "LOSE", to: "processing", kind: "judge_reveal" },
  /* 上装备 / 置入判定区（含闪电换人：判定区 A → 判定区 B）/ 武将牌上的放置区。 */
  { reason: "PUT", to: "equip", kind: "place_equip" },
  { reason: "PUT", to: "judge", kind: "place_judge" },
  { reason: "PUT", to: "hero", kind: "place_equip" },
  /* 放回牌堆顶（观星）：沿用摸牌的时长，D8 不新增键。 */
  { reason: "PUT", to: "deck", kind: "draw" },
  /* 顺 / 借刀 / 获得 / 置入手牌：区 → 手牌。 */
  { reason: "LOSE", to: "hand", kind: "gain" },
  { reason: "PUT", to: "hand", kind: "gain" },
];

/** 顶替旧装备的起跑延迟 = 半条自身飞行时长（"错开半拍"；见 D3 与票 01 的 Answer）。 */
const ANIM_REPLACE_OUT_DELAY = 0.5;

/** 首条命中的裁定；没有登记返回 ``null``（运行期据此标"未登记"，不飞）。 */
function animRuleFor(reason, toZone, fromZone) {
  const wantedReason = String(reason || "");
  const wantedTo = String(toZone || "");
  const wantedFrom = String(fromZone || "");
  for (const rule of ANIM_FLIGHT_RULES) {
    if (rule.reason !== "*" && rule.reason !== wantedReason) continue;
    if (rule.to !== wantedTo) continue;
    if (rule.from && rule.from !== wantedFrom) continue;
    return rule;
  }
  return null;
}

function animRawDuration(kind) {
  if (!kind) return 0;
  const value = Number(animConfig[`flight_${kind}`]);
  return Number.isFinite(value) ? value : 0;
}

/** N = 同一条 ``card_move`` 的牌数；隐藏移动（``cards`` 抹空）读 ``card_count``。 */
function animCountOf(record) {
  const cards = (record && record.cards) || [];
  if (cards.length) return cards.length;
  const hidden = Number(record && record.card_count);
  return Number.isFinite(hidden) && hidden > 0 ? hidden : 0;
}

/** 一条记录在飞行层上占用的**动画总时长**（票 02 的 ``delay = base + dur × base/100`` 用）。
 *
 * = 飞行时长 + N 张的错峰尾巴（``fan_ms``）+ 顶替旧装备的半拍。没登记 / 不飞 / 时长为 0
 * 返回 0（"没有动画的记录只吃 base"）。本文件只提供这个只读口，播放步长仍住
 * ``session.js:delayOf`` / ``main.js``。
 */
function durationOf(record) {
  if (!record || record.kind !== "delta" || record.delta_type !== "card_move") return 0;
  const rule = animRuleFor(
    record.reason,
    record.to_zone && record.to_zone.zone,
    record.from_zone && record.from_zone.zone,
  );
  if (!rule || rule.kind === null) return 0;
  const base = animRawDuration(rule.kind);
  if (base <= 0) return 0;
  const count = animCountOf(record);
  const fan = count > 1 ? Math.max(0, Number(animConfig.fan_ms) || 0) : 0;
  const replace = rule.kind === "replace_out" ? base * ANIM_REPLACE_OUT_DELAY : 0;
  return Math.round(base + fan + replace);
}

/* ------------------------------------------------------------ 小工具 / 锚点 */

function animRect(node) {
  if (!node || typeof node.getBoundingClientRect !== "function") return null;
  const rect = node.getBoundingClientRect();
  if (!rect) return null;
  const width = Number(rect.width) || 0;
  const height = Number(rect.height) || 0;
  if (!width && !height) return null;
  return {
    left: Number(rect.left) || 0,
    top: Number(rect.top) || 0,
    width,
    height,
  };
}

function animAttr(node, name) {
  return node && typeof node.getAttribute === "function" ? node.getAttribute(name) : null;
}

/** 深度优先找第一个命中的后代（含根）。真 DOM 的 ``children`` 是 HTMLCollection，先展开。 */
function animFind(root, predicate) {
  if (!root) return null;
  if (predicate(root)) return root;
  for (const child of Array.from(root.children || [])) {
    const hit = animFind(child, predicate);
    if (hit) return hit;
  }
  return null;
}

function animMySeat() {
  const view = state.liveView || state.view;
  const seat = view && view.snapshot ? view.snapshot.seat : null;
  return seat === null || seat === undefined ? 0 : Number(seat);
}

function animSeatNode(seat) {
  const number = Number(seat);
  if (number === animMySeat()) return $("t-self-seat");
  const ring = $("t-seats");
  if (!ring) return null;
  return Array.from(ring.children || []).find(
    (node) => animAttr(node, "data-seat") === String(number),
  ) || null;
}

/** 一个座位的装备 / 判定 / 武将牌上的牌区行（``data-zone`` 落在行上）。 */
function animZoneRow(seat, zone) {
  const number = Number(seat);
  let root;
  if (number === animMySeat()) {
    // 垫片里静态节点没有父子关系：自己的装备 / 判定行住 `#t-self-zones`，武将牌上的
    // 牌区住 `#t-self-seat` 的武将块里——两边分别找。
    root = zone === "hero" ? $("t-self-seat") : $("t-self-zones");
  } else {
    root = animSeatNode(number);
  }
  return animFind(
    root,
    (node) => animAttr(node, "data-zone") === zone
      && animAttr(node, "data-seat") === String(number),
  );
}

/** 手牌区的锚点：自己 = 手牌行；他人 = 武将块底部的「手牌 N」计数。 */
function animHandAnchor(seat) {
  const number = seat === null || seat === undefined ? animMySeat() : Number(seat);
  if (number === animMySeat()) return $("t-hand");
  const seatNode = animSeatNode(number);
  if (!seatNode) return null;
  const foot = animFind(seatNode, (node) => nodeHasClass(node, "seat-foot"));
  const count = foot ? Array.from(foot.children || [])[0] : null;
  return count || seatNode;
}

/** 处理区里这张牌的舞台卡（转化牌是产物、card_id 不同：调用方退回处理区容器）。 */
function animStageCard(cardId) {
  if (cardId === undefined || cardId === null) return null;
  const box = $("t-processing");
  return animFind(box, (node) => animAttr(node, "data-stage-key") === String(cardId));
}

/** 区域坐标 → 锚点节点（``card`` 只用来定位**具体槽位**，不是必须的）。 */
function animAnchorFor(ref, card) {
  if (!ref) return null;
  const zone = String(ref.zone || "");
  const seat = ref.seat === null || ref.seat === undefined ? null : Number(ref.seat);
  if (zone === "deck") return $("t-deck");
  if (zone === "discard") return $("t-discard");
  if (zone === "processing") {
    const exact = card ? animStageCard(card.card_id) : null;
    return exact || $("t-processing");
  }
  if (zone === "hand") return animHandAnchor(seat);
  if (zone === "equip") {
    const row = animZoneRow(seat, "equip");
    if (!row) return null;
    if (card && card.card_id !== undefined) {
      const exact = animFind(
        row, (node) => animAttr(node, "data-card-id") === String(card.card_id));
      if (exact) return exact;
    }
    if (card && card.slot) {
      const bySlot = animFind(
        row, (node) => animAttr(node, "data-slot") === String(card.slot));
      if (bySlot) return bySlot;
    }
    return row;
  }
  if (zone === "judge") {
    const row = animZoneRow(seat, "judge");
    if (!row) return null;
    if (card && card.card_id !== undefined) {
      const exact = animFind(
        row, (node) => animAttr(node, "data-card-id") === String(card.card_id));
      if (exact) return exact;
    }
    const empty = animFind(
      row,
      (node) => animAttr(node, "data-slot") === "judge" && nodeHasClass(node, "empty"),
    );
    return empty || row;
  }
  if (zone === "hero") {
    const row = animZoneRow(seat, "hero");
    return row || animSeatNode(seat);
  }
  return null;
}

/** 起点 = **起飞时真实所在的位置**（D2）：在舞台上就从舞台卡出发，否则从来源区锚点。 */
function animSourceNode(record) {
  const cards = (record && record.cards) || [];
  const from = (record && record.from_zone) || {};
  for (const card of cards) {
    const staged = card && card.card_id !== undefined ? animStageCard(card.card_id) : null;
    if (staged) return staged;
  }
  const anchor = animAnchorFor(from, cards[0] || null);
  if (anchor) return anchor;
  if (String(from.zone) === "processing") return $("t-processing");
  return null;
}

/* ------------------------------------------------------------ 层上的读法 */

function animLayer() {
  return $("t-anim-flights");
}

function animMarkMissing(which) {
  const layer = animLayer();
  if (layer) layer.setAttribute("data-anim-missing", String(which));
}

function animMarkUnregistered(record) {
  const layer = animLayer();
  if (!layer) return;
  const to = record && record.to_zone ? record.to_zone.zone : "?";
  layer.setAttribute("data-anim-unregistered", `${(record && record.reason) || "?"}->${to}`);
}

function animResetReadings() {
  const layer = animLayer();
  if (!layer) return;
  layer.removeAttribute("data-anim-missing");
  layer.removeAttribute("data-anim-unregistered");
}

/* ------------------------------------------------------------ 飞行器 */

/** 牌面副本：有牌面就画真卡面（隐藏侧画牌背「?」）。 */
function animFlightNode(card, hidden) {
  let node;
  if (!hidden && card && card.card_id !== undefined && typeof cardFaceNode === "function") {
    node = cardFaceNode(card, { static: true });
  } else {
    node = document.createElement("div");
    node.className = "card face unknown";
    node.textContent = "?";
    node.setAttribute("data-anim-hidden", "true");
  }
  node.classList.add("anim-flight");
  // 位置写死：起飞时量一次，之后不跟随任何重绘（D1）。
  node.style.position = "absolute";
  node.style.left = "0";
  node.style.top = "0";
  node.style.margin = "0";
  node.style.transformOrigin = "50% 50%";
  node.style.pointerEvents = "none";
  node.tabIndex = -1;
  return node;
}

/** 一只飞行器：起飞时量一次起 / 落点矩形，位置写死成 left/top + translate 动画。 */
function animSpawn(layer, layerRect, plan, card, index, toRect) {
  const node = animFlightNode(card, plan.hidden || !card);
  layer.appendChild(node);
  const own = animRect(node) || { width: 88, height: 124 };
  const width = own.width || 88;
  const height = own.height || 124;
  const fromRect = plan.sourceRect;
  // 小锚点（牌堆 / 弃牌计数）出来的牌略缩一点，免得盖满整行顶栏。
  const fromScale = fromRect.width < width * 0.6 ? 0.72 : 1;
  const startX = fromRect.left - layerRect.left + fromRect.width / 2 - width / 2;
  const startY = fromRect.top - layerRect.top + fromRect.height / 2 - height / 2;
  const endX = toRect.left - layerRect.left + toRect.width / 2 - width / 2;
  const endY = toRect.top - layerRect.top + toRect.height / 2 - height / 2;

  // D5：N 张同时起飞时沿**路径法向**散开，免得 N 张精确叠成一张。
  const count = Math.max(1, Number(plan.count) || 1);
  const fanPx = Math.max(0, Number(animConfig.fan_px) || 0);
  const offset = count > 1 ? (index - (count - 1) / 2) * fanPx : 0;
  const dx = endX - startX;
  const dy = endY - startY;
  const length = Math.max(1, Math.hypot(dx, dy));
  const normalX = (-dy / length) * offset;
  const normalY = (dx / length) * offset;
  node.style.left = `${startX + normalX}px`;
  node.style.top = `${startY + normalY}px`;

  const total = Math.max(0, Number(plan.duration) || 0);
  const fanMs = Math.max(0, Number(animConfig.fan_ms) || 0);
  const fanDelay = count > 1 ? Math.round((fanMs * index) / (count - 1)) : 0;
  const delay = (Number(plan.replaceDelay) || 0) + fanDelay;
  node.setAttribute("data-anim-kind", plan.kind);
  node.setAttribute("data-anim-index", String(index));
  const remove = () => {
    if (node.parentNode) node.parentNode.removeChild(node);
  };
  if (typeof node.animate !== "function") {
    remove();
    return null;
  }
  const animation = node.animate(
    [
      { transform: `translate(0px, 0px) scale(${fromScale})`, opacity: 1, offset: 0 },
      {
        transform: `translate(${dx * 0.55 + normalX * 0.2}px, ${dy * 0.55 + normalY * 0.2}px)`
          + ` scale(${(1 + fromScale) / 2})`,
        opacity: 1,
        offset: 0.55,
      },
      { transform: `translate(${dx}px, ${dy}px) scale(.55)`, opacity: 0.05, offset: 1 },
    ],
    {
      duration: total,
      delay,
      easing: "cubic-bezier(.32,.72,.35,1)",
      fill: "forwards",
    },
  );
  if (animation) {
    animation.onfinish = remove;
    animation.oncancel = remove;
  }
  return node;
}

/* ------------------------------------------------------------ 阶段 / 回合飘字（票 04 / D10） */

/** 六个阶段的中文名：`PHASE_<名字>_START` → 「<名字>开始」（D10 的六种）。
 *
 * 表驱动：引擎当前只发 `PHASE_DRAW_START` / `PHASE_PLAY_START` / `PHASE_DISCARD_START`
 * 三个「开始时」（准备 / 判定 / 结束的 `_START` 在 `EMPTY_LOG`，见 `sgs/timing/enum.py`），
 * 但 D10 登记的是六个，来什么画什么、不按"引擎会不会发"分叉。
 */
const PHASE_FLOAT_LABELS = {
  PREPARE: "准备阶段",
  JUDGE: "判定阶段",
  DRAW: "摸牌阶段",
  PLAY: "出牌阶段",
  DISCARD: "弃牌阶段",
  FINISH: "结束阶段",
};

const PHASE_FLOAT_START_RE = /^PHASE_([A-Z]+)_START$/;

/** 定位的设计单位：判定 / 装备区上方的空隙、夹进壳内的边距、淡出时上浮的距离。 */
const PHASE_FLOAT_GAP_UNITS = 6;
const PHASE_FLOAT_MARGIN_UNITS = 6;
const PHASE_FLOAT_RISE_UNITS = 20;

/** 阶段飘字层：与飞行层同形的覆盖层，**第一次飘字时**由本文件建出来（不改 index.html）。
 *
 * 层随 `#table-shell` 走，层级 17（高于技能名飘字 16、低于飞行器 18 与弹窗 60）。
 * 壳 / 座位在每次重绘都会被重建，但层不在它们里面，故不会被一次 commit 抹掉。
 */
function animPhaseLayer() {
  const shell = $("table-shell");
  const existing = $("t-anim-phase");
  if (existing && existing.parentNode === shell) return existing;
  if (!shell) return null;
  const layer = existing || document.createElement("div");
  layer.id = "t-anim-phase";
  layer.className = "anim-phase-layer";
  layer.setAttribute("aria-hidden", "true");
  shell.appendChild(layer);
  return layer;
}

/** 该座位**判定 / 装备区上方**的锚点（D10）：对手 = 右侧判定行（没有就装备格），
 *  自己 = `#t-self-zones`（判定 + 装备整块，住在底部自己面板的最右）。
 *
 *  刻意**不**用武将块：武将块正上方是技能名飘字（U3-04 / D9），两层各走各的锚，
 *  于是同座位"发动技能 + 阶段切换"同时发生时也不会叠在一起。
 */
function animPhaseAnchor(seat) {
  const number = Number(seat);
  if (number === animMySeat()) {
    const zones = $("t-self-zones");
    return zones ? { node: zones, source: "self-zones" } : null;
  }
  const judge = animZoneRow(number, "judge");
  if (judge) return { node: judge, source: "judge-row" };
  const equip = animZoneRow(number, "equip");
  if (equip) return { node: equip, source: "equip-grid" };
  return null;
}

/** 一条 `timing` 记录的座位：`record.actor`；为 null（帧外时机的兜底）时退回当前座位。 */
function animPhaseSeat(record) {
  const actor = record ? record.actor : null;
  if (actor === null || actor === undefined) return animMySeat();
  const number = Number(actor);
  return Number.isFinite(number) ? number : animMySeat();
}

/** 一条 `timing` 记录的飘字文本；不归本层负责的时机返回 `null`（零动作）。
 *
 * `TURN_START` 的武将名走接入层**已有**的展示名（`heroNameOf`），前端不新建映射；
 * 拿不到名字（`（无武将）` / 空串）时退回座位号（票面验收）。
 */
function animPhaseText(timing, seat) {
  const match = PHASE_FLOAT_START_RE.exec(timing);
  if (match) {
    const label = PHASE_FLOAT_LABELS[match[1]];
    return label ? `${label}开始` : null;
  }
  if (timing === "TURN_START") {
    const view = tableViewForRender();
    const name = String(heroNameOf(playerAt(view, seat), view) || "").trim();
    const label = name && name !== "（无武将）" ? name : `座位 ${seat}`;
    return `轮到 ${label}`;
  }
  return null;
}

/** 放一枚阶段飘字：位置 / 寿命 / 动画都在这里定，**不排计时器、不碰播放时钟**。
 *
 * 锚点中心 = `left`（CSS 的 `translate(-50%, -100%)` 把它读成水平中心），锚点上沿留
 * 一个空隙后 = `top`（= 飘字的**底边**）。贴边的座位夹进壳内：自己的判定 / 装备区在最右，
 * 不夹会顶出 `#table-shell`；夹到了就在节点上留 `data-anim-phase-clamped=left|right`。
 */
function animShowPhaseFloat(seat, text, timing) {
  const layer = animPhaseLayer();
  const shell = $("table-shell");
  const anchor = animPhaseAnchor(seat);
  if (!layer || !shell || !anchor) return null;
  const shellRect = animRect(shell);
  const layerRect = animRect(layer) || shellRect;
  const anchorRect = animRect(anchor.node);
  const lifetime = Number(animConfig.phase_float_ms);
  if (!shellRect || !layerRect || !anchorRect || !(lifetime > 0)) return null;

  const dsu = designUnitOf(shell);
  const margin = PHASE_FLOAT_MARGIN_UNITS * dsu;
  const node = document.createElement("div");
  node.className = "anim-phase-float";
  node.textContent = text;
  node.setAttribute("data-anim-phase-seat", String(seat));
  node.setAttribute("data-anim-phase-timing", String(timing));
  node.setAttribute("data-anim-phase-anchor", anchor.source);
  layer.appendChild(node);

  // 先量自己的宽度再夹：宽度由文本决定（CSS 的 padding / 字号都是设计单位）。
  const half = ((animRect(node) || {}).width || 0) / 2;
  const minLeft = margin + half;
  const maxLeft = (Number(shellRect.width) || 0) - margin - half;
  let left = anchorRect.left - layerRect.left + (Number(anchorRect.width) || 0) / 2;
  let clamped = "";
  if (maxLeft >= minLeft) {
    if (left > maxLeft) {
      left = maxLeft;
      clamped = "right";
    } else if (left < minLeft) {
      left = minLeft;
      clamped = "left";
    }
  }
  node.style.left = `${Math.round(left)}px`;
  node.style.top = `${Math.round(anchorRect.top - layerRect.top - PHASE_FLOAT_GAP_UNITS * dsu)}px`;
  if (clamped) node.setAttribute("data-anim-phase-clamped", clamped);

  const remove = () => {
    if (node.parentNode) node.parentNode.removeChild(node);
  };
  if (typeof node.animate !== "function") {
    // 没有 Web Animations 就不留一枚永久节点（纯视觉层，退化成"不显示"）。
    remove();
    return null;
  }
  const fadeIn = Math.round(4 * dsu);
  const rise = Math.round(PHASE_FLOAT_RISE_UNITS * dsu);
  const animation = node.animate(
    [
      { opacity: 0, transform: `translate(-50%, -100%) translateY(${fadeIn}px)` },
      { opacity: 1, transform: "translate(-50%, -100%)", offset: 0.18 },
      { opacity: 1, transform: "translate(-50%, -100%)", offset: 0.7 },
      { opacity: 0, transform: `translate(-50%, -100%) translateY(-${rise}px)` },
    ],
    { duration: lifetime, easing: "ease-out", fill: "forwards" },
  );
  if (animation) {
    animation.onfinish = remove;
    animation.oncancel = remove;
  }
  return node;
}

/* ------------------------------------------------------------ 钩子（session.js 调用） */

/** 起飞前调用：登记 / 不飞判定 + **量一次源矩形**。任何异常都吞掉（不阻塞播放时钟）。 */
function animPlanFlight(record) {
  try {
    if (!record || record.kind !== "delta" || record.delta_type !== "card_move") return null;
    // 读法只对**牌移动**重置：一条缺失 / 未登记的读法留给下一次牌移动（与烟测），
    // 不被夹在中间的时机 / 帧记录顺手抹掉。
    animResetReadings();
    const rule = animRuleFor(
      record.reason,
      record.to_zone && record.to_zone.zone,
      record.from_zone && record.from_zone.zone,
    );
    if (!rule) {
      animMarkUnregistered(record);
      return null;
    }
    if (rule.kind === null) return null;
    const count = animCountOf(record);
    const duration = animRawDuration(rule.kind);
    if (duration <= 0 || count <= 0) return null;
    const sourceRect = animRect(animSourceNode(record));
    if (!sourceRect) {
      animMarkMissing("source");
      return null;
    }
    const cards = (record.cards || []).slice();
    return {
      kind: rule.kind,
      record,
      cards,
      hidden: cards.length === 0,
      count,
      duration,
      sourceRect,
      replaceDelay: rule.kind === "replace_out"
        ? Math.round(duration * ANIM_REPLACE_OUT_DELAY)
        : 0,
    };
  } catch (error) {
    return null;
  }
}

/** 重绘之后调用：量落点并**同步**起飞。返回实际出生的飞行器数量。 */
function animLaunchFlight(plan) {
  if (!plan) return 0;
  const layer = animLayer();
  if (!layer) return 0;
  try {
    const layerRect = animRect(layer) || { left: 0, top: 0, width: 0, height: 0 };
    let spawned = 0;
    for (let index = 0; index < plan.count; index += 1) {
      const card = plan.cards[index] || plan.cards[plan.cards.length - 1] || null;
      const toRect = animRect(animAnchorFor(plan.record.to_zone, card));
      if (!toRect) {
        animMarkMissing("destination");
        continue;
      }
      try {
        if (animSpawn(layer, layerRect, plan, card, index, toRect)) spawned += 1;
      } catch (error) {
        /* 单只失败不影响其余牌，也不影响播放时钟。 */
      }
    }
    return spawned;
  } catch (error) {
    return 0;
  }
}

/* 阶段 / 回合飘字（D10）：只放画面——`durationOf` 对非牌移动恒为 0，步长照旧只吃 base。
 * 任何异常都吞掉（缺失锚点 / 座位 / 层时静默跳过），绝不阻塞播放时钟。 */
function animPlanPhase(record) {
  try {
    if (!record || record.kind !== "timing") return null;
    const timing = String(record.timing || "");
    const seat = animPhaseSeat(record);
    const text = animPhaseText(timing, seat);
    if (text === null) return null;
    return animShowPhaseFloat(seat, text, timing);
  } catch (error) {
    return null;
  }
}

/* 页面启动即读时间表；失败 / 缺键的提示由 animLoadConfig 写一行。
 * `animReady` 是"时间表已生效"的公开口（票 02 的初始档位经 `animOnConfig` 订阅，
 * 需要等待启动的调用方 / 测试用它）。 */
const animReady = animLoadConfig();
