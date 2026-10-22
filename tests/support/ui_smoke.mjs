/* M4 前端渲染烟测（一次性工作产物，不入库）。
 *
 * 浏览器工具在这台机器上不可用（无 `bsk`、无 Chromium、无 cargo），所以这里用
 * **最小 DOM 垫片 + node:vm** 把 `tools/sgs_web/static/` 下的**真脚本**跑起来
 * （按 `index.html` 的 `<script src>` 顺序拼接，与页面共用同一份清单——U1-00），
 * 喂**真的服务器响应**，断言它：
 *   ① 三个页面都能渲染（座位、手牌、询问候选、事件流、回放）；
 *   ② 每类询问的渲染路径**不抛错**、且都能提交；
 *   ③ 提交的 Action **原样取自** `legal_actions`（前端不自己拼 Action——`04` §4.1 的纪律）；
 *   ④ 对手不出现牌面（不泄露）。
 *
 * 用法：`node tests/support/ui_smoke.mjs <base-url>`（由 `tests/test_web.sh` 在起好服务后调用）。
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const BASE = process.argv[2] || "http://127.0.0.1:8100";

const post = async (p, body) => {
  const response = await fetch(`${BASE}${p}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!response.ok) throw new Error(`${p} → HTTP ${response.status}`);
  return response.json();
};
const get = async (p) => (await fetch(`${BASE}${p}`)).json();

/* ----------------------------------------------------------------- DOM 垫片 */

class ClassList {
  constructor() { this.set = new Set(); }
  add(...n) { n.forEach((x) => this.set.add(x)); }
  remove(...n) { n.forEach((x) => this.set.delete(x)); }
  toggle(x, on) { if (on === undefined) { this.set.has(x) ? this.set.delete(x) : this.set.add(x); } else if (on) { this.set.add(x); } else { this.set.delete(x); } }
  contains(x) { return this.set.has(x); }
}

class El {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase(); this.id = id; this.children = []; this.listeners = {};
    this.parentNode = null;
    this.classList = new ClassList(); this._text = ""; this._html = ""; this.value = "";
    this.hidden = false; this.disabled = false; this.type = ""; this.className = "";
    // 真 DOM 的 `element.style` 是个可写对象；垫片补一个扁平副本，供断言"写进去了什么"。
    this.style = {};
    // 真 DOM 的 `<input type=checkbox>` 缺省未勾选；前端读的是 `.checked`（不是 `.value`）。
    this.checked = false;
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent || c.text()).join(""); }
  set textContent(v) { this._text = String(v); this._html = ""; this._detachChildren(); }
  get innerHTML() { return this._html + this.children.map((c) => c.dump()).join(""); }
  // 垫片的 innerHTML 赋值要**真的清空子节点**（真 DOM 就是这么干的；不清空会造出
  // "元素累积"这种假 bug —— 第一版垫片在这里骗过我一次）。
  set innerHTML(v) { this._html = String(v); this._detachChildren(); this._text = ""; }
  _detachChildren() {
    for (const child of this.children) child.parentNode = null;
    this.children = [];
  }
  appendChild(c) {
    // 真 DOM 的 appendChild = **移动**（不是复制）。模态把 `#t-options` 搬进正文、
    // 关闭再搬回来，若垫片只 push，会造出"同一个节点在两个父节点的 children 里"的假象。
    if (c && c.parentNode) {
      const at = c.parentNode.children.indexOf(c);
      if (at >= 0) c.parentNode.children.splice(at, 1);
    }
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  addEventListener(k, fn) { (this.listeners[k] ||= []).push(fn); }
  // 静态节点（U1-01 起自己的座位块常驻 DOM）用 `onclick` 属性覆盖式绑定：
  // 真 DOM 里它是"同一个处理器"，垫片把它映射到 listeners.click，语义一致。
  set onclick(fn) { this.listeners.click = fn ? [fn] : []; }
  get onclick() { return (this.listeners.click || [])[0] || null; }
  // U2-05：座位是 `<div>`，可点时用 `onkeydown` 补键盘入口（真 DOM 的同一个处理器）。
  set onkeydown(fn) { this.listeners.keydown = fn ? [fn] : []; }
  get onkeydown() { return (this.listeners.keydown || [])[0] || null; }
  setAttribute(name, value) { (this.attrs ||= {})[name] = String(value); }
  getAttribute(name) { return (this.attrs || {})[name]; }
  removeAttribute(name) { if (this.attrs) delete this.attrs[name]; }
  querySelector(selector) {
    const tag = String(selector).toLowerCase();
    return this.children.find((c) => c.tagName.toLowerCase() === tag) || null;
  }
  dump() { return `<${this.tagName.toLowerCase()}>${this._html}${this.children.map((c) => c.dump()).join("")}</${this.tagName.toLowerCase()}>`; }
  scrollIntoView() {}
  get lastChild() { return this.children[this.children.length - 1] || null; }
  text() { return (this._html || this._text).replace(/<[^>]*>/g, "") + this.children.map((c) => c.text()).join(""); }
}

const byId = new Map();
const el = (tag, id) => { const n = new El(tag, id); if (id) byId.set(id, n); return n; };
const html = readFileSync("tools/sgs_web/static/index.html", "utf8");
for (const m of html.matchAll(/<(input|select|button|span|div|ol|section|main|form|p|h1|h2|h3|nav|code|a)\b[^>]*\bid="([^"]+)"/g)) {
  const node = el(m[1], m[2]);
  if (m[1] === "input") node.value = "0";
  if (m[1] === "main") node.hidden = m[2] !== "view-lobby";
}
for (const id of ["in-players", "in-ai", "in-seat", "in-timeout", "in-seed"]) if (!byId.has(id)) el("input", id);
// `<select>` 的初值 = 带 `selected` 的那个 option（真 DOM 语义）；垫片不解析 option 结构，
// 故这里给一个显式初值，由下方 `setSelect` 在测试里改。
byId.get("t-log-mode").value = "simple";
byId.get("r-log-mode").value = "simple";
Object.assign(byId.get("in-players"), { value: "8" });
Object.assign(byId.get("in-ai"), { value: "heuristic" });
Object.assign(byId.get("in-seat"), { value: "0" });
Object.assign(byId.get("in-timeout"), { value: "0" });
Object.assign(byId.get("in-seed"), { value: "20261012" });

const documentListeners = {};
const documentShim = {
  getElementById: (id) => byId.get(id) || (byId.set(id, el("div", id)), byId.get(id)),
  createElement: (tag) => new El(tag),
  // 模态的 ESC 关闭挂在 document 的 keydown 上；垫片记下来交给用例显式触发。
  addEventListener: (name, fn) => { (documentListeners[name] ||= []).push(fn); },
};
const fireDocument = (name, event) => (documentListeners[name] || []).forEach((fn) => fn(event));

/* -------------------------------------------------- 只记录前端发出的请求 */

const calls = [];
const realFetch = globalThis.fetch;
/* 两个**一次性注入**（W6-07 的 409 用例）：让"服务端返回 409 + 刷新取回一份给不出规格的
 * 现状"这条只在真实竞态里出现的路径，也能在烟测里被跑到。注入用完即清。 */
let staleAnswerStatus = null;   // 下一次 `/answer` 直接回这个状态码（409）
let nextGetPending = null;      // 下一次 `GET /api/sessions` 把 `pending` 换成这一份
globalThis.fetch = async (path, options) => {
  if (typeof path === "string" && path.startsWith("/api/")) {
    calls.push({ path, body: options && options.body ? JSON.parse(options.body) : null });
    if (staleAnswerStatus && path.includes("/answer")) {
      const status = staleAnswerStatus;
      staleAnswerStatus = null;
      const text = JSON.stringify({ error: "这个询问已过期" });
      return { ok: false, status, text: async () => text, json: async () => JSON.parse(text) };
    }
    const response = await realFetch(`${BASE}${path}`, options);
    if (nextGetPending && !(options && options.method && options.method !== "GET")) {
      const body = await response.json();
      const pending = nextGetPending;
      nextGetPending = null;
      body.pending = pending;
      const text = JSON.stringify(body);
      return { ok: response.ok, status: response.status, text: async () => text, json: async () => JSON.parse(text) };
    }
    return response;
  }
  return realFetch(path, options);
};

/* 计时器垫片：**只排队、不自动跑**。既有用例仍由 `flushPlayback()` 一次性冲刷；
 * 回放 play/pause 的用例显式跑一条，才能断言"暂停后计时器被清掉、播放推进只留一个"。 */
const pendingTimers = new Map();
let nextTimerId = 1;
const runNextTimer = () => {
  const entry = pendingTimers.entries().next().value;
  if (!entry) return false;
  pendingTimers.delete(entry[0]);
  entry[1]();
  return true;
};
const windowShim = {
  setTimeout: (fn) => {
    const id = nextTimerId;
    nextTimerId += 1;
    pendingTimers.set(id, fn);
    return id;
  },
  clearTimeout: (id) => { pendingTimers.delete(id); },
  setInterval: () => 0,
};

const context = vm.createContext({
  document: documentShim,
  window: windowShim,
  fetch: globalThis.fetch,
  performance: { timeOrigin: Date.now() },
  console, Date, JSON, Object, Array, Number, String, Math, encodeURIComponent,
});
context.globalThis = context;
/* 加载契约（U1-00）：脚本清单**只认** index.html 的 `<script src>` 顺序。
 * 页面怎么加载，这里就按同样的顺序加载——不在测试里另维护一份文件名清单。
 * 每份脚本**单独** `runInContext`（不拼接）：这与浏览器的经典脚本语义一致——
 * 同一个 context 的全局词法作用域跨脚本可见，而每份脚本自己编译，于是顶层
 * `const` 重名、以及 `main.js` 末尾 `wire()` 的执行顺序都会被真正走到。 */
const scriptSrcs = [...html.matchAll(/<script\b[^>]*\bsrc="([^"]+)"/g)].map((match) => match[1]);
if (!scriptSrcs.length) throw new Error("index.html 没有 <script src> 清单，无法加载前端");
for (const src of scriptSrcs) {
  vm.runInContext(readFileSync(`tools/sgs_web/static/${src}`, "utf8"), context, { filename: src });
}
const bridge = readFileSync("tests/support/ui_bridge.js", "utf8");
vm.runInContext(bridge, context, { filename: "ui_bridge.js" });
const ui = context.__bridge;

/* ------------------------------------------------------------------ 断言工具 */

let failures = 0;
const check = (label, ok, detail = "") => {
  if (ok) console.log(`PASS ${label}${detail ? ` — ${detail}` : ""}`);
  else { failures += 1; console.log(`FAIL ${label}${detail ? ` — ${detail}` : ""}`); }
};
const $ = (id) => documentShim.getElementById(id);
/** 触发一次 click 并**等它跑完**：右上动作 / 取消里发请求的处理器是 async 的，
 *  不等完就进下一个块，会把下一个块撞进"请求在飞"的冻结态（U2-03 起四键会冻结）。 */
const clickAndWait = async (node, event = null) => {
  for (const fn of node.listeners.click || []) await fn(event || { target: node });
};
/** 候选行的文本指纹（U2-03 起界面**不再**把 `legal_actions` 逐条铺成按钮）：
 *  任何一条命中都说明旧的逃生梯又长回来了。 */
const CANDIDATE_ROW_PATTERN = /发动技能 |选项：|选择座位 |使用【|弃牌（\d|排列（\d/;
const candidateRowButtons = () => $("t-options").children.filter(
  (node) => node.tagName === "BUTTON" && CANDIDATE_ROW_PATTERN.test(node.textContent || ""),
);
/** 牌桌座位节点：对手在 `#t-seats` 环上，自己在底部 `#t-self-seat`（U1-01 起不在环上）。 */
const seatNodes = () => [...$("t-seats").children, $("t-self-seat")];
const seatBox = (seat) => seatNodes().find(
  (node) => node.getAttribute("data-seat") === String(seat)) || null;
/** 自己装备区里的第一张牌（就地高亮的用例共用一处查找）。
 *
 * U1-02 起自己的装备区是**固定四槽**（武器 / 防具 / 攻马 / 防马），槽位节点自己带
 * `data-card-id`；这里找"第一个装了牌的槽"，而不是第一个槽（空槽不该被点）。 */
const selfEquipChip = () => {
  const row = $("t-self-zones").children.find((c) => c.getAttribute("data-zone") === "equip");
  return row ? row.children.find((slot) => slot.getAttribute("data-card-id")) || null : null;
};
const setSelect = (id, value) => {
  const node = $(id);
  node.value = value;
  (node.listeners.change || []).forEach((fn) => fn({ target: node }));
};
/** 重开一局并把它灌进界面（测试之间必须各自干净——状态污染会让断言指向上一局）。 */
const openSession = async (body) => {
  ui.state.sessionId = body.session_id;
  ui.state.view = null;
  ui.state.logRecords = [];
  ui.state.narrative = [];
  ui.state.narrativeShown = 0;
  ui.state.cursor = -1;
  ui.state.logSeq = 0;
  ui.resetSelection();
  $("t-log").innerHTML = "";
  ui.applyView(body);
  return body;
};
const newSession = async (seed, extra = {}) => openSession(await post("/api/sessions", {
  player_count: 8, ai: "random", human_seat: 0, timeout_seconds: null, seed, ...extra,
}));
const logText = () => $("t-log").text();
const logCount = () => $("t-log").children.length;

/* ------------------------------------------------- ⓪ 开局点将（票 19）
 *
 * 这里**走真的开局表单**（`#lobby-form` 的 submit → `startGame` → `POST /api/sessions`），
 * 不是直接调 `post`：判据是"用户点得出来"，接线断在 `startGame` 里也必须红。
 * 请求体从烟测自己的 `calls`（`/api/` 拦截器）里读，故"关了开关 ⇒ 不带 `my_hero`"
 * 这条是**线上事实**而不是读 `state`。
 */

const submitLobby = async () => {
  const form = $("lobby-form");
  for (const fn of form.listeners.submit || []) await fn({ preventDefault() {} });
  return ui.state.view;
};
const tickPointjiang = (on) => {
  const box = $("in-pointjiang");
  box.checked = on;
  (box.listeners.change || []).forEach((fn) => fn({ target: box }));
};
/** 这一段之后新发生的建局请求体（`calls` 里也可能有烟测自己发的 `post`）。 */
const sessionBodiesSince = (from) =>
  calls.slice(from).filter((call) => call.path === "/api/sessions").map((call) => call.body);

await ui.loadHeroCatalog();
check("⓪1 点将列表拉到了启用池", ui.state.heroCatalog.length > 0, `${ui.state.heroCatalog.length} 名`);
check("⓪2 列表铺进了「我的武将」下拉",
  $("in-my-hero").children.length === ui.state.heroCatalog.length,
  `选项 ${$("in-my-hero").children.length} / 池 ${ui.state.heroCatalog.length}`);
check("⓪3 开关缺省是关的（不点将 = 老路径）", $("in-pointjiang").checked === false);

$("in-players").value = "5";
const picked = ui.state.heroCatalog[2];
tickPointjiang(true);
check("⓪4 开关开着时「我的武将」可选", $("in-my-hero").disabled === false);
$("in-my-hero").value = picked.id;
$("in-seed").value = "2468";
let mark = calls.length;
const pinned = await submitLobby();
check("⓪5 点将：我的座位（0）就是我点的那名武将",
  pinned.heroes[0] === picked.id, `点 ${picked.id} / 实得 ${pinned.heroes[0]}`);
check("⓪6 点将：本局其它 AI 不选我已点的武将",
  !pinned.heroes.slice(1).includes(picked.id), pinned.heroes.join(","));
check("⓪7 点将：建局请求体带上了 my_hero",
  sessionBodiesSince(mark).pop().my_hero === picked.id,
  JSON.stringify(sessionBodiesSince(mark)));

mark = calls.length;
const again = await submitLobby();
check("⓪8 同 seed + 同点将 ⇒ 同一份发将（可复现）",
  JSON.stringify(again.heroes) === JSON.stringify(pinned.heroes),
  `${again.heroes.join(",")} vs ${pinned.heroes.join(",")}`);

tickPointjiang(false);
check("⓪9 关了开关时「我的武将」被置灰", $("in-my-hero").disabled === true);
mark = calls.length;
await submitLobby();
const randomBody = sessionBodiesSince(mark).pop();
check("⓪10 关了开关 ⇒ 建局不带 my_hero（回到随机选择模式）",
  !randomBody.my_hero, JSON.stringify(randomBody));

// "回到随机"的正面判据：同一个 seed 下座位 0 不再被钉成我点的那名，且换 seed 会跟着变。
// （单看一次"随机结果不等于我点的那名"是撞运气——池有 16 名，换 seed 扫一遍才立得住。）
const seatZero = [];
for (const seed of ["2468", "101", "202", "303", "404", "505", "606"]) {
  $("in-seed").value = seed;
  seatZero.push((await submitLobby()).heroes[0]);
}
check("⓪11 关了开关 ⇒ 六个 seed 里出现过不是我已点的那名（没被钉死）",
  seatZero.some((hero) => hero !== picked.id), `点将 ${picked.id} / 实得 ${seatZero.join(",")}`);
check("⓪12 关了开关 ⇒ 我的武将随 seed 变（真的是随机发将）",
  new Set(seatZero).size > 1, seatZero.join(","));

/* ------------------------------------------- ⓪b 牌堆档位（M5-01b）
 *
 * 同样走**真的开局表单**（submit → startGame → POST /api/sessions）。选项来自服务端
 * `GET /api/decks`（前端不抄第二份档位文案表），请求体从烟测自己的 `calls` 拦截器里读，
 * 响应体里的 `deck_profile` 是会话真档位（`GameRegistry.view` 下发）。
 */

await ui.loadDeckCatalog();
const deckOptions = $("in-deck-profile").children;
check("⓪13 牌堆档位选项来自服务端（标准 108 / 军争 160）",
  calls.some((call) => call.path === "/api/decks")
    && deckOptions.length === 2
    && deckOptions.some((option) => option.value === "standard_108")
    && deckOptions.some((option) => option.value === "standard_ex_160"),
  deckOptions.map((option) => `${option.value}:${option.textContent}`).join(","));
check("⓪14 档位缺省选中 standard_108（默认对局不变）",
  $("in-deck-profile").value === "standard_108", $("in-deck-profile").value);

mark = calls.length;
await submitLobby();
ui.flushPlayback();  // startGame 里的 applyView 走真计时器（垫片只排队）；冲完才提交新视图
const standardDeck = ui.state.view;
check("⓪15 未选档也把 standard_108 带进建局请求体",
  sessionBodiesSince(mark).pop().deck_profile === "standard_108",
  JSON.stringify(sessionBodiesSince(mark)));
check("⓪16 未选档建出的仍是 108 档",
  standardDeck.deck_profile === "standard_108", String(standardDeck.deck_profile));

setSelect("in-deck-profile", "standard_ex_160");
check("⓪17 切到军争 160 后选择器的值是 160 档",
  $("in-deck-profile").value === "standard_ex_160");
mark = calls.length;
await submitLobby();
ui.flushPlayback();
const expandedDeck = ui.state.view;
check("⓪18 160 档的建局请求体带 deck_profile",
  sessionBodiesSince(mark).pop().deck_profile === "standard_ex_160",
  JSON.stringify(sessionBodiesSince(mark)));
check("⓪19 开局页能建出 standard_ex_160 的局",
  expandedDeck.deck_profile === "standard_ex_160", String(expandedDeck.deck_profile));
setSelect("in-deck-profile", "standard_108");

/** 推进一条对局到一个干净局面。
 *
 * 取**第一条候选**（`legal_actions[0]`）：这是"与引擎扫描路径一致"的那条路——
 * 界面里那些前置（【南蛮入侵】落入人类手里这类）只在同一条路径上才出现，
 * 走"结束阶段优先"会让对局完全长成另一个样子，前置就永远扫不到。
 * 只有"界面本就要显示将提交什么"时才退到 `selectedOption`。
 */
const advanceOnce = async () => {
  const p = ui.state.view.pending;
  if (p && ui.isHandPick(p)) {
    // 手选询问：界面**只**允许"点一张牌"这条路，所以驱动方也走那条路，
    // 否则重放回不到同一局面（点哪张牌会改变对局，前置就漂了）。
    ui.resetSelection();
    ui.renderTable();
    const index = ui.cardIndex(p);
    const cardId = Object.keys(index)[0];
    if (cardId !== undefined) {
      ui.toggleFocus(Number(cardId));
      const combo = (index[String(cardId)].combos || [])[0] || [];
      for (const seat of combo) {
        const box = $("t-seats").children.find((c) => c.getAttribute("data-seat") === String(seat));
        if (box) (box.listeners.click || []).forEach((fn) => fn({ target: box }));
      }
    }
    if (ui.buildAction() !== null) { await ui.submitAction(); return; }
  }
  ui.resetSelection();
  ui.state.selectedOption = 0;
  await ui.submitAction();
};

/** 重放回扫描时停住的那一步（同一 seed 同一响应序列 ⇒ 同一局面）。 */
const replayTo = async (found) => {
  await newSession(found.seed);
  for (let step = 0; step < found.steps; step += 1) await advanceOnce();
  ui.renderTable();
  ui.renderPending(ui.state.view);
  return found;
};

/** 一遍扫出本次改造需要的三种前置：半可用局面 / 多组合牌 / 目标被迫唯一。
 *
 * 三种都是真的引擎局面（不造假的响应体），且都记下 `steps` 供 :func:`replayTo` 复现。
 */
const scanCases = async () => {
  let partial = null;
  let manual = null;
  let auto = null;
  for (const seed of [1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]) {
    await newSession(seed);
    for (let step = 0; step < 400 && ui.state.view.pending; step += 1) {
      const pending = ui.state.view.pending;
      if (ui.isHandPick(pending)) {
        const hand = ui.state.view.snapshot.self_hand || [];
        const usable = hand.filter((card) => ui.cardSelectable(pending, card.card_id));
        const unusable = hand.filter((card) => !ui.cardSelectable(pending, card.card_id));
        if (!partial && usable.length > 0 && unusable.length > 0) {
          partial = { seed, steps: step, unusable: unusable[0], usable: usable[0] };
        }
      }
      if (pending.kind === "PLAY_PHASE" && pending.actor === 0) {
        const index = ui.cardIndex(pending);
        for (const [cardId, entry] of Object.entries(index)) {
          if (!manual && entry.combos.length > 1 && entry.pool.length > 1) {
            manual = { seed, steps: step, cardId, entry };
          }
          if (!auto && entry.combos.length === 1 && entry.pool.length > 1) {
            auto = { seed, steps: step, cardId, entry };
          }
        }
      }
      if (partial && manual && auto) return { partial, manual, auto };
      await advanceOnce();
    }
  }
  return { partial, manual, auto };
};

/* ------------------------------------------------ ① 建局 + 牌桌/手牌/事件流 */

const created = await post("/api/sessions", {
  player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
});
ui.state.sessionId = created.session_id;
ui.applyView(created);

check("牌桌环上渲染出 7 个对手（自己不在环上）",
  $("t-seats").children.length === 7, `实得 ${$("t-seats").children.length}`);
check("自己在底部独立面板，带 me 标记",
  $("t-self-seat").classList.contains("me")
    && $("t-self-seat").getAttribute("data-seat") === "0",
  `data-seat=${$("t-self-seat").getAttribute("data-seat")} me=${$("t-self-seat").classList.contains("me")}`);
// U1-02：武将块显示的是**中文名 + 势力**（服务端 `snapshot.players[].hero_name`），不是引擎 id。
check("对手座位块显示武将（公开信息）",
  $("t-seats").children[0].text().includes(created.snapshot.players[1].hero_name),
  $("t-seats").children[0].text().replace(/\s+/g, " ").trim().slice(0, 40));
check("自己的武将块显示武将",
  $("t-hero").textContent === created.snapshot.players[0].hero_name, $("t-hero").textContent);
check("手牌渲染出牌面且张数一致", $("t-hand").children.length === created.snapshot.self_hand.length && $("t-hand").children.length > 0, `${$("t-hand").children.length} 张`);
check("对手座位不出现牌面名", !["杀", "闪", "桃", "无懈可击"].some((n) => $("t-seats").children[1].text().includes(n)), $("t-seats").children[1].text().replace(/\s+/g, " ").trim().slice(0, 40));
check("种子已显示", $("t-seed").textContent === String(created.seed), $("t-seed").textContent);
check("事件流已渲染开局记录", $("t-log").children.length > 0, `${$("t-log").children.length} 条`);

// 牌堆 / 弃牌堆数量（服务端 `PlayerView` 早就下发了 `deck_count` 与 `discard`，界面此前没画）。
check(
  "牌堆数量已显示",
  $("t-deck").textContent === String(created.snapshot.deck_count),
  `实得 "${$("t-deck").textContent}"，期望 ${created.snapshot.deck_count}`,
);
check(
  "弃牌堆数量已显示",
  $("t-discard").textContent === String(created.snapshot.discard.length),
  `实得 "${$("t-discard").textContent}"，期望 ${created.snapshot.discard.length}`,
);

/* ================= ㉿ 红测试：阶段可读性 + 事件流噪音（刀 1） =================
 *
 * 两条现在应当**失败**的断言：
 *   ① `回合计` 显示的是 `Phase` 枚举的字面值（`play`），不是中文阶段名；
 *   ② 事件流把每次响应窗口的 `query` + `action` 逐条铺开（一局 268 次 RESPOND），
 *      而阶段时点（`PHASE_*` / `TURN_*`）被推到很远的上方——玩家在牌桌上"看不见自己在哪个阶段"。
 * 修法（刀 1）：`回合计` 走中文映射 + 新增"轮到谁"；事件流默认**重点**模式
 * （隐藏对手的响应往来）并有"全部"开关；新记录自动滚到底。
 */

// 阶段可见性要在"重点"模式下检查（真实游玩时的缺省视图）。
setSelect("t-log-mode", "focus");

check("㉿1a 回合计显示中文阶段名（不是枚举字面值 play）", $("t-phase").textContent === "出牌阶段", `实际 "${$("t-phase").textContent}"`);
const turnText = $("t-turn").textContent;
check("㉿1a 轮到谁已显示", typeof turnText === "string" && turnText.length > 0, `实际 "${turnText}"`);

const logNow = logText();
check("㉿1b 重点模式里能看到阶段时点", /准备阶段|判定阶段|摸牌阶段|出牌阶段|弃牌阶段|结束阶段|回合开始|回合结束/.test(logNow), logNow.replace(/\s+/g, " ").slice(0, 80));
check("㉿1b 重点模式里不再逐条铺对手的响应往来", !/询问 RESPOND → 座位 (?!0\b)/.test(logNow), logNow.replace(/\s+/g, " ").slice(0, 80));

setSelect("t-log-mode", "all");
const logAll = logText();
check("㉿1c 切到全部能看到对手的响应往来", /询问 RESPOND/.test(logAll), logAll.replace(/\s+/g, " ").slice(0, 80));
setSelect("t-log-mode", "focus");

/* ============ ㉿ 红测试：可点的牌 ≠ 可用的牌（刀 2 → 手选版） ============
 *
 * 真界面上发生过：手牌张张可点，点了一张**用不出**的牌之后提交按钮永久灰掉、
 * 界面只说"先选一项，或点一张手牌再提交"——玩家不知道是这张牌不能用于当前询问。
 * 手选之后这两条仍然成立，只是判据从 `cardCandidates` 换成**候选索引**：
 * ① 用不出的牌**不给焦点**（置灰 + 点它不设焦点）；② 说出**为什么**。
 *
 * 这条断言需要一个"手里既有用得出、又有用不出"的**手选**局面（`PLAY_PHASE` / `RESPOND`），
 * 故先扫一个出来——直接拿建局后那个询问会在"唯一候选 = 放弃"的窗口上落空。
 */
const { partial, manual: multi, auto } = await scanCases();

check("㉿2 找到一个前置：手里既有用得出、又有用不出的牌", partial !== null,
  partial ? `seed=${partial.seed} 用不出=${partial.unusable.name} 用得出=${partial.usable.name}` : "16 个种子内没找到");

if (partial) {
  await replayTo(partial);
  ui.toggleFocus(partial.unusable.card_id);
  check("㉿2a 用不出的牌**不成为焦点**", ui.state.focusCardId !== partial.unusable.card_id,
    `focusCardId=${ui.state.focusCardId}`);
  check("㉿2b 用不出的牌被置灰（dis 类）", (() => {
    const chip = $("t-hand").children.find((c) => c.text().includes(partial.unusable.name) && c.classList.contains("dis"));
    return !!chip;
  })(), `手牌里的【${partial.unusable.name}】应当是 dis`);
  check("㉿2c 提交按钮**不可点**", $("btn-submit").disabled === true, `disabled=${$("btn-submit").disabled}`);
  check("㉿2d 给出**为什么**（不是笼统提示）", /不能用于当前询问/.test($("t-choice").textContent), `提示="${$("t-choice").textContent}"`);

  ui.toggleFocus(partial.usable.card_id);
  check("㉿2e 用得出的牌可以成为焦点", String(ui.state.focusCardId) === String(partial.usable.card_id),
    `focusCardId=${ui.state.focusCardId}`);
  ui.clearSelection();
  check("㉿2f 清空选择后按钮不可点（没选中任何东西）", $("btn-submit").disabled === true
    && ui.state.focusCardId === null && ui.state.selectedTargets.length === 0);
} else {
  check("㉿2 跳过：16 个种子内没找到半可用局面", true);
}

/* ============ ㉿ 红测试：技能按钮三态（刀 4 前端片） ============
 *
 * 用户要：每个技能一个按钮；主动技满足条件 = 绿色可点、不满足 = 深灰不可点；
 * 锁定/被动技 = 银灰常暗（由引擎在时机上派发或弹询问）。
 * 本片只渲染色态与可点性——**不新增引擎数据**：清单来自响应体的 `skills`（刀 4 后端片）。
 */
{
  // 先扫出"某个种子的出牌阶段里有技能可发动"，**记下步数**——断言前用 `replayTo` 回到那个局面。
  // （老写法在扫到更早的种子后 `break` 出内层循环、外层又跑到最后一个种子，断言就落在了别的局面上。）
  let skillCase = null;
  for (const seed of [7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67]) {
    await newSession(seed);
    for (let step = 0; step < 400 && ui.state.view.pending; step += 1) {
      const pending = ui.state.view.pending;
      const offered = (ui.state.view.skills || []).filter((s) => s.available);
      if (pending.kind === "PLAY_PHASE" && pending.actor === 0 && offered.length > 0) {
        skillCase = { seed, steps: step, offered };
        break;
      }
      // **必须**与 `replayTo` 走同一条推进路径（`advanceOnce`），否则重放回不到这一步：
      // 这里曾用"优先结束阶段"的策略，`replayTo` 用 `advanceOnce`，两边在出牌阶段点的是
      // 不同的东西 ⇒ 断言的局面比前置少一步，`㉿4c/4d` 因此假红。
      await advanceOnce();
    }
    if (skillCase) break;
  }
  if (skillCase) await replayTo(skillCase);
  else await newSession(7);   // 没找到前置时也留一个带技能的局面

  check("㉿4a 锁定/被动技是银灰且**不可点**（引擎在时机上派发，点了没意义）", (() => {
    const locked = ui.state.view.skills.filter((s) => s.locked);
    if (!locked.length) return true;  // 该武将没有锁定技时不判
    return locked.every((s) => {
      const b = $("t-skills").children.find((c) => c.getAttribute && c.getAttribute("data-skill-id") === s.skill_id);
      return b && b.classList.contains("locked") && b.disabled === true;
    });
  })(), `锁定技 ${ui.state.view.skills.filter((s) => s.locked).map((s) => s.name).join("、") || "（无）"}`);

  check("㉿4b 当前不满足的主动技是深灰且不可点", (() => {
    const off = ui.state.view.skills.filter((s) => !s.locked && !s.available);
    if (!off.length) return true;
    return off.every((s) => {
      const b = $("t-skills").children.find((c) => c.getAttribute && c.getAttribute("data-skill-id") === s.skill_id);
      return b && !b.classList.contains("ready") && b.disabled === true;
    });
  })(), `不可用主动技 ${ui.state.view.skills.filter((s) => !s.locked && !s.available).map((s) => s.name).join("、") || "（无）"}`);

  check("㉿4 每个技能都渲染成一个按钮（清单来自响应体）", (() => {
    const buttons = $("t-skills").children.filter((c) => c.tagName === "BUTTON");
    const skills = ui.state.view.skills || [];
    return skills.length > 0 && buttons.length === skills.length;
  })(), `按钮 ${$("t-skills").children.filter((c) => c.tagName === "BUTTON").length} 个 / 清单 ${(ui.state.view.skills || []).length} 条`);

  check("㉿4 找到一个技能可发动的前置", skillCase !== null, skillCase ? `seed=${skillCase.seed} 可发动=${skillCase.offered.map((s) => s.name).join("、")}` : "10 个种子内没找到");

  if (skillCase) {
    const target = skillCase.offered[0];
    const button = $("t-skills").children.find((c) => c.getAttribute && c.getAttribute("data-skill-id") === target.skill_id);
    check("㉿4c 可发动的技能是绿色高亮且**可点**",
      !!button && button.classList.contains("ready") && button.disabled === false,
      `按钮存在=${!!button} ready=${button && button.classList.contains("ready")} disabled=${button && button.disabled}`);
    if (button && target.preactivated) {
      // W4-2：已翻预发动 ⇒ 点它进入**清单驱动**的本地态（不直接选骨架候选、不提交）。
      (button.listeners.click || []).forEach((fn) => fn({ target: button }));
      check("㉿4d 点了它 = 进入预发动（清单入口），不提交",
        !!ui.state.preactivation && ui.state.preactivation.skillId === target.skill_id
          && ui.state.selectedOption === null,
        `pre=${JSON.stringify(ui.state.preactivation && ui.state.preactivation.skillId)} option=${ui.state.selectedOption}`);
      const beforeCancel = calls.length;
      await ui.cancelOrClear();
      const sent = calls.slice(beforeCancel).find((c) => c.path.includes("/answer"));
      check("㉿4e 取消预发动 = 零 action 提交",
        !sent && ui.state.preactivation === null,
        `sent=${JSON.stringify(sent && sent.body && sent.body.action)}`);
    } else if (button) {
      const beforeClick = ui.state.selectedOption;
      (button.listeners.click || []).forEach((fn) => fn({ target: button }));
      const pending = ui.state.view.pending;
      const index = pending.legal_actions.findIndex((a) => a.kind === "ActivateSkill" && a.skill_id === target.skill_id);
      check("㉿4d 点了它 = 选中那条 ActivateSkill 候选", ui.state.selectedOption === index && index >= 0,
        `selectedOption=${ui.state.selectedOption} want=${index}（点击前 ${beforeClick}）`);
      const before = calls.length;
      await ui.submitAction();
      const sent = calls.slice(before).find((c) => c.path.includes("/answer"));
      const sentAction = sent && sent.body ? sent.body.action : null;
      check("㉿4e 提交的正是那个技能的候选", sentAction !== null && sentAction.kind === "ActivateSkill" && sentAction.skill_id === target.skill_id,
        `sent=${JSON.stringify(sentAction && { kind: sentAction.kind, skill_id: sentAction.skill_id })}`);
    }
  }

  // 装备技能与武将技能**同列**（报障：装备技能此前整条不进清单，界面也就画不出按钮）。
  // 这里直接喂一份含装备技能的清单给**真的** `renderSkills`，只检查渲染与标注；清单本身
  // 由服务端 `build_skills` 提供（其"装备技能也要列"由 pytest 的 test_web.py 钉住）。
  const realSkillsView = ui.state.view;
  ui.renderSkills({ ...realSkillsView, skills: [
    { skill_id: "hero.guanyu.wusheng", name: "武圣", skill_type: "NORMAL", locked: false, carrier: "HERO", available: true },
    { skill_id: "equip.crossbow", name: "诸葛连弩", skill_type: "NORMAL", locked: true, carrier: "EQUIPMENT", available: false },
  ] });
  const mixedButtons = $("t-skills").children.filter((c) => c.tagName === "BUTTON");
  check("㉿4f 装备技能与武将技能同列渲染", mixedButtons.length === 2, `按钮 ${mixedButtons.length} 个`);
  const equipButton = mixedButtons.find((c) => c.getAttribute("data-skill-id") === "equip.crossbow");
  check("㉿4f 装备技能标注为「装备技」且锁定态不可点",
    !!equipButton && equipButton.text().includes("装备技") && equipButton.disabled === true,
    equipButton ? equipButton.text() : "按钮缺失");
  ui.renderSkills(realSkillsView);

  await openSession(await post("/api/sessions", {
    player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
  }));
}

check("㉿M1 找到一个前置：同一张牌有多个目标组合（该手选，不该自动选中）", multi !== null,
  multi ? `seed=${multi.seed} card_id=${multi.cardId} 池=${multi.entry.pool.length} 组合=${multi.entry.combos.length} 条` : "16 个种子内没找到");

if (multi) {
  await replayTo(multi);
  check("㉿M0 重放回到了同一个局面（候选索引一致）", (() => {
    const entry = ui.cardEntry(ui.state.view.pending, multi.cardId);
    return entry !== null && entry.combos.length === multi.entry.combos.length;
  })(), `组合 ${multi.entry.combos.length} 条`);

  ui.toggleFocus(Number(multi.cardId));
  check("㉿M2 点手牌 = 设焦点", String(ui.state.focusCardId) === String(multi.cardId), `focusCardId=${ui.state.focusCardId}`);
  check("㉿M3 有多个合法组合时**不**自动选中", ui.state.selectedTargets.length === 0,
    `selectedTargets=${JSON.stringify(ui.state.selectedTargets)}`);
  check("㉿M4 未选目标时提交按钮不可点", $("btn-submit").disabled === true);

  const targetable = $("t-seats").children.filter((c) => c.classList.contains("targetable"));
  check("㉿M5 合法目标座位被标成可点（**数据**给的类）", targetable.length === multi.entry.pool.length,
    `可点座位 ${targetable.length} 个 / 池 ${multi.entry.pool.length} 个`);

  // 手选路径：点出**第一组组合**要求的那些座位（多目标牌就得点够）
  const want = multi.entry.combos[0];
  for (const seat of want) {
    const box = $("t-seats").children.find((c) => c.getAttribute("data-seat") === String(seat));
    if (box) (box.listeners.click || []).forEach((fn) => fn({ target: box }));
  }
  check("㉿M6 点座位 = 选目标", JSON.stringify(ui.state.selectedTargets) === JSON.stringify(want),
    `已选 ${JSON.stringify(ui.state.selectedTargets)} 想要 ${JSON.stringify(want)}`);
  check("㉿M7 选够后提交按钮可点（组合恰好在候选里）", $("btn-submit").disabled === false);

  const chosen = ui.buildAction();
  check("㉿M8 buildAction() 取到的是候选里那一条（targets 逐字段相同）",
    chosen !== null && JSON.stringify(chosen.targets) === JSON.stringify(want),
    `chosen=${JSON.stringify(chosen && chosen.targets)}`);

  // 再点一次已选座位 = 取消该目标
  const firstSeat = $("t-seats").children.find((c) => c.getAttribute("data-seat") === String(want[0]));
  if (firstSeat) (firstSeat.listeners.click || []).forEach((fn) => fn({ target: firstSeat }));
  check("㉿M9 再点一次已选座位 = 取消该目标", !ui.state.selectedTargets.includes(want[0]),
    `selectedTargets=${JSON.stringify(ui.state.selectedTargets)}`);

  // 点牌一次 = 整张取消
  ui.toggleFocus(Number(multi.cardId));
  check("㉿M10 再点一次牌 = 取消焦点并清空已选目标",
    ui.state.focusCardId === null && ui.state.selectedTargets.length === 0);

  // 走真 HTTP：重选一遍并提交，断言发出去的字节
  ui.toggleFocus(Number(multi.cardId));
  for (const seat of want) {
    const box = $("t-seats").children.find((c) => c.getAttribute("data-seat") === String(seat));
    if (box) (box.listeners.click || []).forEach((fn) => fn({ target: box }));
  }
  const before = calls.length;
  await ui.submitAction();
  const sent = calls.slice(before).find((c) => c.path.includes("/answer"));
  const sentAction = sent && sent.body ? sent.body.action : null;
  check("㉿M11 提交的正是**那一条**候选（targets 逐字段相同）",
    sentAction !== null && JSON.stringify(sentAction.targets) === JSON.stringify(want),
    `sent=${JSON.stringify(sentAction && sentAction.targets)} want=${JSON.stringify(want)}`);
  // 不变量 ④：前端**不合成** Action——`buildAction()` 给回的必须是 `legal_actions` 里
  // **同一个对象**（`Object.is`），不是"一个字段长得一样"的新对象。
  // 只比 JSON 字符串会放过"重建了一份"这种改法。注意 `SelectCards` 那条**故意**是拷贝
  // （引擎只发骨架、picks 由界面补），故此断言只对手选路径成立。
  // （上一步的提交已把询问推进走，故这里重放回同一局面再验。）
  await replayTo(multi);
  ui.toggleFocus(Number(multi.cardId));
  for (const seat of want) {
    const box = $("t-seats").children.find((c) => c.getAttribute("data-seat") === String(seat));
    if (box) (box.listeners.click || []).forEach((fn) => fn({ target: box }));
  }
  const picked = ui.buildAction();
  check("㉿M18 buildAction() 返回的是 legal_actions 里的**同一个对象**（不合成）",
    picked !== null && ui.state.view.pending.legal_actions.includes(picked),
    picked === null ? "buildAction() 返回 null" : `includes 命中=${ui.state.view.pending.legal_actions.includes(picked)}`);
}

/* ============ ㉿ 非手选询问里点手牌：不许崩 ============
 *
 * 真 bug（code-review 的 Standards 轴抓到）：`toggleFocus` 在非手选询问上委托回
 * `toggleCard`，而 `toggleCard` 又委托回 `toggleFocus` ⇒ 在**弃牌 / 排列 / 选择面**这类
 * 旧路径的询问上点任意一张手牌就 `RangeError: Maximum call stack size exceeded`。
 * 手选询问走 picker 分支、碰不到那条路，所以端到端烟测当时全绿也照样漏掉了它。
 */
{
  let nonPick = null;
  for (const seed of [1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]) {
    await newSession(seed);
    for (let step = 0; step < 400 && ui.state.view.pending; step += 1) {
      const pending = ui.state.view.pending;
      const hand = ui.state.view.snapshot.self_hand || [];
      if (!ui.isHandPick(pending) && hand.length > 0) { nonPick = { seed, steps: step }; break; }
      await advanceOnce();
    }
    if (nonPick) break;
  }
  check("㉿N1 找到一个非手选询问的前置（弃牌 / 排列 / 选择面）", nonPick !== null,
    nonPick ? `seed=${nonPick.seed}` : "16 个种子内没找到");
  if (nonPick) {
    await replayTo(nonPick);
    const pending = ui.state.view.pending;
    const cardId = (ui.state.view.snapshot.self_hand || [])[0].card_id;
    let threw = null;
    try { ui.toggleFocus(cardId); } catch (error) { threw = error; }
    check("㉿N2 非手选询问里点手牌**不抛错**（不得互递归）", threw === null,
      threw ? `${threw.constructor.name}: ${String(threw.message).slice(0, 50)}` : "无异常");
    check("㉿N3 非手选询问里点手牌**不设焦点**（那条路由候选列表驱动）",
      ui.state.focusCardId === null && ui.state.selectedTargets.length === 0,
      `focusCardId=${ui.state.focusCardId} targets=${JSON.stringify(ui.state.selectedTargets)}`);
    check("㉿N4 界面仍停在同一询问（点牌没有误提交）",
      ui.state.view.pending === pending && pending.kind === ui.state.view.pending.kind);
  }
}

/* ============ ㉿ 前后端键名是契约：唯一一条带目标的候选必须手选 ============
 *
 * 真 bug（code-review 的 Spec 轴抓到，P0）：服务端发 `min_targets` / `max_targets`，
 * 界面读 `entry.max` ⇒ 恒 `undefined` ⇒ `needsPicker()` 恒假 ⇒ 借刀杀人那种
 * "唯一一条 `UseCard{杀, targets=(4,)}`" 被 `direct` 分支抢先命中，**未点任何东西提交按钮就可点**，
 * 违反 D8「永不自动提交」与 Acceptance 2。这条断言把它钉死：只断言服务端键名不够，
 * 必须断言**界面读得到**。
 */
{
  // **先清掉前一个块残留的选中态**：`renderKind` / `buildAction` 把 `state.selectedOption`
  // 当作"我显式选了这条候选"的输入（前端的 `renderKind` 在它非空时直接返回 `list`）。
  // 本块断言的是**合成的 `lone` 询问**本身该走 `picker`，与前面哪个真局块最后停在什么选择上无关；
  // 不先清，断言就变成 order-dependent（票 13 收窄【反馈】/【流离】条件后，前面的扫种子块落到
  // 别的 seed、留下的选中态不同，本块才暴露出来）。
  ui.resetSelection();
  const lone = {
    kind: "RESPOND",
    prompt: "响应窗口：请使用一张牌，或放弃。",
    actor: 0,
    legal_actions: [{ answers_seq: 1, kind: "UseCard", card: { card_id: 34, name: "杀", type: "slash" }, targets: [4] }],
    candidate_index: {
      "34": {
        card: { card_id: 34, name: "杀", type: "slash" },
        pool: [4, 6, 7, 9], min_targets: 1, max_targets: 1,
        combos: [[4], [6], [7], [9]],
      },
    },
  };
  const entry = ui.cardEntry(lone, 34);
  check("㉿K1 界面按服务端的键名读目标数（max_targets / min_targets）",
    entry.min_targets === 1 && entry.max_targets === 1,
    `min_targets=${entry.min_targets} max_targets=${entry.max_targets}`);
  check("㉿K2 唯一一条带目标的候选 ⇒ needsPicker 为真（不得走 direct）",
    ui.needsPicker(lone) === true && ui.renderKind(lone, lone.legal_actions) === "picker",
    `needsPicker=${ui.needsPicker(lone)} renderKind=${ui.renderKind(lone, lone.legal_actions)}`);
  const saved = ui.state.view;
  ui.state.view = { pending: lone, snapshot: { self_hand: [], players: [], seat: 0 }, human_seat: 0, heroes: {} };
  ui.renderPending(ui.state.view);
  check("㉿K3 未交互前提交按钮**不可点**（D8 永不自动提交）",
    $("btn-submit").disabled === true && ui.buildAction() === null,
    `disabled=${$("btn-submit").disabled} buildAction=${JSON.stringify(ui.buildAction())}`);
  ui.toggleFocus(34);
  check("㉿K4 池 4 / 目标数 1 ⇒ **不**自动选中任何目标",
    ui.state.selectedTargets.length === 0,
    `selectedTargets=${JSON.stringify(ui.state.selectedTargets)}`);
  ui.state.view = saved;
  ui.resetSelection();
}

/* ============ ㉿ 响应窗口：放弃必须是一个**点得到**的入口 ============
 *
 * 报障原文：「响应南蛮入侵时强制出杀，没有放弃选项」。引擎侧 `_respond` **恒追加**
 * `Pass`（`effects/actions.py`），所以候选从来没缺过——缺的是界面入口：手选路径
 * 只画"点一张手牌"，`Pass` 因此不可见，玩家以为必须出牌。
 *
 * 判据用**合成 payload**：形状取自真实引擎（一张真【杀】的 `PlayCard` + `Pass`），
 * 但不必为了构造它去搜一颗恰好让人类手上留【杀】的种子。
 */
{
  const nanman = {
    kind: "RESPOND",
    prompt: "响应窗口：请打出一张牌，或放弃。",
    actor: 0,
    cause: "BARBARIAN_INVASION",
    required: "slash",
    legal_actions: [
      { answers_seq: 9, kind: "PlayCard", card: { card_id: 7, name: "杀", type: "slash" } },
      { answers_seq: 9, kind: "Pass" },
    ],
    candidate_index: {
      "7": { card: { card_id: 7, name: "杀", type: "slash" }, pool: [], min_targets: 0, max_targets: 0, combos: [[]] },
    },
  };
  const saved = ui.state.view;
  ui.state.view = { pending: nanman, snapshot: { self_hand: [{ card_id: 7, name: "杀" }], players: [], seat: 0 }, human_seat: 0, heroes: {} };
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ui.state.view);

  const buttons = $("t-options").children;
  const savedSession = ui.state.sessionId;
  const giveUp = $("btn-pass");
  check("㉿R1 响应窗口里『放弃』是提示条**右端**看得见的按钮",
    !!giveUp && giveUp.hidden === false && (giveUp.textContent || "").includes("放弃"),
    `hidden=${giveUp && giveUp.hidden} text="${giveUp && giveUp.textContent}" options=${JSON.stringify(buttons.map((b) => b.textContent))}`);
  check("㉿R2 有【杀】可出时，未选任何东西**不可**提交（别替玩家出牌）",
    $("btn-submit").disabled === true && ui.buildAction() === null);
  check("㉿R2b 放弃不在浮动选项行里（选项行只放简单询问）",
    !buttons.some((b) => (b.textContent || "").includes("放弃")),
    `选项=${JSON.stringify(buttons.map((b) => b.textContent))}`);
  check("㉿R3 右端『放弃』指向那条 Pass 候选",
    !!ui.rightActionIndex && ui.rightActionIndex(nanman, "Pass")
      === nanman.legal_actions.findIndex((action) => action.kind === "Pass"),
    `index=${ui.rightActionIndex ? ui.rightActionIndex(nanman, "Pass") : "n/a"}`);
  if (giveUp) {
    // 合成载荷没有会话 id：右端按钮只留下**内部选中态**，不发请求、也不重绘内容区（U2-03）。
    ui.state.sessionId = null;
    (giveUp.listeners.click || []).forEach((fn) => fn({ target: giveUp }));
    check("㉿R4 点右端『放弃』= 选中 Pass、提交按钮可点（不越权提交、不铺候选行）", (() => {
      const picked = ui.buildAction();
      return picked !== null && picked.kind === "Pass" && $("btn-submit").disabled === false
        && $("t-options").children.length === 0 && candidateRowButtons().length === 0;
    })(), `buildAction=${JSON.stringify(ui.buildAction())} disabled=${$("btn-submit").disabled} options=${JSON.stringify($("t-options").children.map((node) => node.textContent))}`);
  }
  ui.state.sessionId = savedSession;
  ui.state.view = saved;
  ui.resetSelection();
}

/* ============ ㉿ 选择面：点槽位必须能提交（点击路径，不手工设 selectedOption） ============
 *
 * 真 bug（本轮用户报障「五谷丰登也没法选择卡牌获得，按钮是灰的」）：
 * `renderSlots` 只写 `state.picks`，从不设 `state.selectedOption`；而提交态统一到
 * `refreshSubmit()` 后它要求有选中的候选 ⇒ `buildAction()` 恒 null ⇒ **点完槽位按钮仍是灰的**。
 *
 * ㉿S 段刻意**不**像驱动循环那样手工设 `selectedOption`——正是那个"替玩家把这一步做掉"
 * 的写法让这个 bug 穿过了当时的全部门禁。
 */
{
  const face = {
    kind: "SELECT_CARDS",
    prompt: "请从选择面里选择一张牌。",
    actor: 0,
    spec: { zones: ["processing"], total: [1, 1], per_zone: [], optional: false, sequential: false },
    slots: [0, 1, 2, 3].map((i) => ({
      ref: { zone: "processing", seat: null },
      card: { card_id: 200 + i, name: ["杀", "闪", "桃", "决斗"][i] },
    })),
    legal_actions: [{ answers_seq: 9, kind: "SelectCards", picks: [] }],
    candidate_index: {},
  };
  const saved = ui.state.view;
  ui.state.view = { pending: face, snapshot: { self_hand: [], players: [], seat: 0 }, human_seat: 0, heroes: {} };
  ui.resetSelection();
  ui.renderPending(ui.state.view);
  const row = $("t-options").children.find((c) => c.className === "cards");
  check("㉿S1 选择面渲染出 4 个槽位", !!row && row.children.length === 4, `槽位=${row ? row.children.length : 0}`);
  check("㉿S2 未选槽位时不可提交", $("btn-submit").disabled === true);
  if (row) {
    (row.children[2].listeners.click || []).forEach((fn) => fn({ target: row.children[2] }));
    check("㉿S3 点第三个槽位 = picks 收下它", JSON.stringify(ui.state.picks) === "[2]",
      `picks=${JSON.stringify(ui.state.picks)}`);
    check("㉿S4 点完槽位提交按钮**可点**（这是本轮修的 bug）", $("btn-submit").disabled === false);
    check("㉿S5 buildAction 带上所选 picks", (() => {
      const picked = ui.buildAction();
      return picked !== null && picked.kind === "SelectCards" && JSON.stringify(picked.picks) === "[2]";
    })(), `buildAction=${JSON.stringify(ui.buildAction())}`);
    // 再点一次取消
    const again = $("t-options").children.find((c) => c.className === "cards");
    (again.children[2].listeners.click || []).forEach((fn) => fn({ target: again.children[2] }));
    check("㉿S6 再点一次取消槽位", JSON.stringify(ui.state.picks) === "[]" && $("btn-submit").disabled === true);
  }
  ui.state.view = saved;
  ui.resetSelection();
}

check("㉿M12 找到一个前置：目标被迫唯一的牌（combos == 池）", auto !== null,
  auto ? `seed=${auto.seed} card_id=${auto.cardId} 池=${auto.entry.pool.length} 组合=1 条` : "16 个种子内没找到");

if (auto) {
  await replayTo(auto);
  ui.toggleFocus(Number(auto.cardId));
  check("㉿M13 自动选中：点牌即把池的全集填成已选目标（不用逐个点）",
    JSON.stringify(ui.state.selectedTargets.slice().sort((a, b) => a - b)) === JSON.stringify(auto.entry.pool.slice().sort((a, b) => a - b)),
    `已选 ${JSON.stringify(ui.state.selectedTargets)} / 池 ${JSON.stringify(auto.entry.pool)}`);
  check("㉿M14 自动选中后提交按钮直接可点", $("btn-submit").disabled === false);
  check("㉿M15 自动选中的目标是**确定态**：点单个座位不取消它（否则组合就非法了）", (() => {
    const box = $("t-seats").children.find((c) => c.getAttribute("data-seat") === String(auto.entry.pool[0]));
    if (box) (box.listeners.click || []).forEach((fn) => fn({ target: box }));
    return ui.state.selectedTargets.length === auto.entry.pool.length;
  })(), `已选 ${JSON.stringify(ui.state.selectedTargets)}`);
  check("㉿M16 提示说明了目标为什么是自动的",
    /自动选中|唯一确定/.test($("t-target-hint").textContent), `提示="${$("t-target-hint").textContent}"`);

  // 反向判据：**这不是"什么都选"**——有真实选择余地的牌绝不自动选中。
  // 必须先把局面重放回来：`scanCases` 只记 `{seed, steps, cardId, entry}`，**不存 pending**，
  // 拿一个不存在的 `multi.pending` 去判会让这条断言恒真（本轮 code-review 抓到的空转）。
  await replayTo(multi);
  check("㉿M17 自动选中不越界：多组合的牌 autoCombo() 返回 null",
    ui.autoCombo(ui.state.view.pending, Number(multi.cardId)) === null,
    `池=${ui.cardEntry(ui.state.view.pending, multi.cardId).pool.length} 组合=${ui.cardEntry(ui.state.view.pending, multi.cardId).combos.length}`);
}

// 回到干净的一局，别把后续断言带进上面那个中间局面
{
  const fresh = await post("/api/sessions", {
    player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });
  ui.state.sessionId = fresh.session_id;
  ui.state.logRecords = [];
  ui.resetSelection();
  ui.applyView(fresh);
}

/* ------------------------------------------- ② 九类询问：渲染 + 提交纪律 */

// 先回到建局那一刻：上面那些前置扫描把界面推到了别的局面，直接读 `created` 的字段会张冠李戴。
await openSession(created);
const pending0 = ui.state.view.pending;
check("建局后有挂起询问", !!pending0, pending0 && pending0.kind);
// 唯一候选**不铺按钮**：界面只说"将提交什么"（spec D9）。建局后那个 RESPOND 往往正是这种
// （合法行动只有"放弃"），所以这里断言的是"零按钮 + 说明将提交什么"，而不是"零选项 = 坏了"。
check("唯一候选不铺按钮，只说明将提交什么", (() => {
  const rk = ui.renderKind(pending0, pending0.legal_actions);
  if (rk === "direct") {
    return $("t-options").children.length === 0 && /将提交/.test($("t-choice").textContent);
  }
  return $("t-options").children.length > 0;
})(), `${ui.renderKind(pending0, pending0.legal_actions)} / ${pending0.legal_actions.map((a) => a.kind).join(",")} / ${JSON.stringify(pending0.legal_actions[0])} / choice="${$("t-choice").textContent}"`);
check("询问文案已渲染", $("t-prompt").textContent.length > 0, $("t-prompt").textContent.slice(0, 40));
// item 4：询问面板 = **效果区说明**（#t-context，跟处理区走）+ **需要做什么**
// （#t-prompt 优先显示服务端 `context.requirement`）；被提供的技能文案另起一行（#t-skill-hints）。
const askContext = (pending0 && pending0.context) || null;
check("㉿N5 询问面板显示「需要做什么」（服务端 requirement）",
  !askContext || !askContext.requirement || $("t-prompt").textContent === askContext.requirement,
  `prompt="${$("t-prompt").textContent}" requirement=${askContext && askContext.requirement}`);
check("㉿N5 技能提示行已渲染", !!$("t-skill-hints"), `hints="${$("t-skill-hints").textContent}"`);
check("时限关闭时不显示倒计时", $("t-timer").textContent === "", `"${$("t-timer").textContent}"`);

const seen = new Set();
const renderErrors = [];
// 借刀杀人的被迫【杀】窗口（`cause=BORROWED_SWORD`）：**恒单阶段**（spec D11）——
// 询问里没有 `use_targets`，官方的"再选一次目标"已经烘进候选，所以界面就是"点座位"。
// 这个窗口很稀（实测 40 局只出 1 次），故这里按"遇到就必然成立、遇不到也说得清"来断言。
let borrowed = 0;
let borrowedHadStage = false;
let steps = 0;
while (ui.state.view.pending && steps < 4000) {
  const p = ui.state.view.pending;
  seen.add(p.kind);
  if (p.kind === "RESPOND" && p.cause === "BORROWED_SWORD") {
    borrowed += 1;
    const payload = JSON.parse(JSON.stringify(p));
    borrowedHadStage = "use_targets" in payload || "mode" in payload;
  }
  try {
    ui.state.selectedOption = null;
    ui.state.picks = [];
    ui.renderPending(ui.state.view);
    if (p.kind === "SELECT_CARDS") {
      ui.state.selectedOption = p.legal_actions.findIndex((a) => a.kind === "SelectCards");
      ui.renderPending(ui.state.view);
      if ($("t-options").children.length === 0) renderErrors.push("SELECT_CARDS 没有渲染出选择面");
    }
    // U2-03：任何一类询问的初始面都不许出现逐条候选按钮（含点过技能 / 选项之后的面）。
    if (candidateRowButtons().length) {
      renderErrors.push(`${p.kind}: 出现候选行按钮 ${JSON.stringify(candidateRowButtons().map((b) => b.textContent))}`);
    }
  } catch (error) {
    renderErrors.push(`${p.kind}: ${error.message}`);
    break;
  }
  // 选候选 + 按候选自己的约束补足选择（与真玩家点选等价）。
  // 优先"结束 / 放弃"以便对局真的往前走（一直用第一张牌会反复发动技能、走不到终局）。
  const terminal = p.legal_actions.findIndex((a) => a.kind === "EndPhase" || a.kind === "Pass");
  if (ui.isHandPick(p)) {
    // 手选路径：走玩家那条路——点一张牌（必要时逐个点它第一组合的座位）。
    // 判据是"这条询问走手选"（`isHandPick`）而不是"这条询问必须点目标"（`needsPicker`）：
    // 唯一一张【闪】没有目标要选，但它仍然要点一下牌才能提交。
    ui.resetSelection();
    ui.renderTable();
    const index = ui.cardIndex(p);
    const cardId = Object.keys(index)[0];
    if (terminal >= 0) {
      // 有"结束出牌阶段 / 放弃"就走它：否则同一条询问会反复出同一张牌（无穷循环）。
      // 让对局真的往前走，是本烟测的驱动策略，不是被测行为。
      ui.state.selectedOption = terminal;
      $("t-choice").textContent = `将提交：${ui.actionLabel(p.legal_actions[terminal])}`;
    } else if (cardId === undefined) {
      // 这条手选询问里没有可点的牌（例如池为空的响应窗口）：退到第一条候选。
      ui.state.selectedOption = 0;
    } else {
      ui.toggleFocus(Number(cardId));
      const combo = (index[String(cardId)].combos || [])[0] || [];
      for (const seat of combo) {
        const box = $("t-seats").children.find((c) => c.getAttribute("data-seat") === String(seat));
        if (box) (box.listeners.click || []).forEach((fn) => fn({ target: box }));
      }
    }
  } else if (ui.renderKind(p, p.legal_actions) === "direct") {
    // 唯一且无需选目标的候选：界面显示"将提交：…"，驱动方**必须**取同一条，
    // 否则下一条断言比的就成了另一个 Action（旧断言在这里会假红）。
    ui.state.selectedOption = 0;
  } else {
    ui.state.selectedOption = terminal >= 0 ? terminal : 0;
  }
  const chosen = ui.buildAction()
    || (ui.needsPicker(p) ? p.legal_actions[0] : p.legal_actions[ui.state.selectedOption !== null ? ui.state.selectedOption : 0]);
  // 注意：`SelectCards` 的 buildAction 结果是 `Object.assign({}, 候选, {picks})`，**键序**与
  // 候选不同，故这里不能拿 JSON 字符串当身份；`kind` 在九类询问里是判别式，用它找那一条。
  const chosenAt = p.legal_actions.findIndex((a) => a.kind === chosen.kind);
  if (chosenAt < 0) { renderErrors.push(`${p.kind}: 选不出一个候选`); break; }
  ui.state.selectedOption = chosenAt;
  if (chosen.kind === "SelectCards" && ui.stepDialogKind(p) !== ui.DIALOG_KINDS.reveal) {
    // 亮牌模板（U2-11 / 五谷）是内容按钮**一击即提交**：右端提交键恒灰，由
    // `renderReveal` 的牌面 click 送出那一条 `SelectCards`。驱动方在这里只补 `picks`
    // （等价于点了第 N 张亮牌），不拿右端按钮当判据。
    const least = (p.spec && p.spec.total && p.spec.total[0]) || 1;
    ui.state.picks = Array.from({ length: least }, (_, i) => i);
    const disabled = $("btn-submit").disabled;
    ui.state.picks = [];
    ui.renderPending(ui.state.view);
    if (!disabled) { renderErrors.push("SelectCards 在未选够张数时提交按钮却是可点的"); break; }
    $("btn-submit").disabled = true;
    ui.state.picks = Array.from({ length: least }, (_, i) => i);
    ui.renderPending(ui.state.view);
    if ($("btn-submit").disabled) {
      renderErrors.push(`SelectCards 选够 ${least} 张后提交按钮仍不可点 [spec.total=${JSON.stringify(p.spec && p.spec.total)} slots=${(p.slots||[]).length} picks=${ui.state.picks.length} selectedOption=${ui.state.selectedOption} choice="${$("t-choice").textContent}"]`);
      break;
    }
  } else if (chosen.kind === "SelectCards") {
    // 亮牌：等价于"点了第 least 张亮牌"——补 `picks` 后由 `buildAction()` 造同一条
    // `SelectCards`；右端按钮**本来就该是灰的**（一击提交，没有第二个提交点）。
    const least = (p.spec && p.spec.total && p.spec.total[0]) || 1;
    ui.state.picks = Array.from({ length: least }, (_, i) => i);
  }
  if (chosen.kind === "DiscardCards") {
    // 弃牌没有"手选"路径（spec D10），由旧候选列表路径处理；`picks` 装的是**手牌下标**，
    // 与提交时用来比对的 `card_id` 在同一个秩序里（`self_hand` 就是按 `hand` 顺序投影的）。
    const need = (chosen.cards || []).length;
    ui.state.picks = [];
    const hand = ui.state.view.snapshot.self_hand.map((c) => c.card_id);
    ui.state.picks = hand.slice(0, need);
    if (ui.state.picks.length < need) { renderErrors.push("弃牌张数不够（手牌不足）"); break; }
    ui.renderTable();
    ui.renderPending(ui.state.view);
  }
  const action = ui.buildAction();
  if (!action) { renderErrors.push(`${p.kind}: buildAction() 返回 null`); break; }
  if (!p.legal_actions.some((a) => JSON.stringify(a) === JSON.stringify(action))) {
    renderErrors.push(`${p.kind}: buildAction() 造出了不在候选里的 Action`);
    break;
  }
  const before = calls.length;
  await ui.submitAction();
  const sent = calls.slice(before).find((c) => c.path.includes("/answer"));
  if (!sent || JSON.stringify(sent.body.action) !== JSON.stringify(action)) {
    renderErrors.push(`${p.kind}: 提交的字节与 buildAction() 的结果不一致`);
    break;
  }
  steps += 1;
}
check(`每类询问都能渲染并提交（本局走到 ${seen.size} 类：${[...seen].join("、")}）`, renderErrors.length === 0, renderErrors.join("；"));
check("借刀杀人窗口恒单阶段（询问体不带 use_targets / mode）", borrowedHadStage === false,
  borrowed > 0 ? `本局遇到 ${borrowed} 次 BORROWED_SWORD 窗口` : "本局没遇到（D11 由 40 局探针实测：该窗口 40 局才 1 次；这里遇不到即无第二阶段可言）");
check("对局已终局", ui.state.view.finished === true, `驱动 ${steps} 步后 finished=${ui.state.view.finished}`);
check("终局后提示已结束", $("t-prompt").textContent.includes("结束"), $("t-prompt").textContent);

/* ------------------------------------------------------ ③ 倒计时（有时限时） */

const timed = await post("/api/sessions", { player_count: 4, ai: "random", seed: 5, human_seat: 0, timeout_seconds: 45 });
const timedView = await get(`/api/sessions/${timed.session_id}?after=-1&logs=0`);
ui.applyView(timedView);
check("有时限时倒计时已显示", /剩余|时限/.test($("t-timer").textContent), $("t-timer").textContent);

/* ------------------------------------------------------ ④ 录像列表 + 回放页 */

const runs = await get("/api/runs");
check("录像列表接口可用", Array.isArray(runs.runs) && runs.runs.length > 0, `${runs.runs.length} 局`);
ui.state.runs = runs.runs;
await ui.loadRuns();
check("开局页渲染出录像条目", $("run-list").children.length > 0, `${$("run-list").children.length} 条`);
check("录像条目带胜负标记", /我赢|我输|—/.test($("run-list").text()), $("run-list").text().replace(/\s+/g, " ").trim().slice(0, 70));

// 挑一局 8 人的录像（门禁里可能先跑过 4 人局；断言不该硬编码人数）。
const target = runs.runs.find((run) => run.player_count === 8) || runs.runs[0];
const seats = target.player_count;
await ui.openReplay(target.name);
// 回放页默认是简易档；下面这些 W4-6 断言读的是**逐条记录**，显式切到完整全部。
setSelect("r-log-mode", "all");
ui.state.replay.index = ui.state.replay.records.length - 1;
ui.renderReplay();
check(`回放页渲染出 ${seats} 个座位`, $("r-seats").children.length === seats, `${$("r-seats").children.length}`);
check("回放页显示逐座位武将（侧车 meta）", $("r-heroes").textContent.split("  ").length === seats, $("r-heroes").textContent.slice(0, 70));
check("回放页事件流有内容", $("r-log").children.length > 0, `${$("r-log").children.length} 条`);
check("回放位置指示到末尾", $("r-pos").textContent.includes(String(ui.state.replay.records.length)), $("r-pos").textContent);

/* W4-6：询问面板 / 全区域 / play-pause / 人类座位 / 空壳引导。
 *
 * 断言全部落在**真的前端脚本 + 真的录像**上。挑哪条记录来看，由这里一份**独立的**
 * 增量重建决定（不是调前端的派生函数）——否则"界面画了什么"与"测试算了什么"
 * 是同一份实现，断言会自证。
 */
const replayRecords = ui.state.replay.records;
const replayDerivedAt = (records, upto, count) => {
  const zones = Array.from({ length: count }, () => ({ hand: [], equip: [], judge: [] }));
  const processing = new Map();
  const bucket = (ref) => {
    if (!ref || ref.seat === null || ref.seat === undefined) return null;
    if (!["hand", "equip", "judge"].includes(ref.zone)) return null;
    return zones[ref.seat] ? zones[ref.seat][ref.zone] : null;
  };
  for (let i = 0; i <= upto; i += 1) {
    const record = records[i];
    if (!record || record.kind !== "delta" || record.delta_type !== "card_move") continue;
    const cards = record.cards || [];
    const from = bucket(record.from_zone);
    if (from) for (const card of cards) {
      const at = from.findIndex((held) => held.card_id === card.card_id);
      if (at >= 0) from.splice(at, 1);
    }
    const to = bucket(record.to_zone);
    if (to) for (const card of cards) {
      if (!to.some((held) => held.card_id === card.card_id)) to.push(card);
    }
    if ((record.to_zone || {}).zone === "processing") {
      for (const card of cards) processing.set(card.card_id, card);
    }
    if ((record.from_zone || {}).zone === "processing") {
      for (const card of cards) processing.delete(card.card_id);
    }
  }
  return { zones, processing };
};
const allZonesIndex = replayRecords.findIndex((_, index) => {
  const { zones, processing } = replayDerivedAt(replayRecords, index, seats);
  return zones.some((z) => z.hand.length > 0)
    && zones.some((z) => z.equip.length > 0)
    && zones.some((z) => z.judge.length > 0)
    && processing.size > 0;
});
const seatNode = (seat) => $("r-seats").children.find(
  (node) => node.getAttribute("data-seat") === String(seat));
/** 逐座位区的行：`data-zone` 在行上（回放页仍是旧版 `.cards.mini` 结构）。 */
const zoneRow = (seat, zone) => {
  const node = seatNode(seat);
  return node ? node.children.find((child) => child.getAttribute("data-zone") === zone) : null;
};
/** 回放页的卡面：**名称 + 花色符号 + 点数**仍要逐张比对（U1-02 只是把文本换成卡面，
 *  不是把「这张牌是什么」的断言降级成 id）。符号 / 点数映射是测试自己的独立对照表。 */
const REPLAY_SUIT = { spade: "♠", heart: "♥", club: "♣", diamond: "♦" };
const REPLAY_RANK = { 1: "A", 11: "J", 12: "Q", 13: "K" };
const cardText = (card) => ({
  id: String(card.card_id),
  name: card.name,
  suit: REPLAY_SUIT[card.suit] || "",
  rank: REPLAY_RANK[card.rank] || (card.rank === null || card.rank === undefined ? "" : String(card.rank)),
});
const cardsIn = (node) => (node ? node.children.filter(
  (child) => child.tagName === "BUTTON" || String(child.className || "").split(/\s+/).includes("card"),
) : []);
const cardParts = (child) => {
  const part = (name) => {
    const span = (child.children || []).find(
      (item) => String(item.className || "").split(/\s+/).includes(name));
    return span ? span.textContent : "";
  };
  return {
    id: child.getAttribute("data-card-id"),
    name: part("cname"),
    suit: part("suit"),
    rank: part("rank"),
  };
};
const rowCards = (row) => cardsIn(row).map(cardParts);
const expectedCards = (cards) => cards.map(cardText);

check("录像里有一段同时有手牌 / 装备 / 判定 / 处理区（W4-6 断言的前置）",
  allZonesIndex >= 0, `index=${allZonesIndex}`);
if (allZonesIndex >= 0) {
  const expected = replayDerivedAt(replayRecords, allZonesIndex, seats);
  ui.state.replay.index = allZonesIndex;
  ui.renderReplay();
  for (const zone of ["hand", "equip", "judge"]) {
    const mismatches = [];
    expected.zones.forEach((seatState, seat) => {
      const actual = rowCards(zoneRow(seat, zone));
      const wanted = expectedCards(seatState[zone]);
      const actualText = actual.map((card) => JSON.stringify(card)).join("|");
      const wantedText = wanted.map((card) => JSON.stringify(card)).join("|");
      if (actualText !== wantedText) mismatches.push(`座位${seat}: ${actualText} ≠ ${wantedText}`);
    });
    check(`回放页 ${zone} 区逐座位 = 从 card_move 重建的结果`,
      mismatches.length === 0, mismatches.slice(0, 2).join("；"));
  }
  const opponent = expected.zones.findIndex(
    (seatState, seat) => seat !== target.human_seat && seatState.hand.length > 0);
  check("回放页上帝视角：非人类座位的手牌也画出牌面",
    opponent >= 0 && rowCards(zoneRow(opponent, "hand")).length > 0,
    `座位=${opponent} 手牌=${opponent >= 0 ? rowCards(zoneRow(opponent, "hand")).map((card) => card.name).join("|") : "—"}`);
  const expectedProcessing = [...expected.processing.values()].map(cardText);
  const processingCards = () => cardsIn($("r-processing")).map(cardParts);
  check("回放页处理区 / 效果区 = 从 card_move 重建的结果（名称 / 花色 / 点数 / id）",
    JSON.stringify(processingCards()) === JSON.stringify(expectedProcessing),
    `面板=${JSON.stringify(processingCards())} / 记录=${JSON.stringify(expectedProcessing)}`);
}

const queryIndex = replayRecords.findIndex((record) => record.kind === "query"
  && record.query.prompt && (record.query.legal_actions || []).length > 0);
check("录像里有可渲染的询问记录", queryIndex >= 0, `index=${queryIndex}`);
if (queryIndex >= 0) {
  const query = replayRecords[queryIndex].query;
  ui.state.replay.index = queryIndex;
  ui.renderReplay();
  check("回放页询问面板显示本次询问的文案",
    $("r-query-prompt").textContent === query.prompt,
    `面板="${$("r-query-prompt").textContent}" / 记录="${query.prompt}"`);
  const firstLabel = ui.actionLabel(query.legal_actions[0]);
  check("回放页询问面板列出候选",
    $("r-query-candidates").text().includes(firstLabel),
    `面板="${$("r-query-candidates").text().replace(/\s+/g, " ").slice(0, 90)}" / 首条="${firstLabel}"`);
}

const clickButton = (node) => (node.listeners.click || []).forEach((fn) => fn({ target: node }));

// 预发动确认在记录流里是一条复合 `ActivateSkill`（`picks` / `choices` 都在）：
// 回放事件流必须把它摊开，用户才看得见"确认发动"选了什么（清单本身不进记录流）。
const compositeIndex = replayRecords.findIndex((record) => record.kind === "action"
  && record.action && record.action.kind === "ActivateSkill"
  && ((record.action.picks || []).length > 0 || (record.action.choices || []).length > 0));
check("录像里有复合 ActivateSkill 的应答记录（W4-6 断言的前置）",
  compositeIndex >= 0, `index=${compositeIndex}`);
if (compositeIndex >= 0) {
  const composite = replayRecords[compositeIndex].action;
  ui.state.replay.index = compositeIndex;
  ui.renderReplay();
  const logText = $("r-log").text();
  check("回放事件流展开复合 ActivateSkill（技能 id + picks / choices）",
    logText.includes(String(composite.skill_id))
      && (composite.choices || []).every((choice) => logText.includes(choice)),
    `事件流="${logText.replace(/\s+/g, " ").slice(-140)}"`);
}

// 选择面的区域读 `spec.zones`（W4-fix-3 起青龙的提供步是 hand ∪ equip），不写死手牌。
const cardsQueryIndex = replayRecords.findIndex((record) => record.kind === "query"
  && record.query.kind === "SELECT_CARDS"
  && ((record.query.spec && record.query.spec.zones) || []).length > 0);
check("录像里有带 spec.zones 的选择面记录（W4-6 断言的前置）",
  cardsQueryIndex >= 0, `index=${cardsQueryIndex}`);
if (cardsQueryIndex >= 0) {
  const query = replayRecords[cardsQueryIndex].query;
  const zones = query.spec.zones;
  ui.state.replay.index = cardsQueryIndex;
  ui.renderReplay();
  check("回放页询问面板按 spec.zones 展示区域（不写死手牌）",
    zones.every((zone) => $("r-query-meta").textContent.includes(zone)),
    `面板="${$("r-query-meta").textContent}" / zones=${zones.join("、")}`);
  check("回放页选择面画出记录里的槽位",
    slotsInQueryMatch(query), `${$("r-query-slots").children.length} / ${(query.slots || []).length}`);
}
function slotsInQueryMatch(query) {
  const slots = query.slots || [];
  const box = $("r-query-slots");
  if (!box) return false;
  if (!slots.length) return box.children.length === 0;
  return box.children.length === slots.length
    && slots.every((slot, index) => {
      const name = slot.card ? slot.card.name : "？";
      return box.children[index].text().includes(name);
    });
}

// play / pause：计时器垫片只排队，这里显式跑一条——断言"推进一条"与"不叠第二个计时器"。
pendingTimers.clear();
ui.state.replay.index = 0;
ui.renderReplay();
clickButton($("btn-r-play"));
check("回放可播放（按钮切成暂停）",
  ui.state.replay.playing === true && $("btn-r-play").textContent.includes("暂停"),
  `playing=${ui.state.replay.playing} / ${$("btn-r-play").textContent}`);
check("播放只排一个计时器",
  pendingTimers.size === 1, `${pendingTimers.size} 个`);
check("播放按钮的 data-playing 跟着状态",
  $("btn-r-play").getAttribute("data-playing") === "true",
  $("btn-r-play").getAttribute("data-playing"));
const playIndex = ui.state.replay.index;
runNextTimer();
check("播放一步真的前进了一条记录",
  ui.state.replay.index === playIndex + 1,
  `${playIndex} → ${ui.state.replay.index}`);
check("推进后仍只有一个下一次计时器（再点不叠加）",
  pendingTimers.size === 1 && ui.state.replay.playing === true,
  `${pendingTimers.size} 个 / playing=${ui.state.replay.playing}`);
clickButton($("btn-r-play"));
check("回放可暂停（按钮切回播放）",
  ui.state.replay.playing === false && $("btn-r-play").textContent.includes("播放"),
  `playing=${ui.state.replay.playing} / ${$("btn-r-play").textContent}`);
check("暂停清掉计时器",
  pendingTimers.size === 0, `${pendingTimers.size} 个`);
check("暂停后按钮 data-playing 也回到 false",
  $("btn-r-play").getAttribute("data-playing") === "false",
  $("btn-r-play").getAttribute("data-playing"));

const originalHuman = ui.state.replay.meta.human_seat;
ui.state.replay.meta.human_seat = 0;
ui.renderReplay();
check("回放页在座位上标出「本局人类」",
  $("r-seats").text().includes("本局人类") && $("r-human").textContent.includes("座位 0"),
  $("r-human").textContent);
ui.state.replay.meta.human_seat = null;
ui.renderReplay();
check("录像没有人类座位时明确显示「全是 AI」",
  $("r-human").textContent.includes("全是 AI") && !$("r-seats").text().includes("本局人类"),
  $("r-human").textContent);
ui.state.replay.meta.human_seat = originalHuman;

const delayBefore = ui.state.eventDelayMs;
clickButton($("btn-r-delay"));
const delayAfter = ui.state.eventDelayMs;
check("回放页沿用牌桌的 0.05–0.5s 六档步长",
  delayAfter !== delayBefore && ui.DELAY_STEPS_MS.includes(delayAfter),
  `${delayBefore}ms → ${delayAfter}ms`);
// 步长是**全局一份**：把这六档再点回原档，免得把后面的牌桌断言改成另一档。
for (let step = 1; step < ui.DELAY_STEPS_MS.length; step += 1) clickButton($("btn-r-delay"));
check("六档循环一圈回到原档（回放页与牌桌共用同一份状态）",
  ui.state.eventDelayMs === delayBefore, `${ui.state.eventDelayMs}ms`);

ui.state.replay = null;
ui.renderReplay();
check("未选中录像时回放页有明确引导（不是空壳）",
  !$("r-empty").hidden && $("r-empty-guide").textContent.includes("挑一局"),
  `引导="${$("r-empty-guide").textContent}"`);
check("未选中录像时页内直接列出录像",
  !$("r-run-list").hidden && $("r-run-list").children.length > 0,
  `${$("r-run-list").children.length} 条`);
check("清空回放后播放按钮回到「播放」（不留暂停态）",
  $("btn-r-play").textContent.includes("播放")
    && $("btn-r-play").getAttribute("data-playing") === "false",
  `${$("btn-r-play").textContent} / data-playing=${$("btn-r-play").getAttribute("data-playing")}`);

/* ============ 本轮报障（合成局面）：公开处理区 + 询问上下文 + 遗计选座位 ============
 *
 * 三条都是**界面读法**的断言，故不依赖某一局恰好走到那个前置：直接喂一份与
 * 服务端响应体同形的合成 `view`，看界面按不按那些键读。服务端那一侧（`context` /
 * `option_seats` 怎么算）由 `tests/test_web.py` 钉。
 */

const synthCard = (id, name) => ({
  card_id: id, name, suit: "spade", rank: 7, type: "slash", slot: null,
  attack_range: null, color: "black", virtual_sources: [],
});
const synthView = (pending) => ({
  seed: 1, human_seat: 0, finished: false, timeout_seconds: null,
  heroes: { 0: "guojia", 1: "zhangfei", 2: "diaochan" },
  skills: [],
  pending,
  snapshot: {
    seat: 0, phase: "play", turn_seat: 0,
    players: [0, 1, 2].map((seat) => ({
      seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [], identity: null,
    })),
    self_hand: [synthCard(1, "杀")],
    deck_count: 50, discard: [],
    processing: [synthCard(9, "五谷丰登"), synthCard(10, "闪")],
  },
});

// ① 处理区 / 效果区：公开渲染，且与"是否轮到自己"无关（pending.actor 是另一个座位）。
ui.state.view = synthView({
  kind: "SELECT_CARDS", actor: 2, prompt: "请从选择面里选择一张牌。",
  legal_actions: [], candidate_index: {}, context: null, option_seats: {},
  slots: [], spec: { zones: ["hand"], total: [1, 1] },
});
ui.renderTable();
const processingText = $("t-processing").text();
check("㉿P1 处理区渲染出五谷亮牌（不只轮到自己才看得见）",
  processingText.includes("五谷丰登") && processingText.includes("闪"),
  `处理区="${processingText.replace(/\s+/g, " ").trim()}"`);
check("㉿P2 处理区的牌不可点（公开信息，不是我的选择）",
  $("t-processing").children.length > 0 && $("t-processing").children.every((c) => !(c.listeners.click || []).length));

// ② 描述文本：跟效果区（处理区）走，不再写服务端的"本窗口：谁对谁做了什么"。
ui.state.view = synthView({
  kind: "SKILL_ACTIVATION", actor: 0, prompt: "是否发动该技能？",
  legal_actions: [{ kind: "ActivateSkill", skill_id: "hero.guojia.yiji", answers_seq: 1, choices: [], picks: [] }],
  candidate_index: {}, option_seats: {},
  context: { label: "0号位郭嘉对0号位郭嘉使用遗计", name: "遗计", targets: [0] },
});
ui.renderTable();
ui.renderPending(ui.state.view);
check("㉿P3 描述文本跟效果区（不再写『本窗口：…』查询上下文）",
  $("t-context").textContent.includes("处理中")
    && $("t-context").textContent.includes("五谷丰登")
    && !$("t-context").textContent.includes("本窗口"),
  `描述="${$("t-context").textContent}"`);

// ③ 遗计的"交给谁"：座位可点，点座位 = 选中那条 seat:N 候选。
ui.state.view = synthView({
  kind: "SELECT_OPTION", actor: 0, prompt: "请选择一项。",
  legal_actions: [
    { kind: "SelectOption", choice: "seat:0", answers_seq: 9 },
    { kind: "SelectOption", choice: "seat:2", answers_seq: 9 },
  ],
  candidate_index: {}, context: { label: "0号位郭嘉对0号位郭嘉使用遗计", name: "遗计", targets: [0] },
  option_seats: { "seat:0": 0, "seat:2": 2 },
});
ui.state.selectedOption = null;
ui.renderTable();
ui.renderPending(ui.state.view);
const selectedSeatBox = seatBox(2);
check("㉿P4 可交给的座位画成可点（targetable）",
  !!selectedSeatBox && selectedSeatBox.classList.contains("targetable"));
// 报障 2：**取消选项设计**——`SELECT_OPTION` 的选座位形态不铺任何选项按钮，只点场上武将。
check("㉿P4b 遗计选座位不再铺『选择座位 N』按钮",
  ui.renderKind(ui.state.view.pending, ui.state.view.pending.legal_actions) === "seat"
    && $("t-options").children.length === 0,
  `kind=${ui.renderKind(ui.state.view.pending, ui.state.view.pending.legal_actions)} 选项=${JSON.stringify($("t-options").children.map((b) => b.textContent))}`);
(selectedSeatBox.listeners.click || []).forEach((fn) => fn({ target: selectedSeatBox }));
check("㉿P5 点座位 = 选中那条 seat:2 候选", ui.state.selectedOption === 1,
  `selectedOption=${ui.state.selectedOption}`);
check("㉿P5b 点中的座位画成确定态（tsel）",
  (() => {
    ui.renderTable();
    const box = seatBox(2);
    return !!box && box.classList.contains("tsel");
  })(), `seat2 类=${JSON.stringify([...(seatBox(2).classList.set)])}`);
check("㉿P6 点座位后提交按钮可点", $("btn-submit").disabled === false,
  `disabled=${$("btn-submit").disabled}`);
const optAction = ui.buildAction();
check("㉿P7 提交的正是 seat:2 那条原候选",
  !!optAction && optAction.kind === "SelectOption" && optAction.choice === "seat:2",
  JSON.stringify(optAction));

/* ============ 本轮报障（合成局面）：场上装备/判定区可见 + 弃牌手选 + 效果区并栏 ============
 *
 * 三条都是**界面读法**的断言，故喂一份与服务端响应体同形的合成 `view`。
 */

// ① 场上每个座位的装备区 / 判定区都要画出**牌面**（不只是数量）。
//    报障原文：「不可见场上装备区、判定区牌」——座位框只有"装备 N 判定 N"，
//    而 `PlayerPublic.equip/judge` 早就逐座位下发了（`src/sgs/state/protocol.py`）。
//    这也是「乐不思蜀在座次 2 却判定给座次 0」误判的根因：两个座位都摆着乐不思蜀时，
//    看不见牌的人分不清哪张是谁的（真实对局 `s2` 里一张被过河拆桥拿走、另一张才判）。
{
  const zoneView = synthView({
    kind: "PLAY_PHASE", actor: 0, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [], candidate_index: {}, context: null, option_seats: {},
  });
  zoneView.snapshot.players[0].equip = [synthCard(11, "八卦阵")];
  zoneView.snapshot.players[0].judge = [synthCard(12, "闪电")];
  zoneView.snapshot.players[1].judge = [synthCard(13, "乐不思蜀")];
  ui.state.view = zoneView;
  ui.resetSelection();
  ui.renderTable();
  const boxes = $("t-seats").children;
  const selfSeat = ui.state.view.snapshot.seat;
  // U1-02：座位卡是「左武将块 + 右判定 / 装备块」，行不再挂在座位节点的一层。
  // 逐层找带 `data-zone` 的容器（自己的在 `#t-self-zones`，对手的在 `.side-block`）。
  const findZone = (node, zone) => {
    if (!node || !node.children) return null;
    for (const child of node.children) {
      if (child.getAttribute && child.getAttribute("data-zone") === zone) return child;
      const found = findZone(child, zone);
      if (found) return found;
    }
    return null;
  };
  const zoneRow = (seat, zone) => {
    // 自己的判定 / 装备区在底部面板的 `#t-self-zones` 里（U1-01 起不在环上）。
    const box = Number(seat) === Number(selfSeat) ? $("t-self-zones") : seatBox(seat);
    return box ? findZone(box, zone) : null;
  };
  const rowText = (seat, zone) => (zoneRow(seat, zone) ? zoneRow(seat, zone).text() : "");
  check("㉿Q1 0 号位的装备区画出牌面【八卦阵】",
    rowText(0, "equip").includes("八卦阵"), `装备行="${rowText(0, "equip")}"`);
  check("㉿Q2 0 号位的判定区画出牌面【闪电】",
    rowText(0, "judge").includes("闪电"), `判定行="${rowText(0, "judge")}"`);
  check("㉿Q3 对手（1 号位）的判定区也画出牌面【乐不思蜀】",
    rowText(1, "judge").includes("乐不思蜀"), `对手判定行="${rowText(1, "judge")}"`);
  check("㉿Q4 装备/判定行的牌不可点（公开信息，不是我的选择）",
    [zoneRow(0, "equip"), zoneRow(0, "judge"), zoneRow(1, "judge")]
      .filter(Boolean)
      .every((row) => (row.children || []).every((chip) => !(chip.listeners.click || []).length)));
}

// ② 弃牌阶段：从手牌点选，直到"未选张数 == 手牌上限"（= 选够 `pending.count` 张）。
//    报障原文：「现在是很多很多选择，名称都是『弃牌（x张）』」——引擎把 C(n,k) 个组合
//    逐条铺成按钮（`effects/actions.py:_discard`），界面照单全收。改为点手牌。
{
  const discard = {
    kind: "DISCARD", actor: 0, seq: 50, timing: "PHASE_DISCARD",
    prompt: "弃牌阶段：请弃置 1 张手牌。", count: 1,
    legal_actions: [
      { kind: "DiscardCards", answers_seq: 50, cards: [synthCard(1, "杀")] },
      { kind: "DiscardCards", answers_seq: 50, cards: [synthCard(2, "闪")] },
      { kind: "DiscardCards", answers_seq: 50, cards: [synthCard(3, "桃")] },
    ],
    candidate_index: {}, context: null, option_seats: {},
  };
  const discardView = synthView(discard);
  discardView.snapshot.self_hand = [synthCard(1, "杀"), synthCard(2, "闪"), synthCard(3, "桃")];
  ui.state.view = discardView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(discardView);
  const clickHand = (index) => {
    const chip = $("t-hand").children[index];
    (chip.listeners.click || []).forEach((fn) => fn({ target: chip }));
  };
  check("㉿D1 弃牌阶段不再逐条铺『弃牌（N张）』按钮",
    $("t-options").children.filter((b) => /弃牌（/.test(b.textContent || "")).length === 0,
    `选项=${JSON.stringify($("t-options").children.map((b) => b.textContent))}`);
  check("㉿D2 未选牌时不可提交", $("btn-submit").disabled === true && ui.buildAction() === null,
    `disabled=${$("btn-submit").disabled} action=${JSON.stringify(ui.buildAction())}`);
  clickHand(1);
  check("㉿D3 点一张手牌 = 收进待弃置（picks 装 card_id）",
    JSON.stringify(ui.state.picks) === "[2]", `picks=${JSON.stringify(ui.state.picks)}`);
  const discardAction = ui.buildAction();
  check("㉿D4 选够张数后可提交，且 buildAction 是候选里那一条",
    $("btn-submit").disabled === false && !!discardAction
      && discardAction.kind === "DiscardCards" && discardAction.cards[0].card_id === 2,
    `disabled=${$("btn-submit").disabled} action=${JSON.stringify(discardAction)}`);
  clickHand(0);
  check("㉿D5 只该弃 1 张，选够后再点不超额",
    JSON.stringify(ui.state.picks) === "[2]", `picks=${JSON.stringify(ui.state.picks)}`);
  clickHand(1);
  check("㉿D6 再点一次取消该牌，提交重新变灰",
    JSON.stringify(ui.state.picks) === "[]" && $("btn-submit").disabled === true,
    `picks=${JSON.stringify(ui.state.picks)} disabled=${$("btn-submit").disabled}`);
}

// ③ 布局壳（U1-01）：中央舞台 / 底部面板 / 顶栏。用 index.html 的结构断言——
//    DOM 垫片不建父子关系，故直接查源串里各段的先后与归属。
{
  const tableStart = html.indexOf('id="view-table"');
  const tableEnd = html.indexOf('id="view-replay"');
  const tableView = html.slice(tableStart, tableEnd);
  const at = (needle) => tableView.indexOf(needle);
  check("㉿X1 牌桌是 16:9 壳：顶栏 / 中央舞台 / 底部面板三段",
    at('id="table-shell"') >= 0
      && at('id="table-topbar"') < at('id="table-stage"')
      && at('id="table-stage"') < at('id="table-bottom"'),
    `shell=${at('id="table-shell"')} topbar=${at('id="table-topbar"')} stage=${at('id="table-stage"')} bottom=${at('id="table-bottom"')}`);
  check("㉿X1b 中央舞台两层：当前动作 + 处理区",
    at('id="stage-center"') < at('id="t-action-layer"')
      && at('id="t-action-layer"') < at('id="t-processing"')
      && at('id="t-processing"') < at('id="table-bottom"'),
    `center=${at('id="stage-center"')} action=${at('id="t-action-layer"')} processing=${at('id="t-processing"')}`);
  check("㉿X1c 底部面板从左到右：事件流按钮 | 手牌区 | 技能 | 自己的武将块 | 判定 / 装备区",
    at('id="t-log-toggle"') < at('class="hand-area"')
      && at('class="hand-area"') < at('id="t-hand"')
      && at('id="t-hand"') < at('id="t-skills"')
      && at('id="t-skills"') < at('id="t-self-seat"')
      && at('id="t-self-seat"') < at('id="t-self-zones"'),
    `log=${at('id="t-log-toggle"')} hand=${at('id="t-hand"')} skills=${at('id="t-skills"')} self=${at('id="t-self-seat"')}`);
  check("㉿X1d 提示条在手牌区正上方（#t-promptbar 在 .hand-area 内、#t-hand 之前）",
    at('class="hand-area"') < at('id="t-promptbar"')
      && at('id="t-promptbar"') < at('id="t-hand"')
      && at('id="t-prompt"') > at('id="t-promptbar"')
      && at('id="t-prompt"') < at('id="t-hand"'),
    `handArea=${at('class="hand-area"')} promptbar=${at('id="t-promptbar"')} prompt=${at('id="t-prompt"')} hand=${at('id="t-hand"')}`);
  check("㉿X1e 没有右侧固定栏（牌桌里不再有 .cols）",
    tableView.includes('id="table-bottom"')
      && !tableView.includes('class="cols"') && !tableView.includes('class="panel grow"'));
  check("㉿X1f 顶栏：左身份配比 / 中轮次与行动者 / 右牌堆弃牌堆 + 设置 + 导航三键，且不放计时器",
    (() => {
      const topbar = tableView.slice(at('id="table-topbar"'), at('id="table-stage"'));
      return topbar.includes('id="t-identity-panel"')
        && topbar.includes('id="t-round"')
        && topbar.includes('id="t-turn"')
        && topbar.includes('id="t-deck"')
        && topbar.includes('id="t-discard"')
        && topbar.includes('id="btn-settings"')
        && topbar.includes('id="nav-lobby"')
        && topbar.includes('id="nav-table"')
        && topbar.includes('id="nav-replay"')
        && !topbar.includes('id="btn-go-replay"')
        && !topbar.includes('id="t-timer"');
    })(),
    `计时器在顶栏=${tableView.slice(at('id="table-topbar"'), at('id="table-stage"')).includes('id="t-timer"')}`);
  // 判定时点的人话：事件流里"判定开始 / 判定牌生效后 · 座位 N"读得出这次判定属于谁。
  check("㉿X2 判定时点译成中文（判定开始 / 判定牌生效前 / 生效后）",
    ui.timingLabel("JUDGE_ON") === "判定开始"
      && ui.timingLabel("JUDGE_CARD_BEFORE_EFFECT") === "判定牌生效前"
      && ui.timingLabel("JUDGE_CARD_AFTER_EFFECT") === "判定牌生效后",
    `${ui.timingLabel("JUDGE_ON")} / ${ui.timingLabel("JUDGE_CARD_AFTER_EFFECT")}`);
}

/* ============ 报障 2 / 4（合成局面）：**遗留** hand-pick 渲染路径与龙胆"点技能 → 点闪" =
 *
 * 两条都是**界面读法**的断言：喂与服务端 `pending_payload` / `skills` 同形的合成 view，
 * 看界面按不按 `ui.convert_sources` / `virtual_index` 这些键读。
 * 服务端怎么算这些键由 `tests/test_web.py` 钉；这里钉前端有没有真的读它。
 *
 * ⚠️ 合成 `ui` 只注入生产会写的键（`CODING_STANDARDS.md` §6）：`_skill_ui` 现在只产
 * `requirement` 与 `convert_sources`；`manual` / `pick_cards` / `pick_seats` 的生产者已随
 * W4-2 删除，注入它们只会让测试绿得毫无意义（W4 评审处置 A.1 已清掉）。
 */

// ① 龙胆：转化候选的 card_id 是虚拟牌，界面手牌是实体牌——`virtual_index` 是那座桥。
{
  const dodge = synthCard(2, "闪");
  const virtualSlash = {
    ...synthCard(-3, "杀"), type: "slash", virtual_sources: [2],
  };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 900, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      { kind: "UseCard", answers_seq: 900, card: virtualSlash, targets: [1] },
      { kind: "EndPhase", answers_seq: 900 },
    ],
    candidate_index: {
      "-3": { card: virtualSlash, pool: [1], combos: [[1]], min_targets: 1, max_targets: 1 },
    },
    virtual_index: { "2": [{ card_id: -3, skill_ids: ["hero.zhaoyun.longdan"] }] },
    option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.heroes = { 0: "zhaoyun", 1: "guanyu", 2: "diaochan" };
  view.snapshot.self_hand = [dodge];
  view.skills = [{
    skill_id: "hero.zhaoyun.longdan", name: "龙胆", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false,
    ui: {
      requirement: "选择1张【闪】当【杀】使用或打出",
      convert_sources: [2],
    },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButton = $("t-skills").children.find((b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
  check("㉿L1 龙胆按钮此刻是**可点**的（转牌不是 ActivateSkill 候选，靠 convert_sources 点亮）",
    !!skillButton && skillButton.disabled === false && skillButton.getAttribute("data-convert") === "1",
    `disabled=${skillButton && skillButton.disabled} convert=${skillButton && skillButton.getAttribute("data-convert")}`);
  check("㉿L2 实体【闪】按来源索引可点（不再被置灰）",
    ui.cardSelectable(pending, 2) && !$("t-hand").children[0].classList.contains("dis"),
    `selectable=${ui.cardSelectable(pending, 2)}`);
  (skillButton.listeners.click || []).forEach((fn) => fn({ target: skillButton }));
  check("㉿L3 点龙胆 = 进入转化模式", ui.state.convertSkill === "hero.zhaoyun.longdan",
    `convertSkill=${ui.state.convertSkill}`);
  check("㉿L4 进入后提示词是技能自己的「需要做什么」",
    $("t-prompt").textContent === "选择1张【闪】当【杀】使用或打出", `"${$("t-prompt").textContent}"`);
  const dodgeChip = $("t-hand").children[0];
  (dodgeChip.listeners.click || []).forEach((fn) => fn({ target: dodgeChip }));
  check("㉿L5 点【闪】= 焦点落在实体牌、目标是虚拟【杀】指向的座位",
    String(ui.state.focusCardId) === "2" && JSON.stringify(ui.state.selectedTargets) === "[1]",
    `focus=${ui.state.focusCardId} targets=${JSON.stringify(ui.state.selectedTargets)}`);
  const longdanAction = ui.buildAction();
  check("㉿L6 提交的是**虚拟【杀】**那条原候选（不是实体【闪】的 id）",
    !!longdanAction && longdanAction.kind === "UseCard" && longdanAction.card.card_id === -3
      && JSON.stringify(longdanAction.targets) === "[1]",
    JSON.stringify(longdanAction));
  check("㉿L7 提交按钮可点", $("btn-submit").disabled === false, `disabled=${$("btn-submit").disabled}`);
}

/* ③ 票 14：【青龙偃月刀】续杀的「提供一张【杀】」面——**就地**高亮（不新开区域）、
 *    置灰是**当前状态的纯函数**（进询问 / 换询问 / 退出询问三点各一条）、两条入口都给、
 *    取消预发动**回到同一层询问**（不是取消整条流水线）。
 *
 * 面 = 手牌里的实体【杀】（槽 0）＋【龙胆】的【闪】转化（槽 1）＋【武圣】的装备区红马
 * 转化（槽 2）；手牌里的【桃】不在面上。
 */
{
  const slash = synthCard(1, "杀");
  const dodge = { ...synthCard(2, "闪"), type: "dodge" };
  const peach = { ...synthCard(4, "桃"), type: "peach" };
  const horse = { ...synthCard(5, "赤兔"), type: "horse", color: "red" };
  const fromDodge = { ...synthCard(-3, "杀"), type: "slash", virtual_sources: [2] };
  const fromHorse = { ...synthCard(-6, "杀"), type: "slash", virtual_sources: [5] };
  const providePending = {
    kind: "SELECT_CARDS", actor: 0, seq: 940,
    prompt: "提供一张【杀】继续使用（目标不变；可发动转化技）",
    provide_type: "slash",
    legal_actions: [{ kind: "SelectCards", answers_seq: 940, picks: [] }],
    candidate_index: {}, option_seats: {}, context: null,
    slots: [
      { ref: { zone: "hand", seat: 0 }, card: slash },
      { ref: { zone: "hand", seat: 0 }, card: fromDodge },
      { ref: { zone: "equip", seat: 0 }, card: fromHorse },
    ],
    // 与生产同源（`GREEN_DRAGON_FLOW.PickProvide.zones`）：来源含装备区的【武圣】红马。
    spec: { zones: ["hand", "equip"], total: [1, 1] },
    virtual_index: {
      "2": [{ card_id: -3, skill_ids: ["hero.zhaoyun.longdan"] }],
      "5": [{ card_id: -6, skill_ids: ["hero.guanyu.wusheng"] }],
    },
  };
  const view = synthView(providePending);
  view.heroes = { 0: "zhaoyun", 1: "guanyu", 2: "diaochan" };
  view.snapshot.self_hand = [slash, dodge, peach];
  view.snapshot.players[0].equip = [horse];
  view.skills = [
    {
      skill_id: "hero.zhaoyun.longdan", name: "龙胆", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: false,
      ui: {
        requirement: "选择1张【闪】当【杀】使用或打出",
        convert_sources: [2],
      },
    },
    {
      skill_id: "hero.guanyu.wusheng", name: "武圣", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: false,
      ui: {
        requirement: "一张红色牌当【杀】使用或打出",
        convert_sources: [5],
      },
    },
  ];
  const handChips = () => $("t-hand").children;
  const equipChip = selfEquipChip;
  const skillButton = (id) => $("t-skills").children.find((b) => b.getAttribute("data-skill-id") === id);
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  // (a) 进询问：就地高亮、**不**新开选择区。
  check("㉿B1 「提供一张【杀】」不铺选择面块（t-options 为空）",
    $("t-options").children.length === 0, `options=${$("t-options").children.length}`);
  check("㉿B2 实体【杀】就地亮起（面上槽 0）",
    ui.provideSlotIndex(providePending, 1) === 0 && ui.cardFace(providePending, slash).selectable === true
      && !handChips()[0].classList.contains("dis"),
    `slot=${ui.provideSlotIndex(providePending, 1)} dis=${handChips()[0].classList.contains("dis")}`);
  check("㉿B3 【龙胆】的【闪】走别名就地亮起（面上槽 1）",
    ui.provideSlotIndex(providePending, 2) === 1 && ui.cardFace(providePending, dodge).selectable === true
      && !handChips()[1].classList.contains("dis"),
    `slot=${ui.provideSlotIndex(providePending, 2)} dis=${handChips()[1].classList.contains("dis")}`);
  check("㉿B3b 装备区的【武圣】红牌也在**原地**亮起（面上槽 2，装备区不再静态）",
    ui.provideSlotIndex(providePending, 5) === 2 && !!equipChip()
      && !equipChip().classList.contains("dis"),
    `slot=${ui.provideSlotIndex(providePending, 5)} chip=${!!equipChip()}`);
  check("㉿B4 不满足条件的【桃】置灰（当前状态的纯函数）",
    ui.cardFace(providePending, peach).selectable === false && handChips()[2].classList.contains("dis"),
    `dis=${handChips()[2].classList.contains("dis")}`);

  // (b) 点【闪】= 选中它对应的**面内位置**（虚拟 B 没有实体 id）。
  (handChips()[1].listeners.click || []).forEach((fn) => fn({ target: handChips()[1] }));
  check("㉿B5 点【闪】= 选中面内位置 1（不是实体 id 2）",
    JSON.stringify(ui.state.picks) === "[1]", `picks=${JSON.stringify(ui.state.picks)}`);
  const provided = ui.buildAction();
  check("㉿B6 提交的是 SelectCards 骨架补上面内位置",
    !!provided && provided.kind === "SelectCards" && JSON.stringify(provided.picks) === "[1]",
    JSON.stringify(provided));
  check("㉿B7 提交按钮可点", $("btn-submit").disabled === false, `disabled=${$("btn-submit").disabled}`);

  // (c) 两条入口：源牌直接亮起（上面）+ 技能按钮（下面）。预发动 / 取消 = 两档语义。
  const longdan = skillButton("hero.zhaoyun.longdan");
  check("㉿B8 待答期间【龙胆】按钮可点（convert_sources 点亮，不是 ActivateSkill 候选）",
    !!longdan && longdan.disabled === false && longdan.getAttribute("data-convert") === "1",
    `disabled=${longdan && longdan.disabled} convert=${longdan && longdan.getAttribute("data-convert")}`);
  (longdan.listeners.click || []).forEach((fn) => fn({ target: longdan }));
  check("㉿B9 点技能 = 预发动：只按该方向亮源牌（【闪】亮、【武圣】的马灰），询问**不动**",
    ui.state.convertSkill === "hero.zhaoyun.longdan"
      && ui.state.view.pending === providePending
      && !handChips()[1].classList.contains("dis")
      && equipChip().classList.contains("dis"),
    `convert=${ui.state.convertSkill} dodgeDis=${handChips()[1].classList.contains("dis")} horseDis=${equipChip().classList.contains("dis")}`);
  (longdan.listeners.click || []).forEach((fn) => fn({ target: longdan }));
  check("㉿B10 再点一次 = 退出转化模式、回到**同一层询问**；已选源牌仍合法 ⇒ 保留",
    ui.state.convertSkill === null
      && ui.state.view.pending === providePending
      && JSON.stringify(ui.state.picks) === "[1]"
      && !handChips()[1].classList.contains("dis"),
    `convert=${ui.state.convertSkill} picks=${JSON.stringify(ui.state.picks)}`);

  // (d) 置灰是当前状态的纯函数：换询问 / 退出询问都**整体重算**，不叠加、不粘滞。
  const playPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 941, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      { kind: "UseCard", answers_seq: 941, card: peach, targets: [] },
      { kind: "EndPhase", answers_seq: 941 },
    ],
    candidate_index: { "4": { card: peach, pool: [], combos: [[]], min_targets: 0, max_targets: 0 } },
    virtual_index: {}, option_seats: {}, context: null,
  };
  ui.state.view = synthView(playPending);
  ui.state.view.snapshot.self_hand = [slash, dodge, peach];
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ui.state.view);
  check("㉿B11 换询问整体重算：【杀】【闪】灰、【桃】亮（不保留上一问的亮面）",
    handChips()[0].classList.contains("dis") && handChips()[1].classList.contains("dis")
      && !handChips()[2].classList.contains("dis"),
    `dis=${handChips().map((c) => c.classList.contains("dis")).join(",")}`);

  ui.state.view = synthView(null);
  ui.state.view.snapshot.self_hand = [slash, dodge, peach];
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ui.state.view);
  check("㉿B12 退出询问整体重算：无挂起时全部按不可用判（提供面的亮面不粘滞）",
    handChips().every((c) => c.classList.contains("dis")),
    `dis=${handChips().map((c) => c.classList.contains("dis")).join(",")}`);
}

/* ============ ㉿ 遗计：一次选两张 / 取消（票 15 的用户口径） ============
 *
 * 用户原话（`acceptance.md` §C）："一次给出这两张牌时就需要**允许同时选择两张牌**
 * （目前不行）"。这里用**真前端**的 `renderSlots` 点击路径验四件事：
 * 面上每张牌都能点、点两张 = picks 收两个面内位置、提交的是那条候选补上 picks、
 * 再点第三张**不越界**（上限 = 服务端 `spec.total` 的纯函数）。
 * 面里铺三张（比上限多一张）就是为了让"越界拒绝"可观察——它是"允许两张"的另一半。
 * 私密（牌面只给持有者）由 `test_hero_damage_after.py` / `test_web.py` 的引擎侧判据钉。
 */
{
  const first = synthCard(1, "桃园结义");
  const second = synthCard(2, "万箭齐发");
  const third = synthCard(3, "杀");
  const yijiPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 921,
    prompt: "请从选择面里选择一至两张牌。",
    legal_actions: [
      { kind: "SelectCards", picks: [], answers_seq: 921 },
      { kind: "SelectCards", picks: [0], answers_seq: 921 },
      { kind: "SelectCards", picks: [1], answers_seq: 921 },
      { kind: "SelectCards", picks: [0, 1], answers_seq: 921 },
      { kind: "Pass", answers_seq: 921 },
    ],
    candidate_index: {},
    slots: [
      { ref: { zone: "hand", seat: 0 }, card: first },
      { ref: { zone: "hand", seat: 0 }, card: second },
      { ref: { zone: "hand", seat: 0 }, card: third },
    ],
    // 面里是**观看来的**亮牌（不是手牌区的牌）：上限两张，选中的两张一起交给同一名角色。
    spec: { zones: ["hand"], total: [1, 2], optional: true },
    virtual_index: {}, option_seats: {}, context: null,
  };
  const view = synthView(yijiPending);
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  // 每次点击都会重铺选择面（`renderPending` 清空 `t-options`），故槽位要**重新取**，
  // 否则断言读的是被换掉的旧节点（第一版就在这里红了）。
  const slotChips = () => {
    const row = $("t-options").children.find((c) => c.className === "cards");
    return row ? row.children : [];
  };
  const click = (index) => (slotChips()[index].listeners.click || []).forEach((fn) => fn({ target: slotChips()[index] }));

  check("㉿G1 选牌面铺出亮牌，上限读服务端 spec.total（不是写死 1）",
    slotChips().length === 3 && ui.selectionCap(yijiPending) === 2,
    `槽位=${slotChips().length} cap=${ui.selectionCap(yijiPending)}`);
  check("㉿G2 未选时不可提交", $("btn-submit").disabled === true);

  click(0);
  check("㉿G3 点第一张 = picks 收下位置 0", JSON.stringify(ui.state.picks) === "[0]",
    `picks=${JSON.stringify(ui.state.picks)}`);
  click(1);
  check("㉿G4 一次可以点两张（用户口径：允许同时选择两张牌）",
    JSON.stringify(ui.state.picks) === "[0,1]", `picks=${JSON.stringify(ui.state.picks)}`);
  check("㉿G5 两张都能画成选中态（sel）",
    slotChips()[0].classList.contains("sel") && slotChips()[1].classList.contains("sel")
      && !slotChips()[2].classList.contains("sel"),
    `sel=${slotChips().map((c) => c.classList.contains("sel")).join(",")}`);
  const action = ui.buildAction();
  check("㉿G6 提交的是候选里那条 SelectCards 补上两个面内位置",
    !!action && action.kind === "SelectCards" && JSON.stringify(action.picks) === "[0,1]",
    JSON.stringify(action));
  check("㉿G7 选够两张后提交按钮可点", $("btn-submit").disabled === false,
    `disabled=${$("btn-submit").disabled}`);

  click(2);
  check("㉿G8 上限就是两张：再点第三张不越界（上限 = spec.total 的纯函数）",
    JSON.stringify(ui.state.picks) === "[0,1]", `picks=${JSON.stringify(ui.state.picks)}`);
  click(0);
  check("㉿G9 再点已选中的那张 = 取消它（另一张仍在）",
    JSON.stringify(ui.state.picks) === "[1]", `picks=${JSON.stringify(ui.state.picks)}`);
  click(2);
  check("㉿G10 取消掉一张后名额释放：第三张现在收得下（不是粘滞的置灰）",
    JSON.stringify(ui.state.picks) === "[1,2]", `picks=${JSON.stringify(ui.state.picks)}`);
}

// ② 仁德（W4-2）：点技能 → 按**清单**两步（多选手牌 → 点武将）→ 确认 = 一个复合动作。
//    验收：界面上**不再**出现"发动技能 XXX"选项；确认 / 取消是底部两个按钮。
{
  const first = synthCard(11, "杀");
  const second = synthCard(12, "闪");
  const manifest = [
    { name: "card", zones: ["hand"], min: 1, max: 2, order: 0,
      pool: [{ when: [], options: ["11", "12"] }] },
    { name: "seat", zones: [], min: 1, max: 1, order: 1,
      pool: [{ when: [], options: ["seat:1", "seat:2"] }] },
  ];
  const view = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 901, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 901 }],
    preactivations: { "hero.liubei.rende": manifest },
    preactivation: [],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
  });
  view.heroes = { 0: "liubei", 1: "guanyu", 2: "diaochan" };
  view.snapshot.self_hand = [first, second];
  view.skills = [{
    skill_id: "hero.liubei.rende", name: "仁德", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "交给一名其他角色任意张手牌", convert_sources: [] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const button = $("t-skills").children.find((b) => b.getAttribute("data-skill-id") === "hero.liubei.rende");
  check("㉿R1 仁德是**预发动**入口（按钮可点、标出步骤数）",
    !!button && button.disabled === false
      && button.getAttribute("data-preactivated") === "true"
      && button.getAttribute("data-preactivation") === "2",
    `disabled=${button && button.disabled} pre=${button && button.getAttribute("data-preactivation")}`);
  check("㉿R1b 出牌阶段没有「发动技能 XXX」选项",
    !$("t-options").children.some((b) => /发动技能/.test(b.textContent)),
    `选项=${JSON.stringify($("t-options").children.map((b) => b.textContent))}`);
  (button.listeners.click || []).forEach((fn) => fn({ target: button }));
  check("㉿R2 点仁德 = 进入预发动（提示 = 预发动【仁德】：按清单逐步选择）",
    !!ui.state.preactivation && ui.state.preactivation.skillId === "hero.liubei.rende"
      && ui.state.preactivation.steps.length === 2
      && /预发动【仁德】/.test($("t-prompt").textContent),
    `mode=${JSON.stringify(ui.state.preactivation)} prompt="${$("t-prompt").textContent}"`);
  const handChip = (index) => $("t-hand").children[index];
  (handChip(0).listeners.click || []).forEach((fn) => fn({ target: handChip(0) }));
  (handChip(1).listeners.click || []).forEach((fn) => fn({ target: handChip(1) }));
  check("㉿R3 步 1 多选：两张都收进本地答案",
    JSON.stringify(ui.state.preactivation.answers[0].values) === "[11,12]",
    JSON.stringify(ui.state.preactivation.answers[0]));
  // 多选步：点已选 = 取消该张、点新牌 = 追加（用户裁定的重选规则）。
  (handChip(0).listeners.click || []).forEach((fn) => fn({ target: handChip(0) }));
  check("㉿R3b 多选步再点已选那张 = 取消它（另一张仍在）",
    JSON.stringify(ui.state.preactivation.answers[0].values) === "[12]",
    JSON.stringify(ui.state.preactivation.answers[0]));
  (handChip(0).listeners.click || []).forEach((fn) => fn({ target: handChip(0) }));
  check("㉿R3c 再点一次 = 追加回来",
    JSON.stringify(ui.state.preactivation.answers[0].values) === "[12,11]",
    JSON.stringify(ui.state.preactivation.answers[0]));
  const next = $("t-options").children.find((b) => b.textContent === "下一步");
  check("㉿R4 张数达标后「下一步」可点", !!next && next.disabled === false);
  (next.listeners.click || []).forEach((fn) => fn({ target: next }));
  check("㉿R5 进第 2 步：只高亮清单给的两名其他角色",
    ui.state.preactivation.cursor === 1
      && ui.preactivationOptions(ui.state.preactivation, 1).join(",") === "seat:1,seat:2",
    `cursor=${ui.state.preactivation.cursor}`);
  const targetBox = $("t-seats").children.find((c) => c.getAttribute("data-seat") === "2");
  check("㉿R6 目标武将画成可点（直接点场上武将）",
    !!targetBox && targetBox.classList.contains("targetable"));
  ui.state.sessionId = "smoke-missing-session";
  const beforeOk = calls.length;
  (targetBox.listeners.click || []).forEach((fn) => fn({ target: targetBox }));
  const composite = ui.buildAction();
  check("㉿R7 填完 = 复合动作（picks = 手牌 id、choices = seat:N），按钮文案是「确认发动」",
    !!composite && composite.kind === "ActivateSkill" && composite.skill_id === "hero.liubei.rende"
      && JSON.stringify(composite.picks) === "[12,11]"
      && JSON.stringify(composite.choices) === '["seat:2"]'
      && $("btn-submit").textContent === "确认发动",
    JSON.stringify(composite));
  check("㉿R7b 点座位**不**自动提交（确认由玩家按）", calls.length === beforeOk,
    `calls=${calls.length - beforeOk}`);
  (button.listeners.click || []).forEach((fn) => fn({ target: button }));
  const cancelButton = $("btn-cancel");
  (cancelButton.listeners.click || []).forEach((fn) => fn({ target: cancelButton }));
  check("㉿R8 取消预发动 = 本地态清空、零 action 提交",
    ui.state.preactivation === null && calls.length === beforeOk,
    `pre=${ui.state.preactivation} calls=${calls.length - beforeOk}`);
}

/* ============ 票 01（合成局面）：【离间】流水线 —— 逐步文案 + 取消送出 Pass ============
 *
 * 三步都是**服务端驱动的询问**（点技能 → SELECT_CARDS → SELECT_OPTION ×2），前端只按
 * `pending` 渲染、把候选原样送回去。这里喂与服务端 `pending_payload` 同形的合成 view，
 * 钉三件事：逐步文案真是服务端那句、两步座位共用同一句问法、有 `Pass` 时取消按钮
 * **送出一条候选**（而不是只清本地状态）。
 */
{
  const handCard = synthCard(21, "杀");
  const equipCard = { ...synthCard(22, "的卢"), type: "mount" };
  const lijianSkill = {
    skill_id: "hero.diaochan.lijian", name: "离间", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true,
    ui: {
      requirement: "弃一张牌，选择两名男性角色",
      convert_sources: [],
    },
  };

  // ① 步 1：弃置 1 张牌（手牌 ∪ 装备区），询问里带一条 `Pass` = 可取消。
  const pickView = synthView({
    kind: "SELECT_CARDS", actor: 0, seq: 910, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { kind: "SelectCards", answers_seq: 910, picks: [] },
      { kind: "Pass", answers_seq: 910 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [
      { ref: { seat: 0, zone: "hand" }, card: handCard },
      { ref: { seat: 0, zone: "equip" }, card: equipCard },
    ],
    spec: { zones: ["hand", "equip"], total: [1, 1], sequential: false, optional: true },
    context: { requirement: "弃置1张牌", skill_hints: [] },
  });
  pickView.skills = [lijianSkill];
  pickView.heroes = { 0: "diaochan", 1: "guanyu", 2: "zhangfei" };
  pickView.snapshot.self_hand = [handCard];
  pickView.snapshot.players[0].equip = [equipCard];
  ui.state.view = pickView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(pickView);

  check("㉿J1 步 1 的询问区文案就是服务端那句「弃置1张牌」",
    $("t-prompt").textContent === "弃置1张牌", `"${$("t-prompt").textContent}"`);
  check("㉿J2 选择面把手牌与装备区的槽位都铺出来",
    $("t-options").children.length === 2 && $("t-options").children[1].children.length === 2,
    `块=${$("t-options").children.length}`);
  check("㉿J3 候选里有 Pass ⇒ 取消按钮可点（不再只是清空本地选择）",
    $("btn-cancel").disabled === false, `disabled=${$("btn-cancel").disabled}`);

  ui.state.sessionId = "smoke-missing-session";
  const beforePick = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentPick = calls.length > beforePick ? calls[calls.length - 1].body.action : null;
  check("㉿J4 步 1 点取消 = **送出一条 Pass 候选**",
    !!sentPick && sentPick.kind === "Pass", JSON.stringify(sentPick));

  // ② 步 2 / 3：先点使用者、后点目标；同一句问法，座位可点。
  const seatView = synthView({
    kind: "SELECT_OPTION", actor: 0, seq: 911, prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "seat:1", answers_seq: 911 },
      { kind: "SelectOption", choice: "seat:2", answers_seq: 911 },
      { kind: "Pass", answers_seq: 911 },
    ],
    candidate_index: {}, virtual_index: {},
    option_seats: { "seat:1": 1, "seat:2": 2 },
    context: {
      requirement: "选择场上两名男性角色，先点选的是决斗使用者，后点选的是决斗的目标",
      skill_hints: [],
    },
  });
  seatView.skills = [lijianSkill];
  seatView.heroes = { 0: "diaochan", 1: "guanyu", 2: "zhangfei" };
  ui.state.view = seatView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(seatView);

  check("㉿J5 座位步的询问区文案是服务端那句「先点选的是决斗使用者…」",
    $("t-prompt").textContent === "选择场上两名男性角色，先点选的是决斗使用者，后点选的是决斗的目标",
    `"${$("t-prompt").textContent}"`);
  const targetBox = $("t-seats").children.find((c) => c.getAttribute("data-seat") === "1");
  check("㉿J6 男性武将画成可点", !!targetBox && targetBox.classList.contains("targetable"),
    `targetable=${targetBox && targetBox.classList.contains("targetable")}`);
  (targetBox.listeners.click || []).forEach((fn) => fn({ target: targetBox }));
  const seatAction = ui.buildAction();
  check("㉿J7 点第一个男性 = 选中那条 seat:N 候选（提交才送出去）",
    !!seatAction && seatAction.kind === "SelectOption" && seatAction.choice === "seat:1",
    JSON.stringify(seatAction));

  const beforeSeat = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentSeat = calls.length > beforeSeat ? calls[calls.length - 1].body.action : null;
  check("㉿J8 座位步点取消 = 送出 Pass（中途退出这一步）",
    !!sentSeat && sentSeat.kind === "Pass", JSON.stringify(sentSeat));
}

/* ============ 票 02（合成局面）：【青囊】流水线 —— 逐步文案 + 取消送出 Pass ============
 *
 * 形状与【离间】同源（两步都是服务端驱动的询问：点技能 → SELECT_CARDS → SELECT_OPTION），
 * 只有步数与目标池不同。这里喂与服务端 `pending_payload` 同形的合成 view，钉同一组事：
 * 两步文案各是服务端那一句、座位步有 Pass 时取消按钮**送出一条候选**。
 */
{
  const handCard = synthCard(31, "杀");
  const qingnangSkill = {
    skill_id: "hero.huatuo.qingnang", name: "青囊", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true,
    ui: {
      requirement: "弃一张手牌，令一名已受伤角色回复 1 点体力",
      convert_sources: [],
    },
  };

  // ① 步 1：弃置 1 张手牌，询问里带一条 `Pass` = 可取消。
  const pickView = synthView({
    kind: "SELECT_CARDS", actor: 0, seq: 920, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { kind: "SelectCards", answers_seq: 920, picks: [] },
      { kind: "Pass", answers_seq: 920 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [{ ref: { seat: 0, zone: "hand" }, card: handCard }],
    spec: { zones: ["hand"], total: [1, 1], sequential: false, optional: true },
    context: { requirement: "弃置1张手牌", skill_hints: [] },
  });
  pickView.skills = [qingnangSkill];
  pickView.heroes = { 0: "huatuo", 1: "guanyu", 2: "zhangfei" };
  pickView.snapshot.self_hand = [handCard];
  ui.state.view = pickView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(pickView);

  check("㉿H1 步 1 的询问区文案就是服务端那句「弃置1张手牌」",
    $("t-prompt").textContent === "弃置1张手牌", `"${$("t-prompt").textContent}"`);
  check("㉿H2 选择面只铺手牌（弃的是自己的手牌，不是装备区）",
    $("t-options").children.length === 2 && $("t-options").children[1].children.length === 1,
    `块=${$("t-options").children.length}`);
  check("㉿H3 候选里有 Pass ⇒ 取消按钮可点", $("btn-cancel").disabled === false,
    `disabled=${$("btn-cancel").disabled}`);

  ui.state.sessionId = "smoke-missing-session";
  const beforePick = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentPick = calls.length > beforePick ? calls[calls.length - 1].body.action : null;
  check("㉿H4 步 1 点取消 = **送出一条 Pass 候选**",
    !!sentPick && sentPick.kind === "Pass", JSON.stringify(sentPick));

  // ② 步 2：选一名已受伤角色（目标池含自己 ⇒ 选项里有 seat:0 与 seat:2）。
  const seatView = synthView({
    kind: "SELECT_OPTION", actor: 0, seq: 921, prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "seat:0", answers_seq: 921 },
      { kind: "SelectOption", choice: "seat:2", answers_seq: 921 },
      { kind: "Pass", answers_seq: 921 },
    ],
    candidate_index: {}, virtual_index: {},
    option_seats: { "seat:0": 0, "seat:2": 2 },
    context: { requirement: "选择一名已受伤角色回复1点体力", skill_hints: [] },
  });
  seatView.skills = [qingnangSkill];
  seatView.heroes = { 0: "huatuo", 1: "guanyu", 2: "zhangfei" };
  ui.state.view = seatView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(seatView);

  check("㉿H5 步 2 的询问区文案就是服务端那句「选择一名已受伤角色回复1点体力」",
    $("t-prompt").textContent === "选择一名已受伤角色回复1点体力",
    `"${$("t-prompt").textContent}"`);
  const woundedSeat = seatBox(2);
  check("㉿H6 已受伤角色画成可点", !!woundedSeat && woundedSeat.classList.contains("targetable"),
    `targetable=${woundedSeat && woundedSeat.classList.contains("targetable")}`);
  (woundedSeat.listeners.click || []).forEach((fn) => fn({ target: woundedSeat }));
  const seatAction = ui.buildAction();
  check("㉿H7 点它 = 选中那条 seat:N 候选（提交才送出去）",
    !!seatAction && seatAction.kind === "SelectOption" && seatAction.choice === "seat:2",
    JSON.stringify(seatAction));

  const beforeQingnangSeat = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentQingnangSeat = calls.length > beforeQingnangSeat
    ? calls[calls.length - 1].body.action : null;
  check("㉿H8 步 2 点取消 = 送出 Pass（中途退出这一步）",
    !!sentQingnangSeat && sentQingnangSeat.kind === "Pass", JSON.stringify(sentQingnangSeat));
}

/* ============ 票 03（合成局面）：【结姻】流水线 —— 逐步文案 + 取消送出 Pass ============
 *
 * 形状与【青囊】同源（两步都是服务端驱动的询问：点技能 → SELECT_CARDS → SELECT_OPTION），
 * 差别只在成本是**恰好两张手牌**、目标池是"已受伤男性（不含自己）"。这里喂与服务端
 * `pending_payload` 同形的合成 view，钉同一组事：两步文案各是服务端那一句、有 Pass 时
 * 取消按钮**送出一条候选**。
 */
{
  const firstCard = synthCard(41, "杀");
  const secondCard = synthCard(42, "闪");
  const jieyinSkill = {
    skill_id: "hero.sunshangxiang.jieyin", name: "结姻", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true,
    ui: {
      requirement: "弃两张手牌，选择一名已受伤男性角色",
      convert_sources: [],
    },
  };

  // ① 步 1：一并弃置恰好 2 张手牌，询问里带一条 `Pass` = 可取消。
  const pickView = synthView({
    kind: "SELECT_CARDS", actor: 0, seq: 930, prompt: "请从选择面里选择两张牌。",
    legal_actions: [
      { kind: "SelectCards", answers_seq: 930, picks: [0, 1] },
      { kind: "Pass", answers_seq: 930 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [
      { ref: { seat: 0, zone: "hand" }, card: firstCard },
      { ref: { seat: 0, zone: "hand" }, card: secondCard },
    ],
    spec: { zones: ["hand"], total: [2, 2], sequential: false, optional: true },
    context: { requirement: "弃置2张手牌", skill_hints: [] },
  });
  pickView.skills = [jieyinSkill];
  pickView.heroes = { 0: "sunshangxiang", 1: "guanyu", 2: "zhangfei" };
  pickView.snapshot.self_hand = [firstCard, secondCard];
  ui.state.view = pickView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(pickView);

  check("㉿Y1 步 1 的询问区文案就是服务端那句「弃置2张手牌」",
    $("t-prompt").textContent === "弃置2张手牌", `"${$("t-prompt").textContent}"`);
  check("㉿Y2 选择面把手牌两张都铺出来（恰好两张，一次问完）",
    $("t-options").children.length === 2 && $("t-options").children[1].children.length === 2,
    `块=${$("t-options").children.length}`);
  check("㉿Y3 候选里有 Pass ⇒ 取消按钮可点", $("btn-cancel").disabled === false,
    `disabled=${$("btn-cancel").disabled}`);

  ui.state.sessionId = "smoke-missing-session";
  const beforePick = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentPick = calls.length > beforePick ? calls[calls.length - 1].body.action : null;
  check("㉿Y4 步 1 点取消 = **送出一条 Pass 候选**",
    !!sentPick && sentPick.kind === "Pass", JSON.stringify(sentPick));

  // ② 步 2：选一名已受伤男性（目标池不含自己 ⇒ 选项里只有 seat:1 / seat:2）。
  const seatView = synthView({
    kind: "SELECT_OPTION", actor: 0, seq: 931, prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "seat:1", answers_seq: 931 },
      { kind: "SelectOption", choice: "seat:2", answers_seq: 931 },
      { kind: "Pass", answers_seq: 931 },
    ],
    candidate_index: {}, virtual_index: {},
    option_seats: { "seat:1": 1, "seat:2": 2 },
    context: { requirement: "选择一名已受伤男性角色回复1点体力", skill_hints: [] },
  });
  seatView.skills = [jieyinSkill];
  seatView.heroes = { 0: "sunshangxiang", 1: "guanyu", 2: "zhangfei" };
  ui.state.view = seatView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(seatView);

  check("㉿Y5 步 2 的询问区文案就是服务端那句「选择一名已受伤男性角色回复1点体力」",
    $("t-prompt").textContent === "选择一名已受伤男性角色回复1点体力",
    `"${$("t-prompt").textContent}"`);
  const woundedMaleSeat = seatBox(1);
  check("㉿Y6 已受伤男性画成可点（自己不在池里）",
    !!woundedMaleSeat && woundedMaleSeat.classList.contains("targetable"),
    `targetable=${woundedMaleSeat && woundedMaleSeat.classList.contains("targetable")}`);
  (woundedMaleSeat.listeners.click || []).forEach((fn) => fn({ target: woundedMaleSeat }));
  const seatAction = ui.buildAction();
  check("㉿Y7 点它 = 选中那条 seat:N 候选（提交才送出去）",
    !!seatAction && seatAction.kind === "SelectOption" && seatAction.choice === "seat:1",
    JSON.stringify(seatAction));

  const beforeJieyinSeat = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentJieyinSeat = calls.length > beforeJieyinSeat
    ? calls[calls.length - 1].body.action : null;
  check("㉿Y8 步 2 点取消 = 送出 Pass（中途退出这一步）",
    !!sentJieyinSeat && sentJieyinSeat.kind === "Pass", JSON.stringify(sentJieyinSeat));
}

/* ============ 票 04（合成局面）：【制衡】单步流水线 —— 任意张 + 取消送出 Pass ============
 *
 * 只有一步：一条 `SELECT_CARDS` 覆盖**手牌 ∪ 装备区**、`total = (1, 全部可选)`。钉四件事：
 * 文案是服务端那句、选择面同时铺出手牌与装备区、上限是**真实池大小**而不是哨兵值、
 * 有 `Pass` 时取消按钮**送出一条候选**。
 */
{
  const handCard = synthCard(51, "杀");
  const equipCard = { ...synthCard(52, "的卢"), type: "mount" };
  const zhihengSkill = {
    skill_id: "hero.sunquan.zhiheng", name: "制衡", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true,
    ui: {
      requirement: "弃置任意张牌（手牌 / 装备区），然后摸等量的牌",
      convert_sources: [],
    },
  };

  const pickView = synthView({
    kind: "SELECT_CARDS", actor: 0, seq: 940, prompt: "请从选择面里选择任意张牌。",
    legal_actions: [
      { kind: "SelectCards", answers_seq: 940, picks: [0] },
      { kind: "SelectCards", answers_seq: 940, picks: [0, 1] },
      { kind: "Pass", answers_seq: 940 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [
      { ref: { seat: 0, zone: "hand" }, card: handCard },
      { ref: { seat: 0, zone: "equip" }, card: equipCard },
    ],
    spec: { zones: ["hand", "equip"], total: [1, 2], sequential: false, optional: true },
    context: { requirement: "弃置任意张牌（手牌 / 装备区）", skill_hints: [] },
  });
  pickView.skills = [zhihengSkill];
  pickView.heroes = { 0: "sunquan", 1: "guanyu" };
  pickView.snapshot.self_hand = [handCard];
  pickView.snapshot.players[0].equip = [equipCard];
  ui.state.view = pickView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(pickView);

  check("㉿Z1 单步的询问区文案就是服务端那句「弃置任意张牌（手牌 / 装备区）」",
    $("t-prompt").textContent === "弃置任意张牌（手牌 / 装备区）",
    `"${$("t-prompt").textContent}"`);
  check("㉿Z2 选择面同时铺出手牌与自己的装备区（2 个块 / 共 2 个槽）",
    $("t-options").children.length === 2 && $("t-options").children[1].children.length === 2,
    `块=${$("t-options").children.length}`);
  check("㉿Z3 「任意张」的上限是**真实池大小**（服务端解析 hi=None，不进哨兵值）",
    JSON.stringify(pickView.pending.spec.total) === "[1,2]",
    JSON.stringify(pickView.pending.spec.total));
  check("㉿Z4 候选里有 Pass ⇒ 取消按钮可点", $("btn-cancel").disabled === false,
    `disabled=${$("btn-cancel").disabled}`);

  ui.state.sessionId = "smoke-missing-session";
  const beforePick = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentPick = calls.length > beforePick ? calls[calls.length - 1].body.action : null;
  check("㉿Z5 点取消 = **送出一条 Pass 候选**",
    !!sentPick && sentPick.kind === "Pass", JSON.stringify(sentPick));
}

/* ============ 票 05（合成局面）：【仁德】流水线 —— 逐步文案 + 取消送出 Pass ============
 *
 * 两步：`SELECT_CARDS`（手牌、任意张、上限 = 真实池大小）→ `SELECT_OPTION`（交给谁）。
 * 与【青囊】/【结姻】/【制衡】同形，钉同一组事：两步文案各是服务端那一句、有 `Pass` 时
 * 取消按钮**送出一条候选**。
 */
{
  const firstCard = synthCard(61, "杀");
  const secondCard = synthCard(62, "闪");
  const rendeSkill = {
    skill_id: "hero.liubei.rende", name: "仁德", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true,
    ui: {
      requirement: "交给一名其他角色任意张手牌",
      convert_sources: [],
    },
  };

  // ① 步 1：选任意张手牌交出去，询问里带一条 `Pass` = 可取消。
  const pickView = synthView({
    kind: "SELECT_CARDS", actor: 0, seq: 950, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { kind: "SelectCards", answers_seq: 950, picks: [0] },
      { kind: "SelectCards", answers_seq: 950, picks: [0, 1] },
      { kind: "Pass", answers_seq: 950 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [
      { ref: { seat: 0, zone: "hand" }, card: firstCard },
      { ref: { seat: 0, zone: "hand" }, card: secondCard },
    ],
    spec: { zones: ["hand"], total: [1, 2], sequential: false, optional: true },
    context: { requirement: "选择任意张要交给其他角色的手牌", skill_hints: [] },
  });
  pickView.skills = [rendeSkill];
  pickView.heroes = { 0: "liubei", 1: "guanyu" };
  pickView.snapshot.self_hand = [firstCard, secondCard];
  ui.state.view = pickView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(pickView);

  check("㉿W1 步 1 的询问区文案就是服务端那句「选择任意张要交给其他角色的手牌」",
    $("t-prompt").textContent === "选择任意张要交给其他角色的手牌",
    `"${$("t-prompt").textContent}"`);
  check("㉿W2 选择面把本人手牌铺出来（2 个槽）、上限是真实池大小",
    $("t-options").children.length === 2 && $("t-options").children[1].children.length === 2
      && JSON.stringify(pickView.pending.spec.total) === "[1,2]",
    `块=${$("t-options").children.length} total=${JSON.stringify(pickView.pending.spec.total)}`);
  check("㉿W3 候选里有 Pass ⇒ 取消按钮可点", $("btn-cancel").disabled === false,
    `disabled=${$("btn-cancel").disabled}`);

  ui.state.sessionId = "smoke-missing-session";
  const beforePick = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentPick = calls.length > beforePick ? calls[calls.length - 1].body.action : null;
  check("㉿W4 步 1 点取消 = **送出一条 Pass 候选**",
    !!sentPick && sentPick.kind === "Pass", JSON.stringify(sentPick));

  // ② 步 2：点一名其他角色（选项 = seat:1 / seat:2，自己不在池里）。
  const seatView = synthView({
    kind: "SELECT_OPTION", actor: 0, seq: 951, prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "seat:1", answers_seq: 951 },
      { kind: "SelectOption", choice: "seat:2", answers_seq: 951 },
      { kind: "Pass", answers_seq: 951 },
    ],
    candidate_index: {}, virtual_index: {},
    option_seats: { "seat:1": 1, "seat:2": 2 },
    context: { requirement: "选择交给哪名其他角色", skill_hints: [] },
  });
  seatView.skills = [rendeSkill];
  seatView.heroes = { 0: "liubei", 1: "guanyu", 2: "zhangfei" };
  ui.state.view = seatView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(seatView);

  check("㉿W5 步 2 的询问区文案就是服务端那句「选择交给哪名其他角色」",
    $("t-prompt").textContent === "选择交给哪名其他角色",
    `"${$("t-prompt").textContent}"`);
  const rendeSeat = $("t-seats").children.find((c) => c.getAttribute("data-seat") === "2");
  check("㉿W6 其他角色画成可点", !!rendeSeat && rendeSeat.classList.contains("targetable"),
    `targetable=${rendeSeat && rendeSeat.classList.contains("targetable")}`);
  (rendeSeat.listeners.click || []).forEach((fn) => fn({ target: rendeSeat }));
  const rendeAction = ui.buildAction();
  check("㉿W7 点它 = 选中那条 seat:N 候选（提交才送出去）",
    !!rendeAction && rendeAction.kind === "SelectOption" && rendeAction.choice === "seat:2",
    JSON.stringify(rendeAction));

  const beforeRendeSeat = calls.length;
  await clickAndWait($("btn-cancel"));
  const sentRendeSeat = calls.length > beforeRendeSeat
    ? calls[calls.length - 1].body.action : null;
  check("㉿W8 步 2 点取消 = 送出 Pass（中途退出这一步）",
    !!sentRendeSeat && sentRendeSeat.kind === "Pass", JSON.stringify(sentRendeSeat));
}

/* ============ 票 06（合成局面）：【反间】三步流水线 —— 应答者是**目标** ============
 *
 * 步 1 问发动者（选谁），步 2/3 问**目标**（猜花色、从**发动者**手牌里挑一张）。钉三件事：
 * 三步各自的文案（步 2/3 要写出归属）、步 3 的选择面铺的是发动者的牌、取消按钮送出 `Pass`。
 */
{
  const fanjianSkill = {
    skill_id: "hero.zhouyu.fanjian", name: "反间", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true,
    ui: {
      requirement: "选择一种花色",
      convert_sources: [],
    },
  };

  // ① 步 1：发动者选谁被反间。
  const fanSeatView = synthView({
    kind: "SELECT_OPTION", actor: 0, seq: 960, prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "seat:1", answers_seq: 960 },
      { kind: "Pass", answers_seq: 960 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: { "seat:1": 1 },
    context: { requirement: "选择一名其他角色", skill_hints: [] },
  });
  fanSeatView.skills = [fanjianSkill];
  fanSeatView.heroes = { 0: "zhouyu", 1: "guanyu" };
  ui.state.view = fanSeatView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(fanSeatView);

  check("㉿V1 步 1 的文案 =「选择一名其他角色」（由发动者应答）",
    $("t-prompt").textContent === "选择一名其他角色", `"${$("t-prompt").textContent}"`);

  // 真实会话 id 只用于让"一击即提交"真的走 `submitAction`；请求本身由 400 拦截器挡下，
  // 不进引擎（与 U2-03 的注入用法一致）。
  const fanSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });

  // ② 步 2：**目标**猜花色（`pending.actor` 是别人 ⇒ 文案必须写出归属）。
  // 颜色 / 中文名 / 符号都是服务端 `option_labels` 下发的（票 08 / D10）。
  const suitView = synthView({
    kind: "SELECT_OPTION", actor: 1, seq: 961, prompt: "请选择一项。",
    options: ["heart", "diamond", "spade", "club"],
    option_labels: {
      heart: { name: "红桃", symbol: "♥", color: "red" },
      diamond: { name: "方块", symbol: "♦", color: "red" },
      spade: { name: "黑桃", symbol: "♠", color: "black" },
      club: { name: "梅花", symbol: "♣", color: "black" },
    },
    legal_actions: [
      { kind: "SelectOption", choice: "heart", answers_seq: 961 },
      { kind: "SelectOption", choice: "diamond", answers_seq: 961 },
      { kind: "SelectOption", choice: "spade", answers_seq: 961 },
      { kind: "SelectOption", choice: "club", answers_seq: 961 },
      { kind: "Pass", answers_seq: 961 },
    ],
    candidate_index: {}, virtual_index: {},
    context: {
      actor: 1, name: "反间", targets: [1],
      label: "0号位周瑜对1号位关羽使用反间",
      requirement: "由目标选择一种花色", skill_hints: [],
    },
  });
  suitView.skills = [fanjianSkill];
  suitView.heroes = { 0: "zhouyu", 1: "guanyu" };
  // U1-03：界面只在**询问者就是自己**时铺可点控件；这一问的 actor 是 1 号位，
  // 故把查看者也放在 1 号位（真实会话里 human_seat 就是询问者）。
  suitView.snapshot.seat = 1;
  suitView.human_seat = 1;
  ui.state.view = suitView;
  ui.state.sessionId = fanSession.session_id;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(suitView);

  check("㉿V2 步 2 的文案写明归属 =「由目标选择一种花色」",
    $("t-prompt").textContent === "由目标选择一种花色", `"${$("t-prompt").textContent}"`);
  check("㉿V2b 花色步走选择型弹窗：四个中文按钮 + 服务端符号 / 红黑，无「放弃」",
    ui.stepDialogKind(suitView.pending) === "choice"
      && $("t-dialog").hidden === false
      && $("t-options").children.length === 4
      && $("t-options").children.map((node) => node.textContent).join(",")
        === "♥红桃,♦方块,♠黑桃,♣梅花"
      && $("t-options").children.map((node) => node.getAttribute("data-color")).join(",")
        === "red,red,black,black"
      && $("btn-pass").hidden === true
      && !/heart|diamond|spade|club/.test($("t-options").text()),
    `kind=${ui.stepDialogKind(suitView.pending)} 选项=${JSON.stringify($("t-options").children.map((node) => node.textContent))} passHidden=${$("btn-pass").hidden}`);

  // 一击即提交：点一张花色 = 恰好一条 `SelectOption` POST（没有「下一步」要按），
  // 送出的就是 `legal_actions` 里那一条。
  const suitButton = $("t-options").children[2];
  const beforeSuit = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait(suitButton);
  staleAnswerStatus = null;
  const suitAnswers = calls.slice(beforeSuit).filter((call) => call.path.includes("/answer"));
  check("㉿V3 点一张花色 = 直接提交（一次 POST，动作原样取自候选；无「下一步」）",
    suitAnswers.length === 1
      && JSON.stringify(suitAnswers[0].body.action)
        === JSON.stringify(suitView.pending.legal_actions[2])
      && /提交被拒/.test($("t-choice").textContent),
    `answers=${suitAnswers.length} action=${JSON.stringify(suitAnswers[0] && suitAnswers[0].body.action)}`);

  const beforeCancel = calls.length;
  await clickAndWait($("btn-cancel"));
  const cancelAnswers = calls.slice(beforeCancel).filter((call) => call.path.includes("/answer"));
  check("㉿V4 花色步的取消键不送 Pass（没有隐藏的「放弃」）",
    $("btn-pass").hidden === true && cancelAnswers.length === 0,
    `passHidden=${$("btn-pass").hidden} answers=${cancelAnswers.length}`);

  // ③ 步 3：**目标**从**发动者**手里盲挑一张（槽位只有位置、没有牌面）。
  const fanCardView = synthView({
    kind: "SELECT_CARDS", actor: 1, seq: 962, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      ...[0, 1, 2, 3, 4].map((index) => ({ kind: "SelectCards", answers_seq: 962, picks: [index] })),
      { kind: "Pass", answers_seq: 962 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [0, 1, 2, 3, 4].map(() => ({ ref: { seat: 0, zone: "hand" }, card: null })),
    spec: { zones: ["hand"], total: [1, 1], sequential: false, optional: true },
    context: {
      actor: 1, name: "反间", targets: [1],
      label: "0号位周瑜对1号位关羽使用反间",
      requirement: "由目标从你的手牌中选择一张", skill_hints: [],
    },
  });
  fanCardView.skills = [fanjianSkill];
  fanCardView.heroes = { 0: "zhouyu", 1: "guanyu" };
  fanCardView.snapshot.seat = 1;
  fanCardView.human_seat = 1;
  fanCardView.snapshot.self_hand = [];
  const keyBeforeCard = ui.state.dialog.key;
  ui.state.view = fanCardView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(fanCardView);

  check("㉿V5 步 3 的文案写明归属 =「由目标从你的手牌中选择一张」",
    $("t-prompt").textContent === "由目标从你的手牌中选择一张",
    `"${$("t-prompt").textContent}"`);
  check("㉿V6 步 3 是未知区域弹窗：五个「?」槽位、同一容器继续、无「放弃」",
    ui.stepDialogKind(fanCardView.pending) === "unknown"
      && $("t-dialog").hidden === false
      && ui.state.dialog.key === keyBeforeCard
      && $("t-options").children.length === 1
      && $("t-options").children[0].text().includes("?")
      && $("t-options").children[0].children[1].children.length === 5
      && $("btn-pass").hidden === true
      && !/heart|diamond|spade|club/.test($("t-options").text()),
    `kind=${ui.stepDialogKind(fanCardView.pending)} key=${ui.state.dialog.key} 块=${$("t-options").children.length}`);

  const beforeFanCard = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("t-options").children[0].children[1].children[2]);
  staleAnswerStatus = null;
  const cardAnswers = calls.slice(beforeFanCard).filter((call) => call.path.includes("/answer"));
  check("㉿V7 点一张「?」= 直接提交（一次 SelectCards POST，picks = 槽位位置）",
    cardAnswers.length === 1
      && JSON.stringify(cardAnswers[0].body.action)
        === JSON.stringify(fanCardView.pending.legal_actions[2]),
    `answers=${cardAnswers.length} action=${JSON.stringify(cardAnswers[0] && cardAnswers[0].body.action)}`);

  // 人类是周瑜时：花色 / 盲挑选牌问的是 **AI 目标**（actor ≠ 查看者），不该在人类屏幕上
  // 开一个人工弹窗；牌桌等对方答完。
  const foreignView = synthView({
    ...suitView.pending, actor: 1, seq: 963,
  });
  foreignView.human_seat = 0;
  foreignView.snapshot.seat = 0;
  ui.state.view = foreignView;
  ui.resetSelection();
  ui.renderPending(foreignView);
  check("㉿V8 目标不是人类时不开人工弹窗（等 AI 目标应答）",
    $("t-dialog").hidden === true
      && $("t-prompt").textContent.includes("等待其他座位响应"),
    `dialogHidden=${$("t-dialog").hidden} prompt="${$("t-prompt").textContent}"`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ 报障 3（合成局面）：事件逐个回放 + 效果区逐事件派生 ============ */
{
  const moveIn = {
    kind: "delta", delta_type: "card_move", seq: 501, reason: "USE", owner: 0,
    from_zone: { seat: 0, zone: "hand" }, to_zone: { seat: null, zone: "processing" },
    cards: [synthCard(6, "杀")],
  };
  const moveOut = {
    kind: "delta", delta_type: "card_move", seq: 502, reason: "USE", owner: 0,
    from_zone: { seat: null, zone: "processing" }, to_zone: { seat: null, zone: "discard" },
    cards: [synthCard(6, "杀")],
  };
  const base = synthView(null);
  base.snapshot.processing = [];   // 这条用例测的是"逐事件进出处理区"，起点必须是空的
  const before = { ...base, cursor: 500, log_seq: 0, records: [], finished: false };
  const after = {
    ...base, cursor: 502, log_seq: 0, records: [moveIn, moveOut], finished: false,
    pending: { kind: "PLAY_PHASE", actor: 0, seq: 502, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。", legal_actions: [{ kind: "EndPhase", answers_seq: 502 }], candidate_index: {}, virtual_index: {}, option_seats: {}, context: null },
  };
  $("t-log").innerHTML = "";
  ui.state.logRecords = [];
  ui.state.view = null;
  ui.applyViewRaw(before);
  // 步长按钮：默认 0.1s，点一下在 0.05→0.5 六档之间循环（不记住，刷新回默认）。
  check("㉿E1 默认步长 0.1 秒", ui.state.eventDelayMs === 100, `eventDelayMs=${ui.state.eventDelayMs}`);
  check("㉿E1b 档位就是 0.05–0.5 秒六档（最小挡 0.05s 在首位）",
    JSON.stringify(ui.DELAY_STEPS_MS) === JSON.stringify([50, 100, 200, 300, 400, 500]),
    `steps=${JSON.stringify(ui.DELAY_STEPS_MS)}`);
  const delayBtn = $("btn-delay");
  check("㉿E1c 牌桌上有步长按钮，按钮上显示当前档", !!delayBtn && delayBtn.textContent === "步长 0.1s",
    `按钮="${delayBtn ? delayBtn.textContent : "(缺)"}"`);
  const clickDelay = () => (delayBtn.listeners.click || []).forEach((fn) => fn({ target: delayBtn }));
  clickDelay();
  check("㉿E1d 点一下 → 0.2 秒", ui.state.eventDelayMs === 200,
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayBtn.textContent}"`);
  // 走完 0.2 / 0.3 / 0.4 / 0.5 四档，下一档就是新增的最小挡 0.05s。
  for (let i = 0; i < 4; i += 1) clickDelay();
  check("㉿E1f 满档后落到新的最小挡 0.05 秒，按钮文字跟着换",
    ui.state.eventDelayMs === 50 && delayBtn.textContent === "步长 0.05s",
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayBtn.textContent}"`);
  clickDelay();
  check("㉿E1e 再点一下循环回默认 0.1 秒", ui.state.eventDelayMs === 100 && delayBtn.textContent.includes("0.1"),
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayBtn.textContent}"`);
  ui.applyViewRaw(after);
  check("㉿E2 收到事件先进入回放：快照还没提交、提交按钮灰着",
    !!ui.state.playback && ui.state.view.cursor === 500 && $("btn-submit").disabled === true,
    `playback=${!!ui.state.playback} cursor=${ui.state.view.cursor} disabled=${$("btn-submit").disabled}`);
  check("㉿E2b 回放期间响应文本清空（回放结束、新询问提交后才显示）",
    $("t-prompt").textContent === "", `响应文本="${$("t-prompt").textContent}"`);
  check("㉿E3 播放前效果区还是旧的（空）", !$("t-processing").text().includes("杀"),
    `处理区="${$("t-processing").text().trim()}"`);
  ui.stepPlayback(true);
  check("㉿E4 播到「牌进处理区」⇒ 效果区里看得见这张【杀】",
    $("t-processing").text().includes("杀"), `处理区="${$("t-processing").text().trim()}"`);
  check("㉿E4b 描述文本同一步就跟上效果区（不再残留上一条）",
    $("t-context").textContent.includes("处理中") && $("t-context").textContent.includes("杀")
      && $("t-context").textContent.includes("0号位"),
    `描述="${$("t-context").textContent}"`);
  ui.stepPlayback(true);
  check("㉿E5 播到「离开处理区」⇒ 效果区清空（牌「停留」过一步）",
    !$("t-processing").text().includes("杀"), `处理区="${$("t-processing").text().trim()}"`);
  check("㉿E5b 效果区空了 ⇒ 描述文本立即清空",
    $("t-context").textContent === "", `描述="${$("t-context").textContent}"`);
  ui.flushPlayback();
  check("㉿E6 播完才提交新快照与询问",
    ui.state.playback === null && ui.state.view.cursor === 502 && $("t-prompt").textContent.includes("出牌阶段"),
    `playback=${!!ui.state.playback} cursor=${ui.state.view.cursor}`);
}

/* ============ 报障：描述文本的目标 / 使用者（决斗串味 + 杀缺目标 + 南蛮逐目标） ============
 *
 * 根因：卡牌在 ``USE_CHOOSE_TARGET`` 之后、``USE_DESIGNATE_TARGET`` 之前就进了处理区，
 * 而界面在**进区那一刻**去套"最近一次指定目标"——决斗因此套到上一次【南蛮入侵】的 7 个
 * 目标（报障图 1），杀则因为上一次不是自己而干脆没目标（图 2）。
 * 修法：进区只记使用者；``USE_DESIGNATE_TARGET`` 到了再认领目标；
 * 逐目标结算时用 ``RESOLVE_*`` 的**当前目标**覆盖，南蛮轮到谁就写谁。
 */
{
  const tr = (seq, name, actor, targets) => ({ kind: "timing", seq, timing: name, actor, targets, cards: [] });
  const mv = (seq, owner, card) => ({
    kind: "delta", delta_type: "card_move", seq, reason: "USE", owner,
    from_zone: { seat: owner, zone: "hand" }, to_zone: { seat: null, zone: "processing" }, cards: [card],
  });
  const base = synthView(null);
  base.snapshot.processing = [];
  const before = { ...base, cursor: 600, log_seq: 0, records: [], finished: false };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 900, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 900 }], candidate_index: {}, virtual_index: {},
    option_seats: {}, context: null,
  };
  const play = (records) => {
    const view = { ...base, cursor: 900, log_seq: 0, finished: false, pending, records };
    ui.state.logRecords = [];
    $("t-log").innerHTML = "";
    ui.state.view = null;
    ui.applyViewRaw(before);
    ui.applyViewRaw(view);
  };

  // ① 决斗：上一次是 4 号位的 7 目标【南蛮入侵】；这次决斗只有目标 5。
  play([
    tr(901, "USE_DESIGNATE_TARGET", 4, [5, 6, 7, 0, 1, 2, 3]),
    mv(902, 4, synthCard(20, "决斗")),
    tr(903, "USE_DESIGNATE_TARGET", 4, [5]),
    tr(904, "RESOLVE_START", 4, [5]),
  ]);
  ui.stepPlayback(true);
  ui.stepPlayback(true);
  check("㉿F1 决斗刚进处理区：只写使用者，不套上一次的目标",
    $("t-context").textContent === "处理中：【决斗】（4号位 使用）",
    `描述="${$("t-context").textContent}"`);
  ui.stepPlayback(true);
  check("㉿F2 决斗指定目标后：写它自己的目标（不是上一次南蛮的 7 个）",
    $("t-context").textContent.includes("4号位 对 5号位 使用")
      && !$("t-context").textContent.includes("6号位"),
    `描述="${$("t-context").textContent}"`);
  ui.stepPlayback(true);
  check("㉿F3 逐目标结算窗口里也写得出目标",
    $("t-context").textContent.includes("4号位 对 5号位 使用"),
    `描述="${$("t-context").textContent}"`);

  // ② 杀：进处理区时没有可套的目标（上一次是别人用的），指定目标后才显示。
  play([
    mv(911, 5, synthCard(30, "杀")),
    tr(912, "USE_DESIGNATE_TARGET", 5, [6]),
  ]);
  ui.stepPlayback(true);
  check("㉿F4a 杀刚进处理区：只有使用者",
    $("t-context").textContent === "处理中：【杀】（5号位 使用）",
    `描述="${$("t-context").textContent}"`);
  ui.stepPlayback(true);
  check("㉿F4b 杀指定目标后：补上目标（图 2 的 bug）",
    $("t-context").textContent.includes("5号位 对 6号位 使用"),
    `描述="${$("t-context").textContent}"`);

  // ③ 南蛮入侵：逐目标生效，轮到谁就写谁。
  play([
    mv(921, 3, synthCard(40, "南蛮入侵")),
    tr(922, "USE_DESIGNATE_TARGET", 3, [4, 5, 6, 7, 0, 1, 2]),
    tr(923, "RESOLVE_START", 3, [4]),
    tr(924, "RESOLVE_BEFORE_EFFECT", 4, [4]),
    tr(925, "RESOLVE_START", 3, [5]),
    tr(926, "RESOLVE_BEFORE_EFFECT", 5, [5]),
  ]);
  ui.stepPlayback(true);
  ui.stepPlayback(true);
  ui.stepPlayback(true);
  check("㉿F5 南蛮逐目标：轮到 4 号位就写 4 号位",
    $("t-context").textContent.includes("3号位 对 4号位 使用"),
    `描述="${$("t-context").textContent}"`);
  ui.stepPlayback(true);
  ui.stepPlayback(true);
  check("㉿F6 南蛮换到 5 号位时描述跟着换（不再看不见轮到谁）",
    $("t-context").textContent.includes("3号位 对 5号位 使用")
      && !$("t-context").textContent.includes("4号位"),
    `描述="${$("t-context").textContent}"`);
}

/* ============ 报障 2 / 4（真局）：从真实服务端扫出刘备 / 赵云的局面再验一次 ============ */
{
  const scanHeroCases = async () => {
    let longdan = null;
    let rende = null;
    for (const seed of [1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47]) {
      await newSession(seed);
      const hero = ui.state.view.heroes[0] || "";
      if (hero !== "zhaoyun" && hero !== "liubei") continue;
      for (let step = 0; step < 260 && ui.state.view.pending; step += 1) {
        const view = ui.state.view;
        const p = view.pending;
        if (p && p.kind === "PLAY_PHASE" && p.actor === 0 && hero === "zhaoyun" && !longdan) {
          const dodge = (view.snapshot.self_hand || []).find((card) => card.type === "dodge");
          // W4-fix-2：转化候选已从候选面收回，入口改由**清单**表达。
          const manifest = (p.preactivations || {})["hero.zhaoyun.longdan"];
          if (dodge && manifest) {
            longdan = { seed, steps: step, cardId: dodge.card_id };
          }
        }
        if (p && p.kind === "PLAY_PHASE" && p.actor === 0 && hero === "liubei" && !rende) {
          const skill = (view.skills || []).find((item) => item.skill_id === "hero.liubei.rende");
          if (skill && skill.available) rende = { seed, steps: step };
        }
        if (longdan && rende) return { longdan, rende };
        await advanceOnce();
      }
    }
    return { longdan, rende };
  };
  const replayScanTo = async (found) => {
    await newSession(found.seed);
    for (let step = 0; step < found.steps; step += 1) await advanceOnce();
    return found;
  };
  const found = await scanHeroCases();

  check("㉿L8 真局扫到「赵云出牌阶段手里有可转化的【闪】」", !!found.longdan,
    found.longdan ? `seed=${found.longdan.seed} steps=${found.longdan.steps}` : "16 个种子内没找到");
  if (found.longdan) {
    await replayScanTo(found.longdan);
    const p = ui.state.view.pending;
    const dodge = (ui.state.view.snapshot.self_hand || []).find((card) => card.card_id === found.longdan.cardId);
    check("㉿L9 真局里【闪】不再直接点成【杀】（候选面已收口）",
      !!dodge && !ui.cardSelectable(p, dodge.card_id),
      `card=${dodge && dodge.name} selectable=${dodge && ui.cardSelectable(p, dodge.card_id)}`);
    const longdanButton = $("t-skills").children.find(
      (b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
    check("㉿L9b 龙胆的唯一入口 = 清单（按钮可点、来源 → 目标两步）",
      !!longdanButton && longdanButton.disabled === false
        && longdanButton.getAttribute("data-preactivation") === "2",
      `disabled=${longdanButton && longdanButton.disabled} pre=${longdanButton && longdanButton.getAttribute("data-preactivation")}`);
  }

  check("㉿R8 真局扫到「刘备出牌阶段仁德可发动」", !!found.rende,
    found.rende ? `seed=${found.rende.seed} steps=${found.rende.steps}` : "16 个种子内没找到");
  if (found.rende) {
    await replayScanTo(found.rende);
    const button = $("t-skills").children.find((b) => b.getAttribute("data-skill-id") === "hero.liubei.rende");
    check("㉿R9 真局里仁德是**预发动**入口（按钮可点、清单两步、没有「发动技能」选项）",
      !!button && button.disabled === false
        && button.getAttribute("data-preactivated") === "true"
        && button.getAttribute("data-preactivation") === "2"
        && !$("t-options").children.some((b) => /发动技能/.test(b.textContent)),
      `disabled=${button && button.disabled} pre=${button && button.getAttribute("data-preactivation")}`);
    (button.listeners.click || []).forEach((fn) => fn({ target: button }));
    const pa = ui.state.preactivation;
    const options = ui.preactivationOptions(pa, 0) || [];
    const cardId = Number(options[0]);
    // 点清单里的第一张手牌（清单候选 = 实体牌 id）。
    const handCards = ui.state.view.snapshot.self_hand || [];
    const targetIndex = handCards.findIndex((card) => String(card.card_id) === String(cardId));
    if (targetIndex >= 0) {
      (($("t-hand").children[targetIndex].listeners.click) || []).forEach((fn) =>
        fn({ target: $("t-hand").children[targetIndex] }));
    }
    check("㉿R10 真局里点清单候选 = 收进本地答案（不提交）",
      !!pa && JSON.stringify(pa.answers[0].values) === JSON.stringify([cardId]),
      `answers=${pa && JSON.stringify(pa.answers[0])}`);
    const next = $("t-options").children.find((b) => b.textContent === "下一步");
    if (next) (next.listeners.click || []).forEach((fn) => fn({ target: next }));
    const seats = pa ? ui.preactivationOptions(pa, 1) : null;
    const firstListedSeat = $("t-seats").children.find(
      (c) => seats && seats.includes(`seat:${c.getAttribute("data-seat")}`));
    if (firstListedSeat) {
      (firstListedSeat.listeners.click || []).forEach((fn) => fn({ target: firstListedSeat }));
    }
    const composite = ui.buildAction();
    check("㉿R10b 真局里清单填完 = 复合动作（不要求是候选）",
      !!composite && composite.kind === "ActivateSkill"
        && composite.skill_id === "hero.liubei.rende"
        && composite.picks.length === 1 && composite.choices.length === 1,
      JSON.stringify(composite));
    if (composite) {
      await ui.submitAction();
      const p = ui.state.view.pending;
      check("㉿R11 真局里确认后服务端一次执行完（不再逐步问 SELECT_CARDS / SELECT_OPTION）",
        !!p && p.kind === "PLAY_PHASE", `kind=${p && p.kind}`);
    }
  }
}


/* ④ W4-1：预发动清单驱动（合成 view，验**读取方**真按服务端字段读）。
 *
 * 形状 = `pending_payload` 下发的 `preactivations[skill_id]`：两步（选牌 → 选目标）。
 * 这一块只验前端：清单驱动的进入 / 逐项选 / 重选 / 取消零提交 / 复合动作拼装。
 */
{
  const slash = synthCard(11, "杀");
  const dodge = { ...synthCard(12, "闪"), type: "dodge" };
  const prePending = {
    kind: "PLAY_PHASE", actor: 0, seq: 77,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 77 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {
      "demo.preactivate": [
        {
          name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["11", "12"] }],
        },
        {
          name: "seat", zones: [], min: 1, max: 1, order: 1,
          pool: [{ when: [], options: ["seat:1", "seat:2"] }],
        },
      ],
    },
    preactivation: [],
  };
  const preView = synthView(prePending);
  preView.snapshot.self_hand = [slash, dodge];
  preView.skills = [{
    skill_id: "demo.preactivate", name: "示例技", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "", convert_sources: [] },
  }];
  ui.state.view = preView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(preView);

  const skillButton = () => $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "demo.preactivate",
  );
  const handChips = () => $("t-hand").children;

  check("㉿PZ1 清单驱动：没有骨架候选也能从 preactivations 点亮技能按钮",
    !!skillButton() && skillButton().disabled === false
      && skillButton().getAttribute("data-preactivation") === "2",
    `disabled=${skillButton() && skillButton().disabled} pre=${skillButton() && skillButton().getAttribute("data-preactivation")}`);

  (skillButton().listeners.click || []).forEach((fn) => fn({ target: skillButton() }));
  check("㉿PZ2 点技能 = 进入预发动（不提交、询问原样还在）",
    !!ui.state.preactivation && ui.state.preactivation.skillId === "demo.preactivate"
      && ui.state.view.pending === prePending);

  check("㉿PZ3 第 1 步选牌：清单里的两张牌就地可点、其余牌灰",
    ui.cardFace(prePending, slash).selectable === true
      && ui.cardFace(prePending, dodge).selectable === true
      && !handChips()[0].classList.contains("dis"));

  (handChips()[0].listeners.click || []).forEach((fn) => fn({ target: handChips()[0] }));
  check("㉿PZ4 点牌 = 收进本地答案（不提交）",
    JSON.stringify(ui.state.preactivation.answers[0].values) === "[11]"
      && ui.state.view.pending === prePending,
    JSON.stringify(ui.state.preactivation.answers[0]));

  const next = $("t-options").children.find((c) => c.textContent === "下一步");
  check("㉿PZ5 选牌步的「下一步」在张数达标后可点", !!next && next.disabled === false);
  (next.listeners.click || []).forEach((fn) => fn({ target: next }));
  check("㉿PZ6 进第 2 步：座位 1 / 2 可点、座位 0 不可点",
    seatBox(1).classList.contains("targetable")
      && seatBox(2).classList.contains("targetable")
      && !seatBox(0).classList.contains("targetable"),
    `seat1=${seatBox(1).classList.contains("targetable")} seat0=${seatBox(0).classList.contains("targetable")}`);

  // 重选第 1 步：清空第 1..N 步（用户裁定的"重选第 K 步清空 K..N"）。
  const stepRow = $("t-options").children.find((c) => c.tagName === "DIV");
  const stepChip = stepRow && stepRow.children.find(
    (c) => c.getAttribute && c.getAttribute("data-step") === "0",
  );
  (stepChip.listeners.click || []).forEach((fn) => fn({ target: stepChip }));
  check("㉿PZ7 重选第 1 步清空第 1..N 步",
    ui.state.preactivation.cursor === 0
      && ui.state.preactivation.answers.every((answer) => answer.values.length === 0),
    JSON.stringify(ui.state.preactivation.answers));

  // 再走一遍，确认复合动作的拼装。
  (handChips()[0].listeners.click || []).forEach((fn) => fn({ target: handChips()[0] }));
  const next2 = $("t-options").children.find((c) => c.textContent === "下一步");
  (next2.listeners.click || []).forEach((fn) => fn({ target: next2 }));
  (seatBox(2).listeners.click || []).forEach((fn) => fn({ target: seatBox(2) }));
  const composite = ui.buildAction();
  check("㉿PZ8 清单填完 = 拼出复合 ActivateSkill（picks = 实体牌 id、choices = seat:N）",
    !!composite && composite.kind === "ActivateSkill"
      && JSON.stringify(composite.picks) === "[11]"
      && JSON.stringify(composite.choices) === '["seat:2"]'
      && composite.answers_seq === 77,
    JSON.stringify(composite));

  // 取消：本地态清空、**没有任何请求**发出（取消不提交任何 action）。
  (skillButton().listeners.click || []).forEach((fn) => fn({ target: skillButton() }));
  const before = calls.length;
  ui.cancelOrClear();
  check("㉿PZ9 取消预发动 = 回到同一层询问、零 action 提交",
    ui.state.preactivation === null
      && ui.state.view.pending === prePending
      && calls.length === before,
    `pre=${ui.state.preactivation} calls=${calls.length - before}`);
}



/* ④b W4-fix-3：清单步骤承载**文案**（服务端 `Step.wording`）。
 *
 * 写方 = `flow.manifest`（取自 `SkillDef.step_requirements`，`tests/test_hero_active.py` 钉），
 * 读方 = 这里的步骤条 / 提示行：有 `wording` 就画它，缺失时退回旧的通用文案（"选牌"/"选目标"）。
 */
{
  const card = synthCard(21, "杀");
  const makePending = (steps) => ({
    kind: "PLAY_PHASE", actor: 0, seq: 78,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 78 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: { "demo.wording": steps },
    preactivation: [],
  });
  const worded = makePending([
    { name: "card", zones: ["hand"], min: 1, max: 1, order: 0, wording: "弃掉一张牌",
      pool: [{ when: [], options: ["21"] }] },
    { name: "seat", zones: [], min: 1, max: 1, order: 1, wording: "选择决斗目标",
      pool: [{ when: [], options: ["seat:1"] }] },
  ]);
  const plain = makePending([
    { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
      pool: [{ when: [], options: ["21"] }] },
    { name: "seat", zones: [], min: 1, max: 1, order: 1,
      pool: [{ when: [], options: ["seat:1"] }] },
  ]);
  const renderFor = (pending) => {
    const view = synthView(pending);
    view.snapshot.self_hand = [card];
    view.skills = [{
      skill_id: "demo.wording", name: "示例文案", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: true, preactivated: true,
      ui: { requirement: "", convert_sources: [] },
    }];
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
    const button = $("t-skills").children.find(
      (b) => b.getAttribute("data-skill-id") === "demo.wording");
    (button.listeners.click || []).forEach((fn) => fn({ target: button }));
  };
  const stepRow = () => $("t-options").children.find((c) => c.tagName === "DIV");
  const stepChipText = (index) => {
    const chip = stepRow().children.find((c) => c.getAttribute("data-step") === String(index));
    return chip ? chip.textContent : "";
  };

  renderFor(worded);
  check("㉿WD1 有 wording：步骤条画的是这一问的人话（步 1）",
    stepChipText(0).includes("弃掉一张牌") && !stepChipText(0).includes("选牌"),
    stepChipText(0));
  check("㉿WD2 有 wording：提示行也用它（步 1）",
    $("t-target-hint").textContent.includes("弃掉一张牌"),
    $("t-target-hint").textContent);
  ($("t-hand").children[0].listeners.click || []).forEach((fn) => fn({ target: $("t-hand").children[0] }));
  ui.advancePreativation();
  check("㉿WD3 有 wording：目标步写明\"决斗目标\"（步 2）",
    stepChipText(1).includes("选择决斗目标") && $("t-target-hint").textContent.includes("选择决斗目标"),
    `${stepChipText(1)} / ${$("t-target-hint").textContent}`);

  renderFor(plain);
  check("㉿WD4 没有 wording 时退回旧通用文案（步骤条\"选牌\"、提示也一样）",
    stepChipText(0).includes("选牌") && $("t-target-hint").textContent.includes("选牌"),
    `${stepChipText(0)} / ${$("t-target-hint").textContent}`);
}

/* ⑤ W4-1：退化清单（服务端 `pool=()`）——不铺候选索引，座位步退化为"全都点得动"，
 *    仍能把复合动作拼出来（合法性由服务端校验器裁决，界面不裁决规则）。
 */
{
  const degradedPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 78,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 78 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {
      "demo.degraded": [
        { name: "seat", zones: [], min: 1, max: 1, order: 0, pool: [] },
      ],
    },
    preactivation: [],
  };
  const degradedView = synthView(degradedPending);
  degradedView.skills = [{
    skill_id: "demo.degraded", name: "退化技", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "", convert_sources: [] },
  }];
  ui.state.view = degradedView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(degradedView);
  const button = () => $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "demo.degraded",
  );
  (button().listeners.click || []).forEach((fn) => fn({ target: button() }));
  check("㉿PZ10 退化清单的座位步被识别为座位（不是选项步，不会死路）",
    ui.preactivationStepKind(degradedPending.preactivations["demo.degraded"][0]) === "seat"
      && ui.preactivationOptions(ui.state.preactivation, 0) === null);
  check("㉿PZ11 退化后座位仍可点（全都点得动，合法性交给校验器）",
    seatBox(1).classList.contains("targetable") && seatBox(2).classList.contains("targetable"));
  (seatBox(1).listeners.click || []).forEach((fn) => fn({ target: seatBox(1) }));
  const degradedAction = ui.buildAction();
  check("㉿PZ12 退化清单仍能拼出复合动作",
    !!degradedAction && degradedAction.kind === "ActivateSkill"
      && JSON.stringify(degradedAction.choices) === '["seat:1"]',
    JSON.stringify(degradedAction));
}

/* ⑥ W4-2：被动入口（时机询问随附清单）——不画"发动技能 XXX"，只给入口 + 放弃。
 */
{
  const passivePending = {
    kind: "SKILL_ACTIVATION", actor: 0, seq: 79,
    prompt: "是否发动该技能？", skill_id: "demo.passive",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "demo.passive", answers_seq: 79, picks: [], choices: [] },
      { kind: "Pass", answers_seq: 79 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {},
    preactivation: [
      { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
        pool: [{ when: [], options: ["11"] }] },
    ],
  };
  const passiveView = synthView(passivePending);
  passiveView.snapshot.self_hand = [synthCard(11, "杀")];
  passiveView.skills = [{
    skill_id: "demo.passive", name: "示例被动", skill_type: "NORMAL", locked: false,
    carrier: "EQUIPMENT", available: true, preactivated: true,
    ui: { requirement: "", convert_sources: [] },
  }];
  ui.state.view = passiveView;
  ui.state.sessionId = "smoke-missing-session";
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(passiveView);

  check("㉿PZ13 被动入口：浮动选项行只给「进入预发动」，没有「发动技能 XXX」选项，技能按钮标出清单",
    $("t-options").children.some((b) => /进入预发动/.test(b.textContent))
      && !$("t-options").children.some((b) => /发动技能/.test(b.textContent))
      && (() => {
        const b = $("t-skills").children.find((x) => x.getAttribute("data-skill-id") === "demo.passive");
        return !!b && b.disabled === false && b.getAttribute("data-preactivation") === "1";
      })(),
    `选项=${JSON.stringify($("t-options").children.map((b) => b.textContent))}`);
  const beforePass = calls.length;
  const cancel = $("btn-cancel");
  await clickAndWait(cancel);
  const passSent = calls.length > beforePass ? calls[calls.length - 1].body.action : null;
  check("㉿PZ14 被动入口点「放弃」= 送出 Pass（时机消费，不重问）",
    !!passSent && passSent.kind === "Pass", JSON.stringify(passSent));
}

/* ⑦ W4-2：转化模式切换的**宽松保留**（切换后已选源牌仍合法则保留、不合法才清空）。
 */
{
  const slash = synthCard(1, "杀");
  const dodge = { ...synthCard(2, "闪"), type: "dodge" };
  const fromDodge = { ...synthCard(-3, "杀"), type: "slash", virtual_sources: [2] };
  const fromHorse = { ...synthCard(-6, "杀"), type: "slash", virtual_sources: [5] };
  const horse = { ...synthCard(5, "赤兔"), type: "horse", color: "red" };
  const providePending = {
    kind: "SELECT_CARDS", actor: 0, seq: 80,
    prompt: "提供一张【杀】继续使用（目标不变；可发动转化技）",
    provide_type: "slash",
    legal_actions: [{ kind: "SelectCards", answers_seq: 80, picks: [] }],
    candidate_index: {}, option_seats: {}, context: null,
    slots: [
      { ref: { zone: "hand", seat: 0 }, card: slash },
      { ref: { zone: "hand", seat: 0 }, card: fromDodge },
      { ref: { zone: "equip", seat: 0 }, card: fromHorse },
    ],
    // 与生产同源（`GREEN_DRAGON_FLOW.PickProvide.zones`）：来源含装备区的【武圣】红马。
    spec: { zones: ["hand", "equip"], total: [1, 1] },
    virtual_index: {
      "2": [{ card_id: -3, skill_ids: ["hero.zhaoyun.longdan"] }],
      "5": [{ card_id: -6, skill_ids: ["hero.guanyu.wusheng"] }],
    },
    preactivations: {}, preactivation: [],
  };
  const provideView = synthView(providePending);
  provideView.snapshot.self_hand = [slash, dodge];
  provideView.snapshot.players[0].equip = [horse];
  provideView.skills = [
    {
      skill_id: "hero.zhaoyun.longdan", name: "龙胆", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: false, preactivated: false,
      ui: { requirement: "选择1张【闪】当【杀】使用或打出", convert_sources: [2] },
    },
    {
      skill_id: "hero.guanyu.wusheng", name: "武圣", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: false, preactivated: false,
      ui: { requirement: "一张红色牌当【杀】使用或打出", convert_sources: [5] },
    },
  ];
  ui.state.view = provideView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(provideView);
  const skillButton = (id) => $("t-skills").children.find((b) => b.getAttribute("data-skill-id") === id);
  const handChips = () => $("t-hand").children;

  // ① 先点【闪】（只在龙胆方向合法）→ 切到武圣 ⇒ 不合法、清空。
  (skillButton("hero.zhaoyun.longdan").listeners.click || []).forEach((fn) =>
    fn({ target: skillButton("hero.zhaoyun.longdan") }));
  (handChips()[1].listeners.click || []).forEach((fn) => fn({ target: handChips()[1] }));
  check("㉿PZ15 龙胆方向下点【闪】= 选中面内位置 1",
    JSON.stringify(ui.state.picks) === "[1]", `picks=${JSON.stringify(ui.state.picks)}`);
  (skillButton("hero.guanyu.wusheng").listeners.click || []).forEach((fn) =>
    fn({ target: skillButton("hero.guanyu.wusheng") }));
  check("㉿PZ16 切到武圣 ⇒ 已选的【闪】不再合法、清空",
    JSON.stringify(ui.state.picks) === "[]" && ui.state.convertSkill === "hero.guanyu.wusheng",
    `picks=${JSON.stringify(ui.state.picks)}`);

  // ② 先点实体【杀】（两个方向都合法）→ 切到龙胆 ⇒ 仍合法、保留。
  (handChips()[0].listeners.click || []).forEach((fn) => fn({ target: handChips()[0] }));
  (skillButton("hero.zhaoyun.longdan").listeners.click || []).forEach((fn) =>
    fn({ target: skillButton("hero.zhaoyun.longdan") }));
  check("㉿PZ17 实体【杀】在两个方向都合法 ⇒ 切换后保留",
    JSON.stringify(ui.state.picks) === "[0]" && ui.state.convertSkill === "hero.zhaoyun.longdan",
    `picks=${JSON.stringify(ui.state.picks)}`);

  // ③ 预发动中途切模式：不撤销清单，仍合法的已选答案保留。
  const prePending = {
    kind: "PLAY_PHASE", actor: 0, seq: 81, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 81 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {
      "demo.convert": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["1"] }] },
      ],
    },
    preactivation: [],
  };
  const preView2 = synthView(prePending);
  preView2.snapshot.self_hand = [slash, dodge];
  preView2.skills = [
    {
      skill_id: "demo.convert", name: "示例技", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: true, preactivated: true,
      ui: { requirement: "", convert_sources: [] },
    },
    {
      skill_id: "hero.zhaoyun.longdan", name: "龙胆", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: false, preactivated: false,
      ui: { requirement: "选择1张【闪】当【杀】使用或打出", convert_sources: [2] },
    },
  ];
  ui.state.view = preView2;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(preView2);
  (skillButton("demo.convert").listeners.click || []).forEach((fn) =>
    fn({ target: skillButton("demo.convert") }));
  (handChips()[0].listeners.click || []).forEach((fn) => fn({ target: handChips()[0] }));
  const beforeMode = ui.state.preactivation;
  (skillButton("hero.zhaoyun.longdan").listeners.click || []).forEach((fn) =>
    fn({ target: skillButton("hero.zhaoyun.longdan") }));
  check("㉿PZ18 预发动中途切转化模式：清单不撤销、仍合法的已选源牌保留",
    ui.state.preactivation === beforeMode
      && JSON.stringify(ui.state.preactivation.answers[0].values) === "[1]",
    `pre=${!!ui.state.preactivation} answers=${JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[0])}`);
}


/* ============ W4-3 / W4-fix-2：转化技的**预发动入口**（点技能 → 选源牌 → 选目标 → 确认）
 *
 * 清单由服务端从**这次询问的内部候选**派生（`tests/test_web.py` 钉写方），这里钉前端真的按
 * `pending.preactivations` 的清单进入本地态，且**确认 = `ActivateSkill(来源, 目标)`**——
 * 虚拟牌的 id 与那条普通 `UseCard(虚拟牌, 目标)` 都由服务端解析，候选里已经**不再有**它
 * （W4-fix-2 收口）。
 */
{
  const red = { ...synthCard(2, "桃"), type: "peach", color: "red" };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 910,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 910 }],
    candidate_index: {},
    virtual_index: { "2": [{ card_id: -3, skill_ids: ["hero.guanyu.wusheng"] }] },
    preactivations: {
      "hero.guanyu.wusheng": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["2"] }] },
        { name: "seat", min: 1, max: 1, order: 1,
          pool: [{ when: ["2"], options: ["seat:1"] }] },
      ],
    },
    preactivation: [], option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.heroes = { 0: "guanyu", 1: "zhaoyun", 2: "diaochan" };
  view.snapshot.self_hand = [red];
  view.skills = [{
    skill_id: "hero.guanyu.wusheng", name: "武圣", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "选择1张红色牌当【杀】使用或打出", convert_sources: [2] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const wusheng = $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.guanyu.wusheng");
  check("㉿T1 转化技有清单时按清单入口点亮（data-preactivation = 步骤数）",
    !!wusheng && wusheng.disabled === false && wusheng.getAttribute("data-preactivation") === "2",
    `disabled=${wusheng && wusheng.disabled} steps=${wusheng && wusheng.getAttribute("data-preactivation")}`);
  (wusheng.listeners.click || []).forEach((fn) => fn({ target: wusheng }));
  check("㉿T2 点武圣 = 进入**预发动**本地态（不是旧的转化模式、也不是复合动作）",
    !!ui.state.preactivation
      && ui.state.preactivation.skillId === "hero.guanyu.wusheng"
      && ui.state.preactivation.composite === false
      && ui.state.convertSkill === null,
    `pre=${!!ui.state.preactivation} composite=${ui.state.preactivation && ui.state.preactivation.composite}`);
  const redChip = $("t-hand").children[0];
  (redChip.listeners.click || []).forEach((fn) => fn({ target: redChip }));
  check("㉿T3 清单第 1 步：点源牌 = 选中这张红牌",
    JSON.stringify(ui.state.preactivation.answers[0].values) === "[2]",
    JSON.stringify(ui.state.preactivation.answers[0]));
  ui.advancePreativation();
  const listedSeat = seatBox(1);
  (listedSeat.listeners.click || []).forEach((fn) => fn({ target: listedSeat }));
  const action = ui.buildAction();
  check("㉿T4 清单填完 ⇒ 确认提交 **来源 + 目标**（ActivateSkill），不再去候选里查那条 UseCard",
    !!action && action.kind === "ActivateSkill"
      && action.skill_id === "hero.guanyu.wusheng"
      && JSON.stringify(action.picks) === "[2]"
      && JSON.stringify(action.choices) === "[\"seat:1\"]"
      && !(view.pending.legal_actions || []).some((c) => c.kind === "UseCard"),
    JSON.stringify(action));
  check("㉿T5 只填了源牌、没选目标时不能确认",
    ui.state.preactivation.cursor === 2 && ui.preactivationReady(ui.state.preactivation) === true,
    `cursor=${ui.state.preactivation.cursor}`);
}

/* 求桃窗口：急救的清单 = 一张红牌、**无目标步**（濒死者由窗口固定）。 */
{
  const red = { ...synthCard(4, "闪"), type: "dodge", color: "red" };
  const pending = {
    kind: "PEACH", actor: 0, seq: 920, dying: 2,
    prompt: "座位 2 处于濒死状态：是否对其使用【桃】？",
    legal_actions: [{ kind: "Pass", answers_seq: 920 }],
    candidate_index: {},
    virtual_index: { "4": [{ card_id: -5, skill_ids: ["hero.huatuo.jijiu"] }] },
    preactivations: {
      "hero.huatuo.jijiu": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["4"] }] },
      ],
    },
    preactivation: [], option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.heroes = { 0: "huatuo", 1: "guanyu", 2: "diaochan" };
  view.snapshot.self_hand = [red];
  view.skills = [{
    skill_id: "hero.huatuo.jijiu", name: "急救", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "选择1张红色牌当【桃】使用", convert_sources: [4] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  const jijiu = $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.huatuo.jijiu");
  (jijiu.listeners.click || []).forEach((fn) => fn({ target: jijiu }));
  const chip = $("t-hand").children[0];
  (chip.listeners.click || []).forEach((fn) => fn({ target: chip }));
  ui.advancePreativation();
  const action = ui.buildAction();
  check("㉿T6 急救：一张红牌填完即可确认（无目标步，目标是窗口固定的濒死者）",
    !!action && action.kind === "ActivateSkill"
      && action.skill_id === "hero.huatuo.jijiu"
      && JSON.stringify(action.picks) === "[4]"
      && JSON.stringify(action.choices) === "[]",
    JSON.stringify(action));
  check("㉿T7 急救清单只有一步（没有 seat 步）",
    ui.state.preactivation.steps.length === 1
      && ui.state.preactivation.steps[0].name === "card",
    JSON.stringify(ui.state.preactivation.steps.map((step) => step.name)));
}


/* 求桃（W4-fix-3）：**真【桃】可直接点**，急救只以【急救】入口出现。
 *
 * 载荷 = W4-fix-2 收口后的形状：`legal_actions` 里只有真【桃】的使用 + 放弃；转化【桃】
 * 不在候选面（`virtual_index` 因此是**空的**，不要读它）；急救的来源步只在
 * `preactivations["hero.huatuo.jijiu"]`。这里钉两件事：
 *   ① 点手牌【桃】= 选中那条 `UseCard(桃, 濒死者)` 并可以提交（`PEACH` 进手选询问类）；
 *   ② 选项**不再重复**（旧形状是"使用【桃】→ 座位 2"两条：真桃 + 急救转化桃）。
 */
{
  const redPeach = { ...synthCard(41, "桃"), type: "peach", color: "red" };
  const redDodge = { ...synthCard(42, "闪"), type: "dodge", color: "red" };
  const pending = {
    kind: "PEACH", actor: 0, seq: 930, dying: 2,
    prompt: "座位 2 处于濒死状态：是否对其使用【桃】？",
    legal_actions: [
      { kind: "UseCard", answers_seq: 930, card: redPeach, targets: [2] },
      { kind: "Pass", answers_seq: 930 },
    ],
    candidate_index: {
      "41": { card: redPeach, pool: [2], combos: [[2]], min_targets: 1, max_targets: 1 },
    },
    virtual_index: {},
    preactivations: {
      "hero.huatuo.jijiu": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["41", "42"] }] },
      ],
    },
    preactivation: [],
    option_seats: {},
    context: {
      actor: 0, name: null, targets: [2], label: null,
      requirement: "使用【桃】救回 2 号位",
      skill_hints: [{
        skill_id: "hero.huatuo.jijiu", name: "急救",
        requirement: "选择1张红色牌当【桃】使用",
      }],
    },
  };
  const view = synthView(pending);
  view.heroes = { 0: "huatuo", 1: "guanyu", 2: "diaochan" };
  view.snapshot.self_hand = [redPeach, redDodge];
  view.skills = [{
    skill_id: "hero.huatuo.jijiu", name: "急救", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "选择1张红色牌当【桃】使用", convert_sources: [] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  check("㉿PH1 求桃是**手选**询问（PEACH 进了可点手牌的询问类）",
    ui.isHandPick(pending) === true, `kinds=${JSON.stringify(ui.HAND_PICK_KINDS)}`);
  check("㉿PH2 手牌【桃】可点、红【闪】点不动（急救只经技能入口，`virtual_index` 为空）",
    ui.cardFace(pending, redPeach).selectable === true
      && ui.cardFace(pending, redDodge).selectable === false,
    `peach=${ui.cardFace(pending, redPeach).selectable} dodge=${ui.cardFace(pending, redDodge).selectable}`);

  const peachChip = $("t-hand").children.find((c) => c.text().includes("桃"));
  (peachChip.listeners.click || []).forEach((fn) => fn({ target: peachChip }));
  check("㉿PH3 点手牌【桃】= 选中它，目标自动选中濒死者（座位 2）",
    ui.state.focusCardId === 41
      && JSON.stringify(ui.state.selectedTargets) === "[2]"
      && ui.state.view.pending === pending,
    `focus=${ui.state.focusCardId} targets=${JSON.stringify(ui.state.selectedTargets)}`);
  const action = ui.buildAction();
  check("㉿PH4 点【桃】后拼出候选里的那条 `UseCard(桃, 2)`（不自己拼 Action），提交按钮可点",
    !!action && action.kind === "UseCard" && action.card.card_id === 41
      && JSON.stringify(action.targets) === "[2]" && $("btn-submit").disabled === false,
    JSON.stringify(action));

  const labels = (pending.legal_actions || []).map((item) => ui.actionLabel(item));
  check("㉿PH5 选项**不再重复**：每张真牌 + 目标只有一条标签（真桃 / 急救转化桃不再并列）",
    new Set(labels).size === labels.length
      && labels.filter((label) => label === "使用【桃】 → 座位 2").length === 1,
    JSON.stringify(labels));
  check("㉿PH6 【急救】入口带自己的技能名与\"当【桃】\"文案（与手牌真【桃】分得开）",
    (() => {
      const button = $("t-skills").children.find(
        (b) => b.getAttribute("data-skill-id") === "hero.huatuo.jijiu");
      const hint = $("t-skill-hints").textContent;
      return !!button && button.text().includes("急救")
        && hint.includes("急救") && hint.includes("当【桃】")
        && !labels.some((label) => label.includes("急救"));
    })(),
    `skills=${$("t-skills").children.map((b) => b.text()).join(" / ")} hints=${$("t-skill-hints").textContent}`);

  const jijiu = $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.huatuo.jijiu");
  (jijiu.listeners.click || []).forEach((fn) => fn({ target: jijiu }));
  check("㉿PH7 急救清单只有一张红色来源（无 seat 步）；确认 = ActivateSkill(来源)",
    (() => {
      const built = ui.buildAction();
      return ui.state.preactivation.steps.length === 1
        && built === null;  // 还没选来源 ⇒ 不能确认
    })(),
    JSON.stringify(ui.state.preactivation.steps.map((step) => step.name)));
  const sourceChip = $("t-hand").children.find((c) => c.text().includes("闪"));
  (sourceChip.listeners.click || []).forEach((fn) => fn({ target: sourceChip }));
  ui.advancePreativation();
  const rescue = ui.buildAction();
  check("㉿PH8 急救确认 = ActivateSkill(来源 = 红【闪】)，目标由窗口固定（choices 为空）",
    !!rescue && rescue.kind === "ActivateSkill"
      && rescue.skill_id === "hero.huatuo.jijiu"
      && JSON.stringify(rescue.picks) === "[42]"
      && JSON.stringify(rescue.choices) === "[]",
    JSON.stringify(rescue));
  ui.cancelOrClear();
}


/* 技能标签（W4-fix-3）：**触发技（被动）**不得再标"主动技"。
 *
 * 判据在服务端投影（`build_skills` 的 `ui.passive`）：有钩子 / 接缝、且**没有主动入口**
 * （候选修饰器）⇒ 被动。这里用**真会话**同时钉"写方"（响应体的 `ui.passive`）与"读方"
 * （技能栏徽标）：流离（钩子：成为目标时）与反馈（钩子：受到伤害后）都是被动；
 * 国色（转化技，有候选入口）仍是主动技——别的触发技 / 主动技不回归。
 */
{
  const skillOf = (view, skillId) => (view.skills || []).find((s) => s.skill_id === skillId);
  const buttonOf = (skillId) => $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === skillId);

  await openSession(await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null,
    seed: 11, my_hero: "daqiao",
  }));
  const liuli = skillOf(ui.state.view, "hero.daqiao.liuli");
  check("㉿PS1 流离在投影里是**触发技**（hooks / 接缝 + 无主动入口 ⇒ ui.passive）",
    !!liuli && liuli.ui && liuli.ui.passive === true && liuli.locked === false,
    JSON.stringify(liuli && { passive: liuli.ui.passive, locked: liuli.locked, type: liuli.skill_type }));
  check("㉿PS2 流离的技能栏徽标写「被动」，不再写「主动技」",
    !!buttonOf("hero.daqiao.liuli")
      && buttonOf("hero.daqiao.liuli").text().includes("被动")
      && !buttonOf("hero.daqiao.liuli").text().includes("主动技"),
    buttonOf("hero.daqiao.liuli") && buttonOf("hero.daqiao.liuli").text());
  const guose = skillOf(ui.state.view, "hero.daqiao.guose");
  check("㉿PS3 国色（转化技，有候选入口）仍是主动技——主动侧不回归",
    !!guose && guose.ui.passive === false
      && buttonOf("hero.daqiao.guose").text().includes("主动技"),
    JSON.stringify(guose && { passive: guose.ui.passive }));

  await openSession(await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null,
    seed: 11, my_hero: "simayi",
  }));
  const fankui = skillOf(ui.state.view, "hero.simayi.fankui");
  check("㉿PS4 反馈（别的触发技）同样是被动——触发技一族不回归",
    !!fankui && fankui.ui.passive === true
      && buttonOf("hero.simayi.fankui").text().includes("被动"),
    JSON.stringify(fankui && { passive: fankui.ui.passive }));
}

/* W5-02b：借刀杀人的两个入口（合成载荷，验**读取方**与服务端下发的键逐字一致）。
 *
 * ① 手牌入口：`pending.card_preactivations[card.type]`（键 = `borrowed_sword`）两步清单；
 *    点手牌 ⇒ 本地态两步 ⇒ 确认拼 `UseCard(card, targets=(A,), designations=(Bn,))`，
 *    **不拼虚拟牌 id**、也不要求在 `legal_actions` 里（服务端池成员校验）。
 * ② 持刀者窗口：目标固定 Bn；转化清单由 `preactivations[技能]` 下发（来源步）；
 *    `Pass` = b，按钮文案「交出武器」。
 */
{
  const borrowed = { ...synthCard(31, "借刀杀人"), type: "borrowed_sword" };
  const cardPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 91,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 91 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
    card_preactivations: {
      borrowed_sword: [
        {
          name: "seat", zones: [], min: 1, max: 1, order: 0,
          wording: "选择持刀者（装备区有武器、且有合法【杀】目标）",
          pool: [{ when: [], options: ["seat:1"] }],
        },
        {
          name: "seat", zones: [], min: 1, max: 1, order: 1,
          wording: "选择【杀】的目标（由你指定）",
          pool: [{ when: ["seat:1"], options: ["seat:0", "seat:2"] }],
        },
      ],
    },
  };
  const cardView = synthView(cardPending);
  cardView.snapshot.self_hand = [borrowed];
  ui.state.view = cardView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(cardView);

  const cardChipOf = () => $("t-hand").children[0];
  check("㉿BS1 借刀手牌可点（可用性来自 card_preactivations，不在 candidate_index）",
    ui.cardFace(cardPending, borrowed).selectable === true
      && !cardChipOf().classList.contains("dis"),
    `selectable=${ui.cardFace(cardPending, borrowed).selectable}`);

  const beforeCard = calls.length;
  (cardChipOf().listeners.click || []).forEach((fn) => fn({ target: cardChipOf() }));
  check("㉿BS2 点借刀 = 进入两步清单（本地态、零提交、询问原样还在）",
    !!ui.state.preactivation && ui.state.preactivation.cardEntry === true
      && ui.state.preactivation.cardId === 31
      && ui.state.preactivation.steps.length === 2
      && ui.state.view.pending === cardPending
      && calls.length === beforeCard,
    `pre=${JSON.stringify(ui.state.preactivation && {
      cardEntry: ui.state.preactivation.cardEntry,
      cardId: ui.state.preactivation.cardId,
      steps: ui.state.preactivation.steps.length,
    })} calls=${calls.length - beforeCard}`);

  check("㉿BS3 第 1 步只亮持刀者座位 1（Bn 子池要等 A 选完）",
    seatBox(1).classList.contains("targetable")
      && !seatBox(2).classList.contains("targetable"),
    `seat1=${seatBox(1).classList.contains("targetable")} seat2=${seatBox(2).classList.contains("targetable")}`);
  (seatBox(1).listeners.click || []).forEach((fn) => fn({ target: seatBox(1) }));
  check("㉿BS4 选持刀者后自动进第 2 步：Bn 子池 = seat:0 / seat:2",
    ui.state.preactivation.cursor === 1
      && JSON.stringify(ui.preactivationOptions(ui.state.preactivation, 1)) === '["seat:0","seat:2"]',
    `cursor=${ui.state.preactivation.cursor} options=${JSON.stringify(ui.preactivationOptions(ui.state.preactivation, 1))}`);
  (seatBox(2).listeners.click || []).forEach((fn) => fn({ target: seatBox(2) }));
  const cardAction = ui.buildAction();
  check("㉿BS5 确认 = UseCard(实体借刀, targets=(A,), designations=(Bn,))，不拼虚拟 id",
    !!cardAction && cardAction.kind === "UseCard"
      && cardAction.card.card_id === 31
      && JSON.stringify(cardAction.targets) === "[1]"
      && JSON.stringify(cardAction.designations) === "[2]"
      && cardAction.answers_seq === 91,
    JSON.stringify(cardAction));
  check("㉿BS6 提交按钮在清单填完 + 组合已拼出时可点",
    $("btn-submit").disabled === false && $("btn-submit").textContent === "确认使用",
    `disabled=${$("btn-submit").disabled} label="${$("btn-submit").textContent}"`);
  const beforeCancel = calls.length;
  ui.cancelOrClear();
  check("㉿BS7 取消手牌清单 = 回到同一层询问、零 action 提交",
    ui.state.preactivation === null && calls.length === beforeCancel,
    `pre=${ui.state.preactivation} calls=${calls.length - beforeCancel}`);
}

/* ---- M5-bugfix（D）：借刀预发动提交的 `card` 必须是**纯 CardRef** ----
 *
 * 报障（用户实机）：预发动确认时报
 * `UseCard.card.attribute Extra inputs are not permitted`。根因是界面把**展示副本**
 * （`display_name` / `attribute` / `description` / `kind` / `source_cards` …）原样塞进
 * `UseCard.card`，而服务端的 `CardRef` 是 `extra="forbid"` 的严格模型。
 *
 * 判据：任何从**展示卡**拼出来的 `UseCard` / `PlayCard` 都先过 `bareCardRef`
 * （`core.js`，字段闭集 = 服务端 `CardRef` 的九个键）；其余字段（来源牌 / 展示文案）
 * 一个都不许随提交上行。**不放宽服务端模型**。
 */
{
  const borrowedDisplay = {
    ...synthCard(31, "借刀杀人"),
    type: "borrowed_sword",
    display_name: "借刀杀人",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "借刀杀人的描述：令持刀者对其攻击范围内的一名角色使用【杀】。",
  };
  const borrowedRaw = {
    card_id: 31, type: "borrowed_sword", name: "借刀杀人", suit: "spade",
    rank: 7, slot: null, attack_range: null, color: "black", virtual_sources: [],
  };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 93,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 93 }],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [], card_preactivations: {},
  };
  const pa = {
    skillId: null, cardId: 31, card: borrowedDisplay, name: "借刀杀人",
    composite: false, cardEntry: true,
    steps: [
      {
        name: "seat", zones: [], min: 1, max: 1, order: 0,
        pool: [{ when: [], options: ["seat:1"] }],
      },
      {
        name: "seat", zones: [], min: 1, max: 1, order: 1,
        pool: [{ when: [], options: ["seat:2"] }],
      },
    ],
    answers: [
      { values: [1], texts: ["seat:1"] },
      { values: [2], texts: ["seat:2"] },
    ],
    cursor: 2,
  };

  const action = ui.buildPreativationAction(pending, pa);
  const cardKeys = action && action.card ? Object.keys(action.card).sort() : [];
  const forbidden = ["display_name", "attribute", "description", "kind", "source_cards"];
  check("M5-bugfix-d1 借刀预发动提交的 card 只剩 CardRef 字段（展示副本字段全部剔除）",
    !!action && action.kind === "UseCard"
      && JSON.stringify(action.card) === JSON.stringify(borrowedRaw)
      && JSON.stringify(cardKeys) === JSON.stringify(Object.keys(borrowedRaw).sort())
      && forbidden.every((key) => !(key in action.card))
      && JSON.stringify(action.targets) === "[1]"
      && JSON.stringify(action.designations) === "[2]"
      && action.answers_seq === 93,
    `card=${JSON.stringify(action && action.card)} keys=${JSON.stringify(cardKeys)}`);

  const projected = ui.bareCardRef ? ui.bareCardRef(borrowedDisplay) : undefined;
  check("M5-bugfix-d2 bareCardRef = CardRef 字段闭集的唯一投影口（九键，缺省不补）",
    !!projected
      && JSON.stringify(projected) === JSON.stringify(borrowedRaw)
      && !("description" in projected)
      && !("source_cards" in projected),
    `projected=${JSON.stringify(projected)}`);
}

{
  const dodge = { ...synthCard(51, "闪"), type: "dodge" };
  const realSlash = { ...synthCard(52, "杀"), type: "slash" };
  const sourceStep = {
    name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
    pool: [{ when: [], options: ["51"] }],
  };
  const holderPending = {
    kind: "RESPOND", actor: 0, seq: 92, cause: "BORROWED_SWORD", required: "slash",
    prompt: "【借刀杀人】：对 2 号位使用【杀】，或交出武器。",
    legal_actions: [
      { kind: "UseCard", card: realSlash, targets: [2], answers_seq: 92 },
      { kind: "Pass", answers_seq: 92 },
    ],
    candidate_index: {
      52: { card: realSlash, pool: [2], min_targets: 1, max_targets: 1, combos: [[2]] },
    },
    option_seats: {},
    context: { requirement: "对 2 号位使用【杀】，或交出武器" },
    preactivations: { "hero.zhaoyun.longdan": [sourceStep] },
    preactivation: [],
  };
  const holderView = synthView(holderPending);
  holderView.snapshot.self_hand = [dodge];
  holderView.heroes = { 0: "guanyu", 1: "zhaoyun", 2: "diaochan" };
  holderView.skills = [{
    skill_id: "hero.zhaoyun.longdan", name: "龙胆", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "你可以把【杀】当【闪】、把【闪】当【杀】使用或打出。", convert_sources: [51] },
  }];
  ui.state.view = holderView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(holderView);

  const passButton = $("btn-pass");
  check("㉿BS8 持刀者窗口：`Pass` 按钮在右端、文案 =「交出武器」（不是「放弃」）",
    !!passButton && passButton.hidden === false && passButton.textContent === "交出武器",
    `hidden=${passButton && passButton.hidden} text="${passButton && passButton.textContent}" options=${$("t-options").children.map((c) => c.textContent).join(" / ")}`);
  check("㉿BS9 持刀者窗口：提示行写出「对 Bn 使用【杀】，或交出武器」",
    $("t-prompt").textContent.includes("交出武器")
      && $("t-prompt").textContent.includes("2 号位"),
    `prompt="${$("t-prompt").textContent}"`);

  const longdan = $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
  check("㉿BS10 持刀者窗口下发转化清单：龙胆按钮可点、步骤数 1（只有来源步）",
    !!longdan && longdan.disabled === false
      && longdan.getAttribute("data-preactivation") === "1",
    `disabled=${longdan && longdan.disabled} pre=${longdan && longdan.getAttribute("data-preactivation")}`);
  (longdan.listeners.click || []).forEach((fn) => fn({ target: longdan }));
  const symbol = $("t-hand").children[0];
  (symbol.listeners.click || []).forEach((fn) => fn({ target: symbol }));
  ui.advancePreativation();
  const transformed = ui.buildAction();
  check("㉿BS11 清单填完 = ActivateSkill(龙胆, 来源=实体【闪】)，目标由窗口固定（choices 为空）",
    !!transformed && transformed.kind === "ActivateSkill"
      && transformed.skill_id === "hero.zhaoyun.longdan"
      && JSON.stringify(transformed.picks) === "[51]"
      && JSON.stringify(transformed.choices) === "[]",
    JSON.stringify(transformed));
  ui.cancelOrClear();

  // 只有 `Pass` 的窗口（持刀者没有 a）走 direct 分支：文案同样写「交出武器」。
  const directPending = {
    ...holderPending,
    legal_actions: [{ kind: "Pass", answers_seq: 92 }],
    candidate_index: {},
    preactivations: {},
  };
  const directView = synthView(directPending);
  directView.snapshot.self_hand = [];
  ui.state.view = directView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(directView);
  check("㉿BS12 只有一个 `Pass` 时，direct 文案也写「交出武器」",
    $("t-choice").textContent.includes("交出武器"),
    `choice="${$("t-choice").textContent}"`);
  ui.resetSelection();
}

/* ============ W7-01：借刀窗口的多目标（方天 + 最后一张手牌【杀】） ============
 *
 * 判据（自动化的那一半）：`candidate_index` 的 combos 恒含 Bn、`min/max_targets` = 1 / 3；
 * 既有 picker 直接能点 Bn + 额外（组合恰在候选里才可提交）、**不**自动选中；
 * **只允许含 Bn 的组合**（不选 Bn 的组合不可提交）。真实拖拽 / 实机验收不在这里断言。
 */
{
  const slash = { ...synthCard(61, "杀"), type: "slash" };
  const bn = 2;
  const borrowPending = {
    kind: "RESPOND", actor: 1, seq: 93, cause: "BORROWED_SWORD", required: "slash",
    prompt: "【借刀杀人】：对 3 号位使用【杀】，或交出武器。",
    legal_actions: [
      { kind: "UseCard", card: slash, targets: [bn], answers_seq: 93 },
      { kind: "UseCard", card: slash, targets: [bn, 0], answers_seq: 93 },
      { kind: "UseCard", card: slash, targets: [bn, 0, 3], answers_seq: 93 },
      { kind: "UseCard", card: slash, targets: [bn, 3], answers_seq: 93 },
      { kind: "Pass", answers_seq: 93 },
    ],
    candidate_index: {
      61: {
        card: slash, pool: [bn, 0, 3], min_targets: 1, max_targets: 3,
        combos: [[bn], [bn, 0], [bn, 0, 3], [bn, 3]],
      },
    },
    option_seats: {},
    context: { requirement: "对 3 号位使用【杀】，或交出武器" },
    preactivations: {}, preactivation: [],
  };
  const borrowView = synthView(borrowPending);
  borrowView.human_seat = 1;
  borrowView.snapshot.seat = 1;
  borrowView.snapshot.self_hand = [slash];
  borrowView.snapshot.players = [0, 1, 2, 3].map((seat) => ({
    seat, hp: 4, hp_max: 4, hand_count: 0, alive: true, equip: [], judge: [], identity: null,
  }));
  ui.state.view = borrowView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(borrowView);

  const entry = ui.cardEntry(borrowPending, 61);
  check("㉿FT1 借刀多目标：索引读的是 min=1 / max=3，且每组 combo 恒含 Bn",
    !!entry && entry.min_targets === 1 && entry.max_targets === 3
      && entry.combos.every((combo) => combo.includes(bn)),
    entry ? `min=${entry.min_targets} max=${entry.max_targets} combos=${JSON.stringify(entry.combos)}` : "没有候选条目");

  const slashChip = $("t-hand").children[0];
  (slashChip.listeners.click || []).forEach((fn) => fn({ target: slashChip }));
  check("㉿FT2 多组合时**不**自动选中（方天的 1..3 要玩家自己选）",
    String(ui.state.focusCardId) === "61" && ui.state.selectedTargets.length === 0
      && $("btn-submit").disabled === true,
    `focus=${ui.state.focusCardId} selected=${JSON.stringify(ui.state.selectedTargets)}`);

  check("㉿FT3 池里的座位（Bn 与两个额外目标）都标成可点",
    [bn, 0, 3].every((seat) => seatBox(seat).classList.contains("targetable")),
    [bn, 0, 3].map((seat) => `${seat}:${seatBox(seat).classList.contains("targetable")}`).join(" "));

  // 只点额外目标、不点 Bn ⇒ 不是合法组合（**只有含 Bn 的组合被允许**）。
  for (const seat of [0, 3]) {
    (seatBox(seat).listeners.click || []).forEach((fn) => fn({ target: seatBox(seat) }));
  }
  check("㉿FT4 不含 Bn 的组合**不可提交**（picker 只放行含 Bn 的组合）",
    JSON.stringify(ui.state.selectedTargets) === "[0,3]" && $("btn-submit").disabled === true
      && ui.buildAction() === null,
    `selected=${JSON.stringify(ui.state.selectedTargets)} disabled=${$("btn-submit").disabled}`);

  // 点 Bn 补上 ⇒ 恰好是候选里的 3 目标组合。
  (seatBox(bn).listeners.click || []).forEach((fn) => fn({ target: seatBox(bn) }));
  const chosen = ui.buildAction();
  check("㉿FT5 Bn + 两个额外目标 = 候选里的 3 目标组合（提交按钮可点）",
    !!chosen && JSON.stringify(chosen.targets.slice().sort((a, b) => a - b)) === "[0,2,3]"
      && chosen.card.card_id === 61 && $("btn-submit").disabled === false,
    JSON.stringify(chosen && chosen.targets));

  // 取消 Bn ⇒ 又回到"不含 Bn"的非法组合。
  (seatBox(bn).listeners.click || []).forEach((fn) => fn({ target: seatBox(bn) }));
  check("㉿FT6 取消 Bn 后提交按钮重新不可点",
    $("btn-submit").disabled === true && ui.buildAction() === null,
    `selected=${JSON.stringify(ui.state.selectedTargets)}`);

  check("㉿FT7 借刀窗口的 `Pass` 仍写作「交出武器」（b 支不变）",
    $("btn-pass").hidden === false && $("btn-pass").textContent === "交出武器",
    `btn-pass="${$("btn-pass").textContent}" hidden=${$("btn-pass").hidden} options=${$("t-options").children.map((c) => c.textContent).join(" / ")}`);
  ui.resetSelection();
}

/* ============ W7-01b：清单座位步多选（1..max；合成 pending，不依赖方天候选） ============
 *
 * 判据（自动化的那一半）：`preactivationSeatMulti(step)` = `max > min` 时座位步改成多选——
 * 点座位 = 选 / 取消、超过 `max` 不再收、"下一步"按 `min..max` 放行、提示行写出
 * 「已选 k / 1–max 个」；确认拼出的 `choices` 是**完整目标集**（顺序由点击决定，服务端
 * 按集合比较）。单选座位步（`min == max`）的既有交互由 ㉿T4 / ㉿BS4 继续钉住。
 */
{
  const red = { ...synthCard(2, "桃"), type: "peach", color: "red" };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 930,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 930 }],
    candidate_index: {},
    virtual_index: { "2": [{ card_id: -3, skill_ids: ["hero.guanyu.wusheng"] }] },
    preactivations: {
      "hero.guanyu.wusheng": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["2"] }] },
        { name: "seat", min: 1, max: 3, order: 1,
          pool: [{ when: ["2"], options: ["seat:0", "seat:1", "seat:2", "seat:3"] }] },
      ],
    },
    preactivation: [], option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.heroes = { 0: "guanyu", 1: "zhaoyun", 2: "diaochan", 3: "zhangfei" };
  view.snapshot.players = [0, 1, 2, 3].map((seat) => ({
    seat, hp: 4, hp_max: 4, hand_count: 0, alive: true, equip: [], judge: [], identity: null,
  }));
  view.snapshot.self_hand = [red];
  view.skills = [{
    skill_id: "hero.guanyu.wusheng", name: "武圣", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "选择1张红色牌当【杀】使用或打出", convert_sources: [2] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButton = () => $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.guanyu.wusheng");
  const nextButton = () => $("t-options").children.find((c) => c.textContent === "下一步");
  const clickSeat = (seat) => (seatBox(seat).listeners.click || []).forEach(
    (fn) => fn({ target: seatBox(seat) }));

  (skillButton().listeners.click || []).forEach((fn) => fn({ target: skillButton() }));
  const redChip = $("t-hand").children[0];
  (redChip.listeners.click || []).forEach((fn) => fn({ target: redChip }));
  (nextButton().listeners.click || []).forEach((fn) => fn({ target: nextButton() }));

  const pa = ui.state.preactivation;
  const seatStep = pa && pa.steps[1];
  check("㉿MT1 座位步 max > min ⇒ 多选（同一个渲染器，不再点一下自动进下一步）",
    !!seatStep && ui.preactivationSeatMulti(seatStep) === true
      && pa.cursor === 1 && JSON.stringify(pa.answers[1].values) === "[]",
    `cursor=${pa && pa.cursor} multi=${seatStep && ui.preactivationSeatMulti(seatStep)}`);
  check("㉿MT2 提示行写「已选 k / 1–3 个」",
    /已选 0 \/ 1–3 个/.test($("t-target-hint").textContent),
    `hint="${$("t-target-hint").textContent}"`);
  check("㉿MT3 min 未满足时「下一步」不可点", !!nextButton() && nextButton().disabled === true,
    `next=${!!nextButton()} disabled=${nextButton() && nextButton().disabled}`);

  clickSeat(2);
  clickSeat(0);
  clickSeat(1);
  check("㉿MT4 点座位 = 选（可多选），提示行计数跟着走",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[2,0,1]"
      && /已选 3 \/ 1–3 个/.test($("t-target-hint").textContent),
    `values=${JSON.stringify(ui.state.preactivation.answers[1].values)} hint="${$("t-target-hint").textContent}"`);

  clickSeat(3);
  check("㉿MT5 超过 max 的座位不再收（仍 3 个）",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[2,0,1]",
    JSON.stringify(ui.state.preactivation.answers[1].values));

  clickSeat(0);
  check("㉿MT6 再点已选座位 = 取消它",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[2,1]",
    JSON.stringify(ui.state.preactivation.answers[1].values));
  clickSeat(0);

  check("㉿MT7 未点「下一步」时清单未完成、不能确认",
    ui.preactivationReady(ui.state.preactivation) === false && ui.buildAction() === null,
    `cursor=${ui.state.preactivation.cursor}`);
  (nextButton().listeners.click || []).forEach((fn) => fn({ target: nextButton() }));
  const action = ui.buildAction();
  check("㉿MT8 确认 = ActivateSkill(来源, **完整目标集**)，顺序 = 点击顺序",
    !!action && action.kind === "ActivateSkill"
      && action.skill_id === "hero.guanyu.wusheng"
      && JSON.stringify(action.picks) === "[2]"
      && JSON.stringify(action.choices) === '["seat:2","seat:1","seat:0"]'
      && $("btn-submit").disabled === false,
    JSON.stringify(action));
  ui.resetSelection();
}

/* ============ W7-01b：借刀窗口的多目标步**预选 / 标出 Bn**（必选首项） ================
 *
 * 判据：`cause === "BORROWED_SWORD"` 的座位步首项 = Bn（候选排序的自然结果）⇒ 进入该步时
 * **预选**、标 `req` 类、点它不取消；提示行写「借刀指定的目标（必选）」。引擎的成员判定
 * 仍是最终裁判，界面只做引导（不新增询问 / 协议字段）。
 */
{
  const dodge = { ...synthCard(52, "闪"), type: "dodge" };
  const pending = {
    kind: "RESPOND", actor: 0, seq: 931, cause: "BORROWED_SWORD", required: "slash",
    prompt: "【借刀杀人】：对 3 号位使用【杀】，或交出武器。",
    legal_actions: [{ kind: "Pass", answers_seq: 931 }],
    candidate_index: {},
    preactivations: {
      "hero.zhaoyun.longdan": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["52"] }] },
        { name: "seat", min: 1, max: 3, order: 1,
          pool: [{ when: ["52"], options: ["seat:2", "seat:0", "seat:3"] }] },
      ],
    },
    preactivation: [], option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.heroes = { 0: "zhaoyun", 1: "guanyu", 2: "diaochan", 3: "zhangfei" };
  view.snapshot.players = [0, 1, 2, 3].map((seat) => ({
    seat, hp: 4, hp_max: 4, hand_count: 0, alive: true, equip: [], judge: [], identity: null,
  }));
  view.snapshot.self_hand = [dodge];
  view.skills = [{
    skill_id: "hero.zhaoyun.longdan", name: "龙胆", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "你可以把【杀】当【闪】、把【闪】当【杀】使用或打出。", convert_sources: [52] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButton = () => $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
  const nextButton = () => $("t-options").children.find((c) => c.textContent === "下一步");
  const clickSeat = (seat) => (seatBox(seat).listeners.click || []).forEach(
    (fn) => fn({ target: seatBox(seat) }));

  (skillButton().listeners.click || []).forEach((fn) => fn({ target: skillButton() }));
  const dodgeChip = $("t-hand").children[0];
  (dodgeChip.listeners.click || []).forEach((fn) => fn({ target: dodgeChip }));
  (nextButton().listeners.click || []).forEach((fn) => fn({ target: nextButton() }));

  check("㉿BN1 借刀窗口目标步：首项 = Bn 且**预选**（进入该步即有 1 个答案）",
    ui.preactivationRequiredSeat(ui.state.preactivation, pending, 1) === "seat:2"
      && JSON.stringify(ui.state.preactivation.answers[1].values) === "[2]",
    `required=${ui.preactivationRequiredSeat(ui.state.preactivation, pending, 1)} values=${JSON.stringify(ui.state.preactivation.answers[1].values)}`);
  check("㉿BN2 Bn 座位标成必选（`req` 类）且是已选态（`tsel`）",
    seatBox(2).classList.contains("req") && seatBox(2).classList.contains("tsel"),
    `req=${seatBox(2).classList.contains("req")} tsel=${seatBox(2).classList.contains("tsel")}`);
  check("㉿BN3 提示行写出「借刀指定的目标（必选）」与「已选 1 / 1–3 个」",
    $("t-target-hint").textContent.includes("借刀指定的目标（必选）")
      && /已选 1 \/ 1–3 个/.test($("t-target-hint").textContent),
    `hint="${$("t-target-hint").textContent}"`);

  clickSeat(2);
  check("㉿BN4 点必选首项（Bn）不会取消它",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[2]",
    JSON.stringify(ui.state.preactivation.answers[1].values));

  clickSeat(0);
  clickSeat(3);
  check("㉿BN5 额外目标追加在后面，Bn 仍在首位",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[2,0,3]",
    JSON.stringify(ui.state.preactivation.answers[1].values));

  (nextButton().listeners.click || []).forEach((fn) => fn({ target: nextButton() }));
  const action = ui.buildAction();
  check("㉿BN6 确认拼出完整目标集（Bn 第一、额外目标在后）",
    !!action && action.kind === "ActivateSkill"
      && action.skill_id === "hero.zhaoyun.longdan"
      && JSON.stringify(action.picks) === "[52]"
      && JSON.stringify(action.choices) === '["seat:2","seat:0","seat:3"]',
    JSON.stringify(action));

  // 重选第 1 步会清空来源与目标；重新选源、再进目标步时**预选**重新生效。
  ui.jumpPreativationStep(0);
  (dodgeChip.listeners.click || []).forEach((fn) => fn({ target: dodgeChip }));
  (nextButton().listeners.click || []).forEach((fn) => fn({ target: nextButton() }));
  check("㉿BN7 重选来源步后再进目标步，Bn 的预选重新生效",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[2]",
    JSON.stringify(ui.state.preactivation.answers[1].values));
  ui.resetSelection();
}

/* ============ W7-02：方天 × 转化【杀】的清单面（01b 的多选 / Bn 预选在转化来源上） ========
 *
 * 写方 = `virtual_card_use` 的 1..3 候选派生出清单（Python 侧测试钉住），读方 = 本界面。
 * 判据只有两条：出牌阶段转化【杀】的目标步 `1..3` 画成多选、确认拼**完整目标集**；
 * 借刀窗口的转化【杀】目标步首项 = Bn（预选 / 必选），追加两个额外目标后确认含 Bn。
 */
{
  const red = { ...synthCard(2, "桃"), type: "peach", color: "red" };
  const halberd = { ...synthCard(77, "方天画戟"), type: "halberd", slot: "weapon", attack_range: 4 };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 940,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 940 }],
    candidate_index: {},
    virtual_index: { "2": [{ card_id: -5, skill_ids: ["hero.guanyu.wusheng"] }] },
    preactivations: {
      "hero.guanyu.wusheng": [
        { name: "card", zones: ["hand", "equip"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["77", "2"] }] },
        { name: "seat", min: 1, max: 3, order: 1,
          pool: [{ when: [], options: ["seat:1", "seat:2", "seat:3"] }] },
      ],
    },
    preactivation: [], option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.heroes = { 0: "guanyu", 1: "zhaoyun", 2: "diaochan", 3: "zhangfei" };
  view.snapshot.players = [0, 1, 2, 3].map((seat) => ({
    seat, hp: 4, hp_max: 4, hand_count: 0, alive: true, equip: [], judge: [], identity: null,
  }));
  view.snapshot.self_hand = [red];
  view.snapshot.players[0].equip = [halberd];
  view.skills = [{
    skill_id: "hero.guanyu.wusheng", name: "武圣", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "选择1张红色牌当【杀】使用或打出", convert_sources: [2, 77] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButton = () => $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.guanyu.wusheng");
  const nextButton = () => $("t-options").children.find((c) => c.textContent === "下一步");
  const clickSeat = (seat) => (seatBox(seat).listeners.click || []).forEach(
    (fn) => fn({ target: seatBox(seat) }));
  const clickNext = () => (nextButton().listeners.click || []).forEach(
    (fn) => fn({ target: nextButton() }));

  (skillButton().listeners.click || []).forEach((fn) => fn({ target: skillButton() }));
  const redChip = $("t-hand").children[0];
  (redChip.listeners.click || []).forEach((fn) => fn({ target: redChip }));
  clickNext();

  const playStep = ui.state.preactivation && ui.state.preactivation.steps[1];
  check("㉿TS1 出牌阶段转化【杀】的目标步 1..3 画成多选（不是点一下自动进下一步）",
    !!playStep && ui.preactivationSeatMulti(playStep) === true
      && /已选 0 \/ 1–3 个/.test($("t-target-hint").textContent),
    `multi=${playStep && ui.preactivationSeatMulti(playStep)} hint="${$("t-target-hint").textContent}"`);

  clickSeat(1);
  clickSeat(3);
  clickSeat(2);
  check("㉿TS2 三名目标都选中，计数走「已选 3 / 1–3 个」",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[1,3,2]"
      && /已选 3 \/ 1–3 个/.test($("t-target-hint").textContent),
    `values=${JSON.stringify(ui.state.preactivation.answers[1].values)} hint="${$("t-target-hint").textContent}"`);

  clickNext();
  const playAction = ui.buildAction();
  check("㉿TS3 确认 = ActivateSkill(来源, 完整 3 目标集)，顺序 = 点击顺序",
    !!playAction && playAction.kind === "ActivateSkill"
      && playAction.skill_id === "hero.guanyu.wusheng"
      && JSON.stringify(playAction.picks) === "[2]"
      && JSON.stringify(playAction.choices) === '["seat:1","seat:3","seat:2"]'
      && $("btn-submit").disabled === false,
    JSON.stringify(playAction));
  ui.resetSelection();
}

{
  const dodge = { ...synthCard(52, "闪"), type: "dodge" };
  const halberd = { ...synthCard(77, "方天画戟"), type: "halberd", slot: "weapon", attack_range: 4 };
  const pending = {
    kind: "RESPOND", actor: 0, seq: 941, cause: "BORROWED_SWORD", required: "slash",
    prompt: "【借刀杀人】：对 0 号位使用【杀】，或交出武器。",
    legal_actions: [{ kind: "Pass", answers_seq: 941 }],
    candidate_index: {},
    preactivations: {
      "hero.zhaoyun.longdan": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["52"] }] },
        { name: "seat", min: 1, max: 3, order: 1,
          pool: [{ when: ["52"], options: ["seat:0", "seat:2", "seat:3"] }] },
      ],
    },
    preactivation: [], option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.heroes = { 0: "zhaoyun", 1: "guanyu", 2: "diaochan", 3: "zhangfei" };
  view.snapshot.players = [0, 1, 2, 3].map((seat) => ({
    seat, hp: 4, hp_max: 4, hand_count: 0, alive: true, equip: [], judge: [], identity: null,
  }));
  view.snapshot.self_hand = [dodge];
  view.snapshot.players[0].equip = [halberd];
  view.skills = [{
    skill_id: "hero.zhaoyun.longdan", name: "龙胆", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "你可以把【杀】当【闪】、把【闪】当【杀】使用或打出。", convert_sources: [52] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButton = () => $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
  const nextButton = () => $("t-options").children.find((c) => c.textContent === "下一步");
  const clickSeat = (seat) => (seatBox(seat).listeners.click || []).forEach(
    (fn) => fn({ target: seatBox(seat) }));

  (skillButton().listeners.click || []).forEach((fn) => fn({ target: skillButton() }));
  const dodgeChip = $("t-hand").children[0];
  (dodgeChip.listeners.click || []).forEach((fn) => fn({ target: dodgeChip }));
  (nextButton().listeners.click || []).forEach((fn) => fn({ target: nextButton() }));

  check("㉿TS4 借刀窗口的转化【杀】目标步首项 = Bn，预选并标为必选",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[0]"
      && seatBox(0).classList.contains("req")
      && $("t-target-hint").textContent.includes("借刀指定的目标（必选）"),
    `values=${JSON.stringify(ui.state.preactivation.answers[1].values)} hint="${$("t-target-hint").textContent}"`);

  clickSeat(2);
  clickSeat(3);
  (nextButton().listeners.click || []).forEach((fn) => fn({ target: nextButton() }));
  const borrowAction = ui.buildAction();
  check("㉿TS5 追加两个额外目标后确认 = ActivateSkill(来源, [Bn, extra, extra])",
    !!borrowAction && borrowAction.kind === "ActivateSkill"
      && borrowAction.skill_id === "hero.zhaoyun.longdan"
      && JSON.stringify(borrowAction.picks) === "[52]"
      && JSON.stringify(borrowAction.choices) === '["seat:0","seat:2","seat:3"]',
    JSON.stringify(borrowAction));
  ui.resetSelection();
}

/* ============ W7-03（D4）：清单目标步的**逐来源上限** =================================
 *
 * 自然局（关羽 + 方天 + 唯一红手牌）：来源步两条——装备区的方天画戟（红色 ⇒ 武圣的合法
 * 来源，但不在手牌 ⇒ 不是"最后一张手牌"，上限 1）与手里的最后一张红牌（1..3）。清单目标
 * 步的 `max` 是**全局** 3，而两行的 `options` 完全相同（可达目标与来源无关）⇒ 上限只能读
 * 行上的 `max`。界面必须在**选了来源之后**把可选数收在该来源的合法数目内：选方天只许 1 个，
 * 选手牌红牌才许 3 个；确认因此拼不出引擎会拒绝的组合（D4）。
 */
{
  const red = { ...synthCard(2, "桃"), type: "peach", color: "red" };
  const halberd = { ...synthCard(77, "方天画戟"), type: "halberd", slot: "weapon", attack_range: 4 };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 950,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 950 }],
    candidate_index: {},
    virtual_index: {
      "2": [{ card_id: -5, skill_ids: ["hero.guanyu.wusheng"] }],
      "77": [{ card_id: -6, skill_ids: ["hero.guanyu.wusheng"] }],
    },
    preactivations: {
      "hero.guanyu.wusheng": [
        { name: "card", zones: ["hand", "equip"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["77", "2"] }] },
        { name: "seat", min: 1, max: 3, order: 1,
          pool: [
            { when: ["77"], options: ["seat:1", "seat:2", "seat:3"], max: 1 },
            { when: ["2"], options: ["seat:1", "seat:2", "seat:3"], max: 3 },
          ] },
      ],
    },
    preactivation: [], option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.heroes = { 0: "guanyu", 1: "zhaoyun", 2: "diaochan", 3: "zhangfei" };
  view.snapshot.players = [0, 1, 2, 3].map((seat) => ({
    seat, hp: 4, hp_max: 4, hand_count: 0, alive: true, equip: [], judge: [], identity: null,
  }));
  view.snapshot.self_hand = [red];
  view.snapshot.players[0].equip = [halberd];
  view.skills = [{
    skill_id: "hero.guanyu.wusheng", name: "武圣", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "选择1张红色牌当【杀】使用或打出", convert_sources: [2, 77] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButton = () => $("t-skills").children.find(
    (b) => b.getAttribute("data-skill-id") === "hero.guanyu.wusheng");
  const nextButton = () => $("t-options").children.find((c) => c.textContent === "下一步");
  const clickSeat = (seat) => (seatBox(seat).listeners.click || []).forEach(
    (fn) => fn({ target: seatBox(seat) }));
  const clickNext = () => (nextButton().listeners.click || []).forEach(
    (fn) => fn({ target: nextButton() }));
  const equipChip = selfEquipChip;
  const handChip = () => $("t-hand").children[0];

  (skillButton().listeners.click || []).forEach((fn) => fn({ target: skillButton() }));
  check("㉿WC1 来源步把**装备区**的方天画戟列成可点（武圣的红色装备来源）",
    !!equipChip() && !equipChip().classList.contains("dis"),
    `chip=${!!equipChip()} dis=${equipChip() && equipChip().classList.contains("dis")}`);

  (equipChip().listeners.click || []).forEach((fn) => fn({ target: equipChip() }));
  clickNext();
  const paEquip = ui.state.preactivation;
  check("㉿WC2 选方天 ⇒ 目标步上限收到该来源的 1（不是全局 3）",
    ui.preactivationSeatCap(paEquip) === 1
      && ui.preactivationStepCap(paEquip, 1) === 1
      && /已选 0 \/ 1 个/.test($("t-target-hint").textContent),
    `cap=${ui.preactivationSeatCap(paEquip)} hint="${$("t-target-hint").textContent}"`);

  clickSeat(1);
  clickSeat(2);
  check("㉿WC3 上限 1：再点第二个座位不再收",
    JSON.stringify(paEquip.answers[1].values) === "[1]"
      && /已选 1 \/ 1 个/.test($("t-target-hint").textContent),
    `values=${JSON.stringify(paEquip.answers[1].values)} hint="${$("t-target-hint").textContent}"`);

  clickNext();
  const cappedAction = ui.buildAction();
  check("㉿WC4 选方天的确认 = 1 目标（不会拼出引擎会拒绝的组合）",
    !!cappedAction && cappedAction.kind === "ActivateSkill"
      && JSON.stringify(cappedAction.picks) === "[77]"
      && JSON.stringify(cappedAction.choices) === '["seat:1"]',
    JSON.stringify(cappedAction));

  // 重选来源：手牌的最后一张红牌 ⇒ 同一目标步的上限变成 3。
  ui.jumpPreativationStep(0);
  (handChip().listeners.click || []).forEach((fn) => fn({ target: handChip() }));
  clickNext();
  const paHand = ui.state.preactivation;
  check("㉿WC5 选手牌红牌（最后一张手牌）⇒ 目标步上限 3",
    ui.preactivationSeatCap(paHand) === 3
      && /已选 0 \/ 1–3 个/.test($("t-target-hint").textContent),
    `cap=${ui.preactivationSeatCap(paHand)} hint="${$("t-target-hint").textContent}"`);

  clickSeat(1);
  clickSeat(2);
  clickSeat(3);
  clickNext();
  const fullAction = ui.buildAction();
  check("㉿WC6 手牌来源可点满 3 个目标并确认",
    !!fullAction && fullAction.kind === "ActivateSkill"
      && JSON.stringify(fullAction.picks) === "[2]"
      && JSON.stringify(fullAction.choices) === '["seat:1","seat:2","seat:3"]',
    JSON.stringify(fullAction));
  ui.resetSelection();
}

/* ============ W6-03：观星双行拖拽面（合成 ARRANGE；S4：真实手势走人工验收） ============
 *
 * 判据（自动化的那一半）：两行容器渲染、上行 = 被观看的牌、下行 = 空槽、点选移动后
 * `buildAction()` 拼出的正是 `ArrangeCards(order, split)`（且键序 answers_seq 最先，
 * 能通过 `submitAction` 的 JSON.stringify 自检）、确认按钮只在顺序完整时可点、
 * 取消按钮**不存在**（强制、无取消）、规格缺失时 fail-closed 显示「排列不可用，请刷新」。
 * 拖拽手势本身不在这里断言（S4：DOM 垫片不模拟真手势，人工实机验收）。
 */
{
  const revealed = [
    { ...synthCard(71, "八卦阵"), suit: "spade", rank: 2 },
    { ...synthCard(72, "闪"), suit: "heart", rank: 11 },
    { ...synthCard(73, "雌雄双股剑"), suit: "club", rank: 2 },
  ];
  const arrangePending = {
    kind: "ARRANGE", actor: 0, seq: 77, timing: "PHASE_PREPARE",
    prompt: "请排列这些牌：给出顺序与前几张置于牌堆顶。",
    revealed,
    legal_actions: [
      { answers_seq: 77, kind: "ArrangeCards", order: [0, 1, 2], split: 3 },
      { answers_seq: 77, kind: "ArrangeCards", order: [1, 2, 0], split: 2 },
      { answers_seq: 77, kind: "ArrangeCards", order: [2, 1, 0], split: 2 },
      { answers_seq: 77, kind: "ArrangeCards", order: [2, 0, 1], split: 1 },
    ],
    candidate_index: {}, option_seats: {},
    context: {
      requirement: "观看牌堆顶若干张牌，以任意顺序放回牌堆顶 / 牌堆底",
      label: null, skill_hints: [],
    },
    preactivations: {}, preactivation: [],
    arrange: { count: 3, split: "free" },
  };
  const arrangeView = synthView(arrangePending);
  const rowOf = (name) => $("t-options").children.find(
    (node) => node.getAttribute && node.getAttribute("data-row") === name);
  const cardsBox = (row) => row.children[1];
  const cardIndexes = (row) => cardsBox(row).children
    .filter((node) => node.getAttribute && node.getAttribute("data-card-index") !== undefined)
    .map((node) => Number(node.getAttribute("data-card-index")));
  const slots = (row) => cardsBox(row).children.filter(
    (node) => node.getAttribute && node.getAttribute("data-empty") === "1");
  const cardNode = (row, index) => cardsBox(row).children.find(
    (node) => node.getAttribute && node.getAttribute("data-card-index") === String(index));
  const clickNode = (node) => (node.listeners.click || []).forEach((fn) => fn({ target: node, preventDefault() {} }));

  ui.state.view = arrangeView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(arrangeView);

  const topRow = rowOf("top");
  const bottomRow = rowOf("bottom");
  check("㉿GX1 排列面 = 上下两行，标签是「牌堆顶 / 牌堆底」（不是「获得牌」）",
    !!topRow && !!bottomRow
      && topRow.children[0].textContent.includes("牌堆顶")
      && bottomRow.children[0].textContent.includes("牌堆底")
      && !bottomRow.children[0].textContent.includes("获得牌"),
    `top=${topRow && topRow.children[0].textContent} bottom=${bottomRow && bottomRow.children[0].textContent}`);
  check("㉿GX2 ARRANGE 走唯一的新渲染路径（两行，不铺候选按钮）",
    ui.renderKind(arrangePending, arrangePending.legal_actions) === "arrange"
      && $("t-options").children.length === 2,
    `kind=${ui.renderKind(arrangePending, arrangePending.legal_actions)} 顶层子节点=${$("t-options").children.length}`);
  check("㉿GX3a 牌面只由 `pending.revealed` 下发（规格里没有 cards 副本）",
    !("cards" in arrangePending.arrange)
      && JSON.stringify(ui.arrangeCards(arrangePending)) === JSON.stringify(revealed)
      && ui.arrangeCards({ ...arrangePending, revealed: [] }) === null,
    `spec=${JSON.stringify(arrangePending.arrange)}`);
  check("㉿GX3 上行 = 被观看的 3 张牌，顺序与 revealed 一致",
    JSON.stringify(cardIndexes(topRow)) === "[0,1,2]"
      && ["八卦阵", "闪", "雌雄双股剑"].every((name) => cardsBox(topRow).text().includes(name)),
    `上行=${cardsBox(topRow).text()}`);
  check("㉿GX4 下行 = 3 个空槽（自由切分下留 count 个落点）",
    slots(bottomRow).length === 3 && cardIndexes(bottomRow).length === 0,
    `下行槽=${slots(bottomRow).length} 牌=${cardIndexes(bottomRow).length}`);
  check("㉿GX5 初始顺序完整 ⇒ 确认可点；取消按钮不存在（强制、无取消）",
    $("btn-submit").disabled === false
      && $("btn-submit").textContent === "确认"
      && $("btn-cancel").hidden === true,
    `disabled=${$("btn-submit").disabled} submit="${$("btn-submit").textContent}" cancelHidden=${$("btn-cancel").hidden}`);
  const initialAction = ui.buildAction();
  check("㉿GX6 初始提交 = ArrangeCards(order [0,1,2], split 3)，键序 answers_seq 最先",
    !!initialAction
      && Object.keys(initialAction).join(",") === "answers_seq,kind,order,split"
      && JSON.stringify(initialAction) === JSON.stringify(
        { answers_seq: 77, kind: "ArrangeCards", order: [0, 1, 2], split: 3 }),
    JSON.stringify(initialAction));

  // 点牌 → 点下行空槽 = 拖到牌堆底（跨行）；点牌 → 点同行的牌 = 行内排序。
  clickNode(cardNode(topRow, 0));
  const topAfterSelect = rowOf("top");
  check("㉿GX7 点牌 = 选中它（第一下只选中，不移动）",
    ui.state.arrange && ui.state.arrange.selected === 0
      && cardNode(topAfterSelect, 0).classList.contains("sel"),
    `selected=${ui.state.arrange && ui.state.arrange.selected}`);
  clickNode(slots(rowOf("bottom"))[0]);
  check("㉿GX8 选中后点下行空槽 = 把这张牌移到牌堆底",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[1,2]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[0]"
      && slots(rowOf("bottom")).length === 2,
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))} 槽=${slots(rowOf("bottom")).length}`);
  const movedDown = ui.buildAction();
  check("㉿GX9 跨行后的载荷 = order [1,2,0] / split 2（上行再接下行）",
    !!movedDown
      && JSON.stringify(movedDown) === JSON.stringify(
        { answers_seq: 77, kind: "ArrangeCards", order: [1, 2, 0], split: 2 })
      && arrangePending.legal_actions.some(
        (candidate) => JSON.stringify(candidate) === JSON.stringify(movedDown)),
    JSON.stringify(movedDown));

  // 行内排序：选第 2 张、点第 1 张 ⇒ 插到它前面，上行变 [2,1]。
  clickNode(cardNode(rowOf("top"), 2));
  clickNode(cardNode(rowOf("top"), 1));
  check("㉿GX10 点另一张牌 = 行内排序（插到它前面），载荷 order [2,1,0] / split 2",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[2,1]"
      && JSON.stringify(ui.buildAction()) === JSON.stringify(
        { answers_seq: 77, kind: "ArrangeCards", order: [2, 1, 0], split: 2 }),
    `上=${cardIndexes(rowOf("top"))} action=${JSON.stringify(ui.buildAction())}`);

  // 再移一张到下行 ⇒ 最终载荷 order [2,0,1] / split 1。
  clickNode(cardNode(rowOf("top"), 1));
  clickNode(slots(rowOf("bottom"))[0]);
  const finalAction = ui.buildAction();
  check("㉿GX11 最终载荷 = order [2,0,1] / split 1, 且逐字节是候选里的那一条",
    !!finalAction
      && JSON.stringify(finalAction) === JSON.stringify(
        { answers_seq: 77, kind: "ArrangeCards", order: [2, 0, 1], split: 1 })
      && arrangePending.legal_actions.some(
        (candidate) => JSON.stringify(candidate) === JSON.stringify(finalAction)),
    JSON.stringify(finalAction));

  // 顺序不完整（重复 / 缺一张）⇒ 确认必须置灰（buildAction 的完整性闸）。
  const brokenLayout = { ...ui.state.arrange, top: [2, 2], bottom: [0] };
  ui.state.arrange = brokenLayout;
  ui.renderPending(arrangeView);
  check("㉿GX12 顺序不完整时确认按钮置灰（buildAction 返回 null）",
    ui.buildAction() === null && $("btn-submit").disabled === true,
    `action=${JSON.stringify(ui.buildAction())} disabled=${$("btn-submit").disabled}`);
  ui.state.arrange = null;
  ui.renderPending(arrangeView);

  // 409 / 无技能定义 ⇒ 投影不下发规格：画「排列不可用，请刷新」，不铺任何可提交的牌。
  const noSpecView = synthView({ ...arrangePending, arrange: null });
  ui.state.view = noSpecView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(noSpecView);
  check("㉿GX13 规格缺失（409 裸 Query / 无定义）⇒ fail-closed：只有「排列不可用，请刷新」",
    ui.renderKind(noSpecView.pending, noSpecView.pending.legal_actions) === "arrange"
      && $("t-options").children.length === 1
      && $("t-options").text().includes("排列不可用，请刷新")
      && !rowOf("top") && !rowOf("bottom")
      && $("btn-submit").disabled === true
      && $("btn-cancel").hidden === true,
    `options="${$("t-options").text()}" disabled=${$("btn-submit").disabled}`);

  // 固定切分（未来的李典 2/2）：两行各按声明铺开，跨行移动被拒绝。
  const fixedCards = revealed.concat([synthCard(74, "桃")]);
  const fixedPending = {
    ...arrangePending, seq: 78, revealed: fixedCards,
    legal_actions: [{ answers_seq: 78, kind: "ArrangeCards", order: [0, 1, 2, 3], split: 2 }],
    arrange: { count: 4, split: { top: 2, bottom: 2 } },
  };
  const fixedView = synthView(fixedPending);
  ui.state.view = fixedView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(fixedView);
  const fixedTop = rowOf("top");
  const fixedBottom = rowOf("bottom");
  check("㉿GX14 固定切分：上行 2 张 / 下行 2 张，初始载荷 order [0,1,2,3] / split 2",
    JSON.stringify(cardIndexes(fixedTop)) === "[0,1]"
      && JSON.stringify(cardIndexes(fixedBottom)) === "[2,3]"
      && slots(fixedBottom).length === 0
      && JSON.stringify(ui.buildAction()) === JSON.stringify(
        { answers_seq: 78, kind: "ArrangeCards", order: [0, 1, 2, 3], split: 2 }),
    `上=${cardIndexes(fixedTop)} 下=${cardIndexes(fixedBottom)} action=${JSON.stringify(ui.buildAction())}`);
  clickNode(cardNode(fixedTop, 0));
  clickNode(cardNode(fixedBottom, 2));
  check("㉿GX15 固定切分拒绝跨行移动（张数由服务端声明，前端不改）",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[0,1]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[2,3]",
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))}`);

  // 真服务器回环：投影下发规格 ⇒ 前端拼的 action 通过 `submitAction` 的逐字段自检。
  // 固定 4 人 / 种子 7 / 人类 0 号位诸葛亮：第一问就是「是否发动【观星】」（实测）。
  const guanxing = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null,
    seed: 7, my_hero: "zhugeliang",
  });
  await openSession(guanxing);
  const activation = ui.state.view.pending;
  check("㉿GX16 真会话（4 人 / 种子 7 / 诸葛亮）：第一问是【观星】的发动询问",
    !!activation && activation.kind === "SKILL_ACTIVATION"
      && activation.skill_id === "hero.zhugeliang.guanxing",
    `kind=${activation && activation.kind} skill=${activation && activation.skill_id}`);
  const guanxingButton = $("t-skills").children.find(
    (node) => node.getAttribute("data-skill-id") === "hero.zhugeliang.guanxing");
  // 观星是**触发技**（有钩子、无主动候选入口）⇒ 底部技能按钮只说明、不接 click（U1-06）。
  // 它的"是否发动"由**询问自己的直提态**回答：这就是 B 形技能的新表面（U2-03）。
  check("㉿GX16b 观星这类 B 形发动询问 = 直提态（不再铺「发动技能 hero.…」候选行）",
    !!guanxingButton
      && $("t-options").children.length === 0
      && candidateRowButtons().length === 0
      && /将提交：发动【观星】/.test($("t-choice").textContent)
      && $("btn-submit").disabled === false,
    `button=${!!guanxingButton} choice="${$("t-choice").textContent}" submit=${$("btn-submit").disabled} cand=${candidateRowButtons().length} kind=${ui.renderKind(activation, activation && activation.legal_actions)} options=${JSON.stringify($("t-options").children.map((node) => node.textContent))}`);
  await ui.submitAction();
  const realArrange = ui.state.view.pending;
  check("㉿GX17 确认发动后 ARRANGE 询问带投影规格（count 4 / free）与 4 张 revealed 牌面",
    !!realArrange && realArrange.kind === "ARRANGE"
      && !!realArrange.arrange
      && realArrange.arrange.count === 4
      && realArrange.arrange.split === "free"
      && !("cards" in realArrange.arrange)
      && (realArrange.revealed || []).length === 4,
    `kind=${realArrange && realArrange.kind} arrange=${JSON.stringify(realArrange && realArrange.arrange && {
      count: realArrange.arrange.count, split: realArrange.arrange.split,
    })}`);
  // 409 的 fail-closed 出口（W6-07 / 评审 SPEC-a 的 409 半边）：提交一条**陈旧的**排列
  // ⇒ 服务端 409 ⇒ `submitAction` 不猜、改为 `refresh()` 重取当前询问；刷新回来的载荷若
  // 仍给不出规格（真实 409 = 裸 `Query`，投影 `arrange: null`），界面必须落到
  // 「排列不可用，请刷新」，而不是把陈旧那份画成可提交的面。
  const beforeStale = calls.length;
  staleAnswerStatus = 409;
  nextGetPending = { ...realArrange, arrange: null };
  await ui.submitAction();
  if (ui.flushPlayback) ui.flushPlayback();
  const staleCalls = calls.slice(beforeStale);
  // 注：那句"这个询问已过期…已为你刷新"随后会被 `refreshSubmit` 的模式提示覆盖，故这里不
  // 断言它（瞬态文案），只断言**行为**：先答一次、失败后只刷新、界面落到 fail-closed。
  check("㉿GX19 409 ⇒ 不提交陈旧排列、改走 refresh()；刷新后无规格 ⇒ fail-closed",
    staleCalls.some((call) => call.path.includes("/answer"))
      && staleCalls.some((call) => !call.path.includes("/answer"))
      && $("t-options").text().includes("排列不可用，请刷新")
      && !rowOf("top") && !rowOf("bottom")
      && $("btn-submit").disabled === true,
    `calls=${staleCalls.map((call) => call.path).join(" | ")} options="${$("t-options").text()}"`
      + ` disabled=${$("btn-submit").disabled} top=${!!rowOf("top")}`);
  // 复原真询问，后面的成功提交（㉿GX18）照旧。
  ui.state.view.pending = realArrange;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ui.state.view);
  const realTop = rowOf("top");
  if (realTop) clickNode(cardNode(realTop, 0));
  const realBottom = rowOf("bottom");
  if (realBottom && slots(realBottom)[0]) clickNode(slots(realBottom)[0]);
  const realAction = ui.buildAction();
  const beforeAnswer = calls.length;
  await ui.submitAction();
  const sent = calls.slice(beforeAnswer).find((call) => call.path.includes("/answer"));
  check("㉿GX18 真服务器接受这条 ArrangeCards（投影 + 前端载荷闭环），下一问不再是 ARRANGE",
    !!realAction
      && !!sent
      && JSON.stringify(sent.body.action) === JSON.stringify(realAction)
      && sent.body.action.kind === "ArrangeCards"
      && sent.body.action.order.length === 4
      && sent.body.action.split === 3
      && !!ui.state.view.pending && ui.state.view.pending.kind !== "ARRANGE",
    `action=${JSON.stringify(realAction)} next=${ui.state.view.pending && ui.state.view.pending.kind} choice="${$("t-choice").textContent}"`);

  ui.resetSelection();
}


/* ============ U1-01 布局壳：2 / 4 / 6 / 8 人环桌几何 ============
 *
 * 判据落在 **renderTable 写出的 `data-ring-*` 设计坐标**上（不是另算一份布局）：
 * 座位数 = 总人数 − 1（自己不在环上）、座号 +1 在最右、角度沿上半弧递增、
 * 8 人局 7 个对手在 210×150 下**无正面积重叠**、2 人局只有正上方一个对手。
 * U2-02 / D6：+8% 的 228×163 只在 8 人局破功（U1-01d2 钉住），故实际外形保持 210×150。
 * 设计坐标无重叠 ⇔ 1920×1080 与 1280×720 都无重叠：座位宽 / 高随壳宽与设计单位同比例缩放。
 */
{
  const savedView = ui.state.view;
  const savedLog = ui.state.logRecords;
  const designStage = { width: 1600, height: 608 };
  // U2-02 / D6：对手「+8%」的 228×163 在 8 人环上会重叠（见 U1-01d2），故取票面允许的回退——
  // 座位卡**外形保持 210×150**、只放大内容；矩形判据因此按实际外形算。
  const seatW = 210;
  const seatH = 150;
  const grownSeatW = 228;
  const grownSeatH = 163;
  const ringView = (count, selfSeat = 0) => ({
    seed: 1, player_count: count, human_seat: selfSeat, finished: false, timeout_seconds: null,
    // `serve.py` 的响应里 `heroes` 是 list（按座位下标取）；合成视图同形，别用对象冒充。
    heroes: Array.from({ length: count }, () => "guojia"),
    skills: [],
    pending: {
      kind: "PLAY_PHASE", actor: selfSeat, prompt: "", legal_actions: [],
      candidate_index: {}, option_seats: {}, context: null,
    },
    snapshot: {
      seat: selfSeat, phase: "play", turn_seat: selfSeat,
      players: Array.from({ length: count }, (_, seat) => ({
        seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [], identity: null,
      })),
      self_hand: [], deck_count: 40, discard: [], processing: [],
    },
  });
  const show = (view) => {
    ui.state.view = view;
    ui.state.logRecords = [];
    ui.resetSelection();
    ui.renderTable();
  };
  const ringSeats = () => $("t-seats").children.map((node) => ({
    seat: Number(node.getAttribute("data-seat")),
    angle: Number(node.getAttribute("data-ring-angle")),
    x: Number(node.getAttribute("data-ring-x")),
    y: Number(node.getAttribute("data-ring-y")),
  }));
  const overlaps = (boxes, w = seatW, h = seatH) => {
    const found = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const overlapX = Math.abs(a.x - b.x) < w;
        const overlapY = Math.abs(a.y - b.y) < h;
        if (overlapX && overlapY) found.push(`${a.seat}/${b.seat}`);
      }
    }
    return found;
  };

  for (const count of [2, 4, 6, 8]) {
    show(ringView(count));
    const seats = ringSeats();
    check(`U1-01a ${count} 人：环上 ${count - 1} 个对手，自己在底部面板、不在环上`,
      $("t-seats").children.length === count - 1
        && !seats.some((item) => item.seat === 0)
        && $("t-self-seat").getAttribute("data-seat") === "0"
        && $("t-self-seat").classList.contains("me"),
      `环上 ${$("t-seats").children.length} / 座号 ${seats.map((item) => item.seat).join(",")}`);
    check(`U1-01b ${count} 人：座位号 +1 在自己的右手边（−30° 的右下邻座）`,
      count === 2
        ? seats[0] && Math.abs(seats[0].x - designStage.width / 2) < 0.01
        : Math.abs(seats[0].angle + 30) < 0.01
          && seats[0].x > designStage.width / 2
          && Math.abs(seats[0].y - Math.max(...seats.map((item) => item.y))) < 0.01
          && seats[seats.length - 1].x < designStage.width / 2,
      seats.map((item) => `${item.seat}:${item.angle}° x=${item.x.toFixed(1)}`).join(" "));
    // "均布"不能只断单调：相邻角差必须相等（票面「按 N 边形均布」）。
    const gaps = seats.slice(1).map((item, index) => item.angle - seats[index].angle);
    check(`U1-01c ${count} 人：对手沿上半弧按座号逆时针均布`,
      seats.every((item, index) => (index === 0 ? true : item.angle > seats[index - 1].angle))
        && seats.every((item) => item.angle >= -30.01 && item.angle <= 210.01)
        && (count === 2
          ? seats[0].angle === 90
          : Math.abs((seats[seats.length - 1].angle - seats[0].angle) - 240) < 0.01)
        && (gaps.length === 0 || gaps.every((gap) => Math.abs(gap - gaps[0]) < 0.01)),
      `${seats.map((item) => `${item.seat}:${item.angle}`).join(" ")} / 间距 ${gaps.map((gap) => gap.toFixed(2)).join(",")}`);
    check(`U1-01d ${count} 人：对手座位无正面积重叠（210×150 回退外形 / 设计尺寸）`,
      overlaps(seats).length === 0, overlaps(seats).join("、") || "无重叠");
    if (count === 8) {
      // 把票面 D6 的原尺寸跑同一套矩形判据：相邻角 40° ⇒ |dx| = 680·(cos(-30°) − cos 10°) ≈ 80.8、
      // |dy| = 235·(sin 10° + sin 30°) ≈ 158.3，两者都小于 228×163 → 外形不变的回退有据可依。
      const grown = overlaps(seats, grownSeatW, grownSeatH);
      check("U1-01d2 8 人：D6 的 228×163 会与两组相邻座位重叠 → 回退为外形不变的依据",
        grown.join("、") === "1/2、6/7",
        grown.join("、") || "（228×163 也不重叠——回退就无从谈起）");
    }
    if (count > 2) {
      const first = seats[0];
      const last = seats[seats.length - 1];
      check(`U1-01e ${count} 人：左右对称（座位 1 与座位 ${last.seat}）`,
        Math.abs(first.x + last.x - designStage.width) < 0.01 && Math.abs(first.y - last.y) < 0.01,
        `${first.x.toFixed(1)}+${last.x.toFixed(1)} / y ${first.y.toFixed(1)}、${last.y.toFixed(1)}`);
    }
    // 渲染路径：`left/top` 百分比必须就是设计坐标 / 1600×608（CSS 的尺寸 / 位移由
    // 真浏览器截图核，DOM 垫片没有布局引擎）。
    const placed = $("t-seats").children.map((node) => ({
      left: parseFloat(node.style.left),
      top: parseFloat(node.style.top),
    }));
    check(`U1-01e2 ${count} 人：座位定位 = 设计坐标换算的百分比`,
      placed.every((item, index) => Math.abs(item.left - (seats[index].x / designStage.width) * 100) < 0.01
        && Math.abs(item.top - (seats[index].y / designStage.height) * 100) < 0.01),
      placed.map((item) => `${item.left}%/${item.top}%`).join(" "));
  }

  // 自己不是 0 号位时，环从「自己 + 1」开始编号（座位 +1 仍在右手边）。
  show(ringView(4, 2));
  const rotated = ringSeats();
  check("U1-01g 自己坐 2 号位：环顺序从 3 开始（3 最右、1 最左），自己在底部",
    rotated.map((item) => item.seat).join(",") === "3,0,1"
      && rotated[0].x === Math.max(...rotated.map((item) => item.x))
      && rotated[2].x === Math.min(...rotated.map((item) => item.x))
      && $("t-self-seat").getAttribute("data-seat") === "2",
    `环=${rotated.map((item) => item.seat).join(",")} 自己=${$("t-self-seat").getAttribute("data-seat")}`);

  // 顶栏身份配比：4~8 人公开配比；2 人局（无身份局）整块隐藏。
  show(ringView(8));
  check("U1-01h 8 人局顶栏身份配比 = 主公×1 忠臣×2 反贼×4 内奸×1",
    $("t-identity-ratio").textContent === "主公×1 忠臣×2 反贼×4 内奸×1"
      && !$("t-identity-panel").classList.contains("hidden"),
    `"${$("t-identity-ratio").textContent}"`);
  show(ringView(2));
  check("U1-01i 2 人局（无身份局）不显示身份配比",
    $("t-identity-ratio").textContent === "" && $("t-identity-panel").classList.contains("hidden"),
    `"${$("t-identity-ratio").textContent}" hidden=${$("t-identity-panel").classList.contains("hidden")}`);

  // 顶栏轮次：同一座位第二次 TURN_START 才进下一轮（不是"次数 ÷ 人数"）。
  show(ringView(8));
  ui.state.logRecords = Array.from({ length: 8 }, (_, index) => ({
    kind: "timing", timing: "TURN_START", actor: index % 8, seq: index + 1,
  }));
  ui.renderTable();
  check("U1-01j 8 人各行动一次仍在第 1 轮（最后一人的回合不提前进轮）",
    $("t-round").textContent === "1", `t-round="${$("t-round").textContent}"`);
  ui.state.logRecords.push({ kind: "timing", timing: "TURN_START", actor: 0, seq: 9 });
  ui.renderTable();
  check("U1-01j2 回到座位 0 = 进入第 2 轮",
    $("t-round").textContent === "2", `t-round="${$("t-round").textContent}"`);

  // 中央舞台：处理区有牌 = 正在结算；牌全走了 = 淡出（无计时器判据）。
  show(ringView(4));
  check("U1-01k 中央舞台没有处理中的牌时淡出",
    $("t-action-layer").classList.contains("faded"));
  ui.state.view.snapshot.processing = [synthCard(101, "杀")];
  ui.renderTable();
  check("U1-01l 处理区有牌时舞台不淡出（牌全走了才淡出）",
    !$("t-action-layer").classList.contains("faded")
      && $("t-processing").children.length === 1,
    `processing=${$("t-processing").children.length} faded=${$("t-action-layer").classList.contains("faded")}`);

  // 事件流按钮默认折叠；点一下展开、再点收起。设置浮层同理。
  check("U1-01m 事件流面板默认折叠（index.html 带 hidden）",
    /<div[^>]*id="t-log-panel"[^>]*\bhidden\b/.test(html));
  $("t-log-panel").hidden = true;
  (($("t-log-toggle").listeners.click) || []).forEach((fn) => fn({ target: $("t-log-toggle") }));
  const opened = $("t-log-panel").hidden === false;
  (($("t-log-toggle").listeners.click) || []).forEach((fn) => fn({ target: $("t-log-toggle") }));
  check("U1-01n 事件流按钮展开 / 收起浮层",
    opened && $("t-log-panel").hidden === true,
    `第一次=${opened} 第二次=${$("t-log-panel").hidden}`);
  $("t-settings").hidden = true;
  (($("btn-settings").listeners.click) || []).forEach((fn) => fn({ target: $("btn-settings") }));
  check("U1-01o 设置按钮展开浮层（步长 / 服务端判定 / 刷新住在这里）",
    $("t-settings").hidden === false
      && !!$("btn-delay") && !!$("btn-takeover") && !!$("btn-refresh"));

  // 牌桌顶栏的导航三键 = 真切页（D7：旧「回放」图标按钮已删，回放入口就是导航里的「回放」）。
  $("view-table").hidden = false;
  $("view-replay").hidden = true;
  (($("nav-replay").listeners.click) || []).forEach((fn) => fn({ target: $("nav-replay") }));
  check("U1-01p 牌桌顶栏「回放」导航切到回放页",
    $("view-replay").hidden === false && $("view-table").hidden === true,
    `table=${$("view-table").hidden} replay=${$("view-replay").hidden}`);
  (($("nav-table").listeners.click) || []).forEach((fn) => fn({ target: $("nav-table") }));
  check("U1-01p2 顶栏「牌桌」切回牌桌",
    $("view-table").hidden === false && $("view-replay").hidden === true,
    `table=${$("view-table").hidden} replay=${$("view-replay").hidden}`);

  // 复原：后面的收尾断言（若有）仍看得到最后一局的真视图。
  ui.state.view = savedView;
  ui.state.logRecords = savedLog;
  ui.renderTable();
}


/* ============ U1-02 座位卡 / 武将块 / 卡面 ============
 *
 * 票面判据（`issues/02`；U2-02 按 D6 放大）：
 * - 对手**外形** 210×150（D6 的 228×163 在 8 人环上重叠，回退为外形不变、只放大内容）、
 *   自己 292×210；左武将块收窄 30%（右块 100 / 130）；判定行 33 / 48、装备行 42 / 60；
 * - 处理区 / 舞台卡面与手牌**同一套** 88×124 + 字号 14/11/21/9（不再有第二套小牌面规则）；
 *   舞台面板封顶 300（顶边 220 > 顶座下沿 210）、处理区一行 124 纵向滚动，不盖住环桌顶座；
 * - 体力分段 + current/max，上限 ≥6 只显 x/x；判定 3 槽、装备四槽；手牌数 / 座位号在 footer；
 * - 身份：主公明置、其余暗置 `?`、自己恒可见、阵亡亮明；无身份局不画 `?`；
 * - 横置「-------」虚线 / 酒泛红（无「酒」字）/ 大号「阵亡」叠加不互挡；
 * - 高亮：金框行动中 / 5px 绿外圈可指定 / 4px 内圈 + 填充 + 勾已选 / 悬停浅绿 / 两者都写；
 * - 卡面 88×124（设计单位）：左上花色 + 点数、顶部中间牌名、底部类型 / 属性 / 范围；三态；
 * - 场上牌（装备 / 判定 / 处理区）悬停全文；手牌与场上区域不重复显示。
 *
 * CSS 判据读 style.css（DOM 垫片没有布局引擎）；尺寸用 `var(--dsu)` 的设计单位
 * （1 单位 = 1600×900 设计稿 1px，1280 / 1920 下比例一致）。
 */
{
  const savedView = ui.state.view;
  const savedLog = ui.state.logRecords;
  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) {
      out.push(child);
      walk(child, out);
    }
    return out;
  };
  const hasClass = (child, name) => child.classList.contains(name)
    || String(child.className || "").split(/\s+/).includes(name);
  const byClass = (node, name) => walk(node).filter((child) => hasClass(child, name));
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const click = (node) => (node.listeners.click || []).forEach(
    (fn) => fn({ target: node, stopPropagation() {} }));

  const card = (id, name, extra = {}) => ({
    ...synthCard(id, name), slot: null, attribute: "普通", kind: "normal",
    description: `【${name}】的全文描述`, ...extra,
  });
  const slash = card(1, "杀");
  const peach = card(4, "桃");
  const weapon = card(21, "青釭剑", { type: "qinggang_sword", slot: "weapon", attack_range: 2 });
  const armor = card(22, "八卦阵", { type: "eight_diagrams", slot: "armor" });
  const horse = card(23, "赤兔", { type: "chitu", slot: "horse_offense" });
  const lightning = card(24, "闪电", { type: "lightning" });
  const indulgence = card(25, "乐不思蜀", { type: "indulgence" });
  const supply = card(26, "兵粮寸断", { type: "supply_shortage" });
  const players = (identityMode = true) => [
    {
      seat: 0, hp: 3, hp_max: 3, hand_count: 2, alive: true,
      equip: [weapon], judge: [indulgence], chained: true, wine_active: false,
      identity: identityMode ? "rebel" : null, identity_label: identityMode ? "反贼" : null,
      hero_name: "大乔", kingdom: "wu", kingdom_label: "吴",
    },
    {
      seat: 1, hp: 5, hp_max: 5, hand_count: 4, alive: true,
      equip: [armor], judge: [lightning], chained: false, wine_active: false,
      identity: null, identity_label: null,
      hero_name: "吕布", kingdom: "qun", kingdom_label: "群",
    },
    {
      seat: 2, hp: 6, hp_max: 6, hand_count: 0, alive: true,
      equip: [horse], judge: [], chained: true, wine_active: true,
      identity: identityMode ? "lord" : null, identity_label: identityMode ? "主公" : null,
      hero_name: "曹操", kingdom: "wei", kingdom_label: "魏",
    },
    {
      seat: 3, hp: 0, hp_max: 4, hand_count: 0, alive: false,
      equip: [], judge: [supply], chained: true, wine_active: true,
      identity: identityMode ? "loyalist" : null, identity_label: identityMode ? "忠臣" : null,
      hero_name: "刘备", kingdom: "shu", kingdom_label: "蜀",
    },
  ];
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 10,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      { answers_seq: 10, kind: "UseCard", card: slash, targets: [1] },
      { answers_seq: 10, kind: "UseCard", card: slash, targets: [2] },
      { answers_seq: 10, kind: "EndPhase" },
    ],
    candidate_index: {
      "1": { card: slash, pool: [1, 2], combos: [[1], [2]], min_targets: 1, max_targets: 1 },
    },
    context: null, option_seats: {},
  };
  const table = (identityMode = true) => ({
    seed: 1, player_count: 4, human_seat: 0, finished: false, timeout_seconds: null,
    heroes: ["daqiao", "lvbu", "caocao", "liubei"],
    skills: [],
    pending,
    snapshot: {
      seat: 0, phase: "play", turn_seat: 2,
      players: players(identityMode),
      self_hand: [slash, peach], deck_count: 40, discard: [],
      processing: [lightning],
    },
  });
  const show = (view) => {
    ui.state.view = view;
    ui.state.logRecords = [];
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };

  /* ---- CSS：设计尺寸 / 收窄 / 槽高 / 高亮环 ---- */
  check("U1-02a CSS：对手外形 210×150（D6 的 228×163 回退为外形不变）、自己 292×210（设计比例 + 设计舞台）",
    /\.ring \.seat\s*\{[^}]*width:\s*13\.125%[^}]*aspect-ratio:\s*210\s*\/\s*150/s.test(css)
      && !/aspect-ratio:\s*228\s*\/\s*163/.test(css)
      && /\.seat\.self\s*\{[^}]*flex:\s*0 0 18\.25%[^}]*aspect-ratio:\s*292\s*\/\s*210/s.test(css),
    `ring=${/\.ring \.seat\s*\{[^}]*aspect-ratio:\s*210\s*\/\s*150/s.test(css)} grown=${/aspect-ratio:\s*228\s*\/\s*163/.test(css)} self=${/\.seat\.self\s*\{[^}]*aspect-ratio:\s*292\s*\/\s*210/s.test(css)}`);
  check("U1-02b CSS：左武将块收窄 30%（右块 100 / 130 = 47.62% / 44.52%；对手外形回退不变）",
    /\.seat \.side-block\s*\{[^}]*flex:\s*0 0 47\.62%/s.test(css)
      && /\.self \.side-block\s*\{[^}]*flex:\s*0 0 44\.52%/s.test(css),
    `ring=${/\.seat \.side-block\s*\{[^}]*flex:\s*0 0 47\.62%/s.test(css)} self=${/\.self \.side-block\s*\{[^}]*flex:\s*0 0 44\.52%/s.test(css)}`);
  check("U1-02c CSS：对手内容放大（判定行 33 / 装备行 42、槽位字 10 / 标签 9）——外形仍 210×150",
    /\.seat \.judge-row\s*\{[^}]*calc\(33 \* var\(--dsu\)\)/s.test(css)
      && /\.seat \.equip-grid\s*\{[^}]*grid-auto-rows:\s*calc\(42 \* var\(--dsu\)\)/s.test(css)
      && /\.mini-slot\s*\{[^}]*font-size:\s*calc\(10 \* var\(--dsu\)\)/s.test(css)
      && /\.mini-slot\.judge-slot\s*\{[^}]*height:\s*calc\(33 \* var\(--dsu\)\)/s.test(css)
      && /\.mini-slot \.slot-k\s*\{[^}]*font-size:\s*calc\(9 \* var\(--dsu\)\)/s.test(css),
    `judge=${/calc\(33 \* var\(--dsu\)\)/.test(css)} equip=${/calc\(42 \* var\(--dsu\)\)/.test(css)} slot=${/\.mini-slot\s*\{[^}]*font-size:\s*calc\(10/s.test(css)} label=${/\.mini-slot \.slot-k\s*\{[^}]*font-size:\s*calc\(9/s.test(css)}`);
  check("U1-02c2 CSS：自己武将块内容放大（名 20 / 体力数字 13 / 判定 48 / 装备 60 / 槽位字 13 / 标签 12）",
    /\.self \.hero-name\s*\{[^}]*font-size:\s*calc\(20 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.hp-num\s*\{[^}]*font-size:\s*calc\(13 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.judge-row\s*\{[^}]*calc\(48 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.equip-grid\s*\{[^}]*grid-auto-rows:\s*calc\(60 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.mini-slot\s*\{[^}]*font-size:\s*calc\(13 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.mini-slot\.judge-slot\s*\{[^}]*height:\s*calc\(48 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.mini-slot \.slot-k\s*\{[^}]*font-size:\s*calc\(12 \* var\(--dsu\)\)/s.test(css),
    `name=${/\.self \.hero-name\s*\{[^}]*calc\(20/s.test(css)} hp=${/\.self \.hp-num\s*\{[^}]*calc\(13/s.test(css)} judge=${/\.self \.judge-row\s*\{[^}]*calc\(48/s.test(css)} equip=${/\.self \.equip-grid\s*\{[^}]*calc\(60/s.test(css)} slot=${/\.self \.mini-slot\s*\{[^}]*font-size:\s*calc\(13/s.test(css)} label=${/\.self \.mini-slot \.slot-k\s*\{[^}]*calc\(12/s.test(css)}`);
  check("U1-02c3 CSS：处理区 / 舞台卡面与手牌同一套 88×124 + 字号 14/11/21/9（0.63 版覆盖已删，只有一套卡面规则）",
    /\.card\.face\s*\{[^}]*width:\s*calc\(88 \* var\(--dsu\)\);\s*height:\s*calc\(124 \* var\(--dsu\)\)/s.test(css)
      && /\.card\.face \.suit\s*\{[^}]*font-size:\s*calc\(14 \* var\(--dsu\)\)/s.test(css)
      && /\.card\.face \.rank\s*\{[^}]*font-size:\s*calc\(11 \* var\(--dsu\)\)/s.test(css)
      && /\.card\.face \.cname\s*\{[^}]*font-size:\s*calc\(21 \* var\(--dsu\)\)/s.test(css)
      && /\.card\.face \.ctype\s*\{[^}]*font-size:\s*calc\(9 \* var\(--dsu\)\)/s.test(css)
      && !/\.stage-center \.processing \.card/.test(css)
      && !/calc\(56 \* var\(--dsu\)\)/.test(css)
      && !/calc\(79 \* var\(--dsu\)\)/.test(css)
      && /\.hand \.card\s*\{[^}]*flex:\s*0 0 auto/s.test(css)
      && /\.table-bottom\s*\{[^}]*flex:\s*0 0 27\.5%/s.test(css),
    `card=${/calc\(88 \* var\(--dsu\)\)/.test(css)} stageOverride=${/\.stage-center \.processing \.card/.test(css)} small=${/calc\(56 \* var\(--dsu\)\)/.test(css) || /calc\(79 \* var\(--dsu\)\)/.test(css)} hand=${/\.hand \.card\s*\{[^}]*flex:\s*0 0 auto/s.test(css)} bottom=${/\.table-bottom\s*\{[^}]*27\.5%/s.test(css)}`);
  // U2-02：卡面统一放大后，60.85% 居中的舞台面板会向上长进环桌、盖住顶部座位；故给面板封顶。
  // 几何算式（`table.js:RING_STAGE`）：圆心 y=370、ry=235；顶部座位（90°）下沿 = 370 − 235 + 150/2 = 210。
  // 面板 max-height 300 ⇒ 顶边 370 − 300/2 = 220 > 210；处理区再限一行卡面（125 含 1 设计单位余量）+ 纵向滚动，
  // 保证第一行卡面的上 / 下边框都在滚动框内（不裁切），第 2 行起整体在框外、不出半张牌；
  // 处理中说明最多 2 行（省略号），1280 下也不越过 300 上限。
  const stageRule = css.match(/\.stage-center\s*\{[^}]*\}/s);
  const stageMax = stageRule && /max-height:\s*calc\((\d+) \* var\(--dsu\)\)/.exec(stageRule[0]);
  const ringCenterY = 370;
  const topSeatBottom = ringCenterY - 235 + 150 / 2;
  const panelTop = ringCenterY - Number(stageMax ? stageMax[1] : 0) / 2;
  check("U1-02c4 CSS：舞台面板封顶 300（顶边 220 > 顶座下沿 210），处理区一行卡面（125 含余量）纵向滚动、说明最多 2 行",
    !!stageMax && panelTop > topSeatBottom
      && /\.stage-center\s*\{[^}]*overflow:\s*hidden/s.test(css)
      && /\.stage-center \.processing\s*\{[^}]*flex:\s*0 0 auto[^}]*max-height:\s*calc\(125 \* var\(--dsu\)\)[^}]*overflow-y:\s*auto/s.test(css)
      && /\.stage-center \.processing\s*\{[^}]*align-content:\s*flex-start/s.test(css)
      && /\.stage-center \.context\s*\{[^}]*-webkit-line-clamp:\s*2/s.test(css),
    `panelTop=${panelTop} topSeatBottom=${topSeatBottom} max=${stageMax ? stageMax[1] : "无"} clip=${/\.stage-center\s*\{[^}]*overflow:\s*hidden/s.test(css)} row=${/max-height:\s*calc\(125 \* var\(--dsu\)\)/.test(css)} scroll=${/\.stage-center \.processing\s*\{[^}]*overflow-y:\s*auto/s.test(css)}`);
  check("U1-02d CSS：可指定 = 5px 绿外圈、已选 = 4px 绿内圈、悬停浅绿、行动中 = 金框，当前 + 合法有绿角点",
    /\.seat\.targetable:not\(\.tsel\)\s*\{[^}]*0 0 0 5px var\(--ok\)/s.test(css)
      && /\.seat\.tsel::before\s*\{[^}]*inset 0 0 0 4px var\(--ok\)/s.test(css)
      && /\.seat\.targetable:not\(\.tsel\):hover\s*\{\s*background:\s*#eef7f1/.test(css)
      && /\.seat\.turn\s*\{[^}]*#b8860b/s.test(css)
      && /\.seat\.turn\.targetable:not\(\.tsel\)::after\s*\{[^}]*background:\s*var\(--ok\)/s.test(css),
    `legal=${/0 0 0 5px var\(--ok\)/.test(css)} selected=${/inset 0 0 0 4px var\(--ok\)/.test(css)} hover=${/background:\s*#eef7f1/.test(css)} dot=${/background:\s*var\(--ok\)/.test(css)}`);

  /* ---- DOM：尺寸 / 槽位 / 体力 / 身份 ---- */
  show(table());
  const seat1 = seatBox(1);
  const seat2 = seatBox(2);
  const seat3 = seatBox(3);
  const self = $("t-self-seat");
  check("U1-02e 环上仍是 3 个对手，自己在底部面板（U1-01 的座位归属不变）",
    $("t-seats").children.length === 3 && attr(self, "data-seat") === "0" && self.classList.contains("me"));

  check("U1-02f 武将块：势力 / 中文名 / 身份 / 手牌数 / 座位号（不是引擎 id）",
    seat1.text().includes("群") && seat1.text().includes("吕布") && seat1.text().includes("手牌 4")
      && seat1.text().includes("座位 1") && !seat1.text().includes("lvbu"),
    `seat1="${seat1.text().replace(/\s+/g, " ").trim()}"`);

  check("U1-02g 体力 5/5 = 5 段 + 5/5 文本；上限 6 只显示 6/6 文本、不画方块",
    byClass(seat1, "hp-seg").length === 5
      && seat1.text().includes("5/5")
      && byClass(seat2, "hp-seg").length === 0
      && byClass(seat2, "hp-text").length === 1
      && seat2.text().includes("6/6"),
    `seat1 segs=${byClass(seat1, "hp-seg").length} seat2 segs=${byClass(seat2, "hp-seg").length} seat2="${seat2.text().replace(/\s+/g, " ").trim()}"`);

  check("U1-02h 判定区固定 3 槽（空槽暗色占位），装备四槽按武器 / 防具 / 攻马 / 防马",
    byClass(seat1, "judge-slot").length === 3
      && byClass(seat1, "judge-slot").filter((slot) => hasClass(slot, "empty")).length === 2
      && byClass(seat1, "equip-slot").length === 4
      && byClass(seat1, "equip-slot").map((slot) => attr(slot, "data-slot")).join(",")
        === "weapon,armor,horse_offense,horse_defense",
    `judge=${byClass(seat1, "judge-slot").length} equip=${byClass(seat1, "equip-slot").map((s) => attr(s, "data-slot")).join(",")}`);
  check("U1-02h2 槽里的牌画牌名；空槽画槽名（判定 / 武器 / 防具 / 攻马 / 防马）",
    seat1.text().includes("八卦阵") && seat1.text().includes("闪电")
      && seat2.text().includes("赤兔")
      && byClass(seat1, "equip-slot").filter((slot) => byClass(slot, "slot-k").length > 0)
        .every((slot) => /武器|防具|攻马|防马/.test(slot.text())),
    `seat1="${seat1.text().replace(/\s+/g, " ").trim()}"`);

  check("U1-02i 身份：暗置显示 ?、主公明置、阵亡亮明；自己恒可见（全称，`GLOSSARY「身份」`四值）",
    byClass(seat1, "identity-badge").some((b) => b.textContent === "?")
      && byClass(seat2, "identity-badge").some(
        (b) => b.textContent === "主公" && b.getAttribute("title") === "身份：主公")
      && byClass(seat3, "identity-badge").some(
        (b) => b.textContent === "忠臣" && b.getAttribute("title") === "身份：忠臣")
      && $("t-self-identity").textContent === "反贼"
      && $("t-self-identity").getAttribute("title") === "身份：反贼",
    `seat1=${byClass(seat1, "identity-badge").map((b) => b.textContent)} seat2=${byClass(seat2, "identity-badge").map((b) => b.textContent)} self="${$("t-self-identity").textContent}"`);

  check("U1-02i2 自己的武将块与 footer 读同一份展示契约：大乔 / 吴 / 反贼 / 3/3 / 手牌 2 / 座位 0",
    $("t-hero").textContent === "大乔" && $("t-self-kingdom").textContent === "吴"
      && $("t-self-hp").text().includes("3/3")
      && $("t-self-hand").textContent === "2" && $("t-self-seatno").textContent === "0",
    `hero=${$("t-hero").textContent} kingdom=${$("t-self-kingdom").textContent} hp=${$("t-self-hp").text()}`);

  check("U1-02j 横置 = 中央虚线、酒 = 泛红（无「酒」字）、阵亡 = 蒙灰 + 大号「阵亡」",
    seat2.classList.contains("chained") && byClass(seat2, "chain-line").length === 1
      && seat2.classList.contains("wine") && !seat2.text().includes("酒")
      && seat3.classList.contains("dead") && byClass(seat3, "dead-tag").some((t) => t.textContent === "阵亡"),
    `seat2=${[...seat2.classList.set].join(",")} seat3=${[...seat3.classList.set].join(",")}`);
  check("U1-02j2 三态叠加不互相遮挡：同一个座位同时有铁索线、「阵亡」牌与酒的红色",
    seat3.classList.contains("chained") && seat3.classList.contains("dead")
      && seat3.classList.contains("wine")
      && byClass(seat3, "chain-line").length === 1
      && byClass(seat3, "dead-tag").length === 1
      && byClass(seat3, "dead-text").length === 1
      && !/\.seat\.dead\s*\{[^}]*filter:\s*grayscale/s.test(css),
    `seat3=${[...seat3.classList.set].join(",")}`);

  /* ---- 高亮：当前回合金框行动中 / 合法目标可指定 / 已选内圈 + 勾 ---- */
  check("U1-02k 当前回合金框 + 「行动中」（还没点牌时没有可指定的座位）",
    seat2.classList.contains("turn") && !seat2.classList.contains("targetable")
      && seat2.text().includes("行动中") && !seat2.text().includes("可指定"),
    `seat2="${seat2.text().replace(/\s+/g, " ").trim()}"`);
  click($("t-hand").children[0]);
  check("U1-02k2 当前回合 + 合法目标：金框与绿圈同时成立，文字两行都写",
    seatBox(2).classList.contains("turn") && seatBox(2).classList.contains("targetable")
      && seatBox(2).text().includes("行动中") && seatBox(2).text().includes("可指定"),
    `seat2="${seatBox(2).text().replace(/\s+/g, " ").trim()}"`);
  check("U1-02l 点手牌 = 焦点 + 手牌画选中态（.sel）",
    String(ui.state.focusCardId) === "1" && $("t-hand").children[0].classList.contains("sel"),
    `focus=${ui.state.focusCardId} sel=${$("t-hand").children[0].classList.contains("sel")}`);
  check("U1-02m 用不出的牌置灰（.dis）",
    $("t-hand").children[1].classList.contains("dis"),
    `classes=${[...$("t-hand").children[1].classList.set].join(",")}`);
  click(seatBox(1));
  check("U1-02n 点座位 = 已选：.tsel + 绿内圈 + 勾「✓ 已选」",
    seatBox(1).classList.contains("tsel")
      && seatBox(1).text().includes("✓ 已选")
      && !seatBox(1).text().includes("可指定"),
    `seat1="${seatBox(1).text().replace(/\s+/g, " ").trim()}" targets=${JSON.stringify(ui.state.selectedTargets)} focus=${ui.state.focusCardId}`);

  // 弃牌面：点手牌 = **已选**态（绿框 + 勾），与焦点牌的红色 `.sel` 区分开。
  const discardView = table();
  discardView.pending = {
    kind: "DISCARD", actor: 0, seq: 50, timing: "PHASE_DISCARD",
    prompt: "弃牌阶段：请弃置 1 张手牌。", count: 1,
    legal_actions: [{ kind: "DiscardCards", answers_seq: 50, cards: [slash] }],
    candidate_index: {}, context: null, option_seats: {},
  };
  discardView.snapshot.self_hand = [slash, peach];
  show(discardView);
  click($("t-hand").children[0]);
  const pickedCard = $("t-hand").children[0];
  check("U1-02m2 弃牌面点牌 = 已选态（.picked + 勾；不是焦点 .sel）",
    pickedCard.classList.contains("picked") && !pickedCard.classList.contains("sel")
      && JSON.stringify(ui.state.picks) === "[1]"
      && /\.card\.face\.picked::after\s*\{[^}]*content:\s*"✓"/s.test(css),
    `classes=${[...pickedCard.classList.set].join(",")} picks=${JSON.stringify(ui.state.picks)}`);
  show(table());

  /* ---- 卡面：88×124、花色 + 点数、牌名、类型 / 属性 / 范围、悬停全文 ---- */
  const handCard = $("t-hand").children[0];
  check("U1-02o 卡面：花色 + 点数 + 牌名 + 底行（类型 / 属性 / 范围）",
    hasClass(handCard, "face")
      && byClass(handCard, "suit")[0].textContent === "♠"
      && byClass(handCard, "rank")[0].textContent === "7"
      && byClass(handCard, "cname")[0].textContent === "杀"
      && byClass(handCard, "ctype")[0].textContent === "普通",
    `html="${handCard.text()}"`);
  // 底行的三类事实：装备槽 / 属性 / 攻击范围（都来自服务端展示契约，不在 JS 里猜）。
  const fireSlash = card(-7, "火杀", {
    type: "fire_slash", display_name: "杀", attribute: "火焰", kind: "normal",
  });
  const bottomView = table();
  bottomView.snapshot.self_hand = [weapon, fireSlash];
  show(bottomView);
  const weaponFace = $("t-hand").children[0];
  const fireFace = $("t-hand").children[1];
  check("U1-02o3 卡面底行：武器 = 武器 · 范围 2；火杀 = 普通 · 火焰（红）",
    byClass(weaponFace, "ctype")[0].textContent === "武器 · 范围 2"
      && byClass(fireFace, "ctype")[0].textContent === "普通 · 火焰"
      && hasClass(fireFace, "attr-fire")
      && byClass(fireFace, "cname")[0].textContent === "杀",
    `weapon="${byClass(weaponFace, "ctype")[0].textContent}" fire="${byClass(fireFace, "ctype")[0].textContent}"`);
  show(table());

  const liveSeat1 = seatBox(1);
  check("U1-02o2 卡面悬停 title 读服务端描述（手牌 / 装备 / 判定 / 处理区同一份）",
    handCard.getAttribute("title") === slash.description
      && byClass(liveSeat1, "equip-slot").find((s) => attr(s, "data-card-id") === "22").getAttribute("title")
        === `八卦阵\n${armor.description}`
      && byClass(liveSeat1, "judge-slot").find((s) => attr(s, "data-card-id") === "24").getAttribute("title")
        === `闪电\n${lightning.description}`
      && byClass($("t-processing"), "card")[0].getAttribute("title") === lightning.description,
    `hand="${handCard.getAttribute("title")}"`);

  /* ---- 手牌与场上区域不重复显示 ---- */
  const handIds = $("t-hand").children.map((node) => attr(node, "data-card-id"));
  const fieldIds = byClass($("t-self-zones"), "mini-slot")
    .map((slot) => attr(slot, "data-card-id")).filter(Boolean);
  check("U1-02p 自己装备区里的一张牌（青釭剑）不在手牌区重复出现",
    handIds.join(",") === "1,4"
      && new Set(fieldIds).size === 2
      && fieldIds.includes("21") && fieldIds.includes("25")
      && !handIds.some((id) => fieldIds.includes(id)),
    `hand=${handIds.join(",")} field=${fieldIds.join(",")}`);

  /* ---- 无身份局：不画「?」 ---- */
  const twoPlayer = table(false);
  twoPlayer.player_count = 2;
  twoPlayer.heroes = ["daqiao", "lvbu"];
  twoPlayer.snapshot.players = twoPlayer.snapshot.players.slice(0, 2);
  show(twoPlayer);
  const noIdentity = [...$("t-seats").children, self]
    .flatMap((box) => byClass(box, "identity-badge"))
    .filter((badge) => !badge.classList.contains("none"));
  // 自己的徽标是 index.html 的静态节点（DOM 垫片不建父子关系），故按 id 直接断：
  // 无身份局里它必须是 `.none` 且没有文字。
  const selfIdentity = $("t-self-identity");
  check("U1-02q 2 人无身份局：座位上不画「?」（身份徽标只对身份局显示）",
    noIdentity.length === 0
      && selfIdentity.textContent === ""
      && /(^|\s)none(\s|$)/.test(selfIdentity.className),
    `badges=${noIdentity.map((b) => b.textContent).join(",")} self="${selfIdentity.textContent}" selfClass="${selfIdentity.className}"`);

  /* ---- 真会话载荷：kingdom / kingdom_label 的读取方不是只靠合成形状自证 ---- */
  show(created);
  const realOpponent = created.snapshot.players[1];
  const realBox = seatBox(realOpponent.seat);
  check("U1-02s 真会话载荷：座位卡读 hero_name + kingdom_label（服务端已下发）",
    Boolean(realOpponent.hero_name && realOpponent.kingdom_label && realBox)
      && realBox.text().includes(realOpponent.hero_name)
      && realBox.text().includes(realOpponent.kingdom_label)
      && $("t-self-kingdom").textContent === created.snapshot.players[0].kingdom_label,
    `hero=${realOpponent.hero_name} kingdom=${realOpponent.kingdom_label} selfKingdom=${$("t-self-kingdom").textContent}`);

  /* ---- turn_seat = null（回合之外 / 终局）不得把 0 号位误标成行动中 ---- */
  const noTurn = table();
  noTurn.snapshot.turn_seat = null;
  show(noTurn);
  check("U1-02r turn_seat=null 时不误标「行动中」",
    !$("t-self-seat").classList.contains("turn")
      && !$("t-self-seat").text().includes("行动中")
      && $("t-seats").children.every((box) => !box.classList.contains("turn")),
    `selfTurn=${$("t-self-seat").classList.contains("turn")} ring=${$("t-seats").children.map((b) => [...b.classList.set].join("+")).join(",")}`);

  ui.state.view = savedView;
  ui.state.logRecords = savedLog;
  ui.resetSelection();
  ui.renderTable();
}


/* ============ U1-04 展示契约：前端读服务端的 display_name（不再自己映射） ============
 *
 * 服务端下发 `display_name`（火杀 / 雷杀都叫「杀」）与 `kind` / `attribute` / `source_cards`；
 * 界面只读这些键。这里喂一张**转化火杀**，断言手牌与候选文案都显示「杀」而不是「火杀」。
 */
{
  const saved = ui.state.view;
  const source = synthCard(2, "闪");
  const fire = {
    ...synthCard(-9, "火杀"),
    type: "fire_slash",
    virtual_sources: [2],
    display_name: "杀",
    attribute: "火焰",
    kind: "transformed",
    description: "【杀】的一种，造成火焰伤害。本仓裁定：卡面显示为「杀」并标红色。",
    source_cards: [{ ...source, display_name: "闪", attribute: "普通", kind: "normal", source_cards: [] }],
  };
  // 真实载荷的形状：`legal_actions` 是可回传的**裸**卡（Action extra=forbid，不能带
  // display_*）；展示字段只在只读载体（快照 / `candidate_index`）上。
  const fireRaw = { ...synthCard(-9, "火杀"), type: "fire_slash", virtual_sources: [2] };
  const firePending = {
    kind: "PLAY_PHASE", actor: 0, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ answers_seq: 1, kind: "UseCard", card: fireRaw, targets: [1] }],
    candidate_index: {
      "-9": { card: fire, pool: [1], combos: [[1]], min_targets: 1, max_targets: 1 },
    },
    context: null, option_seats: {},
  };
  const view = synthView(firePending);
  view.snapshot.self_hand = [fire];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const chipText = $("t-hand").children[0].text();
  check("U1-04a 手牌卡名读 display_name（转化火杀显示「杀」）",
    chipText.includes("杀") && !chipText.includes("火杀"), `手牌="${chipText}"`);
  check("U1-04d 牌面悬停 title 读服务端下发的 description（转化牌另外列出源牌）",
    $("t-hand").children[0].getAttribute("title").includes(fire.description),
    `title="${$("t-hand").children[0].getAttribute("title")}"`);
  // 候选里的卡是裸卡：文案按 card_id 回查 candidate_index 的展示副本才拿到「杀」。
  const label = ui.actionLabel({ kind: "UseCard", card: fireRaw, targets: [1] });
  check("U1-04b 候选文案按 card_id 回查 display_name（不是「火杀」）",
    !fireRaw.display_name && label.includes("杀") && !label.includes("火杀"),
    `raw=${fireRaw.display_name} label="${label}"`);

  // 技能按钮悬停（U1-04b）：与牌面同一份服务端 description，不在 JS 里另抄。
  const skillView = synthView({
    kind: "PLAY_PHASE", actor: 0, prompt: "", legal_actions: [],
    candidate_index: {}, context: null, option_seats: {},
  });
  skillView.skills = [{
    skill_id: "hero.guojia.yiji",
    name: "遗计",
    skill_type: "NORMAL",
    locked: false,
    carrier: "HERO",
    available: false,
    preactivated: false,
    description: "当你每受到 1 点伤害后，你可以观看牌堆顶的两张牌，然后交给任意角色。",
    ui: { requirement: "", passive: true, convert_sources: [] },
  }];
  ui.state.view = skillView;
  ui.resetSelection();
  ui.renderTable();
  check("U1-04e 技能按钮悬停 title 读服务端下发的 description",
    $("t-skills").children[0].getAttribute("title") === skillView.skills[0].description,
    `title="${$("t-skills").children[0].getAttribute("title")}"`);

  // 座位身份标签：服务端 identity_label 优先于本地 identityLabel 映射（U1-04）。
  const identityView = synthView({
    kind: "PLAY_PHASE", actor: 0, prompt: "", legal_actions: [],
    candidate_index: {}, context: null, option_seats: {},
  });
  identityView.snapshot.players[1].identity = "lord";
  identityView.snapshot.players[1].identity_label = "主公X";
  ui.state.view = identityView;
  ui.resetSelection();
  ui.renderTable();
  check("U1-04c 座位身份标签读服务端 identity_label",
    seatBox(1).text().includes("主公X"), `seat1="${seatBox(1).text()}"`);

  ui.state.view = saved;
  ui.resetSelection();
  ui.renderTable();
}

/* ============ U1-03：提示条 / 倒计时 / 选项行 / 模态 ============
 *
 * 判据来自票面 `03` 与 spec §3.3 / §3.4：
 * - 自己的提示条在手牌区正上方、只横跨手牌区；其他座位的询问只在**其武将块下方**
 *   显示文本，不给可点按钮、不给倒计时；
 * - 简单询问走浮动选项行；提交 / 取消 / 结束出牌 / 放弃在提示条右端；
 * - 倒计时共用 lobby timeout，进度条 + 剩余秒 + 默认动作，绿 / 黄 / 红、最后 5 秒闪烁；
 * - 模态：正中 / 可滚 / 背景与 ESC 可关 / 打开时背景不可操作 / 不暂停倒计时且内部不显示。
 *
 * 这里先钉**归属**（自己 vs 其他座位）与静态骨架；颜色 / 模态开关在同文件后面的
 * U1-03 小节续钉。
 */

{
  const handAreaAt = html.indexOf('class="hand-area"');
  const bottomAt = html.indexOf('id="table-bottom"');
  const promptbarAt = html.indexOf('id="t-promptbar"');
  const handAt = html.indexOf('id="t-hand"');
  check("U1-03a 提示条在手牌区正上方、只横跨手牌区（在 .hand-area 内、#t-hand 之前）",
    bottomAt >= 0 && bottomAt < handAreaAt && handAreaAt < promptbarAt && promptbarAt < handAt,
    `bottom=${bottomAt} handArea=${handAreaAt} promptbar=${promptbarAt} hand=${handAt}`);
  check("U1-03b 提示条右端有 提交 / 取消 / 结束出牌 / 放弃 四个动作位",
    ['btn-submit', 'btn-cancel', 'btn-end-phase', 'btn-pass']
      .every((id) => html.includes(`id="${id}"`)),
    `submit=${html.includes('id="btn-submit"')} cancel=${html.includes('id="btn-cancel"')} end=${html.includes('id="btn-end-phase"')} pass=${html.includes('id="btn-pass"')}`);
  check("U1-03b2 时长进度条与默认动作文案的容器存在",
    html.includes('id="t-countdown"') && html.includes('id="t-timer-fill"')
      && html.includes('id="t-default-label"'),
    `countdown=${html.includes('id="t-countdown"')} fill=${html.includes('id="t-timer-fill"')} label=${html.includes('id="t-default-label"')}`);
}

{
  const pending = {
    kind: "RESPOND", actor: 1, seq: 9001, prompt: "响应窗口：请打出一张牌，或放弃。",
    prompt_display: { text: "响应窗口：请打出一张牌，或放弃。", default_label: "超时将自动放弃", default_action: { kind: "Pass", answers_seq: 9001 } },
    legal_actions: [{ kind: "Pass", answers_seq: 9001 }],
    candidate_index: {}, option_seats: {}, context: null,
  };
  const view = synthView(pending);
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  const foreign = seatBox(1) && seatBox(1).children.find(
    (node) => (node.className || "").includes("seat-prompt"),
  );
  check("U1-03c 其他座位的询问文本显示在其武将块下方（只有“正在响应…”，不复述询问内容）",
    !!foreign && foreign.textContent.includes("正在响应")
      && !foreign.textContent.includes("响应窗口"),
    `seat1="${seatBox(1) && seatBox(1).text()}"`);
  check("U1-03d 其他座位的提示不显示可点按钮、也不接管自己的提示条动作",
    !!foreign
      && foreign.children.every((node) => node.tagName !== "BUTTON")
      && !foreign.onclick
      && $("btn-submit").disabled === true
      && $("btn-cancel").disabled === true
      && $("btn-pass").hidden === true,
    `children=${foreign && foreign.children.map((node) => node.tagName).join(",")} submit=${$("btn-submit").disabled}`);
  check("U1-03e 其他座位的询问不铺选项行、不显示倒计时",
    $("t-options").children.length === 0
      && ($("t-countdown").hidden === true || $("t-timer").textContent === "")
      && $("t-prompt").textContent.includes("等待其他座位响应"),
    `options=${$("t-options").children.length} timer="${$("t-timer").textContent}" prompt="${$("t-prompt").textContent}"`);
}

{
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9002,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段", default_action: { kind: "EndPhase", answers_seq: 9002 } },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9002 }],
    candidate_index: {}, option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.timeout_seconds = 60;
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  check("U1-03f 自己的询问在提示条里，不在座位块下方",
    $("t-prompt").textContent.includes("出牌阶段")
      && !(seatBox(0) && seatBox(0).children.some(
        (node) => (node.className || "").includes("seat-prompt"))),
    `prompt="${$("t-prompt").textContent}"`);
}

/* 查看者座位缺失（坏报文 / 未来的观战载荷）：**fail-closed**，不认作自己的询问。 */
{
  const view = synthView({
    kind: "RESPOND", actor: 0, seq: 9005, prompt: "响应窗口：请打出一张牌，或放弃。",
    prompt_display: { text: "响应窗口：请打出一张牌，或放弃。", default_label: "超时将自动放弃" },
    legal_actions: [{ kind: "Pass", answers_seq: 9005 }],
    candidate_index: {}, option_seats: {}, context: null,
  });
  delete view.snapshot.seat;
  view.human_seat = null;
  view.timeout_seconds = 60;
  ui.state.view = view;
  ui.resetSelection();
  ui.renderPending(view);
  check("U1-03f2 查看者座位缺失 ⇒ fail-closed（不铺选项 / 右端动作 / 倒计时）",
    $("t-options").children.length === 0
      && $("btn-pass").hidden === true
      && $("btn-submit").disabled === true
      && $("t-countdown").hidden === true
      && $("t-prompt").textContent.includes("等待其他座位响应"),
    `options=${$("t-options").children.length} passHidden=${$("btn-pass").hidden} submit=${$("btn-submit").disabled} countdown=${$("t-countdown").hidden} prompt="${$("t-prompt").textContent}"`);
}

/* ---- 简单询问：浮动选项行（是否发动 / 一次性二选一） ----
 *
 * 票 08 起花色是**复用枚举**，走选择型弹窗（服务端下发 `option_labels`），不再占这条
 * 浮动选项行；这里保留的是"≤3 且一次性"的普通选项面（用非枚举值当样本）。
 */

{
  const pending = {
    kind: "SELECT_OPTION", actor: 0, seq: 9004, prompt: "请选择一项。",
    prompt_display: { text: "请选择一项。", default_label: "超时将自动选择第一项" },
    legal_actions: [
      { kind: "SelectOption", choice: "alpha", answers_seq: 9004 },
      { kind: "SelectOption", choice: "beta", answers_seq: 9004 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
  };
  const view = synthView(pending);
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  const options = $("t-options").children;
  check("U1-03g0 简单询问（一次性二选一）出浮动选项行，且回到提示条内",
    options.length === 2
      && $("t-options-home").children.includes($("t-options")),
    `options=${options.length} home=${$("t-options-home").children.includes($("t-options"))}`);
  (options[0].listeners.click || []).forEach((fn) => fn({ target: options[0] }));
  const picked = ui.buildAction();
  check("U1-03g1 点浮动选项 = 选中候选里的那条 SelectOption（提交仍由玩家按）",
    !!picked && picked.kind === "SelectOption" && picked.choice === "alpha"
      && $("btn-submit").disabled === false,
    JSON.stringify(picked));
}

/* ---- 右端动作：结束出牌 / 放弃 是候选里的原动作，不在浮动选项行里 ---- */

{
  const slash = { ...synthCard(84, "杀"), type: "slash" };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9003,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [
      { answers_seq: 9003, kind: "UseCard", card: slash, targets: [] },
      { answers_seq: 9003, kind: "EndPhase" },
    ],
    candidate_index: {
      84: { card: slash, pool: [], min_targets: 0, max_targets: 0, combos: [[]] },
    },
    option_seats: {}, context: null,
  };
  const view = synthView(pending);
  view.snapshot.self_hand = [slash];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  check("U1-03aa 结束出牌在提示条右端，不出现在浮动选项行",
    $("btn-end-phase").hidden === false
      && $("btn-pass").hidden === true
      && !$("t-options").children.some((node) => /结束出牌/.test(node.textContent || "")),
    `endHidden=${$("btn-end-phase").hidden} passHidden=${$("btn-pass").hidden} options=${JSON.stringify($("t-options").children.map((node) => node.textContent))}`);
  const savedSession = ui.state.sessionId;
  ui.state.sessionId = null;
  const before = calls.length;
  await clickAndWait($("btn-end-phase"));
  check("U1-03ab 点右端「结束出牌」= 选中候选里的那条 EndPhase（不自己拼 Action、合成态不提交）",
    (() => {
      const action = ui.buildAction();
      return !!action && action.kind === "EndPhase" && $("btn-submit").disabled === false;
    })() && calls.length === before,
    `action=${JSON.stringify(ui.buildAction())} calls=${calls.length - before}`);
  // U2-03：右端动作**不重绘**内容区——点完「结束出牌」不应铺出任何候选 / 选项。
  check("U1-03ab2 点右端动作不重绘内容区（选项行仍为空、无候选按钮）",
    $("t-options").children.length === 0 && candidateRowButtons().length === 0,
    `options=${JSON.stringify($("t-options").children.map((node) => node.textContent))}`);
  ui.state.sessionId = savedSession;
}

/* ============ U2-03：删除候选按钮行 + 右端动作「一击即弃」 ============
 *
 * 判据（票面验收 / spec D5、Problem 3）：
 * - 任何询问、任何点击序列下都不再有逐条候选按钮（含点过技能 / 选项之后）；
 * - `SELECT_OPTION` 非座位形态 = 一选项一个按钮；技能候选 = 直提态「将提交：发动【技能名】」；
 * - 「放弃」一击 = 一次 POST：提交前不重绘、在飞时四键全禁用、被拒（400 / 409）回到点击前
 *   那一面（409 可刷新到新询问，但不留下展开的选项）；
 * - 没有合法 `Pass` 的询问不显示「放弃」（弃牌 / 五谷 / 观星排列 / 反间花色），
 *   `EndPhase` 与它相互独立；没有专用面的询问 fail-closed。
 */

/** 四键当前态（disabled / hidden / 文案）：冻结 / 恢复的逐字段快照。 */
const actionButtonsState = () => ["btn-submit", "btn-cancel", "btn-end-phase", "btn-pass"]
  .map((id) => {
    const button = $(id);
    return [id, button.disabled, button.hidden, button.textContent];
  });

/* ---- ① `SELECT_OPTION` 非座位形态：一选项一个按钮，不是候选行 ---- */
{
  // 样本用非枚举值：花色自票 08 起是**复用枚举**，走选择型弹窗（U2-08 段）。
  const pending = {
    kind: "SELECT_OPTION", actor: 0, seq: 9401, prompt: "请选择一项。",
    prompt_display: { text: "请选择一项。", default_label: "超时将自动选择第一项" },
    legal_actions: [
      { kind: "SelectOption", choice: "alpha", answers_seq: 9401 },
      { kind: "SelectOption", choice: "beta", answers_seq: 9401 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  };
  const view = synthView(pending);
  ui.state.view = view;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  const buttons = $("t-options").children;
  check("U2-03a SELECT_OPTION 非座位形态 = 专用小选项按钮组（原值、无「选项：」前缀）",
    ui.renderKind(pending, pending.legal_actions) === "options"
      && buttons.length === 2
      && buttons.map((node) => node.textContent).join(",") === "alpha,beta"
      && !buttons.some((node) => /选项：/.test(node.textContent || "")),
    `kind=${ui.renderKind(pending, pending.legal_actions)} 选项=${JSON.stringify(buttons.map((node) => node.textContent))}`);
  check("U2-03a2 选项面没有候选按钮；点一个选项 = 内部选中 + 提交可点",
    (() => {
      (buttons[0].listeners.click || []).forEach((fn) => fn({ target: buttons[0] }));
      return candidateRowButtons().length === 0
        && ui.state.selectedOption === 0
        && $("btn-submit").disabled === false
        && $("t-options").children.length === 2;
    })(),
    `selectedOption=${ui.state.selectedOption} submit=${$("btn-submit").disabled} options=${$("t-options").children.length}`);
}

/* ---- ② 技能候选的直提态：B 形「是否发动」与点过底部技能按钮之后 ---- */
{
  const pending = {
    kind: "SKILL_ACTIVATION", actor: 0, seq: 9402, skill_id: "hero.caocao.jianxiong",
    prompt: "是否发动【奸雄】？",
    prompt_display: { text: "是否发动【奸雄】？", default_label: "超时将自动放弃" },
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "hero.caocao.jianxiong", answers_seq: 9402, picks: [], choices: [] },
      { kind: "Pass", answers_seq: 9402 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  };
  const view = synthView(pending);
  view.skills = [{
    skill_id: "hero.caocao.jianxiong", name: "奸雄", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: false,
    ui: { passive: true, requirement: "", convert_sources: [] },
  }];
  ui.state.view = view;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  const index = pending.legal_actions.findIndex((action) => action.kind === "ActivateSkill");
  const built = ui.buildAction();
  check("U2-03b B 形「是否发动」= 直提态：中文技能名、无候选行、「放弃」仍独立出现",
    ui.renderKind(pending, pending.legal_actions) === "skill"
      && $("t-options").children.length === 0
      && candidateRowButtons().length === 0
      && $("t-choice").textContent.includes("将提交：发动【奸雄】")
      && ui.state.selectedOption === index
      && $("btn-submit").disabled === false
      && $("btn-pass").hidden === false,
    `kind=${ui.renderKind(pending, pending.legal_actions)} choice="${$("t-choice").textContent}" option=${ui.state.selectedOption} submit=${$("btn-submit").disabled} passHidden=${$("btn-pass").hidden}`);
  check("U2-03b2 直提态提交的正是候选里的那条 ActivateSkill（不拼 Action）",
    !!built && built.kind === "ActivateSkill" && built.skill_id === "hero.caocao.jianxiong"
      && pending.legal_actions.includes(built),
    JSON.stringify(built));
}

{
  const activeSkill = {
    skill_id: "hero.demo.active", name: "示例主动", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: false,
    ui: { passive: false, requirement: "", convert_sources: [] },
  };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9403,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "hero.demo.active", answers_seq: 9403, picks: [], choices: [] },
      { kind: "EndPhase", answers_seq: 9403 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  };
  const view = synthView(pending);
  view.skills = [activeSkill];
  ui.state.view = view;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  const skillButton = $("t-skills").children.find(
    (node) => node.getAttribute("data-skill-id") === "hero.demo.active");
  check("U2-03c 点过底部技能按钮 = 直提态（不再铺「发动技能 hero.xxx」候选行）",
    (() => {
      if (!skillButton) return false;
      (skillButton.listeners.click || []).forEach((fn) => fn({ target: skillButton }));
      return ui.renderKind(pending, pending.legal_actions) === "skill"
        && $("t-options").children.length === 0
        && candidateRowButtons().length === 0
        && $("t-choice").textContent.includes("将提交：发动【示例主动】")
        && $("btn-submit").disabled === false;
    })(),
    `kind=${ui.renderKind(pending, pending.legal_actions)} choice="${$("t-choice").textContent}" options=${JSON.stringify($("t-options").children.map((node) => node.textContent))}`);
}

/* ---- ③ 强制询问不出现「放弃」；EndPhase 与它相互独立 ---- */
{
  const forcedCases = [
    ["弃牌", {
      kind: "DISCARD", actor: 0, seq: 9404, count: 1,
      prompt: "弃牌阶段：请弃置 1 张手牌。",
      legal_actions: [{ kind: "DiscardCards", answers_seq: 9404, cards: [synthCard(1, "杀")] }],
      candidate_index: {}, option_seats: {}, context: null,
      preactivations: {}, preactivation: [],
    }],
    ["五谷", {
      kind: "SELECT_CARDS", actor: 0, seq: 9405,
      prompt: "请从选择面里选择一张牌。",
      legal_actions: [{ kind: "SelectCards", answers_seq: 9405, picks: [0] }],
      // U2-11：五谷取牌已是**亮牌弹窗**（公开处理区），不再是旧的选择面块；本用例钉
      // 新表面同样没有「放弃」。目标条 / 置灰由 U2-11 段单独钉。
      candidate_index: {}, option_seats: {},
      context: {
        actor: 0, name: "五谷丰登", targets: [0], label: "0号位使用五谷丰登",
        requirement: "从亮出的牌里选一张获得", skill_hints: [],
      },
      preactivations: {}, preactivation: [],
      spec: { zones: ["processing"], total: [1, 1] },
      slots: [{ ref: { zone: "processing", seat: null }, card: synthCard(2, "桃") }],
      harvest: {
        active: true, key: "harvest:1:5:0", title: "五谷丰登",
        hint: "从亮出的牌里选一张获得", source: 0,
        revealed: [synthCard(2, "桃")],
        targets: [{ seat: 0, status: "current", card: null, cancelled: false, reason: null }],
        current: 0,
      },
    }],
    ["观星排列", {
      kind: "ARRANGE", actor: 0, seq: 9406,
      prompt: "请排列这些牌。",
      legal_actions: [{ kind: "ArrangeCards", answers_seq: 9406, order: [0], split: 1 }],
      candidate_index: {}, option_seats: {}, context: null,
      preactivations: {}, preactivation: [],
      revealed: [synthCard(3, "闪")],
      arrange: { count: 1, split: "free" },
    }],
    ["反间花色", {
      kind: "SELECT_OPTION", actor: 0, seq: 9407,
      prompt: "请选择一种花色。",
      options: ["heart", "diamond", "spade", "club"],
      option_labels: {
        heart: { name: "红桃", symbol: "♥", color: "red" },
        diamond: { name: "方块", symbol: "♦", color: "red" },
        spade: { name: "黑桃", symbol: "♠", color: "black" },
        club: { name: "梅花", symbol: "♣", color: "black" },
      },
      // 真引擎在这一步给了一条 Pass（流水线语义的"取消这一步"）；界面必须把它藏起来。
      legal_actions: [
        { kind: "SelectOption", choice: "heart", answers_seq: 9407 },
        { kind: "SelectOption", choice: "diamond", answers_seq: 9407 },
        { kind: "SelectOption", choice: "spade", answers_seq: 9407 },
        { kind: "SelectOption", choice: "club", answers_seq: 9407 },
        { kind: "Pass", answers_seq: 9407 },
      ],
      candidate_index: {}, option_seats: {}, context: null,
      preactivations: {}, preactivation: [],
    }],
  ];
  let forcedFailures = [];
  for (const [name, pending] of forcedCases) {
    const view = synthView(pending);
    ui.state.view = view;
    ui.state.sessionId = null;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
    if ($("btn-pass").hidden !== true || $("btn-end-phase").hidden !== true) {
      forcedFailures.push(`${name}: passHidden=${$("btn-pass").hidden} endHidden=${$("btn-end-phase").hidden}`);
    }
    if (candidateRowButtons().length) {
      forcedFailures.push(`${name}: 候选行 ${JSON.stringify(candidateRowButtons().map((node) => node.textContent))}`);
    }
  }
  check("U2-03d 强制询问（弃牌 / 五谷 / 观星排列 / 反间花色）不显示「放弃」",
    forcedFailures.length === 0, forcedFailures.join("；"));

  const endOnly = {
    kind: "PLAY_PHASE", actor: 0, seq: 9408,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 9408 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  };
  ui.state.view = synthView(endOnly);
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ui.state.view);
  check("U2-03d2 EndPhase 独立出现：只有结束出牌、没有放弃",
    $("btn-end-phase").hidden === false && $("btn-pass").hidden === true,
    `endHidden=${$("btn-end-phase").hidden} passHidden=${$("btn-pass").hidden}`);

  const both = { ...endOnly, seq: 9409, legal_actions: [
    { kind: "EndPhase", answers_seq: 9409 },
    { kind: "Pass", answers_seq: 9409 },
  ] };
  ui.state.view = synthView(both);
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ui.state.view);
  check("U2-03d3 两条都合法时各自出现（互不遮蔽）",
    $("btn-end-phase").hidden === false && $("btn-pass").hidden === false,
    `endHidden=${$("btn-end-phase").hidden} passHidden=${$("btn-pass").hidden}`);
}

/* ---- ④ 没有专用面的询问：fail-closed（明话 + 零按钮） ---- */
{
  const pending = {
    kind: "FUTURE_KIND", actor: 0, seq: 9410, prompt: "未来的询问。",
    legal_actions: [
      { kind: "SomethingNew", answers_seq: 9410 },
      { kind: "AnotherNew", answers_seq: 9410 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  };
  ui.state.view = synthView(pending);
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ui.state.view);
  check("U2-03e 没有专用面的询问 fail-closed：明话 + 零内容按钮 + 无右端动作",
    ui.renderKind(pending, pending.legal_actions) === "unsupported"
      && $("t-options").children.length === 0
      && $("t-choice").textContent.includes("这一步没有可用的界面")
      && $("btn-submit").disabled === true
      && $("btn-cancel").disabled === true
      && $("btn-end-phase").hidden === true
      && $("btn-pass").hidden === true,
    `kind=${ui.renderKind(pending, pending.legal_actions)} choice="${$("t-choice").textContent}" options=${$("t-options").children.length}`);
}

/* ---- ⑤ 「放弃」一击即弃：不重绘 / 在飞冻结 / 400 回到点击前那一面 ---- */
{
  // 合成载荷 + 真会话 id：400 由拦截器注入，请求不会真的走到引擎。
  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });
  const slash = { ...synthCard(84, "杀"), type: "slash" };
  const pending = {
    kind: "RESPOND", actor: 0, seq: 9411, cause: "BARBARIAN_INVASION", required: "slash",
    prompt: "响应窗口：请打出一张牌，或放弃。",
    prompt_display: { text: "响应窗口：请打出一张牌，或放弃。", default_label: "超时将自动放弃" },
    legal_actions: [
      { kind: "UseCard", answers_seq: 9411, card: slash, targets: [] },
      { kind: "Pass", answers_seq: 9411 },
    ],
    candidate_index: {
      84: { card: slash, pool: [], min_targets: 0, max_targets: 0, combos: [[]] },
    },
    option_seats: {}, context: null, preactivations: {}, preactivation: [],
  };
  const view = synthView(pending);
  view.snapshot.self_hand = [slash];
  ui.state.view = view;
  ui.state.sessionId = session.session_id;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  ui.toggleFocus(84);   // 点击前那一面：焦点在【杀】上、提交可点
  const beforeState = actionButtonsState();
  const beforeOptions = $("t-options").children.length;
  const beforeCalls = calls.length;
  staleAnswerStatus = 400;
  const clickPromise = ($("btn-pass").listeners.click || [])[0]({ target: $("btn-pass") });
  check("U2-03f 在飞期间提交 / 取消 / 结束出牌 / 放弃全部禁用",
    ["btn-submit", "btn-cancel", "btn-end-phase", "btn-pass"]
      .every((id) => $(id).disabled === true),
    ["btn-submit", "btn-cancel", "btn-end-phase", "btn-pass"]
      .map((id) => `${id}=${$(id).disabled}`).join(" "));
  check("U2-03g 点「放弃」不展开任何候选 / 选项（内容区原样）",
    $("t-options").children.length === beforeOptions
      && candidateRowButtons().length === 0,
    `before=${beforeOptions} now=${$("t-options").children.length} 候选行=${candidateRowButtons().length}`);
  await clickPromise;
  staleAnswerStatus = null;
  nextGetPending = null;
  const answerCalls = calls.slice(beforeCalls).filter((call) => call.path.includes("/answer"));
  check("U2-03h 「放弃」只发一次提交，送出的正是那条 Pass",
    answerCalls.length === 1 && answerCalls[0].body.action.kind === "Pass",
    `calls=${calls.slice(beforeCalls).map((call) => call.path).join(" | ")}`);
  check("U2-03i 400 被拒后回到点击前那一面（焦点 / 选中 / 四键 / 内容区全恢复）",
    ui.state.focusCardId === 84
      && ui.state.selectedOption === null
      && JSON.stringify(actionButtonsState()) === JSON.stringify(beforeState)
      && $("t-options").children.length === beforeOptions
      && /提交被拒/.test($("t-choice").textContent),
    `focus=${ui.state.focusCardId} option=${ui.state.selectedOption} buttons=${JSON.stringify(actionButtonsState())} choice="${$("t-choice").textContent}"`);
  ui.state.sessionId = null;
  ui.resetSelection();
}

/* ---- ⑤b 回归（U2-03 的接线 bug）：真实点击 `#btn-submit` 时 MouseEvent 不能冒充选择快照 ---- */
{
  // 真实浏览器里点 `<button>` 会把 MouseEvent 传进监听器。`submitAction(restoreSelection)`
  // 的形参是**点击前的选择快照**；若 main.js 直接挂 `submitAction`，400 回滚会把 MouseEvent
  // 当快照读 `selectedTargets` ⇒ 抛异常、四键停在冻结态。这里直接调 wire() 挂的监听器，
  // 复现"真实点击"的实参形状（合成载荷 + 真会话 id，400 由拦截器注入）。
  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });
  const slash = { ...synthCard(84, "杀"), type: "slash" };
  const pending = {
    kind: "RESPOND", actor: 0, seq: 9413, cause: "BARBARIAN_INVASION", required: "slash",
    prompt: "响应窗口：请打出一张牌，或放弃。",
    prompt_display: { text: "响应窗口：请打出一张牌，或放弃。", default_label: "超时将自动放弃" },
    legal_actions: [
      { kind: "UseCard", answers_seq: 9413, card: slash, targets: [] },
      { kind: "Pass", answers_seq: 9413 },
    ],
    candidate_index: {
      84: { card: slash, pool: [], min_targets: 0, max_targets: 0, combos: [[]] },
    },
    option_seats: {}, context: null, preactivations: {}, preactivation: [],
  };
  const view = synthView(pending);
  view.snapshot.self_hand = [slash];
  ui.state.view = view;
  ui.state.sessionId = session.session_id;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  ui.toggleFocus(84);   // 点击前那一面：焦点在【杀】上、提交可点
  const beforeState = actionButtonsState();
  const beforeCalls = calls.length;
  staleAnswerStatus = 400;
  let clickError = null;
  try {
    for (const fn of $("btn-submit").listeners.click || []) {
      await fn({ target: $("btn-submit"), type: "click" });
    }
  } catch (error) {
    clickError = error;
  }
  staleAnswerStatus = null;
  nextGetPending = null;
  const answerCalls = calls.slice(beforeCalls).filter((call) => call.path.includes("/answer"));
  check("U2-03m 真实点击「提交」被 400 拒：1 条 POST、不抛异常、四键回到点击前、错误文案可见",
    clickError === null
      && answerCalls.length === 1
      && JSON.stringify(actionButtonsState()) === JSON.stringify(beforeState)
      && ui.state.focusCardId === 84
      && /提交被拒/.test($("t-choice").textContent),
    `error=${clickError && clickError.message} answers=${answerCalls.length} buttons=${JSON.stringify(actionButtonsState())} choice="${$("t-choice").textContent}"`);
  ui.state.sessionId = null;
  ui.resetSelection();
}

/* ---- ⑥ 「放弃」成功 = 一次 POST + 新询问；409 = 刷新到新询问、不留展开选项 ---- */
{
  // 8 人 / heuristic / 该 seed 的建局首问 = 只有一条 Pass 的响应窗口（实测）。
  const fresh = await post("/api/sessions", {
    player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });
  await openSession(fresh);
  ui.renderPending(ui.state.view);
  const beforeCalls = calls.length;
  const successPromise = ($("btn-pass").listeners.click || [])[0]({ target: $("btn-pass") });
  check("U2-03j 成功路径在飞时四键也冻结",
    ["btn-submit", "btn-cancel", "btn-end-phase", "btn-pass"]
      .every((id) => $(id).disabled === true));
  await successPromise;
  ui.flushPlayback();
  const successCalls = calls.slice(beforeCalls).filter((call) => call.path.includes("/answer"));
  check("U2-03k 成功路径「放弃」只发一次 POST，新询问面不出现候选行",
    successCalls.length === 1
      && successCalls[0].body.action.kind === "Pass"
      && candidateRowButtons().length === 0,
    `answers=${successCalls.length} options=${JSON.stringify($("t-options").children.map((node) => node.textContent))}`);

  // 409：下一次 /answer 回 409；刷新取回的新询问换成"只有 EndPhase"的出牌阶段。
  const fresh2 = await post("/api/sessions", {
    player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });
  await openSession(fresh2);
  ui.renderPending(ui.state.view);
  const nextPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9412,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9412 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  };
  const staleBefore = calls.length;
  staleAnswerStatus = 409;
  nextGetPending = nextPending;
  const stalePromise = ($("btn-pass").listeners.click || [])[0]({ target: $("btn-pass") });
  await stalePromise;
  staleAnswerStatus = null;
  nextGetPending = null;
  ui.flushPlayback();
  const staleCalls = calls.slice(staleBefore);
  check("U2-03l 409 = 一次提交 + 刷新到新询问，不留下展开的选项 / 候选行",
    staleCalls.filter((call) => call.path.includes("/answer")).length === 1
      && staleCalls.some((call) => !call.path.includes("/answer"))
      && !!ui.state.view.pending && ui.state.view.pending.seq === 9412
      && ui.state.view.pending.kind === "PLAY_PHASE"
      && ui.state.selectedOption === null
      && $("t-options").children.length === 0
      && candidateRowButtons().length === 0
      && $("btn-pass").hidden === true
      && $("btn-end-phase").hidden === false,
    `calls=${staleCalls.map((call) => call.path).join(" | ")} pending=${ui.state.view.pending && ui.state.view.pending.kind} seq=${ui.state.view.pending && ui.state.view.pending.seq} option=${ui.state.selectedOption} options=${JSON.stringify($("t-options").children.map((node) => node.textContent))} passHidden=${$("btn-pass").hidden} endHidden=${$("btn-end-phase").hidden}`);
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.state.view = null;
}

/* ============ U2-04：提示区技能按钮组 + 选项按钮组（left column） ============
 *
 * 判据（票面验收 / spec D5）：
 * - 询问里中文技能按钮可点，点后进入预发动清单 / 直提态 / 转化来源模式（与底部技能区
 *   同一动作，判据抽成一处 `skillOffer`）；被动 / 锁定技在组里但不可点；
 * - 多个技能同时可发动时并列、可换行、不重叠（flex 行 + wrap + gap；无绝对定位）；
 * - 雌雄双股剑的 `SELECT_OPTION` = 两个中文按钮（无手牌时引擎只发 `draw_one`，界面不补）；
 * - 鬼才改判窗口（`RESPOND` / `REJUDGE`，候选是手牌 `PlayCard`）只加**入口 + 说明**，
 *   选择仍是既有的「点手牌 → 提交」；清空零提交；
 * - 底部技能区三态 / 顺序 / 点击行为不变（U1-06 仍绿）。
 */

const promptSkillButtons = () => $("t-prompt-skills").children.filter(
  (node) => node.tagName === "BUTTON",
);
const promptSkillIds = () => promptSkillButtons().map(
  (node) => node.getAttribute("data-skill-id"),
);
const promptSkill = (id) => promptSkillButtons().find(
  (node) => node.getAttribute("data-skill-id") === id,
);
const clickNode = (node) => (node.listeners.click || []).forEach((fn) => fn({ target: node }));
const bottomSkillIds = () => $("t-skills").children.filter(
  (node) => node.tagName === "BUTTON",
).map((node) => node.getAttribute("data-skill-id"));

/* ---- ① 多个技能同时可发动：并列 / 换行 / 不重叠 + 点它 = 底部同一动作 ---- */
{
  const hand = synthCard(31, "杀");
  const manifest = [{
    name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
    pool: [{ when: [], options: ["31"] }],
  }];
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9420,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "hero.demo.alpha", answers_seq: 9420, picks: [], choices: [] },
      { kind: "ActivateSkill", skill_id: "hero.demo.beta", answers_seq: 9420, picks: [], choices: [] },
      { kind: "ActivateSkill", skill_id: "hero.demo.passive", answers_seq: 9420, picks: [], choices: [] },
      { kind: "ActivateSkill", skill_id: "hero.demo.locked", answers_seq: 9420, picks: [], choices: [] },
      { kind: "EndPhase", answers_seq: 9420 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
    preactivations: { "hero.demo.flow": manifest }, preactivation: [],
  };
  const skill = (id, name, extra = {}) => ({
    skill_id: id, name, skill_type: "NORMAL", locked: false, carrier: "HERO",
    available: false, preactivated: false,
    description: `${name}的描述。`, ui: { requirement: "", passive: false, convert_sources: [] },
    ...extra,
  });
  const view = synthView(pending);
  view.snapshot.self_hand = [hand];
  view.skills = [
    skill("hero.demo.alpha", "甲技", { available: true }),
    skill("hero.demo.beta", "乙技", { available: true }),
    skill("hero.demo.flow", "流程技", { available: true, preactivated: true }),
    skill("hero.demo.convert", "转化技", { ui: { requirement: "", passive: false, convert_sources: [31] } }),
    skill("hero.demo.passive", "被动技", { available: true, ui: { requirement: "", passive: true, convert_sources: [] } }),
    skill("hero.demo.locked", "锁定技", { available: true, locked: true }),
    skill("hero.demo.off", "不可用技"),
  ];
  ui.state.view = view;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const shown = "hero.demo.alpha,hero.demo.beta,hero.demo.flow,hero.demo.convert,hero.demo.passive,hero.demo.locked";
  check("U2-04a 提示区技能组 = 这条询问给了入口的技能（中文名、服务端顺序；不可用技不出现）",
    promptSkillIds().join(",") === shown
      && promptSkillButtons().every((node) => {
        const entry = view.skills.find((item) => item.skill_id === node.getAttribute("data-skill-id"));
        return !!entry && node.textContent === entry.name && node.parentNode === $("t-prompt-skills");
      })
      && !promptSkill("hero.demo.off"),
    `组=${JSON.stringify(promptSkillIds())} 文本=${JSON.stringify(promptSkillButtons().map((node) => node.textContent))}`);

  // CSS 是"并列 / 换行 / 不重叠"的机械判据（DOM 垫片没有布局引擎，与 U1-02c 同姿态）：
  // flex 容器逐项排布 + wrap 换行 + gap 留缝；没有绝对定位 / 负 margin 能把按钮叠起来。
  const groupCss = readFileSync("tools/sgs_web/static/style.css", "utf8");
  check("U2-04b CSS：技能按钮组横向并列、可换行、留 gap（flex 项不重叠、无绝对定位 / 负 margin）",
    /\.promptbar\s+\.prompt-skills\s*\{[^}]*display:\s*flex[^}]*flex-wrap:\s*wrap[^}]*gap:\s*calc\(6 \* var\(--dsu\)\)/s.test(groupCss)
      && /\.promptbar\s+\.prompt-skills\s+\.skill\s*\{[^}]*flex:\s*0 0 auto/s.test(groupCss)
      && !/\.promptbar\s+\.prompt-skills[^{]*\{[^}]*position:\s*absolute/s.test(groupCss)
      && !/\.promptbar\s+\.prompt-skills[^{]*\{[^}]*margin[^;}]*-\d/s.test(groupCss),
    `flex=${/\.promptbar\s+\.prompt-skills\s*\{[^}]*display:\s*flex/s.test(groupCss)} wrap=${/flex-wrap:\s*wrap/s.test(groupCss)}`);

  check("U2-04c 点提示区「甲技」= 与底部同一动作（直提态：选中那条 ActivateSkill + 「将提交：发动【甲技】」）",
    (() => {
      clickNode(promptSkill("hero.demo.alpha"));
      return ui.state.selectedOption === 0
        && ui.renderKind(pending, pending.legal_actions) === "skill"
        && $("t-choice").textContent.includes("将提交：发动【甲技】")
        && $("btn-submit").disabled === false
        && candidateRowButtons().length === 0;
    })(),
    `option=${ui.state.selectedOption} kind=${ui.renderKind(pending, pending.legal_actions)} choice="${$("t-choice").textContent}"`);

  check("U2-04d 点提示区预发动技能「流程技」= 进入清单（与底部入口一致、零提交）",
    (() => {
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      const before = calls.length;
      clickNode(promptSkill("hero.demo.flow"));
      return !!ui.state.preactivation
        && ui.state.preactivation.skillId === "hero.demo.flow"
        && /预发动【流程技】/.test($("t-prompt").textContent)
        && $("t-options").children.length > 0
        && calls.length === before;
    })(),
    `pre=${ui.state.preactivation && ui.state.preactivation.skillId} prompt="${$("t-prompt").textContent}" calls=${calls.length}`);

  check("U2-04e 点提示区转化技 = 切来源模式（再点退出，与底部入口一致）",
    (() => {
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      clickNode(promptSkill("hero.demo.convert"));
      const on = ui.state.convertSkill === "hero.demo.convert";
      clickNode(promptSkill("hero.demo.convert"));
      const off = ui.state.convertSkill === null;
      return on && off;
    })(),
    `convertSkill=${ui.state.convertSkill}`);

  check("U2-04f 被动 / 锁定技在组里但不可点（三态不变：被动银灰、锁定银灰、无 click）",
    (() => {
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      const passive = promptSkill("hero.demo.passive");
      const locked = promptSkill("hero.demo.locked");
      const before = calls.length;
      const option = ui.state.selectedOption;
      clickNode(passive);
      clickNode(locked);
      return !!passive && passive.disabled === true && passive.classList.contains("passive")
        && !!locked && locked.disabled === true && locked.classList.contains("locked")
        && ui.state.selectedOption === option && calls.length === before;
    })(),
    `passive=${promptSkill("hero.demo.passive").disabled}/${[...promptSkill("hero.demo.passive").classList.set].join(",")} locked=${promptSkill("hero.demo.locked").disabled}`);

  check("U2-04g 底部技能区原样：顺序 / 全部技能 / 与提示区点出同一个选中项",
    (() => {
      const order = bottomSkillIds().join(",") === view.skills.map((item) => item.skill_id).join(",");
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      const bottom = $("t-skills").children.find(
        (node) => node.getAttribute("data-skill-id") === "hero.demo.alpha");
      clickNode(bottom);
      const fromBottom = ui.state.selectedOption;
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      clickNode(promptSkill("hero.demo.alpha"));
      return order && fromBottom === 0 && ui.state.selectedOption === fromBottom;
    })(),
    `底部=${JSON.stringify(bottomSkillIds())}`);
  ui.resetSelection();
}

/* ---- ② 雌雄双股剑：两个中文选项按钮；无手牌时只有「令其摸一张牌」 ---- */
{
  const optionView = (options, seq) => synthView({
    kind: "SELECT_OPTION", actor: 0, seq, skill_id: "equip.double_sword",
    prompt: "请选择一项。",
    legal_actions: options.map((choice) => ({ kind: "SelectOption", choice, answers_seq: seq })),
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  const optionLabels = () => $("t-options").children
    .filter((node) => node.tagName === "BUTTON")
    .map((node) => node.textContent);

  const both = optionView(["discard_one", "draw_one"], 9430);
  ui.state.view = both;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(both);
  check("U2-04h 雌雄双股剑 = 两个中文选项按钮（原值不显示、无候选行）",
    optionLabels().join(",") === "弃置 1 张手牌,令其摸一张牌"
      && !/discard_one|draw_one/.test($("t-options").text())
      && candidateRowButtons().length === 0,
    `选项=${JSON.stringify(optionLabels())}`);
  const drawButton = $("t-options").children.find((node) => node.textContent === "令其摸一张牌");
  check("U2-04i 点「令其摸一张牌」= 选中那条 SelectOption(draw_one)，提交原样取候选",
    (() => {
      clickNode(drawButton);
      const action = ui.buildAction();
      return ui.state.selectedOption === 1 && $("btn-submit").disabled === false
        && !!action && action.kind === "SelectOption" && action.choice === "draw_one"
        && both.pending.legal_actions.includes(action);
    })(),
    `option=${ui.state.selectedOption} action=${JSON.stringify(ui.buildAction())}`);

  // 引擎在目标**没有手牌**时不发 `discard_one`（`skills/catalog.py::_double_sword`）。
  // M5-bugfix 起界面按**完整的一对**画控件：付不起的那条是**禁用**控件（不是候选），
  // 可点的只有引擎真发了的 `draw_one`（判据是 `legal_actions`，不是前端规则）。
  const drawOnly = optionView(["draw_one"], 9431);
  ui.state.view = drawOnly;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(drawOnly);
  const drawOnlyButtons = $("t-options").children.filter((node) => node.tagName === "BUTTON");
  check("U2-04j 无手牌时「弃置 1 张手牌」是禁用控件、「令其摸一张牌」仍可点（引擎不发不可支付的选项）",
    optionLabels().join(",") === "弃置 1 张手牌,令其摸一张牌"
      && drawOnlyButtons.find((node) => node.textContent === "弃置 1 张手牌").disabled === true
      && drawOnlyButtons.find((node) => node.textContent === "令其摸一张牌").disabled === false,
    `选项=${JSON.stringify(optionLabels())} disabled=${JSON.stringify(drawOnlyButtons.map((node) => [node.textContent, node.disabled]))}`);

  // 可复用的枚举标签（花色）自票 08 起由服务端下发 `option_labels`：前端只读它摆版面，
  // 不抄第二份中文表；有标签就是选择型弹窗，没有标签才退回原值（fail-visible）。
  const suits = optionView(["heart", "spade"], 9432);
  suits.pending.option_labels = {
    heart: { name: "红桃", symbol: "♥", color: "red" },
    spade: { name: "黑桃", symbol: "♠", color: "black" },
  };
  ui.state.view = suits;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(suits);
  check("U2-04k 花色选项读服务端标签（中文 + 符号，前端不抄第二份表）",
    optionLabels().join(",") === "♥红桃,♠黑桃"
      && !/heart|spade/.test($("t-options").text()),
    `选项=${JSON.stringify(optionLabels())}`);
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ M5-bugfix（B）：雌雄双股剑 · 目标无手牌 ============
 *
 * 用户验收（逐字）：
 *   - 目标**没有手牌**时引擎只发 `draw_one`（付不起的组合不进候选），但左侧控制区仍要
 *     列出两条一次性选项：「弃置 1 张手牌」**禁用**（点了零提交），
 *     「令座位号 N <name>摸一张牌」可点并提交那条 `SelectOption(draw_one)`；
 *   - 来源座位 / 名称读服务端派生的 `option_source`（`{"seat", "name"}`）——前端不按
 *     "谁的回合 / 谁用了【杀】"推（那会是第二份规则）；
 *   - **不是**新的弹窗种类：`modal.js:stepDialogKind` 仍是只读判据的唯一来源，
 *     这一步仍是牌桌表面（对话框不打开）；
 *   - 目标有手牌时两条都可点（既有两选项行为不变）。
 */
{
  const doubleSword = {
    ...synthCard(129, "雌雄双股剑"),
    type: "double_sword", display_name: "雌雄双股剑", kind: "normal",
    attribute: "普通", source_cards: [],
    description: "雌雄双股剑：使用【杀】指定异性目标后，令其选择弃一张手牌或你摸一张牌。",
  };
  const doubleSwordView = (options, seq, { optionSource = { seat: 3, name: "张飞" } } = {}) => {
    const view = synthView({
      kind: "SELECT_OPTION", actor: 0, seq, skill_id: "equip.double_sword",
      prompt: "请选择一项。",
      options,
      legal_actions: options.map((choice) => ({
        kind: "SelectOption", choice, answers_seq: seq,
      })),
      candidate_index: {}, virtual_index: {}, option_seats: {},
      context: {
        actor: 3, name: "雌雄双股剑", targets: [0],
        label: "3号位张飞对0号位使用雌雄双股剑", requirement: "选择：弃一张手牌 / 令使用者摸一张",
        skill_hints: [],
      },
      option_source: optionSource,
      preactivations: {}, preactivation: [],
    });
    view.human_seat = 0;
    view.snapshot.seat = 0;
    view.snapshot.self_hand = [];
    view.snapshot.players = [0, 1, 2, 3].map((seat) => ({
      seat, hp: 4, hp_max: 4,
      hand_count: seat === 0 ? 0 : 2,
      hero_name: seat === 3 ? "张飞" : null,
      alive: true, equip: seat === 3 ? [doubleSword] : [], judge: [], identity: null,
      chained: false,
    }));
    return view;
  };
  const optionButtons = () => $("t-options").children.filter(
    (node) => node.tagName === "BUTTON",
  );
  const optionButton = (text) => optionButtons().find((node) => node.textContent === text) || null;
  const settle = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };
  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261019,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  ui.state.modal.heroSeat = null;

  const renderOptionView = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };

  // ---- ① 目标无手牌：两条选项（弃牌禁用 + 令座位 3 张飞摸一张） ----
  const empty = doubleSwordView(["draw_one"], 9450);
  renderOptionView(empty);
  const discard = optionButton("弃置 1 张手牌");
  const draw = optionButton("令座位号 3 张飞摸一张牌");
  check("M5-bugfix-b1 无手牌目标：左侧两条选项（「弃置 1 张手牌」禁用、「令座位号 3 张飞摸一张牌」可点）",
    !!discard && !!draw
      && discard.disabled === true
      && !draw.disabled
      && ui.stepDialogKind(empty.pending) === null
      && $("t-dialog").hidden === true
      && $("t-options").text().includes("弃置 1 张手牌"),
    `选项=${JSON.stringify(optionButtons().map((node) => node.textContent))} discardDisabled=${discard && discard.disabled} drawDisabled=${draw && draw.disabled} dialog=${ui.stepDialogKind(empty.pending)} hidden=${$("t-dialog").hidden}`);

  // ---- ② 点禁用项 = 零提交、不改变选择 ----
  const beforeDisabled = calls.length;
  const beforeSelected = ui.state.selectedOption;
  (discard.listeners.click || []).forEach((fn) => fn({ target: discard }));
  check("M5-bugfix-b2 点禁用「弃置 1 张手牌」= 零提交且不改变选择",
    calls.length === beforeDisabled
      && ui.state.selectedOption === beforeSelected
      && (ui.buildAction() || {}).choice !== "discard_one",
    `calls=${calls.length - beforeDisabled} option=${ui.state.selectedOption} built=${JSON.stringify(ui.buildAction())}`);

  // ---- ③ 点「令座位号 3 张飞摸一张牌」= 选中那条 SelectOption(draw_one)；提交恰一条 ----
  (draw.listeners.click || []).forEach((fn) => fn({ target: draw }));
  const builtDraw = ui.buildAction();
  check("M5-bugfix-b3 点「令座位号 3 张飞摸一张牌」= 选中 legal_actions 里的 SelectOption(draw_one)",
    !!builtDraw && builtDraw.kind === "SelectOption" && builtDraw.choice === "draw_one"
      && empty.pending.legal_actions.includes(builtDraw)
      && $("btn-submit").disabled === false,
    `built=${JSON.stringify(builtDraw)} submit=${$("btn-submit").disabled}`);
  const beforeDraw = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  await settle();
  staleAnswerStatus = null;
  const drawAnswers = calls.slice(beforeDraw).filter((call) => call.path.includes("/answer"));
  check("M5-bugfix-b4 提交恰一条 SelectOption(draw_one)，原样取自 legal_actions",
    drawAnswers.length === 1
      && JSON.stringify(drawAnswers[0].body.action)
        === JSON.stringify({ kind: "SelectOption", choice: "draw_one", answers_seq: 9450 }),
    `answers=${drawAnswers.length} action=${JSON.stringify(drawAnswers[0] && drawAnswers[0].body.action)}`);

  // ---- ④ 正常情况下两条都可点（既有行为不变） ----
  const both = doubleSwordView(["discard_one", "draw_one"], 9451);
  renderOptionView(both);
  const discardBoth = optionButton("弃置 1 张手牌");
  const drawBoth = optionButton("令座位号 3 张飞摸一张牌");
  (drawBoth.listeners.click || []).forEach((fn) => fn({ target: drawBoth }));
  const builtDrawBoth = ui.buildAction();
  (discardBoth.listeners.click || []).forEach((fn) => fn({ target: discardBoth }));
  const builtDiscardBoth = ui.buildAction();
  check("M5-bugfix-b5 目标有手牌时两条都可点（皆可提交对应 SelectOption）",
    !!discardBoth && !!drawBoth
      && !discardBoth.disabled && !drawBoth.disabled
      && !!builtDiscardBoth && builtDiscardBoth.choice === "discard_one"
      && builtDrawBoth.choice === "draw_one",
    `discard=${JSON.stringify(builtDiscardBoth)} draw=${JSON.stringify(builtDrawBoth)}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ---- ③ 鬼才：改判窗口的提示区入口 + 既有手牌路径 + 清空零提交 ---- */
{
  const judgeCard = { ...synthCard(41, "桃"), type: "peach" };
  const pending = {
    kind: "RESPOND", actor: 0, seq: 9440, cause: "REJUDGE", required: null,
    prompt: "响应窗口：请打出一张牌，或放弃。",
    legal_actions: [
      { kind: "PlayCard", answers_seq: 9440, card: judgeCard },
      { kind: "Pass", answers_seq: 9440 },
    ],
    candidate_index: {
      41: { card: judgeCard, pool: [], min_targets: 0, max_targets: 0, combos: [[]] },
    },
    option_seats: {}, context: { requirement: "打出一张手牌，代替此判定牌", skill_hints: [] },
    preactivations: {}, preactivation: [],
  };
  const view = synthView(pending);
  view.snapshot.self_hand = [judgeCard];
  view.skills = [
    { skill_id: "hero.simayi.fankui", name: "反馈", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: false, preactivated: false,
      description: "反馈的描述。",
      ui: { requirement: "获得伤害来源的一张牌", passive: true, convert_sources: [] } },
    { skill_id: "hero.simayi.guicai", name: "鬼才", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: false, preactivated: false,
      description: "鬼才的描述。",
      ui: { requirement: "打出一张手牌，代替此判定牌", passive: false, convert_sources: [] } },
  ];
  ui.state.view = view;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const entry = promptSkill("hero.simayi.guicai");
  const hintNode = $("t-prompt-skills").children.find(
    (node) => String(node.className || "").split(/\s+/).includes("prompt-skill-hint"));
  const before = calls.length;
  check("U2-04l 鬼才改判窗口：提示区只有一个只说明的「鬼才」入口（+ 点手牌提示；无第二套选择）",
    ui.renderKind(pending, pending.legal_actions) === "picker"
      && promptSkillIds().join(",") === "hero.simayi.guicai"
      && !!entry && entry.getAttribute("data-window-entry") === "true"
      && entry.disabled === false
      && !!hintNode && /点一张手牌/.test(hintNode.textContent) && /提交/.test(hintNode.textContent),
    `kind=${ui.renderKind(pending, pending.legal_actions)} 组=${JSON.stringify(promptSkillIds())} entry=${entry && entry.getAttribute("data-window-entry")} hint="${hintNode && hintNode.textContent}"`);
  check("U2-04m 点「鬼才」只写提示：不选中候选、不设焦点、不发请求（不是第二套选择方式）",
    (() => {
      clickNode(entry);
      return ui.state.selectedOption === null
        && ui.state.focusCardId === null
        && calls.length === before
        && /点一张手牌/.test($("t-target-hint").textContent);
    })(),
    `option=${ui.state.selectedOption} focus=${ui.state.focusCardId} calls=${calls.length - before} hint="${$("t-target-hint").textContent}"`);

  const handChip = () => $("t-hand").children[0];
  check("U2-04n 点一张手牌 = 既有手选路径：焦点 + 提交可点 + 提交的就是那条 PlayCard 候选",
    (() => {
      clickNode(handChip());
      const action = ui.buildAction();
      return ui.state.focusCardId === 41 && $("btn-submit").disabled === false
        && $("btn-pass").hidden === false
        && !!action && action.kind === "PlayCard" && action.card.card_id === 41
        && pending.legal_actions.includes(action);
    })(),
    `focus=${ui.state.focusCardId} submit=${$("btn-submit").disabled} action=${JSON.stringify(ui.buildAction())}`);

  check("U2-04o 清空 = 零 action 提交；「放弃」仍只在右端（Pass 原样在候选里）",
    (() => {
      const callsBefore = calls.length;
      clickNode($("btn-cancel"));
      return ui.state.focusCardId === null
        && calls.length === callsBefore
        && $("btn-pass").hidden === false
        && pending.legal_actions.some((action) => action.kind === "Pass");
    })(),
    `focus=${ui.state.focusCardId} calls=${calls.length} passHidden=${$("btn-pass").hidden}`);

  // 提交路径：一次 POST、载荷就是那条 PlayCard（400 由拦截器注入，不依赖真引擎）。
  // 走 `submitAction` 入口而不是 DOM 事件：按钮的监听器会把 MouseEvent 当"选择快照"
  // 传进来（main.js 的既有接线），与本票无关，见交付说明里的 known issue。
  ui.state.sessionId = "smoke-u2-04-rejudge";
  clickNode(handChip());
  staleAnswerStatus = 400;
  const callsBefore = calls.length;
  const submitPromise = ui.submitAction();
  check("U2-04p 鬼才提交在飞时四键冻结（与既有提交路径同一冻结）",
    ["btn-submit", "btn-cancel", "btn-end-phase", "btn-pass"].every((id) => $(id).disabled === true),
    ["btn-submit", "btn-cancel", "btn-end-phase", "btn-pass"].map((id) => `${id}=${$(id).disabled}`).join(" "));
  await submitPromise;
  staleAnswerStatus = null;
  const answerCalls = calls.slice(callsBefore).filter((call) => call.path.includes("/answer"));
  check("U2-04q 点手牌 + 提交 = 恰好 1 条 POST，载荷是那条 PlayCard（被拒后回到点击前）",
    answerCalls.length === 1
      && answerCalls[0].body.action.kind === "PlayCard"
      && answerCalls[0].body.action.card.card_id === 41
      && ui.state.focusCardId === 41
      && /提交被拒/.test($("t-choice").textContent),
    `answers=${answerCalls.length} body=${JSON.stringify(answerCalls[0] && answerCalls[0].body)} focus=${ui.state.focusCardId}`);
  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ---- 倒计时：进度条 / 三色 / 最后 5 秒闪烁 / 默认动作 / 前端不代答 ---- */

{
  const timedPending = (label, seq) => ({
    kind: "PLAY_PHASE", actor: 0, seq,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: {
      text: "出牌阶段：使用一张牌，或结束出牌阶段。",
      default_label: label,
      default_action: { kind: "EndPhase", answers_seq: seq },
    },
    legal_actions: [{ kind: "EndPhase", answers_seq: seq }],
    candidate_index: {}, option_seats: {}, context: null,
  });
  const renderTimed = (pending, timeout, elapsedSeconds) => {
    const view = synthView(pending);
    view.timeout_seconds = timeout;
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
    ui.state.pendingClock.receivedAt = Date.now() - elapsedSeconds * 1000;
    ui.renderCountdown(view);
    return view;
  };

  check("U1-03g 倒计时三色是剩余比例的纯函数（绿 > 50% > 黄 > 20% ≥ 红）",
    typeof ui.countdownPhase === "function"
      && ui.countdownPhase(60, 60) === "green"
      && ui.countdownPhase(31, 60) === "green"
      && ui.countdownPhase(30, 60) === "yellow"
      && ui.countdownPhase(20, 60) === "yellow"
      && ui.countdownPhase(10, 60) === "red"
      && ui.countdownPhase(0, 60) === "red",
    ["green", "yellow", "red"].map((phase, at) => `${phase}:${ui.countdownPhase ? ui.countdownPhase([60, 30, 10][at], 60) : "n/a"}`).join(" "));

  renderTimed(timedPending("超时将自动结束出牌阶段", 9101), 60, 0);
  check("U1-03h 倒计时初始：绿、满格、剩余秒 + 默认动作文案都在提示条右端",
    $("t-countdown").hidden === false
      && $("t-countdown").getAttribute("data-phase") === "green"
      && parseFloat($("t-timer-fill").style.width) > 95
      && /剩余 60 秒/.test($("t-timer").textContent)
      && $("t-default-label").textContent === "超时将自动结束出牌阶段",
    `phase=${$("t-countdown").getAttribute("data-phase")} fill=${$("t-timer-fill").style.width} timer="${$("t-timer").textContent}" label="${$("t-default-label").textContent}"`);

  renderTimed(timedPending("超时将自动放弃", 9102), 60, 35);
  check("U1-03i 剩余 25 秒（< 50%）转黄",
    $("t-countdown").getAttribute("data-phase") === "yellow"
      && $("t-countdown").classList.contains("blink") === false
      && /剩余 25 秒/.test($("t-timer").textContent)
      && $("t-default-label").textContent === "超时将自动放弃",
    `phase=${$("t-countdown").getAttribute("data-phase")} blink=${$("t-countdown").classList.contains("blink")} timer="${$("t-timer").textContent}"`);

  renderTimed(timedPending("超时将自动选择第一项", 9103), 60, 56);
  check("U1-03j 最后 5 秒转红并闪烁；默认动作是「选择第一项」（强制选择）",
    $("t-countdown").getAttribute("data-phase") === "red"
      && $("t-countdown").classList.contains("blink") === true
      && /剩余 4 秒/.test($("t-timer").textContent)
      && $("t-default-label").textContent === "超时将自动选择第一项",
    `phase=${$("t-countdown").getAttribute("data-phase")} blink=${$("t-countdown").classList.contains("blink")} timer="${$("t-timer").textContent}" label="${$("t-default-label").textContent}"`);

  const beforeExpire = calls.length;
  renderTimed(timedPending("超时将自动放弃", 9104), 60, 61);
  check("U1-03k 非排列询问到时限只显示「服务端即将代答」，前端不发任何请求（代答真源仍在服务端）",
    /服务端即将代答/.test($("t-timer").textContent)
      && $("t-countdown").getAttribute("data-phase") === "red"
      && $("t-countdown").classList.contains("blink") === false
      && calls.length === beforeExpire,
    `timer="${$("t-timer").textContent}" calls=${calls.length - beforeExpire}`);

  // U1-03k2（票 07 / spec D9.3 的改写）：**唯一例外** = 自己的排列到时限自动提交一次
  // 当前排列；重复 tick 不重复提交。用真会话（4 人 / 种子 7 / 诸葛亮）走到 ARRANGE，
  // 再把本机锚点拨到"剩余 1 秒"（< 2 秒的提前量），数 `/answer` 的调用次数。
  const deadlineSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: 60, seed: 7,
    my_hero: "zhugeliang",
  });
  await openSession(deadlineSession);
  await ui.submitAction();   // 答"发动观星" ⇒ 引擎停在 ARRANGE（X = 4）
  ui.state.arrangeAutoSeq = null;
  const deadlineArrange = ui.state.view.pending;
  const deadlineAction = ui.buildAction();
  const beforeArrangeExpire = calls.length;
  ui.state.pendingClock.receivedAt = Date.now() - 59000;
  const expireTick1 = ui.renderCountdown(ui.state.view);
  const expireTick2 = ui.renderCountdown(ui.state.view);
  await expireTick1;
  await expireTick2;
  const arrangeAnswers = calls.slice(beforeArrangeExpire)
    .filter((call) => call.path.includes("/answer"));
  check("U1-03k2 自己的排列到时限只自动提交一次当前排列（重复 tick 不重复发；唯一例外）",
    !!deadlineArrange && deadlineArrange.kind === "ARRANGE"
      && deadlineArrange.legal_actions.every((action) => action.kind === "ArrangeCards")
      && !!deadlineAction && deadlineAction.kind === "ArrangeCards"
      && arrangeAnswers.length === 1
      && JSON.stringify(arrangeAnswers[0].body.action) === JSON.stringify(deadlineAction)
      && ui.state.arrangeAutoSeq === deadlineArrange.seq,
    `pending=${deadlineArrange && deadlineArrange.kind} actions=${deadlineArrange && deadlineArrange.legal_actions.length} pass=${deadlineArrange && deadlineArrange.legal_actions.some((action) => action.kind === "Pass")} action=${JSON.stringify(deadlineAction)} answers=${arrangeAnswers.length} body=${JSON.stringify(arrangeAnswers[0] && arrangeAnswers[0].body.action)} autoSeq=${ui.state.arrangeAutoSeq}`);
  ui.state.sessionId = null;
  ui.state.view = null;
  ui.state.arrangeAutoSeq = null;
  ui.resetSelection();

  // 其他座位的询问 / 回放 / 时限关闭：都不显示倒计时。
  const foreignTimed = synthView({
    kind: "RESPOND", actor: 1, seq: 9105, prompt: "响应窗口：请打出一张牌，或放弃。",
    prompt_display: { text: "响应窗口：请打出一张牌，或放弃。", default_label: "超时将自动放弃" },
    legal_actions: [{ kind: "Pass", answers_seq: 9105 }],
    candidate_index: {}, option_seats: {}, context: null,
  });
  foreignTimed.timeout_seconds = 60;
  ui.state.view = foreignTimed;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(foreignTimed);
  check("U1-03l 其他座位 / AI 的询问不显示倒计时",
    $("t-countdown").hidden === true && $("t-timer").textContent === "",
    `hidden=${$("t-countdown").hidden} timer="${$("t-timer").textContent}"`);

  const playbackView = renderTimed(timedPending("超时将自动放弃", 9106), 60, 0);
  ui.state.playback = { body: {}, records: [], index: 0 };
  ui.renderCountdown(playbackView);
  check("U1-03m 回放期间不显示倒计时",
    $("t-countdown").hidden === true && $("t-timer").textContent === "",
    `hidden=${$("t-countdown").hidden} timer="${$("t-timer").textContent}"`);
  ui.state.playback = null;

  const noTimeoutView = renderTimed(timedPending("超时将自动放弃", 9107), null, 0);
  check("U1-03n 时限关闭（null）时不显示倒计时",
    $("t-countdown").hidden === true && $("t-timer").textContent === "",
    `hidden=${$("t-countdown").hidden} timer="${$("t-timer").textContent}"`);
  ui.renderCountdown(noTimeoutView);

  // 真会话：服务端下发 prompt_display（默认动作文案）+ lobby 时限，界面据此起表。
  const timedSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: 45, seed: 5,
  });
  await openSession(timedSession);
  const timedPendingReal = ui.state.view.pending;
  check("U1-03o 真会话：自己的询问带服务端默认动作文案，倒计时开始走",
    !!timedPendingReal
      && !!timedPendingReal.prompt_display
      && ["超时将自动放弃", "超时将自动结束出牌阶段", "超时将自动选择第一项"]
        .includes(timedPendingReal.prompt_display.default_label)
      && $("t-countdown").hidden === false
      && ["green", "yellow", "red"].includes($("t-countdown").getAttribute("data-phase"))
      && $("t-default-label").textContent === timedPendingReal.prompt_display.default_label,
    `label=${timedPendingReal && timedPendingReal.prompt_display && timedPendingReal.prompt_display.default_label} phase=${$("t-countdown").getAttribute("data-phase")} timer="${$("t-timer").textContent}"`);
}

/* ---- 共享模态：正中 / 可滚 / 关闭按钮与 ESC 可关 / 背景不吞指针（D8）/ 不暂停倒计时 ---- */

{
  const card = synthCard(81, "杀");
  const makePending = (seq) => ({
    kind: "PLAY_PHASE", actor: 0, seq,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: seq }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {
      "demo.modal": [
        {
          name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["81"] }],
        },
        {
          name: "seat", zones: [], min: 1, max: 1, order: 1,
          pool: [{ when: [], options: ["seat:1", "seat:2"] }],
        },
      ],
    },
    preactivation: [],
  });
  const makeView = (seq) => {
    const view = synthView(makePending(seq));
    view.timeout_seconds = 60;
    view.snapshot.self_hand = [card];
    view.skills = [{
      skill_id: "demo.modal", name: "示例模态", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: true, preactivated: true,
      ui: { requirement: "", convert_sources: [] },
    }];
    return view;
  };
  const enterModal = () => {
    const view = makeView(9201);
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
    const button = $("t-skills").children.find(
      (node) => node.getAttribute("data-skill-id") === "demo.modal");
    (button.listeners.click || []).forEach((fn) => fn({ target: button }));
    return view;
  };

  let view = enterModal();
  check("U1-03p 多步技能清单打开询问弹窗（标题 + 清单在正文里；详情窗保持关闭）",
    $("t-dialog").hidden === false
      && /预发动/.test($("t-dialog-title").textContent)
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-modal").hidden === true,
    `dialogHidden=${$("t-dialog").hidden} title="${$("t-dialog-title").textContent}" bodyChildren=${$("t-dialog-body").children.length} detailHidden=${$("t-modal").hidden}`);
  const modalRegions = ["table-topbar", "table-stage", "table-bottom", "t-dialog", "t-modal"];
  check("U1-03q 询问弹窗打开时没有任何区域 inert / aria-hidden：顶栏 / 舞台 / 底部都可检视",
    modalRegions.every((id) => $(id).getAttribute("inert") === undefined
      && $(id).getAttribute("aria-hidden") === undefined)
      && $("table-shell").classList.contains("modal-open")
      && $("t-modal-backdrop").hidden === false,
    `regions=${modalRegions.map((id) => `${id}:${$(id).getAttribute("inert")}`).join(",")} openClass=${$("table-shell").classList.contains("modal-open")} backdropHidden=${$("t-modal-backdrop").hidden}`);
  check("U1-03q2 预发动清单是牌桌表面（D2 的 C 形），不是只读态：selectionEnabled() 仍为真",
    ui.stepDialogKind(view.pending) === null
      && ui.selectionEnabled() === true
      && ui.state.dialog.kind === "preactivation",
    `surface=${ui.stepDialogKind(view.pending)} enabled=${ui.selectionEnabled()} dialog=${ui.state.dialog.kind}`);
  check("U1-03r 询问弹窗内不显示倒计时（倒计时留在提示条上）",
    !$("t-dialog-body").text().includes("剩余")
      && $("t-countdown").hidden === false
      && $("t-default-label").textContent.includes("超时")
      && !$("t-dialog-body").children.includes($("t-countdown")),
    `bodyText="${$("t-dialog-body").text().slice(0, 60)}" countdownHidden=${$("t-countdown").hidden} countdownInBody=${$("t-dialog-body").children.includes($("t-countdown"))}`);

  // 不暂停：弹窗开着时只挪本地锚点，倒计时照常从绿 → 黄 → 红。
  ui.state.pendingClock.receivedAt = Date.now() - 35 * 1000;
  ui.renderCountdown(view);
  const yellowWhileOpen = $("t-countdown").getAttribute("data-phase");
  ui.state.pendingClock.receivedAt = Date.now() - 56 * 1000;
  ui.renderCountdown(view);
  check("U1-03s 询问弹窗打开不暂停倒计时（黄 → 红 + 闪烁仍在提示条上走）",
    yellowWhileOpen === "yellow"
      && $("t-countdown").getAttribute("data-phase") === "red"
      && $("t-countdown").classList.contains("blink") === true
      && $("t-countdown").hidden === false,
    `yellow=${yellowWhileOpen} now=${$("t-countdown").getAttribute("data-phase")} blink=${$("t-countdown").classList.contains("blink")}`);

  // 第 1 步选牌：弹窗外的**手牌**仍可点（预发动是牌桌表面：selectionEnabled 不拦它）。
  const handChip = $("t-hand").children[0];
  (handChip.listeners.click || []).forEach((fn) => fn({ target: handChip }));
  check("U1-03t 弹窗打开时仍能手牌选来源牌（底部提示条 / 手牌交互未被挡）",
    JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[0].values) === "[81]",
    JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[0].values));

  // 第 2 步是**选目标**：弹窗可能挡住环上某几个座位，故正文里同时铺座位按钮；
  // 环上高亮也仍在（没有 inert，D8）。两条入口写同一个答案。
  const next = $("t-options").children.find((node) => node.textContent === "下一步");
  (next.listeners.click || []).forEach((fn) => fn({ target: next }));
  const seatOption = $("t-options").children.find(
    (node) => (node.className || "").includes("seat-opt")
      && (node.textContent || "").includes("座位 1"));
  check("U1-03t2 弹窗里的座位按钮与环上座位都可选目标（被窗口挡住的座位也有入口）",
    !!seatOption && seatBox(1).classList.contains("targetable"),
    `seatOption="${seatOption && seatOption.textContent}" targetable=${seatBox(1).classList.contains("targetable")}`);
  (seatOption.listeners.click || []).forEach((fn) => fn({ target: seatOption }));
  check("U1-03t3 点弹窗里的座位按钮 = 写入同一个答案（可拼出复合 ActivateSkill）",
    (() => {
      const action = ui.buildAction();
      return !!action && action.kind === "ActivateSkill"
        && JSON.stringify(action.choices) === '["seat:1"]';
    })(),
    JSON.stringify(ui.buildAction()));

  // ESC 不再是询问弹窗的出口（D3）：预发动靠提示条右端「取消预发动」退出，零提交。
  const beforeEsc = calls.length;
  fireDocument("keydown", { key: "Escape" });
  const escKeptOpen = $("t-dialog").hidden === false
    && Boolean(ui.state.preactivation)
    && calls.length === beforeEsc;
  await clickAndWait($("btn-cancel"));
  check("U1-03u ESC 对询问弹窗是 no-op；右端「取消预发动」退出本地态、零 action 提交",
    escKeptOpen
      && $("t-dialog").hidden === true
      && ui.state.preactivation === null
      && $("t-dialog-body").children.length === 0
      && calls.length === beforeEsc,
    `escKeptOpen=${escKeptOpen} hidden=${$("t-dialog").hidden} pre=${ui.state.preactivation} body=${$("t-dialog-body").children.length} calls=${calls.length - beforeEsc}`);

  view = enterModal();
  const backdropClicks = ($("t-modal-backdrop").listeners.click || []).length;
  const beforeBackdrop = calls.length;
  ($("t-modal-backdrop").listeners.click || []).forEach((fn) => fn({ target: $("t-modal-backdrop") }));
  // 遮罩是 `pointer-events: none` 的暗化层，没有自己的点击——「点背景 = 关闭」不存在；
  // 真实点击穿透到牌桌，由 selectionEnabled() 判据拦下。
  check("U1-03v 遮罩不再吞指针 / 不再承担关闭（无 click 监听；询问弹窗保持打开、零提交）",
    backdropClicks === 0
      && $("t-dialog").hidden === false
      && Boolean(ui.state.preactivation)
      && calls.length === beforeBackdrop,
    `backdropClicks=${backdropClicks} hidden=${$("t-dialog").hidden} pre=${Boolean(ui.state.preactivation)} calls=${calls.length - beforeBackdrop}`);

  // 排列（强制选择）：同一个询问弹窗；折叠只收起正文，标题条与「查看排列」入口仍在。
  const arrangePending = {
    kind: "ARRANGE", actor: 0, seq: 9202, timing: "PHASE_PREPARE",
    prompt: "请排列这些牌：给出顺序与前几张置于牌堆顶。",
    revealed: [synthCard(82, "八卦阵"), synthCard(83, "闪")],
    legal_actions: [{ answers_seq: 9202, kind: "ArrangeCards", order: [0, 1], split: 2 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
    arrange: { count: 2, split: "free" },
  };
  const arrangeView = synthView(arrangePending);
  arrangeView.timeout_seconds = 60;
  ui.state.view = arrangeView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(arrangeView);
  check("U1-03w 排列面走同一个询问弹窗（标题 + 正文都在 #t-dialog 里；详情窗不掺和）",
    $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-modal").hidden === true,
    `dialogHidden=${$("t-dialog").hidden} detailHidden=${$("t-modal").hidden} title="${$("t-dialog-title").textContent}"`);
  const arrangeActionBefore = JSON.stringify(ui.buildAction());
  (($("t-dialog-collapse").listeners.click) || []).forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  check("U1-03x 排列折叠：仍是只读（selectionEnabled false）、正文撤下、保留已摆顺序 + 「查看排列」入口",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && $("t-dialog-collapse").textContent === "展开"
      && $("btn-show-modal").hidden === false
      && $("btn-show-modal").textContent === "查看排列"
      && ui.selectionEnabled() === false
      && modalRegions.every((id) => $(id).getAttribute("inert") === undefined)
      && JSON.stringify(ui.buildAction()) === arrangeActionBefore,
    `bodyHidden=${$("t-dialog-body").hidden} collapse="${$("t-dialog-collapse").textContent}" reopenHidden=${$("btn-show-modal").hidden} enabled=${ui.selectionEnabled()} action=${JSON.stringify(ui.buildAction())}`);
  (($("btn-show-modal").listeners.click) || []).forEach((fn) => fn({ target: $("btn-show-modal") }));
  check("U1-03y 「查看排列」展开折叠的排列弹窗，顺序不变",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && $("t-dialog-collapse").textContent === "收起"
      && $("btn-show-modal").hidden === true
      && JSON.stringify(ui.buildAction()) === arrangeActionBefore,
    `bodyHidden=${$("t-dialog-body").hidden} reopenHidden=${$("btn-show-modal").hidden}`);

  // 静态规范：询问弹窗与详情窗同几何；遮罩 = pointer-events: none 的暗化层（D8）。
  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");
  check("U1-03z 询问弹窗 CSS：舞台区正中、正文可滚、折叠只撤正文、遮罩不吞指针",
    /\.dialog\s*\{[^}]*align-items:\s*center[^}]*justify-content:\s*center/s.test(css)
      && /\.dialog\s*\{[^}]*bottom:\s*27\.5%/s.test(css)
      && /\.dialog-body\s*\{[^}]*overflow-y:\s*auto/s.test(css)
      && /\.dialog-body\[hidden\]\s*\{[^}]*display:\s*none/s.test(css)
      && /\.modal-backdrop\s*\{[^}]*bottom:\s*27\.5%/s.test(css)
      && /\.modal-backdrop\s*\{[^}]*pointer-events:\s*none/s.test(css)
      && !/\.modal-backdrop\s*\{[^}]*pointer-events:\s*auto/s.test(css)
      && /\.dialog\s*\{[^}]*pointer-events:\s*none/s.test(css)
      && /\.dialog-window\s*\{[^}]*pointer-events:\s*auto/s.test(css),
    `dialog=${/\.dialog\s*\{[^}]*align-items:\s*center[^}]*justify-content:\s*center/s.test(css)} body=${/\.dialog-body\s*\{[^}]*overflow-y:\s*auto/s.test(css)} collapsed=${/\.dialog-body\[hidden\]\s*\{[^}]*display:\s*none/s.test(css)} backdrop=${/\.modal-backdrop\s*\{[^}]*pointer-events:\s*none/s.test(css)}`);
  ui.resetSelection();
  ui.state.view = null;
  ui.renderPending(null);
}


/* ============ U1-05 叙事与事件流：简易 / 完整重点 / 完整全部 ============
 *
 * 票面判据：
 * - 简易流是默认档，一条行动一行；条目由**服务端**生成（`view.narrative`），前端只按
 *   `segments[].cls` 上色，不在 JS 里判断隐藏信息；
 * - 完整重点 / 完整全部沿用现有 `focus` / `all` 的 `describeRecord` 渲染；
 * - 浮层 420×560、左下角按钮、默认折叠、再次点击关闭、展开常驻、自动滚到底；
 * - 牌桌与回放共用同一份条目生成器（回放的简易档读同一批 `narrative`）。
 */

{
  /* ---- 静态契约：三档在顶部切换 + 浮层尺寸 ---- */
  const simpleOption = html.match(/<option value="simple"[^>]*selected[^>]*>/);
  check("U1-05a 事件流默认简易档（index.html 的 simple 选项带 selected）",
    !!simpleOption, `simple=${!!simpleOption}`);
  const modeSelectHtml = html.match(/<select[^>]*id="t-log-mode"[^>]*>([\s\S]*?)<\/select>/);
  const modeValues = modeSelectHtml
    ? [...modeSelectHtml[1].matchAll(/value="([^"]+)"/g)].map((match) => match[1])
    : [];
  check("U1-05b 事件流顶部三档：简易 / 完整重点 / 完整全部",
    JSON.stringify(modeValues) === JSON.stringify(["simple", "focus", "all"]),
    `options=${JSON.stringify(modeValues)}`);
  const replayModeHtml = html.match(/<select[^>]*id="r-log-mode"[^>]*>([\s\S]*?)<\/select>/);
  const replayModeValues = replayModeHtml
    ? [...replayModeHtml[1].matchAll(/value="([^"]+)"/g)].map((match) => match[1])
    : [];
  check("U1-05c 回放页事件流默认简易档（两档：简易 / 完整全部）",
    JSON.stringify(replayModeValues) === JSON.stringify(["simple", "all"])
      && /<option value="simple" selected>/.test(replayModeHtml ? replayModeHtml[1] : ""),
    `options=${JSON.stringify(replayModeValues)}`);

  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");
  check("U1-05d 事件流浮层 420×560、左下角（默认折叠由 U1-01m/n 另钉）",
    /\.log-panel\s*\{[^}]*width:\s*420px/s.test(css)
      && /\.log-panel\s*\{[^}]*height:\s*560px/s.test(css)
      && /\.log-panel\s*\{[^}]*left:\s*0\.9%/s.test(css)
      && /\.log-panel\s*\{[^}]*bottom:\s*27\.2%/s.test(css),
    `width=${/\.log-panel\s*\{[^}]*width:\s*420px/s.test(css)} height=${/\.log-panel\s*\{[^}]*height:\s*560px/s.test(css)}`);
  const colorClasses = ["seat", "hero", "card", "card-fire", "card-thunder", "skill", "act", "dmg", "dmg-fire", "dmg-thunder"];
  const missingColors = colorClasses.filter((name) => !new RegExp(`\\.nar-${name}\\b`).test(css));
  check("U1-05e 叙事颜色闭集都有 CSS（黄 / 黑 / 红 / 蓝 / 粗体）",
    missingColors.length === 0, missingColors.join(",") || "全部存在");

  /* ---- 简易流：服务端条目 → DOM（只渲染，不判断隐藏信息） ---- */
  const narrative = [
    { kind: "divider", seq: 10, text: "第 1 轮 · 座位0 郭嘉 的回合" },
    { kind: "divider", seq: 11, text: "出牌阶段" },
    {
      kind: "line", seq: 11,
      text: "郭嘉 使用【杀】 → 座位1",
      segments: [
        { text: "郭嘉", cls: "hero" },
        { text: " 使用", cls: "act" }, { text: "【杀】", cls: "card-fire" },
        { text: " → ", cls: "act" }, { text: "座位1", cls: "seat" },
      ],
    },
    {
      kind: "line", seq: 12,
      text: "座位1 司马懿 受到 1 点伤害",
      segments: [
        { text: "座位1", cls: "seat" }, { text: " 司马懿", cls: "hero" },
        { text: " 受到 ", cls: "act" }, { text: "1", cls: "dmg-fire" },
        { text: " 点伤害", cls: "act" },
      ],
    },
    {
      kind: "line", seq: 13,
      text: "郭嘉 发动【遗计】",
      segments: [
        { text: "郭嘉", cls: "hero" },
        { text: " 发动", cls: "act" }, { text: "【遗计】", cls: "skill" },
      ],
    },
  ];
  const fullRecords = [
    { kind: "timing", seq: 1, timing: "TURN_START", actor: 0 },
    { kind: "query", seq: 2, query: { kind: "RESPOND", actor: 1 } },
    { kind: "action", seq: 3, accepted: true, action: { kind: "Pass", answers_seq: 2 } },
  ];
  const view = {
    ...synthView(null), cursor: 13, log_seq: 0, records: [], narrative, finished: false,
    pending: {
      kind: "PLAY_PHASE", actor: 0, seq: 13, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
      legal_actions: [{ kind: "EndPhase", answers_seq: 13 }],
      candidate_index: {}, option_seats: {}, context: null,
    },
  };
  ui.state.narrative = [];
  ui.state.narrativeShown = 0;
  ui.state.logRecords = fullRecords;
  ui.state.sessionId = null;   // 合成视图：不让提交 / 刷新碰到别的会话
  setSelect("t-log-mode", "simple");
  ui.applyView(view);

  check("U1-05f 简易流是默认档，渲染服务端叙事而不是引擎日志",
    logCount() === narrative.length
      && logText().includes("受到 1 点伤害")
      && logText().includes("发动【遗计】")
      && !logText().includes("#"),
    `count=${logCount()} text="${logText().replace(/\s+/g, " ").slice(0, 120)}"`);
  const logItems = $("t-log").children;
  check("U1-05g 轮次 / 阶段分隔是独立的暗灰分隔行（不做树）",
    logItems[0].className === "nar-divider"
      && logItems[0].textContent.startsWith("第 1 轮")
      && logItems[1].className === "nar-divider"
      && logItems[1].textContent === "出牌阶段",
    `[0]=${logItems[0].className}:${logItems[0].textContent} [1]=${logItems[1].className}:${logItems[1].textContent}`);
  const lineClasses = logItems[2].children.map((node) => node.className);
  check("U1-05h 服务端的颜色类原样落到 span（武将黄、卡名红、动作粗、伤害红、技能蓝）",
    lineClasses.includes("nar-hero") && lineClasses.includes("nar-card-fire")
      && lineClasses.includes("nar-act")
      && logItems[3].children.some((node) => node.className === "nar-seat")
      && logItems[3].children.some((node) => node.className === "nar-hero")
      && logItems[3].children.some((node) => node.className === "nar-dmg-fire")
      && logItems[4].children.some((node) => node.className === "nar-skill"),
    `line2=${lineClasses.join(",")}`);

  /* ---- 完整两档沿用现有行为；切回简易重新渲染叙事 ---- */
  setSelect("t-log-mode", "focus");
  check("U1-05i 完整重点 = 现有 focus 渲染（保留自己的行动，隐藏对手响应往来）",
    logText().includes("回合开始")
      && !logText().includes("询问 RESPOND")
      && !logText().includes("受到 1 点伤害"),
    `text="${logText().replace(/\s+/g, " ").slice(0, 120)}"`);
  setSelect("t-log-mode", "all");
  check("U1-05j 完整全部 = 现有 all 渲染（含对手的响应往来）",
    logText().includes("询问 RESPOND") && logText().includes("应答"),
    `text="${logText().replace(/\s+/g, " ").slice(0, 120)}"`);
  setSelect("t-log-mode", "simple");
  check("U1-05k 切回简易档：叙事重新铺开（不需要重新请求）",
    logCount() === narrative.length && logText().includes("发动【遗计】"),
    `count=${logCount()} text="${logText().replace(/\s+/g, " ").slice(0, 100)}"`);

  /* ---- 自动滚到底（展开浮层时把 scrollTop 推到底） ---- */
  $("t-log").scrollHeight = 777;
  $("t-log").scrollTop = 0;
  $("t-log-panel").hidden = true;
  (($("t-log-toggle").listeners.click) || []).forEach((fn) => fn({ target: $("t-log-toggle") }));
  check("U1-05l 展开事件流自动滚到底（scrollTop = scrollHeight）",
    $("t-log").scrollTop === 777 && $("t-log-panel").hidden === false,
    `scrollTop=${$("t-log").scrollTop} scrollHeight=${$("t-log").scrollHeight} hidden=${$("t-log-panel").hidden}`);
  // 展开后常驻：点页面别处不会自动收起（只有再次点事件流按钮才关）。
  fireDocument("click", { target: $("table-stage") });
  check("U1-05l2 展开后点别处不自动关（常驻到再次点击）",
    $("t-log-panel").hidden === false, `hidden=${$("t-log-panel").hidden}`);
  (($("t-log-toggle").listeners.click) || []).forEach((fn) => fn({ target: $("t-log-toggle") }));
  check("U1-05m 再次点击收起（展开常驻，不自动关）", $("t-log-panel").hidden === true);
}

/* ---- 回放页：默认简易档，读同一份服务端叙事；完整全部仍可切 ---- */
{
  // 前面的 W4-6 用例收尾时清掉了 `state.replay`；这里**重新打开**一局真录像，
  // 让断言落在真服务端载荷上（`narrative` 由 `load_run` 生成）。
  const replayRuns = await get("/api/runs");
  await ui.openReplay((replayRuns.runs[0] || runs.runs[0]).name);
  const replay = ui.state.replay;
  check("U1-05n 回放载荷带服务端叙事（牌桌与回放共用同一份生成器）",
    !!replay && Array.isArray(replay.narrative) && replay.narrative.length > 0,
    `narrative=${replay && replay.narrative ? replay.narrative.length : "缺"}`);
  check("U1-05n2 打开回放默认简易档（票面「简易流是默认档」）",
    $("r-log-mode").value === "simple", `r-log-mode=${$("r-log-mode").value}`);
  if (replay && Array.isArray(replay.narrative) && replay.narrative.length) {
    // 回放位置回到第 0 条（header）时，简易档不能提前铺出整局（序列跟随播放进度）。
    replay.index = 0;
    setSelect("r-log-mode", "simple");
    ui.renderReplay();
    check("U1-05q 回放简易档不提前播未来事件（第 0 条 = 空）",
      $("r-log").children.length === 0,
      `children=${$("r-log").children.length}`);
    replay.index = replay.records.length - 1;
    setSelect("r-log-mode", "all");
    ui.renderReplay();
    const allText = $("r-log").text();
    setSelect("r-log-mode", "simple");
    ui.renderReplay();
    const simpleText = $("r-log").text();
    check("U1-05o 回放简易档只渲染叙事条目（不含 #seq / 引擎帧）",
      simpleText.length > 0 && !/#\d+/.test(simpleText)
        && simpleText.includes("对局结束")
        && !simpleText.includes("结算帧"),
      `simple="${simpleText.replace(/\s+/g, " ").slice(-120)}"`);
    // 真服务端叙事（不是手写夹具）也要真的落到颜色 span 上。
    const realClasses = new Set();
    for (const item of $("r-log").children) {
      for (const span of item.children) realClasses.add(span.className);
    }
    check("U1-05o2 真服务端叙事渲染出颜色类（座位 / 武将 / 卡名 / 技能 / 伤害）",
      ["nar-seat", "nar-hero", "nar-card", "nar-skill", "nar-dmg"]
        .every((name) => realClasses.has(name)),
      [...realClasses].join(","));
    check("U1-05p 回放完整全部仍是既有逐条记录（切档不改已有行为）",
      /#\d+/.test(allText) && allText.includes("询问"),
      `all="${allText.replace(/\s+/g, " ").slice(-120)}"`);
    setSelect("r-log-mode", "all");
  }
}

/* ============ U1-06：技能按钮三态 + 武将详情模态 ============
 *
 * 票面判据：
 * - 技能按钮区列出全部技能（武将 + 装备 + 被动 / 锁定）；顺序武将在前、装备在后；
 * - 三态：可用（绿）/ 不可用（深灰）/ 锁定 / 被动（银灰）；
 * - 被动 / 锁定技按钮左键 / 右键都不动作，悬停出描述；
 * - 右键武将块 = 详情模态：武将技能在上、分割线、装备技能在下；背景 / ESC 可关、模态；
 * - 打开详情不暂停倒计时，模态内不显示倒计时；阵亡武将也能点开。
 */

{
  ui.state.sessionId = null;   // 纯合成视图：不让按钮 / 提交碰到别的会话
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9301,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9301 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  };
  const view = synthView(pending);
  view.timeout_seconds = 60;
  view.skills = [
    {
      skill_id: "hero.demo.active", name: "可发动", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: true, preactivated: false,
      description: "可发动的主动技描述。", ui: { requirement: "", passive: false, convert_sources: [] },
    },
    {
      skill_id: "hero.demo.off", name: "不可用", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: false, preactivated: false,
      description: "此刻不可用的主动技描述。", ui: { requirement: "", passive: false, convert_sources: [] },
    },
    {
      skill_id: "hero.demo.passive", name: "被动", skill_type: "NORMAL", locked: false,
      carrier: "HERO", available: true, preactivated: false,
      description: "触发技描述。", ui: { requirement: "", passive: true, convert_sources: [] },
    },
    {
      skill_id: "hero.demo.nodesc", name: "缺描述", skill_type: "NORMAL", locked: true,
      carrier: "HERO", available: false, preactivated: false, ui: { requirement: "", passive: false, convert_sources: [] },
    },
    {
      skill_id: "equip.demo.locked", name: "锁定装备", skill_type: "NORMAL", locked: true,
      carrier: "EQUIPMENT", available: false, preactivated: false,
      description: "锁定装备技描述。", ui: { requirement: "", passive: false, convert_sources: [] },
    },
    {
      skill_id: "equip.demo.active", name: "装备主动", skill_type: "NORMAL", locked: false,
      carrier: "EQUIPMENT", available: true, preactivated: false,
      description: "装备主动技描述。", ui: { requirement: "", passive: false, convert_sources: [] },
    },
  ];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButtons = $("t-skills").children.filter((node) => node.tagName === "BUTTON");
  const skillButton = (id) => skillButtons.find((node) => node.getAttribute("data-skill-id") === id);
  check("U1-06a 技能按钮区列出全部技能（武将 + 装备 + 被动 / 锁定）",
    skillButtons.length === view.skills.length,
    `按钮 ${skillButtons.length} / 清单 ${view.skills.length}`);
  check("U1-06b 顺序 = 武将技能在前、装备技能在后（服务端清单顺序原样落到 DOM）",
    skillButtons.map((node) => node.getAttribute("data-skill-id")).join(",")
      === view.skills.map((skill) => skill.skill_id).join(",")
      && (() => {
        const carriers = skillButtons.map((node) => {
          const skill = view.skills.find((item) => item.skill_id === node.getAttribute("data-skill-id"));
          return skill ? skill.carrier : "";
        });
        const firstEquip = carriers.indexOf("EQUIPMENT");
        return firstEquip < 0 || carriers.slice(firstEquip).every((carrier) => carrier === "EQUIPMENT");
      })(),
    skillButtons.map((node) => node.getAttribute("data-skill-id")).join(","));

  const active = skillButton("hero.demo.active");
  const off = skillButton("hero.demo.off");
  const passive = skillButton("hero.demo.passive");
  const locked = skillButton("equip.demo.locked");
  const equipActive = skillButton("equip.demo.active");
  check("U1-06c 可用（绿）态：可点、ready 类",
    !!active && active.classList.contains("ready") && active.disabled === false,
    `ready=${active && active.classList.contains("ready")} disabled=${active && active.disabled}`);
  check("U1-06d 不可用（深灰）态：不可点、unavailable 类、非 ready",
    !!off && off.classList.contains("unavailable") && !off.classList.contains("ready") && off.disabled === true,
    `classes=${off && [...off.classList.set].join(",")} disabled=${off && off.disabled}`);
  check("U1-06e 被动（银灰）与锁定（银灰）都不可点、都不进 ready",
    !!passive && passive.classList.contains("passive") && passive.disabled === true
      && !passive.classList.contains("ready")
      && !!locked && locked.classList.contains("locked") && locked.disabled === true
      && !locked.classList.contains("ready"),
    `passive=${passive && [...passive.classList.set].join(",")} locked=${locked && [...locked.classList.set].join(",")}`);
  check("U1-06f 装备技能不因载体而一律变灰：可发动的装备技仍可点（绿）",
    !!equipActive && equipActive.classList.contains("ready") && equipActive.disabled === false,
    `classes=${equipActive && [...equipActive.classList.set].join(",")} disabled=${equipActive && equipActive.disabled}`);
  check("U1-06g 技能 / 牌面悬停全文：缺条目显示「暂无描述」",
    active.getAttribute("title") === "可发动的主动技描述。"
      && skillButton("hero.demo.nodesc").getAttribute("title") === "暂无描述",
    `active="${active.getAttribute("title")}" nodesc="${skillButton("hero.demo.nodesc").getAttribute("title")}"`);

  const selectedBefore = ui.state.selectedOption;
  const callsBefore = calls.length;
  (passive.listeners.click || []).forEach((fn) => fn({ target: passive }));
  (locked.listeners.click || []).forEach((fn) => fn({ target: locked }));
  check("U1-06h 被动 / 锁定技按钮左键不动作（不选中候选、不发请求）",
    ui.state.selectedOption === selectedBefore && calls.length === callsBefore,
    `option=${ui.state.selectedOption} calls=${calls.length - callsBefore}`);
  check("U1-06i 被动 / 锁定技按钮没有 contextmenu 动作（右键也不动作）",
    (passive.listeners.contextmenu || []).length === 0
      && (locked.listeners.contextmenu || []).length === 0);

  /* ---- 武将详情模态：右键武将块 / 背景与 ESC 关闭 / 阵亡 / 倒计时 ---- */

  const heroBlockOf = (seat) => {
    if (Number(seat) === Number(ui.state.view.snapshot.seat)) return $("t-self-hero");
    const box = seatBox(seat);
    return box ? box.children.find((node) => (node.className || "").split(" ").includes("hero-block")) || null : null;
  };
  /** 右键一个节点：只走该节点自己的 contextmenu 监听（真 DOM 里由它冒泡到牌桌外壳）。 */
  const fireContext = (node) => {
    const event = { target: node, prevented: false, stopPropagation() {} };
    event.preventDefault = () => { event.prevented = true; };
    if (node) (node.listeners.contextmenu || []).forEach((fn) => fn(event));
    return event;
  };
  const detailSkill = (id, name, carrier = "HERO", extra = {}) => ({
    skill_id: id, name, skill_type: "NORMAL", locked: false, carrier,
    description: `${name}的描述。`, ui: { passive: false }, ...extra,
  });
  const detailView = synthView(pending);
  detailView.timeout_seconds = 60;
  detailView.snapshot.players[0].skills = [
    detailSkill("hero.demo.own", "己方技"),
    detailSkill("equip.demo.own", "己方装备", "EQUIPMENT"),
  ];
  detailView.snapshot.players[1].skills = [
    detailSkill("hero.demo.enemy", "敌方技"),
    detailSkill("equip.demo.enemy", "敌方武器", "EQUIPMENT"),
    detailSkill("equip.eight_diagrams", "八卦阵", "EQUIPMENT"),
  ];
  detailView.snapshot.players[1].hero_name = "张飞";
  detailView.snapshot.players[1].kingdom_label = "蜀";
  ui.state.view = detailView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(detailView);

  check("U1-06j 右键武将块打开详情模态（标题 / 遮罩 / 正在对话）",
    $("t-modal").hidden === true
      && (() => {
        fireContext(heroBlockOf(0));
        return $("t-modal").hidden === false
          && $("t-modal-backdrop").hidden === false
          && /武将详情/.test($("t-modal-title").textContent);
      })(),
    `hidden=${$("t-modal").hidden} title="${$("t-modal-title").textContent}"`);
  const ownDetail = $("t-modal-body").children[0];
  const ownSections = ownDetail ? ownDetail.children.map((node) => node.className) : [];
  check("U1-06k 详情结构：武将技能在上 → 分割线 → 装备技能在下",
    ownSections.includes("detail-section hero-skills")
      && ownSections.includes("detail-divider")
      && ownSections.includes("detail-section equip-skills")
      && ownSections.indexOf("detail-section hero-skills") < ownSections.indexOf("detail-divider")
      && ownSections.indexOf("detail-divider") < ownSections.indexOf("detail-section equip-skills"),
    ownSections.join(","));
  check("U1-06l 详情读到该座位的技能与描述（武将技 + 装备技）",
    $("t-modal-body").text().includes("己方技")
      && $("t-modal-body").text().includes("己方技的描述。")
      && $("t-modal-body").text().includes("己方装备")
      && !$("t-modal-body").text().includes("敌方技"),
    $("t-modal-body").text().replace(/\s+/g, " ").slice(0, 120));
  const detailRegions = ["table-topbar", "table-stage", "table-bottom", "t-modal"];
  check("U1-06m 详情是检视窗：没有任何区域 inert / aria-hidden；开合不改变只读态（无弹窗 ⇒ 选择仍可用）",
    detailRegions.every((id) => $(id).getAttribute("inert") === undefined
      && $(id).getAttribute("aria-hidden") === undefined)
      && $("table-shell").classList.contains("modal-open")
      && ui.selectionEnabled() === true,
    `regions=${detailRegions.map((id) => `${id}:${$(id).getAttribute("inert")}`).join(",")} openClass=${$("table-shell").classList.contains("modal-open")} enabled=${ui.selectionEnabled()}`);
  check("U1-06n 详情不暂停倒计时，且模态内不显示倒计时（进度条仍在提示条上）",
    (() => {
      ui.state.pendingClock.receivedAt = Date.now() - 35 * 1000;
      ui.renderCountdown(detailView);
      return $("t-countdown").hidden === false
        && $("t-countdown").getAttribute("data-phase") === "yellow"
        && !$("t-modal-body").text().includes("剩余")
        && !$("t-modal-body").children.includes($("t-countdown"));
    })(),
    `hidden=${$("t-countdown").hidden} phase=${$("t-countdown").getAttribute("data-phase")}`);

  // 正文可滚轮翻阅：同一询问里重画（轮询 / 刷新）不把用户读到的位置拉回顶部。
  $("t-modal-body").scrollTop = 77;
  ui.renderPending(detailView);
  check("U1-06n2 详情重画保留滚动位置（可滚轮翻阅不被打断）",
    $("t-modal").hidden === false && $("t-modal-body").scrollTop === 77,
    `hidden=${$("t-modal").hidden} scrollTop=${$("t-modal-body").scrollTop}`);

  const beforeDetailEsc = calls.length;
  fireDocument("keydown", { key: "Escape" });
  check("U1-06o ESC 关闭详情（零提交；没有 inert 可恢复——只读态只看这一步的表面）",
    $("t-modal").hidden === true
      && detailRegions.every((id) => $(id).getAttribute("inert") === undefined)
      && ui.selectionEnabled() === true
      && calls.length === beforeDetailEsc,
    `hidden=${$("t-modal").hidden} enabled=${ui.selectionEnabled()} calls=${calls.length - beforeDetailEsc}`);

  // 旧载荷 / 合成视图没有 `player.skills`：**只对自己的座位**退回顶层 `view.skills`，
  // 不拿自己的技能去冒充别人的（对手仍为空）。
  {
    const oldView = synthView(pending);
    oldView.skills = [detailSkill("hero.demo.own", "旧载荷技")];
    delete oldView.snapshot.players[0].skills;
    ui.state.view = oldView;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(oldView);
    fireContext(heroBlockOf(0));
    check("U1-06o2 旧载荷没有 player.skills 时自己的详情退回顶层 skills",
      $("t-modal").hidden === false && $("t-modal-body").text().includes("旧载荷技"),
      `hidden=${$("t-modal").hidden} body="${$("t-modal-body").text().replace(/\s+/g, " ").slice(0, 80)}"`);
    fireDocument("keydown", { key: "Escape" });
    ui.state.view = detailView;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(detailView);
  }

  // 换座位：滚动位置不沿用（上一个详情的 offset 不该带到另一个武将身上）。
  $("t-modal-body").scrollTop = 77;
  fireContext(heroBlockOf(1));
  const enemyText = $("t-modal-body").text();
  check("U1-06p 右键对手武将块 = 对手详情（读到对手的技能，不串到自己的）",
    $("t-modal").hidden === false
      && enemyText.includes("敌方技")
      && enemyText.includes("敌方武器")
      && enemyText.includes("八卦阵")
      && !enemyText.includes("己方技")
      && $("t-modal-body").scrollTop === 0,
    `${enemyText.replace(/\s+/g, " ").slice(0, 140)} scrollTop=${$("t-modal-body").scrollTop}`);
  const beforeBackdrop = calls.length;
  const detailBackdropClicks = ($("t-modal-backdrop").listeners.click || []).length;
  ($("t-modal-backdrop").listeners.click || []).forEach((fn) => fn({ target: $("t-modal-backdrop") }));
  check("U1-06q 遮罩不再关闭详情（D8 新行为：没有 click 监听；详情保持打开、零提交）",
    detailBackdropClicks === 0
      && $("t-modal").hidden === false
      && /武将详情/.test($("t-modal-title").textContent)
      && calls.length === beforeBackdrop,
    `backdropClicks=${detailBackdropClicks} hidden=${$("t-modal").hidden} title="${$("t-modal-title").textContent}" calls=${calls.length - beforeBackdrop}`);
  // 背景点击不再是出口 ⇒ 关闭按钮就是「详情」的常规出口（ESC 在 U1-06o / U1-06w4 另有覆盖）。
  (($("t-modal-close").listeners.click) || []).forEach((fn) => fn({ target: $("t-modal-close") }));
  check("U1-06q2 关闭按钮仍能关闭详情（零提交）",
    $("t-modal").hidden === true && calls.length === beforeBackdrop,
    `hidden=${$("t-modal").hidden} calls=${calls.length - beforeBackdrop}`);

  // 阵亡武将：座位卡蒙灰，但详情仍开得出来（详情读公开武将面，不读存活实例）。
  detailView.snapshot.players[1].alive = false;
  ui.renderTable();
  fireContext(heroBlockOf(1));
  check("U1-06r 阵亡武将可点开详情（蒙灰 + 详情仍列技能）",
    !!seatBox(1) && seatBox(1).classList.contains("dead")
      && $("t-modal").hidden === false
      && $("t-modal-body").text().includes("阵亡")
      && $("t-modal-body").text().includes("敌方技"),
    `dead=${!!seatBox(1) && seatBox(1).classList.contains("dead")} body="${$("t-modal-body").text().replace(/\s+/g, " ").slice(0, 100)}"`);
  fireDocument("keydown", { key: "Escape" });

  // 右键装备 / 判定 / 手牌不动作；牌桌外壳仍屏蔽浏览器右键菜单。
  {
    const equipSlot = $("t-self-zones").children
      .find((row) => row.getAttribute("data-zone") === "equip").children[0];
    const judgeSlot = $("t-self-zones").children
      .find((row) => row.getAttribute("data-zone") === "judge").children[0];
    const handChip = $("t-hand").children[0];
    fireContext(equipSlot);
    fireContext(judgeSlot);
    fireContext(handChip);
    check("U1-06s 右键装备 / 判定 / 手牌不动作（不开详情）",
      $("t-modal").hidden === true,
      `hidden=${$("t-modal").hidden}`);

    const shell = $("table-shell");
    const prevented = {};
    for (const [name, node] of [["equip", equipSlot], ["judge", judgeSlot], ["hand", handChip]]) {
      const event = { target: node, preventDefault() { prevented[name] = true; } };
      (shell.listeners.contextmenu || []).forEach((fn) => fn(event));
    }
    check("U1-06t 牌桌屏蔽浏览器右键菜单（装备 / 判定 / 手牌都 preventDefault）",
      prevented.equip === true && prevented.judge === true && prevented.hand === true,
      JSON.stringify(prevented));
  }

  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");
  check("U1-06u 详情沿用共享模态的滚动容器；没有单独的阻断遮罩（单层 pointer-events: none 暗化层）",
    /\.modal-body\s*\{[^}]*overflow-y:\s*auto/s.test(css)
      && /\.modal-backdrop\s*\{[^}]*pointer-events:\s*none/s.test(css)
      && !/modal-blocking/.test(css)
      && /\.skill\.unavailable\s*\{/s.test(css)
      && /\.skill\.locked,\s*\.skill\.passive\s*\{/s.test(css),
    `modalBody=${/\.modal-body\s*\{[^}]*overflow-y:\s*auto/s.test(css)} backdropPE=${/\.modal-backdrop\s*\{[^}]*pointer-events:\s*none/s.test(css)} blockingLeft=${/modal-blocking/.test(css)}`);

  // 详情是**叠加**在询问弹窗上的检视窗（U2-06）：开合它不碰 `#t-dialog` 的正文 / 折叠，
  // 也不改只读态；`#t-options` 始终留在询问弹窗里（不再被详情搬走）。
  {
    const arrangePending = {
      kind: "ARRANGE", actor: 0, seq: 9302, timing: "PHASE_PREPARE",
      prompt: "请排列这些牌：给出顺序与前几张置于牌堆顶。",
      revealed: [synthCard(82, "八卦阵"), synthCard(83, "闪")],
      legal_actions: [{ answers_seq: 9302, kind: "ArrangeCards", order: [0, 1], split: 2 }],
      candidate_index: {}, option_seats: {}, context: null,
      preactivations: {}, preactivation: [], arrange: { count: 2, split: "free" },
    };
    const arrangeView = synthView(arrangePending);
    arrangeView.timeout_seconds = 60;
    arrangeView.snapshot.players[0].skills = [detailSkill("hero.demo.own", "己方技")];
    ui.state.view = arrangeView;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(arrangeView);
    check("U1-06w0 排列弹窗先打开（详情叠加测试的前置；详情窗保持关闭）",
      $("t-dialog").hidden === false
        && $("t-dialog-title").textContent === "排列"
        && $("t-modal").hidden === true,
      `dialogHidden=${$("t-dialog").hidden} detailHidden=${$("t-modal").hidden} title="${$("t-dialog-title").textContent}"`);
    const arrangeActionBefore = JSON.stringify(ui.buildAction());
    const dialogBodyBefore = $("t-dialog-body").children.length;

    fireContext(heroBlockOf(0));
    check("U1-06w 详情盖在排列弹窗上：两个节点各自独立，询问弹窗正文 / 只读态原样",
      $("t-dialog").hidden === false
        && $("t-dialog-title").textContent === "排列"
        && $("t-dialog-body").children.includes($("t-options"))
        && $("t-modal").hidden === false
        && /武将详情/.test($("t-modal-title").textContent)
        && $("t-dialog-body").children.length === dialogBodyBefore
        && JSON.stringify(ui.buildAction()) === arrangeActionBefore
        && ui.selectionEnabled() === false,
      `dialog="${$("t-dialog-title").textContent}" detail="${$("t-modal-title").textContent}" optionsInDialog=${$("t-dialog-body").children.includes($("t-options"))} enabled=${ui.selectionEnabled()}`);

    fireDocument("keydown", { key: "Escape" });
    check("U1-06w2 ESC 只关详情：排列弹窗原样恢复（正文仍是排列、无详情残留、仍只读）",
      $("t-modal").hidden === true
        && $("t-dialog").hidden === false
        && $("t-dialog-title").textContent === "排列"
        && $("t-dialog-body").children.includes($("t-options"))
        && !$("t-dialog-body").children.some(
          (node) => (node.className || "").includes("hero-detail"))
        && JSON.stringify(ui.buildAction()) === arrangeActionBefore
        && ui.selectionEnabled() === false,
      `dialogHidden=${$("t-dialog").hidden} title="${$("t-dialog-title").textContent}" bodyClasses=${JSON.stringify($("t-dialog-body").children.map((node) => node.className))} enabled=${ui.selectionEnabled()}`);

    (($("t-dialog-collapse").listeners.click) || []).forEach((fn) => fn({ target: $("t-dialog-collapse") }));
    check("U1-06w3 排列折叠：标题条留下，右端仍给「查看排列」入口",
      $("t-dialog").hidden === false
        && $("t-dialog-body").hidden === true
        && $("btn-show-modal").hidden === false
        && $("btn-show-modal").textContent === "查看排列",
      `dialogHidden=${$("t-dialog").hidden} bodyHidden=${$("t-dialog-body").hidden} reopen=${$("btn-show-modal").hidden}`);

    fireContext(heroBlockOf(0));
    fireDocument("keydown", { key: "Escape" });
    check("U1-06w4 详情在折叠的排列上开合：关闭详情不重开正文（仍只给「查看排列」）",
      $("t-modal").hidden === true
        && $("t-dialog-body").hidden === true
        && $("btn-show-modal").hidden === false,
      `detailHidden=${$("t-modal").hidden} bodyHidden=${$("t-dialog-body").hidden} reopen=${$("btn-show-modal").hidden}`);

    ($("btn-show-modal").listeners.click || []).forEach((fn) => fn({ target: $("btn-show-modal") }));
    check("U1-06w5 「查看排列」展开后正文回来（详情没有把折叠 / 正文抹掉）",
      $("t-dialog").hidden === false
        && $("t-dialog-body").hidden === false
        && $("t-dialog-title").textContent === "排列"
        && $("t-dialog-body").children.includes($("t-options")),
      `dialogHidden=${$("t-dialog").hidden} bodyHidden=${$("t-dialog-body").hidden} title="${$("t-dialog-title").textContent}"`);
  }

  // 真会话（不是合成夹具）：服务端给**每个座位**下发详情清单，读取方（详情）读到它。
  const realDetail = await post("/api/sessions", {
    player_count: 4, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });
  await openSession(realDetail);
  ui.renderTable();
  const realPlayers = realDetail.snapshot.players;
  const realOpponent = realPlayers[1];
  const realSkills = realOpponent.skills || [];
  fireContext(heroBlockOf(1));
  const realBody = $("t-modal-body").text();
  check("U1-06v 真会话：每个座位都带详情技能清单，右键对手读到服务端下发的名字与描述",
    realPlayers.every((player) => Array.isArray(player.skills))
      && realSkills.length > 0
      && realBody.includes(realSkills[0].name)
      && realBody.includes(realSkills[0].description)
      && $("t-modal").hidden === false,
    `seats=${realPlayers.length} opponentSkills=${realSkills.map((skill) => skill.name).join("、")}`);
  fireDocument("keydown", { key: "Escape" });
}

/* ============ U1-07：虚拟牌 / 转化牌（产物卡面 / 悬停源牌 / 火雷颜色 / 将以使用） ============
 *
 * 判据来自票面 `07` 与 spec §2.5：
 * - 虚拟牌：右上「虚」、三空角、中间牌名；纯虚拟牌悬停显示「虚拟牌」；
 * - 转化牌：右上「转」、左上红 / 黑 / 无、中间产物；单源继承花色 / 点数，多源只显示颜色；
 *   悬停列全部来源牌；
 * - 手牌 / 候选仍是源牌（不另加角标）；选中源牌时提示条写「将以【杀】使用」；
 * - 火杀 / 雷杀 / 普通杀都显示「杀」，火杀红、雷杀蓝；转化 / 虚拟产物同样；
 * - 中央 / 处理区只显示产物，不重复显示源牌；历史进事件流。
 *
 * 前半段喂与响应体同形的合成载荷（界面读法只由这一份决定）；后半段用**真会话**走
 * 龙胆 / 丈八 / 八卦三条真实路径。
 */
{
  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) {
      out.push(child);
      walk(child, out);
    }
    return out;
  };
  const hasClass = (child, name) => child.classList.contains(name)
    || String(child.className || "").split(/\s+/).includes(name);
  const byClass = (node, name) => walk(node).filter((child) => hasClass(child, name));
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const mk = (id, name, extra = {}) => ({
    ...synthCard(id, name), slot: null, attribute: "普通", kind: "normal",
    description: `【${name}】的全文描述`, ...extra,
  });
  const sourceDodge = mk(2, "闪", { type: "dodge", suit: "heart", rank: 10, color: "red" });
  const sourceSlash = mk(3, "杀", { type: "slash", suit: "spade", rank: 7, color: "black" });
  const sourceOf = (card) => ({
    ...card, display_name: card.name, attribute: "普通", kind: "normal", source_cards: [],
  });
  const virtual = mk(-9, "闪", {
    type: "dodge", suit: null, rank: null, color: null, display_name: "闪",
    kind: "virtual", virtual_sources: [], source_cards: [], description: "虚拟【闪】的全文描述",
  });
  const single = mk(-10, "杀", {
    type: "slash", suit: "heart", rank: 10, color: "red", display_name: "杀",
    kind: "transformed", virtual_sources: [2], source_cards: [sourceOf(sourceDodge)],
    description: "转化【杀】的全文描述",
  });
  const multi = mk(-11, "杀", {
    type: "slash", suit: null, rank: null, color: null, display_name: "杀",
    kind: "transformed", virtual_sources: [2, 3],
    source_cards: [sourceOf(sourceDodge), sourceOf(sourceSlash)],
    description: "多源转化【杀】的全文描述",
  });
  const fire = mk(-12, "火杀", {
    type: "fire_slash", suit: "heart", rank: 10, color: "red", display_name: "杀",
    attribute: "火焰", kind: "transformed", virtual_sources: [2],
    source_cards: [sourceOf(sourceDodge)], description: "转化火【杀】的全文描述",
  });
  const thunder = mk(-13, "雷杀", {
    type: "thunder_slash", suit: null, rank: null, color: null, display_name: "杀",
    attribute: "雷电", kind: "virtual", virtual_sources: [], source_cards: [],
    description: "虚拟雷【杀】的全文描述",
  });

  const faceView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 900, prompt: "",
    legal_actions: [], candidate_index: {}, virtual_index: {}, context: null, option_seats: {},
  });
  faceView.snapshot.self_hand = [sourceDodge, sourceSlash];
  faceView.snapshot.processing = [virtual, single, multi, fire, thunder];
  ui.state.view = faceView;
  ui.resetSelection();
  ui.renderTable();
  const faces = $("t-processing").children;
  const faceOf = (id) => faces.find((face) => attr(face, "data-card-id") === String(id));

  check("U1-07a 处理区只画产物：五张产物牌，来源实体牌（2 / 3）不再重复出现",
    faces.length === 5
      && faces.every((face) => ["-9", "-10", "-11", "-12", "-13"].includes(attr(face, "data-card-id")))
      && !faces.some((face) => ["2", "3"].includes(attr(face, "data-card-id"))),
    `ids=${faces.map((face) => attr(face, "data-card-id")).join(",")}`);

  const virtualFace = faceOf(-9);
  check("U1-07b 虚拟牌：右上「虚」、左上 / 左下 / 右下空、中间牌名",
    byClass(virtualFace, "corner")[0].textContent === "虚"
      && byClass(virtualFace, "suit").length === 0
      && byClass(virtualFace, "rank").length === 0
      && byClass(virtualFace, "color-mark").length === 0
      && byClass(virtualFace, "cname")[0].textContent === "闪"
      && attr(virtualFace, "data-kind") === "virtual",
    `text="${virtualFace.text()}"`);
  check("U1-07b2 纯虚拟牌悬停显示「虚拟牌」+ 服务端描述",
    virtualFace.getAttribute("title").startsWith("虚拟牌\n")
      && virtualFace.getAttribute("title").includes(virtual.description),
    `title="${virtualFace.getAttribute("title")}"`);

  const singleFace = faceOf(-10);
  check("U1-07c 单源转化：右上「转」、左上红色 + 继承花色 / 点数、中间产物「杀」",
    byClass(singleFace, "corner")[0].textContent === "转"
      && hasClass(byClass(singleFace, "color-mark")[0], "red")
      && byClass(singleFace, "suit")[0].textContent === "♥"
      && byClass(singleFace, "rank")[0].textContent === "10"
      && byClass(singleFace, "cname")[0].textContent === "杀"
      && attr(singleFace, "data-kind") === "transformed",
    `text="${singleFace.text()}"`);

  const multiFace = faceOf(-11);
  check("U1-07d 多源转化：只显示颜色（无色虚线），花色 / 点数为空、角标仍是「转」",
    byClass(multiFace, "corner")[0].textContent === "转"
      && hasClass(byClass(multiFace, "color-mark")[0], "none")
      && byClass(multiFace, "suit").length === 0
      && byClass(multiFace, "rank").length === 0
      && byClass(multiFace, "cname")[0].textContent === "杀",
    `text="${multiFace.text()}"`);
  check("U1-07e 转化牌悬停列出全部来源牌（单源 / 多源都列）",
    singleFace.getAttribute("title").includes("源牌：♥10【闪】")
      && multiFace.getAttribute("title").includes("源牌：♥10【闪】、♠7【杀】")
      && multiFace.getAttribute("title").includes(multi.description),
    `single="${singleFace.getAttribute("title")}" multi="${multiFace.getAttribute("title")}"`);
  check("U1-07e2 转化牌本身的牌名 / 颜色标在左上：红 / 黑 / 无三种类都有 CSS",
    /\.card\.face \.color-mark\.red\s*\{[^}]*var\(--accent\)/s.test(css)
      && /\.card\.face \.color-mark\.black\s*\{[^}]*var\(--ink\)/s.test(css)
      && /\.card\.face \.color-mark\.none\s*\{[^}]*dashed/s.test(css),
    "红 / 黑 / 无 三条规则");

  check("U1-07f 火杀 / 雷杀 / 普通杀都显示「杀」；火红、雷蓝（转化 / 虚拟产物同样）",
    byClass(faceOf(-12), "cname")[0].textContent === "杀"
      && hasClass(faceOf(-12), "attr-fire")
      && byClass(faceOf(-13), "cname")[0].textContent === "杀"
      && hasClass(faceOf(-13), "attr-thunder")
      && /\.card\.face\.attr-fire \.cname\s*\{[^}]*#b3261e/s.test(css)
      && /\.card\.face\.attr-thunder \.cname\s*\{[^}]*#1d4e9c/s.test(css),
    `fire="${byClass(faceOf(-12), "cname")[0].textContent}" thunder="${byClass(faceOf(-13), "cname")[0].textContent}"`);

  check("U1-07g 手牌仍是源实体牌、**不加角标**（两张普通牌都没有「虚 / 转」）",
    $("t-hand").children.length === 2
      && $("t-hand").children.every((card) => byClass(card, "corner").length === 0)
      && $("t-hand").children.map((card) => attr(card, "data-kind")).join(",") === "normal,normal",
    `kinds=${$("t-hand").children.map((card) => attr(card, "data-kind")).join(",")}`);

  // 选中源牌（【闪】）→ 提交的其实是虚拟【杀】；提示条写「将以【杀】使用」。
  const convertPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 901,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ answers_seq: 901, kind: "UseCard", card: { ...single, source_cards: [] }, targets: [1] }],
    candidate_index: {
      "-10": { card: single, pool: [1], combos: [[1]], min_targets: 1, max_targets: 1 },
    },
    virtual_index: { "2": [{ card_id: -10, skill_ids: ["hero.zhaoyun.longdan"] }] },
    context: null, option_seats: {},
  };
  const convertView = synthView(convertPending);
  convertView.snapshot.self_hand = [sourceDodge];
  convertView.snapshot.processing = [];
  ui.state.view = convertView;
  ui.resetSelection();
  ui.toggleFocus(2);
  ui.renderTable();
  ui.renderPending(convertView);
  const hintText = $("t-target-hint").textContent;
  check("U1-07h 选中源牌：提示条写「将以【杀】使用」（不是源牌的牌名）",
    hintText.includes("将以【杀】使用") && !hintText.includes("将以【闪】使用"),
    `hint="${hintText}"`);
  check("U1-07h2 仍是手牌里的源牌被选中；提交的候选是虚拟【杀】",
    $("t-hand").children[0].classList.contains("sel")
      && byClass($("t-hand").children[0], "corner").length === 0
      && ui.buildAction() && String(ui.buildAction().card.card_id) === "-10",
    `focus=${ui.state.focusCardId} card=${ui.buildAction() && ui.buildAction().card.card_id}`);

  // 回放：来源牌进处理区的那条 card_move 也画产物（快照的 processing 是产物映射的来源）。
  faceView.snapshot.processing = [];
  ui.state.view = faceView;
  ui.resetSelection();
  ui.renderTable();
  const playbackBody = {
    session_id: "u1-07-playback", seed: 1, human_seat: 0, finished: false, timeout_seconds: null,
    heroes: [], skills: [], pending: null, cursor: 3, log_seq: 0, narrative: [],
    snapshot: { ...faceView.snapshot, processing: [single] },
    records: [{
      kind: "delta", delta_type: "card_move", seq: 2,
      cards: [{ ...sourceDodge }],
      from_zone: { zone: "hand", seat: 0 }, to_zone: { zone: "processing", seat: null },
      reason: "USE", owner: 0,
    }],
  };
  ui.applyViewRaw(playbackBody);
  ui.stepPlayback(true);
  check("U1-07i 回放期间来源牌进处理区 = 画产物（不先闪两张源牌）",
    ui.state.effectCards.length === 1
      && String(ui.state.effectCards[0].card_id) === "-10"
      && ui.state.effectCards[0].source === 0,
    `effect=${JSON.stringify(ui.state.effectCards.map((card) => card.card_id))}`);
  ui.flushPlayback();
  check("U1-07i2 快照落地后处理区仍是产物（来源牌不重复显示）",
    $("t-processing").children.length === 1
      && attr($("t-processing").children[0], "data-card-id") === "-10",
    `ids=${$("t-processing").children.map((child) => attr(child, "data-card-id")).join(",")}`);

  /* ---- 真会话：龙胆 / 丈八 / 八卦各一条 ---- */
  const submitEquip = async (typeId) => {
    const pending = ui.state.view.pending;
    if (!pending) return false;
    const hit = Object.entries(pending.candidate_index || {}).find(
      ([, entry]) => entry.card && entry.card.type === typeId);
    if (!hit) return false;
    ui.resetSelection();
    ui.toggleFocus(Number(hit[0]));
    ui.renderPending(ui.state.view);
    if (!ui.buildAction()) return false;
    await ui.submitAction();
    return true;
  };
  const passResponse = async () => {
    const pending = ui.state.view.pending;
    if (!pending) return false;
    const index = (pending.legal_actions || []).findIndex((action) => action.kind === "Pass");
    if (index < 0) return false;
    ui.resetSelection();
    ui.state.selectedOption = index;
    await ui.submitAction();
    return true;
  };

  // ① 龙胆：seed 5 的赵云起始手牌里有【闪】；出牌阶段真会话给出龙胆清单。
  await newSession(5);
  let longdanPending = null;
  for (let step = 0; step < 40; step += 1) {
    const pending = ui.state.view.pending;
    if (!pending) break;
    if (pending.actor === 0 && pending.kind === "PLAY_PHASE"
      && pending.preactivations && pending.preactivations["hero.zhaoyun.longdan"]) {
      longdanPending = pending;
      break;
    }
    if (!(await passResponse())) await advanceOnce();
  }
  const longdanHandDodge = (ui.state.view.snapshot.self_hand || [])
    .find((card) => card.type === "dodge");
  const longdanButton = $("t-skills").children.find(
    (node) => node.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
  if (longdanButton) (longdanButton.listeners.click || []).forEach((fn) => fn({ target: longdanButton }));
  const longdanOptions = ui.state.preactivation
    ? (ui.preactivationOptions(ui.state.preactivation, 0) || [])
    : [];
  check("U1-07j 真会话【龙胆】：技能按钮进清单，来源步列出真手牌里的【闪】",
    Boolean(longdanPending) && Boolean(longdanButton)
      && Boolean(longdanHandDodge)
      && longdanOptions.map(String).includes(String(longdanHandDodge.card_id)),
    `dodge=${longdanHandDodge && longdanHandDodge.card_id} options=${longdanOptions.join(",")}`);
  if (longdanHandDodge) {
    ui.togglePreativationCard(Number(longdanHandDodge.card_id));
    ui.renderPending(ui.state.view);
  }
  check("U1-07j2 真会话【龙胆】：选中真手牌的【闪】后提示条写「将以【杀】使用」",
    Boolean(longdanHandDodge) && $("t-target-hint").textContent.includes("将以【杀】使用"),
    `hint="${$("t-target-hint").textContent}" products=${longdanPending && (longdanPending.transform_products || []).length}`);
  ui.cancelOrClear();

  // ② 丈八蛇矛：seed 9 起始手牌有它；装备后真会话给出两来源步的转化清单。
  await newSession(9);
  let equippedSerpent = false;
  for (let step = 0; step < 80 && !equippedSerpent; step += 1) {
    const pending = ui.state.view.pending;
    if (!pending) break;
    if (pending.actor === 0 && pending.kind === "PLAY_PHASE"
      && await submitEquip("serpent_spear")) {
      equippedSerpent = true;
      break;
    }
    if (!(await passResponse())) await advanceOnce();
  }
  const serpentPending = ui.state.view.pending;
  const serpentEquip = (ui.state.view.snapshot.players[0].equip || [])
    .map((card) => card.type);
  const serpentSteps = (serpentPending && serpentPending.preactivations
    && serpentPending.preactivations["equip.serpent_spear"]) || null;
  const serpentButton = $("t-skills").children.find(
    (node) => node.getAttribute("data-skill-id") === "equip.serpent_spear");
  check("U1-07k 真会话【丈八】：seed 9 装备后服务端下发两来源步清单，技能按钮可点",
    equippedSerpent && serpentEquip.includes("serpent_spear")
      && Boolean(serpentSteps) && serpentSteps.length >= 1
      && serpentSteps[0].min === 2 && serpentSteps[0].max === 2
      && Boolean(serpentButton) && serpentButton.disabled === false
      && serpentButton.getAttribute("data-preactivation") === "2",
    `equipped=${serpentEquip.join(",")} steps=${serpentSteps && JSON.stringify(serpentSteps.map((s) => [s.name, s.min, s.max]))} button=${serpentButton && serpentButton.disabled}`);
  if (serpentButton) (serpentButton.listeners.click || []).forEach((fn) => fn({ target: serpentButton }));
  const serpentOptions = ui.state.preactivation
    ? (ui.preactivationOptions(ui.state.preactivation, 0) || []).map(String)
    : [];
  const serpentProducts = (serpentPending && serpentPending.transform_products) || [];
  // 真会话的产物 = 一张虚拟【杀】+ 两张来源牌面；挑一组两来源都在清单里的。
  const serpentProduct = serpentProducts.find(
    (candidate) => (candidate.source_cards || []).length === 2
      && candidate.source_cards.every(
        (card) => serpentOptions.includes(String(card.card_id))));
  const serpentPair = serpentProduct
    ? serpentProduct.source_cards.map((card) => Number(card.card_id))
    : [];
  for (const id of serpentPair) ui.togglePreativationCard(id);
  if (serpentProduct) ui.renderPending(ui.state.view);
  const chosenSources = ui.state.preactivation
    ? (ui.state.preactivation.answers[0].values || []).map(Number)
    : [];
  check("U1-07k2 真会话【丈八】：产物带两张来源牌面；选齐来源后提示条写「将以【杀】使用」",
    Boolean(serpentProduct)
      && chosenSources.slice().sort().join(",") === serpentPair.slice().sort().join(",")
      && serpentPair.every((id) => serpentOptions.includes(String(id)))
      && $("t-target-hint").textContent.includes("将以【杀】使用"),
    `sources=${serpentPair.join(",")} chosen=${chosenSources.join(",")} hint="${$("t-target-hint").textContent}"`);

  // ③ 八卦阵：seed 7 的起始手牌有它；装上后真会话走到需要【闪】的窗口，候选里有八卦。
  await newSession(7);
  let equippedBagua = false;
  let sawBaguaDodge = false;
  for (let step = 0; step < 160; step += 1) {
    const pending = ui.state.view.pending;
    if (!pending) break;
    if (pending.actor === 0 && pending.kind === "PLAY_PHASE" && !equippedBagua
      && await submitEquip("eight_diagrams")) {
      equippedBagua = true;
      continue;
    }
    if (pending.actor === 0 && pending.kind === "DODGE"
      && (pending.legal_actions || []).some(
        (action) => action.kind === "ActivateSkill" && action.skill_id === "equip.eight_diagrams")) {
      sawBaguaDodge = true;
      break;
    }
    if (!(await passResponse())) await advanceOnce();
  }
  const baguaPending = ui.state.view.pending;
  const baguaEquip = (ui.state.view.snapshot.players[0].equip || []).map((card) => card.type);
  check("U1-07l 真会话【八卦】：seed 7 装上后走到 DODGE 窗口，候选里出现八卦阵的 ActivateSkill",
    equippedBagua && baguaEquip.includes("eight_diagrams") && sawBaguaDodge
      && (baguaPending.legal_actions || []).some(
        (action) => action.kind === "ActivateSkill" && action.skill_id === "equip.eight_diagrams"),
    `equip=${baguaEquip.join(",")} dodge=${sawBaguaDodge} legal=${(baguaPending.legal_actions || []).map((a) => a.kind + ":" + (a.skill_id || "")).join(",")}`);
  const baguaIndex = (baguaPending.legal_actions || []).findIndex(
    (action) => action.kind === "ActivateSkill" && action.skill_id === "equip.eight_diagrams");
  if (baguaIndex >= 0) {
    ui.resetSelection();
    ui.state.selectedOption = baguaIndex;
    await ui.submitAction();
  }
  check("U1-07l2 真会话【八卦】：选中八卦阵候选并提交，服务端接受（没有 4xx 拒绝）",
    baguaIndex >= 0 && ui.state.lastRejectReason === ""
      && Boolean(ui.state.view && (ui.state.view.finished || ui.state.view.pending)),
    `index=${baguaIndex} reject="${ui.state.lastRejectReason}" pending=${ui.state.view && (ui.state.view.pending && ui.state.view.pending.kind)}`);

  ui.state.view = faceView;
  ui.resetSelection();
  ui.renderTable();
}

/* ============ U2-01 顶栏接管导航（spec D7） ============
 *
 * 判据（票面验收）：
 * - 三页都不再有全局 header（品牌文本 / 导航 / #status），`<title>` = 「SGS · 三国杀」；
 * - 牌桌顶栏右端顺序 = 牌堆 / 弃牌 / 设置 / 开局 / 牌桌 / 回放，旧「回放」图标按钮消失；
 * - 开局页 / 回放页各有一条同款顶栏，导航三键与牌桌同序、当前页高亮；
 * - 无会话时三处「牌桌」禁用，有会话可点；回放页能一键回开局 / 牌桌；
 * - 16:9 壳宽 = min(100%, 100vh × 16/9)（1080p 吃满 1920、720p 无横向溢出）。
 *
 * 放在文件末尾：这一段会真的调 `show()` 切页（前面的用例只断言各自的渲染），
 * 放最后不会把别的用例留在非预期页面上。
 */
{
  const bodyHtml = html.slice(html.indexOf("<body>") + "<body>".length, html.indexOf("<script"));
  const firstBodyTag = bodyHtml.replace(/<!--[\s\S]*?-->/g, "").trimStart();
  const tableStart = html.indexOf('id="view-table"');
  const replayStart = html.indexOf('id="view-replay"');
  const lobbyView = html.slice(html.indexOf('id="view-lobby"'), tableStart);
  const replayView = html.slice(replayStart, html.indexOf("<script"));
  const navIds = (prefix) => ["lobby", "table", "replay"].map((name) => `${prefix}nav-${name}`);
  const positionsIn = (text, ids) => ids.map((id) => text.indexOf(`id="${id}"`));
  const inOrder = (positions) => positions.every((pos, i) => pos >= 0 && (i === 0 || pos > positions[i - 1]));
  const isOn = (id) => $(id).classList.contains("on");

  check("U2-01a 三页都没有全局 header；<title> = SGS · 三国杀；内容从视口顶部开始",
    /<title>SGS · 三国杀<\/title>/.test(html)
      && firstBodyTag.startsWith("<main")
      && !/<header[^>]*class="bar"/.test(html)
      && !/id="status"/.test(html)
      && !html.includes("极简界面"),
    `title=${(html.match(/<title>([^<]*)<\/title>/) || [])[1]} first=${firstBodyTag.slice(0, 40)}`);

  const tableTopbar = html.slice(tableStart, html.indexOf('id="table-stage"', tableStart));
  const tableRightOrder = ["t-deck", "t-discard", "btn-settings", "nav-lobby", "nav-table", "nav-replay"]
    .map((id) => tableTopbar.indexOf(`id="${id}"`));
  check("U2-01b 牌桌顶栏右端顺序 = 牌堆 / 弃牌 / 设置 / 开局 / 牌桌 / 回放；旧「回放」图标按钮已删",
    inOrder(tableRightOrder)
      && tableTopbar.indexOf('class="topbar-right"') < tableRightOrder[0]
      && !tableTopbar.includes('id="btn-go-replay"'),
    `顺序=${tableRightOrder.join(",")}`);

  const lobbyTopbar = lobbyView.slice(0, lobbyView.indexOf('id="lobby-form"'));
  const replayTopbar = replayView.slice(0, replayView.indexOf('id="r-empty"'));
  check("U2-01c 开局页 / 回放页各有一条同款顶栏，导航三键与牌桌同序",
    inOrder(positionsIn(lobbyTopbar, navIds("lobby-")))
      && inOrder(positionsIn(replayTopbar, navIds("replay-")))
      && /class="[^"]*\btopbar\b/.test(lobbyTopbar)
      && /class="[^"]*\btopbar\b/.test(replayTopbar)
      && /class="[^"]*\btopbar\b/.test(tableTopbar),
    `lobby=${positionsIn(lobbyTopbar, navIds("lobby-")).join(",")} replay=${positionsIn(replayTopbar, navIds("replay-")).join(",")}`);

  ui.show("lobby");
  const lobbyOn = ["nav-lobby", "lobby-nav-lobby", "replay-nav-lobby"].every(isOn)
    && ["nav-table", "lobby-nav-table", "replay-nav-table",
      "nav-replay", "lobby-nav-replay", "replay-nav-replay"].every((id) => !isOn(id));
  ui.show("replay");
  const replayOn = ["nav-replay", "lobby-nav-replay", "replay-nav-replay"].every(isOn)
    && ["nav-lobby", "lobby-nav-lobby", "replay-nav-lobby"].every((id) => !isOn(id));
  check("U2-01d 当前页在导航里高亮：三处顶栏同步（开局 / 回放）", lobbyOn && replayOn,
    `lobby=${lobbyOn} replay=${replayOn}`);

  const savedSession = ui.state.sessionId;
  const savedActive = $("view-replay").hidden === false ? "replay"
    : $("view-lobby").hidden === false ? "lobby" : "table";
  const tableNavIds = ["nav-table", "lobby-nav-table", "replay-nav-table"];
  ui.state.sessionId = null;
  ui.show("lobby");
  const disabledWithoutSession = tableNavIds.every((id) => $(id).disabled === true);
  ui.state.sessionId = "stub-session";
  ui.show("lobby");
  const enabledWithSession = tableNavIds.every((id) => $(id).disabled === false);
  ui.show("replay");
  (($("replay-nav-lobby").listeners.click) || []).forEach((fn) => fn({ target: $("replay-nav-lobby") }));
  const backToLobby = $("view-lobby").hidden === false && $("view-replay").hidden === true
    && isOn("nav-lobby");
  ui.show("replay");
  (($("replay-nav-table").listeners.click) || []).forEach((fn) => fn({ target: $("replay-nav-table") }));
  const backToTable = $("view-table").hidden === false && $("view-replay").hidden === true;
  check("U2-01e 无会话时三处「牌桌」禁用；有会话可点；回放页能一键回开局 / 牌桌",
    disabledWithoutSession && enabledWithSession && backToLobby && backToTable,
    `无会话禁用=${disabledWithoutSession} 有会话可点=${enabledWithSession} 回开局=${backToLobby} 回牌桌=${backToTable}`);
  ui.state.sessionId = savedSession;
  ui.show(savedActive);

  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");
  const shellRule = css.match(/\.table-shell\s*\{[^}]*\}/s);
  const shellWidth = (vw, vh) => Math.min(vw, (vh * 16) / 9);
  check("U2-01f 16:9 壳宽 = min(100%, 100vh × 16/9)：1080p 吃满 1920、720p 无横向溢出",
    !!shellRule
      && /width:\s*min\(100%,\s*calc\(100vh \* 16 \/ 9\)\)/.test(shellRule[0])
      && !shellRule[0].includes("100vh - 64px")
      && shellWidth(1920, 1080) === 1920
      && shellWidth(1280, 720) === 1280
      && /\.table-view\s*\{[^}]*max-width:\s*none/s.test(css),
    shellRule ? (shellRule[0].match(/width:[^;]+;/) || ["(无 width)"])[0] : "找不到 .table-shell 规则");

  // 当前页高亮的红底按钮被 hover 时，通用 button:hover 的红色文字会与红底同色（实测"牌桌"消失）；
  // 顶栏导航因此必须有一条 hover 仍白字的覆盖规则（U2-01 实机截图时发现）。
  check("U2-01g 当前页高亮按钮 hover 时仍白字（红底红字不可读）",
    /\.tab\.on:hover:not\(:disabled\)\s*\{[^}]*color:\s*#fff/s.test(css),
    (css.match(/\.tab\.on:hover[^{]*\{[^}]*\}/s) || ["(无覆盖规则)"])[0].replace(/\s+/g, " "));
}

/* ============ U2-05 只读态（spec D8）：选择 no-op + 检视照常 ============
 *
 * 判据（票面验收）：
 * - 询问弹窗打开时点手牌 / 座位 / 槽位不产生选择态，提交键不变、目标高亮不出现；
 * - 只读态里检视照常：悬停 title、右键武将详情、折叠（查看排列）、顶栏 / 事件流；
 * - 收起后仍是只读；没有弹窗时选择恢复；键盘（回车 / 空格）走同一批选择函数；
 * - 遮罩 `pointer-events: none`（不吞指针），穿透到牌桌的点击由 `selectionEnabled()` 拦下；
 * - 预发动清单是**牌桌表面**（D2 C 形），不是只读：打开时点牌点座仍生效（回归守卫）；
 * - 武将详情是叠加的检视窗：开合不改变只读态，关闭后底下弹窗原样恢复。
 *
 * 当前唯一"表面是弹窗"的步骤是 ARRANGE（票 06 把四类模板接进来时只改 `stepDialogKind`）。
 * 断言的是**外部可观察行为**（DOM / 选择态 / 提交键），不钉内部实现。
 */
{
  ui.state.sessionId = null;   // 全程合成视图：不碰上一段的真会话
  ui.show("table");

  const arrangeCardA = { ...synthCard(82, "八卦阵"), description: "【八卦阵】的全文描述" };
  const arrangeCardB = { ...synthCard(83, "闪"), description: "【闪】的全文描述" };
  const arrangePending = (seq = 9501) => ({
    kind: "ARRANGE", actor: 0, seq, timing: "PHASE_PREPARE",
    prompt: "请排列这些牌：给出顺序与前几张置于牌堆顶。",
    prompt_display: { text: "请排列这些牌。", default_label: "超时按当前排列提交" },
    revealed: [arrangeCardA, arrangeCardB],
    legal_actions: [{ answers_seq: seq, kind: "ArrangeCards", order: [0, 1], split: 2 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
    arrange: { count: 2, split: "free" },
  });
  const arrangeView = () => {
    const view = synthView(arrangePending());
    view.timeout_seconds = 60;
    view.snapshot.self_hand = [{ ...synthCard(1, "杀"), description: "【杀】的全文描述" }];
    view.snapshot.players[0].equip = [
      { ...synthCard(5, "青釭剑"), slot: "weapon", description: "【青釭剑】的全文描述" },
    ];
    return view;
  };
  const heroBlockOf = (seat) => {
    if (Number(seat) === Number(ui.state.view.snapshot.seat)) return $("t-self-hero");
    const box = seatBox(seat);
    return box ? box.children.find((node) => (node.className || "").split(" ").includes("hero-block")) || null : null;
  };
  const fireContext = (node) => {
    const event = { target: node, prevented: false, stopPropagation() {} };
    event.preventDefault = () => { event.prevented = true; };
    if (node) (node.listeners.contextmenu || []).forEach((fn) => fn(event));
    return event;
  };
  /** 键盘激活一座/一张：手牌 / 槽位是 `<button>`（原生回车 = 一次 click）；座位是 `<div>`
   *  （键盘入口 = `bindSeatActivation` 挂的 keydown）。按真浏览器各走一条，别双触发。 */
  const pressEnter = (node) => {
    if (!node) return;
    if (node.tagName === "BUTTON") {
      (node.listeners.click || []).forEach((fn) => fn({ target: node, type: "click" }));
      return;
    }
    (node.listeners.keydown || []).forEach((fn) => fn({
      key: "Enter", target: node, preventDefault() {},
    }));
  };
  const selectionFingerprint = () => JSON.stringify({
    focus: ui.state.focusCardId,
    targets: ui.state.selectedTargets,
    option: ui.state.selectedOption,
    picks: ui.state.picks,
    pre: ui.state.preactivation,
    submit: $("btn-submit").disabled,
  });
  const seatHighlighted = (seat) => {
    const box = seatBox(seat);
    return !!box && (box.classList.contains("targetable") || box.classList.contains("tsel")
      || box.classList.contains("req"));
  };
  const handChip = () => $("t-hand").children[0];
  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");

  /* ---- ① 排列弹窗打开 = 只读态 ---- */
  let view = arrangeView();
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  check("U2-05a 排列弹窗 = 只读态：stepDialogKind=arrange、selectionEnabled false、暗化层可见、全程无 inert",
    ui.stepDialogKind(view.pending) === "arrange"
      && ui.selectionEnabled() === false
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "排列"
      && $("t-modal").hidden === true
      && $("t-modal-backdrop").hidden === false
      && $("table-shell").classList.contains("modal-open")
      && ["table-topbar", "table-stage", "table-bottom", "t-dialog", "t-modal"].every(
        (id) => $(id).getAttribute("inert") === undefined
          && $(id).getAttribute("aria-hidden") === undefined),
    `surface=${ui.stepDialogKind(view.pending)} enabled=${ui.selectionEnabled()} dialogHidden=${$("t-dialog").hidden} detailHidden=${$("t-modal").hidden} backdropHidden=${$("t-modal-backdrop").hidden}`);

  /* ---- ② 点手牌 / 座位 / 槽位：不产生选择态、提交键不变、目标高亮不出现 ---- */
  const beforeBlocked = selectionFingerprint();
  await clickAndWait(handChip());
  await clickAndWait(seatBox(1));
  pressEnter(handChip());
  pressEnter(seatBox(1));
  // 每个选择入口的直接调用也一并钉住（它们还有键盘 / 脚本调用方）：
  ui.toggleFocus(1);
  ui.selectTarget(1);
  ui.selectOptionSeat(1);
  ui.toggleDiscardPick(1);
  ui.toggleSlotPick(view.pending, 0);
  ui.toggleProvidePick(0);
  ui.togglePreativationCard(1);
  ui.selectPreativationSeat(1);
  check("U2-05b 弹窗打开时点手牌 / 座位 / 槽位不产生选择态：焦点 / 目标 / picks / 选中项都不动、提交键不变",
    selectionFingerprint() === beforeBlocked
      && ui.state.focusCardId === null
      && ui.state.selectedTargets.length === 0
      && ui.state.picks.length === 0,
    `now=${selectionFingerprint()} before=${beforeBlocked}`);
  check("U2-05b2 目标高亮不出现：任何座位都不是可指定 / 已选 / 必选",
    [0, 1, 2].every((seat) => !seatHighlighted(seat)),
    [0, 1, 2].map((seat) => `${seat}:${seatHighlighted(seat)}`).join(" "));

  /* ---- ③ 检视照常：悬停 title / 右键详情 ---- */
  check("U2-05c 只读态里悬停照常：手牌与装备槽的 title 仍是服务端描述",
    handChip().getAttribute("title") === "【杀】的全文描述"
      && (() => {
        const slot = selfEquipChip();
        return !!slot && /青釭剑/.test(slot.getAttribute("title") || "");
      })(),
    `hand="${handChip().getAttribute("title")}" equip="${selfEquipChip() && selfEquipChip().getAttribute("title")}"`);
  // 检视还包括顶栏 / 事件流：弹窗开着时都不 inert、都能点（D8）。
  (($("t-log-toggle").listeners.click) || []).forEach((fn) => fn({ target: $("t-log-toggle") }));
  const logOpenedWhileReadonly = $("t-log-panel").hidden === false;
  (($("t-log-toggle").listeners.click) || []).forEach((fn) => fn({ target: $("t-log-toggle") }));
  check("U2-05c2 只读态里事件流照常展开 / 收起；顶栏导航不在 inert 里",
    logOpenedWhileReadonly
      && $("t-log-panel").hidden === true
      && $("t-dialog").hidden === false
      && $("table-topbar").getAttribute("inert") === undefined
      && $("nav-lobby").getAttribute("inert") === undefined,
    `opened=${logOpenedWhileReadonly} closedAgain=${$("t-log-panel").hidden} dialogHidden=${$("t-dialog").hidden}`);
  fireContext(heroBlockOf(1));
  check("U2-05d 只读态里右键武将块照开详情（检视窗开合不改变只读态）",
    $("t-modal").hidden === false
      && /武将详情/.test($("t-modal-title").textContent)
      && ui.selectionEnabled() === false,
    `hidden=${$("t-modal").hidden} title="${$("t-modal-title").textContent}" enabled=${ui.selectionEnabled()}`);
  const arrangeActionBefore = JSON.stringify(ui.buildAction());
  fireDocument("keydown", { key: "Escape" });
  check("U2-05d2 关闭详情后排列弹窗原样恢复（正文仍是排列、已摆顺序在，仍只读）",
    $("t-modal").hidden === true
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-body").children.includes($("t-options"))
      && !$("t-dialog-body").children.some(
        (node) => (node.className || "").includes("hero-detail"))
      && JSON.stringify(ui.buildAction()) === arrangeActionBefore
      && ui.selectionEnabled() === false,
    `detailHidden=${$("t-modal").hidden} title="${$("t-dialog-title").textContent}" bodyClasses=${JSON.stringify($("t-dialog-body").children.map((node) => node.className))} action=${JSON.stringify(ui.buildAction())}`);

  /* ---- ④ 键盘：blocked 态回车不选；卡面是 button（可聚焦）但 click 被拦 ---- */
  check("U2-05e 阻断态键盘激活不产生选择：手牌是 <button>（Tab 到它再回车 = 一次 click）却无焦点；座位无键盘入口",
    handChip().tagName === "BUTTON"
      && ui.state.focusCardId === null
      && ui.state.selectedTargets.length === 0
      && (seatBox(1).listeners.keydown || []).length === 0
      && seatBox(1).getAttribute("tabindex") === undefined,
    `tag=${handChip().tagName} focus=${ui.state.focusCardId} seatKeys=${(seatBox(1).listeners.keydown || []).length} tabindex=${seatBox(1).getAttribute("tabindex")}`);

  /* ---- ⑤ 折叠（只撤正文）仍是只读；检视照常；不擅自重开；ESC 不是出口 ---- */
  const collapseClicksBefore = ($("t-dialog-collapse").listeners.click || []).length;
  (($("t-dialog-collapse").listeners.click) || []).forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  check("U2-05f 折叠按钮把弹窗缩成标题条（标题 + 步骤提示 + 展开）；仍是只读、详情窗不掺和",
    collapseClicksBefore === 1
      && $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-hint").textContent.length > 0
      && $("t-dialog-collapse").textContent === "展开"
      && $("btn-show-modal").hidden === false
      && $("t-modal").hidden === true
      && ui.selectionEnabled() === false,
    `bodyHidden=${$("t-dialog-body").hidden} collapse="${$("t-dialog-collapse").textContent}" hint="${$("t-dialog-hint").textContent}" reopen=${$("btn-show-modal").hidden} enabled=${ui.selectionEnabled()}`);
  const collapsedActionBefore = JSON.stringify(ui.buildAction());
  const beforeDismissed = selectionFingerprint();
  fireDocument("keydown", { key: "Escape" });
  await clickAndWait(handChip());
  pressEnter(handChip());
  pressEnter(seatBox(1));
  check("U2-05f2 折叠后点牌点座仍不生效、ESC 也不关 / 不展开（折叠只改遮挡，不改只读态）",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && selectionFingerprint() === beforeDismissed
      && JSON.stringify(ui.buildAction()) === collapsedActionBefore
      && ui.state.focusCardId === null
      && ui.state.selectedTargets.length === 0,
    `now=${selectionFingerprint()} action=${JSON.stringify(ui.buildAction())}`);
  check("U2-05f3 折叠后悬停 title 仍可用（检视不随折叠关闭）",
    handChip().getAttribute("title") === "【杀】的全文描述",
    `hand="${handChip().getAttribute("title")}"`);
  fireContext(heroBlockOf(0));
  check("U2-05f4 折叠后右键详情仍可开（检视窗叠在标题条上）",
    $("t-modal").hidden === false
      && /武将详情/.test($("t-modal-title").textContent)
      && $("t-dialog-body").hidden === true,
    `detailHidden=${$("t-modal").hidden} title="${$("t-modal-title").textContent}" bodyHidden=${$("t-dialog-body").hidden}`);
  fireDocument("keydown", { key: "Escape" });
  check("U2-05f5 关闭详情后不擅自展开已折叠的排列：仍只给「查看排列」入口",
    $("t-modal").hidden === true
      && $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && $("btn-show-modal").hidden === false,
    `detailHidden=${$("t-modal").hidden} bodyHidden=${$("t-dialog-body").hidden} reopen=${$("btn-show-modal").hidden}`);
  (($("btn-show-modal").listeners.click) || []).forEach((fn) => fn({ target: $("btn-show-modal") }));
  check("U2-05f6 「查看排列」展开：仍是只读态、排列正文回到窗口里",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-body").children.includes($("t-options"))
      && $("btn-show-modal").hidden === true
      && ui.selectionEnabled() === false,
    `bodyHidden=${$("t-dialog-body").hidden} title="${$("t-dialog-title").textContent}" reopen=${$("btn-show-modal").hidden} enabled=${ui.selectionEnabled()}`);

  /* ---- ⑥ 遮罩不吞指针：没有自己的 click；穿透到牌桌的点击由只读判据拦下 ---- */
  const beforePassThrough = selectionFingerprint();
  const backdropClicks = ($("t-modal-backdrop").listeners.click || []).length;
  (($("t-modal-backdrop").listeners.click) || []).forEach((fn) => fn({ target: $("t-modal-backdrop") }));
  // 真实点击穿过遮罩落到牌桌手牌上（同一份可点节点）：
  await clickAndWait(handChip());
  check("U2-05g 遮罩不吞指针：无 click 监听 + CSS pointer-events: none；穿透的点击被只读判据拦下、弹窗不被点关",
    backdropClicks === 0
      && /\.modal-backdrop\s*\{[^}]*pointer-events:\s*none/s.test(css)
      && $("t-dialog").hidden === false
      && selectionFingerprint() === beforePassThrough,
    `backdropClicks=${backdropClicks} peNone=${/\.modal-backdrop\s*\{[^}]*pointer-events:\s*none/s.test(css)} dialogHidden=${$("t-dialog").hidden} now=${selectionFingerprint()}`);
  // 收尾：让后面几个状态从干净的一面开始（不能留一个开着的弹窗）。
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  /* ---- ⑦ 没有弹窗时选择恢复（同一条判据的两个方向） ---- */
  const pickCard = { ...synthCard(1, "杀"), description: "【杀】的全文描述" };
  const pickPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9502, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "UseCard", answers_seq: 9502, card: pickCard, targets: [1] }],
    candidate_index: {
      1: { card: pickCard, pool: [1, 2], combos: [[1], [2]], min_targets: 1, max_targets: 1 },
    },
    option_seats: {}, context: null, preactivations: {}, preactivation: [],
  };
  const pickView = synthView(pickPending);
  pickView.snapshot.self_hand = [pickCard];
  ui.state.view = pickView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(pickView);
  check("U2-05h 没有弹窗（牌桌表面）时 selectionEnabled 为真，选择入口全部恢复",
    ui.stepDialogKind(pickPending) === null && ui.selectionEnabled() === true,
    `surface=${ui.stepDialogKind(pickPending)} enabled=${ui.selectionEnabled()}`);
  pressEnter(handChip());
  check("U2-05h2 键盘回车点手牌 = 设焦点（非只读态）", ui.state.focusCardId === 1,
    `focus=${ui.state.focusCardId}`);
  const pickSeat = seatBox(1);
  check("U2-05h3 可点的座位是键盘可达的（tabindex=0 + role=button + keydown）",
    pickSeat.getAttribute("tabindex") === "0"
      && pickSeat.getAttribute("role") === "button"
      && (pickSeat.listeners.keydown || []).length === 1,
    `tabindex=${pickSeat.getAttribute("tabindex")} role=${pickSeat.getAttribute("role")} keys=${(pickSeat.listeners.keydown || []).length}`);
  pressEnter(pickSeat);
  check("U2-05h4 键盘回车点座位 = 选中目标，提交键启用（与鼠标同一条 selectTarget）",
    JSON.stringify(ui.state.selectedTargets) === "[1]"
      && $("btn-submit").disabled === false,
    `targets=${JSON.stringify(ui.state.selectedTargets)} submitDisabled=${$("btn-submit").disabled}`);

  /* ---- ⑧ 回归守卫：预发动清单打开时牌桌仍接选择（C 形牌桌表面，不得被只读判据误伤） ---- */
  const preView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 9503,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9503 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {
      "demo.modal": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0, pool: [{ when: [], options: ["1"] }] },
        { name: "seat", zones: [], min: 1, max: 1, order: 1, pool: [{ when: [], options: ["seat:1", "seat:2"] }] },
      ],
    },
    preactivation: [],
  });
  preView.snapshot.self_hand = [{ ...synthCard(1, "杀"), description: "【杀】的全文描述" }];
  preView.skills = [{
    skill_id: "demo.modal", name: "示例模态", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "", convert_sources: [] },
  }];
  ui.state.view = preView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(preView);
  const modalSkill = $("t-skills").children.find(
    (node) => node.getAttribute("data-skill-id") === "demo.modal");
  (modalSkill.listeners.click || []).forEach((fn) => fn({ target: modalSkill }));
  check("U2-05i 预发动清单打开仍是牌桌表面：弹窗在、selectionEnabled 为真（不是只读）",
    $("t-dialog").hidden === false
      && ui.state.dialog.kind === "preactivation"
      && /预发动/.test($("t-dialog-title").textContent)
      && ui.selectionEnabled() === true,
    `dialogKind=${ui.state.dialog.kind} title="${$("t-dialog-title").textContent}" enabled=${ui.selectionEnabled()}`);
  await clickAndWait(handChip());
  check("U2-05i2 预发动选牌步：点手牌仍生效（C 形：点自己的牌）",
    JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[0].values) === "[1]",
    JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[0].values));
  const nextStep = $("t-options").children.find((node) => node.textContent === "下一步");
  (nextStep.listeners.click || []).forEach((fn) => fn({ target: nextStep }));
  await clickAndWait(seatBox(1));
  check("U2-05i3 预发动选座步：点座位仍生效（C 形：点自己的座位）",
    JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[1].values) === "[1]",
    JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[1].values));
  const beforePreCancel = calls.length;
  fireDocument("keydown", { key: "Escape" });
  const escKeptPreactivation = ui.state.preactivation !== null && $("t-dialog").hidden === false;
  (($("t-dialog-collapse").listeners.click) || []).forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  const collapsedKeptPicks = $("t-dialog-body").hidden === true
    && JSON.stringify(ui.state.preactivation.answers.map((answer) => answer.values)) === "[[1],[1]]";
  (($("t-dialog-collapse").listeners.click) || []).forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  await clickAndWait($("btn-cancel"));
  check("U2-05i4 ESC 不退出预发动；折叠不动答案；右端「取消预发动」退出且零提交、弹窗清正文",
    escKeptPreactivation
      && collapsedKeptPicks
      && ui.state.preactivation === null
      && $("t-dialog").hidden === true
      && $("t-dialog-body").children.length === 0
      && $("btn-show-modal").hidden === true
      && calls.length === beforePreCancel
      && ui.selectionEnabled() === true,
    `escKept=${escKeptPreactivation} collapsedKept=${collapsedKeptPicks} pre=${ui.state.preactivation} calls=${calls.length - beforePreCancel} enabled=${ui.selectionEnabled()}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ U2-06 询问弹窗框架（spec D3）：无关闭 / 可折叠 / 随询问开关 ============
 *
 * 判据（票面验收）：
 * - 询问弹窗没有关闭按钮；ESC 与点背景都不关；弹窗里没有提交 / 放弃 / 取消 / 结束出牌；
 * - 折叠把窗口缩成标题条（标题 + 步骤提示 + 展开），展开恢复；折叠不改只读态；
 * - 生命周期：表面不是弹窗 ⇒ 关；同一问重复渲染 ⇒ 只换正文、保留折叠；换问 ⇒ 关旧开新，
 *   不留上一问的窗口 / 正文；跨步骤（弹窗 → 牌桌 → 弹窗）必须关过再开；
 * - 排列与预发动都迁移到同一个容器：排列保留「查看排列」折叠态入口，预发动保留牌桌点选
 *   与「取消预发动」零提交；预发动仍然**不是**只读态；
 * - 武将详情仍是唯一可叠加的检视窗：它自己的关闭按钮 / ESC 只关它自己，关掉后询问弹窗
 *   原样恢复，开合不改只读判据。
 */
{
  ui.state.sessionId = null;   // 全程合成视图：不碰真会话
  ui.show("table");

  const cardA = { ...synthCard(82, "八卦阵"), suit: "spade", rank: 2, description: "【八卦阵】的全文描述" };
  const cardB = { ...synthCard(83, "闪"), suit: "heart", rank: 11, description: "【闪】的全文描述" };
  const cardC = { ...synthCard(84, "桃"), suit: "club", rank: 3, description: "【桃】的全文描述" };
  const arrangePending = (seq, cards = [cardA, cardB]) => ({
    kind: "ARRANGE", actor: 0, seq, timing: "PHASE_PREPARE",
    prompt: "请排列这些牌：给出顺序与前几张置于牌堆顶。",
    prompt_display: { text: "请排列这些牌。", default_label: "超时按当前排列提交" },
    revealed: cards,
    legal_actions: [{ answers_seq: seq, kind: "ArrangeCards", order: cards.map((_, at) => at), split: cards.length }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
    arrange: { count: cards.length, split: "free" },
  });
  const arrangeView = (seq, cards = [cardA, cardB]) => {
    const view = synthView(arrangePending(seq, cards));
    view.timeout_seconds = 60;
    view.snapshot.self_hand = [{ ...synthCard(1, "杀"), description: "【杀】的全文描述" }];
    view.snapshot.players[0].skills = [{
      skill_id: "hero.demo.own", name: "己方技", skill_type: "NORMAL", locked: false,
      carrier: "HERO", description: "己方技的描述。", ui: { requirement: "", convert_sources: [] },
    }];
    return view;
  };
  const tablePending = (seq) => ({
    kind: "PLAY_PHASE", actor: 0, seq,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: seq }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  const tableView = (seq) => synthView(tablePending(seq));
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const openArrange = (seq, cards) => {
    const view = arrangeView(seq, cards);
    render(view);
    return view;
  };
  const closeDialogPress = () => (($("t-dialog-collapse").listeners.click) || [])
    .forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  const openDialogEntry = () => (($("btn-show-modal").listeners.click) || [])
    .forEach((fn) => fn({ target: $("btn-show-modal") }));
  const handChip = () => $("t-hand").children[0];
  const heroBlockOf = (seat) => {
    if (Number(seat) === Number(ui.state.view.snapshot.seat)) return $("t-self-hero");
    const box = seatBox(seat);
    return box ? box.children.find((node) => (node.className || "").split(" ").includes("hero-block")) || null : null;
  };
  const fireContext = (node) => {
    const event = { target: node, prevented: false, stopPropagation() {} };
    event.preventDefault = () => { event.prevented = true; };
    if (node) (node.listeners.contextmenu || []).forEach((fn) => fn(event));
    return event;
  };
  const fireEscape = () => fireDocument("keydown", { key: "Escape" });
  const pressEnter = (node) => {
    if (!node) return;
    if (node.tagName === "BUTTON") {
      (node.listeners.click || []).forEach((fn) => fn({ target: node, type: "click" }));
      return;
    }
    (node.listeners.keydown || []).forEach((fn) => fn({
      key: "Enter", target: node, preventDefault() {},
    }));
  };
  const actionIds = ["btn-submit", "btn-cancel", "btn-end-phase", "btn-pass"];
  // 静态结构：`#t-dialog` 到 `#t-modal` 之间就是询问弹窗的全部标记。
  const dialogHtml = html.slice(html.indexOf('<div id="t-dialog"'), html.indexOf('<div id="t-modal"'));
  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");

  /* ---- ① 无关闭 / 无操作型按钮 / 折叠按钮 ---- */
  let view = openArrange(9601);
  check("U2-06a 询问弹窗没有关闭按钮、弹窗里没有四个操作键（标题 / 折叠按钮 / 内容槽之外无动作）",
    !/t-dialog-close|>关闭<|title="关闭/.test(dialogHtml)
      && actionIds.every((id) => !dialogHtml.includes(`id="${id}"`))
      && actionIds.every((id) => html.includes(`id="${id}"`))
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-body").children.includes($("t-options"))
      && !actionIds.some((id) => $("t-dialog-body").children.includes($(id))),
    `htmlClose=${/t-dialog-close|>关闭</.test(dialogHtml)} actionInDialog=${actionIds.filter((id) => dialogHtml.includes(`id="${id}"`)).join(",")} bodyChildren=${JSON.stringify($("t-dialog-body").children.map((node) => node.id || node.className))}`);
  check("U2-06a2 折叠按钮在标题条里（展开时写「收起」），标题条还带步骤提示",
    ($("t-dialog-collapse").listeners.click || []).length === 1
      && $("t-dialog-collapse").textContent === "收起"
      && $("t-dialog-collapse").getAttribute("aria-expanded") === "true"
      && $("t-dialog-hint").hidden === false
      && /牌堆/.test($("t-dialog-hint").textContent)
      && $("t-dialog-body").hidden === false,
    `collapse="${$("t-dialog-collapse").textContent}" aria=${$("t-dialog-collapse").getAttribute("aria-expanded")} hint="${$("t-dialog-hint").textContent}" bodyHidden=${$("t-dialog-body").hidden}`);

  /* ---- ② ESC / 点背景都不关，也不提交 ---- */
  const beforeEsc = calls.length;
  const actionBeforeEsc = JSON.stringify(ui.buildAction());
  fireEscape();
  check("U2-06b ESC 对询问弹窗是 no-op：不关、不改正文、不提交",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && ui.state.dialog.kind === "arrange"
      && JSON.stringify(ui.buildAction()) === actionBeforeEsc
      && calls.length === beforeEsc,
    `dialogHidden=${$("t-dialog").hidden} kind=${ui.state.dialog.kind} calls=${calls.length - beforeEsc}`);
  const backdropClicks = ($("t-modal-backdrop").listeners.click || []).length;
  (($("t-modal-backdrop").listeners.click) || []).forEach((fn) => fn({ target: $("t-modal-backdrop") }));
  check("U2-06b2 点背景不关询问弹窗（遮罩没有 click 监听；弹窗保持打开、零提交）",
    backdropClicks === 0
      && $("t-dialog").hidden === false
      && calls.length === beforeEsc,
    `backdropClicks=${backdropClicks} dialogHidden=${$("t-dialog").hidden} calls=${calls.length - beforeEsc}`);

  /* ---- ③ 折叠 / 展开：只撤正文，标题 + 提示 + 展开留下；只读态不变 ---- */
  closeDialogPress();
  const collapseActionBefore = JSON.stringify(ui.buildAction());
  check("U2-06c 折叠 = 缩成标题条：正文撤下、标题 + 步骤提示 + 「展开」留下；暗化层收起（遮挡变小）",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-hint").hidden === false
      && $("t-dialog-collapse").textContent === "展开"
      && $("t-dialog-collapse").getAttribute("aria-expanded") === "false"
      && $("btn-show-modal").hidden === false
      && $("btn-show-modal").textContent === "查看排列"
      && $("t-modal-backdrop").hidden === true
      && $("table-shell").classList.contains("modal-open") === false
      && ui.state.dialog.collapsed === true,
    `bodyHidden=${$("t-dialog-body").hidden} collapse="${$("t-dialog-collapse").textContent}" backdropHidden=${$("t-modal-backdrop").hidden} collapsed=${ui.state.dialog.collapsed}`);
  check("U2-06c2 折叠不改变只读态：selectionEnabled 仍为 false，已摆顺序不变",
    ui.stepDialogKind(view.pending) === "arrange"
      && ui.selectionEnabled() === false
      && JSON.stringify(ui.buildAction()) === collapseActionBefore,
    `surface=${ui.stepDialogKind(view.pending)} enabled=${ui.selectionEnabled()} action=${JSON.stringify(ui.buildAction())}`);
  const beforeCollapsedPick = JSON.stringify({
    focus: ui.state.focusCardId, targets: ui.state.selectedTargets, option: ui.state.selectedOption,
  });
  await clickAndWait(handChip());
  pressEnter(handChip());
  pressEnter(seatBox(1));
  check("U2-06c3 折叠后点牌点座仍 no-op（折叠只改遮挡，不改只读判据）",
    JSON.stringify({
      focus: ui.state.focusCardId, targets: ui.state.selectedTargets, option: ui.state.selectedOption,
    }) === beforeCollapsedPick
      && ui.state.focusCardId === null
      && ui.state.selectedTargets.length === 0,
    `focus=${ui.state.focusCardId} targets=${JSON.stringify(ui.state.selectedTargets)}`);
  closeDialogPress();
  check("U2-06c4 标题条里的「展开」恢复正文：折叠状态复位、暗化层回来、只读不变",
    $("t-dialog-body").hidden === false
      && $("t-dialog-collapse").textContent === "收起"
      && $("t-dialog-collapse").getAttribute("aria-expanded") === "true"
      && $("btn-show-modal").hidden === true
      && $("t-modal-backdrop").hidden === false
      && $("table-shell").classList.contains("modal-open")
      && ui.selectionEnabled() === false,
    `bodyHidden=${$("t-dialog-body").hidden} collapse="${$("t-dialog-collapse").textContent}" reopen=${$("btn-show-modal").hidden} enabled=${ui.selectionEnabled()}`);
  closeDialogPress();
  openDialogEntry();
  check("U2-06c5 折叠后仍可从右端「查看排列」展开（排列的收起态入口保留）",
    $("t-dialog-body").hidden === false
      && $("btn-show-modal").hidden === true
      && $("t-dialog-collapse").textContent === "收起",
    `bodyHidden=${$("t-dialog-body").hidden} reopen=${$("btn-show-modal").hidden}`);

  /* ---- ④ 同一问重复渲染保留折叠；换问 / 表面不再是弹窗 ⇒ 关旧开新、清正文 ---- */
  closeDialogPress();
  // 让渲染器**真的**重画一次正文（选中第一张牌），再断言折叠没被这次重画抹掉。
  if (ui.state.arrange) ui.state.arrange.selected = 0;
  ui.renderPending(view);
  const renderedWhileCollapsed = $("t-options").children
    .flatMap((row) => (row.children || []))
    .flatMap((box) => (box.children || []))
    .some((node) => node.getAttribute && node.getAttribute("data-card-index") === "0"
      && node.classList.contains("sel"));
  check("U2-06d 同一问（同 seq）的重复渲染只换正文、保留折叠状态",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && ui.state.dialog.collapsed === true
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-body").children.includes($("t-options"))
      && renderedWhileCollapsed
      && JSON.stringify(ui.buildAction()) === collapseActionBefore,
    `bodyHidden=${$("t-dialog-body").hidden} collapsed=${ui.state.dialog.collapsed} rendered=${renderedWhileCollapsed} action=${JSON.stringify(ui.buildAction())}`);
  closeDialogPress();   // 展开，后面按"展开态"继续

  const tableView1 = tableView(9602);
  render(tableView1);
  check("U2-06e 表面不再是弹窗（牌桌步）⇒ 询问弹窗关闭、正文清空、选项行回到提示条",
    $("t-dialog").hidden === true
      && $("t-dialog-body").children.length === 0
      && $("t-dialog-title").textContent === ""
      && $("t-options-home").children.includes($("t-options"))
      && $("btn-show-modal").hidden === true
      && ui.selectionEnabled() === true
      && ui.state.dialog.key === null,
    `hidden=${$("t-dialog").hidden} body=${$("t-dialog-body").children.length} home=${$("t-options-home").children.includes($("t-options"))} enabled=${ui.selectionEnabled()}`);

  const reopened = openArrange(9603, [cardC]);
  check("U2-06e2 新的弹窗步重新打开：折叠复位、正文换成本问的牌（上一问的窗口不残留）",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && $("t-dialog-collapse").textContent === "收起"
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-options").text().includes("桃")
      && !$("t-options").text().includes("八卦阵")
      && $("t-options").text().includes("牌堆顶")
      && $("t-modal").hidden === true
      && ui.state.dialog.key === `arrange:${reopened.pending.seq}`,
    `collapsed=${ui.state.dialog.collapsed} key=${ui.state.dialog.key} options="${$("t-options").text().slice(0, 80)}"`);

  /* ---- ⑤ 跨步骤闭环：弹窗 → 牌桌 → 弹窗 = 关一次、再开一次，且不带走旧正文 ---- */
  openArrange(9604, [cardA]);
  const firstDialogText = $("t-options").text();
  render(tableView(9605));
  const closedInBetween = $("t-dialog").hidden === true && $("t-dialog-body").children.length === 0;
  openArrange(9606, [cardC]);
  check("U2-06e3 两步技能跨步骤（弹窗 → 牌桌 → 弹窗）先关后开，旧询问的正文不留在新窗口里",
    closedInBetween
      && $("t-dialog").hidden === false
      && $("t-dialog-body").children.length === 1
      && $("t-dialog-body").children[0] === $("t-options")
      && $("t-options").text().includes("桃")
      && !$("t-options").text().includes("八卦阵")
      && firstDialogText.includes("八卦阵"),
    `closedInBetween=${closedInBetween} body=${JSON.stringify($("t-dialog-body").children.map((node) => node.id || node.className))} options="${$("t-options").text().slice(0, 80)}"`);

  /* ---- ⑥ 预发动迁移：同一个容器、仍是牌桌表面；无关闭，右端取消零提交 ---- */
  const prePending = (seq) => ({
    kind: "PLAY_PHASE", actor: 0, seq,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: seq }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {
      "demo.modal": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0, pool: [{ when: [], options: ["1"] }] },
        { name: "seat", zones: [], min: 1, max: 1, order: 1, pool: [{ when: [], options: ["seat:1"] }] },
      ],
    },
    preactivation: [],
  });
  const preView = synthView(prePending(9607));
  preView.snapshot.self_hand = [{ ...synthCard(1, "杀"), description: "【杀】的全文描述" }];
  preView.skills = [{
    skill_id: "demo.modal", name: "示例模态", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "", convert_sources: [] },
  }];
  render(preView);
  const preSkill = $("t-skills").children.find(
    (node) => node.getAttribute("data-skill-id") === "demo.modal");
  (preSkill.listeners.click || []).forEach((fn) => fn({ target: preSkill }));
  check("U2-06f 预发动清单迁移到询问弹窗：标题 / 正文在里面，但仍是牌桌表面（不是只读）",
    $("t-dialog").hidden === false
      && /预发动【示例模态】/.test($("t-dialog-title").textContent)
      && $("t-dialog-body").children.includes($("t-options"))
      && ui.state.dialog.kind === "preactivation"
      && ui.stepDialogKind(ui.state.view.pending) === null
      && ui.selectionEnabled() === true
      && actionIds.every((id) => html.includes(`id="${id}"`)),
    `title="${$("t-dialog-title").textContent}" kind=${ui.state.dialog.kind} surface=${ui.stepDialogKind(ui.state.view.pending)} enabled=${ui.selectionEnabled()}`);
  await clickAndWait(handChip());
  check("U2-06f2 预发动清单打开时点自己的手牌仍生效（C 形牌桌表面；弹窗不是只读闸）",
    JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[0].values) === "[1]",
    JSON.stringify(ui.state.preactivation && ui.state.preactivation.answers[0].values));
  closeDialogPress();
  check("U2-06f3 预发动弹窗也能折叠：正文撤下、答案不丢、牌桌仍可点（只改遮挡）",
    $("t-dialog-body").hidden === true
      && JSON.stringify(ui.state.preactivation.answers[0].values) === "[1]"
      && ui.selectionEnabled() === true,
    `bodyHidden=${$("t-dialog-body").hidden} answers=${JSON.stringify(ui.state.preactivation.answers.map((answer) => answer.values))} enabled=${ui.selectionEnabled()}`);
  closeDialogPress();
  const beforePreEsc = calls.length;
  fireEscape();
  check("U2-06f4 ESC 不退出预发动（旧「关闭 = 取消本地态」已改由右端取消承担）",
    $("t-dialog").hidden === false
      && ui.state.preactivation !== null
      && calls.length === beforePreEsc,
    `dialogHidden=${$("t-dialog").hidden} pre=${ui.state.preactivation !== null} calls=${calls.length - beforePreEsc}`);
  await clickAndWait($("btn-cancel"));
  check("U2-06f5 右端「取消预发动」退出：零提交、弹窗关闭、正文清空",
    ui.state.preactivation === null
      && $("t-dialog").hidden === true
      && $("t-dialog-body").children.length === 0
      && calls.length === beforePreEsc,
    `pre=${ui.state.preactivation} hidden=${$("t-dialog").hidden} body=${$("t-dialog-body").children.length} calls=${calls.length - beforePreEsc}`);

  /* ---- ⑦ 武将详情 = 唯一可叠加的检视窗：开合不改只读，关掉后弹窗原样 ---- */
  openArrange(9608);
  const arrangeBodyBeforeDetail = JSON.stringify($("t-options").text());
  const arrangeActionBeforeDetail = JSON.stringify(ui.buildAction());
  const beforeDetailCalls = calls.length;
  fireContext(heroBlockOf(0));
  check("U2-06g 详情叠在询问弹窗上：两个独立节点同时打开，弹窗正文 / 只读态不变",
    $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-modal").hidden === false
      && /武将详情/.test($("t-modal-title").textContent)
      && $("t-modal-body").text().includes("己方技")
      && JSON.stringify($("t-options").text()) === arrangeBodyBeforeDetail
      && JSON.stringify(ui.buildAction()) === arrangeActionBeforeDetail
      && ui.selectionEnabled() === false
      && calls.length === beforeDetailCalls,
    `dialogHidden=${$("t-dialog").hidden} detailHidden=${$("t-modal").hidden} detailTitle="${$("t-modal-title").textContent}" enabled=${ui.selectionEnabled()}`);
  fireEscape();
  check("U2-06g2 ESC 只关详情、不关询问弹窗；底下的排列原样恢复",
    $("t-modal").hidden === true
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "排列"
      && JSON.stringify($("t-options").text()) === arrangeBodyBeforeDetail
      && JSON.stringify(ui.buildAction()) === arrangeActionBeforeDetail
      && ui.selectionEnabled() === false,
    `detailHidden=${$("t-modal").hidden} dialogHidden=${$("t-dialog").hidden} enabled=${ui.selectionEnabled()}`);
  fireContext(heroBlockOf(0));
  (($("t-modal-close").listeners.click) || []).forEach((fn) => fn({ target: $("t-modal-close") }));
  check("U2-06g3 详情的关闭按钮也只关它自己（询问弹窗仍然原样、仍只读）",
    $("t-modal").hidden === true
      && $("t-dialog").hidden === false
      && $("t-dialog-body").children.includes($("t-options"))
      && ui.selectionEnabled() === false,
    `detailHidden=${$("t-modal").hidden} dialogHidden=${$("t-dialog").hidden} enabled=${ui.selectionEnabled()}`);
  closeDialogPress();
  fireContext(heroBlockOf(0));
  fireEscape();
  check("U2-06g4 折叠态下开合详情：不擅自展开，折叠状态原样保留",
    $("t-modal").hidden === true
      && $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && $("btn-show-modal").hidden === false
      && ui.selectionEnabled() === false,
    `bodyHidden=${$("t-dialog-body").hidden} detailHidden=${$("t-modal").hidden} reopen=${$("btn-show-modal").hidden}`);

  /* ---- ⑧ 关闭后没有上一问的窗口 / 正文残留 ---- */
  render(tableView(9609));
  check("U2-06h 关闭清正文：窗口隐藏、body / 标题 / 步骤提示全空、选项行归位、无重开入口",
    $("t-dialog").hidden === true
      && $("t-dialog-body").children.length === 0
      && $("t-dialog-title").textContent === ""
      && $("t-dialog-hint").textContent === ""
      && $("t-dialog-hint").hidden === true
      && $("t-options-home").children.includes($("t-options"))
      && $("btn-show-modal").hidden === true
      && ui.state.dialog.open === false
      && ui.state.dialog.key === null,
    `hidden=${$("t-dialog").hidden} body=${$("t-dialog-body").children.length} title="${$("t-dialog-title").textContent}" home=${$("t-options-home").children.includes($("t-options"))}`);
  check("U2-06i 弹窗与遮罩 CSS：只剩标题条时暗化层可收起；询问弹窗不吃指针、窗口可点；无 inert 复用",
    /\.dialog\s*\{[^}]*pointer-events:\s*none/s.test(css)
      && /\.dialog-window\s*\{[^}]*pointer-events:\s*auto/s.test(css)
      && /\.dialog-body\[hidden\]\s*\{[^}]*display:\s*none/s.test(css)
      && /\.modal-backdrop\s*\{[^}]*pointer-events:\s*none/s.test(css)
      && /\.modal\s*\{[^}]*pointer-events:\s*none/s.test(css)
      && /\.modal-window\s*\{[^}]*pointer-events:\s*auto/s.test(css)
      && !/modal-blocking/.test(css),
    `dialogPE=${/\.dialog\s*\{[^}]*pointer-events:\s*none/s.test(css)} windowPE=${/\.dialog-window\s*\{[^}]*pointer-events:\s*auto/s.test(css)} bodyHidden=${/\.dialog-body\[hidden\]\s*\{[^}]*display:\s*none/s.test(css)}`);

  ui.resetSelection();
  ui.state.view = null;
  ui.state.sessionId = null;
}

/* ============ U2-07 观星（排列）接入：满卡面弹窗 + 超时自动提交（spec D9.3） ============
 *
 * 判据（票面验收）：
 * - 排列仍在 `#t-dialog`（标题「排列」/ 步骤提示 / 折叠 / 无关闭），两行牌面是**完整
 *   88×124 卡面**；X = min(存活数, 5) 张在 1920×1080 与 1280×720 都放得下；
 * - 拖拽与「点选后点目标」两种搬牌手势都仍可用；提交 = 当前 `ArrangeCards`；
 * - 引擎的排列候选**没有 Pass** ⇒ 界面明说"强制、唯一出口是右端确认"，右端「放弃」隐藏；
 *   若某条排列技能真给了 Pass，右端「放弃」也按**当前排列**提交，不是 no-op；
 * - 到时限自动提交**只**对自家 ARRANGE 生效，按 seq 去重、失败不重试、折叠照常、回放不发；
 *   非排列询问到时限仍零请求（U1-03k）。
 */
{
  ui.state.sessionId = null;
  ui.state.view = null;
  ui.state.arrangeAutoSeq = null;
  ui.state.playback = null;
  ui.show("table");

  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");
  const makeCard = (id, name, suit, rank) => ({
    ...synthCard(id, name), suit, rank, kind: "normal", attribute: "普通",
    description: `【${name}】的全文描述`,
  });
  const cards = [
    makeCard(91, "八卦阵", "spade", 2),
    makeCard(92, "闪", "heart", 11),
    makeCard(93, "桃", "club", 3),
    makeCard(94, "雌雄双股剑", "diamond", 5),
    makeCard(95, "杀", "spade", 7),
  ];
  /** 合成 ARRANGE：`withPass` 用来演"引擎真给了 Pass"的另一半语义。 */
  const arrangePending = (seq, revealed, withPass = false) => ({
    kind: "ARRANGE", actor: 0, seq, timing: "PHASE_PREPARE",
    prompt: "请排列这些牌：给出顺序与前几张置于牌堆顶。",
    prompt_display: { text: "请排列这些牌。", default_label: "超时将自动选择第一项" },
    revealed,
    legal_actions: [
      { answers_seq: seq, kind: "ArrangeCards", order: revealed.map((_, at) => at), split: revealed.length },
      ...(withPass ? [{ answers_seq: seq, kind: "Pass" }] : []),
    ],
    candidate_index: {}, option_seats: {},
    context: {
      requirement: "观看牌堆顶若干张牌，以任意顺序放回牌堆顶 / 牌堆底",
      label: null, skill_hints: [],
    },
    preactivations: {}, preactivation: [],
    arrange: { count: revealed.length, split: "free" },
  });
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const rowOf = (name) => $("t-options").children.find(
    (node) => node.getAttribute && node.getAttribute("data-row") === name);
  const cardsBox = (row) => row.children[1];
  const cardNode = (row, index) => cardsBox(row).children.find(
    (node) => node.getAttribute && node.getAttribute("data-card-index") === String(index));
  const cardNodes = (row) => cardsBox(row).children.filter(
    (node) => node.getAttribute && node.getAttribute("data-card-index") !== undefined);
  const cardIndexes = (row) => cardNodes(row).map((node) => Number(node.getAttribute("data-card-index")));
  const slots = (row) => cardsBox(row).children.filter(
    (node) => node.getAttribute && node.getAttribute("data-empty") === "1");
  const clickNode = (node) => (node.listeners.click || []).forEach(
    (fn) => fn({ target: node, preventDefault() {}, stopPropagation() {} }));
  // 垫片的 `className` 是普通属性、`classList.add` 只改 Set（真 DOM 两者同步）；断言两种都认。
  const hasClass = (node, name) => node.classList.contains(name)
    || String(node.className || "").split(/\s+/).includes(name);

  /* ---- ① 完整 88×124 卡面 + 询问弹窗 + 5 张的宽度 / 高度预算 ---- */
  const fiveView = synthView(arrangePending(9701, cards));
  fiveView.timeout_seconds = 60;
  render(fiveView);
  const topRow = rowOf("top");
  const bottomRow = rowOf("bottom");
  const topCards = cardNodes(topRow);
  check("U2-07a 观星走询问弹窗：标题「排列」/ 步骤提示 / 折叠按钮 / 无关闭；两行都在窗内",
    $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "排列"
      && $("t-dialog-hint").hidden === false
      && $("t-dialog-hint").textContent.length > 0
      && (($("t-dialog-collapse").listeners.click) || []).length === 1
      && $("t-dialog-body").children.includes($("t-options"))
      && !!topRow && !!bottomRow
      && $("t-dialog-body").text().includes("牌堆顶")
      && $("t-dialog-body").text().includes("牌堆底"),
    `title="${$("t-dialog-title").textContent}" hint="${$("t-dialog-hint").textContent}" bodyChildren=${JSON.stringify($("t-dialog-body").children.map((node) => node.id || node.className))}`);

  check("U2-07b 两行牌面是完整卡面（.card.face，牌名 / 花色在脸内），不是旧的紧凑排列芯片",
    topCards.length === 5
      && topCards.every((node) => hasClass(node, "card")
        && hasClass(node, "face")
        && hasClass(node, "arrange-card")
        && node.getAttribute("data-card-id") !== undefined
        && node.children.some((child) => hasClass(child, "cname"))
        && node.children.some((child) => hasClass(child, "suit")))
      && !topCards.some((node) => node.children.some((child) => hasClass(child, "n"))),
    `cards=${topCards.length} classes=${JSON.stringify(topCards.map((node) => node.className))}`);

  // 几何：牌面宽 = 88 设计单位、高 = 124；缝隙 / 行内边距 / 正文边距是 px。
  // 1920（--dsu 1.2）与 1280（0.8）两个视口都要放得下 5 张 + 两行。
  const windowMatch = /\.dialog-window\s*\{[^}]*width:\s*min\((\d+)px,\s*66%\)/s.exec(css);
  const windowMax = Number(windowMatch && windowMatch[1]);
  const fit = (width, height) => {
    const dsu = width / 1600;
    const windowWidth = Math.min(windowMax, width * (1 - 0.08) * 0.66);
    const neededWidth = 5 * 88 * dsu + 4 * 6 + 2 * 9 + 2 * 14;
    const rowHeight = 124 * dsu + 13 * 1.65 + 6 + 2 * 7 + 2;
    const neededHeight = 2 * rowHeight + 10 + (12 + 16) + (15 * 1.65 + 2 * 9 + 1);
    const availHeight = (height * (1 - 0.062 - 0.275) - 0.04 * width) * 0.96;
    return {
      dsu, windowWidth, neededWidth, neededHeight, availHeight,
      ok: neededWidth <= windowWidth && neededHeight <= availHeight,
    };
  };
  const at1080 = fit(1920, 1080);
  const at720 = fit(1280, 720);
  check("U2-07c 对话框容得下 X = 5 张满卡面：1920×1080 与 1280×720 的宽 / 高都够（行高预留 124）",
    !!windowMatch
      && /\.card\.face\s*\{[^}]*width:\s*calc\(88 \* var\(--dsu\)\);\s*height:\s*calc\(124 \* var\(--dsu\)\)/s.test(css)
      && /\.arrange-cards\s*\{[^}]*min-height:\s*calc\(124 \* var\(--dsu\)\)/s.test(css)
      && !/\.arrange-card\s*\{[^}]*(?:width|height|flex-basis):/s.test(css)
      && at1080.ok && at720.ok,
    `window=${windowMatch && windowMax}px 1080p=${JSON.stringify(at1080)} 720p=${JSON.stringify(at720)}`);

  /* ---- ② 出口语义按引擎实际候选：无 Pass ⇒ 强制；有 Pass ⇒ 放弃也按当前排列 ---- */
  const forcedView = synthView(arrangePending(9702, cards.slice(0, 2)));
  forcedView.timeout_seconds = 60;
  render(forcedView);
  check("U2-07d 引擎的排列没有 Pass ⇒ 明说强制、唯一出口是右端「确认」；「放弃」保持隐藏",
    ui.arrangeHasPass(forcedView.pending) === false
      && $("btn-pass").hidden === true
      && $("btn-pass").disabled === true
      && $("btn-submit").textContent === "确认"
      && /强制/.test($("t-target-hint").textContent)
      && /唯一出口/.test($("t-target-hint").textContent)
      && /「确认」/.test($("t-target-hint").textContent)
      && /强制/.test($("t-choice").textContent)
      && /「确认」/.test($("t-choice").textContent),
    `hasPass=${ui.arrangeHasPass(forcedView.pending)} passHidden=${$("btn-pass").hidden} hint="${$("t-target-hint").textContent}" choice="${$("t-choice").textContent}"`);

  const passView = synthView(arrangePending(9703, cards.slice(0, 2), true));
  passView.timeout_seconds = 60;
  render(passView);
  const passAction = ui.buildAction();
  check("U2-07e 排列若带 Pass：右端「放弃」出现，但语义是「按当前排列提交」（与确认同一条）",
    ui.arrangeHasPass(passView.pending) === true
      && $("btn-pass").hidden === false
      && $("btn-pass").textContent === "放弃"
      && JSON.stringify(passAction) === JSON.stringify(
        { answers_seq: 9703, kind: "ArrangeCards", order: [0, 1], split: 2 }),
    `hasPass=${ui.arrangeHasPass(passView.pending)} passHidden=${$("btn-pass").hidden} text="${$("btn-pass").textContent}" action=${JSON.stringify(passAction)}`);
  const beforePassClick = calls.length;
  ui.state.sessionId = "u2-07-pass-no-session";   // 只为捕获 POST 体（服务端会 404，界面按拒绝处理）
  await clickAndWait($("btn-pass"));
  const passSent = calls.slice(beforePassClick).find((call) => call.path.includes("/answer"));
  ui.state.sessionId = null;
  check("U2-07e2 点右端「放弃」送出的就是当前 ArrangeCards（不是 Pass、不是 no-op）",
    !!passSent
      && JSON.stringify(passSent.body.action) === JSON.stringify(passAction)
      && passSent.body.action.kind === "ArrangeCards",
    `sent=${JSON.stringify(passSent && passSent.body.action)}`);

  /* ---- ③ 拖拽与「点选后点目标」都仍可用（卡面换了，手势不能丢） ---- */
  const dragView = synthView(arrangePending(9704, cards.slice(0, 3)));
  dragView.timeout_seconds = 60;
  render(dragView);
  const dragCard = cardNode(rowOf("top"), 0);
  (dragCard.listeners.dragstart || []).forEach((fn) => fn({ dataTransfer: { setData() {} } }));
  (slots(rowOf("bottom"))[0].listeners.drop || []).forEach((fn) => fn({ preventDefault() {} }));
  check("U2-07f 拖拽仍可用：dragstart 起拖 → 空槽 drop = 移到牌堆底（完整卡面不挡手势）",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[1,2]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[0]",
    `top=${JSON.stringify(cardIndexes(rowOf("top")))} bottom=${JSON.stringify(cardIndexes(rowOf("bottom")))}`);
  clickNode(cardNode(rowOf("top"), 1));
  const selectedByClick = ui.state.arrange && ui.state.arrange.selected;
  clickNode(slots(rowOf("bottom"))[0]);
  const clickedAction = ui.buildAction();
  check("U2-07g 点选后点目标仍可用：第一下选中、第二下移到牌堆底，载荷是当前排列",
    selectedByClick === 1
      && JSON.stringify(cardIndexes(rowOf("top"))) === "[2]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[0,1]"
      && !!clickedAction
      && JSON.stringify(clickedAction.order) === "[2,0,1]"
      && clickedAction.split === 1,
    `selected=${selectedByClick} top=${JSON.stringify(cardIndexes(rowOf("top")))} bottom=${JSON.stringify(cardIndexes(rowOf("bottom")))} action=${JSON.stringify(clickedAction)}`);

  /* ---- ④ 提前量：2 秒；剩余 2.4 秒不动、1.9 秒判到点（纯判断不发请求） ---- */
  const marginView = synthView(arrangePending(9705, cards.slice(0, 2)));
  marginView.timeout_seconds = 60;
  render(marginView);
  ui.state.arrangeAutoSeq = null;
  const beforeMargin = calls.length;
  ui.state.pendingClock.receivedAt = Date.now() - (60 - 2.4) * 1000;
  const dueAt24 = ui.arrangeAutoSubmitDue(marginView);
  ui.state.pendingClock.receivedAt = Date.now() - (60 - 1.9) * 1000;
  const dueAt19 = ui.arrangeAutoSubmitDue(marginView);
  check("U2-07h 提前量 = 2 秒：剩余 2.4 秒不提交、剩余 1.9 秒判到点；纯判断不发请求",
    ui.ARRANGE_AUTO_SUBMIT_MARGIN_SECONDS === 2
      && calls.length === beforeMargin
      && dueAt24 === false && dueAt19 === true,
    `margin=${ui.ARRANGE_AUTO_SUBMIT_MARGIN_SECONDS} due@2.4=${dueAt24} due@1.9=${dueAt19} calls=${calls.length - beforeMargin}`);

  /* ---- ⑤ 折叠 / 失败 / 回放：自动提交只看询问，不看弹窗是否展开 ---- */
  const realArrangePending = async () => {
    const body = await post("/api/sessions", {
      player_count: 4, ai: "random", human_seat: 0, timeout_seconds: 60, seed: 7,
      my_hero: "zhugeliang",
    });
    await openSession(body);
    await ui.submitAction();   // 发动观星 → ARRANGE
    ui.state.arrangeAutoSeq = null;
    return ui.state.view.pending;
  };

  const collapsedPending = await realArrangePending();
  (($("t-dialog-collapse").listeners.click) || []).forEach(
    (fn) => fn({ target: $("t-dialog-collapse") }));
  const collapsedBeforeSubmit = $("t-dialog-body").hidden === true
    && $("t-dialog-collapse").textContent === "展开";
  const collapsedBefore = calls.length;
  ui.state.pendingClock.receivedAt = Date.now() - 59000;
  await ui.renderCountdown(ui.state.view);
  const collapsedAnswers = calls.slice(collapsedBefore)
    .filter((call) => call.path.includes("/answer"));
  check("U2-07i 折叠不影响自动提交：弹窗缩成标题条、正文已撤下，到点仍只发一次当前排列",
    !!collapsedPending && collapsedPending.kind === "ARRANGE"
      && collapsedBeforeSubmit
      && collapsedAnswers.length === 1
      && collapsedAnswers[0].body.action.kind === "ArrangeCards"
      && ui.state.arrangeAutoSeq === collapsedPending.seq,
    `pending=${collapsedPending && collapsedPending.kind} bodyHidden=${collapsedBeforeSubmit} answers=${collapsedAnswers.length} action=${JSON.stringify(collapsedAnswers[0] && collapsedAnswers[0].body.action)}`);
  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
  ui.state.arrangeAutoSeq = null;

  const retryPending = await realArrangePending();
  const retryBefore = calls.length;
  staleAnswerStatus = 409;
  ui.state.pendingClock.receivedAt = Date.now() - 59000;
  await ui.renderCountdown(ui.state.view);
  const retryWindow = calls.slice(retryBefore);
  const retryAnswers = retryWindow.filter((call) => call.path.includes("/answer"));
  const retryRefreshes = retryWindow.filter((call) => !call.path.includes("/answer"));
  const retryAfter = calls.length;
  await ui.renderCountdown(ui.state.view);
  const retriedAnswers = calls.slice(retryAfter).filter((call) => call.path.includes("/answer"));
  check("U2-07j 失败不重试：被拒（409）只发一次 POST + 一次刷新；之后再 tick 也不补发（交给服务端超时）",
    !!retryPending && retryPending.kind === "ARRANGE"
      && retryAnswers.length === 1
      && retryRefreshes.length >= 1
      && retriedAnswers.length === 0
      && ui.state.arrangeAutoSeq === retryPending.seq,
    `answers=${retryAnswers.length} refresh=${retryRefreshes.length} retried=${retriedAnswers.length} autoSeq=${ui.state.arrangeAutoSeq} choice="${$("t-choice").textContent}"`);
  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
  ui.state.arrangeAutoSeq = null;

  const playbackView = synthView(arrangePending(9706, cards.slice(0, 2)));
  playbackView.timeout_seconds = 60;
  render(playbackView);
  ui.state.arrangeAutoSeq = null;
  ui.state.pendingClock.receivedAt = Date.now() - 59000;
  ui.state.playback = { body: {}, records: [], index: 0 };
  const playbackBefore = calls.length;
  await ui.renderCountdown(playbackView);
  const playbackAnswers = calls.slice(playbackBefore)
    .filter((call) => call.path.includes("/answer"));
  ui.state.playback = null;
  check("U2-07k 回放期间不自动提交（倒计时也不显示）：零请求",
    playbackAnswers.length === 0
      && $("t-countdown").hidden === true
      && ui.state.arrangeAutoSeq === null,
    `answers=${playbackAnswers.length} countdownHidden=${$("t-countdown").hidden}`);

  ui.resetSelection();
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.playback = null;
  ui.state.arrangeAutoSeq = null;
}

/* ============ U2-08（D3 / D10）：选择型弹窗 + 反间 · 未知区域弹窗 ============
 *
 * 两个模板都住在 06 的同一个 `#t-dialog` 容器里：标题 + 步骤提示 + 正文（`#t-options`
 * 搬进 `#t-dialog-body`）+ 折叠；没有关闭 / ESC / 点背景出口；内容按钮一击即提交
 * （送出的动作原样取自 `legal_actions`）；弹窗里没有「放弃」，引擎给的 `Pass` 也不读。
 * 合成载荷与真会话响应同形（`option_labels` / `slots[].card=None` 的字段名见
 * `tests/test_web.py` 的真会话断言）。
 */
{
  const STYLE = readFileSync("tools/sgs_web/static/style.css", "utf8");
  const SUIT_LABELS = {
    heart: { name: "红桃", symbol: "♥", color: "red" },
    diamond: { name: "方块", symbol: "♦", color: "red" },
    spade: { name: "黑桃", symbol: "♠", color: "black" },
    club: { name: "梅花", symbol: "♣", color: "black" },
  };
  const suitPending = (seq) => ({
    kind: "SELECT_OPTION", actor: 1, seq, skill_id: "fanjian:suit",
    prompt: "请选择一项。",
    options: ["heart", "diamond", "spade", "club"],
    option_labels: SUIT_LABELS,
    legal_actions: [
      ...["heart", "diamond", "spade", "club"].map(
        (choice) => ({ kind: "SelectOption", choice, answers_seq: seq })),
      { kind: "Pass", answers_seq: seq },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    context: {
      actor: 1, name: "反间", targets: [1],
      label: "0号位周瑜对1号位关羽使用反间",
      requirement: "由目标选择一种花色", skill_hints: [],
    },
    prompt_display: { text: "请选择一项。", default_label: "超时将自动放弃" },
    preactivations: {}, preactivation: [],
  });
  const blindPending = (seq) => ({
    kind: "SELECT_CARDS", actor: 1, seq, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      ...[0, 1, 2, 3, 4].map((index) => ({ kind: "SelectCards", answers_seq: seq, picks: [index] })),
      { kind: "Pass", answers_seq: seq },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [0, 1, 2, 3, 4].map(() => ({ ref: { seat: 0, zone: "hand" }, card: null })),
    spec: { zones: ["hand"], total: [1, 1], sequential: false, optional: true },
    context: {
      actor: 1, name: "反间", targets: [1],
      label: "0号位周瑜对1号位关羽使用反间",
      requirement: "由目标从你的手牌中选择一张", skill_hints: [],
    },
    prompt_display: { text: "请从选择面里选择一张牌。", default_label: "超时将自动放弃" },
    preactivations: {}, preactivation: [],
  });
  const ownSuitView = (pending) => {
    const view = synthView(pending);
    // 人类是**反间目标**（座位 1）：询问者 = 查看者，界面才铺可点控件。
    view.snapshot.seat = 1;
    view.human_seat = 1;
    view.snapshot.self_hand = [];
    return view;
  };
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const dialogText = () => ["t-dialog", "t-prompt", "t-target-hint", "t-choice"]
    .map((id) => $(id).text()).join(" ");
  const buttons = (id = "t-options") => $(id).children;

  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;

  // ---- ① 选择型：四张中文花色按钮（符号 + 红黑），没有放弃 / 没有下一步 ----
  const suitView = ownSuitView(suitPending(9801));
  render(suitView);
  check("U2-08a 花色步 = 选择型弹窗：四个中文按钮（符号 + 红黑），零英文枚举、零「选项：」",
    ui.stepDialogKind(suitView.pending) === "choice"
      && $("t-dialog").hidden === false
      && $("t-options-home").children.includes($("t-options")) === false
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-options").classList.contains("suit-options")
      && $("t-options").children.length === 4
      && $("t-options").children.map((node) => node.textContent).join(",")
        === "♥红桃,♦方块,♠黑桃,♣梅花"
      && $("t-options").children.map((node) => node.getAttribute("data-color")).join(",")
        === "red,red,black,black"
      && $("t-options").children.every((node) => node.classList.contains("suit"))
      && !/heart|diamond|spade|club|选项：/.test(dialogText())
      && candidateRowButtons().length === 0,
    `kind=${ui.stepDialogKind(suitView.pending)} 选项=${JSON.stringify($("t-options").children.map((node) => node.textContent))} text=${dialogText()}`);
  check("U2-08b 花色步没有「放弃」（引擎给了 Pass 也不显示）；提交 / 清空都不抢跑",
    $("btn-pass").hidden === true
      && $("btn-cancel").disabled === true
      && $("btn-submit").disabled === true
      && $("t-choice").textContent.includes("点一个选项即提交"),
    `passHidden=${$("btn-pass").hidden} cancel=${$("btn-cancel").disabled} submit=${$("btn-submit").disabled} choice="${$("t-choice").textContent}"`);
  check("U2-08c 选择型按钮的红 / 黑落地在 CSS（颜色值本身读服务端 data-color）",
    STYLE.includes('.opt.suit[data-color="red"]')
      && STYLE.includes('.opt.suit[data-color="black"]')
      && STYLE.includes("var(--accent)")
      && STYLE.includes("var(--ink)"),
    `red=${STYLE.includes('.opt.suit[data-color="red"]')} black=${STYLE.includes('.opt.suit[data-color="black"]')}`);
  check("U2-08c2 选择型模板判据：选项 ≥4 也进弹窗（不要求先有中文标签）；选座位形态除外",
    ui.stepDialogKind({
      kind: "SELECT_OPTION", actor: 1, seq: 9804, option_seats: {},
      legal_actions: ["alpha", "beta", "gamma", "delta"].map(
        (choice) => ({ kind: "SelectOption", choice, answers_seq: 9804 })),
    }) === "choice"
      && ui.stepDialogKind({
        kind: "SELECT_OPTION", actor: 1, seq: 9805,
        option_seats: { "seat:0": 0, "seat:1": 1, "seat:2": 2, "seat:3": 3 },
        legal_actions: [0, 1, 2, 3].map(
          (seat) => ({ kind: "SelectOption", choice: `seat:${seat}`, answers_seq: 9805 })),
      }) === null,
    `4options=${ui.stepDialogKind({ kind: "SELECT_OPTION", legal_actions: [1, 2, 3, 4].map((i) => ({ kind: "SelectOption", choice: `o${i}` })), option_seats: {} })} seats=${ui.stepDialogKind({ kind: "SELECT_OPTION", option_seats: { "seat:0": 0 }, legal_actions: [1, 2, 3, 4].map((i) => ({ kind: "SelectOption", choice: `seat:${i - 1}` })) })}`);

  // ---- ② 无关闭 / ESC / 点背景出口；可折叠 ----
  const beforeEsc = calls.length;
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U2-08d 选择型弹窗没有关闭 / ESC / 点背景出口",
    $("t-dialog").hidden === false
      && calls.length === beforeEsc
      && !($("t-modal-backdrop").listeners.click || []).length,
    `dialogHidden=${$("t-dialog").hidden} calls=${calls.length - beforeEsc}`);
  (($("t-dialog-collapse").listeners.click) || []).forEach(
    (fn) => fn({ target: $("t-dialog-collapse") }));
  check("U2-08e 选择型弹窗可折叠：正文撤下、标题与步骤提示留下、暗化层收起",
    $("t-dialog-body").hidden === true
      && $("t-dialog-title").textContent === "选择"
      && $("t-dialog-hint").textContent.includes("点一个选项即提交")
      && $("t-dialog-collapse").textContent === "展开"
      && $("t-modal-backdrop").hidden === true,
    `bodyHidden=${$("t-dialog-body").hidden} title="${$("t-dialog-title").textContent}" hint="${$("t-dialog-hint").textContent}"`);
  (($("t-dialog-collapse").listeners.click) || []).forEach(
    (fn) => fn({ target: $("t-dialog-collapse") }));
  check("U2-08e2 再点「展开」恢复正文与暗化层",
    $("t-dialog-body").hidden === false && $("t-modal-backdrop").hidden === false);

  // ---- ③ 一击即提交：点「黑桃」= 恰好一条 SelectOption POST ----
  const beforeSuitClick = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("t-options").children[2]);
  staleAnswerStatus = null;
  const suitAnswers = calls.slice(beforeSuitClick).filter((call) => call.path.includes("/answer"));
  check("U2-08f 点一张花色 = 一次提交（一条 POST、动作逐字段取自候选、无需「下一步」）",
    suitAnswers.length === 1
      && JSON.stringify(suitAnswers[0].body.action)
        === JSON.stringify(suitView.pending.legal_actions[2])
      && /提交被拒/.test($("t-choice").textContent),
    `answers=${suitAnswers.length} action=${JSON.stringify(suitAnswers[0] && suitAnswers[0].body.action)}`);

  // ---- ④ 链：花色 → 盲挑，同一个 #t-dialog、同一个键，只换正文 ----
  const blindView = ownSuitView(blindPending(9802));
  blindView.skills = suitView.skills;
  const keyBeforeBlind = ui.state.dialog.key;
  render(blindView);
  check("U2-08g 链上两步共用同一容器（键不变、不关窗、正文换成盲挑）",
    ui.stepDialogKind(blindView.pending) === "unknown"
      && $("t-dialog").hidden === false
      && ui.state.dialog.key === keyBeforeBlind
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-dialog-title").textContent === "选择一张牌"
      && $("t-dialog-hint").textContent.includes("点一张牌即提交"),
    `kind=${ui.stepDialogKind(blindView.pending)} key=${ui.state.dialog.key} before=${keyBeforeBlind} title="${$("t-dialog-title").textContent}"`);
  const blindZone = $("t-options").children[0];
  const blindSlots = blindZone.children[1].children;
  check("U2-08h 盲挑 = 五个「?」槽位 + 归属 / 区域提示；不画没提供的装备区",
    $("t-options").children.length === 1
      && blindZone.getAttribute("data-zone") === "hand"
      && blindZone.children[0].textContent.includes("座位0 手牌")
      && blindZone.children[0].textContent.includes("牌面未知")
      && blindSlots.length === 5
      && blindSlots.every((node) => node.textContent === "?" && node.classList.contains("unknown"))
      && !/装备区/.test($("t-options").text())
      && !/heart|diamond|spade|club/.test(dialogText())
      && candidateRowButtons().length === 0,
    `块=${$("t-options").children.length} 槽=${blindSlots.length} 文本=${$("t-options").text()}`);
  check("U2-08i 盲挑步没有「放弃」（引擎给了 Pass 也不显示）；取消键不送 Pass",
    $("btn-pass").hidden === true
      && $("btn-cancel").disabled === true
      && $("t-choice").textContent.includes("点一张牌即提交"),
    `passHidden=${$("btn-pass").hidden} cancel=${$("btn-cancel").disabled} choice="${$("t-choice").textContent}"`);

  // ---- ⑤ 一击即提交：点第 3 个「?」= 恰好一条 SelectCards POST（picks = 位置） ----
  const beforeBlindClick = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait(blindSlots[2]);
  staleAnswerStatus = null;
  const blindAnswers = calls.slice(beforeBlindClick).filter((call) => call.path.includes("/answer"));
  check("U2-08j 点一张「?」= 一次提交（一条 SelectCards POST，picks 填该槽位位置）",
    blindAnswers.length === 1
      && JSON.stringify(blindAnswers[0].body.action)
        === JSON.stringify(blindView.pending.legal_actions[2])
      && !/Pass/.test(JSON.stringify(blindAnswers[0].body.action)),
    `answers=${blindAnswers.length} action=${JSON.stringify(blindAnswers[0] && blindAnswers[0].body.action)}`);

  // ---- ⑥ 未知区域模板：装备区只在**这一步真的提供**时出现（模板级，不接线 09） ----
  $("t-options").innerHTML = "";
  ui.renderUnknownZone({
    kind: "SELECT_CARDS", actor: 0, seq: 9803,
    legal_actions: [{ kind: "SelectCards", answers_seq: 9803, picks: [0] }],
    slots: [
      { ref: { seat: 1, zone: "hand" }, card: null },
      { ref: { seat: 1, zone: "equip" }, card: { ...synthCard(95, "八卦阵"), slot: "armor" } },
    ],
    spec: { zones: ["hand", "equip"], total: [1, 1] },
  });
  check("U2-08k 未知区域模板只为实际提供的区域画块：手牌「?」+ 装备可见",
    $("t-options").children.length === 2
      && $("t-options").children[1].getAttribute("data-zone") === "equip"
      && $("t-options").children[1].text().includes("装备区")
      && $("t-options").children[1].text().includes("八卦阵"),
    `块=${$("t-options").children.map((node) => node.getAttribute("data-zone")).join(",")} 文本=${$("t-options").text()}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ U2-09（D2 / D3 / D8）：未知区域弹窗推广到反馈 / 拆 / 顺 ============
 *
 * 08 的模板已按 `slots[].ref.zone` 分组；本段钉的是**判据的推广**：凡是"从另一名角色的
 * 手牌 / 装备 / 判定三区里挑一张"的 `SELECT_CARDS`（反馈 / 过河拆桥 / 顺手牵羊 / 寒冰剑 /
 * 反间盲挑）都进同一个 `#t-dialog` 模板：手牌画「?」、可见的装备 / 判定照常画牌面、
 * 一击即提交、弹窗内没有「放弃」。亮牌（遗计）、处理区（五谷）、只挑可见装备（麒麟弓）、
 * "提供一张 B"（青龙 / 丈八）与自己的牌**不进**这个模板（判据见 `modal.js`）。
 *
 * 载荷形状与真会话同形：`slots[].ref` / `spec.zones` / `context.requirement` 都由服务端
 * 下发（反馈的 requirement =「获得伤害来源的一张牌」，拆 / 顺 =「选择目标区域里的一张牌」）。
 * 激活询问（是否发动）仍是牌桌表面：放弃留在那一步；弹窗这一步没有 Pass。
 */
{
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  /** 某节点是不是 `#t-dialog` 的子孙（钉"弹窗内容里没有操作四键"）。 */
  const insideDialog = (node) => {
    let at = node ? node.parentNode : null;
    while (at) {
      if (at === $("t-dialog")) return true;
      at = at.parentNode;
    }
    return false;
  };
  const zoneSections = () => $("t-options").children;
  const zoneSlots = (section) => section.children[1].children;
  const allSlots = () => zoneSections().flatMap((section) => zoneSlots(section));
  const fireContext = (node) => {
    const event = { target: node, prevented: false, stopPropagation() {} };
    event.preventDefault = () => { event.prevented = true; };
    if (node) (node.listeners.contextmenu || []).forEach((fn) => fn(event));
    return event;
  };
  const heroBlockOf = (seat) => {
    const box = seatBox(seat);
    return box
      ? box.children.find((node) => (node.className || "").split(" ").includes("hero-block")) || null
      : null;
  };
  const clickCollapse = () => (($("t-dialog-collapse").listeners.click) || [])
    .forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  /** 可见牌面的点击由 `cardFaceNode` 的同步包装转发（不等于 `await` 内部那次提交）。
   *  这里等"提交在飞"结束，避免下一段撞进四键冻结态（用 Node 自己的计时器，不碰 vm 垫片）。 */
  const settleSubmit = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };

  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 1, timeout_seconds: null, seed: 20261013,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  // 上一段可能留着开着的武将详情；本段从"弹窗 + 无详情"这一面开始。
  ui.state.modal.heroSeat = null;

  // ---- 0 数据：来源 / 目标是座位 0，人类是座位 1（反馈的持有者 / 拆顺的使用者） ----
  const armor = {
    ...synthCard(95, "八卦阵"), type: "armor", slot: "armor", color: "black",
    description: "防具描述：需要时打出一张【闪】。",
  };
  const horse = {
    ...synthCard(96, "赤兔"), type: "horse", slot: "horse_defense", color: "red",
    description: "坐骑描述：距离 -1。",
  };
  const judgeCard = {
    ...synthCard(97, "乐不思蜀"), type: "indulgence", slot: null,
    description: "判定描述。",
  };
  const fankuiPending = (seq) => ({
    kind: "SELECT_CARDS", actor: 1, seq, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      ...[0, 1, 2, 3, 4].map(
        (index) => ({ kind: "SelectCards", answers_seq: seq, picks: [index] })),
      // 引擎这一步是 optional=False（没有 Pass）；这里仍塞一条，钉"弹窗内即便有也不画"。
      { kind: "Pass", answers_seq: seq },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [
      ...[0, 1, 2].map(() => ({ ref: { seat: 0, zone: "hand" }, card: null })),
      { ref: { seat: 0, zone: "equip" }, card: armor },
      { ref: { seat: 0, zone: "equip" }, card: horse },
    ],
    spec: { zones: ["hand", "equip"], total: [1, 1], sequential: false, optional: true },
    context: {
      actor: 1, name: "杀", targets: [1],
      label: "0号位张飞对1号位司马懿使用杀",
      requirement: "获得伤害来源的一张牌", skill_hints: [],
    },
    prompt_display: { text: "请从选择面里选择一张牌。", default_label: "超时将自动放弃" },
    preactivations: {}, preactivation: [],
  });
  const fankuiView = (seq) => {
    const view = synthView(fankuiPending(seq));
    // 人类是**反馈的持有者**（座位 1）：询问者 = 查看者，界面才铺可点控件。
    view.snapshot.seat = 1;
    view.human_seat = 1;
    view.snapshot.self_hand = [synthCard(1, "杀")];
    view.snapshot.players[0].hero_name = "张飞";
    view.snapshot.players[0].skills = [{
      skill_id: "hero.zhangfei.paoxiao", name: "咆哮", skill_type: "NORMAL", locked: true,
      carrier: "HERO", description: "锁定技：出杀没有次数限制。", ui: { passive: true },
    }];
    return view;
  };

  // ---- ① 发动询问仍拥有「放弃」；弹窗这一步没有 ----
  {
    const activation = {
      kind: "SKILL_ACTIVATION", actor: 1, seq: 9900, skill_id: "hero.simayi.fankui",
      prompt: "是否发动【反馈】？",
      legal_actions: [
        { kind: "ActivateSkill", skill_id: "hero.simayi.fankui", answers_seq: 9900, choices: [], picks: [] },
        { kind: "Pass", answers_seq: 9900 },
      ],
      candidate_index: {}, virtual_index: {}, option_seats: {},
      context: {
        actor: 1, name: "反馈", targets: [1], label: "0号位张飞对1号位司马懿使用杀",
        requirement: "获得伤害来源的一张牌", skill_hints: [],
      },
      preactivations: {}, preactivation: [],
    };
    const view = synthView(activation);
    view.snapshot.seat = 1;
    view.human_seat = 1;
    view.skills = [{
      skill_id: "hero.simayi.fankui", name: "反馈", skill_type: "NORMAL", locked: false,
      description: "反馈的描述。", ui: { passive: true },
    }];
    render(view);
    check("U2-09a 发动询问仍是牌桌表面：「放弃」在发动这一步（不是弹窗）",
      ui.stepDialogKind(activation) === null
        && $("t-dialog").hidden === true
        && $("btn-pass").hidden === false
        && $("btn-pass").textContent === "放弃",
      `kind=${ui.stepDialogKind(activation)} dialogHidden=${$("t-dialog").hidden} passHidden=${$("btn-pass").hidden} pass="${$("btn-pass").textContent}"`);
  }

  // ---- ② 反馈：来源手牌「?」+ 可见装备，同一个弹窗、两区分组 ----
  let view = fankuiView(9901);
  render(view);
  const handSection = zoneSections()[0];
  const equipSection = zoneSections()[1];
  check("U2-09b 反馈 = 未知区域弹窗：来源手牌「?」+ 可见装备，两区分组、归属 / 区域可读",
    ui.stepDialogKind(view.pending) === "unknown"
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "选择一张牌"
      && $("t-dialog-body").children.includes($("t-options"))
      && zoneSections().length === 2
      && handSection.getAttribute("data-zone") === "hand"
      && handSection.getAttribute("data-seat") === "0"
      && handSection.children[0].textContent.includes("座位0 手牌")
      && handSection.children[0].textContent.includes("牌面未知")
      && zoneSlots(handSection).length === 3
      && zoneSlots(handSection).every((node) => node.textContent === "?"
        && node.classList.contains("unknown"))
      && equipSection.getAttribute("data-zone") === "equip"
      && equipSection.children[0].textContent.includes("座位0 装备区")
      && zoneSlots(equipSection).length === 2
      && zoneSlots(equipSection).every((node) => /八卦阵|赤兔/.test(node.text()))
      && $("t-dialog-hint").textContent.includes("获得伤害来源的一张牌")
      && $("t-dialog-hint").textContent.includes("点一张牌即提交")
      && candidateRowButtons().length === 0,
    `kind=${ui.stepDialogKind(view.pending)} 块=${zoneSections().map((n) => n.getAttribute("data-zone")).join(",")} 手牌=${zoneSlots(handSection).length} 装备=${zoneSlots(equipSection).map((n) => n.text()).join(",")} hint="${$("t-dialog-hint").textContent}"`);

  // ---- ③ 弹窗内没有「放弃」/ 四键；可折叠、无关闭 / ESC / 点背景出口 ----
  const actionIds = ["btn-submit", "btn-pass", "btn-cancel", "btn-end-phase", "t-countdown"];
  check("U2-09c 弹窗内没有「放弃」/ 提交 / 取消 / 结束出牌 / 倒计时（操作键只在提示条右端）",
    $("btn-pass").hidden === true
      && $("btn-cancel").disabled === true
      && $("btn-submit").disabled === true
      && actionIds.every((id) => !insideDialog($(id)))
      && !/放弃/.test($("t-dialog").text()),
    `passHidden=${$("btn-pass").hidden} cancel=${$("btn-cancel").disabled} inDialog=${actionIds.map((id) => `${id}:${insideDialog($(id))}`).join(",")}`);

  const beforeEsc = calls.length;
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U2-09d 弹窗没有关闭 / ESC / 点背景出口（零提交、遮罩没有 click 监听）",
    $("t-dialog").hidden === false
      && calls.length === beforeEsc
      && !($("t-modal-backdrop").listeners.click || []).length,
    `dialogHidden=${$("t-dialog").hidden} calls=${calls.length - beforeEsc} backdropClicks=${($("t-modal-backdrop").listeners.click || []).length}`);

  clickCollapse();
  check("U2-09e 弹窗可折叠：正文撤下、标题与载荷步骤提示留下、暗化层收起",
    $("t-dialog-body").hidden === true
      && $("t-dialog-title").textContent === "选择一张牌"
      && $("t-dialog-hint").textContent.includes("获得伤害来源的一张牌")
      && $("t-dialog-collapse").textContent === "展开"
      && $("t-modal-backdrop").hidden === true,
    `bodyHidden=${$("t-dialog-body").hidden} title="${$("t-dialog-title").textContent}" hint="${$("t-dialog-hint").textContent}"`);
  clickCollapse();
  check("U2-09e2 再点「展开」恢复正文与暗化层（折叠不改只读判据）",
    $("t-dialog-body").hidden === false
      && $("t-modal-backdrop").hidden === false
      && ui.selectionEnabled() === false,
    `bodyHidden=${$("t-dialog-body").hidden} backdropHidden=${$("t-modal-backdrop").hidden} enabled=${ui.selectionEnabled()}`);

  // ---- ④ 弹窗打开时牌桌只读，但检视（悬停 / 右键武将详情）照常 ----
  const beforeHandClick = $("t-hand").children[0];
  (beforeHandClick.listeners.click || []).forEach((fn) => fn({ target: beforeHandClick }));
  check("U2-09f 只读态：点自己的手牌 / 座位不产生选择（焦点 / 目标 / 高亮都不变）",
    ui.selectionEnabled() === false
      && ui.state.focusCardId === null
      && ui.state.selectedTargets.length === 0
      && !beforeHandClick.classList.contains("sel")
      && (seatBox(0).listeners.click || []).length === 0
      && !seatBox(0).classList.contains("targetable")
      && !seatBox(0).classList.contains("tsel"),
    `focus=${ui.state.focusCardId} targets=${JSON.stringify(ui.state.selectedTargets)} handSel=${beforeHandClick.classList.contains("sel")} seatClicks=${(seatBox(0).listeners.click || []).length}`);

  const equipFace = zoneSlots(equipSection)[0];
  const detailBody = () => $("t-modal-body").text();
  fireContext(heroBlockOf(0));
  check("U2-09g 检视照常：装备牌面仍带服务端描述（悬停），右键武将详情叠在弹窗上、只读态不变",
    equipFace.getAttribute("title").includes("防具描述")
      && $("t-modal").hidden === false
      && /武将详情/.test($("t-modal-title").textContent)
      && detailBody().includes("咆哮")
      && $("t-dialog").hidden === false
      && ui.selectionEnabled() === false,
    `title="${equipFace.getAttribute("title")}" detailHidden=${$("t-modal").hidden} detailTitle="${$("t-modal-title").textContent}" enabled=${ui.selectionEnabled()}`);
  const sectionsBeforeDetail = zoneSections().length;
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U2-09g2 ESC 只关武将详情；底下的未知区域弹窗原样恢复、仍只读",
    $("t-modal").hidden === true
      && $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && zoneSections().length === sectionsBeforeDetail
      && ui.selectionEnabled() === false,
    `detailHidden=${$("t-modal").hidden} dialogHidden=${$("t-dialog").hidden} sections=${zoneSections().length} enabled=${ui.selectionEnabled()}`);

  // ---- ⑤ 点一张「?」= 恰好一条 SelectCards POST（picks = 槽位位置）；装备牌面同样一击提交 ----
  {
    const beforePick = calls.length;
    staleAnswerStatus = 400;
    await clickAndWait(zoneSlots(handSection)[1]);
    staleAnswerStatus = null;
    const answers = calls.slice(beforePick).filter((call) => call.path.includes("/answer"));
    check("U2-09h 点第 2 张「?」= 恰一条 SelectCards POST，picks 填该槽位位置、不是 Pass",
      answers.length === 1
        && JSON.stringify(answers[0].body.action)
          === JSON.stringify(fankuiPending(9901).legal_actions[1])
        && answers[0].body.action.kind === "SelectCards"
        && JSON.stringify(answers[0].body.action.picks) === "[1]",
      `answers=${answers.length} action=${JSON.stringify(answers[0] && answers[0].body.action)}`);
  }
  {
    // 400 之后界面会刷新回真会话的询问；重新喂同一份合成视图再点装备槽。
    render(fankuiView(9901));
    const beforePick = calls.length;
    staleAnswerStatus = 400;
    await clickAndWait(zoneSlots(zoneSections()[1])[1]);
    await settleSubmit();
    staleAnswerStatus = null;
    const answers = calls.slice(beforePick).filter((call) => call.path.includes("/answer"));
    check("U2-09i 装备牌面一击也提交（可见牌面不是只读展示）：picks = 装备槽位置",
      answers.length === 1
        && JSON.stringify(answers[0].body.action)
          === JSON.stringify(fankuiPending(9901).legal_actions[4])
        && JSON.stringify(answers[0].body.action.picks) === "[4]",
      `answers=${answers.length} action=${JSON.stringify(answers[0] && answers[0].body.action)}`);
  }

  // ---- ⑥ 过河拆桥 / 顺手牵羊：目标三区在同一弹窗，判定 / 装备可见、手牌「?」 ----
  const zonePending = (seq, name) => ({
    kind: "SELECT_CARDS", actor: 1, seq, prompt: "请从选择面里选择一张牌。",
    legal_actions: [0, 1, 2, 3].map(
      (index) => ({ kind: "SelectCards", answers_seq: seq, picks: [index] })),
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [
      { ref: { seat: 0, zone: "hand" }, card: null },
      { ref: { seat: 0, zone: "hand" }, card: null },
      { ref: { seat: 0, zone: "equip" }, card: armor },
      { ref: { seat: 0, zone: "judge" }, card: judgeCard },
    ],
    spec: { zones: ["hand", "equip", "judge"], total: [1, 1], sequential: false, optional: false },
    context: {
      actor: 1, name, targets: [0],
      label: `1号位关羽对0号位张飞使用${name}`,
      requirement: "选择目标区域里的一张牌", skill_hints: [],
    },
    prompt_display: { text: "请从选择面里选择一张牌。", default_label: "" },
    preactivations: {}, preactivation: [],
  });
  const zoneView = (seq, name) => {
    const view = synthView(zonePending(seq, name));
    view.snapshot.seat = 1;
    view.human_seat = 1;
    return view;
  };

  for (const [seq, name] of [[9910, "过河拆桥"], [9920, "顺手牵羊"]]) {
    const targetView = zoneView(seq, name);
    render(targetView);
    const zones = zoneSections().map((node) => node.getAttribute("data-zone")).join(",");
    check(`U2-09j ${name} = 目标三区同一弹窗（手牌「?」+ 可见装备 / 判定），步骤提示读载荷`,
      ui.stepDialogKind(targetView.pending) === "unknown"
        && $("t-dialog").hidden === false
        && $("t-dialog-title").textContent === "选择一张牌"
        && ui.state.dialog.key === `chain:${name}:1`
        && zones === "hand,equip,judge"
        && zoneSections().every((node) => node.getAttribute("data-seat") === "0")
        && zoneSlots(zoneSections()[0]).length === 2
        && zoneSlots(zoneSections()[0]).every((node) => node.textContent === "?")
        && zoneSlots(zoneSections()[1])[0].text().includes("八卦阵")
        && zoneSlots(zoneSections()[2])[0].text().includes("乐不思蜀")
        && $("t-dialog-hint").textContent.includes("选择目标区域里的一张牌")
        && $("btn-pass").hidden === true
        && $("btn-cancel").disabled === true
        && candidateRowButtons().length === 0,
      `kind=${ui.stepDialogKind(targetView.pending)} key=${ui.state.dialog.key} 块=${zones} hint="${$("t-dialog-hint").textContent}"`);
  }

  {
    const judgeView = zoneView(9930, "顺手牵羊");
    render(judgeView);
    const judgeNode = zoneSlots(zoneSections()[2])[0];
    const beforePick = calls.length;
    staleAnswerStatus = 400;
    await clickAndWait(judgeNode);
    await settleSubmit();
    staleAnswerStatus = null;
    const answers = calls.slice(beforePick).filter((call) => call.path.includes("/answer"));
    check("U2-09k 拆 / 顺：点判定区的一张可见牌也一击提交（三区都是同一个提交点）",
      answers.length === 1
        && JSON.stringify(answers[0].body.action)
          === JSON.stringify(zonePending(9930, "顺手牵羊").legal_actions[3])
        && JSON.stringify(answers[0].body.action.picks) === "[3]",
      `answers=${answers.length} action=${JSON.stringify(answers[0] && answers[0].body.action)} clicks=${(judgeNode.listeners.click || []).length} kind=${ui.stepDialogKind(judgeView.pending)} busy=${ui.state.submitBusy} session=${Boolean(ui.state.sessionId)}`);
  }

  // ---- ⑦ 08 的反间盲挑（单区手牌）不回归：仍是同一个模板，不多画装备区 ----
  {
    const blind = zoneView(9940, "反间");
    blind.pending.slots = [0, 1, 2, 3, 4].map(
      () => ({ ref: { seat: 0, zone: "hand" }, card: null }));
    blind.pending.spec = { zones: ["hand"], total: [1, 1], sequential: false, optional: true };
    blind.pending.context.requirement = "由目标从你的手牌中选择一张";
    blind.pending.legal_actions = [
      ...[0, 1, 2, 3, 4].map(
        (index) => ({ kind: "SelectCards", answers_seq: 9940, picks: [index] })),
      { kind: "Pass", answers_seq: 9940 },
    ];
    render(blind);
    check("U2-09l 08 的反间盲挑单区形状照旧：五个「?」、只有手牌一区、无装备区、无「放弃」",
      ui.stepDialogKind(blind.pending) === "unknown"
        && zoneSections().length === 1
        && zoneSections()[0].getAttribute("data-zone") === "hand"
        && zoneSlots(zoneSections()[0]).length === 5
        && !/装备区/.test($("t-options").text())
        && $("btn-pass").hidden === true
        && $("t-dialog-hint").textContent.includes("由目标从你的手牌中选择一张"),
      `kind=${ui.stepDialogKind(blind.pending)} 块=${zoneSections().length} 槽=${zoneSlots(zoneSections()[0]).length} hint="${$("t-dialog-hint").textContent}"`);
  }

  // ---- ⑧ 判据的**排除面**：亮牌 / 处理区 / 可见装备 / 提供 B / 自己的牌不进未知区域模板 ----
  {
    const card = synthCard(501, "桃");
    const pick = (seq, action) => ({ kind: "SelectCards", answers_seq: seq, picks: [0] });
    const yiji = {
      kind: "SELECT_CARDS", actor: 0, seq: 9950,
      legal_actions: [pick(9950)], candidate_index: {},
      slots: [{ ref: { seat: 0, zone: "hand" }, card }],
      spec: { zones: ["hand"], total: [1, 2] }, revealed: [card],
    };
    const wugu = {
      kind: "SELECT_CARDS", actor: 0, seq: 9951,
      legal_actions: [pick(9951)], candidate_index: {},
      slots: [{ ref: { seat: null, zone: "processing" }, card }],
      spec: { zones: ["processing"], total: [1, 1] },
    };
    const qilin = {
      kind: "SELECT_CARDS", actor: 0, seq: 9952,
      legal_actions: [pick(9952)], candidate_index: {},
      slots: [{ ref: { seat: 1, zone: "equip" }, card: horse }],
      spec: { zones: ["equip"], total: [1, 1] },
    };
    const provide = {
      kind: "SELECT_CARDS", actor: 0, seq: 9953, provide_type: "slash",
      legal_actions: [pick(9953)], candidate_index: {},
      slots: [{ ref: { seat: 1, zone: "hand" }, card: null }],
      spec: { zones: ["hand"], total: [1, 1] },
    };
    const ownPick = {
      kind: "SELECT_CARDS", actor: 0, seq: 9954,
      legal_actions: [pick(9954)], candidate_index: {},
      slots: [{ ref: { seat: 0, zone: "hand" }, card: null }],
      spec: { zones: ["hand"], total: [1, 1] },
    };
    const twoSeats = {
      kind: "SELECT_CARDS", actor: 0, seq: 9955,
      legal_actions: [pick(9955)], candidate_index: {},
      slots: [
        { ref: { seat: 1, zone: "hand" }, card: null },
        { ref: { seat: 2, zone: "hand" }, card: null },
      ],
      spec: { zones: ["hand"], total: [1, 1] },
    };
    check("U2-09m 判据排除面：遗计亮牌 / 五谷处理区 / 麒麟弓可见装备 / 提供 B / 自己的牌 / 跨座位都不抢成未知区域",
      ui.stepDialogKind(yiji) === null
        && ui.stepDialogKind(wugu) === null
        && ui.stepDialogKind(qilin) === null
        && ui.stepDialogKind(provide) === null
        && ui.stepDialogKind(ownPick) === null
        && ui.stepDialogKind(twoSeats) === null,
      `yiji=${ui.stepDialogKind(yiji)} wugu=${ui.stepDialogKind(wugu)} qilin=${ui.stepDialogKind(qilin)} provide=${ui.stepDialogKind(provide)} own=${ui.stepDialogKind(ownPick)} twoSeats=${ui.stepDialogKind(twoSeats)}`);

    // 来源手牌为空时反馈 / 拆 / 顺只剩可见装备 / 判定：仍是同一步、同一个模板（不退回旧选择面）。
    const emptyHand = {
      kind: "SELECT_CARDS", actor: 1, seq: 9956,
      legal_actions: [pick(9956)], candidate_index: {},
      slots: [{ ref: { seat: 0, zone: "equip" }, card: armor }],
      spec: { zones: ["hand", "equip"], total: [1, 1] },
      context: { actor: 1, name: "反馈", targets: [1], requirement: "获得伤害来源的一张牌" },
    };
    check("U2-09n 手牌为空的来源（只剩可见装备）仍走未知区域模板：区域按实际提供的画",
      ui.stepDialogKind(emptyHand) === "unknown"
        && (() => {
          const v = synthView(emptyHand);
          v.snapshot.seat = 1;
          v.human_seat = 1;
          render(v);
          return zoneSections().length === 1
            && zoneSections()[0].getAttribute("data-zone") === "equip"
            && zoneSlots(zoneSections()[0])[0].text().includes("八卦阵");
        })(),
      `kind=${ui.stepDialogKind(emptyHand)} 块=${zoneSections().map((n) => n.getAttribute("data-zone")).join(",")}`);
  }

  // ---- ⑨ 同一条链上的连续两次未知区域询问共用一个容器（寒冰剑两轮 / 票 13 的底座） ----
  {
    const chainPending = (seq, slots) => ({
      kind: "SELECT_CARDS", actor: 1, seq, prompt: "请从选择面里选择一张牌。",
      legal_actions: slots.map(
        (_, index) => ({ kind: "SelectCards", answers_seq: seq, picks: [index] })),
      candidate_index: {}, virtual_index: {}, option_seats: {},
      slots,
      spec: { zones: ["hand", "equip"], total: [1, 1], sequential: false, optional: false },
      context: {
        actor: 1, name: "杀", targets: [1],
        label: "0号位张飞对1号位司马懿使用杀",
        requirement: "获得伤害来源的一张牌", skill_hints: [],
      },
      preactivations: {}, preactivation: [],
    });
    const chainView = (seq, slots) => {
      const v = synthView(chainPending(seq, slots));
      v.snapshot.seat = 1;
      v.human_seat = 1;
      return v;
    };
    render(chainView(9960, [
      { ref: { seat: 0, zone: "hand" }, card: null },
      { ref: { seat: 0, zone: "hand" }, card: null },
      { ref: { seat: 0, zone: "equip" }, card: armor },
    ]));
    const chainKey = ui.state.dialog.key;
    render(chainView(9961, [
      { ref: { seat: 0, zone: "hand" }, card: null },
      { ref: { seat: 0, zone: "hand" }, card: null },
    ]));
    check("U2-09o 同一技能链的先后两次未知区域询问共用键：容器不关、正文换成新一步",
      chainKey === "chain:杀:1"
        && ui.state.dialog.key === chainKey
        && $("t-dialog").hidden === false
        && $("t-dialog-body").children.includes($("t-options"))
        && zoneSections().length === 1
        && zoneSections()[0].getAttribute("data-zone") === "hand",
      `key=${ui.state.dialog.key} before=${chainKey} hidden=${$("t-dialog").hidden} 块=${zoneSections().map((n) => n.getAttribute("data-zone")).join(",")}`);
  }

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ U2-11（D3 / D4 / D10）：五谷丰登亮牌弹窗 + 目标条 ============
 *
 * 载荷形状与真会话同形（`harvest` 的字段名 / 状态值由 `tests/test_web.py` 的真会话断言钉）：
 * `harvest.revealed` 是**全部**亮牌（按亮牌顺序，带 `taken_by`），`harvest.targets` 是全部
 * 目标（按拿牌顺序，带 `status` / `card` / 保留的 `cancelled`），`harvest.current` 是当前目标。
 *
 * 本段只钉**界面读法**：置灰 + 座位号 + 位置保持、目标条四态（含"取消"红）、点一张亮牌 = 一次
 * `SelectCards`、没有「放弃」、AI 拿牌时弹窗不关且不可点、整段结算共用链键、折叠 / 只读 / 检视。
 */
{
  const STYLE = readFileSync("tools/sgs_web/static/style.css", "utf8");
  const cardOf = (id, name, type, description) => ({
    ...synthCard(id, name),
    type,
    display_name: name,
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description,
  });
  const wuguCard = cardOf(5, "五谷丰登", "harvest", "五谷丰登的描述：亮出牌堆顶 X 张。");
  const takenCard = cardOf(2, "闪", "dodge", "闪的描述：抵消一张杀。");
  const secondCard = cardOf(3, "桃", "peach", "桃的描述：回复 1 点体力。");
  const thirdCard = cardOf(4, "杀", "slash", "杀的描述：造成 1 点伤害。");
  const fourthCard = cardOf(6, "无懈可击", "nullify", "无懈可击的描述：抵消一张锦囊。");
  const withTaker = (card, takenBy = null) => ({ ...card, taken_by: takenBy });
  const targetRow = (seat, status, card = null, cancelled = false) => ({
    seat, status, card, cancelled, reason: null,
  });
  /** 一份与 `/api/sessions` 的 `pending` 同形的五谷取牌询问（harvest 字段见 test_web 真会话）。 */
  const wuguPending = ({ seq, actor, current, key = "harvest:2:5:0", revealed, targets, slots }) => ({
    kind: "SELECT_CARDS", actor, seq, prompt: "请从选择面里选择一张牌。",
    legal_actions: slots.map((_, index) => ({
      kind: "SelectCards", answers_seq: seq, picks: [index],
    })),
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: slots.map((card) => ({ ref: { seat: null, zone: "processing" }, card })),
    spec: { zones: ["processing"], total: [1, 1], sequential: false, optional: false },
    harvest: {
      active: true, key, title: "五谷丰登", hint: "从亮出的牌里选一张获得",
      source: 0, card: wuguCard, revealed, targets, current,
    },
    context: {
      actor: 0, name: "五谷丰登",
      targets: slots.map(() => 0).slice(0, 1),
      label: "0号位使用五谷丰登",
      requirement: "从亮出的牌里选一张获得", skill_hints: [],
    },
    prompt_display: { text: "请从选择面里选择一张牌。", default_label: "超时将自动选择第一项" },
    preactivations: {}, preactivation: [],
  });
  const wuguView = (pending, seat) => {
    const view = synthView(pending);
    view.snapshot.seat = seat;
    view.human_seat = seat;
    return view;
  };
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const insideDialog = (node) => {
    let at = node ? node.parentNode : null;
    while (at) {
      if (at === $("t-dialog")) return true;
      at = at.parentNode;
    }
    return false;
  };
  const revealWrap = () => $("t-options").children[0];
  const revealCards = () => (revealWrap() ? revealWrap().children[0].children : []);
  const revealBar = () => (revealWrap()
    ? revealWrap().children.find((node) => node.classList.contains("reveal-targets")) || null
    : null);
  const targetChips = () => (revealBar()
    ? revealBar().children.filter((node) => node.classList.contains("reveal-target"))
    : []);
  const chipStates = () => targetChips().map((node) => node.getAttribute("data-state")).join(",");
  const chipSeats = () => targetChips().map((node) => node.getAttribute("data-seat")).join(",");
  const clickCollapse = () => ($("t-dialog-collapse").listeners.click || [])
    .forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  const heroBlockOf = (seat) => {
    const box = seatBox(seat);
    return box
      ? box.children.find((node) => (node.className || "").split(" ").includes("hero-block")) || null
      : null;
  };
  const fireContext = (node) => {
    const event = { target: node, prevented: false, stopPropagation() {} };
    event.preventDefault = () => { event.prevented = true; };
    if (node) (node.listeners.contextmenu || []).forEach((fn) => fn(event));
    return event;
  };
  /** `cardFaceNode` 的 click 包装是**同步**转发（不 return `onClick()` 的 promise）：
   *  等"提交在飞"结束再断言 / 进入下一段，避免撞进四键冻结态（U2-09i 的同一写法）。 */
  const settleSubmit = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };

  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261014,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  ui.state.modal.heroSeat = null;

  // ---- ① 人类是当前目标：全部亮牌 + 拿走的牌置灰 + 座位号 + 目标条四态 ----
  const ownTargets = [
    targetRow(0, "taken", 2),
    targetRow(1, "invalid"),                       // 被无懈（最终没拿到）
    targetRow(2, "current"),
    targetRow(3, "pending"),
    targetRow(4, "pending", null, true),            // 未来：先被指定、后被取消 ⇒ 同一种红
  ];
  const ownPending = wuguPending({
    seq: 1101, actor: 2, current: 2,
    revealed: [withTaker(takenCard, 0), withTaker(secondCard), withTaker(thirdCard), withTaker(fourthCard)],
    targets: ownTargets,
    slots: [secondCard, thirdCard, fourthCard],
  });
  render(wuguView(ownPending, 2));

  check("U2-11a 五谷取牌步 = 亮牌弹窗：标题「五谷丰登」、公开处理区文案、正文在弹窗里",
    ui.stepDialogKind(ownPending) === "reveal"
      && $("t-dialog").hidden === false
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-dialog-title").textContent === "五谷丰登"
      && $("t-prompt").textContent === "从亮出的牌里选一张获得"
      && $("t-dialog-hint").textContent.includes("从亮出的牌里选一张获得")
      && $("t-dialog-hint").textContent.includes("点一张亮牌即提交")
      && !/隐藏区域/.test($("t-dialog").text())
      && !/请从选择面里选择一张牌/.test($("t-dialog").text()),
    `kind=${ui.stepDialogKind(ownPending)} title="${$("t-dialog-title").textContent}" prompt="${$("t-prompt").textContent}" hint="${$("t-dialog-hint").textContent}"`);

  check("U2-11b 全部亮牌按亮牌顺序铺出：位置不动、牌面可读",
    revealCards().length === 4
      && revealCards().map((node) => node.getAttribute("data-card-id")).join(",") === "2,3,4,6"
      && revealCards().map((node) => node.text()).join(",").includes("闪")
      && revealCards().map((node) => node.text()).join(",").includes("无懈可击"),
    `卡=${revealCards().map((node) => `${node.getAttribute("data-card-id")}:${node.text().replace(/\s+/g, "")}`).join("/")}`);

  check("U2-11c 被拿走的牌留在原位、置灰、标座号 0，牌面描述仍在（检视可用）",
    revealCards()[0].classList.contains("taken")
      && revealCards()[0].getAttribute("data-taken-by") === "0"
      && (revealCards()[0].children.find((node) => node.classList.contains("taken-by")) || {}).textContent === "座0"
      && revealCards()[0].getAttribute("title").includes("闪的描述")
      && !revealCards()[1].classList.contains("taken")
      && revealCards()[1].getAttribute("data-taken-by") === "",
    `taken=${revealCards()[0].classList.contains("taken")} by=${revealCards()[0].getAttribute("data-taken-by")} title="${revealCards()[0].getAttribute("title")}"`);

  check("U2-11d 目标条按拿牌顺序铺全部座位：已拿（勾）/ 无效（红）/ 当前（高亮）/ 未轮到（灰）",
    targetChips().length === 5
      && chipSeats() === "0,1,2,3,4"
      && chipStates() === "taken,invalid,current,pending,invalid"
      && targetChips()[0].classList.contains("is-taken")
      && targetChips()[0].textContent === "座0✓"
      && targetChips()[1].classList.contains("is-invalid")
      && targetChips()[1].textContent === "座1✕"
      && targetChips()[2].classList.contains("is-current")
      && targetChips()[2].textContent === "座2◀"
      && targetChips()[3].classList.contains("is-pending")
      && targetChips()[3].textContent === "座3"
      && revealBar().getAttribute("data-current") === "2",
    `seats=${chipSeats()} states=${chipStates()} texts=${JSON.stringify(targetChips().map((node) => node.textContent))}`);

  check("U2-11d2 「先被指定、后被取消」保留位与无懈同一种红（本轮没有生产者，样式 / 字段先接好）",
    targetChips()[4].getAttribute("data-state") === "invalid"
      && targetChips()[4].classList.contains("is-invalid")
      && targetChips()[4].textContent === "座4✕",
    `state=${targetChips()[4].getAttribute("data-state")} class=${targetChips()[4].className}`);

  check("U2-11e 五谷步没有「放弃」：右端隐藏、取消 / 提交都不抢跑，弹窗里没有操作四键",
    $("btn-pass").hidden === true
      && $("btn-cancel").disabled === true
      && $("btn-submit").disabled === true
      && !/放弃/.test($("t-dialog").text())
      && ["btn-submit", "btn-cancel", "btn-pass", "btn-end-phase", "t-countdown"]
        .every((id) => !insideDialog($(id))),
    `passHidden=${$("btn-pass").hidden} cancel=${$("btn-cancel").disabled} submit=${$("btn-submit").disabled} actionsInDialog=${["btn-submit", "btn-cancel", "btn-pass", "btn-end-phase", "t-countdown"].filter((id) => insideDialog($(id))).join(",")}`);

  check("U2-11f 弹窗打开时牌桌只读，但检视（牌面描述 / 右键武将详情）照常",
    ui.selectionEnabled() === false
      && revealCards()[0].getAttribute("title").includes("闪的描述")
      && (() => {
        fireContext(heroBlockOf(0));
        return $("t-modal").hidden === false && $("t-dialog").hidden === false;
      })(),
    `enabled=${ui.selectionEnabled()} detailHidden=${$("t-modal").hidden} dialogHidden=${$("t-dialog").hidden} title="${revealCards()[0].getAttribute("title")}"`);
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U2-11f2 ESC 只关武将详情；亮牌弹窗无关闭 / ESC / 点背景出口",
    $("t-modal").hidden === true
      && $("t-dialog").hidden === false
      && !($("t-modal-backdrop").listeners.click || []).length,
    `detailHidden=${$("t-modal").hidden} dialogHidden=${$("t-dialog").hidden} backdropClicks=${($("t-modal-backdrop").listeners.click || []).length}`);

  // 牌桌选择入口 no-op：点自己的手牌不会设焦点（只读判据没有为亮牌开小门）。
  const handNode = $("t-hand").children[0];
  if (handNode) (handNode.listeners.click || []).forEach((fn) => fn({ target: handNode }));
  check("U2-11f3 只读态：点自己的手牌不产生选择（selectionEnabled 未为亮牌改判据）",
    ui.state.focusCardId === null && !(handNode && handNode.classList.contains("sel")),
    `focus=${ui.state.focusCardId} sel=${handNode && handNode.classList.contains("sel")}`);

  // ---- ② 折叠仍留标题 + 步骤提示；展开恢复 ----
  clickCollapse();
  check("U2-11g 折叠只撤正文：标题「五谷丰登」与公开处理区提示仍在标题条、暗化层收起",
    $("t-dialog-body").hidden === true
      && $("t-dialog-title").textContent === "五谷丰登"
      && $("t-dialog-hint").textContent.includes("从亮出的牌里选一张获得")
      && $("t-dialog-collapse").textContent === "展开"
      && $("t-modal-backdrop").hidden === true,
    `bodyHidden=${$("t-dialog-body").hidden} title="${$("t-dialog-title").textContent}" hint="${$("t-dialog-hint").textContent}"`);
  clickCollapse();
  check("U2-11g2 展开恢复正文与暗化层，只读判据不变",
    $("t-dialog-body").hidden === false
      && $("t-modal-backdrop").hidden === false
      && ui.selectionEnabled() === false);

  // ---- ③ 点一张亮牌 = 恰好一条 SelectCards POST（picks = 槽位位置） ----
  const beforePick = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait(revealCards()[1]);
  await settleSubmit();
  staleAnswerStatus = null;
  const pickAnswers = calls.slice(beforePick).filter((call) => call.path.includes("/answer"));
  check("U2-11h 点一张亮牌 = 一次提交（一条 SelectCards POST，picks 填该牌在剩余亮牌里的位置）",
    pickAnswers.length === 1
      && JSON.stringify(pickAnswers[0].body.action) === JSON.stringify(ownPending.legal_actions[0])
      && JSON.stringify(pickAnswers[0].body.action.picks) === "[0]"
      && pickAnswers[0].body.action.kind === "SelectCards",
    `answers=${pickAnswers.length} action=${JSON.stringify(pickAnswers[0] && pickAnswers[0].body.action)}`);

  // ---- ④ 整段结算的链键：AI 目标拿牌 → 人类目标拿牌，弹窗不关（只换正文 / 状态） ----
  const foreignTargets = [
    targetRow(0, "taken", 2),
    targetRow(1, "current"),
    targetRow(2, "pending"),
    targetRow(3, "pending"),
  ];
  const foreignPending = wuguPending({
    seq: 1102, actor: 1, current: 1,
    revealed: [withTaker(takenCard, 0), withTaker(secondCard), withTaker(thirdCard), withTaker(fourthCard)],
    targets: foreignTargets,
    slots: [secondCard, thirdCard, fourthCard],
  });
  render(wuguView(foreignPending, 0));
  const chainKey = ui.state.dialog.key;
  const foreignCalls = calls.length;
  const untakenNode = revealCards()[1];
  (untakenNode.listeners.click || []).forEach((fn) => fn({ target: untakenNode }));
  check("U2-11i AI 目标拿牌时弹窗仍显示、仍只读、不可点；提示写明在等谁",
    ui.state.dialog.key === `reveal:${foreignPending.harvest.key}`
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "五谷丰登"
      && chipStates() === "taken,current,pending,pending"
      && $("t-dialog-hint").textContent.includes("等待1号位拿牌")
      && $("t-prompt").textContent.includes("等待其他座位响应")
      && ui.selectionEnabled() === false
      && (untakenNode.listeners.click || []).length === 0
      && calls.length === foreignCalls,
    `key=${ui.state.dialog.key} hidden=${$("t-dialog").hidden} chips=${chipStates()} hint="${$("t-dialog-hint").textContent}" clicks=${(untakenNode.listeners.click || []).length} calls=${calls.length - foreignCalls}`);

  const ownNextTargets = [
    targetRow(0, "taken", 2),
    targetRow(1, "taken", 3),
    targetRow(2, "current"),
    targetRow(3, "pending"),
  ];
  const ownNext = wuguPending({
    seq: 1103, actor: 2, current: 2,
    revealed: [withTaker(takenCard, 0), withTaker(secondCard, 1), withTaker(thirdCard), withTaker(fourthCard)],
    targets: ownNextTargets,
    slots: [thirdCard, fourthCard],
  });
  render(wuguView(ownNext, 2));
  check("U2-11j 换 actor / 换 seq 不关窗：同一个链键、同一容器，正文与目标条随拿牌更新",
    chainKey === `reveal:${ownNext.harvest.key}`
      && ui.state.dialog.key === chainKey
      && $("t-dialog").hidden === false
      && $("t-dialog-body").children.includes($("t-options"))
      && revealCards()[1].classList.contains("taken")
      && (revealCards()[1].children.find((node) => node.classList.contains("taken-by")) || {}).textContent === "座1"
      && chipStates() === "taken,taken,current,pending"
      && $("t-dialog-hint").textContent.includes("点一张亮牌即提交"),
    `key=${ui.state.dialog.key} before=${chainKey} hidden=${$("t-dialog").hidden} chips=${chipStates()} hint="${$("t-dialog-hint").textContent}"`);

  const beforeNextPick = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait(revealCards()[2]);
  await settleSubmit();
  staleAnswerStatus = null;
  const nextAnswers = calls.slice(beforeNextPick).filter((call) => call.path.includes("/answer"));
  check("U2-11k 人类轮到时点一张亮牌同样一击提交（换步骤后的提交点没漂）",
    nextAnswers.length === 1
      && JSON.stringify(nextAnswers[0].body.action) === JSON.stringify(ownNext.legal_actions[0])
      && JSON.stringify(nextAnswers[0].body.action.picks) === "[0]",
    `answers=${nextAnswers.length} action=${JSON.stringify(nextAnswers[0] && nextAnswers[0].body.action)} busy=${ui.state.submitBusy} session=${Boolean(ui.state.sessionId)} listeners=${(revealCards()[2].listeners.click || []).length} kind=${ui.stepDialogKind(ownNext)} picks=${JSON.stringify(ui.state.picks)}`);

  check("U2-11l 亮牌 / 目标条的样式落地（置灰、四态、左下角）",
    STYLE.includes(".card.face.reveal-card.taken")
      && STYLE.includes("filter: grayscale(1)")
      && STYLE.includes(".reveal-targets")
      && STYLE.includes("position: absolute")
      && STYLE.includes(".reveal-target.is-invalid")
      && STYLE.includes("background: #b3261e")
      && STYLE.includes(".reveal-target.is-current")
      && STYLE.includes(".reveal-target.is-pending")
      && STYLE.includes(".dialog-body.reveal-body"),
    `taken=${STYLE.includes(".card.face.reveal-card.taken")} invalid=${STYLE.includes(".reveal-target.is-invalid")} bar=${STYLE.includes(".reveal-targets")}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ U2-review-fix（D4）：五谷弹窗跨无懈 / 反无懈 RESPOND 窗口持续 ============
 *
 * **生产时序证据**（真引擎复核，见 `tests/test_web.py` 的 harvest 真会话段）：亮牌发生在
 * `USE_AFTER_DESIGNATE_TARGET`，收集式无懈窗口在 `RESOLVE_BEFORE_EFFECT`——即亮牌**之后**；
 * `projection.pending_payload` 在无懈 / 反无懈的 `RESPOND` pending 上仍带 `harvest`。
 * 故 `RESPOND` 步是"这一步的表面是牌桌、结算帧还活着"：亮牌 + 目标条必须继续显示，
 * 但 `#t-options` / 手牌 / 座位仍归牌桌（无懈照常可出、可放弃，只读判据不翻）。
 *
 * 两段：
 *   ① 合成载荷（字段与真 payload 同形）：窗口不关、不占选项行、无懈可提交、下一次取牌
 *      复用同一个链键；
 *   ② **真生产路径**（种子 198、5 人、random AI、人类座位 0）：开局就是"人类手里有【无懈】、
 *      五谷已亮牌"的 RESPOND 窗口；逐字段断言 DOM 读的是 `harvest.revealed / taken_by /
 *      targets / current`，再驱动到真实取牌步，断言同一窗口切回正常取牌模式。
 */
{
  const harvestCard = (id, name, type, description) => ({
    ...synthCard(id, name),
    type,
    display_name: name,
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description,
  });
  const revealCard = (id, name, type) => harvestCard(id, name, type, `${name}的描述：悬停可读。`);
  const withSeat = (card, seat) => ({ ...card, taken_by: seat });
  const dodge = revealCard(11, "闪", "dodge");
  const peach = revealCard(12, "桃", "peach");
  const slash = revealCard(13, "杀", "slash");
  const nullify = revealCard(78, "无懈可击", "nullify");
  const harvestCardFace = revealCard(7, "五谷丰登", "harvest");
  const target = (seat, status, card = null, cancelled = false) => ({
    seat, status, card, cancelled, reason: null,
  });
  /** 与真 payload 同形的五谷结算状态（字段见 tests/test_web.py 的真会话断言）。 */
  const harvest = (key, current, revealed, targets) => ({
    active: true, key, title: "五谷丰登", hint: "从亮出的牌里选一张获得",
    source: 2, card: harvestCardFace, revealed, targets, current,
  });
  const view = (pending) => {
    const made = synthView(pending);
    made.snapshot.seat = 0;
    made.human_seat = 0;
    made.snapshot.self_hand = [nullify, synthCard(1, "杀")];
    return made;
  };
  const render = (pending) => {
    ui.state.view = view(pending);
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(ui.state.view);
  };
  const insideDialog = (node) => {
    let at = node ? node.parentNode : null;
    while (at) {
      if (at === $("t-dialog")) return true;
      at = at.parentNode;
    }
    return false;
  };
  /** 亮牌正文的根：持久模式住 `#t-dialog-reveal`，正常取牌住在 `#t-options`（被搬进正文）。 */
  const revealRoot = () => {
    const persistent = $("t-dialog-reveal");
    if (persistent.hidden === false && persistent.children.length) return persistent.children[0];
    return $("t-options").children[0] || null;
  };
  const cards = () => (revealRoot() ? revealRoot().children[0].children : []);
  const bar = () => (revealRoot()
    ? revealRoot().children.find((node) => node.classList.contains("reveal-targets")) || null
    : null);
  const chips = () => (bar()
    ? bar().children.filter((node) => node.classList.contains("reveal-target"))
    : []);
  const chipStates = () => chips().map((node) => node.getAttribute("data-state")).join(",");
  const chipSeats = () => chips().map((node) => node.getAttribute("data-seat")).join(",");
  const takenBy = () => cards().map((node) => node.getAttribute("data-taken-by")).join(",");
  const settleSubmit = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };

  // ---- ① 合成 RESPOND/nullify pending：窗口持续、不抢选项行、无懈可击可提交 ----
  const responsePending = {
    kind: "RESPOND", actor: 0, seq: 1201, cause: "NULLIFY", target: 2,
    prompt: "响应窗口：请使用一张牌，或放弃。",
    legal_actions: [
      { answers_seq: 1201, kind: "UseCard", card: nullify, targets: [] },
      { answers_seq: 1201, kind: "Pass" },
    ],
    candidate_index: {
      [String(nullify.card_id)]: {
        card: nullify, pool: [], min_targets: 0, max_targets: 0, combos: [[]],
      },
    },
    virtual_index: {}, option_seats: {}, option_labels: null,
    preactivations: {}, card_preactivations: {}, preactivation: [],
    harvest: harvest(
      "harvest:9:7:2", 2,
      [withSeat(dodge, null), withSeat(peach, null), withSeat(slash, 1), withSeat(nullify, null)],
      [target(2, "current"), target(0, "pending"), target(1, "taken", slash.card_id)],
    ),
    context: {
      actor: 2, name: "五谷丰登", targets: [2],
      label: "2号位使用五谷丰登", requirement: "需要使用【无懈可击】", skill_hints: [],
    },
    prompt_display: { text: "响应窗口：请使用一张牌，或放弃。", default_label: "超时将自动放弃" },
  };
  const scratchSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261014,
  });
  ui.state.sessionId = scratchSession.session_id;
  const bareReveal = {
    ...responsePending,
    harvest: { ...responsePending.harvest, hint: null },
    context: null,
  };
  check("U2-R0 亮牌提示缺服务端文案时是中性兜底（前端不抄 HARVEST_REQUIREMENT）",
    ui.revealHintText(bareReveal, true) === "从亮出的牌里选择：点一张亮牌即提交。",
    ui.revealHintText(bareReveal, true));
  render(responsePending);
  const persistentKey = ui.state.dialog.key;

  check("U2-R1 无懈 RESPOND 步的表面仍是牌桌（只读判据不翻），但五谷亮牌窗继续显示",
    ui.stepDialogKind(responsePending) === null
      && ui.selectionEnabled() === true
      && $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && $("t-dialog-reveal").hidden === false
      && $("t-dialog-title").textContent === "五谷丰登"
      && $("t-modal-backdrop").hidden === true,
    `surface=${ui.stepDialogKind(responsePending)} enabled=${ui.selectionEnabled()} dialogHidden=${$("t-dialog").hidden} bodyHidden=${$("t-dialog-body").hidden} revealHidden=${$("t-dialog-reveal").hidden} backdropHidden=${$("t-modal-backdrop").hidden}`);

  check("U2-R2 持久亮牌 + 目标条照读 harvest 字段（位置 / 获得者 / 四态 / 当前目标）",
    cards().length === 4
      && takenBy() === ",,1,"
      && chips().length === 3
      && chipSeats() === "2,0,1"
      && chipStates() === "current,pending,taken"
      && bar().getAttribute("data-current") === "2"
      && $("t-dialog-hint").textContent.includes("等待2号位拿牌"),
    `cards=${cards().length} takenBy=${takenBy()} seats=${chipSeats()} states=${chipStates()} current=${bar() && bar().getAttribute("data-current")} hint="${$("t-dialog-hint").textContent}"`);

  check("U2-R3 持久展示不抢 #t-options：选项行仍在提示区，无懈界面不被搬进弹窗",
    $("t-options-home").children.includes($("t-options"))
      && !insideDialog($("t-options"))
      && !insideDialog($("btn-submit")),
    `home=${$("t-options-home").children.includes($("t-options"))} optionsInDialog=${insideDialog($("t-options"))}`);

  // 提示区选项行的**内容**不能被持久同步清掉。真实的无懈窗口只有 UseCard + Pass
  // （picker 的选项行本来就是空的），故给同一份合成载荷补一条 `SelectOption` 备选，
  // 让正常 picker 铺出一个按钮；持久同步之后它必须还在提示区。窗口表面 / `harvest`
  // 字段仍与真 payload 同形——这条只钉"同步不得清空提示区"这一条回归。
  const responseWithAlternative = {
    ...responsePending,
    legal_actions: [
      ...responsePending.legal_actions,
      { answers_seq: 1201, kind: "SelectOption", choice: "seat:1" },
    ],
  };
  render(responseWithAlternative);
  const promptButtons = $("t-options").children.filter((node) => node.tagName === "BUTTON");
  check("U2-R3b 持久同步不清提示区选项行：无懈窗口的 picker 按钮仍在 #t-options",
    promptButtons.length === 1
      && promptButtons[0].textContent.includes("选择座位 1")
      && $("t-options-home").children.includes($("t-options"))
      && !insideDialog($("t-options")),
    `buttons=${JSON.stringify(promptButtons.map((node) => node.textContent))} home=${$("t-options-home").children.includes($("t-options"))} inDialog=${insideDialog($("t-options"))}`);
  render(responsePending);

  // 无懈可提交：点手里的【无懈可击】→ 提交；用 400 注入截住 POST，不改服务端状态。
  const beforeNullify = calls.length;
  staleAnswerStatus = 400;
  ui.toggleFocus(nullify.card_id);
  await ui.submitAction();
  await settleSubmit();
  staleAnswerStatus = null;
  const nullifyAnswers = calls.slice(beforeNullify).filter((call) => call.path.includes("/answer"));
  check("U2-R4 无懈仍可提交：点手牌 + 提交 = 一条原样的 UseCard(无懈可击) POST",
    nullifyAnswers.length === 1
      && nullifyAnswers[0].body.action.kind === "UseCard"
      && nullifyAnswers[0].body.action.card.type === "nullify"
      && JSON.stringify(nullifyAnswers[0].body.action)
        === JSON.stringify(responsePending.legal_actions[0]),
    `answers=${nullifyAnswers.length} action=${JSON.stringify(nullifyAnswers[0] && nullifyAnswers[0].body.action)}`);

  // ---- ② 下一次取牌：同一个链键 / 同一个容器，切回正常取牌模式 ----
  const pickHarvest = harvest(
    "harvest:9:7:2", 2,
    [withSeat(dodge, null), withSeat(peach, 0), withSeat(slash, 1), withSeat(nullify, null)],
    [target(2, "current"), target(0, "taken", peach.card_id), target(1, "taken", slash.card_id)],
  );
  const pickPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 1202, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { answers_seq: 1202, kind: "SelectCards", picks: [0] },
      { answers_seq: 1202, kind: "SelectCards", picks: [1] },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {}, option_labels: null,
    slots: [dodge, nullify].map((card) => ({ ref: { seat: null, zone: "processing" }, card })),
    spec: { zones: ["processing"], total: [1, 1], sequential: false, optional: false },
    harvest: pickHarvest, context: {
      actor: 0, name: "五谷丰登", targets: [2],
      label: "从亮出的牌里选一张获得", requirement: "从亮出的牌里选一张获得", skill_hints: [],
    },
    prompt_display: { text: "请从选择面里选择一张牌。", default_label: "超时将自动选择第一项" },
  };
  render(pickPending);
  check("U2-R5 下一次取牌复用同一个链键：窗口不关，正文从持久展示切回取牌模式",
    persistentKey === "reveal:harvest:9:7:2"
      && ui.state.dialog.key === persistentKey
      && $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-dialog-reveal").hidden === true
      && $("t-modal-backdrop").hidden === false
      && ui.selectionEnabled() === false,
    `key=${ui.state.dialog.key} before=${persistentKey} bodyHidden=${$("t-dialog-body").hidden} optionsInBody=${$("t-dialog-body").children.includes($("t-options"))} revealHidden=${$("t-dialog-reveal").hidden} enabled=${ui.selectionEnabled()}`);

  // ---- ③ harvest 消失（结算结束）⇒ 关窗 ----
  const afterHarvest = {
    kind: "PLAY_PHASE", actor: 0, seq: 1203, prompt: "出牌阶段。",
    legal_actions: [{ answers_seq: 1203, kind: "EndPhase" }],
    candidate_index: {}, virtual_index: {}, option_seats: {}, option_labels: null,
    preactivations: {}, card_preactivations: {}, preactivation: [],
    harvest: null, context: null,
    prompt_display: { text: "出牌阶段。", default_label: "超时将自动结束出牌阶段" },
  };
  render(afterHarvest);
  check("U2-R6 结算结束（harvest 消失）⇒ 亮牌窗关闭、正文清空",
    $("t-dialog").hidden === true
      && $("t-dialog-reveal").hidden === true
      && $("t-dialog-reveal").children.length === 0
      && ui.state.dialog.key === null
      && $("t-options-home").children.includes($("t-options")),
    `dialogHidden=${$("t-dialog").hidden} revealHidden=${$("t-dialog-reveal").hidden} revealChildren=${$("t-dialog-reveal").children.length} key=${ui.state.dialog.key}`);

  // ---- ④ 真生产路径：种子 198（5 人）开局 = 人类持有【无懈】的五谷无懈窗口 ----
  ui.state.sessionId = null;
  ui.state.view = null;
  await newSession(198, { player_count: 5 });
  const realResponse = ui.state.view.pending;
  const realHarvest = realResponse && realResponse.harvest;
  const realKey = ui.state.dialog.key;
  check("U2-R7 真会话（种子 198 / 5 人 / random AI）：开局 pending 就是带 harvest 的无懈 RESPOND",
    !!realResponse && realResponse.kind === "RESPOND" && realResponse.cause === "NULLIFY"
      && !!realHarvest && realHarvest.active === true
      && realHarvest.revealed.length === 5 && realHarvest.current === 3,
    `kind=${realResponse && realResponse.kind} cause=${realResponse && realResponse.cause} harvest=${realHarvest && realHarvest.key} revealed=${realHarvest && realHarvest.revealed.length} current=${realHarvest && realHarvest.current}`);

  check("U2-R8 真 payload 的 DOM 读法：窗口 / 亮牌 / 获得者 / 目标条 / 当前目标全部对上",
    realResponse && realHarvest
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "五谷丰登"
      && cards().length === realHarvest.revealed.length
      && takenBy() === realHarvest.revealed.map((card) => (
        card.taken_by === null || card.taken_by === undefined ? "" : String(card.taken_by)
      )).join(",")
      && chipSeats() === realHarvest.targets.map((item) => String(item.seat)).join(",")
      && chipStates() === realHarvest.targets.map((item) => (
        item.cancelled ? "invalid" : (item.status || "pending")
      )).join(",")
      && bar().getAttribute("data-current") === String(realHarvest.current),
    `title="${$("t-dialog-title").textContent}" cards=${cards().length}/${realHarvest && realHarvest.revealed.length} takenBy=${takenBy()} seats=${chipSeats()} states=${chipStates()} current=${bar() && bar().getAttribute("data-current")}`);

  check("U2-R9 真会话的无懈窗口仍是牌桌表面：#t-options 在提示区、点手牌可提交无懈",
    ui.stepDialogKind(realResponse) === null
      && ui.selectionEnabled() === true
      && !insideDialog($("t-options"))
      && $("t-modal-backdrop").hidden === true,
    `surface=${ui.stepDialogKind(realResponse)} enabled=${ui.selectionEnabled()} optionsInDialog=${insideDialog($("t-options"))}`);

  const beforeRealNullify = calls.length;
  await advanceOnce();
  const realNullifyAnswers = calls.slice(beforeRealNullify).filter((call) => call.path.includes("/answer"));
  check("U2-R10 真会话：界面把【无懈可击】作为一条原样的 UseCard 真的送了出去",
    realNullifyAnswers.length === 1
      && realNullifyAnswers[0].body.action.kind === "UseCard"
      && realNullifyAnswers[0].body.action.card.type === "nullify",
    `answers=${realNullifyAnswers.length} action=${JSON.stringify(realNullifyAnswers[0] && realNullifyAnswers[0].body.action)}`);

  // 拒绝后续每个目标的无懈窗口（选 Pass），直到人类自己的真实取牌询问。
  let declined = 0;
  for (let step = 0; step < 40; step += 1) {
    const pending = ui.state.view ? ui.state.view.pending : null;
    if (!pending) break;
    if (pending.kind === "SELECT_CARDS" && pending.harvest) break;
    if (pending.kind !== "RESPOND" || !pending.harvest) break;
    const pass = (pending.legal_actions || []).findIndex((action) => action.kind === "Pass");
    if (pass < 0) break;
    ui.state.selectedOption = pass;
    await ui.submitAction();
    declined += 1;
  }
  const realPick = ui.state.view ? ui.state.view.pending : null;
  const realPickHarvest = realPick && realPick.harvest;
  check("U2-R11 真会话驱动到真实取牌步：同一个链键 / 同一个窗口，正文切回取牌模式",
    declined > 0
      && !!realPick && realPick.kind === "SELECT_CARDS"
      && !!realPickHarvest && realPickHarvest.key === (realHarvest && realHarvest.key)
      && ui.state.dialog.key === realKey
      && $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-dialog-reveal").hidden === true,
    `declined=${declined} kind=${realPick && realPick.kind} key=${ui.state.dialog.key} before=${realKey} bodyHidden=${$("t-dialog-body").hidden} optionsInBody=${$("t-dialog-body").children.includes($("t-options"))}`);

  check("U2-R12 真实取牌载荷在 DOM 里逐字段可读（taken_by / targets / current 都不是手写夹具）",
    !!realPickHarvest
      && cards().length === realPickHarvest.revealed.length
      && takenBy() === realPickHarvest.revealed.map((card) => (
        card.taken_by === null || card.taken_by === undefined ? "" : String(card.taken_by)
      )).join(",")
      && takenBy().split(",").filter(Boolean).length >= 1
      && chipSeats() === realPickHarvest.targets.map((item) => String(item.seat)).join(",")
      && chipStates() === realPickHarvest.targets.map((item) => (
        item.cancelled ? "invalid" : (item.status || "pending")
      )).join(",")
      && bar().getAttribute("data-current") === String(realPickHarvest.current),
    `cards=${cards().length} takenBy=${takenBy()} seats=${chipSeats()} states=${chipStates()} current=${bar() && bar().getAttribute("data-current")}`);

  // 真取牌一击提交（400 注入截住 POST，断言 Action 原样取自候选）。
  const beforeRealPick = calls.length;
  staleAnswerStatus = 400;
  const firstPickable = cards().find((node) => node.getAttribute("data-taken-by") === "");
  if (firstPickable) await clickAndWait(firstPickable);
  await settleSubmit();
  staleAnswerStatus = null;
  const realPickAnswers = calls.slice(beforeRealPick).filter((call) => call.path.includes("/answer"));
  check("U2-R13 真实取牌：点一张亮牌 = 一条原样取自候选的 SelectCards",
    !!firstPickable
      && realPickAnswers.length === 1
      && realPickAnswers[0].body.action.kind === "SelectCards",
    `got=${Boolean(firstPickable)} answers=${realPickAnswers.length} action=${JSON.stringify(realPickAnswers[0] && realPickAnswers[0].body.action)}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ U2-12（D3 / D9.1）：遗计亮牌弹窗（选 1~2 张 + 右端提交） ============
 *
 * 载荷与真会话同形（字段由 `tests/test_web.py` 的真会话断言钉）：
 *   - `dialog: "reveal"`：服务端按 **SELECT_CARDS + 授予牌面**这个**形状**派生的表面提示
 *     （不认技能 id）；遗计没有五谷的 `harvest` 目标条。
 *   - `revealed` 是两张牌堆顶（私密授予），`slots` 是面内位置，`spec.total = [1, 2]`；
 *     选牌步**没有** `Pass`（D9.1）。
 *
 * 本段只钉界面读法：两张牌只在弹窗里、点牌切换 `.sel`、右端「提交」是唯一提交点；
 * 提交后弹窗关闭（选座步在牌桌）、点座位 + 提交后交出；还有余牌时下一轮自动开窗；
 * 两张交完关窗；没有「放弃」；折叠 / ESC / 只读 / 检视；别人的载荷永远看不到这两张牌。
 */
{
  const cardOf = (id, name, type, description) => ({
    ...synthCard(id, name),
    type,
    display_name: name,
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description,
  });
  const topA = cardOf(31, "杀", "slash", "杀的描述：造成 1 点伤害。");
  const topB = cardOf(32, "闪", "dodge", "闪的描述：抵消一张杀。");
  const yijiContext = (actor) => ({
    actor, name: "遗计", targets: [],
    label: `${actor}号位郭嘉使用遗计`,
    requirement: "从亮出的牌里选择一至两张",
    skill_hints: [],
  });
  /** 与真会话同形的选牌步（`remaining` = 还没交出的亮牌）。 */
  const yijiPick = ({ seq, actor = 0, remaining }) => {
    const combos = remaining.length > 1 ? [[0], [1], [0, 1]] : [[0]];
    return {
      kind: "SELECT_CARDS", actor, seq,
      prompt: "请从选择面里选择一至两张牌。",
      legal_actions: combos.map((picks) => ({ kind: "SelectCards", answers_seq: seq, picks })),
      candidate_index: {}, virtual_index: {}, option_seats: {},
      slots: remaining.map((card) => ({ ref: { seat: actor, zone: "hand" }, card })),
      spec: { zones: ["hand"], total: [1, 2], sequential: false, optional: false },
      revealed: remaining,
      dialog: "reveal",
      harvest: null,
      context: yijiContext(actor),
      prompt_display: {
        text: "请从选择面里选择一至两张牌。",
        default_action: { kind: "SelectCards", answers_seq: seq, picks: [0] },
        default_label: "超时将自动选择第一张亮牌",
      },
      preactivations: {}, preactivation: [],
    };
  };
  /** 「交给谁」步：牌桌表面（`option_seats` 非空 ⇒ 不弹窗），没有 Pass。 */
  const yijiGive = ({ seq, actor = 0, seats }) => ({
    kind: "SELECT_OPTION", actor, seq,
    prompt: "请选择一项。",
    legal_actions: seats.map((seat) => ({
      kind: "SelectOption", answers_seq: seq, choice: `seat:${seat}`,
    })),
    candidate_index: {}, virtual_index: {},
    option_seats: Object.fromEntries(seats.map((seat) => [`seat:${seat}`, seat])),
    context: {
      actor, name: "遗计", targets: [],
      label: `${actor}号位郭嘉使用遗计`,
      requirement: "选择将这些牌交给哪名角色",
      skill_hints: [],
    },
    prompt_display: {
      text: "请选择一项。",
      default_action: { kind: "SelectOption", answers_seq: seq, choice: `seat:${actor}` },
      default_label: "超时将把剩下的牌交给自己",
    },
    preactivations: {}, preactivation: [],
  });
  const yijiView = (pending, seat = 0, timeout = null) => {
    const view = synthView(pending);
    view.human_seat = seat;
    view.snapshot.seat = seat;
    if (timeout !== null) view.timeout_seconds = timeout;
    return view;
  };
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const insideDialog = (node) => {
    let at = node ? node.parentNode : null;
    while (at) {
      if (at === $("t-dialog")) return true;
      at = at.parentNode;
    }
    return false;
  };
  const revealWrap = () => $("t-options").children[0];
  const revealCards = () => (revealWrap() ? revealWrap().children[0].children : []);
  const clickCard = (index) => {
    const node = revealCards()[index];
    (node.listeners.click || []).forEach((fn) => fn({ target: node }));
  };
  const clickCollapse = () => ($("t-dialog-collapse").listeners.click || [])
    .forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  const clickSubmit = async () => {
    for (const fn of $("btn-submit").listeners.click || []) {
      await fn({ target: $("btn-submit"), type: "click" });
    }
  };
  const settleSubmit = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };

  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261015,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  ui.state.modal.heroSeat = null;

  // ---- ① 选牌步：两张亮牌只在弹窗里；右端「提交」是唯一提交点 ----
  const pick = yijiPick({ seq: 1301, actor: 0, remaining: [topA, topB] });
  render(yijiView(pick, 0, 45));

  check("U2-12a 遗计选牌步 = 亮牌弹窗：标题「遗计」、选 1~2 张口径、两张牌在窗内、无放弃",
    ui.stepDialogKind(pick) === "reveal"
      && $("t-dialog").hidden === false
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-dialog-title").textContent === "遗计"
      && $("t-prompt").textContent === "从亮出的牌里选择一至两张"
      && $("t-dialog-hint").textContent.includes("选 1~2 张后点右端「提交」")
      && $("t-dialog-hint").textContent.includes("已选 0 张")
      && revealCards().length === 2
      && revealCards().map((node) => node.getAttribute("data-card-id")).join(",") === "31,32"
      && !revealWrap().children.some((node) => node.classList.contains("reveal-targets"))
      && $("btn-pass").hidden === true
      && !/放弃/.test($("t-dialog").text())
      && ["btn-submit", "btn-cancel", "btn-pass", "btn-end-phase", "t-countdown"]
        .every((id) => !insideDialog($(id)))
      && candidateRowButtons().length === 0
      && $("btn-submit").disabled === true
      && $("btn-submit").textContent === "提交"
      && $("t-choice").textContent.includes("选 1~2 张后点右端「提交」"),
    `kind=${ui.stepDialogKind(pick)} title="${$("t-dialog-title").textContent}" hint="${$("t-dialog-hint").textContent}" cards=${revealCards().length} passHidden=${$("btn-pass").hidden} submit="${$("btn-submit").textContent}" disabled=${$("btn-submit").disabled}`);

  check("U2-12a2 选牌步的超时文案读服务端：第一张亮牌（超时行为在服务端）",
    $("t-default-label").textContent === "超时将自动选择第一张亮牌"
      && /剩余/.test($("t-timer").textContent),
    `label="${$("t-default-label").textContent}" timer="${$("t-timer").textContent}"`);

  // ---- ② 点牌 = 切换选中（不提交）；右端提交键是唯一提交点 ----
  const beforeToggle = calls.length;
  clickCard(0);
  check("U2-12b 点第一张 = 选中（.sel）且**不提交**；第二张仍可继续选",
    JSON.stringify(ui.state.picks) === "[0]"
      && revealCards()[0].classList.contains("sel")
      && !revealCards()[1].classList.contains("sel")
      && $("btn-submit").disabled === false
      && $("t-dialog-hint").textContent.includes("已选 1 张")
      && calls.length === beforeToggle,
    `picks=${JSON.stringify(ui.state.picks)} sel=${revealCards().map((n) => n.classList.contains("sel")).join(",")} submitDisabled=${$("btn-submit").disabled} calls=${calls.length - beforeToggle}`);

  clickCard(1);
  check("U2-12c 选第二张 ⇒ picks 保持面内升序 [0,1]，可以提交",
    JSON.stringify(ui.state.picks) === "[0,1]"
      && revealCards().every((node) => node.classList.contains("sel"))
      && ui.buildAction() !== null
      && ui.buildAction().kind === "SelectCards"
      && JSON.stringify(ui.buildAction().picks) === "[0,1]",
    `picks=${JSON.stringify(ui.state.picks)} action=${JSON.stringify(ui.buildAction())}`);

  clickCard(0);
  check("U2-12d 再点已选中的牌 = 取消它（剩 [1]，仍可提交）",
    JSON.stringify(ui.state.picks) === "[1]"
      && !revealCards()[0].classList.contains("sel")
      && $("btn-submit").disabled === false,
    `picks=${JSON.stringify(ui.state.picks)} sel=${revealCards().map((n) => n.classList.contains("sel")).join(",")}`);
  clickCard(0);
  check("U2-12d2 取消后重新选中 ⇒ 提交的仍是升序 [0,1]（不是点击顺序）",
    JSON.stringify(ui.state.picks) === "[0,1]"
      && JSON.stringify(ui.buildAction().picks) === "[0,1]",
    `picks=${JSON.stringify(ui.state.picks)} action=${JSON.stringify(ui.buildAction())}`);

  // ---- ③ 提交：一条 SelectCards POST（picks = 面内位置） ----
  const beforeSubmit = calls.length;
  staleAnswerStatus = 400;
  await clickSubmit();
  await settleSubmit();
  staleAnswerStatus = null;
  const twoAnswers = calls.slice(beforeSubmit).filter((call) => call.path.includes("/answer"));
  const twoAction = pick.legal_actions.find((action) => action.picks.length === 2);
  check("U2-12e 点右端「提交」= 恰一条 SelectCards POST（两张一起交），没有第二个提交点",
    twoAnswers.length === 1
      && JSON.stringify(twoAnswers[0].body.action) === JSON.stringify(twoAction)
      && JSON.stringify(twoAnswers[0].body.action.picks) === "[0,1]",
    `answers=${twoAnswers.length} action=${JSON.stringify(twoAnswers[0] && twoAnswers[0].body.action)}`);

  // ---- ④ 提交后弹窗关闭：选座步在牌桌（点座位 + 提交），超时交给自己 ----
  const give = yijiGive({ seq: 1302, actor: 0, seats: [0, 1, 2, 3] });
  render(yijiView(give, 0, 45));
  check("U2-12f 提交后弹窗关闭：『交给谁』回到牌桌表面，右端没有放弃",
    $("t-dialog").hidden === true
      && !$("t-dialog-body").children.includes($("t-options"))
      && ui.stepDialogKind(give) === null
      && $("t-prompt").textContent === "选择将这些牌交给哪名角色"
      && $("t-target-hint").textContent === "点场上武将，选择把这张牌交给谁。"
      && $("btn-pass").hidden === true
      && $("btn-submit").disabled === true
      && ui.selectionEnabled() === true,
    `dialogHidden=${$("t-dialog").hidden} kind=${ui.stepDialogKind(give)} prompt="${$("t-prompt").textContent}" passHidden=${$("btn-pass").hidden} enabled=${ui.selectionEnabled()}`);

  check("U2-12f2 选座步的超时文案：剩下的牌交给自己（服务端 default_label）",
    $("t-default-label").textContent === "超时将把剩下的牌交给自己"
      && /剩余/.test($("t-timer").textContent),
    `label="${$("t-default-label").textContent}" timer="${$("t-timer").textContent}"`);

  const seatNode = seatBox(2);
  check("U2-12g 可交给的座位可点（targetable）",
    !!seatNode && seatNode.classList.contains("targetable"),
    `seat=${!!seatNode} classes=${seatNode && seatNode.className}`);
  (seatNode.listeners.click || []).forEach((fn) => fn({ target: seatNode }));
  check("U2-12h 点座位 = 选中 seat:2，提交键可点",
    ui.state.selectedOption !== null
      && (give.legal_actions[ui.state.selectedOption] || {}).choice === "seat:2"
      && $("btn-submit").disabled === false,
    `selected=${ui.state.selectedOption} submitDisabled=${$("btn-submit").disabled}`);

  const beforeGive = calls.length;
  staleAnswerStatus = 400;
  await clickSubmit();
  await settleSubmit();
  staleAnswerStatus = null;
  const giveAnswers = calls.slice(beforeGive).filter((call) => call.path.includes("/answer"));
  check("U2-12h2 点「提交」= 一条 SelectOption POST（seat:2），牌进入其手牌由服务端负责",
    giveAnswers.length === 1
      && JSON.stringify(giveAnswers[0].body.action) === JSON.stringify(
        give.legal_actions.find((action) => action.choice === "seat:2"),
      ),
    `answers=${giveAnswers.length} action=${JSON.stringify(giveAnswers[0] && giveAnswers[0].body.action)}`);

  // ---- ⑤ 还有余牌 ⇒ 下一轮自动重新开窗（只剩那张），两张交完关窗 ----
  const nextPick = yijiPick({ seq: 1303, actor: 0, remaining: [topB] });
  render(yijiView(nextPick, 0));
  check("U2-12i 还有余牌 ⇒ 下一轮自动重新开窗（新 seq = 新键），只剩没交出的那张",
    $("t-dialog").hidden === false
      && ui.state.dialog.key === "reveal:1303"
      && revealCards().length === 1
      && revealCards()[0].getAttribute("data-card-id") === "32"
      && $("btn-submit").disabled === true,
    `hidden=${$("t-dialog").hidden} key=${ui.state.dialog.key} cards=${revealCards().length}`);

  clickCard(0);
  const beforeLast = calls.length;
  staleAnswerStatus = 400;
  await clickSubmit();
  await settleSubmit();
  staleAnswerStatus = null;
  const lastAnswers = calls.slice(beforeLast).filter((call) => call.path.includes("/answer"));
  check("U2-12j 最后一张同样只走右端「提交」（picks=[0]）",
    lastAnswers.length === 1
      && JSON.stringify(lastAnswers[0].body.action) === JSON.stringify(nextPick.legal_actions[0]),
    `answers=${lastAnswers.length} action=${JSON.stringify(lastAnswers[0] && lastAnswers[0].body.action)}`);

  render(yijiView(null, 0));
  check("U2-12k 两张都交完、询问结束 ⇒ 弹窗关闭、正文清空，不留上一问的牌",
    $("t-dialog").hidden === true
      && $("t-dialog-body").children.length === 0
      && $("t-dialog-title").textContent === "",
    `hidden=${$("t-dialog").hidden} bodyChildren=${$("t-dialog-body").children.length} title="${$("t-dialog-title").textContent}"`);

  // ---- ⑥ 折叠 / ESC / 只读 / 检视；没有关闭按钮 ----
  const own2 = yijiPick({ seq: 1304, actor: 0, remaining: [topA, topB] });
  render(yijiView(own2, 0));
  check("U2-12l 弹窗打开时牌桌只读，但牌面描述（检视）仍可读",
    ui.selectionEnabled() === false
      && revealCards()[0].getAttribute("title").includes("杀的描述")
      && revealCards()[1].getAttribute("title").includes("闪的描述"),
    `enabled=${ui.selectionEnabled()} titles=${revealCards().map((n) => n.getAttribute("title")).join("|")}`);

  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U2-12m ESC 不关亮牌弹窗（没有关闭出口）；点背景也没有 click 监听",
    $("t-dialog").hidden === false
      && !($("t-modal-backdrop").listeners.click || []).length,
    `hidden=${$("t-dialog").hidden} backdropClicks=${($("t-modal-backdrop").listeners.click || []).length}`);

  const handNode = $("t-hand").children[0];
  if (handNode) (handNode.listeners.click || []).forEach((fn) => fn({ target: handNode }));
  check("U2-12n 只读态：点自己的手牌不产生选择（选择只在弹窗里）",
    ui.state.focusCardId === null && !(handNode && handNode.classList.contains("sel")),
    `focus=${ui.state.focusCardId}`);

  clickCollapse();
  check("U2-12o 折叠只撤正文：标题「遗计」与选牌口径仍在标题条、暗化层收起",
    $("t-dialog-body").hidden === true
      && $("t-dialog-title").textContent === "遗计"
      && $("t-dialog-hint").textContent.includes("选 1~2 张后点右端「提交」")
      && $("t-dialog-collapse").textContent === "展开"
      && $("t-modal-backdrop").hidden === true,
    `bodyHidden=${$("t-dialog-body").hidden} title="${$("t-dialog-title").textContent}" hint="${$("t-dialog-hint").textContent}"`);
  clickCollapse();
  check("U2-12p 展开恢复正文与暗化层，只读判据不变",
    $("t-dialog-body").hidden === false
      && $("t-modal-backdrop").hidden === false
      && ui.selectionEnabled() === false);

  // ---- ⑦ 别人的载荷永远看不到这两张牌（私密观看） ----
  const maskedPick = {
    ...yijiPick({ seq: 1305, actor: 1, remaining: [topA, topB] }),
    dialog: null,
    revealed: [],
    slots: [topA, topB].map(() => ({ ref: { seat: 1, zone: "hand" }, card: null })),
  };
  render(yijiView(maskedPick, 0));
  check("U2-12q 别人的载荷（revealed / slots 已抹）不开窗、不画牌面：两张牌只归持有者",
    $("t-dialog").hidden === true
      && ui.stepDialogKind(maskedPick) === null
      && $("t-options").children.length === 0
      && $("t-prompt").textContent.includes("等待其他座位响应"),
    `hidden=${$("t-dialog").hidden} kind=${ui.stepDialogKind(maskedPick)} options=${$("t-options").children.length} prompt="${$("t-prompt").textContent}"`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ U2-13（D2 / D3 / D9.2）：寒冰剑 = 未知区域弹窗 + 两轮保持 ============
 *
 * 载荷与真会话同形（服务端的 `context` / `slots` 字段由 `tests/test_web.py` 的投影断言钉，
 * 两轮形状由 golden 38 的回放实测确认）：
 *   - **一次 `SELECT_CARDS` = 一轮**：`spec.zones = [hand, equip]`、`total = [1, 1]`、
 *     `optional: false`（轮内没有 Pass）；`slots` 是目标**此刻**的牌——第一轮搬走的那张，
 *     第二轮的面里已经不在（引擎逐轮立即搬牌，不是先收两张再一起弃）。
 *   - `context.name` 是**投影**给的帧内牌名（这里 = 造成伤害的【杀】），两轮相同；
 *     `context.actor` = 弃牌的应答者（= 伤害来源）。链键
 *     `chain:${context.name}:${actor}` 因此跨两轮不变 ⇒ 同一容器只换正文、不关窗。
 *     注意：不是"寒冰剑"这个名字——投影读的是结算帧的牌面（实测：`3号位孙权对0号位
 *     陆逊使用杀`，name =「杀」），但它在两轮之间**稳定**，这才是链键要的性质。
 *   - `context.requirement` 是本票改后的逐步文案（有牌必须弃满两张，没有"至多"）。
 *
 * 本段只钉界面读法：两轮共用弹窗、第一张从第二轮消失、第二次提交后关窗；目标只有一张
 * 时一轮即关；目标没牌（引擎根本不发问）没有弹窗；全程没有「放弃」/「收手」出口、
 * 右端提交恒灰；折叠 / ESC / 只读 / 检视照旧。
 */
{
  const armor = {
    ...synthCard(90, "八卦阵"), type: "armor", slot: "armor", color: "black",
    description: "防具描述：需要时打出一张【闪】。",
  };
  const hidden = () => ({ ref: { seat: 0, zone: "hand" }, card: null });
  const visibleArmor = () => ({ ref: { seat: 0, zone: "equip" }, card: armor });
  /** 一轮寒冰剑弃牌：与真会话同形（**没有** Pass；槽位全在目标的区里，手牌是隐藏槽）。 */
  const iceRound = ({ seq, slots }) => ({
    kind: "SELECT_CARDS", actor: 1, seq, prompt: "请从选择面里选择一张牌。",
    legal_actions: slots.map((_, index) => ({
      kind: "SelectCards", answers_seq: seq, picks: [index],
    })),
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots,
    spec: { zones: ["hand", "equip"], total: [1, 1], sequential: false, optional: false },
    context: {
      actor: 1, name: "杀", targets: [0],
      label: "1号位关羽对0号位张飞使用杀",
      requirement: "依次弃置目标的两张牌（手牌 / 装备区）", skill_hints: [],
    },
    prompt_display: { text: "请从选择面里选择一张牌。", default_label: "超时将自动选择第一项" },
    preactivations: {}, preactivation: [],
  });
  const iceView = (pending) => {
    const view = synthView(pending);
    // 人类 = 弃牌的应答者（座位 1，伤害来源），目标 = 座位 0（他的手牌是隐藏槽）。
    view.snapshot.seat = 1;
    view.human_seat = 1;
    return view;
  };
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const zoneSections = () => $("t-options").children;
  const zoneSlots = (section) => section.children[1].children;
  const insideDialog = (node) => {
    let at = node ? node.parentNode : null;
    while (at) {
      if (at === $("t-dialog")) return true;
      at = at.parentNode;
    }
    return false;
  };
  const clickCollapse = () => ($("t-dialog-collapse").listeners.click || [])
    .forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  const settleSubmit = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };
  const heroBlockOf = (seat) => {
    const box = seatBox(seat);
    return box
      ? box.children.find((node) => (node.className || "").split(" ").includes("hero-block")) || null
      : null;
  };
  const fireContext = (node) => {
    const event = { target: node, prevented: false, stopPropagation() {} };
    event.preventDefault = () => { event.prevented = true; };
    if (node) (node.listeners.contextmenu || []).forEach((fn) => fn(event));
    return event;
  };
  const answersSince = (from) => calls.slice(from).filter((call) => call.path.includes("/answer"));

  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 1, timeout_seconds: null, seed: 20261016,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  ui.state.modal.heroSeat = null;

  // ---- ① 第一轮：目标手牌「?」+ 装备可见；没有放弃 / 收手；右端提交恒灰 ----
  const round1 = iceRound({
    seq: 1401, slots: [hidden(), hidden(), visibleArmor()],
  });
  render(iceView(round1));
  const firstDialog = $("t-dialog");
  const firstKey = ui.state.dialog.key;
  const handSection = zoneSections()[0];
  const equipSection = zoneSections()[1];
  check("U2-13a 寒冰剑第一轮 = 未知区域弹窗：目标手牌「?」、装备可见、文案是「两张」而不是「至多两张」",
    ui.stepDialogKind(round1) === "unknown"
      && $("t-dialog").hidden === false
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-dialog-title").textContent === "选择一张牌"
      && firstKey === "chain:杀:1"
      && firstKey === `chain:${round1.context.name}:${round1.actor}`
      && zoneSections().length === 2
      && handSection.getAttribute("data-zone") === "hand"
      && handSection.getAttribute("data-seat") === "0"
      && handSection.children[0].textContent.includes("座位0 手牌")
      && handSection.children[0].textContent.includes("牌面未知")
      && zoneSlots(handSection).length === 2
      && zoneSlots(handSection).every((node) => node.textContent === "?"
        && node.classList.contains("unknown"))
      && equipSection.getAttribute("data-zone") === "equip"
      && equipSection.getAttribute("data-seat") === "0"
      && zoneSlots(equipSection).length === 1
      && zoneSlots(equipSection)[0].text().includes("八卦阵")
      && zoneSlots(equipSection)[0].getAttribute("data-slot-index") === "2"
      && $("t-prompt").textContent === "依次弃置目标的两张牌（手牌 / 装备区）"
      && $("t-dialog-hint").textContent.includes("依次弃置目标的两张牌（手牌 / 装备区）")
      && $("t-dialog-hint").textContent.includes("点一张牌即提交")
      && !/至多/.test($("t-dialog-hint").textContent)
      && !/至多/.test($("t-prompt").textContent)
      && candidateRowButtons().length === 0,
    `kind=${ui.stepDialogKind(round1)} key=${firstKey} 块=${zoneSections().map((n) => `${n.getAttribute("data-zone")}@${n.getAttribute("data-seat")}`).join(",")} 手槽=${zoneSlots(handSection).length} 装备=${zoneSlots(equipSection).map((n) => n.text()).join(",")} hint="${$("t-dialog-hint").textContent}"`);

  check("U2-13a2 轮内没有「放弃 / 收手」出口：右端放弃隐藏、提交 / 取消都灰、弹窗里没有操作四键",
    $("btn-pass").hidden === true
      && $("btn-pass").disabled === true
      && $("btn-submit").disabled === true
      && $("btn-cancel").disabled === true
      && !/放弃|收手/.test($("t-dialog").text())
      && /不能放弃/.test($("t-choice").textContent)
      && ["btn-submit", "btn-cancel", "btn-pass", "btn-end-phase", "t-countdown"]
        .every((id) => !insideDialog($(id))),
    `passHidden=${$("btn-pass").hidden} submit=${$("btn-submit").disabled} cancel=${$("btn-cancel").disabled} choice="${$("t-choice").textContent}"`);

  // ---- ② 第一张：点一个「?」= 一条 SelectCards（picks = 槽位位置） ----
  {
    const beforePick = calls.length;
    staleAnswerStatus = 400;
    await clickAndWait(zoneSlots(handSection)[1]);
    await settleSubmit();
    staleAnswerStatus = null;
    const answers = answersSince(beforePick);
    check("U2-13b 第一轮点第 2 个「?」= 恰一条 SelectCards，picks 填该槽位位置",
      answers.length === 1
        && JSON.stringify(answers[0].body.action)
          === JSON.stringify(round1.legal_actions[1])
        && answers[0].body.action.kind === "SelectCards"
        && JSON.stringify(answers[0].body.action.picks) === "[1]",
      `answers=${answers.length} action=${JSON.stringify(answers[0] && answers[0].body.action)}`);
  }

  // ---- ③ 第二轮：同一链键 / 同一容器、第一张已从正文消失；第二张点完关窗 ----
  const round2 = iceRound({ seq: 1402, slots: [hidden(), visibleArmor()] });
  render(iceView(round2));
  check("U2-13c 第二轮同一链键：弹窗不关、同一容器，第一张（少了一张手牌）已从正文消失",
    ui.state.dialog.key === firstKey
      && firstKey === "chain:杀:1"
      && $("t-dialog") === firstDialog
      && $("t-dialog").hidden === false
      && $("t-dialog-body").children.includes($("t-options"))
      && $("t-dialog-body").hidden === false
      && zoneSections().length === 2
      && zoneSlots(zoneSections()[0]).length === 1
      && zoneSlots(zoneSections()[0])[0].textContent === "?"
      && zoneSlots(zoneSections()[1]).length === 1
      && zoneSlots(zoneSections()[1])[0].getAttribute("data-slot-index") === "1"
      && zoneSlots(zoneSections()[1])[0].text().includes("八卦阵")
      && $("t-dialog-title").textContent === "选择一张牌"
      && $("btn-pass").hidden === true
      && $("btn-submit").disabled === true,
    `key=${ui.state.dialog.key} 前=${firstKey} hidden=${$("t-dialog").hidden} 手槽=${zoneSlots(zoneSections()[0]).length} 装备槽=${zoneSlots(zoneSections()[1]).length} 装备索引=${zoneSlots(zoneSections()[1])[0].getAttribute("data-slot-index")}`);

  {
    const beforePick = calls.length;
    staleAnswerStatus = 400;
    await clickAndWait(zoneSlots(zoneSections()[1])[0]);
    await settleSubmit();
    staleAnswerStatus = null;
    const answers = answersSince(beforePick);
    check("U2-13d 第二轮点剩下那张装备 = 恰一条 SelectCards（picks 用第二轮的面内位置）",
      answers.length === 1
        && JSON.stringify(answers[0].body.action)
          === JSON.stringify(round2.legal_actions[1])
        && JSON.stringify(answers[0].body.action.picks) === "[1]",
      `answers=${answers.length} action=${JSON.stringify(answers[0] && answers[0].body.action)}`);
  }

  render(iceView(null));
  check("U2-13e 第二张弃完、询问链结束 ⇒ 弹窗关闭、正文清空、牌桌恢复可选",
    $("t-dialog").hidden === true
      && ui.state.dialog.key === null
      && $("t-dialog-body").children.length === 0
      && $("t-dialog-title").textContent === ""
      && ui.stepDialogKind(null) === null
      && ui.selectionEnabled() === true,
    `hidden=${$("t-dialog").hidden} key=${ui.state.dialog.key} body=${$("t-dialog-body").children.length} title="${$("t-dialog-title").textContent}" enabled=${ui.selectionEnabled()}`);

  // ---- ④ 目标只有一张牌：自然只有一轮，点掉即关 ----
  const single = iceRound({ seq: 1403, slots: [hidden()] });
  render(iceView(single));
  check("U2-13f 目标只有一张牌 ⇒ 只有一轮：弹窗打开、一个「?」、没有放弃",
    ui.stepDialogKind(single) === "unknown"
      && $("t-dialog").hidden === false
      && zoneSections().length === 1
      && zoneSections()[0].getAttribute("data-zone") === "hand"
      && zoneSlots(zoneSections()[0]).length === 1
      && $("btn-pass").hidden === true
      && $("btn-submit").disabled === true,
    `kind=${ui.stepDialogKind(single)} 块=${zoneSections().length} 槽=${zoneSections()[0] ? zoneSlots(zoneSections()[0]).length : "n/a"}`);
  {
    const beforePick = calls.length;
    staleAnswerStatus = 400;
    await clickAndWait(zoneSlots(zoneSections()[0])[0]);
    await settleSubmit();
    staleAnswerStatus = null;
    const answers = answersSince(beforePick);
    check("U2-13g 只有一张时点掉即提交（picks=[0]），之后链结束、弹窗关闭",
      answers.length === 1
        && JSON.stringify(answers[0].body.action)
          === JSON.stringify(single.legal_actions[0])
        && JSON.stringify(answers[0].body.action.picks) === "[0]"
        && (() => {
          render(iceView(null));
          return $("t-dialog").hidden === true && ui.state.dialog.key === null;
        })(),
      `answers=${answers.length} action=${JSON.stringify(answers[0] && answers[0].body.action)} hidden=${$("t-dialog").hidden} key=${ui.state.dialog.key}`);
  }

  // ---- ⑤ 目标没有牌：引擎根本不发选牌询问（test_skills 已钉），界面没有弹窗 / 正文 ----
  render(iceView(null));
  check("U2-13h 目标没有牌（无选牌询问）⇒ 没有未知区域弹窗，也没有任何「?」正文",
    ui.stepDialogKind(null) === null
      && $("t-dialog").hidden === true
      && $("t-options").children.length === 0
      && $("t-dialog-body").children.length === 0
      && $("btn-pass").hidden === true,
    `kind=${ui.stepDialogKind(null)} hidden=${$("t-dialog").hidden} options=${$("t-options").children.length} body=${$("t-dialog-body").children.length}`);

  // ---- ⑥ 折叠 / 无关闭 / 只读 / 检视（未知区域模板的既有性质，寒冰剑沿用） ----
  render(iceView(round1));
  clickCollapse();
  check("U2-13i 寒冰剑弹窗可折叠：正文撤下、标题与「两张」提示留下、暗化层收起",
    $("t-dialog-body").hidden === true
      && $("t-dialog-title").textContent === "选择一张牌"
      && $("t-dialog-hint").textContent.includes("依次弃置目标的两张牌（手牌 / 装备区）")
      && $("t-dialog-collapse").textContent === "展开"
      && $("t-modal-backdrop").hidden === true,
    `bodyHidden=${$("t-dialog-body").hidden} title="${$("t-dialog-title").textContent}" hint="${$("t-dialog-hint").textContent}"`);
  clickCollapse();
  const beforeEsc = calls.length;
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U2-13j 无关闭出口：展开恢复正文；ESC 是 no-op、点背景没有 click 监听、零提交",
    $("t-dialog-body").hidden === false
      && $("t-modal-backdrop").hidden === false
      && $("t-dialog").hidden === false
      && calls.length === beforeEsc
      && !($("t-modal-backdrop").listeners.click || []).length
      && ui.selectionEnabled() === false,
    `bodyHidden=${$("t-dialog-body").hidden} hidden=${$("t-dialog").hidden} calls=${calls.length - beforeEsc} backdropClicks=${($("t-modal-backdrop").listeners.click || []).length}`);

  const ownHand = $("t-hand").children[0];
  if (ownHand) (ownHand.listeners.click || []).forEach((fn) => fn({ target: ownHand }));
  fireContext(heroBlockOf(0));
  check("U2-13k 只读态：点自己的手牌不产生选择；右键武将详情照常叠在弹窗上、只读判断不变",
    ui.state.focusCardId === null
      && !(ownHand && ownHand.classList.contains("sel"))
      && $("t-modal").hidden === false
      && /武将详情/.test($("t-modal-title").textContent)
      && $("t-dialog").hidden === false
      && ui.selectionEnabled() === false,
    `focus=${ui.state.focusCardId} detailHidden=${$("t-modal").hidden} dialogHidden=${$("t-dialog").hidden} enabled=${ui.selectionEnabled()}`);
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U2-13k2 ESC 只关武将详情；底下的寒冰剑弹窗原样恢复",
    $("t-modal").hidden === true
      && $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && ui.selectionEnabled() === false,
    `detailHidden=${$("t-modal").hidden} dialogHidden=${$("t-dialog").hidden} enabled=${ui.selectionEnabled()}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}


/* ============ M5-08a（D10 / §5.1.4）：火攻界面 ============
 *
 * 载荷与真会话同形（字段由 `tests/test_web.py` 的真会话断言钉）：
 *   - 目标过滤 = 候选索引的池：只有有手牌的角色（含自己）；空手牌座位不在池里 ⇒ 不可点；
 *   - 人类被指定：`dialog: "reveal"` + `revealed`（本人的手牌）+ `total: [1, 1]` ⇒ 亮牌模板，
 *     点一张即提交；牌桌只读，但悬停描述照常；
 *   - 使用者弃同花色：`SELECT_CARDS` + `pending.reveal`（已公开的亮牌）+ `optional: true`
 *     ⇒ 点自己手里那张同花色牌即提交；右端「放弃」是另一个出口；
 *   - 无同花色：引擎不发询问（自动放弃），事件流由服务端叙事写「放弃（无可弃的同花色牌）」；
 *   - 公开亮牌进中央舞台的**同一处理区行**，结算帧出栈即撤下；手牌区不留「已知」。
 */
{
  const fire = {
    ...synthCard(109, "火攻"),
    type: "fire_attack",
    display_name: "火攻",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "火攻的描述：亮一张手牌，弃同花色造成 1 点火焰伤害。",
  };
  const revealed = {
    ...synthCard(5, "闪电"),
    type: "lightning",
    display_name: "闪电",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "闪电的描述",
  };
  const sameSuitA = {
    ...synthCard(5, "闪电"),
    type: "lightning",
    display_name: "闪电",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "闪电的描述",
  };
  const sameSuitB = {
    ...synthCard(7, "杀"),
    type: "slash",
    display_name: "杀",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "杀的描述",
  };
  const heartCard = {
    ...synthCard(8, "桃"),
    type: "peach",
    display_name: "桃",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "桃的描述",
    suit: "heart",
    color: "red",
  };
  const clubCard = {
    ...synthCard(9, "闪"),
    type: "dodge",
    display_name: "闪",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "闪的描述",
    suit: "club",
    color: "black",
  };
  const fireContext = (requirement) => ({
    actor: 0, name: "火攻", targets: [0],
    label: "0号位对0号位使用火攻", requirement, skill_hints: [],
  });
  const fireView = (pending, { hand = [], processing = [fire], seats = 4 } = {}) => {
    const view = synthView(pending);
    view.human_seat = 0;
    view.snapshot.seat = 0;
    view.snapshot.phase = "play";
    view.snapshot.turn_seat = 0;
    view.snapshot.self_hand = hand;
    view.snapshot.processing = processing;
    view.snapshot.players = Array.from({ length: seats }, (_, seat) => ({
      seat, hp: 4, hp_max: 4,
      hand_count: seat === 2 ? 0 : 2,
      alive: true, equip: [], judge: [], identity: null,
    }));
    return view;
  };
  const renderFire = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const handNode = (cardId) => $("t-hand").children.find(
    (node) => node.getAttribute("data-card-id") === String(cardId),
  ) || null;
  const clickHand = async (cardId) => {
    const node = handNode(cardId);
    if (!node) return false;
    for (const fn of node.listeners.click || []) await fn({ target: node, stopPropagation() {} });
    return true;
  };
  const settle = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };
  const answersSince = (mark) => calls.slice(mark).filter((call) => call.path.includes("/answer"));

  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261016,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  ui.state.modal.heroSeat = null;

  // ---- ① 目标过滤：只有有手牌的角色可点（含自己；座位 2 空手牌不可点） ----
  const play = {
    kind: "PLAY_PHASE", actor: 0, seq: 2101,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      { kind: "UseCard", answers_seq: 2101, card: fire, targets: [0] },
      { kind: "UseCard", answers_seq: 2101, card: fire, targets: [1] },
      { kind: "UseCard", answers_seq: 2101, card: fire, targets: [3] },
      { kind: "EndPhase", answers_seq: 2101 },
    ],
    candidate_index: {
      109: {
        card: fire, pool: [0, 1, 3], combos: [[0], [1], [3]], min_targets: 1, max_targets: 1,
      },
    },
    virtual_index: {}, option_seats: {}, context: fireContext("使用一张牌（或发动技能），或结束出牌阶段"),
    prompt_display: {
      text: "出牌阶段：使用一张牌，或结束出牌阶段。",
      default_action: { kind: "EndPhase", answers_seq: 2101 },
      default_label: "超时将自动结束出牌阶段",
    },
    preactivations: {}, preactivation: [],
  };
  renderFire(fireView(play, { hand: [fire] }));
  await clickHand(109);
  check("M5-08a-a1 选中火攻后：有手牌的座位（含自己）可点，空手牌座位 2 不可点",
    ui.state.focusCardId === 109
      && seatBox(0).classList.contains("targetable")
      && seatBox(1).classList.contains("targetable")
      && seatBox(3).classList.contains("targetable")
      && !seatBox(2).classList.contains("targetable")
      && ui.pickerSeats(play).map((item) => item.seat).join(",") === "0,1,3",
    `focus=${ui.state.focusCardId} seats=${ui.pickerSeats(play).map((item) => item.seat).join(",")} s2=${seatBox(2).className}`);
  (seatBox(2).listeners.click || []).forEach((fn) => fn({ target: seatBox(2) }));
  check("M5-08a-a2 点空手牌座位 = 没有目标（不会提交空目标）",
    ui.state.selectedTargets.length === 0 && ui.buildAction() === null,
    `targets=${JSON.stringify(ui.state.selectedTargets)}`);
  (seatBox(0).listeners.click || []).forEach((fn) => fn({ target: seatBox(0) }));
  check("M5-08a-a3 可指定自己：点座位 0 = 选中自己，提交候选原样来自 legal_actions",
    JSON.stringify(ui.state.selectedTargets) === "[0]"
      && ui.buildAction() !== null
      && ui.buildAction().targets.join(",") === "0",
    `targets=${JSON.stringify(ui.state.selectedTargets)} action=${JSON.stringify(ui.buildAction())}`);

  // ---- ② 人类被指定：亮牌模板（点一张即提交；只读；悬停可读；其余手牌不外泄） ----
  const revealPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 2102,
    prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { kind: "SelectCards", answers_seq: 2102, picks: [0] },
      { kind: "SelectCards", answers_seq: 2102, picks: [1] },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [
      { ref: { seat: 0, zone: "hand" }, card: sameSuitA },
      { ref: { seat: 0, zone: "hand" }, card: sameSuitB },
    ],
    spec: { zones: ["hand"], total: [1, 1], sequential: false, optional: false },
    revealed: [sameSuitA, sameSuitB],
    dialog: "reveal", harvest: null, reveal: null,
    context: fireContext("从自己的手牌里亮出一张（公开）"),
    prompt_display: {
      text: "请从选择面里选择一张牌。",
      default_action: { kind: "SelectCards", answers_seq: 2102, picks: [0] },
      default_label: "超时将自动选择第一张亮牌",
    },
    preactivations: {}, preactivation: [],
  };
  const revealView = fireView(revealPending, { hand: [sameSuitA, sameSuitB] });
  renderFire(revealView);
  const revealCards = () => $("t-options").children[0].children[0].children;
  check("M5-08a-b1 被指定者（人类）看到亮牌模板：标题「火攻」、两张自己的手牌、没有放弃",
    ui.stepDialogKind(revealPending) === "reveal"
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "火攻"
      && ui.isPickRevealStep(revealPending) === false
      && revealCards().length === 2
      && revealCards().map((node) => node.getAttribute("data-card-id")).join(",") === "5,7"
      && $("btn-pass").hidden === true
      && $("btn-submit").disabled === true,
    `kind=${ui.stepDialogKind(revealPending)} title="${$("t-dialog-title").textContent}" cards=${revealCards().length} submit=${$("btn-submit").disabled}`);
  check("M5-08a-b2 亮牌弹窗打开时牌桌只读，但牌面悬停描述照常可读",
    ui.selectionEnabled() === false
      && revealCards()[0].getAttribute("title").includes("闪电的描述")
      && revealCards()[1].getAttribute("title").includes("杀的描述")
      && $("t-modal-backdrop").hidden === false,
    `enabled=${ui.selectionEnabled()} backdrop=${$("t-modal-backdrop").hidden} titles=${revealCards().map((n) => n.getAttribute("title")).join("|")}`);
  const focusBefore = ui.state.focusCardId;
  await clickHand(5);
  check("M5-08a-b3 只读态：点自己的手牌不产生选择（选择只在亮牌窗里）",
    ui.state.focusCardId === focusBefore && ui.state.picks.length === 0,
    `focus=${ui.state.focusCardId} picks=${JSON.stringify(ui.state.picks)}`);
  const beforeReveal = calls.length;
  staleAnswerStatus = 400;
  (revealCards()[0].listeners.click || []).forEach((fn) => fn({ target: revealCards()[0] }));
  await settle();
  staleAnswerStatus = null;
  const revealAnswers = answersSince(beforeReveal);
  check("M5-08a-b4 点一张亮牌 = 恰一条 SelectCards（picks 填面内位置），不手拼 Action",
    revealAnswers.length === 1
      && JSON.stringify(revealAnswers[0].body.action) === JSON.stringify(revealPending.legal_actions[0]),
    `answers=${revealAnswers.length} action=${JSON.stringify(revealAnswers[0] && revealAnswers[0].body.action)}`);

  // 别人的载荷：不是询问者 ⇒ 不开窗、不放牌面（未亮出的牌一张都不出现）。
  const foreignPending = {
    ...revealPending,
    seq: 2103,
    actor: 1,
    dialog: null,
    revealed: [],
    slots: revealPending.slots.map(() => ({ ref: { seat: 1, zone: "hand" }, card: null })),
  };
  const foreignView = fireView(foreignPending, { hand: [sameSuitA, sameSuitB] });
  foreignView.human_seat = 0;
  renderFire(foreignView);
  check("M5-08a-b5 别人的亮牌步（revealed / 槽位已抹）不开窗、不画牌面",
    $("t-dialog").hidden === true
      && ui.stepDialogKind(foreignPending) === null
      && $("t-prompt").textContent.includes("等待其他座位响应")
      && $("t-options").children.length === 0,
    `hidden=${$("t-dialog").hidden} kind=${ui.stepDialogKind(foreignPending)} prompt="${$("t-prompt").textContent}"`);

  // ---- ③ 同花色弃牌：点自己的同花色手牌 = 一次提交；也可右端放弃 ----
  const discardPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 2104,
    prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { kind: "SelectCards", answers_seq: 2104, picks: [0] },
      { kind: "SelectCards", answers_seq: 2104, picks: [1] },
      { kind: "Pass", answers_seq: 2104 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [
      { ref: { seat: 0, zone: "hand" }, card: sameSuitA },
      { ref: { seat: 0, zone: "hand" }, card: sameSuitB },
    ],
    spec: { zones: ["hand"], total: [1, 1], sequential: false, optional: true },
    revealed: [], dialog: null, harvest: null,
    reveal: { seat: 0, card: sameSuitA },
    context: fireContext("弃一张与亮出的牌同花色的手牌，造成 1 点火焰伤害，或放弃"),
    prompt_display: {
      text: "请从选择面里选择一张牌。",
      default_action: { kind: "Pass", answers_seq: 2104 },
      default_label: "超时将自动放弃",
    },
    preactivations: {}, preactivation: [],
  };
  const discardView = fireView(discardPending, {
    hand: [sameSuitA, sameSuitB, heartCard, clubCard],
  });
  renderFire(discardView);
  check("M5-08a-c1 弃牌步回到牌桌：只有同花色手牌可点，其余置灰；这一步不是弹窗",
    ui.stepDialogKind(discardPending) === null
      && ui.isOwnHandSinglePick(discardPending) === true
      && ui.selectionEnabled() === true
      && $("t-hand").children[0].classList.contains("static") === false
      && !$("t-hand").children[0].classList.contains("dis")
      && !$("t-hand").children[1].classList.contains("dis")
      && $("t-hand").children[2].classList.contains("dis")
      && $("t-hand").children[3].classList.contains("dis")
      && $("t-target-hint").textContent.includes("同花色")
      && $("btn-pass").hidden === false
      && $("btn-submit").disabled === true
      && $("btn-cancel").disabled === true,
    `kind=${ui.stepDialogKind(discardPending)} dis=${$("t-hand").children.map((n) => n.classList.contains("dis")).join(",")} passHidden=${$("btn-pass").hidden} hint="${$("t-target-hint").textContent}"`);
  check("M5-08a-c2 中央舞台看得见亮出的那一张（公开事实；与处理区同一行、不可点）",
    $("t-processing").children.some((node) => node.classList.contains("revealed-card")
      && node.getAttribute("data-card-id") === "5")
      && $("t-context").textContent.includes("亮牌")
      && $("t-context").textContent.includes("闪电"),
    `processing=${$("t-processing").text() } context="${$("t-context").textContent}"`);

  const beforeDiscardClick = calls.length;
  await clickHand(8);  // 桃（不同花色）：不可点
  check("M5-08a-c3 点不同花色的手牌 = 无提交（没有非法动作可发）",
    answersSince(beforeDiscardClick).length === 0,
    `answers=${answersSince(beforeDiscardClick).length}`);
  const beforeSuit = calls.length;
  staleAnswerStatus = 400;
  await clickHand(7);  // 同花色的杀
  await settle();
  staleAnswerStatus = null;
  const suitAnswers = answersSince(beforeSuit);
  check("M5-08a-c4 点同花色手牌 = 恰一条 SelectCards（同花色那张的面内位置）",
    suitAnswers.length === 1
      && JSON.stringify(suitAnswers[0].body.action) === JSON.stringify(discardPending.legal_actions[1]),
    `answers=${suitAnswers.length} action=${JSON.stringify(suitAnswers[0] && suitAnswers[0].body.action)}`);

  // 再渲染一次（提交被 400 拒后回到点击前那一面）：右端「放弃」= 一条 Pass，且不留选择态。
  renderFire(discardView);
  const beforePass = calls.length;
  await clickAndWait($("btn-pass"));
  await settle();
  const passAnswers = answersSince(beforePass);
  check("M5-08a-c5 右端「放弃」= 恰一条 Pass，不重绘、不残留选择态",
    passAnswers.length === 1
      && JSON.stringify(passAnswers[0].body.action) === JSON.stringify(
        discardPending.legal_actions.find((action) => action.kind === "Pass"),
      )
      && ui.state.focusCardId === null
      && ui.state.picks.length === 0,
    `answers=${passAnswers.length} action=${JSON.stringify(passAnswers[0] && passAnswers[0].body.action)} picks=${JSON.stringify(ui.state.picks)}`);

  // ---- ④ 无同花色：没有弃牌询问；事件流有「放弃（无可弃的同花色牌）」；舞台亮牌随帧撤下 ----
  const noSuitPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 2105,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 2105 }],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    context: fireContext("使用一张牌（或发动技能），或结束出牌阶段"),
    prompt_display: {
      text: "出牌阶段：使用一张牌，或结束出牌阶段。",
      default_action: { kind: "EndPhase", answers_seq: 2105 },
      default_label: "超时将自动结束出牌阶段",
    },
    preactivations: {}, preactivation: [],
  };
  const revealRecord = {
    kind: "delta", delta_type: "card_reveal", seat: 1, card: revealed, frame_id: 2, seq: 2106,
  };
  const autoPassRecord = {
    kind: "timing", timing: "RESOLVE_END", frame_id: 2, source: 0, seq: 2107,
  };
  const logWithReveal = (line, seq) => ({
    kind: "line", seq, text: line,
    segments: [{ text: line, cls: "act" }],
  });
  const noSuitBody = {
    session_id: session.session_id, seed: 20261016, player_count: 4, human_seat: 0,
    heroes: [], deck_profile: "standard_ex_160", timeout_seconds: null,
    pending_since: 0, finished: false, cursor: 2107, log_seq: 0,
    snapshot: fireView(noSuitPending).snapshot,
    skills: [], pending: noSuitPending,
    records: [revealRecord, autoPassRecord],
    narrative: [
      logWithReveal("座位1 亮出【闪电】", 2106),
      logWithReveal("座位0 放弃（无可弃的同花色牌）", 2107),
    ],
    session_log: [],
  };
  noSuitBody.snapshot.processing = [fire];
  ui.applyViewRaw(noSuitBody);
  ui.flushPlayback();
  check("M5-08a-d1 无同花色：没有弃牌询问；事件流写出「放弃（无可弃的同花色牌）」",
    ui.state.view.pending.kind === "PLAY_PHASE"
      && logText().includes("放弃（无可弃的同花色牌）")
      && !$("t-prompt").textContent.includes("选择一张手牌"),
    `log="${logText()}" prompt="${$("t-prompt").textContent}"`);
  check("M5-08a-d2 亮牌仍在中央舞台（公开事实），手牌区没有「已知」标记 / 属性",
    $("t-processing").children.some((node) => node.classList.contains("revealed-card")
      && node.getAttribute("data-card-id") === "5")
      && !$("t-hand").text().includes("已知")
      && $("t-hand").children.every((node) => !node.classList.contains("known")
        && !node.classList.contains("revealed")
        && node.getAttribute("data-known") === undefined),
    `processing=${$("t-processing").text()} hand="${$("t-hand").text()}"`);

  const clearedBody = {
    ...noSuitBody,
    cursor: 2108,
    records: [{ kind: "frame", op: "pop", frame_id: 2, depth: 1, seq: 2108 }],
    narrative: [],
  };
  clearedBody.snapshot = { ...noSuitBody.snapshot, processing: [] };
  ui.applyViewRaw(clearedBody);
  ui.flushPlayback();
  check("M5-08a-d3 结算帧出栈 ⇒ 舞台撤下亮牌（只在中央舞台留那一瞬）",
    !$("t-processing").children.some((node) => node.classList.contains("revealed-card"))
      && !$("t-context").textContent.includes("亮牌"),
    `processing=${$("t-processing").text()} context="${$("t-context").textContent}"`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}


/* ============ M5-08b（D6 / D7 / §5.1.2–5.1.3）：铁索 1–2 目标 + 重铸入口 ============
 *
 * 载荷与真会话同形（字段由 `tests/test_web.py` 的真会话断言钉）：
 *   - 铁索 = 手选路 + 候选索引 `min_targets: 1` / `max_targets: 2`：点座位切换选中，
 *     同一个座位再点一次 = 取消；0 目标时提交键不可用；可点自己；不会提交重复目标；
 *   - 重铸 = `legal_actions` 里独立的 `Reforge`（不是 `UseCard` 变体、没有 targets）：
 *     选中该牌时控件区出现「重铸」，点一下 = 恰一条 Reforge 提交；
 *   - 非出牌阶段 / 不可重铸的牌没有 Reforge 候选 ⇒ 没有入口；
 *   - 无懈窗口走既有的 RESPOND 面（右端「放弃」），不新增计数器 / 提示条 / 只读判据。
 */
{
  const iron = {
    ...synthCard(130, "铁索连环"),
    type: "iron_chain",
    display_name: "铁索连环",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "铁索连环的描述：横置或重置至多两名角色。",
    suit: "club",
    color: "black",
  };
  const slash = {
    ...synthCard(5, "杀"),
    type: "slash",
    display_name: "杀",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "杀的描述",
  };
  const nullify = {
    ...synthCard(140, "无懈可击"),
    type: "nullify",
    display_name: "无懈可击",
    kind: "normal",
    attribute: "普通",
    source_cards: [],
    description: "无懈可击的描述",
  };
  const raw = (card) => ({
    card_id: card.card_id, type: card.type, name: card.name, suit: card.suit,
    rank: card.rank, slot: card.slot, attack_range: card.attack_range,
    color: card.color, virtual_sources: [],
  });
  const ironRaw = raw(iron);
  const slashRaw = raw(slash);
  const nullifyRaw = raw(nullify);
  const ironContext = {
    actor: 0, name: null, targets: [], label: null,
    requirement: "使用一张牌（或发动技能），或结束出牌阶段", skill_hints: [],
  };
  const ironView = (pending, { hand = [iron, slash] } = {}) => {
    const view = synthView(pending);
    view.human_seat = 0;
    view.snapshot.seat = 0;
    view.snapshot.phase = "play";
    view.snapshot.turn_seat = 0;
    view.snapshot.self_hand = hand;
    view.snapshot.processing = [];
    view.snapshot.players = [0, 1, 2, 3].map((seat) => ({
      seat, hp: 4, hp_max: 4, hand_count: seat === 2 ? 0 : 2,
      alive: true, equip: [], judge: [], identity: null,
      chained: seat === 1,
    }));
    return view;
  };
  const renderIron = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const handNode = (cardId) => $("t-hand").children.find(
    (node) => node.getAttribute("data-card-id") === String(cardId),
  ) || null;
  const clickHand = async (cardId) => {
    const node = handNode(cardId);
    if (!node) return false;
    for (const fn of node.listeners.click || []) await fn({ target: node, stopPropagation() {} });
    return true;
  };
  const clickSeat = (seat) => {
    const node = seatBox(seat);
    (node.listeners.click || []).forEach((fn) => fn({ target: node, stopPropagation() {} }));
  };
  const reforgeNode = () => $("t-options").children.find(
    (node) => node.getAttribute("data-reforge") === "true") || null;
  const settle = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };
  const answersSince = (mark) => calls.slice(mark).filter((call) => call.path.includes("/answer"));
  const selected = () => ui.state.selectedTargets.join(",");
  const builtTargets = () => {
    const action = ui.buildAction();
    return action && action.targets ? action.targets.join(",") : null;
  };

  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261017,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  ui.state.modal.heroSeat = null;

  const combos = [[0], [0, 1], [0, 2], [0, 3], [1], [1, 2], [1, 3], [2], [2, 3], [3]];
  const play = {
    kind: "PLAY_PHASE", actor: 0, seq: 3101,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      ...combos.map((targets) => ({
        kind: "UseCard", answers_seq: 3101, card: ironRaw, targets,
      })),
      { kind: "UseCard", answers_seq: 3101, card: slashRaw, targets: [1] },
      { kind: "Reforge", answers_seq: 3101, card: ironRaw },
      { kind: "EndPhase", answers_seq: 3101 },
    ],
    candidate_index: {
      130: {
        card: iron, pool: [0, 1, 2, 3], combos, min_targets: 1, max_targets: 2,
      },
      5: { card: slash, pool: [1], combos: [[1]], min_targets: 1, max_targets: 1 },
    },
    virtual_index: {}, option_seats: {}, context: ironContext,
    prompt_display: {
      text: "出牌阶段：使用一张牌，或结束出牌阶段。",
      default_action: { kind: "EndPhase", answers_seq: 3101 },
      default_label: "超时将自动结束出牌阶段",
    },
    preactivations: {}, preactivation: [],
  };
  const useOn = (targets) => play.legal_actions.find(
    (action) => action.kind === "UseCard" && action.card.card_id === 130
      && action.targets.join(",") === targets.join(","),
  );

  // ---- ① 选中铁索：可点自己与所有座位；0 目标提交键不可用；重铸入口并列出现 ----
  renderIron(ironView(play));
  await clickHand(130);
  check("M5-08b-a1 选中铁索：1–2 目标（含自己）、0 目标提交键不可用、重铸入口并列出现",
    ui.state.focusCardId === 130
      && [0, 1, 2, 3].every((seat) => seatBox(seat).classList.contains("targetable"))
      && $("btn-submit").disabled === true
      && $("t-target-hint").textContent.includes("还要选 1–2 个目标")
      && ui.reforgeAction(play, 130) !== null
      && reforgeNode() !== null
      && reforgeNode().textContent.includes("重铸")
      && reforgeNode().textContent.includes("铁索连环"),
    `focus=${ui.state.focusCardId} hint="${$("t-target-hint").textContent}" submit=${$("btn-submit").disabled} reforge="${reforgeNode() && reforgeNode().textContent}"`);

  // ---- ② 点座位切换 / 再点取消；不会产生重复目标 ----
  clickSeat(1);
  check("M5-08b-a2 点座位 1 = 选中 1 个目标，提交候选原样来自 legal_actions",
    selected() === "1" && builtTargets() === "1"
      && $("btn-submit").disabled === false
      && seatBox(1).classList.contains("tsel"),
    `targets=${selected()} built=${builtTargets()} submit=${$("btn-submit").disabled}`);
  clickSeat(1);
  check("M5-08b-a3 同一座位再点一次 = 取消该目标，0 目标提交键回到不可用",
    selected() === "" && ui.buildAction() === null && $("btn-submit").disabled === true,
    `targets=${selected()} submit=${$("btn-submit").disabled}`);
  clickSeat(1);
  clickSeat(2);
  check("M5-08b-a4 2 个目标独立选中；两个座位都画成已选",
    selected() === "1,2" && builtTargets() === "1,2"
      && seatBox(1).classList.contains("tsel") && seatBox(2).classList.contains("tsel"),
    `targets=${selected()} built=${builtTargets()}`);
  clickSeat(1);
  check("M5-08b-a5 取消其中一个 = 只剩另一个（不会残留重复目标）",
    selected() === "2" && builtTargets() === "2"
      && !seatBox(1).classList.contains("tsel") && seatBox(2).classList.contains("tsel"),
    `targets=${selected()} built=${builtTargets()}`);
  clickSeat(2);
  check("M5-08b-a6 再点最后一个也取消 ⇒ 回到 0 目标、提交键不可用",
    selected() === "" && ui.buildAction() === null && $("btn-submit").disabled === true,
    `targets=${selected()} submit=${$("btn-submit").disabled}`);

  // ---- ③ 可指定自己：点座位 0（底部自己的面板） = 选中自己 ----
  clickSeat(0);
  check("M5-08b-a7 可指定自己：点座位 0 = 选中自己，提交候选的 targets 就是 0",
    selected() === "0" && builtTargets() === "0"
      && seatBox(0).classList.contains("tsel")
      && ($("btn-submit").disabled === false),
    `targets=${selected()} built=${builtTargets()} submit=${$("btn-submit").disabled}`);

  // ---- ④ 1 目标 / 2 目标各提交一次：恰一条 UseCard，原样取自 legal_actions ----
  renderIron(ironView(play));
  await clickHand(130);
  clickSeat(1);
  const beforeOne = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  await settle();
  staleAnswerStatus = null;
  const oneAnswers = answersSince(beforeOne);
  check("M5-08b-a8 1 目标提交 = 恰一条 UseCard（targets=[1]）",
    oneAnswers.length === 1
      && JSON.stringify(oneAnswers[0].body.action) === JSON.stringify(useOn([1])),
    `answers=${oneAnswers.length} action=${JSON.stringify(oneAnswers[0] && oneAnswers[0].body.action)}`);

  renderIron(ironView(play));
  await clickHand(130);
  clickSeat(1);
  clickSeat(2);
  const beforeTwo = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  await settle();
  staleAnswerStatus = null;
  const twoAnswers = answersSince(beforeTwo);
  check("M5-08b-a9 2 目标提交 = 恰一条 UseCard（targets=[1,2]），不是两条",
    twoAnswers.length === 1
      && JSON.stringify(twoAnswers[0].body.action) === JSON.stringify(useOn([1, 2])),
    `answers=${twoAnswers.length} action=${JSON.stringify(twoAnswers[0] && twoAnswers[0].body.action)}`);

  // ---- ⑤ 无懈窗口：既有 RESPOND 面；右端「放弃」是唯一出口，不出现重铸入口 ----
  const nullifyPending = {
    kind: "RESPOND", actor: 0, seq: 3102, cause: "NULLIFY",
    prompt: "是否使用【无懈可击】？",
    legal_actions: [
      { kind: "UseCard", answers_seq: 3102, card: nullifyRaw, targets: [1] },
      { kind: "Pass", answers_seq: 3102 },
    ],
    candidate_index: {
      140: {
        card: nullify, pool: [1], combos: [[1]], min_targets: 1, max_targets: 1,
      },
    },
    virtual_index: {}, option_seats: {},
    context: { ...ironContext, requirement: "使用【无懈可击】抵消这张锦囊，或放弃" },
    prompt_display: {
      text: "是否使用【无懈可击】？",
      default_action: { kind: "Pass", answers_seq: 3102 },
      default_label: "超时将自动放弃",
    },
    preactivations: {}, preactivation: [],
  };
  renderIron(ironView(nullifyPending, { hand: [iron, nullify] }));
  check("M5-08b-b1 无懈窗口沿用既有 RESPOND 面：右端「放弃」可用、不铺目标、无重铸入口",
    $("btn-pass").hidden === false
      && $("btn-pass").textContent.includes("放弃")
      && $("btn-submit").disabled === true
      && ui.state.selectedTargets.length === 0
      && ui.reforgeAction(nullifyPending, 130) === null
      && reforgeNode() === null,
    `passHidden=${$("btn-pass").hidden} pass="${$("btn-pass").textContent}" submit=${$("btn-submit").disabled} reforge=${reforgeNode() && reforgeNode().textContent}`);

  // ---- ⑥ 重铸：点一次 = 恰一条 Reforge；不进入目标选择；不产生无懈 / 使用次数 ----
  renderIron(ironView(play));
  check("M5-08b-c1 未选牌时控件区没有重铸入口（零提交）",
    reforgeNode() === null && ui.reforgeAction(play, 130) !== null,
    `entry=${reforgeNode() && reforgeNode().textContent}`);
  await clickHand(130);
  check("M5-08b-c2 不可重铸的牌（杀）没有重铸入口",
    (await (async () => { await clickHand(5); return true; })())
      && ui.state.focusCardId === 5
      && ui.reforgeAction(play, 5) === null
      && reforgeNode() === null,
    `focus=${ui.state.focusCardId} reforge=${reforgeNode() && reforgeNode().textContent}`);

  renderIron(ironView(play));
  const beforeCancel = calls.length;
  await clickAndWait($("btn-cancel"));
  check("M5-08b-c3 未选牌时点「取消」= 零提交（不发明 Action）",
    answersSince(beforeCancel).length === 0 && ui.state.focusCardId === null,
    `answers=${answersSince(beforeCancel).length} focus=${ui.state.focusCardId}`);

  await clickHand(130);
  clickSeat(1);
  clickSeat(2);
  const entryWithTargets = reforgeNode();
  const beforeReforge = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait(reforgeNode());
  await settle();
  staleAnswerStatus = null;
  const reforgeAnswers = answersSince(beforeReforge);
  const reforgeAction = play.legal_actions.find((action) => action.kind === "Reforge");
  check("M5-08b-c4 点一次「重铸」= 恰一条 Reforge POST（保留选中的目标也不进目标步）",
    reforgeAnswers.length === 1
      && JSON.stringify(reforgeAnswers[0].body.action) === JSON.stringify(reforgeAction)
      && !("targets" in reforgeAnswers[0].body.action)
      && !("designations" in reforgeAnswers[0].body.action),
    `answers=${reforgeAnswers.length} action=${JSON.stringify(reforgeAnswers[0] && reforgeAnswers[0].body.action)}`);
  check("M5-08b-c5 目标选择不改变重铸入口（独立动作）：已选 2 个目标时点重铸只发 Reforge，不发 UseCard",
    entryWithTargets !== null
      && reforgeAnswers.every((answer) => answer.body.action.kind === "Reforge"),
    `entry="${entryWithTargets && entryWithTargets.textContent}" kinds=${reforgeAnswers.map((answer) => answer.body.action.kind).join(",")}`);

  // ---- ⑦ 事件流的人话：完整档也不能把 Reforge / REFORGE 英文漏给玩家 ----
  const reforgeLabel = ui.actionLabel({ kind: "Reforge", card: ironRaw });
  // 投影对非本方应答只下发 `kind` / `answers_seq`（`visible_record`）：没有卡面时写动作本身。
  const maskedReforgeLabel = ui.actionLabel({ kind: "Reforge", answers_seq: 3101 });
  const moveLine = ui.describeRecord({
    kind: "delta", delta_type: "card_move", seq: 3104, reason: "REFORGE",
    from_zone: { seat: 0, zone: "hand" }, to_zone: { zone: "discard" }, cards: [iron],
  });
  check("M5-08b-d1 重铸在事件流 / 候选文案里都写「重铸」，不落 Reforge / REFORGE 英文",
    reforgeLabel.includes("重铸") && reforgeLabel.includes("铁索连环")
      && !reforgeLabel.includes("Reforge")
      && maskedReforgeLabel === "重铸"
      && moveLine.includes("重铸") && !moveLine.includes("REFORGE"),
    `label="${reforgeLabel}" masked="${maskedReforgeLabel}" line="${moveLine}"`);

  // ---- ⑧ 弹窗步骤沿 U2：只读判据仍是 selectionEnabled()，重铸入口不出现 ----
  const dialogPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 3103, dialog: "reveal",
    prompt: "请从选择面里选择一张牌。",
    legal_actions: [{ kind: "SelectCards", answers_seq: 3103, picks: [0] }],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [{ ref: { seat: 0, zone: "hand" }, card: slash }],
    spec: { zones: ["hand"], total: [1, 1], sequential: false, optional: false },
    revealed: [slash], harvest: null, reveal: null,
    context: { ...ironContext, requirement: "从自己的手牌里亮出一张（公开）" },
    prompt_display: {
      text: "请从选择面里选择一张牌。",
      default_action: { kind: "SelectCards", answers_seq: 3103, picks: [0] },
      default_label: "超时将自动选择第一张亮牌",
    },
    preactivations: {}, preactivation: [],
  };
  renderIron(ironView(dialogPending, { hand: [iron, slash] }));
  await clickHand(130);
  check("M5-08b-e1 弹窗步骤沿 U2 只读：牌桌不可选、重铸入口不出现（不新写第二套判据）",
    ui.stepDialogKind(dialogPending) === "reveal"
      && ui.selectionEnabled() === false
      && $("t-dialog").hidden === false
      && ui.state.focusCardId === null
      && reforgeNode() === null,
    `kind=${ui.stepDialogKind(dialogPending)} enabled=${ui.selectionEnabled()} dialogHidden=${$("t-dialog").hidden} focus=${ui.state.focusCardId} reforge=${reforgeNode() && reforgeNode().textContent}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ M5-bugfix（C）：铁索按**点击顺序**提交 ============
 *
 * 引擎侧（`effects/actions.py:target_combos(..., ordered=True)`）对声明了
 * `preserve_target_order` 的牌同时给出两种顺序的候选；界面这一侧还必须**保留点击顺序**：
 * `buildAction` 先按点击顺序找候选，找不到才退回排序后的匹配（普通牌不受影响）。
 *
 * 判据（用户逐字要求）：
 *   - 先点座位 4、再点座位 3 ⇒ 提交的是 `UseCard.targets = [4, 3]`（不是升序的 [3, 4]）；
 *   - 提交的仍是 `legal_actions` 里**原样**的那一条（不手拼 Action）；
 *   - 候选里 (3, 4) 与 (4, 3) 都存在（引擎 preserve_target_order 的下发形状）。
 */
{
  const iron = {
    ...synthCard(130, "铁索连环"),
    type: "iron_chain", display_name: "铁索连环", kind: "normal", attribute: "普通",
    source_cards: [], description: "铁索连环的描述：横置或重置至多两名角色。",
  };
  const ironRaw = {
    card_id: iron.card_id, type: iron.type, name: iron.name, suit: iron.suit,
    rank: iron.rank, slot: iron.slot, attack_range: iron.attack_range,
    color: iron.color, virtual_sources: [],
  };
  // 5 座：全部 1 元组 + 全部 2 元组的**两种点击顺序**（服务端 preserve_target_order 的形状）。
  const combos = [];
  for (let a = 0; a <= 4; a += 1) {
    combos.push([a]);
    for (let b = a + 1; b <= 4; b += 1) combos.push([a, b], [b, a]);
  }
  const play = {
    kind: "PLAY_PHASE", actor: 0, seq: 3201,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      ...combos.map((targets) => ({
        kind: "UseCard", answers_seq: 3201, card: ironRaw, targets,
      })),
      { kind: "EndPhase", answers_seq: 3201 },
    ],
    candidate_index: {
      130: {
        card: iron, pool: [0, 1, 2, 3, 4], combos, min_targets: 1, max_targets: 2,
      },
    },
    virtual_index: {}, option_seats: {},
    context: {
      actor: 0, name: null, targets: [], label: null,
      requirement: "使用一张牌（或发动技能），或结束出牌阶段", skill_hints: [],
    },
    prompt_display: {
      text: "出牌阶段：使用一张牌，或结束出牌阶段。",
      default_action: { kind: "EndPhase", answers_seq: 3201 },
      default_label: "超时将自动结束出牌阶段",
    },
    preactivations: {}, preactivation: [],
  };
  const view = synthView(play);
  view.human_seat = 0;
  view.snapshot.seat = 0;
  view.snapshot.phase = "play";
  view.snapshot.turn_seat = 0;
  view.snapshot.self_hand = [iron];
  view.snapshot.players = [0, 1, 2, 3, 4].map((seat) => ({
    seat, hp: 4, hp_max: 4, hand_count: seat === 0 ? 1 : 2,
    alive: true, equip: [], judge: [], identity: null, chained: false,
  }));
  const session = await post("/api/sessions", {
    player_count: 5, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261018,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  ui.state.modal.heroSeat = null;
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const handNode = $("t-hand").children.find(
    (node) => node.getAttribute("data-card-id") === "130",
  );
  for (const fn of handNode.listeners.click || []) fn({ target: handNode });
  const clickSeat = (seat) => {
    const node = seatBox(seat);
    (node.listeners.click || []).forEach((fn) => fn({ target: node, stopPropagation() {} }));
  };
  const settle = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };
  const useOn = (targets) => play.legal_actions.find(
    (action) => action.kind === "UseCard"
      && action.card.card_id === 130
      && action.targets.join(",") === targets.join(","),
  );

  check("M5-bugfix-c1 载荷里 (3,4) 与 (4,3) 都是合法候选（引擎 preserve_target_order 的形状）",
    !!useOn([3, 4]) && !!useOn([4, 3]),
    `4,3=${JSON.stringify(useOn([4, 3]))} 3,4=${JSON.stringify(useOn([3, 4]))}`);

  clickSeat(4);
  clickSeat(3);
  const clicked = ui.state.selectedTargets.join(",");
  const built = ui.buildAction();
  check("M5-bugfix-c2 先点 4 再点 3 ⇒ 提交的 UseCard.targets 是点击顺序 [4,3]（不是升序）",
    clicked === "4,3"
      && !!built && built.kind === "UseCard"
      && JSON.stringify(built.targets) === "[4,3]"
      && JSON.stringify(built) === JSON.stringify(useOn([4, 3])),
    `clicked=${clicked} built=${JSON.stringify(built)}`);

  const before = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  await settle();
  staleAnswerStatus = null;
  const answers = calls.slice(before).filter((call) => call.path.includes("/answer"));
  check("M5-bugfix-c3 提交恰一条 UseCard，targets 原样是点击顺序 [4,3] 且取自 legal_actions",
    answers.length === 1
      && JSON.stringify(answers[0].body.action) === JSON.stringify(useOn([4, 3]))
      && play.legal_actions.includes(useOn([4, 3])),
    `answers=${answers.length} action=${JSON.stringify(answers[0] && answers[0].body.action)}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}


/* ============ M5-08c（D5 / §5.1 酒）：酒界面（出牌阶段 / 濒死自救 / 每回合一次） ============
 *
 * 载荷与真会话同形（字段由 `tests/test_web.py` 的真会话断言钉）：
 *   - 出牌阶段【酒】= 手选路 + 候选索引 `pool: [0]` / `combos: [[0]]`（**自己为目标**）：
 *     点牌自动选中自己，**没有目标步 / 座位目标高亮**；再点控件区「提交」= 恰一条 UseCard。
 *     这是 U2 的自我目标路径（与【桃】/装备同形）——本票**不**改成"点牌即提交"。
 *   - 使用后本回合第二张【酒】没有候选条目 ⇒ 牌置灰、点它零提交。
 *   - 自己濒死：【酒】进求桃窗口（目标恒为自己，无目标步）；他人濒死：没有【酒】入口。
 *   - 只读判据仍只住 `modal.js:stepDialogKind`；弹窗步骤里点牌不选、零提交。
 *   - 事件流文案读 `actionLabel`（「使用【酒】」），不出现英文枚举。
 */
{
  const wine = {
    ...synthCard(123, "酒"),
    type: "wine", display_name: "酒", kind: "normal", attribute: "普通",
    source_cards: [], description: "酒：出牌阶段对自己使用，令下一张【杀】伤害 +1。",
    suit: "club", rank: 3, color: "black",
  };
  const wine2 = {
    ...wine, card_id: 124, suit: "heart", rank: 9, color: "red",
  };
  const peach = {
    ...synthCard(4, "桃"),
    type: "peach", display_name: "桃", kind: "normal", attribute: "普通",
    source_cards: [], description: "桃：回复 1 点体力。",
    suit: "heart", rank: 3, color: "red",
  };
  const slash = {
    ...synthCard(5, "杀"),
    type: "slash", display_name: "杀", kind: "normal", attribute: "普通",
    source_cards: [], description: "杀的描述。",
  };
  const raw = (card) => ({
    card_id: card.card_id, type: card.type, name: card.name, suit: card.suit,
    rank: card.rank, slot: card.slot, attack_range: card.attack_range,
    color: card.color, virtual_sources: [],
  });
  const wineRaw = raw(wine);
  const wine2Raw = raw(wine2);
  const peachRaw = raw(peach);
  const slashRaw = raw(slash);
  const context = {
    actor: 0, name: null, targets: [], label: null,
    requirement: "使用一张牌（或发动技能），或结束出牌阶段", skill_hints: [],
  };
  const promptDisplay = (seq, label) => ({
    text: "出牌阶段：使用一张牌，或结束出牌阶段。",
    default_action: { kind: "EndPhase", answers_seq: seq },
    default_label: label,
  });
  const wineView = (pending, { hand = [wine, wine2, slash], dyingSeat = null } = {}) => {
    const view = synthView(pending);
    view.human_seat = 0;
    view.snapshot.seat = 0;
    view.snapshot.phase = "play";
    view.snapshot.turn_seat = 0;
    view.snapshot.self_hand = hand;
    view.snapshot.processing = [];
    view.snapshot.players = [0, 1, 2, 3].map((seat) => ({
      seat, hp: dyingSeat === seat ? 0 : 4, hp_max: 4, hand_count: 2,
      alive: true, equip: [], judge: [], identity: null,
      chained: false, wine_active: false,
    }));
    view.timeout_seconds = null;
    return view;
  };
  const renderWine = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const handNode = (cardId) => $("t-hand").children.find(
    (node) => node.getAttribute("data-card-id") === String(cardId),
  ) || null;
  const clickHand = async (cardId) => {
    const node = handNode(cardId);
    if (!node) return false;
    for (const fn of node.listeners.click || []) {
      await fn({ target: node, stopPropagation() {} });
    }
    return true;
  };
  const seatFlags = (seat) => {
    const node = seatBox(seat);
    return node ? ["targetable", "tsel"].filter((name) => node.classList.contains(name)) : [];
  };
  const settle = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
  };
  const answersSince = (mark) => calls.slice(mark).filter((call) => call.path.includes("/answer"));

  const session = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261018,
  });
  ui.state.sessionId = session.session_id;
  ui.state.playback = null;
  ui.state.modal.heroSeat = null;

  const playWine = (seq = 3301) => ({
    kind: "PLAY_PHASE", actor: 0, seq,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      { kind: "UseCard", answers_seq: seq, card: wineRaw, targets: [0] },
      { kind: "UseCard", answers_seq: seq, card: wine2Raw, targets: [0] },
      { kind: "UseCard", answers_seq: seq, card: slashRaw, targets: [1] },
      { kind: "EndPhase", answers_seq: seq },
    ],
    candidate_index: {
      123: { card: wine, pool: [0], combos: [[0]], min_targets: 1, max_targets: 1 },
      124: { card: wine2, pool: [0], combos: [[0]], min_targets: 1, max_targets: 1 },
      5: { card: slash, pool: [1], combos: [[1]], min_targets: 1, max_targets: 1 },
    },
    virtual_index: {}, option_seats: {}, context,
    prompt_display: promptDisplay(seq, "超时将自动结束出牌阶段"),
    preactivations: {}, preactivation: [],
  });

  // ---- ① 出牌阶段：点【酒】只选中自己，没有目标步 / 座位高亮；再点「提交」= 1 条 POST ----
  const playPending = playWine();
  renderWine(wineView(playPending));
  const beforeClick = calls.length;
  await clickHand(123);
  check("M5-08c-a1 出牌阶段点【酒】：自动选中自己、没有目标步、没有座位目标高亮、提交可点",
    ui.state.focusCardId === 123
      && JSON.stringify(ui.state.selectedTargets) === "[0]"
      && ui.cardEntry(playPending, 123).max_targets === 1
      && [0, 1, 2, 3].every((seat) => seatFlags(seat).length === 0)
      && $("btn-submit").disabled === false
      && /自动选中|唯一确定/.test($("t-target-hint").textContent)
      && answersSince(beforeClick).length === 0,
    `focus=${ui.state.focusCardId} targets=${JSON.stringify(ui.state.selectedTargets)} flags=${[0, 1, 2, 3].map((seat) => seatFlags(seat).join("/")).join(",")} submit=${$("btn-submit").disabled} hint="${$("t-target-hint").textContent}" answers=${answersSince(beforeClick).length}`);
  ui.selectTarget(1);
  check("M5-08c-a2 没有目标步：点其他座位不改变目标（只对自己）",
    JSON.stringify(ui.state.selectedTargets) === "[0]" && ui.buildAction() !== null
      && ui.buildAction().kind === "UseCard"
      && String(ui.buildAction().card.card_id) === "123"
      && JSON.stringify(ui.buildAction().targets) === "[0]",
    `targets=${JSON.stringify(ui.state.selectedTargets)} built=${JSON.stringify(ui.buildAction())}`);
  const beforeSubmit = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  await settle();
  staleAnswerStatus = null;
  const playAnswers = answersSince(beforeSubmit);
  check("M5-08c-a3 点一次控件区「提交」= 恰一条 UseCard【酒】POST（原样取自候选、目标为自己）",
    playAnswers.length === 1
      && JSON.stringify(playAnswers[0].body.action) === JSON.stringify(playPending.legal_actions[0]),
    `answers=${playAnswers.length} action=${JSON.stringify(playAnswers[0] && playAnswers[0].body.action)}`);

  // ---- ② 使用后本回合第二张【酒】不可用：候选消失 ⇒ 置灰、点它零提交 ----
  const secondPending = {
    ...playWine(3302),
    legal_actions: [
      { kind: "UseCard", answers_seq: 3302, card: slashRaw, targets: [1] },
      { kind: "EndPhase", answers_seq: 3302 },
    ],
    candidate_index: {
      5: { card: slash, pool: [1], combos: [[1]], min_targets: 1, max_targets: 1 },
    },
  };
  renderWine(wineView(secondPending, { hand: [wine2, slash] }));
  const beforeSecond = calls.length;
  const secondNode = handNode(124);
  check("M5-08c-a4 本回合第二张【酒】没有候选、牌置灰（明确不可用）",
    ui.cardSelectable(secondPending, 124) === false
      && Boolean(secondNode) && secondNode.classList.contains("dis")
      && $("btn-submit").disabled === true,
    `selectable=${ui.cardSelectable(secondPending, 124)} dis=${secondNode && secondNode.classList.contains("dis")} submit=${$("btn-submit").disabled}`);
  await clickHand(124);
  check("M5-08c-a5 点不可用的第二张【酒】= 零提交、焦点不落下，提示说清原因",
    answersSince(beforeSecond).length === 0
      && ui.state.focusCardId === null
      && /酒/.test(ui.state.lastRejectReason),
    `answers=${answersSince(beforeSecond).length} focus=${ui.state.focusCardId} reason="${ui.state.lastRejectReason}"`);

  // ---- ③ 自己濒死：求桃窗口里的【酒】目标恒为自己，无目标步；一次提交 = 1 条 POST ----
  const selfPeach = {
    kind: "PEACH", actor: 0, seq: 3303,
    prompt: "你处于濒死状态：是否使用【桃】或【酒】自救？",
    legal_actions: [
      { kind: "UseCard", answers_seq: 3303, card: wineRaw, targets: [0] },
      { kind: "UseCard", answers_seq: 3303, card: peachRaw, targets: [0] },
      { kind: "Pass", answers_seq: 3303 },
    ],
    candidate_index: {
      123: { card: wine, pool: [0], combos: [[0]], min_targets: 1, max_targets: 1 },
      4: { card: peach, pool: [0], combos: [[0]], min_targets: 1, max_targets: 1 },
    },
    virtual_index: {}, option_seats: {},
    context: { ...context, requirement: "你处于濒死状态：使用【桃】或【酒】自救，或放弃" },
    prompt_display: {
      text: "你处于濒死状态：是否使用【桃】或【酒】自救？",
      default_action: { kind: "Pass", answers_seq: 3303 },
      default_label: "超时将自动放弃",
    },
    preactivations: {}, preactivation: [],
  };
  renderWine(wineView(selfPeach, { hand: [wine, peach], dyingSeat: 0 }));
  await clickHand(123);
  check("M5-08c-b1 自己濒死：点【酒】= 选中自己、无目标步 / 无目标高亮，提交可点",
    ui.state.focusCardId === 123
      && JSON.stringify(ui.state.selectedTargets) === "[0]"
      && [0, 1, 2, 3].every((seat) => seatFlags(seat).length === 0)
      && $("btn-submit").disabled === false,
    `focus=${ui.state.focusCardId} targets=${JSON.stringify(ui.state.selectedTargets)} flags=${[0, 1, 2, 3].map((seat) => seatFlags(seat).join("/")).join(",")} submit=${$("btn-submit").disabled}`);
  const beforeSelf = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  await settle();
  staleAnswerStatus = null;
  const selfAnswers = answersSince(beforeSelf);
  check("M5-08c-b2 濒死自救点一次「提交」= 恰一条 UseCard【酒】POST（目标为自己；不产生加伤由 pytest 钉）",
    selfAnswers.length === 1
      && JSON.stringify(selfAnswers[0].body.action) === JSON.stringify(selfPeach.legal_actions[0]),
    `answers=${selfAnswers.length} action=${JSON.stringify(selfAnswers[0] && selfAnswers[0].body.action)}`);

  // ---- ④ 他人濒死：手里有【酒】也没有入口；【桃】仍可救（对照） ----
  const otherPeach = {
    ...selfPeach,
    seq: 3304,
    legal_actions: [
      { kind: "UseCard", answers_seq: 3304, card: peachRaw, targets: [2] },
      { kind: "Pass", answers_seq: 3304 },
    ],
    candidate_index: {
      4: { card: peach, pool: [2], combos: [[2]], min_targets: 1, max_targets: 1 },
    },
    context: { ...context, requirement: "座位 2 处于濒死状态：是否对其使用【桃】？" },
    prompt_display: {
      text: "座位 2 处于濒死状态：是否对其使用【桃】？",
      default_action: { kind: "Pass", answers_seq: 3304 },
      default_label: "超时将自动放弃",
    },
  };
  renderWine(wineView(otherPeach, { hand: [wine, peach], dyingSeat: 2 }));
  const beforeOther = calls.length;
  check("M5-08c-b3 他人濒死：手里【酒】置灰、没有候选，【桃】仍可点（对照）",
    ui.cardSelectable(otherPeach, 123) === false
      && Boolean(handNode(123)) && handNode(123).classList.contains("dis")
      && ui.cardSelectable(otherPeach, 4) === true,
    `wineSelectable=${ui.cardSelectable(otherPeach, 123)} peachSelectable=${ui.cardSelectable(otherPeach, 4)}`);
  await clickHand(123);
  check("M5-08c-b4 点他人濒死窗口里的【酒】= 零提交、不设焦点",
    answersSince(beforeOther).length === 0 && ui.state.focusCardId === null,
    `answers=${answersSince(beforeOther).length} focus=${ui.state.focusCardId}`);

  // ---- ⑤ 取消 / 未选牌 = 零提交 ----
  renderWine(wineView(playWine(3305)));
  const beforeCancel = calls.length;
  await clickAndWait($("btn-cancel"));
  check("M5-08c-c1 未选牌点「取消」= 零提交（不发明 Action，也不自动使用【酒】）",
    answersSince(beforeCancel).length === 0
      && ui.state.focusCardId === null
      && $("btn-submit").disabled === true,
    `answers=${answersSince(beforeCancel).length} focus=${ui.state.focusCardId} submit=${$("btn-submit").disabled}`);

  // ---- ⑥ 只读态：弹窗步骤里点【酒】不选、零提交（判据仍是 stepDialogKind） ----
  const dialogPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 3306, dialog: "reveal",
    prompt: "请从选择面里选择一张牌。",
    legal_actions: [{ kind: "SelectCards", answers_seq: 3306, picks: [0] }],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [{ ref: { seat: 0, zone: "hand" }, card: wine }],
    spec: { zones: ["hand"], total: [1, 1], sequential: false, optional: false },
    revealed: [wine], harvest: null, reveal: null,
    context: { ...context, requirement: "从自己的手牌里亮出一张（公开）" },
    prompt_display: {
      text: "请从选择面里选择一张牌。",
      default_action: { kind: "SelectCards", answers_seq: 3306, picks: [0] },
      default_label: "超时将自动选择第一张亮牌",
    },
    preactivations: {}, preactivation: [],
  };
  renderWine(wineView(dialogPending, { hand: [wine, slash] }));
  const beforeDialog = calls.length;
  await clickHand(123);
  check("M5-08c-d1 只读态：弹窗步骤里点【酒】不设焦点、零提交（没有第二套只读判据）",
    ui.stepDialogKind(dialogPending) === "reveal"
      && ui.selectionEnabled() === false
      && $("t-dialog").hidden === false
      && ui.state.focusCardId === null
      && answersSince(beforeDialog).length === 0,
    `kind=${ui.stepDialogKind(dialogPending)} enabled=${ui.selectionEnabled()} hidden=${$("t-dialog").hidden} focus=${ui.state.focusCardId} answers=${answersSince(beforeDialog).length}`);

  // ---- ⑦ 事件流 / 候选文案：写「使用【酒】」，不落英文枚举 ----
  const wineLabel = ui.actionLabel({ kind: "UseCard", card: wineRaw });
  const wineLine = ui.describeRecord({
    kind: "action", seq: 3307, accepted: true,
    action: { kind: "UseCard", card: wineRaw, targets: [0], answers_seq: 3301 },
  });
  check("M5-08c-e1 事件流 / 候选文案写「使用【酒】」，不出现 UseCard / wine 英文枚举",
    wineLabel.includes("使用【酒】") && wineLine.includes("使用【酒】")
      && !wineLabel.includes("UseCard") && !wineLabel.includes("wine")
      && !wineLine.includes("UseCard") && !wineLine.includes("wine"),
    `label="${wineLabel}" line="${wineLine}"`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ M5-08d（D2 / D5 / D12 / §6 A2 A3）：新卡面 / 横置 / 酒 / 伤害飘字 ============
 *
 * 本组只钉**界面读法**（服务端那一半由 `tests/test_web.py` 的 M5-08d 组钉）：
 *   - 火杀 / 雷杀 = 服务端 `display_name`「杀」+ `attribute` 火焰 / 雷电（红 / 蓝）；
 *   - 酒 / 铁索 / 藤甲 / 白银狮子 / 古锭刀 / 朱雀羽扇 / 骅骝：牌名、描述、槽位、攻击范围
 *     全部来自载荷（前端没有第二份牌名 / 描述表）；
 *   - 横置 = 投影的 `chained` ⇒ 座位中央虚线（自己的静态块 `#t-self-chain` 也画），
 *     重置后消失；
 *   - 酒 = 投影的 `wine_active` ⇒ 席位泛红（无「酒」字徽章），与当前回合 / 横置 / 阵亡
 *     叠加不互挡，回合末清除后恢复；
 *   - 伤害数字只按服务端给的 `segments[].cls`（dmg-fire / dmg-thunder / dmg）上色；
 *   - 事件流：重铸 / 横置 / 重置 / 亮牌 / 酒 都是中文，完整档不漏英文枚举。
 */
{
  const css = readFileSync("tools/sgs_web/static/style.css", "utf8");
  const hasClass = (node, name) => Boolean(node)
    && (node.classList.contains(name) || String(node.className || "").split(/\s+/).includes(name));
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) {
      out.push(child);
      walk(child, out);
    }
    return out;
  };
  const byClass = (node, name) => walk(node).filter((child) => hasClass(child, name));
  const card = (id, type, name, extra = {}) => ({
    ...synthCard(id, name), type, display_name: name, kind: "normal",
    attribute: "普通", source_cards: [], slot: null, attack_range: null,
    description: `服务端描述：${name}`, ...extra,
  });
  // 载荷形状 = 服务端 `card_payload`（`display_name` / `attribute` / `description` /
  // `slot` / `attack_range` 都来自服务端；界面不兜任何一张牌名的表）。
  const fireSlash = card(201, "fire_slash", "火杀", { display_name: "杀", attribute: "火焰" });
  const thunderSlash = card(202, "thunder_slash", "雷杀", { display_name: "杀", attribute: "雷电" });
  const wine = card(203, "wine", "酒");
  const iron = card(204, "iron_chain", "铁索连环");
  const vine = card(205, "vine_armor", "藤甲", { slot: "armor" });
  const silver = card(206, "silver_lion", "白银狮子", { slot: "armor" });
  const scimitar = card(207, "ancient_scimitar", "古锭刀", { slot: "weapon", attack_range: 2 });
  const fan = card(208, "vermilion_feather_fan", "朱雀羽扇", { slot: "weapon", attack_range: 4 });
  const hualiu = card(209, "hualiu", "骅骝", { slot: "horse_defense" });
  const hand = [fireSlash, thunderSlash, wine, iron, vine, silver, scimitar, fan, hualiu];

  const playPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 3401,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 3401 }],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
    prompt_display: {
      text: "出牌阶段：使用一张牌，或结束出牌阶段。",
      default_action: { kind: "EndPhase", answers_seq: 3401 },
      default_label: "超时将自动结束出牌阶段",
    },
    preactivations: {}, preactivation: [],
  };
  const seatState = (seat, extra = {}) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, chained: false, wine_active: false, ...extra,
  });
  const displayView = () => {
    const view = synthView(playPending);
    view.human_seat = 0;
    view.snapshot.seat = 0;
    view.snapshot.phase = "play";
    view.snapshot.turn_seat = 1;
    view.snapshot.self_hand = [...hand];
    view.snapshot.players = [
      seatState(0, { chained: true, wine_active: true }),
      seatState(1, { chained: true, wine_active: true }),  // turn + chained + wine
      seatState(2),                                       // 干净对照
      seatState(3, { alive: false, chained: true, wine_active: true }),  // dead + chained + wine
    ];
    return view;
  };
  const renderDisplay = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
  };

  const view = displayView();
  renderDisplay(view);
  const faces = $("t-hand").children;
  check("M5-08d-a1 火杀 / 雷杀卡面 = 服务端「杀」+ 火焰 / 雷电（红 / 蓝），不在前端另起牌名",
    faces.length === hand.length
      && byClass(faces[0], "cname")[0].textContent === "杀"
      && byClass(faces[0], "ctype")[0].textContent === "普通 · 火焰"
      && hasClass(faces[0], "attr-fire")
      && byClass(faces[1], "cname")[0].textContent === "杀"
      && byClass(faces[1], "ctype")[0].textContent === "普通 · 雷电"
      && hasClass(faces[1], "attr-thunder")
      && /\.card\.face\.attr-fire \.cname\s*\{[^}]*#b3261e/s.test(css)
      && /\.card\.face\.attr-thunder \.cname\s*\{[^}]*#1d4e9c/s.test(css),
    `fire="${byClass(faces[0], "cname")[0].textContent}/${byClass(faces[0], "ctype")[0].textContent}" thunder="${byClass(faces[1], "cname")[0].textContent}/${byClass(faces[1], "ctype")[0].textContent}"`);

  const expectedFaces = [
    { name: "酒", type: "普通" },
    { name: "铁索连环", type: "普通" },
    { name: "藤甲", type: "防具" },
    { name: "白银狮子", type: "防具" },
    { name: "古锭刀", type: "武器 · 范围 2" },
    { name: "朱雀羽扇", type: "武器 · 范围 4" },
    { name: "骅骝", type: "防马" },
  ];
  const faceFacts = faces.slice(2).map((node) => ({
    name: byClass(node, "cname")[0].textContent,
    type: byClass(node, "ctype")[0].textContent,
    title: node.getAttribute("title"),
  }));
  check("M5-08d-a2 酒 / 铁索 / 五件装备的牌名、描述、槽位 / 攻击范围全部读载荷（前端没有第二份表）",
    faceFacts.every((fact, index) => fact.name === expectedFaces[index].name
      && fact.type === expectedFaces[index].type
      && fact.title === hand[index + 2].description),
    `faces=${JSON.stringify(faceFacts)}`);

  /* ---- 横置：投影字段 ⇒ 虚线；自己的静态块也有；重置后消失 ---- */
  check("M5-08d-b1 投影 chained=true ⇒ 座位中央虚线；自己的静态块 #t-self-chain 也画",
    byClass(seatBox(1), "chain-line").length === 1
      && byClass(seatBox(2), "chain-line").length === 0
      && seatBox(1).classList.contains("chained")
      && $("t-self-seat").classList.contains("chained")
      && $("t-self-chain").hidden === false
      && /id="t-self-chain" class="chain-line"/.test(readFileSync("tools/sgs_web/static/index.html", "utf8"))
      && /\.chain-line\s*\{[^}]*dashed/s.test(css),
    `seat1=${[...seatBox(1).classList.set].join(",")} self=${[...$("t-self-seat").classList.set].join(",")} selfChainHidden=${$("t-self-chain").hidden} lines=${byClass(seatBox(1), "chain-line").length}/${byClass(seatBox(2), "chain-line").length} css=${/\.chain-line\s*\{[^}]*dashed/s.test(css)}`);
  const resetView = displayView();
  resetView.snapshot.players[0].chained = false;
  resetView.snapshot.players[1].chained = false;
  resetView.snapshot.players[3].chained = false;
  renderDisplay(resetView);
  check("M5-08d-b2 重置后（同一份投影 chained=false）虚线与静态块一起消失——状态只读投影",
    byClass(seatBox(1), "chain-line").length === 0
      && !seatBox(1).classList.contains("chained")
      && !$("t-self-seat").classList.contains("chained")
      && $("t-self-chain").hidden === true,
    `seat1=${[...seatBox(1).classList.set].join(",")} self=${[...$("t-self-seat").classList.set].join(",")} selfChainHidden=${$("t-self-chain").hidden}`);

  /* ---- 酒：席位泛红 + 三态叠加不互挡；回合末清除后恢复 ---- */
  renderDisplay(displayView());
  const seat1 = seatBox(1);
  const seat3 = seatBox(3);
  check("M5-08d-c1 投影 wine_active=true ⇒ 席位泛红（无「酒」字徽章），与当前回合 / 横置 / 阵亡叠加不互挡",
    seat1.classList.contains("wine") && seat1.classList.contains("turn")
      && seat1.classList.contains("chained")
      && byClass(seat1, "chain-line").length === 1
      && !seat1.text().includes("酒")
      && seat3.classList.contains("wine") && seat3.classList.contains("chained")
      && seat3.classList.contains("dead")
      && byClass(seat3, "chain-line").length === 1
      && byClass(seat3, "dead-tag").length === 1
      && byClass(seat3, "dead-text").length === 1
      && $("t-self-seat").classList.contains("wine")
      && !$("t-self-seat").text().includes("酒")
      && /\.seat\.wine \.hero-block\s*\{[^}]*#fbe4de/s.test(css)
      && !/\.seat\.dead\s*\{[^}]*filter:\s*grayscale/s.test(css),
    `seat1=${[...seat1.classList.set].join(",")} seat3=${[...seat3.classList.set].join(",")} self=${[...$("t-self-seat").classList.set].join(",")}`);
  const soberView = displayView();
  soberView.snapshot.players[0].wine_active = false;
  soberView.snapshot.players[1].wine_active = false;
  soberView.snapshot.players[3].wine_active = false;
  renderDisplay(soberView);
  check("M5-08d-c2 回合末清除 wine_active 后席位恢复：泛红类撤下，横置 / 阵亡不受影响",
    !seatBox(1).classList.contains("wine")
      && !seatBox(3).classList.contains("wine")
      && !$("t-self-seat").classList.contains("wine")
      && seatBox(3).classList.contains("dead")
      && byClass(seatBox(1), "chain-line").length === 1,
    `seat1=${[...seatBox(1).classList.set].join(",")} seat3=${[...seatBox(3).classList.set].join(",")}`);

  /* ---- 伤害飘字：服务端 cls ⇒ 火红 / 雷蓝 / 普通白（前端只搬类名 + CSS 上色） ---- */
  const damageLine = (seq, seat, amount, cls) => ({
    kind: "line", seq, text: `座位${seat} 受到 ${amount} 点伤害`,
    segments: [
      { text: `座位${seat}`, cls: "seat" },
      { text: " 受到 ", cls: "act" },
      { text: String(amount), cls },
      { text: " 点伤害", cls: "act" },
    ],
  });
  ui.state.narrative = [
    damageLine(3410, 1, 1, "dmg-fire"),       // 火杀
    damageLine(3411, 1, 1, "dmg-fire"),       // 火攻
    damageLine(3412, 2, 1, "dmg-fire"),       // 连环传导（沿用源伤害属性）
    damageLine(3413, 1, 1, "dmg-thunder"),    // 雷杀
    damageLine(3414, 1, 3, "dmg-thunder"),    // 闪电（既有雷电伤害）
    damageLine(3415, 1, 1, "dmg"),            // 普通伤害
  ];
  ui.state.narrativeShown = 0;
  ui.renderLog();
  const damageSpans = byClass($("t-log"), "nar-dmg-fire")
    .concat(byClass($("t-log"), "nar-dmg-thunder"))
    .concat(byClass($("t-log"), "nar-dmg"));
  const fireSpans = byClass($("t-log"), "nar-dmg-fire");
  const thunderSpans = byClass($("t-log"), "nar-dmg-thunder");
  const plainSpans = byClass($("t-log"), "nar-dmg");
  check("M5-08d-d1 伤害数字按服务端 cls 上色：火杀 / 火攻 / 传导红、雷杀 / 闪电蓝、普通白",
    damageSpans.length === 6
      && fireSpans.length === 3 && fireSpans.every((span) => span.textContent === "1")
      && thunderSpans.length === 2
      && thunderSpans.some((span) => span.textContent === "3")
      && plainSpans.length === 1
      && /\.nar-card-fire, \.nar-dmg-fire\s*\{[^}]*#b3261e/s.test(css)
      && /\.nar-card-thunder, \.nar-dmg-thunder\s*\{[^}]*#1d4e9c/s.test(css)
      && /\.nar-dmg\s*\{[^}]*font-weight:\s*600/s.test(css),
    `fire=${fireSpans.map((span) => span.textContent)} thunder=${thunderSpans.map((span) => span.textContent)} plain=${plainSpans.map((span) => span.textContent)}`);

  /* ---- 事件流：重铸 / 横置 / 重置 / 亮牌 / 酒 都是中文，没有英文枚举 ---- */
  const describe = ui.describeRecord;
  const reforgeLine = describe({
    kind: "delta", delta_type: "card_move", seq: 3420, reason: "REFORGE",
    from_zone: { seat: 0, zone: "hand" }, to_zone: { zone: "discard" }, cards: [iron],
  });
  const chainLine = describe({
    kind: "delta", delta_type: "chained_change", seq: 3421, frame_id: 7, depth: 0,
    seat: 1, chained: true,
  });
  const resetLine = describe({
    kind: "delta", delta_type: "chained_change", seq: 3422, frame_id: 7, depth: 0,
    seat: 1, chained: false,
  });
  const wineLine = describe({
    kind: "delta", delta_type: "wine_change", seq: 3423, frame_id: 7, depth: 0,
    seat: 0, active: true,
  });
  const revealLine = describe({
    kind: "delta", delta_type: "card_reveal", seq: 3424, frame_id: 7, depth: 0,
    seat: 1, card: iron,
  });
  const allLines = [reforgeLine, chainLine, resetLine, wineLine, revealLine].join(" | ");
  check("M5-08d-e1 完整事件流：重铸 / 横置 / 重置 / 酒 / 亮牌都是中文，不落 REFORGE / *_change 英文枚举",
    reforgeLine.includes("重铸") && reforgeLine.includes("铁索连环")
      && chainLine.includes("横置") && resetLine.includes("重置")
      && wineLine.includes("酒") && revealLine.includes("亮牌")
      && revealLine.includes("铁索连环")
      && !/REFORGE|Reforge|chained_change|wine_change|card_reveal|洗牌/.test(allLines),
    allLines);
  const hiddenMoveLine = describe({
    kind: "delta", delta_type: "card_move", seq: 3426, reason: "DRAW",
    from_zone: { zone: "deck" }, to_zone: { seat: 1, zone: "hand" },
    cards: [], card_count: 2,
  });
  check("M5-09c-e3 隐藏区牌移动只写张数、不写牌名（脱敏在服务端，前端只按下发值渲染）",
    hiddenMoveLine.includes("2 张牌") && !hiddenMoveLine.includes("【"),
    hiddenMoveLine);
  ui.state.narrative = [{
    kind: "line", seq: 3425, text: "座位0 重铸【铁索连环】",
    segments: [
      { text: "座位0", cls: "seat" },
      { text: " 重铸", cls: "act" },
      { text: "【铁索连环】", cls: "card" },
    ],
  }];
  ui.state.narrativeShown = 0;
  ui.renderLog();
  check("M5-08d-e2 简易事件流也写「重铸」（不是「弃置」），服务端叙事原样渲染",
    logText().includes("重铸") && logText().includes("铁索连环")
      && !logText().includes("弃置"),
    `log="${logText()}"`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

console.log(failures === 0 ? `\n全部通过（驱动 ${steps} 步对局，覆盖 ${seen.size} 类询问）` : `\n${failures} 项失败`);process.exit(failures === 0 ? 0 : 1);
