"""M6 票 10：**武将牌上的牌区**（按实例独立）+ 界陆逊【谦逊】/【连营】（spec D14 / §区域）。

规则原文：`三国杀详细规则.txt` §十五 L1507–L1570（置于武将牌上的牌 = **真区域**、
区键含座位与技能实例、这些牌不为任何角色拥有、不算手牌、角色死亡时全部进弃牌堆）、
§十九 L2182–L2188（界陆逊：

    谦逊：当一张延时锦囊牌或其他角色使用的普通锦囊牌对你生效时，若你是此牌的唯一目标，
        你可以将所有手牌置于你的武将牌上，然后你于当前回合结束时获得之。
    连营：当你失去最后的手牌后，你可以令至多 X 名角色各摸一张牌
        （X 为你此次失去的手牌数）。

本票落四件事：

1. **真区域**：``Zone.HERO`` + ``ZoneRef(zone, seat, skill_instance)``——区键 =
   （座位, 技能实例），故同座位的两个放置区彼此独立；``Player.hero_cards`` 装
   ``HeroCardZone``（技能 id / 实例持有者 / 牌），跨座位时座位 = **扣牌的目标**。
2. **放置类技能的三项声明**（数据文件 + 加载校验）：可见性 / 归还时机 / 有无附加效果；
   缺一项 → 结构校验判红（``structure.missing``），代码标记与数据声明不一致 → 语义判红。
3. **界陆逊【谦逊】**：在「成为目标之后、效果发生之前」的窗口（``RESOLVE_EFFECT``）
   把所有手牌置于自己的武将牌上；当前回合结束时整批回到手牌。
4. **界陆逊【连营】**：失去最后的手牌后，令至多 X 名角色各摸一张（X = 此次失去的手牌数；
   X = 0 不发动）。

两条**裁定**（`06` §5「程序化约定与偏离一手」逐条登记）：

- 【谦逊】"对你生效时" = 在成为目标之后、效果发生之前的窗口；
- 【连营】"至多 X 名"的 X = 此次失去的手牌数，0 则不发动。

外部缝：``GameState.zone`` / :func:`sgs.effects.locate` / :func:`sgs.effects.move_card` /
``Engine`` 的公开入口 / ``Engine.records`` 的 delta 形状 / ``sgs.effects`` 的手牌计数。
"""

from __future__ import annotations

from typing import Any

import pytest

from sgs.data import (
    Category,
    DataLoadError,
    Gender,
    Kingdom,
    PlacementReturn,
    PlacementVisibility,
    RuleConfig,
    SkillType,
    hero_placement_decl,
    hero_placement_decls,
    load_content_pack,
    load_heroes,
    validate_heroes,
)
from sgs.effects import EffectContext, discard_count, hand_count, hand_limit, locate, move_card
from sgs.engine import Engine
from sgs.skills import CATALOG, PLACEMENT_SKILL_IDS, hero_card_zone_key
from sgs.state import (
    ActivateSkill,
    CardMoveDelta,
    EndPhase,
    HeroCardZone,
    MoveReason,
    Pass,
    QueryKind,
    SelectOption,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    declare_death,
    deck_excluding,
    ran_timing,
    run_turns,
    run_until,
)

PACK = load_content_pack(deck_profile="standard_108")

JIE_LUXUN = "jie_luxun"
JIE_QIANXUN = "hero.jie_luxun.qianxun"
JIE_LIANYING = "hero.jie_luxun.lianying"
JIE_QINGJIAN = "hero.jie_xiahoudun.qingjian"

QIANXUN_ZONE = hero_card_zone_key(0, JIE_QIANXUN)
"""座位 0 的界陆逊【谦逊】区键（（座位, 技能实例）的规范文本）。"""


# ---------------------------------------------------------------------------
# ① 区域形状：真区域、按实例独立、可跨座位
# ---------------------------------------------------------------------------


def _effect_context(actor: int | None = 0) -> EffectContext:
    return EffectContext(actor=actor, source=actor, targets=(), frame_id=0, depth=0)


def _zone_state(**kwargs: Any):
    """两人局：座位 0 是界陆逊、手里两张牌；座位 1 空手。"""
    hands = kwargs.pop("hands", ((0, 1), ()))
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=JIE_LUXUN, hand=tuple(hands[0])),
            SeatSpec(hero=None, hand=tuple(hands[1])),
        ],
        deck=kwargs.pop("deck", deck_excluding(PACK, *hands[0], *hands[1])),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
        **kwargs,
    )


def _spare_cards(*exclude: int, count: int = 2) -> tuple[int, ...]:
    """牌堆里没被占用的前 ``count`` 张（避免测试里的"手牌 id"与技能牌 id 撞车）。"""
    return deck_excluding(PACK, *exclude)[:count]


def _add_zone(state, seat: int, owner: int, skill_id: str, cards: tuple[int, ...] = ()) -> str:
    """直接构造一个放置区（区是**状态**：构造局面允许直接写，与 ``state.skill_state`` 同姿态）。"""
    key = hero_card_zone_key(owner, skill_id)
    state.player(seat).hero_cards[key] = HeroCardZone(
        skill_id=skill_id, owner=owner, cards=list(cards)
    )
    return key


def test_a_hero_card_zone_is_keyed_by_seat_and_skill_instance():
    """两个区（同座位、不同技能实例）并存：各自的牌互不干扰。"""
    state = _zone_state()
    mine = _add_zone(state, 0, 0, JIE_QIANXUN)
    other = _add_zone(state, 0, 1, "hero.jie_xusheng.pojun")

    assert mine != other
    # 拿座位 0 的两张手牌分别搬进两个区（跨座位：第二个区由**座位 1** 的技能放置）。
    ctx = _effect_context()
    first = move_card(
        state,
        ctx,
        state.refs((0,)),
        to_zone=ZoneRef(Zone.HERO, 0, mine),
        reason=MoveReason.PUT,
        owner=0,
    )
    second = move_card(
        state,
        ctx,
        state.refs((1,)),
        to_zone=ZoneRef(Zone.HERO, 0, other),
        reason=MoveReason.PUT,
        owner=1,
    )

    assert state.zone(ZoneRef(Zone.HERO, 0, mine)) == [0]
    assert state.zone(ZoneRef(Zone.HERO, 0, other)) == [1]
    assert locate(state, 0) == ZoneRef(Zone.HERO, 0, mine)
    assert locate(state, 1) == ZoneRef(Zone.HERO, 0, other)
    assert first.to_zone == ZoneRef(Zone.HERO, 0, mine)
    assert second.owner == 1, "跨座位的归属 = 放置技能的持有者（不是扣牌的目标）"

    # 归还一个区不影响另一个区。
    move_card(
        state,
        ctx,
        state.refs((0,)),
        to_zone=ZoneRef(Zone.HAND, 0),
        reason=MoveReason.PUT,
        owner=0,
    )
    assert state.zone(ZoneRef(Zone.HERO, 0, mine)) == []
    assert state.zone(ZoneRef(Zone.HERO, 0, other)) == [1], "不同技能的区互不归还"


def test_a_hero_card_zone_coordinate_requires_both_seat_and_instance():
    """区域坐标的缺项判据：座位与技能实例缺一即 fail-closed（不静默落进同一张表）。"""
    state = _zone_state()
    _add_zone(state, 0, 0, JIE_QIANXUN)

    with pytest.raises(ValueError, match="座位"):
        state.zone(ZoneRef(Zone.HERO))
    with pytest.raises(ValueError, match="技能实例"):
        state.zone(ZoneRef(Zone.HERO, 0))
    with pytest.raises(ValueError, match="没有"):
        state.zone(ZoneRef(Zone.HERO, 0, "0:hero.jie_xusheng.pojun"))

    assert state.hero_zone(0, "0:hero.jie_xusheng.pojun") == (), "没有该区 = 空元组"


def test_a_hero_card_zone_serialises_the_instance_only_for_hero_zones():
    """记录形状：普通区仍只有 ``zone`` / ``seat``；武将牌上的牌区多带 ``skill_instance``。"""
    plain = CardMoveDelta(
        cards=(),
        from_zone=ZoneRef(Zone.HAND, 1),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=1,
    )
    hero = CardMoveDelta(
        cards=(),
        from_zone=ZoneRef(Zone.HAND, 0),
        to_zone=ZoneRef(Zone.HERO, 0, QIANXUN_ZONE),
        reason=MoveReason.PUT,
        owner=0,
    )

    assert plain.model_dump(mode="json")["to_zone"] == {"zone": "discard", "seat": None}
    dumped = hero.model_dump(mode="json")
    assert dumped["to_zone"] == {
        "zone": "hero",
        "seat": 0,
        "skill_instance": QIANXUN_ZONE,
    }
    assert CardMoveDelta.model_validate(dumped) == hero, "形状可往返反序列化"


def test_hero_card_zone_cards_are_not_hand_cards():
    """区里的牌**不在手牌区**：手牌计数 / 弃牌阶段计数都不再数它们。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=1, hp_max=4, hero=JIE_LUXUN, hand=(0, 1, 2)), SeatSpec()],
        deck=deck_excluding(PACK, 0, 1, 2),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    _add_zone(state, 0, 0, JIE_QIANXUN, cards=(0, 1))
    # 手牌只留第 3 张：状态构造直接写（区里的两张不在 hand 里）。
    state.player(0).hand = [2]

    player = state.player(0)
    assert hand_count(state, player) == 1
    assert hand_limit(state, player) == 1, "手牌上限只读体力（这里没挂别的修正）"
    assert discard_count(state, player) == 0, (
        "区里的两张不计入手牌数：若把它们算进来，计数会是 3 > 上限 1、需要弃牌"
    )


# ---------------------------------------------------------------------------
# ② 数据层的三项声明（可见性 / 归还时机 / 附加效果）
# ---------------------------------------------------------------------------


def _jie_raw() -> dict[str, Any]:
    return load_heroes("jie").model_dump(mode="json")


def _jie_skill(raw: dict[str, Any], hero_id: str, skill_id: str) -> dict[str, Any]:
    hero = next(hero for hero in raw["heroes"] if hero["id"] == hero_id)
    return next(skill for skill in hero["skills"] if skill["id"] == skill_id)


def _validate_jie(raw: dict[str, Any]):
    return validate_heroes(raw, file="heroes.jie.json")


def _jie_errors(raw: dict[str, Any]) -> tuple:
    with pytest.raises(DataLoadError) as excinfo:
        _validate_jie(raw)
    return excinfo.value.errors


def test_every_placement_skill_declares_the_three_facts():
    """登记表 ⇔ 代码标记：每个放置类技能恰有一条数据声明，且三项都在。"""
    declared = hero_placement_decls()
    assert set(declared) == set(PLACEMENT_SKILL_IDS) == {JIE_QIANXUN}
    for skill_id, decl in declared.items():
        definition = CATALOG[skill_id]
        assert definition.places_on_hero_card is True, skill_id
        assert isinstance(decl.visibility, PlacementVisibility), skill_id
        assert isinstance(decl.return_timing, PlacementReturn), skill_id
        assert isinstance(decl.extra_effect, bool), skill_id

    qianxun = declared[JIE_QIANXUN]
    assert qianxun.visibility is PlacementVisibility.PUBLIC, "【谦逊】的放置区公开"
    assert qianxun.return_timing is PlacementReturn.TURN_END, "归还时机 = 当前回合结束时"
    assert qianxun.extra_effect is False, "【谦逊】没有附加效果"
    assert hero_placement_decl(JIE_LIANYING) is None, "【连营】不是放置类技能"


@pytest.mark.parametrize("missing", ["visibility", "return_timing", "extra_effect"])
def test_a_placement_declaration_missing_one_of_the_three_facts_is_rejected(missing: str):
    """合成反例：三项声明**缺一项**即结构错误（不是静默按默认值补）。"""
    raw = _jie_raw()
    placement = _jie_skill(raw, JIE_LUXUN, "qianxun")["placement"]
    del placement[missing]

    errors = _jie_errors(raw)
    assert any(
        error.rule_id == "structure.missing"
        and error.loc.endswith(f"/placement/{missing}")
        for error in errors
    ), errors


def test_a_placement_skill_without_its_data_declaration_is_rejected():
    """反向：代码标着"放置"、数据却没声明（或声明挂到别的技能上）⇒ 语义错误。"""
    raw = _jie_raw()
    del _jie_skill(raw, JIE_LUXUN, "qianxun")["placement"]

    errors = _jie_errors(raw)
    assert any(error.rule_id == "heroes.placement_missing" for error in errors), errors


def test_a_data_declaration_on_a_non_placement_skill_is_rejected():
    """反向（另一半）：数据声明了放置、代码没标 ⇒ 也判红（防"声明了但没人实现"）。"""
    raw = _jie_raw()
    placement = _jie_skill(raw, JIE_LUXUN, "qianxun")["placement"]
    _jie_skill(raw, JIE_LUXUN, "lianying")["placement"] = placement
    del _jie_skill(raw, JIE_LUXUN, "qianxun")["placement"]

    errors = _jie_errors(raw)
    assert any(
        error.rule_id == "heroes.placement_not_registered" for error in errors
    ), errors


def test_the_jie_luxun_data_entry_lands_in_the_catalog():
    """数据文件追加界陆逊（谦逊 / 连营，均启用）；两条技能都在 ``CATALOG`` 里闭合。"""
    from sgs.data import hero_skill_ids

    hero = load_heroes("jie").by_id(JIE_LUXUN)
    assert hero is not None
    assert (hero.name, hero.true_name, hero.version) == ("界陆逊", "陆逊", "jie-ol")
    assert (hero.kingdom, hero.gender, hero.hp_max) == (Kingdom.WU, Gender.MALE, 3)
    assert [skill.id for skill in hero.skills] == ["qianxun", "lianying"]
    assert [skill.skill_type for skill in hero.skills] == [
        SkillType.NORMAL,
        SkillType.NORMAL,
    ]
    assert hero_skill_ids(JIE_LUXUN) == (JIE_QIANXUN, JIE_LIANYING)


# ---------------------------------------------------------------------------
# ③ 界陆逊【谦逊】：生效时把所有手牌置于武将牌上，当前回合结束时获得之
#
# 局面统一取"座位 0 使用锦囊、座位 1 是界陆逊"：引擎从 ``rule_config.first_seat``（0）
# 起跑，故只有座位 0 的回合能被 ``run_turns`` 自然驱动；延时锦囊那条用例例外（判定阶段
# 是**自己的**回合）。
# ---------------------------------------------------------------------------

VICTIM = 1
USER = 0
VICTIM_ZONE = hero_card_zone_key(VICTIM, JIE_QIANXUN)


class Scripted:
    """先跑 ``handler``，返回 ``None`` 时退到最小应答；记下每一次询问。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = PassPlayer()
        self.queries: list[Any] = []

    def respond(self, query):
        self.queries.append(query)
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _find(query, predicate):
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def _activate(query, skill_id: str):
    return _find(
        query,
        lambda action: isinstance(action, ActivateSkill) and action.skill_id == skill_id,
    )


def _seat_option(query, seat: int):
    return _find(
        query,
        lambda action: isinstance(action, SelectOption)
        and action.choice == f"seat:{seat}",
    )


def _decline(query):
    if query.kind in (QueryKind.RESPOND, QueryKind.DODGE, QueryKind.PEACH):
        return _find(query, lambda action: isinstance(action, Pass))
    return None


def _skill_prompts(player: Scripted, skill_id: str) -> list:
    return [
        query
        for query in player.queries
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == skill_id
    ]


def _use_card(card_id: int, card_type: str, *, on_play_phase=None):
    """出牌阶段使用指定的那张牌；没有就结束阶段。"""

    def driver(query):
        if query.kind == QueryKind.PLAY_PHASE:
            if on_play_phase is not None:
                on_play_phase()
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == card_id
                and action.card.type == card_type,
            )
            if action is not None:
                return action
            return _find(query, lambda action: isinstance(action, EndPhase))
        return None

    return Scripted(driver)


def _quiet() -> Scripted:
    return Scripted(lambda query: _decline(query))


def _card(type_id: str) -> int:
    return cards_of_type(PACK, type_id)[0]


def _trick_state(*, victim_hand: tuple[int, ...], trick: int, victim_equip=()):
    """两人局：座位 0 拿着 ``trick``、座位 1 是界陆逊（对手的出牌阶段先走）。"""
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(trick,)),
            SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3, hand=victim_hand, equip=victim_equip),
        ],
        deck=deck_excluding(PACK, *victim_hand, trick, *victim_equip),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )


def _victim_script(
    engine: Engine,
    *,
    seat: int = VICTIM,
    lianying: bool = False,
    seen: dict[str, Any] | None = None,
):
    """某座位的界陆逊：接受【谦逊】；【连营】按需接受 / 放弃，并可在触发点读一次状态。"""
    zone = hero_card_zone_key(seat, JIE_QIANXUN)

    def driver(query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_QIANXUN:
            return _activate(query, JIE_QIANXUN)
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_LIANYING:
            if seen is not None:
                seen["zone"] = engine.state.hero_zone(seat, zone)
                seen["hand"] = tuple(engine.state.player(seat).hand)
            if lianying:
                return _activate(query, JIE_LIANYING)
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    return Scripted(driver)


def _hero_deltas(engine: Engine, seat: int) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone.zone is Zone.HERO
        and record.to_zone.seat == seat
    ]


def test_qianxun_places_all_hand_cards_and_the_turn_end_returns_them() -> None:
    """正例：别人的【过河拆桥】对自己生效时，把手牌全扣上；当前回合结束时拿回来。"""
    trick = _card("dismantlement")
    hand = _spare_cards(trick)
    engine = Engine.from_state(_trick_state(victim_hand=hand, trick=trick), seed=7)
    seen: dict[str, Any] = {}
    players = [_use_card(trick, "dismantlement"), _victim_script(engine, seen=seen)]
    run_turns(engine, players, turns=1)

    assert seen["zone"] == tuple(hand), "所有手牌都在武将牌上（不是只扣被指定的那张）"
    assert seen["hand"] == (), "扣完之后手牌为空 ⇒ 拆 / 顺没有可选目标"
    deltas = _hero_deltas(engine, VICTIM)
    assert len(deltas) == 1
    delta = deltas[0]
    assert delta.from_zone == ZoneRef(Zone.HAND, VICTIM)
    assert delta.to_zone == ZoneRef(Zone.HERO, VICTIM, VICTIM_ZONE)
    assert tuple(card.card_id for card in delta.cards) == hand
    assert delta.reason is MoveReason.PUT
    assert delta.owner == VICTIM, "归属 = 放置技能的持有者"

    # 当前回合（座位 0 的回合）结束时整批回到手牌：记录流里有一条 HERO → HAND 的归还移动。
    returns = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone == ZoneRef(Zone.HERO, VICTIM, VICTIM_ZONE)
        and record.to_zone == ZoneRef(Zone.HAND, VICTIM)
    ]
    assert len(returns) == 1, "归还时机 = 当前回合结束时（只有一条整批归还）"
    assert tuple(card.card_id for card in returns[0].cards) == hand
    assert engine.state.hero_zone(VICTIM, VICTIM_ZONE) == (), "整批归还，区被清空"
    assert VICTIM_ZONE in engine.state.player(VICTIM).hero_cards, "空区保留（区本身不随归还消失）"
    assert set(hand) <= set(engine.state.player(VICTIM).hand), "牌回到了持有者手里"


def test_qianxun_does_not_place_for_a_multi_target_trick() -> None:
    """反例（唯一目标）：一张群体锦囊的目标不止一个 ⇒ 不问【谦逊】、手牌不动。"""
    trick = _card("peach_garden")
    hand = _spare_cards(trick)
    state = _trick_state(victim_hand=hand, trick=trick)
    state.player(VICTIM).hp = 2  # 桃园结义只对已受伤角色生效 ⇒ 两个目标
    engine = Engine.from_state(state, seed=7)
    victim = _victim_script(engine)
    run_until(
        engine,
        [_use_card(trick, "peach_garden"), victim],
        lambda e: ran_timing(e, Timing.RESOLVE_AFTER_END),
    )

    assert _skill_prompts(victim, JIE_QIANXUN) == [], "多目标锦囊不满足『你是此牌的唯一目标』"
    assert engine.state.hero_zone(VICTIM, VICTIM_ZONE) == ()
    assert len(engine.state.player(VICTIM).hand) == len(hand), "手牌没有被扣上武将牌"


def test_qianxun_does_not_place_for_your_own_trick() -> None:
    """反例（使用者）：自己的普通锦囊不满足牌面的『其他角色使用』。"""
    trick = _card("dismantlement")
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3, hand=(*_spare_cards(trick, count=1), trick)),
            SeatSpec(hand=(_spare_cards(trick, count=2)[1],)),
        ],
        deck=deck_excluding(PACK, 0, 1, trick),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)

    def owner(query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_QIANXUN:
            return _activate(query, JIE_QIANXUN)
        if query.kind == QueryKind.PLAY_PHASE:
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == trick,
            )
            if action is not None:
                return action
            return _find(query, lambda action: isinstance(action, EndPhase))
        return None

    player = Scripted(owner)
    run_until(
        engine,
        [player, _quiet()],
        lambda e: ran_timing(e, Timing.RESOLVE_AFTER_END),
    )

    assert _skill_prompts(player, JIE_QIANXUN) == [], "自己使用的锦囊不触发（牌面写『其他角色』）"
    assert engine.state.hero_zone(0, QIANXUN_ZONE) == ()


def test_qianxun_does_not_place_for_another_players_slash() -> None:
    """反例（牌面）：普通锦囊之外（【杀】）即使唯一目标是我，也不触发。"""
    slash = _card("slash")
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,)),
            SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3, hand=_spare_cards(slash)),
        ],
        deck=deck_excluding(PACK, slash, *_spare_cards(slash)),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)
    victim = _victim_script(engine)
    run_until(
        engine,
        [_use_card(slash, "slash"), victim],
        lambda e: ran_timing(e, Timing.RESOLVE_AFTER_END),
    )

    assert _skill_prompts(victim, JIE_QIANXUN) == [], "【杀】不是锦囊 ⇒ 不触发"


def test_qianxun_is_not_offered_without_hand_cards() -> None:
    """反例（前提）：手里没牌 ⇒ 连『是否发动』都不问；拆仍然照常弃装备。"""
    trick = _card("dismantlement")
    equip = _card("eight_diagrams")
    engine = Engine.from_state(
        _trick_state(victim_hand=(), trick=trick, victim_equip=(equip,)), seed=7
    )
    victim = _victim_script(engine)
    run_until(
        engine,
        [_use_card(trick, "dismantlement"), victim],
        lambda e: ran_timing(e, Timing.RESOLVE_AFTER_END),
    )

    assert _skill_prompts(victim, JIE_QIANXUN) == [], "空手放上去没有意义 ⇒ 不问"
    assert engine.state.hero_zone(VICTIM, VICTIM_ZONE) == ()


def test_qianxun_triggers_on_a_delayed_trick_in_the_judge_phase() -> None:
    """正例（延时锦囊）：【乐不思蜀】在判定阶段对自己生效时同样能发动，回合末归还。"""
    delayed = _card("indulgence")
    hand = _spare_cards(delayed)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3, hand=hand), SeatSpec()],
        deck=deck_excluding(PACK, *hand, delayed),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    state.player(0).judge.append(delayed)
    engine = Engine.from_state(state, seed=7)
    seen: dict[str, Any] = {}
    run_turns(engine, [_victim_script(engine, seat=0, seen=seen), _quiet()], turns=1)

    assert seen["zone"] == tuple(hand), "延时锦囊的名字不带『其他角色』限定 ⇒ 照样触发"
    returns = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone == ZoneRef(Zone.HERO, 0, QIANXUN_ZONE)
        and record.to_zone == ZoneRef(Zone.HAND, 0)
    ]
    assert len(returns) == 1, "当前回合（自己的回合）结束时整批归还"
    assert tuple(card.card_id for card in returns[0].cards) == hand
    assert set(hand) <= set(engine.state.player(0).hand)
    assert engine.state.hero_zone(0, QIANXUN_ZONE) == ()


def test_the_return_waits_until_the_current_turn_ends() -> None:
    """边界：扣上之后**不会立刻**归还——一直留到当前回合的回合末。"""
    trick = _card("dismantlement")
    hand = _spare_cards(trick)
    engine = Engine.from_state(_trick_state(victim_hand=hand, trick=trick), seed=7)
    snapshots: list[tuple[int, ...]] = []

    def record_zone() -> None:
        snapshots.append(engine.state.hero_zone(VICTIM, VICTIM_ZONE))

    run_turns(
        engine,
        [
            _use_card(trick, "dismantlement", on_play_phase=record_zone),
            _victim_script(engine),
        ],
        turns=1,
    )

    assert tuple(hand) in snapshots, "回合还没结束时，牌仍留在武将牌上（不提前归还）"
    assert engine.state.hero_zone(VICTIM, VICTIM_ZONE) == ()


def test_two_zones_on_the_same_seat_are_independent() -> None:
    """两个不同技能的区并存：归还只清【谦逊】自己的区，另一个区原样不动。"""
    trick = _card("dismantlement")
    hand = _spare_cards(trick)
    state = _trick_state(victim_hand=(hand[1],), trick=trick)
    other = hero_card_zone_key(USER, "hero.jie_xusheng.pojun")
    state.player(VICTIM).hero_cards[other] = HeroCardZone(
        skill_id="hero.jie_xusheng.pojun", owner=USER, cards=[hand[0]]
    )
    engine = Engine.from_state(state, seed=7)
    run_turns(
        engine,
        [_use_card(trick, "dismantlement"), _victim_script(engine)],
        turns=1,
    )

    assert engine.state.hero_zone(VICTIM, VICTIM_ZONE) == (), "【谦逊】的区在回合末归还"
    assert engine.state.hero_zone(VICTIM, other) == (hand[0],), (
        "别的技能的区不跟着归还 / 不清空（互不可见、互不归还）"
    )
    assert hand[1] in engine.state.player(VICTIM).hand
    assert hand[0] not in engine.state.player(VICTIM).hand, "另一区的牌不回到【谦逊】持有者手里"


# ---------------------------------------------------------------------------
# ④ 界陆逊【连营】：失去最后的手牌后，令至多 X 名角色各摸一张
# ---------------------------------------------------------------------------


def _draw_records(engine: Engine, after_seq: int) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.to_zone.zone is Zone.HAND
        and record.reason is MoveReason.DRAW
        and record.seq > after_seq
    ]


def _draw_records_until_return(engine: Engine, after_seq: int) -> list:
    """``after_seq`` 之后、**回合末归还之前**的摸牌（下一回合自己的摸牌阶段不算）。"""
    end = min(
        (
            record.seq
            for record in engine.records
            if record.kind == "delta"
            and record.delta_type == "card_move"
            and record.from_zone.zone is Zone.HERO
        ),
        default=10**9,
    )
    return [record for record in _draw_records(engine, after_seq) if record.seq < end]


def test_lianying_counts_the_hand_cards_lost_this_time_and_draws_for_each_chosen_seat() -> None:
    """正例（X = 2）：【谦逊】一次扣下两张手牌 ⇒ 至多选 2 名角色各摸一张。

    触发锦囊取【决斗】而不是【过河拆桥】：后者的"弃置其区域里的一张牌"在【谦逊】/【连营】
    之后结算，而【连营】摸到的那张牌此时是陆逊**唯一**的手牌——被弃掉会再触发一次【连营】
    （X = 1，M6 票 12 修好"挂起后续跑不再提前走栈"后正确的级联）。那条级联是另一条机制
    组合，不该混进"X = 此次失去的张数"的判据；本用例只要一次 X = 2 的触发。
    """
    trick = _card("duel")
    hand = _spare_cards(trick)
    engine = Engine.from_state(_trick_state(victim_hand=hand, trick=trick), seed=7)
    asked: list[tuple[str, ...]] = []

    def victim(query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_QIANXUN:
            return _activate(query, JIE_QIANXUN)
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_LIANYING:
            return _activate(query, JIE_LIANYING)
        if query.kind == QueryKind.SELECT_OPTION and query.skill_id.startswith("lianying:"):
            asked.append(tuple(query.options))
            if len(asked) == 1:
                return _seat_option(query, VICTIM)
            return _seat_option(query, USER)
        return None

    run_turns(
        engine,
        [_use_card(trick, "duel"), Scripted(victim)],
        turns=1,
    )

    assert len(asked) == 2, "X = 此次失去的两张手牌 ⇒ 最多问两次『选谁』"
    assert asked[0] == ("seat:0", "seat:1")
    assert asked[1] == ("seat:0",), "同一步的第二次询问排除已选的角色"
    placement = _hero_deltas(engine, VICTIM)[0]
    draws = _draw_records_until_return(engine, placement.seq)
    assert [(record.to_zone.seat, len(record.cards)) for record in draws] == [
        (VICTIM, 1),
        (USER, 1),
    ], "选中的两名角色各摸一张（不是各摸 X 张）"


def test_lianying_triggers_when_a_trick_takes_the_last_hand_card() -> None:
    """正例（X = 1）：只剩一张手牌被【过河拆桥】拿走 ⇒ 选一名角色摸一张。"""
    trick = _card("dismantlement")
    last = _card("peach")
    state = _trick_state(victim_hand=(last,), trick=trick)
    engine = Engine.from_state(state, seed=7)
    asked: list[tuple[str, ...]] = []

    def victim(query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_LIANYING:
            return _activate(query, JIE_LIANYING)
        if query.kind == QueryKind.SELECT_OPTION and query.skill_id.startswith("lianying:"):
            asked.append(tuple(query.options))
            return _seat_option(query, VICTIM)
        return None

    hand_before = len(engine.state.player(VICTIM).hand)
    victim_player = Scripted(victim)
    run_until(
        engine,
        [_use_card(trick, "dismantlement"), victim_player],
        lambda e: ran_timing(e, Timing.RESOLVE_AFTER_END),
    )

    prompts = _skill_prompts(victim_player, JIE_LIANYING)
    assert len(prompts) == 1, "失去最后一张手牌 ⇒ 问一次【连营】"
    assert hand_before == 1
    assert len(asked) == 1 and asked[0] == ("seat:0", "seat:1"), "X = 1 ⇒ 只问一次"
    draws = _draw_records(engine, prompts[0].seq)
    assert [(record.to_zone.seat, len(record.cards)) for record in draws] == [(VICTIM, 1)]
    assert len(engine.state.player(VICTIM).hand) == 1, "失去最后一张 + 连营摸回一张"


def test_lianying_triggers_when_the_last_hand_card_goes_into_another_hand() -> None:
    """裁定（票 07；用户 2026-11-03「把牌交给别人也算失去手牌」）：去向是**别人的手牌**也算。

    【顺手牵羊】把界陆逊最后一张手牌拿进使用者（座位 0）的手牌：对失去方是"失去最后的
    手牌"，【连营】该问——与标版【连营】同一读法
    （`tests/test_hero_jie_hand_limit.py::test_lianying_triggers_when_the_last_hand_cards_are_given_away`）。
    旧判据把"去向是手牌"一律排除（不分是谁的手牌），这条因此静默不触发。
    """
    trick = _card("snatch")
    last = _card("peach")
    engine = Engine.from_state(_trick_state(victim_hand=(last,), trick=trick), seed=7)
    asked: list[tuple[str, ...]] = []

    def victim(query):
        # 拒【谦逊】（它会先把手牌全扣上，改走"手牌 → 武将牌上"那条失去路径）。
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_QIANXUN:
            return _find(query, lambda action: isinstance(action, Pass))
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_LIANYING:
            return _activate(query, JIE_LIANYING)
        if query.kind == QueryKind.SELECT_OPTION and query.skill_id.startswith("lianying:"):
            asked.append(tuple(query.options))
            return _seat_option(query, VICTIM)
        return None

    player = Scripted(victim)
    run_until(
        engine,
        [_use_card(trick, "snatch"), player],
        lambda e: ran_timing(e, Timing.RESOLVE_AFTER_END),
    )

    prompts = _skill_prompts(player, JIE_LIANYING)
    assert len(prompts) == 1, "最后一张手牌进了别人的手牌 ⇒ 失去手牌 ⇒ 连营该问"
    assert last in engine.state.player(USER).hand, "牌确实落在使用者的手牌里（去向是手牌）"
    assert len(asked) == 1 and asked[0] == ("seat:0", "seat:1"), "X = 1 ⇒ 只问一次"
    draws = _draw_records(engine, prompts[0].seq)
    assert [(record.to_zone.seat, len(record.cards)) for record in draws] == [(VICTIM, 1)]


def test_lianying_is_not_offered_when_hand_cards_remain() -> None:
    """反例：失去一张手牌但手里还有牌 ⇒ 不是『最后的手牌』，不问【连营】。"""
    trick = _card("dismantlement")
    engine = Engine.from_state(_trick_state(victim_hand=_spare_cards(trick), trick=trick), seed=7)

    def victim(query):
        # 拒绝【谦逊】（否则它会把手牌全扣上，直接满足"最后的手牌"）。
        if query.kind == QueryKind.SKILL_ACTIVATION:
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    player = Scripted(victim)
    run_until(
        engine,
        [_use_card(trick, "dismantlement"), player],
        lambda e: ran_timing(e, Timing.RESOLVE_AFTER_END),
    )

    assert _skill_prompts(player, JIE_LIANYING) == []
    assert len(engine.state.player(VICTIM).hand) == 1


def test_lianying_keeps_drawing_after_a_qingjian_ask_suspends_mid_loop() -> None:
    """嵌套挂起（票 25）：逐座摸牌里第一座触发【清俭】⇒ 内层答完后**外层接着摸第二座**。

    确定性复现（票 24 的随机局 seed=663 链条的构造版）：界夏侯惇（座位 0）用【决斗】，
    界陆逊（座位 1）以【谦逊】扣下两张手牌 ⇒ 失去最后的手牌，【连营】X = 2 ⇒ 选座位 0
    与座位 1 各摸一张。给座位 0 摸牌是**一次内层移动**，它触发【清俭】并提问挂起。

    旧实现只有**一个** ``_move_pending`` 槽：内层挂起覆盖外层，外层"还要摸座位 1"这段
    永久丢失（连营的游标停在只摸过座位 0）。判据 = 两座**各摸一张**且顺序与选人一致。
    """
    trick = _card("duel")
    hand = _spare_cards(trick)
    qingjian_hand = _spare_cards(trick, *hand)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="jie_xiahoudun", hp=4, hp_max=4, hand=(trick, *qingjian_hand)),
            SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3, hand=hand),
        ],
        deck=deck_excluding(PACK, trick, *hand, *qingjian_hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)
    asked: list[tuple[str, ...]] = []
    qingjian: list[Any] = []
    depths: list[int] = []

    def user(query):
        if query.kind == QueryKind.PLAY_PHASE:
            action = _find(
                query,
                lambda action: isinstance(action, UseCard)
                and action.card.card_id == trick
                and action.card.type == "duel",
            )
            if action is not None:
                return action
            return _find(query, lambda action: isinstance(action, EndPhase))
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_QINGJIAN:
            qingjian.append(query)
            # 读到这一问时内层已经挂起：栈里 = 外层（连营）+ 内层（清俭的这次摸牌）。
            depths.append(len(engine._move_pending))
            # 拒绝发动：内层只挂起这一次（不再有选牌 / 选人两步），判据仍是"外层接着跑"。
            return _find(query, lambda action: isinstance(action, Pass))
        return None

    def victim(query):
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_QIANXUN:
            return _activate(query, JIE_QIANXUN)
        if query.kind == QueryKind.SKILL_ACTIVATION and query.skill_id == JIE_LIANYING:
            return _activate(query, JIE_LIANYING)
        if query.kind == QueryKind.SELECT_OPTION and query.skill_id.startswith("lianying:"):
            asked.append(tuple(query.options))
            # 先选座位 0（触发清俭的内层摸牌），再选座位 1。
            return _seat_option(query, USER if len(asked) == 1 else VICTIM)
        return None

    run_turns(engine, [Scripted(user), Scripted(victim)], turns=1)

    assert len(asked) == 2, "X = 2 的两张手牌 ⇒ 两次选人询问"
    assert asked[0] == ("seat:0", "seat:1")
    assert asked[1] == ("seat:1",), "同一步的第二次询问排除已选的角色"
    assert len(qingjian) == 1, "座位 0 获得牌 ⇒ 清俭问一次（内层挂起的来源）"
    assert depths == [2], "内层挂起时外层（连营）仍在挂起栈里（单槽会只剩 1）"
    assert not engine._move_pending, "两座都摸完 ⇒ 挂起栈清空"
    placement = _hero_deltas(engine, VICTIM)[0]
    draws = _draw_records_until_return(engine, placement.seq)
    assert [(record.to_zone.seat, len(record.cards)) for record in draws] == [
        (USER, 1),
        (VICTIM, 1),
    ], "内层（座位 0）答完后，外层【连营】必须接着给座位 1 摸一张"


def test_a_zero_card_loss_does_not_leave_lianying_available() -> None:
    """裁定（X 可为 0）：本次移动没有失去手牌 ⇒ X = 0、条件不成立（不问、不摸）。

    X = 0 在真实移动里到不了（``move_card`` 拒绝空批），但裁定要求这条出口显式存在——
    故在**钩子条件**这个派发缝上给正 / 反两侧：同一局面，有 1 张手牌时条件为真、空批为假。
    """
    from sgs.skills.catalog import MATCH_OWNER_KEY
    from sgs.skills.move_after import MOVE_EVENT_KEY, MoveEvent

    class _Frame:
        def __init__(self, **data: Any) -> None:
            self.data = data

    class _Host:
        def __init__(self, state) -> None:
            self.state = state

    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3), SeatSpec()],
        deck=(),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    hook = next(
        hook for hook in CATALOG[JIE_LIANYING].hooks if hook.condition is not None
    )
    host = _Host(state)

    def frame(cards: tuple) -> _Frame:
        return _Frame(
            **{
                MOVE_EVENT_KEY: MoveEvent(
                    cards=cards,
                    from_zone=ZoneRef(Zone.HAND, 0),
                    to_zone=ZoneRef(Zone.DISCARD),
                    reason=MoveReason.DISCARD,
                    owner=0,
                    after_holder=None,
                    actor=0,
                ),
                MATCH_OWNER_KEY: 0,
            }
        )

    empty = state.cards[0]
    assert hook.condition(host, frame((empty,))) is True, "手里最后一张被失去 ⇒ 条件成立（X = 1）"
    assert hook.condition(host, frame(())) is False, "X = 0 ⇒ 不发动（连是否发动都不问）"


def test_dead_players_hero_card_cards_go_to_the_discard_pile() -> None:
    """阵亡清区（规则 15.3）：武将牌上的牌全部进弃牌堆（跨座位的区同样如此）。"""
    hand = (0, 1)
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_LUXUN, hp=1, hp_max=3, hand=hand), SeatSpec()],
        deck=deck_excluding(PACK, *hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    cross = hero_card_zone_key(1, "hero.jie_xusheng.pojun")
    state.player(0).hero_cards[QIANXUN_ZONE] = HeroCardZone(
        skill_id=JIE_QIANXUN, owner=0, cards=[hand[0]]
    )
    state.player(0).hero_cards[cross] = HeroCardZone(
        skill_id="hero.jie_xusheng.pojun", owner=1, cards=[hand[1]]
    )
    state.player(0).hand = []
    engine = Engine.from_state(state, seed=7)
    discard_before = list(engine.state.discard)

    declare_death(engine, 0)
    while not engine.finished:
        engine._step()

    assert engine.state.hero_zone(0, QIANXUN_ZONE) == ()
    assert engine.state.hero_zone(0, cross) == ()
    assert hand[0] in engine.state.discard and hand[1] in engine.state.discard
    assert len(engine.state.discard) == len(discard_before) + 2


# ---------------------------------------------------------------------------
# ⑤ 放置原语的边界：跨座位、空批、非放置类技能
# ---------------------------------------------------------------------------


def test_the_placement_primitive_is_cross_seat_and_returns_to_the_skill_owner() -> None:
    """原语支持跨座位（技能持有者 ≠ 武将牌主人），归还收件人 = 放置技能的持有者。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(0, 1)), SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3)],
        deck=deck_excluding(PACK, 0, 1),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)
    cross = hero_card_zone_key(1, JIE_QIANXUN)  # 座位 1 的技能实例，扣在座位 0 的武将牌上

    assert (
        engine.place_on_hero_card(
            None,
            owner=1,
            skill_id=JIE_QIANXUN,
            holder=0,
            cards=state.refs((0, 1)),
        )
        is None
    )
    assert engine.state.hero_zone(0, cross) == (0, 1), "跨座位的区落在宿主的武将牌上"
    assert engine.state.player(0).hand == [], "宿主的手牌被搬走"
    assert engine.state.player(1).hand == [], "持有者没有因此拿到牌"

    engine.return_hero_card_zones()

    assert engine.state.hero_zone(0, cross) == ()
    assert engine.state.player(1).hand == [0, 1], "归还给放置技能的持有者（不是武将牌主人）"


def test_placing_no_cards_is_a_no_op_and_does_not_create_a_zone() -> None:
    """边界（区为空）：空批不建区、不记 delta、不留下"发动过"的痕迹。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3), SeatSpec()],
        deck=deck_excluding(PACK),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)

    assert (
        engine.place_on_hero_card(
            None, owner=0, skill_id=JIE_QIANXUN, holder=0, cards=()
        )
        is None
    )
    assert engine.state.player(0).hero_cards == {}, "没有牌就没有区"
    assert engine.state.hero_zone(0, QIANXUN_ZONE) == ()


def test_the_placement_primitive_refuses_a_skill_that_is_not_registered_as_placement() -> None:
    """fail-closed：未登记为放置类的技能不能往武将牌上放牌（不静默造区）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3, hand=(0,)), SeatSpec()],
        deck=deck_excluding(PACK, 0),
        turn_seat=0,
        rule_config=RuleConfig(player_count=2),
    )
    engine = Engine.from_state(state, seed=7)

    with pytest.raises(ValueError, match="放置类技能"):
        engine.place_on_hero_card(
            None, owner=0, skill_id=JIE_LIANYING, holder=0, cards=state.refs((0,))
        )
    assert engine.state.player(0).hero_cards == {}


def test_a_move_after_question_stops_the_engine_before_the_next_frame_asks() -> None:
    """混合池回归（M6 票 12 暴露）：``MOVE_AFTER`` 挂起后，``_step`` 必须在询问处停住。

    复现（默认混合池 seed=148 的真实组合）：界陆逊【谦逊】把全部手牌放上武将牌 ⇒ 这次
    移动的 ``MOVE_AFTER`` 派发里【连营】发 ``SELECT_OPTION`` 问"令谁摸牌"。旧的
    ``_step`` 在 ``_flush_pending_move`` 返回后**继续走结算栈**，于是同一步又发出一条
    无关询问并覆盖 ``pending``；那条应答随后被【连营】的下一问错配，
    ``ask_option`` 的断言当场崩（``isinstance(action, (SelectOption, Pass))``）。

    判据 = **这一局跑得完**：两条会发问的 ``MOVE_AFTER`` 路径（【谦逊】的放置帧 +
    【连营】）在同一局里真的同场，正是"混合局可玩"的前提。
    """
    from sgs.agents import HeuristicPlayer
    from sgs.engine import ai_stream

    engine = Engine.new_game(
        seed=148,
        rule_config=RuleConfig(player_count=5),
        pack=PACK,
        heroes=("ganning", "liubei", "jie_luxun", "zhaoyun", "huatuo"),
    )
    engine.run([HeuristicPlayer(ai_stream(148, seat)) for seat in range(5)])

    assert engine.finished, "这一局必须跑到终局（挂起后不得再发第二条询问）"


# ---------------------------------------------------------------------------
# ⑤ 同一帧里的第二次【连营】（M6 票 20：``ask_option`` 的帧内答案缓存回归）
# ---------------------------------------------------------------------------


class _DoubleTriggerPlayer:
    """座位脚本：座位 1 用过河拆桥；座位 0 接受【谦逊】/【连营】，选人一律选自己。"""

    def __init__(self, seat: int) -> None:
        self.seat = seat

    def respond(self, query):  # noqa: ANN001 - Participant 协议
        if query.kind == QueryKind.PLAY_PHASE and self.seat == 1:
            for action in query.legal_actions:
                if (
                    getattr(action, "kind", None) == "UseCard"
                    and getattr(getattr(action, "card", None), "type", None)
                    == "dismantlement"
                ):
                    return action
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if query.kind == QueryKind.SKILL_ACTIVATION:
            for action in query.legal_actions:
                if getattr(action, "kind", None) == "ActivateSkill":
                    return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == QueryKind.SELECT_OPTION and str(query.skill_id).startswith(
            "lianying:"
        ):
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == "seat:0":
                    return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def test_a_second_lianying_trigger_in_the_same_frame_asks_a_fresh_question() -> None:
    """票 12 留下的隐患：``ask_option`` 的帧内缓存按 ``(skill_id, actor)`` 分桶。

    复现（混合池 seed=148 暴露过的组合，本测试用构造局面焊死）：界陆逊【谦逊】把全部
    手牌放上武将牌 ⇒ 这次移动的 ``MOVE_AFTER`` 里【连营】问"令谁摸牌"（答案选了座位 0，
    于是座位 0 摸回一张）。随后【过河拆桥】结算，弃掉刚摸的那张——陆逊**再次**失去最后
    的手牌 ⇒ 同一帧里【连营】第二次触发。

    旧实现：这一问的缓存键与上一次相同（``lianying:seat:0``），于是**不问**、直接复用上次
    的选择（记录流里凭空多摸一张，人类 / AI 都没被问过）。判据 = 同一帧里出现**两次**
    ``lianying:seat:0`` 询问；修好后第二次也真的发问。
    """
    dismantle = cards_of_type(PACK, "dismantlement")[0]
    hand0 = tuple(cards_of_type(PACK, "slash")[:2])
    used = {dismantle, *hand0}
    deck = tuple(index for index in range(len(PACK.deck.cards)) if index not in used)
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=JIE_LUXUN, hp=3, hp_max=3, hand=hand0),
            SeatSpec(hero="guanyu", hp=4, hand=(dismantle,)),
        ],
        deck=deck,
        turn_seat=1,
        rule_config=RuleConfig(player_count=2, first_seat=1),
    )
    engine = Engine.from_state(state, seed=3)
    players = [_DoubleTriggerPlayer(0), _DoubleTriggerPlayer(1)]

    def discarded_the_drawn_card(running) -> bool:
        return any(
            getattr(record, "delta_type", None) == "card_move"
            and record.reason is MoveReason.LOSE
            for record in running.records
        )

    run_until(engine, players, lambda running: running.finished or discarded_the_drawn_card(running))
    engine.run(players)

    lose = next(
        record
        for record in engine.records
        if getattr(record, "delta_type", None) == "card_move"
        and record.reason is MoveReason.LOSE
    )
    resolve_end = next(
        record
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.RESOLVE_END
        and record.seq > lose.seq
    )
    asked_again = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SELECT_OPTION
        and str(record.query.skill_id).startswith("lianying:")
        and lose.seq < record.seq < resolve_end.seq
    ]
    assert asked_again, (
        "同一帧里【连营】第二次触发没有发问（ask_option 的答案缓存跨触发复用）："
        f"弃牌（seq={lose.seq}）之后到 RESOLVE_END（seq={resolve_end.seq}）之间"
        "没有任何 lianying: 询问"
    )
    assert engine.finished, "修好缓存键后这一局仍必须跑完"
