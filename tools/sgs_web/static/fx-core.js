"use strict";

/* ============================================================================
 * SGS 武将牌 · 剑光受伤演出
 * ----------------------------------------------------------------------------
 * 场景：左边一张武将牌出剑，一道剑光横过画面，劈在右边那张武将牌上，
 *       牌面留下刀痕、弹出伤害飘字；重击时屏幕转黑白，四点伤害连屏幕一起劈开。
 *
 * 纯 Canvas 2D，零依赖，零外部资源。
 * 画面完全由"秒"决定：draw(t) 同一 t 永远同一帧（随机数全部固定种子）。
 *
 * 用法：SgsDamageFX.create(canvas, { variant: 1|2|3|4 }).draw(t)
 *
 * 两种用途（票 05 起）：
 * - **素材库 / 截图**（缺省）：连宣纸底 + 两张样例武将牌一起画满 1920×1080，
 *   `?still` / `?export` / `renderAt(t)` 走这条链。
 * - **牌桌覆盖层**（`bare: true`）：只出**特效**——剑光 / 刀痕 / 墨点 / 飘字，
 *   纸底与样例牌一概不画，牌桌全透过覆盖层的透明区看得见；刀痕仍裁在中剑方那块
 *   座位矩形里（`stage`）。
 *
 * 落进牌桌时是**普通脚本**（U1-00 的清单契约）：`"use strict";` 是文件的**第一条语句**
 * （`tests/test_web_static.py:test_every_script_is_a_classic_strict_script`），故它写在
 * 参考实现那段块注释**之前**；全局名仍是 `window.SgsDamageFX`，不改成 ESM。
 * ========================================================================== */
(function (global) {

  /* ---------------------------------------------------------------- 配色 ----
   * 取自 SGS 前端 style.css 的 :root。 */
  var C = {
    paper:  '#e9e5da',   // 舞台纸（比 --bg 压深一档，好让武将牌浮起来）
    card:   '#fffdf7',   // 武将牌顶色
    cardLo: '#f8f1e2',   // 武将牌底色（hero-card-block 的渐变终点）
    line:   '#ddd8d0',   // --line
    ink:    '#1c1a17',   // --ink
    dim:    '#6f6a62',   // --dim
    red:    '#9c2b1b',   // --accent
    ok:     '#1d6b3a'    // --ok
  };

  var SERIF = '"Songti SC","STSong","SimSun","Noto Serif CJK SC","Noto Serif SC",serif';
  var SANS  = '"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Noto Sans CJK SC",sans-serif';

  /* ------------------------------------------------------------ 全局尺寸 ---- */
  var W = 1920, H = 1080;

  /* -------------------------------------------------------------- 布阵 ----
   * 两张武将牌同尺寸，左右各一张，中间留出剑光横过的走廊。 */
  var CARD_W = 460, CARD_H = 644;
  var SRC = { cx: 520,  cy: 545, w: CARD_W, h: CARD_H };
  var DST = { cx: 1400, cy: 545, w: CARD_W, h: CARD_H };

  /* 剑光航线：从出剑方右缘，斜着扎进中剑方 */
  var SWORD_A = { x: 752, y: 582 };
  var SWORD_B = { x: 1332, y: 516 };

  /* 剑气劈屏的那条线（取剑光航线的高度） */
  var SPLIT_Y = 545;

  /* 剑光航线 / 飘字的**比例**（票 05）：参考舞台上这两个点相对两张牌的位置。
   * 牌桌上量出来的武将块大小与参考牌 460×644 不同，`stageFor` 按同一比例算出新的
   * 航线与飘字位置——"从出剑牌右缘外飞进中剑牌"在任意座位矩形下都成立。 */
  var SWORD_ANCHOR = {
    ax: (SWORD_A.x - SRC.cx) / CARD_W,   // 0.504：出剑牌中心右方半张牌多一点
    ay: (SWORD_A.y - SRC.cy) / CARD_H,   // 0.057：略低于牌心
    bx: (DST.cx - SWORD_B.x) / CARD_W,   // 0.148：中剑牌中心左方
    by: (DST.cy - SWORD_B.y) / CARD_H    // 0.045：略高于牌心
  };

  /* 飘字相对中剑牌的位置（参考舞台是 (1410, 490) = cx + 10 / cy − 55，上浮 92）。 */
  var NUMBER_OFFSET = { dx: 10 / CARD_W, dy: 55 / CARD_H, rise: 92 / CARD_H };

  /* -------------------------------------------------------------- 武将 ----
   * 数值照三国杀；势力色：魏蓝 / 蜀绿 / 吴红 / 群灰。 */
  var HEROES = {
    src: {
      name: '关羽', faction: '蜀', factionColor: C.ok, hp: 4,
      skills: [['武圣', '你可以把一张红色手牌当【杀】使用'], ['义绝', '出牌阶段限一次，交给其他角色一张牌']]
    },
    dst: {
      name: '吕布', faction: '群', factionColor: C.dim, hp: 5,
      skills: [['无双', '你使用的【杀】需要两张【闪】才能抵消'], ['利驭', '当你造成伤害后，可获得其一张牌']]
    }
  };

  /* -------------------------------------------------------- 时间轴本体 ----
   * 12 秒是素材库默认时长（`export` 档）。牌桌上的 `game` 档由 `anim.ini` 的
   * `damage_fx_*` 每档各给一套（票 04），解算见 `gameTiming`。 */
  var T = {
    DURATION: 12.0,
    CHARGE:   0.18,   // 剑意开始凝聚
    LAUNCH:   0.52,   // 剑光出鞘
    IMPACT:   0.64,   // 劈中
    NUM_LIFE: 1.50    // 飘字存活
  };

  /* ------------------------------------------------------- 游戏档时长表 ----
   * `anim.ini` 的 19 个键的**内置默认**（磁盘副本在 `tools/sgs_web/static/anim.ini`；
   * 缺键 / 解析失败 / 传进来的值不是有限数 ⇒ 退回这里）。这里是 JS 侧**唯一**一份，
   * `anim.js` 不再抄一遍——它只负责把 ini 读进 `animConfig`，效果层原样转交。
   *
   * 每档四个时长各自独立：改 `damage_fx_4_total` 只动 4 点档，不会牵动 1~3 点档。 */
  var TIMING_PREFIX = 'damage_fx_';
  var DEFAULT_TIMING = {
    damage_fx_1_total: 1500, damage_fx_1_flight: 130,
    damage_fx_1_impact: 330, damage_fx_1_fade: 700,
    damage_fx_2_total: 1650, damage_fx_2_flight: 125,
    damage_fx_2_impact: 330, damage_fx_2_fade: 750,
    damage_fx_3_total: 1850, damage_fx_3_flight: 120,
    damage_fx_3_impact: 340, damage_fx_3_fade: 850,
    damage_fx_3_strobe: 160,
    damage_fx_4_total: 2100, damage_fx_4_flight: 110,
    damage_fx_4_impact: 340, damage_fx_4_fade: 950,
    damage_fx_4_strobe: 440, damage_fx_4_split: 44
  };

  /* 单档总时长上限：再长会挡住牌桌（spec §演出口径）。 */
  var MAX_TOTAL_MS = 2200;

  /* 剑意凝聚占"出鞘之前"的比例：19 个键里没有它，按 export 档的比例（0.18 / 0.52）取。 */
  var CHARGE_RATIO = 0.35;

  /* ------------------------------------------------------------ 小工具 ---- */
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ss(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function eOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function eIn(t) { t = clamp(t, 0, 1); return t * t * t; }
  function eBack(t) { t = clamp(t, 0, 1); var c = 2.4; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); }
  function rad(deg) { return deg * Math.PI / 180; }

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

  function noise1(x, seed) {
    var i = Math.floor(x), f = x - i;
    function h(n) { var s = Math.sin(n * 127.1 + seed * 311.7) * 43758.5453; return s - Math.floor(s); }
    var a = h(i), b = h(i + 1), u = f * f * (3 - 2 * f);
    return lerp(a, b, u) * 2 - 1;
  }

  function rgba(hex, a) {
    var n = parseInt(hex.slice(1), 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  function offscreen(w, h) {
    var c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return [c, c.getContext('2d')];
  }

  /* ================================================================
   * 四个档位：剑光越来越粗；3 点起屏幕转黑白；4 点剑气把屏幕劈开
   * ============================================================== */
  var VARIANTS = {
    1: {
      dmg: 1, swordW: 10, flight: 0.13, scarW: 14,
      blade: { glow: '#b9d2e8', mid: '#dceafb', core: '#ffffff' }, wisps: 0,
      inkDrops: 9, cinnabar: 0.05, paperTear: 0, shake: 10,
      numSize: 150, numEcho: 0, strobe: 0, split: 0,
      fadeStart: 4.6, fadeEnd: 7.0
    },
    2: {
      dmg: 2, swordW: 22, flight: 0.125, scarW: 19,
      blade: { glow: '#4f86c6', mid: '#8fb9e8', core: '#ffffff' }, wisps: 3,
      inkDrops: 17, cinnabar: 0.16, paperTear: 0.15, shake: 15,
      numSize: 178, numEcho: 0, strobe: 0, split: 0,
      fadeStart: 4.6, fadeEnd: 7.4
    },
    3: {
      dmg: 3, swordW: 34, flight: 0.12, scarW: 24,
      blade: { glow: '#1f4a8c', mid: '#3f74bd', core: '#eaf4ff' }, wisps: 5,
      inkDrops: 27, cinnabar: 0.34, paperTear: 0.4, shake: 21,
      numSize: 202, numEcho: 1, strobe: 0.16, split: 0,
      fadeStart: 4.8, fadeEnd: 8.2
    },
    4: {
      dmg: 4, swordW: 46, flight: 0.11, scarW: 30,
      blade: { glow: '#0a1a33', mid: '#16294f', core: '#ffffff' }, wisps: 8,
      inkDrops: 42, cinnabar: 0.55, paperTear: 0.75, shake: 30,
      numSize: 228, numEcho: 2, strobe: 0.44, split: 44,
      fadeStart: 4.8, fadeEnd: 9.2
    }
  };

  /* ================================================================
   * 两档时间轴：`export`（素材库 12s，截图 / 导片用）与 `game`（牌桌，四档各一套）
   * ============================================================== */

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

  /* `export` 档：参考实现的 12 秒链，`renderAt(t)` / `?still` / `?export` 三个模式走的都是它。 */
  function exportClock(variantId) {
    var id = clamp(Math.round(variantId || 1), 1, 4);
    return { id: id, profile: 'export', timeline: T, variant: VARIANTS[id], ms: null };
  }

  /* `game` 档：把 `anim.ini` 的 19 个键解成一档**秒**时间轴 + 档位参数。
   *
   * 钳制四条（票 04 §做法 2 + 收口补的一条）：`total ≤ 2200`、`flight ≤ impact ≤ total`、
   * `impact + fade ≤ total`（否则层都摘了刀痕还在，等于白设）。刀痕按
   * "命中 + fade 的 25% 起淡、命中 + fade 散尽"给（不能照搬 export 档的 4.6 → 9.2 秒，
   * 否则层都摘了刀痕还在）。`strobe` 只覆盖 3 / 4 点，`split` 只有 4 点有——缺键时取
   * `VARIANTS` 里那一档的原值。
   *
   * **不压缩时间**：返回的是真常量（秒），不是 `t * k`。音效是另一条时钟（票 06），
   * 两边必须读同一份 `timeline` 才不会漂。
   */
  function gameTiming(variantId, raw) {
    var id = clamp(Math.round(variantId || 1), 1, 4);
    var base = VARIANTS[id];
    function key(field) { return TIMING_PREFIX + id + '_' + field; }
    var total = Math.min(
      millis(raw, key('total'), DEFAULT_TIMING[key('total')]), MAX_TOTAL_MS
    );
    var impact = clamp(millis(raw, key('impact'), DEFAULT_TIMING[key('impact')]), 0, total);
    var flight = clamp(millis(raw, key('flight'), DEFAULT_TIMING[key('flight')]), 0, impact);
    // 刀痕必须在层被摘掉之前散尽：`impact + fade ≤ total`（票 04 §做法 2 补的一条钳制）。
    var fade = clamp(
      millis(raw, key('fade'), DEFAULT_TIMING[key('fade')]),
      0,
      Math.max(0, total - impact)
    );
    var strobe = clamp(millis(raw, key('strobe'), base.strobe * 1000), 0, total);
    var split = Math.max(0, millis(raw, key('split'), base.split));
    var timeline = {
      DURATION: total / 1000,
      LAUNCH: (impact - flight) / 1000,
      IMPACT: impact / 1000,
      // 飘字活到整段结束（命中之后那一段），淡出留给 `drawNumber` 自己的 60ms 尾。
      NUM_LIFE: Math.max(0.001, (total - impact) / 1000)
    };
    timeline.CHARGE = timeline.LAUNCH * CHARGE_RATIO;
    return {
      id: id,
      profile: 'game',
      timeline: timeline,
      ms: {
        total: total, flight: flight, impact: impact, fade: fade,
        strobe: strobe, split: split
      },
      variant: copy(base, {
        flight: flight / 1000,
        strobe: strobe / 1000,
        split: split,
        fadeStart: timeline.IMPACT + 0.25 * fade / 1000,
        fadeEnd: timeline.IMPACT + fade / 1000
      })
    };
  }

  /* 本实例用哪一档钟：`profile` 缺省 = `export`（素材库那条链一字不动）。 */
  function clockFor(opts) {
    return opts && opts.profile === 'game'
      ? gameTiming(opts.variant, opts.timing)
      : exportClock(opts && opts.variant);
  }

  /* ================================================================
   * 座位矩形 → 舞台（票 05）
   * ============================================================== */

  /* 牌桌档的**落屏下限**（CSS px；票 05 R2 实机返工）。
   *
   * 真实机器上武将块约 250×240 CSS px、覆盖层约 660 CSS px 宽 ⇒ 参考构图缩到 ~0.37 倍，
   * 1 点档 14 单位的刀痕落屏只剩 ~2 设备 px，被降采样抹成一条灰铅笔线。故构图比例 `u`
   * 不许小于"这三样一眼看得见"所需的比值（验收下限 4 / 40 CSS px，这里各留 ~20% 余量）：
   * 刀痕墨身 ≈ 0.94 × scarW、飘字字高 ≈ 0.7 × numSize、剑光主体 ≈ 2.1 × swordW。
   * 三个"参考值"取**最细的一档**（1 点），四档里它就是下限。 */
  /* 牌桌档飘字的口径（票 05 R2 / R3 / R4，全部以**尺寸**为单位，不拍脑袋写比例）：
   * - `GLYPH_RATIO`：数字的字形高度 ≈ 0.7 em（衬线体的数字高）。
   * - `NUMBER_MAX_RATIO`：字高不超过牌高的 35%（R2 在真机上占到了 45%，压住体力读数）。
   * - `NUMBER_KEEP_OUT_FALLBACK`：**量不到 DOM 时**的兜底硬线（牌高的 45%）。R4 之前这里
   *   用的是"头部带 25%"，可是真机上头部带（势力印 + 将名 + 内奸徽）本身就占 25%，体力条
   *   在**它下面**（实测下沿 34.7%~42.3% 牌高）——25% 落在头部带中间，所以 R3 仍然压住了
   *   体力点数。现在正路是覆盖层量 `.hp-bar` 的真矩形传进来（`numberKeepOut`），
   *   这个比例只在量不到时兜底。
   * - `NUMBER_GAP_RATIO`：包围盒与硬线之间必留的余量（牌高的 2%）。
   * - `NUMBER_RISE_RATIO`：上浮距离 = 牌高的 10%；`NUMBER_BELOW_MIN`：起飘点至少压在牌心
   *   下方这么多（不让数字飘进上半区）。
   * 位置由这几条**反推**（见 `create`），不用固定的 dy / rise 比例。
   * 素材库那份（`NUMBER_OFFSET`）一个数都不动。 */
  var GLYPH_RATIO = 0.7;
  var NUMBER_MAX_RATIO = 0.35;
  var NUMBER_KEEP_OUT_FALLBACK = 0.45;
  var NUMBER_GAP_RATIO = 0.02;
  var NUMBER_RISE_RATIO = 0.10;
  var NUMBER_BELOW_MIN = 0.02;

  var SCREEN_MIN = { scar: 5, number: 44, sword: 6 };
  var REF_SCAR_W = 14, REF_NUM_SIZE = 150, REF_SWORD_W = 10;
  var COMPOSITION_FLOOR = Math.max(
    SCREEN_MIN.scar / (0.94 * REF_SCAR_W),      // 0.38
    SCREEN_MIN.number / (GLYPH_RATIO * REF_NUM_SIZE),   // 0.42
    SCREEN_MIN.sword / (2.1 * REF_SWORD_W)      // 0.29
  );

  /* 一个座位矩形：缺字段 / 非法值退回参考舞台的对应值（`anim.ini` 删一行不该让页面报错，
   * 覆盖层量不到布局时同理）。w / h 必须为正——0 宽会把构图比例算成 0。 */
  function rectOr(raw, fallback) {
    var out = { cx: fallback.cx, cy: fallback.cy, w: fallback.w, h: fallback.h };
    if (!raw) return out;
    var keys = ['cx', 'cy', 'w', 'h'];
    for (var i = 0; i < keys.length; i++) {
      var value = Number(raw[keys[i]]);
      if (!isFinite(value)) continue;
      if ((keys[i] === 'w' || keys[i] === 'h') && value <= 0) continue;
      out[keys[i]] = value;
    }
    return out;
  }

  /* 把覆盖层量出来的两张武将牌矩形解成本实例的舞台。
   *
   * - `src` / `dst` 就是量出来的矩形（**舞台坐标**）：两张牌画在这两块上，刀痕
   *   （`buildScar(…, dst)`）、墨点、飘字都按它定位——不再按参考舞台的 460×644 硬摆。
   * - `u` = 它们比参考牌小多少（按 460×644 contain，取两牌最小值）：剑光粗细 / 刀痕 /
   *   飘字字号是"牌上的绝对尺寸"，必须一起缩，否则 46 单位的剑光会糊满整张牌；
   *   但**不许小于 `COMPOSITION_FLOOR`**（小牌上再缩就成了一条灰铅笔线）。
   * - `a` / `b` = 剑光航线，按 `SWORD_ANCHOR` 的同一比例从两张牌算出。
   *
   * 不传 `stage` ⇒ 参考舞台（`u = 1`、A / B 仍取原常量），素材库那条链逐字节不变。 */
  function stageFor(raw) {
    if (!raw || (!raw.src && !raw.dst)) {
      return { src: SRC, dst: DST, u: 1, a: SWORD_A, b: SWORD_B };
    }
    var src = rectOr(raw.src, SRC);
    var dst = rectOr(raw.dst, DST);
    var u = Math.min(src.w / CARD_W, src.h / CARD_H, dst.w / CARD_W, dst.h / CARD_H);
    if (!isFinite(u) || u <= 0) u = 1;
    // 小牌不许把"牌上的绝对尺寸"一起缩到看不见：抬到落屏下限（牌桌档的坐标就是 CSS px）。
    u = Math.max(u, COMPOSITION_FLOOR);
    return {
      src: src, dst: dst, u: u,
      a: { x: src.cx + src.w * SWORD_ANCHOR.ax, y: src.cy + src.h * SWORD_ANCHOR.ay },
      b: { x: dst.cx - dst.w * SWORD_ANCHOR.bx, y: dst.cy - dst.h * SWORD_ANCHOR.by }
    };
  }

  /* 飘字文案：`amount` 是服务端下发的**真实**伤害（酒 + 杀 + 加伤可能 ≥ 5，档位只决定
   * 强度、不决定数字）。缺省 / 非法值退回该档的参考值，故不传 `amount` 时与原来一致。 */
  function numberText(variant, amount) {
    var value = Math.round(Number(amount));
    if (!isFinite(value) || value <= 0) value = variant.dmg;
    return '-' + value;
  }

  /* ================================================================
   * 宣纸舞台
   * ============================================================== */
  function paintPaper(w, h, seed) {
    var pc = offscreen(w, h), c = pc[1], r = rng(seed);
    c.fillStyle = C.paper;
    c.fillRect(0, 0, w, h);

    for (var i = 0; i < (w * h) / 24; i++) {
      c.fillStyle = r() < 0.5 ? 'rgba(28,26,23,0.022)' : 'rgba(255,255,255,0.06)';
      c.fillRect(r() * w, r() * h, 1, 1);
    }
    c.strokeStyle = 'rgba(90,70,40,0.036)';
    c.lineWidth = 0.7;
    for (var j = 0; j < (w * h) / 900; j++) {
      var px = r() * w, py = r() * h, a = r() * Math.PI * 2, l = 5 + r() * 20;
      c.beginPath(); c.moveTo(px, py); c.lineTo(px + Math.cos(a) * l, py + Math.sin(a) * l); c.stroke();
    }

    var vg = c.createRadialGradient(w * 0.5, h * 0.47, h * 0.2, w * 0.5, h * 0.5, h);
    vg.addColorStop(0, 'rgba(28,26,23,0)');
    vg.addColorStop(1, 'rgba(28,26,23,0.22)');
    c.fillStyle = vg;
    c.fillRect(0, 0, w, h);
    return pc[0];
  }

  function roundRect(c, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  /* ================================================================
   * 武将牌（460×644）：势力印、将名、体力段、淡墨竖排、技能行
   * ============================================================== */
  /* 阴影一次烘进画布 —— 每帧给 460×644 的图做 shadowBlur 会吃掉几十毫秒。 */
  var SHADOW_PAD = 74;

  function paintHeroCard(hero, seed) {
    var w = CARD_W, h = CARD_H;
    var cc = offscreen(w + SHADOW_PAD * 2, h + SHADOW_PAD * 2);
    var c = cc[1], r = rng(seed);
    var pad = 24;
    c.translate(SHADOW_PAD, SHADOW_PAD);

    // 投影（烘焙）
    c.save();
    c.shadowColor = 'rgba(43,36,28,0.22)';
    c.shadowBlur = 26;
    c.shadowOffsetY = 12;
    roundRect(c, 0, 0, w, h, 14);
    c.fillStyle = C.card;
    c.fill();
    c.restore();

    var g = c.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, C.card);
    g.addColorStop(1, C.cardLo);
    roundRect(c, 0.5, 0.5, w - 1, h - 1, 14);
    c.fillStyle = g;
    c.fill();

    c.save();
    roundRect(c, 0.5, 0.5, w - 1, h - 1, 14);
    c.clip();
    for (var i = 0; i < 2400; i++) {
      c.fillStyle = r() < 0.5 ? 'rgba(28,26,23,0.012)' : 'rgba(255,255,255,0.05)';
      c.fillRect(r() * w, r() * h, 1.3, 1.3);
    }
    c.restore();

    roundRect(c, 0.5, 0.5, w - 1, h - 1, 14);
    c.strokeStyle = C.line;
    c.lineWidth = 1;
    c.stroke();

    roundRect(c, pad - 8, pad - 8, w - (pad - 8) * 2, h - (pad - 8) * 2, 8);
    c.strokeStyle = 'rgba(221,216,208,0.95)';
    c.lineWidth = 1;
    c.stroke();

    // 势力印
    c.save();
    c.translate(pad + 14, pad + 16);
    c.fillStyle = hero.factionColor;
    roundRect(c, -15, -15, 30, 30, 5);
    c.fill();
    c.fillStyle = '#fdf8ef';
    c.font = '700 19px ' + SERIF;
    c.textAlign = 'center';
    c.textBaseline = 'middle';
    c.fillText(hero.faction, 0, 1);
    c.restore();

    // 将名
    c.textAlign = 'left';
    c.textBaseline = 'middle';
    c.fillStyle = C.ink;
    c.font = '700 38px ' + SERIF;
    c.fillText(hero.name, pad + 42, pad + 17);

    // 体力段
    var segW = 22, segH = 13, segGap = 6, hp0 = hero.hp;
    var totalW = hp0 * segW + (hp0 - 1) * segGap;
    var hx = w - pad - 14 - totalW;
    for (var s = 0; s < hp0; s++) {
      roundRect(c, hx + s * (segW + segGap), pad + 10, segW, segH, 3);
      c.fillStyle = C.ink;
      c.fill();
    }
    c.textAlign = 'right';
    c.font = '400 13px ' + SANS;
    c.fillStyle = C.dim;
    c.fillText('体力上限 ' + hp0, w - pad - 14, pad + 44);

    // 淡墨竖排将名
    c.save();
    c.globalAlpha = 0.055;
    c.fillStyle = C.ink;
    c.textAlign = 'center';
    c.textBaseline = 'middle';
    c.font = '700 128px ' + SERIF;
    var chars = hero.name.split('');
    for (var ch = 0; ch < chars.length; ch++) {
      c.fillText(chars[ch], w * 0.5, h * 0.40 + (ch - (chars.length - 1) / 2) * 138);
    }
    c.restore();

    // 分隔线
    var divY = h - 214;
    c.beginPath();
    c.moveTo(pad, divY);
    c.lineTo(w - pad, divY);
    c.strokeStyle = 'rgba(221,216,208,1)';
    c.lineWidth = 1;
    c.stroke();

    // 技能
    for (var k = 0; k < hero.skills.length; k++) {
      var sk = hero.skills[k];
      var y = divY + 30 + k * 84;
      c.textAlign = 'left';
      c.textBaseline = 'alphabetic';
      c.fillStyle = C.ink;
      c.font = '700 20px ' + SERIF;
      c.fillText(sk[0], pad, y);
      c.fillStyle = C.dim;
      c.font = '400 15px ' + SANS;
      wrapText(c, sk[1], pad, y + 24, w - pad * 2, 19);
    }

    c.textAlign = 'right';
    c.textBaseline = 'alphabetic';
    c.font = '400 12px ' + SANS;
    c.fillStyle = 'rgba(111,106,98,0.45)';
    c.fillText('SGS · HERO', w - pad, h - 16);

    return cc[0];
  }

  function wrapText(c, text, x, y, maxW, lineH) {
    var line = '', out = [];
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (c.measureText(line + ch).width > maxW && line) { out.push(line); line = ch; }
      else line += ch;
    }
    if (line) out.push(line);
    for (var j = 0; j < out.length && j < 2; j++) c.fillText(out[j], x, y + j * lineH);
  }

  /* ================================================================
   * 刀痕几何（相对中剑方那张牌）
   * ============================================================== */
  function buildScar(cut, seed, card) {
    var N = 110;
    var ang = rad(cut.a);
    var dx = Math.cos(ang), dy = Math.sin(ang);
    var px = -dy, py = dx;

    var ox = card.cx + px * (cut.off || 0) * card.h * 0.5;
    var oy = card.cy + py * (cut.off || 0) * card.h * 0.5;
    var L = (Math.hypot(card.w, card.h) * 0.5 + 24 * (card.w / CARD_W)) * (cut.span || 1);

    var top = [], bot = [], mid = [];
    for (var i = 0; i <= N; i++) {
      var u = i / N * 2 - 1;
      var s = u * L;
      var bow = Math.sin(u * Math.PI) * 3.0 * Math.sin(seed * 0.7 + 1.3);
      var jitter = noise1(u * 3.2 + seed, seed) * 2.2;

      var nx = ox + dx * s + px * (bow + jitter);
      var ny = oy + dy * s + py * (bow + jitter);

      var taper = Math.pow(Math.max(0, 1 - u * u), 0.5);
      var load = 1 + 0.22 * Math.exp(-Math.pow((u - 0.1) * 3.2, 2));
      var upHalf = Math.max(0.35, cut.w * taper * load * (0.34 + 0.95 * Math.abs(noise1(u * 5.5 + seed * 3, seed + 7))) * 0.5);
      var dnHalf = Math.max(0.35, cut.w * taper * load * (0.34 + 0.95 * Math.abs(noise1(u * 6.1 + seed * 5, seed + 19))) * 0.5);

      mid.push([nx, ny, u]);
      top.push([nx + px * upHalf, ny + py * upHalf]);
      bot.push([nx - px * dnHalf, ny - py * dnHalf]);
    }
    return { top: top, bot: bot, mid: mid, dx: dx, dy: dy, px: px, py: py,
             ox: ox, oy: oy, L: L, ang: ang, cut: cut, seed: seed };
  }

  function drawScar(c, scar, k, a, ws, tear) {
    if (k <= 0 || a <= 0) return;
    var top = scar.top, bot = scar.bot, mid = scar.mid;
    var cutLen = Math.max(3, Math.floor(top.length * k));
    if (cutLen < 3) return;

    c.save();
    c.globalAlpha = a;

    // 纸下的墨晕
    c.save();
    c.globalCompositeOperation = 'multiply';
    for (var g = 0; g < 5; g++) {
      var p = mid[Math.floor((g + 0.5) / 5 * (cutLen - 1))];
      var R = (52 + g * 26) * ws;
      var gr = c.createRadialGradient(p[0], p[1], 0, p[0], p[1], R);
      gr.addColorStop(0, 'rgba(28,26,23,0.13)');
      gr.addColorStop(0.5, 'rgba(28,26,23,0.05)');
      gr.addColorStop(1, 'rgba(28,26,23,0)');
      c.fillStyle = gr;
      c.beginPath(); c.arc(p[0], p[1], R, 0, Math.PI * 2); c.fill();
    }
    c.restore();

    // 外扩软墨
    c.save();
    c.globalAlpha = a * 0.5;
    c.beginPath();
    c.moveTo(top[0][0], top[0][1]);
    for (var i = 1; i < cutLen; i++) c.lineTo(top[i][0], top[i][1]);
    for (var j = cutLen - 1; j >= 0; j--) c.lineTo(bot[j][0], bot[j][1]);
    c.closePath();
    c.shadowColor = 'rgba(28,26,23,0.30)';
    c.shadowBlur = 12 * ws;
    c.fillStyle = 'rgba(28,26,23,0.5)';
    c.fill();
    c.restore();

    // 墨身（沿刀走向有浓淡）
    c.beginPath();
    c.moveTo(top[0][0], top[0][1]);
    for (var i2 = 1; i2 < cutLen; i2++) c.lineTo(top[i2][0], top[i2][1]);
    for (var j2 = cutLen - 1; j2 >= 0; j2--) c.lineTo(bot[j2][0], bot[j2][1]);
    c.closePath();
    var mg = c.createLinearGradient(top[0][0], top[0][1], top[cutLen - 1][0], top[cutLen - 1][1]);
    mg.addColorStop(0.00, 'rgba(30,28,24,0.42)');
    mg.addColorStop(0.12, 'rgba(18,16,13,0.90)');
    mg.addColorStop(0.46, 'rgba(11,10,9,0.96)');
    mg.addColorStop(0.74, 'rgba(16,14,12,0.93)');
    mg.addColorStop(0.92, 'rgba(26,24,20,0.72)');
    mg.addColorStop(1.00, 'rgba(34,31,26,0.38)');
    c.fillStyle = mg;
    c.fill();

    // 毛刺
    var r = rng(scar.seed * 7 + 13);
    c.strokeStyle = 'rgba(24,22,19,0.38)';
    c.lineCap = 'round';
    for (var hh = 0; hh < 8; hh++) {
      var idx = 3 + Math.floor(r() * (cutLen - 7));
      var mp = mid[idx];
      var side = r() < 0.5 ? 1 : -1;
      var len = (5 + r() * 16) * ws;
      var wob = (r() - 0.5) * 0.55;
      c.lineWidth = (0.8 + r() * 1.6) * ws;
      c.beginPath();
      c.moveTo(mp[0] + scar.px * side * 2, mp[1] + scar.py * side * 2);
      c.lineTo(mp[0] + (scar.px * side + scar.dx * wob) * len,
               mp[1] + (scar.py * side + scar.dy * wob) * len);
      c.stroke();
    }

    // 刀沟浓墨
    c.beginPath();
    for (var m = 0; m < cutLen; m++) {
      if (m === 0) c.moveTo(mid[m][0], mid[m][1]); else c.lineTo(mid[m][0], mid[m][1]);
    }
    c.strokeStyle = 'rgba(8,7,7,0.9)';
    c.lineWidth = 2.4 * ws;
    c.lineJoin = 'round';
    c.stroke();

    // 上口沿反光
    var hFrom = Math.max(2, Math.floor(cutLen * 0.12));
    var hTo = Math.floor(cutLen * 0.94);
    c.beginPath();
    for (var q = hFrom; q < hTo; q++) {
      var bx = top[q][0] - scar.px * 1.1 * ws, by = top[q][1] - scar.py * 1.1 * ws;
      if (q === hFrom) c.moveTo(bx, by); else c.lineTo(bx, by);
    }
    c.strokeStyle = 'rgba(255,253,248,0.30)';
    c.lineWidth = 1.0 * ws;
    c.stroke();
    c.lineCap = 'butt';

    // 飞白
    var r2 = rng(Math.round(scar.cut.a * 13 + 71));
    for (var d = 0; d < 9; d++) {
      var pt = mid[2 + Math.floor(r2() * (cutLen - 4))];
      c.globalAlpha = a * (0.30 + r2() * 0.45);
      c.fillStyle = '#fffdf7';
      c.beginPath();
      c.ellipse(pt[0], pt[1], (1.6 + r2() * 4.6) * ws, (0.7 + r2() * 1.5) * ws,
                scar.ang + (r2() - 0.5) * 0.5, 0, Math.PI * 2);
      c.fill();
    }
    c.globalAlpha = a;

    // 破口
    if (tear > 0.01) {
      var tp = mid[clamp(Math.floor(cutLen * 0.46), 1, cutLen - 2)];
      var th = 10 * tear * ws;
      c.save();
      c.translate(tp[0], tp[1]);
      c.rotate(scar.ang);
      c.fillStyle = C.paper;
      c.beginPath();
      for (var e = 0; e <= 30; e++) {
        var ea = e / 30 * Math.PI * 2;
        var rr = th * (0.45 + 0.9 * Math.abs(noise1(e * 1.1 + 4, 12)));
        var ex = Math.cos(ea) * rr * 2.6, ey = Math.sin(ea) * rr * 0.55;
        if (e === 0) c.moveTo(ex, ey); else c.lineTo(ex, ey);
      }
      c.closePath();
      c.fill();
      c.strokeStyle = 'rgba(24,22,19,0.62)';
      c.lineWidth = 1.2;
      c.stroke();
      c.restore();
    }

    c.restore();
  }

  /* ================================================================
   * 剑光
   * ============================================================== */
  /* 剑光航线由 `S`（本实例的舞台）给：参考舞台是 A=(752,582) → B=(1332,516)，
   * 牌桌上是覆盖层量出来的两张牌算出的同一比例航线（票 05）。 */
  function swordHead(t, variant, T, S) {
    var k = clamp((t - T.LAUNCH) / variant.flight, 0, 1);
    return { k: k, x: lerp(S.a.x, S.b.x, eIn(k) * 0.25 + eOut(k) * 0.75), y: lerp(S.a.y, S.b.y, k) };
  }

  function drawSwordLight(c, t, variant, T, S) {
    if (t < T.LAUNCH - 0.02 || t > T.IMPACT + 0.18) return;
    var head = swordHead(t, variant, T, S);
    var ang = Math.atan2(S.b.y - S.a.y, S.b.x - S.a.x);
    var dx = Math.cos(ang), dy = Math.sin(ang);
    var px = -dy, py = dx;

    var after = clamp((t - T.IMPACT) / 0.18, 0, 1);
    var boost = variant.split ? 1.7 : 1;
    var hx = head.x + dx * after * 220 * S.u * boost;
    var hy = head.y + dy * after * 220 * S.u * boost;

    var fade = (1 - after) * (t >= T.LAUNCH ? 1 : 0);
    if (fade <= 0.01) return;

    var w = variant.swordW;
    var B = variant.blade;

    // 拖尾始终回指出剑方；伤害越大，剑光越长
    var distA = Math.hypot(hx - S.a.x, hy - S.a.y);
    var tailLen = Math.min(distA + 90 + (variant.dmg - 1) * 130, 1000);

    c.save();
    c.globalAlpha = fade;

    /* 叶形剑气：尾细 → 七分处最宽 → 剑尖收成一点。
       宽度恒定的话看着就是一根蓝管子。 */
    function blade(wTail, wMax, c0, c1, c2) {
      var tx = hx - dx * tailLen, ty = hy - dy * tailLen;
      var mx = hx - dx * tailLen * 0.28, my = hy - dy * tailLen * 0.28;

      c.beginPath();
      c.moveTo(tx + px * wTail, ty + py * wTail);
      c.lineTo(mx + px * wMax, my + py * wMax);
      c.lineTo(hx, hy);                       // 剑尖
      c.lineTo(mx - px * wMax, my - py * wMax);
      c.lineTo(tx - px * wTail, ty - py * wTail);
      c.closePath();

      var grd = c.createLinearGradient(tx, ty, hx, hy);
      grd.addColorStop(0, c0);
      grd.addColorStop(0.45, c1);
      grd.addColorStop(1, c2);
      c.fillStyle = grd;
      c.fill();
    }

    // 尾丝：几根慢半拍的细剑气，别让它像一根管子
    if (variant.wisps > 0) {
      for (var i = 0; i < variant.wisps; i++) {
        var side = i % 2 === 0 ? 1 : -1;
        var off = (7 + i * 6) * side;
        var lag = 0.5 + 0.12 * i;
        var ex = hx - dx * tailLen * lag, ey = hy - dy * tailLen * lag;
        c.save();
        c.globalAlpha = fade * (0.32 - i * 0.028);
        c.lineWidth = Math.max(1.1, w * (0.14 - i * 0.01));
        c.lineCap = 'round';
        c.strokeStyle = rgba(B.mid, 0.85);
        c.beginPath();
        c.moveTo(ex + px * off, ey + py * off);
        c.quadraticCurveTo(
          lerp(ex, hx, 0.62) + px * off * 1.7, lerp(ey, hy, 0.62) + py * off * 1.7,
          hx + px * off * 0.3, hy + py * off * 0.3
        );
        c.stroke();
        c.restore();
      }
    }

    // 重伤：剑气底下压一层墨影，飞过的纸面都在发暗
    if (variant.dmg >= 3) {
      blade(w * 0.9, w * 2.4, rgba(B.glow, 0), rgba(B.glow, 0.18), rgba(B.glow, 0.30));
    }

    // 外晕 → 本体 → 白芯
    blade(w * 1.6, w * 2.6, rgba(B.glow, 0), rgba(B.glow, 0.38), rgba(B.glow, 0.55));
    blade(w * 0.8, w * 1.05, rgba(B.mid, 0), rgba(B.mid, 0.72), rgba(B.mid, 0.96));
    blade(Math.max(1.2, w * 0.16), Math.max(0.9, w * 0.30),
          'rgba(255,255,255,0)', 'rgba(255,255,255,0.85)', rgba(B.core, 1));

    // 剑尖的一点亮
    var tipR = 16 * S.u + w * 1.1;
    var bgr = c.createRadialGradient(hx, hy, 0, hx, hy, tipR);
    bgr.addColorStop(0, 'rgba(255,255,255,0.92)');
    bgr.addColorStop(0.34, rgba(B.mid, 0.45));
    bgr.addColorStop(1, rgba(B.mid, 0));
    c.fillStyle = bgr;
    c.beginPath(); c.arc(hx, hy, tipR, 0, Math.PI * 2); c.fill();

    // 四点伤害：剑气残影
    if (variant.dmg >= 4) {
      for (var g = 1; g <= 2; g++) {
        c.save();
        c.globalAlpha = fade * 0.18 / g;
        c.translate(-dx * 26 * g, -dy * 26 * g + 18 * g);
        c.strokeStyle = rgba(B.mid, 0.7);
        c.lineWidth = w * 0.5;
        c.lineCap = 'round';
        c.beginPath();
        c.moveTo(hx - dx * tailLen * 0.9, hy - dy * tailLen * 0.9);
        c.lineTo(hx, hy);
        c.stroke();
        c.restore();
      }
    }
    c.restore();
  }

  /* 出剑前：剑意凝聚 */
  function drawCharge(c, t, variant, T, S) {
    if (t < T.CHARGE || t > T.LAUNCH + 0.04) return;
    var k = ss(T.CHARGE, T.LAUNCH, t);
    var a = Math.sin(k * Math.PI * 0.5) * 0.9;
    var r = (6 + 40 * k) * S.u + variant.swordW * 1.4 * k;
    var g = c.createRadialGradient(S.a.x, S.a.y, 0, S.a.x, S.a.y, r * 3);
    g.addColorStop(0, 'rgba(255,255,255,' + (0.9 * a) + ')');
    g.addColorStop(0.3, 'rgba(214,232,255,' + (0.45 * a) + ')');
    g.addColorStop(1, 'rgba(214,232,255,0)');
    c.save();
    c.fillStyle = g;
    c.beginPath(); c.arc(S.a.x, S.a.y, r * 3, 0, Math.PI * 2); c.fill();
    c.restore();
  }

  /* ================================================================
   * 墨点
   * ============================================================== */
  function buildDrops(variant, seed, cut, card) {
    var r = rng(seed);
    var ang = rad(cut.a);
    var px = -Math.sin(ang), py = Math.cos(ang);
    var dx = Math.cos(ang), dy = Math.sin(ang);
    // 墨点的大小 / 铺开距离跟着牌走：牌桌上的武将块比参考牌小得多，不缩就是一串胖墨团。
    var k = card.w / CARD_W;
    var ox = card.cx + px * (cut.off || 0) * card.h * 0.5;
    var oy = card.cy + py * (cut.off || 0) * card.h * 0.5;
    var out = [];
    for (var i = 0; i < variant.inkDrops; i++) {
      var u = (r() * 2 - 1) * 0.9;
      var side = r() < 0.5 ? -1 : 1;
      out.push({
        x: ox + dx * u * card.h * 0.46 + px * side * (6 + r() * 14) * k,
        y: oy + dy * u * card.h * 0.46 + py * side * (6 + r() * 14) * k,
        vx: px * side * (26 + r() * 120) * k + dx * (r() - 0.5) * 60 * k,
        vy: py * side * (26 + r() * 120) * k + dy * (r() - 0.5) * 60 * k - (8 + r() * 40) * k,
        rad: (1.5 + Math.pow(r(), 2.2) * 6.5) * k,
        red: variant.cinnabar * r(),
        life: 0.42 + r() * 0.85,
        streak: r() < 0.35
      });
    }
    return out;
  }

  function drawDrops(c, drops, t, T) {
    for (var i = 0; i < drops.length; i++) {
      var d = drops[i];
      var age = t - T.IMPACT;
      if (age <= 0 || age > d.life + 0.9) continue;
      var settle = eOut(Math.min(age, 0.46) / 0.46);
      var x = d.x + d.vx * settle * 0.46;
      var y = d.y + d.vy * settle * 0.46 + 30 * Math.min(1, age / 1.0);
      var lifeK = 1 - ss(d.life, d.life + 0.9, age);
      var a = lifeK * (0.88 + 0.12 * Math.exp(-age * 0.4));
      if (a <= 0.01) continue;

      var isRed = d.red > 0.12;
      var col = isRed ? C.red : C.ink;
      var rr = d.rad * (0.55 + 0.8 * Math.min(1, age / 0.08));
      c.globalAlpha = a * (isRed ? 0.55 + d.red * 0.42 : 0.95);
      c.fillStyle = col;
      if (d.streak) {
        var vlen = Math.hypot(d.vx, d.vy) || 1;
        c.beginPath();
        c.ellipse(x, y, rr * 2.4, rr * 0.7, Math.atan2(d.vy / vlen, d.vx / vlen), 0, Math.PI * 2);
        c.fill();
      } else {
        c.beginPath(); c.arc(x, y, rr, 0, Math.PI * 2); c.fill();
      }
    }
    c.globalAlpha = 1;
  }

  /* ================================================================
   * 伤害飘字
   * ============================================================== */
  /* 伤害飘字：`txt` 由 `create()` 按服务端的 `amount` 定好（票 05），这里只画。
   * 位置 / 上浮距离按中剑牌那块矩形换算（`offset`），故任意座位大小都落在牌上。 */
  function drawNumber(c, variant, t, T, dst, txt, offset) {
    var age = t - T.IMPACT;
    if (age < 0 || age > T.NUM_LIFE) return;

    var pop = eBack(clamp(age / 0.22, 0, 1));
    var rise = eOut(clamp(age / 1.18, 0, 1));
    var alpha = 1 - clamp((age - (T.NUM_LIFE - 0.60)) / 0.60, 0, 1);
    if (alpha <= 0.01) return;

    var size = variant.numSize;

    c.save();
    c.translate(
      dst.cx + dst.w * offset.dx,
      dst.cy - dst.h * offset.dy - rise * dst.h * offset.rise
    );
    c.rotate(-0.05 + (1 - rise) * 0.03);
    c.scale(0.6 + 0.4 * pop, 0.6 + 0.4 * pop);
    c.textAlign = 'center';
    c.textBaseline = 'middle';

    var glowR = size * (0.82 + variant.cinnabar * 0.4);
    var gr = c.createRadialGradient(0, 0, 0, 0, 0, glowR);
    gr.addColorStop(0, rgba(C.red, 0.13 * alpha * (0.4 + variant.cinnabar)));
    gr.addColorStop(0.55, rgba(C.red, 0.035 * alpha * variant.cinnabar));
    gr.addColorStop(1, rgba(C.red, 0));
    c.fillStyle = gr;
    c.beginPath(); c.arc(0, 0, glowR, 0, Math.PI * 2); c.fill();

    if (variant.numEcho > 0) {
      for (var e = variant.numEcho; e >= 1; e--) {
        c.save();
        c.globalAlpha = alpha * 0.15 * (1 - e / (variant.numEcho + 1.4));
        c.font = '700 ' + size + 'px ' + SERIF;
        c.fillStyle = C.ink;
        c.fillText(txt, e * 11, -e * 13);
        c.restore();
      }
    }

    c.font = '700 ' + size + 'px ' + SERIF;
    c.globalAlpha = alpha;
    c.lineJoin = 'round';
    c.strokeStyle = 'rgba(255,253,248,0.95)';
    c.lineWidth = size * 0.078;
    c.strokeText(txt, 0, 0);

    c.globalAlpha = alpha * (0.42 + variant.cinnabar * 0.30);
    c.strokeStyle = C.red;
    c.lineWidth = size * 0.034;
    c.strokeText(txt, 0, 0);

    c.globalAlpha = alpha;
    c.fillStyle = C.ink;
    c.fillText(txt, 0, 0);
    c.restore();
  }

  /* ================================================================
   * 主入口
   * ============================================================== */

  /* 本实例的目标画布尺寸（CSS px）：缺省 = 参考舞台 1920×1080。
   * 牌桌档由 `effects.js` 传覆盖层的实际尺寸进来（见 `view` 的注释）。 */
  function viewSize(view) {
    var w = view ? Number(view.width) : NaN;
    var h = view ? Number(view.height) : NaN;
    return {
      w: isFinite(w) && w > 0 ? w : W,
      h: isFinite(h) && h > 0 ? h : H
    };
  }

  /* 静态贴图缓存（票 05）：键 → 一张离屏 canvas。画进它之后就只 `drawImage`，故多实例共用。 */
  var IMAGE_CACHE = {};

  function sharedImage(key, paint) {
    if (!IMAGE_CACHE[key]) IMAGE_CACHE[key] = paint();
    return IMAGE_CACHE[key];
  }

  function create(canvas, opts) {
    opts = opts || {};
    // 本实例自己的钟：`profile: 'game'` 走 anim.ini 的四档紧凑时间轴，缺省仍是 export 档
    // 那条 12 秒链（`renderAt(t)` / `?still` / `?export` 三个模式逐字节不变）。
    var clock = clockFor(opts);
    var vId = clock.id;
    // `var T` 遮蔽模块级的 export 时间轴：`create` 内部的所有函数都读实例的这一份。
    var T = clock.timeline;
    var variant = clock.variant;
    // 票 05：两块座位矩形（覆盖层量出来的武将块，**舞台坐标**）。缺省 = 参考舞台。
    var S = stageFor(opts.stage);
    // 构图比例：牌桌上的武将块比参考牌 460×644 小得多，"牌上的绝对尺寸"跟着缩
    // （`u === 1` 时一个乘法都不做，素材库那条链因此逐字节不变）。
    if (S.u !== 1) {
      variant = copy(variant, {
        swordW: variant.swordW * S.u, scarW: variant.scarW * S.u,
        numSize: variant.numSize * S.u, shake: variant.shake * S.u,
        // 劈屏错位同样是"牌上的尺寸"：牌桌档的坐标系是 CSS px，44 单位不缩就是硬劈一刀。
        split: variant.split * S.u
      });
    }
    // 飘字文案：服务端 `amount` 是真实伤害（酒 / 加伤可能 ≥ 5），档位只决定强度。
    var txt = numberText(variant, opts.amount);
    // 票 05 R1：**牌桌档 = 只出特效**。纸底与两张样例武将牌是素材库舞台的布景，铺到牌桌上
    // 就是一块不透明的米色板（整个牌桌被盖住）＋ 与真实武将不符的假牌；故 `bare: true`
    // 时两样都不画，牌桌透得出来。缺省仍是素材库那条链（逐字节不变）。
    var bare = opts.bare === true;
    // 票 05 R2：牌桌档的坐标空间 = **覆盖层的 CSS 像素**（`view` 由 effects.js 给），
    // 画布按 `view × dpr` 建、内部 `drawImage` 是 1:1 合成——不再"1920 舞台画完再缩小"
    // （那次 ~3 倍降采样会把 2px 的刀痕抹平，实机验收里就是一条灰铅笔线）。
    // 缺省仍是 1920×1080 的参考舞台（`?still` / `?export` 逐字节不变）。
    var size = viewSize(opts.view);
    // 局部 `W` / `H` = 本实例的目标画布尺寸，遮蔽模块级的参考舞台（`viewSize` 在模块作用域
    // 里读真值，故缺省仍是 1920×1080）。
    var W = size.w, H = size.h;
    // 票 05 R3 / R4：牌桌档的飘字——**字号两头夹 + 位置由包围盒判据反推**。
    //   字高 ∈ [SCREEN_MIN.number, 35% × 牌高]（牌高小的时候上限优先：宁可小，也不能压住
    //   体力读数；`clamp` 在上限小于下限时返回上限）；
    //   位置：硬线 = 中剑方**体力条下沿**（R4：覆盖层量 `.hp-bar` 的真矩形传进来，相对武将块
    //   左上角的 CSS px；量不到才退回 `NUMBER_KEEP_OUT_FALLBACK`）。上浮到顶那一刻包围盒的
    //   顶边正好落在"硬线 + 2% 牌高"上 ⇒ **任意时刻**的包围盒都不与体力条相交。
    var numberOffset = NUMBER_OFFSET;
    var numberGlyph = variant.numSize * GLYPH_RATIO;
    var keepOutPx = null;
    if (bare) {
      var cardH = Number(S.dst.h) || CARD_H;
      numberGlyph = clamp(numberGlyph, SCREEN_MIN.number, NUMBER_MAX_RATIO * cardH);
      variant = copy(variant, { numSize: numberGlyph / GLYPH_RATIO });
      // 硬线必须在牌内才有意义（量到牌外 / 0 / 负数 ⇒ 当作量不到，退回兜底比例）。
      var keepOut = Number(opts.numberKeepOut);
      var keepOutRatio = isFinite(keepOut) && keepOut > 0 && keepOut < cardH
        ? keepOut / cardH
        : NUMBER_KEEP_OUT_FALLBACK;
      keepOutPx = keepOutRatio * cardH;
      var boxTopRatio = Math.min(0.9, keepOutRatio + NUMBER_GAP_RATIO);
      // 包围盒顶边落在 boxTopRatio ⇒ 反推起飘点（`dy` 为"牌心上方为正"的旧口径，故取负）。
      var belowCenter = boxTopRatio + numberGlyph / (2 * cardH) - 0.5 + NUMBER_RISE_RATIO;
      // 起飘点至少压在牌心下方一点；整盒也不许越出牌底（硬线极低时这条先兜住）。
      belowCenter = Math.max(belowCenter, NUMBER_BELOW_MIN);
      belowCenter = Math.min(belowCenter, 0.5 - numberGlyph / (2 * cardH) - 0.01);
      numberOffset = { dx: NUMBER_OFFSET.dx, dy: -belowCenter, rise: NUMBER_RISE_RATIO };
    }
    // 飘字包围盒（相对中剑牌左上角，CSS px）：取 `rise = 1` 的**最坏情形**（最靠上的那一刻）。
    var cardTop = S.dst.cy - S.dst.h / 2;
    var numberCenterY = S.dst.cy - S.dst.h * numberOffset.dy - S.dst.h * numberOffset.rise;
    var numberBox = {
      top: numberCenterY - numberGlyph / 2 - cardTop,
      bottom: numberCenterY + numberGlyph / 2 - cardTop,
      height: numberGlyph,
    };
    var seed = 1700 + vId * 977;
    var dpr = opts.dpr || 1;

    var ctx = canvas.getContext('2d');
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 场景层：先画在这上面，再做屏裂 / 屏闪的合成
    var sc = offscreen(W * dpr, H * dpr);
    var sceneCanvas = sc[0], sceneCtx = sc[1];
    sceneCtx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 三张静态图（宣纸底 / 两张武将牌）都是**只读**贴图，跨实例复用：牌桌上一段伤害
    // 就建一次 1920×1080 的纸底 + 两张 460×644 的牌是几十毫秒的主线卡顿（票 05 起
    // 每次伤害都要建一个实例，故缓存到模块级；`drawImage` 只读，多实例共用安全）。
    var paper = bare ? null
      : sharedImage('paper:' + W + 'x' + H, function () { return paintPaper(W, H, 21); });
    var heroSrc = bare ? null : sharedImage('heroSrc', function () { return paintHeroCard(HEROES.src, 401); });
    var heroDst = bare ? null : sharedImage('heroDst', function () { return paintHeroCard(HEROES.dst, 733); });

    var cut = { a: -31, off: -0.02, w: variant.scarW, span: 1.0 };
    var scar = buildScar(cut, seed, S.dst);
    var drops = buildDrops(variant, seed + 5, cut, S.dst);

    var fadeStart = variant.fadeStart, fadeEnd = variant.fadeEnd;
    var halfPaths = {};

    function drawCardsAndFx(c, t, S) {
      var age = t - T.IMPACT;

      // 震屏（只震牌面上的东西，纸底不动）
      var sx = 0, sy = 0;
      if (age >= 0 && age < 1.0) {
        var amp = variant.shake * Math.exp(-age * 10);
        sx = amp * Math.sin(age * 92);
        sy = amp * Math.cos(age * 78);
      }

      c.save();
      c.translate(sx, sy);

      // 两张武将牌（阴影已烘进图里，这里只贴图）：贴进本实例的两块座位矩形。
      // `bare`（牌桌档）**不画**——牌桌上那两张是真实的武将块，画上去就是"真牌 + 假牌"两层。
      if (heroSrc && heroDst) {
        drawHeroCard(c, heroSrc, S.src);
        drawHeroCard(c, heroDst, S.dst);
      }

      // 出剑瞬间，出剑方整块亮一下
      var srcFlash = age >= 0 && age < 0.22 ? 0.5 * Math.exp(-age * 14) : 0;
      if (srcFlash > 0.01) {
        c.save();
        c.globalAlpha = srcFlash;
        c.globalCompositeOperation = 'lighter';
        c.fillStyle = 'rgba(210,228,255,0.35)';
        roundRect(c, S.src.cx - S.src.w / 2, S.src.cy - S.src.h / 2, S.src.w, S.src.h, 14);
        c.fill();
        c.restore();
      }

      // ---- 中剑方牌内 ----
      c.save();
      roundRect(c, S.dst.cx - S.dst.w / 2, S.dst.cy - S.dst.h / 2, S.dst.w, S.dst.h, 14);
      c.clip();

      if (age >= 0 && age < 0.5) {
        c.fillStyle = 'rgba(28,26,23,' + (0.09 * Math.exp(-age * 9)).toFixed(4) + ')';
        c.fillRect(S.dst.cx - S.dst.w / 2, S.dst.cy - S.dst.h / 2, S.dst.w, S.dst.h);
      }

      if (age > 0) {
        var fadeOut = 1 - ss(fadeStart, fadeEnd, t);
        var k = eOut(clamp(age / 0.15, 0, 1));
        var a = fadeOut * clamp(0.55 + age / 0.12, 0, 1);
        drawScar(c, scar, k, a, variant.scarW / 14, variant.paperTear);

        if (variant.cinnabar > 0.01 && age > 0.05) {
          var spread = ss(0, 1.8, age) * variant.cinnabar;
          if (spread > 0.01) {
            c.save();
            c.globalCompositeOperation = 'multiply';
            for (var q = 0; q < 2; q++) {
              var mp = scar.mid[Math.floor((q + 0.5) / 2 * (scar.mid.length - 1))];
              var R = (40 + q * 22) * S.u;
              var gr = c.createRadialGradient(mp[0], mp[1], 0, mp[0], mp[1], R);
              gr.addColorStop(0, rgba(C.red, 0.13 * spread * a));
              gr.addColorStop(0.55, rgba(C.red, 0.05 * spread * a));
              gr.addColorStop(1, rgba(C.red, 0));
              c.fillStyle = gr;
              c.beginPath(); c.arc(mp[0], mp[1], R, 0, Math.PI * 2); c.fill();
            }
            c.restore();
          }
        }
        drawDrops(c, drops, t, T);
      }
      c.restore();

      // ---- 剑意 / 剑光 / 命中光（画在牌之上）----
      drawCharge(c, t, variant, T, S);
      drawSwordLight(c, t, variant, T, S);

      if (age >= 0 && age < 0.34) {
        var ba = Math.pow(1 - age / 0.34, 1.8);
        var BR = (120 + 30 * variant.dmg) * S.u * (0.5 + 0.5 * eOut(age / 0.1));
        var bg = c.createRadialGradient(S.b.x, S.b.y, 0, S.b.x, S.b.y, BR);
        bg.addColorStop(0, 'rgba(255,255,255,' + (0.75 * ba) + ')');
        bg.addColorStop(0.35, 'rgba(240,246,255,' + (0.28 * ba) + ')');
        bg.addColorStop(1, 'rgba(240,246,255,0)');
        c.fillStyle = bg;
        c.beginPath(); c.arc(S.b.x, S.b.y, BR, 0, Math.PI * 2); c.fill();
      }

      drawNumber(c, variant, t, T, S.dst, txt, numberOffset);
      c.restore();
    }

    /* 把烘好阴影的武将牌贴进一块矩形：牌面正好铺满 rect，阴影按同一比例外扩。
     * `rect === SRC / DST`（参考舞台）时退回原来的自然尺寸贴图，逐字节一致。 */
    function drawHeroCard(c, image, rect) {
      var padX = SHADOW_PAD * rect.w / CARD_W;
      var padY = SHADOW_PAD * rect.h / CARD_H;
      c.drawImage(
        image,
        rect.cx - rect.w / 2 - padX, rect.cy - rect.h / 2 - padY,
        rect.w + padX * 2, rect.h + padY * 2
      );
    }

    function renderScene(c, target) {
      target.setTransform(dpr, 0, 0, dpr, 0, 0);
      target.clearRect(0, 0, W, H);
      target.drawImage(paper, 0, 0, W, H);
      drawCardsAndFx(target, t, S);
    }

    function draw(t) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // 不劈屏的档位：直接画在主画布上，省掉一次全屏搬运
      if (variant.split <= 0) {
        ctx.clearRect(0, 0, W, H);
        if (paper) ctx.drawImage(paper, 0, 0, W, H);
        drawCardsAndFx(ctx, t, S);
        postProcess(ctx, t);
        return;
      }

      // 要劈屏：先画到场景层
      sceneCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      sceneCtx.clearRect(0, 0, W, H);
      if (paper) sceneCtx.drawImage(paper, 0, 0, W, H);
      drawCardsAndFx(sceneCtx, t, S);
      ctx.clearRect(0, 0, W, H);

      var splitK = 0;
      if (variant.split > 0) {
        splitK = clamp(ss(T.IMPACT, T.IMPACT + 0.10, t) - ss(T.IMPACT + 0.55, T.IMPACT + 1.15, t), 0, 1);
      }
      var sep = variant.split * splitK;

      if (sep > 0.6) {
        // 切口沿剑光的飞行方向斜着走，两半沿法线分开
        var cAng = Math.atan2(S.b.y - S.a.y, S.b.x - S.a.x);
        var cca = Math.cos(cAng), csa = Math.sin(cAng);
        var nx = -csa, ny = cca;                  // 法线（指向下方那半）
        var EXT = 2400;

        var ax = S.b.x - cca * EXT, ay = S.b.y - csa * EXT;
        var bx = S.b.x + cca * EXT, by = S.b.y + csa * EXT;

        if (!halfPaths.up) {
          var up = new Path2D();
          up.moveTo(ax, ay); up.lineTo(bx, by);
          up.lineTo(bx - nx * EXT, by - ny * EXT);
          up.lineTo(ax - nx * EXT, ay - ny * EXT);
          up.closePath();
          var down = new Path2D();
          down.moveTo(ax, ay); down.lineTo(bx, by);
          down.lineTo(bx + nx * EXT, by + ny * EXT);
          down.lineTo(ax + nx * EXT, ay + ny * EXT);
          down.closePath();
          halfPaths = { up: up, down: down };
        }

        // 缝里的"虚空"：素材库档铺黑底（两半像从纸里撕开）；牌桌档**不铺**——
        // 那是一次不透明度 1 的全屏填充，等于把牌桌盖住（实机验收被挡的就是这一类）。
        if (!bare) {
          ctx.fillStyle = '#0d0c0b';
          ctx.fillRect(0, 0, W, H);
        }

        // 上半天 / 下半天（Path2D 缓存，别每帧重建多边形）
        ctx.save();
        ctx.clip(halfPaths.up);
        ctx.translate(-nx * sep, -ny * sep);
        ctx.drawImage(sceneCanvas, 0, 0, W, H);
        ctx.restore();

        ctx.save();
        ctx.clip(halfPaths.down);
        ctx.translate(nx * sep, ny * sep);
        ctx.drawImage(sceneCanvas, 0, 0, W, H);
        ctx.restore();

        // 切口：外黑边 + 白光 + 热芯
        ctx.save();
        ctx.lineCap = 'butt';
        ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by);
        // 缝里留黑，只在正中一条高温亮线 + 一圈柔光
        ctx.strokeStyle = 'rgba(255,255,255,' + (0.22 * splitK) + ')';
        ctx.lineWidth = Math.max(6, sep * 1.7);
        ctx.stroke();
        ctx.strokeStyle = 'rgba(255,255,255,' + (0.75 * splitK) + ')';
        ctx.lineWidth = Math.max(3, sep * 0.34);
        ctx.stroke();
        ctx.strokeStyle = 'rgba(255,255,255,' + splitK + ')';
        ctx.lineWidth = Math.max(1.4, sep * 0.12);
        ctx.stroke();
        ctx.restore();
      } else {
        ctx.drawImage(sceneCanvas, 0, 0, W, H);
      }

      postProcess(ctx, t);
    }

    function postProcess(c, t) {
      var age = t - T.IMPACT;

      // 命中白闪
      if (age >= 0 && age < 0.16) {
        var fa = Math.pow(1 - age / 0.16, 2) * (0.30 + 0.06 * variant.dmg);
        c.fillStyle = 'rgba(255,255,255,' + fa.toFixed(4) + ')';
        c.fillRect(0, 0, W, H);
      }

      // 黑白屏闪（3 点起）
      if (variant.strobe > 0 && t >= T.IMPACT && t <= T.IMPACT + variant.strobe) {
        // 15Hz：30fps 下正好一明一暗各两帧，不会和帧率打架
        var phase = Math.floor((t - T.IMPACT) * 15);
        var bright = phase % 2 === 0;

        if (bare) {
          // 牌桌档做不了"整屏转黑白"：`saturation` 是合成模式，喂给**透明**层只会得到
          // 一张不透明度 1 的灰（等于把牌桌盖住 0.16 / 0.44 秒，实机验收被打回的就是
          // 这一类全屏不透明覆盖）。这里只留明暗闪：牌桌照旧透得出来，重击的顿挫仍在。
          c.save();
          if (bright) {
            c.globalAlpha = 0.12;
            c.fillStyle = '#ffffff';
          } else {
            c.globalAlpha = 0.34;
            c.fillStyle = '#14120f';
          }
          c.fillRect(0, 0, W, H);
          c.restore();
          return;   // 下面那段是素材库档的整屏去色 + 压暗，牌桌档不参与
        }

        // 去饱和：saturation 混合模式喂一个零饱和度色
        c.save();
        c.globalCompositeOperation = 'saturation';
        c.fillStyle = 'hsl(0, 0%, 50%)';
        c.fillRect(0, 0, W, H);
        c.restore();

        c.save();
        if (!bright) {
          c.globalCompositeOperation = 'multiply';
          c.fillStyle = 'rgba(58,56,52,0.44)';
        } else {
          // 亮帧再推一把白，明暗差才够狠
          c.globalAlpha = 0.12;
          c.fillStyle = '#ffffff';
        }
        c.fillRect(0, 0, W, H);
        c.restore();
      }
    }

    return {
      draw: draw, duration: T.DURATION, variant: vId, width: W, height: H,
      profile: clock.profile, timeline: T, timing: clock.ms,
      // 票 05：本实例的舞台（座位矩形 / 构图比例 / 剑光航线）与飘字文案。播放器用
      // `duration` 定时摘层（= `anim.ini` 的 `total`），票 06 的音效读同一份 `timeline`。
      stage: S, number: txt, bare: bare,
      // 落屏尺寸（牌桌档的坐标系就是覆盖层 CSS px）：三个数直接对应验收口径
      // "刀痕墨身 ≥ 4 / 飘字字高 ≥ 40 / 剑光一眼认得出"。
      metrics: {
        u: S.u,
        scar: variant.scarW * 0.94,
        number: numberGlyph,
        sword: variant.swordW * 2.1,
        // 中剑牌的尺寸、飘字的包围盒、生效的硬线（都相对牌左上角 / CSS px）——
        // R4 的验收判据直接读这三个，不再换算百分比。
        card: { w: S.dst.w, h: S.dst.h },
        numberBox: numberBox,
        keepOut: keepOutPx,
      },
      numberOffset: numberOffset
    };
  }

  global.SgsDamageFX = {
    create: create, VARIANTS: VARIANTS, TIMELINE: T, COLORS: C,
    WIDTH: W, HEIGHT: H, SRC: SRC, DST: DST,
    // 票 04：游戏档的时长表。`DEFAULT_TIMING` = anim.ini 那 19 个键的内置默认，
    // `gameTiming(variant, flatMap)` = 解算 + 钳制后的秒级时间轴（effects.js / 票 06 读它）。
    DEFAULT_TIMING: DEFAULT_TIMING, MAX_TOTAL_MS: MAX_TOTAL_MS,
    TIMING_PREFIX: TIMING_PREFIX, gameTiming: gameTiming,
    // 票 05 R2：牌桌档的落屏下限（构图比例不许小于它）与三样下限尺寸（CSS px）。
    SCREEN_MIN: SCREEN_MIN, COMPOSITION_FLOOR: COMPOSITION_FLOOR
  };
})(window);
