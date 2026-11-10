"use strict";

/* SGS 前端 · **火焰**伤害演出的画面（damage-fx-natures 票 03）：`SgsFireFX`。
 *
 * 移植自素材包 `outputs/sgs-fire-fx/fx-fire.js`（可播放的定稿参考实现）：来源牌上的朱砂
 * 「火」印亮起 → 1~3 档射出一串**火焰羽箭** → 钉进目标牌、溅开火星与焦痕 → 4 档再落一颗
 * **火雨流星**。搬进牌桌时只改五类事，画法逐段保留：
 *
 * 1. **双时间轴**（同 `fx-core.js` / `fx-thunder.js` 的姿态）：`profile: "export"` 照抄素材页
 *    的 4.9~6.2 秒链（`?still` / `?export` / node 探针逐帧导静帧）；`profile: "game"` 读
 *    `anim.ini` 的 `damage_fx_fire_<档>_<字段>`（缺键退 `DEFAULT_TIMING`，钳制住在
 *    `gameTiming` 里）。`TIMING_FIELDS` / `TIMING_KEYS` 由 `DEFAULT_TIMING` 的键**生成**——
 *    字段名只有一处。
 * 2. **牌桌档是「等比重排」，不是「另画一套」**：素材页的整条链按**锚点**（第一箭命中 /
 *    最后一击 / 飘字 / 焦痕淡出 / 收尾）分段线性压到 `anim.ini` 的秒数上（`gameTiming`
 *    返回 `warp`），`draw(t)` 先把演出自己的秒数映射回素材页的秒数再画——于是画法里那些
 *    绝对值（引信提前 0.46s、余烬活 2.2s、爆燃 3.2s 散尽）跟着一起压，不必逐个改常量。
 *    这也是 spec「4 档 2.0s 是从素材页 4.9~6.2s 等比压下来」的机械化版本。
 * 3. **座位舞台**：`stage: {src, dst}` 是覆盖层量出来的武将块矩形（CSS px）。火印锚在来源
 *    牌右上、落点在目标牌偏上（与参考舞台同一比例）。`src === null` ⇒ 沿用剑光先例：来源
 *    整块挪到画面左外 200 单位（库里三个火焰生产者都带来源，这条只为不抛错）。
 * 4. **`bare: true`**（牌桌硬口径）：只出特效，不画宣纸底与样例武将牌；整屏**暖白闪不在
 *    画布内画**（那会盖住牌桌），改由排播层挂着色子层 `.fx-flash-fire`。火印 / 暖光仍然
 *    落在真实武将块上（素材页那条「来源牌发亮」的画法保留）。
 * 5. **飘字**：`amount` 是服务端真实点数（≥5 仍播 4 档），位置按目标牌体力条下沿的硬线
 *    反推（与 `fx-core.js` / `fx-thunder.js` 同一套包围盒判据）。
 *
 * 纯 Canvas 2D，零依赖、零外部资源、不发请求、不碰 DOM。`"use strict";` 是文件第一条语句；
 * 全局名 `window.SgsFireFX`，不改成 ESM。
 */
(function (global) {

  var TAU = Math.PI * 2;

  /* ---------------------------------------------------------------- 配色 ----
   * 宣纸 / 墨 / 朱砂取自 SGS 前端 style.css 的 :root，火焰色另加一组：
   * 焦边（暗朱）→ 朱砂 → 橘 → 金 → 白热。 */
  var C = {
    paper:  "#f1ebdf",
    card:   "#fffdf7",
    cardLo: "#f8f1e2",
    line:   "#ddd8d0",
    ink:    "#1c1a17",
    dim:    "#6f6a62",
    red:    "#9c2b1b",
    wei:    "#2f5d8a",
    bronze: "#7c4a20",
    fire:   "#ef5a1e",
    ember:  "#ff9a3c",
    gold:   "#ffc861",
    hot:    "#fff6e4",
    smoke:  "#31201a"
  };

  var PAL_DEEP = { edge: "rgba(70,14,4,0.80)", mid: "#d63a12", mid2: "#ff8a2a", core: "#ffd27a" };
  var PAL_GOLD = { edge: "rgba(120,40,10,0.55)", mid: "#ff7a24", mid2: "#ffb24e", core: "#fff0c4" };

  var SERIF = '"Noto Serif SC","Songti SC","STSong","SimSun",serif';
  var SANS  = '"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif';

  /* ------------------------------------------------------ 参考舞台（导出档）---- */
  var REF_W = 1920, REF_H = 1080;

  /* 两张武将牌与参考舞台的摆位（与剑光 / 雷电版同一套坐标，方便并排对比）。 */
  var CARD = { w: 460, h: 644, pad: 74 };
  var REF_SRC = { cx: 520,  cy: 545, w: CARD.w, h: CARD.h };
  var REF_DST = { cx: 1400, cy: 545, w: CARD.w, h: CARD.h };

  /* 火印 / 引弓点：来源牌右上部；落点中心：目标牌上半部。 */
  var REF_A = { x: 676, y: 356 };
  var REF_B = { x: 1400, y: 452 };

  /* 两个锚点在**牌面里的比例**：牌桌量出来的武将块大小与参考牌不同，但"火印在来源牌右上、
   * 落点在目标牌偏上"这条构图在任意座位矩形下都成立（同 `fx-thunder.js` 的 ANCHOR）。 */
  var ANCHOR = {
    ax: (REF_A.x - REF_SRC.cx) / CARD.w,   // 0.3391
    ay: (REF_A.y - REF_SRC.cy) / CARD.h,   // -0.2935
    bx: (REF_B.x - REF_DST.cx) / CARD.w,   // 0
    by: (REF_B.y - REF_DST.cy) / CARD.h    // -0.1444
  };

  /* **当前帧**的舞台与几何：每条演出在 `draw()` 开头设置一次（`setFrame`）。draw 是同步的，
   * JS 单线程下不存在"画到一半被另一条演出改掉"的窗口——这组是帧上下文，不是共享状态。
   * 导出档（没有 stage）它们就是参考舞台，于是素材页那条链逐字节不变。 */
  var W = REF_W, H = REF_H;
  var SRC = REF_SRC, DST = REF_DST;
  var A = REF_A, B = REF_B;
  /* `U` = 构图比例（牌比参考牌 460×644 小多少，所有落屏尺寸都乘它）。 */
  var U = 1;

  /* ---------------------------------------------------------------- 武将 ----
   * 这是"样例牌"，只为演示火羽从哪张牌飞向哪张牌；牌面与技能文案不属于特效。 */
  var HEROES = {
    src: {
      name: "神周瑜", faction: "神", factionColor: C.bronze, hp: 4,
      skills: [
        ["琴音", "弃牌阶段弃置两张以上手牌时，令所有角色回复或失去1点体力"],
        ["业炎", "限定技，可对至多三名角色造成火焰伤害"]
      ]
    },
    dst: {
      name: "曹操", faction: "魏", factionColor: C.wei, hp: 4,
      skills: [
        ["奸雄", "受到伤害后，可获得造成伤害的牌"],
        ["护驾", "主公技，魏势力角色可替你打出【闪】"]
      ]
    }
  };

  /* -------------------------------------------------------------- 四档 ----
   * 一档一行的强度表：箭数 / 箭长 / 火星 / 焦痕 / 屏震 / 飘字大小。
   * 所有"越来越强"都只读这张表，画法本身只有一套。
   * 时间点（命中、爆炸、飘字）在下面 `fillExportTimes()` 里按这张表推出来，不手写第二份。
   *
   * `blast` / `meteor` 里的坐标是**参考单位**：`dx` / `dy` 相对落点 B（不是画布绝对值），
   * 流星的 `p0` / `c` 在 `create()` 里按画布宽高比例换成落屏坐标。 */
  var TIERS = {
    1: {
      dmg: 1, name: "烬火", tag: "三支火羽",
      duration: 4.9, fadeStart: 3.0, fadeEnd: 4.6,
      count: 3, gap: 0.100, flight: 0.30, launch0: 0.60,
      len: 152, wid: 12,
      spreadX: 118, spreadY: 108,
      sparkN: 14, splatN: 5, scorch: 0.30,
      shake: 4.5,
      numSize: 152, numEcho: 0, numGlow: 0.70, numLife: 1.90,
      pal: { edge: "rgba(94,26,10,0.55)", mid: "#e8481a", mid2: "#ff9a3c", core: "#fff3cf" },
      blast: null, meteor: null
    },
    2: {
      dmg: 2, name: "烈焰", tag: "六箭连珠",
      duration: 5.2, fadeStart: 3.2, fadeEnd: 4.9,
      count: 6, gap: 0.085, flight: 0.30, launch0: 0.60,
      len: 160, wid: 14,
      spreadX: 156, spreadY: 138,
      sparkN: 20, splatN: 7, scorch: 0.44,
      shake: 7,
      numSize: 184, numEcho: 0, numGlow: 0.82, numLife: 1.95,
      pal: { edge: "rgba(92,24,8,0.60)", mid: "#ee5019", mid2: "#ffa242", core: "#fff1c6" },
      blast: null, meteor: null
    },
    3: {
      dmg: 3, name: "爆燃", tag: "九箭灼爆",
      duration: 5.6, fadeStart: 3.4, fadeEnd: 5.2,
      count: 9, gap: 0.070, flight: 0.28, launch0: 0.60,
      len: 168, wid: 16,
      spreadX: 190, spreadY: 162,
      sparkN: 26, splatN: 9, scorch: 0.60,
      shake: 13,
      numSize: 216, numEcho: 1, numGlow: 1.00, numLife: 2.00,
      pal: { edge: "rgba(90,22,8,0.64)", mid: "#f04a13", mid2: "#ffb050", core: "#fff6d6" },
      // `strobeMs` = 牌桌档缺 key 时整屏着色闪的窗口（素材页的白闪是画布内画的，这里只给默认）
      strobeMs: 160,
      blast: { scale: 0.72, shake: 13, flash: 0.55, dx: 0, dy: 18 },
      meteor: null
    },
    4: {
      dmg: 4, name: "天陨", tag: "十二箭 · 天降陨石",
      duration: 6.2, fadeStart: 4.0, fadeEnd: 5.8,
      count: 12, gap: 0.060, flight: 0.27, launch0: 0.58,
      len: 162, wid: 15,
      spreadX: 208, spreadY: 176,
      sparkN: 22, splatN: 8, scorch: 0.56,
      shake: 11,
      numSize: 256, numEcho: 2, numGlow: 1.40, numLife: 2.20,
      pal: { edge: "rgba(86,20,7,0.66)", mid: "#f2460f", mid2: "#ffb452", core: "#fff8e2" },
      strobeMs: 260,
      blast: { scale: 1.24, shake: 32, flash: 1.00, dx: -2, dy: 0, meteor: true },
      meteor: { skyAt: 1.70, hit: 2.14 }
    }
  };

  /* ------------------------------------------------------------ 小工具 ---- */
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function ss(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function eOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function eBack(t) { t = clamp(t, 0, 1); var c = 2.0; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); }
  function dist(x1, y1, x2, y2) { return Math.sqrt((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1)); }

  /* 固定种子随机（mulberry32）：同一 seed 永远同一串数。 */
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

  function hash(n) {
    var x = Math.sin(n * 127.1 + 311.7) * 43758.5453123;
    return x - Math.floor(x);
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

  function clipCard(c, rect, inset) {
    var p = inset || 0;
    pathRound(c, rect.cx - rect.w / 2 + p, rect.cy - rect.h / 2 + p,
      rect.w - p * 2, rect.h - p * 2, 14);
    c.clip();
  }

  function softGlow(c, x, y, r, color, a) {
    if (r <= 0 || a <= 0) return;
    var g = c.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(color, a));
    g.addColorStop(0.45, rgba(color, a * 0.40));
    g.addColorStop(1, rgba(color, 0));
    c.fillStyle = g;
    c.beginPath();
    c.arc(x, y, r, 0, TAU);
    c.fill();
  }

  /* 不规则墨团：一圈半径带噪声的顶点，再接中点二次曲线闭合成形。 */
  function blobShape(r, n, lo, hi) {
    var pts = [];
    for (var i = 0; i < n; i++) {
      var a = i / n * TAU;
      var k = lo + (hi - lo) * r();
      pts.push([Math.cos(a) * k, Math.sin(a) * k]);
    }
    return pts;
  }

  function smoothPath(c, pts) {
    var n = pts.length;
    var mx = (pts[n - 1][0] + pts[0][0]) / 2, my = (pts[n - 1][1] + pts[0][1]) / 2;
    c.beginPath();
    c.moveTo(mx, my);
    for (var i = 0; i < n; i++) {
      var p = pts[i], q = pts[(i + 1) % n];
      c.quadraticCurveTo(p[0], p[1], (p[0] + q[0]) / 2, (p[1] + q[1]) / 2);
    }
    c.closePath();
  }

  function blobPath(c, shape, x, y, R, rot, sy) {
    var n = shape.length;
    var cs = Math.cos(rot || 0), sn = Math.sin(rot || 0);
    var pts = [];
    for (var i = 0; i < n; i++) {
      var px = shape[i][0] * R, py = shape[i][1] * R * (sy || 1);
      pts.push([x + px * cs - py * sn, y + px * sn + py * cs]);
    }
    smoothPath(c, pts);
  }

  function qbez(p0, c, p1, e) {
    var iu = 1 - e;
    return {
      x: iu * iu * p0.x + 2 * iu * e * c.x + e * e * p1.x,
      y: iu * iu * p0.y + 2 * iu * e * c.y + e * e * p1.y,
      dx: 2 * iu * (c.x - p0.x) + 2 * e * (p1.x - c.x),
      dy: 2 * iu * (c.y - p0.y) + 2 * e * (p1.y - c.y)
    };
  }

  function copy(base, extra) {
    var out = {};
    for (var key in base) {
      if (Object.prototype.hasOwnProperty.call(base, key)) out[key] = base[key];
    }
    for (var more in extra) {
      if (Object.prototype.hasOwnProperty.call(extra, more)) out[more] = extra[more];
    }
    return out;
  }

  /* ================================================================
   * 双时间轴（同 `fx-core.js` / `fx-thunder.js`）：export = 素材页，game = anim.ini
   * ============================================================== */

  /* `anim.ini` 的 `damage_fx_fire_<档>_<字段>` 的**内置默认**（磁盘副本在
   * `tools/sgs_web/static/anim.ini`；缺键 / 解析失败 / 非法值 ⇒ 退回这里）。
   *
   * 字段集的取舍与雷电一致：火不需要剑光的 `split`（剑气劈屏），但需要 `flash`
   * （整屏暖白闪的强度，0..1）。`strobe` 只有 3 / 4 档有——它就是着色闪的**窗口**。
   * 单档总时长上限同剑光：2200ms，再长会挡住牌桌。 */
  var TIMING_PREFIX = "damage_fx_fire_";
  var DEFAULT_TIMING = {
    damage_fx_fire_1_total: 1400, damage_fx_fire_1_flight: 90,
    damage_fx_fire_1_impact: 300, damage_fx_fire_1_fade: 700,
    damage_fx_fire_2_total: 1500, damage_fx_fire_2_flight: 95,
    damage_fx_fire_2_impact: 310, damage_fx_fire_2_fade: 750,
    damage_fx_fire_3_total: 1700, damage_fx_fire_3_flight: 100,
    damage_fx_fire_3_impact: 330, damage_fx_fire_3_fade: 820,
    damage_fx_fire_3_strobe: 160, damage_fx_fire_3_flash: 0.6,
    damage_fx_fire_4_total: 2000, damage_fx_fire_4_flight: 105,
    damage_fx_fire_4_impact: 360, damage_fx_fire_4_fade: 900,
    damage_fx_fire_4_strobe: 260, damage_fx_fire_4_flash: 0.6
  };

  /* 字段名名单由 `DEFAULT_TIMING` 的键反推（票 10 同款：只许有一处真源）。 */
  function timingFieldOf(key) {
    var rest = key.slice(TIMING_PREFIX.length);
    return rest.slice(rest.indexOf("_") + 1);
  }
  var TIMING_FIELDS = (function () {
    var fields = [];
    for (var key in DEFAULT_TIMING) {
      if (!Object.prototype.hasOwnProperty.call(DEFAULT_TIMING, key)) continue;
      var field = timingFieldOf(key);
      if (fields.indexOf(field) < 0) fields.push(field);
    }
    return Object.freeze(fields);
  })();
  var TIMING_KEYS = Object.freeze(Object.keys(DEFAULT_TIMING));
  var MAX_TOTAL_MS = 2200;
  var FLASH_DEFAULT = 0.6;

  /* 素材页时间轴（导出档）：命中 / 爆炸 / 飘字都从四档表推出来，画面与音效共用同一份。 */
  (function fillExportTimes() {
    for (var id = 1; id <= 4; id++) {
      var V = TIERS[id];
      V.firstImpact = V.launch0 + V.flight;
      V.lastImpact = V.launch0 + (V.count - 1) * V.gap + V.flight;
      V.blastAt = V.blast ? (V.meteor ? V.meteor.hit : V.lastImpact) : null;
      if (V.meteor) V.numAt = V.meteor.hit + 0.38;
      else V.numAt = V.lastImpact + (V.blast ? 0.42 : 0.26);
    }
  })();

  /* 牌桌档的排期比例（都相对**这一段窗口**，不是绝对秒数）：
   * - `VOLLEY_SHARE`：整串火羽占命中之后那一段的比例（0.30s 封顶，12 支箭也要看得出连珠）；
   * - `METEOR_TAIL_SHARE`：4 档最后一支箭落地到流星命中之间，占剩余时间的比例；
   * - `NUM_SHARE`：飘字在"最后一击 → 收尾"里出现的位置。
   * 这三个数**不是**时长字段：时长只有 `anim.ini` 的六个字段，它们只决定"那一段怎么分"。 */
  var VOLLEY_SHARE = 0.22;
  var VOLLEY_MAX = 0.30;
  var METEOR_TAIL_SHARE = 0.15;
  var NUM_SHARE = 0.10;
  /* 火印开始发亮的提前量（素材页的绝对秒数；导出档照抄，牌桌档由 warp 一起压）。 */
  var CHARGE_LEAD = 0.46;

  /* `warp` 的锚点对：素材页的秒 → 牌桌的秒。段内线性插值，于是画法里的绝对值全部跟着压。
   * 两个方向都在这里：`warpAt`（牌桌秒 → 素材页秒，`draw()` 每帧用）与 `warpGameOf`
   * （素材页秒 → 牌桌秒，`gameTiming` 算时间轴上的关键点用）。 */
  function warpAt(pairs, t) {
    if (!pairs || pairs.length < 2) return t;
    if (t <= pairs[0].g) return pairs[0].e + (t - pairs[0].g);
    for (var i = 1; i < pairs.length; i++) {
      var p0 = pairs[i - 1], p1 = pairs[i];
      if (t <= p1.g) {
        var k = (t - p0.g) / Math.max(1e-9, p1.g - p0.g);
        return p0.e + (p1.e - p0.e) * clamp(k, 0, 1);
      }
    }
    var last = pairs[pairs.length - 1], prev = pairs[pairs.length - 2];
    var rate = (last.e - prev.e) / Math.max(1e-9, last.g - prev.g);
    return last.e + (t - last.g) * rate;
  }

  function warpGameOf(pairs, e) {
    if (!pairs || pairs.length < 2) return e;
    if (e <= pairs[0].e) return pairs[0].g + (e - pairs[0].e);
    for (var i = 1; i < pairs.length; i++) {
      var p0 = pairs[i - 1], p1 = pairs[i];
      if (e <= p1.e) {
        var k = (e - p0.e) / Math.max(1e-9, p1.e - p0.e);
        return p0.g + (p1.g - p0.g) * clamp(k, 0, 1);
      }
    }
    var last = pairs[pairs.length - 1], prev = pairs[pairs.length - 2];
    var rate = (last.g - prev.g) / Math.max(1e-9, last.e - prev.e);
    return last.g + (e - last.e) * rate;
  }

  /* 锚点对按顺序收下；游戏侧不许倒挂（用户把 fade 调成 0 时全靠这条兜住）。 */
  function warpPairs(raw) {
    var out = [{ e: 0, g: 0 }];
    for (var i = 1; i < raw.length; i++) {
      var p = raw[i], prev = out[out.length - 1];
      if (!(p.e > prev.e)) continue;
      out.push({ e: p.e, g: Math.max(p.g, prev.g + 1e-4) });
    }
    return out;
  }

  /* 从 `raw` 取一个毫秒数：不是有限非负数就退回 `fallback`（= 本文件的内置默认）。 */
  function millis(raw, key, fallback) {
    if (raw && Object.prototype.hasOwnProperty.call(raw, key)) {
      var value = Number(raw[key]);
      if (isFinite(value) && value >= 0) return value;
    }
    return fallback;
  }

  /* `flash` 是 0..1 的强度，不是毫秒：钳在 [0, 1]，缺 / 非法 ⇒ 0.6。 */
  function ratio(raw, key, fallback) {
    var base = isFinite(Number(fallback)) ? Number(fallback) : FLASH_DEFAULT;
    if (raw && Object.prototype.hasOwnProperty.call(raw, key)) {
      var value = Number(raw[key]);
      if (isFinite(value)) return clamp(value, 0, 1);
    }
    return clamp(base, 0, 1);
  }

  /* `game` 档：把 `anim.ini` 的六个字段解成一档**秒**时间轴 + 档位参数 + 时间映射。
   *
   * 钳制四条（同 `fx-core.js`）：`total ≤ 2200`、`flight ≤ impact ≤ total`、
   * `impact + fade ≤ total`（否则层都摘了焦痕还在）。`strobe` 是整屏着色闪的窗口，
   * 只覆盖 3 / 4 档；`flash` 是它的强度。
   *
   * 排期全从这四个时长派生（`VOLLEY_SHARE` / `METEOR_TAIL_SHARE` / `NUM_SHARE` 上面有注记）：
   * 第一箭命中 = `impact`、最后一支钉住 = 再一串火羽、3 档立刻爆燃、4 档再留一段给流星；
   * 焦痕从最后一击起算 `fade`，飘字落在最后一段的前 10%。`warp` 把这些锚点对回素材页的秒。 */
  function gameTiming(variantId, raw) {
    var id = clamp(Math.round(variantId || 1), 1, 4);
    var base = TIERS[id];
    function key(field) { return TIMING_PREFIX + id + "_" + field; }
    var total = Math.min(
      millis(raw, key("total"), DEFAULT_TIMING[key("total")]), MAX_TOTAL_MS
    );
    var impact = clamp(millis(raw, key("impact"), DEFAULT_TIMING[key("impact")]), 0, total);
    var flight = clamp(millis(raw, key("flight"), DEFAULT_TIMING[key("flight")]), 0, impact);
    var fade = clamp(
      millis(raw, key("fade"), DEFAULT_TIMING[key("fade")]), 0, Math.max(0, total - impact)
    );
    var strobe = clamp(millis(raw, key("strobe"), base.strobeMs || 0), 0, total);
    var flash = ratio(raw, key("flash"), DEFAULT_TIMING[key("flash")]);

    var seconds = total / 1000;
    var firstHit = impact / 1000;
    var postS = Math.max(0, seconds - firstHit);
    /* 火羽连珠：占命中之后那一段的 22%，封顶 0.30s（12 支箭也要看得出是一串）。 */
    var volley = Math.min(VOLLEY_MAX, postS * VOLLEY_SHARE);
    var lastHit = firstHit + volley;
    /* 最后一击之后才是爆炸 / 焦痕 / 飘字的舞台。3 档最后一支箭落地即爆燃；4 档再留一段
     * 给「火云 → 流星压境」。 */
    var blast = 0;
    if (base.blast) {
      blast = base.meteor
        ? lastHit + Math.max(0, seconds - lastHit) * METEOR_TAIL_SHARE
        : lastHit;
    }
    var anchor = blast > 0 ? blast : lastHit;
    var fadeSpan = Math.min(fade / 1000, Math.max(0, seconds - anchor));
    var fadeEnd = anchor + fadeSpan;
    var fadeStart = anchor + fadeSpan * 0.25;
    var numAt = anchor + Math.max(0, seconds - anchor) * NUM_SHARE;

    var pairs = warpPairs([
      { e: 0, g: 0 },
      { e: base.firstImpact, g: firstHit },
      { e: base.blast ? base.blastAt : base.lastImpact, g: anchor },
      { e: base.numAt, g: numAt },
      { e: base.fadeStart, g: fadeStart },
      { e: base.fadeEnd, g: fadeEnd },
      { e: base.duration, g: seconds }
    ]);
    function gameOf(exportSeconds) { return warpGameOf(pairs, exportSeconds); }

    var count = base.count;
    /* 逐箭的节奏按**命中**摊开（不是按离弦）：画面里相邻两支箭是等间隔落地的，
     * 音效照同一份 `GAP` 排，于是「第 i 箭命中」在两边是同一个时刻。 */
    var flightSec = Math.max(0, gameOf(base.launch0 + base.flight) - gameOf(base.launch0));
    var timeline = {
      DURATION: seconds,
      CHARGE: gameOf(Math.max(0, base.launch0 - CHARGE_LEAD)),
      LAUNCH: Math.max(0, firstHit - flightSec),
      FLIGHT: flightSec,
      COUNT: count,
      GAP: count > 1 ? (lastHit - firstHit) / (count - 1) : 0,
      IMPACT: firstHit,
      LAST_IMPACT: lastHit,
      BLAST: blast,
      SKY_AT: base.meteor ? gameOf(base.meteor.skyAt) : 0,
      EXPLODE: base.meteor ? blast : 0,
      NUM_AT: numAt
    };
    return {
      id: id,
      profile: "game",
      timeline: timeline,
      warp: pairs,
      ms: { total: total, flight: flight, impact: impact, fade: fade, strobe: strobe, flash: flash },
      /* 档位参数里**时间全是素材页的秒数**：`draw()` 每帧先把牌桌秒数映射回素材页再画，
       * 所以 `fadeStart` / `numAt` / `blastAt` / `meteor` 这些画法读的键一个都不能换成牌桌
       * 秒数（换了就是两套钟）。唯一要改的是飘字的寿命：素材页那边是 1.9~2.2s（在 5~6 秒里
       * 刚好），压到 1.4~2.0s 之后要按"素材页的飘字时刻 → 素材页的收尾"整段给，warp 之后
       * 正好延伸到牌桌的摘层。 */
      variant: copy(base, {
        numLife: Math.max(0.3, base.duration - base.numAt)
      })
    };
  }

  /* 导出档的时间轴（素材页那条链）：`create()` 原样交给排播层与音效。 */
  function exportTimeline(id) {
    var V = TIERS[id];
    return {
      DURATION: V.duration,
      CHARGE: Math.max(0, V.launch0 - CHARGE_LEAD),
      LAUNCH: V.launch0,
      FLIGHT: V.flight,
      COUNT: V.count,
      GAP: V.gap,
      IMPACT: V.firstImpact,
      LAST_IMPACT: V.lastImpact,
      BLAST: V.blastAt || 0,
      SKY_AT: V.meteor ? V.meteor.skyAt : 0,
      EXPLODE: V.meteor ? V.meteor.hit : 0,
      NUM_AT: V.numAt
    };
  }

  /* `export` 档：参考实现的 4.9~6.2 秒链（`profile` 缺省走它）。 */
  function exportClock(variantId) {
    var id = clamp(Math.round(variantId || 1), 1, 4);
    return {
      id: id,
      profile: "export",
      timeline: exportTimeline(id),
      warp: null,
      variant: copy(TIERS[id], {}),
      ms: null
    };
  }

  function clockFor(opts) {
    return opts && opts.profile === "game"
      ? gameTiming(opts.variant, opts.timing)
      : exportClock(opts && opts.variant);
  }

  /* ================================================================
   * 座位矩形 → 舞台（同 `fx-thunder.js`）
   * ============================================================== */

  /* 牌桌档的落屏下限：1 点档火羽主体 12 × 0.55 = 6.6 CSS px，再小就看不出"箭"了。 */
  var COMPOSITION_FLOOR = 0.55;
  /* 无来源（`src === null`）时来源块挪到画面左外多远（CSS px）——与 `damage-fx.js` 的
   * `DAMAGE_FX_OFFSTAGE_UNITS` 同值。库里没有无来源的火，这条只是兜底：不猜来源、不抛错。 */
  var OFFSTAGE_UNITS = 200;

  /* 座位矩形：缺字段 / 非法值退回参考舞台（`anim.ini` 删一行不该让页面报错，
   * 覆盖层量不到布局时同理）。w / h 必须为正。 */
  function rectOr(raw, fallback) {
    var out = { cx: fallback.cx, cy: fallback.cy, w: fallback.w, h: fallback.h };
    if (!raw) return out;
    var keys = ["cx", "cy", "w", "h"];
    for (var i = 0; i < keys.length; i++) {
      var value = Number(raw[keys[i]]);
      if (!isFinite(value)) continue;
      if ((keys[i] === "w" || keys[i] === "h") && value <= 0) continue;
      out[keys[i]] = value;
    }
    return out;
  }

  /* 覆盖层量出来的两张武将块 → 本实例的舞台。
   *
   * - `src === null` ⇒ 来源整块挪到画面左外（同剑光先例，**不做**火的"天降"变体：
   *   库里三个火焰生产者都带来源，为一个不存在的场景造画法是浪费）；
   * - `u` = 它比参考牌 460×644 小多少（不许小于 `COMPOSITION_FLOOR`：火羽得看得见）；
   * - 不传 `stage` ⇒ 参考舞台（`u = 1`、A / B 取原常量），素材库那条链逐字节不变。 */
  function stageFor(raw) {
    var hasStage = raw && (raw.src || raw.dst);
    var dst = hasStage ? rectOr(raw.dst, REF_DST) : REF_DST;
    var src = hasStage && raw.src ? rectOr(raw.src, REF_SRC) : null;
    var offstage = hasStage && !src;
    if (!src) {
      if (!hasStage) src = REF_SRC;
      else src = { cx: -dst.w / 2 - OFFSTAGE_UNITS, cy: dst.cy, w: dst.w, h: dst.h };
    }
    var u = Math.min(
      src.w / CARD.w, src.h / CARD.h, dst.w / CARD.w, dst.h / CARD.h
    );
    if (!isFinite(u) || u <= 0) u = 1;
    u = Math.max(u, COMPOSITION_FLOOR);
    var a = { x: src.cx + src.w * ANCHOR.ax, y: src.cy + src.h * ANCHOR.ay };
    var b = { x: dst.cx + dst.w * ANCHOR.bx, y: dst.cy + dst.h * ANCHOR.by };
    return { src: src, dst: dst, a: a, b: b, u: u, offstage: offstage };
  }

  /* 本实例的目标画布尺寸（CSS px）：缺省 = 参考舞台 1920×1080。 */
  function viewSize(view) {
    var w = view ? Number(view.width) : NaN;
    var h = view ? Number(view.height) : NaN;
    return {
      w: isFinite(w) && w > 0 ? w : REF_W,
      h: isFinite(h) && h > 0 ? h : REF_H
    };
  }

  /* 把画法用的帧上下文切到这条演出：两张牌 / 两个锚点 / 画布尺寸 / 构图比例。 */
  function setFrame(stage, w, h) {
    SRC = stage.src;
    DST = stage.dst;
    A = stage.a;
    B = stage.b;
    W = w;
    H = h;
    U = Number(stage.u) > 0 ? Number(stage.u) : 1;
  }

  /* ================================================================
   * 宣纸 / 武将牌（一次画进离屏画布，之后每帧只 drawImage）
   * ============================================================== */

  function paintPaper(S) {
    var w = REF_W + 160, h = REF_H + 160;
    var pc = offscreen(w * S, h * S), c = pc[1];
    c.scale(S, S);
    var r = rng(20261112);

    c.fillStyle = C.paper;
    c.fillRect(0, 0, w, h);

    /* 纸纤维 & 颗粒：只留一点点，别把宣纸压成灰纸。 */
    for (var i = 0; i < (w * h) / 30; i++) {
      c.fillStyle = r() < 0.5 ? "rgba(28,26,23,0.014)" : "rgba(255,255,255,0.05)";
      c.fillRect(r() * w, r() * h, 1, 1);
    }
    c.strokeStyle = "rgba(90,70,40,0.024)";
    c.lineWidth = 0.7;
    for (var j = 0; j < (w * h) / 1250; j++) {
      var px = r() * w, py = r() * h, a = r() * TAU, l = 5 + r() * 22;
      c.beginPath();
      c.moveTo(px, py);
      c.lineTo(px + Math.cos(a) * l, py + Math.sin(a) * l);
      c.stroke();
    }

    /* 背景：一枚若有若无的枯笔圆相 + 两团宿墨；不能抢牌面。 */
    c.save();
    c.globalCompositeOperation = "multiply";
    var cx = REF_W / 2 + 6, cy = REF_H / 2 + 22;
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
    var ink = [
      [w * 0.13, h * 0.20, 220, 0.012], [w * 0.86, h * 0.78, 260, 0.010],
      [w * 0.78, h * 0.15, 170, 0.008]
    ];
    for (var m = 0; m < ink.length; m++) {
      var q = ink[m];
      var g = c.createRadialGradient(q[0], q[1], 0, q[0], q[1], q[2]);
      g.addColorStop(0, "rgba(28,26,23," + q[3] + ")");
      g.addColorStop(0.55, "rgba(28,26,23," + q[3] * 0.45 + ")");
      g.addColorStop(1, "rgba(28,26,23,0)");
      c.fillStyle = g;
      c.beginPath(); c.arc(q[0], q[1], q[2], 0, TAU); c.fill();
    }
    /* 极淡的暖赭，给火焰一点底色，别让后面全是冷白。 */
    var warm = c.createRadialGradient(w * 0.62, h * 0.30, 0, w * 0.62, h * 0.30, 680);
    warm.addColorStop(0, "rgba(178,96,52,0.030)");
    warm.addColorStop(1, "rgba(178,96,52,0)");
    c.fillStyle = warm;
    c.fillRect(0, 0, w, h);
    c.restore();

    /* 四角压暗：暖墨，不用冷黑。 */
    var vg = c.createRadialGradient(w * 0.5, h * 0.47, h * 0.26, w * 0.5, h * 0.5, h * 1.04);
    vg.addColorStop(0, "rgba(70,52,34,0)");
    vg.addColorStop(1, "rgba(70,52,34,0.20)");
    c.fillStyle = vg;
    c.fillRect(0, 0, w, h);

    return pc[0];
  }

  function wrapText(c, text, x, y, maxW, lineH, maxLines) {
    var line = "", out = [];
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (c.measureText(line + ch).width > maxW && line) { out.push(line); line = ch; }
      else line += ch;
    }
    if (line) out.push(line);
    for (var j = 0; j < out.length && j < maxLines; j++) c.fillText(out[j], x, y + j * lineH);
  }

  function paintHero(hero, S) {
    var w = CARD.w, h = CARD.h, pad = 24;
    var cc = offscreen((w + CARD.pad * 2) * S, (h + CARD.pad * 2) * S), c = cc[1];
    c.scale(S, S);
    var r = rng(hero.name.charCodeAt(0) * 97 + 11);
    c.translate(CARD.pad, CARD.pad);

    /* 投影（烘进贴图） */
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
      c.font = "400 15px " + SANS;
      wrapText(c, sk[1], pad, y + 24, w - pad * 2, 19, 2);
    }

    c.textAlign = "right";
    c.textBaseline = "alphabetic";
    c.font = "400 12px " + SANS;
    c.fillStyle = "rgba(111,106,98,0.45)";
    c.fillText("SGS · HERO", w - pad, h - 16);

    return cc[0];
  }

  /* 火焰纹理：一叠暗斑 + 亮斑，烘焙一次；画火团时贴在火上，免得火是"奶油"。
   * 只有 alpha，没有底色，叠在火团上不会变成一块灰。两张画法都只用得到这一张贴图。 */
  function paintFlameTex() {
    var S = 512;
    var cv = offscreen(S, S), c = cv[1];
    var r = rng(4242);
    for (var i = 0; i < 90; i++) {
      var x = r() * S, y = r() * S, R = 8 + r() * 46;
      var g = c.createRadialGradient(x, y, 0, x, y, R);
      g.addColorStop(0, "rgba(70,12,0," + (0.10 + r() * 0.20) + ")");
      g.addColorStop(1, "rgba(70,12,0,0)");
      c.fillStyle = g;
      c.beginPath();
      c.arc(x, y, R, 0, TAU);
      c.fill();
    }
    for (i = 0; i < 70; i++) {
      var x2 = r() * S, y2 = r() * S, R2 = 6 + r() * 30;
      var g2 = c.createRadialGradient(x2, y2, 0, x2, y2, R2);
      g2.addColorStop(0, "rgba(255,234,168," + (0.10 + r() * 0.22) + ")");
      g2.addColorStop(1, "rgba(255,234,168,0)");
      c.fillStyle = g2;
      c.beginPath();
      c.arc(x2, y2, R2, 0, TAU);
      c.fill();
    }
    return cv[0];
  }

  var TEX = {};
  function texture(key, paint) {
    if (!TEX[key]) TEX[key] = paint();
    return TEX[key];
  }

  /* ================================================================
   * 火焰笔触
   * ============================================================== */

  /* 一束火舌：底宽尖细，尖端与两侧随 t 抖动（同一 t 抖动固定）。 */
  function flamePath(c, len, wid, seed, t) {
    var f1 = Math.sin(t * 23 + seed * 3.1);
    var f2 = Math.sin(t * 37 + seed * 1.7 + 1.3);
    var f3 = Math.sin(t * 53 + seed * 0.9);
    var tip = (f1 * 0.34 + f2 * 0.20) * wid;
    var sway = (f2 * 0.22 + f3 * 0.10) * wid;
    var w = wid * (0.9 + 0.1 * f1);
    c.beginPath();
    c.moveTo(0, -w * 0.5);
    c.bezierCurveTo(len * 0.16, -w * 0.80, len * 0.30, -w * 0.62, len * 0.52, -w * 0.30 + sway * 0.30);
    c.bezierCurveTo(len * 0.74, -w * 0.18 + sway * 0.55, len * 0.90, tip * 0.30, len, tip);
    c.bezierCurveTo(len * 0.88, tip * 0.30 + w * 0.10, len * 0.66, w * 0.24 + sway * 0.55, len * 0.42, w * 0.32 + sway * 0.2);
    c.bezierCurveTo(len * 0.26, w * 0.58, len * 0.12, w * 0.68, 0, w * 0.5);
    c.quadraticCurveTo(-w * 0.18, 0, 0, -w * 0.5);
    c.closePath();
  }

  function drawFlame(c, len, wid, seed, t, pal, alpha) {
    if (len <= 0.5 || alpha <= 0.01) return;
    c.save();

    /* 焦边：比本体宽一圈，像宣纸上的暗朱 */
    c.globalAlpha = alpha * 0.36;
    c.fillStyle = pal.edge;
    flamePath(c, len * 1.02, wid * 1.18, seed, t);
    c.fill();

    /* 火体：根部金橙 → 朱砂 → 尖端暗朱收束 */
    var g = c.createLinearGradient(0, 0, len, 0);
    g.addColorStop(0, "rgba(255,206,110,0.98)");
    g.addColorStop(0.16, pal.mid2);
    g.addColorStop(0.52, pal.mid);
    g.addColorStop(0.82, "rgba(168,42,12,0.92)");
    g.addColorStop(1, "rgba(108,22,6,0)");
    c.globalAlpha = alpha * 0.96;
    c.fillStyle = g;
    flamePath(c, len, wid, seed + 3.3, t);
    c.fill();

    /* 内芯：只在长火舌的根部点一星白热 */
    if (len > 90 * U) {
      c.globalCompositeOperation = "lighter";
      var g2 = c.createLinearGradient(0, 0, len * 0.26, 0);
      g2.addColorStop(0, "rgba(255,248,220,0.75)");
      g2.addColorStop(1, "rgba(255,200,120,0)");
      c.globalAlpha = alpha * 0.5;
      c.fillStyle = g2;
      flamePath(c, len * 0.24, wid * 0.22, seed + 7.1, t);
      c.fill();
    }
    c.restore();
  }

  /* 不描边的火舌：根部一种颜色，尖端化开；用来长在火团边缘 */
  function drawTongue(c, len, wid, seed, t, colA, colB) {
    if (len <= 2) return;
    var g = c.createLinearGradient(0, 0, len, 0);
    g.addColorStop(0, colA);
    g.addColorStop(0.42, colB);
    g.addColorStop(0.78, "rgba(198,58,16,0.55)");
    g.addColorStop(1, "rgba(158,38,10,0)");
    c.fillStyle = g;
    flamePath(c, len, wid, seed, t);
    c.fill();
  }

  /* ================================================================
   * 舞台：纸 / 牌 / 座位锚点
   * ============================================================== */

  function drawPaper(c, paper) {
    c.drawImage(paper, -80, -80, W + 160, H + 160);
  }

  function drawCard(c, img, rect, dx, dy, rot) {
    c.save();
    c.translate(rect.cx + dx, rect.cy + dy);
    c.rotate(rot);
    c.drawImage(img,
      -rect.w / 2 - CARD.pad, -rect.h / 2 - CARD.pad,
      rect.w + CARD.pad * 2, rect.h + CARD.pad * 2);
    c.restore();
  }

  /* 逐箭余震 + 爆炸大震：都按 age 指数衰减，同一 t 完全确定。 */
  function cardKick(t, V) {
    var dx = 0, dy = 0, rot = 0;
    for (var i = 0; i < V.arrows.length; i++) {
      var age = t - V.arrows[i].t1;
      if (age < 0) continue;
      var k = Math.exp(-age * 9);
      dx += Math.sin(age * 90 + i * 1.3) * V.shake * 0.055 * k * U;
      dy += Math.cos(age * 77 + i * 1.7) * V.shake * 0.042 * k * U;
      rot += Math.sin(age * 63 + i) * 0.0022 * V.dmg * k;
    }
    if (V.blast) {
      var a = t - V.blastAt;
      if (a >= 0) {
        var kb = Math.exp(-a * 6) * (1 - Math.exp(-a * 30));
        dx += Math.sin(a * 60 + 0.6) * V.blast.shake * 0.20 * kb * U;
        dy += Math.cos(a * 52) * V.blast.shake * 0.14 * kb * U;
        rot += Math.sin(a * 44) * 0.010 * kb;
      }
    }
    return { dx: dx, dy: dy, rot: rot };
  }

  function shakeAt(t, V) {
    var s = { x: 0, y: 0 };
    function add(t0, amp) {
      var age = t - t0;
      if (age < 0) return;
      var k = Math.exp(-age * 8.5) * amp * U;
      s.x += (Math.sin(age * 83 + 1.2) + 0.55 * Math.sin(age * 139 + 0.4)) * k * 0.5;
      s.y += (Math.cos(age * 71 + 0.7) + 0.5 * Math.sin(age * 127 + 2.1)) * k * 0.5;
    }
    for (var i = 0; i < V.arrows.length; i++) add(V.arrows[i].t1, V.shake * 0.42);
    if (V.blast) add(V.blastAt, V.blast.shake);
    return s;
  }

  /* 爆炸瞬间镜头一顶（只放大，不留黑边）。 */
  function blastZoom(t, V) {
    if (!V.blast) return 1;
    var age = t - V.blastAt;
    if (age < 0) return 1;
    var amp = V.blast.meteor ? 0.048 : 0.020;
    return 1 + amp * Math.exp(-age * 6.5) * (1 - Math.exp(-age * 40));
  }

  /* 冲击波把两张牌向外推。 */
  function blastPush(t, V) {
    if (!V.blast) return 0;
    var age = t - V.blastAt;
    if (age < 0) return 0;
    var amp = (V.blast.meteor ? 24 : 9) * U;
    return amp * Math.exp(-age * 5) * (1 - Math.exp(-age * 45));
  }

  /* ================================================================
   * 火印 / 蓄力 / 出箭脉冲
   * ============================================================== */
  function drawSourceFire(c, t, V) {
    var firstOut = V.arrows.length ? V.arrows[0].t0 : 0.60;
    var lastOut = V.arrows.length ? V.arrows[V.arrows.length - 1].t0 : 0.60;
    var charge = ss(firstOut - 0.46, firstOut - 0.06, t) * (1 - ss(lastOut + 0.10, lastOut + 0.55, t));

    /* 火印的暖光洒在来源牌右半（裁在牌里） */
    if (charge > 0.01) {
      c.save();
      clipCard(c, SRC, 2);
      var g = c.createRadialGradient(A.x, A.y, 0, A.x, A.y, 300 * U);
      g.addColorStop(0, "rgba(255,150,70," + (0.30 * charge) + ")");
      g.addColorStop(0.55, "rgba(240,110,50," + (0.12 * charge) + ")");
      g.addColorStop(1, "rgba(240,110,50,0)");
      c.fillStyle = g;
      c.fillRect(SRC.cx - SRC.w / 2, SRC.cy - SRC.h / 2, SRC.w, SRC.h);
      c.restore();
    }

    /* 朱砂方印「火」：蓄力时发亮、发烫 */
    c.save();
    c.translate(A.x - 12 * U, A.y - 34 * U);
    c.rotate(-0.045);
    var pulse = 0.5 + 0.5 * Math.sin(t * 7.2);
    softGlow(c, 0, 0, (58 + 10 * pulse) * U, "#ff7a24", 0.30 * charge + 0.06 * pulse);
    c.globalAlpha = 0.72 + 0.28 * charge;
    c.fillStyle = "#9c2b1b";
    pathRound(c, -21 * U, -21 * U, 42 * U, 42 * U, 4 * U);
    c.fill();
    c.strokeStyle = "rgba(247,241,229," + (0.45 + 0.35 * charge) + ")";
    c.lineWidth = Math.max(1, 2 * U);
    pathRound(c, -18 * U, -18 * U, 36 * U, 36 * U, 3 * U);
    c.stroke();
    c.fillStyle = "rgba(250,244,232," + (0.66 + 0.30 * charge) + ")";
    c.font = "700 " + Math.round(25 * U) + "px " + SERIF;
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.fillText("火", 0, 1);
    c.restore();

    /* 1~3 档：引弓点上的火苗 */
    if (charge > 0.01) {
      c.save();
      c.translate(A.x, A.y);
      c.rotate(-0.30);
      drawFlame(c, (26 + 58 * charge) * U, (10 + 16 * charge) * U, 3.7, t, V.pal, 0.85 * charge);
      c.restore();
    }

    /* 出箭：弓弦一弹的火环 */
    for (var j = 0; j < V.arrows.length; j++) {
      var age2 = t - V.arrows[j].t0;
      if (age2 < 0 || age2 > 0.30) continue;
      var k2 = 1 - age2 / 0.30;
      c.save();
      c.globalCompositeOperation = "lighter";
      softGlow(c, A.x, A.y, (26 + 60 * (1 - k2)) * U, "#ffb35a", 0.42 * k2);
      c.strokeStyle = "rgba(255,168,84," + (0.5 * k2) + ")";
      c.lineWidth = Math.max(1, 1.6 * U);
      c.beginPath();
      c.arc(A.x, A.y, (20 + 80 * (1 - k2)) * U, -0.8, 0.9);
      c.stroke();
      c.restore();
    }
  }

  /* ================================================================
   * 火羽：一次生成一支的航线 / 火星 / 焦痕，按 t 取用
   * ============================================================== */
  function buildVolley(V, r) {
    var out = [];
    var n = V.count;
    for (var i = 0; i < n; i++) {
      var lane = (i + 0.5) / n;
      var tx = DST.cx + (r() - 0.5) * V.spreadX * U;
      var ty = B.y - V.spreadY * 0.5 * U + lane * V.spreadY * U + (r() - 0.5) * 26 * U;
      var launch = V.launch0 + i * V.gap;
      var mx = (A.x + tx) / 2, my = (A.y + ty) / 2;
      var dx = tx - A.x, dy = ty - A.y;
      var len0 = Math.sqrt(dx * dx + dy * dy) || 1;
      var nx = -dy / len0, ny = dx / len0;
      var bend = (55 + r() * 110) * (0.7 + 0.5 * r()) * U;

      var a = {
        t0: launch, t1: launch + V.flight,
        x0: A.x, y0: A.y,
        cx: mx - nx * bend, cy: my - ny * bend,
        x1: tx, y1: ty,
        seed: 13 + i * 7.3 + r() * 3,
        len: V.len * (0.90 + r() * 0.18) * U,
        wid: V.wid * (0.88 + r() * 0.22) * U
      };

      a.sparks = [];
      for (var s = 0; s < V.sparkN; s++) {
        a.sparks.push({
          ang: r() * TAU,
          spd: (120 + r() * 520) * U,
          life: 0.26 + r() * 0.40,
          len: (3 + r() * 9) * U,
          w: (0.9 + r() * 1.8) * U,
          g: 140 + Math.floor(r() * 90)
        });
      }

      a.splat = [];
      for (s = 0; s < V.splatN; s++) {
        a.splat.push({
          ang: r() * TAU,
          dist: (6 + r() * 40) * U,
          rad: (1.1 + r() * 3.2) * U
        });
      }

      a.burn = blobShape(r, 9, 0.52, 1.18);
      a.burnR = (13 + r() * 12) * U;
      out.push(a);
    }
    return out;
  }

  function arrowPoint(a, u) {
    var e = u * u * 0.55 + u * 0.45;
    var iu = 1 - e;
    var x = iu * iu * a.x0 + 2 * iu * e * a.cx + e * e * a.x1;
    var y = iu * iu * a.y0 + 2 * iu * e * a.cy + e * e * a.y1;
    var dx = 2 * iu * (a.cx - a.x0) + 2 * e * (a.x1 - a.cx);
    var dy = 2 * iu * (a.cy - a.y0) + 2 * e * (a.y1 - a.cy);
    return { x: x, y: y, ang: Math.atan2(dy, dx), u: u };
  }

  function arrowAt(a, t) {
    return arrowPoint(a, clamp((t - a.t0) / (a.t1 - a.t0), 0, 1));
  }

  function drawArrow(c, a, t, V) {
    var p = arrowAt(a, t);
    var fadeIn = ss(0.0, 0.05, p.u);
    var fadeOut = 1 - ss(0.94, 1.0, p.u);
    var alpha = fadeIn * (0.25 + 0.75 * fadeOut);
    if (alpha <= 0.01) return;

    var L = a.len, wid = a.wid;

    /* 尾迹：一路淡下去的火痕 */
    for (var e = 9; e >= 1; e--) {
      var ue = p.u - e * 0.028;
      if (ue < 0) continue;
      var qe = arrowPoint(a, ue);
      var kk = 1 - e / 10;
      var rg = c.createRadialGradient(qe.x, qe.y, 0, qe.x, qe.y, (9 + 22 * kk) * U);
      rg.addColorStop(0, "rgba(252,130,52," + ((0.05 + 0.12 * kk) * fadeOut) + ")");
      rg.addColorStop(1, "rgba(252,130,52,0)");
      c.fillStyle = rg;
      c.beginPath();
      c.arc(qe.x, qe.y, (9 + 22 * kk) * U, 0, TAU);
      c.fill();
    }
    for (var e2 = 0; e2 < 9; e2++) {
      var ue2 = p.u - 0.04 - e2 * 0.036;
      if (ue2 < 0) continue;
      var q2 = arrowPoint(a, ue2);
      var h1 = hash(a.seed + e2 * 4.7 + Math.floor(t * 30));
      var h2 = hash(a.seed * 1.3 + e2 * 3.1 + Math.floor(t * 26));
      c.fillStyle = "rgba(255," + (135 + Math.floor(90 * h1)) + ",70," + (0.55 * fadeOut * (1 - e2 / 11)) + ")";
      c.beginPath();
      c.arc(q2.x + (h1 - 0.5) * 22 * U, q2.y + (h2 - 0.5) * 22 * U - e2 * 1.6 * U,
        (1.1 + 1.9 * hash(a.seed + e2)) * U, 0, TAU);
      c.fill();
    }

    c.save();
    c.translate(p.x, p.y);
    c.rotate(p.ang);
    c.globalAlpha = alpha;

    /* 火尾：两束细长的火舌从箭尾往后拖 */
    c.save();
    c.translate(-L * 0.44, 0);
    c.rotate(Math.PI);
    drawFlame(c, L * 1.40, wid * 1.00, a.seed, t, PAL_DEEP, 0.62);
    drawFlame(c, L * 0.98, wid * 0.72, a.seed + 2.4, t, V.pal, 0.85);
    c.save();
    c.translate(0, wid * 0.30);
    c.rotate(-0.10);
    drawFlame(c, L * 0.68, wid * 0.42, a.seed + 5.1, t, PAL_GOLD, 0.55);
    c.restore();
    c.restore();

    /* 箭杆：细、直、一线金高光 */
    c.lineCap = "round";
    c.strokeStyle = "#241009";
    c.lineWidth = Math.max(1.4, wid * 0.15);
    c.beginPath();
    c.moveTo(-L * 0.46, 0);
    c.lineTo(L * 0.30, 0);
    c.stroke();
    c.strokeStyle = "rgba(255,196,132,0.62)";
    c.lineWidth = Math.max(0.8, wid * 0.05);
    c.beginPath();
    c.moveTo(-L * 0.40, -Math.max(1, wid * 0.07));
    c.lineTo(L * 0.26, -Math.max(1, wid * 0.07));
    c.stroke();

    /* 尾羽：两片窄羽贴着箭杆往后掠（深赭，压得住火色） */
    for (var f = 0; f < 2; f++) {
      var sgn = f === 0 ? -1 : 1;
      c.beginPath();
      c.moveTo(-L * 0.28, 0);
      c.quadraticCurveTo(-L * 0.36, sgn * wid * 0.26, -L * 0.47, sgn * wid * 0.20);
      c.quadraticCurveTo(-L * 0.40, sgn * wid * 0.05, -L * 0.28, 0);
      c.closePath();
      c.fillStyle = "#4a1c0e";
      c.fill();
      c.strokeStyle = "rgba(255,140,70,0.55)";
      c.lineWidth = 1;
      c.stroke();
    }

    /* 箭头：细长的柳叶形，尖端白热 */
    var tipX = L * 0.52, hw = wid * 0.28;
    c.beginPath();
    c.moveTo(tipX, 0);
    c.lineTo(tipX - L * 0.17, -hw);
    c.quadraticCurveTo(tipX - L * 0.12, 0, tipX - L * 0.17, hw);
    c.closePath();
    var hg = c.createLinearGradient(tipX - L * 0.18, 0, tipX, 0);
    hg.addColorStop(0, "#6d1a0a");
    hg.addColorStop(0.42, "#e8481a");
    hg.addColorStop(0.78, "#ffb654");
    hg.addColorStop(1, "#fffbe8");
    c.fillStyle = hg;
    c.fill();

    /* 箭尖的一小簇火苗 + 热芯 */
    c.save();
    c.translate(tipX - L * 0.01, 0);
    drawFlame(c, L * 0.10, wid * 0.30, a.seed + 5.2, t, V.pal, 0.75);
    c.globalCompositeOperation = "lighter";
    softGlow(c, 0, 0, wid * 1.8, "#ffe0a2", 0.55);
    c.restore();

    c.restore();
  }

  function drawVolley(c, t, V) {
    for (var i = 0; i < V.arrows.length; i++) {
      var a = V.arrows[i];
      if (t < a.t0 || t > a.t1 + 0.02) continue;
      drawArrow(c, a, t, V);
    }
  }

  /* ================================================================
   * 命中：火星 / 火舌外翻 / 冲击环 / 墨点 / 焦痕
   * ============================================================== */
  function drawSparks(c, a, age) {
    c.save();
    c.lineCap = "round";
    for (var i = 0; i < a.sparks.length; i++) {
      var s = a.sparks[i];
      if (age > s.life) continue;
      var p = age / s.life;
      var d = s.spd * age * (1 - 0.28 * p);
      var x = a.x1 + Math.cos(s.ang) * d;
      var y = a.y1 + Math.sin(s.ang) * d + 300 * U * age * age;
      var alpha = (1 - p) * 0.95;
      var lx = x - Math.cos(s.ang) * s.len * (0.35 + 0.65 * (1 - p));
      var ly = y - Math.sin(s.ang) * s.len * (0.35 + 0.65 * (1 - p));

      /* 底盘：暗赭的一笔，浅纸上才看得见 */
      c.strokeStyle = "rgba(146,42,10," + (0.30 * alpha) + ")";
      c.lineWidth = Math.max(1, s.w * (1 - 0.4 * p) + 2.2 * U);
      c.beginPath();
      c.moveTo(lx, ly);
      c.lineTo(x, y);
      c.stroke();

      /* 火星本体 + 白热芯 */
      c.strokeStyle = "rgba(255," + s.g + ",90," + alpha + ")";
      c.lineWidth = Math.max(0.7, s.w * (1 - 0.5 * p));
      c.beginPath();
      c.moveTo(lx, ly);
      c.lineTo(x, y);
      c.stroke();
      c.strokeStyle = "rgba(255,238,190," + (0.6 * alpha) + ")";
      c.lineWidth = Math.max(0.7, s.w * 0.34);
      c.beginPath();
      c.moveTo(lx, ly);
      c.lineTo(x, y);
      c.stroke();
    }
    c.restore();
  }

  function drawHitFlare(c, a, age, V, t) {
    var p = age / 0.30;
    if (p >= 1) return;
    var k = 1 - p;
    c.save();
    c.translate(a.x1, a.y1);

    /* 溅开的火舌：长短不一、偏心、往上飘 */
    for (var i = 0; i < 5; i++) {
      var ang = -2.2 + i * 0.92 + hash(a.seed + i) * 0.4;
      var len = (28 + 52 * hash(a.seed + i * 1.7)) * (0.55 + 0.45 * k) * (V.dmg >= 3 ? 1.25 : 1) * U;
      var wd = (16 + 14 * hash(a.seed + i * 3.3)) * U;
      c.save();
      c.rotate(ang);
      c.translate(8 * U, 0);
      drawFlame(c, len * (1 - 0.25 * p), wd, a.seed + i * 2.1, t, i % 2 ? V.pal : PAL_DEEP, 0.92 * k);
      c.restore();
    }

    /* 白热核 */
    c.save();
    c.globalCompositeOperation = "lighter";
    softGlow(c, 0, 0, (16 + 12 * k) * U, "#ffd694", 0.50 * k);
    c.restore();
    c.restore();
  }

  function drawRing(c, x, y, R, w, color, alpha, sy) {
    if (alpha <= 0.01 || R <= 0) return;
    c.save();
    c.strokeStyle = color;
    c.globalAlpha = alpha;
    c.lineWidth = Math.max(0.8, w);
    c.beginPath();
    c.ellipse(x, y, R, R * (sy || 1), 0, 0, TAU);
    c.stroke();
    c.restore();
  }

  function drawHits(c, t, V) {
    var fade = 1 - ss(V.fadeStart, V.fadeEnd, t);
    for (var i = 0; i < V.arrows.length; i++) {
      var a = V.arrows[i];
      var age = t - a.t1;
      if (age < 0) continue;

      /* 命中瞬间：白热核 */
      if (age < 0.22) {
        var k = 1 - age / 0.22;
        c.save();
        c.globalCompositeOperation = "lighter";
        softGlow(c, a.x1, a.y1, (18 + 26 * k) * U, "#ffd48a", 0.45 * k);
        c.restore();
      }

      if (!(V.blast && Math.abs(a.t1 - V.blastAt) < 0.05)) drawHitFlare(c, a, age, V, t);
      drawSparks(c, a, age);

      /* 牌面上的火环（2 档起） */
      if (V.dmg >= 2 && age < 0.50) {
        var p = age / 0.50;
        drawRing(c, a.x1, a.y1, (16 + 132 * eOut(p)) * U,
          (3.0 * (1 - p) + 0.6) * (0.7 + 0.15 * V.dmg),
          "rgba(244,96,26," + (0.48 * (1 - p)) + ")", 0.78);
      }

      /* 墨点：少而深，落在焦痕里 */
      if (age < 2.2) {
        var fa = clamp(age / 0.10, 0, 1) * fade;
        c.save();
        c.globalAlpha = 0.38 * fa * V.scorch;
        c.fillStyle = "#2b160c";
        for (var s = 0; s < a.splat.length; s++) {
          var sp = a.splat[s];
          var d = sp.dist * eOut(clamp(age / 0.16, 0, 1));
          c.beginPath();
          c.arc(a.x1 + Math.cos(sp.ang) * d, a.y1 + Math.sin(sp.ang) * d * 0.86, sp.rad * 0.8, 0, TAU);
          c.fill();
        }
        c.restore();
      }
    }
  }

  function drawBurn(c, t, V) {
    var fade = 1 - ss(V.fadeStart, V.fadeEnd, t);
    if (fade <= 0.02) return;

    c.save();
    clipCard(c, DST, 3);

    /* 每根箭钉出来的小焦痕 */
    for (var i = 0; i < V.arrows.length; i++) {
      var a = V.arrows[i];
      var age = t - a.t1;
      if (age < 0) continue;
      var k = ss(0, 0.26, age);
      c.save();
      var br = a.burnR * (0.72 + 0.28 * k);
      var bg = c.createRadialGradient(a.x1, a.y1, 0, a.x1, a.y1, br * 1.15);
      bg.addColorStop(0, "rgba(26,12,7," + (0.78 * k * fade * V.scorch) + ")");
      bg.addColorStop(0.55, "rgba(26,12,7," + (0.38 * k * fade * V.scorch) + ")");
      bg.addColorStop(1, "rgba(26,12,7,0)");
      c.fillStyle = bg;
      c.beginPath();
      c.arc(a.x1, a.y1, br * 1.15, 0, TAU);
      c.fill();
      c.globalAlpha = 0.62 * k * fade * V.scorch;
      c.fillStyle = "#1f0d07";
      blobPath(c, a.burn, a.x1, a.y1, br * 0.55, a.seed);
      c.fill();
      c.restore();
      if (age < 1.6) {
        var e = 1 - age / 1.6;
        c.save();
        c.globalCompositeOperation = "lighter";
        softGlow(c, a.x1, a.y1, (10 + 8 * e) * U, "#ff7a24", 0.46 * e * fade);
        c.restore();
      }
    }

    /* 爆炸留下的整片烧灼 */
    if (V.blast) {
      var age2 = t - V.blastAt;
      if (age2 >= 0) {
        var k2 = ss(0, 0.42, age2);
        var sc = Math.min(1.7, V.blast.scale);
        c.save();
        for (var b = 0; b < 6; b++) {
          var ang = b / 6 * TAU + 0.4;
          var d = (20 + 54 * hash(b * 3.7)) * k2 * sc * U;
          var R = (13 + 20 * hash(b * 5.1)) * k2 * sc * U;
          c.globalAlpha = 0.58 * fade * V.scorch * k2;
          c.fillStyle = "#241009";
          blobPath(c, V.burnBlob, V.blast.x + Math.cos(ang) * d, V.blast.y + Math.sin(ang) * d * 0.9, R * 0.85, ang);
          c.fill();
        }
        /* 焦土里没灭的余烬 */
        c.globalCompositeOperation = "lighter";
        for (b = 0; b < 12; b++) {
          var ea = hash(b * 9.1) * TAU;
          var ed = (10 + 52 * hash(b * 4.3)) * sc * U;
          var flick = 0.55 + 0.45 * Math.sin(t * (7 + b) + b);
          c.globalAlpha = (1 - ss(0, 2.6, age2)) * 0.55 * flick * fade;
          c.fillStyle = "#ff9a3c";
          c.beginPath();
          c.arc(V.blast.x + Math.cos(ea) * ed, V.blast.y + Math.sin(ea) * ed * 0.9,
            (1.4 + 1.8 * hash(b)) * U, 0, TAU);
          c.fill();
        }
        c.restore();
        if (age2 < 3.2) {
          var e2 = 1 - age2 / 3.2;
          var fl = 0.5 + 0.5 * Math.sin(t * 13.7);
          c.save();
          c.globalCompositeOperation = "lighter";
          softGlow(c, V.blast.x, V.blast.y, (70 + 26 * fl) * U, "#ff6a1e", 0.32 * e2 * fade);
          c.restore();
        }
      }
    }

    c.restore();
  }

  /* 火光照牌：命中 / 爆炸时目标牌被暖光洗一遍（裁在牌里） */
  function drawFireLight(c, t, V) {
    var glow = 0;
    for (var i = 0; i < V.arrows.length; i++) {
      var age = t - V.arrows[i].t1;
      if (age < 0) continue;
      glow += Math.exp(-age * 3.4) * 0.5;
    }
    if (V.blast) {
      var ab = t - V.blastAt;
      if (ab >= 0) glow += Math.exp(-ab * 2.4) * 1.5;
    }
    glow = Math.min(1.35, glow);
    if (glow <= 0.01) return;
    var cx = V.blast ? V.blast.x : B.x;
    var cy = V.blast ? V.blast.y : B.y;
    c.save();
    clipCard(c, DST, 2);
    var g = c.createRadialGradient(cx, cy, 0, cx, cy, 430 * U);
    g.addColorStop(0, "rgba(255,150,60," + (0.24 * glow) + ")");
    g.addColorStop(0.55, "rgba(240,110,44," + (0.10 * glow) + ")");
    g.addColorStop(1, "rgba(240,110,44,0)");
    c.fillStyle = g;
    c.fillRect(DST.cx - DST.w / 2, DST.cy - DST.h / 2, DST.w, DST.h);
    c.restore();
  }

  function drawBlast(c, t, V) {
    if (!V.blast) return;
    var age = t - V.blastAt;
    if (age < 0) return;
    var k = V.blast.scale;
    var cx = V.blast.x, cy = V.blast.y;

    /* 冲击环：暖橙一圈 + 墨圈两圈 */
    for (var r = 0; r < 3; r++) {
      var a0 = age - r * 0.07;
      if (a0 < 0) continue;
      var R = (90 + a0 * 1500) * k * U;
      var al = (1 - ss(0.02, 0.78, a0)) * (0.42 - r * 0.12);
      if (al <= 0.01) continue;
      c.save();
      c.strokeStyle = r === 0 ? "rgba(255,170,80," + al + ")" : "rgba(150,50,16," + (al * 0.5) + ")";
      c.lineWidth = Math.max(0.8, (7.5 - r * 2.0) * Math.max(0.28, 1 - a0 * 1.1) * U);
      c.beginPath();
      for (var s = 0; s <= 44; s++) {
        var ang = s / 44 * TAU;
        var rr = R * (1 + 0.022 * Math.sin(ang * 7 + r * 2.1));
        var x = cx + Math.cos(ang) * rr;
        var y = cy + Math.sin(ang) * rr * 0.88;
        if (s === 0) c.moveTo(x, y); else c.lineTo(x, y);
      }
      c.stroke();
      c.restore();
    }

    /* 墨烟：先在火球后面铺开，避免把火心盖成灰绿 */
    if (age < 1.7) {
      var f0 = 1 - ss(0.40, 1.7, age);
      var grow0 = eOut(clamp(age / 0.9, 0, 1));
      c.save();
      for (var s0 = 0; s0 < V.smokeBlobs.length; s0++) {
        var sm0 = V.smokeBlobs[s0];
        var d0 = (60 + 190 * sm0.dist * grow0) * k * U;
        var R0 = (34 + 110 * sm0.r * grow0) * k * U;
        var x0 = cx + Math.cos(sm0.ang) * d0;
        var y0 = cy + Math.sin(sm0.ang) * d0 * 0.8 - age * 55 * sm0.up * U;
        var sg0 = c.createRadialGradient(x0, y0, 0, x0, y0, R0);
        sg0.addColorStop(0, "rgba(38,23,15," + (0.30 * f0) + ")");
        sg0.addColorStop(0.6, "rgba(38,23,15," + (0.14 * f0) + ")");
        sg0.addColorStop(1, "rgba(38,23,15,0)");
        c.fillStyle = sg0;
        c.beginPath();
        c.arc(x0, y0, R0, 0, TAU);
        c.fill();
      }
      c.restore();
    }

    /* 火球：暗红底 + 外圈火舌 + 金芯 */
    var g = eOut(clamp(age / 0.30, 0, 1));
    var R2 = (90 + 240 * g) * k * U;
    if (age < 1.00) {
      var al2 = 1 - ss(0.18, 0.92, age);
      c.save();
      c.translate(cx, cy);
      c.globalAlpha = al2;

      var go = eOut(clamp(age / 0.30, 0, 1)) * (1 - ss(0.46, 0.98, age));

      /* 外焰：从火团边缘向外舔，不描边 */
      for (var i = 0; i < V.tongues.length; i++) {
        var po = V.tongues[i];
        var lo = po.len * R2 * 0.78 * go;
        if (lo < 10 * U) continue;
        c.save();
        c.rotate(po.ang + 0.10 * Math.sin(t * 5 + po.seed));
        c.translate(R2 * 0.40, 0);
        drawTongue(c, lo, R2 * 0.26 * po.wid, po.seed, t, "#b32c0c", "#ff7a24");
        c.restore();
      }

      /* 火团本体：暗红边 → 橙 → 金心 */
      var massR = R2 * 0.86;
      var mg = c.createRadialGradient(0, 0, massR * 0.05, 0, 0, massR);
      mg.addColorStop(0, "rgba(255,232,166,1)");
      mg.addColorStop(0.24, "rgba(255,190,86,1)");
      mg.addColorStop(0.52, "rgba(250,124,32,1)");
      mg.addColorStop(0.76, "rgba(214,66,18,1)");
      mg.addColorStop(0.93, "rgba(150,36,10,0.92)");
      mg.addColorStop(1, "rgba(120,26,8,0)");
      c.fillStyle = mg;
      blobPath(c, V.mass, 0, 0, massR, 0.2, 0.96);
      c.fill();

      /* 纹理：让火面有暗斑和亮斑，不是一块奶油 */
      c.save();
      blobPath(c, V.mass, 0, 0, massR, 0.2, 0.96);
      c.clip();
      c.globalAlpha = 0.55 * al2;
      c.rotate(t * 0.18);
      var texS = massR * 2.6;
      c.drawImage(V.tex, -texS / 2, -texS / 2, texS, texS);
      c.restore();

      /* 内焰：金黄，长在火团里面 */
      var gi = eOut(clamp(age / 0.24, 0, 1)) * (1 - ss(0.42, 0.96, age));
      for (var j = 0; j < V.innerTongues.length; j++) {
        var pi = V.innerTongues[j];
        var li = pi.len * R2 * 0.46 * gi;
        if (li < 8 * U) continue;
        c.save();
        c.rotate(pi.ang + 0.14 * Math.sin(t * 6 + pi.seed));
        c.translate(R2 * 0.12, 0);
        drawTongue(c, li, R2 * 0.17 * pi.wid, pi.seed, t, "#ff9a3c", "#ffd27a");
        c.restore();
      }

      /* 核心：暖金团 */
      var coreR = Math.max(14 * U, R2 * 0.40);
      var grd = c.createRadialGradient(0, 0, 0, 0, 0, coreR);
      grd.addColorStop(0, "rgba(255,236,180,1)");
      grd.addColorStop(0.32, "rgba(255,196,96,1)");
      grd.addColorStop(0.68, "rgba(246,116,30,0.98)");
      grd.addColorStop(0.92, "rgba(196,52,12,0.85)");
      grd.addColorStop(1, "rgba(150,34,8,0)");
      c.fillStyle = grd;
      blobPath(c, V.blastPoly, 0, 0, coreR, 0.7, 0.92);
      c.fill();

      /* 白热小核 */
      c.save();
      c.globalCompositeOperation = "lighter";
      softGlow(c, 0, 0, coreR * 0.34, "#ffeaae", 0.22);
      c.restore();
      c.restore();
    }

    /* 飞溅的余烬 */
    if (age < 1.2) {
      c.save();
      c.lineCap = "round";
      for (var e = 0; e < V.blastSparks.length; e++) {
        var bs = V.blastSparks[e];
        if (age > bs.life) continue;
        var q = age / bs.life;
        var d3 = bs.spd * age * (1 - 0.25 * q) * k * 0.75 * U;
        var x3 = cx + Math.cos(bs.ang) * d3;
        var y3 = cy + Math.sin(bs.ang) * d3 * 0.9 + 320 * U * age * age;
        var lx = x3 - Math.cos(bs.ang) * bs.len * (0.3 + 0.7 * (1 - q)) * U;
        var ly = y3 - Math.sin(bs.ang) * bs.len * (0.3 + 0.7 * (1 - q)) * U;
        c.strokeStyle = "rgba(140,40,10," + (0.30 * (1 - q)) + ")";
        c.lineWidth = Math.max(1, bs.w * (1 - 0.3 * q) + 2.4 * U);
        c.beginPath();
        c.moveTo(lx, ly);
        c.lineTo(x3, y3);
        c.stroke();
        c.strokeStyle = "rgba(255," + bs.g + ",90," + (1 - q) + ")";
        c.lineWidth = Math.max(0.8, bs.w * (1 - 0.4 * q));
        c.beginPath();
        c.moveTo(lx, ly);
        c.lineTo(x3, y3);
        c.stroke();
      }
      c.restore();
    }

    /* 顶上再飘几缕烟，给火球一点厚度 */
    if (age > 0.08 && age < 1.4) {
      var ft = (1 - ss(0.4, 1.4, age)) * 0.4;
      c.save();
      for (var w = 0; w < 5; w++) {
        var wa = -1.9 + w * 0.55;
        var wd = (0.55 + 0.25 * w) * R2 * k;
        var wx = cx + Math.cos(wa) * wd;
        var wy = cy + Math.sin(wa) * wd - age * 40 * U;
        var wg = c.createRadialGradient(wx, wy, 0, wx, wy, R2 * 0.22);
        wg.addColorStop(0, "rgba(44,26,17," + (0.16 * ft) + ")");
        wg.addColorStop(1, "rgba(44,26,17,0)");
        c.fillStyle = wg;
        c.beginPath();
        c.arc(wx, wy, R2 * 0.22, 0, TAU);
        c.fill();
      }
      c.restore();
    }
  }

  /* 4 档：陨石压境——天光压暗、火云聚过来、落点被火环锁住
   * 火云 / 天光都是**画面空间**的东西（参考舞台里它们贴着 1920×1080 的顶边），故按 W/H 的
   * 比例取坐标：导出档 W = 1920 时就是素材页那几个数，牌桌档跟着覆盖层缩放。 */
  function drawSky(c, t, V) {
    if (!V.meteor) return;
    var m = V.meteor;
    var rise = ss(m.skyAt - 0.86, m.skyAt - 0.04, t);
    var fall = 1 - ss(m.hit + 0.30, m.hit + 1.30, t);
    var on = rise * fall;
    if (on <= 0.01) return;

    c.save();
    c.fillStyle = "rgba(92,46,22," + (0.10 * on) + ")";
    c.fillRect(0, 0, W, H);
    c.restore();

    c.save();
    for (var i = 0; i < V.clouds.length; i++) {
      var cl = V.clouds[i];
      var x = cl.x + Math.sin(t * 0.35 + cl.seed) * 26 * U + t * 5 * U;
      var y = cl.y + Math.cos(t * 0.29 + cl.seed) * 10 * U;
      var g = c.createRadialGradient(x, y, 0, x, y, cl.rad);
      g.addColorStop(0, "rgba(146,84,48," + (0.12 * on) + ")");
      g.addColorStop(0.55, "rgba(146,84,48," + (0.055 * on) + ")");
      g.addColorStop(1, "rgba(146,84,48,0)");
      c.fillStyle = g;
      c.beginPath();
      c.arc(x, y, cl.rad, 0, TAU);
      c.fill();
    }
    var wg = c.createLinearGradient(0, H * 0.278, 0, 0);
    wg.addColorStop(0, "rgba(255,120,40,0)");
    wg.addColorStop(0.55, "rgba(255,120,40," + (0.09 * on) + ")");
    wg.addColorStop(1, "rgba(255,120,40," + (0.16 * on) + ")");
    c.fillStyle = wg;
    c.fillRect(W * 0.49, 0, W * 0.47, H * 0.287);
    c.restore();

    /* 云内预闪 */
    var pres = [m.skyAt - 0.62, m.skyAt - 0.40, m.skyAt - 0.16];
    for (var p = 0; p < pres.length; p++) {
      var age = t - pres[p];
      if (age < 0 || age > 0.22) continue;
      var k = 1 - age / 0.22;
      c.save();
      c.globalCompositeOperation = "lighter";
      softGlow(c, W * (0.693 + p * 0.047), H * (0.139 + p * 0.024),
        (130 + 90 * k) * U, "#ff7a24", 0.14 * k);
      c.restore();
    }

    /* 天边的一点火种 */
    if (t < m.skyAt) {
      var pre = ss(m.skyAt - 0.40, m.skyAt, t);
      if (pre > 0.01) {
        c.save();
        c.globalCompositeOperation = "lighter";
        softGlow(c, W * 0.838, -H * 0.019, (90 + 60 * pre) * U, "#ffb35a", 0.5 * pre);
        c.restore();
      }
    }

    /* 目标牌外围的火环 */
    var lock = ss(m.skyAt - 0.62, m.skyAt - 0.12, t) * (1 - ss(m.hit, m.hit + 0.25, t));
    if (lock > 0.01) {
      c.save();
      c.translate(DST.cx, DST.cy);
      c.rotate(t * 0.45);
      c.strokeStyle = "rgba(226,74,26," + (0.40 * lock) + ")";
      c.lineWidth = Math.max(1, 2 * U);
      c.setLineDash([26 * U, 20 * U]);
      c.beginPath();
      c.ellipse(0, 0, DST.w * 0.58, DST.h * 0.54, 0, 0, TAU);
      c.stroke();
      c.setLineDash([]);
      c.restore();
      c.save();
      c.globalCompositeOperation = "lighter";
      softGlow(c, m.p1.x, m.p1.y, (60 + 40 * lock) * U, "#ff7a24", 0.20 * lock);
      c.restore();
    }
  }

  /* 4 档：落点周围的热浪、上浮的余烬与点点火雨 */
  function drawHeat(c, t, V) {
    if (!V.meteor) return;
    var m = V.meteor;
    var on = ss(m.skyAt - 0.82, m.skyAt - 0.20, t) * (1 - ss(m.hit, m.hit + 0.16, t));
    if (on <= 0.01) return;

    c.save();
    c.globalCompositeOperation = "lighter";
    softGlow(c, m.p1.x, m.p1.y, (30 + 80 * on) * U, "#ff8a2e", 0.16 * on);
    c.restore();

    for (var i = 0; i < 18; i++) {
      var ph = (t * 0.42 + i * 0.137) % 1;
      var x = DST.cx + (hash(i * 3.7) - 0.5) * DST.w * 0.72;
      var y = DST.cy + DST.h * 0.264 - ph * DST.h * 0.559;
      c.fillStyle = "rgba(255," + (150 + Math.floor(80 * hash(i))) + ",80," + ((1 - ph) * on * 0.8) + ")";
      c.beginPath();
      c.arc(x + Math.sin(t * 2 + i) * 8 * U, y, (1.3 + 2.0 * hash(i * 5.1)) * U, 0, TAU);
      c.fill();
    }

    for (var j = 0; j < 14; j++) {
      var f = (t * 0.55 + j * 0.071) % 1;
      var fx = W * 0.547 + hash(j * 7.3) * W * 0.365 - f * W * 0.0625;
      var fy = -H * 0.037 + f * H * 0.574;
      var fa = Math.min(1, f * 4) * (1 - f) * on * 0.8;
      c.fillStyle = "rgba(255," + (140 + Math.floor(70 * hash(j * 3.1))) + ",70," + fa + ")";
      c.beginPath();
      c.arc(fx, fy, (1.2 + 1.6 * hash(j * 2.7)) * U, 0, TAU);
      c.fill();
    }
  }

  function drawMeteor(c, t, V) {
    if (!V.meteor) return;
    var m = V.meteor;
    if (t < m.skyAt || t >= m.hit) return;
    var u = clamp((t - m.skyAt) / (m.hit - m.skyAt), 0, 1);
    var e = Math.pow(u, 1.7);
    var head = qbez(m.p0, m.c, m.p1, e);
    var ang = Math.atan2(head.dy, head.dx);
    var grow = 0.55 + 0.45 * e;

    /* 尾焰：沿来路 30 段暖光 */
    c.save();
    for (var i = 30; i >= 1; i--) {
      var uu = Math.max(0, e - i * 0.017);
      var q = qbez(m.p0, m.c, m.p1, uu);
      var kk = 1 - i / 31;
      var R = (10 + 54 * kk) * grow * U;
      var g = c.createRadialGradient(q.x, q.y, 0, q.x, q.y, R);
      g.addColorStop(0, "rgba(255,150,50," + (0.14 + 0.28 * kk) + ")");
      g.addColorStop(1, "rgba(255,120,40,0)");
      c.fillStyle = g;
      c.beginPath();
      c.arc(q.x, q.y, R, 0, TAU);
      c.fill();
    }
    c.restore();

    /* 烟尾：深墨团 */
    c.save();
    for (var s = 8; s >= 2; s -= 2) {
      var us = Math.max(0, e - s * 0.05);
      var qs = qbez(m.p0, m.c, m.p1, us);
      var kk2 = 1 - s / 10;
      c.fillStyle = "rgba(48,30,20," + (0.12 * kk2) + ")";
      c.beginPath();
      c.arc(qs.x + Math.sin(t * 2 + s) * 12 * U, qs.y - s * 5 * U,
        (20 + 36 * kk2) * U, 0, TAU);
      c.fill();
    }
    c.restore();

    /* 火球本体：长尾 + 白热头 */
    c.save();
    c.translate(head.x, head.y);
    c.rotate(ang);
    c.save();
    c.rotate(Math.PI);
    drawTongue(c, 250 * grow * U, 150 * grow * U, 9.1, t, "#b32c0c", "#ff9a3c");
    drawTongue(c, 175 * grow * U, 100 * grow * U, 4.4, t, "#d63a12", "#ffb452");
    c.save();
    c.translate(0, 46 * grow * U);
    c.rotate(-0.12);
    drawTongue(c, 140 * grow * U, 52 * grow * U, 2.2, t, "#b32c0c", "#ff8a2a");
    c.restore();
    c.save();
    c.translate(0, -46 * grow * U);
    c.rotate(0.12);
    drawTongue(c, 130 * grow * U, 48 * grow * U, 6.6, t, "#b32c0c", "#ff8a2a");
    c.restore();
    c.restore();

    c.save();
    c.globalCompositeOperation = "lighter";
    softGlow(c, 0, 0, 180 * grow * U, "#ff8a2e", 0.32);
    softGlow(c, 0, 0, 70 * grow * U, "#ffd894", 0.55);
    c.restore();

    c.fillStyle = "rgba(255,252,238,0.95)";
    c.beginPath();
    c.arc(0, 0, 20 * grow * U, 0, TAU);
    c.fill();
    c.restore();

    /* 掉下来的余烬 */
    c.save();
    for (var j = 0; j < 14; j++) {
      var uj = Math.max(0, e - 0.03 - j * 0.033);
      var qj = qbez(m.p0, m.c, m.p1, uj);
      var h1 = hash(j * 3.1 + Math.floor(t * 30));
      var h2 = hash(j * 5.7 + Math.floor(t * 24));
      c.fillStyle = "rgba(255," + (150 + Math.floor(80 * h1)) + ",70," + (0.62 * (1 - j / 15)) + ")";
      c.beginPath();
      c.arc(qj.x + (h1 - 0.5) * 60 * U, qj.y + (h2 - 0.5) * 60 * U,
        (1.6 + 2.6 * h1) * U, 0, TAU);
      c.fill();
    }
    c.restore();
  }

  /* ================================================================
   * 飘字 / 整屏闪
   * ============================================================== */
  function numGradient(c, size) {
    var g = c.createLinearGradient(0, -size * 0.58, 0, size * 0.58);
    g.addColorStop(0, "#ffe28c");
    g.addColorStop(0.26, "#ffad48");
    g.addColorStop(0.55, "#ff6a24");
    g.addColorStop(0.80, "#e2371a");
    g.addColorStop(1, "#b3261e");
    return g;
  }

  function drawNumber(c, t, V) {
    var age = t - V.numAt;
    if (age < 0 || age > V.numLife) return;

    var pop = eBack(clamp(age / 0.26, 0, 1));
    var rise = eOut(clamp(age / 1.65, 0, 1));
    var alpha = 1 - ss(V.numLife - 0.60, V.numLife, age);
    if (alpha <= 0.01) return;
    var size = V.numSize;
    var txt = "-" + V.amount;

    c.save();
    /* 位置按目标牌的比例给（参考舞台 460×644 时正好是 cx + 4 / cy − 8 / 上浮 150）；
     * 牌桌档由 `create()` 按体力条硬线重算 `numOffset`。 */
    var ox = V.numOffset ? V.numOffset.dx : 4 / CARD.w;
    var oy = V.numOffset ? V.numOffset.dy : 8 / CARD.h;
    var riseK = V.numOffset ? V.numOffset.rise : 150 / CARD.h;
    c.translate(DST.cx + DST.w * ox, DST.cy - DST.h * oy - rise * DST.h * riseK);
    c.rotate(-0.05 + (1 - rise) * 0.035);
    c.scale(0.60 + 0.40 * pop, 0.60 + 0.40 * pop);
    c.textAlign = "center";
    c.textBaseline = "middle";

    /* 暗底：把字从火里托出来 */
    var dk = c.createRadialGradient(0, 0, 0, 0, 0, size * 1.05);
    dk.addColorStop(0, "rgba(72,16,6," + (0.30 * alpha) + ")");
    dk.addColorStop(0.6, "rgba(72,16,6," + (0.14 * alpha) + ")");
    dk.addColorStop(1, "rgba(72,16,6,0)");
    c.fillStyle = dk;
    c.beginPath();
    c.arc(0, 0, size * 1.05, 0, TAU);
    c.fill();

    /* 暖光底：橙红的光晕 */
    var glowR = size * (0.90 + 0.10 * V.dmg) * V.numGlow;
    var gr = c.createRadialGradient(0, 0, 0, 0, 0, glowR);
    gr.addColorStop(0, "rgba(255,110,36," + (0.18 * alpha) + ")");
    gr.addColorStop(0.5, "rgba(255,90,30," + (0.07 * alpha) + ")");
    gr.addColorStop(1, "rgba(255,90,30,0)");
    c.fillStyle = gr;
    c.beginPath();
    c.arc(0, 0, glowR, 0, TAU);
    c.fill();

    /* 残影：高档位两层 */
    if (V.numEcho > 0) {
      for (var e = V.numEcho; e >= 1; e--) {
        c.save();
        c.globalAlpha = alpha * 0.22 * (1 - e / (V.numEcho + 1.4));
        c.font = "700 " + size + "px " + SERIF;
        c.fillStyle = numGradient(c, size);
        c.fillText(txt, e * 13, -e * 16);
        c.restore();
      }
    }

    c.font = "700 " + size + "px " + SERIF;
    c.globalAlpha = alpha;
    c.lineJoin = "round";

    /* 暗影 */
    c.globalAlpha = alpha * 0.26;
    c.fillStyle = "#2a0d06";
    c.fillText(txt, size * 0.03, size * 0.04);

    /* 纸色外晕（压住焦痕，别黑成一团） */
    c.globalAlpha = alpha * 0.80;
    c.strokeStyle = "rgba(255,250,240,0.95)";
    c.lineWidth = size * 0.046;
    c.strokeText(txt, 0, 0);

    /* 深赭描边 */
    c.globalAlpha = alpha;
    c.strokeStyle = "#4a1006";
    c.lineWidth = size * 0.021;
    c.strokeText(txt, 0, 0);

    /* 橙红字身 */
    c.fillStyle = numGradient(c, size);
    c.fillText(txt, 0, 0);

    /* 3 档起：数字底下往上飘的余烬 */
    if (V.dmg >= 3) {
      c.globalCompositeOperation = "lighter";
      for (var i = 0; i < 10; i++) {
        var ph = ((age * 0.55 + i * 0.137) % 1);
        var x = (hash(i * 3.1 + V.dmg) - 0.5) * size * 2.2;
        var y = size * 0.55 - ph * size * 2.4;
        c.fillStyle = "rgba(255," + (150 + Math.floor(70 * hash(i + 9))) + ",80," + (0.55 * (1 - ph) * alpha) + ")";
        c.beginPath();
        c.arc(x, y, 1.6 + 2.2 * hash(i * 7.7), 0, TAU);
        c.fill();
      }
    }
    c.restore();
  }

  function flashStrike(age, peak, hold, decay) {
    if (age < 0) return 0;
    return peak * ss(0, 0.008, age) * Math.exp(-Math.max(0, age - hold) * decay);
  }

  /* 整屏暖白闪（**只在导出档画**）。牌桌档由排播层挂 `.fx-flash.fx-flash-fire` 子层：
   * 在画布内 fillRect 会盖住牌桌（spec §整屏效果）。 */
  function drawFlash(c, t, V) {
    var a = 0;
    if (V.dmg === 3 && V.blast) {
      var age = t - V.blastAt;
      if (age >= 0) {
        a = Math.max(
          flashStrike(age, 0.42, 0.02, 13),
          flashStrike(age - 0.06, 0.22, 0.01, 18)
        );
      }
    } else if (V.dmg === 4 && V.meteor) {
      var a0 = t - (V.meteor.hit - 0.10);
      if (a0 >= 0) a = Math.max(a, flashStrike(a0, 0.26, 0.008, 26));
      var a1 = t - V.meteor.hit;
      if (a1 >= 0) {
        a = Math.max(a,
          flashStrike(a1, 0.60, 0.030, 11),
          flashStrike(a1 - 0.09, 0.42, 0.010, 14),
          flashStrike(a1 - 0.20, 0.30, 0.008, 15)
        );
      }
    }
    if (a <= 0.004) return;
    a = Math.min(0.92, a);
    var cx = V.blast ? V.blast.x : W / 2;
    var cy = V.blast ? V.blast.y : H / 2;
    c.save();
    var g = c.createRadialGradient(cx, cy, 0, cx, cy, 1180 * Math.max(U, 0.6));
    g.addColorStop(0, "rgba(255,240,206," + a + ")");
    g.addColorStop(0.55, "rgba(255,232,198," + (a * 0.94) + ")");
    g.addColorStop(1, "rgba(255,218,166," + (a * 0.84) + ")");
    c.fillStyle = g;
    c.fillRect(0, 0, W, H);
    c.restore();
  }

  /* ================================================================
   * 飘字口径（与 `fx-core.js` / `fx-thunder.js` 同一套包围盒判据）
   * ============================================================== */
  var GLYPH_RATIO = 0.7;
  var NUMBER_MAX_RATIO = 0.35;
  var NUMBER_KEEP_OUT_FALLBACK = 0.45;
  var NUMBER_GAP_RATIO = 0.02;
  var NUMBER_RISE_RATIO = 0.10;
  var NUMBER_BELOW_MIN = 0.02;
  var SCREEN_MIN_NUMBER = 44;

  /* ================================================================
   * 入口（与 `SgsDamageFX.create` / `SgsThunderFX.create` 同形：排播层不认第二种形状）
   * ============================================================== */
  function create(canvas, opts) {
    opts = opts || {};
    var clock = clockFor(opts);
    var id = clock.id;
    var instT = clock.timeline;
    var warp = clock.warp;
    var stage = stageFor(opts.stage);
    var bare = opts.bare === true;
    var size = viewSize(opts.view);
    var useView = bare || Boolean(opts.view);
    var instW = useView ? size.w : REF_W;
    var instH = useView ? size.h : REF_H;

    var dpr = Number(opts.dpr) > 0 ? Number(opts.dpr) : 1;

    /* 每个实例一份可变的档位对象：缓存（火羽形状 / 飘字偏移）挂在上面，别污染 TIERS。
     * `blast` / `meteor` 必须**逐实例**换坐标（牌桌的落点是量出来的），故都做一层浅拷贝。 */
    var V = copy(clock.variant, {});
    V.dmg = TIERS[id].dmg;
    /* 档位数字只决定强度；飘字照真实伤害写（伤害 ≥5 时档位仍是 4，数字写 5）。 */
    var amount = Math.round(Number(opts.amount));
    V.amount = isFinite(amount) && amount > 0 ? amount : TIERS[id].dmg;

    /* 先切帧上下文：下面的形状生成器读 A / B / W / H。 */
    setFrame(stage, instW, instH);
    V.blast = V.blast ? copy(V.blast, {
      x: B.x + (Number(V.blast.dx) || 0) * U,
      y: B.y + (Number(V.blast.dy) || 0) * U
    }) : null;
    V.meteor = V.meteor ? copy(V.meteor, {
      /* 流星从画面右上斜落：起手 / 控制点按画布比例（导出档 W = 1920 就是素材页的坐标），
       * 落点就是命中锚 B。 */
      p0: { x: W * 0.8875, y: -H * 0.1667 },
      c: { x: W * 0.7917, y: H * 0.0593 },
      p1: { x: B.x, y: B.y }
    }) : null;

    /* 飘字：牌桌档按目标牌体力条下沿的硬线反推起飘点（与 `fx-core.js` 的 R3 / R4 同一套
     * 包围盒判据）；导出档沿用素材页的 cx+4 / cy−8 / 上浮 150。 */
    var numOffset = { dx: 4 / CARD.w, dy: 8 / CARD.h, rise: 150 / CARD.h };
    var keepOutPx = null;
    if (bare) {
      var cardH = Number(stage.dst.h) || CARD.h;
      var glyph = clamp(
        V.numSize * GLYPH_RATIO * U, SCREEN_MIN_NUMBER, NUMBER_MAX_RATIO * cardH
      );
      V.numSize = glyph / GLYPH_RATIO;
      var keepOut = Number(opts.numberKeepOut);
      var keepOutRatio = isFinite(keepOut) && keepOut > 0 && keepOut < cardH
        ? keepOut / cardH
        : NUMBER_KEEP_OUT_FALLBACK;
      keepOutPx = keepOutRatio * cardH;
      var boxTopRatio = Math.min(0.9, keepOutRatio + NUMBER_GAP_RATIO);
      var belowCenter = boxTopRatio + glyph / (2 * cardH) - 0.5 + NUMBER_RISE_RATIO;
      belowCenter = Math.max(belowCenter, NUMBER_BELOW_MIN);
      belowCenter = Math.min(belowCenter, 0.5 - glyph / (2 * cardH) - 0.01);
      numOffset = { dx: 4 / CARD.w, dy: -belowCenter, rise: NUMBER_RISE_RATIO };
    }
    V.numOffset = numOffset;
    var cardTop = stage.dst.cy - stage.dst.h / 2;
    var numCenterY = stage.dst.cy - stage.dst.h * numOffset.dy
      - stage.dst.h * numOffset.rise;
    var glyphHeight = V.numSize * GLYPH_RATIO;
    var numberBox = {
      top: numCenterY - glyphHeight / 2 - cardTop,
      bottom: numCenterY + glyphHeight / 2 - cardTop,
      height: glyphHeight
    };

    var tex = texture("fireTex", paintFlameTex);
    V.tex = tex;

    var r = rng(6400 + id * 977);
    V.arrows = buildVolley(V, r);
    V.blastPoly = blobShape(r, 15, 0.62, 1.0);
    V.burnBlob = blobShape(r, 10, 0.55, 1.15);
    V.tongues = [];
    for (var i = 0; i < 24; i++) {
      V.tongues.push({
        ang: i / 24 * TAU + (r() - 0.5) * 0.34,
        len: 0.55 + r() * 0.85,
        wid: 0.65 + r() * 0.7,
        seed: r() * 10
      });
    }
    V.innerTongues = [];
    for (i = 0; i < 11; i++) {
      V.innerTongues.push({
        ang: i / 11 * TAU + (r() - 0.5) * 0.5,
        len: 0.5 + r() * 0.6,
        wid: 0.7 + r() * 0.7,
        seed: r() * 10
      });
    }
    V.mass = blobShape(r, 20, 0.72, 1.16);
    V.smokeBlobs = [];
    for (i = 0; i < 12; i++) {
      V.smokeBlobs.push({ ang: r() * TAU, dist: 0.30 + r() * 0.95, r: 0.35 + r() * 0.7, seed: r() * 10, up: 0.4 + r() * 0.9 });
    }
    V.blastSparks = [];
    for (i = 0; i < 90; i++) {
      V.blastSparks.push({
        ang: r() * TAU,
        spd: 160 + r() * 980,
        life: 0.36 + r() * 0.72,
        len: 8 + r() * 28,
        w: 1 + r() * 2.4,
        g: 150 + Math.floor(r() * 85)
      });
    }
    /* 火云天光：画面空间（贴顶边），按 W/H 比例取坐标——导出档与素材页同值。 */
    V.clouds = [];
    for (i = 0; i < 6; i++) {
      V.clouds.push({
        x: W * (0.6302 + r() * 0.3229),
        y: H * (0.0185 + r() * 0.1574),
        rad: W * (0.0625 + r() * 0.0781),
        seed: r() * 10
      });
    }

    canvas.width = Math.round(instW * dpr);
    canvas.height = Math.round(instH * dpr);
    var c = canvas.getContext("2d");

    /* 三张静态图（宣纸底 / 两张样例武将牌）只在导出档用；牌桌档 `bare` 一概不画。
     * 它们只读，跨实例复用（每次伤害都建一个新实例，重画 1920×1080 的纸底是主线卡顿）。 */
    var paper = bare ? null
      : texture("firePaper@" + dpr, function () { return paintPaper(dpr); });
    var srcImg = bare ? null
      : texture("fireSrc@" + dpr, function () { return paintHero(HEROES.src, dpr); });
    var dstImg = bare ? null
      : texture("fireDst@" + dpr, function () { return paintHero(HEROES.dst, dpr); });

    function draw(t) {
      t = clamp(t, 0, instT.DURATION);
      // 牌桌档：先把演出自己的秒数映射回素材页的秒数（整条链等比压，画法一行不改）。
      if (warp) t = warpAt(warp, t);
      // 每条演出开画前切帧：几条并发依次 draw，互不串味（见文件顶的帧上下文说明）。
      setFrame(stage, instW, instH);
      var sh = shakeAt(t, V);
      var zoom = blastZoom(t, V);
      var push = blastPush(t, V);
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, instW, instH);

      c.save();
      c.translate(instW / 2, instH / 2);
      c.scale(zoom, zoom);
      c.translate(-instW / 2, -instH / 2);
      c.translate(sh.x, sh.y);

      if (!bare) drawPaper(c, paper);
      drawSky(c, t, V);

      if (!bare) {
        var kick = cardKick(t, V);
        drawCard(c, srcImg, SRC, -kick.dx * 0.28 - push, kick.dy * 0.14, -kick.rot * 0.2);
        drawSourceFire(c, t, V);
        drawCard(c, dstImg, DST, kick.dx + push * 0.8, kick.dy, kick.rot);
      } else {
        // 牌桌上的真实武将块不归本模块画；但"来源牌上的火印亮起"要落在它上面。
        drawSourceFire(c, t, V);
      }

      drawBurn(c, t, V);
      drawFireLight(c, t, V);

      drawVolley(c, t, V);
      drawHits(c, t, V);
      drawMeteor(c, t, V);
      drawBlast(c, t, V);
      drawHeat(c, t, V);
      drawNumber(c, t, V);
      c.restore();

      // 牌桌档不画整屏暖白闪（那会盖住牌桌）：改由排播层挂 `.fx-flash-fire` 着色子层。
      if (!bare) drawFlash(c, t, V);
    }

    return {
      draw: draw,
      duration: instT.DURATION,
      variant: id,
      amount: V.amount,
      width: instW,
      height: instH,
      profile: clock.profile,
      timeline: instT,
      timing: clock.ms,
      stage: stage,
      bare: bare,
      metrics: {
        u: stage.u,
        number: glyphHeight,
        numberBox: numberBox,
        keepOut: keepOutPx,
        card: { w: stage.dst.w, h: stage.dst.h }
      }
    };
  }

  global.SgsFireFX = {
    create: create,
    TIERS: TIERS,
    // `TIMELINE` = 4 档的天陨链（导出档关键点的样本）；牌桌档读 `gameTiming()`。
    TIMELINE: exportTimeline(4),
    COLORS: C,
    // 票 03：与 `SgsDamageFX` / `SgsThunderFX` 同名的导出面（`damage-fx.js` 按属性取字段表 / 时长）。
    DEFAULT_TIMING: DEFAULT_TIMING,
    MAX_TOTAL_MS: MAX_TOTAL_MS,
    TIMING_PREFIX: TIMING_PREFIX,
    TIMING_FIELDS: TIMING_FIELDS,
    TIMING_KEYS: TIMING_KEYS,
    gameTiming: gameTiming,
    SCREEN_MIN: { number: SCREEN_MIN_NUMBER },
    COMPOSITION_FLOOR: COMPOSITION_FLOOR
  };
})(window);
