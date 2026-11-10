"use strict";

/* SGS 前端 · **雷电**伤害演出的画面（damage-fx-natures 票 01）：`SgsThunderFX`。
 *
 * 移植自素材包 `outputs/sgs-thunder-fx/fx-thunder.js`（可播放的定稿参考实现）：雷光 /
 * 电蛇 / 墨爆 / 蓝紫飘字 / 4 档「天罚」。搬进牌桌时只改四类事，画法逐段保留：
 *
 * 1. **双时间轴**（同 `fx-core.js` 的姿态）：`profile: "export"` 照抄素材页的 5~6 秒链
 *    （`?still` / `?export` / node 探针逐帧导静帧）；`profile: "game"` 读 `anim.ini` 的
 *    `damage_fx_thunder_<档>_<字段>`（缺键退 `DEFAULT_TIMING`，钳制住在 `gameTiming` 里）。
 *    `TIMING_FIELDS` / `TIMING_KEYS` 由 `DEFAULT_TIMING` 的键**生成**——字段名只有一处。
 * 2. **座位舞台**：`stage: {src, dst}` 是覆盖层量出来的武将块矩形（CSS px）。雷印锚在来源
 *    牌右上、命中锚在目标牌偏上（与参考舞台同一比例）。`src === null` ⇒ **无来源天降**：
 *    不画来源雷印，雷从画面上方进入（默认牌堆那 2 张【闪电】的路子）。
 * 3. **`bare: true`**（牌桌硬口径）：只出特效，不画宣纸底与样例武将牌；坐标就是覆盖层
 *    CSS px。整屏白闪**不在画布内画**（那会盖住牌桌），改由排播层挂着色子层。
 * 4. **飘字**：`amount` 是服务端真实点数（≥5 仍播 4 档），位置按目标牌体力条下沿的硬线
 *    反推（与 `fx-core.js` 同一套包围盒判据）。
 *
 * 纯 Canvas 2D，零依赖、零外部资源、不发请求、不碰 DOM。`"use strict";` 是文件第一条语句；
 * 全局名 `window.SgsThunderFX`，不改成 ESM。
 */
(function (global) {

  var TAU = Math.PI * 2;

  /* ---------------------------------------------------------------- 配色 ----
   * 取自 SGS 前端 style.css 的 :root（宣纸 / 墨 / 朱砂），雷电色另加两组：
   * 冷电青（1~3 档）与紫电（4 档天罚）。 */
  var C = {
    paper:  "#f1ebdf",
    card:   "#fffdf7",
    cardLo: "#f8f1e2",
    line:   "#ddd8d0",
    ink:    "#1c1a17",
    dim:    "#6f6a62",
    red:    "#9c2b1b",
    blue:   "#2f5f8f",
    violet: "#5b3a8c",
    white:  "#fffdf8"
  };

  var SERIF = '"Noto Serif SC","Songti SC","STSong","SimSun",serif';
  var SANS  = '"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif';

  /* ------------------------------------------------------ 参考舞台（导出档）---- */
  var REF_W = 1920, REF_H = 1080;

  /* 两张武将牌与参考舞台的摆位（与剑光版同一套坐标，方便并排对比）。 */
  var CARD = { w: 460, h: 644, pad: 74 };
  var REF_SRC = { cx: 520,  cy: 545, w: CARD.w, h: CARD.h };
  var REF_DST = { cx: 1400, cy: 545, w: CARD.w, h: CARD.h };

  /* 雷光航线：从张角牌面右上方的雷印射出，扎进司马懿牌面上半部。 */
  var REF_A = { x: 672, y: 372 };
  var REF_B = { x: 1408, y: 470 };

  /* 两个锚点在**牌面里的比例**：牌桌量出来的武将块大小与参考牌不同，但"雷印在来源牌右上、
   * 命中在目标牌偏上"这条构图在任意座位矩形下都成立（同 `fx-core.js` 的 SWORD_ANCHOR）。 */
  var ANCHOR = {
    ax: (REF_A.x - REF_SRC.cx) / CARD.w,   // 0.3304
    ay: (REF_A.y - REF_SRC.cy) / CARD.h,   // -0.2686
    bx: (REF_B.x - REF_DST.cx) / CARD.w,   // 0.0174
    by: (REF_B.y - REF_DST.cy) / CARD.h    // -0.1165
  };

  /* **当前帧**的舞台与时钟：每条演出在 `draw()` 开头设置一次（`setFrame`）。draw 是同步的，
   * JS 单线程下不存在"画到一半被另一条演出改掉"的窗口——这组是帧上下文，不是共享状态。
   * 导出档（没有 stage）它们就是参考舞台，于是素材页那条链逐字节不变。 */
  var W = REF_W, H = REF_H;
  var SRC = REF_SRC, DST = REF_DST;
  var A = REF_A, B = REF_B;
  var SKY = false, U = 1;

  /* ---------------------------------------------------------------- 武将 ----
   * 这是"样例牌"，只为演示雷光从哪张牌飞到哪张牌；牌面与技能文案不属于特效。 */
  var HEROES = {
    src: {
      name: "张角", faction: "群", factionColor: C.dim, hp: 3,
      skills: [
        ["雷击", "使用或打出【闪】后，可令一名角色判定，♠则受2点雷电伤害"],
        ["鬼道", "打出一张黑色牌替换判定牌"]
      ]
    },
    dst: {
      name: "司马懿", faction: "魏", factionColor: C.blue, hp: 3,
      skills: [
        ["反馈", "受到伤害后，可获得伤害来源的一张牌"],
        ["鬼才", "打出一张手牌替换判定牌"]
      ]
    }
  };

  /* ------------------------------------------------------------ 时间轴 ----
   * `export` 档（素材页）：五个关键点全片共用；四档的差别在"强度参数"，不在结构。
   * 4 档在 IMPACT 之后还有一次 SKY_AT（天雷蓄势）→ EXPLODE（爆炸）。
   * 牌桌档（`game`）的同一组键由 `gameTiming()` 从 `anim.ini` 解出来（见下）。
   *
   * 2000ms 的 4 档把"雷云 → 天雷 → 爆炸"等比压进命中之后那一段：STORM_IN / STORM_FULL /
   * DIM_OUT / PRE_STRIKES 都是**时间轴自己的键**，画法只读它们，不写死素材页的秒数。 */
  var EXPORT_T = {
    DURATION: 0,       // 每档在 exportClock() 里按 TIERS[id].duration 填
    CHARGE:  0.20,     // 蓄电开始
    LAUNCH:  0.84,     // 雷光出牌
    IMPACT:  1.00,     // 第一击命中
    NUM_AT:  1.24,     // 1~3 档飘字（给白闪让 0.24s）
    NUM_LIFE: 1.80,
    SKY_AT:  2.10,     // 4 档：紫雷落下
    EXPLODE: 2.20,     // 4 档：爆炸
    NUM2_AT: 2.48,     // 4 档：飘字（等爆炸白闪退下去）
    NUM2_LIFE: 2.10,
    STORM_IN: 1.04,    // 雷云压顶开始
    STORM_FULL: 1.90,  // 雷云铺满
    STORM_OUT0: 4.20,  // 雷云开始散
    STORM_OUT1: 5.80,  // 雷云散尽
    DIM_OUT0: 2.16,    // 压暗开始退
    DIM_OUT1: 2.28,
    PRE_STRIKES: [1.48, 1.70, 1.90, 2.03]
  };
  /* 当前帧的时间轴（`setFrame` 每帧改写；导出档 = EXPORT_T + 本档 DURATION）。 */
  var T = EXPORT_T;

  /* -------------------------------------------------------------- 四档 ----
   * 一档一行的强度表：雷光粗细 / 分叉数 / 电流数量 / 屏震 / 飘字大小 / 墨量。
   * 所有"越来越强"都只读这张表，画法本身只有一套。 */
  var TIERS = {
    1: {
      dmg: 1, name: "微雷", tag: "细如游丝",
      duration: 5.0, fadeStart: 2.6, fadeEnd: 4.6,
      core: 3.2, branches: 2, rough: 0.19,
      edge: "#1b191f", color: "#4fb8ff", hot: "#f2fdff",
      arcs: 8, arcW: 1.6, arcLife: 0.85,
      ground: 0, shake: 3.5, tilt: 0.005,
      numSize: 120, numEcho: 0, numGlow: 0.55,
      inkDrops: 10, sparks: 16, scorch: 0.30,
      dim: 0, storm: null
    },
    2: {
      dmg: 2, name: "惊雷", tag: "雷光加粗",
      duration: 5.2, fadeStart: 2.8, fadeEnd: 4.8,
      core: 5.4, branches: 3, rough: 0.18,
      edge: "#1b1922", color: "#3aa6ff", hot: "#f2fdff",
      arcs: 12, arcW: 2.4, arcLife: 1.0,
      ground: 0, shake: 6, tilt: 0.009,
      numSize: 146, numEcho: 0, numGlow: 0.7,
      inkDrops: 18, sparks: 26, scorch: 0.45,
      dim: 0, storm: null
    },
    3: {
      dmg: 3, name: "白雷", tag: "整屏全白",
      duration: 5.4, fadeStart: 3.0, fadeEnd: 5.0,
      core: 8.2, branches: 5, rough: 0.17,
      edge: "#1a1820", color: "#2f9cff", hot: "#f6feff",
      arcs: 18, arcW: 3.2, arcLife: 1.25,
      ground: 0, shake: 13, tilt: 0.015,
      numSize: 172, numEcho: 1, numGlow: 0.95,
      inkDrops: 28, sparks: 38, scorch: 0.65,
      strobeMs: 160, dim: 0, storm: null
    },
    4: {
      dmg: 4, name: "天罚", tag: "紫雷天降",
      duration: 6.0, fadeStart: 3.6, fadeEnd: 5.7,
      // 第一道雷光与 3 档同量级；真正拉开差距的是下面这记天雷。
      core: 9.0, branches: 5, rough: 0.16,
      edge: "#1c0f30", color: "#9a64ff", hot: "#f4ecff",
      arcs: 12, arcW: 3.0, arcLife: 1.15,
      ground: 0, shake: 12, tilt: 0.014,
      numSize: 198, numEcho: 2, numGlow: 1.15,
      inkDrops: 24, sparks: 34, scorch: 0.62,
      strobeMs: 260, dim: 0.06,
      storm: {
        core: 40, edge: "#180a2a", color: "#8a4dff", hot: "#f4eaff",
        arcs: 16, arcW: 4.6, arcLife: 2.3, ground: 0,
        shake: 30, tilt: 0.035,
        numSize: 232, numEcho: 2, numGlow: 1.4,
      }
    }
  };

  /* ------------------------------------------------------------ 小工具 ---- */
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ss(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function eOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function eBack(t) { t = clamp(t, 0, 1); var c = 2.0; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); }
  function rad(deg) { return deg * Math.PI / 180; }
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

  /* ================================================================
   * 双时间轴（同 `fx-core.js`）：export = 素材页，game = anim.ini
   * ============================================================== */

  /* `anim.ini` 的 `damage_fx_thunder_<档>_<字段>` 的**内置默认**（磁盘副本在
   * `tools/sgs_web/static/anim.ini`；缺键 / 解析失败 / 非法值 ⇒ 退回这里）。
   * 火 / 雷不需要剑光的 `split`（剑气劈屏），但需要 `flash`（整屏着色闪的强度，0..1）。
   * 单档总时长上限同剑光：2200ms，再长会挡住牌桌。 */
  var TIMING_PREFIX = "damage_fx_thunder_";
  var DEFAULT_TIMING = {
    damage_fx_thunder_1_total: 1400, damage_fx_thunder_1_flight: 110,
    damage_fx_thunder_1_impact: 300, damage_fx_thunder_1_fade: 700,
    damage_fx_thunder_2_total: 1500, damage_fx_thunder_2_flight: 115,
    damage_fx_thunder_2_impact: 310, damage_fx_thunder_2_fade: 750,
    damage_fx_thunder_3_total: 1700, damage_fx_thunder_3_flight: 120,
    damage_fx_thunder_3_impact: 330, damage_fx_thunder_3_fade: 820,
    damage_fx_thunder_3_strobe: 160, damage_fx_thunder_3_flash: 0.6,
    damage_fx_thunder_4_total: 2000, damage_fx_thunder_4_flight: 125,
    damage_fx_thunder_4_impact: 360, damage_fx_thunder_4_fade: 900,
    damage_fx_thunder_4_strobe: 260, damage_fx_thunder_4_flash: 0.6
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

  /* 飘字口径（与 `fx-core.js` 同一套包围盒判据）：字高 ≤ 牌高 35%，起飘点按体力条下沿
   * 反推；量不到 DOM 时的兜底硬线是牌高的 45%。 */
  var GLYPH_RATIO = 0.7;
  var NUMBER_MAX_RATIO = 0.35;
  var NUMBER_KEEP_OUT_FALLBACK = 0.45;
  var NUMBER_GAP_RATIO = 0.02;
  var NUMBER_RISE_RATIO = 0.10;
  var NUMBER_BELOW_MIN = 0.02;
  var SCREEN_MIN_NUMBER = 44;
  /* 牌桌上的落屏下限：1 点档雷光主体 3.2 × 0.625 = 2 CSS px，再小就看不见了。 */
  var COMPOSITION_FLOOR = 0.625;

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

  /* `game` 档：把 `anim.ini` 的雷键解成一档**秒**时间轴 + 档位参数。
   *
   * 钳制四条（同 `fx-core.js`）：`total ≤ 2200`、`flight ≤ impact ≤ total`、
   * `impact + fade ≤ total`（否则层都摘了墨痕还在）。`strobe` 是整屏着色闪的窗口，
   * 只覆盖 3 / 4 档；`SKY_AT` / `EXPLODE` 只对 4 档有意义，按命中之后那一段的比例排。 */
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
    var postS = Math.max(0.001, (total - impact) / 1000);
    var timeline = {
      DURATION: total / 1000,
      CHARGE: Math.max(0, impact - flight) / 1000 * 0.24,
      LAUNCH: (impact - flight) / 1000,
      IMPACT: impact / 1000,
      NUM_AT: impact / 1000 + Math.min(0.12, postS * 0.08),
      NUM_LIFE: 0,
      SKY_AT: 0, EXPLODE: 0, NUM2_AT: 0, NUM2_LIFE: 0,
      STORM_IN: 0, STORM_FULL: 0, STORM_OUT0: 0, STORM_OUT1: 0,
      DIM_OUT0: 0, DIM_OUT1: 0,
      PRE_STRIKES: []
    };
    timeline.NUM_LIFE = Math.max(0.3, timeline.DURATION - timeline.NUM_AT);
    if (base.storm) {
      timeline.STORM_IN = timeline.IMPACT + postS * 0.03;
      timeline.STORM_FULL = timeline.IMPACT + postS * 0.30;
      timeline.SKY_AT = timeline.IMPACT + postS * 0.45;
      timeline.EXPLODE = timeline.IMPACT + postS * 0.52;
      timeline.NUM2_AT = timeline.IMPACT + postS * 0.58;
      timeline.NUM2_LIFE = Math.max(0.3, timeline.DURATION - timeline.NUM2_AT);
      timeline.STORM_OUT0 = timeline.EXPLODE + postS * 0.20;
      timeline.STORM_OUT1 = timeline.DURATION - 0.02;
      timeline.DIM_OUT0 = timeline.EXPLODE - 0.04;
      timeline.DIM_OUT1 = timeline.EXPLODE + 0.08;
      timeline.PRE_STRIKES = [
        timeline.IMPACT + postS * 0.18,
        timeline.IMPACT + postS * 0.26,
        timeline.IMPACT + postS * 0.33,
        timeline.IMPACT + postS * 0.38
      ];
    }
    return {
      id: id,
      profile: "game",
      timeline: timeline,
      ms: { total: total, flight: flight, impact: impact, fade: fade, strobe: strobe, flash: flash },
      variant: copy(base, {
        duration: timeline.DURATION,
        fadeStart: timeline.IMPACT + 0.25 * fade / 1000,
        fadeEnd: timeline.IMPACT + fade / 1000
      })
    };
  }

  /* `export` 档：参考实现的 5~6 秒链（`profile` 缺省走它）。 */
  function exportClock(variantId) {
    var id = clamp(Math.round(variantId || 1), 1, 4);
    return {
      id: id,
      profile: "export",
      timeline: copy(EXPORT_T, { DURATION: TIERS[id].duration }),
      variant: copy(TIERS[id], {}),
      ms: null
    };
  }

  function clockFor(opts) {
    return opts && opts.profile === "game"
      ? gameTiming(opts.variant, opts.timing)
      : exportClock(opts && opts.variant);
  }

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
   * - `src === null` ⇒ **无来源天降**：不猜来源座位，A 锚在目标牌正上方的画面外；
   * - `u` = 它比参考牌 460×644 小多少（不许小于 `COMPOSITION_FLOOR`：雷光主体得看得见）；
   * - 不传 `stage` ⇒ 参考舞台（`u = 1`、A / B 取原常量），素材库那条链逐字节不变。 */
  function stageFor(raw) {
    if (!raw || (!raw.src && !raw.dst)) {
      return { src: REF_SRC, dst: REF_DST, a: REF_A, b: REF_B, u: 1, sky: false };
    }
    var src = raw.src ? rectOr(raw.src, REF_SRC) : null;
    var dst = rectOr(raw.dst, REF_DST);
    var u = Math.min(
      (src ? src.w : dst.w) / CARD.w, (src ? src.h : dst.h) / CARD.h,
      dst.w / CARD.w, dst.h / CARD.h
    );
    if (!isFinite(u) || u <= 0) u = 1;
    u = Math.max(u, COMPOSITION_FLOOR);
    var b = { x: dst.cx + dst.w * ANCHOR.bx, y: dst.cy + dst.h * ANCHOR.by };
    var a = src
      ? { x: src.cx + src.w * ANCHOR.ax, y: src.cy + src.h * ANCHOR.ay }
      // 无来源：雷从画面上方进入（a.y < 0 是烟测 / 实机的几何判据）。
      : { x: b.x + dst.w * 0.05, y: -Math.max(24, dst.h * 0.35) };
    return { src: src, dst: dst, a: a, b: b, u: u, sky: !src };
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

  /* 把画法用的帧上下文切到这条演出：时钟 / 两张牌 / 两个锚点 / 画布尺寸 / 两种模式。 */
  function setFrame(stage, timeline, w, h, bare) {
    T = timeline;
    SRC = stage.src;
    DST = stage.dst;
    A = stage.a;
    B = stage.b;
    W = w;
    H = h;
    SKY = stage.sky === true;
    U = Number(stage.u) > 0 ? Number(stage.u) : 1;
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

  /* ================================================================
   * 宣纸 / 武将牌（一次画进离屏画布，之后每帧只 drawImage）
   * ============================================================== */

  function paintPaper(S) {
    var w = REF_W + 160, h = REF_H + 160;
    var pc = offscreen(w * S, h * S), c = pc[1];
    c.scale(S, S);
    var r = rng(20261110);

    c.fillStyle = C.paper;
    c.fillRect(0, 0, w, h);

    // 纸纤维 & 颗粒：只留一点点，别把宣纸压成灰纸
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

    // 背景：一枚若有若无的枯笔圆相 + 两团宿墨；不能抢牌面
    c.save();
    c.globalCompositeOperation = "multiply";
    var cx = REF_W / 2 + 6, cy = REF_H / 2 + 22;
    for (var k = 0; k < 48; k++) {
      if (r() < 0.18) continue;                      // 断笔，别画成规整的圆环
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
    c.restore();

    // 四角压暗：暖墨，不用冷黑
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

    // 投影（烘进贴图）
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

    // 势力印
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

    // 将名
    c.textAlign = "left";
    c.textBaseline = "middle";
    c.fillStyle = C.ink;
    c.font = "700 38px " + SERIF;
    c.fillText(hero.name, pad + 42, pad + 17);

    // 体力段
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

    // 淡墨竖排将名
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

    // 技能区
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

  var TEX = {};
  function texture(key, paint) {
    if (!TEX[key]) TEX[key] = paint();
    return TEX[key];
  }

  /* ================================================================
   * 雷电几何
   * ============================================================== */
  function midpointBolt(r, ax, ay, bx, by, levels, rough) {
    var pts = [[ax, ay], [bx, by]];
    for (var l = 0; l < levels; l++) {
      var next = [pts[0]];
      for (var i = 1; i < pts.length; i++) {
        var p = pts[i - 1], q = pts[i];
        var dx = q[0] - p[0], dy = q[1] - p[1];
        var len = dist(p[0], p[1], q[0], q[1]) || 1;
        var off = (r() * 2 - 1) * len * rough;
        next.push([(p[0] + q[0]) / 2 - dy / len * off, (p[1] + q[1]) / 2 + dx / len * off]);
        next.push(q);
      }
      pts = next;
    }
    return pts;
  }

  function boltTree(seed, ax, ay, bx, by, tier, sky) {
    var r = rng(seed);
    var main = midpointBolt(r, ax, ay, bx, by, sky ? 5 : 4, Math.max(tier.rough, sky ? 0.22 : 0.24));
    var branches = [];
    var L = dist(ax, ay, bx, by);
    var base = Math.atan2(by - ay, bx - ax);
    var n = sky ? 7 : tier.branches;
    for (var i = 0; i < n; i++) {
      var idx = 3 + Math.floor(r() * (main.length - 9));
      var p = main[idx];
      var dir = base + (r() < 0.5 ? -1 : 1) * (0.35 + r() * 1.0);
      var len = (sky ? 0.14 + r() * 0.28 : 0.09 + r() * 0.26) * L;
      var ex = p[0] + Math.cos(dir) * len;
      var ey = p[1] + Math.sin(dir) * len;
      var bp = midpointBolt(r, p[0], p[1], ex, ey, 3, sky ? 0.20 : 0.24);
      branches.push(bp);
      if (r() < 0.55) {
        var k = 1 + Math.floor(r() * (bp.length - 3));
        var q = bp[k];
        var d2 = dir + (r() < 0.5 ? -1 : 1) * (0.5 + r() * 0.7);
        var l2 = len * (0.3 + r() * 0.35);
        branches.push(midpointBolt(r, q[0], q[1],
          q[0] + Math.cos(d2) * l2, q[1] + Math.sin(d2) * l2, 2, 0.26));
      }
    }
    return { main: main, branches: branches };
  }


  /* 一条有粗细变化的"墨电"多边形：越靠近两端越细，边缘带一点随机抖动。 */
  function taperFill(c, pts, widthAt) {
    var n = pts.length;
    if (n < 2) return;
    var left = new Array(n), right = new Array(n);
    for (var i = 0; i < n; i++) {
      var p0 = pts[Math.max(0, i - 1)], p1 = pts[Math.min(n - 1, i + 1)];
      var dx = p1[0] - p0[0], dy = p1[1] - p0[1];
      var len = Math.sqrt(dx * dx + dy * dy) || 1;
      var nx = -dy / len, ny = dx / len;
      var w = Math.max(0.06, widthAt(i / (n - 1))) * 0.5;
      left[i] = [pts[i][0] + nx * w, pts[i][1] + ny * w];
      right[i] = [pts[i][0] - nx * w, pts[i][1] - ny * w];
    }
    c.beginPath();
    c.moveTo(left[0][0], left[0][1]);
    for (var l = 1; l < n; l++) c.lineTo(left[l][0], left[l][1]);
    for (var r = n - 1; r >= 0; r--) c.lineTo(right[r][0], right[r][1]);
    c.closePath();
    c.fill();
    // 圆头：多边形两端是平口，补两个小圆才像烧出来的电痕
    var w0 = Math.max(0.06, widthAt(0)) * 0.5;
    var w1 = Math.max(0.06, widthAt(1)) * 0.5;
    c.beginPath(); c.arc(pts[0][0], pts[0][1], w0, 0, TAU); c.fill();
    c.beginPath(); c.arc(pts[n - 1][0], pts[n - 1][1], w1, 0, TAU); c.fill();
  }

  function offsetPath(pts, off, seed) {
    var out = new Array(pts.length);
    for (var i = 0; i < pts.length; i++) {
      var p0 = pts[Math.max(0, i - 1)], p1 = pts[Math.min(pts.length - 1, i + 1)];
      var dx = p1[0] - p0[0], dy = p1[1] - p0[1];
      var len = Math.sqrt(dx * dx + dy * dy) || 1;
      var k = off * (0.55 + 0.45 * Math.sin(i * 0.55 + seed));
      out[i] = [pts[i][0] - dy / len * k, pts[i][1] + dx / len * k];
    }
    return out;
  }

  function boltW(peak, seed, u) {
    var s = Math.sin(Math.PI * clamp(u, 0.015, 0.985));
    var w = peak * (0.22 + 0.78 * Math.pow(s, 0.62));
    // 一段一段地改粗细：像毛笔提按，而不是一根等宽的管子
    var block = 0.58 + 0.92 * hash(seed + Math.floor(u * 11 + 0.5) * 4.7);
    return w * block;
  }

  /* 宣纸上的电不能只靠"加亮"：加白到最后就是一张白纸。
   * 顺序是 色雾 → 焦墨躯体 → 饱和电芯 → 极细的白热芯；
   * 暗边负责在浅色纸上立住轮廓，颜色负责"电"，白芯只点一下高光。 */
  function drawBolt(c, tree, coreW, pal, alpha, sky) {
    if (alpha <= 0.002) return;
    var main = tree.main, branches = tree.branches;
    var edge = pal.edge || "#2a2440", color = pal.color || "#4aa8ff", hot = pal.hot || "#ffffff";
    var i;

    c.save();
    c.globalCompositeOperation = "source-over";

    // 1) 颜色渗开
    c.fillStyle = rgba(color, 0.13 * alpha);
    taperFill(c, main, function (u) { return boltW(coreW * (sky ? 6.2 : 7.0), 3, u); });
    c.fillStyle = rgba(color, 0.07 * alpha);
    for (i = 0; i < branches.length; i++) {
      taperFill(c, branches[i], (function (k) {
        return function (u) { return boltW(coreW * 2.4, 7 + k, u); };
      })(i));
    }

    // 2) 焦墨的软晕（比躯体宽、比颜色淡，让亮电在宣纸上站得住）
    c.globalCompositeOperation = "multiply";
    c.fillStyle = "rgba(26,20,34," + (0.18 * alpha) + ")";
    taperFill(c, main, function (u) { return boltW(coreW * (sky ? 4.4 : 3.8), 9, u); });
    c.globalCompositeOperation = "source-over";

    // 3) 焦墨躯体（真正让雷在宣纸上"长出来"的一层）
    c.fillStyle = rgba(edge, 0.98 * alpha);
    taperFill(c, main, function (u) { return boltW(coreW * (sky ? 2.30 : 2.60), 11, u); });
    for (i = 0; i < branches.length; i++) {
      taperFill(c, branches[i], (function (k) {
        return function (u) { return boltW(coreW * 0.85, 13 + k, u); };
      })(i));
    }

    // 4) 饱和电芯
    c.fillStyle = rgba(color, 0.92 * alpha);
    taperFill(c, main, function (u) { return boltW(coreW * (sky ? 0.55 : 0.55), 17, u); });
    for (i = 0; i < branches.length; i++) {
      taperFill(c, branches[i], (function (k) {
        return function (u) { return boltW(coreW * 0.20, 19 + k, u); };
      })(i));
    }

    // 5) 白热芯：只点一道，别铺满
    c.fillStyle = rgba(hot, 0.96 * alpha);
    taperFill(c, main, function (u) { return boltW(coreW * (sky ? 0.32 : 0.32), 23, u); });
    for (i = 0; i < branches.length; i++) {
      taperFill(c, branches[i], (function (k) {
        return function (u) { return boltW(coreW * 0.08, 29 + k, u); };
      })(i));
    }

    // 6) 外侧的细电丝：两根错开的弧，别让主雷像一根光缆
    if (!sky) {
      for (var f = 0; f < 2; f++) {
        var off = (f === 0 ? -1 : 1) * coreW * (1.6 + 0.5 * Math.sin(f * 2.1 + 0.6));
        var fil = offsetPath(main, off, f * 2.7);
        c.fillStyle = rgba(color, 0.30 * alpha);
        taperFill(c, fil, function (u) { return boltW(coreW * 0.26, 31 + f, u); });
        c.fillStyle = rgba(hot, 0.55 * alpha);
        taperFill(c, fil, function (u) { return boltW(coreW * 0.10, 37 + f, u); });
      }
    }

    // 7) 落点的高光（用饱和色，不用加白）
    var tip = main[main.length - 1];
    c.fillStyle = rgba(color, 0.22 * alpha);
    c.beginPath(); c.arc(tip[0], tip[1], coreW * (sky ? 2.8 : 2.4), 0, TAU); c.fill();
    c.fillStyle = rgba(hot, 0.8 * alpha);
    c.beginPath(); c.arc(tip[0], tip[1], Math.max(0.7, coreW * 0.5), 0, TAU); c.fill();

    c.restore();
  }

  function glowDot(c, x, y, r, color, a) {
    if (r <= 0 || a <= 0) return;
    var g = c.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, "rgba(255,255,255," + a + ")");
    g.addColorStop(0.35, rgba(color, a * 0.5));
    g.addColorStop(1, rgba(color, 0));
    c.fillStyle = g;
    c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill();
  }

  /* ================================================================
   * 牌面电流 / 墨点 / 火星 / 雷云：一次生成，按时间取用
   * ============================================================== */
  function crawlPath(r, sx, sy, steps, step, spread) {
    var pts = [[sx, sy]], a = r() * TAU;
    for (var i = 0; i < steps; i++) {
      a += (r() - 0.5) * spread;
      step *= 0.70 + r() * 0.22;
      sx += Math.cos(a) * step;
      sy += Math.sin(a) * step;
      pts.push([sx, sy]);
    }
    return pts;
  }

  function cardArcs(r, count, tier, sky) {
    var out = [];
    var w = sky ? tier.storm.arcW : tier.arcW;
    var life = sky ? tier.storm.arcLife : tier.arcLife;
    for (var i = 0; i < count; i++) {
      var sx, sy;
      if (r() < 0.6) {
        // 命中点周围的一小团电蛇（参考实现的 0.6 分支：锚在 B，不是来源牌）。
        var ang = r() * TAU, rr = (18 + r() * 72) * U;
        sx = B.x + Math.cos(ang) * rr;
        sy = B.y + Math.sin(ang) * rr * 0.8;
      } else {
        sx = DST.cx - DST.w * 0.5 + 20 + r() * (DST.w - 40);
        sy = DST.cy - DST.h * 0.5 + 20 + r() * (DST.h - 40);
      }
      var pts = crawlPath(r, sx, sy, 6 + Math.floor(r() * 4), (18 + r() * 28) * U, 2.1);
      var fork = null;
      if (r() < 0.7) {
        var k = 2 + Math.floor(r() * (pts.length - 3));
        fork = crawlPath(r, pts[k][0], pts[k][1], 2 + Math.floor(r() * 3), 10 + r() * 18, 2.0);
      }
      out.push({
        pts: pts, fork: fork,
        delay: r() * 0.42,
        life: life * (0.55 + r() * 0.55),
        w: w * U * (0.62 + r() * 0.55),
        phase: r() * TAU
      });
    }
    return out;
  }

  function groundArcs(r, count, tier) {
    var out = [];
    for (var i = 0; i < count; i++) {
      var x = DST.cx - DST.w * 0.4 + r() * DST.w * 0.8;
      var y = DST.cy + DST.h * 0.5 - 16;
      var pts = [[x, y]];
      while (y < H + 90) {
        x += (r() - 0.5) * 110;
        y += 55 + r() * 85;
        pts.push([x, y]);
      }
      out.push({
        pts: pts, delay: 0.1 + r() * 0.4,
        life: 0.9 + r() * 0.8, w: 1.1 + r() * 1.6, phase: r() * TAU
      });
    }
    return out;
  }

  function sourceArcs(r, count) {
    var out = [];
    for (var i = 0; i < count; i++) {
      var ang = r() * TAU;
      var rr = (36 + r() * 104) * U;
      var sx = A.x + Math.cos(ang) * rr;
      var sy = A.y + Math.sin(ang) * rr * 0.8;
      var pts = [[sx, sy]];
      var n = 4 + Math.floor(r() * 3);
      for (var k = 1; k <= n; k++) {
        var u = k / n;
        var x = lerp(sx, A.x, u) + (r() - 0.5) * 34 * (1 - u);
        var y = lerp(sy, A.y, u) + (r() - 0.5) * 34 * (1 - u);
        pts.push([x, y]);
      }
      out.push({ pts: pts, delay: r() * 0.16, life: 0.42 + r() * 0.3, w: 0.9 + r() * 1.1, phase: r() * TAU });
    }
    return out;
  }

  function drops(r, count, minSpd, maxSpd, minSize, maxSize) {
    var out = [];
    for (var i = 0; i < count; i++) {
      out.push({
        ang: r() * TAU,
        spd: minSpd + r() * (maxSpd - minSpd),
        size: minSize + r() * (maxSize - minSize),
        delay: r() * 0.08,
        life: 0.45 + r() * 0.7,
        grav: 620 + r() * 620
      });
    }
    return out;
  }

  function sparks(r, count) {
    var out = [];
    for (var i = 0; i < count; i++) {
      out.push({
        ang: r() * TAU,
        spd: 320 + r() * 1150,
        size: 0.9 + r() * 2.1,
        delay: r() * 0.05,
        life: 0.16 + r() * 0.42
      });
    }
    return out;
  }

  function smoke(r, count) {
    var out = [];
    for (var i = 0; i < count; i++) {
      out.push({
        dx: (r() - 0.5) * 200,
        dy: (r() - 0.5) * 90,
        rise: 28 + r() * 95,
        rad: 50 + r() * 120,
        grow: 1.2 + r() * 1.3,
        delay: r() * 0.18,
        life: 1.3 + r() * 1.2,
        a: 0.06 + r() * 0.10
      });
    }
    return out;
  }

  function burstShape(r, n, rmin, rmax) {
    var pts = [];
    var ph = r() * TAU, ph2 = r() * TAU;
    for (var i = 0; i < n; i++) {
      var a = i / n * TAU + (r() - 0.5) * 0.05;
      // 大波浪 + 小波浪：一片被烧开的墨，不是一颗多角星
      var wave = 0.55 + 0.28 * Math.sin(a * 3 + ph) + 0.17 * Math.sin(a * 7 + ph2);
      var rr = rmin + (rmax - rmin) * clamp(wave, 0, 1) + (r() - 0.5) * 7;
      pts.push([Math.cos(a) * rr, Math.sin(a) * rr]);
    }
    return pts;
  }

  function inkWash(c, x, y, rad, alpha, seed) {
    var rr = rng(seed);
    c.save();
    c.globalCompositeOperation = "multiply";
    for (var i = 0; i < 5; i++) {
      var dx = (rr() - 0.5) * rad * 0.7;
      var dy = (rr() - 0.5) * rad * 0.55;
      var r2 = rad * (0.5 + rr() * 0.55);
      var g = c.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, r2);
      g.addColorStop(0, "rgba(27,21,28," + alpha + ")");
      g.addColorStop(0.55, "rgba(27,21,28," + (alpha * 0.45) + ")");
      g.addColorStop(1, "rgba(27,21,28,0)");
      c.fillStyle = g;
      c.beginPath(); c.arc(x + dx, y + dy, r2, 0, TAU); c.fill();
    }
    c.restore();
  }


  function scorchShape(r) {
    // 三层不规则墨团叠出"烧穿"的边，而不是一颗锐角星星
    return {
      outer: burstShape(r, 64, 34, 78),
      mid: burstShape(r, 48, 22, 54),
      core: burstShape(r, 36, 10, 28),
      cracks: (function () {
        var out = [];
        for (var i = 0; i < 7; i++) {
          var a = r() * TAU;
          var pts = [[0, 0]];
          var x = 0, y = 0, len = 26 + r() * 54;
          var n = 3;
          for (var k = 0; k < n; k++) {
            a += (r() - 0.5) * 0.9;
            x += Math.cos(a) * len / n;
            y += Math.sin(a) * len / n;
            pts.push([x, y]);
          }
          out.push(pts);
        }
        return out;
      })(),
      embers: (function () {
        var out = [];
        for (var e = 0; e < 10; e++) {
          var ea = r() * TAU, er = 12 + r() * 56;
          out.push([Math.cos(ea) * er, Math.sin(ea) * er, 0.9 + r() * 2.2, r() * TAU]);
        }
        return out;
      })()
    };
  }

  function sprayShape(r, n, minL, maxL) {
    var out = [];
    for (var i = 0; i < n; i++) {
      var a = r() * TAU;
      var len = minL + r() * (maxL - minL);
      var pts = [[0, 0]];
      var x = 0, y = 0;
      var steps = 3 + Math.floor(r() * 2);
      for (var k = 0; k < steps; k++) {
        a += (r() - 0.5) * 0.45;
        x += Math.cos(a) * len / steps;
        y += Math.sin(a) * len / steps;
        pts.push([x, y]);
      }
      out.push({ pts: pts, w: 0.8 + r() * 3.2 });
    }
    return out;
  }

  function impactRayShape(r) {
    var out = [];
    for (var i = 0; i < 5; i++) {
      var a = i / 5 * TAU + (r() - 0.5) * 0.55;
      var len = 28 + r() * 62;
      out.push(midpointBolt(r, 0, 0, Math.cos(a) * len, Math.sin(a) * len * 0.7, 2, 0.3));
    }
    return out;
  }

  function stormShape(r) {
    var clouds = [];
    for (var i = 0; i < 10; i++) {
      clouds.push({
        x: B.x - 420 + r() * 840,
        y: -120 + r() * 300,
        rad: 120 + r() * 260,
        a: 0.40 + r() * 0.34,
        ph: r() * TAU,
        drift: 5 + r() * 12
      });
    }
    var puffs = [];
    for (var p = 0; p < 14; p++) {
      puffs.push({
        x: B.x - 360 + r() * 720,
        y: -20 + r() * 300,
        rad: 55 + r() * 150,
        a: 0.46 + r() * 0.32,
        ph: r() * TAU
      });
    }
    var edge = [];
    for (var e = 0; e < 18; e++) {
      edge.push({
        x: -80 + r() * (W + 160),
        y: 170 + r() * 190,
        rad: 38 + r() * 105,
        a: 0.28 + r() * 0.30
      });
    }
    var bleed = [];
    for (var b = 0; b < 22; b++) {
      bleed.push([-60 + r() * (W + 120), 250 + r() * 160, 2 + r() * 9, 0.05 + r() * 0.12]);
    }
    var strikes = [];
    var times = T.PRE_STRIKES || [];
    for (var st = 0; st < times.length; st++) {
      var sx = B.x - 240 + r() * 480;
      var sy = 40 + r() * 150;
      var ex = B.x - 160 + r() * 320;
      var ey = 300 + r() * 140;
      strikes.push({
        t: times[st],
        tree: boltTree(9100 + st * 37, sx, sy, ex, ey, TIERS[4], true)
      });
    }
    return { clouds: clouds, puffs: puffs, edge: edge, bleed: bleed, strikes: strikes };
  }

  function explosionShape(r) {
    return {
      rays: (function () {
        var out = [];
        for (var i = 0; i < 5; i++) {
          var a = r() * TAU;
          out.push(midpointBolt(r, 0, 0,
            Math.cos(a) * (55 + r() * 85), Math.sin(a) * (45 + r() * 70), 2, 0.36));
        }
        return out;
      })(),
      spray: sprayShape(r, 14, 40, 130),
      debris: drops(r, 42, 120, 900, 1.8, 11),
      embers: sparks(r, 52),
      lobes: (function () {
        var out = [];
        for (var i = 0; i < 4; i++) {
          var a = r() * TAU;
          out.push({
            x: Math.cos(a) * (20 + r() * 70),
            y: Math.sin(a) * (16 + r() * 50),
            r: 70 + r() * 100,
            a: 0.12 + r() * 0.10
          });
        }
        return out;
      })()
    };
  }

  /* ================================================================
   * 各层绘制
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

  function cardKick(t, t0, amp, tilt) {
    var age = t - t0;
    if (age < 0) return { dx: 0, dy: 0, rot: 0 };
    var k = Math.exp(-age * 6.5);
    return {
      dx: Math.sin(age * 74 + 0.4) * amp * 0.20 * k,
      dy: Math.cos(age * 61 + 1.1) * amp * 0.14 * k,
      rot: Math.sin(age * 47 + 0.7) * tilt * k
    };
  }

  function shakeAt(t, tier) {
    var s = { x: 0, y: 0 };
    function add(t0, amp) {
      var age = t - t0;
      if (age < 0) return;
      var k = Math.exp(-age * 7.5) * amp;
      s.x += (Math.sin(age * 83 + 1.2) + 0.55 * Math.sin(age * 139 + 0.4)) * k * 0.5;
      s.y += (Math.cos(age * 71 + 0.7) + 0.5 * Math.sin(age * 127 + 2.1)) * k * 0.5;
    }
    add(T.IMPACT, tier.shake);
    if (tier.storm) add(T.EXPLODE, tier.storm.shake);
    return s;
  }

  function fillPoly(c, pts, scale, ox, oy) {
    c.beginPath();
    for (var i = 0; i < pts.length; i++) {
      var x = pts[i][0] * scale + (ox || 0);
      var y = pts[i][1] * scale + (oy || 0);
      if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
    }
    c.closePath();
    c.fill();
  }

  function drawImpactInk(c, t, tier) {
    var age = t - T.IMPACT;
    if (age < 0) return;
    var fade = 1 - ss(tier.fadeStart, tier.fadeEnd, t);
    var sc = tier.scorch * fade;

    // 焦痕（裁在牌里）：三层不规则墨团，中心最深
    c.save();
    clipCard(c, DST, 2);
    c.translate(B.x, B.y);
    c.globalCompositeOperation = "multiply";

    var halo = c.createRadialGradient(0, 0, 0, 0, 0, 96);
    halo.addColorStop(0, "rgba(26,18,16," + (0.45 * sc) + ")");
    halo.addColorStop(0.55, "rgba(26,18,16," + (0.20 * sc) + ")");
    halo.addColorStop(1, "rgba(26,18,16,0)");
    c.fillStyle = halo;
    c.beginPath(); c.arc(0, 0, 96, 0, TAU); c.fill();

    c.fillStyle = "#241d24";
    c.globalAlpha = 0.46 * sc;
    fillPoly(c, tier.scorchData.outer, 1.0, 2, 1);
    c.fillStyle = "#19141c";
    c.globalAlpha = 0.40 * sc;
    fillPoly(c, tier.scorchData.mid, 0.94, -3, 2);
    c.fillStyle = "#0e0b12";
    c.globalAlpha = 0.36 * sc;
    fillPoly(c, tier.scorchData.core, 0.9, 1, -2);

    // 焦裂：细、断、短
    c.globalAlpha = 0.46 * sc;
    c.strokeStyle = "#0d0a10";
    c.lineWidth = 1.5;
    c.lineCap = "round";
    for (var k = 0; k < tier.scorchData.cracks.length; k++) {
      var pts = tier.scorchData.cracks[k];
      c.beginPath();
      c.moveTo(pts[0][0], pts[0][1]);
      for (var j = 1; j < pts.length; j++) c.lineTo(pts[j][0], pts[j][1]);
      c.stroke();
    }
    c.globalAlpha = 1;
    c.restore();

    // 余电：饱和色小点，不加白
    c.save();
    clipCard(c, DST, 2);
    for (var e = 0; e < tier.scorchData.embers.length; e++) {
      var em = tier.scorchData.embers[e];
      var blink = 0.35 + 0.65 * Math.max(0, Math.sin(t * 5 + em[3]));
      c.fillStyle = rgba(tier.storm ? "#b98cff" : tier.color, 0.55 * sc * blink);
      c.beginPath();
      c.arc(B.x + em[0], B.y + em[1], em[2] * 0.8, 0, TAU);
      c.fill();
    }
    c.restore();

    // 牌面外沿的湿墨：只渗出去一圈淡墨，不做大黑斑
    c.save();
    c.globalCompositeOperation = "multiply";
    var bleed = ss(0, 0.5, age) * fade;
    for (var b = 0; b < 3; b++) {
      var bx = B.x + (b - 1) * 86;
      var by = B.y + (b === 1 ? -28 : 38);
      var rr = 130 + b * 42 + Math.min(age, 1.0) * 90;
      var g = c.createRadialGradient(bx, by, 0, bx, by, rr);
      g.addColorStop(0, "rgba(28,26,23," + (0.045 * bleed) + ")");
      g.addColorStop(0.6, "rgba(28,26,23," + (0.018 * bleed) + ")");
      g.addColorStop(1, "rgba(28,26,23,0)");
      c.fillStyle = g;
      c.beginPath(); c.arc(bx, by, rr, 0, TAU); c.fill();
    }
    c.restore();
  }

  function drawArc(c, pts, w, pal, alpha) {
    if (pts.length < 2 || alpha <= 0.003) return;
    var edge = pal.edge || "#2a2440", color = pal.color || "#4aa8ff", hot = pal.hot || "#ffffff";
    var seed = 3 + pts.length;
    c.fillStyle = rgba(color, 0.10 * alpha);
    taperFill(c, pts, function (u) { return boltW(w * 5.2, seed, u); });
    c.globalCompositeOperation = "multiply";
    c.fillStyle = "rgba(26,20,34," + (0.16 * alpha) + ")";
    taperFill(c, pts, function (u) { return boltW(w * 3.0, seed + 3, u); });
    c.globalCompositeOperation = "source-over";
    c.fillStyle = rgba(edge, 0.70 * alpha);
    taperFill(c, pts, function (u) { return boltW(w * 1.70, seed + 5, u); });
    c.fillStyle = rgba(color, 0.96 * alpha);
    taperFill(c, pts, function (u) { return boltW(w * 1.02, seed + 9, u); });
    c.fillStyle = rgba(hot, 0.92 * alpha);
    taperFill(c, pts, function (u) { return boltW(w * 0.52, seed + 13, u); });
    var tip = pts[pts.length - 1];
    c.fillStyle = rgba(color, 0.30 * alpha);
    c.beginPath(); c.arc(tip[0], tip[1], w * 2.4, 0, TAU); c.fill();
    c.fillStyle = rgba(hot, 0.95 * alpha);
    c.beginPath(); c.arc(tip[0], tip[1], Math.max(0.7, w * 0.48), 0, TAU); c.fill();
  }

  function drawCurrents(c, t, tier, list, startT, pal, clipRect, alphaScale) {
    c.save();
    c.globalCompositeOperation = "source-over";
    if (clipRect) clipCard(c, clipRect, 3);
    for (var i = 0; i < list.length; i++) {
      var a = list[i];
      var age = t - startT - a.delay;
      if (age < 0 || age > a.life) continue;
      var grow = ss(0, 0.06, age);
      var n = Math.max(2, Math.floor(a.pts.length * grow));
      var alpha = ss(0, 0.03, age) * (1 - ss(a.life * 0.5, a.life, age)) * (alphaScale === undefined ? 1 : alphaScale);
      var flick = 0.62 + 0.38 * Math.abs(Math.sin(age * 77 + a.phase));
      drawArc(c, a.pts.slice(0, n), a.w, pal, alpha * flick);

      if (a.fork) {
        var fn = 2 + Math.floor(a.fork.length * grow) - 1;
        var fp = a.fork.slice(0, Math.max(2, fn));
        drawArc(c, fp, a.w * 0.45, pal, alpha * flick * 0.8);
      }
    }
    c.restore();
  }

  function drawHitWash(c, t, tier) {
    var age = t - T.IMPACT;
    if (age < 0 || age > 0.5) return;
    var a = Math.exp(-age * 12) * 0.10;
    c.save();
    clipCard(c, DST, 1);
    c.fillStyle = rgba(tier.storm ? C.violet : C.red, a);
    c.fillRect(DST.cx - DST.w / 2, DST.cy - DST.h / 2, DST.w, DST.h);
    c.restore();

    // 牌边电光
    c.save();
    var k = Math.exp(-age * 8);
    c.globalCompositeOperation = "source-over";
    c.strokeStyle = rgba(tier.storm ? "#7d4fce" : tier.color, 0.22 * k);
    c.lineWidth = 5;
    pathRound(c, DST.cx - DST.w / 2, DST.cy - DST.h / 2, DST.w, DST.h, 14);
    c.stroke();
    c.strokeStyle = rgba(tier.storm ? "#d9c2ff" : tier.hot, 0.30 * k);
    c.lineWidth = 1.4;
    c.stroke();
    c.restore();
  }

  function drawGroundArcs(c, t, tier, list, startT, pal) {
    c.save();
    c.globalCompositeOperation = "source-over";
    for (var i = 0; i < list.length; i++) {
      var a = list[i];
      var age = t - startT - a.delay;
      if (age < 0 || age > a.life) continue;
      var grow = ss(0, 0.08, age);
      var n = Math.max(2, Math.floor(a.pts.length * grow));
      var alpha = ss(0, 0.03, age) * (1 - ss(a.life * 0.45, a.life, age));
      var flick = 0.55 + 0.45 * Math.abs(Math.sin(age * 61 + a.phase));
      drawArc(c, a.pts.slice(0, n), a.w * 1.05, pal, alpha * flick * 0.9);
    }
    c.restore();
  }

  function drawSmoke(c, t, tier) {
    var age = t - T.IMPACT;
    if (age < 0) return;
    c.save();
    c.globalCompositeOperation = "multiply";
    for (var i = 0; i < tier.smokeData.length; i++) {
      var s = tier.smokeData[i];
      var a = age - s.delay;
      if (a < 0 || a > s.life) continue;
      var k = ss(0, 0.35, a) * (1 - ss(s.life * 0.55, s.life, a));
      var x = B.x + s.dx + Math.sin(a * 1.3 + i) * 24;
      var y = B.y + s.dy - a * s.rise;
      var rr = s.rad * (0.5 + a * s.grow * 0.35);
      var g = c.createRadialGradient(x, y, 0, x, y, rr);
      g.addColorStop(0, "rgba(28,26,23," + (s.a * k) + ")");
      g.addColorStop(0.55, "rgba(28,26,23," + (s.a * k * 0.4) + ")");
      g.addColorStop(1, "rgba(28,26,23,0)");
      c.fillStyle = g;
      c.beginPath(); c.arc(x, y, rr, 0, TAU); c.fill();
    }
    c.restore();
  }

  function drawInkDrops(c, t, startT, list, color) {
    var age = t - startT;
    if (age < 0) return;
    c.save();
    c.globalCompositeOperation = "source-over";
    for (var i = 0; i < list.length; i++) {
      var d = list[i];
      var a = age - d.delay;
      if (a < 0 || a > d.life) continue;
      var k = 1 - a / d.life;
      var x = B.x + Math.cos(d.ang) * d.spd * a;
      var y = B.y + Math.sin(d.ang) * d.spd * a + 0.5 * d.grav * a * a;
      var px = B.x + Math.cos(d.ang) * d.spd * Math.max(0, a - 0.022);
      var py = B.y + Math.sin(d.ang) * d.spd * Math.max(0, a - 0.022) + 0.5 * d.grav * Math.max(0, a - 0.022) * Math.max(0, a - 0.022);

      c.strokeStyle = "rgba(30,24,32," + (0.55 * k) + ")";
      c.lineWidth = Math.max(1, d.size * 0.55);
      c.lineCap = "round";
      c.beginPath();
      c.moveTo(px, py);
      c.lineTo(x, y);
      c.stroke();

      c.fillStyle = "rgba(30,24,32," + (0.80 * k) + ")";
      c.beginPath();
      c.arc(x, y, Math.max(1, d.size * 0.78), 0, TAU);
      c.fill();
    }
    c.restore();
  }

  function drawSparks(c, t, startT, list, color) {
    var age = t - startT;
    if (age < 0) return;
    c.save();
    c.globalCompositeOperation = "lighter";
    for (var i = 0; i < list.length; i++) {
      var s = list[i];
      var a = age - s.delay;
      if (a < 0 || a > s.life) continue;
      var k = 1 - a / s.life;
      var x = B.x + Math.cos(s.ang) * s.spd * a;
      var y = B.y + Math.sin(s.ang) * s.spd * a;
      var px = x - Math.cos(s.ang) * s.spd * 0.022;
      var py = y - Math.sin(s.ang) * s.spd * 0.022;
      c.strokeStyle = "rgba(255,255,255," + (0.8 * k) + ")";
      c.lineWidth = s.size * 0.6;
      c.lineCap = "round";
      c.beginPath();
      c.moveTo(px, py);
      c.lineTo(x, y);
      c.stroke();
      c.fillStyle = rgba(color, 0.5 * k);
      c.beginPath();
      c.arc(x, y, s.size, 0, TAU);
      c.fill();
    }
    c.restore();
  }

  function drawShockRing(c, x, y, r, w, color, alpha, seed) {
    if (r <= 0 || alpha <= 0) return;
    c.save();
    c.lineJoin = "round";
    c.beginPath();
    for (var i = 0; i <= 56; i++) {
      var a = i / 56 * TAU;
      var rr = r * (0.90 + 0.16 * hash(seed + i * 1.7));
      var px = x + Math.cos(a) * rr;
      var py = y + Math.sin(a) * rr;
      if (i === 0) c.moveTo(px, py); else c.lineTo(px, py);
    }
    c.strokeStyle = "rgba(28,22,26," + (alpha * 0.45) + ")";
    c.lineWidth = w * 2.4;
    c.stroke();
    c.strokeStyle = rgba(color, alpha);
    c.lineWidth = w;
    c.stroke();
    c.restore();
  }


  function drawImpact(c, t, tier) {
    var age = t - T.IMPACT;
    if (age < 0) return;
    var isMax = !!tier.storm;
    var power = isMax ? 0.65 : 1;

    // 命中点：饱和色晕 + 一小团白芯（白只压在焦痕上，不在宣纸上单独加白）
    if (age < 0.42) {
      var a = Math.exp(-age * 12) * 0.62 * power;
      var rr = 34 + eOut(clamp(age / 0.16, 0, 1)) * (isMax ? 86 : 120);
      var g = c.createRadialGradient(B.x, B.y, 0, B.x, B.y, rr);
      g.addColorStop(0, "rgba(255,255,255," + a + ")");
      g.addColorStop(0.16, rgba(tier.color, a * 0.95));
      g.addColorStop(0.55, rgba(tier.color, a * 0.32));
      g.addColorStop(1, rgba(tier.color, 0));
      c.fillStyle = g;
      c.beginPath(); c.arc(B.x, B.y, rr, 0, TAU); c.fill();
    }

    // 电气放射：短、细、快速收掉
    if (age < 0.22 && tier.impactRays && tier.dmg >= 3) {
      var k = eOut(clamp(age / 0.07, 0, 1));
      var ra = (1 - age / 0.22) * 0.55 * power;
      c.save();
      c.translate(B.x, B.y);
      c.scale(0.55 + k * 0.9, 0.55 + k * 0.9);
      for (var i = 0; i < tier.impactRays.length; i++) {
        var pts = tier.impactRays[i];
        c.fillStyle = rgba(tier.edge, 0.55 * ra);
        taperFill(c, pts, function (u) { return boltW(5.5, 17, u); });
        c.fillStyle = rgba(tier.color, 0.85 * ra);
        taperFill(c, pts, function (u) { return boltW(2.0, 19, u); });
        c.fillStyle = rgba(tier.hot, 0.75 * ra);
        taperFill(c, pts, function (u) { return boltW(0.7, 23, u); });
      }
      c.restore();
    }

    // 命中点的湿墨：让电光有深底，才像"落在宣纸上"
    if (age < 1.6) {
      var wk = ss(0, 0.12, age) * (1 - ss(0.7, 1.7, age));
      inkWash(c, B.x, B.y, 86 + tier.dmg * 16, (0.14 + tier.dmg * 0.025) * wk, 540 + tier.dmg);
    }

    // 墨点飞溅（细长的墨丝，不是一颗黑星星）
    if (age < 0.5 && tier.impactSpray) {
      var sa = (1 - age / 0.5) * 0.42 * power;
      var grow = eOut(clamp(age / 0.16, 0, 1));
      c.save();
      c.translate(B.x, B.y);
      c.scale(0.5 + grow, 0.5 + grow);
      c.globalCompositeOperation = "multiply";
      for (var m = 0; m < tier.impactSpray.length; m++) {
        var sp = tier.impactSpray[m];
        c.fillStyle = "rgba(24,19,28," + sa + ")";
        taperFill(c, sp.pts, function (u) { return boltW(sp.w, 31, u); });
      }
      c.restore();
    }

  }

  function blob(c, x, y, r, a, k) {
    var g = c.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, "rgba(13,8,20," + (a * k) + ")");
    g.addColorStop(0.58, "rgba(15,9,24," + (a * k * 0.92) + ")");
    g.addColorStop(1, "rgba(18,11,30,0)");
    c.fillStyle = g;
    c.beginPath(); c.arc(x, y, r, 0, TAU); c.fill();
  }

  function drawSkyStorm(c, t, tier) {
    var k = tier.storm
      ? ss(T.STORM_IN, T.STORM_FULL, t) * (1 - ss(T.STORM_OUT0, T.STORM_OUT1, t))
      : 0;
    if (k <= 0.002) return;

    c.save();
    c.globalCompositeOperation = "multiply";

    // 顶上压一层夜墨
    var top = c.createLinearGradient(0, -40, 0, 300);
    top.addColorStop(0, "rgba(12,8,20," + (0.62 * k) + ")");
    top.addColorStop(0.55, "rgba(16,10,26," + (0.30 * k) + ")");
    top.addColorStop(1, "rgba(16,10,26,0)");
    c.fillStyle = top;
    c.fillRect(-80, -80, W + 160, 380);

    // 云团：大块压顶 + 中块鼓包 + 边缘碎云
    for (var i = 0; i < tier.stormData.clouds.length; i++) {
      var cl = tier.stormData.clouds[i];
      var x = cl.x + Math.sin(t * 0.7 + cl.ph) * cl.drift;
      var y = cl.y + Math.cos(t * 0.5 + cl.ph) * cl.drift * 0.5;
      blob(c, x, y, cl.rad * (1 + 0.05 * Math.sin(t * 0.9 + cl.ph)), cl.a, k);
    }
    for (var p = 0; p < tier.stormData.puffs.length; p++) {
      var pf = tier.stormData.puffs[p];
      blob(c, pf.x + Math.sin(t * 0.55 + pf.ph) * 10,
        pf.y + Math.cos(t * 0.4 + pf.ph) * 7, pf.rad, pf.a, k);
    }
    for (var e = 0; e < tier.stormData.edge.length; e++) {
      var ed = tier.stormData.edge[e];
      blob(c, ed.x, ed.y, ed.rad, ed.a, k);
    }
    c.restore();

    // 云脚墨点
    c.save();
    c.globalCompositeOperation = "multiply";
    for (var b = 0; b < tier.stormData.bleed.length; b++) {
      var bp = tier.stormData.bleed[b];
      c.fillStyle = "rgba(18,12,26," + (bp[3] * k) + ")";
      c.beginPath(); c.arc(bp[0], bp[1], bp[2], 0, TAU); c.fill();
    }
    c.restore();

    // 云底紫光：只压在目标上方一小片，不铺满屏
    c.save();
    c.globalCompositeOperation = "lighter";
    var pulse = 0.5 + 0.5 * Math.pow(Math.max(0, Math.sin(t * 2.7 - 0.4)), 3);
    var pg2 = c.createRadialGradient(B.x + 20, 330, 0, B.x + 20, 330, 300);
    pg2.addColorStop(0, "rgba(154,107,255," + (0.34 * k * pulse) + ")");
    pg2.addColorStop(0.45, "rgba(120,80,220," + (0.13 * k * pulse) + ")");
    pg2.addColorStop(1, "rgba(120,80,220,0)");
    c.fillStyle = pg2;
    c.beginPath(); c.arc(B.x + 20, 330, 300, 0, TAU); c.fill();
    c.restore();

    // 云内预闪
    if (tier.stormData.strikes) {
      for (var s = 0; s < tier.stormData.strikes.length; s++) {
        var st = tier.stormData.strikes[s];
        var age = t - st.t;
        if (age < -0.02 || age > 0.12) continue;
        var a = Math.exp(-Math.abs(age) * 24);
        drawBolt(c, st.tree, 2.6, { edge: "#241040", color: "#b98cff", hot: "#f0e2ff" }, a * 0.7, true);
      }
    }
  }

  function drawDim(c, t, tier) {
    if (!tier.storm) return;
    var k = ss(T.STORM_IN, T.STORM_FULL, t) * (1 - ss(T.DIM_OUT0, T.DIM_OUT1, t));
    if (k <= 0.002) return;
    c.save();
    c.globalCompositeOperation = "multiply";
    c.fillStyle = "rgba(26,18,38," + (tier.dim * k) + ")";
    c.fillRect(-80, -80, W + 160, H + 160);
    c.restore();
  }

  function drawCharge(c, t, tier) {
    var k = ss(T.CHARGE, T.LAUNCH + 0.02, t);
    if (k <= 0.001) return;
    var launch = t > T.LAUNCH;
    var flash = launch ? 1 - ss(T.LAUNCH, T.LAUNCH + 0.22, t) : 1;

    // 湿墨在张角牌面晕开（裁在牌里）
    c.save();
    clipCard(c, SRC, 2);
    c.globalCompositeOperation = "multiply";
    var bloomK = k * (1 - ss(T.LAUNCH, T.LAUNCH + 0.7, t));
    var bloom = c.createRadialGradient(A.x, A.y, 0, A.x, A.y, 170);
    bloom.addColorStop(0, "rgba(28,26,23," + (0.055 * bloomK) + ")");
    bloom.addColorStop(0.55, "rgba(28,26,23," + (0.024 * bloomK) + ")");
    bloom.addColorStop(1, "rgba(28,26,23,0)");
    c.fillStyle = bloom;
    c.beginPath(); c.arc(A.x, A.y, 170, 0, TAU); c.fill();
    c.restore();

    // 雷印：朱砂方块 + 雷字；凝聚时旋转，出雷时一闪
    var sealK = ss(T.CHARGE + 0.04, T.CHARGE + (T.LAUNCH - T.CHARGE) * 0.5, t)
      * (1 - ss(T.IMPACT, T.IMPACT + 0.30, t));
    if (sealK > 0.01) {
      var size = 44 * (0.72 + 0.28 * ss(0.24, 0.5, t)) * (1 + (1 - flash) * 0.18);
      c.save();
      c.translate(A.x, A.y);
      c.rotate(rad(-8) + (1 - k) * 0.22);
      c.globalAlpha = sealK;

      // 印外光
      c.globalCompositeOperation = "lighter";
      var gl = c.createRadialGradient(0, 0, 0, 0, 0, 120);
      gl.addColorStop(0, "rgba(255,180,150," + (0.20 * k) + ")");
      gl.addColorStop(0.5, "rgba(180,60,50," + (0.08 * k) + ")");
      gl.addColorStop(1, "rgba(180,60,50,0)");
      c.fillStyle = gl;
      c.beginPath(); c.arc(0, 0, 120, 0, TAU); c.fill();

      c.globalCompositeOperation = "source-over";
      c.fillStyle = C.red;
      pathRound(c, -size / 2, -size / 2, size, size, 3);
      c.fill();
      c.strokeStyle = "rgba(247,243,234,0.55)";
      c.lineWidth = 1;
      pathRound(c, -size / 2 + 3, -size / 2 + 3, size - 6, size - 6, 2);
      c.stroke();
      c.fillStyle = "#f7f3ea";
      c.font = "700 " + (size * 0.62) + "px " + SERIF;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillText("雷", 0, 1);
      c.restore();

      // 印外旋转的墨环
      c.save();
      c.translate(A.x, A.y);
      c.rotate(t * 1.2);
      c.strokeStyle = "rgba(28,26,23," + (0.16 * k * flash) + ")";
      c.lineWidth = 2;
      for (var i = 0; i < 3; i++) {
        c.beginPath();
        c.arc(0, 0, 48 + i * 12, i * 2.1, i * 2.1 + 1.2);
        c.stroke();
      }
      c.restore();
    }

    // 从牌面各处汇向雷印的细电（裁在来源牌里，命中后 0.4s 断掉）
    drawCurrents(c, t, tier, tier.chargeData, 0.12, tier, SRC,
      1 - ss(T.IMPACT, T.IMPACT + 0.4, t));

    // 出雷瞬间：印心白闪
    if (launch && t < T.LAUNCH + 0.2) {
      var fa = 1 - ss(T.LAUNCH, T.LAUNCH + 0.16, t);
      c.save();
      c.globalCompositeOperation = "lighter";
      glowDot(c, A.x, A.y, 95 * fa, "#ffffff", 0.75 * fa);
      c.restore();
    }
  }

  function drawRay(c, t, tier) {
    var a = ss(T.LAUNCH, T.LAUNCH + 0.03, t);
    if (t > T.IMPACT) a *= 1 - ss(T.IMPACT, T.IMPACT + 0.17, t);
    if (a <= 0.002) return;
    var tick = Math.floor(t * 20);
    // 无来源 ⇒ 「天降」：起笔锚在画面上方，分叉与抖动都按天空那套（素材页 4 档的画法）。
    if (!tier.rayCache || tier.rayCache.tick !== tick) {
      tier.rayCache = {
        tick: tick,
        tree: boltTree(4800 + tier.dmg * 131 + tick * 17, A.x, A.y, B.x, B.y, tier, SKY)
      };
    }
    drawBolt(c, tier.rayCache.tree, tier.core * U, tier, a, SKY);
  }

  function drawSkyBolt(c, t, tier) {
    if (!tier.storm) return;
    var a = ss(T.SKY_AT, T.SKY_AT + 0.04, t) * (1 - ss(T.EXPLODE + 0.06, T.EXPLODE + 0.38, t));
    if (a <= 0.002) return;
    var tick = Math.floor(t * 22);
    if (!tier.skyCache || tier.skyCache.tick !== tick) {
      tier.skyCache = {
        tick: tick,
        tree: boltTree(6600 + tick * 29, B.x + 24, -50, B.x, B.y, TIERS[4], true)
      };
    }
    // 粗壮的紫雷：主柱 + 两条并行支流
    drawBolt(c, tier.skyCache.tree, tier.storm.core * U, tier.storm, a, true);
  }

  function drawTargetSeal(c, t, tier) {
    if (!tier.storm) return;
    var k = ss(T.STORM_IN - 0.02, T.STORM_IN + 0.18, t)
      * (1 - ss(T.SKY_AT - 0.08, T.SKY_AT + 0.02, t));
    if (k <= 0.001) return;
    var r = 38 + Math.sin(t * 6) * 5;
    c.save();
    c.translate(B.x, B.y);
    c.rotate(t * 0.8);
    c.globalCompositeOperation = "lighter";
    c.strokeStyle = "rgba(190,150,255," + (0.55 * k) + ")";
    c.lineWidth = 2;
    c.setLineDash([14, 12]);
    c.beginPath(); c.arc(0, 0, r, 0, TAU); c.stroke();
    c.setLineDash([]);
    c.strokeStyle = "rgba(255,255,255," + (0.35 * k) + ")";
    c.lineWidth = 1;
    c.beginPath(); c.arc(0, 0, r - 12, 0, TAU); c.stroke();
    for (var i = 0; i < 8; i++) {
      var a = i / 8 * TAU;
      c.strokeStyle = "rgba(190,150,255," + (0.4 * k) + ")";
      c.beginPath();
      c.moveTo(Math.cos(a) * (r + 4), Math.sin(a) * (r + 4));
      c.lineTo(Math.cos(a) * (r + 16), Math.sin(a) * (r + 16));
      c.stroke();
    }
    c.restore();
  }

  function drawExplosion(c, t, tier) {
    if (!tier.storm) return;
    var age = t - T.EXPLODE;
    if (age < 0 || age > 1.5) return;
    var ex = tier.explodeData;
    var pal = tier.storm;

    // 0) 大团湿墨：给紫电一个"落在纸上"的深底
    if (age < 1.4) {
      var wk = ss(0, 0.08, age) * (1 - ss(0.65, 1.4, age));
      inkWash(c, B.x, B.y, 160 + tier.storm.core * 1.4, 0.20 * wk, 888);
    }

    // 1) 墨云：让白芯有一个深底
    if (age < 1.1) {
      c.save();
      c.globalCompositeOperation = "multiply";
      for (var i = 0; i < ex.lobes.length; i++) {
        var lo = ex.lobes[i];
        var a = lo.a * ss(0, 0.10, age) * (1 - ss(0.55, 1.1, age));
        var rr = lo.r * (0.55 + age * 0.5);
        var x = B.x + lo.x * (0.6 + age * 0.3);
        var y = B.y + lo.y * (0.6 + age * 0.3);
        var g = c.createRadialGradient(x, y, 0, x, y, rr);
        g.addColorStop(0, "rgba(24,17,32," + a + ")");
        g.addColorStop(0.5, "rgba(24,17,32," + (a * 0.5) + ")");
        g.addColorStop(1, "rgba(24,17,32,0)");
        c.fillStyle = g;
        c.beginPath(); c.arc(x, y, rr, 0, TAU); c.fill();
      }
      c.restore();
    }

    // 2) 墨丝飞溅
    if (age < 0.75) {
      var sa = (1 - age / 0.75) * 0.48;
      var grow = eOut(clamp(age / 0.2, 0, 1));
      c.save();
      c.translate(B.x, B.y);
      c.scale(0.45 + grow * 1.25, 0.45 + grow * 1.25);
      c.globalCompositeOperation = "multiply";
      for (var s = 0; s < ex.spray.length; s++) {
        var sp = ex.spray[s];
        c.fillStyle = "rgba(22,16,30," + sa + ")";
        taperFill(c, sp.pts, function (u) { return boltW(sp.w, 41, u); });
      }
      c.restore();
    }

    // 3) 紫色电芒
    if (age < 0.34) {
      var ra = (1 - age / 0.34) * 0.72;
      var k = eOut(clamp(age / 0.08, 0, 1));
      c.save();
      c.translate(B.x, B.y);
      c.scale(0.35 + k * 1.0, 0.35 + k * 1.0);
      for (var r = 0; r < ex.rays.length; r++) {
        var pts = ex.rays[r];
        c.fillStyle = rgba(pal.edge, 0.7 * ra);
        taperFill(c, pts, function (u) { return boltW(9, 43, u); });
        c.fillStyle = rgba(pal.color, 0.9 * ra);
        taperFill(c, pts, function (u) { return boltW(3.6, 47, u); });
        c.fillStyle = rgba(pal.hot, 0.8 * ra);
        taperFill(c, pts, function (u) { return boltW(1.1, 53, u); });
      }
      c.restore();
    }

    // 4) 白芯
    if (age < 0.5) {
      var ca = Math.exp(-age * 9);
      var coreR = 40 + eOut(clamp(age / 0.16, 0, 1)) * 120;
      var cg = c.createRadialGradient(B.x, B.y, 0, B.x, B.y, coreR);
      cg.addColorStop(0, "rgba(255,255,255," + (0.92 * ca) + ")");
      cg.addColorStop(0.28, "rgba(224,204,255," + (0.55 * ca) + ")");
      cg.addColorStop(0.65, "rgba(154,107,255," + (0.22 * ca) + ")");
      cg.addColorStop(1, "rgba(154,107,255,0)");
      c.fillStyle = cg;
      c.beginPath(); c.arc(B.x, B.y, coreR, 0, TAU); c.fill();
    }

    // 5) 双重冲击环：打断的圆，别做成白圈
    if (age < 0.9) {
      var p1 = eOut(clamp(age / 0.5, 0, 1));
      var ringA = (1 - age / 0.9) * 0.5;
      drawShockRing(c, B.x, B.y, 30 + p1 * 220, 2.2, "#a878ff", ringA, 907);
      drawShockRing(c, B.x, B.y, 20 + p1 * 140, 1.1, "#efe2ff", ringA * 0.5, 917);
    }

    // 6) 碎墨 / 紫色火星
    drawInkDrops(c, t, T.EXPLODE, ex.debris, "#1d1728");
    drawSparks(c, t, T.EXPLODE, ex.embers, "#c7a6ff");
  }

  function drawNumber(c, t, tier) {
    var isSky = !!tier.storm;
    var start = isSky ? T.NUM2_AT : T.NUM_AT;
    var life = isSky ? T.NUM2_LIFE : T.NUM_LIFE;
    var age = t - start;
    if (age < 0 || age > life) return;

    var pop = eBack(clamp(age / 0.24, 0, 1));
    var rise = eOut(clamp(age / 1.55, 0, 1));
    var alpha = 1 - ss(life - 0.55, life, age);
    if (alpha <= 0.01) return;
    var size = tier.numSize;
    var txt = "-" + tier.amount;

    c.save();
    // 位置按目标牌的比例给（参考舞台 460×644 时正好是 cx + 6 / cy − 34 / 上浮 140）。
    var ox = tier.numOffset ? tier.numOffset.dx : 6 / CARD.w;
    var oy = tier.numOffset ? tier.numOffset.dy : 34 / CARD.h;
    var riseK = tier.numOffset ? tier.numOffset.rise : 140 / CARD.h;
    c.translate(DST.cx + DST.w * ox, DST.cy - DST.h * oy - rise * DST.h * riseK);
    c.rotate(-0.05 + (1 - rise) * 0.035);
    c.scale(0.62 + 0.38 * pop, 0.62 + 0.38 * pop);
    c.textAlign = "center";
    c.textBaseline = "middle";

    // 蓝紫色电光底
    var glowColor = isSky ? "#9a6bff" : "#8a5cff";
    var glowR = size * (0.95 + 0.12 * tier.dmg) * tier.numGlow;
    var gr = c.createRadialGradient(0, 0, 0, 0, 0, glowR);
    gr.addColorStop(0, rgba(glowColor, 0.15 * alpha));
    gr.addColorStop(0.5, rgba(glowColor, 0.05 * alpha));
    gr.addColorStop(1, rgba(glowColor, 0));
    c.fillStyle = gr;
    c.beginPath(); c.arc(0, 0, glowR, 0, TAU); c.fill();

    function numGradient() {
      var g = c.createLinearGradient(0, -size * 0.55, 0, size * 0.55);
      if (isSky) {
        g.addColorStop(0, "#c3a2ff");
        g.addColorStop(0.45, "#9a6bff");
        g.addColorStop(1, "#5a4fe8");
      } else {
        g.addColorStop(0, "#a07bff");
        g.addColorStop(0.45, "#7a5cff");
        g.addColorStop(1, "#4a4fe8");
      }
      return g;
    }

    if (tier.numEcho > 0) {
      for (var e = tier.numEcho; e >= 1; e--) {
        c.save();
        c.globalAlpha = alpha * 0.18 * (1 - e / (tier.numEcho + 1.4));
        c.font = "700 " + size + "px " + SERIF;
        c.fillStyle = numGradient();
        c.fillText(txt, e * 11, -e * 14);
        c.restore();
      }
    }

    c.font = "700 " + size + "px " + SERIF;
    c.globalAlpha = alpha;
    c.lineJoin = "round";

    // 暗影
    c.globalAlpha = alpha * 0.30;
    c.fillStyle = "#0d0b1e";
    c.fillText(txt, size * 0.035, size * 0.045);

    // 纸色外晕（压住深色墨底，但不要做成白贴纸）
    c.globalAlpha = alpha * 0.50;
    c.strokeStyle = "rgba(255,250,240,0.90)";
    c.lineWidth = size * 0.030;
    c.strokeText(txt, 0, 0);

    // 深靛描边
    c.globalAlpha = alpha;
    c.strokeStyle = isSky ? "#1b1233" : "#14122a";
    c.lineWidth = size * 0.022;
    c.strokeText(txt, 0, 0);

    // 蓝紫渐变字身
    c.globalAlpha = alpha;
    c.fillStyle = numGradient();
    c.fillText(txt, 0, 0);
    c.restore();
  }

  function flashStrike(age, peak, hold, decay) {
    if (age < 0) return 0;
    var rise = ss(0, 0.008, age);
    return peak * rise * Math.exp(-Math.max(0, age - hold) * decay);
  }

  function drawFlash(c, t, tier) {
    var a = 0;
    if (tier.dmg === 3) {
      var age = t - T.IMPACT;
      if (age >= 0) {
        a = Math.max(
          flashStrike(age, 1.0, 0.035, 11),
          flashStrike(age - 0.07, 0.95, 0.02, 16),
          flashStrike(age - 0.135, 0.55, 0.01, 18)
        );
      }
    } else if (tier.dmg === 4) {
      var a0 = t - T.IMPACT;
      if (a0 >= 0) a = Math.max(a, flashStrike(a0, 0.45, 0.015, 18));
      var a1 = t - T.SKY_AT;
      if (a1 >= 0) a = Math.max(a, flashStrike(a1, 0.82, 0.012, 26));
      var a2 = t - T.EXPLODE;
      if (a2 >= 0) {
        a = Math.max(a,
          flashStrike(a2, 1.0, 0.04, 9),
          flashStrike(a2 - 0.07, 0.95, 0.02, 13),
          flashStrike(a2 - 0.14, 0.55, 0.01, 16)
        );
      }
    }
    if (a <= 0.004) return;
    a = Math.min(1, a);
    c.save();
    c.fillStyle = "rgba(255,255,255," + a + ")";
    c.fillRect(0, 0, W, H);
    c.restore();
  }

  /* ================================================================
   * 入口（与 `SgsDamageFX.create` 同形：排播层不认第二种形状）
   * ============================================================== */
  function create(canvas, opts) {
    opts = opts || {};
    var clock = clockFor(opts);
    var id = clock.id;
    var instT = clock.timeline;
    var stage = stageFor(opts.stage);
    var bare = opts.bare === true;
    var size = viewSize(opts.view);
    var useView = bare || Boolean(opts.view);
    var instW = useView ? size.w : REF_W;
    var instH = useView ? size.h : REF_H;
    // 每个实例一份可变的档位对象：缓存（雷光形状 / 飘字偏移）挂在上面，别污染 TIERS。
    var V = copy(clock.variant, {});
    V.dmg = TIERS[id].dmg;
    // 档位数字只决定强度；飘字照真实伤害写（伤害 ≥5 时档位仍是 4，数字写 5）。
    var amount = Math.round(Number(opts.amount));
    V.amount = isFinite(amount) && amount > 0 ? amount : TIERS[id].dmg;

    /* 飘字：牌桌档按目标牌体力条下沿的硬线反推起飘点（与 `fx-core.js` 的 R3 / R4 同一套
     * 包围盒判据）；导出档沿用素材页的 cx+6 / cy−34 / 上浮 140。 */
    var numOffset = { dx: 6 / CARD.w, dy: 34 / CARD.h, rise: 140 / CARD.h };
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
      numOffset = { dx: 6 / CARD.w, dy: -belowCenter, rise: NUMBER_RISE_RATIO };
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

    /* 形状在 create 时生成一次（随机数固定种子）：先切到本实例的帧上下文，
     * 于是 `cardArcs` / `stormShape` 这些读 A / B / W / H 的生成器拿到的是本实例的舞台。 */
    setFrame(stage, instT, instW, instH, bare);

    var r = rng(9100 + id * 733);
    V.scorchData = scorchShape(r);
    V.impactRays = impactRayShape(r);
    V.impactSpray = sprayShape(r, 7, 22 * U, 72 * U);
    V.smokeData = smoke(r, 7);
    V.chargeData = stage.sky ? [] : sourceArcs(r, 5);
    V.cardArcs = cardArcs(r, V.arcs, V, false);
    V.groundData = groundArcs(r, V.ground, V);
    V.impactDrops = drops(r, V.inkDrops, 120 * U, 760 * U, 1.4 * U, 8 * U);
    V.impactSparks = sparks(r, V.sparks);
    V.rayCache = null;
    V.skyCache = null;
    V.stormData = V.storm ? stormShape(r) : null;
    V.explodeData = V.storm ? explosionShape(r) : null;
    V.skyArcs = V.storm ? cardArcs(r, V.storm.arcs, V, true) : null;
    V.skyGround = V.storm ? groundArcs(r, V.storm.ground, V) : null;

    var dpr = Number(opts.dpr) > 0 ? Number(opts.dpr) : 1;
    canvas.width = Math.round(instW * dpr);
    canvas.height = Math.round(instH * dpr);
    var c = canvas.getContext("2d");

    /* 三张静态图（宣纸底 / 两张样例武将牌）只在导出档用；牌桌档 `bare` 一概不画。
     * 它们只读，跨实例复用（每次伤害都建一个新实例，重画 1920×1080 的纸底是主线卡顿）。 */
    var paper = bare ? null
      : texture("paper@" + dpr, function () { return paintPaper(dpr); });
    var srcImg = bare ? null
      : texture("src@" + dpr, function () { return paintHero(HEROES.src, dpr); });
    var dstImg = bare ? null
      : texture("dst@" + dpr, function () { return paintHero(HEROES.dst, dpr); });

    function draw(t) {
      t = clamp(t, 0, instT.DURATION);
      // 每条演出开画前切帧：三条并发依次 draw，互不串味（见文件顶的帧上下文说明）。
      setFrame(stage, instT, instW, instH, bare);
      var sh = shakeAt(t, V);
      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, instW, instH);

      c.save();
      c.translate(sh.x, sh.y);

      if (!bare) drawPaper(c, paper);
      drawSkyStorm(c, t, V);

      if (!bare && SRC) {
        var kick = cardKick(t, T.IMPACT, V.shake, V.tilt);
        var skyKick = V.storm
          ? cardKick(t, T.EXPLODE, V.storm.shake, V.storm.tilt)
          : { dx: 0, dy: 0, rot: 0 };
        drawCard(c, srcImg, SRC, -kick.dx * 0.28, kick.dy * 0.12, -kick.rot * 0.2);
        drawCard(c, dstImg, DST, kick.dx + skyKick.dx, kick.dy + skyKick.dy, kick.rot + skyKick.rot);
      }

      drawHitWash(c, t, V);
      drawImpactInk(c, t, V);
      drawCurrents(c, t, V, V.cardArcs, T.IMPACT, V, DST, 1);
      if (V.skyArcs) drawCurrents(c, t, V, V.skyArcs, T.EXPLODE, V.storm, DST, 1);
      drawGroundArcs(c, t, V, V.groundData, T.IMPACT, V);
      if (V.skyGround) drawGroundArcs(c, t, V, V.skyGround, T.EXPLODE, V.storm);

      drawTargetSeal(c, t, V);
      if (!SKY) drawCharge(c, t, V);   // 无来源 ⇒ 不画来源牌上的朱砂雷印
      drawDim(c, t, V);
      drawRay(c, t, V);
      drawSkyBolt(c, t, V);

      drawSmoke(c, t, V);
      drawImpact(c, t, V);
      drawExplosion(c, t, V);
      drawInkDrops(c, t, T.IMPACT, V.impactDrops, V.color);
      drawSparks(c, t, T.IMPACT, V.impactSparks, V.color);

      drawNumber(c, t, V);
      c.restore();

      // 牌桌档不画整屏白闪（那会盖住牌桌）：改由排播层挂 `.fx-flash` 着色子层。
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

  global.SgsThunderFX = {
    create: create,
    TIERS: TIERS,
    // `TIMELINE` 是素材页那条 5~6 秒链（导出档）；牌桌档读 `gameTiming()`。
    TIMELINE: EXPORT_T,
    COLORS: C,
    // 票 01：与 `SgsDamageFX` 同名的导出面（`damage-fx.js` 按属性取字段表 / 时长）。
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
