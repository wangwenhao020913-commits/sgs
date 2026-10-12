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
const bridge = readFileSync("tests/support/ui_bridge.js", "utf8");
vm.runInContext(`${source}\n${bridge}`, context, { filename: "app.js" });
const ui = context.__bridge;

/* ------------------------------------------------------------------ 断言工具 */

let failures = 0;
const check = (label, ok, detail = "") => {
  if (ok) console.log(`PASS ${label}${detail ? ` — ${detail}` : ""}`);
  else { failures += 1; console.log(`FAIL ${label}${detail ? ` — ${detail}` : ""}`); }
};
const $ = (id) => documentShim.getElementById(id);
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
  ui.state.selectedCards = [];
  ui.state.selectedOption = null;
  ui.state.picks = [];
  ui.state.lastRejectReason = "";
  $("t-log").innerHTML = "";
  ui.applyView(body);
  return body;
};
const newSession = async (seed, extra = {}) => openSession(await post("/api/sessions", {
  player_count: 8, ai: "random", human_seat: 0, timeout_seconds: null, seed, ...extra,
}));
const logText = () => $("t-log").text();
const logCount = () => $("t-log").children.length;

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

/* ============ ㉿ 红测试：可点的牌 ≠ 可用的牌（刀 2） ============
 *
 * 真界面上发生过：手牌张张可点，点了一张**用不出**的牌之后提交按钮永久灰掉、
 * 界面只说"先选一项，或点一张手牌再提交"——玩家不知道是这张牌不能用于当前询问。
 * 两件事要成立：① 用不出的牌**不给点**（置灰 + 不进选中态）；② 说出**为什么**。
 */
check("㉿2 这条询问里存在一张**用不出**的手牌（前提成立）", (() => {
  const pending = ui.state.view.pending;
  const hand = ui.state.view.snapshot.self_hand || [];
  return hand.some((card) => (ui.cardCandidates ? ui.cardCandidates(pending, card.card_id).length === 0 : true));
})(), `手牌 ${(ui.state.view.snapshot.self_hand || []).length} 张 / 候选 ${ui.state.view.pending.legal_actions.length} 条`);

const unusable = (ui.state.view.snapshot.self_hand || []).find(
  (card) => !ui.cardCandidates || ui.cardCandidates(ui.state.view.pending, card.card_id).length === 0,
);
if (unusable && ui.toggleCard) {
  ui.toggleCard(unusable.card_id);
  check("㉿2a 用不出的牌**不进入**选中态", !ui.state.selectedCards.includes(unusable.card_id), `selectedCards=${JSON.stringify(ui.state.selectedCards)}`);
  check("㉿2b 用不出的牌被置灰（dis 类）", (() => {
    const chip = $("t-hand").children.find((c) => (c._html || "").includes(unusable.name) && c.classList.contains("dis"));
    return !!chip;
  })(), "手牌里应有一个 dis 的牌");
  check("㉿2c 提交按钮**不可点**", $("btn-submit").disabled === true, `disabled=${$("btn-submit").disabled}`);
  check("㉿2d 给出**为什么**（不是笼统提示）", /不能用于当前询问/.test($("t-choice").textContent), `提示="${$("t-choice").textContent}"`);
  ui.clearSelection();
  check("㉿2e 清空选择后按钮仍不可点（没选中任何东西）", $("btn-submit").disabled === true);
} else {
  check("㉿2 跳过：本局手里没有用不出的牌", true);
}

/* ============ ㉿ 红测试：技能按钮三态（刀 4 前端片） ============
 *
 * 用户要：每个技能一个按钮；主动技满足条件 = 绿色可点、不满足 = 深灰不可点；
 * 锁定/被动技 = 银灰常暗（由引擎在时机上派发或弹询问）。
 * 本片只渲染色态与可点性——**不新增引擎数据**：清单来自响应体的 `skills`（刀 4 后端片）。
 */
{
  let skillCase = null;
  for (const seed of [7, 11, 13, 17, 19, 23, 29, 31, 37, 41]) {
    await newSession(seed);
    for (let step = 0; step < 400 && ui.state.view.pending; step += 1) {
      const pending = ui.state.view.pending;
      const offered = (ui.state.view.skills || []).filter((s) => s.available);
      if (pending.kind === "PLAY_PHASE" && pending.actor === 0 && offered.length > 0) {
        // **就停在这个局面上**做断言（别再重开一局——那会把刚找到的前置丢掉）。
        skillCase = { seed, offered, skills: ui.state.view.skills };
        break;
      }
      ui.state.selectedOption = pending.legal_actions.findIndex((a) => a.kind === "EndPhase" || a.kind === "Pass");
      if (ui.state.selectedOption < 0) ui.state.selectedOption = 0;
      await ui.submitAction();
    }
    if (skillCase) break;
  }
  if (!skillCase) await newSession(7);   // 没找到前置时也留一个带技能的局面

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
    if (button) {
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

  await openSession(await post("/api/sessions", {
    player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
  }));
}

/* ============ ㉿ 红测试：同一张牌的多个目标要能点（刀 3） ============
 *
 * 引擎把**每个目标组合**枚举成一条独立候选（实测：`card_id=19` 有
 * `targets:[4]` 与 `targets:[6]` 两条）。当前渲染器按"用哪张牌"聚合，
 * 把 `targets` 塞进标签文字却做不成可点的东西——玩家选不了目标。
 */
// 造一个确定的前置：在真引擎里反复开局，直到人类座位拿到"同一张牌有两个目标"的出牌阶段
const multi = await (async () => {
  for (const seed of [1, 2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31]) {
    const body = await post("/api/sessions", {
      player_count: 8, ai: "random", human_seat: 0, timeout_seconds: null, seed,
    });
    ui.state.sessionId = body.session_id;
    ui.state.logRecords = [];
    ui.applyView(body);
    for (let step = 0; step < 400 && ui.state.view.pending; step += 1) {
      const pending = ui.state.view.pending;
      if (pending.kind === "PLAY_PHASE" && pending.actor === 0) {
        const byCard = new Map();
        for (const action of pending.legal_actions) {
          const id = action.card && action.card.card_id;
          if (id === undefined || id === null) continue;
          if (!byCard.has(id)) byCard.set(id, []);
          byCard.get(id).push(action);
        }
        const found = [...byCard.entries()].find(([, acts]) => acts.length > 1);
        if (found) return { seed, cardId: found[0], actions: found[1], pending };
      }
      ui.state.selectedOption = pending.legal_actions.findIndex((a) => a.kind === "EndPhase" || a.kind === "Pass");
      if (ui.state.selectedOption < 0) ui.state.selectedOption = 0;
      await ui.submitAction();
    }
  }
  return null;
})();

check("㉿3 找到一个前置：人类座位的同一张牌有两个目标候选", multi !== null,
  multi ? `seed=${multi.seed} card_id=${multi.cardId} 目标组合=${multi.actions.map((a) => JSON.stringify(a.targets)).join(" / ")}` : "12 个种子内没找到");

if (multi) {
  // 玩家路径：点这张手牌 → 界面上只出它的目标 → 点一个目标
  ui.state.selectedOption = null;
  ui.state.selectedCards = [multi.cardId];
  ui.renderTable();
  ui.renderPending(ui.state.view);
  const opts = $("t-options").children;
  const grouped = ui.groupActions(ui.state.view.pending.legal_actions);
  check("㉿3 聚合项按**每条候选**给出目标（同一张牌的两个目标 = 两条，不去重合并）",
    grouped.filter((g) => Array.isArray(g.targets)).length >= multi.actions.length,
    `带 targets 的聚合项 ${grouped.filter((g) => Array.isArray(g.targets)).length} 个`);
  const targetsSeen = opts.map((c) => c.getAttribute("data-targets")).filter((v) => v);
  check("㉿3a 点牌后只显示这张牌的候选，且每个目标组合各有一个 data-targets",
    targetsSeen.length === multi.actions.length
      && targetsSeen.every((v, i) => JSON.parse(v).join(",") === multi.actions[i].targets.join(",")),
    `期望 ${multi.actions.length} 个目标项，实得 ${JSON.stringify(targetsSeen)}`);

  const index = ui.state.view.pending.legal_actions.findIndex((a) => JSON.stringify(a) === JSON.stringify(multi.actions[0]));
  const targetOption = opts.find((c) => c.getAttribute("data-targets") === JSON.stringify(multi.actions[0].targets));
  check("㉿3a2 每个目标组合是一个**可点**选项（不是一段死文字）", !!targetOption,
    `选项里找不到 data-targets=${JSON.stringify(multi.actions[0].targets)}`);
  if (targetOption) {
    (targetOption.listeners.click || []).forEach((fn) => fn({ target: targetOption }));
  }
  check("㉿3a3 点了目标选项后它被选中", ui.state.selectedOption === index, `selectedOption=${ui.state.selectedOption} want=${index}`);
  const before = calls.length;
  await ui.submitAction();
  const sent = calls.slice(before).find((c) => c.path.includes("/answer"));
  const sentAction = sent && sent.body ? sent.body.action : null;
  check("㉿3b 点目标后提交的正是**那一条**候选（targets 逐字段相同）",
    sentAction !== null && JSON.stringify(sentAction) === JSON.stringify(multi.actions[0]),
    `sent=${JSON.stringify(sentAction && sentAction.targets)} want=${JSON.stringify(multi.actions[0].targets)}`);
  ui.state.selectedCards = [];
}

// 回到干净的一局，别把后续断言带进上面那个中间局面
{
  const fresh = await post("/api/sessions", {
    player_count: 8, ai: "heuristic", human_seat: 0, timeout_seconds: null, seed: 20261012,
  });
  ui.state.sessionId = fresh.session_id;
  ui.state.logRecords = [];
  ui.state.selectedOption = null;
  ui.state.selectedCards = [];
  ui.state.picks = [];
  ui.state.lastRejectReason = "";
  ui.applyView(fresh);
}

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
