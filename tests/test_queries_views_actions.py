"""询问、候选、行动与参与者视角（票 05 / 票 07 的管道半边 + 票 24 的校验边界）。

落点 = 库门面产出的 ``query`` 记录：候选**内联**、唯一 / 空候选仍发询问、
排序键稳定、视角只露自己的手牌。

校验边界（票 24）也落在这里：**三类非法输入**（结构损坏 / 序号陈旧 / 不在候选集合内）
各被拒绝且零状态改动、被拒应答记 ``accepted = false``、拒绝后不重试不超时。
"""

from __future__ import annotations

import json
from itertools import combinations

import pytest

from sgs.agents import tape_from_journal
from sgs.data import load_content_pack
from sgs.effects import legal_actions_for
from sgs.engine import Engine, IllegalActionError, MalformedAction, dump_journal, read_journal
from sgs.state import (
    DiscardQuery,
    EndPhase,
    Pass,
    Phase,
    PlayPhaseQuery,
    PlayerPublic,
    PlayerView,
    QueryKind,
    SelectOptionContext,
    UseCard,
    project,
)
from sgs.timing import Timing

from support import (
    RecordingPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    run_turns,
    run_until,
)

PACK = load_content_pack(deck_profile="basic_53")
"""M1 回归网显式选中 53 张档（票 16 §9）：``HAND_A`` 的牌 id 锚在旧切片上。"""
HAND_A = (40, 41, 42, 43)
HAND_B = (46, 47, 48, 49)
DECK = (30, 31)
"""牌堆给两张【闪】：座位 A 摸到后出牌阶段仍无可用牌，只有"结束阶段"（票 12 场景 9）。"""
HAND_A_AFTER_DRAW = HAND_A + DECK

SLASH = cards_of_type(PACK, "slash")
PEACH = cards_of_type(PACK, "peach")


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
    run_turns(engine, [RecordingPlayer(), RecordingPlayer()], turns=1)
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
    run_turns(engine, players, turns=1)

    assert players[0].seen[0].legal_actions == _queries(engine, "PLAY_PHASE")[0].legal_actions


# ---------------------------------------------------------------------------
# 候选排序键：牌实例 id 升序 → 行动类型名 → 无牌实例最后（票 05 §4 / 票 24）
# ---------------------------------------------------------------------------


def test_candidates_are_ordered_by_card_id_with_the_cardless_action_last():
    # 座位 0 未满血 ⇒ 两张【杀】与一张【桃】都是候选；手牌故意乱序，候选仍按牌实例 id 升序。
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=3, hand=(PEACH[0], SLASH[1], SLASH[0])),
            SeatSpec(hp=4, hand=HAND_B),
        ],
        deck=DECK,
        turn_seat=0,
    )

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=9)

    card_ids = [action.card.card_id for action in actions if isinstance(action, UseCard)]
    assert card_ids == sorted((SLASH[0], SLASH[1], PEACH[0])), "牌实例 id 升序"
    assert actions[-1].kind == "EndPhase", "无牌实例的 EndPhase 排最后"
    assert all(action.answers_seq == 9 for action in actions)


# ---------------------------------------------------------------------------
# 视角过滤：公开区按置入序，牌堆只有数量（票 05 §5 / 票 24）
# ---------------------------------------------------------------------------


def test_player_view_reads_public_zones_in_insertion_order_and_never_the_deck_order():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=4, hand=HAND_A),
            SeatSpec(hp=3, hand=HAND_B, equip=(26,), judge=(27, 28)),
        ],
        deck=(20, 21, 22),
        discard=(23, 24),
        processing=(25,),
        turn_seat=1,
        phase=Phase.DISCARD,
    )

    view = project(state, 0)

    assert [card.card_id for card in view.discard] == [23, 24], "弃牌堆按置入序（先入在前）"
    assert [card.card_id for card in view.processing] == [25], "处理区是公开区，按置入序"
    assert [card.card_id for card in view.players[1].equip] == [26], "装备区公开且逐座位"
    assert [card.card_id for card in view.players[1].judge] == [27, 28], "判定区先入在前"
    assert view.deck_count == 3
    assert not hasattr(view, "deck_order"), "牌堆只给剩余数量，不给顺序"
    assert [player.hand_count for player in view.players] == [len(HAND_A), len(HAND_B)]
    assert view.players[1].hp == 3 and view.players[1].hp_max == 4
    assert all(card.card_id not in HAND_B for card in view.self_hand)
    assert "deck" not in PlayerView.model_fields and "opponent_hand" not in PlayerView.model_fields
    assert "hand_limit" not in PlayerPublic.model_fields, "手牌上限由当前体力值派生，不落字段"


# ---------------------------------------------------------------------------
# 三类非法输入：拒绝 + 零状态改动 + accepted = false（票 07 §5 / 票 24）
# ---------------------------------------------------------------------------


def _parked_at_first_query(*, hand_a=HAND_A, hand_b=HAND_B, hp_a=4):
    """跑第一个询问前停下（``pending`` 原样留着），好让测试亲手投喂应答。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=hp_a, hand=hand_a), SeatSpec(hp=4, hand=hand_b)],
        deck=DECK,
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_until(engine, [RecordingPlayer(), RecordingPlayer()], lambda e: e.pending is not None)
    assert engine.pending is not None
    return engine


def _game_snapshot(engine):
    """棋盘 + 结算栈的完整快照：被拒应答必须让它**逐字段不变**。"""
    return engine.state.model_dump(), engine.stack.depth, engine.finished


def test_structure_damaged_responses_are_malformed_and_change_nothing():
    engine = _parked_at_first_query()
    query = engine.pending
    baseline = _game_snapshot(engine)
    responses = [
        {"kind": "Frobnicate", "answers_seq": query.seq},
        {"kind": "Pass", "answers_seq": query.seq, "extra": 1},
        {"kind": "Pass"},
        ["Pass"],
        "garbage",
        None,
    ]

    for response in responses:
        before_count = len(engine.records)
        with pytest.raises(IllegalActionError) as caught:
            engine.submit(response)

        assert caught.value.reason_code == "MalformedAction"
        assert _game_snapshot(engine) == baseline, "结构损坏的应答必须零状态改动"
        assert engine.pending is query, "被拒后仍是同一个询问槽位"
        assert len(engine.records) == before_count + 1

        rejected = engine.records[-1]
        assert rejected.kind == "action" and rejected.accepted is False
        assert isinstance(rejected.action, MalformedAction)
        assert rejected.reason is not None and "结构损坏" in rejected.reason


def test_the_malformed_record_keeps_the_offending_payload_and_its_first_issue():
    engine = _parked_at_first_query()
    query = engine.pending

    with pytest.raises(IllegalActionError):
        engine.submit({"kind": "Frobnicate", "answers_seq": query.seq})

    rejected = engine.records[-1].action
    assert isinstance(rejected, MalformedAction)
    assert json.loads(rejected.raw) == {"kind": "Frobnicate", "answers_seq": query.seq}
    assert rejected.answers_seq == query.seq
    assert "kind" in rejected.detail, "结构错误的首条路径进记录，便于定位"


def test_a_stale_answers_seq_is_rejected_and_changes_nothing():
    engine = _parked_at_first_query()
    query = engine.pending
    baseline = _game_snapshot(engine)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(Pass(answers_seq=query.seq + 1))

    assert caught.value.reason_code == "StaleAction"
    assert caught.value.answers_seq == query.seq + 1
    assert _game_snapshot(engine) == baseline
    assert engine.pending is query
    rejected = engine.records[-1]
    assert rejected.accepted is False and rejected.reason == "answers_seq 陈旧"
    assert isinstance(rejected.action, Pass), "合法形状的应答原样留在记录里"


def test_an_answer_outside_the_candidate_set_is_rejected_and_changes_nothing():
    engine = _parked_at_first_query()  # 手里只有【闪】⇒ 唯一候选是 EndPhase
    query = engine.pending
    assert [action.kind for action in query.legal_actions] == ["EndPhase"]
    baseline = _game_snapshot(engine)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(Pass(answers_seq=query.seq))

    assert caught.value.reason_code == "IllegalAction"
    assert _game_snapshot(engine) == baseline
    assert engine.pending is query
    rejected = engine.records[-1]
    assert rejected.accepted is False and rejected.reason == "不在候选集合内"
    assert rejected.action == Pass(answers_seq=query.seq)


def test_a_tampered_target_is_illegal_because_validation_is_membership():
    engine = _parked_at_first_query(hand_a=(SLASH[0],), hand_b=())
    query = engine.pending
    candidate = next(
        action
        for action in query.legal_actions
        if isinstance(action, UseCard) and action.card.card_id == SLASH[0]
    )
    assert candidate.targets == (1,), "1v1 的额定目标就是对方"
    baseline = _game_snapshot(engine)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(candidate.model_copy(update={"targets": (query.actor,)}))

    assert caught.value.reason_code == "IllegalAction", (
        "同一张牌换成非法目标即不在候选集合内——校验是成员判定，不只看牌名"
    )
    assert _game_snapshot(engine) == baseline
    assert engine.pending is query


def test_a_rejected_answer_is_not_retried_and_the_same_query_stays_answerable():
    engine = _parked_at_first_query()
    query = engine.pending
    asked_before = [record.seq for record in engine.records if record.kind == "query"]

    with pytest.raises(IllegalActionError):
        engine.submit(Pass(answers_seq=query.seq))

    assert [record.seq for record in engine.records if record.kind == "query"] == asked_before, (
        "校验失败不重试：引擎不自己重发询问，也不超时代答"
    )
    assert engine.pending is query
    engine.submit(EndPhase(answers_seq=query.seq))
    assert engine.pending is None
    assert engine.records[-1].accepted is True, "同一个询问槽位接受合法应答"


def test_the_three_rejected_records_survive_the_canonical_round_trip(tmp_path):
    engine = _parked_at_first_query()
    query = engine.pending
    for response in (
        {"kind": "Frobnicate", "answers_seq": query.seq},
        Pass(answers_seq=query.seq + 1),
        Pass(answers_seq=query.seq),
    ):
        with pytest.raises(IllegalActionError):
            engine.submit(response)

    text = engine.journal()
    path = tmp_path / "rejected.jsonl"
    path.write_text(text, encoding="utf-8")

    records = read_journal(path)

    assert dump_journal(records) == text, "写读对称（含结构损坏的拒绝记录）"
    rejected = [record for record in records if record.kind == "action" and not record.accepted]
    assert [record.action.kind for record in rejected] == [
        "MalformedAction",
        "Pass",
        "Pass",
    ]
    _, tape = tape_from_journal(records)
    assert tape == (), "被拒应答不进入回放 tape"


# ---------------------------------------------------------------------------
# 校验边界命中检查：同一回合第二次【杀】不在候选（票 07 §4 / 票 24）
# ---------------------------------------------------------------------------


def test_a_second_slash_in_the_same_turn_is_not_a_candidate():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hand=(SLASH[0], SLASH[1])), SeatSpec(hp=4, hand=())],
        deck=(30, 31),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)

    class _SlashOnceThenEnd:
        def respond(self, query):
            if query.kind == "PLAY_PHASE":
                slash = next(
                    (
                        action
                        for action in query.legal_actions
                        if isinstance(action, UseCard) and action.card.type == "slash"
                    ),
                    None,
                )
                if slash is not None:
                    return slash
                return next(action for action in query.legal_actions if isinstance(action, EndPhase))
            if query.kind == "DODGE":
                return next(action for action in query.legal_actions if action.kind == "Pass")
            return query.legal_actions[0]

    # 跑到座位 0 的**第二个**出牌阶段询问为止。
    run_until(
        engine,
        [_SlashOnceThenEnd(), _SlashOnceThenEnd()],
        lambda e: sum(
            1
            for record in e.records
            if record.kind == "query" and record.query.kind == "PLAY_PHASE"
        )
        >= 2,
    )

    second = engine.pending
    assert second is not None and second.kind == "PLAY_PHASE"
    assert [action.kind for action in second.legal_actions] == ["EndPhase"], (
        "本回合已用过额定 1 张【杀】：剩下的那张【杀】不再进候选"
    )
    assert not any(isinstance(action, UseCard) for action in second.legal_actions)


# ---------------------------------------------------------------------------
# SELECT_OPTION 的可取消形态（票 00 / N4）：缺省强制，optional=True 才多一条 Pass
# ---------------------------------------------------------------------------


def test_select_option_is_forced_by_default_and_offers_pass_when_optional():
    """「取消选择流程」需要 ``Pass``，而既有 SELECT_OPTION 是强制闭集。

    故取消能力做成**显式开关**：``optional`` 缺省 ``False`` ⟹ 既有消费者（雌雄双股剑、
    遗计"交给谁"）候选一条不多、一条不少。
    """
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], deck=DECK, turn_seat=0)

    forced = legal_actions_for(
        state,
        0,
        QueryKind.SELECT_OPTION,
        answers_seq=9,
        payload=SelectOptionContext(skill_id="demo", options=("a", "b")),
    )
    assert [action.kind for action in forced] == ["SelectOption", "SelectOption"], (
        "缺省强制：闭集两选项，没有 Pass"
    )

    cancelable = legal_actions_for(
        state,
        0,
        QueryKind.SELECT_OPTION,
        answers_seq=9,
        payload=SelectOptionContext(skill_id="demo", options=("a", "b"), optional=True),
    )
    kinds = [action.kind for action in cancelable]
    assert kinds == ["SelectOption", "SelectOption", "Pass"], (
        "optional=True：多一条 Pass，且「放弃」恒排在真候选**之后**——取首个候选的贪心"
        "参与者（``tests/test_web.py::_play_to_end`` 就是一例）才不会一直挑取消；"
        "``_select_cards`` / ``_skill_activation`` 早已是这个次序，只有 ``SELECT_OPTION`` "
        "曾因 ``_sort_key`` 缺分支而把 Pass 排到首位"
    )
    assert all(action.answers_seq == 9 for action in cancelable)
