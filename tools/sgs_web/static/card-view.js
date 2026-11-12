/* SGS 前端 · 卡面绘制（U1-02）：花色 / 点数 / 牌名 / 类型行。
 *
 * 加载顺序见 index.html（U1-00 拆分）；本文件不需要构建步骤。
 *
 * 纪律：
 *  - 牌名只走 :func:`cardName`（服务端展示契约优先），本文件**不映射**牌名；
 *  - 属性 / 种类 / 攻击范围 / 装备槽都是**服务端已下发的事实**，这里只翻成人话；
 *  - 描述全文只走 :func:`cardDescription`（U1-04b 内容文件），本文件不抄文案。
 */

"use strict";

/** 花色枚举值 → 符号（引擎值是 spade / heart / club / diamond）。 */
const SUIT_SYMBOLS = { spade: "♠", heart: "♥", club: "♣", diamond: "♦" };

/** 点数 1 / 11 / 12 / 13 → A / J / Q / K；其余原样。 */
const RANK_LABELS = { 1: "A", 11: "J", 12: "Q", 13: "K" };

/** 装备槽枚举值 → 中文槽名（顺序 = 装备区四槽）。 */
const EQUIP_SLOTS = ["weapon", "armor", "horse_offense", "horse_defense"];
const EQUIP_SLOT_LABELS = {
  weapon: "武器",
  armor: "防具",
  horse_offense: "攻马",
  horse_defense: "防马",
};

/** 展示种类（服务端 `kind` 的稳定报文值）→ 右上角标（U1-07；U3-01 起**不再有底行文字**）。
 *
 * 普通牌没有角标；虚拟 / 转化牌的角标只在中央舞台 / 处理区出现。手牌拿到的是源实体牌，
 * **例外**是‘只能’型转化的源牌（`transform_products[*].forced`，如武神）：手牌画产物、
 * 身份仍是源牌 id（见 `table.js:renderTable`）。
 * 底行只写**有用的事实**（槽名 / 属性 / 范围；舞台牌另写来源），「普通 / 虚拟 / 转化」
 * 已由右上角标表达，不再重复（U3-01 / D10）。
 */
const CARD_KIND_PRESENTATION = {
  normal: { corner: "" },
  virtual: { corner: "虚" },
  transformed: { corner: "转" },
};

/** 一张牌的展示种类：读服务端 `kind`；旧载荷 / 合成载荷退回 `virtual_sources`
 *  （`04` §5 的读取方纪律：实体牌恒空、转化牌恒非空），再缺就是普通牌。不读规则，
 *  也不拿负 id 当第三份判据。 */
function cardKind(card) {
  if (!card) return "normal";
  if (card.kind) return card.kind;
  if (card.virtual_sources && card.virtual_sources.length) return "transformed";
  return "normal";
}

function suitSymbol(suit) {
  return SUIT_SYMBOLS[suit] || "";
}

function rankLabel(rank) {
  if (rank === null || rank === undefined) return "";
  return RANK_LABELS[rank] || String(rank);
}

/** 花色颜色类：只读引擎派生的 `card.color`（`CardRef` 的 docstring：颜色由花色派生一次，
 *  这里**不抄**花色 → 颜色的映射）；缺失时无色。
 */
function suitColorClass(card) {
  return card && (card.color === "red" || card.color === "black") ? card.color : "";
}

/** 转化牌左上角的颜色标记：红 / 黑 / 无（U1-07）。颜色只读 `card.color`。 */
function colorMarkClass(card) {
  const color = suitColorClass(card);
  return color ? `color-mark ${color}` : "color-mark none";
}

/** 来源牌的一句话：`♥10【杀】`；没有花色 / 点数（多源转化）时只写牌名。 */
function sourceCardLabel(card) {
  if (!card) return "";
  const suit = card.suit ? suitSymbol(card.suit) : "";
  const rank = card.rank === null || card.rank === undefined ? "" : rankLabel(card.rank);
  return `${suit}${rank}【${cardName(card)}】`;
}

/** 卡面悬停全文（U1-07）：转化牌列出**全部来源牌**，纯虚拟牌标明「虚拟牌」。
 *
 * 普通牌保持 U1-04 的读法（只显示服务端 description）；描述本身仍只来自服务端，
 * 这里只拼"这张牌是什么 / 由哪些牌变来"的事实。
 */
function cardHoverText(card) {
  if (!card) return DESCRIPTION_FALLBACK;
  const description = cardDescription(card);
  const kind = cardKind(card);
  if (kind === "virtual") return `虚拟牌\n${description}`;
  if (kind === "transformed") {
    const sources = (card.source_cards || []).map(sourceCardLabel).filter(Boolean);
    const sourceLine = sources.length ? `源牌：${sources.join("、")}` : "来源牌：未知";
    return `转化牌：${sourceCardLabel(card)}\n${sourceLine}\n${description}`;
  }
  return description;
}

/** 卡面底部那行：装备槽 / 属性 / 攻击范围（只拼服务端已给的事实）。
 *
 * 类型（普通 / 虚拟 / 转化）**不再写**：右上角「虚 / 转」已经表达（U3-01 / D10）。
 * 于是普通牌底行为空行；装备牌写槽名；属性只在**不是普通**时出现（火杀 / 雷杀才标红蓝）；
 * 攻击范围只属武器。舞台牌由调用方传 `opts.bottomLine` 换成「座位·武将·区域」。
 */
function cardTypeLine(card) {
  if (!card) return "";
  const parts = [];
  if (card.slot && EQUIP_SLOT_LABELS[card.slot]) {
    parts.push(EQUIP_SLOT_LABELS[card.slot]);
  }
  if (card.attribute && card.attribute !== "普通") parts.push(card.attribute);
  if (card.attack_range !== null && card.attack_range !== undefined) {
    parts.push(`范围 ${card.attack_range}`);
  }
  return parts.join(" · ");
}

/** 卡面中心最多画几个标签（2 列 × 2 行）；更多时末位收成 ``+N``。 */
const CARD_TAG_LIMIT = 4;

/** 卡面中心的**来源技能标签**（服务端 ``card.tags``：``{skill_id, label}``）。
 *
 * 标签只由服务端下发；这里不按技能名 / 牌名做任何分支。2 列网格由 CSS 负责，
 * 超出两行收成末位 ``+N``，悬停仍给出完整标签名。
 */
function cardTagsNode(card) {
  const tags = Array.isArray(card && card.tags) ? card.tags : [];
  if (!tags.length) return null;
  const box = document.createElement("div");
  box.className = "card-tags";
  box.setAttribute("data-tag-count", String(tags.length));
  const shown = tags.length > CARD_TAG_LIMIT ? tags.slice(0, CARD_TAG_LIMIT - 1) : tags;
  for (const tag of shown) {
    const chip = document.createElement("span");
    chip.className = "card-tag";
    chip.textContent = tag.label || tag.skill_id || "";
    chip.setAttribute("data-skill-id", tag.skill_id || "");
    chip.setAttribute("title", tag.label || tag.skill_id || "");
    box.appendChild(chip);
  }
  const hidden = tags.length - shown.length;
  if (hidden > 0) {
    const more = document.createElement("span");
    more.className = "card-tag card-tag-more";
    more.textContent = `+${hidden}`;
    more.setAttribute("title", tags.slice(shown.length).map((tag) => tag.label || tag.skill_id).join("、"));
    box.appendChild(more);
  }
  return box;
}

/** 一张**完整卡面**（88×124）的节点；手牌 / 处理区 / 中央舞台共用。
 *
 * ``opts.selected`` 选中态（`.sel`，焦点牌）、``opts.picked`` 已选态（`.picked`，弃牌 /
 * 提供 B / 预发动选中的牌）、``opts.usable === false`` 置灰（`.dis`）、``opts.static``
 * 不可点（公开信息）。点击回调由调用方给；静态牌也有悬停全文。
 * ``opts.bottomLine`` 换掉卡面底行（舞台牌写来源）、``opts.zoneLabel`` 同时给底行挂
 * `data-zone-label`（U3-01 / D10；手牌 / 候选 / 弹窗不传，仍是槽名 / 属性 / 范围）。
 *
 * **虚拟 / 转化牌（U1-07）**：只在中央舞台 / 处理区出现，右上角标「虚 / 转」；
 * 转化牌左上角另有颜色标记（红 / 黑 / 无），单源同时继承花色 / 点数（引擎给的
 * `suit` / `rank`），多源没有这两项。手牌区画的是源实体牌（普通牌），唯一例外是
 * ‘只能’型转化的源牌（`forced`）：调用方把产物牌面传进来、`card_id` 覆盖回源牌。
 */
function cardFaceNode(card, opts = {}) {
  const kind = cardKind(card);
  const node = document.createElement("button");
  node.type = "button";
  node.className = `card face kind-${kind}`;
  if (opts.selected) node.classList.add("sel");
  if (opts.picked) node.classList.add("picked");
  if (opts.static) node.classList.add("static");
  if (!opts.static && opts.usable === false) node.classList.add("dis");
  if (card.attribute === "火焰") node.classList.add("attr-fire");
  if (card.attribute === "雷电") node.classList.add("attr-thunder");
  node.setAttribute("data-card-id", String(card.card_id));
  node.setAttribute("data-kind", kind);

  const presentation = CARD_KIND_PRESENTATION[kind];
  const corner = presentation ? presentation.corner : "";
  if (corner) {
    const badge = document.createElement("span");
    badge.className = "corner";
    badge.textContent = corner;
    badge.setAttribute("title", kind === "virtual" ? "虚拟牌" : "转化牌");
    node.appendChild(badge);
  }
  if (kind === "transformed") {
    node.classList.add("has-mark");
    const mark = document.createElement("span");
    mark.className = colorMarkClass(card);
    node.appendChild(mark);
  }
  // 单源转化继承花色 / 点数；多源两者皆空，故这里天然不画。虚拟牌也两者皆空。
  if (card.suit) {
    const suit = document.createElement("span");
    suit.className = `suit ${suitColorClass(card)}`;
    suit.textContent = suitSymbol(card.suit);
    node.appendChild(suit);
  }
  if (card.rank !== null && card.rank !== undefined) {
    const rank = document.createElement("span");
    rank.className = "rank";
    rank.textContent = rankLabel(card.rank);
    node.appendChild(rank);
  }
  const name = document.createElement("span");
  name.className = "cname";
  name.textContent = cardName(card);
  // 底行：默认 = 槽名 / 属性 / 范围；舞台牌由调用方换成「座位·武将·区域」并带上
  // `data-zone-label`（U3-01 / D10 的读取口）。
  const type = document.createElement("span");
  type.className = "ctype";
  type.textContent = opts.bottomLine !== undefined ? opts.bottomLine : cardTypeLine(card);
  if (opts.zoneLabel) type.setAttribute("data-zone-label", opts.zoneLabel);
  node.appendChild(name);
  const tags = cardTagsNode(card);
  if (tags) node.appendChild(tags);
  node.appendChild(type);
  // 悬停全文（U1-04b / U1-07）：只读服务端下发的 description 与来源牌；前端不存文案。
  // ``opts.note``（票 12）：一行**这一步为什么点不动**的说明（源牌级置灰），与牌面全文
  // 分开拼、不覆盖它——两条信息都要在悬停里（用户要"灰得看得见理由"）。
  node.setAttribute(
    "title", [cardHoverText(card), opts.note].filter(Boolean).join("\n"),
  );
  if (!opts.static) {
    node.addEventListener("click", (event) => {
      if (event && event.stopPropagation) event.stopPropagation();
      if (opts.onClick) opts.onClick();
    });
  }
  return node;
}

/* ------------------------------------------------ 跟随鼠标的卡牌预览（用户 2026-11-07） */

/** 预览层相对指针的偏移（px）：右下各 16，不让小弹窗压住指针本身。 */
const CARD_PREVIEW_OFFSET = 16;

/** 一次预览最多画几张（武将牌上的牌区可能很多；超出的收成一行说明，不铺满整屏）。 */
const CARD_PREVIEW_LIMIT = 10;

/** 画一张牌的**常规卡面**（`cardFaceNode`，标签 / 转化 / 虚拟都照常）或者一行说明。
 *
 * 层是 `#t-card-preview`（index.html 里的覆盖层，`pointer-events: none`）：本模块只管
 * 内容与可见性，**跟随鼠标**由 :func:`moveCardPreview` 在 `mousemove` 上更新 `left/top`。
 * 调用方（`seat-view.js` 的装备 / 判定槽与武将牌上的牌区）只传牌面 + 指针事件。
 */
function showCardPreview(cards, event) {
  const box = $("t-card-preview");
  if (!box) return;
  const list = (Array.isArray(cards) ? cards : []).filter(Boolean);
  box.innerHTML = "";
  for (const card of list.slice(0, CARD_PREVIEW_LIMIT)) {
    box.appendChild(cardFaceNode(card, { static: true }));
  }
  const hidden = list.length - CARD_PREVIEW_LIMIT;
  if (hidden > 0) {
    const more = document.createElement("div");
    more.className = "card-preview-note";
    more.textContent = `还有 ${hidden} 张`;
    box.appendChild(more);
  }
  box.hidden = list.length === 0;
  moveCardPreview(event);
}

/** 只有一行说明的预览（非宿主看不到牌面时用它，保持"按可见性绘制"）。 */
function showCardPreviewNote(text, event) {
  const box = $("t-card-preview");
  if (!box) return;
  box.innerHTML = "";
  const note = document.createElement("div");
  note.className = "card-preview-note";
  note.textContent = text;
  box.appendChild(note);
  box.hidden = false;
  moveCardPreview(event);
}

/** 让预览跟着指针走（右下偏移 16px）。缺指针坐标（键盘 / 合成事件）时保持原位。
 *
 * 贴边翻转：右下放不下时改到指针左上（`offsetWidth/Height` 与 `innerWidth/Height` 在
 * 测试垫片里是 0 ⇒ 不翻转，断言仍读固定偏移）。 */
function moveCardPreview(event) {
  const box = $("t-card-preview");
  if (!box || box.hidden) return;
  const x = Number(event && event.clientX);
  const y = Number(event && event.clientY);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return;
  const width = Number(box.offsetWidth) || 0;
  const height = Number(box.offsetHeight) || 0;
  const viewW = Number(window.innerWidth) || 0;
  const viewH = Number(window.innerHeight) || 0;
  let left = x + CARD_PREVIEW_OFFSET;
  let top = y + CARD_PREVIEW_OFFSET;
  if (width && viewW && left + width > viewW) {
    left = Math.max(0, x - width - CARD_PREVIEW_OFFSET);
  }
  if (height && viewH && top + height > viewH) {
    top = Math.max(0, y - height - CARD_PREVIEW_OFFSET);
  }
  box.style.left = `${left}px`;
  box.style.top = `${top}px`;
}

function hideCardPreview() {
  const box = $("t-card-preview");
  if (box) box.hidden = true;
}
