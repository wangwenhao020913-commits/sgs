/* SGS 前端 · 选项面：小选项按钮组、选择面槽位、「提供一张 B」面。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/** `SELECT_OPTION` 的**小选项按钮组 / 选择型弹窗内容**（花色 / 二选一）：一个选项一个按钮。
 *
 * 它是询问控件区左栏的专用面，**不是**旧的候选列表：按钮只写选项本身，不带
 * 「选项：…」的候选标签。选座位形态（``option_seats`` 非空）不走这里——那一形态只点场上武将。
 *
 * **选择型模板（票 08 / D10）**：载荷带 ``option_labels`` 时（花色是第一个复用枚举），
 * 按钮写服务端下发的中文名 + 符号，并按 ``color`` 标红黑；前端不另抄花色表。这类步骤
 * **一击即提交**（spec D3「选完即提交」）：按钮的 click 直接送出一条候选里的
 * ``SelectOption``，不再需要「下一步」。
 */
function renderOptionButtons(pending) {
  const box = $("t-options");
  const dialog = stepDialogKind(pending) === DIALOG_KINDS.choice;
  if (dialog) box.classList.add("suit-options");
  if (dialog) {
    const hint = $("t-target-hint");
    if (hint) hint.textContent = "点一个选项即提交；这一步不能放弃。";
  }
  (pending.legal_actions || []).forEach((action, index) => {
    if (action.kind !== "SelectOption" || isRightAction(action)) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "opt";
    if (state.selectedOption === index) button.classList.add("sel");
    const label = optionLabel(pending, action.choice);
    if (label) {
      // 中文名 / 符号 / 红黑全部读服务端 `option_labels`：前端只负责摆版面。
      button.classList.add("suit");
      button.setAttribute("data-color", label.color);
      const symbol = document.createElement("span");
      symbol.className = "suit-symbol";
      symbol.textContent = label.symbol;
      const name = document.createElement("span");
      name.className = "suit-name";
      name.textContent = label.name;
      button.appendChild(symbol);
      button.appendChild(name);
    } else {
      button.textContent = optionText(action);
    }
    button.addEventListener("click", async () => {
      if (dialog) {
        await submitDialogChoice(pending, index);
        return;
      }
      state.selectedOption = index;
      state.lastRejectReason = "";
      renderPending(state.view);
    });
    box.appendChild(button);
  });
  $("btn-cancel").disabled = state.selectedOption === null && !cancelAction(pending);
}

/** 服务端下发的选项标签（`{值: {name, symbol, color}}`）；没有就是 `null`。 */
function optionLabel(pending, choice) {
  const labels = pending && pending.option_labels;
  return labels && labels[choice] ? labels[choice] : null;
}

/** 选择型弹窗：点一个选项 = **一次提交**（不经过"选中 → 再点提交"）。
 *
 * 送出的仍是 `legal_actions` 里那条 `SelectOption`（`buildAction` 原样取），不手拼 Action。
 * 合成载荷 / 尚未建局时只留内部选中态（与右端动作同姿态），不发请求。
 */
async function submitDialogChoice(pending, index) {
  if (state.playback || state.submitBusy) return;
  const before = selectionSnapshot();
  state.selectedOption = index;
  state.focusCardId = null;
  state.selectedTargets = [];
  state.picks = [];
  state.preactivation = null;
  state.lastRejectReason = "";
  if (!state.sessionId) {
    renderPending(state.view);
    return;
  }
  await submitAction(before);
}

/** 一次性二选一的**中文标签**（U2-04）：【雌雄双股剑】的 `discard_one` / `draw_one`。
 *
 * 这两个是**一次性**的小选项（spec D3「≤3 且一次性 ⇒ 提示区左侧按钮」），中文名写在
 * 这里；可复用的枚举（花色 / 【杀】的类型 / 锦囊牌名）中文标签属服务端展示契约：
 * 票 08 起花色由载荷的 `option_labels` 下发（见 :func:`optionLabel`），前端不另抄第二份。
 * 查不到标签的值原样显示（fail-visible，不猜中文）。
 *
 * `discard_one` 只在目标**有手牌**时由引擎发出（`skills/catalog.py`），界面不自己补；
 * 载荷里没有它就不画这个按钮。
 */
const OPTION_LABELS = Object.freeze({
  discard_one: "弃置 1 张手牌",
  draw_one: "令其摸一张牌",
});

/** 选项按钮上的文字：已知的一次性选项用中文；其余选项保留**原值**（复用标签待票 08）。 */
function optionText(action) {
  return OPTION_LABELS[action.choice] || action.choice;
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

/** 技能候选的动作短语（U2-03 直提态）：技能名读服务端下发的 `view.skills`，
 *  前端不在这里抄中文名；查不到时退回技能 id（fail-visible，不猜名字）。 */
function skillActionLabel(action) {
  const skill = (((state.view && state.view.skills) || []).find(
    (item) => item.skill_id === action.skill_id,
  ));
  return `发动【${skill && skill.name ? skill.name : action.skill_id}】`;
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

/** 右端动作在候选里的下标；`-1` = 当前询问没有这一条（按钮隐藏）。 */
function rightActionIndex(pending, kind) {
  const actions = (pending && pending.legal_actions) || [];
  return actions.findIndex((action) => action.kind === kind);
}

/** 提示条右端：结束出牌 / 放弃只在**自己的**询问里、且候选真有那一条时出现。
 *
 * 选择型 / 未知区域弹窗（票 08 / 09）**没有「放弃」**：引擎给这两步的候选可能带 `Pass`
 * （流水线语义的"取消这一步"），但界面上弹窗内不可放弃——右端放弃一并藏起，
 * `cancelAction` 也不再读它（见 `pending.js`）。放弃只留在**发动询问**那一步
 * （反馈 / 寒冰剑的"是否发动"、拆 / 顺是普通用牌）。
 */
function renderRightActions(pending, view = state.view) {
  const own = isOwnPending(pending, view);
  const dialogKind = pending ? stepDialogKind(pending) : null;
  const dialogWithoutPass = dialogKind === DIALOG_KINDS.choice
    || dialogKind === DIALOG_KINDS.unknown;
  const endIndex = own ? rightActionIndex(pending, RIGHT_ACTION_KINDS.endPhase) : -1;
  const passIndex = own && !dialogWithoutPass
    ? rightActionIndex(pending, RIGHT_ACTION_KINDS.pass)
    : -1;
  // 被动入口的「取消」本身就是「放弃」（送出那条 Pass）：不再复制一个同义的右端按钮。
  const cancelIsPass = passIndex >= 0 && isPassivePreativation(pending);
  const end = $("btn-end-phase");
  const pass = $("btn-pass");
  if (end) {
    end.hidden = endIndex < 0;
    end.disabled = state.playback || endIndex < 0;
  }
  if (pass) {
    pass.hidden = passIndex < 0 || cancelIsPass;
    pass.disabled = state.playback || passIndex < 0 || cancelIsPass;
    pass.textContent = passIndex >= 0
      ? alternativeLabel(pending, pending.legal_actions[passIndex])
      : "放弃";
  }
}

/** 无询问 / 其他座位的询问：右端两个动作位收起（不让界面留下上一问的按钮）。 */
function resetRightActions() {
  for (const [id, label] of [["btn-end-phase", "结束出牌"], ["btn-pass", "放弃"]]) {
    const button = $(id);
    if (!button) continue;
    button.hidden = true;
    button.disabled = true;
    button.textContent = label;
  }
}

function renderSlots(pending) {
  // 未知区域弹窗（票 08 盲挑 → 票 09 推广到反馈 / 拆 / 顺 / 寒冰剑，见 `isUnknownZoneStep`）：
  // 内容画成「?」槽位 + 可见牌面，点一张即提交；其余选择面（五谷 / 遗计 / 自己的牌）仍走
  // 下面的既有槽位面——它们各有自己的模板（票 11 / 12）。
  if (stepDialogKind(pending) === DIALOG_KINDS.unknown) {
    renderUnknownZone(pending);
    return;
  }
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

/** 未知区域弹窗的正文（票 08 / D3；票 09 推广到反馈 / 拆 / 顺 / 寒冰剑）：按**实际提供的
 *  区域**分组，手牌画「?」、可见的装备 / 判定牌画牌面；点一张即提交（不经过"选中 → 再点提交"）。
 *
 * 分组完全由 `pending.slots` 的 `ref.zone` 推出：这一步没提供的区域**不画**
 * （反间盲挑只提供发动者手牌 ⇒ 没有装备区；反馈 / 拆 / 顺带装备 / 判定槽时才出现）。
 * 槽位顺序 = 载荷顺序，不重排——「位置」本身是公开事实。
 *
 * 步骤提示（`#t-target-hint`，折叠后镜像进标题条）里的**规则话术**读载荷的
 * `context.requirement`（服务端派生：反馈 =「获得伤害来源的一张牌」、拆 / 顺 =
 * 「选择目标区域里的一张牌」），前端只补一句"怎么点"的通用交互说明；没有载荷文案时
 * 才退回纯交互说明——不在这里抄第二份规则文本。
 */
function renderUnknownZone(pending) {
  const box = $("t-options");
  const cap = selectionCap(pending);
  const hint = $("t-target-hint");
  if (hint) {
    const action = cap === 1 ? "点一张牌即提交。" : `点选至多 ${cap} 张牌即提交。`;
    const requirement = pending.context && pending.context.requirement;
    hint.textContent = requirement
      ? `${requirement}：${action}`
      : `${action}看不到牌面，选中后才揭晓。`;
  }
  for (const group of unknownSlotGroups(pending)) {
    const section = document.createElement("section");
    section.className = "unknown-zone";
    section.setAttribute("data-zone", group.zone);
    if (group.seat !== null && group.seat !== undefined) {
      section.setAttribute("data-seat", String(group.seat));
    }
    const head = document.createElement("div");
    head.className = "zone-head";
    head.textContent = group.label;
    section.appendChild(head);
    const row = document.createElement("div");
    row.className = "cards";
    for (const entry of group.slots) {
      const node = entry.slot.card
        ? cardFaceNode(entry.slot.card, {
          onClick: () => { submitUnknownPick(pending, entry.index); },
        })
        : unknownSlotNode();
      node.setAttribute("data-slot-index", String(entry.index));
      if (!entry.slot.card) {
        node.addEventListener("click", async () => {
          await submitUnknownPick(pending, entry.index);
        });
      }
      row.appendChild(node);
    }
    section.appendChild(row);
    box.appendChild(section);
  }
}

/** 未知区域的槽位分组（按载荷顺序；同一 `(座位, 区域)` 一组）。 */
function unknownSlotGroups(pending) {
  const groups = new Map();
  (pending.slots || []).forEach((slot, index) => {
    const ref = (slot && slot.ref) || {};
    const key = `${ref.seat ?? "?"}:${ref.zone ?? "?"}`;
    let group = groups.get(key);
    if (!group) {
      group = { seat: ref.seat, zone: ref.zone || "hand", slots: [] };
      groups.set(key, group);
    }
    group.slots.push({ slot, index });
  });
  return [...groups.values()].map((group) => {
    const hidden = group.slots.filter((entry) => !entry.slot.card).length;
    const base = zoneLabel({ seat: group.seat, zone: group.zone });
    return {
      ...group,
      label: hidden
        ? `${base}（牌面未知，共 ${hidden} 张）`
        : base,
    };
  });
}

/** 一张「?」槽位：与牌面同尺寸（88×124），点击即提交。 */
function unknownSlotNode() {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "card face unknown";
  // 真浏览器的 classList 与 className 同步；烟测的 DOM 垫片是两份存储，故两处都写
  // （既有的 `cardFaceNode` 也是「className 赋值 + classList.add」同一写法）。
  node.classList.add("card", "face", "unknown");
  node.textContent = "?";
  return node;
}

/** 未知区域：点一张槽位 = **一次提交**（`picks` 填该槽位在面内的位置）。
 *
 * 送出的仍是 `legal_actions` 里那条 `SelectCards`（骨架 + `picks` 由 `buildAction` 补），
 * 不手拼 Action、不把实体牌 id 回传。引擎给这一步的候选是一槽一条（`total=(1,1)` 的
 * 全部组合），故一击即为完整答案。
 */
async function submitUnknownPick(pending, index) {
  if (state.playback || state.submitBusy) return;
  if (stepDialogKind(pending) !== DIALOG_KINDS.unknown) return;
  const actions = pending.legal_actions || [];
  const at = actions.findIndex((action) => action.kind === "SelectCards");
  if (at < 0) return;
  const before = selectionSnapshot();
  state.selectedOption = at;
  state.focusCardId = null;
  state.selectedTargets = [];
  state.picks = [index];
  state.preactivation = null;
  state.lastRejectReason = "";
  if (!state.sessionId) {
    renderPending(state.view);
    return;
  }
  await submitAction(before);
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
  // 选择面槽位是牌桌选择入口（D8）：只读态里 no-op，与点牌 / 点座共用一条判据。
  if (!selectionEnabled()) return;
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
  if (!selectionEnabled() || !isProvide(pending)) return;
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
