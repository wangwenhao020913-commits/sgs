/* SGS 前端 · 提示区文案模板（table-ux-overhaul 票 07 / spec §1 #5 · §2 Q11 / Q18 / Q23）。
 *
 * **要解决什么**：提示区那几句"现在要做什么"是死的（服务端一句 `Step.wording`，前端只会
 * 说"选牌 / 选目标"）。本文件把每个（技能, 步骤）拆成一对 `ask` / `done` 模板，逐级累加
 * 拼出「弃置一张【桃♥7】，选择1名男性角色对【3号位陆逊】使用决斗」这种句子。
 *
 * 数据形状（`prompts.json`，与 `anim.ini` 同姿态：页面启动时异步载入，缺键逐级回退）：
 *
 *   {
 *     "hero.jie_diaochan.lijian": {
 *       "steps": {
 *         "card":   { "ask": "选择1张牌弃置",              "done": "弃置一张【{card}】" },
 *         "victim": { "ask": "选择1名男性角色成为决斗目标" },
 *         "user":   { "ask": "选择1名男性角色对【{seat}】使用决斗" }
 *       }
 *     },
 *     "window:dodge": { "ask": "是否打出【闪】" }
 *   }
 *
 * 键族两条：
 *  - `<skill_id>`（服务端 `view.skills[].skill_id` 原样，如 `hero.diaochan.lijian`）——
 *    预发动 / 技能流水线的**每一步**一对 `ask` / `done`；
 *  - `window:<cause>`（`pending.cause`，如 `DODGE` → `window:DODGE`）——窗口式询问的一句问法。
 *
 * 拼法：`提示文本 = Σ(done[i] for i < 光标) + ask[光标]`，用「，」连接、空段跳过。
 * 占位符取值的**唯一**口径：
 *  - `done` 取**这一步自己**的答案（弃了哪张牌 / 选了哪个座位）；
 *  - `ask` 取**前一步最近一次已答**的值（问句回指前一步，例：界【离间】第 3 步
 *    「选择1名男性角色对【{seat}】使用决斗」里的 `{seat}` 就是第 2 步选的目标）；
 *  - `{n}` 例外：`ask` 读这一步要几个（`Step.min`），`done` 读这一步答了几个。
 *
 * 三层回退（spec §7 最后一条；**唯一实现**是本文件的 :func:`promptQuestionForStep`）：
 *   ① 本文件的模板（`prompts.json` ⊕ `localStorage['sgs.prompts']` 覆盖层，键级覆盖）
 *   ② 服务端 `Step.wording`（= `SkillDef.step_requirements`，由 `flow.manifest` 填进清单）
 *   ③ 前端通用文案：`选择{n}张牌` / `选择{n}名角色` / `选一项`
 *
 * 占位符**白名单**只有七个：`{card}{seat}{name}{n}{suit}{rank}{hero}`（spec §2 Q18）。
 * 写错 / 值取不到 ⇒ **整条模板作废**、落到下一层——绝不把 `{card}` 原样印到界面上。
 *
 * **硬约束（spec §6 #3：记录流 = 权威事实）**：本文件只做**显示覆盖**。文案绝不写进
 * `Query.prompt`——它在记录里（`QueryRecord` 内联整份 Query），改了会打翻全部金录像。
 * 本文件只读 `state` / `view`，不回写任何服务端载荷、不发请求。
 */

"use strict";

/* ------------------------------------------------------------ 常量与状态 */

/** 静态资源名（与 `anim.ini` 同目录、同姿态：`fetch` 相对路径，缺文件 = 没有模板层）。 */
const PROMPT_FILE_NAME = "prompts.json";

/** 用户覆盖层（游戏内「改这段文案」写入；键级叠加在文件层上）。 */
const PROMPT_OVERLAY_KEY = "sgs.prompts";

/** 占位符**白名单**（spec §2 Q18）：只有这七个。写错 = 整条模板作废。 */
const PROMPT_SLOT_WHITELIST = Object.freeze(["card", "seat", "name", "n", "suit", "rank", "hero"]);

/** `{槽名}` 的匹配式。一次定义、校验与填充共用（两处各写一份就会漂）。 */
const PROMPT_SLOT_PATTERN = /\{([^{}]*)\}/g;

/** 文件层（`prompts.json` 原样）与覆盖层（localStorage）分开存：合并只在读取时做。 */
const promptFileTemplates = {};
let promptOverlayTemplates = {};
let promptFileLoaded = false;

/** 一次加载 / 解析的问题（正常时为空；探针与烟测读它）。 */
const promptNotes = [];

/* ------------------------------------------------------------ 解析 / 合并 */

function promptRememberNote(text) {
  const line = String(text || "").trim();
  if (line && !promptNotes.includes(line)) promptNotes.push(line);
}

/** 载入/解析期的问题清单（**只读**；不进 DOM，缺模板时的产品行为就是逐级回退）。 */
function promptProblems() {
  return promptNotes.slice();
}

/** 静态服务可能把**缺失**的 `prompts.json` 回落到 `index.html`（200 + HTML）：整份当取不到。 */
function promptLooksLikeHtml(text) {
  return /^\s*(?:<!doctype\s+html\b|<html\b)/i.test(String(text || ""));
}

/** 一行模板（`ask` / `done`）：只认字符串，别的类型记一条问题、当没写。 */
function promptTemplateText(raw) {
  return typeof raw === "string" ? raw : null;
}

/** 解析一个键的条目（`{steps:{step:{ask,done}}, ask?}`）；形状不对 ⇒ `null`。 */
function promptParseEntry(key, raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    promptRememberNote(`${key} 不是对象，已忽略`);
    return null;
  }
  const entry = {};
  if (raw.ask !== undefined) {
    const ask = promptTemplateText(raw.ask);
    if (ask === null) promptRememberNote(`${key}.ask 不是字符串，已忽略`);
    else entry.ask = ask;
  }
  if (raw.steps !== undefined) {
    if (!raw.steps || typeof raw.steps !== "object" || Array.isArray(raw.steps)) {
      promptRememberNote(`${key}.steps 不是对象，已忽略`);
    } else {
      const steps = {};
      for (const [step, template] of Object.entries(raw.steps)) {
        if (!template || typeof template !== "object" || Array.isArray(template)) {
          promptRememberNote(`${key}.steps.${step} 不是对象，已忽略`);
          continue;
        }
        const clean = {};
        for (const field of ["ask", "done"]) {
          if (template[field] === undefined) continue;
          const text = promptTemplateText(template[field]);
          if (text === null) promptRememberNote(`${key}.steps.${step}.${field} 不是字符串，已忽略`);
          else clean[field] = text;
        }
        if (Object.keys(clean).length) steps[step] = clean;
      }
      if (Object.keys(steps).length) entry.steps = steps;
    }
  }
  return Object.keys(entry).length ? entry : null;
}

/** `prompts.json` 文本 → 模板表。非法条目逐条跳过（一处写错不拖垮整份文件）。 */
function promptParseTemplates(text) {
  const out = {};
  const raw = String(text || "").trim();
  if (!raw) return out;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    promptRememberNote(`prompts.json 不是合法 JSON：${error.message}`);
    return out;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    promptRememberNote("prompts.json 顶层必须是对象");
    return out;
  }
  for (const [key, entry] of Object.entries(parsed)) {
    const clean = promptParseEntry(key, entry);
    if (clean) out[key] = clean;
  }
  return out;
}

/** 键级合并（文件层 ⊕ 覆盖层）：**同一步**的字段逐个覆盖，覆盖层没写的字段保留文件层。 */
function promptMergeTemplates(base, over) {
  if (!base) return over || null;
  if (!over) return base;
  const merged = { ...base };
  if (over.ask !== undefined) merged.ask = over.ask;
  if (over.steps) {
    merged.steps = { ...(base.steps || {}) };
    for (const [step, template] of Object.entries(over.steps)) {
      merged.steps[step] = { ...(merged.steps[step] || {}), ...template };
    }
  }
  return merged;
}

/** 一个键**生效中**的模板（文件 ⊕ 覆盖）；`null` = 两层都没有这一条。 */
function promptTemplateFor(key) {
  if (!key) return null;
  return promptMergeTemplates(promptFileTemplates[key], promptOverlayTemplates[key]);
}

/* ------------------------------------------------------------ 载入 / 覆盖层 */

/** `window.localStorage` 在隐私模式 / 禁用 Cookie 时**读一下就抛**；取不到就当没有覆盖层。 */
function promptOverlayStorage() {
  try {
    return window.localStorage || null;
  } catch (error) {
    return null;
  }
}

/** 读覆盖层（`localStorage['sgs.prompts']`）；坏的 JSON 记一条问题、当没有。 */
function promptReloadOverlay() {
  promptOverlayTemplates = {};
  const store = promptOverlayStorage();
  if (!store || typeof store.getItem !== "function") return promptOverlayTemplates;
  let raw = null;
  try {
    raw = store.getItem(PROMPT_OVERLAY_KEY);
  } catch (error) {
    raw = null;
  }
  if (!raw) return promptOverlayTemplates;
  promptOverlayTemplates = promptParseTemplates(raw);
  return promptOverlayTemplates;
}

/** 载入 `prompts.json`（页面启动即读）。取不到 = 只有覆盖层；两层都空 = 走第二层。
 *
 * 与 `anim.js:animLoadConfig` 同姿态：不抛错、缺文件不挡页面，只留可读的问题清单。
 */
function promptLoadTemplates() {
  promptReloadOverlay();
  const apply = (text, failed) => {
    for (const key of Object.keys(promptFileTemplates)) delete promptFileTemplates[key];
    const unavailable = failed || promptLooksLikeHtml(text);
    const parsed = unavailable ? {} : promptParseTemplates(text);
    Object.assign(promptFileTemplates, parsed);
    promptFileLoaded = !unavailable && Object.keys(parsed).length > 0;
    if (unavailable) promptRememberNote("prompts.json 取不到，文案走服务端 wording");
    if (typeof promptSyncEditor === "function") promptSyncEditor();
    return promptFileTemplates;
  };
  if (typeof fetch !== "function") return Promise.resolve(apply("", true));
  let requested;
  try {
    requested = fetch(PROMPT_FILE_NAME, { cache: "no-store" });
  } catch (error) {
    return Promise.resolve(apply("", true));
  }
  return Promise.resolve(requested)
    .then((response) => {
      if (!response || !response.ok) return { text: "", failed: true };
      return Promise.resolve(response.text()).then((text) => ({ text, failed: false }));
    })
    .then((result) => apply(result.text, result.failed))
    .catch(() => apply("", true));
}

/* ------------------------------------------------------------ 占位符与槽位 */

/** 校验 + 填槽：`null` = 这条模板不可用（占位符不在白名单 / 值取不到 / 空模板）。 */
function promptRenderTemplate(template, slots) {
  if (typeof template !== "string") return null;
  const text = template.trim();
  if (!text) return null;
  let usable = true;
  const filled = text.replace(PROMPT_SLOT_PATTERN, (whole, name) => {
    if (!PROMPT_SLOT_WHITELIST.includes(name)) {
      // spec §2 Q18：白名单之外 = 写错。**整条作废**，不把这个槽原样留在界面上。
      usable = false;
      return whole;
    }
    const value = slots ? slots[name] : undefined;
    if (value === undefined || value === null || value === "") {
      usable = false;
      return whole;
    }
    return String(value);
  });
  return usable ? filled : null;
}

/** 逐级累加：`Σ done[i] + ask`，用「，」连接；空段（缺模板 / 空串）跳过。 */
function promptJoinParts(parts) {
  return (parts || [])
    .map((part) => String(part === null || part === undefined ? "" : part).trim())
    .filter(Boolean)
    .join("，");
}

/** 座位 → 「3号位陆逊」（名字与座位卡同源 `heroDisplayName`；目录没加载时只写座位号）。 */
function promptSeatLabel(seat) {
  const hero = promptHeroName(seat);
  return `${seat}号位${hero}`;
}

/** 座位的武将名（目录里没有 ⇒ 空串：不把 hero id 当名字印出来）。 */
function promptHeroName(seat) {
  const view = promptViewForText();
  const heroId = view && view.heroes ? view.heroes[seat] : null;
  if (!heroId) return "";
  const name = heroDisplayName(heroId);
  return name && name !== String(heroId) ? name : "";
}

/** 文案读哪份视图：回放期读逐事件派生的那一份，其余读权威视图（与座位卡同源）。 */
function promptViewForText() {
  if (typeof tableViewForRender === "function") {
    const view = tableViewForRender();
    if (view) return view;
  }
  return state.view;
}

/** 牌面名 + 花色 + 点数（`桃♥7`）——`{card}`。 */
function promptCardLabel(card) {
  if (!card) return "";
  const suit = card.suit ? suitSymbol(card.suit) : "";
  const rank = card.rank === null || card.rank === undefined ? "" : rankLabel(card.rank);
  return `${cardName(card)}${suit}${rank}`;
}

/** 牌 id → 卡面。只读服务端下发的牌面副本（询问面 / 快照 / 预发动手牌入口），找不到 = `null`。 */
function promptCardById(cardId) {
  const sources = [];
  const view = promptViewForText();
  const pending = view ? view.pending : null;
  if (pending) {
    for (const slot of pending.slots || []) if (slot && slot.card) sources.push(slot.card);
    for (const entry of Object.values(pending.candidate_index || {})) {
      if (entry && entry.card) sources.push(entry.card);
    }
  }
  if (state.preactivation && state.preactivation.card) sources.push(state.preactivation.card);
  const snapshot = view ? view.snapshot : null;
  if (snapshot) {
    for (const card of snapshot.self_hand || []) sources.push(card);
    for (const player of snapshot.players || []) {
      for (const zone of [player.hand, player.equip, player.judge]) {
        for (const card of zone || []) sources.push(card);
      }
    }
    for (const card of snapshot.processing || []) sources.push(card);
    if (Array.isArray(snapshot.discard)) for (const card of snapshot.discard) sources.push(card);
  }
  return sources.find((card) => card && String(card.card_id) === String(cardId)) || null;
}

/** 这一步要几个（`{n}` 的 ask 侧）：`min` 优先，退化到 `max` / 1。 */
function promptStepCount(step) {
  if (!step) return 0;
  if (Number.isInteger(step.min) && step.min > 0) return step.min;
  if (Number.isInteger(step.max) && step.max > 0) return step.max;
  return 1;
}

/** 一步的槽位值（只放白名单里的键；取不到的键**不放** ⇒ 用了它的模板回退）。 */
function promptStepSlots(pa, index, role) {
  const slots = {};
  if (!pa || !pa.steps || !pa.answers) return slots;
  const step = pa.steps[index];
  if (!step) return slots;
  const answer = pa.answers[index] || { values: [], texts: [] };
  const values = answer.values || [];
  if (role === "done") {
    if (values.length) slots.n = String(values.length);
  } else {
    slots.n = String(promptStepCount(step));
  }
  // 取值的那一步：`done` 读本步；`ask` 读**前一步最近一次已答**（问句回指前一步的答案）。
  let source = -1;
  if (role === "done") {
    if (values.length) source = index;
  } else {
    for (let at = index - 1; at >= 0; at -= 1) {
      const prior = pa.answers[at];
      if (prior && prior.values && prior.values.length) {
        source = at;
        break;
      }
    }
  }
  if (source < 0) return slots;
  const sourceStep = pa.steps[source];
  const sourceValues = (pa.answers[source] || {}).values || [];
  const kind = preactivationStepKind(sourceStep);
  if (kind === "card") {
    const cards = sourceValues.map((value) => promptCardById(value)).filter(Boolean);
    if (!cards.length) return slots;
    slots.card = cards.map((card) => promptCardLabel(card)).join("、");
    // `{name}` / `{suit}` / `{rank}` 只说**一张**牌：多张时取不到（模板回退，不猜）。
    if (cards.length === 1) {
      const card = cards[0];
      slots.name = cardName(card);
      if (card.suit) slots.suit = suitSymbol(card.suit);
      if (card.rank !== null && card.rank !== undefined) slots.rank = rankLabel(card.rank);
    }
    return slots;
  }
  if (kind === "seat") {
    const seat = Number(String(sourceValues[0]).replace(/^seat:/, ""));
    if (!Number.isInteger(seat)) return slots;
    slots.seat = promptSeatLabel(seat);
    const hero = promptHeroName(seat);
    if (hero) slots.hero = hero;
    return slots;
  }
  slots.name = sourceValues.map((value) => String(value)).join("、");
  return slots;
}

/* ------------------------------------------------------------ 累加与回退 */

/** 清单上**这一步**的模板层文案（`Σ done[i] + ask[光标]`）；`null` = 模板层没有这一句。 */
function promptComposedText(pa, index) {
  if (!pa || pa.cardEntry || !pa.skillId) return null;
  const at = index === undefined || index === null ? pa.cursor : index;
  if (!Number.isInteger(at) || at < 0 || at >= pa.steps.length) return null;
  const entry = promptTemplateFor(pa.skillId);
  const steps = entry && entry.steps;
  if (!steps) return null;
  const ask = promptRenderTemplate(
    (steps[pa.steps[at].name] || {}).ask,
    promptStepSlots(pa, at, "ask"),
  );
  // 当前这一步没有可用的问句 ⇒ 整层让位：第二层的 `Step.wording` 才是完整的一句。
  if (!ask) return null;
  const parts = [];
  for (let before = 0; before < at; before += 1) {
    const done = promptRenderTemplate(
      (steps[pa.steps[before].name] || {}).done,
      promptStepSlots(pa, before, "done"),
    );
    if (done) parts.push(done);
  }
  parts.push(ask);
  return promptJoinParts(parts);
}

/** 第三层：前端通用文案（不认技能 / 牌名，只读这一步的声明面）。 */
function promptGenericStepText(step) {
  const kind = step ? preactivationStepKind(step) : "option";
  if (kind === "option") return "选一项";
  const unit = kind === "seat" ? "名角色" : "张牌";
  const min = Number.isInteger(step.min) ? step.min : 0;
  const max = Number.isInteger(step.max) ? step.max : null;
  if (max !== null && min === max) return `选择${min}${unit}`;
  if (max !== null) return `选择${min}–${max}${unit}`;
  if (min > 0) return `选择至少${min}${unit}`;
  return `选择任意${unit}`;
}

/** **三层回退的唯一实现**：① 模板层 → ② 服务端 `Step.wording` → ③ 前端通用文案。
 *
 * 消费点只有一个：预发动提示行那一句问法（`preactivation.js:preactivationStepHint`）；
 * 票 16 起步骤不再画成按钮，故也没有第二个读它去铺"步骤条标签"的地方。窗口式询问的
 * `window:<cause>` 走本文件 :func:`promptWindowAsk`，同一份模板表、同样缺键即回退。
 * 模板层缺键时输出与改前逐字相同，故"没有模板的技能"行为不变（票面 §会红的既有断言）。
 */
function promptQuestionForStep(pa, step, index) {
  const composed = promptComposedText(pa, index);
  if (composed) return composed;
  const wording = step && typeof step.wording === "string" ? step.wording.trim() : "";
  if (wording) return wording;
  return promptGenericStepText(step);
}

/** 窗口式询问的一句问法（`window:<cause>`）：缺键 / 取不到 ⇒ `null`（不覆盖专用面文案）。 */
function promptWindowAsk(pending) {
  if (!pending) return null;
  const cause = pending.cause ? String(pending.cause) : "";
  if (!cause) return null;
  const entry = promptTemplateFor(`window:${cause}`);
  if (!entry) return null;
  const context = pending.context || {};
  const slots = {};
  // 窗口没有步骤答案可回指：只填**载荷里已有**的展示名（`context.name`）；别的槽取不到
  // ⇒ 整条回退（`{seat}` / `{card}` 这类窗口里没有唯一答案，不猜）。
  if (context.name) slots.name = String(context.name);
  return promptRenderTemplate(entry.ask, slots);
}

/** 渲染尾钩子：窗口问法**只填空**（渲染器已经写了步骤提示就以渲染器为准）。 */
function promptApplyWindowAsk(pending) {
  const hint = $("t-target-hint");
  if (!hint || String(hint.textContent || "").trim()) return false;
  const text = promptWindowAsk(pending);
  if (!text) return false;
  hint.textContent = text;
  return true;
}

/* ------------------------------------------------------------ 游戏内编辑（最小面） */

/** 当前可编辑的那一格：`{key, step, current}`；`null` = 现在没有可改的模板。 */
function promptEditTarget() {
  const pa = state.preactivation;
  if (!pa || pa.cardEntry || !pa.skillId) return null;
  if (!Number.isInteger(pa.cursor) || pa.cursor < 0 || pa.cursor >= pa.steps.length) return null;
  const key = String(pa.skillId);
  const entry = promptTemplateFor(key);
  const step = pa.steps[pa.cursor].name;
  const template = entry && entry.steps ? entry.steps[step] : null;
  if (!template || typeof template.ask !== "string") return null;
  return { key, step, current: template.ask };
}

/** 覆盖层的深一层副本（`{key: {steps: {step: {ask, done}}}}`）：写一格不动其余。 */
function promptCloneOverlay() {
  const out = {};
  for (const [key, entry] of Object.entries(promptOverlayTemplates)) {
    out[key] = { ...entry };
    if (entry.steps) {
      out[key].steps = {};
      for (const [step, template] of Object.entries(entry.steps)) {
        out[key].steps[step] = { ...template };
      }
    }
  }
  return out;
}

/** 重画当前询问（`pending.js` 的渲染口；缺件时只同步编辑器按钮——本文件不硬依赖它）。 */
function promptRepaint() {
  if (typeof renderPending === "function" && state.view) {
    renderPending(state.view);
    return;
  }
  promptSyncEditor();
}

/** 写覆盖层的一格（键级叠加）并立刻重画当前询问。返回是否真的写下去了。 */
function promptSaveOverride(key, step, field, text) {
  if (!key || !step) return false;
  const store = promptOverlayStorage();
  if (!store || typeof store.setItem !== "function") return false;
  const overlay = promptCloneOverlay();
  const entry = overlay[key] || (overlay[key] = {});
  const steps = entry.steps || (entry.steps = {});
  const template = steps[step] || (steps[step] = {});
  template[field === "done" ? "done" : "ask"] = String(text);
  try {
    store.setItem(PROMPT_OVERLAY_KEY, JSON.stringify(overlay));
  } catch (error) {
    return false;
  }
  promptOverlayTemplates = overlay;
  promptRepaint();
  return true;
}

/** 清空覆盖层（导出贴回仓里之后的复位口；探针 / 烟测也用它收尾）。 */
function promptClearOverlay() {
  const store = promptOverlayStorage();
  if (store && typeof store.removeItem === "function") {
    try {
      store.removeItem(PROMPT_OVERLAY_KEY);
    } catch (error) {
      /* 记不住不该让页面挂掉 */
    }
  }
  promptOverlayTemplates = {};
  promptRepaint();
  return true;
}

/** 「导出 JSON」的内容：当前覆盖层（贴回 `tools/sgs_web/static/prompts.json`，或留作个人覆盖）。 */
function promptExportOverlay() {
  return JSON.stringify(promptOverlayTemplates, null, 2);
}

/** 「导出」按钮的点击口：把 JSON 交给用户复制（浏览器没有剪贴板权限时的最小面）。 */
function promptExportCurrent() {
  const text = promptExportOverlay();
  if (typeof window.prompt === "function") {
    window.prompt("复制这段 JSON，贴回 tools/sgs_web/static/prompts.json", text);
  }
  return text;
}

/** 「改这段文案」：只改**当前步骤的 ask**（用户口径：只改当前询问 / 当前步骤）。 */
function promptEditCurrent() {
  const target = promptEditTarget();
  if (!target) return false;
  const typed = typeof window.prompt === "function"
    ? window.prompt(`改这段文案（${target.key} / ${target.step}）`, target.current)
    : null;
  if (typed === null || typed === undefined) return false;
  return promptSaveOverride(target.key, target.step, "ask", String(typed));
}

/** 编辑器的最小面：两个 ghost 按钮挂进提示条**已有**的动作行（不新开屏幕）。
 *
 * 幂等靠一个模块级 flag（不用 `$("btn-prompt-edit")` 判"已挂过"：最小 DOM 垫片的
 * `getElementById` 对未知 id 会造一个新节点，那会把"还没挂"误判成"已经挂过"）。
 * 静态父链缺失时（探针 / 垫片）挂到提示条本身上——页面永远走 `.prompt-actions` 那一支。
 */
let promptEditorWired = false;

function promptWireEditor() {
  if (promptEditorWired) return;
  const submit = $("btn-submit");
  const row = (submit && submit.parentNode) || $("t-promptbar");
  if (!row) return;
  promptEditorWired = true;
  const edit = document.createElement("button");
  edit.id = "btn-prompt-edit";
  edit.type = "button";
  edit.className = "ghost";
  edit.textContent = "改这段文案";
  edit.title = "只改当前这一步的问句（写进 localStorage['sgs.prompts']）";
  edit.hidden = true;
  edit.disabled = true;
  edit.addEventListener("click", () => {
    promptEditCurrent();
  });
  row.appendChild(edit);
  const exportButton = document.createElement("button");
  exportButton.id = "btn-prompt-export";
  exportButton.type = "button";
  exportButton.className = "ghost";
  exportButton.textContent = "导出文案 JSON";
  exportButton.title = "把 localStorage 里的覆盖层导出来（贴回仓里或留作个人覆盖）";
  exportButton.hidden = true;
  exportButton.addEventListener("click", () => {
    promptExportCurrent();
  });
  row.appendChild(exportButton);
}

/** 每次 `renderPending`（及 reset）后调：按"有没有可改的一格 / 有没有覆盖层"显隐按钮。 */
function promptSyncEditor() {
  const edit = $("btn-prompt-edit");
  if (edit) {
    const target = promptEditTarget();
    edit.hidden = target === null;
    edit.disabled = target === null;
  }
  const exportButton = $("btn-prompt-export");
  if (exportButton) exportButton.hidden = Object.keys(promptOverlayTemplates).length === 0;
}

/* 页面启动即挂编辑器按钮并读模板表；`promptReady` 是"文件层读完了"的公开口
 * （烟测 / 探针等它再断言）。与 `anim.js:animReady` 同姿态：失败不挡页面。 */
promptWireEditor();
const promptReady = promptLoadTemplates();
