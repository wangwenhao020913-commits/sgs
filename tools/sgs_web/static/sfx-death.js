/* SGS 前端 · 「阵亡」的**音效**（chain-death-voices 票 02；素材包
 * `outputs/sgs-death-fx/sfx-death.js` 的移植）：起光低鸣 → 三波裂缝的"嚓" → 停顿里那条细
 * 高音 → 崩（玻璃爆开 + 一记闷响）→ 落屑 → 落印，一次性排完。
 *
 * 姿态与 `sfx-chain.js` 逐字同形：Web Audio **现场合成**（仓库里没有音频文件）、
 * `create()` 返回一个播放器、模块本身**不持有时钟**（cue 时刻从 `SgsDeathFX.PARAMS` 读，
 * 画面与声音同一张表）、`renderOffline` 只给离线电平读数用。它是**一次性排程器**
 * （`play(fromT, speed)` 排完整段、`stop()` 收掉），不进 `sfx-ui.js` 的伤害声道池。
 *
 * 牌桌档的时长是 `anim.ini` 的 `death_fx_total`（缺省 2200ms），所以排播层调
 * `play(0, speed)`——`speed = 3.05 / 牌桌秒`，与画面 `draw(t)` 用的是同一个倍率。
 *
 * 已知边界（素材包 §7，照抄）：模块里只有**一个 bus**，多条阵亡并发时后开的 `play` 会顶掉
 * 前一条。并发上限是 2、更常见的是 1，没做多声道。
 *
 * 常音量的唯一调音处是下面那张 `VOICE` 表（与素材包一致）。
 */
"use strict";

(function (global) {
  var NOISE = 1.2;

  /* 调音台：只在这里改音量 */
  var VOICE = {
    bed: 0.085,      /* 起光时那条低鸣 */
    glass: 0.028,    /* 绷紧的高频 */
    crack: 0.085,    /* 裂缝 */
    hold: 0.020,     /* 停顿里那条细高音 */
    burst: 0.40,     /* 崩 */
    shard: 0.075,    /* 碎片的"叮" */
    debris: 0.045,   /* 落屑 */
    drop: 0.30       /* 落印 */
  };

  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }

  /* ------------------------------------------------------------ 时间表 ---- */
  function cues() {
    var FX = global.SgsDeathFX;
    var T = FX && FX.PARAMS ? FX.PARAMS : null;
    if (!T) {
      T = { wakeAt: 0.02, waves: [0.16, 0.52, 0.98], holdAt: 1.4, burstAt: 1.74, labelAt: 2.26, duration: 3.05 };
    }
    return {
      wake: T.wakeAt,
      waves: T.waves.slice(),
      hold: T.holdAt,
      burst: T.burstAt,
      label: T.labelAt,
      end: T.duration
    };
  }

  /* ------------------------------------------------------------ 噪声源 ---- */
  function noiseBuffer(ac) {
    if (ac.__deathNoise) return ac.__deathNoise;
    var len = Math.floor(ac.sampleRate * NOISE);
    var buf = ac.createBuffer(1, len, ac.sampleRate);
    var d = buf.getChannelData(0);
    var seed = 20261201;                    /* 固定种子：离线渲染两次一样 */
    for (var i = 0; i < len; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      d[i] = (seed / 0x7fffffff) * 2 - 1;
    }
    ac.__deathNoise = buf;
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

  function gain(ac, out, list) {
    var g = ac.createGain();
    g.connect(out);
    return g;
  }

  function filter(ac, type, freq, q) {
    var f = ac.createBiquadFilter();
    f.type = type;
    f.frequency.value = freq;
    f.Q.value = q == null ? 0.9 : q;
    return f;
  }

  function osc(ac, type, list) {
    var o = ac.createOscillator();
    o.type = type;
    return o;
  }

  /* ------------------------------------------------------------ 声音 ---- */

  /* 起光：一条很低的长鸣 + 玻璃绷紧的细高音，一起涨到停顿前 */
  function bed(ac, out, list, t0, t1) {
    var dur = Math.max(0.2, t1 - t0);

    var o = osc(ac, "sine", list);
    o.frequency.setValueAtTime(44, t0);
    o.frequency.linearRampToValueAtTime(58, t0 + dur);
    var g = gain(ac, out, list);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(VOICE.bed, t0 + dur * 0.55);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g);
    o.start(t0); o.stop(t0 + dur + 0.05);
    list.push(o);

    /* 玻璃绷紧：两条微微失谐的细高音，慢慢升 */
    for (var i = 0; i < 2; i++) {
      var h = osc(ac, "sine", list);
      h.frequency.setValueAtTime(210 + i * 3.5, t0);
      h.frequency.exponentialRampToValueAtTime(760 + i * 5, t0 + dur);
      var hg = gain(ac, out, list);
      hg.gain.setValueAtTime(0.0001, t0);
      hg.gain.exponentialRampToValueAtTime(VOICE.glass, t0 + dur * 0.85);
      hg.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
      h.connect(hg);
      h.start(t0); h.stop(t0 + dur + 0.05);
      list.push(h);
    }

    /* 纸面被光"顶"起来的一点气声 */
    var src = noise(ac, list, t0, dur);
    var bp = filter(ac, "bandpass", 1800, 1.1);
    bp.frequency.setValueAtTime(1400, t0);
    bp.frequency.exponentialRampToValueAtTime(5200, t0 + dur);
    var ng = gain(ac, out, list);
    ng.gain.setValueAtTime(0.0001, t0);
    ng.gain.exponentialRampToValueAtTime(VOICE.glass * 0.9, t0 + dur * 0.7);
    ng.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    src.connect(bp); bp.connect(ng);
  }

  /* 裂缝：一声很短的"嚓"，偏冷 */
  function crack(ac, out, list, t, level, pitch) {
    var src = noise(ac, list, t, 0.055);
    var bp = filter(ac, "bandpass", pitch || 5200, 2.2);
    var g = gain(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.crack * level, t + 0.004);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
    src.connect(bp); bp.connect(g);

    var o = osc(ac, "triangle", list);
    o.frequency.setValueAtTime((pitch || 5200) * 0.55, t);
    o.frequency.exponentialRampToValueAtTime((pitch || 5200) * 0.34, t + 0.05);
    var og = gain(ac, out, list);
    og.gain.setValueAtTime(0.0001, t);
    og.gain.exponentialRampToValueAtTime(VOICE.crack * level * 0.45, t + 0.005);
    og.gain.exponentialRampToValueAtTime(0.0001, t + 0.06);
    o.connect(og);
    o.start(t); o.stop(t + 0.07);
    list.push(o);
  }

  /* 停顿：只剩一条细高音（像耳鸣），然后在崩之前掉下去 */
  function hold(ac, out, list, t0, t1) {
    var dur = Math.max(0.12, t1 - t0);
    var o = osc(ac, "sine", list);
    o.frequency.setValueAtTime(1240, t0);
    o.frequency.linearRampToValueAtTime(1180, t0 + dur);
    var g = gain(ac, out, list);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(VOICE.hold, t0 + 0.06);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g);
    o.start(t0); o.stop(t0 + dur + 0.02);
    list.push(o);
  }

  /* 崩：玻璃爆开 + 桌子闷响 + 几记碎片"叮" */
  function burst(ac, out, list, t) {
    /* 玻璃：高频噪声，掉得快 */
    var src = noise(ac, list, t, 0.55);
    var hp = filter(ac, "highpass", 1600, 0.7);
    hp.frequency.setValueAtTime(1600, t);
    hp.frequency.exponentialRampToValueAtTime(4200, t + 0.10);
    var g = gain(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.burst, t + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.42);
    src.connect(hp); hp.connect(g);

    /* 中频那一下"炸" */
    var src2 = noise(ac, list, t, 0.22);
    var bp = filter(ac, "bandpass", 2600, 0.9);
    bp.frequency.setValueAtTime(3200, t);
    bp.frequency.exponentialRampToValueAtTime(700, t + 0.20);
    var g2 = gain(ac, out, list);
    g2.gain.setValueAtTime(0.0001, t);
    g2.gain.exponentialRampToValueAtTime(VOICE.burst * 0.55, t + 0.005);
    g2.gain.exponentialRampToValueAtTime(0.0001, t + 0.20);
    src2.connect(bp); bp.connect(g2);

    /* 桌子：一记闷响 */
    var lo = osc(ac, "sine", list);
    lo.frequency.setValueAtTime(96, t);
    lo.frequency.exponentialRampToValueAtTime(42, t + 0.30);
    var lg = gain(ac, out, list);
    lg.gain.setValueAtTime(0.0001, t);
    lg.gain.exponentialRampToValueAtTime(VOICE.burst * 0.75, t + 0.008);
    lg.gain.exponentialRampToValueAtTime(0.0001, t + 0.40);
    lo.connect(lg);
    lo.start(t); lo.stop(t + 0.42);
    list.push(lo);

    /* 几记碎片的"叮"：高、短、错开 */
    var freqs = [3120, 4180, 5360, 2740];
    for (var i = 0; i < freqs.length; i++) {
      var o = osc(ac, "sine", list);
      var tt = t + i * 0.012;
      o.frequency.setValueAtTime(freqs[i], tt);
      o.frequency.exponentialRampToValueAtTime(freqs[i] * 0.93, tt + 0.16);
      var og = gain(ac, out, list);
      og.gain.setValueAtTime(0.0001, tt);
      og.gain.exponentialRampToValueAtTime(VOICE.shard * (1 - i * 0.16), tt + 0.004);
      og.gain.exponentialRampToValueAtTime(0.0001, tt + 0.16 + i * 0.05);
      o.connect(og);
      o.start(tt); o.stop(tt + 0.3);
      list.push(o);
    }
  }

  /* 落屑：零星的高音碎响，越往后越稀 */
  function debris(ac, out, list, t0, t1) {
    var seed = 7717;
    var t = t0;
    var n = 0;
    while (t < t1 && n < 11) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      var r = seed / 0x7fffffff;
      var o = osc(ac, "sine", list);
      var f = 3200 + r * 3800;
      o.frequency.setValueAtTime(f, t);
      o.frequency.exponentialRampToValueAtTime(f * 0.9, t + 0.10);
      var g = gain(ac, out, list);
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(VOICE.debris * (1 - n * 0.06), t + 0.004);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.09 + r * 0.10);
      o.connect(g);
      o.start(t); o.stop(t + 0.24);
      list.push(o);
      t += 0.055 + r * 0.16;
      n++;
    }
  }

  /* 落印：木印压在纸上 */
  function drop(ac, out, list, t) {
    var o = osc(ac, "sine", list);
    o.frequency.setValueAtTime(132, t);
    o.frequency.exponentialRampToValueAtTime(56, t + 0.13);
    var g = gain(ac, out, list);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(VOICE.drop, t + 0.006);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.22);
    o.connect(g);
    o.start(t); o.stop(t + 0.25);
    list.push(o);

    var src = noise(ac, list, t, 0.10);
    var bp = filter(ac, "bandpass", 780, 1.4);
    var ng = gain(ac, out, list);
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.exponentialRampToValueAtTime(VOICE.drop * 0.5, t + 0.004);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.09);
    src.connect(bp); bp.connect(ng);

    /* 纸被压下去的一点沙响 */
    var src2 = noise(ac, list, t + 0.01, 0.22);
    var hp = filter(ac, "highpass", 3400, 0.7);
    var hg = gain(ac, out, list);
    hg.gain.setValueAtTime(0.0001, t + 0.01);
    hg.gain.exponentialRampToValueAtTime(VOICE.drop * 0.16, t + 0.04);
    hg.gain.exponentialRampToValueAtTime(0.0001, t + 0.20);
    src2.connect(hp); hp.connect(hg);
  }

  /* ------------------------------------------------------------ 排程 ---- */
  function schedule(ac, out, fromT, speed) {
    var list = [];
    var c = cues();
    var sp = speed > 0 ? speed : 1;
    function at(t) { return ac.currentTime + Math.max(0, (t - fromT) / sp); }
    function live(t) { return (t - fromT) / sp >= -0.02; }

    if (live(c.wake)) bed(ac, out, list, at(c.wake), at(Math.max(c.wake + 0.2, c.hold)));

    /* 每一波裂缝：两三声"嚓"，错开一点点 */
    for (var w = 0; w < c.waves.length; w++) {
      var lv = 0.55 + w * 0.16;
      for (var k = 0; k < 3; k++) {
        var ct = c.waves[w] + k * 0.045 + w * 0.012;
        if (!live(ct)) continue;
        crack(ac, out, list, at(ct), lv * (1 - k * 0.22), 4600 + w * 520 + k * 380);
      }
    }
    /* 停顿：一条细高音，然后静下去 */
    if (live(c.hold)) hold(ac, out, list, at(c.hold), at(c.burst - 0.02));

    if (live(c.burst)) {
      burst(ac, out, list, at(c.burst));
      debris(ac, out, list, at(c.burst + 0.10), at(c.burst + 0.85));
    }
    if (live(c.label)) drop(ac, out, list, at(c.label));

    return list;
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
          comp.ratio.value = 3.2;
          comp.attack.value = 0.003;
          comp.release.value = 0.25;
          master.connect(comp);
          comp.connect(ac.destination);
        }
        ac.resume();
        return Promise.resolve(true);
      },
      play: function (fromT, speed) {
        if (!ac || muted) return;
        stopAll();
        bus = ac.createGain();
        bus.gain.value = 1;
        bus.connect(master);
        sources = schedule(ac, bus, Math.max(0, fromT || 0), speed || 1);
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
  function renderOffline(seconds) {
    var Offline = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    if (!Offline) return Promise.resolve({ available: false });
    var dur = Math.max(0.2, Number(seconds) || 3);
    var ac = new Offline(2, Math.ceil(44100 * dur), 44100);
    var out = ac.createGain();
    out.gain.value = 0.9;
    out.connect(ac.destination);
    schedule(ac, out, 0, 1);
    return ac.startRendering().then(function (buf) {
      var ch = buf.getChannelData(0);
      /* 再取右声道能量：单声道时两轨相同，不为 0 就说明立体声路径通 */
      var ch2 = buf.numberOfChannels > 1 ? buf.getChannelData(1) : ch;
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
      var peak2 = 0;
      for (var k = 0; k < ch2.length; k += 7) peak2 = Math.max(peak2, Math.abs(ch2[k]));
      return {
        available: true,
        peak: +peak.toFixed(3),
        peakR: +peak2.toFixed(3),
        rms: +Math.sqrt(sum / ch.length).toFixed(4),
        windows: windows,
        duration: +dur.toFixed(2)
      };
    });
  }

  global.SgsDeathSfx = {
    create: create,
    renderOffline: renderOffline,
    cues: cues,
    VOICE: VOICE
  };
})(window);
