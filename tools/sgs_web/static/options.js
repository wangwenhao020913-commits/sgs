/* SGS 前端 · 选项面：候选按钮、选择面槽位、「提供一张 B」面。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

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
  if (state.preactivation && action.kind === "ActivateSkill") {
    // 张数 / 目标 / 区域的规则由服务端校验器裁决；本地只拦"清单没填完"。
    return preactivationReady(state.preactivation) ? null : "预发动清单还没填完。";
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
    return `使用【${cardName(action.card)}】${targets}`;
  }
  if (action.kind === "ActivateSkill") {
    return `发动技能 ${action.skill_id}${action.picks && action.picks.length ? `（选 ${action.picks.length} 张）` : ""}`;
  }
  if (action.kind === "SelectCards") return "选牌";
  if (action.kind === "SelectOption") {
    const seat = new RegExp(`^${SEAT_PREFIX}(\\d+)$`).exec(action.choice);
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
    const face = slot.card ? cardName(slot.card) : "？";
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
