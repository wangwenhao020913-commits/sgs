"""展示契约（U1-04）：把引擎 / 投影事实翻成**界面字段**的纯函数。

纪律（`04` §4 / D10）：

- 这里只做**表现层派生**，不读 DOM、不读录像、不裁决规则；输入是已经过滤好的
  :class:`~sgs.state.CardRef` / :class:`~sgs.state.PlayerPublic` / :class:`~sgs.state.Query`。
- 卡面的 ``display_name`` / ``attribute`` / ``kind`` / ``source_cards`` 与提示条的
  ``text`` / ``default_action`` / ``default_label`` 都从这里出；前端不再另抄一份
  "火杀 / 雷杀叫杀"的映射。
- 身份标签（``主公 / 忠臣 / 反贼 / 内奸``）也只在这里映射一次；投影里的
  ``PlayerPublic.identity`` 仍是过滤后的枚举值（不可见时 ``None``）。

**不塞进** :mod:`sgs_web.projection`：投影负责"哪些事实可见"，本模块负责"可见事实怎么显示"；
描述全文（`descriptions.json`）归 U1-04b，不在本模块。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sgs.data import IDENTITY_LABELS
from sgs.state import CardRef, EndPhase, Pass, PlayerPublic, Query

from .timeout import fallback_action

__all__ = [
    "CARD_ATTRIBUTES",
    "CARD_KINDS",
    "card_display",
    "decorate_pending",
    "decorate_snapshot",
    "prompt_display",
    "seat_display",
]

CARD_KINDS: tuple[str, ...] = ("normal", "virtual", "transformed")
"""牌面的三种展示种类（稳定报文值；读取方 = 前端卡面）。"""

CARD_ATTRIBUTES: tuple[str, ...] = ("普通", "火焰", "雷电")
"""牌面的属性（中文展示值；火杀 / 雷杀的产物按属性分色）。"""

_SLASH_TYPES = frozenset({"slash", "fire_slash", "thunder_slash"})
"""都显示成「杀」的牌名 id：普通杀 / 火杀 / 雷杀。"""

_FIRE_TYPES = frozenset({"fire_slash", "fire_attack"})
"""火焰属性：火杀（军争）与火攻（军争）；军争落地前只有测试会构造它们。"""

_THUNDER_TYPES = frozenset({"thunder_slash"})
"""雷电属性：雷杀（军争）。"""


def card_display(
    card: CardRef, *, lookup: Mapping[int, CardRef] | None = None
) -> dict[str, Any]:
    """一张牌的展示字段：``display_name`` / ``attribute`` / ``kind`` / ``source_cards``。

    ``display_name``：普通杀、火杀、雷杀**都**是「杀」；其余牌用内容包给的牌名。
    ``attribute``：普通 / 火焰 / 雷电（中文展示值）。
    ``kind``：``normal``（实体牌）/ ``virtual``（无实体来源的虚拟牌）/
    ``transformed``（带 ``virtual_sources`` 的转化牌）。
    ``source_cards``：转化牌的来源实体牌（完整牌面 + 展示字段）；纯虚拟牌为空列表。
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
                sources.append(_source_card(source))
    return {
        "display_name": "杀" if card.type in _SLASH_TYPES else card.name,
        "attribute": attribute,
        "kind": kind,
        "source_cards": sources,
    }


def _source_card(card: CardRef) -> dict[str, Any]:
    """转化牌的来源牌：完整牌面事实 + 它自己的展示字段（来源恒为实体牌，不再递归）。"""
    payload = card.model_dump(mode="json")
    payload.update(card_display(card))
    return payload


def seat_display(player: PlayerPublic) -> dict[str, Any]:
    """座位卡的展示字段：横置 / 酒状态 + 身份中文标签（不可见时 ``None``）。

    ``chained`` / ``wine_active`` 来自投影（U1-04 起是 ``PlayerPublic`` 字段）；
    网页载荷里的身份标签由这里统一映射（前端的旧 ``identityLabel`` 仅作合成 / 旧载荷兜底）。
    """
    return {
        "chained": bool(player.chained),
        "wine_active": bool(player.wine_active),
        "identity_label": (
            IDENTITY_LABELS.get(player.identity) if player.identity is not None else None
        ),
    }


def prompt_display(query: Query) -> dict[str, Any]:
    """提示条契约：询问文本 + 服务端超时默认动作 + 给人看的默认动作说明。

    默认动作的真源仍是 :func:`sgs_web.timeout.fallback_action`（``Pass → EndPhase →
    legal_actions[0]``）；这里只把它序列化成前端可直接显示的形状，不自己重新选。

    没有候选（引擎不该出现的状态，但合成载荷 / 损坏报文里会有）时**不抛**：下发
    ``default_action = None`` + 一句"超时由服务端判定"，别让展示派生把整份载荷打红。
    """
    if not query.legal_actions:
        return {
            "text": query.prompt,
            "default_action": None,
            "default_label": "超时由服务端判定",
        }
    default = fallback_action(query)
    if isinstance(default, Pass):
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


def extend_card_dict(
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
    return [extend_card_dict(card, lookup=lookup) for card in cards]


def decorate_snapshot(
    payload: dict[str, Any],
    players: tuple[PlayerPublic, ...] | list[PlayerPublic],
    *,
    lookup: Mapping[int, CardRef] | None = None,
) -> dict[str, Any]:
    """给一份 ``PlayerView`` 的 JSON 形态补展示字段（原地改，返回同一个 dict）。

    ``players`` 与载荷里的 ``players`` 同序（投影按座位序输出）；座位字段由
    :func:`seat_display` 给出，卡面字段由 :func:`card_display` 给出。
    """
    for key in ("self_hand", "discard", "processing"):
        payload[key] = _extend_card_list(payload.get(key), lookup=lookup)
    seated = payload.get("players")
    if isinstance(seated, list):
        for player_payload, player in zip(seated, players, strict=True):
            for key in ("equip", "judge"):
                player_payload[key] = _extend_card_list(
                    player_payload.get(key), lookup=lookup
                )
            player_payload.update(seat_display(player))
    return payload


def decorate_pending(
    payload: dict[str, Any],
    query: Query,
    *,
    lookup: Mapping[int, CardRef] | None = None,
) -> dict[str, Any]:
    """给一份 ``pending_payload`` 补展示字段（询问里的所有牌面 + 内嵌视角 + 提示条）。"""
    for entry in (payload.get("candidate_index") or {}).values():
        if isinstance(entry, dict):
            entry["card"] = extend_card_dict(entry.get("card"), lookup=lookup)
    # `legal_actions` **不装饰**：界面会把选中的候选**原样**回传，而 Action 模型
    # `extra="forbid"`——多出的 display_* 键会让提交变成 400。展示字段只挂在只读载体
    # （候选索引 / 选择面 / 授予的牌面 / 视角）上；候选文案由前端按 card_id 从
    # `candidate_index` 取展示名（见 `static/core.js: cardName`）。
    for slot in payload.get("slots") or []:
        if isinstance(slot, dict) and isinstance(slot.get("card"), dict):
            slot["card"] = extend_card_dict(slot["card"], lookup=lookup)
    payload["revealed"] = _extend_card_list(payload.get("revealed"), lookup=lookup)
    view = payload.get("view")
    if isinstance(view, dict):
        decorate_snapshot(view, query.view.players, lookup=lookup)
    payload["prompt_display"] = prompt_display(query)
    return payload
