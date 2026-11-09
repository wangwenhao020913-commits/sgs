"""M6 票 16：界·蜀（界刘备 / 界赵云 / 界诸葛亮）。

六条技能逐条走**外部缝**（候选 / 询问 / 记录流 / 终局状态），与各族的既有测试同一姿态：

=====================  ==============================================================
技能                     牌面（§十九 L2074–L2082、L2098–L2105、L2113–L2118）与落点
=====================  ==============================================================
界刘备【仁德】           "出牌阶段**每名角色限一次**，你可以交给一名其他角色任意张
                        手牌，若你此次给出了本阶段的第二张「仁德」牌，你可以视为
                        使用一张基本牌"——与标准【仁德】的"首次给到两张回复 1 点"
                        是**两种**收益：界版不回复，改为额外使用一张基本牌
界刘备【激将】① + ②      ①与标准【激将】同一条求助窗口；②每回合限一次，其他蜀势力
                        角色于其回合外使用 / 打出【杀】时，**其**可以令主公摸一张
界赵云【龙胆】           【杀】↔【闪】与标准版逐字相同，另加**【酒】↔【桃】**两向
界赵云【涯角】           "当你于回合外使用或打出手牌时，你可以亮出牌堆顶的一张牌，
                        然后若这两张牌类别相同，则你将之交给一名角色或放回牌堆顶；
                        否则你可以弃置一名攻击范围内包含你的其他角色区域里的一张
                        牌，并将亮出的牌放回牌堆顶"
界诸葛亮【观星】         五张（存活角色数 < 4 时改为三张）；若均置于牌堆底，可以于
                        结束阶段再次观星（第二次的存活角色数**重算**）
界诸葛亮【空城】         与标准【空城】逐字相同（锁定技）
=====================  ==============================================================

**复用既有实现**：界【龙胆】的两个 杀 / 闪 方向直接经 ``shared_with`` 派生（同一份
声明、同一个谓词）；界【空城】与标准版共用同一条目标池声明（同一个
``TargetPoolSkill.remove_self``）；界【激将】① 与标准【激将】共用同一条
``LordSkill.offer``；界【观星】的排列规格与标准版共用 ``Arrange`` 步执行器。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sgs.data import Identity, Kingdom, RuleConfig, load_content_pack
from sgs.effects import behavior_for, check_targets, is_slash
from sgs.engine import Engine, IllegalActionError
from sgs.skills import conversion_offers
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    EndPhase,
    Pass,
    Phase,
    PlayCard,
    MoveReason,
    QueryKind,
    SelectCards,
    SelectOption,
    UseCard,
    WindowCause,
    Zone,
)
from sgs.timing import Timing

from support import (
    SeatSpec,
    build_state,
    cards_of_type,
    declare_death,
    deck_excluding,
    ran_timing,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")
PACK_EX_160 = load_content_pack(deck_profile="standard_ex_160")
"""军争 160 档才有【酒】实体牌：龙胆的 酒↔桃 方向只在它上面可观测。"""


def _ids(pack, type_id: str) -> list[int]:
    return cards_of_type(pack, type_id)


def _virtual(actions, type_id: str) -> list[UseCard]:
    """候选里**虚拟产物**是该牌名的使用动作。"""
    return [
        action
        for action in actions
        if isinstance(action, UseCard)
        and action.card.is_virtual
        and action.card.type == type_id
    ]


def _virtual_sources(actions, type_id: str) -> set[tuple[int, ...]]:
    return {tuple(action.card.virtual_sources) for action in _virtual(actions, type_id)}


def _play_candidates(state, seat: int = 0):
    from sgs.effects import legal_actions_for

    return legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)


# ---------------------------------------------------------------------------
# ① 界赵云【龙胆】：杀↔闪沿用标准版；新增 酒↔桃（两向）
# ---------------------------------------------------------------------------


def test_jie_longdan_keeps_both_standard_slash_dodge_directions():
    """正例（复用）：界【龙胆】仍有【闪】→【杀】与【杀】→【闪】两条标准方向。"""
    dodge = _ids(PACK, "dodge")[0]
    slash = _ids(PACK, "slash")[0]
    play = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(dodge,), hero="jie_zhaoyun"), SeatSpec()],
        deck=deck_excluding(PACK, dodge),
        turn_seat=0,
    )
    dodge_window = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,), hero="jie_zhaoyun"), SeatSpec()],
        deck=deck_excluding(PACK, slash),
        turn_seat=0,
    )

    assert _virtual_sources(_play_candidates(play), "slash") == {(dodge,)}, (
        "【闪】当【杀】使用（与标准【龙胆】同一条声明）"
    )
    from sgs.effects import legal_actions_for

    assert _virtual_sources(
        legal_actions_for(dodge_window, 0, QueryKind.DODGE, answers_seq=1), "dodge"
    ) == {(slash,)}, "【杀】当【闪】打出"


def test_jie_longdan_uses_a_peach_as_a_wine_in_the_play_phase():
    """正例：160 档的【桃】当【酒】使用——走 酒 的常规规则（每回合一次、目标为自己）。"""
    peach = _ids(PACK_EX_160, "peach")[0]
    state = build_state(
        pack=PACK_EX_160,
        seats=[SeatSpec(hand=(peach,), hero="jie_zhaoyun"), SeatSpec()],
        deck=deck_excluding(PACK_EX_160, peach),
        turn_seat=0,
    )

    wine = _virtual(_play_candidates(state), "wine")

    assert [action.card.virtual_sources for action in wine] == [(peach,)]
    assert wine[0].targets == (0,), "【酒】的目标只能是自己"
    assert wine[0].card.name == "酒", "虚拟产物沿用实体牌名（不是 type id）"


def test_jie_longdan_uses_a_wine_as_a_peach_when_someone_is_dying():
    """正例：160 档的【酒】当【桃】使用——求桃窗口里目标由窗口固定为濒死者。"""
    from sgs.effects import legal_actions_for

    wine = _ids(PACK_EX_160, "wine")[0]
    state = build_state(
        pack=PACK_EX_160,
        seats=[SeatSpec(hand=(wine,), hero="jie_zhaoyun"), SeatSpec(hp=0)],
        deck=deck_excluding(PACK_EX_160, wine),
        turn_seat=1,
    )

    actions = legal_actions_for(state, 0, QueryKind.PEACH, answers_seq=1, dying=1)
    peach = _virtual(actions, "peach")

    assert [action.card.virtual_sources for action in peach] == [(wine,)]
    assert peach[0].targets == (1,), "求桃窗口的目标恒为濒死者"


def test_the_standard_zhaoyun_does_not_gain_the_wine_peach_directions():
    """反例（归属）：标准赵云没有这两条声明——同一张牌在标准版手里转不出来。"""
    peach = _ids(PACK_EX_160, "peach")[0]
    wine = _ids(PACK_EX_160, "wine")[0]
    peach_state = build_state(
        pack=PACK_EX_160,
        seats=[SeatSpec(hand=(peach,), hero="zhaoyun"), SeatSpec()],
        deck=deck_excluding(PACK_EX_160, peach),
        turn_seat=0,
    )
    wine_state = build_state(
        pack=PACK_EX_160,
        seats=[SeatSpec(hand=(wine,), hero="zhaoyun"), SeatSpec(hp=0)],
        deck=deck_excluding(PACK_EX_160, wine),
        turn_seat=1,
    )

    from sgs.effects import legal_actions_for

    assert _virtual(_play_candidates(peach_state), "wine") == [], "标准赵云不能桃当酒"
    assert (
        _virtual(
            legal_actions_for(wine_state, 0, QueryKind.PEACH, answers_seq=1, dying=1),
            "peach",
        )
        == []
    ), "标准赵云不能酒当桃"


def test_peach_to_wine_is_not_offered_when_the_pack_has_no_wine():
    """反例（牌堆）：108 档没有【酒】这张牌 ⇒ 桃当酒不出候选（不凭空造一张牌堆外的牌）。"""
    peach = _ids(PACK, "peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(peach,), hero="jie_zhaoyun"), SeatSpec()],
        deck=deck_excluding(PACK, peach),
        turn_seat=0,
    )

    assert not any(card.type == "wine" for card in state.cards), "前提：108 档没有【酒】"
    assert _virtual(_play_candidates(state), "wine") == []
    assert conversion_offers(state, 0, "wine") == ()


def test_jie_longdan_source_predicates_are_card_names_not_colors():
    """判据是**牌名**：【桃】当【酒】只收【桃】（不是"任意红色牌"）；【酒】当【桃】同理。"""
    peach = _ids(PACK_EX_160, "peach")[0]
    slash = _ids(PACK_EX_160, "slash")[0]
    state = build_state(
        pack=PACK_EX_160,
        seats=[SeatSpec(hand=(peach, slash), hero="jie_zhaoyun"), SeatSpec()],
        deck=deck_excluding(PACK_EX_160, peach, slash),
        turn_seat=0,
    )

    assert _virtual_sources(_play_candidates(state), "wine") == {(peach,)}
    assert is_slash(behavior_for(state.card(slash))) and not is_slash(
        behavior_for(state.card(peach))
    ), "前提：两张牌族不同"


# ---------------------------------------------------------------------------
# ② 界诸葛亮【空城】：与标准版逐字相同（复用同一条目标池声明）
# ---------------------------------------------------------------------------


def test_jie_kongcheng_removes_the_owner_from_the_slash_and_duel_pools():
    """正例：没有手牌时，界诸葛亮不能被【杀】/【决斗】指定（且不会挡别的锦囊）。"""
    slash = _ids(PACK, "slash")[0]
    duel = _ids(PACK, "duel")[0]
    snatch = _ids(PACK, "snatch")[0]
    armor = _ids(PACK, "eight_diagrams")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash, duel, snatch)),
            # 空城只看**手牌**：装备区的牌仍让他成为顺手牵羊的合法目标。
            SeatSpec(equip=(armor,), hero="jie_zhugeliang"),
            SeatSpec(hand=(_ids(PACK, "peach")[0],)),
        ],
        deck=deck_excluding(PACK, slash, duel, snatch, armor, _ids(PACK, "peach")[0]),
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(slash)) == (2,), "界诸葛亮（无手牌）出池"
    assert state.card(slash).type == "slash"
    assert check_targets(state, 0, state.card(duel)) == (2,), "【决斗】同样出池"
    assert 1 in check_targets(state, 0, state.card(snatch)), "【顺手牵羊】不受空城影响"


def test_jie_kongcheng_returns_to_the_pool_when_the_owner_has_a_hand_card():
    """反例（手牌）：有手牌时界诸葛亮回到【杀】的目标池（断言不是恒空转）。"""
    slash = _ids(PACK, "slash")[0]
    filler = _ids(PACK, "peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,)),
            SeatSpec(hand=(filler,), hero="jie_zhugeliang"),
        ],
        deck=deck_excluding(PACK, slash, filler),
        turn_seat=0,
    )

    assert check_targets(state, 0, state.card(slash)) == (1,)


def test_jie_kongcheng_reuses_the_standard_predicate():
    """实现面：界【空城】与标准版是**同一条** ``TargetPoolSkill.remove_self`` 判据。"""
    from sgs.skills.target_pool import TARGET_POOL_SKILLS

    by_id = {skill.skill_id: skill for skill in TARGET_POOL_SKILLS}
    standard = by_id["hero.zhugeliang.kongcheng"]
    jie = by_id["hero.jie_zhugeliang.kongcheng"]

    assert type(jie) is type(standard)
    assert (jie.card_types, jie.requires_empty_hand) == (
        standard.card_types,
        standard.requires_empty_hand,
    )
    assert jie.remove_self.__func__ is standard.remove_self.__func__


# ---------------------------------------------------------------------------
# ③ 界诸葛亮【观星】：五张 / 三张、全置底 ⇒ 结束阶段再次观星（存活数重算）
# ---------------------------------------------------------------------------

JIE_GUANXING = "hero.jie_zhugeliang.guanxing"


class _Scripted:
    """最小脚本参与者：先跑 ``handler``，否则按规则化兜底（弃 / 结束 / 发动 / 取首个）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        if query.kind in ("RESPOND", "DODGE", "PEACH"):
            for action in query.legal_actions:
                if type(action).__name__ == "Pass":
                    return action
        if query.kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if type(action).__name__ == "EndPhase":
                    return action
        if query.kind == "SKILL_ACTIVATION":
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill):
                    return action
        return query.legal_actions[0]


def _run_until_query(engine, players, kind: str):
    return run_until(
        engine, players, lambda e: e.pending is not None and e.pending.kind == kind
    )


def _activate_guanxing(query):
    if query.kind == "SKILL_ACTIVATION" and query.skill_id == JIE_GUANXING:
        for action in query.legal_actions:
            if isinstance(action, ActivateSkill):
                return action
        return None
    if query.kind == "ARRANGE":
        return ArrangeCards(
            answers_seq=query.seq, order=tuple(range(len(query.revealed))), split=0
        )
    return None


def _guanxing_queries(engine):
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SKILL_ACTIVATION"
        and record.query.skill_id == JIE_GUANXING
    ]


def _guanxing_state(*, seats: int, turn_seat: int = 0):
    deck = tuple(range(len(PACK.deck.cards)))
    seat_specs = [SeatSpec(hero="jie_zhugeliang")]
    seat_specs.extend(SeatSpec() for _ in range(seats - 1))
    return build_state(
        pack=PACK,
        seats=seat_specs,
        deck=deck,
        turn_seat=turn_seat,
    )


def test_jie_guanxing_reveals_five_cards_at_four_alive_where_the_standard_looks_at_four():
    """正例：存活 4 人 ⇒ 界观星看**五张**（标准观星同局面只看到四张）。"""
    jie = _guanxing_state(seats=4)
    jie_engine = Engine.from_state(jie, seed=7)
    _run_until_query(jie_engine, [_Scripted(_activate_guanxing)], "ARRANGE")
    assert len(_queries_of_kind(jie_engine, "ARRANGE")[0].revealed) == 5

    standard = build_state(
        pack=PACK,
        seats=[SeatSpec(hero="zhugeliang"), *[SeatSpec() for _ in range(3)]],
        deck=tuple(range(len(PACK.deck.cards))),
        turn_seat=0,
    )
    standard_engine = Engine.from_state(standard, seed=7)
    _run_until_query(standard_engine, [_Scripted()], "ARRANGE")
    assert len(_queries_of_kind(standard_engine, "ARRANGE")[0].revealed) == 4, (
        "标准观星仍是 min(存活数, 5)"
    )


def test_jie_guanxing_reveals_three_cards_when_fewer_than_four_are_alive():
    """正例：存活 3 人 ⇒ 界观星看三张。"""
    state = _guanxing_state(seats=3)
    engine = Engine.from_state(state, seed=7)
    _run_until_query(engine, [_Scripted(_activate_guanxing), _Scripted(), _Scripted()], "ARRANGE")

    assert len(_queries_of_kind(engine, "ARRANGE")[0].revealed) == 3


def _queries_of_kind(engine, kind: str):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def test_jie_guanxing_all_bottom_offers_a_second_look_at_the_finish_phase():
    """正例：准备阶段全部置底 ⇒ 结束阶段再问一次观星（同一条技能、同一份流水线）。"""
    state = _guanxing_state(seats=4)
    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(_activate_guanxing), _Scripted(), _Scripted(), _Scripted()]

    _run_until_query(engine, players, "ARRANGE")
    first = _queries_of_kind(engine, "ARRANGE")[0]
    assert len(first.revealed) == 5
    engine.submit(
        ArrangeCards(answers_seq=engine.pending.seq, order=tuple(range(5)), split=0)
    )
    run_until(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW_END))

    # 本回合有人阵亡：4 → 3 存活 ⇒ 第二次观星应按**当时**的存活数看三张（重算口径）。
    declare_death(engine, 3)
    run_until(engine, players, lambda e: ran_timing(e, Timing.DEATH_AFTER))
    run_until(engine, players, lambda e: len(_guanxing_queries(e)) >= 2)

    activations = _guanxing_queries(engine)
    assert [query.timing for query in activations[:2]] == [
        Timing.PHASE_PREPARE,
        Timing.PHASE_FINISH,
    ], "第二次观星挂在结束阶段"
    engine.submit(
        ActivateSkill(
            skill_id=JIE_GUANXING, answers_seq=engine.pending.seq
        )
    )
    run_until(engine, players, lambda e: len(_queries_of_kind(e, "ARRANGE")) >= 2)
    second = _queries_of_kind(engine, "ARRANGE")[1]
    assert len(second.revealed) == 3, "第二次观星按此刻的存活角色数（3 < 4）重算"


def test_jie_guanxing_does_not_offer_the_second_look_when_a_card_stays_on_top():
    """反例（"均置于牌堆底"）：只要有一张留在牌堆顶，结束阶段就不再观星。"""
    state = _guanxing_state(seats=4)
    engine = Engine.from_state(state, seed=7)

    def activate_and_split_one(query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == JIE_GUANXING:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill):
                    return action
        if query.kind == "ARRANGE":
            return ArrangeCards(
                answers_seq=query.seq,
                order=tuple(range(len(query.revealed))),
                split=1,
            )
        return None

    players = [_Scripted(activate_and_split_one), _Scripted(), _Scripted(), _Scripted()]
    run_until(engine, players, lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert len(_guanxing_queries(engine)) == 1, "只有准备阶段那一次观星"


def test_jie_guanxing_declining_the_prepare_look_offers_no_second_look():
    """反例（不发动）：准备阶段放弃观星 ⇒ 结束阶段也不会冒出来。"""
    state = _guanxing_state(seats=4)
    engine = Engine.from_state(state, seed=7)

    def decline(query):
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == JIE_GUANXING:
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        return None

    players = [_Scripted(decline), _Scripted(), _Scripted(), _Scripted()]
    run_until(engine, players, lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert len(_guanxing_queries(engine)) == 1, "放弃的那次仍是一条询问（准备阶段）"
    assert _queries_of_kind(engine, "ARRANGE") == [], "没有排列询问"


# ---------------------------------------------------------------------------
# ④ 界刘备【仁德】：每名角色限一次 + 本阶段第二张换一次"视为使用一张基本牌"
# ---------------------------------------------------------------------------

JIE_RENDE = "hero.jie_liubei.rende"


def _composite(query, skill_id: str, *, picks=(), choices=()) -> ActivateSkill:
    return ActivateSkill(
        skill_id=skill_id,
        picks=tuple(picks),
        choices=tuple(choices),
        answers_seq=query.seq,
    )


def _park_at_play_phase(state, *, players=2):
    engine = Engine.from_state(state, seed=7)
    run_until(
        engine,
        [_Scripted() for _ in range(players)],
        lambda e: e.pending is not None and e.pending.kind == "PLAY_PHASE",
    )
    return engine


def _rende_state(hand, *, owner_hp: int = 4, targets: int = 1):
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hp=owner_hp, hp_max=4, hand=tuple(hand), hero="jie_liubei"),
            *[SeatSpec() for _ in range(targets)],
        ],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
    )




def _virtual_use_records(engine, type_id: str):
    """记录流里**视为使用** ``type_id`` 的结算记录（虚拟牌不进实体牌表，读 timing 记录）。"""
    return [
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and any(card.is_virtual and card.type == type_id for card in record.cards)
    ]

def _basic_asks(engine):
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SELECT_OPTION"
        and str(record.query.skill_id).endswith("rende.basic")
    ]


def _next_play_phase(engine, players):
    run_until(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == "PLAY_PHASE",
    )
    return engine.pending


def test_jie_rende_limits_each_target_to_once_per_play_phase():
    """正例 / 反例：三名其他角色各收一次；全给过之后不再下发清单（每名角色限一次）。"""
    hand = tuple(_ids(PACK, "slash")[:2])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero="jie_liubei"), SeatSpec(), SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
    )

    def decline_bonus(query):
        if query.kind == "SELECT_OPTION" and str(query.skill_id).endswith("rende.basic"):
            for action in query.legal_actions:
                if isinstance(action, Pass):
                    return action
        return None

    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(decline_bonus), _Scripted(), _Scripted(), _Scripted()]

    query = _next_play_phase(engine, players)
    first_seat_step = query.preactivations[JIE_RENDE][1]
    assert {
        option for row in first_seat_step.pool for option in row.options
    } == {
        "seat:1",
        "seat:2",
        "seat:3",
    }, "第一次：三名其他角色都可选"

    engine.submit(_composite(query, JIE_RENDE, picks=hand, choices=("seat:1",)))
    query = _next_play_phase(engine, players)
    seat_step = query.preactivations[JIE_RENDE][1]
    assert {
        option for row in seat_step.pool for option in row.options
    } == {
        "seat:2",
        "seat:3",
    }, "已收过牌的座位 1 不再出现（本阶段）"

    engine.submit(
        _composite(
            query,
            JIE_RENDE,
            picks=tuple(engine.state.player(0).hand[:1]),
            choices=("seat:2",),
        )
    )
    query = _next_play_phase(engine, players)
    last_step = query.preactivations[JIE_RENDE][1]
    assert {
        option for row in last_step.pool for option in row.options
    } == {"seat:3"}

    engine.submit(
        _composite(
            query,
            JIE_RENDE,
            picks=tuple(engine.state.player(0).hand[:1]),
            choices=("seat:3",),
        )
    )
    query = _next_play_phase(engine, players)

    assert JIE_RENDE not in query.preactivations, (
        "三名其他角色都收过了 ⇒ 本阶段没有合法目标，清单不再下发"
    )


def test_jie_rende_second_card_offers_a_basic_card_and_does_not_heal():
    """正例：给出本阶段第二张 ⇒ 可选一张基本牌；**界版不回复体力**（标准版才回复）。"""
    hand = tuple(_ids(PACK, "slash")[:2])
    state = _rende_state(hand, owner_hp=3)
    engine = Engine.from_state(state, seed=7)
    answers = {"picks": hand}

    def liubei(query):
        if query.kind == "PLAY_PHASE":
            if JIE_RENDE in query.preactivations and "picks" in answers:
                picks = answers.pop("picks")
                return _composite(query, JIE_RENDE, picks=picks, choices=("seat:1",))
            for action in query.legal_actions:
                if type(action).__name__ == "EndPhase":
                    return action
        if query.kind == "SELECT_OPTION":
            if str(query.skill_id).endswith("rende.basic"):
                return SelectOption(answers_seq=query.seq, choice="basic_peach")
            return SelectOption(answers_seq=query.seq, choice=query.options[0])
        return None

    players = [_Scripted(liubei), _Scripted()]
    run_until(engine, players, lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    asks = _basic_asks(engine)
    assert asks, '第二张必须问「视为使用哪一张基本牌」'
    assert set(asks[0].options) == {"basic_slash", "basic_peach"}, (
        "108 档没有【酒】；【闪】不是可在出牌阶段使用的牌"
    )
    assert engine.state.player(0).hp == 4, "给牌本身不回复（标准仁德才回复）；选【桃】回复 1 点"
    peach_uses = _virtual_use_records(engine, "peach")
    assert any(record.targets == (0,) for record in peach_uses), (
        "视为使用的【桃】走真牌同一条使用链（目标为自己）"
    )


def test_jie_rende_single_card_does_not_offer_a_basic_card():
    """反例（累计张数）：只给 1 张 —— 本阶段累计还没到 2 ⇒ 不问基本牌、也不回复。"""
    hand = tuple(_ids(PACK, "slash")[:2])
    state = _rende_state(hand, owner_hp=3)
    engine = Engine.from_state(state, seed=7)

    def liubei(query):
        if query.kind == "PLAY_PHASE" and JIE_RENDE in query.preactivations:
            return _composite(query, JIE_RENDE, picks=hand[:1], choices=("seat:1",))
        return None

    run_until(
        engine,
        [_Scripted(liubei), _Scripted()],
        lambda e: ran_timing(e, Timing.TURN_AFTER_END),
    )

    assert _basic_asks(engine) == []
    assert engine.state.player(0).hp == 3, "界仁德不再回复体力"


def test_jie_rende_second_card_can_be_used_as_a_virtual_slash():
    """正例（牌型 / 目标限制）：选【杀】时要再选目标，且这次使用走完整的【杀】链。"""
    hand = tuple(_ids(PACK, "slash")[:2])
    state = _rende_state(hand)
    engine = Engine.from_state(state, seed=7)

    def liubei(query):
        if query.kind == "PLAY_PHASE" and JIE_RENDE in query.preactivations:
            return _composite(query, JIE_RENDE, picks=hand, choices=("seat:1",))
        if query.kind == "SELECT_OPTION":
            if str(query.skill_id).endswith("rende.basic"):
                return SelectOption(answers_seq=query.seq, choice="basic_slash")
            if str(query.skill_id).endswith("rende.target"):
                return SelectOption(answers_seq=query.seq, choice="seat:1")
            return SelectOption(answers_seq=query.seq, choice=query.options[0])
        return None

    run_until(
        engine,
        [_Scripted(liubei), _Scripted()],
        lambda e: ran_timing(e, Timing.TURN_AFTER_END),
    )

    slash_uses = _virtual_use_records(engine, "slash")
    assert any(record.targets == (1,) for record in slash_uses), (
        "视为使用的【杀】有目标、且是普通【杀】牌型"
    )
    assert engine.state.player(1).hp == 3, "目标没有【闪】⇒ 受到 1 点伤害"


def test_jie_rende_offers_no_basic_card_when_no_type_is_usable():
    """反例（付不起）：满血、【杀】的次数已用完、无【酒】 ⇒ 一个基本牌都用不了。"""
    hand = tuple(_ids(PACK, "slash")[:2])
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=hand, hero="jie_liubei"), SeatSpec(hero="jie_zhugeliang")],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)
    done = {"given": False}

    def liubei(query):
        if (
            query.kind == "PLAY_PHASE"
            and JIE_RENDE in query.preactivations
            and not done["given"]
        ):
            done["given"] = True
            return _composite(query, JIE_RENDE, picks=hand, choices=("seat:1",))
        return None

    players = [_Scripted(liubei), _Scripted()]
    # 构造局面：本阶段【杀】的次数已经用完（手牌 0 张的界诸葛亮另有【空城】）。
    run_until(engine, players, lambda e: ran_timing(e, Timing.PHASE_DRAW_END))
    engine.state.player(0).card_use_counts["slash"] = 1
    run_until(engine, players, lambda e: ran_timing(e, Timing.TURN_AFTER_END))

    assert engine.state.player(0).hp == 4, "满血 ⇒ 【桃】也用不了"
    assert _basic_asks(engine) == [], "没有付得起的基本牌 ⇒ 连询问都不发"


# ---------------------------------------------------------------------------
# ⑤ 界刘备【激将】：①与标准版同一条求助窗口；②其他蜀势力角色于其回合外使用 / 打出
#    【杀】时，**其**可以令主公摸一张（每回合限一次）
# ---------------------------------------------------------------------------

JIE_JIJIANG = "hero.jie_liubei.jijiang"


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _of_type(query, action_type, *, type_id=None):
    return _find(
        query,
        lambda action: isinstance(action, action_type)
        and (
            type_id is None
            or getattr(action, "card", None) is not None
            and action.card.type == type_id
        ),
    )


def _queries_of_kind(engine, kind: str):
    return [
        record.query
        for record in engine.records
        if record.kind == "query" and record.query.kind == kind
    ]


def _respond_queries(engine, cause):
    return [
        query
        for query in _queries_of_kind(engine, "RESPOND")
        if getattr(query, "cause", None) is cause
    ]


def _jijiang_queries(engine):
    return [
        query
        for query in _queries_of_kind(engine, "SKILL_ACTIVATION")
        if query.skill_id == JIE_JIJIANG
    ]


def test_jie_jijiang_first_half_still_runs_the_lord_request_window():
    """正例①：界刘备是主公时，南蛮窗口里令其他蜀势力角色代打【杀】。"""
    barbarian = _ids(PACK, "barbarian_invasion")[0]
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(barbarian,)),
            SeatSpec(hero="jie_liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
            SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
            SeatSpec(kingdom=Kingdom.WEI),
        ],
        deck=deck_excluding(PACK, barbarian, slash),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def lord(query):
        if query.kind == "RESPOND":
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIE_JIJIANG:
                    return action
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(attacker), _Scripted(lord), _Scripted(ally), _Scripted()],
        lambda e: slash in e.state.discard,
    )

    requests = _respond_queries(engine, WindowCause.LORD_REQUEST)
    assert [query.actor for query in requests] == [2], "只问其他蜀势力角色"
    assert all(query.required == "slash" for query in requests)
    assert engine.state.player(1).hp == 4, "需要被满足 ⇒ 主公不受【南蛮入侵】伤害"


def _jie_jijiang_second_state(*, ally_kingdom=Kingdom.SHU, ally_slashes: int = 2):
    barbarians = tuple(_ids(PACK, "barbarian_invasion")[:2])
    slashes = tuple(_ids(PACK, "slash")[:ally_slashes])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=barbarians),
            SeatSpec(hero="jie_liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
            SeatSpec(hand=slashes, kingdom=ally_kingdom),
            SeatSpec(kingdom=Kingdom.WEI),
        ],
        deck=deck_excluding(PACK, *barbarians, *slashes),
        turn_seat=0,
    )
    return barbarians, slashes, state


def test_jie_jijiang_second_asks_the_shu_ally_and_the_lord_draws():
    """正例②：其他蜀势力角色于其回合外**打出【杀】**⇒ 询问发给**其**座位，主公摸一张。"""
    barbarians, slashes, state = _jie_jijiang_second_state()

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BARBARIAN_INVASION
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(attacker), _Scripted(), _Scripted(ally), _Scripted()],
        lambda e: e.state.player(1).hand,
    )

    seconds = _jijiang_queries(engine)
    assert [query.actor for query in seconds] == [2], "询问发给打出【杀】的盟友（决定者）"
    assert len(engine.state.player(1).hand) == 1, "收益归主公（摸一张）"
    from sgs.skills.catalog import skill_used_this_turn

    assert skill_used_this_turn(engine.state, 1, JIE_JIJIANG), (
        "「每回合限一次」按**主公 / 实例**落位（整回合一次）：位图表在持有者这条实例上"
    )


def test_jie_jijiang_second_is_offered_once_per_turn():
    """反例：同一回合里盟友第二次打出【杀】⇒ 不再问（每回合限一次）。"""
    barbarians, slashes, state = _jie_jijiang_second_state()

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BARBARIAN_INVASION
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(attacker), _Scripted(), _Scripted(ally), _Scripted()],
        lambda e: len(slashes) == 2
        and all(slash in e.state.discard for slash in slashes),
    )

    assert len(_jijiang_queries(engine)) == 1, "第二次打出【杀】不再询问"
    assert len(engine.state.player(1).hand) == 1, "只摸到一次"


def test_jie_jijiang_second_does_not_ask_a_non_shu_ally():
    """反例（势力）：魏势力角色打出【杀】⇒ ②不问。"""
    barbarians, slashes, state = _jie_jijiang_second_state(ally_kingdom=Kingdom.WEI)

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BARBARIAN_INVASION
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(attacker), _Scripted(), _Scripted(ally), _Scripted()],
        lambda e: slashes[0] in e.state.discard,
    )

    assert _jijiang_queries(engine) == []
    assert engine.state.player(1).hand == []


def test_jie_jijiang_second_does_not_ask_when_the_lord_plays_himself():
    """反例（"其他"）：界刘备自己打出【杀】⇒ ②不问。"""
    barbarian = _ids(PACK, "barbarian_invasion")[0]
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(barbarian,)),
            SeatSpec(
                hero="jie_liubei",
                identity=Identity.LORD,
                kingdom=Kingdom.SHU,
                hand=(slash,),
            ),
        ],
        deck=deck_excluding(PACK, barbarian, slash),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def lord(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BARBARIAN_INVASION
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(attacker), _Scripted(lord)],
        lambda e: slash in e.state.discard,
    )

    assert _jijiang_queries(engine) == []
    assert engine.state.player(1).hand == [], "自己打出的【杀】不给主公摸牌（收益方是他自己）"


def test_the_standard_liubei_jijiang_has_no_second_hooks():
    """反例（对照）：标准刘备的【激将】只有①，没有 ② 的两条钩子。

    票 20 起标准版另有三条**解锁钩子**（机械锁的观测点，不是 ② 的收益段）：它们
    ``forced=True``、没有流水线，故这里按"② 的钩子一条都不在"断言，而不是"没有钩子"。
    """
    from sgs.skills import CATALOG
    from sgs.skills.lord_request import JIE_JIJIANG_ECHO, JIE_JIJIANG_LORD

    standard = CATALOG["hero.liubei.jijiang"]
    assert standard.hooks, "票 20：标准版也有机械锁的三条解锁钩子"
    assert all(hook.forced for hook in standard.hooks), "解锁钩子不问「是否发动」"
    assert all(hook.flow is None for hook in standard.hooks)
    assert JIE_JIJIANG_ECHO.hooks[0] not in standard.hooks
    # 界版 = ② 的两条 + 同样的三条解锁钩子（同一条 ``_lord_request_definition`` 造）。
    jie = CATALOG["hero.jie_liubei.jijiang"]
    assert jie.hooks[: len(JIE_JIJIANG_ECHO.hooks)] == JIE_JIJIANG_ECHO.hooks
    assert jie.hooks[len(JIE_JIJIANG_ECHO.hooks) :] == standard.hooks
    assert JIE_JIJIANG_LORD.activation_flow is not None


def test_the_shared_lord_definition_derives_the_card_name_from_the_declaration():
    """牌名从声明的 ``required`` 派生，不写死【杀】（评审 P2-e 的回归）。

    ``activation_flow`` 是**通用**字段（任何求助型主公技都可能声明它），而 P2-e 之前共享的
    ``_lord_request_definition`` 把清单文案写死成「选择【杀】的目标」、闸门也写死成【杀】——
    第二个声明者会**静默继承【激将】的措辞与闸门**。现在两边都读
    ``_required_card_name(skill.required)``；未登记的新牌名在**构造期**就抛错（不静默沿用
    【杀】）。
    """
    from sgs.effects import CandidateScope
    from sgs.skills import CATALOG
    from sgs.skills.lord_request import (
        LordSkill,
        JIJIANG_USE_FLOW,
        _lord_request_definition,
    )

    probe = LordSkill(
        hero_id="caocao",
        short_id="hujia_probe",
        required="dodge",
        scope=CandidateScope.DODGE,
        activation_flow=JIJIANG_USE_FLOW,
    )
    probe_definition = _lord_request_definition(probe)
    assert probe_definition.step_requirements == {"seat": "选择【闪】的目标"}, (
        "清单文案必须随 required 走（写死【杀】= 第二个声明者继承别人的措辞）"
    )
    assert probe_definition.requirement == "打出【闪】"
    assert CATALOG["hero.liubei.jijiang"].step_requirements == {"seat": "选择【杀】的目标"}

    unregistered = LordSkill(
        hero_id="caocao",
        short_id="peach_probe",
        required="peach",
        scope=CandidateScope.DODGE,
        activation_flow=JIJIANG_USE_FLOW,
    )
    with pytest.raises(ValueError, match="required"):
        _lord_request_definition(unregistered)


# ---------------------------------------------------------------------------
# ⑦ 刘备 / 界刘备【激将】「使用」侧（yijiang-2014-round2 票 20）：出牌阶段主动
#    **使用**一张【杀】（目标由主公先选、同势力角色代打、产物进**使用**链），
#    外加两道门槛（合法性 = ``check_usable``；防卡死 = 按座位的机械锁）。
# ---------------------------------------------------------------------------

JIJIANG = "hero.liubei.jijiang"


def _jijiang_use_state(
    *, lord="liubei", lord_hand=(), lord_equip=(), ally_slashes=1, seats=3, phase=None
):
    """主公（座位 0，LORD）+ 一名蜀势力盟友（座位 1，手上若干【杀】）+ 其余无势力角色。"""
    slashes = tuple(_ids(PACK, "slash")[:ally_slashes])
    specs = [
        SeatSpec(
            hero=lord,
            identity=Identity.LORD,
            kingdom=Kingdom.SHU,
            hand=tuple(lord_hand),
            equip=tuple(lord_equip),
        ),
        SeatSpec(hand=slashes, kingdom=Kingdom.SHU),
        *[SeatSpec() for _ in range(seats - 2)],
    ]
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck_excluding(PACK, *lord_hand, *lord_equip, *slashes),
        turn_seat=0,
        phase=phase,
    )


def test_jijiang_is_offered_as_a_slash_use_in_the_play_phase():
    """**本票判据点**：出牌阶段，主公的【激将】像一条"使用【杀】的入口"那样出清单。"""
    for lord, skill_id in (("liubei", JIJIANG), ("jie_liubei", JIE_JIJIANG)):
        engine = _park_at_play_phase(_jijiang_use_state(lord=lord), players=3)
        query = engine.pending

        assert query.kind == "PLAY_PHASE"
        assert skill_id in query.preactivations, (
            f"{skill_id}：出牌阶段主动使用【杀】这一侧喊不出来"
        )
        steps = query.preactivations[skill_id]
        assert [step.name for step in steps] == ["seat"], "只有「选目标」一步"
        assert {
            option for row in steps[0].pool for option in row.options
        } == {"seat:1", "seat:2"}, "目标由主公先选（合法目标池 = 其他存活角色）"


def test_jijiang_use_takes_the_lords_targets_and_enters_the_use_chain():
    """目标由主公选、产物进**使用**链（不是响应者的打出链）。"""
    state = _jijiang_use_state()
    slash = state.player(1).hand[0]
    engine = Engine.from_state(state, seed=7)

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    players = [_Scripted(), _Scripted(ally), _Scripted()]
    run_until(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == "PLAY_PHASE",
    )
    query = engine.pending
    engine.submit(_composite(query, JIJIANG, choices=("seat:2",)))
    run_until(engine, players, lambda e: e.state.player(2).hp < 4)

    requests = _respond_queries(engine, WindowCause.LORD_REQUEST)
    assert [request.actor for request in requests] == [1], "只问其他蜀势力角色"
    assert engine.state.player(2).hp == 3, "主公选的那名角色受到 1 点【杀】伤害"
    assert engine.state.player(0).card_use_counts["slash"] == 1, (
        "产物是**主公的使用**：占主公的【杀】次数"
    )
    assert engine.state.player(1).hand == [], "响应者的实体牌付出去了"
    assert slash in engine.state.discard

    resolves = [
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and any(card.card_id == slash for card in record.cards)
    ]
    assert [record.actor for record in resolves] == [0], "使用链的使用者是主公（座位 0）"
    assert not [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.PLAY_ON_PLAY
    ], "产物没有走响应者的打出链"


def _own_slash(query, target: int):
    """候选里"主公自己的那张【杀】打向 ``target``"的使用动作。"""
    for action in query.legal_actions:
        if (
            isinstance(action, UseCard)
            and not action.card.is_virtual
            and action.card.type == "slash"
            and action.targets == (target,)
        ):
            return action
    return None


def test_jijiang_use_is_not_offered_when_the_slash_quota_is_spent():
    """**合法性门槛之一**：本阶段【杀】的次数用尽 ⇒ 候选不出现。"""
    state = _jijiang_use_state(lord_hand=(_ids(PACK, "slash")[-1],))
    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(), _Scripted(), _Scripted()]
    query = _next_play_phase(engine, players)

    assert JIJIANG in query.preactivations, "先确认这一局本来喊得出（防假绿）"
    engine.submit(_own_slash(query, 2))
    query = _next_play_phase(engine, players)

    assert engine.state.player(0).card_use_counts["slash"] == 1
    assert JIJIANG not in query.preactivations, (
        "没有【杀】的次数（且不是无限）⇒ 不能主动发动【激将】使用【杀】"
    )


def test_jijiang_use_is_not_offered_without_a_legal_target():
    """**合法性门槛之二**：没有合法目标 ⇒ 候选不出现（``check_targets`` 空池）。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
            # 界诸葛亮的【空城】：「若你没有手牌，你不能成为【杀】或【决斗】的目标」
            SeatSpec(hero="jie_zhugeliang"),
        ],
        deck=deck_excluding(PACK),
        turn_seat=0,
    )
    engine = _park_at_play_phase(state, players=2)

    assert engine.pending.kind == "PLAY_PHASE"
    assert JIJIANG not in engine.pending.preactivations, "没有合法目标 ⇒ 候选不出现"


def test_jijiang_use_is_still_offered_with_unlimited_slash_quota():
    """【诸葛连弩】/无次数限制 ⇒ 次数花掉之后照旧可发动（不写死成"每回合一次"）。"""
    state = _jijiang_use_state(
        lord_hand=(_ids(PACK, "slash")[-1],),
        lord_equip=(_ids(PACK, "crossbow")[0],),
    )
    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(), _Scripted(), _Scripted()]
    query = _next_play_phase(engine, players)

    engine.submit(_own_slash(query, 2))
    query = _next_play_phase(engine, players)

    assert engine.state.player(0).card_use_counts["slash"] == 1, "次数确实花掉了一张"
    assert JIJIANG in query.preactivations, "无限次数 ⇒ 【激将】的入口照旧在"


# ---------------------------------------------------------------------------
# 机械门槛（防卡死）：PLAY 发动一次 ⇒ 立刻禁止再次发动，直到持有者使用 / 打出【杀】
# 或他自己的新出牌阶段开始（用户 2026-11-09 的两句裁定）。
# ---------------------------------------------------------------------------


def _locked_lord_engine(*, seats=3, lord_hand=(), lord_equip=()):
    """跑完一次**没人响应**的 PLAY 发动（锁已置位），停在下一次出牌阶段询问上。"""
    state = _jijiang_use_state(
        seats=seats, lord_hand=lord_hand, lord_equip=lord_equip
    )
    engine = Engine.from_state(state, seed=7)
    players = [_Scripted() for _ in range(seats)]
    query = _next_play_phase(engine, players)
    assert JIJIANG in query.preactivations, "先确认本来喊得出（防假绿）"
    engine.submit(_composite(query, JIJIANG, choices=("seat:2",)))
    return engine, players, _next_play_phase(engine, players)


def test_jijiang_play_phase_activation_locks_itself():
    """**机械门槛判据点**：发动一次（没成）⇒ 本阶段再问即无候选，且受理侧也拒。"""
    engine, _players, query = _locked_lord_engine()

    assert JIJIANG not in query.preactivations, (
        "发动过一次（没人响应）⇒ 本阶段不能再发动"
    )
    # 受理侧：候选里没有也能提交的那道缝（`Engine._structured_activation_failure`）同样拒。
    with pytest.raises(IllegalActionError):
        engine.submit(_composite(query, JIJIANG, choices=("seat:2",)))


def test_jijiang_play_phase_lock_clears_after_the_owner_plays_a_slash():
    """该持有者**使用或打出**一张【杀】⇒ 解锁（含刚由这次【激将】产出的那张）。"""
    slash = _ids(PACK, "slash")[-1]
    engine, players, query = _locked_lord_engine(
        lord_hand=(slash,), lord_equip=(_ids(PACK, "crossbow")[0],)
    )
    assert JIJIANG not in query.preactivations

    engine.submit(_own_slash(query, 2))
    query = _next_play_phase(engine, players)

    assert slash in engine.state.discard
    assert JIJIANG in query.preactivations, "持有者出过【杀】⇒ 锁解开（【诸葛连弩】让次数留着）"


def test_jijiang_play_phase_lock_clears_at_the_next_play_phase():
    """**用户 ④ 的判据点**：不靠出【杀】，**自己的**新出牌阶段开始也解锁（按座位，不全局）。"""
    engine, players, query = _locked_lord_engine()
    assert JIJIANG not in query.preactivations
    engine.submit(EndPhase(answers_seq=query.seq))

    # 对手的出牌阶段开始**不**清这座位上的锁（否则这扇门就是全局开关）。
    _next_play_phase(engine, players)
    assert engine.state.turn_seat == 1
    assert engine.state.player(0).jijiang_locked is True, (
        "座位 1 的出牌阶段开始不该解开座位 0 的锁"
    )

    # 过完一轮，回到自己的出牌阶段 ⇒ 没有出过【杀】也能再发动一次。
    run_until(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == "PLAY_PHASE"
        and e.state.turn_seat == 0,
    )
    query = engine.pending

    assert engine.state.player(0).jijiang_locked is False
    assert JIJIANG in query.preactivations, "新出牌阶段开始 ⇒ 解锁"


def test_jijiang_play_phase_lock_is_not_cleared_by_someone_elses_slash():
    """该持有者**之外**的人使用 / 打出【杀】**不**解锁（锁绑持有者）。"""
    engine, players, query = _locked_lord_engine()
    assert JIJIANG not in query.preactivations
    engine.submit(EndPhase(answers_seq=query.seq))

    # 座位 1（蜀势力盟友）在自己的回合里使用它手上那张【杀】——锁仍绑在座位 0 身上。
    run_until(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == "PLAY_PHASE"
        and e.state.turn_seat == 1,
    )
    engine.submit(_own_slash(engine.pending, 2))
    run_until(engine, players, lambda e: e.state.player(2).hp < 4)

    assert engine.state.player(0).jijiang_locked is True, "别人的使用不解锁"
    assert engine.state.player(1).card_use_counts["slash"] == 1, "确实是座位 1 的使用"


def test_jijiang_passive_windows_ignore_the_lock():
    """**用户 ③ 的判据点**：锁只挡「出牌阶段主动发动」，被动窗口**不查也不置**。"""
    # 方向 A：锁着的刘备在**别人回合**的南蛮窗口里照样能发动，且没人因此解锁。
    barbarian = _ids(PACK, "barbarian_invasion")[0]
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
            SeatSpec(hand=(barbarian,)),
            SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
            SeatSpec(kingdom=Kingdom.WEI),
        ],
        deck=deck_excluding(PACK, barbarian, slash),
        turn_seat=0,
    )
    asked = {"n": 0}

    def lord(query):
        if query.kind == "RESPOND" and query.actor == 0:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIJIANG:
                    return action
        return None

    def attacker(query):
        if query.kind == "PLAY_PHASE" and query.actor == 1:
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            asked["n"] += 1
            if asked["n"] >= 2:  # 第一次（主公主动发动）放弃，第二次（被动窗口）代打
                return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(lord), _Scripted(attacker), _Scripted(ally), _Scripted()]
    query = _next_play_phase(engine, players)
    assert engine.state.turn_seat == 0
    engine.submit(_composite(query, JIJIANG, choices=("seat:1",)))
    query = _next_play_phase(engine, players)
    assert engine.state.player(0).jijiang_locked is True, "主动发动过一次 ⇒ 锁上"
    engine.submit(EndPhase(answers_seq=query.seq))

    run_until(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == "RESPOND"
        and getattr(e.pending, "cause", None) is WindowCause.BARBARIAN_INVASION
        # 这扇窗按座位序逐个问；走到**刘备自己**那一问才停（前两位 Scripted 放弃）。
        and any(
            isinstance(action, ActivateSkill) and action.skill_id == JIJIANG
            for action in e.pending.legal_actions
        ),
    )
    window = engine.pending
    offered = [
        action
        for action in window.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == JIJIANG
    ]
    assert offered, "被动窗口不查锁：南蛮窗口里【激将】照样在候选里"
    engine.submit(offered[0])
    run_until(engine, players, lambda e: slash in e.state.discard)

    assert engine.state.player(0).hp == 4, "求助满足 ⇒ 刘备不受【南蛮入侵】伤害"
    assert engine.state.player(0).jijiang_locked is True, (
        "被动窗口既不置锁也不解锁——这里仍是上一阶段那把锁"
    )

    # 方向 B：**自己的出牌阶段里**的被动窗口（【决斗】的响应）也不置锁——决斗打完，
    # 主动入口照旧在（否则"被动响应一次"会把自己锁死，正是用户 ③ 要防的那件事）。
    duel = _ids(PACK, "duel")[0]
    ally_slash = _ids(PACK, "slash")[0]
    enemy_slash = _ids(PACK, "slash")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hero="liubei",
                identity=Identity.LORD,
                kingdom=Kingdom.SHU,
                hand=(duel,),
            ),
            SeatSpec(hand=(ally_slash,), kingdom=Kingdom.SHU),
            SeatSpec(hand=(enemy_slash,)),
        ],
        deck=deck_excluding(PACK, duel, ally_slash, enemy_slash),
        turn_seat=0,
    )

    def lord_duel(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.DUEL
        ):
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIJIANG:
                    return action
        return None

    def ally_duel(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    def enemy_duel(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.DUEL
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(lord_duel), _Scripted(ally_duel), _Scripted(enemy_duel)]
    query = _next_play_phase(engine, players)
    duel_action = next(
        action
        for action in query.legal_actions
        if isinstance(action, UseCard)
        and action.card.card_id == duel
        and action.targets == (2,)
    )
    engine.submit(duel_action)
    run_until(engine, players, lambda e: e.state.player(2).hp < 4)
    query = _next_play_phase(engine, players)

    assert engine.state.player(2).hp == 3, "决斗里刘备借到的【杀】真的结算了"
    assert engine.state.player(0).hp == 4
    assert engine.state.player(0).jijiang_locked is False, "被动发动不置锁"
    assert JIJIANG in query.preactivations, "被动发动之后主动入口照旧在"


# ---------------------------------------------------------------------------
# ⑧ 【激将】在**被动**的「使用【杀】」窗口里（yijiang-2014-round2 票 22）：
#    【借刀杀人】逼持刀者对 Bn 使用【杀】——持刀者是主公时【激将】要喊得出来，
#    产物是**持刀者的使用**（目标 = 借刀定死的 Bn），且**不查、不置**票 20 的机械锁。
#
# 座位：0 = 借刀的使用者（回合角色），1 = 主公（持刀者），2 = 蜀势力盟友（代打者）。
# 主公手里**没有**【杀】——否则真牌候选会先被选中，求助这条路被遮蔽；装备区挂一把武器，
# 因为借刀的第 0 层合法性要求持刀者「有武器且有合法【杀】目标」。
# ---------------------------------------------------------------------------

BORROWED_SWORD = "borrowed_sword"


def _borrowed_sword_state(*, lord="liubei"):
    borrowed = _ids(PACK, BORROWED_SWORD)[0]
    weapon = _ids(PACK, "qinggang_sword")[0]
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed,)),
            SeatSpec(
                hero=lord,
                identity=Identity.LORD,
                kingdom=Kingdom.SHU,
                equip=(weapon,),
            ),
            SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
        ],
        deck=deck_excluding(PACK, borrowed, weapon, slash),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    return borrowed, weapon, slash, state


def _borrowed_sword_window(engine):
    """停在「借刀逼持刀者使用【杀】」的窗口上（不判座位：调用方按自己的局面核 actor）。"""
    pending = engine.pending
    return (
        pending is not None
        and pending.kind == "RESPOND"
        and getattr(pending, "cause", None) is WindowCause.BORROWED_SWORD
    )


def _plays_borrowed_sword(borrowed: int, *, caster: int, holder: int, bn: int):
    """座位 ``caster`` 的脚本：打出【借刀杀人】（An = ``holder``、Bn = ``bn``）。"""

    def handler(query):
        if query.kind == "PLAY_PHASE" and query.actor == caster:
            for action in getattr(query, "designation_actions", ()):
                if (
                    action.card.card_id == borrowed
                    and action.targets == (holder,)
                    and action.designations == (bn,)
                ):
                    return action
        return None

    return handler


def _open_borrowed_sword_window(engine, players, borrowed):
    """让座位 0 打出【借刀杀人】（An = 座位 1、Bn = 座位 0），停在持刀者的窗口。"""
    players[0] = _Scripted(_plays_borrowed_sword(borrowed, caster=0, holder=1, bn=0))
    return run_until(engine, players, _borrowed_sword_window)


def test_jijiang_is_offered_for_borrowed_sword_slash_use():
    """**本票判据点**：借刀杀人（``mode = USE``）里，持刀者是主公时【激将】进候选。"""
    for lord, skill_id in (("liubei", JIJIANG), ("jie_liubei", JIE_JIJIANG)):
        borrowed, _weapon, _slash, state = _borrowed_sword_state(lord=lord)
        engine = Engine.from_state(state, seed=7)
        players = [_Scripted(), _Scripted(), _Scripted()]
        _open_borrowed_sword_window(engine, players, borrowed)
        assert _borrowed_sword_window(engine), (
            f"{skill_id}：借刀窗口根本没有开——持刀者的候选为空 ⇒ 直接走了 auto-b（交武器）"
        )
        window = engine.pending
        assert window.actor == 1, "借刀问的是持刀者（主公）"
        offered = [
            action
            for action in window.legal_actions
            if isinstance(action, ActivateSkill) and action.skill_id == skill_id
        ]
        assert offered, (
            f"{skill_id}：借刀杀人的「被迫使用【杀】」窗口里喊不出【激将】"
            f"（候选：{window.legal_actions}）"
        )


def _run_borrowed_sword_jijiang(*, lord="liubei", skill_id=JIJIANG, ally_plays=True, stop=None):
    """借刀窗口里主公发动【激将】的整条流程；盟友按 ``ally_plays`` 决定代不代打。

    ``skill_id`` 按主公的版本给（标准 / 界是两条不同的技能 id）：票 23 起界刘备也要走这条路
    （② 要认代付者）。
    """
    borrowed, weapon, slash, state = _borrowed_sword_state(lord=lord)
    engine = Engine.from_state(state, seed=7)
    caster = _plays_borrowed_sword(borrowed, caster=0, holder=1, bn=0)

    def holder(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BORROWED_SWORD
            and query.actor == 1
        ):
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == skill_id:
                    return action
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
            and ally_plays
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    players = [_Scripted(caster), _Scripted(holder), _Scripted(ally)]
    run_until(
        engine,
        players,
        stop if stop is not None else (lambda e: slash in e.state.discard and not e.stack),
    )
    return engine, borrowed, weapon, slash


def test_jijiang_borrowed_sword_use_hits_the_decreed_target():
    """产物是**持刀者的使用**：目标 = 借刀定死的 Bn（主公改不了），进使用链而非打出链。"""
    engine, _borrowed, weapon, slash = _run_borrowed_sword_jijiang()

    assert engine.state.player(0).hp == 3, "借刀指定 Bn = 座位 0，这一刀落在它身上"
    assert engine.state.player(2).hp == 4, "代打的盟友自己没有换目标"
    assert engine.state.player(1).hp == 4, "主公不挨自己这一刀"
    assert engine.state.player(1).card_use_counts["slash"] == 1, (
        "产物是**持刀者（主公）的使用**：使用次数记在他头上"
    )
    assert engine.state.player(2).card_use_counts == {}, "响应者的实体牌只是**打出去**了"
    assert engine.state.player(1).equip == [weapon], "a 支成立 ⇒ 武器不用交给借刀的使用者"
    assert slash in engine.state.discard

    resolves = [
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and any(card.card_id == slash for card in record.cards)
    ]
    assert [record.actor for record in resolves] == [1], "使用链的使用者是持刀者（座位 1）"
    assert [record.targets for record in resolves] == [(0,)], "目标 = 借刀定死的 Bn"
    assert not [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.PLAY_ON_PLAY
    ], "产物没有走响应者的打出链（代打只在窗口里答了 PlayCard）"


def test_jijiang_borrowed_sword_use_is_not_blocked_by_the_play_phase_lock():
    """**用户 ③ 的判据点在被动侧的镜像**：锁着的主公在借刀窗口里照样能发动【激将】。

    机械锁只挡"出牌阶段主动发动"（票 20 §2 的表）：这里先让出牌阶段的主动入口发动一次
    （盟友放弃 ⇒ 锁置上），再在**别人的回合**里被【借刀杀人】逼着使用【杀】——求助窗口
    既不查那把锁，也不因它拒发。
    """
    borrowed = _ids(PACK, BORROWED_SWORD)[0]
    weapon = _ids(PACK, "qinggang_sword")[0]
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hero="liubei",
                identity=Identity.LORD,
                kingdom=Kingdom.SHU,
                equip=(weapon,),
            ),
            SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
            SeatSpec(hand=(borrowed,)),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, borrowed, weapon, slash),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    asked = {"n": 0}

    def holder(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BORROWED_SWORD
            and query.actor == 0
        ):
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIJIANG:
                    return action
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            asked["n"] += 1
            if asked["n"] >= 2:  # 第一次（主动发动）放弃 ⇒ 锁上；第二次（借刀窗口）代打
                return _of_type(query, PlayCard, type_id="slash")
        return None

    caster = _plays_borrowed_sword(borrowed, caster=2, holder=0, bn=2)
    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(holder), _Scripted(ally), _Scripted(caster), _Scripted()]

    # 第一步：出牌阶段主动发动一次（盟友放弃）⇒ 机械锁置上。
    query = _next_play_phase(engine, players)
    assert JIJIANG in query.preactivations, "先确认这一局本来喊得出（防假绿）"
    engine.submit(_composite(query, JIJIANG, choices=("seat:3",)))
    query = _next_play_phase(engine, players)
    assert engine.state.player(0).jijiang_locked is True, "主动发动过一次 ⇒ 锁上"
    engine.submit(EndPhase(answers_seq=query.seq))

    # 第二步：座位 2 打出【借刀杀人】（An = 主公、Bn = 自己）⇒ 主公的被动窗口。
    run_until(engine, players, _borrowed_sword_window)
    window = engine.pending
    assert window.actor == 0 and _borrowed_sword_window(engine)
    assert engine.state.player(0).jijiang_locked is True, "此刻锁仍在（没出过【杀】）"
    offered = [
        action
        for action in window.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == JIJIANG
    ]
    assert offered, "被动窗口不查锁：锁着的主公在借刀窗口里照样能发动【激将】"

    engine.submit(offered[0])
    run_until(engine, players, lambda e: slash in e.state.discard and not e.stack)

    assert asked["n"] == 2, "借刀窗口里真的问了蜀盟友一次"
    assert engine.state.player(2).hp == 3, "产物打向借刀定死的 Bn（座位 2）"
    assert engine.state.player(0).jijiang_locked is False, (
        "产物是主公**自己的使用**【杀】⇒ 按票 20 的解锁事件①把锁解开（不是锁不挡，是它自解）"
    )


def test_jijiang_borrowed_sword_use_does_not_set_the_lock():
    """被动发动**不置锁**：没人响应时锁仍是假的，之后出牌阶段的主动入口照旧在。"""
    weapon = _ids(PACK, "qinggang_sword")[0]
    engine, _borrowed, _weapon, _slash = _run_borrowed_sword_jijiang(
        ally_plays=False,
        # b 支（交武器）完成 ⇒ 借刀窗口收尾：武器从主公的装备区进了借刀使用者的手牌。
        stop=lambda e: weapon in e.state.player(0).hand,
    )

    # 主公 = 座位 1（借刀的使用者是座位 0）。
    assert engine.state.player(1).jijiang_locked is False, (
        "被动窗口发动【激将】不置锁（用户 2026-11-09：「如果是被动响应使用/打出杀则不影响」）"
    )

    # 「之后出牌阶段主动仍可发动」：跑回主公自己的出牌阶段。
    players = [_Scripted(), _Scripted(), _Scripted()]
    run_until(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == "PLAY_PHASE"
        and e.state.turn_seat == 1,
    )
    assert JIJIANG in engine.pending.preactivations, "被动发动之后主动入口照旧在"


def test_jijiang_luanwu_forced_use_delivers_the_ally_slash():
    """【乱武】与借刀同一档「使用」窗口：产物同样由窗口 ``start_use`` 到最近的座位。

    票 22 的 ``respond_modes`` 把【激将】接进**所有** ``RespondMode.USE`` 的【杀】窗口
    （登记表里只有 ``borrowed_sword`` 与 ``luanwu`` 两行）。乱武这一扇的交付若漏掉，盟友那张
    【杀】会被"真打出"、而该座位**还会被再问一次**（`satisfied=True` 但没有产物 ⇒ 白失 1 点
    体力），本断言把这条同形状的路一起钉住。
    """
    jiaxu = "hero.jiaxu_mobile.luanwu"
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="jiaxu_mobile", hp=3, hp_max=3),
            SeatSpec(hero="liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
            SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
        ],
        deck=deck_excluding(PACK, slash),
        turn_seat=0,
        phase=Phase.PLAY,
    )

    def jiaxu_player(query):
        if query.kind == "PLAY_PHASE" and query.actor == 0:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == jiaxu:
                    return action
        return None

    def lord(query):
        if query.kind == "SELECT_OPTION" and str(query.skill_id).endswith(":target"):
            # 并列最近（乱武由该角色自选）：选贾诩。
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SelectOption) and str(action.choice) == "seat:0"
            )
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LUANWU
            and query.actor == 1
        ):
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIJIANG:
                    return action
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(jiaxu_player), _Scripted(lord), _Scripted(ally)]
    run_until(
        engine,
        players,
        lambda e: e.pending is not None
        and getattr(e.pending, "cause", None) is WindowCause.LUANWU
        and e.pending.actor == 2,
    )

    assert engine.state.player(0).hp == 2, "那张【杀】由持刀者（刘备）打向最近的贾诩"
    assert engine.state.player(1).hp == 4, "使用成立 ⇒ 刘备不因乱武失去体力"
    assert engine.state.player(1).card_use_counts["slash"] == 1, "使用次数记在刘备头上"
    assert engine.state.player(2).card_use_counts == {}, "盟友只是打出了那张牌"
    assert not [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.PLAY_ON_PLAY
    ], "产物没有走打出链"


HUJIA = "hero.caocao.hujia"


def test_hujia_has_no_lock():
    """【护驾】完全不锁：不能主动使用 / 打出【闪】⇒ 无卡死风险（用户原话）。"""
    slashes = tuple(_ids(PACK, "slash")[:2])
    dodges = tuple(_ids(PACK, "dodge")[:2])
    crossbow = _ids(PACK, "crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="caocao", identity=Identity.LORD, kingdom=Kingdom.WEI),
            # 【诸葛连弩】让同一个出牌阶段里能打第二张【杀】（否则次数闸先挡住第二问）。
            SeatSpec(hand=slashes, equip=(crossbow,)),
            SeatSpec(hand=dodges, kingdom=Kingdom.WEI),
        ],
        deck=deck_excluding(PACK, *slashes, *dodges, crossbow),
        turn_seat=0,
    )

    def lord(query):
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == HUJIA:
                    return action
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
        ):
            return _of_type(query, PlayCard, type_id="dodge")
        return None

    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(lord), _Scripted(), _Scripted(ally)]
    query = _next_play_phase(engine, players)
    engine.submit(EndPhase(answers_seq=query.seq))

    # 座位 1 在自己的回合里连续两张【杀】打曹操：两次都该喊得出【护驾】（没有锁）。
    run_until(
        engine,
        players,
        lambda e: e.pending is not None
        and e.pending.kind == "PLAY_PHASE"
        and e.state.turn_seat == 1,
    )
    engine.submit(_own_slash(engine.pending, 0))
    run_until(engine, players, lambda e: dodges[0] in e.state.discard)
    query = _next_play_phase(engine, players)
    engine.submit(_own_slash(query, 0))
    run_until(engine, players, lambda e: dodges[1] in e.state.discard)

    windows = [
        window
        for window in _queries_of_kind(engine, "DODGE")
        if any(
            isinstance(action, ActivateSkill) and action.skill_id == HUJIA
            for action in window.legal_actions
        )
    ]
    assert len(windows) == 2, "两次【闪】窗口里【护驾】都在候选里（没有机械锁）"
    assert engine.state.player(0).hp == 4, "两张【杀】都被【护驾】抵消"
    from sgs.skills import CATALOG

    hujia = CATALOG[HUJIA]
    assert hujia.hooks == (), "【护驾】没有解锁钩子（它不锁）"
    assert hujia.flow is None, "【护驾】没有出牌阶段的发动流水线"


def test_hujia_use_side_mirrors_jijiang():
    """票 22：【护驾】的「使用【闪】」侧与【激将】**同形状**——声明 USE、不锁、不设门槛。

    现行牌堆里没有"主动**使用**【闪】"这条路（【闪】只在响应窗口里作为一次使用照走 L4 /
    L5），故这半句今天是**声明性**的：``DODGE`` 语境的候选派发不带 mode 事实
    （``actions._dodge`` 只给 ``respond_required``），行为与票 09 逐字相同。本断言钉三件事：

    ①两条【护驾】声明都认领「使用 + 打出」两档（与【激将】同一份声明值）；
    ②登记表里**要【闪】的每一扇窗口**都被这条声明覆盖（mode 逐档——闸不许空转）；
    ③没有出牌阶段入口 / 解锁钩子（"不锁"的结构依据：不能主动使用【闪】⇒ 无卡死风险）。
    """
    from sgs.effects import CandidateScope
    from sgs.skills import CATALOG
    from sgs.skills.catalog import CARD_REQUEST_WINDOWS, REQUEST_WINDOW
    from sgs.skills.lord_request import (
        HUJIA as HUJIA_DECL,
        JIE_HUJIA_LORD,
        JIJIANG as JIJIANG_DECL,
        USE_OR_PLAY_MODES,
    )
    from sgs.state import RespondMode

    assert set(USE_OR_PLAY_MODES) == {RespondMode.PLAY, RespondMode.USE}
    for decl in (HUJIA_DECL, JIE_HUJIA_LORD):
        assert decl.required == "dodge"
        assert decl.scope is CandidateScope.DODGE
        assert decl.respond_modes == JIJIANG_DECL.respond_modes, "与【激将】同形状（使用 + 打出）"
        assert RespondMode.USE in decl.respond_modes, "「使用【闪】」这一侧要在声明上"
        assert decl.activation_flow is None, "不能主动使用【闪】⇒ 没有出牌阶段入口"

    # ② 登记表：凡是要【闪】的窗口，都被这条声明按 mode 逐档覆盖（两半各有一扇真实窗口）。
    #    排除势力求助的**子窗口**（`lord_request.dodge`）：那扇问的是**盟友**打不打出，
    #    候选面不属于持有者自己（持有者的候选在它外面那一问里）。
    dodge_rows = [
        row
        for row in CARD_REQUEST_WINDOWS
        if row.kind == REQUEST_WINDOW
        and "【闪】" in row.required
        and WindowCause.LORD_REQUEST not in row.causes
    ]
    assert {row.key for row in dodge_rows} == {"dodge", "arrow_barrage"}, (
        f"登记表里要【闪】的窗口变了：{[row.key for row in dodge_rows]}"
    )
    covered = set()
    for row in dodge_rows:
        assert row.scope is CandidateScope.DODGE, row.key
        assert set(row.modes) <= set(HUJIA_DECL.respond_modes), row.key
        covered |= set(row.modes)
    assert covered == {RespondMode.PLAY, RespondMode.USE}, "使用 / 打出两半各有一扇真实窗口"

    # ③ 不锁、不设门槛：没有解锁钩子、没有声明级流水线、不进预发动清单。
    declared = CATALOG[HUJIA]
    assert declared.hooks == (), "【护驾】没有解锁钩子（它不锁）"
    assert declared.flow is None and declared.preactivated is False


def test_liubei_jijiang_wording_says_use_or_play():
    """牌面文案按官网订正为「使用或打出」，并删掉那句自我设限的实现说明。"""
    path = (
        Path(__file__).resolve().parent.parent
        / "tools"
        / "sgs_web"
        / "descriptions.json"
    )
    text = json.loads(path.read_text(encoding="utf-8"))["skills"][JIJIANG]

    assert "当你需要打出【杀】" not in text, "官网写的是「需要**使用或打出**【杀】」"
    assert "当你需要使用或打出【杀】" in text
    assert "视为由你使用或打出" in text
    assert '只覆盖“打出【杀】”的窗口' not in text, "实现范围的自我设限说明要删掉"
    # 票 22 起注记改为**正面**写覆盖面（含【借刀杀人】/【乱武】的被迫使用），故哨兵从
    # "牌名本身不许出现"收紧成"那句自我设限的话不许出现"。
    assert "不覆盖" not in text, "那句「不覆盖【借刀杀人】的『使用【杀】』」随实现说明一起删"


# ---------------------------------------------------------------------------
# ⑥ 界赵云【涯角】：回合外使用 / 打出手牌时亮出牌堆顶一张，按类别分叉
# ---------------------------------------------------------------------------

YAJIAO = "hero.jie_zhaoyun.yajiao"
JIE_YAJIAO = "hero.jie_zhaoyun.yajiao"


def _yajiao_queries(engine):
    return [
        query
        for query in _queries_of_kind(engine, "SKILL_ACTIVATION")
        if query.skill_id == YAJIAO
    ]


def _yajiao_state(*, reveal_type: str, extra_hand: tuple[int, ...] = ()):
    barbarian = _ids(PACK, "barbarian_invasion")[0]
    slash = _ids(PACK, "slash")[0]
    reveal = _ids(PACK, reveal_type)[0]
    fillers = tuple(
        card_id
        for card_id in (*_ids(PACK, "dodge")[:2], *_ids(PACK, "peach")[:2])
        if card_id not in (barbarian, slash, reveal)
    )[:2]
    deck = (fillers[0], fillers[1], reveal) + tuple(
        card_id
        for card_id in range(len(PACK.deck.cards))
        if card_id not in {barbarian, slash, reveal, *fillers, *extra_hand}
    )
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash, *extra_hand), hero="jie_zhaoyun"),
            SeatSpec(hand=(barbarian,)),
        ],
        deck=deck,
        turn_seat=1,
        # 无身份局的先手座位读 ``rule_config.first_seat``（``Engine._first_seat``）：
        # 本场景要"座位 1 的回合、座位 0 在回合外"，故两处都写 1。
        rule_config=RuleConfig(player_count=2, first_seat=1),
    )
    return reveal, state


def _yajiao_players(*, branch: str = "give", discard: bool = True):
    def turn_player(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def zhaoyun(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BARBARIAN_INVASION
        ):
            return _of_type(query, PlayCard, type_id="slash")
        if query.kind == "SKILL_ACTIVATION" and query.skill_id == YAJIAO:
            return _of_type(query, ActivateSkill)
        if query.kind == "SELECT_OPTION":
            skill_id = str(query.skill_id)
            if skill_id == "yajiao:branch":
                return SelectOption(answers_seq=query.seq, choice=branch)
            if skill_id == "yajiao:branch:discard":
                if not discard:
                    for action in query.legal_actions:
                        if isinstance(action, Pass):
                            return action
                return SelectOption(answers_seq=query.seq, choice="yajiao_discard")
            if skill_id == "yajiao:give_seat":
                return SelectOption(answers_seq=query.seq, choice="seat:0")
            if skill_id == "yajiao:discard_seat":
                return SelectOption(answers_seq=query.seq, choice="seat:1")
        if query.kind == "SELECT_CARDS":
            return SelectCards(answers_seq=query.seq, picks=(0,))
        return None

    return [_Scripted(zhaoyun), _Scripted(turn_player)]


def test_yajiao_same_category_hands_the_revealed_card_to_a_character():
    """正例（类别相同）：亮出的基本牌可以交给一名角色（这里是赵云自己）。"""
    reveal, state = _yajiao_state(reveal_type="dodge")
    engine = Engine.from_state(state, seed=7)
    players = _yajiao_players(branch="yajiao_give")
    run_until(engine, players, lambda e: reveal in e.state.player(0).hand)

    queries = _yajiao_queries(engine)
    assert [query.actor for query in queries] == [0], "询问发给界赵云自己"
    assert queries[0].timing is Timing.PLAY_ON_PLAY, "打出【杀】触发的涯角"
    branches = [
        query
        for query in _queries_of_kind(engine, "SELECT_OPTION")
        if query.skill_id == "yajiao:branch"
    ]
    assert set(branches[0].options) == {"yajiao_give", "yajiao_top"}
    assert reveal in engine.state.player(0).hand, "亮出的牌交给了自己"
    assert reveal not in engine.state.deck


def test_yajiao_different_category_may_discard_one_card_and_leaves_the_reveal_on_top():
    """正例（类别不同）：可以弃置一名「攻击范围内包含你」的角色区域里的一张牌。"""
    reveal, state = _yajiao_state(reveal_type="dismantlement")
    engine = Engine.from_state(state, seed=7)
    players = _yajiao_players(discard=True)
    run_until(
        engine,
        players,
        lambda e: e.pending is not None and e.pending.kind == "SELECT_CARDS",
    )

    discard_seats = [
        query
        for query in _queries_of_kind(engine, "SELECT_OPTION")
        if query.skill_id == "yajiao:discard_seat"
    ]
    assert discard_seats and set(discard_seats[0].options) == {"seat:1"}, (
        "只有攻击范围内包含界赵云的座位 1 可选"
    )
    assert engine.state.deck[0] == reveal, "亮出的牌在弃牌之前仍在牌堆顶"

    engine.submit(SelectCards(answers_seq=engine.pending.seq, picks=(0,)))
    run_until(engine, players, lambda e: e.pending is not None)

    discarded = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.reason is MoveReason.DISCARD
        and record.from_zone.zone is Zone.HAND
        and record.from_zone.seat == 1
    ]
    assert len(discarded) == 1, "恰好弃掉了座位 1 的一张手牌"
    assert engine.state.deck[0] == reveal, (
        "亮出的牌放回牌堆顶（peek 未移动，故仍在顶上；不随弃牌改变）"
    )


def test_yajiao_different_category_can_decline_the_discard():
    """反例（可选）：类别不同时可以放弃弃牌，亮出的牌仍放回牌堆顶。"""
    reveal, state = _yajiao_state(reveal_type="dismantlement")
    engine = Engine.from_state(state, seed=7)
    before_discard = len(engine.state.discard)
    players = _yajiao_players(discard=False)
    run_until(
        engine,
        players,
        lambda e: any(
            query.kind == "SELECT_OPTION" and query.skill_id == "yajiao:branch:discard"
            for query in _queries_of_kind(e, "SELECT_OPTION")
        ),
    )

    assert len(engine.state.discard) == before_discard, "放弃弃牌 ⇒ 一张都不弃"
    assert engine.state.deck[0] == reveal


def test_yajiao_does_not_trigger_in_the_owners_own_turn():
    """反例（回合外）：界赵云**自己的回合**使用手牌 ⇒ 涯角不问。"""
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), hero="jie_zhaoyun"),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, slash),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=7)

    def zhaoyun(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="slash")
        return None

    run_until(
        engine,
        [_Scripted(zhaoyun), _Scripted()],
        lambda e: ran_timing(e, Timing.TURN_AFTER_END),
    )

    assert _yajiao_queries(engine) == []


def test_yajiao_does_not_trigger_for_a_card_played_from_the_equipment_zone():
    """反例（手牌）：装备区的【杀】经【龙胆】当【闪】打出 ⇒ 不是"打出手牌"，不触发。"""
    slash = _ids(PACK, "slash")[0]
    attack = _ids(PACK, "slash")[1]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(slash,), hero="jie_zhaoyun"),
            SeatSpec(hand=(attack,)),
        ],
        deck=deck_excluding(PACK, slash, attack),
        turn_seat=1,
        rule_config=RuleConfig(player_count=2, first_seat=1),
    )
    engine = Engine.from_state(state, seed=7)

    def turn_player(query):
        if query.kind == "PLAY_PHASE":
            return _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == attack
                and action.targets == (0,),
            )
        return None

    def zhaoyun(query):
        if query.kind == "DODGE":
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.is_virtual
                    and action.card.type == "dodge"
                    and action.card.virtual_sources == (slash,)
                ):
                    return action
        return None

    run_until(
        engine,
        [_Scripted(zhaoyun), _Scripted(turn_player)],
        lambda e: slash in e.state.discard,
    )

    assert _yajiao_queries(engine) == []


def test_the_standard_zhaoyun_has_no_yajiao():
    """反例（对照）：标准赵云的武将牌上没有【涯角】。"""
    from sgs.data import hero_skill_ids
    from sgs.skills import CATALOG

    assert JIE_YAJIAO == YAJIAO
    assert YAJIAO in CATALOG
    assert "hero.zhaoyun.yajiao" not in hero_skill_ids("zhaoyun")


# ---------------------------------------------------------------------------
# ⑨ 界刘备【激将】② 的「**替你**使用【杀】」侧（yijiang-2014-round2 票 23）
#
#     每回合限一次，其他蜀势力角色于其回合外使用、打出或**替你使用或打出**【杀】时，
#     其可以令你摸一张牌。
#
# 票 20 / 22 把「替主公使用【杀】」的产品建模成 **`source = 主公`** 的一次使用（规则上正确：
# 视为**主公使用**），于是记录流里读不出那个真正付了牌的盟友。裁定（用户 2026-11-10 逐字
# 「票23需要加帧私有键」）= **帧私有键**：代付者住**使用帧**的 `frame.data`，**不进记录流**
# （47 条金录像逐字节不变；回放读不出谁付的牌——这是**有意接受**的代价）。
#
# 下面六条钉：三扇「使用」窗口（①出牌阶段主动使用【杀】（票 20）；②【借刀杀人】被迫使用
# （票 22）；③【乱武】被迫使用（票 22））+ 一扇**打出**窗口（南蛮，既有路径不许回退）
# + 「每回合限一次」的计数主体（**整回合一次**，按主公 / 实例；用户 2026-11-10 裁定：
#   「激将②和护驾②一样的，每个回合，只能触发一次，而非每个盟友每回合各一次」）
# + 「于其回合外」按代付者判的负例。
# ---------------------------------------------------------------------------


def _jijiang_pays_with_slash(query):
    """求助窗口里代打：把盟友手上那张【杀】交出去（三扇使用窗口共用的一问）。"""
    if (
        query.kind == "RESPOND"
        and getattr(query, "cause", None) is WindowCause.LORD_REQUEST
    ):
        return _of_type(query, PlayCard, type_id="slash")
    return None


def test_jie_jijiang_second_fires_when_an_ally_uses_a_slash_for_the_lord():
    """**本票判据点**：盟友替主公**使用**【杀】（票 20 的 PLAY 入口）⇒ ② 问那名盟友、主公摸一张。

    产品口径**不变**：记录流里这次仍是**主公的使用**（``source = 主公``、占主公的次数）；
    代付者是使用帧的私有键，只有 ② 读它。故断言拆两半：记录面仍归主公，询问面归盟友。
    """
    state = _jijiang_use_state(lord="jie_liubei", phase=Phase.PLAY)
    slash = state.player(1).hand[0]
    engine = Engine.from_state(state, seed=7)

    players = [_Scripted(), _Scripted(_jijiang_pays_with_slash), _Scripted()]
    query = _next_play_phase(engine, players)
    before = len(engine.state.player(0).hand)
    assert JIE_JIJIANG in query.preactivations, "先确认这一局本来喊得出（防假绿）"
    engine.submit(_composite(query, JIE_JIJIANG, choices=("seat:2",)))
    run_until(engine, players, lambda e: len(e.state.player(0).hand) > before)

    seconds = _jijiang_queries(engine)
    assert [second.actor for second in seconds] == [1], (
        "② 必须问那个替主公出了【杀】的盟友（决定者 = 代付者，不是主公）"
    )
    assert seconds[0].timing is Timing.USE_ON_USE, "走的是**使用**那一支，不是打出"
    assert len(engine.state.player(0).hand) == before + 1, "收益归主公（摸一张）"
    assert engine.state.player(0).card_use_counts["slash"] == 1, (
        "记录面不变：这次仍是**主公的使用**（占主公的【杀】次数）"
    )
    assert engine.state.player(1).hand == [], "代付者的实体牌付出去了"
    assert slash in engine.state.processing, "代付的那张牌此刻正在结算（使用链的处理区）"


def test_jie_jijiang_second_does_not_fire_when_the_lord_uses_his_own_slash():
    """反例（**没有第三方付牌**）：主公自己在出牌阶段使用【杀】⇒ ② 不问。

    场上有蜀势力盟友（"能被问"的人在场），故"不问"只能是条件判出来的，不是没人可问。
    """
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hero="jie_liubei",
                identity=Identity.LORD,
                kingdom=Kingdom.SHU,
                hand=(slash,),
            ),
            SeatSpec(kingdom=Kingdom.SHU),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK, slash),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(), _Scripted(), _Scripted()]
    query = _next_play_phase(engine, players)

    engine.submit(_own_slash(query, 2))
    run_until(engine, players, lambda e: e.state.player(2).hp < 4)

    assert engine.state.player(0).card_use_counts["slash"] == 1, "确实是主公自己的使用"
    assert _jijiang_queries(engine) == [], "没有代付者 ⇒ 「替你使用」那一支不该触发"


def test_jie_jijiang_second_still_fires_for_the_play_window():
    """**打出侧不许回退**：盟友于其回合外打出【杀】⇒ ② 仍问它、主公仍摸一张。

    与「替你使用」那一支的区别只在帧：这里是 PLAY 帧、``source`` 就是盟友自己（求助窗口
    走的是"代打是真打出"），根本没有代付者键；② 读「来源 = 我」那一支。
    """
    _barbarians, slashes, state = _jie_jijiang_second_state()

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BARBARIAN_INVASION
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(attacker), _Scripted(), _Scripted(ally), _Scripted()],
        lambda e: e.state.player(1).hand,
    )

    seconds = _jijiang_queries(engine)
    assert [second.actor for second in seconds] == [2], "询问发给打出【杀】的盟友"
    assert seconds[0].timing is Timing.PLAY_ON_PLAY, "走的是**打出**那一支（PLAY 帧）"
    assert len(engine.state.player(1).hand) == 1, "收益归主公（摸一张）"
    assert slashes[0] in engine.state.discard, "打出的实体牌真的进了弃牌堆"


def test_jie_jijiang_second_fires_for_the_borrowed_sword_slash_the_ally_paid():
    """第二扇**使用**窗口（【借刀杀人】，票 22）：被迫使用的那张【杀】由盟友代付 ⇒ ② 问它。

    产品口径同 PLAY 入口：记录流里是**持刀者（主公）的使用**（``source = 主公``、目标 =
    借刀定死的 Bn），代付者只在帧私有键上。
    """
    engine, _borrowed, _weapon, _slash = _run_borrowed_sword_jijiang(
        lord="jie_liubei", skill_id=JIE_JIJIANG
    )

    seconds = _jijiang_queries(engine)
    assert [second.actor for second in seconds] == [2], (
        "② 必须问那个替持刀者出了【杀】的盟友（决定者 = 代付者）"
    )
    assert seconds[0].timing is Timing.USE_ON_USE, "借刀这一扇是**使用**窗口"
    assert len(engine.state.player(1).hand) == 1, "收益归主公（摸一张）"
    assert engine.state.player(1).card_use_counts["slash"] == 1, (
        "记录面不变：使用次数仍记在持刀者（主公）头上"
    )
    assert engine.state.player(0).hp == 3, "这一刀落在借刀定死的 Bn（座位 0）"


def test_jie_jijiang_second_fires_for_the_luanwu_slash_the_ally_paid():
    """第三扇**使用**窗口（【乱武】，票 22）：产物是**主公**对最近者的使用，② 问代付的盟友。"""
    jiaxu = "hero.jiaxu_mobile.luanwu"
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="jiaxu_mobile", hp=3, hp_max=3),
            SeatSpec(hero="jie_liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
            SeatSpec(hand=(slash,), kingdom=Kingdom.SHU),
        ],
        deck=deck_excluding(PACK, slash),
        turn_seat=0,
        phase=Phase.PLAY,
    )

    def jiaxu_player(query):
        if query.kind == "PLAY_PHASE" and query.actor == 0:
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == jiaxu:
                    return action
        return None

    def lord(query):
        if query.kind == "SELECT_OPTION" and str(query.skill_id).endswith(":target"):
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SelectOption) and str(action.choice) == "seat:0"
            )
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.LUANWU
            and query.actor == 1
        ):
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIE_JIJIANG:
                    return action
        return None

    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(jiaxu_player), _Scripted(lord), _Scripted(_jijiang_pays_with_slash)]
    run_until(engine, players, lambda e: len(e.state.player(1).hand) == 1)

    seconds = _jijiang_queries(engine)
    assert [second.actor for second in seconds] == [2], (
        "② 必须问那个替主公出了【杀】的盟友（决定者 = 代付者）"
    )
    assert seconds[0].timing is Timing.USE_ON_USE, "乱武这一扇是**使用**窗口"
    assert engine.state.player(1).card_use_counts["slash"] == 1, (
        "记录面不变：使用次数仍记在主公头上"
    )
    assert engine.state.player(2).hand == [], "代付者的实体牌付出去了"


def test_jie_jijiang_second_is_once_per_turn_not_once_per_ally():
    """「每回合限一次」= **整回合只触发一次**，不是每名蜀盟友各一次（用户 2026-11-10 裁定）。

    与【护驾】② 同一条口径（``tests/test_hero_jie_wei.py::
    test_jie_hujia_second_is_once_per_turn_not_once_per_ally`` 是同形的另一半）：

    - 同一回合两轮【南蛮入侵】，座位 2、3 是**两名不同的蜀势力盟友**，各打出【杀】；
    - 第一个打出【杀】的那名（座位 2，派发序在前）触发② ⇒ 这一回合的名额用掉；
    - **第二个不同的盟友不再被问**（若按"每个盟友各一次"，这里会有第二条询问、主公摸两张）；
    - 主公因此只摸**一张**。

    「真的发动才计数」与"整回合一次"是两条正交的口径：放弃不占由
    ``test_jie_jijiang_second_declining_does_not_consume_the_turn`` 钉。
    """
    barbarians = tuple(_ids(PACK, "barbarian_invasion")[:2])
    slashes = tuple(_ids(PACK, "slash")[:4])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=barbarians),
            SeatSpec(hero="jie_liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
            SeatSpec(hand=slashes[:2], kingdom=Kingdom.SHU),
            SeatSpec(hand=slashes[2:], kingdom=Kingdom.SHU),
        ],
        deck=deck_excluding(PACK, *barbarians, *slashes),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BARBARIAN_INVASION
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        [_Scripted(attacker), _Scripted(), _Scripted(ally), _Scripted(ally)],
        lambda e: all(slash in e.state.discard for slash in slashes),
    )

    seconds = _jijiang_queries(engine)
    assert [second.actor for second in seconds] == [2], (
        "只有第一个打出【杀】的盟友被问（整回合一次）；第二个**不同的**盟友不再被问"
    )
    assert len(engine.state.player(1).hand) == 1, "主公只摸一张"


class _DeclinesSkillActivation(_Scripted):
    """只把「是否发动」答成**放弃**，其余按 :class:`_Scripted` 兜底。

    【激将】② 的「放弃不占名额」需要一个"被问但放弃"的盟友：座位 2 放弃之后，座位 3 仍
    必须被问（这一回合的名额没被一次放弃吃掉）。
    """

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION":
            for action in query.legal_actions:
                if type(action).__name__ == "Pass":
                    return action
        return super().respond(query)


def test_jie_jijiang_second_declining_does_not_consume_the_turn():
    """**真的发动**才落位：盟友答「放弃」不占本回合的那一次（用户 2026-11-10 的复核）。

    座位 2 放弃 ②（②的名额不动），随后座位 3（另一名蜀盟友）打出【杀】⇒ 仍然会问它，
    且它真的发动后主公摸一张。"每回合一次"限制的是**发动次数**，不是**询问次数**。
    """
    barbarians = tuple(_ids(PACK, "barbarian_invasion")[:2])
    slashes = tuple(_ids(PACK, "slash")[:4])
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=barbarians),
            SeatSpec(hero="jie_liubei", identity=Identity.LORD, kingdom=Kingdom.SHU),
            SeatSpec(hand=slashes[:2], kingdom=Kingdom.SHU),
            SeatSpec(hand=slashes[2:], kingdom=Kingdom.SHU),
        ],
        deck=deck_excluding(PACK, *barbarians, *slashes),
        turn_seat=0,
    )

    def attacker(query):
        if query.kind == "PLAY_PHASE":
            return _of_type(query, UseCard, type_id="barbarian_invasion")
        return None

    def ally(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BARBARIAN_INVASION
        ):
            return _of_type(query, PlayCard, type_id="slash")
        return None

    engine = Engine.from_state(state, seed=1)
    run_until(
        engine,
        # 座位 2 放弃 ②，座位 3 照常发动：放弃只影响那一问，不落"已发动"位。
        [_Scripted(attacker), _Scripted(), _DeclinesSkillActivation(ally), _Scripted(ally)],
        lambda e: all(slash in e.state.discard for slash in slashes),
    )

    seconds = _jijiang_queries(engine)
    assert [second.actor for second in seconds] == [2, 3], (
        "座位 2 放弃不占名额 ⇒ 座位 3 仍被问（每回合一次限的是**发动**，不是询问）"
    )
    assert len(engine.state.player(1).hand) == 1, "只有座位 3 真的发动了 ⇒ 主公摸一张"


def test_jie_jijiang_second_does_not_fire_for_the_substitute_in_his_own_turn():
    """反例（「于其回合外」按**代付者**判）：代付者就是当前回合角色 ⇒ ② 不问。

    【借刀杀人】由**蜀势力盟友自己**（回合角色）打出、逼持刀者（主公）对 Bn 使用【杀】；
    主公发动【激将】求助时，回合角色本人也在应答区间里（座位环从它起算），它可以代付。
    这一支的「其」是**代付者**，不是帧来源（主公）——故它自己的回合里不触发。
    """
    borrowed = _ids(PACK, BORROWED_SWORD)[0]
    weapon = _ids(PACK, "qinggang_sword")[0]
    slash = _ids(PACK, "slash")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(borrowed, slash), kingdom=Kingdom.SHU),
            SeatSpec(
                hero="jie_liubei",
                identity=Identity.LORD,
                kingdom=Kingdom.SHU,
                equip=(weapon,),
            ),
            SeatSpec(kingdom=Kingdom.SHU),
        ],
        deck=deck_excluding(PACK, borrowed, weapon, slash),
        turn_seat=0,
        phase=Phase.PLAY,
    )

    def holder(query):
        if (
            query.kind == "RESPOND"
            and getattr(query, "cause", None) is WindowCause.BORROWED_SWORD
            and query.actor == 1
        ):
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == JIE_JIJIANG:
                    return action
        return None

    play_borrowed = _plays_borrowed_sword(borrowed, caster=0, holder=1, bn=0)

    def caster(query):
        """回合角色：先打出【借刀杀人】，再在自己的求助窗口里代付那张【杀】。"""
        return play_borrowed(query) or _jijiang_pays_with_slash(query)

    engine = Engine.from_state(state, seed=7)
    players = [_Scripted(caster), _Scripted(holder), _Scripted()]
    run_until(engine, players, _borrowed_sword_window)
    window = engine.pending
    assert window is not None and window.actor == 1, "停在持刀者（主公）的借刀窗口"
    engine.submit(
        next(
            action
            for action in window.legal_actions
            if isinstance(action, ActivateSkill) and action.skill_id == JIE_JIJIANG
        )
    )
    run_until(engine, players, lambda e: e.state.player(0).hp < 4)

    assert engine.state.player(1).card_use_counts["slash"] == 1, "产物确实是主公的使用"
    assert engine.state.player(0).hp == 3, "这一刀落在 Bn（回合角色自己）身上"
    assert _jijiang_queries(engine) == [], (
        "代付者于**其自己的回合**内 ⇒ 「于其回合外」不成立，② 不问"
    )
