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

const state = {
  sessionId: null,
  cursor: -1,
  logSeq: 0,
  view: null,          // 服务端最近一次下发的会话视图
  focusCardId: null,   // **焦点**：正在挑目标的那张候选牌（再点一次取消）
  selectedTargets: [], // 已选目标座位
  selectedOption: null,// 旧路径：选中的 legal_action 下标（弃牌 / 排列 / 单选 / 技能）
  picks: [],           // SELECT_CARDS 选中的槽位下标
  timer: null,
  runs: [],
  replay: null,
  logRecords: [],
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

/** 清空全部未提交的选择（焦点 / 已选目标 / 旧路径的候选与槽位）。 */
function resetSelection() {
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.picks = [];
  state.lastRejectReason = "";
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

function cardEntry(pending, cardId) {
  return cardIndex(pending)[String(cardId)] || null;
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

  const ring = $("t-seats");
  ring.innerHTML = "";
  for (const player of snapshot.players) {
    const box = document.createElement("div");
    box.className = "seat";
    if (player.seat === snapshot.seat) box.classList.add("me");
    if (!player.alive) box.classList.add("dead");
    if (player.seat === snapshot.turn_seat) box.classList.add("turn");
    const seatState = seatOf(player.seat);
    if (handPick && !selfOnly) {
      if (seatState && seatState.selected) box.classList.add("tsel");
      else if (seatState && seatState.isTargetable) box.classList.add("targetable");
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
      for (const card of cards) row.appendChild(cardChip(card, false, true));
      box.appendChild(row);
    }
    if (seatState && !selfOnly) {
      if (seatState.selected || seatState.isTargetable) {
        box.addEventListener("click", () => selectTarget(player.seat));
      }
    } else if (optionChoiceAt(player.seat)) {
      // 选座位形态：只在座位上点，不在手牌上点（本窗口没有"选牌"这一步）。
      box.classList.add("targetable");
      box.addEventListener("click", () => selectOptionSeat(player.seat));
    }
    ring.appendChild(box);
  }

  $("t-processing").innerHTML = "";
  const processing = snapshot.processing || [];
  if (!processing.length) {
    const empty = document.createElement("span");
    empty.className = "dim";
    empty.textContent = "（空）";
    $("t-processing").appendChild(empty);
  } else {
    for (const card of processing) $("t-processing").appendChild(cardChip(card, false, true));
  }

  $("t-hand-count").textContent = `${snapshot.self_hand.length} 张`;
  const hand = $("t-hand");
  const discardPick = isDiscardPick(pendingNow);
  hand.innerHTML = "";
  for (const card of snapshot.self_hand) {
    if (discardPick) {
      // 弃牌阶段：点手牌 = 收进 / 移出待弃置集合；只该弃 `count` 张，选够后剩下的不再给收。
      const chosen = state.picks.includes(card.card_id);
      const full = state.picks.length >= (pendingNow.count || 0);
      hand.appendChild(cardChip(card, chosen, false, chosen || !full, () => toggleDiscardPick(card.card_id)));
      continue;
    }
    const usable = handPick ? cardSelectable(pendingNow, card.card_id) : cardUsable(pendingNow, card.card_id);
    hand.appendChild(cardChip(card, state.focusCardId === card.card_id, false, usable));
  }
  $("t-hero").textContent = view.heroes[snapshot.seat] || "（无武将）";
  renderSkills(view);
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
    const button = document.createElement("button");
    button.type = "button";
    button.className = "skill";
    if (skill.locked) button.classList.add("locked");
    if (skill.available) button.classList.add("ready");
    button.disabled = !skill.available;
    button.setAttribute("data-skill-id", skill.skill_id);
    button.setAttribute("data-available", String(skill.available));
    button.setAttribute("data-locked", String(skill.locked));
    const kind = skill.locked ? "锁定技 / 被动" : (skill.skill_type === "LORD" ? "主公技" : "主动技");
    button.innerHTML = `${skill.name}<span class="kind">${kind}${skill.available ? " · 可发动" : ""}</span>`;
    if (skill.available && pending) {
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
  if (!isDiscardPick(pending)) return;
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
  if (!isHandPick(pending) || state.focusCardId === null) return;
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
  if (!pending || pending.kind !== "SELECT_OPTION") return;
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
  if (!isHandPick(pending)) return;
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
  $("t-prompt").textContent = pending ? pending.prompt : "（没有待应答的询问）";
  // 询问窗口的"谁对谁做了什么"（服务端从结算栈顶帧派生；读不到就不写这一行）。
  const contextNode = $("t-context");
  if (contextNode) {
    const label = pending && pending.context && pending.context.label;
    contextNode.textContent = label ? `本窗口：${label}` : "";
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
  else if (kind === "slots") {
    // 选择面也要**指到那条候选**：引擎只发一条 `SelectCards` 骨架，具体 `picks` 由
    // `buildAction()` 补上；不指过去它就没有可补的对象 ⇒ `buildAction()` 返回 null ⇒
    // 点完槽位提交按钮仍然是灰的（本轮实测：点第 1 个槽位后 picks=[0] 而按钮不可点）。
    const index = actions.findIndex((action) => action.kind === "SelectCards");
    if (index >= 0) state.selectedOption = index;
    renderSlots(pending);
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
  const selected = state.selectedOption === null ? null : (pending.legal_actions || [])[state.selectedOption];
  const blocked = selected === null ? null : selectionError(selected, pending);
  const ready = blocked === null && buildAction() !== null;
  $("btn-submit").disabled = !ready;
  if (state.lastRejectReason) { $("t-choice").textContent = state.lastRejectReason; return; }
  if (blocked) { $("t-choice").textContent = blocked; return; }
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
        .filter(({ action }) => action.card && String(action.card.card_id) === String(state.focusCardId))
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
    const targets = state.selectedTargets.slice().sort((a, b) => a - b);
    return actions.find(
      (action) =>
        action.card
        && String(action.card.card_id) === String(state.focusCardId)
        && (action.kind === "UseCard" || action.kind === "PlayCard")
        && (action.targets || []).slice().sort((a, b) => a - b).join(",") === targets.join(","),
    ) || null;
  }
  return null;
}

async function submitAction() {
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
  await refresh();
  $("t-choice").textContent = "已请服务端判定一次（到时限即自动代答）。";
}

function clearSelection() {
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
