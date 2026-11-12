"use strict";
/* ============================================================================
 * 三国杀 · 技能发动水墨艺术字 · 音效（Web Audio 现场合成，没有音频文件）
 *
 * 这套声音只有四件事，和画面同一张时间表：
 *   落墨   一声很低的"噗"——墨吸进纸里；随后逐字极轻的纸面擦响
 *   圆相   一笔快扫：带通噪声 900→3200→1400Hz，左→右过声场；
 *          后段加几粒干笔"嚓"，收笔处自然断掉
 *   落印   木印压在纸上的"笃"（短低频 + 纸声 + 一点硬边）
 *   化墨   一条很轻的气声，慢慢淡出——墨被纸吸走，不是"啪"地消失
 *
 * 时间点从 SgsSkillInkFX.PARAMS 读（画面 / 声音同一份，不另抄）。
 *
 *   var sfx = SgsSkillInkSfx.create();
 *   sfx.unlock().then(ok => ...)     // 必须在用户手势的同一个 tick 里调
 *   sfx.play(0, 1);                  // fromT, speed
 *   sfx.stop();
 *   sfx.mute(true);
 *   SgsSkillInkSfx.renderOffline(2.05).then(r => ...)          // 测试 / 导出
 *   SgsSkillInkSfx.renderOffline(7.2, [0, 2.4, 4.8])           // 连播三段的音轨
 * ========================================================================== */
(function (global) {
  "use strict";

  var SR = 44100;
  var NOISE_SECONDS = 2.4;

  /* 调音台：只在这里改音量 */
  var VOICE = {
    ink: 0.34,          /* 落墨的低频 */
    paper: 0.11,        /* 纸吸墨 / 落字 */
    brush: 0.28,        /* 圆相的主扫笔 */
    bristle: 0.06,      /* 刷毛的高频 */
    crackle: 0.05,      /* 干笔的"嚓" */
    seal: 0.30,         /* 落印 */
    fade: 0.06,         /* 化墨气声 */
    lowTail: 0.038,     /* 化墨时的一条低频尾巴 */
    settle: 0.012       /* 停住时的余韵 */
  };

  var DEFAULT = {
    nameAt: 0.02, nameStagger: 0.055,
    ringAt: 0.13, ringDur: 0.30,
    sealAt: 0.50,
    fadeAt: 1.46, fadeDur: 0.59,
    duration: 2.05
  };

  function clamp(x, a, b) { return x < a ? a : x > b ? b : x; }
  function timing() {
    var t = global.SgsSkillInkFX && global.SgsSkillInkFX.PARAMS;
    if (!t) return DEFAULT;
    return {
      nameAt: t.nameAt, nameStagger: t.nameStagger,
      ringAt: t.ringAt, ringDur: t.ringDur,
      sealAt: t.sealAt, fadeAt: t.fadeAt, fadeDur: t.fadeDur,
      duration: t.duration
    };
  }

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

  /* ------------------------------------------------------------ 噪声源 ---- */
  function noiseBuffer(ctx) {
    if (ctx.__skillInkNoise) return ctx.__skillInkNoise;
    var len = Math.floor(ctx.sampleRate * NOISE_SECONDS);
    var buf = ctx.createBuffer(1, len, ctx.sampleRate);
    var d = buf.getChannelData(0);
    var seed = 20261112;
    for (var i = 0; i < len; i++) {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      d[i] = (seed / 0x7fffffff) * 2 - 1;
    }
    ctx.__skillInkNoise = buf;
    return buf;
  }

  function noise(ctx, dest, t, dur) {
    var src = ctx.createBufferSource();
    src.buffer = noiseBuffer(ctx);
    src.loop = true;
    src.connect(dest);
    src.start(t);
    src.stop(t + dur + 0.02);
    return src;
  }

  function gainNode(ctx, value) {
    var g = ctx.createGain();
    g.gain.value = value;
    return g;
  }

  function panner(ctx, pan) {
    if (!ctx.createStereoPanner) return null;
    var p = ctx.createStereoPanner();
    p.pan.value = pan;
    return p;
  }

  function chain(nodes) {
    for (var i = 0; i < nodes.length - 1; i++) nodes[i].connect(nodes[i + 1]);
    return nodes[nodes.length - 1];
  }

  /* 混响：一段很短的纸房间，只给笔和印一点空间 */
  function impulse(ctx) {
    if (ctx.__skillInkIR) return ctx.__skillInkIR;
    var sec = 0.62;
    var len = Math.floor(ctx.sampleRate * sec);
    var buf = ctx.createBuffer(2, len, ctx.sampleRate);
    var seed = 7717;
    for (var ch = 0; ch < 2; ch++) {
      var d = buf.getChannelData(ch);
      for (var i = 0; i < len; i++) {
        seed = (seed * 1103515245 + 12345) & 0x7fffffff;
        var n = (seed / 0x7fffffff) * 2 - 1;
        var env = Math.pow(1 - i / len, 2.8);
        d[i] = n * env * 0.6;
      }
    }
    ctx.__skillInkIR = buf;
    return buf;
  }

  function buildGraph(ctx, opts) {
    var master = gainNode(ctx, opts && opts.muted ? 0 : 0.9);
    var comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -20;
    comp.knee.value = 18;
    comp.ratio.value = 3.2;
    comp.attack.value = 0.003;
    comp.release.value = 0.24;
    var input = gainNode(ctx, 1);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 34;
    input.connect(hp);
    hp.connect(comp);
    comp.connect(master);
    master.connect(ctx.destination);

    var reverb = ctx.createConvolver();
    reverb.buffer = impulse(ctx);
    reverb.connect(master);

    return { master: master, input: input, reverb: reverb };
  }

  /* 一次播放 = 一个独立的干 / 湿总线；停止时把总线拉掉，已排的程序也一起哑掉 */
  function session(ctx, graph) {
    var bus = gainNode(ctx, 1);
    bus.connect(graph.input);
    var send = gainNode(ctx, 0.20);
    send.connect(graph.reverb);
    return { bus: bus, send: send };
  }

  /* ================================================================
   * 四件事
   * ============================================================== */
  function cueInk(ctx, g, t) {
    /* 墨落到纸里的那一下：低频下坠 + 纸的闷响 */
    var o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(126, t);
    o.frequency.exponentialRampToValueAtTime(62, t + 0.20);
    var og = gainNode(ctx, 0.0001);
    og.gain.setValueAtTime(0.0001, t);
    og.gain.linearRampToValueAtTime(VOICE.ink, t + 0.012);
    og.gain.exponentialRampToValueAtTime(0.0001, t + 0.26);
    o.connect(og);
    og.connect(g.bus);
    o.start(t);
    o.stop(t + 0.28);

    var n = noise(ctx, g.bus, t, 0.18);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.setValueAtTime(900, t);
    lp.frequency.exponentialRampToValueAtTime(320, t + 0.16);
    var ng = gainNode(ctx, 0.0001);
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.linearRampToValueAtTime(VOICE.paper, t + 0.02);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.20);
    n.disconnect();
    n.connect(lp);
    lp.connect(ng);
    ng.connect(g.bus);
  }

  function cueWriting(ctx, g, t0, count, stagger) {
    /* 逐字落纸：每字一声极轻的"擦"，只做存在感，不抢圆相 */
    var rnd = rng(918273);
    for (var i = 0; i < count; i++) {
      var t = t0 + i * stagger + 0.035;
      var n = noise(ctx, g.bus, t, 0.055);
      var bp = ctx.createBiquadFilter();
      bp.type = "bandpass";
      bp.frequency.setValueAtTime(1700 + rnd() * 900, t);
      bp.Q.value = 0.9;
      var ng = gainNode(ctx, 0.0001);
      ng.gain.setValueAtTime(0.0001, t);
      ng.gain.linearRampToValueAtTime(VOICE.paper * (0.45 + rnd() * 0.4), t + 0.006);
      ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.055);
      n.disconnect();
      n.connect(bp);
      bp.connect(ng);
      var p = panner(ctx, (i - (count - 1) / 2) * 0.18);
      if (p) { ng.connect(p); p.connect(g.bus); } else ng.connect(g.bus);
      var send = gainNode(ctx, 0.35);
      ng.connect(send);
      send.connect(g.send);
    }
  }

  function cueBrush(ctx, g, t0, dur) {
    /* 圆相：一条从左扫到右的快笔。带通在扫的过程中先高后低，像笔锋从湿到枯。 */
    var t1 = t0 + dur;
    var n = noise(ctx, g.bus, t0, dur + 0.05);
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.Q.value = 1.25;
    bp.frequency.setValueAtTime(760, t0);
    bp.frequency.exponentialRampToValueAtTime(3000, t0 + dur * 0.36);
    bp.frequency.exponentialRampToValueAtTime(1250, t1);
    var ng = gainNode(ctx, 0.0001);
    ng.gain.setValueAtTime(0.0001, t0);
    ng.gain.linearRampToValueAtTime(VOICE.brush, t0 + 0.022);
    ng.gain.linearRampToValueAtTime(VOICE.brush * 0.72, t0 + dur * 0.45);
    ng.gain.linearRampToValueAtTime(VOICE.brush * 0.34, t0 + dur * 0.80);
    ng.gain.exponentialRampToValueAtTime(0.0001, t1 + 0.02);
    n.disconnect();
    n.connect(bp);
    bp.connect(ng);
    var p = panner(ctx, -0.55);
    if (p) {
      p.pan.setValueAtTime(-0.55, t0);
      p.pan.linearRampToValueAtTime(0.55, t0 + dur * 0.9);
      ng.connect(p);
      p.connect(g.bus);
      var send = gainNode(ctx, 0.32);
      p.connect(send);
      send.connect(g.send);
    } else {
      ng.connect(g.bus);
    }

    /* 刷毛的高频：轻，只让"扫"有质感 */
    var n2 = noise(ctx, g.bus, t0, dur);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 3800;
    var ng2 = gainNode(ctx, 0.0001);
    ng2.gain.setValueAtTime(0.0001, t0 + 0.01);
    ng2.gain.linearRampToValueAtTime(VOICE.bristle, t0 + 0.05);
    ng2.gain.linearRampToValueAtTime(VOICE.bristle * 0.35, t1);
    ng2.gain.exponentialRampToValueAtTime(0.0001, t1 + 0.02);
    n2.disconnect();
    n2.connect(hp);
    hp.connect(ng2);
    ng2.connect(g.bus);

    /* 干笔：后段的几粒"嚓"，越靠收笔越稀 */
    var rnd = rng(5150);
    var count = 7;
    for (var i = 0; i < count; i++) {
      var u = 0.35 + (i / count) * 0.60 + rnd() * 0.03;
      var tc = t0 + dur * Math.min(1, u);
      var nn = noise(ctx, g.bus, tc, 0.035);
      var hb = ctx.createBiquadFilter();
      hb.type = "bandpass";
      hb.frequency.value = 2600 + rnd() * 2200;
      hb.Q.value = 2.2;
      var cg = gainNode(ctx, 0.0001);
      cg.gain.setValueAtTime(0.0001, tc);
      cg.gain.linearRampToValueAtTime(VOICE.crackle * (0.4 + rnd() * 0.8), tc + 0.004);
      cg.gain.exponentialRampToValueAtTime(0.0001, tc + 0.032);
      nn.disconnect();
      nn.connect(hb);
      hb.connect(cg);
      cg.connect(g.bus);
    }
  }

  function cueSeal(ctx, g, t) {
    /* 朱印落纸：木头的"笃" + 印面压下去的纸声 */
    var o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(196, t);
    o.frequency.exponentialRampToValueAtTime(84, t + 0.09);
    var og = gainNode(ctx, 0.0001);
    og.gain.setValueAtTime(0.0001, t);
    og.gain.linearRampToValueAtTime(VOICE.seal, t + 0.004);
    og.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
    o.connect(og);
    og.connect(g.bus);
    o.start(t);
    o.stop(t + 0.13);

    var n = noise(ctx, g.bus, t, 0.10);
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.frequency.value = 780;
    bp.Q.value = 0.8;
    var ng = gainNode(ctx, 0.0001);
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.linearRampToValueAtTime(VOICE.paper * 1.1, t + 0.006);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + 0.09);
    n.disconnect();
    n.connect(bp);
    bp.connect(ng);
    ng.connect(g.bus);

    var n2 = noise(ctx, g.bus, t, 0.04);
    var hp = ctx.createBiquadFilter();
    hp.type = "highpass";
    hp.frequency.value = 2600;
    var ng2 = gainNode(ctx, 0.0001);
    ng2.gain.setValueAtTime(0.0001, t);
    ng2.gain.linearRampToValueAtTime(0.035, t + 0.003);
    ng2.gain.exponentialRampToValueAtTime(0.0001, t + 0.035);
    n2.disconnect();
    n2.connect(hp);
    hp.connect(ng2);
    ng2.connect(g.bus);

    var send = gainNode(ctx, 0.32);
    og.connect(send);
    ng.connect(send);
    send.connect(g.send);
  }

  function cueFade(ctx, g, t, dur) {
    /* 化墨：一条很轻的气声，从"纸面"慢慢退到远处 */
    var n = noise(ctx, g.bus, t, dur);
    var bp = ctx.createBiquadFilter();
    bp.type = "bandpass";
    bp.Q.value = 0.55;
    bp.frequency.setValueAtTime(4200, t);
    bp.frequency.exponentialRampToValueAtTime(900, t + dur);
    var ng = gainNode(ctx, 0.0001);
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.linearRampToValueAtTime(VOICE.fade, t + dur * 0.30);
    ng.gain.linearRampToValueAtTime(VOICE.fade * 0.65, t + dur * 0.66);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    n.disconnect();
    n.connect(bp);
    bp.connect(ng);
    var p = panner(ctx, 0.3);
    if (p) {
      p.pan.setValueAtTime(0.4, t);
      p.pan.linearRampToValueAtTime(-0.2, t + dur);
      ng.connect(p);
      p.connect(g.bus);
      var send = gainNode(ctx, 0.4);
      p.connect(send);
      send.connect(g.send);
    } else ng.connect(g.bus);

    var o = ctx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(72, t);
    o.frequency.exponentialRampToValueAtTime(46, t + dur);
    var og = gainNode(ctx, 0.0001);
    og.gain.setValueAtTime(0.0001, t);
    og.gain.linearRampToValueAtTime(VOICE.lowTail, t + dur * 0.35);
    og.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    o.connect(og);
    og.connect(g.bus);
    o.start(t);
    o.stop(t + dur + 0.02);
  }

  /* 停住那一口气：极轻的两条高音 + 一点纸的底噪，把 1 秒的留白托住 */
  function cueSettle(ctx, g, t, dur) {
    var freqs = [1180, 1760];
    for (var i = 0; i < freqs.length; i++) {
      var o = ctx.createOscillator();
      o.type = "sine";
      o.frequency.setValueAtTime(freqs[i], t);
      o.frequency.linearRampToValueAtTime(freqs[i] * 0.992, t + dur);
      var og = gainNode(ctx, 0.0001);
      og.gain.setValueAtTime(0.0001, t);
      og.gain.linearRampToValueAtTime(VOICE.settle / (i + 1.4), t + dur * 0.28);
      og.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(og);
      og.connect(g.bus);
      var send = gainNode(ctx, 0.5);
      og.connect(send);
      send.connect(g.send);
      o.start(t);
      o.stop(t + dur + 0.02);
    }
    var n = noise(ctx, g.bus, t, dur);
    var lp = ctx.createBiquadFilter();
    lp.type = "lowpass";
    lp.frequency.value = 520;
    var ng = gainNode(ctx, 0.0001);
    ng.gain.setValueAtTime(0.0001, t);
    ng.gain.linearRampToValueAtTime(VOICE.settle * 0.9, t + dur * 0.2);
    ng.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    n.disconnect();
    n.connect(lp);
    lp.connect(ng);
    ng.connect(g.bus);
  }

  /* ================================================================
   * 排一套（一个技能的总谱）
   * ============================================================== */
  function scheduleCues(ctx, g, t0, speed, count) {
    var T = timing();
    var scale = 1 / (speed || 1);
    var at = function (sec) { return t0 + sec * scale; };
    var chars = clamp(Number(count) || 2, 1, 8);

    cueInk(ctx, g, at(T.nameAt));
    cueWriting(ctx, g, at(T.nameAt), chars, T.nameStagger * scale);
    cueBrush(ctx, g, at(T.ringAt), T.ringDur * scale);
    cueSeal(ctx, g, at(T.sealAt));
    cueSettle(ctx, g, at(T.sealAt + 0.05), (T.fadeAt - T.sealAt - 0.03) * scale);
    cueFade(ctx, g, at(T.fadeAt), T.fadeDur * scale);
  }

  /* 离线统计：峰值 / RMS / 每 0.25s 窗的电平（验收用） */
  function statsOf(buffer) {
    var ch = buffer.numberOfChannels;
    var n = buffer.length;
    var peak = 0, sum = 0;
    var windows = [];
    var win = Math.floor(buffer.sampleRate * 0.25);
    var winSum = 0, winN = 0, winPeak = 0;
    for (var i = 0; i < n; i++) {
      var v = 0;
      for (var c = 0; c < ch; c++) v += buffer.getChannelData(c)[i];
      v /= ch;
      var a = Math.abs(v);
      if (a > peak) peak = a;
      sum += v * v;
      if (a > winPeak) winPeak = a;
      winSum += v * v;
      winN++;
      if (winN >= win) {
        windows.push({
          at: +(windows.length * 0.25).toFixed(2),
          rms: +Math.sqrt(winSum / winN).toFixed(4),
          peak: +winPeak.toFixed(4)
        });
        winSum = 0; winN = 0; winPeak = 0;
      }
    }
    if (winN > 0) {
      windows.push({
        at: +(windows.length * 0.25).toFixed(2),
        rms: +Math.sqrt(winSum / winN).toFixed(4),
        peak: +winPeak.toFixed(4)
      });
    }
    return {
      peak: +peak.toFixed(4),
      rms: +Math.sqrt(sum / n).toFixed(5),
      windows: windows
    };
  }

  /* ================================================================
   * 现场播放
   * ============================================================== */
  function create(opts) {
    opts = opts || {};
    var ctx = null;
    var graph = null;
    var sessions = [];
    var muted = !!opts.muted;
    var speed = 1;
    var nameCount = 2;

    function ensure() {
      if (ctx) return ctx;
      var AC = global.AudioContext || global.webkitAudioContext;
      if (!AC) return null;
      ctx = new AC();
      graph = buildGraph(ctx, { muted: muted });
      return ctx;
    }

    function unlock() {
      var c = ensure();
      if (!c) return Promise.resolve(false);
      var p = c.state === "suspended" ? c.resume() : Promise.resolve();
      return p.then(function () { return c.state === "running"; }).catch(function () { return false; });
    }

    function retire(s, fade) {
      var i = sessions.indexOf(s);
      if (i < 0) return;
      sessions.splice(i, 1);
      if (!ctx) return;
      var now = ctx.currentTime;
      var k = fade || 0.008;
      try {
        s.bus.gain.cancelScheduledValues(now);
        s.bus.gain.setTargetAtTime(0, now, k);
        s.send.gain.cancelScheduledValues(now);
        s.send.gain.setTargetAtTime(0, now, k);
      } catch (e) {}
      global.setTimeout(function () {
        try { s.bus.disconnect(); s.send.disconnect(); } catch (e) {}
      }, 120);
    }

    function stop() {
      var old = sessions.slice();
      for (var i = 0; i < old.length; i++) retire(old[i], 0.008);
    }

    function play(fromT, nextSpeed, count) {
      var c = ensure();
      if (!c || !graph) return;
      if (muted) return;
      speed = nextSpeed || 1;
      nameCount = clamp(Number(count) || 2, 1, 8);
      var when = c.currentTime + 0.015;
      var s = session(c, graph);
      sessions.push(s);
      if (sessions.length > 4) retire(sessions[0], 0.02);
      scheduleCues(c, s, when, speed, nameCount);
      var life = Math.max(200, (when - c.currentTime + timing().duration / speed + 0.65) * 1000);
      global.setTimeout(function () { retire(s, 0.02); }, life);
      /* 程序都自带 stop 时间；下一次 play / stop 时把这条总线摘掉即可，不另设收尾计时器
         （否则旧的计时器会把新一次发动的声音一起掐掉）。 */
    }

    function mute(on) {
      muted = !!on;
      if (graph && ctx) {
        var now = ctx.currentTime;
        graph.master.gain.cancelScheduledValues(now);
        graph.master.gain.setTargetAtTime(muted ? 0 : 0.9, now, 0.012);
      }
      if (muted) stop();
      if (ctx) { try { localStorage.setItem("sgs.sfx", muted ? "off" : "on"); } catch (e) {} }
      return muted;
    }

    return {
      unlock: unlock,
      play: play,
      stop: stop,
      mute: mute,
      muted: function () { return muted; },
      get context() { return ctx; }
    };
  }

  /* ================================================================
   * 离线渲染（视频音轨 / 自检）
   * ============================================================== */
  function renderOffline(duration, starts, opts) {
    var OAC = global.OfflineAudioContext || global.webkitOfflineAudioContext;
    if (!OAC) return Promise.reject(new Error("OfflineAudioContext unavailable"));
    var sec = Math.max(0.5, Number(duration) || timing().duration);
    var len = Math.ceil(SR * sec);
    var ctx = new OAC(2, len, SR);
    var graph = buildGraph(ctx, { muted: false });
    var s = session(ctx, graph);
    var list = Array.isArray(starts) && starts.length ? starts : [0];
    for (var i = 0; i < list.length; i++) {
      scheduleCues(ctx, s, Number(list[i]) || 0, (opts && opts.speed) || 1, (opts && opts.chars) || 2);
    }
    return ctx.startRendering().then(function (buf) {
      return { buffer: buf, stats: statsOf(buf) };
    });
  }

  function wavBase64(buffer) {
    var ch = buffer.numberOfChannels;
    var sr = buffer.sampleRate;
    var n = buffer.length;
    var bytes = 44 + n * ch * 2;
    var ab = new ArrayBuffer(bytes);
    var dv = new DataView(ab);
    function str(off, s) { for (var i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i)); }
    str(0, "RIFF"); dv.setUint32(4, bytes - 8, true); str(8, "WAVE");
    str(12, "fmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
    dv.setUint16(22, ch, true); dv.setUint32(24, sr, true);
    dv.setUint32(28, sr * ch * 2, true); dv.setUint16(32, ch * 2, true); dv.setUint16(34, 16, true);
    str(36, "data"); dv.setUint32(40, n * ch * 2, true);
    var off = 44;
    var chans = [];
    for (var c = 0; c < ch; c++) chans.push(buffer.getChannelData(c));
    for (var i = 0; i < n; i++) {
      for (var c2 = 0; c2 < ch; c2++) {
        var v = Math.max(-1, Math.min(1, chans[c2][i]));
        dv.setInt16(off, v < 0 ? v * 0x8000 : v * 0x7fff, true);
        off += 2;
      }
    }
    var bytesArr = new Uint8Array(ab);
    var bin = "";
    for (var j = 0; j < bytesArr.length; j++) bin += String.fromCharCode(bytesArr[j]);
    return global.btoa(bin);
  }

  global.SgsSkillInkSfx = {
    create: create,
    renderOffline: renderOffline,
    wavBase64: wavBase64,
    VOICE: VOICE,
    VERSION: "1.0"
  };
})(window);
