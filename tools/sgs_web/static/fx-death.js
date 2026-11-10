/* SGS 前端 · 「阵亡」的**画面 + 排播**（chain-death-voices 票 02）：泛白 → 裂 → 停一下 →
 * 碎 → 落印。画面部分是素材包 `outputs/sgs-death-fx/fx-death.js` 的移植（纯 Canvas 2D、
 * 零依赖、零请求、画面只由秒数决定），本文件在它之上加了三件事：
 *
 *   1. **牌桌档时间表**（同 `fx-core.js` / `fx-chain.js` 的姿态）：素材页的
 *      `PARAMS.duration = 3.05` 只作**基准**；牌桌档读 `anim.ini` 的 `death_fx_total`
 *      （缺键退 `DEFAULT_TIMING`，钳制 **1200–3200ms**，缺省 **2200**）。`draw(t)` 仍吃
 *      **演出自己的秒数**，内部按 `k = total / 3050` 映射回素材秒 ⇒ 各节拍（`waves` /
 *      `holdAt` / `burstAt` / `labelAt`）等比缩放，`hold → burst` 的停顿因此约 **0.25s**
 *      （素材包 §8 的 0.2s 下限仍守）。
 *   2. **排播**（与 `damage-fx.js` / `fx-chain.js` 同姿态）：`fireDeathEventsThrough(seq)`
 *      由 `session.js:stepPlayback` 在 `fireDamageEventsThrough(record.seq)` 之后调；
 *      判据只看**记录**（`kind === "timing"` ∧ `timing === "DEATH_BEFORE_START"`，座位 =
 *      `record.actor`），**绝不用 `hp_change` 到 0 去猜**；在播上限 **2**、第 3 条丢队尾
 *      （不排队等），每条开演前重测 `stage`，到 `duration` 摘层。
 *   3. **三个反向钩子**（照素材包 §6）：`deathFxSync()`（`table.js:renderTable` 末尾重贴
 *      `data-death-fx` + 比矩形）、`clearDeathFx()`（`session.js:commitView` 换局清场）、
 *      回放页不播（与技能 / 伤害演出同一条边界）。
 *
 * **与素材包建议相反的两处（用户口径，逐条记在这里）**：
 *
 *   1. **无时停**：素材包 §3 建议 `registerPlaybackHold("death-fx", …)` 冻住记录流；用户要求
 *      阵亡「均无时停」⇒ 本文件**不登记**。2.2s 里记录流照常推进、后续事件会画在"正在碎的
 *      牌"旁边——这是预期，不是漏接（spec §已知缺口 4）。
 *   2. **自己的覆盖层**：不往 `#t-damage-fx` 里塞画布（那条层有"只有一张 canvas"的正向断言），
 *      本文件的每一条演出各挂一张 canvas 到 `#t-death-fx`。
 *
 * 依赖方向单向：只借 `effects.js` 的 `rectOf` / `heroBlockFor`、`damage-fx.js` 的
 * `damageFxBoxOf` / `damageFxDpr` 口径（缺件时退回本文件的小实现）、`session.js` 的
 * `stageNow` / `state`，以及音效那侧的 `deathSfxPlay` / `deathSfxStop` / `deathSfxMute`
 * （`sfx-ui.js`，单向缝）——全部在**调用时**解析。
 */
"use strict";

(function (global) {
  var TAU = Math.PI * 2;

  /* ---------------------------------------------------------------- 配色 ----
   * 纸 / 墨 / 朱砂取自 SGS 前端 style.css 的 :root；"镜"另加一组冷白：
   * 霜（冷白偏蓝）→ 月白 → 白热。冷白压在暖纸上，白才看得出是"光"而不是"纸"。 */
  var C = {
    paper:  "#f1ebdf",
    card:   "#fffdf7",
    cardLo: "#f8f1e2",
    line:   "#ddd8d0",
    ink:    "#1c1a17",
    dim:    "#6f6a62",
    red:    "#9c2b1b",
    wei:    "#2f5d8a",
    frost:  "#cfe2f2",     /* 最外一层冷光 */
    moon:   "#e6f0fa",     /* 月白：光晕主体 */
    hot:    "#ffffff"
  };

  var SERIF = '"Noto Serif SC","Songti SC","STSong","SimSun",serif';
  var SANS  = '"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif';

  var W = 1920, H = 1080;
  var CARD = { w: 460, h: 644, pad: 74 };

  /* 单张牌居中：阵亡是一张牌的事，不需要攻防两张。
   * 全屏高光 / 碎片都围这张牌算，位置改这里就够。 */
  var SCALE = 1.06;
  var DST = { cx: 960, cy: 520, w: CARD.w * SCALE, h: CARD.h * SCALE };

  var GRAV = 1500;          /* 碎片下坠（px/s²） */

  var HERO = {
    name: "郭嘉", faction: "魏", factionColor: C.wei, hp: 3,
    skills: [
      ["天妒", "当你的判定牌生效后，你可以获得此牌。"],
      ["遗计", "当你受到1点伤害后，你可以观看牌堆顶的两张牌，然后交给任意角色。"]
    ]
  };

  /* ================================================================
   * 时间表（音效读同一份，不另抄一份）
   *   wakeAt    牌心开始渗光，牌边亮起一圈冷边
   *   waves     三波裂缝的生长起点（每道裂缝在这一波里再错开 0~0.06s）
   *   holdAt    白到顶，**停住**——画面完全静止，只留一点呼吸的亮度
   *   burstAt   瞬间碎裂
   *   labelAt   朱砂印落下
   *   washMax   牌面被白光吃掉多少（1 = 全白）
   * ============================================================== */
  var PARAMS = {
    name: "阵亡", tag: "一名武将阵亡 · 泛白、裂开、停顿、碎满一桌",
    duration: 3.05,
    wakeAt: 0.02, waves: [0.16, 0.52, 0.98],
    holdAt: 1.40, burstAt: 1.74, labelAt: 2.26,
    washMax: 0.90,
    /* 裂法：58 个 Voronoi 种子 + 12 个贴边的（贴边的切出细长崩边） */
    cells: 58, edge: 12,
    shardSpin: 6.0, shardFly: 1.00,
    dust: 88, flakes: 12,
    shake: 2.6, flash: 0.72,
    label: "阵亡", labelSize: 138
  };

  /* ================================================================
   * 牌桌档时间表（同 `fx-core.js` / `fx-chain.js` 的双时间轴姿态）
   *
   * `DEFAULT_TIMING` 是字段名与内置默认的**唯一真源**：`TIMING_FIELDS` / `TIMING_KEYS`
   * 都由它生成，磁盘上的 `anim.ini` 与它逐键同值（静态用例钉）。
   * ============================================================== */
  var TIMING_PREFIX = "death_fx_";
  var DEFAULT_TIMING = { total: 2200 };
  /* 钳制范围（spec §演出口径·牌桌档缺省）：比 1.2s 短看不清"泛白 → 裂 → 停一下 → 碎"，
   * 比 3.2s 长会挡在牌桌上（阵亡**没有时停**，它必须自己管住窗口）。 */
  var MIN_TOTAL_MS = 1200;
  var MAX_TOTAL_MS = 3200;

  function timingKeyOf(field) {
    return TIMING_PREFIX + field;
  }

  var TIMING_FIELDS = Object.freeze((function () {
    var fields = [];
    for (var field in DEFAULT_TIMING) {
      if (Object.prototype.hasOwnProperty.call(DEFAULT_TIMING, field)) fields.push(field);
    }
    return fields;
  })());

  var TIMING_KEYS = Object.freeze((function () {
    var keys = [];
    for (var i = 0; i < TIMING_FIELDS.length; i++) keys.push(timingKeyOf(TIMING_FIELDS[i]));
    return keys;
  })());

  /* 从 `raw`（= 排播层搬来的 anim.ini 键值）取一个毫秒数：不是有限非负数就退回内置默认。 */
  function millisOf(raw, key, fallback) {
    if (raw && Object.prototype.hasOwnProperty.call(raw, key)) {
      var value = Number(raw[key]);
      if (isFinite(value) && value >= 0) return value;
    }
    return fallback;
  }

  /* `game` 档：把 `anim.ini` 的 `death_fx_total` 解成**牌桌秒** + 映射倍率。
   *
   * `speed = 素材页基准秒数 / 牌桌秒数`：`draw(t)` 每帧读 `t * speed` 当素材秒，于是整条演出
   * 等比压 / 拉开（= 票面的 `k = total / 3050` 乘在各节拍上），末帧精确落回素材页的末帧。
   * 钳制只在这里做一次——画面模块是时间轴语义的拥有者。 */
  function gameTiming(raw) {
    var total = clamp(
      millisOf(raw, timingKeyOf("total"), DEFAULT_TIMING.total), MIN_TOTAL_MS, MAX_TOTAL_MS
    );
    var seconds = total / 1000;
    var basis = PARAMS.duration;
    var timing = {};
    timing[timingKeyOf("total")] = total;
    return {
      total: total,
      seconds: seconds,
      basis: basis,
      speed: basis / seconds,
      timing: timing
    };
  }

  /* ------------------------------------------------------------ 小工具 ---- */
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function lerp(a, b, t) { return a + (b - a) * t; }
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

  function pathCard(c, rect, radius) {
    pathRound(c, rect.x, rect.y, rect.w, rect.h, radius);
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

  /* 凸多边形向内缩 d：每条边推进一个半平面。Voronoi 的块一定是凸的，够用 */
  function inPoly(pts, d) {
    var cxy = polyCentroid(pts);
    var out = pts;
    for (var i = 0; i < pts.length; i++) {
      var a = pts[i], b = pts[(i + 1) % pts.length];
      var nx = b.y - a.y, ny = -(b.x - a.x);
      var len = Math.hypot(nx, ny) || 1;
      nx /= len; ny /= len;
      /* 法线朝**外**：clipPlane 保留 n·x <= d 的那一侧，内缩就是把 d 往里推 */
      if (nx * (cxy.x - a.x) + ny * (cxy.y - a.y) > 0) { nx = -nx; ny = -ny; }
      out = clipPlane(out, nx, ny, nx * a.x + ny * a.y - d);
      if (out.length < 3) return [];
    }
    return out;
  }

  function polyArea(pts) {
    var a = 0;
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i], q = pts[(i + 1) % pts.length];
      a += p.x * q.y - q.x * p.y;
    }
    return Math.abs(a) / 2;
  }

  function polyBounds(pts) {
    var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (var i = 0; i < pts.length; i++) {
      x0 = Math.min(x0, pts[i].x); x1 = Math.max(x1, pts[i].x);
      y0 = Math.min(y0, pts[i].y); y1 = Math.max(y1, pts[i].y);
    }
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }

  function polyCentroid(pts) {
    var a = 0, cx = 0, cy = 0;
    for (var i = 0; i < pts.length; i++) {
      var p = pts[i], q = pts[(i + 1) % pts.length];
      var f = p.x * q.y - q.x * p.y;
      a += f; cx += (p.x + q.x) * f; cy += (p.y + q.y) * f;
    }
    if (Math.abs(a) < 1e-6) {
      var b = polyBounds(pts);
      return { x: b.x + b.w / 2, y: b.y + b.h / 2 };
    }
    a *= 0.5;
    return { x: cx / (6 * a), y: cy / (6 * a) };
  }

  /* ================================================================
   * 宣纸（一次烘焙）——和火焰 / 雷电 / 回复 / 失去体力版同一张纸
   * ============================================================== */
  function paintPaper(S, view) {
    var w = view.w + 160, h = view.h + 160;
    var pc = offscreen(w * S, h * S), c = pc[1];
    c.scale(S, S);
    var r = rng(20261201);

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

    /* 一圈极淡的枯笔圆相 */
    var cx = view.w / 2 + 6, cy = view.h / 2 + 22;
    for (var k = 0; k < 48; k++) {
      if (r() < 0.18) continue;
      var a0 = k / 48 * TAU;
      var a1 = (k + 1) / 48 * TAU;
      var rr = 458 * (0.96 + r() * 0.05);
      c.strokeStyle = "rgba(28,26,23," + (0.007 + r() * 0.008) + ")";
      c.lineWidth = 8 + r() * 15;
      c.beginPath();
      c.moveTo(cx + Math.cos(a0) * rr, cy + Math.sin(a0) * rr);
      c.lineTo(cx + Math.cos(a1) * rr * 1.002, cy + Math.sin(a1) * rr * 1.002);
      c.stroke();
    }

    /* 几团宿墨的底子 */
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

    /* 四角压暗：白光才立得出来，比失去体力版再深一点 */
    var vg = c.createRadialGradient(w * 0.5, h * 0.47, h * 0.24, w * 0.5, h * 0.5, h * 1.04);
    vg.addColorStop(0, "rgba(66,48,30,0)");
    vg.addColorStop(1, "rgba(66,48,30,0.24)");
    c.fillStyle = vg;
    c.fillRect(0, 0, w, h);

    return pc[0];
  }

  /* ================================================================
   * 武将牌（和伤害 / 雷电 / 回复 / 失去体力版同一张牌）
   * ============================================================== */
  function paintHero(hero, S, withShadow) {
    var w = CARD.w, h = CARD.h, pad = 24;
    var cc = offscreen((w + CARD.pad * 2) * S, (h + CARD.pad * 2) * S), c = cc[1];
    c.scale(S, S);
    var r = rng(hero.name.charCodeAt(0) * 97 + 11);
    c.translate(CARD.pad, CARD.pad);

    if (withShadow) {
      c.save();
      c.shadowColor = "rgba(43,36,28,0.22)";
      c.shadowBlur = 26;
      c.shadowOffsetY = 12;
      pathRound(c, 0, 0, w, h, 14);
      c.fillStyle = C.card;
      c.fill();
      c.restore();
    }

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
   * 牌面贴图 → 场景坐标
   *   样例牌带一圈 pad（阴影），bare 模式没有牌面，用一块近白的"玻璃板"
   *   顶替——碎片翻出来是白玻璃，不再是牌面（牌桌的字读不到，也不该假装读到）。
   * ============================================================== */
  function bakeCardTexture(heroImg, S, rect, bare) {
    var scale = rect.w / CARD.w;
    if (!bare && heroImg) {
      return {
        img: heroImg,
        x: rect.x - CARD.pad * scale,
        y: rect.y - CARD.pad * scale,
        w: rect.w + CARD.pad * 2 * scale,
        h: rect.h + CARD.pad * 2 * scale
      };
    }
    var tex = offscreen(rect.w * S, rect.h * S), c = tex[1];
    c.scale(S, S);
    var g = c.createLinearGradient(0, 0, rect.w * 0.4, rect.h);
    g.addColorStop(0, "#fdfbf6");
    g.addColorStop(0.55, "#f6f3ec");
    g.addColorStop(1, "#efece4");
    c.fillStyle = g;
    c.fillRect(0, 0, rect.w, rect.h);
    var r = rng(5511);
    for (var i = 0; i < 900; i++) {
      c.fillStyle = "rgba(255,255,255," + (0.03 + r() * 0.05) + ")";
      c.fillRect(r() * rect.w, r() * rect.h, 1.4, 1.4);
    }
    return { img: tex[0], x: rect.x, y: rect.y, w: rect.w, h: rect.h };
  }

  /* 碎裂那一瞬牌已经白到顶：碎片贴图从**白到位的牌**上切下来，
   * 否则碎片一飞出来就"变回原色"，白的那一段等于白演了。 */
  function bakeLitCard(tex, S, rect, radius, wash) {
    var cv = offscreen(tex.w * S, tex.h * S), c = cv[1];
    c.scale(S, S);
    c.drawImage(tex.img, 0, 0, tex.w, tex.h);

    c.save();
    pathCard(c, { x: rect.x - tex.x, y: rect.y - tex.y, w: rect.w, h: rect.h }, radius);
    c.clip();
    c.fillStyle = "rgba(255,255,255," + wash + ")";
    c.fillRect(0, 0, tex.w, tex.h);
    /* 牌心更白一点：光是从那里渗出来的 */
    var g = c.createRadialGradient(
      rect.x - tex.x + rect.w * 0.5, rect.y - tex.y + rect.h * 0.40, 0,
      rect.x - tex.x + rect.w * 0.5, rect.y - tex.y + rect.h * 0.40, rect.w * 0.85);
    g.addColorStop(0, "rgba(255,255,255," + (0.55 * wash) + ")");
    g.addColorStop(0.55, "rgba(255,255,255," + (0.18 * wash) + ")");
    g.addColorStop(1, "rgba(255,255,255,0)");
    c.fillStyle = g;
    c.fillRect(0, 0, tex.w, tex.h);
    c.restore();

    return { img: cv[0], x: tex.x, y: tex.y, w: tex.w, h: tex.h };
  }

  /* ================================================================
   * 朱砂印：纸上的红，用 multiply 落下去，纸纤维会透出来
   *   不加白描边（浅色纸上像贴纸），先拿纸色柔光托一层再压字
   * ============================================================== */
  function bakeStamp(txt, px) {
    var S = 2;
    var font = "700 " + px + "px " + SERIF;
    var mc = bakeStamp._mc;
    if (!mc) { mc = bakeStamp._mc = document.createElement("canvas").getContext("2d"); }
    mc.font = font;
    var tw = mc.measureText(txt).width;

    var padX = px * 0.30, padY = px * 0.34;
    var w = tw + padX * 2, h = px * 1.16 + padY * 2;
    var cv = offscreen(w * S, h * S), c = cv[1];
    c.scale(S, S);
    c.translate(w / 2, h / 2);
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.lineJoin = "round";
    c.font = font;

    /* 纸色柔光：把字从底下托起来，看不出边 */
    var blur = px * 0.05;
    if (typeof c.filter === "string") {
      c.filter = "blur(" + blur.toFixed(1) + "px)";
      c.fillStyle = "rgba(246,241,231,0.92)";
      c.fillText(txt, 0, 0);
      c.fillText(txt, 0, 0);
      c.filter = "none";
    }

    /* 字身：朱砂，上深下略浅（印泥越压越薄） */
    var g = c.createLinearGradient(0, -px * 0.56, 0, px * 0.56);
    g.addColorStop(0, "#7d2014");
    g.addColorStop(0.35, "#9c2b1b");
    g.addColorStop(0.75, "#a5341f");
    g.addColorStop(1, "#8f2a19");
    c.fillStyle = g;
    c.fillText(txt, 0, 0);

    /* 印泥不匀：挖掉一些极小的点，再补几道飞白 */
    c.globalCompositeOperation = "destination-out";
    var r = rng(3301 + Math.round(px) + txt.length);
    for (var i = 0; i < 90; i++) {
      var ax = (r() - 0.5) * w * 0.94;
      var ay = (r() - 0.5) * h * 0.86;
      c.fillStyle = "rgba(0,0,0," + (0.18 + r() * 0.5) + ")";
      c.beginPath();
      c.arc(ax, ay, px * (0.004 + r() * 0.014), 0, TAU);
      c.fill();
    }
    for (var k = 0; k < 5; k++) {
      var yy = (r() - 0.5) * px * 0.9;
      c.strokeStyle = "rgba(0,0,0," + (0.10 + r() * 0.18) + ")";
      c.lineWidth = px * (0.003 + r() * 0.005);
      c.beginPath();
      c.moveTo(-tw * 0.56, yy - px * 0.06);
      c.lineTo(tw * 0.56, yy + px * 0.07);
      c.stroke();
    }
    c.globalCompositeOperation = "source-over";

    return { img: cv[0], w: w, h: h };
  }

  /* ================================================================
   * 光晕贴图：一圈带噪点的软光
   *   每一帧现画 radialGradient 会在浅色纸上一圈一圈地"起台阶"（8bit 色阶），
   *   所以把光**烘一张**、里面拌上噪点，每帧只 drawImage —— 没有台阶，也快。
   * ============================================================== */
  function bakeGlow(size, seed, cool) {
    var cv = offscreen(size, size), c = cv[1];
    var r = rng(seed);
    var cx = size / 2, cy = size / 2, R = size / 2;
    var g = c.createRadialGradient(cx, cy, 0, cx, cy, R);
    var core = cool ? "255,255,255" : "255,255,255";
    var mid = cool ? "206,229,248" : "238,246,255";
    var edge = cool ? "188,218,246" : "226,240,255";
    g.addColorStop(0, "rgba(" + core + ",1)");
    g.addColorStop(0.30, "rgba(" + mid + ",0.62)");
    g.addColorStop(0.62, "rgba(" + edge + ",0.22)");
    g.addColorStop(1, "rgba(" + edge + ",0)");
    c.fillStyle = g;
    c.fillRect(0, 0, size, size);
    /* 噪点：dither，把色阶打断 */
    for (var i = 0; i < size * size / 46; i++) {
      var x = r() * size, y = r() * size;
      var dx = (x - cx) / R, dy = (y - cy) / R;
      var fall = 1 - Math.min(1, dx * dx + dy * dy);
      if (fall <= 0) continue;
      c.fillStyle = "rgba(255,255,255," + (r() * 0.075 * fall) + ")";
      c.fillRect(x, y, 1.2, 1.2);
    }
    return cv[0];
  }

  /* ================================================================
   * 裂法：整张牌切成 Voronoi 碎片
   *
   * 裂缝不是"画上去的线"，它就是碎片之间的公共边——所以碎的那一瞬间，牌沿着
   * 这些边散开，不需要另画一套碎片再对位。种子点越靠近受力点越密（锤一下玻璃
   * 就是这样），再沿牌边撒一圈贴边的点，切出一圈细长的崩边。
   * ============================================================== */
  function clipPlane(poly, nx, ny, d) {
    var out = [];
    for (var i = 0; i < poly.length; i++) {
      var a = poly[i], b = poly[(i + 1) % poly.length];
      var da = nx * a.x + ny * a.y - d;
      var db = nx * b.x + ny * b.y - d;
      if (da <= 0) out.push(a);
      if ((da <= 0) !== (db <= 0)) {
        var t = da / (da - db);
        out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
      }
    }
    return out;
  }

  function buildFracture(rect, seed, cfg, k) {
    var r = rng(seed);
    var focus = { x: rect.x + rect.w * 0.5, y: rect.y + rect.h * 0.40 };
    var diag = Math.hypot(rect.w, rect.h);

    /* ---- 种子点：绕受力点一圈圈排（内密外疏，外圈自然被拉长成放射状碎片），
     *      再沿牌边撒一圈贴边的点，切出一圈细长的崩边 ---- */
    var seeds = [];
    var R = diag * 0.52;
    /* 三层、每层角度数递增：径向间隔远大于角向间隔，切出来的碎片就是**拉长的放射片**，
     * 而不是一块块鹅卵石 */
    var layers = [
      [0.20, 5], [0.50, 11], [1.00, 18]
    ];
    var scale = cfg.cells / 34;
    var i;
    for (var L = 0; L < layers.length; L++) {
      var fr = layers[L][0];
      var cnt = Math.max(2, Math.round(layers[L][1] * scale));
      var off = r() * TAU;
      for (var q = 0; q < cnt; q++) {
        var ang = off + (q + (r() - 0.5) * 0.55) / cnt * TAU;
        var rad = R * fr * (0.86 + r() * 0.28);
        seeds.push({ x: focus.x + Math.cos(ang) * rad, y: focus.y + Math.sin(ang) * rad * 0.9 });
      }
    }
    var edgeSeeds = cfg.edge || 0;
    for (var e = 0; e < edgeSeeds; e++) {
      var side = Math.floor(r() * 4), t = 0.03 + r() * 0.94;
      if (side === 0) seeds.push({ x: rect.x + t * rect.w, y: rect.y - 3 });
      else if (side === 1) seeds.push({ x: rect.x + rect.w + 3, y: rect.y + t * rect.h });
      else if (side === 2) seeds.push({ x: rect.x + t * rect.w, y: rect.y + rect.h + 3 });
      else seeds.push({ x: rect.x - 3, y: rect.y + t * rect.h });
    }

    /* ---- Voronoi：每颗种子拿半平面把别人压掉 ---- */
    var rectPoly = [
      { x: rect.x, y: rect.y }, { x: rect.x + rect.w, y: rect.y },
      { x: rect.x + rect.w, y: rect.y + rect.h }, { x: rect.x, y: rect.y + rect.h }
    ];
    var cells = [];
    for (i = 0; i < seeds.length; i++) {
      var poly = rectPoly.slice();
      var s = seeds[i];
      for (var j = 0; j < seeds.length && poly.length >= 3; j++) {
        if (j === i) continue;
        var o = seeds[j];
        var nx = o.x - s.x, ny = o.y - s.y;
        var mx = (s.x + o.x) * 0.5, my = (s.y + o.y) * 0.5;
        poly = clipPlane(poly, nx, ny, nx * mx + ny * my);
      }
      if (poly.length < 3) continue;
      var area = polyArea(poly);
      if (area < 70 * k * k) continue;
      cells.push({ poly: poly, area: area, bbox: polyBounds(poly), centroid: polyCentroid(poly) });
    }

    function keyOf(p, q) {
      var ax = Math.round(p.x * 2), ay = Math.round(p.y * 2);
      var bx = Math.round(q.x * 2), by = Math.round(q.y * 2);
      return (ax < bx || (ax === bx && ay <= by))
        ? ax + "_" + ay + "_" + bx + "_" + by
        : bx + "_" + by + "_" + ax + "_" + ay;
    }

    /* ---- 公共边：中间往外顶一个折点。
     * 纯 Voronoi 的块太像鹅卵石，玻璃碴是带锐角的；折点两边共用，网还是密的。 */
    var mids = {};
    for (i = 0; i < cells.length; i++) {
      var ppm = cells[i].poly;
      for (var vm = 0; vm < ppm.length; vm++) {
        var am = ppm[vm], bm = ppm[(vm + 1) % ppm.length];
        var km = keyOf(am, bm);
        if (mids[km]) continue;
        var dxm = bm.x - am.x, dym = bm.y - am.y;
        var lm = Math.hypot(dxm, dym) || 1;
        var bend = (r() - 0.5) * Math.min(lm * 0.30, 26 * k) * 2;
        mids[km] = {
          x: (am.x + bm.x) * 0.5 - dym / lm * bend,
          y: (am.y + bm.y) * 0.5 + dxm / lm * bend
        };
      }
    }
    for (i = 0; i < cells.length; i++) {
      var cell = cells[i], np = [];
      for (var vv = 0; vv < cell.poly.length; vv++) {
        var va = cell.poly[vv], vb = cell.poly[(vv + 1) % cell.poly.length];
        np.push(va);
        var md = mids[keyOf(va, vb)];
        if (md) np.push(md);
      }
      cell.poly = np;
      cell.bbox = polyBounds(np);
      cell.centroid = polyCentroid(np);
      cell.area = polyArea(np);
    }

    /* ---- 公共边去重：每条边就是一道裂缝 ---- */
    var seen = {}, cracks = [];
    /* 贴着牌边的那几条"裂缝"是裁出来的边，不是裂——画上去只会在牌沿上叠一道，
     * 而且它们最长，会把"先跑长裂"的排序全带偏。直接不收。 */
    function sameBorder(p, q) {
      var e = 0.7;
      if (Math.abs(p.x - rect.x) < e && Math.abs(q.x - rect.x) < e) return true;
      if (Math.abs(p.x - (rect.x + rect.w)) < e && Math.abs(q.x - (rect.x + rect.w)) < e) return true;
      if (Math.abs(p.y - rect.y) < e && Math.abs(q.y - rect.y) < e) return true;
      if (Math.abs(p.y - (rect.y + rect.h)) < e && Math.abs(q.y - (rect.y + rect.h)) < e) return true;
      return false;
    }
    for (i = 0; i < cells.length; i++) {
      var cp = cells[i].poly;
      for (var v = 0; v < cp.length; v++) {
        var p1 = cp[v], p2 = cp[(v + 1) % cp.length];
        if (Math.hypot(p2.x - p1.x, p2.y - p1.y) < 1.2) continue;
        if (sameBorder(p1, p2)) continue;
        var kk = keyOf(p1, p2);
        if (seen[kk]) continue;
        seen[kk] = 1;
        /* 从靠近受力点的那一头往外长 */
        var d1 = Math.hypot(p1.x - focus.x, p1.y - focus.y);
        var d2 = Math.hypot(p2.x - focus.x, p2.y - focus.y);
        var md2 = mids[kk] || { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
        cracks.push(d1 <= d2
          ? { a: p1, m: md2, b: p2, near: d1, l1: Math.hypot(md2.x - p1.x, md2.y - p1.y), l2: Math.hypot(p2.x - md2.x, p2.y - md2.y) }
          : { a: p2, m: md2, b: p1, near: d2, l1: Math.hypot(md2.x - p2.x, md2.y - p2.y), l2: Math.hypot(p1.x - md2.x, p1.y - md2.y) });
      }
    }
    var maxNear = 1;
    for (i = 0; i < cracks.length; i++) {
      var ck = cracks[i];
      ck.len = ck.l1 + ck.l2;
      if (ck.near > maxNear) maxNear = ck.near;
    }

    return { focus: focus, shards: cells, cracks: cracks, coreR: 13 * k, maxNear: maxNear };
  }

  /* ================================================================
   * 创建
   * ============================================================== */

  /* 画布尺寸：牌桌档传 `view: {w, h}`（= `#t-death-fx` 的 CSS 尺寸，内部 1:1 合成）；
   * 素材页不传 ⇒ 1920×1080。改尺寸会清空画布，故由调用方在新建时给一次。 */
  function viewSize(view) {
    var w = Number(view && view.w) > 0 ? Number(view.w) : W;
    var h = Number(view && view.h) > 0 ? Number(view.h) : H;
    return { w: Math.max(1, Math.round(w)), h: Math.max(1, Math.round(h)) };
  }

  function create(canvas, opts) {
    opts = opts || {};
    var V = {};
    for (var key in PARAMS) {
      if (Object.prototype.hasOwnProperty.call(PARAMS, key)) V[key] = PARAMS[key];
    }
    V.label = opts.label !== undefined ? String(opts.label) : PARAMS.label;

    var bare = !!opts.bare;
    /* 时间轴：`opts.timing` = anim.ini 的原始键值（排播层搬来的那一份）。`clock.seconds` 是
     * **牌桌秒**，也是 `draw(t)` 的入参上限；`clock.speed` 把牌桌秒映射回素材秒。 */
    var clock = gameTiming(opts.timing);
    /* 画布尺寸 = 覆盖层的 CSS 尺寸（牌桌档按它建、内部 1:1 合成）；素材页缺省 1920×1080。 */
    var view = viewSize(opts.view);
    var vw = view.w, vh = view.h;
    var geom = opts.stage || {};
    var cx = Number(geom.cx) > 0 ? Number(geom.cx) : DST.cx;
    var cy = Number(geom.cy) > 0 ? Number(geom.cy) : DST.cy;
    var gw = Number(geom.w) > 0 ? Number(geom.w) : DST.w;
    var gh = Number(geom.h) > 0 ? Number(geom.h) : DST.h;
    /* 线宽 / 尘 / 印的字号都按**面积比**缩放：牌桌档传进来的武将块可能不是 3:2.3，
     * 按宽算横的块会显得线特别细。样例牌这一项恒为 1。 */
    var k = Math.sqrt((gw * gh) / (DST.w * DST.h));
    /* `radius: 0` 是合法值（牌桌的武将块是直角）：显式给 0 就用 0，缺省才按牌宽换算。 */
    var radius = opts.radius !== undefined && Number(opts.radius) >= 0
      ? Number(opts.radius)
      : 14 * (gw / CARD.w);

    var dpr = Number(opts.dpr) > 0 ? Number(opts.dpr) : 1;
    canvas.width = Math.round(vw * dpr);
    canvas.height = Math.round(vh * dpr);
    var c = canvas.getContext("2d");

    /* -------------------------------------------------- 几何 ---- */
    var cr = { x: cx - gw / 2, y: cy - gh / 2, w: gw, h: gh };

    /* -------------------------------------------------- 贴图 ---- */
    var paper = bare ? null : paintPaper(dpr, view);
    var heroImg = bare ? null : paintHero(HERO, dpr, true);
    /* 白到发光的时候牌不该还压着一块影子：另烘一张没有影的，按亮度淡入盖住 */
    var heroClean = bare ? null : paintHero(HERO, dpr, false);
    var tex = bakeCardTexture(heroImg, dpr, cr, bare);
    var lit = bakeLitCard(tex, dpr, cr, radius, V.washMax * 0.80);
    var glowCool = bakeGlow(720, 8801, true);
    var glowWhite = bakeGlow(512, 9907, false);
    var frac = buildFracture(cr, 7717, V, k);
    var stampSprite = V.label ? bakeStamp(V.label, Math.round(V.labelSize * clamp(k, 0.5, 1.3))) : null;

    /* 碎片贴图：一次性从"白到顶的牌"上切下来，之后每帧只 translate + rotate */
    var r0 = rng(90211);
    var shards = [];
    for (var si = 0; si < frac.shards.length; si++) {
      var sh = frac.shards[si];
      var pad = 3;
      var ox = Math.round((sh.bbox.x - pad) * dpr);
      var oy = Math.round((sh.bbox.y - pad) * dpr);
      var ow = Math.max(2, Math.round((sh.bbox.w + pad * 2) * dpr));
      var oh = Math.max(2, Math.round((sh.bbox.h + pad * 2) * dpr));
      var sc = offscreen(ow, oh), cc = sc[1];
      cc.scale(dpr, dpr);
      cc.translate(-sh.bbox.x + pad, -sh.bbox.y + pad);

      /* 碎片 = 一圈冷灰断面 + 内缩的牌面。
       * 断面宽度按碎片的**最小边**取（细长片缩太多会整块都变成断面，那样一飞出去
       * 就是一地深灰色的三角）。缩不动的小片退回"只描一条细边"。 */
      var inset = Math.min(1.7, Math.min(sh.bbox.w, sh.bbox.h) * 0.11);
      var inner = inset > 0.35 ? inPoly(sh.poly, inset) : [];
      if (inner.length >= 3 && polyArea(inner) < sh.area * 0.45) inner = [];

      cc.save();
      cc.beginPath();
      cc.moveTo(sh.poly[0].x, sh.poly[0].y);
      for (var pi = 1; pi < sh.poly.length; pi++) cc.lineTo(sh.poly[pi].x, sh.poly[pi].y);
      cc.closePath();

      if (inner.length >= 3) {
        /* 先整块填断面，再贴内缩的牌面 */
        var rimG = cc.createLinearGradient(sh.bbox.x, sh.bbox.y,
          sh.bbox.x + sh.bbox.w * 0.6, sh.bbox.y + sh.bbox.h);
        rimG.addColorStop(0, "rgba(74,88,104,0.92)");
        rimG.addColorStop(0.5, "rgba(58,72,88,0.92)");
        rimG.addColorStop(1, "rgba(96,110,126,0.92)");
        cc.fillStyle = rimG;
        cc.fill();

        cc.beginPath();
        cc.moveTo(inner[0].x, inner[0].y);
        for (var pj = 1; pj < inner.length; pj++) cc.lineTo(inner[pj].x, inner[pj].y);
        cc.closePath();
        cc.clip();
        cc.drawImage(lit.img, lit.x, lit.y, lit.w, lit.h);
        cc.globalCompositeOperation = "lighter";
        cc.strokeStyle = "rgba(255,255,255,0.92)";
        cc.lineWidth = 0.8;
        cc.stroke();
      } else {
        /* 细片：整块就是牌面，只描一条细边 */
        cc.clip();
        cc.drawImage(lit.img, lit.x, lit.y, lit.w, lit.h);
        cc.strokeStyle = "rgba(64,80,98,0.85)";
        cc.lineWidth = 1.1;
        cc.stroke();
      }
      cc.restore();

      /* 牌面还没被白光吃干净的那一点冷调：碎片是"镜"，不是纸。
       * 只压在内缩之后的牌面上——压到断面上会把深边提亮，碎片就飘了。 */
      var tint = cc.createLinearGradient(sh.bbox.x, sh.bbox.y, sh.bbox.x + sh.bbox.w, sh.bbox.y + sh.bbox.h);
      tint.addColorStop(0, "rgba(206,228,248,0.16)");
      tint.addColorStop(0.6, "rgba(226,240,252,0.06)");
      tint.addColorStop(1, "rgba(255,255,255,0.16)");
      if (inner.length >= 3) {
        cc.save();
        cc.beginPath();
        cc.moveTo(inner[0].x, inner[0].y);
        for (var pk = 1; pk < inner.length; pk++) cc.lineTo(inner[pk].x, inner[pk].y);
        cc.closePath();
        cc.clip();
        cc.globalCompositeOperation = "lighter";
        cc.fillStyle = tint;
        cc.fillRect(sh.bbox.x, sh.bbox.y, sh.bbox.w, sh.bbox.h);
        cc.restore();
      }

      /* 飞散参数：离牌心越远飞得越快（爆开的样子），再加一点切向的偏角 */
      var dx = sh.centroid.x - frac.focus.x, dy = sh.centroid.y - frac.focus.y;
      var dist = Math.max(6, Math.hypot(dx, dy));
      var ang = Math.atan2(dy, dx) + (r0() - 0.5) * 0.55;
      var speed = (760 + 900 * Math.min(1.35, dist / (cr.w * 0.55))) * (0.72 + r0() * 0.62) * V.shardFly * k;

      /* 影子贴图：碎片压在浅色纸上，没有影就"飘"。烘一张糊过的黑片，每帧当图贴 */
      var shc = offscreen(ow * 0.5, oh * 0.5), scc = shc[1];
      scc.scale(dpr * 0.5, dpr * 0.5);
      scc.translate(-sh.bbox.x + pad, -sh.bbox.y + pad);
      if (typeof scc.filter === "string") scc.filter = "blur(5px)";
      scc.fillStyle = "rgba(40,34,26,0.55)";
      scc.beginPath();
      scc.moveTo(sh.poly[0].x, sh.poly[0].y);
      for (var ps = 1; ps < sh.poly.length; ps++) scc.lineTo(sh.poly[ps].x, sh.poly[ps].y);
      scc.closePath();
      scc.fill();

      shards.push({
        sprite: sc[0],
        shadow: shc[0],
        w: ow / dpr, h: oh / dpr,
        ox: (sh.centroid.x - sh.bbox.x + pad),
        oy: (sh.centroid.y - sh.bbox.y + pad),
        cx: sh.centroid.x, cy: sh.centroid.y,
        vx: Math.cos(ang) * speed,
        vy: Math.sin(ang) * speed - (60 + r0() * 140) * k,
        rot: 0, spin: (r0() - 0.5) * V.shardSpin * 2.2,
        flip: r0() * TAU, flipV: (3.2 + r0() * 5.2) * (r0() < 0.5 ? -1 : 1),
        area: sh.area
      });
    }

    /* 玻璃尘 / 碎屑 */
    var rb = rng(44107);
    var dust = [], flakes = [];
    for (var di = 0; di < V.dust; di++) {
      var da = rb() * TAU, dr = rb();
      var dspeed = (160 + rb() * 900) * k;
      dust.push({
        x: frac.focus.x + Math.cos(da) * dr * cr.w * 0.34,
        y: frac.focus.y + Math.sin(da) * dr * cr.h * 0.30,
        vx: Math.cos(da) * dspeed,
        vy: Math.sin(da) * dspeed - (40 + rb() * 160) * k,
        r: (0.5 + rb() * 1.5) * k,
        life: 0.42 + rb() * 0.72,
        delay: rb() * 0.05,
        white: rb() < 0.72
      });
    }
    for (var fi = 0; fi < V.flakes; fi++) {
      var fa = rb() * TAU, fr = rb();
      var fspeed = (300 + rb() * 760) * k;
      flakes.push({
        x: frac.focus.x + Math.cos(fa) * fr * cr.w * 0.30,
        y: frac.focus.y + Math.sin(fa) * fr * cr.h * 0.26,
        vx: Math.cos(fa) * fspeed,
        vy: Math.sin(fa) * fspeed - (60 + rb() * 220) * k,
        r: (1.6 + rb() * 3.4) * k,
        rot: rb() * TAU,
        spin: (rb() - 0.5) * 14,
        life: 0.5 + rb() * 0.7,
        delay: rb() * 0.04
      });
    }

    /* 碎裂时从牌心射出去的几道白光 */
    var streak = [];
    for (var ti = 0; ti < 14; ti++) {
      streak.push({
        a: ti / 14 * TAU + (rb() - 0.5) * 0.22,
        len: (150 + rb() * 420) * k,
        w: (1.0 + rb() * 2.4) * k,
        delay: rb() * 0.035
      });
    }

    /* 裂缝的生长顺序：从受力点出发的**图上距离**（BFS）。
     * 按边长或按"离受力点多远"排，都会让一批互不相连的边同时冒出来，看着像散落的短线；
     * 按图距离排，牌裂开是**一棵树从牌心往外长**——先一条主干劈出去，再沿接口分叉，
     * 这才是玻璃被砸开的样子。 */
    var waves = V.waves;
    var holdAt = V.holdAt;
    var burstAt = V.burstAt;
    var rc = rng(60271);
    var cracks = frac.cracks;

    function vkey(p) { return Math.round(p.x * 2) + "," + Math.round(p.y * 2); }
    var adj = {};
    for (var ai = 0; ai < cracks.length; ai++) {
      var ak = vkey(cracks[ai].a), bk = vkey(cracks[ai].b);
      (adj[ak] || (adj[ak] = [])).push(ai);
      (adj[bk] || (adj[bk] = [])).push(ai);
    }
    /* 起点：离受力点最近的那个顶点 */
    var startKey = null, bestD = 1e9;
    for (var vk in adj) {
      if (!Object.prototype.hasOwnProperty.call(adj, vk)) continue;
      var cut = vk.split(",");
      var vx = Number(cut[0]) / 2, vy = Number(cut[1]) / 2;
      var vd = Math.hypot(vx - frac.focus.x, vy - frac.focus.y);
      if (vd < bestD) { bestD = vd; startKey = vk; }
    }
    var level = {}, order = new Array(cracks.length);
    var frontier = [startKey], levelNo = 0, maxOrder = 0;
    level[startKey] = 0;
    while (frontier.length) {
      var next = [];
      for (var fi = 0; fi < frontier.length; fi++) {
        var fk = frontier[fi], list = adj[fk] || [];
        for (var li = 0; li < list.length; li++) {
          var ei = list[li];
          if (order[ei] !== undefined) continue;
          order[ei] = level[fk];
          if (level[fk] > maxOrder) maxOrder = level[fk];
          var other = vkey(cracks[ei].a) === fk ? vkey(cracks[ei].b) : vkey(cracks[ei].a);
          if (level[other] === undefined) { level[other] = level[fk] + 1; next.push(other); }
        }
      }
      frontier = next;
      levelNo++;
    }
    for (var oi = 0; oi < order.length; oi++) if (order[oi] === undefined) order[oi] = maxOrder;

    var maxLen = 0;
    for (var mi = 0; mi < cracks.length; mi++) maxLen = Math.max(maxLen, cracks[mi].len);
    maxLen = Math.max(1, maxLen);
    var span = Math.max(0.3, holdAt - 0.10 - waves[0]);
    for (var ci = 0; ci < cracks.length; ci++) {
      var ck = cracks[ci];
      var f = maxOrder > 0 ? Math.pow(order[ci] / maxOrder, 0.82) : 0;
      /* 同一层里，长的那条先一点：主干先走，碎枝后补 */
      ck.start = waves[0] + f * span * 0.94 + (1 - ck.len / maxLen) * 0.05 + rc() * 0.03;
      ck.dur = 0.13 + 0.30 * (ck.len / maxLen) + rc() * 0.06;
    }

    /* 白度曲线：先只是一层浮光（牌还看得清），裂缝多起来才猛白，最后顶住。
     * 用锚点 + smoothstep 插值，别用一条公式——牌面什么时候还读得清是设计决定的。 */
    var WASH_AT = [
      [V.wakeAt, 0],
      [waves[0] + 0.10, 0.10],
      [waves[1], 0.24],
      [waves[2], 0.56],
      [holdAt, V.washMax]
    ];
    function lit_At(t) {
      if (t > holdAt) {
        return clamp(V.washMax * (1 + 0.012 * Math.sin((t - holdAt) * 9.0)), 0, 1);
      }
      for (var wi = 1; wi < WASH_AT.length; wi++) {
        if (t <= WASH_AT[wi][0]) {
          return lerp(WASH_AT[wi - 1][1], WASH_AT[wi][1],
            ss(WASH_AT[wi - 1][0], WASH_AT[wi][0], t));
        }
      }
      return V.washMax;
    }

    /* 光比白先到：第一道裂缝出现时就该看见牌在发光，牌面本身要更晚才白透 */
    function glowAt(t, inten) {
      return clamp(ss(V.wakeAt, waves[0] + 0.22, t) * (0.55 + 0.45 * inten), 0, 1);
    }

    /* 抖：越到后面越绷不住；停顿段归零（静止才像"憋住"） */
    function shakeAt(t) {
      if (t >= holdAt) return 0;
      var amt = V.shake * ss(waves[0], holdAt - 0.06, t);
      return Math.sin(t * 87) * amt * 0.6;
    }

    /* -------------------------------------------------- 画面 ---- */
    function drawCard(t, inten, settle) {
      var padW = CARD.pad * (gw / CARD.w);
      /* 白起来的时候整张牌"鼓"一点：光从里面撑出来，不是贴上去的 */
      var grow = 1 + 0.011 * ss(waves[1], holdAt, t);
      c.save();
      c.translate(cr.x + cr.w / 2, cr.y + cr.h / 2 + settle);
      c.scale(grow, grow);
      c.translate(-cr.x - cr.w / 2, -cr.y - cr.h / 2 - settle);
      if (!bare) {
        c.drawImage(heroImg, cr.x - padW, cr.y - padW + settle,
          gw + padW * 2, gh + padW * 2);
        /* 影子随白光淡掉 */
        var noShadow = 0.72 * ss(0.12, 0.55, inten);
        if (noShadow > 0.01) {
          c.globalAlpha = noShadow;
          c.drawImage(heroClean, cr.x - padW, cr.y - padW + settle,
            gw + padW * 2, gh + padW * 2);
          c.globalAlpha = 1;
        }
      }

      /* 泛白：整张牌往上压白，越到后面越白（牌桌档只画这一层，牌本身在下面） */
      c.save();
      pathCard(c, { x: cr.x, y: cr.y + settle, w: cr.w, h: cr.h }, radius);
      c.clip();
      c.fillStyle = "rgba(255,255,255," + inten + ")";
      c.fillRect(cr.x, cr.y, cr.w, cr.h);
      if (inten > 0.01) {
        /* 牌心更白一点：光是从那里渗出来的。用烘好的带噪光（source-over，
         * 不再拿 lighter 往上加——浅色牌一加就顶到 255，牌心会变成一块死白） */
        c.globalAlpha = 0.38 * inten;
        var gr = cr.w * 1.2;
        c.drawImage(glowWhite, frac.focus.x - gr, frac.focus.y + settle - gr, gr * 2, gr * 2);
        c.globalAlpha = 1;
      }
      c.restore();
      c.restore();
    }

    /* 裂缝：只画已经长出来的那一段，裁在牌里。
     * 已长完的边合进两条路径（粗 / 细各一条）一次描完；还在长的少量边就地在
     * 那两条路径里画到当前长度——一帧最多 8 笔 stroke，跟碎片数无关。 */
    function drawCracks(t, inten) {
      c.save();
      pathCard(c, cr, radius);
      c.clip();
      c.lineCap = "round";
      c.lineJoin = "round";

      /* 缝分两段画：靠受力点的一半粗、往外的一半细（真裂缝是越走越细的），
       * 每段各自合成一条路径，一帧还是 8 笔。 */
      var thickA = [], thickB = [], thinA = [], thinB = [], tips = [];
      for (var i = 0; i < cracks.length; i++) {
        var ck = cracks[i];
        var age = t - ck.start;
        if (age <= 0) continue;
        var prog = age >= ck.dur ? 1 : eOut(age / ck.dur);
        var pa = ck.thick ? thickA : thinA;
        var pb = ck.thick ? thickB : thinB;
        var half = ck.len > 0.01 ? ck.l1 / ck.len : 0.5;
        if (prog <= half) {
          var f = prog / half;
          pa.push(ck.a.x, ck.a.y, lerp(ck.a.x, ck.m.x, f), lerp(ck.a.y, ck.m.y, f));
          if (prog < 1) tips.push(lerp(ck.a.x, ck.m.x, f), lerp(ck.a.y, ck.m.y, f));
        } else {
          pa.push(ck.a.x, ck.a.y, ck.m.x, ck.m.y);
          var f2 = Math.min(1, (prog - half) / Math.max(0.01, 1 - half));
          pb.push(ck.m.x, ck.m.y, lerp(ck.m.x, ck.b.x, f2), lerp(ck.m.y, ck.b.y, f2));
          if (prog < 1) tips.push(lerp(ck.m.x, ck.b.x, f2), lerp(ck.m.y, ck.b.y, f2));
        }
      }

      crackPass(thickA, 1.0 * k, inten, 1.0);
      crackPass(thickB, 1.0 * k, inten, 0.60);
      crackPass(thinA, 0.78 * k, inten, 1.0);
      crackPass(thinB, 0.78 * k, inten, 0.60);

      /* 正在长的缝尖上一点白：缝是"还在走"的，不是在纸上画好的 */
      if (tips.length) {
        c.save();
        c.globalCompositeOperation = "lighter";
        c.fillStyle = "rgba(255,255,255,0.55)";
        for (var ti = 0; ti < tips.length; ti += 2) {
          c.beginPath();
          c.arc(tips[ti], tips[ti + 1], 1.5 * k, 0, TAU);
          c.fill();
        }
        c.restore();
      }
      c.restore();
    }

    /* 一笔路径 + 四道描边：冷光 / 暗影 / 缝芯 / 亮口。
     * 顺序和偏移是"凹槽"的读法：影子在上左、亮口在下右。 */
    function crackPass(segs, width, inten, scale) {
      if (!segs.length) return;
      c.beginPath();
      for (var i = 0; i < segs.length; i += 4) {
        c.moveTo(segs[i], segs[i + 1]);
        c.lineTo(segs[i + 2], segs[i + 3]);
      }

      /* ① 一圈很淡的冷灰外晕：缝旁边总有一点"料被崩白"的过渡 */
      var gw = 0.52 + 0.48 * inten;
      c.strokeStyle = "rgba(120,142,164," + (0.10 + 0.06 * inten) + ")";
      c.lineWidth = width * scale * 6.6 * gw;
      c.stroke();

      /* ② 缝芯：冷墨，一直留到底——白牌上只有变暗看得见 */
      c.strokeStyle = "rgba(24,30,38," + (0.74 + 0.20 * (1 - inten)) + ")";
      c.lineWidth = width * scale * 3.6 * gw;
      c.stroke();

      /* ③ 缝里透出来的光：**居中**一道细白（不做偏移描边——斜着的缝上，
       *    偏移的亮边会把缝芯盖掉，看上去就成了一段一段的虚线） */
      c.strokeStyle = "rgba(252,254,255," + (0.18 + 0.64 * inten) + ")";
      c.lineWidth = width * scale * (0.9 + 0.25 * inten) * gw;
      c.stroke();
    }

    /* 牌心那一小块"砸碎的核心"：几条主裂都从这里断开，空出来的地方只是更亮一点，
     * 不画成一朵黑色的星（那看起来像漫画里的爆炸，不像玻璃被砸） */
    function drawCore(inten, t) {
      var a = (0.10 + 0.10 * inten) * ss(waves[0] - 0.02, waves[0] + 0.3, t);
      if (a <= 0.002) return;
      var R = frac.coreR * 1.3;
      c.save();
      pathCard(c, cr, radius);
      c.clip();
      c.globalCompositeOperation = "multiply";
      var g = c.createRadialGradient(frac.focus.x, frac.focus.y, 0,
        frac.focus.x, frac.focus.y, R);
      g.addColorStop(0, "rgba(96,104,112," + a + ")");
      g.addColorStop(0.5, "rgba(110,116,122," + (a * 0.45) + ")");
      g.addColorStop(1, "rgba(120,120,120,0)");
      c.fillStyle = g;
      c.beginPath();
      c.arc(frac.focus.x, frac.focus.y, R, 0, TAU);
      c.fill();
      c.restore();
    }

    /* 曝光：白得越厉害，周边越沉一点。浅色纸上"光"只能这么写——
     * 往 #f1ebdf 上加白最多只能加 14 级，压暗才有对比。 */
    function drawExposure(inten) {
      var a = 0.30 * ss(0.16, 0.95, inten);
      if (a <= 0.002) return;
      drawVignette(a);
    }

    /* 压角：越白越压（同时牌桌四周暗下去，画面上只剩这张牌在放光）。
     * 用 multiply 而不是加白：这张纸只有 14 级白可加，能拉开对比的只有变暗。 */
    function drawVignette(a) {
      c.save();
      c.globalCompositeOperation = "multiply";
      var vg = c.createRadialGradient(frac.focus.x, frac.focus.y, cr.w * 0.56,
        frac.focus.x, frac.focus.y, 1320 * k);
      vg.addColorStop(0, "rgba(78,68,54,0)");
      vg.addColorStop(0.42, "rgba(78,68,54," + (a * 0.30) + ")");
      vg.addColorStop(1, "rgba(78,68,54," + a + ")");
      c.fillStyle = vg;
      c.fillRect(-200, -200, vw + 400, vh + 400);
      c.restore();
    }

    /* 牌外的光：纸被照亮 + 牌边一圈冷白 */
    function drawHalo(inten) {
      if (inten <= 0.002) return;
      var R = cr.h * 1.12;
      c.save();
      c.globalCompositeOperation = "lighter";
      c.globalAlpha = 0.06 * inten;
      c.drawImage(glowCool, frac.focus.x - R, frac.focus.y - R * 0.94, R * 2, R * 2 * 0.94);
      c.globalAlpha = 1;

      /* 牌边：外面几层越来越细的白，读作"边缘在漏光"（bake 不了，四笔而已） */
      for (var i = 0; i < 5; i++) {
        c.strokeStyle = "rgba(238,247,255," + ((0.035 + 0.135 * inten) * (1 - i * 0.17)) + ")";
        c.lineWidth = (26 - i * 5.2) * k * (0.35 + inten * 0.9);
        pathCard(c, cr, radius);
        c.stroke();
      }
      c.strokeStyle = "rgba(255,255,255," + (0.14 + 0.72 * inten) + ")";
      c.lineWidth = 1.6 * k;
      pathCard(c, cr, radius);
      c.stroke();
      c.restore();
    }

    /* 碎：余晖 → 爆闪（在碎片后面）→ 冲击环 → 光条 → 碎片 → 尘 */
    function drawBurst(age) {
      /* 余晖：牌原来所在的位置还亮着，很快收掉 */
      var glow = Math.exp(-age * 5.0) * (1 - Math.exp(-age * 26));
      if (glow > 0.004) {
        c.save();
        c.globalCompositeOperation = "lighter";
        c.globalAlpha = 0.06 * glow;
        var R = cr.h * 0.82;
        c.drawImage(glowCool, frac.focus.x - R, frac.focus.y - R * 0.92, R * 2, R * 2 * 0.92);
        c.restore();
      }

      /* 碎那一下的"闪"：这张纸本来就接近 #f1ebdf，加白只会一路顶到 255、把碎片吃掉，
       * 所以闪做成两件事——中心一小块白热芯（很快就收），加四周**压暗**一圈（曝光一沉，
       * 白碎片才立得出来）。整屏白闪在这套配色里是反效果，这里没有。 */
      var hotCore = V.flash * Math.exp(-age * 26) * (1 - Math.exp(-age * 160));
      if (hotCore > 0.006) {
        c.save();
        c.globalCompositeOperation = "lighter";
        c.globalAlpha = 0.34 * hotCore;
        var FR = cr.h * 0.24 * (1 + age * 7);
        c.drawImage(glowWhite, frac.focus.x - FR, frac.focus.y - FR, FR * 2, FR * 2);
        c.restore();
      }
      var dip = 0.20 * V.flash * Math.exp(-age * 11) * (1 - Math.exp(-age * 90));
      if (dip > 0.004) {
        c.save();
        c.globalCompositeOperation = "multiply";
        var vg = c.createRadialGradient(frac.focus.x, frac.focus.y, cr.w * 0.9,
          frac.focus.x, frac.focus.y, 1180 * k);
        vg.addColorStop(0, "rgba(90,80,64,0)");
        vg.addColorStop(0.6, "rgba(90,80,64," + (dip * 0.55) + ")");
        vg.addColorStop(1, "rgba(90,80,64," + dip + ")");
        c.fillStyle = vg;
        c.fillRect(-200, -200, vw + 400, vh + 400);
        c.restore();
      }

      /* 光条：从牌心射出去的几道，极短 */
      if (age < 0.13) {
        c.save();
        c.globalCompositeOperation = "lighter";
        for (var i = 0; i < streak.length; i++) {
          var st = streak[i];
          var a2 = age - st.delay;
          if (a2 <= 0 || a2 > 0.13) continue;
          var f = eOut(a2 / 0.13);
          var fade = 1 - a2 / 0.13;
          c.strokeStyle = "rgba(240,249,255," + (0.42 * fade) + ")";
          c.lineWidth = st.w * (1 - f * 0.5);
          c.beginPath();
          c.moveTo(frac.focus.x + Math.cos(st.a) * cr.w * 0.10,
            frac.focus.y + Math.sin(st.a) * cr.h * 0.09);
          c.lineTo(frac.focus.x + Math.cos(st.a) * st.len * f * 0.55,
            frac.focus.y + Math.sin(st.a) * st.len * f * 0.5);
          c.stroke();
        }
        c.restore();
      }

      /* 碎片：先落一层淡影，再贴片子 —— 浅色碎片压在浅色纸上，没有影就"飘" */
      var shadowA = 0.16 * (1 - ss(0.16, 0.55, age));
      for (var s = 0; s < shards.length; s++) {
        var sh2 = shards[s];
        var x = sh2.cx + sh2.vx * age;
        var y = sh2.cy + sh2.vy * age + 0.5 * GRAV * age * age;
        if (y > vh + 300 || x < -340 || x > vw + 340) continue;
        /* 刚裂开的那几帧是正对着的（不然第一帧碎片就被压扁、整张牌立刻露缝），
         * 之后各自开始翻面 */
        var flip = Math.cos(sh2.flipV * Math.max(0, age - 0.03));
        var sx = Math.max(0.07, Math.abs(flip));
        var rot = sh2.rot + sh2.spin * age;
        var alpha = 1 - ss(0.42, 0.80, age);
        if (alpha <= 0.01) continue;

        /* 边缘反光：正对着的时候最亮，翻到侧面变成一条线 */
        var glint = Math.pow(1 - Math.abs(flip), 2.2);

        if (shadowA > 0.005) {
          c.save();
          c.globalAlpha = shadowA * alpha;
          c.translate(x + 7 * k, y + 11 * k);
          c.rotate(rot);
          c.scale(sx * 0.98, 0.98);
          c.drawImage(sh2.shadow, -sh2.ox, -sh2.oy, sh2.w, sh2.h);
          c.restore();
        }

        /* 拖尾：碎的那一下速度太快，补两道残影才有"崩"的劲 */
        if (age < 0.22 && sx > 0.3) {
          c.save();
          c.translate(x, y);
          c.rotate(rot);
          c.scale(sx, 1);
          for (var tr = 2; tr >= 1; tr--) {
            var back = tr * 0.022;
            c.globalAlpha = alpha * (0.07 + 0.04 * tr) * (1 - age / 0.22);
            c.drawImage(sh2.sprite,
              -sh2.ox - sh2.vx * back, -sh2.oy - sh2.vy * back, sh2.w, sh2.h);
          }
          c.restore();
        }

        c.save();
        c.globalAlpha = clamp(alpha * (0.88 + 0.12 * glint), 0, 1);
        c.translate(x, y);
        c.rotate(rot);
        c.scale(sx, 1);
        c.drawImage(sh2.sprite, -sh2.ox, -sh2.oy, sh2.w, sh2.h);
        c.restore();

        /* 侧过去的那一瞬：一条白线（镜子的边） */
        if (glint > 0.55 && age < 0.9) {
          var lineLen = Math.min(120 * k, Math.sqrt(sh2.area) * 0.75);
          c.save();
          c.globalAlpha = alpha * (glint - 0.55) * 0.85;
          c.globalCompositeOperation = "lighter";
          c.strokeStyle = "rgba(240,250,255,0.95)";
          c.lineWidth = 1.2 * k;
          c.beginPath();
          c.moveTo(x - Math.cos(rot) * lineLen * 0.5, y - Math.sin(rot) * lineLen * 0.5);
          c.lineTo(x + Math.cos(rot) * lineLen * 0.5, y + Math.sin(rot) * lineLen * 0.5);
          c.stroke();
          c.restore();
        }
      }

      /* 碎屑（大一点的片） */
      c.save();
      for (var f2 = 0; f2 < flakes.length; f2++) {
        var fl = flakes[f2];
        var fa2 = age - fl.delay;
        if (fa2 <= 0 || fa2 > fl.life) continue;
        var falpha = (1 - fa2 / fl.life);
        var fx = fl.x + fl.vx * fa2;
        var fy = fl.y + fl.vy * fa2 + 0.5 * GRAV * 0.8 * fa2 * fa2;
        if (fy > vh + 80) continue;
        c.save();
        c.globalAlpha = falpha * 0.85;
        c.translate(fx, fy);
        c.rotate(fl.rot + fl.spin * fa2);
        c.fillStyle = "#ffffff";
        c.strokeStyle = "rgba(140,160,180,0.55)";
        c.lineWidth = 0.7;
        c.beginPath();
        c.moveTo(-fl.r, -fl.r * 0.55);
        c.lineTo(fl.r * 0.85, -fl.r * 0.75);
        c.lineTo(fl.r, fl.r * 0.4);
        c.lineTo(-fl.r * 0.6, fl.r * 0.8);
        c.closePath();
        c.fill();
        c.stroke();
        c.restore();
      }
      c.restore();

      /* 尘：白尘 + 一点墨尘（碎掉的牌屑） */
      c.save();
      for (var d2 = 0; d2 < dust.length; d2++) {
        var du = dust[d2];
        var da2 = age - du.delay;
        if (da2 <= 0 || da2 > du.life) continue;
        var dalpha = (1 - da2 / du.life);
        var dx2 = du.x + du.vx * da2;
        var dy2 = du.y + du.vy * da2 + 0.5 * GRAV * 0.62 * da2 * da2;
        if (dy2 > vh + 40) continue;
        c.globalAlpha = dalpha * 0.9;
        if (du.white) {
          c.globalCompositeOperation = "source-over";
          c.fillStyle = "rgba(246,251,255,0.92)";
          c.strokeStyle = "rgba(150,170,190,0.35)";
          c.lineWidth = 0.6 * k;
          c.beginPath();
          c.arc(dx2, dy2, du.r * (0.5 + 0.5 * dalpha), 0, TAU);
          c.fill();
          c.stroke();
        } else {
          c.globalCompositeOperation = "source-over";
          c.fillStyle = "rgba(32,30,27,0.62)";
          c.beginPath();
          c.arc(dx2, dy2, du.r * (0.6 + 0.6 * dalpha), 0, TAU);
          c.fill();
        }
      }
      c.restore();
    }

    /* 朱砂「阵亡」：落印那一拍，纸跟着麻一下 */
    function drawStamp(t) {
      if (!stampSprite) return;
      var age = t - V.labelAt;
      if (age < 0) return;
      /* 落印：略大 → 压到位（0.16s），再极轻地回一下，别做成弹簧 */
      var press = eOut(clamp(age / 0.16, 0, 1));
      var pop = 1.22 - 0.22 * press - 0.012 * Math.sin(clamp(age / 0.5, 0, 1) * Math.PI);
      var alpha = clamp(age / 0.04, 0, 1);
      var y = cr.y + cr.h * 0.54 + (1 - press) * 10 * k;
      var rot = -0.035;
      c.save();
      c.globalAlpha = alpha;
      c.globalCompositeOperation = "multiply";
      c.translate(cr.x + cr.w * 0.5, y);
      c.rotate(rot);
      c.scale(pop, pop);
      c.drawImage(stampSprite.img, -stampSprite.w / 2, -stampSprite.h / 2, stampSprite.w, stampSprite.h);
      c.restore();
    }

    function draw(t) {
      /* `t` = **牌桌秒**（0..`duration`）。内部乘回素材秒：`clock.speed = 3.05 / 牌桌秒`，
       * 于是票面那张"整段 2200ms、各节拍等比缩放（k = total / 3050）"的表在这里落地，
       * 而不必逐字段重算 `PARAMS`。 */
      t = clamp((Number(t) || 0) * clock.speed, 0, V.duration);
      var pre = t < burstAt;
      var inten = pre ? lit_At(t) : 1;

      /* 抖：碎裂前越来越绷不住，停顿段归零；碎裂与落印各自再麻一下 */
      var sh = 0;
      if (pre) {
        sh = shakeAt(t);
      } else {
        var a = t - burstAt;
        sh = Math.sin(a * 68) * V.shake * 2.0 * Math.exp(-a * 15) * (1 - Math.exp(-a * 46));
        var b = t - V.labelAt;
        if (b >= 0) sh += Math.sin(b * 82) * 1.6 * Math.exp(-b * 22) * (1 - Math.exp(-b * 55));
      }

      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, vw, vh);

      /* 纸不跟着抖：抖的是牌和光，不是桌子 */
      if (!bare) c.drawImage(paper, -80, -80, vw + 160, vh + 160);

      c.save();
      c.translate(sh, sh * 0.4);

      if (pre) {
        drawHalo(glowAt(t, inten));
        /* 起手那一下：牌往下沉 1.4px（"绷住"的那一点重量） */
        drawCard(t, inten, 1.4 * k * ss(V.wakeAt, V.wakeAt + 0.3, t));
        drawCracks(t, inten);
        drawCore(inten, t);
        drawExposure(inten);
      } else {
        var tail = 0.11 * V.flash * Math.exp(-(t - burstAt) * 7.5) * (1 - Math.exp(-(t - burstAt) * 60));
        if (tail > 0.004) drawVignette(tail);
        drawBurst(t - burstAt);
      }

      drawStamp(t);
      c.restore();
    }

    return {
      draw: draw,
      /* 排播层按 `duration`（**牌桌秒**）定时摘层；三个节拍同样换成牌桌秒——
       * `burstAt` 切 `data-death-fx="gone"`、`labelAt` 只是读数（牌桌档不落印）。 */
      duration: clock.seconds,
      total: clock.total,
      timing: clock.timing,
      speed: clock.speed,
      width: vw,
      height: vh,
      burstAt: burstAt / clock.speed,
      holdAt: holdAt / clock.speed,
      labelAt: V.labelAt / clock.speed,
      tier: V,
      /* 本实例**生效**的那张表（`label` 已按 opts 覆盖）：音效报点与本文件同读一份。 */
      PARAMS: V,
      shardCount: shards.length,
      crackCount: cracks.length
    };
  }

  /* ================================================================
   * 排播（住同一文件，与 `damage-fx.js` / `fx-chain.js` 同姿态）
   *
   * **无时停**（用户口径）：不登记 `registerPlaybackHold`，演出与后续记录并行是预期。
   * 反过来，次序必须由这里兜住——`renderTable` 在演出之前就把牌画成"已阵亡"（`opacity .72`
   * + 蒙灰伪元素 + 「阵亡」标签），而演出前 1.7s 牌还得是活的，所以：
   *
   *   `data-death-fx="hold"`  牌看着还活着（CSS 压住置灰与「阵亡」标签）
   *   `data-death-fx="gone"`  碎了之后牌本身藏掉（`visibility: hidden`）
   *   属性摘掉                交还给游戏（灰 + 「阵亡」标签自然回来）
   *
   * 钩子（调用方显式调，本文件不反向抓事件）：
   *   session.js:stepPlayback  → fireDeathEventsThrough(seq)   一条记录到点的判据
   *   table.js:renderTable     → deathFxSync()                 重贴属性 + 版式重检
   *   session.js:commitView    → clearDeathFx()                换局清场
   * ============================================================== */

  /* 同时在播上限（spec §演出口径·阵亡）：一次结算死两家已是罕见场面；**第 3 条丢队尾，
   * 不排队等**（阵亡常连着别的结算，等下去只会把记录流拖长——虽然本套本来就不冻记录流）。 */
  var DEATH_FX_MAX = 2;

  var deathPlays = [];
  var deathFrame = null;
  var deathStartedCount = 0;
  var deathDroppedCount = 0;
  /* `seq` 游标：同一条记录只演一次（与技能 / 伤害共用同一条 seq 的读法）。换局清回 `null`。 */
  var deathCursor = null;
  /* `finishDeathPlay` 在同步途中要求再同步一次（见 `deathFxSync` 的重入说明）。 */
  var deathSyncBusy = false;
  var deathSyncDirty = false;

  function deathNow() {
    if (typeof stageNow === "function") return Number(stageNow()) || 0;
    return Date.now();
  }

  function deathLayer() {
    if (typeof $ !== "function") return null;
    var layer = $("t-death-fx");
    return layer && layer.classList ? layer : null;
  }

  /* 演出画布的分辨率上限：与伤害层同一个封顶（牌桌是 1920 设计单位撑满屏）。 */
  function deathFxDpr() {
    if (typeof damageFxDpr === "function") return Number(damageFxDpr()) || 1;
    var ratio = Number(window.devicePixelRatio);
    return isFinite(ratio) && ratio > 0 ? Math.min(1.5, ratio) : 1;
  }

  function deathSeatNodeOf(seat) {
    if (typeof seatNodeFor !== "function") return null;
    return seatNodeFor(seat);
  }

  /* 座位上的覆盖读数 / 抑制态：`data-death-fx="hold" | "gone"`（三条 CSS 覆盖规则读它）。 */
  function deathSetSeatFlag(seat, phase) {
    var node = deathSeatNodeOf(seat);
    if (!node || !node.setAttribute) return null;
    if (phase) node.setAttribute("data-death-fx", phase);
    else if (node.removeAttribute) node.removeAttribute("data-death-fx");
    return node;
  }

  function deathSeatLive(seat) {
    for (var i = 0; i < deathPlays.length; i++) {
      if (Number(deathPlays[i].seat) === Number(seat)) return deathPlays[i];
    }
    return null;
  }

  /* 层上的可读状态：在播几条 / 累计开演几次 / 丢了几条（量不到 stage 或超出上限）。
   * 画布像素不可断言（也不该断言），所以读数写在节点上——烟测与实机验收都读它。 */
  function deathFxMark(layer) {
    if (!layer || !layer.setAttribute) return;
    layer.setAttribute("data-death-fx-live", String(deathPlays.length));
    layer.setAttribute("data-death-fx-plays", String(deathStartedCount));
    layer.setAttribute("data-death-fx-dropped", String(deathDroppedCount));
  }

  /* -------------------------------------------------- 一次演出 ---- */

  /* 座位 → **覆盖层坐标**里的矩形（CSS px）。借伤害层的 `damageFxBoxOf`（同一套换算），
   * 缺件（静态探针只加载本文件）时退回等价的小实现。 */
  function deathStageOf(seat, layerRect) {
    if (typeof heroBlockFor !== "function" || typeof rectOf !== "function") return null;
    var block = heroBlockFor(seat);
    if (!block) return null;
    var box;
    if (typeof damageFxBoxOf === "function") {
      box = damageFxBoxOf(block, layerRect);
    } else {
      var rect = rectOf(block);
      var left = Number(rect.left) || 0;
      var top = Number(rect.top) || 0;
      var w = Number(rect.width) || 0;
      var h = Number(rect.height) || 0;
      box = {
        cx: left - (Number(layerRect.left) || 0) + w / 2,
        cy: top - (Number(layerRect.top) || 0) + h / 2,
        w: w,
        h: h
      };
    }
    if (!box || !(Number(box.w) > 0) || !(Number(box.h) > 0)) return null;
    return { cx: Number(box.cx), cy: Number(box.cy), w: Number(box.w), h: Number(box.h) };
  }

  function deathBoxKey(box) {
    return [box.cx, box.cy, box.w, box.h]
      .map(function (value) { return Math.round(Number(value) || 0); })
      .join(",");
  }

  /* 变一档时间轴的原始 ini 值给 `create()`：缺键不放进表（模块退默认），钳制在模块里。 */
  function deathTimingRaw() {
    var raw = {};
    if (typeof animTiming !== "function") return raw;
    var key = timingKeyOf("total");
    var value = animTiming(key);
    if (value !== undefined && value !== null && isFinite(Number(value))) raw[key] = Number(value);
    return raw;
  }

  function startDeathPlay(event) {
    var layer = deathLayer();
    if (!layer || typeof rectOf !== "function") return null;
    var seat = Number(event && event.seat);
    if (!isFinite(seat)) return null;
    /* 每条开演前**重测**一次 stage：版式可能刚变（窗口缩放 / 座位重排）。量不到就不演
     * （`deathDroppedCount` 记一笔，实机 / 烟测都读得到），宁可不演也不演在半空。 */
    var layerRect = rectOf(layer);
    var box = deathStageOf(seat, layerRect);
    if (!box) {
      deathDroppedCount += 1;
      deathFxMark(layer);
      return null;
    }
    var view = {
      w: Math.max(1, Math.round(Number(layerRect.width) || 0)),
      h: Math.max(1, Math.round(Number(layerRect.height) || 0))
    };

    var canvas = document.createElement("canvas");
    canvas.className = "death-fx-canvas";
    /* 读数（烟测 / 实机配方都读它）：这一张是哪个座位、演到哪一拍、锚在哪一块矩形上
     * （`cx,cy,w,h`，覆盖层坐标的 CSS px——"两条各在各的牌上"靠它读，不靠像素）。 */
    if (canvas.setAttribute) {
      canvas.setAttribute("data-death-seat", String(seat));
      canvas.setAttribute("data-death-phase", "hold");
      canvas.setAttribute("data-death-box", [
        Math.round(box.cx), Math.round(box.cy), Math.round(box.w), Math.round(box.h)
      ].join(","));
    }
    canvas.style.cssText =
      "position:absolute;pointer-events:none;left:0;top:0;width:" + view.w +
      "px;height:" + view.h + "px";
    layer.appendChild(canvas);

    var fx = create(canvas, {
      /* 牌桌档：**只出特效**（宣纸底 / 样例武将牌不画）、武将块是直角（`radius: 0`）、
       * **不落印**（`label: ""`——游戏自己已经有「阵亡」标签）。 */
      bare: true,
      radius: 0,
      label: "",
      dpr: deathFxDpr(),
      timing: deathTimingRaw(),
      view: view,
      stage: box
    });

    var play = {
      seq: Number(event.seq),
      seat: seat,
      canvas: canvas,
      fx: fx,
      startedAt: deathNow(),
      duration: Math.max(0, Number(fx.duration) || 0) * 1000,
      goneAt: Math.max(0, Number(fx.burstAt) || 0) * 1000,
      boxKey: deathBoxKey(box),
      phase: "hold",
      goneTimer: null,
      timer: null
    };
    deathPlays.push(play);
    deathStartedCount += 1;
    deathSetSeatFlag(seat, "hold");
    /* 音效与画面同一张表：`speed` 把素材秒映射回牌桌秒（音效模块只认 fromT / speed）。 */
    if (typeof deathSfxPlay === "function") {
      deathSfxPlay(0, Number(fx.speed) > 0 ? Number(fx.speed) : 1);
    }
    /* 两拍都挂**演出自己的计时器**：无时停 ⇒ 记录流不会等它，不能"等 commit / 下一批"收尾。 */
    play.goneTimer = window.setTimeout(function () {
      play.goneTimer = null;
      deathMarkGone(play);
    }, play.goneAt);
    play.timer = window.setTimeout(function () { finishDeathPlay(play); }, play.duration);
    deathFxMark(layer);
    deathRequestFrame();
    return play;
  }

  /* 到 `burstAt`：牌已经碎了，牌本身藏掉（等演出收尾再交还给游戏）。 */
  function deathMarkGone(play) {
    if (!play || deathPlays.indexOf(play) < 0) return;
    play.phase = "gone";
    deathSetSeatFlag(play.seat, "gone");
    if (play.canvas && play.canvas.setAttribute) {
      play.canvas.setAttribute("data-death-phase", "gone");
    }
  }

  function deathDropTimers(play) {
    if (!play) return;
    for (var i = 0; i < 2; i++) {
      var key = i === 0 ? "goneTimer" : "timer";
      if (play[key] !== null && play[key] !== undefined && window.clearTimeout) {
        window.clearTimeout(play[key]);
      }
      play[key] = null;
    }
  }

  function finishDeathPlay(play) {
    if (!play) return;
    var at = deathPlays.indexOf(play);
    if (at >= 0) deathPlays.splice(at, 1);
    deathDropTimers(play);
    if (typeof deathSfxStop === "function") deathSfxStop();
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
    /* 这一座位还有别的演出在播 ⇒ 属性留给那一条；否则撤掉（灰 + 「阵亡」标签自然回来）。 */
    if (!deathSeatLive(play.seat)) deathSetSeatFlag(play.seat, null);
    if (!deathPlays.length) deathStopFrame();
    deathFxMark(deathLayer());
    /* 收尾后重贴一次：座位节点可能刚被重绘重建（属性是在开演时贴的）。 */
    deathFxSync();
  }

  function renderDeathFrame(now) {
    if (!deathPlays.length) return;
    var at = now === undefined ? deathNow() : now;
    for (var i = 0; i < deathPlays.length; i++) {
      var play = deathPlays[i];
      var seconds = Math.min(
        Math.max(0, Number(at) - Number(play.startedAt)) / 1000,
        play.duration / 1000
      );
      if (play.fx && typeof play.fx.draw === "function") play.fx.draw(seconds);
    }
  }

  function deathRequestFrame() {
    if (deathFrame !== null) return;
    var raf = window.requestAnimationFrame;
    if (typeof raf !== "function") return;
    deathFrame = raf(function () {
      deathFrame = null;
      renderDeathFrame();
      if (deathPlays.length) deathRequestFrame();
    });
  }

  function deathStopFrame() {
    if (deathFrame === null) return;
    if (window.cancelAnimationFrame) window.cancelAnimationFrame(deathFrame);
    deathFrame = null;
  }

  /* -------------------------------------------------- 触发 ---- */

  /* 阵亡在记录流里**有真身**：`enter_death`（伤害致死）与 `pass_away`（技能直接致死）压同一个
   * `FrameKind.DEATH`，记录都是 `{kind: "timing", timing: "DEATH_BEFORE_START", actor: 死者}`。
   * **不要**用 `hp_change` 到 0 去猜：护甲全吸收、濒死被救、一次掉多点都会猜错，而且错了没有
   * 任何报错。 */
  function deathSeatOf(record) {
    if (!record || record.kind !== "timing") return null;
    if (record.timing !== "DEATH_BEFORE_START") return null;
    var seat = Number(record.actor);
    return isFinite(seat) ? seat : null;
  }

  /* `fireDeathEventsThrough(seq)` 的入参是**这一条记录自己的 seq**（`session.js:stepPlayback` 在
   * `fireDamageEventsThrough(record.seq)` 之后调）。阵亡事实就在记录里，没有派生通道，故这里按
   * seq 从**正在回放的那一批**里取回记录再判（探针也可以直接传记录对象）。
   * 返回取回的那条记录（找不到特殊字段时返回 `null`）。 */
  function deathRecordAt(seq) {
    if (typeof state === "undefined" || !state || !state.playback) return null;
    var playback = state.playback;
    var records = playback.records || [];
    var index = Number(playback.index);
    if (isFinite(index) && records[index] && Number(records[index].seq) === Number(seq)) {
      return records[index];
    }
    for (var i = 0; i < records.length; i++) {
      if (records[i] && Number(records[i].seq) === Number(seq)) return records[i];
    }
    return null;
  }

  function fireDeathEventsThrough(input) {
    var given = input && typeof input === "object" ? input : null;
    var seq = Number(given ? given.seq : input);
    if (!isFinite(seq)) return null;
    var record = given || deathRecordAt(seq);
    var seat = deathSeatOf(record);
    if (seat === null) return null;
    if (deathCursor !== null && !(seq > Number(deathCursor))) return null;
    deathCursor = seq;
    /* 在播上限：第 3 条**丢掉**（不排队等）。 */
    if (deathPlays.length >= DEATH_FX_MAX) {
      deathDroppedCount += 1;
      deathFxMark(deathLayer());
      return null;
    }
    return startDeathPlay({ seat: seat, seq: seq });
  }

  /* -------------------------------------------------- 重绘之后 ---- */

  function deathDropStalePlays() {
    if (!deathPlays.length) return;
    var layer = deathLayer();
    if (!layer || typeof rectOf !== "function") { clearDeathFx(); return; }
    var layerRect = rectOf(layer);
    var stale = [];
    for (var i = 0; i < deathPlays.length; i++) {
      var play = deathPlays[i];
      var box = deathStageOf(play.seat, layerRect);
      if (!box || deathBoxKey(box) !== play.boxKey) stale.push(play);
    }
    for (var j = 0; j < stale.length; j++) finishDeathPlay(stale[j]);
  }

  function deathFxSyncBody() {
    var layer = deathLayer();
    /* 只加载本文件的探针（静态用例的 node sandbox）里没有布局工具：什么都不做，不抛错。 */
    if (typeof heroBlockFor !== "function" || typeof rectOf !== "function") return;
    if (!layer) {
      if (deathPlays.length) clearDeathFx();
      return;
    }
    /* 座位节点每次重绘都会重建 ⇒ `hold` / `gone` 属性重贴一遍。 */
    for (var i = 0; i < deathPlays.length; i++) {
      deathSetSeatFlag(deathPlays[i].seat, deathPlays[i].phase);
    }
    deathDropStalePlays();
    deathFxMark(layer);
  }

  /* `renderTable` 末尾调用（每次重绘都要，否则一步回放就把属性打回原点）。
   *
   * 重入：`deathDropStalePlays` 里的 `finishDeathPlay` 会回头调本函数。这里记一个脏标记，
   * 在当前这趟收尾后**再跑一遍**（摘完就没有新的了，不会转圈）。 */
  function deathFxSync() {
    if (deathSyncBusy) {
      deathSyncDirty = true;
      return;
    }
    deathSyncBusy = true;
    try {
      do {
        deathSyncDirty = false;
        deathFxSyncBody();
      } while (deathSyncDirty);
    } finally {
      deathSyncBusy = false;
    }
  }

  /* 换局清场：游标 / 画布 / 计时器 / 属性 / 声音一起收掉（`commitView` 调用）。 */
  function clearDeathFx() {
    deathCursor = null;
    if (typeof deathSfxStop === "function") deathSfxStop();
    for (var i = 0; i < deathPlays.length; i++) {
      var play = deathPlays[i];
      deathDropTimers(play);
      if (play.canvas && play.canvas.parentNode) play.canvas.parentNode.removeChild(play.canvas);
      play.canvas = null;
      deathSetSeatFlag(play.seat, null);
    }
    deathPlays.length = 0;
    deathStopFrame();
    var layer = deathLayer();
    if (layer) {
      while (layer.children && layer.children.length) layer.removeChild(layer.children[0]);
    }
    /* 座位上的 `data-death-fx` 一律清干净：属性留着会让一张灰牌**永远不显示**（或一张活牌
     * **永远不灰**）——spec §硬约束。宁可多扫一遍所有座位，也不漏一个。 */
    if (typeof seatNodes === "function") {
      var nodes = seatNodes();
      for (var n = 0; n < nodes.length; n++) {
        if (nodes[n] && nodes[n].removeAttribute) nodes[n].removeAttribute("data-death-fx");
      }
    } else {
      var view = typeof tableViewForRender === "function" ? tableViewForRender() : null;
      var snapshot = (view && view.snapshot) || (state && state.view && state.view.snapshot) || null;
      var players = (snapshot && snapshot.players) || [];
      for (var p = 0; p < players.length; p++) {
        var node = deathSeatNodeOf(players[p].seat);
        if (node && node.removeAttribute) node.removeAttribute("data-death-fx");
      }
    }
    deathFxMark(layer);
  }

  /* ------------------------------------------------------------ 导出 ---- */
  global.SgsDeathFX = {
    create: create,
    /* 单时间轴：字段名 / 内置默认 / 钳制的唯一真源（`anim.ini` 与它逐键同值）。 */
    gameTiming: gameTiming,
    DEFAULT_TIMING: DEFAULT_TIMING,
    TIMING_PREFIX: TIMING_PREFIX,
    TIMING_FIELDS: TIMING_FIELDS,
    TIMING_KEYS: TIMING_KEYS,
    MIN_TOTAL_MS: MIN_TOTAL_MS,
    MAX_TOTAL_MS: MAX_TOTAL_MS,
    PARAMS: PARAMS,
    COLORS: C,
    /* 排播口径（烟测 / 实机读数用；改口径只改上面那个常量）。 */
    MAX_PLAYS: DEATH_FX_MAX,
    /* 三个钩子（调用方显式调）：一条记录到点 / 重绘之后 / 换局清场。 */
    deathSeatOf: deathSeatOf,
    fireDeathEventsThrough: fireDeathEventsThrough,
    deathFxSync: deathFxSync,
    clearDeathFx: clearDeathFx,
    deathPlays: function () { return deathPlays.slice(); }
  };
})(window);

/* 三个排播钩子挂成**全局名**（与 `damage-fx.js` / `fx-chain.js` 同姿态）：调用方是
 * `session.js` 的 `stepPlayback` / `commitView` 与 `table.js` 的 `renderTable`，它们直接叫名字。
 * 内部状态（在播表 / 计时器 / 游标）仍住上面那个闭包里——这三行只是同一批函数的转手壳，
 * **不**另存一份状态。
 *
 * 为什么在闭包外写成顶层 `var`：普通脚本的顶层 `var` 就是全局绑定（浏览器 = `window`）；
 * 写成 `window.xxx = …` 在垫片（`window` 是另一个对象）里叫不到，两边的加载方式必须一致。 */
var fireDeathEventsThrough = function (seqOrRecord) {
  return window.SgsDeathFX.fireDeathEventsThrough(seqOrRecord);
};
var deathFxSync = function () {
  return window.SgsDeathFX.deathFxSync();
};
var clearDeathFx = function () {
  return window.SgsDeathFX.clearDeathFx();
};
