"""濒死与求桃（票 21）：L12 三个时机 + 官方求桃顺序 + 回复插入。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流；候选生成从
``sgs.effects`` 的公开面直接断言。

三条互斥路径各一组用例（票 12 §2 的场景 4 / 5 / 6）：

- **自救**：濒死者自己在 ``NEAR_DEATH_AT`` 出【桃】；
- **他人救**：当前回合角色（先被问的那个）出【桃】；
- **无人救**：全部放弃 ⇒ 死亡结算（票 22）跑完并以胜负收局；死亡四段与终局收尾的
  逐条断言落在 ``tests/test_death.py``，本文件只确认"濒死不被打断、救回则继续"。

构造局面一律"**只打一次【杀】**"（``slash_budget``）：濒死是插入结算，一旦一方真的死亡
（或一直被打到 0），后续回合会再触发濒死，断言就分不清是哪一次。牌堆故意留薄，
于是没人再进攻时对局以牌堆耗尽平局收场——本票只关心濒死那段记录。
"""

from __future__ import annotations

import pytest

from sgs.data import load_content_pack
from sgs.effects import UseContext, behavior_for, legal_actions_for
from sgs.engine import Engine, IllegalActionError
from sgs.state import EndPhase, FrameKind, MoveReason, Pass, PlayerView, QueryKind, UseCard
from sgs.timing import Timing

from support import (
    SeatSpec,
    build_state,
    cards_of_type,
    play_phase_peach_engine,
    run_turns,
)

PACK = load_content_pack()
SLASH = cards_of_type(PACK, "slash")
PEACH = cards_of_type(PACK, "peach")
DODGE = cards_of_type(PACK, "dodge")

#: 座位 0 手里那张【杀】、座位 1 手里那两张【桃】之外**全部**都在牌堆里：
#: 牌堆为空会让摸牌阶段立刻以"两堆皆空"结束，够不到被测的那条链。
DECK = tuple(
    card_id
    for card_id in range(len(PACK.deck.cards))
    if card_id not in (SLASH[0], PEACH[0], PEACH[1])
)


class _ScriptedPlayer:
    """确定性脚本玩家：按 ``slash_budget`` 次出【杀】、被问【闪】一律放弃。

    濒死窗口按 ``rescue`` 决定是否出【桃】（对濒死者使用），否则放弃。
    """

    def __init__(self, seat: int, *, rescue: bool = False, slash_budget: int = 1) -> None:
        self.seat = seat
        self.rescue = rescue
        self.slash_budget = slash_budget

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if self.slash_budget > 0:
                slash = _find(query, UseCard, type_id="slash")
                if slash is not None:
                    self.slash_budget -= 1
                    return slash
            return _find(query, EndPhase)
        if query.kind == "DODGE":
            return _find(query, Pass)
        if query.kind == "PEACH":
            if self.rescue:
                peach = _find(query, UseCard, type_id="peach")
                if peach is not None:
                    return peach
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


def _run(*, hp_b: int, rescue_seat: int | None, slash_budget_b: int = 0):
    """座位 0 出一次【杀】打座位 1；之后双方都不再进攻。

    重洗落地后（票 23）被动局面不再因牌堆耗尽收场，故只跑到**第一个回合走完**为止
    （无人救的死亡路径仍会在第一回合内自然收局）。
    """
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hand=(SLASH[0],)),
            SeatSpec(hp=hp_b, hand=(PEACH[0], PEACH[1])),
        ],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_turns(
        engine,
        [
            _ScriptedPlayer(seat, rescue=(seat == rescue_seat), slash_budget=budget)
            for seat, budget in enumerate((1, slash_budget_b))
        ],
        turns=1,
    )
    return engine


def _of(records, kind):
    return [record for record in records if record.kind == kind]


def _timings(records, *, frame_id=None):
    """时机序列，按**字符串取值**给出（``Timing`` 是 ``str`` 子类，见下）。

    记录流里的 ``Timing`` 实例与测试 import 的枚举成员可能来自两个模块实例，
    故一律比 ``.value``，不比成员身份（``in`` / ``is``）。
    """
    if frame_id is None:
        return [record.timing.value for record in _of(records, "timing")]
    return [
        record.timing.value
        for record in _of(records, "timing")
        if record.frame_id == frame_id
    ]


def _frame_ids(records, *, op="push"):
    return [record.frame_id for record in _of(records, "frame") if record.op == op]


def _hp_changes(records, *, frame_id=None):
    changes = [
        record for record in _of(records, "delta") if record.delta_type == "hp_change"
    ]
    if frame_id is not None:
        changes = [change for change in changes if change.frame_id == frame_id]
    return changes


def _queries(records, kind):
    return [record.query for record in _of(records, "query") if record.query.kind == kind]


def _advance_to_first_peach(engine, *, players):
    """手写推进到第一个 ``PEACH`` 询问并停在那里（不替调用者应答它）。"""
    for _ in range(20_000):
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return False
            continue
        query = engine.pending
        if query.kind == "PEACH":
            return True
        engine.submit(players[query.actor].respond(query))
    return False


# ---------------------------------------------------------------------------
# L12 三时机与插入点
# ---------------------------------------------------------------------------


NEAR_DEATH_TIMINGS = (
    Timing.NEAR_DEATH_ENTER.value,
    Timing.NEAR_DEATH_AT.value,
    Timing.NEAR_DEATH_AFTER.value,
)
DAMAGE_AFTERMATH_TIMINGS = (
    Timing.DAMAGE_AFTER_DEAL.value,
    Timing.DAMAGE_AFTER_TAKE.value,
    Timing.DAMAGE_END.value,
)


def test_the_near_death_frame_dispatches_the_official_three_timings_in_order():
    engine = _run(hp_b=1, rescue_seat=None)
    near_death = _frame_ids(engine.records)[4]

    assert _timings(engine.records, frame_id=near_death) == list(NEAR_DEATH_TIMINGS)


def test_a_character_never_enters_near_death_while_staying_above_zero():
    engine = _run(hp_b=2, rescue_seat=None)

    assert not (set(_timings(engine.records)) & set(NEAR_DEATH_TIMINGS)), (
        "体力扣到 1 点以上不进入濒死，三时机一个都不该出现"
    )
    assert not _queries(engine.records, "PEACH")


def test_the_near_death_frame_is_pushed_from_the_life_decrease_frame():
    engine = _run(hp_b=1, rescue_seat=None)
    pushes = [record for record in _of(engine.records, "frame") if record.op == "push"]

    assert [record.frame_kind for record in pushes[:5]] == [
        FrameKind.USE,
        FrameKind.RESOLVE,
        FrameKind.DAMAGE,
        FrameKind.LIFE_DECREASE,
        FrameKind.NEAR_DEATH,
    ], "事件链：使用 → 结算 → 伤害 → 扣减体力 → 濒死"
    assert [record.depth for record in pushes[:5]] == [1, 2, 3, 4, 5]
    assert pushes[4].return_to == ("LIFE_DECREASE", Timing.NEAR_DEATH_ENTER.value)


def test_near_death_takes_priority_over_the_damage_aftermath_timings():
    """无人救：濒死之后直接进入死亡结算，故"造成伤害后"类时机一个都不出现。"""
    engine = _run(hp_b=1, rescue_seat=None)
    emitted = _timings(engine.records)
    last_near_death = max(
        index for index, timing in enumerate(emitted) if timing in NEAR_DEATH_TIMINGS
    )
    death_before_start = emitted.index(Timing.DEATH_BEFORE_START.value)

    assert last_near_death < death_before_start, (
        "濒死三时机整个插在死亡结算之前；死亡结算再跑完四段"
    )
    for timing in DAMAGE_AFTERMATH_TIMINGS:
        assert timing not in emitted, (
            f"官方 4.17：濒死结算优先于'造成伤害后 / 受到伤害后'，{timing} 不该出现"
        )
    assert Timing.LIFE_DECREASE_AFTER.value not in emitted, (
        "无人救时整条伤害链（含扣减体力尾巴）留给终局清栈，不再继续 dispatch"
    )


# ---------------------------------------------------------------------------
# 求桃：官方顺序、粒度与候选
# ---------------------------------------------------------------------------


def test_the_peach_window_opens_at_near_death_at_for_the_dying_character():
    engine = _run(hp_b=1, rescue_seat=None)
    query = _queries(engine.records, "PEACH")[0]

    assert query.timing is Timing.NEAR_DEATH_AT
    assert query.dying == 1


def test_peach_is_asked_one_responder_at_a_time_from_the_turn_seat_counterclockwise():
    engine = _run(hp_b=1, rescue_seat=None)

    assert [query.actor for query in _queries(engine.records, "PEACH")] == [0, 1], (
        "官方 L12.2：从当前回合角色（座位 0）起、逆时针，每人一次"
    )


def test_the_ask_order_starts_from_the_current_turn_seat_not_the_dying_character():
    """回合角色是座位 1、濒死者是座位 0：应答序列从**座位 1** 起，而不是从濒死者起。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=1, hand=(DODGE[0],)),
            SeatSpec(hp=3, hand=(SLASH[0], PEACH[0])),
        ],
        deck=DECK,
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=1)
    run_turns(
        engine, [_ScriptedPlayer(0), _ScriptedPlayer(1, rescue=True, slash_budget=1)], turns=2
    )

    assert [query.actor for query in _queries(engine.records, "PEACH")] == [1], (
        "当前回合角色（座位 1）先被问，且它一出【桃】就结束询问"
    )
    assert all(query.dying == 0 for query in _queries(engine.records, "PEACH"))
    assert engine.state.player(0).hp == 1


def test_passing_is_always_a_legal_peach_answer_and_candidates_are_sorted_by_card_id():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=0, hand=(PEACH[1], PEACH[0]))],
        deck=DECK,
        turn_seat=0,
    )

    actions = legal_actions_for(state, 1, QueryKind.PEACH, answers_seq=1, dying=1)

    assert [action.kind for action in actions] == ["UseCard", "UseCard", "Pass"]
    assert [
        action.card.card_id for action in actions if isinstance(action, UseCard)
    ] == [PEACH[0], PEACH[1]], "候选顺序 = 牌实例 id 升序（票 05 §4）"
    assert all(
        action.targets == (1,) for action in actions if isinstance(action, UseCard)
    ), "濒死语境里【桃】的目标是濒死者，不是应答者自己"


def test_a_responder_without_peach_is_still_asked_and_may_only_pass():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(DODGE[0],)), SeatSpec(hp=0)],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    engine.run([_ScriptedPlayer(0), _ScriptedPlayer(1)])

    first = _queries(engine.records, "PEACH")[0]
    assert first.actor == 0
    assert [action.kind for action in first.legal_actions] == ["Pass"], (
        "唯一候选是放弃：求桃窗口仍然发询问，形状不随局面变形（票 05 §4）"
    )


def test_peach_may_be_used_in_the_near_death_window_even_at_full_hp_as_a_rescuer():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(PEACH[0],)), SeatSpec(hp=0)],
        deck=DECK,
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.PEACH, answers_seq=1, dying=1)
    peach = [action for action in actions if isinstance(action, UseCard)]

    assert [(action.card.card_id, action.targets) for action in peach] == [(PEACH[0], (1,))], (
        "出牌阶段【桃】要求自己未满体力；濒死窗口不受该前提约束（救的是别人）"
    )


def test_the_peach_behavior_declares_both_the_play_phase_and_the_near_death_contexts():
    behavior = behavior_for(PACK.deck.cards[PEACH[0]])

    assert [use.context for use in behavior.use_timings] == [
        UseContext.PLAY_PHASE,
        UseContext.NEAR_DEATH,
    ]


# ---------------------------------------------------------------------------
# 自救与他人救：回复结算插在濒死之下，完成后回到被中断的结算
# ---------------------------------------------------------------------------


def test_the_dying_character_may_save_itself_with_a_peach():
    engine = _run(hp_b=1, rescue_seat=1)
    queries = _queries(engine.records, "PEACH")

    assert [query.actor for query in queries] == [0, 1], (
        "第一个应答者（座位 0）放弃后才轮到濒死者自己"
    )
    assert engine.state.player(1).hp == 1, "【桃】把濒死者从 0 回复到 1"
    assert Timing.DEATH_BEFORE_START.value not in _timings(engine.records), (
        "自救成功：死亡结算帧根本不该出现"
    )


def test_the_current_turn_character_may_save_the_dying_one_and_asking_stops():
    """座位 0 先被问；它一出【桃】就脱离濒死，后面的应答者不再被问。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(SLASH[0], PEACH[0])), SeatSpec(hp=1)],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [_ScriptedPlayer(0, rescue=True, slash_budget=1), _ScriptedPlayer(1)], turns=1)

    assert [query.actor for query in _queries(engine.records, "PEACH")] == [0], (
        "官方 4.17：直到体力首次回复至 1 点或 1 点以上为止——座位 0 一出桃就结束"
    )
    assert engine.state.player(1).hp == 1
    assert Timing.DEATH_BEFORE_START.value not in _timings(engine.records)


def test_the_recover_frame_is_inserted_under_the_near_death_frame():
    engine = _run(hp_b=1, rescue_seat=1)
    pushes = [record for record in _of(engine.records, "frame") if record.op == "push"]
    near_death, recover, peach_use = pushes[4], pushes[5], pushes[6]

    assert recover.frame_kind is FrameKind.RECOVER
    assert recover.return_to == ("NEAR_DEATH", Timing.RECOVER_VALUE.value), (
        "回复结算插在濒死之下（票 21 第 3 条）"
    )
    assert peach_use.frame_kind is FrameKind.USE
    assert peach_use.return_to == ("RECOVER", Timing.NEAR_DEATH_AT.value), (
        "【桃】自己的使用链挂在回复帧之上，且'从哪回来'读得出是求桃窗口"
    )
    assert (near_death.depth, recover.depth, peach_use.depth) == (5, 6, 7)


def test_the_recover_frame_dispatches_its_two_timings_and_writes_the_hp_once():
    engine = _run(hp_b=1, rescue_seat=1)
    recover = _frame_ids(engine.records)[5]

    assert _timings(engine.records, frame_id=recover) == [
        Timing.RECOVER_VALUE.value,
        Timing.RECOVER_AFTER.value,
    ]
    changes = _hp_changes(engine.records, frame_id=recover)
    assert [
        (change.seat, change.before, change.after, change.amount) for change in changes
    ] == [(1, 0, 1, 1)], "濒死者被回复 1 点，体力变化事件归属回复帧"


def test_a_rescue_peach_is_recovered_exactly_once():
    engine = _run(hp_b=1, rescue_seat=1)
    recover_frames = [
        record.frame_id
        for record in _of(engine.records, "frame")
        if record.op == "push" and record.frame_kind is FrameKind.RECOVER
    ]

    assert len(recover_frames) == 1, "一次救援只写一次体力：行为表不再压第二个回复帧"
    assert len(_hp_changes(engine.records, frame_id=recover_frames[0])) == 1


def test_peach_used_in_the_play_phase_recovers_through_the_recover_frame():
    """场景 7：**出牌阶段主动**用【桃】——与濒死窗口两条回复路径互不相同。"""
    engine = play_phase_peach_engine()
    recover = next(
        record.frame_id
        for record in _of(engine.records, "frame")
        if record.op == "push" and record.frame_kind is FrameKind.RECOVER
    )
    emitted = _timings(engine.records)

    assert _timings(engine.records, frame_id=recover) == [
        Timing.RECOVER_VALUE.value,
        Timing.RECOVER_AFTER.value,
    ]
    assert [
        (change.seat, change.before, change.after, change.amount)
        for change in _hp_changes(engine.records, frame_id=recover)
    ] == [(0, 3, 4, 1)], "体力 3 → 4，变化事件归属回复帧"
    assert engine.state.player(0).hp == 4
    assert Timing.NEAR_DEATH_ENTER.value not in emitted, "未满体力时用【桃】不经过濒死"

    use_moves = [
        (record.from_zone.value, record.to_zone.value)
        for record in _of(engine.records, "delta")
        if record.delta_type == "card_move" and record.reason is MoveReason.USE
    ]
    assert use_moves == [("hand", "processing"), ("processing", "discard")], (
        "出牌阶段用【桃】照走手牌 → 处理区 → 弃牌堆两段移动"
    )


def test_play_phase_peach_is_not_offered_again_once_hp_is_full():
    """回复 1 点后回到**同一座位**的出牌阶段重新询问：满体力时【桃】不再在候选里。"""
    engine = play_phase_peach_engine()
    play_queries = [
        query for query in _queries(engine.records, "PLAY_PHASE") if query.actor == 0
    ]

    assert len(play_queries) >= 2, "用完【桃】后出牌阶段重新发问（票 07 §3）"
    assert any(
        action.kind == "UseCard" and action.card.type == "peach"
        for action in play_queries[0].legal_actions
    )
    assert not [
        action
        for action in play_queries[1].legal_actions
        if action.kind == "UseCard" and action.card.type == "peach"
    ]


def test_after_a_rescue_the_interrupted_resolution_continues():
    engine = _run(hp_b=1, rescue_seat=1)
    emitted = _timings(engine.records)

    assert Timing.NEAR_DEATH_AFTER.value in emitted
    assert Timing.LIFE_DECREASE_AFTER.value in emitted, "濒死帧出栈后回到被中断的扣减体力结算"
    for timing in DAMAGE_AFTERMATH_TIMINGS:
        assert timing in emitted, f"救回后伤害结算照常走完：{timing}"
    assert emitted.index(Timing.NEAR_DEATH_AFTER.value) < emitted.index(
        Timing.LIFE_DECREASE_AFTER.value
    )


def test_recovery_never_goes_above_the_hp_max():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(SLASH[0],)), SeatSpec(hp=1, hp_max=2, hand=(PEACH[0],))],
        deck=DECK,
        turn_seat=0,
    )
    # 座位 1：上限 2；被扣到 0 后自救回复 1 点，恰好落在上限内。
    engine = Engine.from_state(state, seed=1)
    run_turns(engine, [_ScriptedPlayer(0, slash_budget=1), _ScriptedPlayer(1, rescue=True)], turns=1)

    limit = engine.state.player(1).hp_max
    changes = _hp_changes(engine.records)
    assert changes, "这条局面必须真的扣过、回复过体力"
    for change in changes:
        assert change.after <= engine.state.player(change.seat).hp_max, "回复值受体力上限限制"
    assert [
        (change.before, change.after, change.amount)
        for change in changes
        if change.seat == 1
    ] == [(1, 0, -1), (0, 1, 1)], "座位 1 先被扣到 0、再被回复 1 点，恰好落在上限内"


# ---------------------------------------------------------------------------
# 无人救的终局：死亡四段与清栈收尾的逐条断言见 tests/test_death.py
# ---------------------------------------------------------------------------


def test_nobody_rescues_so_the_game_ends_in_victory():
    engine = _run(hp_b=1, rescue_seat=None)

    assert engine.state.player(1).hp == 0
    assert engine.finished, "无人救 ⇒ 死亡结算跑完即终局（票 22）"
    assert engine.records[-1].kind == "game_end"
    assert engine.records[-1].result == "winner"
    assert engine.records[-1].winner == 0, "1v1：一方阵亡即另一方获胜"


def test_hp_below_zero_only_happens_while_the_near_death_frame_is_on_the_stack():
    """票 11 §79 的体力范围不变量：``hp < 0`` 仅允许出现在濒死帧在栈上时。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(SLASH[0],)), SeatSpec(hp=1, hand=(PEACH[0],))],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    players = [_ScriptedPlayer(0), _ScriptedPlayer(1, rescue=True)]

    # 重洗落地后（票 23）被动局面不再自然收局：第一个回合走完（濒死链在其中）即停。
    for _ in range(20_000):
        if engine.pending is None:
            engine._step()
            if engine.finished or _turn_finished(engine):
                break
            continue
        on_stack = any(frame.kind is FrameKind.NEAR_DEATH for frame in engine.stack.frames)
        if not on_stack:
            for player in engine.state.players:
                assert player.hp >= 0, "没有濒死帧在栈上时体力不得为负"
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
        if engine.finished or _turn_finished(engine):
            break

    for player in engine.state.players:
        assert player.hp >= 0


def _turn_finished(engine) -> bool:
    return any(
        record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
        for record in engine.records
    )


def test_the_peach_query_view_hides_the_opponents_hand_identities():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hand=(SLASH[0],)),
            SeatSpec(hp=1, hand=(PEACH[0], PEACH[1])),
        ],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    reached = _advance_to_first_peach(
        engine, players=[_ScriptedPlayer(0), _ScriptedPlayer(1)]
    )
    assert reached and engine.pending is not None

    query = _queries(engine.records, "PEACH")[0]
    view = query.view
    assert isinstance(view, PlayerView)
    assert view.seat == query.actor
    assert [card.card_id for card in view.self_hand] == [
        card_id for card_id in engine.state.player(query.actor).hand
    ], "本视角的手牌 = 应答者自己的手牌，身份完整可见"
    assert all(card.card_id not in (PEACH[0], PEACH[1]) for card in view.self_hand), (
        "别人手里的【桃】不得出现在本视角的手牌里"
    )
    opponent = next(player for player in view.players if player.seat == 1 - query.actor)
    assert opponent.hand_count == len(engine.state.player(1 - query.actor).hand), (
        "对手只暴露手牌数，不暴露身份"
    )
    assert not hasattr(view, "deck_order"), "牌堆只给剩余数量、不给顺序"


def test_an_illegal_peach_answer_is_rejected_without_touching_the_state():
    """票 05 §3：不在候选集合内的应答被拒、零状态改动。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=1, hand=(DODGE[0],)),
            SeatSpec(hp=3, hand=(SLASH[0],)),
        ],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    reached = _advance_to_first_peach(
        engine, players=[_ScriptedPlayer(0), _ScriptedPlayer(1, slash_budget=1)]
    )

    assert reached and engine.pending is not None and engine.pending.kind == "PEACH"
    assert [action.kind for action in engine.pending.legal_actions] == ["Pass"], (
        "座位 0 手里只有【闪】，濒死窗口对它的唯一候选是放弃"
    )
    before = [player.hp for player in engine.state.players]
    with pytest.raises(IllegalActionError):
        engine.submit(
            UseCard(
                card=engine.state.card(DODGE[0]),
                targets=(engine.pending.dying,),
                answers_seq=engine.pending.seq,
            )
        )

    assert [player.hp for player in engine.state.players] == before
