/* SGS 前端 · 「铁索连环」的**画面 + 排播**（chain-death-voices 票 01）：入链 / 解锁两条
 * 演进 + 常态铁索贴图。画面部分是素材包 `outputs/sgs-chain-fx/fx-chain.js` 的移植
 * （纯 Canvas 2D、零依赖、零请求、画面只由秒数决定），本文件在它之上加了三件事：
 *
 *   1. **双时间轴**（同 `fx-core.js` / `fx-thunder.js` / `fx-fire.js` 的姿态）：素材页的
 *      `MODES[mode].duration`（1.36s / 1.24s）只作**基准**；牌桌档读 `anim.ini` 的
 *      `chain_fx_<mode>_total`（缺键退 `DEFAULT_TIMING`，钳制 800–2400ms）。`draw(t)` 仍吃
 *      **演出自己的秒数**，内部按 `total / 基准` 映射回素材秒——于是末帧精确落在素材页的
 *      末帧上（`bakeStatic` 烘的就是那一帧，"演完才挂常态"的两张图逐像素一致）。
 *   2. **排播**（与 `damage-fx.js` 同姿态）：`fireChainEventsThrough(record)` 由
 *      `session.js:stepPlayback` 在 `fireDamageEventsThrough(record.seq)` 之后调；
 *      `state.chainSeq` 游标防重播（首批只切常态、不演）；相邻错开 120ms、同座位 200ms
 *      内先收旧的、在播上限 8；`chainFxSync()` 每次 `renderTable` 之后重贴常态贴图 / 抑制态
 *      / 比较武将块矩形；`clearChainFx()` 换局清场。
 *   3. **常态贴图**：`bakeStatic()` 的结果按尺寸缓存，由 `chainFxSync()` 画进 `.chain-mark`
 *      画布（不是 CSS 背景图：烟测垫片没有 `toDataURL`）。
 *
 * **与素材包建议不同的三处（用户口径，逐条记在这里）**：
 *
 *   1. **入链：演完才挂常态**。素材包 §3 的示例是"先挂上、再演"（末帧正好接住）；用户要求
 *      "动画结束后才进入横置表现，不然常态铁索 UI 会挡住动画表现" ⇒ 演出期间座位带
 *      `data-chain-fx="link"`（CSS 把 `.chain-mark` 藏掉，本文件也**不画**那张贴图），
 *      收尾时才让常态出现。
 *   2. **解锁：立刻摘常态、再演断裂**。`chained=false` 的重绘天然不挂 `.chain-mark`，
 *      随后 `break` 演在牌上——本文件不提前补画任何东西。
 *   3. **无时停**：不登记 `registerPlaybackHold`（素材包没要求 hold，但这条口径要写死）。
 *      铁索的演出与后续记录**并行**是预期，不是漏接（spec §已知缺口 4）。
 *
 * 依赖方向单向：只借 `effects.js` 的 `rectOf` / `heroBlockFor` / `descendantWithClass`、
 * `table.js` 的 `tableViewForRender`、`session.js` 的 `stageNow` / `state`，以及音效那侧的
 * `chainSfxPlay` / `chainSfxStop`（`sfx-ui.js`，单向缝）——全部在**调用时**解析。
 */
"use strict";

(function (global) {

  var TAU = Math.PI * 2;

  /* ================================================================
   * 双时间轴（同 `fx-core.js` / `fx-thunder.js` / `fx-fire.js`）
   *
   * `DEFAULT_TIMING` 是字段名与内置默认的**唯一真源**：`TIMING_FIELDS` / `TIMING_KEYS`
   * 都由它生成，磁盘上的 `anim.ini` 与它逐键同值（静态用例钉）。数组冻结——读者不该
   * 就地改别人的时间表。
   * ============================================================== */
  var TIMING_PREFIX = "chain_fx_";
  var MODE_NAMES = ["link", "break"];
  var DEFAULT_TIMING = {
    link: { total: 1360 },
    break: { total: 1240 }
  };
  /* 钳制范围（spec §演出口径·牌桌档缺省）：比 0.8s 短看不清"入链"，比 2.4s 长会挡住牌桌
   * ——铁索**没有时停**，回放照常推进，所以它必须自己管住自己的窗口。 */
  var MIN_TOTAL_MS = 800;
  var MAX_TOTAL_MS = 2400;

  function timingKeyOf(mode, field) {
    return TIMING_PREFIX + mode + "_" + field;
  }

  var TIMING_FIELDS = (function () {
    var fields = [];
    for (var m = 0; m < MODE_NAMES.length; m++) {
      var table = DEFAULT_TIMING[MODE_NAMES[m]];
      for (var field in table) {
        if (!Object.prototype.hasOwnProperty.call(table, field)) continue;
        if (fields.indexOf(field) < 0) fields.push(field);
      }
    }
    return Object.freeze(fields);
  })();

  var TIMING_KEYS = (function () {
    var keys = [];
    for (var m = 0; m < MODE_NAMES.length; m++) {
      var fields = DEFAULT_TIMING[MODE_NAMES[m]];
      for (var field in fields) {
        if (!Object.prototype.hasOwnProperty.call(fields, field)) continue;
        keys.push(timingKeyOf(MODE_NAMES[m], field));
      }
    }
    return Object.freeze(keys);
  })();

  /* 从 `raw`（= 排播层搬来的 anim.ini 键值）取一个毫秒数：不是有限非负数就退回内置默认。 */
  function millisOf(raw, key, fallback) {
    if (raw && Object.prototype.hasOwnProperty.call(raw, key)) {
      var value = Number(raw[key]);
      if (isFinite(value) && value >= 0) return value;
    }
    return fallback;
  }

  /* `game` 档：把 `anim.ini` 的 `chain_fx_<mode>_total` 解成**牌桌秒** + 映射倍率。
   *
   * `speed = 素材页基准秒数 / 牌桌秒数`：`draw(t)` 每帧读 `t * speed` 当素材秒，于是整条
   * 演出（含末帧）等比压 / 拉开。钳制只在这里做一次——画面模块是时间轴语义的拥有者，
   * 排播层只负责把用户改的数字搬过来。 */
  function gameTiming(mode, raw) {
    var name = mode === "break" ? "break" : mode === "hold" ? "hold" : "link";
    var defaults = DEFAULT_TIMING[name] || DEFAULT_TIMING.link;
    var basis = MODES[name] ? MODES[name].duration : MODES.link.duration;
    var total = clamp(
      millisOf(raw, timingKeyOf(name, "total"), defaults.total), MIN_TOTAL_MS, MAX_TOTAL_MS
    );
    var seconds = total / 1000;
    var timing = {};
    timing[timingKeyOf(name, "total")] = total;
    return {
      mode: name,
      total: total,
      seconds: seconds,
      basis: basis,
      speed: basis / seconds,
      timing: timing
    };
  }

  /* ---------------------------------------------------------------- 配色 ---- */
  var C = {
    paper:  "#f1ebdf",
    card:   "#fffdf7",
    cardLo: "#f8f1e2",
    line:   "#ddd8d0",
    ink:    "#1c1a17",
    dim:    "#6f6a62",
    red:    "#9c2b1b",
    shu:    "#9c2b1b",
    wet:    "#1a1815",
    dried:  "#6b6154",
    /* 铁索：焦墨的核 + 一笔带到的铁灰边（不是金属高光，只是墨的浓淡） */
    link0:  "#0a0908",
    link1:  "#1d1a16",
    link2:  "#3b352c"
  };

  var SERIF = '"Noto Serif SC","Songti SC","STSong","SimSun",serif';
  var SANS  = '"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif';

  var W = 1920, H = 1080;
  var CARD = { w: 460, h: 644, pad: 74 };
  var SCALE = 1.06;
  var DST = { cx: 900, cy: 466, w: CARD.w * SCALE, h: CARD.h * SCALE };

  var GRAV = 1500;          /* 墨点下坠（px/s²），比"失去体力"慢：墨比水重 */

  /* 样例牌：庞统（连环 / 涅槃）—— 入链这件事本来就是他干的 */
  var HERO = {
    name: "庞统", faction: "蜀", factionColor: C.shu, hp: 3,
    skills: [
      ["连环", "出牌阶段限一次，你可以将一张梅花手牌当【铁索连环】使用或重铸。"],
      ["涅槃", "限定技，当你处于濒死状态时，你可以弃置你区域里的所有牌，复原武将牌，摸三张牌，将体力回复至3点。"]
    ]
  };

  /* ================================================================
   * 几何：全部按牌宽取比例，接到牌桌上（武将块只有 130px 宽）也成立
   *
   *   entry  左边缘进入的高度（牌高比例）—— 中下
   *   deg    向上倾的角度 —— "稍微向上斜一点"
   *   overL / overR  两端各探出牌外多少（牌宽比例）
   *   wire    环身（铁条）粗细
   *   faceLen 胶囊环的**长**（沿链方向），faceWid 环的**宽**（横链方向）
   *   pitch   相邻环心的间距（≈ 半个环长：一环咬一环）
   *
   * 整条链**只有胶囊环**：隔一环压在上面，压在上面的那圈先擦出一道纸色的缝，
   * 被压住的那一环就"断"在那里 —— 环环相扣是这么读出来的，不是靠另一圈垂直的环。
   * （GEOM.sideOn = true 会把"两个方向互相垂直"的两圈环那套找回来，留作备选。）
   * ============================================================== */
  var GEOM = {
    entry: 0.655,
    deg: 13.5,
    overL: 0.105,
    overR: 0.092,
    wire: 0.0200,      /* 铁条粗细 */
    faceLen: 0.1500,   /* 胶囊环：长（沿链） */
    faceWid: 0.0800,   /* 胶囊环：宽（横链） */
    sideLen: 0.0180,   /* 侧身环（默认关掉，见 sideOn） */
    sideWid: 0.0580,   /* 侧身环（默认关掉，见 sideOn） */
    sideSlot: 0.30,
    pitch: 0.0800,     /* 相邻环心距 ≈ 半个环长：一环咬一环 */
    sideOn: false,     /* false = 整条链只有胶囊环，没有垂直穿出来的圆环 */
    wob: 0.085
  };

  /* ================================================================
   * 三种模式的时间表（音效从同一张表读，不另抄一份）
   * ============================================================== */
  var MODES = {
    link: {
      id: "link", name: "入链", seal: "入", verb: "横置",
      tag: "铁索自牌左沿中下斜上滑入，两端探出牌外，末帧即常态",
      duration: 1.36,
      ghostAt: 0.00, ghostDur: 0.30,   /* 干笔引路 */
      slideAt: 0.09, slideDur: 0.90,   /* 铁索滑入 */
      settleAt: 1.00,                  /* 落定那一下 */
      rubAt: 0.94,                     /* 牌沿压痕 */
      bleedAt: 1.00, bleedDur: 0.36,   /* 墨定 */
      flecks: 6
    },
    break: {
      id: "break", name: "解锁", verb: "重置",
      tag: "铁索绷紧、崩断，两断弹开、化墨散尽",
      duration: 1.24,
      tensionAt: 0.00, tensionDur: 0.155,
      snapAt: 0.155,
      sepDur: 0.44,
      dissolveAt: 0.24, dissolveDur: 0.90,
      goneAt: 1.16,
      shards: 9, dots: 12, mists: 7
    },
    hold: {
      id: "hold", name: "常态", verb: "横置",
      tag: "常态：入链末帧的静止铁索（bakeStatic 就是这一帧）",
      duration: 1.20
    }
  };

  /* ================================================================
   * 常态的一组定值 —— 入链的**末帧**、hold 模式、bakeStatic 用的是同一组，
   * 改一个数三处一起变（"截取最后一帧当常态"因此是恒等式，不是临摹）。
   * ============================================================== */
  var REST = {
    crisp: 0.99,   /* 链本体 */
    wet: 0.15,     /* 洇开的那圈湿墨 */
    rub: 0.85,     /* 牌沿压痕 */
    ghost: 0.135,  /* 干笔引路的残痕 */
    sag: 1.15      /* 落定后往下沉的那一点 */
  };

  /* ------------------------------------------------------------ 小工具 ---- */
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function ss(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function eOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }

  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function rgba(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16 & 255) + "," + (n >> 8 & 255) + "," + (n & 255) + "," + a + ")";
  }

  function offscreen(w, h) {
    var c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return [c, c.getContext("2d")];
  }

  function pathRound(c, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  function wrapText(c, text, x, y, maxW, lineH, maxLines) {
    var line = "", out = [];
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (c.measureText(line + ch).width > maxW && line) {
        out.push(line);
        line = ch;
        if (out.length >= maxLines) break;
      } else {
        line += ch;
      }
    }
    if (line && out.length < maxLines) out.push(line);
    for (var j = 0; j < out.length && j < maxLines; j++) c.fillText(out[j], x, y + j * lineH);
  }

  /* ================================================================
   * 宣纸（一次烘焙）—— 和火焰 / 雷电 / 回复 / 失去体力同一张纸
   * ============================================================== */
  function paintPaper(vw, vh, S) {
    var w = vw + 160, h = vh + 160;
    var pc = offscreen(w * S, h * S), c = pc[1];
    c.scale(S, S);
    var r = rng(20261111);

    c.fillStyle = C.paper;
    c.fillRect(0, 0, w, h);

    for (var i = 0; i < (w * h) / 30; i++) {
      c.fillStyle = r() < 0.5 ? "rgba(28,26,23,0.013)" : "rgba(255,255,255,0.05)";
      c.fillRect(r() * w, r() * h, 1, 1);
    }
    c.strokeStyle = "rgba(90,70,40,0.022)";
    c.lineWidth = 0.7;
    for (var j = 0; j < (w * h) / 1250; j++) {
      var px = r() * w, py = r() * h, a = r() * TAU, l = 5 + r() * 22;
      c.beginPath();
      c.moveTo(px, py);
      c.lineTo(px + Math.cos(a) * l, py + Math.sin(a) * l);
      c.stroke();
    }

    c.save();
    c.globalCompositeOperation = "multiply";
    var cx = w / 2 + 6, cy = h / 2 + 22;
    for (var k = 0; k < 48; k++) {
      if (r() < 0.18) continue;
      var a0 = k / 48 * TAU, a1 = (k + 1) / 48 * TAU;
      var rr = Math.min(w, h) * 0.42 * (0.96 + r() * 0.05);
      c.strokeStyle = "rgba(28,26,23," + (0.007 + r() * 0.008) + ")";
      c.lineWidth = 8 + r() * 15;
      c.beginPath();
      c.moveTo(cx + Math.cos(a0) * rr, cy + Math.sin(a0) * rr);
      c.lineTo(cx + Math.cos(a1) * rr * 1.002, cy + Math.sin(a1) * rr * 1.002);
      c.stroke();
    }
    var ink = [
      [w * 0.16, h * 0.74, 240, 0.013], [w * 0.84, h * 0.30, 260, 0.011],
      [w * 0.70, h * 0.86, 300, 0.012], [w * 0.30, h * 0.16, 180, 0.008]
    ];
    for (var m = 0; m < ink.length; m++) {
      var q = ink[m];
      var g = c.createRadialGradient(q[0], q[1], 0, q[0], q[1], q[2]);
      g.addColorStop(0, "rgba(26,24,21," + q[3] + ")");
      g.addColorStop(0.55, "rgba(26,24,21," + q[3] * 0.45 + ")");
      g.addColorStop(1, "rgba(26,24,21,0)");
      c.fillStyle = g;
      c.beginPath(); c.arc(q[0], q[1], q[2], 0, TAU); c.fill();
    }
    c.restore();

    var vg = c.createRadialGradient(w * 0.5, h * 0.47, h * 0.26, w * 0.5, h * 0.5, h * 1.04);
    vg.addColorStop(0, "rgba(70,52,34,0)");
    vg.addColorStop(1, "rgba(70,52,34,0.20)");
    c.fillStyle = vg;
    c.fillRect(0, 0, w, h);

    return pc[0];
  }

  /* ================================================================
   * 武将牌（和伤害 / 雷电 / 回复 / 失去体力版同一张牌）
   * ============================================================== */
  function paintHero(hero, S) {
    var w = CARD.w, h = CARD.h, pad = 24;
    var cc = offscreen((w + CARD.pad * 2) * S, (h + CARD.pad * 2) * S), c = cc[1];
    c.scale(S, S);
    var r = rng(hero.name.charCodeAt(0) * 97 + 11);
    c.translate(CARD.pad, CARD.pad);

    c.save();
    c.shadowColor = "rgba(43,36,28,0.22)";
    c.shadowBlur = 26;
    c.shadowOffsetY = 12;
    pathRound(c, 0, 0, w, h, 14);
    c.fillStyle = C.card;
    c.fill();
    c.restore();

    var g = c.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, C.card);
    g.addColorStop(1, C.cardLo);
    pathRound(c, 0.5, 0.5, w - 1, h - 1, 14);
    c.fillStyle = g;
    c.fill();

    c.save();
    pathRound(c, 0.5, 0.5, w - 1, h - 1, 14);
    c.clip();
    for (var i = 0; i < 2400; i++) {
      c.fillStyle = r() < 0.5 ? "rgba(28,26,23,0.011)" : "rgba(255,255,255,0.05)";
      c.fillRect(r() * w, r() * h, 1.3, 1.3);
    }
    c.restore();

    pathRound(c, 0.5, 0.5, w - 1, h - 1, 14);
    c.strokeStyle = C.line;
    c.lineWidth = 1;
    c.stroke();

    pathRound(c, pad - 8, pad - 8, w - (pad - 8) * 2, h - (pad - 8) * 2, 8);
    c.strokeStyle = "rgba(221,216,208,0.95)";
    c.stroke();

    /* 势力印 */
    c.save();
    c.translate(pad + 14, pad + 16);
    c.fillStyle = hero.factionColor;
    pathRound(c, -15, -15, 30, 30, 5);
    c.fill();
    c.fillStyle = "#fdf8ef";
    c.font = "700 19px " + SERIF;
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.fillText(hero.faction, 0, 1);
    c.restore();

    /* 将名 */
    c.textAlign = "left";
    c.textBaseline = "middle";
    c.fillStyle = C.ink;
    c.font = "700 38px " + SERIF;
    c.fillText(hero.name, pad + 42, pad + 17);

    /* 体力段 */
    var segW = 22, segH = 13, segGap = 6, hp0 = hero.hp;
    var totalW = hp0 * segW + (hp0 - 1) * segGap;
    var hx = w - pad - 14 - totalW;
    for (var s = 0; s < hp0; s++) {
      pathRound(c, hx + s * (segW + segGap), pad + 10, segW, segH, 3);
      c.fillStyle = C.ink;
      c.fill();
    }
    c.textAlign = "right";
    c.font = "400 13px " + SANS;
    c.fillStyle = C.dim;
    c.fillText("体力上限 " + hp0, w - pad - 14, pad + 44);

    /* 淡墨竖排将名 */
    c.save();
    c.globalAlpha = 0.05;
    c.fillStyle = C.ink;
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.font = "700 128px " + SERIF;
    for (var ch = 0; ch < hero.name.length; ch++) {
      c.fillText(hero.name[ch], w * 0.5, h * 0.40 + (ch - (hero.name.length - 1) / 2) * 138);
    }
    c.restore();

    /* 技能区 */
    var divY = h - 214;
    c.beginPath();
    c.moveTo(pad, divY);
    c.lineTo(w - pad, divY);
    c.strokeStyle = "rgba(221,216,208,1)";
    c.lineWidth = 1;
    c.stroke();

    for (var k = 0; k < hero.skills.length; k++) {
      var sk = hero.skills[k];
      var y = divY + 30 + k * 84;
      c.textAlign = "left";
      c.textBaseline = "alphabetic";
      c.fillStyle = C.ink;
      c.font = "700 20px " + SERIF;
      c.fillText(sk[0], pad, y);
      c.fillStyle = C.dim;
      c.font = "400 14px " + SANS;
      wrapText(c, sk[1], pad, y + 23, w - pad * 2, 18, k === 0 ? 2 : 3);
    }

    c.textAlign = "right";
    c.textBaseline = "alphabetic";
    c.font = "400 12px " + SANS;
    c.fillStyle = "rgba(111,106,98,0.45)";
    c.fillText("SGS · HERO", w - pad, h - 16);

    return cc[0];
  }

  /* ================================================================
   * 几何
   * ============================================================== */
  function viewSize(view) {
    var v = view || {};
    return {
      w: Number(v.w) > 0 ? Number(v.w) : W,
      h: Number(v.h) > 0 ? Number(v.h) : H
    };
  }

  function stageRect(stage, view) {
    var s = stage || {};
    var hasWH = Number(s.w) > 0 && Number(s.h) > 0;
    var hasCenter = Number(s.cx) > 0 && Number(s.cy) > 0;
    var defK = Math.min(view.w / W, view.h / H);
    var w = hasWH ? Number(s.w) : DST.w * defK;
    var h = hasWH ? Number(s.h) : DST.h * defK;
    var cx = hasCenter ? Number(s.cx) : (hasWH ? view.w / 2 : DST.cx * (view.w / W));
    var cy = hasCenter ? Number(s.cy) : (hasWH ? view.h / 2 : DST.cy * (view.h / H));
    return { x: cx - w / 2, y: cy - h / 2, w: w, h: h };
  }

  function buildGeom(cr) {
    var w = cr.w, h = cr.h;
    var ang = GEOM.deg * Math.PI / 180;
    var ca = Math.cos(ang), sa = Math.sin(ang);
    var overL = w * GEOM.overL, overR = w * GEOM.overR;
    var p0 = { x: cr.x, y: cr.y + h * GEOM.entry };
    var winL = cr.x - overL, winR = cr.x + w + overR;
    var sL = (winL - p0.x) / ca;
    var sR = (winR - p0.x) / ca;
    var span = sR - sL;
    var wire = w * GEOM.wire;
    var faceLen = w * GEOM.faceLen, faceWid = w * GEOM.faceWid;
    var pitch = w * GEOM.pitch;
    var half = Math.max(faceWid, w * GEOM.sideWid) * 0.5 + wire * 0.62;
    var hgt = half * 2 + wire * 1.2;
    var len = span;                    /* 链带的墨正好从 winL 铺到 winR */
    return {
      cr: cr, w: w, h: h, k: w / CARD.w,
      ang: ang, ca: ca, sa: sa, p0: p0,
      overL: overL, overR: overR, winL: winL, winR: winR,
      sL: sL, sR: sR, span: span,
      wire: wire, faceLen: faceLen, faceWid: faceWid, pitch: pitch,
      half: half, hgt: hgt, len: len,
      s0: sL,                     /* 链带本地 u=0 落在链上的 s */
      clipL: winL - wire * 0.7, clipR: winR + wire * 0.7,
      travel: span + pitch * 3.2  /* 入链的滑动行程 */
    };
  }

  function pointAt(g, s) {
    return { x: g.p0.x + g.ca * s, y: g.p0.y - g.sa * s };
  }

  /* 链带（含整条链 + 飞白 + 播撒）覆盖到的世界矩形；渲染层只开这么大 */
  function chainBBox(g, view) {
    var a = pointAt(g, g.s0 - g.travel - g.pitch);
    var b = pointAt(g, g.s0 + g.len + g.pitch);
    var x0 = Math.min(a.x, b.x) - g.half - 26;
    var x1 = Math.max(a.x, b.x) + g.half + 26;
    var y0 = Math.min(a.y, b.y) - g.half - 26;
    var y1 = Math.max(a.y, b.y) + g.half + 26;
    var x = clamp(Math.floor(x0), 0, view.w);
    var y = clamp(Math.floor(y0), 0, view.h);
    return {
      x: x, y: y,
      w: Math.max(1, Math.min(view.w, Math.ceil(x1)) - x),
      h: Math.max(1, Math.min(view.h, Math.ceil(y1)) - y)
    };
  }

  /* ================================================================
   * 铁索贴图：整条链画在一张横置的离屏图上
   *
   * 每个环都是"手写"的一笔：椭圆采样 + 轻微抖动（不是矢量圆），
   * 环身用一条渐变上浓下淡，再咬掉几块飞白（destination-out），
   * 正面环先画、侧身环后画（侧身环前先擦一圈 1px 的空隙，读起来是"套进去"）。
   * ============================================================== */
  /* 一个胶囊（stadium）轮廓的采样点：上边 + 右半圆 + 下边 + 左半圆，带外法向 */
  function stadiumPoints(len, wid) {
    var R = wid / 2, half = Math.max(0.01, len / 2 - R);
    var pts = [], i, n, a;
    n = 6;
    for (i = 0; i <= n; i++) pts.push({ x: -half + 2 * half * (i / n), y: -R, nx: 0, ny: -1 });
    n = 8;
    for (i = 1; i <= n; i++) {
      a = -Math.PI / 2 + Math.PI * (i / n);
      pts.push({ x: half + Math.cos(a) * R, y: Math.sin(a) * R, nx: Math.cos(a), ny: Math.sin(a) });
    }
    n = 6;
    for (i = 1; i <= n; i++) pts.push({ x: half - 2 * half * (i / n), y: R, nx: 0, ny: 1 });
    n = 8;
    for (i = 1; i < n; i++) {
      a = Math.PI / 2 + Math.PI * (i / n);
      pts.push({ x: -half + Math.cos(a) * R, y: Math.sin(a) * R, nx: Math.cos(a), ny: Math.sin(a) });
    }
    return pts;
  }

  /* 把胶囊轮廓接成一条闭合曲线（采样点各带一点抖动：手写的边不是真的直） */
  function stadiumPath(c, len, wid, wob, r) {
    var pts = stadiumPoints(len, wid);
    var n = pts.length, jp = [], i, p, j;
    for (i = 0; i < n; i++) {
      p = pts[i];
      j = (r() - 0.5) * wob * wid * 0.6;
      /* 直边加点沿链的抖动，弧上不加，免得胶囊的两个端头变毛 */
      var t = Math.abs(p.ny) * (0.5 + r() * 0.5);
      jp.push([p.x + p.nx * j + (p.ny === 0 ? 0 : 0), p.y + p.ny * j + (p.nx === 0 ? (r() - 0.5) * wob * wid * 0.25 * t : 0)]);
    }
    c.moveTo((jp[n - 1][0] + jp[0][0]) / 2, (jp[n - 1][1] + jp[0][1]) / 2);
    for (i = 0; i < n; i++) {
      var q = jp[(i + 1) % n];
      c.quadraticCurveTo(jp[i][0], jp[i][1], (jp[i][0] + q[0]) / 2, (jp[i][1] + q[1]) / 2);
    }
    c.closePath();
  }

  /* 胶囊中心线上按周长取一点（飞白要咬在环身上，得先知道环身在哪） */
  function stadiumSample(len, wid, t) {
    var R = wid / 2, half = Math.max(0.01, len / 2 - R);
    var straight = 2 * half, cap = Math.PI * R;
    var P = 2 * straight + 2 * cap;
    var d = ((t % 1) + 1) % 1 * P, a;
    if (d < straight) {
      return { x: -half + d, y: -R, nx: 0, ny: -1, tx: 1, ty: 0 };
    }
    d -= straight;
    if (d < cap) {
      a = -Math.PI / 2 + (d / cap) * Math.PI;
      return { x: half + Math.cos(a) * R, y: Math.sin(a) * R, nx: Math.cos(a), ny: Math.sin(a), tx: -Math.sin(a), ty: Math.cos(a) };
    }
    d -= cap;
    if (d < straight) {
      return { x: half - d, y: R, nx: 0, ny: 1, tx: -1, ty: 0 };
    }
    d -= straight;
    a = Math.PI / 2 + (d / cap) * Math.PI;
    return { x: -half + Math.cos(a) * R, y: Math.sin(a) * R, nx: Math.cos(a), ny: Math.sin(a), tx: -Math.sin(a), ty: Math.cos(a) };
  }

  function bakeChainSprite(g, S, seed) {
    var pad = 26;
    var cv = offscreen((g.len + pad * 2) * S, (g.hgt + pad * 2) * S), c = cv[1];
    c.scale(S, S);
    var midY = pad + g.hgt / 2;
    var r = rng(seed);
    var wire = g.wire;

    function centerY(u) {
      return midY + Math.sin(u * 0.0125 + 1.1) * wire * 0.44 + Math.sin(u * 0.0371 + 0.4) * wire * 0.18;
    }

    /* 首尾各一枚完整的正面环收口：铁索的两头是环，不是被抹淡的边 */
    var firstC = g.faceLen * 0.5 + wire * 0.55;
    var lastC = g.len - firstC;
    var n = Math.max(3, Math.round((lastC - firstC) / g.pitch) + 1);
    if (n % 2 === 0) n += 1;
    var step = (lastC - firstC) / (n - 1);
    var links = [];
    for (var i = 0; i < n; i++) {
      var u = pad + firstC + i * step;
      if (!GEOM.sideOn && i % 2 === 1) continue;
      links.push({
        x: u,
        y: centerY(u - pad) + (GEOM.sideOn ? 0 : (i % 2 ? -1 : 1) * g.faceWid * 0.045),
        face: i % 2 === 0,
        over: i % 2 === 0,          /* 只用胶囊时：隔一环压在上面 */
        k: 0.96 + r() * 0.08,
        seed: r(),
        skew: (r() - 0.5) * 0.030
      });
    }

    /* 正面环：胶囊，长轴沿链；侧身环：长轴横链、孔只剩一条缝 —— 两个方向互相垂直 */
    function dims(l) {
      var d = l.face
        ? { len: g.faceLen * l.k, wid: g.faceWid * l.k }
        : { len: g.w * GEOM.sideLen * l.k, wid: g.w * GEOM.sideWid * l.k };
      d.slot = l.face ? 1 : GEOM.sideSlot;
      return d;
    }

    /* 胶囊轮廓（带抖动）追加到当前路径：外圈 + 内圈组成环 */
    function wobbleStadium(len, wid, wob, rngv) {
      var pts = stadiumPoints(len, wid);
      var m = pts.length, jp = [], i, p, j;
      for (i = 0; i < m; i++) {
        p = pts[i];
        j = (rngv() - 0.5) * wob * wid * 0.55;
        jp.push([p.x + p.nx * j, p.y + p.ny * j]);
      }
      c.moveTo((jp[m - 1][0] + jp[0][0]) / 2, (jp[m - 1][1] + jp[0][1]) / 2);
      for (i = 0; i < m; i++) {
        var q = jp[(i + 1) % m];
        c.quadraticCurveTo(jp[i][0], jp[i][1], (jp[i][0] + q[0]) / 2, (jp[i][1] + q[1]) / 2);
      }
      c.closePath();
    }

    function drawRing(l, halo) {
      var d = dims(l);
      var wr = wire * (l.face ? 1.0 : 0.96);
      var ol = d.len + wr, ow = d.wid + wr;
      var il = Math.max(1.0, d.len - wr);
      var iw = Math.max(0.8, (d.wid - wr) * d.slot);
      var rr = rng(Math.floor(l.seed * 99991) + 17);

      c.save();
      c.translate(l.x, l.y);
      c.rotate(l.skew);
      if (halo) {
        /* 压在上面的那一枚：先沿自己这一圈擦出一道纸色的缝 ——
         * 被它压住的邻环就断在交叠处，两枚环才读成"扣在一起"而不是"叠在一起" */
        c.save();
        c.globalCompositeOperation = "destination-out";
        c.strokeStyle = "rgba(0,0,0,0.86)";
        c.lineWidth = wr * 1.42;
        c.beginPath();
        wobbleStadium(d.len + wr * 0.1, d.wid + wr * 0.1, 0, rng(Math.floor(l.seed * 733) + 5));
        c.stroke();
        c.restore();
      }
      c.beginPath();
      wobbleStadium(ol, ow, GEOM.wob, rr);
      wobbleStadium(il, iw, GEOM.wob * 1.35, rng(Math.floor(l.seed * 991) + 41));
      var tone = 0.94 + l.seed * 0.06;
      var lg = c.createLinearGradient(-ol * 0.30, -ow * 0.55, ol * 0.30, ow * 0.95);
      lg.addColorStop(0, rgba(C.link0, 1.0 * tone));
      lg.addColorStop(0.40, rgba(C.link1, 0.99 * tone));
      lg.addColorStop(0.78, rgba(C.link2, 0.98 * tone));
      lg.addColorStop(1, rgba("#4a4336", 0.94 * tone));
      c.fillStyle = lg;
      c.fill("evenodd");
      c.strokeStyle = rgba(C.link0, 0.55 * tone);
      c.lineWidth = Math.max(0.6, wr * 0.13);
      c.stroke();

      /* 下半边"枯"一点：铁条下缘留一线纸色，墨才不是一块平涂 */
      c.save();
      c.beginPath();
      c.rect(-ol, 0, ol * 2, ow);
      c.clip();
      c.globalCompositeOperation = "destination-out";
      c.strokeStyle = "rgba(0,0,0," + (0.10 + l.seed * 0.10) + ")";
      c.lineWidth = wr * 0.30;
      c.beginPath();
      wobbleStadium(d.len, d.wid, 0, rng(11 + Math.floor(l.seed * 977)));
      c.stroke();
      c.restore();
      c.restore();
    }

    /* 真实的链子是"一上一下"扣的：交叠处上边这一侧 A 压 B，下边那一侧 B 压 A。
     * 于是按链轴把整条链劈成上下两半，各自排前后：
     *   · 上半边：正面环在上（侧身环先画）；
     *   · 下半边：侧身环在上（正面环先画）。
     * 压在上面的那一枚沿自己一圈擦一道纸色的缝，被压住的那一枚就断在交叠处 ——
     * 两个方向才真的"扣"在一起，而不是一块儿摊平。 */
    var BIG = g.len + g.hgt * 4;
    function paintHalf(overIsFace) {
      c.save();
      c.beginPath();
      if (overIsFace) c.rect(-BIG, -BIG, BIG * 2, midY + 1.5 + BIG);
      else c.rect(-BIG, midY - 1.5, BIG * 2, BIG * 2);
      c.clip();
      for (var q = 0; q < 2; q++) {
        for (var j = 0; j < links.length; j++) {
          var Lj = links[j];
          var isOver = (Lj.face === overIsFace);
          if (isOver !== (q === 1)) continue;
          drawRing(Lj, isOver);
        }
      }
      c.restore();
    }

    if (GEOM.sideOn) {
      paintHalf(true);    /* 上半边：正面环压在上面 */
      paintHalf(false);   /* 下半边：侧身环压在上面 */
    } else {
      /* 只用胶囊时：隔一环压在上面，在整幅上擦缝 */
      for (var pass0 = 0; pass0 < 2; pass0++) {
        for (i = 0; i < links.length; i++) {
          var L0 = links[i];
          if (L0.over !== (pass0 === 1)) continue;
          drawRing(L0, L0.over);
        }
      }
    }

    /* 飞白：笔走到哪儿墨干了，就在铁条上留一道**顺着环**的干痕（不是挖圆洞） */
    var rb = rng(seed + 977);
    for (var bi0 = 0; bi0 < links.length; bi0++) {
      if (rb() < 0.32) continue;
      var L = links[bi0];
      var d2 = dims(L);
      var biteN = rb() < 0.34 ? 2 : 1;
      for (var b = 0; b < biteN; b++) {
        var sp = stadiumSample(d2.len, d2.wid, rb());
        var off = (rb() < 0.5 ? -1 : 1) * wire * (0.22 + rb() * 0.16);
        c.save();
        c.translate(L.x, L.y);
        c.rotate(L.skew);
        c.translate(sp.x + sp.nx * off, sp.y + sp.ny * off);
        c.rotate(Math.atan2(sp.ty, sp.tx));
        c.globalCompositeOperation = "destination-out";
        c.fillStyle = "rgba(0,0,0," + (0.38 + rb() * 0.34) + ")";
        c.beginPath();
        c.ellipse(0, 0, wire * (0.42 + rb() * 0.5), wire * (0.20 + rb() * 0.16), 0, 0, TAU);
        c.fill();
        c.restore();
      }
    }

    return {
      canvas: cv[0], S: S, pad: pad,
      hgt: g.hgt, len: g.len, g: g
    };
  }

  function blurSprite(sp, px) {
    var S = sp.S;
    var cv = offscreen(sp.canvas.width, sp.canvas.height), c = cv[1];
    c.scale(S, S);
    if ("filter" in c) {
      c.filter = "blur(" + px + "px)";
      c.drawImage(sp.canvas, 0, 0, sp.canvas.width / S, sp.canvas.height / S);
      c.filter = "none";
    } else {
      /* 退化：叠几层偏移，虽然不是真模糊，但也不会变成硬边 */
      for (var i = 0; i < 8; i++) {
        var a = i / 8 * TAU;
        c.globalAlpha = 0.22;
        c.drawImage(sp.canvas, Math.cos(a) * px, Math.sin(a) * px,
          sp.canvas.width / S, sp.canvas.height / S);
      }
      c.globalAlpha = 1;
    }
    return { canvas: cv[0], S: S, pad: sp.pad, hgt: sp.hgt, len: sp.len, g: sp.g };
  }

  /* -------------------------------------------------- 画链带（世界坐标） ---- */
  function drawSprite(c, sp, g, sOrigin, alpha, dy, dx) {
    if (alpha <= 0.002) return;
    var p = pointAt(g, sOrigin);
    c.save();
    c.translate(p.x + (dx || 0), (dy || 0) + p.y);
    c.rotate(-g.ang);
    c.globalAlpha = alpha;
    c.drawImage(sp.canvas, -sp.pad, -(sp.pad + sp.hgt / 2),
      sp.canvas.width / sp.S, sp.canvas.height / sp.S);
    c.restore();
  }

  /* 链的可见窗口：入链时左界停在**牌沿**（链从牌沿后面钻出来），落定后
   * 甩到 winL（链尾那一截探出来）；两端都不做透明度淡出 —— 铁索的两头是环。 */
  function clipWindow(c, bb, x0, x1) {
    c.beginPath();
    c.rect(x0, bb.y - 8, Math.max(1, x1 - x0), bb.h + 16);
    c.clip();
  }

  /* 探出牌外的那一截：先被牌沿的阴影压暗，再淡掉 —— 读起来是"绕到牌后面"，
   * 而不是"墨用完了"。source-atop ⇒ 只作用在已经画出来的链身上。 */
  function shadeStubs(c, g, strengthL, strengthR, bb) {
    function stub(side, strength) {
      if (strength <= 0.002) return;
      var x0 = side < 0 ? g.winL : g.winR;
      var x1 = side < 0 ? g.cr.x : g.cr.x + g.cr.w;
      var gr = c.createLinearGradient(x0, 0, x1, 0);
      gr.addColorStop(0, "rgba(5,5,4," + (0.55 * strength) + ")");
      gr.addColorStop(0.55, "rgba(5,5,4," + (0.22 * strength) + ")");
      gr.addColorStop(1, "rgba(5,5,4,0)");
      c.save();
      c.globalCompositeOperation = "source-atop";
      c.fillStyle = gr;
      var lo = Math.min(x0, x1) - 3;
      c.fillRect(lo, bb.y - 6, Math.abs(x1 - x0) + 6, bb.h + 12);
      c.restore();
    }
    stub(-1, strengthL);
    stub(1, strengthR);
  }

  /* ================================================================
   * 牌面上的墨：干笔引路 / 牌沿压痕 / 挤出来的墨点
   * ============================================================== */
  function drawGhost(c, g, prog, alpha) {
    if (alpha <= 0.004 || prog <= 0) return;
    var r = rng(6113);
    var N = 54;
    c.save();
    c.lineCap = "round";
    for (var i = 0; i < N; i++) {
      var f0 = i / N, f1 = (i + 1) / N;
      if (f0 > prog) break;
      var sA = g.sL + g.span * f0;
      var sB = g.sL + g.span * Math.min(f1, prog);
      var a = pointAt(g, sA), b = pointAt(g, sB);
      /* 一道干笔：贴着链身偏上一点，顺着链走，只做很慢的起伏 —— 不是乱划的短线 */
      var oA = -g.half * 0.40 + Math.sin(f0 * 5.2) * g.wire * 0.35;
      var oB = -g.half * 0.40 + Math.sin(f1 * 5.2) * g.wire * 0.35;
      if (r() < 0.10) continue;
      c.strokeStyle = "rgba(26,24,20," + (alpha * (0.55 + r() * 0.45)) + ")";
      c.lineWidth = g.wire * (0.16 + r() * 0.22);
      c.beginPath();
      c.moveTo(a.x - g.sa * oA, a.y + g.ca * oA);
      c.lineTo(b.x - g.sa * oB, b.y + g.ca * oB);
      c.stroke();
    }
    c.restore();
  }

  function drawEdgeMarks(c, g, alpha) {
    if (alpha <= 0.004) return;
    var edges = [
      { s: 0, side: -1 },
      { s: (g.cr.x + g.cr.w - g.p0.x) / g.ca, side: 1 }
    ];
    for (var i = 0; i < edges.length; i++) {
      var e = edges[i];
      var p = pointAt(g, e.s);
      c.save();
      c.translate(p.x, p.y);
      c.rotate(-g.ang);
      c.globalCompositeOperation = "multiply";
      /* 链压在牌沿上的一道墨 */
      var gg = c.createLinearGradient(0, -g.half, 0, g.half);
      gg.addColorStop(0, "rgba(24,22,19,0)");
      gg.addColorStop(0.42, "rgba(24,22,19," + (0.30 * alpha) + ")");
      gg.addColorStop(0.62, "rgba(18,16,14," + (0.42 * alpha) + ")");
      gg.addColorStop(1, "rgba(24,22,19,0)");
      c.fillStyle = gg;
      c.beginPath();
      c.ellipse(e.side * g.wire * 0.35, 0, g.wire * 0.95, g.half * 1.05, 0, 0, TAU);
      c.fill();
      /* 牌沿背光的一线：铁索压下去，牌边自己也暗一格 */
      c.fillStyle = "rgba(20,18,16," + (0.18 * alpha) + ")";
      c.fillRect(e.side * -1.4 - (e.side < 0 ? 0 : 2.8), -g.half * 1.35, 2.8, g.half * 2.7);
      c.restore();

      /* 三颗被挤出来的小墨点 */
      var r = rng(811 + i * 37);
      c.save();
      c.fillStyle = "rgba(22,20,17,0.55)";
      for (var d = 0; d < 3; d++) {
        var dd = (0.8 + r() * 2.4) * g.wire * 0.4;
        var yy = (r() - 0.5) * g.half * 1.9;
        c.beginPath();
        c.ellipse(p.x + e.side * dd * 0.4, p.y + yy, dd, dd * (0.7 + r() * 0.5), r() * TAU, 0, TAU);
        c.fill();
      }
      c.restore();
    }
  }

  /* 落定时从链底挤出来的几点墨（常态里也留着，是"牌被绑过"的痕迹） */
  function drawSqueeze(c, g, alpha) {
    if (alpha <= 0.004) return;
    var r = rng(94021);
    c.save();
    c.globalCompositeOperation = "multiply";
    for (var i = 0; i < 8; i++) {
      var f = 0.10 + r() * 0.80;
      var s = g.sL + g.span * f;
      var p = pointAt(g, s);
      var off = g.half * (0.72 + r() * 0.62);
      var rad = (0.5 + r() * 1.15) * Math.max(0.6, g.wire * 0.5);
      c.fillStyle = "rgba(24,21,17," + ((0.30 + r() * 0.34) * alpha) + ")";
      c.beginPath();
      c.ellipse(p.x - g.sa * off, p.y + g.ca * off, rad, rad * (0.62 + r() * 0.5), r() * TAU, 0, TAU);
      c.fill();
    }
    c.restore();
  }

  /* ================================================================
   * 墨点 / 碎屑 / 墨雾
   * ============================================================== */
  function drawFleck(c, x, y, r, a, stretch, ang) {
    c.save();
    c.translate(x, y);
    c.rotate(ang || 0);
    c.fillStyle = "rgba(22,20,17," + a + ")";
    c.beginPath();
    c.ellipse(0, 0, r * (stretch || 1), r, 0, 0, TAU);
    c.fill();
    c.restore();
  }

  function drawBloom(c, x, y, r, a) {
    if (a <= 0.003 || r <= 0.5) return;
    var g2 = c.createRadialGradient(x, y, 0, x, y, r);
    g2.addColorStop(0, "rgba(20,18,15," + (a * 0.85) + ")");
    g2.addColorStop(0.45, "rgba(22,20,17," + (a * 0.42) + ")");
    g2.addColorStop(1, "rgba(22,20,17,0)");
    c.save();
    c.globalCompositeOperation = "multiply";
    c.fillStyle = g2;
    c.beginPath();
    c.arc(x, y, r, 0, TAU);
    c.fill();
    c.restore();
  }

  function drawMist(c, x, y, r, a) {
    if (a <= 0.004) return;
    var g2 = c.createRadialGradient(x, y, 0, x, y, r);
    g2.addColorStop(0, "rgba(48,45,40," + (a * 0.75) + ")");
    g2.addColorStop(0.55, "rgba(40,38,34," + (a * 0.34) + ")");
    g2.addColorStop(1, "rgba(40,38,34,0)");
    c.save();
    c.fillStyle = g2;
    c.beginPath();
    c.arc(x, y, r, 0, TAU);
    c.fill();
    c.restore();
  }

  /* ================================================================
   * 场景（贴图 + 粒子 + 图层）—— create 与 bakeStatic 共用
   * ============================================================== */
  function buildScene(opts) {
    var view = viewSize(opts.view);
    var cr = stageRect(opts.stage, view);
    var dpr = Number(opts.dpr) > 0 ? Number(opts.dpr) : 1;
    var bare = opts.bare === true;
    var g = buildGeom(cr);

    var base = Number(opts.seed) > 0 ? Number(opts.seed) : 1701;
    var crisp = bakeChainSprite(g, dpr, base);
    var wet = blurSprite(crisp, Math.max(2.6, g.wire * 0.95));
    var soft = blurSprite(crisp, Math.max(5.0, g.wire * 1.7));

    var paper = bare ? null : paintPaper(view.w, view.h, dpr);
    var heroImg = bare ? null : paintHero(HERO, dpr);

    var bb = chainBBox(g, view);
    var layer = offscreen(bb.w * dpr, bb.h * dpr);

    /* ---- 入链时被甩下来的墨点（固定种子） ---- */
    var r = rng(3301);
    var flecks = [];
    var M = MODES.link;
    for (var i = 0; i < M.flecks; i++) {
      var tf = 0.16 + r() * 0.62;
      var sp = slideProgress(tf, M);
      var sHead = g.s0 + g.len - g.travel * (1 - sp);
      var pt = pointAt(g, sHead);
      flecks.push({
        t0: M.slideAt + M.slideDur * tf,
        x: pt.x + (r() - 0.5) * g.wire * 2,
        y: pt.y + (r() - 0.5) * g.half * 1.5,
        vx: -g.ca * (30 + r() * 110) + (r() - 0.5) * 70,
        vy: -20 - r() * 90,
        rad: (0.8 + r() * 1.8) * Math.max(0.7, g.wire * 0.35),
        life: 0.36 + r() * 0.22
      });
    }

    /* ---- 崩断：碎屑 / 溅点 / 墨雾 ---- */
    var sBreak = g.sL + g.span * 0.455;
    var bp = pointAt(g, sBreak);
    var rb = rng(7717);
    var shards = [];
    for (i = 0; i < MODES.break.shards; i++) {
      var ang = rb() * TAU;
      var sp2 = 80 + rb() * 300;
      shards.push({
        x: bp.x + (rb() - 0.5) * g.wire * 2,
        y: bp.y + (rb() - 0.5) * g.half * 1.8,
        vx: Math.cos(ang) * sp2,
        vy: Math.sin(ang) * sp2 - 70,
        len: (3.5 + rb() * 7) * Math.max(0.6, g.wire * 0.55),
        wid: (1.8 + rb() * 2.6) * Math.max(0.5, g.wire * 0.30),
        rot: rb() * TAU,
        spin: (rb() - 0.5) * 16,
        life: 0.30 + rb() * 0.34
      });
    }

    /* 崩断的那一枚环：裂成两个 C，各自弹开、翻转 */
    var ringArc = [];
    for (i = 0; i < 2; i++) {
      var side = i === 0 ? -1 : 1;
      ringArc.push({
        side: side,
        x: bp.x, y: bp.y,
        vx: -g.sa * side * (150 + rb() * 90) - g.ca * 30,
        vy: g.ca * side * (150 + rb() * 90) - 150,
        r: g.faceLen * 0.42,
        wire: g.wire * 1.0,
        a0: side < 0 ? 0.55 : 0.55 + Math.PI,
        span: 2.05,
        rot: -g.ang + (rb() - 0.5) * 0.5,
        spin: side * (5 + rb() * 6),
        life: 0.30 + rb() * 0.16
      });
    }
    var dots = [];
    for (i = 0; i < MODES.break.dots; i++) {
      var ang2 = rb() * TAU;
      var sp3 = 40 + rb() * 210;
      dots.push({
        x: bp.x, y: bp.y,
        vx: Math.cos(ang2) * sp3,
        vy: Math.sin(ang2) * sp3 - 60,
        rad: (0.7 + rb() * 2.2) * Math.max(0.6, g.wire * 0.3),
        alpha: 0.35 + rb() * 0.5,
        life: 0.4 + rb() * 0.6
      });
    }
    var flakes = [];
    for (i = 0; i < 16; i++) {
      var ff = 0.05 + rb() * 0.92;
      var fp = pointAt(g, g.sL + g.span * ff);
      flakes.push({
        x: fp.x + (rb() - 0.5) * g.wire * 2.4,
        y: fp.y + (rb() - 0.5) * g.half * 1.7,
        vx: (rb() - 0.5) * 40,
        vy: -10 - rb() * 40,
        rad: (0.6 + rb() * 1.9) * Math.max(0.6, g.wire * 0.34),
        t0: 0.10 + rb() * 0.62,
        life: 0.5 + rb() * 0.6
      });
    }

    var mists = [];
    for (i = 0; i < MODES.break.mists; i++) {
      var f = 0.06 + rb() * 0.88;
      var mp = pointAt(g, g.sL + g.span * f);
      mists.push({
        x: mp.x + (rb() - 0.5) * g.half,
        y: mp.y + (rb() - 0.5) * g.half * 1.3,
        r: (18 + rb() * 40) * Math.max(0.55, g.k),
        rise: 10 + rb() * 30,
        delay: rb() * 0.26,
        life: 0.55 + rb() * 0.5,
        a: 0.12 + rb() * 0.13
      });
    }

    return {
      view: view, cr: cr, dpr: dpr, bare: bare, g: g,
      crisp: crisp, wet: wet, soft: soft,
      paper: paper, heroImg: heroImg,
      bb: bb, layer: layer[0], lctx: layer[1],
      flecks: flecks, shards: shards, ringArc: ringArc, dots: dots, flakes: flakes, mists: mists,
      sBreak: sBreak, breakPoint: bp
    };
  }

  /* -------------------------------------------------- 滑动进度 ---- */
  function slideProgress(t, M) {
    var x = clamp((t - M.slideAt) / M.slideDur, 0, 1);
    if (x >= 1) return 1;                      /* 末帧必须精确到位（常态贴图就是这一帧） */
    var e = 1 - Math.pow(1 - x, 2.7);          /* 拎起来快、到位慢 */
    if (x > 0.88) {                            /* 到位前的一点过冲 + 回弹 */
      var q = (x - 0.88) / 0.12;
      e += Math.sin(q * Math.PI) * 0.022;
    }
    return e;
  }

  /* 画整条链：投影 → 洇开的湿墨 → 链身 */
  function drawChain(lctx, scene, sOrigin, dy, dry, dx) {
    var g = scene.g;
    drawSprite(lctx, scene.wet, g, sOrigin, REST.wet * (dry == null ? 1 : dry), dy, dx);
    drawSprite(lctx, scene.crisp, g, sOrigin, REST.crisp, dy, dx);
  }

  function paintChainLayer(scene, alphaMul, opts) {
    var lctx = scene.lctx, g = scene.g, bb = scene.bb;
    lctx.setTransform(scene.dpr, 0, 0, scene.dpr, -bb.x * scene.dpr, -bb.y * scene.dpr);
    lctx.clearRect(bb.x, bb.y, bb.w, bb.h);
    lctx.globalAlpha = 1;
    lctx.globalCompositeOperation = "source-over";
    return lctx;
  }

  /* ================================================================
   * 入链
   * ============================================================== */
  function frameLink(scene, t) {
    var g = scene.g, M = MODES.link, ctx = scene.ctx, lctx = scene.lctx, bb = scene.bb;
    var p = slideProgress(t, M);
    var slideX = -(1 - p) * g.travel;

    /* 抖动：铁索在牌面上拖，越到位越稳 */
    var vib = Math.pow(1 - p, 1.6) * Math.sin(t * 122);
    var vib2 = Math.pow(1 - p, 1.6) * Math.sin(t * 87 + 1.3);
    var sag = 1.15 * ss(M.settleAt - 0.02, M.settleAt + 0.30, t);   /* 落定后往下沉一点 */
    var settleK = ss(M.settleAt, M.settleAt + 0.34, t);
    var bleedK = ss(M.bleedAt, M.duration, t);

    /* ---------------- 牌面（纸 / 牌） ---------------- */
    if (!scene.bare) {
      ctx.drawImage(scene.heroImg,
        g.cr.x - CARD.pad * g.k, g.cr.y - CARD.pad * g.k,
        g.cr.w + CARD.pad * 2 * g.k, g.cr.h + CARD.pad * 2 * g.k);
    }

    /* ---------------- 链底下的墨迹 ---------------- */
    drawGhost(ctx, g, ss(M.ghostAt, M.ghostAt + M.ghostDur, t), 0.30 * (1 - 0.55 * settleK));
    drawEdgeMarks(ctx, g, ss(M.rubAt, M.rubAt + 0.20, t) * REST.rub);
    drawSqueeze(ctx, g, ss(M.settleAt, M.settleAt + 0.26, t));

    /* ---------------- 链（进图层再抠两端） ---------------- */
    paintChainLayer(scene);
    var reveal = ss(M.settleAt - 0.14, M.settleAt + 0.10, t);
    lctx.save();
    clipWindow(lctx, bb,
      reveal >= 1 ? g.clipL : g.cr.x + (g.clipL - g.cr.x) * reveal, g.clipR);
    drawChain(lctx, scene, g.s0 + slideX, sag + vib2 * 1.1, 0.55 + 0.45 * bleedK, vib * 0.9);
    shadeStubs(lctx, g, 1, 1, bb);
    lctx.restore();
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(scene.layer, Math.round(bb.x * scene.dpr), Math.round(bb.y * scene.dpr));
    ctx.restore();

    /* ---------------- 甩下来的墨点 ---------------- */
    for (var i = 0; i < scene.flecks.length; i++) {
      var f = scene.flecks[i];
      var dt = t - f.t0;
      if (dt < 0 || dt > f.life) continue;
      var x = f.x + f.vx * dt;
      var y = f.y + f.vy * dt + 0.5 * GRAV * dt * dt;
      var a = 0.75 * (1 - dt / f.life);
      drawFleck(ctx, x, y, f.rad, a, 1 + dt * 2.2, Math.atan2(f.vy + GRAV * dt, f.vx));
    }

    /* ---------------- 落定：两端各一口墨气（在 duration 之前一定收干净，
     * 否则末帧就比常态多一口墨，末帧 ≡ 常态的等式就破了） ---------------- */
    var BW = 0.32;
    if (t > M.settleAt - 0.02 && t < M.settleAt - 0.02 + BW) {
      var q = clamp((t - M.settleAt + 0.02) / BW, 0, 1);
      var eL = pointAt(g, g.sL + g.span * 0.02);
      var eR = pointAt(g, g.sL + g.span * 0.985);
      drawBloom(ctx, eL.x, eL.y, 34 * g.k * (0.5 + q), 0.20 * (1 - q) * (1 - q));
      drawBloom(ctx, eR.x, eR.y, 30 * g.k * (0.5 + q), 0.16 * (1 - q) * (1 - q));
    }
  }

  /* ================================================================
   * 解锁
   * ============================================================== */
  function frameBreak(scene, t) {
    var g = scene.g, M = MODES.break, ctx = scene.ctx, lctx = scene.lctx, bb = scene.bb;
    var tension = clamp(t / M.snapAt, 0, 1);
    var st = t - M.snapAt;                     /* 断后时间 */
    var snapped = st >= 0;
    var dissolve = ss(M.dissolveAt, M.dissolveAt + M.dissolveDur, t);
    var fade = 1 - dissolve;

    if (!scene.bare) {
      ctx.drawImage(scene.heroImg,
        g.cr.x - CARD.pad * g.k, g.cr.y - CARD.pad * g.k,
        g.cr.w + CARD.pad * 2 * g.k, g.cr.h + CARD.pad * 2 * g.k);
    }

    /* ---------------- 链底下的墨迹：跟着一起化掉 ---------------- */
    drawGhost(ctx, g, 1, REST.ghost * fade);
    drawEdgeMarks(ctx, g, REST.rub * fade * (1 + 0.12 * tension));
    drawSqueeze(ctx, g, fade);

    /* ---------------- 链本体 ---------------- */
    paintChainLayer(scene);

    if (!snapped) {
      /* 绷紧：整条链钉在牌沿上细颤 + 微微拧（t=0 时与常态逐像素一致） */
      var trem = Math.sin(t * 152) * g.wire * 1.5 * Math.pow(tension, 1.8);
      var twist = Math.sin(t * 121 + 0.8) * 0.0035 * Math.pow(tension, 1.5);
      var ccx = g.cr.x + g.cr.w / 2, ccy = g.cr.y + g.cr.h / 2;
      lctx.save();
      clipWindow(lctx, bb, g.clipL, g.clipR);
      lctx.translate(-g.sa * trem, g.ca * trem);
      lctx.translate(ccx, ccy);
      lctx.rotate(twist);
      lctx.translate(-ccx, -ccy);
      drawChain(lctx, scene, g.s0, REST.sag + 0, 1, 0);
      shadeStubs(lctx, g, 1, 1, bb);
      lctx.restore();
    } else {
      /* 断：两断各自弹开、模糊、化墨 */
      /* 崩断是"弹"：0.06s 内就弹到一半行程，之后一点点回弹 */
      var sep = 0.072 * g.span * (1 - Math.exp(-st * 15));
      var spring = 0.16 * Math.sin(st * 34) * Math.exp(-st * 11);
      var dL = -(sep + sep * spring) * 1.06;
      var dR = (sep + sep * spring) * 0.94;
      var rotL = -(0.026 + 0.014 * Math.sin(st * 20)) * clamp(st / 0.30, 0, 1);
      var rotR = (0.020 + 0.012 * Math.sin(st * 17)) * clamp(st / 0.30, 0, 1);
      var drift = 7 * dissolve;          /* 断了就没有张力了：两断往下坠 */

      /* 化墨：两断本身淡得慢，湿墨垫在底下留得久一点 —— 是"墨被纸吸走"，
       * 不是"整条链失焦"。 */
      var q = clamp((t - M.dissolveAt) / M.dissolveDur, 0, 1);
      var w0 = 1 - ss(0.12, 0.66, q);
      var w1 = 0.46 * ss(0.06, 0.30, q) * (1 - ss(0.55, 0.96, q));
      var w2 = 0;

      lctx.save();
      clipWindow(lctx, bb, g.clipL - g.half * 3, g.clipR + g.half * 3);
      drawHalf(lctx, scene, -1, dL, rotL, g.s0, scene.sBreak, w0, w1, w2, drift, fade);
      drawHalf(lctx, scene, 1, dR, rotR, g.s0, scene.sBreak, w0, w1, w2, drift, fade);
      lctx.restore();
    }
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(scene.layer, Math.round(bb.x * scene.dpr), Math.round(bb.y * scene.dpr));
    ctx.restore();

    if (!snapped) return;

    /* ---------------- 断口：迸出的碎屑与溅点 ---------------- */
    var bp = scene.breakPoint;
    var burst = clamp(st / 0.30, 0, 1);
    drawBloom(ctx, bp.x, bp.y, (22 + 54 * burst) * g.k, 0.42 * (1 - burst) * (1 - burst));

    for (var i = 0; i < scene.shards.length; i++) {
      var sh = scene.shards[i];
      if (st > sh.life) continue;
      var x = sh.x + sh.vx * st;
      var y = sh.y + sh.vy * st + 0.5 * GRAV * 1.4 * st * st;
      var a = 0.82 * (1 - st / sh.life);
      ctx.save();
      ctx.translate(x, y);
      ctx.rotate(sh.rot + sh.spin * st);
      ctx.fillStyle = "rgba(18,17,15," + a + ")";
      ctx.beginPath();
      ctx.moveTo(-sh.len, 0);
      ctx.lineTo(sh.len * 0.2, -sh.wid);
      ctx.lineTo(sh.len, sh.wid * 0.35);
      ctx.closePath();
      ctx.fill();
      ctx.restore();
    }

    for (i = 0; i < scene.ringArc.length; i++) {
      var ra = scene.ringArc[i];
      if (st > ra.life) continue;
      var rx2 = ra.x + ra.vx * st;
      var ry2 = ra.y + ra.vy * st + 0.5 * GRAV * 1.3 * st * st;
      var ra2 = 0.9 * (1 - st / ra.life);
      ctx.save();
      ctx.translate(rx2, ry2);
      ctx.rotate(ra.rot + ra.spin * st);
      ctx.strokeStyle = "rgba(16,15,13," + ra2 + ")";
      ctx.lineWidth = ra.wire;
      ctx.lineCap = "round";
      ctx.beginPath();
      ctx.arc(0, 0, ra.r, ra.a0, ra.a0 + ra.span);
      ctx.stroke();
      ctx.restore();
    }

    for (i = 0; i < scene.flakes.length; i++) {
      var fk = scene.flakes[i];
      var ft2 = st - fk.t0;
      if (ft2 < 0 || ft2 > fk.life) continue;
      var fx2 = fk.x + fk.vx * ft2;
      var fy2 = fk.y + fk.vy * ft2 + 0.5 * GRAV * 0.85 * ft2 * ft2;
      drawFleck(ctx, fx2, fy2, fk.rad, 0.62 * (1 - ft2 / fk.life), 1.25,
        Math.atan2(fk.vy + GRAV * 0.85 * ft2, fk.vx));
    }

    for (i = 0; i < scene.dots.length; i++) {
      var d = scene.dots[i];
      if (st > d.life) continue;
      var dx = d.x + d.vx * st;
      var dy = d.y + d.vy * st + 0.5 * GRAV * 1.1 * st * st;
      drawFleck(ctx, dx, dy, d.rad, d.alpha * (1 - st / d.life), 1.4,
        Math.atan2(d.vy + GRAV * 1.1 * st, d.vx));
    }

    /* ---------------- 化墨的雾：慢慢升起来、散掉 ---------------- */
    for (i = 0; i < scene.mists.length; i++) {
      var m = scene.mists[i];
      var mt = st - m.delay;
      if (mt < 0 || mt > m.life) continue;
      var mq = mt / m.life;
      drawMist(ctx, m.x - 6 * mq, m.y - m.rise * eOut(mq),
        m.r * (0.8 + mq * 1.5), m.a * (1 - mq) * (1 - mq));
    }
  }

  /* 两断之一：绕断点转一点、沿链轴弹开、按三个模糊档交叠淡出 */
  function drawHalf(c, scene, side, dist, rot, sOrigin, sBreak, w0, w1, w2, drift, fade) {
    var g = scene.g;
    var bp = scene.breakPoint;
    var uBreak = sBreak - sOrigin;               /* 断口在链带本地坐标里的位置 */
    var sprites = [scene.crisp, scene.wet, scene.soft];
    var weights = [w0, w1, w2];

    for (var s = 0; s < 3; s++) {
      var wgt = weights[s] * fade;
      if (wgt <= 0.004) continue;
      var sp = sprites[s];
      c.save();
      /* 世界层：绕断点弹开 */
      c.translate(bp.x, bp.y);
      c.rotate(rot);
      c.translate(-bp.x, -bp.y);
      c.translate(g.ca * dist, -g.sa * dist + drift);
      /* 链带层 */
      var o = pointAt(g, sOrigin);
      c.translate(o.x, o.y);
      c.rotate(-g.ang);
      /* 断口锯齿 */
      jagClip(c, uBreak, g, side, side < 0 ? 4211 : 9173);
      c.globalAlpha = Math.min(1, wgt);
      /* 只画两断自己的那一段 */
      var sx, sw;
      if (side < 0) {
        sx = 0; sw = uBreak + g.hgt;
      } else {
        sx = uBreak - g.hgt; sw = g.len - sx + g.hgt;
      }
      sx = clamp(sx, 0, g.len);
      sw = clamp(sw, 0, g.len - sx);
      if (sw <= 0.5) { c.restore(); continue; }
      c.drawImage(sp.canvas,
        (sp.pad + sx) * sp.S, 0, sw * sp.S, sp.canvas.height,
        sx, -sp.pad - sp.hgt / 2, sw, sp.canvas.height / sp.S);
      c.restore();
    }
  }

  /* 断口的锯齿裁剪：side<0 保留断口左边，side>0 保留右边 */
  function jagClip(c, uB, g, side, seed) {
    var r = rng(seed);
    var n = 7, ext = g.hgt;
    var y0 = -ext, y1 = ext;
    var pts = [];
    for (var i = 0; i <= n; i++) {
      var y = y0 + (y1 - y0) * (i / n);
      pts.push([uB + (r() - 0.5) * g.wire * 3.1, y]);
    }
    var B = g.len + ext * 2;
    c.beginPath();
    if (side < 0) {
      c.moveTo(-B, -B);
      c.lineTo(-B, B);
      for (i = n; i >= 0; i--) c.lineTo(pts[i][0], pts[i][1]);
    } else {
      c.moveTo(B, -B);
      c.lineTo(B, B);
      for (i = 0; i <= n; i++) c.lineTo(pts[i][0], pts[i][1]);
    }
    c.closePath();
    c.clip();
  }

  /* ================================================================
   * 常态（入链末帧）
   * ============================================================== */
  function frameHold(scene, t) {
    var g = scene.g, ctx = scene.ctx, lctx = scene.lctx, bb = scene.bb;
    if (!scene.bare) {
      ctx.drawImage(scene.heroImg,
        g.cr.x - CARD.pad * g.k, g.cr.y - CARD.pad * g.k,
        g.cr.w + CARD.pad * 2 * g.k, g.cr.h + CARD.pad * 2 * g.k);
    }
    drawGhost(ctx, g, 1, REST.ghost);
    drawEdgeMarks(ctx, g, REST.rub);
    drawSqueeze(ctx, g, 1);

    paintChainLayer(scene);
    lctx.save();
    clipWindow(lctx, bb, g.clipL, g.clipR);
    drawChain(lctx, scene, g.s0, REST.sag + 0, 1, 0);
    shadeStubs(lctx, g, 1, 1, bb);
    lctx.restore();
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.drawImage(scene.layer, Math.round(bb.x * scene.dpr), Math.round(bb.y * scene.dpr));
    ctx.restore();
  }

  /* ================================================================
   * create
   * ============================================================== */
  function create(canvas, opts) {
    opts = opts || {};
    var modeName = opts.mode === "break" ? "break" : opts.mode === "hold" ? "hold" : "link";
    var M = MODES[modeName];
    var clock = gameTiming(modeName, opts.timing);
    var scene = buildScene(opts);
    var dpr = scene.dpr, view = scene.view;

    canvas.width = Math.round(view.w * dpr);
    canvas.height = Math.round(view.h * dpr);
    var ctx = canvas.getContext("2d");
    scene.ctx = ctx;

    /* `draw(t)` 吃**演出自己的秒数**（0..`duration`）。内部把它映射回素材页的秒数：末帧
     * （t = duration）精确落在 `M.duration` 上——`bakeStatic` 烘的就是那一帧，于是
     * "入链演完"与"常态贴图"是同一段代码画出来的同一帧，不是临摹。 */
    function draw(t) {
      var board = clamp(Number(t) || 0, 0, clock.seconds);
      var at = clamp(board * clock.speed, 0, M.duration);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, view.w, view.h);
      if (!scene.bare && scene.paper) {
        ctx.drawImage(scene.paper, -80, -80, view.w + 160, view.h + 160);
      }
      if (modeName === "break") frameBreak(scene, at);
      else if (modeName === "hold") frameHold(scene, at);
      else frameLink(scene, at);
      scene.lastT = at;
    }

    return {
      draw: draw,
      mode: modeName,
      /* 排播层按 `duration`（**牌桌秒**）定时摘层——它就是 anim.ini 解算出来的那一个数。 */
      duration: clock.seconds,
      total: clock.total,
      timing: clock.timing,
      speed: clock.speed,
      width: view.w,
      height: view.h,
      geometry: scene.g,
      scene: scene,
      staticCanvas: function (o) { return bakeStatic(o || {}); }
    };
  }

  function bakeStatic(o) {
    o = o || {};
    var w = Number(o.w) > 0 ? Number(o.w) : CARD.w;
    var h = Number(o.h) > 0 ? Number(o.h) : w * CARD.h / CARD.w;
    var dpr = Number(o.dpr) > 0 ? Number(o.dpr) : 1;
    var overL = GEOM.overL, overR = GEOM.overR;
    var cw = w * (1 + overL + overR);
    var ch = h;
    var scene = buildScene({
      bare: true,
      dpr: dpr,
      view: { w: cw, h: ch },
      stage: { cx: overL * w + w / 2, cy: h / 2, w: w, h: h },
      seed: o.seed
    });
    var out = offscreen(cw * dpr, ch * dpr);
    var c = out[1];
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, cw, ch);

    /* 直接跑"常态"那一帧：末帧与常态贴图是同一段代码，不靠临摹 */
    scene.ctx = c;
    frameHold(scene, 0);

    out[0]._sgsChain = {
      cardW: w, cardH: h, dpr: dpr,
      canvasW: cw, canvasH: ch,
      overL: overL, overR: overR,
      entry: GEOM.entry, deg: GEOM.deg
    };
    return out[0];
  }


  /* ================================================================
   * 排播（住同一文件，与 `damage-fx.js` 同姿态）
   *
   * 与伤害层不同：**无时停**（用户口径）——不登记 `registerPlaybackHold`，演出与后续记录
   * 并行是预期。反过来，入链"演完才挂常态"、解锁"立刻摘常态再演"这两条次序必须由这里兜住，
   * 因为回放**每步都重绘全表**（`table.js:renderTable` 重建座位节点）。
   *
   * 钩子（调用方显式调，本文件不反向抓事件）：
   *   session.js:stepPlayback  → fireChainEventsThrough(record)   一条记录到点的判据
   *   table.js:renderTable     → chainFxSync()                    重贴常态 / 抑制态 + 版式重检
   *   session.js:commitView    → clearChainFx()                   换局清场
   * ============================================================== */

  /* 同时在播上限（spec §演出口径）：铁索连环一次最多横置 2 人，8 是留量；**超出丢队尾**。 */
  var CHAIN_FX_MAX = 8;
  /* 相邻开演错开（同批两条横置）：照 `damage-fx.js:DAMAGE_FX_STAGGER_MS` 的口径。 */
  var CHAIN_FX_STAGGER_MS = 120;
  /* 同座位两次变化的最小间隔：铁索连环再指定已横置的角色 = 解除，先收旧的再起新的。 */
  var CHAIN_FX_SAME_SEAT_MS = 200;
  /* 常态贴图缓存上限：每尺寸烘一次（window 缩放才换尺寸），留几份够用。 */
  var CHAIN_FX_STATIC_CACHE_MAX = 8;

  var chainQueue = [];
  var chainPlays = [];
  var chainStaggerTimer = null;
  var chainFrame = null;
  var chainNextStartAt = 0;
  var chainStartedCount = 0;
  var chainSyncBusy = false;
  /* `finishChainPlay` 在同步途中要求再同步一次（见 `chainFxSync` 的重入说明）。 */
  var chainSyncDirty = false;
  var chainStaticCache = [];

  function chainLayer() {
    if (typeof $ !== "function") return null;
    var layer = $("t-chain-fx");
    return layer && layer.classList ? layer : null;
  }

  /* 演出画布的分辨率上限：牌桌是 1920 设计单位撑满屏，1.5 倍已看不出锯齿（同伤害层）。 */
  function chainFxDpr() {
    var ratio = Number(window.devicePixelRatio);
    return isFinite(ratio) && ratio > 0 ? Math.min(1.5, ratio) : 1;
  }

  function chainSeatNodeOf(seat) {
    if (typeof seatNodeFor !== "function") return null;
    return seatNodeFor(seat);
  }

  /* 座位 → 常态画布：自己的座位是 `index.html` 的常驻节点 `#t-self-chain`（节点常驻、
   * 只切 `hidden`），对手的是 `seat-view.js:chainMarkNode()` 建在武将块里的 `.chain-mark`。 */
  function chainMarkOf(seat, block) {
    var self = state && state.view && state.view.snapshot ? state.view.snapshot.seat : null;
    if (self !== null && self !== undefined && Number(seat) === Number(self)) {
      if (typeof $ === "function") {
        var own = $("t-self-chain");
        if (own) return own;
      }
    }
    if (!block && typeof heroBlockFor === "function") block = heroBlockFor(seat);
    if (!block || typeof descendantWithClass !== "function") return null;
    return descendantWithClass(block, "chain-mark");
  }

  /* 演出期间座位上的读数 / 抑制态：`data-chain-fx="link" | "break"`。
   * CSS 只在 `link` 上藏 `.chain-mark`（"演完才挂"），`break` 只是给人读的读数。 */
  function chainSetSeatFlag(seat, mode) {
    var node = chainSeatNodeOf(seat);
    if (!node || !node.setAttribute) return null;
    if (mode) node.setAttribute("data-chain-fx", mode);
    else if (node.removeAttribute) node.removeAttribute("data-chain-fx");
    return node;
  }

  function chainSeatLive(seat) {
    for (var i = 0; i < chainPlays.length; i++) {
      if (Number(chainPlays[i].seat) === Number(seat)) return chainPlays[i];
    }
    return null;
  }

  /* 入链期间这条座位**不画**常态贴图（用户口径：常态 UI 不许挡住动画）。 */
  function chainLinkLive(seat) {
    for (var i = 0; i < chainPlays.length; i++) {
      var play = chainPlays[i];
      if (Number(play.seat) === Number(seat) && play.mode === "link") return play;
    }
    return null;
  }

  function chainFxMark(layer) {
    if (!layer || !layer.setAttribute) return;
    var live = chainPlays.length;
    layer.setAttribute("data-chain-fx-live", String(live));
    layer.setAttribute("data-chain-fx-plays", String(chainStartedCount));
    layer.setAttribute("data-chain-fx-mode", live ? chainPlays[live - 1].mode : "");
  }

  /* -------------------------------------------------- 常态贴图（`.chain-mark`） ---- */

  function chainBake(w, h, dpr) {
    var key = Math.round(w) + "x" + Math.round(h) + "@" + dpr;
    for (var i = 0; i < chainStaticCache.length; i++) {
      if (chainStaticCache[i].key === key) return chainStaticCache[i].canvas;
    }
    var baked = bakeStatic({ w: w, h: h, dpr: dpr });
    chainStaticCache.push({ key: key, canvas: baked });
    if (chainStaticCache.length > CHAIN_FX_STATIC_CACHE_MAX) chainStaticCache.shift();
    return baked;
  }

  /* 一张常态贴图**不画**（入链演出期间 / 换局 / 座位不再横置）：清零画布，读数也撤掉。 */
  function chainClearMark(mark) {
    if (!mark) return null;
    if (typeof mark.getContext === "function") {
      var ctx = mark.getContext("2d");
      if (ctx && ctx.clearRect) {
        if (ctx.setTransform) ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, Number(mark.width) || 0, Number(mark.height) || 0);
      }
    }
    /* 宽高清零：真浏览器里 0 尺寸画布**什么都不画**，读数也一眼看得出"没挂"。 */
    mark.width = 0;
    mark.height = 0;
    if (mark.removeAttribute) mark.removeAttribute("data-chain-mark");
    return mark;
  }

  /* 按武将块当前矩形（CSS px）把常态贴图画进画布：尺寸变了才重烘（缓存跨重绘复用）。 */
  function chainPaintMark(mark, block) {
    if (!mark || typeof mark.getContext !== "function" || typeof rectOf !== "function") return null;
    var rect = rectOf(block);
    var w = Math.round(Number(rect.width) || 0);
    var h = Math.round(Number(rect.height) || 0);
    if (!(w > 0) || !(h > 0)) return chainClearMark(mark);
    var dpr = chainFxDpr();
    var baked = chainBake(w, h, dpr);
    mark.width = baked.width;
    mark.height = baked.height;
    var ctx = mark.getContext("2d");
    if (ctx) {
      if (ctx.setTransform) ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (ctx.clearRect) ctx.clearRect(0, 0, mark.width, mark.height);
      if (ctx.drawImage) ctx.drawImage(baked, 0, 0);
    }
    if (mark.setAttribute) {
      mark.setAttribute("data-chain-mark", Math.round(w) + "x" + Math.round(h) + "@" + dpr);
    }
    return mark;
  }

  /* -------------------------------------------------- 一次演出 ---- */

  function chainBoxOf(block, layerRect) {
    var rect = rectOf(block);
    var left = Number(rect.left) || 0;
    var top = Number(rect.top) || 0;
    var w = Number(rect.width) || 0;
    var h = Number(rect.height) || 0;
    return {
      cx: left - (Number(layerRect.left) || 0) + w / 2,
      cy: top - (Number(layerRect.top) || 0) + h / 2,
      w: w,
      h: h
    };
  }

  function chainBoxKey(box) {
    return [box.cx, box.cy, box.w, box.h]
      .map(function (value) { return Math.round(Number(value) || 0); })
      .join(",");
  }

  /* 变一档时间轴的原始 ini 值给 `create()`：缺键不放进表（模块退默认），钳制在模块里。 */
  function chainTimingRaw(mode) {
    var raw = {};
    if (typeof animTiming !== "function") return raw;
    var key = timingKeyOf(mode, "total");
    var value = animTiming(key);
    if (value !== undefined && value !== null && isFinite(Number(value))) raw[key] = Number(value);
    return raw;
  }

  function startChainPlay(event) {
    var layer = chainLayer();
    if (!layer || typeof heroBlockFor !== "function" || typeof rectOf !== "function") return null;
    var seat = Number(event.seat);
    var block = heroBlockFor(seat);
    if (!block) return null;                    /* 量不到就不演（fail-open：状态仍然切了） */
    var layerRect = rectOf(layer);
    var box = chainBoxOf(block, layerRect);
    if (!(box.w > 0) || !(box.h > 0)) return null;

    var overL = box.w * GEOM.overL;
    var overR = box.w * GEOM.overR;
    var w = box.w + overL + overR;
    var dpr = chainFxDpr();

    var canvas = document.createElement("canvas");
    canvas.className = "chain-fx-canvas";
    /* 读数（烟测 / 实机配方都读它）：这一张是哪个座位、哪一条演进。 */
    if (canvas.setAttribute) {
      canvas.setAttribute("data-chain-seat", String(seat));
      canvas.setAttribute("data-chain-mode", event.mode);
    }
    canvas.style.cssText =
      "position:absolute;pointer-events:none;left:" + (box.cx - box.w / 2 - overL) +
      "px;top:" + (box.cy - box.h / 2) + "px;width:" + w + "px;height:" + box.h + "px";
    layer.appendChild(canvas);

    var fx = create(canvas, {
      mode: event.mode,
      dpr: dpr,
      /* 牌桌档：**只出铁索**——宣纸底与样例武将牌一概不画（否则一张米色纸板盖住牌桌）。 */
      bare: true,
      timing: chainTimingRaw(event.mode),
      view: { w: w, h: box.h },
      stage: { cx: overL + box.w / 2, cy: box.h / 2, w: box.w, h: box.h }
    });

    var play = {
      seq: event.seq,
      seat: seat,
      mode: event.mode,
      canvas: canvas,
      fx: fx,
      startedAt: stageNow(),
      duration: Math.max(0, Number(fx.duration) || 0) * 1000,
      boxKey: chainBoxKey(box),
      timer: null
    };
    chainPlays.push(play);
    chainStartedCount += 1;
    chainSetSeatFlag(seat, play.mode);
    /* 入链：演完才挂常态 ⇒ 先把这张画布清掉（座位上的 `.chain-mark` 由 CSS 一起藏掉）。 */
    if (play.mode === "link") chainClearMark(chainMarkOf(seat, block));
    if (typeof chainSfxPlay === "function") chainSfxPlay(play.mode);
    play.timer = window.setTimeout(function () { finishChainPlay(play); }, play.duration);
    chainFxMark(layer);
    chainRequestFrame();
    return play;
  }

  function finishChainPlay(play) {
    if (!play) return;
    var at = chainPlays.indexOf(play);
    if (at >= 0) chainPlays.splice(at, 1);
    if (play.timer !== null && play.timer !== undefined && window.clearTimeout) {
      window.clearTimeout(play.timer);
    }
    play.timer = null;
    if (typeof chainSfxStop === "function") chainSfxStop();
    var canvas = play.canvas;
    if (canvas && typeof canvas.getContext === "function") {
      var ctx = canvas.getContext("2d");
      if (ctx && ctx.clearRect) {
        if (ctx.setTransform) ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    }
    if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
    play.canvas = null;
    /* 这一座位还有别的演出在播 ⇒ 属性留给那一条；否则撤掉。 */
    if (!chainSeatLive(play.seat)) chainSetSeatFlag(play.seat, null);
    if (!chainPlays.length) chainStopFrame();
    chainFxMark(chainLayer());
    /* 演完才挂常态 / 收尾后重贴：重绘过无数次，贴图必须按**当前**矩形重画一次。 */
    chainFxSync();
  }

  function renderChainFrame(now) {
    if (!chainPlays.length) return;
    var at = now === undefined ? stageNow() : now;
    for (var i = 0; i < chainPlays.length; i++) {
      var play = chainPlays[i];
      var seconds = Math.min(
        Math.max(0, Number(at) - Number(play.startedAt)) / 1000,
        play.duration / 1000
      );
      if (play.fx && typeof play.fx.draw === "function") play.fx.draw(seconds);
    }
  }

  function chainRequestFrame() {
    if (chainFrame !== null) return;
    var raf = window.requestAnimationFrame;
    if (typeof raf !== "function") return;
    chainFrame = raf(function () {
      chainFrame = null;
      renderChainFrame();
      if (chainPlays.length) chainRequestFrame();
    });
  }

  function chainStopFrame() {
    if (chainFrame === null) return;
    if (window.cancelAnimationFrame) window.cancelAnimationFrame(chainFrame);
    chainFrame = null;
  }

  /* -------------------------------------------------- 排播闸门 ---- */

  function pumpChainQueue() {
    if (chainStaggerTimer !== null && chainStaggerTimer !== undefined) {
      if (window.clearTimeout) window.clearTimeout(chainStaggerTimer);
      chainStaggerTimer = null;
    }
    while (chainQueue.length) {
      if (chainPlays.length >= CHAIN_FX_MAX) break;          /* 在播满 ⇒ 剩下的丢队尾 */
      var wait = Math.round(chainNextStartAt - stageNow());
      if (wait > 0) {
        chainStaggerTimer = window.setTimeout(function () {
          chainStaggerTimer = null;
          /* 到点了：把闸门推到"现在"再重排——不重算等待，否则计时器早到 1~2ms 时会再排一次。 */
          chainNextStartAt = stageNow();
          pumpChainQueue();
        }, wait);
        break;
      }
      startChainPlay(chainQueue.shift());
      chainNextStartAt = stageNow() + CHAIN_FX_STAGGER_MS;
    }
  }

  /* 一条 `chained_change` → 排一次演出。同座位 200ms 内先收掉旧的（spec §演出口径）。 */
  function queueChainEvent(event) {
    if (!event || !isFinite(Number(event.seat))) return null;
    var seat = Number(event.seat);
    var now = stageNow();
    for (var i = chainPlays.length - 1; i >= 0; i--) {
      var play = chainPlays[i];
      if (Number(play.seat) !== seat) continue;
      if (now - Number(play.startedAt) < CHAIN_FX_SAME_SEAT_MS) finishChainPlay(play);
    }
    /* 在播上限：这条本身丢掉（不排队等）——攒着只会把旧铁索演在新画面上。 */
    if (chainPlays.length + chainQueue.length >= CHAIN_FX_MAX) return null;
    var item = { seat: seat, mode: event.chained ? "link" : "break", seq: event.seq };
    chainQueue.push(item);
    pumpChainQueue();
    return item;
  }

  /* 首批初始化 `state.chainSeq`：打开一局**已横置**的对局时只想挂常态，不补播入链。
   *
   * 游标取**这一批的最大 seq**（不是记录自己的）：首批里的所有 `chained_change` 都是历史。
   * 之后 `record.seq > state.chainSeq` 的才演。换局由 `clearChainFx()` 清回 `null`。 */
  function chainPrimeSeq(stateRef) {
    if (!stateRef || (stateRef.chainSeq !== null && stateRef.chainSeq !== undefined)) return;
    var playback = stateRef.playback;
    var records = playback && playback.records ? playback.records : null;
    var max = 0;
    if (records) {
      for (var i = 0; i < records.length; i++) {
        var seq = Number(records[i] && records[i].seq);
        if (isFinite(seq) && seq > max) max = seq;
      }
    }
    stateRef.chainSeq = max;
  }

  /* 回放走到某条记录：只认 `delta_type === "chained_change"`（`Engine.set_chained` 是唯一
   * 写入口，语义明确，前端不需要猜）。由 `session.js:stepPlayback` 在
   * `fireDamageEventsThrough(record.seq)` 的下一行调用。 */
  function fireChainEventsThrough(record) {
    if (typeof state === "undefined" || !state) return null;
    chainPrimeSeq(state);
    if (!record || record.kind !== "delta" || record.delta_type !== "chained_change") return null;
    var seq = Number(record.seq);
    if (!isFinite(seq)) return null;
    if (state.chainSeq !== null && state.chainSeq !== undefined && !(seq > Number(state.chainSeq))) {
      return null;                                   /* 首批历史 / 已演过的那一条：不补播 */
    }
    state.chainSeq = seq;
    return queueChainEvent({ seat: Number(record.seat), chained: Boolean(record.chained), seq: seq });
  }

  /* -------------------------------------------------- 重绘之后 ---- */

  function chainDropStalePlays() {
    if (!chainPlays.length) return;
    var layer = chainLayer();
    if (!layer) { clearChainFx(); return; }
    var layerRect = rectOf(layer);
    var stale = [];
    for (var i = 0; i < chainPlays.length; i++) {
      var play = chainPlays[i];
      var block = heroBlockFor(play.seat);
      if (!block) { stale.push(play); continue; }
      if (chainBoxKey(chainBoxOf(block, layerRect)) !== play.boxKey) stale.push(play);
    }
    for (var j = 0; j < stale.length; j++) finishChainPlay(stale[j]);
  }

  function chainFxSyncBody() {
    var layer = chainLayer();
    /* 只加载本文件的探针（静态用例的 node sandbox）里没有布局工具：什么都不做，不抛错。 */
    if (typeof heroBlockFor !== "function" || typeof rectOf !== "function") return;
    if (!layer) {
      if (chainPlays.length || chainQueue.length) clearChainFx();
      return;
    }
    var view = typeof tableViewForRender === "function" ? tableViewForRender() : null;
    var players = (view && view.snapshot && view.snapshot.players) || [];
    var selfSeat = view && view.snapshot ? view.snapshot.seat : null;
    var chained = {};
    for (var i = 0; i < players.length; i++) {
      var player = players[i];
      if (!player || !player.chained) continue;
      var seat = Number(player.seat);
      chained[seat] = true;
      var block = heroBlockFor(seat);
      if (!block) continue;
      var mark = chainMarkOf(seat, block);
      if (!mark) continue;
      /* 入链演出期间不挂常态（用户口径：常态 UI 不许挡住动画）；收尾那一帧补上。 */
      if (chainLinkLive(seat)) chainClearMark(mark);
      else chainPaintMark(mark, block);
    }
    if (selfSeat !== null && selfSeat !== undefined && !chained[Number(selfSeat)]) {
      if (typeof $ === "function") chainClearMark($("t-self-chain"));
    }
    /* 座位节点每次重绘都会重建 ⇒ 抑制态 / 读数重贴一遍。 */
    for (var p = 0; p < chainPlays.length; p++) {
      chainSetSeatFlag(chainPlays[p].seat, chainPlays[p].mode);
    }
    chainDropStalePlays();
    chainFxMark(layer);
  }

  /* `renderTable` 末尾调用（每次重绘都要，否则一步回放就把贴图 / 抑制态打回原点）。
   *
   * 重入：`chainDropStalePlays` 里的 `finishChainPlay` 会回头调本函数。不能直接 return——那
   * 一趟的顺序是「先画常态、后摘演出」，被摘掉的那一场的座位就**没挂上**常态（摘的那一下要求
   * 再同步一次）。这里记一个脏标记，在当前这趟收尾后**再跑一遍**（脏标记只由 finish 置起，
   * 摘完就没有新的了，不会转圈）。 */
  function chainFxSync() {
    if (chainSyncBusy) {
      chainSyncDirty = true;
      return;
    }
    chainSyncBusy = true;
    try {
      do {
        chainSyncDirty = false;
        chainFxSyncBody();
      } while (chainSyncDirty);
    } finally {
      chainSyncBusy = false;
    }
  }

  /* 换局清场：队列 / 画布 / 计时器 / 抑制态 / 常态贴图 / 声音一起收掉（`commitView` 调用）。 */
  function clearChainFx() {
    if (chainStaggerTimer !== null && chainStaggerTimer !== undefined) {
      if (window.clearTimeout) window.clearTimeout(chainStaggerTimer);
      chainStaggerTimer = null;
    }
    if (typeof chainSfxStop === "function") chainSfxStop();
    chainQueue.length = 0;
    for (var i = 0; i < chainPlays.length; i++) {
      var play = chainPlays[i];
      if (play.timer !== null && play.timer !== undefined && window.clearTimeout) {
        window.clearTimeout(play.timer);
      }
      play.timer = null;
      if (play.canvas && play.canvas.parentNode) play.canvas.parentNode.removeChild(play.canvas);
      play.canvas = null;
      chainSetSeatFlag(play.seat, null);
    }
    chainPlays.length = 0;
    chainStopFrame();
    var layer = chainLayer();
    if (layer) {
      while (layer.children && layer.children.length) layer.removeChild(layer.children[0]);
    }
    /* 常态贴图也清掉：上一局的横置不该在新桌上还挂着（节点本身常驻，只清画布）。 */
    var view = state && state.view;
    var players = (view && view.snapshot && view.snapshot.players) || [];
    for (var p = 0; p < players.length; p++) {
      var node = chainSeatNodeOf(players[p].seat);
      if (node && node.removeAttribute) node.removeAttribute("data-chain-fx");
    }
    if (typeof $ === "function") chainClearMark($("t-self-chain"));
    if (state) state.chainSeq = null;
    chainNextStartAt = 0;
    chainFxMark(layer);
  }

  /* ------------------------------------------------------------ 导出 ---- */
  global.SgsChainFX = {
    create: create,
    bakeStatic: bakeStatic,
    /* 双时间轴：字段名 / 内置默认 / 钳制的唯一真源（`anim.ini` 与它逐键同值）。 */
    gameTiming: gameTiming,
    DEFAULT_TIMING: DEFAULT_TIMING,
    TIMING_PREFIX: TIMING_PREFIX,
    TIMING_FIELDS: TIMING_FIELDS,
    TIMING_KEYS: TIMING_KEYS,
    MIN_TOTAL_MS: MIN_TOTAL_MS,
    MAX_TOTAL_MS: MAX_TOTAL_MS,
    MODES: MODES,
    GEOM: GEOM,
    COLORS: C,
    STATIC: { overL: GEOM.overL, overR: GEOM.overR, entry: GEOM.entry, deg: GEOM.deg, cardRatio: CARD.h / CARD.w },
    /* 排播口径（烟测 / 实机读数用；改口径只改上面那三个常量）。 */
    MAX_PLAYS: CHAIN_FX_MAX,
    STAGGER_MS: CHAIN_FX_STAGGER_MS,
    SAME_SEAT_MS: CHAIN_FX_SAME_SEAT_MS,
    /* 音效读数：同一份时间表 */
    cues: function (mode) {
      var M = MODES[mode] || MODES.link;
      return M;
    },
    /* 三个钩子（调用方显式调）：一条记录到点 / 重绘之后 / 换局清场。 */
    fireChainEventsThrough: fireChainEventsThrough,
    chainFxSync: chainFxSync,
    clearChainFx: clearChainFx,
    chainPlays: function () { return chainPlays.slice(); }
  };

})(window);

/* 三个排播钩子挂成**全局名**（与 `damage-fx.js` 的 `fireDamageEventsThrough` /
 * `damageDropStalePlays` / `clearDamageFx` 同姿态）：调用方是 `session.js` 的 `stepPlayback` /
 * `commitView` 与 `table.js` 的 `renderTable`，它们直接叫名字。内部状态（队列 / 在播表 /
 * 缓存）仍住上面那个闭包里——这三行只是同一批函数的转手壳，**不**另存一份状态。
 *
 * 为什么在闭包外写成顶层 `var`：普通脚本的顶层 `var` 就是全局绑定（浏览器 = `window`）；
 * 写成 `window.xxx = …` 在垫片（`window` 是另一个对象）里叫不到，两边的加载方式必须一致。 */
var fireChainEventsThrough = function (record) {
  return window.SgsChainFX.fireChainEventsThrough(record);
};
var chainFxSync = function () {
  return window.SgsChainFX.chainFxSync();
};
var clearChainFx = function () {
  return window.SgsChainFX.clearChainFx();
};
