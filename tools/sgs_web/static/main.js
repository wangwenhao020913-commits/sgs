/* SGS 前端 · 装配：回放步长按钮、事件绑定与启动（必须是最后一份脚本）。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------------------ 装配 */

/** 玩家点过步长按钮之后，`anim.ini` 的 `base_step` 不再覆盖当前档（U4-02 / D8）。
 *
 * 只住内存：刷新页面会重新读 ini 并重建这个 flag（与"不落 localStorage"同一口径）。
 */
let delayPinnedByPlayer = false;

/** 牌桌按钮：在 0.03–0.3 秒之间切一档回放步长（缺省 0.1，不记住）。 */
function cycleDelay() {
  delayPinnedByPlayer = true;
  const at = DELAY_STEPS_MS.indexOf(state.eventDelayMs);
  state.eventDelayMs = DELAY_STEPS_MS[(at + 1) % DELAY_STEPS_MS.length];
  updateDelayButton();
}

/** `anim.ini` 的 `base_step` → **初始**档位（U4-02 / D8）。
 *
 * 只在玩家还没点过按钮时生效；值必须落在 `DELAY_STEPS_MS` 里——缺键 / 手改成别的数字
 * 一律忽略并保留缺省 100（`animConfig` 缺键时回的就是 100）。返回是否真的改了档位。
 * 飞行层只负责"表读完了"（`animOnConfig`），播放节奏仍住这几个文件。
 */
function applyIniBaseStep(config) {
  if (delayPinnedByPlayer) return false;
  const value = Number(config && config.base_step);
  if (!DELAY_STEPS_MS.includes(value)) return false;
  state.eventDelayMs = value;
  updateDelayButton();
  return true;
}

function updateDelayButton() {
  // 0.03 / 0.05 是亚 0.1 秒挡：一位小数会被 toFixed(1) 抹成 "0.1"，与 0.1 挡撞字，故两位。
  const seconds = state.eventDelayMs / 1000;
  const text = `步长 ${seconds < 0.1 ? seconds.toFixed(2) : seconds.toFixed(1)}s`;
  // 牌桌与回放页共用同一份步长状态、同一组五档（W4-6）。
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
  $("in-players").addEventListener("change", () => {
    seedOptions();
    syncPoolHint();  // 座位数变了，真名池够不够要重算
  });
  $("in-pointjiang").addEventListener("change", syncPointjiang);
  $("in-my-hero").addEventListener("change", syncPoolHint);
  syncPointjiang();
  loadHeroCatalog();
  loadHeroPools();
  loadDeckCatalog();
  $("lobby-form").addEventListener("submit", startGame);
  $("btn-pool-save").addEventListener("click", saveHeroPool);
  // U2-01（D7）：三页顶栏共用同一组导航（顺序 / 高亮 / 禁用都归 core.js:show()）；
  // 这里只按 NAV_BUTTONS 逐个挂同一份处理器。回放入口就是导航三键之一，旧图标按钮已删。
  const openReplayPage = () => {
    show("replay");
    loadRuns();
    renderReplay();
  };
  const wireNav = (view, handler) => {
    for (const id of NAV_BUTTONS[view]) {
      const button = $(id);
      if (button) button.addEventListener("click", handler);
    }
  };
  wireNav("lobby", () => { show("lobby"); loadRuns(); });
  wireNav("table", () => show("table"));
  wireNav("replay", openReplayPage);
  const modeSelect = $("t-log-mode");
  if (modeSelect) modeSelect.addEventListener("change", () => {
    renderLog();
  });
  const replayModeSelect = $("r-log-mode");
  if (replayModeSelect) replayModeSelect.addEventListener("change", () => {
    if (state.replay) renderReplayLog(state.replay);
  });
  // U2-03 的回归修复（U2-05 一并收）：`submitAction(restoreSelection = null)` 的形参是
  // **选择快照**，不是事件。直接挂 `submitAction` 会让真实点击把 `MouseEvent` 当快照传进去，
  // 400 回滚时 `restoreSelectionState(event)` 读 `event.selectedTargets` 抛异常、四键停在
  // 冻结态。改成无参箭头：点击不再冒充快照。
  $("btn-submit").addEventListener("click", () => submitAction());
  $("btn-cancel").addEventListener("click", cancelOrClear);
  // U1-03：提示条右端的两个"到此为止"动作（结束出牌 / 放弃），词表由服务端候选决定。
  $("btn-end-phase").addEventListener("click", () => submitRightAction(RIGHT_ACTION_KINDS.endPhase));
  $("btn-pass").addEventListener("click", () => submitRightAction(RIGHT_ACTION_KINDS.pass));
  wireModal();
  // U1-06：牌桌右键——外壳屏蔽浏览器菜单，武将块开详情（装备 / 判定 / 手牌只悬停）。
  wireHeroDetail();
  $("btn-refresh").addEventListener("click", refresh);
  $("btn-takeover").addEventListener("click", takeover);
  $("btn-delay").addEventListener("click", cycleDelay);
  // U1-01 壳：设置浮层 / 事件流浮层（顶栏右侧）；回放入口是导航三键之一（见上）。
  wireOverlayToggle($("btn-settings"), $("t-settings"));
  wireOverlayToggle($("t-log-toggle"), $("t-log-panel"), () => {
    scrollLogToBottom();
  });
  $("btn-r-delay").addEventListener("click", cycleDelay);
  updateDelayButton();
  // U4-02（D7 / D8）：`anim.ini` 的 `base_step` 只决定**加载时**的初始档。订阅飞行层的
  // "表读完了"口；玩家点过按钮之后，ini 的覆盖由 `applyIniBaseStep` 里的 flag 挡住。
  if (typeof animOnConfig === "function") animOnConfig(applyIniBaseStep);
  $("btn-r-first").addEventListener("click", () => replaySeek(0));
  $("btn-r-prev").addEventListener("click", () => replayStep(-1));
  $("btn-r-next").addEventListener("click", () => replayStep(1));
  $("btn-r-last").addEventListener("click", () => {
    if (state.replay) replaySeek(state.replay.records.length - 1);
  });
  $("btn-r-play").addEventListener("click", toggleReplayPlay);
  // 首屏「牌桌」禁用态与 state 同步（无会话 = 禁用）；首屏高亮已写在 index.html 里。
  syncNavState();
  loadRuns();
  // 倒计时每秒刷新（只在牌桌页可见时）；不改任何服务端状态。
  // 回放期间不刷：那时 `state.view` 还是上一条询问，倒计时会指向已经过去的那个窗口。
  window.setInterval(() => {
    if (!$("view-table").hidden && !state.playback) renderCountdown(state.view);
  }, 1000);
  // U3-05 / D12：静默轮询——每 2 秒取一次增量。播放中 / 提交在飞时 `poll` 自己让路
  // （下一次 tick 自然恢复）；弹窗打开时照常轮询（服务端的超时代答只在这一步判定）。
  window.setInterval(poll, POLL_INTERVAL_MS);
}

wire();
