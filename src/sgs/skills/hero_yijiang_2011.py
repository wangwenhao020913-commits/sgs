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
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierPoint,
    PriorityClass,
    behavior_for,
    damage_base_amount,
    is_slash,
    locate,
)
from sgs.resolution import Control, Frame
from sgs.state import (
    DAMAGE_REPLACEMENT_AMOUNT_KEY,
    DAMAGE_REWRITTEN_KEY,
    LIFE_DECREASE_HP_LOSS_KEY,
    GameState,
    Phase,
    SelectionSpec,
    Zone,
)
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    RUNNING_SKILL_KEY,
    Hook,
    SkillDef,
    hero_card_zone_key,
)
from .flow import SkillFlow, zero_step_effect
from .move_after import move_event

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
"""一将成名-2011·移动版 的武将技能（票 01 = 徐盛【破军】）。"""


def xusheng_skill_definitions() -> tuple[SkillDef, ...]:
    """徐盛的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return XUSHENG_SKILL_DEFS


# ---------------------------------------------------------------------------
# 界徐盛移动版【破军】（票 03；同一份代码、两个版本）
#
# 移动版原文（`D:\SGS_HEROS\界一将成名-2011\界徐盛\技能.txt` 逐字）：
#
#     ①当你使用【杀】指定一名角色为目标后，你可以将该角色的至多X张牌置于其武将牌上
#       （X为该角色的体力值），然后当前回合结束时，其获得武将牌上的牌；
#     ②当你使用【杀】对手牌数与装备区里的牌数均不大于你的目标角色造成伤害时，此伤害+1。
#
# 与徐盛版的两处差异（**A4 的显式区分**，两条短 id 相同、文本不同）：
# - **没有「出牌阶段，」前缀** ⇒ 回合外被【借刀杀人】逼出的【杀】也触发（`_jie_pojun_condition`）；
# - **多出 ②** ⇒ 来源侧 ``DAMAGE_DEAL_AMOUNT`` 修饰器（"造成伤害时 +1"，账本 L788 与
#   界【烈弓】② 同一挂点；按用户 2026-11-20 的口径，它**不计入**【绝情】的金额）。
# ---------------------------------------------------------------------------

JIE_XUSHENG = "jie_xusheng_mobile"
"""界徐盛的武将 id（界一将成名-2011·移动版）。"""

JIE_XUSHENG_POJUN = hero_skill_id(JIE_XUSHENG, "pojun")
"""界徐盛【破军】的全限定技能 id。"""


def _jie_pojun_bonus(state: GameState, ctx: Any, current: Any) -> Any:
    """② ``DAMAGE_DEAL_AMOUNT``：**我**用【杀】造成伤害时，若目标"手牌数与装备区里的牌数
    均不大于我"，此伤害 +1。

    来源侧（``ctx.owner == ctx.subject``）+ 渠道牌是【杀】族（转化 / 虚拟同族，判据 =
    :func:`~sgs.effects.is_slash`）+ 两个张数比较**在伤害结算时现算**（不是选目标时定格）。
    传导帧不派发本挂点（``damage_amount`` 只对 ``chain=False`` 的源头帧派发）⇒ 连环传导
    不会重复 +1。
    """
    if ctx.owner != ctx.subject or ctx.card is None or ctx.target is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    mine, them = state.player(ctx.owner), state.player(int(ctx.target))
    if len(mine.hand) < len(them.hand) or len(mine.equip) < len(them.equip):
        return current
    return current + 1


_JIE_POJUN_BODY, _JIE_POJUN_BACK = _pojun_definitions(
    JIE_XUSHENG_POJUN, _jie_pojun_condition, _JIE_XUSHENG_REQUIREMENT
)
"""界徐盛【破军】① 的两份定义（本体 + 归还段；与徐盛版同一份代码）。"""

_JIE_XUSHENG_POJUN_DEFS: tuple[SkillDef, ...] = (
    # ② 挂在**本体**上（不是归还段）：它是"造成伤害时"的来源侧数值，与「扣牌」无关。
    # 声明仍是同一条技能：一个 id、两半效果（① 的钩子 + ② 的修饰器）。
    replace(
        _JIE_POJUN_BODY,
        modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _jie_pojun_bonus),),
    ),
    _JIE_POJUN_BACK,
)
"""界徐盛【破军】①②（票 03）。"""

JIE_XUSHENG_SKILL_DEFS: tuple[SkillDef, ...] = _JIE_XUSHENG_POJUN_DEFS
"""界一将成名-2011·移动版 的武将技能（票 03 = 界徐盛【破军】①②）。"""


def jie_xusheng_skill_definitions() -> tuple[SkillDef, ...]:
    """界徐盛的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return JIE_XUSHENG_SKILL_DEFS


# ---------------------------------------------------------------------------
# 张春华移动版【绝情】【伤逝】（票 02；用户 2026-11-20 逐条裁定，见票面 §0 Q1–Q5）
#
# 移动版原文（`D:\SGS_HEROS\一将成名-2011\张春华\技能.txt` 逐字）：
#
#     绝情／锁定技，你即将造成的伤害均视为失去体力。
#     伤逝／当你的手牌数小于X时，你可以将手牌摸至X张（X为你已损失的体力值）。
#
# 两条技能的落点：
#
# - **绝情** = 锁定段，挂 ``DAMAGE_BEFORE_START``（规则 §4.14 的时机 0)，票 90 解封的派发点）。
#   改写走引擎的**通用协议**（帧私有 ``damage_rewritten`` + ``damage_replacement_amount``），
#   本模块只负责把"金额"算出来并写进去 —— 引擎不认识技能名（C2）。
#   金额 = **伤害值基数**（票 90 的 ``damage_base_amount``：牌面 + 【酒】+ 「基础伤害值 +N」
#   那一档），**不含**"造成伤害时 +N"（【古锭刀】/界徐盛【破军】②）与"受到伤害时 +N"（【藤甲】）。
# - **伤逝** = 多时机零步流水线（样板 ``hero.sunshangxiang.xiaoji``）：一条 ``SkillDef`` 挂
#   **四条** ``Hook``（失去手牌后 / 扣减体力后 / 回复体力后 / 加体力上限后），四条共用同一份
#   条件与同一份效果。可选（「可以」⇒ 有放弃）、X = 已损失体力值现算、效果是"摸至 X 张"。
#   **受致命伤害时先问伤逝、再进濒死**：官方规则集的【伤逝】例逐字写"在扣減3點體力後能發動
#   〖傷逝〗摸兩張牌，**然後進入瀕死狀態**" ⇒ `steps.LIFE_DECREASE_STEPS` 把濒死压帧排在
#   「扣减体力后」之后（票 02 的修正；"濒死优先"只管"造成 / 受到伤害后"那三条）。
# ---------------------------------------------------------------------------

ZHANGCHUNHUA = "zhangchunhua_mobile"
"""张春华的武将 id（一将成名-2011·移动版）。"""

ZHANGCHUNHUA_JUEQING = hero_skill_id(ZHANGCHUNHUA, "jueqing")
"""张春华【绝情】的全限定技能 id。"""

ZHANGCHUNHUA_SHANGSHI = hero_skill_id(ZHANGCHUNHUA, "shangshi")
"""张春华【伤逝】的全限定技能 id。"""

JUEQING_REQUIREMENT = "锁定技，你即将造成的伤害均视为失去体力。"
"""【绝情】的牌面逐字（移动版；``requirement`` 槽位）。"""

SHANGSHI_REQUIREMENT = (
    "当你的手牌数小于X时，你可以将手牌摸至X张（X为你已损失的体力值）。"
)
"""[伤逝] 的牌面逐字（移动版；``requirement`` 槽位）。

单选项确认态读不到 `OPTION_LABELS`，提示条 / 预发动清单读的就是这一句
（`docs/agents/skill-authoring/shapes.md` §4 的「一句话文案」行 + `edge-cases.md` O4）。
"""


def _jueqing_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**这次伤害是我（张春华）即将造成的**。

    钩子的"关于我"自校读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环
    写），再看帧上的 ``source`` ——伤害事件在派发时**还没有"造成伤害时"那一步**，``source``
    就是伤害来源（票 03 的传导帧也只在这里写着原始来源）。

    无来源的伤害（【闪电】一类）``source is None`` ⇒ 不成立；别人造成的伤害 ⇒ 不成立
    （`edge-cases.md` H2：不自校 = 谁挨打都改写）。
    """
    del host
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    return frame.data.get("source") == int(owner)


def _jueqing_rewrite(host: Any, frame: Frame) -> None:
    """把这一帧的伤害事件**整段改写成"失去体力"**，金额取伤害值基数。

    写入的两个帧私有槽由 :func:`~sgs.engine.steps.damage_before_start` 收口：它跳过本帧
    其余步骤（不派发"造成 / 受到伤害"的后置时机、不 `note_damage_*`、不参与连环传导），
    再压一条**无来源**的 ``LIFE_DECREASE`` 帧。事件不存在 ⇒ 它**不是**"防止伤害"
    （用户 Q1；规则 §4.14 L396–L401 的判据）。

    金额 = :func:`~sgs.effects.damage_base_amount`：牌面基数 + 使用级加伤（【酒】）+
    ``DAMAGE_BASE_AMOUNT`` 链（"基础伤害值 +N"那一档）。用户 Q2 的四个举例都落在这条式子上：
    火杀穿藤甲 → 1；普通杀穿藤甲 → 无事（藤甲先令普通【杀】无效，事件都没发生）；酒杀 → 2；
    【古锭刀】不参与（"造成伤害时 +N"在后面的那一档）。
    """
    frame.data[DAMAGE_REWRITTEN_KEY] = True
    frame.data[DAMAGE_REPLACEMENT_AMOUNT_KEY] = damage_base_amount(
        host.state,
        source=frame.data.get("source"),
        target=int(frame.data["target"]),
        card=frame.data.get("channel"),
        base=int(frame.data.get("amount", 0)),
        chain=bool(frame.data.get("chain")),
        deal_bonus=int(frame.data.get("deal_bonus", 0)),
    )
    return None


def _shangshi_x(state: GameState, seat: int) -> int:
    """X = **已损失的体力值** = 体力上限 − 体力值（牌面括注；每次取值按当下重算）。

    **体力值可能小于 0**（`apply_hp` 的下限不夹：官方明说"角色的体力有可能小于 0"），
    而 X 最多是**体力上限**——《三國殺官方規則集 2018.3-16》的【伤逝】例逐字写着：

        體力為2手牌數為1的張春華受到管道為【閃電】的3點雷電傷害，在扣減3點體力後能發動
        〖傷逝〗**摸兩張牌**，然後進入瀕死狀態……

    −1 体力若按"上限 − 体力"算就是 4（⇒ 摸 3 张），与例文的"摸两张"对不上；按这里
    的读法 X = 3 − max(−1, 0) = 3、手牌 1 ⇒ 摸 2 张，逐字吻合。用户 2026-11-20 的报障
    （"此时已损失体力为自身体力上限（3）"）也是这条。
    """
    player = state.player(seat)
    lost = int(player.hp_max) - max(0, int(player.hp))
    return max(0, lost)


def _shangshi_needs_cards(state: GameState, seat: int) -> bool:
    """「手牌数小于 X」——四条时机共用的**候选判据**（不成立就连"是否发动"都不问）。

    `edge-cases.md` G1 / 总纪律 1：付得起才产出候选。成立时 ``X − 手牌数 ≥ 1``，
    故"摸 0 张"不可能（G2）。
    """
    return len(state.player(seat).hand) < _shangshi_x(state, seat)


def _lost_hand_cards(frame: Frame, seat: int) -> bool:
    """这次移动让 ``seat`` 的**手牌**变少了吗（装备区 / 判定区不算）。

    与 :func:`~sgs.skills.move_after.loses_card` 的分工：那一条把"离开装备区"也算失去
    （【枭姬】要的语义），而【伤逝】的判据是**手牌数**——X 一直是"已损失体力值"，装备区
    那张牌离开了也不改变手牌数。故本函数只认 ``from_zone == (HAND, seat)`` 且去向**不是**
    自己的手牌区（重排 / 整理一类没有离开，账本「交给别人的手牌也算失去」）。
    """
    move = move_event(frame)
    if move is None:
        return False
    if move.from_zone.zone is not Zone.HAND or move.from_zone.seat != seat:
        return False
    return not (move.to_zone.zone is Zone.HAND and move.to_zone.seat == seat)


def _shangshi_move_condition(host: Any, frame: Frame) -> bool:
    """时机 ①（``MOVE_AFTER``）：**失去手牌后**且手牌数小于 X。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    seat = int(owner)
    return _lost_hand_cards(frame, seat) and _shangshi_needs_cards(host.state, seat)


def _shangshi_hp_condition(host: Any, frame: Frame) -> bool:
    """时机 ②（``LIFE_DECREASE_AFTER``）：**体力真的降低了**之后，且手牌数小于 X。

    两个判据各管一件事：

    - ``target == 我``：这一帧扣的是**我的**体力（伤害与"失去体力"都走这条，用户 Q3）；
    - 帧私有 :data:`~sgs.state.LIFE_DECREASE_HP_LOSS_KEY` > 0：**体力真的降了**。
      护甲全吸收的伤害帧 ``hp_loss == 0`` ⇒ **不触发**（用户 Q1 口径"仅失去护甲时无影响"）。
      不能只看 ``amount``：那一帧的 ``amount`` 是全额伤害，体力一点没动。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    seat = int(owner)
    if int(frame.data.get("target", -1)) != seat:
        return False
    if int(frame.data.get(LIFE_DECREASE_HP_LOSS_KEY, 0)) <= 0:
        return False
    return _shangshi_needs_cards(host.state, seat)


def _shangshi_recover_condition(host: Any, frame: Frame) -> bool:
    """时机 ③（``RECOVER_AFTER``）：**回复体力后**，且手牌数小于 X。

    回复让 X 变小，故"回复后仍小于"是常态（低血、少牌的座位）；满体力一帧的
    ``X == 0`` ⇒ 天然不成立（``__shangshi_needs_cards`` 是同一道门，不另写一份判据）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    seat = int(owner)
    if int(frame.data.get("target", -1)) != seat:
        return False
    return _shangshi_needs_cards(host.state, seat)


def _shangshi_maxlife_condition(host: Any, frame: Frame) -> bool:
    """时机 ④（``MAXLIFE_INCREASE_AFTER``）：**加体力上限后**，且手牌数小于 X。

    钩子只挂在"加"侧（`MAXLIFE_INCREASE_AFTER`）；"减上限后"引擎不派发且**不可观测**
    （X 只会变小，见用户 Q3 的括注）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    seat = int(owner)
    if int(frame.data.get("target", -1)) != seat:
        return False
    return _shangshi_needs_cards(host.state, seat)


_SHANGSHI_DONE = "shangshi:done:"
"""帧私有前缀：这一次触发**已经摸过**（摸牌可能内联派发"获得牌后"并挂起）。

按"这次触发是谁"分桶（``edge-cases.md`` G7）：``MOVE_AFTER`` 同一帧可以有两次独立移动，
故键里带**这批牌**的 id；其余三个时机在帧内只出现一次，键退化成常量。
"""


def _shangshi_trigger_key(frame: Frame) -> str:
    """这一次触发的帧内游标键（同一帧的两次"失去手牌"互不干扰）。"""
    move = move_event(frame)
    cards = (
        ",".join(str(int(card.card_id)) for card in move.cards) if move is not None else ""
    )
    return f"{_SHANGSHI_DONE}{cards}"


def _shangshi_draw(host: Any, frame: Frame) -> Control:
    """效果：**摸至 X 张** = 摸 ``X − 手牌数`` 张（不铺面：``draw_for_hand``）。

    「可以」的放弃口由派发环给（非锁定技、不问 ``locked``）：玩家答放弃就到此为止，
    ``edge-cases.md`` 总纪律 2 的"可以不选 ⇒ 给 Pass"由引擎统一给。短摸 ⇒ 平局
    （既有 ``draw_for_hand`` 语义，G10）。

    **游标在摸牌之前前进**：摸牌会内联派发"获得牌后"（【清俭】一类）并挂起，续跑据它
    既不重摸、也不漏摸——与 :func:`~sgs.skills.move_after.MoveAfterSkill.handle` 同一姿态。
    """
    owner = frame.data.get("skill_owner") or frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return None
    seat = int(owner)
    key = _shangshi_trigger_key(frame)
    if frame.data.get(key):
        return None
    player = host.state.player(seat)
    need = _shangshi_x(host.state, seat) - len(player.hand)
    if need <= 0:
        return None
    frame.data[key] = True
    if host.draw_for_hand(frame, seat, need) == "stay":
        return "stay"
    return None


SHANGSHI_FLOW = SkillFlow(name="shangshi", steps=(), effect=zero_step_effect(_shangshi_draw))
"""【伤逝】的零步流水线：「是否发动」→ 摸至 X 张（无步骤；样板 = 【枭姬】）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例。
"""

_JUEQING_DEF = SkillDef(
    # 锁定技（牌面写"锁定技"）⇒ ``locked=True``：不问"是否发动"，派发环直接跑回调。
    skill_id=ZHANGCHUNHUA_JUEQING,
    locked=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(
            Timing.DAMAGE_BEFORE_START,
            handler=_jueqing_rewrite,
            condition=_jueqing_condition,
        ),
    ),
    requirement=JUEQING_REQUIREMENT,
)

_SHANGSHI_DEF = SkillDef(
    # 非锁定（牌面写"可以"）⇒ 每个时机各问一次"是否发动"（`SKILL_ACTIVATION`）。
    skill_id=ZHANGCHUNHUA_SHANGSHI,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(
            Timing.MOVE_AFTER,
            condition=_shangshi_move_condition,
            flow=SHANGSHI_FLOW,
        ),
        # 逐事件（不是逐点）：``per_point`` 保持缺省 False——一次失去 2 点 / 一次扣 2 点伤害
        # 都只问一次（账本默认读法（三）：牌面没有「1 点」这个限定词）。
        Hook(
            Timing.LIFE_DECREASE_AFTER,
            condition=_shangshi_hp_condition,
            flow=SHANGSHI_FLOW,
        ),
        Hook(
            Timing.RECOVER_AFTER,
            condition=_shangshi_recover_condition,
            flow=SHANGSHI_FLOW,
        ),
        Hook(
            Timing.MAXLIFE_INCREASE_AFTER,
            condition=_shangshi_maxlife_condition,
            flow=SHANGSHI_FLOW,
        ),
    ),
    requirement=SHANGSHI_REQUIREMENT,
)

ZHANGCHUNHUA_SKILL_DEFS: tuple[SkillDef, ...] = (_JUEQING_DEF, _SHANGSHI_DEF)
"""张春华【绝情】【伤逝】（票 02）。"""


def zhangchunhua_skill_definitions() -> tuple[SkillDef, ...]:
    """张春华的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return ZHANGCHUNHUA_SKILL_DEFS


YIJIANG_2011_SKILL_DEFS: tuple[SkillDef, ...] = (
    *_XUSHENG_POJUN_DEFS,
    *_JIE_XUSHENG_POJUN_DEFS,
    *ZHANGCHUNHUA_SKILL_DEFS,
)
"""一将成名-2011·移动版（徐盛【破军】，票 01）+ 界一将成名-2011·移动版（界徐盛【破军】①②，
票 03）+ 张春华【绝情】【伤逝】（票 02）。"""


def yijiang_2011_skill_definitions() -> tuple[SkillDef, ...]:
    """一将成名-2011·移动版 的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return YIJIANG_2011_SKILL_DEFS
