"""群英包：「锋势」陆逊【谦逊】【连营】（qunying 票 01；用户 2026-11-21 签 Q1–Q16）。

钉住的是用户在这一轮里逐条给出的口径，能被机械钉住的那几条：

- **Q4（本轮这条是全仓口径）**：「无次数限制」= 规则 §17.2 的 **② 档**——可以越过上限
  使用、但**照常计入次数**（连营① 与谋刘赪【莺舞】① 同一条）；
- **Q5 / 账本**：连营① 的「无距离限制」= 距离与攻击范围都豁免（规则 §17.3 L1706–L1707）；
- **Q6 / Q7**：「下一张牌」= 本回合内**下一次使用**（触发它的那张牌自己不算）、**逐事件**触发；
- **Q10**：代价每出牌阶段各一次、效果每游戏回合各一次；
- **Q12**：效果① 的牌名池含**延时类**锦囊；**Q13**：代价③ 的产物可以是乐 / 兵粮 / 闪电；
- **Q14**：效果② 的「移动」= 另一名角色的对应区域（同槽空 / 判定区不同名牌 / 不能同座）；
- **Q15**：效果③ 牌堆优先、没有才从弃牌堆，两边都随机。
"""

from __future__ import annotations

from typing import Any

import pytest

from sgs.data import Category, load_content_pack
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.skills.hero_qunying import LIANYING, QIANXUN
from sgs.skills.catalog import MATCH_OWNER_KEY
from sgs.skills.move_after import MOVE_EVENT_KEY, MoveEvent
from sgs.skills.catalog import reset_phase_skill_state, reset_turn_skill_state
from sgs.skills import active as active_module
from sgs.state import (
    ActivateSkill,
    GameState,
    MoveReason,
    Phase,
    SelectOption,
    SlotKind,
    Zone,
    ZoneRef,
    EndPhase,
)
from sgs.timing import Timing

from support import SeatSpec, build_state, cards_of_type, deck_excluding, run_queries
from sgs.data import hero_by_id, hero_skill_ids

PACK = load_content_pack(deck_profile="standard_108")
PACK_160 = load_content_pack(deck_profile="standard_ex_160")
HERO = "qunying_luxun"


class _Host:
    def __init__(self, state: GameState) -> None:
        self.state = state


class _Frame:
    def __init__(self, **data: Any) -> None:
        self.data = dict(data)
        self.frame_id = 1


def _state(
    *, seats: int = 4, turn_seat: int = 0, hand_types: tuple[str, ...] = ("slash", "dodge")
) -> GameState:
    hand = tuple(cards_of_type(PACK_160, type_id)[0] for type_id in hand_types)
    specs = [SeatSpec(hero=HERO, hand=hand)]
    specs.extend(SeatSpec() for _ in range(seats - 1))
    return build_state(
        pack=PACK_160,
        seats=specs,
        deck=deck_excluding(PACK_160, *hand),
        turn_seat=turn_seat,
        phase=Phase.PLAY,
    )


def _card(state: GameState, type_id: str) -> Any:
    for card in state.cards:
        if card.type == type_id:
            return card
    raise AssertionError(f"{type_id} 不在牌堆里")


def _draw_event(state: GameState, *, seat: int = 0, card: Any) -> MoveEvent:
    """一次"牌堆 → ``seat`` 手牌"的移动（摸牌；改变手牌张数）。"""
    return MoveEvent(
        cards=(card,),
        from_zone=ZoneRef(Zone.DECK),
        to_zone=ZoneRef(Zone.HAND, seat),
        reason=MoveReason.DRAW,
        owner=seat,
        after_holder=seat,
        actor=seat,
    )


def _frame_for(state: GameState, event: MoveEvent, *, owner: int = 0) -> _Frame:
    return _Frame(**{MATCH_OWNER_KEY: owner, MOVE_EVENT_KEY: event})


# ---------------------------------------------------------------------------
# 数据面 / 声明面
# ---------------------------------------------------------------------------


def test_the_pack_ships_the_hero_with_both_skills() -> None:
    """数据面：群英包装载得出「锋势」陆逊（真名陆逊、吴 / 男 / 4 体力），两条技能都在 CATALOG。"""
    hero = hero_by_id(HERO)
    assert hero is not None
    assert hero.name == "「锋势」陆逊"
    assert hero.true_name == "陆逊"
    assert hero.kingdom.value == "wu"
    assert hero.gender.value == "male"
    assert hero.hp_max == 4
    assert hero_skill_ids(HERO) == (QIANXUN, LIANYING)
    assert QIANXUN in CATALOG and LIANYING in CATALOG


def test_lianying_is_one_locked_declaration_with_four_forced_hooks() -> None:
    """声明面：【连营】= 锁定技 + 三条 ``MOVE_AFTER`` 强制钩子 + 一条兑现钩子 + 两个修饰器。"""
    definition = CATALOG[LIANYING]
    assert definition.locked is True, "牌面写「锁定技」⇒ 一个「是否发动」的询问都不发"
    assert [hook.timing for hook in definition.hooks] == [
        Timing.MOVE_AFTER,
        Timing.MOVE_AFTER,
        Timing.MOVE_AFTER,
        Timing.USE_DESIGNATE_TARGET,
    ]
    assert all(hook.forced for hook in definition.hooks)
    assert [hook.bookkeeping for hook in definition.hooks] == [True, True, False, False], (
        "①② 只写座位态（簿记），③ 摸牌是真效果、兑现钩子要连环"
    )
    assert definition.badge is not None, "Q16：座位常驻标签（连营下一张牌：…）"
    assert definition.requirement.startswith("锁定技，你的手牌数变化后若与你")


def test_qianxun_declares_two_event_flows_with_their_own_steps() -> None:
    """声明面（票 10）：**代价事件**与**效果事件**各一条流水线，由 ``flow_resolver`` 按状态选。

    用户 2026-11-22 裁定第 4 条：一个预发动只含**一个**事件；多步只是这个事件要多次选择。
    ⇒ 代价（+ 代价③ 的选装备牌 / 选延时锦囊）一条，效果（+ 该效果自己的步骤）另一条；
    "此刻是哪一份"不是按技能名特判，而是声明上的解析器读持有者状态（下一用例）。
    """
    from sgs.skills import active as module

    definition = CATALOG[QIANXUN]
    assert definition.locked is False
    assert definition.hooks == ()
    assert definition.requirement.startswith("出牌阶段各限一次")
    assert set(definition.step_requirements) == {
        "cost",
        "equip",
        "product",
        "effect",
        "card",
        "to",
        "type",
    }, f"每一步的文案都显式声明（A4 守卫）：{set(definition.step_requirements)}"

    cost_flow = module.QIANXUN_COST_FLOW
    effect_flow = module.QIANXUN_EFFECT_FLOW
    assert cost_flow.name != effect_flow.name, "两条流水线的询问位置必须分得开"
    assert cost_flow.once_per_phase is False and effect_flow.once_per_phase is False, (
        "次数由三个代价位 / 三个效果位自己数"
    )

    assert [type(step).__name__ for step in cost_flow.steps] == ["PickOption", "FlowBranch"]
    cost_step, cost_branch = cost_flow.steps
    assert (cost_step.key, cost_branch.key) == ("cost", "cost")
    assert set(cost_branch.branches) == {module.QIANXUN_COST_EQUIP}, (
        f"只有代价③ 有子步骤（选装备牌 / 选产物）：{set(cost_branch.branches)}"
    )
    equip_step, product_step = cost_branch.branches[module.QIANXUN_COST_EQUIP]
    assert isinstance(equip_step, module.PickCards)
    assert equip_step.zones == (Zone.HAND, Zone.EQUIP)
    assert tuple(equip_step.slot_kinds) == tuple(SlotKind), "「一张装备牌」= 六个装备槽全收"
    assert (equip_step.lo, equip_step.hi) == (1, 1)
    assert isinstance(product_step, module.PickOption)
    assert product_step.key == "product" and callable(product_step.options)

    assert [type(step).__name__ for step in effect_flow.steps] == ["PickOption", "FlowBranch"]
    effect_step, effect_branch = effect_flow.steps
    assert (effect_step.key, effect_branch.key) == ("effect", "effect")
    assert set(effect_branch.branches) == {
        module.QIANXUN_EFFECT_MOVE,
        module.QIANXUN_EFFECT_GAIN,
    }, f"② / ③ 有子步骤；① 的牌名 / 目标仍是手写询问：{set(effect_branch.branches)}"
    card_step, to_step = effect_branch.branches[module.QIANXUN_EFFECT_MOVE]
    assert isinstance(card_step, module.PickCards)
    assert card_step.zones == (Zone.EQUIP, Zone.JUDGE)
    assert (card_step.lo, card_step.hi) == (1, 1)
    assert callable(card_step.seats), "整体座位（含自己）的面：`PickCards.seats` 可调用档"
    assert isinstance(to_step, module.PickSeats), (
        "目的地 = 座位步（qunying 票 09：牌桌点将；旧的 PickOption + 置灰档退场）"
    )
    assert to_step.key == "to" and (to_step.lo, to_step.hi) == (1, 1)
    assert to_step.optional is False, "目的地必须选（与旧选项步的 optional=False 同一条）"
    assert callable(to_step.pool), "池 = 合法目的地（`_qianxun_move_destinations` 唯一一份判据）"
    (type_step,) = effect_branch.branches[module.QIANXUN_EFFECT_GAIN]
    assert isinstance(type_step, module.PickOption)
    assert type_step.key == "type" and callable(type_step.options), (
        "效果③ 的「选类型」也属于**效果事件**自己的步骤（票 10）"
    )
    for table in (active_module._REQUIREMENTS, active_module._STEP_REQUIREMENTS):
        assert "qianxun" not in table, "不往短 id 表加行（标 / 界陆逊各占一行）"


def test_qianxun_resolves_the_cost_event_then_the_effect_event() -> None:
    """``flow_resolver``：没有窗口 = 代价事件；窗口开着 = 效果事件（同一条技能两套清单）。"""
    from sgs.skills import active as module

    state = _play_state()
    assert module._qianxun_flow(state, 0) is module.QIANXUN_COST_FLOW
    module._qianxun_open_window(state, 0)
    assert module._qianxun_flow(state, 0) is module.QIANXUN_EFFECT_FLOW
    module._qianxun_close_window(state, 0)
    assert module._qianxun_flow(state, 0) is module.QIANXUN_COST_FLOW


def test_qianxun_effect_window_lasts_the_turn_but_not_the_play_phase() -> None:
    """窗口位与效果位同作用域：出牌阶段起点**不**清（额外出牌阶段照样能选效果），回合末清。"""
    from sgs.skills import active as module

    state = _play_state()
    module._qianxun_open_window(state, 0)
    reset_phase_skill_state(state)
    assert module._qianxun_window_open(state, 0), "复位代价位不清效果窗口"
    reset_turn_skill_state(state)
    assert not module._qianxun_window_open(state, 0), "回合末清（效果每回合各一次）"


# ---------------------------------------------------------------------------
# 【连营】三段
# ---------------------------------------------------------------------------


def test_lianying_sets_the_unlimited_flag_when_hand_equals_hp() -> None:
    """①：手牌数变化后手牌数 == 体力值 ⇒ 置「下一张牌无距离与次数限制」位。"""
    from sgs.skills.hero_qunying import _unlimited, _unlimited_condition

    state = _state()
    player = state.player(0)
    event = _draw_event(state, card=_card(state, "slash"))
    assert _unlimited_condition(_Host(state), _frame_for(state, event)) is False, (
        "手牌数还没摆成与体力值相等"
    )
    player.hp = len(player.hand)
    assert _unlimited_condition(_Host(state), _frame_for(state, event)) is True
    _unlimited(_Host(state), _frame_for(state, event))
    assert player.lianying_unlimited_after == 0, "记下触发那一刻的使用序号"


def test_lianying_flags_only_on_a_real_hand_count_change() -> None:
    """「手牌数变化」= 张数真的变了：**手牌重排**不算，装备区搬牌也不算。"""
    from sgs.skills.hero_qunying import _chain_condition, _hand_count_changed

    state = _state()
    player = state.player(0)
    player.hp = len(player.hand)
    hand_card = state.card(player.hand[0])
    rearrange = MoveEvent(
        cards=(hand_card,),
        from_zone=ZoneRef(Zone.HAND, 0),
        to_zone=ZoneRef(Zone.HAND, 0),
        reason=MoveReason.PUT,
        owner=0,
        after_holder=0,
        actor=0,
    )
    assert _hand_count_changed(rearrange, 0) is False
    equip_move = MoveEvent(
        cards=(hand_card,),
        from_zone=ZoneRef(Zone.EQUIP, 0),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.LOSE,
        owner=0,
        after_holder=None,
        actor=0,
    )
    assert _hand_count_changed(equip_move, 0) is False
    assert _hand_count_changed(rearrange, 1) is False, "别人的手牌变化与我无关"
    assert _chain_condition(_Host(state), _frame_for(state, equip_move)) is False


def test_lianying_chains_every_target_of_the_next_use() -> None:
    """②：兑现点 = 下一次使用的「指定目标时」，逐目标 ``set_chained``，且只兑现这一次。"""
    from sgs.skills.hero_qunying import _chain_flag, _settle, _settle_condition

    state = _state()
    engine = Engine.from_state(state, seed=11)
    player = state.player(0)
    player.lianying_chain_after = 0
    player.used_or_played_ever = 1  # 这一次使用已经 +1 ⇒ 序号越过 0
    frame = _Frame(**{MATCH_OWNER_KEY: 0, "source": 0, "targets": (1, 2)})
    assert _settle_condition(engine, frame) is True
    _settle(engine, frame)
    assert state.player(1).chained is True
    assert state.player(2).chained is True
    assert player.lianying_chain_after == -1, "兑现一次就清"


def test_lianying_flags_do_not_apply_to_the_card_whose_use_triggered_them() -> None:
    """G9：触发它的那张牌自己不是「下一张牌」——序号相同 ⇒ 不兑现、位保留。"""
    from sgs.skills.hero_qunying import _settle, _settle_condition

    state = _state()
    engine = Engine.from_state(state, seed=12)
    player = state.player(0)
    player.lianying_chain_after = 3
    player.used_or_played_ever = 3
    frame = _Frame(**{MATCH_OWNER_KEY: 0, "source": 0, "targets": (1,)})
    assert _settle_condition(engine, frame) is False
    _settle(engine, frame) if _settle_condition(engine, frame) else None
    assert player.lianying_chain_after == 3, "自己那一次不消费"
    assert state.player(1).chained is False


def test_lianying_settle_tolerates_an_empty_target_table() -> None:
    """[G4] 空目标表不让兑现抛错，也**不**在钩子里补"自己"（补自己那条口径不在这里）。

    自用牌（【酒】/【桃】/装备）的目标表在**指定目标路径**上就已经是 ``(自己,)``（见下面的
    端到端用例），故 ``_settle`` 不需要一条"空 ⇒ 全体"的兜底；这里只钉"空表不抛、只消费待
    生效位、不横置任何人"。
    """
    from sgs.skills.hero_qunying import _settle

    state = _state()
    engine = Engine.from_state(state, seed=53)
    player = state.player(0)
    player.lianying_chain_after = 0
    player.lianying_unlimited_after = 0
    player.used_or_played_ever = 1
    frame = _Frame(**{MATCH_OWNER_KEY: 0, "source": 0, "targets": ()})
    _settle(engine, frame)
    assert player.lianying_chain_after == -1, "空目标表照样消费 ② 的位"
    assert player.lianying_unlimited_after == -1, "空目标表照样消费 ① 的位"
    assert not any(state.player(seat).chained for seat in (0, 1, 2, 3)), (
        "空目标表 = 没有目标可横置；钩子**不**替牌面补自己"
    )


def test_lianying_unlimited_lets_a_slash_exceed_the_limit_but_still_counts() -> None:
    """**本轮口径（Q4）**：越过上限可用、但照常计入次数；用完那张之后实体【杀】就出不了了。

    局面：4 人局（座 2 与座 0 距离 2，普通【杀】够不着），座 0 手牌 2、体力 3 ⇒ 摸一张后
    手牌 3 == 体力 3 ⇒ ① 置位。此时 ``check_count`` 对已用完额度的【杀】不再报超额、
    ``check_targets`` 也够得着座 2；真的用掉之后次数 +1、位被清、实体【杀】重新被额度挡住。
    """
    from sgs.effects import behavior_for, check_count, check_targets
    from sgs.skills.hero_qunying import _unlimited, _unlimited_condition

    state = _state(seats=4)
    engine = Engine.from_state(state, seed=13)
    player = state.player(0)
    slash = state.card(player.hand[0])
    player.hp = len(player.hand) + 1
    assert 2 not in check_targets(state, 0, slash), "基准：普通【杀】够不着距离 2 的座位"
    drawn = state.card(state.deck[0])
    engine.move_in_frame(
        None,
        (drawn,),
        to_zone=ZoneRef(Zone.HAND, 0),
        reason=MoveReason.DRAW,
        owner=0,
    )
    assert player.lianying_unlimited_after >= 0, "手牌数 == 体力值 ⇒ ① 置位"
    state.player(0).card_use_counts["slash"] = 1
    assert check_count(state, 0, slash) is False, "① 生效：这一次使用没有次数上限"
    assert 2 in check_targets(state, 0, slash), "① 生效：距离 / 攻击范围豁免"
    del _unlimited, _unlimited_condition
    engine.start_use(slash, source=0, targets=(2,), use_timing=Timing.PHASE_PLAY_IDLE)

    class _End:
        def respond(self, query: Any) -> Any:
            if query.kind == "PLAY_PHASE":
                return next(a for a in query.legal_actions if isinstance(a, EndPhase))
            for action in query.legal_actions:
                return action
            raise AssertionError("没有可应答的动作")

    run_queries(
        engine,
        [_End() for _ in range(4)],
        lambda e: e.state.player(0).card_use_counts.get("slash") == 2
        and e.state.player(0).lianying_unlimited_after == -1,
    )
    assert state.player(0).card_use_counts["slash"] == 2, "照常计入次数（② 档）"
    assert behavior_for(slash).count_key == "slash"
    assert check_count(state, 0, slash) is True, "额度真的用掉了 ⇒ 实体【杀】出不了"


def test_lianying_draws_when_hand_count_matches_the_judge_zone() -> None:
    """③：手牌数 == 判定区牌数 ⇒ 摸一张（引擎层跑真钩子）。"""
    judge_card = cards_of_type(PACK_160, "indulgence")[0]
    state = build_state(
        pack=PACK_160,
        seats=[SeatSpec(hero=HERO), SeatSpec(), SeatSpec(), SeatSpec()],
        deck=deck_excluding(PACK_160, judge_card),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=14)
    player = state.player(0)
    player.judge = [judge_card]
    player.hand = []  # 手牌清空
    assert len(player.hand) == 0 and len(player.judge) == 1
    before = len(player.hand)
    # 手牌 0 → 摸 1 ⇒ 1 == 判定区 1 ⇒ ③ 再摸一张 ⇒ 2 ≠ 1 ⇒ 收
    engine.move_in_frame(
        None,
        (state.card(state.deck[0]),),
        to_zone=ZoneRef(Zone.HAND, 0),
        reason=MoveReason.DRAW,
        owner=0,
    )
    assert len(player.hand) == before + 2, "触发那一次 + ③ 的那一张"


def test_lianying_flags_expire_at_turn_end() -> None:
    """两个位的作用域 = 一个游戏回合：回合末兜底复位（`_clear_turn_effects`）。"""
    state = _state(seats=4)
    engine = Engine.from_state(state, seed=15)
    player = state.player(0)
    player.lianying_unlimited_after = 0
    player.lianying_chain_after = 0
    engine._clear_turn_effects()
    assert player.lianying_unlimited_after == -1
    assert player.lianying_chain_after == -1


# ---------------------------------------------------------------------------
# 【谦逊】代价 / 效果的口径
# ---------------------------------------------------------------------------


def test_qianxun_cost_bits_are_per_play_phase_and_effect_bits_per_turn() -> None:
    """Q10：代价位随**出牌阶段**复位、效果位随**回合**复位（用户：额外阶段刷新代价、不刷效果）。"""
    from sgs.skills import active as module

    state = _state()
    module._qianxun_write(state, 0, 1 << (module._QIANXUN_COST_SHIFT + 0))
    module._qianxun_write(state, 0, 1 << (module._QIANXUN_EFFECT_SHIFT + 1))
    assert module._qianxun_used_costs(state, 0) == {module.QIANXUN_COST_DISCARD}
    assert module._qianxun_used_effects(state, 0) == {module.QIANXUN_EFFECT_MOVE}
    reset_phase_skill_state(state)
    assert module._qianxun_used_costs(state, 0) == frozenset(), "出牌阶段起点清代价位"
    assert module._qianxun_used_effects(state, 0) == {module.QIANXUN_EFFECT_MOVE}, (
        "阶段复位**不动**效果位（它的作用域是本回合）"
    )
    reset_turn_skill_state(state)
    assert module._qianxun_used_effects(state, 0) == frozenset(), "回合末清效果位"


def test_qianxun_cost_three_needs_an_equipment_card_and_a_free_judge_zone() -> None:
    """代价③：有装备牌 ∧ 判定区还能收一张延时锦囊；判定区已有同名 ⇒ 那一样不可选。"""
    from sgs.skills import active as module

    state = _state()
    player = state.player(0)
    player.hand = []
    player.equip = []
    assert module.QIANXUN_COST_EQUIP not in module._qianxun_available_costs(state, 0)
    weapon = cards_of_type(PACK_160, "qinggang_sword")[0]
    player.hand = [weapon]
    assert module.QIANXUN_COST_EQUIP in module._qianxun_available_costs(state, 0)
    assert set(module._qianxun_products(state, 0)) == {
        "indulgence",
        "supply_shortage",
        "lightning",
    }, "Q13：乐 / 兵粮 / 闪电都可选"
    indulgence = cards_of_type(PACK_160, "indulgence")[0]
    player.judge = [indulgence]
    assert "indulgence" not in module._qianxun_products(state, 0), "判定区已有同名"


def test_qianxun_effect_one_name_table_excludes_delayed_tricks() -> None:
    """Q12′（用户 2026-11-21 实机报障后改口）：池 = 本牌堆真有的**基本牌 + 普通锦囊**。

    **不产生虚拟的延时类锦囊**（虚拟延时锦囊在判定 / 移动 / 展示三处都会出 bug）；也不含
    装备牌（牌面写"一张基本牌或普通锦囊牌"）。
    """
    from sgs.skills import active as module

    state = _state()
    table = module._qianxun_declaration_table(state)
    assert "乐不思蜀" not in table and "闪电" not in table and "兵粮寸断" not in table
    assert table["桃"] == "peach" and table["杀"] == "slash"
    assert "无中生有" in table and table["无中生有"] == "ex_nihilo"
    assert all(value not in {"qinggang_sword", "eight_diagrams"} for value in table.values())


def test_qianxun_effect_one_greys_names_without_a_legal_target() -> None:
    """效果① 的置灰：出牌阶段的【闪】/【无懈可击】没有目标池 ⇒ 列出来但点不动。"""
    from sgs.skills import active as module

    state = _state()
    player = state.player(0)
    player.hp = player.hp_max  # 满体力 ⇒【桃】也不可用
    disabled = module._qianxun_unusable_names(state, 0)
    names = {name for name, type_id in module._qianxun_declaration_table(state).items()}
    assert {"闪", "无懈可击", "桃"} & disabled, f"置灰集合 = {disabled!r}"
    assert names >= disabled


def test_qianxun_effect_two_move_destinations_follow_the_slot_and_judge_rules() -> None:
    """Q14：装备牌 ⇒ 目标同槽必须为空；判定牌 ⇒ 目标判定区不得已有同名牌；不能同座。"""
    from sgs.skills import active as module

    state = _state(seats=4)
    player = state.player(0)
    sword = cards_of_type(PACK_160, "qinggang_sword")[0]
    player.equip = [sword]
    assert module._qianxun_move_destinations(state, 0, sword) == (1, 2, 3)
    other_sword = cards_of_type(PACK_160, "green_dragon_blade")[0]
    state.player(2).equip = [other_sword]
    assert module._qianxun_move_destinations(state, 0, sword) == (1, 3), (
        "座 2 的同槽已占 ⇒ 不是合法目的地"
    )
    indulgence = cards_of_type(PACK_160, "indulgence")[0]
    player.judge = [indulgence]
    state.player(1).judge = [cards_of_type(PACK_160, "indulgence")[1]]
    assert module._qianxun_move_destinations(state, 0, indulgence) == (2, 3), (
        "座 1 的判定区已有同名牌"
    )


def test_qianxun_offers_only_with_a_cost_and_an_unused_effect() -> None:
    """入口：代价事件（付得起的代价 ∧ 还有没用过的效果）或窗口开着的效果事件。

    用户 2026-11-22 裁定第 4/5 条：效果事件**不再要求还有可付的代价**；反过来，三个效果
    都用过之后两个事件都不给入口。
    """
    from sgs.skills import active as module
    from sgs.effects import CandidateScope, ModifierContext

    state = _state()
    ctx = ModifierContext(subject=0, owner=0, card=None, scope=CandidateScope.PLAY)
    state.turn_seat = 0
    state.phase = Phase.PLAY
    offered = module._qianxun_offers(state, ctx, ())
    assert offered and isinstance(offered[-1], ActivateSkill)

    # 付得起的两项代价都付过、代价③ 没有装备牌 ⇒ 代价事件不给入口……
    for cost in (module.QIANXUN_COST_DISCARD, module.QIANXUN_COST_DAMAGE):
        module._qianxun_write(
            state, 0, 1 << (module._QIANXUN_COST_SHIFT + module._QIANXUN_COST_INDEX[cost])
        )
    assert module._qianxun_offers(state, ctx, ()) == ()
    # ……但窗口开着（代价已结算、效果待选）⇒ 效果事件照旧可发动（不再要求还有代价）。
    module._qianxun_open_window(state, 0)
    offered = module._qianxun_offers(state, ctx, ())
    assert offered and isinstance(offered[-1], ActivateSkill)

    for index in range(len(module.QIANXUN_EFFECTS)):
        module._qianxun_write(state, 0, 1 << (module._QIANXUN_EFFECT_SHIFT + index))
    assert module._qianxun_offers(state, ctx, ()) == (), "三个效果都用过 ⇒ 两个事件都断开"



# ---------------------------------------------------------------------------
# 【谦逊】端到端：真从出牌阶段点下去，走完整条流水线
# ---------------------------------------------------------------------------


class _PlayScript:
    """出牌阶段的脚本参与者：点【谦逊】→ 按 ``options`` 顺序答选项 → 结束阶段。"""

    def __init__(self, options: tuple[str, ...], *, skill_id: str = "", probe: Any = None) -> None:
        self.options = list(options)
        self.skill_id = skill_id or QIANXUN
        self.answered: list[str] = []
        self.probe = probe
        self.hand_before_type: int | None = None

    def respond(self, query: Any) -> Any:
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "PLAY_PHASE":
            for action in query.legal_actions:
                if isinstance(action, ActivateSkill) and action.skill_id == self.skill_id:
                    return action
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        if kind == "SELECT_OPTION":
            if str(getattr(query, "skill_id", "") or "").endswith(":type") and self.probe:
                self.hand_before_type = int(self.probe())
            wanted = self.options.pop(0) if self.options else None
            for action in query.legal_actions:
                if not isinstance(action, SelectOption):
                    continue
                if wanted is None or action.choice == wanted:
                    self.answered.append(action.choice)
                    return action
            raise AssertionError(
                f"期望的选项 {wanted!r} 不在候选里：{query.legal_actions!r}"
            )
        if kind == "SELECT_CARDS":
            for action in query.legal_actions:
                return action
        for action in query.legal_actions:
            return action
        raise AssertionError("没有可应答的动作")


def _play_state(
    *,
    hand_types: tuple[str, ...] = ("slash", "dodge"),
    seats: int = 3,
    judge: bool = True,
) -> GameState:
    """出牌阶段局面。``judge=True`` 给座 0 一张判定牌——**故意**让它 ≠ 弃光后的手牌数（0），
    免得【连营】③ 在"弃置所有手牌"之后顺手再触发一次摸牌，把效果③ 的读数搅乱。"""
    hand = tuple(cards_of_type(PACK_160, type_id)[0] for type_id in hand_types)
    judge_card = cards_of_type(PACK_160, "indulgence")[0] if judge else None
    specs = [
        SeatSpec(
            hero=HERO,
            hand=hand,
            judge=(judge_card,) if judge_card is not None else (),
        )
    ]
    specs.extend(SeatSpec() for _ in range(seats - 1))
    excluded = (*hand, judge_card) if judge_card is not None else hand
    return build_state(
        pack=PACK_160,
        seats=specs,
        deck=deck_excluding(PACK_160, *excluded),
        turn_seat=0,
        phase=Phase.PLAY,
    )


def test_qianxun_end_to_end_discard_everything_then_take_a_basic_card() -> None:
    """端到端：代价①（弃置所有手牌）→ 效果③（取一张基本牌）——付代价与效果各自落位。"""
    from sgs.skills import active as module

    state = _play_state()
    engine = Engine.from_state(state, seed=31)
    script = _PlayScript(
        (module.QIANXUN_COST_DISCARD, module.QIANXUN_EFFECT_GAIN, "basic"),
        probe=lambda: len(state.player(0).hand),
    )
    run_queries(
        engine,
        [script, _PlayScript((), skill_id="none"), _PlayScript((), skill_id="none")],
        lambda e: bool(module._qianxun_used_effects(e.state, 0))
        and e.pending is not None
        and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE",
    )
    player = state.player(0)
    assert script.hand_before_type is not None
    assert len(player.hand) == script.hand_before_type + 1, (
        "效果③ 恰好取一张（弃光手牌后【连营】③ 可能另外摸一张，故按「答类型那一刻」取基准）"
    )
    from sgs.data import card_category

    gained = player.hand[-1]
    assert card_category(state.pack_id, state.card(gained).type) is Category.BASIC, (
        "答的是「基本牌」⇒ 拿到的必须是基本牌"
    )
    assert module._qianxun_used_costs(state, 0) == {module.QIANXUN_COST_DISCARD}
    assert module._qianxun_used_effects(state, 0) == {module.QIANXUN_EFFECT_GAIN}


def test_qianxun_cost_three_puts_a_lightning_in_own_judge_zone() -> None:
    """端到端：代价③（装备牌 → 【闪电】对自己使用）——Q13 的"武将规则大于游戏规则"。"""
    from sgs.skills import active as module

    state = _play_state(hand_types=("qinggang_sword",))
    weapon = state.player(0).hand[0]
    engine = Engine.from_state(state, seed=32)
    # 票 10 的采集顺序：**代价事件**（选代价 → 挑哪张装备牌 → 挑产物）先结算，之后技能再
    # 可发动、进入**效果事件**（选效果 → 选类型）。
    script = _PlayScript(
        (module.QIANXUN_COST_EQUIP, "lightning", module.QIANXUN_EFFECT_GAIN, "equipment")
    )
    run_queries(
        engine,
        [script, _PlayScript((), skill_id="none"), _PlayScript((), skill_id="none")],
        lambda e: bool(module._qianxun_used_effects(e.state, 0))
        and e.pending is not None
        and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE",
    )
    player = state.player(0)
    assert weapon not in player.equip, "那张装备牌已经当【闪电】用掉了"
    assert weapon in player.judge, "那张实体牌进了我的判定区"
    assert state.virtual_judge[weapon].type == "lightning", (
        "判定区里代表的是**虚拟**【闪电】（`virtual_judge` 记着映射）"
    )
    assert module._qianxun_used_costs(state, 0) == {module.QIANXUN_COST_EQUIP}


def test_qianxun_uses_a_pure_virtual_card_of_the_chosen_name() -> None:
    """端到端：效果① 选【无中生有】⇒ 真的摸两张（纯虚拟牌、无来源实体牌）。"""
    from sgs.skills import active as module

    state = _play_state()
    engine = Engine.from_state(state, seed=33)
    held = len(state.player(0).hand)
    script = _PlayScript((module.QIANXUN_COST_DAMAGE, module.QIANXUN_EFFECT_CARD, "无中生有"))
    run_queries(
        engine,
        [script, _PlayScript((), skill_id="none"), _PlayScript((), skill_id="none")],
        lambda e: bool(module._qianxun_used_effects(e.state, 0))
        and e.pending is not None
        and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE",
    )
    player = state.player(0)
    assert player.hp == 3, "代价②：1 点无来源火焰伤害"
    assert len(player.hand) >= held + 2, "虚拟【无中生有】摸两张"
    assert module._qianxun_used_effects(state, 0) == {module.QIANXUN_EFFECT_CARD}


def test_qianxun_moving_a_transformed_delayed_trick_keeps_the_product() -> None:
    """报障第 5 条：场上被转化的延时类锦囊被**移动**时保持其产物（乐还是乐、目标区同名不合法）。

    例：一张装备牌被当【乐不思蜀】放进座 0 的判定区（`state.virtual_judge` 记着映射）；
    移到座 1 之后，座 1 的那张牌**仍然代表**【乐不思蜀】；而座 2 判定区已有【乐不思蜀】
    时，座 2 不是合法目的地。
    """
    from sgs.skills import active as module
    from sgs.skills.virtual import transformed_card

    state = _state(seats=4)
    weapon = cards_of_type(PACK_160, "qinggang_sword")[0]
    product = transformed_card(state, (weapon,), "indulgence")
    player = state.player(0)
    player.equip = [weapon]
    player.judge = [weapon]
    state.virtual_judge[weapon] = product

    other = cards_of_type(PACK_160, "indulgence")[0]
    state.player(2).judge = [other]
    state.virtual_judge[other] = transformed_card(
        state, (cards_of_type(PACK_160, "slash")[1],), "indulgence"
    )
    assert module._qianxun_effective_card(state, weapon).type == "indulgence"
    assert module._qianxun_move_destinations(state, 0, weapon) == (1, 3), (
        "座 2 的判定区已有【乐不思蜀】（产物）⇒ 不是合法目的地"
    )


def test_qianxun_move_destination_lists_illegal_seats_greyed() -> None:
    """用户 2026-11-22（第三轮）：目的地改成**牌桌点将**——非法目的地不进池，也不再画灰。

    （函数名沿用票 01 的旧标识：03a / 03b / 10 的 ``## Answer`` 与 effort 的验收账本都
    按这个名字引用，改名会让 ``tools/check_acceptance.py`` 把它们判成缺失引用；语义已按
    本票**改写**为"非法目的地不出现"，旧口径的"列出来画灰"整条退场。）

    局面：座 0 有一张武器；座 2 的武器槽已被另一张武器占着、座 3 的武器槽被废除。三处读
    **同一份**真源 :func:`_qianxun_move_destinations`：

    - 清单：``to`` 是**座位步**（``Step.kind == "seat"`` ⇒ 前端走"点场上武将"那条渲染路径），
      池里只有座 1；
    - 询问：``SELECT_OPTION`` 的候选 = ``legal_actions`` = 座 1（旧口径的"列出来画灰"退场）；
    - 校验器（[G3]）：带 ``seat:2`` / ``seat:3`` 的合成复合动作一律拒收。
    """
    from sgs.skills import active as module
    from sgs.skills.preactivation import preactivation_for, validate_activation

    hand = (cards_of_type(PACK_160, "qinggang_sword")[0],)
    other_weapon = cards_of_type(PACK_160, "green_dragon_blade")[0]
    specs = [
        SeatSpec(hero=HERO, equip=hand),
        SeatSpec(),
        SeatSpec(equip=(other_weapon,)),
        SeatSpec(),
    ]
    state = build_state(
        pack=PACK_160,
        seats=specs,
        deck=deck_excluding(PACK_160, *hand, other_weapon),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    state.player(3).sealed_zones = ("weapon",)
    sword = state.player(0).equip[0]

    # 效果事件的清单（代价已付 ⇒ 窗口开着，解析器选效果那一份）。
    module._qianxun_open_window(state, 0)
    steps = preactivation_for(state, 0, QIANXUN)
    assert [step.name for step in steps] == ["effect", "card", "to", "type"], (
        f"效果事件 = 选效果 + ② 的两步 + ③ 的一步：{[step.name for step in steps]}"
    )
    to_step = steps[2]
    assert to_step.kind == "seat", (
        f"目的地是**座位步**（前端据 Step.kind 走牌桌点将）：{to_step.kind}"
    )
    by_card = {row.when[1]: tuple(row.options) for row in to_step.pool}
    assert by_card[str(sword)] == ("seat:1",), (
        f"源牌在座 0：座 2 的武器槽已有牌、座 3 被废除 ⇒ 池里只有座 1：{by_card}"
    )
    assert "seat:2" not in {seat for seats in by_card.values() for seat in seats}, (
        "非法目的地不进池（不再列出来画灰）"
    )
    assert "seat:3" not in {seat for seats in by_card.values() for seat in seats}
    assert all(not row.disabled for row in to_step.pool), (
        "座位步没有置灰项：池外 = 不出现（旧口径的 disabled 已退场）"
    )

    # [G3] 校验器（引擎的第二道闸）：池内通过、池外拒收。
    assert validate_activation(
        state, 0, QIANXUN, (sword,), (module.QIANXUN_EFFECT_MOVE, "seat:1")
    ) is None
    for illegal in ("seat:2", "seat:3"):
        reason = validate_activation(
            state, 0, QIANXUN, (sword,), (module.QIANXUN_EFFECT_MOVE, illegal)
        )
        assert reason is not None, f"池外座位 {illegal} 必须被校验器拒收"

    # 逐步询问也同一份池：候选 = legal_actions = 座 1。
    module._qianxun_close_window(state, 0)
    engine = Engine.from_state(state, seed=41)
    seen: dict[str, list[str]] = {}

    class _Script(_PlayScript):
        def respond(self, query: Any) -> Any:
            if str(getattr(query, "skill_id", "") or "").endswith(":to"):
                seen["options"] = [str(item) for item in query.options]
                seen["legal"] = [str(a.choice) for a in query.legal_actions]
            return super().respond(query)

    script = _Script(
        (
            module.QIANXUN_COST_DAMAGE,
            module.QIANXUN_EFFECT_MOVE,
            # 票 03a 起源座位不再单独一问：选牌面的首槽 = 座 0 的武器 ⇒ 目的地池从座 1 起。
            "seat:1",
        )
    )
    run_queries(
        engine,
        [script, _PlayScript((), skill_id="none"), _PlayScript((), skill_id="none"),
         _PlayScript((), skill_id="none")],
        lambda e: bool(seen.get("legal")) and e.pending is not None,
    )
    assert seen["options"] == ["seat:1"], (
        f"候选只有合法目的地（座 2 已占 / 座 3 被废除都不出现）：{seen['options']}"
    )
    assert seen["legal"] == ["seat:1"], f"候选与 legal_actions 同源：{seen['legal']}"


def test_qianxun_empty_destination_pool_cancels_the_effect_without_rolling_back_the_cost() -> None:
    """[G1] 目的地池为空 ⇒ 这一步没有候选；效果事件**取消**，代价不回退（票 10 的口径）。

    2 人局：座 0 / 座 1 的武器槽都已有武器 ⇒ 无论移动哪一张，另一座的同槽都已占、无目的
    地。两处读数：

    - 清单：``to`` 仍是座位步，但每一行候选都是空元组（界面因此一个高亮框都不画）；
    - 引擎：目的地步不发出**没有候选**的询问（``PickSeats`` 的既有纪律），流水线按
      ``"cancel"`` 收尾 —— ``on_cancel`` 关掉效果窗口，代价位照旧、效果位不记。
    """
    from sgs.skills import active as module
    from sgs.skills.preactivation import preactivation_for

    sword = cards_of_type(PACK_160, "qinggang_sword")[0]
    other_weapon = cards_of_type(PACK_160, "green_dragon_blade")[0]
    state = build_state(
        pack=PACK_160,
        seats=[SeatSpec(hero=HERO, equip=(sword,)), SeatSpec(equip=(other_weapon,))],
        deck=deck_excluding(PACK_160, sword, other_weapon),
        turn_seat=0,
        phase=Phase.PLAY,
    )

    module._qianxun_open_window(state, 0)
    steps = preactivation_for(state, 0, QIANXUN)
    to_step = next(step for step in steps if step.name == "to")
    assert to_step.kind == "seat", f"目的地是座位步：{to_step.kind}"
    assert to_step.pool and all(not tuple(row.options) for row in to_step.pool), (
        f"每一行的候选都是空（两座的同槽都有牌）：{to_step.pool}"
    )
    module._qianxun_close_window(state, 0)

    engine = Engine.from_state(state, seed=42)

    class _Recording(_PlayScript):
        def __init__(self, options: tuple[str, ...]) -> None:
            super().__init__(options)
            self.queries: list[Any] = []

        def respond(self, query: Any) -> Any:
            self.queries.append(query)
            return super().respond(query)

    script = _Recording((module.QIANXUN_COST_DAMAGE, module.QIANXUN_EFFECT_MOVE))
    run_queries(
        engine,
        [script, _PlayScript((), skill_id="none")],
        lambda e: bool(module._qianxun_used_costs(e.state, 0))
        and not module._qianxun_window_open(e.state, 0)
        and e.pending is not None
        and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE",
    )
    assert not any(
        str(getattr(query, "skill_id", "") or "").endswith(":to")
        for query in script.queries
    ), "目的地池为空 ⇒ 不发没有候选的询问"
    assert module._qianxun_used_costs(state, 0) == {module.QIANXUN_COST_DAMAGE}, (
        "代价已付，效果事件取消不回退代价（票 10 的裁定）"
    )
    assert module._qianxun_used_effects(state, 0) == frozenset(), "效果位不记"
    assert state.player(0).hp == 3, "代价② 的 1 点伤害照常发生"
    assert sword in state.player(0).equip and other_weapon in state.player(1).equip, (
        "无事发生：两张牌都还在原座位"
    )


# ---------------------------------------------------------------------------
# qunying 票 03a：效果② 的跨座位公开区选择面（一步直选，源座位由牌反查）
# ---------------------------------------------------------------------------


def _cross_seat_move_state() -> tuple[GameState, int]:
    """效果② 的跨座位局面：座 0 装备区一把武器；座 1 判定区一张**转化过的**延时锦囊。

    座 2 / 3 的空场把"哪些座位进 ``spec.seats``"钉成"有牌的存活座位"，而不是"全体存活
    座位"；座 1 那张牌走 ``virtual_judge``（产物 = 乐不思蜀），用来钉住"判定区的转化牌也
    在面里、搬完产物不丢"。
    """
    from sgs.skills.virtual import transformed_card

    own_sword = cards_of_type(PACK_160, "qinggang_sword")[0]
    converted = cards_of_type(PACK_160, "slash")[1]
    product_source = cards_of_type(PACK_160, "dodge")[1]
    hand = (cards_of_type(PACK_160, "dodge")[0],)
    state = build_state(
        pack=PACK_160,
        seats=[
            SeatSpec(hero=HERO, hand=hand, equip=(own_sword,)),
            SeatSpec(judge=(converted,)),
            SeatSpec(),
            SeatSpec(),
        ],
        deck=deck_excluding(PACK_160, own_sword, converted, product_source, hand[0]),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    state.virtual_judge[converted] = transformed_card(state, (product_source,), "indulgence")
    return state, converted


class _CrossSeatMoveScript(_PlayScript):
    """效果② 的脚本参与者：在跨座位面上点**别人判定区**的那张牌（不是首槽）。"""

    def __init__(self, *, pick_seat: int, options: tuple[str, ...]) -> None:
        super().__init__(options)
        self.pick_seat = int(pick_seat)
        self.queries: list[Any] = []

    def respond(self, query: Any) -> Any:
        self.queries.append(query)
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "SELECT_CARDS":
            index = next(
                position
                for position, slot in enumerate(query.slots)
                if int(slot.ref.seat) == self.pick_seat and slot.ref.zone is Zone.JUDGE
            )
            return next(
                action
                for action in query.legal_actions
                if action.kind == "SelectCards" and tuple(action.picks) == (index,)
            )
        return super().respond(query)


def test_selection_face_multi_seat_is_the_seat_major_product() -> None:
    """票 03a §2：``selection_face(..., seats=...)`` = 座位优先的乘积；单座位输出逐字不变。

    既有的单座位调用点必须看到**同一批槽位、同一顺序**（先装备再判定、隐藏槽的洗牌仍只
    走一次）；多座位面则是"先座 0 的全部区，再座 1 的全部区"的拼接。
    """
    state, _converted = _cross_seat_move_state()
    engine = Engine.from_state(state, seed=64)
    zones = (Zone.EQUIP, Zone.JUDGE)
    single = engine.selection_face(0, zones)
    assert [(slot.ref.seat, slot.ref.zone) for slot in single[0]] == [
        (0, Zone.EQUIP)
    ], "单座位面与既有调用点同形（座 0 的装备区；本局面判定区为空）"

    both = engine.selection_face(0, zones, seats=(0, 1))
    first = engine.selection_face(0, zones)
    second = engine.selection_face(1, zones)
    assert both == (first[0] + second[0], first[1] + second[1]), (
        "多座位 = 逐座位拼接（座位优先），每个槽的 ref 带自己那座的真实座位"
    )
    assert [int(slot.ref.seat) for slot in both[0]] == [0, 1]


def test_qianxun_move_effect_asks_once_across_every_field_seat() -> None:
    """票 03a §3：删掉"选源座位"那一问 ⇒ **一次**选牌面，槽位跨座位、含判定区。

    断言走**真引擎**的效果②（不是直接调 `selection_face`）：询问里没有 ``:from`` 那一问，
    ``spec.seats`` = 有牌的存活座位，槽位同时覆盖自己的装备区与**别人**的判定区；
    源座位由**选中的那张牌**反查——点座 1 判定区那张牌，目的地池就不含座 1。
    """
    from sgs.skills import active as module

    state, converted = _cross_seat_move_state()
    engine = Engine.from_state(state, seed=61)
    script = _CrossSeatMoveScript(
        pick_seat=1,
        options=(
            module.QIANXUN_COST_DAMAGE,
            module.QIANXUN_EFFECT_MOVE,
            "seat:2",
        ),
    )
    run_queries(
        engine,
        [script, _PlayScript((), skill_id="none"), _PlayScript((), skill_id="none"),
         _PlayScript((), skill_id="none")],
        lambda e: bool(module._qianxun_used_effects(e.state, 0))
        and e.pending is not None
        and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE",
    )

    faces = [
        query for query in script.queries
        if str(getattr(query.kind, "value", query.kind)) == "SELECT_CARDS"
    ]
    assert len(faces) == 1, (
        f"效果② 只发**一次**选牌询问（源座位那一问必须退场）：{len(faces)} 次"
    )
    face = faces[0]
    assert face.spec.seats == (0, 1), (
        f"seats = 有牌的存活座位（座 2 / 3 空场，不进面）：{face.spec.seats}"
    )
    assert face.spec.zones == (Zone.EQUIP, Zone.JUDGE)
    assert {
        (int(slot.ref.seat), slot.ref.zone) for slot in face.slots
    } == {(0, Zone.EQUIP), (1, Zone.JUDGE)}, (
        "槽位 = 全体有牌座位的装备区 + 判定区（混合座位）"
    )
    assert not any(
        str(getattr(query, "skill_id", "") or "").endswith(":from")
        for query in script.queries
    ), "源座位不再作为单独一问"
    assert sum(
        1
        for query in script.queries
        if str(getattr(query, "skill_id", "") or "").endswith(":to")
    ) == 1, (
        "票 03b 风险 2：代价② 的伤害帧挂起 → 续跑重跑整条流水线时，分支的**目的地**一问"
        "走 accepted_option 复用答案（不得重问）"
    )

    # 源座位从选中的那张牌反查（座 1）⇒ 那块牌搬到座 2 的判定区。
    moved = state.player(2)
    assert list(moved.judge) == [converted], f"座 2 判定区应有那张牌：{moved.judge}"
    assert converted not in state.player(1).judge, "座 1 判定区已空"
    assert state.virtual_judge[converted].type == "indulgence", (
        "移动判定区的转化牌保持产物（乐不思蜀还是乐不思蜀）"
    )


def test_qianxun_move_face_candidates_are_the_only_accepted_answers() -> None:
    """票 03a §4 [C4]：候选面与 ``legal_actions`` 同源——非法槽位由**校验器**拒收。

    面里每个槽位恰好一条候选；越界位置 / 两张（``total=(1,1)``）都不在候选集合里，引擎
    必须先落拒绝记录再拒收，且挂起态不变。界面只负责渲染路径，合法性不靠它挡。
    """
    from sgs.engine import IllegalActionError
    from sgs.skills import active as module
    from sgs.state import SelectCards

    state, _converted = _cross_seat_move_state()
    engine = Engine.from_state(state, seed=62)
    script = _CrossSeatMoveScript(
        pick_seat=1,
        options=(module.QIANXUN_COST_DAMAGE, module.QIANXUN_EFFECT_MOVE),
    )
    for _ in range(400):
        pending = engine.pending
        if pending is None:
            engine._step()
            if engine.finished:
                break
            continue
        if str(getattr(pending.kind, "value", pending.kind)) == "SELECT_CARDS":
            break
        engine.submit(script.respond(pending))
    else:  # pragma: no cover - 没走到选牌面就是夹具坏了
        raise AssertionError("没走到效果② 的跨座位选牌面")

    assert {(int(slot.ref.seat), slot.ref.zone) for slot in pending.slots} == {
        (0, Zone.EQUIP), (1, Zone.JUDGE)
    }
    assert {tuple(action.picks) for action in pending.legal_actions} == {
        (position,) for position in range(len(pending.slots))
    }, "面里每个槽位恰好一条候选（同源）"

    for illegal in ((len(pending.slots),), (0, 1)):
        with pytest.raises(IllegalActionError):
            engine.submit(SelectCards(picks=illegal, answers_seq=pending.seq))
        assert engine.pending is pending, f"被拒的提交不改变挂起态：{illegal}"
    engine.submit(
        next(action for action in pending.legal_actions if tuple(action.picks) == (1,))
    )
    assert engine.pending is not pending, "候选里那条必须被收下"


def test_qianxun_invalid_effect_event_consumes_the_window_without_rolling_back_the_cost() -> None:
    """[G3] 票 10：空场 ⇒ 效果② 的选牌步空池 ⇒ **无事发生**：代价已付、效果位不记、不回退。

    用户 2026-11-22 裁定第 5 条：代价满足之后，效果无论可不可发动都不导致技能本身无法发动；
    只是这个效果不成立（这个**效果事件**用掉、效果位不记、没有任何效果副作用）。代价② 的
    1 点伤害照常发生。裁定原文的"除非还有别的代价可付"⇒ 技能回到**代价事件**（效果② 没有
    被记成本回合已用）。
    """
    from sgs.skills import active as module
    from sgs.skills.preactivation import preactivation_for

    state = _state(seats=3)
    engine = Engine.from_state(state, seed=63)
    script = _CrossSeatMoveScript(
        pick_seat=0,
        options=(module.QIANXUN_COST_DAMAGE, module.QIANXUN_EFFECT_MOVE),
    )
    run_queries(
        engine,
        [script, _PlayScript((), skill_id="none"), _PlayScript((), skill_id="none")],
        lambda e: bool(module._qianxun_used_costs(e.state, 0))
        and not module._qianxun_window_open(e.state, 0)
        and e.pending is not None
        and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE",
    )
    assert not [
        query for query in script.queries
        if str(getattr(query.kind, "value", query.kind)) == "SELECT_CARDS"
    ], "空场时不发选牌询问（效果② 这一步没有候选）"
    assert module._qianxun_used_costs(state, 0) == {module.QIANXUN_COST_DAMAGE}, (
        "代价已付、**不回退**整个技能的发动"
    )
    assert module._qianxun_used_effects(state, 0) == frozenset(), (
        "效果不成立 ⇒ 效果位不记（这个效果下一回合还在）"
    )
    assert state.player(0).hp == 3, "代价② 的 1 点伤害照常发生"
    assert not module._qianxun_window_open(state, 0), "效果事件已经用掉（窗口关闭）"
    steps = preactivation_for(state, 0, QIANXUN)
    assert [step.name for step in steps] == ["cost"], (
        f"还有可付的代价① ⇒ 技能回到代价事件（而不是重复提供那个效果事件）："
        f"{[step.name for step in steps]}"
    )
    assert steps[0].pool and tuple(steps[0].pool[0].options) == (
        module.QIANXUN_COST_DISCARD,
    ), f"只剩代价① 可付：{steps[0].pool}"


def test_lianying_spends_the_flags_on_equipment_peach_and_wine() -> None:
    """用户 2026-11-21 第二轮裁定：**使用装备牌 / 【桃】/ 【酒】同样指定目标（=

    使用者自己）**，故它们照常兑现【连营】② 并消费两个待生效位——"所有目标"就是自己，
    于是把自己横置。移动版势孙綝【戮连】是同一口径的先例（给自己上装备 / 吃酒桃都触发）。
    """
    from sgs.state import EndPhase, UseCard

    for card_type in ("qinggang_sword", "peach", "wine"):
        hand = (cards_of_type(PACK_160, card_type)[0],)
        state = build_state(
            pack=PACK_160,
            seats=[SeatSpec(hero=HERO, hand=hand, hp=3, hp_max=4), SeatSpec()],
            deck=deck_excluding(PACK_160, *hand),
            turn_seat=0,
            phase=Phase.PLAY,
        )
        player = state.player(0)
        player.lianying_chain_after = 0
        player.lianying_unlimited_after = 0
        player.used_or_played_ever = 1
        engine = Engine.from_state(state, seed=51)

        class _UseFirst(_PlayScript):
            def __init__(self) -> None:
                super().__init__(())
                self.done = False

            def respond(self, query: Any) -> Any:
                if query.kind == "PLAY_PHASE" and not self.done:
                    for action in query.legal_actions:
                        if isinstance(action, UseCard) and action.card.type == card_type:
                            self.done = True
                            return action
                return super().respond(query)

        script = _UseFirst()
        run_queries(
            engine,
            [script, _PlayScript((), skill_id="none")],
            lambda e: script.done and e.pending is not None
            and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE",
        )
        assert state.player(0).chained is True, f"用{card_type}指定了自己 ⇒ 自己横置"
        assert player.lianying_chain_after == -1, f"用{card_type}兑现了 ② 的位"
        assert player.lianying_unlimited_after == -1, "两个位共用同一次兑现点"


class _SecondSelfUse(_PlayScript):
    """出牌阶段脚本：先打唯一那张【杀】，再把 ③ 摸到的那张 ``second_type`` 用掉。"""

    def __init__(self, state: GameState, second_type: str) -> None:
        super().__init__(())
        self.state = state
        self.second_type = second_type
        self.played: list[str] = []
        self.flags_before_second: tuple[int, int] | None = None
        self.second_targets: tuple[int, ...] | None = None

    def respond(self, query: Any) -> Any:
        if query.kind == "PLAY_PHASE" and len(self.played) < 2:
            from sgs.state import UseCard

            player = self.state.player(0)
            if len(self.played) == 1:
                self.flags_before_second = (
                    int(player.lianying_unlimited_after),
                    int(player.lianying_chain_after),
                )
            wanted = "slash" if not self.played else self.second_type
            action = next(
                action
                for action in query.legal_actions
                if isinstance(action, UseCard) and action.card.type == wanted
            )
            if self.played:
                self.second_targets = tuple(action.targets)
            self.played.append(wanted)
            return action
        return super().respond(query)


def _play_second_self_use(second_type: str) -> tuple[GameState, Any, _SecondSelfUse]:
    """票 12 的用户局面：手牌 1 + 装备 1 + 判定 0。

    先打掉唯一那张手牌（【杀】）⇒ 手牌 0 == 判定 0 ⇒ ③ 摸一张（牌堆顶 = ``second_type``）⇒
    手牌 0→1 ⇒ 移动之后手牌数同时等于体力值（1）与装备区张数（1）⇒ ①② 两个位**动态**置位。
    再在出牌阶段把 ``second_type`` 用掉，返回 ``(state, player, script)``；``script`` 记下
    第二张牌之前的两个位与它自己的目标表。
    """
    hand = (cards_of_type(PACK_160, "slash")[0],)
    # 第二张是武器时，装备区放另一件装备，免得同名顶替把局面搞乱。
    equip_type = "renwang_shield" if second_type == "qinggang_sword" else "qinggang_sword"
    equip = cards_of_type(PACK_160, equip_type)[0]
    second = cards_of_type(PACK_160, second_type)[0]
    deck = (second, *deck_excluding(PACK_160, *hand, equip, second))
    state = build_state(
        pack=PACK_160,
        seats=[
            SeatSpec(hero=HERO, hand=hand, equip=(equip,), hp=1, hp_max=4),
            SeatSpec(),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
    )
    player = state.player(0)
    engine = Engine.from_state(state, seed=52)
    # ``Engine`` 从局面构造时仍会从**回合起点**跑一遍：把摸牌阶段改摸 0 张，出牌阶段才真是
    # 「手牌 1 + 装备 1 + 判定 0」这个局面（否则摸牌阶段会先摸两张）。
    engine.set_draw_phase_count(0, 0)
    script = _SecondSelfUse(state, second_type)
    run_queries(
        engine,
        [script, _PlayScript((), skill_id="none")],
        lambda e: len(script.played) == 2
        and e.pending is not None
        and str(getattr(e.pending.kind, "value", e.pending.kind)) == "PLAY_PHASE",
    )
    return state, player, script


def test_lianying_chains_wine_after_the_judge_zone_draw() -> None:
    """[G1] 用户 2026-11-22 第四轮实机（票 12）：手牌 1 + 装备 1 + 判定 0。

    用掉唯一那张手牌 ⇒ ③ 摸到【酒】（手牌 0→1）⇒ ①② 置位。再在出牌阶段用【酒】（对自己
    使用、没有选目标那一步）⇒ 「所有目标」= 自己 ⇒ 自己横置、两个位消费。
    """
    state, player, script = _play_second_self_use("wine")
    assert state.phase is Phase.PLAY, "这条路是**出牌阶段**的使用（濒死自救的【酒】不走这里）"
    assert script.flags_before_second is not None
    assert all(flag >= 0 for flag in script.flags_before_second), (
        f"③ 摸牌之后手牌数 == 体力值（1）且 == 装备区张数（1）⇒ ①② 两根位都置位："
        f"{script.flags_before_second}"
    )
    assert script.second_targets == (0,), (
        f"【酒】的「指定目标」路径**已经**把自己写进目标表（票 12 的怀疑不成立）："
        f"{script.second_targets}"
    )
    assert state.player(0).chained is True, (
        f"用【酒】指定自己 ⇒ 自己横置（chained={state.player(0).chained}，"
        f"链位={player.lianying_chain_after}）"
    )
    assert player.lianying_chain_after == -1, "用【酒】兑现了 ② 的位"
    assert player.lianying_unlimited_after == -1, "① ② 共用同一次兑现点"


@pytest.mark.parametrize("second_type", ("peach", "qinggang_sword"))
def test_lianying_chains_peach_and_equipment_on_the_same_auto_self_target_path(
    second_type: str,
) -> None:
    """[G2]【桃】/ 装备牌对自己使用走同一条"自动定目标"路径（用户局面里同样会先 ③ 摸牌）。"""
    state, player, script = _play_second_self_use(second_type)
    assert script.flags_before_second is not None and all(
        flag >= 0 for flag in script.flags_before_second
    ), f"①② 在第二张牌之前都已置位：{script.flags_before_second}"
    assert script.second_targets == (0,), (
        f"{second_type} 的目标 = 自己（不需要玩家选目标）：{script.second_targets}"
    )
    assert state.player(0).chained is True, (
        f"用{second_type}指定自己 ⇒ 自己横置（链位={player.lianying_chain_after}）"
    )
    assert player.lianying_chain_after == -1, f"{second_type} 兑现了 ② 的位"
    assert player.lianying_unlimited_after == -1, "① ② 共用同一次兑现点"


# ---------------------------------------------------------------------------
# qunying 票 03b：按前序选项分叉的步骤（FlowBranch）+ 效果② 的两步进清单
# ---------------------------------------------------------------------------


def _effect_flow():
    from sgs.skills import active as module

    return module.QIANXUN_EFFECT_FLOW


def _cost_flow():
    from sgs.skills import active as module

    return module.QIANXUN_COST_FLOW


def test_qianxun_cost_manifest_lists_only_the_cost_event_steps():
    """[G1] 代价事件的清单 = 选代价 + 代价③ 自己的两步；**看不到**任何效果步骤。"""
    from sgs.skills.flow import manifest
    from sgs.skills import active as module

    state, _converted = _cross_seat_move_state()
    sword = state.player(0).equip[0]
    steps = manifest(_cost_flow(), state, 0)
    assert [step.name for step in steps] == ["cost", "equip", "product"], (
        f"代价事件 = 选代价 + 代价③ 的两小步：{[step.name for step in steps]}"
    )
    assert [step.kind for step in steps] == ["option", "card", "option"]
    for branch_step in steps[1:]:
        assert branch_step.pool, f"{branch_step.name} 的行不空"
        for row in branch_step.pool:
            assert row.when[0] == module.QIANXUN_COST_EQUIP, (
                f"分支行只挂在代价③ 上：{row.when}"
            )
        assert not any(
            module.QIANXUN_COST_DISCARD in row.when
            or module.QIANXUN_COST_DAMAGE in row.when
            for row in branch_step.pool
        ), "代价 ① / ② 没有子步骤"
    equip_rows = {row.when[0]: tuple(row.options) for row in steps[1].pool}
    assert equip_rows[module.QIANXUN_COST_EQUIP] == (str(sword),), (
        f"代价③ 的原料 = 自己的装备牌（手牌里的非装备牌不进面）：{equip_rows}"
    )
    product_rows = {row.when[0]: tuple(row.options) for row in steps[2].pool}
    assert set(product_rows[module.QIANXUN_COST_EQUIP]) == set(module._qianxun_products(state, 0))
    assert not {"effect", "card", "to", "type"} & {step.name for step in steps}, (
        "效果事件的步骤一条都不许混进代价清单"
    )


def test_qianxun_effect_manifest_lists_each_effects_own_steps():
    """[G1] 效果事件的清单 = 选效果 + ② 的选牌 / 目的地、③ 的选类型（① 没有子步骤）。"""
    from sgs.skills.flow import manifest, preview_step
    from sgs.skills import active as module

    state, converted = _cross_seat_move_state()
    flow = _effect_flow()
    steps = manifest(flow, state, 0)
    assert [step.name for step in steps] == ["effect", "card", "to", "type"], (
        f"效果事件 = 选效果 + ② 的两步 + ③ 的一步：{[step.name for step in steps]}"
    )
    assert [step.kind for step in steps] == ["option", "card", "seat", "option"], (
        "目的地是座位步（qunying 票 09：牌桌点将），不是选项列表"
    )
    card_step, to_step, type_step = steps[1], steps[2], steps[3]
    for branch_step, option in (
        (card_step, module.QIANXUN_EFFECT_MOVE),
        (to_step, module.QIANXUN_EFFECT_MOVE),
        (type_step, module.QIANXUN_EFFECT_GAIN),
    ):
        assert branch_step.pool, f"{branch_step.name} 的行不空"
        for row in branch_step.pool:
            assert row.when[0] == option, (
                f"{branch_step.name} 的行必须钉在 {option} 上：{tuple(row.when)}"
            )

    # 预览（与校验器同一份池回调）：① 直接填完；② 停在选牌；③ 停在选类型。
    skipped = preview_step(flow, state, 0, [("effect", (module.QIANXUN_EFFECT_CARD,))], steps=steps)
    assert skipped.cursor == len(steps), f"效果① 后面没有步骤：cursor={skipped.cursor}"

    after_two = preview_step(flow, state, 0, [("effect", (module.QIANXUN_EFFECT_MOVE,))], steps=steps)
    assert after_two.name == "card", f"选② 后光标停在选牌步：{after_two.name}"
    assert set(after_two.options) == {
        str(state.player(0).equip[0]),
        str(converted),
    }, f"候选 = 场上所有装备 / 判定牌：{after_two.options}"

    after_gain = preview_step(flow, state, 0, [("effect", (module.QIANXUN_EFFECT_GAIN,))], steps=steps)
    assert after_gain.name == "type", f"选③ 后光标停在选类型步：{after_gain.name}"
    assert set(after_gain.options) == {"basic", "trick", "equipment"}

    editing_to = preview_step(
        flow,
        state,
        0,
        [
            ("effect", (module.QIANXUN_EFFECT_MOVE,)),
            ("card", (converted,)),
            ("to", ()),
        ],
        steps=steps,
    )
    assert editing_to.name == "to"
    assert editing_to.kind == "seat", "目的地步的预览形状也是座位步"
    assert editing_to.options == ("seat:0", "seat:2", "seat:3"), (
        "源座位（选中那张牌所在的座 1）不列为目的地"
    )


def test_qianxun_rejects_branch_answers_of_another_effect():
    """[G2][G3]：分支答案只属于被选中的选项；两个事件各校验自己那一段。

    「先选② 再改选①」时，效果② 的 ``card`` / ``to`` 答案**不能被读成** ① 的答案：① 没有
    分支步骤，故它们落在"多余的载荷"上被拒（与执行器读 ``answers`` 的方式同源）。
    代价事件同理：代价③ 的两步只属于代价③。
    """
    from sgs.skills.flow import validate_flow_activation
    from sgs.skills import active as module

    state, converted = _cross_seat_move_state()
    flow = _effect_flow()

    def verdict(picks, choices):
        return validate_flow_activation(
            flow, state, 0, tuple(picks), tuple(choices), frame=_Frame(source=0)
        )

    assert verdict((), (module.QIANXUN_EFFECT_CARD,)) is None
    assert verdict((converted,), (module.QIANXUN_EFFECT_CARD,)) is not None, (
        "改了效果① 之后还带着效果② 的 picks ⇒ 多余载荷（fail-closed）"
    )
    assert verdict((), (module.QIANXUN_EFFECT_MOVE,)) is not None, (
        "选中效果② 却缺选牌答案 ⇒ 拒收"
    )
    assert verdict((converted,), (module.QIANXUN_EFFECT_MOVE,)) is not None, (
        "选中效果② 却缺目的地 ⇒ 拒收"
    )
    assert verdict((converted,), (module.QIANXUN_EFFECT_MOVE, "seat:2")) is None
    assert verdict((converted,), (module.QIANXUN_EFFECT_MOVE, "seat:1")) is not None, (
        "源座位不是合法目的地（校验器读同一份目的地池）"
    )
    assert verdict((), (module.QIANXUN_EFFECT_GAIN, "basic")) is None
    assert verdict((), (module.QIANXUN_EFFECT_GAIN,)) is not None, "选中效果③ 却缺类型 ⇒ 拒收"

    sword = state.player(0).equip[0]
    cost_flow = _cost_flow()

    def cost_verdict(picks, choices):
        return validate_flow_activation(
            cost_flow, state, 0, tuple(picks), tuple(choices), frame=_Frame(source=0)
        )

    assert cost_verdict((), (module.QIANXUN_COST_DAMAGE,)) is None
    assert cost_verdict((sword,), (module.QIANXUN_COST_DAMAGE,)) is not None, (
        "代价② 没有子步骤：选牌答案多余"
    )
    assert cost_verdict((sword,), (module.QIANXUN_COST_EQUIP, "lightning")) is None
    assert cost_verdict((), (module.QIANXUN_COST_EQUIP, "lightning")) is not None, (
        "代价③ 缺选牌答案 ⇒ 拒收"
    )
    assert cost_verdict((sword,), (module.QIANXUN_COST_EQUIP,)) is not None, (
        "代价③ 缺产物 ⇒ 拒收"
    )


def _two_equipment_state():
    """座 0 装备区两张武器；座 1 空场（搬牌的目的地）。"""
    first = cards_of_type(PACK_160, "qinggang_sword")[0]
    second = cards_of_type(PACK_160, "green_dragon_blade")[0]
    state = build_state(
        pack=PACK_160,
        seats=[SeatSpec(hero=HERO, equip=(first, second)), SeatSpec()],
        deck=deck_excluding(PACK_160, first, second),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    return state, first, second


class _OnceQianxunScript:
    """发动【谦逊】的脚本：按给定的代价 / 效果 / 目的地答，其余取第一条候选。

    票 10 起一次"发动技能"要走**两次**（代价事件 → 效果事件），故它在每个出牌阶段各点一次
    技能，直到 ``activations`` 用完。
    """

    def __init__(
        self,
        *,
        cost: str,
        effect: str,
        to: str = "seat:1",
        product: str = "lightning",
        activations: int = 2,
    ) -> None:
        self.cost = cost
        self.effect = effect
        self.to = to
        self.product = product
        self.wanted = activations
        self.activations = 0
        self.queries: list[Any] = []

    def respond(self, query: Any) -> Any:
        self.queries.append(query)
        kind = str(getattr(query.kind, "value", query.kind))
        if kind == "PLAY_PHASE":
            if self.activations < self.wanted:
                self.activations += 1
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill) and action.skill_id == QIANXUN
                )
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        if kind == "SELECT_OPTION":
            sid = str(getattr(query, "skill_id", "") or "")
            wanted = None
            if sid.endswith(":cost"):
                wanted = self.cost
            elif sid.endswith(":effect"):
                wanted = self.effect
            elif sid.endswith(":to"):
                wanted = self.to
            elif sid.endswith(":product"):
                wanted = self.product
            if wanted is not None:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, SelectOption) and action.choice == wanted
                )
        return next(action for action in query.legal_actions)


def _drive_once(engine, script) -> None:
    """推进到下一次出牌阶段（两次发动都走完 / 被取消）。"""
    players = [script, _PlayScript((), skill_id="none")]
    for _ in range(400):
        if engine.finished:
            return
        pending = engine.pending
        if pending is None:
            engine._step()
            continue
        if (
            str(getattr(pending.kind, "value", pending.kind)) == "PLAY_PHASE"
            and script.activations >= script.wanted
            and any(isinstance(action, EndPhase) for action in pending.legal_actions)
        ):
            return
        engine.submit(script.respond(pending))
    raise AssertionError("步数上限内没有回到出牌阶段")


def test_qianxun_cost_three_then_effect_two_both_work_on_the_field():
    """票 10：代价③ 打出的那张牌**先**进自己的判定区；效果事件随后才在场上选牌。

    两个事件是两次发动 ⇒ 03b 的"代价③ 与效果② 不能共用同一张装备牌"（风险 1）不再成立：
    代价结算完，那张牌**已经是场上的一张牌**，效果② 的选牌面因此包含它。本用例钉住次序：
    第一问（代价事件的原料面）= 自己的手牌 / 装备区；第二问（效果事件的场上选牌面）=
    跨座位公开区，且里面同时有"另一张装备"与"刚打出的那张延时锦囊"。
    """
    from sgs.skills import active as module

    state, first, second = _two_equipment_state()
    engine = Engine.from_state(state, seed=71)
    script = _OnceQianxunScript(
        cost=module.QIANXUN_COST_EQUIP, effect=module.QIANXUN_EFFECT_MOVE
    )
    _drive_once(engine, script)

    faces = [
        query
        for query in script.queries
        if str(getattr(query.kind, "value", query.kind)) == "SELECT_CARDS"
    ]
    assert len(faces) == 2, (
        f"两个事件各一次选牌：代价③ 的原料面 + 效果② 的场上选牌面（{len(faces)}）"
    )
    cost_face, move_face = faces
    assert not cost_face.spec.seats, "第一问是代价事件：自己的手牌 / 装备区"
    assert move_face.spec.seats, "第二问是效果事件：跨座位公开区"
    assert len(cost_face.slots) == 2, "代价③ 的原料 = 两张装备牌"

    cost_picked = int(cost_face.slots[0].card.card_id)
    assert cost_picked in state.player(0).judge, "代价③ 的原料进了自己的判定区"
    assert state.virtual_judge[cost_picked].type == "lightning", "产物是【闪电】"
    assert move_face.spec.seats, "效果事件的选牌面跨座位"

    move_slot_cards = {int(slot.card.card_id) for slot in move_face.slots}
    assert cost_picked in move_slot_cards, (
        f"刚打出的那张已经是场上的一张牌（效果② 可以移动它）：{move_slot_cards}"
    )
    picked = int(move_face.slots[0].card.card_id)
    other = second if picked == first else first
    assert picked not in state.player(0).equip, "被搬走的那张离开了自己的装备区"
    assert picked in state.player(1).equip, "搬到了座 1 的装备区"
    assert other in state.player(0).judge, "另一张被代价③ 当成延时锦囊放进了自己的判定区"
    assert state.virtual_judge[other].type == "lightning", "代价③ 的产物是【闪电】"
    assert module._qianxun_used_costs(state, 0) == {module.QIANXUN_COST_EQUIP}
    assert module._qianxun_used_effects(state, 0) == {module.QIANXUN_EFFECT_MOVE}


def test_qianxun_single_equipment_card_pays_the_cost_and_becomes_a_field_card():
    """票 10：只有一张装备牌时它照样付得起代价③，结算完就是场上的牌（不再"面被排空"）。

    03b 的"风险 1 另一半"（唯一装备牌被效果② 的面排除 ⇒ 整条发动取消）在事件拆分后消失：
    代价已经付清，效果事件面对的是**场上**的牌 —— 那张牌正躺在自己的判定区（产物仍在）。
    """
    from sgs.skills import active as module

    sword = cards_of_type(PACK_160, "qinggang_sword")[0]
    state = build_state(
        pack=PACK_160,
        seats=[SeatSpec(hero=HERO, equip=(sword,)), SeatSpec()],
        deck=deck_excluding(PACK_160, sword),
        turn_seat=0,
        phase=Phase.PLAY,
    )
    engine = Engine.from_state(state, seed=72)
    script = _OnceQianxunScript(
        cost=module.QIANXUN_COST_EQUIP, effect=module.QIANXUN_EFFECT_MOVE
    )
    _drive_once(engine, script)

    faces = [
        query
        for query in script.queries
        if str(getattr(query.kind, "value", query.kind)) == "SELECT_CARDS"
    ]
    assert len(faces) == 2, "代价③ 的原料面 + 效果② 的场上选牌面（不再是'取消'）"
    cost_face, move_face = faces
    assert [int(slot.card.card_id) for slot in cost_face.slots] == [sword]
    assert [int(slot.card.card_id) for slot in move_face.slots] == [sword], (
        "场上只有刚打出的那一张（在自己的判定区）"
    )
    assert module._qianxun_used_costs(state, 0) == {module.QIANXUN_COST_EQUIP}
    assert module._qianxun_used_effects(state, 0) == {module.QIANXUN_EFFECT_MOVE}
    assert sword in state.player(1).judge, "被效果② 搬到座 1 的判定区"
    assert state.virtual_judge[sword].type == "lightning", "移动判定区的转化牌保持产物"


def test_qianxun_composite_actions_drive_the_two_events() -> None:
    """预发动确认走的是**复合动作**（``ActivateSkill(picks, choices)``）：两个事件各一份。

    这是界面 / AI 真正走的那条路（`validate_activation` → 帧私有 ``activation`` →
    ``run_flow`` 摊回逐步答案）。先提交**代价事件**的复合动作（代价②，无 picks）⇒ 结算完、
    窗口打开；再提交**效果事件**的复合动作（效果② 的选牌 + 目的地）⇒ 搬牌、记效果位。
    两段的 ``flow`` 名归属按持有者状态现算（`engine._activation_payload`），摊回因此认得出
    是哪一份流水线。
    """
    from sgs.skills import active as module
    from sgs.skills.preactivation import preactivation_for

    state, converted = _cross_seat_move_state()
    engine = Engine.from_state(state, seed=73)
    for _ in range(400):
        if engine.pending is not None:
            break
        engine._step()
    pending = engine.pending
    assert pending is not None and str(pending.kind) == "PLAY_PHASE", pending

    engine.submit(
        ActivateSkill(
            skill_id=QIANXUN,
            picks=(),
            choices=(module.QIANXUN_COST_DAMAGE,),
            answers_seq=pending.seq,
        )
    )
    for _ in range(400):
        if engine.pending is not None or engine.finished:
            break
        engine._step()
    assert module._qianxun_used_costs(state, 0) == {module.QIANXUN_COST_DAMAGE}, (
        "代价事件的复合动作被收下并结算：代价位写入"
    )
    assert module._qianxun_window_open(state, 0), "结算完打开效果事件窗口"
    pending = engine.pending
    assert pending is not None and str(pending.kind) == "PLAY_PHASE", pending
    assert [step.name for step in preactivation_for(state, 0, QIANXUN)] == [
        "effect",
        "card",
        "to",
        "type",
    ], "第二次发动发放的是效果事件的清单"

    engine.submit(
        ActivateSkill(
            skill_id=QIANXUN,
            picks=(converted,),
            choices=(module.QIANXUN_EFFECT_MOVE, "seat:2"),
            answers_seq=pending.seq,
        )
    )
    for _ in range(400):
        if engine.pending is not None or engine.finished:
            break
        engine._step()
    assert list(state.player(2).judge) == [converted], "效果事件的复合动作把牌搬到座 2 判定区"
    assert converted not in state.player(1).judge
    assert state.virtual_judge[converted].type == "indulgence", "移动判定区的转化牌保持产物"
    assert module._qianxun_used_effects(state, 0) == {module.QIANXUN_EFFECT_MOVE}
    assert not module._qianxun_window_open(state, 0), "效果结算完窗口关闭"
