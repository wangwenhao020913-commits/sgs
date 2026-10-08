"""【杀】的完整使用链与【闪】抵消（票 19）。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流；
纯规则（距离 / 攻击范围 / 候选）从 ``sgs.effects`` 的公开面直接断言。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.effects import (
    CARD_BEHAVIORS,
    CallOp,
    PushFrame,
    UseContext,
    attack_range,
    behavior_for,
    check_count,
    check_forbidden,
    check_targets,
    distance,
    legal_actions_for,
)
from sgs.engine import Engine
from sgs.state import EndPhase, FrameKind, Pass, QueryKind, UseCard
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack()
SLASH = cards_of_type(PACK, "slash")
DODGE = cards_of_type(PACK, "dodge")
PEACH = cards_of_type(PACK, "peach")


def _state(*, hand_a=(), hand_b=(), hp_a=4, hp_b=4, deck=(), seats=2):
    specs = [SeatSpec(hp=hp_a, hand=tuple(hand_a))]
    if seats > 1:
        specs.append(SeatSpec(hp=hp_b, hand=tuple(hand_b)))
    for _ in range(seats - len(specs)):
        specs.append(SeatSpec())
    return build_state(pack=PACK, seats=specs, deck=tuple(deck), turn_seat=0)


# ---------------------------------------------------------------------------
# 规则函数：距离与攻击范围（票 07 §4）
# ---------------------------------------------------------------------------


def test_distance_counts_living_seats_between_on_the_ring():
    state = _state(seats=4)

    assert distance(state, 0, 1) == 1
    assert distance(state, 0, 2) == 2, "4 人环上隔着 1 个活人 ⇒ 距离 2"
    assert distance(state, 0, 3) == 1

    state.player(1).alive = False
    assert distance(state, 0, 2) == 1, "死人之间的距离不计（官方：只数存活角色）"


def test_attack_range_is_the_rated_one_without_weapons():
    state = _state(seats=4)

    assert attack_range(state, 0) == 1


def test_in_1v1_the_distance_and_range_are_both_one():
    state = _state(hand_a=(SLASH[0],), hand_b=(DODGE[0],))

    assert distance(state, 0, 1) == 1
    assert distance(state, 1, 0) == 1
    assert attack_range(state, 0) == 1


# ---------------------------------------------------------------------------
# 行为表（票 07 §2）
# ---------------------------------------------------------------------------


def test_the_behavior_table_has_exactly_the_three_m1_cards():
    assert set(CARD_BEHAVIORS) == {"slash", "dodge", "peach"}


def test_slash_declares_a_damage_push_and_a_rated_single_use_per_turn():
    behavior = CARD_BEHAVIORS["slash"]

    assert behavior.use_timings[0].context is UseContext.PLAY_PHASE
    assert behavior.target_rule.rated_targets == 1
    assert behavior.target_rule.includes_self is False
    assert behavior.target_rule.needs_distance is True
    assert behavior.use_limit.rated == 1
    assert behavior.use_limit.extra == 0
    assert behavior.effects == (PushFrame(FrameKind.DAMAGE, (("amount", 1),)),)


def test_dodge_is_declared_as_a_use_in_the_response_window_with_no_effect():
    behavior = CARD_BEHAVIORS["dodge"]

    assert len(behavior.use_timings) == 1
    assert behavior.use_timings[0].context is UseContext.RESPONSE
    assert behavior.use_timings[0].timing is Timing.RESOLVE_BEFORE_EFFECT
    assert behavior.target_rule.rated_targets == 0
    assert behavior.effects == (), "【闪】的效果为空：抵消由被响应帧的标志承载"


def test_behavior_steps_are_only_push_frame_and_call_op():
    for behavior in CARD_BEHAVIORS.values():
        for step in behavior.effects:
            assert isinstance(step, (PushFrame, CallOp))


# ---------------------------------------------------------------------------
# 候选生成：出牌阶段与【闪】响应（票 07 §4）
# ---------------------------------------------------------------------------


def test_slash_is_offered_in_the_play_phase_against_the_other_seat_only():
    state = _state(hand_a=(SLASH[0],), hand_b=(DODGE[0],))

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)

    use_slash = [action for action in actions if isinstance(action, UseCard)]
    assert [action.card.card_id for action in use_slash] == [SLASH[0]]
    assert use_slash[0].targets == (1,), "1v1 额定 1 目标 = 对方"
    assert actions[-1].kind == "EndPhase", "无牌实例的 EndPhase 排最后"


def test_dodge_is_never_a_play_phase_candidate():
    state = _state(hand_a=(DODGE[0],), hand_b=())

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)

    assert [action.kind for action in actions] == ["EndPhase"]


def test_dodge_is_offered_without_targets_in_the_dodge_query_and_pass_is_last():
    state = _state(hand_a=(), hand_b=(DODGE[0], DODGE[1]))

    actions = legal_actions_for(state, 1, QueryKind.DODGE, answers_seq=1)

    assert [action.kind for action in actions] == ["UseCard", "UseCard", "Pass"]
    assert all(action.targets == () for action in actions if isinstance(action, UseCard))
    assert [action.card.card_id for action in actions if isinstance(action, UseCard)] == [
        DODGE[0],
        DODGE[1],
    ]


def test_dodge_query_still_asks_with_only_pass_when_the_hand_has_no_dodge():
    state = _state(hand_a=(), hand_b=(PEACH[0],))

    actions = legal_actions_for(state, 1, QueryKind.DODGE, answers_seq=1)

    assert [action.kind for action in actions] == ["Pass"]


def test_peach_is_offered_in_the_play_phase_only_below_full_hp_and_targets_self():
    healthy = _state(hand_a=(PEACH[0],))
    wounded = _state(hand_a=(PEACH[0],), hp_a=3)

    assert [
        action.kind for action in legal_actions_for(healthy, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    ] == ["EndPhase"]
    actions = legal_actions_for(wounded, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    peach = [action for action in actions if isinstance(action, UseCard)]
    assert [(action.card.card_id, action.targets) for action in peach] == [(PEACH[0], (0,))]


def test_l3_checks_are_named_after_the_official_steps_and_agree_with_the_candidates():
    healthy = _state(hand_a=(SLASH[0],))

    assert check_forbidden(healthy, 0, healthy.card(SLASH[0])) is False
    assert check_count(healthy, 0, healthy.card(SLASH[0])) is False
    assert check_targets(healthy, 0, healthy.card(SLASH[0])) == (1,)

    healthy.player(0).card_use_counts["slash"] = 1
    assert check_count(healthy, 0, healthy.card(SLASH[0])) is True
    exhausted = legal_actions_for(healthy, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    assert [action.kind for action in exhausted] == ["EndPhase"], (
        "本回合已用过额定【杀】后，【杀】不再是候选"
    )


def test_behavior_for_rejects_a_card_type_outside_the_m1_table():
    state = _state(hand_a=(SLASH[0],))
    card = state.card(SLASH[0]).model_copy(update={"type": "unobtainium"})

    try:
        behavior_for(card)
    except KeyError as error:
        assert "unobtainium" in str(error)
    else:  # pragma: no cover - 明确失败比静默返回默认行为更安全
        raise AssertionError("未知牌名必须有行为表条目")


# ---------------------------------------------------------------------------
# 使用链（票 19：L4 六个 + L5 六个）
# ---------------------------------------------------------------------------


class _ScriptedPlayer:
    """确定性脚本玩家：按座位精准命中"用【杀】"或"用【闪】"。"""

    def __init__(self, seat: int, *, slash_target: int | None = None, dodge: bool = False) -> None:
        self.seat = seat
        self.slash_target = slash_target
        self.dodge = dodge

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if self.slash_target is not None:
                chosen = _find(query, UseCard, type_id="slash", targets=(self.slash_target,))
                if chosen is not None:
                    return chosen
            return _find(query, EndPhase)
        if query.kind == "DODGE":
            if self.dodge:
                chosen = _find(query, UseCard, type_id="dodge")
                if chosen is not None:
                    return chosen
            return _find(query, Pass)
        return query.legal_actions[0]


def _find(query, action_type, *, type_id=None, targets=None):
    for action in query.legal_actions:
        if not isinstance(action, action_type):
            continue
        if type_id is not None and action.card.type != type_id:
            continue
        if targets is not None and action.targets != targets:
            continue
        return action
    return None


def _duel(*, hand_a, hand_b, hp_a=4, hp_b=4, deck, dodge_response=False):
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=hp_a, hand=tuple(hand_a)), SeatSpec(hp=hp_b, hand=tuple(hand_b))],
        deck=tuple(deck),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    engine.run([_ScriptedPlayer(0, slash_target=1), _ScriptedPlayer(1, dodge=dodge_response)])
    return engine


def _timings_of(records, frame_id):
    return [
        record.timing
        for record in records
        if record.kind == "timing" and record.frame_id == frame_id
    ]


def _frame_ids(records):
    """按压栈顺序返回帧 id（[0] 是用【杀】的 USE、[1] 是它的 RESOLVE……）。"""
    return [
        record.frame_id
        for record in records
        if record.kind == "frame" and record.op == "push"
    ]


def test_a_slash_use_walks_all_six_l4_timings_in_official_order():
    engine = _duel(hand_a=(SLASH[0],), hand_b=(PEACH[0],), deck=(DODGE[0], DODGE[1]))

    timings = [record.timing for record in engine.records if record.kind == "timing"]
    start = timings.index(Timing.USE_CHOOSE_TARGET)

    assert timings[start : start + 6] == [
        Timing.USE_CHOOSE_TARGET,
        Timing.USE_ON_USE,
        Timing.USE_DESIGNATE_TARGET,
        Timing.USE_BECOME_TARGET,
        Timing.USE_AFTER_DESIGNATE_TARGET,
        Timing.USE_AFTER_BECOME_TARGET,
    ]


def test_a_dodge_cancels_the_slash_and_skips_effect_and_after_effect():
    engine = _duel(
        hand_a=(SLASH[0],), hand_b=(DODGE[0],), deck=(PEACH[0], PEACH[1]), dodge_response=True
    )
    slash_use, slash_resolve, dodge_use, dodge_resolve = _frame_ids(engine.records)

    assert _timings_of(engine.records, slash_resolve) == [
        Timing.RESOLVE_START,
        Timing.RESOLVE_BEFORE_EFFECT,
        Timing.RESOLVE_END,
    ], "被抵消 ⇒ 跳过生效时 / 生效后，直接到使用结算结束时"
    assert _timings_of(engine.records, dodge_resolve) == [
        Timing.RESOLVE_START,
        Timing.RESOLVE_BEFORE_EFFECT,
        Timing.RESOLVE_EFFECT,
        Timing.RESOLVE_AFTER_EFFECT,
        Timing.RESOLVE_END,
    ], "【闪】自己的使用链照走（效果列表为空）"
    assert dodge_use > slash_resolve, "【闪】的 USE 帧压在被抵消的那张【杀】的 RESOLVE 之上"


def test_an_uncancelled_slash_walks_effect_and_after_effect_and_pushes_damage():
    engine = _duel(hand_a=(SLASH[0],), hand_b=(PEACH[0],), deck=(DODGE[0], DODGE[1]))
    slash_resolve = _frame_ids(engine.records)[1]

    assert _timings_of(engine.records, slash_resolve) == [
        Timing.RESOLVE_START,
        Timing.RESOLVE_BEFORE_EFFECT,
        Timing.RESOLVE_EFFECT,
        Timing.RESOLVE_AFTER_EFFECT,
        Timing.RESOLVE_END,
    ]
    pushed = [
        record.frame_kind
        for record in engine.records
        if record.kind == "frame" and record.op == "push"
    ]
    assert pushed[:2] == [FrameKind.USE, FrameKind.RESOLVE]
    assert FrameKind.DAMAGE in pushed, "行为表：杀 → 压入伤害帧"


def test_the_dodge_window_is_a_use_query_at_resolve_before_effect():
    engine = _duel(
        hand_a=(SLASH[0],), hand_b=(DODGE[0],), deck=(PEACH[0], PEACH[1]), dodge_response=True
    )
    query = next(
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "DODGE"
    )

    assert query.timing is Timing.RESOLVE_BEFORE_EFFECT
    assert query.actor == 1, "问的是这张【杀】的目标"
    assert any(isinstance(action, UseCard) for action in query.legal_actions)
    assert query.legal_actions[-1].kind == "Pass"


def test_a_used_card_moves_hand_to_processing_to_discard_both_as_use():
    engine = _duel(hand_a=(SLASH[0],), hand_b=(PEACH[0],), deck=(DODGE[0], DODGE[1]))

    moves = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [SLASH[0]]
    ]

    assert [(move.from_zone.value, move.to_zone.value, move.reason.value) for move in moves] == [
        ("hand", "processing", "USE"),
        ("processing", "discard", "USE"),
    ]


def test_the_used_card_sits_in_processing_while_the_response_window_is_open():
    engine = _duel(
        hand_a=(SLASH[0],), hand_b=(DODGE[0],), deck=(PEACH[0], PEACH[1]), dodge_response=True
    )
    records = engine.records
    to_processing = next(
        record.seq
        for record in records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone.value == "processing"
        and [card.card_id for card in record.cards] == [SLASH[0]]
    )
    query = next(
        record.seq for record in records if record.kind == "query" and record.query.kind == "DODGE"
    )
    to_discard = next(
        record.seq
        for record in records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone.value == "processing"
        and [card.card_id for card in record.cards] == [SLASH[0]]
    )

    assert to_processing < query < to_discard


def test_a_dodge_use_moves_its_own_card_through_the_processing_area():
    engine = _duel(
        hand_a=(SLASH[0],), hand_b=(DODGE[0],), deck=(PEACH[0], PEACH[1]), dodge_response=True
    )
    moves = [
        (record.from_zone.value, record.to_zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [DODGE[0]]
    ]

    assert moves == [("hand", "processing", "USE"), ("processing", "discard", "USE")]


def test_the_processing_area_is_public_and_holds_the_used_card_during_the_window():
    engine = _duel(
        hand_a=(SLASH[0],), hand_b=(DODGE[0],), deck=(PEACH[0], PEACH[1]), dodge_response=True
    )
    query = next(
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == "DODGE"
    )

    assert [card.card_id for card in query.view.processing] == [SLASH[0]], (
        "响应窗口开着时，被响应的实体牌已在处理区——公开区，参与者看得见"
    )

