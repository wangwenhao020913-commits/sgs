/* SGS 前端 · 体力演出的**排播层**（vital-fx 票 02）：两条事件源 → 一把闸门 → 开演 →
 * 每帧合成 → 到点摘层；时停口径**按家族**登记，画布生命周期与伤害层同姿态。
 *
 * 为什么与 `damage-fx.js` **分开一份**（spec §Solution 2 / §Problem 3）：体力演出与伤害演出
 * 共用形状（队列 / 闸门 / 错开 / 画布 / 版式作废 / 换局清场）、**不共用户口**——
 * 事件源不同（失去体力 = 引擎缓冲 `life_loss_events`；回复 = `hp_change` 记录里 `after > before`
 * 的那一条）、时停口径不同（失去体力**永不冻**、回复 **3+ 档**冻）、而且
 * `outputs/sgs-heal-fx/SGS-INTEGRATION.md` §3 明说**别把回复塞进伤害的属性家族**：共用伤害的
 * cap / 错开 / 灰阶口径会打架。所以这里是**加**一条层，伤害那层一个字不动。
 *
 * **反方向零调用**：本文件不认识 `damageFxNatureKind` / `damageFxRendererFor` /
 * `damageFxCapFor`，也不读伤害层的在播表。只借两个方向的**只读**小工具，且都在**调用时**
 * 解析（`index.html` 里本文件排在它们之后）：
 *
 *   rectOf / heroBlockFor / descendantWithClass   → effects.js（读真实布局，非纯函数）
 *   stageNow                                      → session.js（唯一的时钟读口）
 *   $                                             → core.js（层节点 `#t-vital-fx`）
 *
 * 调用方（都排在它之后加载，故函数名在调用时才解析）：
 *
 *   session.js:stepPlayback      → fireHpLossEventsThrough(record.seq) / fireVitalEventsThrough(record)
 *   table.js:renderTable 末尾    → vitalDropStalePlays()   重绘后按矩形判"版式变没变"
 *   session.js:commitView        → clearVitalFx()          换局清场（另清 state.lifeLossEvents）
 *
 * 音效那侧同样单向：本文件在四个点调 `sfx-ui.js`（开演 / 收尾 / 每帧 / 清场），它只接
 * `play` 对象，不读这里的在播表；声道池长读本文件的 `vitalFxCapFor(family)`（唯一的 cap 口）。
 *
 * **家族表是唯一查表处**（票面 §1）：`holdsAt` 是时停真源（`Infinity` = 永不冻、`3` = 3+ 档
 * 冻），`renderer` / `fallback` 存**全局名**、调用时解析，`fallback: null` = 模块缺件就不播
 * （与雷 / 火同姿态，事件仍被消费），`cap` 是 `anim.ini` 的键名（只在这张表里写一次）。
 *
 * 硬口径（spec §演出口径）：**轻演出**（失去体力全档、回复 1 / 2 档）相邻开演错开 120ms、
 * 按**该家族自己的 cap** 并发（1–8，缺省 2）、超出丢队尾；**重演出**（回复 3+ 档）**串行且
 * 独占牌桌**（一条播满自己的 `total` 才接下一条，`finishVitalPlay` 末尾续泵）；卡是**该家族的
 * 排队上限**（在播 + 排队），判在入队处。摘层用 `create()` 返回的 `duration`（= 该档 `total`）。
 */
"use strict";

/* -------------------------------------------------- 家族表（唯一查表处） */

/** 体力演出家族 → **唯一**的一张表。
 *
 * `holdsAt` = **从第几档起冻住回放**，这是本 effort 唯一的时停真源（`vitalFxHoldFor` 只读它）：
 * 失去体力 `Infinity`（永不冻，用户口径）、回复 `3`（3+ 档冻）。
 *
 * `renderer` 存**全局名**，调用时才解析——`index.html` 里两个画面模块（票 03 / 04 的
 * `fx-hp-loss.js` / `fx-heal.js`）排在本文件**之后**，顶层此时还没有它们。`fallback: null` =
 * 缺件就**不播**（与雷 / 火同姿态），事件仍被消费。
 *
 * `cap` 是 `anim.ini` 的键名：键名只在这张表里写一次，读的一侧是 `vitalFxCapFor(family)`——
 * 两个家族各一条并发表，调体力不该顺手改了伤害的行为。
 *
 * `stagger` = 这一族轻演出之间要不要错开 120ms（两族今天都是 true；字段放着是为了让闸门
 * 读表、而不是写死一条 if）。
 */
const VITAL_FX_FAMILIES = {
  HP_LOSS: { renderer: "SgsHpLossFX", fallback: null, holdsAt: Infinity,
             cap: "hp_loss_fx_concurrency", stagger: true },
  HEAL:    { renderer: "SgsHealFX",    fallback: null, holdsAt: 3,
             cap: "heal_fx_concurrency",   stagger: true },
};

/** 两个家族的规范名（写进 `play.family` / 读数 / 暂停原因串）。 */
const VITAL_FX_HP_LOSS = "HP_LOSS";
const VITAL_FX_HEAL = "HEAL";

/** 相邻开演错开（spec §多目标：同一家族队列内 120ms）。
 *
 * 与伤害层的 120 只是同值巧合：两层各有各的闸门、各有各的队列，将来会各自调。 */
const VITAL_FX_STAGGER_MS = 120;

/** 回复的**同座位**窗口（spec §多目标）：500ms 内同一张牌又来一条回复 ⇒ 旧的一条提前收掉。
 *  只对回复（全牌光效叠在一起会互相盖住）；失去体力没有"牌面被盖住"的问题。 */
const VITAL_FX_SAME_SEAT_MS = 500;

/** 并发上限的**缺省**值（spec §多目标：缺省 2——"失去体力最多 2 条"，这套比剑光安静，
 *  叠 3 条会很吵）。生效值按家族读 `anim.ini` 的并发表。 */
const VITAL_FX_CONCURRENCY = 2;

/** 并发表的钳制范围（1–8）：`0` / 负数 → 1，`> 8` → 8。钳制在**读者侧**。 */
const VITAL_FX_CAP_MIN = 1;
const VITAL_FX_CAP_MAX = 8;

/** 档位封顶：`amount ≥ 3` 按 3 档演（spec §档位：1 / 2 / 3 点各是第 1 / 2 / 3 档）。
 *  档位只决定强度，飘字照旧是真实点数。 */
const VITAL_FX_MAX_VARIANT = 3;

/** 家族名归一成**表里那一行的名字**（未登记返回空串）；大小写不敏感。 */
function vitalFxFamilyName(family) {
  const name = family === null || family === undefined ? "" : String(family).toUpperCase();
  return Object.prototype.hasOwnProperty.call(VITAL_FX_FAMILIES, name) ? name : "";
}

/** 家族名 → 表里那一行；未登记的家族返回 `null`（**不** fail-open 到别的家族：体力演出
 *  只有两条事实源，认不出来就是没有演出）。 */
function vitalFxFamilyFor(family) {
  const name = vitalFxFamilyName(family);
  return name ? VITAL_FX_FAMILIES[name] : null;
}

/** 家族 → 画面模块（**分派查表只许写在这一处**）。
 *
 * `renderer` / `fallback` 都是**全局名**，调用时才解析：`index.html` 里两个画面模块
 * （票 03 的 `fx-hp-loss.js`、票 04 的 `fx-heal.js`）排在本文件**之后**，顶层此时还没有它们。
 * 缺件（本票交付时就是这种情况）⇒ null ⇒ `startVitalPlay` 不播，事件仍被消费（与雷 / 火
 * 同姿态：`fallback: null` 是"不回落别的家族"，不是"回落到伤害"）。 */
function vitalFxRendererFor(family) {
  const spec = vitalFxFamilyFor(family);
  if (!spec) return null;
  return window[spec.renderer] || (spec.fallback ? window[spec.fallback] : null) || null;
}

/** 一档演出要读的字段名；键名 = `<模块的 TIMING_PREFIX><档>_<字段>`（见 `anim.ini`）。
 *
 * **字段名名单不在这份文件里维护**：真源是各画面模块的 `DEFAULT_TIMING`，它导出
 * `TIMING_FIELDS`（由那张表的键生成，票 03 / 04 落成）。画面模块缺件 ⇒ 空表。
 */
function vitalFxTimingFields(family) {
  const api = vitalFxRendererFor(family);
  return (api && api.TIMING_FIELDS) || [];
}

/** 这一族的时间轴前缀：模块自报 `TIMING_PREFIX` 优先，缺件时按家族名派生
 *  （`HP_LOSS` → `hp_loss_fx_`、`HEAL` → `heal_fx_`）——与 `anim.ini` 的键块同名，
 *  于是没有第二份前缀表。 */
function vitalFxTimingPrefix(family) {
  const api = vitalFxRendererFor(family);
  const declared = api && api.TIMING_PREFIX;
  if (typeof declared === "string" && declared) return declared;
  return `${String(family).toLowerCase()}_fx_`;
}

/** 从生效中的 `anim.ini` 表取**某一档**的时长（毫秒），原样交给画面模块的 `create()`。
 *
 * **这里不给兜底值、也不钳制**（与 `damageFxTiming` 同一姿态）：内置默认（模块的
 * `DEFAULT_TIMING`）与钳制都在画面模块内部——它是时间轴语义的唯一拥有者，本层只负责
 * "把用户改的数字搬过去"。缺键 ⇒ 不放进返回的表 ⇒ 模块退默认。
 */
function vitalFxTiming(family, variant) {
  const timing = {};
  if (typeof animTiming !== "function") return timing;
  const prefix = vitalFxTimingPrefix(family);
  const id = vitalFxVariantOf(variant);
  for (const field of vitalFxTimingFields(family)) {
    const key = `${prefix}${id}_${field}`;
    const value = animTiming(key);
    if (value !== undefined && value !== null && Number.isFinite(Number(value))) {
      timing[key] = Number(value);
    }
  }
  return timing;
}

/** 某家族自己的同时在播上限（spec §多目标：1–8，缺省 **2**）。
 *
 * 读 `anim.ini` 的并发表：失去体力 `hp_loss_fx_concurrency`、回复 `heal_fx_concurrency`
 * （键名只在 `VITAL_FX_FAMILIES` 里写一次）。**这是唯一的 cap 口**：排播闸门与
 * `sfx-ui.js` 的声道池长度都读它。
 *
 * 钳制在**读者侧**（照 `damageFxCapFor` 的姿态：`animTiming` 只搬数字、不给兜底也不钳）：
 * 缺键 / 非数字 → 2；`0` 或负数 → 1；`> 8` → 8；小数取整（cap 是**条数**）。
 * **调用时才解析**——`anim.ini` 的加载是异步的，顶层读只会读到空表。
 */
function vitalFxCapFor(family) {
  const spec = vitalFxFamilyFor(family);
  const key = spec ? spec.cap : "";
  const raw = typeof animTiming === "function" && key ? Number(animTiming(key)) : Number.NaN;
  const cap = Number.isFinite(raw) ? Math.round(raw) : VITAL_FX_CONCURRENCY;
  return Math.max(VITAL_FX_CAP_MIN, Math.min(VITAL_FX_CAP_MAX, cap));
}

/** `amount` → **档位**（1..3，取整、非法按 1、封顶 3）。**唯一的换算处**：开演、排播闸门
 *  （判"重演出"）、暂停判据（判要不要冻回放）都读它——三处各写一遍就会漂。 */
function vitalFxVariantOf(amount) {
  const rounded = Math.round(Number(amount));
  const points = Number.isFinite(rounded) && rounded > 0 ? rounded : 1;
  return Math.max(1, Math.min(VITAL_FX_MAX_VARIANT, points));
}

/** 这一条体力演出**要不要冻住回放**（spec §家族与键的时停列）。
 *
 * - 失去体力：`holdsAt: Infinity` ⇒ **永远** false（用户口径："失去体力均无时停"）；
 * - 回复：`holdsAt: 3` ⇒ 3 / 3+ 档 true，1 / 2 档 false。
 *
 * 排播侧的登记表只认"有没有人要求暂停"，不解读档位——故这里是本 effort **唯一**的时停判据。
 */
function vitalFxHoldFor(family, variant) {
  const spec = vitalFxFamilyFor(family);
  if (!spec) return false;
  const holdsAt = Number(spec.holdsAt);
  // `Infinity`（永不冻）走这一条：`Number.isFinite` 为 false。
  if (!Number.isFinite(holdsAt)) return false;
  return vitalFxVariantOf(variant) >= holdsAt;
}

/* -------------------------------------------------- 队列 / 在播表 */

/** 待开演的事件（已到 seq、还没轮到）：`{family, seq, seat, amount}`。**不跨批攒**：
 *  换局 / 版式作废 / 演出层不在了都清。
 *
 *  两个事件源（`fireHpLossEventsThrough` 读引擎缓冲、`fireVitalEventsThrough` 读记录）各自
 *  入队，闸门只有这一把——同样的错开 / cap / 串行口径对两条家族一致，只是**数值**从列各读各的。 */
let vitalQueue = [];

/** 正在播的演出：`[{family, seq, seat, variant, amount, fx, canvas, slot, plan, startedAt,
 *  duration, timer, sfxLane, sfxVoice}]`。 */
const vitalPlays = [];

/** 累计开演次数（只增；写在层的 `data-vital-fx-plays` 上，实机 / 烟测都读得懂）。 */
let vitalStartedCount = 0;

/** 层里那**一张**可见 canvas（跨演出复用；闲置时摘掉，于是层是空的）。 */
let vitalCanvas = null;

/** 舞台离屏画布池：一张离屏舞台对应一个在播槽位（槽号取最小空闲槽），池长随在播数
 *  **自然增长**（上限 = 两族 cap 之和，1~8 × 2）。按需建一次、跨演出复用。 */
const vitalStagePool = [];

let vitalStaggerTimer = null;
let vitalFrame = null;
let vitalNextStartAt = 0;
/** 闸门重入守卫：同座位折叠 / 版式作废都会在开演路径里回调 `pumpVitalQueue`，重入会让
 *  "正在起的这一条"和"队列里下一条"同时开演。 */
let vitalPumping = false;

/** 覆盖层节点（`index.html` 的 `#t-vital-fx`）；没有它（回放页 / 首屏）就什么都不做。 */
function vitalFxLayer() {
  const layer = $("t-vital-fx");
  return layer && layer.classList ? layer : null;
}

/** 演出画布的分辨率上限：牌桌是 1920 设计单位撑满屏，1.5 倍已看不出锯齿。 */
function vitalFxDpr() {
  const ratio = Number(window.devicePixelRatio);
  return Number.isFinite(ratio) && ratio > 0 ? Math.min(1.5, ratio) : 1;
}

/** 覆盖层的目标画布尺寸（CSS px）。牌桌档按它建画布、内部 1:1 合成。 */
function vitalFxView(layerRect) {
  return {
    width: Math.max(1, Math.round(Number(layerRect && layerRect.width) || 0)),
    height: Math.max(1, Math.round(Number(layerRect && layerRect.height) || 0)),
  };
}

/** 一张武将块 → **覆盖层坐标**里的矩形（CSS px；量一次 `getBoundingClientRect()`，
 *  不拿 `--dsu` 手算）。牌桌档的坐标系就是覆盖层的 CSS 像素，故这里不做任何比例换算。 */
function vitalFxBoxOf(node, layerRect) {
  const rect = rectOf(node);
  const left = Number(rect.left) || 0;
  const top = Number(rect.top) || 0;
  const width = Number(rect.width) || 0;
  const height = Number(rect.height) || 0;
  return {
    cx: left - (Number(layerRect.left) || 0) + width / 2,
    cy: top - (Number(layerRect.top) || 0) + height / 2,
    w: width,
    h: height,
  };
}

/** 座位矩形的可比较读法：版式是否变过（演出还锚得准不准）由它判。 */
function vitalFxBoxKey(box) {
  return [box.cx, box.cy, box.w, box.h]
    .map((value) => Math.round(Number(value) || 0))
    .join(",");
}

/** 目标武将块里**体力条**的下沿（相对武将块左上角的 CSS px）：飘字不许压到它。
 *
 * 判据照 `damage-fx.js:damageFxNumberKeepOut`：自己的体力条是 `index.html` 的常驻节点
 * `#t-self-hp`（烟测垫片不建静态父子树，只能按 id 取），对手的由 `seat-view.js:hpBarNode`
 * 建 `.hp-bar`；量不到 ⇒ `null`，交给画面模块的兜底比例。
 */
function vitalFxNumberKeepOut(block, seat) {
  if (!block) return null;
  const selfSeat = state.view && state.view.snapshot ? state.view.snapshot.seat : null;
  let bar = null;
  if (selfSeat !== null && selfSeat !== undefined && Number(seat) === Number(selfSeat)) {
    bar = $("t-self-hp");
  }
  if (!bar) bar = descendantWithClass(block, "hp-bar");
  if (!bar) return null;
  const barRect = rectOf(bar);
  const blockRect = rectOf(block);
  const blockHeight = Number(blockRect.height) || 0;
  const barHeight = Number(barRect.height) || 0;
  if (!(blockHeight > 0) || !(barHeight > 0)) return null;
  const bottom = (Number(barRect.bottom) || 0) - (Number(blockRect.top) || 0);
  return Number.isFinite(bottom) ? bottom : null;
}

/** 一条体力事实 → 开演要用的几样：目标座位矩形（覆盖层坐标）、档位、真实点数。
 *
 * - **只量目标一块**（spec §家族与键）：失去体力没有来源、回复也不表现"从别处飞来"；
 * - 目标块量不到（还没渲染 / 已阵亡）⇒ `null`：宁可不演，也不要演在半空；
 * - `amount ≥ 3` ⇒ 按 3 档演，飘字仍是真实点数（档位只管强度）。
 */
function vitalFxPlanFor(event) {
  const layer = vitalFxLayer();
  if (!layer) return null;
  const family = vitalFxFamilyName(event && event.family);
  if (!family) return null;
  const seat = Number(event && event.seat);
  if (!Number.isFinite(seat)) return null;
  const block = heroBlockFor(seat);
  if (!block) return null;
  const layerRect = rectOf(layer);
  const dst = vitalFxBoxOf(block, layerRect);
  if (!(dst.w > 0) || !(dst.h > 0)) return null;
  const raw = Math.round(Number(event.amount));
  const amount = Number.isFinite(raw) && raw > 0 ? raw : 1;
  const variant = vitalFxVariantOf(amount);
  return {
    family,
    seat,
    dst,
    variant,
    amount,
    // 版式作废判据只比目标那一块（`vitalDropStalePlays` 读它）。
    keys: { dst: vitalFxBoxKey(dst) },
    // 飘字的硬线：目标武将块的体力条下沿（相对块左上角；量不到是 null）。
    keepOut: vitalFxNumberKeepOut(block, seat),
  };
}

/** 找一个空闲的离屏槽（最小空闲号）。在播上限由排播闸门**按家族**保证，故必有一个空槽。 */
function vitalFxSlot() {
  const used = new Set(vitalPlays.map((play) => play.slot));
  let slot = 0;
  while (used.has(slot)) slot += 1;
  return slot;
}

/** 覆盖层那张唯一的 canvas：按层的大小定尺寸，**复用同一个节点**。 */
function vitalFxMountCanvas(layer) {
  if (!vitalCanvas) vitalCanvas = document.createElement("canvas");
  if (vitalCanvas.className !== "vital-fx-canvas") vitalCanvas.className = "vital-fx-canvas";
  const rect = rectOf(layer);
  const width = Math.max(1, Math.round(Number(rect.width) || 0));
  const height = Math.max(1, Math.round(Number(rect.height) || 0));
  const dpr = vitalFxDpr();
  const deviceW = Math.round(width * dpr);
  const deviceH = Math.round(height * dpr);
  // 改尺寸会清空画布，故只在真的变了的时候写。
  if (vitalCanvas.width !== deviceW || vitalCanvas.height !== deviceH) {
    vitalCanvas.width = deviceW;
    vitalCanvas.height = deviceH;
  }
  if (vitalCanvas.parentNode !== layer) layer.appendChild(vitalCanvas);
  return vitalCanvas;
}

/** 清可见画布并把它摘下来：层因此回到"空"（下一段演出重新挂回去，节点仍复用同一个）。 */
function vitalClearCanvas() {
  const canvas = vitalCanvas;
  if (canvas && typeof canvas.getContext === "function") {
    const ctx = canvas.getContext("2d");
    if (ctx && ctx.clearRect) {
      if (ctx.setTransform) ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }
  if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
}

/** 层上的可读状态：`data-vital-fx-live` = 正在播几条、`data-vital-fx-plays` = 累计几次、
 *  `data-vital-fx-hold` = 这条演出正要求"暂停回放"吗（`"1"` / 空串）；最近一条演出的
 *  `family` / `variant` / `amount` 也写在这里（画布像素不可断言，实机验收读这几个数）。
 */
function vitalFxMark(layer, play = null) {
  if (!layer || !layer.setAttribute) return;
  layer.setAttribute("data-vital-fx-live", String(vitalPlays.length));
  layer.setAttribute("data-vital-fx-plays", String(vitalStartedCount));
  layer.setAttribute("data-vital-fx-hold", vitalFxPlaybackHold() ? "1" : "");
  if (!play) return;
  layer.setAttribute("data-vital-fx-family", String(play.family));
  layer.setAttribute("data-vital-fx-variant", String(play.variant));
  layer.setAttribute("data-vital-fx-amount", String(play.amount));
}

/** 摘掉一条演出的计时器（寿命只有一只：本层没有整屏闪窗口）。两处调用：自然到点与清场。 */
function vitalFxDropPlayTimers(play) {
  if (!play) return;
  if (play.timer !== null && play.timer !== undefined && window.clearTimeout) {
    window.clearTimeout(play.timer);
  }
  play.timer = null;
}

/** 一段演出到点：从在播表里摘掉，最后一帧合成完之后清画布、摘 canvas（不留残影）。 */
function finishVitalPlay(play) {
  const at = vitalPlays.indexOf(play);
  if (at >= 0) vitalPlays.splice(at, 1);
  vitalSfxStop(play);
  vitalFxDropPlayTimers(play);
  if (!vitalPlays.length) {
    vitalStopFrame();
    vitalClearCanvas();
  }
  vitalFxMark(vitalFxLayer());
  // 重演出串行的**交接点**：上一条彻底结束（摘层）后紧接着开队列里的下一条。
  pumpVitalQueue();
}

/** 回复的同座位规则（spec §多目标）：同一张牌 500ms 内又来一条 ⇒ 把旧的一条**提前收掉**
 *  再起新的（两条全牌光效叠着会互相盖住）；跨座位互不影响。 */
function vitalFxFoldSameSeat(seat) {
  const now = Number(stageNow());
  const stale = vitalPlays.filter((play) => play.family === VITAL_FX_HEAL
    && Number(play.seat) === Number(seat)
    && now - Number(play.startedAt) < VITAL_FX_SAME_SEAT_MS);
  for (const play of stale) finishVitalPlay(play);
}

/** 一条体力演出开演：取离屏槽 → 按家族选画面模块 → `create()`（game 档 + 目标矩形 +
 *  真实值 + bare）→ 定时摘层。
 *
 * 摘层时刻用 `create()` 返回的 `duration`（= `anim.ini` 的 `total`）**定时**，不靠 rAF
 * 自然停：音效是另一条时钟，两边必须读同一份 timeline。画面模块缺件 ⇒ 不播（返回 null），
 * **事件仍被消费**——与雷 / 火同姿态。
 */
function startVitalPlay(event) {
  const layer = vitalFxLayer();
  const plan = event ? vitalFxPlanFor(event) : null;
  if (!layer || !plan) return null;
  const api = vitalFxRendererFor(plan.family);
  if (!api || typeof api.create !== "function") return null;
  if (plan.family === VITAL_FX_HEAL) vitalFxFoldSameSeat(plan.seat);
  const slot = vitalFxSlot();
  while (vitalStagePool.length <= slot) vitalStagePool.push(document.createElement("canvas"));
  const stageCanvas = vitalStagePool[slot];
  if (!stageCanvas || typeof stageCanvas.getContext !== "function") return null;
  vitalFxMountCanvas(layer);
  const view = vitalFxView(rectOf(layer));
  const fx = api.create(stageCanvas, {
    variant: plan.variant,
    profile: "game",
    // 字段表按家族取（票 03 / 04 的 `TIMING_FIELDS`），读的是同一份 anim.ini。
    timing: vitalFxTiming(plan.family, plan.variant),
    amount: plan.amount,
    // 体力演出**只量目标一块**：没有来源，`src` 恒为 null。
    stage: { src: null, dst: plan.dst },
    // 飘字不许压到体力条：硬线 = `.hp-bar` 的下沿（DOM 真值，量不到是 null）。
    numberKeepOut: plan.keepOut,
    view,
    dpr: vitalFxDpr(),
    // 牌桌档**只出特效**：宣纸底 + 样例武将牌一概不画（否则覆盖层不透明、牌桌被盖住）。
    bare: true,
  });
  const play = {
    family: plan.family,
    seq: Number(event.seq),
    seat: plan.seat,
    variant: plan.variant,
    amount: plan.amount,
    plan,
    fx,
    canvas: stageCanvas,
    slot,
    startedAt: stageNow(),
    duration: Math.max(0, Number(fx.duration) || 0) * 1000,
    timer: null,
    // 音效那侧的两口（`vitalSfxStart` 里填）：这条演出占用的声道与档位参数。
    sfxLane: null,
    sfxVoice: null,
  };
  vitalPlays.push(play);
  vitalStartedCount += 1;
  // 声道先起（紧接着下面就要画第一帧、并喂同一个 `seconds`）。开关关着 / 没解锁 /
  // 模块缺件时 `vitalSfxStart` 返回 null，画面照旧。
  vitalSfxStart(play);
  play.timer = window.setTimeout(() => finishVitalPlay(play), play.duration);
  vitalFxMark(layer, play);
  renderVitalFrame(stageNow());
  vitalRequestFrame();
  return play;
}

/** 一帧：每条在播的演出画进自己的离屏舞台，再合成到层里**唯一**那张 canvas 上。 */
function renderVitalFrame(now = stageNow()) {
  if (!vitalPlays.length) return;
  const layer = vitalFxLayer();
  const canvas = vitalCanvas;
  if (!layer || !canvas) return;
  for (const play of vitalPlays) {
    // 演出自己的时钟：画面与声音**同吃这一个 `seconds`**。
    const seconds = Math.min(
      Math.max(0, Number(now) - Number(play.startedAt)) / 1000,
      play.duration / 1000,
    );
    if (play.fx && typeof play.fx.draw === "function") play.fx.draw(seconds);
    vitalSfxTick(play, seconds);
  }
  const ctx = typeof canvas.getContext === "function" ? canvas.getContext("2d") : null;
  if (!ctx) return;
  const view = vitalFxView(rectOf(layer));
  const dpr = vitalFxDpr();
  if (ctx.setTransform) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, view.width, view.height);
  for (const play of vitalPlays) {
    if (!play.canvas) continue;
    // 1:1（离屏舞台与可见画布同尺寸、同 dpr）。
    ctx.drawImage(play.canvas, 0, 0, view.width, view.height);
  }
}

function vitalRequestFrame() {
  if (vitalFrame !== null) return;
  const raf = window.requestAnimationFrame;
  if (typeof raf !== "function") return;
  vitalFrame = raf(() => {
    vitalFrame = null;
    renderVitalFrame();
    if (vitalPlays.length) vitalRequestFrame();
  });
}

function vitalStopFrame() {
  if (vitalFrame === null) return;
  if (window.cancelAnimationFrame) window.cancelAnimationFrame(vitalFrame);
  vitalFrame = null;
}

/** 该家族**正在播**几条（闸门只认这一个计数）。 */
function vitalFxLiveOf(family) {
  const name = vitalFxFamilyName(family);
  let live = 0;
  for (const play of vitalPlays) {
    if (play && play.family === name) live += 1;
  }
  return live;
}

/** 该家族**在播 + 排队**几条：cap 是"该家族的**排队**上限"（重演出串行后"同时在播"恒为 1，
 *  按在播判 cap 等于失效）。入队处用它丢掉超出的条目。 */
function vitalFxPendingOf(family) {
  const name = vitalFxFamilyName(family);
  let pending = vitalFxLiveOf(name);
  for (const event of vitalQueue) {
    if (event && event.family === name) pending += 1;
  }
  return pending;
}

/** 现在有没有**重演出**在播（`holdsAt` 档及以上）。重演出串行、独占牌桌，这是"下一条能不能上"
 *  的总闸——连轻演出也不许在这个窗口里抢跑。 */
function vitalFxHeavyLive() {
  for (const play of vitalPlays) {
    if (vitalFxHoldFor(play && play.family, play && play.variant)) return true;
  }
  return false;
}

/** 排播闸门：已到 seq 的事件逐条开演，轻演出相邻错开 120ms、按**该家族自己的** cap 并发；
 *  重演出（回复 3+）**串行且独占牌桌**。
 *
 * 超出上限的那几条在**入队处**就丢掉了（`fireHpLossEventsThrough` / `fireVitalEventsThrough`）：
 * 体力早已由服务端结算（体力条到位了），攒着只会把旧数字飘在新画面上。
 */
function pumpVitalQueue() {
  if (vitalPumping) return;
  vitalPumping = true;
  try {
    if (vitalStaggerTimer !== null) {
      if (window.clearTimeout) window.clearTimeout(vitalStaggerTimer);
      vitalStaggerTimer = null;
    }
    while (vitalQueue.length) {
      const event = vitalQueue[0];
      const family = event && event.family;
      const spec = vitalFxFamilyFor(family);
      const variant = vitalFxVariantOf(event && event.amount);
      const heavy = vitalFxHoldFor(family, variant);
      // 重演出在播 ⇒ 一个都不开（轻演出也不许抢跑），等它摘层——`finishVitalPlay` 续泵。
      if (vitalFxHeavyLive()) break;
      if (heavy && vitalPlays.length) break;
      // 轻演出：按**该家族自己的**并发表并发。
      if (!heavy && vitalFxLiveOf(family) >= vitalFxCapFor(family)) break;
      const wait = Math.round(vitalNextStartAt - stageNow());
      if (wait > 0) {
        vitalStaggerTimer = window.setTimeout(() => {
          vitalStaggerTimer = null;
          // 到点了：把闸门推到"现在"再重排——不重算等待，否则计时器早到 1~2ms 时会再排
          // 一次 120ms（同一条事件永远开不了演）。
          vitalNextStartAt = stageNow();
          pumpVitalQueue();
        }, wait);
        break;
      }
      startVitalPlay(vitalQueue.shift());
      // 重演出串行，下一条要等它摘层（那时早过点了），不必再错开。
      vitalNextStartAt = stageNow() + (heavy || !spec || !spec.stagger ? 0 : VITAL_FX_STAGGER_MS);
    }
  } finally {
    vitalPumping = false;
  }
}

/** 把 `seq` 及之前的**失去体力**事实排上（与 `fireDamageEventsThrough` 同构 / 同一条游标）。
 *
 * `state.lifeLossEvents` 是随视图下发的待触发队列（票 01 落的状态）：回放走到那条记录的
 * `seq` 才演出，于是墨在"这一下失去体力"发生的那一步漏下去，而不是响应一到就全部同时出现。
 * **触发点只有 `seq` 游标**——不在 `applyLiveDelta` 里、也不在提交视图时触发。
 *
 * 入队处按"该家族的排队上限"（在播 + 排队）丢掉超出的条目（spec §多目标）。
 */
function fireHpLossEventsThrough(seq = Infinity) {
  const events = state.lifeLossEvents || [];
  if (!events.length) return;
  const threshold = seq === null || seq === undefined ? Infinity : Number(seq);
  const due = [];
  const kept = [];
  for (const event of events) {
    const eventSeq = Number(event && event.seq);
    if (!Number.isFinite(eventSeq) || eventSeq <= threshold) due.push(event);
    else kept.push(event);
  }
  if (!due.length) return;
  state.lifeLossEvents = kept;
  let queued = 0;
  for (const event of due) {
    if (!event) continue;
    if (vitalFxPendingOf(VITAL_FX_HP_LOSS) >= vitalFxCapFor(VITAL_FX_HP_LOSS)) continue;
    vitalQueue.push({
      family: VITAL_FX_HP_LOSS,
      seq: event.seq,
      seat: event.seat,
      amount: event.amount,
    });
    queued += 1;
  }
  if (queued) pumpVitalQueue();
}

/** 一条记录 → **回复**事实：`hp_change` 里 `after > before` 的那一条（spec §家族与键）。
 *
 * 回复的事实源就是记录本身（`HpChangeDelta` 带 `seat / before / after / amount`），不需要
 * 服务端加通道——`flashHeroHp` 的 0.4s 闪照旧是兜底。判据只看记录，不认技能名。
 */
function vitalHealEventOf(record) {
  if (!record || record.kind !== "delta" || record.delta_type !== "hp_change") return null;
  const before = Number(record.before);
  const after = Number(record.after);
  const seat = Number(record.seat);
  if (!Number.isFinite(before) || !Number.isFinite(after) || !(after > before)) return null;
  if (!Number.isFinite(seat)) return null;
  return { family: VITAL_FX_HEAL, seq: record.seq, seat, amount: after - before };
}

/** 把**这一条记录**里的回复排上（`session.js:stepPlayback` 在 `applyRecordEffects` 之后调）。
 *
 * 与失去体力那条一样：入队处按该家族的排队上限丢弃超出的条目。
 */
function fireVitalEventsThrough(record) {
  const heal = vitalHealEventOf(record);
  if (!heal) return;
  if (vitalFxPendingOf(VITAL_FX_HEAL) >= vitalFxCapFor(VITAL_FX_HEAL)) return;
  vitalQueue.push(heal);
  pumpVitalQueue();
}

/** 版式变了就丢掉正在播的演出：座位块重建后矩形不同 ⇒ 演出的目标坐标已经不对了。
 *
 * 与伤害层同一条判据（`damageDropStalePlays`）：回放**每一步**都会重绘全表，一刀切会让
 * 1~2s 的演出活不过 100ms；这里按**几何**判断——目标武将块的矩形（四舍五入到 CSS 像素）
 * 没变就继续演。**由 `table.js:renderTable` 在重绘末尾显式调用**。
 */
function vitalDropStalePlays() {
  if (!vitalPlays.length) return;
  const layer = vitalFxLayer();
  if (!layer) {
    clearVitalFx();
    return;
  }
  const layerRect = rectOf(layer);
  const stale = vitalPlays.filter((play) => {
    const plan = play.plan;
    if (!plan || !plan.keys) return true;
    const block = heroBlockFor(plan.seat);
    if (!block) return true;
    return vitalFxBoxKey(vitalFxBoxOf(block, layerRect)) !== plan.keys.dst;
  });
  for (const play of stale) finishVitalPlay(play);
}

/** 换局 / 版式作废：清队列 + 摘层（照 `clearDamageFx` 的收尾姿态）。
 *
 * **调用方**：`session.js:commitView` 换会话时调它，并顺手清 `state.lifeLossEvents`；
 * `vitalDropStalePlays()` 发现覆盖层不在了也走这里（兜底）。
 */
function clearVitalFx() {
  if (vitalStaggerTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(vitalStaggerTimer);
    vitalStaggerTimer = null;
  }
  // 在播的演出连声音一起收掉——上一局那条回复不该在新桌上响完。
  vitalSfxStopAll();
  vitalQueue = [];
  for (const play of vitalPlays.splice(0, vitalPlays.length)) vitalFxDropPlayTimers(play);
  vitalStopFrame();
  vitalClearCanvas();
  vitalNextStartAt = 0;
  vitalFxMark(vitalFxLayer());
}

/* -------------------------------------------------- 时停（登记表，不写死） */

/** 「这条演出要求暂停记录回放吗」的只读判据（登记在 `session.js:registerPlaybackHold`）。
 *
 * **在播表 + 队列都要算**（票面 §3，照 `damageFxPlaybackHold` 的口径）：回复 3+ 档重演出串行，
 * 队列里还有一条就说明这个窗口没结束——"上一条摘层、下一条还没起"的缝隙也不许漏冻。
 *
 * 返回**原因串**：`"heal:3"` = 有 3+ 档回复在播 / 在排队；失去体力**永远**不返回原因
 * （`holdsAt: Infinity` ⇒ `vitalFxHoldFor` 恒 false）。空串 = 不要求暂停。
 */
function vitalFxPlaybackHold() {
  for (const play of vitalPlays) {
    if (vitalFxHoldFor(play && play.family, play && play.variant)) return vitalFxHoldReason(play.family);
  }
  for (const event of vitalQueue) {
    if (vitalFxHoldFor(event && event.family, vitalFxVariantOf(event && event.amount))) {
      return vitalFxHoldReason(event.family);
    }
  }
  return "";
}

/** 原因串 = `<家族小写>:<从第几档起冻>`（回复 ⇒ `"heal:3"`）；未登记家族返回空串。
 *  串里的数字来自家族表，不另写一份"3"。 */
function vitalFxHoldReason(family) {
  const spec = vitalFxFamilyFor(family);
  if (!spec) return "";
  return `${String(family).toLowerCase()}:${spec.holdsAt}`;
}

/* 把判据登记进排播侧的暂停表（`registerPlaybackHold` 住 `session.js`）。
 *
 * 登记是**加载时**的一次动作（判据本身读的是调用时的在播表 / 队列，故什么时候登记不影响它
 * 读到什么）：`index.html` 里 `session.js` 排在本文件之前。`typeof` 守卫与 `damage-fx.js` 末尾
 * 同口径——只加载本文件的探针里没有排播侧，本文件仍是一份独立的排播模块；真的缺了就**喊一声**，
 * 别让"切了脚本次序 ⇒ 回复 3+ 档的时停悄悄失效"没人看见。 */
if (typeof registerPlaybackHold === "function") {
  registerPlaybackHold("vital", vitalFxPlaybackHold);
} else if (typeof console !== "undefined" && console.warn) {
  console.warn("[playback-hold] 没有排播侧的 registerPlaybackHold：回复 3+ 档演出不会暂停回放");
}
