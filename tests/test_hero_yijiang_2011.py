"""一将成名-2011·移动版：徐盛【破军】（yijiang-2011-xusheng-chunhua 票 01）。

牌面（移动版 = 用户资产 `D:\\SGS_HEROS\\一将成名-2011\\徐盛\\技能.txt`，用户 2026-11-20 签）：

    破军：出牌阶段，当你使用【杀】指定一名角色为目标后，你可以将该角色的至多X张牌
        置于其武将牌上（X为该角色的体力值），然后当前回合结束时，其获得武将牌上的牌。

本文件钉三件事：

1. **数据面 / 声明面**：独立移动版表（版本 `yijiang-2011-mobile`、显示名「徐盛移动版」）、
   放置三项声明（公开 / 回合末归还 / 无附加效果）、归还段是标记载体的效果定义且
   `dispatch_tier = HERO`（票 91 的加法：与目标自己的回合末技能同面选先后）。
2. **放置**：使用【杀】指定目标后 → 询问 → 选 1..X 张（X = **该目标**的体力值）→ 进**目标**的
   武将牌上（区键带目标座位）+ 一枚「破军」标记；判定区不铺进选牌面。
3. **归还**：当前回合结束时整批还回目标手里，标记被移去；`turn_after_end` 的整批兜底不会
   重复归还（区已空）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sgs.data import RuleConfig, hero_placement_decl, load_content_pack, load_heroes
from sgs.effects import damage_amount, damage_base_amount
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.skills.catalog import hero_card_zone_key
from sgs.skills.hero_yijiang_2011 import (
    POJUN_MARK,
    XUSHENG_POJUN,
    ZHANGCHUNHUA_JUEQING,
    ZHANGCHUNHUA_SHANGSHI,
    _pojun_condition,
    _pojun_return_condition_for,
)
from sgs.state import (
    ActivateSkill,
    EndPhase,
    FrameKind,
    Pass,
    Phase,
    PlayCard,
    SelectCards,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing
from support import (
    SeatSpec,
    build_state,
    cards_of_type,
    deck_excluding,
    ran_timing,
)

PACK = load_content_pack(deck_profile="standard_108")
HERO = "xusheng_mobile"
ME = 0
TARGET = 1
SLASH = cards_of_type(PACK, "slash")
DODGE = cards_of_type(PACK, "dodge")
INDULGENCE = cards_of_type(PACK, "indulgence")[0]
"""一张延时锦囊（判定区里的牌——破军**不**该能拿走它）。"""


def _state(*specs: SeatSpec, turn_seat: int = ME, phase: Phase = Phase.PLAY):
    """两座局面：**第一个** ``specs`` 是座位 0（徐盛移动版，**手牌要写在这里**），其余顺延。

    手牌必须经 ``SeatSpec`` 给：手牌同时还在牌堆里的话 ``locate`` 会给出两个来源区
    （``move_card`` 直接拒绝），测试就测不到技能本身了。
    """
    seats = [replace(specs[0], hero=HERO) if specs else SeatSpec(hero=HERO)]
    seats.extend(specs[1:])
    while len(seats) < 2:
        seats.append(SeatSpec())
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *used),
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(first_seat=0),
    )


class _Pojun:
    """应答器：出牌阶段先打出去那张【杀】、能发动【破军】就发动、选牌取给定 id。"""

    def __init__(
        self,
        *,
        picks: tuple[int, ...] = (),
        activate: bool = True,
        slash: int | None = None,
    ) -> None:
        self.picks = picks
        self.activate = activate
        self.slash = slash
        self.played = False
        self.faces: list[tuple[str, ...]] = []
        self.selections: list[Any] = []

    def respond(self, query):
        if query.kind == "PLAY_PHASE" and self.slash is not None and not self.played:
            self.played = True
            for action in query.legal_actions:
                card = getattr(action, "card", None)
                if card is not None and getattr(card, "card_id", None) == self.slash:
                    return action
        if query.kind == "SKILL_ACTIVATION":
            picks = tuple(
                action.skill_id
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
            self.faces.append(picks)
            if self.activate and XUSHENG_POJUN in picks:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                    and action.skill_id == XUSHENG_POJUN
                )
        if query.kind == "SELECT_CARDS":
            self.selections.append(query)
            wanted = frozenset(self.picks)
            for action in query.legal_actions:
                if not isinstance(action, SelectCards):
                    continue
                # ``picks`` 是**槽位下标**：可见槽（装备区）带牌面，隐藏槽（别人手牌）不带。
                picked = tuple(
                    int(query.slots[index].card.card_id)
                    for index in action.picks
                    if query.slots[index].card is not None
                )
                if len(picked) == len(action.picks) and frozenset(picked) == wanted:
                    return action
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _play_slash(engine: Engine, slash: int, target: int = TARGET):
    """从出牌阶段发一张【杀】：优先走 ``PlayCard``，没有就退回引擎入口。"""
    del target
    state = engine.state
    engine.start_use(state.card(slash), source=ME, targets=(TARGET,))


def _drive(engine: Engine, stop, *, limit: int = 900) -> Engine:
    for _ in range(limit):
        if stop(engine):
            return engine
        if engine.pending is not None:
            del engine.pending  # 由调用方的应答器处理（见各用例）
            return engine
        engine._step()
        if engine.finished:
            return engine
    raise AssertionError("步数上限内没有到达停止条件")


def _run(engine: Engine, player: _Pojun, stop, *, limit: int = 900) -> Engine:
    """推进引擎到 ``stop(engine)``；每个挂起询问交给 ``player`` 应答。"""
    for _ in range(limit):
        if stop(engine):
            return engine
        if engine.pending is not None:
            engine.submit(player.respond(engine.pending))
            continue
        engine._step()
        if engine.finished:
            return engine
    raise AssertionError("步数上限内没有到达停止条件")


# ---------------------------------------------------------------------------
# 数据面 / 声明面
# ---------------------------------------------------------------------------


def test_xusheng_mobile_is_in_the_mobile_table_with_pojun() -> None:
    hero = load_heroes("yijiang_2011_mobile").by_id(HERO)
    assert hero is not None, "徐盛移动版必须落在 heroes.yijiang_2011_mobile.json"
    assert hero.name == "徐盛移动版"
    assert hero.true_name == "徐盛"
    assert hero.kingdom.value == "wu" and hero.gender.value == "male"
    assert hero.hp_max == 4
    assert hero.version == "yijiang-2011-mobile"
    assert hero.enabled is True
    assert [skill.id for skill in hero.skills] == ["pojun"]


def test_pojun_declares_placement_and_a_hero_tier_return_segment() -> None:
    definition = CATALOG[XUSHENG_POJUN]
    assert definition.locked is False, "「可以」⇒ 每次问一次是否发动"
    assert definition.places_on_hero_card is True
    assert definition.preactivated is False, "首步读别人手牌（隐藏面）⇒ 不翻预发动"
    assert [hook.timing for hook in definition.hooks] == [
        Timing.USE_AFTER_DESIGNATE_TARGET
    ]
    decl = hero_placement_decl(XUSHENG_POJUN, pack_id=PACK.pack_id)
    assert decl is not None, "放置类技能必须在数据里声明三项"
    assert decl.visibility.value == "public"
    assert decl.return_timing.value == "turn_end"
    assert decl.extra_effect is False, "没有附加流程 ⇒ 引擎的回合末整批兜底也认它"

    back = CATALOG[f"{XUSHENG_POJUN}_return"]
    assert back.carrier.value == "MARKER", "归还段是**标记自身效果**（失效不影响）"
    assert back.dispatch_tier is not None, "票 91：声明成武将档才与目标自己的回合末技能同面"
    assert int(back.dispatch_tier) == 0, "HERO 档"
    assert [hook.timing for hook in back.hooks] == [Timing.TURN_END]
    assert back.hooks[0].forced is True, "不问「是否发动」：到点就还"


# ---------------------------------------------------------------------------
# ① 使用【杀】指定目标后 → 扣牌进**目标**的武将牌上
# ---------------------------------------------------------------------------


def test_pojun_moves_the_target_cards_to_its_own_hero_card() -> None:
    equip_armor = cards_of_type(PACK, "eight_diagrams")[0]
    state = _state(
        SeatSpec(hand=(SLASH[0], DODGE[0])),
        SeatSpec(hand=(DODGE[1],), equip=(equip_armor,)),
    )
    engine = Engine(state, seed=7)
    player = _Pojun(picks=(equip_armor,), slash=SLASH[0])

    _run(engine, player, lambda e: bool(state.player(TARGET).hero_cards))

    key = hero_card_zone_key(TARGET, XUSHENG_POJUN)
    assert state.hero_zone(TARGET, key) == (equip_armor,), "牌扣在**目标**的武将牌上"
    assert key in state.player(TARGET).hero_cards
    assert state.player(TARGET).hero_cards[key].owner == TARGET, (
        "区的归属 = 回合末的收件人 = 被扣牌的目标"
    )
    markers = [m for m in state.player(TARGET).markers if m.name == POJUN_MARK]
    assert len(markers) == 1, "扣牌同时挂一枚「破军」标记"
    assert markers[0].effect == f"{XUSHENG_POJUN}_return"
    assert markers[0].source_skill_id == XUSHENG_POJUN


def test_pojun_returns_the_cards_at_the_end_of_the_current_turn() -> None:
    equip_armor = cards_of_type(PACK, "eight_diagrams")[0]
    state = _state(
        SeatSpec(hand=(SLASH[0], DODGE[0])),
        SeatSpec(hand=(DODGE[1], DODGE[2], DODGE[3]), equip=(equip_armor,)),
    )
    engine = Engine(state, seed=7)
    player = _Pojun(picks=(equip_armor,), slash=SLASH[0])

    _run(engine, player, lambda e: bool(state.player(TARGET).hero_cards))
    key = hero_card_zone_key(TARGET, XUSHENG_POJUN)
    assert state.hero_zone(TARGET, key) == (equip_armor,)

    # 过完座位 0 的整个回合（结束阶段 / 回合末都在里面）。
    _run(engine, player, lambda e: e.state.turn_seat != ME)

    assert state.hero_zone(TARGET, key) == (), "当前回合结束时整批还回去"
    assert equip_armor in state.player(TARGET).hand, "还给的是被扣牌的目标本人"
    assert not [m for m in state.player(TARGET).markers if m.name == POJUN_MARK], (
        "归还之后标记移去"
    )
    returns = [
        record
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and record.from_zone == ZoneRef(Zone.HERO, TARGET, key)
        and record.to_zone == ZoneRef(Zone.HAND, TARGET)
    ]
    assert len(returns) == 1, "只有一条整批归还（引擎兜底没有重复归还）"


def test_pojun_never_offers_the_judge_zone() -> None:
    state = _state(SeatSpec(hand=(SLASH[0], DODGE[0])), SeatSpec(judge=(INDULGENCE,)))
    engine = Engine(state, seed=7)
    player = _Pojun(slash=SLASH[0])

    _run(engine, player, lambda e: e.state.turn_seat != ME)

    assert player.faces == [] or XUSHENG_POJUN not in player.faces[0], (
        "目标只有判定区的牌 ⇒ 没有可扣的牌，连「是否发动」都不问"
    )
    assert state.zone(ZoneRef(Zone.JUDGE, TARGET)) == [INDULGENCE], (
        "判定区的牌不属于「该角色的牌」（账本 L1008）"
    )


def test_pojun_x_is_the_target_hp_not_its_card_count() -> None:
    """X = **该目标**当前体力值：3 张手牌的 2 血目标最多只给 2 张。"""
    state = _state(
        SeatSpec(hand=(SLASH[0], DODGE[0])),
        SeatSpec(hand=(DODGE[1], DODGE[2], DODGE[3]), hp=2, hp_max=4),
    )
    engine = Engine(state, seed=7)
    player = _Pojun(slash=SLASH[0])

    _run(engine, player, lambda e: bool(player.selections))

    query = player.selections[0]
    assert query.spec.total == (1, 2), "上限 = min(目标体力值, 它的牌数)"
    assert query.spec.optional is True, "「至多」= 上限 + 可放弃（账本）"
    assert any(isinstance(action, Pass) for action in query.legal_actions), (
        "放弃口在候选里（Pass）"
    )


def test_pojun_condition_requires_the_owners_own_play_phase() -> None:
    """徐盛版带「出牌阶段，」⇒ 别人的出牌阶段（借刀杀人逼出的【杀】）不触发。"""
    from sgs.resolution import Frame

    state = _state(
        SeatSpec(hand=(SLASH[0], DODGE[0])),
        SeatSpec(hand=(DODGE[0],)),
        turn_seat=TARGET,
    )

    class _Host:
        def __init__(self, state):
            self.state = state

    frame = Frame(kind=Timing.USE_AFTER_DESIGNATE_TARGET, data={})
    frame.data["source"] = ME
    frame.data["card"] = state.card(SLASH[0])
    frame.data["targets"] = (TARGET,)
    frame.data["hook_owner"] = ME
    host = _Host(state)

    assert _pojun_condition(host, frame) is False, "回合角色不是我 ⇒ 不触发"
    state.turn_seat = ME
    assert _pojun_condition(host, frame) is True
    state.phase = Phase.DRAW
    assert _pojun_condition(host, frame) is False, "不在出牌阶段 ⇒ 不触发"


def test_pojun_return_segment_reads_the_zone_not_the_marker() -> None:
    """归还段的条件读**区**：标记被清掉（来源失去技能）时仍有牌可还，交给兜底。"""
    from sgs.resolution import Frame
    from sgs.state.models import HeroCardZone

    state = _state(SeatSpec(), SeatSpec(hand=(DODGE[0],)))
    key = hero_card_zone_key(TARGET, XUSHENG_POJUN)
    state.player(TARGET).hero_cards[key] = HeroCardZone(
        skill_id=XUSHENG_POJUN, owner=TARGET, cards=[DODGE[0]]
    )
    state.player(TARGET).hand = []

    class _Host:
        def __init__(self, state):
            self.state = state

    frame = Frame(kind=Timing.TURN_END, data={})
    frame.data["hook_owner"] = TARGET
    host = _Host(state)

    assert _pojun_return_condition_for(XUSHENG_POJUN)(host, frame) is True, "区里有牌 ⇒ 该还"
    assert not state.player(TARGET).markers, "条件不看标记"


# ---------------------------------------------------------------------------
# 票 03：界徐盛移动版【破军】①②（同一份代码的另一个版本）
# ---------------------------------------------------------------------------

JIE_XUSHENG = "jie_xusheng_mobile"
JIE_POJUN = "hero.jie_xusheng_mobile.pojun"


def test_jie_xusheng_mobile_is_in_its_own_mobile_table() -> None:
    hero = load_heroes("jie_yijiang_2011_mobile").by_id(JIE_XUSHENG)
    assert hero is not None, "界徐盛移动版必须落在 heroes.jie_yijiang_2011_mobile.json"
    assert hero.name == "界徐盛移动版"
    assert hero.true_name == "徐盛", "真名与徐盛共用 ⇒ 同一局只出一个"
    assert hero.kingdom.value == "wu" and hero.gender.value == "male"
    assert hero.hp_max == 4
    assert hero.version == "jie-yijiang-2011-mobile"
    assert hero.enabled is True
    assert [skill.id for skill in hero.skills] == ["pojun"]

    decl = hero_placement_decl(JIE_POJUN, pack_id=PACK.pack_id)
    assert decl is not None
    assert decl.return_timing.value == "turn_end" and decl.extra_effect is False


def test_jie_pojun_declares_the_second_clause_on_the_same_skill() -> None:
    """② 挂**本体**（不是归还段）：一个技能 id、两半效果（① 的钩子 + ② 的修饰器）。"""
    definition = CATALOG[JIE_POJUN]
    assert [hook.timing for hook in definition.hooks] == [
        Timing.USE_AFTER_DESIGNATE_TARGET
    ]
    assert [modifier.point.value for modifier in definition.modifiers] == [
        "DAMAGE_DEAL_AMOUNT"
    ], "② = 造成伤害时 +1（来源侧数值；与界【烈弓】② / 古锭刀同一挂点）"
    back = CATALOG[f"{JIE_POJUN}_return"]
    assert back.carrier.value == "MARKER" and int(back.dispatch_tier) == 0
    assert back.modifiers == (), "归还段不带 ② 的修饰器"


def test_jie_pojun_bonus_reads_both_counts_at_damage_time() -> None:
    """②：目标的手牌数与装备区张数**均不大于我** ⇒ +1；任一更大 ⇒ 不加。"""
    from sgs.effects import ModifierContext, ModifierRole
    from sgs.skills.hero_yijiang_2011 import _jie_pojun_bonus

    state = _state(
        SeatSpec(hand=(SLASH[0], DODGE[0], DODGE[1])),
        SeatSpec(hand=(DODGE[2], DODGE[3])),
    )
    slash = state.card(SLASH[0])

    def bonus() -> int:
        ctx = ModifierContext(
            subject=ME,
            target=TARGET,
            role=ModifierRole.SUBJECT,
            card=slash,
            owner=ME,
        )
        return _jie_pojun_bonus(state, ctx, 1)

    assert bonus() == 2, "1 + 1（目标 2 张手牌 ≤ 我 3 张、装备都空）"

    state.player(TARGET).hand = [DODGE[2]] * 4  # 目标手牌比我多
    assert bonus() == 1, "目标手牌更多 ⇒ 不加"

    state.player(TARGET).hand = []
    armor = cards_of_type(PACK, "eight_diagrams")[0]
    state.player(TARGET).equip = [armor]
    assert bonus() == 1, "目标装备比我多 ⇒ 不加"


def test_jie_pojun_condition_has_no_play_phase_restriction() -> None:
    """A4：界徐盛没有「出牌阶段，」前缀 ⇒ 别人的回合里被【借刀杀人】逼出的【杀】也触发。"""
    from sgs.resolution import Frame
    from sgs.skills.hero_yijiang_2011 import _jie_pojun_condition, _pojun_condition

    state = _state(
        SeatSpec(hand=(SLASH[0], DODGE[0])),
        SeatSpec(hand=(DODGE[1],)),
        turn_seat=TARGET,
    )

    class _Host:
        def __init__(self, state):
            self.state = state

    frame = Frame(kind=Timing.USE_AFTER_DESIGNATE_TARGET, data={})
    frame.data["source"] = ME
    frame.data["card"] = state.card(SLASH[0])
    frame.data["targets"] = (TARGET,)
    frame.data["hook_owner"] = ME
    host = _Host(state)

    assert _pojun_condition(host, frame) is False, "徐盛版：回合角色不是我 ⇒ 不触发"
    assert _jie_pojun_condition(host, frame) is True, "界徐盛版：没有阶段限定 ⇒ 触发"


# ---------------------------------------------------------------------------
# 张春华移动版【绝情】【伤逝】（票 02）
#
# 移动版原文（`D:\SGS_HEROS\一将成名-2011\张春华\技能.txt` 逐字）：
#
#     绝情／锁定技，你即将造成的伤害均视为失去体力。
#     伤逝／当你的手牌数小于X时，你可以将手牌摸至X张（X为你已损失的体力值）。
#
# 口径 = 用户 2026-11-20 的逐条裁定（票面 §0 Q1–Q5）：
# - 绝情 = 伤害事件**整段不发生**，改为"失去等同于伤害值基数"（无来源 / 不碰护甲 /
#   不触发任何"造成 / 受到伤害"类技能 / 不传导）；
# - 金额 = 基数（含【酒】与"基础伤害值 +N"那一档，**不含**"造成伤害时 +N"）；
# - 伤逝的触发面 = 手牌数小于 X 的每一次状态变化后（失去手牌 / 体力真的降了 / 回复体力 /
#   加体力上限），可选、X 现算、摸至 X 张。
# ---------------------------------------------------------------------------

ZHANGCHUNHUA = "zhangchunhua_mobile"
JUEQING = ZHANGCHUNHUA_JUEQING
SHANGSHI = ZHANGCHUNHUA_SHANGSHI


class _Zhangchunhua:
    """应答器：可发动的技能一律按 ``answer`` 决定，其余询问走最小应答。"""

    def __init__(self, *, answer: bool = True, skill: str = SHANGSHI) -> None:
        self.answer = answer
        self.skill = skill
        self.offers: list[tuple[str, ...]] = []

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION":
            picks = tuple(
                action.skill_id
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
            self.offers.append(picks)
            if self.answer and self.skill in picks:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill) and action.skill_id == self.skill
                )
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def _zc_state(*specs: SeatSpec, turn_seat: int = ME, phase: Phase = Phase.PLAY):
    """两座局面：座位 0 = 张春华移动版（**手牌写在这里**），其余顺延。"""
    seats = [replace(specs[0], hero=ZHANGCHUNHUA) if specs else SeatSpec(hero=ZHANGCHUNHUA)]
    seats.extend(specs[1:])
    while len(seats) < 2:
        seats.append(SeatSpec())
    used = [card for spec in seats for card in (*spec.hand, *spec.equip, *spec.judge)]
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck_excluding(PACK, *used),
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(first_seat=0),
    )


def _damage_frame(*, source, target, amount, nature=None, channel=None):
    """与引擎在【杀】命中时压的 ``DAMAGE`` 子帧同形（渠道 / 属性按需给）。"""
    return {
        "source": source,
        "targets": (target,),
        "target": target,
        "card": None,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "channel": channel,
        "nature": nature,
        "chain": False,
        "amount": amount,
    }


def _run_until_idle(engine: Engine, player: _Zhangchunhua, *, limit: int = 900) -> Engine:
    """把引擎推到"没有结算栈且没有待答询问"（伤害帧 / 回复帧这类构造帧用）。"""
    for _ in range(limit):
        if not engine.stack and engine.pending is None:
            return engine
        if engine.pending is not None:
            engine.submit(player.respond(engine.pending))
            continue
        engine._step()
        if engine.finished:
            return engine
    raise AssertionError("步数上限内没有跑完构造帧")


# ---- 数据面 / 声明面 -------------------------------------------------------


def test_zhangchunhua_mobile_is_in_the_mobile_table_with_both_skills() -> None:
    hero = load_heroes("yijiang_2011_mobile").by_id(ZHANGCHUNHUA)
    assert hero is not None, "张春华移动版必须落在 heroes.yijiang_2011_mobile.json"
    assert hero.name == "张春华移动版"
    assert hero.true_name == "张春华"
    assert hero.kingdom.value == "wei" and hero.gender.value == "female"
    assert hero.hp_max == 3
    assert hero.version == "yijiang-2011-mobile"
    assert hero.enabled is True
    assert [skill.id for skill in hero.skills] == ["jueqing", "shangshi"]


def test_jueqing_is_locked_and_hooks_the_damage_window() -> None:
    """【绝情】= 锁定段，挂在「伤害结算开始前」（票 90 解封的派发点），不问「是否发动」。"""
    definition = CATALOG[JUEQING]
    assert definition.locked is True, "牌面写「锁定技」⇒ 不问"
    assert [hook.timing for hook in definition.hooks] == [Timing.DAMAGE_BEFORE_START]
    assert definition.hooks[0].forced is False, "锁定技由 ``locked`` 表达，不重复标 forced"
    assert definition.requirement == "锁定技，你即将造成的伤害均视为失去体力。"
    assert definition.preactivated is False


def test_shangshi_hooks_the_four_state_changes_with_one_zero_step_flow() -> None:
    """【伤逝】= 四条时机钩子共用**同一条**零步流水线（票面 §2 的形状）。"""
    definition = CATALOG[SHANGSHI]
    assert definition.locked is False, "「可以」⇒ 每次问一次是否发动"
    assert [hook.timing for hook in definition.hooks] == [
        Timing.MOVE_AFTER,
        Timing.LIFE_DECREASE_AFTER,
        Timing.RECOVER_AFTER,
        Timing.MAXLIFE_INCREASE_AFTER,
    ]
    assert all(hook.per_point is False for hook in definition.hooks), "逐事件，不逐点"
    assert len({id(hook.flow) for hook in definition.hooks}) == 1, "四条共用同一条流水线"
    assert definition.hooks[0].flow.steps == (), "零步：只有「是否发动」那一问"
    assert "摸至X张" in definition.requirement


# ---- 绝情 -----------------------------------------------------------------


def test_jueqing_rewrites_a_real_slash_hit_into_a_life_decrease() -> None:
    """真实使用链：【杀】命中 ⇒ **不是伤害**，是"失去 1 点体力"。

    `三国杀详细规则.txt:378–383`（§4.14 时机 0)）："触发后不再进行伤害结算，本该受到伤害的
    角色改为失去等同于伤害值基数的体力"。
    """
    state = _zc_state(SeatSpec(hand=(SLASH[0],)), SeatSpec(hp=4))
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine.start_use(state.card(SLASH[0]), source=ME, targets=(TARGET,))

    _run_until_idle(engine, player)

    assert state.player(TARGET).hp == 3, "按基数 1 点扣体力"
    assert tuple(engine.damage_events) == (), "伤害事件整段不发生 ⇒ 一条伤害事实都不许记"
    assert not ran_timing(engine, Timing.DAMAGE_AFTER_TAKE), "受到伤害后不派发"
    assert not ran_timing(engine, Timing.DAMAGE_AFTER_DEAL), "造成伤害后不派发"
    assert ran_timing(engine, Timing.DAMAGE_BEFORE_START), "改写落在时机 0)"
    assert ran_timing(engine, Timing.LIFE_DECREASE_AFTER), "换成失去体力的事件"


def test_jueqing_does_not_touch_armor() -> None:
    """失去体力**不碰护甲**（账本「护甲值 = 独立第二轴」）：2 点护甲、2 点伤害 ⇒ 掉 2 点体力。"""
    state = _zc_state(SeatSpec(hp=4, hp_max=4))
    state.player(TARGET).armor = 2
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(source=ME, target=TARGET, amount=2),
        timing=Timing.DAMAGE_DEAL,
    )

    _run_until_idle(engine, player)

    assert state.player(TARGET).armor == 2, "护甲一点不动（这不是伤害）"
    assert state.player(TARGET).hp == 2, "体力照扣基数 2 点"
    assert tuple(engine.damage_events) == ()


def test_jueqing_ignores_the_deal_amount_tier() -> None:
    """「造成伤害时 +1」（``DAMAGE_DEAL_AMOUNT``）**不算进基数**（用户 Q2）。

    载体用界徐盛移动版【破军】②（``hero.jie_xusheng_mobile.pojun`` 的第二个修饰器，
    与【古锭刀】同一个挂点）：给它一个 ``grant_skill``，目标的手牌数 / 装备数都不大于我
    ⇒ 那一段 +1 落在 ``DAMAGE_DEAL_AMOUNT`` 上；【绝情】只读基数，故目标只失去 1 点。
    112 张的标准档里没有【古锭刀】这张牌（它在军争档），而这一条判据要的只是"同一个挂点
    上有一个 +1"，用哪张牌不影响结论。
    """
    state = _zc_state(SeatSpec(hand=(SLASH[0], SLASH[1])), SeatSpec(hp=4))
    engine = Engine(state, seed=7)
    engine.grant_skill(ME, "hero.jie_xusheng_mobile.pojun")
    card = state.card(SLASH[0])
    # 先钉住"这一段 +1 真的挂在 ``DAMAGE_DEAL_AMOUNT`` 上"：否则下面的绿是假绿。
    assert damage_amount(
        state, source=ME, target=TARGET, card=card, base=1, chain=False
    ) == 2
    assert damage_base_amount(
        state, source=ME, target=TARGET, card=card, base=1, chain=False
    ) == 1
    player = _Zhangchunhua()
    engine.start_use(card, source=ME, targets=(TARGET,))

    _run_until_idle(engine, player)

    assert state.player(TARGET).hp == 3, "只失去 1 点（破军② 的那 +1 不在基数里）"


def test_jueqing_counts_the_base_amount_tier() -> None:
    """「基础伤害值 +N」**算进基数**（用户 Q2 点名的【裸衣】那一档）。"""
    state = _zc_state(SeatSpec(hand=(SLASH[0],)), SeatSpec(hp=4))
    engine = Engine(state, seed=7)
    engine.grant_skill(ME, "hero.xuchu.luoyi")
    state.player(ME).luoyi_active = True
    card = state.card(SLASH[0])
    # 反向对照：这一档进的是**基数**（``DAMAGE_BASE_AMOUNT``），不是"造成伤害时"。
    assert damage_base_amount(
        state, source=ME, target=TARGET, card=card, base=1, chain=False
    ) == 2
    player = _Zhangchunhua()
    engine.start_use(card, source=ME, targets=(TARGET,))

    _run_until_idle(engine, player)

    assert state.player(TARGET).hp == 2, "1 + 裸衣 1 = 基数 2 ⇒ 失去 2 点体力"


def test_jueqing_leaves_other_seats_damage_alone() -> None:
    """别人造成的伤害照旧是伤害（条件自校"这次伤害**我**即将造成"）。"""
    state = _zc_state(SeatSpec(hp=3, hp_max=3, hand=(SLASH[0], SLASH[1])), SeatSpec())
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(source=TARGET, target=ME, amount=1),
        timing=Timing.DAMAGE_DEAL,
    )

    _run_until_idle(engine, player)

    assert state.player(ME).hp == 2, "照常扣 1 点体力"
    assert len(engine.damage_events) == 1, "这是一次真伤害（绝情不改写别人的伤害）"


# ---- 伤逝 -----------------------------------------------------------------


def test_shangshi_draws_up_to_the_lost_hp_after_losing_a_hand_card() -> None:
    """时机 ①：失去手牌后、手牌数 0 < X = 1 ⇒ 摸至 1 张。"""
    state = _zc_state(SeatSpec(hp=2, hp_max=3, hand=(SLASH[0],)), SeatSpec(hp=4))
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine.start_use(state.card(SLASH[0]), source=ME, targets=(TARGET,))

    _run_until_idle(engine, player)

    assert any(SHANGSHI in offer for offer in player.offers), "条件成立时必须问一次"
    assert len(state.player(ME).hand) == 1, "摸至 X = 1 张"


def test_shangshi_no_offer_when_hand_reaches_x() -> None:
    """G1：失去一张后手牌数仍 ≥ X ⇒ **候选阶段就不问**（`edge-cases.md` 总纪律 1）。"""
    state = _zc_state(
        SeatSpec(hp=2, hp_max=3, hand=(SLASH[0], SLASH[1])), SeatSpec(hp=4)
    )
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine.start_use(state.card(SLASH[0]), source=ME, targets=(TARGET,))

    _run_until_idle(engine, player)

    assert player.offers == [], "手牌数 1 ≥ X = 1 ⇒ 一条询问都不该有"
    assert len(state.player(ME).hand) == 1, "没有摸牌"


def test_shangshi_triggers_after_losing_hp() -> None:
    """时机 ②：**体力真的降了**之后、手牌数 < X ⇒ 摸至 X 张。"""
    state = _zc_state(SeatSpec(hp=3, hp_max=3), SeatSpec())
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(source=TARGET, target=ME, amount=1),
        timing=Timing.DAMAGE_DEAL,
    )

    _run_until_idle(engine, player)

    assert state.player(ME).hp == 2, "先掉 1 点体力"
    assert len(state.player(ME).hand) == 1, "X 从 0 变 1 ⇒ 摸 1 张"


def test_shangshi_ignores_a_damage_frame_absorbed_by_armor() -> None:
    """用户 Q1 的口径「仅失去护甲时无影响」：护甲全吸收 ⇒ 体力没降 ⇒ **不问**。"""
    state = _zc_state(SeatSpec(hp=3, hp_max=3), SeatSpec())
    state.player(ME).armor = 1
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine.push_frame(
        FrameKind.DAMAGE,
        _damage_frame(source=TARGET, target=ME, amount=1),
        timing=Timing.DAMAGE_DEAL,
    )

    _run_until_idle(engine, player)

    assert state.player(ME).hp == 3, "体力一点没动（全被护甲吸收）"
    assert state.player(ME).armor == 0
    assert not any(SHANGSHI in offer for offer in player.offers), "这一帧不该问伤逝"


def test_shangshi_triggers_after_maxlife_increase() -> None:
    """时机 ④：加体力上限后 ⇒ X 变大 ⇒ 手牌数 < X 时摸至 X 张。"""
    state = _zc_state(SeatSpec(hp=3, hp_max=3), SeatSpec())
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine._step()  # L0 派发（GAME_START）：先让开局那一步过去
    engine.raise_hp_max(ME, 1)

    _run_until_idle(engine, player)

    assert state.player(ME).hp_max == 4, "上限 +1（不同步体力）"
    assert len(state.player(ME).hand) == 1, "X = 1 ⇒ 摸 1 张"


def test_shangshi_deck_out_draw() -> None:
    """G10：牌堆见底 ⇒ 摸牌短摸即平局（既有 ``draw_for_hand`` 语义，不另造判据）。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=2, hp_max=3, hand=(SLASH[0],), hero=ZHANGCHUNHUA), SeatSpec()],
        deck=(),
        turn_seat=ME,
        phase=Phase.PLAY,
        rule_config=RuleConfig(first_seat=0),
    )
    engine = Engine(state, seed=7)
    player = _Zhangchunhua()
    engine.start_use(state.card(SLASH[0]), source=ME, targets=(TARGET,))

    _run_until_idle(engine, player)

    assert engine.finished, "两堆皆空 + 摸不够 ⇒ 平局"
    assert len(state.player(ME).hand) == 0, "一张都摸不到"
