/* SGS 前端 · 剧本整备面 + 结算 / 选择弹窗 + 事件 / 商店 / 问号揭晓三面（M7 票 22）。
 *
 * 依据 **spec §4「整备面 + 结算 / 选择弹窗 + 商店 / 事件 / 问号」**、**票 05 UI-3 / UI-4**、
 * **票 02 §1–§6** 的成长层。四个面全住在**棋盘视图**里（`#view-script-board`），因为它们的
 * 判据都是同一份棋盘体（票 19 的 `_board`）：`run{coins, backpack, relics}`、
 * `pending{node_id, spec}`、`catalog`（一件东西的 名字 / 稀有度 / 单价）。
 *
 * 三条纪律（评审会按它们读代码）：
 *
 * 1. **前端不算价、不记账**：铜钱（`run.coins`）、单价（`catalog[id].price`）、折价
 *    （服务端结算事件 `PickOffered.coins`）、商品是否卖得出去（服务端 400/409）全部印服务端
 *    给的值/原话。前端只做两件显示层的事：把 `run.backpack.slots` 画成 24 格、把数字原样写。
 *    唯一一处比较是"铜钱 < 价格 ⇒ 置灰"这类**提示**，真正的判据仍在结算端点（点击照样发请求）。
 * 2. **结算一律弹窗**（三选一 / 遗物 / 结果），走票 20 那**一个**模态壳
 *    （`script-lobby.js:scriptConfirm` + `#script-confirm`），不新造第二套模态。
 *    Boss「失去 4 条出战技能」是**规则效果**：它自动落、**没有确认**——界面上只有一块
 *    只读的 `.script-lose`（`data-auto="1"`、没有按钮），结算请求里也没有任何"确认"字段。
 * 3. **编辑只在节点之间**：`pending` 非空时整备面**不接编辑入口**（不挂 click / dragstart、
 *    「丢弃」整键收起），不是"点了无效"。判据是服务端棋盘体，前端不判断自己在不在节点里。
 *
 * 事件 / 商店 / 问号房是三张**面**（票面写的是"面"，不是弹窗）：文本 + 选项 / 6 技能 + 2 遗物
 * 的货架 / 揭晓了哪个内层房间。三选一与结果才是弹窗。
 *
 * 位置纪律：新节点在回放视图**之后**（`index.html`）；注释里不写那两个 `main` 的 id 字面量。
 */
"use strict";

/* ---------------------------------------------------------------- 常量 */

/** 背包 24 格（服务端 `BACKPACK_SLOTS`）；以下三行是票 22 的硬几何判据。 */
const SCRIPT_BACKPACK_SLOTS = 24;
const SCRIPT_LOADOUT_COLS = 8;
const SCRIPT_LOADOUT_ROWS = 3;

/** 遗物栏上限（服务端 `MAX_RELICS`）。 */
const SCRIPT_RELIC_SLOTS = 6;

/** 一件东西的类别标签（id → 中文只住显示层；类别本身由服务端 `catalog[id].kind` 给）。 */
const SCRIPT_ITEM_KIND_LABELS = { skill: "技能", relic: "遗物" };

/** 结算结果里铜钱来由的中文（服务端 `CoinReason` 的四个线上取值 + 折价）。 */
const SCRIPT_COIN_REASON_LABELS = {
  rewards: "战斗奖励",
  boss: "Boss 固定奖励",
  extra: "房间追加奖励",
  event: "事件",
  convert: "折价",
};

/** 整轮结束的三种原因（服务端 `RunEnded.reason`）→ 结果页的标题。 */
const SCRIPT_RUN_END_LABELS = {
  cleared: "通关",
  story_loss: "剧情战败（整轮结束）",
  boss_loss: "Boss 战败（整轮结束）",
};

/* ---------------------------------------------------------------- 状态 */

/** 票 22 的本地态（整备面选中 / 拖拽起点 / 商店购物车 / 打开中的结算流程）。
 *
 * 全部是**显示态**：没有任何一格是规则判据（能不能编辑读棋盘体、能不能买由服务端判）。
 * `flow` = 打开中的结算流程：`{kind, spec, room, boss, rounds, picks, round, stage, option,
 * purchases, catalog}`；`catalog` 是**开流程那一刻**的目录行——结果弹窗要显示没选中的候选
 * 名字（结算后的棋盘体里已经没有它们了）。
 */
function scriptLoadoutState() {
  if (!state.script.loadout) {
    state.script.loadout = {
      selected: null,
      drag: null,
      cart: [],
      flow: null,
      revealOpen: false,
    };
  }
  return state.script.loadout;
}

/** 进了一个**新节点**（或换了棋盘）时复位节点内的显示态：揭晓面从"揭晓"这一步重新开始。 */
function scriptLoadoutResetNodeState() {
  const loot = scriptLoadoutState();
  loot.selected = null;
  loot.drag = null;
  loot.cart = [];
  loot.flow = null;
  loot.revealOpen = false;
  return loot;
}

/* ------------------------------------------------------------ 棋盘体读数 */

function scriptLoadoutBoard() {
  return state.script.board || null;
}

function scriptLoadoutRun() {
  const board = scriptLoadoutBoard();
  return (board && board.run) || null;
}

/** 当前节点（服务端棋盘体的 `pending`；`node_id` 是 `<layer_id>/<node_id>`）。 */
function scriptLoadoutPending() {
  const board = scriptLoadoutBoard();
  return (board && board.pending) || null;
}

/** `pending` 对应的那个棋盘节点（画房间名 / 房间类型用）。 */
function scriptLoadoutPendingNode() {
  const board = scriptLoadoutBoard();
  const pending = scriptLoadoutPending();
  if (!board || !pending) return null;
  const tail = String(pending.node_id || "").split("/").pop();
  return (board.nodes || []).find((node) => node.id === tail) || null;
}

function scriptLoadoutSpec() {
  const pending = scriptLoadoutPending();
  return (pending && pending.spec) || null;
}

function scriptLoadoutRoom() {
  const node = scriptLoadoutPendingNode();
  return (node && node.room) || null;
}

/** 揭晓后的内层房间（外层 `room.revealed`；服务端只在待结算的问号节点上下发它）。 */
function scriptLoadoutInnerRoom() {
  const room = scriptLoadoutRoom();
  if (!room) return null;
  return room.revealed || room;
}

/** 编辑窗口 = **节点之间**（`pending` 为空）。这是本文件唯一的可编辑判据。 */
function scriptLoadoutEditable() {
  return Boolean(scriptLoadoutBoard() && scriptLoadoutRun() && !scriptLoadoutPending());
}

/* ------------------------------------------------------------ 目录行 / 槽位 */

/** 棋盘体的目录行（服务端算好的 名字 / 类别 / 稀有度 / 单价）。 */
function scriptLoadoutCatalog() {
  const board = scriptLoadoutBoard();
  return (board && board.catalog) || {};
}

/** 一件东西的显示行；服务端没给（表外 / 老载荷）⇒ 原样印 id、价格留空（**不编价格**）。 */
function scriptItemRow(itemId, catalog) {
  const rows = catalog || scriptLoadoutCatalog();
  const raw = (itemId && rows[itemId]) || null;
  if (!raw) {
    return {
      id: String(itemId || ""),
      name: String(itemId || ""),
      kind: "skill",
      rarity: null,
      price: null,
    };
  }
  return {
    id: String(itemId),
    name: String(raw.name || itemId),
    kind: raw.kind === "relic" ? "relic" : "skill",
    rarity: raw.rarity || null,
    price: Number.isInteger(raw.price) ? raw.price : null,
  };
}

/** 24 格内容（长度恒 24：`None` 补位，格数就是位置）。 */
function scriptLoadoutSlots() {
  const run = scriptLoadoutRun();
  const raw = (run && run.backpack && run.backpack.slots) || [];
  const slots = [];
  for (let index = 0; index < SCRIPT_BACKPACK_SLOTS; index += 1) {
    slots.push(raw[index] === undefined ? null : raw[index]);
  }
  return slots;
}

/** 出战槽的格号（缺省前 4 格；服务端说哪 4 格就是哪 4 格）。 */
function scriptLoadoutEquipped() {
  const run = scriptLoadoutRun();
  const raw = (run && run.backpack && run.backpack.equipped_slots) || [0, 1, 2, 3];
  return raw
    .map((value) => Number(value))
    .filter((value) => Number.isInteger(value) && value >= 0 && value < SCRIPT_BACKPACK_SLOTS);
}

/** 出战技能（Boss 结算第一段"自动失去"要念出来的那几条）。 */
function scriptLoadoutEquippedSkills() {
  const slots = scriptLoadoutSlots();
  return scriptLoadoutEquipped()
    .map((index) => slots[index])
    .filter((skillId) => Boolean(skillId));
}

function scriptLoadoutRelics() {
  const run = scriptLoadoutRun();
  return ((run && run.relics) || []).slice();
}

/** 背包满没满（服务端 24 格全非空；"选择将折成铜钱"的显示判据）。 */
function scriptBackpackFull() {
  return scriptLoadoutSlots().every((skillId) => Boolean(skillId));
}

function scriptRelicFull() {
  return scriptLoadoutRelics().length >= SCRIPT_RELIC_SLOTS;
}

function scriptOwnsSkill(skillId) {
  return scriptLoadoutSlots().indexOf(skillId) >= 0;
}

function scriptOwnsRelic(relicId) {
  return scriptLoadoutRelics().indexOf(relicId) >= 0;
}

/** 房间类型的中文（`script-board.js` 的同一张表：房源类型只有一份文案）。 */
function scriptFaceKindLabel(kind) {
  return SCRIPT_GRAPH_KIND_LABELS[kind] || String(kind || "未知");
}

/* ---------------------------------------------------------------- DOM 小工具 */

function scriptNode(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** 一行带 `data-*` 的按钮（选项 / 货架 / 候选共用一处 setAttribute 拼装）。 */
function scriptDataButton(className, dataset, text) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  for (const [name, value] of Object.entries(dataset || {})) {
    if (value !== undefined && value !== null && value !== "") button.setAttribute(name, String(value));
  }
  if (text !== undefined && text !== null) button.textContent = String(text);
  return button;
}

/** 一件东西在货架 / 候选里的消费提示（价格只印服务端给的那个数）。 */
function scriptPriceText(row) {
  if (!row || row.price === null) return "";
  return `${row.price} 铜钱`;
}

/* ---------------------------------------------------------------- 整备面 */

function scriptLoadoutHint(text) {
  const node = $("script-loadout-hint");
  if (node) node.textContent = text || "";
  return text || "";
}

/** 一格背包：`data-slot` / `data-skill-id` / `data-equipped` / `data-empty`。
 *
 * **可编辑时才接编辑入口**（click / dragstart / drop）：节点内连监听都不挂——票 22 要的是
 * "编辑入口不存在"，不是"点了无效"。
 *
 * 票 06：`data-skill` = 技能详情浮层的宿主挂点（**只加属性**，标签 / id 不动；浮层在
 * `document` 上委托，不给这一格挂 `mouseenter` / `mouseleave`）。
 */
function scriptLoadoutSlotNode(index, skillId, equipped, editable, selected) {
  const slot = document.createElement("button");
  slot.type = "button";
  slot.className = "script-slot";
  slot.setAttribute("data-slot", String(index));
  slot.setAttribute("data-equipped", equipped ? "1" : "0");
  if (skillId) slot.setAttribute("data-skill-id", String(skillId));
  if (skillId) slot.setAttribute("data-skill", String(skillId));
  if (!skillId) slot.setAttribute("data-empty", "1");
  if (selected) slot.classList.add("on");
  const badge = scriptNode("span", "script-slot-badge", equipped ? "出战" : "");
  const name = scriptNode("span", "script-slot-name", skillId ? scriptItemRow(skillId).name : "空");
  slot.appendChild(badge);
  slot.appendChild(name);
  if (editable) {
    slot.setAttribute("draggable", "true");
    slot.addEventListener("click", () => scriptLoadoutClickSlot(index));
    slot.addEventListener("dragstart", (event) => scriptLoadoutDragStart(event, index));
    slot.addEventListener("dragover", (event) => {
      if (event && typeof event.preventDefault === "function") event.preventDefault();
    });
    slot.addEventListener("drop", (event) => scriptLoadoutDrop(event, index));
  }
  return slot;
}

/** 画整备面：铜钱 HUD / 背包 24 格（3×8，出战槽标「出战」）/ 遗物栏（≤6）/ 编辑提示。 */
function scriptLoadoutRender() {
  // 票 06：格子要重铺 ⇒ 浮层先收（否则它挂着一个已经不存在的宿主的内容）。
  if (typeof skillTipHide === "function") skillTipHide();
  const run = scriptLoadoutRun();
  const grid = $("script-loadout-grid");
  const relicsBox = $("script-loadout-relics");
  const loot = scriptLoadoutState();
  const editable = scriptLoadoutEditable();
  if (!run) {
    if (grid) {
      grid.innerHTML = "";
    }
    if (relicsBox) relicsBox.innerHTML = "";
    const blank = ["script-loadout-coins", "script-loadout-backpack", "script-loadout-relic-count"];
    for (const id of blank) {
      const node = $(id);
      if (node) node.textContent = "";
    }
    const lock = $("script-loadout-lock");
    if (lock) lock.hidden = true;
    scriptLoadoutHint("");
    return null;
  }
  const slots = scriptLoadoutSlots();
  const equipped = scriptLoadoutEquipped();
  const used = slots.filter((skillId) => Boolean(skillId)).length;
  const relics = scriptLoadoutRelics();
  const coins = Number(run.coins) || 0;

  const coinsNode = $("script-loadout-coins");
  if (coinsNode) coinsNode.textContent = `铜钱 ${coins}`;
  const countNode = $("script-loadout-backpack");
  if (countNode) {
    countNode.textContent = scriptBackpackFull()
      ? `背包 ${used}/${SCRIPT_BACKPACK_SLOTS} · 已满（三选一 / 商店的技能会折成铜钱）`
      : `背包 ${used}/${SCRIPT_BACKPACK_SLOTS}`;
  }
  const relicCount = $("script-loadout-relic-count");
  if (relicCount) relicCount.textContent = `遗物 ${relics.length}/${SCRIPT_RELIC_SLOTS}`;

  if (grid) {
    grid.innerHTML = "";
    for (let index = 0; index < SCRIPT_BACKPACK_SLOTS; index += 1) {
      grid.appendChild(scriptLoadoutSlotNode(
        index,
        slots[index],
        equipped.indexOf(index) >= 0,
        editable,
        loot.selected === index,
      ));
    }
  }
  if (relicsBox) {
    relicsBox.innerHTML = "";
    for (let index = 0; index < SCRIPT_RELIC_SLOTS; index += 1) {
      const relicId = relics[index] || null;
      const cell = scriptNode("div", "script-relic", relicId ? scriptItemRow(relicId).name : "空");
      cell.setAttribute("data-relic-slot", String(index));
      if (relicId) cell.setAttribute("data-relic-id", String(relicId));
      // 票 06：遗物格也是浮层宿主（服务端目录行里遗物同样有裁过的 `desc`）。
      if (relicId) cell.setAttribute("data-skill", String(relicId));
      else cell.setAttribute("data-empty", "1");
      relicsBox.appendChild(cell);
    }
  }
  const relicHint = $("script-loadout-relic-hint");
  if (relicHint) {
    relicHint.textContent = relics.length ? "每场战斗开局自动生效（独立于背包，不算武将牌上的技能）"
      : "还没有遗物（商店 / 事件 / Boss 给；上限 6）";
  }

  const discard = $("btn-script-loadout-discard");
  if (discard) {
    // 节点内「丢弃」整键收起：入口不存在（不是置灰）。
    discard.hidden = !editable;
    discard.disabled = !editable || !Number.isInteger(loot.selected)
      || !slots[loot.selected];
  }
  const lock = $("script-loadout-lock");
  if (lock) {
    const node = scriptLoadoutPendingNode();
    lock.hidden = editable;
    lock.textContent = editable ? ""
      : `节点「${(node && node.room && node.room.name) || ""}」内不可编辑背包：`
        + "结算之后回到棋盘再整理（服务端拒绝节点内编辑）。";
  }
  scriptLoadoutHint(editable
    ? "点两格 = 交换 / 移动到空格（拖动也行）；丢弃不返还铜钱。"
    : "这个节点结算之后才回到棋盘（整备面此刻只读）。");
  return null;
}

/** 背包落盘：`PUT …/<run_id>/backpack`（**立即写**、幂等：内容没变就不发）。
 *
 * `slots` / `equipped_slots` 原样上行，回包是**整份棋盘体**（与结算同一个出口）。
 */
async function scriptLoadoutPut(slots, equippedSlots) {
  const board = scriptLoadoutBoard();
  const run = scriptLoadoutRun();
  if (!board || !run || state.script.busy) return null;
  if (!scriptLoadoutEditable()) return null;
  const runId = board.run_id || state.script.runId;
  const current = (run.backpack && run.backpack.slots) || [];
  const same = slots.every((value, index) => (current[index] || null) === (value || null));
  if (same) {
    scriptLoadoutRender();
    return board;
  }
  state.script.busy = true;
  let body = null;
  let failure = null;
  try {
    body = await api(`/api/script-runs/${encodeURIComponent(runId)}/backpack`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: board.revision,
        slots,
        equipped_slots: equippedSlots,
      }),
    });
  } catch (error) {
    failure = error;
  } finally {
    state.script.busy = false;
  }
  if (!body) {
    scriptLoadoutRender();
    scriptBoardShowError((failure && failure.body) || { reason: failure && failure.message });
    scriptLoadoutHint("背包没写进去：上面那行是服务端给的原因。");
    if (failure && failure.body && typeof failure.body.status === "string") {
      scriptApplySave(failure.body);
    }
    return null;
  }
  scriptBoardShow(body);
  scriptLoadoutHint(`背包已保存（revision ${body.revision}；丢弃不返还铜钱）。`);
  return body;
}

/** 两格交换 / 移动（目标空 = 移动）：写一次 `PUT …/backpack`。 */
function scriptLoadoutSwap(from, to) {
  if (!scriptLoadoutEditable()) return null;
  if (!Number.isInteger(from) || !Number.isInteger(to) || from === to) return null;
  const slots = scriptLoadoutSlots();
  if (from < 0 || from >= slots.length || to < 0 || to >= slots.length) return null;
  const swapped = slots.slice();
  const held = swapped[from];
  swapped[from] = swapped[to];
  swapped[to] = held;
  scriptLoadoutState().selected = null;
  return scriptLoadoutPut(swapped, scriptLoadoutEquipped());
}

function scriptLoadoutDragStart(event, index) {
  if (!scriptLoadoutEditable()) return null;
  const loot = scriptLoadoutState();
  loot.drag = index;
  if (event && event.dataTransfer && typeof event.dataTransfer.setData === "function") {
    event.dataTransfer.setData("text/plain", String(index));
  }
  return index;
}

function scriptLoadoutDrop(event, index) {
  if (event && typeof event.preventDefault === "function") event.preventDefault();
  if (!scriptLoadoutEditable()) return null;
  const loot = scriptLoadoutState();
  const from = Number.isInteger(loot.drag) ? loot.drag : -1;
  loot.drag = null;
  if (from < 0 || from === index) return null;
  return scriptLoadoutSwap(from, index);
}

/** 点一格：第一次 = 选中，第二次 = 与选中的那格交换 / 移动到空格，再点自己 = 取消。 */
function scriptLoadoutClickSlot(index) {
  if (!scriptLoadoutEditable()) return null;
  const loot = scriptLoadoutState();
  const slots = scriptLoadoutSlots();
  if (loot.selected === null) {
    if (!slots[index]) {
      scriptLoadoutHint("空格：先选中一个装着技能的格子，再点它 = 移动过去。");
      return null;
    }
    loot.selected = index;
    scriptLoadoutRender();
    return null;
  }
  if (loot.selected === index) {
    loot.selected = null;
    scriptLoadoutRender();
    return null;
  }
  const from = loot.selected;
  loot.selected = null;
  return scriptLoadoutSwap(from, index);
}

/** 「丢弃选中」：**永久**丢弃（不返还铜钱——请求里根本没有铜钱字段）。 */
function scriptLoadoutDiscard() {
  if (!scriptLoadoutEditable()) return null;
  const loot = scriptLoadoutState();
  const index = loot.selected;
  if (!Number.isInteger(index)) return null;
  const slots = scriptLoadoutSlots();
  if (!slots[index]) return null;
  const dropped = slots[index];
  slots[index] = null;
  loot.selected = null;
  return scriptLoadoutPut(slots, scriptLoadoutEquipped()).then((body) => {
    if (body) scriptLoadoutHint(`已丢弃 ${scriptItemRow(dropped).name}（不返还铜钱）。`);
    return body;
  });
}

/* ------------------------------------------------------------ 当前节点的面 */

/** 「当前节点的面」的容器：标题 / 元信息 / 文案 / 列表 / 动作。
 *
 * 三面（事件 / 商店 / 问号揭晓）+ 战斗的"该结界"全走它；面的可见性由 `pending` 决定。
 */
function scriptFaceClear() {
  const list = $("script-face-list");
  const actions = $("script-face-actions");
  if (list) list.innerHTML = "";
  if (actions) actions.innerHTML = "";
  const title = $("script-face-title");
  if (title) title.textContent = "";
  const meta = $("script-face-meta");
  if (meta) meta.textContent = "";
  const text = $("script-face-text");
  if (text) {
    text.hidden = true;
    text.textContent = "";
  }
  return { list, actions };
}

/** 揭晓后的内层 spec（问号房在外层 `unknown` 里；其余房间就是它自己）。 */
function scriptFlowSpec(spec) {
  const source = spec || scriptLoadoutSpec();
  if (!source) return null;
  if (source.kind !== "unknown") return source;
  return (source.revealed && source.revealed.spec) || null;
}

/** 结算指令的形状由**内层房间**的类型决定（服务端 `settle_node` 也是这么解包的）。 */
function scriptFlowKind(spec) {
  const inner = scriptFlowSpec(spec);
  return (inner && inner.kind) || null;
}

/** 事件房的**抽中**那条事件（`room.events` 是作者候选，`spec.event_id` 才是抽出来的）。 */
function scriptFaceEvent(spec, room) {
  const events = (room && room.events) || [];
  const wanted = (spec && spec.event_id) || null;
  return events.find((event) => event.id === wanted) || null;
}

/** 一条事件后果的显示文案（id 换成名字；查不到就印 id——不编名字）。 */
function scriptFaceEffectText(effect) {
  const value = effect || {};
  if (value.kind === "coins") {
    const amount = Number(value.amount) || 0;
    return `铜钱 ${amount >= 0 ? "+" : "−"}${Math.abs(amount)}`;
  }
  if (value.kind === "set_flags") return `设置标签：${(value.flags || []).join("、")}`;
  if (value.kind === "grant_skill") return `获得技能：${scriptItemRow(value.skill).name}`;
  if (value.kind === "grant_relic") return `获得遗物：${scriptItemRow(value.relic).name}`;
  return String(value.kind || "未知后果");
}

/** 事件选项的条件文案（与边条件**同一个**实现：`script-board.js:scriptGraphEdgeLabel`）。 */
function scriptFaceRequirementText(option) {
  return scriptGraphEdgeLabel({ requires: (option && option.requires) || {} });
}

/** 画事件面：事件全文 + 选项（条件与后果逐条写出来）。点选项 = 这一问的答案。 */
function scriptFaceRenderEvent(spec) {
  const room = scriptLoadoutInnerRoom();
  const event = scriptFaceEvent(spec, room);
  const parts = scriptFaceClear();
  const title = $("script-face-title");
  if (title) title.textContent = (event && event.name) || spec.event_id || "事件";
  const meta = $("script-face-meta");
  if (meta) meta.textContent = `事件 · 房间「${(room && room.name) || ""}」`;
  const text = $("script-face-text");
  if (text) {
    text.hidden = false;
    text.textContent = (event && event.text) || "";
  }
  if (!parts.list) return null;
  if (!event) {
    parts.list.appendChild(scriptNode("div", "script-face-row", "房间里没有这次抽出的事件。"));
    return null;
  }
  (event.options || []).forEach((option, index) => {
    const row = scriptDataButton("script-face-row", { "data-option": index }, "");
    row.appendChild(scriptNode("span", "script-face-item", option.text || `选项 ${index + 1}`));
    const requires = scriptFaceRequirementText(option);
    // 条件只**写出来**，置不置灰不归前端判（服务端 `requirement_met` 说了算）。
    if (requires) row.appendChild(scriptNode("span", "script-face-req", requires));
    for (const effect of option.effects || []) {
      const note = scriptNode("span", "script-face-note", scriptFaceEffectText(effect));
      // 票 06：**给技能 / 给遗物**的那条后果是浮层宿主（"事件后果行"这一面）；
      // 别的后果（铜钱 / 标签）没有 id，也就不带 `data-skill`。
      const granted = effect.kind === "grant_skill" ? effect.skill
        : effect.kind === "grant_relic" ? effect.relic : null;
      if (granted) note.setAttribute("data-skill", String(granted));
      row.appendChild(note);
    }
    row.addEventListener("click", () => scriptFaceChooseEvent(index));
    parts.list.appendChild(row);
  });
  return null;
}

/** 画商店面：6 技能 + 2 遗物（已拥有的**不出现**、满包的技能置灰、售完不补货）。
 *
 * 价格是服务端 `catalog[id].price` 那个数；库存里不足 6 / 2 时"有几张给几张"（服务端事实）。
 */
function scriptFaceRenderShop(spec) {
  const parts = scriptFaceClear();
  const room = scriptLoadoutInnerRoom();
  const stock = (spec && spec.stock) || { skills: [], relics: [] };
  const loot = scriptLoadoutState();
  const title = $("script-face-title");
  if (title) title.textContent = (room && room.name) || "商店";
  const meta = $("script-face-meta");
  if (meta) meta.textContent = "商店 · 售完即空、不补货";
  const text = $("script-face-text");
  if (text) {
    text.hidden = false;
    text.textContent = "每次 6 技能 + 2 遗物；已拥有的不卖，背包满了技能买不了。";
  }
  const full = scriptBackpackFull();
  const relicFull = scriptRelicFull();
  const addItem = (itemId, kind, blocked, why) => {
    const row = scriptItemRow(itemId);
    // 已拥有：**不出现**在货架上（票 22 的原话：已拥有不出现）。
    if (kind === "relic" ? scriptOwnsRelic(itemId) : scriptOwnsSkill(itemId)) return;
    if (!parts.list) return;
    const inCart = loot.cart.indexOf(itemId) >= 0;
    const button = scriptDataButton("script-face-row", {
      "data-item-id": itemId,
      // 票 06：货架行是浮层宿主（`data-skill` 只加属性，标签 / `data-item-id` 不动）。
      "data-skill": itemId,
      "data-kind": row.kind,
      "data-price": row.price === null ? "" : row.price,
      "data-full": blocked ? "1" : "0",
    }, "");
    button.appendChild(scriptNode("span", "script-face-item", row.name));
    button.appendChild(scriptNode("span", "script-face-note", SCRIPT_ITEM_KIND_LABELS[row.kind] || ""));
    if (scriptPriceText(row)) button.appendChild(scriptNode("span", "script-face-price", scriptPriceText(row)));
    if (blocked && why) button.appendChild(scriptNode("span", "script-face-note", why));
    if (blocked) button.disabled = true; // 满包 / 遗物栏满：**置灰**（点不动，不是点了没用）
    if (inCart) button.classList.add("on");
    button.setAttribute("aria-pressed", inCart ? "true" : "false");
    button.addEventListener("click", () => scriptFaceToggleCart(itemId));
    parts.list.appendChild(button);
  };
  for (const skillId of stock.skills || []) {
    addItem(skillId, "skill", full, full ? "背包已满（买不了）" : "");
  }
  for (const relicId of stock.relics || []) {
    addItem(relicId, "relic", relicFull, relicFull ? "遗物栏已满（买不了）" : "");
  }
  if (parts.list && !parts.list.children.length) {
    parts.list.appendChild(scriptNode("div", "script-face-row", "货架上没有可卖的东西。"));
  }
  if (parts.list && (!stock.relics || !stock.relics.length)) {
    parts.list.appendChild(scriptNode(
      "div", "script-face-note", "这次没有遗物可卖（价值表的遗物段还是空的）。"));
  }
  if (parts.actions) {
    const go = scriptDataButton("primary", {}, "");
    go.textContent = loot.cart.length ? `结算（买 ${loot.cart.length} 件）` : "结算（不买，离开）";
    go.addEventListener("click", () => scriptFaceSettleShop());
    parts.actions.appendChild(go);
  }
  return null;
}

/** 问号房：揭晓了哪个内层房间（进入即揭晓、结果已进档 ⇒ 重进/刷新都是同一行）。 */
function scriptFaceRenderReveal(spec) {
  const innerRoom = scriptLoadoutInnerRoom();
  const revealed = (spec && spec.revealed) || {};
  const innerSpec = revealed.spec || null;
  const parts = scriptFaceClear();
  const title = $("script-face-title");
  if (title) title.textContent = "问号房揭晓";
  const meta = $("script-face-meta");
  if (meta) meta.textContent = "进入即揭晓（已进存档，重进不重抽）";
  const text = $("script-face-text");
  if (text) {
    text.hidden = false;
    text.textContent = `揭晓：${scriptFaceKindLabel((innerRoom && innerRoom.kind) || (innerSpec && innerSpec.kind))}`
      + `「${(innerRoom && innerRoom.name) || revealed.room || "未知"}」`;
  }
  if (parts.list) {
    const row = scriptNode("div", "script-face-row", "");
    row.setAttribute("data-reveal", "1");
    row.setAttribute("data-room", String(revealed.room || ""));
    row.appendChild(scriptNode("span", "script-face-item", "按内层房间的流程走"));
    parts.list.appendChild(row);
  }
  if (parts.actions) {
    const go = scriptDataButton("primary", {}, "看看这个房间");
    go.addEventListener("click", () => scriptFaceRevealInner());
    parts.actions.appendChild(go);
  }
  return null;
}

/** 战斗（含揭晓出来的战斗房）：这一场在牌桌上打，结算 = 三选一的流程。
 *
 * 续档 / 刷新之后棋盘体**没有** `session_id`（会话 id 不进档，票 04 §4）：这时先给一个
 * "重开这一场"的入口——重进同一节点不重抽 `spec`、只换一个新会话（票 19），打完再结算。
 */
function scriptFaceRenderBattle(spec) {
  const room = scriptLoadoutInnerRoom();
  const board = scriptLoadoutBoard();
  const parts = scriptFaceClear();
  const title = $("script-face-title");
  if (title) title.textContent = (room && room.name) || "战斗";
  const meta = $("script-face-meta");
  if (meta) meta.textContent = `战斗${room && room.is_boss ? " · Boss" : ""} · 在牌桌上打`;
  const text = $("script-face-text");
  if (board && board.session_id) {
    if (text) {
      text.hidden = false;
      text.textContent = "这一场已经在牌桌上开好了；打完回棋盘，这里按服务端判的胜负结算。";
    }
    const go = scriptDataButton("primary", {}, "结算（三选一）");
    go.addEventListener("click", () => scriptSettleOpenBattle());
    parts.actions.appendChild(go);
    return null;
  }
  if (text) {
    text.hidden = false;
    text.textContent = "这一场还没开到牌桌上（续档 / 刷新后会话就没了）：先重开这一场（不重抽），打完再结算。";
  }
  const reopen = scriptDataButton("primary", {}, "重开这一场");
  reopen.addEventListener("click", () => scriptFaceReopenBattle());
  parts.actions.appendChild(reopen);
  return null;
}

/** 重开待结算的战斗：重进**同一个**节点（服务端不重抽 `spec`、换一个新会话）。 */
function scriptFaceReopenBattle() {
  const node = scriptLoadoutPendingNode();
  if (!node) return null;
  return scriptBoardEnterNode(node);
}

/** 画当前节点的面（判据是 `pending.spec`；问号房先揭晓再按内层走）。
 *
 * ui-one-screen 票 05 起它还管**窗的开关**：有 `pending.spec` ⇒ 这块面住在 M 窗里
 * （`script-board.js:scriptBoardFaceWindowOpen`，进入节点就开）；`pending` 落了 ⇒ 收窗、
 * 两块内容回寄存格。这里只是"谁该在窗里"的唯一裁决点——窗壳与单槽位归 `panel-window.js`。
 */
function scriptFaceRender() {
  const face = $("script-face");
  const spec = scriptLoadoutSpec();
  if (!face) return null;
  // 先收回寄存格再画：窗里那块若被别的模态顶掉过，会变成游离节点（id 查不到 ⇒ 渲染静默失效）。
  if (typeof restashBoardWindows === "function") restashBoardWindows();
  // 票 06：面的行重铺之后浮层不能还挂着旧宿主的内容（`restashBoardWindows` 已经收过一次，
  // 这里再收一次是为了"面的行被原地重画"那条路——收是幂等的）。
  if (typeof skillTipHide === "function") skillTipHide();
  if (!spec) {
    face.hidden = true;
    face.removeAttribute("data-face");
    scriptFaceClear();
    if (typeof scriptBoardWindowKind === "function" && scriptBoardWindowKind() === "script-face") {
      panelClose();
    }
    if (typeof restashBoardWindows === "function") restashBoardWindows();
    return null;
  }
  const loot = scriptLoadoutState();
  const innerKind = scriptFlowKind(spec);
  face.hidden = false;
  face.setAttribute("data-face", spec.kind === "unknown" && !loot.revealOpen
    ? "reveal"
    : String(innerKind || spec.kind));
  if (spec.kind === "unknown" && !loot.revealOpen) scriptFaceRenderReveal(spec);
  else if (innerKind === "event") scriptFaceRenderEvent(scriptFlowSpec(spec));
  else if (innerKind === "shop") scriptFaceRenderShop(scriptFlowSpec(spec));
  else if (innerKind === "battle") scriptFaceRenderBattle(scriptFlowSpec(spec));
  // 内容画好之后再开窗（进节点即开；换成另一块面时窗里的内容已经是最新的）。
  if (typeof scriptBoardFaceWindowOpen === "function") scriptBoardFaceWindowOpen();
  return null;
}

/** 揭晓面 → 内层面（"按内层房间的流程走"）。 */
function scriptFaceRevealInner() {
  scriptLoadoutState().revealOpen = true;
  return scriptFaceRender();
}

/* ------------------------------------------------------------ 事件 / 商店动作 */

/** 选一个事件选项 = 这一问的答案（`POST …/settle {option}`），随后出结果弹窗。 */
function scriptFaceChooseEvent(index) {
  const flow = scriptLoadoutState();
  flow.flow = {
    kind: "event",
    spec: scriptFlowSpec(),
    option: index,
    catalog: scriptLoadoutCatalog(),
  };
  return scriptSettleSubmit(flow.flow);
}

/** 货架上点一件 = 收进 / 移出购物车（置灰的件是 `disabled`，根本进不来）。 */
function scriptFaceToggleCart(itemId) {
  const loot = scriptLoadoutState();
  const at = loot.cart.indexOf(itemId);
  if (at >= 0) loot.cart.splice(at, 1);
  else loot.cart.push(itemId);
  const spec = scriptFlowSpec();
  if (spec && spec.kind === "shop") scriptFaceRenderShop(spec);
  else scriptFaceRender();
  return { itemId, cart: loot.cart.slice() };
}

/** 商店结算：`POST …/settle {purchases}`（买的每一件都按服务端价格扣钱）。 */
function scriptFaceSettleShop() {
  const loot = scriptLoadoutState();
  loot.flow = {
    kind: "shop",
    spec: scriptFlowSpec(),
    purchases: loot.cart.slice(),
    catalog: scriptLoadoutCatalog(),
  };
  return scriptSettleSubmit(loot.flow);
}

/* ------------------------------------------------------------ 结算流程（弹窗） */

/** 弹窗正文的一个区块（Boss 自动失去那一段；**没有按钮** = 不是选项）。 */
function scriptLoseBlock() {
  const skills = scriptLoadoutEquippedSkills();
  const box = scriptNode("div", "script-lose", "");
  box.setAttribute("data-auto", "1");
  box.appendChild(scriptNode("div", "", "已自动失去本场 4 条出战技能（规则效果，无需确认）"));
  box.appendChild(scriptNode(
    "div", "dim",
    skills.length
      ? `失去：${skills.map((skillId) => scriptItemRow(skillId).name).join("、")}`
      : "（出战槽是空的，没有可失去的技能）",
  ));
  return box;
}

/** 开一次三选一：Boss 房的第一轮弹窗**顶格**写"已自动失去 4 条"（自动、无确认）。 */
function scriptSettleOpenBattle() {
  const spec = scriptFlowSpec();
  if (!spec || spec.kind !== "battle") return null;
  const room = scriptLoadoutInnerRoom();
  const loot = scriptLoadoutState();
  loot.flow = {
    kind: "battle",
    spec,
    room,
    boss: Boolean(room && room.is_boss),
    rounds: (spec.rewards || []).map((round) => (round || []).slice()),
    picks: [],
    round: 0,
    stage: "pick",
    catalog: scriptLoadoutCatalog(),
  };
  return scriptSettleNextStage();
}

/** 下一段：还有轮次就弹下一轮三选一（**连续弹**），Boss 再走遗物那一段，最后落结算。 */
function scriptSettleNextStage() {
  const flow = scriptLoadoutState().flow;
  if (!flow) return null;
  if (flow.stage === "pick") {
    if (flow.round < flow.rounds.length) return scriptSettlePickDialog(flow);
    // 池空（没有候选的轮次）也占一轮：`null` = 放弃，服务端按规则折价。
    flow.stage = flow.kind === "battle" && flow.boss ? "relic" : "submit";
    return scriptSettleNextStage();
  }
  if (flow.stage === "relic") return scriptSettleRelicDialog(flow);
  return scriptSettleSubmit(flow);
}

function scriptSettlePickDialog(flow) {
  const candidates = flow.rounds[flow.round] || [];
  const body = [];
  if (flow.boss && flow.round === 0) body.push(scriptLoseBlock());
  const full = scriptBackpackFull();
  if (!candidates.length) {
    body.push(scriptNode("div", "script-face-note",
      "这一轮没有候选（池空：服务端按规则折成铜钱，金额在结算结果里给出）。"));
  }
  for (const skillId of candidates) {
    const row = scriptItemRow(skillId, flow.catalog);
    const button = scriptDataButton("script-pick", {
      "data-skill-id": skillId,
      // 票 06：三选一的行也是浮层宿主。
      "data-skill": skillId,
      "data-price": row.price === null ? "" : row.price,
    }, "");
    button.appendChild(scriptNode("span", "script-face-item", row.name));
    button.appendChild(scriptNode("span", "script-face-note",
      SCRIPT_ITEM_KIND_LABELS[row.kind] || ""));
    if (full) {
      // 满包：这一选会被折成铜钱。数字**只**印服务端给的价——前端不乘稀有度。
      button.appendChild(scriptNode("span", "script-pick-price", row.price === null
        ? "背包已满：选择将转为铜钱（金额由服务端结算时给出）"
        : `背包已满：选它 → 折 ${row.price} 铜钱`));
      button.setAttribute("data-full", "1");
    }
    button.addEventListener("click", () => scriptSettleChoose(skillId));
    body.push(button);
  }
  const actions = [];
  if (!candidates.length) {
    actions.push({
      label: "按规则折铜钱",
      primary: true,
      run: () => scriptSettleChoose(null),
    });
  } else {
    actions.push({
      label: "放弃（本轮不选）",
      ghost: true,
      run: () => scriptSettleChoose(null),
    });
  }
  // 借的是同一扇窗（单槽位）：先把棋盘那两块送回寄存格，别让它们被清槽变成游离节点。
  restashBoardWindows(true);
  const buttons = scriptConfirm({
    kind: "pick",
    dialog: "pick",
    title: `三选一（第 ${flow.round + 1}/${flow.rounds.length} 轮）`,
    text: full
      ? "背包已满：选中的技能会按服务端价格折成铜钱（也可以放弃）。"
      : "选一张进背包（空格优先，出战槽先填）。",
    body,
    actions,
  });
  const dialog = $("script-confirm");
  if (dialog) {
    // 烟测读这两格断"多轮 pick k 连续弹"（第 k 轮 / 共几轮），不靠标题文案。
    dialog.setAttribute("data-round", String(flow.round));
    dialog.setAttribute("data-rounds", String(flow.rounds.length));
  }
  return buttons;
}

/** 一轮的选择落进 `flow.picks`（`null` = 放弃）⇒ 关窗、弹下一轮 / 遗物 / 提交。 */
function scriptSettleChoose(skillId) {
  const flow = scriptLoadoutState().flow;
  if (!flow) return null;
  flow.picks.push(skillId === undefined ? null : skillId);
  flow.round += 1;
  scriptCloseConfirm();
  return scriptSettleNextStage();
}

/** Boss 的遗物那一段（不是选择：服务端结算时自动发 / 无货折铜钱）。 */
function scriptSettleRelicDialog(flow) {
  const relic = (flow.spec && flow.spec.boss_relic) || null;
  const body = [];
  if (relic) {
    const row = scriptItemRow(relic, flow.catalog);
    const item = scriptNode("div", "script-face-row", "");
    item.setAttribute("data-relic-id", String(relic));
    // 票 06：结算弹窗里出现遗物名的地方也是浮层宿主。
    item.setAttribute("data-skill", String(relic));
    item.appendChild(scriptNode("span", "script-face-item", row.name));
    item.appendChild(scriptNode("span", "script-face-note", "遗物"));
    body.push(item);
  } else {
    body.push(scriptNode("div", "script-face-note",
      "这一场没有遗物可发：服务端按规则折成铜钱（金额在结算结果里给出）。"));
  }
  restashBoardWindows(true);
  return scriptConfirm({
    kind: "relic",
    dialog: "relic",
    title: "Boss 结算 · 遗物",
    text: relic ? "Boss 结算第 3 段：这 1 件遗物自动进遗物栏（≤6）。"
      : "Boss 结算第 3 段：无货，按规则折铜钱。",
    body,
    actions: [{
      label: relic ? "领取这件遗物" : "按规则折铜钱",
      primary: true,
      run: () => {
        scriptCloseConfirm();
        flow.stage = "submit";
        return scriptSettleNextStage();
      },
    }],
  });
}

/** 结算结果里一条事件的中文（数字一律是服务端事件里的那个数）。
 *
 * 票 06：**一行只有一个主语**的那几种（pick / relic）带 `data-skill`（浮层宿主）；
 * `skills` / `relics` / `lose_equipped` 这几行是"甲、乙、丙"的并列清单，一个属性装不下
 * 多个 id，而票面明确本票不改行内结构（只加属性）——那几行因此不是宿主。
 */
function scriptSettleEventRow(event, catalog) {
  const kind = event && event.kind;
  const row = scriptNode("div", "script-event-row", "");
  row.setAttribute("data-event", String(kind || ""));
  if (kind === "lose_equipped") {
    const skills = event.skills || [];
    row.textContent = `失去出战技能：${skills.length
      ? skills.map((skillId) => scriptItemRow(skillId, catalog).name).join("、")
      : "（没有可失去的）"}（自动，无需确认）`;
    row.setAttribute("data-auto", "1");
    return row;
  }
  if (kind === "pick") {
    const chosen = event.chosen ? scriptItemRow(event.chosen, catalog).name : "放弃";
    const coins = Number(event.coins) || 0;
    row.textContent = `三选一 第 ${Number(event.round) + 1} 轮：${chosen}`
      + (coins ? `（折 ${coins} 铜钱）` : "");
    // 票 06：结算弹窗里出现技能名的地方 —— 这一行的主语就是那一张，故整行是宿主。
    if (event.chosen) row.setAttribute("data-skill", String(event.chosen));
    if (coins) row.setAttribute("data-coins", String(coins));
    return row;
  }
  if (kind === "relic") {
    const coins = Number(event.coins) || 0;
    row.textContent = event.relic
      ? `遗物：${scriptItemRow(event.relic, catalog).name}`
      : `遗物无货${coins ? `（折 ${coins} 铜钱）` : ""}`;
    if (event.relic) row.setAttribute("data-skill", String(event.relic));
    if (coins) row.setAttribute("data-coins", String(coins));
    return row;
  }
  if (kind === "skills") {
    row.textContent = `获得技能：${(event.skills || []).map((skillId) => scriptItemRow(skillId, catalog).name).join("、")}`;
    return row;
  }
  if (kind === "relics") {
    row.textContent = `获得遗物：${(event.relics || []).map((relicId) => scriptItemRow(relicId, catalog).name).join("、")}`;
    return row;
  }
  if (kind === "flags") {
    row.textContent = `设置标签：${(event.flags || []).join("、")}`;
    return row;
  }
  if (kind === "coins") {
    row.textContent = `铜钱 +${Number(event.amount) || 0}`
      + `（${SCRIPT_COIN_REASON_LABELS[event.reason] || event.reason}）`;
    row.setAttribute("data-coins", String(Number(event.amount) || 0));
    return row;
  }
  if (kind === "coins_lost") {
    row.textContent = `铜钱 −${Number(event.amount) || 0}`;
    row.setAttribute("data-coins", String(Number(event.amount) || 0));
    return row;
  }
  if (kind === "node_closed") {
    row.textContent = `节点关闭（不可重打）：${event.node_id}`;
    return row;
  }
  if (kind === "run_ended") {
    row.textContent = `整轮结束：${SCRIPT_RUN_END_LABELS[event.reason] || event.reason}`;
    return row;
  }
  row.textContent = String(kind || "未知事件");
  return row;
}

/** 商店结算后的货架（"售完即空、不补货"的可核读数：买过的那几件标 `data-sold="1"`）。 */
function scriptSettleSoldShelf(flow) {
  const stock = (flow && flow.spec && flow.spec.stock) || null;
  if (!stock) return [];
  const rows = [];
  const bought = (flow.purchases || []).slice();
  for (const itemId of [...(stock.skills || []), ...(stock.relics || [])]) {
    const sold = bought.indexOf(itemId) >= 0;
    const row = scriptItemRow(itemId, flow.catalog);
    const node = scriptNode("div", "script-face-row", "");
    node.setAttribute("data-item-id", itemId);
    // 票 06：结果弹窗的货架行一件一行 ⇒ 也是浮层宿主。
    node.setAttribute("data-skill", itemId);
    node.setAttribute("data-kind", row.kind);
    node.setAttribute("data-sold", sold ? "1" : "0");
    node.appendChild(scriptNode("span", "script-face-item", row.name));
    node.appendChild(scriptNode("span", "script-face-note",
      SCRIPT_ITEM_KIND_LABELS[row.kind] || ""));
    if (scriptPriceText(row)) {
      node.appendChild(scriptNode("span", "script-face-price", scriptPriceText(row)));
    }
    if (sold) node.appendChild(scriptNode("span", "script-face-note", "已售出（不补货）"));
    rows.push(node);
  }
  return rows;
}

/** 结算响应 → 结果弹窗（**所有数字都来自 `events` / `board.run`**；前端只排版）。 */
function scriptSettleShowResult(body, flow) {
  const board = (body && body.board) || null;
  const catalog = Object.assign({}, (flow && flow.catalog) || {}, (board && board.catalog) || {});
  const events = (body && body.events) || [];
  const bodyNodes = events.map((event) => scriptSettleEventRow(event, catalog));
  if (flow && flow.kind === "shop") {
    bodyNodes.push(scriptNode("div", "dim", "货架（售完即空、不补货）："));
    for (const row of scriptSettleSoldShelf(flow)) bodyNodes.push(row);
  }
  const outcome = (body && body.outcome) || "continue";
  const endReason = events.find((event) => event.kind === "run_ended");
  const title = outcome === "cleared" ? "通关"
    : outcome === "failed"
      ? (SCRIPT_RUN_END_LABELS[(endReason && endReason.reason) || ""] || "整轮失败")
      : "结算";
  const coins = board && board.run ? Number(board.run.coins) || 0 : null;
  const text = coins === null
    ? "这一轮结束了（存档已删除）。"
    : `结算完成：铜钱 ${coins}。`;
  const loot = scriptLoadoutState();
  const actions = [];
  if (board) {
    actions.push({
      label: "回棋盘",
      primary: true,
      run: () => {
        scriptCloseConfirm();
        loot.flow = null;
        loot.cart = [];
        if (body.save) scriptApplySave(body.save);
        scriptBoardShow(board);
        return null;
      },
    });
  } else {
    actions.push({
      label: "回开局页",
      primary: true,
      run: () => {
        scriptCloseConfirm();
        loot.flow = null;
        loot.cart = [];
        state.script.board = null;
        if (body.save) scriptApplySave(body.save);
        else scriptLoadSave();
        scriptBoardSync();
        show("lobby");
        return null;
      },
    });
  }
  restashBoardWindows(true);
  return scriptConfirm({
    kind: "result",
    dialog: "result",
    title,
    text,
    body: bodyNodes,
    actions,
  });
}

/** 落一次结算：`POST …/settle`（body 的形状由**内层房间类型**决定，多传即被服务端拒）。 */
async function scriptSettleSubmit(flow) {
  const target = flow || scriptLoadoutState().flow;
  const board = scriptLoadoutBoard();
  if (!board || !target || state.script.busy) return null;
  const runId = board.run_id || state.script.runId;
  const body = { expected_revision: board.revision };
  if (target.kind === "battle") {
    body.session_id = board.session_id;
    body.picks = (target.picks || []).slice();
  } else if (target.kind === "event") {
    body.option = target.option;
  } else if (target.kind === "shop") {
    body.purchases = (target.purchases || []).slice();
  }
  state.script.busy = true;
  let response = null;
  let failure = null;
  try {
    response = await post(`/api/script-runs/${encodeURIComponent(runId)}/settle`, body);
  } catch (error) {
    failure = error;
  } finally {
    state.script.busy = false;
  }
  if (!response) {
    scriptCloseConfirm();
    // 结算没落地：收窗之后那两块要立刻回寄存格（否则它们停在已清空的正文槽里，id 查不到）。
    restashBoardWindows(true);
    scriptLoadoutState().flow = null;
    const payload = (failure && failure.body) || { reason: failure && failure.message };
    scriptBoardShowError(payload);
    scriptLoadoutHint("结算没落地：上面那行是服务端给的原因（棋盘没有改动）。");
    if (payload && typeof payload.status === "string") {
      scriptApplySave(payload);
      await scriptBoardRefresh();
    }
    return null;
  }
  if (response.save) scriptApplySave(response.save);
  return scriptSettleShowResult(response, target);
}

/* ---------------------------------------------------------------- HUD（票 05）
 *
 * 右栏 HUD 是**只读展示**（spec A7）：铜钱 / 背包 / 遗物三个数读服务端棋盘体的 `run`，
 * 前端不记账、不重算；四格出战槽与六格遗物格用**固定格数**（空也画一格），于是背包 / 遗物
 * 再多，HUD 的宽与每行高度都不变（判据在 `style.css` 的 `.design-canvas .hud-*` 那几条）。
 * 动作区里「背包」这颗键只在**节点之外**存在：`pending` 非空 ⇒ 这个节点根本不进 DOM
 * （spec R6：不是置灰、不是隐藏）。`data-skill` 是票 06 浮层的宿主挂点，本票只定形状与位置。
 */

/** 出战槽的格数（与缺省 `equipped_slots` 的 4 个格号同一份事实）。 */
const SCRIPT_EQUIPPED_SLOTS = 4;

/** HUD 的「背包」键：整页只创建一次，之后只挂 / 摘（`pending` 的判据在 `scriptHudActions`）。 */
let SCRIPT_HUD_BAG = null;

function scriptHudBagButton() {
  if (SCRIPT_HUD_BAG) return SCRIPT_HUD_BAG;
  const button = document.createElement("button");
  button.type = "button";
  button.id = "script-hud-bag";
  button.className = "hud-bag";
  button.textContent = "背包";
  button.addEventListener("click", () => scriptBoardLoadoutWindowOpen());
  SCRIPT_HUD_BAG = button;
  return button;
}

/** 动作区：`pending` 为空才有「背包」（「离开棋局」是静态节点，永远在）。
 *
 * **不是置灰、不是隐藏**：节点进行中这颗键被 `removeChild` 掉，DOM 里少一个节点。
 */
function scriptHudActions() {
  const box = $("script-hud-actions");
  const bag = scriptHudBagButton();
  if (!box) return null;
  if (scriptLoadoutPending()) {
    if (bag.parentNode === box) box.removeChild(bag);
    return null;
  }
  if (bag.parentNode !== box) box.insertBefore(bag, box.children.length ? box.children[0] : null);
  return bag;
}

/** HUD 的一格出战槽：`data-slot` = 第几格（0–3），`data-skill` = 票 06 浮层的宿主。 */
function scriptHudEquipCell(index, skillId) {
  const cell = scriptNode("div", "hud-equip-cell", "");
  cell.setAttribute("data-slot", String(index));
  cell.setAttribute("data-tag", `出战 ${index + 1}`);
  if (skillId) {
    cell.setAttribute("data-skill", String(skillId));
    cell.setAttribute("data-skill-id", String(skillId));
  } else {
    cell.setAttribute("data-empty", "1");
  }
  cell.appendChild(scriptNode("span", "hud-etag", `出战 ${index + 1}`));
  cell.appendChild(scriptNode("span", "hud-en", skillId ? scriptItemRow(skillId).name : "空"));
  return cell;
}

/** HUD 的一格遗物：`data-relic-slot` = 第几格（0–5），`data-skill` = 票 06 浮层的宿主。 */
function scriptHudRelicCell(index, relicId) {
  const cell = scriptNode("div", "hud-relic", relicId ? scriptItemRow(relicId).name : "空");
  cell.setAttribute("data-relic-slot", String(index));
  if (relicId) {
    cell.setAttribute("data-skill", String(relicId));
    cell.setAttribute("data-relic-id", String(relicId));
  } else {
    cell.setAttribute("data-empty", "1");
  }
  return cell;
}

/** 画 HUD：三个读数 + 4 出战槽 + 6 遗物格 + 动作区。没有棋盘体时整块收干净。 */
function scriptHudRender() {
  // 票 06：出战 / 遗物格要重铺 ⇒ 浮层先收（与整备面同一条纪律）。
  if (typeof skillTipHide === "function") skillTipHide();
  const run = scriptLoadoutRun();
  const coins = $("script-hud-coins");
  const backpack = $("script-hud-backpack");
  const relicCount = $("script-hud-relic-count");
  const equippedBox = $("script-hud-equipped");
  const relicsBox = $("script-hud-relic-grid");
  if (!run) {
    for (const node of [coins, backpack, relicCount]) {
      if (node) node.textContent = "";
    }
    if (equippedBox) equippedBox.innerHTML = "";
    if (relicsBox) relicsBox.innerHTML = "";
    scriptHudActions();
    return null;
  }
  const slots = scriptLoadoutSlots();
  const equipped = scriptLoadoutEquipped();
  const relics = scriptLoadoutRelics();
  if (coins) coins.textContent = String(Number(run.coins) || 0);
  if (backpack) backpack.textContent = `${slots.filter(Boolean).length}/${SCRIPT_BACKPACK_SLOTS}`;
  if (relicCount) relicCount.textContent = `${relics.length}/${SCRIPT_RELIC_SLOTS}`;
  if (equippedBox) {
    equippedBox.innerHTML = "";
    for (let index = 0; index < SCRIPT_EQUIPPED_SLOTS; index += 1) {
      const slotIndex = Number(equipped[index]);
      const skillId = Number.isInteger(slotIndex) ? slots[slotIndex] : null;
      equippedBox.appendChild(scriptHudEquipCell(index, skillId || null));
    }
  }
  if (relicsBox) {
    relicsBox.innerHTML = "";
    for (let index = 0; index < SCRIPT_RELIC_SLOTS; index += 1) {
      relicsBox.appendChild(scriptHudRelicCell(index, relics[index] || null));
    }
  }
  scriptHudActions();
  return null;
}

/* -------------------------------------------------------------- 同步 / 接线 */

/** 棋盘体一变就重画（`script-board.js` 的 show / sync 两处调；没有棋盘体就收干净）。 */
function scriptLoadoutSync() {
  const board = scriptLoadoutBoard();
  if (!board) {
    const loot = scriptLoadoutState();
    loot.selected = null;
    loot.drag = null;
    loot.cart = [];
    loot.flow = null;
    loot.revealOpen = false;
    scriptLoadoutRender();
    scriptFaceRender();
    scriptHudRender();
    // 没有棋盘体了（删档 / 通关 / 放弃）：棋盘那扇窗也一并收起，别留一扇空窗。
    if (typeof scriptBoardWindowClose === "function") scriptBoardWindowClose();
    return null;
  }
  scriptLoadoutRender();
  scriptFaceRender();
  scriptHudRender();
  return null;
}

/** 进入一个节点之后的收尾（`script-board.js:scriptBoardEnterNode` 调）：
 *  节点内的显示态从"刚进来"开始（问号房重新走一遍揭晓那一步，不沿用上一个节点）。
 *  战斗房的跳转归票 23——本文件不切会话、不 show 牌桌。
 */
function scriptLoadoutAfterEnter(board) {
  // `scriptBoardEnterNode` **已经**调过 `scriptBoardShow`（棋盘与提示行都画好了）：这里只复位
  // 节点内的显示态再重画，不再 show 一次（否则会盖掉"已进入「…」"那句提示）。
  if (board) scriptLoadoutResetNodeState();
  scriptLoadoutSync();
  return null;
}

/* ------------------------------------------------------ 棋盘 ↔ 牌桌（票 23） */

/** 进战斗节点之后**换到牌桌**（M7 票 23）：剧本局的战斗就是现有身份局牌桌，零新增 view。
 *
 * 棋盘体上的 `session_id` 就是既有 `/api/sessions/<id>` 的会话（票 19）；这里只做三件事：
 * 记住"这一场是从棋盘进来的"（表盘出口的显隐判据），按开局页建局之后**同一条路**把首屏
 * 视图换进来（`session.js:startSessionView`），再切到牌桌页。不复制 `table.js`、不新造 DOM。
 */
async function scriptBattleEnter(board) {
  const sessionId = board && board.session_id;
  if (!sessionId) return null;
  const runId = board.run_id || state.script.runId;
  const pending = (board && board.pending) || null;
  const view = await api(
    `/api/sessions/${encodeURIComponent(sessionId)}?after=-1&logs=0`,
  );
  state.script.battle = {
    sessionId,
    runId,
    nodeId: (pending && pending.node_id) || null,
  };
  startSessionView(view);
  // 票 05：节点面住在面板窗里（`所有 <main>` 之外），换到牌桌时必须**先收起那扇窗**，
  // 否则它会浮在牌桌上（`show()` 只藏 `main`，藏不到窗壳）。
  if (typeof scriptBoardWindowClose === "function") scriptBoardWindowClose();
  return view;
}

/** 这一场是不是"从棋盘进来的、且服务端说终局了"（出口按钮的唯一判据，只读）。 */
function scriptBattleFinished(view) {
  const battle = state.script.battle;
  return Boolean(
    battle
    && battle.sessionId
    && state.sessionId
    && battle.sessionId === state.sessionId
    && view
    && view.finished === true,
  );
}

/** 牌桌 → 棋盘（M7 票 23）：一场打完就回棋盘，结算仍走票 22 的既有流程（三选一 / 遗物弹窗）。
 *
 * 结算端点的 `session_id` 从**内存里的棋盘体**读（会话 id 不进档，票 04 §4）；先把牌桌会话
 * 摘掉（`state.sessionId` 归空 ⇒ 顶栏「牌桌」随之置灰），再从棋盘开结算——服务端判胜负。
 */
function scriptBattleReturnToBoard() {
  if (!state.script.battle) return null;
  const board = state.script.board;
  state.sessionId = null;
  state.script.battle = null;
  if (board) scriptBoardShow(board);
  return scriptSettleOpenBattle();
}

/** 自己的 PvE 显示位（`table.js:renderSelfSeat` 末尾调用）：全局技能标签 + 只读遗物栏 + 出口。
 *
 * 判据全是**载荷事实**：`player.side` 有值 = 这是一场剧本战斗（票 01 §3 的友 / 敌）；遗物
 * 列表读快照顶层的只读 `relics`（票 23 的投影增量）；出口另要"这一场是从棋盘进来的"。
 * 身份局的这三个键都不存在 / 为空 ⇒ 节点一律收起，牌桌 DOM 与引入前同形。
 */
function scriptBattleTableSync(view) {
  const player = view && view.snapshot
    ? (view.snapshot.players || []).find(
      (item) => Number(item.seat) === Number(view.snapshot.seat),
    )
    : null;
  const globalBox = $("t-self-global");
  if (globalBox) {
    const text = typeof pveGlobalText === "function" ? pveGlobalText(player) : "";
    globalBox.hidden = text === "";
    globalBox.textContent = text;
    if (text) {
      globalBox.setAttribute("data-side", String((player.global_skill || {}).side || ""));
    }
  }
  const relicBox = $("t-self-relics");
  if (relicBox) {
    const pve = Boolean(player && player.side);
    relicBox.hidden = !pve;
    if (pve) {
      const relics = (view && view.snapshot && view.snapshot.relics) || [];
      const list = $("t-self-relic-list");
      if (list) {
        list.textContent = relics.length
          ? relics.map((row) => String((row && (row.name || row.id)) || "")).join("、")
          : "无";
      }
    }
  }
  const back = $("btn-t-pve-return");
  if (back) back.hidden = !scriptBattleFinished(view);
  // 战斗中退出（用户故事 17）：只有"从棋盘进来的那一场"且还没终局时出现——终局之后
  // 出口是「回棋盘结算」，两个键不同时占版面。
  const exit = $("btn-t-pve-exit");
  if (exit) {
    const inBattle = Boolean(
      state.script.battle
      && state.script.battle.sessionId
      && state.script.battle.sessionId === state.sessionId,
    );
    exit.hidden = !inBattle || scriptBattleFinished(view);
  }
  return null;
}

/** 接线（`main.js:wire()` 里调一次）：整备面只有「丢弃」是静态键，其余全是现铺的行。 */
function wireScriptLoadout() {
  const on = (id, handler) => {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  };
  on("btn-script-loadout-discard", () => scriptLoadoutDiscard());
  // 票 05：窗的关闭键（面板窗自己的收窗口）要把棋盘那两块送回寄存格——否则它们会停在
  // 不可见的正文槽里（下一次重画时再来一次也无害，`restashBoardWindows` 幂等）。
  on("script-confirm-close", () => restashBoardWindows(true));
  // M7 票 23：牌桌上那个"回棋盘结算"的出口（显示态由 `scriptBattleTableSync` 维护）。
  on("btn-t-pve-return", () => scriptBattleReturnToBoard());
  // 「退出战斗」与棋盘的「离开棋局」是**同一条**退出路径（票 20 的确认框 + 一条 suspend）：
  // 入口在牌桌上（战斗中唯一可见的那一个），收尾动作（回开局页 / 摘会话）住在同一个函数里。
  on("btn-t-pve-exit", () => (typeof scriptBoardExit === "function" ? scriptBoardExit() : null));
  scriptLoadoutSync();
  return null;
}
