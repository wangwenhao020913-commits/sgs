/* SGS 前端 · 「铁索连环」的**音效**（chain-death-voices 票 01；素材包
 * `outputs/sgs-chain-fx/sfx-chain.js` 的移植）：入链（干笔 → 铁索摩擦 + 环环叩击 →
 * 落定"哐" → 墨渗进纸）与解锁（绷紧 → 崩断 → 断片落地 → 墨化开）两条一次性排程。
 *
 * 姿态与伤害家族的三份音效（`sfx.js` / `sfx-thunder.js` / `sfx-fire.js`）一致：
 * Web Audio **现场合成**（仓库里没有音频文件）、`create()` 返回一个播放器、模块本身
 * **不持有时钟**（cue 时刻从 `SgsChainFX.MODES` / 牌桌档的 `gameTiming` 读，画面与声音
 * 同一张表）。`renderOffline` 只给离线电平读数用。
 *
 * 与伤害家族**不同**的一点：铁索是**一次性排程器**（`play(mode)` 排完整段、`stop()` 收掉），
 * 不进 `sfx-ui.js` 的伤害声道池，也不逐帧 `tick`——横置不是高频事件，池长 1（spec §演出口径）。
 *
 * 常音量的唯一调音处是下面那张 `VOICE` 表（与素材包一致）。
 */
"use strict";

(function (global) {
  "use strict";

  var NOISE = 1.4;          /* 噪声缓冲最长 1.4s */

  /* 调音台：改音量只改这里 */
  var VOICE = {
    brush: 0.032,     /* 干笔划纸 */
    friction: 0.050,  /* 铁索摩擦（垫底，不能有周期性"拉链"感） */
    clank: 0.155,     /* 铁链叮当（一声环扣相击） */
    clink: 0.046,     /* 单次叩击（断片落地用） */
    settle: 0.470,    /* 落定 */
    ink: 0.045,       /* 墨渗进纸 */
    strain: 0.115,    /* 绷紧 */
    snap: 0.720,      /* 崩断 */
    shard: 0.130,     /* 断片碎响 */
    mist: 0.085       /* 墨雾 */
  };

  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }

  /* ------------------------------------------------------------ 时间表 ---- */
  function cues(mode) {
    var FX = global.SgsChainFX;
    var M = (FX && FX.MODES && FX.MODES[mode]) || null;
    if (M) return M;
    return mode === "break"
      ? { id: "break", duration: 1.24, tensionAt: 0, tensionDur: 0.155, snapAt: 0.155, sepDur: 0.44, dissolveAt: 0.24, dissolveDur: 0.90, goneAt: 1.16 }
      : { id: "link", duration: 1.36, ghostAt: 0, ghostDur: 0.30, slideAt: 0.09, slideDur: 0.90, settleAt: 1.00, rubAt: 0.94, bleedAt: 1.00, bleedDur: 0.36 };
  }

  /* 与画面同一条滑动曲线：拎起来快、到位慢（取 1-(1-x)^2.7） */
  function slideCurve(x) {
    x = clamp(x, 0, 1);
    if (x >= 1) return 1;
    var e = 1 - Math.pow(1 - x, 2.7);
    if (x > 0.88) e += Math.sin((x - 0.88) / 0.12 * Math.PI) * 0.022;
    return e;
  }

  /* 环扣掠过"牌沿"的节拍点：把 0..1 的行程切成 n 段，反解出各自的时刻 */
  function clinkTimes(M, n) {
    var out = [], k = 1;
    for (var i = 0; i <= 800 && k <= n; i++) {
      var x = i / 800;
      if (slideCurve(x) >= k / (n + 1)) {
        out.push(M.slideAt + x * M.slideDur);
        k++;
      }
    }
    return out;
  }

  /* ------------------------------------------------------------ 噪声源 ---- */
  function noiseBuffer(ac) {
    if (ac.__chainNoise) return ac.__chainNoise;
    var len = Math.floor(ac.sampleRate * NOISE);
    var buf = ac.createBuffer(1, len, ac.sampleRate);
    var d = buf.getChannelData(0);
    var seed = 20261118;                    /* 固定种子：离线渲染两次结果一致 */
    for (var i = 0; i < len; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      d[i] = (seed / 0x7fffffff) * 2 - 1;
    }
    ac.__chainNoise = buf;
    return buf;
  }

  function noise(ac, list, t, dur) {
    var src = ac.createBufferSource();
    src.buffer = noiseBuffer(ac);
    src.loop = true;
    src.start(t);
    src.stop(t + dur);
    list.push(src);
    return src;
  }

  function gainNode(ac, out, list) {
    var g = ac.createGain();
    g.connect(out);
    return g;
  }

  function filterNode(ac, type, freq, q) {
    var f = ac.createBiquadFilter();
    f.type = type;
    f.frequency.value = freq;
    f.Q.value = q == null ? 0.9 : q;
    return f;
  }

  function osc(ac, type, t, dur, list) {
    var o = ac.createOscillator();
    o.type = type;
    o.start(t);
    o.stop(t + dur);
    list.push(o);
    return o;
  }

  /* 一声金属叩击：几个不成谐波的分音 + 一撮噪声，尾巴很短 */
  function clink(ac, out, list, t, base, level, decay) {
    var ratios = [1, 2.41, 3.87];
    var amps = [1, 0.42, 0.20];
    decay = decay || 0.055;
    for (var i = 0; i < ratios.length; i++) {
      var o = osc(ac, "sine", t, decay * 3.2, list);
      /* 抖动用时间派生而不是 Math.random：离线渲染两次必须一模一样 */
      o.frequency.setValueAtTime(base * ratios[i] * (0.97 + (Math.sin((t + i) * 91.7) * 0.5 + 0.5) * 0.06), t);
      var g = gainNode(ac, out, list);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(VOICE.clink * level * amps[i], t + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, t + decay * (1 + i * 0.5));
      o.connect(g);
    }
    var src = noise(ac, list, t, decay * 1.6);
    var bp = filterNode(ac, "bandpass", base * 1.5, 1.6);
    var ng = gainNode(ac, out, list);
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.exponentialRampToValueAtTime(VOICE.clink * level * 0.5, t + 0.003);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + decay * 1.2);
    src.connect(bp); bp.connect(ng);
  }

  /* ------------------------------------------------------------ 入链 ---- */

  /* 干笔划纸：一撮高频的气，很短 */
  function brush(ac, out, list, t) {
    var src = noise(ac, list, t, 0.30);
    var bp = filterNode(ac, "bandpass", 2600, 0.9);
    bp.frequency.setValueAtTime(1800, t);
    bp.frequency.exponentialRampToValueAtTime(4200, t + 0.22);
    var g = gainNode(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.brush, t + 0.05);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.28);
    src.connect(bp); bp.connect(g);
  }

  /* 铁索拖过牌面：中频摩擦（带一点低频"沉"） */
  function friction(ac, out, list, t, dur, level) {
    var src = noise(ac, list, t, dur + 0.1);
    var bp = filterNode(ac, "bandpass", 900, 1.5);
    bp.frequency.setValueAtTime(740, t);
    bp.frequency.exponentialRampToValueAtTime(1500, t + dur * 0.30);
    bp.frequency.exponentialRampToValueAtTime(520, t + dur);
    var g = gainNode(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.friction * level, t + 0.10);
    g.gain.exponentialRampToValueAtTime(VOICE.friction * level * 0.55, t + dur * 0.7);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur + 0.06);
    src.connect(bp); bp.connect(g);

    var lo = osc(ac, "sine", t, dur, list);
    lo.frequency.setValueAtTime(78, t);
    lo.frequency.linearRampToValueAtTime(58, t + dur);
    var lg = gainNode(ac, out, list);
    lg.gain.setValueAtTime(0.0001, t);
    lg.gain.exponentialRampToValueAtTime(VOICE.friction * level * 0.35, t + 0.14);
    lg.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    lo.connect(lg);
  }

  /* 铁链叮当：几个不成谐波的分音 + 一撮高频"铁"噪声，短促、带一点下滑。
   *   ring 越大，尾巴越长（"当——"），其余是短"叮"。 */
  function clank(ac, out, list, t, base, level, ring) {
    var ratios = [1, 1.61, 2.28, 3.05, 4.11];
    var amps = [1, 0.55, 0.30, 0.17, 0.08];
    var dec = 0.040 + 0.085 * ring;
    for (var i = 0; i < ratios.length; i++) {
      var o = osc(ac, "sine", t, dec * 4.5, list);
      o.frequency.setValueAtTime(base * ratios[i], t);
      o.frequency.exponentialRampToValueAtTime(base * ratios[i] * 0.962, t + dec * 2.4);
      var g = gainNode(ac, out, list);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(VOICE.clank * level * amps[i], t + 0.0018);
      g.gain.exponentialRampToValueAtTime(0.0001, t + dec * (1 + i * 0.75));
      o.connect(g);
    }
    /* 铁与铁相击的那一下"嗒" */
    var src = noise(ac, list, t, 0.05);
    var bp = filterNode(ac, "bandpass", base * 1.75, 1.3);
    var ng = gainNode(ac, out, list);
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.exponentialRampToValueAtTime(VOICE.clank * level * 0.42, t + 0.0015);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.035);
    src.connect(bp); bp.connect(ng);
  }

  /* 落定：一记闷重的"哐" */
  function settle(ac, out, list, t, level) {
    /* 金属双击 */
    var base = 1280;
    for (var i = 0; i < 3; i++) {
      var o = osc(ac, "sine", t, 0.5, list);
      o.frequency.setValueAtTime(base * [1, 2.37, 3.61][i], t);
      o.frequency.exponentialRampToValueAtTime(base * [0.92, 2.2, 3.3][i], t + 0.30);
      var g = gainNode(ac, out, list);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(VOICE.settle * level * [0.55, 0.24, 0.12][i], t + 0.005);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.24 + i * 0.12);
      o.connect(g);
    }
    /* 低频：铁索压在牌上 */
    var lo = osc(ac, "sine", t, 0.42, list);
    lo.frequency.setValueAtTime(104, t);
    lo.frequency.exponentialRampToValueAtTime(46, t + 0.26);
    var lg = gainNode(ac, out, list);
    lg.gain.setValueAtTime(0.0001, t);
    lg.gain.exponentialRampToValueAtTime(VOICE.settle * level * 0.7, t + 0.008);
    lg.gain.exponentialRampToValueAtTime(0.0001, t + 0.40);
    lo.connect(lg);
    /* 纸被压住的一口 */
    var src = noise(ac, list, t, 0.22);
    var bp = filterNode(ac, "bandpass", 1100, 1.2);
    bp.frequency.setValueAtTime(2200, t);
    bp.frequency.exponentialRampToValueAtTime(700, t + 0.18);
    var ng = gainNode(ac, out, list);
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.exponentialRampToValueAtTime(VOICE.settle * level * 0.26, t + 0.006);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.20);
    src.connect(bp); bp.connect(ng);
  }

  /* 墨渗进纸：一口很轻的低频沙 */
  function inkSeep(ac, out, list, t, dur) {
    var src = noise(ac, list, t, dur);
    var lp = filterNode(ac, "lowpass", 900, 0.7);
    lp.frequency.setValueAtTime(1400, t);
    lp.frequency.exponentialRampToValueAtTime(420, t + dur);
    var g = gainNode(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.ink, t + dur * 0.35);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(lp); lp.connect(g);
  }

  /* ------------------------------------------------------------ 解锁 ---- */

  /* 绷紧：一条往上收的金属"吱" */
  function strain(ac, out, list, t, dur) {
    var src = noise(ac, list, t, dur + 0.06);
    var bp = filterNode(ac, "bandpass", 2100, 6.0);
    bp.frequency.setValueAtTime(1500, t);
    bp.frequency.exponentialRampToValueAtTime(2700, t + dur);
    var g = gainNode(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.strain * 0.5, t + dur * 0.5);
    g.gain.exponentialRampToValueAtTime(VOICE.strain, t + dur);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur + 0.03);
    src.connect(bp); bp.connect(g);

    var o = osc(ac, "sine", t, dur + 0.06, list);
    o.frequency.setValueAtTime(230, t);
    o.frequency.exponentialRampToValueAtTime(470, t + dur);
    var og = gainNode(ac, out, list);
    og.gain.setValueAtTime(0.0001, t);
    og.gain.exponentialRampToValueAtTime(VOICE.strain * 0.30, t + dur * 0.7);
    og.gain.exponentialRampToValueAtTime(0.0001, t + dur + 0.02);
    o.connect(og);
  }

  /* 崩断：爆裂瞬态 + 金属泛音 + 低频泄气 + 一声"嘣" */
  function snap(ac, out, list, t) {
    /* 1. 爆裂瞬态 */
    var src = noise(ac, list, t, 0.10);
    var hp = filterNode(ac, "highpass", 2600, 0.9);
    var g = gainNode(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.snap, t + 0.0025);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.075);
    src.connect(hp); hp.connect(g);

    /* 2. 金属泛音（不成谐波，尾巴短） */
    var base = 2350, ratios = [1, 1.58, 2.21, 3.14], amps = [1, 0.55, 0.34, 0.18];
    for (var i = 0; i < ratios.length; i++) {
      var o = osc(ac, "sine", t, 0.5, list);
      o.frequency.setValueAtTime(base * ratios[i], t);
      o.frequency.exponentialRampToValueAtTime(base * ratios[i] * 0.955, t + 0.35);
      var og = gainNode(ac, out, list);
      og.gain.setValueAtTime(0.0001, t);
      og.gain.exponentialRampToValueAtTime(VOICE.snap * 0.30 * amps[i], t + 0.003);
      og.gain.exponentialRampToValueAtTime(0.0001, t + 0.16 + i * 0.07);
      o.connect(og);
    }

    /* 3. 低频：张力一松 */
    var lo = osc(ac, "sine", t, 0.5, list);
    lo.frequency.setValueAtTime(128, t);
    lo.frequency.exponentialRampToValueAtTime(42, t + 0.28);
    var lg = gainNode(ac, out, list);
    lg.gain.setValueAtTime(0.0001, t);
    lg.gain.exponentialRampToValueAtTime(VOICE.snap * 0.62, t + 0.006);
    lg.gain.exponentialRampToValueAtTime(0.0001, t + 0.42);
    lo.connect(lg);

    /* 4. 一声短"嘣" */
    var tw = osc(ac, "sawtooth", t, 0.16, list);
    tw.frequency.setValueAtTime(760, t);
    tw.frequency.exponentialRampToValueAtTime(190, t + 0.12);
    var bp = filterNode(ac, "bandpass", 900, 3.2);
    var tg = gainNode(ac, out, list);
    tg.gain.setValueAtTime(0.0001, t);
    tg.gain.exponentialRampToValueAtTime(VOICE.snap * 0.16, t + 0.004);
    tg.gain.exponentialRampToValueAtTime(0.0001, t + 0.14);
    tw.connect(bp); bp.connect(tg);
  }

  /* 墨化开：一口慢气 + 一点低频 */
  function mist(ac, out, list, t, dur) {
    var src = noise(ac, list, t, dur);
    var bp = filterNode(ac, "bandpass", 1500, 0.8);
    bp.frequency.setValueAtTime(2300, t);
    bp.frequency.exponentialRampToValueAtTime(520, t + dur);
    var g = gainNode(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.mist, t + dur * 0.28);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    src.connect(bp); bp.connect(g);

    var o = osc(ac, "sine", t, dur, list);
    o.frequency.setValueAtTime(44, t);
    o.frequency.linearRampToValueAtTime(38, t + dur);
    var og = gainNode(ac, out, list);
    og.gain.setValueAtTime(0.0001, t);
    og.gain.exponentialRampToValueAtTime(VOICE.mist * 0.6, t + dur * 0.3);
    og.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(og);
  }

  /* ------------------------------------------------------------ 排程 ---- */
  function schedule(ac, out, mode, fromT, speed) {
    var list = [];
    var M = cues(mode);
    var sp = speed > 0 ? speed : 1;
    function at(t) { return ac.currentTime + Math.max(0, (t - fromT) / sp); }
    function live(t) { return (t - fromT) / sp >= -0.02; }

    if (mode === "break") {
      var st0 = M.snapAt;
      if (live(0)) strain(ac, out, list, at(M.tensionAt), (M.snapAt - M.tensionAt) / sp);
      if (live(st0)) {
        snap(ac, out, list, at(st0));
        /* 断片落地：越往后越轻、越散 */
        var r = seedRng(9187);
        var tt = st0 + 0.055;
        for (var i = 0; i < 7; i++) {
          if (live(tt)) clink(ac, out, list, at(tt), 1900 + r() * 1500, 0.9 - i * 0.11, 0.05 + r() * 0.05);
          tt += 0.035 + r() * 0.075;
        }
        if (live(st0 + 0.08)) mist(ac, out, list, at(st0 + 0.08), Math.min(1.0, (M.goneAt - st0 - 0.08) / sp));
      }
      return list;
    }

    /* ---- 入链 ---- */
    if (live(M.ghostAt)) brush(ac, out, list, at(M.ghostAt));
    if (live(M.slideAt)) {
      friction(ac, out, list, at(M.slideAt), M.slideDur / sp, 1);
      /* 铁链叮当：环扣一枚枚从牌沿上碾过去 —— 开头急、到位慢，
       * 所以前半段密、后半段疏；频率与轻重都错开，听起来才是链子不是拉链 */
      var rj = seedRng(4471);
      var times = clinkTimes(M, 26);
      for (var k = 0; k < times.length; k++) {
        if (rj() < 0.12) continue;
        var tt = times[k] + (rj() - 0.5) * 0.013;
        if (!live(tt)) continue;
        var base = 1450 + rj() * 1950;
        var lv = 0.45 + rj() * 0.55;
        clank(ac, out, list, at(tt), base, lv, k % 6 === 0 ? 1 : 0.30);
        /* 偶尔两枚环几乎同时碰一下：链子的"哗啦" */
        if (rj() < 0.34) {
          clank(ac, out, list, at(tt + 0.016 + rj() * 0.022),
            base * (0.80 + rj() * 0.45), lv * 0.55, 0.2);
        }
      }
    }
    if (live(M.settleAt)) {
      settle(ac, out, list, at(M.settleAt), 1);
      inkSeep(ac, out, list, at(M.settleAt + 0.05), 0.42);
    }
    return list;
  }

  function seedRng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  /* ------------------------------------------------------------ 在线 ---- */
  function create() {
    var ac = null, master = null, bus = null, sources = [], muted = false;

    function stopAll() {
      for (var i = 0; i < sources.length; i++) {
        try { sources[i].stop(); } catch (e) { /* 已经停过 */ }
      }
      sources = [];
      if (bus) {
        try { bus.disconnect(); } catch (e) { /* 忽略 */ }
        bus = null;
      }
    }

    return {
      unlock: function () {
        if (!ac) {
          var Ctor = global.AudioContext || global.webkitAudioContext;
          if (!Ctor) return Promise.resolve(false);
          try { ac = new Ctor(); } catch (e) { return Promise.resolve(false); }
          master = ac.createGain();
          master.gain.value = muted ? 0 : 0.9;
          var comp = ac.createDynamicsCompressor();
          comp.threshold.value = -16;
          comp.ratio.value = 4;
          comp.attack.value = 0.003;
          comp.release.value = 0.22;
          master.connect(comp);
          comp.connect(ac.destination);
        }
        ac.resume();
        return Promise.resolve(true);
      },
      play: function (mode, fromT, speed) {
        if (!ac || muted) return;
        stopAll();
        bus = ac.createGain();
        bus.gain.value = 1;
        bus.connect(master);
        sources = schedule(ac, bus, mode === "break" ? "break" : "link", Math.max(0, fromT || 0), speed || 1);
      },
      stop: function () {
        if (!ac) return;
        stopAll();
      },
      mute: function (on) {
        muted = !!on;
        if (master) master.gain.value = muted ? 0 : 0.9;
      },
      get context() { return ac; }
    };
  }

  /* ------------------------------------------------------------ 离线 ---- */
  function renderOffline(mode, seconds) {
    var Offline = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    if (!Offline) return Promise.resolve({ available: false });
    var dur = Math.max(0.4, Number(seconds) || 1.4);
    var ac = new Offline(2, Math.ceil(44100 * dur), 44100);
    var out = ac.createGain();
    out.gain.value = 0.9;
    var comp = ac.createDynamicsCompressor();
    comp.threshold.value = -16;
    comp.ratio.value = 4;
    comp.attack.value = 0.003;
    comp.release.value = 0.22;
    out.connect(comp);
    comp.connect(ac.destination);
    schedule(ac, out, mode === "break" ? "break" : "link", 0, 1);
    return ac.startRendering().then(function (buf) {
      var ch = buf.getChannelData(0);
      var peak = 0, sum = 0;
      for (var i = 0; i < ch.length; i++) {
        var v = Math.abs(ch[i]);
        if (v > peak) peak = v;
        sum += ch[i] * ch[i];
      }
      var windows = [];
      var win = Math.floor(44100 * 0.25);
      for (var w = 0; w * win < ch.length; w++) {
        var s = 0, n = 0;
        for (var j = w * win; j < Math.min(ch.length, (w + 1) * win); j++) { s += ch[j] * ch[j]; n++; }
        windows.push(+(Math.sqrt(s / Math.max(1, n))).toFixed(4));
      }
      return {
        available: true,
        peak: +peak.toFixed(3),
        rms: +Math.sqrt(sum / ch.length).toFixed(4),
        windows: windows,
        duration: +dur.toFixed(2)
      };
    });
  }

  global.SgsChainSfx = {
    create: create,
    renderOffline: renderOffline,
    cues: cues,
    VOICE: VOICE,
    slideCurve: slideCurve
  };
})(window);
