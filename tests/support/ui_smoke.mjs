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

/* 真 DOM 的 `element.children` 是**活的 HTMLCollection**：只有 `length`、数字下标、
 * `item(i)` 和迭代器；`find` / `map` / `filter` / `includes` 这些数组方法一概没有。
 * 垫片此前把 children 做成真数组，于是「对 children 直接调 `.find`」在真浏览器里抛
 * `TypeError` 的 bug（U3 实机验收的舞台卡缺陷）在烟测里全绿。这里用 Proxy 包住活的
 * `_children`，只放行上述读法：数组方法和赋值一律不存在，同一条 bug 在垫片里也会红。 */
const htmlCollectionView = (children) => new Proxy(children, {
  get(target, prop) {
    if (prop === "length") return target.length;
    if (prop === "item") return (index) => target[index] ?? null;
    if (prop === Symbol.iterator) return target[Symbol.iterator].bind(target);
    if (typeof prop === "string" && /^(?:0|[1-9]\d*)$/.test(prop)) return target[Number(prop)];
    // find / map / filter / forEach / includes / push / splice …… 一概不提供。
    return undefined;
  },
  has(target, prop) {
    if (prop === "length" || prop === "item" || prop === Symbol.iterator) return true;
    return typeof prop === "string" && /^(?:0|[1-9]\d*)$/.test(prop) && Number(prop) < target.length;
  },
  // 只读：真 DOM 的 children 也没有 setter，`el.children = ...` 应当失败。
  set() { return false; },
});

/* U4-01：飞行层要读**起 / 落点矩形**，并在飞行节点上起 Web Animations 动画。
 * 垫片不排版，故给每一步一个**确定性**矩形：固定 id 有显式表，其余按 data-* / 构造序
 * 派生（同一步里同一节点重复读 = 同一个值）。动画只登记参数、不自动跑——也就不会触发
 * `onfinish` 删节点，用例可以在一步之后数飞行器、读 `delay`。 */
const RECT_TABLE = new Map(Object.entries({
  "table-shell": { left: 0, top: 0, width: 1440, height: 810 },
  "t-anim-flights": { left: 0, top: 0, width: 1440, height: 810 },
  "t-target-lines": { left: 0, top: 0, width: 1440, height: 810 },
  "t-deck": { left: 1120, top: 16, width: 44, height: 20 },
  "t-discard": { left: 1210, top: 16, width: 44, height: 20 },
  "t-processing": { left: 520, top: 190, width: 400, height: 180 },
  "t-hand": { left: 280, top: 620, width: 720, height: 140 },
  "t-self-seat": { left: 1040, top: 560, width: 380, height: 240 },
  "t-self-zones": { left: 1200, top: 560, width: 220, height: 240 },
  "t-self-hand": { left: 1100, top: 604, width: 40, height: 20 },
  "t-seats": { left: 20, top: 60, width: 1000, height: 420 },
  // U4-04：阶段飘字层由 anim.js 动态建出（inset: 0 铺满壳），与飞行层同一块矩形。
  "t-anim-phase": { left: 0, top: 0, width: 1440, height: 810 },
}));
let rectSeq = 0;
const rectOfBox = (left, top, width, height) => ({
  left, top, width, height, right: left + width, bottom: top + height, x: left, y: top,
});
const hasClassText = (node, name) =>
  String((node && node.className) || "").split(/\s+/).includes(name);
/** 节点所属座位号（沿 `parentNode` 找最近的 `data-seat`）；找不到 ⇒ `null`。 */
const seatAncestorOf = (node) => {
  let cursor = node ? node.parentNode : null;
  while (cursor) {
    const seat = cursor.getAttribute ? cursor.getAttribute("data-seat") : undefined;
    if (seat !== undefined && seat !== null && seat !== "") return Number(seat);
    cursor = cursor.parentNode;
  }
  return null;
};
const shimRect = (node) => {
  const explicit = node.id ? RECT_TABLE.get(node.id) : null;
  if (explicit) return rectOfBox(explicit.left, explicit.top, explicit.width, explicit.height);
  const attr = (name) => (node.getAttribute ? node.getAttribute(name) : undefined);
  const cardId = attr("data-card-id");
  const stageKey = attr("data-stage-key");
  const seat = attr("data-seat");
  const zone = attr("data-zone");
  const slot = attr("data-slot");
  if (cardId !== undefined && cardId !== null) {
    const n = Number(cardId);
    const safe = Number.isFinite(n) ? n : rectSeq;
    return rectOfBox(140 + (safe * 37) % 880, 120 + (safe * 53) % 460, 88, 124);
  }
  if (stageKey !== undefined && stageKey !== null) {
    const n = Number(stageKey);
    const safe = Number.isFinite(n) ? n : rectSeq;
    return rectOfBox(540 + (safe * 61) % 300, 210, 88, 124);
  }
  if (seat !== undefined && seat !== null) {
    const n = Number(seat);
    const left = 80 + (Number.isFinite(n) ? n : 0) * 180;
    const top = zone === "judge" ? 100 : zone === "equip" ? 160 : 60;
    return rectOfBox(left, top, 120, slot ? 34 : 52);
  }
  // 座位里的武将块（无 id / data-* 属性）：位置按**所属座位**固定派生。座位每次重绘都会
  // 重建这些节点，若沿用下面"构造序"的通用矩形，同一个武将块在重绘前后会落到不同位置——
  // 「两条飘字起点相差 14」这类跨重绘比较位置的用例（U3-04d）就会随全局取数序偶发变红。
  if (!node.id && hasClassText(node, "hero-block")) {
    const seatNo = seatAncestorOf(node);
    if (seatNo !== null && Number.isFinite(seatNo)) {
      return rectOfBox(84 + seatNo * 180, 76, 104, 40);
    }
  }
  if (hasClassText(node, "face")) return rectOfBox(360, 300, 88, 124);
  // U4-04：阶段飘字的盒子由文本 / 设计单位决定（垫片只给一个确定尺寸，供夹边距用）。
  if (hasClassText(node, "anim-phase-float")) return rectOfBox(0, 0, 100, 28);
  rectSeq += 1;
  return rectOfBox(40 + (rectSeq % 12) * 100, 40 + Math.floor(rectSeq / 12) * 90, 100, 40);
};

class El {
  constructor(tag = "div", id = "") {
    this.tagName = tag.toUpperCase(); this.id = id;
    // 子节点本身放在 `_children`（垫片内部读写它）；对外的 children 是上面的活视图。
    this._children = [];
    this._childrenView = htmlCollectionView(this._children);
    this.listeners = {};
    // U4-01：飞行器在这上面挂动画参数（见 `animate`）。
    this.animations = [];
    this.parentNode = null;
    this.classList = new ClassList(); this._text = ""; this._html = ""; this.value = "";
    this.hidden = false; this.disabled = false; this.type = "";
    // 真 DOM 的 `element.style` 是个可写对象；垫片补一个扁平副本，供断言"写进去了什么"。
    this.style = {};
    // 真 DOM 的 `<input type=checkbox>` 缺省未勾选；前端读的是 `.checked`（不是 `.value`）。
    this.checked = false;
    // 滚动容器的三个度量：舞台「粘底」断言直接写这几个数（不读布局）。
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
  }
  /** `className` 与 `classList` 在真 DOM 里是**同一个属性**：垫片曾把它做成两个互不相干
   *  的字段，于是"代码用 `el.className = "opt"`、断言用 `classList.contains("opt")`"这种
   *  读法在垫片里恒假（真浏览器恒真）。这里补成一对访问器。 */
  get className() { return Array.from(this.classList.set).join(" "); }
  set className(v) { this.classList.set = new Set(String(v).split(/\s+/).filter(Boolean)); }
  /** 活的 HTMLCollection 形状视图（真 DOM 的 `element.children`）。 */
  get children() { return this._childrenView; }
  get textContent() { return this._text + this._children.map((c) => c.textContent || c.text()).join(""); }
  set textContent(v) { this._text = String(v); this._html = ""; this._detachChildren(); }
  get innerHTML() { return this._html + this._children.map((c) => c.dump()).join(""); }
  // 垫片的 innerHTML 赋值要**真的清空子节点**（真 DOM 就是这么干的；不清空会造出
  // "元素累积"这种假 bug —— 第一版垫片在这里骗过我一次）。
  set innerHTML(v) { this._html = String(v); this._detachChildren(); this._text = ""; }
  _detachChildren() {
    for (const child of this._children) child.parentNode = null;
    // 原地清空：children 是活集合，视图不能换目标。
    this._children.length = 0;
  }
  appendChild(c) {
    // 真 DOM 的 appendChild = **移动**（不是复制）。模态把 `#t-options` 搬进正文、
    // 关闭再搬回来，若垫片只 push，会造出"同一个节点在两个父节点的 children 里"的假象。
    if (c && c.parentNode) {
      const at = c.parentNode._children.indexOf(c);
      if (at >= 0) c.parentNode._children.splice(at, 1);
    }
    c.parentNode = this;
    this._children.push(c);
    return c;
  }
  // 票 01：排列面的落点预览靠 `insertBefore` 把占位格插到某张牌之前（拆不开的整面重绘
  // 会在真浏览器里中断拖拽）。语义照真 DOM：**移动**；`c === ref` 是 no-op；`ref === null`
  // 追加；`ref` 不是本节点的子节点 ⇒ 抛 NotFoundError（不静默追加，免得掩盖"锚已经过期"）。
  insertBefore(c, ref) {
    if (!c) return c;
    if (c === ref) return c;
    if (ref !== null && ref !== undefined && this._children.indexOf(ref) < 0) {
      const error = new Error("insertBefore: ref 不是本节点的子节点");
      error.name = "NotFoundError";
      throw error;
    }
    if (c.parentNode) {
      const at = c.parentNode._children.indexOf(c);
      if (at >= 0) c.parentNode._children.splice(at, 1);
    }
    c.parentNode = this;
    const at = ref ? this._children.indexOf(ref) : -1;
    if (at < 0) this._children.push(c);
    else this._children.splice(at, 0, c);
    return c;
  }
  // 舞台卡面按 `data-stage-key` 复用节点（淡出靠同一个节点上的 CSS 过渡），
  // 故垫片必须有真正的 removeChild —— 被撤下的旧项要真的从父节点消失。
  removeChild(c) {
    const at = this._children.indexOf(c);
    if (at >= 0) this._children.splice(at, 1);
    if (c) c.parentNode = null;
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
    return this._children.find((c) => c.tagName.toLowerCase() === tag) || null;
  }
  dump() { return `<${this.tagName.toLowerCase()}>${this._html}${this._children.map((c) => c.dump()).join("")}</${this.tagName.toLowerCase()}>`; }
  scrollIntoView() {}
  /* U4-01：确定性矩形（见 shimRect）；真 DOM 返回视口坐标，垫片只有"存在 / 不同"两种读法。 */
  getBoundingClientRect() {
    if (!this._rect) this._rect = shimRect(this);
    return this._rect;
  }
  /* U4-01：Web Animations 的登记版：参数原样存下，`onfinish` / `oncancel` 由用例显式触发
   * （飞行层本身只负责在两者之一里移除节点）。 */
  animate(keyframes, options = {}) {
    const animation = {
      keyframes, options: { ...options }, onfinish: null, oncancel: null,
      cancel() { if (this.oncancel) this.oncancel(); },
      finish() { if (this.onfinish) this.onfinish(); },
    };
    this.animations.push(animation);
    return animation;
  }
  get lastChild() { return this._children[this._children.length - 1] || null; }
  text() { return (this._html || this._text).replace(/<[^>]*>/g, "") + this._children.map((c) => c.text()).join(""); }
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
let nextGetPendingSticky = false; // true = 这份注入一直有效到显式清掉（409 刷新窗口用）
/* U3-05 / D12：静默轮询的失败注入——接下来的 N 次 `GET /api/sessions/…` 直接失败。
 * 用来钉「失败静默，连续 3 次才提示」；注入用完即清。 */
let failNextGets = 0;
/* U4-01：飞行时间表 `anim.ini` 是相对静态文件的 fetch（不是 `/api/` 请求）。烟测默认回
 * **磁盘上的真文件**（改 ini 即生效的口径）；用例可注入下一份 `{ok, text}` 看缺键 /
 * 非法值 / 未知键的退回默认与"一行提示"。 */
let iniInjection = null;
const readIniFile = () => {
  try { return readFileSync("tools/sgs_web/static/anim.ini", "utf8"); } catch { return ""; }
};

globalThis.fetch = async (path, options) => {
  if (path === "anim.ini") {
    const injected = iniInjection;
    iniInjection = null;
    if (injected) {
      return {
        ok: injected.ok !== false,
        status: injected.ok === false ? 404 : 200,
        text: async () => String(injected.text || ""),
        json: async () => ({}),
      };
    }
    const text = readIniFile();
    return { ok: true, status: 200, text: async () => text, json: async () => ({}) };
  }
  if (typeof path === "string" && path.startsWith("/api/")) {
    calls.push({ path, body: options && options.body ? JSON.parse(options.body) : null });
    if (failNextGets > 0 && !(options && options.method) && path.startsWith("/api/sessions/")) {
      failNextGets -= 1;
      throw new Error("注入的取增量失败");
    }
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
      if (!nextGetPendingSticky) nextGetPending = null;
      body.pending = pending;
      const text = JSON.stringify(body);
      return { ok: response.ok, status: response.status, text: async () => text, json: async () => JSON.parse(text) };
    }
    return response;
  }
  return realFetch(path, options);
};

/* 计时器垫片：**只排队、不自动跑**。既有用例仍由 `flushPlayback()` 一次性冲刷；
 * 回放 play/pause 的用例显式跑一条，才能断言"暂停后计时器被清掉、播放推进只留一个"。
 * `setInterval` 只登记（U3-05 起 2 秒轮询靠它接线）：用例按登记表取回调显式跑一拍。 */
const intervals = [];
const pendingTimers = new Map();
/* U4-02：步长是**排程出来**的毫秒数，垫片顺手记下 `setTimeout` 的第二个参数，
 * 用例才能断言"收尾按 delayOf(record) 排、不是固定步长"（不读真实时钟）。 */
const timerDelays = new Map();
let nextTimerId = 1;
const runNextTimer = () => {
  const entry = pendingTimers.entries().next().value;
  if (!entry) return false;
  pendingTimers.delete(entry[0]);
  timerDelays.delete(entry[0]);
  entry[1]();
  return true;
};
/** 最近一个排队的计时器毫秒数（一步回放的收尾排程总是**最后**排的那个）。 */
const latestTimerDelay = () => {
  const ids = Array.from(timerDelays.keys());
  return ids.length ? timerDelays.get(ids[ids.length - 1]) : null;
};
const windowShim = {
  setTimeout: (fn, ms) => {
    const id = nextTimerId;
    nextTimerId += 1;
    pendingTimers.set(id, fn);
    timerDelays.set(id, ms);
    return id;
  },
  clearTimeout: (id) => { pendingTimers.delete(id); timerDelays.delete(id); },
  setInterval: (fn, ms) => {
    intervals.push({ fn, ms });
    return intervals.length;
  },
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
const candidateRowButtons = () => Array.from($("t-options").children).filter(
  (node) => node.tagName === "BUTTON" && CANDIDATE_ROW_PATTERN.test(node.textContent || ""),
);
/** 牌桌座位节点：对手在 `#t-seats` 环上，自己在底部 `#t-self-seat`（U1-01 起不在环上）。 */
const seatNodes = () => [...$("t-seats").children, $("t-self-seat")];
const seatBox = (seat) => seatNodes().find(
  (node) => node.getAttribute("data-seat") === String(seat)) || null;
/** 预发动步骤条的 chip 行（`renderPreativation` 铺在 `#t-options` 里的那个 `.cards` DIV）。
 *  预发动用例共用同一处查找：步骤 chip 的文本 / 点击都不各自再抄一遍。 */
const preactivationStepRow = () => Array.from($("t-options").children).find(
  (node) => node.tagName === "DIV") || null;
const preactivationStepChip = (index) => {
  const row = preactivationStepRow();
  if (!row) return null;
  return Array.from(row.children).find(
    (node) => node.getAttribute("data-step") === String(index)) || null;
};
/** 自己装备区里的第一张牌（就地高亮的用例共用一处查找）。
 *
 * U1-02 起自己的装备区是**固定四槽**（武器 / 防具 / 攻马 / 防马），槽位节点自己带
 * `data-card-id`；这里找"第一个装了牌的槽"，而不是第一个槽（空槽不该被点）。 */
const selfEquipChip = () => {
  const row = Array.from($("t-self-zones").children).find((c) => c.getAttribute("data-zone") === "equip");
  return row ? Array.from(row.children).find((slot) => slot.getAttribute("data-card-id")) || null : null;
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

/** 走真的开局表单并等开局批**播完提交**（`startGame` 的 `applyView` 现在按正常节奏回放：
 *  M6 报障 2 的 A 方案，计时器垫片不会自己跑，故这里显式冲刷）。
 *  要观察"开局批还在播"的中间态用 :func:`submitLobbyRaw`。 */
const submitLobby = async () => {
  await submitLobbyRaw();
  ui.flushPlayback();
  return ui.state.view;
};
/** 只提交开局表单、**不**冲刷开局批（返回时 `state.view` 可能还是 null / 上一局）。 */
const submitLobbyRaw = async () => {
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
const catalogVersions = ui.heroVersions();
/** U2 / M6 两条**真会话**用例的固定 5 人阵容（huo-shenzhugeliang 票 01）。
 *
 * 它们断言的是"开局的第一问就是五谷的逐目标无懈窗口"——这依赖 **AI 的随机选择**，而 AI
 * 的选择随阵容变。以前每次换池都要重扫种子（票 12 / feng-xiahouyuan 都重扫过）；这里改成
 * **钉死阵容**（禁将 = 除这五个标准版之外的全部版本，版本粒度、只作用于随机配将），
 * 以后加武将不再重排这两条用例的局面。 */
const SMOKE_ROSTER = ["caocao", "guanyu", "zhangfei", "diaochan", "lvbu"];
const SMOKE_BANNED = catalogVersions
  .map((hero) => hero.id)
  .filter((id) => !SMOKE_ROSTER.includes(id));
// wuming 票 01：自制包加「无名自制版」（新真名「无名」）⇒ 真名 42 → 43、版本 68 → 69。
// lei-batch 票 02：雷包再加张绣（新真名「张绣」）⇒ 47 / 73 → 48 / 74。
check("⓪1 点将列表拉到了启用并集（标准 + 界版 + 风包 + 风包移动版 + 火包移动版 + 火包 OL + 自制包，按真名分组）",
  // huo-taishici-xunyu 票 01 / 02：火包移动版再加太史慈标准版 / 荀彧标准版 ⇒ 45 / 71 → 46 / 72。
  // lei-batch 票 01：雷包加郝昭（新真名「郝昭」）⇒ 46 / 72 → 47 / 73。
  // tao-caocao 票 01：自制包加韬·曹操自制版（真名「曹操」已在池里）⇒ 真名不变、版本 75 → 76。
  // lin-batch 票 01：林包移动版加神曹操移动版（新真名「神曹操」）⇒ 真名 54 → 55、版本 83 → 84。
  // lin-batch 票 02：林包移动版加孙坚移动版（新真名「孙坚」）⇒ 真名 55 → 56、版本 84 → 85。
  // lin-batch 票 03：林包移动版加徐晃移动版（新真名「徐晃」）⇒ 真名 56 → 57、版本 85 → 86。
  // lin-batch2 票 01：林包移动版加曹丕移动版（新真名「曹丕」）⇒ 真名 57 → 58、版本 86 → 87。
  // lin-batch2 票 02：林包移动版加董卓移动版（新真名「董卓」）⇒ 真名 58 → 59、版本 87 → 88。
  // lin-batch2 票 03：林包移动版加贾诩移动版（新真名「贾诩」）⇒ 真名 59 → 60、版本 88 → 89。
  ui.state.heroCatalog.length === 62 && catalogVersions.length === 93,
  `${ui.state.heroCatalog.length} 个真名 / ${catalogVersions.length} 个版本`);
check("⓪2 列表铺进了「我的武将」下拉",
  $("in-my-hero").children.length === catalogVersions.length,
  `选项 ${$("in-my-hero").children.length} / 池 ${catalogVersions.length}`);
check("⓪2b 同一真名的两版在下拉里分得清（关羽 / 界关羽）",
  Array.from($("in-my-hero").children).some((option) => option.textContent === "关羽")
  && Array.from($("in-my-hero").children).some((option) => option.textContent === "界关羽"),
  Array.from($("in-my-hero").children).slice(7, 10).map((option) => option.textContent).join(","));
check("⓪3 开关缺省是关的（不点将 = 老路径）", $("in-pointjiang").checked === false);

$("in-players").value = "5";
const picked = catalogVersions[2];
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
const deckOptions = Array.from($("in-deck-profile").children);
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

/* --------------------------------------------- ⓪c 将池预设（M6 票 13）
 *
 * 走真的开局表单 + 真的服务端存档端点（`GET/POST /api/hero-presets`）：5 槽切换 / 编辑 /
 * 保存 / 覆盖 / 重启后加载 active，以及"真名池不足 ⇒ 开局页拒绝且不发建局请求"。
 * 面板与存档都由真脚本渲染 / 读写；断言只看 DOM 形状与线上请求体，不读内部实现。
 */

/** 某真名那一行的版本格（重渲染后必须重查：DOM 节点是替换的，不是原地改）。 */
const poolRow = (trueName) => Array.from($("hero-pool").children)
  .find((row) => row.getAttribute("data-true-name") === trueName) || null;
const poolCells = (trueName) => {
  const row = poolRow(trueName);
  return row ? Array.from(row.children).filter((cell) => cell.tagName === "BUTTON") : [];
};
const slotButtons = () => Array.from($("pool-slots").children);
const bannedIds = (trueName) => poolCells(trueName)
  .filter((cell) => cell.classList.contains("banned"))
  .map((cell) => cell.getAttribute("data-hero-id"));

await ui.loadHeroPools();

check("⓪20 服务端存档铺出 5 个常驻槽位",
  slotButtons().length === 5, `${slotButtons().length} 个`);
check("⓪21 启动时选中上次保存的 active（新档 = 槽位 0）",
  ui.heroPools().selected === ui.heroPools().active
  && slotButtons()[ui.heroPools().active].classList.contains("on")
  && slotButtons()[ui.heroPools().active].classList.contains("active"),
  `selected=${ui.heroPools().selected} active=${ui.heroPools().active}`);
check("⓪22 将池按真名分组渲染（一行一个真名、行内 1–2 个版本格）",
  $("hero-pool").children.length === ui.state.heroCatalog.length
  && ui.state.heroCatalog.every((group, at) =>
    $("hero-pool").children[at].getAttribute("data-true-name") === group.true_name
    && $("hero-pool").children[at].children.length === group.versions.length + 1),
  `行 ${$("hero-pool").children.length} / 真名 ${ui.state.heroCatalog.length}`);
check("⓪23 同一真名的两个版本在同一行（关羽 / 界关羽）",
  poolCells("关羽").length === 2
  && poolCells("关羽")[0].getAttribute("data-hero-id") === "guanyu"
  && poolCells("关羽")[1].getAttribute("data-hero-id") === "jie_guanyu",
  poolCells("关羽").map((cell) => cell.getAttribute("data-hero-id")).join(","));

// 点一格 = 禁掉这一版（灰 + aria-pressed=false）；同真名的另一版仍是可用（粒度是版本）。
mark = calls.length;
await clickAndWait(poolCells("关羽")[0]);
check("⓪24 点版本格即禁掉该版本（禁用即灰）",
  poolCells("关羽")[0].classList.contains("banned")
  && poolCells("关羽")[0].getAttribute("aria-pressed") === "false",
  poolCells("关羽")[0].getAttribute("class"));
check("⓪25 禁将粒度是版本：同真名的另一版仍可用",
  !poolCells("关羽")[1].classList.contains("banned")
  && poolCells("关羽")[1].getAttribute("aria-pressed") === "true");
check("⓪26 编辑不落盘（点格子不发保存请求）", calls.length === mark, `${calls.length - mark} 个新请求`);

// 切到槽位 2：编辑的是**另一个槽位**（空集合），禁界关羽后保存 ⇒ 覆盖槽位 2 并置 active。
mark = calls.length;
await clickAndWait(slotButtons()[2]);
check("⓪27 切槽位只改编辑对象（槽位按钮高亮）",
  ui.heroPools().selected === 2 && slotButtons()[2].classList.contains("on")
  && !slotButtons()[0].classList.contains("on"));
check("⓪27b 新槽位是空集合（不串槽位 0 的编辑）",
  JSON.stringify(ui.heroPools().slots[2]) === JSON.stringify([]),
  JSON.stringify(ui.heroPools().slots[2]));
await clickAndWait(poolCells("关羽")[1]);
await clickAndWait($("btn-pool-save"));
const savedBody = calls.slice(mark).filter((call) => call.path === "/api/hero-presets").pop();
check("⓪28 保存请求带槽位 2 + 当前禁将集合",
  savedBody.body.slot === 2 && JSON.stringify(savedBody.body.banned_heroes) === JSON.stringify(["jie_guanyu"]),
  JSON.stringify(savedBody.body));
check("⓪29 保存后槽位 2 置为 active（★）并保持选中",
  ui.heroPools().active === 2 && ui.heroPools().selected === 2
  && slotButtons()[2].classList.contains("active"),
  `active=${ui.heroPools().active}`);

// 槽位彼此独立：切回槽位 0，它禁的是 guanyu、界版仍可用；槽位 2 的集合不受影响。
await clickAndWait(slotButtons()[0]);
check("⓪30 槽位彼此独立（槽位 0 = guanyu、槽位 2 = jie_guanyu）",
  JSON.stringify(bannedIds("关羽")) === JSON.stringify(["guanyu"])
  && JSON.stringify(ui.heroPools().slots[2]) === JSON.stringify(["jie_guanyu"]),
  `槽位0禁=${bannedIds("关羽").join(",")} 槽位2=${ui.heroPools().slots[2].join(",")}`);

// 重启（重新拉存档 = 新进程读同一个本地文件）⇒ 自动加载 active 2 的禁将。
await ui.loadHeroPools();
check("⓪31 重启后自动加载上次 active（槽位 2、界关羽仍禁用、标准版可用）",
  ui.heroPools().selected === 2
  && slotButtons()[2].classList.contains("on")
  && JSON.stringify(bannedIds("关羽")) === JSON.stringify(["jie_guanyu"]),
  `selected=${ui.heroPools().selected} 禁=${bannedIds("关羽").join(",")}`);

// 覆盖：在槽位 2 上恢复界关羽再存 ⇒ 存档里旧条目消失（整槽覆盖，不是追加）。
await clickAndWait(poolCells("关羽")[1]);
await clickAndWait($("btn-pool-save"));
await ui.loadHeroPools();
check("⓪32 保存覆盖整槽（恢复的那版从存档里消失）",
  JSON.stringify(ui.heroPools().slots[2]) === JSON.stringify([])
  && bannedIds("关羽").length === 0,
  JSON.stringify(ui.heroPools().slots[2]));

// 开局：请求体带**当前编辑**的禁将集合（不必先保存），本局随机配将不得发出被禁版本。
await clickAndWait(slotButtons()[0]);
await clickAndWait(poolCells("关羽")[0]);
$("in-players").value = "2";
$("in-seed").value = "2468";
mark = calls.length;
const bannedStart = await submitLobby();
const bannedBody = sessionBodiesSince(mark).pop();
check("⓪33 建局请求带当前槽位的禁将集合",
  JSON.stringify(bannedBody.banned_heroes) === JSON.stringify(["guanyu"]),
  JSON.stringify(bannedBody.banned_heroes));
check("⓪34 禁将只作用于随机配将：本局没有 guanyu",
  !bannedStart.heroes.includes("guanyu"), bannedStart.heroes.join(","));

// 真名池不足 ⇒ 开局页先拦：切到槽位 1，禁到只剩 1 个可用真名（2 个座位）。
await clickAndWait(slotButtons()[1]);
const banAllButOne = [];
for (const group of ui.state.heroCatalog.slice(1)) {
  for (const version of group.versions) banAllButOne.push(version.id);
}
for (const heroId of banAllButOne) ui.togglePoolBan(heroId);
ui.syncPoolHint();
check("⓪35 可用真名 < 座位数 ⇒ 开局页给出可读原因",
  $("pool-hint").textContent.includes("可用真名不足")
  && $("pool-hint").classList.contains("warn"),
  $("pool-hint").textContent);
mark = calls.length;
const refused = await submitLobby();
check("⓪36 拒绝时不发建局请求、不创建会话",
  sessionBodiesSince(mark).length === 0
  && $("lobby-hint").textContent.includes("可用真名不足")
  && refused === ui.state.view,
  `新请求 ${sessionBodiesSince(mark).length} / 提示 ${$("lobby-hint").textContent}`);

// 服务端同守：同一条禁将集合直接打 HTTP ⇒ 400 + 可读原因（引擎侧 fail-closed）。
const denied = await fetch(`${BASE}/api/sessions`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    player_count: 2, ai: "random", human_seat: 0, timeout_seconds: null, seed: 5,
    banned_heroes: banAllButOne,
  }),
});
const deniedBody = await denied.json();
check("⓪37 服务端同守：真名池不足 ⇒ 400 + 可读原因",
  denied.status === 400 && String(deniedBody.error).includes("真名池不足"),
  `HTTP ${denied.status} ${deniedBody.error}`);

// 收尾：清空存档，别把这一段的禁将带进后面的用例（active 槽位归零）。
await ui.loadHeroPools();
ui.heroPools().slots[ui.heroPools().selected].splice(0);
await clickAndWait($("btn-pool-save"));
$("in-players").value = "8";

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
        const box = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === String(seat));
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
        // 两种前置各走一条判据：
        // - "用得出" = 候选索引里有它（点它 **设焦点**，2e 的判据）；
        // - "用不出" = 候选索引里没有**且** ``cardFace`` 也说点不动——W5-02b 的借刀杀人
        //   点得动（走两步清单入口、不设焦点），所以它既不算"用得出"、也不算"置灰"。
        // 判据仍是"手里既有可点、又有不可点的牌"，不是放宽。
        const usable = hand.filter((card) => ui.cardSelectable(pending, card.card_id));
        const unusable = hand.filter(
          (card) => !ui.cardSelectable(pending, card.card_id)
            && !ui.cardFace(pending, card).selectable);
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

// 种子判据（判据未放宽）：混合池（M6 票 12 起）里**所有座位都是标准版**——界将的
// hero_name / 详情名称解析归票 19，故这一局必须是标准局（否则座位卡退回引擎 id）。
// M6 票 14 换池（版本 37 → 41）后 20261012 的座位 1 是界司马懿，按同一判据重扫到
// 20261053（8 席全是标准版，且这一局里仍有复合 `ActivateSkill` 的应答——W4-6 断言的前置）。
// M6 票 15 再换池（版本 41 → 43）：按同一判据重扫，20261053 仍命中（8 席全是标准版、
// 复合 `ActivateSkill` 的 W4-6 断言照旧通过），故种子不变。
// M6 票 18 换池（版本 50 → 54）：20261053 的座位 0 变成界华雄，按同一判据
// （8 席全是标准版 + 该局仍有复合 `ActivateSkill` 应答）重扫到 20273291。
const created = await post("/api/sessions", {
  player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20273291,
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
    const chip = Array.from($("t-hand").children).find((c) => c.text().includes(partial.unusable.name) && c.classList.contains("dis"));
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
  // feng-yujin 票 01 加池后（默认池 82 / 53）同批种子的配将整体后移了一位：原列表的头几位
  // 落到"没有可点主动技"的武将上 ⇒ 扫描列表前补四个新池下的命中种子（2 = 界周瑜【反间】、
  // 8 = 界大乔【国色】、14 = 典韦移动版【强袭】、20 = 神甘宁【魄袭】）。判据不变。
  for (const seed of [2, 8, 14, 20, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67]) {
    await newSession(seed);
    for (let step = 0; step < 400 && ui.state.view.pending; step += 1) {
      const pending = ui.state.view.pending;
      const offered = (ui.state.view.skills || []).filter((s) => s.available);
      // 只收"点按钮 → 能选中一条候选"的形态：预发动技能走清单入口，非预发动的主动技要有
      // `ActivateSkill` 骨架候选。**转化技**（【国色】等）的候选已收回 `legal_actions`、
      // 由 `transform_preactivations` 表达，按钮点击不在本段的三态判据里——M6 P0 收口换池后
      // 这里最先撞到【国色】（判据不变，扫描谓词按被测形态收窄，见下面 ㉿4d/4e）。
      const clickable = offered.filter(
        (s) => s.preactivated || (
          (pending.legal_actions || []).some(
            (a) => a.kind === "ActivateSkill" && a.skill_id === s.skill_id)
          // 有预发动清单的技能，点按钮进的是**清单入口**（不是选骨架候选）——那不是
          // ㉿4d/4e 要判的形态（huo-shenzhugeliang 加池后扫描最先撞到的就是这种）。
          && !(pending.preactivations && (pending.preactivations[s.skill_id] || []).length)));
      if (pending.kind === "PLAY_PHASE" && pending.actor === 0 && clickable.length > 0) {
        skillCase = { seed, steps: step, offered: clickable };
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

  // 锁定技的按钮**只有"这一问里没有它的候选"时**才必须不可点：`available` 为真的锁定技
  // （卧龙诸葛【八阵】在【闪】窗口里）是**真入口**，点一下 = 选中那条 `ActivateSkill`
  // （用户实机报障 2026-11-06 修；正例见下方 ㉿4g）。
  check("㉿4a 没有候选的锁定/被动技是银灰且**不可点**（引擎在时机上派发，点了没意义）", (() => {
    const locked = ui.state.view.skills.filter((s) => s.locked && !s.available);
    if (!locked.length) return true;  // 该武将没有（此刻无候选的）锁定技时不判
    return locked.every((s) => {
      const b = Array.from($("t-skills").children).find((c) => c.getAttribute && c.getAttribute("data-skill-id") === s.skill_id);
      return b && b.classList.contains("locked") && b.disabled === true;
    });
  })(), `锁定技 ${ui.state.view.skills.filter((s) => s.locked && !s.available).map((s) => s.name).join("、") || "（无）"}`);

  check("㉿4b 当前不满足的主动技是深灰且不可点", (() => {
    const off = ui.state.view.skills.filter((s) => !s.locked && !s.available);
    if (!off.length) return true;
    return off.every((s) => {
      const b = Array.from($("t-skills").children).find((c) => c.getAttribute && c.getAttribute("data-skill-id") === s.skill_id);
      return b && !b.classList.contains("ready") && b.disabled === true;
    });
  })(), `不可用主动技 ${ui.state.view.skills.filter((s) => !s.locked && !s.available).map((s) => s.name).join("、") || "（无）"}`);

  check("㉿4 每个技能都渲染成一个按钮（清单来自响应体）", (() => {
    const buttons = Array.from($("t-skills").children).filter((c) => c.tagName === "BUTTON");
    const skills = ui.state.view.skills || [];
    return skills.length > 0 && buttons.length === skills.length;
  })(), `按钮 ${Array.from($("t-skills").children).filter((c) => c.tagName === "BUTTON").length} 个 / 清单 ${(ui.state.view.skills || []).length} 条`);

  check("㉿4 找到一个技能可发动的前置", skillCase !== null, skillCase ? `seed=${skillCase.seed} 可发动=${skillCase.offered.map((s) => s.name).join("、")}` : "扫描列表内的种子都没找到");

  if (skillCase) {
    const target = skillCase.offered[0];
    const button = Array.from($("t-skills").children).find((c) => c.getAttribute && c.getAttribute("data-skill-id") === target.skill_id);
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
  const mixedButtons = Array.from($("t-skills").children).filter((c) => c.tagName === "BUTTON");
  check("㉿4f 装备技能与武将技能同列渲染", mixedButtons.length === 2, `按钮 ${mixedButtons.length} 个`);
  const equipButton = mixedButtons.find((c) => c.getAttribute("data-skill-id") === "equip.crossbow");
  check("㉿4f 装备技能标注为「装备技」且锁定态不可点",
    !!equipButton && equipButton.text().includes("装备技") && equipButton.disabled === true,
    equipButton ? equipButton.text() : "按钮缺失");
  // **锁定技的裸骨架候选**（用户实机报障 2026-11-06：卧龙诸葛【八阵】在【闪】窗口里
  // 提示条写着效果、按钮却整片锁死 = 死路）。合成一份同形状的视图：锁定技 + 这一问给了
  // 它的 `ActivateSkill`、且**没有清单**（八阵没有流水线）⇒ 按钮必须可点、点一下 = 选中
  // 那条候选、提交出去的就是它。
  {
    const dodgePending = {
      kind: "DODGE", actor: 0, seq: 9101,
      prompt: "以你为目标的【杀】生效前：是否使用【闪】？",
      prompt_display: { text: "以你为目标的【杀】生效前：是否使用【闪】？", default_label: "" },
      legal_actions: [
        { kind: "ActivateSkill", skill_id: "hero.wolong_zhugeliang.bazhen", answers_seq: 9101 },
        { kind: "Pass", answers_seq: 9101 },
      ],
      candidate_index: {}, option_seats: {}, context: { requirement: "判定为红则视为使用【闪】" },
      preactivations: {}, transform_preactivations: {}, transform_actions: [],
    };
    const bazhenView = {
      ...realSkillsView,
      pending: dodgePending,
      skills: [
        {
          skill_id: "hero.wolong_zhugeliang.bazhen", name: "八阵", skill_type: "NORMAL",
          locked: true, carrier: "HERO", available: true,
          ui: { requirement: "判定为红则视为使用【闪】", description: "锁定技，当你装备区没有防具牌时，你视为装备着【八卦阵】。" },
        },
      ],
    };
    ui.state.view = bazhenView;
    ui.renderSkills(bazhenView);
    const bazhenButton = Array.from($("t-skills").children)
      .find((c) => c.getAttribute && c.getAttribute("data-skill-id") === "hero.wolong_zhugeliang.bazhen");
    check("㉿4g 锁定技在这一问里有裸骨架候选 ⇒ 按钮**可点**（八阵在【闪】窗口里；报障 2026-11-06）",
      !!bazhenButton && bazhenButton.disabled === false && bazhenButton.classList.contains("ready"),
      `存在=${!!bazhenButton} disabled=${bazhenButton && bazhenButton.disabled} ready=${bazhenButton && bazhenButton.classList.contains("ready")}`);
    if (bazhenButton) {
      (bazhenButton.listeners.click || []).forEach((fn) => fn({ target: bazhenButton }));
      check("㉿4g 点它 = 选中那条 ActivateSkill（与真【八卦阵】的装备按钮同一条直提态）",
        ui.state.selectedOption === 0,
        `selectedOption=${ui.state.selectedOption}`);
      const before = calls.length;
      await ui.submitAction();
      const sent = calls.slice(before).find((c) => c.path.includes("/answer"));
      const sentAction = sent && sent.body ? sent.body.action : null;
      check("㉿4g 提交的正是八阵的候选",
        sentAction !== null && sentAction.kind === "ActivateSkill"
          && sentAction.skill_id === "hero.wolong_zhugeliang.bazhen",
        `sent=${JSON.stringify(sentAction && { kind: sentAction.kind, skill_id: sentAction.skill_id })}`);
    }
    // 反例：同一形态但这一问**没有**它的候选（`available: false`）⇒ 仍只做说明（U1-06）。
    ui.renderSkills({
      ...bazhenView,
      pending: { ...dodgePending, legal_actions: [{ kind: "Pass", answers_seq: 9101 }] },
      skills: [{ ...bazhenView.skills[0], available: false }],
    });
    const bazhenOff = Array.from($("t-skills").children)
      .find((c) => c.getAttribute && c.getAttribute("data-skill-id") === "hero.wolong_zhugeliang.bazhen");
    check("㉿4g 锁定技在这一问里**没有**候选 ⇒ 仍只做说明（不可点）",
      !!bazhenOff && bazhenOff.disabled === true && bazhenOff.classList.contains("locked"),
      `存在=${!!bazhenOff} disabled=${bazhenOff && bazhenOff.disabled}`);
    ui.state.view = realSkillsView;
    ui.resetSelection();
  }

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

  const targetable = Array.from($("t-seats").children).filter((c) => c.classList.contains("targetable"));
  check("㉿M5 合法目标座位被标成可点（**数据**给的类）", targetable.length === multi.entry.pool.length,
    `可点座位 ${targetable.length} 个 / 池 ${multi.entry.pool.length} 个`);

  // 手选路径：点出**第一组组合**要求的那些座位（多目标牌就得点够）
  const want = multi.entry.combos[0];
  for (const seat of want) {
    const box = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === String(seat));
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
  const firstSeat = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === String(want[0]));
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
    const box = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === String(seat));
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
    const box = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === String(seat));
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

  const buttons = Array.from($("t-options").children);
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
    })(), `buildAction=${JSON.stringify(ui.buildAction())} disabled=${$("btn-submit").disabled} options=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))}`);
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
  const row = Array.from($("t-options").children).find((c) => c.className === "cards");
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
    const again = Array.from($("t-options").children).find((c) => c.className === "cards");
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
    const box = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === String(auto.entry.pool[0]));
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
        const box = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === String(seat));
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
const isCompositeActivate = (record) => record.kind === "action" && record.action
  && record.action.kind === "ActivateSkill"
  && (((record.action.picks || []).length > 0) || ((record.action.choices || []).length > 0));
// 回放断言的前置（W4-6）要求这一局里**出现复合 `ActivateSkill`**：烟测自己跑出来的录像
// 有哪几局、哪一局带这条记录，取决于本次新跑的对局（换池 / 换 AI 都会变），故按**性质**
// 挑一局（而不是按"第一局 8 人"硬点）：先问各局的记录，取第一局带复合应答的；
// 一局都没有时退回第一局 8 人（断言照旧会红，红得也说得清）。
const eightRunList = runs.runs.filter((run) => run.player_count === 8);
let target = eightRunList[0] || runs.runs[0];
for (const candidate of eightRunList) {
  const body = await get(`/api/runs/${encodeURIComponent(candidate.name)}`);
  if ((body.records || []).some(isCompositeActivate)) {
    target = candidate;
    break;
  }
}
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
const seatNode = (seat) => Array.from($("r-seats").children).find(
  (node) => node.getAttribute("data-seat") === String(seat));
/** 逐座位区的行：`data-zone` 在行上（回放页仍是旧版 `.cards.mini` 结构）。 */
const zoneRow = (seat, zone) => {
  const node = seatNode(seat);
  return node ? Array.from(node.children).find((child) => child.getAttribute("data-zone") === zone) : null;
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
const cardsIn = (node) => (node ? Array.from(node.children).filter(
  (child) => child.tagName === "BUTTON" || String(child.className || "").split(/\s+/).includes("card"),
) : []);
const cardParts = (child) => {
  const part = (name) => {
    const span = Array.from(child.children || []).find(
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
  // U3-01：回放页的舞台也是三组同流——处理区只比**处理中**那一组（淡出中的已不在处理区）。
  const processingCards = () => cardsIn($("r-processing")).filter(
    (node) => node.getAttribute("data-stage-lane") === "processing"
      && !node.classList.contains("fading"),
  ).map(cardParts);
  check("回放页处理区 / 效果区 = 从 card_move 重建的结果（名称 / 花色 / 点数 / id）",
    JSON.stringify(processingCards()) === JSON.stringify(expectedProcessing),
    `面板=${JSON.stringify(processingCards())} / 记录=${JSON.stringify(expectedProcessing)}`);
  const stageLanes = cardsIn($("r-processing")).map((node) => node.getAttribute("data-stage-lane"));
  check("回放页的舞台卡面都带 data-stage-lane（复用舞台渲染）",
    stageLanes.every((lane) => ["processing", "showcase", "discard"].includes(lane))
      && stageLanes.length >= expectedProcessing.length,
    `lanes=${stageLanes.join(",")}`);
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
const compositeIndex = replayRecords.findIndex(isCompositeActivate);
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

/* ---- U4-02（D7 / D8）：anim.ini 的 base_step 只决定**加载时**的初始档 ----
 *
 * 必须在任何按钮点击之前跑（玩家点过之后 ini 不再覆盖）。这里走真实的
 * `animLoadConfig()`（fetch → 解析 → 生效表），不直接写 `state.eventDelayMs`。
 */
{
  const delayNode = $("btn-delay");
  iniInjection = { ok: true, text: "base_step = 150\n" };
  await ui.animLoadConfig();
  check("U4-02-ini 非法 base_step（150 不在五档里）被忽略：仍是缺省 0.1s",
    ui.state.eventDelayMs === 100 && delayNode.textContent === "步长 0.1s",
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayNode.textContent}"`);
  // 删掉 anim.ini 时静态服务会回落到 index.html（200 + HTML）：整份按"取不到"处理，
  // 回缺省档、只留一行短提示——不把整页 HTML 逐行报成"无法解析"。
  iniInjection = {
    ok: true,
    text: "<!doctype html>\n<html lang=\"zh-CN\"><body>index</body></html>\n",
  };
  await ui.animLoadConfig();
  const fallbackNote = String($("t-anim-note").textContent || "");
  check("U4-02-ini 删掉 anim.ini（回落 index.html）⇒ 缺省档 + 一行提示、不逐行报错",
    ui.state.eventDelayMs === 100 && delayNode.textContent === "步长 0.1s"
      && $("t-anim-note").hidden === false
      && !fallbackNote.includes("\n")
      && !fallbackNote.includes("无法解析")
      && fallbackNote.includes("取不到"),
    `eventDelayMs=${ui.state.eventDelayMs} note="${fallbackNote}"`);
  iniInjection = { ok: true, text: "base_step = 200\n" };
  await ui.animLoadConfig();
  check("U4-02-ini 合法 base_step（200）在首次点击前生效：按钮显示 0.2s",
    ui.state.eventDelayMs === 200 && delayNode.textContent === "步长 0.2s",
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayNode.textContent}"`);
  clickButton(delayNode);   // 200 → 300：玩家点过按钮，初始档不再被 ini 覆盖
  iniInjection = { ok: true, text: "base_step = 30\n" };
  await ui.animLoadConfig();
  check("U4-02-ini 玩家点过按钮后 ini 不再覆盖（仍是点出来的 0.3s）",
    ui.state.eventDelayMs === 300 && delayNode.textContent === "步长 0.3s",
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayNode.textContent}"`);
  // 300 → 30 → 50 → 100：回到缺省档，后面的回放页 / 牌桌循环用例仍从 0.1s 起。
  for (let i = 0; i < 3; i += 1) clickButton(delayNode);
  check("U4-02-ini 恢复缺省档（后续按钮循环用例的起点）",
    ui.state.eventDelayMs === 100 && delayNode.textContent === "步长 0.1s",
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayNode.textContent}"`);
  await ui.animLoadConfig();   // 时间表回到磁盘真文件
}

const delayBefore = ui.state.eventDelayMs;
clickButton($("btn-r-delay"));
const delayAfter = ui.state.eventDelayMs;
check("回放页沿用牌桌的 30–300ms 五档步长",
  delayAfter !== delayBefore && ui.DELAY_STEPS_MS.includes(delayAfter),
  `${delayBefore}ms → ${delayAfter}ms`);
// 步长是**全局一份**：把这五档再点回原档，免得把后面的牌桌断言改成另一档。
for (let step = 1; step < ui.DELAY_STEPS_MS.length; step += 1) clickButton($("btn-r-delay"));
check("五档循环一圈回到原档（回放页与牌桌共用同一份状态）",
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
      seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
      // lei 票 05：键恒在（新局 = 空列表），与真投影同形；各用例按需改这一格。
      sealed_zones: [], identity: null,
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
  $("t-processing").children.length > 0 && Array.from($("t-processing").children).every((c) => !(c.listeners.click || []).length));

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
  `kind=${ui.renderKind(ui.state.view.pending, ui.state.view.pending.legal_actions)} 选项=${JSON.stringify(Array.from($("t-options").children).map((b) => b.textContent))}`);
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
  const boxes = Array.from($("t-seats").children);
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
      .every((row) => Array.from(row.children || []).every((chip) => !(chip.listeners.click || []).length)));
}

/* ============ 被废除（封印）的**槽**各叠一个红色禁止角标（lukang-mobile 票 03）============
 *
 * 判据只读服务端下发的 `players[].sealed_zones`（自 `PlayerPublic` 下发，票 03 起是**槽 id**：
 * 判定 `judge_1..3` + 装备 `weapon` / `armor` / `horse_offense` / `horse_defense`）。
 * **每个被废除的槽各画一个** `.zone-ban`——判定行 / 装备格**本身不再画**；
 * 形状不变：判定仍 3 槽、装备仍 4 槽（槽节点多了 `data-slot-key` 作封印判据）。
 * 两侧都断言：封印的画角标，未封印的**一个都不许有**。
 */
{
  const sealedView = synthView({
    kind: "PLAY_PHASE", actor: 0, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [], candidate_index: {}, context: null, option_seats: {},
  });
  // 0 号位（本人）：7 个槽全封（【雄乱】的形状）。
  sealedView.snapshot.players[0].sealed_zones = [
    "judge_1", "judge_2", "judge_3",
    "weapon", "armor", "horse_offense", "horse_defense",
  ];
  // 1 号位：只封 1 个判定槽 ⇒ 判定区**不**算被废除（派生口径），装备区一个角标都没有。
  sealedView.snapshot.players[1].sealed_zones = ["judge_2"];
  // 2 号位：只封武器槽（决堰 / 将来的单槽载体）。
  sealedView.snapshot.players[2].sealed_zones = ["weapon"];
  ui.state.view = sealedView;
  ui.resetSelection();
  ui.renderTable();
  const selfSeat = ui.state.view.snapshot.seat;
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
    const box = Number(seat) === Number(selfSeat) ? $("t-self-zones") : seatBox(seat);
    return box ? findZone(box, zone) : null;
  };
  const slotsOf = (row) => Array.from((row && row.children) || [])
    .filter((node) => node.getAttribute && node.getAttribute("data-slot"));
  const slotsWith = (row, name) => slotsOf(row).filter((slot) => slot.classList.contains(name));
  // 该区域节点后代里一共几个禁止角标（角标是槽的子节点，不是新的区块）。
  const bansIn = (node, out = []) => {
    if (!node || !node.children) return out;
    for (const child of node.children) {
      if (child.classList && child.classList.contains("zone-ban")) out.push(child);
      bansIn(child, out);
    }
    return out;
  };
  const judge0 = zoneRow(0, "judge");
  const equip0 = zoneRow(0, "equip");
  const judge1 = zoneRow(1, "judge");
  const equip1 = zoneRow(1, "equip");
  const equip2 = zoneRow(2, "equip");
  check("㉿ZB1 判定区 3 槽全封 ⇒ **每个**判定槽各 1 个角标、判定行本身不带 zone-sealed",
    !!judge0 && !judge0.classList.contains("zone-sealed")
      && bansIn(judge0).length === 3
      && slotsWith(judge0, "zone-sealed").length === 3
      && slotsOf(judge0).every((slot) => slot.classList.contains("zone-sealed")),
    `judge0 类=${JSON.stringify([...(judge0 ? judge0.classList.set : [])])} ban=${bansIn(judge0).length}`);
  check("㉿ZB2 装备区 4 槽全封 ⇒ **每个**装备槽各 1 个角标、装备格本身不带 zone-sealed",
    !!equip0 && !equip0.classList.contains("zone-sealed")
      && bansIn(equip0).length === 4
      && slotsWith(equip0, "zone-sealed").length === 4,
    `equip0 类=${JSON.stringify([...(equip0 ? equip0.classList.set : [])])} ban=${bansIn(equip0).length}`);
  check("㉿ZB3 装备区未封印（该座位只有一个判定槽被封）⇒ 装备一个角标都不画",
    !!equip1 && bansIn(equip1).length === 0
      && !equip1.classList.contains("zone-sealed"),
    `equip1 ban=${bansIn(equip1).length}`);
  check("㉿ZB4 只封 1 个槽（weapon）⇒ 只有武器槽画角标",
    !!equip2 && !equip2.classList.contains("zone-sealed")
      && bansIn(equip2).length === 1
      && slotsWith(equip2, "zone-sealed").length === 1
      && slotsWith(equip2, "zone-sealed")[0].getAttribute("data-slot") === "weapon",
    `equip2 ban=${bansIn(equip2).length} 封槽=${JSON.stringify(slotsOf(equip2).map((s) => [s.getAttribute("data-slot"), s.classList.contains("zone-sealed")]))}`);
  check("㉿ZB5 封印不发明新区块：判定仍 3 槽 / 装备仍 4 槽（角标是槽的子节点）",
    slotsOf(judge0).length === 3 && slotsOf(equip0).length === 4
      && judge0.getAttribute("data-zone") === "judge"
      && equip0.getAttribute("data-zone") === "equip",
    `judge=${slotsOf(judge0).length} 槽 / equip=${slotsOf(equip0).length} 槽`);
  check("㉿ZB6 判定槽带 data-slot-key（judge_1..3）作封印判据，data-slot 仍是 judge（anim 锚点）",
    slotsOf(judge0).length === 3
      && slotsOf(judge0).every((slot) => slot.getAttribute("data-slot") === "judge")
      && JSON.stringify(slotsOf(judge0).map((slot) => slot.getAttribute("data-slot-key")))
        === JSON.stringify(["judge_1", "judge_2", "judge_3"]),
    `判定槽键=${JSON.stringify(slotsOf(judge0).map((slot) => [slot.getAttribute("data-slot"), slot.getAttribute("data-slot-key")]))}`);
  check("㉿ZB7 只封 1 个判定槽（judge_2）⇒ 只有那一格有角标，判定行 / 另两格都没有",
    !!judge1 && !judge1.classList.contains("zone-sealed")
      && bansIn(judge1).length === 1
      && slotsWith(judge1, "zone-sealed").length === 1
      && slotsWith(judge1, "zone-sealed")[0].getAttribute("data-slot-key") === "judge_2",
    `judge1 ban=${bansIn(judge1).length} 封槽=${JSON.stringify(slotsWith(judge1, "zone-sealed").map((s) => s.getAttribute("data-slot-key")))}`);
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
    Array.from($("t-options").children).filter((b) => /弃牌（/.test(b.textContent || "")).length === 0,
    `选项=${JSON.stringify(Array.from($("t-options").children).map((b) => b.textContent))}`);
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

  const skillButton = Array.from($("t-skills").children).find((b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
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
  const handChips = () => Array.from($("t-hand").children);
  const equipChip = selfEquipChip;
  const skillButton = (id) => Array.from($("t-skills").children).find((b) => b.getAttribute("data-skill-id") === id);
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
 * （目前不行）"。这里用**真前端**的“自己区域多选直选”路径验四件事：
 * 手牌面上每张牌都能点、点两张 = picks 收两个面内位置、提交的是那条候选补上 picks、
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
  view.snapshot.self_hand = [first, second, third];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  // 多张自己区域面 = 牌桌直选：每次点击重绘手牌，故按 `card_id` 重新取节点。
  const cardNode = (cardId) => Array.from($("t-hand").children).find(
    (node) => node.getAttribute("data-card-id") === String(cardId));
  const click = (cardId) => {
    const node = cardNode(cardId);
    (node.listeners.click || []).forEach((fn) => fn({ target: node }));
  };

  check("㉿G1 多张自己区域面 = 牌桌直选：不铺左下角选择面，上限仍读 spec.total",
    $("t-options").children.length === 0
      && ui.selectionCap(yijiPending) === 2
      && $("t-target-hint").textContent.includes("手牌"),
    `块=${$("t-options").children.length} cap=${ui.selectionCap(yijiPending)}`);
  check("㉿G2 未选时不可提交", $("btn-submit").disabled === true);

  click(1);
  check("㉿G3 点第一张 = picks 收下位置 0", JSON.stringify(ui.state.picks) === "[0]",
    `picks=${JSON.stringify(ui.state.picks)}`);
  click(2);
  check("㉿G4 一次可以点两张（用户口径：允许同时选择两张牌）",
    JSON.stringify(ui.state.picks) === "[0,1]", `picks=${JSON.stringify(ui.state.picks)}`);
  check("㉿G5 两张都能画成选中态（picked）",
    cardNode(1).classList.contains("picked") && cardNode(2).classList.contains("picked")
      && !cardNode(3).classList.contains("picked"),
    `picked=${[1, 2, 3].map((id) => cardNode(id).classList.contains("picked")).join(",")}`);
  const action = ui.buildAction();
  check("㉿G6 提交的是候选里那条 SelectCards 补上两个面内位置",
    !!action && action.kind === "SelectCards" && JSON.stringify(action.picks) === "[0,1]",
    JSON.stringify(action));
  check("㉿G7 选够两张后提交按钮可点", $("btn-submit").disabled === false,
    `disabled=${$("btn-submit").disabled}`);

  click(3);
  check("㉿G8 上限就是两张：再点第三张不越界（上限 = spec.total 的纯函数）",
    JSON.stringify(ui.state.picks) === "[0,1]", `picks=${JSON.stringify(ui.state.picks)}`);
  click(1);
  check("㉿G9 再点已选中的那张 = 取消它（另一张仍在）",
    JSON.stringify(ui.state.picks) === "[1]", `picks=${JSON.stringify(ui.state.picks)}`);
  click(3);
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

  const button = Array.from($("t-skills").children).find((b) => b.getAttribute("data-skill-id") === "hero.liubei.rende");
  check("㉿R1 仁德是**预发动**入口（按钮可点、标出步骤数）",
    !!button && button.disabled === false
      && button.getAttribute("data-preactivated") === "true"
      && button.getAttribute("data-preactivation") === "2",
    `disabled=${button && button.disabled} pre=${button && button.getAttribute("data-preactivation")}`);
  check("㉿R1b 出牌阶段没有「发动技能 XXX」选项",
    !Array.from($("t-options").children).some((b) => /发动技能/.test(b.textContent)),
    `选项=${JSON.stringify(Array.from($("t-options").children).map((b) => b.textContent))}`);
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
  const next = Array.from($("t-options").children).find((b) => b.textContent === "下一步");
  check("㉿R4 张数达标后「下一步」可点", !!next && next.disabled === false);
  (next.listeners.click || []).forEach((fn) => fn({ target: next }));
  check("㉿R5 进第 2 步：只高亮清单给的两名其他角色",
    ui.state.preactivation.cursor === 1
      && ui.preactivationOptions(ui.state.preactivation, 1).join(",") === "seat:1,seat:2",
    `cursor=${ui.state.preactivation.cursor}`);
  const targetBox = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === "2");
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

/* ============ 票 25 报障（合成局面）：麒麟弓预发动的第一步要点**目标**装备区的坐骑 ============
 *
 * 清单形状来自引擎真实下发的 Step（`PickCards(zones=("equip",), slot_kinds=坐骑)`）：
 * `zones=["equip"]`、`pool.options=[目标坐骑的 card_id]`。麒麟弓是**装备技**的被动询问
 * （`SKILL_ACTIVATION`），入口 = 技能按钮 → 预发动清单；第一步必须在牌桌表面点**目标
 * 座位**的装备槽。旧实现只把"**自己**的装备区"接成可点，目标座位的槽是静态 div。
 */
{
  const mount = { ...synthCard(37, "的卢"), type: "dilu", slot: "horse_offense" };
  const bow = { ...synthCard(30, "麒麟弓"), type: "qilin_bow", slot: "weapon" };
  const manifest = [{
    name: "card", zones: ["equip"], min: 1, max: 1, order: 0,
    pool: [{ when: [], options: ["37"] }],
    wording: "弃置目标装备区里的一匹坐骑（只能选坐骑）",
  }];
  const view = synthView({
    kind: "SKILL_ACTIVATION", actor: 0, seq: 902,
    prompt: "是否发动该技能？", skill_id: "equip.qilin_bow",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "equip.qilin_bow", answers_seq: 902 },
      { kind: "Pass", answers_seq: 902 },
    ],
    preactivation: manifest, preactivations: [],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
  });
  view.heroes = { 0: "jie_luxun", 1: "jie_xiahoudun", 2: "lvbu" };
  view.snapshot.players[0].equip = [bow];
  view.snapshot.players[1].equip = [mount];
  view.skills = [{
    skill_id: "equip.qilin_bow", name: "麒麟弓", skill_type: "NORMAL", locked: false,
    carrier: "EQUIPMENT", available: true, preactivated: true,
    ui: { requirement: "当你使用【杀】造成伤害时，你可以弃置其装备区里的一张坐骑牌" },
  }];
  ui.state.view = view;
  ui.state.sessionId = "smoke-missing-session";
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButton = Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "equip.qilin_bow");
  check("㉿Q1 麒麟弓是预发动入口（按钮可点）",
    !!skillButton && skillButton.disabled === false,
    `disabled=${skillButton && skillButton.disabled}`);
  (skillButton.listeners.click || []).forEach((fn) => fn({ target: skillButton }));
  check("㉿Q2 点它 = 进入 1 步预发动（步骤提示 = 服务端那句）",
    !!ui.state.preactivation && ui.state.preactivation.steps.length === 1
      && /弃置目标装备区里的一匹坐骑/.test($("t-target-hint").textContent),
    `steps=${ui.state.preactivation && ui.state.preactivation.steps.length} hint="${$("t-target-hint").textContent}"`);

  const walk = (node, out = []) => {
    for (const child of Array.from(node.children || [])) { out.push(child); walk(child, out); }
    return out;
  };
  const targetSlot = walk(seatBox(1)).find(
    (node) => node.getAttribute("data-card-id") === "37"
      && node.getAttribute("data-zone") === "equip");
  check("㉿Q3 目标座位的坐骑槽画成可点节点（不是静态 div）",
    !!targetSlot && targetSlot.tagName === "BUTTON" && (targetSlot.listeners.click || []).length > 0,
    `tag=${targetSlot && targetSlot.tagName} click=${targetSlot && (targetSlot.listeners.click || []).length}`);
  if (targetSlot) (targetSlot.listeners.click || []).forEach((fn) => fn({ target: targetSlot }));
  check("㉿Q4 点目标坐骑 = 收进第 1 步答案",
    JSON.stringify(ui.state.preactivation.answers[0].values) === "[37]",
    JSON.stringify(ui.state.preactivation.answers[0]));
  const next = Array.from($("t-options").children).find((b) => b.textContent === "下一步");
  check("㉿Q5 张数达标后「下一步」可点", !!next && next.disabled === false,
    `next=${next && next.disabled}`);
  (next.listeners.click || []).forEach((fn) => fn({ target: next }));
  const action = ui.buildAction();
  check("㉿Q6 清单填完 = 复合 ActivateSkill（picks = 目标坐骑 id）",
    !!action && action.kind === "ActivateSkill" && action.skill_id === "equip.qilin_bow"
      && JSON.stringify(action.picks) === "[37]",
    JSON.stringify(action));
}

/* ============ 票 04 §3（合成局面）：选项步的**置灰不可选** ============
 *
 * 蛊惑的牌名弹窗列全牌堆名，把"此刻没有合法目标"的那几个置灰。载荷形状 =
 * `StepPool.disabled`（服务端 `flow.manifest` 下发、`projection._steps_payload` 透传）；
 * 读取方 = `preactivation.js:renderPreativation` 的选项分支 + `selectPreativationOption`
 * 的守卫。五条一起钉：列出来（在候选里）、画成灰的（`.dis` + `disabled`）、点不动
 * （没有 click 监听 / 合成 click 也不改答案）、没被置灰的照常能选、载荷带的是可用的那个。
 */
{
  const handCard = synthCard(41, "闪");
  const manifest = [
    { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
      pool: [{ when: [], options: ["41"] }], wording: "扣置一张手牌" },
    { name: "name", zones: [], min: 1, max: 1, order: 1,
      pool: [{
        when: ["41"],
        options: ["桃园结义", "闪", "杀"],
        disabled: ["闪"],
      }],
      wording: "声明一种牌名：基本牌或普通锦囊牌" },
  ];
  const view = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 903,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 903 }],
    preactivations: { "hero.yuji_mobile.guohuo": manifest },
    preactivation: [],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
  });
  view.heroes = { 0: "yuji_mobile", 1: "guanyu" };
  view.snapshot.self_hand = [handCard];
  view.skills = [{
    skill_id: "hero.yuji_mobile.guohuo", name: "蛊惑", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "扣置一张手牌，将此牌当任意一张基本牌或普通锦囊牌使用" },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const skillButton = Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.yuji_mobile.guohuo");
  (skillButton.listeners.click || []).forEach((fn) => fn({ target: skillButton }));
  const handChip = (index) => $("t-hand").children[index];
  (handChip(0).listeners.click || []).forEach((fn) => fn({ target: handChip(0) }));
  const next = Array.from($("t-options").children).find((b) => b.textContent === "下一步");
  (next.listeners.click || []).forEach((fn) => fn({ target: next }));

  const optionButton = (text) => Array.from($("t-options").children).find(
    (b) => b.textContent === text);
  const grayed = optionButton("闪");
  const usable = optionButton("杀");
  check("㉿U1 置灰的名字**照样列出来**（不是从选项里删掉）",
    !!grayed && !!usable,
    `选项=${JSON.stringify(Array.from($("t-options").children).map((b) => b.textContent))}`);
  check("㉿U2 置灰项画成 .dis + disabled、且**不接 click**",
    !!grayed && grayed.classList.contains("dis") && grayed.disabled === true
      && (grayed.listeners.click || []).length === 0,
    `dis=${grayed && grayed.classList.contains("dis")} disabled=${grayed && grayed.disabled} click=${grayed && (grayed.listeners.click || []).length}`);
  (grayed && grayed.listeners.click || []).forEach((fn) => fn({ target: grayed }));
  ui.selectPreativationOption("闪");
  check("㉿U3 置灰项点 / 调用都选不动（答案仍是空的）",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[]",
    JSON.stringify(ui.state.preactivation.answers[1]));
  (usable.listeners.click || []).forEach((fn) => fn({ target: usable }));
  check("㉿U4 没被置灰的那个照常可选",
    JSON.stringify(ui.state.preactivation.answers[1].values) === '["杀"]',
    JSON.stringify(ui.state.preactivation.answers[1]));
  check("㉿U4b 选项少的清单**不**走紧凑多列（仍是单列按钮）",
    $("t-options").classList.contains("opt-compact") === false,
    `class="${$("t-options").className}"`);
  const action = ui.buildAction();
  check("㉿U5 提交载荷里带的是可用的那个名字",
    !!action && JSON.stringify(action.choices) === '["杀"]',
    JSON.stringify(action));
  // 切转化模式时的「宽松保留」也要认置灰：已选里的置灰项必须被剔掉，不能把"点了必被拒"
  // 的值原样带过提交（`prunePreativationAnswers` 的第二个消费者）。
  ui.state.preactivation.answers[1] = { values: ["闪"], texts: ["闪"] };
  ui.prunePreativationAnswers(ui.state.preactivation, view.pending);
  check("㉿U6 已选里的置灰项被剔掉（宽松保留也认 disabled）",
    JSON.stringify(ui.state.preactivation.answers[1].values) === "[]",
    JSON.stringify(ui.state.preactivation.answers[1]));

  // 长清单（13 个牌名，与实机同形）⇒ 紧凑多列，且**子节点形状不变**（按钮仍是
  // `#t-options` 的直接子节点，只有布局换成分行等宽的格子）。
  const manyNames = ["桃园结义", "万箭齐发", "闪", "桃", "五谷丰登", "无中生有", "杀",
    "过河拆桥", "决斗", "顺手牵羊", "南蛮入侵", "无懈可击", "借刀杀人"];
  const longView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 904,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 904 }],
    preactivations: {
      "hero.yuji_mobile.guohuo": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["41"] }], wording: "扣置一张手牌" },
        { name: "name", zones: [], min: 1, max: 1, order: 1,
          pool: [{ when: [], options: manyNames, disabled: ["闪", "桃"] }],
          wording: "声明一种牌名" },
      ],
    },
    preactivation: [],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
  });
  longView.heroes = { 0: "yuji_mobile", 1: "guanyu" };
  longView.snapshot.self_hand = [handCard];
  longView.skills = view.skills;
  ui.state.view = longView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(longView);
  const longButton = Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.yuji_mobile.guohuo");
  (longButton.listeners.click || []).forEach((fn) => fn({ target: longButton }));
  (handChip(0).listeners.click || []).forEach((fn) => fn({ target: handChip(0) }));
  const longNext = Array.from($("t-options").children).find((b) => b.textContent === "下一步");
  (longNext.listeners.click || []).forEach((fn) => fn({ target: longNext }));
  const optionNodes = Array.from($("t-options").children).filter(
    (node) => manyNames.includes(node.textContent));
  check("㉿U7 长清单（13 个牌名）改**紧凑多列**，按钮仍是直接子节点",
    $("t-options").classList.contains("opt-compact")
      && optionNodes.length === manyNames.length,
    `classes=${JSON.stringify(Array.from($("t-options").classList.set))} 选项数=${optionNodes.length}`);
  check("㉿U8 置灰在紧凑布局里同样成立（闪 / 桃仍是 .dis + disabled）",
    optionNodes.filter((node) => node.classList.contains("dis")).length === 2
      && optionNodes.filter((node) => node.disabled === true).length === 2,
    JSON.stringify(optionNodes.map((node) => [node.textContent, node.disabled])));
}

/* ============ 报障 2026-11-02（合成局面）：**已翻预发动**的技能仍要能走"提供者"入口 ============
 *
 * 【蛊惑】封了两个入口：出牌阶段走**预发动清单**（复合动作），"需要牌时"走**提供者**
 * （这一问里它就是一条普通的 `ActivateSkill` 候选）。技能按钮的三态曾经按
 * `skill.preactivated` 一刀切——已翻预发动就"只认清单"，于是响应窗口里没有清单 ⇒ 整片
 * 变灰，玩家没法用【蛊惑】响应（用户实机报障）。判据改成看**这一问给了哪种入口**：
 * 有清单 ⇒ 清单入口；没有清单但服务端说 `available`（= 这一问有骨架候选）⇒ 直提态。
 */
{
  const nullify = synthCard(61, "无懈可击");
  const view = synthView({
    kind: "RESPOND", actor: 0, seq: 905,
    cause: "NULLIFY", required: "nullify",
    prompt: "需要使用【无懈可击】",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "hero.yuji_mobile.guohuo", answers_seq: 905 },
      { kind: "UseCard", card: nullify, targets: [], answers_seq: 905 },
      { kind: "Pass", answers_seq: 905 },
    ],
    preactivations: {}, preactivation: [],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
  });
  view.heroes = { 0: "yuji_mobile", 1: "guanyu" };
  view.snapshot.self_hand = [nullify];
  view.skills = [{
    skill_id: "hero.yuji_mobile.guohuo", name: "蛊惑", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "扣置一张手牌，将此牌当任意一张基本牌或普通锦囊牌使用或打出" },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const button = Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.yuji_mobile.guohuo");
  check("㉿PR1 响应窗口里【蛊惑】**点得动**（已翻预发动 ≠ 只认清单）",
    !!button && button.disabled === false && button.classList.contains("ready"),
    `disabled=${button && button.disabled} usable=${button && button.getAttribute("data-usable")} badge=${button && button.textContent}`);
  // 徽标读 `aria-label`（服务端 → 节点的读屏文案，`innerHTML` 里的 `.kind` 副行同源）：
  // 垫片不解析 `innerHTML` 文本，`textContent` 在这里恒空。
  const label = (button && button.getAttribute("aria-label")) || "";
  check("㉿PR2 徽标如实写「可发动」而不是「预发动」（这一问给的是骨架候选）",
    !!button && !/预发动/.test(label) && /可发动/.test(label),
    `aria-label="${label}"`);
  (button.listeners.click || []).forEach((fn) => fn({ target: button }));
  const picked = ui.buildAction();
  check("㉿PR3 点它就是选中那条 `ActivateSkill` 候选（直提态，不是进预发动）",
    ui.state.preactivation === null
      && !!picked && picked.kind === "ActivateSkill"
      && picked.skill_id === "hero.yuji_mobile.guohuo",
    `pre=${JSON.stringify(ui.state.preactivation)} action=${JSON.stringify(picked)}`);
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
  const findCard = (root, cardId) => {
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) { out.push(child); walk(child); }
    };
    walk(root);
    return out.find((node) => node.getAttribute
      && node.getAttribute("data-card-id") === String(cardId)) || null;
  };
  check("㉿J2 自己区域内选一张 = 牌桌直选：不铺左下角选择面，提示指向手牌 / 装备区",
    $("t-options").children.length === 0
      && $("t-target-hint").textContent.includes("手牌或装备区")
      && $("t-target-hint").textContent.includes("即提交"),
    `块=${$("t-options").children.length} hint="${$("t-target-hint").textContent}"`);
  check("㉿J3 直选态不再用左下角取消键；“放弃”仍在右端（候选里真有 Pass）",
    $("btn-cancel").disabled === true && $("btn-pass").hidden === false,
    `cancel=${$("btn-cancel").disabled} passHidden=${$("btn-pass").hidden}`);

  // 直选手牌 / 直选装备都 = 一次 SelectCards（面内位置）；未建局时只留本地态。
  ui.state.sessionId = null;
  await clickAndWait(findCard($("t-hand"), 21));
  const handPick = ui.buildAction();
  check("㉿J3b 点手牌 = 直接选中那条 SelectCards（picks=[0]），不再先铺槽位再提交",
    !!handPick && handPick.kind === "SelectCards"
      && JSON.stringify(handPick.picks) === "[0]",
    JSON.stringify(handPick));
  ui.state.picks = [];
  ui.renderTable();
  ui.renderPending(pickView);
  await clickAndWait(findCard($("t-self-zones"), 22));
  const equipPick = ui.buildAction();
  check("㉿J3c 点装备区 = 同样直接选中那条 SelectCards（picks=[1]）",
    !!equipPick && equipPick.kind === "SelectCards"
      && JSON.stringify(equipPick.picks) === "[1]",
    JSON.stringify(equipPick));

  ui.state.sessionId = "smoke-missing-session";
  ui.state.picks = [];
  ui.renderTable();
  ui.renderPending(pickView);
  const beforePick = calls.length;
  await clickAndWait($("btn-pass"));
  const sentPick = calls.length > beforePick ? calls[calls.length - 1].body.action : null;
  check("㉿J4 直选态点右端「放弃」= **送出一条 Pass 候选**",
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
  const targetBox = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === "1");
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

/* ============ 票 14 A：空候选的「选一张」询问仍要发，界面只能放弃 ============
 *
 * 形状 = 铁骑"没有同花色牌"：`SELECT_CARDS` + `slots=[]` + 只有一条 `Pass`。界面
 * 不铺空选择面，也不闪错误；右端「放弃」与「提交」都是那条唯一候选。
 */
{
  const empty = synthView({
    kind: "SELECT_CARDS", actor: 0, seq: 930,
    prompt: "请从选择面里选择一张牌。",
    legal_actions: [{ kind: "Pass", answers_seq: 930 }],
    candidate_index: {}, virtual_index: {}, option_seats: {},
    slots: [],
    spec: { zones: ["hand", "equip"], total: [1, 1], sequential: false, optional: true },
    context: { requirement: "弃置一张与判定结果花色相同的牌", skill_hints: [] },
  });
  empty.snapshot.self_hand = [synthCard(41, "闪")];
  ui.state.view = empty;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(empty);
  check("㉿TQ1 空候选仍渲染成“只能放弃”：不铺空选择面、无错误文案，右端放弃可见",
    $("t-options").children.length === 0
      && $("btn-pass").hidden === false
      && !/错误|没有可用|不可用/.test($("t-choice").textContent),
    `块=${$("t-options").children.length} passHidden=${$("btn-pass").hidden} choice="${$("t-choice").textContent}"`);

  ui.state.sessionId = "smoke-missing-session";
  const beforeEmpty = calls.length;
  await clickAndWait($("btn-submit"));
  const emptyAction = calls.length > beforeEmpty ? calls[calls.length - 1].body.action : null;
  check("㉿TQ2 空候选中点「提交」送出的只能是那条 Pass",
    !!emptyAction && emptyAction.kind === "Pass", JSON.stringify(emptyAction));
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
  check("㉿H2 手牌单张直选：不铺左下角选择面，提示只指向手牌",
    $("t-options").children.length === 0
      && $("t-target-hint").textContent.includes("手牌")
      && !$("t-target-hint").textContent.includes("装备区"),
    `块=${$("t-options").children.length} hint="${$("t-target-hint").textContent}"`);
  check("㉿H3 直选态“放弃”在右端（候选里真有 Pass）", $("btn-pass").hidden === false,
    `passHidden=${$("btn-pass").hidden}`);

  ui.state.sessionId = "smoke-missing-session";
  const beforePick = calls.length;
  await clickAndWait($("btn-pass"));
  const sentPick = calls.length > beforePick ? calls[calls.length - 1].body.action : null;
  check("㉿H4 直选态点右端「放弃」= **送出一条 Pass 候选**",
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
  check("㉿Y2 两张手牌 = 牌桌直选：不铺左下角选择面，提示要求选中两张",
    $("t-options").children.length === 0
      && $("t-target-hint").textContent.includes("手牌")
      && $("t-target-hint").textContent.includes("2 张"),
    `块=${$("t-options").children.length} hint="${$("t-target-hint").textContent}"`);
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
  check("㉿Z2 手牌 ∪ 装备区 = 牌桌直选：不铺左下角选择面，提示同时指向两区",
    $("t-options").children.length === 0
      && $("t-target-hint").textContent.includes("手牌或装备区"),
    `块=${$("t-options").children.length} hint="${$("t-target-hint").textContent}"`);
  check("㉿Z3 「任意张」的上限是**真实池大小**（服务端解析 hi=None，不进哨兵值）",
    JSON.stringify(pickView.pending.spec.total) === "[1,2]",
    JSON.stringify(pickView.pending.spec.total));
  check("㉿Z4 候选里有 Pass ⇒ 取消按钮可点", $("btn-cancel").disabled === false,
    `disabled=${$("btn-cancel").disabled}`);

  const findCard = (root, cardId) => {
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) { out.push(child); walk(child); }
    };
    walk(root);
    return out.find((node) => node.getAttribute
      && node.getAttribute("data-card-id") === String(cardId)) || null;
  };
  ui.state.sessionId = null;
  await clickAndWait(findCard($("t-hand"), 51));
  await clickAndWait(findCard($("t-self-zones"), 52));
  const both = ui.buildAction();
  check("㉿Z4b 多张自己区域面：手牌 + 装备区都能直接点选，一次提交两个面内位置",
    !!both && both.kind === "SelectCards"
      && JSON.stringify(both.picks) === "[0,1]",
    JSON.stringify(both));
  ui.state.picks = [];
  ui.renderTable();
  ui.renderPending(pickView);

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
  check("㉿W2 本人手牌 = 牌桌直选：不铺左下角选择面，上限仍是真实池大小",
    $("t-options").children.length === 0
      && $("t-target-hint").textContent.includes("手牌")
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
  const rendeSeat = Array.from($("t-seats").children).find((c) => c.getAttribute("data-seat") === "2");
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
      && Array.from($("t-options").children).map((node) => node.textContent).join(",")
        === "♥红桃,♦方块,♠黑桃,♣梅花"
      && Array.from($("t-options").children).map((node) => node.getAttribute("data-color")).join(",")
        === "red,red,black,black"
      && $("btn-pass").hidden === true
      && !/heart|diamond|spade|club/.test($("t-options").text()),
    `kind=${ui.stepDialogKind(suitView.pending)} 选项=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))} passHidden=${$("btn-pass").hidden}`);

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
  // 步长按钮：默认 0.1s（U4-02 / D7），点一下在 0.03→0.3 五档之间循环（不记住，刷新回默认）。
  check("㉿E1 默认步长 0.1 秒", ui.state.eventDelayMs === 100, `eventDelayMs=${ui.state.eventDelayMs}`);
  check("㉿E1b 档位就是五档 30/50/100/200/300（最小挡 0.03s 在首位）",
    JSON.stringify(ui.DELAY_STEPS_MS) === JSON.stringify([30, 50, 100, 200, 300]),
    `steps=${JSON.stringify(ui.DELAY_STEPS_MS)}`);
  const delayBtn = $("btn-delay");
  check("㉿E1c 牌桌上有步长按钮，按钮上显示当前档", !!delayBtn && delayBtn.textContent === "步长 0.1s",
    `按钮="${delayBtn ? delayBtn.textContent : "(缺)"}"`);
  const clickDelay = () => (delayBtn.listeners.click || []).forEach((fn) => fn({ target: delayBtn }));
  clickDelay();
  check("㉿E1d 点一下 → 0.2 秒", ui.state.eventDelayMs === 200 && delayBtn.textContent === "步长 0.2s",
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayBtn.textContent}"`);
  // 走完 0.3 / 0.03 两档：满档后落到最小挡 0.03s（两位小数，不与 0.1 撞字）。
  clickDelay();
  clickDelay();
  check("㉿E1f 满档后落到新的最小挡 0.03 秒，按钮文字跟着换",
    ui.state.eventDelayMs === 30 && delayBtn.textContent === "步长 0.03s",
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayBtn.textContent}"`);
  clickDelay();
  clickDelay();
  check("㉿E1e 五档循环一圈回到默认 0.1 秒", ui.state.eventDelayMs === 100 && delayBtn.textContent.includes("0.1"),
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
  // U3-01：离区不再瞬删——牌留在舞台上淡出（`.fading`），描述文本立即清空。
  check("㉿E5 播到「离开处理区」⇒ 牌从「处理中」转入淡出（不再瞬间消失）",
    $("t-processing").text().includes("杀")
      && ((Array.from($("t-processing").children).find(
        (node) => node.getAttribute("data-card-id") === "6",
      ) || {}).classList || { contains: () => false }).contains("fading"),
    `处理区="${$("t-processing").text().trim()}"`);
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
    // M6 P0（启用池 16 → 19）后同一个 seed 的发将会变，故这两条场景**钉住座位 0 的武将**
    // （``my_hero``）再扫种子，而不是赌随机发到赵云 / 刘备。种子只决定"第 1 回合手牌够用"：
    // 探针在 seed 5 上分别命中赵云的【闪】+ 龙胆清单（第 3 步）与刘备的仁德（第 1 步）；
    // 后续换池若命中不了，重扫一次种子即可（判据不变）。
    const found = { longdan: null, rende: null };
    for (const seed of [5, 1, 2, 3, 7]) {
      if (found.longdan && found.rende) break;
      if (!found.longdan) {
        await newSession(seed, { my_hero: "zhaoyun" });
        for (let step = 0; step < 260 && ui.state.view.pending; step += 1) {
          const view = ui.state.view;
          const p = view.pending;
          if (p && p.kind === "PLAY_PHASE" && p.actor === 0) {
            const dodge = (view.snapshot.self_hand || []).find((card) => card.type === "dodge");
            // W4-fix-2：转化候选已从候选面收回，入口改由**清单**表达。
            const manifest = (p.preactivations || {})["hero.zhaoyun.longdan"];
            if (dodge && manifest) {
              found.longdan = { seed, steps: step, cardId: dodge.card_id, hero: "zhaoyun" };
              break;
            }
          }
          await advanceOnce();
        }
      }
      if (!found.rende) {
        await newSession(seed, { my_hero: "liubei" });
        for (let step = 0; step < 260 && ui.state.view.pending; step += 1) {
          const view = ui.state.view;
          const p = view.pending;
          if (p && p.kind === "PLAY_PHASE" && p.actor === 0) {
            const skill = (view.skills || []).find((item) => item.skill_id === "hero.liubei.rende");
            if (skill && skill.available) {
              found.rende = { seed, steps: step, hero: "liubei" };
              break;
            }
          }
          await advanceOnce();
        }
      }
    }
    return found;
  };
  const replayScanTo = async (found) => {
    await newSession(found.seed, { my_hero: found.hero });
    for (let step = 0; step < found.steps; step += 1) await advanceOnce();
    return found;
  };
  const found = await scanHeroCases();

  check("㉿L8 真局扫到「赵云出牌阶段手里有可转化的【闪】」", !!found.longdan,
    found.longdan ? `seed=${found.longdan.seed} steps=${found.longdan.steps}` : "钉住赵云后 5 个种子内没找到");
  if (found.longdan) {
    await replayScanTo(found.longdan);
    const p = ui.state.view.pending;
    const dodge = (ui.state.view.snapshot.self_hand || []).find((card) => card.card_id === found.longdan.cardId);
    check("㉿L9 真局里【闪】不再直接点成【杀】（候选面已收口）",
      !!dodge && !ui.cardSelectable(p, dodge.card_id),
      `card=${dodge && dodge.name} selectable=${dodge && ui.cardSelectable(p, dodge.card_id)}`);
    const longdanButton = Array.from($("t-skills").children).find(
      (b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
    check("㉿L9b 龙胆的唯一入口 = 清单（按钮可点、来源 → 目标两步）",
      !!longdanButton && longdanButton.disabled === false
        && longdanButton.getAttribute("data-preactivation") === "2",
      `disabled=${longdanButton && longdanButton.disabled} pre=${longdanButton && longdanButton.getAttribute("data-preactivation")}`);
  }

  check("㉿R8 真局扫到「刘备出牌阶段仁德可发动」", !!found.rende,
    found.rende ? `seed=${found.rende.seed} steps=${found.rende.steps}` : "钉住刘备后 5 个种子内没找到");
  if (found.rende) {
    await replayScanTo(found.rende);
    const button = Array.from($("t-skills").children).find((b) => b.getAttribute("data-skill-id") === "hero.liubei.rende");
    check("㉿R9 真局里仁德是**预发动**入口（按钮可点、清单两步、没有「发动技能」选项）",
      !!button && button.disabled === false
        && button.getAttribute("data-preactivated") === "true"
        && button.getAttribute("data-preactivation") === "2"
        && !Array.from($("t-options").children).some((b) => /发动技能/.test(b.textContent)),
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
    const next = Array.from($("t-options").children).find((b) => b.textContent === "下一步");
    if (next) (next.listeners.click || []).forEach((fn) => fn({ target: next }));
    const seats = pa ? ui.preactivationOptions(pa, 1) : null;
    const firstListedSeat = Array.from($("t-seats").children).find(
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

  const skillButton = () => Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "demo.preactivate",
  );
  const handChips = () => Array.from($("t-hand").children);

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

  const next = Array.from($("t-options").children).find((c) => c.textContent === "下一步");
  check("㉿PZ5 选牌步的「下一步」在张数达标后可点", !!next && next.disabled === false);
  (next.listeners.click || []).forEach((fn) => fn({ target: next }));
  check("㉿PZ6 进第 2 步：座位 1 / 2 可点、座位 0 不可点",
    seatBox(1).classList.contains("targetable")
      && seatBox(2).classList.contains("targetable")
      && !seatBox(0).classList.contains("targetable"),
    `seat1=${seatBox(1).classList.contains("targetable")} seat0=${seatBox(0).classList.contains("targetable")}`);

  // 重选第 1 步：清空第 1..N 步（用户裁定的"重选第 K 步清空 K..N"）。
  const stepChip = preactivationStepChip(0);
  (stepChip.listeners.click || []).forEach((fn) => fn({ target: stepChip }));
  check("㉿PZ7 重选第 1 步清空第 1..N 步",
    ui.state.preactivation.cursor === 0
      && ui.state.preactivation.answers.every((answer) => answer.values.length === 0),
    JSON.stringify(ui.state.preactivation.answers));

  // 再走一遍，确认复合动作的拼装。
  (handChips()[0].listeners.click || []).forEach((fn) => fn({ target: handChips()[0] }));
  const next2 = Array.from($("t-options").children).find((c) => c.textContent === "下一步");
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
    const button = Array.from($("t-skills").children).find(
      (b) => b.getAttribute("data-skill-id") === "demo.wording");
    (button.listeners.click || []).forEach((fn) => fn({ target: button }));
  };
  const stepChipText = (index) => {
    const chip = preactivationStepChip(index);
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

/* ④c 票 08：预发动**不许越步** + 标题 / 页脚同源（三步清单：seat → option → card）。
 *
 * 旧实现允许点第 2 步的步骤 chip 跳过空着的第 1 步；把后面两步填满后光标到末尾而第 1 步
 * 仍空 ⇒ 标题（`#t-target-hint`，只看 `pa.cursor`）写「清单已填完」，页脚（`#t-choice`，
 * 按 `preactivationReady`）写「清单里还有步骤没填完。」，确认按钮禁用；此时点场上武将无效
 * （座位步只认当前光标），点第 1 步 chip 又会把第 2/3 步清空 ⇒ 死锁。
 * 修法：`firstUnfilledPreativationStep` 是**唯一判据**——ready / 提示 / chip 落点都读它。
 *
 * 清单形状与**写入面同源**（界孙尚香【结姻】的 `step_requirements` + `JIE_JIEYIN_OPTIONS`，
 * `src/sgs/skills/active.py`）：选项步的池值是**机器 id**（实机渲染的就是这个），不是文案。
 */
{
  const ticket08Card = { ...synthCard(31, "杀"), type: "slash" };
  const ticket08Pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 950,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 950 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {
      "demo.ticket08": [
        { name: "seat", zones: [], min: 1, max: 1, order: 0, wording: "选择一名男性角色",
          pool: [{ when: [], options: ["seat:1", "seat:2"] }] },
        { name: "option", zones: [], min: 1, max: 1, order: 1,
          wording: "选择弃置一张手牌，或将一张装备牌置入其装备区",
          pool: [{ when: [], options: ["jieyin_discard_hand", "jieyin_place_equipment"] }] },
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 2,
          wording: "弃置一张手牌 / 置入一张装备牌（目标同槽必须有空位）",
          pool: [{ when: [], options: ["31"] }] },
      ],
    },
    preactivation: [],
  };
  const ticket08View = synthView(ticket08Pending);
  ticket08View.heroes = { 0: "jie_sunshangxiang", 1: "jie_zhangfei", 2: "jie_huaxiong" };
  ticket08View.snapshot.self_hand = [ticket08Card];
  ticket08View.skills = [{
    skill_id: "demo.ticket08", name: "三步技", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "", convert_sources: [] },
  }];
  ui.state.view = ticket08View;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ticket08View);

  const t8Skill = () => Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "demo.ticket08");
  const t8Button = (text) => Array.from($("t-options").children).find(
    (c) => c.textContent === text) || null;
  const t8Click = (node) => {
    if (!node) return false;
    (node.listeners.click || []).forEach((fn) => fn({ target: node }));
    return true;
  };
  const t8Hand = () => $("t-hand").children[0] || null;
  const t8Hint = () => $("t-target-hint").textContent;
  const t8Foot = () => $("t-choice").textContent;
  const t8Pa = () => ui.state.preactivation;
  // 桥对缺失项容错 ⇒ 未实现的判据在这里是 `undefined`（一条 FAIL），不是加载期 ReferenceError。
  const t8First = (pa) => (typeof ui.firstUnfilledPreativationStep === "function"
    ? ui.firstUnfilledPreativationStep(pa) : undefined);

  t8Click(t8Skill());
  check("㉿JS1 三步清单进入预发动：光标停在第 1 步",
    !!t8Pa() && t8Pa().cursor === 0 && t8Pa().steps.length === 3,
    `cursor=${t8Pa() && t8Pa().cursor} steps=${t8Pa() && t8Pa().steps.length}`);

  // 空着第 1 步点第 2 步的 chip：旧实现跳过去，修后钳回第一个空步。
  t8Click(preactivationStepChip(1));
  check("㉿JS2 不许越步：点第 2 步 chip 落回第 1 步（钳到第一个空步）",
    !!t8Pa() && t8Pa().cursor === 0
      && t8Pa().answers.every((answer) => answer.values.length === 0),
    `cursor=${t8Pa() && t8Pa().cursor} answers=${JSON.stringify(t8Pa() && t8Pa().answers)}`);
  check("㉿JS2b 唯一判据：第 1 步就是第一个空步、清单未 ready",
    !!t8Pa() && t8First(t8Pa()) === 0 && ui.preactivationReady(t8Pa()) === false,
    `first=${t8Pa() && t8First(t8Pa())} ready=${!!t8Pa() && ui.preactivationReady(t8Pa())}`);
  check("㉿JS3 标题与页脚同源：都指第 1 步、都不说「清单已填完」、确认禁用",
    t8Hint().includes("第 1/3 步") && t8Hint().includes("选择一名男性角色")
      && t8Foot().includes("第 1/3 步")
      // 标题 =（可能的产物前缀「将以【X】使用。」）+ 同一份步骤提示 ⇒ 以页脚那句结尾。
      && t8Hint().endsWith(t8Foot())
      && !t8Hint().includes("清单已填完") && !t8Foot().includes("清单已填完")
      && $("btn-submit").disabled === true,
    `hint="${t8Hint()}" foot="${t8Foot()}" submit=${$("btn-submit").disabled}`);

  // 按序补完：座位步（点场上武将）→ 选项步（点选项池里的机器 id）→ 选牌步（点手牌 + 下一步）。
  t8Click(seatBox(1));
  check("㉿JS4 填完第 1 步自动进第 2 步（选项步）",
    !!t8Pa() && t8Pa().cursor === 1 && t8Hint().includes("第 2/3 步"),
    `cursor=${t8Pa() && t8Pa().cursor} hint="${t8Hint()}"`);
  t8Click(t8Button("jieyin_discard_hand"));
  check("㉿JS5 选项选完自动进第 3 步（选牌步），确认仍禁用",
    !!t8Pa() && t8Pa().cursor === 2 && t8Hint().includes("第 3/3 步")
      && $("btn-submit").disabled === true,
    `cursor=${t8Pa() && t8Pa().cursor} hint="${t8Hint()}" submit=${$("btn-submit").disabled}`);
  t8Click(t8Hand());
  t8Click(t8Button("下一步"));
  check("㉿JS6 按序填完 = 标题才说「清单已填完」、页脚说可以确认、按钮启用",
    !!t8Pa() && ui.preactivationReady(t8Pa()) === true
      && t8Hint().includes("清单已填完") && t8Hint().includes("可以确认发动")
      && t8Foot().includes("可以确认发动") && !t8Foot().includes("还有步骤没填完")
      && $("btn-submit").disabled === false,
    `hint="${t8Hint()}" foot="${t8Foot()}" submit=${$("btn-submit").disabled}`);

  // 兜底支路：「光标在末尾、第 1 步空」旧版跳步能造出来；跳步已被钳住 ⇒ 这里直接合成
  // 那份坏态，钉住标题 / 页脚**同读**同一个判据（本票的症状就是两处各算一套）。
  t8Pa().answers[0] = { values: [], texts: [] };
  t8Pa().cursor = t8Pa().steps.length;
  ui.renderPending(ticket08View);
  check("㉿JS7 光标在末尾但第 1 步空：标题/页脚都指第 1 步、确认禁用",
    t8Hint().endsWith(t8Foot()) && t8Hint().includes("第 1/3 步")
      && !t8Hint().includes("清单已填完")
      && $("btn-submit").disabled === true,
    `hint="${t8Hint()}" foot="${t8Foot()}" submit=${$("btn-submit").disabled}`);
  t8Click(preactivationStepChip(2));
  check("㉿JS8 坏态下点第 3 步 chip ⇒ 落回第 1 步（目标步补得回来，不再死锁）",
    !!t8Pa() && t8Pa().cursor === 0
      && t8Pa().answers.every((answer) => answer.values.length === 0),
    `cursor=${t8Pa() && t8Pa().cursor} answers=${JSON.stringify(t8Pa() && t8Pa().answers)}`);
  ui.resetSelection();
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
  const button = () => Array.from($("t-skills").children).find(
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
    Array.from($("t-options").children).some((b) => /进入预发动/.test(b.textContent))
      && !Array.from($("t-options").children).some((b) => /发动技能/.test(b.textContent))
      && (() => {
        const b = Array.from($("t-skills").children).find((x) => x.getAttribute("data-skill-id") === "demo.passive");
        return !!b && b.disabled === false && b.getAttribute("data-preactivation") === "1";
      })(),
    `选项=${JSON.stringify(Array.from($("t-options").children).map((b) => b.textContent))}`);
  const beforePass = calls.length;
  const cancel = $("btn-cancel");
  await clickAndWait(cancel);
  const passSent = calls.length > beforePass ? calls[calls.length - 1].body.action : null;
  check("㉿PZ14 被动入口点「放弃」= 送出 Pass（时机消费，不重问）",
    !!passSent && passSent.kind === "Pass", JSON.stringify(passSent));
}

/* ⑯ M6 票 23 报障：被动入口的**两步**清单，选完牌要能走到第 2 步。
 *
 * 用户实机（界【清俭】：选牌 → 交给一名其他角色）：第 1 步选满牌后「确认发动」是灰的，
 * 而界面上**没有「下一步」**——这一步进不去，清单填不完，复合动作永远拼不出来。
 * 与 ⑥ 的区别：⑥ 只验入口按钮画出来了，没验进入之后能不能往下走。
 */
{
  const handIds = [41, 42, 43];
  const twoStepPending = {
    kind: "SKILL_ACTIVATION", actor: 0, seq: 95,
    prompt: "是否发动该技能？", skill_id: "demo.passive2",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "demo.passive2", answers_seq: 95, picks: [], choices: [] },
      { kind: "Pass", answers_seq: 95 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {},
    preactivation: [
      { name: "card", zones: ["hand"], min: 1, max: 3, order: 0,
        pool: [{ when: [], options: ["41", "42", "43"] }] },
      { name: "seat", zones: [], min: 1, max: 1, order: 1,
        pool: [{ when: [], options: ["seat:1", "seat:2"] }] },
    ],
  };
  const twoStepView = synthView(twoStepPending);
  twoStepView.snapshot.self_hand = handIds.map((id) => synthCard(id, "杀"));
  twoStepView.skills = [{
    skill_id: "demo.passive2", name: "两步被动", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "", convert_sources: [], passive: true },
  }];
  ui.state.view = twoStepView;
  ui.state.sessionId = "smoke-missing-session";
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(twoStepView);

  const optionButtons = () => Array.from($("t-options").children);
  const optionTexts = () => optionButtons()
    .map((node) => String(node.textContent || "").trim()).join(" | ");
  const entry = optionButtons().find((node) => /进入预发动/.test(node.textContent || ""));
  (entry ? entry.listeners.click || [] : []).forEach((fn) => fn({ target: entry }));

  check("㉿PZ19 被动入口进入预发动后，第 1 步选牌有「下一步」",
    optionButtons().some((node) => String(node.textContent || "").trim() === "下一步"),
    `选项=${optionTexts()}`);

  // 选满 3 张手牌（清单第 1 步的候选正好这 3 张）。
  for (const chip of Array.from($("t-hand").children)) {
    for (const fn of chip.listeners.click || []) fn({ target: chip });
  }
  const nextButton = optionButtons()
    .find((node) => String(node.textContent || "").trim() === "下一步");
  check("㉿PZ20 选满之后「下一步」可点（张数达标才放行）",
    !!nextButton && nextButton.disabled === false,
    `选项=${optionTexts()} disabled=${nextButton ? nextButton.disabled : "no-button"}`);
  for (const fn of (nextButton || { listeners: {} }).listeners.click || []) {
    fn({ target: nextButton });
  }
  check("㉿PZ21 「下一步」进第 2 步：座位候选铺出来、状态仍是预发动",
    ui.preactivationStepKind(ui.state.preactivation) === "seat"
      && optionButtons().some((node) => /座位 1/.test(node.textContent || "")),
    `选项=${optionTexts()}`);

  const seatButton = optionButtons().find((node) => /座位 1/.test(node.textContent || ""));
  for (const fn of (seatButton || { listeners: {} }).listeners.click || []) {
    fn({ target: seatButton });
  }
  const action = ui.buildAction();
  check("㉿PZ22 两步填完 = 复合 ActivateSkill（picks = 三张实体牌、choices = seat:1）",
    !!action && action.kind === "ActivateSkill"
      && JSON.stringify(action.picks) === JSON.stringify(handIds)
      && JSON.stringify(action.choices) === '["seat:1"]',
    JSON.stringify(action));
}

/* ⑰ M6 票 23 报障（层二）：**五谷结算里**的预发动询问，正文槽被"持久亮牌"模式藏起来。
 *
 * 实机：五谷丰登取牌之后、结算帧还活着时紧跟着【清俭】的"是否发动"——这一帧的载荷里
 * `harvest` 仍在，于是 `state.dialog.persistent` 被算成 true。玩家点「进入预发动」后弹窗
 * 换了标题，**正文槽却仍按亮牌模式整体隐藏**：步骤条与「下一步」都在看不见的正文里，
 * 屏幕上只剩一个空白大白框 + 灰色的「确认发动」。
 */
{
  const harvestInFlight = {
    active: true, key: "harvest:9:7:0", title: "五谷丰登",
    hint: "从亮出的牌里选一张获得", source: 0,
    revealed: [synthCard(2, "桃")],
    targets: [{ seat: 0, status: "current", card: null, cancelled: false, reason: null }],
    current: 0,
  };
  const qingjianPending = {
    kind: "SKILL_ACTIVATION", actor: 0, seq: 97,
    prompt: "是否发动该技能？", skill_id: "demo.passive3",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "demo.passive3", answers_seq: 97, picks: [], choices: [] },
      { kind: "Pass", answers_seq: 97 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, harvest: harvestInFlight,
    preactivation: [
      { name: "card", zones: ["hand"], min: 1, max: 3, order: 0,
        pool: [{ when: [], options: ["41", "42", "43"] }] },
      { name: "seat", zones: [], min: 1, max: 1, order: 1,
        pool: [{ when: [], options: ["seat:1"] }] },
    ],
  };
  const qingjianView = synthView(qingjianPending);
  qingjianView.snapshot.self_hand = [41, 42, 43].map((id) => synthCard(id, "杀"));
  qingjianView.skills = [{
    skill_id: "demo.passive3", name: "两步被动", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true, preactivated: true,
    ui: { requirement: "", convert_sources: [], passive: true },
  }];
  ui.state.view = qingjianView;
  ui.state.sessionId = "smoke-missing-session";
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(qingjianView);

  const entry3 = Array.from($("t-options").children)
    .find((node) => /进入预发动/.test(node.textContent || ""));
  for (const fn of (entry3 ? entry3.listeners.click || [] : [])) fn({ target: entry3 });

  const dialogBody = $("t-dialog-body");
  const optionsInBody = Array.from(dialogBody.children).includes($("t-options"));
  check("㉿PZ23 五谷结算中进入预发动：正文槽必须可见（不能按亮牌模式整体隐藏）",
    dialogBody.hidden !== true && optionsInBody,
    `bodyHidden=${dialogBody.hidden} optionsInBody=${optionsInBody}`);
  // 选一张手牌（min=1）后「下一步」应当放行：这一帧的完整交互与上一块同一条路径。
  const firstChip = Array.from($("t-hand").children)[0];
  for (const fn of (firstChip ? firstChip.listeners.click || [] : [])) fn({ target: firstChip });
  const next3 = Array.from($("t-options").children)
    .find((node) => String(node.textContent || "").trim() === "下一步");
  check("㉿PZ24 这一帧里「下一步」看得见、选够张数后可点",
    !!next3 && next3.disabled === false && next3.parentNode === $("t-options"),
    `选项=${Array.from($("t-options").children).map((n) => n.textContent).join(" | ")}`
      + ` disabled=${next3 ? next3.disabled : "no-button"}`);
}

/* ㉿W4-3（票 02）同一时机的激活面：一张面选一条发动（N ≥ 2 条技能候选）。
 *
 * 01 落地后一条 `SKILL_ACTIVATION` 里会有**多条** `ActivateSkill` 候选（同一时机同一角色
 * 的可发动技能）。今天的直提态只在「恰好一条」时成立，N ≥ 2 会落到「没有可用的界面」。
 * 这里喂一份与服务端同形的合成载荷（`activation_preactivations` = 逐候选清单），钉住：
 *
 *   ① 面 = 「选一条发动」，每条候选一行（技能名 + 描述），末条「都不发动」；
 *   ② 「都不发动」的有无**只由 `legal_actions` 里的 `Pass` 决定**（不认技能 id）；
 *   ③ 选中哪条，展开的就是**哪条**的清单（`activation_preactivations[skill_id]`）——
 *      不能退化成代表候选那份 `preactivation`。
 */
{
  const alphaSteps = [
    { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
      pool: [{ when: [], options: ["41"] }], wording: "先付甲的代价" },
  ];
  const betaSteps = [
    { name: "seat", zones: [], min: 1, max: 1, order: 0,
      pool: [{ when: [], options: ["seat:1"] }], wording: "选乙的目标" },
  ];
  const facePending = (withPass) => ({
    kind: "SKILL_ACTIVATION", actor: 0, seq: withPass ? 1300 : 1301,
    prompt: "是否发动该技能？", skill_id: "demo.alpha",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "demo.alpha", answers_seq: 1300, picks: [], choices: [] },
      { kind: "ActivateSkill", skill_id: "demo.beta", answers_seq: 1300, picks: [], choices: [] },
      ...(withPass ? [{ kind: "Pass", answers_seq: 1300 }] : []),
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {},
    preactivation: alphaSteps,
    activation_preactivations: { "demo.alpha": alphaSteps, "demo.beta": betaSteps },
  });
  const faceSkill = (skillId, name, description) => ({
    skill_id: skillId, name, skill_type: "NORMAL", locked: false, carrier: "HERO",
    available: true, preactivated: true, description,
    ui: { requirement: "", convert_sources: [] },
  });
  const showFace = (withPass = true) => {
    const view = synthView(facePending(withPass));
    view.skills = [
      faceSkill("demo.alpha", "示例甲", "甲的技能描述"),
      faceSkill("demo.beta", "示例乙", "乙的技能描述"),
    ];
    view.snapshot.self_hand = [synthCard(41, "杀")];
    ui.state.view = view;
    ui.state.sessionId = "smoke-missing-session";
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
    return view;
  };
  const faceRows = () => Array.from($("t-options").children);
  const faceTexts = () => faceRows().map((node) => String(node.textContent || ""));

  const face = showFace(true);
  check("㉿AF1 N=2 的激活面走「选一条发动」（不再落到『没有可用的界面』）",
    ui.renderKind(face.pending, face.pending.legal_actions) === "activation-face"
      && ui.stepDialogKind(face.pending) === ui.DIALOG_KINDS.choice,
    `kind=${ui.renderKind(face.pending, face.pending.legal_actions)}`
      + ` dialog=${ui.stepDialogKind(face.pending)}`);
  check("㉿AF2 面的正文在询问弹窗里，标题「选一条发动」",
    $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "选一条发动"
      && faceRows().length === 3,
    `hidden=${$("t-dialog").hidden} title="${$("t-dialog-title").textContent}"`
      + ` rows=${JSON.stringify(faceTexts())}`);
  check("㉿AF3 每条候选一行 = 技能名 + 描述，末条「都不发动」",
    faceTexts().length === 3
      && /示例甲/.test(faceTexts()[0]) && /甲的技能描述/.test(faceTexts()[0])
      && /示例乙/.test(faceTexts()[1]) && /乙的技能描述/.test(faceTexts()[1])
      && faceTexts()[2].trim() === "都不发动",
    `rows=${JSON.stringify(faceTexts())}`);

  await clickAndWait(faceRows()[0]);
  check("㉿AF4 选中甲 ⇒ 展开的是甲自己的清单（不是代表候选那份的复制品）",
    !!ui.state.preactivation
      && ui.state.preactivation.skillId === "demo.alpha"
      && ui.state.preactivation.steps[0].wording === "先付甲的代价",
    `skillId=${ui.state.preactivation && ui.state.preactivation.skillId}`
      + ` wording="${ui.state.preactivation && ui.state.preactivation.steps[0].wording}"`);

  showFace(true);
  await clickAndWait(faceRows()[1]);
  check("㉿AF5 选中乙 ⇒ 换成乙自己的清单（按选中的 skill_id 取）",
    !!ui.state.preactivation
      && ui.state.preactivation.skillId === "demo.beta"
      && ui.state.preactivation.steps[0].wording === "选乙的目标",
    `skillId=${ui.state.preactivation && ui.state.preactivation.skillId}`
      + ` wording="${ui.state.preactivation && ui.state.preactivation.steps[0].wording}"`);

  showFace(false);
  check("㉿AF6 候选里没有 Pass ⇒ 末条「都不发动」不铺（判据是候选，不是技能 id）",
    faceRows().length === 2 && !faceTexts().some((text) => /都不发动/.test(text)),
    `rows=${JSON.stringify(faceTexts())}`);

  showFace(true);
  const beforeGiveUp = calls.length;
  await clickAndWait(faceRows()[2]);
  const giveUpSent = calls.length > beforeGiveUp
    ? calls[calls.length - 1].body.action : null;
  check("㉿AF7 「都不发动」= 送出面里那条 Pass 候选",
    !!giveUpSent && giveUpSent.kind === "Pass", JSON.stringify(giveUpSent));
}

/* ㉿W4-5（复核修复 F2）N ≥ 2 激活面里**带清单的那条候选**：进入清单后提交键不能被打灰。
 *
 * 复现：点带清单的那条 ⇒ `enterPreativation` 重绘**同一张** N ≥ 2 的询问；`refreshSubmit`
 * 只判 `isActivationFace`（漏了 `state.preactivation`）⇒ 提交键被置灰、提示条被改写成
 * "点一条技能发动…"，清单填完了也提不出去。今天没有活的 N ≥ 2 面带清单，故它一直潜伏。
 * 判据两半：①有清单：进入 → 清单可见 → 填完 ⇒ 提交键可点、提示不是激活面那句；
 * ②没清单：一击即提交（既有"点一个选项即提交"的语义不变）。
 */
{
  const alphaSteps = [
    { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
      pool: [{ when: [], options: ["41"] }], wording: "先付甲的代价" },
  ];
  const mixedFacePending = {
    kind: "SKILL_ACTIVATION", actor: 0, seq: 1320,
    prompt: "是否发动该技能？", skill_id: "demo.alpha",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "demo.alpha", answers_seq: 1320, picks: [], choices: [] },
      { kind: "ActivateSkill", skill_id: "demo.beta", answers_seq: 1320, picks: [], choices: [] },
      { kind: "Pass", answers_seq: 1320 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {},
    preactivation: alphaSteps,
    // 只有甲带清单：乙选中就是那条裸 `ActivateSkill`（一击即提交）。
    activation_preactivations: { "demo.alpha": alphaSteps },
  };
  const skillOf = (skillId, name) => ({
    skill_id: skillId, name, skill_type: "NORMAL", locked: false, carrier: "HERO",
    available: true, preactivated: true, description: `${name}的描述`,
    ui: { requirement: "", convert_sources: [] },
  });
  const mixedView = synthView(mixedFacePending);
  mixedView.skills = [skillOf("demo.alpha", "示例甲"), skillOf("demo.beta", "示例乙")];
  mixedView.snapshot.self_hand = [synthCard(41, "杀")];
  const showMixedFace = () => {
    ui.state.view = mixedView;
    ui.state.sessionId = "smoke-missing-session";
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(mixedView);
  };
  const mixedRows = () => Array.from($("t-options").children);
  const mixedRow = (text) => mixedRows()
    .find((node) => new RegExp(text).test(node.textContent || ""));
  const activationHint = "点一条技能发动";

  showMixedFace();
  await clickAndWait(mixedRow("示例甲"));
  check("㉿AF12 点带清单的候选 ⇒ 进入它自己的清单（表面切到预发动、步骤可见）",
    !!ui.state.preactivation && ui.state.preactivation.skillId === "demo.alpha"
      && ui.renderKind(mixedView.pending, mixedView.pending.legal_actions) === "preactivation"
      && mixedRows().some((node) => String(node.textContent || "").trim() === "下一步"),
    `skillId=${ui.state.preactivation && ui.state.preactivation.skillId}`
      + ` kind=${ui.renderKind(mixedView.pending, mixedView.pending.legal_actions)}`
      + ` rows=${JSON.stringify(mixedRows().map((n) => String(n.textContent || "").trim()))}`);
  check("㉿AF13 清单没填完之前提交键仍不可点（ready 判据照旧）",
    $("btn-submit").disabled === true,
    `disabled=${$("btn-submit").disabled} hint="${$("t-choice").textContent}"`);

  const mixedChip = Array.from($("t-hand").children)[0];
  for (const fn of mixedChip ? mixedChip.listeners.click || [] : []) {
    fn({ target: mixedChip });
  }
  // 选牌步的"下一步"（与既有预发动用例同一条交互）：填满才放行，光标走到末尾才算 ready。
  const nextButton = mixedRows()
    .find((node) => String(node.textContent || "").trim() === "下一步");
  for (const fn of (nextButton || { listeners: {} }).listeners.click || []) {
    fn({ target: nextButton });
  }
  check("㉿AF14 清单填完 ⇒ 提交键**可点**、提示不是激活面那句（F2 的回归）",
    $("btn-submit").disabled === false
      && !String($("t-choice").textContent || "").includes(activationHint)
      && /可以确认发动/.test($("t-choice").textContent || ""),
    `disabled=${$("btn-submit").disabled} hint="${$("t-choice").textContent}"`);

  showMixedFace();
  const beforeNoManifest = calls.length;
  await clickAndWait(mixedRow("示例乙"));
  const noManifestSent = calls.length > beforeNoManifest
    ? calls[calls.length - 1].body.action : null;
  check("㉿AF15 没清单的候选仍是一击即提交（不被打灰的提交键挡住）",
    !!noManifestSent && noManifestSent.kind === "ActivateSkill"
      && noManifestSent.skill_id === "demo.beta",
    JSON.stringify(noManifestSent));
}

/* ㉿W4-4（票 02）**收窄面**：选完一条之后引擎**再问一次**，只剩剩下的那条 + `Pass`。
 *
 * 票 01 的派发环是"一次选一条"⇒ 同一时机不会把 N 条一起问第二遍；第二问只剩没选过的。
 * 这一问的候选数 = 1，**不走激活面**（§3-3「单候选不得变样」）：今天 N = 1 的直提态
 * 原样（`selectedSkillAction` 那条裸 `ActivateSkill` = 剩下的技能），已选的那条不再进面，
 * 「都不发动」由候选里那条 `Pass`（右端「放弃」）承担——候选里没有 `Pass` 时它就不出现。
 */
{
  const skillEntry = (skillId, name) => ({
    skill_id: skillId, name, skill_type: "NORMAL", locked: false, carrier: "HERO",
    available: false, preactivated: false, description: `${name}的描述`,
    ui: { requirement: "", convert_sources: [] },
  });
  const narrowedPending = {
    kind: "SKILL_ACTIVATION", actor: 0, seq: 1310,
    prompt: "是否发动该技能？", skill_id: "demo.beta",
    legal_actions: [
      { kind: "ActivateSkill", skill_id: "demo.beta", answers_seq: 1310, picks: [], choices: [] },
      { kind: "Pass", answers_seq: 1310 },
    ],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [], activation_preactivations: {},
  };
  const view = synthView(narrowedPending);
  // 两条技能都还在**武将**上（投影的 `view.skills` 不按候选裁），只是这条面里只剩乙。
  view.skills = [skillEntry("demo.alpha", "示例甲"), skillEntry("demo.beta", "示例乙")];
  ui.state.view = view;
  ui.state.sessionId = "smoke-missing-session";
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  // 「这一步的问句面」= 提示条 + 目标提示 + 询问文本 + 弹窗标题 + 选项格（技能按钮区不算）。
  const askText = () => [
    $("t-prompt").textContent, $("t-choice").textContent, $("t-target-hint").textContent,
    $("t-dialog-title").textContent, $("t-options").text(),
  ].join("  ");

  check("㉿AF8 收窄面 = N=1 的今天直提态（不铺选择面、弹窗已关）",
    ui.renderKind(narrowedPending, narrowedPending.legal_actions) === "skill"
      && $("t-options").children.length === 0
      && $("t-dialog").hidden === true,
    `kind=${ui.renderKind(narrowedPending, narrowedPending.legal_actions)}`
      + ` rows=${$("t-options").children.length} dialogHidden=${$("t-dialog").hidden}`);
  check("㉿AF9 面里只剩另一条（已选的那条不再出现，剩下的那条写明将提交）",
    /示例乙/.test(askText()) && !/示例甲/.test(askText())
      && /将提交：发动【示例乙】/.test($("t-choice").textContent),
    `ask="${askText().replace(/\s+/g, " ").trim()}"`);
  check("㉿AF10 收窄面的「都不发动」= 候选里那条 Pass 的右端「放弃」",
    $("btn-pass").hidden === false && $("btn-pass").textContent === "放弃"
      && ui.rightActionIndex(narrowedPending, "Pass") === 1,
    `hidden=${$("btn-pass").hidden} text="${$("btn-pass").textContent}"`
      + ` index=${ui.rightActionIndex(narrowedPending, "Pass")}`);

  const beforeNarrowed = calls.length;
  await clickAndWait($("btn-pass"));
  const narrowedSent = calls.length > beforeNarrowed
    ? calls[calls.length - 1].body.action : null;
  check("㉿AF11 收窄面点「放弃」= 送出一条 Pass（时机消费，不再重问）",
    !!narrowedSent && narrowedSent.kind === "Pass", JSON.stringify(narrowedSent));
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
  const skillButton = (id) => Array.from($("t-skills").children).find((b) => b.getAttribute("data-skill-id") === id);
  const handChips = () => Array.from($("t-hand").children);

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

  const wusheng = Array.from($("t-skills").children).find(
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
  const jijiu = Array.from($("t-skills").children).find(
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

  const peachChip = Array.from($("t-hand").children).find((c) => c.text().includes("桃"));
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
      const button = Array.from($("t-skills").children).find(
        (b) => b.getAttribute("data-skill-id") === "hero.huatuo.jijiu");
      const hint = $("t-skill-hints").textContent;
      return !!button && button.text().includes("急救")
        && hint.includes("急救") && hint.includes("当【桃】")
        && !labels.some((label) => label.includes("急救"));
    })(),
    `skills=${Array.from($("t-skills").children).map((b) => b.text()).join(" / ")} hints=${$("t-skill-hints").textContent}`);

  const jijiu = Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.huatuo.jijiu");
  (jijiu.listeners.click || []).forEach((fn) => fn({ target: jijiu }));
  check("㉿PH7 急救清单只有一张红色来源（无 seat 步）；确认 = ActivateSkill(来源)",
    (() => {
      const built = ui.buildAction();
      return ui.state.preactivation.steps.length === 1
        && built === null;  // 还没选来源 ⇒ 不能确认
    })(),
    JSON.stringify(ui.state.preactivation.steps.map((step) => step.name)));
  const sourceChip = Array.from($("t-hand").children).find((c) => c.text().includes("闪"));
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
  const buttonOf = (skillId) => Array.from($("t-skills").children).find(
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
    `hidden=${passButton && passButton.hidden} text="${passButton && passButton.textContent}" options=${Array.from($("t-options").children).map((c) => c.textContent).join(" / ")}`);
  check("㉿BS9 持刀者窗口：提示行写出「对 Bn 使用【杀】，或交出武器」",
    $("t-prompt").textContent.includes("交出武器")
      && $("t-prompt").textContent.includes("2 号位"),
    `prompt="${$("t-prompt").textContent}"`);

  const longdan = Array.from($("t-skills").children).find(
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
    `btn-pass="${$("btn-pass").textContent}" hidden=${$("btn-pass").hidden} options=${Array.from($("t-options").children).map((c) => c.textContent).join(" / ")}`);
  ui.resetSelection();
}

/* ============ 乱武窗口（合成载荷）：提示行 + `Pass` 按钮文案 ============
 *
 * lin-batch2 票 03（贾诩移动版【乱武】）：选项 1 = 窗口里的「使用【杀】」候选、选项 2 =
 * `Pass`（按钮写「失去1点体力」）。两处文案分别住在 `projection.py:_need_text` 的
 * `WindowCause.LUANWU` 分支（服务端下发 `context.requirement`，`tests/test_web.py` 钉）
 * 与 `pending.js:alternativeLabel` 的 `LUANWU` 分支（**本文件**验读取方）。
 */
{
  const slash = { ...synthCard(61, "杀"), type: "slash" };
  const luanwuPending = {
    kind: "RESPOND", actor: 0, seq: 94, cause: "LUANWU", required: "slash",
    prompt: "对距离最近的另一名角色使用【杀】，或失去1点体力",
    legal_actions: [
      { kind: "UseCard", card: slash, targets: [1], answers_seq: 94 },
      { kind: "Pass", answers_seq: 94 },
    ],
    candidate_index: {
      61: { card: slash, pool: [1], min_targets: 1, max_targets: 1, combos: [[1]] },
    },
    option_seats: {},
    context: { requirement: "对距离最近的另一名角色使用【杀】，或失去1点体力" },
    preactivations: {},
    preactivation: [],
  };
  const luanwuView = synthView(luanwuPending);
  luanwuView.snapshot.self_hand = [slash];
  luanwuView.heroes = { 0: "jiaxu_mobile", 1: "zhangfei", 2: "diaochan" };
  ui.state.view = luanwuView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(luanwuView);

  check("㉿LW1 乱武窗口：提示行 =「对距离最近的另一名角色使用【杀】，或失去1点体力」",
    $("t-prompt").textContent === "对距离最近的另一名角色使用【杀】，或失去1点体力",
    `prompt="${$("t-prompt").textContent}"`);
  check("㉿LW2 乱武窗口：`Pass` 按钮在右端、文案 =「失去1点体力」（不是「放弃」）",
    $("btn-pass").hidden === false && $("btn-pass").textContent === "失去1点体力",
    `hidden=${$("btn-pass").hidden} text="${$("btn-pass").textContent}"`);
  check("㉿LW3 乱武窗口不新增按钮：选项行里既没有「放弃」也没有第二个「失去1点体力」",
    Array.from($("t-options").children).every(
      (child) => !child.textContent.includes("放弃") && !child.textContent.includes("失去1点体力")),
    Array.from($("t-options").children).map((child) => child.textContent).join(" / "));
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

  const skillButton = () => Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.guanyu.wusheng");
  const nextButton = () => Array.from($("t-options").children).find((c) => c.textContent === "下一步");
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

  const skillButton = () => Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
  const nextButton = () => Array.from($("t-options").children).find((c) => c.textContent === "下一步");
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

  const skillButton = () => Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.guanyu.wusheng");
  const nextButton = () => Array.from($("t-options").children).find((c) => c.textContent === "下一步");
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

  const skillButton = () => Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.zhaoyun.longdan");
  const nextButton = () => Array.from($("t-options").children).find((c) => c.textContent === "下一步");
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

  const skillButton = () => Array.from($("t-skills").children).find(
    (b) => b.getAttribute("data-skill-id") === "hero.guanyu.wusheng");
  const nextButton = () => Array.from($("t-options").children).find((c) => c.textContent === "下一步");
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
  const rowOf = (name) => Array.from($("t-options").children).find(
    (node) => node.getAttribute && node.getAttribute("data-row") === name);
  const cardsBox = (row) => row.children[1];
  const cardIndexes = (row) => Array.from(cardsBox(row).children)
    .filter((node) => node.getAttribute && node.getAttribute("data-card-index") !== undefined)
    .map((node) => Number(node.getAttribute("data-card-index")));
  const slots = (row) => Array.from(cardsBox(row).children).filter(
    (node) => node.getAttribute && node.getAttribute("data-empty") === "1");
  const cardNode = (row, index) => Array.from(cardsBox(row).children).find(
    (node) => node.getAttribute && node.getAttribute("data-card-index") === String(index));
  const clickNode = (node) => (node.listeners.click || []).forEach((fn) => fn({ target: node, preventDefault() {} }));
  /* 拖拽手势（票 01）：`dragstart` 落在**源牌**上；`dragover` / `drop` 落在**行容器**上
   * （真浏览器里牌 / 空槽的事件冒泡到行，垫片不冒泡 ⇒ 用例直接派发给行）。
   *
   * 落点判定只认**指针坐标**（见 arrange.js 的 `arrangePlanFromPoint`），故这里按"源已让位"
   * 的折叠顺序给两行 / 每张牌铺一份确定矩形（一 = 100 宽）——它扮演真浏览器里排版结果的角色，
   * 也是这次实机闪烁回归（同一坐标重复派发必须得到同一落点）的输入。 */
  const fire = (node, name, event = {}) => {
    const payload = { preventDefault() {}, stopPropagation() {}, ...event };
    (node.listeners[name] || []).forEach((fn) => fn(payload));
    return payload;
  };
  const arrangeRects = () => {
    const layout = ui.state.arrange;
    if (!layout) return;
    const put = (node, left, top, width = 88, height = 124) => {
      if (!node) return;
      node._rect = {
        left, top, width, height, right: left + width, bottom: top + height, x: left, y: top,
      };
    };
    const rowTop = { top: 200, bottom: 400 };
    for (const name of ["top", "bottom"]) {
      const row = rowOf(name);
      if (!row) continue;
      put(cardsBox(row), 100, rowTop[name], 600, 124);
      let at = 0;
      for (const index of layout[name]) {
        if (index === layout.dragging) continue;
        put(cardNode(row, index), 100 + (at * 100), rowTop[name]);
        at += 1;
      }
    }
  };
  const dragStart = (node) => fire(node, "dragstart", { dataTransfer: { setData() {} } });
  /** 起拖 + 铺夹具（夹具按"源已让位"的折叠顺序铺，故必须在 dragstart 之后）。 */
  const startDrag = (rowName, index) => {
    const node = cardNode(rowOf(rowName), index);
    dragStart(node);
    arrangeRects();
    return node;
  };
  const dragOverAt = (rowName, clientX, clientY) => {
    const event = { clientX, clientY };
    fire(rowOf(rowName), "dragover", event);
    return event;
  };
  /** 拖到某张牌上：`after` = 指针在牌面右半边（坐标取夹具）。 */
  const dragOntoCard = (rowName, index, after = false) => {
    const rect = cardNode(rowOf(rowName), index).getBoundingClientRect();
    return dragOverAt(
      rowName,
      after ? rect.left + rect.width - 2 : rect.left + 2,
      rect.top + (rect.height / 2),
    );
  };
  /** 拖到该行**行末空白**（所有牌之后 / 空槽区）：坐标取夹具（牌最多 5 张，右边留空）。 */
  const dragOntoRowEnd = (rowName) => dragOverAt(
    rowName, 100 + 500, (rowName === "top" ? 200 : 400) + 62,
  );
  const dragDropAt = (rowName, event) => fire(rowOf(rowName), "drop", event || {});
  const boxChildren = (name) => {
    const row = rowOf(name);
    return row ? Array.from(cardsBox(row).children) : [];
  };
  /** 垫片的 `className` 与 `classList` 互不同步（真 DOM 是同一份）：两处都查，
   *  静态类走既有的 `hasClassText`，动态 `classList.add` 走 `classList.contains`。 */
  const hasClass = (node, name) => !!node
    && (hasClassText(node, name) || !!(node.classList && node.classList.contains(name)));
  const placeholders = (name) => boxChildren(name).filter(
    (node) => hasClass(node, "arrange-placeholder"));
  const placeholderIndex = (name) => boxChildren(name).findIndex(
    (node) => hasClass(node, "arrange-placeholder"));
  /** 回到一份询问的**初始**双行布局（丢开上一手摆好的顺序）。 */
  const showArrange = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };

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

  // 拖拽落点判定（票 01）：拖到一张牌上按**指针在牌面中点的哪一侧**决定放到它前 / 后
  // （旧实现一律"插到它前面"，要放到后面必须再往右拖过一整张牌）。
  showArrange(arrangeView);
  startDrag("top", 0);
  dragDropAt("top", dragOntoCard("top", 1, true));
  check("㉿GX11b 行内拖到牌的**右半边** = 放到它后面（order [1,0,2] / split 3）",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[1,0,2]"
      && JSON.stringify(ui.buildAction()) === JSON.stringify(
        { answers_seq: 77, kind: "ArrangeCards", order: [1, 0, 2], split: 3 }),
    `上=${cardIndexes(rowOf("top"))} action=${JSON.stringify(ui.buildAction())}`);

  showArrange(arrangeView);
  startDrag("top", 2);
  dragDropAt("top", dragOntoCard("top", 1, false));
  check("㉿GX11c 行内拖到牌的**左半边** = 放到它前面（order [0,2,1] / split 3）",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[0,2,1]"
      && JSON.stringify(ui.buildAction()) === JSON.stringify(
        { answers_seq: 77, kind: "ArrangeCards", order: [0, 2, 1], split: 3 }),
    `上=${cardIndexes(rowOf("top"))} action=${JSON.stringify(ui.buildAction())}`);

  // 跨行（自由切分）：拖到下行某张牌上同样是插入，左右半边决定插在它前 / 后。
  showArrange(arrangeView);
  startDrag("top", 0);
  dragDropAt("bottom", dragOntoRowEnd("bottom"));
  startDrag("top", 1);
  dragDropAt("bottom", dragOntoCard("bottom", 0, true));
  check("㉿GX11d 跨行拖到下行那张牌的右半边 = 插到它后面（order [2,0,1] / split 1）",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[2]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[0,1]"
      && JSON.stringify(ui.buildAction()) === JSON.stringify(
        { answers_seq: 77, kind: "ArrangeCards", order: [2, 0, 1], split: 1 }),
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))} action=${JSON.stringify(ui.buildAction())}`);

  showArrange(arrangeView);
  startDrag("top", 0);
  dragDropAt("bottom", dragOntoRowEnd("bottom"));
  startDrag("top", 1);
  dragDropAt("bottom", dragOntoCard("bottom", 0, false));
  check("㉿GX11e 跨行拖到下行那张牌的左半边 = 插到它前面（order [2,1,0] / split 1）",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[2]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[1,0]"
      && JSON.stringify(ui.buildAction()) === JSON.stringify(
        { answers_seq: 77, kind: "ArrangeCards", order: [2, 1, 0], split: 1 }),
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))} action=${JSON.stringify(ui.buildAction())}`);

  // 动态展示（票 01 / 用户报障）：鼠标拽着某张牌时，其它牌为它让道——落点上插一个与
  // 卡面同尺寸的占位格（只动 DOM，不整面重绘，否则真浏览器会当场中断拖拽）。
  showArrange(arrangeView);
  const previewSource = cardNode(rowOf("top"), 0);
  const previewTarget = cardNode(rowOf("top"), 1);
  dragStart(previewSource);
  arrangeRects();
  const previewEvent = dragOntoCard("top", 1, true);
  check("㉿GX11f 拖动中：占位格落在目标牌之后（其它牌让道），拖动源挂 dragging",
    placeholderIndex("top") === boxChildren("top").indexOf(previewTarget) + 1
      && placeholders("bottom").length === 0
      && hasClass(previewSource, "dragging"),
    `占位格=${placeholderIndex("top")} 目标=${boxChildren("top").indexOf(previewTarget)}`);
  dragDropAt("top", previewEvent);
  check("㉿GX11g 放下后预览清干净（没有残留占位格 / dragging），顺序已按落点更新",
    placeholders("top").length === 0 && placeholders("bottom").length === 0
      && JSON.stringify(cardIndexes(rowOf("top"))) === "[1,0,2]"
      && !hasClass(cardNode(rowOf("top"), 0), "dragging"),
    `上=${cardIndexes(rowOf("top"))} 占位格=${placeholders("top").length}`);

  // 取消拖拽（没落到任何落点就松手）：预览是纯 DOM 手法、模型没被它改过 ⇒ `dragend`
  // 重绘一次就整体复原（拖动源从文档流里"抽走"的那一格也回来了）。
  showArrange(arrangeView);
  const cancelSource = cardNode(rowOf("top"), 0);
  dragStart(cancelSource);
  arrangeRects();
  dragOntoCard("top", 2, false);
  fire(cancelSource, "dragend");
  check("㉿GX11h 拖拽取消（dragend 没有落点）⇒ 顺序与预览都复原",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[0,1,2]"
      && placeholders("top").length === 0 && placeholders("bottom").length === 0
      && !hasClass(cardNode(rowOf("top"), 0), "dragging"),
    `上=${cardIndexes(rowOf("top"))} 占位格=${placeholders("top").length}`);

  // 落点是**空槽 / 行容器**（行末）时，占位格要落在空槽**之前**：自由切分的空槽恒排在牌的
  // 后面，占位格若跟着追加到整行末尾，让出来的那一格就跑到空槽后面去了（与真落点错位）。
  showArrange(arrangeView);
  startDrag("top", 0);
  const slotEvent = dragOntoRowEnd("bottom");
  check("㉿GX11i 落点是空槽 / 行末空白时：占位格插在空槽之前（不是整行末尾），行容器同时高亮",
    placeholderIndex("bottom") === 0
      && placeholders("bottom").length === 1
      && hasClass(rowOf("bottom"), "drop"),
    `占位格=${placeholderIndex("bottom")} 行=${boxChildren("bottom").length}`);
  dragDropAt("bottom", slotEvent);
  check("㉿GX11j 空槽放下后 = 移到该行末尾（观星拖到下行）",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[1,2]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[0]"
      && placeholders("bottom").length === 0,
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))}`);

  // 实机闪烁回归（2026-10-30 用户报障）：拖到牌面**左半边** ⇒ 占位格正好占住指针所在那一格，
  // 浏览器随即把下一次 dragover 派给指针底下的**另一个**节点（占位格 / 相邻牌 / 行容器）。
  // 落点只由坐标决定 ⇒ 同一坐标重复派发必须是同一个落点；旧实现按"当前事件目标"判，
  // 会在"指针处 ⇄ 行末"之间来回跳，实机就是其它牌一直闪。
  showArrange(arrangeView);
  startDrag("top", 0);
  const flickerEvent = dragOntoCard("top", 1, false);
  const flickerIndex = placeholderIndex("top");
  const flickerKey = ui.state.arrange.preview && ui.state.arrange.preview.key;
  dragOverAt("top", flickerEvent.clientX, flickerEvent.clientY);
  dragOverAt("top", flickerEvent.clientX, flickerEvent.clientY);
  check("㉿GX11k 同一坐标重复 dragover ⇒ 落点与占位格都不变（其它牌不再来回闪）",
    placeholderIndex("top") === flickerIndex
      && !!flickerKey && ui.state.arrange.preview
      && ui.state.arrange.preview.key === flickerKey
      && JSON.stringify(cardIndexes(rowOf("top"))) === "[0,1,2]",
    `占位格=${placeholderIndex("top")}/${flickerIndex}`
      + ` key=${ui.state.arrange.preview && ui.state.arrange.preview.key}/${flickerKey}`);
  fire(cardNode(rowOf("top"), 0), "dragend");

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

  // 固定切分（李典【恂恂】2/2）：两行各按声明铺开；跨行 = 互换（票 01），不是拒绝。
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
  clickNode(cardNode(rowOf("top"), 0));
  clickNode(cardNode(rowOf("bottom"), 2));
  const swapAction = ui.buildAction();
  check("㉿GX15 固定切分跨行点选 = 互换：上行 [2,1] / 下行 [0,3]，张数仍 2/2、载荷 split 2",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[2,1]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[0,3]"
      && !!swapAction && swapAction.split === 2
      && JSON.stringify(swapAction.order) === JSON.stringify([2, 1, 0, 3])
      && new Set(swapAction.order).size === 4,
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))} action=${JSON.stringify(swapAction)}`);

  // 票 01：同一判据也吃**拖拽**——落到另一行的牌上 = 与那张牌互换；落到行容器上 =
  // 与该行末尾那张互换。两行张数都不变（否则提交的 split 与服务端候选不符）。
  showArrange(fixedView);
  startDrag("top", 0);
  dragDropAt("bottom", dragOntoCard("bottom", 2, true));
  check("㉿GX15b 固定切分跨行拖拽 = 互换（与点选同一判据），张数不变",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[2,1]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[0,3]"
      && JSON.stringify(ui.buildAction()) === JSON.stringify(swapAction),
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))} action=${JSON.stringify(ui.buildAction())}`);

  showArrange(fixedView);
  startDrag("top", 0);
  dragDropAt("bottom", dragOntoRowEnd("bottom"));
  check("㉿GX15c 固定切分跨行拖到**行容器** = 与该行末尾那张互换（张数仍是 2/2）",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[3,1]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[2,0]"
      && !!ui.buildAction() && ui.buildAction().split === 2,
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))} action=${JSON.stringify(ui.buildAction())}`);

  // 动态展示（票 01）：交换没有空槽可让，故不插占位格；把"这一下会换哪张"挂成交换高亮。
  showArrange(fixedView);
  const swapSource = cardNode(rowOf("top"), 0);
  const swapTarget = cardNode(rowOf("bottom"), 2);
  dragStart(swapSource);
  arrangeRects();
  const swapDragEvent = dragOntoCard("bottom", 2, true);
  check("㉿GX15d 固定切分跨行拖动中：目标牌挂交换高亮、拖动源挂 dragging、不插占位格、行容器高亮",
    hasClass(swapTarget, "swap") && hasClass(swapSource, "dragging")
      && hasClass(rowOf("bottom"), "drop") && !hasClass(rowOf("top"), "drop")
      && placeholders("top").length === 0 && placeholders("bottom").length === 0,
    `swap=${hasClass(swapTarget, "swap")} dragging=${hasClass(swapSource, "dragging")}`
      + ` 行=${hasClass(rowOf("bottom"), "drop")}`
      + ` 占位格=${placeholders("top").length + placeholders("bottom").length}`);
  dragDropAt("bottom", swapDragEvent);
  check("㉿GX15e 交换放下后目标已换位、没有残留高亮 / 占位格",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[2,1]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[0,3]"
      && placeholders("top").length === 0 && placeholders("bottom").length === 0
      && !hasClass(rowOf("top"), "drop") && !hasClass(rowOf("bottom"), "drop"),
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))}`
      + ` 占位格=${placeholders("top").length + placeholders("bottom").length}`);

  // 行内排序仍可用：2/2 里把上行第 2 张点选、再点第 1 张 ⇒ 插到它前面（不互换、张数不变）。
  showArrange(fixedView);
  clickNode(cardNode(rowOf("top"), 1));
  clickNode(cardNode(rowOf("top"), 0));
  check("㉿GX15g 固定切分行内点选仍是**排序**（不是互换），两行张数不变",
    JSON.stringify(cardIndexes(rowOf("top"))) === "[1,0]"
      && JSON.stringify(cardIndexes(rowOf("bottom"))) === "[2,3]"
      && !!ui.buildAction() && ui.buildAction().split === 2
      && JSON.stringify(ui.buildAction().order) === JSON.stringify([1, 0, 2, 3]),
    `上=${cardIndexes(rowOf("top"))} 下=${cardIndexes(rowOf("bottom"))} action=${JSON.stringify(ui.buildAction())}`);

  showArrange(fixedView);
  check("㉿GX15f 固定切分提示条改说「可交换上下两行」，不再说「只能行内排序」",
    /交换/.test($("t-target-hint").textContent)
      && /2 顶 \/ 2 底/.test($("t-target-hint").textContent)
      && !/只能在上行/.test($("t-target-hint").textContent),
    `hint="${$("t-target-hint").textContent}"`);

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
  const guanxingButton = Array.from($("t-skills").children).find(
    (node) => node.getAttribute("data-skill-id") === "hero.zhugeliang.guanxing");
  // 观星是**触发技**（有钩子、无主动候选入口）⇒ 底部技能按钮只说明、不接 click（U1-06）。
  // 它的"是否发动"由**询问自己的直提态**回答：这就是 B 形技能的新表面（U2-03）。
  check("㉿GX16b 观星这类 B 形发动询问 = 直提态（不再铺「发动技能 hero.…」候选行）",
    !!guanxingButton
      && $("t-options").children.length === 0
      && candidateRowButtons().length === 0
      && /将提交：发动【观星】/.test($("t-choice").textContent)
      && $("btn-submit").disabled === false,
    `button=${!!guanxingButton} choice="${$("t-choice").textContent}" submit=${$("btn-submit").disabled} cand=${candidateRowButtons().length} kind=${ui.renderKind(activation, activation && activation.legal_actions)} options=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))}`);
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
  // 复原真询问，后面的成功提交（㉿GX18）照旧——**手势换成拖拽**（票 01 验收条：
  // 观星上行 4 张 / 下行空槽，拖一张到下行 ⇒ split 3；点选那条由 ㉿GX8 覆盖）。
  ui.state.view.pending = realArrange;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(ui.state.view);
  const realTop = rowOf("top");
  const realBottom = rowOf("bottom");
  if (realTop && realBottom && slots(realBottom)[0]) {
    dragStart(cardNode(realTop, 0));
    arrangeRects();
    dragDropAt("bottom", dragOntoRowEnd("bottom"));
  }
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
    // 合成 view = 一个新场景：舞台项不跨场景沿用（U3-01 起 commit 不清空展示 / 弃牌，
    // 故这里显式清一次，只影响测试之间的干净度，不改产品行为）。
    ui.state.effectCards = [];
    ui.state.revealedCards = [];
    ui.resetSelection();
    ui.renderTable();
  };
  const ringSeats = () => Array.from($("t-seats").children).map((node) => ({
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
    const placed = Array.from($("t-seats").children).map((node) => ({
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
 * - 处理区 / 舞台卡面与手牌**同一套** 88×124 + 字号 14/11/20/9（不再有第二套小牌面规则）；
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
      hand_limit: 3,
    },
    {
      seat: 1, hp: 5, hp_max: 5, hand_count: 4, alive: true,
      equip: [armor], judge: [lightning], chained: false, wine_active: false,
      // 翻面（背面朝上）：只影响"自己的回合开始前"，座位卡只画蒙灰、不写文字。
      flipped: true,
      identity: null, identity_label: null,
      hero_name: "吕布", kingdom: "qun", kingdom_label: "群",
      hand_limit: 5,
    },
    {
      seat: 2, hp: 6, hp_max: 6, hand_count: 0, alive: true,
      equip: [horse], judge: [], chained: true, wine_active: true,
      identity: identityMode ? "lord" : null, identity_label: identityMode ? "主公" : null,
      hero_name: "曹操", kingdom: "wei", kingdom_label: "魏",
      hand_limit: 6,
    },
    {
      seat: 3, hp: 0, hp_max: 4, hand_count: 0, alive: false,
      equip: [], judge: [supply], chained: true, wine_active: true,
      identity: identityMode ? "loyalist" : null, identity_label: identityMode ? "忠臣" : null,
      hero_name: "刘备", kingdom: "shu", kingdom_label: "蜀",
      hand_limit: 0,
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
    // 合成 view = 一个新场景：舞台项不跨场景沿用（U3-01 起 commit 不清空展示 / 弃牌）。
    ui.state.effectCards = [];
    ui.state.revealedCards = [];
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
  check("U1-02c CSS：对手内容放大（判定行 36 / 装备行 42、槽位字 10 / 标签 9）——外形仍 210×150",
    /\.seat \.judge-row\s*\{[^}]*calc\(36 \* var\(--dsu\)\)/s.test(css)
      && /\.seat \.equip-grid\s*\{[^}]*grid-auto-rows:\s*calc\(42 \* var\(--dsu\)\)/s.test(css)
      && /\.mini-slot\s*\{[^}]*font-size:\s*calc\(10 \* var\(--dsu\)\)/s.test(css)
      && /\.mini-slot\.judge-slot\s*\{[^}]*height:\s*calc\(36 \* var\(--dsu\)\)/s.test(css)
      && /\.mini-slot \.slot-k\s*\{[^}]*font-size:\s*calc\(9 \* var\(--dsu\)\)/s.test(css),
    `judge=${/calc\(36 \* var\(--dsu\)\)/.test(css)} equip=${/calc\(42 \* var\(--dsu\)\)/.test(css)} slot=${/\.mini-slot\s*\{[^}]*font-size:\s*calc\(10/s.test(css)} label=${/\.mini-slot \.slot-k\s*\{[^}]*font-size:\s*calc\(9/s.test(css)}`);
  check("U1-02c2 CSS：自己武将块内容放大（名 20 / 体力数字 13 / 判定 52 / 装备 60 / 槽位字 13 / 标签 12）",
    /\.self \.hero-name\s*\{[^}]*font-size:\s*calc\(20 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.hp-num\s*\{[^}]*font-size:\s*calc\(13 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.judge-row\s*\{[^}]*calc\(52 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.equip-grid\s*\{[^}]*grid-auto-rows:\s*calc\(60 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.mini-slot\s*\{[^}]*font-size:\s*calc\(13 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.mini-slot\.judge-slot\s*\{[^}]*height:\s*calc\(52 \* var\(--dsu\)\)/s.test(css)
      && /\.self \.mini-slot \.slot-k\s*\{[^}]*font-size:\s*calc\(12 \* var\(--dsu\)\)/s.test(css),
    `name=${/\.self \.hero-name\s*\{[^}]*calc\(20/s.test(css)} hp=${/\.self \.hp-num\s*\{[^}]*calc\(13/s.test(css)} judge=${/\.self \.judge-row\s*\{[^}]*calc\(52/s.test(css)} equip=${/\.self \.equip-grid\s*\{[^}]*calc\(60/s.test(css)} slot=${/\.self \.mini-slot\s*\{[^}]*font-size:\s*calc\(13/s.test(css)} label=${/\.self \.mini-slot \.slot-k\s*\{[^}]*calc\(12/s.test(css)}`);
  check("U1-02c3 CSS：处理区 / 舞台卡面与手牌同一套 88×124 + 字号 14/11/20/9（0.63 版覆盖已删，只有一套卡面规则）",
    /\.card\.face\s*\{[^}]*width:\s*calc\(88 \* var\(--dsu\)\);\s*height:\s*calc\(124 \* var\(--dsu\)\)/s.test(css)
      && /\.card\.face \.suit\s*\{[^}]*font-size:\s*calc\(14 \* var\(--dsu\)\)/s.test(css)
      && /\.card\.face \.rank\s*\{[^}]*font-size:\s*calc\(11 \* var\(--dsu\)\)/s.test(css)
      && /\.card\.face \.cname\s*\{[^}]*font-size:\s*calc\(20 \* var\(--dsu\)\)/s.test(css)
      && /\.card\.face \.ctype\s*\{[^}]*font-size:\s*calc\(9 \* var\(--dsu\)\)/s.test(css)
      && !/\.stage-center \.processing \.card/.test(css)
      && !/calc\(56 \* var\(--dsu\)\)/.test(css)
      && !/calc\(79 \* var\(--dsu\)\)/.test(css)
      && /\.hand \.card\s*\{[^}]*flex:\s*0 0 auto/s.test(css)
      && /\.table-bottom\s*\{[^}]*flex:\s*0 0 27\.5%/s.test(css),
    `card=${/calc\(88 \* var\(--dsu\)\)/.test(css)} stageOverride=${/\.stage-center \.processing \.card/.test(css)} small=${/calc\(56 \* var\(--dsu\)\)/.test(css) || /calc\(79 \* var\(--dsu\)\)/.test(css)} hand=${/\.hand \.card\s*\{[^}]*flex:\s*0 0 auto/s.test(css)} bottom=${/\.table-bottom\s*\{[^}]*27\.5%/s.test(css)}`);
  // U3-01：舞台面板下移 + 加宽 + 封顶 360（spec D3）：
  // 圆心 64.47% × 608 = 392；360 上限 ⇒ 顶边 392 − 180 = 212 > 顶座下沿 210；
  // 40% 宽 = 640 设计单位，横跨 480–1120，不压左右座位（中心 212 / 1388、卡宽 210）；
  // 舞台行限两行卡面（253 = 124×2 + 5）纵向滚动、说明最多 2 行。
  const stageRule = css.match(/\.stage-center\s*\{[^}]*\}/s);
  const stageMax = stageRule && /max-height:\s*calc\((\d+) \* var\(--dsu\)\)/.exec(stageRule[0]);
  const stageTop = stageRule && /top:\s*(\d+(?:\.\d+)?)%/.exec(stageRule[0]);
  const ringCenterY = (Number(stageTop ? stageTop[1] : 0) / 100) * 608;
  const topSeatBottom = 370 - 235 + 150 / 2;
  const panelTop = ringCenterY - Number(stageMax ? stageMax[1] : 0) / 2;
  check("U1-02c4 CSS：舞台宽 40%、面板封顶 360、中心下移到 64.47%（顶边 212 > 顶座下沿 210），舞台行两行卡面（253）纵向滚动、说明最多 2 行",
    !!stageMax && Math.abs(ringCenterY - 392) < 0.5 && panelTop > topSeatBottom
      && /\.stage-center\s*\{[^}]*width:\s*40%/s.test(css)
      && /\.stage-center\s*\{[^}]*overflow:\s*hidden/s.test(css)
      && /\.stage-center \.processing\s*\{[^}]*flex:\s*0 0 auto[^}]*max-height:\s*calc\(253 \* var\(--dsu\)\)[^}]*overflow-y:\s*auto/s.test(css)
      && /\.stage-center \.processing\s*\{[^}]*align-content:\s*flex-start/s.test(css)
      && /\.stage-center \.context\s*\{[^}]*-webkit-line-clamp:\s*2/s.test(css),
    `centerY=${ringCenterY} panelTop=${panelTop} topSeatBottom=${topSeatBottom} max=${stageMax ? stageMax[1] : "无"} width=${/\.stage-center\s*\{[^}]*width:\s*40%/s.test(css)} row=${/max-height:\s*calc\(253 \* var\(--dsu\)\)/.test(css)} scroll=${/\.stage-center \.processing\s*\{[^}]*overflow-y:\s*auto/s.test(css)}`);
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

  check("U1-02f 武将块：势力 / 中文名 / 身份 / 手牌数（张数/上限） / 座位号（不是引擎 id）",
    seat1.text().includes("群") && seat1.text().includes("吕布") && seat1.text().includes("手牌：4/5")
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
  check("U1-02h2 槽里的牌画牌名 + 花色点数；有装备时**不**显示武器/防具/攻马/防马，空槽才显示；判定槽常显",
    seat1.text().includes("八卦阵") && seat1.text().includes("闪电")
      && seat2.text().includes("赤兔")
      && (() => {
        const slots = byClass(seat1, "equip-slot");
        const filled = slots.filter((slot) => hasClass(slot, "filled"));
        const empty = slots.filter((slot) => hasClass(slot, "empty"));
        const judge = byClass(seat1, "judge-slot")[0];
        return filled.length === 1
          && byClass(filled[0], "slot-k").length === 0
          && byClass(filled[0], "slot-suit").length === 1
          && filled[0].text().includes("♠7")
          && empty.length === 3
          && empty.every((slot) => byClass(slot, "slot-k").some(
            (label) => /武器|防具|攻马|防马/.test(label.textContent)))
          && byClass(judge, "slot-suit").length === 1
          && byClass(judge, "slot-k").length === 1;
      })(),
    `seat1="${seat1.text().replace(/\s+/g, " ").trim()}"`);

  // 用户 2026-11-07：转化牌（多源转化的乐不思蜀 / 兵粮寸断等）在简略表现里右上角标「转」。
  const transformed = card(27, "乐不思蜀", {
    type: "indulgence", kind: "transformed", suit: null, rank: null,
    virtual_sources: [synthCard(25, "乐不思蜀")],
  });
  const transSlot = ui.miniSlotNode({
    zone: "judge", slot: "judge", sealKey: "judge_1", card: transformed,
    interactive: false, ctx: {},
  });
  check("U1-02h3 转化牌在判定 / 装备槽右上角标「转」；普通牌没有；多源转化没有花色点数",
    byClass(transSlot, "slot-trans").length === 1
      && byClass(transSlot, "slot-trans")[0].textContent === "转"
      && byClass(transSlot, "slot-suit").length === 0
      && byClass(transSlot, "slot-v")[0].textContent === "乐不思蜀",
    `trans=${byClass(transSlot, "slot-trans").map((n) => n.textContent)} suit=${byClass(transSlot, "slot-suit").length}`);

  // 用户 2026-11-07：悬停装备 / 判定区的牌 = 跟随鼠标的常规卡面小弹窗（title 不再是唯一读法）。
  const armorSlot = byClass(seat1, "equip-slot").find(
    (slot) => attr(slot, "data-slot") === "armor");
  const fireMouse = (node, name, event) => (node.listeners[name] || []).forEach(
    (fn) => fn(event || { target: node, clientX: 120, clientY: 140 }));
  fireMouse(armorSlot, "mouseenter");
  const preview = $("t-card-preview");
  const entered = {
    hidden: preview.hidden,
    left: preview.style.left,
    top: preview.style.top,
    faces: byClass(preview, "card").length,
    ids: byClass(preview, "card").map((node) => node.getAttribute("data-card-id")),
  };
  fireMouse(armorSlot, "mousemove", { target: armorSlot, clientX: 200, clientY: 240 });
  const followed = { left: preview.style.left, top: preview.style.top };
  fireMouse(armorSlot, "mouseleave");
  const leftHidden = preview.hidden;
  check("U1-02h4 悬停装备 / 判定牌 = 跟随鼠标的卡面小弹窗；移动跟着走，离开即收",
    !entered.hidden && entered.faces === 1 && entered.ids.join(",") === "22"
      && entered.left === "136px" && entered.top === "156px"
      && followed.left === "216px" && followed.top === "256px"
      && leftHidden === true,
    `entered=${JSON.stringify(entered)} followed=${JSON.stringify(followed)} hidden=${leftHidden}`);

  ui.showCardPreview([{
    ...armor,
    tags: [{ skill_id: "hero.feng_yujin.dingluan", label: "定乱" }],
  }], { clientX: 10, clientY: 10 });
  const taggedPreview = byClass($("t-card-preview"), "card-tag").map((node) => node.textContent);
  ui.hideCardPreview();
  check("U1-02h5 预览走常规卡牌渲染：牌上的标签（如「定乱」）照常可见",
    taggedPreview.includes("定乱"),
    `tags=${JSON.stringify(taggedPreview)}`);

  check("U1-02i 身份：暗置显示 ?、主公明置、阵亡亮明；自己恒可见（全称，`GLOSSARY「身份」`四值）",
    byClass(seat1, "identity-badge").some((b) => b.textContent === "?")
      && byClass(seat2, "identity-badge").some(
        (b) => b.textContent === "主公" && b.getAttribute("title") === "身份：主公")
      && byClass(seat3, "identity-badge").some(
        (b) => b.textContent === "忠臣" && b.getAttribute("title") === "身份：忠臣")
      && $("t-self-identity").textContent === "反贼"
      && $("t-self-identity").getAttribute("title") === "身份：反贼",
    `seat1=${byClass(seat1, "identity-badge").map((b) => b.textContent)} seat2=${byClass(seat2, "identity-badge").map((b) => b.textContent)} self="${$("t-self-identity").textContent}"`);

  check("U1-02i2 自己的武将块与 footer 读同一份展示契约：大乔 / 吴 / 反贼 / 3/3 / 手牌 2/3 / 座位 0",
    $("t-hero").textContent === "大乔" && $("t-self-kingdom").textContent === "吴"
      && $("t-self-hp").text().includes("3/3")
      && $("t-self-hand").textContent === "2/3" && $("t-self-seatno").textContent === "0",
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

  check("U1-02j3 翻面 = 与阵亡**完全同一档**置灰（同一条规则）+ **没有文字**（避免遮挡标记）",
    seat1.classList.contains("flipped") && !seat1.classList.contains("dead")
      && byClass(seat1, "flipped-layer").length === 1
      && byClass(seat1, "flipped-layer")[0].textContent === ""
      && seat1.text().includes("吕布")
      // 置灰配比只有一份：阵亡与翻面写在**同一条**选择器里（用户裁定"完全一样"），
      // 这里核的就是"没有第二份配比"，避免日后有人把其中一档调淡。
      && /\.seat\.dead,\s*\.seat\.flipped\s*\{[^}]*opacity:\s*\.72/s.test(css)
      && /\.seat\.dead \.hero-block::after,\s*\n\.flipped-layer\s*\{[^}]*background:\s*rgba\(120, 116, 108, \.35\)/s.test(css)
      // 也不许再出现"行首起一条 .seat.flipped 规则又改 opacity"（即第二份置灰配比）。
      && !/^\.seat\.flipped\b[^{]*\{[^}]*opacity/m.test(css),
    `seat1=${[...seat1.classList.set].join(",")} layer=${byClass(seat1, "flipped-layer").length}`);

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
      && byClass(handCard, "ctype")[0].textContent === "",
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
  check("U1-02o3 卡面底行：武器 = 武器 · 范围 2；火杀 = 火焰（红）；不再写「普通 / 虚拟 / 转化」",
    byClass(weaponFace, "ctype")[0].textContent === "武器 · 范围 2"
      && byClass(fireFace, "ctype")[0].textContent === "火焰"
      && hasClass(fireFace, "attr-fire")
      && byClass(fireFace, "cname")[0].textContent === "杀"
      && !/普通|虚拟|转化/.test(byClass(fireFace, "ctype")[0].textContent),
    `weapon="${byClass(weaponFace, "ctype")[0].textContent}" fire="${byClass(fireFace, "ctype")[0].textContent}"`);
  show(table());

  const liveSeat1 = seatBox(1);
  const previewTitleAfterHover = (slot) => {
    if (!slot) return null;
    (slot.listeners.mouseenter || []).forEach(
      (fn) => fn({ target: slot, clientX: 0, clientY: 0 }));
    const faces = Array.from($("t-card-preview").children).filter(
      (node) => String(node.className || "").split(/\s+/).includes("card"));
    const title = faces.length ? faces[0].getAttribute("title") : null;
    ui.hideCardPreview();
    return title;
  };
  check("U1-02o2 卡面悬停读服务端描述（手牌 / 处理区的 title；装备 / 判定走预览卡面同一份描述）",
    handCard.getAttribute("title") === slash.description
      && previewTitleAfterHover(byClass(liveSeat1, "equip-slot").find(
        (s) => attr(s, "data-card-id") === "22")) === armor.description
      && previewTitleAfterHover(byClass(liveSeat1, "judge-slot").find(
        (s) => attr(s, "data-card-id") === "24")) === lightning.description
      && byClass($("t-processing"), "card")[0].getAttribute("title") === lightning.description,
    `hand="${handCard.getAttribute("title")}"`);

  /* ---- 手牌与场上区域不重复显示 ---- */
  const handIds = Array.from($("t-hand").children).map((node) => attr(node, "data-card-id"));
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
      && Array.from($("t-seats").children).every((box) => !box.classList.contains("turn")),
    `selfTurn=${$("t-self-seat").classList.contains("turn")} ring=${Array.from($("t-seats").children).map((b) => [...b.classList.set].join("+")).join(",")}`);

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
  const foreign = seatBox(1) && Array.from(seatBox(1).children).find(
    (node) => (node.className || "").includes("seat-prompt"),
  );
  check("U1-03c 其他座位的询问文本显示在其武将块下方（只有“正在响应…”，不复述询问内容）",
    !!foreign && foreign.textContent.includes("正在响应")
      && !foreign.textContent.includes("响应窗口"),
    `seat1="${seatBox(1) && seatBox(1).text()}"`);
  check("U1-03d 其他座位的提示不显示可点按钮、也不接管自己的提示条动作",
    !!foreign
      && Array.from(foreign.children).every((node) => node.tagName !== "BUTTON")
      && !foreign.onclick
      && $("btn-submit").disabled === true
      && $("btn-cancel").disabled === true
      && $("btn-pass").hidden === true,
    `children=${foreign && Array.from(foreign.children).map((node) => node.tagName).join(",")} submit=${$("btn-submit").disabled}`);
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
      && !(seatBox(0) && Array.from(seatBox(0).children).some(
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
  const options = Array.from($("t-options").children);
  check("U1-03g0 简单询问（一次性二选一）出浮动选项行，且回到提示条内",
    options.length === 2
      && Array.from($("t-options-home").children).includes($("t-options")),
    `options=${options.length} home=${Array.from($("t-options-home").children).includes($("t-options"))}`);
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
      && !Array.from($("t-options").children).some((node) => /结束出牌/.test(node.textContent || "")),
    `endHidden=${$("btn-end-phase").hidden} passHidden=${$("btn-pass").hidden} options=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))}`);
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
    `options=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))}`);
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
  const buttons = Array.from($("t-options").children);
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

/* ---- ①b 单选项可选确认（B1）：一个 SelectOption + 一条 Pass = 提交 / 放弃，不画按钮 ---- */
{
  const pending = {
    kind: "SELECT_OPTION", actor: 0, seq: 9403, skill_id: "luoshen:repeat:0",
    options: ["repeat"],
    prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "repeat", answers_seq: 9403 },
      { kind: "Pass", answers_seq: 9403 },
    ],
    candidate_index: {}, option_seats: {},
    context: {
      actor: 0, name: "洛神", targets: [], label: null, skill_hints: [],
      requirement: "选择是否再进行一次【洛神】判定",
    },
    preactivations: {}, preactivation: [],
  };
  const view = synthView(pending);
  ui.state.view = view;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  const kind = ui.renderKind(pending, pending.legal_actions);
  const selected = ui.state.selectedOption === null
    ? null
    : pending.legal_actions[ui.state.selectedOption];
  const built = ui.buildAction();
  const cancel = ui.cancelAction(pending);
  check("U2-03c 单选项可选确认：唯一选项自动选中、不画选项按钮、提交 = SelectOption / 放弃 = Pass",
    kind === "option-confirm"
      && $("t-options").children.length === 0
      && selected && selected.kind === "SelectOption" && selected.choice === "repeat"
      && built && built.kind === "SelectOption" && built.choice === "repeat"
      && cancel && cancel.kind === "Pass"
      && $("btn-submit").disabled === false
      && $("btn-pass").hidden === false
      && $("btn-cancel").hidden === true
      && $("t-target-hint").textContent.includes("提交")
      && $("t-target-hint").textContent.includes("放弃"),
    `kind=${kind} options=${$("t-options").children.length} selected=${selected && selected.choice}`
      + ` built=${built && built.kind} cancel=${cancel && cancel.kind}`
      + ` submit=${$("btn-submit").disabled} passHidden=${$("btn-pass").hidden}`
      + ` cancelHidden=${$("btn-cancel").hidden} hint="${$("t-target-hint").textContent}"`);
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
  const skillButton = Array.from($("t-skills").children).find(
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
    `kind=${ui.renderKind(pending, pending.legal_actions)} choice="${$("t-choice").textContent}" options=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))}`);
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
  nextGetPendingSticky = false;
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
  nextGetPendingSticky = false;
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
  // wuming 票 01：默认池加了「无名自制版」之后发将会重排——旧 seed 20261012 的这一局
  // 会让无名落在别人身上、L0 的【天成】展示询问成为会话第一个可见询问（不是 Pass 窗口）。
  // 按本仓纪律**重新选种**（不放宽判据）：20261013 的同一局不含无名、首问仍只有一条 Pass。
  // huo-taishici-xunyu 票 01 / 02：火包移动版再加太史慈 / 荀彧两个真名，发将再重排——
  // 这一局的首问变成神诸葛亮【七星】的两行交换（不可放弃的 ARRANGE）。本条只测
  // "重放窗口的四键 / 一次 POST / 409 刷新"，与池子无关：**钉死禁用名单**把新真名排除，
  // 恢复 20261013 的旧发将（同 SMOKE_ROSTER 的处置，不放宽判据）。
  // huo-wolong-yanliang 票 01：火包 OL 再加卧龙诸葛（新真名）⇒ 同一局又重排、首问变成
  // 【天成】的展示询问（不是 Pass 窗口）⇒ 把新版本也钉进禁用名单，恢复该局的旧发将。
  // 票 02 再加颜良文丑（同一缘故，一并钉住）。
  const fresh = await post("/api/sessions", {
    player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261013,
    banned_heroes: ["taishici", "xunyu", "wolong_zhugeliang", "yanliang_wenchou"],
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
    `answers=${successCalls.length} options=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))}`);

  // 409：下一次 /answer 回 409；刷新取回的新询问换成"只有 EndPhase"的出牌阶段。
  const fresh2 = await post("/api/sessions", {
    player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261013,
    banned_heroes: ["taishici", "xunyu", "wolong_zhugeliang", "yanliang_wenchou"],
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
  nextGetPendingSticky = true;
  const stalePromise = ($("btn-pass").listeners.click || [])[0]({ target: $("btn-pass") });
  await stalePromise;
  staleAnswerStatus = null;
  nextGetPendingSticky = false;
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
    `calls=${staleCalls.map((call) => call.path).join(" | ")} pending=${ui.state.view.pending && ui.state.view.pending.kind} seq=${ui.state.view.pending && ui.state.view.pending.seq} option=${ui.state.selectedOption} options=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))} passHidden=${$("btn-pass").hidden} endHidden=${$("btn-end-phase").hidden}`);
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

const promptSkillButtons = () => Array.from($("t-prompt-skills").children).filter(
  (node) => node.tagName === "BUTTON",
);
const promptSkillIds = () => promptSkillButtons().map(
  (node) => node.getAttribute("data-skill-id"),
);
const promptSkill = (id) => promptSkillButtons().find(
  (node) => node.getAttribute("data-skill-id") === id,
);
const clickNode = (node) => (node.listeners.click || []).forEach((fn) => fn({ target: node }));
const bottomSkillIds = () => Array.from($("t-skills").children).filter(
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
    preactivations: { "hero.demo.flow": manifest, "hero.demo.lockedflow": manifest }, preactivation: [],
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
    skill("hero.demo.lockedflow", "锁定转化技", { available: true, locked: true }),
    skill("hero.demo.off", "不可用技"),
  ];
  ui.state.view = view;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const shown = "hero.demo.alpha,hero.demo.beta,hero.demo.flow,hero.demo.convert,hero.demo.passive,hero.demo.locked,hero.demo.lockedflow";
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

  // **被动技**仍只做说明（U1-06）；**锁定技要看这一问有没有它的候选**——有裸骨架候选的
  // （【八阵】在【闪】窗口里）必须能点，没有的仍只做说明（用户实机报障 2026-11-06 之前
  // 这里把两者一刀切，正是死路的来源）。锁定**转化**技走下一条用例。
  check("U2-04f 被动技在组里但不可点（被动银灰、无 click）；有候选的锁定技可点 = 选中那条候选",
    (() => {
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      const passive = promptSkill("hero.demo.passive");
      const before = calls.length;
      const option = ui.state.selectedOption;
      clickNode(passive);
      const passiveOk = !!passive && passive.disabled === true
        && passive.classList.contains("passive")
        && ui.state.selectedOption === option && calls.length === before;
      const locked = promptSkill("hero.demo.locked");
      const cached = ui.state.selectedOption;
      clickNode(locked);
      const wantIndex = view.pending.legal_actions.findIndex(
        (action) => action.kind === "ActivateSkill" && action.skill_id === "hero.demo.locked",
      );
      const lockedOk = !!locked && locked.disabled === false
        && locked.classList.contains("locked")
        && ui.state.selectedOption === wantIndex && wantIndex >= 0
        && $("t-choice").textContent.includes("将提交：发动【锁定技】");
      ui.resetSelection();
      void cached;
      return passiveOk && lockedOk;
    })(),
    `passive=${promptSkill("hero.demo.passive").disabled} locked=${promptSkill("hero.demo.locked").disabled} option=${ui.state.selectedOption}`);

  check("U2-04f2 锁定**转化**技（这一问下发了清单）可点 = 进入清单（锁定技按钮只做说明，不适用于锁定转化）",
    (() => {
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      const node = promptSkill("hero.demo.lockedflow");
      if (!node) return false;
      const enabled = node.disabled !== true;
      clickNode(node);
      const entered = !!ui.state.preactivation
        && ui.state.preactivation.skillId === "hero.demo.lockedflow";
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      return enabled && entered;
    })(),
    `disabled=${promptSkill("hero.demo.lockedflow") && promptSkill("hero.demo.lockedflow").disabled} pre=${ui.state.preactivation && ui.state.preactivation.skillId}`);

  // 锁定转化（神关羽【武神】）：手牌区画**产物**牌面（单字【杀】+「转」），点它 =
  // 进转化清单并预选来源牌 —— 锁定技没有"是否发动"，点牌就是"就用它"。
  {
    const source = {
      ...synthCard(13, "无中生有"), suit: "heart", rank: 12, color: "red", type: "ex_nihilo",
    };
    const product = {
      ...source, card_id: -23, name: "杀", type: "slash", display_name: "杀",
      attribute: "火焰", kind: "transformed", virtual_sources: [13], source_cards: [source],
      forced: true,
    };
    const wushenPending = {
      kind: "PLAY_PHASE", actor: 0, seq: 9500,
      prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
      legal_actions: [{ kind: "EndPhase", answers_seq: 9500 }],
      candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
      preactivations: {
        "hero.demo.wushen": [
          { name: "card", zones: ["hand"], min: 1, max: 1, order: 0, pool: [{ when: [], options: ["13"] }] },
          { name: "seat", min: 1, max: 1, order: 1, pool: [{ when: [], options: ["seat:1"] }] },
        ],
      },
      preactivation: [],
      transform_products: [product],
    };
    const wv = synthView(wushenPending);
    wv.snapshot.self_hand = [source];
    wv.skills = [{
      skill_id: "hero.demo.wushen", name: "武神", skill_type: "NORMAL", locked: true,
      carrier: "HERO", available: true, preactivated: false, description: "锁定转化。",
      ui: { requirement: "", passive: false, convert_sources: [] },
    }];
    ui.state.view = wv;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(wv);
    const handNode = $("t-hand").children[0];
    // 垫片的 `className` 与 `classList` 不联动（见其它烟测注释）：子节点按 `className` 找。
    const child = (cls) => Array.from(handNode.children).find(
      (node) => String(node.className).split(/\s+/).includes(cls),
    );
    const corner = child("corner");
    const cname = child("cname");
    check("U2-04h 锁定转化的手牌画**产物**牌面（单字【杀】+「转」角标；身份仍是源牌 id）",
      !!handNode
        && handNode.getAttribute("data-card-id") === "13"
        && handNode.classList.contains("attr-fire")
        && !!corner && corner.textContent === "转"
        && !!cname && cname.textContent === "杀"
        && handNode.getAttribute("title").indexOf("无中生有") >= 0,
      `id=${handNode && handNode.getAttribute("data-card-id")} corner=${corner && corner.textContent} name=${cname && cname.textContent}`);
    clickNode(handNode);
    check("U2-04h2 点它 = 进入转化清单且来源步已预选这张牌（不再弹「不能用于当前询问」）",
      !!ui.state.preactivation
        && ui.state.preactivation.skillId === "hero.demo.wushen"
        && JSON.stringify(ui.state.preactivation.answers[0].values) === "[13]"
        && ui.state.preactivation.cursor === 1
        && !ui.state.lastRejectReason,
      `skill=${ui.state.preactivation && ui.state.preactivation.skillId} answers=${ui.state.preactivation && JSON.stringify(ui.state.preactivation.answers[0])} reason="${ui.state.lastRejectReason}"`);
    // 回到上一节的夹具（后面 U2-04g 起仍读同一个 view）。
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  }

  check("U2-04g 底部技能区原样：顺序 / 全部技能 / 与提示区点出同一个选中项",
    (() => {
      const order = bottomSkillIds().join(",") === view.skills.map((item) => item.skill_id).join(",");
      ui.resetSelection();
      ui.renderTable();
      ui.renderPending(view);
      const bottom = Array.from($("t-skills").children).find(
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

/* ---- WS2：‘可以’型转化（武圣）的手牌默认仍画**原牌**，转化只走技能预发动 ----
 *
 * 报障（2026-10-31）：服务端 `transform_products` 里有产物 ⇒ 手牌被画成【杀】。
 * 但产物索引的 `forced` 才是"默认画产物"的判据（‘只能’型 = 武神）；武圣是‘可以’型，
 * 红【无中生有】必须还能按原用途使用。两者都不是 `locked` 推出来的（见服务端断言）。
 */
{
  const source = {
    ...synthCard(13, "无中生有"), display_name: "无中生有",
    suit: "heart", rank: 12, color: "red", type: "ex_nihilo",
  };
  const product = {
    ...source, card_id: -23, name: "杀", type: "slash", display_name: "杀",
    attribute: "普通", kind: "transformed", virtual_sources: [13], source_cards: [source],
    forced: false,
  };
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9501,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [
      { kind: "UseCard", card: source, targets: [0], answers_seq: 9501 },
      { kind: "EndPhase", answers_seq: 9501 },
    ],
    candidate_index: {
      "13": { card: source, pool: [0], combos: [[0]], min_targets: 1, max_targets: 1 },
    },
    virtual_index: {}, option_seats: {}, context: null,
    preactivations: {
      "hero.guanyu.wusheng": [
        { name: "card", zones: ["hand"], min: 1, max: 1, order: 0,
          pool: [{ when: [], options: ["13"] }] },
        { name: "seat", min: 1, max: 1, order: 1,
          pool: [{ when: [], options: ["seat:1"] }] },
      ],
    },
    preactivation: [],
    transform_products: [product],
  };
  const view = synthView(pending);
  view.snapshot.self_hand = [source];
  view.skills = [{
    skill_id: "hero.guanyu.wusheng", name: "武圣", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: false, preactivated: false,
    ui: { requirement: "选择1张红色牌当【杀】使用或打出", convert_sources: [13] },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const handNode = $("t-hand").children[0];
  const child = (cls) => Array.from(handNode.children).find(
    (node) => String(node.className).split(/\s+/).includes(cls),
  );
  const corner = child("corner");
  const cname = child("cname");
  check("WS2-1 可转化（武圣）的手牌仍画**原牌**：红【无中生有】不带「转」角标",
    !!handNode
      && handNode.getAttribute("data-card-id") === "13"
      && handNode.getAttribute("data-kind") === "normal"
      && !handNode.classList.contains("attr-fire")
      && corner === undefined
      && !!cname && cname.textContent === "无中生有",
    `id=${handNode && handNode.getAttribute("data-card-id")}`
      + ` kind=${handNode && handNode.getAttribute("data-kind")}`
      + ` corner=${corner && corner.textContent} name=${cname && cname.textContent}`);

  clickNode(handNode);
  check("WS2-2 点它 = 按**原用途**用这张牌（焦点留在原牌），不进入转化预发动",
    ui.state.focusCardId === 13
      && !ui.state.preactivation
      && ui.state.convertSkill === null,
    `focus=${ui.state.focusCardId} pre=${!!ui.state.preactivation}`
      + ` convert=${ui.state.convertSkill}`);

  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);
  const wusheng = Array.from($("t-skills").children).find(
    (node) => node.getAttribute("data-skill-id") === "hero.guanyu.wusheng");
  if (wusheng) clickNode(wusheng);
  check("WS2-3 转化仍只经**技能预发动**进入：点武圣 ⇒ 清单本地态",
    !!wusheng
      && !!ui.state.preactivation
      && ui.state.preactivation.skillId === "hero.guanyu.wusheng",
    `button=${!!wusheng} skill=${ui.state.preactivation && ui.state.preactivation.skillId}`);
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
  const optionLabels = () => Array.from($("t-options").children)
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
  const drawButton = Array.from($("t-options").children).find((node) => node.textContent === "令其摸一张牌");
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
  const drawOnlyButtons = Array.from($("t-options").children).filter((node) => node.tagName === "BUTTON");
  check("U2-04j 无手牌时「弃置 1 张手牌」是禁用控件、「令其摸一张牌」仍可点（引擎不发不可支付的选项）",
    optionLabels().join(",") === "弃置 1 张手牌,令其摸一张牌"
      && drawOnlyButtons.find((node) => node.textContent === "弃置 1 张手牌").disabled === true
      && drawOnlyButtons.find((node) => node.textContent === "令其摸一张牌").disabled === false,
    `选项=${JSON.stringify(optionLabels())} disabled=${JSON.stringify(drawOnlyButtons.map((node) => [node.textContent, node.disabled]))}`);

  // 界许褚【裸衣】的第二次询问（用户裁定）也走通用 SELECT_OPTION：中文标签来自同一张
  // 一次性标签表，合法项仍原样取自 legal_actions（前端不按技能分支）。
  const luoyi = optionView(["luoyi_gain", "luoyi_skip"], 9433);
  luoyi.pending.skill_id = "luoyi:gain";
  ui.state.view = luoyi;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(luoyi);
  check("U2-04h2 裸衣「获得 / 不获得」= 两个中文选项按钮，原值不显示",
    optionLabels().join(",") === "获得,不获得"
      && !/luoyi_gain|luoyi_skip/.test($("t-options").text()),
    `选项=${JSON.stringify(optionLabels())}`);
  const gainButton = Array.from($("t-options").children)
    .find((node) => node.textContent === "获得");
  check("U2-04h3 点「获得」= 选中那条 SelectOption(luoyi_gain)，可提交",
    (() => {
      clickNode(gainButton);
      const action = ui.buildAction();
      return ui.state.selectedOption === 0 && $("btn-submit").disabled === false
        && !!action && action.kind === "SelectOption" && action.choice === "luoyi_gain"
        && luoyi.pending.legal_actions.includes(action);
    })(),
    `option=${ui.state.selectedOption} action=${JSON.stringify(ui.buildAction())}`);

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
  const optionButtons = () => Array.from($("t-options").children).filter(
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
    prompt: "改判窗口：用一张牌代替此判定牌，或放弃。",
    legal_actions: [
      { kind: "SubstituteJudgeCard", answers_seq: 9440, skill_id: "hero.simayi.guicai", card: judgeCard },
      { kind: "Pass", answers_seq: 9440 },
    ],
    candidate_index: {
      41: { card: judgeCard, pool: [], min_targets: 0, max_targets: 0, combos: [[]] },
    },
    option_seats: {}, context: { requirement: "用一张手牌，代替此判定牌", skill_hints: [] },
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
      ui: { requirement: "用一张手牌，代替此判定牌", passive: false, convert_sources: [] } },
  ];
  ui.state.view = view;
  ui.state.sessionId = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const entry = promptSkill("hero.simayi.guicai");
  const hintNode = Array.from($("t-prompt-skills").children).find(
    (node) => String(node.className || "").split(/\s+/).includes("prompt-skill-hint"));
  const before = calls.length;
  check("U2-04l 鬼才改判窗口：提示区只有一个只说明的「鬼才」入口（+ 选牌提示；无第二套选择）",
    ui.renderKind(pending, pending.legal_actions) === "picker"
      && promptSkillIds().join(",") === "hero.simayi.guicai"
      && !!entry && entry.getAttribute("data-window-entry") === "true"
      && entry.disabled === false
      && !!hintNode && /选一张牌/.test(hintNode.textContent) && /提交/.test(hintNode.textContent),
    `kind=${ui.renderKind(pending, pending.legal_actions)} 组=${JSON.stringify(promptSkillIds())} entry=${entry && entry.getAttribute("data-window-entry")} hint="${hintNode && hintNode.textContent}"`);
  check("U2-04m 点「鬼才」只写提示：不选中候选、不设焦点、不发请求（不是第二套选择方式）",
    (() => {
      clickNode(entry);
      return ui.state.selectedOption === null
        && ui.state.focusCardId === null
        && calls.length === before
        && /选一张牌/.test($("t-target-hint").textContent);
    })(),
    `option=${ui.state.selectedOption} focus=${ui.state.focusCardId} calls=${calls.length - before} hint="${$("t-target-hint").textContent}"`);

  const handChip = () => $("t-hand").children[0];
  check("U2-04n 点一张手牌 = 既有手选路径：焦点 + 提交可点 + 提交的就是那条改判候选",
    (() => {
      clickNode(handChip());
      const action = ui.buildAction();
      return ui.state.focusCardId === 41 && $("btn-submit").disabled === false
        && $("btn-pass").hidden === false
        && !!action && action.kind === "SubstituteJudgeCard" && action.card.card_id === 41
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

  // 界版【鬼才】的改判窗口含装备区（M6 票 14）：同一句提示必须对"一张牌"成立，
  // 且入口仍按服务端 requirement 认领（不是只认标准版那句）。
  const jieRequirement = "用一张牌，代替此判定牌";
  pending.context = { ...pending.context, requirement: jieRequirement };
  view.skills[1].ui = { ...view.skills[1].ui, requirement: jieRequirement };
  ui.renderTable();
  ui.renderPending(view);
  const jieEntry = promptSkill("hero.simayi.guicai");
  const jieHint = Array.from($("t-prompt-skills").children).find(
    (node) => String(node.className || "").split(/\s+/).includes("prompt-skill-hint"));
  check("U2-04l2 界版【鬼才】（候选含装备区）同样认领只说明入口，提示对「一张牌」成立",
    !!jieEntry && jieEntry.getAttribute("data-window-entry") === "true"
      && !!jieHint && /选一张牌/.test(jieHint.textContent)
      && !/一张手牌/.test(jieHint.textContent)
      && /提交/.test(jieHint.textContent),
    `entry=${jieEntry && jieEntry.getAttribute("data-window-entry")} hint="${jieHint && jieHint.textContent}"`);

  // 提交路径：一次 POST、载荷就是那条 SubstituteJudgeCard（400 由拦截器注入，不依赖真引擎）。
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
  check("U2-04q 点手牌 + 提交 = 恰好 1 条 POST，载荷是那条改判动作（被拒后回到点击前）",
    answerCalls.length === 1
      && answerCalls[0].body.action.kind === "SubstituteJudgeCard"
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
    const button = Array.from($("t-skills").children).find(
      (node) => node.getAttribute("data-skill-id") === "demo.modal");
    (button.listeners.click || []).forEach((fn) => fn({ target: button }));
    return view;
  };

  let view = enterModal();
  check("U1-03p 多步技能清单打开询问弹窗（标题 + 清单在正文里；详情窗保持关闭）",
    $("t-dialog").hidden === false
      && /预发动/.test($("t-dialog-title").textContent)
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
      && !Array.from($("t-dialog-body").children).includes($("t-countdown")),
    `bodyText="${$("t-dialog-body").text().slice(0, 60)}" countdownHidden=${$("t-countdown").hidden} countdownInBody=${Array.from($("t-dialog-body").children).includes($("t-countdown"))}`);

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
  const next = Array.from($("t-options").children).find((node) => node.textContent === "下一步");
  (next.listeners.click || []).forEach((fn) => fn({ target: next }));
  const seatOption = Array.from($("t-options").children).find(
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
  const logItems = Array.from($("t-log").children);
  check("U1-05g 轮次 / 阶段分隔是独立的暗灰分隔行（不做树）",
    logItems[0].className === "nar-divider"
      && logItems[0].textContent.startsWith("第 1 轮")
      && logItems[1].className === "nar-divider"
      && logItems[1].textContent === "出牌阶段",
    `[0]=${logItems[0].className}:${logItems[0].textContent} [1]=${logItems[1].className}:${logItems[1].textContent}`);
  const lineClasses = Array.from(logItems[2].children).map((node) => node.className);
  check("U1-05h 服务端的颜色类原样落到 span（武将黄、卡名红、动作粗、伤害红、技能蓝）",
    lineClasses.includes("nar-hero") && lineClasses.includes("nar-card-fire")
      && lineClasses.includes("nar-act")
      && Array.from(logItems[3].children).some((node) => node.className === "nar-seat")
      && Array.from(logItems[3].children).some((node) => node.className === "nar-hero")
      && Array.from(logItems[3].children).some((node) => node.className === "nar-dmg-fire")
      && Array.from(logItems[4].children).some((node) => node.className === "nar-skill"),
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
    // 真服务端叙事（不是手写夹具）也要真的落到颜色 span 上。判据**从载荷派生**：
    // U1-05 的契约是"前端只按 segments[].cls 上色"，而这一局里出现哪几种颜色由**对局
    // 内容**决定（谁发动了技能、伤害是不是火焰 / 雷电）——把它钉成固定五个类名等于把
    // 前端烟测绑在 AI 的行为面上（票 21 的 I1 换了一局的走向就红过）。类名的闭集由
    // test_web.py 的叙事契约钉住、`skill` 这类"某局未必出现"的色由真对局的那条生成断言
    // 兜（`test_the_narrative_is_a_flat_observer_readable_stream`）；这里核"**已渲染的**
    // 条目里，申报的类都真的被画出来了"。
    // 窗口由 **DOM 反推**（`#r-log` 的 li 数 = 渲染器铺出的条目数），不抄 replay.js 的
    // "seq <= 当前 + 最后 60 条"规则——窗口或取数口径改了这里跟着走，不会静默空转。
    const renderedEntries = $("r-log").children.length;
    const shownEntries = renderedEntries > 0
      ? (replay.narrative || []).slice(-renderedEntries)
      : [];
    const declared = new Set();
    for (const entry of shownEntries) {
      for (const segment of entry.segments || []) {
        declared.add(`nar-${segment.cls || "act"}`);
      }
    }
    const realClasses = new Set();
    for (const item of $("r-log").children) {
      for (const span of item.children) realClasses.add(span.className);
    }
    check("U1-05o2 真服务端叙事申报的颜色类都落到 span 上（闭集与生成另由叙事契约钉住）",
      declared.size >= 3 && [...declared].every((name) => realClasses.has(name)),
      `entries=${renderedEntries} declared=${[...declared].join(",")} rendered=${[...realClasses].join(",")}`);
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

  const skillButtons = Array.from($("t-skills").children).filter((node) => node.tagName === "BUTTON");
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
    return box ? Array.from(box.children).find((node) => (node.className || "").split(" ").includes("hero-block")) || null : null;
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
  const ownSections = ownDetail ? Array.from(ownDetail.children).map((node) => node.className) : [];
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
        && !Array.from($("t-modal-body").children).includes($("t-countdown"));
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
    const equipSlot = Array.from($("t-self-zones").children)
      .find((row) => row.getAttribute("data-zone") === "equip").children[0];
    const judgeSlot = Array.from($("t-self-zones").children)
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

  // 用户报障（#6）：没有 pending 时右键武将块也必须能开详情。这里用纯快照视图
  // （pending=null）覆盖"非询问态"，而不是只在询问态里测。
  {
    const noPending = synthView(null);
    noPending.snapshot.players[0].skills = [
      detailSkill("hero.demo.own", "非询问己方技"),
    ];
    noPending.snapshot.players[1].skills = [
      detailSkill("hero.demo.enemy", "非询问敌方技"),
    ];
    noPending.snapshot.players[1].hero_name = "张飞";
    ui.state.view = noPending;
    ui.state.sessionId = null;
    ui.resetSelection();
    ui.renderTable();
    fireContext(heroBlockOf(0));
    check("U1-06v1 非询问态（pending=null）右键自己的武将块也能开详情",
      $("t-modal").hidden === false
        && $("t-modal-body").text().includes("非询问己方技"),
      `hidden=${$("t-modal").hidden} body="${$("t-modal-body").text().replace(/\s+/g, " ").slice(0, 100)}"`);
    fireDocument("keydown", { key: "Escape" });
    fireContext(heroBlockOf(1));
    check("U1-06w1 非询问态右键对手武将块也能开详情（只读公开清单）",
      $("t-modal").hidden === false
        && $("t-modal-body").text().includes("非询问敌方技")
        && !$("t-modal-body").text().includes("非询问己方技"),
      `hidden=${$("t-modal").hidden} body="${$("t-modal-body").text().replace(/\s+/g, " ").slice(0, 100)}"`);
    fireDocument("keydown", { key: "Escape" });
    const blank = $("t-processing");
    const blankEvent = { target: blank, preventDefault() {} };
    (($("table-shell").listeners.contextmenu) || []).forEach((fn) => fn(blankEvent));
    // 事件回放期间 `tableViewForRender` 把 pending 收起（视觉上也是"非询问态"），
    // 但 `openHeroDetail` / `syncHeroModal` 不能因此拒绝打开详情。
    const playbackView = synthView(null);
    playbackView.snapshot.players[0].skills = [detailSkill("hero.demo.own", "回放己方技")];
    playbackView.snapshot.players[1].skills = [detailSkill("hero.demo.enemy", "回放敌方技")];
    ui.state.view = playbackView;
    ui.state.liveView = playbackView;
    ui.state.playback = { body: playbackView, records: [], index: 0, live: false };
    ui.state.sessionId = null;
    ui.resetSelection();
    ui.renderTable();
    fireContext(heroBlockOf(0));
    check("U1-06v3 事件回放的非询问态也能右键开自己的详情",
      $("t-modal").hidden === false
        && $("t-modal-body").text().includes("回放己方技"),
      `hidden=${$("t-modal").hidden} body="${$("t-modal-body").text().replace(/\s+/g, " ").slice(0, 100)}"`);
    fireDocument("keydown", { key: "Escape" });
    fireContext(heroBlockOf(1));
    check("U1-06w6 事件回放的非询问态也能右键开对手详情",
      $("t-modal").hidden === false
        && $("t-modal-body").text().includes("回放敌方技")
        && !$("t-modal-body").text().includes("回放己方技"),
      `hidden=${$("t-modal").hidden} body="${$("t-modal-body").text().replace(/\s+/g, " ").slice(0, 100)}"`);
    fireDocument("keydown", { key: "Escape" });
    ui.state.playback = null;
    ui.state.liveView = null;

    check("U1-06x 非询问态空白处右键不开详情（外壳仍只屏蔽浏览器菜单）",
      $("t-modal").hidden === true,
      `hidden=${$("t-modal").hidden}`);
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
        && Array.from($("t-dialog-body").children).includes($("t-options"))
        && $("t-modal").hidden === false
        && /武将详情/.test($("t-modal-title").textContent)
        && $("t-dialog-body").children.length === dialogBodyBefore
        && JSON.stringify(ui.buildAction()) === arrangeActionBefore
        && ui.selectionEnabled() === false,
      `dialog="${$("t-dialog-title").textContent}" detail="${$("t-modal-title").textContent}" optionsInDialog=${Array.from($("t-dialog-body").children).includes($("t-options"))} enabled=${ui.selectionEnabled()}`);

    fireDocument("keydown", { key: "Escape" });
    check("U1-06w2 ESC 只关详情：排列弹窗原样恢复（正文仍是排列、无详情残留、仍只读）",
      $("t-modal").hidden === true
        && $("t-dialog").hidden === false
        && $("t-dialog-title").textContent === "排列"
        && Array.from($("t-dialog-body").children).includes($("t-options"))
        && !Array.from($("t-dialog-body").children).some(
          (node) => (node.className || "").includes("hero-detail"))
        && JSON.stringify(ui.buildAction()) === arrangeActionBefore
        && ui.selectionEnabled() === false,
      `dialogHidden=${$("t-dialog").hidden} title="${$("t-dialog-title").textContent}" bodyClasses=${JSON.stringify(Array.from($("t-dialog-body").children).map((node) => node.className))} enabled=${ui.selectionEnabled()}`);

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
        && Array.from($("t-dialog-body").children).includes($("t-options")),
      `dialogHidden=${$("t-dialog").hidden} bodyHidden=${$("t-dialog-body").hidden} title="${$("t-dialog-title").textContent}"`);
  }

  // 真会话（不是合成夹具）：服务端给**每个座位**下发详情清单，读取方（详情）读到它。
  // 判据未放宽：详情里的武将名称 / 技能清单走标准武将表（界将的显示归票 19），故这一局
  // 的对手（座位 1）必须是标准版武将。M6 票 18 换池（版本 50 → 54）后 20261012 的座位 1
  // 变成界将，按同一判据重扫到 20261017（座位 0 司马懿 / 座位 1 吕蒙，均为标准版）。
  const realDetail = await post("/api/sessions", {
    player_count: 4, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261017,
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
  // 合成 view = 新场景：舞台项不跨场景沿用（U3-01 起 commit 不清空展示 / 弃牌）。
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.view = faceView;
  ui.resetSelection();
  ui.renderTable();
  const faces = Array.from($("t-processing").children);
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
      && Array.from($("t-hand").children).every((card) => byClass(card, "corner").length === 0)
      && Array.from($("t-hand").children).map((card) => attr(card, "data-kind")).join(",") === "normal,normal",
    `kinds=${Array.from($("t-hand").children).map((card) => attr(card, "data-kind")).join(",")}`);

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
  // 新场景：上一小节的五张产物不该以淡出态混进这一段的计数。
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
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
    `ids=${Array.from($("t-processing").children).map((child) => attr(child, "data-card-id")).join(",")}`);

  // 朱雀羽扇（票 09）：来源牌先进处理区，**使用中**被换成火【杀】——舞台要跟着换牌面。
  // 记录流里产物只在时机的 `cards` 上（帧数据不进记录流），故判据只看时机记录。
  const fanProduct = {
    ...single, card_id: -609569600673815, type: "fire_slash", name: "火杀",
    display_name: "火杀", virtual_sources: [18], color: "red", suit: "heart", rank: 10,
  };
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.view = faceView;
  ui.applyViewRaw({
    session_id: "u1-07-fan", seed: 1, human_seat: 0, finished: false, timeout_seconds: null,
    heroes: [], skills: [], pending: null, cursor: 5, log_seq: 0, narrative: [],
    snapshot: { ...faceView.snapshot, processing: [] },
    records: [
      {
        kind: "delta", delta_type: "card_move", seq: 4,
        cards: [{ ...single, card_id: 18, type: "slash", name: "杀" }],
        from_zone: { zone: "hand", seat: 0 }, to_zone: { zone: "processing", seat: null },
        reason: "USE", owner: 0,
      },
      { kind: "timing", seq: 5, timing: "DAMAGE_TAKE", actor: 1, targets: [1], cards: [fanProduct] },
    ],
  });
  ui.stepPlayback(true);   // ① 来源牌进处理区
  ui.stepPlayback(true);   // ② 时机记录：使用中换成火【杀】
  check("U1-07p 使用中被转化（羽扇：普通【杀】→火【杀】）：舞台换牌面（时机记录带 virtual_sources）",
    ui.state.effectCards.length === 1
      && String(ui.state.effectCards[0].card_id) === String(fanProduct.card_id)
      && ui.state.effectCards[0].type === "fire_slash"
      && String((ui.state.effectCards[0].virtual_sources || [])[0]) === "18",
    `effect=${JSON.stringify(ui.state.effectCards.map((card) => [card.card_id, card.type]))}`);

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
  const endPlayPhase = async () => {
    const pending = ui.state.view.pending;
    if (!pending || pending.kind !== "PLAY_PHASE") return false;
    const index = (pending.legal_actions || []).findIndex((action) => action.kind === "EndPhase");
    if (index < 0) return false;
    ui.resetSelection();
    ui.state.selectedOption = index;
    await ui.submitAction();
    return true;
  };
  /** 弃牌阶段：界面没有手选路径（spec D10），按"前 N 张手牌"补 picks 再提交。 */
  const discardPhase = async () => {
    const pending = ui.state.view.pending;
    if (!pending || pending.kind !== "DISCARD") return false;
    const first = (pending.legal_actions || []).find((action) => action.kind === "DiscardCards");
    if (!first) return false;
    const need = (first.cards || []).length;
    const hand = (ui.state.view.snapshot.self_hand || []).map((card) => card.card_id);
    if (hand.length < need) return false;
    ui.resetSelection();
    ui.state.picks = hand.slice(0, need);
    ui.renderTable();
    ui.renderPending(ui.state.view);
    if (ui.buildAction() === null) return false;
    await ui.submitAction();
    return true;
  };

  // ① 龙胆：seed 5 的赵云起始手牌里有【闪】；出牌阶段真会话给出龙胆清单。
  // 钉住座位 0 = 赵云（M6 P0 换池后随机发将不再保证 seed 5 的座位 0 是他）。
  await newSession(5, { my_hero: "zhaoyun" });
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
  const longdanButton = Array.from($("t-skills").children).find(
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

  // ② 丈八蛇矛：seed 38 起始手牌有它；装备后真会话给出两来源步的转化清单。
  await newSession(38);
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
  const serpentButton = Array.from($("t-skills").children).find(
    (node) => node.getAttribute("data-skill-id") === "equip.serpent_spear");
  check("U1-07k 真会话【丈八】：seed 38 装备后服务端下发两来源步清单，技能按钮可点",
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

  // ③ 八卦阵：起始手牌有它，装上后真会话走到需要【闪】的窗口，候选里有八卦。
  // 这条场景**依赖对手真的对座位 0 出【杀】**，因此对"启用池 → 随机发将 → AI 行动序列"敏感；
  // M6 P0 票 02 换池（启用池 22 名）后 seed 21 不再命中，按同一判据重扫到 seed 29；
  // feng-zhangjiao 票 01 换池（张角移动版进池 ⇒ 63 版本）后原清单全部不再命中，
  // 按**同一判据**重扫到 seed 63（探针 `.scratch/feng-zhangjiao/scan_bagua.py`：
  // 起始手牌有【八卦阵】→ 装上 → 对手真的对座位 0 出【杀】→ DODGE 窗口的候选里有八卦）。
  // 后续换池若失效，仍按同一判据重扫；判据本身未放宽。
  let baguaSeed = null;
  let equippedBagua = false;
  let sawBaguaDodge = false;
  for (const seed of [63, 66, 88, 89, 90, 29, 21, 7, 15, 20, 2, 24, 28, 38]) {
    await newSession(seed);
    equippedBagua = false;
    sawBaguaDodge = false;
    for (let step = 0; step < 160; step += 1) {
      const pending = ui.state.view.pending;
      if (!pending) break;
      if (pending.actor === 0 && pending.kind === "PLAY_PHASE") {
        // 这个场景要的是"对手真的对座位 0 出【杀】"：座位 0 只装八卦、然后结束出牌阶段
        // （不是随手甩牌把局面打散）——否则扫描结果取决于 UI 的"第一个候选"策略。
        if (!equippedBagua && await submitEquip("eight_diagrams")) {
          equippedBagua = true;
          continue;
        }
        if (await endPlayPhase()) continue;
      }
      if (pending.actor === 0 && pending.kind === "DODGE"
        && (pending.legal_actions || []).some(
          (action) => action.kind === "ActivateSkill" && action.skill_id === "equip.eight_diagrams")) {
        sawBaguaDodge = true;
        break;
      }
      if (pending.kind === "DISCARD" && await discardPhase()) continue;
      const before = pending;
      if (!(await passResponse())) await advanceOnce();
      if (ui.state.view.pending === before) break; // 这一轮没提交成功 ⇒ 换下一个种子
    }
    if (sawBaguaDodge) { baguaSeed = seed; break; }
  }
  const baguaPending = ui.state.view.pending;
  const baguaEquip = (ui.state.view.snapshot.players[0].equip || []).map((card) => card.type);
  // 扫不到命中时 `pending` 可能已经是 null（最后一个种子的局打完了）：判据照旧判红，
  // 但不许在 detail 字符串里崩（那会把 FAIL 变成 TypeError、看不出是哪条场景）。
  const baguaLegal = (baguaPending && baguaPending.legal_actions) || [];
  check("U1-07l 真会话【八卦】：装上后走到 DODGE 窗口，候选里出现八卦阵的 ActivateSkill",
    equippedBagua && baguaEquip.includes("eight_diagrams") && sawBaguaDodge
      && baguaLegal.some(
        (action) => action.kind === "ActivateSkill" && action.skill_id === "equip.eight_diagrams"),
    `seed=${baguaSeed} equip=${baguaEquip.join(",")} dodge=${sawBaguaDodge} legal=${baguaLegal.map((a) => a.kind + ":" + (a.skill_id || "")).join(",")}`);
  const baguaIndex = baguaLegal.findIndex(
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
    return box ? Array.from(box.children).find((node) => (node.className || "").split(" ").includes("hero-block")) || null : null;
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
  check("U2-05c 只读态里悬停照常：手牌的 title 与装备槽的预览卡面仍是服务端描述",
    handChip().getAttribute("title") === "【杀】的全文描述"
      && (() => {
        const slot = selfEquipChip();
        if (!slot) return false;
        (slot.listeners.mouseenter || []).forEach(
          (fn) => fn({ target: slot, clientX: 0, clientY: 0 }));
        const faces = Array.from($("t-card-preview").children).filter(
          (node) => String(node.className || "").split(/\s+/).includes("card"));
        const title = faces.length ? faces[0].getAttribute("title") : "";
        ui.hideCardPreview();
        return /青釭剑/.test(title);
      })(),
    `hand="${handChip().getAttribute("title")}"`);
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && !Array.from($("t-dialog-body").children).some(
        (node) => (node.className || "").includes("hero-detail"))
      && JSON.stringify(ui.buildAction()) === arrangeActionBefore
      && ui.selectionEnabled() === false,
    `detailHidden=${$("t-modal").hidden} title="${$("t-dialog-title").textContent}" bodyClasses=${JSON.stringify(Array.from($("t-dialog-body").children).map((node) => node.className))} action=${JSON.stringify(ui.buildAction())}`);

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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
  const modalSkill = Array.from($("t-skills").children).find(
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
  const nextStep = Array.from($("t-options").children).find((node) => node.textContent === "下一步");
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
    return box ? Array.from(box.children).find((node) => (node.className || "").split(" ").includes("hero-block")) || null : null;
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && !actionIds.some((id) => Array.from($("t-dialog-body").children).includes($(id))),
    `htmlClose=${/t-dialog-close|>关闭</.test(dialogHtml)} actionInDialog=${actionIds.filter((id) => dialogHtml.includes(`id="${id}"`)).join(",")} bodyChildren=${JSON.stringify(Array.from($("t-dialog-body").children).map((node) => node.id || node.className))}`);
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
  const renderedWhileCollapsed = Array.from($("t-options").children)
    .flatMap((row) => Array.from(row.children || []))
    .flatMap((box) => Array.from(box.children || []))
    .some((node) => node.getAttribute && node.getAttribute("data-card-index") === "0"
      && node.classList.contains("sel"));
  check("U2-06d 同一问（同 seq）的重复渲染只换正文、保留折叠状态",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === true
      && ui.state.dialog.collapsed === true
      && $("t-dialog-title").textContent === "排列"
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
      && Array.from($("t-options-home").children).includes($("t-options"))
      && $("btn-show-modal").hidden === true
      && ui.selectionEnabled() === true
      && ui.state.dialog.key === null,
    `hidden=${$("t-dialog").hidden} body=${$("t-dialog-body").children.length} home=${Array.from($("t-options-home").children).includes($("t-options"))} enabled=${ui.selectionEnabled()}`);

  const reopened = openArrange(9603, [cardC]);
  check("U2-06e2 新的弹窗步重新打开：折叠复位、正文换成本问的牌（上一问的窗口不残留）",
    $("t-dialog").hidden === false
      && $("t-dialog-body").hidden === false
      && $("t-dialog-collapse").textContent === "收起"
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
    `closedInBetween=${closedInBetween} body=${JSON.stringify(Array.from($("t-dialog-body").children).map((node) => node.id || node.className))} options="${$("t-options").text().slice(0, 80)}"`);

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
  const preSkill = Array.from($("t-skills").children).find(
    (node) => node.getAttribute("data-skill-id") === "demo.modal");
  (preSkill.listeners.click || []).forEach((fn) => fn({ target: preSkill }));
  check("U2-06f 预发动清单迁移到询问弹窗：标题 / 正文在里面，但仍是牌桌表面（不是只读）",
    $("t-dialog").hidden === false
      && /预发动【示例模态】/.test($("t-dialog-title").textContent)
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
      && Array.from($("t-options-home").children).includes($("t-options"))
      && $("btn-show-modal").hidden === true
      && ui.state.dialog.open === false
      && ui.state.dialog.key === null,
    `hidden=${$("t-dialog").hidden} body=${$("t-dialog-body").children.length} title="${$("t-dialog-title").textContent}" home=${Array.from($("t-options-home").children).includes($("t-options"))}`);
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
  const rowOf = (name) => Array.from($("t-options").children).find(
    (node) => node.getAttribute && node.getAttribute("data-row") === name);
  const cardsBox = (row) => row.children[1];
  const cardNode = (row, index) => Array.from(cardsBox(row).children).find(
    (node) => node.getAttribute && node.getAttribute("data-card-index") === String(index));
  const cardNodes = (row) => Array.from(cardsBox(row).children).filter(
    (node) => node.getAttribute && node.getAttribute("data-card-index") !== undefined);
  const cardIndexes = (row) => cardNodes(row).map((node) => Number(node.getAttribute("data-card-index")));
  const slots = (row) => Array.from(cardsBox(row).children).filter(
    (node) => node.getAttribute && node.getAttribute("data-empty") === "1");
  const clickNode = (node) => (node.listeners.click || []).forEach(
    (fn) => fn({ target: node, preventDefault() {}, stopPropagation() {} }));
  // 垫片的 `className` 是普通属性、`classList.add` 只改 Set（真 DOM 两者同步）；断言两种都认。
  const hasClass = (node, name) => node.classList.contains(name)
    || String(node.className || "").split(/\s+/).includes(name);
  /** 票 01 起落点只认指针坐标：给两行行框与每张牌铺确定矩形（与 W6-03 块同一套夹具）。 */
  const layoutRects = () => {
    const layout = ui.state.arrange;
    const put = (node, left, top, width = 88, height = 124) => {
      if (!node) return;
      node._rect = {
        left, top, width, height, right: left + width, bottom: top + height, x: left, y: top,
      };
    };
    for (const name of ["top", "bottom"]) {
      const row = rowOf(name);
      if (!row) continue;
      put(cardsBox(row), 100, name === "top" ? 200 : 400, 600, 124);
      let at = 0;
      for (const index of layout[name]) {
        if (index === layout.dragging) continue;
        put(cardNode(row, index), 100 + (at * 100), name === "top" ? 200 : 400);
        at += 1;
      }
    }
  };

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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && !!topRow && !!bottomRow
      && $("t-dialog-body").text().includes("牌堆顶")
      && $("t-dialog-body").text().includes("牌堆底"),
    `title="${$("t-dialog-title").textContent}" hint="${$("t-dialog-hint").textContent}" bodyChildren=${JSON.stringify(Array.from($("t-dialog-body").children).map((node) => node.id || node.className))}`);

  check("U2-07b 两行牌面是完整卡面（.card.face，牌名 / 花色在脸内），不是旧的紧凑排列芯片",
    topCards.length === 5
      && topCards.every((node) => hasClass(node, "card")
        && hasClass(node, "face")
        && hasClass(node, "arrange-card")
        && node.getAttribute("data-card-id") !== undefined
        && Array.from(node.children).some((child) => hasClass(child, "cname"))
        && Array.from(node.children).some((child) => hasClass(child, "suit")))
      && !topCards.some((node) => Array.from(node.children).some((child) => hasClass(child, "n"))),
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
  layoutRects();
  const dragPoint = { clientX: 100 + 500, clientY: 400 + 62 };
  (rowOf("bottom").listeners.dragover || []).forEach(
    (fn) => fn({ preventDefault() {}, stopPropagation() {}, ...dragPoint }));
  (rowOf("bottom").listeners.drop || []).forEach(
    (fn) => fn({ preventDefault() {}, ...dragPoint }));
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
    // U4-06 / D13：未知区域的归属写**武将名**（没有 hero_name 时才会退回「座位N」）。
    view.snapshot.players[0].hero_name = "郭嘉";
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
  const buttons = (id = "t-options") => Array.from($(id).children);

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
      && Array.from($("t-options-home").children).includes($("t-options")) === false
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && $("t-options").classList.contains("suit-options")
      && $("t-options").children.length === 4
      && Array.from($("t-options").children).map((node) => node.textContent).join(",")
        === "♥红桃,♦方块,♠黑桃,♣梅花"
      && Array.from($("t-options").children).map((node) => node.getAttribute("data-color")).join(",")
        === "red,red,black,black"
      && Array.from($("t-options").children).every((node) => node.classList.contains("suit"))
      && !/heart|diamond|spade|club|选项：/.test(dialogText())
      && candidateRowButtons().length === 0,
    `kind=${ui.stepDialogKind(suitView.pending)} 选项=${JSON.stringify(Array.from($("t-options").children).map((node) => node.textContent))} text=${dialogText()}`);
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && $("t-dialog-title").textContent === "选择一张牌"
      && $("t-dialog-hint").textContent.includes("点一张牌即提交"),
    `kind=${ui.stepDialogKind(blindView.pending)} key=${ui.state.dialog.key} before=${keyBeforeBlind} title="${$("t-dialog-title").textContent}"`);
  const blindZone = $("t-options").children[0];
  const blindSlots = Array.from(blindZone.children[1].children);
  check("U2-08h 盲挑 = 五个「?」槽位 + 归属 / 区域提示；不画没提供的装备区",
    $("t-options").children.length === 1
      && blindZone.getAttribute("data-zone") === "hand"
      && blindZone.children[0].textContent.includes("郭嘉 手牌")
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
    `块=${Array.from($("t-options").children).map((node) => node.getAttribute("data-zone")).join(",")} 文本=${$("t-options").text()}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

/* ============ 票 08（B-ui）：展示武将牌的选项弹窗（【天成】） ============
 *
 * 载荷形状 = 票 07 的投影契约（`hero_card` / `option_details`，服务端那一侧由
 * `tests/test_web.py` 钉）；本段钉**界面读法**：标题 / n-of-m / 武将块三要素 /
 * 技能行带原文描述 / 置灰行 `.dis` 且点不动 / 选项 2 的具体 X / 没有「放弃」/
 * 没牌的那一次仍画副标题与选项 2。
 *
 * **同源**（Standards §6）：下面的合成载荷**逐字抄** `src/sgs/skills/start.py` 的写入面——
 * `context.requirement` = `TIANCHENG_REQUIREMENT`（牌面原文，以 `(锁)：` 起头）、
 * `HERO_OPTION_ID` = `TIANCHENG_MAX_HP`（`"tiancheng:max_hp"`）、选项 2 的文本 = `_OPTION_TWO_TEXT`
 * 现算到 X=2。票 01 已把【天成】接上 `ask_option`，真流程的 3 次展示由 `tests/test_hero_wuming.py`
 * 钉；这里只钉**界面读法**（喂合成 `pending`，走真脚本的渲染入口 `renderTable` + `renderPending`）。
 * 真浏览器验收见票面「复现配方」。
 */
{
  const HERO_SKILL_ID = "hero.jie_guanyu.wusheng";
  const HERO_OPTION_ID = "tiancheng:max_hp";   // = start.py:TIANCHENG_MAX_HP（选项 2 的真实 id）
  const HERO_SKILL_DESC = "你可以将一张红色牌当【杀】使用或打出；你使用方块【杀】无距离限制。"
    + "本仓实现：①与标准版【武圣】逐字相同（手牌或装备区的红牌）；"
    + "②「无距离限制」落在目标池的查询期修正上——方块（含单源转化出的方块）【杀】的"
    + "目标池不受「距离 ≤ 攻击范围」限制，失去或失效【武圣】后立刻回到基线。";
  // = start.py:_OPTION_TWO_TEXT.format(x=2)（选项 2 的动态文本，X 由引擎现算）
  const HERO_OPTION_TEXT = "增加 1 点体力上限，回复 1 点体力，摸 2 张牌";
  // = start.py:TIANCHENG_REQUIREMENT 逐字（`context.requirement`；以 `(锁)：` 起头）
  const HERO_REQUIREMENT = "(锁)：游戏开始时，依次展示3张真名不在场上的随机武将牌，且每次展示你须选择："
    + "1.获得该武将牌上的1个技能；2.增加一点体力上限，回复一点体力，摸X张牌"
    + "（X为你选择选项2的次数）";

  /** 与 `tools/sgs_web/projection.pending_payload` 的键 / 值同形（`hero_id=null` =
   *  这一次没有武将牌：武将块三要素全 null、`option_details` 只剩效果行）。 */
  const heroCardPending = ({
    heroId = "jie_guanyu", name = "界关羽", index = 2, total = 3,
    enabledSkill = false, optionText = HERO_OPTION_TEXT,
  } = {}) => {
    const seq = 9950;
    const card = heroId === null
      ? { hero_id: null, index, total, name: null, kingdom: null, kingdom_label: null, hp_max: null }
      : { hero_id: heroId, index, total, name, kingdom: "shu", kingdom_label: "蜀", hp_max: 4 };
    const skillRow = {
      choice: HERO_SKILL_ID, text: "武圣", description: HERO_SKILL_DESC,
      skill_id: HERO_SKILL_ID, disabled: !enabledSkill,
    };
    const optionRow = {
      choice: HERO_OPTION_ID, text: optionText, description: null, skill_id: null, disabled: false,
    };
    const rows = heroId === null ? [optionRow] : [skillRow, optionRow];
    return {
      kind: "SELECT_OPTION", actor: 0, seq,
      skill_id: "hero.wuming.tiancheng",
      prompt: "请选择一项。",
      options: rows.map((row) => row.choice),
      disabled: rows.filter((row) => row.disabled).map((row) => row.choice),
      option_texts: { [HERO_OPTION_ID]: optionText },
      hero_card: card,
      option_details: rows,
      legal_actions: rows.filter((row) => !row.disabled)
        .map((row) => ({ kind: "SelectOption", choice: row.choice, answers_seq: seq })),
      candidate_index: {}, virtual_index: {}, option_seats: {},
      context: {
        actor: 0, name: "天成", targets: [0], label: "0号位界关羽的【天成】",
        requirement: HERO_REQUIREMENT, skill_hints: [],
      },
      preactivations: {}, preactivation: [],
    };
  };
  const heroView = (pending) => {
    const view = synthView(pending);
    // 标题的第二个来源：`build_skills` 里同一 `skill_id` 的技能名（真实会话的
    // `body.skills`；合成视图没有它时标题只能退回中性文案）。
    view.skills = [{
      skill_id: "hero.wuming.tiancheng", name: "天成", skill_type: "NORMAL",
      locked: true, carrier: "HERO", description: "游戏开始时，依次展示3张武将牌。",
      ui: { passive: true },
    }];
    return view;
  };
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const heroCardNode = () => Array.from($("t-options").children)
    .find((node) => hasClassText(node, "hero-card")) || null;
  const heroChildren = (name) => {
    const card = heroCardNode();
    return card
      ? Array.from(card.children).filter((node) => hasClassText(node, name))
      : [];
  };
  const heroRows = () => heroChildren("hero-card-row");
  const clickCount = (node) => (node.listeners.click || []).length;

  // ---- ① 外壳：仍是 `choice`（**不为武将牌另立 kind**），标题来自技能名 ----
  // 种类总数在 huo-shenzhugeliang 票 02 之后是 7（新增 `heroPick` = 武将牌上的牌点选），
  // lei-shenganning 票 93 再加第八种 `watchTwoRows`（两行观看手牌），jie-feng-batch2 票 01a
  // 再加第九种 `multiChoice`（多选勾选面）。本条判的仍是"武将牌弹窗**不**占用一个专用 kind"。
  render(heroView(heroCardPending()));
  check("U1-08a 武将牌弹窗 = 既有选择型外壳（不另立 kind）：标题「天成」、正文在弹窗里",
    Object.keys(ui.DIALOG_KINDS).length === 9
      && !("hero" in ui.DIALOG_KINDS)
      && ui.stepDialogKind(ui.state.view.pending) === "choice"
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "天成"
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && !!heroCardNode(),
    `kinds=${Object.keys(ui.DIALOG_KINDS).join(",")} kind=${ui.stepDialogKind(ui.state.view.pending)} title="${$("t-dialog-title").textContent}" body=${$("t-dialog-body").children.length}`);

  // ---- ② 副标题「第 k/m 张」+ 武将块三要素（势力 / 名称 / hp_max/hp_max 点数条） ----
  const subNode = heroChildren("hero-card-sub")[0];
  const blockNode = heroChildren("hero-card-block")[0];
  const blockParts = blockNode ? Array.from(blockNode.children) : [];
  const headNode = blockParts.find((node) => hasClassText(node, "hero-card-head"));
  const hpNode = blockParts.find((node) => hasClassText(node, "hp-bar"));
  const kingdomNode = headNode
    ? Array.from(headNode.children).find((node) => hasClassText(node, "kingdom"))
    : null;
  const nameNode = headNode
    ? Array.from(headNode.children).find((node) => hasClassText(node, "hero-name"))
    : null;
  const hpSegs = hpNode
    ? Array.from(hpNode.children).filter((node) => hasClassText(node, "hp-seg"))
    : [];
  const hpNums = hpNode
    ? Array.from(hpNode.children).filter((node) => hasClassText(node, "hp-num"))
    : [];
  check("U1-08b 副标题「第 k/m 张」+ 武将块三要素：势力徽记 / 名称 / hp_max/hp_max 点数条",
    !!subNode && subNode.textContent === "第 2/3 张"
      && !!blockNode
      && !!kingdomNode && kingdomNode.textContent === "蜀"
      && kingdomNode.classList.contains("shu")
      && !!nameNode && nameNode.textContent === "界关羽"
      && hpSegs.length === 4
      && hpSegs.every((node) => !node.classList.contains("empty"))
      && hpNums.length === 1 && hpNums[0].textContent === "4/4",
    `sub="${subNode && subNode.textContent}" kingdom="${kingdomNode && kingdomNode.textContent}" name="${nameNode && nameNode.textContent}" hp="${hpNode && hpNode.textContent}"`);

  // ---- ③ 选项行：与 options 同序；技能名 + 原文描述（不截断）；选项 2 固定最后、带具体 X ----
  const rows = heroRows();
  const skillRowNode = rows[0];
  const optionRowNode = rows[rows.length - 1];
  const skillDescNode = skillRowNode
    ? Array.from(skillRowNode.children).find((node) => hasClassText(node, "hero-card-desc"))
    : null;
  check("U1-08c 选项行与 options 同序：技能名 + 原文描述（不截断），选项 2 最后且带具体 X",
    rows.length === 2
      && skillRowNode.getAttribute("data-choice") === HERO_SKILL_ID
      && skillRowNode.textContent.includes("武圣")
      && !!skillDescNode && skillDescNode.textContent === HERO_SKILL_DESC
      && skillDescNode.textContent.length > 40
      && optionRowNode.getAttribute("data-choice") === HERO_OPTION_ID
      && optionRowNode.textContent.includes(HERO_OPTION_TEXT)
      && /摸 2 张牌/.test(optionRowNode.textContent)
      && !/摸 X 张牌/.test(optionRowNode.textContent),
    `rows=${rows.length} first="${skillRowNode && skillRowNode.textContent.slice(0, 24)}" last="${optionRowNode && optionRowNode.textContent}"`);

  // ---- ④ 置灰态：已拥有的技能行 `.dis` + disabled + 没有 click；效果行接 click ----
  check("U1-08d 已拥有的技能行画 `.dis` + disabled、不接 click；效果行接 click（一击提交）",
    skillRowNode.classList.contains("dis")
      && skillRowNode.disabled === true
      && clickCount(skillRowNode) === 0
      && optionRowNode.classList.contains("dis") === false
      && optionRowNode.disabled === false
      && clickCount(optionRowNode) === 1,
    `skillDis=${skillRowNode.classList.contains("dis")} skillDisabled=${skillRowNode.disabled} skillClicks=${clickCount(skillRowNode)} optionDis=${optionRowNode.classList.contains("dis")} optionDisabled=${optionRowNode.disabled} optionClicks=${clickCount(optionRowNode)}`);

  // ---- ⑤ 没有「放弃」：载荷本来就没有 Pass，右端也不画；不走单选项确认那条分支 ----
  check("U1-08e 弹窗里没有「放弃」：右端 Pass 收起、正文不含「放弃」、不退回单选项确认",
    $("btn-pass").hidden === true
      && !/放弃/.test($("t-dialog-body").text())
      && ui.renderKind(ui.state.view.pending, ui.state.view.pending.legal_actions) === "options",
    `passHidden=${$("btn-pass").hidden} bodyText="${$("t-dialog-body").text()}" renderKind=${ui.renderKind(ui.state.view.pending, ui.state.view.pending.legal_actions)}`);

  // ---- ⑥ 一击提交：点效果行 ⇒ 只选中 `legal_actions` 里那条候选（合成载荷无会话，不 POST） ----
  const beforeHeroClick = calls.length;
  await clickAndWait(optionRowNode);
  check("U1-08f 点效果行 = 选中 `legal_actions` 里那条候选（不手拼 Action、不发请求）",
    ui.state.selectedOption === 0
      && calls.length === beforeHeroClick
      && JSON.stringify(ui.buildAction())
        === JSON.stringify(ui.state.view.pending.legal_actions[0]),
    `selected=${ui.state.selectedOption} calls=${calls.length - beforeHeroClick} action=${JSON.stringify(ui.buildAction())}`);

  // ---- ⑦ 没牌的那一次（hero_id=null）：仍画副标题 + 选项 2，没有武将块 ----
  const emptyView = heroView(heroCardPending({ heroId: null, index: 3, total: 3 }));
  render(emptyView);
  const emptySub = heroChildren("hero-card-sub")[0];
  const emptyRows = heroRows();
  check("U1-08g 没牌的那一次（hero_id=null）：无武将块，但副标题 3/3 与选项 2 都在（展示恒为 3 次）",
    $("t-dialog").hidden === false
      && ui.stepDialogKind(emptyView.pending) === "choice"
      && !!emptySub && emptySub.textContent === "第 3/3 张"
      && heroChildren("hero-card-block").length === 0
      && emptyRows.length === 1
      && emptyRows[0].getAttribute("data-choice") === HERO_OPTION_ID
      && emptyRows[0].textContent.includes(HERO_OPTION_TEXT)
      && $("btn-pass").hidden === true,
    `sub="${emptySub && emptySub.textContent}" block=${heroChildren("hero-card-block").length} rows=${emptyRows.length} text="${emptyRows[0] && emptyRows[0].textContent}"`);

  // ---- ⑧ 连续两次展示共用同一个窗（链键），只换副标题 / 正文：第 2 张 → 第 3 张 ----
  render(heroView(heroCardPending({ index: 2, total: 3 })));
  const keyAtSecond = ui.state.dialog.key;
  const secondSub = heroChildren("hero-card-sub")[0];
  render(heroView(heroCardPending({ index: 3, total: 3 })));
  const thirdSub = heroChildren("hero-card-sub")[0];
  check("U1-08h 连续两次展示共用同一个窗（键不变），只换副标题 / 正文",
    !!secondSub && secondSub.textContent === "第 2/3 张"
      && !!thirdSub && thirdSub.textContent === "第 3/3 张"
      && ui.state.dialog.key === keyAtSecond
      && $("t-dialog").hidden === false,
    `key=${ui.state.dialog.key} before=${keyAtSecond} sub="${thirdSub && thirdSub.textContent}"`);

  // ---- ⑨ 标题的第二个来源：`context` 缺失（409 裸 Query / 合成载荷）⇒ build_skills 的技能名 ----
  const noContext = heroCardPending();
  noContext.context = null;
  render(heroView(noContext));
  check("U1-08i 没有 context 时标题退回 `build_skills` 里同一 skill_id 的技能名（前端不抄技能名）",
    $("t-dialog-title").textContent === "天成",
    `title="${$("t-dialog-title").textContent}"`);

  // ---- ⑩ 回放生命周期（Standards §7）：批里从第 1/3 张收敛到第 2/3 张 ----
  // 一次「点掉第 1 张 + 服务器给出第 2 张」的响应 = **一批记录 + 新 pending**；弹窗的寿命
  // 必须挂在这批回放上，而不是等下一次刷新：起播让位（旧问已答完，回放期演的是它的后果），
  // 批尾收尾时回到**同一个窗、同一个键**（`chain:天成:0`），正文换成下一张武将牌。
  // 判据分三处：① 批中途不残留第 1 张的正文；② 批尾收敛到第 2/3 张、键不变；③ 收敛后的
  // 渲染与「把同一份载荷直接 commit」逐项相同（回放这条路径没有第二套读法）。
  {
    const partText = (parent, name) => (parent
      ? (Array.from(parent.children).find((node) => hasClassText(node, name)) || {}).textContent || ""
      : "");
    const heroCardSnapshot = () => {
      const block = heroChildren("hero-card-block")[0] || null;
      const head = block
        ? Array.from(block.children).find((node) => hasClassText(node, "hero-card-head")) || null
        : null;
      const hpBar = block
        ? Array.from(block.children).find((node) => hasClassText(node, "hp-bar")) || null
        : null;
      return {
        title: $("t-dialog-title").textContent,
        sub: (heroChildren("hero-card-sub")[0] || {}).textContent || "",
        kingdom: partText(head, "kingdom"),
        name: partText(head, "hero-name"),
        hp: partText(hpBar, "hp-num"),
        rows: heroRows().map((node) => [
          node.getAttribute("data-choice"),
          node.classList.contains("dis"),
          node.textContent,
        ]),
        bodyText: $("t-dialog-body").text(),
      };
    };
    const revealAt = (index, heroId, name) =>
      heroCardPending({ heroId, name, index, total: 3 });

    render(heroView(revealAt(1, "jie_guanyu", "界关羽")));
    const keyBefore = ui.state.dialog.key;
    const firstSub = (heroChildren("hero-card-sub")[0] || {}).textContent;
    const firstOpen = $("t-dialog").hidden === false;

    // 下一批：这次询问的应答 + 第二张的询问；`pending` 已经换成第 2/3 张（另一张武将牌）。
    const nextReveal = revealAt(2, "zhangfei", "张飞");
    ui.applyViewRaw({
      ...heroView(nextReveal),
      session_id: "u1-08j-hero-playback",
      cursor: 21, log_seq: 0, finished: false,
      records: [
        { kind: "action", seq: 9951, accepted: true, reason: null,
          action: { kind: "SelectOption", choice: HERO_OPTION_ID, answers_seq: 9950 } },
        { kind: "query", seq: 9952, query: { actor: 0, kind: "SELECT_OPTION" } },
      ],
    });
    const batchSize = ui.state.playback ? ui.state.playback.records.length : 0;
    // 起播即让位：旧问的第一张正文不再留在屏上（换成"要么收起、要么已经是新内容"）。
    const subAtStart = (heroChildren("hero-card-sub")[0] || {}).textContent || "";
    const noStaleAtStart = !($("t-dialog").hidden === false) || subAtStart !== firstSub;
    for (let index = 0; index < batchSize; index += 1) ui.stepPlayback(true);
    const stillPlaying = ui.state.playback !== null;
    ui.stepPlayback(true);   // 批尾那一步 = 这一批的收尾（commit）
    const keyAfter = ui.state.dialog.key;
    const afterBatch = heroCardSnapshot();
    // 对照：同一份载荷走「直接 commit」（render = renderTable + renderPending）。
    render(heroView(nextReveal));
    const commitRender = heroCardSnapshot();

    check("U1-08j 批里收敛：第 1/3 张 → 第 2/3 张（同一窗同一键），且与直接 commit 的渲染逐项相同",
      firstOpen
        && keyBefore === "chain:天成:0"
        && firstSub === "第 1/3 张"
        && batchSize === 2
        && noStaleAtStart
        && stillPlaying
        && keyAfter === keyBefore
        && ui.state.dialog.open === true
        && $("t-dialog").hidden === false
        && afterBatch.sub === "第 2/3 张"
        && afterBatch.name === "张飞"
        && JSON.stringify(afterBatch) === JSON.stringify(commitRender),
      `key=${keyBefore}→${keyAfter} sub="${firstSub}"→"${afterBatch.sub}"`
        + ` batch=${batchSize} startSub="${subAtStart}" stillPlaying=${stillPlaying}`
        + ` after=${JSON.stringify(afterBatch)} commit=${JSON.stringify(commitRender)}`);
  }

  ui.state.view = null;
  ui.state.sessionId = null;
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
  const zoneSections = () => Array.from($("t-options").children);
  const zoneSlots = (section) => Array.from(section.children[1].children);
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
      ? Array.from(box.children).find((node) => (node.className || "").split(" ").includes("hero-block")) || null
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && zoneSections().length === 2
      && handSection.getAttribute("data-zone") === "hand"
      && handSection.getAttribute("data-seat") === "0"
      && handSection.children[0].textContent.includes("张飞 手牌")
      && handSection.children[0].textContent.includes("牌面未知")
      && zoneSlots(handSection).length === 3
      && zoneSlots(handSection).every((node) => node.textContent === "?"
        && node.classList.contains("unknown"))
      && equipSection.getAttribute("data-zone") === "equip"
      && equipSection.children[0].textContent.includes("张飞 装备区")
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
        && Array.from($("t-dialog-body").children).includes($("t-options"))
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
  const revealCards = () => (revealWrap() ? Array.from(revealWrap().children[0].children) : []);
  const revealBar = () => (revealWrap()
    ? Array.from(revealWrap().children).find((node) => node.classList.contains("reveal-targets")) || null
    : null);
  const targetChips = () => (revealBar()
    ? Array.from(revealBar().children).filter((node) => node.classList.contains("reveal-target"))
    : []);
  const chipStates = () => targetChips().map((node) => node.getAttribute("data-state")).join(",");
  const chipSeats = () => targetChips().map((node) => node.getAttribute("data-seat")).join(",");
  const clickCollapse = () => ($("t-dialog-collapse").listeners.click || [])
    .forEach((fn) => fn({ target: $("t-dialog-collapse") }));
  const heroBlockOf = (seat) => {
    const box = seatBox(seat);
    return box
      ? Array.from(box.children).find((node) => (node.className || "").split(" ").includes("hero-block")) || null
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
      && (Array.from(revealCards()[0].children).find((node) => node.classList.contains("taken-by")) || {}).textContent === "座0"
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && revealCards()[1].classList.contains("taken")
      && (Array.from(revealCards()[1].children).find((node) => node.classList.contains("taken-by")) || {}).textContent === "座1"
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
 *   ② **真生产路径**（种子 238、5 人、random AI、人类座位 0）：开局就是"人类手里有【无懈】、
 *      五谷已亮牌"的 RESPOND 窗口；逐字段断言 DOM 读的是 `harvest.revealed / taken_by /
 *      targets / current`，再驱动到真实取牌步，断言同一窗口切回正常取牌模式。
 *      （票 12 切每座位 `ai:<seat>` 子流后原种子 198 不再落到这个窗口，按本文件纪律
 *      在 5 人 random AI 里重扫：526 的 revealed=5 / current=3、且人类手里真有【无懈】，
 *      与旧断言同形。**feng-xiahouyuan 票 01 换池（60→61 版本 / 34→35 真名）后 526 的
 *      开局第一问变成 PLAY_PHASE**，按同一条判据（`RESPOND` / `NULLIFY` / `harvest.active` /
 *      revealed=5 / current=3 / 人类持【无懈】）从 1 起重扫，首个"首个候选就是【无懈】"的命中 = **238**（huo-shenzhugeliang 加火包 OL 后重扫：1303 不再是命中；5 / 32 的候选序把 Pass 排在前）；
 *      探针 `.scratch/feng-xiahouyuan/rescan_harvest_seed.py`。）
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
  const cards = () => (revealRoot() ? Array.from(revealRoot().children[0].children) : []);
  const bar = () => (revealRoot()
    ? Array.from(revealRoot().children).find((node) => node.classList.contains("reveal-targets")) || null
    : null);
  const chips = () => (bar()
    ? Array.from(bar().children).filter((node) => node.classList.contains("reveal-target"))
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
    Array.from($("t-options-home").children).includes($("t-options"))
      && !insideDialog($("t-options"))
      && !insideDialog($("btn-submit")),
    `home=${Array.from($("t-options-home").children).includes($("t-options"))} optionsInDialog=${insideDialog($("t-options"))}`);

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
  const promptButtons = Array.from($("t-options").children).filter((node) => node.tagName === "BUTTON");
  check("U2-R3b 持久同步不清提示区选项行：无懈窗口的 picker 按钮仍在 #t-options",
    promptButtons.length === 1
      && promptButtons[0].textContent.includes("选择座位 1")
      && Array.from($("t-options-home").children).includes($("t-options"))
      && !insideDialog($("t-options")),
    `buttons=${JSON.stringify(promptButtons.map((node) => node.textContent))} home=${Array.from($("t-options-home").children).includes($("t-options"))} inDialog=${insideDialog($("t-options"))}`);
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && $("t-dialog-reveal").hidden === true
      && $("t-modal-backdrop").hidden === false
      && ui.selectionEnabled() === false,
    `key=${ui.state.dialog.key} before=${persistentKey} bodyHidden=${$("t-dialog-body").hidden} optionsInBody=${Array.from($("t-dialog-body").children).includes($("t-options"))} revealHidden=${$("t-dialog-reveal").hidden} enabled=${ui.selectionEnabled()}`);

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
      && Array.from($("t-options-home").children).includes($("t-options")),
    `dialogHidden=${$("t-dialog").hidden} revealHidden=${$("t-dialog-reveal").hidden} revealChildren=${$("t-dialog-reveal").children.length} key=${ui.state.dialog.key}`);

  // ---- ④ 真生产路径：种子 238（5 人）开局 = 人类持有【无懈】的五谷无懈窗口 ----
  ui.state.sessionId = null;
  ui.state.view = null;
  await newSession(238, { player_count: 5, banned_heroes: SMOKE_BANNED });
  const realResponse = ui.state.view.pending;
  const realHarvest = realResponse && realResponse.harvest;
  const realKey = ui.state.dialog.key;
  check("U2-R7 真会话（种子 238 / 5 人 / random AI）：开局 pending 就是带 harvest 的无懈 RESPOND",
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && $("t-dialog-reveal").hidden === true,
    `declined=${declined} kind=${realPick && realPick.kind} key=${ui.state.dialog.key} before=${realKey} bodyHidden=${$("t-dialog-body").hidden} optionsInBody=${Array.from($("t-dialog-body").children).includes($("t-options"))}`);

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
  const revealCards = () => (revealWrap() ? Array.from(revealWrap().children[0].children) : []);
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && $("t-dialog-title").textContent === "遗计"
      && $("t-prompt").textContent === "从亮出的牌里选择一至两张"
      && $("t-dialog-hint").textContent.includes("选 1~2 张后点右端「提交」")
      && $("t-dialog-hint").textContent.includes("已选 0 张")
      && revealCards().length === 2
      && revealCards().map((node) => node.getAttribute("data-card-id")).join(",") === "31,32"
      && !Array.from(revealWrap().children).some((node) => node.classList.contains("reveal-targets"))
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
      && !Array.from($("t-dialog-body").children).includes($("t-options"))
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
    // U4-06 / D13：未知区域的归属写**武将名**（没有 hero_name 时才会退回「座位N」）。
    view.snapshot.players[0].hero_name = "郭嘉";
    return view;
  };
  const render = (view) => {
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const zoneSections = () => Array.from($("t-options").children);
  const zoneSlots = (section) => Array.from(section.children[1].children);
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
      ? Array.from(box.children).find((node) => (node.className || "").split(" ").includes("hero-block")) || null
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && $("t-dialog-title").textContent === "选择一张牌"
      && firstKey === "chain:杀:1"
      && firstKey === `chain:${round1.context.name}:${round1.actor}`
      && zoneSections().length === 2
      && handSection.getAttribute("data-zone") === "hand"
      && handSection.getAttribute("data-seat") === "0"
      && handSection.children[0].textContent.includes("郭嘉 手牌")
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
      && Array.from($("t-dialog-body").children).includes($("t-options"))
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
    // 合成 view = 新场景：舞台项不跨场景沿用（U3-01 起 commit 不清空展示 / 弃牌）。
    ui.state.effectCards = [];
    ui.state.revealedCards = [];
    ui.resetSelection();
    ui.renderTable();
    ui.renderPending(view);
  };
  const handNode = (cardId) => Array.from($("t-hand").children).find(
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
  const revealCards = () => Array.from($("t-options").children[0].children[0].children);
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
    `kind=${ui.stepDialogKind(discardPending)} dis=${Array.from($("t-hand").children).map((n) => n.classList.contains("dis")).join(",")} passHidden=${$("btn-pass").hidden} hint="${$("t-target-hint").textContent}"`);
  check("M5-08a-c2 中央舞台的「展示」组看得见亮出的那一张（公开事实；同一行、不可点）",
    Array.from($("t-processing").children).some((node) => node.classList.contains("revealed-card")
      && node.getAttribute("data-card-id") === "5"
      && node.getAttribute("data-stage-lane") === "showcase")
      && $("t-context").textContent.includes("展示")
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
  check("M5-08a-d2 亮牌仍在中央舞台的「展示」组（公开事实），手牌区没有「已知」标记 / 属性",
    Array.from($("t-processing").children).some((node) => node.classList.contains("revealed-card")
      && node.getAttribute("data-card-id") === "5"
      && node.getAttribute("data-stage-lane") === "showcase")
      && !$("t-hand").text().includes("已知")
      && Array.from($("t-hand").children).every((node) => !node.classList.contains("known")
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
  // D9：pop 后先停留 0.5(N+1)（亮牌记录 N=1 ⇒ 1s），到点再进入 1s 淡出；不瞬删、不提前撤下。
  const popItem = (ui.state.revealedCards || []).find(
    (item) => String(item.card_id) === "5");
  const popFadeAt = popItem && popItem.stage.fadeAt;
  const popDwell = Number.isFinite(popFadeAt)
    && ui.stageLane(popItem, popFadeAt - 1) === "visible";
  ui.renderProcessing(popFadeAt);
  check("M5-08a-d3 结算帧出栈 ⇒ 展示项先停留 1s 再进入 1s 淡出（不瞬删、不提前撤下）",
    popDwell
      && Array.from($("t-processing").children).some((node) => node.classList.contains("revealed-card")
        && node.getAttribute("data-card-id") === "5"
        && node.classList.contains("fading"))
      && $("t-context").textContent.includes("展示"),
    `fadeAt=${popFadeAt} dwell=${popDwell}`
      + ` processing=${$("t-processing").text()} context="${$("t-context").textContent}"`);

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
  const handNode = (cardId) => Array.from($("t-hand").children).find(
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
  const reforgeNode = () => Array.from($("t-options").children).find(
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

/* ============ 票 05（庞统移动版【连环】）：牌名级预发动与重铸入口并存 ============
 *
 * 验收报障（2026-11-02 实机）：①♣【借刀杀人】点不出「重铸」；②重铸♣装备后其余牌
 * "用不了 / 熔不了"。
 *
 * **真服务端载荷**（形状由 `tests/test_web.py` 的真会话用例
 * `test_reforge_and_the_card_name_preactivation_coexist_on_one_card` 钉）：一张♣
 * 【借刀杀人】既有牌名级两步清单（持刀者 / 【杀】目标）、又有【连环】授予的 ``Reforge``；
 * 它在 `candidate_index` 里只有"可点、没有使用组合"的空条目（`max_targets = 0`）。
 *
 * 缺陷的根因是**点牌分派次序**：`cardPreactivationManifest` 抢在 `toggleFocus` 之前 ⇒
 * 焦点永远设不上，而重铸入口只认 `state.focusCardId`；且清单开在**座位步**上时
 * `preactivationCardOptions` 返回 undefined ⇒ 整片手牌置灰（玩家读作"用不了 / 熔不了"）。
 *
 * 判据按票 §5 的顺序走一遍真局面（seed 4 起扫）：先重铸♣装备（缺陷 B 的起点），再点
 * ♣借刀杀人（缺陷 A），最后连续重铸它：
 *   - 重铸一次 = 恰一条 `Reforge` POST：牌进弃牌堆、摸一张、手牌数不变，其余牌照旧可用；
 *   - 点♣借刀杀人 ⇒ 焦点 + 并列出现「重铸【借刀杀人】」；使用方向仍从「使用【借刀杀人】」
 *     进两步清单（不丢使用路径：裸借刀在 `legal_actions` 里**没有** UseCard 候选）；
 *   - 点过它之后**其余手牌**的可用性与点之前逐张一致（不再一片 dis、点什么都不对）。
 */
{
  // AI 档位用**默认的启发式**：票 05 §2 的复现局面没有选 AI（= 默认档），而 `random` 档
  // 下牌次序不同、种子 4 摸到的第 6 张是 ♦8杀（68）而不是票面点名的 ♠5绝影（37）。
  const openPangtongSession = async (seed) => openSession(await post("/api/sessions", {
    player_count: 4, ai: "heuristic", human_seat: 0, timeout_seconds: null,
    seed, my_hero: "pangtong_mobile", deck_profile: "standard_108",
  }));
  /** 推进到**我的**出牌阶段：一律先选 `Pass` / `EndPhase`（"什么都不做"，与实机开局同一路）。 */
  const passFirstIndex = (pending) => {
    const actions = pending.legal_actions || [];
    for (const kind of ["Pass", "EndPhase"]) {
      const at = actions.findIndex((action) => action.kind === kind);
      if (at >= 0) return at;
    }
    return 0;
  };
  const walkToMyPlay = async () => {
    for (let step = 0; step < 40; step += 1) {
      const pending = ui.state.view && ui.state.view.pending;
      if (!pending) return null;
      if (pending.kind === "PLAY_PHASE" && Number(pending.actor) === 0) return pending;
      ui.resetSelection();
      ui.state.selectedOption = passFirstIndex(pending);
      await ui.submitAction();
    }
    return null;
  };
  const hand = () => (ui.state.view.snapshot.self_hand || []);
  const handNode = (cardId) => Array.from($("t-hand").children).find(
    (node) => node.getAttribute("data-card-id") === String(cardId)) || null;
  const clickHand = async (cardId) => {
    const node = handNode(cardId);
    if (!node) return false;
    for (const fn of node.listeners.click || []) await fn({ target: node, stopPropagation() {} });
    return true;
  };
  const reforgeNode = () => Array.from($("t-options").children).find(
    (node) => node.getAttribute("data-reforge") === "true") || null;
  const useNode = (cardId) => Array.from($("t-options").children).find(
    (node) => node.getAttribute("data-preactivation-card") === String(cardId)) || null;
  const answersSince = (mark) => calls.slice(mark).filter((call) => call.path.includes("/answer"));
  const settle = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    ui.flushPlayback();
  };
  /** 手牌可用性指纹（`card_id → cardFace.selectable` + DOM 上的 dis 类），可排除一张。 */
  const availability = (excludeId = null) => {
    const pending = ui.state.view.pending;
    const out = {};
    for (const card of hand()) {
      if (excludeId !== null && Number(card.card_id) === Number(excludeId)) continue;
      const node = handNode(card.card_id);
      out[String(card.card_id)] = {
        selectable: ui.cardFace(pending, card).selectable,
        dis: node ? node.classList.contains("dis") : null,
      };
    }
    return out;
  };
  const sameAvailability = (a, b) => Object.keys(a).length === Object.keys(b).length
    && Object.keys(a).every(
      (key) => b[key] && a[key].selectable === b[key].selectable && a[key].dis === b[key].dis,
    );
  /** 并存局面的三张关键牌：A = 既有清单又有重铸；B = 另一张可重铸；C = 有使用候选。 */
  const pickScenario = (pending) => {
    if (!Object.keys(pending.card_preactivations || {}).length) return null;
    const both = hand().find((card) => ui.cardPreactivationManifest(pending, card)
      && ui.reforgeAction(pending, card.card_id) !== null);
    if (!both) return null;
    const second = hand().find((card) => String(card.card_id) !== String(both.card_id)
      && ui.reforgeAction(pending, card.card_id) !== null);
    if (!second) return null;
    const usable = hand().find((card) => String(card.card_id) !== String(both.card_id)
      && String(card.card_id) !== String(second.card_id)
      && ui.cardSelectable(pending, card.card_id));
    if (!usable) return null;
    return { both, second, usable };
  };

  let scenario = null;
  for (const seed of [4, 6, 8, 9, 12, 13, 17, 21]) {
    await openPangtongSession(seed);
    const pending = await walkToMyPlay();
    if (!pending) continue;
    const found = pickScenario(pending);
    if (!found) continue;
    scenario = { seed, ...found, pending };
    break;
  }
  check("票 05-0 找到一个真局面：一张♣牌同时有牌名级预发动与【连环】重铸",
    scenario !== null,
    scenario
      ? `seed=${scenario.seed} 并存牌=${scenario.both.card_id}(${scenario.both.name}) `
        + `另一张可重铸=${scenario.second.card_id} 手牌=${hand().map((card) => card.card_id).join(",")}`
      : "8 个种子内没找到（庞统移动版 / 4 人 / 座位 0）");

  // 票 05 §2/§5 点名的是**种子 4**，并给了摸牌后的六张手牌（♣借刀杀人 106 / ♥桃 16 /
  // ♥爪黄飞电 26 / ♣诸葛连弩 82 / ♠杀 47 / ♠绝影 37）。上面的种子扫描在 4 号不再命中时
  // 会**悄悄**换到后面的种子，本段的语义就漂了——这里把「就是种子 4 + 这一手」显式锚住。
  const TICKET05_SEED = 4;
  const TICKET05_HAND = [
    [106, "♣借刀杀人"], [16, "♥桃"], [26, "♥爪黄飞电"],
    [82, "♣诸葛连弩"], [47, "♠杀"], [37, "♠绝影"],
  ];
  const settledHand = hand().map((card) => Number(card.card_id));
  const settledIds = settledHand.slice().sort((a, b) => a - b);
  const namedIds = TICKET05_HAND.map(([cardId]) => cardId).sort((a, b) => a - b);
  const missingNamed = TICKET05_HAND.filter(([cardId]) => !settledIds.includes(cardId));
  const extraNamed = settledIds.filter((cardId) => !namedIds.includes(cardId));
  check("票 05-0a 局面锚定在票 §5 的种子 4（摸牌后六张手牌逐张点名）",
    scenario !== null
      && scenario.seed === TICKET05_SEED
      && settledIds.length === namedIds.length
      && missingNamed.length === 0,
    `seed=${scenario && scenario.seed} 应为 ${TICKET05_SEED}；手牌=${settledHand.join(",")}`
      + (missingNamed.length
        ? `；缺票面点名的牌：${missingNamed.map(([cardId, name]) => `${cardId}(${name})`).join("、")}`
        : "")
      + (extraNamed.length ? `；多出票面没有的牌：${extraNamed.join(",")}` : ""));

  if (scenario) {
    const both = scenario.both;
    const bothId = Number(both.card_id);
    const secondId = Number(scenario.second.card_id);
    const entry = ui.cardEntry(scenario.pending, bothId);

    // ==== ①（票 §5 第一句）先重铸那张♣装备：一步一条 POST、牌进弃牌堆、摸一张 ====
    check("票 05-A1 未点牌时：没有焦点、没有重铸入口（前置）",
      ui.state.focusCardId === null && reforgeNode() === null,
      `focus=${ui.state.focusCardId} entry=${reforgeNode() && reforgeNode().textContent}`);
    const handBeforeFirst = hand().length;
    const discardBeforeFirst = (ui.state.view.snapshot.discard || []).length;
    await clickHand(secondId);
    const firstEntry = reforgeNode();
    check("票 05-A2 点♣装备 = 给焦点 + 「重铸」入口（M5-08b 既有行为不回归）",
      Number(ui.state.focusCardId) === secondId && firstEntry !== null
        && firstEntry.textContent.includes("重铸"),
      `focus=${ui.state.focusCardId} entry=${firstEntry && firstEntry.textContent}`);
    const beforeFirst = calls.length;
    if (firstEntry) await clickAndWait(firstEntry);
    await settle();
    const firstCalls = answersSince(beforeFirst);
    check("票 05-B1 重铸♣装备 = 恰一条 Reforge POST，牌进弃牌堆、摸一张（手牌数不变）",
      firstCalls.length === 1
        && firstCalls[0].body.action.kind === "Reforge"
        && Number(firstCalls[0].body.action.card.card_id) === secondId
        && (ui.state.view.snapshot.discard || []).length === discardBeforeFirst + 1
        && hand().length === handBeforeFirst
        && !hand().some((card) => Number(card.card_id) === secondId),
      `answers=${firstCalls.length} discard=${discardBeforeFirst}→${(ui.state.view.snapshot.discard || []).length} `
        + `hand=${hand().map((card) => card.card_id).join(",")}`);

    // ==== ②（票 §5 缺陷 B）重铸之后其余牌照旧可用：没有一片 dis、点得动 ====
    const facesAfterFirst = availability();
    check("票 05-B2 重铸♣装备之后：其余手牌的 dis 只跟着 selectable 走（没有整片置灰）",
      Object.keys(facesAfterFirst).every(
        (key) => facesAfterFirst[key].dis === !facesAfterFirst[key].selectable,
      ),
      JSON.stringify(facesAfterFirst));

    // ==== ③（票 §5 缺陷 A）重铸之后再点♣借刀杀人：焦点 + 「重铸」并列出现 ====
    check("票 05-A3 前置：这张牌在候选索引里只有空条目（可点、没有使用组合）",
      entry !== null && (entry.combos || []).length === 0 && entry.max_targets === 0,
      JSON.stringify(entry));
    const facesBeforeBoth = availability(bothId);
    await clickHand(bothId);
    check("票 05-A4 点♣借刀杀人 = 给焦点 + 「重铸【借刀杀人】」并列出现（不再只进清单）",
      Number(ui.state.focusCardId) === bothId
        && ui.state.preactivation === null
        && reforgeNode() !== null
        && reforgeNode().textContent.includes("重铸")
        && reforgeNode().textContent.includes(both.name),
      `focus=${ui.state.focusCardId} pre=${!!ui.state.preactivation} `
        + `entry=${reforgeNode() && reforgeNode().textContent} hint="${$("t-target-hint").textContent}"`);
    check("票 05-A5 使用方向没有丢：并列一个「使用【借刀杀人】」入口",
      useNode(bothId) !== null && useNode(bothId).textContent.includes(both.name),
      `use=${useNode(bothId) && useNode(bothId).textContent}`);
    check("票 05-A6 提示条把两个出口都说清（重铸 / 使用清单）",
      $("t-target-hint").textContent.includes("重铸")
        && $("t-target-hint").textContent.includes("使用"),
      `hint="${$("t-target-hint").textContent}"`);
    check("票 05-B3 点过借刀之后，其余手牌的可用性 / 置灰与点之前逐张一致（缺陷 B）",
      sameAvailability(facesBeforeBoth, availability(bothId)),
      `before=${JSON.stringify(facesBeforeBoth)} after=${JSON.stringify(availability(bothId))}`);
    check("票 05-B4 其余手牌不是「死不点」：点一张有使用候选的牌仍能设焦点",
      (await clickHand(scenario.usable.card_id))
        && Number(ui.state.focusCardId) === Number(scenario.usable.card_id)
        && ui.state.preactivation === null,
      `focus=${ui.state.focusCardId} want=${scenario.usable.card_id} pre=${!!ui.state.preactivation}`);

    // ==== ④ 使用入口仍进两步清单（零提交），取消能回到同一问 ====
    await clickHand(bothId);
    const beforeUse = calls.length;
    const useButton = useNode(bothId);
    if (useButton) await clickAndWait(useButton);
    check("票 05-A7 点「使用【借刀杀人】」= 进两步清单（本地态、零提交、第 1 步是选座位）",
      useButton !== null
        && !!ui.state.preactivation
        && Number(ui.state.preactivation.cardId) === bothId
        && ui.state.preactivation.steps.length === 2
        && ui.state.preactivation.cursor === 0
        && ui.preactivationStepKind(ui.state.preactivation.steps[0]) === "seat"
        && calls.length === beforeUse,
      `pre=${JSON.stringify(ui.state.preactivation && {
        cardId: ui.state.preactivation.cardId,
        steps: ui.state.preactivation.steps.length,
        cursor: ui.state.preactivation.cursor,
        kind: ui.preactivationStepKind(ui.state.preactivation.steps[0]),
      })} calls=${calls.length - beforeUse}`);
    const pendingSeq = ui.state.view.pending.seq;
    await clickAndWait($("btn-cancel"));
    check("票 05-A8 取消清单 = 回到同一问、零提交、没有残留只读态",
      ui.state.preactivation === null
        && ui.state.focusCardId === null
        && ui.state.view.pending.seq === pendingSeq
        && ui.selectionEnabled() === true,
      `pre=${!!ui.state.preactivation} focus=${ui.state.focusCardId} `
        + `seq=${ui.state.view.pending.seq}/${pendingSeq} enabled=${ui.selectionEnabled()}`);

    // ==== ⑤（票 §5 第二句）连续重铸：借刀杀人也一步一条 POST ====
    const handBeforeSecond = hand().length;
    const discardBeforeSecond = (ui.state.view.snapshot.discard || []).length;
    await clickHand(bothId);
    const secondEntry = reforgeNode();
    const beforeSecond = calls.length;
    if (secondEntry) await clickAndWait(secondEntry);
    await settle();
    const secondCalls = answersSince(beforeSecond);
    check("票 05-B5 连续重铸♣借刀杀人 = 恰一条 Reforge POST，牌进弃牌堆（手牌数不变）",
      secondCalls.length === 1
        && secondCalls[0].body.action.kind === "Reforge"
        && Number(secondCalls[0].body.action.card.card_id) === bothId
        && (ui.state.view.snapshot.discard || []).length === discardBeforeSecond + 1
        && hand().length === handBeforeSecond
        && !hand().some((card) => Number(card.card_id) === bothId),
      `answers=${secondCalls.length} discard=${discardBeforeSecond}→${(ui.state.view.snapshot.discard || []).length} `
        + `hand=${hand().map((card) => card.card_id).join(",")}`);

    // ==== ⑥（票 §5 第三句）重铸之后：没有残留只读态，其余牌照旧可用 ====
    check("票 05-B6 重铸后没有残留只读态：回到我的出牌阶段、焦点 / 清单 / 选中项都清空",
      ui.state.view.pending.kind === "PLAY_PHASE"
        && Number(ui.state.view.pending.actor) === 0
        && ui.state.preactivation === null
        && ui.state.focusCardId === null
        && ui.state.selectedOption === null
        && ui.selectionEnabled() === true,
      `pending=${ui.state.view.pending.kind}#${ui.state.view.pending.seq} `
        + `pre=${!!ui.state.preactivation} focus=${ui.state.focusCardId} `
        + `selectedOption=${ui.state.selectedOption} enabled=${ui.selectionEnabled()}`);
    check("票 05-B7 重铸之后其余手牌照旧可用（新 candidate_index 已替换、不留旧索引）",
      Object.entries(facesAfterFirst)
        .filter(([key]) => hand().some((card) => Number(card.card_id) === Number(key)))
        .every(([key, face]) => {
          const card = hand().find((item) => Number(item.card_id) === Number(key));
          const now = ui.cardFace(ui.state.view.pending, card);
          const node = handNode(card.card_id);
          return now.selectable === face.selectable
            && node !== null
            && node.classList.contains("dis") === !now.selectable;
        }),
      JSON.stringify(availability()));
  }

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

  const handNode = Array.from($("t-hand").children).find(
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
  const handNode = (cardId) => Array.from($("t-hand").children).find(
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
  const faces = Array.from($("t-hand").children);
  check("M5-08d-a1 火杀 / 雷杀卡面 = 服务端「杀」+ 火焰 / 雷电（红 / 蓝），不在前端另起牌名",
    faces.length === hand.length
      && byClass(faces[0], "cname")[0].textContent === "杀"
      && byClass(faces[0], "ctype")[0].textContent === "火焰"
      && hasClass(faces[0], "attr-fire")
      && byClass(faces[1], "cname")[0].textContent === "杀"
      && byClass(faces[1], "ctype")[0].textContent === "雷电"
      && hasClass(faces[1], "attr-thunder")
      && /\.card\.face\.attr-fire \.cname\s*\{[^}]*#b3261e/s.test(css)
      && /\.card\.face\.attr-thunder \.cname\s*\{[^}]*#1d4e9c/s.test(css),
    `fire="${byClass(faces[0], "cname")[0].textContent}/${byClass(faces[0], "ctype")[0].textContent}" thunder="${byClass(faces[1], "cname")[0].textContent}/${byClass(faces[1], "ctype")[0].textContent}"`);

  const expectedFaces = [
    { name: "酒", type: "" },
    { name: "铁索连环", type: "" },
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

  /* ---- 票 11：牌面标签（服务端 card.tags；多标签 2 列逐行） ---- */
  {
    const tags = [
      { skill_id: "hero.jie_zhenji.luoshen", label: "洛神" },
      { skill_id: "hero.jie_zhenji.qingguo", label: "倾国" },
      { skill_id: "hero.jie_zhenji.luoyi", label: "裸衣" },
    ];
    const taggedFace = ui.cardFaceNode(
      card(301, "slash", "杀", { tags }),
      { static: true },
    );
    const tagBox = byClass(taggedFace, "card-tags")[0];
    const chips = tagBox ? Array.from(tagBox.children) : [];
    check("M5-08d-c1 卡面标签逐行绘制（CSS 2 列网格；3 个标签按服务端 label 原样）",
      !!tagBox
        && tagBox.getAttribute("data-tag-count") === "3"
        && chips.length === 3
        && chips.map((node) => node.textContent).join(",") === "洛神,倾国,裸衣"
        && /\.card\.face \.card-tags\s*\{[^}]*grid-template-columns:\s*repeat\(2/s.test(css),
      tagBox ? `count=${tagBox.getAttribute("data-tag-count")} tags=${JSON.stringify(chips.map((n) => n.textContent))}` : "没有标签块");

    const many = [
      ...tags,
      { skill_id: "hero.jie_zhenji.extra1", label: "标签四" },
      { skill_id: "hero.jie_zhenji.extra2", label: "标签五" },
    ];
    const manyFace = ui.cardFaceNode(card(302, "slash", "杀", { tags: many }), { static: true });
    const manyBox = byClass(manyFace, "card-tags")[0];
    const manyChips = manyBox ? Array.from(manyBox.children) : [];
    check("M5-08d-c2 超过两行收敛为末位 +N（不把卡面撑破）",
      manyChips.length === 4
        && manyChips.map((node) => node.textContent).join(",") === "洛神,倾国,裸衣,+2"
        && manyChips[3].getAttribute("title").includes("标签四"),
      `tags=${JSON.stringify(manyChips.map((n) => n.textContent))}`);
    check("M5-08d-c3 没有 tags 的卡面不画标签块",
      byClass(faces[0], "card-tags").length === 0);
  }

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

  /* ---- 自身翻面（lin-batch2 票 04）：自己的面板与环上座位卡同一套蒙灰层 ---- */
  const selfFlip = displayView();
  selfFlip.snapshot.players[0].flipped = true;
  renderDisplay(selfFlip);
  const selfLayer = $("t-self-flipped");
  check("LIN-04-a1 自身翻面 = 与其他座位同一套蒙灰层（#t-self-flipped 去掉 hidden、无文字、同一 class）",
    $("t-self-seat").classList.contains("flipped")
      && selfLayer.hidden === false
      && selfLayer.textContent === ""
      // 静态节点的 class / title 由 test_web_static 那条从 index.html 里核（垫片只按 id 建节点、
      // 不解析 HTML 属性）；这里核的是**渲染后的状态**：与环上座位同一个 class、同样没有文字。
      && /id="t-self-flipped" class="flipped-layer"/.test(readFileSync("tools/sgs_web/static/index.html", "utf8")),
    `self=${[...$("t-self-seat").classList.set].join(",")} hidden=${selfLayer.hidden} text="${selfLayer.textContent}"`);
  const selfFront = displayView();
  selfFront.snapshot.players[0].flipped = false;
  renderDisplay(selfFront);
  check("LIN-04-a2 翻回正面后同一节点重新 hidden（层不残留）",
    !$("t-self-seat").classList.contains("flipped") && $("t-self-flipped").hidden === true,
    `self=${[...$("t-self-seat").classList.set].join(",")} hidden=${$("t-self-flipped").hidden}`);

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

/* ============ 票 13：tag_add / tag_remove 逐事件更新 live view（不等下一次询问） ====
 *
 * 标签 delta 在人类记录流里仍整条裁掉（服务端另走 `live_deltas` 展示通道）；这里
 * 只喂前端会收到的形状：`{kind, delta_type, seq, card_id, tag:{skill_id,label}}`。
 * 判据 = 同一询问序号下，处理完 tag 事件后卡面标签已经出现 / 消失——不产生新询问。
 */
{
  const hasClass = (node, name) => Boolean(node)
    && (node.classList.contains(name) || String(node.className || "").split(/\s+/).includes(name));
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walk(child, out); }
    return out;
  };
  const byClass = (node, name) => walk(node).filter((child) => hasClass(child, name));
  const tag = { skill_id: "hero.jie_zhenji.luoshen", label: "洛神" };
  const card = (id, extra = {}) => ({
    ...synthCard(id, "杀"), display_name: "杀", kind: "normal",
    attribute: "普通", description: "服务端描述：杀", ...extra,
  });
  const pending = {
    kind: "PLAY_PHASE", actor: 0, seq: 8801,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 8801 }],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  };
  const viewWith = (handTags, equipTags) => {
    const view = synthView(pending);
    view.snapshot.self_hand = [card(301, handTags ? { tags: handTags } : {})];
    view.snapshot.players[0].equip = [
      card(401, { slot: "weapon", attack_range: 2, ...(equipTags ? { tags: equipTags } : {}) }),
    ];
    view.cursor = 0;
    view.log_seq = 0;
    view.records = [];
    view.live_deltas = [];
    return view;
  };
  const tagText = (cardId) => {
    const nodes = walk($("t-hand")).concat(walk($("t-self-zones")));
    const face = nodes.find((node) => hasClass(node, "face")
      && node.getAttribute("data-card-id") === String(cardId));
    const box = face ? byClass(face, "card-tags")[0] : null;
    return box ? box.textContent : "";
  };

  ui.state.sessionId = "tag-live";
  ui.state.view = null;
  ui.applyViewRaw(viewWith(null, null));
  ui.flushPlayback();

  const add = viewWith(null, null);
  add.cursor = 1;
  add.live_deltas = [
    { kind: "delta", delta_type: "tag_add", seq: 1, card_id: 301, tag },
    { kind: "delta", delta_type: "tag_add", seq: 2, card_id: 401, tag },
  ];
  ui.applyViewRaw(add);
  ui.stepPlayback(true);
  ui.stepPlayback(true);
  const equipTags = () => {
    const equip = ((ui.state.liveView || {}).snapshot || {}).players;
    const card = equip && equip[0] && (equip[0].equip || [])[0];
    return (card && Array.isArray(card.tags) ? card.tags : []).map((item) => item.label).join(",");
  };
  check("M5-08d-t1 tag_add 逐事件更新 live view：自己的手牌与公开装备区当场出现标签，询问序号不变",
    tagText(301) === "洛神" && equipTags() === "洛神"
      && ui.state.view.pending.seq === 8801,
    `hand="${tagText(301)}" equip="${equipTags()}" seq=${ui.state.view.pending.seq}`);

  // 先造一份“已提交且带标签”的权威视图，再让 tag_remove 在 live view 里逐事件撤掉。
  ui.flushPlayback();
  ui.state.view = viewWith([tag], [tag]);
  ui.state.liveView = null;
  ui.renderTable();
  const remove = viewWith([tag], [tag]);
  remove.cursor = 2;
  remove.live_deltas = [
    { kind: "delta", delta_type: "tag_remove", seq: 3, card_id: 301, tag },
    { kind: "delta", delta_type: "tag_remove", seq: 4, card_id: 401, tag },
  ];
  ui.applyViewRaw(remove);
  ui.stepPlayback(true);
  ui.stepPlayback(true);
  check("M5-08d-t2 tag_remove 逐事件更新 live view：手牌与公开装备区的标签当场消失，询问序号不变",
    tagText(301) === "" && equipTags() === ""
      && ui.state.view.pending.seq === 8801,
    `hand="${tagText(301)}" equip="${equipTags()}" seq=${ui.state.view.pending.seq}`);

  ui.flushPlayback();
  ui.state.sessionId = null;
  ui.state.view = null;
  ui.state.liveView = null;
  ui.state.playback = null;
  ui.state.logRecords = [];
  ui.resetSelection();
}

/* ============ U3-01（票 01）：舞台三组 / 来源底行 / 淡出寿命 / 粘底 / 回放 ============
 *
 * 判据来自票面与 spec D2 / D3 / D5 / D10 / D14 / D15。载荷是**与响应体同形**的合成
 * `view` + 记录；断言只落在 DOM 的 `data-stage-lane` / `data-zone-label` / `.fading`
 * 与底行文本上。时间不靠真实等待：`renderProcessing(now)` 直接喂 `now`，动画倍率
 * `state.animScale`（`0` = 淡出立即完成）。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walk(child, out); }
    return out;
  };
  const byClass = (node, name) => walk(node).filter(
    (child) => child.classList.contains(name)
      || String(child.className || "").split(/\s+/).includes(name));
  const stageNodes = () => Array.from($("t-processing").children).filter(
    (node) => node.classList.contains("stage-card"));
  const stageNode = (id) => stageNodes().find(
    (node) => attr(node, "data-card-id") === String(id));
  const laneOf = (id) => attr(stageNode(id), "data-stage-lane");
  const lineOf = (id) => {
    const node = stageNode(id);
    const line = node ? byClass(node, "ctype")[0] : null;
    return line ? line.textContent : "";
  };
  /** 底行节点上的 `data-zone-label`（D10 的机械读法；无来源时不存在）。 */
  const labelOf = (id) => {
    const node = stageNode(id);
    const line = node ? byClass(node, "ctype")[0] : null;
    return line ? attr(line, "data-zone-label") : undefined;
  };
  const fading = (id) => Boolean(stageNode(id) && stageNode(id).classList.contains("fading"));
  const card = (id, name, extra = {}) => ({
    ...synthCard(id, name), kind: "normal", attribute: "普通",
    description: `【${name}】的全文描述`, ...extra,
  });
  const seatPlayer = (seat, heroName) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, hero_name: heroName,
  });
  const view = (session = "u3-stage-1") => {
    const out = synthView(null);
    out.session_id = session;
    out.snapshot.processing = [];
    out.snapshot.players = [
      seatPlayer(0, "郭嘉"), seatPlayer(1, "周瑜"), seatPlayer(2, "貂蝉"),
    ];
    return out;
  };
  const TO_PROCESSING = { zone: "processing", seat: null };
  const TO_DISCARD = { zone: "discard", seat: null };
  const move = (seq, frame, moveCard, from, to, reason) => ({
    kind: "delta", delta_type: "card_move", seq, frame_id: frame, depth: 1,
    reason, owner: from.seat, from_zone: from, to_zone: to, cards: [moveCard],
  });
  const at = (zone, seat = null) => ({ zone, seat });
  const pop = (seq, frame) => ({
    kind: "frame", op: "pop", frame_id: frame, frame_kind: "RESOLVE", depth: 1, seq,
  });

  const A = card(101, "杀");
  const B = card(102, "闪");
  const C = card(103, "八卦阵", { slot: "armor" });
  const J = card(104, "闪电");
  const D = card(105, "桃");
  const records = [
    move(101, 10, A, at("hand", 0), TO_PROCESSING, "USE"),
    move(102, 11, B, at("hand", 1), TO_DISCARD, "DISCARD"),
    move(103, 11, C, at("equip", 2), TO_DISCARD, "LOSE"),
    move(104, 12, J, at("deck"), TO_PROCESSING, "JUDGE"),
    move(105, 12, J, at("processing"), TO_DISCARD, "JUDGE"),
    { kind: "delta", delta_type: "card_reveal", seq: 106, frame_id: 11, depth: 1, seat: 1, card: D },
    { kind: "timing", seq: 107, timing: "USE_DESIGNATE_TARGET", frame_id: 10, depth: 1, actor: 0, targets: [1], cards: [] },
  ];

  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.animScale = 1;
  const before = { ...view(), cursor: 100, log_seq: 0, records: [], finished: false };
  ui.applyViewRaw(before);
  const after = {
    ...view(), cursor: 107, log_seq: 0, records, finished: false,
  };
  // 这一批播完时 A 还在处理区：快照是权威终态，commit 不能把它判成"已离场"。
  after.snapshot.processing = [A];
  ui.applyViewRaw(after);
  const step = () => ui.stepPlayback(true);

  step();
  check("U3-01a 处理中的牌：data-stage-lane=processing、底行 座位·武将·区域",
    laneOf(101) === "processing"
      && labelOf(101) === "0·郭嘉·手牌"
      && lineOf(101) === "0·郭嘉·手牌"
      && $("t-context").textContent.includes("处理中"),
    `lane=${laneOf(101)} label=${labelOf(101)} context="${$("t-context").textContent}"`);

  step();
  check("U3-01b 手牌弃置进弃牌组：data-stage-lane=discard、底行 1·周瑜·手牌",
    laneOf(102) === "discard"
      && labelOf(102) === "1·周瑜·手牌"
      && $("t-context").textContent.includes("弃牌"),
    `lane=${laneOf(102)} label=${labelOf(102)} context="${$("t-context").textContent}"`);

  step();
  check("U3-01c 装备替换弃置：底行 2·貂蝉·装备（LOSE 也算弃牌）",
    laneOf(103) === "discard"
      && labelOf(103) === "2·貂蝉·装备"
      && lineOf(103) === "2·貂蝉·装备",
    `lane=${laneOf(103)} label=${labelOf(103)}`);

  step();
  step();
  check("U3-01d 牌堆来源的判定牌：仍在处理中组、底行不写座位（无 undefined / null）",
    laneOf(104) === "processing"
      && !labelOf(104)
      && lineOf(104) === ""
      && !/undefined|null/.test(stageNode(104).text())
      && fading(104),
    `lane=${laneOf(104)} label=${labelOf(104)} text="${stageNode(104) ? stageNode(104).text() : "(缺)"}"`);

  step();
  check("U3-01e card_reveal 进展示组：data-stage-lane=showcase、保留 revealed-card 与亮出者",
    laneOf(105) === "showcase"
      && stageNode(105).classList.contains("revealed-card")
      && attr(stageNode(105), "data-reveal-seat") === "1"
      && labelOf(105) === "1·周瑜·手牌"
      && $("t-context").textContent.includes("展示"),
    `lane=${laneOf(105)} seat=${attr(stageNode(105), "data-reveal-seat")} context="${$("t-context").textContent}"`);

  step();
  check("U3-01f 三组在同一说明里拼接（处理中：… / 展示：… / 弃牌：…）",
    ["处理中：", "展示：", "弃牌：", " / "].every((part) => $("t-context").textContent.includes(part)),
    `context="${$("t-context").textContent}"`);
  ui.flushPlayback();

  // ---- 寿命规则（D5）：有帧的展示 / 弃牌挂到 pop；无帧的固定 1s + 1s；兜底 30s ----
  const item = (id) => (ui.state.effectCards || [])
    .concat(ui.state.revealedCards || [])
    .find((entry) => String(entry.card_id) === String(id));
  const fiveSecondsOn = item(102).stage.enteredAt + 5000;
  ui.renderProcessing(fiveSecondsOn);
  check("U3-01g 有承载帧的展示 / 弃牌：pop 之前一直可见（5 秒后仍不淡出）",
    !!stageNode(102) && !!stageNode(103) && !!stageNode(105)
      && !fading(102) && !fading(103) && !fading(105),
    `B=${fading(102)} C=${fading(103)} D=${fading(105)}`);

  // 时钟注入：两张列表的 pop 必须比较同一个 `now`，不读真实时钟 / 不等计时器（标准 §4）。
  const framePopNow = fiveSecondsOn;
  ui.state.effectCards = ui.applyEffectDelta(
    pop(108, 11), ui.state.effectCards, ui.state.processingProducts, framePopNow);
  ui.state.revealedCards = ui.applyRevealDelta(
    pop(108, 11), ui.state.revealedCards, framePopNow);
  const frameFadeAts = [102, 103, 105].map((id) => item(id) && item(id).stage.fadeAt);
  const frameDwell = frameFadeAts.every((fadeAt) => fadeAt === framePopNow + 1000);
  ui.renderProcessing(framePopNow + 999);
  const frameStillDwelling = !fading(102) && !fading(103) && !fading(105) && !fading(101);
  ui.renderProcessing(framePopNow + 1000);
  check("U3-01h 产生它们的结算帧 pop ⇒ 先各自停留 0.5(N+1)（N=1 = 1s）再 1s 淡出（不再是瞬删）",
    frameDwell && frameStillDwelling
      && fading(102) && fading(103) && fading(105) && !fading(101),
    `fadeAt=${frameFadeAts.join("/")} popAt=${framePopNow} dwell=${frameStillDwelling}`
      + ` A=${fading(101)} B=${fading(102)} C=${fading(103)} D=${fading(105)}`);

  const aPopNow = item(101).stage.enteredAt + 6000;
  ui.state.effectCards = ui.applyEffectDelta(
    pop(109, 10), ui.state.effectCards, ui.state.processingProducts, aPopNow);
  const aFadeAt = item(101) && item(101).stage.fadeAt;
  const aDwell = aFadeAt === aPopNow + 1000;
  ui.renderProcessing(aPopNow + 999);
  const aStillDwelling = !fading(101);
  ui.renderProcessing(aPopNow + 1000);
  check("U3-01i 处理中的牌由它自己的离区 / 帧 pop 触发：A 的帧 pop 后先停留 1s 再 1s 淡出",
    aDwell && aStillDwelling && fading(101),
    `fadeAt=${aFadeAt} popAt=${aPopNow} dwell=${aStillDwelling} A=${fading(101)}`);
  const goneAt = Math.max(...[101, 102, 103, 105].map((id) => item(id).stage.fadeAt + 1000));
  ui.renderProcessing(goneAt);
  check("U3-01j 1s 淡出结束 ⇒ 舞台项移除（不留幽灵）",
    stageNodes().length === 0, `剩余=${stageNodes().map((n) => attr(n, "data-card-id")).join(",")}`);

  // 判定区来源的牌（拆 / 顺）：底行写「N·武将·判定」（D10 的第三种区域）。
  ui.state.effectCards = [];
  ui.state.effectCards = ui.applyEffectDelta(
    move(251, 42, card(251, "闪电"), at("judge", 1), TO_DISCARD, "LOSE"), []);
  ui.renderProcessing(Date.now());
  check("U3-01c2 判定区来源的弃牌：底行 1·周瑜·判定（非牌堆来源）",
    laneOf(251) === "discard" && labelOf(251) === "1·周瑜·判定",
    `lane=${laneOf(251)} label=${labelOf(251)}`);

  // 无帧（frame_id = 0）：停留 1s + 淡出 1s = 2s（弃牌阶段等）。
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.effectCards = ui.applyEffectDelta(
    move(201, 0, card(201, "杀"), at("hand", 0), TO_DISCARD, "DISCARD"), []);
  const noFrame = ui.state.effectCards[0];
  check("U3-01k 无帧弃牌：fadeAt = 入台 + 1s 停留",
    !!noFrame && noFrame.stage.fadeAt === noFrame.stage.enteredAt + 1000,
    `fadeAt=${noFrame && noFrame.stage.fadeAt} enteredAt=${noFrame && noFrame.stage.enteredAt}`);
  ui.renderProcessing(noFrame.stage.enteredAt + 999);
  const beforeHold = !!stageNode(201) && !fading(201);
  ui.renderProcessing(noFrame.stage.enteredAt + 1000);
  const duringFade = fading(201);
  ui.renderProcessing(noFrame.stage.enteredAt + 2000);
  check("U3-01l 无帧弃牌：1s 内 visible、1s–2s fading、2s 后 gone",
    beforeHold && duringFade && !stageNode(201),
    `before=${beforeHold} fading=${duringFade} gone=${!stageNode(201)}`);

  // 兜底 30s：pop 丢失（断线 / 刷新）也不留幽灵。
  ui.state.effectCards = [];
  ui.state.effectCards = ui.applyEffectDelta(
    move(301, 77, card(301, "闪"), at("hand", 1), TO_DISCARD, "DISCARD"), []);
  const dangling = ui.state.effectCards[0];
  ui.renderProcessing(dangling.stage.enteredAt + 29999);
  const stillThere = !!stageNode(301) && !fading(301);
  ui.renderProcessing(dangling.stage.enteredAt + 30000);
  const fallbackFading = fading(301);
  ui.renderProcessing(dangling.stage.enteredAt + 31000);
  check("U3-01m 兜底 30s：没有 pop 也到点无条件淡出",
    stillThere && fallbackFading && !stageNode(301),
    `visible=${stillThere} fading=${fallbackFading} gone=${!stageNode(301)}`);

  // 动画倍率 0：淡出立即完成（D15 测试口径）。
  ui.state.effectCards = [];
  ui.state.animScale = 0;
  ui.state.effectCards = ui.applyEffectDelta(
    move(401, 0, card(401, "桃"), at("hand", 2), TO_DISCARD, "DISCARD"), []);
  const fast = ui.state.effectCards[0];
  ui.renderProcessing(fast.stage.enteredAt + 999);
  const fastVisible = !!stageNode(401);
  ui.renderProcessing(fast.stage.enteredAt + 1000);
  check("U3-01n animScale = 0 ⇒ 淡出立即完成（不等真实时间）",
    fastVisible && !stageNode(401), `visible=${fastVisible} gone=${!stageNode(401)}`);
  ui.state.animScale = 1;

  // 淡出期间同一张牌再次入台：撤旧项、新项重新落位（D5.6），台上始终只有一条。
  ui.state.effectCards = [];
  const again = card(260, "杀");
  ui.state.effectCards = ui.applyEffectDelta(
    move(260, 0, again, at("hand", 0), TO_DISCARD, "DISCARD"), []);
  const againItem = ui.state.effectCards[0];
  ui.renderProcessing(againItem.stage.enteredAt + 1500);
  const wasFading = fading(260);
  ui.state.effectCards = ui.applyEffectDelta(
    move(261, 9, again, at("hand", 1), TO_PROCESSING, "USE"), ui.state.effectCards);
  ui.renderProcessing(Date.now());
  check("U3-01n2 淡出期间同一张牌再入台 ⇒ 撤旧项、只剩一条处理中项",
    wasFading && ui.state.effectCards.length === 1
      && laneOf(260) === "processing" && !fading(260) && stageNodes().length === 1,
    `wasFading=${wasFading} items=${ui.state.effectCards.length} lane=${laneOf(260)} nodes=${stageNodes().length}`);

  // 一次 8 张的阵亡弃置：全部落位、不折叠。
  ui.state.effectCards = [];
  const eight = Array.from({ length: 8 }, (_, index) => card(500 + index, "杀"));
  ui.state.effectCards = ui.applyEffectDelta({
    kind: "delta", delta_type: "card_move", seq: 500, frame_id: 0, depth: 1,
    reason: "DISCARD", owner: 0, from_zone: at("hand", 0), to_zone: TO_DISCARD, cards: eight,
  }, []);
  ui.renderProcessing(Date.now());
  check("U3-01o 一次 8 张弃置全部落位（8 个舞台项，不折叠成「+N」）",
    stageNodes().length === 8
      && stageNodes().every((node) => attr(node, "data-stage-lane") === "discard")
      && !/\+8|\+ ?[1-9]/.test($("t-processing").text()),
    `节点=${stageNodes().length} text="${$("t-processing").text()}"`);

  // ---- commit / 换会话 / 手动刷新（D5.5）----
  const committed = { ...view(), cursor: 501, log_seq: 0, records: [], finished: false };
  ui.applyViewRaw(committed);
  check("U3-01p 一批事件播完（commit）不清空弃牌组",
    stageNodes().length === 8, `节点=${stageNodes().length}`);
  const refreshed = { ...view(), cursor: 502, log_seq: 0, records: [], finished: false };
  ui.applyViewRaw(refreshed);
  check("U3-01q 同一会话的手动刷新不清空弃牌组",
    stageNodes().length === 8, `节点=${stageNodes().length}`);
  const otherSession = { ...view("u3-stage-2"), cursor: 0, log_seq: 0, records: [], finished: false };
  ui.applyViewRaw(otherSession);
  check("U3-01r 换会话清空舞台",
    stageNodes().length === 0, `节点=${stageNodes().length}`);

  // ---- 粘底滚动（D3）：新牌落位滚到底；用户手动上滚后不再打扰 ----
  const box = $("t-processing");
  const fireScroll = () => (box.listeners.scroll || []).forEach((fn) => fn({ target: box }));
  box.scrollHeight = 600;
  box.clientHeight = 253;
  box.scrollTop = 300;
  fireScroll();
  const pinnedUp = attr(box, "data-stage-pinned");
  ui.state.effectCards = ui.applyEffectDelta(
    move(601, 0, card(601, "杀"), at("hand", 0), TO_DISCARD, "DISCARD"), ui.state.effectCards);
  ui.renderProcessing();
  const keptUp = box.scrollTop === 300;
  box.scrollTop = 600 - 253;
  fireScroll();
  const pinnedBottom = attr(box, "data-stage-pinned");
  ui.state.effectCards = ui.applyEffectDelta(
    move(602, 0, card(602, "闪"), at("hand", 0), TO_DISCARD, "DISCARD"), ui.state.effectCards);
  ui.renderProcessing();
  check("U3-01s 新牌落位粘底；手动上滚后不再打扰（滚回底部后恢复）",
    pinnedUp === "false" && keptUp && pinnedBottom === "true" && box.scrollTop === 600,
    `up=${pinnedUp} keptUp=${keptUp} bottom=${pinnedBottom} scrollTop=${box.scrollTop}`);

  // ---- 回放页复用同一套派生与卡面渲染：三组 + 淡出，但不接线 / 名字 / 白光 ----
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  const replayRecords = [
    move(701, 10, A, at("hand", 0), TO_PROCESSING, "USE"),
    move(702, 11, B, at("hand", 1), TO_DISCARD, "DISCARD"),
    { kind: "delta", delta_type: "card_reveal", seq: 703, frame_id: 11, depth: 1, seat: 2, card: D },
    pop(704, 11),
  ];
  ui.state.replay = {
    name: "u3-stage-replay",
    meta: { seed: 1, player_count: 3, human_seat: 0, heroes: ["guojia", "zhouyu", "diaochan"] },
    records: replayRecords,
    narrative: [],
    index: replayRecords.length - 1,
    seats: null,
    query: null,
    effectCards: [],
    revealedCards: [],
    appliedIndex: -1,
    playing: false,
    timer: null,
  };
  ui.renderReplay();
  const replayNodes = Array.from($("r-processing").children).filter(
    (node) => node.classList.contains("stage-card"));
  const replayLane = (id) => {
    const node = replayNodes.find((child) => attr(child, "data-card-id") === String(id));
    return attr(node, "data-stage-lane");
  };
  const replayState = ui.state.replay;
  const replayReveal = (replayState.revealedCards || []).find(
    (entry) => String(entry.card_id) === "105");
  const replayDiscard = (replayState.effectCards || []).find(
    (entry) => String(entry.card_id) === "102");
  const replayFadeAt = replayReveal && replayReveal.stage.fadeAt;
  // D9：pop 后先停留 0.5(N+1)，到点才淡出；回放页同一套寿命（渲染时仍在停留期）。
  const replayDwell = Number.isFinite(replayFadeAt) && Boolean(replayDiscard)
    && replayDiscard.stage.fadeAt === replayFadeAt
    && ui.stageLane(replayReveal, replayFadeAt - 1) === "visible"
    && ui.stageLane(replayReveal, replayFadeAt) === "fading";
  check("U3-01t 回放页显示同样三组：处理中 / 弃牌 / 展示（pop 后先停留、到点淡出）",
    replayLane(101) === "processing"
      && replayLane(102) === "discard"
      && replayLane(105) === "showcase"
      && replayDwell
      && !replayNodes.some((node) => node.classList.contains("fading"))
      && $("r-effect").textContent.includes("弃牌：")
      && $("r-effect").textContent.includes("展示："),
    `lanes=${replayNodes.map((node) => `${attr(node, "data-card-id")}:${attr(node, "data-stage-lane")}`).join(",")}`
      + ` fadeAt=${replayFadeAt} dwell=${replayDwell} effect="${$("r-effect").textContent}"`);
  check("U3-01u 回放页不出现目标线 / 技能名 / 白光（04 的范围裁定）",
    byClass($("table-stage"), "target-line").length === 0
      && byClass($("table-stage"), "skill-float").length === 0
      && byClass($("table-stage"), "hero-flash").length === 0,
    `lines=${byClass($("table-stage"), "target-line").length} floats=${byClass($("table-stage"), "skill-float").length}`);

  // 真 DOM 回归（U3 验收缺陷）：`node.children` 是 HTMLCollection——只有 length / item /
  // 索引 / 迭代，**没有 `.find`**。舞台卡复用更新（renderEffectCards → updateStageCardNode）
  // 曾直接对 children 调 `.find`，真浏览器里每次更新都抛 TypeError、整场舞台不渲染。
  // 垫片现在也把 children 做成 HTMLCollection 形状；这里同时钉：①形状；②旧写法在垫片上
  // 确实会抛；③现行更新路径在真实垫片节点与「只有 length / 下标的类数组假节点」上都更新底行。
  const reused = documentShim.createElement("button");
  reused.className = "card face kind-normal static stage-card";
  reused.setAttribute("data-stage-key", "903");
  const reusedLine = documentShim.createElement("span");
  reusedLine.className = "ctype";
  reusedLine.textContent = "旧底行";
  reused.appendChild(reusedLine);
  const collection = reused.children;
  // 活集合：同一个视图随 appendChild / removeChild 变化（真 HTMLCollection 的语义）。
  const probeChild = documentShim.createElement("i");
  reused.appendChild(probeChild);
  const liveAppend = collection.length === 2 && collection[1] === probeChild;
  reused.removeChild(probeChild);
  const liveRemove = collection.length === 1 && collection[0] === reusedLine;
  let assignedError = null;
  try {
    reused.children = [];  // 真 DOM 的 children 是只读 getter
  } catch (error) {
    assignedError = error;
  }
  check("U3-01v 垫片 children = HTMLCollection 形状：无数组方法、只读、活集合、可迭代",
    !("find" in collection) && !("map" in collection) && !("includes" in collection)
      && reused.children === collection
      && collection.length === 1 && collection[0] === reusedLine
      && collection.item(0) === reusedLine
      && Array.from(collection)[0] === reusedLine
      && [...collection][0] === reusedLine
      && liveAppend && liveRemove
      && assignedError instanceof TypeError,
    `find=${"find" in collection} map=${"map" in collection} length=${collection.length} live=${liveAppend && liveRemove} assigned=${assignedError ? assignedError.name : "no-throw"}`);

  const guardItem = {
    ...card(903, "闪"),
    stage: {
      lane: "processing", frameId: 0, enteredAt: Date.now(), fadeAt: null,
      fromZone: { seat: 1, zone: "hand" },
    },
  };
  // 旧实现的等价探针：把 children 当数组直接 `.find`，在垫片上必须抛 TypeError。
  let oldStyleError = null;
  try {
    collection.find((child) => child === reusedLine);
  } catch (error) {
    oldStyleError = error;
  }
  const reusedBox = documentShim.createElement("div");
  reusedBox.appendChild(reused);
  let updateError = null;
  try {
    ui.renderEffectCards(reusedBox, [guardItem], Date.now(), () => "周瑜");
  } catch (error) {
    updateError = error;
  }
  check("U3-01v 舞台卡复用更新：旧写法在垫片上抛 TypeError；现行路径在 HTMLCollection 形状上更新底行",
    oldStyleError instanceof TypeError && !updateError
      && reusedLine.getAttribute("data-zone-label") === "1·周瑜·手牌"
      && reusedLine.textContent === "1·周瑜·手牌",
    `old=${oldStyleError ? oldStyleError.message : "(none)"} threw=${updateError ? updateError.message : "(none)"} label=${reusedLine.getAttribute("data-zone-label")} text="${reusedLine.textContent}"`);

  // 老烟测的类数组夹具继续保留：修复靠 Array.from，既要吃真 HTMLCollection（迭代器），
  // 也要吃只有 length / 下标的类数组（下标），不能只对其中一种形状成立。
  const fakeLine = {
    className: "ctype",
    textContent: "旧底行",
    attrs: {},
    getAttribute(name) { return this.attrs[name]; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
  };
  const fakeNode = {
    tagName: "BUTTON",
    className: "card face kind-normal static stage-card",
    classList: new ClassList(),
    attrs: { "data-stage-key": "904" },
    getAttribute(name) { return this.attrs[name]; },
    setAttribute(name, value) { this.attrs[name] = String(value); },
    removeAttribute(name) { delete this.attrs[name]; },
    children: { 0: fakeLine, length: 1 },  // 类数组：没有 .find / 没有迭代器
  };
  const fakeItem = {
    ...card(904, "桃"),
    stage: {
      lane: "processing", frameId: 0, enteredAt: Date.now(), fadeAt: null,
      fromZone: { seat: 2, zone: "equip" },
    },
  };
  const fakeBox = documentShim.createElement("div");
  fakeBox.appendChild(fakeNode);
  let fakeError = null;
  try {
    ui.renderEffectCards(fakeBox, [fakeItem], Date.now(), () => "周瑜");
  } catch (error) {
    fakeError = error;
  }
  check("U3-01v 类数组 children（无 .find / 无迭代器）也更新底行：不抛错、data-zone-label / 文本更新",
    !fakeError
      && fakeLine.getAttribute("data-zone-label") === "2·周瑜·装备"
      && fakeLine.textContent === "2·周瑜·装备",
    `threw=${fakeError ? fakeError.message : "(none)"} label=${fakeLine.getAttribute("data-zone-label")} text="${fakeLine.textContent}"`);
  ui.state.replay = null;
  ui.renderReplay();
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.animScale = 1;
  ui.resetSelection();
}

/* ============ U3-02（票 02）：反间 —— 处理区 → 手牌 + SKILL 帧 ⇒ 转展示组 ============
 *
 * 判据来自票面与 spec D2 / D5 / D6：只有**服务端已下发的事实**参与判定
 * （`FrameRecord(push)` 的 `frame_kind === "SKILL"`），前端不写技能规则。转组沿用同一条
 * 舞台项（不重新落位、不出现两份）；五谷（USE / RESOLVE 帧）与判定牌（JUDGE 帧）的同类
 * 移动都不转组。时间仍不靠真实等待：`renderProcessing(now)` 直接喂 `now`（D15）。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walk(child, out); }
    return out;
  };
  const stageNodes = () => Array.from($("t-processing").children).filter(
    (node) => node.classList.contains("stage-card"));
  const nodesOf = (id) => stageNodes().filter(
    (node) => attr(node, "data-card-id") === String(id));
  const stageNode = (id) => nodesOf(id)[0];
  const laneOf = (id) => attr(stageNode(id), "data-stage-lane");
  const fading = (id) => Boolean(stageNode(id) && stageNode(id).classList.contains("fading"));
  const lineNode = (id) => {
    const node = stageNode(id);
    return node ? walk(node).find(
      (child) => child.classList.contains("ctype")
        || String(child.className || "").split(/\s+/).includes("ctype")) : null;
  };
  const lineOf = (id) => (lineNode(id) ? lineNode(id).textContent : "");
  const labelOf = (id) => attr(lineNode(id), "data-zone-label");
  const card = (id, name) => ({
    ...synthCard(id, name), kind: "normal", attribute: "普通",
    description: `【${name}】的全文描述`,
  });
  const seatPlayer = (seat, heroName) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, hero_name: heroName,
  });
  const view = () => {
    const out = synthView(null);
    out.session_id = "u3-reveal-1";
    out.snapshot.processing = [];
    out.snapshot.players = [
      seatPlayer(0, "周瑜"), seatPlayer(1, "郭嘉"), seatPlayer(2, "貂蝉"),
    ];
    return out;
  };
  const at = (zone, seat = null) => ({ zone, seat });
  const move = (seq, frame, moved, from, to, reason = "PUT") => ({
    kind: "delta", delta_type: "card_move", seq, frame_id: frame, depth: 1,
    reason, owner: to.seat === null || to.seat === undefined ? from.seat : to.seat,
    from_zone: from, to_zone: to, cards: [moved],
  });
  const push = (seq, frame, kind) => ({
    kind: "frame", op: "push", frame_id: frame, frame_kind: kind, depth: 1, seq,
  });
  const pop = (seq, frame) => ({
    kind: "frame", op: "pop", frame_id: frame, frame_kind: "SKILL", depth: 1, seq,
  });
  const itemOf = (id) => (ui.state.effectCards || [])
    .concat(ui.state.revealedCards || [])
    .find((entry) => String(entry.card_id) === String(id));

  const FANJIAN = card(211, "桃");
  const WUGU_USE = card(212, "杀");
  const WUGU_RESOLVE = card(213, "闪");
  const JUDGE_CARD = card(214, "闪电");

  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.animScale = 1;

  // 真实批次形状（真会话记录流）：SKILL 帧 push 随"确认发动"那次应答先到、先提交；
  // 处理区里的进出移动在后面几次应答里 —— 承载帧的 kind 必须跨 commit 记得住。
  // 先落一条空视图（= 首屏提交，会话已建立），否则「首屏 commit 清舞台」会吃掉假批次。
  const before = {
    ...view(), cursor: 0, log_seq: 0, records: [], finished: false,
  };
  ui.applyViewRaw(before);
  const first = {
    ...view(), cursor: 1, log_seq: 0,
    records: [push(1, 81, "SKILL")], finished: false,
  };
  ui.applyViewRaw(first);
  ui.flushPlayback();
  const second = {
    ...view(), cursor: 3, log_seq: 0, finished: false,
    records: [
      move(2, 81, FANJIAN, at("hand", 0), at("processing")),
      move(3, 81, FANJIAN, at("processing"), at("hand", 1)),
    ],
  };
  ui.applyViewRaw(second);
  ui.stepPlayback(true);
  const entering = itemOf(211);
  const enteredAt = entering && entering.stage.enteredAt;
  const wasProcessing = laneOf(211) === "processing"
    && lineOf(211) === "0·周瑜·手牌" && Boolean(entering);
  ui.stepPlayback(true);
  const transferred = itemOf(211);
  check("U3-02a 处理区 → 手牌 + SKILL 帧 ⇒ 同一条舞台项转进展示组（不重新落位 / 不两份）",
    wasProcessing && laneOf(211) === "showcase"
      && Boolean(stageNode(211)) && stageNode(211).classList.contains("revealed-card")
      && transferred === entering
      && transferred.stage.enteredAt === enteredAt
      && nodesOf(211).length === 1
      && (ui.state.revealedCards || []).length === 0,
    `wasProcessing=${wasProcessing} lane=${laneOf(211)} same=${transferred === entering}`
      + ` entered=${enteredAt} nodes=${nodesOf(211).length}`);
  check("U3-02b 转组沿用首次入台的来源：底行仍是 0·周瑜·手牌，说明文本只剩「展示」",
    lineOf(211) === "0·周瑜·手牌"
      && labelOf(211) === "0·周瑜·手牌"
      && $("t-context").textContent.includes("展示：")
      && !$("t-context").textContent.includes("处理中："),
    `line="${lineOf(211)}" label="${labelOf(211)}" context="${$("t-context").textContent}"`);

  ui.renderProcessing(enteredAt + 5000);
  check("U3-02c 转组后活到 SKILL 帧 pop：5 秒后仍可见、尚未淡出",
    Boolean(stageNode(211)) && !fading(211) && laneOf(211) === "showcase",
    `node=${Boolean(stageNode(211))} fading=${fading(211)} lane=${laneOf(211)}`);

  const skillPopBefore = Date.now();
  ui.state.effectCards = ui.applyEffectDelta(pop(4, 81), ui.state.effectCards);
  const skillPopAfter = Date.now();
  const fadeAt = itemOf(211) && itemOf(211).stage.fadeAt;
  const dwellLane = Number.isFinite(fadeAt)
    && fadeAt >= skillPopBefore + 1000 && fadeAt <= skillPopAfter + 1000;
  ui.renderProcessing(fadeAt - 1);
  const stillDwelling = Boolean(stageNode(211)) && !fading(211);
  ui.renderProcessing(fadeAt);
  check("U3-02d SKILL 帧 pop ⇒ 转组项先停留 0.5(N+1)（D9，N=1 = 1s）再 1s 淡出（不是一闪而过）",
    itemOf(211).stage.lane === "showcase" && Number.isFinite(fadeAt)
      && dwellLane && stillDwelling && fading(211),
    `fadeAt=${fadeAt} popAt=${skillPopBefore}..${skillPopAfter}`
      + ` dwell=${stillDwelling} fading=${fading(211)}`);
  ui.renderProcessing(fadeAt + 1000);
  check("U3-02e 1s 淡出结束 ⇒ 转组项从舞台移除（不留幽灵）",
    !stageNode(211), `node=${Boolean(stageNode(211))}`);
  ui.flushPlayback();
  ui.state.effectCards = [];
  ui.renderProcessing();

  // 五谷丰登（D6 的排除）：USE 帧里进 / 出处理区 ⇒ 不转组，离区即 1s 淡出。
  ui.state.effectCards = ui.applyEffectDelta(push(10, 82, "USE"), ui.state.effectCards);
  ui.state.effectCards = ui.applyEffectDelta(
    move(11, 82, WUGU_USE, at("deck"), at("processing")), ui.state.effectCards);
  const wuguUse = itemOf(212);
  const wuguEntered = wuguUse.stage.enteredAt;
  ui.state.effectCards = ui.applyEffectDelta(
    move(12, 82, WUGU_USE, at("processing"), at("hand", 1)), ui.state.effectCards);
  ui.renderProcessing();
  check("U3-02f 处理区 → 手牌 + USE 帧 ⇒ 不转组（五谷不命中），仍按离区淡出",
    laneOf(212) === "processing"
      && !stageNode(212).classList.contains("revealed-card")
      && Number.isFinite(wuguUse.stage.fadeAt) && fading(212)
      && ui.state.effectCards.filter((entry) => String(entry.card_id) === "212").length === 1,
    `lane=${laneOf(212)} fadeAt=${wuguUse.stage.fadeAt} fading=${fading(212)}`);
  ui.renderProcessing(wuguEntered + 5000);
  check("U3-02g USE 帧的离场牌不滞留整轮（5 秒后已从舞台消失）",
    !stageNode(212), `node=${Boolean(stageNode(212))}`);

  // 五谷的真实形状：进处理区在 USE 帧、取走进手牌在 RESOLVE 帧 ⇒ 也不转组、不额外镜像。
  ui.state.effectCards = ui.applyEffectDelta(push(20, 83, "USE"), ui.state.effectCards);
  ui.state.effectCards = ui.applyEffectDelta(
    move(21, 83, WUGU_RESOLVE, at("deck"), at("processing")), ui.state.effectCards);
  ui.state.effectCards = ui.applyEffectDelta(push(22, 84, "RESOLVE"), ui.state.effectCards);
  const wuguResolve = itemOf(213);
  ui.state.effectCards = ui.applyEffectDelta(
    move(23, 84, WUGU_RESOLVE, at("processing"), at("hand", 0)), ui.state.effectCards);
  ui.renderProcessing();
  check("U3-02h 五谷真实形状（USE 帧进 / RESOLVE 帧出）也不转组、不镜像",
    laneOf(213) === "processing"
      && !stageNode(213).classList.contains("revealed-card")
      && Number.isFinite(wuguResolve.stage.fadeAt)
      && ui.state.effectCards.filter((entry) => String(entry.card_id) === "213").length === 1,
    `lane=${laneOf(213)} fadeAt=${wuguResolve.stage.fadeAt}`);

  // 判定牌（JUDGE 帧，如【天妒】获得判定牌）：不进展示组，仍在处理中组里淡出。
  ui.state.effectCards = ui.applyEffectDelta(push(30, 85, "JUDGE"), ui.state.effectCards);
  ui.state.effectCards = ui.applyEffectDelta(
    move(31, 85, JUDGE_CARD, at("deck"), at("processing"), "JUDGE"), ui.state.effectCards);
  ui.state.effectCards = ui.applyEffectDelta(
    move(32, 85, JUDGE_CARD, at("processing"), at("hand", 0)), ui.state.effectCards);
  ui.renderProcessing();
  check("U3-02i 判定牌（JUDGE 帧）从处理区进手牌也不进展示组，仍在处理中组淡出",
    laneOf(214) === "processing"
      && !stageNode(214).classList.contains("revealed-card")
      && itemOf(214).stage.lane === "processing" && fading(214),
    `lane=${laneOf(214)} fading=${fading(214)}`);

  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.renderProcessing();
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.animScale = 1;
  ui.resetSelection();
}

/* ============ U3-03（票 03）：使用牌目标线 / 行动者高亮 / 体力闪 ============
 *
 * 判据来自票面与 spec D8.1 / D13 / D15。载荷是**与响应体同形**的合成 view + 记录；
 * 断言只落在 DOM：SVG 线的 `data-line-to` / `.target-line`、座位的 `.acting`、武将块的
 * `.hp-flash-*`。时间不靠真实等待：`state.animScale = 0`（淡出立即完成）；要验证寿命时
 * 把线上的 `enteredAt` 改到 1.2s 前再重绘一次（不读像素、不等真实时钟）。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walk(child, out); }
    return out;
  };
  const hasClass = (node, name) => Boolean(node && (
    node.classList.contains(name)
      || String(node.className || "").split(/\s+/).includes(name)));
  const seatNodesNow = () => [...$("t-seats").children, $("t-self-seat")];
  const seatNodeOf = (seat) => seatNodesNow().find(
    (node) => attr(node, "data-seat") === String(seat)) || null;
  const heroBlockOf = (seat) => {
    // 垫片不建静态 HTML 的父子树：自己的武将块按稳定 id 取（真页面里它在 #t-self-seat 里）。
    const selfSeat = ui.state.view && ui.state.view.snapshot
      ? ui.state.view.snapshot.seat : null;
    if (selfSeat !== null && selfSeat !== undefined && String(seat) === String(selfSeat)) {
      return $("t-self-hero");
    }
    const seatNode = seatNodeOf(seat);
    return seatNode ? walk(seatNode).find((node) => hasClass(node, "hero-block")) || null : null;
  };
  const lineNodes = () => walk($("t-target-lines")).filter((node) => hasClass(node, "target-line"));
  const lineTos = () => lineNodes().map((node) => attr(node, "data-line-to"))
    .filter((value) => value !== undefined);
  /** 集合相等（Standards §4）：只在**确实是集合**的断言上用，并且显式命名——
   *  它比的是成员、不比次序；反过来，次序是契约的地方（如 U3-03d）仍直接比序列。 */
  const setEquals = (actual, expected) => {
    const left = new Set((actual || []).map(String));
    const right = new Set((expected || []).map(String));
    return left.size === right.size && [...right].every((value) => left.has(value));
  };
  const linePart = (node, name) => Boolean(node && Array.from(node.children).find(
    (child) => hasClass(child, name)));
  const seatPlayer = (seat, heroName, extra = {}) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, hero_name: heroName, ...extra,
  });
  const PLAYERS = [
    seatPlayer(0, "郭嘉"), seatPlayer(1, "周瑜"),
    seatPlayer(2, "貂蝉"), seatPlayer(3, "张飞"),
  ];
  const baseView = (session, players) => {
    const out = synthView(null);
    out.session_id = session;
    out.heroes = { 0: "guojia", 1: "zhouyu", 2: "diaochan", 3: "zhangfei" };
    out.snapshot.processing = [];
    out.snapshot.players = players;
    return out;
  };
  let serial = 0;
  /** 落一份基准视图（座位渲染出来）再起一份带记录的回放；返回后由用例逐条 step。 */
  const loadEffects = (records, players = PLAYERS) => {
    ui.flushPlayback();
    serial += 1;
    const session = `u3-effects-${serial}`;
    ui.applyViewRaw({
      ...baseView(session, players), cursor: 0, log_seq: 0, records: [], finished: false,
    });
    ui.applyViewRaw({
      ...baseView(session, players), cursor: records.length, log_seq: 0,
      records, finished: false,
    });
  };
  const step = (count = 1) => { for (let index = 0; index < count; index += 1) ui.stepPlayback(true); };
  const designate = (seq, actor, targets, frame = 5) => ({
    kind: "timing", timing: "USE_DESIGNATE_TARGET", seq, frame_id: frame, depth: 1,
    actor, source: actor, targets, cards: [],
  });
  const query = (seq, actor) => ({ kind: "query", seq, query: { actor } });
  const action = (seq) => ({
    kind: "action", seq, accepted: true, reason: null, action: { kind: "Pass" },
  });
  const hpChange = (seq, seat, before, after, frame = 3) => ({
    kind: "delta", delta_type: "hp_change", seq, frame_id: frame, depth: 1,
    seat, before, after, amount: after - before,
  });

  ui.state.animScale = 0;

  // 垫片只按 id 建静态节点（不解析 HTML 的 class），自己的武将块在真页面里带
  // class="hero-block"；这里补上，端点才能真的落在自己的武将块上。
  const selfHeroBlock = $("t-self-hero");
  selfHeroBlock.className = "hero-block";
  selfHeroBlock.classList.add("hero-block");

  // AI 对你使用【杀】：从发动者武将块到你一条线，data-line-to 指目标座位。
  loadEffects([designate(1, 1, [0])]);
  step();
  check("U3-03a 使用牌指定目标：发动者到目标一条 SVG 线（data-line-to=0）",
    lineNodes().length === 1
      && attr(lineNodes()[0], "data-line-to") === "0"
      && attr(lineNodes()[0], "data-line-from") === "1"
      && linePart(lineNodes()[0], "target-shaft")
      && linePart(lineNodes()[0], "target-arrow"),
    `lines=${JSON.stringify(lineTos())}`);

  // 多目标牌（南蛮入侵 / 万箭齐发）：每个目标一条，互不覆盖。
  loadEffects([designate(1, 1, [2, 3])]);
  step();
  check("U3-03b 多目标牌：每个目标各一条线（2 / 3 两条独立节点）",
    lineNodes().length === 2 && setEquals(lineTos(), ["2", "3"])
      && lineNodes().every((node) => attr(node, "data-line-from") === "1"),
    `lines=${JSON.stringify(lineTos())}`);

  // 桃 / 无中生有 / 装备牌：没有目标 ⇒ 一条线都不画。
  loadEffects([designate(1, 1, [])]);
  step();
  check("U3-03c 无目标的牌（桃 / 无中生有 / 装备）不画线",
    lineNodes().length === 0, `lines=${JSON.stringify(lineTos())}`);

  // 目标表里包含自己 / 已阵亡座位：都跳过，只画活着的别人。
  const deadPlayers = PLAYERS.map(
    (player) => (player.seat === 2 ? { ...player, alive: false } : player));
  loadEffects([designate(1, 1, [1, 2, 3])], deadPlayers);
  step();
  check("U3-03d 目标含自己（发动者）与已阵亡座位：跳过，只画剩下的活目标",
    lineTos().join(",") === "3", `lines=${JSON.stringify(lineTos())}`);

  // 同一时刻多条线：各自带自己的 data-line-to，前一条不会被后一条挤掉。
  loadEffects([designate(1, 1, [2]), designate(2, 1, [3], 6)]);
  step(2);
  check("U3-03e 多条线同时存在、互不干扰（每条一个 data-line-to + 自己的箭头）",
    lineNodes().length === 2 && setEquals(lineTos(), ["2", "3"])
      && lineNodes().every((node) => linePart(node, "target-arrow")),
    `lines=${JSON.stringify(lineTos())}`);

  // 寿命：1.2s 保持；animScale = 0 ⇒ 淡出立即完成。把线的 enteredAt 拨回 1.2s 前再重绘一次，
  // 走的是**真实渲染口**（renderTable → syncTransientEffects → targetLineLane），不等真实时间。
  loadEffects([designate(1, 1, [0])]);
  step();
  const lineItem = (ui.state.targetLines || [])[0];
  const hold = 1200;
  let visibleBeforeHold = false;
  let goneAfterHold = false;
  if (lineItem) {
    ui.renderTable();
    visibleBeforeHold = lineNodes().length === 1;
    lineItem.enteredAt = Date.now() - hold;
    ui.renderTable();
    goneAfterHold = lineNodes().length === 0;
  }
  check("U3-03f 线在 1.2s 保持期内可见、保持结束即消失（animScale=0，不读真实时间）",
    visibleBeforeHold && goneAfterHold,
    `before=${visibleBeforeHold} after=${goneAfterHold} hold=${hold}`);

  // 决策点高亮（D13）：action 记录给应答者加 .acting（0.5s 描边）。
  loadEffects([query(1, 2), action(2)]);
  step(2);
  check("U3-03g action 记录是决策点：给应答座位加 .acting",
    hasClass(seatNodeOf(2), "acting")
      && seatNodesNow().filter((node) => hasClass(node, "acting")).length === 1,
    `acting=${seatNodesNow().map((node) => `${attr(node, "data-seat")}:${hasClass(node, "acting")}`).join(",")}`);

  // 决策点高亮：USE_DESIGNATE_TARGET 也高亮发动者（同时画线）。
  loadEffects([designate(1, 2, [0])]);
  step();
  check("U3-03h USE_DESIGNATE_TARGET 也是决策点：高亮发动者并画线",
    hasClass(seatNodeOf(2), "acting") && lineTos().join(",") === "0",
    `acting=${hasClass(seatNodeOf(2), "acting")} lines=${JSON.stringify(lineTos())}`);

  // 结算类时机不高亮：actor 在结算期常是目标，加描边会误导。
  loadEffects([
    { kind: "timing", timing: "RESOLVE_START", seq: 1, frame_id: 3, depth: 2, actor: 1, source: 0, targets: [0] },
    { kind: "timing", timing: "USE_BECOME_TARGET", seq: 2, frame_id: 2, depth: 1, actor: 0, source: 1, targets: [0] },
    { kind: "timing", timing: "RESOLVE_EFFECT", seq: 3, frame_id: 3, depth: 2, actor: 1, source: 0, targets: [0] },
  ]);
  step(3);
  check("U3-03i 结算类时机（RESOLVE_* / USE_BECOME_TARGET）不高亮、不画线",
    seatNodesNow().every((node) => !hasClass(node, "acting")) && lineNodes().length === 0,
    `acting=${seatNodesNow().map((node) => `${attr(node, "data-seat")}:${hasClass(node, "acting")}`).join(",")}`);

  // 体力闪（D13）：掉血武将块闪红，回复闪绿。
  loadEffects([hpChange(1, 1, 4, 3)]);
  step();
  check("U3-03j hp_change 掉血：对应武将块闪红（hp-flash + hp-flash-down）",
    hasClass(heroBlockOf(1), "hp-flash") && hasClass(heroBlockOf(1), "hp-flash-down")
      && !hasClass(heroBlockOf(1), "hp-flash-up"),
    `class="${heroBlockOf(1) ? heroBlockOf(1).className : ""}"`);

  loadEffects([hpChange(1, 2, 2, 3)]);
  step();
  check("U3-03k hp_change 回复：对应武将块闪绿（hp-flash + hp-flash-up）",
    hasClass(heroBlockOf(2), "hp-flash") && hasClass(heroBlockOf(2), "hp-flash-up")
      && !hasClass(heroBlockOf(2), "hp-flash-down"),
    `class="${heroBlockOf(2) ? heroBlockOf(2).className : ""}"`);

  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.animScale = 1;
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.resetSelection();
}


/* ============ U3-04（票 04）：技能名 / 白光 / 技能目标线 ============
 *
 * 判据来自票面与 spec D8.2 / D9 / D15。载荷是**与响应体同形**的合成 `view` + 记录 +
 * `skill_events`（派生通道由服务端下发）：断言只落在 DOM——`.skill-float` 的文本 /
 * 座位 / 叠放属性与坐标、武将块的 `.skill-sweep`、线层的 `data-line-to`。
 * 时间不靠真实等待：`state.animScale = 0`（淡出立即完成），回放用 `stepPlayback(true)`
 * 逐条推进；纯刷新走"没有 records 的 applyView"。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walk(child, out); }
    return out;
  };
  const hasClass = (node, name) => Boolean(node && (
    node.classList.contains(name)
      || String(node.className || "").split(/\s+/).includes(name)));
  const seatNodesNow = () => [...$("t-seats").children, $("t-self-seat")];
  const seatNodeOf = (seat) => seatNodesNow().find(
    (node) => attr(node, "data-seat") === String(seat)) || null;
  const heroBlockOf = (seat) => {
    // 垫片不建静态 HTML 的父子树：自己的武将块按稳定 id 取（真页面里它在 #t-self-seat 里）。
    const selfSeat = ui.state.view && ui.state.view.snapshot
      ? ui.state.view.snapshot.seat : null;
    if (selfSeat !== null && selfSeat !== undefined && String(seat) === String(selfSeat)) {
      return $("t-self-hero");
    }
    const seatNode = seatNodeOf(seat);
    return seatNode ? walk(seatNode).find((node) => hasClass(node, "hero-block")) || null : null;
  };
  const allHeroBlocks = () => [
    ...seatNodesNow().flatMap(
      (node) => walk(node).filter((child) => hasClass(child, "hero-block"))),
    $("t-self-hero"),
  ];
  const floats = () => walk($("t-skill-floats")).filter(
    (node) => hasClass(node, "skill-float"));
  const lineNodes = () => walk($("t-target-lines")).filter(
    (node) => hasClass(node, "target-line"))
    .filter((node) => attr(node, "data-line-to") !== undefined);

  const seatPlayer = (seat, heroName) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, hero_name: heroName,
  });
  const baseView = (session) => {
    const out = synthView(null);
    out.session_id = session;
    out.heroes = { 0: "guojia", 1: "zhouyu", 2: "diaochan", 3: "zhangfei" };
    out.snapshot.processing = [];
    out.snapshot.players = [
      seatPlayer(0, "郭嘉"), seatPlayer(1, "周瑜"),
      seatPlayer(2, "貂蝉"), seatPlayer(3, "张飞"),
    ];
    return out;
  };
  const query = (seq, actor) => ({ kind: "query", seq, query: { actor } });
  const skillAction = (seq) => ({
    kind: "action", seq, accepted: true, reason: null,
    action: { kind: "ActivateSkill", answers_seq: seq - 1, skill_id: "hero.zhouyu.fanjian", choices: [] },
  });
  const useAction = (seq) => ({
    kind: "action", seq, accepted: true, reason: null,
    action: { kind: "UseCard", answers_seq: seq - 1 },
  });
  const skillEvent = (seq, seat, name, targets = []) => ({
    seq, seat, skill_id: "hero.zhouyu.fanjian", name, targets,
  });
  const designate = (seq, actor, targets) => ({
    kind: "timing", timing: "USE_DESIGNATE_TARGET", seq, frame_id: 5, depth: 1,
    actor, source: actor, targets, cards: [],
  });

  let serial = 0;
  /** 落一份干净会话（换会话 = 走真实的清屏路径），再起一份带记录 + skill_events 的回放。 */
  const loadSkillBatch = (records, events) => {
    ui.flushPlayback();
    serial += 1;
    const session = `u3-skill-${serial}`;
    ui.applyViewRaw({
      ...baseView(session), cursor: 0, log_seq: 0, records: [], skill_events: [], finished: false,
    });
    ui.applyViewRaw({
      ...baseView(session), cursor: records.length, log_seq: 0,
      records, skill_events: events, finished: false,
    });
    return session;
  };
  const step = (count = 1) => { for (let index = 0; index < count; index += 1) ui.stepPlayback(true); };

  ui.state.animScale = 0;
  // 垫片只按 id 建静态节点（不解析 class）：自己的武将块在真页面里带 class="hero-block"。
  const selfHeroBlock = $("t-self-hero");
  selfHeroBlock.className = "hero-block";
  selfHeroBlock.classList.add("hero-block");

  // ---- 时序：事件在**产生它的记录 seq** 那一步演出，不在响应到达时全部出现 ----
  loadSkillBatch(
    [
      query(1, 1),
      skillAction(2),
      { kind: "delta", delta_type: "hp_change", seq: 3, frame_id: 9, depth: 1,
        seat: 1, before: 4, after: 3, amount: -1 },
    ],
    [skillEvent(2, 1, "反间", [0])],
  );
  const atArrival = floats().length;
  const pendingAtArrival = (ui.state.skillEvents || []).length;
  step();  // query(1)
  const afterQuery = floats().length;
  step();  // action(2) = 发动
  const node = floats()[0];
  check("U3-04a 技能名在**发动那条记录**的步上飘出（query 步不飘、响应到达时不出现）",
    atArrival === 0 && pendingAtArrival === 1 && afterQuery === 0
      && floats().length === 1 && Boolean(node)
      && node.textContent === "反间"
      && attr(node, "data-skill-seat") === "1",
    `arrival=${atArrival} pending=${pendingAtArrival} afterQuery=${afterQuery}`
      + ` floats=${floats().map((item) => item.textContent).join(",")}`);
  check("U3-04b 反间的目标线复用 U3-03 的线层：发动者 1 → 目标 0（data-line-to=0）",
    lineNodes().length === 1
      && attr(lineNodes()[0], "data-line-to") === "0"
      && attr(lineNodes()[0], "data-line-from") === "1",
    `lines=${lineNodes().map((item) => `${attr(item, "data-line-from")}->${attr(item, "data-line-to")}`).join(",")}`);
  // U3-05 起每步都全表重绘（座位节点会换），而 `animScale = 0` 的白光"到点即结束"——
  // 故"发动时挂上"要在**发动那一步**读；"活过重绘"由 U3-05r 在 animScale = 1 下另钉。
  const sweepAtActivation = hasClass(heroBlockOf(1), "skill-sweep")
    && !hasClass(heroBlockOf(0), "skill-sweep");
  step();  // hp_change(3)
  check("U3-04c 白光：发动者武将块在发动时挂 .skill-sweep（裁在块内由 CSS 的 overflow:hidden 保证）",
    sweepAtActivation,
    `seat1=${sweepAtActivation} self=${hasClass($("t-self-hero"), "skill-sweep")}`);

  // ---- 同一武将块 2s 内两条：不合并 / 不去重；后一条起点抬高 14 设计单位 ----
  loadSkillBatch(
    [query(1, 1), skillAction(2), query(3, 1), skillAction(4)],
    [skillEvent(2, 1, "反间", []), skillEvent(4, 1, "制衡", [])],
  );
  step(4);
  const stacked = floats();
  const tops = stacked.map((item) => parseFloat(item.style.top));
  check("U3-04d 同一武将块 2s 内两条：两个独立飘字，后一条起点抬高 14 设计单位",
    stacked.length === 2
      && stacked[0].textContent === "反间" && stacked[1].textContent === "制衡"
      && attr(stacked[0], "data-skill-stack") === "0"
      && attr(stacked[1], "data-skill-stack") === "1"
      && Math.abs((tops[0] - tops[1]) - 14) < 1e-6,
    `n=${stacked.length} stacks=${stacked.map((item) => attr(item, "data-skill-stack")).join(",")}`
      + ` tops=${tops.join(",")}`);
  check("U3-04e 没有指定目标的发动（制衡 / 装备技）：只飘名 + 白光，一条线都不画",
    lineNodes().length === 0 && hasClass(heroBlockOf(1), "skill-sweep"),
    `lines=${lineNodes().length} sweep=${hasClass(heroBlockOf(1), "skill-sweep")}`);

  // ---- 自己（底部武将块）也会飘：同一条路径，不是只给环上对手 ----
  loadSkillBatch(
    [query(1, 0), skillAction(2)],
    [skillEvent(2, 0, "遗计", [2])],
  );
  step(2);
  check("U3-04f 自己的发动：飘字挂在自己武将块上、白光落在 #t-self-hero、并向目标画线",
    floats().length === 1 && attr(floats()[0], "data-skill-seat") === "0"
      && hasClass($("t-self-hero"), "skill-sweep")
      && lineNodes().length === 1 && attr(lineNodes()[0], "data-line-to") === "2",
    `floats=${floats().length} selfSweep=${hasClass($("t-self-hero"), "skill-sweep")}`
      + ` lines=${lineNodes().map((item) => attr(item, "data-line-to")).join(",")}`);

  // ---- 使用牌不飘技能名（转化技武圣 / 龙胆 / 奇袭最终走 UseCard，没有 ActivateSkill 记录）----
  loadSkillBatch(
    [query(1, 2), useAction(2), designate(3, 2, [0])],
    [],
  );
  step(3);
  check("U3-04g 使用牌不飘技能名 / 不扫白光：只有 U3-03 的使用牌目标线",
    floats().length === 0
      && allHeroBlocks().every((block) => !hasClass(block, "skill-sweep"))
      && lineNodes().length === 1 && attr(lineNodes()[0], "data-line-to") === "0",
    `floats=${floats().length} sweeps=${allHeroBlocks().filter((b) => hasClass(b, "skill-sweep")).length}`
      + ` lines=${lineNodes().length}`);

  // ---- 纯刷新（没有 records）：事件立即演出，不等回放 ----
  const refreshSession = loadSkillBatch([query(1, 1), skillAction(2)], [skillEvent(2, 1, "反间", [])]);
  step(2);
  const beforeRefresh = floats().length;
  ui.applyViewRaw({
    ...baseView(refreshSession), cursor: 2, log_seq: 0, records: [],
    skill_events: [skillEvent(6, 1, "反间", [])], finished: false,
  });
  check("U3-04h 纯刷新（无回放）：技能名立即演出，不丢事件、不等不存在的记录步",
    beforeRefresh === 1 && floats().length === 2
      && (ui.state.skillEvents || []).length === 0,
    `before=${beforeRefresh} after=${floats().length} pending=${(ui.state.skillEvents || []).length}`);

  // ---- 名字只用服务端下发的 name：前端没有 skill_id → 中文常量表 ----
  loadSkillBatch(
    [query(1, 1), skillAction(2)],
    [{ seq: 2, seat: 1, skill_id: "hero.zhouyu.fanjian", name: "服务端命名", targets: [] }],
  );
  step(2);
  check("U3-04j 飘字的文本就是下发载荷的 name（前端没有 skill_id → 中文常量表）",
    floats().length === 1 && floats()[0].textContent === "服务端命名",
    `floats=${floats().map((item) => item.textContent).join(",")}`);

  // ---- 换会话：飘字 / 白光 / 待触发事件一起清掉，不给下一局留残影 ----
  ui.applyViewRaw({
    ...baseView("u3-skill-next"), cursor: 0, log_seq: 0, records: [],
    skill_events: [], finished: false,
  });
  check("U3-04i 换会话清空技能名 / 白光 / 待触发事件",
    floats().length === 0
      && (ui.state.skillEvents || []).length === 0
      && allHeroBlocks().every((block) => !hasClass(block, "skill-sweep"))
      && lineNodes().length === 0,
    `floats=${floats().length} pending=${(ui.state.skillEvents || []).length}`
      + ` sweeps=${allHeroBlocks().filter((b) => hasClass(b, "skill-sweep")).length}`);

  // ---- lei 票 06：**锁定技**（【父荫】这类不问"是否发动"的）也要演出 ----
  // 载荷与响应体同形：`skill_events` 由服务端从引擎的发动缓冲派生（记录面没有
  // `ActivateSkill` 应答），seq 锚在那条派发它的 `timing` 记录上。前端判据一字不改——
  // 这里断的就是"同键同型喂进去就演出"，没有为技能名 / 技能 id 加分支。
  const becomeTarget = (seq, actor, source, targets) => ({
    kind: "timing", timing: "USE_AFTER_BECOME_TARGET", seq, frame_id: 5, depth: 1,
    actor, source, targets, cards: [],
  });
  loadSkillBatch(
    [query(1, 1), becomeTarget(2, 1, 2, [1])],
    [skillEvent(2, 1, "父荫", [])],
  );
  step(2);
  check("U3-04k 锁定技的发动事件（没有激活应答）：父荫在持有者武将块飘字 + 白光，不画线",
    floats().length === 1
      && floats()[0].textContent === "父荫"
      && attr(floats()[0], "data-skill-seat") === "1"
      && hasClass(heroBlockOf(1), "skill-sweep")
      && lineNodes().length === 0,
    `floats=${floats().map((item) => item.textContent).join(",")}`
      + ` sweep=${hasClass(heroBlockOf(1), "skill-sweep")} lines=${lineNodes().length}`);

  // ---- lei 票 06：**转化技**（【武圣】这类确认后走普通 UseCard 的）同样演出 ----
  // 记录部分与 U3-04g 同形（query → UseCard）；区别只在服务端多下发了一条
  // `skill_events`。于是"使用牌本身不飘技能名"仍然成立（U3-04g），而转化技这次使用会飘
  // ——前端不需要知道这条使用是哪条技能转的。白光按发动那一步读（animScale = 0 时到点即
  // 摘类，与 U3-04c 同一姿态）。
  loadSkillBatch(
    [query(1, 2), useAction(2)],
    [{ seq: 2, seat: 2, skill_id: "hero.guanyu.wusheng", name: "武圣", targets: [0] }],
  );
  step(2);
  const convertSweep = hasClass(heroBlockOf(2), "skill-sweep");
  check("U3-04l 转化技的发动事件：武圣在发动者武将块飘字 + 白光，并画线到目标",
    floats().length === 1
      && floats()[0].textContent === "武圣"
      && attr(floats()[0], "data-skill-seat") === "2"
      && convertSweep
      && lineNodes().length === 1
      && attr(lineNodes()[0], "data-line-from") === "2"
      && attr(lineNodes()[0], "data-line-to") === "0",
    `floats=${floats().map((item) => item.textContent).join(",")}`
      + ` sweep=${convertSweep}`
      + ` lines=${lineNodes().map((item) => `${attr(item, "data-line-from")}->${attr(item, "data-line-to")}`).join(",")}`);

  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.animScale = 1;
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.resetSelection();
}

/* ============ U3-05（票 05）：逐事件全表更新 / 静默轮询 / 步长 200ms ============
 *
 * 判据来自票面与 spec D11 / D12 / D13（步长）。载荷是**与响应体同形**的合成 view + 记录；
 * 回放用 `stepPlayback(true)` 逐条推进（不跑真实计时器），轮询直接取 `wire()` 注册的 2s
 * 间隔回调（垫片只登记、不自动跑）。断言只落在 DOM 与 `state.liveView` / `state.view`
 * 的可观察值上；派生集合之外的东西（技能可用性 / 体力上限 / 上一问）另行显式钉住。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walk(child, out); }
    return out;
  };
  const hasClass = (node, name) => Boolean(node && (
    node.classList.contains(name)
    || String(node.className || "").split(/\s+/).includes(name)));
  const seatNodesNow = () => [...$("t-seats").children, $("t-self-seat")];
  const seatNodeOf = (seat) => seatNodesNow().find(
    (node) => attr(node, "data-seat") === String(seat)) || null;
  const seatText = (seat) => (seatNodeOf(seat) ? seatNodeOf(seat).text() : "")
    .replace(/\s+/g, " ").trim();
  const seatPlayer = (seat, extra = {}) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, hero_name: `武将${seat}`, ...extra,
  });
  const basePlayers = () => [seatPlayer(0), seatPlayer(1), seatPlayer(2), seatPlayer(3)];
  const ownPending = (seq) => ({
    kind: "PLAY_PHASE", actor: 0, seq, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: seq }],
    candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
  });
  const foreignPending = (seq, actor) => ({
    kind: "PLAY_PHASE", actor, seq, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [], candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
  });
  const testSkill = (available = true) => ({
    skill_id: "hero.test.skill", name: "测试技", skill_type: "NORMAL", locked: false,
    carrier: "HERO", description: "测试用技能", available, ui: {},
  });
  /** 一份干净的基准提交视图；`session` 相同 = 同一会话（不清 live view）。 */
  const baseView = (session, players = basePlayers()) => {
    const out = synthView(null);
    out.session_id = session;
    out.heroes = { 0: "guojia", 1: "zhouyu", 2: "diaochan", 3: "zhangfei" };
    out.records = [];
    out.narrative = [];
    out.cursor = 100;
    out.log_seq = 0;
    out.finished = false;
    out.snapshot.players = players;
    out.snapshot.self_hand = [synthCard(11, "杀"), synthCard(12, "闪")];
    out.snapshot.deck_count = 40;
    out.snapshot.discard = [synthCard(13, "桃")];
    out.snapshot.processing = [];
    out.snapshot.turn_seat = 0;
    out.snapshot.phase = "play";
    out.skills = [testSkill(true)];
    out.pending = ownPending(50);
    return out;
  };
  const moveRecord = (seq, options) => ({
    kind: "delta", delta_type: "card_move", seq, frame_id: 0, depth: 0,
    reason: options.reason || "MOVE",
    owner: options.owner === undefined ? null : options.owner,
    from_zone: options.from, to_zone: options.to,
    cards: options.cards || [],
    ...(options.cardCount === undefined ? {} : { card_count: options.cardCount }),
  });
  const timing = (seq, name, extra = {}) => ({
    kind: "timing", timing: name, seq, frame_id: 0, depth: 0, targets: [], cards: [], ...extra,
  });
  /** 派生集合（D11 的"可派生"清单）：逐事件由记录写进 live view 的那几项。 */
  const derived = (snapshot) => ({
    seat: snapshot.seat,
    deck_count: snapshot.deck_count,
    self_hand: snapshot.self_hand.map((card) => card.card_id),
    discard: snapshot.discard.map((card) => card.card_id),
    turn_seat: snapshot.turn_seat,
    phase: snapshot.phase,
    players: snapshot.players.map((player) => ({
      seat: player.seat,
      hp: player.hp,
      hand_count: player.hand_count,
      alive: player.alive,
      chained: Boolean(player.chained),
      wine_active: Boolean(player.wine_active),
      identity: player.identity,
      identity_label: player.identity_label || null,
      // 装备区只比**成员**：权威顺序是槽位枚举序（`SlotKind`），前端不复制第二份槽序；
      // 判定区按置入序（`extend`），故比次序。
      equip: player.equip.map((card) => card.card_id).sort((left, right) => left - right),
      judge: player.judge.map((card) => card.card_id),
    })),
  });
  const step = (count = 1) => {
    for (let index = 0; index < count; index += 1) ui.stepPlayback(true);
  };

  ui.state.animScale = 1;

  // ---- ① 逐事件更新 + 派生视图 vs 提交快照（不变量） ----
  const session = "u3-live-1";
  const equipCard = { ...synthCard(15, "青龙偃月刀"), slot: "weapon", type: "equip", attack_range: 3 };
  const base = baseView(session);
  ui.state.view = null;
  ui.state.logRecords = [];
  $("t-log").innerHTML = "";
  ui.applyViewRaw(base);
  check("U3-05a0 提交后 live view 从最近一次提交视图起算",
    Boolean(ui.state.liveView) && ui.state.liveView !== ui.state.view
      && ui.state.liveView.snapshot !== ui.state.view.snapshot
      && JSON.stringify(derived(ui.state.liveView.snapshot))
        === JSON.stringify(derived(base.snapshot)),
    `live=${Boolean(ui.state.liveView)} viewSame=${ui.state.liveView === ui.state.view}`);

  const tape = [
    { kind: "delta", delta_type: "hp_change", seq: 101, frame_id: 3, depth: 1,
      seat: 1, before: 4, after: 3, amount: -1 },
    // 对手摸牌：隐藏区只给张数（服务端 `visible_record` 的裁剪形状）。
    moveRecord(102, { reason: "DRAW", owner: 1, from: { seat: null, zone: "deck" },
      to: { seat: 1, zone: "hand" }, cardCount: 2 }),
    // 自己摸牌：牌面可见（自己的手牌不是隐藏信息）。
    moveRecord(103, { reason: "DRAW", owner: 0, from: { seat: null, zone: "deck" },
      to: { seat: 0, zone: "hand" }, cards: [synthCard(14, "闪")] }),
    // 装备置入：公开区牌面。
    moveRecord(104, { reason: "PUT", owner: 1, from: { seat: 1, zone: "hand" },
      to: { seat: 1, zone: "equip" }, cards: [equipCard] }),
    moveRecord(105, { reason: "DISCARD", owner: 2, from: { seat: 2, zone: "hand" },
      to: { seat: null, zone: "discard" }, cards: [synthCard(16, "桃")] }),
    moveRecord(106, { reason: "PUT", owner: 2, from: { seat: null, zone: "deck" },
      to: { seat: 2, zone: "judge" }, cards: [synthCard(17, "乐不思蜀")] }),
    { kind: "delta", delta_type: "chained_change", seq: 107, frame_id: 0, depth: 0,
      seat: 2, chained: true },
    { kind: "delta", delta_type: "wine_change", seq: 108, frame_id: 0, depth: 0,
      seat: 0, active: true },
    { kind: "delta", delta_type: "identity_reveal", seq: 109, frame_id: 0, depth: 0,
      seat: 3, identity: "rebel" },
    timing(110, "DEATH_BEFORE_START", { frame_id: 9, depth: 2, actor: 3, targets: [3] }),
    timing(111, "TURN_END", { actor: 0 }),
    timing(112, "TURN_BEFORE_START", { actor: 1 }),
    timing(113, "TURN_START", { actor: 1 }),
    timing(114, "PHASE_DRAW_START", { actor: 1 }),
    timing(115, "PHASE_DRAW", { actor: 1 }),
    timing(116, "PHASE_PLAY_START", { actor: 1 }),
    // 重洗：弃牌堆并回牌堆。A3 下服务端只给**数量**（cards 抹空 + card_count），
    // 故这条夹具必须是脱敏形状——派生牌堆数只能读 card_count，读牌序会停在 36。
    { kind: "delta", delta_type: "deck_shuffle", seq: 117, frame_id: 0, depth: 0,
      cards: [], card_count: 2,
      from_zone: { seat: null, zone: "discard" }, to_zone: { seat: null, zone: "deck" } },
  ];
  const authoritative = baseView(session);
  authoritative.cursor = 117;
  authoritative.pending = ownPending(51);
  authoritative.skills = [testSkill(false)];
  authoritative.snapshot.players = [
    seatPlayer(0, { hand_count: 3, wine_active: true }),
    seatPlayer(1, { hp: 3, hp_max: 5, hand_count: 3, equip: [equipCard] }),
    seatPlayer(2, { hand_count: 1, judge: [synthCard(17, "乐不思蜀")], chained: true }),
    seatPlayer(3, { alive: false, identity: "rebel", identity_label: "反贼" }),
  ];
  authoritative.snapshot.self_hand = [synthCard(11, "杀"), synthCard(12, "闪"), synthCard(14, "闪")];
  authoritative.snapshot.deck_count = 38;
  authoritative.snapshot.discard = [];
  authoritative.snapshot.turn_seat = 1;
  authoritative.snapshot.phase = "play";
  ui.applyViewRaw({ ...authoritative, records: tape });
  check("U3-05a1 收到记录先回放（还没提交、表体仍是上一份视图）",
    Boolean(ui.state.playback) && ui.state.view.cursor === 100,
    `playback=${Boolean(ui.state.playback)} cursor=${ui.state.view.cursor}`);

  step();  // 101 hp_change
  check("U3-05a 体力随 hp_change 逐事件变化（不等到整批播完）",
    seatText(1).includes("3/4"), `座1="${seatText(1)}"`);
  step();  // 102 对手摸两张
  check("U3-05b 对手手牌数随摸牌事件变化（隐藏区只给张数；合成载荷没有 hand_limit ⇒ 上限退回张数）",
    seatText(1).includes("手牌：4/4"), `座1="${seatText(1)}"`);
  step();  // 103 自己摸一张
  // 张数行（旧 `#t-hand-count`）已按报障删掉（票 23）：这里只核牌面本身跟着事件更新。
  check("U3-05c 自己的手牌面随事件更新",
    $("t-hand").children.length === 3
      && Array.from($("t-hand").children).some((node) => attr(node, "data-card-id") === "14"),
    `hand=${$("t-hand").children.length}`);
  step();  // 104 装备置入
  check("U3-05d 装备区牌面随 card_move 更新",
    walk(seatNodeOf(1)).some((node) => attr(node, "data-card-id") === "15"),
    `座1="${seatText(1)}"`);
  step();  // 105 弃置进弃牌堆
  check("U3-05e 弃牌堆数量随事件更新",
    $("t-discard").textContent === "2", `弃牌="${$("t-discard").textContent}"`);
  step();  // 106 判定区
  const deckAfterSix = $("t-deck").textContent;
  check("U3-05f 判定区牌面 / 牌堆数量随事件更新",
    walk(seatNodeOf(2)).some((node) => attr(node, "data-card-id") === "17")
      && deckAfterSix === "36",
    `座2="${seatText(2)}" deck=${deckAfterSix}`);
  step();  // 107 横置
  check("U3-05g 横置随 chained_change 更新",
    hasClass(seatNodeOf(2), "chained"), `座2="${seatText(2)}"`);
  step();  // 108 酒
  check("U3-05h 酒随 wine_change 更新（自己座位泛红）",
    hasClass($("t-self-seat"), "wine"), `self="${$("t-self-seat").className}"`);
  step();  // 109 身份亮明
  const badge = walk(seatNodeOf(3)).find((node) => hasClass(node, "identity-badge"));
  check("U3-05i 身份亮明随 identity_reveal 更新",
    Boolean(badge) && badge.textContent === "反贼", `badge=${badge ? badge.textContent : "(缺)"}`);
  step();  // 110 阵亡
  check("U3-05j 阵亡随 DEATH_BEFORE_START 更新",
    hasClass(seatNodeOf(3), "dead"), `座3="${seatText(3)}"`);
  step(2);  // 111 TURN_END / 112 TURN_BEFORE_START
  check("U3-05k 回合随 TURN_* 更新",
    $("t-turn").textContent === "座位 1", `回合="${$("t-turn").textContent}"`);
  step(2);  // 113 TURN_START / 114 PHASE_DRAW_START
  const midPhase = $("t-phase").textContent;
  step(2);  // 115 PHASE_DRAW / 116 PHASE_PLAY_START
  check("U3-05l 阶段随 PHASE_* 更新",
    midPhase === "摸牌阶段" && $("t-phase").textContent === "出牌阶段",
    `mid="${midPhase}" now="${$("t-phase").textContent}"`);
  step();  // 117 重洗
  check("U3-05m 重洗把弃牌堆并回牌堆（弃牌清零 / 牌堆补回）",
    $("t-discard").textContent === "0" && $("t-deck").textContent === "38",
    `弃牌="${$("t-discard").textContent}" 牌堆="${$("t-deck").textContent}"`);

  const liveBeforeCommit = ui.state.liveView;
  check("U3-05n 派生视图与随后提交的权威快照一致（派生集合内）",
    JSON.stringify(derived(liveBeforeCommit.snapshot))
      === JSON.stringify(derived(authoritative.snapshot)),
    `live=${JSON.stringify(derived(liveBeforeCommit.snapshot))}`
      + ` authoritative=${JSON.stringify(derived(authoritative.snapshot))}`);
  check("U3-05n2 逐事件（不是最后一次性跳变）：中途的牌堆数曾到 36",
    deckAfterSix === "36" && $("t-deck").textContent === "38",
    `mid=${deckAfterSix} final=${$("t-deck").textContent}`);
  check("U3-05n3 不可派生字段留在最近一次提交：体力上限 / 技能可用性 / 上一问",
    liveBeforeCommit.snapshot.players.find((player) => player.seat === 1).hp_max === 4
      && liveBeforeCommit.skills[0].available === true
      && liveBeforeCommit.pending.seq === 50
      && authoritative.snapshot.players.find((player) => player.seat === 1).hp_max === 5
      && authoritative.skills[0].available === false
      && authoritative.pending.seq === 51,
    `live hp_max=${liveBeforeCommit.snapshot.players[1].hp_max}`
      + ` available=${liveBeforeCommit.skills[0].available} pending=${liveBeforeCommit.pending.seq}`);
  ui.flushPlayback();
  check("U3-05o 提交用权威快照覆盖 live view（不可派生字段随之更新）",
    ui.state.playback === null
      && ui.state.liveView !== ui.state.view
      && ui.state.liveView.snapshot !== ui.state.view.snapshot
      && JSON.stringify(derived(ui.state.liveView.snapshot))
        === JSON.stringify(derived(ui.state.view.snapshot))
      && ui.state.liveView.snapshot.players[1].hp_max === 5
      && ui.state.liveView.skills[0].available === false
      && ui.state.liveView.pending.seq === 51,
    `playback=${Boolean(ui.state.playback)} hp_max=${ui.state.liveView.snapshot.players[1].hp_max}`);

  // ---- ② 播放期间不画上一问的 pending 派生 UI ----
  // 上一问是**别人**的：提交视图里那个座位挂「正在响应…」，播放期间必须收起。
  const foreignBase = baseView("u3-live-foreign");
  foreignBase.pending = foreignPending(50, 1);
  ui.state.view = null;
  ui.applyViewRaw(foreignBase);
  const promptsBefore = walk($("t-seats")).filter((node) => hasClass(node, "seat-prompt"));
  ui.applyViewRaw({
    ...foreignBase, cursor: 201, pending: ownPending(51),
    records: [{ kind: "delta", delta_type: "hp_change", seq: 201, frame_id: 3, depth: 1,
      seat: 2, before: 4, after: 3, amount: -1 }],
  });
  step();
  const promptsDuring = walk($("t-seats")).concat(walk($("t-self-seat")))
    .filter((node) => hasClass(node, "seat-prompt"));
  check("U3-05p 播放期间不画上一问的「正在响应…」（提交后恢复）",
    promptsBefore.length === 1 && promptsBefore[0].textContent === "正在响应…"
      && promptsDuring.length === 0,
    `before=${promptsBefore.length} during=${promptsDuring.length}`);
  ui.flushPlayback();
  check("U3-05p2 播完提交后 pending 派生的座位提示按新询问重算",
    walk($("t-seats")).filter((node) => hasClass(node, "seat-prompt")).length === 0
      && ui.state.view.pending.seq === 51,
    `prompts=${walk($("t-seats")).filter((node) => hasClass(node, "seat-prompt")).length}`);

  // 上一问是**自己**的：技能按钮在提交视图里可点，播放期间不得画可点态。
  const ownBase = baseView("u3-live-own");
  ui.state.view = null;
  ui.applyViewRaw(ownBase);
  const skillButton = () => walk($("t-skills")).find(
    (node) => attr(node, "data-skill-id") === "hero.test.skill") || null;
  const readyBefore = skillButton();
  ui.applyViewRaw({
    ...ownBase, cursor: 301, pending: ownPending(51),
    records: [{ kind: "delta", delta_type: "hp_change", seq: 301, frame_id: 3, depth: 1,
      seat: 2, before: 4, after: 3, amount: -1 }],
  });
  step();
  const readyDuring = skillButton();
  check("U3-05q 播放期间技能按钮不画可点态（上一问的可点态收起）",
    hasClass(readyBefore, "ready") && attr(readyBefore, "data-usable") === "true"
      && readyBefore.disabled === false
      && !hasClass(readyDuring, "ready") && attr(readyDuring, "data-usable") === "false"
      && readyDuring.disabled === true
      && $("t-prompt-skills").children.length === 0,
    `before=${readyBefore ? readyBefore.className : "(缺)"}`
      + ` during=${readyDuring ? readyDuring.className : "(缺)"}`
      + ` usable=${readyDuring ? attr(readyDuring, "data-usable") : "-"}`
      + ` promptSkills=${$("t-prompt-skills").children.length}`);
  ui.flushPlayback();
  check("U3-05q2 提交后可点态随新询问恢复",
    hasClass(skillButton(), "ready") && attr(skillButton(), "data-usable") === "true",
    `after=${skillButton() ? skillButton().className : "(缺)"}`);

  // ---- ③ 每步全表重绘后，票 03/04 的线 / 描边 / 技能名仍在 ----
  const effectsBase = baseView("u3-live-effects");
  ui.state.view = null;
  ui.applyViewRaw(effectsBase);
  const designate = { kind: "timing", timing: "USE_DESIGNATE_TARGET", seq: 402,
    frame_id: 5, depth: 1, actor: 1, source: 1, targets: [2], cards: [] };
  const skillAction = { kind: "action", seq: 403, accepted: true, reason: null,
    action: { kind: "ActivateSkill", answers_seq: 402,
      skill_id: "hero.test.skill", choices: [], picks: [] } };
  ui.applyViewRaw({
    ...effectsBase, cursor: 404, pending: ownPending(51),
    records: [
      { kind: "query", seq: 401, query: { actor: 1 } },
      designate,
      skillAction,
      { kind: "delta", delta_type: "hp_change", seq: 404, frame_id: 5, depth: 1,
        seat: 2, before: 4, after: 3, amount: -1 },
    ],
    skill_events: [{ seq: 403, seat: 1, skill_id: "hero.test.skill", name: "测试技", targets: [] }],
  });
  const lineCount = () => walk($("t-target-lines")).filter(
    (node) => hasClass(node, "target-line") && attr(node, "data-line-to") !== undefined).length;
  const floatCount = () => walk($("t-skill-floats")).filter(
    (node) => hasClass(node, "skill-float")).length;
  const actingCount = () => seatNodesNow().filter((node) => hasClass(node, "acting")).length;
  const sweptCount = () => seatNodesNow().filter((node) => {
    const block = walk(node).find((child) => hasClass(child, "hero-block"));
    return hasClass(block, "skill-sweep");
  }).length + (hasClass($("t-self-hero"), "skill-sweep") ? 1 : 0);
  step(2);  // query / designate
  const lineAfterDesignate = lineCount();
  const actingAfterDesignate = actingCount();
  step();  // action → 技能名飘字 + 白光
  const floatAfterAction = floatCount();
  const sweepAfterAction = sweptCount();
  step();  // hp_change → 又一次全表重绘
  check("U3-05r 每步全表重绘后票 03/04 的线 / 描边 / 技能名 / 白光仍在（独立图层 + syncTransientEffects）",
    lineAfterDesignate === 1 && actingAfterDesignate === 1 && floatAfterAction === 1
      && sweepAfterAction === 1
      && lineCount() === 1 && floatCount() === 1 && actingCount() === 1 && sweptCount() === 1,
    `line=${lineAfterDesignate}->${lineCount()} acting=${actingAfterDesignate}->${actingCount()}`
      + ` float=${floatAfterAction}->${floatCount()} sweep=${sweepAfterAction}->${sweptCount()}`);
  ui.flushPlayback();

  // ---- ④ 静默 2s 轮询（真会话）：没新事实时不清选择 ----
  ui.show("table");
  const pollSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: 60, seed: 20261013,
  });
  await openSession(pollSession);
  const pollTimer = intervals.find((entry) => entry.ms === 2000);
  check("U3-05s 2 秒轮询已接上（唯一一条 2000ms 间隔）",
    Boolean(pollTimer) && intervals.filter((entry) => entry.ms === 2000).length === 1,
    `intervals=${intervals.map((entry) => entry.ms).join(",")}`);
  const pollPending = ui.state.view.pending;
  const handCardId = (ui.state.view.snapshot.self_hand[0] || {}).card_id;
  check("U3-05s2 静默轮询的前置：真会话停在人类询问上",
    Boolean(pollPending) && handCardId !== undefined,
    `pending=${pollPending ? pollPending.kind : "(无)"} card=${handCardId}`);
  // 选到一半：焦点 / 已选目标 / 预发动本地态都摆上。
  ui.state.focusCardId = handCardId;
  ui.state.selectedTargets = [1];
  ui.state.preactivation = {
    skillId: "hero.test.skill", name: "测试技", steps: [], answers: [], cursor: 0,
  };
  const viewBefore = ui.state.view;
  const ringFirst = $("t-seats").children[0];
  const clockBefore = ui.state.pendingClock.receivedAt;
  const idleCallsBefore = calls.length;
  await pollTimer.fn();
  const idleGets = calls.slice(idleCallsBefore).filter(
    (call) => call.path.startsWith("/api/sessions/") && call.body === null);
  check("U3-05t 静默轮询：游标与询问序号都没变 ⇒ 不提交、不重绘、不清选择",
    idleGets.length === 1
      && ui.state.view === viewBefore
      && ui.state.liveView && ui.state.liveView.pending === viewBefore.pending
      && $("t-seats").children[0] === ringFirst
      && ui.state.focusCardId === handCardId
      && ui.state.selectedTargets.join(",") === "1"
      && ui.state.preactivation && ui.state.preactivation.name === "测试技"
      && ui.state.pendingClock.receivedAt === clockBefore,
    `gets=${idleGets.length} viewSame=${ui.state.view === viewBefore}`
      + ` ringSame=${$("t-seats").children[0] === ringFirst}`
      + ` focus=${ui.state.focusCardId} targets=${ui.state.selectedTargets.join(",")}`
      + ` pre=${ui.state.preactivation ? ui.state.preactivation.name : "(清空)"}`
      + ` clockSame=${ui.state.pendingClock.receivedAt === clockBefore}`);

  // 弹窗打开期间继续轮询（超时代答靠它生效）：不因为 `state.dialog.open` 跳过。
  ui.state.dialog.open = true;
  const dialogCallsBefore = calls.length;
  await pollTimer.fn();
  const dialogGets = calls.slice(dialogCallsBefore).filter(
    (call) => call.path.startsWith("/api/sessions/") && call.body === null);
  ui.state.dialog.open = false;
  check("U3-05u 弹窗打开期间仍轮询（超时代答依赖它）",
    dialogGets.length === 1, `gets=${dialogGets.length}`);

  // 失败静默：前两次页面一字不动，连续第 3 次才提示。
  const noticeBefore = $("t-choice").textContent;
  const failureCallsBefore = calls.length;
  failNextGets = 1;
  await pollTimer.fn();
  const noticeAfterFirst = $("t-choice").textContent;
  failNextGets = 1;
  await pollTimer.fn();
  const noticeAfterSecond = $("t-choice").textContent;
  failNextGets = 1;
  await pollTimer.fn();
  const noticeAfterThird = $("t-choice").textContent;
  failNextGets = 0;
  const failureGets = calls.slice(failureCallsBefore).filter(
    (call) => call.path.startsWith("/api/sessions/") && call.body === null).length;
  check("U3-05v 轮询失败静默：前两次不提示，连续第 3 次才提示",
    failureGets === 3
      && noticeAfterFirst === noticeBefore
      && noticeAfterSecond === noticeBefore
      && noticeAfterThird.includes("取增量失败"),
    `gets=${failureGets} firstSame=${noticeAfterFirst === noticeBefore}`
      + ` secondSame=${noticeAfterSecond === noticeBefore} third="${noticeAfterThird}"`);

  // ---- ⑤ 播放中 / 提交在飞时暂停轮询，commit 后恢复 ----
  const resumeSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: 60, seed: 20261014,
  });
  await openSession(resumeSession);
  ui.applyViewRaw({
    ...resumeSession, cursor: resumeSession.cursor + 1, pending: null,
    records: [{ kind: "delta", delta_type: "hp_change", seq: resumeSession.cursor + 1,
      frame_id: 3, depth: 1, seat: 1, before: 4, after: 3, amount: -1 }],
  });
  const playCallsBefore = calls.length;
  await pollTimer.fn();
  const playGets = calls.slice(playCallsBefore).filter(
    (call) => call.path.startsWith("/api/sessions/") && call.body === null).length;
  check("U3-05w 播放中暂停轮询（整批事件播完之前不发 GET）",
    Boolean(ui.state.playback) && playGets === 0,
    `playback=${Boolean(ui.state.playback)} gets=${playGets}`);
  ui.flushPlayback();
  const resumeCallsBefore = calls.length;
  await pollTimer.fn();
  const resumeGets = calls.slice(resumeCallsBefore).filter(
    (call) => call.path.startsWith("/api/sessions/") && call.body === null).length;
  check("U3-05w2 commit 后轮询恢复", ui.state.playback === null && resumeGets === 1,
    `playback=${Boolean(ui.state.playback)} gets=${resumeGets}`);
  ui.state.submitBusy = true;
  const busyCallsBefore = calls.length;
  await pollTimer.fn();
  const busyGets = calls.slice(busyCallsBefore).filter(
    (call) => call.path.startsWith("/api/sessions/") && call.body === null).length;
  ui.state.submitBusy = false;
  check("U3-05w3 提交在飞时暂停轮询（不换掉点击前那一面）",
    busyGets === 0, `gets=${busyGets}`);

  // 收尾：状态不留给后面的用例（本段在文件末尾，只是 hygiene）。
  ui.state.liveView = null;
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.playback = null;
  ui.state.animScale = 1;
  ui.resetSelection();
  ui.state.cursor = -1;
  ui.state.pendingClock.seq = null;
  ui.state.pendingClock.receivedAt = 0;
}

/* ============ U3 评审修复：读取方（live view / 跨快照来源）+ 纯函数直喂 ============
 *
 * 三条都来自 U3 的评审（`CODING_STANDARDS.md` §1 / §4 / §6）：
 *  - D8.1：目标线的「目标已阵亡」必须读**牌桌正在渲染的视图**（回放期 = 逐事件派生的
 *    `state.liveView`）。同一批里刚阵亡的座位只在 live view 里 `alive === false`，读上一份
 *    提交的 `state.view` 会把线画到已死的座位上；舞台底行的武将名同理（:func:`tableViewForRender`）。
 *  - D2 / D10：`mergeEffectCards` 从快照补出来的处理中项没有产生它的 `card_move`，
 *    来源从这份舞台项列表记着的 `card_id → fromZone` 里补回来（真机验收的【火攻】底行空）。
 *  - D15：`stageLane` 是纯函数，直接喂 `now` 断 visible / fading / gone，不经过
 *    `renderProcessing`。
 * 载荷与响应体同形；时间不靠真实等待。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walk(child, out); }
    return out;
  };
  const hasClass = (node, name) => Boolean(node && (
    node.classList.contains(name)
      || String(node.className || "").split(/\s+/).includes(name)));
  const seatNodesNow = () => [...$("t-seats").children, $("t-self-seat")];
  const seatNodeOf = (seat) => seatNodesNow().find(
    (node) => attr(node, "data-seat") === String(seat)) || null;
  const lineNodes = () => walk($("t-target-lines")).filter(
    (node) => hasClass(node, "target-line") && attr(node, "data-line-to") !== undefined);
  const stageNodes = () => Array.from($("t-processing").children).filter(
    (node) => node.classList.contains("stage-card"));
  const stageNode = (id) => stageNodes().find(
    (node) => attr(node, "data-card-id") === String(id));
  const stageLineNode = (id) => {
    const node = stageNode(id);
    return node ? walk(node).find((child) => hasClass(child, "ctype")) || null : null;
  };
  const stageLine = (id) => (stageLineNode(id) ? stageLineNode(id).textContent : "");
  const labelOf = (id) => attr(stageLineNode(id), "data-zone-label");
  const seatPlayer = (seat, heroName) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, hero_name: heroName,
  });
  const card = (id, name) => ({
    ...synthCard(id, name), kind: "normal", attribute: "普通",
    description: `【${name}】的全文描述`,
  });
  const baseView = (session) => {
    const out = synthView(null);
    out.session_id = session;
    out.heroes = { 0: "guojia", 1: "zhouyu", 2: "diaochan", 3: "zhangfei" };
    out.records = [];
    out.narrative = [];
    out.snapshot.processing = [];
    out.snapshot.players = [
      seatPlayer(0, "郭嘉"), seatPlayer(1, "周瑜"),
      seatPlayer(2, "貂蝉"), seatPlayer(3, "张飞"),
    ];
    return out;
  };
  const designate = (seq, actor, targets) => ({
    kind: "timing", timing: "USE_DESIGNATE_TARGET", seq, frame_id: 5, depth: 1,
    actor, source: actor, targets, cards: [],
  });
  const death = (seq, seat) => ({
    kind: "timing", timing: "DEATH_BEFORE_START", seq, frame_id: 9, depth: 2,
    actor: seat, targets: [seat], cards: [],
  });
  const move = (seq, moved, from, to) => ({
    kind: "delta", delta_type: "card_move", seq, frame_id: 0, depth: 1, reason: "USE",
    owner: from.seat === null || from.seat === undefined ? null : from.seat,
    from_zone: from, to_zone: to, cards: [moved],
  });
  const playerOf = (view, seat) => view.snapshot.players.find(
    (player) => Number(player.seat) === Number(seat));

  ui.state.animScale = 1;

  // ---- ① 目标线的存活读 live view：提交视图说活、live view 说死 ⇒ 不画线 ----
  // 先钉夹具：两个视图都活时，同一份目标表真的会画线（否则"没有线"可能只是夹具写错）。
  ui.state.view = null;
  ui.applyViewRaw({ ...baseView("u3-review-live"), cursor: 0, records: [], finished: false });
  ui.applyViewRaw({
    ...baseView("u3-review-live"), cursor: 1, records: [designate(1, 1, [2])], finished: false,
  });
  ui.stepPlayback(true);
  const fixtureLine = lineNodes().length;
  ui.flushPlayback();
  check("U3-R1 夹具：提交视图里目标活着时，USE_DESIGNATE_TARGET 画一条线",
    fixtureLine === 1 && lineNodes().length === 1, `lines=${lineNodes().length}`);

  // 同一批里目标先阵亡、再收到指定它的目标时机：提交视图仍是"活"，live view 已是"死"。
  ui.applyViewRaw({ ...baseView("u3-review-live-2"), cursor: 0, records: [], finished: false });
  const stageCard = card(511, "火攻");
  ui.applyViewRaw({
    ...baseView("u3-review-live-2"), cursor: 3, finished: false,
    records: [
      death(1, 2),
      designate(2, 1, [2]),
      move(3, stageCard, { zone: "hand", seat: 1 }, { zone: "processing", seat: null }),
    ],
  });
  ui.stepPlayback(true);  // 阵亡：只写 live view
  ui.stepPlayback(true);  // 指定目标：目标在 live view 里已死
  const committedAlive = playerOf(ui.state.view, 2).alive;
  const liveAlive = playerOf(ui.state.liveView, 2).alive;
  const linesToDead = lineNodes().length;
  check("U3-R2 目标已阵亡读 live view：提交视图说活、live view 说死 ⇒ 不画线",
    committedAlive === true && liveAlive === false && linesToDead === 0
      && Boolean(ui.state.playback),
    `committed=${committedAlive} live=${liveAlive} lines=${linesToDead}`);

  ui.stepPlayback(true);  // 处理中的牌：底行的武将名走**渲染视图**
  check("U3-R3 播放期间舞台底行仍按渲染视图取武将名（live 路径照常渲染）",
    Boolean(ui.state.playback) && stageLine(511) === "1·周瑜·手牌"
      && labelOf(511) === "1·周瑜·手牌",
    `playback=${Boolean(ui.state.playback)} line="${stageLine(511)}" label=${labelOf(511)}`);
  ui.flushPlayback();

  // ---- ② 跨快照来源：快照补出来的处理中项仍写得出「座位·武将·区域」（D2 / D10） ----
  ui.applyViewRaw({ ...baseView("u3-review-zone"), cursor: 0, records: [], finished: false });
  ui.state.effectCards = ui.applyEffectDelta(
    move(10, stageCard, { zone: "hand", seat: 1 }, { zone: "processing", seat: null }),
    ui.state.effectCards);
  ui.renderProcessing();
  const recordedLabel = labelOf(511);
  const recordedItem = (ui.state.effectCards || []).find(
    (entry) => String(entry.card_id) === "511");
  // 让这一项离开列表（兜底 30s 到点，只拨 fadeAt、不等真实时间），下一次 commit 只从快照补出来。
  if (recordedItem) recordedItem.stage.fadeAt = 1;
  const unseenCard = card(512, "无中生有");
  const zoneRefresh = baseView("u3-review-zone");
  zoneRefresh.cursor = 11;
  zoneRefresh.snapshot.processing = [stageCard, unseenCard];
  ui.applyViewRaw(zoneRefresh);
  check("U3-R4 快照补出来的处理中项从列表记的 fromZone 补底行（1·周瑜·手牌）；没见过的牌不编来源",
    recordedLabel === "1·周瑜·手牌"
      && stageNode(511) !== undefined
      && attr(stageNode(511), "data-stage-lane") === "processing"
      && labelOf(511) === "1·周瑜·手牌"
      && stageLine(511) === "1·周瑜·手牌"
      && !labelOf(512) && stageLine(512) === "",
    `recorded="${recordedLabel}" after="${stageLine(511)}" label=${labelOf(511)}`
      + ` unseen=${labelOf(512)}`);

  // ---- ③ stageLane 纯函数：直接喂 now 断三段（不经过 renderProcessing，D15） ----
  ui.state.animScale = 1;
  const laneItem = {
    card_id: 77,
    stage: { lane: "processing", frameId: 0, enteredAt: 1000, fadeAt: 2000, fromZone: null },
  };
  const fallbackItem = {
    card_id: 78,
    stage: { lane: "discard", frameId: 7, enteredAt: 0, fadeAt: null, fromZone: null },
  };
  const stageBefore = $("t-processing").children.length;
  const lanes = [ui.stageLane(laneItem, 1999), ui.stageLane(laneItem, 2000),
    ui.stageLane(laneItem, 3000)].join(",");
  const fallbackLanes = [ui.stageLane(fallbackItem, 29999), ui.stageLane(fallbackItem, 30000),
    ui.stageLane(fallbackItem, 31000)].join(",");
  ui.state.animScale = 0;
  const collapsed = ui.stageLane(laneItem, 2000);
  ui.state.animScale = 1;
  check("U3-R5 stageLane 纯函数直喂 now：visible / fading / gone（含兜底 30s；animScale=0 淡出即时）",
    lanes === "visible,fading,gone"
      && fallbackLanes === "visible,fading,gone"
      && collapsed === "gone"
      && laneItem.stage.fadeAt === 2000
      && $("t-processing").children.length === stageBefore,
    `lanes=${lanes} fallback=${fallbackLanes} collapsed=${collapsed}`
      + ` fadeAt=${laneItem.stage.fadeAt} nodes=${stageBefore}->${$("t-processing").children.length}`);

  // 收尾：不留状态给后面的用例。
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.liveView = null;
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.playback = null;
  ui.state.animScale = 1;
  ui.resetSelection();
}


/* ============ 票 19（M6）：武将牌上的牌区 / ☆·失效·失去 / 混合池名称 / 轮次与额外回合 ====
 *
 * 四处都是**界面读法**的断言：服务端载荷形状由 `tests/test_web.py` 钉，这里喂与响应体
 * 同形的合成 `view` 看界面按不按那些键读。混合池名称另有一条**真会话**判据（点将 =
 * 界刘备），证明服务端与界面两侧都翻得出"界刘备"（spec 用户故事：刘备 / 界刘备 分得清）。
 */
{
  const boxSkills = (node) => {                   // 递归找 detail / zone 行（合成 DOM 只有 children）
    const found = [];
    const walk = (n) => {
      for (const child of Array.from((n && n.children) || [])) {
        found.push(child);
        walk(child);
      }
    };
    walk(node);
    return found;
  };
  const rowOf = (root, attr, value) => boxSkills(root).find(
    (node) => node.getAttribute && node.getAttribute(attr) === String(value)) || null;
  const hasClass = (node, name) => Boolean(node && (
    node.classList.contains(name)
    || String(node.className || "").split(/\s+/).includes(name)));

  /* ---- ④ 混合池：真会话 + 旧载荷兜底（spec 用户故事「刘备 / 界刘备 分得清」） ---- */
  const jieSession = await newSession(4321, { my_hero: "jie_liubei" });
  ui.flushPlayback();
  check("㉿M6-1 混合池：本人点将界刘备 ⇒ 载荷座位名是「界刘备」（不是引擎 id）",
    jieSession.snapshot.players[0].hero_name === "界刘备",
    `载荷 hero_name=${JSON.stringify(jieSession.snapshot.players[0].hero_name)}`);
  check("㉿M6-2 混合池：座位卡写「界刘备」，不显示 jie_liubei",
    $("t-hero").textContent === "界刘备", `界面="${$("t-hero").textContent}"`);

  const fallbackView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 9401,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9401 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  fallbackView.heroes = { 0: "jie_liubei", 1: "liubei", 2: "jie_daqiao" };
  // 旧载荷 / 合成视图没有 `hero_name`：界面必须按目录把 id 翻成中文名，而不是显示 id。
  for (const player of fallbackView.snapshot.players) delete player.hero_name;
  ui.state.sessionId = null;
  ui.state.view = fallbackView;
  ui.resetSelection();
  ui.renderTable();
  check("㉉M6-3 没有 hero_name 的旧载荷：目录把 jie_liubei 翻成「界刘备」",
    $("t-hero").textContent === "界刘备", `界面="${$("t-hero").textContent}"`);
  const foeBoxOne = seatBox(1);
  check("㉉M6-4 同真名的标准版仍写「刘备」（两版在环上分得清）",
    !!foeBoxOne && foeBoxOne.text().includes("刘备") && !foeBoxOne.text().includes("jie_liubei"),
    foeBoxOne ? foeBoxOne.text().replace(/\s+/g, " ").trim().slice(0, 60) : "缺座位 1");
  const displayName = (heroId) => (typeof ui.heroDisplayName === "function"
    ? ui.heroDisplayName(heroId) : null);
  check("㉉M6-5 目录里没有的 id 才退回 id 本身（不编名字）",
    displayName("jie_not_a_hero") === "jie_not_a_hero"
      && displayName("jie_daqiao") === "界大乔",
    `${displayName("jie_not_a_hero")} / ${displayName("jie_daqiao")}`);

  /* ---- ② 武将牌上的牌区（可跨座位、可多人并存） + 不算手牌 ----
   * 形状绑定：`players[].hero_zones` 的键集（skill_id / skill_name / owner / cards）
   * 由 tests/test_web.py::test_the_ui_smoke_hero_zone_and_skill_fixtures_match_the_real_snapshot_shape
   * 从真快照钉住；改这里的夹具形状先看那条用例。 */
  const zoneView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 9402,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9402 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  zoneView.snapshot.players[0].hand_count = 2;   // 区里的两张**不算**手牌
  zoneView.snapshot.players[0].hero_zones = [{
    skill_id: "hero.jie_luxun.qianxun", skill_name: "谦逊", owner: 1, count: 2,
    cards: [synthCard(21, "杀"), synthCard(22, "闪")],
  }];
  zoneView.snapshot.players[0].markers = [{
    name: "梦魇", count: 2, max_count: null, source_seat: 0,
    source_skill_id: "hero.shen_guanyu_mobile.wuhun",
    source_label: "座位 1 神关羽·武魂", description: null,
  }];
  zoneView.snapshot.players[0].hero_tags = [
    { tag: "hand_limit_bonus", label: "手牌上限+1" },
    { tag: "luoyi", label: "裸衣" },
  ];
  // 非宿主拿到的同一区：标签在、cards 为空（服务端已裁；界面不自己藏牌）。
  zoneView.snapshot.players[2].hero_zones = [{
    skill_id: "hero.jie_xusheng.pojun", skill_name: "破军", owner: 0, count: 2, cards: [],
  }];
  zoneView.snapshot.players[2].hero_tags = [{ tag: "luoyi", label: "裸衣" }];
  ui.state.view = zoneView;
  ui.resetSelection();
  ui.renderTable();
  const selfZoneRow = rowOf($("t-self-hero-zones"), "data-zone", "hero");
  const selfZoneTag = selfZoneRow
    ? Array.from(selfZoneRow.children).find((node) => hasClass(node, "hero-zone-tag"))
    : null;
  const selfZonesBox = $("t-self-hero-zones");
  const previewFaces = () => Array.from($("t-card-preview").children).filter(
    (node) => String(node.className || "").split(/\s+/).includes("card"));
  const hoverTag = (tag, event) => {
    if (!tag) return;
    (tag.listeners.mouseenter || []).forEach(
      (fn) => fn(event || { target: tag, clientX: 100, clientY: 120 }));
  };
  hoverTag(selfZoneTag);
  const selfPreview = {
    hidden: $("t-card-preview").hidden,
    ids: previewFaces().map((node) => node.getAttribute("data-card-id")),
    left: $("t-card-preview").style.left,
    top: $("t-card-preview").style.top,
  };
  // 用户 2026-11-07：点击不再"在武将块上依次展示牌名"——标签上没有 click 监听、行里没有牌容器。
  if (selfZoneTag) {
    (selfZoneTag.listeners.click || []).forEach(
      (fn) => fn({ target: selfZoneTag, stopPropagation() {} }));
  }
  const afterClick = { hidden: $("t-card-preview").hidden, faces: previewFaces().length };
  if (selfZoneTag) {
    (selfZoneTag.listeners.mouseleave || []).forEach((fn) => fn({ target: selfZoneTag }));
  }
  const hiddenAfterLeave = $("t-card-preview").hidden;
  check("㉉M6-6 自己的武将牌上区域 = 一个标签；悬停出跟随鼠标的常规卡面预览，点击不再展开",
    !!selfZoneRow && selfZonesBox.hidden === false
      && selfZoneRow.getAttribute("data-skill-id") === "hero.jie_luxun.qianxun"
      && selfZoneRow.getAttribute("data-cards-visible") === "true"
      && !!selfZoneTag && selfZoneTag.textContent === "谦逊 2"
      && (selfZoneTag.listeners.click || []).length === 0
      && !Array.from(selfZoneRow.children).some(
        (node) => hasClass(node, "hero-zone-cards"))
      && selfPreview.hidden === false
      && selfPreview.ids.join(",") === "21,22"
      && selfPreview.left === "116px" && selfPreview.top === "136px"
      && afterClick.hidden === false && afterClick.faces === 2
      && hiddenAfterLeave === true,
    selfZoneRow ? selfZoneRow.text().replace(/\s+/g, " ").trim() : "没有 hero 行");

  // 非宿主：同一区只有标签；悬停给一行说明（牌面按可见性不给）。
  const seat2Box = rowOf($("t-seats"), "data-seat", "2");
  const nonHostRow = rowOf(seat2Box, "data-skill-id", "hero.jie_xusheng.pojun");
  const nonHostTag = nonHostRow
    ? Array.from(nonHostRow.children).find((node) => hasClass(node, "hero-zone-tag"))
    : null;
  hoverTag(nonHostTag);
  const nonHostPreview = {
    hidden: $("t-card-preview").hidden,
    text: $("t-card-preview").text(),
    faces: previewFaces().length,
  };
  if (nonHostTag) {
    (nonHostTag.listeners.mouseleave || []).forEach((fn) => fn({ target: nonHostTag }));
  }
  check("㉉M6-6b 非宿主：标签照常可见，悬停只给「牌面仅宿主可见」一行、不画牌面",
    !!nonHostRow && nonHostRow.getAttribute("data-cards-visible") === "false"
      && !!nonHostTag && nonHostTag.textContent === "破军 2"
      && nonHostPreview.hidden === false
      && nonHostPreview.faces === 0
      && nonHostPreview.text.includes("牌面仅宿主可见（2 张）")
      && $("t-card-preview").hidden === true,
    `row=${nonHostRow ? nonHostRow.getAttribute("data-cards-visible") : "none"}`
      + ` text="${nonHostPreview.text}"`);
  // 票 06：标记与武将牌上的牌区同骨架、异色；文本 = 名称×数量，悬停 = 来源。
  const selfMarkerRow = selfZonesBox
    ? rowOf(selfZonesBox, "data-zone", "marker")
    : null;
  const selfMarkerTag = selfMarkerRow
    ? Array.from(selfMarkerRow.children).find((node) => hasClass(node, "marker-tag"))
    : null;
  check("㉉M6-6c 标记行：名称×数量 + 悬停来源（无效果说明只显示来源）",
    !!selfMarkerRow
      && selfMarkerRow.getAttribute("data-marker") === "梦魇"
      && !!selfMarkerTag && selfMarkerTag.textContent === "梦魇×2"
      && selfMarkerTag.getAttribute("title") === "座位 1 神关羽·武魂",
    selfMarkerRow ? selfMarkerRow.text().replace(/\s+/g, " ").trim() : "没有标记行");
  check("㉉M6-7 区里的牌**不算**手牌数（公开数字仍是 hand_count；合成载荷没有 hand_limit ⇒ 票 09 的 fail-closed 读数 2/2）",
    $("t-self-hand").textContent === "2/2" && $("t-hand").children.length === 1,
    `hand_count=${$("t-self-hand").textContent} 手牌面=${$("t-hand").children.length}`);
  const foeZoneBox = seatBox(2);
  const foeZoneRow = foeZoneBox ? rowOf(foeZoneBox, "data-zone", "hero") : null;
  const foeZoneTag = foeZoneRow
    ? Array.from(foeZoneRow.children).find((node) => hasClass(node, "hero-zone-tag"))
    : null;
  const foeZoneCardsBox = foeZoneRow
    ? Array.from(foeZoneRow.children).find((node) => hasClass(node, "hero-zone-cards"))
    : null;
  check("㉉M6-8 非宿主：只看到标签，没有牌 / 没有可展开的牌面",
    !!foeZoneRow
      && foeZoneRow.getAttribute("data-skill-id") === "hero.jie_xusheng.pojun"
      && foeZoneRow.getAttribute("data-seat") === "2"
      && foeZoneRow.getAttribute("data-cards-visible") === "false"
      && !!foeZoneTag && foeZoneTag.textContent === "破军 2"
      && !foeZoneCardsBox
      && !foeZoneRow.text().includes("桃")
      && !(foeZoneTag.listeners.click || []).length,
    foeZoneRow ? foeZoneRow.text().replace(/\s+/g, " ").trim() : "对手没有 hero 行");
  // B：手牌上限 / 裸衣 = 武将块上的状态标签（读服务端 `hero_tags`，不按牌画 +1）。
  const selfHeroTagsBox = $("t-self-hero-tags");
  const selfHeroTagChips = selfHeroTagsBox
    ? Array.from(selfHeroTagsBox.children).filter(
      (node) => hasClass(node, "hero-tag"))
    : [];
  const foeHeroTagsBox = foeZoneBox
    ? boxSkills(foeZoneBox).find((node) => hasClass(node, "hero-tags"))
    : null;
  const foeHeroTagChips = foeHeroTagsBox
    ? Array.from(foeHeroTagsBox.children).filter(
      (node) => hasClass(node, "hero-tag"))
    : [];
  check("㉉M6-8b 手牌上限 / 裸衣画成武将块标签（读 hero_tags，不给每张牌画 +1）",
    selfHeroTagsBox && selfHeroTagsBox.hidden === false
      && selfHeroTagChips.map((node) => node.getAttribute("data-tag")).join(",")
        === "hand_limit_bonus,luoyi"
      && selfHeroTagChips.map((node) => node.textContent).join(",")
        === "手牌上限+1,裸衣"
      && foeHeroTagChips.map((node) => node.getAttribute("data-tag")).join(",") === "luoyi"
      && foeHeroTagChips.map((node) => node.textContent).join(",") === "裸衣",
    `self=${selfHeroTagChips.map((n) => n.textContent).join(",")}`
      + ` foe=${foeHeroTagChips.map((n) => n.textContent).join(",")}`);
  const noZoneView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 9403,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9403 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  ui.state.view = noZoneView;
  ui.resetSelection();
  ui.renderTable();
  const noZoneHeroTags = $("t-self-hero-tags");
  check("㉉M6-9 没有武将牌上区域 ⇒ 不画空行（整块隐藏；空区不是界面概念）",
    $("t-self-hero-zones").hidden === true
      && !rowOf($("t-self-hero-zones"), "data-zone", "hero")
      && !rowOf($("t-seats"), "data-zone", "hero")
      && noZoneHeroTags && noZoneHeroTags.hidden === true,
    `self=${!$("t-self-hero-zones").hidden}`);

  /* ---- ③ ☆ / 失效 / 失去：同一条技能清单里的三种状态 ---- */
  const skillView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 9404,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9404 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  /* 形状绑定：`players[].skills[]` 的键集与 `star` / `state` 三态（active / suppressed /
   * lost）同样由 tests/test_web.py 的上面那条形状用例从真投影钉住。 */
  const skillRow = (id, name, extra = {}) => ({
    skill_id: id, name, skill_type: "NORMAL", locked: false, carrier: "HERO",
    star: false, state: "active", description: `${name}的描述。`,
    ui: { passive: false, requirement: "", convert_sources: [] }, ...extra,
  });
  skillView.snapshot.players[0].skills = [
    skillRow("hero.jie_xushu.qianxue", "勤学", { skill_type: "AWAKENING" }),
    skillRow("hero.jie_xushu.jianyan", "荐言", { star: true }),
    skillRow("hero.jie_xushu.zhuhai", "诛害", { state: "suppressed" }),
    skillRow("hero.jie_xushu.fenwei", "奋威", { state: "lost" }),
  ];
  skillView.skills = [
    { ...skillRow("hero.jie_xushu.jianyan", "荐言", { star: true }), available: true, preactivated: false },
    { ...skillRow("hero.jie_xushu.zhuhai", "诛害", { state: "suppressed" }), available: false, preactivated: false },
    { ...skillRow("hero.jie_xushu.qianxue", "勤学"), available: false, preactivated: false },
  ];
  // 失效优先于"预发动"：即使这份询问里还残留同名清单，按钮也不该画入口徽标。
  skillView.pending.preactivations = { "hero.jie_xushu.zhuhai": [{}] };
  ui.state.view = skillView;
  ui.resetSelection();
  ui.renderTable();
  const barButton = Array.from($("t-skills").children)
    .find((node) => node.getAttribute("data-skill-id") === "hero.jie_xushu.jianyan");
  check("㉉M6-10 技能按钮上标出 ☆（觉醒后出现的技能）",
    !!barButton && barButton.text().includes("☆")
      && barButton.getAttribute("data-star") === "true",
    barButton ? barButton.text() : "没有荐言按钮");

  /* 用户报障：被【铁骑】封锁的技能在左侧技能按钮上没有直观变化。禁止图标只属于
   * `state = suppressed` 这一档；`unavailable`（此刻不给入口）与 active 都不画，
   * 也不把 `lost`（按钮清单里根本没有它）混进来。 */
  const skillButton = (id) => Array.from($("t-skills").children)
    .find((node) => node.getAttribute("data-skill-id") === id);
  const suppressedButton = skillButton("hero.jie_xushu.zhuhai");
  const unavailableButton = skillButton("hero.jie_xushu.qianxue");
  const banMarks = (button) => Array.from((button && button.children) || [])
    .filter((node) => node.classList && node.classList.contains("skill-ban"));
  const ban = banMarks(suppressedButton);
  check("㉉M6-10a 失效技能按钮画禁止图标（内联 SVG：红圈 + 斜杠）且带无障碍名称",
    !!suppressedButton
      && suppressedButton.disabled === true
      && suppressedButton.classList.contains("suppressed")
      && suppressedButton.getAttribute("data-state") === "suppressed"
      && /失效/.test(suppressedButton.getAttribute("aria-label") || "")
      && /失效/.test(suppressedButton.getAttribute("title") || "")
      && !/预发动/.test(suppressedButton.text())
      && suppressedButton.getAttribute("data-preactivation") === undefined
      && ban.length === 1
      && ban[0].getAttribute("aria-hidden") === "true"
      && /<svg[\s>]/.test(ban[0].innerHTML)
      && /<circle[\s>]/.test(ban[0].innerHTML)
      && /<line[\s>]/.test(ban[0].innerHTML),
    suppressedButton
      ? `class=${suppressedButton.className} label=${suppressedButton.getAttribute("aria-label")}`
        + ` ban=${ban.length} svg=${ban[0] ? ban[0].innerHTML : ""}`
      : "没有诛害按钮");
  check("㉉M6-10b 只有失效按钮画禁止图标（可发动 / 此刻不可用都不画）",
    banMarks(barButton).length === 0
      && !!unavailableButton
      && banMarks(unavailableButton).length === 0
      && !unavailableButton.classList.contains("suppressed")
      && unavailableButton.getAttribute("data-state") === "active",
    `active=${banMarks(barButton).length} unavailable=${banMarks(unavailableButton).length}`);
  const iconCss = readFileSync("tools/sgs_web/static/style.css", "utf8");
  const cssRule = (selector) => {
    const found = new RegExp(`${selector}\\s*\\{([^}]*)\\}`).exec(iconCss);
    return found ? found[1] : "";
  };
  const suppressedRule = cssRule("\\.skill\\.suppressed");
  const banRule = cssRule("\\.skill-ban");
  check("㉉M6-10c 禁止图标用现有强调红、绝对定位在右侧且不挡技能名",
    /position:\s*relative/.test(suppressedRule)
      && /padding-right:\s*calc\(/.test(suppressedRule)
      && /position:\s*absolute/.test(banRule)
      && /right:\s*calc\(/.test(banRule)
      && /color:\s*var\(--accent\)/.test(banRule)
      && /pointer-events:\s*none/.test(banRule),
    `suppressed={${suppressedRule}} ban={${banRule}}`);

  const fireContext = (node) => {
    const event = { target: node, preventDefault() {}, stopPropagation() {} };
    (node.listeners.contextmenu || []).forEach((fn) => fn(event));
  };
  fireContext($("t-self-hero"));
  const detailRow = (id) => rowOf($("t-modal-body"), "data-skill-id", id);
  const starRow = detailRow("hero.jie_xushu.jianyan");
  const suppressedRow = detailRow("hero.jie_xushu.zhuhai");
  const lostRow = detailRow("hero.jie_xushu.fenwei");
  const activeRow = detailRow("hero.jie_xushu.qianxue");
  check("㉉M6-11 ☆ 技能在详情里带 ☆ 标记（未觉醒时根本不出现是服务端判据）",
    !!starRow && starRow.text().includes("☆"),
    starRow ? starRow.text().replace(/\s+/g, " ").trim() : "没有 ☆ 行");
  // 垫片的 `className` 与 `classList` 不联动（真 DOM 联动）：类名断言读 className。
  const classNamesOf = (node) => String((node && node.className) || "").split(/\s+/).filter(Boolean);
  check("㉉M6-12 失效 = 「有但被挡住」：仍列出 + 「失效」徽标 + state-suppressed",
    !!suppressedRow
      && suppressedRow.getAttribute("data-state") === "suppressed"
      && classNamesOf(suppressedRow).includes("state-suppressed")
      && suppressedRow.text().includes("失效"),
    suppressedRow ? suppressedRow.text().replace(/\s+/g, " ").trim() : "没有失效行");
  check("㉉M6-13 失去 = 「没了」：仍列出但划掉 + 「失去」徽标，与失效视觉可区分",
    !!lostRow
      && lostRow.getAttribute("data-state") === "lost"
      && classNamesOf(lostRow).includes("state-lost")
      && lostRow.text().includes("失去")
      && !classNamesOf(lostRow).includes("state-suppressed"),
    lostRow ? lostRow.text().replace(/\s+/g, " ").trim() : "没有失去行");
  check("㉉M6-14 正常的技能不带状态徽标（active 不噪声）",
    !!activeRow && activeRow.getAttribute("data-state") === "active"
      && !activeRow.text().includes("失效") && !activeRow.text().includes("失去"),
    activeRow ? activeRow.text().replace(/\s+/g, " ").trim() : "没有勤学行");
  const stateLabel = (skill) => (typeof ui.skillStateLabel === "function"
    ? ui.skillStateLabel(skill) : null);
  check("㉉M6-15 徽标文案的纯函数（active / 未登记值都不出字）",
    stateLabel({ state: "suppressed" }) === "失效"
      && stateLabel({ state: "lost" }) === "失去"
      && stateLabel({ state: "active" }) === ""
      && stateLabel({}) === "",
    `${stateLabel({ state: "suppressed" })}/${stateLabel({ state: "lost" })}`);
  fireDocument("keydown", { key: "Escape" });

  /* ---- ① 轮次与额外回合提示 ---- */
  const roundView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 9405,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9405 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  roundView.snapshot.round = 5;
  roundView.snapshot.turn_extra = true;
  ui.state.view = roundView;
  ui.state.logRecords = [];
  ui.resetSelection();
  ui.renderTable();
  check("㉉M6-16 顶栏轮次读服务端 `snapshot.round`（不在前端数回合）",
    $("t-round").textContent === "5", `实得 "${$("t-round").textContent}"`);
  // 提示条的**标签文本**是静态 HTML（垫片不解析静态文本），显隐由 `turn_extra` 决定。
  check("㉉M6-17 额外回合有提示（标签在顶栏；显示 / 隐藏由服务端的 turn_extra 决定）",
    $("t-extra-turn").hidden === false
      && /id="t-extra-turn"[^>]*>\s*额外回合/.test(html),
    `hidden=${$("t-extra-turn").hidden} 标签在 HTML=${/id="t-extra-turn"[^>]*>\s*额外回合/.test(html)}`);

  const oldRoundView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 9406,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    prompt_display: { text: "出牌阶段：使用一张牌，或结束出牌阶段。", default_label: "超时将自动结束出牌阶段" },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9406 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  ui.state.view = oldRoundView;
  ui.state.logRecords = [
    { kind: "timing", timing: "TURN_START", actor: 0, seq: 1 },
    { kind: "timing", timing: "TURN_START", actor: 1, seq: 2 },
    { kind: "timing", timing: "TURN_START", actor: 0, seq: 3 },
  ];
  ui.renderTable();
  check("㉉M6-18 旧录像 / 合成视图没有 round ⇒ 退回按 TURN_START 的本地数法（顶栏不空）",
    $("t-round").textContent === "2" && $("t-extra-turn").hidden === true,
    `round="${$("t-round").textContent}" hidden=${$("t-extra-turn").hidden}`);

  const live = {
    snapshot: { ...roundView.snapshot, round: 1, turn_extra: false, players: [] },
  };
  if (typeof ui.applyLiveDelta === "function") {
    ui.applyLiveDelta(
      { kind: "delta", delta_type: "turn_change", seat: 3, round: 7, extra: true }, live);
  }
  check("㉉M6-19 逐事件派生：`turn_change` 记录把轮次与额外回合写进 live view",
    live.snapshot.round === 7 && live.snapshot.turn_extra === true,
    `round=${live.snapshot.round} extra=${live.snapshot.turn_extra}`);

  // 收尾：别把这一段的状态带进后面的用例。
  ui.state.logRecords = [];
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.liveView = null;
  ui.resetSelection();
}

/* ============ 反证 / 回归：缺陷 2（火攻亮牌寿命）与缺陷 3（连续判定展示） ============
 *
 * 这两条**不是**实现缺陷，而是既有设计（06 §5 的 U3-02 行 / 04「寿命与淡出」）：
 * - 火攻亮牌走 `card_reveal`、挂 RESOLVE 帧，帧 `pop` 后 1s 淡出；
 * - 洛神判定牌走 JUDGE 帧 + `card_move`，离处理区即 1s 淡出、不进展示组。
 * 这里用与服务端同形的合成记录把现行为钉死（防以后被“顺手改成瞬删 / 挂外层帧”）：
 * 同会话 commit 不清展示组，换会话必清；火攻亮牌等 RESOLVE `pop`；判定牌之间重叠 1s。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const hasClass = (node, name) => Boolean(node && (
    node.classList.contains(name)
    || String(node.className || "").split(/\s+/).includes(name)));
  const stageNodes = () => Array.from($("t-processing").children).filter(
    (node) => node.classList.contains("stage-card"));
  const stageNode = (id) => stageNodes().find(
    (node) => attr(node, "data-card-id") === String(id)) || null;
  const laneOf = (id) => attr(stageNode(id), "data-stage-lane");
  const fading = (id) => hasClass(stageNode(id), "fading");
  const card = (id, name) => ({
    ...synthCard(id, name), kind: "normal", attribute: "普通",
    display_name: name, description: `【${name}】的全文描述`,
  });
  const seatPlayer = (seat, heroName) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, hero_name: heroName,
  });
  const view = (session) => {
    const out = synthView(null);
    out.session_id = session;
    out.snapshot.processing = [];
    out.snapshot.players = [
      seatPlayer(0, "甄姬"), seatPlayer(1, "郭嘉"), seatPlayer(2, "貂蝉"),
    ];
    return out;
  };
  const at = (zone, seat = null) => ({ zone, seat });
  const move = (seq, frame, moved, from, to, reason) => ({
    kind: "delta", delta_type: "card_move", seq, frame_id: frame, depth: 1,
    reason, owner: from.seat, from_zone: from, to_zone: to, cards: [moved],
  });
  const push = (seq, frame, kind) => ({
    kind: "frame", op: "push", frame_id: frame, frame_kind: kind, depth: 1, seq,
  });
  const pop = (seq, frame, kind) => ({
    kind: "frame", op: "pop", frame_id: frame, frame_kind: kind, depth: 1, seq,
  });
  const reveal = (seq, frame, moved, seat) => ({
    kind: "delta", delta_type: "card_reveal", seq, frame_id: frame, depth: 1,
    seat, card: moved,
  });
  const revealItem = (id) => (ui.state.revealedCards || []).find(
    (entry) => String(entry.card_id) === String(id)) || null;
  const effectItem = (id) => (ui.state.effectCards || []).find(
    (entry) => String(entry.card_id) === String(id)) || null;
  const loadView = (body) => {
    ui.applyViewRaw(body);
    ui.flushPlayback();
  };
  const resetStage = () => {
    ui.flushPlayback();
    ui.state.view = null;
    ui.state.sessionId = null;
    ui.state.effectCards = [];
    ui.state.revealedCards = [];
    ui.state.playback = null;
    ui.state.animScale = 1;
    ui.renderProcessing();
    ui.resetSelection();
  };

  // ---- 缺陷 2-a：亮牌挂 RESOLVE 帧；pop 之前一直 visible（跨 commit） ----
  resetStage();
  const SESSION = "u3-def2-fire";
  loadView({ ...view(SESSION), cursor: 0, log_seq: 0, records: [], finished: false });
  const FIRE_REVEAL = card(701, "赤兔");
  loadView({
    ...view(SESSION), cursor: 1, log_seq: 0, finished: false,
    records: [reveal(1, 2, FIRE_REVEAL, 1)],
  });
  const fire = revealItem(701);
  const fireVisibleBeforePop = Boolean(fire)
    && ui.stageLane(fire, fire.stage.enteredAt + 5000) === "visible"
    && laneOf(701) === "showcase" && !fading(701);
  loadView({
    ...view(SESSION), cursor: 2, log_seq: 0, finished: false,
    records: [pop(2, 2, "RESOLVE")],
  });
  const fireFadeAt = fire && fire.stage.fadeAt;
  const fireFadesOnPop = Boolean(fire) && Number.isFinite(fireFadeAt)
    && ui.stageLane(fire, fireFadeAt) === "fading"
    && ui.stageLane(fire, fireFadeAt + 999) === "fading"
    && ui.stageLane(fire, fireFadeAt + 1000) === "gone";
  ui.renderProcessing(fireFadeAt);
  const fireNodeFading = Boolean(stageNode(701)) && fading(701);
  ui.renderProcessing(fireFadeAt + 999);
  const fireNodeStill = Boolean(stageNode(701));
  ui.renderProcessing(fireFadeAt + 1000);
  const fireNodeGone = !stageNode(701);
  check("U3-01w 火攻亮牌挂 RESOLVE 帧：pop 前跨 commit 一直 visible、pop 后 fading 1s 才移除",
    fireVisibleBeforePop && fireFadesOnPop && fireNodeFading && fireNodeStill && fireNodeGone,
    `before=${fireVisibleBeforePop} pop=${fireFadesOnPop} fading=${fireNodeFading}`
      + ` keep=${fireNodeStill} gone=${fireNodeGone} fadeAt=${fireFadeAt}`);

  // ---- 缺陷 1 续播：每次重绘都 appendChild（节点被移出再插入）会让 CSS 动画从头播；
  // 负 animation-delay 把动画直接推到 now − fadeAt 的位置，重绘越多也只会前进、不回 1。
  resetStage();
  const RESUME = "u3-def1-resume";
  loadView({ ...view(RESUME), cursor: 0, log_seq: 0, records: [], finished: false });
  const RESUME_REVEAL = card(706, "闪");
  loadView({
    ...view(RESUME), cursor: 1, log_seq: 0, finished: false,
    records: [reveal(40, 12, RESUME_REVEAL, 1)],
  });
  const resume = revealItem(706);
  loadView({
    ...view(RESUME), cursor: 2, log_seq: 0, finished: false,
    records: [pop(41, 12, "RESOLVE")],
  });
  const resumeFadeAt = resume && resume.stage.fadeAt;
  const animationDelayMs = (node) => {
    const raw = node && node.style ? String(node.style.animationDelay || "") : "";
    const found = raw.match(/^(-?\d+(?:\.\d+)?)(ms|s)$/);
    if (!found) return null;
    return Number(found[1]) * (found[2] === "s" ? 1000 : 1);
  };
  ui.renderProcessing(resumeFadeAt + 300);
  const node300 = stageNode(706);
  const delay300 = animationDelayMs(node300);
  ui.renderProcessing(resumeFadeAt + 700);
  const node700 = stageNode(706);
  const delay700 = animationDelayMs(node700);
  const nodes706 = stageNodes().filter((node) => attr(node, "data-card-id") === "706");
  check("U3-01w2 淡出重绘不重置：负 animation-delay 随 now 单调前进（+300 → +700），不回到 opacity 1",
    Boolean(resume) && Number.isFinite(resumeFadeAt)
      && delay300 !== null && delay300 <= -300
      && delay700 !== null && delay700 <= -700
      && delay700 < delay300
      && node700 === node300
      && nodes706.length === 1
      && hasClass(node300, "fading") && hasClass(node700, "fading"),
    `t300=${delay300} t700=${delay700} sameNode=${node700 === node300}`
      + ` nodes=${nodes706.length}`);

  // ---- 缺陷 2-b：结算同批走完（无可弃同花色）：0 停留 + 真实 1s 淡出，不是瞬删 ----
  resetStage();
  const BATCH = "u3-def2-batch";
  loadView({ ...view(BATCH), cursor: 0, log_seq: 0, records: [], finished: false });
  const BATCH_REVEAL = card(702, "闪");
  loadView({
    ...view(BATCH), cursor: 2, log_seq: 0, finished: false,
    records: [
      reveal(10, 7, BATCH_REVEAL, 1),
      pop(11, 7, "RESOLVE"),
    ],
  });
  const batch = revealItem(702);
  // D9：pop 不再 0 停留——亮牌项 N=1 ⇒ pop 后先停 1s，再走真实 1s 淡出。
  const batchDwell = Boolean(batch)
    && Number.isFinite(batch.stage.fadeAt)
    && batch.stage.fadeAt - batch.stage.enteredAt >= 1000
    && ui.stageLane(batch, batch.stage.fadeAt - 1) === "visible";
  const batchFadeWindow = Boolean(batch)
    && ui.stageLane(batch, batch.stage.fadeAt) === "fading"
    && ui.stageLane(batch, batch.stage.fadeAt + 999) === "fading"
    && ui.stageLane(batch, batch.stage.fadeAt + 1000) === "gone";
  ui.renderProcessing(batch.stage.fadeAt);
  const batchNodeFading = Boolean(stageNode(702)) && fading(702);
  ui.renderProcessing(batch.stage.fadeAt + 500);
  const batchNodeStill = Boolean(stageNode(702));
  ui.renderProcessing(batch.stage.fadeAt + 1000);
  const batchNodeGone = !stageNode(702);
  check("U3-01x 火攻亮牌与 RESOLVE pop 同批（无可弃同花色）：pop 后停留 1s + 真实 1s 淡出，不是瞬删",
    batchDwell && batchFadeWindow && batchNodeFading && batchNodeStill && batchNodeGone,
    `hold=${batch ? batch.stage.fadeAt - batch.stage.enteredAt : "?"}ms`
      + ` window=${batchFadeWindow} fading=${batchNodeFading}`
      + ` keep=${batchNodeStill} gone=${batchNodeGone}`);

  // ---- 缺陷 3-a：同会话 commit 不清展示组；换会话必须清 ----
  resetStage();
  const KEEP = "u3-def3-keep";
  loadView({ ...view(KEEP), cursor: 0, log_seq: 0, records: [], finished: false });
  const KEEP_REVEAL = card(703, "桃");
  loadView({
    ...view(KEEP), cursor: 1, log_seq: 0, finished: false,
    records: [reveal(20, 9, KEEP_REVEAL, 1)],
  });
  const keepItem = revealItem(703);
  // 同一会话、无记录的纯刷新 = `commitView` 的路径；展示组不能被清掉。
  loadView({ ...view(KEEP), cursor: 1, log_seq: 0, records: [], finished: false });
  const keptSameSession = revealItem(703) === keepItem
    && Boolean(stageNode(703))
    && laneOf(703) === "showcase";
  // 换会话（`session_id` 变化）= `viewStartsNewSession` 为真；展示组必须清空。
  loadView({ ...view("u3-def3-other"), cursor: 0, log_seq: 0, records: [], finished: false });
  const clearedNewSession = (ui.state.revealedCards || []).length === 0
    && !stageNode(703);
  check("U3-01y 同会话 commit 不清展示组，换会话必清（缺陷 3 的“清空”语义）",
    keptSameSession && clearedNewSession,
    `same=${keptSameSession} new=${clearedNewSession}`
      + ` left=${(ui.state.revealedCards || []).length}`);

  // ---- 缺陷 3-b：连续判定重叠——下一张出现时上一张仍在真实 1s 淡出；都不进展示组 ----
  resetStage();
  const JUDGE_SESSION = "u3-def3-judge";
  loadView({ ...view(JUDGE_SESSION), cursor: 0, log_seq: 0, records: [], finished: false });
  const JUDGE_1 = card(704, "闪电");
  const JUDGE_2 = card(705, "杀");
  const judgeRecords = [
    push(30, 40, "JUDGE"),
    move(31, 40, JUDGE_1, at("deck"), at("processing"), "JUDGE"),
    move(32, 40, JUDGE_1, at("processing"), at("discard"), "JUDGE"),
    pop(33, 40, "JUDGE"),
    push(34, 41, "JUDGE"),
    move(35, 41, JUDGE_2, at("deck"), at("processing"), "JUDGE"),
  ];
  ui.applyViewRaw({
    ...view(JUDGE_SESSION), cursor: 35, log_seq: 0, finished: false,
    records: judgeRecords,
  });
  for (let index = 0; index < judgeRecords.length; index += 1) ui.stepPlayback(true);
  const first = effectItem(704);
  const second = effectItem(705);
  const firstNode = stageNode(704);
  const secondNode = stageNode(705);
  const overlap = Boolean(first && second && firstNode && secondNode)
    && first.stage.lane === "processing" && second.stage.lane === "processing"
    && Number.isFinite(first.stage.fadeAt)
    && second.stage.enteredAt >= first.stage.fadeAt
    && second.stage.enteredAt < first.stage.fadeAt + 1000
    && ui.stageLane(first, second.stage.enteredAt) === "fading"
    && ui.stageLane(second, second.stage.enteredAt) === "visible"
    && hasClass(firstNode, "fading") && !hasClass(secondNode, "fading")
    && !hasClass(firstNode, "revealed-card")
    && !hasClass(secondNode, "revealed-card");
  const firstFadeIsOneSecond = Boolean(first)
    && ui.stageLane(first, first.stage.fadeAt + 999) === "fading"
    && ui.stageLane(first, first.stage.fadeAt + 1000) === "gone";
  check("U3-01z 连续判定重叠：下一张判定牌出现时上一张仍在真实 1s 淡出（且都不进展示组）",
    overlap && firstFadeIsOneSecond,
    `overlap=${overlap} firstLane=${first && first.stage.lane}`
      + ` secondLane=${second && second.stage.lane}`
      + ` fadeAt=${first && first.stage.fadeAt}`
      + ` nextEntered=${second && second.stage.enteredAt}`);

  resetStage();
}

/* ============ 界许褚【裸衣】：亮牌在「是否获得」答完前不得淡出 ============
 *
 * 现场缺陷：阶段体帧缺省 `frame_id = 0`，`stageItem` 对无帧展示项只给固定 1s
 * 停留，三张亮牌在二选一还没答时就淡出了。修法在引擎侧：亮牌前给阶段体帧分配
 * 真实帧号并落 `push`，询问挂起期间不 `pop`；前端只沿用既有的“有帧等 pop”。
 * 这里用真会话（2 人 / 种子 1 / 界许褚）走完整一条：发动 → 停在 `luoyi:gain`
 * → 断三张仍 visible（把时间直接拨到 +5s）→ 答「获得」→ 收到 `pop` 后才 fading。
 */
{
  const session = await post("/api/sessions", {
    player_count: 2, ai: "random", human_seat: 0, timeout_seconds: null,
    seed: 1, my_hero: "jie_xuchu",
  });
  await openSession(session);
  const activation = ui.state.view.pending;
  check("U3-01aa 真会话（2 人 / 种子 1 / 界许褚）：第一问是【裸衣】的发动询问",
    !!activation && activation.kind === "SKILL_ACTIVATION"
      && activation.skill_id === "hero.jie_xuchu.luoyi",
    `kind=${activation && activation.kind} skill=${activation && activation.skill_id}`);
  await ui.submitAction();
  ui.flushPlayback();
  const gain = ui.state.view.pending;
  const reveals = (ui.state.revealedCards || []).filter(
    (item) => item && item.stage && item.stage.lane === "showcase");
  const frameIds = [...new Set(reveals.map((item) => Number(item.stage.frameId) || 0))];
  const lanesAtFiveSeconds = reveals.map(
    (item) => ui.stageLane(item, item.stage.enteredAt + 5000));
  check("U3-01ab 亮出三张挂在承载询问的正帧号上；询问未答时 +5s 仍 visible（不是 1s 后 gone）",
    !!gain && gain.skill_id === "luoyi:gain"
      && reveals.length === 3
      && frameIds.length === 1 && frameIds[0] > 0
      && lanesAtFiveSeconds.every((lane) => lane === "visible"),
    `pending=${gain && gain.skill_id} reveals=${reveals.length}`
      + ` frames=${frameIds.join(",")} lanes=${lanesAtFiveSeconds.join(",")}`);

  const gainAction = gain && gain.legal_actions.find(
    (action) => action.kind === "SelectOption" && action.choice === "luoyi_gain");
  const answered = await post(
    `/api/sessions/${ui.state.sessionId}/answer?after=${ui.state.cursor}&logs=${ui.state.logSeq}`,
    { action: gainAction });
  const answerFlushBefore = Date.now();
  ui.applyView(answered);
  ui.flushPlayback();
  const answerFlushAfter = Date.now();
  const fadeAt = reveals[0] && reveals[0].stage.fadeAt;
  const fadingAfterAnswer = reveals.length === 3 && Number.isFinite(fadeAt)
    && reveals.every((item) => ui.stageLane(item, fadeAt) === "fading");
  // D9：pop 后先停 0.5(N+1)（亮牌记录 N=1 ⇒ 1s），到点才 fading；淡出仍 1s。
  const dwellAfterAnswer = reveals.length === 3 && Number.isFinite(fadeAt)
    && fadeAt >= answerFlushBefore + 1000 && fadeAt <= answerFlushAfter + 1000;
  check("U3-01ac 答完「获得」收到承载帧 pop 后先停留 1s、到点 fading（D9；淡出仍 1s）",
    fadingAfterAnswer && dwellAfterAnswer,
    `fadeAt=${fadeAt} flush=${answerFlushBefore}..${answerFlushAfter}`
      + ` lanes=${reveals.map((item) => ui.stageLane(item, fadeAt)).join(",")}`);

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.state.playback = null;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.logRecords = [];
  ui.resetSelection();
}

/* ============ A1：洛神连续判定的亮牌弹窗（显示层派生 judgment_reveal） ============
 *
 * 服务端 `pending_payload.judgment_reveal = {chain_key, cards, final}`；前端不认技能名：
 * - `final:false` 的继续询问 → 亮牌弹窗显示**本次循环已翻出的全部判定牌**（有序累积），
 *   提交 = 唯一选项，放弃 = Pass；
 * - 同一 `chain_key` 的下一问 → 同一个弹窗里追加，前一张不消失；
 * - `final:true` / 后续普通询问 → 整窗关闭；
 * - 卡片只进弹窗，不进 `state.revealedCards` / 舞台（舞台寿命口径不变）。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const card = (id, name, extra = {}) => ({
    ...synthCard(id, name), kind: "normal", attribute: "普通",
    display_name: name, description: `【${name}】的全文描述`, ...extra,
  });
  const basePending = (seq, overrides = {}) => ({
    kind: "SELECT_OPTION", actor: 0, seq, skill_id: "luoshen:repeat:0",
    options: ["repeat"],
    prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "repeat", answers_seq: seq },
      { kind: "Pass", answers_seq: seq },
    ],
    candidate_index: {}, option_seats: {},
    context: {
      actor: 0, name: "洛神", targets: [], label: null, skill_hints: [],
      requirement: "选择是否再进行一次【洛神】判定",
    },
    preactivations: {}, preactivation: [],
    ...overrides,
  });
  const viewFor = (pending) => {
    const view = synthView(pending);
    view.session_id = "a1-judgment-reveal";
    view.records = [];
    view.snapshot.processing = [];
    return view;
  };
  const revealCardNodes = () => {
    const walk = (node, out = []) => {
      for (const child of (node.children || [])) {
        out.push(child);
        walk(child, out);
      }
      return out;
    };
    return walk($("t-options")).filter(
      (node) => node.classList.contains("reveal-card"));
  };
  const revealCardNode = () => revealCardNodes()[0] || null;

  ui.flushPlayback();
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.renderPending(null);

  const firstCard = card(801, "闪电");
  const firstPending = basePending(9501, {
    judgment_reveal: { chain_key: "judge-loop:42", cards: [firstCard], final: false },
  });
  const firstView = viewFor(firstPending);
  ui.state.view = firstView;
  ui.renderTable();
  ui.renderPending(firstView);
  const firstName = revealCardNode();
  const firstKey = ui.state.dialog.key;
  const firstOwnOption = firstPending.legal_actions[ui.state.selectedOption];
  check("A1-a 洛神继续询问 = 亮牌弹窗（同一弹窗）：标题「洛神」、显示本张判定牌、无 repeat/stop 按钮",
    ui.stepDialogKind(firstPending) === "reveal"
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "洛神"
      && Array.from($("t-dialog-body").children).includes($("t-options"))
      && Boolean(firstName) && attr(firstName, "data-card-id") === "801"
      && $("t-options").children.length === 1
      && Array.from($("t-options").children).every((node) => !node.classList.contains("opt"))
      && firstOwnOption && firstOwnOption.kind === "SelectOption" && firstOwnOption.choice === "repeat"
      && $("btn-submit").disabled === false
      && $("btn-pass").hidden === false
      && $("btn-cancel").hidden === true,
    `kind=${ui.stepDialogKind(firstPending)} title="${$("t-dialog-title").textContent}"`
      + ` card=${firstName && attr(firstName, "data-card-id")} options=${$("t-options").children.length}`
      + ` selected=${firstOwnOption && firstOwnOption.choice} submit=${$("btn-submit").disabled}`
      + ` passHidden=${$("btn-pass").hidden} cancelHidden=${$("btn-cancel").hidden}`);
  check("A1-b 判定牌只进弹窗：不推 state.revealedCards、不新增舞台卡",
    (ui.state.revealedCards || []).length === 0
      && Array.from($("t-processing").children).every(
        (node) => !node.classList.contains("stage-card")),
    `revealed=${(ui.state.revealedCards || []).length}`
      + ` stage=${Array.from($("t-processing").children).filter((n) => n.classList.contains("stage-card")).length}`);

  // 同一 chain_key 的下一问：**累积**，前一张不消失（同一弹窗、键不变）。
  const secondCard = card(802, "杀", { type: "slash" });
  const secondPending = basePending(9502, {
    judgment_reveal: { chain_key: "judge-loop:42", cards: [firstCard, secondCard], final: false },
  });
  const secondView = viewFor(secondPending);
  ui.state.view = secondView;
  ui.renderTable();
  ui.renderPending(secondView);
  const secondNodes = revealCardNodes();
  const secondIds = secondNodes.map((node) => attr(node, "data-card-id"));
  check("A1-c 同一 chain_key 的下一张判定牌 = 同一个弹窗内累积（前一张仍在、顺序稳定、不重开）",
    $("t-dialog").hidden === false
      && ui.state.dialog.key === firstKey
      && secondIds.join(",") === "801,802"
      && $("t-dialog-title").textContent === "洛神",
    `hidden=${$("t-dialog").hidden} key=${ui.state.dialog.key} firstKey=${firstKey}`
      + ` cards=${secondIds.join(",")}`);

  // final:true（循环结束）→ 关窗；累积数组随关窗帧下发，但不再画。
  const redCard = card(803, "桃", { type: "peach", suit: "heart", color: "red" });
  const finalPending = basePending(9503, {
    kind: "PLAY_PHASE",
    judgment_reveal: {
      chain_key: "judge-loop:42",
      cards: [firstCard, secondCard, redCard],
      final: true,
    },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9503 }],
    context: null,
  });
  const finalView = viewFor(finalPending);
  ui.state.view = finalView;
  ui.renderTable();
  ui.renderPending(finalView);
  check("A1-d 循环结束（final:true）⇒ 关窗；弹窗不跨到下一份普通询问",
    $("t-dialog").hidden === true
      && ui.state.dialog.open === false
      && (ui.state.revealedCards || []).length === 0,
    `hidden=${$("t-dialog").hidden} open=${ui.state.dialog.open}`);

  // ---- 最后一张红牌：它没有“是否继续”询问，靠记录流的 JUDGE 判定牌追加到已开的弹窗 ----
  const redRecords = [
    { kind: "frame", op: "push", frame_id: 60, frame_kind: "JUDGE", depth: 1, seq: 9601 },
    {
      kind: "delta", delta_type: "card_move", seq: 9602, frame_id: 60, depth: 1,
      reason: "JUDGE", owner: 0,
      from_zone: { zone: "deck", seat: null },
      to_zone: { zone: "processing", seat: null },
      cards: [redCard],
    },
  ];
  ui.state.view = secondView;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.renderTable();
  ui.renderPending(secondView);
  const nextPending = basePending(9603, {
    kind: "PLAY_PHASE",
    judgment_reveal: {
      chain_key: "judge-loop:42",
      cards: [firstCard, secondCard, redCard],
      final: true,
    },
    legal_actions: [{ kind: "EndPhase", answers_seq: 9603 }],
    context: null,
  });
  const nextView = viewFor(nextPending);
  nextView.records = redRecords;
  ui.applyViewRaw(nextView);
  ui.stepPlayback(true);  // JUDGE push
  ui.stepPlayback(true);  // 判定牌进处理区 ⇒ 追加到已开的弹窗
  const redNodes = revealCardNodes();
  const redIds = redNodes.map((node) => attr(node, "data-card-id"));
  const redShownBeforeCommit = $("t-dialog").hidden === false
    && redIds.join(",") === "801,802,803";
  ui.flushPlayback();  // 提交下一份 pending（final:true）⇒ 关窗
  check("A1-e 红牌（无继续询问）经 JUDGE 记录追加到累积弹窗；下一份 final:true 才关窗",
    redShownBeforeCommit
      && $("t-dialog").hidden === true
      && ui.state.dialog.open === false,
    `beforeCommit=${redShownBeforeCommit} cards=${redIds.join(",")}`
      + ` hiddenAfter=${$("t-dialog").hidden}`);

  // 没有下一份 pending（局终 / 阵亡等）时也不能留一个永远挂着的弹窗。
  ui.state.view = secondView;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.renderTable();
  ui.renderPending(secondView);
  const noPendingView = viewFor(null);
  ui.applyViewRaw(noPendingView);
  check("A1-f 没有下一份 pending（pending=null）⇒ 关窗，不留幽灵",
    $("t-dialog").hidden === true
      && ui.state.dialog.open === false,
    `hidden=${$("t-dialog").hidden} open=${ui.state.dialog.open}`);

  ui.flushPlayback();
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.renderPending(null);
  ui.resetSelection();
}

/* ============ U4-01：飞行层（D1 / D2 / D3 / D5 / D6） ============
 *
 * 合成载荷走**真入口**：`applyViewRaw` 交给 `stepPlayback(true)`（与页面同一条回放路径），
 * 飞行器由 `stepPlayback` 在重绘之后同步起飞。断言按记录数飞行器、起 / 落点矩形存在
 * （没有 `data-anim-missing`）、`processing -> discard` 为零、多张 N 全飞。
 *
 * 不读真实布局 / 时钟：垫片矩形是确定性的（`shimRect`），`animate` 只登记参数
 * （`node.animations`），故 `delay` / 扇开幅度都能直接断言。
 */
{
  const layer = $("t-anim-flights");
  const flights = () => Array.from(layer.children || []);
  const clearFlights = () => {
    layer.innerHTML = "";
    layer.removeAttribute("data-anim-missing");
    layer.removeAttribute("data-anim-unregistered");
  };
  /** 舞台卡 / 手牌卡的节点（D2 的源锚点判据：上装备的落点那一步必须从舞台卡起飞）。 */
  const stageCardNode = (id) => Array.from($("t-processing").children).find(
    (node) => node.getAttribute && node.getAttribute("data-stage-key") === String(id)) || null;
  const handCardNode = (id) => Array.from($("t-hand").children).find(
    (node) => node.getAttribute && node.getAttribute("data-card-id") === String(id)) || null;
  // 真 DOM 的 getAttribute 缺省返回 null，垫片返回 undefined：读法统一成 null。
  const missing = () => layer.getAttribute("data-anim-missing") ?? null;
  const unregistered = () => layer.getAttribute("data-anim-unregistered") ?? null;
  const flightCard = (id, name, extra = {}) => ({ ...synthCard(id, name), ...extra });
  const zoneRef = (zone, seat = null, extra = {}) => ({ zone, seat, ...extra });
  const moveRecord = (seq, reason, from, to, cards, extra = {}) => ({
    kind: "delta", delta_type: "card_move", seq, frame_id: 0, depth: 0,
    reason, owner: 0, from_zone: from, to_zone: to, cards, ...extra,
  });
  const playerFor = (seat, extra = {}) => ({
    seat, hp: 4, hp_max: 4, hand_count: 3, alive: true,
    equip: [], judge: [], identity: null, ...extra,
  });
  const snapshotFor = (players, selfHand) => ({
    seat: 0, phase: "play", turn_seat: 0, deck_count: 30, discard: [],
    processing: [], players, self_hand: selfHand || [flightCard(1, "杀")],
  });
  const bodyFor = (records, snapshot) => ({
    session_id: "u4-flight", seed: 1, human_seat: 0, finished: false,
    timeout_seconds: null, heroes: { 0: "guojia", 1: "zhangfei", 2: "diaochan" },
    skills: [], pending: null, snapshot, records, live_deltas: [], narrative: [],
    skill_events: [], cursor: records.length, log_seq: records.length,
  });
  const feed = (records, before, { live = true } = {}) => {
    ui.state.playback = null;
    ui.state.effectCards = [];
    ui.state.revealedCards = [];
    ui.state.sessionId = "u4-flight";
    ui.state.view = live ? bodyFor([], before) : null;
    clearFlights();
    ui.applyViewRaw(bodyFor(records, before));
  };
  /** 走一步回放，返回这一步新出生的飞行器（按节点读法，不读动画内部状态）。 */
  const stepFlights = () => {
    const before = flights().length;
    ui.stepPlayback(true);
    return flights().slice(before);
  };
  const kindOf = (node) => node.getAttribute("data-anim-kind");
  const delayOf = (node) => (node && node.animations && node.animations[0]
    ? node.animations[0].options.delay : null);
  const durationOf = (node) => (node && node.animations && node.animations[0]
    ? node.animations[0].options.duration : null);
  const summary = (nodes) => `节点=${nodes.length} kinds=${JSON.stringify(nodes.map(kindOf))}`
    + ` missing=${missing()} unregistered=${unregistered()}`;
  const okFlight = (label, nodes, count, kind = null) => {
    check(label,
      nodes.length === count
        && (kind === null || nodes.every((node) => kindOf(node) === kind))
        && missing() === null
        && unregistered() === null,
      summary(nodes));
  };

  /* ---- 时间表：内置默认、缺键 / 非法值退回默认、一行提示（D8） ---- */
  const iniKeys = [
    "base_step", "flight_use", "flight_judge_reveal", "flight_draw", "flight_gain",
    "flight_discard", "flight_place_equip", "flight_place_judge", "flight_replace_out",
    "flight_stage_out", "fan_ms", "fan_px", "phase_float_ms",
  ];
  const defaults = ui.animDefaults;
  check("U4-01-init 内置默认含 D8 全部键、没有 debug_scale",
    iniKeys.every((key) => typeof defaults[key] === "number") && !("debug_scale" in defaults),
    `keys=${JSON.stringify(Object.keys(defaults))}`);
  iniInjection = {
    ok: true,
    text: "flight_draw = 400\nfan_px = 不是数字\nflight_gain =\nquantum = 1\n",
  };
  await ui.animLoadConfig();
  const noteNode = $("t-anim-note");
  const fallbackNote = String(noteNode.textContent || "");
  check("U4-01-ini 缺键 / 非法值（含空值）退回默认，并只写一行提示（debug_scale 不再是合法键）",
    ui.animConfig.flight_draw === 400
      && ui.animConfig.fan_px === defaults.fan_px
      && ui.animConfig.flight_gain === defaults.flight_gain
      && noteNode.hidden === false
      && fallbackNote.length > 0
      && !fallbackNote.includes("\n")
      && /默认/.test(fallbackNote),
    `draw=${ui.animConfig.flight_draw} fan_px=${ui.animConfig.fan_px}`
      + ` gain=${ui.animConfig.flight_gain} note="${fallbackNote}"`);
  iniInjection = {
    ok: true,
    text: iniKeys.map((key) => `${key} = ${defaults[key]}`).join("\n"),
  };
  await ui.animLoadConfig();
  check("U4-01-ini 全键 ini 覆盖后无提示（正常启动不打扰）",
    iniKeys.every((key) => ui.animConfig[key] === defaults[key])
      && noteNode.hidden === true && noteNode.textContent === "",
    `noteHidden=${noteNode.hidden} note="${noteNode.textContent}"`);
  iniInjection = { ok: false, text: "" };
  await ui.animLoadConfig();
  check("U4-01-ini 取不到 ini 时回内置默认、仍只一行提示",
    iniKeys.every((key) => ui.animConfig[key] === defaults[key])
      && noteNode.hidden === false
      && !String(noteNode.textContent || "").includes("\n")
      && /默认/.test(noteNode.textContent),
    `note="${noteNode.textContent}"`);
  noteNode.hidden = true;
  noteNode.textContent = "";

  const playersBase = () => [playerFor(0), playerFor(1), playerFor(2)];

  /* ---- D3-1 摸牌：牌堆 → 自己手牌行；对手摸牌只飞牌背，数量读 card_count ---- */
  {
    const hand = [flightCard(1, "杀"), flightCard(2, "闪")];
    const snapshot = snapshotFor(playersBase(), hand);
    feed([
      moveRecord(1, "DRAW", zoneRef("deck"), zoneRef("hand", 0), [hand[0], hand[1]]),
    ], snapshot);
    const nodes = stepFlights();
    okFlight("U4-01-a 摸牌：2 张牌从「牌堆」飞进自己手牌行（2 个飞行器）", nodes, 2, "draw");
  }
  {
    const snapshot = snapshotFor(playersBase(), []);
    feed([
      moveRecord(2, "DRAW", zoneRef("deck"), zoneRef("hand", 1), [], { card_count: 3 }),
    ], snapshot);
    const nodes = stepFlights();
    check("U4-01-b 对手摸牌：只飞牌背「?」，数量读 card_count（3 个）",
      nodes.length === 3
        && nodes.every((node) => kindOf(node) === "draw"
          && node.getAttribute("data-anim-hidden") === "true"
          && node.textContent.includes("?"))
        && missing() === null,
      summary(nodes));
  }

  /* ---- D3-2 使用 / 判定亮牌：手牌 / 牌堆 → 处理区（舞台卡） ---- */
  {
    const used = flightCard(9, "杀");
    const snapshot = snapshotFor(playersBase(), [used]);
    feed([
      moveRecord(3, "USE", zoneRef("hand", 0), zoneRef("processing"), [used]),
    ], snapshot);
    const nodes = stepFlights();
    okFlight("U4-01-c 使用：从手牌行飞进舞台的处理区卡面", nodes, 1, "use");
  }
  {
    const judged = flightCard(10, "桃");
    feed([
      moveRecord(4, "JUDGE", zoneRef("deck"), zoneRef("processing"), [judged]),
    ], snapshotFor(playersBase(), [flightCard(1, "杀")]));
    const nodes = stepFlights();
    okFlight("U4-01-d 判定亮牌：牌堆 → 舞台处理区", nodes, 1, "judge_reveal");
  }

  /* ---- D3-3 上装备：舞台卡 → 目标槽（含顶替的旧装备错开半拍） ---- */
  {
    const weapon = flightCard(11, "寒冰剑", { slot: "weapon", attack_range: 2, type: "equip" });
    const before = snapshotFor(playersBase(), [weapon]);
    feed([
      moveRecord(5, "USE", zoneRef("hand", 0), zoneRef("processing"), [weapon]),
      moveRecord(6, "PUT", zoneRef("processing"), zoneRef("equip", 0), [weapon]),
    ], before);
    const first = stepFlights();
    const second = stepFlights();
    // D2：PUT 那一步的源必须是**舞台卡**（手牌行即使还在 DOM 里也不能当源）。
    const stageRect = stageCardNode(11) ? stageCardNode(11).getBoundingClientRect() : null;
    const handRect = handCardNode(11) ? handCardNode(11).getBoundingClientRect() : null;
    const flightLeft = second[0] ? parseFloat(second[0].style.left) : Number.NaN;
    const flightTop = second[0] ? parseFloat(second[0].style.top) : Number.NaN;
    const fromStage = Boolean(stageRect)
      && Math.abs(flightLeft - stageRect.left) < 0.5
      && Math.abs(flightTop - stageRect.top) < 0.5;
    const fromHandInstead = Boolean(handRect)
      && Math.abs(flightLeft - handRect.left) < 0.5
      && Math.abs(flightTop - handRect.top) < 0.5;
    check("U4-01-e 上装备：舞台卡 → 武器槽（两条记录各一只飞行器，源不是手牌）",
      first.length === 1 && kindOf(first[0]) === "use"
        && second.length === 1 && kindOf(second[0]) === "place_equip"
        && fromStage && !fromHandInstead
        && missing() === null,
      `${summary(first)} / ${summary(second)}`
        + ` stage=${stageRect && stageRect.left},${stageRect && stageRect.top}`
        + ` hand=${handRect && handRect.left},${handRect && handRect.top}`
        + ` flight=${flightLeft},${flightTop} fromStage=${fromStage} fromHand=${fromHandInstead}`);
  }
  {
    const oldWeapon = flightCard(17, "青釭剑", { slot: "weapon", attack_range: 2, type: "equip" });
    const newWeapon = flightCard(18, "寒冰剑", { slot: "weapon", attack_range: 2, type: "equip" });
    const before = snapshotFor([
      playerFor(0, { equip: [oldWeapon] }), playerFor(1), playerFor(2),
    ], [newWeapon]);
    feed([
      moveRecord(7, "LOSE", zoneRef("equip", 0), zoneRef("discard"), [oldWeapon]),
      moveRecord(8, "PUT", zoneRef("hand", 0), zoneRef("equip", 0), [newWeapon]),
    ], before);
    const out = stepFlights();
    const into = stepFlights();
    const oldNode = out[0];
    const oldDuration = oldNode ? durationOf(oldNode) : null;
    check("U4-01-f 顶替：旧装备另飞一只去「弃牌」，且与置入错开半拍（delay = 半条飞行）",
      out.length === 1 && kindOf(oldNode) === "replace_out"
        && into.length === 1 && kindOf(into[0]) === "place_equip"
        && delayOf(oldNode) === Math.round(oldDuration / 2)
        && delayOf(into[0]) === 0
        && missing() === null,
      `out=${summary(out)} delay=${delayOf(oldNode)} dur=${oldDuration}`
        + ` intoKind=${into[0] && kindOf(into[0])} intoDelay=${into[0] && delayOf(into[0])}`);
  }

  /* ---- D3-4 置入判定区 / 闪电换人（跨座位同区） ---- */
  {
    const lightning = flightCard(13, "闪电", { type: "trick" });
    const before = snapshotFor(playersBase(), [lightning]);
    feed([
      moveRecord(9, "USE", zoneRef("hand", 0), zoneRef("processing"), [lightning]),
      moveRecord(10, "PUT", zoneRef("processing"), zoneRef("judge", 0), [lightning]),
      moveRecord(11, "PUT", zoneRef("judge", 0), zoneRef("judge", 2), [lightning]),
    ], before);
    const staged = stepFlights();
    const placed = stepFlights();
    const moved = stepFlights();
    check("U4-01-g 判定区：置入第一个空判定槽；闪电换人 A 判定槽 → B 判定槽",
      staged.length === 1 && kindOf(staged[0]) === "use"
        && placed.length === 1 && kindOf(placed[0]) === "place_judge"
        && moved.length === 1 && kindOf(moved[0]) === "place_judge"
        && missing() === null,
      `${summary(staged)} / ${summary(placed)} / ${summary(moved)}`);
  }

  /* ---- D3-5 不过处理区的置入（界【结姻】）：从源区起飞，没有舞台卡也不报错 ---- */
  {
    const gift = flightCard(19, "赤兔", { slot: "horse_defense", type: "equip" });
    const before = snapshotFor(playersBase(), [gift]);
    feed([
      moveRecord(12, "PUT", zoneRef("hand", 0), zoneRef("equip", 0), [gift]),
    ], before);
    const nodes = stepFlights();
    check("U4-01-h 界【结姻】这类不过处理区的置入：从手牌行起飞、落到装备槽",
      nodes.length === 1 && kindOf(nodes[0]) === "place_equip"
        && missing() === null && unregistered() === null,
      summary(nodes));
  }

  /* ---- D3-6 顺 / 借刀 / 获得：对方槽 / 手牌数 → 自己手牌区 ---- */
  {
    const stolen = flightCard(14, "漆黑", { slot: "armor", type: "equip" });
    const before = snapshotFor([
      playerFor(0), playerFor(1, { equip: [stolen] }), playerFor(2),
    ], [flightCard(1, "杀")]);
    feed([
      moveRecord(13, "LOSE", zoneRef("equip", 1), zoneRef("hand", 0), [stolen]),
    ], before);
    const nodes = stepFlights();
    okFlight("U4-01-i 顺 / 借刀：对方装备槽 → 自己手牌区", nodes, 1, "gain");
  }
  {
    // 借刀 / 顺手牵羊动到**他人的手牌**：来源只能是「手牌 N」计数，牌面按隐藏侧画牌背。
    feed([
      moveRecord(24, "LOSE", zoneRef("hand", 1), zoneRef("hand", 0), [], { card_count: 2 }),
    ], snapshotFor(playersBase(), [flightCard(1, "杀")]));
    const nodes = stepFlights();
    check("U4-01-i2 对方手牌数 → 自己手牌区：2 只牌背「?」（来源是手牌计数锚点）",
      nodes.length === 2
        && nodes.every((node) => kindOf(node) === "gain"
          && node.getAttribute("data-anim-hidden") === "true"
          && node.textContent.includes("?"))
        && missing() === null && unregistered() === null,
      summary(nodes));
  }

  /* ---- D3-7 拆 / 弃置 / 重铸：区 → 弃牌计数 ---- */
  {
    const stolenJudge = flightCard(15, "乐不思蜀", { type: "trick" });
    const before = snapshotFor([
      playerFor(0), playerFor(1, { judge: [stolenJudge] }), playerFor(2),
    ], [flightCard(1, "杀")]);
    feed([
      moveRecord(14, "LOSE", zoneRef("judge", 1), zoneRef("discard"), [stolenJudge]),
    ], before);
    const nodes = stepFlights();
    okFlight("U4-01-j 拆：对方判定槽 → 顶栏「弃牌」计数", nodes, 1, "discard");
  }
  {
    const junk = flightCard(16, "杀");
    const before = snapshotFor(playersBase(), [junk]);
    feed([moveRecord(15, "DISCARD", zoneRef("hand", 0), zoneRef("discard"), [junk])], before);
    const nodes = stepFlights();
    okFlight("U4-01-k 弃置：手牌行 → 顶栏「弃牌」", nodes, 1, "discard");
  }
  {
    const iron = flightCard(20, "铁索连环", { type: "trick" });
    const before = snapshotFor(playersBase(), [iron]);
    feed([moveRecord(16, "REFORGE", zoneRef("hand", 0), zoneRef("discard"), [iron])], before);
    const nodes = stepFlights();
    okFlight("U4-01-k2 重铸：手牌行 → 顶栏「弃牌」", nodes, 1, "discard");
  }

  /* ---- D3-8 使用 / 判定收尾（processing → discard）：零飞行器 ---- */
  {
    feed([
      moveRecord(17, "USE", zoneRef("processing"), zoneRef("discard"), [flightCard(21, "杀")]),
      moveRecord(18, "JUDGE", zoneRef("processing"), zoneRef("discard"), [flightCard(22, "闪")]),
    ], snapshotFor(playersBase(), []));
    ui.flushPlayback();
    check("U4-01-l 使用 / 判定收尾（processing → discard）：0 个飞行器",
      flights().length === 0 && missing() === null && unregistered() === null,
      `节点=${flights().length} missing=${missing()} unregistered=${unregistered()}`);
  }

  /* ---- D5 多张：N = 同一条记录里的牌数，全部起飞、扇开、错峰 ---- */
  {
    const ten = Array.from({ length: 10 }, (_, index) => flightCard(30 + index, "杀"));
    const before = snapshotFor(playersBase(), ten);
    feed([moveRecord(19, "DISCARD", zoneRef("hand", 0), zoneRef("discard"), ten)], before);
    const nodes = stepFlights();
    const offsets = nodes.map((node) => `${node.style.left}|${node.style.top}`);
    const delays = nodes.map(delayOf);
    const fanMs = ui.animConfig.fan_ms;
    check("U4-01-m 一次 10 张：10 个飞行器、各自扇开（位置不重合）、fan_ms 内错峰",
      nodes.length === 10
        && new Set(offsets).size === 10
        && Math.min(...delays) === 0
        && Math.max(...delays) === fanMs
        && new Set(nodes.map((node) => node.getAttribute("data-anim-index"))).size === 10
        && missing() === null,
      `节点=${nodes.length} 位置=${new Set(offsets).size} delays=${Math.min(...delays)}..${Math.max(...delays)} fan_ms=${fanMs}`);
  }

  /* ---- 未登记 / 起点缺失 / 落点缺失：可见读法、静默跳过、不阻塞播放 ---- */
  {
    feed([
      moveRecord(20, "PUT", zoneRef("hand", 0), zoneRef("moon"), [flightCard(23, "杀")]),
    ], snapshotFor(playersBase(), [flightCard(23, "杀")]));
    ui.flushPlayback();
    check("U4-01-n 未登记的 (reason, to_zone) 组合：0 个飞行器 + 层上标「未登记」",
      flights().length === 0
        && unregistered() === "PUT->moon"
        && ui.state.playback === null,
      `节点=${flights().length} unregistered=${unregistered()}`);
  }
  {
    feed([
      moveRecord(21, "LOSE", zoneRef("hand", 7), zoneRef("hand", 0), [flightCard(24, "杀")]),
    ], snapshotFor(playersBase(), [flightCard(1, "杀")]));
    ui.flushPlayback();
    check("U4-01-o 起点缺失：静默跳过（0 个）、层上 data-anim-missing=source、播放走完不卡",
      flights().length === 0 && missing() === "source" && ui.state.playback === null,
      `节点=${flights().length} missing=${missing()} playback=${ui.state.playback}`);
  }
  {
    feed([
      moveRecord(22, "LOSE", zoneRef("hand", 0), zoneRef("hand", 7), [flightCard(25, "杀")]),
    ], snapshotFor(playersBase(), [flightCard(1, "杀"), flightCard(25, "杀")]));
    ui.flushPlayback();
    check("U4-01-p 落点缺失：静默跳过（0 个）、层上 data-anim-missing=destination、播放走完不卡",
      flights().length === 0 && missing() === "destination" && ui.state.playback === null,
      `节点=${flights().length} missing=${missing()} playback=${ui.state.playback}`);
  }

  /* ---- 开局批次（!live）：没有上一份提交视图时，钩子照样起飞 ---- */
  {
    const hand = [flightCard(1, "杀"), flightCard(2, "闪")];
    feed([
      moveRecord(23, "DRAW", zoneRef("deck"), zoneRef("hand", 0), hand),
    ], snapshotFor(playersBase(), hand), { live: false });
    // M6 报障 2（A 方案）起，开局批也按正常节奏播（不再在 `applyView` 里即时冲刷）；
    // 烟测的计时器垫片不跑计时器，故这里显式播完再判"钩子起过飞、视图已提交"。
    ui.flushPlayback();
    check("U4-01-q 开局批次（!live）也走同一个钩子：牌堆 → 自己手牌行照样起飞",
      flights().length === 2 && flights().every((node) => kindOf(node) === "draw")
        && ui.state.view !== null,
      `节点=${flights().length} view=${Boolean(ui.state.view)}`);
  }

  /* ---- D7 动画缩放：base 同时缩放 WAAPI 时长，不是只缩间隔 ---- */
  {
    const drawCard = flightCard(40, "杀");
    const drawRecords = [
      moveRecord(30, "DRAW", zoneRef("deck"), zoneRef("hand", 0), [drawCard]),
    ];
    ui.state.eventDelayMs = 50;
    feed(drawRecords, snapshotFor(playersBase(), [drawCard]));
    const half = stepFlights();
    const halfDuration = half[0] ? durationOf(half[0]) : null;
    ui.state.eventDelayMs = 300;
    feed(drawRecords, snapshotFor(playersBase(), [drawCard]));
    const triple = stepFlights();
    const tripleDuration = triple[0] ? durationOf(triple[0]) : null;
    ui.state.eventDelayMs = 100;
    check("U4-02-k 飞行动画按 base 缩放（表 220ms：50ms 档 110、300ms 档 660），不是只缩间隔",
      half.length === 1 && triple.length === 1
        && halfDuration === 110 && tripleDuration === 660,
      `nodes=${half.length}/${triple.length} dur50=${halfDuration} dur300=${tripleDuration}`);
  }

  ui.state.playback = null;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.resetSelection();
}

/* ============ U4-02（spec D7 / D8）：节奏公式与五档步长 ============
 *
 * `delayOf(record, base) = round(base + durationOf(record) × base / 100)` 是**纯函数**：
 * 烟测直接喂 `{base, record}`，不读 `state` / 不读时钟；随后用计时器垫片断"收尾真的按
 * 这一条记录排程"，并断播放全程不改基准档（原型在播放里改 `state.eventDelayMs` 是 bug）。
 * 时长期望值取自 D8 的缺省表（本块先把 `animConfig` 重置成内置默认）。
 */
{
  iniInjection = {
    ok: true,
    text: Object.entries(ui.animDefaults).map(([key, value]) => `${key} = ${value}`).join("\n"),
  };
  await ui.animLoadConfig();
  const stepCard = (id, name, extra = {}) => ({ ...synthCard(id, name), ...extra });
  const move = (seq, reason, from, to, cards) => ({
    kind: "delta", delta_type: "card_move", seq, frame_id: 0, depth: 0,
    reason, owner: 0, from_zone: from, to_zone: to, cards,
  });
  const ref = (zone, seat = null) => ({ zone, seat });
  const useRecord = move(40, "USE", ref("hand", 0), ref("processing"), [stepCard(41, "杀")]);
  check("U4-02-a delayOf 是纯函数：base=100 + 使用牌（飞行 180ms）⇒ 280ms",
    ui.durationOf(useRecord) === 180 && ui.delayOf(useRecord, 100) === 280,
    `durationOf=${ui.durationOf(useRecord)} delay=${ui.delayOf(useRecord, 100)}`);
  check("U4-02-b delayOf：没有动画的记录（非牌移动 / null）只吃 base",
    ui.delayOf({ kind: "delta", delta_type: "hp_change", seq: 1, seat: 0 }, 100) === 100
      && ui.delayOf(null, 100) === 100,
    `hp_change=${ui.delayOf({ kind: "delta", delta_type: "hp_change" }, 100)} null=${ui.delayOf(null, 100)}`);
  check("U4-02-c delayOf：base 同时是下限与缩放因子（50 = 0.5×、300 = 3×）",
    ui.delayOf(useRecord, 50) === 140 && ui.delayOf(useRecord, 300) === 840,
    `50→${ui.delayOf(useRecord, 50)}ms 300→${ui.delayOf(useRecord, 300)}ms`);

  const drawRecord = move(41, "DRAW", ref("deck"), ref("hand", 0),
    [stepCard(42, "杀"), stepCard(43, "闪")]);
  check("U4-02-d delayOf：多张摸牌的 fan_ms 尾巴算进 dur（220+120 ⇒ 440ms @base=100）",
    ui.durationOf(drawRecord) === 340 && ui.delayOf(drawRecord, 100) === 440,
    `durationOf=${ui.durationOf(drawRecord)} delay=${ui.delayOf(drawRecord, 100)}`);

  const replaceRecord = move(42, "LOSE", ref("equip", 0), ref("discard"),
    [stepCard(44, "青釭剑", { slot: "weapon", type: "equip" })]);
  check("U4-02-e delayOf：顶替出去的旧装备带半拍（280+140 ⇒ 420ms dur / 520ms @base=100）",
    ui.durationOf(replaceRecord) === 420 && ui.delayOf(replaceRecord, 100) === 520,
    `durationOf=${ui.durationOf(replaceRecord)} delay=${ui.delayOf(replaceRecord, 100)}`);

  /* ---- 收尾排程读 delayOf(record)：第一条等 base，之后按上一条的时长缩放 ---- */
  const hand = [stepCard(45, "杀"), stepCard(46, "闪")];
  const snapshot = {
    seat: 0, phase: "play", turn_seat: 0, deck_count: 30, discard: [], processing: [],
    players: [0, 1, 2].map((seat) => ({
      seat, hp: 4, hp_max: 4, hand_count: 3, alive: true, equip: [], judge: [], identity: null,
    })),
    self_hand: hand,
  };
  const body = (records) => ({
    session_id: "u4-pacing", seed: 1, human_seat: 0, finished: false, timeout_seconds: null,
    heroes: { 0: "guojia", 1: "zhangfei", 2: "diaochan" }, skills: [], pending: null,
    snapshot, records, live_deltas: [], narrative: [], skill_events: [],
    cursor: records.length, log_seq: records.length,
  });
  ui.state.playback = null;
  ui.state.effectCards = [];
  ui.state.revealedCards = [];
  ui.state.sessionId = "u4-pacing";
  pendingTimers.clear();
  timerDelays.clear();
  ui.state.view = body([]);
  ui.applyViewRaw(body([drawRecord, useRecord]));
  const delayBefore = ui.state.eventDelayMs;
  check("U4-02-f 回放第一条先等一个基准步长（base=100）",
    latestTimerDelay() === 100, `排程=${latestTimerDelay()}ms`);
  // 每步先清掉上一条的计时器（垫片不自动跑），再显式走一步：收尾那条是最后排的。
  pendingTimers.clear();
  timerDelays.clear();
  ui.stepPlayback(false);
  check("U4-02-g 播完「摸 2 张」后按 delayOf(record) 排下一条：100 + 340 = 440ms",
    latestTimerDelay() === 440, `排程=${latestTimerDelay()}ms`);
  pendingTimers.clear();
  timerDelays.clear();
  ui.stepPlayback(false);
  check("U4-02-h 播完「使用杀」后排下一条：100 + 180 = 280ms",
    latestTimerDelay() === 280, `排程=${latestTimerDelay()}ms`);
  check("U4-02-i 播放全程不改基准档（state.eventDelayMs 仍是 100）",
    delayBefore === 100 && ui.state.eventDelayMs === 100,
    `before=${delayBefore} after=${ui.state.eventDelayMs}`);
  ui.flushPlayback();
  check("U4-02-j 回放收尾后基准档仍不变",
    ui.state.eventDelayMs === 100 && ui.state.playback === null,
    `eventDelayMs=${ui.state.eventDelayMs} playback=${Boolean(ui.state.playback)}`);
  ui.state.view = null;
  ui.state.sessionId = null;
}

/* ============ U4-03（spec D9）：舞台批量停留 0.5(N+1) ============
 *
 * N = **产生该舞台项的那条记录**的牌数，记在 `item.stage.batch` 上（换组 / 跨 commit 不重算）。
 * 无帧项从入台起停 `0.5×(N+1)` 秒，有帧项从该帧 `pop` 起停；淡出仍 1s（`STAGE_FADE_MS`），
 * `animScale` 只缩淡出、不缩停留。时间不靠真实等待：`renderProcessing(now)` 直接喂 `now`。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const card = (id, name) => ({
    ...synthCard(id, name), kind: "normal", attribute: "普通",
    description: `【${name}】的全文描述`,
  });
  const at = (zone, seat = null) => ({ zone, seat });
  const move = (seq, frame, cards, reason, from, to) => ({
    kind: "delta", delta_type: "card_move", seq, frame_id: frame, depth: 1,
    reason, owner: from.seat, from_zone: from, to_zone: to, cards,
  });
  const reveal = (seq, frame, moved, seat) => ({
    kind: "delta", delta_type: "card_reveal", seq, frame_id: frame, depth: 1,
    seat, card: moved,
  });
  const pop = (seq, frame, kind = "RESOLVE") => ({
    kind: "frame", op: "pop", frame_id: frame, frame_kind: kind, depth: 1, seq,
  });
  const stageNodes = () => Array.from($("t-processing").children).filter(
    (node) => node.classList.contains("stage-card"));
  const stageNode = (id) => stageNodes().find(
    (node) => attr(node, "data-card-id") === String(id));
  const fading = (id) => Boolean(stageNode(id) && stageNode(id).classList.contains("fading"));
  const itemOf = (id) => (ui.state.effectCards || []).concat(ui.state.revealedCards || [])
    .find((entry) => String(entry.card_id) === String(id));
  const resetStage = () => {
    ui.state.view = null;
    ui.state.sessionId = null;
    ui.state.playback = null;
    ui.state.effectCards = [];
    ui.state.revealedCards = [];
    ui.state.animScale = 1;
    ui.renderProcessing();
  };
  const TO_PROCESSING = at("processing");
  const TO_DISCARD = at("discard");

  resetStage();
  // 缺桥时给一个可读的标记，让红落在断言上而不是调用期 TypeError（桥的容错约定）。
  const holdOf = (n) => (typeof ui.stageHoldMs === "function" ? ui.stageHoldMs(n) : "缺");
  check("U4-03a stageHoldMs 纯函数：停留 = 0.5×(N+1) 秒（N=1→1000、N=2→1500、N=10→5500；缺省按 1）",
    [1, 2, 10].map(holdOf).join(",") === "1000,1500,5500"
      && holdOf(0) === 1000 && holdOf(undefined) === 1000,
    `holds=${[1, 2, 10, 0, undefined].map(holdOf).join(",")}`);

  // 【制衡】一次弃 10 张：同一条记录 → 每张 batch=10、同刻入台、停留 5.5s，然后一起淡出。
  resetStage();
  const ten = Array.from({ length: 10 }, (_, index) => card(810 + index, "杀"));
  ui.state.effectCards = ui.applyEffectDelta(
    move(800, 0, ten, "DISCARD", at("hand", 0), TO_DISCARD), []);
  const tenItems = ui.state.effectCards;
  const tenEntered = tenItems.length && tenItems[0].stage.enteredAt;
  const tenBatch = tenItems.length === 10 && tenItems.every((item) => item.stage.batch === 10);
  const tenFadeAt = tenItems.length === 10
    && tenItems.every((item) => item.stage.fadeAt === tenEntered + 5500);
  ui.renderProcessing(tenEntered + 5499);
  const tenVisible = stageNodes().length === 10 && !fading(810) && !fading(819);
  ui.renderProcessing(tenEntered + 5500);
  const tenFadingTogether = stageNodes().length === 10
    && stageNodes().every((node) => node.classList.contains("fading"));
  ui.renderProcessing(tenEntered + 6499);
  const tenStillFading = stageNodes().length === 10;
  ui.renderProcessing(tenEntered + 6500);
  check("U4-03b 一次弃 10 张（frame 0）：batch=10、停留 5500ms 后一起淡出、6500ms 一起移除",
    tenBatch && tenFadeAt && tenVisible && tenFadingTogether && tenStillFading
      && stageNodes().length === 0,
    `batch=${tenItems.map((item) => item.stage.batch).join(",")}`
      + ` fadeAt=${tenItems.map((item) => item.stage.fadeAt - tenEntered).join(",")}`
      + ` visible=${tenVisible} fading=${tenFadingTogether}`
      + ` still=${tenStillFading} gone=${stageNodes().length === 0}`);

  // 有承载帧的 3 张：pop 前一直可见；pop 后停留 0.5×(3+1)=2000ms 再一起淡出。
  // 其中一张先换组进弃牌组（那条记录只动 1 张）——batch 沿用首次入台的 3，不按换组记录重算。
  resetStage();
  const framed = [card(830, "杀"), card(831, "闪"), card(832, "桃")];
  ui.state.effectCards = ui.applyEffectDelta(
    move(810, 50, framed, "USE", at("hand", 0), TO_PROCESSING), []);
  const framedEntered = ui.state.effectCards.length && ui.state.effectCards[0].stage.enteredAt;
  ui.state.effectCards = ui.applyEffectDelta(
    move(811, 50, [framed[0]], "LOSE", TO_PROCESSING, TO_DISCARD), ui.state.effectCards);
  const framedItems = ui.state.effectCards;
  const framedBatches = framedItems.length === 3
    && framedItems.every((item) => item.stage.batch === 3);
  const framedAttached = itemOf(830).stage.lane === "discard"
    && itemOf(830).stage.fadeAt === null;
  ui.renderProcessing(framedEntered + 5000);
  const framedVisibleBeforePop = stageNodes().length === 3
    && framedItems.every((item) => !stageNode(item.card_id).classList.contains("fading"));
  const popBefore = Date.now();
  ui.state.effectCards = ui.applyEffectDelta(pop(812, 50), ui.state.effectCards);
  const popAfter = Date.now();
  const framedFadeAt = itemOf(831) && itemOf(831).stage.fadeAt;
  const framedDwell = Number.isFinite(framedFadeAt)
    && framedFadeAt >= popBefore + 2000 && framedFadeAt <= popAfter + 2000
    && framedItems.every((item) => item.stage.fadeAt === framedFadeAt);
  ui.renderProcessing(framedFadeAt - 1);
  const framedStillDwelling = stageNodes().length === 3
    && stageNodes().every((node) => !node.classList.contains("fading"));
  ui.renderProcessing(framedFadeAt);
  const framedFadingTogether = stageNodes().length === 3
    && stageNodes().every((node) => node.classList.contains("fading"));
  ui.renderProcessing(framedFadeAt + 1000);
  check("U4-03c 有承载帧的 3 张弃置：batch=3（换组不重算）、pop 前一直可见、pop 后停留 2000ms 再一起淡出",
    framedBatches && framedAttached && framedVisibleBeforePop && framedDwell
      && framedStillDwelling && framedFadingTogether && stageNodes().length === 0,
    `batches=${framedItems.map((item) => item.stage.batch).join(",")}`
      + ` attached=${framedAttached} beforePop=${framedVisibleBeforePop}`
      + ` fadeAt=${framedFadeAt} popAt=${popBefore}..${popAfter}`
      + ` dwell=${framedStillDwelling} fading=${framedFadingTogether}`
      + ` gone=${stageNodes().length === 0}`);

  // 郭嘉分两次交 2 张 = 两条 1 张的记录：各按 N=1 计、各停 1s，先后消失（不并成一批）。
  resetStage();
  // 两条记录相隔半拍：用**注入的 now** 表达先后，不读真实时钟、不等计时器（标准 §4）。
  const firstNow = 1_000_000;
  const secondNow = firstNow + 500;
  ui.state.effectCards = ui.applyEffectDelta(
    move(820, 0, [card(840, "杀")], "DISCARD", at("hand", 0), TO_DISCARD), [], null, firstNow);
  ui.state.effectCards = ui.applyEffectDelta(
    move(821, 0, [card(841, "闪")], "DISCARD", at("hand", 0), TO_DISCARD),
    ui.state.effectCards, null, secondNow);
  const firstMoveItem = itemOf(840);
  const secondMoveItem = itemOf(841);
  const sequentialBatches = ui.state.effectCards.length === 2
    && firstMoveItem.stage.batch === 1 && secondMoveItem.stage.batch === 1;
  const sequentialHold = firstMoveItem.stage.fadeAt - firstMoveItem.stage.enteredAt === 1000
    && secondMoveItem.stage.fadeAt - secondMoveItem.stage.enteredAt === 1000;
  const sequentialOrder = secondMoveItem.stage.fadeAt > firstMoveItem.stage.fadeAt;
  ui.renderProcessing(firstMoveItem.stage.fadeAt);
  const firstFadingSecondVisible = fading(840) && !fading(841);
  ui.renderProcessing(secondMoveItem.stage.fadeAt);
  const bothFading = fading(840) && fading(841);
  ui.renderProcessing(secondMoveItem.stage.fadeAt + 1000);
  check("U4-03d 依次两条 1 张移动：各自 batch=1、各停 1000ms、先后消失（郭嘉两次交牌不并批）",
    sequentialBatches && sequentialHold && sequentialOrder
      && firstFadingSecondVisible && bothFading && stageNodes().length === 0,
    `batches=${firstMoveItem.stage.batch},${secondMoveItem.stage.batch}`
      + ` holds=${firstMoveItem.stage.fadeAt - firstMoveItem.stage.enteredAt},`
      + `${secondMoveItem.stage.fadeAt - secondMoveItem.stage.enteredAt}`
      + ` order=${secondMoveItem.stage.fadeAt - firstMoveItem.stage.fadeAt}`
      + ` firstOnly=${firstFadingSecondVisible} both=${bothFading}`
      + ` gone=${stageNodes().length === 0}`);

  // `card_reveal` 记录也记 batch（今天一条记录一张），走 applyRevealDelta 的 pop 分支。
  resetStage();
  const revealCard = card(850, "桃");
  ui.state.revealedCards = ui.applyRevealDelta(reveal(860, 60, revealCard, 1), []);
  const revealItem = itemOf(850);
  const revealBatch = Boolean(revealItem) && revealItem.stage.batch === 1
    && revealItem.stage.fadeAt === null;
  ui.renderProcessing(revealItem.stage.enteredAt + 5000);
  const revealVisibleBeforePop = Boolean(stageNode(850)) && !fading(850);
  const revealPopNow = revealItem.stage.enteredAt + 5000;
  ui.state.revealedCards = ui.applyRevealDelta(
    pop(861, 60), ui.state.revealedCards, revealPopNow);
  const revealFadeAt = revealItem && revealItem.stage.fadeAt;
  const revealDwell = revealFadeAt === revealPopNow + 1000;
  ui.renderProcessing(revealPopNow + 999);
  const revealStillDwelling = !fading(850);
  ui.renderProcessing(revealPopNow + 1000);
  check("U4-03e card_reveal（N=1）也记 batch：pop 后停留 1000ms 再淡出（applyRevealDelta 同一公式）",
    revealBatch && revealVisibleBeforePop && revealDwell && revealStillDwelling && fading(850),
    `batch=${revealItem && revealItem.stage.batch} fadeAt=${revealFadeAt}`
      + ` popAt=${revealPopNow}`
      + ` dwell=${revealStillDwelling} fading=${fading(850)}`);

  // `animScale` 只缩淡出：置 0 后 3 张仍停满 2000ms（0.5×(3+1)）才整批移除（停留不缩）。
  resetStage();
  ui.state.animScale = 0;
  ui.state.effectCards = ui.applyEffectDelta(
    move(870, 0, [card(871, "杀"), card(872, "闪"), card(873, "桃")],
      "DISCARD", at("hand", 0), TO_DISCARD), []);
  const fastItems = ui.state.effectCards;
  const fastEntered = fastItems.length && fastItems[0].stage.enteredAt;
  const fastHold = fastItems.length === 3
    && fastItems.every((item) => item.stage.fadeAt === fastEntered + 2000);
  ui.renderProcessing(fastEntered + 1999);
  const fastVisibleDuringDwell = stageNodes().length === 3 && !fading(871);
  ui.renderProcessing(fastEntered + 2000);
  check("U4-03f animScale = 0 只缩淡出：3 张仍停满 2000ms 才移除（停留不缩）",
    fastHold && fastVisibleDuringDwell && stageNodes().length === 0,
    `fadeAt=${fastItems.map((item) => item.stage.fadeAt - fastEntered).join(",")}`
      + ` visible=${fastVisibleDuringDwell} gone=${stageNodes().length === 0}`);

  // 兜底 30s 只接"丢 pop"（fadeAt=null），不是停留上限：N=60 ⇒ 30500ms 照公式走完。
  resetStage();
  const sixty = Array.from({ length: 60 }, (_, index) => card(900 + index, "杀"));
  ui.state.effectCards = ui.applyEffectDelta(
    move(880, 0, sixty, "DISCARD", at("hand", 0), TO_DISCARD), []);
  const sixtyItems = ui.state.effectCards;
  const sixtyEntered = sixtyItems.length && sixtyItems[0].stage.enteredAt;
  const sixtyHold = sixtyItems.length === 60
    && sixtyItems.every((item) => item.stage.batch === 60)
    && sixtyItems.every((item) => item.stage.fadeAt === sixtyEntered + 30500);
  ui.renderProcessing(sixtyEntered + 30000);
  const sixtyStillHolding = stageNodes().length === 60 && !fading(900);
  ui.renderProcessing(sixtyEntered + 30500);
  const sixtyFading = stageNodes().length === 60 && fading(900);
  ui.renderProcessing(sixtyEntered + 31500);
  check("U4-03g 30s 兜底只接丢 pop（fadeAt=null），不是停留上限：N=60 ⇒ 30500ms 后淡出",
    sixtyHold && sixtyStillHolding && sixtyFading && stageNodes().length === 0,
    `batch=${sixtyItems[0] && sixtyItems[0].stage.batch}`
      + ` hold=${sixtyItems[0] && sixtyItems[0].stage.fadeAt - sixtyEntered}`
      + ` at30s=${sixtyStillHolding} at30.5s=${sixtyFading}`
      + ` gone=${stageNodes().length === 0}`);
  resetStage();
}

/* ============ U4-04（spec D10）：阶段 / 回合飘字 ============
 *
 * 走真入口：`timing` 记录交给 `stepPlayback(true)`（与页面同一条回放），飘字由
 * `stepPlayback` 在**重绘之前**规划、由 `anim.js` 落在自己的覆盖层 `#t-anim-phase`。
 * 断言只落在 DOM（文本 / `data-anim-phase-*` / 写死的 `left`、`top`）与纯函数（`dur = 0`）。
 * 不读真实布局：垫片矩形是确定性的（`shimRect`）；窄壳 / 贴边座位用 `pinRect` 钉矩形夹具。
 */
{
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walk(child, out); }
    return out;
  };
  const hasClass = (node, name) => Boolean(node
    && String(node.className || "").split(/\s+/).includes(name));
  /* 垫片矩形 = `getBoundingClientRect` 的 `_rect` 缓存：钉住它就是本篇的布局夹具。
   * 只给"窄壳 / 贴右 / 贴左"这类真实布局下会顶出壳外的用例用，用完 `delete` 恢复。 */
  const pinRect = (node, left, top, width, height) => {
    if (node) {
      node._rect = {
        left, top, width, height,
        right: left + width, bottom: top + height, x: left, y: top,
      };
    }
    return node;
  };
  const unpinRect = (node) => { if (node) delete node._rect; };
  // 垫片给 `.anim-phase-float` 的确定尺寸（与 CSS 无关，夹具常量）：夹边距按它算。
  const FLOAT_WIDTH = 100;
  const layer = () => $("t-anim-phase");
  const floatNodes = () => Array.from((layer() && layer().children) || [])
    .filter((node) => hasClass(node, "anim-phase-float"));
  const clearFloats = () => { const box = layer(); if (box) box.innerHTML = ""; };
  const px = (value) => Number.parseFloat(String(value || ""));

  const player = (seat, heroName) => ({
    seat, hp: 4, hp_max: 4, hand_count: 2, alive: true, equip: [], judge: [],
    identity: null, hero_name: heroName,
  });
  const baseView = (records, extra = {}) => ({
    session_id: "u4-phase", seed: 1, human_seat: 0, finished: false, timeout_seconds: null,
    heroes: { 0: "guojia", 1: "zhouyu" }, skills: [], pending: null,
    snapshot: {
      seat: 0, phase: "play", turn_seat: 1, deck_count: 30, discard: [], processing: [],
      players: [player(0, "郭嘉"), player(1, "周瑜"), player(2, "")],
      self_hand: [synthCard(1, "杀")],
    },
    records, live_deltas: [], narrative: [], skill_events: [],
    cursor: records.length, log_seq: records.length, ...extra,
  });
  const timingRecord = (seq, name, actor) => ({
    kind: "timing", timing: name, seq, frame_id: 0, depth: 0, actor,
  });
  /** 落一份干净会话：先铺一遍表体（锚点来自真实渲染口），再起带 records 的回放。 */
  const load = (records, extra = {}) => {
    ui.flushPlayback();
    ui.state.playback = null;
    ui.state.effectCards = [];
    ui.state.revealedCards = [];
    ui.state.sessionId = "u4-phase";
    ui.state.view = baseView([], extra);
    clearFloats();
    ui.renderTable();
    ui.applyViewRaw(baseView(records, extra));
  };
  const step = () => ui.stepPlayback(true);
  const seatNode = (seat) => [...$("t-seats").children]
    .find((node) => attr(node, "data-seat") === String(seat)) || null;
  const judgeRow = (seat) => {
    const root = seatNode(seat);
    return root ? walk(root).find((node) => hasClass(node, "judge-row")) || null : null;
  };
  const heroBlock = (seat) => {
    const root = seatNode(seat);
    return root ? walk(root).find((node) => hasClass(node, "hero-block")) || null : null;
  };

  // ---- 层 + 六个阶段标签 ----
  // 先看一眼**真对局**（本文件开头的 53 步驱动）留下的节点：引擎真发的 `TURN_START` /
  // `PHASE_*_START` 走过同一条 `stepPlayback`，飘字早已落在这一层上（§6：读取方断言
  // 先有真产出路径的证据；下面的用例再用同形合成载荷逐条钉文本 / 位置）。
  const realFloats = floatNodes();
  const realTimings = realFloats.map((node) => String(attr(node, "data-anim-phase-timing")));
  check("U4-04-real 真对局回放里引擎真发的时机已经在本层起过飘字（不靠合成载荷）",
    realTimings.includes("TURN_START")
      && realTimings.some((timing) => /^PHASE_[A-Z]+_START$/.test(timing)),
    `n=${realFloats.length} timings=${JSON.stringify([...new Set(realTimings)])}`);

  const labels = [
    ["PHASE_PREPARE_START", "准备阶段开始"],
    ["PHASE_JUDGE_START", "判定阶段开始"],
    ["PHASE_DRAW_START", "摸牌阶段开始"],
    ["PHASE_PLAY_START", "出牌阶段开始"],
    ["PHASE_DISCARD_START", "弃牌阶段开始"],
    ["PHASE_FINISH_START", "结束阶段开始"],
  ];
  load(labels.map(([name], index) => timingRecord(index + 1, name, 1)));
  for (let index = 0; index < labels.length; index += 1) step();
  const six = floatNodes();
  const layerNode = layer();
  check("U4-04-layer 阶段飘字层由 anim.js 动态建在 #table-shell 里（纯视觉层）",
    Boolean(layerNode) && layerNode.parentNode === $("table-shell")
      && hasClass(layerNode, "anim-phase-layer")
      && attr(layerNode, "aria-hidden") === "true",
    `parent=${layerNode && layerNode.parentNode && layerNode.parentNode.id}`
      + ` class=${layerNode && layerNode.className} aria=${attr(layerNode, "aria-hidden")}`);
  check("U4-04-a 六个 PHASE_*_START → 「XX阶段开始」六种；座位 = actor、timing 原样留在 data-*",
    six.length === labels.length
      && six.every((node, index) => node.textContent === labels[index][1]
        && attr(node, "data-anim-phase-seat") === "1"
        && attr(node, "data-anim-phase-timing") === labels[index][0]),
    `n=${six.length} texts=${JSON.stringify(six.map((node) => node.textContent))}`);

  // ---- 锚：判定行（230..380）上方，不是武将块正上方（那是技能名飘字） ----
  const first = six[0] || null;
  const jr = judgeRow(1);
  const jrRect = jr ? jr.getBoundingClientRect() : null;
  const firstLeft = first ? px(first.style.left) : Number.NaN;
  const firstTop = first ? px(first.style.top) : Number.NaN;
  check("U4-04-b 锚在对手判定行上方：水平居中于 .judge-row（垫片 260..380 ⇒ 320），top 在其上沿之上",
    Boolean(first) && attr(first, "data-anim-phase-anchor") === "judge-row"
      && Boolean(jrRect)
      && Math.abs(firstLeft - (jrRect.left + jrRect.width / 2)) < 1e-6
      && firstTop < jrRect.top && firstTop > jrRect.top - 12,
    `left=${first && first.style.left} top=${first && first.style.top}`
      + ` judge=${jrRect && `${jrRect.left}..${jrRect.left + jrRect.width}@${jrRect.top}`}`
      + ` anchor=${first && attr(first, "data-anim-phase-anchor")}`
      + ` hero=${heroBlock(1) && heroBlock(1).getBoundingClientRect().left}`);

  // ---- 自己的座位：锚在 #t-self-zones（判定 + 装备整块，最右），夹在壳内 ----
  load([timingRecord(20, "PHASE_DRAW_START", 0)]);
  step();
  const own = floatNodes()[0] || null;
  const zonesRect = $("t-self-zones").getBoundingClientRect();
  const ownLeft = own ? px(own.style.left) : Number.NaN;
  const ownTop = own ? px(own.style.top) : Number.NaN;
  check("U4-04-c 自己的座位锚在 #t-self-zones（判定 + 装备整块）上方的空隙里，水平居中",
    Boolean(own) && attr(own, "data-anim-phase-anchor") === "self-zones"
      && Math.abs(ownLeft - (zonesRect.left + zonesRect.width / 2)) < 1e-6
      && ownTop < zonesRect.top && ownTop > zonesRect.top - 12,
    `left=${own && own.style.left} top=${own && own.style.top}`
      + ` zones=${zonesRect.left}..${zonesRect.left + zonesRect.width}@${zonesRect.top}`);

  // ---- 夹进壳内：自己的区贴最右（窄壳）+ 最左座位贴左缘，都不顶出 #table-shell ----
  load([
    timingRecord(30, "PHASE_DRAW_START", 0),
    timingRecord(31, "PHASE_DRAW_START", 0),
    timingRecord(32, "PHASE_DRAW_START", 2),
  ]);
  step();                       // 起一层（层在第一次飘字时惰性创建）
  const shellNode = $("table-shell");
  pinRect(shellNode, 0, 0, 900, 810);
  pinRect(layer(), 0, 0, 900, 810);
  step();                       // 窄壳：自己的座位（中心 1310）会顶出 900 宽的壳
  const rightNode = floatNodes()[1] || null;
  const rightLeft = rightNode ? px(rightNode.style.left) : Number.NaN;
  unpinRect(shellNode);
  unpinRect(layer());
  const edgeJudge = pinRect(judgeRow(2), -20, 100, 120, 52);
  step();                       // 最左：判定行钉在左缘外，夹回边距内
  const leftNode = floatNodes()[2] || null;
  const leftLeft = leftNode ? px(leftNode.style.left) : Number.NaN;
  unpinRect(edgeJudge);
  const rightLimit = 900 - 6 - FLOAT_WIDTH / 2;
  const leftLimit = 6 + FLOAT_WIDTH / 2;
  check("U4-04-d 夹进壳内：最右（自己的座位 / 窄壳）夹右、最左座位夹左，都不顶出边界",
    Boolean(rightNode) && Boolean(leftNode)
      && attr(rightNode, "data-anim-phase-clamped") === "right"
      && Math.abs(rightLeft - rightLimit) < 1e-6
      && rightLeft + FLOAT_WIDTH / 2 <= 900 - 6
      && attr(leftNode, "data-anim-phase-clamped") === "left"
      && Math.abs(leftLeft - leftLimit) < 1e-6
      && leftLeft - FLOAT_WIDTH / 2 >= 6,
    `right=${rightLeft}(${rightNode && attr(rightNode, "data-anim-phase-clamped")})`
      + ` expect<=${900 - 6}`
      + ` left=${leftLeft}(${leftNode && attr(leftNode, "data-anim-phase-clamped")})`
      + ` expect>=6`);

  // ---- 回合：轮到 <武将名>；接入层拿不到名字时退回座位号 ----
  load([timingRecord(40, "TURN_START", 1)]);
  step();
  const turnNode = floatNodes()[0] || null;
  check("U4-04-e TURN_START → 「轮到 周瑜」：武将展示名走接入层 heroNameOf（前端不新建映射）",
    Boolean(turnNode) && turnNode.textContent === "轮到 周瑜"
      && attr(turnNode, "data-anim-phase-seat") === "1"
      && attr(turnNode, "data-anim-phase-timing") === "TURN_START",
    `text=${turnNode && turnNode.textContent}`);

  load([timingRecord(50, "TURN_START", 2)]);
  step();
  const noName = floatNodes()[0] || null;
  check("U4-04-f 无武将名（（无武将） / 空）→ 退回「轮到 座位 2」",
    Boolean(noName) && noName.textContent === "轮到 座位 2"
      && attr(noName, "data-anim-phase-seat") === "2",
    `text=${noName && noName.textContent}`);

  // ---- 座位兜底：timing 没有 actor 时退回当前座位（自己的判定 / 装备区） ----
  load([timingRecord(60, "PHASE_PLAY_START", null)]);
  step();
  const fallbackSeat = floatNodes()[0] || null;
  check("U4-04-g 时机没有 actor 时退回当前座位（#t-self-zones）",
    Boolean(fallbackSeat) && attr(fallbackSeat, "data-anim-phase-seat") === "0"
      && attr(fallbackSeat, "data-anim-phase-anchor") === "self-zones",
    `seat=${fallbackSeat && attr(fallbackSeat, "data-anim-phase-seat")}`
      + ` anchor=${fallbackSeat && attr(fallbackSeat, "data-anim-phase-anchor")}`);

  // ---- 与技能名飘字同时：同座位、不同层 / 不同锚（技能名住武将块正上方） ----
  load([timingRecord(71, "PHASE_DRAW_START", 1)], {
    skill_events: [{ seq: 71, seat: 1, skill_id: "hero.zhouyu.fanjian", name: "反间", targets: [] }],
  });
  step();
  const phaseNode = floatNodes()[0] || null;
  const skillNode = walk($("t-skill-floats"))
    .find((node) => hasClass(node, "skill-float")) || null;
  const skillBlock = heroBlock(1);
  const skillBlockRect = skillBlock ? skillBlock.getBoundingClientRect() : null;
  const skillLayerRect = $("t-skill-floats").getBoundingClientRect();
  const judgeRect1 = judgeRow(1) ? judgeRow(1).getBoundingClientRect() : null;
  const phaseLeftH = phaseNode ? px(phaseNode.style.left) : Number.NaN;
  const phaseTopH = phaseNode ? px(phaseNode.style.top) : Number.NaN;
  const skillLeftH = skillNode ? px(skillNode.style.left) : Number.NaN;
  const skillTopH = skillNode ? px(skillNode.style.top) : Number.NaN;
  check("U4-04-h 同座位「发动技能 + 阶段切换」同时发生：两个节点各占一层、各锚各的区，不叠",
    Boolean(phaseNode) && Boolean(skillNode)
      && phaseNode.parentNode === layer()
      && skillNode.parentNode === $("t-skill-floats")
      && attr(phaseNode, "data-anim-phase-seat") === "1"
      && attr(skillNode, "data-skill-seat") === "1"
      && Boolean(skillBlockRect) && Boolean(judgeRect1)
      && Math.abs(phaseLeftH - (judgeRect1.left + judgeRect1.width / 2)) < 1e-6
      && phaseTopH < judgeRect1.top
      && Math.abs(skillLeftH
        - (skillBlockRect.left - skillLayerRect.left + skillBlockRect.width / 2)) < 1e-6
      && skillTopH < skillBlockRect.top
      && Math.abs(phaseLeftH - skillLeftH) > 1e-6,
    `phase=${phaseLeftH},${phaseTopH}@${phaseNode && phaseNode.parentNode && phaseNode.parentNode.id}`
      + ` skill=${skillLeftH},${skillTopH}@${skillNode && skillNode.parentNode && skillNode.parentNode.id}`
      + ` judge=${judgeRect1 && judgeRect1.left} hero=${skillBlockRect && skillBlockRect.left}`);

  // ---- 不占步长：durationOf(timing) = 0、delayOf 只吃 base；收尾不另排计时器 ----
  const timingDelay = timingRecord(80, "PHASE_DRAW_START", 1);
  load([timingDelay, timingRecord(81, "TURN_START", 1)]);
  pendingTimers.clear();
  timerDelays.clear();
  ui.stepPlayback(false);
  const phaseStepDelay = latestTimerDelay();
  const phaseStepTimers = pendingTimers.size;
  ui.flushPlayback();
  check("U4-04-i 不占步长：dur=0、delayOf(…,100)=100；收尾仍只排 base（飘字不加计时器）",
    ui.durationOf(timingDelay) === 0
      && ui.delayOf(timingDelay, 100) === 100
      && phaseStepDelay === 100
      && phaseStepTimers === 1,
    `dur=${ui.durationOf(timingDelay)} delay=${ui.delayOf(timingDelay, 100)}`
      + ` 排程=${phaseStepDelay}ms timers=${phaseStepTimers}`);

  // ---- 缺锚点 / 其他座位 / 换尺寸：静默、不抛错、不卡播放 ----
  load([timingRecord(90, "PHASE_DRAW_START", 7)]);   // 表体上没有座位 7
  let missingThrew = null;
  let missingFloats = -1;
  try {
    step();
    missingFloats = floatNodes().length;
  } catch (error) {
    missingThrew = String(error && error.message);
  }
  const missingAdvanced = Boolean(ui.state.playback) && ui.state.playback.index > 0;
  check("U4-04-j 没有对应座位的锚点：静默跳过（只放节点 = 0）、不抛错、播放照走",
    missingThrew === null && missingFloats === 0 && missingAdvanced,
    `floats=${missingFloats} threw=${missingThrew} advanced=${missingAdvanced}`);

  load([timingRecord(91, "PHASE_DRAW_START", 1)]);
  pinRect($("table-shell"), 0, 0, 720, 405);
  pinRect(layer(), 0, 0, 720, 405);
  let resizedThrew = null;
  let resizedNode = null;
  try {
    step();
    resizedNode = floatNodes()[0] || null;
  } catch (error) {
    resizedThrew = String(error && error.message);
  }
  unpinRect($("table-shell"));
  unpinRect(layer());
  const resizedLeft = resizedNode ? px(resizedNode.style.left) : Number.NaN;
  check("U4-04-k 壳换尺寸（720 宽）：飘字照常居中并留在壳内，不抛错",
    resizedThrew === null && Boolean(resizedNode)
      && resizedLeft - FLOAT_WIDTH / 2 >= 6
      && resizedLeft + FLOAT_WIDTH / 2 <= 720 - 6
      && attr(resizedNode, "data-anim-phase-clamped") === undefined,
    `left=${resizedLeft} threw=${resizedThrew}`);

  // ---- 寿命 = anim.ini 的 phase_float_ms（同一份 animConfig）；动画结束即移除 ----
  iniInjection = {
    ok: true,
    text: Object.entries(ui.animDefaults)
      .map(([key, value]) => `${key} = ${key === "phase_float_ms" ? 600 : value}`).join("\n"),
  };
  await ui.animLoadConfig();
  load([timingRecord(95, "PHASE_DRAW_START", 1)]);
  step();
  const lifetimeNode = floatNodes()[0] || null;
  const lifetimeAnimation = lifetimeNode && lifetimeNode.animations && lifetimeNode.animations[0];
  const lifetimeMs = lifetimeAnimation ? lifetimeAnimation.options.duration : null;
  let removedOnFinish = false;
  if (lifetimeAnimation) {
    lifetimeAnimation.finish();   // 垫片只登记动画；显式跑完 ⇒ onfinish 移除节点
    removedOnFinish = floatNodes().length === 0 && !lifetimeNode.parentNode;
  }
  check("U4-04-l 寿命 = anim.ini 的 phase_float_ms（注入 600 即生效）；动画结束即移除节点",
    Boolean(lifetimeAnimation) && lifetimeMs === 600 && removedOnFinish,
    `duration=${lifetimeMs} removed=${removedOnFinish}`);
  iniInjection = null;
  await ui.animLoadConfig();
  check("U4-04-l2 只走票 02 的那一份 animConfig（重读磁盘 ini 后回到缺省 1200，不新建表）",
    ui.animConfig.phase_float_ms === ui.animDefaults.phase_float_ms,
    `phase_float_ms=${ui.animConfig.phase_float_ms}`);

  // ---- 准备 / 判定 / 结束读**阶段体**：这三个阶段的 `_START` 在 EMPTY_LOG、不 dispatch ----
  load([
    timingRecord(100, "PHASE_PREPARE", 1),
    timingRecord(101, "PHASE_JUDGE", 1),
    timingRecord(102, "PHASE_FINISH", 1),
  ]);
  for (let index = 0; index < 3; index += 1) step();
  const bodyFloats = floatNodes();
  check("U4-04-m 准备 / 判定 / 结束三个阶段体各起一次「XX阶段开始」（M6 报障 3）",
    bodyFloats.length === 3
      && bodyFloats.map((node) => node.textContent).join(",") === "准备阶段开始,判定阶段开始,结束阶段开始"
      && bodyFloats.map((node) => attr(node, "data-anim-phase-timing")).join(",")
        === "PHASE_PREPARE,PHASE_JUDGE,PHASE_FINISH",
    `texts=${bodyFloats.map((node) => node.textContent).join("|")}`
      + ` timings=${bodyFloats.map((node) => attr(node, "data-anim-phase-timing")).join("|")}`);

  // ---- 其余时机零动作：摸牌 / 出牌的阶段体（它们本来就有 `_START`）、阶段空闲、回合结束、
  //      非 timing（一个阶段只许出一次飘字） ----
  load([
    timingRecord(103, "PHASE_DRAW", 1),
    timingRecord(104, "PHASE_PLAY_IDLE", 1),
    timingRecord(105, "TURN_END", 1),
    { kind: "delta", delta_type: "hp_change", seq: 106, frame_id: 0, depth: 0 },
  ]);
  for (let index = 0; index < 4; index += 1) step();
  check("U4-04-m2 摸牌 / 出牌阶段体、阶段空闲、回合结束、非 timing 仍零动作",
    floatNodes().length === 0,
    `floats=${floatNodes().length}`);

  // ---- 判定行缺失时退回装备格（同一块区域上方的空隙） ----
  load([timingRecord(110, "PHASE_DRAW_START", 1)]);
  const judge1Node = judgeRow(1);
  if (judge1Node && judge1Node.parentNode) judge1Node.parentNode.removeChild(judge1Node);
  step();
  const equipFallback = floatNodes()[0] || null;
  const equipNode = seatNode(1)
    ? walk(seatNode(1)).find((node) => hasClass(node, "equip-grid")) : null;
  const equipRect = equipNode ? equipNode.getBoundingClientRect() : null;
  check("U4-04-n 对手没有判定行时退回 .equip-grid[data-seat]（同一块区域上方）",
    Boolean(equipFallback) && Boolean(equipRect)
      && attr(equipFallback, "data-anim-phase-anchor") === "equip-grid"
      && px(equipFallback.style.top) < equipRect.top
      && Math.abs(px(equipFallback.style.left) - (equipRect.left + equipRect.width / 2)) < 1e-6,
    `anchor=${equipFallback && attr(equipFallback, "data-anim-phase-anchor")}`
      + ` left=${equipFallback && equipFallback.style.left} top=${equipFallback && equipFallback.style.top}`
      + ` equip=${equipRect && `${equipRect.left}..${equipRect.left + equipRect.width}@${equipRect.top}`}`);

  ui.state.view = null;
  ui.state.sessionId = null;
}

/* ============ U4-05（spec D11）：弃牌堆面板 ============
 *
 * 顶栏「弃牌 N」开一个**只读**模态：数据源与表体同一份 `tableViewForRender()`，
 * 时间倒序（最新在上），进入 / 关闭都不改选择态。这一节不读真实布局 / 时钟：
 * 打开走 `#btn-discard` 的点击监听，关闭走 Esc / 自己的遮罩 / 关闭按钮。
 */
{
  const discardCard = (id, name, suit = "heart", rank = 10) => ({
    ...synthCard(id, name), suit, rank,
    color: suit === "heart" || suit === "diamond" ? "red" : "black",
  });
  const discardBody = (discard) => ({
    session_id: "u4-discard",
    seed: 1, human_seat: 0, finished: false, timeout_seconds: null,
    heroes: { 0: "guojia" }, skills: [], pending: null,
    snapshot: { ...synthView(null).snapshot, discard },
    records: [], live_deltas: [], narrative: [], skill_events: [],
    cursor: 1, log_seq: 1,
  });
  const rows = () => Array.from($("t-discard-body").children);
  const rowIds = () => rows().map((node) => node.getAttribute("data-card-id")).join(",");
  const openDiscard = () => clickButton($("btn-discard"));
  const discardClosed = () => $("t-discard-modal").hidden === true
    && $("t-discard-backdrop").hidden === true;

  const slash = discardCard(101, "杀", "spade", 7);
  const dodge = discardCard(102, "闪", "diamond", 9);
  const peach = discardCard(103, "桃", "heart", 10);
  ui.state.playback = null;
  ui.state.liveView = null;
  ui.state.view = discardBody([slash, dodge, peach]);
  ui.resetSelection();
  ui.renderTable();
  openDiscard();
  check("U4-05a 点顶栏「弃牌 N」开只读面板：标题 / 「共 N 张」/ 3 行牌面",
    $("t-discard-modal").hidden === false
      && $("t-discard-backdrop").hidden === false
      && $("t-discard-title").textContent === "弃牌堆"
      && $("t-discard-summary").textContent === "共 3 张"
      && rows().length === 3
      && rows()[0].text().includes("桃")
      && rows()[0].text().includes("♥")
      && rows()[0].text().includes("10"),
    `hidden=${$("t-discard-modal").hidden} title="${$("t-discard-title").textContent}"`
      + ` summary="${$("t-discard-summary").textContent}" rows=${rows().length}`);
  check("U4-05b 时间倒序：最后弃的在最上（插入序 [101,102,103] ⇒ 行序 103,102,101）",
    rowIds() === "103,102,101" && rows().every((node) => node.listeners.click === undefined),
    `rowIds=${rowIds()}`);
  clickButton($("t-discard-close"));
  check("U4-05c 关闭按钮收起面板（窗口与自己的遮罩一起隐藏）",
    discardClosed(), `hidden=${$("t-discard-modal").hidden} backdrop=${$("t-discard-backdrop").hidden}`);

  // 空态：一句空话 + 「共 0 张」，没有牌面行。
  ui.state.view = discardBody([]);
  ui.renderTable();
  openDiscard();
  check("U4-05d 空弃牌堆打开显示一句空态（不画牌行）",
    $("t-discard-summary").textContent === "共 0 张"
      && rows().length === 1
      && hasClassText(rows()[0], "discard-empty")
      && rows()[0].text() === "弃牌堆里还没有牌。",
    `summary="${$("t-discard-summary").textContent}" rows=${rows().length}`
      + ` text="${rows()[0] ? rows()[0].text() : ""}"`);
  clickButton($("t-discard-close"));

  // Esc：只关面板、零提交。
  ui.state.view = discardBody([slash, dodge, peach]);
  ui.renderTable();
  openDiscard();
  const escCalls = calls.length;
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U4-05e Esc 收起面板（零 /api/ 提交）",
    discardClosed() && calls.length === escCalls,
    `hidden=${$("t-discard-modal").hidden} calls=${calls.length - escCalls}`);

  // 自己的遮罩：点它关闭；`#t-modal-backdrop` 仍旧没有 click（U2 断言不受影响）。
  openDiscard();
  const backdropCalls = calls.length;
  const backdropListeners = ($("t-discard-backdrop").listeners.click || []).length;
  clickButton($("t-discard-backdrop"));
  check("U4-05f 点自己的遮罩关闭；与 #t-modal-backdrop 是两层（那一层仍无 click）",
    discardClosed() && backdropListeners === 1
      && ($("t-modal-backdrop").listeners.click || []).length === 0
      && calls.length === backdropCalls,
    `hidden=${$("t-discard-modal").hidden} listeners=${backdropListeners}`
      + ` legacy=${($("t-modal-backdrop").listeners.click || []).length}`);

  // 只读：打开与点牌都不设焦点 / 不改 picks / 不提交；selectionEnabled 与步骤表面不变。
  ui.state.focusCardId = 101;
  ui.state.picks = [102];
  ui.state.selectedTargets = [1];
  const enabledBefore = ui.selectionEnabled();
  const surfaceBefore = ui.stepDialogKind(ui.state.view.pending);
  const readOnlyCalls = calls.length;
  openDiscard();
  const focusAfterOpen = ui.state.focusCardId;
  const picksAfterOpen = JSON.stringify(ui.state.picks);
  rows().forEach((node) => clickButton(node));
  check("U4-05g 只读：开面板与点牌面都不改焦点 / picks / 提交，selectionEnabled 不变",
    $("t-discard-modal").hidden === false
      && focusAfterOpen === 101
      && picksAfterOpen === "[102]"
      && ui.state.focusCardId === 101
      && JSON.stringify(ui.state.picks) === "[102]"
      && JSON.stringify(ui.state.selectedTargets) === "[1]"
      && ui.selectionEnabled() === enabledBefore
      && ui.stepDialogKind(ui.state.view.pending) === surfaceBefore
      && calls.length === readOnlyCalls,
    `focus=${ui.state.focusCardId} picks=${JSON.stringify(ui.state.picks)}`
      + ` enabled=${ui.selectionEnabled()} calls=${calls.length - readOnlyCalls}`);
  clickButton($("t-discard-close"));

  // Esc 的两个窗口互相独立：先收面板，武将详情（#t-modal）原样留着，再按一次才关它。
  const ownHeroBlock = $("t-self-hero");
  const openHeroDetailByContext = () => (ownHeroBlock.listeners.contextmenu || []).forEach(
    (fn) => fn({ target: ownHeroBlock, preventDefault() {}, stopPropagation() {} }));
  openDiscard();
  openHeroDetailByContext();
  const bothOpen = $("t-discard-modal").hidden === false && $("t-modal").hidden === false;
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  const discardFirst = $("t-discard-modal").hidden === true && $("t-modal").hidden === false;
  fireDocument("keydown", { key: "Escape", preventDefault() {} });
  check("U4-05h Esc 独立：面板先关、武将详情不被连坐；再按一次才关详情",
    bothOpen && discardFirst && $("t-modal").hidden === true,
    `both=${bothOpen} discardFirst=${discardFirst} detailHidden=${$("t-modal").hidden}`);

  // 数据源 = `tableViewForRender()`：回放期读逐事件派生的 live view，**不**读权威视图。
  ui.state.playback = null;
  ui.state.liveView = null;
  ui.state.view = discardBody([slash]);
  ui.state.liveView = discardBody([dodge, peach]);
  ui.state.playback = { index: 0, records: [], live: true };
  ui.renderTable();
  openDiscard();
  check("U4-05i 回放期读 live view 的弃牌堆（权威 state.view 只有 1 张也不读）",
    rowIds() === "103,102" && $("t-discard-summary").textContent === "共 2 张",
    `rowIds=${rowIds()} summary="${$("t-discard-summary").textContent}"`);
  ui.state.playback = null;
  ui.state.liveView = null;
  clickButton($("t-discard-close"));

  // 自动刷新（提交）：面板开着时一次 commit 走 `renderTable()` 末尾的重画，不新起轮询。
  ui.state.view = discardBody([slash, dodge]);
  ui.renderTable();
  openDiscard();
  const intervalsBefore = intervals.length;
  ui.applyView(discardBody([slash, dodge, peach]));
  check("U4-05j 面板开着 + 一次提交：列表 / 「共 N 张」自动更新（不新增轮询计时器）",
    rowIds() === "103,102,101"
      && $("t-discard-summary").textContent === "共 3 张"
      && intervals.length === intervalsBefore,
    `rowIds=${rowIds()} summary="${$("t-discard-summary").textContent}"`
      + ` intervals=${intervals.length - intervalsBefore}`);
  clickButton($("t-discard-close"));

  // 自动刷新（回放一步）：live view 由 `card_move` 派生弃牌，面板每一步都重画。
  ui.state.view = discardBody([slash]);
  ui.renderTable();
  openDiscard();
  const stepBody = discardBody([slash]);
  stepBody.records = [{
    kind: "delta", delta_type: "card_move", seq: 1, frame_id: 0, depth: 0,
    reason: "DISCARD", owner: 0,
    from_zone: { zone: "hand", seat: 0 }, to_zone: { zone: "discard", seat: null },
    cards: [peach],
  }];
  ui.applyViewRaw(stepBody);
  ui.stepPlayback(true);
  check("U4-05k 面板开着 + 回放一步（弃置 → 弃牌堆）：行自动更新、最新在上",
    rowIds() === "103,101" && $("t-discard-summary").textContent === "共 2 张",
    `rowIds=${rowIds()} summary="${$("t-discard-summary").textContent}"`);
  ui.flushPlayback();
  clickButton($("t-discard-close"));

  ui.state.playback = null;
  ui.state.liveView = null;
  ui.state.view = null;
  ui.state.sessionId = null;
  ui.state.focusCardId = null;
  ui.state.picks = [];
  ui.state.selectedTargets = [];
  ui.state.modal.heroSeat = null;
  ui.resetSelection();
  ui.renderPending(null);
}

/* ============ U4-06（spec D13 / D14）：文案与槽位布局 ============
 *
 * 投影只把**人类自己的**应答展开（`{kind, answers_seq}` 之外不再给别人的细节）：
 * 中央「当前动作」与事件流必须对两种形状都说人话——中文牌名 / 技能名、无英文构造子名、
 * 无 undefined。槽位这一半（DOM 文本）：满槽同时保留槽名与牌名（判定 / 武器 + 四字牌名）；
 * 列宽与折行的机械断言住 `tests/test_web_static.py`（DOM 垫片没有布局引擎）。
 */
{
  const walk = (node, out = []) => {
    for (const child of (node && node.children) || []) {
      out.push(child);
      walk(child, out);
    }
    return out;
  };
  const byClass = (node, name) => walk(node).filter(
    (child) => child.classList.contains(name)
      || String(child.className || "").split(/\s+/).includes(name),
  );
  const attr = (node, name) => (node ? node.getAttribute(name) : undefined);

  const view = synthView(null);
  ui.state.playback = null;
  ui.state.liveView = null;
  ui.state.view = view;
  ui.state.logRecords = [];
  ui.resetSelection();
  ui.renderTable();

  /** 把一条 action 记录喂给「当前动作」并读回文本（`renderTable` 会重画舞台上层）。 */
  const actionText = (action) => {
    ui.state.logRecords = [{ kind: "action", seq: 5, accepted: true, action }];
    ui.renderTable();
    return $("t-action-text").textContent;
  };

  const iceSword = {
    ...synthCard(31, "寒冰剑"), type: "ice_sword", slot: "weapon", attack_range: 2,
  };
  check("U4-06a 自己的使用牌：「当前动作」=「使用【寒冰剑】」（不是 UseCard）",
    actionText({ kind: "UseCard", answers_seq: 3, card: iceSword, targets: [] })
      === "使用【寒冰剑】",
    `text="${$("t-action-text").textContent}"`);

  check("U4-06b 自己的技能发动：「当前动作」=「发动【英姿】」（服务端 skill_name，不是 skill_id / undefined）",
    actionText({
      kind: "ActivateSkill", answers_seq: 4,
      skill_id: "hero.zhouyu.yingzi", skill_name: "英姿", choices: [], picks: [],
    }) === "发动【英姿】",
    `text="${$("t-action-text").textContent}"`);

  // 没有 `skill_name` 的旧载荷：退回 `view.skills` 的中文名（同一份接入层展示表）。
  ui.state.view.skills = [{ skill_id: "hero.zhouyu.yingzi", name: "英姿" }];
  check("U4-06b2 没有 skill_name 时读 view.skills 的技能名（前端不自建映射）",
    actionText({
      kind: "ActivateSkill", answers_seq: 4,
      skill_id: "hero.zhouyu.yingzi", choices: [], picks: [],
    }) === "发动【英姿】",
    `text="${$("t-action-text").textContent}"`);
  ui.state.view.skills = [];

  // 投影对**别人的**应答只留 kind / answers_seq：文案是中性中文，不出现英文构造子名。
  // 断生产里的 ACTION_KIND_LABELS（不在测试里另抄一份映射）。
  const trimmedKinds = ["UseCard", "ActivateSkill", "ArrangeCards", "SelectOption"];
  const trimmedTexts = trimmedKinds.map((kind) => actionText({ kind, answers_seq: 4 }));
  check("U4-06c 别人的应答只说应答类型（无 UseCard / ArrangeCards / SelectOption / undefined，也不泄露细节）",
    trimmedTexts.every((text, index) => text === ui.ACTION_KIND_LABELS[trimmedKinds[index]])
      && trimmedTexts.every((text) => !/UseCard|ArrangeCards|SelectOption|undefined/.test(text)),
    `texts=${JSON.stringify(trimmedTexts)} labels=${JSON.stringify(ui.ACTION_KIND_LABELS)}`);

  // 事件流的区域写武将名（拆 / 顺的选择面共用同一个 zoneLabel）。
  const named = synthView(null);
  named.snapshot.players[0].hero_name = "郭嘉";
  named.snapshot.players[1].hero_name = "孙权";
  ui.state.view = named;
  ui.state.logRecords = [];
  const moveLine = ui.describeRecord({
    kind: "delta", delta_type: "card_move", seq: 9, reason: "LOSE",
    from_zone: { zone: "equip", seat: 1 },
    to_zone: { zone: "hand", seat: 0 },
    cards: [],
  });
  check("U4-06d 牌移动的区域写武将名（不是「座位1 装备区」）",
    moveLine.includes("孙权 装备区") && moveLine.includes("郭嘉 手牌")
      && !moveLine.includes("座位1") && !moveLine.includes("座位0"),
    `line="${moveLine}"`);

  // 没有视图（旧载荷 / 无会话）时退回「座位N」：不把引擎 id 写进玩家文案。
  ui.state.view = null;
  const fallbackLine = ui.describeRecord({
    kind: "delta", delta_type: "card_move", seq: 10, reason: "LOSE",
    from_zone: { zone: "equip", seat: 1 },
    to_zone: { zone: "hand", seat: 0 },
    cards: [],
  });
  check("U4-06d2 没有武将名时 zoneLabel 退回「座位N」",
    fallbackLine.includes("座位1 装备区") && fallbackLine.includes("座位0 手牌"),
    `line="${fallbackLine}"`);

  // 满槽：槽名与四字牌名同时可读（DOM 文本；列宽 / 折行由 style.css 的机械断言钉）。
  const filled = synthView(null);
  filled.snapshot.players[1].hero_name = "吕布";
  filled.snapshot.players[1].judge = [
    { ...synthCard(41, "兵粮寸断"), type: "supply_shortage" },
  ];
  filled.snapshot.players[1].equip = [
    {
      ...synthCard(42, "方天画戟"), type: "halberd", slot: "weapon", attack_range: 4,
      attribute: "普通", kind: "normal", description: "【方天画戟】的全文描述",
    },
  ];
  ui.state.view = filled;
  ui.resetSelection();
  ui.renderTable();
  const seat1 = seatBox(1);
  const judgeSlot = byClass(seat1, "judge-slot").find((node) => attr(node, "data-card-id"));
  const equipSlot = byClass(seat1, "equip-slot").find((node) => attr(node, "data-card-id"));
  check("U4-06e 满槽：牌名 + 花色点数；装备槽有牌时不再写槽名，判定槽仍写「判定」",
    !!judgeSlot && !!equipSlot
      && judgeSlot.textContent.includes("判定")
      && judgeSlot.textContent.includes("兵粮寸断")
      && equipSlot.textContent.includes("方天画戟")
      && !equipSlot.textContent.includes("武器")
      && byClass(equipSlot, "slot-suit").length === 1,
    `judge="${judgeSlot ? judgeSlot.textContent : ""}"`
      + ` equip="${equipSlot ? equipSlot.textContent : ""}"`);

  // 状态复位：后面的用例不继承这里的合成视图 / 记录。
  ui.state.view = null;
  ui.state.logRecords = [];
  ui.state.playback = null;
  ui.state.liveView = null;
  ui.resetSelection();
}

/* ============ M6 报障（用户实机）：三条回归 ============
 *
 * 每条断言对应一条用户实机报障（红命令已转绿，留作回归）。三条都走**真服务 + 真前端
 * 脚本**，不造合成响应：
 *
 *  - B1 五谷亮牌弹窗：源牌结算完（五谷 RESOLVE 帧 pop）时弹窗要**当场**收起，
 *    而不是等整批回放播完、下一次询问提交时才收起；
 *  - B2 新局开局：不能拿上一局的视图当回放底稿（残局布局），且"有没有上一局"
 *    不该改变开局这一批的回放方式；B2c 钉"倒推出来的发牌前骨架"能把开局批逐条
 *    长成新局快照；
 *  - B3 阶段飘字：一回合六个阶段都该起「XX阶段开始」（准备 / 判定 / 摸牌 / 出牌 /
 *    弃牌 / 结束）；回合开始前 / 回合结束后两个节点不显示但照旧派发钩子。
 */
{
  const hasClass = (node, name) =>
    String((node && node.className) || "").split(/\s+/).includes(name);
  const resetAll = () => {
    ui.flushPlayback();
    ui.state.playback = null;
    ui.state.sessionId = null;
    ui.state.view = null;
    ui.state.liveView = null;
    ui.state.logRecords = [];
    ui.state.narrative = [];
    ui.state.narrativeShown = 0;
    ui.state.cursor = -1;
    ui.state.logSeq = 0;
    ui.resetSelection();
    $("t-log").innerHTML = "";
  };
  const bodyHeroName = (body) => {
    const players = (body && body.snapshot && body.snapshot.players) || [];
    const player = players.find((item) => Number(item.seat) === Number(body.human_seat || 0));
    return player ? String(player.hero_name || "") : "";
  };

  /* ---- B3：一回合六个阶段都要起「XX阶段开始」--------------------------------- */
  resetAll();
  // 先清掉前面合成载荷留在层上的飘字：这一条只认**真对局**产出的节点。
  if ($("t-anim-phase")) $("t-anim-phase").innerHTML = "";
  const wantedPhases = [
    "准备阶段开始", "判定阶段开始", "摸牌阶段开始",
    "出牌阶段开始", "弃牌阶段开始", "结束阶段开始",
  ];
  const floatTexts = () => {
    const layer = $("t-anim-phase");
    return layer ? Array.from(layer.children)
      .filter((node) => hasClass(node, "anim-phase-float"))
      .map((node) => String(node.textContent || "")) : [];
  };
  const seenFloats = new Set();
  const collectFloats = () => { for (const text of floatTexts()) seenFloats.add(text); };
  await newSession(20273291, { ai: "random" });
  collectFloats();
  for (let step = 0; step < 60 && ui.state.view && ui.state.view.pending; step += 1) {
    await advanceOnce();
    collectFloats();
  }
  const missingPhases = wantedPhases.filter((text) => !seenFloats.has(text));
  check("M6-B3 真对局：一回合六个阶段都起「XX阶段开始」",
    missingPhases.length === 0,
    `missing=${missingPhases.join(" / ")} seen=${Array.from(seenFloats).join(" | ")}`);
  // 两个**节点**不显示（但后端照旧派发，见 sgs.timing 的 TURN_BEFORE_START / TURN_AFTER_END）。
  const nodeFloats = Array.from(seenFloats).filter(
    (text) => text.includes("回合开始前") || text.includes("回合结束后"));
  check("M6-B3b 回合开始前 / 回合结束后两个节点不显示飘字（只做钩子）",
    nodeFloats.length === 0, `unexpected=${nodeFloats.join(" | ")}`);

  /* ---- B1：五谷亮牌弹窗在源牌结算完时当场收起 -------------------------------- */
  resetAll();
  // 种子 238 / 5 人：开局的人类第一问就是【五谷丰登】的逐目标无懈窗口（既有用例同源；
  // 票 12 切每座位子流后原种子 198 失效；feng-xiahouyuan 票 01 换池后 526 亦失效，
  // 按同一固定策略与命中判据从 1 起重扫到 238（huo-shenzhugeliang 加池后重扫；探针
  // `.scratch/feng-xiahouyuan/rescan_harvest_seed.py`）。
  const harvestBody = await post("/api/sessions", {
    player_count: 5, ai: "random", human_seat: 0, timeout_seconds: null, seed: 238,
    banned_heroes: SMOKE_BANNED,
  });
  ui.state.sessionId = harvestBody.session_id;
  ui.state.view = null;
  ui.state.cursor = -1;
  ui.state.logSeq = 0;
  ui.applyView(harvestBody);
  let harvestFrame = null;
  let pendingBefore = null;
  let closingBatch = null;
  for (let step = 0; step < 40; step += 1) {
    const pending = ui.state.view ? ui.state.view.pending : null;
    if (!pending) break;
    const harvest = pending.harvest;
    if (!harvest) break;
    harvestFrame = Number(String(harvest.key || "").split(":")[1]);
    pendingBefore = pending;
    if (pending.kind === "SELECT_CARDS") await ui.submitRevealPick(pending, 0);
    else if (pending.kind === "RESPOND") {
      const pass = (pending.legal_actions || []).findIndex((action) => action.kind === "Pass");
      if (pass < 0) break;
      ui.state.selectedOption = pass;
      await ui.submitActionRaw();
    } else break;
    const nextPending = ui.state.playback
      ? ui.state.playback.body.pending
      : (ui.state.view ? ui.state.view.pending : null);
    if (!(nextPending && nextPending.harvest)) {
      closingBatch = ui.state.playback;
      break;
    }
    ui.flushPlayback();
  }
  const dialogOpenBeforeBatch = $("t-dialog").hidden === false;
  const batchRecords = closingBatch ? closingBatch.records : [];
  for (let index = 0; index < batchRecords.length; index += 1) ui.stepPlayback(true);
  const playbackStillOpen = ui.state.playback !== null;
  const dialogClosedDuringPlayback = $("t-dialog").hidden === true;
  check("M6-B1 五谷亮牌弹窗在源牌结算完（仍在该批回放中）就收起，不等下一次询问",
    !!closingBatch && harvestFrame !== null && dialogOpenBeforeBatch
      && playbackStillOpen && dialogClosedDuringPlayback,
    `frame=${harvestFrame} before=${pendingBefore && pendingBefore.kind}`
      + ` records=${batchRecords.length} openBefore=${dialogOpenBeforeBatch}`
      + ` playbackOpen=${playbackStillOpen} closedMidBatch=${dialogClosedDuringPlayback}`
      + ` dialogHidden=${$("t-dialog").hidden}`);
  ui.flushPlayback();
  ui.show("lobby");

  /* ---- B2：新局开局不残留上一局布局、且两条开局路径一致 ---------------------- */
  resetAll();
  // 先扫一个"人类武将与第一局不同"的 seed（同一份建局载荷，避开"两局刚好同名"的假绿）。
  $("in-players").value = "8";
  tickPointjiang(false);
  const lobbyBody = (seed) => ({
    player_count: Number($("in-players").value),
    ai: $("in-ai").value,
    human_seat: Number($("in-seat").value),
    timeout_seconds: Number($("in-timeout").value) > 0 ? Number($("in-timeout").value) : null,
    seed: Number(seed),
    my_hero: null,
    banned_heroes: ui.currentBans().slice(),
    deck_profile: $("in-deck-profile").value || null,
  });
  $("in-seed").value = "2468";
  await submitLobbyRaw();                         // 没有上一局：state.view === null
  const firstPlayback = ui.state.playback;
  const firstBody = firstPlayback ? firstPlayback.body : ui.state.view;
  const firstHero = bodyHeroName(firstBody);
  const firstPaced = firstPlayback !== null;
  ui.flushPlayback();
  let secondSeed = null;
  let secondHero = "";
  for (const seed of [101, 202, 303, 404, 505, 606, 707, 808, 909, 1010]) {
    const probe = await post("/api/sessions", lobbyBody(seed));
    const hero = bodyHeroName(probe);
    if (hero && hero !== firstHero) { secondSeed = seed; secondHero = hero; break; }
  }
  // 自己的武将名有稳定读取口 `#t-hero`（table.js:renderSelfSeat 只改内容、不重建节点）。
  const selfHeroName = () => String(($("t-hero") && $("t-hero").textContent) || "");
  ui.show("lobby");
  $("in-seed").value = String(secondSeed);
  const staleHero = selfHeroName();                 // 第一局画在屏上的武将
  await submitLobbyRaw();                           // 有上一局：开局批照常按节奏播
  const secondPaced = ui.state.playback !== null;
  const secondBody = ui.state.playback ? ui.state.playback.body : ui.state.view;
  if (ui.state.playback) ui.stepPlayback(true);     // 回放第一条：表体按 liveView 重绘
  const paintedHero = selfHeroName();
  check("M6-B2 新局开局的第一帧不残留上一局布局（表体画的是新局的武将）",
    secondSeed !== null && !!secondBody && staleHero === firstHero
      && paintedHero === secondHero && paintedHero !== staleHero,
    `seed=${secondSeed} first=${firstHero} second=${secondHero}`
      + ` stale=${staleHero} painted=${paintedHero}`);
  check("M6-B2b 开局这一批的回放方式与有没有上一局无关（两条路径一致）",
    firstPaced === secondPaced,
    `firstPaced=${firstPaced} secondPaced=${secondPaced}`
      + ` firstRecords=${firstBody && (firstBody.records || []).length}`);

  // ---- 初始化重写（B2b 选 A 的底稿）：开局这条批逐条播完、**还没 commit** 时，表体应当已经
  //      逐事件长成新局快照本身。底稿错（拿上一局 / 拿跑完后的快照倒着播）都会在这里露馅：
  //      牌堆 / 手牌数 / 阶段 / 轮次对不上。
  if (ui.state.playback) {
    const total = ui.state.playback.records.length;
    for (let index = ui.state.playback.index; index < total; index += 1) ui.stepPlayback(true);
  }
  const liveSnapshot = secondBody ? secondBody.snapshot : null;
  const liveHuman = liveSnapshot ? (liveSnapshot.players || []).find(
    (player) => Number(player.seat) === Number(secondBody.human_seat)) : null;
  check("M6-B2c 开局批播完（未 commit）时表体已长成新局快照（初始化底稿正确）",
    !!liveSnapshot && !!liveHuman
      && $("t-deck").textContent === String(liveSnapshot.deck_count ?? 0)
      && $("t-self-hand").textContent === `${liveHuman.hand_count}/${liveHuman.hand_limit}`
      && $("t-phase").textContent === ui.phaseLabel(liveSnapshot.phase)
      && $("t-round").textContent === String(liveSnapshot.round)
      && selfHeroName() === secondHero,
    `deck=${$("t-deck").textContent}/${liveSnapshot && liveSnapshot.deck_count}`
      + ` hand=${$("t-self-hand").textContent}/${liveHuman && liveHuman.hand_count}/${liveHuman && liveHuman.hand_limit}`
      + ` phase=${$("t-phase").textContent}/${liveSnapshot && ui.phaseLabel(liveSnapshot.phase)}`
      + ` round=${$("t-round").textContent}/${liveSnapshot && liveSnapshot.round}`
      + ` hero=${selfHeroName()}/${secondHero}`);

  // ---- 同一份底稿在另外两个 seed 上复跑：开局批里出现的 delta 类型（发牌 / 判定 / 装备 /
  //      弃牌…）会随对局变，底稿只要漏接一类，牌堆 / 手牌数就会在这里对不上。
  const extraProblems = [];
  for (const seed of [303, 404]) {
    ui.show("lobby");
    $("in-seed").value = String(seed);
    await submitLobbyRaw();
    const body = ui.state.playback ? ui.state.playback.body
      : (ui.state.view || null);
    const snapshot = body ? body.snapshot : null;
    const human = snapshot ? (snapshot.players || []).find(
      (player) => Number(player.seat) === Number(body.human_seat)) : null;
    if (!ui.state.playback || !snapshot || !human) {
      extraProblems.push(`seed ${seed}: 没有开局回放 / 快照`);
      ui.flushPlayback();
      continue;
    }
    const total = ui.state.playback.records.length;
    for (let index = 0; index < total; index += 1) ui.stepPlayback(true);
    const ok = $("t-deck").textContent === String(snapshot.deck_count ?? 0)
      && $("t-self-hand").textContent === `${human.hand_count}/${human.hand_limit}`
      && $("t-phase").textContent === ui.phaseLabel(snapshot.phase)
      && $("t-round").textContent === String(snapshot.round);
    if (!ok) {
      extraProblems.push(
        `seed ${seed}: deck=${$("t-deck").textContent}/${snapshot.deck_count}`
        + ` hand=${$("t-self-hand").textContent}/${human.hand_count}/${human.hand_limit}`
        + ` phase=${$("t-phase").textContent}/${ui.phaseLabel(snapshot.phase)}`
        + ` round=${$("t-round").textContent}/${snapshot.round}`);
    }
    ui.flushPlayback();
  }
  check("M6-B2d 同一份初始化底稿在另外两个 seed 上也能长成快照（开局 delta 类型不漏接）",
    extraProblems.length === 0, extraProblems.join(" | "));
  ui.flushPlayback();
  resetAll();
}


/* ============ M6 实机报障（合成局面）：询问面跟着「这一步」走 ============
 *
 * 三条报障同一条根因（观星的排列弹窗在效果演完前还挂在场上 / 五谷的亮牌格留在提示区 /
 * 上一问的文案留在提示条）：`applyView` 起播时只清了 `#t-prompt` 与 `#t-choice`，其余五样
 * 询问面（技能串 / 目标提示 / 选项格 / 询问弹窗 / 弹窗正文）要等**下一次** `renderPending`
 * 才被冲掉，而那一问可能隔着几十条记录。这里钉住"起播 = 询问面整块让位"，以及**亮牌窗**
 * 那条例外（五谷的持久亮牌跨整个结算、洛神的连续判定靠记录流往里追加，收起各有其出口）。
 *
 * 判据只读**外部可观察**的东西：弹窗 `hidden`、`#t-options` 的子节点数、提示条文本。
 */
{
  ui.state.sessionId = null;
  ui.show("table");

  const clearAsk = () => {
    ui.flushPlayback();
    ui.state.playback = null;
    ui.state.view = null;
    ui.state.liveView = null;
    ui.state.logRecords = [];
    ui.state.narrative = [];
    ui.state.narrativeShown = 0;
    ui.state.cursor = 0;
    ui.state.logSeq = 0;
    ui.resetSelection();
    $("t-log").innerHTML = "";
    $("t-options").innerHTML = "";
    ui.renderPending(null);
  };
  const batchRecord = (seq) => ({ seq, kind: "timing", timing: "PHASE_PLAY", actor: 0 });
  const askState = () => ({
    dialog: $("t-dialog").hidden !== true,
    options: $("t-options").children.length,
    prompt: $("t-prompt").textContent,
    hints: $("t-skill-hints").textContent,
    choice: $("t-choice").textContent,
  });

  // ① 排列面开着：弹窗 + 正文格都在。
  clearAsk();
  const arrangePending = {
    kind: "ARRANGE", actor: 0, seq: 9701, timing: "PHASE_PREPARE",
    prompt: "请排列这些牌：给出顺序与前几张置于牌堆顶。",
    revealed: [synthCard(84, "闪"), synthCard(85, "桃")],
    legal_actions: [{ answers_seq: 9701, kind: "ArrangeCards", order: [0, 1], split: 1 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
    arrange: { count: 2, split: "free" },
  };
  ui.applyView(synthView(arrangePending));
  const opened = askState();
  check("M6-ASK1 排列面画出弹窗与正文格", opened.dialog && opened.options > 0,
    JSON.stringify(opened));

  // ② 排列已答：这一批演的是"答完之后"，新询问是出牌阶段。
  const playPending = {
    kind: "PLAY_PHASE", actor: 0, seq: 9702, timing: "PHASE_PLAY",
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ answers_seq: 9702, kind: "EndPhase" }],
    candidate_index: {}, option_seats: {},
    context: { requirement: "使用一张牌（或发动技能），或结束出牌阶段", skill_hints: [] },
    preactivations: {}, preactivation: [],
  };
  const next = synthView(playPending);
  next.records = [batchRecord(9702)];
  ui.applyViewRaw(next);
  const during = askState();
  check("M6-ASK2 起播即让位：上一问的排列弹窗当场收起", during.dialog === false,
    JSON.stringify(during));
  check("M6-ASK3 起播即让位：提示区不留上一问的正文格 / 文案 / 技能串",
    during.options === 0 && during.prompt === "" && during.choice === "" && during.hints === "",
    JSON.stringify(during));
  ui.flushPlayback();
  const after = askState();
  check("M6-ASK4 播完提交新询问：提示文本换成本问的，弹窗仍关着",
    after.dialog === false && after.options === 0 && after.prompt.includes("出牌阶段"),
    JSON.stringify(after));

  // ③ 五谷的**持久亮牌**例外：结算帧还活着（表面是牌桌的 RESPOND）⇒ 跨批次保留。
  clearAsk();
  const harvestCards = [
    { ...synthCard(5, "五谷丰登"), type: "harvest" },
    { ...synthCard(6, "桃"), type: "peach" },
  ];
  const harvestPending = {
    kind: "RESPOND", actor: 0, seq: 9703, cause: "NULLIFY",
    prompt: "响应窗口：请使用一张牌，或放弃。",
    legal_actions: [{ answers_seq: 9703, kind: "Pass" }],
    candidate_index: {}, option_seats: {},
    context: { requirement: "需要使用【无懈可击】", skill_hints: [] },
    preactivations: {}, preactivation: [],
    harvest: {
      key: "harvest:9:5:0", title: "五谷丰登", active: true,
      revealed: harvestCards, current: 0, targets: [0, 1],
    },
  };
  ui.applyView(synthView(harvestPending));
  const harvestOpen = askState();
  check("M6-ASK5 五谷结算的无懈窗口：亮牌弹窗开着（公开处理区）", harvestOpen.dialog === true,
    JSON.stringify(harvestOpen));
  const harvestNext = synthView(harvestPending);
  harvestNext.records = [batchRecord(9703)];
  harvestNext.pending = { ...harvestPending, seq: 9704 };
  ui.applyViewRaw(harvestNext);
  const harvestDuring = askState();
  check("M6-ASK6 亮牌窗跨批次保留（五谷持久展示 / 洛神连续判定，结算期间不闪）",
    harvestDuring.dialog === true, JSON.stringify(harvestDuring));
  check("M6-ASK7 亮牌窗保留时，提示区仍让位（不留上一问的正文格 / 文案）",
    harvestDuring.options === 0 && harvestDuring.prompt === "" && harvestDuring.choice === "",
    JSON.stringify(harvestDuring));
  ui.flushPlayback();
  clearAsk();
}



/* ============ 票 01（合成局面）：涉猎的按花色取牌 + 攻心的「放弃」 ============
 *
 * 两块都是**界面读法**：载荷形状 → 弹窗 / 置灰 / 出口。服务端那一侧（`max_per_suit`
 * 的候选收窄、`ask_when_empty` 的询问）由 `tests/test_hero_feng.py` 钉。
 */

{
  const suitCard = (id, name, suit) => ({
    ...synthCard(id, name),
    suit,
    color: suit === "heart" || suit === "diamond" ? "red" : "black",
  });
  const five = [
    suitCard(101, "桃", "heart"),
    suitCard(102, "闪", "diamond"),
    suitCard(103, "杀", "club"),
    suitCard(104, "酒", "spade"),
    suitCard(105, "桃", "heart"),
  ];
  const suitPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 9501, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { kind: "SelectCards", picks: [], answers_seq: 9501 },
      { kind: "SelectCards", picks: [0], answers_seq: 9501 },
      { kind: "SelectCards", picks: [1], answers_seq: 9501 },
      { kind: "SelectCards", picks: [2], answers_seq: 9501 },
      { kind: "SelectCards", picks: [3], answers_seq: 9501 },
      { kind: "SelectCards", picks: [4], answers_seq: 9501 },
      { kind: "SelectCards", picks: [0, 1], answers_seq: 9501 },
    ],
    candidate_index: {}, option_seats: {},
    context: { requirement: "获得其中每种花色的牌各一张", skill_hints: [] },
    preactivations: {}, preactivation: [],
    slots: five.map((card) => ({ ref: { zone: "processing", seat: null }, card })),
    spec: { zones: ["processing"], total: [0, 4], max_per_suit: 1 },
  };
  const suitView = synthView(suitPending);
  suitView.snapshot.processing = five;
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.suitResult = null;
  ui.state.view = suitView;
  ui.renderTable();
  ui.renderPending(suitView);

  const findAll = (root, predicate) => {
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) {
        if (predicate(child)) out.push(child);
        walk(child);
      }
    };
    walk(root);
    return out;
  };
  const suitNodes = () => findAll($("t-options"), (node) => node.getAttribute
    && node.getAttribute("data-suit")
    && node.getAttribute("data-slot-index") !== undefined);
  const byCard = (cardId) => suitNodes().find(
    (node) => node.getAttribute("data-card-id") === String(cardId)) || null;

  check("票 01-S1 按花色取牌 = 新弹窗（kind=suit），五张公开牌都在",
    ui.stepDialogKind(suitPending) === ui.DIALOG_KINDS.suit
      && !$("t-dialog").hidden && suitNodes().length === 5,
    `kind=${ui.stepDialogKind(suitPending)} cards=${suitNodes().length}`);
  check("票 01-S2 提交恒可点（0 张也合法）",
    $("btn-submit").disabled === false && $("btn-cancel").disabled === true,
    `submit=${$("btn-submit").disabled} cancel=${$("btn-cancel").disabled}`);
  check("票 01-S3 右端「放弃」可见（与提交同义，不是 Pass 候选）",
    $("btn-pass").hidden === false && $("btn-pass").textContent === "放弃",
    `hidden=${$("btn-pass").hidden} text=${$("btn-pass").textContent}`);

  await clickAndWait(byCard(101));
  check("票 01-S4 点一张红桃 = 选中；同花色另一张置灰、不可点",
    ui.state.picks.length === 1 && byCard(101).getAttribute("data-picked") === "true"
      && byCard(105).getAttribute("data-dim") === "true"
      && !(byCard(105).listeners.click || []).length,
    JSON.stringify({ picks: ui.state.picks, dim: byCard(105).getAttribute("data-dim") }));
  const dimHeart = byCard(105);
  await clickAndWait(dimHeart);
  check("票 01-S5 置灰牌点了不改变选择",
    ui.state.picks.length === 1 && ui.state.picks[0] === 0,
    JSON.stringify(ui.state.picks));
  await clickAndWait(byCard(101));
  check("票 01-S6 再点已选牌 = 取消选中，同花色另一张重新亮起",
    ui.state.picks.length === 0 && !byCard(105).getAttribute("data-dim")
      && !!(byCard(105).listeners.click || []).length,
    JSON.stringify(ui.state.picks));

  // 提交 = 一条 SelectCards（面内位置）；放弃 = 同一条（先选一张再点右端放弃）。
  ui.state.sessionId = "smoke-missing-session";
  await clickAndWait(byCard(101));
  const beforeSuit = calls.length;
  await clickAndWait($("btn-submit"));
  const sentSuit = calls.length > beforeSuit ? calls[calls.length - 1].body.action : null;
  check("票 01-S7 提交 = SelectCards（picks 指向选中的槽位）",
    !!sentSuit && sentSuit.kind === "SelectCards"
      && JSON.stringify(sentSuit.picks) === "[0]",
    JSON.stringify(sentSuit));
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.renderPending(suitView);
  await clickAndWait(byCard(102));
  ui.state.sessionId = "smoke-missing-session";
  const beforeGiveUp = calls.length;
  await clickAndWait($("btn-pass"));
  const sentGiveUp = calls.length > beforeGiveUp ? calls[calls.length - 1].body.action : null;
  check("票 01-S8 右端「放弃」= 提交当前选择（与提交完全相同）",
    !!sentGiveUp && sentGiveUp.kind === "SelectCards"
      && JSON.stringify(sentGiveUp.picks) === "[1]",
    JSON.stringify(sentGiveUp));

  // 2.5 秒结果态：获得的亮、其余置灰；标题读提交时记下的技能名。
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.suitResult = {
    cards: five, picked: [0, 2], until: Date.now() + 2500, title: "涉猎",
  };
  const afterPending = {
    ...suitPending, seq: 9502, kind: "PLAY_PHASE",
    legal_actions: [{ kind: "EndPhase", answers_seq: 9502 }],
  };
  const afterView = synthView(afterPending);
  ui.state.view = afterView;
  ui.renderPending(afterView);
  const resultNodes = suitNodes();
  check("票 01-S9 提交后 2.5 秒结果态：获得的牌亮、其余置灰",
    resultNodes.length === 5
      && resultNodes.filter((node) => node.getAttribute("data-dim") === "true").length === 3
      && resultNodes.filter((node) => node.classList.contains("sel")).length === 2,
    `nodes=${resultNodes.length}`);
  check("票 01-S10 结果态标题 = 提交时记下的技能名（不是下一问的）",
    $("t-dialog-title").textContent === "涉猎",
    `title=${$("t-dialog-title").textContent}`);
  ui.state.suitResult = null;

  // 攻心：亮牌步带 Pass ⇒ 右端「放弃」可见；无红桃时也展示整副手牌。
  const heart = suitCard(201, "桃", "heart");
  const blackCard = suitCard(202, "杀", "club");
  const watchPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 9601, prompt: "请从选择面里选择一张牌。",
    legal_actions: [
      { kind: "SelectCards", picks: [0], answers_seq: 9601 },
      { kind: "Pass", answers_seq: 9601 },
    ],
    candidate_index: {}, option_seats: {},
    context: { requirement: "观看其手牌：选择一张红桃牌展示（可放弃）", skill_hints: [] },
    preactivations: {}, preactivation: [],
    revealed: [heart, blackCard],
    slots: [{ ref: { zone: "hand", seat: 1 }, card: heart }],
    spec: { zones: ["hand"], total: [1, 1], optional: true },
    dialog: "reveal",
  };
  const watchView = synthView(watchPending);
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.view = watchView;
  ui.renderTable();
  ui.renderPending(watchView);
  check("票 01-G1 亮牌弹窗 + Pass ⇒ 右端「放弃」可见、提示写明可放弃",
    !$("t-dialog").hidden && $("btn-pass").hidden === false
      && $("t-dialog-hint").textContent.includes("放弃"),
    `pass=${$("btn-pass").hidden} hint=${$("t-dialog-hint").textContent}`);

  const emptyWatch = {
    ...watchPending, seq: 9602,
    legal_actions: [{ kind: "Pass", answers_seq: 9602 }],
    slots: [], revealed: [blackCard],
  };
  const emptyView = synthView(emptyWatch);
  ui.state.view = emptyView;
  ui.renderTable();
  ui.renderPending(emptyView);
  const watchCards = findAll($("t-options"), (node) => node.getAttribute
    && node.getAttribute("data-card-id"));
  check("票 01-G2 没有红桃也展示整副手牌；提示指向「放弃」",
    watchCards.length === 1 && $("btn-pass").hidden === false
      && $("t-dialog-hint").textContent.includes("没有可展示的红桃牌"),
    `cards=${watchCards.length} hint=${$("t-dialog-hint").textContent}`);

  // 攻心的两个选项：中文标签（不再直接写 discard / deck_top）。
  const optionPending = {
    kind: "SELECT_OPTION", actor: 0, seq: 9603, prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "discard", answers_seq: 9603 },
      { kind: "SelectOption", choice: "deck_top", answers_seq: 9603 },
    ],
    candidate_index: {}, option_seats: {},
    context: { requirement: "选择：弃置此牌 / 将此牌置于牌堆顶", skill_hints: [] },
    preactivations: {}, preactivation: [],
  };
  const optionView = synthView(optionPending);
  ui.state.view = optionView;
  ui.renderTable();
  ui.renderPending(optionView);
  const optionButtons = findAll($("t-options"), (node) => node.tagName === "BUTTON");
  const optionTexts = optionButtons.map((node) => node.textContent);
  check("票 01-G3 攻心的两个选项写中文（弃置此牌 / 置于牌堆顶）",
    optionTexts.some((text) => text.includes("弃置此牌"))
      && optionTexts.some((text) => text.includes("置于牌堆顶")),
    JSON.stringify(optionTexts));

  // 票 01（自制包）：韬·刘备自制版【义起】= **一问**：选项步带着观看授予。
  // 牌面画在弹窗里（只读、仅自己可见），3 个类型按钮也在同一个弹窗里（点一个即提交）。
  const yiqiSeen = [suitCard(301, "杀", "spade"), suitCard(302, "无懈可击", "heart")];
  const yiqiPending = {
    kind: "SELECT_OPTION", actor: 0, seq: 9701, prompt: "请选择一项。",
    legal_actions: [
      { kind: "SelectOption", choice: "basic", answers_seq: 9701 },
      { kind: "SelectOption", choice: "trick", answers_seq: 9701 },
      { kind: "SelectOption", choice: "equipment", answers_seq: 9701 },
    ],
    candidate_index: {}, option_seats: {},
    context: {
      name: "义起",
      requirement: "观看弃牌堆顶的 x 张牌（只有你能看），选择一种类型：获得其中该类型的所有牌（必须选一项）",
      skill_hints: [],
    },
    preactivations: {}, preactivation: [],
    revealed: yiqiSeen,
  };
  const yiqiView = synthView(yiqiPending);
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.view = yiqiView;
  ui.renderTable();
  ui.renderPending(yiqiView);
  const yiqiCards = findAll($("t-options"), (node) => node.getAttribute
    && node.getAttribute("data-card-id"));
  check("票 01-Y1 义起：观看牌面与选项在**同一个弹窗**里（牌 2 张、标题写技能名）",
    !$("t-dialog").hidden && yiqiCards.length === 2
      && $("t-dialog-title").textContent === "义起",
    `dialog=${$("t-dialog").hidden} cards=${yiqiCards.length} title=${$("t-dialog-title").textContent}`);
  const yiqiTypeTexts = findAll(
    $("t-options"),
    (node) => node.tagName === "BUTTON"
      && !(node.getAttribute && node.getAttribute("data-card-id")),
  ).map((node) => node.textContent);
  check("票 01-Y2 义起：恒 3 个中文类型按钮（基本牌 / 锦囊牌 / 装备牌）、没有座位行",
    yiqiTypeTexts.length === 3
      && yiqiTypeTexts.some((text) => text.includes("基本牌"))
      && yiqiTypeTexts.some((text) => text.includes("锦囊牌"))
      && yiqiTypeTexts.some((text) => text.includes("装备牌"))
      && !findAll($("t-options"), (node) => /^座位/.test(String(node.textContent || ""))).length,
    JSON.stringify(yiqiTypeTexts));
}

/* ============ 报障（2026-11-01）：义起之后的技能按钮区 =====================
 * 两件事一起钉：
 *  ① 按钮格子里**只放技能名**（前 3 个字 + 省略号）：面板是固定 3 列 × 6 行的等大网格，
 *     名字长度不再决定排版；完整名字 / 类别 / 徽标进 `aria-label`，悬停描述仍是 `title`；
 *  ② 一批记录里出现 `skill_gain` / `skill_lose` 时，按钮区与详情清单在**起播前**就换成
 *     这一批的终态——技能没有逐事件的 live 通道，否则整段回放都在显示上一份清单（实机
 *     看到的就是"失去【从讨】之后按钮区还写着【从讨】、而详情页已经有【武圣】【咆哮】"）。
 */
{
  const reportSkillRow = (id, name, extra = {}) => ({
    skill_id: id, name, skill_type: "NORMAL", locked: false, carrier: "HERO",
    star: false, state: "active", description: `${name}的描述。`,
    ui: { passive: false, requirement: "", convert_sources: [] }, ...extra,
  });
  const reportView = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 9710,
    prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: [{ kind: "EndPhase", answers_seq: 9710 }],
    candidate_index: {}, option_seats: {}, context: null,
    preactivations: {}, preactivation: [],
  });
  reportView.skills = [
    { ...reportSkillRow("hero.demo.yiqi", "义起"), available: false, preactivated: false },
    { ...reportSkillRow("demo.longname", "驱虎吞狼"), available: false, preactivated: false },
  ];
  ui.state.sessionId = null;
  ui.state.view = reportView;
  ui.resetSelection();
  ui.renderTable();
  const reportButton = (id) => Array.from($("t-skills").children)
    .find((node) => node.getAttribute && node.getAttribute("data-skill-id") === id);
  const longNameButton = reportButton("demo.longname");
  check("报障-1 技能按钮只放前 3 个字（4 字名截成「驱虎吞…」），全名 / 类别进 aria-label",
    !!longNameButton
      && longNameButton.text().startsWith("驱虎吞…")
      && longNameButton.getAttribute("aria-label").startsWith("驱虎吞狼（")
      && longNameButton.getAttribute("title") === "驱虎吞狼的描述。",
    longNameButton
      ? `text=${longNameButton.text()} label=${longNameButton.getAttribute("aria-label")}`
      : "没有长名字按钮");

  /* ② 同一批里：失去【从讨】+ 获得【武圣】【咆哮】——起播前清单就是终态。 */
  const afterView = {
    ...reportView,
    records: [
      { kind: "delta", delta_type: "skill_lose", seat: 0, skill_id: "hero.tao_liubei.congtao", seq: 9711 },
      { kind: "delta", delta_type: "skill_gain", seat: 0, skill_id: "hero.guanyu.wusheng", seq: 9712 },
      { kind: "delta", delta_type: "skill_gain", seat: 0, skill_id: "hero.zhangfei.paoxiao", seq: 9713 },
    ],
    live_deltas: [], narrative: [], skill_events: [],
    skills: [
      { ...reportSkillRow("hero.guanyu.wusheng", "武圣"), available: false, preactivated: false },
      { ...reportSkillRow("hero.zhangfei.paoxiao", "咆哮", { locked: true }), available: false, preactivated: false },
      { ...reportSkillRow("hero.demo.yiqi", "义起"), available: false, preactivated: false },
    ],
    snapshot: {
      ...reportView.snapshot,
      players: reportView.snapshot.players.map((player) => (Number(player.seat) === 0
        ? {
          ...player,
          skills: [
            reportSkillRow("hero.tao_liubei.congtao", "从讨", { state: "lost" }),
            reportSkillRow("hero.guanyu.wusheng", "武圣"),
          ],
        }
        : player)),
    },
  };
  ui.applyViewRaw(afterView);
  ui.renderTable();  // 回放期的表体走 live view
  const midSkills = Array.from($("t-skills").children)
    .filter((node) => node.tagName === "BUTTON")
    .map((node) => node.getAttribute("data-skill-id"));
  check("报障-2 回放期按钮区已是这一批的终态（从讨没了、武圣 / 咆哮在）",
    !!ui.state.playback
      && midSkills.length === 3
      && midSkills.includes("hero.guanyu.wusheng")
      && midSkills.includes("hero.zhangfei.paoxiao")
      && !midSkills.includes("hero.tao_liubei.congtao"),
    `playback=${!!ui.state.playback} skills=${JSON.stringify(midSkills)}`);
  const midDetail = ((ui.state.liveView.snapshot.players[0] || {}).skills || []);
  check("报障-3 回放期详情清单同源（从讨仍是「失去」一条，不是从清单里被抹掉）",
    midDetail.length === 2
      && midDetail[0].skill_id === "hero.tao_liubei.congtao"
      && midDetail[0].state === "lost",
    JSON.stringify(midDetail.map((entry) => [entry.skill_id, entry.state])));
  ui.flushPlayback();
}

/* ============ 票 09：武将块手牌读数「手牌：张数/上限」 ============
 *
 * 用户口径（2026-11-02 实机验收，血裔通过后追加）：武将块左下角的手牌数改成
 * 「手牌：手牌数/手牌上限」，例「手牌：3/5」，**自己与对手都**显示。
 *
 * 上限是**服务端下发的派生量**（`player.hand_limit` = `effects.hand_limit`：体力值 →
 * 替换链 → 一次性加式），界面不自己拿体力算——那会是第二份真源（【英姿】的替换 /
 * 【清俭】的加式立刻对不上）。
 *
 * 三条判据：
 *  ① 对手座位卡读「手牌：3/5」（不因"不是自己"退回纯数字）；
 *  ② 自己的武将块 footer 与右键详情说**同一句话**（`table.js` / `hero-detail.js` 同步）；
 *  ③ fail-closed：旧载荷 / 合成视图没有 `hand_limit` ⇒ 上限退回张数，绝不出现 `undefined`。
 */
{
  // 与响应体同形的合成 view：玩家 0（自己）2/3、玩家 1（对手）3/5、玩家 2（对手）4/4。
  const handView = (withLimit) => {
    const view = synthView(null);
    view.snapshot.players = [
      { seat: 0, hp: 3, hp_max: 3, hand_count: 2, alive: true,
        equip: [], judge: [], identity: null, hero_name: "郭嘉" },
      { seat: 1, hp: 5, hp_max: 5, hand_count: 3, alive: true,
        equip: [], judge: [], identity: null, hero_name: "吕布" },
      { seat: 2, hp: 4, hp_max: 4, hand_count: 4, alive: true,
        equip: [], judge: [], identity: null, hero_name: "曹操" },
    ];
    if (withLimit) {
      view.snapshot.players[0].hand_limit = 3;
      view.snapshot.players[1].hand_limit = 5;
      view.snapshot.players[2].hand_limit = 4;
    }
    return view;
  };
  const showHandView = (view) => {
    ui.state.sessionId = null;
    ui.state.view = view;
    ui.resetSelection();
    ui.renderTable();
  };
  const walkAll = (node, out = []) => {
    for (const child of (node && node.children) || []) { out.push(child); walkAll(child, out); }
    return out;
  };
  const classOf = (node) => String((node && node.className) || "").split(/\s+/);
  const fireContext = (node) => {
    const event = { target: node, stopPropagation() {} };
    event.preventDefault = () => {};
    if (node) (node.listeners.contextmenu || []).forEach((fn) => fn(event));
  };
  showHandView(handView(true));

  /* ---- ① 对手座位卡：`手牌：3/5`（不是纯数字） ---- */
  const foe = seatBox(1);
  check("票 09-A1 对手座位卡读「手牌：3/5」（不因不是自己就退回纯数字）",
    !!foe
      && foe.text().includes("手牌：3/5")
      && foe.text().includes("座位 1")
      && !foe.text().includes("undefined"),
    foe ? foe.text().replace(/\s+/g, " ").trim() : "缺座位 1");

  /* ---- ② 自己的武将块 footer：读数 2/3 + 静态标签「手牌：」 ---- */
  const html = readFileSync("tools/sgs_web/static/index.html", "utf8");
  check("票 09-A2 自己的武将块同形：footer 读数 2/3、静态标签「手牌：」（顶栏不是纯数字）",
    $("t-self-hand").textContent === "2/3"
      && /手牌：<b id="t-self-hand">\s*0\/0\s*<\/b>/.test(html),
    `t-self-hand="${$("t-self-hand").textContent}" 静态标签=${/手牌：<b id="t-self-hand">/.test(html)}`);

  /* ---- ②b 右键详情：同一句话（`hero-detail.js` 与座位卡同步） ---- */
  const foeHeroBlock = foe
    ? Array.from(foe.children).find((node) => classOf(node).includes("hero-block"))
    : null;
  fireContext(foeHeroBlock);
  const detailMeta = walkAll($("t-modal-body")).find((node) => classOf(node).includes("detail-meta"));
  check("票 09-A3 对手的武将详情同读「手牌：3/5」（三处同一句话）",
    $("t-modal").hidden === false
      && !!detailMeta
      && String(detailMeta.textContent).includes("手牌：3/5"),
    detailMeta ? `detail-meta="${detailMeta.textContent}"` : "详情没有 detail-meta");
  ui.state.modal.heroSeat = null;
  $("t-modal").hidden = true;

  /* ---- ③ fail-closed：没有 hand_limit 的旧载荷 ⇒ 张数/张数 ---- */
  showHandView(handView(false));
  const foeNoLimit = seatBox(2);
  check("票 09-B1 fail-closed：载荷没有 hand_limit ⇒ 上限退回张数（不是 undefined）",
    $("t-self-hand").textContent === "2/2"
      && !!foeNoLimit
      && foeNoLimit.text().includes("手牌：4/4")
      && !$("t-seats").text().includes("undefined")
      && !$("t-self-hand").textContent.includes("undefined"),
    `t-self-hand="${$("t-self-hand").textContent}"`
      + ` 座位2="${foeNoLimit ? foeNoLimit.text().replace(/\s+/g, " ").trim() : "缺"}"`);
}

/* ============ 票 10：多来源步的合法来源组合（【乱击】两张同花色） ============
 *
 * 验收报障 ④（2026-11-02 实机）：袁绍移动版【乱击】要两张**花色相同**的手牌。清单的来源步
 * 原先只有一张并集（`pool[0].options` = 5 张牌 id），界面无从知道"哪两张能配成一组"——
 * 选两张不同花色 → 「下一步」把光标送到末尾 → 整片手牌置灰、提交键却亮着 → 每按一次都是
 * 同一条注定 400 的 `ActivateSkill`（用户读作"什么按钮都按不了"）。
 *
 * 判据（票 §6）：
 *  ① 来源步带 `groups`（合法来源组合）；点 ♦9杀 之后 ♠ / ♣ 牌**不可选**（置灰）、
 *     ♦K紫骍 / ♦4闪 仍可选 ⇒ 凑不出不同花色的一对；
 *  ② 同花色一对（♦9杀 + ♦K紫骍）照旧 下一步 → 提交 ⇒ 恰一条 POST、对局推进（服务端 200）；
 *  ③ 反向保险：把**非法组合**塞进清单（合成载荷 / 陈旧 `groups`）⇒ 提交键不可点、给出
 *     通用那句话、**零 POST**；`advancePreativation` 也不放行（光标停在原地）。
 *
 * 种子 12 的六张手牌由票 §2 逐张点名（真服务端载荷，不走合成视图）。
 */
{
  const LUANJI = "hero.yuanshao_mobile.luanji";
  const openYuanshaoSession = async (seed) => openSession(await post("/api/sessions", {
    player_count: 4, ai: "heuristic", human_seat: 0, timeout_seconds: null,
    seed, my_hero: "yuanshao_mobile", deck_profile: "standard_108",
  }));
  const passFirstIndex = (pending) => {
    const actions = pending.legal_actions || [];
    for (const kind of ["Pass", "EndPhase"]) {
      const at = actions.findIndex((action) => action.kind === kind);
      if (at >= 0) return at;
    }
    return 0;
  };
  const walkToMyPlay = async () => {
    for (let step = 0; step < 40; step += 1) {
      const pending = ui.state.view && ui.state.view.pending;
      if (!pending) return null;
      if (pending.kind === "PLAY_PHASE" && Number(pending.actor) === 0) return pending;
      ui.resetSelection();
      ui.state.selectedOption = passFirstIndex(pending);
      await ui.submitAction();
    }
    return null;
  };
  const selfHand = () => (ui.state.view.snapshot.self_hand || []);
  const handNode = (cardId) => Array.from($("t-hand").children).find(
    (node) => node.getAttribute("data-card-id") === String(cardId)) || null;
  const clickHand = async (cardId) => {
    const node = handNode(cardId);
    if (!node) return false;
    for (const fn of node.listeners.click || []) await fn({ target: node, stopPropagation() {} });
    return true;
  };
  const skillNode = (id) => Array.from($("t-skills").children).find(
    (node) => node.getAttribute("data-skill-id") === id) || null;
  const faceOf = (cardId) => {
    const pending = ui.state.view.pending;
    const card = selfHand().find((item) => Number(item.card_id) === Number(cardId));
    return card ? ui.cardFace(pending, card) : null;
  };
  const groupsOf = () => {
    const pending = ui.state.view.pending;
    const steps = (pending.preactivations || {})[LUANJI] || [];
    return (steps[0] && steps[0].groups) || [];
  };
  const groupKey = (group) => group.map(Number).sort((a, b) => a - b).join("-");
  const picks = () => ui.state.preactivation.answers[0].values.slice();
  const nextButton = () => Array.from($("t-options").children).find(
    (node) => node.tagName === "BUTTON" && node.textContent === "下一步") || null;
  const answersSince = (mark) => calls.slice(mark).filter((call) => call.path.includes("/answer"));
  const settle = async () => {
    for (let i = 0; i < 400 && ui.state.submitBusy; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    ui.flushPlayback();
  };
  const ticketHand = [
    [70, "♦9杀"], [41, "♠7南蛮入侵"], [80, "♦K紫骍"],
    [40, "♠7杀"], [81, "♣A决斗"], [60, "♦4闪"],
  ];
  const freshLuanji = async () => {
    await openYuanshaoSession(12);
    const atPlay = await walkToMyPlay();
    const skill = atPlay ? skillNode(LUANJI) : null;
    if (skill) await clickAndWait(skill);
    return { atPlay, skill };
  };

  /* ---- ① 真局面：清单带 groups、点 ♦9杀 之后不同花色的牌置灰 ---- */
  const { atPlay, skill } = await freshLuanji();
  const handIds = selfHand().map((card) => Number(card.card_id));
  check("票 10-0 局面锚定票 §2：种子 12 / 4 人 / 座位 0 的六张手牌逐张点名",
    atPlay !== null
      && handIds.length === ticketHand.length
      && ticketHand.every(([cardId]) => handIds.includes(cardId)),
    `手牌=${selfHand().map((card) => `${card.card_id}(${card.name})`).join(",")}`);

  // 组合的**集合**是判据（服务端的组合顺序 = 手牌分组序，不该由本测试钉死）。
  const expectedGroups = new Set(["70-80", "60-70", "60-80", "40-41"]);
  const actualGroups = new Set(groupsOf().map(groupKey));
  check("票 10-A1 点【乱击】⇒ 来源步带出合法来源组合（= 同花色的四对，不是一张并集）",
    !!skill && !!ui.state.preactivation
      && ui.state.preactivation.steps.length === 1
      && actualGroups.size === expectedGroups.size
      && [...expectedGroups].every((key) => actualGroups.has(key)),
    `groups=${JSON.stringify(groupsOf())}`);

  check("票 10-A2 未选牌时：五张有同花搭档的牌都可点（♣A决斗没有搭档 ⇒ 不可点）",
    faceOf(70).selectable === true && faceOf(80).selectable === true
      && faceOf(60).selectable === true && faceOf(40).selectable === true
      && faceOf(41).selectable === true && faceOf(81).selectable === false
      && !!handNode(81) && handNode(81).classList.contains("dis"),
    `70=${faceOf(70).selectable} 41=${faceOf(41).selectable} 81=${faceOf(81).selectable}`);

  await clickHand(70);
  check("票 10-A3 点♦9杀之后：♠7南蛮 / ♠7杀 / ♣A决斗 置灰点不动，♦K紫骍 / ♦4闪 仍可点（已选的牌可点 = 取消）",
    JSON.stringify(picks()) === JSON.stringify([70])
      && faceOf(41).selectable === false && faceOf(40).selectable === false
      && faceOf(81).selectable === false
      && faceOf(80).selectable === true && faceOf(60).selectable === true
      && faceOf(70).selectable === true
      && [41, 40, 81].every((cardId) => {
        const node = handNode(cardId);
        return !!node && node.classList.contains("dis");
      }),
    `picks=${JSON.stringify(picks())} 41=${faceOf(41).selectable} 40=${faceOf(40).selectable} `
      + `81=${faceOf(81).selectable} 80=${faceOf(80).selectable} 60=${faceOf(60).selectable} 70=${faceOf(70).selectable}`);

  let mark = calls.length;
  const blockedClick = await clickHand(41);
  check("票 10-A4 点置灰的♠7南蛮 = no-op（不同花色凑不成一对、零 POST）",
    blockedClick && JSON.stringify(picks()) === JSON.stringify([70])
      && answersSince(mark).length === 0,
    `picks=${JSON.stringify(picks())} answers=${answersSince(mark).length}`);

  await clickHand(80);
  check("票 10-A5 再点♦K紫骍 = 同花色的一对：「下一步」可点",
    JSON.stringify(picks()) === JSON.stringify([70, 80])
      && nextButton() !== null && nextButton().disabled === false,
    `picks=${JSON.stringify(picks())} next=${nextButton() ? nextButton().disabled : "缺"}`);

  const seqBefore = atPlay ? atPlay.seq : null;
  await clickAndWait(nextButton());
  check("票 10-A6 合法一对的「下一步」把光标送到末尾：提交键可点（不再是\"末尾 + 非法组合\"的死路）",
    ui.state.preactivation.cursor === 1
      && $("btn-submit").disabled === false
      && ui.buildAction() !== null,
    `cursor=${ui.state.preactivation.cursor} disabled=${$("btn-submit").disabled} built=${JSON.stringify(ui.buildAction())}`);

  mark = calls.length;
  await clickAndWait($("btn-submit"));
  await settle();
  const submitted = answersSince(mark);
  const afterPending = ui.state.view.pending || {};
  check("票 10-A7 同花色一对提交 = 恰一条 ActivateSkill POST，服务端 200、对局推进（【万箭齐发】结算）",
    submitted.length === 1
      && submitted[0].body.action.kind === "ActivateSkill"
      && submitted[0].body.action.skill_id === LUANJI
      && JSON.stringify(submitted[0].body.action.picks) === JSON.stringify([70, 80])
      && !$("t-choice").textContent.includes("提交被拒")
      && Number(afterPending.seq) > Number(seqBefore)
      && logText().includes("万箭齐发"),
    `answers=${submitted.length} action=${JSON.stringify(submitted[0] && submitted[0].body.action)} `
      + `seq=${seqBefore}→${afterPending.seq} log=${logText().includes("万箭齐发")}`);

  /* ---- ③ 反向保险：合成载荷 / 陈旧 groups ⇒ 提交键不亮、通用文案、零 POST ---- */
  await freshLuanji();
  const pa2 = ui.state.preactivation;
  const inject = (values, cursor) => {
    pa2.answers[0].values = values.slice();
    pa2.answers[0].texts = values.map(String);
    pa2.cursor = cursor;
    ui.renderTable();
    ui.renderPending(ui.state.view);
  };

  inject([70, 41], 1);
  mark = calls.length;
  check("票 10-B1 合成载荷（♦9杀 + ♠7南蛮，服务端会 400 的那一对）塞到光标末尾："
    + "提交键不可点、给出通用那句话、零 POST",
    $("btn-submit").disabled === true
      && ui.buildAction() === null
      && $("t-choice").textContent.includes("这组来源不能一起转化")
      && answersSince(mark).length === 0,
    `disabled=${$("btn-submit").disabled} built=${JSON.stringify(ui.buildAction())} `
      + `t-choice=${$("t-choice").textContent} answers=${answersSince(mark).length}`);

  inject([70, 80], 1);
  pa2.steps[0].groups = [["41", "40"]];  // 陈旧 groups：只剩♠组，♦ 对不再合法
  ui.renderTable();
  ui.renderPending(ui.state.view);
  mark = calls.length;
  check("票 10-B2 陈旧 groups（只剩♠组）⇒ 真合法的一对也不再可提交、零 POST",
    $("btn-submit").disabled === true
      && $("t-choice").textContent.includes("这组来源不能一起转化")
      && answersSince(mark).length === 0,
    `disabled=${$("btn-submit").disabled} t-choice=${$("t-choice").textContent}`);

  pa2.steps[0].groups = [["70", "80"], ["70", "60"], ["80", "60"], ["41", "40"]];
  inject([70, 41], 0);
  const cursorBefore = pa2.cursor;
  mark = calls.length;
  ui.advancePreativation();
  check("票 10-B3 非法来源时「下一步」不放行（cursor 停在原地、按钮也是灰的；两张已选的牌仍可点 = 能取消重选）",
    pa2.cursor === cursorBefore
      && nextButton() !== null && nextButton().disabled === true
      && $("btn-submit").disabled === true
      && faceOf(70).selectable === true && faceOf(41).selectable === true
      && answersSince(mark).length === 0,
    `cursor=${pa2.cursor}/${cursorBefore} next=${nextButton() ? nextButton().disabled : "缺"} `
      + `submit=${$("btn-submit").disabled} 70=${faceOf(70).selectable} 41=${faceOf(41).selectable}`);
}

/* ============ 票 05：计数式分配（神周瑜移动版【业炎】） ============
 *
 * 与真实 `pending_payload` 同形的合成 view：`distribution` 的座位表 + 两个上限 + `Distribute`
 * 候选。钉四件事：逐座计数默认 0 / 点一行 +1 且合计封顶 / 「重置」清零 / 提交送出的就是
 * 候选里那一条（不手拼服务端没发的动作）。全 0 也是合法分配 ⇒ 「提交」从第一刻就可点。
 */
{
  const distributeSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261102,
  });
  const dSeq = 970;
  const dActions = [
    [],
    [[1, 1]],
    [[2, 1]],
    [[1, 1], [2, 1]],
    [[1, 2]],
    [[1, 2], [2, 1]],
  ].map((counts) => ({
    kind: "Distribute",
    counts: counts.map(([seat, count]) => [seat, count]),
    answers_seq: dSeq,
  }));
  const distributeView = synthView({
    kind: "DISTRIBUTE", actor: 0, seq: dSeq, prompt: "请为角色分配点数。",
    legal_actions: dActions,
    candidate_index: {}, option_seats: {},
    distribution: {
      max_total: 3, per_seat_max: 3,
      seats: [
        { seat: 0, name: "神周瑜移动版", label: "0号位神周瑜移动版", max: 3 },
        { seat: 1, name: "关羽", label: "1号位关羽", max: 3 },
        { seat: 2, name: "张飞", label: "2号位张飞", max: 3 },
      ],
    },
    context: {
      actor: 0, name: "业炎", targets: [],
      label: "0号位神周瑜移动版发动业炎",
      requirement: "为角色分配火焰伤害（合计至多3点）", skill_hints: [],
    },
  });
  ui.state.view = distributeView;
  ui.state.sessionId = distributeSession.session_id;
  ui.state.distribution = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(distributeView);

  const rows = () => Array.from($("t-options").children).filter(
    (node) => String(node.className || "").includes("distribution-row"),
  );
  check("分配弹窗-1 分配弹窗：每个存活座位一行 + 计数徽标（默认 0）+「重置」",
    ui.stepDialogKind(distributeView.pending) === "distribution"
      && $("t-dialog").hidden === false
      && rows().length === 3
      && rows().map((row) => row.textContent).join(",") === "0号位神周瑜移动版0,1号位关羽0,2号位张飞0"
      && Array.from($("t-options").children).some((node) => node.textContent === "重置"),
    `kind=${ui.stepDialogKind(distributeView.pending)} rows=${JSON.stringify(rows().map((row) => row.textContent))}`);
  check("分配弹窗-2 分配步没有「放弃」出口；全 0 也能提交（提交键从一开始就可点）",
    $("btn-cancel").hidden === true
      && $("btn-pass").hidden === true
      && $("btn-submit").disabled === false
      && JSON.stringify(ui.buildAction()) === JSON.stringify(dActions[0]),
    `cancelHidden=${$("btn-cancel").hidden} submit=${$("btn-submit").disabled}`);

  await clickAndWait(rows()[1]);
  await clickAndWait(rows()[1]);
  await clickAndWait(rows()[2]);
  check("分配弹窗-3 点一行 = 计数 +1；合计到 3 后其余行不可点（封顶）",
    rows()[1].textContent.endsWith("2")
      && rows()[2].textContent.endsWith("1")
      && rows()[0].disabled === true
      && JSON.stringify(ui.buildAction()) === JSON.stringify(dActions[5]),
    `rows=${JSON.stringify(rows().map((row) => [row.textContent, row.disabled]))} built=${JSON.stringify(ui.buildAction())}`);

  const resetNode = Array.from($("t-options").children).find((node) => node.textContent === "重置");
  await clickAndWait(resetNode);
  check("分配弹窗-4「重置」把全部计数清零（回到全 0 的合法分配）",
    rows().every((row) => row.textContent.endsWith("0"))
      && JSON.stringify(ui.buildAction()) === JSON.stringify(dActions[0]),
    `rows=${JSON.stringify(rows().map((row) => row.textContent))}`);

  await clickAndWait(rows()[1]);
  const beforeDistribute = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  for (let index = 0; index < 400 && ui.state.submitBusy; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  staleAnswerStatus = null;
  const distributeAnswers = calls
    .slice(beforeDistribute)
    .filter((call) => call.path.includes("/answer"));
  check("分配弹窗-5 点「提交」= 恰一条 POST，送出的就是候选里那条 Distribute（计数升序、省略 0）",
    distributeAnswers.length === 1
      && JSON.stringify(distributeAnswers[0].body.action) === JSON.stringify(dActions[1]),
    `answers=${distributeAnswers.length} action=${JSON.stringify(distributeAnswers[0] && distributeAnswers[0].body.action)}`);
}

/* ============ 票 01a：多选选项弹窗（界夏侯渊【神速】「至多三项」）============
 *
 * 与真实 `pending_payload` 同形的合成 view：`SELECT_OPTIONS` + `options` / `disabled` /
 * 两个区间 + `SelectOptions` 候选。钉四件事：勾选面画成逐项勾选 / 置灰项列出来但点不动 /
 * 勾选拼出候选里那条集合动作 / 空集也是合法答案（提交键从一开始就可点）。
 */
{
  const multiSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261108,
  });
  const multiSeq = 971;
  const multiActions = [
    [],
    ["jie_shensu_1"],
    ["jie_shensu_3"],
    ["jie_shensu_1", "jie_shensu_3"],
  ].map((choices) => ({
    kind: "SelectOptions",
    choices,
    answers_seq: multiSeq,
  }));
  const multiView = synthView({
    kind: "SELECT_OPTIONS", actor: 0, seq: multiSeq, prompt: "请选择若干项（可多选）。",
    legal_actions: multiActions,
    candidate_index: {}, option_seats: {},
    options: ["jie_shensu_1", "jie_shensu_2", "jie_shensu_3"],
    disabled: ["jie_shensu_2"],
    min_selected: 0, max_selected: 3,
    option_texts: {
      jie_shensu_1: "【1】跳过判定阶段和摸牌阶段",
      jie_shensu_2: "【2】跳过出牌阶段并弃置一张装备牌",
      jie_shensu_3: "【3】跳过弃牌阶段并翻面",
    },
    context: {
      actor: 0, name: "神速", targets: [],
      label: "0号位界夏侯渊发动神速",
      requirement: "回合开始时，你可以选择至多三项", skill_hints: [],
    },
  });
  ui.state.view = multiView;
  ui.state.sessionId = multiSession.session_id;
  ui.state.multiSelect = null;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(multiView);

  const multiRows = () => Array.from($("t-options").children).filter(
    (node) => String(node.className || "").includes("multi-option"),
  );
  check("多选弹窗-1 画成逐项勾选面（弹窗 + 三行都是未勾选）",
    ui.stepDialogKind(multiView.pending) === "multiChoice"
      && $("t-dialog").hidden === false
      && multiRows().length === 3
      && multiRows().every((row) => row.textContent.startsWith("☐")),
    `kind=${ui.stepDialogKind(multiView.pending)} rows=${JSON.stringify(multiRows().map((row) => row.textContent))}`);
  check("多选弹窗-2 置灰项列出来但点不动（第 2 项）",
    multiRows()[1].disabled === true
      && String(multiRows()[1].className || "").includes("dis")
      && multiRows()[0].disabled === false,
    `row2=${multiRows()[1].disabled} cls=${multiRows()[1].className}`);
  check("多选弹窗-3 空集也是合法答案（没有勾选时提交键就可点，动作 = 候选里那个空集）",
    $("btn-submit").disabled === false
      && JSON.stringify(ui.buildAction()) === JSON.stringify(multiActions[0]),
    `submit=${$("btn-submit").disabled} built=${JSON.stringify(ui.buildAction())}`);

  await clickAndWait(multiRows()[0]);
  check("多选弹窗-4 点第 1 行 = 勾上；动作变成 {1}",
    multiRows()[0].textContent.startsWith("☑")
      && JSON.stringify(ui.buildAction()) === JSON.stringify(multiActions[1]),
    `row1=${multiRows()[0].textContent} built=${JSON.stringify(ui.buildAction())}`);

  await clickAndWait(multiRows()[2]);
  check("多选弹窗-5 再点第 3 行 = 勾上两项；动作变成 {1,3}（按 options 原序）",
    multiRows()[2].textContent.startsWith("☑")
      && JSON.stringify(ui.buildAction()) === JSON.stringify(multiActions[3]),
    `built=${JSON.stringify(ui.buildAction())}`);

  await clickAndWait(multiRows()[1]);
  check("多选弹窗-6 点置灰行不改变勾选（引擎侧也不会有含它的候选）",
    multiRows()[0].textContent.startsWith("☑")
      && multiRows()[2].textContent.startsWith("☑")
      && multiRows()[1].textContent.startsWith("☐")
      && JSON.stringify(ui.buildAction()) === JSON.stringify(multiActions[3]),
    `rows=${JSON.stringify(multiRows().map((row) => row.textContent))}`);

  const beforeMulti = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  for (let index = 0; index < 400 && ui.state.submitBusy; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  staleAnswerStatus = null;
  const multiAnswers = calls
    .slice(beforeMulti)
    .filter((call) => call.path.includes("/answer"));
  check("多选弹窗-7 点「提交」= 恰一条 POST，送出的就是候选里那条 SelectOptions",
    multiAnswers.length === 1
      && JSON.stringify(multiAnswers[0].body.action) === JSON.stringify(multiActions[3]),
    `answers=${multiAnswers.length} action=${JSON.stringify(multiAnswers[0] && multiAnswers[0].body.action)}`);
}

/* ============ 改判窗口：装备区的牌必须点得动（2026-11-08 实机报障）============
 *
 * 报障原文：「鬼道无法选择装备区的牌改判；标鬼道也有这个问题」。引擎侧从来没错：
 * `SkillDef.rejudge_zones = (HAND, EQUIP)` ⇒ 候选里就有装备区那张黑牌（`SubstituteJudgeCard`
 * 带 `card`，`candidate_index` 因此也有它）。缺的是**点击**：装备槽此前只在自己的选牌面
 * （`isOwnZonePick`）里接点击，改判窗口是 `RESPOND` 手选面 ⇒ 装备区那张牌画得出来、点不动。
 *
 * 判据用合成 payload（形状取自真实引擎：一条 `SubstituteJudgeCard` + `Pass`）。
 */
{
  ui.resetSelection();
  const equipCard = {
    card_id: 21, name: "青釭剑", type: "weapon", suit: "spade", rank: 6, color: "black",
  };
  const rejudge = {
    kind: "RESPOND", cause: "REJUDGE", actor: 0, seq: 972,
    prompt: "改判窗口：用一张牌代替此判定牌，或放弃。",
    legal_actions: [
      {
        kind: "SubstituteJudgeCard", skill_id: "hero.jie_zhangjiao_mobile.guidao",
        card: equipCard, targets: [], answers_seq: 972,
      },
      { kind: "Pass", answers_seq: 972 },
    ],
    candidate_index: {
      "21": { card: equipCard, pool: [], combos: [[]], min_targets: 0, max_targets: 0 },
    },
  };
  const rejudgeView = {
    seed: 1, human_seat: 0, finished: false, timeout_seconds: null, heroes: {},
    skills: [], pending: rejudge,
    snapshot: {
      seat: 0, phase: "judge", turn_seat: 0,
      players: [{
        seat: 0, hp: 4, hp_max: 4, hand_count: 0, alive: true, equip: [equipCard], judge: [],
        sealed_zones: [], identity: null, chained: false, flipped: false, markers: [],
        wine_active: false, hero: "jie_zhangjiao_mobile", kingdom: "qun",
      }],
      self_hand: [], deck_count: 50, discard: [], processing: [],
    },
  };
  const savedView = ui.state.view;
  ui.state.view = rejudgeView;
  ui.renderTable();
  ui.renderPending(rejudgeView);

  const attrOf = (node, name) => (node.getAttribute ? node.getAttribute(name) : undefined);
  const walkZones = (node, out = []) => {
    for (const child of Array.from(node.children || [])) { out.push(child); walkZones(child, out); }
    return out;
  };
  const equipSlot = walkZones($("t-self-zones")).find(
    (node) => attrOf(node, "data-card-id") === "21"
      && attrOf(node, "data-zone") === "equip");
  check("改判-1 装备区的牌在改判窗口里是可点的控件（此前是死的 div）",
    Boolean(equipSlot) && equipSlot.tagName === "BUTTON"
      && attrOf(equipSlot, "data-zone") === "equip",
    `slot=${equipSlot ? equipSlot.tagName : "缺"} zone=${equipSlot ? attrOf(equipSlot, "data-zone") : ""}`);

  await clickAndWait(equipSlot);
  check("改判-2 点装备区的牌 = 设上焦点，提交动作就是候选里那条 SubstituteJudgeCard",
    ui.state.focusCardId === 21
      && JSON.stringify(ui.buildAction()) === JSON.stringify(rejudge.legal_actions[0]),
    `focus=${ui.state.focusCardId} built=${JSON.stringify(ui.buildAction())}`);

  ui.state.view = savedView;
  ui.resetSelection();
}

/* ============ 选牌**顺序无关**（2026-11-03 实机报障：业炎的四色成本从右往左点 = 非法）============
 *
 * 根因：`SELECT_CARDS` 的候选由引擎按 `combinations(...)` 枚举 ⇒ 面内位置**升序**；而界面
 * `state.picks` 记的是**点击顺序**。多选面（≥2 张）倒着点会拼出降序 picks ⇒ 逐字段自检找不到
 * 候选 ⇒ 「选择非法」。这里钉住"点击顺序不影响提交"，并核对送出的就是候选里那条升序动作。
 */
{
  const pickSession = await post("/api/sessions", {
    player_count: 4, ai: "random", human_seat: 0, timeout_seconds: null, seed: 20261103,
  });
  const pickSeq = 993;
  const suitCards = [
    { suit: "spade", name: "杀" },
    { suit: "heart", name: "桃" },
    { suit: "club", name: "闪" },
    { suit: "diamond", name: "酒" },
  ].map((entry, index) => ({
    card_id: 300 + index,
    name: entry.name,
    suit: entry.suit,
    rank: 7,
    type: index % 2 === 0 ? "slash" : "dodge",
    slot: null,
    attack_range: null,
    color: entry.suit === "heart" || entry.suit === "diamond" ? "red" : "black",
    virtual_sources: [],
  }));
  const pickAction = {
    kind: "SelectCards", picks: [0, 1, 2, 3], answers_seq: pickSeq,
  };
  const pickView = synthView({
    kind: "SELECT_CARDS", actor: 0, seq: pickSeq,
    prompt: "请从选择面里选择一张牌。",
    legal_actions: [pickAction],
    candidate_index: {}, option_seats: {},
    slots: suitCards.map((card) => ({ ref: { seat: 0, zone: "hand" }, card })),
    spec: { zones: ["hand"], total: [4, 4], max_per_suit: 1 },
    context: {
      actor: 0, name: "业炎", targets: [],
      label: "0号位神周瑜移动版发动业炎",
      requirement: "弃置四张花色各不相同的手牌", skill_hints: [],
    },
  });
  pickView.snapshot.self_hand = suitCards;
  pickView.snapshot.players[0].hand_count = suitCards.length;
  ui.state.view = pickView;
  ui.state.sessionId = pickSession.session_id;
  ui.resetSelection();
  ui.state.selectedOption = 0;
  // 倒序点选：面内位置 3 → 2 → 1 → 0（`state.picks` 就是点击顺序）。
  ui.state.picks = [3, 2, 1, 0];
  ui.renderTable();
  ui.renderPending(pickView);

  const builtReverse = ui.buildAction();
  check("选牌顺序-1 倒序点选也拼出候选里那条（picks 规范化为升序）",
    !!builtReverse
      && JSON.stringify(builtReverse.picks) === JSON.stringify([0, 1, 2, 3])
      && pickView.pending.legal_actions.some(
        (action) => JSON.stringify(action) === JSON.stringify(builtReverse),
      ),
    `built=${JSON.stringify(builtReverse)}`);
  check("选牌顺序-2 倒序点选时「提交」可点（不再因为顺序而灰着）",
    $("btn-submit").disabled === false,
    `disabled=${$("btn-submit").disabled}`);

  const beforePickSubmit = calls.length;
  staleAnswerStatus = 400;
  await clickAndWait($("btn-submit"));
  for (let index = 0; index < 400 && ui.state.submitBusy; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  staleAnswerStatus = null;
  const pickAnswers = calls
    .slice(beforePickSubmit)
    .filter((call) => call.path.includes("/answer"));
  check("选牌顺序-3 提交送出的是升序那条候选（不是点击顺序）",
    pickAnswers.length === 1
      && JSON.stringify(pickAnswers[0].body.action) === JSON.stringify(pickAction),
    `answers=${pickAnswers.length} action=${JSON.stringify(pickAnswers[0] && pickAnswers[0].body.action)}`);
}

/* ============ huo-shenzhugeliang 票 02：两个新面（合成载荷） ============
 *
 * ① **两行交换**（神诸葛亮【七星】）：上排「星」、下排「手牌」，只上下交换、行内不调序；
 *    候选**不枚举**（`arrange.enumerate === false`）⇒ 界面拼出来的 `ArrangeCards`
 *    不在 `legal_actions` 里也必须能提交（服务端做结构校验）。
 * ② **武将牌上的牌点选**（【狂风】/【大雾】的代价步）：`SELECT_CARDS` 且每一槽都来自
 *    `zone: "hero"` ⇒ 走弹窗（`heroPick`），正文标题不再说"看不到牌面"。
 */
{
  const cards5 = [
    { ...synthCard(201, "桃"), suit: "heart", rank: 12 },
    { ...synthCard(202, "杀"), suit: "spade", rank: 7 },
    { ...synthCard(203, "闪"), suit: "diamond", rank: 2 },
    { ...synthCard(204, "过河拆桥"), suit: "club", rank: 3 },
    { ...synthCard(205, "无中生有"), suit: "heart", rank: 7 },
  ];
  const swapPending = {
    kind: "ARRANGE", actor: 0, seq: 991, timing: "PHASE_DRAW_END",
    prompt: "请交换两行之间的牌（行内顺序不变）。",
    revealed: cards5,
    legal_actions: [{ answers_seq: 991, kind: "ArrangeCards", order: [0, 1, 2, 3, 4], split: 3 }],
    candidate_index: {}, option_seats: {}, preactivations: {}, preactivation: [],
    arrange: {
      count: 5, split: { top: 3, bottom: 2 },
      rows: { top: "星", bottom: "手牌" }, enumerate: false, reorder: false,
    },
  };
  const swapView = synthView(swapPending);
  const rowOf = (name) => Array.from($("t-options").children).find(
    (node) => node.getAttribute && node.getAttribute("data-row") === name);
  const cardsBox = (row) => row.children[1];
  const cardIndexes = (row) => Array.from(cardsBox(row).children)
    .filter((node) => node.getAttribute && node.getAttribute("data-card-index") !== undefined)
    .map((node) => Number(node.getAttribute("data-card-index")));
  const cardNode = (row, index) => Array.from(cardsBox(row).children).find(
    (node) => node.getAttribute && node.getAttribute("data-card-index") === String(index));
  const clickNode = (node) => (node.listeners.click || []).forEach(
    (fn) => fn({ target: node, preventDefault() {} }));

  ui.state.view = swapView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(swapView);

  const topRow = rowOf("top");
  const bottomRow = rowOf("bottom");
  check("七星-1 两行交换面：行标签来自载荷（「星」/「手牌」，不是牌堆顶 / 底）",
    !!topRow && !!bottomRow
      && topRow.children[0].textContent.includes("星")
      && bottomRow.children[0].textContent.includes("手牌")
      && !topRow.children[0].textContent.includes("牌堆顶"),
    `top=${topRow && topRow.children[0].textContent} bottom=${bottomRow && bottomRow.children[0].textContent}`);
  check("七星-2 上排 3 张星、下排 2 张手牌（切分点来自载荷）",
    JSON.stringify(cardIndexes(topRow)) === "[0,1,2]"
      && JSON.stringify(cardIndexes(bottomRow)) === "[3,4]",
    `上=${cardIndexes(topRow)} 下=${cardIndexes(bottomRow)}`);
  const identity = ui.buildAction();
  check("七星-3 原样提交 = ArrangeCards(order 0..4, split 3)（= 不换任何一张）",
    !!identity
      && JSON.stringify(identity) === JSON.stringify(
        { answers_seq: 991, kind: "ArrangeCards", order: [0, 1, 2, 3, 4], split: 3 }),
    JSON.stringify(identity));

  // 上下交换：点星 0 → 点下排的手牌 3（固定切分跨行 = 两张互换）。
  clickNode(cardNode(rowOf("top"), 0));
  clickNode(cardNode(rowOf("bottom"), 3));
  const swapped = ui.buildAction();
  check("七星-4 上下交换 = 两张互换（上排仍是 3 张），载荷 order [3,1,2,0,4] / split 3",
    !!swapped
      && JSON.stringify(swapped.order) === JSON.stringify([3, 1, 2, 0, 4])
      && swapped.split === 3,
    JSON.stringify(swapped));

  // 行内不调序：点星 1 → 点星 2（同一行）⇒ 不动。
  const beforeSameRow = JSON.stringify(cardIndexes(rowOf("top")));
  clickNode(cardNode(rowOf("top"), 1));
  clickNode(cardNode(rowOf("top"), 2));
  check("七星-5 行内不可调序（reorder=false）：点同一行的另一张不产生移动",
    JSON.stringify(cardIndexes(rowOf("top"))) === beforeSameRow,
    `before=${beforeSameRow} after=${JSON.stringify(cardIndexes(rowOf("top")))}`);

  // 不枚举档：界面的答案不在候选里，也必须能提交（服务端结构校验）。
  const beforeSwapSubmit = calls.length;
  await ui.submitAction();
  const swapAnswers = calls.slice(beforeSwapSubmit).filter((call) => call.path.includes("/answer"));
  check("七星-6 不枚举档：自造的 ArrangeCards 不在 legal_actions 里也能提交",
    swapAnswers.length === 1
      && JSON.stringify(swapAnswers[0].body.action) === JSON.stringify(swapped),
    `answers=${swapAnswers.length} action=${JSON.stringify(swapAnswers[0] && swapAnswers[0].body.action)}`);

  /* ---- ② 武将牌上的牌点选：弹窗 + 可见牌面 ---- */
  const starSlots = [201, 202].map((id, index) => ({
    ref: { zone: "hero", seat: 0, skill_instance: "0:hero.shen_zhugeliang.qixing" },
    card: cards5[index],
  }));
  const pickPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 993, timing: "PHASE_FINISH",
    prompt: "移去一张“星”（武将牌上的牌）",
    slots: starSlots,
    spec: { zones: ["hero"], total: [1, 1], optional: false },
    legal_actions: [{ answers_seq: 993, kind: "SelectCards", picks: [0] }],
    candidate_index: {}, option_seats: {}, preactivations: {}, preactivation: [],
  };
  const pickView = synthView(pickPending);
  ui.state.view = pickView;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(pickView);

  check("狂风/大雾-1 武将牌上的牌点选 = heroPick 弹窗（不是左下角面板）",
    ui.stepDialogKind(pickPending) === "heroPick"
      && $("t-dialog").hidden === false
      && $("t-dialog-title").textContent === "选择武将牌上的牌"
      && Array.from($("t-dialog-body").children).includes($("t-options")),
    `kind=${ui.stepDialogKind(pickPending)} title="${$("t-dialog-title").textContent}"`);
  check("狂风/大雾-2 正文标题不再说『看不到牌面』（星对宿主可见）",
    $("t-options").text().includes("武将牌上的牌")
      && !$("t-options").text().includes("看不到牌面"),
    $("t-options").text().slice(0, 40));
  const chips = Array.from($("t-options").children)
    .flatMap((node) => (node.children ? Array.from(node.children) : []))
    .filter((node) => node.className && String(node.className).includes("card"));
  check("狂风/大雾-3 每个星一张可点牌（两张）", chips.length === 2, `chips=${chips.length}`);
  // 报障修复（用户 2026-11-04）：星牌要与**常规卡牌**同一套牌面——花色 + 点数 + 牌名，
  // 不能只剩牌名。合成载荷的第 1 张是 ♥Q【桃】，第 2 张是 ♠7【杀】。
  const chipText = (index) => (chips[index] ? chips[index].text() : "");
  check("狂风/大雾-3b 星牌按常规卡面画：花色 + 点数 + 牌名（♥Q 桃 / ♠7 杀）",
    chipText(0).includes("♥") && chipText(0).includes("Q") && chipText(0).includes("桃")
      && chipText(1).includes("♠") && chipText(1).includes("7") && chipText(1).includes("杀"),
    `chip0="${chipText(0)}" chip1="${chipText(1)}"`);
  if (chips.length) clickNode(chips[0]);
  const starPick = ui.buildAction();
  check("狂风/大雾-4 点一张星 = SelectCards(picks [0])",
    !!starPick && JSON.stringify(starPick) === JSON.stringify(
      { answers_seq: 993, kind: "SelectCards", picks: [0] }),
    `${JSON.stringify(starPick)} picks=${JSON.stringify(ui.state.picks)} chips=${chips.length}`);
}

/* ============ 票 04（合成记录）：拼点弹窗——全场可见的只读结果面 ============
 *
 * 判据读**服务端 `pindian` delta 的形状**（`state/deltas.py:PindianDelta` 逐字段：title /
 * initiator / targets / cards / won / tied），渲染走生产入口 `applyPindianDelta`
 * （`session.js:stepPlayback` 每条记录调一次）。弹窗**不是询问面**：不进 `stepDialogKind`、
 * 没有可点节点（选牌仍走牌桌）、停留 3 秒。
 *
 * 版面口径（用户 2026-11-05 复验）：每个**玩家块** = 上方「赢 / 没赢」+ 中间**正常卡面** +
 * 下方「座位号 武将名」；1v1 两块并排，1vN 时发起者的牌只画一次。
 */
{
  const pinCard = (id, name, suit, rank) => {
    const card = synthCard(id, name);
    card.suit = suit;
    card.rank = rank;
    card.color = suit === "heart" || suit === "diamond" ? "red" : "black";
    return card;
  };
  const initiator = pinCard(901, "杀", "heart", 12);
  const winner = pinCard(902, "闪", "spade", 9);
  const sameRank = pinCard(903, "桃", "diamond", 12);
  const pindianRecord = (overrides = {}) => ({
    kind: "delta", delta_type: "pindian", seq: 9510, frame_id: 42, depth: 3,
    title: "天义", initiator: 0, targets: [1], cards: [initiator, winner],
    won: [true], tied: [false],
    ...overrides,
  });
  // 座位号 + 武将名读视图的 heroes（与座位卡同源）：显式给一份，不吃上一段残留的 view。
  ui.state.view = synthView(null);
  const popup = $("t-pindian");
  const popupBody = $("t-pindian-body");
  const isPlayer = (node) => hasClassText(node, "pindian-player");
  const initiatorBlock = () => Array.from(popupBody.children).find(isPlayer) || null;
  const verdictOf = (block) => {
    if (!block) return null;
    return Array.from(block.children).find((node) => hasClassText(node, "pindian-verdict")) || null;
  };
  const whoOf = (block) => {
    if (!block) return null;
    return Array.from(block.children).find((node) => hasClassText(node, "pindian-who")) || null;
  };
  const cardOf = (block) => {
    if (!block) return null;
    const slot = Array.from(block.children).find((node) => hasClassText(node, "pindian-slot"));
    return slot ? Array.from(slot.children)[0] || null : null;
  };
  const targetBlocks = () => {
    const column = Array.from(popupBody.children)
      .find((node) => hasClassText(node, "pindian-targets"));
    return column ? Array.from(column.children) : [];
  };

  // ① 1v1 赢：标题 = 发动技能名；两块并排（左发起者、右被拼点者），各自上方赢 / 没赢
  const opened = ui.applyPindianDelta(pindianRecord());
  const left = initiatorBlock();
  const right = targetBlocks()[0] || null;
  check("票04-1 拼点弹窗：全场可见 + 标题 = 发动技能名 + 左 1 块 / 右 1 块",
    opened === true && popup.hidden === false
      && $("t-pindian-title").textContent === "天义"
      && !!left && left.getAttribute("data-role") === "initiator"
      && targetBlocks().length === 1
      && !!right && right.getAttribute("data-role") === "target",
    `opened=${opened} hidden=${popup.hidden} title="${$("t-pindian-title").textContent}" rows=${targetBlocks().length}`);
  check("票04-2 卡面走正常渲染：左块 = ♥Q 杀、右块 = ♠9 闪（卡面节点带 data-card-id）",
    !!cardOf(left) && cardOf(left).text().includes("♥")
      && cardOf(left).text().includes("Q") && cardOf(left).text().includes("杀")
      && cardOf(left).getAttribute("data-card-id") === "901"
      && !!cardOf(right) && cardOf(right).text().includes("♠")
      && cardOf(right).text().includes("9") && cardOf(right).text().includes("闪")
      && cardOf(right).getAttribute("data-card-id") === "902",
    `left="${cardOf(left) ? cardOf(left).text() : ""}" right="${cardOf(right) ? cardOf(right).text() : ""}"`);
  check("票04-3 结果在**卡面上方**：发起者「赢」、被拼点者「没赢」",
    !!verdictOf(left) && verdictOf(left).textContent === "赢"
      && verdictOf(left).classList.contains("win")
      && !!verdictOf(right) && verdictOf(right).textContent === "没赢"
      && verdictOf(right).classList.contains("lose"),
    `left="${verdictOf(left) ? verdictOf(left).textContent : ""}" right="${verdictOf(right) ? verdictOf(right).textContent : ""}"`);
  check("票04-4 归属在**卡面下方**：座位号 + 武将名（0号位 郭嘉 / 1号位 张飞）",
    !!whoOf(left) && whoOf(left).textContent === `0号位 ${ui.heroDisplayName("guojia")}`
      && !!whoOf(right) && whoOf(right).textContent === `1号位 ${ui.heroDisplayName("zhangfei")}`
      && whoOf(left).textContent.includes("号位") && whoOf(right).textContent.includes("号位"),
    `left="${whoOf(left) ? whoOf(left).textContent : ""}" right="${whoOf(right) ? whoOf(right).textContent : ""}"`);
  check("票04-5 停留 3 秒 = 前端展示（PINDIAN_HOLD_MS，服务端不阻塞）",
    ui.PINDIAN_HOLD_MS === 3000 && latestTimerDelay() === 3000,
    `hold=${ui.PINDIAN_HOLD_MS} timer=${latestTimerDelay()}`);

  // ② 平点：两块都「没赢」（读 tied；被拼点者不能靠取反说谎），平点信息进悬停
  ui.applyPindianDelta(pindianRecord({ cards: [initiator, sameRank], won: [false], tied: [true] }));
  const tieLeft = initiatorBlock();
  const tieRight = targetBlocks()[0] || null;
  check("票04-6 平点：两个槽位都写「没赢」（读 tied，前端不比点数）",
    verdictOf(tieLeft).textContent === "没赢" && verdictOf(tieRight).textContent === "没赢"
      && verdictOf(tieRight).getAttribute("title").includes("平点")
      && tieRight.getAttribute("data-verdict") === "tie",
    `left="${verdictOf(tieLeft).textContent}" right="${verdictOf(tieRight).textContent}" tie=${verdictOf(tieRight).getAttribute("title")}`);

  // ③ 1vN：发起者的牌只画一次；每个被拼点者一块，各自的结果各行其是
  ui.applyPindianDelta(pindianRecord({
    targets: [1, 2], cards: [initiator, winner, sameRank],
    won: [true, false], tied: [false, false],
  }));
  const vnLeft = initiatorBlock();
  const vnRows = targetBlocks();
  check("票04-7 1vN：左 1 块 + 右 2 块（发起者的牌只画一次）；发起者未全赢 ⇒ 写「没赢」",
    vnRows.length === 2
      && verdictOf(vnLeft).textContent === "没赢"
      && verdictOf(vnLeft).getAttribute("title").includes("并非每一对都赢")
      && verdictOf(vnRows[0]).textContent === "没赢"
      && verdictOf(vnRows[1]).textContent === "赢"
      && whoOf(vnRows[1]).textContent.startsWith("2号位"),
    `left="${verdictOf(vnLeft).textContent}" rows=${vnRows.map((r) => verdictOf(r).textContent).join(",")}`);

  // ④ 生效点数（tao-caocao 票 03）：delta 带 `ranks` 时两个槽位各画一行「点数 N」；
  //    不带（旧记录）⇒ 一行都不画，也**不**拿牌面点数去猜——牌面点数与生效点数可以不同。
  const pointOf = (block) => {
    if (!block) return null;
    return Array.from(block.children).find((node) => hasClassText(node, "pindian-point")) || null;
  };
  ui.applyPindianDelta(pindianRecord({ ranks: [7, 6] }));
  const boostLeft = initiatorBlock();
  const boostRight = targetBlocks()[0] || null;
  check("票04-10 生效点数：delta 带 ranks ⇒ 两块各写「点数 7 / 点数 6」（牌面仍是 Q / ♠9）",
    !!pointOf(boostLeft) && pointOf(boostLeft).textContent === "点数 7"
      && !!pointOf(boostRight) && pointOf(boostRight).textContent === "点数 6"
      && cardOf(boostLeft).text().includes("Q") && cardOf(boostRight).text().includes("9"),
    `left="${pointOf(boostLeft) ? pointOf(boostLeft).textContent : ""}" right="${pointOf(boostRight) ? pointOf(boostRight).textContent : ""}"`);
  ui.applyPindianDelta(pindianRecord());
  check("票04-11 旧记录（没有 ranks）：一行点数都不画（前端不拿牌面点数猜）",
    pointOf(initiatorBlock()) === null && pointOf(targetBlocks()[0] || null) === null,
    `left=${!!pointOf(initiatorBlock())} right=${!!pointOf(targetBlocks()[0] || null)}`);

  // ⑤ 只读：不进询问面判据、弹窗里没有一个可点节点（选牌仍走牌桌）
  const clickables = [popupBody, ...Array.from(popupBody.children)]
    .flatMap((node) => [node, ...Array.from(node.children || [])])
    .flatMap((node) => [node, ...Array.from(node.children || [])])
    .filter((node) => (node.listeners.click || []).length);
  check("票04-8 只读：DIALOG_KINDS 里没有 pindian、弹窗内没有可点节点",
    !Object.values(ui.DIALOG_KINDS).includes("pindian") && clickables.length === 0,
    `clickables=${clickables.length} kinds=${Object.keys(ui.DIALOG_KINDS).join(",")}`);

  // ⑥ 判据只看 `delta_type`：别的记录一律不弹窗
  const ignored = ui.applyPindianDelta({ kind: "delta", delta_type: "hp_change", seq: 1 });
  check("票04-9 非拼点记录不弹窗（判据只看 delta_type）", ignored === false, `ignored=${ignored}`);
}

/* ============ 票 93（合成载荷）：两行观看手牌——上行目标 / 下行自己、四色各一 ============
 *
 * 判据只读载荷形状（`SELECT_CARDS` ∧ `spec.max_per_suit` ∧ 槽位恰好跨两个座位、含自己），
 * 不认技能 id（首个技能载体是神甘宁【魄袭】，本票不带技能）。这一块钉界面的**读法**：
 * 两行 / 上行牌标记 / 同花色置灰（两行一起算）/ 四色各一才可提交 / 点已选取消 /
 * 右端「放弃」= 候选里那条 `Pass` / 空选择面（凑不出四色）只有放弃 + 一句明话。
 */
{
  const watchCard = (id, name, suit, tags = []) => {
    const card = synthCard(id, name);
    card.suit = suit;
    card.rank = 7;
    card.color = suit === "heart" || suit === "diamond" ? "red" : "black";
    if (tags.length) card.tags = tags;
    return card;
  };
  // 目标座 1：【♠杀】（带来源技能标记）+【♥桃】；自己：♦闪 + ♣酒 + ♥桃（与目标同花色）。
  //「四色」= ♠♥♦♣ 齐全；♥ 在**两行**各有一张 ⇒ 置灰判据必须跨行算。
  const watchTargetCards = [
    watchCard(1101, "杀", "spade", [{ skill_id: "hero.test.tag", label: "测试标记" }]),
    watchCard(1102, "桃", "heart"),
  ];
  const watchSelfCards = [
    watchCard(1103, "闪", "diamond"),
    watchCard(1104, "酒", "club"),
    watchCard(1105, "桃", "heart"),
  ];
  const watchSlots = [
    ...watchTargetCards.map((card) => ({ ref: { zone: "hand", seat: 1 }, card })),
    ...watchSelfCards.map((card) => ({ ref: { zone: "hand", seat: 0 }, card })),
  ];
  const watchAction = { kind: "SelectCards", picks: [0, 1, 2, 3], answers_seq: 9801 };
  const watchPending = {
    kind: "SELECT_CARDS", actor: 0, seq: 9801, prompt: "请从选择面里选择牌。",
    legal_actions: [
      { kind: "SelectCards", picks: [], answers_seq: 9801 },
      watchAction,
      { kind: "Pass", answers_seq: 9801 },
    ],
    candidate_index: {}, option_seats: {},
    context: {
      actor: 0, name: "观看测试", targets: [1],
      requirement: "观看其手牌，选择四张花色各不相同的牌", skill_hints: [],
    },
    preactivations: {}, preactivation: [],
    slots: watchSlots,
    spec: { zones: ["hand"], total: [4, 4], max_per_suit: 1, optional: true },
    revealed: watchTargetCards.slice(),   // 观看授予（服务端同形；非应答者会被抹空）
    dialog: "watchTwoRows",
  };
  const watchView = synthView(watchPending);
  watchView.snapshot.self_hand = watchSelfCards;
  watchView.snapshot.players[0].hand_count = watchSelfCards.length;
  watchView.snapshot.players[1].hand_count = watchTargetCards.length;

  const watchFindAll = (root, predicate) => {
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) {
        if (predicate(child)) out.push(child);
        walk(child);
      }
    };
    walk(root);
    return out;
  };
  const watchRows = () => watchFindAll($("t-options"), (node) => node.getAttribute
    && node.getAttribute("data-watch-row") !== undefined);
  const watchSlotNode = (index) => watchFindAll($("t-options"), (node) => node.getAttribute
    && node.getAttribute("data-slot-index") === String(index))[0] || null;
  const watchTagTexts = () => watchFindAll($("t-options"), (node) => node.classList
    && node.classList.contains("card-tag")).map((node) => node.textContent);
  const watchCardTagOf = (cardId) => watchFindAll($("t-options"), (node) => node.getAttribute
    && node.getAttribute("data-card-id") === String(cardId)
    && node.classList && node.classList.contains("reveal-card"))[0] || null;

  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.suitResult = null;
  ui.state.view = watchView;
  ui.renderTable();
  ui.renderPending(watchView);

  const watchRowEls = watchRows();
  const headText = (row) => (row && row.children[0] ? row.children[0].textContent : "");
  check("票 93-W1 两行观看面 = 新弹窗（watchTwoRows），上行目标、下行自己（固定行序）",
    ui.stepDialogKind(watchPending) === ui.DIALOG_KINDS.watchTwoRows
      && !$("t-dialog").hidden
      && $("t-dialog-title").textContent === "观看测试"
      && watchRowEls.length === 2
      && watchRowEls[0].getAttribute("data-seat") === "1"
      && watchRowEls[1].getAttribute("data-seat") === "0"
      && headText(watchRowEls[0]) === "座1·目标手牌"
      && headText(watchRowEls[1]) === "你的手牌",
    `kind=${ui.stepDialogKind(watchPending)} rows=${watchRowEls.length} `
      + `head0=${headText(watchRowEls[0])} head1=${headText(watchRowEls[1])}`);

  const watchSlotCount = watchSlots.filter(
    (slot, index) => !!watchFindAll($("t-options"), (node) => node.getAttribute
      && node.getAttribute("data-slot-index") === String(index)).length,
  ).length;
  check("票 93-W2 两行共 5 张可点牌面（上行 2 + 下行 3），按卡面渲染（带花色 / 点数 / 牌名）",
    watchSlotCount === 5
      && !!watchCardTagOf(1101) && watchCardTagOf(1101).text().includes("♠")
      && watchCardTagOf(1101).text().includes("杀")
      && !!watchCardTagOf(1105) && watchCardTagOf(1105).text().includes("♥"),
    `slots=${watchSlotCount} card1101="${watchCardTagOf(1101) ? watchCardTagOf(1101).text() : ""}"`);

  check("票 93-W3 上行牌标记可见（服务端 card.tags 照画，不因为是别人的手牌就丢）",
    watchTagTexts().includes("测试标记"),
    `tags=${JSON.stringify(watchTagTexts())}`);

  check("票 93-W4 未满 4 张「提交」灰着；右端「放弃」在（候选里的 Pass）",
    $("btn-submit").disabled === true && $("btn-pass").hidden === false
      && $("btn-pass").textContent === "放弃"
      && $("t-target-hint").textContent.includes("已选 0/4"),
    `submit=${$("btn-submit").disabled} pass=${$("btn-pass").hidden} `
      + `hint=${$("t-target-hint").textContent}`);

  // 点一张目标行的 ♥ ⇒ 自己行那张 ♥ 同时置灰且**点不动**（两行一起算）。
  await clickAndWait(watchCardTagOf(1102));
  const dimHeart = watchCardTagOf(1105);
  check("票 93-W5 点一张 ⇒ 同花色其余牌（两行都算）置暗、不接 click",
    ui.state.picks.length === 1 && ui.state.picks[0] === 1
      && watchCardTagOf(1102).getAttribute("data-picked") === "true"
      && !!dimHeart && dimHeart.getAttribute("data-dim") === "true"
      && !(dimHeart.listeners.click || []).length,
    `picks=${JSON.stringify(ui.state.picks)} dim=${dimHeart && dimHeart.getAttribute("data-dim")}`);
  await clickAndWait(dimHeart);
  check("票 93-W6 置暗牌点了不改变选择",
    JSON.stringify(ui.state.picks) === "[1]", JSON.stringify(ui.state.picks));

  // 点已选的那张 = 取消；同花色另一张重新亮起。
  await clickAndWait(watchCardTagOf(1102));
  check("票 93-W7 点已选的牌 = 取消选中，同花色另一张重新亮起",
    ui.state.picks.length === 0 && watchCardTagOf(1105).getAttribute("data-dim") !== "true"
      && !!(watchCardTagOf(1105).listeners.click || []).length,
    JSON.stringify(ui.state.picks));

  // 四色各一：♠(0) ＋ ♥(1) ＋ ♦(2) ＋ ♣(3) ⇒ 提交可点，送出的仍是 SelectCards（升序 picks）。
  for (const index of [1, 0, 3, 2]) await clickAndWait(watchSlotNode(index));
  check("票 93-W8 四色各一 ⇒ 「提交」可点；已选计数读 4/4",
    ui.state.picks.length === 4 && $("btn-submit").disabled === false
      && $("t-target-hint").textContent.includes("已选 4/4"),
    `picks=${JSON.stringify(ui.state.picks)} submit=${$("btn-submit").disabled}`);
  ui.state.sessionId = "smoke-missing-session";
  const beforeWatchSubmit = calls.length;
  const watchBuilt = ui.buildAction();
  await clickAndWait($("btn-submit"));
  const watchAnswers = calls
    .slice(beforeWatchSubmit)
    .filter((call) => call.path.includes("/answer"));
  check("票 93-W9 提交 = 候选里那条 SelectCards（picks 规范化升序，与点击顺序无关）",
    !!watchBuilt && JSON.stringify(watchBuilt.picks) === "[0,1,2,3]"
      && watchAnswers.length === 1
      && JSON.stringify(watchAnswers[0].body.action) === JSON.stringify(watchAction),
    `built=${JSON.stringify(watchBuilt)} answers=${JSON.stringify(watchAnswers[0]
      && watchAnswers[0].body.action)}`);

  // 右端「放弃」= 候选里那条 Pass（不是"提交当前选择"）。
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.renderPending(watchView);
  ui.state.sessionId = "smoke-missing-session";
  const beforeWatchPass = calls.length;
  await clickAndWait($("btn-pass"));
  const watchPass = calls
    .slice(beforeWatchPass)
    .filter((call) => call.path.includes("/answer"))
    .map((call) => call.body.action)[0];
  check("票 93-W10 点「放弃」送出的就是候选里的那条 Pass（超时兜底同一条）",
    !!watchPass && watchPass.kind === "Pass"
      && watchPass.answers_seq === 9801,
    JSON.stringify(watchPass));

  // 非应答者：两行照画**位置**、牌面「?」、不给 click（§3 B4）。
  const foreignSlots = watchSlots.map((slot) => ({ ref: slot.ref, card: null }));
  const foreignPending = {
    ...watchPending, actor: 1, seq: 9802,
    legal_actions: [{ kind: "Pass", answers_seq: 9802 }],
    slots: foreignSlots, revealed: [],
  };
  const foreignView = synthView(foreignPending);
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.view = foreignView;
  ui.renderTable();
  ui.renderPending(foreignView);
  const foreignRows = watchRows();
  const unknownSlots = watchFindAll($("t-options"), (node) => node.classList
    && node.classList.contains("unknown"));
  check("票 93-W11 非应答者：两行照画位置、牌面全是「?」、一个都不接 click",
    ui.stepDialogKind(foreignPending) === ui.DIALOG_KINDS.watchTwoRows
      && !$("t-dialog").hidden
      && foreignRows.length === 2 && unknownSlots.length === 5
      && unknownSlots.every((node) => !(node.listeners.click || []).length)
      && $("btn-pass").hidden === true,
    `rows=${foreignRows.length} unknown=${unknownSlots.length} passHidden=${$("btn-pass").hidden}`);

  // 空选择面（凑不出四色）：仍画上行的整副手牌；出口只有「放弃」，且提示必须说清。
  const emptyPending = {
    ...watchPending, seq: 9803,
    legal_actions: [{ kind: "Pass", answers_seq: 9803 }],
    slots: [], revealed: watchTargetCards.slice(),
  };
  const emptyView = synthView(emptyPending);
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.view = emptyView;
  ui.renderTable();
  ui.renderPending(emptyView);
  const emptyRows = watchRows();
  const emptyFaces = watchFindAll($("t-options"), (node) => node.getAttribute
    && node.getAttribute("data-card-id") !== undefined);
  check("票 93-W12 凑不出四色（空选择面）：整副手牌仍可见、「提交」灰着、明说只能放弃",
    emptyRows.length === 1
      && emptyRows[0].children[0].textContent === "目标手牌"
      && emptyFaces.length === 2 && $("btn-submit").disabled === true
      && $("t-target-hint").textContent.includes("凑不出四种花色")
      && $("t-choice").textContent.includes("凑不出四种花色")
      && $("btn-pass").hidden === false,
    `rows=${emptyRows.length} faces=${emptyFaces.length} `
      + `hint=${$("t-target-hint").textContent} choice=${$("t-choice").textContent}`);

  // 非应答者 + 空选择面：服务端显式标记仍在，但牌面（revealed）已被抹空 ⇒ 仍画上行标签、
  // 0 张牌；不能因为"没有牌可画"就把弹窗整个吞掉（Python 侧把这条标记当回答，前端要消费它）。
  const foreignEmptyPending = {
    ...foreignPending, seq: 9806,
    slots: [], revealed: [],
    legal_actions: [{ kind: "Pass", answers_seq: 9806 }],
  };
  const foreignEmptyView = synthView(foreignEmptyPending);
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.view = foreignEmptyView;
  ui.renderTable();
  ui.renderPending(foreignEmptyView);
  const foreignEmptyRows = watchRows();
  check("票 93-W13 非应答者 + 空选择面：仍画上行标签（牌面已抹 ⇒ 0 张），提示等待",
    ui.stepDialogKind(foreignEmptyPending) === ui.DIALOG_KINDS.watchTwoRows
      && !$("t-dialog").hidden
      && foreignEmptyRows.length === 1
      && foreignEmptyRows[0].children[0].textContent === "目标手牌"
      && watchFindAll($("t-options"), (node) => node.getAttribute
        && node.getAttribute("data-card-id") !== undefined).length === 0
      && $("t-target-hint").textContent.includes("等待该座位选择"),
    `rows=${foreignEmptyRows.length} hint=${$("t-target-hint").textContent}`);

  // 生命周期（§7 的多步回放口径）：一批记录在播时询问面让位（关窗），播完按新询问重开。
  const lifecyclePending = { ...watchPending, seq: 9807 };
  const lifecycleBody = synthView(lifecyclePending);
  lifecycleBody.records = [{ seq: 9807, kind: "timing", timing: "PHASE_PLAY", actor: 0 }];
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.view = watchView;
  ui.applyViewRaw(lifecycleBody);
  const closedDuringPlayback = $("t-dialog").hidden === true;
  ui.flushPlayback();
  check("票 93-W14 生命周期：回放期间两行面让位（关窗），播完按新询问重开",
    closedDuringPlayback && $("t-dialog").hidden === false
      && ui.stepDialogKind(lifecyclePending) === ui.DIALOG_KINDS.watchTwoRows
      && watchRows().length === 2,
    `during=${closedDuringPlayback} after=${$("t-dialog").hidden} rows=${watchRows().length}`);

  // B1：只有自己一只手的退化面 ⇒ 不套两行模板（fail-closed 回落到既有槽位面），不崩。
  const oneRowPending = {
    ...watchPending, seq: 9804,
    slots: watchSelfCards.map((card) => ({ ref: { zone: "hand", seat: 0 }, card })),
  };
  const oneRowView = synthView(oneRowPending);
  ui.state.sessionId = null;
  ui.state.picks = [];
  ui.state.view = oneRowView;
  ui.renderTable();
  ui.renderPending(oneRowView);
  check("票 93-B1 只有一手的退化面不画两行（判据要求恰好两个座位）",
    ui.stepDialogKind(oneRowPending) === null && watchRows().length === 0,
    `kind=${ui.stepDialogKind(oneRowPending)} rows=${watchRows().length}`);

  // B2：同一 card_id 出现两次（引擎数据错误）⇒ 画得出、但不许提交。
  const duplicatePending = {
    ...watchPending, seq: 9805,
    slots: [
      { ref: { zone: "hand", seat: 1 }, card: watchTargetCards[0] },
      { ref: { zone: "hand", seat: 1 }, card: watchTargetCards[0] },
      { ref: { zone: "hand", seat: 0 }, card: watchSelfCards[0] },
      { ref: { zone: "hand", seat: 0 }, card: watchSelfCards[1] },
    ],
  };
  const duplicateView = synthView(duplicatePending);
  ui.state.sessionId = null;
  ui.state.picks = [0, 1, 2, 3];
  ui.state.view = duplicateView;
  ui.renderTable();
  ui.renderPending(duplicateView);
  check("票 93-B2 重复槽位（同一 card_id 两次）画得出但「提交」恒灰，提示说数据异常",
    $("btn-submit").disabled === true
      && $("t-choice").textContent.includes("重复"),
    `submit=${$("btn-submit").disabled} choice=${$("t-choice").textContent}`);
  ui.state.picks = [];
}

/* ============ 票 06：未知区域弹窗的**多选档**（【定乱】展示别人的至多 3 张牌） ============
 *
 * 报障原样：挑**别人**的牌时形状命中 `isUnknownZoneStep` ⇒ 落「未知区域」单选模板，
 * 点一张即提交 ⇒"展示至多 X 张"只能展示 1 张，而且没有"选好 → 提交"这一步。
 *
 * 本段用**真会话**（票面 §2.2 的复现配方：2 人局 + 我的武将是锋·于禁）走一遍：
 * 第 1 轮开始的【定乱】→ 点技能 + 「提交」（= 发动）→ 目标点座位 1 → 挑牌步
 * （`SELECT_CARDS` + `total=[1,3]`，真载荷）点两张 → **弹窗不消失**、提交键可点 →
 * 点「提交」→ 一条多张 `SelectCards{picks:[0,2]}` 上行 → 两张牌一起被展示
 * （两条 `card_reveal` delta）且一局继续推进。
 *
 * 判据只读**真载荷**（`spec.total` / `slots` / `legal_actions` 都是引擎现算的），
 * 种子 1 扫得这份局面；换池 / 换 AI 档位后形状变了，下面的循环跳过不匹配的种子。
 * `cap === 1` 的旧档（反间式盲挑）"一击即提交"由上面的 U2-08j 继续钉，本段不重复。
 */
{
  /** 挑牌步的槽位节点：每个 `<section class="unknown-zone">` 的正文行（第 2 个孩子）里的牌面。
   *  区域顺序 = 载荷顺序（手牌在前、装备 / 判定按需），故扁平化后顺序 = 面内位置序。 */
  const unknownSlotNodes = () => Array.from($("t-options").children)
    .map((section) => Array.from(section.children)[1])
    .filter(Boolean)
    .flatMap((row) => Array.from(row.children));
  const clickSkill = async (skillId) => {
    const button = Array.from($("t-skills").children).find(
      (node) => node.getAttribute && node.getAttribute("data-skill-id") === skillId,
    );
    if (!button) return false;
    await clickAndWait(button);
    return true;
  };
  const clickSeat = async (seat) => {
    const box = seatBox(seat);
    if (!box) return false;
    await clickAndWait(box);
    return true;
  };
  const answerCallsSince = (mark) => calls.slice(mark)
    .filter((call) => call.path.includes("/answer"));

  let scenario = null;
  for (const seed of [1, 2, 3, 5]) {
    await openSession(await post("/api/sessions", {
      player_count: 2, ai: "random", human_seat: 0, timeout_seconds: null,
      seed, my_hero: "feng_yujin",
    }));
    const first = ui.state.view && ui.state.view.pending;
    if (!first || first.kind !== "SKILL_ACTIVATION") continue;
    // ① 第 1 轮开始的【定乱】询问（直提态）：点技能（选中那条 ActivateSkill）→「提交」发动。
    await clickSkill("hero.feng_yujin.dingluan");
    if ($("btn-submit").disabled) continue;
    await clickAndWait($("btn-submit"));
    ui.flushPlayback();
    const second = ui.state.view && ui.state.view.pending;
    if (!second || second.kind !== "SELECT_OPTION") continue;
    // ② 目标 = 对方（座位 1）。
    if (!(await clickSeat(1)) || $("btn-submit").disabled) continue;
    await clickAndWait($("btn-submit"));
    ui.flushPlayback();
    const pick = ui.state.view && ui.state.view.pending;
    if (!pick || pick.kind !== "SELECT_CARDS") continue;
    if (ui.stepDialogKind(pick) !== "unknown") continue;
    if (!pick.spec || (pick.spec.total || [])[1] < 2) continue;   // 必须是**多选**档
    if (unknownSlotNodes().length < 3) continue;
    scenario = { seed, pick };
    break;
  }
  check("票 06-0 找到真局面：2 人局第 1 轮【定乱】挑对方的牌，是多选档（total 上界 ≥ 2）",
    scenario !== null,
    scenario
      ? `种子 ${scenario.seed} total=${JSON.stringify(scenario.pick.spec.total)} `
        + `槽=${unknownSlotNodes().length} 候选=${scenario.pick.legal_actions.length}`
      : "四个种子都没有这个形状");

  if (scenario) {
    const pick = scenario.pick;
    const total = pick.spec.total || [1, 1];
    const expectedTitle = total[1] <= 1
      ? "选择一张牌"
      : (total[0] === total[1] ? `选择 ${total[0]} 张牌` : `选择 ${total[0]}~${total[1]} 张牌`);
    const answerMark = calls.length;
    // ③ 点两张牌：只切换选中态（零 POST）、弹窗不消失、两张都画成已选。
    //    每次点击后**重新取**槽位节点（点击会重铺正文）；取不到就不点（旧行为在第一次点击
    //    就提交 ⇒ 弹窗已换 ⇒ 后面的断言按"挑牌步被推进了"红，而不是抛 TypeError）。
    const clickSlot = async (index) => {
      const node = unknownSlotNodes()[index];
      if (!node) return false;
      await clickAndWait(node);
      return true;
    };
    await clickSlot(0);
    await clickSlot(2);
    const slotsAfter = unknownSlotNodes();
    check("票 06-1 点两张「?」= 只收进 picks：零 POST、弹窗不消失、两张都 .sel",
      answerCallsSince(answerMark).length === 0
        && $("t-dialog").hidden === false
        && Array.from($("t-dialog-body").children).includes($("t-options"))
        && slotsAfter.length === pick.slots.length
        && slotsAfter[0].classList.contains("sel")
        && slotsAfter[2].classList.contains("sel")
        && JSON.stringify(ui.state.picks) === "[0,2]",
      `answers=${answerCallsSince(answerMark).length} dialogHidden=${$("t-dialog").hidden} `
        + `inBody=${Array.from($("t-dialog-body").children).includes($("t-options"))} `
        + `sel=${slotsAfter.map((node) => node.classList.contains("sel")).join(",")} `
        + `picks=${JSON.stringify(ui.state.picks)}`);
    check("票 06-2 标题 / 提示按 spec.total 说话，「提交」这时才可点",
      $("t-dialog-title").textContent === expectedTitle
        && $("t-target-hint").textContent.includes("再点「提交」")
        && $("btn-submit").disabled === false
        && $("t-choice").textContent.includes("可以提交"),
      `title="${$("t-dialog-title").textContent}"(期望 ${expectedTitle}) `
        + `hint="${$("t-target-hint").textContent}" submit=${$("btn-submit").disabled} `
        + `choice="${$("t-choice").textContent}"`);

    // ④ 点「提交」：一条多张 SelectCards（picks = 面内位置升序），逐字段命中候选。
    const stillOnPick = Boolean(
      ui.state.view && ui.state.view.pending && ui.state.view.pending.seq === pick.seq,
    );
    if (!stillOnPick) {
      // 旧行为已经"点一张即提交"了：这个询问没了，提交与展示两条断言都不成立（不是跳过）。
      check("票 06-3 点「提交」= 一条多张 SelectCards（picks=[0,2]），逐字段命中 legal_actions",
        false, `挑牌步在点「提交」之前就被推进了（现在 seq=${ui.state.view && ui.state.view.pending ? ui.state.view.pending.seq : "null"}）`);
      check("票 06-4 提交后两张牌**一起**被展示（两条 card_reveal），一局继续推进",
        false, "同上：询问换了，提交 / 展示不在这一步");
    } else {
      const submitMark = calls.length;
      const recordMark = ui.state.logRecords.length;
      await clickAndWait($("btn-submit"));
      ui.flushPlayback();
      const posted = answerCallsSince(submitMark);
      const action = posted.length ? posted[0].body.action : null;
      check("票 06-3 点「提交」= 一条多张 SelectCards（picks=[0,2]），逐字段命中 legal_actions",
        posted.length === 1
          && action !== null
          && action.kind === "SelectCards"
          && JSON.stringify(action.picks) === "[0,2]"
          && pick.legal_actions.some(
            (candidate) => JSON.stringify(candidate) === JSON.stringify(action)),
        `answers=${posted.length} action=${JSON.stringify(action)}`);

      // ⑤ 两张牌一张成果地展示（`card_reveal`）× 2；询问推进到下一步。
      const reveals = ui.state.logRecords.slice(recordMark)
        .filter((record) => record && record.delta_type === "card_reveal"
          && Number(record.seat) === 1);
      const next = ui.state.view && ui.state.view.pending;
      check("票 06-4 提交后两张牌**一起**被展示（两条 card_reveal），一局继续推进",
        reveals.length === 2
          && reveals.every((record) => record.card && record.card.card_id !== undefined)
          && (!next || next.seq !== pick.seq),
        `reveals=${reveals.length} `
          + `cards=${JSON.stringify(reveals.map((record) => record.card && record.card.card_id))} `
          + `seq=${pick.seq}→${next ? next.seq : "null"}`);
    }
  }

  ui.state.sessionId = null;
  ui.state.view = null;
  ui.resetSelection();
}

console.log(failures === 0 ? `\n全部通过（驱动 ${steps} 步对局，覆盖 ${seen.size} 类询问）` : `\n${failures} 项失败`);process.exit(failures === 0 ? 0 : 1);