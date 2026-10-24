"""展示契约（U1-04）：把引擎 / 投影事实翻成**界面字段**的纯函数。

纪律（`04` §4 / D10）：

- 这里只做**表现层派生**，不读 DOM、不读录像、不裁决规则；输入是已经过滤好的
  :class:`~sgs.state.CardRef` / :class:`~sgs.state.PlayerPublic` / :class:`~sgs.state.Query`。
- 卡面的 ``display_name`` / ``attribute`` / ``kind`` / ``source_cards`` / ``description``
  与提示条的 ``text`` / ``default_action`` / ``default_label`` 都从这里出；前端不再另抄
  一份 "火杀 / 雷杀叫杀" 的映射（读取口 = ``static/core.js: cardName``）。
- 身份标签（``主公 / 忠臣 / 反贼 / 内奸``）也在这里映射一次；投影里的
  ``PlayerPublic.identity`` 仍是过滤后的枚举值（不可见时 ``None``）。
- 武将块的**名称 / 势力**（U1-02）：``hero_name`` 从武将表翻一次（引擎只给 id），
  ``kingdom`` / ``kingdom_label`` 取公开的 ``PlayerPublic.kingdom`` 再映射成中文；两者都
  不进记录流，只在网页载荷里出现。
- **花色标签**（U2-08 / D10）：``SUIT_LABELS`` 是中文名 + 符号 + 红黑的**唯一**映射表，
  ``option_labels`` 把它派生成询问载荷（只对全是花色的选项闭集）。前端的花色按钮只读
  这份下发值，不再抄第二份中文表；颜色仍读引擎的 :func:`sgs.data.color_of_suit`。
- **描述全文**（U1-04b）住在 :mod:`sgs_web.descriptions`（内容文件
  ``descriptions.json``），这里只按牌名 id 查一次并并入 ``description``；文案本身
  不抄进本模块，更不进引擎 / 记录流。
- **消费节奏**：``display_name`` / ``attribute`` / ``kind`` / ``source_cards`` /
  ``description`` 由 U1-02 / U1-07 的卡面读取（U1-07 读虚拟 / 转化牌的角标、颜色与
  来源牌悬停）；``prompt_display`` 归 U1-03（提示条）。本模块先把契约发出去，
  不在前端提前造表现。
- **处理区显示产物**（U1-07）：``decorate_snapshot(virtual_cards=...)`` 把正在结算的
  虚拟 / 转化牌并入 ``processing`` —— 转化牌的来源实体牌被替换成产物，纯虚拟牌在它
  结算期间直接出现；引擎的 ``state.processing`` 与记录流都不动（历史照旧进事件流）。

**不塞进** :mod:`sgs_web.projection`：投影负责"哪些事实可见"，本模块负责"可见事实怎么显示"。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sgs.data import IDENTITY_LABELS, Hero, HeroTable, Suit, color_of_suit
from sgs.state import CardRef, EndPhase, Pass, PlayerPublic, Query, SelectCards

from .descriptions import card_description
from .timeout import fallback_action, yiji_give_self_choice

__all__ = [
    "CARD_ATTRIBUTES",
    "CARD_KINDS",
    "KINGDOM_LABELS",
    "SUIT_LABELS",
    "card_display",
    "card_lookup_from_query",
    "card_payload",
    "decorate_pending",
    "decorate_snapshot",
    "hero_in_tables",
    "prompt_display",
    "seat_display",
    "suit_labels",
]

CARD_KINDS: tuple[str, ...] = ("normal", "virtual", "transformed")
"""牌面的三种展示种类（稳定报文值；读取口落在 U1-02 / U1-07 的卡面）。"""

CARD_ATTRIBUTES: tuple[str, ...] = ("普通", "火焰", "雷电")
"""牌面的属性（中文展示值；U1-02 的卡面据此分色）。"""

KINGDOM_LABELS: dict[str, str] = {"wei": "魏", "shu": "蜀", "wu": "吴", "qun": "群"}
"""势力枚举值 → 中文展示值（`GLOSSARY.md`「势力」：中文只出现在渲染层）。"""

_SUIT_TEXT: dict[Suit, tuple[str, str]] = {
    Suit.HEART: ("红桃", "♥"),
    Suit.DIAMOND: ("方块", "♦"),
    Suit.SPADE: ("黑桃", "♠"),
    Suit.CLUB: ("梅花", "♣"),
}
"""花色的中文名 + 符号（U2-08 / D10）：**唯一**的映射表，颜色不在这里抄。"""


def _suit_label(suit: Suit) -> dict[str, str]:
    """一类花色的展示字段；颜色读引擎的 :func:`sgs.data.color_of_suit`（同一份真源）。"""
    name, symbol = _SUIT_TEXT[suit]
    color = color_of_suit(suit)
    if color is None:  # pragma: no cover - Suit 是闭集，本条只为全函数性保留
        raise ValueError(f"花色 {suit!r} 没有颜色")
    return {"name": name, "symbol": symbol, "color": color.value}


SUIT_LABELS: dict[str, dict[str, str]] = {
    suit.value: _suit_label(suit) for suit in Suit
}
"""四类花色的展示契约（U2-08 / D10）：``{花色值: {name, symbol, color}}``。

颜色（``red`` / ``black``）与卡面的 ``CardRef.color`` 同一套字符串；中文名与符号只
在这里写一次，前端的花色按钮**只读**这份下发值，不另抄第二份表。
"""


def suit_labels(options: Any) -> dict[str, dict[str, str]] | None:
    """一**组选项值**若全是花色，返回它们的展示标签；否则 ``None``（fail-closed）。

    这是询问载荷 ``option_labels`` 的唯一派生点（票 08）：花色是第一个会被复用的枚举
    模板，将来【杀】的类型 / 锦囊牌名同样从这里出标签。选项闭集里只要有一个不是已知
    花色就整组不翻（不给出半份标签让前端去补），非 ``SELECT_OPTION``（没有
    ``options``）也返回 ``None``。返回值是副本，调用方改它不会污染 :data:`SUIT_LABELS`。
    """
    if not options:
        return None
    labels: dict[str, dict[str, str]] = {}
    for option in options:
        label = SUIT_LABELS.get(option) if isinstance(option, str) else None
        if label is None:
            return None
        labels[option] = dict(label)
    return labels


_SLASH_TYPES = frozenset({"slash", "fire_slash", "thunder_slash"})
"""都显示成「杀」的牌名 id：普通杀 / 火杀 / 雷杀。"""

_FIRE_TYPES = frozenset({"fire_slash", "fire_attack"})
"""**火焰伤害**的牌名 id：火杀（军争）与火攻（锦囊）。

这是**展示事实**（这张牌造成的伤害是火焰），不是规则分支：引擎的属性仍由
``CardBehavior`` / ``ops`` 声明，展示层只把同一事实翻成界面用的 ``attribute``。
``narrative._damage_class`` 读的就是渠道牌的这份 ``attribute``——火攻的伤害帧
渠道牌是【火攻】本体、传导伤害帧沿用源伤害的渠道牌（M5-03 裁定），两条因此都取得到色。
"""

_THUNDER_TYPES = frozenset({"thunder_slash", "lightning"})
"""**雷电伤害**的牌名 id：雷杀（军争）与闪电（标准包延时锦囊）。

与 :data:`_FIRE_TYPES` 同一姿态：只翻展示事实。既有的【闪电】伤害（3 点雷电）
由此从"普通白"回到雷电色，不回归。
"""


def card_display(
    card: CardRef, *, lookup: Mapping[int, CardRef] | None = None
) -> dict[str, Any]:
    """一张牌的展示字段：``display_name`` / ``attribute`` / ``kind`` / ``source_cards`` /
    ``description``。

    ``display_name``：普通杀、火杀、雷杀**都**是「杀」；其余牌用内容包给的牌名。
    ``attribute``：普通 / 火焰 / 雷电（中文展示值）。
    ``kind``：``normal``（实体牌）/ ``virtual``（无实体来源的虚拟牌）/
    ``transformed``（带 ``virtual_sources`` 的转化牌）。
    ``source_cards``：转化牌的来源实体牌（完整牌面 + 展示字段）；纯虚拟牌为空列表。
    ``description``：按 ``card.type`` 查 :mod:`sgs_web.descriptions` 的全文；缺条目时是
    显式的「暂无描述」，不返回空串。
    没有 ``lookup``（409 里附带的裸询问拿不到引擎）时只给空来源，**不猜**。
    """
    if card.is_virtual:
        kind = "transformed" if card.virtual_sources else "virtual"
    else:
        kind = "normal"
    if card.type in _FIRE_TYPES:
        attribute = "火焰"
    elif card.type in _THUNDER_TYPES:
        attribute = "雷电"
    else:
        attribute = "普通"
    sources: list[dict[str, Any]] = []
    if kind == "transformed" and lookup is not None:
        for source_id in card.virtual_sources:
            source = lookup.get(int(source_id))
            if source is not None:
                sources.append(card_payload(source, lookup=lookup))
    return {
        "display_name": "杀" if card.type in _SLASH_TYPES else card.name,
        "attribute": attribute,
        "kind": kind,
        "source_cards": sources,
        "description": card_description(card.type),
    }


def card_payload(
    card: CardRef, *, lookup: Mapping[int, CardRef] | None = None
) -> dict[str, Any]:
    """一张牌的完整卡面事实 + 展示字段（转化牌来源 / 处理区产物共用）。

    产物自己也要带 ``source_cards``（悬停要看全部来源），故 ``lookup`` 必须继续传下去；
    来源恒为实体牌，递归只走一层。
    """
    payload = card.model_dump(mode="json")
    payload.update(card_display(card, lookup=lookup))
    return payload


def _processing_display(
    cards: Any,
    *,
    lookup: Mapping[int, CardRef] | None,
    virtual_cards: tuple[CardRef, ...] | list[CardRef],
) -> list[Any]:
    """处理区要画的牌：**正在结算的虚拟 / 转化牌**顶掉它们的来源实体牌。

    ``cards`` 是 ``PlayerView.processing`` 的序列化形态（引擎只把**实体牌**放进区域）；
    转化牌的来源牌正在处理区时，界面要画的是产物（U1-07 / spec §2.5），故按
    ``virtual_sources`` 把来源整组换成那张虚拟牌。纯虚拟牌没有来源、从不进区域，
    在它结算期间直接追加进处理区。同一个来源组只换一次；来源不齐（已有一张离场）
    就不换，避免把半组来源错画成产物。
    """
    if not isinstance(cards, list):
        return cards
    by_id = {
        int(card["card_id"]): index
        for index, card in enumerate(cards)
        if isinstance(card, dict) and "card_id" in card
    }
    replacements: dict[int, dict[str, Any]] = {}
    covered: set[int] = set()
    extras: list[dict[str, Any]] = []
    for virtual in virtual_cards:
        sources = tuple(int(source) for source in virtual.virtual_sources)
        if not sources:
            extras.append(card_payload(virtual, lookup=lookup))
            continue
        if not all(source in by_id for source in sources) or any(
            source in covered for source in sources
        ):
            continue
        replacements[min(by_id[source] for source in sources)] = card_payload(
            virtual, lookup=lookup
        )
        covered.update(sources)
    out: list[Any] = []
    for index, card in enumerate(cards):
        replacement = replacements.get(index)
        if replacement is not None:
            out.append(replacement)
        if isinstance(card, dict) and int(card.get("card_id", 0)) in covered:
            continue
        out.append(_extend_card_dict(card, lookup=lookup))
    out.extend(extras)
    return out


def seat_display(
    player: PlayerPublic, *, heroes: HeroTable | Sequence[HeroTable] | None = None
) -> dict[str, Any]:
    """座位卡的展示字段：横置 / 酒状态 + 身份标签 + 武将名称 / 势力。

    ``chained`` / ``wine_active`` 来自投影（U1-04 起是 ``PlayerPublic`` 字段）；
    网页载荷里的身份标签由这里统一给出，前端可优先读它；旧的 ``identityLabel`` 仅作
    回放 / 合成载荷兜底。

    ``hero_name`` / ``kingdom`` / ``kingdom_label``（U1-02）：引擎只给武将 id
    （``PlayerPublic.hero``）与势力枚举；``heroes`` 是武将表——**可以是多张**（M6 票 19：
    标准表 + 界版包），引擎配将的默认池是两者的并集，只查一张表会让界将退回引擎 id。
    缺表 / 未发放武将时给 ``None``（界面退回「（无武将）」）。
    """
    hero = hero_in_tables(heroes, player.hero)
    kingdom = player.kingdom.value if player.kingdom is not None else None
    return {
        "chained": bool(player.chained),
        "wine_active": bool(player.wine_active),
        "identity_label": (
            IDENTITY_LABELS.get(player.identity) if player.identity is not None else None
        ),
        "hero_name": hero.name if hero is not None else None,
        "kingdom": kingdom,
        "kingdom_label": KINGDOM_LABELS.get(kingdom) if kingdom is not None else None,
    }


def hero_in_tables(
    heroes: HeroTable | Sequence[HeroTable] | None, hero_id: str | None
) -> Hero | None:
    """按武将 id 在**一张或多张**武将表里取武将；取不到返回 ``None``（不编名字）。

    单一读取面（M6 票 19）：默认池是标准 + 界版的并集，故"id → 武将"的解析必须能跨表。
    接受单张表（既有调用方 / 测试）或表的序列；``None`` / 未发放武将一律 ``None``。
    """
    if heroes is None or not hero_id:
        return None
    tables = (heroes,) if isinstance(heroes, HeroTable) else tuple(heroes)
    for table in tables:
        hero = table.by_id(hero_id)
        if hero is not None:
            return hero
    return None


def prompt_display(query: Query) -> dict[str, Any]:
    """提示条契约：询问文本 + 服务端超时默认动作 + 给人看的默认动作说明。

    默认动作的真源仍是 :func:`sgs_web.timeout.fallback_action`（遗计给牌特判 →
    ``Pass`` → ``EndPhase`` → ``legal_actions[0]``）；这里只把它序列化成前端可直接显示的
    形状，不自己重新选。没有候选是引擎不该出现的状态：与 ``fallback_action`` 一致直接抛，
    **不**编一个 ``None`` 默认动作把契约打成半截；调用方（``decorate_pending``）对损坏报文
    显式下发 ``prompt_display = None``。

    **【遗计】的文案特判**（U2-12 / spec D2「超时 = 剩下的交给自己」）：只在默认动作**真的**
    是那条 ``seat:{actor}`` 时才说"交给自己"——判据与代答共用
    :func:`~sgs_web.timeout.yiji_give_self_choice`，不按技能名猜，也不改任何规则。选牌步
    同理：亮牌面的默认动作是第一张亮牌，文案就说第一张亮牌（仍是纯展示）。

    **【观星】排列的文案特判**（U2-07 / spec D9.3）：排列到时限不是服务端取第一项，而是
    **前端自动提交当前排列**（`answer.js:maybeAutoSubmitArrange`，本仓「前端不代答」的唯一
    例外）。文案因此说"按当前排列提交"；服务端的 ``fallback_action`` 仍是 ``ArrangeCards``
    候选里的第一条，契约本身不改。
    """
    default = fallback_action(query)
    give_self = yiji_give_self_choice(query)
    if give_self is not None:
        label = "超时将把剩下的牌交给自己"
    elif str(getattr(query, "kind", "")) == "ARRANGE":
        label = "超时将按当前排列提交"
    elif (
        isinstance(default, SelectCards)
        and str(getattr(query, "kind", "")) == "SELECT_CARDS"
        and getattr(query, "revealed", ())
    ):
        label = "超时将自动选择第一张亮牌"
    elif isinstance(default, Pass):
        label = "超时将自动放弃"
    elif isinstance(default, EndPhase):
        label = "超时将自动结束出牌阶段"
    else:
        label = "超时将自动选择第一项"
    return {
        "text": query.prompt,
        "default_action": default.model_dump(mode="json"),
        "default_label": label,
    }


def _extend_card_dict(
    card: dict[str, Any], *, lookup: Mapping[int, CardRef] | None = None
) -> dict[str, Any]:
    """把展示字段并进一份**已序列化**的牌面 dict；幂等（已有 ``display_name`` 即原样返回）。"""
    if not isinstance(card, dict) or "display_name" in card:
        return card
    model = CardRef.model_validate(card)
    return {**card, **card_display(model, lookup=lookup)}


def _extend_card_list(
    cards: Any, *, lookup: Mapping[int, CardRef] | None = None
) -> list[Any]:
    if not isinstance(cards, list):
        return cards
    return [_extend_card_dict(card, lookup=lookup) for card in cards]


def card_lookup_from_query(query: Query) -> dict[int, CardRef]:
    """从**裸 Query**（409 里附带的现状）收集 ``card_id → CardRef``。

    409 拿不到 ``Session.engine``，但询问自己带着视角 / 授予牌面 / 选择面槽位 / 候选牌；
    转化牌的来源牌通常就在这些位置。只收询问里已经出现的牌，不额外读引擎、不越权。
    """
    cards: dict[int, CardRef] = {}

    def add(card: CardRef | None) -> None:
        if card is not None:
            cards[card.card_id] = card

    for card in query.view.self_hand:
        add(card)
    for player in query.view.players:
        for card in (*player.equip, *player.judge):
            add(card)
    for card in query.revealed:
        add(card)
    for slot in getattr(query, "slots", ()) or ():
        add(slot.card)
    for action in query.legal_actions:
        add(getattr(action, "card", None))
        for card in getattr(action, "cards", ()) or ():
            add(card)
    return cards


def decorate_snapshot(
    payload: dict[str, Any],
    players: tuple[PlayerPublic, ...] | list[PlayerPublic],
    *,
    lookup: Mapping[int, CardRef] | None = None,
    heroes: Sequence[HeroTable] | None = None,
    virtual_cards: tuple[CardRef, ...] | list[CardRef] = (),
) -> dict[str, Any]:
    """给一份 ``PlayerView`` 的 JSON 形态补展示字段（原地改，返回同一个 dict）。

    ``players`` 与载荷里的 ``players`` 同序（投影按座位序输出）；座位字段由
    :func:`seat_display` 给出，卡面字段由 :func:`card_display` 给出。``heroes``
    是武将表（U1-02 的 ``hero_name`` 要用；M6 票 19 起是**多张表**——标准 + 界版包，
    与配将池同源），缺省时名称留空、界面退回 id。
    ``virtual_cards`` 是**当前正在结算**的虚拟 / 转化牌（U1-07）：``processing``
    按它换成产物（见 :func:`_processing_display`），其余区域只补展示字段。
    """
    for key in ("self_hand", "discard"):
        payload[key] = _extend_card_list(payload.get(key), lookup=lookup)
    payload["processing"] = _processing_display(
        payload.get("processing"), lookup=lookup, virtual_cards=virtual_cards
    )
    seated = payload.get("players")
    if isinstance(seated, list):
        for player_payload, player in zip(seated, players, strict=True):
            for key in ("equip", "judge"):
                player_payload[key] = _extend_card_list(
                    player_payload.get(key), lookup=lookup
                )
            player_payload.update(seat_display(player, heroes=heroes))
    return payload


def decorate_pending(
    payload: dict[str, Any],
    query: Query,
    *,
    lookup: Mapping[int, CardRef] | None = None,
    heroes: Sequence[HeroTable] | None = None,
    virtual_cards: tuple[CardRef, ...] | list[CardRef] = (),
) -> dict[str, Any]:
    """给一份 ``pending_payload`` 补展示字段（询问里的所有牌面 + 内嵌视角 + 提示条）。"""
    for entry in (payload.get("candidate_index") or {}).values():
        if isinstance(entry, dict):
            entry["card"] = _extend_card_dict(entry.get("card"), lookup=lookup)
    # `legal_actions` **不装饰**：界面会把选中的候选**原样**回传，而 Action 模型
    # `extra="forbid"`——多出的 display_* 键会让提交变成 400。展示字段只挂在只读载体
    # （候选索引 / 选择面 / 授予的牌面 / 视角）上；候选文案由前端按 card_id 从
    # `candidate_index` 取展示名（见 `static/core.js: cardName`）。
    for slot in payload.get("slots") or []:
        if isinstance(slot, dict) and isinstance(slot.get("card"), dict):
            slot["card"] = _extend_card_dict(slot["card"], lookup=lookup)
    payload["revealed"] = _extend_card_list(payload.get("revealed"), lookup=lookup)
    view = payload.get("view")
    if isinstance(view, dict):
        decorate_snapshot(
            view,
            query.view.players,
            lookup=lookup,
            heroes=heroes,
            virtual_cards=virtual_cards,
        )
    # 没有候选是引擎不该出现的状态；显式给 None，不让展示派生抛异常，也不伪造默认动作。
    payload["prompt_display"] = prompt_display(query) if query.legal_actions else None
    return payload
