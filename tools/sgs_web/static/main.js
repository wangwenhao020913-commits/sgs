/* SGS 前端 · 装配：回放步长按钮、事件绑定与启动（必须是最后一份脚本）。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------------ 装配 */

/** 牌桌按钮：在 0.05–0.5 秒之间切一档回放步长（缺省 0.1，不记住）。 */
function cycleDelay() {
  const at = DELAY_STEPS_MS.indexOf(state.eventDelayMs);
  state.eventDelayMs = DELAY_STEPS_MS[(at + 1) % DELAY_STEPS_MS.length];
  updateDelayButton();
}

function updateDelayButton() {
  // 0.05s 是亚 0.1 秒挡：一位小数会被 toFixed(1) 抹成 "0.1"，与 0.1 挡撞字，故两位。
  const seconds = state.eventDelayMs / 1000;
  const text = `步长 ${seconds < 0.1 ? seconds.toFixed(2) : seconds.toFixed(1)}s`;
  // 牌桌与回放页共用同一份步长状态、同一组六档（W4-6）。
  for (const id of ["btn-delay", "btn-r-delay"]) {
    const button = $(id);
    if (button) button.textContent = text;
  }
}

/** 浮层开关（设置 / 事件流）：按钮 ↔ 面板的 hidden 与 aria-expanded 只在这一处同步。
 *
 * `onOpen` 是"展开之后"的钩子（事件流要用它滚到底），收起时不调用。
 */
function wireOverlayToggle(button, panel, onOpen) {
  if (!button || !panel) return;
  button.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    button.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden && onOpen) onOpen();
  });
}

function wire() {
  seedOptions();
  $("in-players").addEventListener("change", seedOptions);
  $("in-pointjiang").addEventListener("change", syncPointjiang);
  syncPointjiang();
  loadHeroCatalog();
  $("lobby-form").addEventListener("submit", startGame);
  $("nav-lobby").addEventListener("click", () => { show("lobby"); loadRuns(); });
  $("nav-table").addEventListener("click", () => show("table"));
  // 顶部导航与牌桌顶栏的「回放」入口是同一条路：切页 + 刷新录像列表 + 画回放页。
  const openReplayPage = () => {
    show("replay");
    loadRuns();
    renderReplay();
  };
  $("nav-replay").addEventListener("click", openReplayPage);
  const modeSelect = $("t-log-mode");
  if (modeSelect) modeSelect.addEventListener("change", () => {
    renderLog();
  });
  const replayModeSelect = $("r-log-mode");
  if (replayModeSelect) replayModeSelect.addEventListener("change", () => {
    if (state.replay) renderReplayLog(state.replay);
  });
  $("btn-submit").addEventListener("click", submitAction);
  $("btn-cancel").addEventListener("click", cancelOrClear);
  // U1-03：提示条右端的两个"到此为止"动作（结束出牌 / 放弃），词表由服务端候选决定。
  $("btn-end-phase").addEventListener("click", () => submitRightAction(RIGHT_ACTION_KINDS.endPhase));
  $("btn-pass").addEventListener("click", () => submitRightAction(RIGHT_ACTION_KINDS.pass));
  wireModal();
  $("btn-refresh").addEventListener("click", refresh);
  $("btn-takeover").addEventListener("click", takeover);
  $("btn-delay").addEventListener("click", cycleDelay);
  // U1-01 壳：设置浮层 / 事件流浮层 / 回放入口（顶栏右侧）。
  wireOverlayToggle($("btn-settings"), $("t-settings"));
  wireOverlayToggle($("t-log-toggle"), $("t-log-panel"), () => {
    scrollLogToBottom();
  });
  const replayEntry = $("btn-go-replay");
  if (replayEntry) replayEntry.addEventListener("click", openReplayPage);
  $("btn-r-delay").addEventListener("click", cycleDelay);
  updateDelayButton();
  $("btn-r-first").addEventListener("click", () => replaySeek(0));
  $("btn-r-prev").addEventListener("click", () => replayStep(-1));
  $("btn-r-next").addEventListener("click", () => replayStep(1));
  $("btn-r-last").addEventListener("click", () => {
    if (state.replay) replaySeek(state.replay.records.length - 1);
  });
  $("btn-r-play").addEventListener("click", toggleReplayPlay);
  loadRuns();
  // 倒计时每秒刷新（只在牌桌页可见时）；不改任何服务端状态。
  // 回放期间不刷：那时 `state.view` 还是上一条询问，倒计时会指向已经过去的那个窗口。
  window.setInterval(() => {
    if (!$("view-table").hidden && !state.playback) renderCountdown(state.view);
  }, 1000);
}

wire();
