/* SGS 前端 · 应答：倒计时、Action 组装、提交 / 托管 / 清空。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

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
