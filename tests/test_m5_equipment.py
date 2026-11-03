"""M5-05a：四件军争装备技能（藤甲 / 白银狮子 / 古锭刀 / 骅骝）+ 青釭剑越过防具。

落点 = 库门面（主缝）：``Engine.from_state`` 的人为局面 + 记录流 + ``sgs.effects`` 的
只读判据（``distance`` / ``attack_range`` / ``target_is_effective`` / ``damage_amount``）。
军争牌只在 ``standard_ex_160`` 档里，故每个用例都显式选这一档（档位纪律）。

四件装备全部住**技能层**（``equip.<牌名 id>``，D8）：
藤甲 = 有效性修饰 + 目标侧火焰 +1；白银狮子 = 目标侧封顶 + 失去装备回复；
古锭刀 = 来源侧无手牌 +1；骅骝 = 距离 +1（复用既有 ``_DISTANCE_PLUS_ONE``）。
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from sgs.data import RuleConfig, load_content_pack
from sgs.effects import (
    CarrierKind,
    DamageNature,
    Modifier,
    ModifierPoint,
    attack_range,
    check_targets,
    damage_amount,
    distance,
    target_is_effective,
)
from sgs.engine import Engine
from sgs.skills import catalog as catalog_module
from sgs.skills.catalog import SkillDef
from sgs.state import EndPhase, FrameKind, MoveReason, Pass, QueryKind, UseCard, Zone
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_ex_160")

SLASH = cards_of_type(PACK, "slash")
FIRE_SLASH = cards_of_type(PACK, "fire_slash")
THUNDER_SLASH = cards_of_type(PACK, "thunder_slash")
BARBARIAN = cards_of_type(PACK, "barbarian_invasion")
ARROW_BARRAGE = cards_of_type(PACK, "arrow_barrage")
DUEL = cards_of_type(PACK, "duel")
PEACH = cards_of_type(PACK, "peach")
WINE = cards_of_type(PACK, "wine")

VINE = cards_of_type(PACK, "vine_armor")
SILVER = cards_of_type(PACK, "silver_lion")
SCIMITAR = cards_of_type(PACK, "ancient_scimitar")
HUALIU = cards_of_type(PACK, "hualiu")
JUYING = cards_of_type(PACK, "jueying")  # 标准 +1 防御马（同槽位）
CHITU = cards_of_type(PACK, "chitu")  # 标准 −1 进攻马
QINGGANG = cards_of_type(PACK, "qinggang_sword")
CROSSBOW = cards_of_type(PACK, "crossbow")
EIGHT_DIAGRAMS = cards_of_type(PACK, "eight_diagrams")


def _deck_excluding(*used: int) -> tuple[int, ...]:
    excluded = set(used)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _specs_used(specs) -> list[int]:
    return [card for spec in specs for card in (*spec.hand, *spec.equip, *spec.judge)]


def _state(specs, *, turn_seat: int = 0, deck=None):
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck if deck is not None else _deck_excluding(*_specs_used(specs)),
        turn_seat=turn_seat,
        # 人为局面按无身份局（2 人模式）跑回合环：本文件的对手应答不涉及身份，
        # 构造座位数只是测试便利（与 tests/test_equipment.py / test_normal_tricks.py 同姿态）。
        rule_config=RuleConfig(first_seat=0),
    )


# ---------------------------------------------------------------------------
# 马：骅骝（+1 防御坐骑）
# ---------------------------------------------------------------------------


def test_hualiu_adds_one_to_every_other_seats_distance_to_its_holder():
    state = _state([SeatSpec(equip=(HUALIU[0],)), SeatSpec(), SeatSpec(), SeatSpec()])

    assert distance(state, 1, 0) == 2, "相邻 1 + 防御马 +1"
    assert distance(state, 0, 1) == 1, "防御马不加自己到别人的距离（主体向）"


def test_hualiu_stacks_with_an_attack_mount_in_the_existing_chain_order():
    state = _state(
        [SeatSpec(equip=(HUALIU[0],)), SeatSpec(equip=(CHITU[0],)), SeatSpec(), SeatSpec()]
    )

    assert distance(state, 1, 0) == 1, "1（座位）+ 1（骅骝）− 1（赤兔）= 1；两条修饰器都在链上"


# ---------------------------------------------------------------------------
# 武器：古锭刀（攻击范围 2 / 目标无手牌 +1）
# ---------------------------------------------------------------------------


def test_ancient_scimitar_has_attack_range_two():
    state = _state(
        [SeatSpec(equip=(SCIMITAR[0],), hand=(SLASH[0],)), SeatSpec(), SeatSpec(), SeatSpec()]
    )

    assert attack_range(state, 0) == 2
    assert 2 in check_targets(state, 0, state.card(SLASH[0])), (
        "范围 2 ⇒ 距离 2 的座位 2 成为【杀】的合法目标"
    )


def test_ancient_scimitar_adds_one_when_the_target_has_no_hand_cards():
    state = _state([SeatSpec(equip=(SCIMITAR[0],)), SeatSpec()])

    assert (
        damage_amount(
            state, source=0, target=1, card=state.card(SLASH[0]), base=1, chain=False
        )
        == 2
    )


def test_ancient_scimitar_does_not_add_when_the_target_holds_a_hand_card():
    state = _state([SeatSpec(equip=(SCIMITAR[0],)), SeatSpec(hand=(PEACH[0],))])

    assert (
        damage_amount(
            state, source=0, target=1, card=state.card(SLASH[0]), base=1, chain=False
        )
        == 1
    )


def test_ancient_scimitar_does_not_add_to_non_slash_damage():
    state = _state([SeatSpec(equip=(SCIMITAR[0],)), SeatSpec()])

    assert (
        damage_amount(state, source=0, target=1, card=state.card(DUEL[0]), base=1, chain=False)
        == 1
    )


def test_ancient_scimitar_does_not_readd_on_a_conduction_frame():
    """来源侧加伤只在 ``chain=False`` 的源头帧派发：传导帧的基数已含它一次。"""
    state = _state([SeatSpec(equip=(SCIMITAR[0],)), SeatSpec()])

    assert (
        damage_amount(
            state, source=0, target=1, card=state.card(SLASH[0]), base=2, chain=True
        )
        == 2
    )


def _plus_one_for_source(state, ctx, current):
    """构造的「造成伤害 +1」来源侧修正（只作用于来源本人）。"""
    del state
    return current + 1 if ctx.owner == ctx.subject else current


def test_ancient_scimitar_stacks_with_another_source_side_bonus(monkeypatch):
    """同一段 ``DAMAGE_DEAL_AMOUNT`` 上的另一条 +1 与古锭刀相加：1 + 1 + 1 = 3。"""
    fake = SkillDef(
        skill_id="test.deal_plus_one",
        locked=True,
        carrier=CarrierKind.EQUIPMENT,
        modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _plus_one_for_source),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, fake.skill_id: fake}),
    )
    state = _state(
        [SeatSpec(equip=(SCIMITAR[0],), virtual_equipment=(fake.skill_id,)), SeatSpec()]
    )

    assert (
        damage_amount(
            state, source=0, target=1, card=state.card(SLASH[0]), base=1, chain=False
        )
        == 3
    ), "古锭刀 +1 与构造的另一条来源侧 +1 走同一段链，加法可叠加"


def _plus_one_for_source(state, ctx, current):
    """构造的「造成伤害 +1」来源侧修正（只作用于来源本人）。"""
    del state
    return current + 1 if ctx.owner == ctx.subject else current


# ---------------------------------------------------------------------------
# 藤甲：普通【杀】/ 南蛮 / 万箭无效 + 火焰伤害 +1（目标侧）
# ---------------------------------------------------------------------------


def test_vine_armor_makes_a_normal_slash_ineffective_against_its_holder():
    state = _state([SeatSpec(), SeatSpec(equip=(VINE[0],))])

    assert target_is_effective(state, state.card(SLASH[0]), 0, 1) is False


def test_vine_armor_makes_barbarian_invasion_and_arrow_barrage_ineffective():
    state = _state([SeatSpec(), SeatSpec(equip=(VINE[0],))])

    assert target_is_effective(state, state.card(BARBARIAN[0]), 0, 1) is False
    assert target_is_effective(state, state.card(ARROW_BARRAGE[0]), 0, 1) is False


def test_vine_armor_does_not_stop_fire_or_thunder_slashes():
    """火杀 / 雷杀是独立牌型（D9）：藤甲只挡**产物牌型 slash**，不挡属性【杀】。"""
    state = _state([SeatSpec(), SeatSpec(equip=(VINE[0],))])

    assert target_is_effective(state, state.card(FIRE_SLASH[0]), 0, 1) is True
    assert target_is_effective(state, state.card(THUNDER_SLASH[0]), 0, 1) is True


def test_vine_armor_only_protects_its_own_holder():
    state = _state([SeatSpec(equip=(VINE[0],)), SeatSpec()])

    assert target_is_effective(state, state.card(SLASH[0]), 1, 0) is False, "藤甲在座位 0"
    assert target_is_effective(state, state.card(SLASH[0]), 0, 1) is True, "无藤甲的座位照常有效"


def test_vine_armor_adds_one_to_fire_damage_taken():
    state = _state([SeatSpec(), SeatSpec(equip=(VINE[0],))])

    assert (
        damage_amount(
            state,
            source=0,
            target=1,
            card=state.card(FIRE_SLASH[0]),
            base=1,
            chain=False,
            nature=DamageNature.FIRE,
        )
        == 2
    )


def test_vine_armor_does_not_add_to_normal_or_thunder_damage():
    state = _state([SeatSpec(), SeatSpec(equip=(VINE[0],))])

    normal = damage_amount(
        state,
        source=0,
        target=1,
        card=state.card(SLASH[0]),
        base=1,
        chain=False,
        nature=DamageNature.NORMAL,
    )
    thunder = damage_amount(
        state,
        source=0,
        target=1,
        card=state.card(THUNDER_SLASH[0]),
        base=1,
        chain=False,
        nature=DamageNature.THUNDER,
    )

    assert (normal, thunder) == (1, 1)


def test_qinggang_sword_bypasses_vine_armors_normal_slash_immunity():
    state = _state([SeatSpec(equip=(QINGGANG[0],)), SeatSpec(equip=(VINE[0],))])

    assert target_is_effective(state, state.card(SLASH[0]), 0, 1) is True, (
        "无视防具 ⇒ 普通【杀】照常有效"
    )


def test_qinggang_sword_bypasses_vine_armors_fire_damage_bonus():
    """`ignore_armor` 在**目标侧伤害链**里同样先置：火焰 +1 也被越过。

    渠道牌用普通【杀】+ 帧属性 FIRE：M5-05a 的【青釭剑】仍只对产物牌型 ``slash`` 置标志
    （【杀】族通用判据是 M5-05b），故"青釭剑砍火杀"还构不出来；本用例钉的是**伤害链读同一
    个 flag** 这条合同，属性由帧给。
    """
    armed = _state([SeatSpec(equip=(QINGGANG[0],)), SeatSpec(equip=(VINE[0],))])
    bare = _state([SeatSpec(), SeatSpec(equip=(VINE[0],))])

    assert (
        damage_amount(
            armed,
            source=0,
            target=1,
            card=armed.card(SLASH[0]),
            base=1,
            chain=False,
            nature=DamageNature.FIRE,
        )
        == 1
    ), "青釭剑越过藤甲 ⇒ 火焰 +1 不生效"
    assert (
        damage_amount(
            bare,
            source=0,
            target=1,
            card=bare.card(SLASH[0]),
            base=1,
            chain=False,
            nature=DamageNature.FIRE,
        )
        == 2
    ), "对照：没有青釭剑时同一份火焰伤害 +1"


# ---------------------------------------------------------------------------
# 白银狮子：受到伤害最多 1 点（目标侧封顶，加伤之后）+ 青釭剑读法
# ---------------------------------------------------------------------------


def test_silver_lion_caps_three_damage_to_one():
    state = _state([SeatSpec(), SeatSpec(equip=(SILVER[0],))])

    assert (
        damage_amount(
            state,
            source=0,
            target=1,
            card=state.card(SLASH[0]),
            base=3,
            chain=False,
            nature=DamageNature.NORMAL,
        )
        == 1
    )


def test_silver_lion_leaves_one_damage_at_one():
    state = _state([SeatSpec(), SeatSpec(equip=(SILVER[0],))])

    assert (
        damage_amount(
            state,
            source=0,
            target=1,
            card=state.card(SLASH[0]),
            base=1,
            chain=False,
            nature=DamageNature.NORMAL,
        )
        == 1
    )


def test_silver_lion_cap_is_applied_after_an_added_point():
    """古锭刀 + 白银狮子：1 + 1 = 2 之后才封顶 ⇒ 实际只受 1 点。

    两条修饰器分处两段链（``DAMAGE_DEAL_AMOUNT`` 来源侧 → ``DAMAGE_TAKE_AMOUNT`` /
    ``DAMAGE_CAP`` 目标侧）；``damage_amount`` 的实现次序就是"先加后封"的构造。
    """
    state = _state(
        [SeatSpec(equip=(SCIMITAR[0],)), SeatSpec(equip=(SILVER[0],))]
    )

    assert (
        damage_amount(
            state,
            source=0,
            target=1,
            card=state.card(SLASH[0]),
            base=1,
            chain=False,
            nature=DamageNature.NORMAL,
        )
        == 1
    )


def test_qinggang_sword_bypasses_silver_lions_damage_cap():
    """青釭剑的读法：无视防具 ⇒ 白银狮子的封顶也不生效（防具的整体效果）。"""
    armed = _state([SeatSpec(equip=(QINGGANG[0],)), SeatSpec(equip=(SILVER[0],))])
    bare = _state([SeatSpec(), SeatSpec(equip=(SILVER[0],))])

    assert (
        damage_amount(
            armed,
            source=0,
            target=1,
            card=armed.card(SLASH[0]),
            base=3,
            chain=False,
            nature=DamageNature.NORMAL,
        )
        == 3
    ), "青釭剑越过白银狮子 ⇒ 3 点照常"
    assert (
        damage_amount(
            bare,
            source=0,
            target=1,
            card=bare.card(SLASH[0]),
            base=3,
            chain=False,
            nature=DamageNature.NORMAL,
        )
        == 1
    ), "对照：没有青釭剑时封顶到 1"


# ---------------------------------------------------------------------------
# 驱动：脚本座位 + 逐步推进（装备使用 / 失去装备 / 伤害三组共用）
# ---------------------------------------------------------------------------


def _minimal(query):
    """最小合法应答：出牌阶段结束、响应 / 濒死放弃、其余取第一个候选。"""
    if query.kind == QueryKind.PLAY_PHASE:
        return next(a for a in query.legal_actions if isinstance(a, EndPhase))
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


class _Scripted:
    """一个座位：``handler`` 给不出应答时退到最小合法应答。"""

    def __init__(self, handler=None) -> None:
        self.handler = handler

    def respond(self, query):
        if self.handler is not None:
            action = self.handler(query)
            if action is not None:
                return action
        return _minimal(query)


def _advance(engine, players, stop, *, limit: int = 60_000):
    """逐步推进到 ``stop(engine)`` 成立（由测试直接驱动 ``_step`` / ``submit``）。"""
    for _ in range(limit):
        if stop(engine) or engine.finished:
            return engine
        if engine.pending is None:
            engine._step()
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _use(query, card_id: int, target: int):
    for action in query.legal_actions:
        if (
            isinstance(action, UseCard)
            and action.card.card_id == card_id
            and action.targets == (target,)
        ):
            return action
    return None


def _play_cards(seat_specs, card_ids, *, stop=None, seed: int = 1):
    """座位 0 按次序使用 ``card_ids``；跑到全部用完且回到出牌阶段即停。"""
    state = _state(seat_specs)
    engine = Engine.from_state(state, seed=seed)
    pending = list(card_ids)

    def handler(query):
        if (
            query.kind == QueryKind.PLAY_PHASE
            and query.actor == 0
            and pending
            and engine.pending is not None
        ):
            action = _use(query, pending[0], 0)
            if action is not None:
                pending.pop(0)
                return action
        return None

    condition = stop or (
        lambda e: not pending
        and e.pending is not None
        and e.pending.kind == QueryKind.PLAY_PHASE
    )
    players = [_Scripted(handler)] + [_Scripted() for _ in range(len(seat_specs) - 1)]
    _advance(engine, players, condition)
    return engine


def _frames(engine, kind: FrameKind):
    return [
        record
        for record in engine.records
        if record.kind == "frame" and record.op == "push" and record.frame_kind is kind
    ]


def _hp_deltas(engine):
    return [
        (record.seat, record.before, record.after)
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "hp_change"
    ]


def _equip_replacement_order(engine):
    """顶替路径的三类关键记录，**按记录流原样**取出（票 06 §5 / `CODING_STANDARDS.md` §4）。

    旧装备离开装备区 / ``hp`` 变化 / 新装备进入装备区各是唯一事件，故这条列表就是
    "顶替这条路径"的次序本身——不 ``sorted``、不去重、不取集合。
    """
    order = []
    for record in engine.records:
        if record.kind != "delta":
            continue
        if record.delta_type == "card_move":
            if record.reason is MoveReason.LOSE and record.from_zone.zone is Zone.EQUIP:
                order.append(("leave", tuple(card.card_id for card in record.cards)))
            elif record.reason is MoveReason.PUT and record.to_zone.zone is Zone.EQUIP:
                order.append(("enter", tuple(card.card_id for card in record.cards)))
        elif record.delta_type == "hp_change":
            order.append(("hp", record.seat, record.before, record.after))
    return order


# ---------------------------------------------------------------------------
# 白银狮子（二）：失去装备时回复 1 点（走正常 RECOVER 帧）
# ---------------------------------------------------------------------------


def test_losing_silver_lion_recovers_one_through_the_recover_frame():
    engine = _play_cards(
        [
            SeatSpec(hp=2, hp_max=4, equip=(SILVER[0],), hand=(EIGHT_DIAGRAMS[0],)),
            SeatSpec(),
        ],
        (EIGHT_DIAGRAMS[0],),
    )

    assert engine.state.player(0).equip == [EIGHT_DIAGRAMS[0]], "新防具顶替白银狮子"
    assert SILVER[0] in engine.state.discard
    assert engine.state.player(0).hp == 3, "失去白银狮子 ⇒ 回复 1 点"
    assert _hp_deltas(engine) == [(0, 2, 3)]
    assert len(_frames(engine, FrameKind.RECOVER)) == 1, "回复走正常 RECOVER 帧，不直接改 HP"
    assert any(
        record.kind == "timing" and record.timing is Timing.RECOVER_VALUE
        for record in engine.records
    ), "RECOVER_VALUE 时机照常派发"


def test_replacing_silver_lion_recovers_before_the_new_equipment_enters():
    """票 06：用**新**装备牌顶替白银狮子时，它"失去装备 ⇒ 回复 1 点"要先结算完。

    记录流次序必须是：旧装备进弃牌堆 → ``hp x→x+1``（RECOVER 帧的结算）→ 新装备进装备区。
    修前 ``move_used_card_to_equip_zone`` 丢掉旧装备那次移动的 ``"stay"``，于是同一趟里
    接着把新装备 ``PUT`` 进槽——记录流里新装备的 ``card_move`` 排在 ``hp_change`` **之前**
    （``hp`` 数值与帧数都不变，故旧回归看不见）。
    """
    engine = _play_cards(
        [
            SeatSpec(hp=1, hp_max=4, equip=(SILVER[0],), hand=(EIGHT_DIAGRAMS[0],)),
            SeatSpec(),
        ],
        (EIGHT_DIAGRAMS[0],),
    )

    assert _equip_replacement_order(engine) == [
        ("leave", (SILVER[0],)),
        ("hp", 0, 1, 2),
        ("enter", (EIGHT_DIAGRAMS[0],)),
    ], "次序 = 旧装备离场 → 回复 → 新装备入场（比较序列本身，不排序）"
    # 本票只修**顺序**：`hp` 的数值与 `RECOVER` 帧数一个是 1 → 2、一个是恰好一张，都不许变。
    assert engine.state.player(0).hp == 2
    assert _hp_deltas(engine) == [(0, 1, 2)]
    assert len(_frames(engine, FrameKind.RECOVER)) == 1
    # 顶替恰好发生一次：装备区只剩新装备，旧装备在弃牌堆里只有那一张。
    assert engine.state.player(0).equip == [EIGHT_DIAGRAMS[0]], "新防具顶替白银狮子"
    assert engine.state.discard.count(SILVER[0]) == 1


def test_losing_another_equipment_while_wearing_silver_lion_does_not_recover():
    engine = _play_cards(
        [
            SeatSpec(
                hp=2,
                hp_max=4,
                equip=(SILVER[0], CROSSBOW[0]),
                hand=(QINGGANG[0],),
            ),
            SeatSpec(),
        ],
        (QINGGANG[0],),
    )

    assert SILVER[0] in engine.state.player(0).equip, "被顶替的是武器，白银狮子还在"
    assert CROSSBOW[0] in engine.state.discard
    assert engine.state.player(0).hp == 2, "失去的不是白银狮子 ⇒ 不回复"
    assert _frames(engine, FrameKind.RECOVER) == []


def test_a_second_defense_mount_replaces_hualiu_instead_of_stacking():
    engine = _play_cards(
        [SeatSpec(equip=(HUALIU[0],), hand=(JUYING[0],)), SeatSpec(), SeatSpec(), SeatSpec()],
        (JUYING[0],),
    )

    assert engine.state.player(0).equip == [JUYING[0]], "同槽位任一时刻至多一张"
    assert HUALIU[0] in engine.state.discard
    assert distance(engine.state, 1, 0) == 2, "只算一张 +1 防御马，不叠成 3"


# ---------------------------------------------------------------------------
# 端到端：真实伤害帧走 `damage_finalize_amount`（属性透传 / 封顶 / 来源侧 +1）
# ---------------------------------------------------------------------------


def _use_chain_done(engine) -> bool:
    """这张牌的使用链整个跑完（USE 帧已出栈）。"""
    return any(
        record.kind == "frame" and record.op == "pop" and record.frame_kind is FrameKind.USE
        for record in engine.records
    )


def _attack(seat_specs, card_id: int, *, target: int = 1):
    """座位 0 用 ``card_id`` 打 ``target``，跑到这次使用链跑完。"""
    state = _state(seat_specs)
    engine = Engine.from_state(state, seed=1)

    def handler(query):
        if query.kind == QueryKind.PLAY_PHASE and query.actor == 0:
            return _use(query, card_id, target)
        return None

    players = [_Scripted(handler)] + [_Scripted() for _ in range(len(seat_specs) - 1)]
    _advance(engine, players, _use_chain_done)
    return engine


def test_a_fire_slash_deals_two_to_vine_armor_through_the_real_damage_frame():
    """火的 +1 由 DAMAGE 帧的 ``nature`` 透传进 ``damage_amount``，不是测试里手调的。"""
    engine = _attack(
        [SeatSpec(hand=(FIRE_SLASH[0],)), SeatSpec(equip=(VINE[0],))], FIRE_SLASH[0]
    )

    assert _hp_deltas(engine) == [(1, 4, 2)], "火杀 1 + 藤甲 +1"
    assert any(
        record.kind == "query" and record.query.kind == QueryKind.DODGE
        for record in engine.records
    ), "火杀不被藤甲无效，仍照常开【闪】窗口"


def test_a_normal_slash_is_ineffective_against_vine_armor_end_to_end():
    engine = _attack(
        [SeatSpec(hand=(SLASH[0],)), SeatSpec(equip=(VINE[0],))], SLASH[0]
    )

    assert engine.state.player(1).hp == 4
    assert _hp_deltas(engine) == []
    assert not any(
        record.kind == "query" and record.query.kind == QueryKind.DODGE
        for record in engine.records
    ), "无效 ⇒ C1 即终止，不开【闪】窗口"


def test_wine_bonus_is_capped_by_silver_lion_after_the_addition():
    """真实的"先加后封"：酒 +1 ⇒ 2 点，再被白银狮子封到 1 点。"""
    wine, slash = WINE[0], SLASH[0]
    state = _state([SeatSpec(hand=(wine, slash)), SeatSpec(equip=(SILVER[0],))])
    engine = Engine.from_state(state, seed=1)

    def handler(query):
        if query.kind == QueryKind.PLAY_PHASE and query.actor == 0:
            hand = engine.state.player(0).hand
            if wine in hand:
                return _use(query, wine, 0)
            if slash in hand:
                return _use(query, slash, 1)
        return None

    players = [_Scripted(handler), _Scripted()]
    _advance(
        engine,
        players,
        lambda e: sum(
            1
            for record in e.records
            if record.kind == "frame"
            and record.op == "pop"
            and record.frame_kind is FrameKind.USE
        )
        >= 2,
    )  # 【酒】与【杀】各一条 USE 链

    assert _hp_deltas(engine) == [(1, 4, 3)], "1 + 1（酒）先落地，再封顶到 1"


def test_ancient_scimitar_adds_one_end_to_end_against_an_empty_handed_target():
    engine = _attack(
        [SeatSpec(equip=(SCIMITAR[0],), hand=(SLASH[0],)), SeatSpec()], SLASH[0]
    )

    assert _hp_deltas(engine) == [(1, 4, 2)]


def test_ancient_scimitar_skips_a_target_that_still_holds_a_card():
    engine = _attack(
        [SeatSpec(equip=(SCIMITAR[0],), hand=(SLASH[0],)), SeatSpec(hand=(PEACH[0],))],
        SLASH[0],
    )

    assert _hp_deltas(engine) == [(1, 4, 3)]


# ---------------------------------------------------------------------------
# 四件装备都走既有装备机器：装备 / 替换 / 被拆 / 被顺
# ---------------------------------------------------------------------------

DISMANTLEMENT = cards_of_type(PACK, "dismantlement")
SNATCH = cards_of_type(PACK, "snatch")

M5_EQUIPMENT_CASES = (
    ("silver_lion", SILVER[0], EIGHT_DIAGRAMS[0]),
    ("vine_armor", VINE[0], EIGHT_DIAGRAMS[0]),
    ("ancient_scimitar", SCIMITAR[0], CROSSBOW[0]),
    ("hualiu", HUALIU[0], JUYING[0]),
)
"""``(牌名, 这张实体牌, 同槽位的标准替代牌)``：四件军争装备各一条。"""


@pytest.mark.parametrize("card_id,replacement", [(c, r) for _n, c, r in M5_EQUIPMENT_CASES])
def test_each_m5_equipment_can_be_equipped_and_replaced_normally(card_id, replacement):
    equipped = _play_cards([SeatSpec(hand=(card_id,)), SeatSpec()], (card_id,))
    assert equipped.state.player(0).equip == [card_id], "装备牌经既有 PUT 路径进装备区"

    replaced = _play_cards(
        [SeatSpec(equip=(card_id,), hand=(replacement,)), SeatSpec()],
        (replacement,),
    )
    assert replaced.state.player(0).equip == [replacement], "同槽位替换成功"
    assert card_id in replaced.state.discard, "被顶替的旧装备进弃牌堆"


def _trick_on_equipment(
    trick_card: int, equipment_card: int, *, attacker_equip: tuple[int, ...] = ()
) -> Engine:
    """座位 0 对座位 1 的装备用一张拆 / 顺；选择面取唯一合法项（目标只有这一张牌）。

    ``attacker_equip``：顺手牵羊要过距离闸门；目标是【骅骝】时距离 1 + 1 = 2，故给使用者
    先装一张 −1 进攻马，把距离压回 1（这两条修饰器本来就该按既有链序叠加）。
    """
    state = _state(
        [
            SeatSpec(hand=(trick_card,), equip=attacker_equip),
            SeatSpec(equip=(equipment_card,)),
        ]
    )
    engine = Engine.from_state(state, seed=1)
    used = False

    def handler(query):
        nonlocal used
        if query.kind == QueryKind.PLAY_PHASE and query.actor == 0 and not used:
            action = _use(query, trick_card, 1)
            if action is not None:
                used = True
                return action
        if query.kind == QueryKind.SELECT_CARDS:
            return query.legal_actions[0]
        return None

    players = [_Scripted(handler), _Scripted()]
    _advance(engine, players, _use_chain_done)
    return engine


@pytest.mark.parametrize("card_id", [c for _n, c, _r in M5_EQUIPMENT_CASES])
def test_each_m5_equipment_can_be_dismantled_normally(card_id):
    engine = _trick_on_equipment(DISMANTLEMENT[0], card_id)

    assert engine.state.player(1).equip == []
    assert card_id in engine.state.discard


@pytest.mark.parametrize("card_id", [c for _n, c, _r in M5_EQUIPMENT_CASES])
def test_each_m5_equipment_can_be_snatched_normally(card_id):
    engine = _trick_on_equipment(SNATCH[0], card_id, attacker_equip=(CHITU[0],))

    assert engine.state.player(1).equip == []
    assert card_id in engine.state.player(0).hand
