"use strict";
/* ============================================================================
 * 三国杀 · 武将牌「技能发动」水墨艺术字演出
 *
 * 武将发动技能时，在武将牌正中**写**出技能名：
 *   一点墨先在纸里洇开 → 行楷逐字落下 → 一笔圆相从左上扫到右上（没有笔，只有墨）
 *   → 朱印「技」压角 → 停一口气 → 整幅墨迹化开、被纸吸走。
 *
 * 纯 Canvas 2D，零依赖、零请求、零外部图片 / 字体文件（字体走系统字体栈）。
 * 画面只由秒数 t 决定（随机数固定种子，种子由技能名算出来），同一个 t 永远画出同一帧；
 * 同名技能每次的圆相抖动 / 飞白 / 溅点完全一样，不同名的技能各有一套墨迹。
 *
 * 接口
 *   var fx = SgsSkillInkFX.create(canvas, {
 *     name: "制衡",              // 技能名（2~4 字最佳；≥5 字自动横排）
 *     dpr: 2,                    // 后备缓冲像素比（缺省 min(2, devicePixelRatio)）
 *     w: 460, h: 644,            // 画布 CSS 尺寸（缺省取 clientWidth / clientHeight）
 *     seal: "技",                // 角落朱印；空串 = 不落印
 *     layout: "auto",           // "auto" | "vertical" | "horizontal"
 *     blend: true                // 只读标记：为 true 时建议画布挂 mix-blend-mode: multiply
 *   });
 *   fx.setName("苦肉");           // 换技能名：重新排版 / 烘字（画布与 dpr 不变）
 *   fx.resize(w, h);              // 换尺寸（版式变了才需要）
 *   fx.draw(seconds);             // 画面只吃 seconds；0 ≤ seconds ≤ duration
 *   fx.duration;                  // 2.05
 *   fx.width / fx.height          // 虚拟牌面尺寸（默认 460 × 644）
 *   SgsSkillInkFX.layoutFor(name, w, h)   // 纯几何（自检 / 文档用）
 *   SgsSkillInkFX.PARAMS                  // 时间表（音效从同一份读）
 *
 * 牌桌接法：canvas 铺满**武将块**（.hero-block），CSS 尺寸 = 武将块尺寸，
 * 建议 `mix-blend-mode: multiply`，这样墨会压在牌面上而不是浮在上面。
 * 本文件不接管 DOM、不发请求、不读全局状态。
 * ========================================================================== */
(function (global) {
  "use strict";

  var TAU = Math.PI * 2;

  /* ---------------------------------------------------------------- 字体 ----
   * 题字走行楷（Windows 华文行楷 / 方正行楷 / macOS 楷体回退）：
   * 行楷有连笔与提按，放大到 100px 以上才像"写"的；宋体放大会变成刻的。
   * 与 SGS `outputs/sgs-ink-ui` 的字体栈同源，回退顺序不变。 */
  var XING = '"STXingkai","FZSTK","华文行楷","STKaiti","KaiTi","Kaiti SC","Noto Serif SC","Songti SC",serif';
  var LISHU = '"STLiti","FZSTK","STXingkai","STKaiti","KaiTi",serif';

  /* ---------------------------------------------------------------- 配色 ----
   * 墨不是纯黑：焦墨偏暖（纸是暖白的，纯黑会像贴纸）。朱印取 SGS 的 accent。 */
  var C = {
    ink: "22,17,13",          /* 焦墨 */
    inkEdge: "52,37,24",      /* 洇开的墨边 */
    inkWash: "48,40,32",      /* 纸里那层淡墨 */
    red: "#a63425",           /* 朱砂 */
    redDark: "#8f2619",
    paper: "#fbf6ea"          /* 印文（挖白） */
  };

  /* ================================================================
   * 时间表（音效读同一份，不另抄一份）
   *   nameAt      第一字落纸；nameStagger 逐字错开；nameDur 单字洇开
   *   ringAt      圆相起笔；ringDur 走完一圈（含 4.5% 过笔）
   *   sealAt      朱印落下
   *   fadeAt      开始化墨；fadeDur 化尽
   * ============================================================== */
  var PARAMS = {
    duration: 2.05,
    nameAt: 0.02,
    nameStagger: 0.055,
    nameDur: 0.20,
    ringAt: 0.13,
    ringDur: 0.30,
    ringTurns: 1.045,
    ringStart: -2.15,
    sealAt: 0.50,
    sealDur: 0.10,
    fadeAt: 1.46,
    fadeDur: 0.59
  };

  /* ------------------------------------------------------------ 小工具 ---- */
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ss(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function eOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function eOutSoft(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 2.2); }

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

  function hashText(text) {
    var h = 2166136261 >>> 0;
    for (var i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 16777619) >>> 0;
    }
    return h >>> 0;
  }

  function hash2(x, y, seed) {
    var h = Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul(seed | 0, 1442695041);
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }

  function vnoise(x, y, seed) {
    var xi = Math.floor(x), yi = Math.floor(y);
    var xf = x - xi, yf = y - yi;
    var u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
    var a = hash2(xi, yi, seed), b = hash2(xi + 1, yi, seed);
    var c = hash2(xi, yi + 1, seed), d = hash2(xi + 1, yi + 1, seed);
    return lerp(lerp(a, b, u), lerp(c, d, u), v);
  }

  function fbm(x, y, seed) {
    return vnoise(x, y, seed) * 0.55
      + vnoise(x * 2.1, y * 2.1, seed + 11) * 0.28
      + vnoise(x * 4.3, y * 4.3, seed + 23) * 0.17;
  }

  function makeCanvas(w, h) {
    var cv = document.createElement("canvas");
    cv.width = Math.max(1, Math.ceil(w));
    cv.height = Math.max(1, Math.ceil(h));
    return cv;
  }

  var FILTER_OK = (function () {
    try {
      var c = makeCanvas(2, 2).getContext("2d");
      c.filter = "blur(1px)";
      return c.filter === "blur(1px)";
    } catch (e) { return false; }
  })();

  /* 一块可平铺的墨粒噪声：用来咬出飞白 / 纸纹，也用来做化墨时的溶解遮罩。 */
  function noiseTile(size, seed, freq) {
    var cv = makeCanvas(size, size);
    var c = cv.getContext("2d");
    var img = c.createImageData(size, size);
    var d = img.data;
    for (var y = 0; y < size; y++) {
      for (var x = 0; x < size; x++) {
        var n = fbm((x / size) * freq, (y / size) * freq, seed);
        var i = (y * size + x) * 4;
        d[i] = 255; d[i + 1] = 255; d[i + 2] = 255;
        d[i + 3] = Math.round(clamp((n - 0.18) / 0.82, 0, 1) * 255);
      }
    }
    c.putImageData(img, 0, 0);
    return cv;
  }

  /* 化墨遮罩：把噪声按阈值切成"整块消失"的斑块。
   * 同一张图里阈值越高、吃掉的面积越大；相邻两张按小数混合，化墨就不会一格一格跳。 */
  function dissolveMask(cw, ch, seed, threshold) {
    var cv = makeCanvas(cw, ch);
    var c = cv.getContext("2d");
    var img = c.createImageData(cw, ch);
    var d = img.data;
    for (var y = 0; y < ch; y++) {
      for (var x = 0; x < cw; x++) {
        var n = fbm((x / cw) * 5.2 + 13.7, (y / ch) * 5.2 + 4.1, seed);
        var a = 1 - smooth01(threshold - 0.045, threshold + 0.045, n);
        var i = (y * cw + x) * 4;
        d[i] = 255; d[i + 1] = 255; d[i + 2] = 255;
        d[i + 3] = Math.round(a * 255);
      }
    }
    c.putImageData(img, 0, 0);
    return cv;
  }

  function smooth01(a, b, x) {
    var t = clamp((x - a) / (b - a), 0, 1);
    return t * t * (3 - 2 * t);
  }

  /* ================================================================
   * 版式：只算几何，不碰 canvas。
   *   竖排（1~3 字）：字在牌面中线从上往下排，圆相直径约 0.85 牌宽；
   *   横排（≥4 字）：一行居中，圆相直径按字宽涨，夹在牌面内。
   * 圆的半径取"包住字块 + 0.55 字"和牌面 0.47 短边的较小者。
   * ============================================================== */
  function layoutFor(name, w, h, mode) {
    var text = String(name == null ? "" : name).replace(/\s+/g, "");
    if (!text) text = "技能";
    var chars = Array.from(text);
    var n = chars.length;
    var horizontal = mode === "horizontal" || (mode !== "vertical" && n >= 4);
    var size, lineH = 0;
    if (!horizontal) {
      if (n === 1) size = w * 0.30;
      else if (n === 2) size = w * 0.25;
      else size = w * 0.19;
      size = Math.min(size, h * 0.20);
    } else {
      size = Math.min(w * 0.165, (w * 0.70) / n);
    }
    var cx = w * 0.5, cy = h * 0.47;
    var out = [];
    var halfW, halfH;
    if (!horizontal) {
      lineH = size * 1.07;
      halfW = size * 0.5;
      halfH = (n - 1) * lineH * 0.5 + size * 0.5;
      for (var i = 0; i < n; i++) {
        out.push({ ch: chars[i], x: cx, y: cy + (i - (n - 1) / 2) * lineH });
      }
    } else {
      halfW = (n * size) * 0.5;
      halfH = size * 0.5;
      for (var j = 0; j < n; j++) {
        out.push({ ch: chars[j], x: cx + (j - (n - 1) / 2) * size, y: cy });
      }
    }
    var R = Math.max(halfW, halfH) + size * 0.55;
    R = Math.min(R, Math.min(w, h) * 0.44);
    R = Math.max(R, size * 1.02);
    var sealSize = Math.min(w, h) * 0.085;
    /* 三字竖排时名字更长，朱印仍压在名下会碰到最后一个字：往下让到环底内侧。
       二字 / 四字保持原位置（这次不重排已验收的两档）。 */
    var sealY = cy + R * 0.76;
    if (n === 3 && !horizontal) sealY = Math.max(sealY, cy + halfH + sealSize * 0.55);
    return {
      text: text, chars: out, n: n, horizontal: horizontal,
      size: size, lineH: lineH, cx: cx, cy: cy,
      halfW: halfW, halfH: halfH, R: R,
      sealSize: sealSize,
      sealX: cx,
      sealY: sealY,
      seed: hashText(text)
    };
  }

  /* ================================================================
   * 烘字：每个字一张贴图（核心 + 洇边 + 纸纹咬边 + 溅点）
   *   pad 留出洇开与飞溅的位置；字心按真实墨迹包围盒居中（行楷的墨不一定在 em 框中心）
   * ============================================================== */
  function bakeGlyph(ch, size, seed, dpr, grain) {
    var pad = size * 0.34;
    var unit = size + pad * 2;
    var px = Math.ceil(unit * dpr);
    var cv = makeCanvas(px, px);
    var c = cv.getContext("2d", { willReadFrequently: false });
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.font = size + "px " + XING;
    c.textAlign = "center";
    c.textBaseline = "alphabetic";
    var m;
    try { m = c.measureText(ch); } catch (e) { m = null; }
    var asc = m && isFinite(m.actualBoundingBoxAscent) ? m.actualBoundingBoxAscent : size * 0.78;
    var desc = m && isFinite(m.actualBoundingBoxDescent) ? m.actualBoundingBoxDescent : size * 0.22;
    var mid = unit * 0.5;
    var baseY = mid + (asc - desc) * 0.5;
    var rnd = rng(seed);

    /* 1. 纸里先洇一层：宽而淡的暖墨，让字有"吸进去"的底 */
    c.save();
    if (FILTER_OK) c.filter = "blur(" + (size * 0.085) + "px)";
    c.fillStyle = "rgba(" + C.inkEdge + ",0.20)";
    c.fillText(ch, mid, baseY);
    c.restore();

    /* 2. 湿边：紧贴笔画的一圈淡墨 */
    c.save();
    if (FILTER_OK) c.filter = "blur(" + (size * 0.026) + "px)";
    c.fillStyle = "rgba(" + C.ink + ",0.34)";
    c.fillText(ch, mid, baseY);
    c.restore();

    /* 3. 焦墨字心：上浓下淡，像一笔蘸饱了墨写下来 */
    var grad = c.createLinearGradient(0, mid - size * 0.62, 0, mid + size * 0.62);
    grad.addColorStop(0, "rgba(" + C.ink + ",0.99)");
    grad.addColorStop(0.62, "rgba(" + C.ink + ",0.96)");
    grad.addColorStop(1, "rgba(38,28,20,0.90)");
    c.fillStyle = grad;
    c.fillText(ch, mid, baseY);

    /* 4. 纸纹咬边：destination-out 让纸从笔画里透出来（干笔的颗粒） */
    c.save();
    c.globalCompositeOperation = "destination-out";
    c.globalAlpha = 0.42;
    c.drawImage(grain, 0, 0, unit, unit);
    c.globalAlpha = 0.16;
    c.drawImage(grain, unit * 0.17, unit * 0.11, unit * 0.9, unit * 0.9);
    c.restore();

    /* 5. 飞白：顺着手写方向擦几道细缝，越靠下越枯 */
    c.save();
    c.globalCompositeOperation = "destination-out";
    c.lineCap = "round";
    var streaks = 7 + Math.floor(rnd() * 5);
    for (var s = 0; s < streaks; s++) {
      var sx = mid + (rnd() - 0.5) * size * 0.82;
      var sy = mid + (rnd() - 0.5) * size * 0.86;
      var len = size * (0.16 + rnd() * 0.38);
      var ang = -1.16 + (rnd() - 0.5) * 0.34;
      c.globalAlpha = 0.12 + rnd() * 0.24;
      c.lineWidth = size * (0.006 + rnd() * 0.016);
      c.beginPath();
      c.moveTo(sx, sy);
      c.quadraticCurveTo(
        sx + Math.cos(ang) * len * 0.5 + (rnd() - 0.5) * size * 0.04,
        sy + Math.sin(ang) * len * 0.5,
        sx + Math.cos(ang) * len,
        sy + Math.sin(ang) * len
      );
      c.stroke();
    }
    c.restore();

    /* 6. 溅点：字脚 / 起笔处甩出去的小墨点；三颗拉长的墨星 */
    c.save();
    c.fillStyle = "rgba(" + C.ink + ",0.9)";
    var specks = 12 + Math.floor(rnd() * 8);
    for (var k = 0; k < specks; k++) {
      var a = rnd() * TAU;
      var dist = size * (0.30 + rnd() * 0.22);
      var px2 = mid + Math.cos(a) * dist;
      var py2 = baseY - size * 0.5 + Math.sin(a) * dist * 0.9;
      var r = size * (0.004 + rnd() * 0.012);
      c.globalAlpha = 0.20 + rnd() * 0.55;
      c.beginPath();
      c.arc(px2, py2, r, 0, TAU);
      c.fill();
    }
    for (var e = 0; e < 3; e++) {
      var ea = -0.5 + rnd() * 0.9;
      var ex = mid + (rnd() - 0.5) * size * 0.72;
      var ey = mid + (rnd() - 0.2) * size * 0.66;
      c.globalAlpha = 0.25 + rnd() * 0.4;
      c.save();
      c.translate(ex, ey);
      c.rotate(ea);
      c.beginPath();
      c.ellipse(0, 0, size * (0.012 + rnd() * 0.02), size * 0.0045, 0, 0, TAU);
      c.fill();
      c.restore();
    }
    c.restore();

    /* 7. 洇开的晕（另一张贴图，出场那一下单独放大淡出） */
    var hpx = Math.ceil(unit * dpr);
    var halo = makeCanvas(hpx, hpx);
    var hc = halo.getContext("2d");
    hc.setTransform(dpr, 0, 0, dpr, 0, 0);
    hc.font = size + "px " + XING;
    hc.textAlign = "center";
    hc.textBaseline = "alphabetic";
    hc.save();
    if (FILTER_OK) hc.filter = "blur(" + (size * 0.115) + "px)";
    hc.fillStyle = "rgba(" + C.ink + ",0.55)";
    hc.fillText(ch, mid, baseY);
    hc.restore();

    return { core: cv, halo: halo, pad: pad, unit: unit, mid: mid, baseY: baseY };
  }

  /* 朱印：粗边方印 + 挖白隶书，印泥不匀靠噪点咬出来。 */
  function bakeSeal(text, size, seed, dpr) {
    var pad = size * 0.22;
    var unit = size + pad * 2;
    var px = Math.ceil(unit * dpr);
    var cv = makeCanvas(px, px);
    var c = cv.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    var rnd = rng(seed);
    var x = pad, y = pad, w = size, h = size;

    /* 印底：两层错开的圆角方，叠出印泥的厚薄 */
    c.save();
    c.fillStyle = "rgba(166,52,37,0.88)";
    c.beginPath();
    c.rect(x + 1, y + 1, w - 2, h - 2);
    c.fill();
    c.fillStyle = "rgba(143,38,25,0.55)";
    c.beginPath();
    c.rect(x + 2.5, y + 1.5, w - 4, h - 2.5);
    c.fill();
    c.restore();

    /* 边缘咬缺：destination-out 小点 / 短划 */
    c.save();
    c.globalCompositeOperation = "destination-out";
    for (var i = 0; i < 90; i++) {
      var edge = rnd();
      var ex, ey;
      if (edge < 0.25) { ex = x + rnd() * w; ey = y + rnd() * 3; }
      else if (edge < 0.5) { ex = x + rnd() * w; ey = y + h - rnd() * 3; }
      else if (edge < 0.75) { ex = x + rnd() * 3; ey = y + rnd() * h; }
      else { ex = x + w - rnd() * 3; ey = y + rnd() * h; }
      c.globalAlpha = 0.25 + rnd() * 0.65;
      c.beginPath();
      c.arc(ex, ey, 0.4 + rnd() * 1.8, 0, TAU);
      c.fill();
    }
    /* 印面下缘的不匀（印泥没蘸满） */
    for (var j = 0; j < 26; j++) {
      c.globalAlpha = 0.08 + rnd() * 0.2;
      c.beginPath();
      c.arc(x + rnd() * w, y + h * (0.62 + rnd() * 0.38), 0.6 + rnd() * 2.2, 0, TAU);
      c.fill();
    }
    c.restore();

    /* 印文：挖白，笔画里也留一点印泥的缺口 */
    if (text) {
      c.save();
      c.font = size * 0.66 + "px " + LISHU;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillStyle = C.paper;
      c.fillText(text, x + w / 2, y + h / 2 + size * 0.02);
      c.restore();
      c.save();
      c.globalCompositeOperation = "destination-out";
      c.globalAlpha = 0.25;
      for (var k = 0; k < 40; k++) {
        c.beginPath();
        c.arc(x + w * (0.2 + rnd() * 0.6), y + h * (0.2 + rnd() * 0.6), 0.3 + rnd() * 1.1, 0, TAU);
        c.fill();
      }
      c.restore();
    }
    return { core: cv, pad: pad, unit: unit };
  }

  /* ================================================================
   * 圆相：把一圈切成 N 段，每段的半径 / 粗细 / 浓淡都先算好。
   *   - 起笔粗、收笔尖，过笔 4.5% 叠回起笔处；
   *   - 半径带两道很轻的起伏 + 逐点细抖（手画的圆不会正）；
   *   - 后 30% 开始"枯"，随机漏掉几段，露出纸色；
   *   - 前 55% 墨最浓，之后慢慢薄下去。
   * ============================================================== */
  function buildRing(L, seed) {
    var rnd = rng(seed);
    var N = 520;
    var sweep = TAU * PARAMS.ringTurns;
    var start = PARAMS.ringStart;
    var pts = [];
    var jitter = [];
    var phase1 = rnd() * TAU, phase2 = rnd() * TAU;
    var driftX = (rnd() - 0.5) * L.R * 0.028;
    var driftY = (rnd() - 0.5) * L.R * 0.028;
    var rot = (rnd() - 0.5) * 0.05;

    var prevJ = 0;
    for (var i = 0; i < N; i++) {
      var u = i / (N - 1);
      prevJ = prevJ * 0.72 + (rnd() - 0.5) * 0.28;
      jitter.push(prevJ);
    }
    var gap = false;
    for (var j = 0; j < N; j++) {
      var u2 = j / (N - 1);
      var a = start + sweep * u2;
      var wob = 1 + 0.012 * Math.sin(a * 3 + phase1) + 0.007 * Math.sin(a * 7 + phase2) + jitter[j] * 0.004;
      var r = L.R * wob;
      var px = L.cx + driftX + Math.cos(a + rot) * r;
      var py = L.cy + driftY + Math.sin(a + rot) * r;
      var taper = 1 - 0.90 * Math.pow(ss(0.52, 1.0, u2), 1.2);
      var ridge = 0.88 + 0.18 * Math.sin(a * 5 + phase2) + 0.10 * Math.sin(a * 13 + phase1)
        + jitter[j] * 0.10;
      var w = L.R * 0.046 * taper * ridge;
      if (w < L.R * 0.0030) w = L.R * 0.0030;
      var alpha = 0.90 * (1 - 0.46 * ss(0.60, 1.0, u2))
        * (0.84 + 0.10 * Math.sin(a * 9 + phase1) + 0.06 * Math.sin(a * 21 + phase2));
      if (u2 < 0.05) alpha *= 0.9 + u2 * 2;
      /* 枯笔：从 68% 开始，成段漏墨；最后 6% 再稀一点 */
      if (u2 > 0.68) {
        if (!gap && rnd() < (u2 - 0.68) * 1.15) {
          gap = true;
          var gl = 2 + Math.floor(rnd() * 5);
          for (var g = 0; g < gl && j + g < N; g++) {
            pts.push({ x: px, y: py, w: w, a: 0, u: u2, gap: true });
          }
          j += gl - 1;
          gap = false;
          continue;
        }
      }
      if (gap) continue;
      pts.push({ x: px, y: py, w: w, a: alpha, u: u2, x0: px, y0: py });
    }

    /* 墨池：起笔那一点最厚，收笔处再挤一小口 */
    var pools = [
      { u: 0.0, r: L.R * 0.062, a: 0.95 },
      { u: 0.34, r: L.R * 0.022, a: 0.30 },
      { u: 0.62, r: L.R * 0.026, a: 0.40 },
      { u: 1.0, r: L.R * 0.020, a: 0.45 }
    ];
    /* 环外溅点：跟着笔势往外甩 */
    var drops = [];
    for (var d = 0; d < 11; d++) {
      var du = 0.12 + rnd() * 0.8;
      var da = start + sweep * du;
      var dist = L.R * (1.04 + rnd() * 0.16);
      drops.push({
        a: da, dist: dist,
        x: L.cx + Math.cos(da + rot) * dist,
        y: L.cy + Math.sin(da + rot) * dist,
        r: L.R * (0.005 + rnd() * 0.012),
        alpha: 0.18 + rnd() * 0.5,
        at: du,
        stretch: 0.5 + rnd() * 1.6,
        ang: da + Math.PI / 2 + (rnd() - 0.5) * 0.5
      });
    }
    /* 起笔的甩墨：几颗明显大一点，落在圆外左上方 */
    for (var f = 0; f < 4; f++) {
      var fa = start - 0.08 + rnd() * 0.3;
      var fd = L.R * (1.02 + rnd() * 0.12);
      drops.push({
        a: fa, dist: fd,
        x: L.cx + Math.cos(fa + rot) * fd,
        y: L.cy + Math.sin(fa + rot) * fd,
        r: L.R * (0.008 + rnd() * 0.011),
        alpha: 0.35 + rnd() * 0.45,
        at: 0.0,
        stretch: 1 + rnd() * 2,
        ang: fa + (rnd() - 0.5) * 0.9
      });
    }
    var fly = [];
    for (var q = 0; q < 6; q++) {
      fly.push({
        u: 0.40 + rnd() * 0.52,
        len: 6 + Math.floor(rnd() * 18),
        width: 0.5 + rnd() * 0.9,
        spread: (rnd() - 0.5) * 1.1,
        alpha: 0.24 + rnd() * 0.4
      });
    }
    return {
      pts: pts, pools: pools, drops: drops, rot: rot, start: start, sweep: sweep,
      fly: fly, grain: (typeof noiseTile === "function" ? noiseTile(220, (seed ^ 0xb5297a4d) >>> 0, 42) : null)
    };
  }

  /* 把一段圆相画进 ringCanvas（device px，1:1 坐标系） */
  function drawRingPath(rc, ring, progress, dpr, L) {
    var pts = ring.pts;
    if (!pts.length || progress <= 0) return;
    var upto = Math.max(1, Math.floor(pts.length * progress));
    rc.setTransform(dpr, 0, 0, dpr, 0, 0);
    rc.lineCap = "round";
    rc.lineJoin = "round";

    function quad(p0, p1, grow, alpha) {
      var dx = p1.x - p0.x, dy = p1.y - p0.y;
      var len = Math.hypot(dx, dy) || 1;
      var nx = -dy / len, ny = dx / len;
      var w0 = p0.w * 0.5 * grow, w1 = p1.w * 0.5 * grow;
      rc.globalAlpha = alpha;
      rc.beginPath();
      rc.moveTo(p0.x + nx * w0, p0.y + ny * w0);
      rc.lineTo(p1.x + nx * w1, p1.y + ny * w1);
      rc.lineTo(p1.x - nx * w1, p1.y - ny * w1);
      rc.lineTo(p0.x - nx * w0, p0.y - ny * w0);
      rc.closePath();
      rc.fill();
    }

    /* 先一层洇开的边（宽、淡、暖），再压焦墨 */
    rc.fillStyle = "rgba(" + C.inkEdge + ",0.9)";
    for (var i = 0; i < upto - 1; i++) {
      var p0 = pts[i], p1 = pts[i + 1];
      if (p0.gap || p1.gap || p0.a <= 0) continue;
      quad(p0, p1, 2.1, 0.13 * p0.a);
      quad(p0, p1, 1.25, 0.22 * p0.a);
    }
    rc.fillStyle = "rgba(" + C.ink + ",1)";
    for (var j = 0; j < upto - 1; j++) {
      var q0 = pts[j], q1 = pts[j + 1];
      if (q0.gap || q1.gap || q0.a <= 0) continue;
      quad(q0, q1, 1, q0.a);
      if (q0.w > L.R * 0.02) {
        rc.globalAlpha = q0.a;
        rc.beginPath();
        rc.arc(q0.x, q0.y, q0.w * 0.5, 0, TAU);
        rc.fill();
      }
    }

    /* 起笔墨池 + 过笔处的一小口墨 */
    for (var k = 0; k < ring.pools.length; k++) {
      var pool = ring.pools[k];
      if (progress < pool.u + 0.02 && pool.u > 0) continue;
      var idx = Math.min(pts.length - 1, Math.round(pool.u * (pts.length - 1)));
      var pp = pts[idx];
      if (!pp) continue;
      var r = pool.r * (0.7 + 0.3 * Math.min(1, progress * 4));
      var g = rc.createRadialGradient(pp.x, pp.y, 0, pp.x, pp.y, r);
      g.addColorStop(0, "rgba(" + C.ink + "," + (pool.a * 0.95) + ")");
      g.addColorStop(0.55, "rgba(" + C.ink + "," + (pool.a * 0.5) + ")");
      g.addColorStop(1, "rgba(" + C.inkEdge + ",0)");
      rc.globalAlpha = 1;
      rc.fillStyle = g;
      rc.beginPath();
      rc.arc(pp.x, pp.y, r, 0, TAU);
      rc.fill();
    }

    /* 铁线里的纸纹：整圈咬一层细颗粒（只咬 ringCanvas，不动字） */
    if (progress > 0.02) {
      rc.globalCompositeOperation = "destination-out";
      rc.globalAlpha = 0.34;
      rc.drawImage(ring.grain, L.cx - L.R * 1.12, L.cy - L.R * 1.12, L.R * 2.24, L.R * 2.24);
      rc.globalAlpha = 0.12;
      rc.drawImage(ring.grain, L.cx - L.R * 0.7, L.cy - L.R * 0.9, L.R * 1.9, L.R * 1.9);
      rc.globalCompositeOperation = "source-over";
      rc.globalAlpha = 1;
    }

    /* 飞白：沿笔势在环的后半段擦开几道，墨断而气不断 */
    if (progress > 0.45) {
      rc.globalCompositeOperation = "destination-out";
      rc.strokeStyle = "rgba(0,0,0,0.6)";
      rc.lineCap = "round";
      var fly = ring.fly || [];
      for (var fi = 0; fi < fly.length; fi++) {
        var fse = fly[fi];
        if (progress < fse.u) continue;
        var i0 = Math.round(fse.u * (pts.length - 1));
        var i1 = Math.min(pts.length - 1, i0 + fse.len);
        if (i0 >= pts.length - 1) continue;
        var a0 = pts[i0], a1 = pts[i1];
        var dx = a1.x - a0.x, dy = a1.y - a0.y;
        var dl = Math.hypot(dx, dy) || 1;
        var nx = -dy / dl, ny = dx / dl;
        var spread = (a0.w * 0.5) * fse.spread;
        rc.globalAlpha = fse.alpha;
        rc.lineWidth = Math.max(0.6, a0.w * fse.width);
        rc.beginPath();
        rc.moveTo(a0.x + nx * spread, a0.y + ny * spread);
        rc.quadraticCurveTo(
          (a0.x + a1.x) / 2 + nx * spread * 0.35,
          (a0.y + a1.y) / 2 + ny * spread * 0.35,
          a1.x + nx * spread * 0.2,
          a1.y + ny * spread * 0.2
        );
        rc.stroke();
      }
      rc.globalCompositeOperation = "source-over";
      rc.globalAlpha = 1;
    }

    /* 甩出去的墨点：只画进度已经过的 */
    for (var d = 0; d < ring.drops.length; d++) {
      var drop = ring.drops[d];
      if (progress < drop.at - 0.01) continue;
      var pop = clamp((progress - drop.at) / 0.08, 0, 1);
      rc.save();
      rc.globalAlpha = drop.alpha * pop;
      rc.translate(drop.x, drop.y);
      rc.rotate(drop.ang);
      rc.scale(1, 1 / drop.stretch);
      rc.fillStyle = "rgba(" + C.ink + ",0.9)";
      rc.beginPath();
      rc.arc(0, 0, drop.r * (0.7 + 0.3 * pop), 0, TAU);
      rc.fill();
      rc.restore();
    }

    /* 收笔后往下滴的一小注墨：让"停住"的那一秒不是死画面 */
    if (progress > 0.985) {
      var dripU = 0.985;
      var di = Math.round(dripU * (pts.length - 1));
      var dp = pts[di];
      var grow = ss(0, 1, clamp((progress - 0.985) / 0.06, 0, 1));
      if (dp) {
        var dl = L.R * 0.16 * grow;
        var dw = Math.max(1, L.R * 0.008 * (1 - 0.5 * grow));
        rc.globalAlpha = 0.55;
        rc.fillStyle = "rgba(" + C.ink + ",0.85)";
        rc.beginPath();
        rc.moveTo(dp.x - dw, dp.y);
        rc.quadraticCurveTo(dp.x - dw * 0.4, dp.y + dl * 0.6, dp.x, dp.y + dl);
        rc.quadraticCurveTo(dp.x + dw * 0.4, dp.y + dl * 0.6, dp.x + dw, dp.y);
        rc.closePath();
        rc.fill();
        rc.beginPath();
        rc.arc(dp.x, dp.y + dl, dw * 1.8, 0, TAU);
        rc.fill();
      }
    }
  }

  /* ================================================================
   * 主对象
   * ============================================================== */
  function create(canvas, opts) {
    opts = opts || {};
    var ctx = canvas.getContext("2d");
    var dpr = clamp(Number(opts.dpr) || (global.devicePixelRatio || 1), 1, 3);
    var w = Number(opts.w) || canvas.clientWidth || 460;
    var h = Number(opts.h) || canvas.clientHeight || 644;
    var mode = opts.layout || "auto";
    var sealText = opts.seal === undefined ? "技" : String(opts.seal || "");
    var name = String(opts.name == null ? "制衡" : opts.name);

    var layer = makeCanvas(1, 1);
    var ringCv = makeCanvas(1, 1);
    var ringBaked = makeCanvas(1, 1);
    var washBaked = makeCanvas(1, 1);
    var lctx = layer.getContext("2d");
    var rctx = ringCv.getContext("2d");

    var L, glyphs, seal, ring, grain, seed, wipe = [];

    function resize(nextW, nextH) {
      w = Math.max(1, Number(nextW) || w);
      h = Math.max(1, Number(nextH) || h);
      canvas.width = Math.max(1, Math.round(w * dpr));
      canvas.height = Math.max(1, Math.round(h * dpr));
      layer.width = canvas.width;
      layer.height = canvas.height;
      ringCv.width = canvas.width;
      ringCv.height = canvas.height;
      ringBaked.width = canvas.width;
      ringBaked.height = canvas.height;
      washBaked.width = canvas.width;
      washBaked.height = canvas.height;
      L = layoutFor(name, w, h, mode);
      seed = L.seed;
      grain = noiseTile(192, seed ^ 0x9e3779b9, 7.5);
      wipe = [];
      for (var wi = 0; wi <= 10; wi++) {
        wipe.push(dissolveMask(160, 224, (seed ^ 0x51ed270b + wi * 131) >>> 0, wi / 10));
      }
      glyphs = L.chars.map(function (entry, i) {
        var g = bakeGlyph(entry.ch, L.size, (seed + i * 7919) >>> 0, dpr, grain);
        var rnd = rng((seed + i * 104729) >>> 0);
        return {
          ch: entry.ch,
          x: entry.x, y: entry.y,
          rot: (rnd() - 0.5) * 0.045,
          dx: (rnd() - 0.5) * L.size * 0.035,
          dy: (rnd() - 0.5) * L.size * 0.028,
          core: g.core, halo: g.halo, pad: g.pad, unit: g.unit
        };
      });
      seal = sealText ? bakeSeal(sealText, L.sealSize, (seed ^ 0x7f4a7c15) >>> 0, dpr) : null;
      ring = buildRing(L, (seed ^ 0x2545f491) >>> 0);
      bakeRing();
      bakeWash();
    }

    /* 圆相只画一次，之后按角度裁出一段来"放"——每帧不再重画 500 段 + 两次模糊 */
    function bakeRing() {
      var bc = ringBaked.getContext("2d");
      bc.setTransform(1, 0, 0, 1, 0, 0);
      bc.clearRect(0, 0, ringBaked.width, ringBaked.height);
      rctx.setTransform(1, 0, 0, 1, 0, 0);
      rctx.clearRect(0, 0, ringCv.width, ringCv.height);
      drawRingPath(rctx, ring, 1, dpr, L);
      bc.save();
      if (FILTER_OK) {
        bc.filter = "blur(" + Math.max(0.35, 0.55 * dpr) + "px)";
        bc.globalAlpha = 0.97;
        bc.drawImage(ringCv, 0, 0);
        bc.filter = "blur(" + (3.2 * dpr) + "px)";
        bc.globalAlpha = 0.16;
        bc.drawImage(ringCv, 0, 0);
        bc.filter = "none";
      } else {
        bc.globalAlpha = 0.96;
        bc.drawImage(ringCv, 0, 0);
      }
      bc.restore();
    }

    /* 淡墨底也是静的：烘一次，每帧只改透明度 */
    function bakeWash() {
      var wc = washBaked.getContext("2d");
      wc.setTransform(1, 0, 0, 1, 0, 0);
      wc.clearRect(0, 0, washBaked.width, washBaked.height);
      wc.setTransform(dpr, 0, 0, dpr, 0, 0);
      var R = L.R;
      for (var i = 0; i < 7; i++) {
        var a = i * 1.37 + 0.6;
        var ox = Math.cos(a) * R * 0.44;
        var oy = Math.sin(a) * R * 0.34;
        var rr = R * (0.36 + (i % 4) * 0.08);
        var g = wc.createRadialGradient(L.cx + ox, L.cy + oy, 0, L.cx + ox, L.cy + oy, rr);
        g.addColorStop(0, "rgba(" + C.inkWash + ",0.034)");
        g.addColorStop(0.55, "rgba(" + C.inkWash + ",0.014)");
        g.addColorStop(1, "rgba(" + C.inkWash + ",0)");
        wc.fillStyle = g;
        wc.beginPath();
        wc.arc(L.cx + ox, L.cy + oy, rr, 0, TAU);
        wc.fill();
      }
    }

    resize(w, h);

    function setName(nextName) {
      name = String(nextName == null ? "" : nextName) || "技能";
      resize(w, h);
    }

    function drawWash(c, alpha) {
      if (alpha <= 0) return;
      c.save();
      c.setTransform(1, 0, 0, 1, 0, 0);
      c.globalAlpha = alpha;
      c.drawImage(washBaked, 0, 0);
      c.restore();
    }

    function drawName(c, t) {
      for (var i = 0; i < glyphs.length; i++) {
        var g = glyphs[i];
        var start = PARAMS.nameAt + i * PARAMS.nameStagger;
        var local = clamp((t - start) / PARAMS.nameDur, 0, 1);
        if (local <= 0) continue;
        var ease = eOut(local);
        var gx = g.x + g.dx, gy = g.y + g.dy;
        c.save();
        c.translate(gx, gy);
        c.rotate(g.rot);
        var scale = 1 + 0.11 * (1 - ease);
        var reach = g.unit * 0.5 * scale;
        /* 洇开的晕：先到，随后收进去 */
        c.globalAlpha = (1 - ease) * 0.55 + 0.06;
        c.drawImage(g.halo, -reach, -reach, g.unit * scale, g.unit * scale);
        /* 字心：由湿到定 */
        c.globalAlpha = ease;
        var cs = 1 + 0.035 * (1 - ease);
        var cr = g.unit * 0.5 * cs;
        c.drawImage(g.core, -cr, -cr, g.unit * cs, g.unit * cs);
        c.restore();
      }
    }

    function drawSeal(c, t) {
      if (!seal) return;
      var local = clamp((t - PARAMS.sealAt) / PARAMS.sealDur, 0, 1);
      if (local <= 0) return;
      var ease = eOut(local);
      var scale = 1.42 - 0.42 * ease;
      var alpha = ease * (0.86 + 0.05 * Math.sin(t * 19));
      c.save();
      c.translate(L.sealX, L.sealY);
      c.rotate(-0.075);
      c.globalAlpha = alpha;
      var unit = seal.unit * scale;
      c.drawImage(seal.core, -unit / 2, -unit / 2, unit, unit);
      c.restore();
    }

    function drawMotes(c, t, f) {
      if (f <= 0) return;
      var rnd = rng((seed ^ 0x0f3d1c07) >>> 0);
      var count = 18;
      c.save();
      for (var i = 0; i < count; i++) {
        var a = rnd() * TAU;
        var rr = L.R * (0.18 + rnd() * 0.86);
        var x = L.cx + Math.cos(a) * rr;
        var y = L.cy + Math.sin(a) * rr * 0.9;
        var rise = 18 + rnd() * 46;
        var life = 0.35 + rnd() * 0.6;
        var f2 = clamp((f - 0.04) / life, 0, 1);
        if (f2 <= 0) continue;
        var alpha = (1 - f2) * (0.12 + rnd() * 0.28);
        var rad = 0.5 + rnd() * 1.4;
        c.globalAlpha = alpha;
        c.fillStyle = "rgba(" + C.ink + ",0.9)";
        c.beginPath();
        c.arc(x + Math.sin(t * 3 + i) * 1.8, y - rise * f2, rad, 0, TAU);
        c.fill();
      }
      c.restore();
    }

    function draw(t) {
      t = clamp(Number(t) || 0, 0, PARAMS.duration);
      var pw = canvas.width, ph = canvas.height;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, pw, ph);
      lctx.setTransform(1, 0, 0, 1, 0, 0);
      lctx.clearRect(0, 0, pw, ph);

      var fade = clamp((t - PARAMS.fadeAt) / PARAMS.fadeDur, 0, 1);
      var baseAlpha = t <= 0 ? 0 : 1;

      /* ---- 1. 纸里的淡墨底 ---- */
      lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawWash(lctx, ss(PARAMS.nameAt, PARAMS.nameAt + 0.22, t) * 0.9);

      /* ---- 2. 逐字落墨 ---- */
      drawName(lctx, t);

      /* ---- 3. 圆相：烘好的整圈按角度裁一段；笔尖补一颗湿墨，盖住裁口 ---- */
      var rp = eOutSoft(clamp((t - PARAMS.ringAt) / PARAMS.ringDur, 0, 1));
      if (rp > 0) {
        lctx.save();
        lctx.setTransform(1, 0, 0, 1, 0, 0);
        if (rp < 0.999) {
          var a0 = ring.start + ring.rot - 0.035;
          var a1 = a0 + ring.sweep * Math.min(1, rp) + 0.015;
          lctx.beginPath();
          lctx.moveTo(L.cx * dpr, L.cy * dpr);
          lctx.arc(L.cx * dpr, L.cy * dpr, 460 * 0.62 * dpr, a0, a1, false);
          lctx.closePath();
          lctx.clip();
        }
        lctx.drawImage(ringBaked, 0, 0);
        lctx.restore();

        if (rp < 0.999) {
          /* 领头的一颗墨珠：径向裁口在它后面 */
          var tipA = ring.start + ring.rot + ring.sweep * rp;
          var tipR = L.R * (1 + 0.012 * Math.sin(tipA * 3 + 1.7));
          var tx = L.cx + Math.cos(tipA) * tipR;
          var ty = L.cy + Math.sin(tipA) * tipR;
          var tw = L.R * 0.046 * (1 - 0.9 * Math.pow(ss(0.52, 1.0, rp), 1.2));
          if (tw < L.R * 0.004) tw = L.R * 0.004;
          lctx.save();
          lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
          lctx.globalAlpha = 0.86;
          lctx.fillStyle = "rgba(" + C.ink + ",0.95)";
          lctx.beginPath();
          lctx.arc(tx, ty, Math.max(0.7, tw * 0.58), 0, TAU);
          lctx.fill();
          lctx.globalAlpha = 0.13;
          lctx.beginPath();
          lctx.arc(tx, ty, tw * 1.9, 0, TAU);
          lctx.fill();
          lctx.restore();
        }
      }

      /* ---- 4. 朱印 ---- */
      lctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawSeal(lctx, t);

      /* ---- 5. 化墨：墨先被纸咬出洞，再整幅淡下去 ---- */
      var dissolveAt = clamp((fade - 0.14) / 0.86, 0, 1);
      if (dissolveAt > 0) {
        var slot = dissolveAt * (wipe.length - 1);
        var lo = Math.floor(slot), hi = Math.min(wipe.length - 1, lo + 1);
        var frac = slot - lo;
        lctx.setTransform(1, 0, 0, 1, 0, 0);
        lctx.save();
        lctx.globalCompositeOperation = "destination-out";
        lctx.globalAlpha = 1;
        lctx.drawImage(wipe[lo], 0, 0, pw, ph);
        if (frac > 0.002) {
          lctx.globalAlpha = frac;
          lctx.drawImage(wipe[hi], 0, 0, pw, ph);
        }
        lctx.restore();
      }

      var alpha = baseAlpha * (1 - Math.pow(fade, 1.35));
      if (alpha > 0.001) {
        ctx.save();
        ctx.globalAlpha = alpha;
        ctx.translate(pw * 0.5, ph * 0.5);
        var scale = 1 + 0.016 * fade;
        ctx.scale(scale, scale);
        ctx.translate(-pw * 0.5, -ph * 0.5 - 7 * dpr * fade);
        if (FILTER_OK && fade > 0.06) {
          ctx.filter = "blur(" + (fade * 2.2 * dpr) + "px)";
        }
        ctx.drawImage(layer, 0, 0);
        ctx.filter = "none";
        ctx.restore();
      }

      /* ---- 6. 化墨时浮起来的墨尘 ---- */
      ctx.save();
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      drawMotes(ctx, t, fade);
      ctx.restore();
    }

    return {
      draw: draw,
      setName: setName,
      resize: function (nw, nh) { resize(nw, nh); },
      get duration() { return PARAMS.duration; },
      get width() { return w; },
      get height() { return h; },
      get layout() { return L; },
      get timing() { return PARAMS; }
    };
  }

  global.SgsSkillInkFX = {
    create: create,
    layoutFor: layoutFor,
    PARAMS: PARAMS,
    VERSION: "1.0",
    font: XING
  };
})(window);
