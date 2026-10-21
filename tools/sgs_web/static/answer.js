/* SGS 前端 · 应答：倒计时、Action 组装、提交 / 托管 / 清空。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/** 倒计时着色分界（剩余 / 总时限）：≤ 20% 红、≤ 50% 黄、其余绿；最后 5 秒必红 + 闪烁。 */
const COUNTDOWN_RED_AT = 0.2;
const COUNTDOWN_YELLOW_AT = 0.5;
const COUNTDOWN_BLINK_SECONDS = 5;

/** 观星排列**提前**多少秒自动提交（票 07 / spec D9.3）。
 *
 * 为什么要提前：客户端的倒计时锚点（`state.pendingClock.receivedAt`）是**第一次渲染**
 * 这一问的本地时刻，比服务端的 `pending_since` 晚（服务端在响应发出前就起表，客户端还要
 * 等事件回放播完才渲染；实测普通响应约 0.2–1.0 秒）；而 `serve.py:advance_clock` 在每次
 * 取增量 GET（每 1.2 秒轮询一次）里判超时，到点后先代答、后到的 POST 会 409。故必须比
 * **本机**剩余时间提前一点发：2 秒 ≥ 1 秒 tick 粒度 + 正常回放 / 网络偏差，触发时服务端
 * 通常还剩约 1–2 秒，抢在 `advance_clock` 之前。取 1.5 秒在 0.5 秒步长 + 多记录回放时
 * 余量太薄；2 秒是既够早又不至于在玩家还在摆牌时误触的值。
 */
const ARRANGE_AUTO_SUBMIT_MARGIN_SECONDS = 2;

/** 剩余秒 → 进度条颜色阶段（纯函数：便于烟测钉住三色与闪烁边界）。 */
function countdownPhase(remain, total) {
  const left = Number(remain);
  const limit = Number(total);
  if (!Number.isFinite(left) || !Number.isFinite(limit) || limit <= 0) return "off";
  if (left <= 0) return "red";
  const ratio = left / limit;
  if (left <= COUNTDOWN_BLINK_SECONDS || ratio <= COUNTDOWN_RED_AT) return "red";
  if (ratio <= COUNTDOWN_YELLOW_AT) return "yellow";
  return "green";
}

function resetCountdown() {
  const box = $("t-countdown");
  if (box) {
    box.hidden = true;
    box.setAttribute("data-phase", "off");
    box.classList.remove("blink");
  }
  const timer = $("t-timer");
  if (timer) timer.textContent = "";
  const fill = $("t-timer-fill");
  if (fill) {
    fill.style.width = "0%";
  }
  const label = $("t-default-label");
  if (label) label.textContent = "";
}

/** 这一问按本机锚点还剩几秒；`null` = 不是本机上有时限的询问。 */
function arrangeAutoSubmitRemain(view) {
  const pending = view && view.pending;
  const timeout = view ? Number(view.timeout_seconds) : NaN;
  if (!pending || !Number.isFinite(timeout) || timeout <= 0) return null;
  const received = state.pendingClock.receivedAt;
  if (!received) return null;
  return timeout - (Date.now() - received) / 1000;
}

/** 观星排列是否**到了该自动提交**的时刻（票 07 / spec D9.3 的唯一例外）。
 *
 * 只对 `pending.kind === "ARRANGE"` 为真；其余询问（含到时限）永远返回 false ⇒ 前端
 * 仍然零请求。另外四条闸门：
 *  - `pending.seq` 已自动提交过（`state.arrangeAutoSeq`）⇒ 不再发第二次；
 *  - 提交在飞（`state.submitBusy`）⇒ 让路，不并发；
 *  - 回放 / 不是自己的询问 / 已结束 / 无时限 ⇒ 不代答；
 *  - 本地排列还没排成完整顺序（`arrangeAction` 为 null，含规格缺失）⇒ 没有可提交的
 *    `ArrangeCards`，交给服务端默认，不发半截请求。
 *
 * 提前量见 :data:`ARRANGE_AUTO_SUBMIT_MARGIN_SECONDS`：本机剩余时间 ≤ 2 秒即触发，
 * 不等 `remain === 0`（那时服务端的轮询 GET 往往已先代答）。
 */
function arrangeAutoSubmitDue(view) {
  const pending = view && view.pending;
  if (!pending || pending.kind !== "ARRANGE") return false;
  // 只回答**界面上当前这一问**：测试 / 旧视图调用 renderCountdown 不会替别的视图提交。
  if (view !== state.view) return false;
  if (state.playback || (view && view.finished)) return false;
  if (!isOwnPending(pending, view)) return false;
  if (state.arrangeAutoSeq === pending.seq) return false;
  if (state.submitBusy || state.preactivation) return false;
  const remain = arrangeAutoSubmitRemain(view);
  if (remain === null || remain > ARRANGE_AUTO_SUBMIT_MARGIN_SECONDS) return false;
  return arrangeAction(pending) !== null;
}

/** 到点就提交**一次**当前排列；返回 `false`（没到点 / 不能提交）或提交的 Promise。
 *
 * 先落 `state.arrangeAutoSeq = pending.seq` **再**发请求：即使请求失败、或同一 tick 里
 * 又被重复调用，也不会重试（服务端的 `advance_clock` 超时代答是安全网）。409 的刷新
 * 由 :func:`submitAction` 的既有分支处理。
 */
function maybeAutoSubmitArrange(view) {
  if (!state.sessionId) return false;   // 合成载荷 / 尚未建局：不朝不存在的会话发请求
  if (!arrangeAutoSubmitDue(view)) return false;
  state.arrangeAutoSeq = view.pending.seq;
  return submitAction();
}

/** 倒计时（U1-03）：进度条 + 剩余秒 + 默认动作；只显示**自己的**人类询问。
 *
 * 时间锚点住在 `state.pendingClock`（同一 `pending.seq` 轮询刷新不重置）。真正
 * 代答在服务端（`timeout.py`）；本函数对**所有非排列询问**都只读，不提交、不刷新、
 * 不碰 `legal_actions`。**唯一例外**是观星排列（`ARRANGE`）：到时限前
 * :data:`ARRANGE_AUTO_SUBMIT_MARGIN_SECONDS` 秒，前端自动提交一次当前排列
 * （spec D9.3 / 票 07，见 :func:`arrangeAutoSubmitDue`）。
 */
function renderCountdown(view) {
  const box = $("t-countdown");
  const timer = $("t-timer");
  if (!box || !timer) return;
  const pending = view && view.pending;
  const timeout = view ? view.timeout_seconds : null;
  if (
    !pending
    || !isOwnPending(pending, view)
    || timeout === null || timeout === undefined || Number(timeout) <= 0
    || state.playback
    || (view && view.finished)
  ) {
    resetCountdown();
    return;
  }
  const total = Number(timeout);
  // 服务端用的是单调时钟，与浏览器时钟不可比；这里只用"第一次见到这一问的本地时刻 + 时限"。
  const received = state.pendingClock.receivedAt || Date.now();
  const remain = Math.max(0, total - (Date.now() - received) / 1000);
  const phase = countdownPhase(remain, total);
  box.hidden = false;
  box.setAttribute("data-phase", phase);
  box.classList.toggle("blink", remain > 0 && remain <= COUNTDOWN_BLINK_SECONDS);
  const fill = $("t-timer-fill");
  if (fill) {
    const ratio = Math.max(0, Math.min(1, remain / total));
    fill.style.width = `${(ratio * 100).toFixed(1)}%`;
  }
  timer.textContent = remain > 0
    ? `剩余 ${Math.ceil(remain)} 秒`
    : "已到时限，服务端即将代答";
  const label = $("t-default-label");
  if (label) {
    label.textContent = (pending.prompt_display && pending.prompt_display.default_label) || "";
  }
  // 票 07：唯一例外——排列到点自动提交（其它询问在这里恒为 false，零请求）。
  return maybeAutoSubmitArrange(view);
}

/* ------------------------------------------------------------------ 提交 */

/** 提示区右端 / 左端的四个操作键（U2-03 的冻结范围）。 */
const ACTION_BUTTON_IDS = ["btn-submit", "btn-cancel", "btn-end-phase", "btn-pass"];

/** 冻结四键：请求在飞期间**全部** disabled；快照留到请求结束，失败时原样恢复。 */
function freezeActionButtons() {
  if (state.actionFreeze) return;
  state.actionFreeze = ACTION_BUTTON_IDS.map((id) => {
    const button = $(id);
    return {
      id,
      disabled: button.disabled,
      hidden: button.hidden,
      text: button.textContent,
    };
  });
  enforceActionFreeze();
}

/** 在飞期间任何重绘之后再把四键压回 disabled（`renderPending` 末尾调用）。 */
function enforceActionFreeze() {
  if (!state.actionFreeze) return;
  for (const id of ACTION_BUTTON_IDS) {
    const button = $(id);
    if (button) button.disabled = true;
  }
}

/** 解除冻结。``restore`` = 恢复点击前那一面（失败且界面没有换掉时）；
 *  重新渲染过的界面会先丢弃快照，四键状态由那次渲染决定。 */
function releaseActionButtons(restore) {
  const snapshot = state.actionFreeze;
  state.actionFreeze = null;
  if (!restore || !snapshot) return;
  for (const saved of snapshot) {
    const button = $(saved.id);
    if (!button) continue;
    button.disabled = saved.disabled;
    button.hidden = saved.hidden;
    button.textContent = saved.text;
  }
}

function buildAction() {
  const pending = state.view.pending;
  if (!pending) return null;
  // 预发动（W4-1）：确认 = **复合动作**，由清单的本地答案拼出（不在 `legal_actions` 里；
  // 服务端校验器按同一份步骤声明验）。取消走 :func:`cancelOrClear` 的本地分支。
  if (state.preactivation) return buildPreativationAction(pending, state.preactivation);
  // 排列（W6-03）：由服务端下发的 `arrange` 规格 + 本地双行布局拼一条 `ArrangeCards`。
  // **键序**必须是 answers_seq 在前（见 :func:`arrangeAction`），否则下面的逐字段自检会拒。
  if (pending.kind === "ARRANGE") return arrangeAction(pending);
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

/** 提示条右端的两个动作按钮：选中候选里的那条 `EndPhase` / `Pass` 并提交。
 *
 * 「放弃」严格**一击即弃**（U2-03）：提交前**不重绘**（不重铺候选 / 选项），POST 在飞时
 * 四键冻结；被拒（400 / 409）回到点击前的内部选择与四键状态。合成载荷 / 尚未建局
 * （`state.sessionId` 为空）时只留下内部选中态，不朝不存在的会话发请求。
 * 提交的仍是**原样取自** `legal_actions` 的那一条——前端不自己拼 Action。
 *
 * **排列（票 07）**：`ARRANGE` 的右端「放弃」（若该技能真给了 `Pass`）不是送出 `Pass`，
 * 而是与「确认」等价地提交**当前排列**（`buildAction` 对 `ARRANGE` 优先返回
 * `ArrangeCards`）。引擎当前的观星排列是强制的（无 `Pass`），故这个按钮根本不出现。
 */
async function submitRightAction(kind) {
  if (state.playback || state.submitBusy) return;
  const pending = state.view ? state.view.pending : null;
  const index = rightActionIndex(pending, kind);
  if (index < 0) return;
  // 票 07：排列没有可用布局（规格缺失 / 顺序不完整）时不要退回成"送 Pass"。
  if (pending && pending.kind === "ARRANGE" && kind === RIGHT_ACTION_KINDS.pass
    && !arrangeAction(pending)) {
    $("t-choice").textContent = "排列不可用，请刷新";
    return;
  }
  // 先留一份**点击前**的选择快照：失败时原样还回去（界面 DOM 本来就没动）。
  const before = selectionSnapshot();
  state.selectedOption = index;
  state.focusCardId = null;
  state.selectedTargets = [];
  state.picks = [];
  state.preactivation = null;
  state.lastRejectReason = "";
  if (!state.sessionId) {
    // 合成载荷 / 尚未建局：只同步内部选中态与按钮状态，不发请求、也不重绘内容区。
    refreshSubmit(pending);
    renderRightActions(pending, state.view);
    return;
  }
  await submitAction(before);
}

/** 提交当前选择。``restoreSelection`` = 点右端动作（或取消）时点击前的选择快照：
 *  非 409 失败时恢复它，界面就仍是点击前那一面（400 只加一行错误文案）。 */
async function submitAction(restoreSelection = null) {
  if (state.playback || state.submitBusy) return;
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
  // 预发动 / 手牌清单入口的确认动作**不在** `legal_actions` 里：
  // - 技能清单 = `ActivateSkill`（复合动作，服务端校验器验）；
  // - 手牌清单（W5-02b 借刀）= 带 `designations` 的 `UseCard`，服务端只认本次
  //   `designation_actions` 池的成员（同一份清单派生），故这里同样让开。
  // 只对这两类让开：其余动作仍走下面的逐字段自检（服务端也会再验一次）。
  const cardEntry = Boolean(state.preactivation && state.preactivation.cardEntry);
  const composite = state.preactivation !== null
    && (action.kind === "ActivateSkill" || (cardEntry && action.kind === "UseCard"));
  if (
    !composite
    && !pending.legal_actions.some((candidate) => JSON.stringify(candidate) === JSON.stringify(action))
  ) {
    // 最后一道自检：造出来的必须**逐字段**是候选里的那一条（否则引擎会拒，且错在界面）。
    // 预发动是**唯一**让开的地方：复合动作不要求在候选里，由服务端校验器验（W4-1）。
    $("t-choice").textContent = "这个组合不在合法候选里，请换一个选择。";
    return;
  }
  // U2-03：POST 在飞 = 提示区四键全部禁用（快照留到结束）；轮询刷新也让路（见 refresh）。
  const viewBefore = state.view;
  freezeActionButtons();
  state.submitBusy = true;
  try {
    const body = await post(
      `/api/sessions/${state.sessionId}/answer?after=${state.cursor}&logs=${state.logSeq}`,
      { action },
    );
    state.submitBusy = false;
    releaseActionButtons(false);
    resetSelection();
    applyView(body);
  } catch (error) {
    state.submitBusy = false;
    if (error.status === 409) {
      // 409 = 询问过期：允许刷新到**新的**询问；刷新失败才退回点击前那一面。
      $("t-choice").textContent = "这个询问已过期（可能刚被代答）：已为你刷新。";
      await refresh(true);
      if (state.view === viewBefore && !state.playback) {
        if (restoreSelection) restoreSelectionState(restoreSelection);
        releaseActionButtons(true);
      } else {
        // 新视图 / 回放已接管界面：旧快照不能盖上去。
        releaseActionButtons(false);
      }
      return;
    }
    // 400 / 其它拒绝：恢复点击前的选择与四键，DOM 只多一行错误文案。
    if (restoreSelection) restoreSelectionState(restoreSelection);
    releaseActionButtons(true);
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
