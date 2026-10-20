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
    top: Array.from({ length: topCount }, (_, at) => at),
    bottom: Array.from({ length: count - topCount }, (_, at) => topCount + at),
    selected: null,
    dragging: null,
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

/** 把 `index` 移到 `row`：`beforeIndex` = 插到那张牌之前，`null` = 该行末尾。
 * 固定切分只允许**行内**排序（跨行会破坏服务端声明的 top / bottom），越界即拒绝。 */
function arrangeMoveTo(index, row, beforeIndex) {
  const layout = state.arrange;
  if (!layout || !Number.isInteger(index)) return false;
  const from = arrangeRowOf(layout, index);
  if (from === null || (from !== row && !layout.free)) return false;
  const source = from === "top" ? layout.top : layout.bottom;
  source.splice(source.indexOf(index), 1);
  const target = row === "top" ? layout.top : layout.bottom;
  if (beforeIndex === null || beforeIndex === undefined || beforeIndex < 0) {
    target.push(index);
  } else {
    const at = target.indexOf(beforeIndex);
    target.splice(at < 0 ? target.length : at, 0, index);
  }
  return true;
}

/** 点牌 / 点空槽：第一下**选中**，第二下把它移到目标位置（同行的牌 = 插到它前面；
 * 另一行的牌 = 跨行插入；空槽 = 追加到那一行末尾）。拖动之外的点击 / 键盘后备。 */
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

/** 落到某一行（拖拽的 drop / 空槽点击）：把当前拖动或选中的牌移过去。 */
function arrangeDrop(row, beforeIndex) {
  const pending = state.view ? state.view.pending : null;
  const layout = state.arrange;
  if (state.playback || !pending || pending.kind !== "ARRANGE" || !layout) return;
  const from = layout.dragging !== null && layout.dragging !== undefined
    ? layout.dragging
    : layout.selected;
  if (from === null || from === undefined) return;
  arrangeMoveTo(from, row, beforeIndex);
  layout.dragging = null;
  layout.selected = null;
  renderPending(state.view);
}

/** 排列面里一张**被观看的牌**：可拖、可点选（点选后点目标位置 = 移动）。 */
function arrangeCardNode(layout, index) {
  const card = layout.cards[index];
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "card arrange-card";
  chip.setAttribute("data-card-index", String(index));
  chip.setAttribute("data-row", arrangeRowOf(layout, index));
  if (layout.selected === index) chip.classList.add("sel");
  chip.draggable = true;
  chip.innerHTML = `<span class="n">${cardName(card)}</span><span class="s">${card.suit || ""}${card.rank ?? ""}</span>`;
  chip.addEventListener("click", () => arrangePick(index));
  chip.addEventListener("dragstart", (event) => {
    if (state.playback) return;
    layout.dragging = index;
    if (event && event.dataTransfer && event.dataTransfer.setData) {
      event.dataTransfer.setData("text/plain", String(index));
    }
  });
  chip.addEventListener("dragend", () => { layout.dragging = null; });
  chip.addEventListener("dragover", (event) => {
    if (event && event.preventDefault) event.preventDefault();
  });
  chip.addEventListener("drop", (event) => {
    if (event && event.preventDefault) event.preventDefault();
    const row = arrangeRowOf(layout, index);
    const from = layout.dragging !== null && layout.dragging !== undefined
      ? layout.dragging
      : layout.selected;
    if (from !== null && from !== undefined && row !== null) arrangeMoveTo(from, row, index);
    layout.dragging = null;
    layout.selected = null;
    renderPending(state.view);
  });
  return chip;
}

/** 排列面的一个空槽：点 / 拖到它 = 追加到这一行的末尾。 */
function arrangeSlotNode(row) {
  const chip = document.createElement("button");
  chip.type = "button";
  chip.className = "card arrange-slot";
  chip.setAttribute("data-empty", "1");
  chip.setAttribute("data-row", row);
  chip.textContent = "空槽";
  chip.addEventListener("click", () => arrangeDrop(row, null));
  chip.addEventListener("dragover", (event) => {
    if (event && event.preventDefault) event.preventDefault();
  });
  chip.addEventListener("drop", (event) => {
    if (event && event.preventDefault) event.preventDefault();
    arrangeDrop(row, null);
  });
  return chip;
}

/** 双行容器：标签行（牌堆顶 / 牌堆底）+ 牌 / 空槽行；整行也是拖拽落点（追加到行末）。 */
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
    if (event && event.preventDefault) event.preventDefault();
    row.classList.add("drop");
  });
  row.addEventListener("dragleave", () => row.classList.remove("drop"));
  row.addEventListener("drop", (event) => {
    if (event && event.preventDefault) event.preventDefault();
    row.classList.remove("drop");
    arrangeDrop(name, null);
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
  const top = arrangeRowNode("top", "牌堆顶", "（左 = 最上）");
  const bottom = arrangeRowNode(
    "bottom",
    "牌堆底",
    layout.free ? "（左起接在现有牌堆底之下）" : `（固定 ${layout.fixed.top} 顶 / ${layout.fixed.bottom} 底）`,
  );
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
    hint.textContent = layout.free
      ? "拖动卡牌在牌堆顶 / 牌堆底之间切换，行内拖动排序；也可先点一张牌再点目标位置。"
      : "固定切分：只能在上行 / 下行内排序（跨行移动会改变服务端声明的张数）。";
  }
}
