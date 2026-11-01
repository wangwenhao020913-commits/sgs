/* SGS 前端 · 牌桌：座位 / 手牌 / 处理区 / 技能按钮渲染。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------------ 牌桌布局 */

/** 环桌几何（U1-01）：1600×608 的设计舞台 + 椭圆 rx 680 / ry 235、圆心 (800, 370)。
 *
 * 数字来自原型 verdict（`.scratch/u1-table-foundation/prototype-notes.md`）：
 * 8 人局 7 个对手沿**上半弧**均布、卡面 210×150 时无正面积重叠。上半弧 = −30° → 210°，
 * **座位号 +1 在自己的右手边**（−30° 在屏幕右下），因此对手按「自己 + 1、+2 …」逆时针排。
 * U2-02 / D6 的 +8%（228×163）会与相邻座位重叠：40° 相邻角的 |dx| ≈ 80.8 < 228、
 * |dy| ≈ 158.3 < 163，故座位卡取「外形不变、只放大内容」的回退（见 style.css）。
 * 2 人局只有一个对手：正上方（90°）。
 *
 * 只算**设计坐标**（相对 1600×608 舞台的像素）；渲染时再换算成百分比，
 * 于是 16:9 壳整体缩放时座位比例不变（`data-ring-x` / `data-ring-y` 落的就是这个坐标，
 * 烟测用它算矩形、断言无重叠）。
 */
const RING_STAGE = { width: 1600, height: 608, cx: 800, cy: 370, rx: 680, ry: 235 };

function ringLayout(playerCount) {
  const count = Number(playerCount) || 0;
  if (count < 2) return [];
  const opponents = count - 1;
  const out = [];
  for (let index = 0; index < opponents; index += 1) {
    const angle = opponents === 1 ? 90 : -30 + (240 * index) / (opponents - 1);
    const rad = (angle * Math.PI) / 180;
    out.push({
      seatOffset: index + 1,
      angle,
      x: RING_STAGE.cx + RING_STAGE.rx * Math.cos(rad),
      y: RING_STAGE.cy - RING_STAGE.ry * Math.sin(rad),
    });
  }
  return out;
}

/** 公开身份配比（4~8 人身份局）。**显示副本**：真源住 `src/sgs/data/identities.py`，
 *  这里只用于顶栏那块公开信息，不参与任何合法性判定。2 人局 = 无身份局，返回 null。
 *  与 `IDENTITY_ROSTERS` 的一致性由 `tests/test_web_static.py` 机械核（漂移即红）。 */
const IDENTITY_RATIOS = {
  4: [1, 1, 1, 1],
  5: [1, 1, 2, 1],
  6: [1, 1, 3, 1],
  7: [1, 2, 3, 1],
  8: [1, 2, 4, 1],
};

function identityRatioText(playerCount) {
  const counts = IDENTITY_RATIOS[playerCount];
  if (!counts) return null;
  const [lord, loyalist, rebel, renegade] = counts;
  return `主公×${lord} 忠臣×${loyalist} 反贼×${rebel} 内奸×${renegade}`;
}

/** 当前是第几轮：**显示用**的计数——同一座位第二次 `TURN_START` 即进入下一轮。
 *
 * M6 票 11 起引擎有真正的轮次（`state.round`，额外回合不推进自然指针），票 19 起由
 * 投影下发 `snapshot.round`；本函数只在**旧录像 / 合成视图**（没有那个键）时兜底：
 * 按「本轮已经行动过的座位」记，座位重复出现 = 转了一圈。两种数法都只是显示，
 * 顶栏不拿它做任何判据。
 */
function roundNumber(records) {
  let round = 1;
  const seen = new Set();
  for (const record of records || []) {
    if (!record || record.kind !== "timing" || record.timing !== "TURN_START") continue;
    const seat = record.actor;
    if (seat === null || seat === undefined) continue;
    if (seen.has(seat)) {
      round += 1;
      seen.clear();
    }
    seen.add(seat);
  }
  return round;
}

/** 顶栏轮次文本：服务端 `snapshot.round`（引擎事实）优先，缺键时退回本地数法。 */
function roundLabelOf(snapshot, records) {
  const server = snapshot ? snapshot.round : null;
  if (server !== null && server !== undefined && Number.isFinite(Number(server))) {
    return String(Number(server));
  }
  return String(roundNumber(records));
}

/** 座位交互态的类全集 / `applySeatState` 住 `seat-view.js`（U1-02）；环上与底部面板共用一份。 */

/** 座位卡的交互态（可指定 / 已选 / 必选）与点击行为，对手与自己的面板共用一份判据。 */
function seatInteraction(player, ctx) {
  const { seatOf, handPick, selfOnly, optionChoiceAt, preSeats, pendingNow } = ctx;
  // 其他座位的询问只显示文本（U1-03）：座位不给可指定 / 可点语义。
  if (pendingNow && !ctx.ownPending) return { classes: [], handler: null };
  // 只读态（D8）：这一步的表面是弹窗 ⇒ 不给可指定 / 已选高亮，也不接点击 / 键盘。
  if (!selectionEnabled()) return { classes: [], handler: null };
  const classes = [];
  let handler = null;
  const seatState = seatOf(player.seat);
  if (state.preactivation) {
    if (preSeats && preSeats.includes(`${SEAT_PREFIX}${player.seat}`)) {
      const answer = state.preactivation.answers[state.preactivation.cursor];
      const required = preactivationRequiredSeat(
        state.preactivation, pendingNow, state.preactivation.cursor);
      if (required === `${SEAT_PREFIX}${player.seat}`) classes.push("req");
      if (answer && answer.values.includes(player.seat)) classes.push("tsel");
      else classes.push("targetable");
      handler = () => selectPreativationSeat(player.seat);
    }
  } else if (handPick && !selfOnly) {
    if (seatState && seatState.selected) {
      classes.push("tsel");
      handler = () => selectTarget(player.seat);
    } else if (seatState && seatState.isTargetable) {
      classes.push("targetable");
      handler = () => selectTarget(player.seat);
    }
  } else {
    const optionChoice = optionChoiceAt(player.seat);
    if (optionChoice !== null) {
      // 选座位形态（【遗计】"交给谁"）：座位可点，选中的那座画成确定态。
      const optionSelected = state.selectedOption !== null
        && (pendingNow.legal_actions[state.selectedOption] || {}).choice === optionChoice;
      if (optionSelected) classes.push("tsel");
      else classes.push("targetable");
      handler = () => selectOptionSeat(player.seat);
    }
  }
  return { classes, handler };
}

/** 环上座位：只画**对手**（自己 = 底部独立面板，不占环上座位）。 */
function renderRingSeats(view, ctx) {
  const snapshot = view.snapshot;
  const ring = $("t-seats");
  ring.innerHTML = "";
  const layout = ringLayout(snapshot.players.length);
  const bySeat = new Map(snapshot.players.map((player) => [player.seat, player]));
  for (let offset = 1; offset < snapshot.players.length; offset += 1) {
    const player = bySeat.get((snapshot.seat + offset) % snapshot.players.length);
    const position = layout[offset - 1];
    if (!player || !position) continue;
    ring.appendChild(seatCardNode(player, position, view, ctx));
  }
}

/** 自己的底部面板：武将块（左）+ 判定 / 装备区（右）。节点是常驻的，故只改内容不重建，
 *  以免抹掉 `#t-hero` 这类稳定 id（回放 / 烟测都按 id 找它）。 */
function renderSelfSeat(view, ctx) {
  const snapshot = view.snapshot;
  const player = snapshot.players.find((item) => item.seat === snapshot.seat);
  const box = $("t-self-seat");
  if (!player || !box) return;
  const { classes, handler } = seatInteraction(player, ctx);
  box.setAttribute("data-seat", String(snapshot.seat));
  applySeatState(box, ctx.flagsFor(player, classes));
  // 静态节点复用：`onclick` 是覆盖式绑定；环上节点也用同一条路，避免两套接法。
  // 点击 / 键盘（Tab + 回车 / 空格）走同一个 handler（`bindSeatActivation`）。
  bindSeatActivation(box, handler);
  const kingdom = $("t-self-kingdom");
  if (kingdom) {
    kingdom.textContent = player.kingdom_label || "";
    kingdom.className = `kingdom ${player.kingdom || ""}`;
    kingdom.setAttribute("title", player.kingdom_label ? `势力：${player.kingdom_label}` : "");
  }
  // 武将名读服务端展示契约（U1-02），旧载荷退回武将 id。
  $("t-hero").textContent = heroNameOf(player, view);
  const badge = identityBadgeView(player, ctx.identityMode);
  const identity = $("t-self-identity");
  identity.textContent = badge.label;
  identity.className = badge.className;
  identity.setAttribute("title", badge.title);
  const hp = hpBarNode(player);
  const hpBox = $("t-self-hp");
  hpBox.innerHTML = "";
  hpBox.className = hp.className;
  // `hp.children` 是**活集合**：appendChild 会把它搬走，边遍历边搬会漏掉一半节点。
  for (const segment of [...hp.children]) hpBox.appendChild(segment);
  fillSeatTags($("t-self-tags"), ctx.flagsFor(player, classes));
  if ($("t-self-chain")) $("t-self-chain").hidden = !player.chained;
  if ($("t-self-dead")) $("t-self-dead").hidden = Boolean(player.alive);
  $("t-self-hand").textContent = String(player.hand_count);
  $("t-self-seatno").textContent = String(player.seat);
  const zones = $("t-self-zones");
  if (zones) {
    zones.innerHTML = "";
    for (const row of sideBlockRows(player, ctx)) zones.appendChild(row);
  }
  // M6 票 19：武将牌上的牌区画在**武将块**里（与对手块同一个渲染口）。
  fillHeroZones($("t-self-hero-zones"), player);
  fillHeroTags($("t-self-hero-tags"), player);
}

/** 顶栏那块公开身份配比：2 人局（无身份局）整块隐藏。 */
function renderIdentityPanel(playerCount) {
  const panel = $("t-identity-panel");
  const ratio = $("t-identity-ratio");
  const text = identityRatioText(playerCount);
  if (panel) panel.classList.toggle("hidden", text === null);
  if (ratio) ratio.textContent = text === null ? "" : text;
}

/* ------------------------------------------------------------------ 牌桌 */

/** 牌桌渲染读的视图（U3-05 / D11）：回放期间 = 逐事件派生的 `state.liveView`，其余时候 =
 *  权威提交视图（`commitView` 会把 live view 覆盖成它）。
 *
 *  回放期间 `pending` **收起**：上一问的「正在响应…」、技能可点态、手牌可点态都属于旧询问，
 *  新询问要等这批事件播完提交（spec D11）。技能可用性等不可派生字段仍留在 live view 里，
 *  只是不画 pending 派生的那一层。
 */
function tableViewForRender() {
  if (state.playback && state.liveView) {
    return { ...state.liveView, pending: null };
  }
  return state.view;
}

function renderTable() {
  const view = tableViewForRender();
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
  const rounds = $("t-round");
  if (rounds) {
    rounds.textContent = roundLabelOf(
      snapshot,
      state.logRecords.length ? state.logRecords : (view.records || []),
    );
  }
  // M6 票 19：额外回合提示（票 11 的 `turn_extra`）。自然回合不挂这个标记。
  const extraTurn = $("t-extra-turn");
  if (extraTurn) extraTurn.hidden = !snapshot.turn_extra;
  renderIdentityPanel(snapshot.players.length);

  const pendingNow = view.pending || null;
  const ownPending = isOwnPending(pendingNow);
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
  const preStep = state.preactivation && state.preactivation.cursor < state.preactivation.steps.length
    ? state.preactivation.steps[state.preactivation.cursor]
    : null;
  const preSeats = preStep && preactivationStepKind(preStep) === "seat"
    ? (preactivationOptions(state.preactivation, state.preactivation.cursor)
      || snapshot.players.filter((player) => player.alive).map((player) => `${SEAT_PREFIX}${player.seat}`))
    : null;
  // U1-02：座位卡的状态类（自己 / 阵亡 / 当前回合 / 横置 / 酒）+ 交互态（可指定 / 已选 /
  // 必选）在一处拼齐，环上与底部面板因此不会各写一套判据。2 人局 = 无身份局 ⇒ 不画「?」。
  const identityMode = snapshot.players.length > 2;
  const turnSeatNow = snapshot.turn_seat;
  // `turn_seat` 可以是 null（回合之外 / 终局）：`Number(null) === 0` 会把 0 号位误标成
  // 「行动中」，故先显式排除空值再比。
  const isTurnSeat = (seat) => turnSeatNow !== null && turnSeatNow !== undefined
    && Number(seat) === Number(turnSeatNow);
  const flagsFor = (player, extra = []) => [
    ...(Number(player.seat) === Number(snapshot.seat) ? ["me"] : ["opponent"]),
    ...(player.alive ? [] : ["dead"]),
    ...(isTurnSeat(player.seat) ? ["turn"] : []),
    ...(player.chained ? ["chained"] : []),
    ...(player.flipped ? ["flipped"] : []),
    ...(player.wine_active ? ["wine"] : []),
    ...extra,
  ];
  const ctx = {
    seatOf, handPick, selfOnly, optionChoiceAt, preSeats, pendingNow, ownPending,
    snapshot, identityMode, flagsFor,
  };

  renderRingSeats(view, ctx);
  renderSelfSeat(view, ctx);
  // U3-03：座位节点在这一屏被重建；仍在播的行动者描边 / 体力闪按状态补回类（D13）。
  syncTransientEffects();

  // 快照到达时把处理区并进 `state.effectCards`（同一张牌保留回放期间的使用者标注）；
  // 回放期间不并——那份是逐事件派生的，快照要等播完才提交。
  if (!state.playback) state.effectCards = mergeEffectCards(snapshot.processing || []);
  renderProcessing();

  // 手牌区的标题行（旧 `#t-hand-count`）已删（票 23 报障）：它占的那一行高度让提示条
  // 越长越压住牌面顶部的花色 / 点数。手牌张数照旧看自己的武将块（`#t-self-hand`，
  // 由座位渲染写入 `hand_count`），这里不再重复一遍。
  const hand = $("t-hand");
  hand.innerHTML = "";
  for (const card of snapshot.self_hand) {
    // 置灰 / 选中**只**由 :func:`cardFace` 从"当前询问 + 这张牌"算出（票 14 §D）：
    // 弃牌、技能手选、提供 B、普通手选四条路都在那一个函数里分流，这里不再各写一份。
    // 回放期间不画上一问的选择态（焦点 / 待弃 / 预发动）——它们属于旧询问（D11）。
    const face = state.playback
      ? { selectable: false, selected: false, picked: false, slotIndex: -1 }
      : cardFace(pendingNow, card);
    // 被**强制转化**的手牌（武神）：画产物牌面（「转」+ 属性色），但身份仍是那张源牌
    // ——`card_id` 覆盖回源牌，点选 / 提交 / 置灰都还按源牌走。
    const form = state.playback ? null : convertedForm(pendingNow, card.card_id);
    hand.appendChild(
      cardChip(
        form ? { ...form, card_id: card.card_id } : card, face.selected, false, face.selectable,
        () => activateCard(pendingNow, card, face), face.picked,
      ),
    );
  }
  renderSkills(view);
  // 提示区左栏的技能按钮组：与底部技能区同一份清单 / 判据（U2-04），additive。
  renderPromptSkills(view);
  // U4-05（D11）：弃牌堆面板开着时，每次提交 / 回放步都从同一份视图重画（关着时 no-op）。
  renderDiscardPanel();
}

/* --------------------------------------------------- 弃牌堆面板（U4-05 / D11） */

/** 只读面板正文：时间倒序（最新在上），每行一张牌面（花色 + 点数 + 名）。
 *
 * 开合状态住 `modal.js`（`discardPanelIsOpen()`），本函数在关着时 **no-op**，
 * 因而可以直接挂在 `renderTable()` 的末尾做"面板开着时每次提交 / 回放步自动重画"。
 *
 * 数据源与表体同一份视图（`tableViewForRender()`）：回放期间是逐事件派生的 live view，
 * 其余是权威视图；**不**读引擎 / 记录流，也不给来源座位 / 区域。服务端
 * `snapshot.discard` 按**置入序**给牌面，反转即时间倒序。
 */
function renderDiscardPanel() {
  if (!discardPanelIsOpen()) return;
  const view = tableViewForRender();
  const snapshot = view && view.snapshot;
  const discard = (snapshot && snapshot.discard) || [];
  const summary = $("t-discard-summary");
  if (summary) summary.textContent = `共 ${discard.length} 张`;
  const body = $("t-discard-body");
  if (!body) return;
  body.innerHTML = "";
  body.scrollTop = 0;
  if (!discard.length) {
    const empty = document.createElement("p");
    empty.className = "dim discard-empty";
    empty.textContent = "弃牌堆里还没有牌。";
    body.appendChild(empty);
    return;
  }
  // N 多时纯滚动（CSS：正文 `overflow-y: auto`），不做分组、不做分页。
  for (const card of [...discard].reverse()) {
    const face = cardFaceNode(card, { static: true });
    face.classList.add("discard-card");
    body.appendChild(face);
  }
}

/* --------------------------------------------------- 中央舞台（U3-01 / D2–D5） */

/** 牌桌当前的武将名表：底行「座位号·武将名·区域」读 `snapshot.players[].hero_name`
 *  （U1-02 的展示契约，D10）。缺名时底行只写座位号 + 区域，不编一个名字。
 *
 *  读的是**牌桌正在渲染的那份视图**（:func:`tableViewForRender`）：回放期间 = 逐事件派生的
 *  live view，不是上一次提交的 `state.view`——底行与表体因此不会各读一份视图。 */
function stageHeroNameOf(seat) {
  const view = tableViewForRender();
  const snapshot = view && view.snapshot;
  const player = ((snapshot && snapshot.players) || []).find(
    (item) => Number(item.seat) === Number(seat));
  return (player && player.hero_name) || "";
}

/** 舞台重绘的计时器：**只负责到点重绘**，判定在 :func:`stageLane`（D5）。 */
let stageTimer = null;

function scheduleStageRender(now = stageNow()) {
  if (stageTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(stageTimer);
    stageTimer = null;
  }
  const items = (state.effectCards || []).concat(state.revealedCards || []);
  const next = nextStageDeadline(items, now);
  if (next === null) return;
  stageTimer = window.setTimeout(() => {
    stageTimer = null;
    renderProcessing();
  }, Math.max(0, next - now));
}

/** 中央舞台：把三组按入台顺序画进**同一个**换行流，并同步描述文本。
 *
 * `now` 参数是寿命判定的唯一时间输入（测试直接喂 `now`，不等真实时钟；D15）。
 */
function renderProcessing(now = stageNow()) {
  const box = $("t-processing");
  const reveals = stageRevealCards(now);
  const items = stageCardItems(state.effectCards || [], reveals, now);
  renderEffectCards(box, items, now);
  bindStageScroll(box);
  keepStagePinned(box);
  const contextNode = $("t-context");
  if (contextNode) contextNode.textContent = describeStage(now);
  renderStageAction(now);
  scheduleStageRender(now);
}

/** 中央舞台描述文本的**唯一派生点**：处理中 / 展示 / 弃牌三段（`renderPending` 也读它）。 */
function describeStage(now = stageNow()) {
  return describeStageGroups(state.effectCards || [], stageRevealCards(now), now);
}

/** 中央舞台要画的**公开亮牌**（M5-08a / U3-01）：记录流派生的那一份 + 当前询问自带的公开事实。
 *
 * 后者（``pending.reveal``）是服务端为「火攻弃牌步」下发的已亮牌：用户刷新 / 断线重连时
 * 记录可能已不在增量里，但这一步仍要看得见亮出的那一张。两者按牌 id 去重。
 * 它没有承载帧记录，故按**当前询问还挂着**这个事实展示（不入 `state.revealedCards`，
 * 换问即消失）；寿命仍由 `stageLane` 判定，不在渲染里特判。
 */
function stageRevealCards(now = stageNow()) {
  const cards = (state.revealedCards || []).slice();
  // 回放期间不画上一问的 `pending.reveal`：它属于旧询问，舞台此时该显示记录流派生的那几组。
  const pending = state.playback ? null : (state.view && state.view.pending);
  const reveal = pending && pending.reveal;
  const card = reveal && reveal.card;
  if (card && !cards.some((existing) => String(existing.card_id) === String(card.card_id))) {
    cards.push({
      ...stageItem(card, {
        lane: "showcase",
        frameId: 0,
        enteredAt: now,
        fromZone: { seat: reveal.seat, zone: "hand" },
      }),
      revealedBy: reveal.seat,
    });
  }
  return cards;
}

/** 中央舞台上层 = 当前动作（最近一条应答的人话；没有就写"等待行动"）。
 *
 * **结算完淡出**用「处理中组是否还有牌」当判据（无计时器）：牌还在处理区 = 正在结算
 * （不透明），牌全走了 = 这条动作已经落定（`.faded` 半透明 + CSS 过渡）。历史仍进事件流。
 */
function renderStageAction(now = stageNow()) {
  const textNode = $("t-action-text");
  if (!textNode) return;
  const records = state.logRecords || [];
  let lastAction = null;
  for (let index = records.length - 1; index >= 0; index -= 1) {
    if (records[index] && records[index].kind === "action") {
      lastAction = records[index];
      break;
    }
  }
  textNode.textContent = lastAction ? describeAction(lastAction.action) : "等待行动";
  const processing = (state.effectCards || []).filter(
    (item) => item.stage && item.stage.lane === "processing" && stageLane(item, now) === "visible",
  );
  const layer = $("t-action-layer");
  if (layer) layer.classList.toggle("faded", processing.length === 0);
}

/** 舞台行「粘底」（D3）：新牌落位时自动滚到可见；用户手动上滚过之后不再打扰。 */
function bindStageScroll(box) {
  if (!box || box.getAttribute("data-stage-scroll") === "bound") return;
  box.setAttribute("data-stage-scroll", "bound");
  box.addEventListener("scroll", () => {
    const top = Number(box.scrollTop) || 0;
    const height = Number(box.clientHeight) || 0;
    const bottom = Number(box.scrollHeight) || 0;
    box.setAttribute("data-stage-pinned", top + height >= bottom - 4 ? "true" : "false");
  });
}

function keepStagePinned(box) {
  if (!box || box.getAttribute("data-stage-pinned") === "false") return;
  box.scrollTop = Number(box.scrollHeight) || 0;
}

/** 节点是否带某个类（垫片 / 旧代码用 `className`，`classList` 未必同步；真 DOM 两者一致）。 */
function nodeHasClass(node, name) {
  if (!node) return false;
  if (node.classList && node.classList.contains(name)) return true;
  return String(node.className || "").split(/\s+/).includes(name);
}

/** 一张舞台卡面：复用 88×124 卡面（`cardFaceNode`），底行改写成来源，左侧色条由 CSS 给。 */
function stageCardNode(item, heroNameOf) {
  const label = stageZoneLabel(item, heroNameOf);
  const node = cardFaceNode(item, {
    static: true,
    bottomLine: label,
    zoneLabel: label || undefined,
  });
  node.classList.add("stage-card");
  node.setAttribute("data-stage-key", String(item.card_id));
  return node;
}

/** 淡出动画的**续播锚点**：把 CSS `animation` 直接推到 `now − fadeAt` 的位置。
 *
 * `renderEffectCards` 每次重绘都会把节点 `appendChild` 回容器（保持入台顺序）；对已有
 * 子节点而言这是"移出再插入"——浏览器的 CSS animation 会**从头播**，表现为淡出过程中
 * 反复闪回 opacity 1。负 `animation-delay` 让每次插入都从"该在的那一帧"开始：`now`
 * 越大 delay 越负，进度单调前进。淡出中新出生的节点同样适用，不依赖上一帧的计算样式。
 *
 * 时长与 `style.css` 的 1s / `session.js:STAGE_FADE_MS` 同一份；`animScale` 只缩 JS
 * 寿命（`stageLane`），不缩 CSS 动画。
 */
function syncStageCardFade(node, item, now) {
  if (!node || !node.style) return;
  const stage = item && item.stage;
  if (!stage || stageLane(item, now) !== "fading") {
    node.style.animationDelay = "";
    return;
  }
  const at = Number(now);
  const elapsed = Number.isFinite(at)
    ? Math.max(0, Math.min(STAGE_FADE_MS, at - Number(stageFadeAt(stage))))
    : 0;
  node.style.animationDelay = `-${elapsed}ms`;
}

/** 更新一张已经在台上的卡面：道 / 淡出类 / 底行 / 展示者角标（只改变的部分）。 */
function updateStageCardNode(node, item, now, heroNameOf) {
  const stage = item.stage;
  node.setAttribute("data-stage-lane", stage.lane);
  const lane = stageLane(item, now);
  node.classList.toggle("fading", lane === "fading");
  syncStageCardFade(node, item, now);
  node.classList.toggle("revealed-card", stage.lane === "showcase");
  const label = stageZoneLabel(item, heroNameOf);
  // 真 DOM 的 `node.children` 是 HTMLCollection（无 `.find`）；`Array.from` 对数组同样成立。
  const line = Array.from(node.children || []).find((child) => nodeHasClass(child, "ctype"));
  if (line) {
    if (line.textContent !== label) line.textContent = label;
    if (label) line.setAttribute("data-zone-label", label);
    else line.removeAttribute("data-zone-label");
  }
  const seat = item.revealedBy !== undefined ? item.revealedBy : item.revealSeat;
  if (stage.lane === "showcase" && seat !== undefined && seat !== null) {
    node.setAttribute("data-reveal-seat", String(seat));
  } else {
    node.removeAttribute("data-reveal-seat");
  }
}

/** A1：一条记录是不是"判定牌进处理区"，是则返回刚入台的判定牌。
 *
 * 只读服务端事实：`card_move(reason=JUDGE, to=processing)` + 承载帧 `frame_kind=JUDGE`
 * （由 `applyEffectDelta` 已记在 `state.effectCards` 的帧表上）。不认技能 id。
 */
function judgmentCardFromRecord(record) {
  if (!record || record.kind !== "delta" || record.delta_type !== "card_move") return null;
  if (String(record.reason) !== "JUDGE") return null;
  const to = record.to_zone || {};
  if (String(to.zone) !== "processing") return null;
  const cards = record.cards || [];
  if (cards.length !== 1) return null;
  const frameId = stageFrameOf(record);
  if (!frameId || stageFrameKindOf(state.effectCards, frameId) !== "JUDGE") return null;
  return (state.effectCards || []).find(
    (item) => String(item.card_id) === String(cards[0].card_id),
  ) || cards[0];
}

/** A1：弹窗已开着时，把新判定牌**追加**到同一个窗口（不关窗、不重开、不覆盖前一张）。
 *
 * 只更新**弹窗正文**（`#t-options` 里的亮牌模板）；选中态 / 提交 / 放弃 / 提示条动作都不动。
 * 追加的是服务端 `card_move(reason=JUDGE)` 这条事实（最后一张红牌没有继续询问，只能走
 * 记录流）；下一份 pending 的 `judgment_reveal.cards` 是服务端聚合的权威顺序，提交时
 * `renderPending` 会用它整份覆盖，故这里只做"已开窗口的即时追加"，不写规则。
 */
function refreshJudgmentReveal(card) {
  if (!card || !state.dialog || !state.dialog.open) return;
  if (state.dialog.kind !== DIALOG_KINDS.reveal) return;
  const pending = state.view ? state.view.pending : null;
  if (!pending || !pending.judgment_reveal || pending.judgment_reveal.final) return;
  const box = $("t-options");
  if (!box) return;
  const cards = Array.isArray(pending.judgment_reveal.cards)
    ? pending.judgment_reveal.cards
    : [];
  const known = cards.some(
    (entry) => String(entry.card_id) === String(card.card_id),
  );
  const nextCards = known ? cards : [...cards, card];
  box.innerHTML = "";
  renderReveal({
    ...pending,
    judgment_reveal: { ...pending.judgment_reveal, cards: nextCards },
  });
  syncDialogHint();
}

/** 把舞台项画进任意容器（牌桌 / 回放页共用这一份渲染）。
 *
 * 按 `data-stage-key`（= card_id）**复用节点**：淡出不重建一屏；但每次为了保持入台顺序
 * 都会 `appendChild`（已有子节点 = 移出再插入），CSS `animation` 会从头播。故淡出进度
 * 由 :func:`syncStageCardFade` 的负 `animation-delay` 续上，重绘只前进、不回 opacity 1。
 * 被淡出结束的项真的从父节点移除。
 * 空舞台仍画「（空）」占位（U1-02 的既有读法）。
 */
function renderEffectCards(box, items, now = stageNow(), heroNameOf = stageHeroNameOf) {
  if (!box) return;
  const wanted = items || [];
  if (!wanted.length) {
    const onlyEmpty = box.children.length === 1 && nodeHasClass(box.children[0], "dim")
      && !nodeHasClass(box.children[0], "stage-card");
    if (!onlyEmpty) {
      box.innerHTML = "";
      const empty = document.createElement("span");
      empty.className = "dim";
      empty.textContent = "（空）";
      box.appendChild(empty);
    }
    return;
  }
  const existing = new Map();
  for (const node of [...(box.children || [])]) {
    const key = node.getAttribute ? node.getAttribute("data-stage-key") : null;
    if (key) existing.set(key, node);
    else box.removeChild(node);
  }
  const ordered = [];
  for (const item of wanted) {
    const key = String(item.card_id);
    let node = existing.get(key);
    if (node) existing.delete(key);
    else node = stageCardNode(item, heroNameOf);
    updateStageCardNode(node, item, now, heroNameOf);
    ordered.push(node);
  }
  for (const node of existing.values()) box.removeChild(node);
  for (const node of ordered) box.appendChild(node);
}

/** 技能按钮右下角的状态补充（预发动 / 点它转化 / 可发动）；详情模态不读它。
 *
 * 徽标只描述**这一问给的入口**：有清单 ⇒ 预发动；已翻预发动、这一问给的却是一条普通
 * 候选（【蛊惑】的提供者入口）⇒ 仍是"可发动"，不冒充清单入口。 */
function skillBadgeText(skill, manifest, convertCards) {
  if (!skill) return "";
  if (manifest) return " · 预发动";
  if (convertCards.length) return " · 点它转化";
  return skill.available ? " · 可发动" : "";
}

/** 给技能按钮补上服务端三态（`active` / `suppressed` / `lost`）。
 *
 * 只有 `suppressed` 需要额外画禁止图标：它是"有但被挡住"（实例还在武将牌上，只是被
 * 【铁骑】等效果挡在解析之外），与"此刻不给入口"（`available=false`）和"失去"都不同。
 * 图标是内联 SVG（不引外部图片），颜色取现有强调红 `var(--accent)`；`aria-hidden`
 * 避免读屏重复，按钮的 `aria-label` 负责念出「名称（失效）」。
 */
function applySkillState(button, skill) {
  const skillState = (skill && skill.state) || "active";
  button.setAttribute("data-state", skillState);
  if (skillState !== "suppressed") return;
  button.classList.add("suppressed");
  button.setAttribute("aria-label", `${skill.name}（${skillStateLabel(skill)}）`);
  button.setAttribute("title", `${skillStateLabel(skill)}：${skillDescription(skill)}`);
  const mark = document.createElement("span");
  mark.className = "skill-ban";
  // 垫片的 className / classList 不联动；两处都写，真 DOM 与烟测同一条判据。
  mark.classList.add("skill-ban");
  mark.setAttribute("aria-hidden", "true");
  mark.innerHTML = '<svg viewBox="0 0 16 16" focusable="false">'
    + '<circle cx="8" cy="8" r="6.4" fill="none" stroke="currentColor" stroke-width="1.6"/>'
    + '<line x1="3.6" y1="12.4" x2="12.4" y2="3.6" stroke="currentColor"'
    + ' stroke-width="1.6" stroke-linecap="round"/></svg>';
  button.appendChild(mark);
}

/** 一条技能**在这份询问里**的可点性与点击动作；底部技能区与提示区技能组共用这一处。
 *
 * U1-06 的三态判据只在这里实现一次（各写一遍就会一个绿一个灰）：
 *
 * - `usable`：这条询问真的给了入口——已翻预发动 = 预发动清单在；其余 = 骨架
 *   `ActivateSkill` 候选 / 转化来源 / 转化清单任一在（与 `view.skills.available` 同源）。
 *   清单有两类消费者（W4-2 / W4-3）：已翻到预发动的技能（`preactivated`）= **复合动作**
 *   清单；转化技 = **本地入口**清单——两者都由 `pending.preactivations[skill_id]` 下发。
 * - `suppressed`：服务端标明技能失效——即使询问里碰巧还有同名入口也不许点；
 * - `inert`：失效 / 被动技（`ui.passive`）/ 锁定技——按钮只说明（悬停出描述），不接 click；
 * - `clickable` = `usable && !inert`。回放期间 `handler` 为 `null`（外观不变）。
 *
 * 点击动作与旧入口完全一致：清单 → 进入预发动；转化来源 → 切来源模式；
 * 骨架候选 → 直提态（选中那条候选）。`handler === null` = 此刻不给 click。
 */
function skillOffer(pending, skill) {
  const ui = skill.ui || {};
  const convertCards = ui.convert_sources || [];
  const preactivated = Boolean(skill.preactivated);
  const suppressed = skill.state === "suppressed";
  // 失效优先于一切入口：即使这份询问里还留着同名清单，也不显示"预发动"。
  const manifest = suppressed ? null : preactivationManifest(pending, skill.skill_id);
  // **已翻预发动 ≠ "只能走清单"**：同一条技能可以有第二个入口——【蛊惑】的"需要牌时"
  // 就是**提供者**：它在【闪】/【无懈可击】/濒死这些窗口里是一条普通的 `ActivateSkill`
  // 候选（`_guohuo_provider_offers`），与出牌阶段那份清单是两条路。判据只看**这一问给了
  // 哪种入口**：有清单 ⇒ 复合动作入口；没有清单但服务端说可用（`skill.available` = 这一问
  // 里有它的骨架候选）⇒ 直提态。故不能按 `preactivated` 一刀切成"只认清单"——那会让
  // 提供者入口在响应窗口里整片变灰（用户实机报障 2026-11-02）。
  const usable = !suppressed && isOwnPending(pending) && (
    Boolean(manifest) || Boolean(skill.available) || convertCards.length > 0
  );
  const passive = Boolean(ui.passive);
  // 锁定技也可以是**转化入口**（神关羽【武神】既是锁定技、又是‘只能’型转化）。这类
  // "锁定"没有"是否发动"要问，但玩家仍要挑来源牌与目标 ⇒ 按钮必须点得动。判据只读
  // "这一问有没有下发清单"，纯锁定技 / 被动技（无清单、无转化来源）照旧只做说明。
  // 注意：这里读的是 `locked`（规则类型，管失效与三态），**不是** `forced`（‘只能 / 可以’）。
  const lockedEntry = Boolean(skill.locked) && Boolean(manifest) && !passive;
  const inert = suppressed || (Boolean(skill.locked) && !lockedEntry) || passive;
  const clickable = usable && !inert;
  let handler = null;
  if (clickable && pending && !state.playback) {
    if (manifest) {
      handler = () => enterPreativation(skill, manifest);
    } else if (convertCards.length) {
      handler = () => toggleConvertSkill(skill);
    } else if (skill.available) {
      const index = (pending.legal_actions || []).findIndex(
        (action) => action.kind === "ActivateSkill" && action.skill_id === skill.skill_id,
      );
      if (index >= 0) handler = () => selectSkillCandidate(index);
    }
  }
  return {
    convertCards, preactivated, manifest, suppressed, usable, passive, inert, clickable, handler,
  };
}

/** 选中一条 `ActivateSkill` 候选 = 技能直提态（「将提交：发动【X】」）。
 *  底部技能按钮与提示区技能组共用这一处状态写入（`buildAction` 原样返回那条候选）。 */
function selectSkillCandidate(index) {
  state.selectedOption = index;
  state.focusCardId = null;
  state.selectedTargets = [];
  state.lastRejectReason = "";
  renderTable();
  renderPending(state.view);
}

/** 技能按钮：三态由**数据**决定（`locked` 表类型、`available` 表此刻能不能发动）。
 *
 * 纪律：
 * - **被动 / 锁定技**（`ui.passive` / `locked`）按钮只做说明：左键 / 右键都不动作，
 *   悬停出描述（U1-06）。被动预发动的入口在提示条的浮动选项行，不靠点按钮；
 * - **失效**（`state = suppressed`）：按钮保留并画禁止图标（见 :func:`applySkillState`），
 *   不可点；它不是"此刻不可用"（深灰）——失效是"有但被挡住"；
 * - 可发动的主动技 / 装备技：绿（`.ready`）且可点；此刻不可用：深灰（`.unavailable`）；
 * - 清单顺序由服务端给（武将在前、装备在后），这里**只渲染**，不自己重排。
 *
 * **预发动（W4-1 / W4-2）**：`skill.preactivated` 的技能走**清单入口**——点它进入
 * 清单驱动的本地态（确认 = 复合动作、取消零提交），不再去 `legal_actions` 里找
 * 骨架候选（已翻的技能连骨架都没有）。未翻的技能照旧：转化技点它进入来源模式，其余
 * 点一下 = 选中那条 `ActivateSkill` 候选。判据与点击动作都住在 :func:`skillOffer`。
 *
 * **格子（报障 2026-11-01）**：底部技能区是**固定 3 列 × 6 行**的等大网格（CSS 里
 * `#t-skills`），故按钮上只放名字的前 3 个字（:func:`shortSkillName`）；类别 / 徽标
 * 副行（`.kind`）由 CSS 隐藏但仍在节点里，完整名字 + 类别 + 徽标写进 `aria-label`，
 * 悬停描述照旧读 `title`。多于 18 条（现役武将到不了）按同一份等分继续往下排，仍等大、
 * 一个都不裁——面板的格高来自 CSS，JS 只负责顺序与标签。
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
    const offer = skillOffer(pending, skill);
    const { convertCards, preactivated, manifest, usable, passive, clickable } = offer;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "skill";
    if (skill.locked) button.classList.add("locked");
    if (passive && !skill.locked) button.classList.add("passive");
    if (clickable) button.classList.add("ready");
    else if (!offer.inert) button.classList.add("unavailable");
    button.disabled = !clickable;
    button.setAttribute("data-skill-id", skill.skill_id);
    button.setAttribute("data-available", String(skill.available));
    button.setAttribute("data-usable", String(usable));
    button.setAttribute("data-locked", String(skill.locked));
    // 悬停全文（U1-04b / U1-06）：技能详情 / 悬停都读服务端下发的同一份 description；
    // 缺条目时显式「暂无描述」，不留空 title，也不在 JS 里抄文案。
    button.setAttribute("title", skillDescription(skill));
    if (!offer.suppressed) {
      if (preactivated) button.setAttribute("data-preactivated", "true");
      if (convertCards.length) button.setAttribute("data-convert", String(convertCards.length));
      if (manifest) button.setAttribute("data-preactivation", String(manifest.length));
    }
    const kind = skillKindLabel(skill);
    const badge = offer.suppressed
      ? ""
      : skillBadgeText(skill, manifest, convertCards);
    // M6 票 19：☆ 技能（觉醒后获得）在按钮名字前标出来；未觉醒时它不在清单里。
    const star = skillStarLabel(skill);
    if (star) button.setAttribute("data-star", "true");
    // 报障 2026-11-01：格子是固定 3 列 × 6 行的等大网格 ⇒ 名字**只放前 3 个字**
    //（`shortSkillName`，超出加省略号）；类别 / 状态副行仍留在节点里给悬停与读屏用
    //（`#t-skills .kind` 由 CSS 隐藏），完整名字 + 类别 + 徽标进 `aria-label`。
    const shownName = star ? `${star} ${shortSkillName(skill.name)}` : shortSkillName(skill.name);
    button.innerHTML = `${shownName}<span class="kind">${kind}${badge}</span>`;
    button.setAttribute("aria-label", `${skill.name}（${kind}${badge}）`);
    applySkillState(button, skill);
    if (offer.handler) button.addEventListener("click", offer.handler);
    box.appendChild(button);
  }
}

/** 改判窗口的**只说明**入口文案：两版【鬼才】的选择都是「选一张牌 → 提交」，没有第二套
 * 选择方式。标准版候选只给手牌、界版还含装备区（M6 票 14），故这里说「一张牌」而不是
 * 「一张手牌」——文案对两版都成立。 */
const REJUDGE_ENTRY_HINT = "选一张牌打出，然后点「提交」（改判不需要选目标）。";

/** 这条技能是不是**改判窗口**（`RESPOND` / `REJUDGE`）里的那个只说明入口。
 *
 * 改判窗口的候选是手牌的 `PlayCard`（没有 `ActivateSkill`），所以 `available` 恒为假
 * ——底部技能区的旧判据看不见它。这里只做一次**服务端字符串相等**：询问的
 * `context.requirement`（这一步要做什么）与本技能的 `ui.requirement`（技能声明上的
 * 需要做什么）写成同一句时，说明这条技能就是当前窗口的来源。它不是合法性判据：
 * 能不能打某张牌仍只由候选 / 候选索引决定；被动 / 锁定技不在此列。
 */
function rejudgeWindowEntry(pending, skill) {
  if (!pending || pending.kind !== "RESPOND" || pending.cause !== "REJUDGE") return false;
  const ui = skill.ui || {};
  if (skill.state === "suppressed" || skill.locked || ui.passive) return false;
  const requirement = pending.context && pending.context.requirement;
  return Boolean(requirement) && ui.requirement === requirement;
}

/** 提示区左栏的**技能按钮组**（U2-04 / spec D5）：这条询问真正给了入口的技能，中文名横排。
 *
 * 与底部技能区**共用** :func:`skillOffer`（可点性 / 点击动作只有一份）：清单 → 预发动、
 * 转化来源 → 来源模式、可用 `ActivateSkill` → 直提态；被动 / 锁定技照旧只说明——
 * 出现在组里但 `disabled`，点不动（三态不变）。
 *
 * 多出来的一个**只说明**的入口是改判窗口（见 :func:`rejudgeWindowEntry`）：按钮只补一句
 * 提示，真正的选择仍走既有的「点手牌 → 提交」（本函数不写任何选择态）。
 */
function renderPromptSkills(view) {
  const box = $("t-prompt-skills");
  if (!box) return;
  box.innerHTML = "";
  const pending = view && view.pending;
  if (!pending || !isOwnPending(pending)) return;
  for (const skill of view.skills || []) {
    const offer = skillOffer(pending, skill);
    const windowEntry = rejudgeWindowEntry(pending, skill);
    if (!offer.usable && !windowEntry) continue;
    const actionable = offer.clickable || windowEntry;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "skill";
    if (skill.locked) button.classList.add("locked");
    if (offer.passive && !skill.locked) button.classList.add("passive");
    if (offer.inert) {
      // 被动 / 锁定技：画在组里，但只说明（与底部技能区同一姿态）。
    } else if (actionable) button.classList.add("ready");
    else button.classList.add("unavailable");
    button.disabled = !actionable || Boolean(state.playback);
    button.setAttribute("data-skill-id", skill.skill_id);
    if (windowEntry) button.setAttribute("data-window-entry", "true");
    button.setAttribute("title", windowEntry ? REJUDGE_ENTRY_HINT : skillDescription(skill));
    button.textContent = skill.name;
    applySkillState(button, skill);
    if (offer.handler) button.addEventListener("click", offer.handler);
    else if (windowEntry && !state.playback) {
      // 只把"怎么点"写明白：不选中候选、不改焦点、不发请求（不是第二套选择方式）。
      button.addEventListener("click", () => {
        const hint = $("t-target-hint");
        if (hint) hint.textContent = REJUDGE_ENTRY_HINT;
      });
    }
    box.appendChild(button);
    if (windowEntry) {
      const hint = document.createElement("span");
      hint.className = "prompt-skill-hint";
      hint.textContent = REJUDGE_ENTRY_HINT;
      box.appendChild(hint);
    }
  }
}
