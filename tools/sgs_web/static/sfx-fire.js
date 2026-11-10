"use strict";

/* SGS 前端 · **火焰**伤害演出的音效（damage-fx-natures 票 03）：`SgsFireSfx`。
 *
 * 移植自素材包 `outputs/sgs-fire-fx/sfx-fire.js`：弓弦离弦的闷响、火羽掠空的风声、火星
 * 噼啪、钉入牌面的闷击；3 档加爆燃轰鸣，4 档加火云低频、云内预闪、流星下坠的呼啸、落地
 * 大爆炸与扫过全屏的冲击波。全部由 Web Audio 现场合成，没有音频文件、没有外部资源。
 *
 * 接口与 `SgsSfx` / `SgsThunderSfx` **同形**，`sfx-ui.js` 的声道池直接驱动（它不认第二种
 * 形状）：
 *
 *   var lane = SgsFireSfx.create(timeline);      // timeline 是**可变**的引用
 *   lane.start();                                // 手势里 resume（没有 AudioContext ⇒ false）
 *   lane.tick(seconds, voice);                   // 每帧喂演出自己的秒数
 *   lane.stop();  lane.mute(on);
 *   SgsFireSfx.renderOffline(variant, timeline, seconds)   // ?audiotest=1 / 导出
 *
 * 三条硬口径（与 `sfx.js` / `sfx-thunder.js` 一致）：
 * 1. **cue 与画面同一条时钟**：`tick(t)` 的 `t` 就是喂 `fx.draw(t)` 的那个秒数；cue 时刻读
 *    声道里那份可变时间轴（`sfx-ui.js` 开演前按该档把画面模块的 `timeline` 整份写进来），
 *    不另读墙钟。
 * 2. **多声道并发**：几条声道共用一个模块级 `AudioContext` + 压缩器，每条声道一个 session
 *    gain（素材页是单场播放器，同时三条火焰只响最后一条——这里是按声道隔离的）。
 * 3. **没有 `AudioContext` 就静默降级**：`start()` 返回 false，画面照旧。
 *
 * 火羽是**逐箭**发声的：cue 表按 `COUNT` / `GAP` / `FLIGHT` 摊开（这三个数由画面模块的
 * `gameTiming` 算好写进时间轴），所以 `anim.ini` 改箭数 / 节奏时声音跟着走，不在这里另写
 * 一份四档数字。
 */
(function (global) {
  var SR = 44100;
  /* 共享图：{ ctx, comp, buffers }。第一次 start() 时建；建不出来就一直是 null。 */
  var GRAPH = null;

  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }

  /* ------------------------------------------------------------ 噪声源 ---- */
  function makeNoise(ctx, seconds, seed) {
    var len = Math.max(1, Math.floor(ctx.sampleRate * seconds));
    var buf = ctx.createBuffer(1, len, ctx.sampleRate);
    var data = buf.getChannelData(0);
    var a = seed >>> 0;
    for (var i = 0; i < len; i++) {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      data[i] = (((t ^ (t >>> 14)) >>> 0) / 4294967296) * 2 - 1;
    }
    return buf;
  }

  function makeBrown(ctx, seconds, seed) {
    var len = Math.max(1, Math.floor(ctx.sampleRate * seconds));
    var buf = ctx.createBuffer(1, len, ctx.sampleRate);
    var data = buf.getChannelData(0);
    var last = 0;
    var a = seed >>> 0;
    for (var i = 0; i < len; i++) {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      var white = (((t ^ (t >>> 14)) >>> 0) / 4294967296) * 2 - 1;
      last = (last + 0.02 * white) / 1.02;
      data[i] = last * 3.2;
    }
    return buf;
  }

  function buffers(ctx) {
    return {
      noise: makeNoise(ctx, 2.6, 0x51a7),
      brown: makeBrown(ctx, 4.0, 0x9e21)
    };
  }

  function compressor(ctx) {
    var comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -20;
    comp.knee.value = 12;
    comp.ratio.value = 4;
    comp.attack.value = 0.003;
    comp.release.value = 0.25;
    comp.connect(ctx.destination);
    return comp;
  }

  /* 模块级共享图：三条声道共用，避免每条声道各建一个 AudioContext。 */
  function graph() {
    if (GRAPH) return GRAPH;
    var AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) return null;
    try {
      var ctx = new AC();
      GRAPH = { ctx: ctx, comp: compressor(ctx), buffers: buffers(ctx) };
    } catch (error) {
      GRAPH = null;
    }
    return GRAPH;
  }

  /* ------------------------------------------------------------ 小工具 ---- */
  function panner(ctx, pan) {
    if (ctx.createStereoPanner) {
      var p = ctx.createStereoPanner();
      p.pan.value = clamp(pan || 0, -1, 1);
      return p;
    }
    return ctx.createGain();
  }

  function gainEnv(ctx, when, attack, peak, end) {
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), when + Math.max(0.002, attack));
    g.gain.exponentialRampToValueAtTime(0.0001, end);
    return g;
  }

  /* 噪声起点：素材页用 `Math.random()` 挑 offset，这里按 `when` 取一个固定偏移——离线渲染
   * 逐次同值（与 `sfx-thunder.js` 同一口径：同一时刻永远同一段噪声）。 */
  function noiseStart(ctx, B, when, dur, offsetMax) {
    var s = ctx.createBufferSource();
    s.buffer = B.noise;
    var off = (when * 997 % 1) * (offsetMax || 1.0);
    s.start(when, off, dur + 0.06);
    s.stop(when + dur + 0.08);
    return s;
  }

  /* ------------------------------------------------------------ 音色 ---- */

  /* 弓弦 / 离弦 / 命中：低闷的一记，加一丝空气声。 */
  function thud(ctx, out, B, when, dur, gain, pan) {
    var osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(158, when);
    osc.frequency.exponentialRampToValueAtTime(54, when + dur);
    var g = gainEnv(ctx, when, 0.006, gain, when + dur);
    var p = panner(ctx, pan);
    osc.connect(g); g.connect(p); p.connect(out);
    osc.start(when); osc.stop(when + dur + 0.06);

    var src = noiseStart(ctx, B, when, Math.min(dur, 0.16), 1.0);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 900;
    var g2 = gainEnv(ctx, when, 0.004, gain * 0.45, when + Math.min(dur, 0.16));
    src.connect(lp); lp.connect(g2); g2.connect(p);
  }

  /* 火羽掠空：一段带通噪声从低扫到高，声像跟着飞。 */
  function whoosh(ctx, out, B, when, dur, gain, f0, f1, pan0, pan1) {
    dur = Math.max(0.05, dur);
    var src = noiseStart(ctx, B, when, dur, 0.9);
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.Q.value = 1.1;
    bp.frequency.setValueAtTime(f0, when);
    bp.frequency.exponentialRampToValueAtTime(f1, when + dur);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), when + dur * 0.45);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    var p = panner(ctx, pan0);
    if (p.pan) {
      p.pan.setValueAtTime(clamp(pan0, -1, 1), when);
      p.pan.linearRampToValueAtTime(clamp(pan1, -1, 1), when + dur);
    }
    src.connect(bp); bp.connect(g); g.connect(p); p.connect(out);
  }

  /* 火星噼啪：极短的高频爆点。 */
  function pop(ctx, out, B, when, gain, pan) {
    var src = noiseStart(ctx, B, when, 0.05, 1.2);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 1800;
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 3200 + (when * 7919 % 1) * 1200;
    bp.Q.value = 1.4;
    var g = gainEnv(ctx, when, 0.002, gain, when + 0.05);
    var p = panner(ctx, pan);
    src.connect(hp); hp.connect(bp); bp.connect(g); g.connect(p); p.connect(out);
  }

  /* 击中 / 撕裂：中频噪声的一记爆点。 */
  function crack(ctx, out, B, when, dur, gain, hp, pan) {
    var src = noiseStart(ctx, B, when, dur, 1.2);
    var f1 = ctx.createBiquadFilter();
    f1.type = "highpass";
    f1.frequency.value = hp;
    var f2 = ctx.createBiquadFilter();
    f2.type = "bandpass";
    f2.frequency.value = hp * 2.1;
    f2.Q.value = 0.8;
    var g = gainEnv(ctx, when, 0.003, gain, when + dur);
    var p = panner(ctx, pan);
    src.connect(f1); f1.connect(f2); f2.connect(g); g.connect(p); p.connect(out);
  }

  /* 火舌的嘶声：高频噪声 + 快速的幅度颤动。 */
  function sizzle(ctx, out, B, when, dur, gain, pan) {
    dur = Math.max(0.1, dur);
    var src = noiseStart(ctx, B, when, dur, 0.8);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 2200;
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 4800;
    bp.Q.value = 0.7;
    var g = gainEnv(ctx, when, 0.012, gain, when + dur);
    var trem = ctx.createGain();
    trem.gain.value = 0.62;
    var lfo = ctx.createOscillator();
    lfo.type = "sine";
    lfo.frequency.value = 27;
    var lg = ctx.createGain();
    lg.gain.value = 0.32;
    lfo.connect(lg); lg.connect(trem.gain);
    var p = panner(ctx, pan);
    src.connect(hp); hp.connect(bp); bp.connect(g); g.connect(trem); trem.connect(p); p.connect(out);
    lfo.start(when); lfo.stop(when + dur + 0.06);
  }

  /* 蓄力：低频上升 + 一点高处的火苗声（火印亮起来那一段）。 */
  function riser(ctx, out, B, when, dur, gain) {
    dur = Math.max(0.08, dur);
    var osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(52, when);
    osc.frequency.exponentialRampToValueAtTime(250, when + dur);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(220, when);
    lp.frequency.exponentialRampToValueAtTime(1300, when + dur);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), when + dur * 0.82);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur + 0.04);
    var p = panner(ctx, -0.24);
    osc.connect(lp); lp.connect(g); g.connect(p); p.connect(out);
    osc.start(when); osc.stop(when + dur + 0.08);

    var src = noiseStart(ctx, B, when, dur, 0.8);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 900;
    var g2 = ctx.createGain();
    g2.gain.setValueAtTime(0.0001, when);
    g2.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain * 0.5), when + dur * 0.85);
    g2.gain.exponentialRampToValueAtTime(0.0001, when + dur + 0.04);
    src.connect(hp); hp.connect(g2); g2.connect(p);
  }

  /* 火团铺开：一片噪声从暗到亮再收。 */
  function swell(ctx, out, B, when, dur, gain, pan) {
    dur = Math.max(0.1, dur);
    var src = noiseStart(ctx, B, when, dur, 0.9);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(420, when);
    lp.frequency.exponentialRampToValueAtTime(2400, when + dur * 0.5);
    lp.frequency.exponentialRampToValueAtTime(700, when + dur);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain * 0.45), when + dur * 0.34);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), when + dur * 0.6);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    var p = panner(ctx, pan);
    src.connect(lp); lp.connect(g); g.connect(p); p.connect(out);
  }

  /* 流星下坠：呼啸 + 越坠越尖的噪流。 */
  function meteorFall(ctx, out, B, when, dur) {
    dur = Math.max(0.1, dur);
    var src = noiseStart(ctx, B, when, dur, 0.7);
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.Q.value = 0.9;
    bp.frequency.setValueAtTime(240, when);
    bp.frequency.exponentialRampToValueAtTime(2700, when + dur);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(0.42, when + dur * 0.92);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur + 0.03);
    var p = panner(ctx, 0.4);
    src.connect(bp); bp.connect(g); g.connect(p); p.connect(out);

    var osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(420, when);
    osc.frequency.exponentialRampToValueAtTime(86, when + dur);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 900;
    var g2 = ctx.createGain();
    g2.gain.setValueAtTime(0.0001, when);
    g2.gain.exponentialRampToValueAtTime(0.075, when + dur * 0.9);
    g2.gain.exponentialRampToValueAtTime(0.0001, when + dur + 0.03);
    var p2 = panner(ctx, 0.3);
    osc.connect(lp); lp.connect(g2); g2.connect(p2); p2.connect(out);
    osc.start(when); osc.stop(when + dur + 0.1);
  }

  /* 低沉的暖色轰鸣：整段爆炸的底。 */
  function boom(ctx, out, B, when, dur, gain, pan) {
    dur = Math.max(0.1, dur);
    var osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(112, when);
    osc.frequency.exponentialRampToValueAtTime(25, when + dur);
    var g = gainEnv(ctx, when, 0.008, gain, when + dur);
    var p = panner(ctx, pan);
    osc.connect(g); g.connect(p); p.connect(out);
    osc.start(when); osc.stop(when + dur + 0.08);

    var src = noiseStart(ctx, B, when, Math.min(dur, 0.5), 1.2);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(2200, when);
    lp.frequency.exponentialRampToValueAtTime(140, when + Math.min(dur, 0.5));
    var g2 = gainEnv(ctx, when, 0.005, gain * 0.7, when + Math.min(dur, 0.5));
    src.connect(lp); lp.connect(g2); g2.connect(p);
  }

  /* 冲击波：高频先冲、低频兜底的一记空气推。 */
  function shock(ctx, out, B, when, gain) {
    var src = noiseStart(ctx, B, when, 0.8, 1.2);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 240;
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(5200, when);
    lp.frequency.exponentialRampToValueAtTime(180, when + 0.55);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), when + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, when + 0.8);
    var p = panner(ctx, 0.05);
    src.connect(hp); hp.connect(lp); lp.connect(g); g.connect(p); p.connect(out);
  }

  /* 长尾轰鸣：棕色噪声压到 110Hz 以下。 */
  function rumble(ctx, out, B, when, dur, gain) {
    dur = Math.max(0.12, dur);
    var src = ctx.createBufferSource();
    src.buffer = B.brown;
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 110;
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.linearRampToValueAtTime(gain, when + Math.min(0.32, dur * 0.5));
    g.gain.setValueAtTime(gain, when + Math.max(0.1, dur - 0.25));
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    var p = panner(ctx, 0);
    src.connect(lp); lp.connect(g); g.connect(p); p.connect(out);
    src.start(when, 0, dur + 0.1);
    src.stop(when + dur + 0.12);
  }

  /* ------------------------------------------------------------ 排程 ----
   * 事件表在每次调用时按**当前**时间轴与档位算一遍（时间轴是可变引用，开演前被改写）。
   * `fromT < at <= toT` 的事件交给 `fire`，`when0` 是它们落到音频时钟上的起点：
   * 实时 tick 用"现在"（帧粒度），离线渲染用精确的 `at - fromT`。
   *
   * 火羽是逐箭发声：箭数 / 间距 / 飞行时长都读时间轴的 `COUNT` / `GAP` / `FLIGHT`
   * （画面模块的 `gameTiming` 算好的那一份），不在本文件里写死四档的数字。 */
  var LEVEL = [0, 0.72, 0.86, 0.96, 1.0];

  function fireEvents(ctx, out, B, timeline, tierId, fromT, toT, when0, exact) {
    var id = clamp(Math.round(tierId) || 1, 1, 4);
    var level = LEVEL[id];
    var T = timeline || {};
    function num(value) {
      var n = Number(value);
      return isFinite(n) && n > 0 ? n : 0;
    }
    var charge = num(T.CHARGE);
    var launch = num(T.LAUNCH);
    var flight = Math.max(0.02, num(T.FLIGHT));
    var count = Math.max(0, Math.round(num(T.COUNT)));
    var gap = num(T.GAP);
    var impact = num(T.IMPACT);
    var lastImpact = Math.max(num(T.LAST_IMPACT), impact);
    var blast = num(T.BLAST);
    var sky = num(T.SKY_AT);
    var explode = num(T.EXPLODE);
    var numAt = num(T.NUM_AT);
    var events = [];
    function at(time, fire) {
      if (time > 0) events.push({ at: time, fire: fire });
    }
    function whenOf(time) {
      return when0 + (exact ? Math.max(0, time - fromT) : 0);
    }

    /* 蓄力：火印亮起来的低频（火羽离弦前那一段） */
    at(charge, function (when) {
      riser(ctx, out, B, when, Math.max(0.12, launch - charge), 0.06 * level);
    });

    /* 逐箭：离弦（弓弦 + 掠空 + 火星）与命中（闷击 + 爆点） */
    for (var i = 0; i < count; i++) {
      (function (index) {
        var launchT = launch + index * gap;
        var impactT = launchT + flight;
        var pan = 0.10 + 0.10 * ((index % 3) - 1);
        at(launchT, function (when) {
          thud(ctx, out, B, when, 0.16, 0.16 * level, -0.34);
          whoosh(ctx, out, B, when, flight, 0.075 * level, 620, 2300, -0.34, 0.20);
          pop(ctx, out, B, when + 0.012, 0.045 * level, -0.2);
        });
        at(impactT, function (when) {
          thud(ctx, out, B, when, 0.18 + 0.02 * id, 0.17 * level, pan);
          crack(ctx, out, B, when, 0.08, 0.085 * level, 900, pan);
          pop(ctx, out, B, when + 0.02, 0.05 * level, pan);
        });
      })(i);
    }

    /* 2 档起：命中后铺开一片火声 */
    if (id >= 2 && count > 0) {
      at(impact, function (when) {
        swell(ctx, out, B, when, 0.62, 0.085 * level, 0.16);
      });
    }

    /* 3 档：爆燃（没有流星的那一档，最后一支箭落地即炸） */
    if (blast > 0 && explode <= 0) {
      at(blast, function (when) {
        boom(ctx, out, B, when, 0.72, 0.52 * level, 0.16);
        crack(ctx, out, B, when, 0.20, 0.42 * level, 520, 0.16);
        swell(ctx, out, B, when, 0.8, 0.12 * level, 0.16);
        sizzle(ctx, out, B, when + 0.02, 1.15, 0.15 * level, 0.14);
        rumble(ctx, out, B, when, 1.25, 0.22);
        pop(ctx, out, B, when + 0.03, 0.06, 0.2);
      });
    }

    /* 4 档：火云低频 → 云内预闪 → 流星下坠 → 大爆炸 + 冲击波。
     * 火云从**最后一支箭钉住**开始压顶（`LAST_IMPACT`，时间轴上就是"箭串播完"的那一刻），
     * 不是从第一支命中就开始——那样低频会比画面早一整串箭。 */
    if (explode > 0) {
      var lead = Math.max(0.12, sky > lastImpact ? sky - lastImpact : 0.2);
      at(lastImpact, function (when) {
        if (sky > lastImpact) rumble(ctx, out, B, when, sky - lastImpact, 0.14);
      });
      at(sky, function (when) {
        var pres = [when - lead * 0.7, when - lead * 0.45, when - lead * 0.2];
        for (var p = 0; p < pres.length; p++) {
          crack(ctx, out, B, pres[p], 0.08, 0.06 * level, 2200, -0.10);
          pop(ctx, out, B, pres[p], 0.05, -0.1);
        }
        // 4 档的流星一定先有「压境」（`SKY_AT > 0`），下落时长 = 压境到命中的那一段。
        meteorFall(ctx, out, B, when, Math.max(0.06, explode - sky));
      });
      at(explode, function (when) {
        boom(ctx, out, B, when, 1.15, 0.88 * level, 0.05);
        shock(ctx, out, B, when, 0.62);
        crack(ctx, out, B, when, 0.26, 0.55 * level, 480, 0.05);
        swell(ctx, out, B, when, 1.0, 0.16 * level, 0.05);
        sizzle(ctx, out, B, when + 0.03, 1.95, 0.20 * level, 0.10);
        rumble(ctx, out, B, when, 2.25, 0.34);
        for (var q = 0; q < 10; q++) {
          pop(ctx, out, B, when + 0.04 + q * 0.075, 0.055, 0.24);
        }
      });
    }

    /* 飘字：轻轻一记，不抢爆炸 */
    at(numAt, function (when) {
      thud(ctx, out, B, when, 0.14, 0.10 * level, 0.12);
      pop(ctx, out, B, when + 0.01, 0.04 * level, 0.12);
    });

    for (var e = 0; e < events.length; e++) {
      var event = events[e];
      if (!(event.at > fromT && event.at <= toT)) continue;
      event.fire(whenOf(event.at));
    }
  }

  /* ------------------------------------------------------------ 声道 ---- */
  function create(timeline) {
    var T = timeline || {};
    var session = null;
    var on = false;
    var lastT = -1;
    var muted = false;

    return {
      start: function () {
        var g = graph();
        if (!g) return false;
        if (g.ctx.state === "suspended" && typeof g.ctx.resume === "function") {
          g.ctx.resume();
        }
        if (session) {
          try { session.disconnect(); } catch (error) { /* ignore */ }
        }
        session = g.ctx.createGain();
        session.gain.value = muted ? 0 : 1;
        session.connect(g.comp);
        on = true;
        lastT = -1;
        return true;
      },
      stop: function () {
        on = false;
        var g = graph();
        if (!session || !g) return;
        var old = session;
        session = null;
        try { old.gain.setTargetAtTime(0.0001, g.ctx.currentTime, 0.012); } catch (error) { /* ignore */ }
        setTimeout(function () {
          try { old.disconnect(); } catch (error) { /* ignore */ }
        }, 90);
      },
      mute: function (mutedFlag) {
        muted = Boolean(mutedFlag);
        if (session) session.gain.value = muted ? 0 : 1;
      },
      tick: function (t, voice) {
        if (!on) { lastT = t; return; }
        var g = graph();
        if (!g || !session) { lastT = t; return; }
        if (lastT < 0) { lastT = t; return; }
        if (t > lastT) {
          fireEvents(g.ctx, session, g.buffers, T, (voice && voice.dmg) || 1,
            lastT, t, g.ctx.currentTime, false);
        }
        lastT = t;
      }
    };
  }

  /* ------------------------------------------------------------ 离线 ---- */
  function stats(buffer) {
    var peak = 0, sum = 0, count = 0;
    for (var ch = 0; ch < buffer.numberOfChannels; ch++) {
      var d = buffer.getChannelData(ch);
      for (var i = 0; i < d.length; i++) {
        var v = d[i];
        if (!isFinite(v)) continue;
        var av = Math.abs(v);
        if (av > peak) peak = av;
        sum += v * v;
        count++;
      }
    }
    return { peak: +peak.toFixed(4), rms: +Math.sqrt(sum / Math.max(1, count)).toFixed(4) };
  }

  /* `?audiotest=1` / 导出用：按给定时间轴离线渲染一档，回总电平 + 四个窗口的 RMS。 */
  function renderOffline(variant, timeline, seconds) {
    var OAC = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    if (!OAC) return Promise.resolve(null);
    var dur = Number(seconds) > 0 ? Number(seconds) : 2.4;
    var ctx = new OAC(2, Math.ceil(SR * dur), SR);
    var comp = compressor(ctx);
    var master = ctx.createGain();
    master.gain.value = 1.0;
    master.connect(comp);
    var B = buffers(ctx);
    // 离线：时间轴从 0 起算，事件落到"时间轴秒数"上（`fromT = 0`，`exact` 用 `at - 0`）。
    fireEvents(ctx, master, B, timeline, variant, 0, Infinity, 0, true);
    return ctx.startRendering().then(function (buffer) {
      var level = stats(buffer);
      var ch = buffer.getChannelData(0);
      function win(a, b) {
        var i0 = Math.floor(a * SR), i1 = Math.min(ch.length, Math.floor(b * SR));
        var s = 0, peak = 0;
        for (var i = i0; i < i1; i++) {
          var x = ch[i];
          s += x * x;
          if (Math.abs(x) > peak) peak = Math.abs(x);
        }
        var n = Math.max(1, i1 - i0);
        return { rms: Math.sqrt(s / n), peak: peak };
      }
      var T = timeline || {};
      var impact = Number(T.IMPACT) || 0;
      var blast = Number(T.EXPLODE) || Number(T.BLAST) || 0;
      return {
        total: level,
        charge: win(Number(T.CHARGE) || 0, Number(T.LAUNCH) || 0),
        launch: win(Number(T.LAUNCH) || 0, (Number(T.LAUNCH) || 0) + 0.08),
        flight: win((Number(T.LAUNCH) || 0) + 0.02, impact - 0.01),
        hit: win(impact, impact + 0.12),
        blast: blast > 0 ? win(blast, blast + 0.3) : { rms: 0, peak: 0 }
      };
    });
  }

  global.SgsFireSfx = {
    create: create,
    renderOffline: renderOffline,
    /* 十一个音色单独导出：调音时不必碰排程（与 `SgsSfx` / `SgsThunderSfx` 的姿态一致） */
    thud: thud, whoosh: whoosh, pop: pop, crack: crack,
    sizzle: sizzle, riser: riser, swell: swell, meteorFall: meteorFall,
    boom: boom, shock: shock, rumble: rumble
  };
})(window);
