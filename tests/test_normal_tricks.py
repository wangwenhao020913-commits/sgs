"""票 27：多目标结算与普通锦囊（库门面主缝）。

断言全部落在**记录流与棋盘状态**上：时机 / delta / query / action、体力、区域、手牌数。
不碰帧私有 ``data`` / ``pc``，也不碰注册表内部结构。

覆盖三组行为：

1. **多目标两段式**：B 段"时机外层、目标内层"、C 段一个 ``RESOLVE`` 帧 + 游标逐目标、
   目标顺序"从当前回合角色起逆时针"、目标减为 0 ⇒ 终止使用流程；
2. **"无效"的层次**：桃园结义对未受伤者仍被指定、到 C1 终止（故没有后续结算）；
3. **普通锦囊**：无中生有 / 过河拆桥 / 顺手牵羊 / 决斗 / 借刀杀人 / 南蛮入侵 / 万箭齐发 /
   五谷丰登，以及 RESPOND / SELECT_CARDS 两类询问的选择面。

场景用 ``tests/support/positions.py`` 的纯函数构造局面，由库 API 驱动到目标段落完成即止
（被动局面不会自然终局）。构造局面里的人数为测试便利，``RuleConfig`` 仍是合法配置。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, IllegalActionError
from sgs.state import (
    EndPhase,
    FrameKind,
    Pass,
    PlayCard,
    PlayPhaseQuery,
    QueryKind,
    SelectCards,
    StepPool,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support.positions import (
    RecordingPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    run_until,
)

STANDARD = "standard_108"


@pytest.fixture(scope="module")
def pack():
    return load_content_pack(deck_profile=STANDARD)


# ---------------------------------------------------------------------------
# 驱动：一个座位一个应答器，命中目标牌后其余只走"结束阶段 / 放弃"
# ---------------------------------------------------------------------------


@dataclass
class _Plan:
    play: str
    respond: str = "pass"
    """RESPOND 窗口的应答：``play`` = 打出第一张候选、``use`` = 使用第一张候选、
    ``pass`` = 放弃。"""

    pick: int = 0
    use_target: int | None = None
    designations: tuple[int, ...] = ()
    """借刀杀人的**附带指定** Bn（W5-02b）：入口只剩 ``designation_actions`` 池。"""

    played: bool = field(default=False)


class _Planned:
    def __init__(self, plan: _Plan) -> None:
        self.plan = plan

    def respond(self, query):
        plan = self.plan
        if query.kind == QueryKind.PLAY_PHASE:
            if not plan.played:
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and action.card.type == plan.play:
                        plan.played = True
                        return action
                # 借刀杀人的裸候选已收回（W5-02b）：合法动作是"牌 + A + Bn"三元组，
                # 只在询问的**内部池**里；测试脚本与真实入口同一条路。
                for action in getattr(query, "designation_actions", ()):
                    if (
                        isinstance(action, UseCard)
                        and action.card.type == plan.play
                        and action.designations == plan.designations
                    ):
                        plan.played = True
                        return action
            return _find(query, EndPhase)
        if query.kind == QueryKind.RESPOND:
            if plan.respond == "play":
                card = _first(query, PlayCard)
                if card is not None:
                    return card
            if plan.respond == "use":
                for action in query.legal_actions:
                    if isinstance(action, UseCard) and (
                        plan.use_target is None or action.targets == (plan.use_target,)
                    ):
                        return action
            return _find(query, Pass)
        if query.kind == QueryKind.SELECT_CARDS:
            return query.legal_actions[plan.pick]
        if query.kind in (QueryKind.DODGE, QueryKind.PEACH):
            return _find(query, Pass)
        return query.legal_actions[0]


def _first(query, action_type):
    for action in query.legal_actions:
        if isinstance(action, action_type):
            return action
    return None


def _find(query, action_type):
    action = _first(query, action_type)
    assert action is not None, f"{query.kind} 询问里没有 {action_type.__name__} 候选"
    return action


def _deck_excluding(pack, *card_ids: int) -> tuple[int, ...]:
    """除被指定的牌之外的全部牌实例（与手牌 / 装备区 / 判定区不相交）。"""
    excluded = set(card_ids)
    return tuple(index for index in range(len(pack.deck.cards)) if index not in excluded)


def _run_trick(
    pack,
    *,
    play: str,
    seats: list[SeatSpec],
    deck: tuple[int, ...] | None = None,
    turn_seat: int = 0,
    seed: int = 1,
    respond: str = "pass",
    pick: int = 0,
    use_target: int | None = None,
    designations: tuple[int, ...] = (),
) -> tuple[Engine, _Plan]:
    """构造局面 → 跑到"这张牌的使用结算结束后"即止。"""
    plan = _Plan(
        play=play,
        respond=respond,
        pick=pick,
        use_target=use_target,
        designations=designations,
    )
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=deck if deck is not None else _deck_excluding(pack, *used),
        turn_seat=turn_seat,
        rule_config=RuleConfig(first_seat=turn_seat),
    )
    engine = Engine.from_state(state, seed=seed)
    players = [_Planned(plan) for _ in seats]

    run_until(engine, players, lambda e: plan.played and _use_settled(e, play))
    return engine, plan


def _use_settled(engine: Engine, play: str) -> bool:
    return any(
        record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and any(card.type == play for card in record.cards)
        for record in engine.records
    )


def _timings(engine: Engine, timing: Timing) -> list:
    return [r for r in engine.records if r.kind == "timing" and r.timing is timing]


def _queries(engine: Engine, kind: QueryKind) -> list:
    return [r.query for r in engine.records if r.kind == "query" and r.query.kind == kind]


def _windows(engine: Engine, cause: WindowCause) -> list:
    """某成因的响应窗口；票 28 起每张锦囊的"生效前"还会开一层无懈窗口，故按成因取。"""
    return [query for query in _queries(engine, QueryKind.RESPOND) if query.cause is cause]


def _deltas(engine: Engine, delta_type: str) -> list:
    return [r for r in engine.records if r.kind == "delta" and r.delta_type == delta_type]


def _resolve_frame_id(engine: Engine) -> int:
    return next(
        record.frame_id
        for record in engine.records
        if record.kind == "frame" and record.frame_kind.value == "RESOLVE"
    )


# ---------------------------------------------------------------------------
# 协议扩充
# ---------------------------------------------------------------------------


def test_the_nine_query_kinds_and_the_window_causes_are_defined():
    """票 30 把 ``QueryKind`` 从 6 类扩到 8 类（+ 发动 / 选项），票 10 再加 ``ARRANGE``（排列）；
    M1 四类与票 27 两类不动（闭集只许增，与 ``WindowCause`` 同一姿态）。"""
    assert {kind.value for kind in QueryKind} == {
        "PLAY_PHASE",
        "DODGE",
        "PEACH",
        "DISCARD",
        "RESPOND",
        "SELECT_CARDS",
        "SKILL_ACTIVATION",
        "SELECT_OPTION",
        "ARRANGE",
    }
    assert {cause.value for cause in WindowCause} == {
        "NULLIFY",
        "BARBARIAN_INVASION",
        "ARROW_BARRAGE",
        "DUEL",
        "BORROWED_SWORD",
        "LORD_REQUEST",
        "REJUDGE",
    }


def test_play_card_and_select_cards_parse_through_the_single_entry():
    from sgs.state import CardRef, parse_action

    slash = CardRef(card_id=0, type="slash", name="杀", suit="spade", rank=7)
    played = parse_action({"kind": "PlayCard", "card": slash.model_dump(), "answers_seq": 3})
    picked = parse_action({"kind": "SelectCards", "picks": [1], "answers_seq": 4})

    assert isinstance(played, PlayCard) and played.answers_seq == 3
    assert isinstance(picked, SelectCards) and picked.picks == (1,)


# ---------------------------------------------------------------------------
# 第 0 层合法性（目标集合）
# ---------------------------------------------------------------------------


def test_dismantlement_needs_cards_in_the_targets_zones(pack):
    from sgs.effects import check_targets

    dismantle = cards_of_type(pack, "dismantlement")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(hand=(cards_of_type(pack, "slash")[0],)), SeatSpec()],
        turn_seat=0,
        rule_config=RuleConfig(),
    )

    assert check_targets(state, 0, state.card(dismantle)) == (1,)


def test_snatch_is_capped_at_distance_one(pack):
    from sgs.effects import check_targets

    snatch = cards_of_type(pack, "snatch")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(hand=(1,)), SeatSpec(hand=(2,)), SeatSpec(hand=(3,))],
        turn_seat=0,
        rule_config=RuleConfig(),
    )

    assert check_targets(state, 0, state.card(snatch)) == (1, 3)


def test_borrowed_sword_needs_a_weapon_and_a_legal_slash_target(pack):
    from sgs.effects import check_targets

    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(), SeatSpec(equip=(weapon,)), SeatSpec(), SeatSpec()],
        turn_seat=0,
        rule_config=RuleConfig(),
    )

    assert check_targets(state, 0, state.card(borrowed)) == (1,)


def test_use_card_candidates_are_ordered_by_their_explicit_targets(pack):
    from sgs.effects import legal_actions_for

    slash = cards_of_type(pack, "slash")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec(), SeatSpec()],
        turn_seat=0,
        rule_config=RuleConfig(),
    )
    uses = [
        action
        for action in legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, UseCard)
    ]

    assert [action.targets for action in uses] == [(1,), (3,)]


# ---------------------------------------------------------------------------
# 多目标两段式：B 段时机外层 / C 段帧内游标
# ---------------------------------------------------------------------------


def _barbarian_engine(pack, *, turn_seat: int, seats: list[SeatSpec]):
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    seats = list(seats)
    holder = seats[turn_seat]
    seats[turn_seat] = SeatSpec(hp=holder.hp, hp_max=holder.hp_max, hand=(barbarian,))
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    return _run_trick(
        pack,
        play="barbarian_invasion",
        seats=seats,
        deck=_deck_excluding(pack, *used),
        turn_seat=turn_seat,
        respond="pass",
    )


def test_a_multi_target_use_runs_one_resolve_frame_with_a_cursor_per_target(pack):
    engine, _ = _barbarian_engine(
        pack, turn_seat=0, seats=[SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec()]
    )

    pushes = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind.value == "RESOLVE"
    ]
    assert len(pushes) == 1, "多目标只有一个 RESOLVE 帧（票 15 §4）"

    frame_id = pushes[0].frame_id
    starts = [
        record for record in _timings(engine, Timing.RESOLVE_START) if record.frame_id == frame_id
    ]
    assert [record.targets for record in starts] == [(1,), (2,), (3,)]
    assert all(record.actor == 0 for record in starts), "C1 的 actor 保持使用者"
    assert [record.actor for record in _timings(engine, Timing.USE_BECOME_TARGET)] == [
        1,
        2,
        3,
    ], "B 段也是时机外层、目标内层"


def test_every_target_gets_its_own_resolution_before_the_next(pack):
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="barbarian_invasion",
        seats=[SeatSpec(hand=(barbarian,)), SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, barbarian, slash),
        turn_seat=0,
        respond="play",
    )

    frame_id = _resolve_frame_id(engine)
    stages = [
        record.timing
        for record in engine.records
        if record.kind == "timing"
        and record.frame_id == frame_id
        and record.timing.value.startswith("RESOLVE_")
    ]
    per_target = [
        Timing.RESOLVE_START,
        Timing.RESOLVE_BEFORE_EFFECT,
        Timing.RESOLVE_EFFECT,
        Timing.RESOLVE_AFTER_EFFECT,
        Timing.RESOLVE_END,
    ]
    assert stages == per_target * 3, "一个目标连续跑完它的所有 C 段时机，再轮到下一个"


def test_target_order_is_counterclockwise_from_the_turn_seat(pack):
    engine, _ = _barbarian_engine(
        pack, turn_seat=1, seats=[SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec()]
    )
    frame_id = _resolve_frame_id(engine)
    order = [
        record.targets[0]
        for record in _timings(engine, Timing.RESOLVE_START)
        if record.frame_id == frame_id
    ]

    assert order == [2, 3, 4, 0], "从当前回合角色（座位 1）起逆时针"


def test_an_empty_target_list_terminates_the_use_flow(pack):
    """目标减为 0 ⇒ 不压 RESOLVE、实体牌直接进弃牌堆（票 11 §5）。"""
    slash = cards_of_type(pack, "slash")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, slash),
        turn_seat=0,
        rule_config=RuleConfig(),
    )
    engine = Engine.from_state(state, seed=1)
    engine.start_use(state.card(slash), source=0, targets=())
    while engine.stack:
        engine._step()

    assert not _timings(engine, Timing.RESOLVE_START), "空目标不进入逐目标结算"
    assert _timings(engine, Timing.RESOLVE_AFTER_END), "仍走'使用结算结束后'"
    assert engine.state.discard == [slash]


# ---------------------------------------------------------------------------
# 第 2 层"无效"：桃园结义对未受伤者
# ---------------------------------------------------------------------------


def test_peach_garden_targets_the_unwounded_but_terminates_at_resolve_start(pack):
    garden = cards_of_type(pack, "peach_garden")[0]
    engine, _ = _run_trick(
        pack,
        play="peach_garden",
        seats=[SeatSpec(hp=4, hand=(garden,)), SeatSpec(hp=4), SeatSpec(hp=2)],
        deck=_deck_excluding(pack, garden),
        turn_seat=0,
    )

    frame_id = _resolve_frame_id(engine)
    starts = [
        r.targets[0] for r in _timings(engine, Timing.RESOLVE_START) if r.frame_id == frame_id
    ]
    after = [
        r.targets[0]
        for r in _timings(engine, Timing.RESOLVE_AFTER_EFFECT)
        if r.frame_id == frame_id
    ]
    recover = [r.targets[0] for r in _timings(engine, Timing.RECOVER_VALUE)]

    assert starts == [0, 1, 2], "未受伤者仍被指定"
    assert after == [2], "只有受伤者继续结算（未受伤者在 C1 终止）"
    assert recover == [2]
    assert [player.hp for player in engine.state.players] == [4, 4, 3]


# ---------------------------------------------------------------------------
# 普通锦囊
# ---------------------------------------------------------------------------


def test_ex_nihilo_draws_two_cards(pack):
    ex_nihilo = cards_of_type(pack, "ex_nihilo")[0]
    engine, _ = _run_trick(
        pack,
        play="ex_nihilo",
        seats=[SeatSpec(hand=(ex_nihilo,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, ex_nihilo),
        turn_seat=0,
    )
    frame_id = _resolve_frame_id(engine)
    draws = [
        delta
        for delta in _deltas(engine, "card_move")
        if delta.reason.value == "DRAW"
        and delta.to_zone.zone is Zone.HAND
        and delta.frame_id == frame_id
    ]

    assert [len(delta.cards) for delta in draws] == [2]


def test_dismantlement_discards_a_hidden_hand_card_by_slot_position(pack):
    dismantle = cards_of_type(pack, "dismantlement")[0]
    victim_card = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="dismantlement",
        seats=[SeatSpec(hand=(dismantle,)), SeatSpec(hand=(victim_card,)), SeatSpec()],
        deck=_deck_excluding(pack, dismantle, victim_card),
        turn_seat=0,
    )

    selections = _queries(engine, QueryKind.SELECT_CARDS)
    assert len(selections) == 1
    assert [(slot.ref.zone, slot.card) for slot in selections[0].slots] == [(Zone.HAND, None)], (
        "他人手牌是隐藏槽：只有位置、没有牌面身份"
    )
    picks = [
        r.action.picks
        for r in engine.records
        if r.kind == "action" and r.action.kind == "SelectCards"
    ]
    assert picks == [(0,)], "行动用面内位置引用"
    assert victim_card in engine.state.discard
    assert engine.state.player(1).hand == []


def test_snatch_takes_a_visible_equipment_card_into_hand(pack):
    snatch = cards_of_type(pack, "snatch")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    engine, _ = _run_trick(
        pack,
        play="snatch",
        seats=[SeatSpec(hand=(snatch,)), SeatSpec(), SeatSpec(), SeatSpec(equip=(weapon,))],
        deck=_deck_excluding(pack, snatch, weapon),
        turn_seat=0,
    )

    slots = _queries(engine, QueryKind.SELECT_CARDS)[0].slots
    assert [(slot.ref.zone, slot.card.card_id) for slot in slots] == [(Zone.EQUIP, weapon)]
    assert weapon in engine.state.player(0).hand
    assert engine.state.player(3).equip == []


def test_barbarian_invasion_only_hits_who_cannot_play_a_slash(pack):
    barbarian = cards_of_type(pack, "barbarian_invasion")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="barbarian_invasion",
        seats=[SeatSpec(hand=(barbarian,)), SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, barbarian, slash),
        turn_seat=0,
        respond="play",
    )

    assert [player.hp for player in engine.state.players] == [4, 4, 3, 3]
    responses = _windows(engine, WindowCause.BARBARIAN_INVASION)
    assert {query.required for query in responses} == {"slash"}
    assert slash in engine.state.discard, "打出的【杀】进弃牌堆"


def test_arrow_barrage_needs_a_dodge(pack):
    arrows = cards_of_type(pack, "arrow_barrage")[0]
    dodge = cards_of_type(pack, "dodge")[0]
    engine, _ = _run_trick(
        pack,
        play="arrow_barrage",
        seats=[SeatSpec(hand=(arrows,)), SeatSpec(hand=(dodge,)), SeatSpec()],
        deck=_deck_excluding(pack, arrows, dodge),
        turn_seat=0,
        respond="play",
    )

    assert [player.hp for player in engine.state.players] == [4, 4, 3]
    assert {query.required for query in _windows(engine, WindowCause.ARROW_BARRAGE)} == {"dodge"}


def test_duel_alternates_slash_until_someone_cannot_play(pack):
    duel = cards_of_type(pack, "duel")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="duel",
        seats=[SeatSpec(hand=(duel,)), SeatSpec(hand=(slash,)), SeatSpec()],
        deck=_deck_excluding(pack, duel, slash),
        turn_seat=0,
        respond="play",
    )

    windows = _windows(engine, WindowCause.DUEL)
    assert [query.cause for query in windows] == [WindowCause.DUEL, WindowCause.DUEL]
    assert [query.target for query in windows] == [None, None], "打出窗口的 target 为 None"
    # 目标先出【杀】，使用者无【杀】⇒ 使用者受到目标造成的 1 点伤害。
    assert [player.hp for player in engine.state.players] == [3, 4, 4]


def test_borrowed_sword_lets_the_holder_use_a_slash(pack):
    """使用者按清单指定 [持刀者 A=1, Bn=2]；持刀者选 a（使用【杀】）⇒ 不交出武器。"""
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="borrowed_sword",
        seats=[SeatSpec(hand=(borrowed,)), SeatSpec(hand=(slash,), equip=(weapon,)), SeatSpec()],
        deck=_deck_excluding(pack, borrowed, weapon, slash),
        turn_seat=0,
        respond="use",
        use_target=2,
        designations=(2,),
    )

    window = _windows(engine, WindowCause.BORROWED_SWORD)[0]
    assert window.cause is WindowCause.BORROWED_SWORD
    assert window.target == 1, "借刀窗口针对持刀者（具体座位）"
    assert any(isinstance(action, UseCard) for action in window.legal_actions)
    uses = [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind.value == "USE"
    ]
    assert len(uses) == 2, "借刀本身 + 持刀者使用的【杀】"
    assert weapon in engine.state.player(1).equip, "使用【杀】后不交出武器"
    assert engine.state.player(2).hp == 3, "被指定的 Bn 受到【杀】的伤害"


def test_borrowed_sword_hands_over_the_weapon_when_refused(pack):
    """b = ``Pass``：持刀者**能**使用【杀】却主动放弃 ⇒ 武器进使用者手牌。"""
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="borrowed_sword",
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(hand=(slash,), equip=(weapon,)),
            SeatSpec(),
        ],
        deck=_deck_excluding(pack, borrowed, weapon, slash),
        turn_seat=0,
        respond="pass",
        designations=(2,),
    )

    assert weapon in engine.state.player(0).hand
    assert engine.state.player(1).equip == []
    assert slash in engine.state.player(1).hand, "主动放弃 ⇒ 那张【杀】没被用掉"


# ---------------------------------------------------------------------------
# W5-02a 借刀：附带指定（暗发布）
# ---------------------------------------------------------------------------


def _borrowed_designation_engine(pack):
    """座位 1 持刀（雌雄双股剑，攻击范围 2）；座位 0 手里一张借刀。

    4 个座位让座位 1 的攻击范围覆盖 0 / 2 / 3——旧路径的 Bn 候选因此有 3 个，
    新路径固定到其中一个才有可观测差别。
    """
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    weapon = cards_of_type(pack, "double_sword")[0]
    slash = cards_of_type(pack, "slash")[0]
    seats = [
        SeatSpec(hand=(borrowed,)),
        SeatSpec(hand=(slash,), equip=(weapon,)),
        SeatSpec(),
        SeatSpec(),
    ]
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    state = build_state(
        pack=pack,
        seats=seats,
        deck=_deck_excluding(pack, *used),
        turn_seat=0,
        rule_config=RuleConfig(first_seat=0),
    )
    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [RecordingPlayer() for _ in seats],
        lambda e: e.pending is not None,
    )
    assert isinstance(engine.pending, PlayPhaseQuery)
    return engine, borrowed


def _finish_borrowed_window(engine) -> None:
    """从已提交的借刀继续跑到**持刀者使用的【杀】结算结束**。"""
    players = [
        _Planned(_Plan(play="__none__", respond="use", played=True)) for _ in range(4)
    ]
    run_until(engine, players, lambda e: _use_settled(e, "slash"))


def test_the_borrowed_sword_designation_pool_and_manifest_shape(pack):
    engine, borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)

    pool = query.designation_actions
    assert {(action.targets, action.designations) for action in pool} == {
        ((1,), (0,)),
        ((1,), (2,)),
        ((1,), (3,)),
    }, "每张借刀 × 合法持刀者 A × A 攻击范围内的每个 Bn"
    assert all(action.card.card_id == borrowed for action in pool)
    assert all(action.answers_seq == query.seq for action in pool), "池与本次询问同源"

    steps = query.card_preactivations["borrowed_sword"]
    assert [(step.name, step.min, step.max, step.order) for step in steps] == [
        ("seat", 1, 1, 0),
        ("seat", 1, 1, 1),
    ], "两步清单：持刀者 → Bn"
    assert steps[0].wording == "选择持刀者（装备区有武器、且有合法【杀】目标）"
    assert steps[1].wording == "选择【杀】的目标（由你指定）"
    assert steps[0].pool == (StepPool(when=(), options=("seat:1",)),)
    assert steps[1].pool == (
        StepPool(when=("seat:1",), options=("seat:0", "seat:2", "seat:3")),
    ), "Bn 子池按 when='seat:A' 分列"

    assert "designation_actions" not in query.model_dump(), "内部池不进记录流"
    assert "card_preactivations" not in query.model_dump(), "清单不进记录流"
    assert not any(
        isinstance(action, UseCard) and action.card.type == "borrowed_sword"
        for action in query.legal_actions
    ), "W5-02b：裸借刀候选已从 legal_actions 收回（入口只剩清单）"
    assert "borrowed_sword" in query.card_preactivations, "可用性只剩这份两步清单"


def test_a_submitted_designation_must_be_a_member_of_the_pool(pack):
    engine, _borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    action = next(
        candidate for candidate in query.designation_actions if candidate.designations == (2,)
    )

    engine.submit(action)

    assert engine.pending is None
    accepted = engine.records[-1]
    assert accepted.kind == "action" and accepted.accepted is True
    assert accepted.action == action
    assert accepted.action.designations == (2,)


def test_a_designation_outside_the_pool_is_rejected_and_changes_nothing(pack):
    engine, _borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    candidate = query.designation_actions[0]
    tampered = UseCard(
        card=candidate.card,
        targets=candidate.targets,
        designations=(99,),
        answers_seq=query.seq,
    )
    baseline = (engine.state.model_dump(), engine.stack.depth, engine.finished)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(tampered)

    assert caught.value.reason_code == "IllegalAction"
    assert "候选池" in caught.value.message
    assert (engine.state.model_dump(), engine.stack.depth, engine.finished) == baseline, (
        "附带指定校验失败必须零状态改动"
    )
    assert engine.pending is query
    rejected = engine.records[-1]
    assert rejected.kind == "action" and rejected.accepted is False
    assert rejected.action == tampered
    assert rejected.reason is not None and "附带指定" in rejected.reason


def test_a_designation_fixes_the_holder_windows_target_to_bn(pack):
    engine, _borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    action = next(
        candidate for candidate in query.designation_actions if candidate.designations == (2,)
    )
    engine.submit(action)

    _finish_borrowed_window(engine)

    window = _windows(engine, WindowCause.BORROWED_SWORD)[0]
    assert window.target == 1, "窗口仍问持刀者"
    assert {
        candidate.targets
        for candidate in window.legal_actions
        if isinstance(candidate, UseCard)
    } == {(2,)}, "有 designation 时目标固定为 Bn，持刀者只选 a / b"
    assert [player.hp for player in engine.state.players] == [4, 4, 3, 4]


def test_the_bare_borrowed_sword_is_no_longer_offered_or_submittable(pack):
    """W5-02b 翻转：裸候选**收回**；仍然提交裸动作 ⇒ `IllegalAction` + 零状态改动。

    旧路径（持刀者自选 Bn）已删除：``resolve_borrowed_sword`` 要求恰好一个 designation，
    缺失即 fail-closed。入口因此只剩清单，提交也必须带 ``designations``。
    """
    engine, borrowed = _borrowed_designation_engine(pack)
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)
    assert not any(
        isinstance(action, UseCard) and action.card.type == "borrowed_sword"
        for action in query.legal_actions
    ), "裸借刀候选不得再出现在 legal_actions"

    plain = UseCard(
        card=engine.state.card(borrowed), targets=(1,), answers_seq=query.seq
    )
    baseline = (engine.state.model_dump(), engine.stack.depth, engine.finished)

    with pytest.raises(IllegalActionError) as caught:
        engine.submit(plain)

    assert caught.value.reason_code == "IllegalAction"
    assert "候选集合" in caught.value.message
    assert (engine.state.model_dump(), engine.stack.depth, engine.finished) == baseline, (
        "旧路径的裸动作被拒后必须零状态改动"
    )
    assert engine.pending is query


def test_the_borrowed_sword_is_unavailable_when_the_manifest_is_empty(pack):
    """可用性只看清单：没有合法持刀者 ⇒ ``card_preactivations`` 为空，牌不可用。

    不 fail-open：清单渲不出来就是此刻不可用（声明错误应当可见），也不许退回裸候选。
    """
    borrowed = cards_of_type(pack, "borrowed_sword")[0]
    state = build_state(
        pack=pack,
        seats=[SeatSpec(hand=(borrowed,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, borrowed),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [RecordingPlayer() for _ in range(3)],
        lambda e: e.pending is not None,
    )
    query = engine.pending
    assert isinstance(query, PlayPhaseQuery)

    assert query.card_preactivations == {}, "没有合法持刀者 ⇒ 清单为空"
    assert query.designation_actions == (), "内部池同样为空"
    assert not any(
        isinstance(action, UseCard) and action.card.type == "borrowed_sword"
        for action in query.legal_actions
    ), "不可用时也不得退回裸候选（不 fail-open）"


# ---------------------------------------------------------------------------
# W5-02b：b 支与结算层的 fail-closed / no-op（``ops.resolve_borrowed_sword``）
# ---------------------------------------------------------------------------


class _BorrowedHost:
    """``resolve_borrowed_sword`` 的最小宿主：只回答"有没有 a / 武器还在不在"。"""

    def __init__(self, *, offers: tuple = (), weapon=None) -> None:
        self.offers = tuple(offers)
        self.weapon = weapon
        self.moves: list[dict] = []
        self.asked = 0

    def current_target(self, frame):
        return 1

    def respond_candidates(self, actor, **kwargs):
        return self.offers

    def ask_respond(self, frame, actor, timing, **kwargs):
        self.asked += 1
        raise AssertionError("auto-b：没有非 Pass 候选就不许发询问")

    def weapon_of(self, seat):
        return self.weapon

    def move_in_frame(self, frame, cards, **kwargs):
        self.moves.append({"cards": tuple(cards), **kwargs})
        return "moved"


def _borrowed_frame(*designations):
    from sgs.resolution import Frame

    return Frame(
        kind=FrameKind.RESOLVE,
        data={"source": 0, "designations": tuple(designations)},
    )


def test_the_borrowed_sword_resolution_requires_exactly_one_designation():
    """fail-closed：入口只经清单，缺失 / 多于一个即显式报错（不回落到持刀者自选）。

    用 ``ValueError`` 而不是 ``assert``：``python -O`` 会删掉断言，而这条是产品路径上的
    编程错误，必须无论优化级别都兜住。
    """
    from sgs.effects.ops import resolve_borrowed_sword

    host = _BorrowedHost()

    for designations in ((), (1, 2)):
        with pytest.raises(ValueError):
            resolve_borrowed_sword(host, _borrowed_frame(*designations))

    assert host.asked == 0 and host.moves == [], "报错在问 / 搬牌之前"


def test_the_borrowed_sword_b_branch_is_a_no_op_when_the_weapon_is_already_gone():
    """a 不可行 ⇒ 不问；武器已离装备区 ⇒ 什么都不做、也不报错。"""
    from sgs.effects.ops import resolve_borrowed_sword

    host = _BorrowedHost(offers=(), weapon=None)

    assert resolve_borrowed_sword(host, _borrowed_frame(2)) is None
    assert host.asked == 0, "a 不可行（非 Pass 候选为空）⇒ 自动 b，不发询问"
    assert host.moves == [], "武器已离区 ⇒ no-op"


def test_harvest_reveals_target_count_and_each_target_picks_one(pack):
    harvest = cards_of_type(pack, "harvest")[0]
    engine, _ = _run_trick(
        pack,
        play="harvest",
        seats=[SeatSpec(hand=(harvest,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, harvest),
        turn_seat=0,
    )

    revealed = [
        delta
        for delta in _deltas(engine, "card_move")
        if delta.to_zone.zone is Zone.PROCESSING and delta.from_zone.zone is Zone.DECK
    ]
    assert [len(delta.cards) for delta in revealed] == [3], "X = 目标数"
    reveal = _timings(engine, Timing.USE_AFTER_DESIGNATE_TARGET)
    assert reveal and reveal[0].seq < _timings(engine, Timing.RESOLVE_START)[0].seq, (
        "亮牌发生在'指定目标后'（B5），早于 C 段"
    )

    selections = _queries(engine, QueryKind.SELECT_CARDS)
    assert [query.actor for query in selections] == [0, 1, 2], "从当前回合角色起逆时针、含自己"
    assert [len(query.slots) for query in selections] == [3, 2, 1], "前一个取走后池子变小"
    assert [len(player.hand) for player in engine.state.players] == [3, 1, 1]
    assert engine.state.processing == [], "取完后处理区清空"


def test_harvest_revealed_cards_end_in_hands_and_processing_is_emptied(pack):
    harvest = cards_of_type(pack, "harvest")[0]
    engine, _ = _run_trick(
        pack,
        play="harvest",
        seats=[SeatSpec(hand=(harvest,)), SeatSpec(), SeatSpec()],
        deck=_deck_excluding(pack, harvest),
        turn_seat=0,
    )
    after_end = [
        record
        for record in _timings(engine, Timing.RESOLVE_AFTER_END)
        if any(card.type == "harvest" for card in record.cards)
    ]
    assert after_end, "使用结算结束后已到"
    assert engine.state.processing == []
    assert harvest in engine.state.discard


def test_harvest_terminates_the_use_when_the_reveal_cannot_complete(pack):
    harvest = cards_of_type(pack, "harvest")[0]
    other = [i for i in range(len(pack.deck.cards)) if i != harvest]
    engine, _ = _run_trick(
        pack,
        play="harvest",
        seats=[SeatSpec(hand=(harvest,)), SeatSpec(), SeatSpec()],
        # 摸牌阶段先取 2 张，只剩 1 张 < X = 3 ⇒ 执行动作未完整执行。
        deck=tuple(other[:3]),
        turn_seat=0,
    )

    assert not _timings(engine, Timing.RESOLVE_START), "终止使用结算、不进入逐目标结算"
    assert not _queries(engine, QueryKind.SELECT_CARDS), "不构造选牌询问"
    assert _timings(engine, Timing.RESOLVE_AFTER_END), "仍走'使用结算结束后'"
    assert not [r for r in engine.records if r.kind == "game_end"], "执行动作未完成不判平局"
    assert engine.state.processing == [], "已亮出的牌进弃牌堆"


def test_a_hidden_selection_face_is_reproducible_for_the_same_seed(pack):
    dismantle = cards_of_type(pack, "dismantlement")[0]
    victim_hand = tuple(cards_of_type(pack, "slash")[:3])
    deck = _deck_excluding(pack, dismantle, *victim_hand)
    seats = [SeatSpec(hand=(dismantle,)), SeatSpec(hand=victim_hand), SeatSpec()]

    first, _ = _run_trick(
        pack, play="dismantlement", seats=seats, deck=deck, turn_seat=0, seed=99
    )
    second, _ = _run_trick(
        pack, play="dismantlement", seats=seats, deck=deck, turn_seat=0, seed=99
    )

    assert first.journal() == second.journal()


def test_a_selection_query_round_trips_through_the_journal(pack, tmp_path):
    """选择面随询问落盘：canonical JSONL 往返后仍逐字节相等，隐藏槽仍无牌面身份。"""
    from sgs.engine import dump_journal, read_journal

    dismantle = cards_of_type(pack, "dismantlement")[0]
    victim = cards_of_type(pack, "slash")[0]
    engine, _ = _run_trick(
        pack,
        play="dismantlement",
        seats=[SeatSpec(hand=(dismantle,)), SeatSpec(hand=(victim,)), SeatSpec()],
        deck=_deck_excluding(pack, dismantle, victim),
        turn_seat=0,
    )

    text = dump_journal(engine.records)
    path = tmp_path / "trick.jsonl"
    path.write_text(text, encoding="utf-8")
    restored = read_journal(path)

    assert dump_journal(restored) == text
    selection = next(
        record.query
        for record in restored
        if record.kind == "query" and record.query.kind == QueryKind.SELECT_CARDS
    )
    assert selection.slots[0].ref.zone is Zone.HAND
    assert selection.slots[0].card is None
