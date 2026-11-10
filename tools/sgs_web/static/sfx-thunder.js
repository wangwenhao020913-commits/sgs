"use strict";

/* SGS 前端 · **雷电**伤害演出的音效（damage-fx-natures 票 01）：`SgsThunderSfx`。
 *
 * 移植自素材包 `outputs/sgs-thunder-fx/sfx-thunder.js`：蓄电的上升电流、出雷的放电、
 * 命中低频轰响、电火嘶声；4 档再加雷云低频、云内预闪、天雷撕裂与爆炸轰鸣。全部由
 * Web Audio 现场合成，没有音频文件、没有外部资源。
 *
 * 接口与 `SgsSfx` **同形**，`sfx-ui.js` 的声道池直接驱动（它不认第二种形状）：
 *
 *   var lane = SgsThunderSfx.create(timeline);   // timeline 是**可变**的引用
 *   lane.start();                                // 手势里 resume（没有 AudioContext ⇒ false）
 *   lane.tick(seconds, voice);                   // 每帧喂演出自己的秒数
 *   lane.stop();  lane.mute(on);
 *   SgsThunderSfx.renderOffline(variant, timeline, seconds)   // ?audiotest=1 / 导出
 *
 * 三条硬口径（与 `sfx.js` / `sfx-ui.js` 一致）：
 * 1. **cue 与画面同一条时钟**：`tick(t)` 的 `t` 就是喂 `fx.draw(t)` 的那个秒数；cue 时刻读
 *    声道里那份可变时间轴（`sfx-ui.js` 开演前按该档写 CHARGE / LAUNCH / IMPACT / SKY_AT /
 *    EXPLODE），不另读墙钟。
 * 2. **多声道并发**：三条声道共用一个模块级 `AudioContext` + 压缩器，每条声道一个 session
 *    gain（素材页是单场播放器，同时三条雷电只响最后一条——这里是按声道隔离的）。
 * 3. **没有 `AudioContext` 就静默降级**：`start()` 返回 false，画面照旧。
 */
(function (global) {
  var SR = 44100;
  /* 共享图：{ ctx, comp, noise, brown }。第一次 start() 时建；建不出来就一直是 null。 */
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

  function noiseStart(ctx, B, when, dur, offsetMax) {
    var s = ctx.createBufferSource();
    s.buffer = B.noise;
    var off = (when * 997 % 1) * (offsetMax || 1.0);
    s.start(when, off, dur + 0.06);
    s.stop(when + dur + 0.08);
    return s;
  }

  /* ------------------------------------------------------------ 音色 ---- */
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

  function boom(ctx, out, B, when, dur, gain, pan) {
    var osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(92, when);
    osc.frequency.exponentialRampToValueAtTime(27, when + dur);
    var g = gainEnv(ctx, when, 0.006, gain, when + dur);
    var p = panner(ctx, pan);
    osc.connect(g); g.connect(p); p.connect(out);
    osc.start(when); osc.stop(when + dur + 0.06);

    var src = noiseStart(ctx, B, when, Math.min(dur, 0.28), 1.2);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 160;
    var g2 = gainEnv(ctx, when, 0.004, gain * 0.55, when + Math.min(dur, 0.28));
    src.connect(lp); lp.connect(g2); g2.connect(p);
  }

  function zap(ctx, out, B, when, dur, gain, pan0, pan1) {
    var osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(1700, when);
    osc.frequency.exponentialRampToValueAtTime(110, when + dur);
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 900;
    bp.Q.value = 2.4;
    var g = gainEnv(ctx, when, 0.002, gain, when + dur);
    var p = panner(ctx, pan0);
    if (p.pan) {
      p.pan.setValueAtTime(pan0, when);
      p.pan.linearRampToValueAtTime(pan1, when + dur);
    }
    osc.connect(bp); bp.connect(g); g.connect(p); p.connect(out);
    osc.start(when); osc.stop(when + dur + 0.05);
  }

  function sizzle(ctx, out, B, when, dur, gain, pan) {
    var src = noiseStart(ctx, B, when, dur, 0.8);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 2600;
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 5400;
    bp.Q.value = 0.7;
    var g = gainEnv(ctx, when, 0.012, gain, when + dur);
    var trem = ctx.createGain();
    trem.gain.value = 0.62;
    var lfo = ctx.createOscillator();
    lfo.type = "sine";
    lfo.frequency.value = 23;
    var lg = ctx.createGain();
    lg.gain.value = 0.32;
    lfo.connect(lg); lg.connect(trem.gain);
    var p = panner(ctx, pan);
    src.connect(hp); hp.connect(bp); bp.connect(g); g.connect(trem); trem.connect(p); p.connect(out);
    lfo.start(when); lfo.stop(when + dur + 0.06);
  }

  function riser(ctx, out, B, when, dur, gain) {
    dur = Math.max(0.08, dur);
    var osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(55, when);
    osc.frequency.exponentialRampToValueAtTime(320, when + dur);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(280, when);
    lp.frequency.exponentialRampToValueAtTime(1700, when + dur);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(gain, when + dur * 0.82);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur + 0.04);
    var p = panner(ctx, -0.16);
    osc.connect(lp); lp.connect(g); g.connect(p); p.connect(out);
    osc.start(when); osc.stop(when + dur + 0.08);

    var src = noiseStart(ctx, B, when, dur, 0.8);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 1200;
    var g2 = ctx.createGain();
    g2.gain.setValueAtTime(0.0001, when);
    g2.gain.exponentialRampToValueAtTime(gain * 0.42, when + dur * 0.85);
    g2.gain.exponentialRampToValueAtTime(0.0001, when + dur + 0.04);
    src.connect(hp); hp.connect(g2); g2.connect(p);
  }

  function rumble(ctx, out, B, when, dur, gain) {
    dur = Math.max(0.12, dur);
    var src = ctx.createBufferSource();
    src.buffer = B.brown;
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 120;
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

  function strobe(ctx, out, B, when, gain) {
    crack(ctx, out, B, when, 0.05, gain, 3600, 0);
    var osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.value = 1500;
    var g = gainEnv(ctx, when, 0.002, gain * 0.22, when + 0.05);
    var p = panner(ctx, 0);
    osc.connect(g); g.connect(p); p.connect(out);
    osc.start(when); osc.stop(when + 0.07);
  }

  /* ------------------------------------------------------------ 排程 ----
   * 事件表在每次调用时按**当前**时间轴与档位算一遍（时间轴是可变引用，开演前被改写）。
   * `fromT < at <= toT` 的事件交给 `fire`，`when0` 是它们落到音频时钟上的起点：
   * 实时 tick 用"现在"（帧粒度），离线渲染用精确的 `at - fromT`。 */
  var LEVEL = [0, 0.58, 0.78, 0.96, 1.0];

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
    var impact = num(T.IMPACT);
    var sky = num(T.SKY_AT);
    var explode = num(T.EXPLODE);
    var events = [];
    function at(time, fire) {
      if (time > 0) events.push({ at: time, fire: fire });
    }
    function whenOf(time) {
      return when0 + (exact ? Math.max(0, time - fromT) : 0);
    }

    // 蓄电：上升电流 + 几下细碎放电（分布在蓄电窗口里）
    var chargeSpan = Math.max(0.08, launch - charge);
    at(charge, function (when) {
      riser(ctx, out, B, when, chargeSpan, 0.075 * level);
      var fractions = id === 1 ? [0.28, 0.62] : [0.2, 0.4, 0.6, 0.8];
      for (var i = 0; i < fractions.length; i++) {
        crack(ctx, out, B, when + chargeSpan * fractions[i], 0.05, 0.048 + 0.014 * id, 2100, -0.2);
      }
    });
    // 出雷
    at(launch, function (when) {
      zap(ctx, out, B, when, 0.13, 0.10 * level, -0.28, 0.22);
      crack(ctx, out, B, when, 0.09, 0.12 * level, 1600, -0.1);
    });
    // 命中
    at(impact, function (when) {
      boom(ctx, out, B, when, 0.55, 0.62 * level, 0.22);
      crack(ctx, out, B, when, 0.14, 0.55 * level, 650, 0.20);
      sizzle(ctx, out, B, when, 0.35 + 0.08 * id, 0.12 * level, 0.18);
      if (id >= 2) sizzle(ctx, out, B, when + 0.02, 0.55, 0.075 * level, 0.18);
      if (id === 3) {
        strobe(ctx, out, B, when, 0.095 * level);
        strobe(ctx, out, B, when + 0.07, 0.075 * level);
        strobe(ctx, out, B, when + 0.135, 0.05 * level);
      } else if (id === 4) {
        strobe(ctx, out, B, when, 0.055 * level);
      }
    });
    if (id === 4) {
      // 雷云低频：从命中一直压到天雷落下
      at(impact, function (when) {
        if (sky > impact) rumble(ctx, out, B, when, sky - impact, 0.26);
      });
      // 云内预闪：紧挨天雷之前的三下细碎放电
      at(sky, function (when) {
        crack(ctx, out, B, when - 0.18, 0.05, 0.06, 2300, -0.1);
        crack(ctx, out, B, when - 0.10, 0.06, 0.08, 2300, -0.1);
        crack(ctx, out, B, when - 0.04, 0.08, 0.10, 2300, -0.1);
      });
      // 天雷本体
      at(sky, function (when) {
        crack(ctx, out, B, when, 0.18, 0.62, 480, 0);
        boom(ctx, out, B, when, 0.32, 0.38, 0);
        strobe(ctx, out, B, when, 0.11);
      });
      // 爆炸：轰鸣 + 撕裂 + 长尾 + 三连闪
      at(explode, function (when) {
        boom(ctx, out, B, when, 0.75, 0.95, 0.04);
        crack(ctx, out, B, when, 0.22, 0.90, 420, 0.04);
        sizzle(ctx, out, B, when, 0.9, 0.20, 0.1);
        rumble(ctx, out, B, when, 0.9, 0.32);
        strobe(ctx, out, B, when, 0.16);
        strobe(ctx, out, B, when + 0.07, 0.10);
        strobe(ctx, out, B, when + 0.14, 0.06);
      });
    }

    for (var i = 0; i < events.length; i++) {
      var event = events[i];
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

  /* `?audiotest=1` / 导出用：按给定时间轴离线渲染一档，回分区 RMS。 */
  function renderOffline(variant, timeline, seconds) {
    var OAC = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    if (!OAC) return Promise.resolve(null);
    var dur = Number(seconds) > 0 ? Number(seconds) : 2.6;
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
      return {
        total: level,
        charge: win(Number(T.CHARGE) || 0, Number(T.LAUNCH) || 0),
        launch: win(Number(T.LAUNCH) || 0, (Number(T.LAUNCH) || 0) + 0.1),
        flight: win((Number(T.LAUNCH) || 0) + 0.02, (Number(T.IMPACT) || 0) - 0.01),
        hit: win(Number(T.IMPACT) || 0, (Number(T.IMPACT) || 0) + 0.12)
      };
    });
  }

  global.SgsThunderSfx = {
    create: create,
    renderOffline: renderOffline,
    /* 六个音色单独导出：调音时不必碰排程（与 `SgsSfx` 的姿态一致） */
    riser: riser, crack: crack, boom: boom, zap: zap,
    sizzle: sizzle, rumble: rumble, strobe: strobe
  };
})(window);
