"use strict";
/* ============================================================================
   skill-ink-fx.js —— 技能发动的水墨艺术字：排播 / canvas 生命周期 / 清理

   演出本体在 `fx-skill-ink.js`（素材包原件，只加了一行 `"use strict";`），音效在
   `sfx-skill-ink.js`。本文件只做三件事：**什么时候放**、**画在哪**、**什么时候摘**。

   口径（ink-table-skin 票 03，用户 2026-11-13 裁定）：
     - 整段 **1.8s**。素材内部时间表是 2.05s，按契约由本文件把秒数乘 `2050/1800`
       再交给 `draw()`——**不改模块内部常数**。
     - 不透明度从真实 **0.53s**（= 朱印落完）起线性 1 → 0.55，止于 **1.28s**
       （= 素材内部 `fadeAt 1.46` 的同比位置），其后由模块自己的化墨收尾。
     - **位置一动不动**：canvas 只量一次武将块矩形，之后不动位移也不动缩放——不继承
       金色飘字那套"2s 上浮 20 设计单位"。
     - 四类载体（`HERO` / `EQUIPMENT` / `MARKER` / `CARD_TAG`）一律用墨，不做区分。
     - **不登记 `registerPlaybackHold`**：技能是高频事件，冻住记录流会让牌桌干等 1.8s。
   ============================================================================ */

/** 同时在播上限：第 4 条直接丢（不排队——技能名是"看一眼"的信息，排 4 条会把牌桌压住）。 */
const SKILL_INK_MAX = 3;

/** 整段时长 / 喂给素材的秒数缩放（素材 2.05s → 现网 1.8s，同比压缩四拍）。 */
const SKILL_INK_TOTAL_MS = 1800;
const SKILL_INK_TIME_SCALE = 2050 / 1800;

/** 外部不透明度：`at` 起线性降到 `end`，`to` 之后保持（剩下的交给模块的化墨）。 */
const SKILL_INK_FADE_AT = 0.53;
const SKILL_INK_FADE_TO = 1.28;
const SKILL_INK_FADE_END = 0.55;

/** 在播的场：`{seat, canvas, fx, startedAt, box}`。 */
let skillInkPlays = [];

/** 技能墨字层：与其它演出层同档（z19），DOM 排在 `#t-death-fx` 之后 ⇒ 画在最上面。 */
function skillInkLayer() {
  return $("t-skill-ink");
}

/** 某一刻该给 canvas 的不透明度（秒；真实秒，不是素材秒）。 */
function skillInkOpacityAt(seconds) {
  if (seconds <= SKILL_INK_FADE_AT) return 1;
  if (seconds >= SKILL_INK_FADE_TO) return SKILL_INK_FADE_END;
  const k = (seconds - SKILL_INK_FADE_AT) / (SKILL_INK_FADE_TO - SKILL_INK_FADE_AT);
  return 1 + (SKILL_INK_FADE_END - 1) * k;
}

/** 摘掉一场：停帧、摘节点、从队列里除名，并把层的可断言读数刷新。 */
function skillInkDrop(play) {
  if (!play) return;
  if (play.raf) window.cancelAnimationFrame(play.raf);
  play.raf = 0;
  if (play.canvas && play.canvas.parentNode) play.canvas.parentNode.removeChild(play.canvas);
  skillInkPlays = skillInkPlays.filter((item) => item !== play);
  const layer = skillInkLayer();
  if (!layer) return;
  layer.setAttribute("data-skill-ink", String(skillInkPlays.length));
  if (skillInkPlays.length) layer.setAttribute("data-skill-ink-seat", String(skillInkPlays[0].seat));
  else layer.removeAttribute("data-skill-ink-seat");
}

/** 一帧：按真实秒推进，摘层由自己算（不靠计时器——rAF 被节流时也要能正确收尾）。 */
function skillInkFrame(play, now) {
  if (!skillInkPlays.includes(play)) return;
  const seconds = (now - play.startedAt) / 1000;
  if (seconds >= SKILL_INK_TOTAL_MS / 1000) {
    skillInkDrop(play);
    return;
  }
  if (!play.fx) {
    /* 测试缝（没有烘焙）：照样走时钟与摘层，只是不画。 */
    play.canvas.style.opacity = skillInkOpacityAt(seconds).toFixed(3);
    play.raf = window.requestAnimationFrame((next) => skillInkFrame(play, next));
    return;
  }
  try {
    play.fx.draw(seconds * SKILL_INK_TIME_SCALE);
  } catch (error) {
    /* 装饰层不许把牌桌带走：画崩了就当场摘掉，下一次发动照常。 */
    skillInkDrop(play);
    return;
  }
  play.canvas.style.opacity = skillInkOpacityAt(seconds).toFixed(3);
  play.raf = window.requestAnimationFrame((next) => skillInkFrame(play, next));
}

/**
 * 一条 `skill_event` 的墨字演出（`effects.js:playSkillEvent` 末尾调）。
 *
 * 只认 `seat` / `name` 两件事：载荷里没有载体字段，而裁定是**四类载体一律用墨**，
 * 所以这里不判载体、不查表。`typeof` 守卫是为了"只加载了 effects.js 的探针"不炸。
 */
const skillInkDiag = { calls: 0, noModule: 0, badSeat: 0, badName: 0, noLayer: 0, noBlock: 0, noRect: 0, created: 0 };
/* 排播自检读数（烟测 / 实机诊断；不参与演出）。挂在 window 上，而非常量位置——
 * 顶层 `window.x = const` 会踩 TDZ。 */
window.skillInkDiag = skillInkDiag;

function skillInkFxPlay(event) {
  skillInkDiag.calls += 1;
  /* 测试缝：烟测垫片跑的是**没有像素**的假 2D 上下文，而这版烘焙在 Node 的 vm 里比
   * Chromium 慢约 35 倍（实测 135ms → 4.5s；套件一次要烘一百多次，会把 70 秒的烟测拖成
   * 12 分钟）。垫片只断言 DOM / 生命周期、不断言像素，故用 `window.__sgsNoInkBake`
   * 跳过"烘 + 画"，但**照建 canvas、照记属性、照走摘层**——被断言的那部分一个字不差。 */
  const skipPaint = Boolean(window.__sgsNoInkBake);
  if (!event) return;
  /* 素材的 IIFE 是 `(function (global) {...})(window)`，所以它挂在 **`window`** 上，
   * 不是裸全局——烟测垫片里 `window` 是个独立对象，裸名字取不到。 */
  const fxApi = window.SgsSkillInkFX;
  if (!fxApi || typeof fxApi.create !== "function") { skillInkDiag.noModule += 1; return; }
  const rawSeat = event.seat;
  if (rawSeat === null || rawSeat === undefined) { skillInkDiag.badSeat += 1; return; }
  const seat = Number(rawSeat);
  if (!Number.isFinite(seat)) return;
  const name = String(event.name || "").trim();
  if (!name) { skillInkDiag.badName += 1; return; }
  const layer = skillInkLayer();
  const block = heroBlockFor(seat);
  if (!layer) { skillInkDiag.noLayer += 1; return; }
  if (!block) { skillInkDiag.noBlock += 1; return; }

  /* 同一武将块的新发动：先摘旧的（不叠两张墨）。 */
  for (const old of skillInkPlays.slice()) {
    if (old.seat === seat) skillInkDrop(old);
  }
  while (skillInkPlays.length >= SKILL_INK_MAX) skillInkDrop(skillInkPlays[0]);

  const layerRect = rectOf(layer);
  const box = rectOf(block);
  if (!box || !layerRect || !box.width || !box.height) { skillInkDiag.noRect += 1; return; }
  skillInkDiag.created += 1;

  const canvas = document.createElement("canvas");
  canvas.className = "skill-ink-canvas";
  canvas.style.left = `${box.left - layerRect.left}px`;
  canvas.style.top = `${box.top - layerRect.top}px`;
  canvas.style.width = `${box.width}px`;
  canvas.style.height = `${box.height}px`;
  /* 两枚可断言读数（烟测 / 探针）：画布本身没有文字，名字与座位只能挂在属性上。
   * `data-skill-ink-seat` 是"这一张写的是谁的牌"，`data-skill-ink-name` 是写上去的字。 */
  canvas.setAttribute("data-skill-ink-seat", String(seat));
  canvas.setAttribute("data-skill-ink-name", name);
  layer.appendChild(canvas);

  let fx = null;
  try {
    fx = skipPaint ? null : fxApi.create(canvas, {
      name,
      w: box.width,
      h: box.height,
      dpr: Math.min(2, window.devicePixelRatio || 1),
      seal: "技",
    });
  } catch (error) {
    /* 装饰层不许把牌桌带走，但也**不许悄悄失败**：把原因挂到层上，烟测 / 实机一眼能看见。 */
    if (canvas.parentNode) canvas.parentNode.removeChild(canvas);
    layer.setAttribute("data-skill-ink-error", String((error && error.message) || error));
    return;
  }

  const play = { seat, canvas, fx, startedAt: stageNow(), box, raf: 0 };
  skillInkPlays.push(play);
  layer.setAttribute("data-skill-ink", String(skillInkPlays.length));
  layer.setAttribute("data-skill-ink-seat", String(seat));

  if (typeof skillInkSfxPlay === "function") skillInkSfxPlay(name);

  if (fx) {
    try {
      fx.draw(0);
    } catch (error) {
      skillInkDrop(play);
      return;
    }
  }
  canvas.style.opacity = "1";
  play.raf = window.requestAnimationFrame((now) => skillInkFrame(play, now));
}

/**
 * 每次 `renderTable` 之后：版式变了就作废（照 `chainFxSync()` / `deathFxSync()` 的先例）。
 *
 * `renderTable` 会重建座位节点，而层里的 canvas 不跟着重建；牌挪了 / 换座 / 阵亡重建
 * ⇒ 这一场直接摘掉，**不追着牌跑**（墨写的是"那一瞬间在那张牌上"）。
 */
function skillInkFxSync() {
  if (!skillInkPlays.length) return;
  for (const play of skillInkPlays.slice()) {
    const block = heroBlockFor(play.seat);
    if (!block) {
      skillInkDrop(play);
      continue;
    }
    const now = rectOf(block);
    const old = play.box;
    if (
      !now
      || Math.abs(now.left - old.left) > 0.5
      || Math.abs(now.top - old.top) > 0.5
      || Math.abs(now.width - old.width) > 0.5
      || Math.abs(now.height - old.height) > 0.5
    ) {
      skillInkDrop(play);
    }
  }
}

/** 换局收场（`session.js:commitView` 紧挨 `clearDamageFx()` 调）：层里一张 canvas 都不留。 */
function skillInkFxClear() {
  for (const play of skillInkPlays.slice()) skillInkDrop(play);
  skillInkPlays = [];
  const layer = skillInkLayer();
  if (layer) {
    layer.removeAttribute("data-skill-ink");
    layer.removeAttribute("data-skill-ink-seat");
  }
}
