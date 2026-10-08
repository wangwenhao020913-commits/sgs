"""询问、候选、行动与参与者视角（票 05 / 票 07 的管道半边）。

落点 = 库门面产出的 ``query`` 记录：候选**内联**、唯一 / 空候选仍发询问、
排序键稳定、视角只露自己的手牌。
"""

from __future__ import annotations

from itertools import combinations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import DiscardQuery, PlayPhaseQuery, PlayerView
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state

PACK = load_content_pack()
HAND_A = (40, 41, 42, 43)
HAND_B = (46, 47, 48, 49)
DECK = (0, 1)
HAND_A_AFTER_DRAW = HAND_A + DECK


def _run(*, hand_a=HAND_A, hand_b=HAND_B, hp_a=4, deck=DECK):
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=hp_a, hand=hand_a),
            SeatSpec(hp=4, hand=hand_b),
        ],
        deck=deck,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    engine.run([RecordingPlayer(), RecordingPlayer()])
    return engine


def _queries(engine, kind):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def test_play_phase_still_asks_when_the_only_candidate_is_end_phase():
    engine = _run()
    queries = _queries(engine, "PLAY_PHASE")

    assert queries, "出牌阶段每回合都要发询问，哪怕没有可用牌"
    first = queries[0]
    assert isinstance(first, PlayPhaseQuery)
    assert [action.kind for action in first.legal_actions] == ["EndPhase"]
    assert first.timing is Timing.PHASE_PLAY_IDLE


def test_candidates_carry_the_answers_seq_of_their_own_query():
    engine = _run()

    for record in engine.records:
        if record.kind != "query":
            continue
        for action in record.query.legal_actions:
            assert action.answers_seq == record.query.seq


def test_discard_candidates_are_all_subsets_of_the_required_size_in_stable_order():
    engine = _run()
    query = _queries(engine, "DISCARD")[0]

    assert isinstance(query, DiscardQuery)
    assert query.count == len(HAND_A_AFTER_DRAW) - 4
    expected = sorted(combinations(HAND_A_AFTER_DRAW, query.count))
    got = [tuple(card.card_id for card in action.cards) for action in query.legal_actions]
    assert got == expected
    assert all(action.kind == "DiscardCards" for action in query.legal_actions)


def test_no_discard_query_when_the_hand_is_within_the_limit():
    # 起始 2 张，摸 2 张后恰等于体力值 4 ⇒ 弃牌阶段无需弃牌。
    engine = _run(hand_a=(40, 41))

    assert _queries(engine, "DISCARD") == []


def test_player_view_exposes_only_public_information_and_own_hand():
    engine = _run()
    view = _queries(engine, "PLAY_PHASE")[0].view

    assert isinstance(view, PlayerView)
    assert tuple(card.card_id for card in view.self_hand) == HAND_A_AFTER_DRAW
    assert all(card.card_id not in HAND_B for card in view.self_hand)
    assert [player.hand_count for player in view.players] == [
        len(HAND_A_AFTER_DRAW),
        len(HAND_B),
    ]
    assert view.deck_count == 0, "牌堆只给剩余数量，不给顺序"
    assert view.discard == ()
    assert view.turn_seat == 0
    assert "opponent_hand" not in PlayerView.model_fields


def test_recording_player_saw_the_same_candidates_that_the_journal_recorded():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=HAND_A), SeatSpec(hp=4, hand=HAND_B)],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    players = [RecordingPlayer(), RecordingPlayer()]
    engine.run(players)

    assert players[0].seen[0].legal_actions == _queries(engine, "PLAY_PHASE")[0].legal_actions
