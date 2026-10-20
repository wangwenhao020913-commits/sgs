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
    this.classList = new ClassList(); this._text = ""; this._html = ""; this.value = "";
    this.hidden = false; this.disabled = false; this.type = ""; this.className = "";
    // 真 DOM 的 `element.style` 是个可写对象；垫片补一个扁平副本，供断言"写进去了什么"。
    this.style = {};
    // 真 DOM 的 `<input type=checkbox>` 缺省未勾选；前端读的是 `.checked`（不是 `.value`）。
    this.checked = false;
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent || c.text()).join(""); }
  set textContent(v) { this._text = String(v); this._html = ""; this.children = []; }
  get innerHTML() { return this._html + this.children.map((c) => c.dump()).join(""); }
  // 垫片的 innerHTML 赋值要**真的清空子节点**（真 DOM 就是这么干的；不清空会造出
  // "元素累积"这种假 bug —— 第一版垫片在这里骗过我一次）。
  set innerHTML(v) { this._html = String(v); this.children = []; this._text = ""; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(k, fn) { (this.listeners[k] ||= []).push(fn); }
  // 静态节点（U1-01 起自己的座位块常驻 DOM）用 `onclick` 属性覆盖式绑定：
  // 真 DOM 里它是"同一个处理器"，垫片把它映射到 listeners.click，语义一致。
  set onclick(fn) { this.listeners.click = fn ? [fn] : []; }
  get onclick() { return (this.listeners.click || [])[0] || null; }
  setAttribute(name, value) { (this.attrs ||= {})[name] = String(value); }
  getAttribute(name) { return (this.attrs || {})[name]; }
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
byId.get("t-log-mode").value = "focus";
Object.assign(byId.get("in-players"), { value: "8" });
Object.assign(byId.get("in-ai"), { value: "heuristic" });
Object.assign(byId.get("in-seat"), { value: "0" });
Object.assign(byId.get("in-timeout"), { value: "0" });
Object.assign(byId.get("in-seed"), { value: "20261012" });

const documentShim = {
  getElementById: (id) => byId.get(id) || (byId.set(id, el("div", id)), byId.get(id)),
  createElement: (tag) => new El(tag),
  addEventListener: () => {},
};

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
/** 牌桌座位节点：对手在 `#t-seats` 环上，自己在底部 `#t-self-seat`（U1-01 起不在环上）。 */
const seatNodes = () => [...$("t-seats").children, $("t-self-seat")];
const seatBox = (seat) => seatNodes().find(
  (node) => node.getAttribute("data-seat") === String(seat)) || null;
/** 自己装备区里的第一张牌（就地高亮的用例共用一处查找）。 */
const selfEquipChip = () => {
  const row = $("t-self-zones").children.find((c) => c.getAttribute("data-zone") === "equip");
  return row ? row.children[0] : null;
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
check("对手座位块显示武将（公开信息）", /[a-z_]{4,}/.test($("t-seats").children[0].text()), $("t-seats").children[0].text().replace(/\s+/g, " ").trim().slice(0, 40));
check("自己的武将块显示武将", /[a-z_]{4,}/.test($("t-hero").textContent), $("t-hero").textContent);
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
    const chip = $("t-hand").children.find((c) => (c._html || "").includes(partial.unusable.name) && c.classList.contains("dis"));
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
  const giveUp = buttons.find((b) => (b.textContent || "").includes("放弃"));
  check("㉿R1 响应窗口里『放弃』是一个**看得见**的按钮", !!giveUp,
    `选项=${JSON.stringify(buttons.map((b) => b.textContent))}`);
  check("㉿R2 有【杀】可出时，未选任何东西**不可**提交（别替玩家出牌）",
    $("btn-submit").disabled === true && ui.buildAction() === null);
  if (giveUp) {
    (giveUp.listeners.click || []).forEach((fn) => fn({ target: giveUp }));
    check("㉿R3 点『放弃』= 选中那条 Pass 候选", (() => {
      const picked = ui.buildAction();
      return picked !== null && picked.kind === "Pass";
    })(), `buildAction=${JSON.stringify(ui.buildAction())}`);
    check("㉿R4 点『放弃』后提交按钮可点", $("btn-submit").disabled === false);
  }
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
  if (chosen.kind === "SelectCards") {
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
const zoneRow = (seat, zone) => {
  const node = seatNode(seat);
  return node ? node.children.find((child) => child.getAttribute("data-zone") === zone) : null;
};
const cardText = (card) => `${card.name}${card.suit || ""}${card.rank ?? ""}`;
const cardsIn = (node) => (node ? node.children.filter(
  (child) => child.tagName === "BUTTON" || String(child.className || "").split(/\s+/).includes("card"),
) : []);
const rowCards = (row) => cardsIn(row).map((child) => child.text());
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
      if (actual.join("|") !== wanted.join("|")) mismatches.push(`座位${seat}: ${actual.join("|")} ≠ ${wanted.join("|")}`);
    });
    check(`回放页 ${zone} 区逐座位 = 从 card_move 重建的结果`,
      mismatches.length === 0, mismatches.slice(0, 2).join("；"));
  }
  const opponent = expected.zones.findIndex(
    (seatState, seat) => seat !== target.human_seat && seatState.hand.length > 0);
  check("回放页上帝视角：非人类座位的手牌也画出牌面",
    opponent >= 0 && rowCards(zoneRow(opponent, "hand")).length > 0,
    `座位=${opponent} 手牌=${opponent >= 0 ? rowCards(zoneRow(opponent, "hand")).join("|") : "—"}`);
  const expectedProcessing = [...expected.processing.values()].map(cardText);
  check("回放页处理区 / 效果区 = 从 card_move 重建的结果",
    cardsIn($("r-processing")).map((child) => child.text()).join("|")
      === expectedProcessing.join("|"),
    `面板=${cardsIn($("r-processing")).map((child) => child.text()).join("|")} / 记录=${expectedProcessing.join("|")}`);
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
  const zoneRow = (seat, zone) => {
    // 自己的判定 / 装备区在底部面板的 `#t-self-zones` 里（U1-01 起不在环上）。
    const box = Number(seat) === Number(selfSeat) ? $("t-self-zones") : seatBox(seat);
    return box ? (box.children || []).find((c) => c.getAttribute && c.getAttribute("data-zone") === zone) : null;
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
  check("㉿X1f 顶栏：左身份配比 / 中轮次与行动者 / 右牌堆弃牌堆 + 设置 + 回放，且不放计时器",
    (() => {
      const topbar = tableView.slice(at('id="table-topbar"'), at('id="table-stage"'));
      return topbar.includes('id="t-identity-panel"')
        && topbar.includes('id="t-round"')
        && topbar.includes('id="t-turn"')
        && topbar.includes('id="t-deck"')
        && topbar.includes('id="t-discard"')
        && topbar.includes('id="btn-settings"')
        && topbar.includes('id="btn-go-replay"')
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
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

  // ② 步 2：**目标**猜花色（`pending.actor` 是别人 ⇒ 文案必须写出归属）。
  const suitView = synthView({
    kind: "SELECT_OPTION", actor: 1, seq: 961, prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "heart", answers_seq: 961 },
      { kind: "SelectOption", choice: "spade", answers_seq: 961 },
      { kind: "Pass", answers_seq: 961 },
    ],
    candidate_index: {}, virtual_index: {},
    context: { requirement: "由目标选择一种花色", skill_hints: [] },
  });
  suitView.skills = [fanjianSkill];
  suitView.heroes = { 0: "zhouyu", 1: "guanyu" };
  ui.state.view = suitView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(suitView);

  check("㉿V2 步 2 的文案写明归属 =「由目标选择一种花色」",
    $("t-prompt").textContent === "由目标选择一种花色", `"${$("t-prompt").textContent}"`);
  // 候选列表形态的 `SELECT_OPTION`（花色不是座位）：未选任何一项时取消按钮先不可点，
  // 选中一项后可点 —— 与座位步 `renderSeatPick`（有 Pass 即可取消）略有差别，是既有形态。
  const suitButton = $("t-options").children[0];
  (suitButton.listeners.click || []).forEach((fn) => fn({ target: suitButton }));
  check("㉿V3 步 2 选中一项花色后取消按钮可点", $("btn-cancel").disabled === false,
    `disabled=${$("btn-cancel").disabled}`);

  ui.state.sessionId = "smoke-missing-session";
  const beforeSuit = calls.length;
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
  const sentSuit = calls.length > beforeSuit ? calls[calls.length - 1].body.action : null;
  check("㉿V4 步 2 点取消 = 送出 Pass", !!sentSuit && sentSuit.kind === "Pass",
    JSON.stringify(sentSuit));

  // ③ 步 3：**目标**从**发动者**手里盲挑一张（槽位只有位置、没有牌面）。
  const fanCardView = synthView({
    kind: "SELECT_CARDS", actor: 1, seq: 962, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { kind: "SelectCards", answers_seq: 962, picks: [0] },
      { kind: "Pass", answers_seq: 962 },
    ],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [{ ref: { seat: 0, zone: "hand" }, card: null }],
    spec: { zones: ["hand"], total: [1, 1], sequential: false, optional: true },
    context: { requirement: "由目标从你的手牌中选择一张", skill_hints: [] },
  });
  fanCardView.skills = [fanjianSkill];
  fanCardView.heroes = { 0: "zhouyu", 1: "guanyu" };
  fanCardView.snapshot.self_hand = [];
  ui.state.view = fanCardView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(fanCardView);

  check("㉿V5 步 3 的文案写明归属 =「由目标从你的手牌中选择一张」",
    $("t-prompt").textContent === "由目标从你的手牌中选择一张",
    `"${$("t-prompt").textContent}"`);
  check("㉿V6 步 3 铺出发动者（座位 0）的**隐藏**槽位（盲挑，不亮牌面）",
    $("t-options").children.length === 2 && $("t-options").children[1].children.length === 1,
    `块=${$("t-options").children.length}`);

  const beforeFanCard = calls.length;
  ($("btn-cancel").listeners.click || []).forEach((fn) => fn({ target: $("btn-cancel") }));
  const sentFanCard = calls.length > beforeFanCard ? calls[calls.length - 1].body.action : null;
  check("㉿V7 步 3 点取消 = 送出 Pass（中途退出这一步）",
    !!sentFanCard && sentFanCard.kind === "Pass", JSON.stringify(sentFanCard));
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

  check("㉿PZ13 被动入口：没有「发动技能 XXX」选项，技能按钮标出清单",
    !$("t-options").children.some((b) => /发动技能/.test(b.textContent))
      && (() => {
        const b = $("t-skills").children.find((x) => x.getAttribute("data-skill-id") === "demo.passive");
        return !!b && b.disabled === false && b.getAttribute("data-preactivation") === "1";
      })(),
    `选项=${JSON.stringify($("t-options").children.map((b) => b.textContent))}`);
  const beforePass = calls.length;
  const cancel = $("btn-cancel");
  (cancel.listeners.click || []).forEach((fn) => fn({ target: cancel }));
  await new Promise((resolve) => setImmediate(resolve));
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

{
  const dodge = { ...synthCard(51, "闪"), type: "dodge" };
  const realSlash = { ...synthCard(52, "杀"), type: "slash" };
  const sourceStep = {
    name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
    pool: [{ when: [], options: ["51"] }],
  };
  const holderPending = {
    kind: "RESPOND", actor: 1, seq: 92, cause: "BORROWED_SWORD", required: "slash",
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

  const passButton = $("t-options").children.find((c) => c.textContent === "交出武器");
  check("㉿BS8 持刀者窗口：`Pass` 按钮文案 =「交出武器」（不是「放弃」）",
    !!passButton,
    `options=${$("t-options").children.map((c) => c.textContent).join(" / ")}`);
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
    $("t-options").children.some((c) => c.textContent === "交出武器"),
    `options=${$("t-options").children.map((c) => c.textContent).join(" / ")}`);
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
  const activateButton = $("t-options").children.find(
    (node) => node.textContent.includes("发动技能")
      && node.textContent.includes("hero.zhugeliang.guanxing"),
  );
  if (activateButton) clickNode(activateButton);
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
 */
{
  const savedView = ui.state.view;
  const savedLog = ui.state.logRecords;
  const designStage = { width: 1600, height: 608 };
  const seatW = 210;
  const seatH = 150;
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
  const overlaps = (boxes) => {
    const found = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        const overlapX = Math.abs(a.x - b.x) < seatW;
        const overlapY = Math.abs(a.y - b.y) < seatH;
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
    check(`U1-01d ${count} 人：对手座位无正面积重叠（210×150 设计尺寸）`,
      overlaps(seats).length === 0, overlaps(seats).join("、") || "无重叠");
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

  // 顶栏「回放」入口 = 真切页（不是只在 HTML 里有个 id）；顶部「牌桌」能回来。
  $("view-table").hidden = false;
  $("view-replay").hidden = true;
  (($("btn-go-replay").listeners.click) || []).forEach((fn) => fn({ target: $("btn-go-replay") }));
  check("U1-01p 顶栏回放入口切到回放页",
    $("view-replay").hidden === false && $("view-table").hidden === true,
    `table=${$("view-table").hidden} replay=${$("view-replay").hidden}`);
  (($("nav-table").listeners.click) || []).forEach((fn) => fn({ target: $("nav-table") }));
  check("U1-01p2 顶部「牌桌」切回牌桌",
    $("view-table").hidden === false && $("view-replay").hidden === true,
    `table=${$("view-table").hidden} replay=${$("view-replay").hidden}`);

  // 复原：后面的收尾断言（若有）仍看得到最后一局的真视图。
  ui.state.view = savedView;
  ui.state.logRecords = savedLog;
  ui.renderTable();
}

console.log(failures === 0 ? `\n全部通过（驱动 ${steps} 步对局，覆盖 ${seen.size} 类询问）` : `\n${failures} 项失败`);process.exit(failures === 0 ? 0 : 1);
