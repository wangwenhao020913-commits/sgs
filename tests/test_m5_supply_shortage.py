"""M5-06：【兵粮寸断】——距离 1 的延时锦囊 / 判定 / 跳过摸牌阶段（spec §5.1.4 / §6 A1）。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流（timing / ``card_move``）
+ ``legal_actions_for`` / ``check_targets`` 的候选面 + 无懈窗口（``test_nullify.py`` 同姿态）。

钉住的合同：

1. 目标 = 距离 1 以内的一名**其他**角色（不能指定自己，也不能指定距离 2 者）；
2. 使用后置于目标判定区，**判定阶段**才结算（使用当时不压 RESOLVE）；
3. 判定**不是梅花** ⇒ 跳过该角色的**摸牌阶段**（连阶段体与边界一并越过，正常进出牌阶段）；
   是梅花 ⇒ 照常摸两张；
4. 判定前有无懈窗口（约定 B：延到判定阶段的那张结算帧上）；被无懈 ⇒ 不判定、不跳摸牌；
5. 同名延时锦囊不能叠在同一判定区（走既有的同名守卫）；判定后结算完进弃牌堆（不顺延）。

军争牌只在 ``standard_ex_160`` 档里，故每个用例都显式选这一档（档位纪律）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Suit, load_content_pack
from sgs.effects import check_targets, legal_actions_for
from sgs.engine import Engine
from sgs.state import (
    EndPhase,
    MoveReason,
    Pass,
    Phase,
    QueryKind,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, run_turns

PACK = load_content_pack(deck_profile="standard_ex_160")

SHORTAGE = cards_of_type(PACK, "supply_shortage")
NULLIFY = cards_of_type(PACK, "nullify")
SHORTAGE_CARD = SHORTAGE[0]
"""被测的渠道牌（另一张作"同名"对照，两张花色不同）。"""


def _find_card(*, suit: Suit, exclude: tuple[int, ...] = ()) -> int:
    """按判定结果找一张具体牌（判定花色由测试显式指定，不依赖洗牌）。"""
    excluded = set(exclude)
    for index, card in enumerate(PACK.deck.cards):
        if index in excluded:
            continue
        if card.suit is suit:
            return index
    raise AssertionError(f"标准 160 张里找不到 {suit} 的牌")


NOT_CLUB = _find_card(suit=Suit.SPADE, exclude=tuple(SHORTAGE))
CLUB = _find_card(suit=Suit.CLUB, exclude=tuple(SHORTAGE))


# ---------------------------------------------------------------------------
# 驱动：最小应答参与者（响应窗口一律 Pass、出牌阶段直接结束）
# ---------------------------------------------------------------------------


class _Passive:
    """除"用一张指定牌"外全部取最小合法应答；录下询问供断言。"""

    def __init__(self, play: int | None = None) -> None:
        self.play = play
        self.queries: list[Any] = []

    def respond(self, query):
        self.queries.append(query)
        if query.kind == QueryKind.PLAY_PHASE:
            if self.play is not None:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.card_id == self.play:
                        self.play = None
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


class _Nullifier:
    """在**无懈窗口**里用掉一张【无懈可击】；其余最小应答。"""

    def __init__(self, card_id: int) -> None:
        self.card_id = card_id
        self.used = False

    def respond(self, query):
        if query.kind == QueryKind.RESPOND and query.cause == WindowCause.NULLIFY:
            if not self.used:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.card_id == self.card_id:
                        self.used = True
                        return action
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        return _Passive().respond(query)


def _deck_excluding(*worst: int) -> tuple[int, ...]:
    excluded = set(worst)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _first_turn(engine) -> list[Any]:
    """第一个回合（座位 0）的记录，直到该回合的 ``TURN_AFTER_END`` 为止。

    判定 / 跳过摸牌都发生在这个回合里；把断言收在它之内，才不会读进下一个座位
    摸索 / 重洗造成的记录（``run_turns(turns=1)`` 停在"下一回合的第一个询问"处）。
    """
    out: list[Any] = []
    for record in engine.records:
        out.append(record)
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END:
            break
    return out


def _timings(records) -> list[Timing]:
    return [record.timing for record in records if record.kind == "timing"]


def _moves(records, *, reason: MoveReason):
    return [
        record
        for record in records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is reason
    ]


def _four_seats(
    *,
    seat0_hand: tuple[int, ...] = (),
    judge1: tuple[int, ...] = (),
) -> list[SeatSpec]:
    return [
        SeatSpec(hp=4, hand=seat0_hand),
        SeatSpec(hp=4, judge=judge1),
        SeatSpec(hp=4),
        SeatSpec(hp=4),
    ]


# ---------------------------------------------------------------------------
# 1. 目标规则：其他角色、距离 1
# ---------------------------------------------------------------------------


def test_supply_shortage_targets_only_other_characters_within_distance_one():
    """4 人局：座位 1 / 3 距离 1、座位 2 距离 2、自己 0 ⇒ 候选恰 {1, 3}。"""
    state = build_state(
        pack=PACK,
        seats=_four_seats(seat0_hand=(SHORTAGE_CARD,)),
        deck=_deck_excluding(SHORTAGE_CARD),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    assert check_targets(state, 0, state.card(SHORTAGE_CARD)) == (1, 3), (
        "只能对其他角色、距离 1 以内"
    )
    offered = [
        action.targets
        for action in legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, UseCard) and action.card.card_id == SHORTAGE_CARD
    ]
    assert offered == [(1,), (3,)]
    assert (0,) not in offered and (2,) not in offered


def test_supply_shortage_cannot_stack_a_same_named_card_in_the_same_judge_zone():
    """同名守卫（成对反例）：目标判定区已有兵粮 ⇒ 他不再是合法目标；别的目标照常。"""
    state = build_state(
        pack=PACK,
        seats=_four_seats(
            seat0_hand=(SHORTAGE_CARD,), judge1=(SHORTAGE[1],)
        ),
        deck=_deck_excluding(SHORTAGE_CARD, SHORTAGE[1]),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    assert check_targets(state, 0, state.card(SHORTAGE_CARD)) == (3,), (
        "座位 1 判定区已有同名的【兵粮寸断】⇒ 不能叠"
    )


# ---------------------------------------------------------------------------
# 2. 使用：置于目标判定区，判定推迟到目标的判定阶段
# ---------------------------------------------------------------------------


def test_using_supply_shortage_places_it_in_the_target_judge_zone_without_a_judgment():
    state = build_state(
        pack=PACK,
        seats=_four_seats(seat0_hand=(SHORTAGE_CARD,)),
        deck=_deck_excluding(SHORTAGE_CARD),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1)
    seats = [_Passive(play=SHORTAGE_CARD), _Passive(), _Passive(), _Passive()]
    run_turns(engine, seats, turns=1)

    placement = [
        record
        for record in _moves(engine.records, reason=MoveReason.PUT)
        if record.to_zone.zone is Zone.JUDGE
    ]
    assert [(record.to_zone.seat, record.owner) for record in placement] == [(1, 0)]
    assert not [
        timing for timing in _timings(engine.records) if timing.value.startswith("JUDGE_")
    ], (
        "使用当时不判定：L5 推迟到目标的判定阶段"
    )


# ---------------------------------------------------------------------------
# 3. 判定后果：非梅花 ⇒ 跳过摸牌阶段；梅花 ⇒ 照常摸牌
# ---------------------------------------------------------------------------


def _judge_engine(*, deck: tuple[int, ...], judge: tuple[int, ...] = (SHORTAGE_CARD,)):
    """座位 0 的判定阶段带一张【兵粮寸断】；``deck`` 的第一张就是判定牌。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, judge=judge), SeatSpec(hp=4)],
        deck=deck,
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [_Passive(), _Passive()], turns=1)
    return engine


def test_supply_shortage_with_a_non_club_judgment_skips_the_draw_phase():
    engine = _judge_engine(deck=(NOT_CLUB, 20, 21, 22))
    turn = _first_turn(engine)
    timings = _timings(turn)

    assert Timing.PHASE_DRAW_START not in timings, "跳过摸牌阶段：边界也不发"
    assert Timing.PHASE_DRAW not in timings, "连阶段体一并越过"
    assert Timing.PHASE_DRAW_END not in timings
    assert Timing.PHASE_PLAY_START in timings, "只跳过摸牌阶段，出牌阶段照常"
    assert engine.state.player(0).hand == [], "一张牌都没摸"
    assert not [
        record
        for record in _moves(turn, reason=MoveReason.DRAW)
        if record.to_zone.seat == 0
    ]
    assert [
        record
        for record in _moves(turn, reason=MoveReason.USE)
        if SHORTAGE_CARD in {card.card_id for card in record.cards}
    ], "判定后结算完进弃牌堆"


def test_supply_shortage_with_a_club_judgment_draws_normally():
    engine = _judge_engine(deck=(CLUB, 20, 21, 22))
    turn = _first_turn(engine)
    timings = _timings(turn)

    assert Timing.PHASE_DRAW_START in timings
    assert Timing.PHASE_DRAW in timings
    assert Timing.PHASE_DRAW_END in timings
    assert Timing.PHASE_PLAY_START in timings
    assert len(engine.state.player(0).hand) == 2, "梅花 ⇒ 照常摸两张"
    assert [
        record
        for record in _moves(turn, reason=MoveReason.USE)
        if SHORTAGE_CARD in {card.card_id for card in record.cards}
    ], "判定后结算完进弃牌堆"


def test_supply_shortage_does_not_forward_to_the_next_judge_zone():
    """结算后进弃牌堆（与【乐不思蜀】同姿态），不学【闪电】顺延。"""
    engine = _judge_engine(deck=(NOT_CLUB, 20, 21, 22))

    assert all(SHORTAGE_CARD not in player.judge for player in engine.state.players)
    turn = _first_turn(engine)
    discarded = [
        record
        for record in _moves(turn, reason=MoveReason.USE)
        if SHORTAGE_CARD in {card.card_id for card in record.cards}
    ]
    assert len(discarded) == 1
    assert discarded[0].to_zone.zone is Zone.DISCARD
    assert discarded[0].to_zone.seat is None, "延时锦囊的弃牌堆移动无座位坐标"


# ---------------------------------------------------------------------------
# 4. 判定前的无懈窗口（约定 B：在判定阶段的那张结算帧上）
# ---------------------------------------------------------------------------


def test_supply_shortage_can_be_nullified_before_the_judgment():
    nullify = NULLIFY[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, judge=(SHORTAGE_CARD,)),
            SeatSpec(hp=4, hand=(nullify,)),
        ],
        deck=_deck_excluding(SHORTAGE_CARD, nullify),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [_Passive(), _Nullifier(nullify)], turns=1)
    turn = _first_turn(engine)

    assert not [timing for timing in _timings(turn) if timing.value.startswith("JUDGE_")], (
        "被无懈 ⇒ 不判定"
    )
    assert [
        record
        for record in _moves(turn, reason=MoveReason.USE)
        if SHORTAGE_CARD in {card.card_id for card in record.cards}
    ], "被无懈的延时锦囊进弃牌堆"
    assert Timing.PHASE_DRAW_START in _timings(turn), "没判定 ⇒ 不跳摸牌阶段"
    assert len(engine.state.player(0).hand) == 2, "照常摸两张"
