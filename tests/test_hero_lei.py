"""神话再临·雷包：郝昭【镇骨】（01 票；lei 包脚手架的首个载体）。

牌面（用户录入 `D:\\SGS_HEROS\\神话再临-雷包\\郝昭\\技能.txt`，逐字）：

    镇骨
    　结束阶段，你可以选择一名其他角色，你的回合结束时和该角色的下个回合结束时，
      其将手牌摸至或弃置至与你手牌数相同（摸牌时至多摸至五张）。

用户 2026-11-05 签的裁定（`.scratch/lei-batch/issues/90-zhengku-open-questions.md`）：

- Q1 = A：弃哪些牌由**目标自己**选（``SELECT_CARDS`` 的 ``actor`` = 目标）；
- Q2 = A：「至多摸至五张」= **手牌终值上限 5**（同【节命】）；
- Q3 = A：标记来源阵亡 ⇒ 标记清除（``clear_on_source_death``）、该次结算不发生；
- Q4 = A：目标的"下个回合"被跳过（翻面）⇒ 顺延到下一个真实执行的回合末；
- Q5 = A：郝昭在自己回合内阵亡 ⇒ 第 1 次结算不发生。

标记记法：一枚「镇骨」标记、``count = 2`` = 两次结算都欠着；你的回合末只减 1，
目标的回合末清空（见 `src/sgs/skills/hero_lei.py` 的模块头）。
"""

from __future__ import annotations

from sgs.data import load_content_pack
from sgs.engine import Engine
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    Phase,
    SelectCards,
    SelectOption,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, run_queries

PACK = load_content_pack(deck_profile="standard_108")
ZHENGKU = "hero.haozhao.zhengku"
HERO = "haozhao"
MARK = "镇骨"


class Scripted:
    """按座位脚本应答：是否发动【镇骨】、选谁、弃牌步挑几张。"""

    def __init__(
        self,
        *,
        activate: bool = False,
        seat_choice: str | None = None,
        pick_size: int | None = None,
    ) -> None:
        self.activate = activate
        self.seat_choice = seat_choice
        self.pick_size = pick_size
        self.queries: list = []
        self.selections: list = []
        self.options: list = []

    def respond(self, query):
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "SKILL_ACTIVATION" and str(query.skill_id) == ZHENGKU:
            self.queries.append(query)
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if kind == "PLAY_PHASE":
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if kind == "SELECT_OPTION":
            self.options.append(query)
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and (
                    str(action.choice) == self.seat_choice
                ):
                    return action
            return query.legal_actions[0]
        if kind == "SELECT_CARDS":
            self.selections.append(query)
            for action in query.legal_actions:
                if isinstance(action, SelectCards) and len(action.picks) == self.pick_size:
                    return action
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SelectCards)
            )
        return query.legal_actions[0]


def _state(
    *,
    owner_hand: tuple[int, ...],
    target_hand: tuple[int, ...] = (),
    third_hand: tuple[int, ...] = (),
    owner_hp: int = 4,
    owner_hp_max: int = 4,
    target_hp: int = 4,
    target_alive: bool = True,
    seats: int = 2,
):
    used = {*owner_hand, *target_hand, *third_hand}
    specs = [
        SeatSpec(hero=HERO, hp=owner_hp, hp_max=owner_hp_max, hand=owner_hand),
        SeatSpec(hp=target_hp, hp_max=target_hp, hand=target_hand, alive=target_alive),
    ]
    if seats >= 3:
        specs.append(SeatSpec(hp=4, hp_max=4, hand=third_hand))
    deck = tuple(card_id for card_id in range(60, 108) if card_id not in used)
    return build_state(pack=PACK, seats=specs, deck=deck, turn_seat=0, phase=Phase.PLAY)


def _engine(
    state,
    *,
    activate: bool,
    seat_choice: str | None = "seat:1",
    pick_size: int | None = None,
    target_pick_size: int | None = None,
):
    engine = Engine.from_state(state, seed=7)
    owner = Scripted(activate=activate, seat_choice=seat_choice, pick_size=pick_size)
    others = [
        Scripted(
            activate=False,
            seat_choice=seat_choice,
            pick_size=target_pick_size,
        )
        for _ in range(len(state.players) - 1)
    ]
    return engine, [owner, *others]


def _markers(engine, seat: int) -> list:
    return [
        marker
        for marker in engine.state.player(seat).markers
        if marker.name == MARK and marker.source_seat == 0
    ]


def _marker_counts(engine) -> list[tuple[int, int]]:
    """记录流里的「镇骨」标记变化：``(座位, 绝对值 count)``。"""
    return [
        (record.seat, record.count)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "marker_change"
        and record.name == MARK
    ]


def _completed_turns(engine) -> int:
    return sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.TURN_AFTER_END
    )


def _run_turns(engine, players, *, turns: int = 1):
    """跑到 ``turns`` 个**完整回合**（``TURN_AFTER_END``）为止，不跨进下一回合。"""
    run_queries(engine, players, lambda e: _completed_turns(e) >= turns)


def test_zhengku_marks_the_target_with_two_owed_settlements():
    """发起：结束阶段选一名其他角色 ⇒ 给他挂「镇骨」，count = 2（两次结算都欠着）。"""
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,)), activate=True
    )
    _run_turns(engine, players, turns=1)

    assert len(players[0].queries) == 1, "结束阶段问一次「是否发动」"
    assert _marker_counts(engine) == [(1, 2), (1, 1)], "挂 2、我的回合末减到 1"
    assert _markers(engine, 1)[0].count == 1
    assert _markers(engine, 1)[0].clear_on_source_death is True, "Q3：来源阵亡即清除"


def test_declining_zhengku_leaves_no_marker_and_no_adjustment():
    """不发动 ⇒ 没有标记、目标一张不摸。"""
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,)), activate=False
    )
    _run_turns(engine, players, turns=1)

    assert len(players[0].queries) == 1
    assert _marker_counts(engine) == []
    assert len(engine.state.player(1).hand) == 1, "目标一张不摸"


def test_first_settlement_draws_only_the_deficit_at_the_owners_turn_end():
    """第 1 次结算（你的回合末）：目标摸到与你相同的张数。"""
    # 郝昭 2 张 → 摸牌阶段 +2 = 4 张；目标 1 张 ⇒ 摸 3 张。
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,)), activate=True
    )
    _run_turns(engine, players, turns=1)

    owner_hand = len(engine.state.player(0).hand)
    assert owner_hand == 4, "构造：2 + 摸牌阶段 2"
    assert len(engine.state.player(1).hand) == owner_hand, "目标摸到与之相同"


def test_second_settlement_happens_at_the_targets_own_turn_end_and_clears_the_marker():
    """第 2 次结算（该角色的下个回合结束时）：再调整一次，标记清空。"""
    # 目标体力上限 8 ⇒ 它的弃牌阶段不掷牌，回合末手牌 2（摸牌阶段 2 张）< 郝昭 4。
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(), target_hp=8), activate=True
    )
    _run_turns(engine, players, turns=2)

    assert _marker_counts(engine) == [(1, 2), (1, 1), (1, 0)], "两次结算后清空"
    assert _markers(engine, 1) == [], "标记已清除"
    assert len(engine.state.player(1).hand) == len(engine.state.player(0).hand)


def test_the_target_chooses_which_cards_to_discard():
    """弃置支路（Q1 = A）：弃哪些牌由**目标自己**选，不是郝昭代选。"""
    # 郝昭 1 张 → 摸牌阶段 +2 = 3 张；目标 4 张 ⇒ 目标自己弃 1 张。
    engine, players = _engine(
        _state(owner_hand=(0,), target_hand=(10, 11, 12, 13)),
        activate=True,
        target_pick_size=1,
    )
    _run_turns(engine, players, turns=1)

    assert len(players[1].selections) >= 1, "选牌询问发给了目标（座位 1）"
    assert players[0].selections == [], "郝昭没有被问（不代选）"
    assert len(engine.state.player(1).hand) == 3, "目标弃到与郝昭相同"


def test_draw_cap_is_a_final_hand_size_of_five():
    """上限口径（Q2 = A）：「至多摸至五张」= 手牌**终值** ≤ 5。"""
    # 郝昭 5 张、体力上限 8（弃牌阶段不掷牌）→ 摸牌 +2 = 7 张；目标 1 张 ⇒ 只摸到 5。
    engine, players = _engine(
        _state(
            owner_hand=(0, 1, 2, 3, 4),
            target_hand=(10,),
            owner_hp=8,
            owner_hp_max=8,
            target_hp=8,
        ),
        activate=True,
    )
    _run_turns(engine, players, turns=1)

    assert len(engine.state.player(0).hand) == 7, "构造：5 + 2"
    assert len(engine.state.player(1).hand) == 5, "摸牌终值封顶 5，不是摸 5 张"


def test_no_activation_question_when_no_other_character_is_alive():
    """池空 ⇒ 连「是否发动」都不问（不发没有候选的询问）。"""
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,), target_alive=False),
        activate=True,
    )
    _run_turns(engine, players, turns=1)

    assert players[0].queries == [], "没有其他存活角色 ⇒ 不问"
    assert _marker_counts(engine) == []


def test_an_unrelated_turn_end_does_not_settle():
    """别人的回合末不结算：标记只在**你自己**与**目标**的回合末兑现。"""
    # 三个座位、目标 = 座位 2：座位 1 的回合末不该动它。
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,), third_hand=(20,), seats=3),
        activate=True,
        seat_choice="seat:2",
    )
    _run_turns(engine, players, turns=2)  # 我的回合 + 座位 1 的回合

    assert _marker_counts(engine) == [(2, 2), (2, 1)], "座位 1 的回合末不结算"
    assert _markers(engine, 2), "标记还在（等座位 2 的回合末）"

    _run_turns(engine, players, turns=3)  # 座位 2 的回合
    assert _marker_counts(engine) == [(2, 2), (2, 1), (2, 0)], "目标的回合末结算并清空"


def test_the_second_settlement_is_skipped_when_the_targets_turn_is_skipped():
    """Q4 = A：目标的回合被翻面跳过 ⇒ 该轮没有回合结束，标记留着等真回合。"""
    engine, players = _engine(
        _state(owner_hand=(0, 1), target_hand=(10,)), activate=True
    )
    _run_turns(engine, players, turns=1)
    assert [count for _seat, count in _marker_counts(engine)] == [2, 1]
    # 之后不再发动（否则第二个回合末会挂新账，混进来）。
    players[0].activate = False
    # 把目标翻面 ⇒ 它的下个回合整轮跳过（`TURN_BEFORE_START` 翻回正面、不发 TURN_END）。
    engine.set_flipped(None, 1, True)
    _run_turns(engine, players, turns=2)  # 跳过座位 1 的回合 → 走到座位 0 的下个回合末
    assert [count for _seat, count in _marker_counts(engine)] == [2, 1], (
        "目标回合被跳过时不在我的下个回合末补结"
    )
    assert _markers(engine, 1), "标记留着，等目标的下一个真实回合末"

    # 目标的下一个**真实**回合末仍然兑现（翻回正面之后的那一轮）。
    _run_turns(engine, players, turns=3)
    assert [count for _seat, count in _marker_counts(engine)] == [2, 1, 0]
    assert _markers(engine, 1) == []
