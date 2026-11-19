r"""一将成名-2011·**移动版** 的武将技能：徐盛【破军】（yijiang-2011-xusheng-chunhua 票 01）。

牌面权威 = **移动版**（用户 2026-11-20 签：本批的徐盛 / 界徐盛取移动版文本；
``D:\SGS_HEROS\一将成名-2011\徐盛\技能.txt`` 逐字）：

    破军：出牌阶段，当你使用【杀】指定一名角色为目标后，你可以将该角色的至多X张牌
        置于其武将牌上（X为该角色的体力值），然后当前回合结束时，其获得武将牌上的牌。

界徐盛（``hero.jie_xusheng_mobile.pojun``，票 03 落地）是**同一条技能的另一个版本**：去掉
「出牌阶段，」前缀（⇒ 回合外被【借刀杀人】逼出的【杀】也触发）、并多出 ②
「…造成伤害时，此伤害+1」。两版共用本模块的同一份代码（:func:`_pojun_definition` 的两次调用）。

**三个落地要点**（票 91 的框架件 + 本票的三处读法）：

1. **牌置于"其"武将牌上** ⇒ ``Engine.place_on_hero_card(owner=目标, skill_id=破军,
   holder=目标)``。``owner`` 在这里是"这批牌归谁、回合末还给谁"——破军的收件人是**被扣牌
   的那个目标本人**（不是徐盛），故 ``owner == holder == 目标``；区键因此带**目标**座位，
   ``Engine._drop_hero_zones_of_lost_skill`` 扫不到它 ⇒ **徐盛失去【破军】不影响这个区**
   （用户 2026-11-20 口径）。
2. **「当前回合结束时，其获得」= 一段与目标自己的回合末技能并列的强制段** ⇒ 标记自身效果
   （``Marker.effect``，不受技能失效影响）+ ``SkillDef.dispatch_tier = PriorityClass.HERO``
   （票 91 的加法）：它与该座位自己的 ``TURN_END`` 段进**同一张"选先后"面**。标记没了
   （来源失去技能 / 阵亡）也不丢牌：数据声明的 ``return_timing = TURN_END`` 让引擎在
   ``_turn_after_end`` 整批兜底归还（收件人仍是 ``zone.owner`` = 目标）。
3. **「至多X张」**：X = **该目标**当前体力值（不是徐盛的），下限 1、可放弃（账本
   「至多 = 上限 + 可放弃」）；选牌面 = 该角色的**手牌 + 装备区**（账本 L1008 默认读法，
   不含判定区）。

**为什么手写选牌而不是步骤原语**：``PickCards.hi`` 是常量（``int | None``），而这里的上限
是"目标体力值 ∧ 它的牌数"——动态上限只能走手写询问（与【定乱】/【雌雄双股剑】同一姿态，
``SelectionSpec(total=(1, hi), optional=True)``，``optional`` 正是「至多」那条账本给的放弃口）。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, PriorityClass, behavior_for, is_slash, locate
from sgs.resolution import Control, Frame
from sgs.state import GameState, Phase, SelectionSpec, Zone
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    RUNNING_SKILL_KEY,
    Hook,
    SkillDef,
    hero_card_zone_key,
)
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "POJUN_FLOW",
    "POJUN_MARK",
    "XUSHENG",
    "XUSHENG_POJUN",
    "xusheng_skill_definitions",
]

XUSHENG = "xusheng_mobile"
"""徐盛的武将 id（一将成名-2011·移动版）。"""

XUSHENG_POJUN = hero_skill_id(XUSHENG, "pojun")
"""徐盛【破军】的全限定技能 id。"""

POJUN_MARK = "破军"
"""【破军】标记名（扣在**被扣牌的目标**身上；同一目标可同时有徐盛 / 界徐盛两枚）。"""

_ASK_POJUN_PICKED = "pojun:card"
"""【破军】选牌步的**询问位置**（帧内缓存按 ``(owner, key)`` 分桶；目标只带在 ``key`` 上）。"""

_POJUN_CURSOR = "pojun_cursor"
"""帧私有：多目标【杀】里已经处理到第几个目标（重入不重走前面的目标）。"""

_POJUN_PLACED = "pojun:placed:"
"""帧私有前缀：这个目标已经问过（放弃 / 已扣都算；搬牌挂起重入不得再问一次）。"""

_POJUN_GROUPS = "pojun:groups:"
"""帧私有前缀：这个目标**还没搬**的那些（来源区, 牌）批（一次 ``card_move`` 只能一个来源区）。"""

_XUSHENG_REQUIREMENT = (
    "出牌阶段，当你使用【杀】指定一名角色为目标后，你可以将该角色的至多X张牌"
    "置于其武将牌上（X为该角色的体力值），然后当前回合结束时，其获得武将牌上的牌。"
)
"""徐盛【破军】的牌面逐字（移动版；``requirement`` 槽位）。"""

_JIE_XUSHENG_REQUIREMENT = (
    "当你使用【杀】指定一名角色为目标后，你可以将该角色的至多X张牌置于其武将牌上"
    "（X为其体力值），然后当前回合结束时，其获得武将牌上的牌；"
    "当你使用【杀】对手牌数与装备区里的牌数均不大于你的目标角色造成伤害时，此伤害+1。"
)
"""界徐盛【破军】的牌面逐字（移动版；① 去掉「出牌阶段，」、② 是同一条技能的第二句）。"""


def _return_skill_id(skill_id: str) -> str:
    """一条【破军】的**归还段**（标记自身效果定义）id。"""
    return f"{skill_id}_return"


def _targets_with_cards(state: GameState, targets: tuple[int, ...]) -> tuple[int, ...]:
    """本次【杀】的目标里**手牌 + 装备区有牌**的那些（判定区不算，账本 L1008）。

    没有牌的目标连"是否发动"都不该问（`edge-cases.md` G1 / 总纪律 1）。
    """
    return tuple(
        target
        for target in targets
        if state.player(target).hand or state.player(target).equip
    )


def _group_by_source_zone(
    state: GameState, card_ids: tuple[int, ...]
) -> tuple[tuple[Zone, tuple[int, ...]], ...]:
    """把选中的牌按**来源区**分组（手牌在前、装备区在后，固定次序）。

    ``move_card`` 的合同是"一次移动只有一个来源区"，而 ``PickCards``/``ask_selection`` 的
    选择面可以同时铺手牌与装备区 ⇒ 跨区的一次选牌必须拆成两次搬。
    """
    buckets: dict[Zone, list[int]] = {}
    for card_id in card_ids:
        ref = locate(state, card_id)
        buckets.setdefault(ref.zone, []).append(int(card_id))
    return tuple(
        (zone, tuple(buckets[zone]))
        for zone in (Zone.HAND, Zone.EQUIP)
        if buckets.get(zone)
    )


def _pojun_trigger(host: Any, frame: Frame, *, phase_restricted: bool) -> bool:
    """【破军】的触发判据：**自己**使用的【杀】、至少一个目标有牌、（徐盛版）还在自己的出牌阶段。

    「出牌阶段，」写在牌面里 ⇒ 徐盛版要在 ``state.phase is Phase.PLAY`` ∧ 回合角色是自己；
    界徐盛版没有这个前缀（A4 的显式区分）⇒ 回合外被【借刀杀人】逼出的【杀】照样触发。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    state = host.state
    if phase_restricted and (
        state.phase is not Phase.PLAY or state.turn_seat != int(owner)
    ):
        return False
    card = frame.data.get("card")
    if card is None or not is_slash(behavior_for(card)):
        return False
    return bool(_targets_with_cards(state, tuple(frame.data.get("targets", ()))))


def _pojun_condition(host: Any, frame: Frame) -> bool:
    """徐盛版的条件（**含**出牌阶段限定）。"""
    return _pojun_trigger(host, frame, phase_restricted=True)


def _jie_pojun_condition(host: Any, frame: Frame) -> bool:
    """界徐盛版的条件（**不含**阶段限定，见 A4 的显式区分）。"""
    return _pojun_trigger(host, frame, phase_restricted=False)


def _pojun_effect(host: Any, frame: Frame) -> Control:
    """效果：逐个目标问一次"扣哪几张"（1..X，可放弃），扣到它的武将牌上并挂标记。

    手写循环（多目标【杀】= 方天画戟一类）：游标住 :data:`_POJUN_CURSOR`，每个目标
    "问过了"住 :data:`_POJUN_PLACED`——搬牌会内联派发"失去牌后"（【连营】/【枭姬】一类会
    提问）并挂起，重入时既不重问也不重扣（`edge-cases.md` G7）。

    X = **该目标**当前体力值（牌面括注），上限再与它的牌数取小；``optional=True`` =
    账本「至多 = 上限 + 可放弃」的放弃口 ⇒ 放弃这个目标、继续下一个。
    """
    state = host.state
    owner = int(frame.data["skill_owner"])
    skill_id = str(frame.data[RUNNING_SKILL_KEY])
    targets = tuple(frame.data.get("targets", ()))
    cursor = int(frame.data.get(_POJUN_CURSOR, 0))
    while cursor < len(targets):
        target = int(targets[cursor])
        frame.data[_POJUN_CURSOR] = cursor
        placed_key = f"{_POJUN_PLACED}{target}"
        if frame.data.get(placed_key):
            cursor += 1
            continue
        player = state.player(target)
        pool = len(player.hand) + len(player.equip)
        if pool <= 0:
            frame.data[placed_key] = True
            cursor += 1
            continue
        hi = min(max(int(player.hp), 1), pool)
        picked = host.ask_selection(
            frame,
            owner=_ASK_POJUN_PICKED,
            key=f"pojun:card:{target}",
            actor=owner,
            timing=Timing.USE_AFTER_DESIGNATE_TARGET,
            spec=SelectionSpec(
                zones=(Zone.HAND, Zone.EQUIP), total=(1, hi), optional=True
            ),
            # 面只构造一次（引擎按 (owner, key) 缓存）：隐藏手牌的位置打乱消耗一次
            # ``select`` 子流，重入绝不能再跑一遍。
            build_face=lambda target=target: host.selection_face(
                target, (Zone.HAND, Zone.EQUIP), reveal_hand=(target == owner)
            ),
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            # 「至多」的放弃口：这个目标不扣，继续下一个。
            frame.data[placed_key] = True
            cursor += 1
            continue
        # **一次 move_card 只能有一个来源区**（规则 §「牌的移动」）：手牌 + 装备区各一批，
        # 分批搬。进度住帧私有：搬牌会内联派发"失去牌后"并挂起，续跑时不重搬已完成的那批。
        groups_key = f"{_POJUN_GROUPS}{target}"
        groups = frame.data.get(groups_key)
        if groups is None:
            groups = _group_by_source_zone(state, tuple(picked))
            frame.data[groups_key] = groups
        while groups:
            _, card_ids = groups[0]
            control = host.place_on_hero_card(
                frame,
                owner=target,
                skill_id=skill_id,
                holder=target,
                cards=state.refs(card_ids),
            )
            if control == "stay":
                return "stay"
            groups = tuple(groups[1:])
            frame.data[groups_key] = groups
        frame.data[placed_key] = True
        # 标记 = "这批牌要在这个回合结束时还给他"的独立事实（用户 2026-11-20 口径）。
        host.add_marker(
            frame,
            target,
            name=POJUN_MARK,
            source_seat=owner,
            source_skill_id=skill_id,
            effect=_return_skill_id(skill_id),
        )
        cursor += 1
    return None


POJUN_FLOW = SkillFlow(name="pojun", steps=(), effect=zero_step_effect(_pojun_effect))
"""【破军】的零步流水线：[是否发动] → 逐目标扣牌（无步骤；选牌住效果里，见模块头）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例。
"""


def _pojun_return_condition_for(skill_id: str) -> Any:
    """归还段的触发条件（按技能 id 闭包）：**我**武将牌上有这条技能的待还牌。

    ``owner`` 读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环写）。
    读的是**区**而不是标记：区在、牌就在，与"标记是否还在"解耦——标记被清理掉时引擎的
    整批兜底（``_turn_after_end``）照样把牌还给 ``zone.owner``。

    **不读** ``RUNNING_SKILL_KEY``：条件在候选收集期求值，那时帧上还没有这个键（它由
    派发环在**跑**之前写）。技能 id 只有两份（徐盛 / 界徐盛），各自闭包捕获自己的那一个。
    """

    def condition(host: Any, frame: Frame) -> bool:
        owner = frame.data.get(MATCH_OWNER_KEY)
        if owner is None:
            return False
        zone = hero_card_zone_key(int(owner), skill_id)
        return bool(host.state.hero_zone(int(owner), zone))

    return condition


def _pojun_return_for(skill_id: str) -> Any:
    """归还段的强制回调（按技能 id 闭包）：只还**我这一个**区，然后移去标记。

    一次同时机的段只管自己那个区（``return_hero_card_zones(holder=…, skill_id=…)`` 的
    两个过滤器，票 91）——**别人**武将牌上的破军牌不归这一段还，否则"先结哪一条"的
    顺序面就没有意义。搬牌走帧外移动，故照常派发"获得牌后"一类钩子、可能挂起（G7）：
    已还完的区是空区，重入天然跳过；标记在**还完之后**才移去。
    """

    def handler(host: Any, frame: Frame) -> Control:
        holder = int(frame.data["skill_owner"])
        if host.return_hero_card_zones(holder=holder, skill_id=skill_id) == "stay":
            return "stay"
        for marker in tuple(host.state.player(holder).markers):
            if marker.name == POJUN_MARK and marker.source_skill_id == skill_id:
                host.remove_marker(
                    frame,
                    holder,
                    name=marker.name,
                    source_seat=marker.source_seat,
                    source_skill_id=marker.source_skill_id,
                )
        return None

    return handler


def _pojun_definitions(
    skill_id: str, condition: Any, requirement: str
) -> tuple[SkillDef, SkillDef]:
    """一条【破军】的两份定义：本体 + **归还段**（``carrier = MARKER``，声明成武将档）。

    归还段声明 :attr:`SkillDef.dispatch_tier` = ``HERO``（票 91）：它与被破军角色自己的
    ``TURN_END`` 强制段进**同一张"选先后"面**——牌面把"当前回合结束时其获得"写在这个
    座位上，故结算顺序归该座位玩家（`06`（§5「程序化约定与偏离一手」）的同一时机裁定 + 框架票 95）。
    """
    body = SkillDef(
        skill_id=skill_id,
        locked=False,
        carrier=CarrierKind.HERO,
        skill_type=SkillType.NORMAL,
        hooks=(
            Hook(
                Timing.USE_AFTER_DESIGNATE_TARGET,
                condition=condition,
                flow=POJUN_FLOW,
            ),
        ),
        requirement=requirement,
        # 选牌步的文案：``SELECT_CARDS`` 恒读键 ``card``（`shapes.md` §4 的逐步文案一行）。
        step_requirements={"card": "选择其至多X张牌置于其武将牌上"},
        places_on_hero_card=True,
    )
    back = SkillDef(
        skill_id=_return_skill_id(skill_id),
        locked=False,
        carrier=CarrierKind.MARKER,
        skill_type=SkillType.NORMAL,
        dispatch_tier=PriorityClass.HERO,
        hooks=(
            Hook(
                Timing.TURN_END,
                handler=_pojun_return_for(skill_id),
                condition=_pojun_return_condition_for(skill_id),
                forced=True,
            ),
        ),
        requirement="当前回合结束时，其获得其武将牌上以此法放置的牌",
    )
    return body, back


_XUSHENG_POJUN_DEFS: tuple[SkillDef, ...] = _pojun_definitions(
    XUSHENG_POJUN, _pojun_condition, _XUSHENG_REQUIREMENT
)
"""徐盛【破军】+ 它的归还段（票 01）。"""

XUSHENG_SKILL_DEFS: tuple[SkillDef, ...] = _XUSHENG_POJUN_DEFS
"""一将成名-2011·移动版 的武将技能（逐票追加；票 01 只有徐盛【破军】）。"""


def xusheng_skill_definitions() -> tuple[SkillDef, ...]:
    """徐盛的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return XUSHENG_SKILL_DEFS


YIJIANG_2011_SKILL_DEFS: tuple[SkillDef, ...] = _XUSHENG_POJUN_DEFS
"""一将成名-2011·移动版 的全部技能定义（徐盛【破军】；张春华由票 02 追加）。"""


def yijiang_2011_skill_definitions() -> tuple[SkillDef, ...]:
    """一将成名-2011·移动版 的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return YIJIANG_2011_SKILL_DEFS
