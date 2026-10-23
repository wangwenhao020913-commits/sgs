/* SGS 前端 · 牌桌动效（U3-03 / spec D8.1 / D13）：使用牌目标线、行动者高亮、体力闪。
 *
 * 纪律：
 * - **只用已下发的事实**：目标线来自 `USE_DESIGNATE_TARGET` 的 `actor` / 整份 `targets`
 *   （steps.py 的记录形状）；高亮只在**决策点**（`action` 记录 / `USE_DESIGNATE_TARGET`），
 *   结算类时机（`RESOLVE_*` / `USE_BECOME_TARGET` 等）不高亮——结算期 `actor` 常是目标。
 * - **寿命判定是纯函数** :func:`targetLineLane`（`line + now → playing | fading | gone`）：
 *   `setTimeout` 只负责到点重绘；测试直接喂 `now`，不读真实时钟（D15）。
 * - **端点只算一次**：画线时读一次 `getBoundingClientRect()` 并写进 SVG 属性；之后窗口
 *   尺寸变化不会重算已经在播的线（没有 resize 监听，也没有每帧重排）。
 * - 回放页不接线（D14）：它走 `replay.js` 的 `replayStep`，不经过牌桌的 `stepPlayback`。
 */
"use strict";

/* ------------------------------------------------------------------ 目标线 */

/** 线龄（D8.1）：2s = 保持 1.2s + 淡出 0.8s。淡出时长乘 `animScale`（测试里为 0）。 */
const TARGET_LINE_HOLD_MS = 1200;
const TARGET_LINE_FADE_MS = 800;

/** 箭头尺寸（设计单位）：10 长、半宽 5。乘以 1 设计单位（壳宽 / 1600）得 CSS 像素。 */
const TARGET_ARROW_SIZE = 10;
const TARGET_ARROW_HALF_WIDTH = 5;

/** 行动者描边 / 体力闪的时长（与 CSS 的 .5s / .4s 是同一份裁定，D13）。 */
const ACTING_HIGHLIGHT_MS = 500;
const HP_FLASH_MS = 400;

const SVG_NS = "http://www.w3.org/2000/svg";

/** `setTimeout` 归零也不影响测试：到点重绘的计时器只触发 :func:`renderTargetLines`。 */
let targetLineTimer = null;

/** 行动者高亮：`{seat, until}`。座位在 commit 时会重建，故重绘后按它补类。 */
let actingState = null;
let actingTimer = null;

/** 正在闪的武将块：`seat → {direction, timer}`（同一座位连续掉血 / 回复不叠计时器）。 */
const hpFlashes = new Map();

/** 造一个 SVG 节点；烟测垫片没有 `createElementNS`，退回普通 `createElement`（断言只看属性）。 */
function svgNode(tag) {
  return document.createElementNS
    ? document.createElementNS(SVG_NS, tag)
    : document.createElement(tag);
}

/** 带 class 的 SVG 节点：真 DOM 的 SVG `className` 是只读的 `SVGAnimatedString`，
 *  故 class 走 `setAttribute`；垫片断言读 `classList`，两边都写一份。 */
function svgNodeWithClass(tag, name) {
  const node = svgNode(tag);
  node.setAttribute("class", name);
  node.classList.add(name);
  return node;
}

/** 节点的布局矩形；垫片没有布局，退回 0 矩形（测试不读像素，D15）。 */
function rectOf(node) {
  if (node && typeof node.getBoundingClientRect === "function") {
    return node.getBoundingClientRect();
  }
  return { left: 0, top: 0, width: 0, height: 0 };
}

/** 一个元素中心相对牌桌壳左上角的 CSS 像素坐标。 */
function centerInShell(node, shellRect) {
  const rect = rectOf(node);
  return {
    x: rect.left - shellRect.left + (Number(rect.width) || 0) / 2,
    y: rect.top - shellRect.top + (Number(rect.height) || 0) / 2,
  };
}

/** 1 设计单位 = 壳宽 / 1600 CSS 像素（`--dsu` 的来源；见 style.css 的 `.table-shell`）。 */
function designUnitOf(shell) {
  const width = Number(shell && shell.clientWidth);
  if (Number.isFinite(width) && width > 0) return width / 1600;
  return 1;
}

/** 当前牌桌上的座位节点（环上对手 + 自己的底部面板）。 */
function seatNodes() {
  const ring = $("t-seats");
  const self = $("t-self-seat");
  return [...(ring ? ring.children : []), self].filter(Boolean);
}

function seatNodeFor(seat) {
  const wanted = String(seat);
  return seatNodes().find(
    (node) => node.getAttribute && node.getAttribute("data-seat") === wanted,
  ) || null;
}

/** 座位节点里第一个带某个类的后代（垫片没有 `querySelector`，故自己走一遍）。 */
function descendantWithClass(root, name) {
  for (const child of (root && root.children) || []) {
    if (nodeHasClass(child, name)) return child;
    const found = descendantWithClass(child, name);
    if (found) return found;
  }
  return null;
}

function heroBlockFor(seat) {
  // 自己的武将块有稳定 id（U1-02 的常驻节点）；对手座位才走后代查找。真页面里
  // `#t-self-hero` 也住在 `#t-self-seat` 里，两条路指向同一个节点。
  const view = state.view;
  const selfSeat = view && view.snapshot ? view.snapshot.seat : null;
  if (selfSeat !== null && selfSeat !== undefined && Number(seat) === Number(selfSeat)) {
    const selfBlock = $("t-self-hero");
    if (selfBlock) return selfBlock;
  }
  const seatNode = seatNodeFor(seat);
  return seatNode ? descendantWithClass(seatNode, "hero-block") : null;
}

/** 目标还活着吗？读的是**当前视图快照**（服务端已下发的公开事实），不自己推死亡。 */
function seatAlive(seat) {
  const view = state.view;
  const players = (view && view.snapshot && view.snapshot.players) || [];
  const player = players.find((item) => Number(item.seat) === Number(seat));
  return !player || player.alive !== false;
}

/** 线的一条 SVG 组：`<g class="target-line" data-line-to=…>` + 直线 + 箭头。
 *
 * 端点已经换成壳内 CSS 像素；箭头长 / 半宽按设计单位换算。`data-line-to` 同时落在
 * 组与直线上，读“每条线指向谁”时不必关心实现分层。
 */
function targetLineNode(from, to, target, actor, designUnit) {
  const group = svgNodeWithClass("g", "target-line");
  group.setAttribute("data-line-to", String(target));
  group.setAttribute("data-line-from", String(actor));

  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy) || 1;
  const ux = dx / length;
  const uy = dy / length;
  const arrow = TARGET_ARROW_SIZE * designUnit;
  const half = TARGET_ARROW_HALF_WIDTH * designUnit;
  const fixed = (value) => Number(value).toFixed(2);

  const shaft = svgNodeWithClass("line", "target-shaft");
  shaft.setAttribute("data-line-to", String(target));
  shaft.setAttribute("x1", fixed(from.x));
  shaft.setAttribute("y1", fixed(from.y));
  // 直线停在箭头底边：箭头自己画到目标块中心，不出现“线头穿出三角形”。
  shaft.setAttribute("x2", fixed(to.x - ux * arrow));
  shaft.setAttribute("y2", fixed(to.y - uy * arrow));
  group.appendChild(shaft);

  const baseX = to.x - ux * arrow;
  const baseY = to.y - uy * arrow;
  const arrowNode = svgNodeWithClass("polygon", "target-arrow");
  arrowNode.setAttribute("points", [
    `${fixed(to.x)},${fixed(to.y)}`,
    `${fixed(baseX - uy * half)},${fixed(baseY + ux * half)}`,
    `${fixed(baseX + uy * half)},${fixed(baseY - ux * half)}`,
  ].join(" "));
  group.appendChild(arrowNode);
  return group;
}

/** 寿命（**纯函数**，D15）：`line + now → playing | fading | gone`。
 *
 * 保持 1.2s 不缩；淡出 0.8s 乘 `animScale`，故测试里 `animScale = 0` 时保持结束即移除。
 */
function targetLineLane(line, now) {
  const enteredAt = Number(line && line.enteredAt);
  if (!Number.isFinite(enteredAt)) return "gone";
  const fadeMs = TARGET_LINE_FADE_MS * animScale();
  if (now >= enteredAt + TARGET_LINE_HOLD_MS + fadeMs) return "gone";
  if (now >= enteredAt + TARGET_LINE_HOLD_MS) return "fading";
  return "playing";
}

function removeTargetLine(line) {
  const node = line && line.node;
  if (node && node.parentNode) node.parentNode.removeChild(node);
}

/** 到点重绘：把该淡出的加 `.fading`、该移除的从 SVG 里摘掉（判定只走 :func:`targetLineLane`）。 */
function renderTargetLines(now = stageNow()) {
  const kept = [];
  for (const line of state.targetLines || []) {
    if (!line || !line.node) continue;
    const lane = targetLineLane(line, now);
    if (lane === "gone") {
      removeTargetLine(line);
      continue;
    }
    line.node.classList.toggle("fading", lane === "fading");
    kept.push(line);
  }
  state.targetLines = kept;
  scheduleTargetLines(now);
}

function scheduleTargetLines(now = stageNow()) {
  if (targetLineTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(targetLineTimer);
    targetLineTimer = null;
  }
  let next = null;
  const fadeMs = TARGET_LINE_FADE_MS * animScale();
  for (const line of state.targetLines || []) {
    const enteredAt = Number(line && line.enteredAt);
    if (!Number.isFinite(enteredAt)) continue;
    for (const at of [enteredAt + TARGET_LINE_HOLD_MS, enteredAt + TARGET_LINE_HOLD_MS + fadeMs]) {
      if (at > now && (next === null || at < next)) next = at;
    }
  }
  if (next === null) return;
  targetLineTimer = window.setTimeout(() => {
    targetLineTimer = null;
    renderTargetLines(stageNow());
  }, Math.max(0, next - now));
}

/** 清空全部目标线（换会话 / 测试收尾）：节点与计时器一起撤掉，不留幽灵。 */
function clearTargetLines() {
  if (targetLineTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(targetLineTimer);
    targetLineTimer = null;
  }
  for (const line of state.targetLines || []) removeTargetLine(line);
  state.targetLines = [];
}

/** 使用牌指定目标：从发动者武将块向每个目标画一条线（D8.1）。
 *
 * 跳过发动者自己与已阵亡座位；`targets` 为空（桃 / 无中生有 / 装备牌）时一条都不画。
 * 端点只在这里读一次布局（`getBoundingClientRect`），之后都读 SVG 属性。
 */
function drawTargetLines(actor, targets, now = stageNow()) {
  const shell = $("table-shell");
  const layer = $("t-target-lines");
  if (!shell || !layer || actor === null || actor === undefined) return [];
  const fromNode = heroBlockFor(actor);
  if (!fromNode) return [];
  const shellRect = rectOf(shell);
  const designUnit = designUnitOf(shell);
  const from = centerInShell(fromNode, shellRect);
  const drawn = [];
  for (const target of targets || []) {
    if (target === null || target === undefined) continue;
    if (Number(target) === Number(actor)) continue;
    if (!seatAlive(target)) continue;
    const toNode = heroBlockFor(target);
    if (!toNode) continue;
    const to = centerInShell(toNode, shellRect);
    const node = targetLineNode(from, to, target, actor, designUnit);
    layer.appendChild(node);
    const line = {
      from: Number(actor),
      to: Number(target),
      node,
      enteredAt: Number(now) || 0,
    };
    state.targetLines.push(line);
    drawn.push(line);
  }
  scheduleTargetLines(now);
  return drawn;
}

/* ------------------------------------------------------------------ 行动者高亮 / 体力闪 */

/** 决策点的行动者：座位描边 0.5s（类住 DOM，时长住 CSS；到点摘类）。 */
function markActing(seat) {
  if (seat === null || seat === undefined) return;
  const node = seatNodeFor(seat);
  if (!node) return;
  actingState = { seat: Number(seat), until: stageNow() + ACTING_HIGHLIGHT_MS * animScale() };
  // 连续两个决策点：先摘类、读一次布局强制重算，再挂回去——CSS 动画才会重新起跑。
  // 读 `offsetWidth` 在烟测垫片里是 `undefined`（没有布局），不会抛错。
  node.classList.remove("acting");
  if (typeof node.offsetWidth === "number") void node.offsetWidth;
  node.classList.add("acting");
  if (actingTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(actingTimer);
    actingTimer = null;
  }
  actingTimer = window.setTimeout(() => {
    actingTimer = null;
    const current = actingState ? seatNodeFor(actingState.seat) : null;
    if (current) current.classList.remove("acting");
    actingState = null;
  }, Math.max(0, ACTING_HIGHLIGHT_MS * animScale()));
}

function clearHpFlashClasses(block) {
  block.classList.remove("hp-flash", "hp-flash-down", "hp-flash-up");
}

/** 体力变化：武将块闪 0.4s，掉血红 / 回复绿（`after` 与 `before` 都来自记录）。 */
function flashHeroHp(seat, before, after) {
  const from = Number(before);
  const to = Number(after);
  if (!Number.isFinite(from) || !Number.isFinite(to) || from === to) return;
  const direction = to < from ? "down" : "up";
  const block = heroBlockFor(seat);
  if (!block) return;
  const key = String(seat);
  const previous = hpFlashes.get(key);
  if (previous && previous.timer !== null) {
    if (window.clearTimeout) window.clearTimeout(previous.timer);
  }
  clearHpFlashClasses(block);
  block.classList.add("hp-flash", direction === "down" ? "hp-flash-down" : "hp-flash-up");
  const timer = window.setTimeout(() => {
    hpFlashes.delete(key);
    const current = heroBlockFor(seat);
    if (current) clearHpFlashClasses(current);
  }, Math.max(0, HP_FLASH_MS * animScale()));
  hpFlashes.set(key, { direction, timer });
}

/** 座位 / 武将块在 commit 时会被重建：按仍在播的状态补类（0.5s / 0.4s 不被一次重绘截断）。
 *
 * 目标线不重建，但换会话 / 重绘时也顺手收一次寿命（判定仍只走 :func:`targetLineLane`）：
 * 这样断线重连后画半截的线不会跨过整批 commit 留在桌上。
 */
function syncTransientEffects() {
  renderTargetLines(stageNow());
  if (actingState && stageNow() < actingState.until) {
    const node = seatNodeFor(actingState.seat);
    if (node) node.classList.add("acting");
  }
  for (const [seat, entry] of hpFlashes) {
    const block = heroBlockFor(seat);
    if (!block) continue;
    block.classList.add("hp-flash", entry.direction === "down" ? "hp-flash-down" : "hp-flash-up");
  }
}

/** 换会话 / 测试收尾：撤掉目标线、行动者描边与体力闪，不给下一局留残影。 */
function clearTransientEffects() {
  clearTargetLines();
  if (actingTimer !== null) {
    if (window.clearTimeout) window.clearTimeout(actingTimer);
    actingTimer = null;
  }
  actingState = null;
  for (const [seat, entry] of hpFlashes) {
    if (entry.timer !== null && window.clearTimeout) window.clearTimeout(entry.timer);
    const block = heroBlockFor(seat);
    if (block) clearHpFlashClasses(block);
  }
  hpFlashes.clear();
  for (const seatNode of seatNodes()) {
    seatNode.classList.remove("acting");
    const block = descendantWithClass(seatNode, "hero-block");
    if (block) clearHpFlashClasses(block);
  }
  // 自己的武将块走稳定 id（烟测垫片不建静态 HTML 的父子树；真页面里两条路同一节点）。
  const selfBlock = $("t-self-hero");
  if (selfBlock) clearHpFlashClasses(selfBlock);
}

/** 事件回放的一步对「演出」的贡献（牌桌专属；回放页不走这里，D14）。
 *
 * - `action` 记录 = 决策点：高亮应答者（应答者来自它前面的 query 记录）；
 * - `USE_DESIGNATE_TARGET` = 决策点 + 画出整份目标表；
 * - `hp_change` = 武将块闪红 / 绿；
 * - 其余（含全部结算类时机）**什么也不做**：结算期的 `actor` 常是目标，高亮会误导（D13）。
 */
function applyRecordEffects(record, querySeat) {
  if (!record) return;
  if (record.kind === "action") {
    markActing(querySeat);
    return;
  }
  if (record.kind === "delta" && record.delta_type === "hp_change") {
    flashHeroHp(record.seat, record.before, record.after);
    return;
  }
  if (record.kind !== "timing" || record.timing !== "USE_DESIGNATE_TARGET") return;
  markActing(record.actor);
  drawTargetLines(record.actor, record.targets || []);
}
