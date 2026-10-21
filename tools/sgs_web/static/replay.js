/* SGS 前端 · 回放页：录像逐条重建、座位全区、询问面板与播放控制。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------------ 录像 */

/** 一条录像的摘要行（开局页「已有录像」与回放页空壳引导**共用**这一份）。 */
function runRow(run) {
  const row = document.createElement("div");
  row.className = "run";
  const result = run.human_seat_won === true ? `<span class="win">我赢</span>`
    : run.human_seat_won === false ? `<span class="lose">我输</span>` : `<span class="dim">—</span>`;
  row.innerHTML = `<span><code>${run.name}</code> <span class="dim">种子 ${run.seed} · ${run.player_count} 人</span></span>
    <span>${result} <button type="button" class="ghost">看回放</button></span>`;
  const button = row.querySelector("button");
  if (button) button.addEventListener("click", () => openReplay(run.name));
  return row;
}

function renderRunList(box, runs) {
  if (!box) return;
  box.innerHTML = "";
  if (!runs || !runs.length) {
    box.innerHTML = `<p class="dim">还没有录像。打完一局就会出现在这里。</p>`;
    return;
  }
  for (const run of runs) box.appendChild(runRow(run));
}

async function loadRuns() {
  try {
    const body = await api("/api/runs");
    state.runs = body.runs || [];
    renderRunList($("run-list"), state.runs);
    renderRunList($("r-run-list"), state.runs);
  } catch (error) {
    const message = `读取录像列表失败：${error.message}`;
    for (const id of ["run-list", "r-run-list"]) {
      const box = $(id);
      if (box) box.innerHTML = `<p class="dim">${message}</p>`;
    }
  }
}

async function openReplay(name) {
  stopReplayPlay();
  try {
    const body = await api(`/api/runs/${encodeURIComponent(name)}`);
    state.replay = {
      name,
      meta: body.meta,
      records: body.records,
      // U1-05：服务端已按录像的人类座位过滤好的简易叙事；回放页只渲染。
      narrative: body.narrative || [],
      index: 0,
      seats: null,       // 逐座位：手牌 / 装备 / 判定 + 体力 / 上限 / 身份 / 存活
      query: null,       // 当前位置之前最近的一条询问（询问面板显示它）
      effectCards: [],   // 处理区 / 效果区（复用 applyEffectDelta 逐事件派生）
      appliedIndex: -1,  // 已经应用到第几条记录（向前播放只追加，回跳才整体重来）
      playing: false,
      timer: null,
    };
    // 每次打开录像都从**简易档**起步（票面默认）；选过完整档是本地偏好，不跨录像沿用。
    const modeNode = $("r-log-mode");
    if (modeNode) modeNode.value = "simple";
    show("replay");
    renderReplay();
  } catch (error) {
    $("status").textContent = `回放失败：${error.message}`;
  }
}

/** 回放页的派生状态清零：从第 0 条记录重新应用。 */
function resetReplayDerived(replay) {
  const count = Math.max(0, Number(replay.meta.player_count) || 0);
  replay.seats = Array.from({ length: count }, () => ({
    hand: [], equip: [], judge: [],
    hp: null, hpMax: null, alive: null, identity: null,
  }));
  replay.effectCards = [];
  replay.query = null;
  replay.appliedIndex = -1;
}

/** 逐座位区的**唯一**名单：牌移动落区与座位盒渲染都读它。 */
const REPLAY_SEAT_ZONES = [["hand", "手牌"], ["equip", "装备"], ["judge", "判定"]];

/** 这条牌移动落在哪个**逐座位**区；共享区（牌堆 / 弃牌堆 / 处理区）返回 `null`。 */
function replayZoneBucket(replay, ref) {
  if (!ref || ref.seat === null || ref.seat === undefined) return null;
  const seat = replay.seats[ref.seat];
  if (!seat) return null;
  if (REPLAY_SEAT_ZONES.some(([zone]) => zone === ref.zone)) return seat[ref.zone];
  return null;
}

/** 牌移动 → 逐座位区的增删（回放页的"全区域"就是这条增量重建）。 */
function applyReplayCardMove(replay, record) {
  if (!record || record.kind !== "delta" || record.delta_type !== "card_move") return;
  const cards = record.cards || [];
  const from = replayZoneBucket(replay, record.from_zone);
  if (from) {
    for (const card of cards) {
      const at = from.findIndex((held) => held.card_id === card.card_id);
      if (at >= 0) from.splice(at, 1);
    }
  }
  const to = replayZoneBucket(replay, record.to_zone);
  if (to) {
    for (const card of cards) {
      if (!to.some((held) => held.card_id === card.card_id)) to.push(card);
    }
  }
}

/** 体力 / 身份 / 存活：delta 是公开事实，询问视图补上"开局时就是满血"这类初值。 */
function applyReplaySeatDelta(replay, record) {
  if (!record || record.kind !== "delta") return;
  if (record.delta_type === "hp_change") {
    const seat = replay.seats[record.seat];
    if (seat) seat.hp = record.after;
    return;
  }
  if (record.delta_type === "identity_deal" || record.delta_type === "identity_reveal") {
    const seat = replay.seats[record.seat];
    if (seat) {
      seat.identity = record.identity;
      // 阵亡亮明身份是公开事实；它同时也说明这名角色已经不在场上了。
      if (record.delta_type === "identity_reveal") seat.alive = false;
    }
  }
}

/** 询问记录附带的 `PlayerView`（参与者视角；hp / hp_max / alive 本身是公开事实）：
 *  补上"开局就是满血"这类初值——记录流里没有一条"初始体力"delta。 */
function noteReplayQuery(replay, query) {
  replay.query = query;
  const view = query && query.view;
  for (const player of (view && view.players) || []) {
    const seat = replay.seats[player.seat];
    if (!seat) continue;
    if (player.hp !== undefined) seat.hp = player.hp;
    if (player.hp_max !== undefined) seat.hpMax = player.hp_max;
    if (player.alive !== undefined) seat.alive = player.alive;
  }
}

/** 把回放推进到 `index`：只应用**新**记录；往回跳时整体重建。
 *
 * 效果区**复用**牌桌的 :func:`applyEffectDelta`：它收一份牌组、返回新牌组，回放页和牌桌
 * 各持自己那一份，谁都不读对方的全局状态。
 */
function applyReplayThrough(replay, index) {
  if (replay.appliedIndex === undefined || replay.appliedIndex < 0 || index < replay.appliedIndex) {
    resetReplayDerived(replay);
  }
  for (let i = replay.appliedIndex + 1; i <= index; i += 1) {
    const record = replay.records[i];
    if (!record) continue;
    applyReplayCardMove(replay, record);
    applyReplaySeatDelta(replay, record);
    if (record.kind === "query") noteReplayQuery(replay, record.query);
    replay.effectCards = applyEffectDelta(record, replay.effectCards, null);
  }
  replay.appliedIndex = index;
}

/** 一个逐座位区的牌面行：空区也画出来（"全区域"不是"有牌才出现"）。 */
function replayZoneRow(cards, zone, seat) {
  const row = document.createElement("div");
  row.className = "cards mini";
  row.setAttribute("data-zone", zone);
  row.setAttribute("data-seat", String(seat));
  if (!cards || !cards.length) {
    const empty = document.createElement("span");
    empty.className = "dim";
    empty.textContent = "（空）";
    row.appendChild(empty);
    return row;
  }
  for (const card of cards) row.appendChild(cardChip(card, false, true));
  return row;
}

function renderReplaySeats(replay) {
  const box = $("r-seats");
  if (!box) return;
  box.innerHTML = "";
  const heroes = replay.meta.heroes || [];
  const humanSeat = replay.meta.human_seat;
  replay.seats.forEach((seatState, seat) => {
    const node = document.createElement("div");
    node.className = "seat";
    node.setAttribute("data-seat", String(seat));
    const isHuman = humanSeat !== null && humanSeat !== undefined && Number(humanSeat) === seat;
    if (isHuman) node.classList.add("me");
    const hp = seatState.hp === null || seatState.hp === undefined
      ? "—"
      : `${seatState.hp} / ${seatState.hpMax === null || seatState.hpMax === undefined ? "?" : seatState.hpMax}`;
    const identity = seatState.identity ? identityLabel(seatState.identity) : "—";
    const alive = seatState.alive === false ? " · 已阵亡" : "";
    node.innerHTML = `<div class="top"><span class="who">座位 ${seat}${isHuman ? "（本局人类）" : ""}</span>
      <span class="small">体力 ${hp}${alive}</span></div>
      <div class="small">${heroes[seat] || "（无武将）"} · 身份 ${identity}</div>`;
    for (const [zone, label] of REPLAY_SEAT_ZONES) {
      const cards = seatState[zone] || [];
      const line = document.createElement("div");
      line.className = "small";
      line.textContent = `${label} ${cards.length}`;
      node.appendChild(line);
      node.appendChild(replayZoneRow(cards, zone, seat));
    }
    box.appendChild(node);
  });
}

/** 询问面板：回放页把**记录里**的询问按牌桌的版式铺开（文案 + 候选 + 选择面）。
 *
 * 清单（`preactivations` / `preactivation`）不在记录流里，这里**不**画清单；确认发动在
 * 流里的形态就是一条复合 `ActivateSkill`，事件流照常显示它。
 */
function renderReplayQuery(query) {
  const meta = $("r-query-meta");
  const prompt = $("r-query-prompt");
  const options = $("r-query-candidates");
  const slots = $("r-query-slots");
  if (!prompt || !options) return;
  options.innerHTML = "";
  if (slots) slots.innerHTML = "";
  if (!query) {
    if (meta) meta.textContent = "（还没到第一次询问）";
    prompt.textContent = "（还没到第一次询问）";
    return;
  }
  if (meta) {
    const zones = (query.spec && query.spec.zones) || [];
    const where = zones.length ? ` · 区域 ${zones.join("、")}` : "";
    meta.textContent = `#${query.seq} · 座位 ${query.actor} · ${query.kind}${where}`;
  }
  prompt.textContent = query.prompt || "（本次询问没有文案）";
  for (const action of query.legal_actions || []) {
    const row = document.createElement("div");
    row.className = "opt static";
    row.setAttribute("data-kind", action.kind);
    row.textContent = actionLabel(action);
    options.appendChild(row);
  }
  if (!slots) return;
  for (const slot of query.slots || []) {
    const chip = document.createElement("div");
    chip.className = "card static";
    const ref = slot.ref || {};
    const where = ref.seat === null || ref.seat === undefined
      ? String(ref.zone || "")
      : `${ref.zone} · 座位 ${ref.seat}`;
    chip.innerHTML = `<span class="n">${slot.card ? slot.card.name : "？"}</span><span class="s">${where}</span>`;
    slots.appendChild(chip);
  }
}

/** 回放事件流的详略：默认 `simple`（与牌桌同一份服务端叙事、按**录像的人类座位**过滤）。
 *
 * `all` 是既有的上帝视角逐条记录；U1 没有座位选择器 / 上帝视角开关（U2），故这里只有
 * 两档——不给完整档加一套新的过滤语义。U2 接查看者时只需换 `viewer` 与选项。
 */
function replayLogMode() {
  const node = $("r-log-mode");
  const mode = node && node.value ? node.value : "simple";
  return mode === "all" ? "all" : "simple";
}

function renderReplayLog(replay) {
  const log = $("r-log");
  if (!log) return;
  log.innerHTML = "";
  if (replayLogMode() === "simple") {
    const current = replay.records[replay.index] || {};
    // 首条 `header` 没有 `seq`：它之前没有叙事（不能把 `Infinity` 当成"已到终局"，
    // 否则刚打开回放就会把整局事件流提前铺完）。
    const limit = typeof current.seq === "number" ? current.seq : 0;
    const entries = (replay.narrative || []).filter((entry) => entry.seq <= limit);
    const from = Math.max(0, entries.length - 60);
    for (let i = from; i < entries.length; i += 1) appendNarrativeEntry(entries[i], log);
  } else {
    const from = Math.max(0, replay.index - 60);
    let querySeat = null;
    for (let i = 0; i <= replay.index; i += 1) {
      const record = replay.records[i];
      if (!record) continue;
      if (record.kind === "query") querySeat = record.query.actor;
      if (i < from) continue;
      appendLog(record, querySeat, {
        log,
        mode: "all",
        humanSeat: replay.meta.human_seat,
        banner: i === replay.index,
      });
    }
  }
  scrollLogToBottom(log);
}

function updateReplayPlayButton() {
  const button = $("btn-r-play");
  if (!button) return;
  const playing = Boolean(state.replay && state.replay.playing);
  button.textContent = playing ? "⏸ 暂停" : "▶ 播放";
  button.setAttribute("data-playing", String(playing));
}

/** 停下回放播放并清掉计时器（可重复调用；离开回放页也会走这里）。 */
function stopReplayPlay() {
  const replay = state.replay;
  if (!replay) return;
  replay.playing = false;
  if (replay.timer !== null && replay.timer !== undefined) {
    if (window.clearTimeout) window.clearTimeout(replay.timer);
    replay.timer = null;
  }
  updateReplayPlayButton();
}

function scheduleReplayTick() {
  const replay = state.replay;
  if (!replay || !replay.playing) return;
  replay.timer = window.setTimeout(() => {
    replay.timer = null;
    if (!replay.playing || state.replay !== replay) return;
    if (replay.index >= replay.records.length - 1) {
      stopReplayPlay();
      return;
    }
    replayStep(1);
    if (replay.playing) scheduleReplayTick();
  }, state.eventDelayMs);
}

/** 播放 / 暂停：再点一次就停，**不会**叠出第二个计时器。 */
function toggleReplayPlay() {
  const replay = state.replay;
  if (!replay) return false;
  if (replay.playing) {
    stopReplayPlay();
    return false;
  }
  if (replay.index >= replay.records.length - 1) {
    replay.index = 0;
    renderReplay();
  }
  replay.playing = true;
  updateReplayPlayButton();
  scheduleReplayTick();
  return true;
}

function replaySeek(index) {
  const replay = state.replay;
  if (!replay) return;
  replay.index = Math.max(0, Math.min(replay.records.length - 1, Number(index) || 0));
  renderReplay();
}

/** 回放页总渲染：空壳引导 / 录像头 / 座位与全区域 / 询问面板 / 效果区 / 事件流。 */
function renderReplay() {
  const replay = state.replay;
  const empty = $("r-empty");
  const body = $("r-body");
  if (!replay) {
    if (empty) empty.hidden = false;
    if (body) body.hidden = true;
    const guide = $("r-empty-guide");
    if (guide) {
      guide.textContent = "提示：回放是「上帝视角」——能看见询问、每人的手牌 / 装备 / 判定区，"
        + "以及处理区 / 效果区。从下面挑一局；也可以去「开局」打完一局，终局后会存录像。";
    }
    renderRunList($("r-run-list"), state.runs || []);
    updateReplayPlayButton();
    return;
  }
  if (empty) empty.hidden = true;
  if (body) body.hidden = false;
  replay.index = Math.max(0, Math.min(replay.records.length - 1, replay.index));
  applyReplayThrough(replay, replay.index);

  $("r-name").textContent = replay.name;
  $("r-seed").textContent = String(replay.meta.seed);
  $("r-heroes").textContent = (replay.meta.heroes || [])
    .map((hero, seat) => `${seat}:${hero || "—"}`)
    .join("  ");
  const humanSeat = replay.meta.human_seat;
  $("r-human").textContent = humanSeat === null || humanSeat === undefined
    ? "全是 AI"
    : `座位 ${humanSeat}`;

  renderReplaySeats(replay);
  renderReplayQuery(replay.query);
  renderEffectCards($("r-processing"), replay.effectCards);
  const effectText = $("r-effect");
  if (effectText) effectText.textContent = describeEffect(replay.effectCards);

  const record = replay.records[replay.index] || {};
  $("r-pos").textContent = `${replay.index + 1} / ${replay.records.length}（seq ${record.seq || "-"}）`;
  renderReplayLog(replay);
  updateReplayPlayButton();
}

function replayStep(delta) {
  const replay = state.replay;
  if (!replay) return;
  replay.index = Math.max(0, Math.min(replay.records.length - 1, replay.index + delta));
  renderReplay();
}
