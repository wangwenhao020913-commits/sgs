/* SGS 前端 · 共享模态（U1-03）：多步技能清单 / 排列 / 武将详情共用一套规范。
 *
 * 纪律（票面 `03` / spec §3.2–3.3）：
 *  - 屏幕正中；正文可滚轮翻阅；背景或 ESC 可关；
 *  - 打开时**顶栏 / 事件流浮层**整体 `inert`，空白舞台被遮罩挡住（点击 = 关闭）；
 *    座位与底部面板留在遮罩之上——多步技能要选目标 / 选来源牌。**倒计时不暂停**：
 *    进度条仍在提示条上走，模态正文里**不**放倒计时；
 *  - 模态只负责“摆哪里 / 怎么关”，内容仍由各自的渲染器画（预发动清单 / 排列面 /
 *    未来的武将详情）。当前实现把提示区的 `#t-options` 节点整体搬进模态正文，
 *    于是“简单询问走浮动选项行、多步面走模态”是两个**位置**，不是两套渲染。
 *
 * **底部面板不 inert**：多步技能清单的第 1 步就是“点手牌选来源牌”，排列/技能的确认与
 * 取消也按 spec §3.3 留在提示条右端。故模态挡的是背景窗口，不是当前这一问的操作条。
 *
 * 关闭语义分两类：
 *  - 预发动：关闭 = 取消本地态，**零 action 提交**（与「取消预发动」同一条路）；
 *  - 排列：强制选择、没有取消。关闭只收起窗口，右端出现「查看排列」可再打开，
 *    `state.arrange` 与已摆的顺序保留。
 */
"use strict";

/** 打开模态时要对背景设置的 `inert` 区域（顶栏 / 事件流浮层）。
 *
 * **舞台不 inert**：多步技能清单的座位步要点场上武将选目标；遮罩压在座位之下（CSS），
 * 空白背景与顶栏仍被挡住。**底部面板也不 inert**：模态里的多步技能清单第 1 步就是
 * “点手牌选来源牌”，排列/技能的确认与取消也按 spec §3.3 留在提示条右端。
 */
const MODAL_BACKGROUND_IDS = ["table-topbar", "t-log-panel"];

/** 当前询问 / 本地态是否需要模态：预发动清单 > ARRANGE。`null` = 不需要。 */
function modalWantedKind(pending) {
  if (state.preactivation) return "preactivation";
  if (pending && pending.kind === "ARRANGE") return "arrange";
  return null;
}

function modalTitleFor(kind) {
  if (kind === "preactivation") {
    const pa = state.preactivation;
    return pa ? `预发动【${pa.name}】` : "预发动";
  }
  return "排列";
}

/** 遮罩 / 背景态的唯一开关：`inert` 挡操作，遮罩挡空白背景，座位留在遮罩之上。 */
function setModalBackgroundInert(on) {
  for (const id of MODAL_BACKGROUND_IDS) {
    const node = $(id);
    if (!node) continue;
    if (on) {
      node.setAttribute("inert", "");
      node.setAttribute("aria-hidden", "true");
    } else {
      node.removeAttribute("inert");
      node.removeAttribute("aria-hidden");
    }
  }
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
  setModalBackgroundInert(false);
  state.modal.open = false;
}

/** 强制选择被收起后，右端出现重开入口（预发动不用它：关闭即取消）。 */
function showReopenButton(kind, visible) {
  const reopen = $("btn-show-modal");
  if (!reopen) return;
  reopen.hidden = !visible;
  if (visible) reopen.textContent = kind === "arrange" ? "查看排列" : "继续";
}

/** 打开 / 刷新模态：标题 + 正文节点 + 背景 inert 都只在这里写一次。 */
function openModalDom(kind) {
  const modal = $("t-modal");
  const body = $("t-modal-body");
  const options = $("t-options");
  const title = $("t-modal-title");
  if (!modal || !body || !options) return;
  if (title) title.textContent = modalTitleFor(kind);
  body.appendChild(options);
  modal.hidden = false;
  setModalBackgroundInert(true);
  state.modal.kind = kind;
  state.modal.open = true;
  state.modal.dismissed = false;
  showReopenButton(kind, false);
}

function closeModalDom({ restoreContent = true } = {}) {
  hideModalDom();
  if (restoreContent) showOptionsHome();
}

/** 在每次 `renderPending` 末尾调用：把内容送到正确的位置（浮动选项行 / 模态正文）。 */
function syncModal() {
  const pending = state.view ? state.view.pending : null;
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
    state.modal.kind = kind;
    state.modal.dismissed = false;
    state.modal.open = false;
  }
  if (state.modal.dismissed) {
    hideModalDom();
    showReopenButton(kind, true);
    return;
  }
  openModalDom(kind);
}

/** 关闭模态：预发动 = 取消本地态（零提交）；排列 = 收起并给出「查看排列」入口。 */
function dismissModal() {
  const kind = state.modal.kind;
  if (!kind || !state.modal.open) return;
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
  if (!state.modal.kind && !state.modal.open) return;
  state.modal.kind = null;
  state.modal.dismissed = false;
  closeModalDom();
  showReopenButton(null, false);
}

function wireModal() {
  const backdrop = $("t-modal-backdrop");
  if (backdrop) backdrop.addEventListener("click", dismissModal);
  const close = $("t-modal-close");
  if (close) close.addEventListener("click", dismissModal);
  const reopen = $("btn-show-modal");
  if (reopen) reopen.addEventListener("click", reopenModal);
  document.addEventListener("keydown", handleModalKey);
}
