"""重洗与牌堆耗尽可观测（规则 10.4；票 08 §4–§6 / 票 23）。

落点 = 库门面（主缝）：``Engine.new_game`` / ``Engine.from_state`` 产出的记录流与活状态。
重洗是"需要摸牌而牌堆为空"的前置规则，故断言都落在 journal 的 ``deck_shuffle`` 记录、
它后面的 ``card_move``，以及"短摸 ⟺ 两堆皆空"上——不碰 ``draw_from_deck`` 的内部。
"""

from __future__ import annotations

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, RandomSource, dump_journal
from sgs.state import MoveReason, Phase, Zone

from support import RecordingPlayer, SeatSpec, build_state, run_turns, run_until

PACK = load_content_pack()
OLD_DECK = (0,)
DISCARD_PILE = (1, 2, 3)
"""牌堆只剩 1 张、弃牌堆 3 张：第一回合要摸 2 张，必然跨过一次重洗。"""


class _QuietChoice:
    """确定性应答：能结束阶段就结束、能放弃就放弃，否则取第一个候选。

    ``noise`` > 0 时每次应答前先消耗若干 ``ai`` 子流随机数，用来冒充"另一种 AI 实现"；
    两种实现的**行动完全相同**，只有随机消耗不同（票 23 的独立性判据）。
    """

    def __init__(self, noise: int = 0) -> None:
        self._noise = noise
        self._rng = RandomSource(11).substream("ai")

    def respond(self, query):
        for _ in range(self._noise):
            self._rng.randbelow(1000)
        for candidate in query.legal_actions:
            if candidate.kind == "EndPhase":
                return candidate
        for candidate in query.legal_actions:
            if candidate.kind == "Pass":
                return candidate
        return query.legal_actions[0]


def _position(*, deck=OLD_DECK, discard=DISCARD_PILE):
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hand=(40, 41, 42, 43)),
            SeatSpec(hp=4, hand=(46, 47, 48, 49)),
        ],
        deck=deck,
        discard=discard,
        turn_seat=0,
        phase=Phase.PREPARE,
    )


def _deltas(engine, delta_type):
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def _reshuffles(engine):
    return [record for record in _deltas(engine, "deck_shuffle") if record.from_zone is not None]


def _first_draw(engine):
    return next(record for record in _deltas(engine, "card_move") if record.reason is MoveReason.DRAW)


def _passive_turns(engine, *, turns: int):
    return run_turns(engine, [RecordingPlayer(), RecordingPlayer()], turns=turns)


# ---------------------------------------------------------------------------
# 初次洗牌：记录流第 1 条 + 无帧哨兵（票 08 §5）
# ---------------------------------------------------------------------------


def test_the_opening_shuffle_is_the_first_record_and_belongs_to_the_no_frame_sentinel():
    engine = Engine.new_game(seed=7, rule_config=RuleConfig(), pack=PACK)

    assert engine.records[0].kind == "header"
    shuffle = engine.records[1]
    assert shuffle.kind == "delta" and shuffle.delta_type == "deck_shuffle"
    assert shuffle.seq == 1, "seq = 1 必是初次洗牌"
    assert (shuffle.frame_id, shuffle.depth) == (0, 0), "无帧哨兵 = 引擎初始化"
    assert shuffle.from_zone is None, "初次洗牌不来自任何区域"
    assert shuffle.to_zone.zone is Zone.DECK
    assert len(shuffle.cards) == 53


# ---------------------------------------------------------------------------
# 重洗可观测（票 12 场景 11）
# ---------------------------------------------------------------------------


def test_a_reshuffle_leaves_exactly_one_deck_shuffle_delta_from_discard_to_deck():
    engine = _passive_turns(Engine.from_state(_position(), seed=1), turns=1)

    reshuffles = _reshuffles(engine)
    assert len(reshuffles) == 1, "一次摸牌最多重洗一次，且这一回合必然发生"
    shuffle = reshuffles[0]
    assert shuffle.from_zone.zone is Zone.DISCARD and shuffle.to_zone.zone is Zone.DECK
    assert (shuffle.frame_id, shuffle.depth) == (0, 0), "摸牌阶段没有结算帧，走无帧哨兵"
    assert sorted(card.card_id for card in shuffle.cards) == sorted(DISCARD_PILE), (
        "记录的是洗后的新牌堆：弃牌堆那三张的一个置换"
    )


def test_a_reshuffle_does_not_also_emit_a_card_move_out_of_the_discard_pile():
    engine = _passive_turns(Engine.from_state(_position(), seed=1), turns=3)

    assert _reshuffles(engine), "这一局面必然重洗"
    assert not [
        record
        for record in _deltas(engine, "card_move")
        if record.from_zone.zone is Zone.DISCARD and record.to_zone.zone is Zone.DECK
    ], "弃牌堆→牌堆由 deck_shuffle 承载，不得另发 card_move"


def test_a_draw_tops_up_across_the_boundary_and_the_shuffle_records_the_new_deck():
    engine = Engine.from_state(_position(), seed=1)
    run_until(
        engine,
        [RecordingPlayer(), RecordingPlayer()],
        lambda current: bool(_reshuffles(current)),
    )

    shuffle = _reshuffles(engine)[0]
    draw = _first_draw(engine)

    assert [card.card_id for card in draw.cards] == [
        OLD_DECK[0],
        shuffle.cards[0].card_id,
    ], "先取旧牌堆剩下的那张，再从重洗后的新牌堆补足"
    assert len(draw.cards) == 2, "要 2 张、牌堆只剩 1 张也能自然补足"
    assert engine.state.deck == [card.card_id for card in shuffle.cards[1:]], (
        "重洗记录就是新牌堆：摸走继续要的那张后，剩下的正是它的尾巴"
    )


# ---------------------------------------------------------------------------
# 短摸 ⟺ 两堆皆空（票 08 §6）
# ---------------------------------------------------------------------------


def test_a_short_draw_means_both_zones_are_empty_and_ends_in_a_draw():
    engine = Engine.from_state(_position(deck=(0,), discard=()), seed=1)
    engine.run([RecordingPlayer(), RecordingPlayer()])

    tail = engine.records[-1]
    assert tail.kind == "game_end" and tail.result == "draw" and tail.winner is None
    assert tail.reason == "both_decks_empty"
    assert not _reshuffles(engine), "弃牌堆为空 ⇒ 没有可重洗的牌"
    assert tail.check_seq == _deltas(engine, "card_move")[-1].seq, "检测点 = 那次短摸的 card_move"


def test_a_nonempty_discard_pile_prevents_the_short_draw():
    engine = _passive_turns(Engine.from_state(_position(), seed=1), turns=1)

    assert not [record for record in engine.records if record.kind == "game_end"]
    assert len(_first_draw(engine).cards) == 2, "弃牌堆非空 ⇒ 重洗补足，不触发平局"


# ---------------------------------------------------------------------------
# 洗牌与随机 AI 走独立子流（票 08 §3）
# ---------------------------------------------------------------------------


def test_swapping_the_ai_implementation_does_not_move_the_shuffle_stream():
    def play(noise: int):
        engine = Engine.from_state(_position(), seed=42)
        run_turns(engine, [_QuietChoice(noise), _QuietChoice(noise)], turns=3)
        return engine

    quiet = play(0)
    noisy = play(7)

    assert dump_journal(quiet.records) == dump_journal(noisy.records), (
        "换掉 AI 的随机消耗，洗牌取到的数与整条记录流都不变"
    )
    assert _reshuffles(quiet), "这一局面必然重洗"
    assert [card.card_id for card in _reshuffles(quiet)[0].cards] == [
        card.card_id for card in _reshuffles(noisy)[0].cards
    ]


def test_the_same_seed_replays_every_reshuffle_byte_for_byte():
    def play() -> str:
        engine = Engine.from_state(_position(), seed=2026)
        run_turns(engine, [_QuietChoice(), _QuietChoice()], turns=4)
        return dump_journal(engine.records)

    first = play()

    assert first == play(), "同种子下每一次重洗都逐字节可复现"
    assert "deck_shuffle" in first
