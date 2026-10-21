/* SGS 前端 · 点选：卡面芯片与点牌 / 点座位 / 点选项的本地选择。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

function identityLabel(identity) {
  return { lord: "主公", loyalist: "忠臣", rebel: "反贼", traitor: "内奸" }[identity] || identity;
}

/** 一张卡片的节点：U1-02 起是**完整卡面**（88×124，`card-view.js` 的 `cardFaceNode`）。
 *
 * 手牌 / 处理区 / 中央舞台 / 回放页共用这一份；`static` 牌不可点（公开信息），但仍带
 * 悬停全文。置灰 / 选中 / 已选只由调用方给（手牌走 `cardFace` 的纯函数结果）。
 */
function cardChip(card, selected, isStatic, usable = true, onClick = null, picked = false) {
  return cardFaceNode(card, {
    selected,
    picked,
    static: isStatic,
    usable,
    onClick: onClick || (() => toggleFocus(card.card_id)),
  });
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
    (action) => action.kind === "SelectOption" && action.choice === `${SEAT_PREFIX}${seat}`,
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
    state.lastRejectReason = `【${card ? cardName(card) : cardId}】不能用于当前询问（${pending.prompt}）`;
  } else {
    state.focusCardId = cardId;
    state.selectedTargets = [];
    // 点牌 = 改用这张牌：先前选中的技能 / 小选项必须让位（U2-03 删掉候选行后，
    // 再没有"另点一条候选"来改选中项；`buildAction()` 里 `selectedOption` 优先于手选）。
    state.selectedOption = null;
    const auto = autoCombo(pending, cardId);
    if (auto) state.selectedTargets = auto.slice();
    state.lastRejectReason = "";
  }
  renderTable();
  renderPending(state.view);
}

/** 本次询问里"用这张牌"的候选（非手选面里卡片置灰判据用；候选按钮行已删除）。 */
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
