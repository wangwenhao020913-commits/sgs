/* SGS M4 极简界面：只做两件事——渲染事件流、收集并提交应答。
 *
 * 纪律（`04` §4.1 与地图 D8）：
 *  - 前端**不做规则判定**，合法性只用于高亮与禁用；提交的 Action **原样取自** legal_actions；
 *  - 隐藏信息在传输层已被过滤，本文件拿不到未公开数据，也不去猜；
 *  - 界面**不自己代答超时**，只显示倒计时（代答在服务端，是纯函数）。
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
const state = {
  sessionId: null,
  cursor: -1,
  logSeq: 0,
  view: null,          // 服务端最近一次下发的会话视图
  selectedCards: [],   // 手牌里选中的 card_id
  selectedOption: null,// 选中的 legal_action 下标
  picks: [],           // SELECT_CARDS 选中的槽位下标
  timer: null,
  runs: [],
  replay: null,
  logRecords: [],   // 已收到的全部下发记录（切换详略时重画用）
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

async function startGame(event) {
  event.preventDefault();
  const seedRaw = $("in-seed").value.trim();
  const timeout = Number($("in-timeout").value);
  $("lobby-hint").textContent = "正在建局…";
  try {
    const body = await post("/api/sessions", {
      player_count: Number($("in-players").value),
      ai: $("in-ai").value,
      human_seat: Number($("in-seat").value),
      timeout_seconds: timeout > 0 ? timeout : null,
      seed: seedRaw === "" ? null : Number(seedRaw),
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

function applyView(body) {
  state.view = body;
  state.cursor = body.cursor;
  state.logSeq = body.log_seq;
  $("t-seed").textContent = String(body.seed);
  $("t-seat").textContent = String(body.human_seat);
  $("btn-takeover").hidden = body.finished;
  state.logRecords = (state.logRecords || []).concat(body.records || []);
  for (const record of body.records) {
    if (record.kind === "query") state.logQuerySeat = record.query.actor;
    appendLog(record, state.logQuerySeat);
  }
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
    $("status").textContent = "已结束（录像已存）";
    loadRuns();
    return;
  }
  renderPending(body);
}

async function refresh() {
  if (!state.sessionId) return;
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

  const seats = $("t-seats");
  seats.innerHTML = "";
  for (const player of snapshot.players) {
    const box = document.createElement("div");
    box.className = "seat";
    if (player.seat === snapshot.seat) box.classList.add("me");
    if (!player.alive) box.classList.add("dead");
    if (player.seat === snapshot.turn_seat) box.classList.add("turn");
    const hero = view.heroes[player.seat] || "（无武将）";
    const identity = player.identity ? ` · ${identityLabel(player.identity)}` : "";
    box.innerHTML = `
      <div class="top">
        <span class="who">座位 ${player.seat}${player.seat === snapshot.seat ? "（我）" : ""}</span>
        <span class="hp">${"●".repeat(Math.max(0, player.hp))}${"○".repeat(Math.max(0, player.hp_max - Math.max(0, player.hp)))}</span>
      </div>
      <div class="small">${hero}${identity}</div>
      <div class="small">手牌 ${player.hand_count}　装备 ${player.equip.length}　判定 ${player.judge.length}</div>
      <div class="small">${player.alive ? "" : "已阵亡"}</div>`;
    seats.appendChild(box);
  }

  $("t-hand-count").textContent = `${snapshot.self_hand.length} 张`;
  const hand = $("t-hand");
  hand.innerHTML = "";
  for (const card of snapshot.self_hand) {
    hand.appendChild(cardChip(card, state.selectedCards.includes(card.card_id)));
  }
  $("t-equip").innerHTML = "";
  for (const card of ownZone(snapshot, "equip")) $("t-equip").appendChild(cardChip(card, false, true));
  $("t-judge").innerHTML = "";
  for (const card of ownZone(snapshot, "judge")) $("t-judge").appendChild(cardChip(card, false, true));
  $("t-hero").textContent = view.heroes[snapshot.seat] || "（无武将）";
}

function ownZone(snapshot, zone) {
  const me = snapshot.players.find((player) => player.seat === snapshot.seat);
  return me ? me[zone] : [];
}

function identityLabel(identity) {
  return { lord: "主公", loyalist: "忠臣", rebel: "反贼", traitor: "内奸" }[identity] || identity;
}

function cardChip(card, selected, isStatic) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "card";
  if (selected) chip.classList.add("sel");
  if (isStatic) chip.classList.add("static");
  chip.innerHTML = `<span class="n">${card.name}</span><span class="s">${card.suit || ""}${card.rank ?? ""}</span>`;
  if (!isStatic) chip.addEventListener("click", () => toggleCard(card.card_id));
  return chip;
}

function toggleCard(cardId) {
  const index = state.selectedCards.indexOf(cardId);
  if (index >= 0) state.selectedCards.splice(index, 1);
  else state.selectedCards.push(cardId);
  renderTable();
  renderPending(state.view);
}

/* ------------------------------------------------------------ 询问渲染 */

function renderPending(view) {
  const pending = view.pending;
  const options = $("t-options");
  options.innerHTML = "";
  $("t-prompt").textContent = pending ? pending.prompt : "（没有待应答的询问）";
  $("btn-submit").disabled = true;
  $("btn-cancel").disabled = true;
  $("t-choice").textContent = "";
  if (!pending) {
    $("t-timer").textContent = "";
    return;
  }
  const actions = pending.legal_actions;
  const selected = state.selectedOption === null ? null : actions[state.selectedOption];

  // 候选太多时（转化 / 多目标组合）不逐条铺开：先按"用哪张牌"聚合。
  const groups = groupActions(actions);
  for (const group of groups) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "opt";
    button.textContent = group.label;
    if (group.actionIndex !== null && state.selectedOption === group.actionIndex) button.classList.add("sel");
    button.addEventListener("click", () => {
      state.selectedOption = group.actionIndex;
      renderPending(state.view);
    });
    options.appendChild(button);
  }

  if (selected && selected.kind === "SelectCards") {
    renderSlots(pending);
  }
  renderCountdown(view);
  // 提交按钮：只有"能造出一个**候选里真有**的 Action"时才可点。空选提交会被引擎拒（400），
  // 所以这里必须自己拦住——前端可以做合法性高亮与禁用，但**不能替引擎裁决**（`04` §4.1）。
  const blocked = selected === null ? null : selectionError(selected, pending);
  const ready = selected !== null
    ? blocked === null
    : state.selectedCards.length > 0 && buildAction() !== null;
  $("btn-submit").disabled = !ready;
  $("t-choice").textContent = blocked
    || (selected === null && !ready ? "先选一项，或点一张手牌再提交。" : "");
  $("btn-cancel").disabled = selected === null && state.selectedCards.length === 0 && state.picks.length === 0;
}

/** 这次选择的**未就绪原因**；``null`` = 可以提交。
 *
 * 判据全部来自 `legal_actions` 里的那一条候选（`spec.total` / `cards` 长度 / `order` 长度），
 * 不自己发明规则。
 */
function selectionError(action, pending) {
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
    if (state.selectedCards.length !== need) return `要弃 ${need} 张牌（已选 ${state.selectedCards.length}）。`;
    return null;
  }
  if (action.kind === "ArrangeCards") {
    const need = (action.order || []).length;
    if (!need) return "排列为空，不能提交。";
    return null;
  }
  return null;
}

function groupActions(actions) {
  const out = [];
  const seen = new Map();
  actions.forEach((action, index) => {
    let label;
    if (action.kind === "UseCard" || action.kind === "PlayCard") {
      const targets = action.targets && action.targets.length ? ` → 座位 ${action.targets.join("、")}` : "";
      label = `使用【${action.card.name}】${targets}`;
    } else if (action.kind === "ActivateSkill") {
      label = `发动技能 ${action.skill_id}${action.picks && action.picks.length ? `（选 ${action.picks.length} 张）` : ""}`;
    } else if (action.kind === "SelectCards") {
      label = `选牌（已选 ${state.picks.length}）`;
    } else if (action.kind === "SelectOption") {
      label = `选项：${action.choice}`;
    } else if (action.kind === "ArrangeCards") {
      label = `排列（${action.order.length} 张）`;
    } else if (action.kind === "DiscardCards") {
      label = `弃牌（${(action.cards || []).length} 张）`;
    } else if (action.kind === "EndPhase") {
      label = "结束出牌阶段";
    } else if (action.kind === "Pass") {
      label = "放弃";
    } else {
      label = action.kind;
    }
    if (seen.has(label)) return;
    seen.set(label, true);
    out.push({ label, actionIndex: index });
  });
  return out;
}

function renderSlots(pending) {
  const box = $("t-options");
  const heading = document.createElement("div");
  heading.className = "small";
  heading.textContent = "选择面：隐藏区域的槽位只有位置（看不到牌面），选中并结算后才揭晓。";
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
      const at = state.picks.indexOf(index);
      if (at >= 0) state.picks.splice(at, 1);
      else state.picks.push(index);
      renderPending(state.view);
    });
    row.appendChild(chip);
  });
  box.appendChild(row);
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
  const actions = pending.legal_actions;
  if (state.selectedOption !== null) {
    const action = actions[state.selectedOption];
    if (action.kind === "SelectCards") {
      return Object.assign({}, action, { picks: state.picks.slice() });
    }
    return action;
  }
  // 没有显式选候选：用选中的手牌去匹配"使用这张牌"的候选。
  if (state.selectedCards.length === 1) {
    const cardId = state.selectedCards[0];
    return actions.find((action) => action.card && action.card.card_id === cardId) || null;
  }
  // 只有唯一候选时允许直接提交（例如结束阶段 / 放弃）。
  const direct = actions.filter((action) => ["EndPhase", "Pass"].includes(action.kind));
  return direct.length === 1 ? direct[0] : null;
}

async function submitAction() {
  const action = buildAction();
  if (!action) {
    $("t-choice").textContent = "没有可提交的候选：先选中一张牌或一个选项。";
    return;
  }
  const pending = state.view.pending;
  const blocked = selectionError(action, pending);
  if (blocked) {
    $("t-choice").textContent = blocked;
    return;
  }
  if (!pending.legal_actions.some((candidate) => JSON.stringify(candidate) === JSON.stringify(action))) {
    // 最后一道自检：造出来的必须**逐字段**是候选里的那一条（否则引擎会拒，且错在界面）。
    $("t-choice").textContent = "这个组合不在合法候选里，请换一个选择。";
    return;
  }
  try {
    const body = await post(
      `/api/sessions/${state.sessionId}/answer?after=${state.cursor}&logs=${state.logSeq}`,
      { action },
    );
    state.selectedCards = [];
    state.selectedOption = null;
    state.picks = [];
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
  await refresh();
  $("t-choice").textContent = "已请服务端判定一次（到时限即自动代答）。";
}

function clearSelection() {
  state.selectedCards = [];
  state.selectedOption = null;
  state.picks = [];
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

function wire() {
  seedOptions();
  $("in-players").addEventListener("change", seedOptions);
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
  $("btn-cancel").addEventListener("click", clearSelection);
  $("btn-refresh").addEventListener("click", refresh);
  $("btn-takeover").addEventListener("click", takeover);
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
  window.setInterval(() => {
    if (!$("view-table").hidden) renderCountdown(state.view);
  }, 1000);
}

wire();
