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

from sgs.data import Identity, Kingdom, RuleConfig, load_content_pack
from sgs.effects import behavior_for, check_targets, is_slash
from sgs.engine import Engine
from sgs.skills import conversion_offers
from sgs.state import (
    ActivateSkill,
    ArrangeCards,
    Pass,
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
    """反例（对照）：标准刘备的【激将】只有①，没有 ② 的两条钩子。"""
    from sgs.skills import CATALOG
    from sgs.skills.lord_request import JIE_JIJIANG_ECHO

    assert CATALOG["hero.liubei.jijiang"].hooks == ()
    assert CATALOG["hero.jie_liubei.jijiang"].hooks == JIE_JIJIANG_ECHO.hooks


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
