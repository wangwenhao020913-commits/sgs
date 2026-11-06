/* SGS 前端 · 询问渲染：按询问种类分派到各自的专用面。
 *
 * 没有「合法行动逐条铺按钮」的候选列表逃生梯（U2-03）：能答的九类询问各有专用面，
 * 其余一律 fail-closed（「这一步没有可用的界面」）。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------ 询问归属 */

/** 这一问是不是**自己**的（U1-03）：只有询问者 = 查看者座位时才铺可点控件。
 *
 * 查看者真源与服务端同一条：会话载荷的 `human_seat`（缺省退回投影快照的 `seat`）。
 * 其他座位的询问只在那个座位块下方显示一行文本（spec §3.3）。查看者缺失 / 未声明时
 * **不认作自己的**（fail-closed，与服务端 `viewer is None ⇒ foreign` 同姿态）；旧载荷
 * 没有 `actor` 时才按"自己的"处理（回放 / 合成载荷的兜底）。
 */
function isOwnPending(pending, view = state.view) {
  if (!pending) return false;
  if (pending.actor === null || pending.actor === undefined) return true;
  const viewer = view ? (view.human_seat ?? (view.snapshot && view.snapshot.seat)) : null;
  if (viewer === null || viewer === undefined) return false;
  return Number(pending.actor) === Number(viewer);
}

/** 倒计时锚点：同一询问序号在轮询刷新时**不**重置（只有新询问才重新起表）。 */
function syncPendingClock(view) {
  const seq = view && view.pending ? view.pending.seq : null;
  const clock = state.pendingClock;
  if (seq === null || seq === undefined) {
    clock.seq = null;
    clock.receivedAt = 0;
    return;
  }
  if (seq !== clock.seq) {
    clock.seq = seq;
    clock.receivedAt = Date.now();
  }
}

/** 右端动作（结束出牌 / 放弃）的稳定报文值：**唯一**一份前端常量。
 *
 * 这两个动作不占"简单询问的浮动选项行"，统一由提示条右端画（`renderRightActions`）；
 * 主按钮绑定与候选查找都读这里，别在别处再写字符串字面量。
 */
const RIGHT_ACTION_KINDS = Object.freeze({ endPhase: "EndPhase", pass: "Pass" });

function isRightAction(action) {
  return !!action
    && (action.kind === RIGHT_ACTION_KINDS.endPhase || action.kind === RIGHT_ACTION_KINDS.pass);
}

/* ------------------------------------------------------------ 询问渲染 */

/** **清空询问面**：提示文本 / 询问文本 / 技能串 / 目标提示 / 选项格 / 询问弹窗。
 *
 * 回放期演的是"上一问**已经答完**"之后发生的事（`session.js:applyView` 在起播时调用）：
 * 旧询问的这几样都不再属于当前这一步。此前只有 `#t-prompt` / `#t-choice` 被清，其余
 * 五样一直挂到**下一次** `renderPending` 才被冲掉——那一问可能隔着几十条记录，于是：
 * 观星的排列弹窗在效果演完前一直留在场上、五谷的亮牌格留在提示区、其它座位的询问来了
 * 提示区还是上一问的技能文案（M6 实机报障的三条同源症状）。
 *
 * **亮牌窗例外**：`state.dialog.kind` 是 `reveal` 时窗口与它的正文都留着——亮牌是公开 /
 * 授予的牌面展示，记录流还会往里追加（洛神的连续判定靠 `refreshJudgmentReveal` 把 JUDGE
 * 记录接在已开的窗口上；五谷的持久亮牌跨整个结算）。它自己的出口有两条：源结算帧出栈
 * （`modal.js:syncPlaybackDialog`）或下一问提交（`syncDialog`）。持久亮牌模式下正文画在
 * `#t-dialog-reveal`、`#t-options` 留在提示区 ⇒ 那份仍属于上一问，照样清。
 */
function blankAskSurface() {
  const prompt = $("t-prompt");
  if (prompt) prompt.textContent = "";
  const choice = $("t-choice");
  if (choice) choice.textContent = "";
  const hints = $("t-skill-hints");
  if (hints) hints.textContent = "";
  const targetHint = $("t-target-hint");
  if (targetHint) targetHint.textContent = "";
  const promptSkills = $("t-prompt-skills");
  if (promptSkills) promptSkills.innerHTML = "";
  const options = $("t-options");
  const body = $("t-dialog-body");
  // 正文这时**挂在弹窗正文槽里**吗（`renderPending` 的分派器把它写进 `#t-options`，
  // `modal.js:applyDialogContentMode` 再把它搬进正文槽）——亮牌窗的累积正文就在那儿。
  const bodyOwnsOptions = !!options && !!body && options.parentNode === body;
  // 票 01：涉猎的 2.5 秒结果态也跨"提交后的这一批回放"保留（与亮牌窗同一条理由：
  // 起播即让位会把刚刷新的结果当场收掉，玩家看不到"拿走了哪几张"）。
  const keepDialog = state.dialog.open
    && (state.dialog.kind === DIALOG_KINDS.reveal
      || (state.dialog.kind === DIALOG_KINDS.suit && suitResultActive()));
  if (!keepDialog) {
    // 弹窗收起时 `closeDialogDom()` 会把 `#t-options` 搬回提示区，故清格子放在它**之后**：
    // 上一问的排列面 / 选项 / 槽位不跟着回到提示条上。
    closeDialogDom();
    if (options) options.innerHTML = "";
    return;
  }
  if (!bodyOwnsOptions && options) options.innerHTML = "";
}

/** 手选路径：点牌设焦点、点座位选目标、选出一组**候选里真有**的组合才可提交。 */
function renderPending(view) {
  const pending = view && view.pending;
  // 票 01：涉猎提交后的 2.5 秒结果态（仅发动者本地）。它**压过**下一问的表面直到计时
  // 结束：牌桌已按新视图重画，询问面留在这里展示"拿走了哪几张"；计时结束再画真正的下一步。
  if (!state.playback && suitResultActive()) {
    renderSuitResult();
    setActionLabels(false, false);
    resetRightActions();
    resetCountdown();
    syncDialog();
    enforceActionFreeze();
    if (!state.suitResultTimer) {
      state.suitResultTimer = window.setTimeout(() => {
        state.suitResultTimer = null;
        state.suitResult = null;
        renderPending(state.view);
      }, Math.max(0, state.suitResult.until - Date.now()));
    }
    return;
  }
  syncPendingClock(view);
  // 重新渲染的界面是四键状态的**新真源**：丢弃上一次提交留下的冻结快照（请求还在飞时
  // `submitBusy` 为真，快照留着由 `enforceActionFreeze()` 继续压住四键）。
  if (!state.submitBusy) releaseActionButtons(false);
  $("t-options").innerHTML = "";
  // 弹窗模板可能给选项组换版式（选择型的 2×2 网格）；每个询问重新渲染时先复位，
  // 避免上一问的模板类名留在下一问。
  $("t-options").classList.remove("suit-options");
  // 亮牌模板（票 11）给正文加了下内边距（给左下角目标条让位）：换询问时复位。
  if ($("t-dialog-body")) $("t-dialog-body").classList.remove("reveal-body");
  // 询问文本：**需要做什么**（服务端 `context.requirement`）优先；没有就退回引擎的通用 prompt。
  // 亮牌（票 11）另有一条派生文案 `harvest.hint`：409 裸 Query 没有 `context` 时也用它，
  // 不会掉回引擎那句通用的「请从选择面里选择一张牌」（公开处理区的文案 bug）。
  const requirement = (pending && pending.context && pending.context.requirement)
    || (pending && pending.harvest && pending.harvest.hint)
    || null;
  $("t-prompt").textContent = requirement
    || (pending ? pending.prompt : "（没有待应答的询问）");
  // 预发动 / 转化技正在等玩家点牌时，说清"现在要做什么"：
  // 这些是**界面自己**开的模式，服务端的通用 prompt 读不出它。
  if (state.preactivation) {
    $("t-prompt").textContent = state.preactivation.cardEntry
      ? `使用【${state.preactivation.name}】：按清单选择持刀者与【杀】的目标。`
      : `预发动【${state.preactivation.name}】：按清单逐步选择。`;
  } else if (state.convertSkill !== null) {
    const skill = (view.skills || []).find((item) => item.skill_id === state.convertSkill);
    const text = skill && skill.ui && skill.ui.requirement;
    if (text) $("t-prompt").textContent = text;
  }
  // 描述文本 = 效果区（处理区）当前内容的说明，**不**再写服务端的"本窗口：谁对谁使用…"。
  // 那条只在询问到达时更新，回放期间会残留；效果区那行才与牌桌同步（用户裁定）。
  const contextNode = $("t-context");
  if (contextNode) contextNode.textContent = describeStage();
  // 这条窗口里**被提供**的技能（主动技 / 提供者 / 转化技）的"需要做什么"文案。
  const hintsNode = $("t-skill-hints");
  if (hintsNode) {
    const hints = (pending && pending.context && pending.context.skill_hints) || [];
    hintsNode.textContent = hints.map((hint) => `【${hint.name}】${hint.requirement}`).join("　");
  }
  $("btn-submit").disabled = true;
  $("btn-cancel").disabled = true;
  // 排列是**强制、无取消**（spec §4.3 / Q9(d)）：进入排列面时把取消按钮整个藏起来；
  // 其余询问路径照旧显示（本行是唯一的复位点，分支只负责按需隐藏）。
  $("btn-cancel").hidden = false;
  $("t-choice").textContent = "";
  const targetHint = $("t-target-hint");
  if (targetHint) targetHint.textContent = "";
  if (!pending) {
    setActionLabels(false, false);
    resetRightActions();
    resetCountdown();
    syncDialog();
    return;
  }
  // 其他座位的询问（U1-03）：提示条只写"等待"，可点控件一律不铺；该座位的武将块下方
  // 由 `renderRingSeats` 显示“正在响应…”。倒计时也不显示（AI 即时回答 / 他人窗口）。
  if (!isOwnPending(pending)) {
    $("t-prompt").textContent = "等待其他座位响应…";
    if (hintsNode) hintsNode.textContent = "";
    if (targetHint) targetHint.textContent = "";
    // 五谷（票 11 / D4）：AI 目标拿牌时亮牌弹窗仍留在人类屏幕上（只读、不可点）。
    // 正文照画、目标条按新状态更新；`interactive:false` 保证一张牌都不接 click。
    if (stepDialogKind(pending) === DIALOG_KINDS.reveal) {
      renderReveal(pending, { interactive: false });
    }
    // 票 01：涉猎的亮牌在公开处理区——其他人也看得到这条弹窗（只读；五张牌面公开）。
    if (stepDialogKind(pending) === DIALOG_KINDS.suit) {
      renderSuitPick(pending, { interactive: false });
    }
    // 票 93：两行观看面的位置是公开形状——非应答者也画两行、牌面为「?」、不给 click。
    if (stepDialogKind(pending) === DIALOG_KINDS.watchTwoRows) {
      renderWatchRows(pending, { interactive: false });
    }
    $("t-choice").textContent = "";
    $("btn-submit").disabled = true;
    $("btn-cancel").disabled = true;
    resetRightActions();
    resetCountdown();
    syncDialog();
    return;
  }
  const actions = pending.legal_actions || [];
  const kind = renderKind(pending, actions);

  if (kind === "picker") renderPicker(pending);
  else if (kind === "discard") renderDiscard(pending);
  else if (kind === "preactivation") renderPreativation(pending);
  // 同一时机的激活面（票 02）：N ≥ 2 条技能候选 ⇒ 一张面选一条发动（末条「都不发动」）。
  else if (kind === "activation-face") renderActivationFace(pending, actions);
  else if (kind === "preactivation-entry") renderPreativationEntry(pending);
  else if (kind === "seat") renderSeatPick(pending);
  else if (kind === "own-hand-pick") renderOwnHandPick(pending);
  else if (kind === "own-zone-pick") renderOwnZonePick(pending);
  else if (kind === "slots") {
    // 选择面也要**指到那条候选**：引擎只发一条 `SelectCards` 骨架，具体 `picks` 由
    // `buildAction()` 补上；不指过去它就没有可补的对象 ⇒ `buildAction()` 返回 null ⇒
    // 点完槽位提交按钮仍然是灰的（本轮实测：点第 1 个槽位后 picks=[0] 而按钮不可点）。
    const index = actions.findIndex((action) => action.kind === "SelectCards");
    if (index >= 0) state.selectedOption = index;
    // 「提供一张 B」（票 14）：**就地**渲染（不铺选择面块），候选牌在牌桌上直接亮起。
    if (isProvide(pending)) renderProvide(pending);
    else renderSlots(pending);
    // 选择面这一步可不可以取消（票 01 的技能流水线给出一条 `Pass`）。
    $("btn-cancel").disabled = state.picks.length === 0 && !cancelAction(pending);
  }
  else if (kind === "direct") renderDirect(actions[0], pending);
  // 技能候选的直提态：无清单 = 「将提交：发动【技能名】」；有清单 = 既有预发动入口。
  else if (kind === "skill") renderDirectSkill(pending, actions);
  // `SELECT_OPTION` 的非座位形态：一选项一个按钮的专用小选项组（不是候选列表）。
  else if (kind === "options") renderOptionButtons(pending);
  // 单选项可选确认（B1）：唯一选项自动选中，不画选项按钮；出口 = 提交 / 右端放弃。
  else if (kind === "option-confirm") renderOptionConfirm(pending);
  // 洛神连续判定的亮牌确认（A1）：同一弹窗换牌 + 单选项确认。
  else if (kind === "judge-reveal") renderJudgmentReveal(pending);
  else if (kind === "distribution") {
    // 计数式分配（票 05）：强制、无取消——唯一出口是右端「提交」（随时可点）。
    $("btn-cancel").hidden = true;
    $("btn-cancel").disabled = true;
    renderDistribution(pending);
  }
  else if (kind === "arrange") {
    // 排列（W6-03）：双行拖拽面；强制、无取消——唯一出口是「确认」。
    $("btn-cancel").hidden = true;
    $("btn-cancel").disabled = true;
    renderArrange(pending);
  }
  else {
    // fail-closed：没有专用面的询问不铺任何按钮，也不给右端动作（不发明合法性）。
    renderUnsupported(pending);
    setActionLabels(false, false);
    $("btn-submit").disabled = true;
    $("btn-cancel").disabled = true;
    resetRightActions();
    resetCountdown();
    syncDialog();
    enforceActionFreeze();
    return;
  }

  // 提交 / 取消两个按钮的**文案**跟着模式走（预发动 = 确认发动 / 取消预发动；被动入口 = 放弃；
  // 排列 = 确认）。
  setActionLabels(state.preactivation !== null, isPassivePreativation(pending), kind === "arrange");
  // 提交按钮的启用与否**只算一次**（每个分支只管"铺界面"）：分散在各分支里写过一次
  // 就会漏掉一条路径（`SELECT_CARDS` 的选择面就因此永远灰着）。
  refreshSubmit(pending);
  renderRightActions(pending, view);
  renderCountdown(view);
  // U2-06：询问弹窗的生命周期在**每次渲染末尾**按当前步骤的表面同步一次（开 / 关 /
  // 同一问只换正文）。它必须是最后一步：上面的渲染器刚把内容写进 `#t-options`。
  syncDialog();
  // 请求在飞期间四键保持禁用（这一条盖住任何在飞期间发生的重绘）。
  enforceActionFreeze();
}

/** 底部两个按钮的文案：预发动 = 「确认发动 / 取消预发动」；手牌入口 = 「确认使用 / 取消选择」；
 *  被动入口 = 「放弃」；排列 = 「确认」（取消按钮在排列面里整个隐藏）。 */
function setActionLabels(inPreativation, passiveEntry, arrange = false) {
  const submit = $("btn-submit");
  const cancel = $("btn-cancel");
  const cardEntry = Boolean(inPreativation && state.preactivation && state.preactivation.cardEntry);
  if (submit) {
    submit.textContent = arrange
      ? "确认"
      : (inPreativation ? (cardEntry ? "确认使用" : "确认发动") : "提交");
  }
  if (cancel) {
    cancel.textContent = cardEntry
      ? "取消选择"
      : (inPreativation
        ? (passiveEntry ? "放弃" : "取消预发动")
        : (passiveEntry ? "放弃" : "清空选择"));
  }
}

/** 单选项可选确认（B1）：`SELECT_OPTION` 的候选里**恰有一个** `SelectOption`，且还有
 *  一条 `Pass`。这是"做 / 不做"的确认形状（洛神的「是否继续」、涯角的「是否弃牌」、
 *  界限的「是否弃置」），界面不再画那一个选项按钮：唯一选项自动选中，「提交」送它、
 *  「放弃」送 `Pass`。
 *
 *  判据只读 `legal_actions` 的形状：不认技能 id、不认选项值；有两个以上选项的询问仍走
 *  小选项按钮组，选择型（`option_labels` / ≥4）仍走自己的弹窗。选座位形态另走 `seat`。
 */
function isSingleOptionalOption(pending) {
  if (!pending || pending.kind !== "SELECT_OPTION") return false;
  if (pending.option_seats && Object.keys(pending.option_seats).length) return false;
  const actions = pending.legal_actions || [];
  const options = actions.filter(
    (action) => action.kind === "SelectOption" && !isRightAction(action),
  );
  return options.length === 1
    && actions.some((action) => action.kind === "Pass");
}

/** A1：洛神连续判定的**亮牌确认**（服务端派生 `judgment_reveal.cards`）。
 *
 *  `judgment_reveal.final !== true` ⇒ 这一问仍是循环里的"看本次已翻出的判定牌，提交继续 /
 *  放弃停止"；`cards` 是**有序累积**（第一张到当前这张），前一张不消失。
 *  `final === true` ⇒ 循环已结束、只用于关窗（不画弹窗）。判据只看服务端字段，不认技能名。
 */
function isJudgmentRevealOption(pending) {
  const cards = pending && pending.judgment_reveal
    ? pending.judgment_reveal.cards
    : null;
  return !!pending
    && pending.kind === "SELECT_OPTION"
    && !!pending.judgment_reveal
    && pending.judgment_reveal.final !== true
    && Array.isArray(cards) && cards.length > 0;
}

/** 这次询问该走哪条渲染路径。
 *
 * - `picker`：出牌阶段 / 响应窗口 —— 点牌 + 点目标；
 * - `discard`：弃牌阶段 —— 点手牌收进待弃置集合；
 * - `slots`：`SELECT_CARDS` 的选择面（点槽位）；
 * - `arrange`：`ARRANGE` 的双行拖拽面（W6-03；规格由投影下发）；
 * - `preactivation` / `preactivation-entry`：清单驱动的本地态 / 被动入口；
 * - `seat` / `options`：`SELECT_OPTION` 的选座位形态 / 小选项按钮组；
 * - `option-confirm`：单选项可选确认（B1；只画「提交 / 放弃」）；
 * - `judge-reveal`：洛神连续判定的亮牌确认（A1；弹窗 + 单选项确认）；
 * - `skill`：技能候选的直提态（无清单，如 B 形"是否发动"）；
 * - `direct`：唯一且无需选目标的候选 —— 不铺按钮，只说"将提交：…"；
 * - `unsupported`：没有专用面 —— fail-closed（不退回候选列表，不发明合法性）。
 *
 * **`list` 逃生梯已删除**（U2-03 / spec D5）：界面不再把 `legal_actions` 逐条铺成按钮。
 */
function renderKind(pending, actions) {
  // 选择面**永远**要渲染：它的交互在 `slots` 上（点槽位），不在候选条数上。
  // 引擎为 `SELECT_CARDS` 只生成**一条**候选（`SelectCards` + 全部 picks 的组合是前端拼的），
  // 所以"只有一条候选"绝不能理解成"没什么可选的、直接提交"。
  if (isOwnHandSinglePick(pending)) return "own-hand-pick";
  if (isOwnZonePick(pending)) return "own-zone-pick";
  // 空选择面 = 引擎仍要发的那条"只能放弃"询问（如【铁骑】没有同花色牌）：不铺空面板，
  // 只走唯一候选（Pass）的直提态；没有可选动作则 fail-closed。
  // 票 01：【攻心】的"观看"在没有红桃时也是空选择面——表面仍是亮牌弹窗，要画整副手牌
  // （正文在 `renderSlots` → `renderReveal`），不能退化成"将提交：放弃"的空弹窗。
  if (pending.kind === "SELECT_CARDS" && !(pending.slots || []).length) {
    if (stepDialogKind(pending) === DIALOG_KINDS.reveal) return "slots";
    // 票 93：凑不出四色的**空选择面**仍要画上行的整副手牌（牌面在 `revealed` 里），
    // 出口只有「放弃」——不能退化成"将提交：放弃"的空弹窗。
    if (stepDialogKind(pending) === DIALOG_KINDS.watchTwoRows) return "slots";
    return directAction(pending) ? "direct" : "unsupported";
  }
  if (pending.kind === "SELECT_CARDS") return "slots";
  // 弃牌阶段：不铺候选按钮，点手牌收进待弃置集合（`buildAction` 按 picks 找候选）。
  if (pending.kind === "DISCARD") return "discard";
  // 排列（W6-03）：**唯一**的渲染路径 —— 双行拖拽面；不在候选上铺 720 个按钮。
  // 规格缺失（409 裸 Query / 无技能定义）时也只走这条路径，由 `renderArrange` 画
  // "排列不可用"（fail-closed），**不**退回候选列表猜一个顺序。
  if (pending.kind === "ARRANGE") return "arrange";
  // 票 05：计数式分配（【业炎】）——窗内逐座位计数，不铺候选、不点牌桌。
  if (pending.kind === "DISTRIBUTE") return "distribution";
  // 预发动（W4-1）：清单驱动，不铺候选列表、不走手选。
  if (state.preactivation) return "preactivation";
  // 同一时机的激活面（票 02）：本轮 N ≥ 2 条技能候选 ⇒ 一张面选一条发动（末条
  // 「都不发动」）。判据只看候选条数（`isActivationFace`），**排在被动入口之前**——
  // 代表候选（`pending.skill_id`）可能正好是已翻预发动的那条，但这一问的答案不是它。
  // 单候选面（N = 1）掉到下面既有的两条支路（直提态 / 预发动入口），一字不变。
  if (isActivationFace(pending)) return "activation-face";
  // 被动入口（W4-2）：已翻预发动的技能在时机上只发一次询问并随询问下发清单。界面给它
  // 一个**入口**（技能按钮）与一条"放弃"（= `Pass`），不把骨架候选画成"发动技能 XXX"。
  if (isPassivePreativation(pending)) return "preactivation-entry";
  // `SELECT_OPTION` 的"选座位"形态（【遗计】"交给谁"）：**取消选项按钮**，只点场上武将。
  if (pending.kind === "SELECT_OPTION" && seatChoices(pending).length > 0) return "seat";
  // 洛神连续判定的亮牌确认（A1）：弹窗显示本张判定牌，同时仍是单选项确认。
  if (isJudgmentRevealOption(pending)) return "judge-reveal";
  // 单选项可选确认（B1）：唯一 `SelectOption` + 一条 `Pass` = 「提交 / 放弃」两出口，
  // 不再画那一个选项按钮。判据只看候选形状（选项条数 + Pass），不认技能 id；
  // 涯角 / 界限等既有单选项可选技能自动套同一形态。选择型（`option_labels` / ≥4）
  // 仍走自己的弹窗，不抢。
  if (isSingleOptionalOption(pending) && !isChoiceStep(pending)) return "option-confirm";
  // 其余 `SELECT_OPTION`（花色 / 二选一）：专用的小选项按钮组。
  if (pending.kind === "SELECT_OPTION" && actions.some((action) => action.kind === "SelectOption")) {
    return "options";
  }
  // 技能候选（点了底部技能按钮 / B 形"是否发动"）：无清单 = 直提态；有清单 = 既有入口。
  // 判据只看候选自己的 `skill_id` 与清单，不读技能名、不做第二条合法性。
  const skillAction = selectedSkillAction(pending, actions);
  if (skillAction) {
    return preactivationManifest(pending, skillAction.skill_id)
      ? "preactivation-entry"
      : "skill";
  }
  // 唯一且不需要选目标（放弃 / 结束阶段 / 唯一的无目标响应牌）：直接说"将提交：…"。
  // 判据用 `needsPicker`，**不能**只看"只有一条候选"——一条 `UseCard{杀, targets=(4,)}`
  // 也是唯一候选，但它必须让玩家先看见"打的是谁"。
  if (directAction(pending)) return "direct";
  // 手选询问（出牌阶段 / 响应窗口）**一律**走 picker：点牌 + 点目标。
  if (isHandPick(pending)) return "picker";
  return "unsupported";
}

/** 这一次询问里"要直提的那条技能候选"：显式选中优先；B 形"是否发动"询问里那条裸
 *  `ActivateSkill` 本身就是这一步的答案（技能按钮只说明、不接 click，见 U1-06）。
 *  `null` = 这一问没有技能直提态。 */
function selectedSkillAction(pending, actions) {
  const selected = state.selectedOption === null ? null : actions[state.selectedOption];
  if (selected && selected.kind === "ActivateSkill") return selected;
  if (pending && pending.kind === "SKILL_ACTIVATION") {
    const skills = actions.filter((action) => action.kind === "ActivateSkill");
    if (skills.length === 1) return skills[0];
  }
  return null;
}

/** 技能直提态（无预发动清单）：不铺任何按钮，只把"这一步将提交什么"写清楚。
 *
 * 内部仍把 `state.selectedOption` 指到那条 `ActivateSkill`（底部技能按钮设的是同一个
 * 值）：提交按钮据此启用，`buildAction()` 原样返回候选里的那一条（不拼 Action）。
 */
function renderDirectSkill(pending, actions) {
  const action = selectedSkillAction(pending, actions);
  if (!action) {
    renderUnsupported(pending);
    return;
  }
  const index = actions.indexOf(action);
  if (index >= 0 && state.selectedOption === null) state.selectedOption = index;
  $("t-choice").textContent = `将提交：${skillActionLabel(action)}`;
}

/** 没有专用面的询问：fail-closed。一句明话，不铺任何可点控件（含右端动作）。 */
function renderUnsupported() {
  $("t-choice").textContent = "这一步没有可用的界面";
}

/** ``SELECT_OPTION`` 里形如 ``seat:N`` 的选项（服务端已解析成 ``option_seats``）。 */
function seatChoices(pending) {
  return Object.keys((pending && pending.option_seats) || {});
}

/** 这一步能不能**取消**（票 01 的技能流水线）：候选里真有一条 `Pass` 吗。
 *
 * 与「清空选择」的区别：清空只动界面本地状态；取消要**送出一条合法候选**，服务端据此
 * 中止这一步（选中的牌不会被搬走、技能使用次数不消耗）。故只有候选里真有 `Pass` 时，
 * 取消按钮才接到提交上——其余情形仍是原来的"清空选择"，既有询问一字不改。
 */
function cancelAction(pending) {
  if (!pending) return null;
  if (pending.kind !== "SELECT_CARDS" && pending.kind !== "SELECT_OPTION") return null;
  // 弹窗表面**没有「放弃」**（spec D3 / 票 08 / 09 / 11）：选择型、未知区域与亮牌步骤
  // 即便引擎给了 Pass，界面也不读它——右端放弃被藏起，取消键因此只剩"清空本地选择"
  // （这里的 null 让 `cancelOrClear` 走清空分支，而不是偷偷替玩家送出一条 Pass）。
  const kind = stepDialogKind(pending);
  if (dialogDisablesPass(kind, pending)) return null;
  // 票 93：两行观看面的「放弃」只住**右端**（它送出候选里那条 `Pass`）；左端取消键仍是
  // "清空本地选择"（`cancelOrClear` 的既有语义），不替玩家送 Pass。
  if (kind === DIALOG_KINDS.watchTwoRows) return null;
  return (pending.legal_actions || []).find((action) => action.kind === "Pass") || null;
}

/** 取消：候选里真有 `Pass` 就送出去（服务端据此中止这一步），否则只清本地选择。 */
async function cancelOrClear() {
  if (state.playback || state.submitBusy) return;
  const pending = state.view ? state.view.pending : null;
  // 预发动（W4-1 / W4-2）：主动入口的取消只是退出**接入层**的本地态，**一个 action 都
  // 不提交**（回到同一个出牌阶段询问）。被动入口的取消是「放弃」——**送出那条 `Pass`**，
  // 时机随之消费、不重问（`GLOSSARY.md`「放弃」）；没进入本地态时同样可以直接放弃。
  if (state.preactivation || isPassivePreativation(pending)) {
    const passive = isPassivePreativation(pending);
    if (passive) {
      const pass = (pending.legal_actions || []).find((action) => action.kind === "Pass");
      if (pass) {
        // 「放弃」一击即弃：不重绘；失败时把点击前的选择快照还回去（U2-03）。
        const before = selectionSnapshot();
        state.selectedOption = (pending.legal_actions || []).indexOf(pass);
        state.preactivation = null;
        state.focusCardId = null;
        state.selectedTargets = [];
        state.picks = [];
        state.lastRejectReason = "";
        await submitAction(before);
        return;
      }
    }
    state.preactivation = null;
    state.lastRejectReason = "";
    renderTable();
    renderPending(state.view);
    return;
  }
  const pass = cancelAction(pending);
  if (!pass) {
    clearSelection();
    return;
  }
  const before = selectionSnapshot();
  state.selectedOption = (pending.legal_actions || []).indexOf(pass);
  state.focusCardId = null;
  state.selectedTargets = [];
  state.picks = [];
  state.preactivation = null;
  state.lastRejectReason = "";
  await submitAction(before);
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
  // 焦点牌若有独立的重铸候选，控件区并列给一个「重铸」入口（M5-08b / D6）。
  renderReforgeEntry(pending);
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

/** 被动入口（W4-2）：时机询问已带清单，界面只给**进入预发动**的入口与「放弃」。

 * 入口 = 提示条浮动选项行的「进入预发动」（主动技按钮也仍可点；被动 / 锁定技按钮
 * 按 U1-06 只做说明、不接 click）；这里**不**把骨架候选画成"发动技能 XXX"选项
 * （验收：界面上不再出现这种选项），只留一条真候选 `Pass`。
 */
function renderPreativationEntry(pending) {
  const skill = preactivatedSkill(pending);
  const manifest = skill ? preactivationManifest(pending, skill.skill_id) : null;
  const hint = $("t-target-hint");
  if (hint) {
    const name = skill ? skill.name : pending.skill_id;
    const passive = Boolean(skill && skill.ui && skill.ui.passive);
    // U1-06：被动 / 锁定技按钮本身不动作（只悬停看描述）；被动入口走浮动选项行。
    hint.textContent = passive
      ? `点下方「进入预发动【${name}】」；被动技按钮本身只说明，悬停可看描述。`
        + "或点右端「放弃」跳过这次时机（放弃后不再询问）。"
      : `点技能【${name}】进入预发动；`
        + "或点右端「放弃」跳过这次时机（放弃后不再询问）。";
  }
  // 「是否发动」的浮动选项行：进入清单是唯一选项；`Pass` 是右端动作（U1-03），
  // `取消` 的文案在这里就是「放弃」（`setActionLabels` 的被动入口分支）。
  if (skill && manifest) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "opt";
    button.textContent = `进入预发动【${skill.name}】`;
    button.addEventListener("click", () => enterPreativation(skill, manifest));
    $("t-options").appendChild(button);
  }
  $("btn-cancel").disabled = false;
  $("btn-submit").disabled = true;
}

/** 同一时机的**激活面**（票 02）：一张面选一条发动。
 *
 * 每一行 = 一条 `ActivateSkill` 候选（**不是**右端动作），文案 = 技能名 + 描述
 * （`view.skills[*]` 的展示事实；前端不抄中文名、不编描述）。末条「都不发动」只在
 * `legal_actions` 里**真有 `Pass`** 时铺——判据是候选，**不认技能 id**。
 *
 * 选中一条之后按**这条候选自己的**清单分流（`preactivationManifest` 读
 * `activation_preactivations`）：有清单 ⇒ 进入它的预发动（清单 = 这次发动的成本 / 目标，
 * 在牌桌表面逐步选）；无清单 ⇒ 这条裸 `ActivateSkill` 本身就是答案，**一击即提交**
 * （与选择型弹窗的"点一个选项即提交"同一交互；服务端随后按技能自己的流程继续问）。
 *
 * 两个出口都在面内（行点击 / 末条「都不发动」），故右端的「提交 / 放弃」在这张面上
 * 不承担出口（`refreshSubmit` 把提交键留灰；这张面走 `choice` 外壳，右端放弃按既有
 * `dialogDisablesPass` 的口径不铺——「都不发动」不是右端那个通用「放弃」）。
 */
function renderActivationFace(pending, actions) {
  const skills = (state.view && state.view.skills) || [];
  const hint = $("t-target-hint");
  if (hint) hint.textContent = "选一条技能发动；都不发动就点末条。";
  actions
    .filter((action) => action.kind === "ActivateSkill")
    .forEach((action) => {
      const skill = skills.find((item) => item && item.skill_id === action.skill_id) || null;
      const name = (skill && skill.name) || skillActionName(action) || action.skill_id;
      const manifest = preactivationManifest(pending, action.skill_id);
      const row = document.createElement("button");
      row.type = "button";
      row.className = "hero-card-row";
      row.setAttribute("data-skill-id", String(action.skill_id));
      const label = document.createElement("span");
      label.className = "hero-card-row-name";
      label.textContent = name;
      row.appendChild(label);
      // 描述是 `descriptions.json` 的原文（服务端下发，界面不裁剪）——缺省时只画技能名。
      const description = skill && skill.description;
      if (description) {
        const body = document.createElement("div");
        body.className = "hero-card-desc";
        body.textContent = description;
        row.appendChild(body);
      }
      row.addEventListener("click", async () => {
        if (state.playback || state.submitBusy) return;
        if (manifest) {
          enterPreativation(
            skill || { skill_id: action.skill_id, name, preactivated: false },
            manifest,
          );
          return;
        }
        await submitDialogChoice(pending, actions.indexOf(action));
      });
      $("t-options").appendChild(row);
    });
  // 末条「都不发动」：候选里真有 `Pass` 才铺（「可以都不发动」由引擎的 `optional` 聚合
  // 决定，界面只读候选集合）。它与其它「放弃」是同一个动作：送出那条 `Pass`。
  if (actions.some((action) => action.kind === RIGHT_ACTION_KINDS.pass)) {
    const giveUp = document.createElement("button");
    giveUp.type = "button";
    giveUp.className = "opt";
    giveUp.textContent = "都不发动";
    giveUp.addEventListener("click", async () => {
      if (state.playback || state.submitBusy) return;
      await submitRightAction(RIGHT_ACTION_KINDS.pass);
    });
    $("t-options").appendChild(giveUp);
  }
  // 面里的行就是出口：底部两键不承担这一步（提交键由 `refreshSubmit` 统一留灰）。
  $("btn-cancel").disabled = true;
}

/** `SELECT_OPTION` 的"选座位"形态（【遗计】"交给谁" / 【离间】选使用者与目标）：只点场上武将。 */
function renderSeatPick(pending) {
  const hint = $("t-target-hint");
  if (hint) {
    hint.textContent = cancelAction(pending)
      ? "点场上武将选择；可点「取消」放弃这一步。"
      : "点场上武将，选择把这张牌交给谁。";
  }
  $("btn-cancel").disabled = state.selectedOption === null && !cancelAction(pending);
}

/** 响应窗口里"非使用牌"候选的按钮文案（W5-02b）：借刀的 `Pass` = b（**交出武器**）。
 *
 * 只看窗口成因这一条公开事实，不改 `actionLabel` 的通用语义（同一个 `Pass` 在别的窗口
 * 仍是"放弃"）。
 */
function alternativeLabel(pending, action) {
  if (action.kind === "Pass" && pending && pending.cause === "BORROWED_SWORD") {
    return "交出武器";
  }
  return actionLabel(action);
}

function renderPickerAlternatives(pending) {
  const box = $("t-options");
  (pending.legal_actions || []).forEach((action, index) => {
    if (action.card || action.kind === "ActivateSkill" || action.kind === "SelectCards") return;
    // `Pass` / `EndPhase` 是右端动作（提交 / 取消 / 结束出牌 / 放弃），不再占浮动选项行。
    if (isRightAction(action)) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "opt";
    if (state.selectedOption === index) button.classList.add("sel");
    button.textContent = alternativeLabel(pending, action);
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

/** 唯一且不需要选目标的候选：不铺按钮，直接说"将提交：…"（spec D9）。
 *  技能候选写中文技能名（`view.skills`），不再拼「发动技能 hero.xxx」的报文 id。 */
function renderDirect(only, pending) {
  const label = only && only.kind === "ActivateSkill"
    ? skillActionLabel(only)
    : (only ? alternativeLabel(pending, only) : "");
  $("t-choice").textContent = only ? `将提交：${label}` : "";
}

/** 提交按钮的唯一判据：**能造出一个候选里真有的 Action** 时才可点。
 *
 * 空选提交会被引擎拒（400），所以界面必须自己拦住——但**不能替引擎裁决**（`04` §4.1）：
 * `buildAction()` 只做"查找"，最后一道逐字段核对在 :func:`submitAction` 里。
 */
function refreshSubmit(pending) {
  if (state.playback) {
    // 严格顺序：这批事件播完之前不接任何提交。
    $("btn-submit").disabled = true;
    $("btn-cancel").disabled = true;
    $("t-choice").textContent = "正在播放事件…";
    if (state.lastRejectReason) $("t-choice").textContent = state.lastRejectReason;
    return;
  }
  // 弹窗模板（票 08）：内容按钮**一击即提交**，右端没有"下一步"要按；这里只把
  // 这一步的读法说清楚。提交按钮保持灰（选择由点击内容完成，防两个提交点各算一次）。
  // 放在 `blocked` 判定**之前**：未知区域会把第一条 `SelectCards` 预选进
  // `state.selectedOption`，否则这里会显示旧的"还要选 1 张牌"而不是弹窗读法。
  //
  // **遗计式多选亮牌是例外**（U2-12）：牌面只切换选中态，右端「提交」是**唯一**提交点，
  // 故这一支按既有 `selectionError` / `buildAction` 算可点态（判据仍是"能造出候选里真有
  // 的那条 Action"），不把提交键恒置灰。
  const dialogKind = stepDialogKind(pending);
  // 同一时机的激活面（票 02）：出口在**面内**（每条技能行一击即提交 / 末条「都不发动」），
  // 底部「提交」不承担这一步（两个提交点会让"选一条"变成"选一条再按一次"）。
  // 判据与 `stepDialogKind` 共用 `activationFaceSurface`：点开某条候选的清单之后这一步的
  // 表面是**牌桌**，提交键要按清单的 `ready` 算（复核修复 F2：只判 N ≥ 2 会让清单永远
  // 提不出去——提交键恒灰、提示条被改写成面内那句）。
  if (activationFaceSurface(pending)) {
    $("btn-submit").disabled = true;
    $("t-choice").textContent = state.lastRejectReason
      || "点一条技能发动；不发动就点「都不发动」。";
    return;
  }
  // 票 01：按花色取牌（涉猎）——「提交」恒可点（0 张也合法）；「放弃」在右端，与提交同义，
  // 由 `answer.js:submitRightAction` 特判成"提交当前选择"。
  if (dialogKind === DIALOG_KINDS.suit) {
    const ready = buildAction() !== null;
    $("btn-submit").disabled = !ready;
    $("t-choice").textContent = state.lastRejectReason
      || `点选每种花色至多一张（已选 ${state.picks.length} 张）；`
      + "点「提交」或右端「放弃」都按当前选择结算。";
    return;
  }
  // 票 93：两行观看手牌——「提交」只在**四色各一**（picks 凑满 `spec.total` 下限）时可点；
  // 同花色在界面上已被置灰消掉，故不需要"选了 4 张但花色重复 ⇒ 拒收"这一档（票面 §2.2）。
  // 空选择面（凑不出四色）只有右端「放弃」一个出口，明话说清（否则玩家会一直点牌）。
  if (dialogKind === DIALOG_KINDS.watchTwoRows) {
    const required = watchRequiredCount(pending);
    const duplicate = watchDuplicateCardId(pending);
    const empty = !((pending.slots || []).length);
    const ready = !duplicate && !empty && state.picks.length >= required
      && buildAction() !== null;
    $("btn-submit").disabled = !ready;
    if (state.lastRejectReason) {
      $("t-choice").textContent = state.lastRejectReason;
    } else if (duplicate) {
      $("t-choice").textContent = WATCH_DUPLICATE_MESSAGE;
    } else if (empty) {
      $("t-choice").textContent = WATCH_EMPTY_MESSAGE;
    } else {
      $("t-choice").textContent = ready
        ? "可以提交。"
        : `必须四种花色各一张才能提交（已选 ${state.picks.length}/${required} 张）。`;
    }
    return;
  }
  // A1：洛神亮牌确认——弹窗里没有可选卡，出口是「提交 / 放弃」；按唯一选项的正常提交态算。
  if (isJudgmentRevealOption(pending)) {
    const selected = state.selectedOption === null
      ? null
      : (pending.legal_actions || [])[state.selectedOption];
    const blocked = selected === null ? null : selectionError(selected, pending);
    const ready = blocked === null && buildAction() !== null;
    $("btn-submit").disabled = !ready;
    $("t-choice").textContent = state.lastRejectReason
      || (ready ? "可以提交。" : "点右端「提交」继续，或「放弃」停止。");
    return;
  }
  if (dialogKind === DIALOG_KINDS.reveal && isPickRevealStep(pending)) {
    const selected = state.selectedOption === null
      ? null
      : (pending.legal_actions || [])[state.selectedOption];
    const blocked = selected === null ? null : selectionError(selected, pending);
    const ready = blocked === null && buildAction() !== null;
    $("btn-submit").disabled = !ready;
    if (state.lastRejectReason) {
      $("t-choice").textContent = state.lastRejectReason;
    } else if (blocked) {
      const total = (pending.spec && pending.spec.total) || [1, 1];
      const least = Math.max(1, Number(total[0]) || 1);
      const most = Math.max(least, Number(total[1]) || least);
      const howMany = least === most ? `${least} 张` : `${least}~${most} 张`;
      $("t-choice").textContent = `选 ${howMany}后点右端「提交」（已选 ${state.picks.length} 张）。`;
    } else {
      $("t-choice").textContent = "可以提交。";
    }
    return;
  }
  // 票 01：可选亮牌（攻心）——点一张即提交（`submitRevealPick`），「提交」键不是出口；
  // 没有可点的红桃时唯一出口是右端「放弃」。
  if (dialogKind === DIALOG_KINDS.reveal && hasPassAction(pending)) {
    $("btn-submit").disabled = true;
    $("t-choice").textContent = state.lastRejectReason
      || ((pending.slots || []).length
        ? "点一张红桃牌即展示；也可以点右端「放弃」。"
        : "没有可展示的红桃牌，点右端「放弃」结束。");
    return;
  }
  // 只从自己手牌 / 装备区选一张（火攻 / 铁骑等）：点牌即提交；出口是内容点击或右端「放弃」。
  if (isOwnHandSinglePick(pending)) {
    $("btn-submit").disabled = true;
    const where = ownPickHasEquip(pending) ? "手牌或装备区里的牌" : "手牌";
    $("t-choice").textContent = state.lastRejectReason
      || `点一张亮起的${where}即提交；也可以点右端「放弃」。`;
    return;
  }
  if (dialogDisablesPass(dialogKind, pending)) {
    $("btn-submit").disabled = true;
    $("t-choice").textContent = state.lastRejectReason
      || (dialogKind === DIALOG_KINDS.choice
        ? "点一个选项即提交（这一步不能放弃）。"
        : (dialogKind === DIALOG_KINDS.unknown
          ? "点一张牌即提交（这一步不能放弃）。"
          : "点一张亮牌即提交（这一步不能放弃）。"));
    return;
  }
  const selected = state.selectedOption === null ? null : (pending.legal_actions || [])[state.selectedOption];
  const blocked = selected === null ? null : selectionError(selected, pending);
  const ready = blocked === null && buildAction() !== null;
  $("btn-submit").disabled = !ready;
  if (isOwnZonePick(pending)) {
    const where = ownPickHasEquip(pending) ? "手牌或装备区里的牌" : "手牌";
    $("t-choice").textContent = state.lastRejectReason
      || blocked
      || (ready ? "可以提交。" : `点选${where}（已选 ${state.picks.length} 张）。`);
    return;
  }
  if (state.lastRejectReason) { $("t-choice").textContent = state.lastRejectReason; return; }
  if (blocked) { $("t-choice").textContent = blocked; return; }
  if (state.preactivation) {
    const pa = state.preactivation;
    // 复合动作：填完就交（服务端校验器裁决）；转化技的本地入口：填完还得**查得到候选**
    // （查不到 = 这组来源 / 目标此刻不合法，按钮不给点）。
    //
    // 票 10：**来源是不是合法组合**另判（`Step.groups`）——清单填完不等于这组来源能一起
    // 转化。缺省 `groups` 的步恒真；有 `groups` 时，陈旧 / 合成载荷也过不了这道闸，
    // 「提交」不会亮、更不会发出一条注定 400 的 `ActivateSkill`。
    const legal = preactivationSourcesLegal(pa);
    const ready = preactivationReady(pa);
    $("btn-submit").disabled = !legal || !ready || buildAction() === null;
    if (!legal) {
      // 通用文案（不认牌名 / 花色）：说清这一组不能一起转化，出口 = 取消再重选。
      $("t-choice").textContent = PREACTIVATION_GROUP_HINT;
    } else if (!ready) {
      // 未填完 = 说**同一句**步骤提示（标题 `#t-target-hint` 读的也是它）：光标到末尾但
      // 还有空步时它会指回第一个空步，不再出现标题说「清单已填完」、页脚说「还有步骤
      // 没填完」的矛盾（票 08）。
      $("t-choice").textContent = preactivationStepHint(pa, pending);
    } else {
      $("t-choice").textContent = pa.cardEntry
        ? "可以确认使用（提交一次带附带指定 [持刀者, Bn] 的使用）。"
        : (pa.composite
          ? "可以确认发动（提交一个复合动作）。"
          : "可以确认发动（服务端会把它解析成这次的使用 / 响应）。");
    }
    return;
  }
  if (isPassivePreativation(pending) && state.preactivation === null) {
    $("t-choice").textContent = ready ? "可以提交：放弃。" : "点「进入预发动」，或点「放弃」。";
    return;
  }
  if (pending.kind === "ARRANGE") {
    // 规格缺失 = 409 裸 Query / 无技能定义（投影 fail-closed）：不猜顺序，只说刷新。
    // 票 07：出口按**实际候选**说话（文案唯一一份住 `arrange.js:arrangeExitHint`）。
    const exit = arrangeExitHint(pending);
    $("t-choice").textContent = !arrangeSpec(pending)
      ? "排列不可用，请刷新"
      : (ready
        ? `可以确认：提交你摆放的顺序（上行左→右，再接下行左→右）。${exit}`
        : `先把全部卡牌排成一个完整顺序。${exit}`);
    return;
  }
  // 弹窗模板（票 08）的分支在函数开头（`blocked` 之前）已经返回：这里不会再走到
  // choice / unknown。
  if (pending.kind === "SELECT_OPTION" && seatChoices(pending).length > 0) {
    $("t-choice").textContent = ready ? "可以提交。" : "点场上武将选择交给谁。";
    return;
  }
  // 技能直提态：把下一条要提交的技能候选写成中文技能名（不重铺候选行）。
  const skillAction = selectedSkillAction(pending, pending.legal_actions || []);
  if (skillAction) {
    const built = ready ? buildAction() : null;
    $("t-choice").textContent = built && built.kind === "ActivateSkill"
      ? `将提交：${skillActionLabel(skillAction)}`
      : "";
    return;
  }
  // `SELECT_OPTION` 的非座位形态：小选项按钮组。
  if (pending.kind === "SELECT_OPTION") {
    $("t-choice").textContent = ready ? "可以提交。" : "点一个选项。";
    return;
  }
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
    renderDirect((pending.legal_actions || [])[0], pending);
    return;
  }
  $("t-choice").textContent = ready ? "" : "先选一项，或点一张可用的手牌再提交。";
}
