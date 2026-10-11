/* M4 前端渲染烟测（一次性工作产物，不入库）。
 *
 * 浏览器工具在这台机器上不可用（无 `bsk`、无 Chromium、无 cargo），所以这里用
 * **最小 DOM 垫片 + node:vm** 把**真的 `tools/sgs_web/static/app.js`** 跑起来，
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
  }
  get textContent() { return this._text + this.children.map((c) => c.textContent || c.text()).join(""); }
  set textContent(v) { this._text = String(v); this._html = ""; this.children = []; }
  get innerHTML() { return this._html + this.children.map((c) => c.dump()).join(""); }
  // 垫片的 innerHTML 赋值要**真的清空子节点**（真 DOM 就是这么干的；不清空会造出
  // "元素累积"这种假 bug —— 第一版垫片在这里骗过我一次）。
  set innerHTML(v) { this._html = String(v); this.children = []; this._text = ""; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(k, fn) { (this.listeners[k] ||= []).push(fn); }
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

/* -------------------------------------------------- 只记录 app.js 发出的请求 */

const calls = [];
const realFetch = globalThis.fetch;
globalThis.fetch = async (path, options) => {
  if (typeof path === "string" && path.startsWith("/api/")) {
    calls.push({ path, body: options && options.body ? JSON.parse(options.body) : null });
    return realFetch(`${BASE}${path}`, options);
  }
  return realFetch(path, options);
};

const context = vm.createContext({
  document: documentShim,
  window: { setTimeout: () => 0, setInterval: () => 0 },
  fetch: globalThis.fetch,
  performance: { timeOrigin: Date.now() },
  console, Date, JSON, Object, Array, Number, String, Math, encodeURIComponent,
});
context.globalThis = context;
const source = readFileSync("tools/sgs_web/static/app.js", "utf8");
vm.runInContext(
  `${source}\n;globalThis.__bridge = { state, applyView, renderPending, buildAction, submitAction, loadRuns, openReplay, renderReplay };\n`,
  context,
  { filename: "app.js" },
);
const ui = context.__bridge;

/* ------------------------------------------------------------------ 断言工具 */

let failures = 0;
const check = (label, ok, detail = "") => {
  if (ok) console.log(`PASS ${label}${detail ? ` — ${detail}` : ""}`);
  else { failures += 1; console.log(`FAIL ${label}${detail ? ` — ${detail}` : ""}`); }
};
const $ = (id) => documentShim.getElementById(id);

/* ------------------------------------------------ ① 建局 + 牌桌/手牌/事件流 */

const created = await post("/api/sessions", {
  player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
});
ui.state.sessionId = created.session_id;
ui.applyView(created);

check("牌桌渲染出 8 个座位", $("t-seats").children.length === 8, `实得 ${$("t-seats").children.length}`);
check("我的座位带 me 标记", $("t-seats").children[0].classList.contains("me"));
check("座位块显示武将（公开信息）", /[a-z_]{4,}/.test($("t-seats").children[0].text()), $("t-seats").children[0].text().replace(/\s+/g, " ").trim().slice(0, 40));
check("我的武将已渲染", $("t-hero").textContent !== "（无武将）", $("t-hero").textContent);
check("手牌渲染出牌面且张数一致", $("t-hand").children.length === created.snapshot.self_hand.length && $("t-hand").children.length > 0, `${$("t-hand").children.length} 张`);
check("对手座位不出现牌面名", !["杀", "闪", "桃", "无懈可击"].some((n) => $("t-seats").children[1].text().includes(n)), $("t-seats").children[1].text().replace(/\s+/g, " ").trim().slice(0, 40));
check("种子已显示", $("t-seed").textContent === String(created.seed), $("t-seed").textContent);
check("事件流已渲染开局记录", $("t-log").children.length > 0, `${$("t-log").children.length} 条`);

/* ------------------------------------------- ② 九类询问：渲染 + 提交纪律 */

const pending0 = created.pending;
check("建局后有挂起询问", !!pending0, pending0 && pending0.kind);
check("候选已聚合渲染（不刷屏）", $("t-options").children.length > 0, `${$("t-options").children.length} 个选项 / 服务端 ${pending0.legal_actions.length} 条候选`);
check("询问文案已渲染", $("t-prompt").textContent.length > 0, $("t-prompt").textContent.slice(0, 40));
check("时限关闭时不显示倒计时", $("t-timer").textContent === "", `"${$("t-timer").textContent}"`);

const seen = new Set();
const renderErrors = [];
let steps = 0;
while (ui.state.view.pending && steps < 1200) {
  const p = ui.state.view.pending;
  seen.add(p.kind);
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
  ui.state.selectedOption = terminal >= 0 ? terminal : 0;
  const chosen = p.legal_actions[ui.state.selectedOption];
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
    if ($("btn-submit").disabled) { renderErrors.push(`SelectCards 选够 ${least} 张后提交按钮仍不可点`); break; }
  }
  if (chosen.kind === "DiscardCards") {
    const need = (chosen.cards || []).length;
    ui.state.selectedCards = [];
    const hand = ui.state.view.snapshot.self_hand.map((c) => c.card_id);
    ui.state.selectedCards = hand.slice(0, need);
    if (ui.state.selectedCards.length < need) { renderErrors.push("弃牌张数不够（手牌不足）"); break; }
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
check("对局已终局", ui.state.view.finished === true);
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

console.log(failures === 0 ? `\n全部通过（驱动 ${steps} 步对局，覆盖 ${seen.size} 类询问）` : `\n${failures} 项失败`);
process.exit(failures === 0 ? 0 : 1);
