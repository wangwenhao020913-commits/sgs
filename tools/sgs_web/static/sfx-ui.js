/* SGS 前端 · 无属性伤害演出的**音效 UI**（票 06 落成、damage-fx 票 08 从 `effects.js` 抽出）：
 * 顶栏「♪」开关、一次性手势解锁、`localStorage` 记忆、三条声道池、`?audiotest=1` 自检。
 *
 * 为什么单独一份：它不属于"动效层"，而且是**用户以后要自己调音色**的那一块——改音效时
 * 面对的是一个独立文件，不是 1400 行的 `effects.js`（音色表在 `sfx.js` 顶部的 `VOICE`，
 * 仍是唯一的调音处）。
 *
 * 缝是**单向**的，只有四个调用点，全在 `damage-fx.js` 的伤害排播里（票 09 从 `effects.js`
 * 搬过去）：
 *
 *   startDamagePlay   → damageSfxStart(play)
 *   finishDamagePlay  → damageSfxStop(play)
 *   renderDamageFrame → damageSfxTick(play, seconds)
 *   clearDamageFx     → damageSfxStopAll()
 *
 * 本文件只接 `play` 对象（读 `play.variant` / `play.fx.timing` / `play.fx.timeline` /
 * `play.slot`），并且只收自己绑过的声道，**不读**伤害排播层的运行状态。反方向只有两个
 * **常量**：`DAMAGE_FX_CONCURRENCY`（声道数 = 同时在播上限）与 `DAMAGE_FX_MAX_VARIANT`
 * （档位号钳到 1..4）——两者都住在 `damage-fx.js`，且都在**调用时**才解析
 * （`index.html` 里本文件排在它之后：`effects.js` → `damage-fx.js` → … → `sfx.js` →
 * `sfx-ui.js` → `main.js`）。
 *
 * 三条硬口径（票 06，逐字未改）：**缺省静音** / **对齐演出时钟** / **没有 `AudioContext`
 * 就静默降级**。下面这一段是它们各自的理由。
 */
"use strict";

/* ---------------------------------------------- 无属性伤害音效（damage-fx 票 06） */

/* 浏览器规定：没有**真实用户手势**之前 `AudioContext` 处于 `suspended`——`new AudioContext()`
 * 不报错、但一点声音都没有。解锁因此必须发生在手势的**同一个 tick** 里（见 main.js:unlockAudio）。
 *
 * 三条硬口径：
 * 1. **缺省静音**：`localStorage['sgs.sfx']` 缺省 `'off'`，首次进来不主动出声；开关关着时
 *    连 `AudioContext` 都不建（零代价静音），用户点开顶栏「♪」那一下本身就是手势。
 * 2. **对齐时间轴**：`tick(t, v)` 的 `t` 是**演出自己的秒数**（与 `fx.draw(t)` 同一个数），
 *    三个 cue 时刻取该档的 `fx.timeline`（`anim.ini` 解算出来的那一份），不另起墙钟。
 * 3. **没有 `AudioContext` 就静默降级**：`SgsSfx.create()` 的 `start()` 返回 false，画面照旧。
 *
 * 为什么是**三条声道**而不是一条：`sfx.js` 的 `tick` 用一个 `lastT` 判"跨过了哪个 cue"，
 * 一条声道同时喂两个时钟就会互相吞 cue；而排播口允许**三条同时开演**（相邻错开 120ms）。
 * 每条声道持有一个**可变**的时间轴对象（`create()` 按引用读它），开演时把该档的三个时刻
 * 写进去——于是声道跨演出复用，也不需要在开演时新建 `AudioContext`。
 */

/** 开关的持久化键与两个取值（票面 §做法 2：缺省 `'off'`）。 */
const DAMAGE_SFX_KEY = "sgs.sfx";
const DAMAGE_SFX_ON = "on";
const DAMAGE_SFX_OFF = "off";

/** 当前开关（启动时从 `localStorage` 读，缺省关）。 */
let damageSfxOn = false;

/** 一次性手势解锁是否已经发生过（`main.js` 的 `pointerdown` 只算一次）。 */
let damageSfxUnlocked = false;

/** 声道池：`[{player, timeline, live}]`，长度 = 同时开演上限（3）。
 *
 * `live` = 这个播放器真的建出了 `AudioContext`（`start()` 返回 true）；false 的声道此后
 * 不再喂 `tick`——没有 `AudioContext` 的浏览器上整套音效就是**不存在**，不报错。
 */
const damageSfxLanes = [];

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

/** 声道建出来时的占位时间轴：用**1 点档的游戏时间轴**（每次开演都会按该档重写这三个数）。 */
function damageSfxBaseTimeline() {
  const api = window.SgsDamageFX;
  if (api && typeof api.gameTiming === "function") return api.gameTiming(1).timeline;
  return (api && api.TIMELINE) || {};
}

/** 一条声道取一份**可变**的时间轴副本：`create()` 捕获的是对象引用，开演时原地改这三个数。 */
function damageSfxTimeline(source) {
  const timeline = source || {};
  return {
    CHARGE: Number(timeline.CHARGE) || 0,
    LAUNCH: Number(timeline.LAUNCH) || 0,
    IMPACT: Number(timeline.IMPACT) || 0,
  };
}

/** 建声道并**立刻** `start()`（`sfx.js` 内部会 `ctx.resume()`）。
 *
 * ⚠️ 只能在**真实用户手势的同一个 tick** 里调用：iOS / Safari 在 `fetch` 回调、定时器、
 * `requestAnimationFrame` 里 resume 一律不生效（票面 §别踩的坑），声音会一直挂着不出。
 * 没有 `AudioContext`（老浏览器 / 被策略禁用）时 `start()` 返回 false ⇒ 静默降级。
 * 返回真正出得了声的声道数。
 */
function damageSfxEnsureLanes() {
  const api = window.SgsSfx;
  if (!api || typeof api.create !== "function") return 0;
  const base = damageSfxBaseTimeline();
  let live = 0;
  while (damageSfxLanes.length < DAMAGE_FX_CONCURRENCY) {
    const timeline = damageSfxTimeline(base);
    const player = api.create(timeline);
    if (!player || typeof player.tick !== "function") break;
    const started = typeof player.start === "function" ? player.start() : false;
    damageSfxLanes.push({ player, timeline, live: started === true });
    if (started === true) live += 1;
  }
  damageSfxMute(!damageSfxOn);
  return live;
}

/** 把开关灌到所有声道上（没有声道时只是空转：状态仍由 `damageSfxOn` 记着）。 */
function damageSfxMute(muted) {
  for (const lane of damageSfxLanes) {
    if (!lane || !lane.player || typeof lane.player.mute !== "function") continue;
    lane.player.mute(Boolean(muted));
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
  return damageSfxEnsureLanes() > 0;
}

/** 开 / 关 + 记忆 + 刷按钮；开的时候顺手建声道（这次点击本身就是手势）。 */
function damageSfxSetEnabled(on) {
  damageSfxOn = Boolean(on);
  if (damageSfxOn) damageSfxEnsureLanes();
  damageSfxMute(!damageSfxOn);
  damageSfxRemember(damageSfxOn);
  damageSfxSyncButton();
  return damageSfxOn;
}

/** 顶栏「♪」的点击处理器（`main.js` 接线）。 */
function damageSfxToggle() {
  return damageSfxSetEnabled(!damageSfxOn);
}

/** `sfx.js` 的 `tick(t, v)` 要的档位参数（形状与参考实现的 `SgsDamageFX.VARIANTS[id]` 一致）。
 *
 * 注意 `fx.variant` 只是**档位号**（1..4），真正的时长在 `fx.timing` —— 那是票 04 从
 * `anim.ini` 解算出来的毫秒表。`whoosh` 的飞行时长、`strobe` / `split` 的窗口都读它，
 * 不在这里另写一份数字（否则调 ini 之后声音与画面就分家了）。
 */
function damageSfxVoice(play) {
  const timing = (play.fx && play.fx.timing) || {};
  return {
    dmg: Math.max(1, Math.min(DAMAGE_FX_MAX_VARIANT, Number(play.variant) || 1)),
    flight: Math.max(0, Number(timing.flight) || 0) / 1000,
    strobe: Math.max(0, Number(timing.strobe) || 0) / 1000,
    split: Math.max(0, Number(timing.split) || 0),
  };
}

/** 一条演出开演：把**这一档**的三个时刻写进它那条声道，并 `start()` 重置 cue 游标。
 *
 * `play.slot` 是离屏舞台槽（在播期间唯一，0..2），正好一条声道一个槽——三条并发各有各的
 * 时钟，互不吞 cue。返回占用的声道（没声道 / 已关时 null）。
 */
function damageSfxStart(play) {
  if (!play || !damageSfxLanes.length) return null;
  const lane = damageSfxLanes[play.slot];
  if (!lane || !lane.live) return null;
  const timeline = damageSfxTimeline(play.fx && play.fx.timeline);
  lane.timeline.CHARGE = timeline.CHARGE;
  lane.timeline.LAUNCH = timeline.LAUNCH;
  lane.timeline.IMPACT = timeline.IMPACT;
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
 * 清场由调用方（`effects.js:clearDamageFx`）决定，音效层不需要知道伤害排播的内部结构。
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
