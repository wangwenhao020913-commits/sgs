/* SGS 前端 · 全局面板窗（ui-one-screen 票 02 / spec A2 / A3）。
 *
 * 一套壳、三档**字面**尺寸、单槽位不叠：撞旧档三选一 / 退出确认 / 票 22 的三选一 / 遗物 /
 * 结算结果 / 票 25 的删除确认，都填这一个壳（`#script-confirm` 系列 id 一个都没改名）。
 *
 * 形状：定位器 `#script-confirm` 仍是 `position: fixed; inset: 0` 的 flex 居中层（它本来就住在
 * 所有 `<main>` 之外），真正的窗是它里面那扇 `.pw`。**别把定位器写成窗**：`inset: 0` 会把它
 * 撑成满屏，量出来的尺寸永远不是 480×300 dsu。
 *
 * 两条纪律来自原型实测（spec §3）：
 *  - **单槽位**：同一时刻只有一扇；再开一扇 = 覆盖内容并改 `data-size`，不是叠第二层。
 *  - **不新增 ESC 行为**：询问弹窗的 ESC 归 `modal.js`；两套壳争同一个键就会互相收窗。
 *
 * 第三条（票 10 / review 修复 C）：**关闭键只画在允许关的窗上**（`dismissible`，缺省可关）。
 * 强制选择面（三选一 / 遗物 / 结算结果）关掉只是清 DOM、没有路径重开 ⇒ 卡死到刷新。
 *
 * `--dsu` 由定位器**自给自足**（style.css 里 `.script-confirm` 那条）：窗住在 `<main>` 之外，
 * 拿不到画布上的 `--dsu`；若回落到 `:root` 的视口口径，视口比 16:9 宽时窗会比画布大一圈。
 */
"use strict";

/** 面板窗三档（CSS 的 `.pw[data-size]` 是同一份字面尺寸的唯一真源）。 */
const PANEL_WINDOW_SIZES = ["S", "M", "L"];

/** 打开（或**覆盖**唯一那扇）面板窗；返回动作按钮数组。
 *
 * 返回数组而不是 promise：`scriptConfirm` 的调用方与烟测的 `clickAndWait` 靠这个箭头
 * 返回 `run()` 的 promise 来决定要不要等这一拍。
 *
 * - `size` ∈ `"S" | "M" | "L"`（默认 `S`：确认框就是 S 档的一扇窗，spec R2）；
 * - `body` 是节点数组或文本（文本落成一个 `<p>`）；
 * - `actions` 是 `{label, primary, ghost, run}[]`；
 * - `layout: "split"` 写进 `.pw-body` 的 `data-layout`（固定区 + 只有列表滚，spec §3.2）；
 * - `dialog` 写进定位器的 `data-dialog`（烟测据此区分是哪一种面）；
 * - `dismissible` 决定**画不画关闭键**（票 10 / review 修复 C）：缺省 = 可关（开局页三扇窗、
 *   棋盘那两块面都直连本口、不传参）。**强制选择面**（三选一 / 遗物 / 结算结果）必须显式传
 *   `false`——判据只有这一个显式入参，**不许**按 `data-dialog` 字符串猜：猜法会把新面默认
 *   变成"可关"，而关掉那类面只是清 DOM（`state.script.confirm` 不重开、只能刷新）。
 */
function panelOpen(spec) {
  const options = spec || {};
  const dismissible = options.dismissible !== false;
  const dialog = $("script-confirm");
  const backdrop = $("script-confirm-backdrop");
  const win = $("script-confirm-window");
  const bodyBox = $("script-confirm-body");
  if (!dialog || !win || !bodyBox) return null;
  win.setAttribute("data-size", PANEL_WINDOW_SIZES.indexOf(options.size) >= 0 ? options.size : "S");
  const title = $("script-confirm-title");
  if (title) title.textContent = options.title || "";
  const hint = $("script-confirm-text");
  if (hint) hint.textContent = options.hint || "";
  const note = $("script-confirm-note");
  if (note) note.textContent = options.footNote || "";
  bodyBox.innerHTML = "";
  const items = Array.isArray(options.body) ? options.body : (options.body ? [options.body] : []);
  for (const item of items) {
    if (typeof item === "string") {
      const line = document.createElement("p");
      line.textContent = item;
      bodyBox.appendChild(line);
    } else if (item) {
      bodyBox.appendChild(item);
    }
  }
  bodyBox.hidden = items.length === 0;
  if (options.layout) bodyBox.setAttribute("data-layout", options.layout);
  else bodyBox.removeAttribute("data-layout");
  const box = $("script-confirm-actions");
  const buttons = [];
  if (box) {
    box.innerHTML = "";
    for (const action of options.actions || []) {
      const button = document.createElement("button");
      button.type = "button";
      if (action.primary) button.classList.add("primary");
      if (action.ghost) button.classList.add("ghost");
      button.textContent = action.label;
      // 箭头**返回** `run()` 的 promise：烟测的 `clickAndWait` 会等它跑完再进下一块。
      button.addEventListener("click", () => action.run());
      box.appendChild(button);
      buttons.push(button);
    }
  }
  // 关闭键：**逐扇现写**（同一扇壳被所有面共用，上一次开的是哪种面不算数）。`hidden` 的
  // display 归全局那条 `!important`，这里不补第二条 display 补丁（spec A3）。
  const close = $("script-confirm-close");
  if (close) close.hidden = !dismissible;
  dialog.setAttribute("data-dialog", options.dialog || "confirm");
  dialog.hidden = false;
  if (backdrop) backdrop.hidden = false;
  return buttons;
}

/** 收起面板窗与遮罩，清掉两处状态（`data-dialog` / `data-layout`）。 */
function panelClose() {
  const dialog = $("script-confirm");
  const backdrop = $("script-confirm-backdrop");
  const bodyBox = $("script-confirm-body");
  if (dialog) {
    dialog.hidden = true;
    dialog.removeAttribute("data-dialog");
  }
  if (backdrop) backdrop.hidden = true;
  if (bodyBox) bodyBox.removeAttribute("data-layout");
  return null;
}

/** 窗开着吗（定位器的 `hidden` 是真源）。 */
function panelIsOpen() {
  const dialog = $("script-confirm");
  return Boolean(dialog) && dialog.hidden === false;
}

/* 关闭键：只收起窗，**不接管 ESC**（两套壳争一个键会互相收窗）。 */
(function () {
  const close = $("script-confirm-close");
  if (close) close.addEventListener("click", () => panelClose());
})();
