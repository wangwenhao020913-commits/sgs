"""叶子原语：只改状态、返回 delta，**绝不**压栈 / 发询问 / 暂停 / 访问结算栈（票 07 §1）。

两个口：``move_card``（移动牌）与 ``apply_hp``（写体力，唯一改体力的口）。
洗牌是"明确的随机置换"，走 ``deck_shuffle`` 记录而不是 ``move_card``（票 08 §5）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sgs.state import (
    CardMoveDelta,
    CardRef,
    GameState,
    HpChangeDelta,
    MoveReason,
    Zone,
)

__all__ = [
    "EffectContext",
    "Randomness",
    "apply_hp",
    "draw_from_deck",
    "locate",
    "move_card",
]


class Randomness(Protocol):
    """唯一随机源暴露给效果层的最小接口（票 08 §2）；效果层不 import ``random``。"""

    def shuffle(self, items: list[int]) -> None: ...

    def randbelow(self, n: int) -> int: ...

    def randint(self, a: int, b: int) -> int: ...


@dataclass(frozen=True, slots=True)
class EffectContext:
    """一次结算的主体与层级信息；原语只读它，不改它。"""

    actor: int
    source: int | None = None
    targets: tuple[int, ...] = ()
    card: CardRef | None = None
    frame_id: int = 0
    depth: int = 0
    rng: Randomness | None = None


@dataclass(frozen=True, slots=True)
class _Location:
    zone: Zone
    owner: int | None = None


def locate(state: GameState, card_id: int) -> _Location:
    """找出这张牌现在在哪个区域（一张实体牌任一时刻只在一个区域）。"""
    if card_id in state.deck:
        return _Location(Zone.DECK)
    for player in state.players:
        if card_id in player.hand:
            return _Location(Zone.HAND, player.seat)
    if card_id in state.discard:
        return _Location(Zone.DISCARD)
    if card_id in state.processing:
        return _Location(Zone.PROCESSING)
    raise ValueError(f"牌 {card_id} 不在任何区域；牌守恒被破坏")


def _source_list(state: GameState, location: _Location) -> list[int]:
    if location.zone is Zone.DECK:
        return state.deck
    if location.zone is Zone.DISCARD:
        return state.discard
    if location.zone is Zone.PROCESSING:
        return state.processing
    if location.owner is None:
        raise ValueError("手牌区没有持有者座位")
    return state.player(location.owner).hand


def move_card(
    state: GameState,
    ctx: EffectContext,
    cards: tuple[CardRef, ...],
    *,
    to_zone: Zone,
    reason: MoveReason,
    to_seat: int | None = None,
) -> CardMoveDelta:
    """把一批牌从各自（同一个）来源区域移到目标区域，返回一条变化事件。

    M1 的调用点包括：发牌、摸牌、使用入处理区、处理区→弃牌堆、弃牌阶段、
    ``DEATH_ON`` 遗牌与终局收尾。``ctx`` 只提供结算主体信息，本原语不读结算栈。
    """
    card_ids = tuple(card.card_id for card in cards)
    if not card_ids:
        raise ValueError("空的一批牌不构成一次移动操作")

    source = locate(state, card_ids[0])
    for card_id in card_ids[1:]:
        other = locate(state, card_id)
        if other.zone is not source.zone or other.owner != source.owner:
            raise ValueError("一次 move_card 只能有一个来源区域")

    source_list = _source_list(state, source)
    # 成员判定用元组而非 set：本项目禁一切无序容器参与顺序（票 08 §7）。
    kept = [card_id for card_id in source_list if card_id not in card_ids]
    source_list[:] = kept

    if to_zone is Zone.HAND:
        if to_seat is None:
            raise ValueError("移动到手牌必须给出目标座位")
        state.player(to_seat).hand.extend(card_ids)
    else:
        state.zone(to_zone).extend(card_ids)

    return CardMoveDelta(
        cards=cards,
        from_zone=source.zone,
        to_zone=to_zone,
        reason=reason,
        owner=source.owner if source.zone is Zone.HAND else None,
    )


def apply_hp(state: GameState, ctx: EffectContext, seat: int, value: int) -> HpChangeDelta:
    """把座位 ``seat`` 的体力**写成** ``value``，返回一条变化事件（唯一改体力的口）。

    ``value`` 由调用帧算好（扣减帧 = ``hp - amount``；回复帧 = ``min(hp + amount, hp_max)``）；
    本原语只做**上限**检查——越过体力上限是编程错误，帧必须先夹住。

    **下限不在这里**：官方明说"角色的体力有可能小于 0"，体力降至 0 即进入濒死结算，
    是规则上的中间态（濒死流程归票 21）。因此这里允许写到 0 以下；调用方不得据此
    猜测"死亡"，死亡由濒死 → 死亡事件裁定。
    """
    player = state.player(seat)
    if value > player.hp_max:
        raise ValueError(f"体力 {value} 超过上限 {player.hp_max}；回复帧必须先夹住")
    before = player.hp
    player.hp = value
    return HpChangeDelta(seat=seat, before=before, after=value, amount=value - before)


def draw_from_deck(state: GameState, n: int) -> tuple[CardRef, ...]:
    """牌堆顶至多 ``n`` 张的牌面事实；**不改状态**（由 ``move_card`` 真正移动）。

    返回长度 ``< n`` 就是"短摸"信号：需要摸牌而牌堆给不出（票 08 §6）。
    重洗规则（票 23）落地后，这个短返回只在弃牌堆也为空时出现——届时签名补上 ``rng``
    （票 07 §7 的冻结形状），本函数内部负责"牌堆空且弃牌堆非空则先重洗"。
    """
    return state.refs(state.deck[:n])
