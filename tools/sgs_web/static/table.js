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
 * 引擎侧没有轮次计数器（`PlayerView` 只给 `turn_seat` / `phase`），阵亡座位会跳过回合；
 * 因此不能用"次数 ÷ 人数"（那会在最后一人的回合就提前进轮）。按「本轮已经行动过的座位」
 * 记：座位重复出现 = 转了一圈。顶栏不拿它做任何判据，等 U2 有服务端轮次字段再换。
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
  const rounds = $("t-round");
  if (rounds) {
    rounds.textContent = String(roundNumber(
      state.logRecords.length ? state.logRecords : (view.records || []),
    ));
  }
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
    ...(player.wine_active ? ["wine"] : []),
    ...extra,
  ];
  const ctx = {
    seatOf, handPick, selfOnly, optionChoiceAt, preSeats, pendingNow, ownPending,
    snapshot, identityMode, flagsFor,
  };

  renderRingSeats(view, ctx);
  renderSelfSeat(view, ctx);

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
      cardChip(
        card, face.selected, false, face.selectable,
        () => activateCard(pendingNow, card, face), face.picked,
      ),
    );
  }
  renderSkills(view);
  // 提示区左栏的技能按钮组：与底部技能区同一份清单 / 判据（U2-04），additive。
  renderPromptSkills(view);
}

/** 效果区 / 处理区：画 `state.effectCards`（回放期间逐事件派生、快照到达时并进来），
 * 并把**描述文本**（`#t-context`）同步成它的说明——两者同源，故不会一个变一个不变。
 *
 * 描述文本曾经是服务端下发的"本窗口：谁对谁使用…"，只在询问到达时更新，回放期间会残留；
 * 现在它跟着效果区走：有牌写「处理中：【牌】（谁在使用）」，牌全走了**立即清空**。 */
function renderProcessing() {
  renderEffectCards($("t-processing"), state.effectCards || []);
  const contextNode = $("t-context");
  if (contextNode) contextNode.textContent = describeEffect(state.effectCards || []);
  renderStageAction();
}

/** 中央舞台上层 = 当前动作（最近一条应答的人话；没有就写"等待行动"）。
 *
 * **结算完淡出**用「处理区是否为空」当判据（无计时器）：牌还在处理区 = 正在结算（不透明），
 * 牌全走了 = 这条动作已经落定（`.faded` 半透明 + CSS 过渡）。历史仍进事件流。
 */
function renderStageAction() {
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
  const layer = $("t-action-layer");
  if (layer) layer.classList.toggle("faded", !(state.effectCards || []).length);
}

/** 把一组"处理区牌"画进任意容器（牌桌 / 回放页共用同一份渲染）。
 *
 * 回放页的效果区**复用** :func:`applyEffectDelta` 的逐事件派生，渲染也复用这一处——
 * "怎么画处理区"只有一份实现，两个页面不会漂开。
 */
function renderEffectCards(box, cards) {
  if (!box) return;
  box.innerHTML = "";
  if (!cards || !cards.length) {
    const empty = document.createElement("span");
    empty.className = "dim";
    empty.textContent = "（空）";
    box.appendChild(empty);
    return;
  }
  for (const card of cards) box.appendChild(cardChip(card, false, true));
}

/** 技能按钮右下角的状态补充（预发动 / 点它转化 / 可发动）；详情模态不读它。 */
function skillBadgeText(skill, manifest, convertCards) {
  if (!skill) return "";
  if (skill.preactivated || manifest) return " · 预发动";
  if (convertCards.length) return " · 点它转化";
  return skill.available ? " · 可发动" : "";
}

/** 一条技能**在这份询问里**的可点性与点击动作；底部技能区与提示区技能组共用这一处。
 *
 * U1-06 的三态判据只在这里实现一次（各写一遍就会一个绿一个灰）：
 *
 * - `usable`：这条询问真的给了入口——已翻预发动 = 预发动清单在；其余 = 骨架
 *   `ActivateSkill` 候选 / 转化来源 / 转化清单任一在（与 `view.skills.available` 同源）。
 *   清单有两类消费者（W4-2 / W4-3）：已翻到预发动的技能（`preactivated`）= **复合动作**
 *   清单；转化技 = **本地入口**清单——两者都由 `pending.preactivations[skill_id]` 下发。
 * - `inert`：被动技（`ui.passive`）/ 锁定技——按钮只说明（悬停出描述），不接 click；
 * - `clickable` = `usable && !inert`。回放期间 `handler` 为 `null`（外观不变）。
 *
 * 点击动作与旧入口完全一致：清单 → 进入预发动；转化来源 → 切来源模式；
 * 骨架候选 → 直提态（选中那条候选）。`handler === null` = 此刻不给 click。
 */
function skillOffer(pending, skill) {
  const ui = skill.ui || {};
  const convertCards = ui.convert_sources || [];
  const preactivated = Boolean(skill.preactivated);
  const manifest = preactivationManifest(pending, skill.skill_id);
  const usable = isOwnPending(pending) && (
    preactivated
      ? Boolean(manifest)
      : (Boolean(skill.available) || convertCards.length > 0 || Boolean(manifest))
  );
  const passive = Boolean(ui.passive);
  const inert = Boolean(skill.locked) || passive;
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
  return { convertCards, preactivated, manifest, usable, passive, inert, clickable, handler };
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
 * - 可发动的主动技 / 装备技：绿（`.ready`）且可点；此刻不可用：深灰（`.unavailable`）；
 * - 清单顺序由服务端给（武将在前、装备在后），这里**只渲染**，不自己重排。
 *
 * **预发动（W4-1 / W4-2）**：`skill.preactivated` 的技能走**清单入口**——点它进入
 * 清单驱动的本地态（确认 = 复合动作、取消零提交），不再去 `legal_actions` 里找
 * 骨架候选（已翻的技能连骨架都没有）。未翻的技能照旧：转化技点它进入来源模式，其余
 * 点一下 = 选中那条 `ActivateSkill` 候选。判据与点击动作都住在 :func:`skillOffer`。
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
    if (preactivated) button.setAttribute("data-preactivated", "true");
    if (convertCards.length) button.setAttribute("data-convert", String(convertCards.length));
    if (manifest) button.setAttribute("data-preactivation", String(manifest.length));
    const kind = skillKindLabel(skill);
    const badge = preactivated
      ? (manifest ? " · 预发动" : "")
      : skillBadgeText(skill, manifest, convertCards);
    button.innerHTML = `${skill.name}<span class="kind">${kind}${badge}</span>`;
    if (offer.handler) button.addEventListener("click", offer.handler);
    box.appendChild(button);
  }
}

/** 改判窗口的**只说明**入口文案：【鬼才】的选择是「点一张手牌 → 提交」，没有第二套选择方式。 */
const REJUDGE_ENTRY_HINT = "点一张手牌打出，然后点「提交」（改判不需要选目标）。";

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
  if (skill.locked || ui.passive) return false;
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
