/* SGS 前端 · 排列：观星双行布局与 ArrangeCards 载荷。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 */
"use strict";

/* ------------------------------------------------------- 排列（W6-03） */

/** 排列的**牌面**：只认本次询问的 `pending.revealed`（= 引擎授予这次询问的牌堆顶）。
 *
 * W6-07 / 评审 J9：规格里**不再重发**同一份牌面（`arrange.cards` 已删），牌面只有这一个
 * 真源。非空校验与 `_mask_watched_faces` 同姿态：不是询问者就没有牌面 ⇒ `null`。
 */
function arrangeCards(pending) {
  if (!pending || pending.kind !== "ARRANGE") return null;
  const cards = pending.revealed;
  return Array.isArray(cards) && cards.length ? cards : null;
}

/** 这一步有没有 `Pass`（票 07：提交 / 放弃的语义按**引擎实际候选**决定）。
 *
 * 引擎当前的 `_arrange` 是**强制**的（只给 `ArrangeCards`，没有 `Pass`）：观星想不排，
 * 应当在上一步「是否发动」里放弃。若将来某条排列技能真给了 `Pass`，右端「放弃」也按
 * **当前排列**提交（见 :func:`submitRightAction`），不是送出一条 `Pass`。
 */
function arrangeHasPass(pending) {
  return ((pending && pending.legal_actions) || []).some((action) => action.kind === "Pass");
}

/** 排列的**退出说明**（唯一一份文案）：`refreshSubmit` 的提示条与排列面里的步骤提示
 *  原先逐字重复这两句，抽到这里避免改一处漏一处。
 *
 *  按**实际候选**说话：引擎的 `_arrange` 不给 `Pass` ⇒ 强制、唯一出口是「确认」；
 *  若某条排列技能给了 `Pass`，右端「放弃」也按当前排列执行（见 `submitRightAction`）。 */
function arrangeExitHint(pending) {
  return arrangeHasPass(pending)
    ? "右端「确认」与「放弃」等价：都按当前排列执行。"
    : "这一步是强制的：排列不能放弃，右端「确认」是唯一出口。";
}

/** 排列规格的**读取口**：只认服务端下发的 `pending.arrange`（投影读 `SkillDef.deck_arrange`）。
 *
 * 返回 `null` ⇒ fail-closed：没有规格就不画双行面、也不拼 Action（409 裸 Query /
 * 无技能定义 / 形状不完整 / 张数或切分对不上）。形状 = `{count, split: FREE_SPLIT |
 * {top, bottom}}`；`count` / `split` **不是**前端自己算的规则（S2：投影从技能定义读）。
 * 牌面从 `pending.revealed` 取（:func:`arrangeCards`），不在这里。
 */
function arrangeSpec(pending) {
  if (!pending || pending.kind !== "ARRANGE") return null;
  const cards = arrangeCards(pending);
  const spec = pending.arrange;
  if (!cards || !spec) return null;
  const count = Number(spec.count);
  if (!Number.isInteger(count) || count <= 0 || cards.length !== count) return null;
  if (spec.split === FREE_SPLIT) return spec;
  const split = spec.split || {};
  const top = Number(split.top);
  const bottom = Number(split.bottom);
  if (!Number.isInteger(top) || !Number.isInteger(bottom)) return null;
  if (top < 0 || bottom < 0 || top + bottom !== count) return null;
  return spec;
}

/** 排列面的本地布局：`top` / `bottom` 装的是**面内位置**（`order` 的元素），不是牌 id。
 * 键 = 询问序号 + 切分声明 + 牌面 id 序列：换询问 / 换规格 / 换牌就重建，同一询问的
 * 刷新保留玩家摆好的顺序。 */
function arrangeKey(pending, spec) {
  const cards = arrangeCards(pending) || [];
  return `${pending.seq}:${JSON.stringify(spec.split)}:${cards.map((card) => card.card_id).join(",")}`;
}

/** 初始布局：自由切分 = 全部在牌堆顶（参考图的样子，下行全是空槽）；固定切分按声明铺开。 */
function initialArrange(pending, spec, key) {
  const cards = arrangeCards(pending) || [];
  const count = cards.length;
  const free = spec.split === FREE_SPLIT;
  const fixed = free ? null : { top: Number(spec.split.top), bottom: Number(spec.split.bottom) };
  const topCount = free ? count : fixed.top;
  return {
    key,
    cards,
    count,
    free,
    fixed,
    // 两行交换（huo-shenzhugeliang 票 02）：行标签与"能不能行内调序"都来自载荷。
    rows: spec.rows || null,
    reorder: spec.reorder !== false,
    top: Array.from({ length: topCount }, (_, at) => at),
    bottom: Array.from({ length: count - topCount }, (_, at) => topCount + at),
    selected: null,
    dragging: null,
    // 拖拽期的**纯 DOM** 引用（票 01 的落点预览）：拖动源节点、两行容器与当前预览。
    // 它们不进载荷、不参与 `buildAction`；每次重绘由 :func:`renderArrange` 重新挂一遍。
    dragNode: null,
    nodes: null,
    preview: null,
    geometry: null,
  };
}

/** 双行布局 → 一条 `ArrangeCards`；`null` = 还没排成完整顺序（提交按钮据此置灰）。
 *
 * `order` = 上行左→右 **再接** 下行左→右，`split` = 上行张数（Q9(c) 的裁定）。
 * **`answers_seq` 必须写在最前**：它是 `_ActionBase` 的第一个字段，服务端候选的 JSON 也以
 * 它开头；`submitAction` 的 `JSON.stringify` 自检逐字节比键序，顺序错了会把一条合法的排列
 * 判成"不在候选里"。
 */
function arrangeAction(pending) {
  const spec = arrangeSpec(pending);
  const layout = state.arrange;
  if (!spec || !layout) return null;
  if (layout.key !== arrangeKey(pending, spec)) return null;
  const order = layout.top.concat(layout.bottom);
  if (order.length !== layout.count) return null;
  const seen = new Set();
  for (const index of order) {
    if (!Number.isInteger(index) || index < 0 || index >= layout.count || seen.has(index)) {
      return null;
    }
    seen.add(index);
  }
  if (!layout.free) {
    if (layout.top.length !== layout.fixed.top) return null;
    if (layout.bottom.length !== layout.fixed.bottom) return null;
  }
  return { answers_seq: pending.seq, kind: "ArrangeCards", order, split: layout.top.length };
}

/** 这张**面内位置**现在在哪一行（`null` = 布局坏了，调用方按无效处理）。 */
function arrangeRowOf(layout, index) {
  if (!layout) return null;
  if (layout.top.includes(index)) return "top";
  if (layout.bottom.includes(index)) return "bottom";
  return null;
}

/** 这一行的面内位置数组（未知行 ⇒ `null`）：拖拽判据里"行 → 数组"的唯一一处。 */
function arrangeRowCards(layout, row) {
  if (!layout) return null;
  if (row === "top") return layout.top;
  if (row === "bottom") return layout.bottom;
  return null;
}

/** 这一行的 DOM 句柄 `{row, cards}`（没重绘过 ⇒ `null`）：预览只碰这一份。 */
function arrangeRowNodes(layout, row) {
  if (!layout || !layout.nodes) return null;
  if (row === "top") return layout.nodes.top;
  if (row === "bottom") return layout.nodes.bottom;
  return null;
}

/** 拖拽落点必须 `preventDefault`：否则浏览器不允许 drop（冒泡上来的也一样）。 */
function arrangeAllowDrop(event) {
  if (event && event.preventDefault) event.preventDefault();
}

/** 排列的**移动判据**（唯一一份）：把面内位置 `index` 落到 `row` 的 `anchor` 之前
 * （`anchor === null` / `undefined` = 该行末尾）。返回要实施的计划；`null` = 这一下
 * 不合法、或落到自己身上（不产生移动）。
 *
 * - **自由切分**（观星）：一律插入——跨行插入后两行张数会变，`split` 由上行张数重算，
 *   仍然落在服务端候选集里（自由切分的候选覆盖每种切分）；
 * - **固定切分**（恂恂）：行内仍是插入（排序，一行张数不变）；**跨行 = 两张互换**
 *   （上行的牌落到下行那张的位置、下行的牌落到上行那张的位置）。两行张数必须恒等于
 *   声明值，否则提交的 `split` 与服务端候选（只有 `split = top` 那一种）对不上；
 * - 固定切分跨行的 `anchor` 只用来**认目标牌**：`null`（落到行容器）= 该行末尾那张
 *   （行是满的，没有空槽可追加）；目标行是空的 ⇒ 没有可换的牌，拒绝。
 *
 * 拖拽与点选（:func:`arrangePick`）、空槽点击 / 行容器落点（:func:`arrangeDrop`）
 * 全部走这一份判据，两条手势的语义因此一致。
 */
function arrangeMovePlan(index, row, anchor) {
  const layout = state.arrange;
  const target = arrangeRowCards(layout, row);
  if (!target) return null;
  if (!Number.isInteger(index) || index < 0 || index >= layout.count) return null;
  const from = arrangeRowOf(layout, index);
  if (from === null) return null;
  const at = anchor === undefined ? null : anchor;
  if (at !== null && target.indexOf(at) < 0) return null;
  if (at === index) return null;                     // 落到自己身上：不动（不再"甩到行末"）
  if (layout.reorder === false && from === row) {
    // 两行交换（七星）：**只上下交换、行内不调序**（用户 2026-11-04 Q4）。
    return null;
  }
  if (layout.free || from === row) {
    return { kind: "insert", index, row, anchor: at };
  }
  const partner = at === null ? target[target.length - 1] : at;
  if (partner === undefined) return null;            // 目标行空着：固定切分没有可换的牌
  return { kind: "swap", index, row, partner };
}

/** 实施 :func:`arrangeMovePlan` 的计划：只改本地布局（`top` / `bottom`），不碰 DOM 与载荷。 */
function arrangeApplyPlan(plan) {
  const layout = state.arrange;
  const source = arrangeRowCards(layout, arrangeRowOf(layout, plan.index));
  const target = arrangeRowCards(layout, plan.row);
  if (!source || !target) return false;
  const at = source.indexOf(plan.index);
  if (plan.kind === "swap") {
    const other = target.indexOf(plan.partner);
    if (other < 0) return false;
    source[at] = plan.partner;
    target[other] = plan.index;
    return true;
  }
  source.splice(at, 1);
  const anchor = plan.anchor === null ? -1 : target.indexOf(plan.anchor);
  target.splice(anchor < 0 ? target.length : anchor, 0, plan.index);
  return true;
}

/** 把 `index` 移到 `row` 的 `anchor` 之前（`null` = 该行末尾）。
 * 判据住 :func:`arrangeMovePlan`（自由切分 = 插入；固定切分跨行 = 互换，张数不变）。 */
function arrangeMoveTo(index, row, anchor) {
  const plan = arrangeMovePlan(index, row, anchor);
  if (!plan) return false;
  return arrangeApplyPlan(plan);
}

/** 拖拽开始时的**折叠几何**（票 01 的落点判定只认它）：拖动源先被抽出文档流，再把两行的
 *  行框与每张牌的矩形量一遍（源自己不参与）。
 *
 *  为什么不在每次 `dragover` 现读"指针底下是哪个节点"：预览本身要插占位格、让牌移位，
 *  指针底下的节点于是跟着变 —— 按当前节点判落点会形成回环（占位格恰好跑到指针下 ⇒ 判成
 *  "行末" ⇒ 整行再让一次 ⇒ 又回到原处），实机表现就是其它牌一直闪。落点改成**坐标对这份
 *  折叠几何的纯函数**之后，预览怎么动都不会反过来改判据（实机报障 2026-10-30）。 */
function arrangeMeasure(layout) {
  if (!layout.nodes) return null;
  const rows = {};
  for (const row of ["top", "bottom"]) {
    const nodes = arrangeRowNodes(layout, row);
    if (!nodes) { rows[row] = null; continue; }
    const box = nodes.cards.getBoundingClientRect();
    const cards = [];
    for (const index of arrangeRowCards(layout, row)) {
      if (index === layout.dragging) continue;      // 拖动源已不在流里：它那格不算落点
      const node = arrangeCardNodeIn(nodes.cards, index);
      if (!node) continue;
      const rect = node.getBoundingClientRect();
      if (!rect || !(Number(rect.width) > 0)) continue;
      cards.push({
        index,
        left: rect.left,
        right: rect.left + Number(rect.width),
        middle: rect.left + Number(rect.width) / 2,
      });
    }
    rows[row] = { box, cards };
  }
  return { rows };
}

/** 折叠几何**只量一次**（第一次 `dragover`，或没收到 `dragover` 就直接 drop 的那一下）。
 *  量之前先把拖动源抽出文档流（`.dragging`），量到的就是"源已经让出位置"的稳定布局；
 *  这也正是拖影的保险：`dragstart` 时不动样式，浏览器拍完拖影才在这里收走源。 */
function arrangeEnsureGeometry(layout) {
  if (layout.geometry) return layout.geometry;
  if (layout.dragNode) layout.dragNode.classList.add("dragging");
  layout.geometry = arrangeMeasure(layout) || { rows: {} };
  return layout.geometry;
}

/** 指针落在哪一行：按两行**行框**的纵向中点取最近（行间空隙 / 行标签都算给最近的那行）。 */
function arrangeRowAt(geometry, clientY) {
  const rows = geometry && geometry.rows;
  if (!rows) return null;
  let best = null;
  for (const row of ["top", "bottom"]) {
    const info = rows[row];
    if (!info || !info.box) continue;
    const distance = Math.abs(clientY - (Number(info.box.top) + Number(info.box.height) / 2));
    if (!best || distance < best.distance) best = { row, distance };
  }
  return best ? best.row : null;
}

/** 指针 x 在这一行**折叠后**的插入边界：插到第一张"中点在指针右边"的牌之前；中点在指针
 *  左边的都不算 ⇒ 行末（`null`）。落在牌面左 / 右半边因此天然就是"放到它前 / 后"。 */
function arrangeAnchorAt(rowInfo, clientX) {
  for (const card of rowInfo.cards) {
    if (clientX < card.middle) return card.index;
  }
  return null;
}

/** 固定切分跨行时的**交换对象**：指针底下那张牌；落在牌与牌之间 / 行容器空白处取离指针
 *  最近的那张（落到行末空白 = 该行末尾那张——行是满的，没有空槽可追加）。 */
function arrangeSwapPartner(rowInfo, clientX) {
  const cards = rowInfo.cards;
  if (!cards.length) return null;
  for (const card of cards) {
    if (clientX >= card.left && clientX <= card.right) return card.index;
  }
  let best = null;
  for (const card of cards) {
    const distance = Math.abs(clientX - card.middle);
    if (!best || distance < best.distance) best = { index: card.index, distance };
  }
  return best.index;
}

/** 拖拽落点计划（唯一判据的**坐标版**）：指针 → 行 / 插入边界 / 交换对象，再交给
 *  :func:`arrangeMovePlan`。没有坐标（合成事件）⇒ `null`（不猜、不动）。 */
function arrangePlanFromPoint(layout, clientX, clientY) {
  const dragging = layout.dragging;
  if (dragging === null || dragging === undefined) return null;
  if (typeof clientX !== "number" || typeof clientY !== "number") return null;
  const geometry = arrangeEnsureGeometry(layout);
  const row = arrangeRowAt(geometry, clientY);
  if (row === null) return null;
  const rowInfo = geometry.rows[row];
  if (!rowInfo) return null;
  const crossRow = arrangeRowOf(layout, dragging) !== row;
  const anchor = !layout.free && crossRow
    ? arrangeSwapPartner(rowInfo, clientX)
    : arrangeAnchorAt(rowInfo, clientX);
  return arrangeMovePlan(dragging, row, anchor);
}

/** 行容器里 `data-card-index` 为 `index` 的牌节点（预览用；`null` = 没找到）。 */
function arrangeCardNodeIn(box, index) {
  if (!box) return null;
  return Array.from(box.children).find(
    (node) => node.getAttribute && node.getAttribute("data-card-index") === String(index)) || null;
}

/** 清掉拖拽预览（占位格 / 交换高亮 / 行高亮）：只动 DOM，不动布局。 */
function arrangePreviewClear() {
  const layout = state.arrange;
  const preview = layout && layout.preview;
  if (!preview) return;
  if (preview.placeholder && preview.placeholder.parentNode) {
    preview.placeholder.parentNode.removeChild(preview.placeholder);
  }
  if (preview.swapNode) preview.swapNode.classList.remove("swap");
  for (const row of ["top", "bottom"]) {
    const nodes = arrangeRowNodes(layout, row);
    if (nodes) nodes.row.classList.remove("drop");
  }
  layout.preview = null;
}

/** 把占位格放到这一行的**落点**上：有锚 = 插到那张牌之前；没有锚（行末）= 插到所有牌
 *  之后、**空槽之前**。自由切分的空槽恒排在牌的后面，占位格若跟着追加到整行末尾，
 *  "落到格子里"这一下就看错位了（真落点还在空槽之前）。 */
function arrangeInsertPlaceholder(cards, anchor, placeholder) {
  if (anchor) {
    cards.insertBefore(placeholder, anchor);
    return;
  }
  const firstSlot = Array.from(cards.children).find(
    (node) => node.getAttribute && node.getAttribute("data-empty") === "1");
  if (firstSlot) cards.insertBefore(placeholder, firstSlot);
  else cards.appendChild(placeholder);
}

/** 拖拽中的**动态落点预览**（票 01）：鼠标拽着一张牌时，其它牌为它让道。
 *
 * - **插入**（自由切分 / 行内排序）：在落点上插一个与卡面同尺寸的占位格，那一行从落点
 *   起整体让开一格——玩家在放下之前就看得到会落在谁前 / 谁后；
 * - **交换**（固定切分跨行）：两行张数不变、没有空槽可让，改为给**会换走的那张牌**挂
 *   交换高亮（`::after` 写「交换」），别让玩家猜这一下换哪张。
 *
 * 这里只改 DOM、**不跑** `renderPending`：整面重绘会把拖动源换成新节点，真浏览器里会
 * 当场中断拖拽。 */
function arrangePreview(plan) {
  const layout = state.arrange;
  if (!layout || !layout.nodes) return;
  const key = plan
    ? `${plan.kind}:${plan.row}:${plan.kind === "swap" ? plan.partner : plan.anchor}`
    : "none";
  if (layout.preview && layout.preview.key === key) return;
  arrangePreviewClear();
  const preview = { key, placeholder: null, swapNode: null };
  layout.preview = preview;
  if (!plan) return;
  const row = arrangeRowNodes(layout, plan.row);
  if (!row) return;
  row.row.classList.add("drop");
  if (plan.kind === "swap") {
    preview.swapNode = arrangeCardNodeIn(row.cards, plan.partner);
    if (preview.swapNode) preview.swapNode.classList.add("swap");
    return;
  }
  const placeholder = document.createElement("div");
  placeholder.className = "arrange-placeholder";
  placeholder.setAttribute("data-row", plan.row);
  placeholder.setAttribute("aria-hidden", "true");
  preview.placeholder = placeholder;
  arrangeInsertPlaceholder(
    row.cards,
    plan.anchor === null ? null : arrangeCardNodeIn(row.cards, plan.anchor),
    placeholder,
  );
}

/** 拖拽经过（行容器上的 `dragover`；牌 / 空槽的事件会冒泡到这里）：坐标算计划 → 画预览。
 *
 *  拖动源被抽出文档流（`.dragging`）在 :func:`arrangeEnsureGeometry` 里、**比 `dragstart`
 *  晚一步**，免得浏览器在 `dragstart` 之后拍的拖影被一起藏掉；抽走的那一格正好由占位格
 *  顶上，行宽不涨、不换行。 */
function arrangeHover(event) {
  const layout = state.arrange;
  if (!layout || layout.dragging === null || layout.dragging === undefined) return;
  arrangePreview(arrangePlanFromPoint(layout, event && event.clientX, event && event.clientY));
}

/** 拖拽放下（行容器上的 `drop`）：按同一份坐标判据落地 → 清拖拽态 → 重绘。
 *  没有坐标 / 没有合法落点 = 不动（只收掉预览，拖拽取消）。 */
function arrangeCommitDrag(event) {
  const layout = state.arrange;
  const pending = state.view ? state.view.pending : null;
  if (state.playback || !pending || pending.kind !== "ARRANGE" || !layout) return;
  if (layout.dragging === null || layout.dragging === undefined) return;
  const plan = arrangePlanFromPoint(layout, event && event.clientX, event && event.clientY);
  arrangePreviewClear();
  if (plan) arrangeApplyPlan(plan);
  layout.dragging = null;
  layout.dragNode = null;
  layout.geometry = null;
  layout.selected = null;
  renderPending(state.view);
}

/** 拖拽结束（放下 / 取消都走这里）：预览是纯 DOM 手法，模型没被它改过 ⇒ 取消时重绘一次
 *  就整体复原；成功放下时 `dragging` 已在本步清空，这里不再重绘。已点选的牌不清掉
 *  （取消拖拽不该顺手丢掉用户上一手的点选）。 */
function arrangeDragEnd() {
  const layout = state.arrange;
  if (!layout) return;
  const dragging = layout.dragging;
  arrangePreviewClear();
  layout.dragging = null;
  layout.dragNode = null;
  layout.geometry = null;
  if ((dragging === null || dragging === undefined) || !state.view) return;
  renderPending(state.view);
}

/** 点牌 / 点空槽：第一下**选中**，第二下把它移到目标位置（同行的牌 = 插到它前面；
 * 另一行的牌 = 按 :func:`arrangeMovePlan` 插入 / 互换；空槽 = 追加到那一行末尾）。
 * 拖动之外的点击 / 键盘后备。 */
function arrangePick(index) {
  const pending = state.view ? state.view.pending : null;
  const layout = state.arrange;
  if (state.playback || !pending || pending.kind !== "ARRANGE" || !layout) return;
  if (layout.selected === null || layout.selected === undefined) {
    layout.selected = index;
  } else if (layout.selected === index) {
    layout.selected = null;
  } else {
    const target = arrangeRowOf(layout, index);
    if (target !== null) arrangeMoveTo(layout.selected, target, index);
    layout.selected = null;
  }
  renderPending(state.view);
}

/** **点选手势**的落点（空槽点击 / 桥上的直接调用）：把当前拖动或选中的牌移过去。
 *  拖拽的落点不走这里——那条路径由 :func:`arrangeCommitDrag` 按坐标算计划。
 *
 * `anchor` = 目标行里的**落点锚**（插到它之前；`null` = 该行末尾 / 固定切分时
 * 认「该行末尾那张」为交换对象）。 */
function arrangeDrop(row, anchor) {
  const pending = state.view ? state.view.pending : null;
  const layout = state.arrange;
  if (state.playback || !pending || pending.kind !== "ARRANGE" || !layout) return;
  const from = layout.dragging !== null && layout.dragging !== undefined
    ? layout.dragging
    : layout.selected;
  if (from === null || from === undefined) return;
  arrangePreviewClear();
  arrangeMoveTo(from, row, anchor);
  layout.dragging = null;
  layout.dragNode = null;
  layout.selected = null;
  renderPending(state.view);
}

/** 排列面里一张**被观看的牌**：可拖、可点选（点选后点目标位置 = 移动）。
 *
 * 票 07（spec D6）：牌面与手牌 / 处理区**同一套 88×124**（:func:`cardFaceNode`），不再
 * 画成紧凑的 `.card` 芯片。拖拽与点击两种搬牌手势都保留：`draggable` + 四个 drag 事件；
 * 点击走 :func:`arrangePick`（第一下选中、第二下移到目标位置）。
 *
 * 票 01：拖拽落点按指针坐标判（见 :func:`arrangePlanFromPoint`）——落在牌面中点的右半边
 * 就是"放到它后面"，不再一律插到它前面；本节点只负责起拖 / 收尾，落点由行容器统一算。 */
function arrangeCardNode(layout, index) {
  const card = layout.cards[index];
  const chip = cardFaceNode(card, { selected: layout.selected === index });
  chip.classList.add("arrange-card");
  chip.setAttribute("data-card-index", String(index));
  chip.setAttribute("data-row", arrangeRowOf(layout, index));
  chip.draggable = true;
  chip.addEventListener("click", () => arrangePick(index));
  chip.addEventListener("dragstart", (event) => {
    if (state.playback) return;
    layout.dragging = index;
    layout.dragNode = chip;
    layout.preview = null;
    layout.geometry = null;
    if (event && event.dataTransfer && event.dataTransfer.setData) {
      event.dataTransfer.setData("text/plain", String(index));
    }
  });
  chip.addEventListener("dragend", () => arrangeDragEnd());
  return chip;
}

/** 排列面的一个空槽：点它 = 追加到这一行的末尾（拖到它上面由行容器统一收）。 */
function arrangeSlotNode(row) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "card arrange-slot";
  chip.setAttribute("data-empty", "1");
  chip.setAttribute("data-row", row);
  chip.textContent = "空槽";
  chip.addEventListener("click", () => arrangeDrop(row, null));
  return chip;
}

/** 双行容器：标签行（牌堆顶 / 牌堆底）+ 牌 / 空槽行。
 *
 *  **拖拽的唯一落点就是行容器**：牌 / 空槽 / 占位格的 `dragover` 与 `drop` 都冒泡到这里，
 *  落点由 :func:`arrangePlanFromPoint` 按指针坐标对**折叠几何**算（牌上按中线分前后，行末
 *  空白 = 该行末尾；固定切分跨行 = 与指针下 / 最近那张互换）。这样预览怎么让牌移位，都不会
 *  反过来改判据（实机闪烁的根因）。行高亮跟着预览走（见 :func:`arrangePreview`）。 */
function arrangeRowNode(name, label, hint) {
  const row = document.createElement("div");
  row.className = "arrange-row";
  row.setAttribute("data-row", name);
  const head = document.createElement("div");
  head.className = "arrange-label";
  head.textContent = label;
  const note = document.createElement("span");
  note.className = "dim";
  note.textContent = hint;
  head.appendChild(note);
  const cards = document.createElement("div");
  cards.className = "arrange-cards";
  cards.setAttribute("data-row", name);
  row.appendChild(head);
  row.appendChild(cards);
  row.addEventListener("dragover", (event) => {
    arrangeAllowDrop(event);
    arrangeHover(event);
  });
  row.addEventListener("drop", (event) => {
    arrangeAllowDrop(event);
    arrangeCommitDrag(event);
  });
  return { row, cards };
}

/** `ARRANGE` 的双行拖拽面（W6-03）：**唯一**的排列渲染路径。
 *
 * 上行 = 牌堆顶（左 → 右 = 从最上往下），下行 = 牌堆底（左 → 右 = 先接在现有牌堆底
 * 之下）；确认提交的 `order` 就是上行左→右再接下行左→右（Q9(c)）。自由切分的下行始终
 * 留 `count` 个位置（用户参考图的布局：全部在顶上时下行全是空槽），固定切分按声明的
 * `bottom` 收口。
 *
 * **fail-closed 的触发面**（W6-07）：规格或牌面缺失时只画"排列不可用，请刷新"，不铺
 * 任何可提交的牌。两种载荷都走到这里 ——
 * ① **200 但服务端给不出规格**：投影的 ``_arrange_spec`` 返回 ``null``（无技能定义 /
 *    固定切分形状不完整 / 声明张数与实际牌数对不上 / 查看者不是询问者）；
 * ② **409 的陈旧询问**：`submitAction` 收到 409 后 `refresh()` 会丢掉这份陈旧载荷并
 *    取当前询问；若刷新回来的仍是 ``ARRANGE`` 而规格仍拿不到，就落到这里。
 *    故"刷新"这个动作**就是** 409 的 fail-closed 出口：陈旧询问永远不排，刷新后的状态
 *    才排；而不是把陈旧载荷硬画成一份能提交的排列面。
 */
function renderArrange(pending) {
  const box = $("t-options");
  const hint = $("t-target-hint");
  const spec = arrangeSpec(pending);
  if (!spec) {
    const line = document.createElement("div");
    line.className = "hint arrange-unavailable";
    line.textContent = "排列不可用，请刷新";
    box.appendChild(line);
    state.arrange = null;
    if (hint) {
      hint.textContent = "这一份排列缺少规格（可能是 409 的陈旧询问）：请点「刷新」重新取当前询问。";
    }
    return;
  }
  if (!state.arrange || state.arrange.key !== arrangeKey(pending, spec)) {
    state.arrange = initialArrange(pending, spec, arrangeKey(pending, spec));
  }
  const layout = state.arrange;
  const rows = layout.rows || {};
  const topLabel = rows.top || "牌堆顶";
  const bottomLabel = rows.bottom || "牌堆底";
  const top = arrangeRowNode("top", topLabel, layout.rows ? "" : "（左 = 最上）");
  const bottom = arrangeRowNode(
    "bottom",
    bottomLabel,
    layout.rows
      ? `（固定 ${layout.fixed ? layout.fixed.top : layout.top.length} / ${layout.fixed ? layout.fixed.bottom : layout.bottom.length}：只能上下交换）`
      : layout.free
        ? "（左起接在现有牌堆底之下）"
        : `（固定 ${layout.fixed.top} 顶 / ${layout.fixed.bottom} 底）`,
  );
  // 重绘后旧节点全部作废：预览 / 拖动源的 DOM 引用要跟着换（票 01）。
  layout.nodes = { top, bottom };
  layout.preview = null;
  layout.dragNode = null;
  layout.geometry = null;
  box.appendChild(top.row);
  box.appendChild(bottom.row);
  for (const index of layout.top) top.cards.appendChild(arrangeCardNode(layout, index));
  if (layout.free && layout.top.length < layout.count) {
    top.cards.appendChild(arrangeSlotNode("top"));
  }
  for (const index of layout.bottom) bottom.cards.appendChild(arrangeCardNode(layout, index));
  const capacity = layout.free ? layout.count : layout.fixed.bottom;
  for (let at = layout.bottom.length; at < capacity; at += 1) {
    bottom.cards.appendChild(arrangeSlotNode("bottom"));
  }
  if (hint) {
    // 票 01：固定切分不再说"只能行内排序"——跨行拖动是**互换**，两行张数不变。
    const how = layout.reorder === false
      ? `只交换「${topLabel}」与「${bottomLabel}」两行之间的牌：拖一张到另一行的牌上 = 两张互换；`
        + "行内顺序不变（也可先点一张牌再点另一行的牌）。"
      : layout.free
        ? "拖动卡牌在牌堆顶 / 牌堆底之间切换：落在牌的左 / 右半边 = 放到它前 / 后；行内也是这样排序。"
          + "也可先点一张牌再点目标位置。"
        : `上下两行张数固定（${layout.fixed.top} 顶 / ${layout.fixed.bottom} 底）：`
          + "拖动可交换上下两行的牌（拖到另一行的牌上 = 两张互换），行内拖动排序；"
          + "也可先点一张牌再点目标位置。";
    // 票 07：出口说明按**实际候选**说话（文案唯一一份住 :func:`arrangeExitHint`）。
    hint.textContent = `${how} ${arrangeExitHint(pending)}`;
  }
}
