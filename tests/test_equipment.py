"""装备区、距离与攻击范围（施工票 29）。

落点两处，与票 29 的"测试缝"一致：

- **规则函数**（主缝内的纯函数，M1 先例 ``tests/test_use_and_resolve.py`` 已直接断言
  ``distance()``）：距离 / 座位距离 / 攻击范围 / 候选池；
- **库门面**（唯一"真"缝）：``Engine.from_state`` 的人为局面 + 脚本玩家，断言记录流里的
  区域迁移（``PUT`` / ``LOSE``）、响应窗口的有无、体力变化与候选组合。

不测修饰器注册表内部结构（票 17 §"明确不开的缝"）：行为全部经候选、距离与 delta 流观测。
"""

from __future__ import annotations

from sgs.data import Color, RuleConfig, color_of_suit, load_content_pack
from sgs.effects import (
    attack_range,
    check_count,
    check_targets,
    distance,
    legal_actions_for,
    seat_distance,
    target_is_effective,
    targets_within_attack_range,
)
from sgs.engine import Engine
from sgs.state import FrameKind, QueryKind, UseCard
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, cards_of_type, run_until

PACK = load_content_pack(deck_profile="standard_108")


def _cards(type_id: str) -> list[int]:
    return cards_of_type(PACK, type_id)


def _suit(card_id: int) -> str:
    return PACK.deck.cards[card_id].suit.value


def _first_by_color(type_id: str, *, black: bool) -> int:
    """取一张指定颜色的实体牌；颜色由 :func:`sgs.data.color_of_suit` 派生（不重抄映射）。"""
    wanted = Color.BLACK if black else Color.RED
    return next(
        card_id for card_id in _cards(type_id) if color_of_suit(_suit(card_id)) is wanted
    )


CROSSBOW = _cards("crossbow")[0]
QINGGANG = _cards("qinggang_sword")[0]
HALBERD = _cards("halberd")[0]
QILIN = _cards("qilin_bow")[0]
RENWANG = _cards("renwang_shield")[0]
EIGHT_DIAGRAMS = _cards("eight_diagrams")[0]
CHITU = _cards("chitu")[0]  # −1 进攻马
JUYING = _cards("jueying")[0]  # +1 防御马
SLASH = _cards("slash")
BLACK_SLASH = _first_by_color("slash", black=True)
RED_SLASH = _first_by_color("slash", black=False)


# ---------------------------------------------------------------------------
# 距离：座位距离 + 坐骑（票 12 §1）
# ---------------------------------------------------------------------------


def test_seat_distance_counts_living_seats_between_on_both_directions():
    state = build_state(pack=PACK, seats=[SeatSpec() for _ in range(4)], turn_seat=0)

    assert seat_distance(state, 0, 1) == 1, "相邻两人中间 0 个活人 ⇒ 1"
    assert seat_distance(state, 0, 2) == 2
    assert seat_distance(state, 0, 3) == 1, "另一方向中间 0 个活人 ⇒ 1"
    assert seat_distance(state, 0, 0) == 0


def test_distance_without_mounts_is_exactly_the_seat_distance():
    """无装备时管道恒等，M1 的断言与公式一句不改（票 12 §1）。"""
    state = build_state(pack=PACK, seats=[SeatSpec() for _ in range(4)], turn_seat=0)

    for a in range(4):
        for b in range(4):
            assert distance(state, a, b) == seat_distance(state, a, b)


def test_a_plus_one_mount_on_the_target_adds_one_to_the_distance():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(), SeatSpec(equip=(JUYING,)), SeatSpec()],
        turn_seat=0,
    )

    assert distance(state, 0, 2) == 3, "座位距离 2 + 目标 +1 坐骑 = 3"


def test_a_minus_one_mount_on_the_subject_subtracts_one_from_the_distance():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CHITU,)), SeatSpec(), SeatSpec(), SeatSpec()],
        turn_seat=0,
    )

    assert distance(state, 0, 2) == 1, "座位距离 2 − 自身 −1 坐骑 = 1"


def test_mounts_from_both_sides_compose_on_the_same_distance():
    """+1 属客体、−1 属主体，可能分属不同角色——链式管道保证两者都命中（票 07 §3）。"""
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(equip=(CHITU,)),
            SeatSpec(),
            SeatSpec(equip=(JUYING,)),
            SeatSpec(),
        ],
        turn_seat=0,
    )

    assert distance(state, 0, 2) == 2, "2 − 1（自身进攻马）+ 1（目标防御马）= 2"


def test_distance_has_a_floor_of_one_between_distinct_living_seats():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CHITU,)), SeatSpec(), SeatSpec(), SeatSpec()],
        turn_seat=0,
    )

    assert distance(state, 0, 1) == 1, "相邻距离 1 − 1 = 0 ⇒ 下限夹到 1"
    assert distance(state, 0, 0) == 0, "自身恒 0，不参与下限"


def test_the_mount_direction_is_read_from_the_subject_target_roles():
    """+1 只加"别人到你"、−1 只减"你到别人"（`R:745-749`）。"""
    defender = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(JUYING,)), SeatSpec(), SeatSpec(), SeatSpec()],
        turn_seat=0,
    )
    attacker = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CHITU,)), SeatSpec(), SeatSpec(), SeatSpec()],
        turn_seat=0,
    )

    assert distance(defender, 2, 0) == 3, '座位 2 到装防御马的座位 0：+1'
    assert distance(defender, 0, 2) == 2, '反向不变：防御马不加"你到别人"'
    assert distance(attacker, 0, 2) == 1, '装进攻马的座位 0 到座位 2：−1'
    assert distance(attacker, 2, 0) == 2, '反向不变：进攻马不减"别人到你"'


# ---------------------------------------------------------------------------
# 攻击范围：真读武器牌（票 12 §1）
# ---------------------------------------------------------------------------


def test_attack_range_is_one_without_a_weapon():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(EIGHT_DIAGRAMS,)), SeatSpec()],
        turn_seat=0,
    )

    assert attack_range(state, 0) == 1, "防具不是武器，不改攻击范围"


def test_attack_range_reads_the_weapon_card_value():
    state = build_state(pack=PACK, seats=[SeatSpec(equip=(QILIN,)), SeatSpec()], turn_seat=0)

    assert attack_range(state, 0) == 5


def test_a_weapon_makes_a_farther_target_legal_for_slash():
    slash = SLASH[0]
    seats = [SeatSpec(hand=(slash,)), SeatSpec(), SeatSpec(), SeatSpec()]

    unarmed = build_state(pack=PACK, seats=seats, turn_seat=0)
    armed_seats = [SeatSpec(hand=(slash,), equip=(QINGGANG,)), *seats[1:]]
    armed = build_state(pack=PACK, seats=armed_seats, turn_seat=0)

    assert set(check_targets(unarmed, 0, unarmed.card(slash))) == {1, 3}, (
        "无武器范围 1：只够得到相邻座位"
    )
    assert set(check_targets(armed, 0, armed.card(slash))) == {1, 2, 3}, (
        "青釭剑范围 2：距离 2 的座位 2 也进池"
    )
    assert set(targets_within_attack_range(armed, 0)) == {1, 2, 3}


def test_attack_range_is_not_modified_by_the_distance_pipeline():
    """坐骑改座位距离、武器改攻击范围；攻击范围**不**经 DISTANCE 挂点（票 12 §Q22）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(QILIN, CHITU)), SeatSpec()],
        turn_seat=0,
    )

    assert attack_range(state, 0) == 5, "进攻马只改距离，不改攻击范围"


# ---------------------------------------------------------------------------
# 装备牌的使用：置入装备区与"顶替"（票 12 §2）
# ---------------------------------------------------------------------------


class _EquipUser:
    """脚本玩家：按给定次序使用装备牌，其余询问交给 :class:`RecordingPlayer` 的最小应答。"""

    def __init__(self, seat: int, card_ids: tuple[int, ...]) -> None:
        self.seat = seat
        self.pending = list(card_ids)
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if query.kind == "PLAY_PHASE" and self.pending:
            wanted = self.pending[0]
            for action in query.legal_actions:
                if isinstance(action, UseCard) and action.card.card_id == wanted:
                    self.pending.pop(0)
                    return action
        return self._fallback.respond(query)


def _run_equipment_use(*, card_ids, stop, hand=(), equip=(), seats=2, seed=1):
    """构造一个"座位 0 手握 / 已装指定牌"的局面并跑到 ``stop`` 成立。

    牌堆补上**其余全部实体牌**：``build_state`` 的默认空牌堆会让摸牌阶段直接撞上
    "两堆皆空 ⇒ 平局"（票 14），那样根本走不到出牌阶段。
    """
    used = list(hand) + list(equip)
    deck = tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used)
    specs = [SeatSpec(hand=tuple(hand), equip=tuple(equip))]
    for _ in range(seats - 1):
        specs.append(SeatSpec())
    state = build_state(pack=PACK, seats=specs, deck=deck, turn_seat=0)
    engine = Engine.from_state(state, seed=seed)
    players = [_EquipUser(0, card_ids)] + [_EquipUser(seat, ()) for seat in range(1, seats)]
    run_until(engine, players, stop)
    return engine


def _moves_of(engine, card_id):
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


def test_using_an_equipment_card_leaves_it_in_the_equipment_zone():
    engine = _run_equipment_use(
        card_ids=(CROSSBOW,),
        hand=(CROSSBOW,),
        stop=lambda engine: bool(engine.state.player(0).equip),
    )

    assert engine.state.player(0).equip == [CROSSBOW]
    assert _moves_of(engine, CROSSBOW) == [
        ("hand", "processing", "USE"),
        ("processing", "equip", "PUT"),
    ], "装备牌结算完留装备区，不进弃牌堆"
    assert CROSSBOW not in engine.state.discard
    assert any(
        record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and [card.card_id for card in record.cards] == [CROSSBOW]
        for record in engine.records
    ), '置入装备区发生在"使用结算结束后"这一步'


def test_equipment_is_offered_as_a_play_phase_candidate_targeting_itself():
    state = build_state(
        pack=PACK, seats=[SeatSpec(hand=(CROSSBOW,)), SeatSpec()], turn_seat=0
    )

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    uses = [action for action in actions if isinstance(action, UseCard)]

    assert [(action.card.card_id, action.targets) for action in uses] == [(CROSSBOW, (0,))]


def test_replacing_an_equipment_moves_the_old_card_to_the_discard_pile_as_a_loss():
    engine = _run_equipment_use(
        card_ids=(CROSSBOW, QINGGANG),
        hand=(CROSSBOW, QINGGANG),
        stop=lambda engine: QINGGANG in engine.state.player(0).equip,
    )

    assert engine.state.player(0).equip == [QINGGANG], "同一槽位任一时刻至多一张"
    assert _moves_of(engine, CROSSBOW) == [
        ("hand", "processing", "USE"),
        ("processing", "equip", "PUT"),
        ("equip", "discard", "LOSE"),
    ], "被顶替的旧装备强制进弃牌堆，且**不算弃置**（reason = LOSE）"
    assert CROSSBOW in engine.state.discard
    assert _moves_of(engine, QINGGANG)[-1] == ("processing", "equip", "PUT")


def test_the_equipment_zone_still_takes_part_in_card_conservation():
    engine = _run_equipment_use(
        card_ids=(CROSSBOW,),
        hand=(CROSSBOW,),
        stop=lambda engine: bool(engine.state.player(0).equip),
    )

    everywhere = [
        *engine.state.deck,
        *engine.state.discard,
        *engine.state.processing,
        *(card_id for player in engine.state.players for card_id in player.hand),
        *(card_id for player in engine.state.players for card_id in player.equip),
        *(card_id for player in engine.state.players for card_id in player.judge),
    ]
    assert sorted(everywhere) == list(range(len(PACK.deck.cards))), (
        "装备区的牌仍恰在一处：区域互斥与牌守恒对六区同一条"
    )


# ---------------------------------------------------------------------------
# 诸葛连弩：次数上限修饰器（COUNT_LIMIT）
# ---------------------------------------------------------------------------


def test_crossbow_lifts_the_slash_per_turn_limit_for_its_holder():
    slash = SLASH[0]
    armed = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CROSSBOW,), hand=(slash,)), SeatSpec()],
        turn_seat=0,
    )
    unarmed = build_state(pack=PACK, seats=[SeatSpec(hand=(slash,)), SeatSpec()], turn_seat=0)

    armed.player(0).card_use_counts["slash"] = 3
    unarmed.player(0).card_use_counts["slash"] = 1

    assert check_count(armed, 0, armed.card(slash)) is False, "连弩在场，【杀】不再受额定上限约束"
    assert check_count(unarmed, 0, unarmed.card(slash)) is True


def test_crossbow_only_lifts_the_limit_of_slash():
    peach = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CROSSBOW,), hand=(peach,)), SeatSpec()],
        turn_seat=0,
    )

    assert check_count(state, 0, state.card(peach)) is False, "【桃】本就无次数上限"


def test_a_second_slash_is_still_offered_with_the_crossbow():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(equip=(CROSSBOW,), hand=(SLASH[0], SLASH[1])), SeatSpec()],
        turn_seat=0,
    )
    state.player(0).card_use_counts["slash"] = 1

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)

    assert [
        action.card.card_id for action in actions if isinstance(action, UseCard)
    ] == [SLASH[0], SLASH[1]]


def test_a_second_slash_is_not_offered_without_the_crossbow():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(SLASH[0], SLASH[1])), SeatSpec()],
        turn_seat=0,
    )
    state.player(0).card_use_counts["slash"] = 1

    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)

    assert [action.kind for action in actions] == ["EndPhase"]


# ---------------------------------------------------------------------------
# 仁王盾：有效性修饰器（EFFECTIVENESS）
# ---------------------------------------------------------------------------


def test_renwang_shield_makes_a_black_slash_ineffective_against_its_holder():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec(equip=(RENWANG,))], turn_seat=0)

    assert target_is_effective(state, state.card(BLACK_SLASH), 0, 1) is False


def test_renwang_shield_does_not_stop_a_red_slash():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec(equip=(RENWANG,))], turn_seat=0)

    assert target_is_effective(state, state.card(RED_SLASH), 0, 1) is True


def test_renwang_shield_only_protects_its_own_holder():
    state = build_state(pack=PACK, seats=[SeatSpec(equip=(RENWANG,)), SeatSpec()], turn_seat=0)

    assert target_is_effective(state, state.card(BLACK_SLASH), 1, 0) is False
    assert target_is_effective(state, state.card(BLACK_SLASH), 0, 1) is True, (
        "盾在座位 0：座位 1 被黑色【杀】指定时照常有效"
    )


def test_a_black_slash_against_renwang_never_opens_the_dodge_window_or_takes_hp():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(BLACK_SLASH,)), SeatSpec(equip=(RENWANG,))],
        deck=tuple(
            card_id for card_id in range(len(PACK.deck.cards)) if card_id != BLACK_SLASH
        ),
        turn_seat=0,
    )
    engine = Engine.from_state(state, seed=1)
    target = 1
    run_until(
        engine,
        [_EquipUser(0, (BLACK_SLASH,)), _EquipUser(1, ())],
        lambda engine: any(
            record.kind == "frame"
            and record.op == "pop"
            and record.frame_kind is FrameKind.USE
            for record in engine.records
        ),
    )

    assert engine.state.player(target).hp == 4
    assert not any(
        record.kind == "delta" and record.delta_type == "hp_change" for record in engine.records
    )
    assert not any(
        record.kind == "query" and record.query.kind == "DODGE" for record in engine.records
    ), '第 2 层"无效"到 C1 即终止，故不开 C2 的响应窗口'

    slash_resolve = next(
        record.frame_id
        for record in engine.records
        if record.kind == "frame"
        and record.op == "push"
        and record.frame_kind is FrameKind.RESOLVE
    )
    timings = [
        record.timing
        for record in engine.records
        if record.kind == "timing" and record.frame_id == slash_resolve
    ]
    assert timings == [Timing.RESOLVE_START], (
        "有效性检测点就是 RESOLVE_START；无效 ⇒ 直接推进游标，不记生效前 / 生效后"
    )


# ---------------------------------------------------------------------------
# 方天画戟：目标池与候选池（TARGET_POOL + CANDIDATES）
# ---------------------------------------------------------------------------


def _halberd_state(*, hand, seats, equip=(HALBERD,)):
    specs = [SeatSpec(hand=tuple(hand), equip=tuple(equip))]
    specs.extend(seats)
    return build_state(
        pack=PACK,
        seats=specs,
        turn_seat=0,
        rule_config=RuleConfig(player_count=len(specs)),
    )


def _extra_combos(state, card):
    actions = legal_actions_for(state, 0, QueryKind.PLAY_PHASE, answers_seq=1)
    return sorted(
        action.targets
        for action in actions
        if isinstance(action, UseCard) and action.card.card_id == card
    )


def test_halberd_target_pool_is_unchanged_by_the_modifier():
    """TARGET_POOL 挂点是"合法性同标准"的明写落点，M2 本身不改池（票 12 §4）。"""
    slash = SLASH[0]
    with_halberd = _halberd_state(hand=(slash,), seats=[SeatSpec() for _ in range(3)])
    without = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hand=(slash,), equip=(QINGGANG,)),
            SeatSpec(),
            SeatSpec(),
            SeatSpec(),
        ],
        turn_seat=0,
    )

    assert set(check_targets(with_halberd, 0, with_halberd.card(slash))) == set(
        check_targets(without, 0, without.card(slash))
    ), "目标的第 0 层合法性检测与是否装备方天画戟无关"


def test_halberd_adds_extra_target_combinations_when_the_slash_is_the_last_hand_card():
    slash = SLASH[0]
    state = _halberd_state(hand=(slash,), seats=[SeatSpec() for _ in range(3)])

    assert _extra_combos(state, slash) == [
        (1,),
        (1, 2),
        (1, 2, 3),
        (1, 3),
        (2,),
        (2, 3),
        (3,),
    ], "额定 1 + 额外至多 2：组合大小为 1 / 2 / 3"


def test_halberd_does_not_trigger_when_another_hand_card_remains():
    slash = SLASH[0]
    other = SLASH[1]
    state = _halberd_state(hand=(slash, other), seats=[SeatSpec() for _ in range(3)])

    assert _extra_combos(state, slash) == [(1,), (2,), (3,)], (
        '"最后两张手牌"不满足（`A/glossary_gamecard.txt:103-105`）'
    )


def test_halberd_triggers_even_with_cards_in_the_equipment_zone():
    """条件是"此【杀】是**最后的手牌**"，与装备区里有几张牌无关。

    官方反例（`A/glossary_gamecard.txt:105`「最后一张方块手牌和装备区里的一张方块牌当
    火【杀】使用」）说的是**转化牌**消耗了装备区里的牌时不满足——那条路径由票 30 的
    转化 / 虚拟牌机制承载；本票的实体【杀】只看手牌。
    """
    slash = SLASH[0]
    state = _halberd_state(
        hand=(slash,),
        equip=(HALBERD, EIGHT_DIAGRAMS),
        seats=[SeatSpec() for _ in range(3)],
    )

    assert (1, 2, 3) in _extra_combos(state, slash)


def test_halberd_does_not_add_targets_for_other_cards():
    peach = _cards("peach")[0]
    state = _halberd_state(hand=(peach,), seats=[SeatSpec() for _ in range(3)])
    state.player(0).hp = 3

    assert _extra_combos(state, peach) == [(0,)], "【桃】仍是单目标"


def test_halberd_extra_targets_must_pass_the_same_attack_range_check():
    """额外目标与额定目标共用同一个池，故攻击范围（含坐骑）同样约束额外目标。"""
    slash = SLASH[0]
    seats = [SeatSpec() for _ in range(7)]
    seats[3] = SeatSpec(equip=(JUYING,))  # 座位 0→4 距离 4 + 1 = 5 > 方天画戟范围 4
    seats[4] = SeatSpec()
    state = _halberd_state(hand=(slash,), seats=seats)

    combos = _extra_combos(state, slash)

    assert (4,) not in combos and all(4 not in combo for combo in combos), (
        "距离 5 > 范围 4 的座位 4 连额定目标都不是，更不会出现在额外目标里"
    )
    assert (3,) in combos, "距离 3（+0）仍在范围 4 内"


# ---------------------------------------------------------------------------
# W7-01：目标数修饰点（TARGET_COUNT）与"可叠加"
# ---------------------------------------------------------------------------


def _fake_target_count(monkeypatch, step: int) -> str:
    """装一条**非武器槽**的虚拟装备，它的 ``TARGET_COUNT`` 回调把最大目标数 +``step``。

    W7-01 用它证两件事：``TARGET_COUNT`` 只对 ``TargetCount.RATED`` 的牌查链、且修饰器
    **可叠加**（1 + 1 + 2 = 4 的那个"1"）。虚拟装备不占武器槽 ⇒ 不与【方天画戟】抢槽
    （票 07 §6 / 票 30 的既有姿态）；未来【天义】落地时换掉这个合成回调即可。
    """
    from types import MappingProxyType

    from sgs.effects import CarrierKind, Modifier, ModifierPoint
    from sgs.skills import catalog as catalog_module
    from sgs.skills.catalog import SkillDef
    from sgs.state import SlotKind

    def handler(state, ctx, current):
        return current + step

    fake = SkillDef(
        skill_id="test.target_count",
        locked=True,
        carrier=CarrierKind.EQUIPMENT,
        virtual_slot=SlotKind.ARMOR,
        modifiers=(Modifier(ModifierPoint.TARGET_COUNT, handler),),
    )
    monkeypatch.setattr(
        catalog_module,
        "CATALOG",
        MappingProxyType({**catalog_module.CATALOG, fake.skill_id: fake}),
    )
    return fake.skill_id


def _combos_of(state, card_id: int, seat: int = 0) -> list[tuple[int, ...]]:
    """``seat`` 出牌阶段里该牌的全部目标组合（排序后）。"""
    actions = legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)
    return sorted(
        action.targets
        for action in actions
        if isinstance(action, UseCard) and action.card.card_id == card_id
    )


def test_target_count_is_only_read_for_rated_cards(monkeypatch):
    """``TARGET_COUNT`` 只对 ``TargetCount.RATED`` 的牌查链（W7-01 / 评审 S9）。

    反例是"全体存活"档：【南蛮入侵】【万箭齐发】的目标数由池决定，若也读链，就会切出
    "只打一半人"的**非法子集**。正例是 RATED 的【顺手牵羊】：同一个合成 +2 修饰器确实
    把它的候选从 1 目标放宽到 1..2（池只有两名相邻角色）。
    """
    fake = _fake_target_count(monkeypatch, step=2)
    barbarian = _cards("barbarian_invasion")[0]
    barrage = _cards("arrow_barrage")[0]
    snatch = _cards("snatch")[0]
    filler = _cards("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(
                hand=(barbarian, barrage, snatch),
                virtual_equipment=(fake,),
            ),
            SeatSpec(hand=(filler,)),
            SeatSpec(hand=(filler,)),
            SeatSpec(hand=(filler,)),
        ],
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )

    assert _combos_of(state, barbarian) == [(1, 2, 3)], "南蛮：目标数 = 池，不读 TARGET_COUNT"
    assert _combos_of(state, barrage) == [(1, 2, 3)], "万箭：同上"
    assert _combos_of(state, snatch) == [(1,), (1, 3), (3,)], (
        "RATED 的【顺手牵羊】读链：额定 1 → 最大 3，池只有相邻的 1 / 3 ⇒ 1..2 目标"
    )


def test_target_count_modifiers_stack_with_the_halberd(monkeypatch):
    """``TARGET_COUNT`` 可叠加：合成 +1 与方天 +2 一起 ⇒ 1 + 1 + 2 = 4（W7-01 / 评审 S8）。

    只证明"可叠加"这一件事，不实现【天义】（它还差"无距离限制"，见 W7 spec §7）。
    """
    from sgs.effects import ModifierContext, ModifierPoint, apply_modifiers

    fake = _fake_target_count(monkeypatch, step=1)
    slash = SLASH[0]
    seats = [SeatSpec(hand=(slash,), equip=(HALBERD,), virtual_equipment=(fake,))]
    seats.extend(SeatSpec() for _ in range(6))  # 7 人：池 6 人，距离都 ≤ 方天范围 4
    state = build_state(
        pack=PACK,
        seats=seats,
        turn_seat=0,
        rule_config=RuleConfig(player_count=7),
    )

    high = apply_modifiers(
        ModifierPoint.TARGET_COUNT,
        state,
        ModifierContext(subject=0, card=state.card(slash)),
        1,
    )

    assert high == 4, "方天 +2 与合成 +1 在链上相加"
    assert (1, 2, 3, 4) in _combos_of(state, slash), "4 目标组合真的进了候选"
    assert all(len(combo) <= 4 for combo in _combos_of(state, slash))
