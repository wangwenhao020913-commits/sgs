/* SGS 前端 · **卡牌语音**（chain-death-voices 票 03）：使用 / 打出那一刻，按**产物牌型** +
 * 使用者的**武将性别**播一条 SGS OL 的原声（`static/voices/<type>_<gender>.mp3`，用户素材）。
 *
 * 三件与别的声音家族**不同**的口径（spec §演出口径「卡牌语音」）：
 *
 * 1. **数据驱动**：唯一真源是下面那张 `CARD_VOICES` 表——覆盖 160 卡牌档**全部 43 类**，
 *    没有素材的显式 `null`（`null` = 明确静默，不是缺项）。查不到类型 / 表项是 `null`
 *    ⇒ **一声不出**，不猜、不退回别的牌型。
 * 2. **可重叠、不打断**：每条新语音**自起一路** `new Audio(...)`，后一条**从不**停前一条——
 *    用户原话"音效播放可以重复，不会被打断"。存活的那几路记在 `live` 里，`ended` / `error`
 *    时摘掉；换局（`session.js`）与关「♪」（`sfx-ui.js`）才 `stopAll()`。
 * 3. **同一个开关、同一次手势、缺省静音**：开关状态由 `sfx-ui.js` 单向灌进来（`mute(on)`），
 *    本模块**不读** `sfx-ui.js` 的任何内部状态；关着时**连 `Audio` 都不建**（零请求）。
 *    `unlock()` 只记"手势已发生"（浏览器的自动播放策略；首次点击「开始对局」后即满足）。
 *
 * 读数（实机 / 烟测用，**不写 DOM**）：`SgsCardVoice.last()`（最近一条真的出了声的事实
 * `{type, gender, name, virtual, src, at}`——`name` / `virtual` 让"这一次是哪张产物牌、
 * 是不是转化 / 虚拟牌"在读数里就是事实，不必只靠载荷用例验；触发仍只按 `type` / `gender`
 * 查表）与 `SgsCardVoice.count()`（累计真的出了声的条数）。
 *
 * 没有 `Audio`（node 垫片 / 关掉媒体能力的浏览器）⇒ 静默降级：返回 `null`，不抛错。
 *
 * 映射表也是 `static/voices/README.md` 那张表的唯一真源（静态用例逐行比对）。
 */
"use strict";

(function (global) {
  /* 映射表：`<card_type>` → 男 / 女两个文件名（`null` = 这类牌今天明确没有语音）。
   *
   * 43 类 = 160 卡牌档（`standard_ex_160`）的全部牌型，顺序与 `sgs.data` 的牌型表一致
   * （基本牌 → 锦囊 → 装备 → 160 档新增）。改这张表 = 改卡牌语音的全部素材口径。
   *
   * - 有语音（20 类）：基本牌 + 普通 / 延时锦囊里 SGS OL 提供了原声的那 20 种；
   * - `peach`（【桃】）：**待补素材**（用户 2026-11-11 确认先静默）。补素材只做两件事：
   *   把 `peach_male.mp3` / `peach_female.mp3` 放进 `voices/`、把这两格改成文件名，代码不动；
   * - 22 种装备：SGS OL 本来就没有装备卡语音 ⇒ `null` 是**预期终态**（不是缺口、不必补）。 */
  var CARD_VOICES = {
    slash: { male: "slash_male.mp3", female: "slash_female.mp3" },
    dodge: { male: "dodge_male.mp3", female: "dodge_female.mp3" },
    peach: { male: null, female: null },
    dismantlement: { male: "dismantlement_male.mp3", female: "dismantlement_female.mp3" },
    snatch: { male: "snatch_male.mp3", female: "snatch_female.mp3" },
    ex_nihilo: { male: "ex_nihilo_male.mp3", female: "ex_nihilo_female.mp3" },
    nullify: { male: "nullify_male.mp3", female: "nullify_female.mp3" },
    duel: { male: "duel_male.mp3", female: "duel_female.mp3" },
    barbarian_invasion: {
      male: "barbarian_invasion_male.mp3",
      female: "barbarian_invasion_female.mp3"
    },
    arrow_barrage: { male: "arrow_barrage_male.mp3", female: "arrow_barrage_female.mp3" },
    peach_garden: { male: "peach_garden_male.mp3", female: "peach_garden_female.mp3" },
    borrowed_sword: { male: "borrowed_sword_male.mp3", female: "borrowed_sword_female.mp3" },
    harvest: { male: "harvest_male.mp3", female: "harvest_female.mp3" },
    indulgence: { male: "indulgence_male.mp3", female: "indulgence_female.mp3" },
    lightning: { male: "lightning_male.mp3", female: "lightning_female.mp3" },
    crossbow: { male: null, female: null },
    qinggang_sword: { male: null, female: null },
    double_sword: { male: null, female: null },
    green_dragon_blade: { male: null, female: null },
    serpent_spear: { male: null, female: null },
    axe: { male: null, female: null },
    halberd: { male: null, female: null },
    qilin_bow: { male: null, female: null },
    ice_sword: { male: null, female: null },
    eight_diagrams: { male: null, female: null },
    renwang_shield: { male: null, female: null },
    jueying: { male: null, female: null },
    dilu: { male: null, female: null },
    zhuahuang_feidian: { male: null, female: null },
    chitu: { male: null, female: null },
    dawan: { male: null, female: null },
    zixing: { male: null, female: null },
    fire_slash: { male: "fire_slash_male.mp3", female: "fire_slash_female.mp3" },
    thunder_slash: { male: "thunder_slash_male.mp3", female: "thunder_slash_female.mp3" },
    wine: { male: "wine_male.mp3", female: "wine_female.mp3" },
    supply_shortage: { male: "supply_shortage_male.mp3", female: "supply_shortage_female.mp3" },
    fire_attack: { male: "fire_attack_male.mp3", female: "fire_attack_female.mp3" },
    iron_chain: { male: "iron_chain_male.mp3", female: "iron_chain_female.mp3" },
    silver_lion: { male: null, female: null },
    vine_armor: { male: null, female: null },
    ancient_scimitar: { male: null, female: null },
    vermilion_feather_fan: { male: null, female: null },
    hualiu: { male: null, female: null }
  };

  /** 唯一一个音量数（响度归一不在本票：40 条素材的音量只能靠耳朵调，spec §已知缺口 7）。 */
  var VOLUME = 0.75;

  /** 素材目录（相对 `index.html`：`static/` 下的一层子目录）。 */
  var BASE = "voices/";

  /** 手势已发生（`unlock()` 只记这一件事；本模块不据此拦播放）。 */
  var gestureSeen = false;

  /** 开关（「♪」）：缺省**静音**，由 `sfx-ui.js:damageSfxMute` 单向灌进来。 */
  var muted = true;

  /** 存活的那几路（每条语音自己一个 `Audio`；`ended` / `error` 时摘掉）。 */
  var live = [];

  /** 最近一条真的出了声的事实（`last()` 的读数）。 */
  var lastEvent = null;

  /** 累计真的出了声的条数（`count()` 的读数）。 */
  var triggered = 0;

  /** 构造器：浏览器的 `Audio`；没有就返回 `null`（静默降级）。 */
  function audioCtor() {
    return typeof global.Audio === "function" ? global.Audio : null;
  }

  /** 查表 ⇒ 文件名；未知类型 / 表项 `null` / 空名一律 `null`（静默，不猜）。 */
  function fileNameFor(type, gender) {
    if (!type) return null;
    if (!Object.prototype.hasOwnProperty.call(CARD_VOICES, type)) return null;
    var row = CARD_VOICES[type];
    if (!row) return null;
    var name = row[gender === "female" ? "female" : "male"];
    return typeof name === "string" && name ? name : null;
  }

  /** 从存活表里摘掉一条（重复摘是零动作：`stopAll` 之后 `ended` 还会回来一次）。 */
  function drop(audio) {
    var index = live.indexOf(audio);
    if (index >= 0) live.splice(index, 1);
  }

  /** 停掉一路：`pause` + 清 `src`（清 `src` 才会真的取消还在下载的那次请求）。 */
  function stopAudio(audio) {
    if (!audio) return;
    try {
      audio.pause();
    } catch (error) {
      // 没 `pause` 的垫片 / 已被回收的元素：静默降级。
    }
    try {
      audio.src = "";
    } catch (error) {
      // `src` 只读的垫片：静默降级。
    }
  }

  /** 一条卡牌事实到点：查表 → **自起一路** → 播。静音 / 未知 / 没素材 / 没 `Audio` 都无声。 */
  function play(event) {
    if (muted) return null;
    var type = event ? event.card_type : null;
    // `gender` 空（无武将模式 / 老载荷）⇒ 回落 `male`（spec：前端回落，服务端不猜）。
    var gender = event && event.gender === "female" ? "female" : "male";
    // `card_name` / `virtual` **不参与查表**（触发仍只按 type / gender）：它们是 `last()` 的
    // 读数载荷，让"这一次是哪张产物牌、是不是转化 / 虚拟牌"有第二个读取方（评审修复）。
    var cardName =
      event && event.card_name !== undefined && event.card_name !== null
        ? String(event.card_name)
        : null;
    var virtual = Boolean(event && event.virtual);
    var name = fileNameFor(type, gender);
    if (!name) return null;
    var Ctor = audioCtor();
    if (!Ctor) return null;
    var src = BASE + name;
    var audio = new Ctor(src);
    try {
      audio.volume = VOLUME;
    } catch (error) {
      // 只读 `volume` 的垫片：静默降级。
    }
    live.push(audio);
    var release = function () {
      drop(audio);
    };
    if (typeof audio.addEventListener === "function") {
      audio.addEventListener("ended", release);
      audio.addEventListener("error", release);
    } else {
      audio.onended = release;
      audio.onerror = release;
    }
    try {
      var pending = audio.play();
      // 自动播放策略 / 用户没交互：`play()` 返回被拒的 Promise，静默吞掉（画面照旧）。
      if (pending && typeof pending.catch === "function") pending.catch(function () {});
    } catch (error) {
      drop(audio);
      return null;
    }
    triggered += 1;
    lastEvent = {
      type: String(type),
      gender: gender,
      name: cardName,
      virtual: virtual,
      src: src,
      at: typeof Date !== "undefined" && Date.now ? Date.now() : 0
    };
    return audio;
  }

  /** 换局 / 关「♪」：停掉**全部**存活语音并清表（上一局的声音不拖进新局）。 */
  function stopAll() {
    var audios = live.slice();
    live = [];
    for (var index = 0; index < audios.length; index += 1) stopAudio(audios[index]);
    return audios.length;
  }

  /** 单向开关（`sfx-ui.js` → 本模块）：关 = 停掉全部存活语音；开 = 下一次事实才建 `Audio`。 */
  function mute(on) {
    muted = Boolean(on);
    if (muted) stopAll();
  }

  /** 手势已发生（`damageSfxUnlock()` 在真实 `pointerdown` 的同一个 tick 里调它）。 */
  function unlock() {
    gestureSeen = true;
    return gestureSeen;
  }

  /** 读数：最近一条真的出了声的事实（没有则 `null`）。 */
  function last() {
    if (!lastEvent) return null;
    return {
      type: lastEvent.type,
      gender: lastEvent.gender,
      name: lastEvent.name,
      virtual: lastEvent.virtual,
      src: lastEvent.src,
      at: lastEvent.at
    };
  }

  /** 读数：累计真的出了声的条数（静音 / 未知 / `null` 表项都不计数）。 */
  function count() {
    return triggered;
  }

  /** 读数：手势是否已发生（浏览器自动播放策略的唯一事实）。 */
  function isUnlocked() {
    return gestureSeen;
  }

  global.SgsCardVoice = {
    CARD_VOICES: CARD_VOICES,
    VOLUME: VOLUME,
    play: play,
    stopAll: stopAll,
    mute: mute,
    unlock: unlock,
    last: last,
    count: count,
    isUnlocked: isUnlocked
  };
})(window);

/* 桥接：`session.js` 按**裸全局名**调本模块（与 `fx-chain.js` 的 `fireChainEventsThrough`
 * 同一姿态）——垫片里 `window` 是另一个对象，只有顶层 `var` 落在脚本自己的全局作用域里，
 * `sfx-ui.js` 那侧仍写 `window.SgsCardVoice`（与它调 `window.SgsChainSfx` 一致）。 */
var SgsCardVoice = window.SgsCardVoice;

/* 排播钩子（与 `damage-fx.js:fireDamageEventsThrough` / `fx-chain.js:fireChainEventsThrough`
 * 同姿态）：`session.js:stepPlayback` 在 `fireDamageEventsThrough(record.seq)` 之后调它。
 *
 * 到点的事实（`seq <= 游标`）**逐条直接播**，其余留在 `state.cardEvents` 里等下一批——
 * **不需要**排播队列：每条语音自起一路 `Audio`，后一条从不打断前一条（用户口径"可以重复、
 * 不会被打断"）。缺 `seq` 的坏条目按"到点"放行（与伤害层同一兜底：不让它卡到下一批）。
 */
var fireCardEventsThrough = function (seq) {
  const events = state.cardEvents || [];
  if (!events.length) return 0;
  const threshold = seq === null || seq === undefined ? Infinity : Number(seq);
  const due = [];
  const kept = [];
  for (const event of events) {
    const eventSeq = Number(event && event.seq);
    if (!Number.isFinite(eventSeq) || eventSeq <= threshold) due.push(event);
    else kept.push(event);
  }
  if (!due.length) return 0;
  state.cardEvents = kept;
  for (const event of due) SgsCardVoice.play(event);
  return due.length;
};

