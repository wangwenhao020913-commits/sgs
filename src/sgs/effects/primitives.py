"""叶子原语：只改状态、返回 delta，**绝不**压栈 / 发询问 / 暂停 / 访问结算栈（票 07 §1）。

两个口：``move_card``（移动牌）与 ``apply_hp``（写体力，唯一改体力的口）。
洗牌是"明确的随机置换"，走 ``deck_shuffle`` 记录而不是 ``move_card``（票 08 §5）：
``draw_from_deck`` 在牌堆耗尽时把弃牌堆重洗成新牌堆，并把这次置换作为 delta 交回调用方。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from sgs.state import (
    CardMoveDelta,
    CardRef,
    DeckShuffleDelta,
    GameState,
    HpChangeDelta,
    MoveReason,
    Zone,
)

__all__ = [
    "DrawResult",
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


@dataclass(frozen=True, slots=True)
class DrawResult:
    """一次摸牌的结果：自顶向下的牌面事实 + 途中最多一次重洗（票 08 §4 / 票 23）。

    ``shuffle`` 非空时调用方必须先把它 emit 成一条 ``deck_shuffle`` 记录，
    再做这张/这些牌的 ``move_card``——记录流里的次序因此与真实发生次序一致。
    """

    cards: tuple[CardRef, ...]
    shuffle: DeckShuffleDelta | None = None


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


def draw_from_deck(state: GameState, n: int, rng: Randomness) -> DrawResult:
    """自牌堆顶取至多 ``n`` 张；牌堆耗尽而弃牌堆非空时先重洗，再继续（票 08 §4 / 票 23）。

    返回的 ``cards`` 是自顶向下的牌面事实，**不改这批牌的位置**（由 ``move_card`` 真正移动）；
    ``shuffle`` 是途中发生的那次重洗（每取一张前检查，故一次调用最多一次），
    记录内容 = 洗后新牌堆的顺序，**不记洗前**，也**不**另发 ``card_move``（票 08 §5）。

    返回长度 ``< n`` 就是"短摸"信号：需要摸牌而两堆皆空（票 08 §6）。
    """
    if n < 0:
        raise ValueError(f"摸牌张数不能为负，得到 {n!r}")
    taken: list[int] = []
    shuffle: DeckShuffleDelta | None = None
    while len(taken) < n:
        if len(taken) < len(state.deck):
            # 已取的那些牌还在 state.deck 的前缀里（尚未 move_card 走），故游标即 len(taken)。
            taken.append(state.deck[len(taken)])
            continue
        if not state.discard:
            break
        shuffle = _reshuffle_into_deck(state, rng)
    return DrawResult(cards=state.refs(taken), shuffle=shuffle)


def _reshuffle_into_deck(state: GameState, rng: Randomness) -> DeckShuffleDelta:
    """把弃牌堆洗混后接到牌堆尾，返回一条 ``deck_shuffle``（规则 10.4 / 票 08 §5）。

    已经摸出的牌仍在 ``state.deck`` 的前缀里，故**只**置换新并入的那一段：
    记录里的 ``cards`` 因此恰是洗后的**新牌堆**顺序；``move_card`` 随后移走前缀，
    牌堆剩下的就是这段新牌堆减去本次继续摸走的那几张。
    """
    shuffled = list(state.discard)
    rng.shuffle(shuffled)
    state.discard.clear()
    state.deck.extend(shuffled)
    return DeckShuffleDelta(
        cards=state.refs(shuffled),
        from_zone=Zone.DISCARD,
        to_zone=Zone.DECK,
    )
