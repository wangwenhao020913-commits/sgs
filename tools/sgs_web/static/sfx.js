"use strict";

/* ============================================================================
 * SGS 剑光 · 音效（Web Audio 现场合成，零外部音频文件）
 * ----------------------------------------------------------------------------
 * 六个声音，全部由振荡器 / 噪声 + 滤波 + 包络现场合成：
 *   riser   剑意凝聚（渐强的高频啸声）
 *   shing   出鞘（金属刀鸣）
 *   whoosh  剑光飞行（带多普勒感的呼啸，从出剑方扫到中剑方）
 *   impact  命中（低频闷响 + 碎裂 + 金属余鸣）
 *   strobe  黑白屏闪（15Hz 电流噪声）
 *   split   劈屏（更长的低频轰鸣 + 撕裂）
 *
 * 浏览器要求先有用户手势才能出声，所以默认静音，点一下才开（damage-fx 票 06 接到牌桌；
 * 素材库那套壳子见参考实现的 `shell.js`）。噪声用固定种子的线性同余生成，同一段音频每次
 * 渲染都一样。
 *
 * 落进牌桌时是**普通脚本**（U1-00 的清单契约）：`"use strict";` 是文件的**第一条语句**
 * （`tests/test_web_static.py:test_every_script_is_a_classic_strict_script`），故它写在
 * 参考实现那段块注释**之前**；全局名仍是 `window.SgsSfx`，不改成 ESM。
 * ========================================================================== */
(function (global) {

  /* ================================================================
   * 调音台：想改音色只动这张表，不用碰下面的合成代码
   * ----------------------------------------------------------------
   * 每个声音一行，键名就是它自己的参数。改完保存、刷新页面即生效。
   * 常见改法：
   *   出鞘太尖      → shing.partials 整体调低，或 shing.hp 调低
   *   命中不够沉    → impact.subFrom 调高、impact.subTo 调低
   *   飞行太吵      → whoosh.gain 调小
   *   屏闪电流太刺耳 → strobe.freq 调低
   *   整体太响      → 改 masterLevel（在 create() 里，0.85）
   * ============================================================== */
  var VOICE = {
    riser:  { from: 420,  to: 2600, q: 7,   gain: 0.19,
              subFrom: 190, subTo: 780, subGain: 0.075 },
    shing:  { hp: 2400, gain: 0.26, decay: 0.22,
              partials: [2480, 3720, 4960], partialGain: 0.09, ring: 0.35 },
    whoosh: { from: 700, to: 3400, back: 650, q: 1.6, gain: 0.20,
              panFrom: -0.75, panTo: 0.85 },
    impact: { subFrom: 140, subTo: 36, subGain: 0.55,
              lpFrom: 7200, lpTo: 600, crashGain: 0.34,
              ring: [1620, 2410, 3230], ringGain: 0.07 },
    strobe: { freq: 1850, q: 2.2, gain: 0.14, lfo: 15 },
    split:  { subFrom: 90, subTo: 26, subGain: 0.5,
              hpFrom: 5200, hpTo: 320, ripGain: 0.22 }
  };

  /* ------------------------------------------------------------ 噪声源 ---- */
  function noiseBuffer(ctx, seconds, seed) {
    var n = Math.max(1, Math.ceil(ctx.sampleRate * seconds));
    var buf = ctx.createBuffer(1, n, ctx.sampleRate);
    var d = buf.getChannelData(0);
    var s = (seed || 12345) >>> 0;
    for (var i = 0; i < n; i++) {
      s = (s * 16807) % 2147483647;
      d[i] = (s / 2147483647) * 2 - 1;
    }
    return buf;
  }

  function noise(ctx, seconds, seed) {
    var src = ctx.createBufferSource();
    src.buffer = noiseBuffer(ctx, seconds, seed);
    return src;
  }

  /* 一个通用的"连接 → 包络 → 输出"管脚 */
  function chain(ctx, out, node) {
    node.connect(out);
    return node;
  }

  /* ------------------------------------------------------------ 六个声音 ---- */

  /* 剑意凝聚：高频噪声从暗到亮，一路推上去 */
  function riser(ctx, out, t0, dur, level) {
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(VOICE.riser.gain * level, t0 + dur * 0.85);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    g.connect(out);

    var bp = ctx.createBiquadFilter();
    bp.type = 'bandpass';
    bp.Q.value = VOICE.riser.q;
    bp.frequency.setValueAtTime(VOICE.riser.from, t0);
    bp.frequency.exponentialRampToValueAtTime(VOICE.riser.to, t0 + dur);
    bp.connect(g);

    var n = noise(ctx, dur + 0.05, 7717);
    n.connect(bp);
    n.start(t0);
    n.stop(t0 + dur + 0.02);

    // 底下垫一条跟着升的三角波，让"蓄力"更有实体
    var o = ctx.createOscillator();
    o.type = 'triangle';
    o.frequency.setValueAtTime(VOICE.riser.subFrom, t0);
    o.frequency.exponentialRampToValueAtTime(VOICE.riser.subTo, t0 + dur);
    var og = ctx.createGain();
    og.gain.setValueAtTime(0.0001, t0);
    og.gain.exponentialRampToValueAtTime(VOICE.riser.subGain * level, t0 + dur * 0.9);
    og.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(og);
    og.connect(out);
    o.start(t0);
    o.stop(t0 + dur + 0.02);
  }

  /* 出鞘：白噪声打头 + 三个金属泛音，极短 */
  function shing(ctx, out, t0, level) {
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(VOICE.shing.gain * level, t0 + 0.008);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + VOICE.shing.decay);
    g.connect(out);

    var hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.value = VOICE.shing.hp;
    hp.connect(g);

    var n = noise(ctx, 0.25, 3313);
    n.connect(hp);
    n.start(t0);
    n.stop(t0 + 0.24);

    var partials = VOICE.shing.partials;
    for (var i = 0; i < partials.length; i++) {
      var o = ctx.createOscillator();
      o.type = 'triangle';
      o.frequency.value = partials[i] * (1 + i * 0.002);
      var og = ctx.createGain();
      og.gain.setValueAtTime(0.0001, t0 + i * 0.006);
      og.gain.exponentialRampToValueAtTime(VOICE.shing.partialGain * level / (i + 1), t0 + i * 0.006 + 0.006);
      og.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.35 + i * 0.05);
      o.connect(og);
      og.connect(out);
      o.start(t0 + i * 0.006);
      o.stop(t0 + 0.45 + i * 0.05);
    }
  }

  /* 飞行：带通扫频 + 声像从左飞到右 */
  function whoosh(ctx, out, t0, dur, level) {
    var pan = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
    var tail = ctx.createGain();
    tail.gain.value = 1;
    if (pan) {
      pan.pan.setValueAtTime(VOICE.whoosh.panFrom, t0);
      pan.pan.linearRampToValueAtTime(VOICE.whoosh.panTo, t0 + dur);
      tail.connect(pan);
      pan.connect(out);
    } else {
      tail.connect(out);
    }

    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(VOICE.whoosh.gain * level, t0 + dur * 0.45);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur * 1.15);
    g.connect(tail);

    var bp = ctx.createBiquadFilter();
    bp.type = 'bandpass';
    bp.Q.value = VOICE.whoosh.q;
    bp.frequency.setValueAtTime(VOICE.whoosh.from, t0);
    bp.frequency.exponentialRampToValueAtTime(VOICE.whoosh.to, t0 + dur * 0.55);
    bp.frequency.exponentialRampToValueAtTime(VOICE.whoosh.back, t0 + dur * 1.15);
    bp.connect(g);

    var n = noise(ctx, dur * 1.2 + 0.05, 9059);
    n.connect(bp);
    n.start(t0);
    n.stop(t0 + dur * 1.2 + 0.02);
  }

  /* 命中：低频闷响 + 碎裂 + 金属余鸣，重伤更沉更响 */
  function impact(ctx, out, t0, level, weight) {
    // 低频
    var sub = ctx.createOscillator();
    sub.type = 'sine';
    sub.frequency.setValueAtTime(VOICE.impact.subFrom, t0);
    sub.frequency.exponentialRampToValueAtTime(VOICE.impact.subTo, t0 + 0.42);
    var sg = ctx.createGain();
    sg.gain.setValueAtTime(0.0001, t0);
    sg.gain.exponentialRampToValueAtTime(VOICE.impact.subGain * level, t0 + 0.012);
    sg.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.55 + weight * 0.3);
    sub.connect(sg);
    sg.connect(out);
    sub.start(t0);
    sub.stop(t0 + 0.95 + weight * 0.3);

    // 碎裂
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(VOICE.impact.crashGain * level, t0 + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.26 + weight * 0.12);
    g.connect(out);

    var lp = ctx.createBiquadFilter();
    lp.type = 'lowpass';
    lp.frequency.setValueAtTime(VOICE.impact.lpFrom, t0);
    lp.frequency.exponentialRampToValueAtTime(VOICE.impact.lpTo, t0 + 0.3);
    lp.connect(g);

    var n = noise(ctx, 0.5 + weight * 0.2, 4409);
    n.connect(lp);
    n.start(t0);
    n.stop(t0 + 0.48 + weight * 0.2);

    // 金属余鸣
    var ring = VOICE.impact.ring;
    for (var i = 0; i < ring.length; i++) {
      var o = ctx.createOscillator();
      o.type = 'triangle';
      o.frequency.value = ring[i] * (1 + i * 0.003);
      var og = ctx.createGain();
      og.gain.setValueAtTime(0.0001, t0);
      og.gain.exponentialRampToValueAtTime((VOICE.impact.ringGain * level) / (i + 1), t0 + 0.01);
      og.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.5 + weight * 0.25 + i * 0.07);
      o.connect(og);
      og.connect(out);
      o.start(t0);
      o.stop(t0 + 0.9 + weight * 0.3 + i * 0.07);
    }
  }

  /* 黑白屏闪：15Hz 门控噪声，像电流抽打 */
  function strobe(ctx, out, t0, dur, level) {
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(VOICE.strobe.gain * level, t0 + 0.01);
    g.gain.setValueAtTime(VOICE.strobe.gain * level, t0 + dur - 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    g.connect(out);

    // 15Hz 方波门控（跟画面的闪频对齐）
    var lfo = ctx.createOscillator();
    lfo.type = 'square';
    lfo.frequency.value = VOICE.strobe.lfo;
    var lg = ctx.createGain();
    lg.gain.value = 0.5;
    var gate = ctx.createGain();
    gate.gain.value = 0.5;
    lfo.connect(lg);
    lg.connect(gate.gain);
    gate.connect(g);

    var bp = ctx.createBiquadFilter();
    bp.type = 'bandpass';
    bp.Q.value = VOICE.strobe.q;
    bp.frequency.value = VOICE.strobe.freq;
    bp.connect(gate);

    var n = noise(ctx, dur + 0.05, 6007);
    n.connect(bp);
    n.start(t0);
    n.stop(t0 + dur + 0.02);
    lfo.start(t0);
    lfo.stop(t0 + dur + 0.02);
  }

  /* 劈屏：比命中更长的低频轰鸣 + 一道撕裂 */
  function split(ctx, out, t0, level) {
    var sub = ctx.createOscillator();
    sub.type = 'sine';
    sub.frequency.setValueAtTime(VOICE.split.subFrom, t0);
    sub.frequency.exponentialRampToValueAtTime(VOICE.split.subTo, t0 + 0.9);
    var sg = ctx.createGain();
    sg.gain.setValueAtTime(0.0001, t0);
    sg.gain.exponentialRampToValueAtTime(VOICE.split.subGain * level, t0 + 0.02);
    sg.gain.exponentialRampToValueAtTime(0.0001, t0 + 1.15);
    sub.connect(sg);
    sg.connect(out);
    sub.start(t0);
    sub.stop(t0 + 1.2);

    // 撕裂：高通噪声快速下滑
    var g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(VOICE.split.ripGain * level, t0 + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.7);
    g.connect(out);

    var hp = ctx.createBiquadFilter();
    hp.type = 'highpass';
    hp.frequency.setValueAtTime(VOICE.split.hpFrom, t0);
    hp.frequency.exponentialRampToValueAtTime(VOICE.split.hpTo, t0 + 0.65);
    hp.connect(g);

    var n = noise(ctx, 0.8, 8123);
    n.connect(hp);
    n.start(t0);
    n.stop(t0 + 0.75);
  }

  /* ================================================================
   * 一整套：按档位与时间轴把声音排进去
   * ============================================================== */
  function scheduleAll(ctx, out, t0, v) {
    var d = v.dmg;
    var level = 0.55 + d * 0.13;          // 伤害越大越响
    var weight = (d - 1) / 3;             // 0..1

    riser(ctx, out, t0 + v.CHARGE, v.LAUNCH - v.CHARGE, 0.7 + d * 0.1);
    shing(ctx, out, t0 + v.LAUNCH, 0.75 + d * 0.12);
    whoosh(ctx, out, t0 + v.LAUNCH, v.flight, 0.7 + d * 0.13);
    impact(ctx, out, t0 + v.IMPACT, level, weight);
    if (v.strobe > 0) strobe(ctx, out, t0 + v.IMPACT, v.strobe, 0.8 + d * 0.1);
    if (v.split > 0) split(ctx, out, t0 + v.IMPACT + 0.01, level);
  }

  /* ================================================================
   * 现场播放器
   * ============================================================== */
  function create(timeline) {
    var T = timeline;
    var actx = null, master = null, on = false, lastT = -1;

    function ensure() {
      if (actx) return actx;
      var AC = global.AudioContext || global.webkitAudioContext;
      if (!AC) return null;
      actx = new AC();

      master = actx.createGain();
      master.gain.value = 0.85;

      var comp = actx.createDynamicsCompressor();
      comp.threshold.value = -16;
      comp.knee.value = 22;
      comp.ratio.value = 9;
      comp.attack.value = 0.003;
      comp.release.value = 0.22;

      master.connect(comp);
      comp.connect(actx.destination);
      return actx;
    }

    /* 每个声音只在时间轴跨过它的时刻触发一次 */
    var cues = [
      { at: function () { return T.CHARGE; }, fn: function (v) { riser(actx, master, actx.currentTime, T.LAUNCH - T.CHARGE, 0.7 + v.dmg * 0.1); } },
      { at: function () { return T.LAUNCH; }, fn: function (v) { shing(actx, master, actx.currentTime, 0.75 + v.dmg * 0.12); } },
      { at: function () { return T.LAUNCH; }, fn: function (v) { whoosh(actx, master, actx.currentTime, v.flight, 0.7 + v.dmg * 0.13); } },
      { at: function () { return T.IMPACT; }, fn: function (v) { impact(actx, master, actx.currentTime, 0.55 + v.dmg * 0.13, (v.dmg - 1) / 3); } },
      { at: function (v) { return v.strobe > 0 ? T.IMPACT : -1; }, fn: function (v) { strobe(actx, master, actx.currentTime, v.strobe, 0.8 + v.dmg * 0.1); } },
      { at: function (v) { return v.split > 0 ? T.IMPACT + 0.01 : -1; }, fn: function (v) { split(actx, master, actx.currentTime, 0.55 + v.dmg * 0.13); } }
    ];

    return {
      get enabled() { return on; },
      start: function () {
        if (!ensure()) return false;
        if (actx.state === 'suspended') actx.resume();
        on = true;
        lastT = -1;
        return true;
      },
      stop: function () {
        on = false;
        if (master) master.gain.value = 0;
      },
      mute: function (m) {
        if (!master) return;
        master.gain.value = m ? 0 : 0.85;
      },
      /* 每帧调用：t 是动画秒数，v 是当前档位对象 */
      tick: function (t, v) {
        if (!on || !actx) { lastT = t; return; }
        if (lastT < 0) { lastT = t; return; }
        var wrapped = t < lastT - 1;      // 12 秒一圈，掉这么多就是绕回来了
        for (var i = 0; i < cues.length; i++) {
          var at = cues[i].at(v);
          if (at < 0) continue;
          var hit = wrapped ? (at > lastT || at <= t) : (at > lastT && at <= t);
          if (hit) cues[i].fn(v);
        }
        lastT = t;
      }
    };
  }

  /* ================================================================
   * 离线渲染：给 ?audiotest=1 用，能用数值验证"真的出声了"
   * ============================================================== */
  function renderOffline(v, timeline, seconds) {
    var OAC = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    if (!OAC) return Promise.resolve(null);
    var dur = seconds || 2.6;
    var ctx = new OAC(2, Math.ceil(44100 * dur), 44100);
    var comp = ctx.createDynamicsCompressor();
    comp.connect(ctx.destination);
    scheduleAll(ctx, comp, 0, {
      dmg: v.dmg, CHARGE: timeline.CHARGE, LAUNCH: timeline.LAUNCH,
      IMPACT: timeline.IMPACT, flight: v.flight, strobe: v.strobe, split: v.split
    });
    return ctx.startRendering().then(function (buf) {
      var ch = buf.getChannelData(0);
      // 分窗口测 RMS，能看出每个声音落在哪
      function win(a, b) {
        var i0 = Math.floor(a * 44100), i1 = Math.min(ch.length, Math.floor(b * 44100));
        var s = 0, peak = 0;
        for (var i = i0; i < i1; i++) {
          var x = ch[i];
          s += x * x;
          if (Math.abs(x) > peak) peak = Math.abs(x);
        }
        var n = Math.max(1, i1 - i0);
        return { rms: Math.sqrt(s / n), peak: peak };
      }
      return {
        total: win(0, dur),
        charge: win(timeline.CHARGE, timeline.LAUNCH),
        launch: win(timeline.LAUNCH, timeline.LAUNCH + 0.1),
        flight: win(timeline.LAUNCH + 0.02, timeline.IMPACT - 0.01),
        hit: win(timeline.IMPACT, timeline.IMPACT + 0.12)
      };
    });
  }

  global.SgsSfx = {
    create: create,
    scheduleAll: scheduleAll,
    renderOffline: renderOffline,
    riser: riser, shing: shing, whoosh: whoosh,
    impact: impact, strobe: strobe, split: split
  };
})(window);
