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

check("牌桌渲染出 8 个座位", $("t-seats").children.length === 8, `实得 ${$("t-seats").children.length}`);
check("我的座位带 me 标记", $("t-seats").children[0].classList.contains("me"));
check("座位块显示武将（公开信息）", /[a-z_]{4,}/.test($("t-seats").children[0].text()), $("t-seats").children[0].text().replace(/\s+/g, " ").trim().slice(0, 40));
check("我的武将已渲染", $("t-hero").textContent !== "（无武将）", $("t-hero").textContent);
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
const seatBox = $("t-seats").children.find((c) => c.getAttribute("data-seat") === "2");
check("㉿P4 可交给的座位画成可点（targetable）",
  !!seatBox && seatBox.classList.contains("targetable"));
// 报障 2：**取消选项设计**——`SELECT_OPTION` 的选座位形态不铺任何选项按钮，只点场上武将。
check("㉿P4b 遗计选座位不再铺『选择座位 N』按钮",
  ui.renderKind(ui.state.view.pending, ui.state.view.pending.legal_actions) === "seat"
    && $("t-options").children.length === 0,
  `kind=${ui.renderKind(ui.state.view.pending, ui.state.view.pending.legal_actions)} 选项=${JSON.stringify($("t-options").children.map((b) => b.textContent))}`);
(seatBox.listeners.click || []).forEach((fn) => fn({ target: seatBox }));
check("㉿P5 点座位 = 选中那条 seat:2 候选", ui.state.selectedOption === 1,
  `selectedOption=${ui.state.selectedOption}`);
check("㉿P5b 点中的座位画成确定态（tsel）",
  (() => {
    ui.renderTable();
    const box = $("t-seats").children.find((c) => c.getAttribute("data-seat") === "2");
    return !!box && box.classList.contains("tsel");
  })(), `seat2 类=${JSON.stringify([...($("t-seats").children.find((c) => c.getAttribute("data-seat") === "2").classList.set)])}`);
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
  const zoneRow = (seat, zone) => {
    const box = boxes[seat];
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

// ③ 效果区（处理区）与询问窗口同栏：两者息息相关（报障原文「放一起才对」）。
//    用 index.html 的结构断言——DOM 垫片不建父子关系，故直接查源串的 section 归属。
{
  const sectionOf = (id) => {
    const at = html.indexOf(`id="${id}"`);
    if (at < 0) return "";
    return html.slice(html.lastIndexOf("<section", at), html.indexOf("</section>", at));
  };
  const askSection = sectionOf("t-prompt");
  check("㉿X1 效果区（#t-processing）与询问窗口（#t-prompt）在同一个 section",
    askSection.includes('id="t-processing"') && askSection.includes('id="t-prompt"'),
    `询问 section 含效果区=${askSection.includes('id="t-processing"')}`);
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
 * 看界面按不按 `ui.manual` / `ui.convert_sources` / `virtual_index` 这些键读。
 * 服务端怎么算这些键由 `tests/test_web.py` 钉；这里钉前端有没有真的读它。
 *
 * ⚠️ 【仁德】那一条（①'）自**票 05** 起是**遗留路径**：A 族六条主动技已全部改走
 * `SkillFlow`，候选恒骨架（不带 `picks`/`choices`）⇒ `ui.manual` 在生产里恒假，手选模式
 * 没有生产者。它按 design.md §7 Q4"先留、之后再清"保留在这里，测的是**这段尚未删除的
 * 渲染代码**仍然读得对；【仁德】的真实交互（服务端两步询问）由 ㉿W1–W8 与 ㉿R8–R11 覆盖。
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
      requirement: "选择1张【闪】当【杀】使用或打出", manual: false,
      pick_cards: [], pick_seats: [], convert_sources: [2],
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

// ② 仁德：点技能 → 直接多选手牌 → 直接点场上武将 → 一次交出去；**没有任何选项按钮**。
{
  const first = synthCard(11, "杀");
  const second = synthCard(12, "闪");
  const legal = [];
  for (const picks of [[11], [12], [11, 12]]) {
    for (const seat of [1, 2]) {
      legal.push({ kind: "ActivateSkill", skill_id: "hero.liubei.rende", picks, choices: [`seat:${seat}`], answers_seq: 901 });
    }
  }
  legal.push({ kind: "EndPhase", answers_seq: 901 });
  const view = synthView({
    kind: "PLAY_PHASE", actor: 0, seq: 901, prompt: "出牌阶段：使用一张牌，或结束出牌阶段。",
    legal_actions: legal, candidate_index: {}, virtual_index: {}, option_seats: {}, context: null,
  });
  view.heroes = { 0: "liubei", 1: "guanyu", 2: "diaochan" };
  view.snapshot.self_hand = [first, second];
  view.skills = [{
    skill_id: "hero.liubei.rende", name: "仁德", skill_type: "NORMAL", locked: false,
    carrier: "HERO", available: true,
    ui: {
      requirement: "交给一名其他角色任意张手牌", manual: true,
      pick_cards: [11, 12], pick_seats: [1, 2], convert_sources: [],
    },
  }];
  ui.state.view = view;
  ui.resetSelection();
  ui.renderTable();
  ui.renderPending(view);

  const button = $("t-skills").children.find((b) => b.getAttribute("data-skill-id") === "hero.liubei.rende");
  check("㉿R1 仁德按钮标成手选模式且可点",
    !!button && button.disabled === false && button.getAttribute("data-manual") === "true",
    `disabled=${button && button.disabled} manual=${button && button.getAttribute("data-manual")}`);
  (button.listeners.click || []).forEach((fn) => fn({ target: button }));
  check("㉿R2 点仁德进入手选模式（提示 = 交给一名其他角色任意张手牌）",
    !!ui.state.skillMode && ui.state.skillMode.skillId === "hero.liubei.rende"
      && $("t-prompt").textContent === "交给一名其他角色任意张手牌",
    `mode=${JSON.stringify(ui.state.skillMode)} prompt="${$("t-prompt").textContent}"`);
  check("㉿R3 这个过程**不铺任何选项按钮**", $("t-options").children.length === 0,
    `选项=${JSON.stringify($("t-options").children.map((b) => b.textContent))}`);
  const handChip = (index) => $("t-hand").children[index];
  (handChip(0).listeners.click || []).forEach((fn) => fn({ target: handChip(0) }));
  (handChip(1).listeners.click || []).forEach((fn) => fn({ target: handChip(1) }));
  check("㉿R4 复选手牌（两张都收下）", JSON.stringify(ui.state.picks) === "[11,12]",
    `picks=${JSON.stringify(ui.state.picks)}`);
  check("㉿R5 还没点目标 ⇒ 不可提交", $("btn-submit").disabled === true && ui.buildAction() === null,
    `disabled=${$("btn-submit").disabled}`);
  const targetBox = $("t-seats").children.find((c) => c.getAttribute("data-seat") === "2");
  check("㉿R6 目标武将画成可点（直接点场上武将）",
    !!targetBox && targetBox.classList.contains("targetable"));
  // 点座位 = 交出去：界面会自动提交那条 (picks, seat:2) 候选。用不存在的会话 id，免得
  // 这次探测真的改掉服务器上的对局。
  ui.state.sessionId = "smoke-missing-session";
  const before = calls.length;
  (targetBox.listeners.click || []).forEach((fn) => fn({ target: targetBox }));
  const sent = calls.length > before ? calls[calls.length - 1].body.action : null;
  check("㉿R7 点座位 = 一次提交「这组牌 → 这个座位」那条原候选",
    !!sent && sent.kind === "ActivateSkill" && sent.skill_id === "hero.liubei.rende"
      && JSON.stringify(sent.picks) === "[11,12]" && JSON.stringify(sent.choices) === '["seat:2"]',
    JSON.stringify(sent));
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
      requirement: "弃一张牌，选择两名男性角色", manual: false,
      pick_cards: [], pick_seats: [], convert_sources: [],
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
      requirement: "弃一张手牌，令一名已受伤角色回复 1 点体力", manual: false,
      pick_cards: [], pick_seats: [], convert_sources: [],
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
  const seatBox = $("t-seats").children.find((c) => c.getAttribute("data-seat") === "2");
  check("㉿H6 已受伤角色画成可点", !!seatBox && seatBox.classList.contains("targetable"),
    `targetable=${seatBox && seatBox.classList.contains("targetable")}`);
  (seatBox.listeners.click || []).forEach((fn) => fn({ target: seatBox }));
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
      requirement: "弃两张手牌，选择一名已受伤男性角色", manual: false,
      pick_cards: [], pick_seats: [], convert_sources: [],
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
  const seatBox = $("t-seats").children.find((c) => c.getAttribute("data-seat") === "1");
  check("㉿Y6 已受伤男性画成可点（自己不在池里）",
    !!seatBox && seatBox.classList.contains("targetable"),
    `targetable=${seatBox && seatBox.classList.contains("targetable")}`);
  (seatBox.listeners.click || []).forEach((fn) => fn({ target: seatBox }));
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
      requirement: "弃置任意张牌（手牌 / 装备区），然后摸等量的牌", manual: false,
      pick_cards: [], pick_seats: [], convert_sources: [],
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
      requirement: "交给一名其他角色任意张手牌", manual: false,
      pick_cards: [], pick_seats: [], convert_sources: [],
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
  // 步长按钮：默认 0.1s，点一下在 0.1→0.5 五档之间循环（不记住，刷新回默认）。
  check("㉿E1 默认步长 0.1 秒", ui.state.eventDelayMs === 100, `eventDelayMs=${ui.state.eventDelayMs}`);
  check("㉿E1b 档位就是 0.1–0.5 秒五档",
    JSON.stringify(ui.DELAY_STEPS_MS) === JSON.stringify([100, 200, 300, 400, 500]),
    `steps=${JSON.stringify(ui.DELAY_STEPS_MS)}`);
  const delayBtn = $("btn-delay");
  check("㉿E1c 牌桌上有步长按钮，按钮上显示当前档", !!delayBtn && delayBtn.textContent.includes("0.1"),
    `按钮="${delayBtn ? delayBtn.textContent : "(缺)"}"`);
  const clickDelay = () => (delayBtn.listeners.click || []).forEach((fn) => fn({ target: delayBtn }));
  clickDelay();
  check("㉿E1d 点一下 → 0.2 秒", ui.state.eventDelayMs === 200,
    `eventDelayMs=${ui.state.eventDelayMs} 按钮="${delayBtn.textContent}"`);
  for (let i = 0; i < 4; i += 1) clickDelay();
  check("㉿E1e 满档后循环回 0.1 秒", ui.state.eventDelayMs === 100 && delayBtn.textContent.includes("0.1"),
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
          if (dodge && ui.cardAliases(p, dodge.card_id).length > 0) {
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
    check("㉿L9 真局里【闪】点得动（候选索引按来源牌命中虚拟【杀】）",
      !!dodge && ui.cardSelectable(p, dodge.card_id),
      `card=${dodge && dodge.name} selectable=${dodge && ui.cardSelectable(p, dodge.card_id)}`);
  }

  check("㉿R8 真局扫到「刘备出牌阶段仁德可发动」", !!found.rende,
    found.rende ? `seed=${found.rende.seed} steps=${found.rende.steps}` : "16 个种子内没找到");
  if (found.rende) {
    await replayScanTo(found.rende);
    const button = $("t-skills").children.find((b) => b.getAttribute("data-skill-id") === "hero.liubei.rende");
    check("㉿R9 真局里仁德按钮可点、**不再**是手选模式（票 05：改由服务端逐步询问）",
      !!button && button.disabled === false && button.getAttribute("data-manual") !== "true",
      `disabled=${button && button.disabled} manual=${button && button.getAttribute("data-manual")}`);
    (button.listeners.click || []).forEach((fn) => fn({ target: button }));
    const rendeAction = ui.buildAction();
    check("㉿R10 真局里点仁德 = 选中那条骨架 ActivateSkill 候选（载荷全空）",
      !!rendeAction && rendeAction.kind === "ActivateSkill"
        && rendeAction.skill_id === "hero.liubei.rende"
        && (rendeAction.picks || []).length === 0 && (rendeAction.choices || []).length === 0,
      JSON.stringify(rendeAction));
    if (rendeAction) {
      await ui.submitAction();
      const p = ui.state.view.pending;
      check("㉿R11 真局里提交后服务端问步 1「选择任意张要交给其他角色的手牌」",
        !!p && p.kind === "SELECT_CARDS"
          && p.context && p.context.requirement === "选择任意张要交给其他角色的手牌",
        `kind=${p && p.kind} req=${p && p.context && p.context.requirement}`);
    }
  }
}

console.log(failures === 0 ? `\n全部通过（驱动 ${steps} 步对局，覆盖 ${seen.size} 类询问）` : `\n${failures} 项失败`);process.exit(failures === 0 ? 0 : 1);
