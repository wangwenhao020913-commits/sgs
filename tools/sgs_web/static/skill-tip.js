/* SGS 前端 · 技能详情浮层（ui-one-screen 票 06 / spec A8、§8 R7）。
 *
 * 一层**全局**浮层，宿主只带 `data-skill`：`document` 上**一组**三条监听（mouseover /
 * mousemove / mouseout）就是全部接线——不给每个格子 / 每一行挂 `mouseenter` / `mouseleave`
 * （行数一多就是 O(n) 个监听，列表一重绘还得重挂）。
 *
 * 三条纪律（评审会按它们读代码）：
 *
 * 1. **正文只来自目录行**（§8 R7）：正文只认棋盘体目录行（`catalog[id]`）的 `desc`——票 01
 *    加的字段，服务端 `descriptions.py` 裁过。拿不到目录行 ⇒ **这一层不弹**（票 08 拍板的
 *    读法：名字 / 正文都不印，绝不退回任何未裁的文案；编辑器页那些只有 `data-skill`、没有
 *    目录行的行因此也不再弹「裸 id」）。本文件**零正则、零截断、零第二份文案**——
 *    裁切规则只有 `descriptions.py` 一份实现。
 * 2. **一层、不吃鼠标**：`#skilltip` 是静态节点（住在所有 `<main>` 之外），
 *    `pointer-events: none`；宿主侧只有属性，没有任何逐节点监听。
 * 3. **翻转判据是纯函数**（`skillTipPlace`）：它只吃数字（设计单位），DOM 读取留在调用方。
 *    宿主在**开着的面板窗**里 ⇒ bounds = 那扇窗的 rect（贴窗边也翻，票面拍板的第 3 条），
 *    否则 = 画布壳的 rect。垫片没有布局引擎，故这条判据由
 *    `tests/test_web_static.py::test_the_skill_tip_flips_at_the_shell_and_the_window_edge`
 *    直接喂数字；行为归烟测 `UI1-30..32`。
 *
 * `--dsu` 与面板窗同一口径**自带**（见 `style.css:.skilltip`）：浮层住在所有 `<main>` 之外，
 * 继承不到画布上的 `--dsu`。
 */
"use strict";

/* ---------------------------------------------------------------- 常量 */

/** 光标到浮层左上角的偏移（原型 `tipMove` 的 14 / 18 dsu）。 */
const SKILL_TIP_GAP_X_DSUS = 14;
const SKILL_TIP_GAP_Y_DSUS = 18;

/** 贴边界时的内缩：翻转之后也不越出这条线（原型 8dsu）。 */
const SKILL_TIP_INSET_DSUS = 8;

/** 画布壳的宽 = 1600 设计单位（`.design-canvas` 的 `--dsu` 就是这个除法）。 */
const SKILL_TIP_SHELL_WIDTH_DSUS = 1600;

/** 稀有度的显示名（取值住 `src/sgs/data/values.py:Rarity`，显示名归界面层）。
 *
 * 与整备面的 `SCRIPT_ITEM_KIND_LABELS` 同姿态：这是**类别**文案，不是描述来源。
 */
const SKILL_TIP_RARITY_LABELS = {
  common: "普通",
  rare: "稀有",
  epic: "史诗",
  legendary: "传说",
};

/** 当前宿主（`[data-skill]` 命中的那个节点）；`null` = 浮层收着。 */
let SKILL_TIP_HOST = null;

/* ---------------------------------------------------------------- 纯函数 */

/** 纯函数：光标（相对 `bounds` 左上角）⇒ 浮层落点（同样相对 `bounds`）。**全程设计单位**。
 *
 * - 默认落在光标右下方（14 / 18 dsu，原型同值）；
 * - 越右 / 越下 ⇒ 翻到光标左侧 / 上方（两边都判——窗内宿主也要贴窗边翻）；
 * - 翻完仍越界（浮层比窗还宽 / 光标贴边）⇒ 贴住 8dsu 的内缩边界。
 *
 * **不读 DOM**（票面：DOM 读取留在调用方）：`bounds` 是 `{width, height}` 纯数据。
 */
function skillTipPlace(cursorX, cursorY, tipW, tipH, bounds) {
  const width = Number(bounds && bounds.width) || 0;
  const height = Number(bounds && bounds.height) || 0;
  const inset = SKILL_TIP_INSET_DSUS;
  // 先按"右下方"摆，越界就翻到另一侧；最后统一夹进 [内缩, 边界 - 浮层尺寸 - 内缩]。
  const maxX = Math.max(inset, width - inset - tipW);
  const maxY = Math.max(inset, height - inset - tipH);
  let x = cursorX + SKILL_TIP_GAP_X_DSUS;
  let y = cursorY + SKILL_TIP_GAP_Y_DSUS;
  if (x + tipW > width - inset) x = cursorX - SKILL_TIP_GAP_X_DSUS - tipW;
  if (y + tipH > height - inset) y = cursorY - SKILL_TIP_GAP_Y_DSUS - tipH;
  return {
    x: Math.min(maxX, Math.max(inset, x)),
    y: Math.min(maxY, Math.max(inset, y)),
  };
}

/* ---------------------------------------------------------------- 形状 */

/** 浮层层本身（静态节点；整个页面只有这一份）。 */
function skillTipNode() {
  return $("skilltip");
}

/** 浮层里的一行（`createElement` + 文本；三层就够，不引组件）。 */
function skillTipLine(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text === undefined || text === null ? "" : String(text);
  return node;
}

/** 当前可见的设计画布（`core.js` 的 `VIEW_NAMES` 是页面名的唯一真源；`show()` 只翻 `hidden`）。 */
function skillTipShell() {
  for (const page of VIEW_NAMES) {
    const node = $(`view-${page}`);
    if (node && node.hidden === false) return node;
  }
  return null;
}

/** 翻转边界：宿主在**开着的面板窗**里 ⇒ 那扇窗的 rect；否则 ⇒ 画布壳的 rect。
 *
 * 返回的 `unit` = 一 dsu 的像素数（取画布壳宽 / 1600；窗与画布同一口径，见
 * `style.css:.script-confirm` 的 `--dsu` 注释）。DOM 读取只住在这里。
 */
function skillTipFrame(host) {
  const closable = Boolean(host) && typeof host.closest === "function";
  const win = closable ? host.closest(".pw") : null;
  const box = win || (closable ? host.closest(".design-canvas") : null) || skillTipShell();
  const rect = box && typeof box.getBoundingClientRect === "function"
    ? box.getBoundingClientRect()
    : { left: 0, top: 0, width: 0, height: 0 };
  const shell = skillTipShell();
  const shellRect = shell && typeof shell.getBoundingClientRect === "function"
    ? shell.getBoundingClientRect()
    : rect;
  const unit = (Number(shellRect.width) || 0) / SKILL_TIP_SHELL_WIDTH_DSUS;
  return {
    left: Number(rect.left) || 0,
    top: Number(rect.top) || 0,
    width: Number(rect.width) || 0,
    height: Number(rect.height) || 0,
    unit: unit > 0 ? unit : 1,
  };
}

/* ---------------------------------------------------------------- 载荷 */

/** 目录行（棋盘体 `catalog[id]`）：`desc` 就是票 01 服务端裁过的那段全文。
 *
 * **这是正文的唯一来源**：没有目录行 ⇒ 一条描述都拿不到（快照里那份是未裁的，不读）。
 */
function skillTipCatalogRow(skillId) {
  const board = state && state.script ? state.script.board : null;
  const catalog = (board && board.catalog) || null;
  return (catalog && catalog[skillId]) || null;
}

/** 浮层要的四段：名字 / 类别 / 一行元信息（稀有度 · 价，目录行才有）/ 正文。
 *
 * 名字 / 类别 / 稀有度 / 单价全走整备面那一份目录读口（`scriptItemRow` / `scriptPriceText`），
 * 不写第二份中文表；正文只有目录行的 `desc` 一处来源（服务端裁过的全文）。
 * **拿不到目录行 ⇒ 返回 `null`**（调用方不弹这一层，连名字都不印——那个名字只会是裸 id）；
 * 目录行在、只是没有 `desc` ⇒ 名字照印，正文不印。
 */
function skillTipData(skillId) {
  const id = String(skillId || "");
  const raw = skillTipCatalogRow(id);
  if (!raw) return null;
  const row = scriptItemRow(id);
  const meta = [];
  if (raw.rarity) meta.push(SKILL_TIP_RARITY_LABELS[raw.rarity] || String(raw.rarity));
  if (scriptPriceText(row)) meta.push(scriptPriceText(row));
  return {
    id,
    name: String(row.name || id),
    kind: SCRIPT_ITEM_KIND_LABELS[row.kind] || "",
    meta: meta.join(" · "),
    desc: String(raw.desc || ""),
  };
}

/* ---------------------------------------------------------------- 三个口 */

/** 显示 / 重铺浮层（**只有一层**：每次重铺它自己，不碰宿主、不给宿主挂监听）。
 *
 * `meta.host` = 宿主节点：只用来定翻转边界（`skillTipFrame`），不读它的文案；
 * `meta.event` = 触发它的那次指针事件（有就顺手摆好落点，避免第一帧闪在 0,0）。
 * **没有目录行的宿主返回 `null` 并收起浮层**（票 08：无目录行 ⇒ 不弹这一层）。
 */
function skillTipShow(skillId, meta) {
  const tip = skillTipNode();
  const id = String(skillId || "");
  if (!tip || !id) return null;
  const data = skillTipData(id);
  if (!data) return skillTipHide();
  SKILL_TIP_HOST = (meta && meta.host) || null;
  tip.innerHTML = "";
  const head = document.createElement("div");
  head.className = "tt-head";
  head.appendChild(skillTipLine("span", "tt-name", data.name));
  if (data.kind) head.appendChild(skillTipLine("span", "tt-kind", data.kind));
  tip.appendChild(head);
  if (data.meta) tip.appendChild(skillTipLine("div", "tt-meta", data.meta));
  if (data.desc) tip.appendChild(skillTipLine("div", "tt-desc", data.desc));
  tip.hidden = false;
  if (meta && meta.event) skillTipMove(meta.event);
  return data;
}

/** 跟随鼠标：像素读数在这里折成设计单位，落点由纯函数 `skillTipPlace` 给。 */
function skillTipMove(event) {
  const tip = skillTipNode();
  if (!tip || tip.hidden || !SKILL_TIP_HOST) return null;
  const cursorX = Number(event && event.clientX);
  const cursorY = Number(event && event.clientY);
  if (!Number.isFinite(cursorX) || !Number.isFinite(cursorY)) return null;
  const frame = skillTipFrame(SKILL_TIP_HOST);
  const box = typeof tip.getBoundingClientRect === "function"
    ? tip.getBoundingClientRect()
    : { width: 0, height: 0 };
  const place = skillTipPlace(
    (cursorX - frame.left) / frame.unit,
    (cursorY - frame.top) / frame.unit,
    (Number(box.width) || 0) / frame.unit,
    (Number(box.height) || 0) / frame.unit,
    { width: frame.width / frame.unit, height: frame.height / frame.unit },
  );
  tip.style.left = `${frame.left + place.x * frame.unit}px`;
  tip.style.top = `${frame.top + place.y * frame.unit}px`;
  return place;
}

/** 收起：换页 / 换局 / 关窗都走这里；宿主引用一并摘掉，不留一层挂着的 DOM。 */
function skillTipHide() {
  const tip = skillTipNode();
  SKILL_TIP_HOST = null;
  if (!tip) return null;
  tip.hidden = true;
  tip.innerHTML = "";
  return null;
}

/* ---------------------------------------------------------------- 一次委托 */

/** 事件目标 → 宿主（`closest` 找不到就是 `null`：垫片同语义地实现了这一口）。 */
function skillTipHostOf(target) {
  return target && typeof target.closest === "function" ? target.closest("[data-skill]") : null;
}

/** 三条 document 监听 = 浮层的全部接线（票面「一次委托」）。 */
document.addEventListener("mouseover", (event) => {
  const host = skillTipHostOf(event && event.target);
  if (host) return skillTipShow(host.getAttribute("data-skill"), { host, event });
  return skillTipHide();
});

document.addEventListener("mousemove", (event) => skillTipMove(event));

document.addEventListener("mouseout", (event) => {
  const host = skillTipHostOf(event && event.target);
  if (!host) return null;
  // `relatedTarget` 还在**同一个**宿主里（移到它自己的子节点）= 没离开，不收。
  const related = event.relatedTarget;
  const inside = related && typeof related.closest === "function"
    ? related.closest("[data-skill]") === host
    : false;
  return inside ? null : skillTipHide();
});
