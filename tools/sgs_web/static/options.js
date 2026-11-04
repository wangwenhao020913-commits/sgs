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
 *
 * **一次性二选一（M5-bugfix / 雌雄双股剑）**：引擎只发**付得起的**选项（目标无手牌时
 * 没有 ``discard_one``），但这一面仍把已知的另一项画成**禁用**控件（见
 * :func:`optionChoices`）——它是控件区的可及性展示，不是候选：点它不提交任何东西。
 * 禁用与否只看"这条在不在 ``legal_actions`` 里"，前端不重算"为什么不可支付"
 * （那是引擎的规则）。
 */
/** 单选项可选确认（B1）：唯一 `SelectOption` 自动选中，不再画选项按钮。
 *
 *  出口 = 提示条右端「提交」（送唯一选项）与「放弃」（送 `Pass`）。判据住在
 *  `pending.js:isSingleOptionalOption`（只看候选形状），本函数只摆版面 / 设选中态。
 *  取消键（清空选择）在这一形态没有意义，隐藏它，避免与右端「放弃」成为两个同义出口。
 */
function renderOptionConfirm(pending) {
  const actions = pending.legal_actions || [];
  const index = actions.findIndex(
    (action) => action.kind === "SelectOption" && !isRightAction(action),
  );
  if (index >= 0) state.selectedOption = index;
  const requirement = (pending.context && pending.context.requirement) || "";
  const hint = $("t-target-hint");
  if (hint) {
    hint.textContent = `${requirement}${requirement ? "：" : ""}`
      + "点右端「提交」确认，或「放弃」取消。";
  }
  $("btn-cancel").hidden = true;
  $("btn-cancel").disabled = true;
}

/** A1：洛神连续判定的亮牌确认 = 单选项确认 + 亮牌模板。
 *
 *  `renderOptionConfirm` 负责自动选中唯一选项 / 隐藏取消键 / 写"提交 / 放弃"提示；
 *  `renderReveal` 再把 `judgment_reveal.cards`（有序累积）画进同一个 `#t-options`。
 *  卡片是**只读**的：`renderReveal` 按 `pending.slots` 决定可点性；这一问没有槽位，
 *  所以不会把点击判成提交——提交 / 放弃始终只在提示条右端。
 */
function renderJudgmentReveal(pending) {
  renderOptionConfirm(pending);
  renderReveal(pending);
}

function renderOptionButtons(pending) {
  const box = $("t-options");
  // 票 01（自制包·韬·刘备【义起】）：这一问带着**观看授予**（`revealed`）——先把牌面画在
  // 按钮上面（只读、仅自己可见），再摆 3 个类型按钮。与洛神"亮牌 + 单选项确认"共用同一个
  // 容器（`#t-options`，随后整体搬进弹窗正文）。
  if ((pending.revealed || []).length) {
    renderReveal(pending, { interactive: false, promptHint: false });
  }
  const dialog = stepDialogKind(pending) === DIALOG_KINDS.choice;
  if (dialog) box.classList.add("suit-options");
  if (dialog) {
    const hint = $("t-target-hint");
    if (hint) hint.textContent = "点一个选项即提交；这一步不能放弃。";
  }
  const actions = pending.legal_actions || [];
  optionChoices(pending).forEach((choice) => {
    // 候选下标 = 提交时要指过去的那一条；-1 = 引擎没发（画成禁用，点它零提交）。
    const index = actions.findIndex(
      (action) => action.kind === "SelectOption"
        && action.choice === choice
        && !isRightAction(action),
    );
    const button = document.createElement("button");
    button.type = "button";
    button.className = "opt";
    button.disabled = index < 0;
    if (index >= 0 && state.selectedOption === index) button.classList.add("sel");
    const label = optionLabel(pending, choice);
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
      button.textContent = optionText(choice, pending);
    }
    button.addEventListener("click", async () => {
      if (index < 0) return; // 禁用项：不是候选，绝不提交（真 DOM 也不向 disabled 派发 click）
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

/** 计数式分配的本地计数（票 05）：同一 `pending.seq` 内跨重绘保留；换问即清零。
 *
 * 计数是**本地态**（服务端看不到玩家点了几次），提交时由 :func:`distributionAction`
 * 拼成候选里那条 `Distribute`；超时同理——前端先把手上的计数交出去，服务端先到点则按
 * 全 0 代答（`legal_actions[0]`）。
 */
function distributionCounts(pending) {
  const seq = pending && pending.seq !== undefined && pending.seq !== null ? pending.seq : null;
  if (!state.distribution || state.distribution.seq !== seq) {
    state.distribution = { seq, counts: {} };
  }
  return state.distribution.counts;
}

/** 当前分配合计（点数总和）。 */
function distributionTotal(counts) {
  return Object.values(counts || {}).reduce(
    (sum, value) => sum + (Number(value) || 0),
    0,
  );
}

/** 计数式分配面（神周瑜移动版【业炎】；huo-shenzhouyu-mobile 票 05）。
 *
 *  逐座位一行：点一行 = 计数 +1（合计到上限 / 单座到上限后该行不可点）；
 *  底下「重置」清零；右端「提交」恒可点（全 0 也是合法分配）。
 *  与 `renderOptionButtons` 同一个容器（`#t-options`，随后整体搬进弹窗正文）。
 */
function renderDistribution(pending) {
  const box = $("t-options");
  if (!box) return;
  const info = pending.distribution || {};
  const seats = Array.isArray(info.seats) ? info.seats : [];
  const maxTotal = Number(info.max_total) || 0;
  const perSeat = Number(info.per_seat_max) || 0;
  const counts = distributionCounts(pending);
  const total = distributionTotal(counts);
  const hint = $("t-target-hint");
  if (hint) {
    hint.textContent = `点一行 = 计数 +1；合计至多 ${maxTotal} 点`
      + `（单座至多 ${perSeat} 点）；可「重置」，随时「提交」。`;
  }
  seats.forEach((entry) => {
    const seat = Number(entry.seat);
    const label = entry.label
      || (entry.name ? `${entry.seat}号位${entry.name}` : `${entry.seat}号位`);
    const count = Number(counts[seat] || 0);
    const button = document.createElement("button");
    button.type = "button";
    button.className = "opt distribution-row";
    button.disabled = total >= maxTotal || count >= perSeat;
    const name = document.createElement("span");
    name.className = "distribution-name";
    name.textContent = label;
    const badge = document.createElement("span");
    badge.className = "distribution-count";
    badge.textContent = String(count);
    button.appendChild(name);
    button.appendChild(badge);
    button.addEventListener("click", () => {
      if (button.disabled) return;
      counts[seat] = count + 1;
      state.lastRejectReason = "";
      renderPending(state.view);
    });
    box.appendChild(button);
  });
  const reset = document.createElement("button");
  reset.type = "button";
  reset.className = "opt distribution-reset";
  reset.textContent = "重置";
  reset.disabled = total === 0;
  reset.addEventListener("click", () => {
    state.distribution = { seq: pending.seq, counts: {} };
    state.lastRejectReason = "";
    renderPending(state.view);
  });
  box.appendChild(reset);
}

/** 这一面要画哪些选项值（M5-bugfix）：一次性二选一画**完整的一对**，其余只画候选真有的。
 *
 * 配对来自 :data:`OPTION_PAIRS`——只要候选里有同一对的一项，就把整对都画出来；另一项
 * 是否可提交仍只看它在不在 ``legal_actions`` 里（引擎裁决），这里只决定"要不要把另一条
 * 画成禁用"。没有一次性选项的步骤（花色等复用枚举）原样，且不会把别的技能的配对摊进来。
 */
function optionChoices(pending) {
  const legal = ((pending && pending.legal_actions) || [])
    .filter((action) => action.kind === "SelectOption" && !isRightAction(action))
    .map((action) => action.choice);
  const pair = OPTION_PAIRS.find((choices) => choices.some((choice) => legal.includes(choice)));
  if (!pair) return legal;
  return [...new Set([...pair, ...legal])];
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

/** 一次性二选一的**中文标签**（U2-04）：雌雄双股剑 / 界许褚【裸衣】。
 *
 * 这些是**一次性**的小选项（spec D3「≤3 且一次性 ⇒ 提示区左侧按钮」），中文名写在
 * 这里；可复用的枚举（花色 / 【杀】的类型 / 锦囊牌名）中文标签属服务端展示契约：
 * 票 08 起花色由载荷的 `option_labels` 下发（见 :func:`optionLabel`），前端不另抄第二份。
 * 查不到标签的值原样显示（fail-visible，不猜中文）。
 *
 * `discard_one` 只在目标**有手牌**时由引擎发出（`skills/catalog.py`）；界面不自己补
 * 这条**合法性**，但会把已知的另一项画成禁用（见 :func:`optionChoices`）。
 *
 * `draw_one` 的"令谁摸"读服务端派生的 `option_source`（M5-bugfix）：
 * `{"seat": N, "name": <武将名或 None>}` = 雌雄双股剑的持有者。没有这份展示事实时
 * 退回旧文案「令其摸一张牌」——前端不按回合 / 使用者自己推来源。
 */
const OPTION_LABELS = Object.freeze({
  discard_one: "弃置 1 张手牌",
  draw_one: "令其摸一张牌",
  // 界许褚【裸衣】（用户裁定）：亮出三张后的「获得 / 不获得」。
  luoyi_gain: "获得",
  luoyi_skip: "不获得",
  // 攻心（票 01；界吕蒙 ☆ / 神吕蒙移动版共用）：展示红桃后的二选一。
  discard: "弃置此牌",
  deck_top: "将此牌置于牌堆顶",
  // 韬·刘备自制版【从讨】：另一张的去处（两项恒可执行、不可放弃）。
  congtao_discard: "置入弃牌堆",
  congtao_deck_top: "放回牌堆顶",
  // 韬·刘备自制版【义起】：类型三选一（恒三项，必须选一项）。
  basic: "基本牌",
  trick: "锦囊牌",
  equipment: "装备牌",
  // 风包·移动版于吉【蛊惑】：其他角色逐个表态（两选项，必答；"相信"就是放弃质疑）。
  guohuo_doubt: "质疑",
  guohuo_trust: "相信",
  // 风包夏侯渊【神速】：一至两项（三档单选项；"两项都选"是【1】+【2】）。
  shensu_skip_judge_draw: "【1】跳过判定阶段和摸牌阶段",
  shensu_skip_play_equip: "【2】跳过出牌阶段并弃置一张装备牌",
  shensu_both: "两项都选",
  // 火包移动版典韦【强袭】（huo-dianwei-mobile 票 01）：代价二选一（可以放弃）。
  qiangxi_lose_hp: "失去1点体力",
  qiangxi_discard_weapon: "弃置一张武器牌",
});

/** 一次性二选一的**配对**：只在候选里出现了同一对的一员时，才把整对画出来。
 *
 * 不能把 :data:`OPTION_LABELS` 的所有键一把摊开：两张技能各有各的一对，摊开会让
 * 【雌雄双股剑】的询问多出两个永远禁用的「获得 / 不获得」。配对是**通用**的展示
 * 形状，不按技能 id 分支；合法性仍只看候选里有没有那一条。
 */
const OPTION_PAIRS = Object.freeze([
  Object.freeze(["discard_one", "draw_one"]),
  Object.freeze(["luoyi_gain", "luoyi_skip"]),
]);

/** 选项按钮上的文字：已知的一次性选项用中文；其余选项保留**原值**（复用标签待票 08）。 */
function optionText(choice, pending) {
  if (choice === "draw_one" && pending && pending.option_source) {
    const source = pending.option_source;
    return source.name
      ? `令座位号 ${source.seat} ${source.name}摸一张牌`
      : `令座位号 ${source.seat} 摸一张牌`;
  }
  return OPTION_LABELS[choice] || choice;
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

/** 动作种类 → 中文短语（U4-06 / D13）。
 *
 * 投影只把**人类自己的**应答展开；别人的 action 只剩 `kind` / `answers_seq`。事件流与
 * 中央「当前动作」因此必须能读**裁过的**载荷：这里给每个已知种类一句中性中文，
 * **不**回退到英文构造子名（`UseCard` / `ArrangeCards` / `SelectOption`），也不猜细节。
 */
const ACTION_KIND_LABELS = Object.freeze({
  UseCard: "使用牌",
  PlayCard: "打出牌",
  SubstituteJudgeCard: "改判",
  ActivateSkill: "发动技能",
  SelectCards: "选牌",
  SelectOption: "做出选择",
  ArrangeCards: "排列手牌",
  DiscardCards: "弃牌",
  Reforge: "重铸",
  EndPhase: "结束出牌阶段",
  Pass: "放弃",
});

/** 技能动作的中文名：`action.skill_name`（服务端接入层展示表）优先；旧载荷退回
 *  `view.skills` 的展示名；都查不到才退回 skill_id（fail-visible，前端不新建映射）。 */
function skillActionName(action) {
  if (action && action.skill_name) return action.skill_name;
  const skill = (((state.view && state.view.skills) || []).find(
    (item) => item.skill_id === action.skill_id,
  ));
  if (skill && skill.name) return skill.name;
  return action.skill_id || null;
}

/** 技能候选的动作短语（U2-03 直提态）：技能名读服务端下发的 `view.skills`，
 *  前端不在这里抄中文名；查不到时退回技能 id（fail-visible，不猜名字）。 */
function skillActionLabel(action) {
  const name = skillActionName(action);
  return name ? `发动【${name}】` : "发动技能";
}

function actionLabel(action) {
  if (action.kind === "UseCard" || action.kind === "PlayCard") {
    const targets = action.targets && action.targets.length ? ` → 座位 ${action.targets.join("、")}` : "";
    return `使用【${cardName(action.card)}】${targets}`;
  }
  if (action.kind === "SubstituteJudgeCard") {
    // 改判**不是打出**（feng-zhangjiao 票 02）：事件流写「改判【…】」，不写「打出 / 使用」。
    return action.card ? `改判【${cardName(action.card)}】` : "改判";
  }
  if (action.kind === "Reforge") {
    // 重铸不是使用（M5-08b / D6）：事件流与任何候选文案都写「重铸」，不落英文构造子名。
    // 投影对**别人的**应答会裁掉 card（只留 kind / answers_seq）：没有牌面时只写动作。
    return action.card ? `重铸【${cardName(action.card)}】` : "重铸";
  }
  if (action.kind === "ActivateSkill") {
    const picks = action.picks && action.picks.length ? `（选 ${action.picks.length} 张）` : "";
    return `${skillActionLabel(action)}${picks}`;
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
  return ACTION_KIND_LABELS[action.kind] || "应答";
}

/** 右端动作在候选里的下标；`-1` = 当前询问没有这一条（按钮隐藏）。 */
function rightActionIndex(pending, kind) {
  const actions = (pending && pending.legal_actions) || [];
  return actions.findIndex((action) => action.kind === kind);
}

/** 提示条右端：结束出牌 / 放弃只在**自己的**询问里、且候选真有那一条时出现。
 *
 * 选择型 / 未知区域 / 亮牌弹窗（票 08 / 09 / 11）**没有「放弃」**：引擎给这几步的候选可能
 * 带 `Pass`（流水线语义的"取消这一步"），但界面上弹窗内不可放弃——右端放弃一并藏起，
 * `cancelAction` 也不再读它（见 `pending.js`）。放弃只留在**发动询问**那一步
 * （反馈 / 寒冰剑的"是否发动"、拆 / 顺是普通用牌）。
 */
function renderRightActions(pending, view = state.view) {
  const own = isOwnPending(pending, view);
  const dialogKind = pending ? stepDialogKind(pending) : null;
  // 弹窗步骤没有「放弃」：判据与 `cancelAction` / `refreshSubmit` 共用一处
  // （`modal.js:dialogDisablesPass`，评审 Standards：重复谓词）。
  const dialogWithoutPass = dialogDisablesPass(dialogKind, pending);
  const endIndex = own ? rightActionIndex(pending, RIGHT_ACTION_KINDS.endPhase) : -1;
  // 票 01：涉猎的「放弃」不是一条 `Pass` 候选，而是"提交当前选择"的第二个出口（与「提交」
  // 同义）。它不走候选下标，单独标记；点击由 `answer.js:submitRightAction` 特判。
  const suitForm = own && !!pending && isSuitPickStep(pending);
  const passIndex = own && !dialogWithoutPass
    ? rightActionIndex(pending, RIGHT_ACTION_KINDS.pass)
    : -1;
  // 被动入口的「取消」本身就是「放弃」（送出那条 Pass）：不再复制一个同义的右端按钮。
  const cancelIsPass = passIndex >= 0 && isPassivePreativation(pending);
  const passVisible = passIndex >= 0 || suitForm;
  const end = $("btn-end-phase");
  const pass = $("btn-pass");
  if (end) {
    end.hidden = endIndex < 0;
    end.disabled = state.playback || endIndex < 0;
  }
  if (pass) {
    pass.hidden = !passVisible || cancelIsPass;
    pass.disabled = state.playback || !passVisible || cancelIsPass;
    pass.textContent = suitForm
      ? "放弃"
      : (passIndex >= 0
        ? alternativeLabel(pending, pending.legal_actions[passIndex])
        : "放弃");
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

/** 只从自己手牌 / 装备区选一张的**牌桌步**（火攻 / 铁骑同花色弃牌）：不铺槽位块，直接点牌。
 *
 * 提交键恒灰（点牌就是一次提交）；「放弃」在右端，是候选里真有的那条 `Pass`。
 */
function renderOwnHandPick(pending) {
  const index = (pending.legal_actions || []).findIndex((action) => action.kind === "SelectCards");
  if (index >= 0) state.selectedOption = index;
  const hint = $("t-target-hint");
  if (hint) {
    const requirement = (pending.context && pending.context.requirement) || "选择一张牌";
    const where = ownPickHasEquip(pending) ? "手牌或装备区里的牌" : "手牌";
    hint.textContent = `${requirement}：点一张亮起的${where}即提交；也可以点右端「放弃」。`;
  }
  $("btn-cancel").disabled = true;
}

/** 从自己手牌 / 装备区选**多张**的牌桌步（结姻两张 / 贯石斧两张等）：同样不铺槽位块。
 *
 * 点牌 = 收进 / 移出 `state.picks`（面内位置）；「提交」按候选骨架补 `picks` 后送出，
 * 右端「放弃」是候选里真有的那条 `Pass`。
 */
function renderOwnZonePick(pending) {
  const index = (pending.legal_actions || []).findIndex(
    (action) => action.kind === "SelectCards");
  if (index >= 0) state.selectedOption = index;
  const hint = $("t-target-hint");
  if (hint) {
    const requirement = (pending.context && pending.context.requirement) || "选择牌";
    const where = ownPickHasEquip(pending) ? "手牌或装备区里的牌" : "手牌";
    const total = (pending.spec && pending.spec.total) || [1, 1];
    const least = Math.max(1, Number(total[0]) || 1);
    const most = Math.max(least, Number(total[1]) || least);
    const howMany = least === most ? `${least} 张` : `${least}~${most} 张`;
    hint.textContent = `${requirement}：点选${where}中的 ${howMany}，再点「提交」；`
      + "也可以点右端「放弃」。";
  }
  $("btn-cancel").disabled = state.picks.length === 0 && !cancelAction(pending);
}

/** 只从自己手牌 / 装备区选一张：点一张牌 = **恰一条** `SelectCards`（面内位置）。
 *
 * 送出的仍是 `legal_actions` 里的那条骨架（`buildAction` 补 `picks`），不手拼 Action；
 * 与未知区域 / 亮牌模板的「一击即提交」同一姿态。合成载荷 / 尚未建局时只留内部态。
 */
async function submitOwnHandSinglePick(pending, slotIndex) {
  if (state.playback || state.submitBusy) return;
  if (!isOwnHandSinglePick(pending)) return;
  const actions = pending.legal_actions || [];
  const at = actions.findIndex((action) => action.kind === "SelectCards");
  if (at < 0) return;
  const before = selectionSnapshot();
  state.selectedOption = at;
  state.focusCardId = null;
  state.selectedTargets = [];
  state.picks = [slotIndex];
  state.preactivation = null;
  state.lastRejectReason = "";
  if (!state.sessionId) {
    renderPending(state.view);
    return;
  }
  await submitAction(before);
}

/** 焦点手牌此刻的**重铸**候选；`null` = 这张牌不能重铸 / 不在出牌阶段。
 *
 * 唯一判据是 `legal_actions` 里真的有一条 ``Reforge`` 且牌号相同（M5-08b / D6）：
 * 前端**不**写"这张牌可重铸"的牌名表，也不读阶段名。服务端只在出牌阶段、且手里有
 * 可重铸牌时才发这个构造子（`effects/actions.py`）。
 */
function reforgeAction(pending, cardId) {
  if (!pending || cardId === null || cardId === undefined) return null;
  return (pending.legal_actions || []).find(
    (action) => action.kind === "Reforge"
      && action.card
      && String(action.card.card_id) === String(cardId),
  ) || null;
}

/** 控件区里的**重铸入口**（M5-08b）：与「使用」并列的另一个动作，不是它的一个目标。
 *
 * 只在手选路（出牌阶段）且**已选中一张可重铸的牌**时出现；按钮点一下即提交一条
 * ``Reforge``（见 :func:`submitReforge`）。它不铺目标、不进目标选择，也不开门。
 * 弹窗步骤里 `selectionEnabled()` 为假，入口不出现（只读判据不另写一份）。
 *
 * **牌名级预发动与重铸并存**（票 05 / 缺陷 A 的落法 = 并存）：♣【借刀杀人】这类牌同时有
 * `pending.card_preactivations` 的两步清单与【连环】授予的 ``Reforge``。点牌给焦点后
 * （`card-face.js:activateCard`），这里除了「重铸」再并列一个「使用【X】」——清单仍是
 * **使用**的入口，不进重铸；把重铸塞进清单会与"重铸不是使用"的口径打架。
 */
function renderReforgeEntry(pending) {
  if (!isHandPick(pending) || state.focusCardId === null) return;
  if (!selectionEnabled()) return;
  const action = reforgeAction(pending, state.focusCardId);
  if (!action) return;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "opt reforge";
  button.setAttribute("data-reforge", "true");
  button.textContent = `重铸【${cardName(action.card)}】`;
  button.title = "重铸：弃置这张牌并摸一张牌（不算使用，不消耗使用次数）。";
  button.addEventListener("click", () => submitReforge(pending, action));
  $("t-options").appendChild(button);
  renderCardPreativationEntry(pending, action.card);
}

/** 焦点手牌若**另有**牌名级预发动（服务端下发的清单），并列一个「使用【X】」入口（票 05）。
 *
 * 点牌已改由重铸入口接管（见 `card-face.js:activateCard`），使用方向不能再没有入口——
 * 裸【借刀杀人】在 `legal_actions` 里**没有** ``UseCard`` 候选（`tests/test_web.py` 钉），
 * 清单就是它唯一的使用路径。判据只读服务端下发的 `pending.card_preactivations[card.type]`，
 * 前端不认牌名。
 */
function renderCardPreativationEntry(pending, card) {
  const manifest = cardPreactivationManifest(pending, card);
  if (!manifest) return;
  // 优先用快照里的**展示副本**（候选里那张牌故意不带 `display_*`，见 `core.js:cardName`）。
  const shown = selfHandCard(card.card_id) || card;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "opt";
  button.setAttribute("data-preactivation-card", String(card.card_id));
  button.textContent = `使用【${cardName(shown)}】`;
  button.title = `按清单逐步选${preactivationPickWording(manifest)}；重铸不走清单。`;
  button.addEventListener("click", () => enterCardPreativation(pending, shown, manifest));
  $("t-options").appendChild(button);
}

/** 点一次「重铸」= **恰一次**真实 `Reforge` 提交（M5-08b / §5.1.3）。
 *
 * 提交的仍是 `legal_actions` 里扫描到的那一条（不手拼 Action）；选中目标一律丢弃
 * ——重铸没有目标。合成载荷 / 尚未建局时只留内部选中态，不发请求。
 */
async function submitReforge(pending, action) {
  if (state.playback || state.submitBusy || !selectionEnabled()) return;
  const at = (pending.legal_actions || []).indexOf(action);
  if (at < 0) return;
  const before = selectionSnapshot();
  state.selectedOption = at;
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

function renderSlots(pending) {
  // 亮牌模板（票 11 五谷 / 票 12 遗计）：全部亮牌 + 目标条；**不**走下面那条
  // 「隐藏区域的槽位只有位置」的旧选择面（公开处理区的牌面是可见的）。
  const dialogKind = stepDialogKind(pending);
  // 票 01：按花色取牌（涉猎）——一行公开亮牌 + 同花色置灰，单独一个模板。
  if (dialogKind === DIALOG_KINDS.suit) {
    renderSuitPick(pending, { interactive: true });
    return;
  }
  if (dialogKind === DIALOG_KINDS.reveal) {
    renderReveal(pending, { interactive: true });
    return;
  }
  // 未知区域弹窗（票 08 盲挑 → 票 09 推广到反馈 / 拆 / 顺 / 寒冰剑，见 `isUnknownZoneStep`）：
  // 内容画成「?」槽位 + 可见牌面，点一张即提交；其余选择面（五谷 / 遗计 / 自己的牌）仍走
  // 下面的既有槽位面——它们各有自己的模板（票 11 / 12）。
  if (dialogKind === DIALOG_KINDS.unknown) {
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
  // 武将牌上的牌（huo-shenzhugeliang 票 02）：牌面对宿主**可见**，故不能沿用"隐藏区域 /
  // 看不到牌面"那句话（那是对拆 / 顺 / 反馈那段说的）。判据只看 dialog kind。
  heading.textContent = dialogKind === DIALOG_KINDS.heroPick
    ? `武将牌上的牌（只有你可见）：这一步${pickText}，点选后提交。`
    : `选择面：隐藏区域的槽位只有位置（看不到牌面），选中并结算后才揭晓。这一步${pickText}。`;
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
  // 例外：武将牌上的牌点选（huo-shenzhugeliang 票 02）的**正文就在弹窗里**，此时
  // `selectionEnabled()` 为假（弹窗开着 = 牌桌不可选）——但那一面必须可点。
  if (!selectionEnabled() && stepDialogKind(state.view ? state.view.pending : null) !== DIALOG_KINDS.heroPick) {
    return;
  }
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
