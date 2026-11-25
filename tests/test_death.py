"""死亡、胜负与终局收尾（票 22）：L13 裁剪段序 + 检测点/终止点分离 + 清栈与牌守恒。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流。

被测的那条链固定为"座位 0 出一次【杀】打座位 1，座位 1 濒死而无人救"：
它同时命中濒死与死亡两层嵌套，且之后**必达真胜负**（1v1 一方阵亡）。
无人救的死亡路径照票 04 §5 的冻结实测逐帧丢弃
``NEAR_DEATH / LIFE_DECREASE / DAMAGE / RESOLVE / USE`` 五帧。
"""

from __future__ import annotations

import pytest

from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Cancelled, Engine
from sgs.engine.steps import WINDOW_CANCELLED_KEY, death_time_window_open
from sgs.skills import RUNNING_SKILL_KEY
from sgs.state import (
    EndPhase,
    FrameKind,
    MoveReason,
    Pass,
    QueryKind,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import PassPlayer, SeatSpec, build_state, cards_of_type, run_queries

PACK = load_content_pack(deck_profile="basic_53")
"""M1 回归网显式选中 53 张档（票 16 §9）；身份奖惩的 108 档用例在 ``test_victory.py``。"""
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


def _death_engine(*, identity: tuple[Identity | None, Identity | None] = (None, None)):
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
    assert (tail.side, tail.winners) == (None, (0,)), (
        "票 25：无身份局形状统一——side=None、winners=(获胜座位,)"
    )


def test_identity_fields_stay_in_state_but_do_not_take_part_in_the_verdict():
    engine = _death_engine(identity=(Identity.REBEL, Identity.LORD))

    assert (engine.state.player(0).identity, engine.state.player(1).identity) == (
        Identity.REBEL,
        Identity.LORD,
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


# ---------------------------------------------------------------------------
# 阵亡后已在飞（dead-actor-in-flight 票 01）：询问口 / 授予口
# ---------------------------------------------------------------------------


def test_a_dead_actor_is_only_asked_inside_the_death_time_window():
    """询问口：窗口外**不落询问**、返回 ``Cancelled``；窗口内仍问死者本人（武魂平票）。

    窗口内那一半走**真实的死亡时技能派发形状**（dead-actor-in-flight 票 07）：承载帧 =
    死者的 ``DEATH`` 帧、窗口由 :func:`~sgs.engine.steps.death_time_window_open` 打开、
    帧上的 ``running_skill`` / ``skill_owner`` 与派发环在回调前写下的同形
    （`src/sgs/skills/catalog.py:_enter_skill_keys`）。窗口**只放行这一种**询问——别的
    在飞效果问死者仍是 ``Cancelled``，负例见下一条。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(alive=False, hp=0), SeatSpec()],
        deck=DECK,
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=1)
    frame = engine.push_frame(
        FrameKind.DEATH,
        {"target": 0, "targets": (0,), "source": None, "damage_source": None},
        timing=Timing.DEATH_BEFORE_START,
    )

    before = engine._seq
    refused = engine.ask_option(
        frame, 0, Timing.DEATH_ON, skill_id="test.death_time", options=("a",), optional=True
    )
    assert refused is Cancelled, "死者的询问返回 Cancelled 哨兵（不是 Pass / 空选 / SUSPEND）"
    assert engine.cancelled(refused) and not engine.suspended(refused)
    assert not isinstance(refused, Pass), "Cancelled 不是放弃"
    assert not [record for record in engine.records if record.kind == "query"], (
        "被取消的询问不落 QueryRecord"
    )
    assert engine._seq == before, "被取消的询问不占询问序号"

    death_time_window_open(engine, frame)
    # 派发环在回调前的两个写入（`_enter_skill_keys`）：这一刻跑的是死者本人的技能。
    frame.data[RUNNING_SKILL_KEY] = "test.death_time"
    frame.data["skill_owner"] = 0
    allowed = engine.ask_option(
        frame, 0, Timing.DEATH_ON, skill_id="test.death_time", options=("a",), optional=True
    )
    assert engine.suspended(allowed), "死亡时技能窗口仍可询问死者本人"
    assert [record.query.actor for record in engine.records if record.kind == "query"] == [0]


def test_the_death_time_window_only_opens_the_dying_actors_own_skill_dispatch():
    """窗口内**别的在飞效果**问该死者 ⇒ ``Cancelled``、不落询问（票 07 的负例）。

    放行判据要三条同时成立：承载帧是**该死者的 DEATH 帧**、询问时机在窗口内、帧上正在
    跑的技能**属于死者本人**。三个变体各破坏一条，且都不落 ``QueryRecord`` / 不占序号。
    """
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(alive=False, hp=0), SeatSpec()],
        deck=DECK,
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=1)
    death = engine.push_frame(
        FrameKind.DEATH,
        {"target": 0, "targets": (0,), "source": None, "damage_source": None},
        timing=Timing.DEATH_BEFORE_START,
    )
    death_time_window_open(engine, death)
    # ①别的座位的技能帧 = 另一个在飞效果（票面的构造）。
    other = engine.push_frame(
        FrameKind.SKILL,
        {"source": 1, "targets": (0,), "skill_id": "test.other"},
        timing=Timing.DEATH_ON,
    )
    other.data[RUNNING_SKILL_KEY] = "test.other"
    other.data["skill_owner"] = 1
    before = engine._seq
    refused = engine.ask_option(
        other, 0, Timing.DEATH_ON, skill_id="test.other", options=("a",), optional=True
    )
    assert refused is Cancelled, "窗口内别的效果问死者同样是 Cancelled"
    assert not isinstance(refused, Pass), "Cancelled 不是放弃"
    assert engine._seq == before, "被取消的询问不占询问序号"
    assert not [record for record in engine.records if record.kind == "query"], (
        "被取消的询问不落 QueryRecord"
    )
    # ②同一张 DEATH 帧上，正在跑的是别人（不是死者的）技能。
    death.data[RUNNING_SKILL_KEY] = "test.other"
    death.data["skill_owner"] = 1
    assert engine.ask_option(
        death, 0, Timing.DEATH_ON, skill_id="test.other", options=("a",), optional=True
    ) is Cancelled, "running skill owner 不是死者本人 ⇒ 不放行"
    # ③死者本人的技能，但询问时机不在窗口内。
    death.data[RUNNING_SKILL_KEY] = "test.death_time"
    death.data["skill_owner"] = 0
    assert engine.ask_option(
        death,
        0,
        Timing.PHASE_PLAY_IDLE,
        skill_id="test.death_time",
        options=("a",),
        optional=True,
    ) is Cancelled, "窗口外的时机不放行"
    assert not [record for record in engine.records if record.kind == "query"], (
        "三个变体一条询问都不落"
    )


def test_grants_do_not_land_on_a_dead_player():
    """授予口：牌进死者区域 / 加体力 / 标记 / 状态都不落地、不发 delta（取走照旧）。"""
    card = cards_of_type(PACK, "slash")[1]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(alive=False, hp=0), SeatSpec(hand=(card,))],
        deck=tuple(card_id for card_id in DECK if card_id != card),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=1)
    frame = engine.push_frame(
        FrameKind.SKILL,
        {"source": 1, "targets": (), "skill_id": "test.grants"},
        timing=Timing.DEATH_ON,
    )
    before = len(engine.records)

    assert engine.move_in_frame(
        frame,
        state.refs((card,)),
        to_zone=ZoneRef(Zone.HAND, 0),
        reason=MoveReason.PUT,
        owner=0,
    ) is None
    assert card in engine.state.player(1).hand, "牌进死者区域的授予不落地（牌留原处）"
    assert engine.apply_hp_change(frame, 0, 1) is None
    assert engine.add_marker(
        frame, 0, name="梦魇", source_seat=1, source_skill_id="test.grants"
    ) is False
    assert engine.set_chained(frame, 0, True) is False
    assert engine.set_flipped(frame, 0, True) is False
    assert engine.set_wine_active(frame, 0, True) is False
    assert engine.grant_skill(0, "test.granted", frame=frame) is False

    dead = engine.state.player(0)
    assert (dead.hp, dead.markers, dead.chained, dead.flipped, dead.wine_active) == (
        0,
        (),
        False,
        False,
        False,
    ), "体力 / 标记 / 状态一个都没写进死者"
    assert dead.granted_skills == []
    assert not [record for record in engine.records[before:] if record.kind == "delta"], (
        "没有落地的授予不产生 delta"
    )


# ---------------------------------------------------------------------------
# 阵亡后已在飞（dead-actor-in-flight 票 06）：响应窗口里的取消 ≠ "没出闪 / 没声明"
# ---------------------------------------------------------------------------

STANDARD = load_content_pack(deck_profile="standard_108")
"""无懈窗口要多一张锦囊；本文件其余用例仍钉 ``basic_53``（票 16 §9 的回归网）。"""


def _frame_identity_in_stack(engine: Engine, frame) -> bool:
    return any(current is frame for current in engine.stack.frames)


def test_a_dead_dodge_target_does_not_record_a_missed_dodge() -> None:
    """【闪】窗口的询问被取消 ⇒ 这一份效果段不发生，**不是**"没出闪"（票 06）。

    目标在 ``RESOLVE`` 帧已在飞时已阵亡：``ask_query(DODGE)`` 返回 ``Cancelled``。旧实现
    把它落到 ``frame.data["canceled"] = False``（= "没出闪"的裁决）；正确读法是票 01 的
    "目标阵亡 ⇒ 该目标那一份整段不做"——故 C3/C4 的时机照记、``apply_effects`` 的存活闸
    跳过效果表、C5 照常收尾，帧随即出栈、对局继续。
    """
    slash = SLASH[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hand=(slash,), identity=Identity.LORD),
            SeatSpec(hp=0, alive=False, identity=Identity.REBEL),
            SeatSpec(hp=4, identity=Identity.REBEL),
            SeatSpec(hp=4, identity=Identity.REBEL),
        ],
        deck=DECK,
        turn_seat=0,
        rule_config=RuleConfig(player_count=4, first_seat=0),
    )
    engine = Engine.from_state(state, seed=1)
    frame = engine.push_frame(
        FrameKind.RESOLVE,
        {
            "card": state.card(slash),
            "source": 0,
            "targets": (1,),
            "cursor": 0,
        },
        timing=Timing.RESOLVE_START,
    )
    players = [_NoRescuePlayer(seat, slash_budget=0) for seat in range(4)]

    # 跑到窗口收尾的那一步（C2 第一次重入之前）：这一瞬间的帧私有终态就是"取消"的落点。
    for _ in range(20):
        if WINDOW_CANCELLED_KEY in frame.data or "canceled" in frame.data:
            break
        engine._step()
    assert WINDOW_CANCELLED_KEY in frame.data, "取消要落成显式的窗口终态（不是 'canceled'）"
    assert "canceled" not in frame.data, (
        "取消不许记成 ``canceled = False``：那是'没出闪'的裁决，会派生后续效果"
    )
    assert frame.data[WINDOW_CANCELLED_KEY] is True

    # 之后照常走完 C3–C5 并出栈：对局继续，不回到窗口重问。
    run_queries(engine, players, lambda e: not _frame_identity_in_stack(e, frame))
    assert not _frame_identity_in_stack(engine, frame), "帧必须出栈（不能挂死在 C2）"
    assert engine.pending is None
    assert [
        record.query for record in engine.records if record.kind == "query"
        and record.query.kind == QueryKind.DODGE
    ] == [], "窗口外不向死者发问，也不落 QueryRecord"
    assert engine.state.player(1).hp == 0, "没有伤害落到已阵亡的目标身上"
    assert not [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_DEAL
    ], "取消不是把【杀】照常结算：不压伤害帧"
    assert [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.RESOLVE_END
    ], "C5 照常收尾（'这段不可实现'不等于'这张牌整次使用作废'）"


def test_a_dead_seat_in_the_nullify_ring_is_skipped_and_the_window_continues() -> None:
    """收集式无懈窗口：已阵亡的座位不发询问（``Cancelled``）⇒ 跳过它、窗口照旧问下一个。

    收集式窗口的询问者区间是"当前回合角色起逆时针"的**全部座位**；已阵亡的座位也在名单里
    （票 01 的引擎闸拒绝发问、返回 ``Cancelled``）。本用例钉住"跳过这一个、继续问下一个"
    的语义：死座既不进记录、也不成为声明，其余座位照常被问，窗口照常收尾。
    """
    ex_nihilo = cards_of_type(STANDARD, "ex_nihilo")[0]
    deck = tuple(
        card_id
        for card_id in range(len(STANDARD.deck.cards))
        if card_id != ex_nihilo
    )
    state = build_state(
        pack=STANDARD,
        seats=[
            SeatSpec(hp=4, identity=Identity.LORD),
            SeatSpec(hp=4, identity=Identity.REBEL),
            SeatSpec(hp=0, alive=False, identity=Identity.REBEL),
            SeatSpec(hp=4, identity=Identity.REBEL),
        ],
        deck=deck,
        processing=(ex_nihilo,),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4, first_seat=0),
    )
    engine = Engine.from_state(state, seed=1)
    frame = engine.push_frame(
        FrameKind.RESOLVE,
        {
            "card": state.card(ex_nihilo),
            "source": 0,
            "targets": (),
            "cursor": 0,
        },
        timing=Timing.RESOLVE_START,
    )
    players = [PassPlayer() for _ in range(4)]

    run_queries(engine, players, lambda e: not _frame_identity_in_stack(e, frame))
    assert not _frame_identity_in_stack(engine, frame), "帧必须出栈（死座不能让窗口卡住）"

    respond_actors = [
        record.query.actor
        for record in engine.records
        if record.kind == "query" and record.query.kind == QueryKind.RESPOND
    ]
    assert respond_actors == [0, 1, 3], (
        "询问者区间里的死座（2）被跳过，其余座位按次序照问——"
        f"实得 {respond_actors}"
    )
    assert len(state.player(0).hand) == 2, "窗口照常收尾：无中生有的摸两张照常执行"
    assert [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.RESOLVE_END
    ], "C5 照常收尾"
