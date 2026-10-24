/* SGS 前端 · 座位卡（U1-02）：左武将块 + 右判定 / 装备区。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 *
 * 形状（spec §2.2）：对手 210×150、自己 292×210；左武将块收窄 30%（右块 100 / 130）。
 * U2-02 / D6：对手 +8%（228×163）在 8 人环上会重叠，回退为外形不变、内容放大（见 style.css）。
 *   |势力-名称-身份| |判定槽 ×3|
 *   |----体力条----| |-武器-| |-防具-|
 *   |手牌数--座位号| |-攻马-| |-防马-|
 * 尺寸 / 配色在 style.css；本文件只造节点的**语义形状**（槽位、状态类、悬停全文）。
 *
 * 纪律：
 *  - 身份可见性 / 横置 / 酒 / 武将名 / 势力全部读服务端字段，前端不自己判可见性；
 *  - 可点的只有"可指定 / 已选 / 提供一张 B / 预发动"这几种**数据给了的**状态；
 *  - 场上牌（装备 / 判定 / 处理区 / 中央舞台）都带悬停全文，牌面文本不常显描述。
 */

"use strict";

/** 座位交互态的类全集：环上与底部面板共用一份（新增状态只改这里）。 */
const SEAT_STATE_CLASSES = [
  "opponent",
  "me",
  "dead",
  "turn",
  "targetable",
  "tsel",
  "req",
  "chained",
  "wine",
];

function applySeatState(box, flags) {
  for (const name of SEAT_STATE_CLASSES) box.classList.toggle(name, flags.includes(name));
}

/** 座位卡的可点绑定：点击与键盘（回车 / 空格）走**同一个** handler。
 *
 * 座位是 `<div>`（不是 `<button>`），故可点时补 `tabindex` + `role` + keydown，不可点时
 * 一起摘掉。这里**不**判断"现在能不能选"——能不能选由 handler 自己问
 * `selectionEnabled()`（D8：选择入口共用一条判据），键盘激活因此天然被同一判据拦下。
 */
function bindSeatActivation(box, handler) {
  if (!box) return;
  box.onclick = handler || null;
  box.onkeydown = handler
    ? (event) => {
      if (!event || (event.key !== "Enter" && event.key !== " " && event.key !== "Spacebar")) return;
      if (event.preventDefault) event.preventDefault();
      handler();
    }
    : null;
  if (handler) {
    box.setAttribute("tabindex", "0");
    box.setAttribute("role", "button");
  } else {
    box.removeAttribute("tabindex");
    box.removeAttribute("role");
  }
}

/** 环上 / 底部共用的武将 id：`view.heroes` 在服务端是 list，合成视图可能是对象。 */
function heroIdAt(view, seat) {
  const heroes = (view && view.heroes) || [];
  return heroes[seat] || "";
}

/** 武将块的名称：优先服务端 `hero_name`（U1-02）。
 *
 * 旧载荷 / 合成视图没有 `hero_name`（M6 票 19 前只对标准版武将翻得出名字）⇒ 按
 * **目录**把 id 翻成中文名（`core.js:heroDisplayName`）。混合池下"刘备 / 界刘备"
 * 因此在环上与自己的面板上都分得清，不会退回 `jie_liubei` 这种引擎 id。
 */
function heroNameOf(player, view) {
  if (player && player.hero_name) return player.hero_name;
  const assigned = heroIdAt(view, player ? player.seat : null);
  return assigned ? heroDisplayName(assigned) : "（无武将）";
}

/** 视角快照里的座位（详情标题 / 详情头 / 右键入口共用这一处查找）。 */
function playerAt(view, seat) {
  if (!view || !view.snapshot || seat === null) return null;
  return (view.snapshot.players || []).find(
    (item) => Number(item.seat) === Number(seat),
  ) || null;
}

/** 身份徽标：明置给**全称**（`GLOSSARY「身份」`的四值），暗置给 `?`；
 *  2 人无身份局整块不显示（票面「无身份局」）。 */
function identityBadgeView(player, identityMode) {
  if (player && player.identity) {
    const label = player.identity_label || identityLabel(player.identity);
    return {
      label,
      className: `identity-badge ${player.identity}`,
      title: `身份：${label}`,
    };
  }
  if (!identityMode) return { label: "", className: "identity-badge none", title: "" };
  return { label: "?", className: "identity-badge hidden", title: "身份：暗置" };
}

/** 体力条：分段方块 + `current/max`；上限 ≥6 只画 `x/x` 文本（避免超出卡面）。 */
function hpBarNode(player) {
  const hp = Math.max(0, Number(player && player.hp) || 0);
  const hpMax = Math.max(hp, Number(player && player.hp_max) || 0);
  const bar = document.createElement("div");
  bar.className = "hp-bar";
  if (hpMax >= 6) {
    bar.classList.add("hp-text");
    bar.setAttribute("title", "体力上限 ≥6：只显示 x/x，不画方块");
  } else {
    for (let index = 0; index < hpMax; index += 1) {
      const segment = document.createElement("span");
      segment.className = "hp-seg";
      if (index >= hp) segment.classList.add("empty");
      else if (hp <= 1) segment.classList.add("low");
      bar.appendChild(segment);
    }
  }
  const number = document.createElement("span");
  number.className = "hp-num";
  number.textContent = `${hp}/${hpMax}`;
  bar.appendChild(number);
  return bar;
}

/** 高亮语义文字：当前回合「行动中」、合法目标「可指定」、已选「✓ 已选」。
 *
 * 当前回合 + 合法目标**两者都写**（不是二选一）；已选是确定态，不再重复「可指定」。
 */
function seatTagItems(flags) {
  const items = [];
  if (flags.includes("turn")) items.push({ text: "行动中", className: "seat-flag flag-turn" });
  if (flags.includes("tsel")) items.push({ text: "✓ 已选", className: "seat-flag flag-selected" });
  else if (flags.includes("targetable")) items.push({ text: "可指定", className: "seat-flag flag-legal" });
  if (flags.includes("req")) items.push({ text: "必选", className: "seat-flag flag-req" });
  return items;
}

function fillSeatTags(node, flags) {
  node.innerHTML = "";
  for (const item of seatTagItems(flags)) {
    const span = document.createElement("span");
    span.className = item.className;
    span.textContent = item.text;
    node.appendChild(span);
  }
}

/** 判定区固定 3 槽（乐不思蜀 / 闪电 / 兵粮寸断；同名不能重复）；多余的不画。 */
function judgeSlotLayout(cards) {
  const list = cards || [];
  return [0, 1, 2].map((index) => ({ zone: "judge", slot: "judge", card: list[index] || null }));
}

/** 装备区固定四槽：武器 / 防具 / 攻马 / 防马。
 *
 * 实体牌的 `slot` 是引擎派生的事实，按它落槽；合成局面没有 `slot` 的牌按顺序补空槽
 * （不让一张没带槽位的牌从场上消失）。
 */
function equipSlotLayout(cards) {
  const slots = EQUIP_SLOTS.map((slot) => ({ zone: "equip", slot, card: null }));
  const loose = [];
  for (const card of cards || []) {
    const at = slots.findIndex((item) => item.slot === card.slot && !item.card);
    if (at >= 0) slots[at].card = card;
    else loose.push(card);
  }
  for (const card of loose) {
    const at = slots.findIndex((item) => !item.card);
    if (at < 0) break;
    slots[at].card = card;
  }
  return slots;
}

/** 一个判定 / 装备槽：空槽画暗色占位；有牌画牌名 + 悬停全文。
 *
 * ``interactive`` 只在**本人**的"提供一张 B / 预发动"面里为真（票 14 / W4-1）：
 * 槽本身可点，点它 = 点那张牌；其余座位 / 其余窗口保持静态。
 */
function miniSlotNode(options) {
  const { zone, slot, card, interactive, ctx } = options;
  const judge = zone === "judge";
  const node = document.createElement(interactive && card ? "button" : "div");
  if (node.tagName === "BUTTON") node.type = "button";
  node.className = `mini-slot ${judge ? "judge-slot" : "equip-slot"} ${card ? "filled" : "empty"}`;
  node.setAttribute("data-zone", zone);
  node.setAttribute("data-slot", slot);
  if (!card) {
    const key = document.createElement("span");
    key.className = "slot-k";
    key.textContent = judge ? "判定" : EQUIP_SLOT_LABELS[slot] || "空";
    node.appendChild(key);
    return node;
  }
  node.setAttribute("data-card-id", String(card.card_id));
  // 悬停全文（U1-02）：装备区 / 判定区的牌与手牌、处理区读**同一份**服务端描述。
  // 槽很窄，牌名可能被省略号截断；标题里带全名，悬停能确认是哪张牌。
  node.setAttribute("title", `${cardName(card)}\n${cardDescription(card)}`);
  const value = document.createElement("span");
  value.className = "slot-v";
  value.textContent = cardName(card);
  node.appendChild(value);
  if (interactive) {
    const face = cardFace(ctx.pendingNow, card);
    if (face.selected) node.classList.add("sel");
    if (face.picked) node.classList.add("picked");
    if (!face.selectable) node.classList.add("dis");
    node.onclick = (event) => {
      if (event && event.stopPropagation) event.stopPropagation();
      activateCard(ctx.pendingNow, card, face);
    };
  }
  return node;
}

/** **武将牌上的牌区**（M6 票 10 / 票 19；spec §区域）：一个区一行，画在**武将块**里。
 *
 * 形状判据（服务端已裁好可见性，前端不判）：
 * - 可跨座位：区记 `owner`（放置技能的持有者），行落在**宿主座位**的武将牌上；
 * - 可多人并存：同一座位可以为每个技能实例各有一行（服务端 `hero_zones` 是数组）；
 * - 这些牌**不在手牌区**，故手牌数（`player.hand_count`）不含它们——界面不另算。
 *
 * 布局：座位卡是固定尺寸（对手 210×150 / 自己 292×210），故一行**单行横向滚动**而不换行
 * ——【谦逊】一次可以扣下整手牌，换行会把盒子撑破（实机验收的发现）。
 * 空区不下发（服务端只给有牌的区），故这里不需要空占位。
 */
function heroZoneRows(player) {
  const rows = [];
  for (const zone of (player && player.hero_zones) || []) {
    const row = document.createElement("div");
    row.className = "hero-zone-row";
    row.setAttribute("data-zone", "hero");
    row.setAttribute("data-seat", String(player.seat));
    row.setAttribute("data-skill-id", zone.skill_id || "");
    const label = document.createElement("span");
    label.className = "zone-k";
    label.textContent = zone.skill_name || "武将牌上";
    label.setAttribute("title", `武将牌上（${zone.skill_name || zone.skill_id || "技能"}）`);
    row.appendChild(label);
    for (const card of zone.cards || []) {
      const chip = document.createElement("span");
      chip.className = "hero-zone-card";
      chip.setAttribute("data-card-id", String(card.card_id));
      // 悬停全文与手牌 / 装备 / 处理区**同一份**服务端描述（U1-04b）。
      chip.setAttribute("title", `${cardName(card)}\n${cardDescription(card)}`);
      chip.textContent = cardName(card);
      row.appendChild(chip);
    }
    rows.push(row);
  }
  return rows;
}

/** 把武将牌上的牌区铺进一个容器；没有区就整块隐藏（不占布局）。 */
function fillHeroZones(box, player) {
  if (!box) return;
  box.innerHTML = "";
  const rows = heroZoneRows(player);
  box.hidden = rows.length === 0;
  for (const row of rows) box.appendChild(row);
}

/** 右侧判定 / 装备区（两个容器，`data-zone` 落在行上——与旧读取口一致）。
 *  **武将牌上的牌区**不在这里：它画在**武将块**里（见 :func:`heroZoneRows`），
 *  因为座位卡的右侧块装不下【谦逊】整手牌那样的行。 */
function sideBlockRows(player, ctx) {
  const seat = ctx.snapshot.seat;
  const interactive = Number(player.seat) === Number(seat)
    && Boolean(isProvide(ctx.pendingNow) || state.preactivation);
  const judge = document.createElement("div");
  judge.className = "judge-row";
  judge.setAttribute("data-zone", "judge");
  judge.setAttribute("data-seat", String(player.seat));
  for (const layout of judgeSlotLayout(player.judge)) {
    judge.appendChild(miniSlotNode({ ...layout, card: layout.card, interactive, ctx }));
  }
  const equip = document.createElement("div");
  equip.className = "equip-grid";
  equip.setAttribute("data-zone", "equip");
  equip.setAttribute("data-seat", String(player.seat));
  for (const layout of equipSlotLayout(player.equip)) {
    equip.appendChild(miniSlotNode({ ...layout, card: layout.card, interactive, ctx }));
  }
  return [judge, equip];
}

function sideBlockNode(player, ctx) {
  const block = document.createElement("div");
  block.className = "side-block";
  for (const row of sideBlockRows(player, ctx)) block.appendChild(row);
  return block;
}

/** 左武将块：势力 / 名称 / 身份 / 体力 / 手牌数 / 座位号 + 横置 / 酒 / 阵亡叠加。
 *
 * ``flags`` 是**这一轮**的完整状态类（含 seatInteraction 给的 targetable / tsel）；
 * 高亮文字必须与座位节点的类同源，不能各自再算一遍。
 */
function heroBlockNode(player, view, ctx, flags) {
  const block = document.createElement("div");
  block.className = "hero-block";
  const head = document.createElement("div");
  head.className = "seat-head";
  if (player.kingdom_label) {
    const kingdom = document.createElement("span");
    kingdom.className = `kingdom ${player.kingdom || ""}`;
    kingdom.textContent = player.kingdom_label;
    kingdom.setAttribute("title", `势力：${player.kingdom_label}`);
    head.appendChild(kingdom);
  }
  const name = document.createElement("span");
  name.className = "hero-name";
  name.textContent = heroNameOf(player, view);
  head.appendChild(name);
  const badge = identityBadgeView(player, ctx.identityMode);
  if (badge.label) {
    const identity = document.createElement("span");
    identity.className = badge.className;
    identity.textContent = badge.label;
    identity.setAttribute("title", badge.title);
    head.appendChild(identity);
  }
  block.appendChild(head);
  block.appendChild(hpBarNode(player));
  const tags = document.createElement("div");
  tags.className = "seat-tags";
  fillSeatTags(tags, flags);
  block.appendChild(tags);
  // M6 票 19：武将牌上的牌区（一个放置区一行；没有区时整块隐藏）。
  const zones = document.createElement("div");
  zones.className = "hero-zones";
  fillHeroZones(zones, player);
  block.appendChild(zones);
  const foot = document.createElement("div");
  foot.className = "seat-foot";
  const hand = document.createElement("span");
  hand.innerHTML = `手牌 <b>${Number(player.hand_count) || 0}</b>`;
  const seat = document.createElement("span");
  seat.innerHTML = `座位 <b>${player.seat}</b>`;
  foot.appendChild(hand);
  foot.appendChild(seat);
  block.appendChild(foot);
  // 横置 = 武将块中央从左到右一条虚线；酒 = 席位泛红（只上类，无「酒」字徽章）；
  // 阵亡 = 蒙灰 + 大号「阵亡」覆盖中心。三个状态可以同时存在、互不遮挡（CSS 分层）。
  if (player.chained) block.appendChild(chainLineNode());
  if (!player.alive) block.appendChild(deadTagNode());
  // U1-06：右键**武将块** = 武将详情；装备 / 判定 / 手牌不响应右键（它们只悬停出描述）。
  // 接线与自己的静态块共用 `bindHeroDetailContext`（止冒泡：外壳只负责屏蔽浏览器菜单）。
  bindHeroDetailContext(block, player.seat);
  return block;
}

function chainLineNode() {
  const line = document.createElement("div");
  line.className = "chain-line";
  line.setAttribute("title", "横置（连环状态）");
  return line;
}

function deadTagNode() {
  const tag = document.createElement("div");
  tag.className = "dead-tag";
  const text = document.createElement("span");
  text.className = "dead-text";
  text.textContent = "阵亡";
  tag.appendChild(text);
  return tag;
}

/** 一个对手座位卡：环桌几何（U1-01 的 `data-ring-*`）照旧落在节点上。 */
function seatCardNode(player, position, view, ctx) {
  const box = document.createElement("div");
  box.className = "seat opponent";
  const { classes, handler } = seatInteraction(player, ctx);
  const flags = ctx.flagsFor(player, classes);
  applySeatState(box, flags);
  box.setAttribute("data-seat", String(player.seat));
  box.setAttribute("data-ring-angle", position.angle.toFixed(2));
  box.setAttribute("data-ring-x", position.x.toFixed(2));
  box.setAttribute("data-ring-y", position.y.toFixed(2));
  box.style.left = `${(position.x / RING_STAGE.width) * 100}%`;
  box.style.top = `${(position.y / RING_STAGE.height) * 100}%`;
  box.appendChild(heroBlockNode(player, view, ctx, flags));
  box.appendChild(sideBlockNode(player, ctx));
  // 其他座位的询问（U1-03）：紧贴武将信息下方显示文本，不给按钮 / 倒计时。
  if (ctx.pendingNow && !ctx.ownPending && Number(ctx.pendingNow.actor) === Number(player.seat)) {
    const line = document.createElement("div");
    line.className = "seat-prompt";
    line.setAttribute("data-seat", String(player.seat));
    line.textContent = "正在响应…";
    box.appendChild(line);
  }
  // 点击 / 键盘（Tab + 回车 / 空格）走同一个 handler（`bindSeatActivation`）。
  bindSeatActivation(box, handler);
  return box;
}
