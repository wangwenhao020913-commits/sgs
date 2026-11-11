"use strict";

/* SGS 前端 · **失去体力**演出的画面（vital-fx 票 03）：`SgsHpLossFX`。
 *
 * 移植自素材包 `outputs/sgs-hp-loss-fx/fx-hp-loss.js`（可播放的定稿参考实现）：一张武将牌
 * 失去体力——不是被打，是牌里的墨自己漏出去一点。牌脚先渗出一小片湿墨 → 挂成一颗墨珠、
 * 拉丝、断开 → 坠到纸上洇开一团墨 → `-N` 从墨晕里浮起来，最后慢慢干掉。三点以上三滴齐坠、
 * 整张牌往下湿。**没有来源、没有属性、没有命中**，所以这里没有光束、没有屏闪。
 *
 * 搬进牌桌时只改五类事，画法逐段保留（与 `fx-thunder.js` / `fx-fire.js` 同一套姿态）：
 *
 * 1. **双时间轴**：`profile: "export"` 照抄素材页的 2.65 / 2.90 / 3.35 秒链（`?still` /
 *    `?export` / node 探针逐帧导静帧）；`profile: "game"` 读 `anim.ini` 的
 *    `hp_loss_fx_<档>_<字段>`（缺键退 `DEFAULT_TIMING`，钳制在 `gameTiming` 里）。
 *    `TIMING_FIELDS` / `TIMING_KEYS` 由 `DEFAULT_TIMING` 的键**生成**——字段名只有一处。
 * 2. **牌桌档是「等比重排」，不是「另画一套」**：素材页的四个拍子（`spotAt` / 第一次
 *    `detachAt` / `numAt` / `duration`）按**锚点**各自对到 `anim.ini` 的 `spot` / `break` /
 *    `num` / `total` 上（`gameTiming` 返回 `warp`），`draw(t)` 先把演出自己的秒数映射回素材页
 *    的秒数再画——段内线性，于是墨珠的「湿 → 挂 → 断 → 坠 → 洇 → 数」比例不变，只是节拍
 *    可调。这是 spec §演出口径「等比压下来」的机械化版本。
 * 3. **座位舞台**：`stage: { dst }`（**只有目标**——失去体力没有来源），矩形来自票 02 的
 *    `vitalFxBoxOf(heroBlockFor(seat))`。墨池 / 湿墨 / 牌脚水线都按 `dst` 算；落点是素材页的
 *    「牌脚下方 `card.h × 0.14`」（参考牌 644 高时是 89.32，与素材页同值）。量不到时退参考舞台。
 * 4. **`bare: true`**（牌桌硬口径）：不画宣纸底与样例武将牌；飘字的包围盒照 `fx-core.js` 的
 *    判据（不压体力条）。**注意**：素材页的墨用 `multiply`，在**透明覆盖层**上 `multiply`
 *    只对画布内已画的东西生效 ⇒ `bare` 下墨是实色、牌桌不透。牌桌档因此把墨的透明度整体
 *    乘一个 `BARE_INK_ALPHA`（见下），**最终色值以实机为准**：接真牌桌时按实机把
 *    `BARE_INK_ALPHA` 调低（或改成"先画一层极淡的牌色底再 multiply"），票面不预设数字。
 * 5. **样本牌的"化开"**（`bakeWet`）只在导出档：牌桌档的真实牌像素归游戏，不归本模块；
 *    牌桌档的"整张牌往下湿"由牌脚水线 + 落点墨气表达（见 `damp`）。
 *
 * **档位判据不在这里**（spec §档位）：`vital-fx.js:vitalFxVariantOf(amount)` 是唯一的一处，
 * 模块只认排播层交给它的 `variant`。素材包那个 `tierFor()` 不许作为第二份判据活下来——
 * 多一份判据就会漂（`-4` 播 3 档、飘字写真实点数，两件事各有各的真源）。
 *
 * 纯 Canvas 2D，零依赖、零外部资源、不发请求、不碰 DOM（只 `document.createElement("canvas")`
 * 建离屏贴图）。`"use strict";` 是文件第一条语句；全局名 `window.SgsHpLossFX`，不改成 ESM。
 */
(function (global) {

  var TAU = Math.PI * 2;

  /* ---------------------------------------------------------------- 配色 ----
   * 宣纸 / 墨取自 SGS 前端 style.css 的 :root；湿墨用近黑的暖灰、干墨更浅。 */
  var C = {
    paper:  "#f1ebdf",
    card:   "#fffdf7",
    cardLo: "#f8f1e2",
    line:   "#ddd8d0",
    ink:    "#1c1a17",
    dim:    "#6f6a62",
    red:    "#9c2b1b",
    wu:     "#1d6b3a",   /* 吴 · 势力印（与 style.css 的 .kingdom.wu 同色） */
    wet:    "#1a1815",
    dried:  "#6b6154"
  };

  var SERIF = '"Noto Serif SC","Songti SC","STSong","SimSun",serif';
  var SANS  = '"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif';

  /* ------------------------------------------------------ 参考舞台（导出档）---- */
  var REF_W = 1920, REF_H = 1080;
  var CARD = { w: 460, h: 644, pad: 74 };

  /* 单张牌：失去体力没有来源，不需要攻防两张（素材页的摆位）。 */
  var SCALE = 1.06;
  var REF_RECT = { cx: 900, cy: 466, w: CARD.w * SCALE, h: CARD.h * SCALE };

  /* 牌脚下方那口「墨池」的落点：由牌脚往下量（素材页 644 高的牌是 89.32）。
   * 重力与它一起决定坠落时长——颗数 / 档位都不改重力，牌越小落得越快（真实尺度）。 */
  var IMPACT_DROP = 89.32;
  var GRAV = 5200;

  /* **bare 档的墨色起点**（票面 §1 的注意）：透明覆盖层上 `multiply` 只对画布内已画的
   * 东西生效，素材页那套墨会变成不透明的实色块。这里给一个**唯一的**可调常量，把墨的
   * 整体透明度乘下去；**最终色值以实机为准**（接真牌桌时看牌面颜色再定，票面不预设）。
   * 只在牌桌档（bare）生效；导出档照素材页 1:1。 */
  var BARE_INK_ALPHA = 0.55;

  /* 构图下限：牌桌的武将块可能只有参考牌的三分之一宽，再小墨珠就看不见了（与
   * `fx-fire.js` 的 `COMPOSITION_FLOOR` 同一个理由）。墨珠另外有一个**显示下限**，
   * 见 `beadScale`。 */
  var COMPOSITION_FLOOR = 0.2;
  var BEAD_MIN = 0.4;
  var BEAD_MAX = 1.3;

  var HERO = {
    name: "黄盖", faction: "吴", factionColor: C.wu, hp: 4,
    skills: [
      ["苦肉", "出牌阶段限一次，你可以弃置一张牌，失去1点体力。"],
      ["诈降", "锁定技，当你失去1点体力后，你摸三张牌，本阶段你使用的红色【杀】不能被【闪】响应。"]
    ]
  };

  /* ------------------------------------------------------------------ 三档 ----
   * 素材页的三档时间表（**秒**，绝对时刻）。`spotAt` = 牌脚开始凝墨；`blotDur` = 一小片
   * 湿墨铺开的时间；`detachAt` = 第一颗墨珠断开的时刻（后面几颗各自 +`stagger`）；
   * `numAt` = 飘字浮起。**导出档逐字用它**；牌桌档只把四个锚点压到 `anim.ini` 的毫秒上。
   *
   * 档位的名字（一滴 / 成流 / 漏尽）只活在这行注释里——牌桌上没有地方读它。 */
  var TIERS = {
    1: {
      dmg: 1, name: "一滴",
      duration: 2.65,
      spots: 1, stagger: 0.12,
      spotAt: 0.06, blotDur: 0.42, detachAt: 0.60,
      bloomR: 112, bloomAlpha: 0.92,
      numAt: 0.86, numLife: 1.62, numSize: 180, numEcho: 0,
      numTo: { x: 952, y: 444 },
      wet: null, damp: 0, shake: 0
    },
    2: {
      dmg: 2, name: "成流",
      duration: 2.90,
      spots: 2, stagger: 0.12,
      spotAt: 0.05, blotDur: 0.42, detachAt: 0.56,
      bloomR: 168, bloomAlpha: 0.98,
      numAt: 0.92, numLife: 1.78, numSize: 208, numEcho: 0,
      numTo: { x: 948, y: 438 },
      wet: null, damp: 0.4, shake: 2
    },
    3: {
      dmg: 3, name: "漏尽",
      duration: 3.35,
      spots: 3, stagger: 0.08,
      spotAt: 0.04, blotDur: 0.42, detachAt: 0.52,
      bloomR: 232, bloomAlpha: 1.00,
      numAt: 0.90, numLife: 2.20, numSize: 240, numEcho: 1,
      numTo: { x: 944, y: 430 },
      wet: { at: 0.26, dur: 0.85 },
      damp: 1, shake: 3.0
    }
  };

  /* ------------------------------------------------------------ 小工具 ---- */
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ss(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function eOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function eBack(t) { t = clamp(t, 0, 1); var c = 2.0; return 1 + (c + 1) * Math.pow(t - 1, 3) + c * Math.pow(t - 1, 2); }

  /* 固定种子随机（mulberry32）：同一 seed 永远同一串数（画面只由秒数决定）。 */
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

  function offscreen(w, h) {
    var c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return [c, c.getContext("2d")];
  }

  function addRoundRect(c, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  function pathRound(c, x, y, w, h, r) {
    c.beginPath();
    addRoundRect(c, x, y, w, h, r);
  }

  /* 裁剪到圆角牌里（牌面上的墨是牌面上的东西，不是飘在牌边的一团灰）。 */
  function clipInside(c, rect, inset) {
    var p = inset || 0;
    pathRound(c, rect.x + p, rect.y + p, rect.w - p * 2, rect.h - p * 2, 14);
    c.clip();
  }

  /* 只画在**牌外**：素材页的墨池画在牌之前、被牌压住上面那截；牌桌档没有"本模块画的牌"，
   * 用 evenodd 裁掉牌矩形，得到同一条视觉（墨不会糊到真实牌面中间）。 */
  function clipOutsideCard(c, rect) {
    c.beginPath();
    c.rect(0, 0, W, H);
    addRoundRect(c, rect.x, rect.y, rect.w, rect.h, 14);
    c.clip("evenodd");
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
   * 双时间轴：export = 素材页，game = anim.ini
   * ============================================================== */

  /* `anim.ini` 的 `hp_loss_fx_<档>_<字段>` 的**内置默认**（磁盘副本在
   * `tools/sgs_web/static/anim.ini`；缺键 / 解析失败 / 非法值 ⇒ 退回这里）。
   *
   * 四个字段都是**从开演算的绝对毫秒**（不是间隔），钳制见 `gameTiming`：
   * `spot ≤ break ≤ num ≤ total ≤ MAX_TOTAL_MS`。缺省 = spec §游戏档缺省时长。
   *
   * 为什么失去体力压到 1.2~1.8s（素材页是 2.65~3.35s）：它**永不冻回放**（用户口径），
   * 与后续记录并行走，不能占住牌桌（spec §演出口径）。 */
  var TIMING_PREFIX = "hp_loss_fx_";
  var DEFAULT_TIMING = {
    hp_loss_fx_1_total: 1200, hp_loss_fx_1_spot: 80,
    hp_loss_fx_1_break: 320, hp_loss_fx_1_num: 560,
    hp_loss_fx_2_total: 1500, hp_loss_fx_2_spot: 80,
    hp_loss_fx_2_break: 360, hp_loss_fx_2_num: 640,
    hp_loss_fx_3_total: 1800, hp_loss_fx_3_spot: 80,
    hp_loss_fx_3_break: 380, hp_loss_fx_3_num: 720
  };

  /* 字段名名单由 `DEFAULT_TIMING` 的键反推（只许有一处真源；与雷 / 火同款）。 */
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
  /* 单档最长的硬上限：与剑光 / 雷电同一条理由——再长会挡住牌桌。失去体力不冻回放，
   * 所以它比火 / 雷的时停窗口短得多。 */
  var MAX_TOTAL_MS = 2200;

  /* 素材页的重力落点（参考牌 460×644、`SCALE = 1.06`）：`gameTiming` 的 timeline 在
   * 不知道舞台尺寸时的坠落时长；`create()` 会按真实座位矩形重算（见 `fallSeconds`）。 */
  var REF_FALL = fallSeconds(IMPACT_DROP * SCALE - 6);

  function fallSeconds(distance) {
    return Math.sqrt(Math.max(0.04, 2 * Math.max(0, distance) / GRAV));
  }

  /* 牌桌秒 ↔ 素材页秒（与 `fx-fire.js` 的 `warpAt` / `warpGameOf` 同一实现）：
   * 段内线性，段外按最后一段的斜率延拓。 */
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

  /* 锚点对按顺序收下；游戏侧不许倒挂（用户把 break 调成 0 时全靠这条兜住）。 */
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

  /* 一条**牌桌**时间轴上的 cue 时刻（秒）——画面与音效读同一份（`create()` 的返回里带走）。
   * 只放数字：`sfx-ui.js` 把整份时间轴逐键写进声道（数组 / 字符串在那里会被跳过）。
   *
   * 素材页的拍子全部落在锚点之间 ⇒ `warpGameOf` 把它们映射到牌桌秒；墨珠的逐颗错开
   * （`stagger`）与坠落时长用同一套换算，于是"第 i 颗断/落"在两边是同一个相对位置。 */
  function timelineFor(warp, base, fallDur) {
    function game(exportSeconds) {
      return warp ? warpGameOf(warp, exportSeconds) : exportSeconds;
    }
    var spots = base.spots;
    var lastDetach = base.detachAt + (spots - 1) * base.stagger;
    var firstImpact = base.detachAt + fallDur;
    var lastImpact = lastDetach + fallDur;
    return {
      DURATION: game(base.duration),
      SPOT: game(base.spotAt),
      BREAK: game(base.detachAt),
      GAP: spots > 1 ? (game(lastDetach) - game(base.detachAt)) / (spots - 1) : 0,
      IMPACT: game(firstImpact),
      IMPACT_LAST: game(lastImpact),
      IMPACT_GAP: spots > 1 ? (game(lastImpact) - game(firstImpact)) / (spots - 1) : 0,
      NUM: game(base.numAt),
      COUNT: spots
    };
  }

  /* 一条**空**时间轴（`total = 0`：用户把这一档关掉）。四个锚点重合，画 / 声音都只剩
   * 一层壳；显式返回，免得 `warpPairs` 收成一对之后 `duration` 退回素材页秒数。 */
  function zeroTimeline(base) {
    return {
      DURATION: 0, SPOT: 0, BREAK: 0, GAP: 0,
      IMPACT: 0, IMPACT_LAST: 0, IMPACT_GAP: 0, NUM: 0, COUNT: base.spots
    };
  }

  /* `game` 档：把 `anim.ini` 的四个字段解成一档**秒**时间轴 + 时间映射。
   *
   * 钳制（在模块内部，spec §游戏档缺省时长）：`spot ≤ break ≤ num ≤ total ≤ MAX_TOTAL_MS`。
   * 缺键 / 非法值退 `DEFAULT_TIMING`；**数字合法但越界**时按"抬到前一个锚点"钳（不是退
   * 默认）——用户把 break 调到比 spot 还小，意图是"一断开就落地"，不是"回到出厂值"。
   *
   * `numLife` 在牌桌档要撑到摘层（素材页那边是 1.6~2.2s，压到 1.2s 的窗口里会提前消失），
   * 其余档位参数（`numAt` / `detachAt` / `wet.at` …）**全部是素材页秒数**：`draw()` 每帧先把
   * 牌桌秒数映射回素材页再画，所以它们一个都不能换成牌桌秒数（换了就是两套钟）。 */
  function gameTiming(variantId, raw) {
    var id = clamp(Math.round(variantId || 1), 1, 3);
    var base = TIERS[id];
    function key(field) { return TIMING_PREFIX + id + "_" + field; }
    var total = Math.min(
      millis(raw, key("total"), DEFAULT_TIMING[key("total")]), MAX_TOTAL_MS
    );
    var spot = clamp(millis(raw, key("spot"), DEFAULT_TIMING[key("spot")]), 0, total);
    var brk = clamp(millis(raw, key("break"), DEFAULT_TIMING[key("break")]), spot, total);
    var num = clamp(millis(raw, key("num"), DEFAULT_TIMING[key("num")]), brk, total);
    var ms = { total: total, spot: spot, break: brk, num: num };
    var variant = copy(base, {
      numLife: Math.max(0.3, base.duration - base.numAt)
    });
    if (total <= 0) {
      return {
        id: id, profile: "game", timeline: zeroTimeline(base), warp: null, ms: ms, variant: variant
      };
    }
    var seconds = total / 1000;
    var pairs = warpPairs([
      { e: 0, g: 0 },
      { e: base.spotAt, g: spot / 1000 },
      { e: base.detachAt, g: brk / 1000 },
      { e: base.numAt, g: num / 1000 },
      { e: base.duration, g: seconds }
    ]);
    return {
      id: id,
      profile: "game",
      timeline: timelineFor(pairs, base, REF_FALL),
      warp: pairs,
      ms: ms,
      variant: variant
    };
  }

  /* `export` 档：参考实现的 2.65 / 2.90 / 3.35 秒链（`profile` 缺省走它）。 */
  function exportClock(variantId) {
    var id = clamp(Math.round(variantId || 1), 1, 3);
    var base = TIERS[id];
    return {
      id: id,
      profile: "export",
      timeline: timelineFor(null, base, REF_FALL),
      warp: null,
      variant: copy(base, {}),
      ms: null
    };
  }

  function clockFor(opts) {
    return opts && opts.profile === "game"
      ? gameTiming(opts.variant, opts.timing)
      : exportClock(opts && opts.variant);
  }

  /* ================================================================
   * 座位矩形 → 舞台（只有目标一块：失去体力没有来源）
   * ============================================================== */

  /* 座位矩形：缺字段 / 非法值退回参考舞台（覆盖层量不到布局时同理）。w / h 必须为正。 */
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

  /* 目标武将块 → 本实例的舞台。
   *
   * - `scale` = 它比参考牌 460×644 宽多少（墨片 / 水线 / 落点距离都按牌宽走）；
   * - `bead` = 墨珠的显示倍率（有个下限：小牌上的珠子也得看得见）；
   * - `k` = 相对参考舞台的比例（素材页的 `gw / DST.w`，字号用它）；
   * - 不传 `stage` ⇒ 参考舞台（`scale = 1.06`、`bead = 1`），素材库那条链逐字节不变。 */
  function stageFor(raw) {
    var hasStage = Boolean(raw && raw.dst);
    var dst = hasStage ? rectOr(raw.dst, REF_RECT) : REF_RECT;
    var scale = dst.w / CARD.w;
    if (!isFinite(scale) || scale <= 0) scale = SCALE;
    scale = Math.max(scale, COMPOSITION_FLOOR);
    var k = scale / SCALE;
    return {
      dst: dst,
      rect: { x: dst.cx - dst.w / 2, y: dst.cy - dst.h / 2, w: dst.w, h: dst.h },
      scale: scale,
      k: isFinite(k) && k > 0 ? k : 1,
      bead: clamp(scale, BEAD_MIN, BEAD_MAX),
      offstage: !hasStage
    };
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

  /* ---------------------------------------------------------- 帧上下文 ----
   * 每条演出在 `draw()` 开头切一次（同 `fx-fire.js` 的姿态）：同一条演出是同步画的，
   * 并发几条依次 draw、互不串味。`INK` 就是上面那条 bare 透明度的应用点。 */
  var W = REF_W, H = REF_H, INK = 1;

  function setFrame(w, h, bare) {
    W = w;
    H = h;
    INK = bare ? BARE_INK_ALPHA : 1;
  }

  /* ================================================================
   * 宣纸 / 武将牌 / 墨晕 / 飘字（一次性烘焙，跨实例复用）
   * ============================================================== */

  function paintPaper(S) {
    var w = REF_W + 160, h = REF_H + 160;
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

    /* 一圈极淡的枯笔圆相（与伤害 / 回复同一张纸的底子） */
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

    /* 几团宿墨的底子：偏冷、偏淡，纸看起来"放旧了" */
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

    /* 体力段（牌面写的是体力上限，失去体力不改它，故保持静态） */
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

  /* 墨晕贴图：一团边缘不规则、中间浓、边上化开的水墨（画在离屏画布上，用 multiply 贴上纸）。
   * 先画一块不规则的形状、整体虚化一层——墨落在纸上是有边的，只是边被水泡毛了；拿一堆软圆
   * 叠出来会糊成一团雾。这里的 `c.filter` 是**画布内的模糊**（墨边缘），不是 CSS 的层滤镜
   * （spec §硬约束禁的是给 `#table-shell` 挂 filter）。 */
  function bakeWash(seed, size) {
    var r = rng(seed);
    var cv = offscreen(size, size), c = cv[1];
    var cx = size / 2, cy = size / 2, R = size * 0.30;

    var tmp = offscreen(size, size), t = tmp[1];
    function blob(scale, alpha) {
      var N = 72;
      t.beginPath();
      for (var i = 0; i <= N; i++) {
        var a = i / N * TAU;
        var k = scale * (1
          + 0.15 * Math.sin(a * 3 + 0.7)
          + 0.09 * Math.sin(a * 5 + 1.9)
          + 0.05 * Math.sin(a * 9 + 0.4));
        k *= 0.985 + hash(seed + i * 3.7) * 0.03;
        var x = cx + Math.cos(a) * R * k;
        var y = cy + Math.sin(a) * R * k * 0.92;
        if (i === 0) t.moveTo(x, y); else t.lineTo(x, y);
      }
      t.closePath();
      t.fillStyle = "rgba(22,20,17," + alpha + ")";
      t.fill();
    }
    blob(1.00, 1);
    blob(0.78, 0.55);
    blob(0.54, 0.5);

    if (typeof c.filter === "string") {
      c.filter = "blur(" + (size * 0.045).toFixed(1) + "px)";
      c.drawImage(tmp[0], 0, 0);
      c.filter = "none";
    } else {
      c.drawImage(tmp[0], 0, 0);
    }

    var core = c.createRadialGradient(cx, cy, 0, cx, cy, R * 0.70);
    core.addColorStop(0, "rgba(14,13,11,0.34)");
    core.addColorStop(0.55, "rgba(14,13,11,0.16)");
    core.addColorStop(1, "rgba(14,13,11,0)");
    c.fillStyle = core;
    c.beginPath(); c.arc(cx, cy, R * 0.70, 0, TAU); c.fill();

    /* 里面的水痕：几道更深的细纹 */
    c.save();
    for (var k = 0; k < 8; k++) {
      var ka = r() * TAU, kr = R * (0.12 + r() * 0.52);
      c.strokeStyle = "rgba(12,11,10," + (0.04 + r() * 0.06) + ")";
      c.lineWidth = 1 + r() * 3.4;
      c.beginPath();
      c.arc(cx + Math.cos(ka) * kr * 0.24, cy + Math.sin(ka) * kr * 0.24,
        kr, ka, ka + 0.8 + r() * 1.7);
      c.stroke();
    }
    c.restore();

    /* 纸吃墨不匀：挖掉一些很淡的小孔，墨块里才有深浅 */
    c.save();
    c.globalCompositeOperation = "destination-out";
    for (var pi = 0; pi < 46; pi++) {
      var pa = r() * TAU, pr = R * (0.10 + r() * 0.96);
      var prr = size * (0.006 + r() * 0.030);
      var pg = c.createRadialGradient(cx + Math.cos(pa) * pr, cy + Math.sin(pa) * pr, 0,
        cx + Math.cos(pa) * pr, cy + Math.sin(pa) * pr, prr);
      pg.addColorStop(0, "rgba(0,0,0," + (0.05 + r() * 0.13) + ")");
      pg.addColorStop(1, "rgba(0,0,0,0)");
      c.fillStyle = pg;
      c.beginPath();
      c.arc(cx + Math.cos(pa) * pr, cy + Math.sin(pa) * pr, prr, 0, TAU);
      c.fill();
    }
    c.restore();

    /* 几道更深的墨脉 */
    c.save();
    for (var vi = 0; vi < 7; vi++) {
      var va = r() * TAU;
      var vr0 = R * (0.05 + r() * 0.30);
      var vr1 = vr0 + R * (0.25 + r() * 0.55);
      c.strokeStyle = "rgba(10,9,8," + (0.05 + r() * 0.07) + ")";
      c.lineWidth = 1.5 + r() * 5;
      c.lineCap = "round";
      c.beginPath();
      c.moveTo(cx + Math.cos(va) * vr0, cy + Math.sin(va) * vr0);
      c.quadraticCurveTo(
        cx + Math.cos(va + 0.5) * (vr0 + vr1) * 0.5, cy + Math.sin(va + 0.5) * (vr0 + vr1) * 0.5,
        cx + Math.cos(va + 0.2) * vr1, cy + Math.sin(va + 0.2) * vr1
      );
      c.stroke();
    }
    c.restore();

    /* 甩出去的墨点 */
    for (var d = 0; d < 16; d++) {
      var da = r() * TAU, dr = R * (0.86 + r() * 0.60);
      var ds = size * (0.003 + r() * 0.016);
      c.fillStyle = "rgba(18,16,14," + (0.08 + r() * 0.26) + ")";
      c.beginPath();
      c.ellipse(cx + Math.cos(da) * dr, cy + Math.sin(da) * dr, ds, ds * (0.5 + r() * 0.7), da, 0, TAU);
      c.fill();
    }
    return cv[0];
  }

  /* 飘字贴图：墨字 + 纸色柔光晕 + 飞白。不用描边（描边在浅色牌上像贴纸），改成先用 blur
   * 画一层纸色的光。数字本身照**真实点数**写（`−4` 就是 `−4`，不是档位号）。 */
  function bakeNumber(txt, px) {
    var S = 2;
    var font = "700 " + px + "px " + SERIF;
    var mc = bakeNumber._mc;
    if (!mc) { mc = bakeNumber._mc = document.createElement("canvas").getContext("2d"); }
    mc.font = font;

    /* 负号自己画一笔：字体里的 − 是一根又宽又长的横，和数字分得太开 */
    var body = txt;
    var hasMinus = /^[\u2212\-]/.test(txt);
    if (hasMinus) body = txt.slice(1);
    var bw = mc.measureText(body).width;
    var barW = px * 0.34, barH = px * 0.100, gap = px * 0.085;
    var tw = bw + (hasMinus ? barW + gap : 0);

    var padX = px * 0.26, padY = px * 0.34;
    var w = tw + padX * 2, h = px * 1.16 + padY * 2;
    var cv = offscreen(w * S, h * S), c = cv[1];
    c.scale(S, S);
    c.translate(w / 2, h / 2);
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.lineJoin = "round";
    c.font = font;

    var bodyCx = hasMinus ? (barW + gap) / 2 : 0;
    var barCx = hasMinus ? -(bw + gap) / 2 : 0;

    function paintNumber() {
      c.fillText(body, bodyCx, 0);
      if (hasMinus) {
        var y = -px * 0.015;
        c.beginPath();
        c.moveTo(barCx - barW / 2 + barH * 0.4, y - barH / 2);
        c.quadraticCurveTo(barCx - barW / 2, y, barCx - barW / 2 + barH * 0.4, y + barH / 2);
        c.lineTo(barCx + barW / 2 - barH * 0.4, y + barH / 2);
        c.quadraticCurveTo(barCx + barW / 2, y, barCx + barW / 2 - barH * 0.4, y - barH / 2);
        c.closePath();
        c.fill();
      }
    }

    /* 纸色柔光：把字从背后的墨晕里托出来，但看不出边 */
    var blur = px * 0.055;
    if (typeof c.filter === "string") {
      c.filter = "blur(" + blur.toFixed(1) + "px)";
      c.fillStyle = "rgba(244,239,229,0.95)";
      paintNumber();
      paintNumber();
      c.filter = "none";
    } else {
      c.strokeStyle = "rgba(244,239,229,0.8)";
      c.lineWidth = px * 0.05;
      c.strokeText(body, bodyCx, 0);
    }

    /* 字身：上褐墨、下近黑 */
    var g = c.createLinearGradient(0, -px * 0.56, 0, px * 0.56);
    g.addColorStop(0, "#4b453a");
    g.addColorStop(0.30, "#27231e");
    g.addColorStop(0.72, "#14120f");
    g.addColorStop(1, "#090807");
    c.fillStyle = g;
    paintNumber();

    /* 飞白：枯笔扫过字面，露出下面的纸（少而细，别像划痕） */
    c.globalCompositeOperation = "destination-out";
    var r = rng(8241 + Math.round(px));
    for (var i = 0; i < 6; i++) {
      var yy = (r() - 0.5) * px * 0.92;
      c.strokeStyle = "rgba(0,0,0," + (0.12 + r() * 0.22) + ")";
      c.lineWidth = px * (0.003 + r() * 0.006);
      c.beginPath();
      c.moveTo(-tw * 0.58, yy - px * 0.07);
      c.lineTo(tw * 0.58, yy + px * 0.09);
      c.stroke();
    }
    c.globalCompositeOperation = "source-over";

    return { img: cv[0], w: w, h: h };
  }

  /* 卡片「湿下去」的一层：牌脚糊成一片（三点档专用，**只导出档**）。
   * 牌桌档的真实牌像素归游戏：这条"化开"由牌脚水线与落点墨气表达（见 `draw()` 的 damp）。 */
  function bakeWet(heroImg, S) {
    var pad = CARD.pad;
    var cv = offscreen(CARD.w * S, CARD.h * S), c = cv[1];
    c.scale(S, S);

    if (typeof c.filter === "string") c.filter = "blur(2.5px)";
    c.drawImage(heroImg, -pad, -pad + 5, CARD.w + pad * 2, CARD.h + pad * 2);
    if (typeof c.filter === "string") c.filter = "none";

    c.fillStyle = "rgba(28,26,23,0.075)";
    c.fillRect(0, 0, CARD.w, CARD.h);

    c.globalCompositeOperation = "destination-in";
    var g = c.createLinearGradient(0, CARD.h * 0.70, 0, CARD.h * 1.00);
    g.addColorStop(0, "rgba(0,0,0,0)");
    g.addColorStop(0.45, "rgba(0,0,0,0.34)");
    g.addColorStop(1, "rgba(0,0,0,1)");
    c.fillStyle = g;
    c.fillRect(0, 0, CARD.w, CARD.h);
    c.globalCompositeOperation = "source-over";
    return cv[0];
  }

  var TEX = {};
  function texture(key, paint) {
    if (!TEX[key]) TEX[key] = paint();
    return TEX[key];
  }

  /* ================================================================
   * 墨：牌脚的湿墨 / 挂珠 / 坠落 / 墨池 / 落地 / 飘字
   * ============================================================== */

  /* 牌脚的一小片湿墨（牌面上，用 multiply 像真吸进去）。`INK` 在牌桌档把整套墨压淡。 */
  function drawBlot(c, x, y, rx, ry, a, seed) {
    if (a <= 0.005) return;
    c.save();
    c.globalCompositeOperation = "multiply";
    c.globalAlpha = INK;

    var g = c.createRadialGradient(x, y, 0, x, y, Math.max(rx, ry));
    g.addColorStop(0, "rgba(20,18,15," + (0.46 * a) + ")");
    g.addColorStop(0.45, "rgba(22,20,17," + (0.28 * a) + ")");
    g.addColorStop(0.78, "rgba(26,23,20," + (0.12 * a) + ")");
    g.addColorStop(1, "rgba(26,23,20,0)");
    c.fillStyle = g;
    c.beginPath();
    c.ellipse(x, y, rx, ry, 0, 0, TAU);
    c.fill();

    /* 边上几片不规则的小舌头，别让它是个正圆 */
    for (var i = 0; i < 3; i++) {
      var ang = 0.6 + i * 2.2 + seed;
      var dx = Math.cos(ang) * rx * 0.52;
      var dy = Math.sin(ang) * ry * 0.70;
      var rr = Math.min(rx, ry) * (0.42 + hash(seed + i) * 0.34);
      var g2 = c.createRadialGradient(x + dx, y + dy, 0, x + dx, y + dy, rr);
      g2.addColorStop(0, "rgba(22,20,17," + (0.30 * a) + ")");
      g2.addColorStop(1, "rgba(22,20,17,0)");
      c.fillStyle = g2;
      c.beginPath(); c.arc(x + dx, y + dy, rr, 0, TAU); c.fill();
    }
    c.restore();
  }

  /* 牌脚吃水的一小条：湿前沿是一条软的水线，不是一条直线。高度按档位（1 / 2 / 3）与牌宽走。 */
  function drawFootWet(c, rect, p, level, unit) {
    if (p <= 0.002) return;
    c.save();
    pathRound(c, rect.x, rect.y, rect.w, rect.h, 14);
    c.clip();
    c.globalCompositeOperation = "multiply";
    c.globalAlpha = INK;

    var baseY = rect.y + rect.h;
    var h = (level >= 3 ? 104 : level >= 2 ? 78 : 56) * unit;
    c.beginPath();
    c.moveTo(rect.x, baseY);
    for (var x = 0; x <= rect.w; x += 6) {
      var k = x / rect.w;
      var f = 0.54 + 0.16 * Math.sin(k * 5.3 + 1.2) + 0.09 * Math.sin(k * 13.7 + 0.3) + 0.05 * Math.sin(k * 29.1 + 2.1);
      c.lineTo(rect.x + x, baseY - h * f);
    }
    c.lineTo(rect.x + rect.w, baseY);
    c.closePath();

    /* 渐变从水线最高的地方开始是 0，裁出来的边才看不见 */
    var g = c.createLinearGradient(0, baseY - h * 0.86, 0, baseY);
    g.addColorStop(0, "rgba(28,26,23,0)");
    g.addColorStop(0.28, "rgba(28,26,23," + (0.035 * p) + ")");
    g.addColorStop(0.72, "rgba(28,26,23," + ((level >= 3 ? 0.13 : 0.10) * p) + ")");
    g.addColorStop(1, "rgba(28,26,23," + ((level >= 3 ? 0.30 : level >= 2 ? 0.24 : 0.19) * p) + ")");
    c.fillStyle = g;
    c.fill();

    /* 水线：湿纸边上那一条深一点的边，看得出"湿到哪儿了" */
    c.strokeStyle = "rgba(26,24,21," + (0.13 * p) + ")";
    c.lineWidth = Math.max(1, 1.1 * unit);
    c.stroke();
    c.restore();
  }

  /* 挂着还没掉的那颗墨珠（含拉丝、断前的细颈） */
  function drawHang(c, x, edgeY, r, neck) {
    var cy = edgeY + r * 1.02;
    c.save();
    c.globalAlpha = INK;

    var nw = Math.max(0.55, r * 0.62 * (1 - Math.pow(neck, 2.2)) + 0.4);
    var g0 = c.createLinearGradient(0, edgeY - 2, 0, cy);
    g0.addColorStop(0, "rgba(22,20,17,0.86)");
    g0.addColorStop(1, "rgba(14,13,11,0.95)");
    c.fillStyle = g0;
    c.beginPath();
    c.moveTo(x - nw, edgeY - 3);
    c.quadraticCurveTo(x - nw * 0.75, edgeY + (cy - edgeY) * 0.6, x, cy);
    c.quadraticCurveTo(x + nw * 0.75, edgeY + (cy - edgeY) * 0.6, x + nw, edgeY - 3);
    c.closePath();
    c.fill();

    /* 珠子本身（拉长一点，像要掉了） */
    var sy = 1 + 0.28 * neck;
    c.translate(x, cy);
    c.scale(1 / Math.sqrt(sy), sy);
    var g = c.createRadialGradient(-r * 0.30, -r * 0.34, 0, 0, 0, r * 1.06);
    g.addColorStop(0, "rgba(92,84,70,1)");
    g.addColorStop(0.45, "rgba(28,25,21,1)");
    g.addColorStop(1, "rgba(10,9,8,1)");
    c.fillStyle = g;
    c.beginPath(); c.arc(0, 0, r, 0, TAU); c.fill();
    c.fillStyle = "rgba(255,253,247,0.42)";
    c.beginPath(); c.ellipse(-r * 0.32, -r * 0.34, r * 0.24, r * 0.30, -0.3, 0, TAU); c.fill();
    c.restore();
  }

  /* 坠落的墨珠 */
  function drawFalling(c, x, y, vy, r) {
    var stretch = 1 + clamp(vy / 900, 0, 0.5);
    c.save();
    c.globalAlpha = INK;
    c.translate(x, y);
    c.scale(1 / Math.sqrt(stretch), stretch);
    var g = c.createRadialGradient(-r * 0.32, -r * 0.36, 0, 0, 0, r * 1.12);
    g.addColorStop(0, "rgba(82,75,64,1)");
    g.addColorStop(0.42, "rgba(28,25,21,1)");
    g.addColorStop(1, "rgba(10,9,8,1)");
    c.fillStyle = g;
    c.beginPath();
    c.moveTo(0, -r * 1.85);
    c.quadraticCurveTo(r * 1.00, -r * 0.18, r * 0.94, r * 0.32);
    c.arc(0, r * 0.32, r * 0.94, 0, Math.PI);
    c.quadraticCurveTo(-r * 1.00, -r * 0.18, 0, -r * 1.85);
    c.closePath();
    c.fill();
    c.fillStyle = "rgba(255,253,247,0.36)";
    c.beginPath(); c.ellipse(-r * 0.32, -r * 0.06, r * 0.24, r * 0.32, -0.3, 0, TAU); c.fill();
    c.restore();
  }

  /* 纸上的墨池：最外一圈水 → 洇开的一层 → 落点浓墨 → 副晕 → 溅点 → 三道伸出 → 落地墨气 */
  function drawPool(c, t, V, washes, poolX, impactY) {
    var age = t - V.impactAt;
    if (age <= 0) return;

    var grow = eOut(clamp(age / 0.70, 0, 1));
    var dry = 1 - 0.24 * ss(1.05, V.duration - V.impactAt, age);
    var a0 = V.bloomAlpha * grow * dry;

    var R = V.bloomR;
    /* 贴图里"实心"的半径约是画出来半宽的 0.62 倍，所以画的时候要放大 */
    var RX = R * 1.50, RY = RX * 0.50;
    var spread = 0.66 + 0.34 * grow;

    c.save();
    c.globalCompositeOperation = "multiply";
    c.globalAlpha = INK * a0 * 0.12;

    /* 最外一圈水：只是一层很淡的灰 */
    c.save();
    c.translate(poolX - RX * 0.12, impactY + RY * 0.10);
    c.rotate(-0.30);
    c.drawImage(washes[2].img,
      -RX * 1.55 * spread, -RY * 1.75 * spread,
      RX * 3.1 * spread, RY * 3.5 * spread);
    c.restore();

    /* 洇开的一层：纸吃水之后化开的那圈 */
    c.globalAlpha = INK * a0 * 0.30;
    c.save();
    c.translate(poolX, impactY);
    c.rotate(0.05);
    c.drawImage(washes[0].img,
      -RX * spread, -RY * spread, RX * 2 * spread, RY * 2 * spread);
    c.restore();

    /* 落点那团浓墨：几片叠在一起，边不整齐，才不像一块石头 */
    var coreR = R * 0.40 * (0.74 + 0.26 * grow);
    for (var ci = 0; ci < 3; ci++) {
      var cang = ci * 2.2 + 0.6;
      var cdx = Math.cos(cang) * coreR * 0.22;
      var cdy = Math.sin(cang) * coreR * 0.16;
      var cr = coreR * (0.66 + hash(ci + 3.1) * 0.34);
      var cg = c.createRadialGradient(poolX + cdx, impactY + cdy, 0,
        poolX + cdx, impactY + cdy, cr);
      cg.addColorStop(0, "rgba(10,9,8," + (0.42 * a0) + ")");
      cg.addColorStop(0.45, "rgba(14,12,11," + (0.20 * a0) + ")");
      cg.addColorStop(1, "rgba(18,16,14,0)");
      c.fillStyle = cg;
      c.beginPath();
      c.ellipse(poolX + cdx, impactY + cdy, cr, cr * 0.72, cang, 0, TAU);
      c.fill();
    }

    /* 副晕：偏到右下方，转个角度，边才不像一个椭圆 */
    c.globalAlpha = INK * a0 * 0.22;
    c.save();
    c.translate(poolX + RX * 0.32 * spread, impactY + RY * 0.14 * spread);
    c.rotate(2.1);
    c.drawImage(washes[1].img,
      -RX * 0.60 * spread, -RY * 0.80 * spread,
      RX * 1.20 * spread, RY * 1.60 * spread);
    c.restore();

    /* 溅在墨池边上的墨点：软边的小圆，别用大贴图缩，会缩成黑渣 */
    for (var di = 0; di < V.blots.length; di++) {
      var bl = V.blots[di];
      var ba = ss(0.04 + bl.delay, 0.26 + bl.delay, age) *
               (1 - 0.20 * ss(1.2, V.duration - V.impactAt, age));
      if (ba <= 0.01) continue;
      var bx = poolX + bl.dx * (0.75 + 0.25 * grow);
      var by = impactY + bl.dy * (0.75 + 0.25 * grow);
      var br = bl.r, bsy = bl.squash;
      c.save();
      c.translate(bx, by);
      c.rotate(bl.rot);
      c.scale(1, bsy);
      var bg = c.createRadialGradient(0, 0, 0, 0, 0, br);
      bg.addColorStop(0, "rgba(14,12,10," + (0.62 * ba) + ")");
      bg.addColorStop(0.55, "rgba(18,16,13," + (0.38 * ba) + ")");
      bg.addColorStop(0.85, "rgba(20,18,15," + (0.08 * ba) + ")");
      bg.addColorStop(1, "rgba(20,18,15,0)");
      c.fillStyle = bg;
      c.beginPath(); c.arc(0, 0, br, 0, TAU); c.fill();
      c.restore();
    }

    /* 溅开时伸出去的三道：软的长椭圆，不能是尖的 */
    if (V.dmg >= 2) {
      var fg2 = eOut(clamp((age - 0.10) / 0.75, 0, 1));
      for (var fi = 0; fi < 3; fi++) {
        var fang = [-0.50, 0.20, 0.60][fi];
        var flen = R * (0.72 + fi * 0.14) * fg2;
        var fcx = poolX + Math.cos(fang) * flen * 0.50;
        var fcy = impactY + Math.sin(fang) * flen * 0.32;
        var frx = flen * 0.58, fry = R * 0.20;
        c.globalAlpha = INK * a0 * (fi === 1 ? 0.30 : 0.18);
        c.save();
        c.translate(fcx, fcy);
        c.rotate(fang);
        c.scale(1, fry / frx);
        var fg3 = c.createRadialGradient(0, 0, 0, 0, 0, frx);
        fg3.addColorStop(0, "rgba(18,16,13,0.9)");
        fg3.addColorStop(0.5, "rgba(20,18,15,0.5)");
        fg3.addColorStop(1, "rgba(20,18,15,0)");
        c.fillStyle = fg3;
        c.beginPath(); c.arc(0, 0, frx, 0, TAU); c.fill();
        c.restore();
      }
    }

    /* 落地那一下：一口墨气，不是闪光（渐变半径 R×3 之外是透明的，不会整屏压暗；
     * alpha 仍按 `a0` 收，牌桌档再乘 `INK`——它是墨的一部分，不是独立的闪）。 */
    var fa = age > 0 ? 0.20 * Math.exp(-age * 26) * (1 - Math.exp(-age * 110)) : 0;
    if (fa > 0.002) {
      c.globalAlpha = INK * a0 * 0.18;
      var fg = c.createRadialGradient(poolX, impactY, 0, poolX, impactY, R * 3.0);
      fg.addColorStop(0, "rgba(18,16,13," + fa + ")");
      fg.addColorStop(0.45, "rgba(18,16,13," + (fa * 0.55) + ")");
      fg.addColorStop(1, "rgba(18,16,13,0)");
      c.fillStyle = fg;
      c.fillRect(0, 0, W, H);
    }

    c.restore();
  }

  /* 砸下去那一瞬间：一小圈水纹 + 几颗墨点 */
  function drawSplash(c, t, V, drops, poolX, impactY) {
    var age = t - V.impactAt;
    if (age < 0 || age > 0.52) return;
    var fade = 1 - ss(0.14, 0.52, age);

    c.save();
    c.globalCompositeOperation = "multiply";
    c.globalAlpha = INK;

    var ring = (1 - Math.pow(1 - clamp(age / 0.40, 0, 1), 2)) * V.bloomR * 0.30;
    c.strokeStyle = "rgba(26,24,21," + (0.13 * fade) + ")";
    c.lineWidth = 1.4;
    c.beginPath();
    c.ellipse(poolX, impactY, ring, ring * 0.40, 0, 0, TAU);
    c.stroke();

    for (var i = 0; i < drops.length; i++) {
      var d = drops[i];
      var a = age - d.delay;
      if (a <= 0 || a > d.life) continue;
      var x = poolX + d.vx * a;
      var y = impactY + d.vy * a + 0.5 * 2600 * a * a;
      if (y > impactY + V.bloomR * 0.46) continue;
      c.fillStyle = "rgba(16,15,13," + (0.55 * (1 - a / d.life)) + ")";
      c.beginPath();
      c.arc(x, y, d.r * (1 - a / d.life * 0.4), 0, TAU);
      c.fill();
    }
    c.restore();
  }

  /* ================================================================
   * 飘字（位置由 `create()` 算好：牌桌档按体力条下沿反推）
   * ============================================================== */
  function drawNumber(c, t, V, sprite, poolX, impactY) {
    var age = t - V.numAt;
    if (age < 0 || age > V.numLife) return;

    var rise = eOut(clamp(age / 0.78, 0, 1));
    var pop = 0.30 + 0.70 * eBack(clamp(age / 0.44, 0, 1));
    var alpha = 1 - ss(V.numLife - 0.68, V.numLife, age);
    if (alpha <= 0.01) return;

    var x = lerp(poolX, V.numTo.x, rise);
    var y = lerp(impactY - 30 * V.unit, V.numTo.y, rise)
      - V.numDrift * eOut(age / V.numLife);
    var size = V.numSize;

    c.save();
    c.translate(x, y);
    c.rotate(-0.030 + (1 - rise) * 0.05);
    c.scale(pop, pop);
    c.globalAlpha = 1;

    /* 残影（三点档） */
    if (V.numEcho > 0) {
      for (var e = V.numEcho; e >= 1; e--) {
        c.save();
        c.globalAlpha = alpha * 0.12;
        c.drawImage(sprite.img, -sprite.w / 2 + e * 9, -sprite.h / 2 - e * 12,
          sprite.w, sprite.h);
        c.restore();
      }
    }

    c.globalAlpha = alpha;
    c.drawImage(sprite.img, -sprite.w / 2, -sprite.h / 2, sprite.w, sprite.h);

    /* 小字「失去体力」：随字一起浮起来 */
    var ca = ss(0.24, 0.46, age) * alpha;
    if (ca > 0.01) {
      c.globalAlpha = ca;
      var fs = Math.round(size * 0.150);
      c.font = "600 " + fs + "px " + SERIF;
      if ("letterSpacing" in c) c.letterSpacing = (size * 0.085) + "px";
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.lineJoin = "round";
      c.strokeStyle = "rgba(249,245,235,0.90)";
      c.lineWidth = Math.max(1, size * 0.020);
      c.strokeText(V.label, 0, size * 0.575);
      c.fillStyle = "#454034";
      c.fillText(V.label, 0, size * 0.575);
      if ("letterSpacing" in c) c.letterSpacing = "0px";
    }
    c.restore();
  }

  /* ================================================================
   * 飘字口径（与 `fx-core.js` / `fx-fire.js` 同一套包围盒判据）
   * ============================================================== */
  var GLYPH_RATIO = 0.7;
  var NUMBER_MAX_RATIO = 0.35;
  var NUMBER_KEEP_OUT_FALLBACK = 0.45;
  var NUMBER_GAP_RATIO = 0.03;
  var SCREEN_MIN_NUMBER = 44;

  /* ================================================================
   * 入口（与 `SgsFireFX.create` 同形：排播层不认第二种形状）
   * ============================================================== */
  function create(canvas, opts) {
    opts = opts || {};
    var clock = clockFor(opts);
    var id = clock.id;
    var base = TIERS[id];
    var bare = opts.bare === true;
    var size = viewSize(opts.view);
    var instW = (bare || opts.view) ? size.w : REF_W;
    var instH = (bare || opts.view) ? size.h : REF_H;
    var dpr = Number(opts.dpr) > 0 ? Number(opts.dpr) : 1;
    /* 本实例的总时长（秒）：牌桌档 = `anim.ini` 的 `total`（到点摘层）；导出档 = 素材页。 */
    var totalSeconds = clock.profile === "game"
      ? (clock.ms ? clock.ms.total / 1000 : clock.timeline.DURATION)
      : base.duration;
    /* 先切帧上下文：下面的贴图 / 度量读 W / H（`clipOutsideCard` 与墨池的整屏墨气）与 INK。 */
    setFrame(instW, instH, bare);

    var stage = stageFor(opts.stage);
    var cr = stage.rect;
    var scale = stage.scale;
    var unit = clamp(scale, BEAD_MIN, BEAD_MAX);
    var bottom = cr.y + cr.h;

    /* 落点：牌脚下方 `card.h × 0.14`（素材页的 89.32 @ 644 高），随牌宽一起缩。
     * 坠落时长由同一段重力算出来——牌桌档的牌比参考牌小，落得更快（画面里看到的就是这样）。 */
    var impactY = bottom + IMPACT_DROP * scale;
    var fallDist = impactY - (bottom + 6);
    var fallDur = fallSeconds(fallDist);
    var impactAt = base.detachAt + fallDur;

    /* 每实例一份可变的档位对象：几何（珠位 / 池宽 / 字号 / 飘字落点）随座位走，
     * 别污染 `TIERS`（同一条家族的几条演出可以落在不同大小的牌上）。 */
    var V = copy(clock.variant, {});
    V.label = "失去体力";
    /* `draw()` 里画的是**素材页秒数**（牌桌档每帧先 warp 回来），故 `duration` 必须一直是
     * 素材页那个数（2.65 / 2.90 / 3.35）——换成牌桌秒数就是两套钟。 */
    V.duration = base.duration;
    V.impactAt = impactAt;
    V.unit = unit;
    V.bloomR = base.bloomR * scale;
    var amount = Math.round(Number(opts.amount));
    V.amount = isFinite(amount) && amount > 0 ? amount : base.dmg;

    /* 牌脚凝墨的位置：按牌宽取比例，避开左右圆角（素材页的三个比例）。 */
    var fracs = [[0.585], [0.545, 0.692], [0.520, 0.628, 0.716]][base.spots - 1];
    var spots = [];
    for (var i = 0; i < base.spots; i++) {
      spots.push({
        x: cr.x + cr.w * fracs[i],
        t0: base.spotAt + i * base.stagger * 0.7,
        detach: base.detachAt + i * base.stagger,
        r: (8.2 + hash(i * 5.1 + id) * 3.0) * (1 - i * 0.14) * unit,
        seed: hash(71 + i * 13.3) * 10
      });
    }
    var poolX = (spots[0].x + spots[spots.length - 1].x) / 2 + 8 * unit;

    /* 飘字：字号照素材页按牌宽封顶 / 保底（`fx-core.js` 的口径）。 */
    var numK = clamp(cr.w / REF_RECT.w, 0.55, 1.2);
    if (numK !== 1) V.numSize = Math.max(96, Math.round(base.numSize * numK));
    V.numDrift = bare ? 0 : 14;
    var numBox = { top: 0, bottom: 0, height: 0 };
    var keepOutPx = null;

    if (bare) {
      /* 牌桌档：飘字不许压体力条——硬线 = `.hp-bar` 的下沿（`opts.numberKeepOut`，相对牌顶）。
       * 最终落点由这条线反推（与 `fx-fire.js` / `fx-core.js` 同一套包围盒判据）。 */
      var cardH = cr.h;
      var glyph = clamp(
        V.numSize * GLYPH_RATIO, SCREEN_MIN_NUMBER, NUMBER_MAX_RATIO * cardH
      );
      V.numSize = glyph / GLYPH_RATIO;
      var keepOut = Number(opts.numberKeepOut);
      var keepOutRatio = isFinite(keepOut) && keepOut > 0 && keepOut < cardH
        ? keepOut / cardH
        : NUMBER_KEEP_OUT_FALLBACK;
      keepOutPx = keepOutRatio * cardH;
      /* 包围盒顶边 = 体力条下沿 + 3% 牌高（余量留给字身与飞白）。 */
      var boxTopRatio = Math.min(0.88, keepOutRatio + NUMBER_GAP_RATIO);
      var centerRatio = boxTopRatio + glyph / (2 * cardH);
      V.numTo = {
        x: cr.x + cr.w * 0.5 + 4 * scale,
        y: cr.y + cardH * centerRatio
      };
      var centerY = V.numTo.y - V.numDrift;
      numBox = { top: centerY - glyph / 2 - cr.y, bottom: centerY + glyph / 2 - cr.y, height: glyph };
    } else {
      /* 导出档：沿用素材页的落点（按参考舞台的牌矩形等比映射）。 */
      var refX = REF_RECT.cx - REF_RECT.w / 2;
      var refY = REF_RECT.cy - REF_RECT.h / 2;
      V.numTo = {
        x: cr.x + cr.w * ((base.numTo.x - refX) / REF_RECT.w),
        y: cr.y + cr.h * ((base.numTo.y - refY) / REF_RECT.h)
      };
      var glyphSize = V.numSize * GLYPH_RATIO;
      numBox = {
        top: V.numTo.y - glyphSize / 2 - cr.y,
        bottom: V.numTo.y + glyphSize / 2 - cr.y,
        height: glyphSize
      };
      keepOutPx = null;
    }

    /* 墨池边上溅开的墨点（固定种子）：档位越高越多。 */
    var blots = [];
    var rb = rng(771 + id * 97);
    for (var bi = 0; bi < (base.dmg >= 3 ? 20 : base.dmg >= 2 ? 14 : 9); bi++) {
      var ang = rb() * TAU;
      var dist = 0.55 + rb() * 0.95;
      blots.push({
        dx: Math.cos(ang) * V.bloomR * 1.15 * dist,
        dy: Math.sin(ang) * V.bloomR * 0.62 * dist,
        r: (5 + rb() * 17) * (base.dmg >= 3 ? 1.15 : 1) * unit,
        squash: 0.55 + rb() * 0.6,
        rot: rb() * TAU,
        delay: rb() * 0.22
      });
    }
    V.blots = blots;

    /* 落地溅起来的墨点 */
    var drops = [];
    var r = rng(4400 + id * 811);
    for (var d = 0; d < (base.dmg >= 3 ? 12 : base.dmg >= 2 ? 8 : 5); d++) {
      drops.push({
        vx: (r() - 0.5) * 400 * unit,
        vy: (-210 - r() * 380) * unit,
        r: (1.2 + r() * 2.4) * unit,
        delay: r() * 0.06,
        life: 0.20 + r() * 0.18
      });
    }

    canvas.width = Math.round(instW * dpr);
    canvas.height = Math.round(instH * dpr);
    var c = canvas.getContext("2d");

    /* 三张静态图（宣纸底 / 样例武将牌 / 湿牌）只在导出档用；牌桌档 `bare` 一概不画。
     * 它们只读，跨实例复用（每次失去体力都重画 1920×1080 的纸底是主线卡顿）。 */
    var paper = bare ? null
      : texture("hpLossPaper@" + dpr, function () { return paintPaper(dpr); });
    var heroImg = bare ? null
      : texture("hpLossHero@" + dpr, function () { return paintHero(HERO, dpr); });
    var wetImg = (!bare && base.wet && heroImg) ? bakeWet(heroImg, dpr) : null;
    var washes = [
      { img: texture("hpLossWash9101", function () { return bakeWash(9101, 512); }) },
      { img: texture("hpLossWash9227", function () { return bakeWash(9227, 512); }) },
      { img: texture("hpLossWash9371", function () { return bakeWash(9371, 512); }) }
    ];
    var sprite = bakeNumber("\u2212" + V.amount, Math.max(24, Math.round(V.numSize)));

    /* 本实例的时间轴：坠落时长按**真实座位矩形**重算 ⇒ 声音的落地那一拍跟画面同刻。 */
    var timeline = timelineFor(clock.warp, base, fallDur);

    function draw(t) {
      t = clamp(t, 0, totalSeconds);
      // 牌桌档：先把演出自己的秒数映射回素材页的秒数（整条链按锚点压，画法一行不改）。
      if (clock.warp) t = warpAt(clock.warp, t);
      setFrame(instW, instH, bare);

      var shake = 0;
      if (base.shake) {
        var sa = t - impactAt;
        if (sa > 0) shake = Math.sin(sa * 74) * base.shake * Math.exp(-sa * 17) * (1 - Math.exp(-sa * 60));
      }

      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, instW, instH);
      c.save();
      c.translate(shake, 0);

      if (!bare) c.drawImage(paper, -80, -80, instW + 160, instH + 160);

      /* 纸上的墨池在牌之前画：牌脚压住的那一块本来就被牌挡住。牌桌档没有本模块画的牌，
       * 用 evenodd 把牌矩形挖掉，得到同一条"墨在牌后面"的视觉。 */
      c.save();
      if (bare) clipOutsideCard(c, cr);
      drawPool(c, t, V, washes, poolX, impactY);
      c.restore();

      /* 牌：起手往下沉一点（失去体力是"泄"不是"打"，没有弹起）。牌桌档的真实牌不归本模块
       * 移动（会与游戏自己的布局打架），故不沉。 */
      var settle = bare ? 0
        : 2.4 * ss(0.02, 0.34, t) + (V.wet ? 1.8 * ss(V.wet.at, V.wet.at + 1.2, t) : 0);
      var cardRect = { x: cr.x, y: cr.y + settle, w: cr.w, h: cr.h };
      if (!bare && heroImg) {
        c.save();
        c.translate(cr.x + cr.w / 2, cr.y + cr.h / 2 + settle);
        c.drawImage(heroImg,
          -cr.w / 2 - CARD.pad * scale, -cr.h / 2 - CARD.pad * scale,
          cr.w + CARD.pad * 2 * scale, cr.h + CARD.pad * 2 * scale);
        c.restore();

        /* 三点档：牌脚吃水，笔画化开、颜色沉下去（裁在圆角牌里） */
        if (wetImg && V.wet) {
          var wp = ss(V.wet.at, V.wet.at + V.wet.dur, t);
          if (wp > 0.001) {
            c.save();
            c.globalCompositeOperation = "multiply";
            c.globalAlpha = 0.72 * wp;
            pathRound(c, cardRect.x, cardRect.y, cardRect.w, cardRect.h, 14);
            c.clip();
            c.drawImage(wetImg, cr.x, cr.y + settle + (1 - wp) * 22, cr.w, cr.h);
            c.restore();
          }
        }
      }

      /* 牌脚开始吃水：这条水线最先出来，比墨片早 */
      drawFootWet(c, cardRect, ss(base.spotAt, base.spotAt + 0.55, t), base.dmg, scale);

      /* 牌脚：凝墨 → 挂珠 → 断开 */
      var edgeY = bottom + settle;
      for (var k = 0; k < spots.length; k++) {
        var sp = spots[k];
        if (t < sp.t0) continue;

        /* 湿墨一点点铺开（裁在牌里：它是牌面上的墨，不是飘在牌边的一团灰） */
        var bp = ss(sp.t0, sp.t0 + base.blotDur, t);
        var rx = (12 + 26 * bp) * scale * (0.86 + hash(sp.seed) * 0.26) * (1 - k * 0.16);
        var ry = (6 + 12 * bp) * scale * (1 - k * 0.14);
        c.save();
        clipInside(c, cardRect, 0);
        drawBlot(c, sp.x, edgeY - ry * 1.15, rx, ry * 1.25, bp, sp.seed);
        c.restore();

        /* 从墨片往下淌的一小道，把墨片和珠子连起来 */
        var tg = c.createLinearGradient(0, edgeY - ry * 0.6, 0, edgeY + 3);
        tg.addColorStop(0, "rgba(22,20,17,0)");
        tg.addColorStop(0.45, "rgba(20,18,15," + (0.26 * bp) + ")");
        tg.addColorStop(1, "rgba(16,14,12," + (0.50 * bp) + ")");
        c.save();
        c.globalCompositeOperation = "multiply";
        c.globalAlpha = INK;
        c.fillStyle = tg;
        c.beginPath();
        c.moveTo(sp.x - rx * 0.24, edgeY - ry * 0.7);
        c.quadraticCurveTo(sp.x - rx * 0.12, edgeY - 1, sp.x - rx * 0.10, edgeY + 3);
        c.lineTo(sp.x + rx * 0.10, edgeY + 3);
        c.quadraticCurveTo(sp.x + rx * 0.12, edgeY - 1, sp.x + rx * 0.24, edgeY - ry * 0.7);
        c.closePath();
        c.fill();
        c.restore();

        /* 珠：凝到断开之前挂在牌沿上 */
        var hangStart = sp.detach - 0.20;
        if (t >= hangStart && t < sp.detach) {
          var hq = clamp((t - hangStart) / 0.20, 0, 1);
          drawHang(c, sp.x, edgeY, sp.r * (0.45 + 0.55 * eOut(hq)), hq);
        }

        /* 掉下去 */
        if (t >= sp.detach) {
          var ft = t - sp.detach;
          var yFall = edgeY + sp.r * 1.02 + 0.5 * GRAV * ft * ft;
          if (yFall < impactY - 2) drawFalling(c, sp.x, yFall, GRAV * ft, sp.r);
        }
      }

      drawSplash(c, t, V, drops, poolX, impactY);
      drawNumber(c, t, V, sprite, poolX, impactY);

      /* 三点档：整张牌被水浸的那点灰（素材页是"纸面压灰、从底边漫上来"；牌桌档只压牌周围
       * 一小片——整屏压灰会盖住牌桌，spec §硬约束只允许画布内的局部墨气）。 */
      if (base.damp) {
        var da = base.damp * (ss(impactAt, impactAt + 0.7, t) * 0.070 -
                              ss(impactAt + 0.9, base.duration, t) * 0.026);
        if (da > 0.001) {
          var rad = Math.max(cr.w * 1.7, V.bloomR * 2.4);
          var dg = c.createRadialGradient(poolX, impactY + cr.h * 0.2, 0, poolX, impactY + cr.h * 0.2, rad);
          dg.addColorStop(0, "rgba(58,55,48," + da + ")");
          dg.addColorStop(0.55, "rgba(58,55,48," + (da * 0.62) + ")");
          dg.addColorStop(1, "rgba(58,55,48,0)");
          c.save();
          c.globalCompositeOperation = "multiply";
          c.globalAlpha = INK;
          c.fillStyle = dg;
          c.fillRect(0, 0, W, H);
          c.restore();
        }
      }

      c.restore();
    }

    return {
      draw: draw,
      duration: totalSeconds,
      variant: id,
      amount: V.amount,
      width: instW,
      height: instH,
      profile: clock.profile,
      timeline: timeline,
      timing: clock.ms,
      stage: stage,
      bare: bare,
      metrics: {
        u: scale,
        keepOut: keepOutPx,
        number: numBox.height,
        numberBox: numBox,
        card: { w: cr.w, h: cr.h },
        inkAlpha: INK,
        fall: fallDur,
        /* 飘字贴图的完整文本（负号是**画**出来的一根短横，`fillText` 只写数字部分——
         * 探针 / 实机读数用它判"写的是真实点数"）。 */
        numberText: "\u2212" + V.amount
      }
    };
  }

  global.SgsHpLossFX = {
    create: create,
    TIERS: TIERS,
    COLORS: C,
    // 与 `SgsDamageFX` / `SgsThunderFX` / `SgsFireFX` 同名的导出面（排播层按字段表 / 时长取）。
    DEFAULT_TIMING: DEFAULT_TIMING,
    MAX_TOTAL_MS: MAX_TOTAL_MS,
    TIMING_PREFIX: TIMING_PREFIX,
    TIMING_FIELDS: TIMING_FIELDS,
    TIMING_KEYS: TIMING_KEYS,
    gameTiming: gameTiming,
    SCREEN_MIN: { number: SCREEN_MIN_NUMBER },
    COMPOSITION_FLOOR: COMPOSITION_FLOOR,
    BARE_INK_ALPHA: BARE_INK_ALPHA
  };
})(window);
