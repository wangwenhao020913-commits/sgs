"""回合环与最短牌堆的平局路径（票 18 的主缝验收）。

落点 = 库门面：低层入口 ``Engine.from_state``（人为指定局面）+ 唯一终局入口。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import MoveReason, Phase
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state

PACK = load_content_pack()


def _run(state, seed: int = 1):
    engine = Engine.from_state(state, seed=seed)
    engine.run([RecordingPlayer(), RecordingPlayer()])
    return engine


def _timings(engine, *, after: int = 0, before: int | None = None):
    values = [
        record.timing
        for record in engine.records
        if record.kind == "timing" and record.seq > after and (before is None or record.seq < before)
    ]
    return values


def _small_position(*, deck, discard=(), hand_a=(), hand_b=(), hp_a=4, hp_b=4):
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hp=hp_a, hand=hand_a), SeatSpec(hp=hp_b, hand=hand_b)],
        deck=deck,
        discard=discard,
        turn_seat=0,
        phase=Phase.PREPARE,
    )


def test_a_turn_walks_every_phase_in_order_and_skips_intervals():
    engine = _run(_small_position(deck=tuple(range(10)), hand_a=(40, 41, 42, 43)))
    first_turn = _timings(engine, before=_first_turn_after_end(engine))

    assert first_turn == [
        Timing.GAME_START,
        Timing.TURN_BEFORE_START,
        Timing.TURN_START,
        Timing.PHASE_PREPARE,
        Timing.PHASE_JUDGE,
        Timing.PHASE_DRAW_START,
        Timing.PHASE_DRAW,
        Timing.PHASE_DRAW_END,
        Timing.PHASE_PLAY_START,
        Timing.PHASE_PLAY_IDLE,
        Timing.PHASE_PLAY_END,
        Timing.PHASE_DISCARD_START,
        Timing.PHASE_DISCARD,
        Timing.PHASE_DISCARD_END,
        Timing.PHASE_FINISH,
        Timing.TURN_END,
        Timing.TURN_AFTER_END,
    ]
    assert not [timing for timing in first_turn if timing.value.startswith("INTERVAL_")]


def test_empty_phases_emit_only_their_body():
    engine = _run(_small_position(deck=tuple(range(10)), hand_a=(40, 41, 42, 43)))
    emitted = set(_timings(engine))

    for absent in (
        Timing.PHASE_PREPARE_START,
        Timing.PHASE_PREPARE_END,
        Timing.PHASE_JUDGE_START,
        Timing.PHASE_JUDGE_END,
        Timing.PHASE_FINISH_START,
        Timing.PHASE_FINISH_END,
    ):
        assert absent not in emitted


def test_discard_phase_discards_down_to_current_hp():
    engine = _run(_small_position(deck=(0, 1), hand_a=(40, 41, 42, 43)))

    discards = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DISCARD
    ]
    assert len(discards) == 1, "这一局只应有一次弃牌操作"
    assert len(discards[0].cards) == 2, "手牌 6、体力 4 ⇒ 弃 2 张"
    assert discards[0].from_zone.value == "hand"
    assert discards[0].to_zone.value == "discard"
    assert len(engine.state.player(0).hand) == engine.state.player(0).hp


def test_short_draw_ends_the_game_at_the_single_entry_and_game_end_is_last():
    engine = _run(_small_position(deck=(0,)))
    tail = engine.records[-1]

    assert tail.kind == "game_end"
    assert tail.result == "draw"
    assert tail.winner is None
    assert tail.reason == "both_decks_empty"
    assert tail.check_seq == _short_draw_move_seq(engine)
    assert not [record for record in engine.records if record.kind == "frame"], (
        "平局路径没有结算帧在飞，故 DISCARD_FRAME 预期 0 条"
    )
    assert engine.finished


def test_deck_exhausted_with_cards_in_discard_reports_the_real_reason():
    engine = _run(_small_position(deck=(0,), discard=(1, 2, 3)))
    tail = engine.records[-1]

    assert tail.reason == "deck_exhausted"
    assert tail.check_seq == _short_draw_move_seq(engine)


def test_an_ample_draw_does_not_end_the_game():
    engine = _run(_small_position(deck=tuple(range(10)), hand_a=(40, 41, 42, 43)))
    first_draw = next(
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.PHASE_DRAW
    )

    assert engine.records[-1].kind == "game_end", "这局最终会因牌堆耗尽收局"
    assert engine.records[-1].check_seq > first_draw.seq


def test_the_game_starts_with_the_configured_first_seat():
    state = _small_position(deck=tuple(range(10)))
    state.rule_config = state.rule_config.model_copy(update={"first_seat": 1})

    engine = _run(state)

    first_turn = next(
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_START
    )
    assert first_turn.actor == 1


def _first_turn_after_end(engine) -> int:
    """第一个回合结束后的下一条记录序号（用作"本回合"的右开区间）。"""
    for record in engine.records:
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END:
            return record.seq + 1
    raise AssertionError("这一局没有走完一个回合")


def _short_draw_move_seq(engine) -> int:
    moves = [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "card_move"
    ]
    return moves[-1].seq
