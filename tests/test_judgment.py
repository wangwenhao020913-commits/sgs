"""判定帧与延时锦囊（票 26）。

落点 = **库门面**（主缝）：``Engine.from_state`` 的人为局面 + 记录流断言；纯规则
（同名禁止 / 顺延目标 / 改判原语）从 ``sgs.effects`` / ``sgs.state`` 的公开面直接断言。

被测的票面行为（`issues/26-judgment-frame-and-delayed-tricks.md`）：

- 判定是真帧：取牌入处理区 → 改判窗口 → 结果 → 入弃牌堆之后调后果 op；
- 三个判定时机真 dispatch，记录以**被判定者**为 ``actor``、带得出**判定牌**；
- 判定取牌复用摸牌原语（沿用 ``shuffle`` 子流、按需重洗、不算摸牌、短摸判平局）；
- 判定阶段自循环、每轮重取判定区、``judged`` 守卫防死循环；
- 延时锦囊 LIFO、**按牌名禁同名**、判定牌归属被判定者；
- 改判机制建好不接效果（真 dispatch + 逆时针队列 + `代替`/`替换`原语，队列恒空）；
- 【乐不思蜀】非红桃 ⇒ 跳过出牌阶段；【闪电】♠2~9 ⇒ 3 点无来源雷电伤害后入弃牌堆，
  否则逆时针顺延；伤害带属性标签但不实现属性后果。

不测帧私有 ``data`` 本身：``subject`` / ``judge_card`` 由"记录的 ``actor`` / ``cards``"
与"伤害渠道牌 ≠ 判定牌"间接判决；``nature`` 例外，它按票面要求存在于伤害帧里，
只能经引擎的压栈出口观测（``_FrameSpy``）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import ContentPack, Suit, load_content_pack
from sgs.effects import (
    DamageNature,
    EffectContext,
    check_targets,
    delayed_settle_target,
    locate,
    substitute_judge_card,
    swap_judge_card,
)
from sgs.engine import Engine
from sgs.skills import rejudge_capable_seats
from sgs.state import (
    EndPhase,
    MoveReason,
    Phase,
    Query,
    QueryKind,
    UseCard,
    Zone,
    ZoneRef,
    rejudge_askers,
)
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, cards_of_type, run_turns

STANDARD = "standard_108"
BOTH_DECKS_EMPTY = "both_decks_empty"

PACK = load_content_pack(deck_profile=STANDARD)
INDULGENCE = cards_of_type(PACK, "indulgence")
LIGHTNING = cards_of_type(PACK, "lightning")


# ---------------------------------------------------------------------------
# 构造与读取助手
# ---------------------------------------------------------------------------


def _find_card(
    pack: ContentPack,
    *,
    suit: str | None = None,
    rank: int | None = None,
    spade_rank_in: tuple[int, int] | None = None,
    exclude: tuple[int, ...] = (),
) -> int:
    """按牌面事实找一个 ``card_id``：判定结果由测试显式指定，不依赖随机。"""
    for index, card in enumerate(pack.deck.cards):
        if index in exclude:
            continue
        if suit is not None and card.suit.value != suit:
            continue
        if rank is not None and card.rank != rank:
            continue
        if spade_rank_in is not None:
            low, high = spade_rank_in
            if card.suit is not Suit.SPADE or not low <= card.rank <= high:
                continue
        return index
    raise AssertionError("标准 108 张里找不到满足条件的牌")


NON_HEART = _find_card(PACK, suit="spade", rank=1, exclude=tuple(INDULGENCE))
HEART = _find_card(PACK, suit="heart", exclude=tuple(INDULGENCE))
SPADE_IN_RANGE = _find_card(PACK, spade_rank_in=(2, 9), exclude=tuple(INDULGENCE))


def _first_turn(engine: Engine) -> list[Any]:
    """第一个回合（座位 0，先手）的记录，直到该回合的 ``TURN_AFTER_END`` 为止。"""
    out: list[Any] = []
    for record in engine.records:
        out.append(record)
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END:
            break
    return out


def _timings(records: list[Any]) -> list[Timing]:
    return [record.timing for record in records if record.kind == "timing"]


def _moves(records: list[Any], *, reason: MoveReason | None = None):
    return [
        record
        for record in records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and (reason is None or record.reason is reason)
    ]


def _frames(records: list[Any], op: str | None = None):
    return [
        record
        for record in records
        if record.kind == "frame" and (op is None or record.op == op)
    ]


def _judge_engine(
    *,
    seat0: SeatSpec,
    seat1: SeatSpec = SeatSpec(),
    deck: tuple[int, ...] = (),
    discard: tuple[int, ...] = (),
    seed: int = 1,
    players: int = 2,
    spy: bool = False,
) -> Engine:
    """座位 0 的判定阶段带牌的构造局面；``seat0.judge`` 就是待结算的延时锦囊。"""
    seats = [seat0, seat1]
    for _ in range(players - 2):
        seats.append(SeatSpec())
    state = build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        discard=discard,
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine_cls = _FrameSpy if spy else Engine
    engine = engine_cls.from_state(state, seed=seed)
    run_turns(engine, [RecordingPlayer() for _ in seats], turns=1)
    return engine


class _FrameSpy(Engine):
    """记下每一次压栈的帧类型与载荷：``nature`` 这类**帧私有**事实只能这样观测。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.pushed: list[tuple[Any, dict[str, Any]]] = []

    def push_frame(self, kind, data, *, timing):  # type: ignore[no-untyped-def]
        frame = super().push_frame(kind, data, timing=timing)
        self.pushed.append((kind, dict(data)))
        return frame


class _UseTypePlayer:
    """在出牌阶段使用指定牌名的第一张候选；没有就结束阶段。"""

    def __init__(self, type_id: str) -> None:
        self._type = type_id

    def respond(self, query: Query):
        for candidate in query.legal_actions:
            if isinstance(candidate, UseCard) and candidate.card.type == self._type:
                return candidate
        for candidate in query.legal_actions:
            if isinstance(candidate, EndPhase):
                return candidate
        return query.legal_actions[0]


# ---------------------------------------------------------------------------
# 使用延时锦囊：L4 走完就置入判定区，L5 推迟到判定阶段
# ---------------------------------------------------------------------------


def test_using_a_delayed_trick_places_it_in_the_target_judge_zone_without_resolving_it():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(INDULGENCE[0],)), SeatSpec(hp=4)],
        deck=(20, 21, 22, 23),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [_UseTypePlayer("indulgence"), RecordingPlayer()], turns=1)
    turn = _first_turn(engine)

    placement = [
        record
        for record in _moves(turn, reason=MoveReason.PUT)
        if record.to_zone.zone is Zone.JUDGE
    ]
    use_push = _frames(turn, "push")[0]
    use_pop = [record for record in _frames(turn, "pop") if record.frame_kind.value == "USE"][0]
    between = turn[turn.index(use_push) : turn.index(use_pop)]

    assert [(record.to_zone.seat, record.owner) for record in placement] == [(1, 0)]
    assert [record.frame_kind.value for record in _frames(between)] == ["USE"], (
        "使用当时不压 RESOLVE：L5 归判定阶段"
    )
    assert not [timing for timing in _timings(turn) if timing.value.startswith("JUDGE_")]


# ---------------------------------------------------------------------------
# 判定是真帧：四步、三时机、subject / judge_card 分列
# ---------------------------------------------------------------------------


def test_the_judge_frame_is_a_real_frame_with_the_three_dispatched_timings():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0],)),
        deck=(NON_HEART, 20, 21, 22),
    )
    turn = _first_turn(engine)

    pushes = _frames(turn, "push")
    assert [record.frame_kind.value for record in pushes] == ["RESOLVE", "JUDGE"]
    assert [record.depth for record in pushes] == [1, 2], "判定帧嵌在 L5 的 RESOLVE 帧里"
    assert [record.frame_kind.value for record in _frames(turn, "pop")] == ["JUDGE", "RESOLVE"]

    judge_timings = [timing for timing in _timings(turn) if timing.value.startswith("JUDGE_")]
    assert judge_timings == [
        Timing.JUDGE_ON,
        Timing.JUDGE_CARD_BEFORE_EFFECT,
        Timing.JUDGE_CARD_AFTER_EFFECT,
    ]


def test_the_judgment_timings_act_for_the_subject_and_carry_the_judge_card():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0],)),
        deck=(NON_HEART, 20, 21, 22),
    )
    turn = _first_turn(engine)
    judge_card = engine.state.card(NON_HEART)

    judge_records = [
        record
        for record in turn
        if record.kind == "timing" and record.timing.value.startswith("JUDGE_")
    ]

    assert [record.actor for record in judge_records] == [0, 0, 0], "被判定者是行为者"
    assert [record.cards for record in judge_records] == [(judge_card,)] * 3
    assert all(record.source is None for record in judge_records), "判定无使用者"


def test_the_judgment_draw_moves_deck_to_processing_then_to_discard_owned_by_the_subject():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0],)),
        deck=(NON_HEART, 20, 21, 22),
    )
    turn = _first_turn(engine)
    judge_card = engine.state.card(NON_HEART)

    judgement_moves = _moves(turn, reason=MoveReason.JUDGE)

    assert [(record.from_zone.zone, record.to_zone.zone) for record in judgement_moves] == [
        (Zone.DECK, Zone.PROCESSING),
        (Zone.PROCESSING, Zone.DISCARD),
    ]
    assert [record.owner for record in judgement_moves] == [0, 0], "判定牌归属 = 被判定者"
    assert [record.cards for record in judgement_moves] == [(judge_card,)] * 2


def test_the_judgment_draw_reshuffles_when_the_deck_is_empty_and_is_not_a_draw():
    """判定取牌复用摸牌原语：按需重洗发一条 ``deck_shuffle``，但**不算摸牌**（reason ≠ DRAW）。"""
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0],)),
        discard=(30, 31, 32, 33, 34),
        seed=3,
    )
    turn = _first_turn(engine)

    shuffles = [
        record for record in turn if record.kind == "delta" and record.delta_type == "deck_shuffle"
    ]
    judge_on = next(
        record for record in turn if record.kind == "timing" and record.timing is Timing.JUDGE_ON
    )
    judge_card = judge_on.cards[0]
    judge_moves = [record for record in _moves(turn, reason=MoveReason.JUDGE) if judge_card in record.cards]

    assert [record.from_zone.zone for record in shuffles] == [Zone.DISCARD], "牌堆空 ⇒ 重洗弃牌堆"
    assert [(record.from_zone.zone, record.to_zone.zone) for record in judge_moves] == [
        (Zone.DECK, Zone.PROCESSING),
        (Zone.PROCESSING, Zone.DISCARD),
    ]
    assert all(judge_card not in record.cards for record in _moves(turn, reason=MoveReason.DRAW))


def test_a_short_judgment_draw_is_a_both_decks_empty_draw():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0],)),
        seed=5,
    )

    assert engine.finished
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert (tail.result, tail.reason) == ("draw", BOTH_DECKS_EMPTY)


# ---------------------------------------------------------------------------
# 判定区与判定阶段循环
# ---------------------------------------------------------------------------


def test_the_judge_zone_resolves_last_placed_first():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0], LIGHTNING[0])),
        deck=(NON_HEART, HEART, 20, 21, 22),
    )
    turn = _first_turn(engine)

    resolved = [
        record.cards[0].card_id
        for record in _moves(turn, reason=MoveReason.LOSE)
        if record.from_zone.zone is Zone.JUDGE
    ]

    assert resolved == [LIGHTNING[0], INDULGENCE[0]], "最后置入的【闪电】先结算"


def test_the_judged_guard_keeps_a_passed_lightning_from_looping_forever():
    """两名存活者各持一张【闪电】时，顺延只能落回自己判定区；``judged`` 守卫终止循环。"""
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(LIGHTNING[0],)),
        seat1=SeatSpec(hp=4, judge=(LIGHTNING[1],)),
        deck=(NON_HEART, 20, 21, 22),
    )
    turn = _first_turn(engine)

    back = [
        record
        for record in _moves(turn, reason=MoveReason.PUT)
        if record.to_zone.zone is Zone.JUDGE
    ]
    rejudged = [
        record
        for record in _moves(turn, reason=MoveReason.LOSE)
        if record.from_zone.zone is Zone.JUDGE and record.cards[0].card_id == LIGHTNING[0]
    ]

    assert [record.to_zone.seat for record in back] == [0], "全不合法 ⇒ 回到自己的判定区"
    assert len(rejudged) == 1, "刚判完的牌不得原地重判"


def test_a_judge_phase_without_delayed_cards_still_runs_empty():
    """回归网：判定区为空时阶段体照发、不产生任何 JUDGE_* 节点（M1 语义不变）。"""
    engine = _judge_engine(seat0=SeatSpec(hp=4), deck=(20, 21, 22))
    turn = _first_turn(engine)
    timings = _timings(turn)

    assert Timing.PHASE_JUDGE in timings
    assert not [timing for timing in timings if timing.value.startswith("JUDGE_")]


# ---------------------------------------------------------------------------
# 判定后果：【乐不思蜀】与【闪电】
# ---------------------------------------------------------------------------


def test_indulgence_skips_the_play_phase_when_the_judgment_is_not_hearts():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0],)),
        deck=(NON_HEART, 20, 21, 22),
    )
    turn = _first_turn(engine)
    timings = _timings(turn)

    assert Timing.PHASE_DISCARD_START in timings
    assert Timing.PHASE_PLAY_START not in timings, "跳过出牌阶段连阶段体一并越过"
    assert not [
        record
        for record in turn
        if record.kind == "query" and record.query.kind is QueryKind.PLAY_PHASE
    ]


def test_indulgence_leaves_the_play_phase_alone_when_the_judgment_is_hearts():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0],)),
        deck=(HEART, 20, 21, 22),
    )
    turn = _first_turn(engine)

    assert Timing.PHASE_PLAY_START in _timings(turn)


def test_lightning_hits_for_three_sourceless_thunder_damage_then_discards():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(LIGHTNING[0],)),
        deck=(SPADE_IN_RANGE, 20, 21, 22),
        spy=True,
    )
    assert isinstance(engine, _FrameSpy)
    turn = _first_turn(engine)

    damage_frames = [data for kind, data in engine.pushed if kind.value == "DAMAGE"]
    assert len(damage_frames) == 1
    damage = damage_frames[0]
    assert damage["source"] is None, "无来源"
    assert damage["amount"] == 3
    assert damage["nature"] is DamageNature.THUNDER, "带属性标签、不实现属性后果"
    assert damage["channel"].card_id == LIGHTNING[0], "渠道牌 = 延时锦囊本体"

    hp = [record for record in turn if record.kind == "delta" and record.delta_type == "hp_change"]
    assert [(record.seat, record.before, record.after) for record in hp] == [(0, 4, 1)]

    discard = [
        record
        for record in _moves(turn, reason=MoveReason.USE)
        if record.to_zone.zone is Zone.DISCARD
    ]
    assert [record.cards[0].card_id for record in discard] == [LIGHTNING[0]]
    damage_end = next(
        index
        for index, record in enumerate(turn)
        if record.kind == "timing" and record.timing is Timing.DAMAGE_END
    )
    discard_index = next(index for index, record in enumerate(turn) if record is discard[0])
    assert damage_end < discard_index, "伤害结算完才入弃牌堆"


def test_lightning_hit_that_kills_the_judged_seat_still_clears_the_processing_zone():
    """命中**把被判定者打死**时，收尾照样要跑：牌进弃牌堆，处理区不留它（W4-fix-4）。

    上一类形状（4 血吃 3 点、站着）走的是"判定循环下一轮自己续跑"，收尾自然发生；本用例
    打的是**循环被放弃**那一支：非主公在自己的回合里阵亡 ⇒ 引擎把剩余阶段整体跳过
    （:meth:`~sgs.engine.game.Engine._turn_is_over` 的回合结束分支），判定阶段循环**再也
    不会续跑**，而延时锦囊的 L5.6 收尾（命中入弃牌堆 / 未命中顺延）是**循环**做的 ⇒
    牌留在处理区，直到终局清栈才被顺带搬走（手动验收：闪电生效后效果区一直挂着它）。

    3 人局是这条用例的前提：死一人**不终局**，否则终局清栈会顺手把牌搬走，缺陷被盖住。
    阵亡者在记录流里是「先 `DEATH`、后收尾」——收尾仍须在 `DAMAGE_END` 之后。
    """
    engine = _judge_engine(
        seat0=SeatSpec(hp=3, judge=(LIGHTNING[0],)),
        seat1=SeatSpec(hp=4),
        deck=(SPADE_IN_RANGE, 20, 21, 22),
        players=3,
    )
    turn = _first_turn(engine)

    assert not engine.state.player(0).alive, "3 血吃 3 点雷电 ⇒ 被判定者阵亡（本用例的前提）"
    assert not engine.finished, "3 人局死一人不终局（终局清栈会顺手搬牌，测不到本缺陷）"
    assert LIGHTNING[0] not in engine.state.processing, "判定循环被放弃，收尾也得补做"
    assert LIGHTNING[0] in engine.state.discard, "命中后的去向仍是弃牌堆"

    discard = [
        record
        for record in _moves(turn, reason=MoveReason.USE)
        if record.to_zone.zone is Zone.DISCARD
    ]
    assert [record.cards[0].card_id for record in discard] == [LIGHTNING[0]]
    damage_end = next(
        index
        for index, record in enumerate(turn)
        if record.kind == "timing" and record.timing is Timing.DAMAGE_END
    )
    discard_index = next(index for index, record in enumerate(turn) if record is discard[0])
    turn_after_end = next(
        index
        for index, record in enumerate(turn)
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
    )
    assert damage_end < discard_index < turn_after_end, "伤害结算完、回合彻底结束前入弃牌堆"


def test_lightning_damage_channel_is_not_the_judge_card():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(LIGHTNING[0],)),
        deck=(SPADE_IN_RANGE, 20, 21, 22),
    )
    turn = _first_turn(engine)
    judge_card = engine.state.card(SPADE_IN_RANGE)

    damage_cards = {
        record.cards
        for record in turn
        if record.kind == "timing" and record.timing.value.startswith("DAMAGE_")
    }
    judge_cards = {
        record.cards
        for record in turn
        if record.kind == "timing" and record.timing.value.startswith("JUDGE_")
    }

    assert damage_cards == {(engine.state.card(LIGHTNING[0]),)}
    assert judge_cards == {(judge_card,)}
    assert damage_cards.isdisjoint(judge_cards)


def test_lightning_passes_counterclockwise_to_the_first_legal_judge_zone():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(LIGHTNING[0],)),
        seat1=SeatSpec(hp=4, judge=(LIGHTNING[1],)),
        deck=(NON_HEART, 20, 21, 22),
        players=3,
    )
    turn = _first_turn(engine)

    passes = [
        record
        for record in _moves(turn, reason=MoveReason.PUT)
        if record.to_zone.zone is Zone.JUDGE and record.to_zone.seat != 0
    ]

    assert [(record.to_zone.seat, record.owner) for record in passes] == [(2, None)]


# ---------------------------------------------------------------------------
# 判定区的合法性：按牌名禁同名
# ---------------------------------------------------------------------------


def test_a_delayed_trick_cannot_target_a_judge_zone_that_already_has_its_name():
    seat0 = SeatSpec(hp=4, hand=(INDULGENCE[1],))
    seat1 = SeatSpec(hp=4, judge=(INDULGENCE[0],))
    state = build_state(pack=PACK, seats=[seat0, seat1, SeatSpec()], turn_seat=0)
    card = state.card(INDULGENCE[1])

    assert check_targets(state, 0, card) == (2,), "已有同名【乐不思蜀】的座位不是合法目标"


def test_lightning_cannot_be_used_at_all_when_your_judge_zone_already_has_it():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(LIGHTNING[1],), judge=(LIGHTNING[0],)), SeatSpec()],
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(LIGHTNING[1])) == ()


# ---------------------------------------------------------------------------
# 改判：真 dispatch + 逆时针队列 + 代替 / 替换原语（机制建好、不接效果）
# ---------------------------------------------------------------------------


def test_the_rejudge_window_is_dispatched_without_asking_anything():
    engine = _judge_engine(
        seat0=SeatSpec(hp=4, judge=(INDULGENCE[0],)),
        deck=(NON_HEART, 20, 21, 22),
    )
    turn = _first_turn(engine)
    window = [
        index
        for index, record in enumerate(turn)
        if record.kind == "timing" and record.timing is Timing.JUDGE_CARD_BEFORE_EFFECT
    ]

    assert len(window) == 1
    assert not [
        record
        for record in turn
        if record.kind == "query" and record.query.timing is Timing.JUDGE_CARD_BEFORE_EFFECT
    ], "改判窗口不构造询问：M2 队列恒空、无日志节点（票 28 起判定阶段的结算另开无懈窗口）"


def test_the_rejudge_queue_is_counterclockwise_from_the_turn_seat_and_one_per_seat():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(), SeatSpec(alive=False), SeatSpec()],
        turn_seat=1,
    )

    assert rejudge_askers(state, capable=(3, 0, 1)) == (1, 3, 0), "逆时针、每人一次、按座位序"
    assert rejudge_askers(state, capable=(2,)) == (), "死者不在区间里"
    assert rejudge_capable_seats(state) == (), "无武将局面没有改判技能 ⇒ 队列恒空"


def test_the_substitute_primitive_puts_the_new_card_in_processing_and_discards_the_old():
    old = _find_card(PACK, suit="spade", rank=4)
    new = _find_card(PACK, suit="club", rank=4, exclude=(old,))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(new,)), SeatSpec()],
        processing=(old,),
        turn_seat=0,
    )

    substitute_judge_card(
        state, EffectContext(actor=0), old_card=state.card(old), new_card=state.card(new), subject=0
    )

    assert state.processing == [new]
    assert state.discard == [old]


def test_the_swap_primitive_gives_the_original_judge_card_to_the_rejudger():
    old = _find_card(PACK, suit="spade", rank=5)
    new = _find_card(PACK, suit="club", rank=5, exclude=(old,))
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4, hand=(new,))],
        processing=(old,),
        turn_seat=0,
    )

    swap_judge_card(
        state,
        EffectContext(actor=1),
        old_card=state.card(old),
        new_card=state.card(new),
        subject=0,
        rejudger=1,
    )

    assert state.processing == [new]
    assert state.player(1).hand == [old]
    assert locate(state, old) == ZoneRef(Zone.HAND, 1)
    assert locate(state, new) == ZoneRef(Zone.PROCESSING)


# ---------------------------------------------------------------------------
# 顺延目标的纯函数 + 判定串行、无第二个随机来源
# ---------------------------------------------------------------------------


def test_delayed_settle_target_is_discard_for_indulgence_and_a_seat_for_lightning():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(judge=(LIGHTNING[1],)), SeatSpec()],
        turn_seat=0,
    )

    assert delayed_settle_target(state, state.card(INDULGENCE[0]), 0) is None
    assert delayed_settle_target(state, state.card(LIGHTNING[0]), 0) == 2


def test_only_lightning_declares_the_pass_on_and_both_tricks_declare_judge_zone():
    """顺延是**牌面事实**：落在行为表，引擎与规则层都不出现具体牌名（票 11 §4）。"""
    from sgs.effects import CARD_BEHAVIORS

    assert CARD_BEHAVIORS["lightning"].passes_to_next_judge is True
    assert CARD_BEHAVIORS["indulgence"].passes_to_next_judge is False
    assert CARD_BEHAVIORS["lightning"].is_delayed is True
    assert CARD_BEHAVIORS["indulgence"].is_delayed is True
    assert CARD_BEHAVIORS["slash"].is_delayed is False


def test_two_engines_with_the_same_seed_produce_the_same_journal():
    def run() -> str:
        return _judge_engine(
            seat0=SeatSpec(hp=4, judge=(INDULGENCE[0], LIGHTNING[0])),
            deck=(NON_HEART, HEART, 20, 21, 22),
            seed=11,
        ).journal()

    assert run() == run(), "判定不引入第二个随机来源：同种子逐字节复现"
