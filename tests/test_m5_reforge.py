"""M5-03：重铸（`Reforge` = 独立 Action，不是 `UseCard` 的变体）。

落点 = 外部缝：``legal_actions_for`` 的候选面 + ``Engine.from_state`` 的人为局面 +
记录流（``card_move`` 的 ``reason`` / ``MOVE_AFTER`` 时机 / 摸牌）。

规格 §5.1.3 / D6：

- **仅出牌阶段**可主动重铸；发动者 = 牌的拥有者；
- 结算 = 弃置这张牌 + 摸一张牌；不能被无懈、无次数限制、**不算使用锦囊**；
- ``MoveReason.REFORGE`` **算失去、不算弃置**：``MOVE_AFTER``（连营一类）照常触发，
  ``card_move.reason`` 写 REFORGE 而不是 DISCARD。
"""

from __future__ import annotations

import pytest

from sgs.data import RuleConfig, load_content_pack
from sgs.effects import legal_actions_for
from sgs.engine import Engine, IllegalActionError
from sgs.state import (
    ActivateSkill,
    EndPhase,
    MoveReason,
    Pass,
    QueryKind,
    Reforge,
    UseCard,
    parse_action,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_ex_160")

IRON_CHAIN = cards_of_type(PACK, "iron_chain")
PEACH = cards_of_type(PACK, "peach")
SLASH = cards_of_type(PACK, "slash")
DODGE = cards_of_type(PACK, "dodge")


def _deck_excluding(*cards: int) -> tuple[int, ...]:
    excluded = set(cards)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _deltas(engine, delta_type):
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def _minimal(query):
    """最小合法应答：响应放弃 / 出牌阶段结束 / 其余取第一个候选。"""
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    if query.kind == "PLAY_PHASE":
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    return query.legal_actions[0]


def _reforge_once(query):
    """出牌阶段：挑第一条 ``Reforge``；挑不到就结束阶段。"""
    if query.kind == QueryKind.PLAY_PHASE:
        for action in query.legal_actions:
            if isinstance(action, Reforge):
                return action
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    return None


class _Responder:
    """``handler`` 优先，其次"能重铸就重铸"，最后退回 :func:`_minimal`。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        action = _reforge_once(query)
        if action is not None:
            return action
        return _minimal(query)


def _advance(engine, players, stop) -> Engine:
    """逐步推进到 ``stop(engine)`` 成立（与 ``tests/test_hero_move_after.py`` 同形）。"""
    for _ in range(20_000):
        if stop(engine):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _play_phase_candidates(state, seat: int):
    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


def _state(hand=(IRON_CHAIN[0], PEACH[0]), *, turn_seat: int = 0):
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand), SeatSpec(hand=(DODGE[0],))],
        deck=_deck_excluding(*hand, DODGE[0]),
        turn_seat=turn_seat,
        rule_config=RuleConfig(player_count=2),
    )


def _seed_hand(engine, seat: int, cards: tuple[int, ...]) -> None:
    """把 ``seat`` 的手牌**换成** ``cards``，且不动牌数（拿走的牌回牌堆、给出的牌从牌堆取）。

    时点精确的局面（"手里就这一张"）必须这样起步，否则"掉最后一张"不成立；同时保持
    牌守恒，好让同一局能断言"重铸只是搬牌 + 摸牌"。
    """
    state = engine.state
    player = state.player(seat)
    for card_id in list(player.hand):
        state.deck.append(card_id)
    player.hand.clear()
    for card_id in cards:
        state.deck.remove(card_id)
        player.hand.append(card_id)


def _reforge_engine(handler=None, *, hand=(IRON_CHAIN[0],), hero=None, moves=1):
    """座位 0 跑到出牌阶段开始、**种下** ``hand`` 后重铸；停在 MOVE 帧整个跑完时。

    手牌要种在出牌阶段开始（摸牌阶段之后）：重铸的"掉最后一张手牌"才是真的只有一张
    （与 ``tests/test_hero_move_after.py::_own_play_phase`` 同一姿态）。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=hero), SeatSpec()],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=1)
    _advance(
        engine,
        [_Responder(), _Responder()],
        lambda engine: any(
            record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
            for record in engine.records
        ),
    )
    _seed_hand(engine, 0, hand)

    player = _Responder(handler or _reforge_once)
    _advance(
        engine,
        [player, _Responder()],
        lambda engine: sum(
            1
            for record in engine.records
            if record.kind == "delta"
            and record.delta_type == "card_move"
            and record.reason is MoveReason.REFORGE
        )
        >= moves
        and not any(frame.kind.value == "MOVE" for frame in engine.stack.frames),
    )
    return engine


# ---------------------------------------------------------------------------
# 候选面：只有可重铸的牌在出牌阶段给出 Reforge
# ---------------------------------------------------------------------------


def test_reforge_is_offered_only_in_the_play_phase_and_only_for_reforgeable_cards():
    """铁索可重铸（出牌阶段一条 ``Reforge``），【桃】不行；别的询问种类一条都不给。"""
    state = _state()

    playing = [
        action for action in _play_phase_candidates(state, 0) if isinstance(action, Reforge)
    ]
    assert [action.card.card_id for action in playing] == [IRON_CHAIN[0]], (
        "只有行为表声明可重铸的牌给出 Reforge；【桃】不可重铸"
    )
    assert all(isinstance(action, Reforge) and not isinstance(action, UseCard) for action in playing), (
        "重铸必须是独立构造子，不是 UseCard 的变体"
    )
    candidates = _play_phase_candidates(state, 0)
    assert candidates[-1].kind == "EndPhase", "结束阶段仍排最后（重铸不是兜底候选）"
    assert "Reforge" in [action.kind for action in candidates[:-1]]

    for kind in (QueryKind.DODGE, QueryKind.DISCARD):
        kinds = [
            action.kind
            for action in legal_actions_for(state, 1, kind, answers_seq=1)
        ]
        assert "Reforge" not in kinds, f"{kind.value} 询问不该给出重铸候选"


def test_reforge_can_only_be_initiated_by_the_cards_owner():
    """重铸的发动者 = 牌的拥有者：别人的铁索不是本座位的候选。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(PEACH[0],)), SeatSpec(hand=(IRON_CHAIN[0],))],
        deck=_deck_excluding(PEACH[0], IRON_CHAIN[0]),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=1)

    query = _advance(
        engine,
        [_Responder(), _Responder()],
        lambda engine: engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE,
    ).pending
    assert query.actor == 0
    assert not any(
        isinstance(action, Reforge) and action.card.card_id == IRON_CHAIN[0]
        for action in query.legal_actions
    ), "手里没有铁索的座位不该拿到它的重铸候选"

    with pytest.raises(IllegalActionError):
        engine.submit(Reforge(card=state.card(IRON_CHAIN[0]), answers_seq=query.seq))


# ---------------------------------------------------------------------------
# 结算：弃置 + 摸一张，写 REFORGE 而不是 DISCARD
# ---------------------------------------------------------------------------


def test_reforge_moves_the_card_to_the_discard_pile_and_draws_one():
    engine = _reforge_engine()

    moves = _deltas(engine, "card_move")
    reforge_moves = [record for record in moves if record.reason is MoveReason.REFORGE]
    draws = [record for record in moves if record.reason is MoveReason.DRAW]

    assert len(reforge_moves) == 1, "重铸 = 一次牌移动（手牌 → 弃牌堆）"
    assert [card.card_id for card in reforge_moves[0].cards] == [IRON_CHAIN[0]]
    assert reforge_moves[0].from_zone.zone.value == "hand"
    assert reforge_moves[0].to_zone.zone.value == "discard"
    assert reforge_moves[0].owner == 0, "重铸的归属 = 发动者"
    assert len(draws[-1].cards) == 1, "重铸摸一张（摸牌阶段那次摸两张在它之前）"
    assert draws[-1].to_zone.zone.value == "hand" and draws[-1].to_zone.seat == 0
    assert draws[-1].seq > reforge_moves[0].seq, "先弃置、再摸牌"

    assert all(record.reason is not MoveReason.DISCARD for record in moves), (
        "事件流写 REFORGE，不写 DISCARD"
    )
    assert IRON_CHAIN[0] in engine.state.discard
    hand = engine.state.player(0).hand
    assert len(hand) == 1, "手牌数守恒：种下的一张弃置后又摸回一张"
    assert draws[-1].cards[0].card_id in hand
    assert _cards_in_play(engine) == len(PACK.deck.cards), "牌数守恒（重铸只是搬牌 + 摸牌）"


def _cards_in_play(engine) -> int:
    state = engine.state
    return (
        len(state.deck)
        + len(state.discard)
        + len(state.processing)
        + sum(len(player.hand) + len(player.equip) + len(player.judge) for player in state.players)
    )


def test_reforge_is_not_a_use_and_does_not_touch_the_use_count():
    """不算使用锦囊：没有 USE 帧、没有「使用」类时机、不进 use count。"""
    engine = _reforge_engine()

    frame_kinds = [
        record.frame_kind.value
        for record in engine.records
        if record.kind == "frame" and record.op == "push"
    ]
    assert "USE" not in frame_kinds, "重铸不走使用结算栈"
    assert not any(
        record.kind == "timing"
        and record.timing
        in (
            Timing.USE_CHOOSE_TARGET,
            Timing.USE_ON_USE,
            Timing.USE_AFTER_DESIGNATE_TARGET,
            Timing.PLAY_DECLARE,
        )
        for record in engine.records
    ), "重铸不触发「使用锦囊」类时机"
    assert engine.state.player(0).card_use_counts == {}, "重铸不消耗 use count"


def test_reforge_does_not_open_a_nullify_window():
    engine = _reforge_engine()

    respond_queries = [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.RESPOND
    ]
    assert all(query.cause.value != "NULLIFY" for query in respond_queries), (
        "重铸不能被无懈：结算里不开无懈窗口"
    )


# ---------------------------------------------------------------------------
# MoveReason.REFORGE：算「失去」不算「弃置」（连营照常触发）
# ---------------------------------------------------------------------------


def test_reforge_is_a_loss_not_a_discard_for_move_after_triggers():
    """陆逊【连营】：失去最后一张手牌 ⇒ 摸一张；重铸写 REFORGE ⇒ 仍算失去。

    这是 §5.1.3「不算弃置、算失去」的机械判据：``MOVE_AFTER`` 时机出现、且它对应的
    那条移动的 ``reason`` 是 REFORGE。引擎里没有「弃置触发」的第二条通道（本票不新增）：
    弃置类效果将来若出现，读的必须是同一个 ``reason`` 字段，不能自带豁免。
    """

    def activate(query):
        if query.kind == "SKILL_ACTIVATION":
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill):
                    return action
        return None

    engine = _reforge_engine(handler=activate, hand=(IRON_CHAIN[0],), hero="luxun")

    records = list(engine.records)
    positions = [
        index
        for index, record in enumerate(records)
        if record.kind == "timing"
        and record.timing is Timing.MOVE_AFTER
        and [card.card_id for card in record.cards] == [IRON_CHAIN[0]]
    ]
    assert len(positions) == 1, "重铸的弃置照常派发「失去牌后」"
    after = records[positions[0] + 1 :]
    draws = [
        record
        for record in after
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DRAW
        and record.to_zone.zone.value == "hand"
        and record.to_zone.seat == 0
    ]
    assert [len(record.cards) for record in draws] == [1, 1], (
        "连营摸一张 + 重铸摸一张（顺序 = 失去牌后先于重铸自己的摸牌）"
    )
    assert len(engine.state.player(0).hand) == 2
    assert all(
        record.reason is not MoveReason.DISCARD
        for record in _deltas(engine, "card_move")
    )


def test_the_reforge_action_round_trips_through_the_wire_shape():
    """``Reforge`` 的报文形状与其它 Action 同姿态：``card`` + ``answers_seq``。"""
    state = _state()
    action = Reforge(card=state.card(IRON_CHAIN[0]), answers_seq=7)

    payload = action.model_dump(mode="json")
    assert payload["kind"] == "Reforge"
    assert payload["answers_seq"] == 7
    assert payload["card"]["card_id"] == IRON_CHAIN[0]
    assert parse_action(payload) == action


def test_reforge_has_no_per_turn_limit():
    """无次数限制：同一出牌阶段连着重铸两张铁索都合法。"""
    engine = _reforge_engine(hand=(IRON_CHAIN[0], IRON_CHAIN[1]), moves=2)

    reforge_moves = [
        record for record in _deltas(engine, "card_move") if record.reason is MoveReason.REFORGE
    ]
    assert [[card.card_id for card in record.cards] for record in reforge_moves] == [
        [IRON_CHAIN[0]],
        [IRON_CHAIN[1]],
    ]
    assert engine.state.player(0).card_use_counts == {}, "重铸始终不是「使用」"
    reforge_draws = [
        record
        for record in _deltas(engine, "card_move")
        if record.reason is MoveReason.DRAW and record.seq > reforge_moves[0].seq
    ]
    assert [len(record.cards) for record in reforge_draws] == [1, 1], "两次重铸各摸一张"
    assert sorted(engine.state.player(0).hand) == sorted(
        card.card_id for record in reforge_draws for card in record.cards
    ), "两次重铸后手牌 = 摸回来的两张（弃置的两张已进弃牌堆）"


class _PlaySlash:
    """出牌阶段打一张【杀】向 1 号；其余最小应答。"""

    def __init__(self, card_id: int) -> None:
        self.card_id = card_id

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE:
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.card_id == self.card_id
                    and action.targets == (1,)
                ):
                    return action
        return _minimal(query)


def test_reforge_is_rejected_outside_the_play_phase():
    """重铸只对出牌阶段开放：响应窗口里交一条 Reforge（牌确实在手里）也会被拒。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH[0],)), SeatSpec(hand=(IRON_CHAIN[0],))],
        deck=_deck_excluding(SLASH[0], IRON_CHAIN[0]),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=1)
    query = _advance(
        engine,
        [_PlaySlash(SLASH[0]), _Responder()],
        lambda engine: engine.pending is not None
        and engine.pending.kind == QueryKind.DODGE,
    ).pending
    assert query.actor == 1

    with pytest.raises(IllegalActionError):
        engine.submit(Reforge(card=state.card(IRON_CHAIN[0]), answers_seq=query.seq))
