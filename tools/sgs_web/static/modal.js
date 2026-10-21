/* SGS 前端 · 共享模态（U1-03）：多步技能清单 / 排列 / 武将详情共用一套规范。
 *
 * 纪律（票面 `03` / spec D8）：
 *  - 屏幕正中；正文可滚轮翻阅；`#t-modal-close` 与 ESC 可关；
 *  - **没有"阻断弹窗"**：打开模态**不**给顶栏 / 舞台 / 底部加 `inert`，也不设
 *    `aria-hidden`。遮罩是 `pointer-events: none` 的暗化层——点击直接穿透到牌桌，
 *    能不能选由 :func:`selectionEnabled` 那**一条**判据拦下。**倒计时不暂停**：
 *    进度条仍在提示条上走，模态正文里**不**放倒计时；
 *  - 模态只负责“摆哪里 / 怎么关”，内容仍由各自的渲染器画（预发动清单 / 排列面 /
 *    武将详情）。当前实现把提示区的 `#t-options` 节点整体搬进模态正文，
 *    于是“简单询问走浮动选项行、多步面走模态”是两个**位置**，不是两套渲染。
 *
 * 关闭语义分两类：
 *  - 预发动：关闭 = 取消本地态，**零 action 提交**（与「取消预发动」同一条路）；
 *  - 排列：强制选择、没有取消。关闭只收起窗口，右端出现「查看排列」可再打开，
 *    `state.arrange` 与已摆的顺序保留。
 */
"use strict";

/** 当前**步骤**的表面若是弹窗就返回它的种类（spec D8「只读态」的唯一判据源）。
 *
 * 现在只有排列（`ARRANGE`）是弹窗表面。另外两类模态**不**进这里：
 *  - 预发动清单是**牌桌表面**（D2 的 C 形：点自己的牌 / 点自己的座位），打开时牌桌
 *    照常接选择——它只是把选项搬进了窗口，不是"这一步的答案在窗内"；
 *  - 武将详情是叠加的**检视窗**：开合不改变只读态，所以这里读的是**底下那一步**的
 *    表面，而不是"哪个模态正开着"。
 * 票 06 把四类询问模板接进来时，只改这个函数（`selectionEnabled` 不用动）。
 */
function stepDialogKind(pending) {
  if (pending && pending.kind === "ARRANGE") return "arrange";
  return null;
}

/** 只读态判据（spec D8）：当前步骤的表面是弹窗 ⇒ 牌桌选择入口一律 no-op。
 *
 * 与既有的 `state.playback` 判据同型，但只回答"现在能不能选"：检视（悬停 / 右键详情 /
 * 折叠 / 顶栏 / 事件流）与操作（提示条右端的四键）都不走这里。所有选择入口
 * （点手牌 / 点座位 / 点槽位 / 提供 B / 弃牌 / 预发动选牌选座）都先问它一次；
 * 键盘回车 / 空格触发的 click 走同一批函数，故同一判据。
 * 收起（`modal.dismissed`）只改遮挡，不改只读态——判据只看这一步的表面。
 */
function selectionEnabled(view = state.view) {
  if (state.playback) return false;
  return stepDialogKind(view ? view.pending : null) === null;
}

/** 当前询问 / 本地态是否需要模态：武将详情 > 预发动清单 > ARRANGE。`null` = 不需要。 */
function modalWantedKind(pending) {
  if (state.modal.heroSeat !== null) return "hero";
  if (state.preactivation) return "preactivation";
  if (pending && pending.kind === "ARRANGE") return "arrange";
  return null;
}

function modalTitleFor(kind) {
  if (kind === "hero") {
    const player = playerAt(state.view, state.modal.heroSeat);
    return player ? `武将详情 · ${heroNameOf(player, state.view)}` : "武将详情";
  }
  if (kind === "preactivation") {
    const pa = state.preactivation;
    return pa ? `预发动【${pa.name}】` : "预发动";
  }
  return "排列";
}

/** 遮罩 / 页面态的唯一开关：暗化层只负责"看得见"，**不吞指针**（D8）。
 *
 * 点击落在遮罩上会穿透到牌桌（`.modal-backdrop { pointer-events: none }`），再由
 * :func:`selectionEnabled` 判据拦下；模态窗口自己保留 `pointer-events: auto`。
 * 遮罩因此不再有 click 监听、也不再是"点背景 = 关闭"的出口。
 */
function setModalChrome(on) {
  const backdrop = $("t-modal-backdrop");
  if (backdrop) backdrop.hidden = !on;
  const shell = $("table-shell");
  if (shell) shell.classList.toggle("modal-open", on);
}

/** 把提示区的 `#t-options` 搬回浮动选项行（真 DOM 的 appendChild = 移动）。 */
function showOptionsHome() {
  const home = $("t-options-home");
  const options = $("t-options");
  if (home && options) home.appendChild(options);
}

function hideModalDom() {
  const modal = $("t-modal");
  if (modal) modal.hidden = true;
  setModalChrome(false);
  state.modal.open = false;
}

/** 强制选择被收起后，右端出现重开入口（预发动不用它：关闭即取消）。 */
function showReopenButton(kind, visible) {
  const reopen = $("btn-show-modal");
  if (!reopen) return;
  reopen.hidden = !visible;
  if (visible) reopen.textContent = kind === "arrange" ? "查看排列" : "继续";
}

/** 打开 / 刷新模态：标题 + 正文节点 + 暗化层都只在这里写一次。
 *
 * 两类正文：武将详情由 `hero-detail.js` 现画（不搬 `#t-options`）；预发动 / 排列仍把
 * 提示区的 `#t-options` 整体搬进正文（“简单询问走浮动选项行、多步面走模态”）。
 *
 * **回到询问弹窗时先清正文**：详情是叠加的检视窗，关掉它再打开底下那个弹窗时必须把
 * 正文里残留的详情内容清掉，否则「关闭详情后弹窗原样恢复」会变成详情 + 弹窗两段。
 * `#t-options` 是共享节点（可能正被清掉的正文包含），清完再 append 回来。
 */
function openModalDom(kind) {
  const modal = $("t-modal");
  const body = $("t-modal-body");
  const options = $("t-options");
  const title = $("t-modal-title");
  if (!modal || !body) return;
  if (title) title.textContent = modalTitleFor(kind);
  if (kind === "hero") {
    renderHeroDetailBody();
  } else {
    if (!options) return;
    // 正文里若有叠加详情留下的内容，清掉再放询问内容；只有 #t-options 时**不清**
    // （清空 = 每次轮询重绘都把窗口滚动位置拉回顶部）。`#t-options` 是共享节点，
    // 可能正被清掉的正文包含，清完再 append 回来。
    if (Array.from(body.children || []).some((child) => child !== options)) {
      body.innerHTML = "";
      body.scrollTop = 0;
    }
    body.appendChild(options);
  }
  modal.hidden = false;
  setModalChrome(true);
  state.modal.kind = kind;
  state.modal.open = true;
  // `dismissed` 属于**底下的**排列模态；打开详情时不能把它抹掉（关闭详情后仍只给
  // 「查看排列」入口）。预发动 / 排列本身重新打开时重置。
  if (kind !== "hero") state.modal.dismissed = false;
  showReopenButton(kind, false);
}

function closeModalDom({ restoreContent = true } = {}) {
  hideModalDom();
  if (restoreContent) showOptionsHome();
}

/** 在每次 `renderPending` 末尾调用：把内容送到正确的位置（浮动选项行 / 模态正文）。
 *
 * 优先级：武将详情（本地态，独立于询问）> 预发动 > 排列。详情关掉后再按当前询问
 * 回到它自己的模态；详情开着时它**不被** `renderPending` 掉。
 */
function syncModal() {
  const pending = state.view ? state.view.pending : null;
  // 回放期间不挂任何模态（详情也是本地态，播放开始前就收起）。
  if (state.playback) state.modal.heroSeat = null;
  const kind = state.playback ? null : modalWantedKind(pending);
  if (!kind) {
    state.modal.kind = null;
    state.modal.dismissed = false;
    // 幂等：内容可能仍在模态正文里（上一问的模态被 `resetSelection` 清掉本地态）。
    closeModalDom();
    showReopenButton(null, false);
    return;
  }
  if (state.modal.kind !== kind) {
    // 详情是**叠加**在询问模态之上的本地窗口：打开 / 关闭它都不该重置底下排列模态的
    // 「收起」状态（ticket 03：收起后只给「查看排列」）。
    const wasHero = state.modal.kind === "hero";
    if (kind !== "hero" && !wasHero) state.modal.dismissed = false;
    state.modal.kind = kind;
    state.modal.open = false;
  }
  // 排列收起后只给「查看排列」；武将详情是叠加的检视窗，没有"收起"这回事。
  if (state.modal.dismissed && kind !== "hero") {
    hideModalDom();
    showReopenButton(kind, true);
    return;
  }
  openModalDom(kind);
}

/** 关闭模态：武将详情 = 只关本地窗口（零提交）；预发动 = 取消本地态（零提交）；
 *  排列 = 收起并给出「查看排列」入口。 */
function dismissModal() {
  const kind = state.modal.kind;
  if (!kind || !state.modal.open) return;
  if (kind === "hero") {
    closeHeroDetail();
    return;
  }
  if (kind === "preactivation") {
    state.preactivation = null;
    state.convertSkill = null;
    state.picks = [];
    state.focusCardId = null;
    state.selectedTargets = [];
    state.lastRejectReason = "";
    state.modal.kind = null;
    state.modal.dismissed = false;
    closeModalDom();
    showReopenButton(null, false);
    renderTable();
    renderPending(state.view);
    return;
  }
  state.modal.dismissed = true;
  hideModalDom();
  showReopenButton(kind, true);
}

/** 「查看排列」：强制选择被收起后重新打开（`state.arrange` 原样保留）。 */
function reopenModal() {
  if (!state.modal.kind) return;
  openModalDom(state.modal.kind);
}

/** document 的 keydown 入口：ESC / Esc 关闭当前模态（并阻止浏览器默认的 Esc 行为）。 */
function handleModalKey(event) {
  if (!event || (event.key !== "Escape" && event.key !== "Esc")) return;
  if (!state.modal.open) return;
  if (event.preventDefault) event.preventDefault();
  dismissModal();
}

/** 离开牌桌页 / 回放期间：收起模态并恢复浮动选项行（不提交任何 action）。 */
function closeModalForView() {
  if (!state.modal.kind && !state.modal.open && state.modal.heroSeat === null) return;
  state.modal.kind = null;
  state.modal.dismissed = false;
  state.modal.heroSeat = null;
  closeModalDom();
  showReopenButton(null, false);
}

function wireModal() {
  // 遮罩**不接 click**：`pointer-events: none` 的暗化层没有自己的点击，穿透到牌桌的
  // 点击由 `selectionEnabled()` 判据拦下（D8：「点背景 = 关闭」不再存在）。
  const close = $("t-modal-close");
  if (close) close.addEventListener("click", dismissModal);
  const reopen = $("btn-show-modal");
  if (reopen) reopen.addEventListener("click", reopenModal);
  document.addEventListener("keydown", handleModalKey);
}
