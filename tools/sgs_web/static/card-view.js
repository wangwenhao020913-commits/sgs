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

/** 展示种类（服务端 `kind` 的稳定报文值）→ 卡面底行 / 右上角标（U1-07）。
 *
 * 普通牌没有角标；虚拟 / 转化牌的角标只在中央舞台 / 处理区出现（手牌拿到的是源实体牌）。
 */
const CARD_KIND_PRESENTATION = {
  normal: { label: "普通", corner: "" },
  virtual: { label: "虚拟", corner: "虚" },
  transformed: { label: "转化", corner: "转" },
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

/** 卡面底部那行：类型 / 属性 / 攻击范围（只拼服务端已给的事实）。
 *
 * 类型：装备牌用槽名（武器 / 防具 / 攻马 / 防马），其余用展示种类（普通 / 虚拟 / 转化）。
 * 属性只在**不是普通**时出现（火杀 / 雷杀才标红蓝）；攻击范围只属武器。
 */
function cardTypeLine(card) {
  if (!card) return "";
  const parts = [];
  if (card.slot && EQUIP_SLOT_LABELS[card.slot]) {
    parts.push(EQUIP_SLOT_LABELS[card.slot]);
  } else {
    const kind = CARD_KIND_PRESENTATION[cardKind(card)];
    if (kind) parts.push(kind.label);
  }
  if (card.attribute && card.attribute !== "普通") parts.push(card.attribute);
  if (card.attack_range !== null && card.attack_range !== undefined) {
    parts.push(`范围 ${card.attack_range}`);
  }
  return parts.join(" · ");
}

/** 一张**完整卡面**（88×124）的节点；手牌 / 处理区 / 中央舞台共用。
 *
 * ``opts.selected`` 选中态（`.sel`，焦点牌）、``opts.picked`` 已选态（`.picked`，弃牌 /
 * 提供 B / 预发动选中的牌）、``opts.usable === false`` 置灰（`.dis`）、``opts.static``
 * 不可点（公开信息）。点击回调由调用方给；静态牌也有悬停全文。
 *
 * **虚拟 / 转化牌（U1-07）**：只在中央舞台 / 处理区出现，右上角标「虚 / 转」；
 * 转化牌左上角另有颜色标记（红 / 黑 / 无），单源同时继承花色 / 点数（引擎给的
 * `suit` / `rank`），多源没有这两项。手牌区拿到的始终是源实体牌（普通牌），
 * 因此不会多出角标。
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
  const type = document.createElement("span");
  type.className = "ctype";
  type.textContent = cardTypeLine(card);
  node.appendChild(name);
  node.appendChild(type);
  // 悬停全文（U1-04b / U1-07）：只读服务端下发的 description 与来源牌；前端不存文案。
  node.setAttribute("title", cardHoverText(card));
  if (!opts.static) {
    node.addEventListener("click", (event) => {
      if (event && event.stopPropagation) event.stopPropagation();
      if (opts.onClick) opts.onClick();
    });
  }
  return node;
}
