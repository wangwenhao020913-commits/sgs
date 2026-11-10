/* SGS 前端 · 牌桌动效层（U3-03 / spec D8.1 / D13）：使用牌目标线、行动者高亮、体力闪，
 * 以及技能名飘字 / 白光（U3-04 / D9）。**只此一职**——伤害演出与音效都已搬走。
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
 * - **音效开关不在这份文件里**（damage-fx 票 08）：顶栏「♪」的状态机 / `localStorage` 记忆 /
 *   `?audiotest=1` 住在 `sfx-ui.js`。
 * - **伤害演出也不在这份文件里**（damage-fx 票 09）：排播 / 座位几何 / 画布生命周期住在
 *   `damage-fx.js`。反过来是 `damage-fx.js` 往本文件**借三个**读布局的小工具（`rectOf` /
 *   `heroBlockFor` / `descendantWithClass` 由本文件定义，`stageNow` 由 `session.js` 提供），
 *   即依赖方向只有「伤害 → 动效」。动效层对伤害层**零调用**：座位重建后的版式重检由
 *   `table.js:renderTable` 在重绘末尾显式调 `damageDropStalePlays()`，换局清场由
 *   `session.js:commitView` 显式调 `clearDamageFx()`（这两处原来是本文件里的反向钩子）。
 * - 页面顺序：本文件 → `damage-fx.js` → … → `fx-core.js` → `sfx.js` → `sfx-ui.js` →
 *   `main.js`。
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

/** 技能名 / 白光的时长与设计单位（U3-04 / D9；CSS 里是同一份裁定）。 */
const SKILL_FLOAT_MS = 2000;
const SKILL_FLOAT_GAP_UNITS = 8;
const SKILL_FLOAT_STACK_UNITS = 14;
const SKILL_SWEEP_MS = 600;
const SKILL_SWEEP_STAGGER_MS = 120;

const SVG_NS = "http://www.w3.org/2000/svg";

/** `setTimeout` 归零也不影响测试：到点重绘的计时器只触发 :func:`renderTargetLines`。 */
let targetLineTimer = null;

/** 行动者高亮：`{seat, until}`。座位在 commit 时会重建，故重绘后按它补类。 */
let actingState = null;
let actingTimer = null;

/** 正在闪的武将块：`seat → {direction, timer}`（同一座位连续掉血 / 回复不叠计时器）。 */
const hpFlashes = new Map();

/** 技能名飘字（D9）：`seat → [{node, enteredAt, timer}]`；同一武将块 2s 内的后一条
 *  起点依次抬高 14（**不合并不去重**——两次发动就是两次演出）。 */
const skillFloats = new Map();

/** 白光（D9）：`seat → {until, timer}`；武将块在 commit 时会被重建，重绘后按它补类。 */
const skillSweeps = new Map();

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

/** 目标还活着吗？读的是**牌桌正在渲染的那份视图**（`tableViewForRender()`，D11）。
 *
 * 回放期间那是逐事件派生的 `state.liveView`，不是上一次提交的 `state.view`：同一批里
 * 刚阵亡的座位只在 live view 里 `alive === false`，读旧快照会把线画到已死的座位上。
 * 存活是服务端已下发的公开事实，前端不自己推死亡。
 */
function seatAlive(seat) {
  const view = tableViewForRender();
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

/* ------------------------------------------------------------ 技能名 / 白光（U3-04 / D9） */

/** 技能名飘字：在发动者武将块正上方 8 设计单位起飘，2s 内上浮 20 并淡出（D9）。
 *
 * 同一武将块 2s 内的多条**不合并、不去重**：后一条的起点再抬高 14 设计单位，节点各自
 * 保留（`data-skill-stack` 是这条口径的可断言读法）。位置只在创建时读一次布局：
 * 与目标线一致，已经在播的飘字不随窗口尺寸变化重算。
 */
function showSkillFloat(seat, name, now = stageNow()) {
  const layer = $("t-skill-floats");
  const shell = $("table-shell");
  const block = heroBlockFor(seat);
  if (!layer || !shell || !block) return null;
  const key = String(seat);
  const active = (skillFloats.get(key) || []).filter(
    (entry) => now - Number(entry.enteredAt) < SKILL_FLOAT_MS,
  );
  const stack = active.length;
  const node = document.createElement("div");
  node.className = "skill-float";
  node.classList.add("skill-float");
  node.setAttribute("data-skill-seat", key);
  node.setAttribute("data-skill-stack", String(stack));
  node.textContent = name;
  const layerRect = rectOf(layer);
  const blockRect = rectOf(block);
  const designUnit = designUnitOf(shell);
  const left = blockRect.left - layerRect.left + (Number(blockRect.width) || 0) / 2;
  const top = blockRect.top - layerRect.top
    - (SKILL_FLOAT_GAP_UNITS + stack * SKILL_FLOAT_STACK_UNITS) * designUnit;
  node.style.left = `${left}px`;
  node.style.top = `${top}px`;
  layer.appendChild(node);
  const entry = { node, enteredAt: now, timer: null };
  entry.timer = window.setTimeout(() => {
    if (node.parentNode) node.parentNode.removeChild(node);
    const kept = (skillFloats.get(key) || []).filter((item) => item !== entry);
    if (kept.length) skillFloats.set(key, kept);
    else skillFloats.delete(key);
  }, Math.max(0, SKILL_FLOAT_MS * animScale()));
  active.push(entry);
  skillFloats.set(key, active);
  return node;
}

/** 白光：武将块扫过一道白光 0.6s；粗 8 / 细 3，细带晚 0.12s（CSS 的伪元素）。
 *
 * 这里只负责挂 / 摘 `.skill-sweep` 类（可断言的读法）；几何与时长住在 CSS。同一武将块
 * 连续发动时先摘类、强制一次重排，再挂回去——CSS 动画才会重新起跑。
 */
function flashSkillSweep(seat, now = stageNow()) {
  const block = heroBlockFor(seat);
  if (!block) return;
  const key = String(seat);
  const previous = skillSweeps.get(key);
  if (previous && previous.timer !== null && window.clearTimeout) {
    window.clearTimeout(previous.timer);
  }
  block.classList.remove("skill-sweep");
  if (typeof block.offsetWidth === "number") void block.offsetWidth;
  block.classList.add("skill-sweep");
  // 细带晚 0.12s 起跑，故类的寿命要把这段错开算进去（否则会在细带播完前摘掉）。
  const duration = (SKILL_SWEEP_MS + SKILL_SWEEP_STAGGER_MS) * animScale();
  const timer = window.setTimeout(() => {
    skillSweeps.delete(key);
    const current = heroBlockFor(seat);
    if (current) current.classList.remove("skill-sweep");
  }, Math.max(0, duration));
  skillSweeps.set(key, { until: now + duration, timer });
}

/** 一条 `skill_events` 的演出：飘技能名 + 白光；只有 `targets` 非空才画线（D9 / D8.2）。
 *
 * 制衡 / 贯石斧 / 青龙偃月刀 / 寒冰剑这类没有指定目标的发动只飘名 + 白光；使用牌不产
 * `skill_events`（转化技最终走 UseCard，记录流里没有"技能发动"这条事实）。
 */
function playSkillEvent(event, now = stageNow()) {
  if (!event) return;
  if (event.seat === null || event.seat === undefined) return;
  const seat = Number(event.seat);
  if (!Number.isFinite(seat)) return;
  const name = String(event.name || "").trim();
  if (name) showSkillFloat(seat, name, now);
  flashSkillSweep(seat, now);
  const targets = Array.isArray(event.targets) ? event.targets : [];
  if (targets.length) drawTargetLines(seat, targets, now);
}

/** 把 `seq` 及之前的技能发动事件放出来（按记录流顺序）。
 *
 * `state.skillEvents` 是随视图下发的**待触发**队列：回放走到某条记录的 `seq` 才演出，
 * 于是技能名 / 白光 / 线在发动那一步出现，而不是响应一到就全部同时出现（D15）。
 * `seq = Infinity` = 没有回放（纯刷新）或回放收尾：立即触发。
 */
function fireSkillEventsThrough(seq = Infinity) {
  const events = state.skillEvents || [];
  if (!events.length) return;
  const threshold = seq === null || seq === undefined ? Infinity : Number(seq);
  const due = [];
  const kept = [];
  for (const event of events) {
    const eventSeq = Number(event && event.seq);
    if (!Number.isFinite(eventSeq) || eventSeq <= threshold) due.push(event);
    else kept.push(event);
  }
  if (!due.length) return;
  state.skillEvents = kept;
  for (const event of due) playSkillEvent(event);
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
  for (const [seat, entry] of skillSweeps) {
    if (stageNow() >= entry.until) continue;
    const block = heroBlockFor(seat);
    if (block && !block.classList.contains("skill-sweep")) block.classList.add("skill-sweep");
  }
  // damage-fx 票 09：这里**不再**顺手调伤害层的 `damageDropStalePlays()`（那是「动效 → 伤害」
  // 的反向钩子，会把两份文件重新绑在一起）。同一句话现在由重绘的调用方说：
  // `table.js:renderTable` 在本函数之后显式调它。
}

/** 换会话 / 测试收尾：撤掉目标线、行动者描边、体力闪、技能名与白光，不给下一局留残影。 */
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
  for (const [seat, entry] of skillSweeps) {
    if (entry.timer !== null && window.clearTimeout) window.clearTimeout(entry.timer);
    const block = heroBlockFor(seat);
    if (block) block.classList.remove("skill-sweep");
  }
  skillSweeps.clear();
  for (const entries of skillFloats.values()) {
    for (const entry of entries) {
      if (entry.timer !== null && window.clearTimeout) window.clearTimeout(entry.timer);
      const node = entry.node;
      if (node && node.parentNode) node.parentNode.removeChild(node);
    }
  }
  skillFloats.clear();
  const floatLayer = $("t-skill-floats");
  if (floatLayer) floatLayer.innerHTML = "";
  // 换会话后上一批的待触发事件不属于新局（换会话的清空同时写在 applyView 里，这里兜底）。
  state.skillEvents = [];
  // damage-fx 票 09：伤害队列与覆盖层不在这里收——`session.js:commitView` 在本函数之后显式
  // 调 `damage-fx.js` 的 `clearDamageFx()` 并清 `state.damageEvents`（本文件不再认识那一层）。
  for (const seatNode of seatNodes()) {
    seatNode.classList.remove("acting");
    const block = descendantWithClass(seatNode, "hero-block");
    if (block) {
      clearHpFlashClasses(block);
      block.classList.remove("skill-sweep");
    }
  }
  // 自己的武将块走稳定 id（烟测垫片不建静态 HTML 的父子树；真页面里两条路同一节点）。
  const selfBlock = $("t-self-hero");
  if (selfBlock) {
    clearHpFlashClasses(selfBlock);
    selfBlock.classList.remove("skill-sweep");
  }
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
