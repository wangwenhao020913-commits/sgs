/* 事件流上限探针（table-ux-overhaul 票 01）：把**真的** `log.js` 放进最小 DOM 垫片，
 * 走生产入口（简易档 `appendNarrativeEntry` / 完整档 `appendLog`）各喂 2×上限条数，
 * 再数 `#t-log` 的子节点。
 *
 * 为什么不用 `ui_smoke.mjs`：那条要起真服务、跑整局，慢且与本票无关；这里只需要
 * "一个 `<li>` 流 + 三个全局（`$` / `state` / `document`）"，与 `pool_probe.mjs` 同姿态。
 * 加载纪律不变：脚本从 `tools/sgs_web/static/` 读真文件，垫片只补 DOM 与状态。
 *
 * 输出（stdout，JSON）：
 *   {"limit": 500, "appended": 1000, "narrativeNodes": 500, "logNodes": 500,
 *    "newestKept": true}
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";

const STATIC_DIR = "tools/sgs_web/static";
const APPEND_PER_MODE = 1000;

class ShimNode {
  constructor(tag) {
    this.tagName = String(tag || "").toUpperCase();
    this._children = [];
    this.className = "";
    this._textContent = "";
    this._innerHTML = "";
    this.listeners = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.id = "";
  }

  get children() {
    return this._children;
  }

  get lastChild() {
    return this._children.length ? this._children[this._children.length - 1] : null;
  }

  appendChild(node) {
    this._children.push(node);
    node.parentNode = this;
    return node;
  }

  removeChild(node) {
    const index = this._children.indexOf(node);
    if (index < 0) throw new Error("removeChild：节点不在这个父节点下");
    this._children.splice(index, 1);
    node.parentNode = null;
    return node;
  }

  get textContent() {
    if (this._textContent) return this._textContent;
    return this._children.map((child) => child.textContent).join("");
  }

  set textContent(value) {
    this._textContent = String(value);
    this._children = [];
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    // 真 DOM 赋值 innerHTML 会清空子节点；垫片照做（不清空会造出"元素累积"的假象）。
    this._innerHTML = String(value);
    this._children = [];
  }

  scrollIntoView() {}

  setAttribute() {}

  getAttribute() {
    return null;
  }
}

const byId = new Map();
const node = (id) => {
  if (!byId.has(id)) {
    const created = new ShimNode("div");
    created.id = id;
    byId.set(id, created);
  }
  return byId.get(id);
};

const documentShim = {
  createElement: (tag) => new ShimNode(tag),
  getElementById: (id) => byId.get(id) || null,
};

/* `logMode()` 读 `#t-log-mode`；完整档探针要用 `all`（不做查看者过滤）。 */
node("t-log-mode").value = "all";

/* 垫片状态只要 `log.js` 读过的那几格：`keepRecord` 读 `state.view.human_seat`；
 * 两份本地序列由 `log.js` 的**同一个**上限裁剪（本探针只读它们的长度做旁证）。 */
const state = { view: { human_seat: 0 }, logRecords: [], narrative: [], narrativeShown: 0 };

const context = vm.createContext({
  document: documentShim,
  $: (id) => byId.get(id) || null,
  state,
  console,
});
context.globalThis = context;

vm.runInContext(readFileSync(`${STATIC_DIR}/log.js`, "utf8"), context, { filename: "log.js" });

const limit = vm.runInContext(
  'typeof LOG_ITEM_LIMIT === "number" ? LOG_ITEM_LIMIT : null',
  context,
);
if (!Number.isFinite(limit) || limit <= 0) {
  throw new Error("log.js 没有声明上限常量 LOG_ITEM_LIMIT");
}

const log = node("t-log");
for (let index = 0; index < APPEND_PER_MODE; index += 1) {
  vm.runInContext(
    `appendNarrativeEntry({ kind: "line", seq: ${index + 1}, text: "叙事 ${index + 1}",
       segments: [{ text: "叙事 ${index + 1}", cls: "act" }] })`,
    context,
  );
}
const narrativeNodes = log.children.length;
const narrativeNewest = log.lastChild ? log.lastChild.textContent : "";

for (let index = 0; index < APPEND_PER_MODE; index += 1) {
  vm.runInContext(
    `appendLog({ kind: "query", seq: ${index + 1},
       query: { kind: "RESPOND", actor: 0 } }, 0, { mode: "all" })`,
    context,
  );
}
const logNodes = log.children.length;
// 完整档的 `<li>` 是 `innerHTML = describeRecord(...)`（与真 DOM 同一条渲染口），
// 故这里读 innerHTML 而不是 textContent（垫片的 textContent 只汇总子节点）。
const logNewest = log.lastChild ? log.lastChild.innerHTML : "";

const result = {
  limit,
  appended: APPEND_PER_MODE,
  narrativeNodes,
  logNodes,
  // 留下的是**最新**的那几条：末条是最后喂进去的那一条（两条路径各验一次）。
  newestKept: narrativeNewest.includes(String(APPEND_PER_MODE))
    && logNewest.includes(`#${APPEND_PER_MODE}`),
};
process.stdout.write(`${JSON.stringify(result)}\n`);
