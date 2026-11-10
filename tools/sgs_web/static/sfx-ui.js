/* SGS 前端 · 伤害演出的**音效 UI**（票 06 落成、damage-fx 票 08 从 `effects.js` 抽出；
 * damage-fx-natures 票 01 改成**按属性模块**分声道池）：
 * 顶栏「♪」开关、一次性手势解锁、`localStorage` 记忆、模块化声道池、`?audiotest=1` 自检。
 *
 * 为什么单独一份：它不属于"动效层"，而且是**用户以后要自己调音色**的那一块——改音效时
 * 面对的是一个独立文件，不是 1400 行的 `effects.js`（音色表在 `sfx.js` / `sfx-thunder.js`
 * 顶部，仍是各自的唯一调音处）。
 *
 * 缝是**单向**的，只有四个调用点，全在 `damage-fx.js` 的伤害排播里（票 09 从 `effects.js`
 * 搬过去）：
 *
 *   startDamagePlay   → damageSfxStart(play)
 *   finishDamagePlay  → damageSfxStop(play)
 *   renderDamageFrame → damageSfxTick(play, seconds)
 *   clearDamageFx     → damageSfxStopAll()
 *
 * 本文件只接 `play` 对象（读 `play.nature` / `play.variant` / `play.fx.timing` /
 * `play.fx.timeline` / `play.slot` / `play.flash`），并且只收自己绑过的声道，**不读**伤害
 * 排播层的运行状态。反方向只读三个**纯读**口：`DAMAGE_FX_MAX_VARIANT`（档位号钳到 1..4）、
 * `damageFxCapFor(nature)`（声道池长度 = 该属性的在播上限；本票恒 3）与
 * `damageFxNatureKind(nature)`（属性归类）——三者都住在 `damage-fx.js`，都只读、都不反向
 * 驱动排播，且都在**调用时**才解析。
 *
 * 三条硬口径（票 06，逐字未改）：**缺省静音** / **对齐演出时钟** / **没有 `AudioContext`
 * 就静默降级**。下面这一段是它们各自的理由。
 */
"use strict";

/* ---------------------------------------------- 伤害音效（damage-fx 票 06） */

/* 浏览器规定：没有**真实用户手势**之前 `AudioContext` 处于 `suspended`——`new AudioContext()`
 * 不报错、但一点声音都没有。解锁因此必须发生在手势的**同一个 tick** 里（见 main.js:unlockAudio）。
 *
 * 三条硬口径：
 * 1. **缺省静音**：`localStorage['sgs.sfx']` 缺省 `'off'`，首次进来不主动出声；开关关着时
 *    连 `AudioContext` 都不建（零代价静音），用户点开顶栏「♪」那一下本身就是手势。
 * 2. **对齐时间轴**：`tick(t, v)` 的 `t` 是**演出自己的秒数**（与 `fx.draw(t)` 同一个数），
 *    三个 cue 时刻取该档的 `fx.timeline`（`anim.ini` 解算出来的那一份），不另起墙钟。
 * 3. **没有 `AudioContext` 就静默降级**：播放器的 `start()` 返回 false，画面照旧。
 *
 * **按模块分池**（票 01）：每条声道**绑死**一个音效模块（剑 = `SgsSfx`、雷 =
 * `SgsThunderSfx`），池子按模块分组；一条声道同时喂两个时钟会互相吞 cue，雷与剑因此
 * 各有各的声道。池长 = 该属性的 cap（`damageFxCapFor`），开演时用 `play.slot` 取声道。
 * 池**惰性建**：只有页面上加载了该模块、且用户点开「♪」时才建（不一次把三套都建出来）。
 */

/** 开关的持久化键与两个取值（票面 §做法 2：缺省 `'off'`）。 */
const DAMAGE_SFX_KEY = "sgs.sfx";
const DAMAGE_SFX_ON = "on";
const DAMAGE_SFX_OFF = "off";

/** 当前开关（启动时从 `localStorage` 读，缺省关）。 */
let damageSfxOn = false;

/** 一次性手势解锁是否已经发生过（`main.js` 的 `pointerdown` 只算一次）。 */
let damageSfxUnlocked = false;

/** 声道池：**按音效模块分组** `{SgsSfx: [{player, timeline, live}], SgsThunderSfx: [...]}`。
 *
 * `live` = 这个播放器真的建出了 `AudioContext`（`start()` 返回 true）；false 的声道此后
 * 不再喂 `tick`——没有 `AudioContext` 的浏览器上整套音效就是**不存在**，不报错。
 * 池子惰性建（`damageSfxEnsureModules`）：只有加载了该模块、且用户点开「♪」时才建。
 */
const damageSfxLanes = {};

/** 已经绑上声道的演出（`damageSfxStart` 记下、`damageSfxStop` 摘除）。
 *
 * 换局清场时 `damageSfxStopAll()` 收的是**这张表**，不去读伤害排播层的在播表
 * （`damagePlays` 是 `damage-fx.js` 的私有状态）——这条缝是单向的：伤害 → 音效。
 */
const damageSfxBound = new Set();

/** `localStorage` 在隐私模式 / 禁用 Cookie 的浏览器里**读一下就抛**；取不到就当没有记忆。 */
function damageSfxStore() {
  try {
    return window.localStorage || null;
  } catch (error) {
    return null;
  }
}

/** 记忆里的开关：**只有** `'on'` 算开；缺键 / 空串 / 别的值（含 `'off'`）一律静音。 */
function damageSfxRemembered() {
  const store = damageSfxStore();
  if (!store || typeof store.getItem !== "function") return false;
  try {
    return store.getItem(DAMAGE_SFX_KEY) === DAMAGE_SFX_ON;
  } catch (error) {
    return false;
  }
}

/** 写记忆（失败不抛：记不住不该让页面挂掉）。 */
function damageSfxRemember(on) {
  const store = damageSfxStore();
  if (!store || typeof store.setItem !== "function") return false;
  try {
    store.setItem(DAMAGE_SFX_KEY, on ? DAMAGE_SFX_ON : DAMAGE_SFX_OFF);
    return true;
  } catch (error) {
    return false;
  }
}

/** 顶栏「♪」的文案（票面 §做法 2 的措辞）：`♪ 音效 开` / `♪ 音效 关`。 */
function damageSfxButtonText(on) {
  return on ? "♪ 音效 开" : "♪ 音效 关";
}

/** 把开关状态刷到顶栏按钮上（文案 / `aria-pressed` / `.on` 三处一起变）。 */
function damageSfxSyncButton() {
  const button = $("btn-sfx");
  if (!button) return;
  button.textContent = damageSfxButtonText(damageSfxOn);
  button.setAttribute("aria-pressed", damageSfxOn ? "true" : "false");
  if (!button.classList) return;
  if (damageSfxOn) button.classList.add("on");
  else button.classList.remove("on");
}

/** 属性 → **音效引擎 + 它的画面模块**（声道池按模块分组；时间轴从画面模块的
 *  `gameTiming` 取）。这是音效这一侧唯一的查表处。
 *
 * 为什么不复用 `damageFxRendererFor`：画面模块名（`SgsThunderFX`）与音效模块名
 * （`SgsThunderSfx`）是两套全局名，映射只能在音效层说。属性归类仍走 `damageFxNatureKind`
 * （`damage-fx.js` 的单一判据，调用时解析）。
 */
function damageSfxEngineFor(nature) {
  const kind = typeof damageFxNatureKind === "function" ? damageFxNatureKind(nature) : "NORMAL";
  if (kind === "THUNDER") {
    return {
      key: "SgsThunderSfx", nature: kind,
      sfx: window.SgsThunderSfx || null, fx: window.SgsThunderFX || null,
    };
  }
  if (kind === "FIRE") {
    return {
      key: "SgsFireSfx", nature: kind,
      sfx: window.SgsFireSfx || null, fx: window.SgsFireFX || null,
    };
  }
  return {
    key: "SgsSfx", nature: "NORMAL",
    sfx: window.SgsSfx || null, fx: window.SgsDamageFX || null,
  };
}

/** 声道建出来时的占位时间轴：用**该画面的 1 点档**（每次开演都会按该档重写这些数）。
 *
 * 画面模块缺了（只加载了音效）⇒ 空表；`voice` 仍在每帧给档位，声音不会因此消失。
 */
function damageSfxBaseTimeline(fxApi) {
  const api = fxApi || null;
  if (api && typeof api.gameTiming === "function") return api.gameTiming(1).timeline;
  return (api && api.TIMELINE) || {};
}

/** 一条声道取一份**可变**的时间轴副本：`create()` 捕获的是对象引用，开演时原地改写。
 *
 * `SKY_AT` / `EXPLODE` / `DURATION` 只有雷电用（4 档的天雷与爆炸）；剑的声道读不到它们
 * 也没有关系（`SgsSfx` 的 cue 只认前三个）。
 */
function damageSfxTimeline(source) {
  const timeline = source || {};
  return {
    CHARGE: Number(timeline.CHARGE) || 0,
    LAUNCH: Number(timeline.LAUNCH) || 0,
    IMPACT: Number(timeline.IMPACT) || 0,
    SKY_AT: Number(timeline.SKY_AT) || 0,
    EXPLODE: Number(timeline.EXPLODE) || 0,
    DURATION: Number(timeline.DURATION) || 0,
  };
}

/** 给**一个**已加载的音效模块建声道池（惰性：没有该模块就一条都不建）。
 *
 * 声道数 = 该属性的 cap（`damageFxCapFor`，本票恒 3；票 02 改成按键读 1–8）。
 * 每条声道持有一份**可变**的时间轴（`create()` 按引用读它），开演时把该档的 cue 时刻
 * 写进去——于是声道跨演出复用，也不需要在开演时新建 `AudioContext`。
 *
 * ⚠️ 只能在**真实用户手势的同一个 tick** 里调用：iOS / Safari 在 `fetch` 回调、定时器、
 * `requestAnimationFrame` 里 resume 一律不生效（票面 §别踩的坑），声音会一直挂着不出。
 * 没有 `AudioContext`（老浏览器 / 被策略禁用）时 `start()` 返回 false ⇒ 静默降级。
 */
function damageSfxEnsureLanes(engine) {
  if (!engine || !engine.sfx || typeof engine.sfx.create !== "function") return [];
  const key = engine.key;
  if (!damageSfxLanes[key]) damageSfxLanes[key] = [];
  const lanes = damageSfxLanes[key];
  const cap = typeof damageFxCapFor === "function"
    ? damageFxCapFor(engine.nature)
    : DAMAGE_FX_CONCURRENCY;
  const base = damageSfxBaseTimeline(engine.fx);
  while (lanes.length < cap) {
    const timeline = damageSfxTimeline(base);
    const player = engine.sfx.create(timeline);
    if (!player || typeof player.tick !== "function") break;
    const started = typeof player.start === "function" ? player.start() : false;
    lanes.push({ player, timeline, live: started === true });
  }
  damageSfxMute(!damageSfxOn);
  return lanes;
}

/** 给**页面上已经加载**的音效模块各建一套声道（剑 / 雷；火还没有）。
 *
 * 为什么要全部建：`AudioContext` 必须在手势的同一个 tick 里 resume。若等到第一次伤害才建，
 * 那时距用户点开「♪」已经隔了很久（Safari 会再次挂起），雷电就永远不出声。这里只给
 * **存在的**模块建池——"三套"里缺谁就跳过谁，不会凭空建出 `SgsFireSfx`。
 * 返回真正出得了声的声道数。
 */
function damageSfxEnsureModules() {
  let live = 0;
  for (const nature of ["NORMAL", "THUNDER", "FIRE"]) {
    const engine = damageSfxEngineFor(nature);
    if (!engine.sfx) continue;
    for (const lane of damageSfxEnsureLanes(engine)) {
      if (lane.live) live += 1;
    }
  }
  return live;
}

/** 把开关灌到所有声道上（没有声道时只是空转：状态仍由 `damageSfxOn` 记着）。 */
function damageSfxMute(muted) {
  for (const key of Object.keys(damageSfxLanes)) {
    for (const lane of damageSfxLanes[key]) {
      if (!lane || !lane.player || typeof lane.player.mute !== "function") continue;
      lane.player.mute(Boolean(muted));
    }
  }
}

/** 启动时读一次记忆并刷按钮（`main.js:wire()` 里调一次）。 */
function damageSfxSync() {
  damageSfxOn = damageSfxRemembered();
  damageSfxMute(!damageSfxOn);
  damageSfxSyncButton();
  return damageSfxOn;
}

/** 一次性手势解锁（`main.js` 的 `pointerdown` 处理器**同步**调它）。
 *
 * 开关关着时**不建 `AudioContext`**：缺省静音因此是零代价的；用户点开「♪」那一下同样是
 * 手势，声道在 `damageSfxSetEnabled` 里建。开着（上次留下的记忆）才在这次手势里建好并 resume。
 */
function damageSfxUnlock() {
  if (damageSfxUnlocked) return false;
  damageSfxUnlocked = true;
  if (!damageSfxOn) return false;
  return damageSfxEnsureModules() > 0;
}

/** 开 / 关 + 记忆 + 刷按钮；开的时候顺手建声道（这次点击本身就是手势）。 */
function damageSfxSetEnabled(on) {
  damageSfxOn = Boolean(on);
  if (damageSfxOn) damageSfxEnsureModules();
  damageSfxMute(!damageSfxOn);
  damageSfxRemember(damageSfxOn);
  damageSfxSyncButton();
  return damageSfxOn;
}

/** 顶栏「♪」的点击处理器（`main.js` 接线）。 */
function damageSfxToggle() {
  return damageSfxSetEnabled(!damageSfxOn);
}

/** 音效模块的 `tick(t, v)` 要的档位参数（剑的 `SgsSfx` 要 `dmg / flight / strobe /
 *  split`，雷的 `SgsThunderSfx` 只要 `dmg`，`flash` 留给将来的逐帧音量）。
 *
 * 注意 `fx.variant` 只是**档位号**（1..4），真正的时长在 `fx.timing` —— 那是 `anim.ini`
 * 解算出来的毫秒表。`whoosh` 的飞行时长、`strobe` / `split` 的窗口都读它，不在这里另写
 * 一份数字（否则调 ini 之后声音与画面就分家了）。
 */
function damageSfxVoice(play) {
  const timing = (play.fx && play.fx.timing) || {};
  return {
    dmg: Math.max(1, Math.min(DAMAGE_FX_MAX_VARIANT, Number(play.variant) || 1)),
    flight: Math.max(0, Number(timing.flight) || 0) / 1000,
    strobe: Math.max(0, Number(timing.strobe) || 0) / 1000,
    split: Math.max(0, Number(timing.split) || 0),
    flash: Math.max(0, Math.min(1, Number(timing.flash) || 0.6)),
  };
}

/** 一条演出开演：按 `play.nature` 取该模块的声道池，把**这一档**的 cue 时刻写进去，
 *  并 `start()` 重置 cue 游标。
 *
 * `play.slot` 是离屏舞台槽（在播期间唯一），正好一条声道一个槽——并发各有各的时钟，
 * 互不吞 cue。**不在这里建池**：池只在手势里建（开关关着 / 没解锁时这里是 null，不建
 * `AudioContext`）。返回占用的声道（没声道 / 已关时 null）。
 */
function damageSfxStart(play) {
  if (!play) return null;
  const engine = damageSfxEngineFor(play.nature);
  const lanes = damageSfxLanes[engine.key];
  if (!lanes || !lanes.length) return null;
  const lane = lanes[play.slot];
  if (!lane || !lane.live) return null;
  const timeline = damageSfxTimeline(play.fx && play.fx.timeline);
  lane.timeline.CHARGE = timeline.CHARGE;
  lane.timeline.LAUNCH = timeline.LAUNCH;
  lane.timeline.IMPACT = timeline.IMPACT;
  lane.timeline.SKY_AT = timeline.SKY_AT;
  lane.timeline.EXPLODE = timeline.EXPLODE;
  lane.timeline.DURATION = timeline.DURATION;
  play.sfxVoice = damageSfxVoice(play);
  // `start()` 里会重置 `lastT`：本段从 0 重新判 cue（声道是跨演出复用的）。
  if (typeof lane.player.start === "function") lane.player.start();
  if (typeof lane.player.mute === "function") lane.player.mute(!damageSfxOn);
  play.sfxLane = lane;
  damageSfxBound.add(play);
  return lane;
}

/** 每帧：喂**演出自己的** `seconds`（与 `fx.draw(t)` 同一个数）和这一档的档位参数。
 *
 * 这里**不读** `Date.now()` / `stageNow()`：声音的尺子就是画面的尺子，快进 / 暂停 / 掉帧
 * 都不会漂（票面 §做法 3）。
 */
function damageSfxTick(play, seconds) {
  const lane = play && play.sfxLane;
  if (!lane || !lane.live || typeof lane.player.tick !== "function") return;
  const voice = play.sfxVoice || null;
  if (!voice) return;
  lane.player.tick(seconds, voice);
}

/** 一段演出收尾：这条声道静音并停 cue（下次开演重新 `start()`）。 */
function damageSfxStop(play) {
  if (!play) return;
  damageSfxBound.delete(play);
  const lane = play.sfxLane;
  if (!lane) return;
  play.sfxLane = null;
  if (lane.player && typeof lane.player.stop === "function") lane.player.stop();
}

/** 换局 / 版式作废：在播的演出全部收声（尾巴也不留）。
 *
 * 「在播的」= 本文件**自己记的**那几条（`damageSfxBound`）：收声是音效层的事，
 * 清场由调用方（`damage-fx.js:clearDamageFx`）决定，音效层不需要知道伤害排播的内部结构。
 */
function damageSfxStopAll() {
  for (const play of damageSfxBound) damageSfxStop(play);
}

/** URL 里有没有 `?audiotest=1`（`damage-fx 票 06` 的离线自检口）。 */
function damageSfxAudiotestRequested() {
  const search = String((window.location && window.location.search) || "");
  return /(?:^[?&])audiotest=1(?:&|$)/.test(search);
}

/** `?audiotest=1`：离线渲染四档，把四个窗口的电平写进 `<html data-audio>`。
 *
 * 这条链**不碰现场播放器**：`SgsSfx.renderOffline` 走 `OfflineAudioContext`（纯函数式，
 * 与 `render_video.py` 将来接 FFmpeg 用的是同一条），所以 headless 里也能给出"真的出声了"
 * 的数值证据——声音本身（听感）仍然只能在真实浏览器里听。窗口口径与参考实现 `shell.js`
 * 一致：charge（蓄势）/ launch（出鞘）/ flight（飞行）/ hit（命中），外加整段峰值。
 *
 * 没有 `OfflineAudioContext` 时写 `data-audio="unavailable"`（不抛错）。
 */
function damageSfxAudiotest() {
  const root = document.documentElement;
  if (!root || typeof root.setAttribute !== "function") return null;
  const api = window.SgsSfx;
  const fxApi = window.SgsDamageFX;
  const usable = api && typeof api.renderOffline === "function"
    && fxApi && typeof fxApi.gameTiming === "function";
  if (!usable) {
    root.setAttribute("data-audio", "unavailable");
    return null;
  }
  const round = (value) => Math.round(Number(value) * 10000) / 10000;
  const variants = {};
  const jobs = [1, 2, 3, 4].map((id) => {
    const clock = fxApi.gameTiming(id);
    // 2.6s 盖住全档（最长 2.1s）与命中之后那几段尾巴（`impact` 0.42s / `split` 0.9s）。
    return Promise.resolve(api.renderOffline(clock.variant, clock.timeline, 2.6))
      .then((levels) => {
        if (!levels) return false;
        variants[id] = {
          charge: round(levels.charge.rms),
          launch: round(levels.launch.rms),
          flight: round(levels.flight.rms),
          hit: round(levels.hit.rms),
          peak: round(levels.total.peak),
        };
        return true;
      });
  });
  return Promise.all(jobs).then((done) => {
    if (!done.some(Boolean)) {
      root.setAttribute("data-audio", "unavailable");
      return null;
    }
    const payload = { profile: "game", variants };
    root.setAttribute("data-audio", JSON.stringify(payload));
    return payload;
  });
}
