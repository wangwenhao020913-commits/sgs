/* SGS 前端 · 会话：接收视图、事件回放、处理区派生。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------ 会话视图的接收与应用 */

let playbackTimer = null;

/** 收到一份新视图：**有事件就先回放**，播完再提交快照与询问（报障 3 的严格顺序）。
 *
 * 回放期间 ``state.view`` 仍是**上一条**视图、提交按钮是灰的——用户看到的是事件一条条
 * 发生，而不是"画面直接跳到结算完"。没有事件（纯刷新）就直接提交。
 */
function applyView(body) {
  if (state.playback) flushPlayback();
  // U1-05：叙事条目随视图增量下发（服务端已按查看者过滤）。先收进本地序列，
  // 再按记录回放的进度逐条铺——不在前端补事实 / 判断隐藏信息。
  for (const entry of body.narrative || []) state.narrative.push(entry);
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
  // 回放期间倒计时与右端动作都收起：`state.view` 还停在上一条询问，继续画会误导。
  resetCountdown();
  resetRightActions();
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
  // 简易档铺服务端叙事（与记录同 seq 的条目在这一步出现）；完整两档铺记录。
  if (logMode() === "simple") appendNarrativeThrough(record.seq);
  else appendLog(record, playback.querySeat);
  state.effectCards = applyEffectDelta(record, state.effectCards);
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
 *
 * **牌组作为参数传入、返回值就是新牌组**：牌桌与回放页各持一份，派生函数不读 / 不写
 * ``state.effectCards``。旧签名在内部把 `state.effectCards` 整个换掉（`filter` 产生新数组），
 * 回放页借来的那份会从第二条 "离开处理区" 起就和写方分家——页面于是停在一张早已离场的牌上。
 */
function applyEffectDelta(record, effectCards = state.effectCards) {
  let held = effectCards || [];
  if (!record) return held;
  if (record.kind === "timing") {
    applyUseTiming(record, held);
    return held;
  }
  if (record.kind !== "delta" || record.delta_type !== "card_move") return held;
  const cards = record.cards || [];
  const from = record.from_zone || {};
  const to = record.to_zone || {};
  if (from.zone === "processing") {
    const ids = new Set(cards.map((card) => card.card_id));
    held = held.filter((card) => !ids.has(card.card_id));
  }
  if (to.zone === "processing") {
    for (const card of cards) {
      if (held.some((existing) => existing.card_id === card.card_id)) continue;
      held.push({
        ...card,
        source: record.reason === "USE" ? record.owner : null, // 使用者随这次使用已知
        targets: null,        // 目标等 USE_DESIGNATE_TARGET 认领
        currentTarget: null,  // 逐目标结算时由 RESOLVE_* 覆盖
      });
    }
  }
  return held;
}

/** 时机记录对描述文本的两个贡献：认领这次使用的目标、更新"当前结算到谁"。
 *
 * 认领只看**栈顶那张处理区牌**（刚进来的那张）且要求使用者与时机 ``actor`` 相同、
 * 目标还没认领过；认领过一次就不再改，故一次使用的目标只认领自己那一份。
 */
function applyUseTiming(record, effectCards = state.effectCards) {
  const held = effectCards || [];
  const card = held[held.length - 1];
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
    return `【${cardName(card)}】${who}`;
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
  // 换会话（开新局 / 从录像回到牌桌）时倒计时锚点必须作废：不同会话的询问序号会重复，
  // 沿用旧锚点会把上一局已经过去的秒数算到新局的第一个询问上。
  if (state.view && body && state.view.session_id && body.session_id
    && state.view.session_id !== body.session_id) {
    state.pendingClock.seq = null;
    state.pendingClock.receivedAt = 0;
  }
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
  // 回放收尾 / 纯刷新：把这一批里还没铺的叙事条目补齐（简易档；其它档是空操作）。
  appendNarrativeThrough(Infinity);
  const logNode = $("t-log");
  if (logNode.lastChild) logNode.lastChild.scrollIntoView({ block: "nearest" });
  if (body.finished) {
    state.timer = null;
    resetCountdown();
    resetRightActions();
    syncModal();
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

/** 清空全部未提交的选择（焦点 / 已选目标 / 旧路径的候选与槽位 / 预发动本地态 / 排列面）。 */
function resetSelection() {
  state.focusCardId = null;
  state.selectedTargets = [];
  state.selectedOption = null;
  state.picks = [];
  state.arrange = null;
  state.preactivation = null;
  state.convertSkill = null;
  state.lastRejectReason = "";
  // U1-03：新视图 = 新询问。模态的重开状态归零；`syncModal()` 会按需要重新打开 / 收起。
  state.modal.kind = null;
  state.modal.open = false;
  state.modal.dismissed = false;
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
