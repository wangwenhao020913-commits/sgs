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

    assert skill_used_this_turn(engine.state, 1, JIE_JIJIANG), "落「本回合已用」位"


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


# ---------------------------------------------------------------------------
# ⑦ 刘备 / 界刘备【激将】「使用」侧（yijiang-2014-round2 票 20）：出牌阶段主动
#    **使用**一张【杀】（目标由主公先选、同势力角色代打、产物进**使用**链），
#    外加两道门槛（合法性 = ``check_usable``；防卡死 = 按座位的机械锁）。
# ---------------------------------------------------------------------------

JIJIANG = "hero.liubei.jijiang"


def _jijiang_use_state(
    *, lord="liubei", lord_hand=(), lord_equip=(), ally_slashes=1, seats=3
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
    assert "【借刀杀人】" not in text, "那句「不覆盖借刀杀人」随实现说明一起删"


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
