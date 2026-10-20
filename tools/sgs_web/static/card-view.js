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

/** 展示种类（服务端 `kind` 的稳定报文值）→ 卡面底部的中文。 */
const CARD_KIND_LABELS = { normal: "普通", virtual: "虚拟", transformed: "转化" };

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
    const kind = CARD_KIND_LABELS[card.kind];
    if (kind) parts.push(kind);
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
 */
function cardFaceNode(card, opts = {}) {
  const node = document.createElement("button");
  node.type = "button";
  node.className = "card face";
  if (opts.selected) node.classList.add("sel");
  if (opts.picked) node.classList.add("picked");
  if (opts.static) node.classList.add("static");
  if (!opts.static && opts.usable === false) node.classList.add("dis");
  if (card.attribute === "火焰") node.classList.add("attr-fire");
  if (card.attribute === "雷电") node.classList.add("attr-thunder");
  node.setAttribute("data-card-id", String(card.card_id));

  const color = suitColorClass(card);
  const suit = document.createElement("span");
  suit.className = color ? `suit ${color}` : "suit";
  suit.textContent = suitSymbol(card.suit);
  const rank = document.createElement("span");
  rank.className = "rank";
  rank.textContent = rankLabel(card.rank);
  const name = document.createElement("span");
  name.className = "cname";
  name.textContent = cardName(card);
  const type = document.createElement("span");
  type.className = "ctype";
  type.textContent = cardTypeLine(card);
  node.appendChild(suit);
  node.appendChild(rank);
  node.appendChild(name);
  node.appendChild(type);
  // 悬停全文（U1-04b）：只读服务端下发的 description，前端不存文案；缺条目由服务端
  // 填「暂无描述」。静态牌也挂，装备区 / 判定区 / 处理区因此可悬停。
  node.setAttribute("title", cardDescription(card));
  if (!opts.static) {
    node.addEventListener("click", (event) => {
      if (event && event.stopPropagation) event.stopPropagation();
      if (opts.onClick) opts.onClick();
    });
  }
  return node;
}
