"""死亡、胜负与终局收尾（票 22）：L13 裁剪段序 + 检测点/终止点分离 + 清栈与牌守恒。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流。

被测的那条链固定为"座位 0 出一次【杀】打座位 1，座位 1 濒死而无人救"：
它同时命中濒死与死亡两层嵌套，且之后**必达真胜负**（1v1 一方阵亡）。
无人救的死亡路径照票 04 §5 的冻结实测逐帧丢弃
``NEAR_DEATH / LIFE_DECREASE / DAMAGE / RESOLVE / USE`` 五帧。
"""

from __future__ import annotations

import pytest

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import EndPhase, FrameKind, MoveReason, Pass, UseCard, Zone, ZoneRef
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack()
SLASH = cards_of_type(PACK, "slash")
PEACH = cards_of_type(PACK, "peach")

#: 座位 0 手里那张【杀】与座位 1 手里那两张【桃】之外**全部**在牌堆里：
#: 牌堆为空会让摸牌阶段立刻以"两堆皆空"结束，够不到被测的那条链。
DECK = tuple(
    card_id
    for card_id in range(len(PACK.deck.cards))
    if card_id not in (SLASH[0], PEACH[0], PEACH[1])
)

DEATH_TIMINGS = (
    Timing.DEATH_BEFORE_START.value,
    Timing.DEATH_REVEAL_IDENTITY_AFTER.value,
    Timing.DEATH_ON.value,
    Timing.DEATH_AFTER.value,
)


class _NoRescuePlayer:
    """座位 0 出一次【杀】；被问【闪】/【桃】一律放弃（无人救 → 真死亡）。"""

    def __init__(self, seat: int, *, slash_budget: int) -> None:
        self.seat = seat
        self.slash_budget = slash_budget

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if self.slash_budget > 0:
                slash = _find(query, UseCard, type_id="slash")
                if slash is not None:
                    self.slash_budget -= 1
                    return slash
            return _find(query, EndPhase)
        if query.kind in ("DODGE", "PEACH"):
            return _find(query, Pass)
        return query.legal_actions[0]


def _find(query, action_type, *, type_id=None):
    for action in query.legal_actions:
        if not isinstance(action, action_type):
            continue
        if type_id is not None and action.card.type != type_id:
            continue
        return action
    return None


def _death_engine(*, identity: tuple[str | None, str | None] = (None, None)):
    """座位 1 被一次【杀】打到濒死、双方放弃 ⇒ 死亡终局。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(SLASH[0],)), SeatSpec(hp=1, hand=(PEACH[0], PEACH[1]))],
        deck=DECK,
        turn_seat=0,
    )
    state.player(0).identity, state.player(1).identity = identity
    engine = Engine.from_state(state, seed=1)
    engine.run([_NoRescuePlayer(0, slash_budget=1), _NoRescuePlayer(1, slash_budget=0)])
    return engine


def _of(records, kind):
    return [record for record in records if record.kind == kind]


def _frames(records, *, op):
    return [record for record in _of(records, "frame") if record.op == op]


def _timings(records, *, frame_id=None):
    records = _of(records, "timing")
    if frame_id is not None:
        records = [record for record in records if record.frame_id == frame_id]
    return [record.timing.value for record in records]


def _moves(records):
    return [record for record in _of(records, "delta") if record.delta_type == "card_move"]


def test_the_death_frame_dispatches_the_trimmed_l13_segments_in_order():
    engine = _death_engine()
    death = next(
        record
        for record in _frames(engine.records, op="push")
        if record.frame_kind is FrameKind.DEATH
    )

    assert _timings(engine.records, frame_id=death.frame_id) == list(DEATH_TIMINGS), (
        "裁剪后的段序：BEFORE_START → REVEAL_IDENTITY_AFTER → ON → AFTER（空实现也照记）"
    )


def test_the_skipped_identity_reveal_segment_is_never_dispatched():
    engine = _death_engine()

    assert Timing.DEATH_REVEAL_IDENTITY_BEFORE.value not in _timings(engine.records), (
        "票 13 §4：无身份牌 ⇒ '亮出身份牌前' 跳过、不 dispatch（枚举位保留）"
    )


def test_the_death_frame_is_pushed_from_the_near_death_frame():
    engine = _death_engine()
    pushes = _frames(engine.records, op="push")
    death = pushes[-1]

    assert death.frame_kind is FrameKind.DEATH
    assert death.return_to == ("NEAR_DEATH", Timing.DEATH_BEFORE_START.value), (
        "压栈标签 =（父帧 kind, 子帧首步时机）；死亡结算是濒死结算的插入子帧，"
        "濒死帧因此留在栈上，交给终局清栈"
    )
    assert death.depth == 6, "死亡结算挂在濒死（深度 5）之上"


def test_victory_is_recorded_at_the_check_point_but_does_not_terminate_there():
    engine = _death_engine()
    values = _timings(engine.records)

    reveal = values.index(Timing.DEATH_REVEAL_IDENTITY_AFTER.value)
    assert values.index(Timing.DEATH_ON.value) > reveal, (
        "检测点只判 mark、不终止：DEATH_ON 与 DEATH_AFTER 必须照常跑完"
    )
    assert values.index(Timing.DEATH_AFTER.value) > values.index(Timing.DEATH_ON.value)


def test_the_death_settlement_is_atomic_no_query_or_other_segment_interleaves():
    engine = _death_engine()
    death = next(
        record
        for record in _frames(engine.records, op="push")
        if record.frame_kind is FrameKind.DEATH
    )
    queries_after_push = [
        record
        for record in _of(engine.records, "query")
        if record.seq > death.seq
    ]

    assert not queries_after_push, "死亡结算内部没有暂停点（M1 无技能）"
    assert [record.frame_id for record in _of(engine.records, "timing") if record.seq > death.seq][
        : len(DEATH_TIMINGS)
    ] == [death.frame_id] * len(DEATH_TIMINGS), (
        "死亡结算的四段连续 dispatch，中间没有别的帧插进来"
    )


def test_the_termination_point_comes_after_the_last_death_timing():
    engine = _death_engine()
    after_death = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing.value == Timing.DEATH_AFTER.value
    ]
    assert after_death, "DEATH_AFTER 必须被 dispatch"
    cutoff = after_death[-1].seq
    tail = [
        record
        for record in engine.records
        if getattr(record, "seq", 0) > cutoff
    ]

    assert tail[0].kind == "frame" and tail[0].op == "pop", "先死亡帧正常出栈"
    assert tail[0].frame_kind is FrameKind.DEATH
    assert [record.frame_kind.value for record in tail if getattr(record, "op", None) == "discard"] == [
        "NEAR_DEATH",
        "LIFE_DECREASE",
        "DAMAGE",
        "RESOLVE",
        "USE",
    ], "终止点之后才是逐帧清栈"
    assert tail[-1].kind == "game_end", "流里最后一条是终局记录"


def test_the_game_end_record_is_last_and_carries_the_check_point_seq():
    engine = _death_engine()
    reveal = next(
        record
        for record in _of(engine.records, "timing")
        if record.timing.value == Timing.DEATH_REVEAL_IDENTITY_AFTER.value
    )
    tail = engine.records[-1]

    assert tail.kind == "game_end", "终局记录是流里最后一条"
    assert tail.check_seq == reveal.seq, "带胜负检测点（DEATH_REVEAL_IDENTITY_AFTER）那条记录的序号"
    assert tail.result == "winner"
    assert tail.winner == 0, "1v1：一方阵亡即另一方获胜"


def test_identity_fields_stay_in_state_but_do_not_take_part_in_the_verdict():
    engine = _death_engine(identity=("反贼", "主公"))

    assert (engine.state.player(0).identity, engine.state.player(1).identity) == (
        "反贼",
        "主公",
    ), "身份字段保留在状态里"
    assert engine.records[-1].winner == 0, "胜负只看存活数，不看身份（票 13 §3）"


def test_death_on_discards_every_card_in_the_dead_characters_area():
    engine = _death_engine()
    death = next(
        record
        for record in _frames(engine.records, op="push")
        if record.frame_kind is FrameKind.DEATH
    )
    on_records = [
        record
        for record in _of(engine.records, "delta")
        if record.delta_type == "card_move" and record.frame_id == death.frame_id
    ]

    assert len(on_records) == 1, "Ⅰ.a 是一次移动操作（一次性把死者区域里的牌置入弃牌堆）"
    move = on_records[0]
    assert (move.from_zone, move.to_zone, move.reason) == (
        ZoneRef(Zone.HAND, 1),
        ZoneRef(Zone.DISCARD),
        MoveReason.DISCARD,
    )
    assert [card.card_id for card in move.cards] == [PEACH[0], PEACH[1]]
    assert engine.state.player(1).hand == [], "死者手牌清空"
    assert not engine.state.player(1).alive, "阵亡在检测点之前已落到状态里"


def test_the_final_stack_is_cleared_frame_by_frame_innermost_first():
    engine = _death_engine()
    discarded = _frames(engine.records, op="discard")

    assert [record.frame_kind for record in discarded] == [
        FrameKind.NEAR_DEATH,
        FrameKind.LIFE_DECREASE,
        FrameKind.DAMAGE,
        FrameKind.RESOLVE,
        FrameKind.USE,
    ], "票 04 §5 的冻结实测：逐帧丢弃五帧，内层在前"
    assert [record.depth for record in discarded] == [4, 3, 2, 1, 0], (
        "丢弃顺序可在记录流里断言（深度随出栈递减）"
    )
    assert all(record.frame_kind is not FrameKind.DEATH for record in discarded), (
        "死亡帧本身是正常出栈（pop），不是被丢弃的上层帧"
    )
    assert not engine.stack, "清栈后栈为空"


def test_the_teardown_moves_leftover_processing_cards_into_the_discard_pile():
    engine = _death_engine()
    move = _moves(engine.records)[-1]
    discarded = _frames(engine.records, op="discard")

    assert discarded[-1].seq < move.seq, "收尾在逐帧清栈之后"
    assert move.seq < engine.records[-1].seq, "收尾在终局记录之前"
    assert (move.from_zone, move.to_zone, move.reason) == (
        ZoneRef(Zone.PROCESSING),
        ZoneRef(Zone.DISCARD),
        MoveReason.USE,
    ), "补做 L5.6'使用结算结束后把处理区里的牌置入弃牌堆'（票 07 §6）"
    assert engine.state.processing == [], "处理区在终局为空"


def test_card_conservation_holds_unconditionally_at_game_end():
    engine = _death_engine()
    state = engine.state
    everywhere = [
        *state.deck,
        *state.discard,
        *state.processing,
        *(card_id for player in state.players for card_id in player.hand),
        *(card_id for player in state.players for card_id in player.equip),
        *(card_id for player in state.players for card_id in player.judge),
    ]

    assert sorted(everywhere) == list(range(len(state.cards))), (
        "终局时每张实体牌恰在一个区域（六个区）：牌守恒含终局无条件成立"
    )


def test_push_frame_is_refused_once_the_verdict_is_decided():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(SLASH[0],)), SeatSpec(hp=1, hand=(PEACH[0], PEACH[1]))],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    players = [_NoRescuePlayer(0, slash_budget=1), _NoRescuePlayer(1, slash_budget=0)]

    for _ in range(20_000):
        if engine.pending is None:
            engine._step()
            if engine.pending_outcome is not None:
                break
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))

    assert engine.pending_outcome is not None, "应当已经过了胜负检测点"
    assert not engine.finished, "检测点不终止：死亡结算还在跑"
    with pytest.raises(RuntimeError):
        engine.push_frame(FrameKind.DAMAGE, {}, timing=Timing.DAMAGE_DEAL)
