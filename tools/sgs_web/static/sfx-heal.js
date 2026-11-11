"use strict";

/* SGS 前端 · **回复体力**演出的音效（vital-fx 票 04）：`SgsHealSfx`。
 *
 * 移植自素材包 `outputs/sgs-heal-fx/sfx-heal.js`：这套声音的基调是「温润、上行、化开」——
 *   起光     一声清磬（1 档）/ 三音上行（宫 → 角 → 徵，2 档）/ 一口起势的气声（3 档）
 *   聚气     带通噪声 300→2600Hz 上扫 + 低频蓄势 + 越收越紧的高处细响（3 档）
 *   白住     几乎没声：一点高处的亮 + 58→76Hz 的低频在垫（3 档）
 *   迸发     一记很软的低响 + 五音泛音串 + 长余韵的磬 + 一口气 + 几颗小磬（3 档）
 *   飘字     一小口气声（所有档），1 / 2 档另有一串上行的泛音
 * 全部由 Web Audio 现场合成，没有音频文件、没有外部资源。**不做爆炸，不做金属噪声**——
 * 回复是"长回来"，不是"打出去"。
 *
 * 接口与 `SgsSfx` / `SgsHpLossSfx` **同形**，`sfx-ui.js` 的声道池直接驱动（它不认第二种形状）：
 *
 *   var lane = SgsHealSfx.create(timeline);      // timeline 是**可变**的引用
 *   lane.start();                                // 手势里 resume（没有 AudioContext ⇒ false）
 *   lane.tick(seconds, voice);                   // 每帧喂演出自己的秒数
 *   lane.stop();  lane.mute(on);
 *   SgsHealSfx.renderOffline(variant, timeline, seconds)   // ?audiotest=1 / 导出
 *
 * 三条硬口径（与 `sfx.js` / `sfx-hp-loss.js` 一致）：
 * 1. **cue 与画面同一条时钟**：`tick(t)` 的 `t` 就是喂 `fx.draw(t)` 的那个秒数；cue 时刻读
 *    声道里那份可变时间轴（`sfx-ui.js` 开演前把画面模块的 `timeline` 整份写进来），不另读墙钟。
 *    素材包是"单场播放器"（`unlock/play/stop`，自己按 `TIERS` 排程）——这里改成**车道适配器**，
 *    与雷 / 火 / 失去体力同一个改法：素材页那条「吸气（1.02s）→ 留白（~0.3s）→ 落点 →
 *    长余韵」的**位置全部来自时间轴**（`GLOW` / `GATHER_START` / `GATHER` / `HOLD` /
 *    `BURST` / `NUM`），于是压进牌桌档（3+ 档 4.5s）之后三拍还是同一套比例，本文件不写死任何秒数。
 * 2. **多声道并发**：几条声道共用一个模块级 `AudioContext` + 压缩器 + 一条反馈延迟；每条声道一个
 *    session gain（素材页是单场，同时两条只响最后一条——这里是按声道隔离的）。
 * 3. **没有 `AudioContext` 就静默降级**：`start()` 返回 false，画面照旧。
 */
(function (global) {
  var SR = 44100;
  /* 共享图：{ ctx, comp, noise, echo }。第一次 start() 时建；建不出来就一直是 null。 */
  var GRAPH = null;

  /* 三档的响度（素材页 `play()` 里那张表）：档越高越厚，但都压在削波线以下。 */
  var LEVEL = [0, 0.95, 1.06, 1.18];

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

  function compressor(ctx) {
    var comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -22;
    comp.knee.value = 14;
    comp.ratio.value = 3.5;
    comp.attack.value = 0.004;
    comp.release.value = 0.28;
    comp.connect(ctx.destination);
    return comp;
  }

  /* 一条小小的反馈延迟：给磬与泛音留尾（素材页的 `dl` / `fb` / `lp` 三个节点）。
   * 只接在压缩器**之前**：尾音照样过压缩器，不会被挤爆。 */
  function echo(ctx, out) {
    var dl = ctx.createDelay(1.0);
    dl.delayTime.value = 0.21;
    var fb = ctx.createGain();
    fb.gain.value = 0.26;
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 3200;
    dl.connect(lp);
    lp.connect(fb);
    fb.connect(dl);
    lp.connect(out);
    return dl;
  }

  /* 模块级共享图：几条声道共用，避免每条声道各建一个 AudioContext。 */
  function graph() {
    if (GRAPH) return GRAPH;
    var AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) return null;
    try {
      var ctx = new AC();
      var comp = compressor(ctx);
      GRAPH = {
        ctx: ctx,
        comp: comp,
        noise: makeNoise(ctx, 3.0, 0x2f17),
        echo: echo(ctx, comp)
      };
    } catch (error) {
      GRAPH = null;
    }
    return GRAPH;
  }

  function panner(ctx, pan) {
    if (typeof ctx.createStereoPanner === "function") {
      var p = ctx.createStereoPanner();
      p.pan.value = clamp(pan || 0, -1, 1);
      return p;
    }
    return ctx.createGain();
  }

  function gainEnv(ctx, when, attack, peak, end) {
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), when + Math.max(0.003, attack));
    g.gain.exponentialRampToValueAtTime(0.0001, end);
    return g;
  }

  /* 噪声起点：素材页用 `Math.random()` 挑 offset，这里按 `when` 取一个固定偏移——离线渲染
   * 逐次同值（与 `sfx-thunder.js` / `sfx-hp-loss.js` 同一口径：同一时刻永远同一段噪声）。 */
  function noiseStart(ctx, B, when, dur, offsetMax) {
    var s = ctx.createBufferSource();
    s.buffer = B.noise;
    var off = (when * 997 % 1) * (offsetMax || 1.0);
    s.start(when, off, dur + 0.06);
    s.stop(when + dur + 0.08);
    return s;
  }

  /* ------------------------------------------------------------ 音色 ----
   * 六个音色各自一个函数（调音时不必碰排程；与 `SgsSfx` / `SgsHpLossSfx` 的姿态一致）。 */

  /* 磬：基音 + 两个偏亮的泛音，衰减快；`send` 是反馈延迟，给一点尾音。 */
  function bell(ctx, out, when, freq, dur, gain, pan, send) {
    var p = panner(ctx, pan);
    p.connect(out);

    var parts = [
      { f: freq, g: 1.0, type: "sine" },
      { f: freq * 2.01, g: 0.34, type: "sine" },
      { f: freq * 3.03, g: 0.11, type: "sine" },
      { f: freq * 1.004, g: 0.55, type: "triangle" }
    ];
    for (var i = 0; i < parts.length; i++) {
      var o = ctx.createOscillator();
      o.type = parts[i].type;
      o.frequency.setValueAtTime(parts[i].f, when);
      var g = gainEnv(ctx, when, 0.006, gain * parts[i].g,
        when + dur * (i === 0 ? 1 : 0.45 + i * 0.08));
      o.connect(g);
      g.connect(p);
      if (send) {
        var s = ctx.createGain();
        s.gain.value = 0.28;
        g.connect(s);
        s.connect(send);
      }
      o.start(when);
      o.stop(when + dur + 0.08);
    }
  }

  /* 泛音串：像琵琶扫了一下，几个音错开几毫秒。 */
  function harp(ctx, out, when, notes, gain, pan, spacing) {
    var gap = spacing || 0.055;
    for (var i = 0; i < notes.length; i++) {
      var o = ctx.createOscillator();
      o.type = "triangle";
      o.frequency.setValueAtTime(notes[i], when + i * gap);
      var g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, when + i * gap);
      g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain * (1 - i * 0.09)),
        when + i * gap + 0.006);
      g.gain.exponentialRampToValueAtTime(0.0001, when + i * gap + 1.35);
      var p = panner(ctx, pan * (0.6 + i * 0.1));
      o.connect(g); g.connect(p); p.connect(out);
      o.start(when + i * gap);
      o.stop(when + i * gap + 1.45);
    }
  }

  /* 吸气：带通噪声往上扫，像聚气的风。 */
  function breath(ctx, out, B, when, dur, gain, f0, f1, pan) {
    var src = noiseStart(ctx, B, when, dur, 0.9);
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.Q.value = 2.0;
    bp.frequency.setValueAtTime(f0, when);
    bp.frequency.exponentialRampToValueAtTime(f1, when + dur);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), when + dur * 0.82);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur + 0.03);
    var p = panner(ctx, pan);
    src.connect(bp); bp.connect(g); g.connect(p); p.connect(out);
  }

  /* 低频的"暖"：聚气蓄势，也做爆发落点的底。 */
  function warm(ctx, out, when, dur, gain, f0, f1, pan) {
    var o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(f0, when);
    o.frequency.exponentialRampToValueAtTime(f1, when + dur);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, gain), when + dur * 0.6);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    var p = panner(ctx, pan);
    o.connect(g); g.connect(p); p.connect(out);
    o.start(when); o.stop(when + dur + 0.08);
  }

  /* 落点：一声很软的低响 + 一片化开的气。 */
  function bloom(ctx, out, B, when, gain) {
    warm(ctx, out, when, 0.75, gain * 0.55, 128, 42, 0.05);
    var src = noiseStart(ctx, B, when, 0.55, 1.0);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(2400, when);
    lp.frequency.exponentialRampToValueAtTime(240, when + 0.5);
    var g = gainEnv(ctx, when, 0.02, gain * 0.55, when + 0.55);
    var p = panner(ctx, 0.05);
    src.connect(lp); lp.connect(g); g.connect(p); p.connect(out);
  }

  /* 高处的细碎光：几颗小磬错开。 */
  function sparkle(ctx, out, when, gain, pan, seed) {
    var base = [2093.0, 2349.32, 2637.0, 3135.96];
    for (var i = 0; i < base.length; i++) {
      bell(ctx, out, when + i * 0.06 + (seed % 3) * 0.012, base[i], 0.45,
        gain * 0.35, pan + (i - 1.5) * 0.12, null);
    }
  }

  /* ------------------------------------------------------------ 排程 ----
   * 事件表在每次调用时按**当前**时间轴算一遍（时间轴是可变引用，开演前被改写）。
   * `fromT < at <= toT` 的事件交给 `fire`，`when0` 是它们落到音频时钟上的起点：
   * 实时 tick 用"现在"（帧粒度），离线渲染用精确的 `at - fromT`。
   *
   * **每一拍的时刻都读时间轴**（`GLOW` / `GATHER_START` / `GATHER` / `HOLD` / `BURST` /
   * `NUM` / `DURATION` / `TIER`），本文件不写死任何一档的秒数——`anim.ini` 改节拍，声音跟着走。
   * `TIER` 只决定**音色**（清磬 / 三音上行 / 吸气→落点），不决定时刻。 */
  function fireEvents(ctx, out, B, send, timeline, fromT, toT, when0, exact) {
    var T = timeline || {};
    function seconds(value) {
      var n = Number(value);
      return isFinite(n) && n > 0 ? n : 0;
    }
    var tier = Math.round(Number(T.TIER));
    if (!isFinite(tier) || tier < 1) tier = 1;
    tier = Math.min(3, tier);
    var level = LEVEL[tier];
    var glow = seconds(T.GLOW);
    var gatherStart = seconds(T.GATHER_START);
    var gatherEnd = seconds(T.GATHER);
    var hold = seconds(T.HOLD);
    var burstAt = seconds(T.BURST);
    var numAt = seconds(T.NUM);
    var duration = seconds(T.DURATION);

    var events = [];
    function at(time, fire) {
      if (time > 0) events.push({ at: time, fire: fire });
    }
    function whenOf(time) {
      return when0 + (exact ? Math.max(0, time - fromT) : 0);
    }

    /* ---- 起光：三档都有自己的第一声（音效的**第一拍**落在时间轴的 GLOW 上）---- */
    at(glow, function (when) {
      if (tier === 1) {
        /* 1 点：一声清磬（D6 + A6 泛音），尾巴开得很小 */
        bell(ctx, out, when, 1046.5, 1.35, 0.085 * level, 0.0, send);
        bell(ctx, out, when + 0.02, 1568.0, 1.05, 0.028 * level, 0.10, send);
      } else if (tier === 2) {
        /* 2 点：三音上行（A5 → C#6 → E6），底下垫一口 220Hz 的暖气 */
        var notes2 = [880, 1108.73, 1318.51];
        for (var i = 0; i < notes2.length; i++) {
          bell(ctx, out, when + i * 0.085, notes2[i], 1.15 + i * 0.1,
            0.062 * level, -0.08 + i * 0.08, send);
        }
        warm(ctx, out, when, 1.25, 0.022 * level, 220, 220, 0.0);
      } else {
        /* 3 点：起势的一口气（聚气那口大吸气在 GATHER_START，这里只是"亮起来"） */
        breath(ctx, out, B, when, 0.42, 0.014 * level, 380, 980, 0.0);
      }
    });

    /* ---- 1 / 2 档的余韵：一串上行的泛音 + 尾部一小口气 ---- */
    if (tier < 3) {
      at(numAt, function (when) {
        harp(ctx, out, when, tier === 1 ? [1174.66, 1567.98] : [1318.51, 1760.0, 2093.0],
          0.030 * level, 0.06, tier === 1 ? 0.07 : 0.06);
      });
      /* 尾部那口气的**时刻**从时间轴算：飘字到摘层之间的 55%（不写死 0.95s）。
       * `duration` 为 0（关掉这一档）时不排。 */
      if (duration > numAt) {
        at(numAt + (duration - numAt) * 0.55, function (when) {
          breath(ctx, out, B, when, 0.45, 0.010 * level, 700, 1500, 0.0);
        });
      }
    }

    /* ---- 3 档：吸气 → 白住 → 迸发 → 长余韵 ---- */
    if (tier === 3 && burstAt > 0) {
      /* 吸气：一层上升的风 + 低频蓄势 + 越收越紧的高处细声（细响按聚气段均分） */
      at(gatherStart, function (when) {
        var span = Math.max(0.12, gatherEnd - gatherStart);
        breath(ctx, out, B, when, span * 0.96, 0.055 * level, 300, 2600, 0.0);
        warm(ctx, out, when, span * 1.02, 0.045 * level, 96, 420, 0.0);
        var steps = 10;
        for (var k = 1; k < steps; k++) {
          (function (ratio) {
            var tick = gatherStart + span * ratio;
            at(tick, function (when2) {
              bell(ctx, out, when2, 2400 + ratio * 1800, 0.16, 0.010 * (0.3 + ratio),
                (ratio - 0.5) * 0.4, null);
            });
          })(k / steps);
        }
      });
      /* 白住：几乎没声，只留一点高处的亮和 58→76Hz 的低频在垫（时长读 `HOLD`） */
      at(gatherEnd, function (when) {
        bell(ctx, out, when + 0.005, 2637.0, 0.55, 0.012 * level, 0.0, null);
        warm(ctx, out, when, Math.max(0.08, hold), 0.020 * level, 58, 76, 0.0);
      });
      /* 迸发：软低响 + 五音泛音 + 长余韵的磬 + 一口气 + 几颗小磬 */
      at(burstAt, function (when) {
        bloom(ctx, out, B, when, 0.42 * level);
        harp(ctx, out, when + 0.02, [587.33, 739.99, 880.0, 1174.66, 1479.98],
          0.075 * level, 0.0, 0.05);
        bell(ctx, out, when + 0.03, 1568.0, 2.0, 0.045 * level, 0.02, send);
        breath(ctx, out, B, when + 0.02, 0.9, 0.020 * level, 2600, 700, 0.0);
        sparkle(ctx, out, when + 0.12, 0.045 * level, 0.1, 3);
      });
      at(numAt, function (when) {
        bell(ctx, out, when, 1046.5, 1.4, 0.032 * level, 0.0, send);
      });
    }

    /* 飘字：轻轻一下，所有档位都有 */
    at(numAt, function (when) {
      breath(ctx, out, B, when, 0.35, 0.010 * level, 900, 1600, 0.1);
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
        try { old.gain.setTargetAtTime(0.0001, g.ctx.currentTime, 0.015); } catch (error) { /* ignore */ }
        setTimeout(function () {
          try { old.disconnect(); } catch (error) { /* ignore */ }
        }, 110);
      },
      mute: function (mutedFlag) {
        muted = Boolean(mutedFlag);
        if (session) session.gain.value = muted ? 0 : 1;
      },
      /* 每帧喂演出自己的秒数（与 `fx.draw(t)` 同一个数）。第一帧只记游标：素材页的那一拍
       * 发生在开演之前的不排（`lastT < 0` 那一支）。 */
      tick: function (t, voice) {
        if (!on) { lastT = t; return; }
        var g = graph();
        if (!g || !session) { lastT = t; return; }
        if (lastT < 0) { lastT = t; return; }
        if (t > lastT) {
          fireEvents(g.ctx, session, g.noise, g.echo, T, lastT, t, g.ctx.currentTime, false);
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

  /* `?audiotest=1` / 导出用：按给定时间轴离线渲染一档，回总电平 + 五个窗口的 RMS
   * （起光 / 聚气 / 白住 / 迸发 / 飘字）。没有 `OfflineAudioContext` ⇒ `null`
   * （与 `SgsSfx` / `SgsHpLossSfx` 同一条降级路径）。 */
  function renderOffline(variant, timeline, seconds) {
    var OAC = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    if (!OAC) return Promise.resolve(null);
    var dur = Number(seconds) > 0 ? Number(seconds) : 5;
    var ctx = new OAC(2, Math.ceil(SR * dur), SR);
    var comp = compressor(ctx);
    var master = ctx.createGain();
    master.gain.value = 1.0;
    master.connect(comp);
    var B = { noise: makeNoise(ctx, 3.0, 0x2f17) };
    var send = echo(ctx, comp);
    // 离线：时间轴从 0 起算，事件落到"时间轴秒数"上（`fromT = 0`，`exact` 用 `at - 0`）。
    fireEvents(ctx, master, B, send, timeline, 0, Infinity, 0, true);
    return ctx.startRendering().then(function (buffer) {
      function win(a, b) {
        var i0 = Math.max(0, Math.floor(a * SR));
        var i1 = Math.min(buffer.length, Math.floor(b * SR));
        var s = 0, peak = 0;
        for (var i = i0; i < i1; i++) {
          var x = buffer.getChannelData(0)[i];
          s += x * x;
          if (Math.abs(x) > peak) peak = Math.abs(x);
        }
        var n = Math.max(1, i1 - i0);
        return { rms: Math.sqrt(s / n), peak: peak };
      }
      var T = timeline || {};
      var glow = Number(T.GLOW) || 0;
      var gather = Number(T.GATHER_START) || 0;
      var hold = Number(T.GATHER) || 0;
      var burst = Number(T.BURST) || 0;
      var num = Number(T.NUM) || 0;
      return {
        total: stats(buffer),
        glow: win(glow, glow + 0.3),
        gather: win(gather, gather + 0.3),
        hold: win(hold, hold + 0.2),
        burst: win(burst, burst + 0.3),
        num: win(num, num + 0.2),
        duration: +dur.toFixed(2)
      };
    });
  }

  global.SgsHealSfx = {
    create: create,
    renderOffline: renderOffline,
    /* 六个音色单独导出：调音时不必碰排程（与 `SgsSfx` / `SgsHpLossSfx` 的姿态一致）。 */
    bell: bell, harp: harp, breath: breath, warm: warm, bloom: bloom, sparkle: sparkle
  };
})(window);
