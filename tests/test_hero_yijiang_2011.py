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
from sgs.engine import Engine
from sgs.skills import CATALOG
from sgs.skills.catalog import hero_card_zone_key
from sgs.skills.hero_yijiang_2011 import (
    POJUN_MARK,
    XUSHENG_POJUN,
    _pojun_condition,
    _pojun_return_condition_for,
)
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    Phase,
    PlayCard,
    SelectCards,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing
from support import SeatSpec, build_state, cards_of_type, deck_excluding

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
