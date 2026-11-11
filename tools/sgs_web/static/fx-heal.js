"use strict";

/* SGS 前端 · **回复体力**演出的画面（vital-fx 票 04）：`SgsHealFX`。
 *
 * 移植自素材包 `outputs/sgs-heal-fx/fx-heal.js`（可播放的定稿参考实现）：一张武将牌自己回血
 * ——牌沿先浮起一层很淡的**石绿**，十几枚小十字贴着牌面往上飘；回复越多，绿越厚、十字越密。
 * 到 **3 点以上**换一套演法：绿色粒子从四面八方汇聚到牌心，牌面同时越来越亮，直到**白到把
 * 牌名与技能字全部盖住**；**白住**一下，再从牌心**迸发一圈环形绿光**，波前骑着一圈十字向外
 * 推，最后弹出**青绿飘字**（`+N`）。
 *
 * 搬进牌桌时只改五类事，画法逐段保留（与 `fx-hp-loss.js` / `fx-fire.js` 同一套姿态）：
 *
 * 1. **双时间轴**：`profile: "export"` 照抄素材页的 3.20 / 3.55 / 4.45 秒链（`?still` /
 *    `?export` / node 探针逐帧导静帧）；`profile: "game"` 读 `anim.ini` 的
 *    `heal_fx_<档>_<字段>`（缺键退 `DEFAULT_TIMING`，钳制在 `gameTiming` 里）。
 *    `TIMING_FIELDS` / `TIMING_KEYS` 由 `DEFAULT_TIMING` 的键**生成**——字段名只有一处。
 * 2. **字段集按档分**：`total` / `num` 全档都有；`gather`（聚气**结束**的时刻）/ `hold`
 *    （白住**时长**）/ `burst`（迸发**时刻**）**只有 3+ 档**（与 `fx-thunder.js` 里
 *    `strobe` / `flash` 只 3 / 4 档同姿态：1 / 2 档不硬造这三个键）。
 * 3. **牌桌档是「按锚点重排」，不是「另画一套」**：素材页的拍子
 *    （`gather.at` / 聚气结束 / `burst.at` / `numAt` / `duration`）按**锚点**各自对到
 *    `anim.ini` 的 `gather` / `burst` / `num` / `total` 上（`gameTiming` 返回 `warp`），
 *    `draw(t)` 先把演出自己的秒数映射回素材页的秒数再画——段内线性，于是**每段内部**的
 *    相对比例与素材页一致；**档级**比例由 ini 的五个键决定：3+ 档缺省是**用户 2026-11-11
 *    实机手调**的节拍（票 07：聚气 ≈ 44% / 白住 ≈ 11% / 迸发后余韵 ≈ 44%），
 *    `SGS-INTEGRATION.md` §3 B4 / §6.2 的示例表仍是不动比例的参考。
 * 4. **座位舞台**：`stage: { dst }`（**只有目标**——回复不表现"从别处飞来"），矩形来自票 02 的
 *    `vitalFxBoxOf(heroBlockFor(seat))`。素材页的设计坐标是 1920×1080、牌心 (960, 556)、
 *    牌面 515×721（`CARD.w × CARD_SCALE`）；接牌桌用 §3 B2 那一条映射
 *    `s = block.width / 515`，画的时候直接把画布**按这个 s 变换一次**，于是素材页的每一笔
 *    都不用改单位。量不到座位矩形时退参考舞台（`s = 1`，导出档就是这一档）。
 * 5. **`bare: true`**（牌桌硬口径）：不画宣纸底与样例武将牌；白化用**半透明白**叠在覆盖层上
 *    （`rgba(255,255,255,α)`，α 到峰值时正好把真实牌面盖掉——**不要**改成 CSS 隐藏牌面：那样
 *    牌会先消失再出现，穿帮）；飘字的包围盒照 `fx-core.js` 的判据（不压体力条）。
 *
 * **档位判据不在这里**（spec §档位）：`vital-fx.js:vitalFxVariantOf(amount)` 是唯一的一处，
 * 模块只认排播层交给它的 `variant`。素材包那个 `tierFor()` 不许作为第二份判据活下来——多一份
 * 判据就会漂（`+4` 播 3 档、飘字写真实点数，两件事各有各的真源）。
 *
 * **没有整屏闪**：不碰 `.fx-flash`、不挂 CSS `filter`（画布内的 `c.filter` 是墨边缘的模糊）。
 * 素材页 3 档的"短白闪"在牌桌档收成**牌周围一圈径向光**（径向渐变的边缘 alpha 已是 0，
 * 画布外缘不着色）——整屏铺白会把牌桌刷白，spec §硬约束不允许。
 *
 * 纯 Canvas 2D，零依赖、零外部资源、不发请求、不碰 DOM（只 `document.createElement("canvas")`
 * 建离屏贴图）。`"use strict";` 是文件第一条语句；全局名 `window.SgsHealFX`，不改成 ESM。
 */
(function (global) {

  var TAU = Math.PI * 2;

  /* ---------------------------------------------------------------- 配色 ----
   * 宣纸 / 墨取自 SGS 前端 style.css 的 :root；绿不是荧光绿，是"青绿山水"里的**石绿**：
   * 深做墨、中做光、最亮做芯。 */
  var C = {
    paper:  "#f1ebdf",
    card:   "#fffdf7",
    cardLo: "#f8f1e2",
    line:   "#ddd8d0",
    ink:    "#1c1a17",
    dim:    "#6f6a62",
    red:    "#9c2b1b",
    bronze: "#7c4a20",
    jadeInk:  "#14543a",
    jadeDeep: "#1f7a4f",
    jade:     "#2ea06a",
    jadeMid:  "#59c88d",
    jadePale: "#b6f2cf",
    mint:     "#e8fff2"
  };

  var SERIF = '"Noto Serif SC","Songti SC","STSong","SimSun",serif';
  var SANS  = '"Noto Sans SC","PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif';

  /* ------------------------------------------------------ 参考舞台（导出档）---- */
  var REF_W = 1920, REF_H = 1080;
  var CARD = { w: 460, h: 644, pad: 74 };
  /* 单张牌居中：回复是"自己的牌在发光"，不需要攻防两张。单牌构图比双牌空，牌按 1.12 倍画；
   * 所有特效尺寸都跟着这张牌走（素材页的 `DST`）。 */
  var CARD_SCALE = 1.12;
  var DST = { cx: 960, cy: 556, w: CARD.w * CARD_SCALE, h: CARD.h * CARD_SCALE };

  /* 量不到座位矩形时的舞台 = 参考舞台（`s = 1`，素材页那条链逐字节不变）。 */
  var REF_RECT = { cx: DST.cx, cy: DST.cy, w: DST.w, h: DST.h };

  /* **白化的峰值**（票面 §1 的注意）：素材页的 α 是照着**浅色样例牌**调的，接真实牌面
   * 时要按牌底色重调一档（真实牌面比样例牌更花，白到底通常要**更高**才盖得住牌名 / 技能字）。
   * 两个具名常量、同一素材包原值（`0.985`），两档各管各的，都按**实机**可再调一档：
   *   - `BARE_WHITE_PEAK`：牌桌档（bare）。`2e-3` 级的小数留到实机再定：
   *     **最终值以实机为准，票面不预设**；
   *   - `EXPORT_WHITE_PEAK`：导出档（照素材页 1:1）。要改也照真实牌面**实机**校，
   *     别动素材页那条链。 */
  var BARE_WHITE_PEAK = 0.985;
  var EXPORT_WHITE_PEAK = 0.985;

  /* 飘字口径（与 `fx-core.js` / `fx-hp-loss.js` 同一套包围盒判据）：
   *   字高 ∈ [SCREEN_MIN_NUMBER, 35% × 牌高]（牌高小的时候上限优先：宁可小，也不能压住体力
   *   读数）；位置由"体力条下沿 + 2~3% 牌高"的硬线反推。 */
  var GLYPH_RATIO = 0.7;
  var NUMBER_MAX_RATIO = 0.35;
  var NUMBER_KEEP_OUT_FALLBACK = 0.45;
  var NUMBER_GAP_RATIO = 0.03;
  var SCREEN_MIN_NUMBER = 44;
  /* 素材页的飘字从牌心上方 150 设计像素起步、向上浮；牌桌档从硬线下方浮到硬线上（见
   * `create()` 的 `numFrom`）——两档的"上浮距离"用同一个设计像素数。 */
  var NUM_RISE = 150;

  var HERO = {
    name: "华佗", faction: "群", factionColor: C.bronze, hp: 4,
    skills: [
      ["急救", "你的回合外，你可以将一张红色牌当【桃】使用"],
      ["青囊", "出牌阶段限一次，你可以弃置一张手牌，令一名已受伤角色回复1点体力"]
    ]
  };

  /* ------------------------------------------------------------------ 三档 ----
   * 素材页的三档时间表（**秒**，绝对时刻）。`glowAt` = 牌沿起光；`gather = {at, dur}` =
   * 聚气从 `at` 开始、到 `at + dur` **结束**（素材页 0.22 → 1.24）；`hold` = 白住**时长**；
   * `burst.at` = 迸发时刻；`numAt` = 飘字浮起。**导出档逐字用它**；牌桌档只把五个锚点压到
   * `anim.ini` 的毫秒上。
   *
   * `dmg` 是档位号（1 / 2 / 3+ 都按 3 档演），不是回复点数——飘字写的是真实点数。
   * 档位的名字（微光 / 盎然 / 回春）只活在这行注释里：牌桌上没有地方读它。 */
  var TIERS = {
    1: {
      dmg: 1, name: "微光",
      duration: 3.20,
      glowPeak: 0.52, glowAt: 0.16,
      crossN: 18, crossLife: 1.85, crossSize: 1.00,
      ring: false, gather: null, burst: null,
      numAt: 0.54, numLife: 1.90, numSize: 168, numEcho: 0
    },
    2: {
      dmg: 2, name: "盎然",
      duration: 3.55,
      glowPeak: 0.86, glowAt: 0.16,
      crossN: 30, crossLife: 1.75, crossSize: 1.12,
      ring: true, gather: null, burst: null,
      numAt: 0.54, numLife: 1.95, numSize: 200, numEcho: 0
    },
    3: {
      dmg: 3, name: "回春",
      duration: 4.45,
      glowPeak: 1.05, glowAt: 0.16,
      crossN: 30, crossLife: 1.95, crossSize: 1.05,
      ring: false,
      /* 0.22 → 1.24 环形聚气并泛白，1.24 → 1.54 全白停顿，1.54 迸发 */
      gather: { at: 0.22, dur: 1.02 },
      hold: 0.30,
      burst: { at: 1.54, shake: 7 },
      numAt: 1.60, numLife: 2.20, numSize: 236, numEcho: 1
    }
  };

  /* ------------------------------------------------------------ 小工具 ---- */
  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ss(a, b, x) { var t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); }
  function eOut(t) { t = clamp(t, 0, 1); return 1 - Math.pow(1 - t, 3); }
  function eIn(t) { t = clamp(t, 0, 1); return t * t * t; }
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

  function pathCard(c, rect, inset) {
    var p = inset || 0;
    pathRound(c, rect.cx - rect.w / 2 + p, rect.cy - rect.h / 2 + p,
      rect.w - p * 2, rect.h - p * 2, 14);
  }

  function softGlow(c, x, y, r, color, a) {
    if (r <= 0 || a <= 0) return;
    var g = c.createRadialGradient(x, y, 0, x, y, r);
    g.addColorStop(0, rgba(color, a));
    g.addColorStop(0.45, rgba(color, a * 0.42));
    g.addColorStop(1, rgba(color, 0));
    c.fillStyle = g;
    c.beginPath();
    c.arc(x, y, r, 0, TAU);
    c.fill();
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

  /* `anim.ini` 的 `heal_fx_<档>_<字段>` 的**内置默认**（磁盘副本在
   * `tools/sgs_web/static/anim.ini`；缺键 / 解析失败 / 非法值 ⇒ 退回这里）。
   *
   * 五个字段都是**从开演算的绝对毫秒**（`hold` 是**时长**，不是时刻），钳制见 `gameTiming`：
   * `gather + hold ≤ burst ≤ num ≤ total ≤ MAX_TOTAL_MS`。缺省 = **用户 2026-11-11 实机手调
   * 的口径（票 07）**：1 = 1100 / 2 = 1300 / 3 = 4500；3+ 档聚气 2000 / 白住 500 /
   * 迸发 2500 / 飘字 2500（聚气 44.4% / 白住 11.1% / 迸发 55.6% / 余韵 44.4%，尾巴与聚气
   * 等长；`2000 + 500 = 2500 = burst`）。`SGS-INTEGRATION` §6.2 的示例表仍是不动比例的
   * 参考，不是这张表的缺省。
   *
   * **1 / 2 档没有 `gather` / `hold` / `burst` 三个键**：它们没有聚气 / 白住 / 迸发这三拍
   * （素材页那两档是"牌沿绿光 + 上浮十字 + 一圈淡绿涟漪"）。`TIMING_FIELDS` 由下面这张表的
   * 键反推，所以"少三个键"这件事只有这一处真源。
   *
   * 为什么 3+ 档是 4500（伤害的剑光是 2100 的上限、失去体力是 1800）：回复的 3+ 档
   * **冻回放**（串行 + 独占牌桌，spec §演出口径），4.5s 是给它的**时停窗口**——用户
   * 2026-11-11 实机改判：原来的 2200 是"看得到聚气 → 白住 → 迸发"的下限，实机看下来 3+
   * 档偏短（素材页这一档本身是 4.45s），裁定上限放宽到 4500、3+ 档播满；拍子随后按实机
   * 手感重排（票 07：聚气约占一半、白住 0.5s、数字在迸发那一刻出，尾巴留到 4.5s）。1 / 2
   * 档不冻，1100 / 1300 与素材页的"清磬 / 三音上行"同节奏；**失去体力与伤害仍守 2200**
   * （`fx-hp-loss.js` 一个字不动）。 */
  var TIMING_PREFIX = "heal_fx_";
  var DEFAULT_TIMING = {
    heal_fx_1_total: 1100, heal_fx_1_num: 350,
    heal_fx_2_total: 1300, heal_fx_2_num: 350,
    heal_fx_3_total: 4500,
    heal_fx_3_gather: 2000, heal_fx_3_hold: 500,
    heal_fx_3_burst: 2500, heal_fx_3_num: 2500
  };

  /* 字段名名单由 `DEFAULT_TIMING` 的键反推（只许有一处真源；与雷 / 火 / 失去体力同款）。 */
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
  /* 单档最长的硬上限：回复 3+ 档是**时停**演出，4.5s 是按用户 2026-11-11 实机口径给的窗口
   * （票 06 改判，原 2200）。1 / 2 档与后续记录并行，仍守同一个上限；**失去体力仍压 2200**
   * （`fx-hp-loss.js` 的 `MAX_TOTAL_MS` 一个字不动，伤害也是）。 */
  var MAX_TOTAL_MS = 4500;

  /* 牌桌秒 ↔ 素材页秒（与 `fx-fire.js` / `fx-hp-loss.js` 的 `warpAt` / `warpGameOf` 同一实现）：
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

  /* 锚点对按顺序收下；游戏侧不许倒挂（用户把 gather / burst 调成 0 时全靠这条兜住）。 */
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
   * `GATHER` 是聚气**结束**的时刻（= 白住的起点），`HOLD` 是白住**时长**
   * （`BURST - GATHER`，与 ini 里的 `hold` 同值），两者相加就是迸发时刻。 */
  function timelineFor(warp, base) {
    function game(exportSeconds) {
      return warp ? warpGameOf(warp, exportSeconds) : exportSeconds;
    }
    var gather = base.gather ? game(base.gather.at + base.gather.dur) : 0;
    var burst = base.burst ? game(base.burst.at) : 0;
    return {
      DURATION: game(base.duration),
      GLOW: game(base.glowAt),
      NUM: game(base.numAt),
      GATHER_START: base.gather ? game(base.gather.at) : 0,
      GATHER: gather,
      HOLD: base.gather ? Math.max(0, burst - gather) : 0,
      BURST: burst,
      // 档位号：音效按档选音色（1 = 清磬 / 2 = 三音上行 / 3 = 吸气→落点）。数字，照旧过
      // `sfx-ui.js` 的"只收数字"那道口。
      TIER: base.dmg
    };
  }

  /* 一条**空**时间轴（`total = 0`：用户把这一档关掉）。所有锚点重合，画 / 声音都只剩一层壳；
   * 显式返回，免得 `warpPairs` 收成一对之后 `duration` 退回素材页秒数。 */
  function zeroTimeline(base) {
    return {
      DURATION: 0, GLOW: 0, NUM: 0,
      GATHER_START: 0, GATHER: 0, HOLD: 0, BURST: 0, TIER: base.dmg
    };
  }

  /* `game` 档：把 `anim.ini` 的五个字段解成一档**秒**时间轴 + 时间映射。
   *
   * 钳制（在模块内部，spec §演出口径）：
   *   `total ≤ MAX_TOTAL_MS`；`gather ≤ total`；`gather + hold ≤ total`；
   *   `burst ≥ gather + hold`；`num ≥ burst`；`num ≤ total`。
   * 也就是票面那条 `gather ≤ hold 起点 ≤ burst ≤ num ≤ total`——`hold` 是**时长**，所以
   * 它的"起点"就是 `gather`（聚气结束 = 白住起点），写成算术是 `gather + hold ≤ burst`。
   *
   * 缺键 / 非法值退 `DEFAULT_TIMING`；**数字合法但越界**时按"抬到前一个锚点"钳（不是退
   * 默认）——用户把 burst 调到比聚气还早，意图是"聚完就炸"，不是"回到出厂值"。
   *
   * `numLife` 在牌桌档要撑到摘层（素材页那边是 1.9~2.2s，压到 1.1s 的窗口里会提前消失），
   * 其余档位参数（`numAt` / `gather.at` / `burst.at` / `glowAt` …）**全部是素材页秒数**：
   * `draw()` 每帧先把牌桌秒数映射回素材页再画，所以它们一个都不能换成牌桌秒数（换了就是
   * 两套钟）。 */
  function gameTiming(variantId, raw) {
    var id = clamp(Math.round(variantId || 1), 1, 3);
    var base = TIERS[id];
    function key(field) { return TIMING_PREFIX + id + "_" + field; }
    var total = Math.min(
      millis(raw, key("total"), DEFAULT_TIMING[key("total")]), MAX_TOTAL_MS
    );
    var gather = 0, hold = 0, burst = 0, num;
    if (base.gather) {
      gather = clamp(millis(raw, key("gather"), DEFAULT_TIMING[key("gather")]), 0, total);
      hold = clamp(millis(raw, key("hold"), DEFAULT_TIMING[key("hold")]), 0,
        Math.max(0, total - gather));
      burst = clamp(millis(raw, key("burst"), DEFAULT_TIMING[key("burst")]), gather + hold, total);
      num = clamp(millis(raw, key("num"), DEFAULT_TIMING[key("num")]), burst, total);
    } else {
      num = clamp(millis(raw, key("num"), DEFAULT_TIMING[key("num")]), 0, total);
    }
    var ms = { total: total, num: num };
    if (base.gather) {
      ms.gather = gather;
      ms.hold = hold;
      ms.burst = burst;
    }
    var variant = copy(base, {
      numLife: Math.max(0.3, base.duration - base.numAt)
    });
    if (total <= 0) {
      return {
        id: id, profile: "game", timeline: zeroTimeline(base), warp: null, ms: ms, variant: variant
      };
    }
    var seconds = total / 1000;
    var gatherEnd = base.gather ? base.gather.at + base.gather.dur : 0;
    var pairs = base.gather
      ? warpPairs([
        { e: 0, g: 0 },
        /* 聚气**起点**按素材页的比例压：2000 × 0.22 / 1.24 ≈ 355ms ⇒ 聚气段内部的起点比例
         * 与素材页一致（段内线性；档级节拍由 ini 的 gather / burst / num / total 定）。 */
        { e: base.gather.at, g: gather * (base.gather.at / gatherEnd) / 1000 },
        { e: gatherEnd, g: gather / 1000 },
        { e: base.burst.at, g: burst / 1000 },
        { e: base.numAt, g: num / 1000 },
        { e: base.duration, g: seconds }
      ])
      : warpPairs([
        { e: 0, g: 0 },
        { e: base.numAt, g: num / 1000 },
        { e: base.duration, g: seconds }
      ]);
    return {
      id: id,
      profile: "game",
      timeline: timelineFor(pairs, base),
      warp: pairs,
      ms: ms,
      variant: variant
    };
  }

  /* `export` 档：参考实现的 3.20 / 3.55 / 4.45 秒链（`profile` 缺省走它）。 */
  function exportClock(variantId) {
    var id = clamp(Math.round(variantId || 1), 1, 3);
    var base = TIERS[id];
    return {
      id: id,
      profile: "export",
      timeline: timelineFor(null, base),
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
   * 座位矩形 → 舞台（只有目标一块：回复不表现"从别处飞来"）
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
   * `s` = 它比参考牌面 515×721 宽多少 —— **素材页的每一笔都按这个 s 缩放一次**（§3 B2 的映射
   * `s = block.width / 515`）。这里**不给** s 兜底下限：变换必须把设计牌面精确映射到真实牌面，
   * 否则特效会漂到牌外；字太小由飘字的屏幕下限（`SCREEN_MIN_NUMBER`）兜。不传 `stage` ⇒
   * 参考舞台（`s = 1`），素材库那条链逐字节不变。 */
  function stageFor(raw) {
    var hasStage = Boolean(raw && raw.dst);
    var dst = hasStage ? rectOr(raw.dst, REF_RECT) : REF_RECT;
    var s = dst.w / DST.w;
    if (!isFinite(s) || s <= 0) s = 1;
    return {
      dst: dst,
      rect: { x: dst.cx - dst.w / 2, y: dst.cy - dst.h / 2, w: dst.w, h: dst.h },
      s: s,
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
   * 每条演出在 `draw()` 开头切一次（同 `fx-hp-loss.js` 的姿态）：同一条演出是同步画的，
   * 并发几条依次 draw、互不串味。`BARE` 只影响"整屏白闪要不要收成牌周围一圈"。 */
  var W = REF_W, H = REF_H, BARE = false;

  function setFrame(w, h, bare) {
    W = w;
    H = h;
    BARE = bare;
  }

  /* ================================================================
   * 宣纸 / 武将牌 / 十字 / 光尾（一次性烘焙，跨实例复用）
   * ============================================================== */

  function paintPaper(S) {
    var w = REF_W + 160, h = REF_H + 160;
    var pc = offscreen(w * S, h * S), c = pc[1];
    c.scale(S, S);
    var r = rng(20261112);

    c.fillStyle = C.paper;
    c.fillRect(0, 0, w, h);

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
    /* 一点青绿底子：和火焰版的暖赭对称，别让纸全是黄的。 */
    var cool = c.createRadialGradient(w * 0.42, h * 0.34, 0, w * 0.42, h * 0.34, 720);
    cool.addColorStop(0, "rgba(64,132,104,0.026)");
    cool.addColorStop(1, "rgba(64,132,104,0)");
    c.fillStyle = cool;
    c.fillRect(0, 0, w, h);
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

    /* 体力段（静态墨块；回复时外面叠一层青绿光） */
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

  var TEX = {};
  function texture(key, paint) {
    if (!TEX[key]) TEX[key] = paint();
    return TEX[key];
  }

  /* 一条朝右渐亮的光尾：头亮尾透，边缘糊一点；聚气时旋转到射线方向用。 */
  function paintTrail() {
    var w = 160, h = 16;
    var cv = offscreen(w, h), c = cv[1];
    var g = c.createLinearGradient(0, h / 2, w, h / 2);
    g.addColorStop(0, "rgba(70,200,135,0)");
    g.addColorStop(0.55, "rgba(86,214,146,0.40)");
    g.addColorStop(1, "rgba(230,255,242,1)");
    c.fillStyle = g;
    c.beginPath();
    if (c.roundRect) c.roundRect(0, h / 2 - 2, w, 4, 2); else c.rect(0, h / 2 - 2, w, 4);
    c.fill();

    var out = offscreen(w, h), o = out[1];
    /* 画布内的模糊（贴图边缘），不是 CSS 的层滤镜。 */
    if (typeof o.filter === "string") o.filter = "blur(1.2px)";
    o.drawImage(cv[0], 0, 0);
    if (typeof o.filter === "string") o.filter = "none";
    return out[0];
  }

  /* 十字：圆头十字 + 白芯 + 柔光底，预烘焙成贴图。 */
  function paintCross(px) {
    var S = px || 128;
    var cv = offscreen(S, S), c = cv[1];
    var r = S / 2;

    var g = c.createRadialGradient(r, r, 0, r, r, r);
    g.addColorStop(0, "rgba(96,226,158,0.50)");
    g.addColorStop(0.26, "rgba(96,226,158,0.22)");
    g.addColorStop(0.62, "rgba(96,226,158,0.06)");
    g.addColorStop(1, "rgba(96,226,158,0)");
    c.fillStyle = g;
    c.beginPath(); c.arc(r, r, r, 0, TAU); c.fill();

    var arm = S * 0.30, thick = S * 0.105;
    c.lineCap = "round";
    c.fillStyle = "rgba(31,132,84,0.94)";
    c.shadowColor = "rgba(70,214,140,0.95)";
    c.shadowBlur = S * 0.09;
    c.fillRect(r - thick / 2, r - arm, thick, arm * 2);
    c.fillRect(r - arm, r - thick / 2, arm * 2, thick);
    c.shadowBlur = 0;

    c.fillStyle = "rgba(238,255,244,0.95)";
    c.fillRect(r - thick * 0.24, r - arm * 0.86, thick * 0.48, arm * 1.72);
    c.fillRect(r - arm * 0.86, r - thick * 0.24, arm * 1.72, thick * 0.48);
    c.beginPath(); c.arc(r, r, thick * 0.42, 0, TAU); c.fill();
    return cv[0];
  }

  /* 飘字贴图：青绿渐变 + 深墨绿描边 + 纸色外晕 + 青绿墨晕托底。数字照**真实回复量**写
   * （`+4` 就是 `+4`，不是档位号）。 */
  function numGradient(c, size) {
    var g = c.createLinearGradient(0, -size * 0.60, 0, size * 0.60);
    g.addColorStop(0, "#e6fff0");
    g.addColorStop(0.16, "#9df0c2");
    g.addColorStop(0.42, "#45d18c");
    g.addColorStop(0.70, "#169159");
    g.addColorStop(1, "#0a5738");
    return g;
  }

  /* ================================================================
   * 光量曲线
   * ============================================================== */

  function glowAt(t, V) {
    if (V.gather) {
      var g0 = V.gather.at, g1 = g0 + V.gather.dur;
      if (t < g0) return 0.12 * ss(0, 0.5, t);
      if (t < g1) { var p = (t - g0) / V.gather.dur; return 0.30 + 0.85 * p * p; }
      /* 停顿：光憋在高位，只再涨一点，等迸发 */
      if (t < V.burst.at) return 1.18 + 0.22 * ss(g1, V.burst.at, t);
      var b = t - V.burst.at;
      var tail = Math.exp(-Math.max(0, b - 0.55) / 0.85);
      return (1.45 + 0.55 * Math.exp(-3.0 * b)) * tail;
    }
    var a = ss(V.glowAt, V.glowAt + 0.55, t);
    var decay = Math.exp(-Math.max(0, t - (V.glowAt + 0.85)) / 1.05);
    return V.glowPeak * a * decay;
  }

  /* 0~1：整张牌的白光覆盖度。聚气末段升到 1（文字被盖住），停顿期间保持，迸发后迅速退掉。 */
  function whiteAt(t, V) {
    if (!V.gather) return 0;
    var g0 = V.gather.at, g1 = g0 + V.gather.dur;
    if (t < g0) return 0;
    if (t < g1) {
      var p = (t - g0) / V.gather.dur;
      return Math.pow(p, 1.9);
    }
    if (t < V.burst.at) return 1;                     /* 停顿 */
    return 0.85 * Math.exp(-9.5 * (t - V.burst.at));
  }

  /* ================================================================
   * 牌外绿光：纸上洇开的石绿水晕 + 贴边亮芯
   * ============================================================== */

  /* 洇开的墨晕边缘：半径带噪声的圆，比正圆更像水墨。 */
  function washPath(c, x, y, R, seed, rot) {
    var n = 26, pts = [];
    for (var i = 0; i < n; i++) {
      var a = i / n * TAU + rot;
      var k = 1 + 0.06 * Math.sin(a * 3 + seed) + 0.04 * Math.sin(a * 7 - seed * 1.7);
      pts.push([x + Math.cos(a) * R * k, y + Math.sin(a) * R * k]);
    }
    var m = pts.length;
    c.beginPath();
    c.moveTo((pts[m - 1][0] + pts[0][0]) / 2, (pts[m - 1][1] + pts[0][1]) / 2);
    for (var j = 0; j < m; j++) {
      var p0 = pts[j], p1 = pts[(j + 1) % m];
      c.quadraticCurveTo(p0[0], p0[1], (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2);
    }
    c.closePath();
  }

  function drawAura(c, t, V) {
    var A = glowAt(t, V);
    if (A <= 0.004) return;
    var cx = DST.cx, cy = DST.cy;
    var k = clamp(A, 0, 1.35);
    var grow = V.gather ? clamp(t / 2.2, 0, 1) : clamp(t / 1.6, 0, 1);

    c.save();
    /* 1) 石绿水晕：multiply 让牌真的变绿一点，而不是变成一片奶白。 */
    c.globalCompositeOperation = "multiply";
    var R = 350 + 160 * grow;
    if (typeof c.filter === "string") c.filter = "blur(26px)";
    c.fillStyle = "rgba(112,198,154," + (0.17 * k + 0.015) + ")";
    washPath(c, cx, cy - 10, R, 1.3, 0.4);
    c.fill();
    if (typeof c.filter === "string") c.filter = "blur(34px)";
    c.fillStyle = "rgba(84,178,128," + (0.10 * k + 0.008) + ")";
    washPath(c, cx + 16, cy + 18, R * 1.34, 4.1, 1.1);
    c.fill();
    if (typeof c.filter === "string") c.filter = "none";

    var g = c.createRadialGradient(cx, cy, 120, cx, cy, R);
    g.addColorStop(0, "rgba(255,255,255,0)");
    g.addColorStop(0.72, "rgba(212,244,224," + (0.16 * k) + ")");
    g.addColorStop(1, "rgba(150,224,184," + (0.30 * k) + ")");
    c.fillStyle = g;
    c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.fill();

    /* 2) 牌背后透出的光：只提亮牌附近，不把整张纸刷白 */
    c.globalCompositeOperation = "screen";
    var g2 = c.createRadialGradient(cx, cy, 40, cx, cy, 380);
    g2.addColorStop(0, "rgba(214,255,232," + (0.30 * k) + ")");
    g2.addColorStop(0.45, "rgba(176,246,208," + (0.18 * k) + ")");
    g2.addColorStop(1, "rgba(140,230,180,0)");
    c.fillStyle = g2;
    c.beginPath(); c.arc(cx, cy, 380, 0, TAU); c.fill();

    /* 3) 贴边亮芯：这才是"牌在发光" */
    c.globalCompositeOperation = "source-over";
    c.save();
    c.shadowColor = "rgba(42,190,116," + (0.95 * k) + ")";
    c.shadowBlur = 34 * k + 8;
    c.strokeStyle = "rgba(30,150,94," + (0.78 * clamp(k, 0, 1)) + ")";
    c.lineWidth = 3.6;
    pathCard(c, DST, -2);
    c.stroke();
    c.restore();

    c.save();
    c.strokeStyle = "rgba(248,255,250," + (0.55 * clamp(k - 0.35, 0, 1)) + ")";
    c.lineWidth = 1.5;
    pathCard(c, DST, -1.2);
    c.stroke();
    c.restore();
    c.restore();

    /* 4) 体力段的绿光：表示"这几点体力回来了" */
    var hpW = 4 * 22 + 3 * 6;
    var hx = DST.cx + DST.w / 2 - 30 - 16 - hpW * 1.12;
    var hy = DST.cy - DST.h / 2 + 28 + 18;
    c.save();
    c.globalCompositeOperation = "screen";
    softGlow(c, hx + hpW * 0.56, hy, 86 * k, C.jadeMid, 0.5 * clamp(A, 0, 1));
    c.restore();
  }

  /* ================================================================
   * 牌面光：内缘青绿、整张牌泛白、聚气气核
   * ============================================================== */

  function drawCardLight(c, t, V) {
    var A = glowAt(t, V);
    var Wt = whiteAt(t, V);
    if (A <= 0.004 && Wt <= 0.004) return;

    var cover = clamp(Wt, 0, 1);
    var x0 = DST.cx - DST.w / 2, y0 = DST.cy - DST.h / 2;

    /* 牌外的白光晕：白到一定程度，牌像一块烧起来的玉 */
    if (cover > 0.02) {
      c.save();
      c.globalCompositeOperation = "screen";
      softGlow(c, DST.cx, DST.cy, 520, "#f2fff8", 0.55 * cover);
      softGlow(c, DST.cx, DST.cy, 300, "#ffffff", 0.45 * cover * cover);
      c.restore();
    }

    c.save();
    pathCard(c, DST, 0.5);
    c.clip();

    /* 内缘的青绿：白得越厉害，绿越退到边上 */
    if (A > 0.01 && cover < 0.96) {
      c.globalCompositeOperation = "multiply";
      var g = c.createRadialGradient(DST.cx, DST.cy, DST.w * 0.22, DST.cx, DST.cy, DST.w * 0.80);
      var fade = 1 - 0.75 * cover;
      g.addColorStop(0, "rgba(255,255,255,0)");
      g.addColorStop(0.7, "rgba(196,240,212," + (0.13 * clamp(A, 0, 1) * fade) + ")");
      g.addColorStop(1, "rgba(120,208,160," + (0.34 * clamp(A, 0, 1) * fade) + ")");
      c.fillStyle = g;
      c.fillRect(x0, y0, DST.w, DST.h);
      c.globalCompositeOperation = "source-over";
    }

    /* 白到头：近乎实白，牌名 / 技能字全部盖住。牌桌档这一层**盖在真实牌面之上**（覆盖层是
     * 透明的，白是半透明白叠上去）——**不是**用 CSS 把牌藏起来：那样牌会先消失再出现。
     * 峰值按档取具名常量：牌桌档 `BARE_WHITE_PEAK`、导出档 `EXPORT_WHITE_PEAK`（同值，各按实机可调）。 */
    if (cover > 0.01) {
      var peak = BARE ? BARE_WHITE_PEAK : EXPORT_WHITE_PEAK;
      c.fillStyle = "rgba(255,255,255," + (peak * Math.pow(cover, 1.05)) + ")";
      c.fillRect(x0, y0, DST.w, DST.h);
      var gw = c.createRadialGradient(DST.cx, DST.cy, 0, DST.cx, DST.cy, DST.w * 0.9);
      gw.addColorStop(0, "rgba(244,255,249," + (0.55 * cover) + ")");
      gw.addColorStop(0.55, "rgba(226,255,238," + (0.28 * cover) + ")");
      gw.addColorStop(1, "rgba(206,255,226,0)");
      c.fillStyle = gw;
      c.fillRect(x0, y0, DST.w, DST.h);
    }
    c.restore();

    /* 聚气：牌心的气核，边缘石绿、中心白热 */
    if (V.gather) {
      var age = t - V.gather.at;
      if (age >= 0 && age < V.gather.dur + 0.02) {
        var p = clamp(age / V.gather.dur, 0, 1);
        /* 先胀起来，最后一小段往里压 —— 蓄满的那一下 */
        var grow = eOut(clamp(p / 0.72, 0, 1));
        var squeeze = p > 0.72 ? (p - 0.72) / 0.28 : 0;
        var R = lerp(8, 84, grow) * (1 - 0.40 * squeeze);
        var a = ss(0, 0.10, p) * (0.32 + 0.68 * p) * (1 + 0.35 * squeeze);
        var g3 = c.createRadialGradient(DST.cx, DST.cy, 0, DST.cx, DST.cy, R);
        g3.addColorStop(0, "rgba(255,255,255," + (0.98 * a) + ")");
        g3.addColorStop(0.35, "rgba(214,255,232," + (0.92 * a) + ")");
        g3.addColorStop(0.78, "rgba(58,196,128," + (0.72 * a) + ")");
        g3.addColorStop(1, "rgba(30,140,88,0)");
        c.save();
        c.globalCompositeOperation = "screen";
        softGlow(c, DST.cx, DST.cy, R * 2.4, "#b6ffd4", 0.30 * a);
        c.restore();
        c.fillStyle = g3;
        c.beginPath(); c.arc(DST.cx, DST.cy, R, 0, TAU); c.fill();

        /* 气核外面再套一圈亮环 */
        c.strokeStyle = "rgba(240,255,246," + (0.8 * a) + ")";
        c.lineWidth = 1.6;
        c.beginPath(); c.arc(DST.cx, DST.cy, R * 0.92, 0, TAU); c.stroke();

        /* 核里往外顶的短亮丝：读得出"在蓄" */
        c.save();
        c.translate(DST.cx, DST.cy);
        c.rotate(age * 1.6);
        for (var ri = 0; ri < 10; ri++) {
          var ra = ri / 10 * TAU;
          var l0 = R * 0.30, l1 = R * (1.05 + 0.18 * Math.sin(age * 9 + ri));
          c.strokeStyle = "rgba(255,255,255," + (0.55 * a) + ")";
          c.lineWidth = 1.6;
          c.beginPath();
          c.moveTo(Math.cos(ra) * l0, Math.sin(ra) * l0);
          c.lineTo(Math.cos(ra) * l1, Math.sin(ra) * l1);
          c.stroke();
        }
        c.restore();
      }
    }

    /* 停顿：整张牌白住，牌心只剩一颗发绿的"种子"，等它炸开 */
    if (V.gather && V.burst && t >= V.gather.at + V.gather.dur && t < V.burst.at) {
      var hp = (t - (V.gather.at + V.gather.dur)) /
        Math.max(0.01, V.burst.at - (V.gather.at + V.gather.dur));
      var rr = 34 - 10 * hp;
      var pulse = 0.78 + 0.22 * Math.sin(t * 26);
      c.save();
      c.shadowColor = "rgba(40,190,120," + (0.85 * pulse) + ")";
      c.shadowBlur = 26;
      c.strokeStyle = "rgba(26,152,92," + (0.95 * pulse) + ")";
      c.lineWidth = 3.4;
      c.beginPath(); c.arc(DST.cx, DST.cy, rr, 0, TAU); c.stroke();
      c.shadowBlur = 0;
      c.strokeStyle = "rgba(255,255,255," + (0.95 * pulse) + ")";
      c.lineWidth = 1.4;
      c.beginPath(); c.arc(DST.cx, DST.cy, rr * 0.84, 0, TAU); c.stroke();
      var g5 = c.createRadialGradient(DST.cx, DST.cy, 0, DST.cx, DST.cy, rr * 1.9);
      g5.addColorStop(0, "rgba(255,255,255," + (0.98 * pulse) + ")");
      g5.addColorStop(0.45, "rgba(150,255,198," + (0.5 * pulse) + ")");
      g5.addColorStop(1, "rgba(120,240,170,0)");
      c.fillStyle = g5;
      c.beginPath(); c.arc(DST.cx, DST.cy, rr * 1.9, 0, TAU); c.fill();
      /* 外面再套一圈虚的，像气在往外渗 */
      c.strokeStyle = "rgba(70,206,140," + (0.30 * pulse) + ")";
      c.lineWidth = 1.4;
      c.beginPath(); c.arc(DST.cx, DST.cy, rr * 1.55, 0, TAU); c.stroke();
      c.restore();
    }
  }

  /* ================================================================
   * 上升的十字
   * ============================================================== */

  function drawSprite(c, img, x, y, size, alpha, rot) {
    if (alpha <= 0.004) return;
    c.save();
    c.globalAlpha = clamp(alpha, 0, 1);
    c.translate(x, y);
    if (rot) c.rotate(rot);
    c.drawImage(img, -size / 2, -size / 2, size, size);
    c.restore();
  }

  function drawRisingCrosses(c, t, V) {
    if (t < 0.1) return;
    /* 3 档白起来之后，散在牌上的十字被光"吞"进去：一边淡掉，一边往牌心靠 */
    var cov = V.gather ? clamp(whiteAt(t, V), 0, 1) : 0;
    var absorb = 1 - 0.95 * cov;
    if (absorb <= 0.01) return;
    for (var i = 0; i < V.crosses.length; i++) {
      var p = V.crosses[i];
      var a = t - 0.18 - p.delay;
      if (a < 0) continue;
      var life = p.life;
      if (a > life) continue;
      var x = DST.cx + (p.u - 0.5) * DST.w * 0.94 + Math.sin(a * 1.5 + p.ph) * 16;
      var y = DST.cy + (p.y0 - 0.5) * DST.h - a * p.spd - eOut(a / life) * p.h * 0.35;
      if (cov > 0) {
        x = lerp(x, DST.cx, cov * 0.45);
        y = lerp(y, DST.cy, cov * 0.45);
      }
      var fade = ss(0, 0.28, a) * (1 - ss(life * 0.62, life, a)) * absorb;
      var size = p.size * (0.78 + 0.3 * ss(0, 0.5, a)) * (1 - 0.35 * cov);
      drawSprite(c, V.crossImg, x, y, size, fade, Math.sin(a * 1.2 + p.ph) * 0.22);
    }
  }

  /* ================================================================
   * 聚气：绿色粒子从四面八方往牌心汇聚，同时牌面越来越白
   * ============================================================== */
  function drawGather(c, t, V) {
    if (!V.gather) return;
    var age = t - V.gather.at;
    if (age < 0 || age > V.gather.dur + 0.04) return;
    var p = clamp(age / V.gather.dur, 0, 1);
    var cx = DST.cx, cy = DST.cy;

    c.save();
    for (var i = 0; i < V.gatherParts.length; i++) {
      var g = V.gatherParts[i];
      var q = (p - g.delay) / g.travel;
      if (q <= 0 || q >= 1) continue;

      /* 头：从外面一路加速收进牌心，带一点旋转，像被吸进去 */
      var r = g.r0 * (1 - eIn(q));
      var ang = g.a + g.spin * q + p * 0.18;
      var x = cx + Math.cos(ang) * r;
      var y = cy + Math.sin(ang) * r * 0.94;
      var al = ss(0, 0.12, q) * (1 - ss(0.9, 1, q));
      var tw = 0.72 + 0.28 * Math.sin(age * 7 + g.ph);
      var sz = g.size * (0.55 + 0.45 * (1 - q));

      /* 尾：沿射线往回拖一条渐隐的光线，方向一眼看得出来 */
      var qt = Math.max(0, q - 0.10 * g.trail);
      var rt = g.r0 * (1 - eIn(qt));
      var at = g.a + g.spin * qt + p * 0.18;
      var tx = cx + Math.cos(at) * rt;
      var ty = cy + Math.sin(at) * rt * 0.94;
      var dx = x - tx, dy = y - ty;
      var len = Math.sqrt(dx * dx + dy * dy);
      if (len > 1) {
        c.save();
        c.globalAlpha = al * 0.8;
        c.translate(tx, ty);
        c.rotate(Math.atan2(dy, dx));
        c.drawImage(V.trailImg, 0, -8, len, 16);
        c.restore();
      }

      drawSprite(c, V.crossImg, x, y, sz, al * (0.72 + 0.28 * tw), ang + 0.3);
    }

    /* 收束的最后一段：牌心往外顶的短光丝（不是环，只是中心的气） */
    if (p > 0.7) {
      var kk = (p - 0.7) / 0.3;
      c.strokeStyle = "rgba(255,255,255," + (0.55 * kk) + ")";
      c.lineWidth = 1.4;
      for (var m = 0; m < 14; m++) {
        var am = m / 14 * TAU + age * 1.8;
        var r0 = 54 * (1 - kk * 0.8);
        c.beginPath();
        c.moveTo(cx + Math.cos(am) * r0, cy + Math.sin(am) * r0);
        c.lineTo(Math.cos(am) * (r0 + 24 * (1 - kk)) + cx,
          Math.sin(am) * (r0 + 24 * (1 - kk)) + cy);
        c.stroke();
      }
    }
    c.restore();
  }

  /* ================================================================
   * 爆发：一圈绿光从牌心荡出去，环上骑着一圈十字
   * ============================================================== */
  function drawBurst(c, t, V) {
    if (!V.burst) return;
    var b = t - V.burst.at;
    if (b < 0) return;
    var cx = DST.cx, cy = DST.cy;

    /* 0) 起手：牌心一团白光迅速收掉 */
    var core = Math.exp(-b * 26);
    if (core > 0.01) {
      c.save();
      c.globalCompositeOperation = "screen";
      softGlow(c, cx, cy, 210 * (0.6 + 0.4 * core), "#f4fff8", 0.55 * core);
      c.restore();
    }

    /* 1) 主环：先在纸上洇一圈绿墨，再压一圈白光 */
    var p1 = clamp(b / 0.86, 0, 1);
    if (p1 < 1) {
      var R = 92 + 1010 * eOut(p1);
      var a1 = Math.pow(1 - p1, 1.7);
      c.save();
      c.globalCompositeOperation = "multiply";
      if (typeof c.filter === "string") c.filter = "blur(14px)";
      c.strokeStyle = "rgba(42,158,104," + (0.40 * a1) + ")";
      c.lineWidth = 92 * Math.pow(1 - p1, 1.1) + 44;
      c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.stroke();
      if (typeof c.filter === "string") c.filter = "none";
      c.restore();

      c.save();
      c.strokeStyle = "rgba(44,196,126," + (0.34 * a1) + ")";
      c.lineWidth = 62 * Math.pow(1 - p1, 1.2) + 6;
      c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.stroke();
      c.strokeStyle = "rgba(18,138,82," + (0.68 * a1) + ")";
      c.lineWidth = 12 * (1 - p1) + 2.4;
      c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.stroke();
      c.strokeStyle = "rgba(255,255,255," + (0.98 * a1) + ")";
      c.lineWidth = 3.6 * (1 - p1) + 1.4;
      c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.stroke();
      c.restore();

      /* 环上的十字：跟着波前往外走 */
      for (var i = 0; i < V.waveCrosses.length; i++) {
        var wc = V.waveCrosses[i];
        var rw = R + wc.off;
        var xw = cx + Math.cos(wc.a) * rw;
        var yw = cy + Math.sin(wc.a) * rw * 0.9;
        drawSprite(c, V.crossImg, xw, yw, wc.size * (1 - 0.25 * p1), a1 * 0.95, wc.a + p1 * 1.2);
      }
    }

    /* 2) 地面冲击环：压扁，往两边荡开 */
    var pg = clamp(b / 0.9, 0, 1);
    if (pg > 0 && pg < 1) {
      var Rg = 50 + 1220 * eOut(pg);
      var ag = Math.pow(1 - pg, 2.0) * 0.55;
      c.save();
      c.translate(cx, cy + DST.h * 0.22);
      c.scale(1, 0.30);
      c.strokeStyle = "rgba(34,156,102," + (0.35 * ag) + ")";
      c.lineWidth = (26 * Math.pow(1 - pg, 1.3) + 3) / 0.30;
      c.beginPath(); c.arc(0, 0, Rg, 0, TAU); c.stroke();
      c.strokeStyle = "rgba(252,255,253," + (0.7 * ag) + ")";
      c.lineWidth = (3.5 * Math.pow(1 - pg, 1.6) + 1) / 0.30;
      c.beginPath(); c.arc(0, 0, Rg, 0, TAU); c.stroke();
      c.restore();
    }

    /* 3) 二次环：稍晚、更淡 */
    var p3 = clamp((b - 0.14) / 0.9, 0, 1);
    if (p3 > 0 && p3 < 1) {
      var R3 = 40 + 1180 * eOut(p3);
      c.strokeStyle = "rgba(46,180,116," + (0.26 * Math.pow(1 - p3, 1.8)) + ")";
      c.lineWidth = 6 * (1 - p3) + 1;
      c.beginPath(); c.arc(cx, cy, R3, 0, TAU); c.stroke();
    }

    /* 4) 放射速度线 */
    for (var m = 0; m < V.streaks.length; m++) {
      var st = V.streaks[m];
      var ag = b - st.delay;
      if (ag < 0 || ag > st.life) continue;
      var q = ag / st.life;
      var r0 = st.r0 + eOut(q) * 140;
      var r1 = r0 + st.len * eOut(Math.min(1, q * 1.5));
      c.strokeStyle = "rgba(26,120,78," + (0.40 * Math.pow(1 - q, 1.9)) + ")";
      c.lineWidth = st.w * (1 - q * 0.45);
      c.beginPath();
      c.moveTo(cx + Math.cos(st.a) * r0, cy + Math.sin(st.a) * r0 * 0.84);
      c.lineTo(cx + Math.cos(st.a) * r1, cy + Math.sin(st.a) * r1 * 0.84);
      c.stroke();
    }

    /* 5) 溅出去的十字 */
    for (var n = 0; n < V.burstCrosses.length; n++) {
      var bc = V.burstCrosses[n];
      var ag2 = b - bc.delay;
      if (ag2 < 0) continue;
      var kk = 3.2;
      var rr = bc.v0 * (1 - Math.exp(-kk * ag2)) / kk;
      var x = cx + Math.cos(bc.a) * rr;
      var y = cy + Math.sin(bc.a) * rr * 0.88 - 22 * ag2;
      var life = bc.life;
      var fade = ss(0, 0.07, ag2) * (1 - ss(life * 0.45, life, ag2));
      drawSprite(c, V.crossImg, x, y, bc.size * (1 - 0.22 * Math.min(1, ag2 / life)),
        fade, bc.a + bc.spin * ag2);
    }

    /* 6) 细碎光点 */
    for (var s2 = 0; s2 < V.sparks.length; s2++) {
      var sk = V.sparks[s2];
      var ag3 = b - sk.delay;
      if (ag3 < 0 || ag3 > sk.life) continue;
      var k2 = 5.4;
      var r2 = sk.v0 * (1 - Math.exp(-k2 * ag3)) / k2;
      var x2 = cx + Math.cos(sk.a) * r2;
      var y2 = cy + Math.sin(sk.a) * r2 * 0.9;
      var al2 = 1 - ag3 / sk.life;
      c.fillStyle = "rgba(238,255,245," + (al2 * 0.9) + ")";
      c.beginPath(); c.arc(x2, y2, sk.size * al2, 0, TAU); c.fill();
    }
  }

  /* tier 2 的淡绿涟漪：只荡一圈，别抢 3 档 */
  function drawSoftRing(c, t, V) {
    if (!V.ring) return;
    var age = t - 0.30;
    if (age < 0 || age > 1.25) return;
    var p = clamp(age / 1.25, 0, 1);
    var R = 120 + 520 * eOut(p);
    var a = Math.pow(1 - p, 1.8) * 0.5;
    c.save();
    c.translate(DST.cx, DST.cy + DST.h * 0.30);
    c.scale(1, 0.30);
    c.strokeStyle = "rgba(46,160,106," + (a * 0.6) + ")";
    c.lineWidth = (10 * (1 - p) + 1.2) / 0.30;
    c.beginPath(); c.arc(0, 0, R, 0, TAU); c.stroke();
    c.strokeStyle = "rgba(248,255,250," + a + ")";
    c.lineWidth = (2.2 * (1 - p) + 0.6) / 0.30;
    c.beginPath(); c.arc(0, 0, R, 0, TAU); c.stroke();
    c.restore();
  }

  /* ================================================================
   * 飘字
   * ============================================================== */

  function bakeNumber(txt, px) {
    var S = 2;
    var font = "700 " + px + "px " + SERIF;
    var mc = bakeNumber._mc;
    if (!mc) { mc = bakeNumber._mc = document.createElement("canvas").getContext("2d"); }
    mc.font = font;
    var tw = Math.max(1, mc.measureText(txt).width);
    var padX = px * 0.26, padY = px * 0.34;
    var w = tw + padX * 2, h = px * 1.16 + padY * 2;
    var cv = offscreen(w * S, h * S), c = cv[1];
    c.scale(S, S);
    c.translate(w / 2, h / 2);
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.lineJoin = "round";
    c.font = font;

    /* 纸色柔光：把字从背后的绿里托出来，但看不出边 */
    var blur = px * 0.055;
    if (typeof c.filter === "string") {
      c.filter = "blur(" + blur.toFixed(1) + "px)";
      c.fillStyle = "rgba(255,253,247,0.95)";
      c.fillText(txt, 0, 0);
      c.fillText(txt, 0, 0);
      c.filter = "none";
    } else {
      c.strokeStyle = "rgba(255,253,247,0.85)";
      c.lineWidth = px * 0.06;
      c.strokeText(txt, 0, 0);
    }

    /* 字身：上白绿 → 中翠 → 下墨绿 */
    c.fillStyle = numGradient(c, px);
    c.fillText(txt, 0, 0);

    /* 深墨绿描边：白牌上必须有一圈压底，不然字会化进绿光里 */
    c.strokeStyle = "#0b3f29";
    c.lineWidth = px * 0.026;
    c.strokeText(txt, 0, 0);

    return { img: cv[0], w: w, h: h };
  }

  /* 飘字：屏幕 CSS 坐标（位置由 `create()` 按体力条下沿反推），字号已经在屏上。
   * `age` / `pop` / `rise` 全部吃**素材页秒数**（`draw()` 每帧先 warp 回来）。 */
  function drawNumber(c, t, V, sprite) {
    var age = t - V.numAt;
    if (age < 0 || age > V.numLife) return;

    var pop = eBack(clamp(age / 0.28, 0, 1));
    var rise = eOut(clamp(age / 1.70, 0, 1));
    var alpha = 1 - ss(V.numLife - 0.62, V.numLife, age);
    if (alpha <= 0.01) return;
    var size = V.numSize;
    var txt = "+" + V.amount;

    var x = lerp(V.numFrom.x, V.numTo.x, rise);
    var y = lerp(V.numFrom.y, V.numTo.y, rise);

    c.save();
    c.translate(x, y);
    c.rotate(-0.045 + (1 - rise) * 0.03);
    c.scale(0.60 + 0.40 * pop, 0.60 + 0.40 * pop);
    c.textAlign = "center";
    c.textBaseline = "middle";

    /* 青绿墨晕：把字从牌面上托起来（白牌上必须有一层压底） */
    c.save();
    c.globalCompositeOperation = "multiply";
    var dk = c.createRadialGradient(0, 0, 0, 0, 0, size * 1.18);
    dk.addColorStop(0, "rgba(96,178,134," + (0.26 * alpha) + ")");
    dk.addColorStop(0.45, "rgba(126,196,154," + (0.15 * alpha) + ")");
    dk.addColorStop(1, "rgba(126,196,154,0)");
    c.fillStyle = dk;
    c.beginPath(); c.arc(0, 0, size * 1.18, 0, TAU); c.fill();
    c.restore();

    /* 光晕 */
    var glowR = size * (0.92 + 0.06 * V.dmg);
    var gr = c.createRadialGradient(0, 0, 0, 0, 0, glowR);
    gr.addColorStop(0, "rgba(110,236,164," + (0.26 * alpha) + ")");
    gr.addColorStop(0.5, "rgba(70,214,144," + (0.13 * alpha) + ")");
    gr.addColorStop(1, "rgba(70,214,144,0)");
    c.fillStyle = gr;
    c.beginPath(); c.arc(0, 0, glowR, 0, TAU); c.fill();

    /* 残影（3 档）：偏右上，逐层变淡 */
    if (V.numEcho > 0) {
      for (var e = V.numEcho; e >= 1; e--) {
        c.save();
        c.globalAlpha = alpha * 0.18;
        c.font = "700 " + size + "px " + SERIF;
        c.fillStyle = numGradient(c, size);
        c.fillText(txt, e * size * 0.05, -e * size * 0.064);
        c.restore();
      }
    }

    c.font = "700 " + size + "px " + SERIF;
    c.globalAlpha = alpha;
    c.lineJoin = "round";

    /* 墨影 */
    c.globalAlpha = alpha * 0.34;
    c.fillStyle = "#0b3a26";
    c.fillText(txt, size * 0.030, size * 0.045);

    /* 纸色外晕（压住背后的绿） */
    c.globalAlpha = alpha * 0.85;
    c.strokeStyle = "rgba(255,253,247,0.9)";
    c.lineWidth = size * 0.040;
    c.strokeText(txt, 0, 0);

    /* 深墨绿描边 */
    c.globalAlpha = alpha;
    c.strokeStyle = "#0b3f29";
    c.lineWidth = size * 0.026;
    c.strokeText(txt, 0, 0);

    /* 字身 */
    c.fillStyle = numGradient(c, size);
    c.fillText(txt, 0, 0);

    /* 3 档起：数字脚下往上飘的绿光点 */
    if (V.dmg >= 3) {
      for (var i = 0; i < 10; i++) {
        var ph = ((age * 0.5 + i * 0.137) % 1);
        var px = (hash(i * 3.1 + V.dmg) - 0.5) * size * 2.2;
        var py = size * 0.62 - ph * size * 2.3;
        c.globalAlpha = alpha * 0.6 * (1 - ph);
        c.fillStyle = "rgba(120,240,170,0.9)";
        c.beginPath();
        c.arc(px, py, 1.6 + 2.0 * hash(i * 7.7), 0, TAU);
        c.fill();
      }
    }

    /* 小字「回复体力」：随字一起浮起来 */
    var sa = ss(0.06, 0.22, age) * alpha;
    if (sa > 0.01) {
      c.globalAlpha = sa;
      c.font = "600 " + Math.round(size * 0.205) + "px " + SERIF;
      if ("letterSpacing" in c) c.letterSpacing = (size * 0.035) + "px";
      c.lineWidth = size * 0.026;
      c.strokeStyle = "rgba(252,255,253,0.92)";
      c.strokeText(V.label, 0, size * 0.73);
      c.fillStyle = "#0d4a30";
      c.fillText(V.label, 0, size * 0.73);
      if ("letterSpacing" in c) c.letterSpacing = "0px";
    }
    c.restore();
  }

  /* ================================================================
   * 白闪 / 屏震 / 镜头
   * ============================================================== */
  function flashStrike(age, peak, hold, decay) {
    if (age < 0) return 0;
    return peak * ss(0, 0.004, age) * Math.exp(-Math.max(0, age - hold) * decay);
  }

  /* 迸发那一记白闪。素材页是**整屏**（`fillRect(0, 0, W, H)`）；牌桌档收成**牌周围一圈
   * 径向光**——覆盖层是透明的，整屏铺白就是"整屏闪"（spec §硬约束不允许），而且会把牌桌
   * 刷白。径向渐变的边缘 alpha 是 0 ⇒ 画布外缘不着色，只在牌周围亮一下。导出档照素材页
   * 1:1（参考舞台就是整幅画面）。 */
  function drawFlash(c, t, V) {
    if (!V.burst) return;
    var age = t - V.burst.at;
    if (age < 0) return;
    var a = Math.max(
      flashStrike(age, 0.26, 0.012, 17),
      flashStrike(age - 0.05, 0.12, 0.006, 22)
    );
    if (a <= 0.004) return;
    var peak = Math.min(0.9, a);
    if (BARE) {
      var R = DST.w * 2.6;
      var g = c.createRadialGradient(DST.cx, DST.cy, 0, DST.cx, DST.cy, R);
      g.addColorStop(0, "rgba(246,255,249," + peak + ")");
      g.addColorStop(0.45, "rgba(235,255,242," + (peak * 0.55) + ")");
      g.addColorStop(1, "rgba(218,252,231,0)");
      c.fillStyle = g;
      c.fillRect(DST.cx - R, DST.cy - R, R * 2, R * 2);
      return;
    }
    var ge = c.createRadialGradient(DST.cx, DST.cy, 0, DST.cx, DST.cy, 1180);
    ge.addColorStop(0, "rgba(246,255,249," + peak + ")");
    ge.addColorStop(0.55, "rgba(235,255,242," + (peak * 0.9) + ")");
    ge.addColorStop(1, "rgba(218,252,231," + (peak * 0.8) + ")");
    c.fillStyle = ge;
    c.fillRect(0, 0, W, H);
  }

  function shakeAt(t, V) {
    if (!V.burst) return { x: 0, y: 0 };
    var a = t - V.burst.at;
    if (a < 0) return { x: 0, y: 0 };
    var k = Math.exp(-a * 9) * (1 - Math.exp(-a * 34));
    return {
      x: Math.sin(a * 68 + 0.4) * V.burst.shake * k,
      y: Math.cos(a * 57) * V.burst.shake * 0.7 * k
    };
  }

  function cardKick(t, V) {
    /* 聚气时牌轻轻收一口气，爆发时轻轻弹一下 —— 回复是"松"，不是"砸" */
    var s = 1, dy = 0;
    if (V.gather) {
      var g0 = V.gather.at, g1 = g0 + V.gather.dur;
      if (t >= g0 && t < g1) s -= 0.012 * eIn((t - g0) / V.gather.dur);
      else if (t >= g1 && t < V.burst.at) s -= 0.014;   /* 停顿：憋住 */
      var b = t - V.burst.at;
      if (b >= 0) {
        s += 0.016 * Math.exp(-7 * b) * (1 - Math.exp(-30 * b));
        dy -= 3 * Math.exp(-6 * b) * (1 - Math.exp(-30 * b));
      }
    } else {
      var a = t - V.glowAt;
      if (a > 0) {
        s += 0.004 * ss(0, 0.5, a) * Math.exp(-Math.max(0, a - 0.8) / 1.2);
      }
    }
    return { s: s, dy: dy };
  }

  /* ================================================================
   * 入口（与 `SgsHpLossFX.create` / `SgsFireFX.create` 同形：排播层不认第二种形状）
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
    /* 先切帧上下文：下面的贴图 / 白闪读 W / H 与 BARE。 */
    setFrame(instW, instH, bare);

    var stage = stageFor(opts.stage);
    var cr = stage.rect;
    var scale = stage.s;

    /* 每实例一份可变的档位对象：几何（字号 / 飘字落点）随座位走，别污染 `TIERS`
     * （同一条家族的几条演出可以落在不同大小的牌上）。 */
    var V = copy(clock.variant, {});
    V.label = "回复体力";
    /* `draw()` 里画的是**素材页秒数**（牌桌档每帧先 warp 回来），故 `duration` 必须一直是
     * 素材页那个数（3.20 / 3.55 / 4.45）——换成牌桌秒数就是两套钟。 */
    V.duration = base.duration;
    var amount = Math.round(Number(opts.amount));
    V.amount = isFinite(amount) && amount > 0 ? amount : base.dmg;

    /* 飘字：字号照素材页按牌宽缩，再按 `fx-core.js` 的包围盒判据两头夹
     * （字高 ∈ [屏上 44px, 35% × 牌高]）；位置由体力条下沿反推。 */
    var cardH = cr.h;
    var numBox = { top: 0, bottom: 0, height: 0 };
    var keepOutPx = null;
    if (bare) {
      var glyph = clamp(base.numSize * scale * GLYPH_RATIO,
        SCREEN_MIN_NUMBER, NUMBER_MAX_RATIO * cardH);
      V.numSize = glyph / GLYPH_RATIO;
      V.numGlyph = glyph;
      var keepOut = Number(opts.numberKeepOut);
      var keepOutRatio = isFinite(keepOut) && keepOut > 0 && keepOut < cardH
        ? keepOut / cardH
        : NUMBER_KEEP_OUT_FALLBACK;
      keepOutPx = keepOutRatio * cardH;
      /* 包围盒顶边 = 体力条下沿 + 3% 牌高（余量留给字身与描边）。 */
      var boxTop = cr.y + Math.min(0.88, keepOutRatio + NUMBER_GAP_RATIO) * cardH;
      V.numTo = { x: cr.x + cr.w * 0.5 + 4 * scale, y: boxTop + glyph / 2 };
      /* 从硬线下方浮上来：上浮到顶那一刻包围盒顶边正好落在硬线上 ⇒ 任意时刻都不压体力条。 */
      V.numFrom = { x: V.numTo.x, y: V.numTo.y + NUM_RISE * scale };
      numBox = { top: boxTop - cr.y, bottom: boxTop + glyph - cr.y, height: glyph };
    } else {
      /* 导出档：沿用素材页的落点（牌心上方 10 → 160 设计像素，按舞台映射）。 */
      V.numSize = base.numSize * scale;
      V.numGlyph = base.numSize * GLYPH_RATIO * scale;
      V.numTo = { x: cr.x + cr.w * 0.5 + 4 * scale, y: cr.y + cr.h * 0.5 - 160 * scale };
      V.numFrom = { x: V.numTo.x, y: cr.y + cr.h * 0.5 - 10 * scale };
      numBox = {
        top: V.numTo.y - V.numGlyph / 2 - cr.y,
        bottom: V.numTo.y + V.numGlyph / 2 - cr.y,
        height: V.numGlyph
      };
      keepOutPx = null;
    }

    /* 十字 / 光尾 / 气核（固定种子）：三档的密度与大小都在 `TIERS` 里。 */
    var r = rng(7300 + id * 613);
    V.crosses = [];
    for (var i = 0; i < base.crossN; i++) {
      V.crosses.push({
        u: 0.10 + r() * 0.80,
        y0: 0.58 + r() * 0.42,
        h: 120 + r() * 210,
        spd: 56 + r() * 60,
        size: (18 + r() * 15) * base.crossSize,
        delay: 0.10 + r() * 0.85,
        ph: r() * TAU,
        life: base.crossLife * (0.82 + r() * 0.36)
      });
    }

    V.gatherParts = [];
    if (base.gather) {
      /* 四面八方：角度按圈分层撒开（避免结块），半径和出发时间各自错开，所以不会在某一个
       * 半径上排成一个环。 */
      var N = 112;
      for (var gi = 0; gi < N; gi++) {
        var layer = gi % 3;
        V.gatherParts.push({
          a: gi / N * TAU + (r() - 0.5) * 0.22 + layer * 0.09,
          r0: 300 + r() * 330,
          delay: r() * 0.46,
          travel: 0.40 + r() * 0.34,
          spin: (r() - 0.5) * 1.7,
          size: 12 + r() * 17,
          ph: r() * TAU,
          trail: 0.7 + r() * 1.1
        });
      }
    }

    V.burstCrosses = [];
    V.sparks = [];
    V.streaks = [];
    V.waveCrosses = [];
    if (base.burst) {
      for (var wi = 0; wi < 44; wi++) {
        V.waveCrosses.push({
          a: wi / 44 * TAU + (r() - 0.5) * 0.08,
          off: (r() - 0.5) * 34,
          size: 15 + r() * 13
        });
      }
      for (var j = 0; j < 120; j++) {
        V.burstCrosses.push({
          a: r() * TAU,
          v0: 600 + r() * 1200,
          size: 10 + r() * 18,
          spin: (r() - 0.5) * 2.6,
          delay: r() * 0.09,
          life: 1.35 + r() * 1.15
        });
      }
      for (var k = 0; k < 70; k++) {
        V.sparks.push({
          a: r() * TAU,
          v0: 380 + r() * 1300,
          size: 2 + r() * 5,
          delay: r() * 0.10,
          life: 0.7 + r() * 1.0
        });
      }
      for (var m = 0; m < 46; m++) {
        V.streaks.push({
          a: r() * TAU,
          r0: 60 + r() * 110,
          len: 240 + r() * 520,
          w: 1 + r() * 2.4,
          delay: r() * 0.06,
          life: 0.5 + r() * 0.45
        });
      }
    }

    canvas.width = Math.round(instW * dpr);
    canvas.height = Math.round(instH * dpr);
    var c = canvas.getContext("2d");

    /* 三张静态图（宣纸底 / 样例武将牌 / 十字 / 光尾）：牌桌档 `bare` 一概不画前两张；
     * 四张都只读、跨实例复用（每次回复都重画 1920×1080 的纸底是主线卡顿）。 */
    var paper = bare ? null
      : texture("healPaper@" + dpr, function () { return paintPaper(dpr); });
    var heroImg = bare ? null
      : texture("healHero@" + dpr, function () { return paintHero(HERO, dpr); });
    var crossImg = texture("healCross", function () { return paintCross(128); });
    var trailImg = texture("healTrail", paintTrail);
    V.crossImg = crossImg;
    V.trailImg = trailImg;
    var sprite = bakeNumber("+" + V.amount, Math.max(24, Math.round(V.numSize)));

    function draw(t) {
      t = clamp(t, 0, totalSeconds);
      // 牌桌档：先把演出自己的秒数映射回素材页的秒数（整条链按锚点压，画法一行不改）。
      if (clock.warp) t = warpAt(clock.warp, t);
      setFrame(instW, instH, bare);

      var shake = shakeAt(t, V);
      var kick = cardKick(t, V);

      c.setTransform(dpr, 0, 0, dpr, 0, 0);
      c.clearRect(0, 0, instW, instH);
      c.save();
      /* 屏震按**屏幕**像素走（素材页是设计像素的 7px，牌变小了要一起缩）。 */
      c.translate(shake.x * scale, shake.y * scale);

      if (!bare) c.drawImage(paper, -80, -80, instW + 160, instH + 160);

      /* 进设计坐标：把参考牌面精确映射到真实座位上（§3 B2 的 s = 块宽 / 515），于是下面
       * 每一笔都还是素材页的写法与单位。 */
      c.save();
      c.translate(stage.dst.cx, stage.dst.cy);
      c.scale(scale, scale);
      c.translate(-DST.cx, -DST.cy);

      /* 牌外的光要在牌之前画，牌内的光在牌之后画 */
      drawAura(c, t, V);

      /* 样例武将牌只在导出档：牌桌档的真实牌是 DOM，不归本模块移动（牌桌档的"牌在发光"
       * 由 `drawAura` 的贴边亮芯与 `drawCardLight` 的白化表达）。 */
      if (!bare && heroImg) {
        c.save();
        c.translate(DST.cx, DST.cy + kick.dy);
        c.scale(kick.s, kick.s);
        c.drawImage(heroImg,
          -DST.w / 2 - CARD.pad, -DST.h / 2 - CARD.pad,
          DST.w + CARD.pad * 2, DST.h + CARD.pad * 2);
        c.restore();
      }

      drawCardLight(c, t, V);
      drawSoftRing(c, t, V);
      drawRisingCrosses(c, t, V);
      drawGather(c, t, V);
      drawBurst(c, t, V);
      drawFlash(c, t, V);

      c.restore();

      /* 飘字在**屏幕**坐标里画：字号与位置是照真实牌高算好反推的（包围盒判据在
       * `create()` 里），不跟着设计变换走（否则小牌上的字会小到看不见）。 */
      drawNumber(c, t, V, sprite);

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
      timeline: clock.timeline,
      timing: clock.ms,
      stage: stage,
      bare: bare,
      metrics: {
        u: scale,
        keepOut: keepOutPx,
        number: numBox.height,
        numberBox: numBox,
        card: { w: cr.w, h: cr.h },
        /* bare 下白化的峰值（**半透明白**叠在覆盖层上；最终值以实机为准）。 */
        whiteAlpha: bare ? BARE_WHITE_PEAK : 1,
        /* 飘字贴图的完整文本（探针 / 实机读数用它判"写的是真实点数"）。 */
        numberText: "+" + V.amount
      }
    };
  }

  global.SgsHealFX = {
    create: create,
    TIERS: TIERS,
    COLORS: C,
    // 与 `SgsDamageFX` / `SgsHpLossFX` 同名的导出面（排播层按字段表 / 时长取）。
    DEFAULT_TIMING: DEFAULT_TIMING,
    MAX_TOTAL_MS: MAX_TOTAL_MS,
    TIMING_PREFIX: TIMING_PREFIX,
    TIMING_FIELDS: TIMING_FIELDS,
    TIMING_KEYS: TIMING_KEYS,
    gameTiming: gameTiming,
    SCREEN_MIN: { number: SCREEN_MIN_NUMBER },
    BARE_WHITE_PEAK: BARE_WHITE_PEAK
  };
})(window);
