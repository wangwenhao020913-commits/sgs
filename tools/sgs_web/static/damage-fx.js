/* SGS 前端 · 无属性伤害演出的**排播 + 座位几何 + 画布生命周期**（damage-fx 票 05 / 06，
 * 票 09 从 `effects.js` 抽出）：队列 → 排播闸门 → 开演 → 每帧合成 → 到点摘层。
 *
 * 为什么单独一份（票 09 的切口）：这一段原本是 `effects.js` 的尾巴（~590 行），与动效层
 * **双向耦合**，所以票 08 先搬走音效、这一票再搬排播——拆完 `effects.js` 只剩动效层。
 *
 * **依赖方向单向：本文件 → `effects.js`**。只借四个"读真实布局"的小工具，且都在**调用时**
 * 解析（`index.html` 里本文件排在 `effects.js` 之后）：
 *
 *   rectOf / heroBlockFor / descendantWithClass   → effects.js（渲染层的小工具，非纯函数）
 *   stageNow                                      → session.js（唯一的时钟读口）
 *
 * **反方向零调用**——两个钩子都挪到了调用方（票 09 的解耦，不是本文件自己收的）：
 *
 *   table.js:renderTable  → damageDropStalePlays()   重绘后按矩形判"版式变没变"
 *   session.js:commitView → clearDamageFx()          换局清场（另清 state.damageEvents）
 *
 * 音效那侧同样单向：本文件在四个点调 `sfx-ui.js`（开演 / 收尾 / 每帧 / 清场），它只接
 * `play` 对象，不读这里的在播表。
 *
 * 硬口径（spec §演出口径，票面要求逐字不变）：相邻开演错开 120ms、同时在播上限 3 条、
 * 超出丢队列尾；只演无属性伤害（`null` ≡ 无属性）；摘层用 `anim.ini` 的 `total` 定时；
 * 3 / 4 点档的黑白屏闪窗口 = 该档 `strobe` 的时长。
 */
"use strict";

/* -------------------------------------------------- 无属性伤害演出（damage-fx 票 04） */

/** 一档演出要读的六个量；键名 = `damage_fx_<档>_<字段>`（见 `anim.ini` 的那 19 行）。
 *
 * 每档四个时长（`total` / `flight` / `impact` / `fade`）**各自独立**，改 4 点档不会牵动
 * 1~3 点档；`strobe` 只有 3 / 4 点有，`split` 只有 4 点有。
 *
 * **字段名名单不在这份文件里维护**（票 10）：真源是 `fx-core.js` 的 `DEFAULT_TIMING`，它导出
 * `SgsDamageFX.TIMING_FIELDS`（由那张表的键生成）。早先这里抄了第二份清单，加字段时漏改一处
 * **不报错**、只是静默不生效——现在只剩一处。
 *
 * **调用时取**：`index.html` 里 `fx-core.js` 排在 `damage-fx.js` **之后**（脚本清单第 24 位
 * vs 第 10 位），文件顶层此时还没有 `SgsDamageFX`；`damageFxTiming` 是播放器运行时调的，那时
 * 两份都在。
 */
function damageFxTimingFields() {
  const api = window.SgsDamageFX;
  return (api && api.TIMING_FIELDS) || [];
}

/** 从生效中的 `anim.ini` 表取**某一档**的时长（毫秒），原样交给 `SgsDamageFX`。
 *
 * **这里不给兜底值、也不钳制**：内置默认（`SgsDamageFX.DEFAULT_TIMING`）与四条钳制
 * （`total ≤ 2200` / `flight ≤ impact ≤ total` / `impact + fade ≤ total`）都住在
 * `fx-core.js` —— 它是时间轴语义的唯一拥有者，前端这一侧只负责"把用户改的数字搬过去"。
 * 缺键 ⇒ 不放进返回的表 ⇒ fx-core 退默认（`anim.ini` 删一行不该让页面报错）。
 *
 * 票 05 的播放器把它交给 `SgsDamageFX.create(canvas, {variant, profile: "game", timing})`；
 * 票 06 的音效读 fx-core 解算后的同一份 `timeline`，两条时钟因此不会漂。
 */
function damageFxTiming(variant) {
  const timing = {};
  if (typeof animTiming !== "function") return timing;
  const id = Math.max(1, Math.min(4, Math.round(Number(variant) || 1)));
  for (const field of damageFxTimingFields()) {
    const key = `damage_fx_${id}_${field}`;
    const value = animTiming(key);
    if (value !== undefined && value !== null && Number.isFinite(Number(value))) {
      timing[key] = Number(value);
    }
  }
  return timing;
}

/* ------------------------------------ 无属性伤害演出（damage-fx 票 05）：排播 + 座位映射 */

/** 同一批里多条伤害的排播口径（spec §演出口径）：相邻开演错开 120ms、同时在播上限 3 条。 */
const DAMAGE_FX_STAGGER_MS = 120;
const DAMAGE_FX_CONCURRENCY = 3;

/** 唯一会播的属性名。 */
const DAMAGE_FX_NATURE = "NORMAL";

/** 这一条伤害该不该演剑光：**只挂无属性**，`FIRE` / `THUNDER` 一律不播（留给将来的 effort）。
 *
 * **`null` / 缺字段 = 无属性**，三层文档同一句话：
 * - `game.py:note_damage`：「`None` = 牌面没声明，**等同无属性**」；
 * - `projection.py:damage_events_after`：「缺声明（拼点类帧）保持 `None`，与"无属性"同义」；
 * - `active.py:_quhu_burn`：「造成 1 点伤害（无渠道牌、无属性）」并显式写 `"nature": None`。
 *
 * 评审修复：早先这里是 `event.nature !== DAMAGE_FX_NATURE` 就丢，`null !== "NORMAL"` 于是把
 * 【驱虎】拼点那 1 点**无属性**伤害一起丢了——那正是本功能最常见的入口之一。
 */
function damageFxNaturePlayable(nature) {
  if (nature === null || nature === undefined) return true;
  return nature === DAMAGE_FX_NATURE;
}

/** 档位封顶：`amount ≥ 5` 按 4 档演——档位只决定强度，数字照旧是服务端给的真实值。 */
const DAMAGE_FX_MAX_VARIANT = 4;

/** 没有来源（`source == null`，判定类伤害）时剑光从画面左侧外这么远处飞进来（CSS px）。 */
const DAMAGE_FX_OFFSTAGE_UNITS = 200;

/** 3 / 4 点档的黑白屏闪（票 05 R2）：canvas 的 `saturation` 合成管不到下层 DOM，正解是
 *  给**表壳**挂 CSS 滤镜（派发者指定做法）。窗口 = 该档 `strobe` 的时长。 */
const DAMAGE_FX_GRAYSCALE = "grayscale(1) contrast(1.08)";

/** 待开演的事件（已到 seq、还没轮到）。**不跨批攒**：换局 / 换批 / 版式变了都清。 */
let damageQueue = [];

/** 正在播的演出：`[{seq, variant, amount, fx, canvas, slot, plan, startedAt, duration, timer}]`。 */
const damagePlays = [];

/** 层里那**一张**可见 canvas（跨演出复用；闲置时摘掉，于是层是空的）。 */
let damageCanvas = null;

/** 舞台离屏画布池：同时在播最多 3 条 ⇒ 最多 3 张，按需建一次、跨演出复用。
 *  **不是**每来一次伤害就新建一张（票 05 §2：一个层里复用 canvas）。 */
const damageStagePool = [];

/** 正在黑白屏闪窗口里的演出（可能多条重叠）；非空 ⇒ 表壳挂 `filter`。 */
const damageGrayPlays = new Set();
let damageGrayOn = false;

let damageStaggerTimer = null;
let damageFrame = null;
let damageNextStartAt = 0;
/** 累计开演次数（只增；写在层的 `data-damage-fx-plays` 上，实机 / 烟测都读得懂）。 */
let damageStartedCount = 0;

/** 覆盖层节点（`index.html` 的 `#t-damage-fx`）；没有它（回放页 / 首屏）就什么都不做。 */
function damageFxLayer() {
  const layer = $("t-damage-fx");
  return layer && layer.classList ? layer : null;
}

/** 演出画布的分辨率上限：牌桌是 1920 设计单位撑满屏，1.5 倍已看不出锯齿。 */
function damageFxDpr() {
  const ratio = Number(window.devicePixelRatio);
  return Number.isFinite(ratio) && ratio > 0 ? Math.min(1.5, ratio) : 1;
}

/** 覆盖层的目标画布尺寸（CSS px）。牌桌档按它建画布、内部 1:1 合成——**不再**
 *  "1920 舞台画完再整块缩小"（票 05 R2：那次 ~3 倍降采样会把 2px 的刀痕抹成灰铅笔线）。 */
function damageFxView(layerRect) {
  return {
    width: Math.max(1, Math.round(Number(layerRect && layerRect.width) || 0)),
    height: Math.max(1, Math.round(Number(layerRect && layerRect.height) || 0)),
  };
}

/** 一张武将块 → **覆盖层坐标**里的矩形（CSS px；量一次 `getBoundingClientRect()`，
 *  不拿 `--dsu` 手算）。牌桌档的坐标系就是覆盖层的 CSS 像素，故这里不做任何比例换算。 */
function damageFxBoxOf(node, layerRect) {
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

/** 中剑方武将块里**体力条**的下沿（相对武将块左上角的 CSS px）：飘字不许压到它。
 *
 * 票 05 R4：判据不走"牌高百分之几"——真机上武将块的头部带（势力印 + 将名 + 内奸徽）本身
 * 就占 25%，体力条在**它下面**（实测下沿 34.7%~42.3% 牌高），百分比判据会落在头部带中间。
 * 自己的体力条是 `index.html` 的常驻节点 `#t-self-hp`（烟测垫片不建静态父子树，只能按 id 取），
 * 对手的武将块里由 `seat-view.js:hpBarNode` 建 `.hp-bar`。
 * 量不到（没有体力条 / 垫片没有布局）⇒ 返回 `null`，交给 fx-core 的兜底比例。
 */
function damageFxNumberKeepOut(block, seat) {
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

/** 座位矩形的可比较读法：版式是否变过（演出还锚得准不准）由它判。 */
function damageFxBoxKey(box) {
  return [box.cx, box.cy, box.w, box.h]
    .map((value) => Math.round(Number(value) || 0))
    .join(",");
}

/** 一条伤害事件 → 开演要用的三样：两块座位矩形（舞台坐标）、档位、真实伤害值。
 *
 * - 中剑方量不到（还没渲染 / 已阵亡）⇒ 返回 `null`：宁可不演，也不要演在半空；
 * - `source == null` ⇒ **不猜来源**：剑光从画面左侧外飞进来（票 05 §4）；
 * - `amount ≥ 5` ⇒ 按 4 档演，飘字仍是真实值（档位只管强度）。
 */
function damageFxPlanFor(event) {
  const layer = damageFxLayer();
  if (!layer) return null;
  const target = event && event.target;
  if (target === null || target === undefined) return null;
  const dstBlock = heroBlockFor(target);
  if (!dstBlock) return null;
  const layerRect = rectOf(layer);
  const dst = damageFxBoxOf(dstBlock, layerRect);
  if (!(dst.w > 0) || !(dst.h > 0)) return null;
  const source = event.source === null || event.source === undefined ? null : Number(event.source);
  let src = null;
  let srcSeat = null;   // **量到了**来源武将块的座位号；null = 无来源 / 量不到（画面外进场）
  if (source !== null && Number.isFinite(source)) {
    const srcBlock = heroBlockFor(source);
    if (srcBlock) {
      const box = damageFxBoxOf(srcBlock, layerRect);
      if (box.w > 0 && box.h > 0) {
        src = box;
        srcSeat = source;
      }
    }
  }
  if (!src) {
    // 画面外进场：整块挪到左边外，牌心与中剑方同高——剑光仍然"从外面飞进来"。
    src = {
      cx: -dst.w / 2 - DAMAGE_FX_OFFSTAGE_UNITS,
      cy: dst.cy,
      w: dst.w,
      h: dst.h,
    };
  }
  const amount = Math.round(Number(event.amount));
  const damage = Number.isFinite(amount) && amount > 0 ? amount : 1;
  const variant = Math.max(1, Math.min(DAMAGE_FX_MAX_VARIANT, damage));
  return {
    src, dst, variant, amount: damage,
    // 版式作废判据要重量的**两张牌**（`damageDropStalePlays` 读它）：`src` 为 null =
    // 无来源 / 画面外进场——那块坐标是按中剑方矩组合成的，与版式无关，故只比中剑方。
    keys: { dst: damageFxBoxKey(dst), src: srcSeat !== null ? damageFxBoxKey(src) : null },
    sourceSeat: srcSeat,
    // 飘字的硬线：中剑方体力条的下沿（相对武将块左上角；量不到是 null）。
    keepOut: damageFxNumberKeepOut(dstBlock, target),
    targetSeat: target,
  };
}

/** 找一个空闲的离屏槽（0..2）。同时在播上限由排播闸门保证，故必有一个空槽。 */
function damageFxSlot() {
  const used = new Set(damagePlays.map((play) => play.slot));
  for (let slot = 0; slot < DAMAGE_FX_CONCURRENCY; slot += 1) {
    if (!used.has(slot)) return slot;
  }
  return 0;
}

/** 覆盖层那张唯一的 canvas：按层的大小定尺寸，**复用同一个节点**（票 05 §2）。 */
function damageFxMountCanvas(layer) {
  if (!damageCanvas) damageCanvas = document.createElement("canvas");
  if (damageCanvas.className !== "damage-fx-canvas") damageCanvas.className = "damage-fx-canvas";
  const rect = rectOf(layer);
  const width = Math.max(1, Math.round(Number(rect.width) || 0));
  const height = Math.max(1, Math.round(Number(rect.height) || 0));
  const dpr = damageFxDpr();
  const deviceW = Math.round(width * dpr);
  const deviceH = Math.round(height * dpr);
  // 改尺寸会清空画布，故只在真的变了的时候写（每帧写一次 = 每帧一次全屏清 + 重排）。
  if (damageCanvas.width !== deviceW || damageCanvas.height !== deviceH) {
    damageCanvas.width = deviceW;
    damageCanvas.height = deviceH;
  }
  if (damageCanvas.parentNode !== layer) layer.appendChild(damageCanvas);
  return damageCanvas;
}

/** 层上的可读状态：`data-damage-fx-live` = 正在播几条、`data-damage-fx-plays` = 累计几次。
 *
 * 画布像素不可断言（也不该断言），故把"演了几条 / 在播几条"写在节点上——烟测与实机
 * 验收都读它，与 `.skill-float` 的 `data-skill-stack` 同一姿态。
 */
function damageFxMark(layer, play = null) {
  if (!layer || !layer.setAttribute) return;
  layer.setAttribute("data-damage-fx-live", String(damagePlays.length));
  layer.setAttribute("data-damage-fx-plays", String(damageStartedCount));
  // 最近一条的飘字读数（全部相对中剑方武将块左上角、CSS px）：实机验收在 DevTools 里
  // 直接读这三个数就能判"有没有压到体力条"，不必自己按百分比换算。
  const metrics = play && play.fx ? play.fx.metrics : null;
  if (!metrics) return;
  layer.setAttribute("data-damage-fx-number", metrics.number.toFixed(1));
  layer.setAttribute("data-damage-fx-number-top", metrics.numberBox.top.toFixed(1));
  layer.setAttribute(
    "data-damage-fx-keep-out",
    metrics.keepOut === null || metrics.keepOut === undefined ? "" : metrics.keepOut.toFixed(1),
  );
  // 剑光航线的**起点 x**（相对覆盖层左沿、CSS px）：`source == null` 时整块在画面外，
  // 故 x < 0 —— "从画面外飞入"因此有了几何读法，不再只有代码路径。
  const stage = play && play.fx ? play.fx.stage : null;
  if (stage && stage.a) layer.setAttribute("data-damage-fx-sword-x", stage.a.x.toFixed(1));
}

/** 表壳的灰阶滤镜：非空集合 ⇒ 挂上（3 / 4 点档的"整屏转黑白"，票 05 R2）。
 *
 * 为什么不在画布上做：canvas 的 `saturation` 合成模式只能改**自己那层**的像素，管不到
 * 下面的牌桌 DOM（在透明层上还会得到一张不透明的灰）。正解是给表壳挂 CSS `filter`——
 * 牌桌与演出一起转黑白（票 05 R2 派发者指定做法）。
 *
 * 已知副作用（可接受，窗口只有 0.16 / 0.44 秒）：`filter` 会建 containing block，
 * 表壳里两个 `position: fixed` 的子节点在窗口内改按表壳定位——`#t-anim-note`（平时
 * `hidden`，本来就在左下角）与 `#t-card-preview`（悬停才出现）。两者的位置差都在几 px
 * 量级，且窗口极短；要彻底避免得给它们搬出表壳，不属本票。
 */
function damageGrayscaleSync() {
  const want = damageGrayPlays.size > 0;
  if (want === damageGrayOn) return;
  const shell = $("table-shell");
  if (shell && shell.style) shell.style.filter = want ? DAMAGE_FX_GRAYSCALE : "";
  damageGrayOn = want;
}

function damageGrayscalePush(play) {
  if (!play || !(play.strobeMs > 0)) return;
  damageGrayPlays.add(play);
  damageGrayscaleSync();
}

function damageGrayscalePop(play) {
  if (!damageGrayPlays.delete(play)) return;
  damageGrayscaleSync();
}

/** 一条伤害开演：取离屏槽 → `SgsDamageFX.create()`（game 档 + 座位矩形 + 真实值 + bare）→ 定时摘层。
 *
 * 摘层时刻用 `create()` 返回的 `duration`（= `anim.ini` 的 `total`）**定时**，不靠 rAF
 * 自然停、也不用 `draw(t × k)` 压时间：音效是另一条时钟（票 06），两边必须读同一份 timeline。
 */
function startDamagePlay(event) {
  const api = window.SgsDamageFX || null;
  if (!api || typeof api.create !== "function") return null;
  const layer = damageFxLayer();
  const plan = damageFxPlanFor(event);
  if (!layer || !plan) return null;
  const slot = damageFxSlot();
  while (damageStagePool.length <= slot) damageStagePool.push(document.createElement("canvas"));
  const stageCanvas = damageStagePool[slot];
  if (!stageCanvas || typeof stageCanvas.getContext !== "function") return null;
  damageFxMountCanvas(layer);
  const view = damageFxView(rectOf(layer));
  const fx = api.create(stageCanvas, {
    variant: plan.variant,
    profile: "game",
    timing: damageFxTiming(plan.variant),
    amount: plan.amount,
    stage: { src: plan.src, dst: plan.dst },
    // 飘字不许压到中剑方的体力条：硬线 = `.hp-bar` 的下沿（票 05 R4，DOM 真值）。
    numberKeepOut: plan.keepOut,
    // 画布按覆盖层的实际尺寸建、内部 1:1 合成（票 05 R2：不再整块缩小）。
    view,
    dpr: damageFxDpr(),
    // 牌桌档**只出特效**：宣纸底 + 样例武将牌一概不画（否则覆盖层不透明、牌桌被盖住，
    // 而且会在真实武将块上叠出参考实现的关羽 / 吕布）。素材库那条链（缺省）不受影响。
    bare: true,
  });
  const timeline = fx.timeline || {};
  const timing = fx.timing || {};
  const impactMs = Math.max(0, Number(timeline.IMPACT) || 0) * 1000;
  const strobeMs = Math.max(0, Number(timing.strobe) || 0);
  const play = {
    seq: Number(event.seq),
    variant: plan.variant,
    amount: plan.amount,
    plan,
    fx,
    canvas: stageCanvas,
    slot,
    startedAt: stageNow(),
    duration: Math.max(0, Number(fx.duration) || 0) * 1000,
    strobeMs,
    timer: null,
    grayOnTimer: null,
    grayOffTimer: null,
    // 票 06：这条演出占用的音效声道与它那一段的档位参数（`damageSfxStart` 里填）。
    sfxLane: null,
    sfxVoice: null,
  };
  damagePlays.push(play);
  damageStartedCount += 1;
  // 票 06：这一档的声道先起（紧接着下面就要画第一帧、并喂同一个 `seconds`）。开关是关的 /
  // 没解锁 / 没有 `AudioContext` 时 `damageSfxStart` 返回 null，画面照旧。
  damageSfxStart(play);
  play.timer = window.setTimeout(() => finishDamagePlay(play), play.duration);
  damageFxMark(layer, play);
  if (strobeMs > 0) {
    // 黑白屏闪的窗口：命中起、`strobe` 后收。用定时器而不是逐帧判：垫片 / 掉帧下也准。
    play.grayOnTimer = window.setTimeout(() => {
      play.grayOnTimer = null;
      damageGrayscalePush(play);
    }, impactMs);
    play.grayOffTimer = window.setTimeout(() => {
      play.grayOffTimer = null;
      damageGrayscalePop(play);
    }, impactMs + strobeMs);
  }
  damageFxMark(layer);
  renderDamageFrame(stageNow());
  damageRequestFrame();
  return play;
}

/** 一段演出到点：从在播表里摘掉，最后一帧合成完之后清画布、摘 canvas（不留残影）。 */
function finishDamagePlay(play) {
  const at = damagePlays.indexOf(play);
  if (at >= 0) damagePlays.splice(at, 1);
  // 票 06：先收声（这一段的声道静音并清 cue 游标），再撤画面。
  damageSfxStop(play);
  if (play && play.timer !== null && play.timer !== undefined) {
    if (window.clearTimeout) window.clearTimeout(play.timer);
    play.timer = null;
  }
  // 收尾必须把屏闪窗口一起撤掉：否则中途作废的演出会把整块牌桌留在黑白里。
  for (const key of ["grayOnTimer", "grayOffTimer"]) {
    if (play && play[key] !== null && play[key] !== undefined && window.clearTimeout) {
      window.clearTimeout(play[key]);
    }
    if (play) play[key] = null;
  }
  damageGrayscalePop(play);
  if (!damagePlays.length) {
    damageStopFrame();
    damageClearCanvas();
  }
  damageFxMark(damageFxLayer());
}

/** 清可见画布并把它摘下来：层因此回到"空"（下一段演出重新挂回去，节点仍复用同一个）。 */
function damageClearCanvas() {
  const canvas = damageCanvas;
  if (canvas && typeof canvas.getContext === "function") {
    const ctx = canvas.getContext("2d");
    if (ctx && ctx.clearRect) {
      if (ctx.setTransform) ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
    }
  }
  if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
}

/** 一帧：每条在播的演出画进自己的离屏舞台，再合成到层里**唯一**那张 canvas 上。
 *
 * 合成而不是共用一张可见画布，是因为 `fx.draw(t)` 每帧整屏清一次——3 条同时在播时
 * 只能各画各的，最后一起贴上来（票 05 §4 的"同时在播上限 3 条"）。
 */
function renderDamageFrame(now = stageNow()) {
  if (!damagePlays.length) return;
  const layer = damageFxLayer();
  const canvas = damageCanvas;
  if (!layer || !canvas) return;
  for (const play of damagePlays) {
    // 演出自己的时钟（票 06）：画面与声音**同吃这一个 `seconds`**——不另起墙钟，
    // 于是快进 / 掉帧 / 暂停时两边一起走，不会漂。
    const seconds = Math.min(
      Math.max(0, Number(now) - Number(play.startedAt)) / 1000,
      play.duration / 1000,
    );
    if (play.fx && typeof play.fx.draw === "function") play.fx.draw(seconds);
    damageSfxTick(play, seconds);
  }
  const ctx = typeof canvas.getContext === "function" ? canvas.getContext("2d") : null;
  if (!ctx) return;
  const view = damageFxView(rectOf(layer));
  const dpr = damageFxDpr();
  if (ctx.setTransform) ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, view.width, view.height);
  for (const play of damagePlays) {
    if (!play.canvas) continue;
    // 1:1（离屏舞台与可见画布同尺寸、同 dpr）：不再有整块降采样。
    ctx.drawImage(play.canvas, 0, 0, view.width, view.height);
  }
}

function damageRequestFrame() {
  if (damageFrame !== null) return;
  const raf = window.requestAnimationFrame;
  if (typeof raf !== "function") return;
  damageFrame = raf(() => {
    damageFrame = null;
    renderDamageFrame();
    if (damagePlays.length) damageRequestFrame();
  });
}

function damageStopFrame() {
  if (damageFrame === null) return;
  if (window.cancelAnimationFrame) window.cancelAnimationFrame(damageFrame);
  damageFrame = null;
}

/** 排播闸门：已到 seq 的事件逐条开演，相邻错开 120ms，同时在播不超过 3 条。
 *
 * 超过上限的**丢队列尾**（spec §演出口径）：伤害本身早已由服务端结算（体力条 / 事件流
 * 都到位了），攒着等下一批只会把旧伤害飘在新画面上。
 */
function pumpDamageQueue() {
  if (damageStaggerTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(damageStaggerTimer);
    damageStaggerTimer = null;
  }
  while (damageQueue.length) {
    if (damagePlays.length >= DAMAGE_FX_CONCURRENCY) {
      damageQueue = [];
      break;
    }
    const wait = Math.round(damageNextStartAt - stageNow());
    if (wait > 0) {
      damageStaggerTimer = window.setTimeout(() => {
        damageStaggerTimer = null;
        // 到点了：把闸门推到"现在"再重排——不重算等待，否则计时器早到 1~2ms 时会再排
        // 一次 120ms（同一条事件永远开不了演）。
        damageNextStartAt = stageNow();
        pumpDamageQueue();
      }, wait);
      break;
    }
    startDamagePlay(damageQueue.shift());
    damageNextStartAt = stageNow() + DAMAGE_FX_STAGGER_MS;
  }
}

/** 把 `seq` 及之前的伤害事件排上（与 :func:`fireSkillEventsThrough` 同构）。
 *
 * `state.damageEvents` 是随视图下发的**待触发**队列：回放走到某条记录的 `seq` 才演出，
 * 于是剑光在"这一下伤害"发生的那一步出现，而不是响应一到就全部同时出现。
 * **触发点只有 `seq` 游标**（`stepPlayback` 里与技能事件相邻的那一行）——不在 `applyLiveDelta`
 * 里、也不在提交视图时触发。
 */
function fireDamageEventsThrough(seq = Infinity) {
  const events = state.damageEvents || [];
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
  state.damageEvents = kept;
  let queued = 0;
  for (const event of due) {
    // `null` / 缺字段 = 无属性（视同 NORMAL）；只有 FIRE / THUNDER 显式跳过。
    if (!event || !damageFxNaturePlayable(event.nature)) continue;
    damageQueue.push(event);
    queued += 1;
  }
  if (queued) pumpDamageQueue();
}

/** 版式变了就丢掉正在播的演出：座位块重建后矩形不同 ⇒ 剑光的两个端点已经不对了。
 *
 * 票 05 §5 的原话是"renderTable 重建即摘层"，但回放**每一步**都会重绘全表
 * （`stepPlayback` → `renderTable`），一刀切会让 1.5~2.1s 的演出活不过 100ms。
 * 这里按**几何**判据：中剑方武将块的矩形（四舍五入到 CSS 像素）没变就继续演。
 *
 * **由 `table.js:renderTable` 在重绘末尾显式调用**（票 09）：原来住在
 * `effects.js:syncTransientEffects` 里，那是"动效层 → 伤害层"的反向钩子。
 */
function damageDropStalePlays() {
  if (!damagePlays.length) return;
  const layer = damageFxLayer();
  if (!layer) {
    clearDamageFx();
    return;
  }
  const layerRect = rectOf(layer);
  const stale = damagePlays.filter((play) => {
    const plan = play.plan;
    if (!plan || !plan.keys) return true;
    const dstBlock = heroBlockFor(plan.targetSeat);
    if (!dstBlock) return true;
    if (damageFxBoxKey(damageFxBoxOf(dstBlock, layerRect)) !== plan.keys.dst) return true;
    // 评审修复：**来源**座位挪了同样是版式作废——剑光是从出剑方那张牌起飞的，
    // 只比中剑方会让它从旧坐标出发。无来源（画面外进场）没有源矩形，只比中剑方。
    if (plan.keys.src === null) return false;
    const srcBlock = heroBlockFor(plan.sourceSeat);
    if (!srcBlock) return true;
    return damageFxBoxKey(damageFxBoxOf(srcBlock, layerRect)) !== plan.keys.src;
  });
  for (const play of stale) finishDamagePlay(play);
}

/** 换局 / 回放重置 / 版式作废：清队列 + 摘层（照 `effects.js:clearTransientEffects` 的收尾姿态）。
 *
 * **调用方**（票 09 起）：`session.js:commitView` 在换会话时紧挨着
 * `clearTransientEffects()` 调它，并顺手清 `state.damageEvents`；`damageDropStalePlays()`
 * 发现覆盖层不在了也走这里（兜底）。动效层不再认识本函数。
 */
function clearDamageFx() {
  if (damageStaggerTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(damageStaggerTimer);
    damageStaggerTimer = null;
  }
  // 票 06：在播的演出连声音一起收掉——上一局那一剑不该在新桌上响完。
  damageSfxStopAll();
  damageQueue = [];
  for (const play of damagePlays.splice(0, damagePlays.length)) {
    if (play.timer !== null && play.timer !== undefined && window.clearTimeout) {
      window.clearTimeout(play.timer);
    }
    play.timer = null;
  }
  damageStopFrame();
  damageClearCanvas();
  damageNextStartAt = 0;
  // 换局 / 版式作废：屏闪窗口一并撤掉（牌桌不能留在黑白里）。
  damageGrayPlays.clear();
  damageGrayscaleSync();
  damageFxMark(damageFxLayer());
}
