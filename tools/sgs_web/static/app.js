/* SGS M4 极简界面：只做两件事——渲染事件流、收集并提交应答。
 *
 * 纪律（`04` §4.1 与地图 D8；本轮裁定见 `.scratch/m4-manual-select/spec.md`）：
 *  - 前端**不做规则判定**：合法性只问"这个组合在不在候选索引里"，不读距离 / 花色 / 体力 / 存活；
 *  - **手选卡牌 + 手选目标**：点牌设**焦点**，点座位选目标，再点一次取消；
 *  - **自动选中**只在"合法组合恰好一组、且那组就是池的全部"时触发（省掉一次没有信息量的点击）；
 *  - 提交的 Action **原样取自** legal_actions（不自己拼 Action）；
 *  - 隐藏信息在传输层已被过滤；界面**不自己代答超时**，只显示倒计时（代答在服务端）。
 *
 * **跨线契约**：`pending.candidate_index` 是 `{card_id: entry}`，界面读的字段**只有这四个**——
 *
 *     entry.card          这张牌的牌面（`CardRef`）
 *     entry.pool          合法目标座位
 *     entry.min_targets   这次使用最少要选几个目标
 *     entry.max_targets   这次使用最多能选几个目标
 *     entry.combos        合法目标组合（元组的数组）
 *
 * 真源在 `tools/sgs_web/projection.py:candidate_index`；键名两边必须**逐字**一致。
 * 只断言服务端"发了这个键"是不够的——服务端断言与界面读法之间曾经失配过
 * （界面读 `entry.max` 而服务端发 `max_targets`），后果是 `needsPicker()` 恒假、
 * 唯一一条带目标的候选在未交互时就能提交。故 `ui_smoke.mjs` 的 ㉿K1–K4 两侧都钉。
 */
"use strict";

const $ = (id) => document.getElementById(id);

/** `Phase` 枚举的字面值 → 中文阶段名。
 *
 * 枚举值（`prepare` / `judge` / `draw` / `play` / `discard` / `finish`）是**稳定报文值**，
 * 改它就是改契约（还会波及记录流与基准），所以映射只住表现层这一处。
 * `null` 段也覆盖到：`phase` 可能为 `null`（回合之外）。
 */
const PHASE_LABELS = {
  prepare: "准备阶段",
  judge: "判定阶段",
  draw: "摸牌阶段",
  play: "出牌阶段",
  discard: "弃牌阶段",
  finish: "结束阶段",
};

function phaseLabel(phase) {
  if (!phase) return "—";
  return PHASE_LABELS[phase] || phase;
}

/** 走手选的询问类（本刀射程：出牌阶段 + 响应窗口）。
 *
 * 弃牌阶段的交互是**另一条**手选路（点手牌收进待弃置集合，没有目标要选），
 * 由 :func:`isDiscardPick` 单独认出来——它不进这里，免得 `pickerSeats` 去读候选索引。
 * 其余四类（排列 / 选择面 / 技能发动 / 单选）保留旧的候选列表按钮。
 */
const HAND_PICK_KINDS = ["PLAY_PHASE", "RESPOND", "DODGE"];

/** 事件回放的步长档位（毫秒）：每一条事件在事件流 / 效果区停留这么久。
 *
 * 报障 3：AI 一回合的结算在一次响应里全部返回，界面**一次性**画完，于是【杀】【闪】
 * 【桃】在效果区一闪而过。改成逐条回放后，牌会在效果区停满一个步长。
 * **严格顺序**（用户裁定）：这一批播完才提交新的快照与询问，故播放期间禁掉提交。
 *
 * 步长改成牌桌上的按钮（0.05–0.5 秒，点一下换一档）；缺省 0.1 秒，**不记住**——
 * 刷新/换局回默认，故它只住内存（`state.eventDelayMs`），不落 localStorage。
 */
const DELAY_STEPS_MS = [50, 100, 200, 300, 400, 500];

const state = {
  sessionId: null,
  cursor: -1,
  logSeq: 0,
  view: null,          // 服务端最近一次下发的会话视图
  focusCardId: null,   // **焦点**：正在挑目标的那张候选牌（再点一次取消）
  selectedTargets: [], // 已选目标座位
  selectedOption: null,// 旧路径：选中的 legal_action 下标（弃牌 / 排列 / 单选 / 技能）
  picks: [],           // 手选槽位下标 / 弃牌 card_id / 主动技手选的 card_id
  skillMode: null,     // 主动技"多选手牌 → 点座位"模式（【仁德】）：{skillId,name,requirement,cards,seats}
  skillSeat: null,     // 上面那个模式里已点中的座位
  preactivation: null, // 预发动（W4-1）：{skillId,name,steps,answers,cursor}；本地态，取消零提交
  convertSkill: null,  // 转化技"挑来源牌"模式（【龙胆】）：正在等玩家点一张可转化的手牌
  playback: null,      // 事件回放：{body, records, index, querySeat}
  eventDelayMs: 100,   // 回放步长（牌桌按钮切档）；缺省 0.1s **不随档位表首位走**
  effectCards: [],     // 效果区里当前可见的牌；回放期间由 card_move delta 逐事件派生
  timer: null,
  runs: [],
  replay: null,
  heroCatalog: [],     // 点将列表（`GET /api/heroes` 的启用池）；开局页的选项来源
  logRecords: [],
  lastQuerySeat: null, // 事件流里"上一条询问的 actor"（action 记录靠它归属）
  lastRejectReason: "",   // 已收到的全部下发记录（切换详略时重画用）
};

/* ------------------------------------------------------------------ 网络 */

async function api(path, options) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = { error: text }; }
  if (!response.ok) {
    const error = new Error((body && body.error) || `HTTP ${response.status}`);
    error.status = response.status;
    error.body = body;
    throw error;
  }
  return body;
}

const post = (path, payload) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(payload || {}),
});

/* ------------------------------------------------------------------ 视图 */

function show(view) {
  for (const name of ["lobby", "table", "replay"]) {
    $(`view-${name}`).hidden = name !== view;
    $(`nav-${name}`).classList.toggle("on", name === view);
  }
}

/* ------------------------------------------------------------------ 开局 */

function seedOptions() {
  const count = Number($("in-players").value);
  const select = $("in-seat");
  const previous = select.value;
  select.innerHTML = "";
  for (let seat = 0; seat < count; seat += 1) {
    const option = document.createElement("option");
    option.value = String(seat);
    option.textContent = seat === 0 ? "0（主公位）" : String(seat);
    select.appendChild(option);
  }
  if (previous && Number(previous) < count) select.value = previous;
}

/** 点将列表：把服务端下发的启用池铺成选项（`id` 是值、名称是给人看的字）。 */
function fillHeroOptions() {
  const select = $("in-my-hero");
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  for (const hero of state.heroCatalog) {
    const option = document.createElement("option");
    option.value = hero.id;
    option.textContent = hero.name;
    select.appendChild(option);
  }
  const known = state.heroCatalog.some((hero) => hero.id === previous);
  select.value = known ? previous : (state.heroCatalog[0] || {}).id || "";
}

/** 拉一次点将列表；失败只提示、不挡住开局（列表空 ⇒ 开关自然点不出东西）。 */
async function loadHeroCatalog() {
  try {
    const body = await api("/api/heroes");
    state.heroCatalog = (body && body.heroes) || [];
  } catch (error) {
    state.heroCatalog = [];
    $("lobby-hint").textContent = `点将列表拉取失败：${error.message}`;
  }
  fillHeroOptions();
  syncPointjiang();
}

/** 开关的**唯一**落点：关了 = 置灰选将列表 + 建局不带 `my_hero`（纯随机）。 */
function syncPointjiang() {
  const on = !!($("in-pointjiang") || {}).checked;
  const select = $("in-my-hero");
  if (select) select.disabled = !on;
  return on;
}

async function startGame(event) {
  event.preventDefault();
  const seedRaw = $("in-seed").value.trim();
  const timeout = Number($("in-timeout").value);
  // 点将（票 19）：开关关着 ⇒ `my_hero: null`，服务端走**纯随机**那条老路径。
  const myHero = syncPointjiang() ? $("in-my-hero").value || null : null;
  $("lobby-hint").textContent = "正在建局…";
  try {
    const body = await post("/api/sessions", {
      player_count: Number($("in-players").value),
      ai: $("in-ai").value,
      human_seat: Number($("in-seat").value),
      timeout_seconds: timeout > 0 ? timeout : null,
      seed: seedRaw === "" ? null : Number(seedRaw),
      my_hero: myHero,
    });
    state.sessionId = body.session_id;
    state.cursor = -1;
    state.logSeq = 0;
    $("t-log").innerHTML = "";
    applyView(body);
    show("table");
    $("nav-table").disabled = false;
    $("lobby-hint").textContent = "";
  } catch (error) {
    $("lobby-hint").textContent = `建局失败：${error.message}`;
  }
}

/* ------------------------------------------------ 会话视图的接收与应用 */

let playbackTimer = null;

/** 收到一份新视图：**有事件就先回放**，播完再提交快照与询问（报障 3 的严格顺序）。
 *
 * 回放期间 ``state.view`` 仍是**上一条**视图、提交按钮是灰的——用户看到的是事件一条条
 * 发生，而不是"画面直接跳到结算完"。没有事件（纯刷新）就直接提交。
 */
function applyView(body) {
  if (state.playback) flushPlayback();
  const incoming = body.records || [];
  const first = state.view === null;
  if (!incoming.length) {
    commitView(body);
    return;
  }
  const previous = state.view && state.view.snapshot ? state.view.snapshot.processing || [] : [];
  state.effectCards = mergeEffectCards(previous);
  state.playback = { body, records: incoming, index: 0, querySeat: state.lastQuerySeat };
  $("btn-submit").disabled = true;
  $("btn-cancel").disabled = true;
  $("t-choice").textContent = "";
  // 回放期间**响应文本清空**：它属于"下方询问界面"的那一问，等播完、新询问提交后再显示。
  // 描述文本不在这里清——它由 renderProcessing 按效果区逐事件改写。
  $("t-prompt").textContent = "";
  // 第一条也等满一个步长再出现：每个事件之间的间隔就是当前的步长档。
  playbackTimer = window.setTimeout(() => stepPlayback(false), state.eventDelayMs);
  // **开局**是唯一不等节奏的：此前牌桌上一片空白，逐条播开局事件只是空屏五秒。
  // 直接把记录走完（日志照收），让牌桌立刻有局面可看。
  if (first) flushPlayback();
}

/** 回放一步：画一条事件（事件流 + 效果区），然后排下一步。
 *
 * ``immediate`` = 测试 / 批量冲刷用：不排计时器，由 :func:`flushPlayback` 直接连跑。
 */
function stepPlayback(immediate) {
  const playback = state.playback;
  if (!playback) return;
  if (playback.index >= playback.records.length) {
    finishPlayback();
    return;
  }
  const record = playback.records[playback.index];
  if (record.kind === "query") {
    playback.querySeat = record.query.actor;
    state.lastQuerySeat = record.query.actor;
  }
  state.logRecords.push(record);
  appendLog(record, playback.querySeat);
  applyEffectDelta(record);
  renderProcessing();
  const logNode = $("t-log");
  if (logNode.lastChild) logNode.lastChild.scrollIntoView({ block: "nearest" });
  playback.index += 1;
  if (!immediate) playbackTimer = window.setTimeout(() => stepPlayback(false), state.eventDelayMs);
}

/** 把整批回放**立刻**跑完（测试与"收到新视图前先收尾"用）。 */
function flushPlayback() {
  if (playbackTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(playbackTimer);
    playbackTimer = null;
  }
  let guard = 0;
  while (state.playback && guard < 100000) {
    stepPlayback(true);
    guard += 1;
    if (playbackTimer !== null) {
      if (window.clearTimeout) window.clearTimeout(playbackTimer);
      playbackTimer = null;
    }
  }
}

function finishPlayback() {
  const playback = state.playback;
  if (!playback) return;
  state.playback = null;
  if (playbackTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(playbackTimer);
    playbackTimer = null;
  }
  commitView(playback.body);
}

/** 逐目标结算的 C 段时机：它们的 ``targets`` 是**当前正在结算的那个目标**。
 *
 * 不含 ``RESOLVE_AFTER_END``：那一条带的还是整张牌的目标列表，且卡牌此时已在离开处理区，
 * 拿它更新"当前目标"会把下一张牌错认成目标。
 */
const RESOLVE_TARGET_TIMINGS = new Set([
  "RESOLVE_START",
  "RESOLVE_BEFORE_EFFECT",
  "RESOLVE_EFFECT",
  "RESOLVE_AFTER_EFFECT",
  "RESOLVE_END",
]);

/** 效果区的逐事件派生：``card_move`` 进 / 出处理区时增删牌面。
 *
 * 处理区是**公开区**（`snapshot.processing`），但一次响应只给**终态**；回放期间要看见
 * 【杀】【闪】【桃】停在里面，只能顺着增量把牌挪进挪出。
 *
 * **为什么进区时只记使用者、不记目标**：卡牌在 ``USE_CHOOSE_TARGET`` 之后、
 * ``USE_DESIGNATE_TARGET`` 之前就进了处理区，那一刻这次使用的目标**还没定**；拿"上一次
 * 指定目标"去套会串味（【决斗】套到上一次【南蛮入侵】的 7 个目标，报障图 1）。
 * 目标在 :func:`applyUseTiming` 里等 ``USE_DESIGNATE_TARGET`` 到了再认领。
 */
function applyEffectDelta(record) {
  if (!record) return;
  if (record.kind === "timing") {
    applyUseTiming(record);
    return;
  }
  if (record.kind !== "delta" || record.delta_type !== "card_move") return;
  const cards = record.cards || [];
  const from = record.from_zone || {};
  const to = record.to_zone || {};
  if (from.zone === "processing") {
    const ids = new Set(cards.map((card) => card.card_id));
    state.effectCards = state.effectCards.filter((card) => !ids.has(card.card_id));
  }
  if (to.zone === "processing") {
    for (const card of cards) {
      if (state.effectCards.some((held) => held.card_id === card.card_id)) continue;
      state.effectCards.push({
        ...card,
        source: record.reason === "USE" ? record.owner : null, // 使用者随这次使用已知
        targets: null,        // 目标等 USE_DESIGNATE_TARGET 认领
        currentTarget: null,  // 逐目标结算时由 RESOLVE_* 覆盖
      });
    }
  }
}

/** 时机记录对描述文本的两个贡献：认领这次使用的目标、更新"当前结算到谁"。
 *
 * 认领只看**栈顶那张处理区牌**（刚进来的那张）且要求使用者与时机 ``actor`` 相同、
 * 目标还没认领过；认领过一次就不再改，故一次使用的目标只认领自己那一份。
 */
function applyUseTiming(record) {
  const card = state.effectCards[state.effectCards.length - 1];
  if (!card) return;
  if (record.timing === "USE_DESIGNATE_TARGET") {
    if (card.source === record.actor && (card.targets === null || card.targets === undefined)) {
      card.targets = (record.targets || []).slice();
    }
    return;
  }
  if (RESOLVE_TARGET_TIMINGS.has(record.timing) && (record.targets || []).length) {
    card.currentTarget = record.targets[0];
  }
}

/** 把新快照的处理区并进 `state.effectCards`：同一张牌保留回放期间标注的使用者/目标。 */
function mergeEffectCards(cards) {
  const previous = new Map((state.effectCards || []).map((card) => [card.card_id, card]));
  return (cards || []).map((card) => {
    const held = previous.get(card.card_id);
    return {
      ...card,
      source: held ? held.source : null,
      targets: held ? held.targets : null,
      currentTarget: held ? held.currentTarget : null,
    };
  });
}

/** 效果区（处理区）的说明文本：有牌才有一行，牌全走了就**立即清空**（用户裁定）。 */
function describeEffect(cards) {
  if (!cards || !cards.length) return "";
  const parts = cards.map((card) => {
    const who = (card.source === null || card.source === undefined)
      ? ""
      : `（${useText(card)}）`;
    return `【${card.name}】${who}`;
  });
  return `处理中：${parts.join("、")}`;
}

/** 处理区里某张牌的"谁对谁"：逐目标结算时写**当前**目标；否则写全部（去重使用者本人）。 */
function useText(card) {
  const source = card.source;
  const current = card.currentTarget;
  if (current !== null && current !== undefined && current !== source) {
    return `${source}号位 对 ${current}号位 使用`;
  }
  const others = (card.targets || []).filter((seat) => seat !== source);
  return others.length
    ? `${source}号位 对 ${others.map((seat) => `${seat}号位`).join("、")} 使用`
    : `${source}号位 使用`;
}

/** 提交这份视图：装快照 / 游标，重画牌桌与询问（回放播完后的落点）。 */
function commitView(body) {
  state.view = body;
  state.cursor = body.cursor;
  state.logSeq = body.log_seq;
  $("t-seed").textContent = String(body.seed);
  $("t-seat").textContent = String(body.human_seat);
  $("btn-takeover").hidden = body.finished;
  // `state.effectCards` 不在这里重设：`renderTable` 会把快照的处理区并进来
  // （`mergeEffectCards`），从而保留回放期间标注的"谁在用"。
  // 新询问到达 ⇒ 未提交的选择一律作废（spec D14：不弹确认、不尝试提交）。
  resetSelection();
  renderTable();
  const logNode = $("t-log");
  if (logNode.lastChild) logNode.lastChild.scrollIntoView({ block: "nearest" });
  if (body.finished) {
    state.timer = null;
    $("t-timer").textContent = "";
    $("t-prompt").textContent = "本局已结束。";
    $("btn-submit").disabled = true;
    $("btn-cancel").disabled = true;
    $("t-options").innerHTML = "";
    $("t-target-hint").textContent = "";
    $("status").textContent = "已结束（录像已存）";
    loadRuns();
    return;
  }
  renderPending(body);
}

/** 清空全部未提交的选择（焦点 / 已选目标 / 旧路径的候选与槽位 / 主动技手选模式）。 */
function resetSelection() {
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.picks = [];
  state.skillMode = null;
  state.skillSeat = null;
  state.preativation = null;
  state.convertSkill = null;
  state.lastRejectReason = "";
}

async function refresh() {
  if (!state.sessionId || state.playback) return;
  try {
    const body = await api(`/api/sessions/${state.sessionId}?after=${state.cursor}&logs=${state.logSeq}`);
    applyView(body);
  } catch (error) {
    $("status").textContent = `取增量失败：${error.message}`;
  }
}

async function poll() {
  if (!state.sessionId) return;
  await refresh();
  window.setTimeout(poll, 1200);
}

/* ------------------------------------------------------------ 候选索引 */

/** 这次询问的候选索引：**只读服务端下发的那一份**（`pending.candidate_index`）。
 *
 * 前端**不自己从 `legal_actions` 重建索引**——那是一份会漂移的第二实现，
 * 而"池 ⊆ 候选、候选 ⊆ 索引"这条不变量已经由 `tests/test_web.py` 钉在服务端那一侧。
 * 空索引是合法状态（`Pass` / `SelectOption` 这类没有"牌 → 目标"的询问就是空的）。
 */
function cardIndex(pending) {
  return (pending && pending.candidate_index) || {};
}

/** 这张**实体手牌**能转化出的虚拟牌候选（服务端 `pending.virtual_index`）。
 *
 * 转化技（【龙胆】）的候选里带的 ``card`` 是虚拟牌（``card_id < 0``），而手牌区渲染的是
 * 实体牌；服务端按"实体来源 id → [{card_id, skill_ids}]"下发了别名索引，界面只读它。
 */
function cardAliases(pending, cardId) {
  const map = (pending && pending.virtual_index) || {};
  return map[String(cardId)] || [];
}

/** 这张牌此刻的候选条目：先看它自己，再看**转化别名**（单来源、必要时限定当前技能）。
 *
 * 返回的 ``entry.card`` 可能是虚拟牌——它才是要提交的那张；焦点仍是玩家点的**实体牌**，
 * 故 :func:`buildAction` 按 ``entry.card.card_id`` 找候选，而不是按焦点 id。
 */
function cardEntry(pending, cardId) {
  const direct = cardIndex(pending)[String(cardId)] || null;
  if (direct) return direct;
  let aliases = cardAliases(pending, cardId);
  if (state.convertSkill !== null) {
    aliases = aliases.filter((alias) => (alias.skill_ids || []).includes(state.convertSkill));
  }
  if (aliases.length !== 1) return null;
  return cardIndex(pending)[String(aliases[0].card_id)] || null;
}

/** 这张牌此刻点不点得动：候选索引里有它才有候选（唯一判据，不读规则）。 */
function cardSelectable(pending, cardId) {
  return cardEntry(pending, cardId) !== null;
}

function sameCombo(a, b) {
  if (a.length !== b.length) return false;
  const left = a.slice().sort((x, y) => x - y);
  const right = b.slice().sort((x, y) => x - y);
  return left.every((v, i) => v === right[i]);
}

/** 组合是否恰好是候选索引里的一组（**唯一的合法性判据**）。 */
function isLegalCombo(pending, cardId, targets) {
  const entry = cardEntry(pending, cardId);
  if (!entry) return false;
  return entry.combos.some((combo) => sameCombo(combo, targets || []));
}

/** **自动选中**：候选组合恰好只有一组、且那组就是池的全部 ⇒ 返回那一组；否则 ``null``。
 *
 * 判据只读候选索引，不引用任何规则知识。反例：借刀杀人的被迫【杀】是"池 4、目标数 1、
 * 组合 4 条"——此时**必须手选**，否则会把 4 个座位全自动选中。
 */
function autoCombo(pending, cardId) {
  const entry = cardEntry(pending, cardId);
  if (!entry || entry.combos.length !== 1) return null;
  const combo = entry.combos[0];
  const pool = entry.pool.slice().sort((x, y) => x - y);
  const sorted = combo.slice().sort((x, y) => x - y);
  if (pool.length !== sorted.length) return null;
  if (!pool.every((seat, i) => seat === sorted[i])) return null;
  // 空组合（无目标使用：【闪】【无懈】）**不算自动选中**——没有目标可填，
  // 返回 `[]` 会让"已自动选中"这句话为假（`[]` 是 truthy，调用方会当成填好了）。
  return combo.length === 0 ? null : combo.slice();
}

function isHandPick(pending) {
  return Boolean(pending) && HAND_PICK_KINDS.includes(pending.kind);
}

/** 弃牌阶段：点手牌 = 收进 / 移出待弃置集合（`picks` 装 **card_id**，与 `DiscardCards.cards` 同键）。 */
function isDiscardPick(pending) {
  return Boolean(pending) && pending.kind === "DISCARD";
}

/** 出牌阶段 / 响应窗口下"哪些座位此刻可点"（**数据**给的，不是 CSS 猜的）。
 *
 * 返回**池里的每一个座位**并标出它此刻是什么状态，而不是只返回"可点的那些"——
 * 已选目标必须留在里面，否则它就没法被再点一次取消（点击路径只在"池里"的座位上生效）。
 */
function pickerSeats(pending) {
  if (!isHandPick(pending)) return [];
  if (state.focusCardId === null) return [];
  const entry = cardEntry(pending, state.focusCardId);
  if (!entry) return [];
  if (entry.max_targets === 0) return [];   // 无目标使用（【闪】【无懈】）：没有座位可点
  const selected = state.selectedTargets;
  const candidates = entry.combos.filter((combo) => selected.every((seat) => combo.includes(seat)));
  return entry.pool.map((seat) => ({
    seat,
    selected: selected.includes(seat),
    isTargetable: candidates.some((combo) => combo.includes(seat) && !selected.includes(seat)),
  }));
}

/** 目标区的一句人话（不推荐、不排序，只说"还差什么"）。 */
function pickerTargetHint(pending) {
  if (!isHandPick(pending)) return "";
  if (state.focusCardId === null) return "点一张手牌 = 选牌；点座位 = 选目标。";
  const entry = cardEntry(pending, state.focusCardId);
  if (!entry) return "";
  if (entry.max_targets === 0) return "本窗口无需指定目标。";
  if (autoCombo(pending, state.focusCardId)) return "目标由规则唯一确定，已自动选中（点牌可整张取消）。";
  const have = state.selectedTargets.length;
  if (isLegalCombo(pending, state.focusCardId, state.selectedTargets)) return "目标已选好，可以提交。";
  const rest = entry.combos.filter((combo) => state.selectedTargets.every((seat) => combo.includes(seat)));
  if (!rest.length) return "这组目标不是合法组合，点座位取消再选。";
  const need = Math.max(...rest.map((combo) => combo.length)) - have;
  return need > 0 ? `还要选 ${need} 个目标（已选 ${have}）。` : "这组目标不是合法组合，点座位取消再选。";
}

/** 目标座位是否**就是自己或空集**：这类牌（【无中生有】【桃】、装备牌）不画座位高亮。 */
function targetsSelfOnly(entry, selfSeat) {
  if (!entry) return false;
  return entry.pool.length === 1 && entry.pool[0] === selfSeat;
}

/* ------------------------------------------------- 提供一张 B（票 14） */

/** 这一步是不是**"提供一张 B"**（票 14）：``provide_type`` 非空 = B 的牌名 id。
 *
 * 服务端只在**成本 / 续用 / 被问使用或打出**这类"提供一张 B"的询问上下发它
 * （`flow.PickProvide`）。这种面**就地**渲染：候选牌在本人手牌 / 装备区 / 判定区
 * 直接亮起，不新开选择区（用户裁定 7）。
 */
function isProvide(pending) {
  return Boolean(pending) && pending.kind === "SELECT_CARDS" && Boolean(pending.provide_type);
}

/** "提供一张 B"面上这张牌对应的**槽位序号**（票 14）；``-1`` = 这张牌不在候选面里。
 *
 * 面上位置（不是牌 id）才是提交要的答案：虚拟 B 背后没有实体牌，服务端的 ``picks``
 * 是面内下标。两类命中：
 *
 * 1. **实体 B**：槽上的牌面就是这张牌（手牌 / 装备 / 判定区里的【杀】）；
 * 2. **转化来源**：服务端 ``virtual_index`` 按来源实体牌给出面内那张虚拟 B 的 id
 *    （【龙胆】的【闪】/【武圣】的红牌）。预发动（点了技能按钮）后只认那条技能的方向。
 */
function provideSlotIndex(pending, cardId) {
  if (!isProvide(pending)) return -1;
  const slots = pending.slots || [];
  for (let index = 0; index < slots.length; index += 1) {
    const face = slots[index].card;
    if (face && String(face.card_id) === String(cardId)) return index;
  }
  let aliases = cardAliases(pending, cardId);
  if (state.convertSkill !== null) {
    aliases = aliases.filter((alias) => (alias.skill_ids || []).includes(state.convertSkill));
  }
  for (const alias of aliases) {
    const index = slots.findIndex(
      (slot) => slot.card && String(slot.card.card_id) === String(alias.card_id),
    );
    if (index >= 0) return index;
  }
  return -1;
}

/** 这张牌此刻的 ``{selectable, selected, slotIndex}``——**唯一**的置灰派生（票 14 §D）。
 *
 * 输入只有"当前询问 + 这张牌"：没有一处缓存上一问的置灰，:func:`renderTable` 每次
 * 整体重算，`state.picks` 又在新询问到达时由 :func:`resetSelection` 清空 ⇒
 * **进询问 / 换询问 / 退出询问**三个时点各自只看当前状态，不叠加、不粘滞（用户逐字要求）。
 * 无询问（退出询问）按"候选索引里有没有它"退回可不用/打出，而不是保留上一问的亮面。
 */
function cardFace(pending, card) {
  const slotIndex = provideSlotIndex(pending, card.card_id);
  if (slotIndex >= 0) {
    const total = (pending.spec && pending.spec.total) || [1, 1];
    const selected = state.picks.includes(slotIndex);
    return { selectable: selected || state.picks.length < total[1], selected, slotIndex };
  }
  if (isDiscardPick(pending)) {
    const selected = state.picks.includes(card.card_id);
    return {
      selectable: selected || state.picks.length < (pending.count || 0),
      selected,
      slotIndex: -1,
    };
  }
  if (state.skillMode) {
    return {
      selectable: state.skillMode.cards.includes(card.card_id),
      selected: state.picks.includes(card.card_id),
      slotIndex: -1,
    };
  }
  if (state.preativation) {
    const options = preactivationCardOptions(pending);
    const answer = state.preativation.answers[state.preativation.cursor];
    return {
      selectable: options === undefined ? false : (options === null || options.includes(String(card.card_id))),
      selected: Boolean(answer && answer.values.includes(card.card_id)),
      slotIndex: -1,
    };
  }
  const selectable = pending
    ? (isHandPick(pending) ? cardSelectable(pending, card.card_id) : cardUsable(pending, card.card_id))
    : false;
  return { selectable, selected: state.focusCardId === card.card_id, slotIndex: -1 };
}

/** 点一张牌：按 :func:`cardFace` 给出的**当前模式**分派（提供 B / 弃牌 / 技能手选 / 设焦点）。 */
function activateCard(pending, card, face) {
  if (state.playback) return;
  if (isProvide(pending)) {
    if (face.slotIndex >= 0) toggleProvidePick(face.slotIndex);
    return;
  }
  if (isDiscardPick(pending)) { toggleDiscardPick(card.card_id); return; }
  if (state.skillMode) { toggleSkillCard(card.card_id); return; }
  if (state.preativation) { togglePreativationCard(card.card_id); return; }
  toggleFocus(card.card_id);
}

/* ------------------------------------------------------------------ 牌桌 */

function renderTable() {
  const view = state.view;
  if (!view) return;
  const snapshot = view.snapshot;
  $("t-phase").textContent = phaseLabel(snapshot.phase);
  const turnSeat = snapshot.turn_seat;
  $("t-turn").textContent = turnSeat === null || turnSeat === undefined
    ? "—"
    : `座位 ${turnSeat}${turnSeat === snapshot.seat ? "（我）" : ""}`;
  // 牌堆 / 弃牌堆**数量**（服务端 `PlayerView` 只给牌堆数量、`discard` 按置入序给牌面，
  // 都是公开事实；这里只画数量，不泄露牌堆顺序）。
  $("t-deck").textContent = String(snapshot.deck_count ?? 0);
  $("t-discard").textContent = String((snapshot.discard || []).length);

  const pendingNow = view.pending || null;
  const handPick = isHandPick(pendingNow);
  const seats = pickerSeats(pendingNow);
  const seatOf = (seat) => seats.find((item) => item.seat === seat) || null;
  const focusEntry = state.focusCardId === null ? null : cardEntry(pendingNow, state.focusCardId);
  // 池就是"自己"一个座位（【无中生有】【桃】、装备牌）：不画座位高亮，免得看着像要打自己
  const selfOnly = targetsSelfOnly(focusEntry, snapshot.seat);
  // `SELECT_OPTION` 的"选座位"形态（【遗计】的"交给谁"，服务端下发 `option_seats`）：
  // 座位是**可点的**——点它就等于选中那条 `seat:N` 候选。选项串的解析在服务端。
  const optionSeats = (pendingNow && pendingNow.option_seats) || {};
  const optionChoiceAt = (seat) =>
    Object.keys(optionSeats).find((choice) => Number(optionSeats[choice]) === Number(seat)) || null;
  // 预发动（W4-1）：当前的选目标步 ⇒ 可点的座位来自**清单**（服务端已按前序选择算好）。
  const preStep = state.preativation && state.preativation.cursor < state.preativation.steps.length
    ? state.preativation.steps[state.preativation.cursor]
    : null;
  const preSeats = preStep && preactivationStepKind(preStep) === "seat"
    ? (preactivationOptions(state.preativation, state.preativation.cursor)
      || snapshot.players.filter((player) => player.alive).map((player) => `seat:${player.seat}`))
    : null;

  const ring = $("t-seats");
  ring.innerHTML = "";
  for (const player of snapshot.players) {
    const box = document.createElement("div");
    box.className = "seat";
    if (player.seat === snapshot.seat) box.classList.add("me");
    if (!player.alive) box.classList.add("dead");
    if (player.seat === snapshot.turn_seat) box.classList.add("turn");
    const seatState = seatOf(player.seat);
    const optionChoice = optionChoiceAt(player.seat);
    const optionSelected = optionChoice !== null
      && state.selectedOption !== null
      && (pendingNow.legal_actions[state.selectedOption] || {}).choice === optionChoice;
    if (state.preativation) {
      if (preSeats && preSeats.includes(`seat:${player.seat}`)) {
        const answer = state.preativation.answers[state.preativation.cursor];
        if (answer && answer.values.includes(player.seat)) box.classList.add("tsel");
        else box.classList.add("targetable");
      }
    } else if (state.skillMode) {
      // 主动技手选（【仁德】）：候选座位由技能清单下发；点它就是"把选好的牌交给他"。
      if (state.skillMode.seats.includes(player.seat)) {
        if (state.skillSeat === player.seat) box.classList.add("tsel");
        else box.classList.add("targetable");
      }
    } else if (handPick && !selfOnly) {
      if (seatState && seatState.selected) box.classList.add("tsel");
      else if (seatState && seatState.isTargetable) box.classList.add("targetable");
    } else if (optionChoice !== null) {
      // 选座位形态（【遗计】"交给谁"）：座位可点，选中的那座画成确定态。
      if (optionSelected) box.classList.add("tsel");
      else box.classList.add("targetable");
    }
    const hero = view.heroes[player.seat] || "（无武将）";
    const identity = player.identity ? ` · ${identityLabel(player.identity)}` : "";
    box.setAttribute("data-seat", String(player.seat));
    box.innerHTML = `
      <div class="top">
        <span class="who">座位 ${player.seat}${player.seat === snapshot.seat ? "（我）" : ""}</span>
        <span class="hp">${"●".repeat(Math.max(0, player.hp))}${"○".repeat(Math.max(0, player.hp_max - Math.max(0, player.hp)))}</span>
      </div>
      <div class="small">${hero}${identity}</div>
      <div class="small">手牌 ${player.hand_count}　装备 ${player.equip.length}　判定 ${player.judge.length}</div>
      <div class="small">${player.alive ? "" : "已阵亡"}</div>`;
    // 装备区 / 判定区是**公开信息且逐座位**（`PlayerPublic.equip/judge` 早就下发了）：
    // 只写数量会让"谁的判定区里有乐不思蜀"看不见，进而把别人的判定误读成自己的。
    for (const [zone, cards] of [["equip", player.equip || []], ["judge", player.judge || []]]) {
      if (!cards.length) continue;
      const row = document.createElement("div");
      row.className = "cards mini";
      row.setAttribute("data-zone", zone);
      row.setAttribute("data-seat", String(player.seat));
      for (const card of cards) {
        // 「提供一张 B」面（票 14）：**本人自己**可见区域里的候选牌**就地**亮起、可点
        // （【武圣】的来源可以是装备 / 判定区的红牌）；别人的区与其余窗口保持静态不可点。
        // 预发动（W4-1）的选牌步同样在本人的装备区就地高亮（【制衡】/【贯石】的代价牌）。
        const ownInteractive = player.seat === snapshot.seat
          && (isProvide(pendingNow) || state.preativation);
        if (!ownInteractive) {
          row.appendChild(cardChip(card, false, true));
          continue;
        }
        const face = cardFace(pendingNow, card);
        row.appendChild(
          cardChip(card, face.selected, false, face.selectable, () => activateCard(pendingNow, card, face)),
        );
      }
      box.appendChild(row);
    }
    if (state.preativation) {
      if (preSeats && preSeats.includes(`seat:${player.seat}`)) {
        box.addEventListener("click", () => selectPreativationSeat(player.seat));
      }
    } else if (state.skillMode) {
      if (state.skillMode.seats.includes(player.seat)) {
        box.addEventListener("click", () => selectSkillSeat(player.seat));
      }
    } else if (seatState && !selfOnly) {
      if (seatState.selected || seatState.isTargetable) {
        box.addEventListener("click", () => selectTarget(player.seat));
      }
    } else if (optionChoiceAt(player.seat)) {
      // 选座位形态：只在座位上点，不在手牌上点（本窗口没有"选牌"这一步）。
      box.addEventListener("click", () => selectOptionSeat(player.seat));
    }
    ring.appendChild(box);
  }

  // 快照到达时把处理区并进 `state.effectCards`（同一张牌保留回放期间的使用者标注）；
  // 回放期间不并——那份是逐事件派生的，快照要等播完才提交。
  if (!state.playback) state.effectCards = mergeEffectCards(snapshot.processing || []);
  renderProcessing();

  $("t-hand-count").textContent = `${snapshot.self_hand.length} 张`;
  const hand = $("t-hand");
  hand.innerHTML = "";
  for (const card of snapshot.self_hand) {
    // 置灰 / 选中**只**由 :func:`cardFace` 从"当前询问 + 这张牌"算出（票 14 §D）：
    // 弃牌、技能手选、提供 B、普通手选四条路都在那一个函数里分流，这里不再各写一份。
    const face = cardFace(pendingNow, card);
    hand.appendChild(
      cardChip(card, face.selected, false, face.selectable, () => activateCard(pendingNow, card, face)),
    );
  }
  $("t-hero").textContent = view.heroes[snapshot.seat] || "（无武将）";
  renderSkills(view);
}

/** 效果区 / 处理区：画 `state.effectCards`（回放期间逐事件派生、快照到达时并进来），
 * 并把**描述文本**（`#t-context`）同步成它的说明——两者同源，故不会一个变一个不变。
 *
 * 描述文本曾经是服务端下发的"本窗口：谁对谁使用…"，只在询问到达时更新，回放期间会残留；
 * 现在它跟着效果区走：有牌写「处理中：【牌】（谁在使用）」，牌全走了**立即清空**。 */
function renderProcessing() {
  const box = $("t-processing");
  if (!box) return;
  const cards = state.effectCards || [];
  box.innerHTML = "";
  if (!cards.length) {
    const empty = document.createElement("span");
    empty.className = "dim";
    empty.textContent = "（空）";
    box.appendChild(empty);
  } else {
    for (const card of cards) box.appendChild(cardChip(card, false, true));
  }
  const contextNode = $("t-context");
  if (contextNode) contextNode.textContent = describeEffect(cards);
}

/** 技能按钮：三态由**数据**决定（`locked` 表类型、`available` 表此刻能不能发动）。
 *
 * 纪律：不发动的技能**不给点**——它是"引擎在时机上自动派发/弹询问"的东西，
 * 界面上点它没有意义。能用时点了就等于选它那条 `ActivateSkill` 候选。
 */
function renderSkills(view) {
  const box = $("t-skills");
  if (!box) return;
  box.innerHTML = "";
  const skills = view.skills || [];
  if (!skills.length) {
    box.innerHTML = '<span class="dim">（无武将，或本局未发放武将）</span>';
    return;
  }
  const pending = view.pending;
  for (const skill of skills) {
    const ui = skill.ui || {};
    const convertCards = ui.convert_sources || [];
    const manifest = preactivationManifest(pending, skill.skill_id);
    const skeleton = Boolean(pending) && (pending.legal_actions || []).some(
      (action) => action.kind === "ActivateSkill" && action.skill_id === skill.skill_id,
    );
    const usable = Boolean(skill.available) || convertCards.length > 0 || Boolean(manifest);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "skill";
    if (skill.locked) button.classList.add("locked");
    if (usable) button.classList.add("ready");
    button.disabled = !usable;
    button.setAttribute("data-skill-id", skill.skill_id);
    button.setAttribute("data-available", String(skill.available));
    button.setAttribute("data-usable", String(usable));
    button.setAttribute("data-locked", String(skill.locked));
    if (ui.manual) button.setAttribute("data-manual", "true");
    if (convertCards.length) button.setAttribute("data-convert", String(convertCards.length));
    if (manifest) button.setAttribute("data-preativation", String(manifest.length));
    const kind = skill.carrier === "EQUIPMENT"
      ? (skill.locked ? "装备技 / 锁定" : "装备技")
      : (skill.locked ? "锁定技 / 被动" : (skill.skill_type === "LORD" ? "主公技" : "主动技"));
    const badge = manifest && !skeleton
      ? " · 预发动"
      : (ui.manual ? " · 点它选牌" : (convertCards.length ? " · 点它转化" : (skill.available ? " · 可发动" : "")));
    button.innerHTML = `${skill.name}<span class="kind">${kind}${badge}</span>`;
    if (state.playback) {
      // 回放期间不接任何操作（严格顺序：这批事件播完才轮到下一次询问）。
    } else if (usable && pending && manifest && !skeleton) {
      // 已翻到预发动（骨架已删）：点它进入**清单驱动**的本地态；取消 = 什么都不提交。
      button.addEventListener("click", () => enterPreativation(skill, manifest));
    } else if (usable && pending && ui.manual) {
      // 主动技手选（【仁德】）：点它进入"多选手牌 → 点场上武将"的模式，不直接提交候选。
      button.addEventListener("click", () => enterSkillMode(skill));
    } else if (usable && pending && convertCards.length) {
      // 转化技（【龙胆】）：点它进入"挑一张来源牌"的模式（再点一次退出）。
      button.addEventListener("click", () => toggleConvertSkill(skill));
    } else if (skill.available && pending) {
      const index = pending.legal_actions.findIndex((a) => a.kind === "ActivateSkill" && a.skill_id === skill.skill_id);
      if (index >= 0) {
        button.addEventListener("click", () => {
          state.selectedOption = index;
          state.focusCardId = null;
          state.selectedTargets = [];
          state.lastRejectReason = "";
          renderTable();
          renderPending(state.view);
        });
      }
    }
    box.appendChild(button);
  }
}

/** 进入主动技手选模式（【仁德】：候选带 picks + 唯一目标）。 */
function enterSkillMode(skill) {
  const ui = skill.ui || {};
  state.skillMode = {
    skillId: skill.skill_id,
    name: skill.name,
    requirement: ui.requirement || "",
    cards: (ui.pick_cards || []).slice(),
    seats: (ui.pick_seats || []).slice(),
  };
  state.skillSeat = null;
  state.preativation = null;
  state.picks = [];
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.convertSkill = null;
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 点手牌：收进 / 移出"要交出去的牌"。 */
function toggleSkillCard(cardId) {
  if (state.playback || !state.skillMode) return;
  if (!state.skillMode.cards.includes(cardId)) return;
  const at = state.picks.indexOf(cardId);
  if (at >= 0) state.picks.splice(at, 1);
  else state.picks.push(cardId);
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 点场上武将：把选好的牌交给他 —— 组合在候选里就直接提交（用户要的"点座位即交出去"）。 */
function selectSkillSeat(seat) {
  if (state.playback || !state.skillMode) return;
  if (!state.skillMode.seats.includes(seat)) return;
  state.skillSeat = seat;
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
  if (buildAction() !== null) submitAction();
}

/** 进入 / 退出转化技模式（【龙胆】：点技能 → 点一张可转化的手牌 → 照常选目标提交）。 */
function toggleConvertSkill(skill) {
  if (state.playback) return;
  state.convertSkill = state.convertSkill === skill.skill_id ? null : skill.skill_id;
  state.skillMode = null;
  state.skillSeat = null;
  state.preativation = null;
  state.picks = [];
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/* ------------------------------------------------------------ 预发动（W4-1） */

/** 一条技能此刻的预发动清单：主动入口 = `pending.preactivations[skill_id]`，
 * 被动入口 = `pending.preativation`。两者都由服务端按 `SkillFlow.steps` 渲染。 */
function preactivationManifest(pending, skillId) {
  if (!pending) return null;
  const table = pending.preactivations || {};
  if (table[skillId] && table[skillId].length) return table[skillId];
  if (
    pending.kind === "SKILL_ACTIVATION"
    && pending.skill_id === skillId
    && pending.preativation
    && pending.preativation.length
  ) return pending.preativation;
  return null;
}

/** 这一步选什么：`card` = 区域里的牌；`seat` = `seat:N` 闭集；`option` = 其余闭集文本。 */
function preactivationStepKind(step) {
  if (step.zones && step.zones.length) return "card";
  // 服务端退化掉候选索引时（`pool=()`）只能按"非选牌步的默认真实形态"判：座位步。
  // 选项步的候选是常量、不会退化，故这条 fallback 不会把选项步误判成座位。
  if (!step.pool || !step.pool.length) return "seat";
  const first = (step.pool[0].options || [])[0];
  if (typeof first === "string" && first.startsWith("seat:")) return "seat";
  return "option";
}

/** 一步的候选：先找**精确匹配前序答案**的那一行，其次找 `when=()` 的（与前序无关）。
 *  `null` = 服务端退化掉了候选索引（或本步没有池）——界面退化为"全都点得动"，
 *  合法性仍由服务端校验器裁决（`04` §4.1：前端不裁决规则）。 */
function preactivationOptions(pa, index) {
  const step = pa && pa.steps[index];
  if (!step || !step.pool || !step.pool.length) return null;
  const prior = [];
  for (let i = 0; i < index; i += 1) prior.push(...(pa.answers[i] ? pa.answers[i].texts : []));
  const exact = step.pool.find(
    (row) => row.when.length === prior.length && row.when.every((value, at) => value === prior[at]),
  );
  const any = step.pool.find((row) => row.when.length === 0);
  const row = exact || any || null;
  return row ? (row.options || []) : null;
}

function preactivationAnswerTexts(step, values) {
  const kind = preactivationStepKind(step);
  if (kind === "seat") return values.map((seat) => `seat:${seat}`);
  return values.map((value) => String(value));
}

/** 进入预发动：**不压帧、不计次数**，只把清单与本地答案摊开（W4-1）。 */
function enterPreativation(skill, manifest) {
  state.preativation = {
    skillId: skill.skill_id,
    name: skill.name,
    steps: manifest,
    answers: manifest.map(() => ({ values: [], texts: [] })),
    cursor: 0,
  };
  state.skillMode = null;
  state.skillSeat = null;
  state.convertSkill = null;
  state.picks = [];
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 当前这一步是不是选牌步；是则返回它的候选 card_id 集合（`null` = 退化，全都可点）。 */
function preactivationCardOptions(pending) {
  const pa = state.preativation;
  if (!pa || pa.cursor >= pa.steps.length) return undefined;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "card") return undefined;
  return preactivationOptions(pa, pa.cursor);
}

/** 点一张牌：多选步里点已选 = 取消该张、点新牌 = 追加（用户裁定的重选规则）。 */
function togglePreativationCard(cardId) {
  const pending = state.view ? state.view.pending : null;
  const pa = state.preativation;
  if (state.playback || !pa || pa.cursor >= pa.steps.length) return;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "card") return;
  const options = preactivationOptions(pa, pa.cursor);
  if (options && !options.includes(String(cardId))) return;
  const answer = pa.answers[pa.cursor];
  const at = answer.values.indexOf(cardId);
  if (at >= 0) answer.values.splice(at, 1);
  else if (step.max === null || step.max === undefined || answer.values.length < step.max) {
    answer.values.push(cardId);
  }
  answer.texts = preactivationAnswerTexts(step, answer.values);
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 选牌步的"下一步"：张数落在 `(min, max)` 内才放行。 */
function advancePreativation() {
  const pa = state.preativation;
  if (state.playback || !pa || pa.cursor >= pa.steps.length) return;
  const step = pa.steps[pa.cursor];
  const count = pa.answers[pa.cursor].values.length;
  if (count < step.min) return;
  if (step.max !== null && step.max !== undefined && count > step.max) return;
  pa.cursor += 1;
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 点场上武将：选座位步的答案；选完自动进下一步。 */
function selectPreativationSeat(seat) {
  const pa = state.preativation;
  if (state.playback || !pa || pa.cursor >= pa.steps.length) return;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "seat") return;
  const options = preactivationOptions(pa, pa.cursor);
  const text = `seat:${seat}`;
  if (options && !options.includes(text)) return;
  pa.answers[pa.cursor] = { values: [seat], texts: [text] };
  pa.cursor += 1;
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 点选项步的一项；选完自动进下一步。 */
function selectPreativationOption(text) {
  const pa = state.preativation;
  if (state.playback || !pa || pa.cursor >= pa.steps.length) return;
  const step = pa.steps[pa.cursor];
  if (preactivationStepKind(step) !== "option") return;
  const options = preactivationOptions(pa, pa.cursor);
  if (options && !options.includes(text)) return;
  pa.answers[pa.cursor] = { values: [text], texts: [text] };
  pa.cursor += 1;
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 重选第 K 步：清空 K..N（用户裁定），把光标拨回去。 */
function jumpPreativationStep(index) {
  const pa = state.preativation;
  if (state.playback || !pa) return;
  for (let at = index; at < pa.steps.length; at += 1) {
    pa.answers[at] = { values: [], texts: [] };
  }
  pa.cursor = Math.max(0, Math.min(index, pa.steps.length));
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 清单是否已按每一步的 `(min, max)` 填完。 */
function preactivationReady(pa) {
  if (!pa || pa.cursor < pa.steps.length) return false;
  return pa.steps.every((step, index) => {
    const answer = pa.answers[index];
    const count = answer ? answer.values.length : 0;
    if (count < step.min) return false;
    return step.max === null || step.max === undefined || count <= step.max;
  });
}

/** 预发动的提交载荷：**复合动作**（不要求出现在 `legal_actions` 里，由服务端校验器验）。 */
function buildPreativationAction(pending, pa) {
  if (!pending || !pa || !preactivationReady(pa)) return null;
  const picks = [];
  const choices = [];
  pa.steps.forEach((step, index) => {
    const answer = pa.answers[index];
    if (preactivationStepKind(step) === "card") picks.push(...answer.values);
    else choices.push(...answer.texts);
  });
  return {
    kind: "ActivateSkill",
    skill_id: pa.skillId,
    picks,
    choices,
    answers_seq: pending.seq,
  };
}

function preactivationStepLabel(step) {
  const kind = preactivationStepKind(step);
  if (kind === "card") return "选牌";
  if (kind === "seat") return "选目标";
  return "选一项";
}

function preactivationStepHint(pa, pending) {
  const step = pa.steps[pa.cursor];
  if (!step) return "清单已填完，可以确认发动。";
  const options = preactivationOptions(pa, pa.cursor);
  if (preactivationStepKind(step) === "card") {
    const count = pa.answers[pa.cursor].values.length;
    return `第 ${pa.cursor + 1}/${pa.steps.length} 步：选牌（${step.min}–${step.max} 张，已选 ${count} 张）。`;
  }
  if (preactivationStepKind(step) === "seat") {
    return `第 ${pa.cursor + 1}/${pa.steps.length} 步：点场上武将选目标。`;
  }
  return `第 ${pa.cursor + 1}/${pa.steps.length} 步：选一项。`;
}

/** 清单驱动的预发动界面（W4-1）：步骤条 + 当前的选牌 / 选目标 / 选项。 */
function renderPreativation(pending) {
  const pa = state.preativation;
  const box = $("t-options");
  box.innerHTML = "";
  const hint = $("t-target-hint");
  if (hint) hint.textContent = preactivationStepHint(pa, pending);
  const row = document.createElement("div");
  row.className = "cards";
  pa.steps.forEach((step, index) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "opt";
    if (index === pa.cursor) chip.classList.add("sel");
    const done = pa.answers[index].values.length >= step.min;
    chip.textContent = `${index + 1}. ${preactivationStepLabel(step)}${done ? " ✓" : ""}`;
    chip.setAttribute("data-step", String(index));
    chip.addEventListener("click", () => jumpPreativationStep(index));
    row.appendChild(chip);
  });
  box.appendChild(row);
  if (pa.cursor >= pa.steps.length) {
    $("btn-cancel").disabled = false;
    return;
  }
  const step = pa.steps[pa.cursor];
  const kind = preactivationStepKind(step);
  if (kind === "option") {
    const options = preactivationOptions(pa, pa.cursor) || [];
    for (const option of options) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "opt";
      button.textContent = String(option);
      button.addEventListener("click", () => selectPreativationOption(option));
      box.appendChild(button);
    }
  } else if (kind === "card") {
    const next = document.createElement("button");
    next.type = "button";
    next.className = "opt";
    next.textContent = "下一步";
    const count = pa.answers[pa.cursor].values.length;
    next.disabled = count < step.min || (step.max !== null && step.max !== undefined && count > step.max);
    next.addEventListener("click", advancePreativation);
    box.appendChild(next);
  }
  $("btn-cancel").disabled = false;
}


function identityLabel(identity) {
  return { lord: "主公", loyalist: "忠臣", rebel: "反贼", traitor: "内奸" }[identity] || identity;
}

function cardChip(card, selected, isStatic, usable = true, onClick = null) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "card";
  if (selected) chip.classList.add("sel");
  if (isStatic) chip.classList.add("static");
  if (!isStatic && !usable) chip.classList.add("dis");
  chip.innerHTML = `<span class="n">${card.name}</span><span class="s">${card.suit || ""}${card.rank ?? ""}</span>`;
  // 静态牌（装备 / 判定 / 处理区）不可点；其余默认走"点牌设焦点"，弃牌阶段另给入口。
  if (!isStatic) chip.addEventListener("click", onClick || (() => toggleFocus(card.card_id)));
  return chip;
}

/** 弃牌阶段：点手牌 = 收进 / 移出待弃置集合；收够 `pending.count` 张后不再多收。 */
function toggleDiscardPick(cardId) {
  const pending = state.view ? state.view.pending : null;
  if (state.playback || !isDiscardPick(pending)) return;
  const at = state.picks.indexOf(cardId);
  if (at >= 0) state.picks.splice(at, 1);
  else if (state.picks.length < (pending.count || 0)) state.picks.push(cardId);
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 点座位 = 选 / 取消目标。**自动选中**的目标是确定态，点单个座位不取消它（spec D6）。 */
function selectTarget(seat) {
  const pending = state.view ? state.view.pending : null;
  if (state.playback || !isHandPick(pending) || state.focusCardId === null) return;
  const here = pickerSeats(pending).find((item) => item.seat === seat);
  // 只有"池里的座位"能点：池外的点了没反应（置灰）；已选的座位**始终**能再点一次取消。
  if (!here || (!here.selected && !here.isTargetable)) return;
  if (here.selected && autoCombo(pending, state.focusCardId)) return;
  const at = state.selectedTargets.indexOf(seat);
  if (at >= 0) state.selectedTargets.splice(at, 1);
  else state.selectedTargets.push(seat);
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 点座位 = 选中那条 `seat:N` 的 `SELECT_OPTION` 候选（【遗计】的"交给谁"）。
 *
 * 服务端把 `seat:N` 解析成 `pending.option_seats`（{选项串: 座位}），界面只读这份数据，
 * 不自己在客户端解析选项串。
 */
function selectOptionSeat(seat) {
  const pending = state.view ? state.view.pending : null;
  if (state.playback || !pending || pending.kind !== "SELECT_OPTION") return;
  const index = (pending.legal_actions || []).findIndex(
    (action) => action.kind === "SelectOption" && action.choice === `seat:${seat}`,
  );
  if (index < 0) return;
  state.selectedOption = index;
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 点手牌 = 设**焦点**；再点一次取消焦点并清空已选目标。
 *
 * 非手选询问（弃牌 / 排列 / 选择面 / 单选）里的手牌点击**不是手选**——那些路径由
 * `legal_actions` 的候选列表驱动，点牌不设焦点。本刀之前 `toggleCard` 承担这个入口，
 * 但它与 `toggleFocus` 互相委托会让非手选询问一点牌面就**无限递归**
 * （`toggleFocus` → `toggleCard` → `toggleFocus` → …），故收成一个函数、不再互调。
 */
function toggleFocus(cardId) {
  const pending = state.view ? state.view.pending : null;
  if (state.playback || !isHandPick(pending)) return;
  if (state.focusCardId === cardId) {
    state.focusCardId = null;
    state.selectedTargets = [];
    state.lastRejectReason = "";
  } else if (!cardSelectable(pending, cardId)) {
    // 用不出的牌不给焦点，并说清为什么（原来这里会静默选中、按钮永久灰掉）。
    state.focusCardId = null;
    state.selectedTargets = [];
    const card = (state.view.snapshot.self_hand || []).find((c) => c.card_id === cardId);
    state.lastRejectReason = `【${card ? card.name : cardId}】不能用于当前询问（${pending.prompt}）`;
  } else {
    state.focusCardId = cardId;
    state.selectedTargets = [];
    const auto = autoCombo(pending, cardId);
    if (auto) state.selectedTargets = auto.slice();
    state.lastRejectReason = "";
  }
  renderTable();
  renderPending(state.view);
}

/** 本次询问里"用这张牌"的候选（旧路径：候选列表按钮用）。 */
function cardCandidates(pending, cardId) {
  if (!pending) return [];
  return (pending.legal_actions || []).filter(
    (action) => action.card && action.card.card_id === cardId && (action.kind === "UseCard" || action.kind === "PlayCard"),
  );
}

/** 这张牌**能不能点**（旧路径的判据；手选路径用 :func:`cardSelectable`）。 */
function cardUsable(pending, cardId) {
  return cardCandidates(pending, cardId).length > 0;
}

/* ------------------------------------------------------------ 询问渲染 */

/** 手选路径：点牌设焦点、点座位选目标、选出一组**候选里真有**的组合才可提交。 */
function renderPending(view) {
  const pending = view.pending;
  $("t-options").innerHTML = "";
  // 询问文本：**需要做什么**（服务端 `context.requirement`）优先；没有就退回引擎的通用 prompt。
  const requirement = pending && pending.context && pending.context.requirement;
  $("t-prompt").textContent = requirement
    || (pending ? pending.prompt : "（没有待应答的询问）");
  // 主动技手选（【仁德】）/ 转化技（【龙胆】）正在等玩家点牌时，说清"现在要做什么"：
  // 这两步是**界面自己**开的模式，服务端的通用 prompt 读不出它。
  if (state.preativation) {
    $("t-prompt").textContent = `预发动【${state.preativation.name}】：按清单逐步选择。`;
  } else if (state.skillMode) {
    $("t-prompt").textContent = state.skillMode.requirement || `发动【${state.skillMode.name}】`;
  } else if (state.convertSkill !== null) {
    const skill = (view.skills || []).find((item) => item.skill_id === state.convertSkill);
    const text = skill && skill.ui && skill.ui.requirement;
    if (text) $("t-prompt").textContent = text;
  }
  // 描述文本 = 效果区（处理区）当前内容的说明，**不**再写服务端的"本窗口：谁对谁使用…"。
  // 那条只在询问到达时更新，回放期间会残留；效果区那行才与牌桌同步（用户裁定）。
  const contextNode = $("t-context");
  if (contextNode) contextNode.textContent = describeEffect(state.effectCards);
  // 这条窗口里**被提供**的技能（主动技 / 提供者 / 转化技）的"需要做什么"文案。
  const hintsNode = $("t-skill-hints");
  if (hintsNode) {
    const hints = (pending && pending.context && pending.context.skill_hints) || [];
    hintsNode.textContent = hints.map((hint) => `【${hint.name}】${hint.requirement}`).join("　");
  }
  $("btn-submit").disabled = true;
  $("btn-cancel").disabled = true;
  $("t-choice").textContent = "";
  const targetHint = $("t-target-hint");
  if (targetHint) targetHint.textContent = "";
  if (!pending) {
    $("t-timer").textContent = "";
    return;
  }
  const actions = pending.legal_actions || [];
  const kind = renderKind(pending, actions);

  if (kind === "picker") renderPicker(pending);
  else if (kind === "discard") renderDiscard(pending);
  else if (kind === "preativation") renderPreativation(pending);
  else if (kind === "skill") renderSkillPick(pending);
  else if (kind === "seat") renderSeatPick(pending);
  else if (kind === "slots") {
    // 选择面也要**指到那条候选**：引擎只发一条 `SelectCards` 骨架，具体 `picks` 由
    // `buildAction()` 补上；不指过去它就没有可补的对象 ⇒ `buildAction()` 返回 null ⇒
    // 点完槽位提交按钮仍然是灰的（本轮实测：点第 1 个槽位后 picks=[0] 而按钮不可点）。
    const index = actions.findIndex((action) => action.kind === "SelectCards");
    if (index >= 0) state.selectedOption = index;
    // 「提供一张 B」（票 14）：**就地**渲染（不铺选择面块），候选牌在牌桌上直接亮起。
    if (isProvide(pending)) renderProvide(pending);
    else renderSlots(pending);
    // 选择面这一步可不可以取消（票 01 的技能流水线给出一条 `Pass`）。
    $("btn-cancel").disabled = state.picks.length === 0 && !cancelAction(pending);
  }
  else if (kind === "direct") renderDirect(actions[0]);
  else renderCandidateList(pending, actions);

  // 提交按钮的启用与否**只算一次**（每个分支只管"铺界面"）：分散在各分支里写过一次
  // 就会漏掉一条路径（`SELECT_CARDS` 的选择面就因此永远灰着）。
  refreshSubmit(pending);
  renderCountdown(view);
}

/** 这次询问该走哪条渲染路径。
 *
 * - `picker`：出牌阶段 / 响应窗口 —— 点牌 + 点目标（本刀新增）；
 * - `direct`：只有一条候选、且它**没有目标要选** —— 不铺按钮，直接显示"将提交：…"；
 * - `slots`：`SELECT_CARDS` 的选择面（下一刀再统一）；
 * - `list`：其余询问类保留旧的候选列表按钮（逃生梯，下一刀抹平）。
 */
function renderKind(pending, actions) {
  // 选择面**永远**要渲染：它的交互在 `slots` 上（点槽位），不在候选条数上。
  // 引擎为 `SELECT_CARDS` 只生成**一条**候选（`SelectCards` + 全部 picks 的组合是前端拼的），
  // 所以"只有一条候选"绝不能理解成"没什么可选的、直接提交"。
  if (pending.kind === "SELECT_CARDS") return "slots";
  // 弃牌阶段：不铺候选按钮，点手牌收进待弃置集合（`buildAction` 按 picks 找候选）。
  if (pending.kind === "DISCARD") return "discard";
  // 预发动（W4-1）：清单驱动，不铺候选列表、不走手选。
  if (state.preativation) return "preativation";
  // 主动技手选模式（【仁德】）：牌子由手牌区点、座位由牌桌点，本窗口不铺选项按钮。
  if (state.skillMode) return "skill";
  // `SELECT_OPTION` 的"选座位"形态（【遗计】"交给谁"）：**取消选项按钮**，只点场上武将。
  if (pending.kind === "SELECT_OPTION" && seatChoices(pending).length > 0) return "seat";
  // **显式选中的候选优先**：技能按钮（`ActivateSkill`）在出牌阶段里与手选并存，
  // 点了它就等于"我就要发这个技能"，界面必须画那条候选的选中态，不能再画手选提示。
  if (state.selectedOption !== null && actions[state.selectedOption]) return "list";
  // 唯一且不需要选目标（放弃 / 结束阶段 / 唯一的无目标响应牌）：直接说"将提交：…"。
  // 判据用 `needsPicker`，**不能**只看"只有一条候选"——一条 `UseCard{杀, targets=(4,)}`
  // 也是唯一候选，但它必须让玩家先看见"打的是谁"。
  if (directAction(pending)) return "direct";
  // 手选询问（出牌阶段 / 响应窗口）**一律**走 picker：点牌 + 点目标。
  if (isHandPick(pending)) return "picker";
  return "list";
}

/** ``SELECT_OPTION`` 里形如 ``seat:N`` 的选项（服务端已解析成 ``option_seats``）。 */
function seatChoices(pending) {
  return Object.keys((pending && pending.option_seats) || {});
}

/** 这一步能不能**取消**（票 01 的技能流水线）：候选里真有一条 `Pass` 吗。
 *
 * 与「清空选择」的区别：清空只动界面本地状态；取消要**送出一条合法候选**，服务端据此
 * 中止这一步（选中的牌不会被搬走、技能使用次数不消耗）。故只有候选里真有 `Pass` 时，
 * 取消按钮才接到提交上——其余情形仍是原来的"清空选择"，既有询问一字不改。
 */
function cancelAction(pending) {
  if (!pending) return null;
  if (pending.kind !== "SELECT_CARDS" && pending.kind !== "SELECT_OPTION") return null;
  return (pending.legal_actions || []).find((action) => action.kind === "Pass") || null;
}

/** 取消：候选里真有 `Pass` 就送出去（服务端据此中止这一步），否则只清本地选择。 */
async function cancelOrClear() {
  if (state.playback) return;
  // 预发动（W4-1）：取消只是退出**接入层**的本地态，**一个 action 都不提交**；
  // 主动入口回到同一个出牌阶段询问，被动入口的"放弃"才是另一条 `Pass`（W4-2）。
  if (state.preativation) {
    state.preativation = null;
    state.lastRejectReason = "";
    renderTable();
    renderPending(state.view);
    return;
  }
  const pending = state.view ? state.view.pending : null;
  const pass = cancelAction(pending);
  if (!pass) {
    clearSelection();
    return;
  }
  state.selectedOption = (pending.legal_actions || []).indexOf(pass);
  state.focusCardId = null;
  state.selectedTargets = [];
  state.picks = [];
  state.skillMode = null;
  state.preativation = null;
  state.lastRejectReason = "";
  await submitAction();
}

/** 这条询问是不是"唯一一条候选、且不需要选目标"——是则返回那条候选，否则 `null`。
 *
 * 界面（:func:`renderKind` / :func:`refreshSubmit`）与提交（:func:`buildAction`）**必须**用
 * 同一个判据：各写一遍时，改一处漏一处就会出现"界面显示将提交：…、而 buildAction 返回 null"
 * 这种失配（本刀在 `SELECT_CARDS` 与技能按钮上各踩过一次）。
 */
function directAction(pending) {
  const actions = (pending && pending.legal_actions) || [];
  if (actions.length !== 1) return null;
  return needsPicker(pending) ? null : actions[0];
}

/** 这条询问是否必须让玩家手选（点牌 / 点目标）。 */
function needsPicker(pending) {
  if (!isHandPick(pending)) return false;
  const lone = (pending.legal_actions || [])[0];
  if (lone && lone.card) {
    const entry = cardEntry(pending, lone.card.card_id);
    if (entry && entry.max_targets > 0) return true;   // 有目标要选 ⇒ 必须手选
  }
  return false;
}

/** 手选路径：点牌设焦点、点座位选目标（提交按钮由 :func:`refreshSubmit` 统一算）。
 *
 * 另外把**不是"用某张牌"的候选**（`Pass` / `EndPhase`）也渲染成按钮：响应窗口里
 * "放弃"是一个**真候选**，只把它藏在提交按钮后面，玩家会以为"必须出牌"——
 * 报障原文：「响应南蛮入侵时强制出杀，没有放弃选项」。引擎侧 `_respond` 恒追加 `Pass`
 * （`effects/actions.py`），所以这不是缺候选，是缺入口。
 */
function renderPicker(pending) {
  const hint = $("t-target-hint");
  if (hint) hint.textContent = pickerTargetHint(pending);
  $("btn-cancel").disabled = state.focusCardId === null;
  renderPickerAlternatives(pending);
}

/** 弃牌阶段：不铺候选按钮（引擎按 C(n,k) 枚举组合，会铺出几十个「弃牌（N张）」），
 * 只提示"还要弃几张"，实际交互是 `renderTable` 里手牌的点选（`toggleDiscardPick`）。
 * 提交态由 :func:`refreshSubmit` 统一算——`buildAction` 按 `picks` 反查那条候选。
 */
function renderDiscard(pending) {
  const hint = $("t-target-hint");
  if (hint) {
    const need = pending.count || 0;
    hint.textContent = `点手牌选要弃置的牌：共 ${need} 张（已选 ${state.picks.length} 张）。`;
  }
  $("btn-cancel").disabled = state.picks.length === 0;
}

/** 主动技手选（【仁德】）：点手牌多选、点场上武将交出去；不铺任何选项按钮。 */
function renderSkillPick(pending) {
  const mode = state.skillMode;
  const hint = $("t-target-hint");
  if (hint) {
    hint.textContent = `点手牌选要交出的牌（已选 ${state.picks.length} 张），再点场上武将交给他。`;
  }
  $("btn-cancel").disabled = state.picks.length === 0 && state.skillSeat === null;
}

/** `SELECT_OPTION` 的"选座位"形态（【遗计】"交给谁" / 【离间】选使用者与目标）：只点场上武将。 */
function renderSeatPick(pending) {
  const hint = $("t-target-hint");
  if (hint) {
    hint.textContent = cancelAction(pending)
      ? "点场上武将选择；可点「取消」放弃这一步。"
      : "点场上武将，选择把这张牌交给谁。";
  }
  $("btn-cancel").disabled = state.selectedOption === null && !cancelAction(pending);
}

function renderPickerAlternatives(pending) {
  const box = $("t-options");
  (pending.legal_actions || []).forEach((action, index) => {
    if (action.card || action.kind === "ActivateSkill" || action.kind === "SelectCards") return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "opt";
    if (state.selectedOption === index) button.classList.add("sel");
    button.textContent = actionLabel(action);
    button.addEventListener("click", () => {
      state.selectedOption = index;
      state.focusCardId = null;
      state.selectedTargets = [];
      state.lastRejectReason = "";
      renderTable();
      renderPending(state.view);
    });
    box.appendChild(button);
  });
}

/** 唯一且不需要选目标的候选：不铺按钮，直接说"将提交：…"（spec D9）。 */
function renderDirect(only) {
  $("t-choice").textContent = only ? `将提交：${actionLabel(only)}` : "";
}

/** 提交按钮的唯一判据：**能造出一个候选里真有的 Action** 时才可点。
 *
 * 空选提交会被引擎拒（400），所以界面必须自己拦住——但**不能替引擎裁决**（`04` §4.1）：
 * `buildAction()` 只做"查找"，最后一道逐字段核对在 :func:`submitAction` 里。
 */
function refreshSubmit(pending) {
  if (state.playback) {
    // 严格顺序：这批事件播完之前不接任何提交。
    $("btn-submit").disabled = true;
    $("btn-cancel").disabled = true;
    $("t-choice").textContent = "正在播放事件…";
    if (state.lastRejectReason) $("t-choice").textContent = state.lastRejectReason;
    return;
  }
  const selected = state.selectedOption === null ? null : (pending.legal_actions || [])[state.selectedOption];
  const blocked = selected === null ? null : selectionError(selected, pending);
  const ready = blocked === null && buildAction() !== null;
  $("btn-submit").disabled = !ready;
  if (state.lastRejectReason) { $("t-choice").textContent = state.lastRejectReason; return; }
  if (blocked) { $("t-choice").textContent = blocked; return; }
  if (state.preativation) {
    const pa = state.preativation;
    $("btn-submit").disabled = !preactivationReady(pa);
    $("t-choice").textContent = preactivationReady(pa)
      ? "可以确认发动（提交一个复合动作）。"
      : (pa.cursor >= pa.steps.length ? "清单里还有步骤没填完。" : preactivationStepHint(pa, pending));
    return;
  }
  if (state.skillMode) {
    // 主动技手选（【仁德】）：说清"还差什么"，不替玩家决定。
    $("t-choice").textContent = ready
      ? "可以提交。"
      : (state.picks.length === 0 ? "先点手牌选要交出的牌。" : "再点一名其他角色。");
    return;
  }
  if (pending.kind === "SELECT_OPTION" && seatChoices(pending).length > 0) {
    $("t-choice").textContent = ready ? "可以提交。" : "点场上武将选择交给谁。";
    return;
  }
  if (isDiscardPick(pending)) {
    const need = pending.count || 0;
    $("t-choice").textContent = ready
      ? "可以提交。"
      : `还要选 ${Math.max(0, need - state.picks.length)} 张手牌弃置。`;
    return;
  }
  if (state.focusCardId !== null && isHandPick(pending)) {
    $("t-choice").textContent = ready ? "可以提交。" : pickerTargetHint(pending);
    return;
  }
  if (directAction(pending)) {
    renderDirect((pending.legal_actions || [])[0]);
    return;
  }
  $("t-choice").textContent = ready ? "" : "先选一项，或点一张可用的手牌再提交。";
}

/** 旧路径：每条候选一个按钮（`SelectOption` / `SKILL_ACTIVATION` / `DISCARD` 等）。 */
function renderCandidateList(pending, actions) {
  const options = $("t-options");
  // 候选太多时（转化 / 多目标组合）不逐条铺开：先按"用哪张牌"聚合。
  // 点了手牌就**只**显示它的候选：点牌 → 出目标 → 点目标（这是玩家的心智模型）。
  const focused = state.focusCardId !== null
    ? actions.map((action, index) => ({ action, index }))
        .filter(({ action }) => {
          if (!action.card) return false;
          const entry = cardEntry(pending, state.focusCardId);
          return entry !== null && String(action.card.card_id) === String(entry.card.card_id);
        })
    : null;
  const groups = groupActions(focused ? focused.map(({ action }) => action) : actions);
  if (focused) {
    // 把"焦点内的下标"翻译回原候选下标（选项点击要落回原数组）
    groups.forEach((group, i) => { group.actionIndex = focused[i].index; });
  }
  for (const group of groups) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "opt";
    button.textContent = group.label;
    if (group.targets && group.targets.length) button.setAttribute("data-targets", JSON.stringify(group.targets));
    if (group.actionIndex !== null && state.selectedOption === group.actionIndex) button.classList.add("sel");
    button.addEventListener("click", () => {
      state.selectedOption = group.actionIndex;
      state.lastRejectReason = "";
      renderPending(state.view);
    });
    options.appendChild(button);
  }

  if (state.selectedOption !== null) {
    const chosen = actions[state.selectedOption];
    if (chosen && chosen.kind === "SelectCards") renderSlots(pending);
  }
  $("btn-cancel").disabled = state.selectedOption === null && state.focusCardId === null && state.picks.length === 0;
}

/** 这次选择的**未就绪原因**；``null`` = 可以提交。
 *
 * 判据全部来自 `legal_actions` 里的那一条候选（`spec.total` / `cards` 长度 / `order` 长度），
 * 不自己发明规则。
 */
function selectionError(action, pending) {
  if (state.preativation && action.kind === "ActivateSkill") {
    // 张数 / 目标 / 区域的规则由服务端校验器裁决；本地只拦"清单没填完"。
    return preactivationReady(state.preativation) ? null : "预发动清单还没填完。";
  }
  if (action.kind === "SelectCards") {
    const total = (pending.spec && pending.spec.total) || [1, 1];
    const least = total[0];
    const most = total[1];
    if (state.picks.length < least) return `从选择面里选 ${least} 张牌（已选 ${state.picks.length}）。`;
    if (state.picks.length > most) return `最多只能选 ${most} 张（已选 ${state.picks.length}）。`;
    return null;
  }
  if (action.kind === "DiscardCards") {
    const need = (action.cards || []).length;
    const chosen = state.picks.length;
    if (chosen !== need) return `要弃 ${need} 张牌（已选 ${chosen}）。`;
    return null;
  }
  if (action.kind === "ArrangeCards") {
    const need = (action.order || []).length;
    if (!need) return "排列为空，不能提交。";
    return null;
  }
  return null;
}

function actionLabel(action) {
  if (action.kind === "UseCard" || action.kind === "PlayCard") {
    const targets = action.targets && action.targets.length ? ` → 座位 ${action.targets.join("、")}` : "";
    return `使用【${action.card.name}】${targets}`;
  }
  if (action.kind === "ActivateSkill") {
    return `发动技能 ${action.skill_id}${action.picks && action.picks.length ? `（选 ${action.picks.length} 张）` : ""}`;
  }
  if (action.kind === "SelectCards") return "选牌";
  if (action.kind === "SelectOption") {
    const seat = /^seat:(\d+)$/.exec(action.choice);
    return seat ? `选择座位 ${seat[1]}` : `选项：${action.choice}`;
  }
  if (action.kind === "ArrangeCards") return `排列（${action.order.length} 张）`;
  if (action.kind === "DiscardCards") return `弃牌（${(action.cards || []).length} 张）`;
  if (action.kind === "EndPhase") return "结束出牌阶段";
  if (action.kind === "Pass") return "放弃";
  return action.kind;
}

/** 候选 → 可点选项。**每条候选一个选项**（不再按标签去重）：
 *
 * 引擎把每个目标组合枚举成一条独立候选，所以"指定目标"就是"点那一条候选"。
 * 去重会把同一张牌的多个目标组合并成一条，玩家就没法选目标了。
 * 带 `targets` 的候选在选项上打 `data-targets`，好让界面与测试都能"看见目标"。
 */
function groupActions(actions) {
  return actions.map((action, index) => ({
    label: actionLabel(action),
    actionIndex: index,
    targets: action.targets === undefined ? null : action.targets,
    cardId: action.card ? action.card.card_id : null,
  }));
}

function renderSlots(pending) {
  const box = $("t-options");
  const heading = document.createElement("div");
  heading.className = "small";
  // 张数上限读服务端的 `spec.total`（同一份约束，前端不另立真源）：
  // 上限 > 1 时要说清"可以多选"（【遗计】一次交两张，票 15）。
  const cap = selectionCap(pending);
  const pickText = cap === 1 ? "选择一张" : `选择一至 ${cap} 张`;
  heading.textContent = `选择面：隐藏区域的槽位只有位置（看不到牌面），选中并结算后才揭晓。这一步${pickText}。`;
  box.appendChild(heading);
  const row = document.createElement("div");
  row.className = "cards";
  (pending.slots || []).forEach((slot, index) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "card";
    if (state.picks.includes(index)) chip.classList.add("sel");
    const face = slot.card ? `${slot.card.name}` : "？";
    const where = slot.ref && slot.ref.seat !== null && slot.ref.seat !== undefined ? `座${slot.ref.seat}` : "";
    chip.innerHTML = `<span class="n">${face}</span><span class="s">${where} ${slot.ref ? slot.ref.zone : ""}</span>`;
    chip.addEventListener("click", () => {
      toggleSlotPick(pending, index);
      renderPending(state.view);
    });
    row.appendChild(chip);
  });
  box.appendChild(row);
}

/** 选择面的张数上限（服务端 ``spec.total`` 的上界；缺省 1）。 */
function selectionCap(pending) {
  const total = (pending && pending.spec && pending.spec.total) || [1, 1];
  return Math.max(1, Number(total[1]) || 1);
}

/** 点一个槽位 = 选中 / 取消它；**上限之内**才收下（票 15：【遗计】一次可选两张，多选不得越界）。
 *
 * 上限是服务端 ``spec.total`` 的纯函数（与 :func:`cardFace` 同一条"当前状态的纯函数"纪律），
 * 故这里只做越界拒绝，不做任何跨询问的记账。
 */
function toggleSlotPick(pending, index) {
  const at = state.picks.indexOf(index);
  if (at >= 0) {
    state.picks.splice(at, 1);
    return;
  }
  if (state.picks.length >= selectionCap(pending)) return;
  state.picks.push(index);
}

/** 「提供一张 B」面（票 14 §C）：渲染实现**不是**选择面块——候选牌已经在 :func:`renderTable`
 * 里按 :func:`cardFace` **就地**亮在本人手牌 / 装备区 / 判定区上，这里只留一句"怎么点"。
 *
 * 与 :func:`renderSlots` 的分岔点只有一处（`renderPending` 的 `slots` 分支）：
 * `provide_type` 非空走这里，其余一字不改（规则 B 窄化到"提供 B"面，`design.md` §11 C）。
 */
function renderProvide(pending) {
  const hint = $("t-target-hint");
  if (!hint) return;
  const total = (pending.spec && pending.spec.total) || [1, 1];
  const named = (pending.slots || []).map((slot) => slot.card).find(Boolean);
  const name = (named && named.name) || pending.provide_type;
  hint.textContent = state.picks.length >= total[0]
    ? `已提供 ${state.picks.length} 张【${name}】，可以提交。`
    : `在当前亮起的牌上直接点选：提供一张【${name}】（已选 ${state.picks.length} 张）。`;
}

/** 「提供一张 B」面：点一张就地高亮的牌 = 选中 / 取消它对应的**面上槽位**（票 14）。
 *
 * 提交的答案是**面内位置**（`picks`），由既有 `SelectCards` 骨架补上（`buildAction`）——
 * 虚拟 B 背后没有实体牌，回传实体 id 会把"转出来的是哪张"丢掉。
 */
function toggleProvidePick(slotIndex) {
  const pending = state.view ? state.view.pending : null;
  if (state.playback || !isProvide(pending)) return;
  const at = state.picks.indexOf(slotIndex);
  if (at >= 0) {
    state.picks.splice(at, 1);
  } else {
    const total = (pending.spec && pending.spec.total) || [1, 1];
    if (state.picks.length < total[1]) state.picks.push(slotIndex);
  }
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

function renderCountdown(view) {
  const timer = $("t-timer");
  if (!view || !view.pending || view.timeout_seconds === null) {
    timer.textContent = "";
    return;
  }
  // 服务端用的是单调时钟，与浏览器时钟不可比；这里只用"收到的本地时刻 + 时限"做倒计时。
  // 真正的代答**在服务端**（纯函数，见 timeout.py）：前端只显示、绝不自己代答。
  const received = view.received_at || (view.received_at = Date.now());
  const remain = Math.max(0, view.timeout_seconds - (Date.now() - received) / 1000);
  timer.textContent = remain > 0 ? `剩余 ${remain.toFixed(0)} 秒` : "已到时限，服务端即将代答";
}

/* ------------------------------------------------------------------ 提交 */

function buildAction() {
  const pending = state.view.pending;
  if (!pending) return null;
  // 预发动（W4-1）：确认 = **复合动作**，由清单的本地答案拼出（不在 `legal_actions` 里；
  // 服务端校验器按同一份步骤声明验）。取消走 :func:`cancelOrClear` 的本地分支。
  if (state.preativation) return buildPreativationAction(pending, state.preativation);
  const actions = pending.legal_actions;
  // 选择面的候选里**没有**具体 picks（引擎只发一条骨架），要按已点槽位补上。
  const withPicks = (action) => (action.kind === "SelectCards" ? Object.assign({}, action, { picks: state.picks.slice() }) : action);
  // 弃牌阶段：候选带的是 `cards`（不是单张 `card`），按"已选手牌集合"反查那一条。
  // 这条必须在 `directAction` **之前**：弃牌可能恰好只有一条候选（必须全弃），
  // 但那也不该在玩家点牌之前就显示成"将提交"。
  if (isDiscardPick(pending)) {
    if (!state.picks.length) return null;
    const mine = state.picks.slice().sort((a, b) => a - b).join(",");
    return actions.find(
      (candidate) =>
        candidate.kind === "DiscardCards"
        && (candidate.cards || []).map((card) => card.card_id).sort((a, b) => a - b).join(",") === mine,
    ) || null;
  }
  // 主动技手选（【仁德】）：已选手牌 + 已点座位 → 在候选里**查**那一条（不自己拼 Action）。
  if (state.skillMode) {
    if (!state.picks.length || state.skillSeat === null) return null;
    const picks = state.picks.slice().sort((a, b) => a - b).join(",");
    const choices = `seat:${state.skillSeat}`;
    return actions.find(
      (action) =>
        action.kind === "ActivateSkill"
        && action.skill_id === state.skillMode.skillId
        && (action.picks || []).slice().sort((a, b) => a - b).join(",") === picks
        && (action.choices || []).join(",") === choices,
    ) || null;
  }
  // 唯一且不需要选目标的候选（放弃 / 结束阶段 / 唯一的无目标响应牌）：直接就是它。
  // 这条必须在手选分支**之前**——`renderKind` 对同一个询问也返回 `direct`，
  // 否则界面显示"将提交：放弃"、而 `buildAction()` 在手选模式下因为没有焦点而返回 null。
  const only = directAction(pending);
  if (only) return withPicks(only);
  // **显式选中的候选优先于手选**：技能按钮（`ActivateSkill`）住在出牌阶段的候选里，
  // 点了它就是"我就要发这个技能"——它没有焦点也没有目标，走手选分支会返回 null。
  if (state.selectedOption !== null) {
    const action = actions[state.selectedOption];
    if (!action) return null;
    if (action.kind === "SelectCards") return withPicks(action);
    if (action.kind === "DiscardCards") {
      // 弃牌候选带的是 `cards`（不是单张 `card`），故按"已选牌集合"找那一条。
      const mine = state.picks.slice().sort((a, b) => a - b).join(",");
      return actions.find(
        (candidate) =>
          candidate.kind === "DiscardCards"
          && (candidate.cards || []).map((card) => card.card_id).sort((a, b) => a - b).join(",") === mine,
      ) || null;
    }
    return action;
  }
  if (isHandPick(pending)) {
    // 手选路径：焦点 + 已选目标 → 在候选里**查**那一条（不自己拼 Action）。
    if (state.focusCardId === null) return null;
    if (!isLegalCombo(pending, state.focusCardId, state.selectedTargets)) return null;
    // 焦点是玩家点的**实体牌**；转化技下要提交的是它对应的**虚拟牌**（`entry.card`）。
    const entry = cardEntry(pending, state.focusCardId);
    if (!entry) return null;
    const targets = state.selectedTargets.slice().sort((a, b) => a - b);
    return actions.find(
      (action) =>
        action.card
        && String(action.card.card_id) === String(entry.card.card_id)
        && (action.kind === "UseCard" || action.kind === "PlayCard")
        && (action.targets || []).slice().sort((a, b) => a - b).join(",") === targets.join(","),
    ) || null;
  }
  return null;
}

async function submitAction() {
  if (state.playback) return;
  const action = buildAction();
  if (!action) {
    $("t-choice").textContent = "没有可提交的候选：先选一张牌或一个选项。";
    return;
  }
  const pending = state.view.pending;
  const blocked = selectionError(action, pending);
  if (blocked) {
    $("t-choice").textContent = blocked;
    return;
  }
  const composite = state.preativation !== null && action.kind === "ActivateSkill";
  if (
    !composite
    && !pending.legal_actions.some((candidate) => JSON.stringify(candidate) === JSON.stringify(action))
  ) {
    // 最后一道自检：造出来的必须**逐字段**是候选里的那一条（否则引擎会拒，且错在界面）。
    // 预发动是**唯一**让开的地方：复合动作不要求在候选里，由服务端校验器验（W4-1）。
    $("t-choice").textContent = "这个组合不在合法候选里，请换一个选择。";
    return;
  }
  try {
    const body = await post(
      `/api/sessions/${state.sessionId}/answer?after=${state.cursor}&logs=${state.logSeq}`,
      { action },
    );
    resetSelection();
    applyView(body);
  } catch (error) {
    if (error.status === 409) {
      $("t-choice").textContent = "这个询问已过期（可能刚被代答）：已为你刷新。";
      await refresh();
      return;
    }
    $("t-choice").textContent = `提交被拒：${error.message}`;
  }
}

async function takeover() {
  /* 托管 = **服务端的超时代答**（纯函数，见 `timeout.py`）：前端不自己造 Action，
   * 也不越过 legal_actions。故这个按钮只做一件事——催服务端判定一次（取增量即判定）。 */
  if (state.playback) return;
  await refresh();
  $("t-choice").textContent = "已请服务端判定一次（到时限即自动代答）。";
}

function clearSelection() {
  if (state.playback) return;
  resetSelection();
  renderTable();
  renderPending(state.view);
}

/* ------------------------------------------------------------------ 日志 */

/** 事件流的详略；`focus` = 隐藏对手之间的响应往来（一局 268 次 RESPOND 会把阶段埋掉）。 */
const LOG_MODES = ["focus", "all"];

function logMode() {
  const node = $("t-log-mode");
  const mode = node && node.value ? node.value : "focus";
  return LOG_MODES.includes(mode) ? mode : "focus";
}

/** 这条记录在**重点**模式下要不要显示。回放页不走这个函数（那边是上帝视角、逐条看）。 */
function keepRecord(record, querySeat) {
  if (logMode() === "all") return true;
  // `action` 记录不带座位，故用"上一条询问的 actor"当它的座位（记录流里询问与应答严格相邻）。
  const seat = record.kind === "query" ? record.query.actor : querySeat;
  const me = state.view ? state.view.human_seat : null;
  if (record.kind === "query" || record.kind === "action") return seat === me;
  if (record.kind === "delta" && record.delta_type === "deck_shuffle") return false;
  return true;
}

function appendLog(record, querySeat) {
  if (!keepRecord(record, querySeat)) return;
  const item = document.createElement("li");
  item.innerHTML = describeRecord(record);
  $("t-log").appendChild(item);
}

/** 重画整条事件流（切换详略时需要把已渲染的清掉重来）。 */
function renderLog() {
  const log = $("t-log");
  log.innerHTML = "";
  if (!state.view) return;
  let querySeat = null;
  for (const record of state.view.records || []) {
    if (record.kind === "query") querySeat = record.query.actor;
    appendLog(record, querySeat);
  }
}

function describeRecord(record) {
  if (record.kind === "header") return `<span class="k">开局</span> 种子 ${record.seed} · ${record.rule_config.player_count} 人`;
  if (record.kind === "timing") {
    const who = record.actor !== null && record.actor !== undefined ? ` · 座位 ${record.actor}` : "";
    return `<span class="k">#${record.seq}</span> ${timingLabel(record.timing)}${who}`;
  }
  if (record.kind === "query") return `<span class="k">#${record.seq}</span> 询问 ${record.query.kind} → 座位 ${record.query.actor}`;
  if (record.kind === "action") return `<span class="k">#${record.seq}</span> 应答 ${record.action ? record.action.kind : "？"} ${record.accepted ? "（接受）" : "（被拒）"}`;
  if (record.kind === "frame") return `<span class="k">#${record.seq}</span> 结算帧 ${record.op} ${record.frame_kind}（深度 ${record.depth}）`;
  if (record.kind === "game_end") return `<span class="k">#${record.seq}</span> 终局 ${record.result} · 获胜座位 [${(record.winners || []).join("、")}] · ${record.reason}`;
  if (record.kind === "delta") {
    if (record.delta_type === "card_move") {
      return `<span class="k">#${record.seq}</span> 牌移动 ${zoneLabel(record.from_zone)} → ${zoneLabel(record.to_zone)}（${record.reason}）${cardsLabel(record.cards)}`;
    }
    if (record.delta_type === "hp_change") return `<span class="k">#${record.seq}</span> 体力 座位 ${record.seat} ${record.before} → ${record.after}`;
    if (record.delta_type === "identity_deal") return `<span class="k">#${record.seq}</span> 身份 座位 ${record.seat}（只有自己 / 主公 / 阵亡者公开）`;
    if (record.delta_type === "identity_reveal") return `<span class="k">#${record.seq}</span> 亮明身份 座位 ${record.seat}`;
    return `<span class="k">#${record.seq}</span> 洗牌`;
  }
  return `<span class="k">#${record.seq || ""}</span> ${record.kind}`;
}

/** 时机枚举名 → 中文。只做**通用裁剪**（前缀 + 阶段专名），不逐条手抄 83 个名字。 */
const TIMING_PREFIXES = [
  ["PHASE_PREPARE", "准备阶段"],
  ["PHASE_JUDGE", "判定阶段"],
  ["PHASE_DRAW", "摸牌阶段"],
  ["PHASE_PLAY", "出牌阶段"],
  ["PHASE_DISCARD", "弃牌阶段"],
  ["PHASE_FINISH", "结束阶段"],
  // 判定帧的三个时点：逐字译出来，"这次判定属于哪个座位"才读得清
  // （事件流的人话里带 `· 座位 N`，而 N 就是被判定者）。
  ["JUDGE_CARD_BEFORE_EFFECT", "判定牌生效前"],
  ["JUDGE_CARD_AFTER_EFFECT", "判定牌生效后"],
  ["JUDGE_ON", "判定开始"],
  ["JUDGE_", "判定"],
  ["TURN_", "回合"],
  ["GAME_", "开局"],
];

function timingLabel(timing) {
  if (!timing) return "时机";
  for (const [prefix, label] of TIMING_PREFIXES) {
    if (timing === prefix || timing.startsWith(prefix)) {
      const rest = timing.slice(prefix.length).replace(/^_/, "");
      const suffix = { "": "", START: "开始", END: "结束", IDLE: "空闲", BEFORE: "前", AFTER: "后" }[rest];
      return suffix === undefined ? `${label} ${rest}` : `${label}${suffix}`;
    }
  }
  return timing;
}

function zoneLabel(ref) {
  if (!ref) return "（初次）";
  const names = { deck: "牌堆", hand: "手牌", discard: "弃牌堆", processing: "处理区", equip: "装备区", judge: "判定区" };
  const where = ref.seat === null || ref.seat === undefined ? "" : `座位${ref.seat} `;
  return `${where}${names[ref.zone] || ref.zone}`;
}

function cardsLabel(cards) {
  if (!cards || !cards.length) return "";
  return ` · ${cards.map((card) => card.name).join("、")}`;
}

/* ------------------------------------------------------------------ 录像 */

async function loadRuns() {
  try {
    const body = await api("/api/runs");
    state.runs = body.runs || [];
    const box = $("run-list");
    box.innerHTML = "";
    if (!state.runs.length) {
      box.innerHTML = `<p class="dim">还没有录像。打完一局就会出现在这里。</p>`;
      return;
    }
    for (const run of state.runs) {
      const row = document.createElement("div");
      row.className = "run";
      const result = run.human_seat_won === true ? `<span class="win">我赢</span>`
        : run.human_seat_won === false ? `<span class="lose">我输</span>` : `<span class="dim">—</span>`;
      row.innerHTML = `<span><code>${run.name}</code> <span class="dim">种子 ${run.seed} · ${run.player_count} 人</span></span>
        <span>${result} <button type="button" class="ghost">看回放</button></span>`;
      const button = row.querySelector("button");
      if (button) button.addEventListener("click", () => openReplay(run.name));
      box.appendChild(row);
    }
  } catch (error) {
    $("run-list").innerHTML = `<p class="dim">读取录像列表失败：${error.message}</p>`;
  }
}

async function openReplay(name) {
  try {
    const body = await api(`/api/runs/${encodeURIComponent(name)}`);
    state.replay = { name, meta: body.meta, records: body.records, index: 0 };
    $("r-name").textContent = name;
    $("r-seed").textContent = String(body.meta.seed);
    $("r-heroes").textContent = (body.meta.heroes || []).map((hero, seat) => `${seat}:${hero || "—"}`).join("  ");
    $("r-log").innerHTML = "";
    show("replay");
    renderReplay();
  } catch (error) {
    $("status").textContent = `回放失败：${error.message}`;
  }
}

function renderReplay() {
  const replay = state.replay;
  if (!replay) return;
  const counts = replay.meta.player_count;
  const heroes = replay.meta.heroes || [];
  const cards = new Map();
  const hp = new Map();
  const identity = new Map();
  const record = replay.records[replay.index] || {};
  for (let seat = 0; seat < counts; seat += 1) hp.set(seat, null);

  for (const item of replay.records.slice(0, replay.index + 1)) {
    if (item.kind === "delta" && item.delta_type === "hp_change") hp.set(item.seat, `${item.before}→${item.after}`);
    if (item.kind === "delta" && item.delta_type === "identity_deal") identity.set(item.seat, item.identity);
    if (item.kind === "delta" && item.delta_type === "card_move") {
      for (const card of item.cards || []) {
        if (card.slot) cards.set(`座位${item.to_zone && item.to_zone.seat}:${card.slot}`, card.name);
      }
    }
  }

  const seats = $("r-seats");
  seats.innerHTML = "";
  for (let seat = 0; seat < counts; seat += 1) {
    const box = document.createElement("div");
    box.className = "seat";
    box.innerHTML = `<div class="top"><span class="who">座位 ${seat}</span>
      <span class="small">体力 ${hp.get(seat) || "—"}</span></div>
      <div class="small">${heroes[seat] || "（无武将）"} · 身份 ${identity.get(seat) ? identityLabel(identity.get(seat)) : "未公开"}</div>`;
    seats.appendChild(box);
  }

  $("r-pos").textContent = `${replay.index + 1} / ${replay.records.length}（seq ${record.seq || "-"}）`;
  const log = $("r-log");
  log.innerHTML = "";
  const from = Math.max(0, replay.index - 60);
  for (let i = from; i <= replay.index; i += 1) {
    const item = document.createElement("li");
    if (i === replay.index) item.className = "banner";
    item.innerHTML = describeRecord(replay.records[i]);
    log.appendChild(item);
  }
  if (log.lastChild) log.lastChild.scrollIntoView({ block: "nearest" });
}

function replayStep(delta) {
  const replay = state.replay;
  if (!replay) return;
  replay.index = Math.max(0, Math.min(replay.records.length - 1, replay.index + delta));
  renderReplay();
}

/* ------------------------------------------------------------------ 装配 */

/** 牌桌按钮：在 0.05–0.5 秒之间切一档回放步长（缺省 0.1，不记住）。 */
function cycleDelay() {
  const at = DELAY_STEPS_MS.indexOf(state.eventDelayMs);
  state.eventDelayMs = DELAY_STEPS_MS[(at + 1) % DELAY_STEPS_MS.length];
  updateDelayButton();
}

function updateDelayButton() {
  const button = $("btn-delay");
  if (!button) return;
  // 0.05s 是亚 0.1 秒挡：一位小数会被 toFixed(1) 抹成 "0.1"，与 0.1 挡撞字，故两位。
  const seconds = state.eventDelayMs / 1000;
  button.textContent = `步长 ${seconds < 0.1 ? seconds.toFixed(2) : seconds.toFixed(1)}s`;
}

function wire() {
  seedOptions();
  $("in-players").addEventListener("change", seedOptions);
  $("in-pointjiang").addEventListener("change", syncPointjiang);
  syncPointjiang();
  loadHeroCatalog();
  $("lobby-form").addEventListener("submit", startGame);
  $("nav-lobby").addEventListener("click", () => { show("lobby"); loadRuns(); });
  $("nav-table").addEventListener("click", () => show("table"));
  $("nav-replay").addEventListener("click", () => { show("replay"); if (!state.replay) $("r-log").innerHTML = "<li class=\"dim\">从开局页选一局录像。</li>"; });
  const modeSelect = $("t-log-mode");
  if (modeSelect) modeSelect.addEventListener("change", () => {
    state.view = Object.assign({}, state.view, { records: state.logRecords });
    renderLog();
  });
  $("btn-submit").addEventListener("click", submitAction);
  $("btn-cancel").addEventListener("click", cancelOrClear);
  $("btn-refresh").addEventListener("click", refresh);
  $("btn-takeover").addEventListener("click", takeover);
  $("btn-delay").addEventListener("click", cycleDelay);
  updateDelayButton();
  $("btn-r-first").addEventListener("click", () => { state.replay.index = 0; renderReplay(); });
  $("btn-r-prev").addEventListener("click", () => replayStep(-1));
  $("btn-r-next").addEventListener("click", () => replayStep(1));
  $("btn-r-last").addEventListener("click", () => { if (state.replay) { state.replay.index = state.replay.records.length - 1; renderReplay(); } });
  $("btn-r-play").addEventListener("click", () => {
    const tick = () => {
      if (!state.replay) return;
      if (state.replay.index >= state.replay.records.length - 1) return;
      replayStep(1);
      window.setTimeout(tick, 120);
    };
    tick();
  });
  loadRuns();
  // 倒计时每秒刷新（只在牌桌页可见时）；不改任何服务端状态。
  // 回放期间不刷：那时 `state.view` 还是上一条询问，倒计时会指向已经过去的那个窗口。
  window.setInterval(() => {
    if (!$("view-table").hidden && !state.playback) renderCountdown(state.view);
  }, 1000);
}

wire();
