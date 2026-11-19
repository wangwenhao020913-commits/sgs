/* SGS 前端 · 剧本棋盘（M7 票 21 / spec §4「棋盘新视图」、票 05 UI-1）。
 *
 * 这一份脚本装两样东西，**它们必须一直是同一个渲染器**（D26）：
 *
 * 1. **节点图渲染器**：`scriptGraphRender(container, spec)` —— 图标 / 上方标题 / SVG 连线 /
 *    边条件标签的唯一画法。棋盘（这里）与编辑器（票 25 的 `script-editor.js`）都调它，
 *    实现期**不许出现第二份节点图画法**（`tests/test_web_static.py` 的两条机械断言：
 *    入口全局只有一处定义、静态目录里只有这一份文件画 `<line>`）。
 *    两种语境由一个 `mode` 区分：`"board"` = 玩家视角（画已走 / 当前 / 可走 / 关闭 /
 *    去不了的状态色），`"editor"` = 作者视角（**一个状态类都不写**）。
 * 2. **棋盘视图**：`scriptBoardShow` / `scriptBoardRender` / `scriptBoardRefresh` /
 *    `scriptBoardEnterNode`。判据全在服务端的棋盘体上（`state.script.board`，票 19 的
 *    `_board`）：节点状态、可到达集（`available`）、当前位置（`current` / `pending`）、
 *    死路原因（`board_error`）都是原样字段，前端**不重算**。
 *
 * 键盘 / 鼠标都只是"悬停出卡"的入口：`mouseenter` / `focus` 开卡，`mouseleave` / `blur` 收卡。
 * 卡是本视图唯一"新造 DOM 形状"的地方：战斗房给座位布局与敌人信息（模板名 / 档位 / 血量 /
 * 初始技能 / 随机抽到的技能 / 胜负 / 全局副本技能 / 奖励），非战斗房给房间类型与已抽出的
 * 结果（商店库存 / 抽中的事件 / 揭晓的内层房间）。
 *
 * 位置纪律：本视图在回放视图**之后**（`index.html`）；注释里也不写那两个 `main` 的 id 字面量。
 */
"use strict";

/* ---------------------------------------------------------------- 常量 */

/** 节点图标统一 64×64（65,536 px²）；票 05 UI-1 的硬尺寸，CSS 同一条（`style.css`）。 */
const SCRIPT_GRAPH_ICON_PX = 64;

/** 标题在节点**上方**：标题行高 + 与图标之间的空隙。 */
const SCRIPT_GRAPH_TITLE_H = 20;
const SCRIPT_GRAPH_TITLE_GAP = 4;

/** 卡格（格子）尺寸：节点定死在格里居中，格与格之间留出连线与标题的空间。 */
const SCRIPT_GRAPH_CELL_W = 176;
const SCRIPT_GRAPH_CELL_H = 132;
const SCRIPT_GRAPH_MIN_CELL_W = 96;
const SCRIPT_GRAPH_PAD = 24;

/** 没有视口读数时（烟测垫片 / 非浏览器）的设计宽；真浏览器按 `window.innerWidth` 折页边。 */
const SCRIPT_GRAPH_DEFAULT_WIDTH = 1280;
const SCRIPT_GRAPH_PAGE_PAD = 32;

/** 悬浮卡的宽度（贴边时翻到节点的另一侧）。 */
const SCRIPT_GRAPH_CARD_W = 360;

/** 房间类型 → 中文标签（房间里只存枚举值，标签是显示层的事）。 */
const SCRIPT_GRAPH_KIND_LABELS = {
  story: "剧情战斗",
  battle: "遭遇战",
  event: "事件",
  shop: "商店",
  unknown: "问号房",
};

/** 房间类型 → 图标名；Boss 房恒用红色感叹号（图标本身是红色，不是状态色）。 */
const SCRIPT_GRAPH_ICON_NAMES = {
  story: "flag",
  battle: "swords",
  event: "book",
  shop: "purse",
  unknown: "question",
};

/** 本进程内画过几次节点图：**唯一入口**被调用的机械读数（烟测断"棋盘与编辑器都走它"）。 */
let SCRIPT_GRAPH_DRAWS = 0;

/* -------------------------------------------------------------- 图标 */

/** 六种图标的内联 SVG 路径（不用图片资源、不手写命名空间）。
 *
 * 颜色全部走 `currentColor`：状态色只挂在按钮上、Boss 的红只挂在 `.script-icon-boss` 上，
 * 于是"红 = Boss"与"颜色 = 状态"是两条互不相干的渠道。图标里**没有文字节点**
 * （`?` / `!` 也画成路径），节点因此真的"只放图标"。
 */
const SCRIPT_GRAPH_ICON_PATHS = {
  // 剧情战斗 = 旗帜
  flag: '<path d="M24 12 L24 52" /><path d="M24 14 L46 21 L24 28 Z" />',
  // 遭遇战 = 交叉双剑
  swords: '<path d="M18 46 L46 18" /><path d="M46 46 L18 18" />'
    + '<path d="M20 40 L24 44" /><path d="M44 40 L40 44" />',
  // 事件 = 书
  book: '<path d="M16 20 Q26 15 32 20 L32 48 Q26 43 16 47 Z" />'
    + '<path d="M48 20 Q38 15 32 20 L32 48 Q38 43 48 47 Z" />',
  // 商店 = 钱袋
  purse: '<path d="M20 30 Q14 42 20 48 Q32 54 44 48 Q50 42 44 30 Z" />'
    + '<path d="M20 30 L32 22 L44 30" />',
  // 问号房 = ？
  question: '<path d="M24 24 Q24 14 32 14 Q40 14 40 22 Q40 28 32 32 L32 38" />'
    + '<circle cx="32" cy="47" r="2.5" />',
  // Boss = 红色感叹号（颜色由 CSS 给）
  boss: '<path d="M32 14 L32 38" /><circle cx="32" cy="47" r="2.5" />',
};

/** 一个节点的图标：`{name, markup}`。`name` 进 `data-icon`，供烟测 / 样式按名读。 */
function scriptGraphIconMarkup(kind, boss) {
  const name = boss ? "boss" : SCRIPT_GRAPH_ICON_NAMES[kind] || "question";
  const className = name === "boss"
    ? "script-icon script-icon-boss"
    : "script-icon";
  const markup = `<svg class="${className}" viewBox="0 0 64 64" fill="none"`
    + ' stroke="currentColor" stroke-width="3" stroke-linecap="round"'
    + ` stroke-linejoin="round" aria-hidden="true">${SCRIPT_GRAPH_ICON_PATHS[name]}</svg>`;
  return { name, markup };
}

/* -------------------------------------------------------------- 布局 */

/** 边条件标签的文案（空条件 = 不画标签；标签只读边自己的 `requires`）。 */
function scriptGraphEdgeLabel(edge) {
  const requires = (edge && edge.requires) || {};
  const flags = Array.isArray(requires.flags) ? requires.flags : [];
  const notFlags = Array.isArray(requires.not_flags) ? requires.not_flags : [];
  const parts = [];
  if (flags.length) parts.push(`需要 ${flags.join("、")}`);
  if (notFlags.length) parts.push(`不要 ${notFlags.join("、")}`);
  return parts.join("；");
}

/** 节点图几何的**纯函数**：节点格 / 图标 / 标题矩形的唯一来源，DOM 与样式都读它。
 *
 * `width` 由调用方给（棋盘 = 量到的内容宽；烟测显式喂 1280 / 1920 两档）：窄屏时只收
 * **格宽**，图标恒 64×64，标题恒在图标上方——于是"图标 + 标题 + 连线"在任何一档都不重叠。
 */
function scriptGraphLayout(spec) {
  const source = spec || {};
  const nodes = Array.isArray(source.nodes) ? source.nodes : [];
  const edges = Array.isArray(source.edges) ? source.edges : [];
  const layer = source.layer || {};
  const rows = Math.max(1, Number(source.rows) || Number(layer.rows) || 1);
  const cols = Math.max(1, Number(source.cols) || Number(layer.cols) || 1);
  const width = Math.max(
    SCRIPT_GRAPH_MIN_CELL_W + 2 * SCRIPT_GRAPH_PAD,
    Math.round(Number(source.width) || SCRIPT_GRAPH_DEFAULT_WIDTH),
  );
  const inner = Math.max(SCRIPT_GRAPH_ICON_PX + 16, width - 2 * SCRIPT_GRAPH_PAD);
  const cellW = Math.max(
    SCRIPT_GRAPH_MIN_CELL_W,
    Math.min(SCRIPT_GRAPH_CELL_W, Math.floor(inner / cols)),
  );
  const cellH = SCRIPT_GRAPH_CELL_H;
  const left = Math.max(SCRIPT_GRAPH_PAD, Math.round((width - cellW * cols) / 2));
  const top = SCRIPT_GRAPH_PAD;

  const boxes = new Map();
  for (const node of nodes) {
    const col = Math.max(0, Math.min(cols - 1, Number(node.col) || 0));
    const row = Math.max(0, Math.min(rows - 1, Number(node.row) || 0));
    const cellX = left + col * cellW;
    const cellY = top + row * cellH;
    boxes.set(node.id, {
      id: node.id,
      row,
      col,
      cellX,
      cellY,
      cellW,
      cellH,
      titleX: cellX,
      titleY: cellY,
      titleW: cellW,
      titleH: SCRIPT_GRAPH_TITLE_H,
      iconX: cellX + Math.round((cellW - SCRIPT_GRAPH_ICON_PX) / 2),
      iconY: cellY + SCRIPT_GRAPH_TITLE_H + SCRIPT_GRAPH_TITLE_GAP,
      iconW: SCRIPT_GRAPH_ICON_PX,
      iconH: SCRIPT_GRAPH_ICON_PX,
    });
  }

  const lines = [];
  for (const edge of edges) {
    const from = boxes.get(edge.from);
    const to = boxes.get(edge.to);
    if (!from || !to) continue; // 端点不在本层：不画（加载器已经拒绝断链，这里只是防御）
    lines.push({
      from: edge.from,
      to: edge.to,
      x1: from.iconX + from.iconW / 2,
      y1: from.iconY + from.iconH / 2,
      x2: to.iconX + to.iconW / 2,
      y2: to.iconY + to.iconH / 2,
      label: scriptGraphEdgeLabel(edge),
    });
  }

  return {
    width,
    height: top * 2 + rows * cellH,
    rows,
    cols,
    cellW,
    cellH,
    top,
    left,
    nodes: Array.from(boxes.values()),
    lines,
  };
}

/** 连线 + 条件标签的 SVG 字符串（**唯一**一处 `<line>`）。 */
function scriptGraphEdgeMarkup(layout) {
  const lines = layout.lines.map((line) => {
    const label = line.label
      ? `<text class="script-edge-label" x="${Math.round((line.x1 + line.x2) / 2)}"`
        + ` y="${Math.round((line.y1 + line.y2) / 2) - 6}" text-anchor="middle">`
        + `${scriptGraphEscape(line.label)}</text>`
      : "";
    return `<line class="script-edge" x1="${line.x1}" y1="${line.y1}"`
      + ` x2="${line.x2}" y2="${line.y2}" />${label}`;
  }).join("");
  return `<svg class="script-graph-svg" width="${layout.width}" height="${layout.height}"`
    + ` viewBox="0 0 ${layout.width} ${layout.height}" aria-hidden="true">${lines}</svg>`;
}

/** 标签是作者写的 flag id：进 SVG 字符串前转义（不是 innerHTML 拼任意 HTML）。 */
function scriptGraphEscape(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ---------------------------------------------------------- 节点状态 */

/** 节点的状态（**只读服务端字段**：`status` / `current` / `available`）。
 *
 * 编辑器语境返回空 key：作者视角不画玩家状态色（票 05 §3.6）。这里不重算可到达集——
 * `available` 就是服务端 `available_next` 的原样结果。
 */
function scriptGraphNodeState(node, mode) {
  if (mode === "editor") return { key: "", label: "" };
  if (node && node.current) return { key: "current", label: "当前" };
  if (node && node.available) return { key: "available", label: "可走" };
  if (node && node.status === "completed") return { key: "completed", label: "已走" };
  if (node && node.status === "closed") return { key: "closed", label: "关闭" };
  return { key: "blocked", label: "去不了" };
}

/** 一块节点格：标题在上、64×64 图标按钮在下；悬停 / 聚焦开卡，点击进节点。 */
function scriptGraphNodeCell(node, box, mode, handlers, cols) {
  const room = (node && node.room) || {};
  const kind = String(room.kind || "unknown");
  const boss = Boolean(room.is_boss);
  const name = String(room.name || (node && node.id) || "");

  const cell = document.createElement("div");
  cell.className = "script-node";
  cell.setAttribute("data-node-id", String(node.id));
  cell.setAttribute("data-room-kind", kind);
  cell.setAttribute("data-boss", boss ? "1" : "0");
  if (box.col === cols - 1) cell.setAttribute("data-exit", "1");
  cell.style.position = "absolute";
  cell.style.left = `${box.cellX}px`;
  cell.style.top = `${box.cellY}px`;
  cell.style.width = `${box.cellW}px`;
  cell.style.height = `${box.cellH}px`;

  const title = document.createElement("span");
  title.className = "script-node-title";
  title.textContent = name;
  title.style.position = "absolute";
  title.style.left = `${box.titleX - box.cellX}px`;
  title.style.top = `${box.titleY - box.cellY}px`;
  title.style.width = `${box.titleW}px`;
  title.style.height = `${box.titleH}px`;

  const state = scriptGraphNodeState(node, mode);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "script-node-button";
  if (state.key) button.classList.add(`st-${state.key}`);
  button.setAttribute("data-node-id", String(node.id));
  button.title = state.label ? `${name} · ${state.label}` : name;
  button.style.position = "absolute";
  button.style.left = `${box.iconX - box.cellX}px`;
  button.style.top = `${box.iconY - box.cellY}px`;
  button.style.width = `${box.iconW}px`;
  button.style.height = `${box.iconH}px`;

  const icon = document.createElement("span");
  icon.className = "script-node-icon";
  const glyph = scriptGraphIconMarkup(kind, boss);
  icon.setAttribute("data-icon", glyph.name);
  icon.innerHTML = glyph.markup;
  button.appendChild(icon);

  cell.appendChild(title);
  cell.appendChild(button);

  if (handlers) {
    if (typeof handlers.onNodeHover === "function") {
      const open = () => handlers.onNodeHover(node, box);
      cell.addEventListener("mouseenter", open);
      button.addEventListener("focus", open);
    }
    if (typeof handlers.onNodeLeave === "function") {
      const close = () => handlers.onNodeLeave(node);
      cell.addEventListener("mouseleave", close);
      button.addEventListener("blur", close);
    }
    if (typeof handlers.onNodeClick === "function") {
      button.addEventListener("click", () => handlers.onNodeClick(node));
    }
  }
  return cell;
}

/* ---------------------------------------------------------- 渲染器 */

/** **唯一**的节点图渲染入口：图标 + 上方标题 + SVG 连线 + 边条件标签。
 *
 * `spec` = `{mode, layer:{rows,cols}, nodes, edges, width, onNodeHover, onNodeLeave, onNodeClick}`
 * ——棋盘直接喂服务端的棋盘体（多出来的键不看），编辑器喂它自己的节点 / 连线（票 25）。
 * 返回 `{mode, layout, count}`；调用一次 `scriptGraphDrawCount()` +1。
 */
function scriptGraphRender(container, spec) {
  if (!container) return null;
  const source = spec || {};
  const mode = source.mode === "editor" ? "editor" : "board";
  const nodes = Array.isArray(source.nodes) ? source.nodes : [];
  const edges = Array.isArray(source.edges) ? source.edges : [];
  const layout = scriptGraphLayout(source);
  SCRIPT_GRAPH_DRAWS += 1;

  container.innerHTML = "";
  container.setAttribute("data-graph-mode", mode);
  container.style.position = "relative";
  container.style.height = `${layout.height}px`;

  const edgeLayer = document.createElement("div");
  edgeLayer.className = "script-graph-edges";
  edgeLayer.setAttribute("data-graph-layer", "edges");
  edgeLayer.innerHTML = scriptGraphEdgeMarkup(layout);
  container.appendChild(edgeLayer);

  const nodeLayer = document.createElement("div");
  nodeLayer.className = "script-graph-nodes";
  nodeLayer.setAttribute("data-graph-layer", "nodes");
  for (const node of nodes) {
    const box = layout.nodes.find((entry) => entry.id === node.id);
    if (!box) continue;
    nodeLayer.appendChild(scriptGraphNodeCell(node, box, mode, source, layout.cols));
  }
  container.appendChild(nodeLayer);
  return { mode, layout, count: nodeLayer.children.length };
}

/** 画过几次节点图（烟测断"棋盘与编辑器调的是同一个函数"的读数口）。 */
function scriptGraphDrawCount() {
  return SCRIPT_GRAPH_DRAWS;
}

/* ------------------------------------------------------------ 棋盘视图 */

/** 内容宽：真浏览器量**容器**（页身是 1180 居中栏，不能拿视口宽当内容宽），
 *  垫片没有布局引擎（也没有 `window.innerWidth`）⇒ 设计宽 1280；调用方也可以显式喂 `width`。 */
function scriptGraphWidth(container) {
  if (typeof window !== "undefined") {
    const viewport = Number(window.innerWidth);
    if (Number.isFinite(viewport) && viewport > 0) {
      const target = container || $("script-board-graph");
      const measured = target ? Number(target.clientWidth) : 0;
      const raw = measured > 0 ? measured : viewport - SCRIPT_GRAPH_PAGE_PAD * 2;
      return Math.max(320, Math.floor(raw));
    }
  }
  return SCRIPT_GRAPH_DEFAULT_WIDTH;
}

/** 棋盘提示行（`#script-board-hint`）：动作回执与"现在该做什么"共用这一格。 */
function scriptBoardHint(text) {
  const node = $("script-board-hint");
  if (node) node.textContent = text || "";
  return text || "";
}

/** 棋盘错误行（`#script-board-error`）：死路 / 400 的**服务端原话**。
 *
 * `board_error` 是服务端 `available_next` 抛错时下发的 `{rule_id, loc, reason}`（票 19）——
 * 前端只显示，不解释、不重算。`null` ⇒ 收起整行。
 */
function scriptBoardShowError(error) {
  const node = $("script-board-error");
  if (!node) return null;
  const reason = error && error.reason ? String(error.reason) : (error && error.error ? String(error.error) : "");
  node.hidden = !reason;
  node.textContent = reason;
  if (reason && error && error.rule_id) node.setAttribute("data-rule-id", String(error.rule_id));
  else node.removeAttribute("data-rule-id");
  return reason || null;
}

/** 当前棋盘体里的节点（按 id）。 */
function scriptBoardNode(nodeId) {
  const board = state.script.board;
  const nodes = (board && board.nodes) || [];
  return nodes.find((node) => node.id === nodeId) || null;
}

/** 节点的房间名（标题 / 回执用）。 */
function scriptBoardNodeName(node) {
  const room = (node && node.room) || {};
  return String(room.name || (node && node.id) || "");
}

/** 已进入的节点：`pending.node_id` 是 `<layer_id>/<node_id>`，尾段才是层内 id。 */
function scriptBoardPendingSpec(nodeId) {
  const board = state.script.board;
  const pending = (board && board.pending) || null;
  if (!pending) return null;
  const tail = String(pending.node_id || "").split("/").pop();
  return tail === nodeId ? pending.spec || null : null;
}

/** 画一整屏棋盘（头部 / 错误行 / 提示 / 节点图）。
 *
 * 判据全在 `state.script.board`：`script` / `layer` / `nodes` / `edges` / `pending` /
 * `board_error` 都是服务端字段；`state.script.board` 为空时把图收干净（不画上一次的底稿）。
 */
function scriptBoardRender() {
  const board = state.script.board;
  const graph = $("script-board-graph");
  const card = $("script-node-card");
  if (card) card.hidden = true;
  if (!board || !graph) {
    if (graph) {
      graph.innerHTML = "";
      graph.style.height = "0px";
    }
    return null;
  }
  const script = board.script || {};
  const layer = board.layer || {};
  const scriptLine = $("script-board-script");
  if (scriptLine) {
    scriptLine.textContent = script.name
      ? `《${script.name}》${script.version ? ` v${script.version}` : ""}`
      : "";
  }
  const layerLine = $("script-board-layer");
  if (layerLine) {
    layerLine.textContent = layer.name
      ? `大节 ${Number(layer.index || 0) + 1}/${Number(layer.count || 1)} · ${layer.name}`
      : "";
  }
  scriptBoardShowError(board.board_error || null);
  const pendingNode = board.pending ? scriptBoardNode(
    String(board.pending.node_id || "").split("/").pop(),
  ) : null;
  if (board.board_error) {
    scriptBoardHint("这条路线走不下去了（上面那行就是服务端给的原因）。");
  } else if (pendingNode) {
    scriptBoardHint(`当前节点：${scriptBoardNodeName(pendingNode)}（进入后还没结算）。`);
  } else {
    scriptBoardHint("点一个可走的节点推进（可到达集来自服务端，前端不重算）。");
  }
  return scriptGraphRender(graph, {
    mode: "board",
    layer,
    nodes: board.nodes || [],
    edges: board.edges || [],
    width: scriptGraphWidth(graph),
    onNodeHover: scriptBoardHover,
    onNodeLeave: scriptBoardUnhover,
    onNodeClick: scriptBoardEnterNode,
  });
}

/** 进棋盘：收下一份棋盘体（建局 / 继续 / 进入节点 / 刷新 / 结算的共同出口）并画出来。
 *
 * 只认带 `nodes` 的棋盘体；`run_id` / `revision` 一并对齐到本地态（`state.script`）。
 */
function scriptBoardShow(body) {
  if (body && Array.isArray(body.nodes)) {
    state.script.board = body;
    if (body.run_id) state.script.runId = body.run_id;
    if (Number.isInteger(body.revision)) state.script.revision = body.revision;
  }
  if (!state.script.board) return null;
  show("view-script-board");
  scriptBoardRender();
  // 票 22：整备面 / 当前节点的面读同一份棋盘体（`script-loadout.js`；调用时解析）。
  if (typeof scriptLoadoutSync === "function") scriptLoadoutSync();
  return state.script.board;
}

/** 没有棋盘体时把视图收干净（存档被删 / 放弃之后由开局页那边调；有棋盘体时不动）。 */
function scriptBoardSync() {
  if (state.script.board) return null;
  const graph = $("script-board-graph");
  if (graph) {
    graph.innerHTML = "";
    graph.style.height = "0px";
  }
  const card = $("script-node-card");
  if (card) card.hidden = true;
  scriptBoardShowError(null);
  scriptBoardHint("");
  if (typeof scriptLoadoutSync === "function") scriptLoadoutSync();
  return null;
}

/** 刷新棋盘：重新读一次 `GET /api/script-runs/<run_id>`（只读；结算归票 22 的调用方）。 */
async function scriptBoardRefresh() {
  const board = state.script.board;
  const runId = (board && board.run_id) || state.script.runId;
  if (!runId || state.script.busy) return null;
  state.script.busy = true;
  let body = null;
  let failure = null;
  try {
    body = await api(`/api/script-runs/${encodeURIComponent(runId)}`);
  } catch (error) {
    failure = error.body && error.body.reason ? error.body : { reason: error.message };
  } finally {
    state.script.busy = false;
  }
  if (body) {
    scriptBoardShow(body);
  } else if (failure) {
    scriptBoardShowError(failure);
  }
  return body;
}

/** 进入节点：`POST /api/script-runs/<run_id>/nodes/<node_id>`（**服务端**判可到达 / 起战斗会话）。
 *
 * 前端不预判可到达（`available` 只用来上色）：点哪个节点就发哪条请求，400 的原话落到
 * 错误行上。战斗房回来的棋盘体带 `session_id`——战场面归票 23，本视图先把棋盘体收下并
 * 说清楚"这一场在牌桌上打"。
 */
async function scriptBoardEnterNode(node) {
  const board = state.script.board;
  if (!board || !node || !node.id || state.script.busy) return null;
  const runId = board.run_id || state.script.runId;
  if (!runId) return null;
  state.script.busy = true;
  scriptBoardShowError(null);
  scriptBoardHint(`正在进入「${scriptBoardNodeName(node)}」…`);
  let body = null;
  let failure = null;
  try {
    body = await post(
      `/api/script-runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(node.id)}`,
      { expected_revision: board.revision },
    );
  } catch (error) {
    failure = error.body && (error.body.reason || error.body.error)
      ? error.body
      : { reason: error.message };
  } finally {
    state.script.busy = false;
  }
  if (!body) {
    scriptBoardShowError(failure);
    scriptBoardHint(`没能进入「${scriptBoardNodeName(node)}」：上面那行是服务端给的原因。`);
    return null;
  }
  scriptBoardShow(body);
  scriptBoardHint(body.session_id
    ? `已进入「${scriptBoardNodeName(node)}」：这一场在牌桌上打，打完回棋盘结算。`
    : `已进入「${scriptBoardNodeName(node)}」：结算之后回到棋盘。`);
  // 票 22：进节点后节点内的三个面（事件 / 商店 / 问号揭晓）由整备面那份脚本接手。
  if (typeof scriptLoadoutAfterEnter === "function") scriptLoadoutAfterEnter(body);
  // 票 23：战斗房**直接换到牌桌**——`session_id` 就是既有身份局牌桌的会话（零新增 view）。
  // 放在收尾钩子之后：棋盘体与节点内的显示态先落定，再切页；打不开就留在棋盘上说明原因。
  if (body.session_id && typeof scriptBattleEnter === "function") {
    try {
      await scriptBattleEnter(body);
    } catch (error) {
      const failure = error.body && (error.body.reason || error.body.error)
        ? error.body
        : { reason: error.message };
      scriptBoardShowError(failure);
      scriptBoardHint("牌桌没能打开：上面那行是服务端给的原因；回棋盘后可「重开这一场」。");
    }
  }
  return body;
}

/* ------------------------------------------------------------ 悬浮卡 */

/** 卡片的可见区块：每次开卡先把它们全收起，再按房间类型铺（不留上一张卡的残影）。 */
function scriptBoardCardSections() {
  return [
    "script-node-card-seats",
    "script-node-card-enemies",
    "script-node-card-victory",
    "script-node-card-globals",
    "script-node-card-reward",
    "script-node-card-options",
  ];
}

/** 一份奖励的显示文案：铜钱 + 逐档 `k 选 1` 的次数（字段名 `pickK` 的 k = 候选张数）。 */
function scriptBoardRewardText(reward) {
  const value = reward || {};
  const bits = [];
  if (Number(value.coins)) bits.push(`铜钱 ${Number(value.coins)}`);
  for (const size of [1, 2, 3]) {
    const count = Number(value[`pick${size}`]) || 0;
    if (count) bits.push(`${size === 1 ? "单选" : `${size} 选 1`}×${count}`);
  }
  return bits.length ? bits.join(" · ") : "无";
}

/** 模板名（`battle.templates` 是按敌人座位收的；友方模板不在里面，故退模板 id）。 */
function scriptBoardTemplateName(battle, templateId) {
  const templates = (battle && battle.templates) || [];
  const found = templates.find((item) => item.id === templateId);
  return found && found.name ? String(found.name) : String(templateId || "（未知模板）");
}

/** 座位布局的一行：友 / 敌 + 模板名（友方没有名字可用时就写模板 id）。 */
function scriptBoardSeatRow(side, templateId, extra) {
  const row = document.createElement("div");
  row.className = "script-card-seat";
  row.setAttribute("data-side", side);
  row.setAttribute("data-template", String(templateId || ""));
  const marks = [];
  if (extra && extra.protected) marks.push("受保护");
  if (extra && extra.boss) marks.push("Boss");
  row.textContent = `${side === "ally" ? "友" : "敌"} · ${extra && extra.name
    ? extra.name
    : String(templateId || "（无模板）")}${marks.length ? `（${marks.join("、")}）` : ""}`;
  return row;
}

/** 敌人信息的一行：模板名 / 档位 / 血量 / 初始技能 / 随机技能 / 奖励。
 *
 * "随机抽到的技能"只在**已进入**的战斗里存在（进入节点 = 抽完，票 04 纪律 2）：那一份在
 * `pending.spec.pve.grants` 里按座位给（座位 = 1 + 友方数 + 敌人序）。没进入时如实写
 * "进入时从随机池抽定"——前端不假装知道还没抽的东西。
 */
function scriptBoardEnemyRow(template, drawn) {
  const row = document.createElement("div");
  row.className = "script-card-enemy";
  row.setAttribute("data-template", String(template.id || ""));
  const skills = (template.initial_skills || []).join("、") || "无";
  const drawnText = drawn === null
    ? "进入时抽定（固定对手则无）"
    : (drawn.length ? drawn.join("、") : "无（固定对手）");
  row.textContent = `${template.name || template.id} · 档位 ${template.band}`
    + ` · 血量 ${template.hp}/${template.hp_max}`
    + ` · 初始技能：${skills}`
    + ` · 随机技能：${drawnText}`
    + ` · 奖励：${scriptBoardRewardText(template.reward)}`;
  return row;
}

/** 战斗房的相对座位（敌人序号 = `1 + 友方数 + i`），用来从 `grants` 找抽到的技能。
 *
 * 一座敌人的技能 = 模板 `initial_skills` **在前** + 随机抽到的**在后**（`_template_skills`
 * 的书写顺序），故"随机抽到的"= 该座 grants 去掉前 `len(initial_skills)` 条——不按 id 差集，
 * 于是池里那份恰好与初始技能同名时仍看得出它真被抽到过。
 */
function scriptBoardDrawnSkills(battle, spec, enemyIndex, template) {
  if (!spec || spec.kind !== "battle") return null;
  const seats = (spec.pve && spec.pve.seats) || [];
  const seat = 1 + ((battle && battle.allies) || []).length + enemyIndex;
  if (!seats[seat]) return null;
  const initial = ((template && template.initial_skills) || []).length;
  const grants = (spec.pve && spec.pve.grants) || [];
  return grants
    .filter((grant) => grant.seat === seat && grant.kind === "skill")
    .map((grant) => grant.id)
    .slice(initial);
}

/** 已进入的战斗里某个座位的合成武将名（`pending.spec.pve.seats[seat].name`）。 */
function scriptBoardSeatName(spec, seatIndex) {
  if (!spec || spec.kind !== "battle") return "";
  const seat = ((spec.pve && spec.pve.seats) || [])[seatIndex];
  return seat && seat.name ? String(seat.name) : "";
}

/** 敌人信息行要铺哪几个模板：**已进入**的战斗用 `enemy_templates`（真抽到的那些），
 *  没进入时用房间里的候选模板（按档抽 = 同档全体，固定 = 该场那几个）。 */
function scriptBoardEnemyTemplateIds(battle, spec) {
  const pool = battle.templates || [];
  if (spec && spec.kind === "battle" && (spec.enemy_templates || []).length) {
    return spec.enemy_templates.slice();
  }
  return pool.map((template) => template.id);
}

/** 战斗房：座位布局 + 敌人信息 + 胜负 + 全局副本技能 + 奖励。 */
function scriptBoardFillBattle(room, spec) {
  const battle = room.battle;
  const seats = $("script-node-card-seats");
  const enemiesBox = $("script-node-card-enemies");
  if (seats) {
    seats.innerHTML = "";
    seats.hidden = false;
    const allies = battle.allies || [];
    allies.forEach((seat, index) => {
      seats.appendChild(scriptBoardSeatRow("ally", seat.template, {
        protected: seat.protected,
        name: scriptBoardSeatName(spec, 1 + index)
          || scriptBoardTemplateName(battle, seat.template),
      }));
    });
    const enemySpec = battle.enemies || {};
    if (enemySpec.kind === "draw") {
      const row = document.createElement("div");
      row.className = "script-card-seat";
      row.setAttribute("data-side", "enemy");
      row.setAttribute("data-band", String(enemySpec.band || ""));
      const count = enemySpec.count || [1, 1];
      row.textContent = `敌 · 按档抽：${enemySpec.band}（${count[0]}~${count[1]} 名）`;
      seats.appendChild(row);
    } else {
      const list = enemySpec.seats || [];
      list.forEach((seat, index) => {
        seats.appendChild(scriptBoardSeatRow("enemy", seat.template, {
          boss: seat.boss,
          name: scriptBoardSeatName(spec, 1 + allies.length + index)
            || scriptBoardTemplateName(battle, seat.template),
        }));
      });
    }
  }
  if (enemiesBox) {
    enemiesBox.innerHTML = "";
    const pool = battle.templates || [];
    const ids = scriptBoardEnemyTemplateIds(battle, spec);
    ids.forEach((templateId, index) => {
      const template = pool.find((item) => item.id === templateId) || {
        id: templateId,
        name: templateId,
        band: "?",
        hp: "?",
        hp_max: "?",
        initial_skills: [],
        reward: {},
      };
      enemiesBox.appendChild(
        scriptBoardEnemyRow(
          template, scriptBoardDrawnSkills(battle, spec, index, template),
        ),
      );
    });
    enemiesBox.hidden = ids.length === 0;
  }
  const victory = $("script-node-card-victory");
  if (victory) {
    const condition = battle.victory || {};
    const labels = {
      rout: "全歼敌人",
      survive_rounds: `存活到第 ${condition.rounds} 轮之后`,
      rout_within_rounds: `${condition.rounds} 轮之内全歼`,
      slay_boss: "击败 Boss 座位",
    };
    victory.hidden = false;
    victory.textContent = `胜利条件：${labels[condition.kind] || condition.kind}`;
  }
  const globals = $("script-node-card-globals");
  if (globals) {
    const references = battle.global_skills || [];
    globals.hidden = references.length === 0;
    globals.textContent = references
      .map((reference) => `副本全局技能：${reference.skill}`
        + (reference.scale ? "（本房覆盖了三档区间）" : "（按库内定义）"))
      .join("；");
  }
  const reward = $("script-node-card-reward");
  if (reward) {
    reward.hidden = false;
    reward.textContent = `房间额外奖励：${scriptBoardRewardText(battle.extra_reward)}`
      + `（全队敌人模板的奖励另计）`;
  }
}

/** 非战斗房：房间类型 + 已抽出的结果（事件 / 商店库存 / 问号揭晓）。 */
function scriptBoardFillPlain(room, spec) {
  const box = $("script-node-card-options");
  if (!box) return;
  box.innerHTML = "";
  const row = (text) => {
    const node = document.createElement("div");
    node.className = "script-card-option";
    node.textContent = text;
    box.appendChild(node);
    return node;
  };
  if (room.kind === "event") {
    const events = room.events || [];
    for (const event of events) {
      row(`${event.name || event.id}：${(event.options || []).map((option) => option.text).join(" / ")}`);
    }
    if (!events.length) row("事件池是空的");
    if (spec && spec.kind === "event") {
      const drawn = events.find((event) => event.id === spec.event_id);
      row(`本节点已抽出：${drawn ? drawn.name || drawn.id : spec.event_id}`);
    }
  } else if (room.kind === "shop") {
    if (spec && spec.kind === "shop" && spec.stock) {
      row(`本节点库存 · 技能：${(spec.stock.skills || []).join("、") || "无"}`);
      row(`本节点库存 · 遗物：${(spec.stock.relics || []).join("、") || "无"}`);
    } else {
      row("库存：进入节点时从价值表抽定（6 技能 + 2 遗物，售完不补货）");
    }
  } else if (room.kind === "unknown") {
    for (const entry of room.pool || []) {
      row(`${entry.name || entry.room}（权重 ${entry.weight}）`);
    }
    if (!(room.pool || []).length) row("问号池是空的");
    if (spec && spec.kind === "unknown" && spec.revealed) {
      row(`已揭晓：${spec.revealed.room}`);
    }
  } else {
    row("这一格没有可抽的结果");
  }
  box.hidden = false;
}

/** 铺开一张卡：标题 / 房间类型 / 说明，再按房间类型铺各区块。 */
function scriptBoardFillCard(node, spec) {
  const room = (node && node.room) || {};
  const title = $("script-node-card-title");
  if (title) title.textContent = scriptBoardNodeName(node);
  const meta = $("script-node-card-meta");
  if (meta) {
    meta.textContent = `房间：${SCRIPT_GRAPH_KIND_LABELS[room.kind] || room.kind || "未知"}`
      + (room.is_boss ? " · Boss" : "");
  }
  const text = $("script-node-card-text");
  if (text) {
    text.hidden = !room.text;
    text.textContent = room.text || "";
  }
  for (const id of scriptBoardCardSections()) {
    const section = $(id);
    if (section) section.hidden = true;
  }
  if (room.battle) scriptBoardFillBattle(room, spec);
  else scriptBoardFillPlain(room, spec);
  return $("script-node-card");
}

/** 悬停 / 聚焦一个节点：铺卡并贴到节点的右侧（右侧放不下就翻到左侧）。 */
function scriptBoardHover(node, box) {
  const card = $("script-node-card");
  if (!card || !node || !box) return null;
  scriptBoardFillCard(node, scriptBoardPendingSpec(node.id));
  const gap = 16;
  const width = scriptGraphWidth($("script-board-graph"));
  let left = box.iconX + box.iconW + gap;
  if (left + SCRIPT_GRAPH_CARD_W > width) left = box.iconX - SCRIPT_GRAPH_CARD_W - gap;
  card.style.left = `${Math.max(0, Math.round(left))}px`;
  card.style.top = `${Math.max(0, Math.round(box.iconY - 8))}px`;
  card.hidden = false;
  return card;
}

/** 收起悬浮卡。 */
function scriptBoardUnhover() {
  const card = $("script-node-card");
  if (card) card.hidden = true;
  return null;
}

/* ------------------------------------------------------------ 离开棋局 */

/** 「离开棋局」：走票 20 的退出确认框，确认后 suspend 并回开局页（进度已保存）。 */
function scriptBoardExit() {
  return scriptExitConfirm(() => {
    show("lobby");
    scriptLoadSave();
  });
}

/* -------------------------------------------------------------- 接线 */

/** 接线（`main.js:wire()` 里调一次）：刷新 / 离开棋局 / 三个导航键由 `NAV_BUTTONS` 统一挂。 */
function wireScriptBoard() {
  const on = (id, handler) => {
    const node = $(id);
    if (node) node.addEventListener("click", handler);
  };
  on("btn-script-board-refresh", () => scriptBoardRefresh());
  on("btn-script-board-exit", () => scriptBoardExit());
  scriptBoardSync();
  return null;
}
