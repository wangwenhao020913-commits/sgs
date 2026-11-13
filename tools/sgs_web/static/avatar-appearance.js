/* SGS 前端 · 技能外观组件（shan-baozuoci 票 05）
 *
 * 「技能外观组件」= 有些技能会记录 / 缓存一些数据（最常见的是花色），为了让这些数据直观，
 * 这些技能有**自己的显示组件**（用户 2026-11-13 逐字）。本轮只落**化身**一个 kind：
 *
 *   第一行 = 手里每一张化身牌（太多时**横向滑块**）；
 *   第二行 = 该张化身牌的技能 + 详细介绍（文字太多时**纵向滑块**，形态同【天成】的武将牌面）。
 *
 * **同一个组件、两种模式**（用户 2026-11-13 的设计 + Q1 / Q2 的签字）：
 *
 *   - **只读**（Q1）：技能按钮区多一个「只查看」按钮，**左键 / 中键**都开一个只读检视窗
 *     （`#t-avatar-modal`，自己的遮罩 + 关闭 + ESC，与弃牌堆面板同一姿态）。点第一行的化身牌
 *     只切换第二行；点第二行的技能**没有任何效果**——那两行根本没有接点击。
 *   - **发动**（Q2）：化身①/② 的选择询问复用它，画进询问弹窗的正文槽（`#t-options`，
 *     前端按载荷里有没有 `appearance` 选面）。这时点**第一行的化身牌仍然只切换**，
 *     点**第二行的技能 = 直接提交**那条 `SelectOption`；不合格行（限定 / 觉醒 / 主公，
 *     或本次不在候选里的）列出来但置灰、点不动（Q4）。
 *
 * 纪律（与其他渲染器同姿态）：
 *
 *  - **数据只读载荷**：卡片 / 技能名 / 全文 / `eligible` / `current` / `selectable` 全部来自
 *    服务端（`sgs_web.projection` 的 `APPEARANCE_BUILDERS`），本文件不猜任何一条规则；
 *  - **不新增选择方式**：发动面提交的是询问自己 `legal_actions` 里的那条候选
 *    （`submitDialogChoice`），与【天成】的选项行同一条路；
 *  - **窗口大小钉死**（用户逐字："窗口大小钉死，避免切换化身时频繁改变窗口大小，容易误触"）：
 *    第一行的高度 / 第二行的可视高度都是 CSS 常量，切换化身牌只换第二行的内容，
 *    容器高度一个字都不变；
 *  - **本地态只有一个**：这一屏高亮哪张化身牌（`avatarFocusHero`）。重绘 / 轮询不丢它；
 *    换一局或载荷里没有这张牌时退回 `current` / 第一张。
 */
"use strict";

/** 外观组件的 kind 闭集（与 `sgs.skills.AppearanceKind` 同值；本轮只有化身一档）。 */
const APPEARANCE_KINDS = Object.freeze({ avatar: "avatar" });

/** 只读入口按钮的文案（Q1：技能按钮区多一个「只查看」按钮）。 */
const AVATAR_LOOK_LABEL = "只查看";

/** 发动面的取消文案（Q2：② 可放弃 ⇒ 右端「取消（不重新进行）」；① 强制段没有取消）。 */
const AVATAR_CANCEL_LABEL = "取消（不重新进行）";

/** 当前这一屏高亮的化身牌（`hero_id`）；`null` = 还没定（每次渲染现推导）。 */
let avatarFocusHero = null;

/** 只读检视窗打开着的那个技能 id；`null` = 关着。 */
let avatarModalSkill = null;

/** 载荷里的外观组件 kind；不是外观组件的数据面 ⇒ `null`（**唯一**判据）。 */
function appearanceKindOf(appearance) {
  if (!appearance || typeof appearance !== "object") return null;
  const kind = appearance.kind;
  return Object.prototype.hasOwnProperty.call(APPEARANCE_KINDS, kind) ? kind : null;
}

/** 这条询问是不是化身的外观组件面（载荷里有 `kind: "avatar"` 的 `appearance`）。 */
function isAvatarAppearanceStep(pending) {
  if (!pending || pending.kind !== "SELECT_OPTION") return false;
  return appearanceKindOf(pending.appearance) === APPEARANCE_KINDS.avatar;
}

/** 外观组件弹窗的标题：技能展示名（服务端 `context.name` = "化身"），缺省给中性文案。 */
function appearanceTitleFor(pending) {
  const contextName = pending && pending.context && pending.context.name;
  return contextName || "化身";
}

/** 发动面右端的取消文案（`pending.js:alternativeLabel` 读它）；不是本组件 ⇒ `null`。 */
function appearancePassLabel(pending) {
  return isAvatarAppearanceStep(pending) ? AVATAR_CANCEL_LABEL : null;
}

/** 这一行技能**能不能点**：只有发动面才有可点行，且必须同时满足
 *
 *  - 载荷说它合格（`eligible`：无标签 / 锁定技）——限定 / 觉醒 / 主公为 `false`（Q4）；
 *  - 本次询问真的给了它一条候选（`selectable`：服务端算好的"合格 ∩ 本次候选"）。
 *
 * 缺 `selectable` 的载荷（只读面 / 旧载荷）**一行都不可点**（fail-closed）。
 */
function avatarRowSelectable(appearance, row) {
  if (!row || row.eligible !== true) return false;
  const selectable = appearance && appearance.selectable;
  if (!Array.isArray(selectable)) return false;
  return selectable.includes(row.skill_id);
}

/** 这一屏该高亮哪张化身牌：记住的那张仍在 ⇒ 用它；否则当前化身 / 第一张。 */
function avatarFocusCard(appearance) {
  const cards = (appearance && appearance.cards) || [];
  if (!cards.length) return null;
  const remembered = cards.find((card) => card.hero_id === avatarFocusHero);
  if (remembered) return remembered;
  const current = appearance.current && appearance.current.hero_id;
  const focused = cards.find((card) => card.hero_id === current) || cards[0];
  avatarFocusHero = focused.hero_id;
  return focused;
}

/** 这一屏的**落点**：只读检视窗开着 ⇒ `#t-avatar-body`；否则是发动面 ⇒ `#t-options`
 *（`modal.js:applyDialogContentMode` 再把它搬进询问弹窗的正文槽）。
 *
 * 由**当前视图**现推导，不缓存"上一次渲染到哪"：换局 / 换问之后那份缓存就是上一屏的残值，
 * 重画的会是已经不存在的面。返回 `null` = 这一屏既没有只读窗、也不是外观组件的询问。
 */
function avatarRenderTarget() {
  if (avatarModalSkill !== null) {
    const found = avatarAppearanceOfSkill(avatarModalSkill);
    const body = $("t-avatar-body");
    if (!found || !body) return null;
    return { appearance: found.appearance, container: body, interactive: false, pending: null };
  }
  const pending = state.view ? state.view.pending : null;
  const box = $("t-options");
  if (!isAvatarAppearanceStep(pending) || !box) return null;
  return { appearance: pending.appearance, container: box, interactive: true, pending };
}

/** 点第一行的化身牌：**只切换第二行**，绝不提交（Q2 用户逐字："选择第一行化身武将牌后，
 * 不要自动提交，等到选中一个技能后才直接提交"）。
 *
 * 就地重画当前那一屏（只读检视窗或发动面，见 :func:`avatarRenderTarget`）。容器高度由
 * CSS 钉死 ⇒ 切换时窗口不变形。
 */
function toggleAvatarCard(heroId) {
  avatarFocusHero = heroId === undefined || heroId === null ? null : String(heroId);
  const target = avatarRenderTarget();
  if (!target) return;
  renderAvatarAppearance(target.appearance, target);
}

/** 一行技能的节点：可点行是 `button`（点了就提交），其余一律是 `div`——**连监听都不接**，
 *  "只读模式点技能无任何效果"因此是结构事实，不是"点了判断一下再忽略"。
 */
function avatarSkillRowNode(pending, appearance, row) {
  // `selectable` 在 = 这一屏是**发动面**（Q2 / Q4）；不在 = 只读面：技能行只是说明，
  // 一律不接点击、也不写「不可选」（那一档只有"这一次能不能点"才有意义）。
  const asking = Array.isArray(appearance && appearance.selectable);
  const clickable = Boolean(pending) && asking && avatarRowSelectable(appearance, row);
  // 置灰的两种来由（Q4）：这一屏**用不了它**（不合格：限定 / 觉醒 / 主公），或这一屏是
  // 发动面而它**不在本次候选里**。只读面只有前者——"能看但本次不能点"在那儿不成立。
  const blocked = asking ? !clickable : !(row && row.eligible === true);
  const node = document.createElement(clickable ? "button" : "div");
  if (clickable) node.type = "button";
  node.className = blocked ? "avatar-skill dis" : "avatar-skill";
  node.setAttribute("data-skill-id", String((row && row.skill_id) || ""));
  if (row && row.selected) node.classList.add("sel");
  const name = document.createElement("span");
  name.className = "avatar-skill-name";
  name.textContent = (row && row.name) || "";
  node.appendChild(name);
  if (row && row.selected) {
    const current = document.createElement("span");
    current.className = "avatar-skill-flag";
    current.textContent = "当前";
    node.appendChild(current);
  } else if (asking && blocked) {
    // Q4：列出来但置灰、点不动——玩家看得出这张牌为什么不能用（与【天成】的
    // `disabled` 同一姿态）。只读面不写这一档（那里一行都不可点，写了等于全灰）。
    const badge = document.createElement("span");
    badge.className = "avatar-skill-flag";
    badge.textContent = "不可选";
    node.appendChild(badge);
  }
  if (row && row.text) {
    // `descriptions.json` 的原文：**不截断**（纵向滑块看全文，与【天成】的武将牌面同形）。
    const text = document.createElement("div");
    text.className = "avatar-skill-text";
    text.textContent = row.text;
    node.appendChild(text);
  }
  if (clickable) {
    node.addEventListener("click", () => submitAvatarSkill(pending, row.skill_id));
  }
  return node;
}

/** 选中一行技能 = **提交**那条 `SelectOption`（Q2：点技能直接提交）。
 *
 *  找不到候选就什么都不做（fail-closed）：不手拼 Action、不猜下标。
 */
async function submitAvatarSkill(pending, skillId) {
  if (!pending || state.playback || state.submitBusy) return;
  // 候选下标只查一处（`modal.js:dialogChoiceIndex`，与【天成】的选项行同一个口）：
  // 找不到 = 引擎没发这条候选 ⇒ 什么都不做（不手拼 Action、不猜下标）。
  const index = dialogChoiceIndex(pending, skillId);
  if (index < 0) return;
  await submitDialogChoice(pending, index);
}

/** **组件的唯一渲染器**：第一行（化身牌，横向滑块）+ 第二行（该牌的技能，纵向滑块）。
 *
 *  `interactive` 只由调用方声明：询问面传 `pending`，只读面传 `null`。渲染器自己不判
 *  "现在能不能交"——那由 `legal_actions` 决定（`avatarRowSelectable` 已经把它收成一行）。
 */
function renderAvatarAppearance(appearance, options = {}) {
  const container = options.container || $("t-avatar-body");
  if (!container) return null;
  const pending = options.interactive ? options.pending || null : null;
  container.innerHTML = "";
  const view = document.createElement("div");
  view.className = "avatar-view";
  view.setAttribute("data-appearance", String((appearance && appearance.kind) || ""));
  const cards = (appearance && appearance.cards) || [];
  if (!cards.length) {
    // Q3：手里没有化身牌 ⇒ 不开弹窗（服务端压根不下发这个载荷）。构造载荷走到这里时
    // 画一句明话，不留一个空窗。
    const empty = document.createElement("p");
    empty.className = "dim avatar-empty";
    empty.textContent = "没有化身牌。";
    view.appendChild(empty);
    container.appendChild(view);
    return view;
  }
  const focused = avatarFocusCard(appearance);
  const current = appearance.current || null;
  // ---- 第一行：化身牌（横向滑块） ----
  const row = document.createElement("div");
  row.className = "avatar-cards";
  for (const card of cards) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "avatar-card";
    if (focused && card.hero_id === focused.hero_id) button.classList.add("sel");
    if (current && card.hero_id === current.hero_id) {
      button.classList.add("current");
      button.setAttribute("title", "当前化身");
    }
    button.setAttribute("data-hero-id", String(card.hero_id));
    const name = document.createElement("span");
    name.className = "avatar-card-name";
    name.textContent = card.name || card.hero_id;
    button.appendChild(name);
    button.addEventListener("click", () => toggleAvatarCard(card.hero_id));
    row.appendChild(button);
  }
  view.appendChild(row);
  // ---- 第二行：该牌的技能 + 详细介绍（纵向滑块） ----
  const skills = document.createElement("div");
  skills.className = "avatar-skills";
  const rows = (focused && focused.skills) || [];
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "dim avatar-empty";
    empty.textContent = "这张化身牌没有列出的技能。";
    skills.appendChild(empty);
  }
  for (const entry of rows) {
    skills.appendChild(avatarSkillRowNode(pending, appearance, entry));
  }
  view.appendChild(skills);
  container.appendChild(view);
  return view;
}

/* ------------------------------------------------------------ 只读检视窗（Q1） */

/** 只读窗是否开着（真源 = `avatarModalSkill`，不是 DOM 的 hidden 属性）。 */
function avatarModalIsOpen() {
  return avatarModalSkill !== null;
}

/** 从当前视图取某个技能的 `appearance`（`build_skills` 的产物）；取不到 ⇒ `null`。
 *
 *  数据源与表体**同一份视图**（`tableViewForRender()`）：回放期间是逐事件派生的 live view。
 *  换局 / 技能没了 / 化身库空了 ⇒ 这一屏没有可显示的数据，调用方据此收窗（fail-closed）。
 */
function avatarAppearanceOfSkill(skillId) {
  const view = typeof tableViewForRender === "function" ? tableViewForRender() : state.view;
  const skills = (view && view.skills) || [];
  const skill = skills.find((item) => item && item.skill_id === skillId);
  const appearance = skill && skill.appearance;
  if (appearanceKindOf(appearance) !== APPEARANCE_KINDS.avatar) return null;
  if (!appearance.cards || !appearance.cards.length) return null;
  return { skill, appearance };
}

/** 只读窗的显隐同步（唯一写 `hidden` 的地方）：窗口 / 自己的遮罩一起。 */
function syncAvatarModalChrome() {
  const modal = $("t-avatar-modal");
  const backdrop = $("t-avatar-backdrop");
  const open = avatarModalSkill !== null;
  if (modal) modal.hidden = !open;
  if (backdrop) backdrop.hidden = !open;
}

/** 打开只读窗（左键 / 中键「只查看」，或中键点技能按钮）：先画一帧再显示。
 *
 *  不改任何选择态、不发请求、不进只读判据（`selectionEnabled` 只看询问的表面）。
 */
function openAvatarAppearance(skillId) {
  const found = avatarAppearanceOfSkill(String(skillId || ""));
  if (!found) return;
  avatarModalSkill = String(skillId);
  avatarFocusHero = null;  // 新开一窗：从当前化身 / 第一张看起
  const title = $("t-avatar-title");
  if (title) title.textContent = (found.skill && found.skill.name) || "化身";
  const body = $("t-avatar-body");
  if (body) renderAvatarAppearance(found.appearance, { container: body });
  syncAvatarModalChrome();
}

/** 关闭只读窗：只收起窗口与遮罩（不改选择态、不提交）。 */
function closeAvatarAppearance() {
  if (avatarModalSkill === null) return;
  avatarModalSkill = null;
  const body = $("t-avatar-body");
  if (body) body.innerHTML = "";
  syncAvatarModalChrome();
}

/** 每次 `renderTable` 之后重画开着的那一扇窗（关着时 no-op，与 `renderDiscardPanel` 同姿态）。
 *
 *  数据没了（技能失去 / 换局 / 化身库空）⇒ 收窗：留着一个画不出来的白框比关掉更糟。
 */
function syncAvatarAppearance() {
  if (avatarModalSkill === null) return;
  const found = avatarAppearanceOfSkill(avatarModalSkill);
  if (!found) {
    closeAvatarAppearance();
    return;
  }
  const body = $("t-avatar-body");
  if (body) renderAvatarAppearance(found.appearance, { container: body });
  syncAvatarModalChrome();
}
