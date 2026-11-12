"use strict";
/* ==========================================================================
   ink-bg.js —— 牌桌的水墨山水动态底（ink-table-skin 票 01）

   来源：`outputs/sgs-ink-bg/index.html`（1920×1080 设计坐标 / 90 秒无缝 / 程序生成 /
   零外部资源）。**绘制代码一字未改**，只换了"谁来开、按谁的量、什么时候停"：

     window.SgsInkBg.mount(canvas)   // 载入期什么都不做，第一次 mount 才烘静态层
     window.SgsInkBg.pause()/.resume()/.destroy()
     window.SgsInkBg.state()         // { mounted, running, reduced, seconds } —— 烟测的唯一读法
     window.SgsInkBg.draw(t)         // 定帧（t 秒）；同一 t 像素一致

   它在 `#table-shell` 里当固定底（`position:absolute; inset:0; z-index:0`），
   铺满壳 = 16:9，与设计坐标同构 ⇒ **零裁切**。不吃指针、不发请求、不碰 DOM 结构。
   `prefers-reduced-motion: reduce` 时只画一帧，running 恒 false。
   ========================================================================== */


/* ══════════════════════════════════════════════════════════════════════════
   SGS · 水墨山水 动态背景（16:9 / 1920×1080 设计坐标 / 程序生成 / 零外部资源）

   观感：生宣底 + 三叠远山 + 云气留白 + 一片空江；左重右空，右上留白给雁。
   动静：山 / 岸 / 松 / 亭 / 印 = 静；雾 / 水光 / 小舟 = 缓动；
         雁阵 + 镜头前落叶 = 随机小动画（固定种子排程，无交互）。
   接口：window.renderAt(t) 由秒数唯一推进 ⇒ 同一 t 渲染一致；
         ?t=12.5 单帧 / ?export=1 不启循环 / 其余自动播放。
   ══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  /* ─────────────────────────── 可调配置 ─────────────────────────── */

  const CONFIG = {
    design: { w: 1920, h: 1080 },        // 设计坐标（16:9），输出按 cover 铺满视口
    loop: 90,                            // 无缝循环周期（秒）
    waterline: 0.615,                    // 水天分界（0–1，设计坐标高）

    seed: {                              // 全部随机数的固定种子
      paper: 20260301,
      far: 1301, mid: 2707, near: 4519,
      rock: 7717, shore: 3313, water: 9091, pavilion: 617,
      schedule: 88103,
    },

    palette: {
      paper: '#F2ECDE',                  // 生宣底
      paperRGB: '242,236,222',
      ink: '25,21,18',                   // 焦墨（暖黑）
      leaf: '104,68,41',                 // 赭墨（落叶：一点暖色，别成为画面里唯一的黑）
      accent: '#A63425',                 // 朱砂（印章）
    },

    paper: { blotches: 13, fibersPer: 2400, specksPer: 4000, vignette: 0.060, grain: 0.055 },

    // 三叠远山：base 山脚基线 / alpha 墨度 / rough 山脊起伏 / peaks [峰心x, 峰高, 半宽]（归一化）
    ranges: [
      { key: 'far',  base: 0.470, alpha: 0.140, rough: 0.042, strokes: 55,  edge: 0, blobs: 26, moss: 0,
        peaks: [[0.075, 0.115, 0.145], [0.215, 0.175, 0.135], [0.355, 0.140, 0.125], [0.495, 0.085, 0.120], [0.68, 0.050, 0.150], [0.85, 0.032, 0.130]] },
      { key: 'mid',  base: 0.545, alpha: 0.215, rough: 0.055, strokes: 110, edge: 1, blobs: 34, moss: 0,
        peaks: [[0.060, 0.160, 0.135], [0.170, 0.215, 0.115], [0.300, 0.150, 0.110], [0.430, 0.082, 0.100], [0.600, 0.042, 0.130]] },
      { key: 'near', base: 0.625, alpha: 0.310, rough: 0.070, strokes: 165, edge: 2, blobs: 40, moss: 26,
        peaks: [[0.030, 0.150, 0.120], [0.140, 0.235, 0.115], [0.275, 0.128, 0.100], [0.395, 0.078, 0.085]] },
    ],

    mistBands: [                               // 云气横带：基线 / 浓淡 / 厚度
      { y: 0.512, a: 0.17, t: 0.062, seed: 233 },
      { y: 0.630, a: 0.20, t: 0.070, seed: 257 },
    ],

    boat: { x0: 0.285, x1: 0.605, y: 0.712, len: 46 },
    pavilion: { enabled: true, u: 0.72, w: 36, alpha: 0.46 },     // 远山古亭
    farShore: { enabled: true, y: 0.600, trees: 7 },              // 右侧对岸小松
    seal: { enabled: true, x: 0.947, y: 0.880, size: 38, alpha: 0.46 },
    // 镜头呼吸：走 CSS（合成器变换，不重采样画布）或干脆关掉。
    // 'css' 才要每帧写一次 style，开销小；'off' 最省。
    camera: { mode: 'css', zoom: 0.010, panX: 7, panY: 4 },

    // 画质上限：高 DPI 屏别把 backing store 撑爆（内存 / 每帧带宽都按它算）
    quality: { maxDpr: 1.5, maxPixels: 4.2e6 },

    // 雁阵：把循环切成 slot 秒一段，每段按 chance 概率起一队
    birds: { slot: 15, chance: 0.85, transit: 13.5, yMin: 0.14, yMax: 0.31, maxBirds: 6 },
    // 落叶：镜头前飘过，随机出现
    leaves: { slot: 5, chance: 0.42, transitMin: 4.4, transitMax: 6.8 },
  };

  /* ─────────────────────────── 小工具 ─────────────────────────── */

  const DW = CONFIG.design.w, DH = CONFIG.design.h;
  const TAU = Math.PI * 2;
  const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
  const lerp = (a, b, k) => a + (b - a) * k;
  const ss = (a, b, x) => { const k = clamp((x - a) / (b - a), 0, 1); return k * k * (3 - 2 * k); };
  const mod = (a, n) => ((a % n) + n) % n;

  const ink = (a) => `rgba(${CONFIG.palette.ink},${clamp(a, 0, 1)})`;
  const paper = (a) => `rgba(${CONFIG.palette.paperRGB},${clamp(a, 0, 1)})`;
  const leafInk = (a) => `rgba(${CONFIG.palette.leaf},${clamp(a, 0, 1)})`;

  // mulberry32：同一种子 ⇒ 同一序列
  function rng(seed) {
    let a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function hash1(i, seed) {
    const x = Math.sin(i * 127.1 + seed * 311.7) * 43758.5453;
    return x - Math.floor(x);
  }

  function vnoise(x, seed) {
    const i = Math.floor(x), f = x - i, s = f * f * (3 - 2 * f);
    const a = hash1(i, seed);
    return a + (hash1(i + 1, seed) - a) * s;
  }

  // 一维分形噪声，返回 -0.5 … 0.5
  function fbm(x, seed, oct) {
    let v = 0, amp = 0.5, f = 1, w = 0;
    for (let i = 0; i < oct; i++) {
      v += (vnoise(x * f, seed + i * 19) - 0.5) * amp;
      w += amp; amp *= 0.54; f *= 2.11;
    }
    return v / w;
  }

  function layer(w, h) {
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    return [c, c.getContext('2d')];
  }

  function roundRectPath(x, px, py, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    x.beginPath();
    x.moveTo(px + r, py);
    x.lineTo(px + w - r, py);
    x.quadraticCurveTo(px + w, py, px + w, py + r);
    x.lineTo(px + w, py + h - r);
    x.quadraticCurveTo(px + w, py + h, px + w - r, py + h);
    x.lineTo(px + r, py + h);
    x.quadraticCurveTo(px, py + h, px, py + h - r);
    x.lineTo(px, py + r);
    x.quadraticCurveTo(px, py, px + r, py);
    x.closePath();
  }

  // 一圈柔边：椭圆径向渐变（云的唯一画法，避免出现横带硬边）
  function softEllipse(x, cx, cy, rx, ry, color, peak) {
    x.save();
    x.translate(cx, cy);
    x.scale(1, ry / rx);
    const g = x.createRadialGradient(0, 0, 0, 0, 0, rx);
    g.addColorStop(0, color(peak));
    g.addColorStop(0.42, color(peak * 0.62));
    g.addColorStop(0.74, color(peak * 0.20));
    g.addColorStop(1, color(0));
    x.fillStyle = g;
    x.beginPath(); x.arc(0, 0, rx, 0, TAU); x.fill();
    x.restore();
  }

  /* ─────────────────────────── 运行态 ─────────────────────────── */

  let cv = null;                               /* 由 mount(canvas) 给 */
  let ctx = null;
  const FILTER_OK = (function () {
    try { const c = document.createElement('canvas').getContext('2d'); return typeof c.filter === 'string'; }
    catch (e) { return false; }
  })();

  let VW = 0, VH = 0;                           // 视口（CSS 像素）
  let DPR = 1, COVER = 1;                       // 像素比 / cover 系数
  let S = 1, OX = 0, OY = 0;                    // 设计坐标 → 设备像素的缩放与偏移
  let staticLayer = null;                       // 静态层 [canvas, ctx]：与画布同尺寸，1:1 贴
  let leafSprites = null, mistSprite = null;    // 预烘贴图（叶 / 雾）
  const DIRTY_PAD = 10;                         // 设备像素；还原时扩边，宁可多还原一点
  let dirtyRects = [];                          // 上一帧画过的区域（设备像素 x,y,w,h）
  let frameRects = [];                          // 本帧画过的区域（设计坐标）
  let needFullBlit = true;                      // 重建静态层之��要整屏贴一次
  let shimmer = [];                             // 参与"水光"呼吸的水纹
  const events = { birds: [], leaves: [] };     // 固定种子排好的事件表
  let ready = false;
  let lastT = 0;                                // 最近一次绘制用的 t（改尺寸后照它重画）

  /* ─────────────────────────── 宣纸 ─────────────────────────── */

  let grainPattern = null;

  function makeGrain() {
    const gs = 200;
    const [c, g] = layer(gs, gs);
    const img = g.createImageData(gs, gs);
    const r = rng(CONFIG.seed.paper + 1);
    for (let i = 0; i < gs * gs; i++) {
      const v = 128 + (r() - 0.5) * 96;
      const o = i * 4;
      img.data[o] = img.data[o + 1] = img.data[o + 2] = v;
      img.data[o + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    return c;
  }

  function paintPaper(x) {
    const r = rng(CONFIG.seed.paper);
    const P = CONFIG.paper;

    x.fillStyle = CONFIG.palette.paper;
    x.fillRect(0, 0, DW, DH);

    // 云斑：大块极淡明暗，纸才有"厚度"
    for (let i = 0; i < P.blotches; i++) {
      const cx = r() * DW, cy = r() * DH, rad = (0.16 + r() * 0.34) * Math.max(DW, DH);
      const dark = r() < 0.52;
      const g = x.createRadialGradient(cx, cy, 0, cx, cy, rad);
      g.addColorStop(0, dark ? 'rgba(120,100,70,.038)' : 'rgba(255,252,244,.046)');
      g.addColorStop(0.62, dark ? 'rgba(120,100,70,.015)' : 'rgba(255,252,244,.017)');
      g.addColorStop(1, 'rgba(0,0,0,0)');
      x.fillStyle = g;
      x.beginPath(); x.arc(cx, cy, rad, 0, TAU); x.fill();
    }

    // 天光：顶部浮一层极淡的亮，给留白一点空气感
    const sky = x.createLinearGradient(0, 0, 0, DH * 0.64);
    sky.addColorStop(0, 'rgba(255,253,247,.34)');
    sky.addColorStop(0.5, 'rgba(255,253,247,.14)');
    sky.addColorStop(1, 'rgba(255,253,247,0)');
    x.fillStyle = sky;
    x.fillRect(0, 0, DW, DH * 0.64);

    // 纤维
    const fibers = Math.round((DW * DH) / P.fibersPer);
    x.lineWidth = 0.55;
    for (let i = 0; i < fibers; i++) {
      const px = r() * DW, py = r() * DH, a = r() * TAU, l = 3 + r() * 17;
      x.strokeStyle = r() < 0.5 ? 'rgba(96,78,52,.050)' : 'rgba(255,255,250,.070)';
      x.beginPath();
      x.moveTo(px, py);
      x.quadraticCurveTo(px + Math.cos(a) * l * 0.5 + (r() - 0.5) * 3, py + Math.sin(a) * l * 0.5,
                         px + Math.cos(a) * l, py + Math.sin(a) * l);
      x.stroke();
    }

    // 纸屑 / 草筋
    const specks = Math.round((DW * DH) / P.specksPer);
    for (let i = 0; i < specks; i++) {
      x.fillStyle = 'rgba(70,55,35,.070)';
      x.fillRect(r() * DW, r() * DH, 0.9 + r() * 1.7, 0.7 + r() * 1.2);
    }

    // 纸纹颗粒（细砂目）
    if (!grainPattern) grainPattern = x.createPattern(makeGrain(), 'repeat');
    x.save();
    x.globalCompositeOperation = 'multiply';
    x.globalAlpha = P.grain;
    x.fillStyle = grainPattern;
    x.fillRect(0, 0, DW, DH);
    x.restore();

    // 极轻暗角（背景永远退到字后面）
    const vg = x.createRadialGradient(DW * 0.5, DH * 0.46, Math.min(DW, DH) * 0.36, DW * 0.5, DH * 0.46, Math.max(DW, DH) * 0.82);
    vg.addColorStop(0, 'rgba(0,0,0,0)');
    vg.addColorStop(1, `rgba(96,74,44,${P.vignette})`);
    x.fillStyle = vg;
    x.fillRect(0, 0, DW, DH);
  }

  /* ─────────────────────────── 远山 ─────────────────────────── */

  function ridgePts(spec) {
    const seed = CONFIG.seed[spec.key];
    const N = 560;
    const pts = [];
    for (let i = 0; i <= N; i++) {
      const u = i / N;
      let bump = 0;
      for (const [px, ph, pw] of spec.peaks) {
        const skew = (hash1(px * 97, seed) - 0.5) * 0.62;                     // 峰往一边偏
        const pwL = pw * (1 - skew * 0.55), pwR = pw * (1 + skew * 0.55);
        const du = u - px;
        const d = du < 0 ? -du / pwL : du / pwR;
        if (d < 1) {
          const sharp = 0.80 + hash1(px * 41, seed) * 0.34;                   // 尖顶程度各不相同
          const shape = Math.pow(1 - d, sharp);
          const mod = 0.78 + 0.44 * (fbm(u * 11 + px * 31, seed, 3) + 0.5);   // 峰形各自不同
          bump = Math.max(bump, ph * shape * mod);
        }
      }
      const rough = fbm(u * 5.2, seed + 5, 4) * spec.rough;                   // 山脊大起伏
      const fine = fbm(u * 24.0, seed + 9, 2) * spec.rough * 0.18;            // 碎岩
      pts.push([u * DW, (spec.base - bump + rough + fine) * DH]);
    }
    return { pts, seed };
  }

  function paintRange(x, spec) {
    const { pts, seed } = ridgePts(spec);
    const r = rng(seed * 7 + 13);
    const baseY = spec.base * DH;
    let ridgeTop = Infinity;
    for (const p of pts) ridgeTop = Math.min(ridgeTop, p[1]);

    // ① 山体淡墨：脊线处浓、向下化开（先立住轮廓）
    const g = x.createLinearGradient(0, ridgeTop - 6, 0, baseY + 0.04 * DH);
    g.addColorStop(0, ink(spec.alpha * 0.52));
    g.addColorStop(0.30, ink(spec.alpha * 0.36));
    g.addColorStop(0.68, ink(spec.alpha * 0.17));
    g.addColorStop(1, ink(spec.alpha * 0.05));
    x.beginPath();
    x.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) x.lineTo(pts[i][0], pts[i][1]);
    x.lineTo(DW, DH); x.lineTo(0, DH);
    x.closePath();
    x.fillStyle = g;
    x.fill();

    // ② 贴脊湿墨：把「脊线 → 脊线下 offset」这条带层层叠起来，
    //    墨色于是跟着山脊走（不是一堆圆斑），越贴脊越浓、越往下越淡。
    const layers = 12;
    const stepY = 7;
    x.fillStyle = ink(spec.alpha * 0.050);
    for (let k = layers - 1; k >= 0; k--) {
      x.beginPath();
      // 每一层的下沿按噪声深浅不一 ⇒ 墨是"沉"在脊下的，不是一条均匀的暗边
      for (let i = 0; i < pts.length; i++) {
        const d = (k + 1) * stepY * (0.45 + 1.15 * vnoise(i * 0.038 + k * 2.7, seed));
        const y = pts[i][1] + d;
        if (i === 0) x.moveTo(pts[i][0], y); else x.lineTo(pts[i][0], y);
      }
      for (let i = pts.length - 1; i >= 0; i--) x.lineTo(pts[i][0], pts[i][1]);
      x.closePath();
      x.fill();
    }

    // ③ 山体内的墨团：湿气与石面，避免大面积平涂
    for (let i = 0; i < spec.blobs; i++) {
      const [px, py] = pts[Math.floor(r() * pts.length)];
      const cy = py + Math.pow(r(), 1.4) * 0.13 * DH;
      const rr = 26 + r() * 80;
      const gg = x.createRadialGradient(px, cy, 0, px, cy, rr);
      gg.addColorStop(0, ink(spec.alpha * (0.05 + r() * 0.10)));
      gg.addColorStop(0.5, ink(spec.alpha * (0.02 + r() * 0.05)));
      gg.addColorStop(1, ink(0));
      x.fillStyle = gg;
      x.beginPath(); x.arc(px, cy, rr, 0, TAU); x.fill();
    }

    // ④ 勾：脊线焦墨（只在近中两叠）。先一道连续的细脊线立住骨相，
    //    再叠断笔与重笔，才不是一条矢量曲线。
    if (spec.edge) {
      const thick = spec.edge === 2 ? 1.35 : 1.0;
      x.beginPath();
      x.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) x.lineTo(pts[i][0], pts[i][1]);
      x.strokeStyle = ink(Math.min(0.56, spec.alpha * 1.62));
      x.lineWidth = thick;
      x.lineJoin = 'round';
      x.stroke();
      for (let i = 0; i < pts.length - 2; i += 2) {
        if (r() < 0.30) continue;                                   // 飞白
        const a0 = pts[i], a1 = pts[i + 2];
        x.strokeStyle = ink(Math.min(0.62, spec.alpha * (1.15 + r() * 1.35)));
        x.lineWidth = thick * (0.65 + r() * 1.0);
        x.lineCap = 'round';
        x.beginPath();
        x.moveTo(a0[0], a0[1] + (r() - 0.5) * 1.8);
        x.lineTo(a1[0], a1[1] + (r() - 0.5) * 1.8);
        x.stroke();
      }
      // 焦墨重笔：局部加粗的山脊（像笔根下压）
      for (let i = 0; i < 14; i++) {
        const k = Math.floor(r() * (pts.length - 8));
        const a0 = pts[k], a1 = pts[k + 6];
        x.strokeStyle = ink(Math.min(0.72, spec.alpha * (1.6 + r() * 1.2)));
        x.lineWidth = thick * (1.6 + r() * 1.9);
        x.beginPath();
        x.moveTo(a0[0], a0[1] + 1);
        x.lineTo(a1[0], a1[1] + 1);
        x.stroke();
      }
    }

    // ⑤ 披麻皴：成组的顺坡短笔（不是一根根散着画，否则像下雨）
    const clusters = Math.round(spec.strokes / 6);
    for (let c = 0; c < clusters; c++) {
      const [cx, cy] = pts[Math.floor(r() * pts.length)];
      const baseDepth = Math.pow(r(), 1.6) * 0.10 * DH;
      const lean = (r() - 0.5) * 0.85;
      const n = 3 + Math.floor(r() * 4);
      for (let j = 0; j < n; j++) {
        const px = cx + (j - n / 2) * (3 + r() * 5) + (r() - 0.5) * 6;
        const len = 12 + r() * 24;
        x.strokeStyle = ink(spec.alpha * (0.10 + r() * 0.26));
        x.lineWidth = 0.85 + r() * 1.0;
        x.lineCap = 'round';
        x.beginPath();
        x.moveTo(px, cy + baseDepth + r() * 6);
        x.lineTo(px + lean * len * 0.9, cy + baseDepth + len);
        x.stroke();
      }
    }

    // ⑤b 干笔擦：贴着山脊的极淡短擦，出"毛"的纸感
    for (let i = 0; i < spec.strokes * 0.55; i++) {
      const [px, py] = pts[Math.floor(r() * pts.length)];
      const dy = Math.pow(r(), 2.0) * 0.06 * DH;
      const len = 8 + r() * 26;
      x.strokeStyle = ink(spec.alpha * (0.05 + r() * 0.13));
      x.lineWidth = 0.7 + r() * 0.7;
      x.beginPath();
      x.moveTo(px, py + dy);
      x.lineTo(px + len, py + dy + (r() - 0.5) * 2.4);
      x.stroke();
    }

    // ⑥ 苔点：只在近山，成小簇
    if (spec.moss > 0) {
      for (let i = 0; i < spec.moss; i++) {
        const [px, py] = pts[Math.floor(r() * pts.length)];
        const cluster = 2 + Math.floor(r() * 3);
        for (let j = 0; j < cluster; j++) {
          x.fillStyle = ink(0.20 + r() * 0.30);
          x.beginPath();
          x.arc(px + (r() - 0.5) * 20, py + 1 + r() * 9, 0.8 + r() * 1.5, 0, TAU);
          x.fill();
        }
      }
    }
    return pts;
  }

  // 云气：一串柔边椭圆叠成的横带（没有硬边），把三叠山分开
  function mistBand(x, band) {
    const r = rng(band.seed);
    const y = band.y * DH;
    const n = 5;
    for (let i = 0; i < n; i++) {
      const cx = ((i + 0.5) / n) * DW + (r() - 0.5) * DW * 0.16;
      const cy = y + (r() - 0.5) * band.t * DH * 0.9;
      const rx = DW * (0.10 + r() * 0.11);
      const ry = band.t * DH * (0.5 + r() * 0.5);
      softEllipse(x, cx, cy, rx, ry, paper, band.a * (0.55 + r() * 0.6));
    }
  }

  /* ─────────────────────────── 水 / 对岸 ─────────────────────────── */

  function paintWaterBase(x) {
    const r = rng(CONFIG.seed.water);
    const wy = CONFIG.waterline * DH;
    shimmer = [];

    // 水纹：成簇、带弧、越近越疏淡（直长线会读成划痕）
    for (let i = 0; i < 40; i++) {
      const cy = wy + (0.010 + Math.pow(r(), 1.35) * 0.30) * DH;
      const cx = (0.02 + r() * 0.94) * DW;
      const n = 1 + Math.floor(r() * 4);
      for (let j = 0; j < n; j++) {
        const y = cy + j * (1.4 + r() * 2.0);
        const len = (18 + Math.pow(r(), 1.6) * 96) * (1 - j * 0.12);
        const a = 0.024 + r() * 0.048;
        const bow = (r() - 0.5) * 2.6;
        x.strokeStyle = ink(a);
        x.lineWidth = 0.7 + r() * 0.7;
        x.lineCap = 'round';
        x.beginPath();
        x.moveTo(cx + (r() - 0.5) * 20, y);
        x.quadraticCurveTo(cx + len * 0.5, y + bow, cx + len, y + bow * 0.4);
        x.stroke();
        if (i % 3 === 0 && j === 0) shimmer.push({ x0: cx, y, len, a, ph: r() * TAU, sp: 0.35 + r() * 0.5 });
      }
    }
  }

  // 静态雾：大面积的那一层烘进背景，运行时不用再画（省掉的正是每帧最大的像素量）
  function paintStaticHaze(x) {
    const r = rng(CONFIG.seed.pavilion + 91);
    const spots = [
      [0.20, 0.288, 0.26, 0.24], [0.38, 0.345, 0.22, 0.18], [0.58, 0.395, 0.26, 0.20],
      [0.79, 0.250, 0.20, 0.16], [0.33, 0.560, 0.24, 0.17], [0.66, 0.630, 0.22, 0.16],
      [0.90, 0.470, 0.16, 0.14],
    ];
    for (const [cx, cy, w, h] of spots) {
      softEllipse(x, cx * DW + (r() - 0.5) * 30, cy * DH + (r() - 0.5) * 14,
                  w * DW * (0.8 + r() * 0.4), h * DH * (0.9 + r() * 0.5),
                  paper, 0.10 + r() * 0.06);
    }
  }

  // 右侧对岸：一条两头淡出的岸线 + 几粒小树，压住"空"又不抢
  function paintFarShore(x) {
    if (!CONFIG.farShore.enabled) return;
    const r = rng(CONFIG.seed.shore);
    const y = CONFIG.farShore.y * DH;

    for (let i = 0; i < 4; i++) {
      const cx = DW * (0.70 + i * 0.085) + (r() - 0.5) * 46;
      softEllipse(x, cx, y + (r() - 0.5) * 4, DW * 0.055, 3 + r() * 3, ink, 0.040);
    }
    x.strokeStyle = ink(0.10);
    x.lineWidth = 0.9;
    x.lineCap = 'round';
    for (let i = 0; i < 4; i++) {
      const x0 = DW * (0.66 + i * 0.08) + (r() - 0.5) * 30;
      x.beginPath();
      x.moveTo(x0, y + (r() - 0.5) * 5);
      x.lineTo(x0 + 60 + r() * 130, y + (r() - 0.5) * 5);
      x.stroke();
    }

    for (let i = 0; i < CONFIG.farShore.trees; i++) {
      const px = DW * (0.70 + r() * 0.27);
      const py = y + 1 + (r() - 0.5) * 2;
      const hgt = 4.5 + r() * 6;
      x.strokeStyle = ink(0.16 + r() * 0.10);
      x.lineWidth = 0.8;
      x.beginPath();
      x.moveTo(px, py);
      x.lineTo(px + (r() - 0.5) * 2.0, py - hgt);
      x.stroke();
      for (let k = 0; k < 3; k++) {
        const yy = py - hgt * (0.34 + k * 0.21);
        const ww = 2.6 - k * 0.55;
        x.beginPath();
        x.moveTo(px - ww, yy + 1.4);
        x.lineTo(px + ww, yy - 0.5);
        x.stroke();
      }
    }
  }

  /* ─────────────────────────── 近岸 · 松 ─────────────────────────── */

  // 锥形笔（画树干 / 枝条）
  function taper(x, pts, w0, w1, alpha) {
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      const wa = lerp(w0, w1, i / (pts.length - 1));
      const wb = lerp(w0, w1, (i + 1) / (pts.length - 1));
      const dx = b[0] - a[0], dy = b[1] - a[1];
      const len = Math.hypot(dx, dy) || 1;
      const nx = -dy / len, ny = dx / len;
      x.beginPath();
      x.moveTo(a[0] + nx * wa / 2, a[1] + ny * wa / 2);
      x.lineTo(b[0] + nx * wb / 2, b[1] + ny * wb / 2);
      x.lineTo(b[0] - nx * wb / 2, b[1] - ny * wb / 2);
      x.lineTo(a[0] - nx * wa / 2, a[1] - ny * wa / 2);
      x.closePath();
      x.fillStyle = ink(alpha);
      x.fill();
    }
  }

  // 松针团：几团不规则的墨块 + 边缘挑出的短针（远看是"一蓬松针"，不是蒲公英）
  function canopyMass(x, cx, cy, rx, ry, alpha, r) {
    for (let k = 0; k < 3; k++) {
      const ox = (r() - 0.5) * rx * 0.9, oy = (r() - 0.5) * ry * 0.8;
      x.beginPath();
      const n = 9;
      for (let i = 0; i <= n; i++) {
        const a = (i / n) * TAU;
        const rr = 0.55 + r() * 0.55;
        const px = cx + ox + Math.cos(a) * rx * 0.62 * rr;
        const py = cy + oy + Math.sin(a) * ry * 0.62 * rr;
        if (i === 0) x.moveTo(px, py); else x.lineTo(px, py);
      }
      x.closePath();
      x.fillStyle = ink(alpha * 0.42);
      x.fill();
    }
    // 边缘挑针
    const n = 18;
    for (let i = 0; i < n; i++) {
      const a = -Math.PI * (0.12 + r() * 0.76);
      const px = cx + Math.cos(a) * rx * (0.35 + r() * 0.5);
      const py = cy + Math.sin(a) * ry * (0.35 + r() * 0.5);
      const len = Math.min(rx, ry * 2.4) * (0.35 + r() * 0.5);
      const ang = a + (r() - 0.5) * 0.5;
      x.strokeStyle = ink(alpha * (0.35 + r() * 0.5));
      x.lineWidth = 0.7 + r() * 0.45;
      x.lineCap = 'round';
      x.beginPath();
      x.moveTo(px, py);
      x.lineTo(px + Math.cos(ang) * len, py + Math.sin(ang) * len);
      x.stroke();
    }
  }

  function pine(x, bx, by, hgt, lean, alpha, seed) {
    const r = rng(seed);
    const topX = bx + lean * hgt, topY = by - hgt;

    // 树干：带弯的锥笔 + 鳞纹
    taper(x, [
      [bx, by],
      [bx + lean * hgt * 0.18, by - hgt * 0.32],
      [bx + lean * hgt * 0.58, by - hgt * 0.62],
      [topX, topY],
    ], Math.max(1.5, hgt * 0.052), Math.max(0.5, hgt * 0.013), alpha * 0.95);

    for (let i = 0; i < 7; i++) {
      const k = 0.12 + r() * 0.72;
      const px = lerp(bx + lean * hgt * k * 0.5, topX, k) - 1;
      const py = lerp(by, topY, k);
      x.strokeStyle = ink(alpha * 0.40);
      x.lineWidth = 0.7;
      x.beginPath();
      x.moveTo(px - hgt * 0.014, py);
      x.lineTo(px + hgt * 0.014, py - hgt * 0.008);
      x.stroke();
    }

    // 枝层：下大上小，左右交替，针团压在枝梢
    for (let i = 0; i < 4; i++) {
      const k = 0.26 + (i / 4) * 0.64;
      const sx = lerp(bx + lean * hgt * k * 0.3, topX, k);
      const sy = lerp(by, topY, k);
      const dir = i % 2 === 0 ? -1 : 1;
      const bl = hgt * (0.36 - i * 0.055) * (0.9 + r() * 0.2);

      x.beginPath();
      x.moveTo(sx, sy);
      x.quadraticCurveTo(sx + dir * bl * 0.55, sy - bl * 0.16, sx + dir * bl, sy - bl * 0.07);
      x.strokeStyle = ink(alpha * 0.78);
      x.lineWidth = Math.max(0.7, hgt * 0.013);
      x.lineCap = 'round';
      x.stroke();

      canopyMass(x, sx + dir * bl * 0.52, sy - bl * 0.22, bl * 0.46, hgt * 0.085, alpha * 0.95, r);
      canopyMass(x, sx + dir * bl * 0.95, sy - bl * 0.10, bl * 0.30, hgt * 0.060, alpha * 0.85, r);

      // 逆侧短枝
      const ex2 = sx - dir * bl * 0.48, ey2 = sy - bl * 0.12;
      x.beginPath();
      x.moveTo(sx, sy);
      x.quadraticCurveTo(sx - dir * bl * 0.26, sy - bl * 0.10, ex2, ey2);
      x.lineWidth = Math.max(0.6, hgt * 0.010);
      x.stroke();
      canopyMass(x, ex2, ey2 - hgt * 0.012, bl * 0.26, hgt * 0.052, alpha * 0.75, r);
    }

    // 树顶
    canopyMass(x, topX, topY, hgt * 0.15, hgt * 0.07, alpha * 0.9, r);
  }

  function paintRock(x) {
    const r = rng(CONFIG.seed.rock);
    const topY = DH * 0.862;
    const W = DW * 0.118;
    const pts = [];
    const n = 18;
    for (let i = 0; i <= n; i++) {
      const u = i / n;
      const ux = u * W + (r() - 0.5) * 8;
      const jag = fbm(u * 5.2, CONFIG.seed.rock, 4) * DH * 0.080 + Math.sin(u * 9.1) * DH * 0.010;  // 石的棱
      const uy = topY + jag + Math.pow(u, 1.9) * DH * 0.085 + (r() - 0.5) * 5;
      pts.push([ux, Math.min(uy, DH - 4)]);
    }
    const rockYAt = (px) => {
      const u = clamp(px / W, 0, 1) * n;
      const i = Math.floor(u), f = u - i;
      const a = pts[Math.min(i, n)], b = pts[Math.min(i + 1, n)];
      return a[1] + (b[1] - a[1]) * f;
    };

    const path = () => {
      x.beginPath();
      x.moveTo(-12, DH + 12);
      for (const [px, py] of pts) x.lineTo(px, py);
      x.lineTo(W + 10, DH + 12);
      x.closePath();
    };

    // 石体：淡墨打底（不糊成一块死黑）
    path();
    const g = x.createLinearGradient(0, topY - DH * 0.03, 0, DH);
    g.addColorStop(0, ink(0.19));
    g.addColorStop(0.55, ink(0.30));
    g.addColorStop(1, ink(0.16));
    x.fillStyle = g;
    x.fill();

    // 贴顶湿墨：与远山同一手法，墨色顺着石棱走
    for (let k = 5; k >= 0; k--) {
      const off = (k + 1) * 7;
      x.beginPath();
      x.moveTo(pts[0][0], pts[0][1] + off);
      for (let i = 1; i < pts.length; i++) x.lineTo(pts[i][0], pts[i][1] + off);
      for (let i = pts.length - 1; i >= 0; i--) x.lineTo(pts[i][0], pts[i][1]);
      x.closePath();
      x.fillStyle = ink(0.050);
      x.fill();
    }

    // 斧劈皴：带棱的短笔
    for (let i = 0; i < 48; i++) {
      const px = r() * W, py = topY + r() * DH * 0.17;
      const len = 8 + r() * 26;
      x.strokeStyle = ink(0.05 + r() * 0.15);
      x.lineWidth = 1 + r() * 1.8;
      x.lineCap = 'butt';
      x.beginPath();
      x.moveTo(px, py);
      x.lineTo(px + (r() - 0.5) * 26, py + len);
      x.stroke();
    }

    // 顶部焦边
    x.beginPath();
    x.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) x.lineTo(pts[i][0], pts[i][1]);
    x.strokeStyle = ink(0.34);
    x.lineWidth = 1.5;
    x.lineJoin = 'round';
    x.stroke();

    // 干笔留白：石面上擦出两处透气的纸白
    for (let i = 0; i < 3; i++) {
      softEllipse(x, W * (0.18 + r() * 0.6), topY + DH * (0.02 + r() * 0.05),
                  26 + r() * 40, 9 + r() * 14, paper, 0.16 + r() * 0.10);
    }

    // 局部深墨 + 苔
    for (let i = 0; i < 6; i++) {
      softEllipse(x, r() * W, topY + DH * (0.01 + r() * 0.07), 24 + r() * 40, 8 + r() * 16, ink, 0.055);
    }
    for (let i = 0; i < 26; i++) {
      x.fillStyle = ink(0.16 + r() * 0.26);
      x.beginPath();
      x.arc(r() * W, topY + (r() - 0.3) * 14, 0.8 + r() * 1.6, 0, TAU);
      x.fill();
    }

    // 三株松：左重右轻，画眼落在这一角
    const pines = [
      { x: DW * 0.038, h: 0.118, lean: 0.20, a: 0.46, seed: 11 },
      { x: DW * 0.078, h: 0.090, lean: -0.12, a: 0.38, seed: 29 },
      { x: DW * 0.110, h: 0.060, lean: 0.05, a: 0.28, seed: 47 },
    ];
    for (const p of pines) {
      pine(x, p.x, rockYAt(p.x) + 2, DH * p.h, p.lean, p.a, CONFIG.seed.rock + p.seed);
    }
  }

  /* ─────────────────────────── 古亭 · 印 ─────────────────────────── */

  function pavilion(x, px, py, w, a) {
    x.strokeStyle = ink(a);
    x.lineWidth = 1.1;
    x.lineCap = 'round';

    // 台基
    x.beginPath(); x.moveTo(px - w * 0.52, py); x.lineTo(px + w * 0.52, py); x.stroke();
    // 上檐（两端起翘）
    x.beginPath();
    x.moveTo(px - w * 0.50, py - w * 0.30);
    x.quadraticCurveTo(px - w * 0.30, py - w * 0.62, px, py - w * 0.70);
    x.quadraticCurveTo(px + w * 0.30, py - w * 0.62, px + w * 0.50, py - w * 0.30);
    x.stroke();
    // 檐口
    x.beginPath();
    x.moveTo(px - w * 0.44, py - w * 0.28);
    x.lineTo(px + w * 0.44, py - w * 0.28);
    x.stroke();
    // 柱
    x.lineWidth = 0.9;
    for (const dx of [-0.30, -0.10, 0.10, 0.30]) {
      x.beginPath();
      x.moveTo(px + w * dx, py - w * 0.28);
      x.lineTo(px + w * dx, py - w * 0.02);
      x.stroke();
    }
    // 宝顶
    x.beginPath();
    x.moveTo(px, py - w * 0.70);
    x.lineTo(px, py - w * 0.80);
    x.stroke();
  }

  function sealStamp(x) {
    if (!CONFIG.seal.enabled) return;
    const s = CONFIG.seal.size, px = CONFIG.seal.x * DW, py = CONFIG.seal.y * DH;
    x.save();
    x.translate(px, py);
    x.fillStyle = `rgba(166,52,37,${CONFIG.seal.alpha})`;
    roundRectPath(x, 0, 0, s, s, s * 0.08);
    x.fill();
    // 白文「山」：三竖一横
    x.strokeStyle = paper(0.90);
    x.lineCap = 'butt';
    x.lineWidth = s * 0.095;
    const ys = [[0.255, 0.735], [0.215, 0.775], [0.275, 0.735]];
    [0.285, 0.5, 0.715].forEach((cx, i) => {
      x.beginPath();
      x.moveTo(s * cx, s * ys[i][0]);
      x.lineTo(s * cx, s * ys[i][1]);
      x.stroke();
    });
    x.beginPath();
    x.moveTo(s * 0.245, s * 0.755);
    x.lineTo(s * 0.755, s * 0.755);
    x.stroke();
    x.restore();
  }

  /* ─────────────────────────── 静态层组装 ─────────────────────────── */

  function buildStatic() {
    const wy = CONFIG.waterline * DH;

    // 与画布同尺寸：这样每帧是 1:1 blit（不重采样），比"小图画大"便宜一个量级
    staticLayer = layer(cv.width, cv.height);
    const x = staticLayer[1];
    x.setTransform(1, 0, 0, 1, 0, 0);
    x.fillStyle = CONFIG.palette.paper;      /* 素材里 FIT 那条"两侧留黑"分支随用仅截图的分支一并删了 */
    x.fillRect(0, 0, cv.width, cv.height);
    x.setTransform(S, 0, 0, S, OX, OY);
    x.lineJoin = 'round';
    x.lineCap = 'round';

    paintPaper(x);

    // 远、中两叠画在低分辨率层上，放大贴回 ⇒ 天然的柔边
    const softScale = 0.72;
    const soft = layer(DW * softScale, DH * softScale);
    soft[1].setTransform(softScale, 0, 0, softScale, 0, 0);
    soft[1].lineJoin = 'round';
    soft[1].lineCap = 'round';
    paintRange(soft[1], CONFIG.ranges[0]);
    paintRange(soft[1], CONFIG.ranges[1]);

    // 近山：单独一层，向右淡出（构图左重右空）
    const near = layer(DW * S, DH * S);
    near[1].setTransform(S, 0, 0, S, 0, 0);
    near[1].lineJoin = 'round';
    near[1].lineCap = 'round';
    paintRange(near[1], CONFIG.ranges[2]);
    near[1].globalCompositeOperation = 'destination-in';
    const fade = near[1].createLinearGradient(0, 0, DW, 0);
    fade.addColorStop(0.00, 'rgba(0,0,0,1)');
    fade.addColorStop(0.40, 'rgba(0,0,0,.97)');
    fade.addColorStop(0.58, 'rgba(0,0,0,.42)');
    fade.addColorStop(0.72, 'rgba(0,0,0,0)');
    near[1].fillStyle = fade;
    near[1].fillRect(0, 0, DW, DH);
    near[1].globalCompositeOperation = 'source-over';

    x.save();
    x.imageSmoothingEnabled = true;
    x.imageSmoothingQuality = 'high';
    if (FILTER_OK) x.filter = 'blur(0.6px)';                 // 远山柔一点，仍留住细笔
    x.drawImage(soft[0], 0, 0, soft[0].width, soft[0].height, 0, 0, DW, DH);
    x.filter = 'none';
    x.drawImage(near[0], 0, 0, DW, DH);
    x.restore();

    // 水中倒影：以水线为轴镜像、压扁、压淡
    const reflect = (src, alpha, sy) => {
      x.save();
      x.beginPath();
      x.rect(0, wy, DW, DH - wy);
      x.clip();
      x.globalAlpha = alpha;
      x.translate(0, wy * (1 + sy));
      x.scale(1, -sy);
      x.drawImage(src, 0, 0, src.width, src.height, 0, 0, DW, DH);
      x.restore();
    };
    if (FILTER_OK) x.filter = 'blur(2.4px)';
    reflect(soft[0], 0.10, 0.80);
    x.filter = 'none';

    const fadeWater = x.createLinearGradient(0, wy, 0, wy + 0.24 * DH);
    fadeWater.addColorStop(0, paper(0.10));
    fadeWater.addColorStop(0.40, paper(0.34));
    fadeWater.addColorStop(1, paper(0.66));
    x.fillStyle = fadeWater;
    x.fillRect(0, wy, DW, DH - wy);

    // 云气（静态：山脚横带 + 几团大面积的雾）
    for (const band of CONFIG.mistBands) mistBand(x, band);
    paintStaticHaze(x);

    paintFarShore(x);
    paintWaterBase(x);
    paintRock(x);

    // 远山古亭：落在最远一叠的脊上（找最近的脊顶）
    if (CONFIG.pavilion.enabled) {
      const far = ridgePts(CONFIG.ranges[0]).pts;
      const target = CONFIG.pavilion.u;
      let best = far[0], bestScore = Infinity;
      for (let i = 6; i < far.length - 6; i++) {
        const [px, py] = far[i];
        const isCrest = py <= far[i - 5][1] && py <= far[i + 5][1];
        if (!isCrest) continue;
        const score = Math.abs(px / DW - target) * 3 + py / DH;
        if (score < bestScore) { bestScore = score; best = far[i]; }
      }
      pavilion(x, best[0], best[1] + 1, CONFIG.pavilion.w, CONFIG.pavilion.alpha);
    }

    sealStamp(x);

    soft[0].width = soft[0].height = 0;
    near[0].width = near[0].height = 0;
  }

  /* ─────────────────────────── 事件排程（固定种子） ─────────────────────────── */

  function schedule() {
    const L = CONFIG.loop;
    const r = rng(CONFIG.seed.schedule);

    // ── 雁阵
    events.birds = [];
    const B = CONFIG.birds;
    const bslots = Math.max(1, Math.round(L / B.slot));
    for (let i = 0; i < bslots; i++) {
      if (r() > B.chance) continue;
      const slotStart = i * (L / bslots);
      const slotLen = L / bslots;
      const transit = Math.min(B.transit * (0.92 + r() * 0.22), slotLen * 0.98);
      const start = Math.min(slotStart + r() * Math.max(0, slotLen - transit * 0.9), L - transit);
      const far = r() < 0.30;
      const maxN = far ? 3 : B.maxBirds;
      const n = 2 + Math.floor(r() * (maxN - 1));
      const y = lerp(B.yMin, B.yMax, r()) * DH;
      const dir = r() < 0.72 ? 1 : -1;             // 多数从左往右
      const flock = {
        t0: start, dur: transit, y, dir,
        scale: (far ? 0.58 : 1) * (0.85 + r() * 0.45),
        alpha: far ? 0.40 : 0.68,
        birds: [],
      };
      for (let k = 0; k < n; k++) {
        const side = k === 0 ? 0 : (k % 2 ? 1 : -1) * Math.ceil(k / 2);
        flock.birds.push({
          side,
          lag: Math.abs(side) * (0.30 + r() * 0.16) + (k === 0 ? 0 : r() * 0.12),
          dy: side * (far ? 11 : 19) * (0.75 + r() * 0.5),
          s: flock.scale * (10.5 + r() * 4.5),
          ph: r() * TAU,
          a: flock.alpha * (0.78 + r() * 0.42),
          bob: 1.2 + r() * 2.2,
          bph: r() * TAU,
        });
      }
      events.birds.push(flock);
    }

    // ── 落叶（镜头前）
    events.leaves = [];
    const LV = CONFIG.leaves;
    const lslots = Math.max(1, Math.round(L / LV.slot));
    for (let i = 0; i < lslots; i++) {
      if (r() > LV.chance) continue;
      const count = r() < 0.18 ? 2 : 1;            // 偶尔两片一起
      for (let c = 0; c < count; c++) {
        const transit = lerp(LV.transitMin, LV.transitMax, r());
        const start = Math.min(i * (L / lslots) + r() * (L / lslots), L - transit - 0.05);
        events.leaves.push({
          t0: Math.max(0, start), dur: transit,
          x0: -0.12 * DW,
          y0: (0.02 + r() * 0.52) * DH,
          vx: (1.30 + r() * 0.30) * DW,
          vy: (0.030 + r() * 0.060) * DH,
          sway: 14 + r() * 30,
          swf: 0.55 + r() * 0.75,
          swp: r() * TAU,
          rot0: r() * TAU,
          rotV: (r() - 0.5) * 1.9,
          len: 78 + r() * 46,
          grow: 1.10 + r() * 0.16,
          tone: 1.0 + r() * 0.45,
          sprite: Math.floor(r() * 3) + (r() < 0.5 ? 0 : 3),   // 2 叶形 × 3 档模糊
          seed: Math.floor(r() * 1e6),
        });
      }
    }
    events.leaves.sort((a, b) => a.t0 - b.t0);
  }

  /* ─────────────────────────── 动态：雾 ─────────────────────────── */

  // 会漂的雾只留三小团：每帧重画的面积直接等于帧成本，
  // 大面积的雾气已经烘进静态层（见 paintStaticHaze），不需要每帧再画一遍。
  const PUFFS = [
    { x: 0.235, y: 0.300, rx: 0.072, a: 0.30, dx: 24, dy: 6, ph: 0.0 },
    { x: 0.560, y: 0.402, rx: 0.082, a: 0.24, dx: -20, dy: 5, ph: 1.9 },
    { x: 0.775, y: 0.268, rx: 0.062, a: 0.20, dx: 14, dy: 8, ph: 3.4 },
  ];

  function markBox(x0, y0, x1, y1) { frameRects.push(x0, y0, x1, y1); }

  // 雾团贴图：柔边烘一次，之后每帧只是 drawImage（原来每帧造 5 个径向渐变 ≈12ms）
  function bakeMistSprite() {
    const N = 180;
    const [c, g] = layer(N, N);
    const gr = g.createRadialGradient(N / 2, N / 2, 0, N / 2, N / 2, N / 2);
    gr.addColorStop(0, paper(1));
    gr.addColorStop(0.42, paper(0.62));
    gr.addColorStop(0.74, paper(0.20));
    gr.addColorStop(1, paper(0));
    g.fillStyle = gr;
    g.fillRect(0, 0, N, N);
    return c;
  }

  function drawMist(x, t) {
    const L = CONFIG.loop;
    if (!mistSprite) return;
    for (const p of PUFFS) {
      const k = (t / L) * TAU + p.ph;
      const px = p.x * DW + Math.sin(k) * p.dx * (DW / 1920);
      const py = p.y * DH + Math.cos(k * 2) * p.dy * (DH / 1080);
      const rx = p.rx * DW * (1 + Math.sin(k) * 0.05);
      const ry = rx * 0.34;
      x.globalAlpha = p.a * 0.50 * (0.82 + Math.sin(k * 3) * 0.18);
      x.drawImage(mistSprite, px - rx, py - ry, rx * 2, ry * 2);
      markBox(px - rx, py - ry, px + rx, py + ry);
    }
    x.globalAlpha = 1;
  }

  /* ─────────────────────────── 动态：水光 ─────────────────────────── */

  function drawShimmer(x, t) {
    const L = CONFIG.loop;
    for (const s of shimmer) {
      const k = 0.5 + 0.5 * Math.sin((t / L) * TAU * 2 + s.ph + s.sp * s.y * 0.01);
      const a = s.a * (0.35 + 0.65 * k);
      if (a < 0.008) continue;
      x.strokeStyle = ink(a);
      x.lineWidth = 0.8;
      x.lineCap = 'round';
      x.beginPath();
      x.moveTo(s.x0, s.y);
      x.quadraticCurveTo(s.x0 + s.len * 0.5, s.y + Math.sin(t * 0.5 + s.ph) * 1.2,
                         s.x0 + s.len * (0.7 + 0.3 * k), s.y);
      x.stroke();
      markBox(s.x0 - 2, s.y - 7, s.x0 + s.len + 8, s.y + 7);
    }
  }

  /* ─────────────────────────── 动态：小舟 ─────────────────────────── */

  function drawBoat(x, t) {
    const L = CONFIG.loop;
    const B = CONFIG.boat;
    const k = (t / L) * TAU;
    const px = (B.x0 + (B.x1 - B.x0) * (0.5 + 0.5 * Math.sin(k - 0.7))) * DW;   // 缓漂，不瞬移
    const py = B.y * DH + Math.sin(k * 3) * 1.6;
    const tilt = Math.sin(k * 2 + 1.1) * 0.012;
    const len = B.len * (DW / 1920);

    function hull(alpha, sy) {
      x.save();
      x.translate(px, py);
      x.rotate(tilt);
      x.scale(1, sy);
      x.lineCap = 'round';
      // 船身
      x.beginPath();
      x.moveTo(-len * 0.5, -len * 0.03);
      x.quadraticCurveTo(0, len * 0.21, len * 0.5, -len * 0.04);
      x.quadraticCurveTo(0, len * 0.09, -len * 0.5, -len * 0.03);
      x.closePath();
      x.fillStyle = ink(alpha * 0.60);
      x.fill();
      // 船头翘起
      x.beginPath();
      x.moveTo(len * 0.5, -len * 0.04);
      x.quadraticCurveTo(len * 0.56, -len * 0.14, len * 0.55, -len * 0.22);
      x.strokeStyle = ink(alpha * 0.72);
      x.lineWidth = Math.max(0.8, len * 0.022);
      x.stroke();
      // 篷
      x.beginPath();
      x.moveTo(-len * 0.27, -len * 0.03);
      x.quadraticCurveTo(-len * 0.13, -len * 0.30, len * 0.03, -len * 0.03);
      x.closePath();
      x.fillStyle = ink(alpha * 0.28);
      x.fill();
      x.strokeStyle = ink(alpha * 0.62);
      x.lineWidth = Math.max(0.7, len * 0.018);
      x.beginPath();
      x.moveTo(-len * 0.27, -len * 0.03);
      x.quadraticCurveTo(-len * 0.13, -len * 0.30, len * 0.03, -len * 0.03);
      x.stroke();
      // 渔翁：斗笠 + 身 + 篙
      x.fillStyle = ink(alpha * 0.80);
      x.beginPath();
      x.moveTo(len * 0.09, -len * 0.10);
      x.lineTo(len * 0.21, -len * 0.10);
      x.lineTo(len * 0.15, -len * 0.20);
      x.closePath();
      x.fill();
      x.beginPath();
      x.moveTo(len * 0.15, -len * 0.10);
      x.lineTo(len * 0.15, -len * 0.02);
      x.strokeStyle = ink(alpha * 0.72);
      x.lineWidth = Math.max(0.7, len * 0.024);
      x.stroke();
      x.beginPath();
      x.moveTo(len * 0.16, -len * 0.05);
      x.lineTo(len * 0.44, -len * 0.36);
      x.strokeStyle = ink(alpha * 0.52);
      x.lineWidth = Math.max(0.6, len * 0.014);
      x.stroke();
      x.restore();
    }

    // 倒影 + 舟尾水痕（水痕永远在船尾：漂向哪边，痕迹就在另一边）
    hull(0.32, -0.62);
    const stern = Math.cos(k - 0.7) >= 0 ? -1 : 1;
    x.strokeStyle = ink(0.15);
    x.lineWidth = 0.9;
    for (let i = 1; i <= 2; i++) {
      const wob = Math.sin(k * 4 + i) * 1.2;
      const bx = px + stern * len * (0.52 + i * 0.32);
      x.beginPath();
      x.moveTo(bx, py + 2 + i * 1.6 + wob);
      x.lineTo(bx + stern * len * (0.32 - i * 0.05), py + 2 + i * 1.6 + wob);
      x.stroke();
    }
    hull(1, 1);
    markBox(px - len * 1.5, py - len * 0.7, px + len * 0.9, py + len * 0.6);
  }

  /* ─────────────────────────── 动态：雁 ─────────────────────────── */

  function drawBird(x, px, py, s, phase, alpha) {
    const f = Math.sin(phase);
    const tipY = -f * s * 0.62;
    const ctl = -f * s * 0.22;
    x.strokeStyle = ink(alpha);
    x.lineWidth = Math.max(0.9, s * 0.115);
    x.lineCap = 'round';
    for (const d of [-1, 1]) {
      x.beginPath();
      x.moveTo(px, py);
      x.quadraticCurveTo(px + d * s * 0.58, py + ctl - s * 0.05, px + d * s, py + tipY);
      x.stroke();
    }
    x.lineWidth = Math.max(0.9, s * 0.21);
    x.beginPath();
    x.moveTo(px - s * 0.15, py + s * 0.03);
    x.lineTo(px + s * 0.20, py - s * 0.02);
    x.stroke();
  }

  function drawBirds(x, t) {
    for (const fl of events.birds) {
      const e = t - fl.t0;
      if (e < 0 || e > fl.dur) continue;
      const p = e / fl.dur;
      const x0 = fl.dir > 0 ? -0.10 * DW : 1.10 * DW;
      const x1 = fl.dir > 0 ? 1.10 * DW : -0.10 * DW;
      const cx = lerp(x0, x1, p);
      const cy = fl.y + Math.sin(p * Math.PI) * -DH * 0.012;
      const fade = Math.min(ss(0, 0.12, p), ss(1, 0.88, p));

      for (const b of fl.birds) {
        const bx = cx - fl.dir * b.lag * 34;
        const by = cy + b.dy + Math.sin(t * 0.9 + b.bph) * b.bob;
        if (bx < -0.16 * DW || bx > 1.16 * DW) continue;
        drawBird(x, bx, by, b.s, t * 5.1 + b.ph, b.a * fade);
        const r = b.s * 1.9;
        markBox(bx - r, by - r, bx + r, by + r);
      }
    }
  }

  /* ─────────────────────────── 动态：落叶 ─────────────────────────── */

  function leafPath(x, len) {
    const w = len * 0.30;
    x.beginPath();
    x.moveTo(0, 0);
    x.bezierCurveTo(len * 0.18, -w * 1.06, len * 0.70, -w * 0.94, len, 0);   // 上缘（鼓）
    x.bezierCurveTo(len * 0.72, w * 0.66, len * 0.28, w * 0.84, 0, 0);       // 下缘（略平）
    x.closePath();
  }

  // 贴图里的叶长（画到屏幕上会缩到 78~156）
  const LEAF_SPRITE_LEN = 300, LEAF_SPRITE_PAD = 26;

  // 满浓度的叶身 + 叶缘焦墨 + 叶脉 + 叶柄 + 洇点：全部烘进贴图
  function paintLeafSprite(g, len) {
    const w = len * 0.30;
    const grad = g.createLinearGradient(0, 0, len, 0);
    grad.addColorStop(0, leafInk(1));
    grad.addColorStop(0.55, leafInk(0.86));
    grad.addColorStop(1, leafInk(0.52));
    leafPath(g, len);
    g.fillStyle = grad;
    g.fill();

    leafPath(g, len);
    g.strokeStyle = ink(0.60);
    g.lineWidth = Math.max(1, len * 0.010);
    g.stroke();

    g.strokeStyle = ink(0.62);
    g.lineWidth = Math.max(1, len * 0.011);
    g.lineCap = 'round';
    g.beginPath();
    g.moveTo(len * 0.02, 0);
    g.quadraticCurveTo(len * 0.46, -len * 0.026, len * 0.95, -len * 0.004);
    g.stroke();
    g.lineWidth = Math.max(1, len * 0.007);
    for (let i = 1; i <= 4; i++) {
      const t = i / 5;
      const bx = len * t, by = -len * 0.018 * Math.sin(t * 3.1);
      g.beginPath();
      g.moveTo(bx, by);
      g.quadraticCurveTo(bx + len * 0.06, by - w * 0.42, bx + len * 0.12, by - w * 0.66);
      g.stroke();
      g.beginPath();
      g.moveTo(bx, by);
      g.quadraticCurveTo(bx + len * 0.06, by + w * 0.34, bx + len * 0.11, by + w * 0.56);
      g.stroke();
    }

    g.strokeStyle = leafInk(0.95);
    g.lineWidth = Math.max(1, len * 0.014);
    g.beginPath();
    g.moveTo(-len * 0.11, len * 0.010);
    g.quadraticCurveTo(-len * 0.05, len * 0.004, 0, 0);
    g.stroke();

    g.fillStyle = leafInk(0.25);
    g.beginPath();
    g.arc(len * 0.46, -w * 0.12, len * 0.028, 0, TAU);
    g.fill();
  }

  // 烘 2 种叶形 × 3 档模糊 ⇒ 6 张贴图；运行时按 drawLeaf 的用法直接贴
  function bakeLeafSprites() {
    const L = LEAF_SPRITE_LEN;
    const w = Math.ceil(L * 1.26 + LEAF_SPRITE_PAD * 2);
    const h = Math.ceil(L * 0.46 + LEAF_SPRITE_PAD * 2);
    const blurs = [1.2, 3.0, 5.5];                 // 贴图 px；缩到屏上 ≈0.4 / 1.1 / 2.0 设计 px
    const out = [];
    for (let v = 0; v < 2; v++) {
      const [c, g] = layer(w, h);
      g.translate(LEAF_SPRITE_PAD + L * 0.10, h / 2);
      paintLeafSprite(g, L);
      for (const b of blurs) {
        const [bc, bg] = layer(w, h);
        if (FILTER_OK) bg.filter = 'blur(' + b + 'px)';   // 只在烘焙时用一次
        bg.drawImage(c, 0, 0);
        out.push({ c: bc, ox: LEAF_SPRITE_PAD + L * 0.10, oy: h / 2, len: L });
      }
      c.width = c.height = 0;
    }
    return out;
  }

  function drawLeafSprite(x, sp, px, py, ang, len, alpha) {
    const k = len / sp.len;
    x.save();
    x.translate(px, py);
    x.rotate(ang);
    x.globalAlpha = alpha;
    x.drawImage(sp.c, -sp.ox * k, -sp.oy * k, sp.c.width * k, sp.c.height * k);
    x.restore();
  }

  function drawLeaves(x, t) {
    if (!leafSprites) return;
    for (const ev of events.leaves) {
      const e = t - ev.t0;
      if (e < 0 || e > ev.dur) continue;
      const p = e / ev.dur;
      const px = ev.x0 + ev.vx * p + Math.sin(e * ev.swf + ev.swp) * ev.sway;
      const py = ev.y0 + ev.vy * p + Math.sin(e * ev.swf * 0.83 + ev.swp * 1.7) * ev.sway * 0.55;
      const ang = ev.rot0 + ev.rotV * e + Math.sin(e * 1.7 + ev.swp) * 0.5;
      const len = ev.len * lerp(1, ev.grow, p);
      const fade = Math.min(ss(0, 0.10, p), ss(1, 0.90, p));
      const alpha = 0.27 * fade * ev.tone;
      const sp = leafSprites[ev.sprite % leafSprites.length];

      // 拖影（运动模糊）：两片很淡的残影，贴得近才不会读成"三片叶子"
      for (let i = 2; i >= 0; i--) {
        const k = i * 0.006;
        const a = i === 0 ? alpha : alpha * (i === 1 ? 0.16 : 0.07);
        drawLeafSprite(x, sp, px - ev.vx * k, py - ev.vy * k * 0.6,
                       ang - ev.rotV * k * 0.5, len, a);
      }
      const sw = sp.c.width * (len / sp.len), sh = sp.c.height * (len / sp.len);
      const rad = 0.5 * Math.hypot(sw, sh) + ev.vx * 0.014 + 6;
      markBox(px - rad, py - rad, px + rad, py + rad);
    }
  }

  /* ─────────────────────────── 镜头 / 主绘制 ─────────────────────────── */

  function cameraAt(t) {
    const L = CONFIG.loop;
    const k = (t / L) * TAU;                       // 周期 = 整个循环，接缝两侧速度也连续
    return {
      z: 1 + CONFIG.camera.zoom * (0.5 - 0.5 * Math.cos(k)),
      dx: Math.sin(k) * CONFIG.camera.panX,
      dy: Math.cos(k) * CONFIG.camera.panY,
    };
  }

  function draw(t) {
    if (!ready) return;
    framesDrawn += 1;
    lastT = t;
    const tm = mod(t, CONFIG.loop);

    // ① 只把上一帧动过的方块贴回静态层（阴影/雾/舟/雁/叶都在这几块里）
    //    整屏重画是"每帧 1.5M 像素"，只还原脏块通常不到它的 1/4。
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = 'source-over';
    if (needFullBlit) {
      ctx.drawImage(staticLayer[0], 0, 0);
      dirtyRects.length = 0;
      needFullBlit = false;
    } else {
      const smooth = ctx.imageSmoothingEnabled;
      ctx.imageSmoothingEnabled = false;                  // 1:1 还原本该是"拷贝"，别插值
      for (let i = 0; i < dirtyRects.length; i += 4) {
        const rx = dirtyRects[i], ry = dirtyRects[i + 1], rw = dirtyRects[i + 2], rh = dirtyRects[i + 3];
        ctx.drawImage(staticLayer[0], rx, ry, rw, rh, rx, ry, rw, rh);
      }
      ctx.imageSmoothingEnabled = smooth;
    }

    // ② 动态层：设计坐标 → 设备像素（每帧同一个变换，镜头不在这里做）
    frameRects.length = 0;
    ctx.setTransform(S, 0, 0, S, OX, OY);
    drawMist(ctx, tm);
    drawShimmer(ctx, tm);
    drawBoat(ctx, tm);
    drawBirds(ctx, tm);
    drawLeaves(ctx, tm);
    ctx.setTransform(1, 0, 0, 1, 0, 0);

    // ③ 记账：本帧的区域扩边、转设备像素，留给下一帧还原
    dirtyRects.length = 0;
    for (let i = 0; i < frameRects.length; i += 4) {
      const x0 = clamp(Math.floor(frameRects[i] * S + OX - DIRTY_PAD), 0, cv.width);
      const y0 = clamp(Math.floor(frameRects[i + 1] * S + OY - DIRTY_PAD), 0, cv.height);
      const x1 = clamp(Math.ceil(frameRects[i + 2] * S + OX + DIRTY_PAD), 0, cv.width);
      const y1 = clamp(Math.ceil(frameRects[i + 3] * S + OY + DIRTY_PAD), 0, cv.height);
      const rw = x1 - x0, rh = y1 - y0;
      if (rw > 0 && rh > 0) dirtyRects.push(x0, y0, rw, rh);
    }

    // ③ 镜头呼吸：交给合成器（CSS 变换），画布内容一个字都不用重采样
    if (CAMERA_CSS) {
      const cam = cameraAt(tm);
      const px = cam.dx * COVER, py = cam.dy * COVER;
      cv.style.transform = 'translate3d(' + px.toFixed(2) + 'px,' + py.toFixed(2) +
        'px,0) scale(' + cam.z.toFixed(4) + ')';
    }
  }

  /* ─────────────────────────── 尺寸 / 启动 / 导出 ─────────────────────────── */

  function resizeTo(w, h) {
    VW = Math.max(320, Number(w) || 0);
    VH = Math.max(240, Number(h) || 0);

    // 画质上限：先按设备像素比，再按"最多多少像素"压回来（4K + DPR2 不再是灾难）
    const raw = window.devicePixelRatio || 1;
    let dpr = Math.min(raw, CONFIG.quality.maxDpr);
    const want = VW * VH * dpr * dpr;
    if (want > CONFIG.quality.maxPixels) dpr = Math.sqrt(CONFIG.quality.maxPixels / (VW * VH));
    DPR = clamp(dpr, 0.75, raw);

    cv.width = Math.round(VW * DPR);
    cv.height = Math.round(VH * DPR);

    COVER = Math.max(VW / DW, VH / DH);            /* 壳是硬 16:9 ⇒ cover 即 1:1，不裁不拉伸 */
    S = COVER * DPR;                               // 设计坐标 → 设备像素
    OX = (cv.width - DW * S) / 2;
    OY = (cv.height - DH * S) / 2;

    buildStatic();
    needFullBlit = true;
    ready = true;
  }

  /* ─────────────────── 模块外壳：不自动启动，一切从 mount() 开始 ───────────────────
   * 与素材页的差别只有"谁来开、按谁的量"（绘制代码一行未改）：
   *   ① 尺寸从**容器**取——素材读 window.innerWidth/innerHeight，本模块读画布的 CSS 尺寸；
   *   ② 不读 location、不写 document.title、不解析 ?t/?fit/?export/?fps/?bench/?prof；
   *   ③ **载入期什么都不做**：SGS 的烟测垫片把每份脚本塞进同一个 vm，且没有 location /
   *      URLSearchParams / performance.now / 裸 requestAnimationFrame——顶层碰一下就把
   *      整轮烟测打断（`tests/support/ui_smoke.mjs:604-612` 没有 try/catch）；
   *   ④ 起停由 pause()/resume() 控制：切视图即停，省电也省合成器。
   */

  var REDUCE = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
  var CAMERA_CSS = CONFIG.camera.mode === 'css' && !REDUCE;
  var running = false;
  var elapsed = 0;             // 已播放秒数（跨多次 pause/resume 累计）
  var resumedAt = 0;
  var frameId = 0;
  var resizeBound = false;
  var measuredW = 0;
  var measuredH = 0;
  var framesDrawn = 0;   /* 内部自检：真画了几帧（烟测 / 探针用） */
  var containerObserver = null;

  function clock() {
    if (window.performance && typeof window.performance.now === 'function') return window.performance.now();
    return Date.now();
  }
  function rafCall(fn) {
    if (typeof window.requestAnimationFrame !== 'function') return 0;
    return window.requestAnimationFrame(fn);
  }
  function rafCancel(id) {
    if (id && typeof window.cancelAnimationFrame === 'function') window.cancelAnimationFrame(id);
  }
  function currentT() {
    return running ? elapsed + (clock() - resumedAt) / 1000 : elapsed;
  }
  function moduleState() {
    return { mounted: !!cv, running: running, reduced: REDUCE, seconds: currentT(), frames: framesDrawn };
  }
  function step() {
    if (!running) return;
    followContainer();
    draw(currentT());
    frameId = rafCall(step);
  }
  /* 容器尺寸在 mount 之后仍可能变：`aspect-ratio` 解析、字体/滚动条、窗口缩放都算。
   * 显式 resize 事件只覆盖最后一种，所以每帧再对一次 CSS 尺寸——读 `clientWidth` 不写布局属性
   * （写的是 canvas 的 backing store），读的是没脏的布局，代价接近 0；对不上才重量一次。 */
  function followContainer() {
    if (!cv) return;
    const w = cv.clientWidth;
    const h = cv.clientHeight;
    if (w > 0 && h > 0 && (w !== measuredW || h !== measuredH)) resizeToContainer();
  }
  function resizeToContainer() {
    if (!cv) return;
    resizeTo(cv.clientWidth || cv.width, cv.clientHeight || cv.height);
    measuredW = VW;
    measuredH = VH;
    if (!running) draw(currentT());
  }
  function mount(canvas) {
    if (!canvas || typeof canvas.getContext !== 'function') return moduleState();
    cv = canvas;
    ctx = cv.getContext('2d', { alpha: false });
    if (!ctx) { cv = null; return moduleState(); }
    if (!leafSprites) {
      leafSprites = bakeLeafSprites();      // 叶 / 雾的柔边一次性烘好，运行时不碰 ctx.filter
      mistSprite = bakeMistSprite();
      schedule();
    }
    if (!resizeBound) {
      resizeBound = true;
      /* 容器只在视口变化时改尺寸（壳是 min(100%, 100vh*16/9)），故一个 window resize 够用。 */
      window.addEventListener('resize', function () {
        if (!cv) return;
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(resizeToContainer, 180);
      }, { passive: true });
      /* 但 mount 那一刻布局未必已经定（`aspect-ratio` 还没解析、字体 / 滚动条还在变），
       * 而 rAF 在后台标签页会被节流到几乎不跑 ⇒ 靠每帧比对不可靠。
       * `ResizeObserver` 跟着布局走、不受节流影响，是这里唯一稳的信号。
       * 老环境没有它就退化成"只认 window resize"。 */
      if (typeof window.ResizeObserver === 'function') {
        containerObserver = new window.ResizeObserver(function () { resizeToContainer(); });
        containerObserver.observe(cv);
      }
    }
    resizeToContainer();
    draw(0);
    if (!REDUCE) resume();
    return moduleState();
  }
  var resizeTimer = null;
  function pause() {
    if (!running) return moduleState();
    elapsed = currentT();
    running = false;
    rafCancel(frameId);
    frameId = 0;
    return moduleState();
  }
  function resume() {
    if (running || !cv || REDUCE) return moduleState();
    running = true;
    resumedAt = clock();
    frameId = rafCall(step);
    return moduleState();
  }
  function destroy() {
    pause();
    if (containerObserver) { containerObserver.disconnect(); containerObserver = null; }
    cv = null;
    ctx = null;
    ready = false;
    needFullBlit = true;
    return moduleState();
  }

  window.SgsInkBg = {
    mount: mount,
    pause: pause,
    resume: resume,
    destroy: destroy,
    refresh: resizeToContainer,
    draw: draw,
    state: moduleState,
    config: CONFIG,
    events: events,
    get duration() { return CONFIG.loop; },
  };
})();
