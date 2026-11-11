"use strict";

/* SGS 前端 · **失去体力**演出的音效（vital-fx 票 03）：`SgsHpLossSfx`。
 *
 * 移植自素材包 `outputs/sgs-hp-loss-fx/sfx-hp-loss.js`：这套声音的基调是「闷、湿、低」——
 *   纸吸水   低频带通噪声慢慢升起来
 *   凝珠 / 断开  一声很轻的表面张力"嗒" + 一声极短的下滑
 *   坠落     一口短气声（带通往下扫）
 *   落地     一记"啵"（正弦 420→95Hz）+ 低频闷响 + 一点水声 + 纸吸掉的沙声
 *   飘字     一记很软的木质"咚"（196 / 294 / 392Hz 三个弱泛音）
 *   三点档   另加一口低频墨气（45Hz 慢慢散开）
 * 全部由 Web Audio 现场合成，没有音频文件、没有外部资源。
 *
 * 接口与 `SgsSfx` / `SgsThunderSfx` / `SgsFireSfx` **同形**，`sfx-ui.js` 的声道池直接驱动
 * （它不认第二种形状）：
 *
 *   var lane = SgsHpLossSfx.create(timeline);    // timeline 是**可变**的引用
 *   lane.start();                                // 手势里 resume（没有 AudioContext ⇒ false）
 *   lane.tick(seconds, voice);                   // 每帧喂演出自己的秒数
 *   lane.stop();  lane.mute(on);
 *   SgsHpLossSfx.renderOffline(variant, timeline, seconds)   // ?audiotest=1 / 导出
 *
 * 三条硬口径（与 `sfx.js` / `sfx-fire.js` 一致）：
 * 1. **cue 与画面同一条时钟**：`tick(t)` 的 `t` 就是喂 `fx.draw(t)` 的那个秒数；cue 时刻读
 *    声道里那份可变时间轴（`sfx-ui.js` 开演前把画面模块的 `timeline` 整份写进来），不另读墙钟。
 *    素材包是"单场播放器"（`unlock/play/stop`，自己按 `TIERS` 排程）——这里改成**车道适配器**，
 *    与雷 / 火同一个改法：排程表不在本文件里再写一份，`SPOT` / `BREAK` / `GAP` / `IMPACT` /
 *    `NUM` / `COUNT` 全部来自时间轴。
 * 2. **多声道并发**：几条声道共用一个模块级 `AudioContext` + 压缩器，每条声道一个 session
 *    gain（素材页是单场，同时两条只响最后一条——这里是按声道隔离的）。
 * 3. **没有 `AudioContext` 就静默降级**：`start()` 返回 false，画面照旧。
 */
(function (global) {
  var SR = 44100;
  /* 共享图：{ ctx, comp, noise }。第一次 start() 时建；建不出来就一直是 null。 */
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

  /* 模块级共享图：几条声道共用，避免每条声道各建一个 AudioContext。 */
  function graph() {
    if (GRAPH) return GRAPH;
    var AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) return null;
    try {
      var ctx = new AC();
      GRAPH = { ctx: ctx, comp: compressor(ctx), noise: makeNoise(ctx, 1.2, 0x5e90) };
    } catch (error) {
      GRAPH = null;
    }
    return GRAPH;
  }

  function gainEnv(ctx, when, attack, peak, end) {
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), when + Math.max(0.002, attack));
    g.gain.exponentialRampToValueAtTime(0.0001, end);
    return g;
  }

  /* 噪声起点：素材页用 `Math.random()` 挑 offset，这里按 `when` 取一个固定偏移——离线渲染
   * 逐次同值（与 `sfx-thunder.js` / `sfx-fire.js` 同一口径：同一时刻永远同一段噪声）。 */
  function noiseStart(ctx, B, when, dur, offsetMax) {
    var s = ctx.createBufferSource();
    s.buffer = B.noise;
    var off = (when * 997 % 1) * (offsetMax || 1.0);
    s.start(when, off, dur + 0.06);
    s.stop(when + dur + 0.08);
    return s;
  }

  function bandpass(ctx, freq, q) {
    var f = ctx.createBiquadFilter();
    f.type = "bandpass";
    f.frequency.value = freq;
    f.Q.value = q == null ? 0.9 : q;
    return f;
  }

  /* ------------------------------------------------------------ 音色 ----
   * 六个音色各自一个函数（调音时不必碰排程；与 `SgsSfx` 的姿态一致）。
   * `level` = 该档响度（1 / 2 / 3 颗墨珠逐级抬高，见 `LEVEL`）。 */

  /* 纸吸水：一条很低的带通噪声，慢慢升起来再收掉。 */
  function seep(ctx, out, B, when, level) {
    var src = noiseStart(ctx, B, when, 1.0, 0.8);
    var bp = bandpass(ctx, 180, 1.1);
    bp.frequency.setValueAtTime(180, when);
    bp.frequency.exponentialRampToValueAtTime(760, when + 0.62);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(0.055 * level, when + 0.26);
    g.gain.exponentialRampToValueAtTime(0.0001, when + 1.0);
    src.connect(bp); bp.connect(g); g.connect(out);

    var o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(56, when);
    var og = gainEnv(ctx, when, 0.34, 0.030 * level, when + 1.05);
    o.connect(og); og.connect(out);
    o.start(when); o.stop(when + 1.1);
  }

  /* 凝珠 / 断开：一声很轻的"嗒" + 极短的下滑。 */
  function detach(ctx, out, B, when, level) {
    var o = ctx.createOscillator();
    o.type = "triangle";
    o.frequency.setValueAtTime(1500, when);
    o.frequency.exponentialRampToValueAtTime(720, when + 0.085);
    var g = gainEnv(ctx, when, 0.008, 0.05 * level, when + 0.10);
    o.connect(g); g.connect(out);
    o.start(when); o.stop(when + 0.12);

    var src = noiseStart(ctx, B, when, 0.08, 1.0);
    var bp = bandpass(ctx, 2600, 1.6);
    var ng = gainEnv(ctx, when, 0.006, 0.028 * level, when + 0.075);
    src.connect(bp); bp.connect(ng); ng.connect(out);
  }

  /* 坠落：一口往下扫的短气声。 */
  function fall(ctx, out, B, when, level) {
    var src = noiseStart(ctx, B, when, 0.18, 0.9);
    var bp = bandpass(ctx, 900, 1.4);
    bp.frequency.setValueAtTime(980, when);
    bp.frequency.exponentialRampToValueAtTime(320, when + 0.17);
    var g = gainEnv(ctx, when, 0.03, 0.04 * level, when + 0.18);
    src.connect(bp); bp.connect(g); g.connect(out);
  }

  /* 落地：啵 + 闷响 + 水声 + 纸吸。 */
  function impact(ctx, out, B, when, level) {
    /* "啵"：正弦从 420 掉到 95 */
    var o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(420, when);
    o.frequency.exponentialRampToValueAtTime(95, when + 0.11);
    var g = gainEnv(ctx, when, 0.012, 0.52 * level, when + 0.30);
    o.connect(g); g.connect(out);
    o.start(when); o.stop(when + 0.32);

    /* 低频闷响：桌面的那一下 */
    var lo = ctx.createOscillator();
    lo.type = "sine";
    lo.frequency.setValueAtTime(82, when);
    lo.frequency.exponentialRampToValueAtTime(48, when + 0.26);
    var lg = gainEnv(ctx, when, 0.02, 0.29 * level, when + 0.42);
    lo.connect(lg); lg.connect(out);
    lo.start(when); lo.stop(when + 0.44);

    /* 水声：一小撮中频噪声 */
    var src = noiseStart(ctx, B, when, 0.15, 1.0);
    var bp = bandpass(ctx, 1800, 1.2);
    bp.frequency.setValueAtTime(2400, when);
    bp.frequency.exponentialRampToValueAtTime(900, when + 0.14);
    var ng = gainEnv(ctx, when, 0.008, 0.08 * level, when + 0.15);
    src.connect(bp); bp.connect(ng); ng.connect(out);

    /* 纸把水吸掉：一条很短的高频沙声 */
    var src2 = noiseStart(ctx, B, when + 0.02, 0.24, 1.1);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 3600;
    var hg = gainEnv(ctx, when + 0.02, 0.04, 0.016 * level, when + 0.26);
    src2.connect(hp); hp.connect(hg); hg.connect(out);
  }

  /* 飘字：三个弱泛音的木质"咚"（加一点点木头味的窄带噪声）。 */
  function num(ctx, out, B, when) {
    var freqs = [196, 294, 392];
    var amps = [1, 0.45, 0.24];
    for (var i = 0; i < freqs.length; i++) {
      var o = ctx.createOscillator();
      o.type = i === 0 ? "sine" : "triangle";
      o.frequency.setValueAtTime(freqs[i], when);
      var g = ctx.createGain();
      g.gain.setValueAtTime(0.0001, when);
      g.gain.exponentialRampToValueAtTime(0.16 * amps[i], when + 0.012);
      g.gain.exponentialRampToValueAtTime(0.0001, when + 0.9 + i * 0.15);
      o.connect(g); g.connect(out);
      o.start(when); o.stop(when + 1.1);
    }
    var src = noiseStart(ctx, B, when, 0.05, 1.2);
    var bp = bandpass(ctx, 1400, 2.2);
    var ng = gainEnv(ctx, when, 0.004, 0.064, when + 0.055);
    src.connect(bp); bp.connect(ng); ng.connect(out);
  }

  /* 三点档：一口低频墨气，跟着整张牌湿下去。 */
  function ink(ctx, out, B, when) {
    var o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(46, when);
    o.frequency.linearRampToValueAtTime(39, when + 1.6);
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, when);
    g.gain.exponentialRampToValueAtTime(0.10, when + 0.5);
    g.gain.exponentialRampToValueAtTime(0.0001, when + 1.7);
    o.connect(g); g.connect(out);
    o.start(when); o.stop(when + 1.75);

    var src = noiseStart(ctx, B, when, 1.4, 0.7);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 320;
    var ng = ctx.createGain();
    ng.gain.setValueAtTime(0.0001, when);
    ng.gain.exponentialRampToValueAtTime(0.05, when + 0.45);
    ng.gain.exponentialRampToValueAtTime(0.0001, when + 1.5);
    src.connect(lp); lp.connect(ng); ng.connect(out);
  }

  /* ------------------------------------------------------------ 排程 ----
   * 事件表在每次调用时按**当前**时间轴算一遍（时间轴是可变引用，开演前被改写）。
   * `fromT < at <= toT` 的事件交给 `fire`，`when0` 是它们落到音频时钟上的起点：
   * 实时 tick 用"现在"（帧粒度），离线渲染用精确的 `at - fromT`。
   *
   * 六类 cue 的时刻**全部**读时间轴（`SPOT` / `BREAK` / `GAP` / `IMPACT` / `IMPACT_GAP` /
   * `NUM`），本文件不写死任何一档的秒数——`anim.ini` 改节拍，声音跟着走。 */
  var LEVEL = [0, 0.80, 0.92, 1.0];

  function fireEvents(ctx, out, B, timeline, fromT, toT, when0, exact) {
    var T = timeline || {};
    function seconds(value) {
      var n = Number(value);
      return isFinite(n) && n > 0 ? n : 0;
    }
    function count() {
      var n = Math.round(Number(T.COUNT));
      return isFinite(n) && n > 0 ? n : 1;
    }
    var spots = count();
    var level = LEVEL[Math.min(spots, 3)];
    var spot = seconds(T.SPOT);
    var brk = seconds(T.BREAK);
    var gap = seconds(T.GAP);
    var impactAt = seconds(T.IMPACT);
    var impactGap = seconds(T.IMPACT_GAP) || gap;
    var numAt = seconds(T.NUM);

    var events = [];
    function at(time, fire) {
      if (time > 0) events.push({ at: time, fire: fire });
    }
    function whenOf(time) {
      return when0 + (exact ? Math.max(0, time - fromT) : 0);
    }

    at(spot, function (when) { seep(ctx, out, B, when, level); });

    for (var i = 0; i < spots; i++) {
      (function (index) {
        var detachT = brk + index * gap;
        var landing = impactAt + index * impactGap;
        var lv = level * (1 - index * 0.16);
        at(detachT, function (when) {
          detach(ctx, out, B, when, lv);
          fall(ctx, out, B, when + 0.02, lv);
        });
        at(landing, function (when) { impact(ctx, out, B, when, lv); });
      })(i);
    }

    /* 三点档：整张牌湿下去的那口低频墨气（跟着第一颗落地） */
    if (spots >= 3) {
      at(impactAt + 0.06, function (when) { ink(ctx, out, B, when); });
    }

    at(numAt, function (when) { num(ctx, out, B, when); });

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
          fireEvents(g.ctx, session, g.noise, T, lastT, t, g.ctx.currentTime, false);
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

  /* `?audiotest=1` / 导出用：按给定时间轴离线渲染一档，回总电平 + 四个窗口的 RMS。
   * 没有 `OfflineAudioContext` ⇒ `null`（与 `SgsSfx` / `SgsFireSfx` 同一条降级路径）。 */
  function renderOffline(variant, timeline, seconds) {
    var OAC = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    if (!OAC) return Promise.resolve(null);
    var dur = Number(seconds) > 0 ? Number(seconds) : 3;
    var ctx = new OAC(2, Math.ceil(SR * dur), SR);
    var comp = compressor(ctx);
    var master = ctx.createGain();
    master.gain.value = 1.0;
    master.connect(comp);
    var B = { noise: makeNoise(ctx, 1.2, 0x5e90) };
    // 离线：时间轴从 0 起算，事件落到"时间轴秒数"上（`fromT = 0`，`exact` 用 `at - 0`）。
    fireEvents(ctx, master, B, timeline, 0, Infinity, 0, true);
    return ctx.startRendering().then(function (buffer) {
      function win(a, b) {
        var i0 = Math.floor(a * SR), i1 = Math.min(buffer.length, Math.floor(b * SR));
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
      var impactAt = Number(T.IMPACT) || 0;
      return {
        total: stats(buffer),
        seep: win(Number(T.SPOT) || 0, (Number(T.SPOT) || 0) + 0.3),
        detach: win(Number(T.BREAK) || 0, (Number(T.BREAK) || 0) + 0.12),
        impact: win(impactAt, impactAt + 0.3),
        num: win(Number(T.NUM) || 0, (Number(T.NUM) || 0) + 0.2),
        duration: +dur.toFixed(2)
      };
    });
  }

  global.SgsHpLossSfx = {
    create: create,
    renderOffline: renderOffline,
    /* 六个音色单独导出：调音时不必碰排程（与 `SgsSfx` / `SgsFireSfx` 的姿态一致）。 */
    seep: seep, detach: detach, fall: fall, impact: impact, num: num, ink: ink
  };
})(window);
