r"""出牌阶段**主动技**（A 族）的候选与发动（票 04；W6 收口）。

A 族与其他族的形态差别只有一处：它的候选**不是某张牌的**使用 / 打出，而是**技能本身**
作为出牌阶段空闲时间点的一个行动（票 04 §要做什么 1）。W4-1 起主动技都已是
**骨架候选 + 步骤流水线**，候选与载荷只有一份声明：

- **候选**：每条技能一个 ``Modifier(ModifierPoint.CANDIDATES, …)``，只在
  ``CandidateScope.PLAY`` 的**整段追加**里生效（与 H 族的虚拟牌同一姿态）。候选恒为
  一条 ``ActivateSkill(skill_id=…)`` **骨架**（``picks == ()``、``choices == ()``）：
  给哪些牌 / 选哪名角色由该技能的 :class:`SkillFlow` 在**发动之后**逐步询问
  （``PickCards`` / ``PickSeats`` / ``PickOption`` …），候选数因此与手牌数 / 目标数无关。
  **付不起 / 没有合法目标 ⇒ 一个候选都不造**（`available` 判据在派发前挡掉）。
- **清单 / 预发动**：六条都 ``preactivated=True``（W4-2）——出牌阶段可用性只由
  ``PlayPhaseQuery.preactivations`` 的清单表达，骨架不再进 ``legal_actions``；
  确认 = 一个**复合动作**（W4-1），校验器与步骤声明同源。
- **发动**：引擎收到 ``ActivateSkill`` 后压一个 ``FrameKind.SKILL`` 帧，步骤表按
  ``skill_id`` 查 :data:`ACTIVATIONS` 通用派发（:func:`activate_skill`）。回调签名与
  ``OPS`` 相同（``(host, frame) -> Control``），故提问让出控制权、压子帧都能自然沿用
  既有的帧内游标范式（票 19 §8）。
- **每阶段限一次**：per-instance ``state.skill_state`` 的**位标记**（:data:`_USED`），
  由 :func:`_activation_handler` 在**流水线提交点**现算并落位（不在装配处烘死）；
  复位走 :func:`sgs.skills.reset_phase_skill_state`（引擎在 ``PHASE_PLAY_START`` 调用）。
  【仁德】是例外：规则 976 没有"出牌阶段限一次"，
  ``RENDE_FLOW.once_per_phase=False`` ⇒ 一个 `_USED` 位都不落。

**候选上界**：``CANDIDATE_CAP`` 仍是 fuzz 的候选数上界（``tests/test_fuzz.py``）；
旧的两维枚举 / 顺序多轮付成本路径（按 design.md §7 Q4 曾"先留、之后再清"）已在
W6-06a 删除，本族不再有降级分支。

**苦肉**（M6 票 03 起真登记）：§九 L1031 的标准版没有"出牌阶段限一次"、也不弃牌，
故它是**裸发动**（``ActivateSkill`` 载荷可全空：``picks == ()``、``choices == ()``）
+ ``once_per_phase=False``（同一阶段可反复发动）。"失去体力"压 ``LIFE_DECREASE`` 帧走
唯一的体力写入点（**不**压 DAMAGE 帧——失去体力不是受到伤害），该帧自带濒死插入；
摸两张在子帧出栈后执行，故濒死救回之后才摸（"然后"）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sgs.data import (
    Category,
    Color,
    Gender,
    Kingdom,
    SkillType,
    Suit,
    card_category,
    color_of_suit,
    hero_card_skill_ids,
    hero_skill_id,
)
from sgs.effects import (
    CARD_BEHAVIORS,
    CandidateScope,
    CarrierKind,
    DamageNature,
    Modifier,
    ModifierContext,
    ModifierPoint,
    PEACH_RESPONSE_CARD,
    behavior_for,
    check_count,
    check_targets,
    forced_use_targets,
    is_slash,
    needs_designation,
    satisfies_required,
    targets_within_attack_range,
)
from sgs.resolution import Control, Tag, add_tag
from sgs.state import (
    ActivateSkill,
    CardRef,
    FrameKind,
    GameState,
    MoveReason,
    Pass,
    Reforge,
    RespondMode,
    SEALED_SLOT_IDS,
    SelectionSpec,
    Slot,
    SlotKind,
    UseCard,
    Zone,
    ZoneRef,
    alive_seats,
    seats_from,
)
from sgs.timing import Timing

from .catalog import (
    HeroSkillDecl,
    SkillDef,
    _GIVEN_TO_SHIFT,
    _HUANGTIAN_SHIFT,
    _USED_SECOND,
    _move_by_source,
    instance_key,
    limited_skill_used,
    mark_limited_skill_used,
    resolve_instances,
    same_kingdom,
)
from .virtual import pure_virtual_card, transformed_card

__all__ = [
    "ACTIVATIONS",
    "ACTIVE_SKILLS",
    "CANDIDATE_CAP",
    "JIE_LIJIAN",
    "JIE_LIJIAN_FLOW",
    "POXI_CARDS",
    "POXI_FLOW",
    "ActiveSkill",
    "Arrange",
    "PickCards",
    "PickOption",
    "PickProvide",
    "PickSeats",
    "SkillFlow",
    "activate_skill",
    "active_skill_definitions",
    "run_flow",
]

CANDIDATE_CAP = 128
"""单次询问候选数的组合上界（票 04 §要做什么 3 / `spec.md` §机制面的统一约定）。

A 族迁到骨架候选后没有降级路径，故这个常量只剩**候选数审计**这一个读取方：
``tests/test_fuzz.py`` 断言任一技能 / 非技能候选组 ≤ 128。W6-06a 删除两维降级原语时
**保留**它（fuzz 上界，不是旧路径的残留）。
"""

_USED = 1
"""per-instance 阶段位：本出牌阶段已经发动过（候选生成期据此挡掉）。"""
_RECOVERED = 2
"""per-instance 阶段位：【仁德】本阶段那次"累计达到第 2 张"的回复**已经发生过**（每阶段至多一次）。"""
_GAVE_ANY = 4
"""per-instance 阶段位：【仁德】本阶段**已经给过 ≥1 张**。

规则 976 判的是**本阶段累计**给出的张数是否首次达到 2，不是单次发动的张数（见
:func:`_rende_recover`）。每次发动至少给 1 张（`lo=1`），所以"本阶段是否已给过 ≥1 张"
这一位就够推出"这次会把累计推过 2"。
"""

_SUIT_OPTIONS: tuple[str, ...] = tuple(suit.value for suit in Suit)
"""【反间】让目标选的四种花色；取值序 = :class:`~sgs.data.Suit` 的枚举序（确定性）。"""


# ---------------------------------------------------------------------------
# per-instance 阶段状态：位标记（候选生成期与发动回调共用）
# ---------------------------------------------------------------------------


def _skill_key(state: GameState, owner: int, skill_id: str) -> str | None:
    """``(owner, skill_id)`` 那条**存活实例**的 ``skill_state`` 键；没有则不落状态。"""
    for instance in resolve_instances(state):
        if instance.owner == owner and instance.skill_id == skill_id:
            return instance_key(instance)
    return None


def _phase_flags(state: GameState, owner: int, skill_id: str) -> int:
    """本实例当前的阶段位集合（实例不在 ⇒ ``0``，即"没用过"）。"""
    key = _skill_key(state, owner, skill_id)
    return 0 if key is None else state.skill_state.get(key, 0)


def _mark_phase(state: GameState, owner: int, skill_id: str, bit: int) -> None:
    """置一个阶段位（幂等；状态键与 ``prune_skill_state`` 的"存活实例键"同形）。"""
    key = _skill_key(state, owner, skill_id)
    if key is not None:
        state.skill_state[key] = state.skill_state.get(key, 0) | bit


# ---------------------------------------------------------------------------
# 载荷编解码与公共原语
# ---------------------------------------------------------------------------


def _other_alive_seats(state: GameState, owner: int) -> tuple[int, ...]:
    """**除发动者本人外**的存活座位（座位序）。【仁德】/【反间】的目标池同一形状。"""
    return tuple(p.seat for p in state.players if p.alive and p.seat != owner)


def _can_offer(state: GameState, ctx: ModifierContext, *, once_per_phase: bool = True) -> bool:
    """本技能此刻能不能进 PLAY 池：整段追加 + 本人 + （可选）本阶段没用过。

    ``once_per_phase=False`` 用于**没有"出牌阶段限一次"**的主动技（【仁德】：
    规则 976 只给"任意数量"、没有次数上限），它每轮出牌阶段询问都该重新进池。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return False
    if ctx.scope is not CandidateScope.PLAY:
        return False
    if not once_per_phase:
        return True
    return not (_phase_flags(state, ctx.owner, ctx.skill_id) & _USED)


def _move_to_discard(
    host: Any, frame: Any, card_ids: tuple[int, ...], *, owner: int, key: str
) -> Any:
    """把选中的牌按**来源区**分组弃置（一次 ``move_card`` 只能有一个来源区）。

    与【贯石斧】的成本移动是**同一个形状**，故直接复用
    :func:`~sgs.skills.catalog._move_by_source`（code-review C10 去重）：plan / done 游标、
    "内联 ``MOVE_AFTER`` 挂起时透传 ``"stay"``"的重入纪律都只有一份实现。
    这里的 ``to_zone`` 恒为弃牌堆、``reason`` 由 helper 恒取 ``DISCARD``。
    """
    return _move_by_source(
        host,
        frame,
        card_ids,
        to_zone=ZoneRef(Zone.DISCARD),
        owner=owner,
        key=key,
    )


def _push_recover(host: Any, frame: Any, *, source: int, target: int, amount: int = 1) -> None:
    """压一个 ``RECOVER`` 子帧（回复量由帧算、写体力走唯一原语；超过上限由帧夹住）。"""
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": source,
            "targets": (target,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "amount": amount,
        },
        timing=Timing.RECOVER_VALUE,
    )


def _push_damage(
    host: Any,
    frame: Any,
    *,
    source: int | None,
    target: int,
    amount: int = 1,
    nature: DamageNature = DamageNature.NORMAL,
) -> None:
    """压一个 ``DAMAGE`` 子帧（主动技的伤害无渠道牌，故 ``channel = None``）。

    ``nature``（神周瑜移动版【业炎】的加法）：火焰 / 雷电等属性伤害走行为表的同一处声明；
    缺省 ``NORMAL`` ⇒ 既有主动技的伤害帧逐字不变。``source`` 可以是 ``None``——效果结算中
    来源已阵亡时按"无伤害来源"压帧（【业炎】的用户裁定）。
    """
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": frame.data.get("card"),
            "use_timing": frame.data.get("use_timing"),
            "target": target,
            "channel": frame.data.get("card"),
            "nature": nature,
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )


# ---------------------------------------------------------------------------
# 通用步骤流水线：**抽到 `sgs.skills.flow`**（票 07 §1）
#
# 主动技（本模块）与被动技（`catalog` 的钩子 / 接缝）都要用这套步骤执行器，而本模块已经
# ``from .catalog import …``；流水线若留在本模块，``catalog`` 再 import 本模块就是循环。
# 故原语整体搬去 :mod:`sgs.skills.flow`（反向只依赖 ``sgs.state`` / ``sgs.timing``），
# 本模块**重导出**同一批名字：既有 import 点（``sgs.skills`` 包入口、
# ``tests/test_hero_active.py`` 的 ``active_module.PickCards`` 等）与 :data:`__all__`
# 一字不变，消费者无需改 import；私有名的包装见下方 :func:`_seat_choice` / :func:`_option_seat`。
# ---------------------------------------------------------------------------

from .flow import (  # noqa: F401  （重导出：既有 import 点与 __all__ 不变）
    ActorRef,
    Arrange,
    FlowAnswers,
    FlowEffect,
    FlowStep,
    PickCards,
    PickOption,
    PickProvide,
    PickSeats,
    SeatPool,
    SkillFlow,
    option_seat as _option_seat,
    run_flow,
    seat_choice as _seat_choice,
)

def _limited_offers(
    skill: "ActiveSkill", offers: Callable[[GameState, ModifierContext, Any], Any]
) -> Callable[[GameState, ModifierContext, Any], Any]:
    """限定技的**候选闸**（韬·刘备【义起】）：整局已用之后不再产出候选。

    被动侧的同一条门在 :func:`~sgs.skills.catalog._dispatch`（"限定技整局一次 ⇒ 不再产出
    候选、不再发是否发动询问"）；主动侧没有派发环，故在声明装配处包一层。
    **按类型判断，不按技能名**：非限定技原样返回同一个回调（零额外开销、零行为变化）。
    """
    if skill.skill_type is not SkillType.LIMITED:
        return offers

    def guarded(state: GameState, ctx: ModifierContext, current: Any) -> Any:
        if limited_skill_used(state, ctx.owner, skill.skill_id):
            return current
        return offers(state, ctx, current)

    return guarded


def _activation_handler(skill: ActiveSkill) -> ActivationHandler:
    """一条声明的发动回调：手写回调原样取用，声明式流水线在这里**现建闭包**。

    ``on_commit``（票 07）**在每次调用时现算**：主动技的"出牌阶段限一次"落在
    ``(skill_owner, skill_id)`` 那条实例的 ``_USED`` 位，``owner`` 读的是**本次 SKILL 帧**
    的 ``source`` —— 在装配 :class:`ActiveSkill` 那一处就把 owner / skill_id 烘死是错的。
    （票 12 §4：旧实现由 ``flow.flow_handler`` 这个通用工厂派生，但它拿不到帧、做不了这件事，
    已删；本函数是**唯一**落点。）
    ``once_per_phase=False`` 的技能（【仁德】）照样不落位。
    """
    if skill.flow is not None:
        flow = skill.flow
        limited = skill.skill_type is SkillType.LIMITED

        def handler(host: Any, frame: Any) -> Any:
            def commit() -> None:
                if flow.once_per_phase:
                    _mark_phase(
                        host.state, frame.data["source"], frame.data["skill_id"], _USED
                    )
                if limited:
                    # 限定技的"整局已用"位（与被动侧执行完落位同一时机）。它**不随**
                    # ``once_per_phase`` 开关：一次性与一次性是两条轴（【义起】只整局一次，
                    # 不需要阶段位）。
                    mark_limited_skill_used(
                        host.state, frame.data["source"], frame.data["skill_id"]
                    )

            return run_flow(
                host,
                frame,
                flow,
                on_commit=commit if (flow.once_per_phase or limited) else None,
            )

        return handler
    assert skill.activate is not None  # __post_init__ 已保证恰有其一
    return skill.activate


# ---------------------------------------------------------------------------
# 孙权【制衡】：弃任意张（手牌 + 装备区）→ 摸等量
#
# 票 04：本技能是 :class:`SkillFlow` **只有一步**的第一个实例（票 01/02/03 都是两步）：
# ``PickCards(HAND ∪ EQUIP, 1, None)`` → 弃置那几张、摸等量。旧的 2ⁿ 全枚举
# （手牌 8 张约 255 条候选）随之删除；"任意张"的上下限由步骤声明表达：
# 下限 ``lo=1``（0 张不是合法提交）、上限 ``hi=None``（= 全部可选牌，执行器解析）。
# ---------------------------------------------------------------------------


def _zhiheng_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    player = state.player(ctx.subject)
    if not player.hand and not player.equip:
        return current
    # 票 04：候选**恒为一条骨架**（与【离间】/【青囊】/【结姻】同姿态），不再烘 2ⁿ 个子集。
    # 弃哪几张改由发动后的步骤流水线问 ⇒ 候选数与手牌数、装备数无关。
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _zhiheng_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：按来源区弃置选中的牌，再摸**等量**的牌（``_USED`` 已由执行器落位）。"""
    owner = frame.data["source"]
    cards = answers["card"]
    assert isinstance(cards, tuple)
    if _move_to_discard(host, frame, cards, owner=owner, key="zhiheng:cost") == "stay":
        return "stay"
    host.draw_for_hand(frame, owner, len(cards))
    return None


ZHIHENG_FLOW = SkillFlow(
    name="zhiheng",
    steps=(PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=None, key="card"),),
    effect=_zhiheng_effect,
)
"""【制衡】的单步流水线：弃任意张（手牌 ∪ 装备区、非空）→ 摸等量（票 04）。"""


# ---------------------------------------------------------------------------
# 界孙权【制衡】（M6 票 17）：与标准版同一条单步流水线，另加"弃光所有手牌多摸一张"
#
# §十九 L2137–L2139：
#
#     制衡：出牌阶段限一次，你可以弃置任意张牌，然后摸等量的牌（若你以此法弃置了所有
#           的手牌，则多摸一张牌）。
#
# 差异只有"多摸一张"这一句，故**复用** :data:`ZHIHENG_FLOW` 的步骤声明，只在提交点里
# 换一个效果体（弃牌三态与重入纪律仍是 :func:`_move_to_discard` 那一份）。
#
# "弃置了所有的手牌"的判据（本票裁定，`## Answer` 记录）：选中的牌**包含了发动时的全部
# 手牌**（因此弃完之后手牌为空）。发动时本来就没有手牌（只弃装备区）不额外摸——牌面写的是
# "弃置了所有的手牌"，没有手牌时不存在"以此法弃置"这件事。
# ---------------------------------------------------------------------------

JIE_ZHIHENG = hero_skill_id("jie_sunquan", "zhiheng")
"""界孙权【制衡】的全限定技能 id（§十九 L2137–L2139）。"""

_JIE_ZHIHENG_HAND_KEY = "jie_zhiheng_hand"
"""帧私有：发动时的手牌快照（重入时据此判"是否弃光了所有手牌"，不重算）。"""

_JIE_ZHIHENG_DRAW_KEY = "jie_zhiheng_draw"
"""帧私有：本次摸牌数（重入时不再重算，也保证只摸一次）。"""


def _jie_zhiheng_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：先记"是否弃光所有手牌"→ 按来源区弃置 → 摸等量（+1）。"""
    owner = frame.data["source"]
    cards = answers["card"]
    assert isinstance(cards, tuple)
    if _JIE_ZHIHENG_DRAW_KEY not in frame.data:
        hand = tuple(host.state.player(owner).hand)
        frame.data[_JIE_ZHIHENG_HAND_KEY] = hand
        extra = 1 if hand and set(hand) <= set(cards) else 0
        frame.data[_JIE_ZHIHENG_DRAW_KEY] = len(cards) + extra
    if _move_to_discard(host, frame, cards, owner=owner, key="jie_zhiheng:cost") == "stay":
        return "stay"
    host.draw_for_hand(frame, owner, int(frame.data[_JIE_ZHIHENG_DRAW_KEY]))
    return None


JIE_ZHIHENG_FLOW = SkillFlow(
    name="jie_zhiheng",
    steps=(PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=None, key="card"),),
    effect=_jie_zhiheng_effect,
)
"""界【制衡】的单步流水线：弃任意张（手牌 ∪ 装备区）→ 摸等量、弃光手牌则多摸一张。"""


# ---------------------------------------------------------------------------
# 刘备【仁德】：给任意张手牌 → 一名其他角色；本阶段首次给到第 2 张回复 1 点
#
# 票 05：本技能是 :class:`SkillFlow` 的第四个消费者，也是**唯一一条没有"出牌阶段限一次"**
# 的（规则 976）⇒ 流水线传 ``once_per_phase=False``，`_USED` 一个位都不落，取消后同一
# 阶段立刻可再发动。旧形状按用户裁定不设组合闸（非空手牌子集 × 目标全枚举，手牌 8 张
# 约 1800 条候选），换来的是"点座位即提交、没有确认步"的交互；票 05 换成骨架候选 +
# 两步询问，交互随之变成**选任意张手牌 → 点一名其他角色 → 确认**（用户已知情并接受）。
# ---------------------------------------------------------------------------


def _rende_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """【仁德】的可选目标：其他存活角色（座位序；``chosen`` 对单选无意义）。

    池为空 ⇒ 候选根本不产出（:func:`_rende_offers`），不会发出没有候选的询问。
    """
    del frame, chosen
    return _other_alive_seats(state, owner)


def _rende_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    hand = tuple(state.player(ctx.subject).hand)
    if not hand or not _rende_targets(state, None, ctx.subject, ()):
        return current
    # 票 05：候选**恒为一条骨架**（与【离间】/【青囊】/【结姻】/【制衡】同姿态），
    # 给哪几张、给谁改由发动后的步骤流水线问 ⇒ 候选数与手牌数、目标数无关。
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _give(
    host: Any, frame: Any, owner: int, target: int, card_ids: tuple[int, ...], *, key: str
) -> Any:
    """把 ``card_ids`` 从发动者手牌交给目标（``PUT`` 进目标手牌、归属 = 目标）。

    与 :func:`_move_to_discard` 同姿态：内联 ``MOVE_AFTER`` 挂起时透传 ``"stay"``；
    帧私有游标 ``key`` 让重入不把同一次交给做第二遍。
    """
    if not card_ids:
        return None
    done_key = f"{key}:done"
    if frame.data.get(done_key):
        return None
    frame.data[done_key] = True
    return host.move_in_frame(
        frame,
        host.state.refs(card_ids),
        to_zone=ZoneRef(Zone.HAND, target),
        reason=MoveReason.PUT,
        owner=target,
        actor=owner,
    )


def _rende_recover(
    host: Any, frame: Any, owner: int, skill_id: str, given: int
) -> bool:
    """本次交出 ``given`` 张后，**本阶段累计**是否**首次**达到 2 张；返回是否压了回复帧。

    规则 976 的口径是**累计**（"若你于此阶段内给出的牌首次达到两张"），不是单次发动的张数：

    - 给 1 张、再给 1 张 ⇒ **第二次**发动时累计到 2 ⇒ 回血；
    - 给 1 张、再给 3 张 ⇒ 同样是**第二次**发动时累计跨过 2 ⇒ 回血；
    - 一次给 2 张 ⇒ 当次回血；
    - 累计达到 2 之后，本阶段再给多少都不算"首次" ⇒ 每阶段**至多回一次**。

    判据只用两个阶段位：``_RECOVERED``（回过没有）与 ``_GAVE_ANY``（本阶段是否已给过 ≥1 张）。
    每次发动至少给 1 张（`lo=1`），故读这两个位就够，不必把累计张数存进状态。
    """
    state = host.state
    flags = _phase_flags(state, owner, skill_id)
    healed = bool(flags & _RECOVERED)
    # 这次确实交出去了（无论回不回血）：先落位，供**下一次**发动判断累计。
    _mark_phase(state, owner, skill_id, _GAVE_ANY)
    if healed or given < 1:
        return False
    if given < 2 and not (flags & _GAVE_ANY):
        return False
    _mark_phase(state, owner, skill_id, _RECOVERED)
    _push_recover(host, frame, source=owner, target=owner)
    return True


def _rende_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：把选中的手牌一次性交给目标；**本阶段累计**首次达到 2 张时回复 1 点。

    **不置 ``_USED``**：执行器按 ``once_per_phase=False`` 跳过它（规则 976 没有
    "出牌阶段限一次"）；本阶段只按 :func:`_rende_recover` 落 ``_GAVE_ANY`` / ``_RECOVERED``。
    """
    owner = frame.data["source"]
    skill_id = frame.data["skill_id"]
    seats = answers["seat"]
    cards = answers["card"]
    assert isinstance(seats, tuple) and isinstance(cards, tuple)
    target = int(seats[0])
    if _give(host, frame, owner, target, cards, key="rende:cost") == "stay":
        return "stay"
    _rende_recover(host, frame, owner, skill_id, len(cards))
    return None


RENDE_FLOW = SkillFlow(
    name="rende",
    steps=(
        PickCards(zones=(Zone.HAND,), lo=1, hi=None, key="card"),
        PickSeats(lo=1, hi=1, pool=_rende_targets, key="seat"),
    ),
    effect=_rende_effect,
    once_per_phase=False,
)
"""【仁德】的两步流水线：给任意张手牌 → 一名其他角色（**无出牌阶段限一次**，票 05）。"""


# ---------------------------------------------------------------------------
# 界刘备【仁德】（M6 票 16）：出牌阶段**每名角色限一次**；本阶段第二张换一次"视为使用
# 一张基本牌"
#
# §十九 L2075–L2077：
#
#     仁德：出牌阶段每名角色限一次，你可以交给一名其他角色任意张手牌，若你此次给出了
#           本阶段的第二张"仁德"牌，你可以视为使用一张基本牌。
#
# 与标准【仁德】（票 05）是**两处**差异，故不复用标准版的流水线（收益不同、目标限次
# 也不同）；共用的是给牌原语 :func:`_give` 与"本阶段累计给到 2"的判据形状：
#
# - **每名角色限一次**：每位其他角色本阶段只能被你仁德一次。座位位段住 ``skill_state``
#   的 :data:`~sgs.skills.catalog._GIVEN_TO_MASK`（作用域 = 本出牌阶段，与 A 族阶段位
#   同一个复位点），故不需要新的状态字段；
# - **本阶段第二张 ⇒ 视为使用一张基本牌**：不再回复体力（标准版才是回复）。累计口径与
#   标准版一致（"首次达到 2"：给 1 张再给 1 张、或一次给 ≥2 张），每阶段至多一次；
# - **"一张基本牌"的牌型 / 目标限制**（本票裁定，`06`（§5「程序化约定与偏离一手」））：只有**此刻走得通完整使用
#   规则**的基本牌才进候选——【杀】（次数未满且有合法目标）、【桃】（仅自己且已受伤）、
#   【酒】（仅自己且本回合未用过）；【闪】不是可在出牌阶段"使用"的牌，故不在列。牌型
#   清单用既有的 :func:`~sgs.effects.check_count` / :func:`~sgs.effects.check_targets`
#   现算，与真牌候选同一条判据，不在技能里另抄一份规则；产物牌名在本档牌堆里没有实体牌
#   时（108 档的【酒】）不进候选——不凭空造一张牌堆外的牌。
# ---------------------------------------------------------------------------

JIE_RENDE = hero_skill_id("jie_liubei", "rende")
"""界刘备【仁德】的全限定技能 id（§十九 L2075–L2077）。"""

JIE_RENDE_BASIC_SLASH = "basic_slash"
JIE_RENDE_BASIC_PEACH = "basic_peach"
JIE_RENDE_BASIC_WINE = "basic_wine"
"""界【仁德】"视为使用一张基本牌"的选项 id（闭集；不用裸牌名，避免与别的选项表撞键）。"""

JIE_RENDE_BASIC_OPTIONS: tuple[str, ...] = (
    JIE_RENDE_BASIC_SLASH,
    JIE_RENDE_BASIC_PEACH,
    JIE_RENDE_BASIC_WINE,
)
"""询问顺序：杀 → 桃 → 酒（与牌面"基本牌"的常见用法序一致；届时只保留下得去的）。"""

_JIE_RENDE_BASIC_TYPES: dict[str, str] = {
    JIE_RENDE_BASIC_SLASH: "slash",
    JIE_RENDE_BASIC_PEACH: "peach",
    JIE_RENDE_BASIC_WINE: "wine",
}
"""选项 id → 产物牌名 id。"""

_JIE_RENDE_ASK_BASIC = "hero.jie_liubei.rende.basic"
"""手写询问位置：界【仁德】"视为使用哪一张基本牌"的 ``ask_option`` 标识。"""

_JIE_RENDE_ASK_TARGET = "hero.jie_liubei.rende.target"
"""手写询问位置：视为使用【杀】时的目标座位（``seat:N`` 选项）。"""

_JIE_RENDE_GIVEN_KEY = "jie_rende_given"
"""帧私有：这一次发动的手牌已经交给目标（续跑不重交）。"""

_JIE_RENDE_BONUS_KEY = "jie_rende_bonus"
"""帧私有：这次发动是否触发了"本阶段第二张"（续跑不重算）。"""

_JIE_RENDE_OPTION_KEY = "jie_rende_option"
"""帧私有：选中的基本牌选项（空串 = 放弃；续跑不重问）。"""

_JIE_RENDE_TARGET_KEY = "jie_rende_target"
"""帧私有：视为使用【杀】时选中的目标座位。"""

_JIE_RENDE_USED_KEY = "jie_rende_used"
"""帧私有：视为使用已经发起（续跑不重复 ``start_use``）。"""


def _has_entity_of_type(state: GameState, type_id: str) -> bool:
    """本档牌堆里有没有这种牌名的**实体牌**（界【仁德】的基本牌清单用；108 档没有【酒】）。"""
    return any(card.type == type_id for card in state.cards)


def _gave_to(state: GameState, owner: int, skill_id: str, seat: int) -> bool:
    """``seat`` 本出牌阶段是否已经收过这条【仁德】的牌（座位位段，见
    :data:`~sgs.skills.catalog._GIVEN_TO_MASK`）。"""
    key = _skill_key(state, owner, skill_id)
    if key is None:
        return True  # 实例不在 ⇒ 记不了"已给过" ⇒ fail-closed，不产出这个目标
    return bool(state.skill_state.get(key, 0) & (1 << (_GIVEN_TO_SHIFT + seat)))


def _mark_gave_to(state: GameState, owner: int, skill_id: str, seat: int) -> None:
    """记下"本阶段已经给过 ``seat``"（位段随出牌阶段复位，见
    :data:`~sgs.skills.catalog._GIVEN_TO_MASK`）。"""
    key = _skill_key(state, owner, skill_id)
    if key is not None and 0 <= seat < 8:
        state.skill_state[key] = state.skill_state.get(key, 0) | (
            1 << (_GIVEN_TO_SHIFT + seat)
        )


def _jie_rende_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """界【仁德】的可选目标：**本阶段还没收过**的其他存活角色（座位序）。"""
    del frame, chosen
    return tuple(
        seat
        for seat in _other_alive_seats(state, owner)
        if not _gave_to(state, owner, JIE_RENDE, seat)
    )


def _jie_rende_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    if not state.player(ctx.subject).hand:
        return current
    if not _jie_rende_targets(state, None, ctx.subject, ()):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _jie_rende_basic_options(state: GameState, owner: int) -> tuple[str, ...]:
    """此刻"视为使用"能用的基本牌选项（本票裁定；判据与真牌候选同源）。

    逐项用 :func:`~sgs.effects.check_count`（使用次数）与
    :func:`~sgs.effects.check_targets`（目标池与目标规则：桃仅自己且已受伤、酒仅自己、
    杀走距离与"不能成为目标"链）现算；产物牌名在本档牌堆里没有实体牌时跳过。
    """
    options: list[str] = []
    for option in JIE_RENDE_BASIC_OPTIONS:
        type_id = _JIE_RENDE_BASIC_TYPES[option]
        if not _has_entity_of_type(state, type_id):
            continue
        card = pure_virtual_card(state, type_id)
        if check_count(state, owner, card):
            continue
        if not check_targets(state, owner, card):
            continue
        options.append(option)
    return tuple(options)


def _jie_rende_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：交牌 + 记目标 +（本阶段第二张时）可视为使用一张基本牌。

    "然后"的严格次序：先给牌（可能内联派发"失去牌后"而挂起），再按累计张数决定是否
    触发基本牌，最后才问牌型 / 目标并 ``start_use``。每段一个帧私有游标：重入既不重交、
    也不重问、更不重复使用。
    """
    owner = frame.data["source"]
    skill_id = frame.data["skill_id"]
    if not frame.data.get(_JIE_RENDE_GIVEN_KEY):
        cards = answers["card"]
        seats = answers["seat"]
        assert isinstance(cards, tuple) and isinstance(seats, tuple)
        target = int(seats[0])
        if _give(host, frame, owner, target, cards, key="jie_rende:cost") == "stay":
            return "stay"
        frame.data[_JIE_RENDE_GIVEN_KEY] = True
        _mark_gave_to(host.state, owner, skill_id, target)
        flags = _phase_flags(host.state, owner, skill_id)
        _mark_phase(host.state, owner, skill_id, _GAVE_ANY)
        bonus = not (flags & _RECOVERED) and (
            len(cards) >= 2 or bool(flags & _GAVE_ANY)
        )
        if bonus:
            _mark_phase(host.state, owner, skill_id, _RECOVERED)
        frame.data[_JIE_RENDE_BONUS_KEY] = bonus
    if not frame.data.get(_JIE_RENDE_BONUS_KEY) or frame.data.get(_JIE_RENDE_USED_KEY):
        return None
    option = frame.data.get(_JIE_RENDE_OPTION_KEY)
    if option is None:
        options = _jie_rende_basic_options(host.state, owner)
        if not options:
            frame.data[_JIE_RENDE_USED_KEY] = True
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.PHASE_PLAY_IDLE,
            skill_id=_JIE_RENDE_ASK_BASIC,
            options=options,
            optional=True,
        )
        if host.suspended(answer):
            return "stay"
        option = "" if isinstance(answer, Pass) else str(answer.choice)
        frame.data[_JIE_RENDE_OPTION_KEY] = option
    if not option:
        frame.data[_JIE_RENDE_USED_KEY] = True
        return None
    type_id = _JIE_RENDE_BASIC_TYPES[option]
    card = pure_virtual_card(host.state, type_id)
    targets: tuple[int, ...]
    if type_id == "slash":
        target_seat = frame.data.get(_JIE_RENDE_TARGET_KEY)
        if target_seat is None:
            pool = check_targets(host.state, owner, card)
            if not pool:
                frame.data[_JIE_RENDE_USED_KEY] = True
                return None
            answer = host.ask_option(
                frame,
                owner,
                Timing.PHASE_PLAY_IDLE,
                skill_id=_JIE_RENDE_ASK_TARGET,
                options=tuple(f"seat:{seat}" for seat in pool),
                optional=False,
            )
            if host.suspended(answer):
                return "stay"
            target_seat = int(str(answer.choice).split(":", 1)[1])
            frame.data[_JIE_RENDE_TARGET_KEY] = target_seat
        targets = (int(target_seat),)
    else:
        targets = (owner,)
    frame.data[_JIE_RENDE_USED_KEY] = True
    host.start_use(
        card,
        source=owner,
        targets=targets,
        use_timing=Timing.PHASE_PLAY_IDLE,
    )
    return None


JIE_RENDE_FLOW = SkillFlow(
    name="jie_rende",
    steps=(
        PickCards(zones=(Zone.HAND,), lo=1, hi=None, key="card"),
        PickSeats(lo=1, hi=1, pool=_jie_rende_targets, key="seat"),
    ),
    effect=_jie_rende_effect,
    once_per_phase=False,
)
"""界【仁德】的两步流水线：给任意张手牌 → **本阶段还没收过**的一名其他角色（可再发动）。"""


# ---------------------------------------------------------------------------
# 周瑜【反间】：目标选花色 → 其获得并展示你的一张手牌 → 花色不同则受伤
#
# 票 06：本票是这批唯一需要**新执行语义**的一条 —— 三步里有两步的**应答者是目标**
# （不是发动者），而第三步的**选择面铺的又是发动者的手牌**。"谁应答"与"铺谁的区"在
# 这里第一次分开，正是 :class:`PickCards` 的 ``actor`` / ``face`` 两个字段要承载的语义。
# ---------------------------------------------------------------------------


def _fanjian_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """【反间】的可选目标：其他存活角色（座位序；``chosen`` 对单选无意义）。"""
    del frame, chosen
    return _other_alive_seats(state, owner)


def _fanjian_target(state: GameState, frame: Any, owner: int, answers: FlowAnswers) -> int:
    """步 2 / 步 3 的**应答者**：步 1 选中的那名目标。"""
    return int(answers["seat"][0])


def _fanjian_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    # **没有手牌不能发动**（用户验收）：反间的代价是"交出一张手牌"，手牌为空时
    # 候选 / 预发动清单都不产出，技能按钮不可点（不是进游戏后才失败）。
    if not state.player(ctx.subject).hand:
        return current
    if not _fanjian_targets(state, None, ctx.subject, ()):
        return current
    # 票 06：候选恒**一条骨架**（旧形状是"每个目标一条"），目标改由发动后的步 1 问。
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _fanjian_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：目标挑中的那张牌先亮进处理区、再交给目标；花色不同则目标受伤。

    三步的选择只收集，在这里一次性生效。搬牌可能内联派发"失去牌后"并**挂起**（票 07），
    故三个帧内游标（``revealed`` / ``delivered`` / ``damage_done``）与票 04 的手写版
    一字同形：重入既不重做移动、也不漏掉伤害。
    """
    state = host.state
    owner = frame.data["source"]
    target = int(answers["seat"][0])
    suit = str(answers["suit"])
    card = state.card(answers["card"][0])
    if "revealed" not in frame.data:
        # 载荷先落帧、再搬牌：搬牌挂起时据此跳过本段、直接续跑交付段。
        frame.data["revealed"] = card.card_id
        if (
            host.move_in_frame(
                frame,
                (card,),
                to_zone=ZoneRef(Zone.PROCESSING),
                reason=MoveReason.PUT,
                owner=target,
                actor=owner,
            )
            == "stay"
        ):
            return "stay"
    if "delivered" not in frame.data:
        frame.data["delivered"] = True
        if (
            host.move_in_frame(
                frame,
                (state.card(frame.data["revealed"]),),
                to_zone=ZoneRef(Zone.HAND, target),
                reason=MoveReason.PUT,
                owner=target,
                actor=owner,
            )
            == "stay"
        ):
            return "stay"
    if "damage_done" not in frame.data:
        # 伤害**单列一个帧内游标**：交付那一步若挂起，重入必须只跑伤害。
        frame.data["damage_done"] = True
        if state.card(frame.data["revealed"]).suit != suit:
            _push_damage(host, frame, source=owner, target=target)
    return None


FANJIAN_FLOW = SkillFlow(
    name="fanjian",
    steps=(
        PickSeats(lo=1, hi=1, pool=_fanjian_targets, key="seat"),
        # 票 08 复盘 / 用户验收：**目标确认之后不可退回**。花色与盲挑都 `optional=False`
        # ——候选里没有 `Pass`，超时兜底自然落到第一个合法项（花色按 `Suit` 枚举序，
        # 盲挑按面内槽位序）。"你可以"只到第一段（预发动选目标）为止；选的这一刻起
        # 必定交出一张手牌，故"放弃"只留在第 1 步。
        PickOption(options=_SUIT_OPTIONS, actor=_fanjian_target, key="suit", optional=False),
        PickCards(
            zones=(Zone.HAND,),
            lo=1,
            hi=1,
            key="card",
            actor=_fanjian_target,
            optional=False,
            face=lambda state, frame, owner, answers: owner,
            # **盲挑**：目标只看得到槽位、看不到牌面（`reveal_hand=False`，与迁移前
            # `selection_face(owner, (HAND,))` 的缺省一字同形）。官方口径是"获得你的一张
            # 手牌并展示之"——**获得之后**才展示，故挑的时候不亮牌；`reveal_hand=True` 是
            # "挑牌者就是手牌持有者本人"（贯石斧 / 雌雄 / 青龙），用在这里会泄露手牌。
            reveal_hand=False,
        ),
    ),
    effect=_fanjian_effect,
)
"""【反间】的三步流水线：选谁 →（目标）猜花色 →（目标）从**发动者手牌**挑一张（票 06）。

U2 验收修正（用户裁定）：第一段（预发动选目标）仍可本地取消；**确认之后花色 / 盲挑两步
不可放弃**——`optional=False` 去掉了 `Pass`，超时兜底因此取第一个合法项（第一个花色 /
面内第一张手牌）。周瑜没有手牌时 `_fanjian_offers` 不产出候选 / 清单，技能不发动。
"""


# ---------------------------------------------------------------------------
# 界周瑜【反间】（M6 票 08）：展示一张手牌交给一名其他角色，然后令其二选一
#
# §十九 L2171–L2174：
#
#     反间：出牌阶段限一次，你可以展示一张手牌并交给一名其他角色，然后令其选择一项：
#           1. 展示所有手牌，弃置与此牌花色相同的所有牌；2. 失去 1 点体力。
#
# 与标准【反间】**整句重写**（标准版是"目标选花色 → 获得你一张手牌 → 花色不同则受伤"），
# 故不复用 :data:`FANJIAN_FLOW`。次序是牌面写死的"先交给、后二选一"：交出之后那张牌已经
# 在目标手里，**它也属于"与此牌花色相同的所有牌"**（选①时一并弃置）——这是"展示所有手牌"
# 的字面读法，也是"先给后选"这个次序的事实后果。
#
# 二选一的应答者是**目标**（不是发动者），且不可放弃（``optional=False``）：发动者已经
# 交出了牌，这一幕必须落地。失去体力走 ``LIFE_DECREASE`` 帧（**不是**伤害：没有来源、
# 不经过伤害修正链，与【苦肉】同口径）。
# ---------------------------------------------------------------------------

JIE_FANJIAN = hero_skill_id("jie_zhouyu", "fanjian")
"""界周瑜【反间】的全限定技能 id（§十九 L2171–L2174）。"""

JIE_FANJIAN_DISCARD_SUIT = "discard_suit"
"""界【反间】选项①：展示所有手牌，弃置与此牌花色相同的所有牌。"""

JIE_FANJIAN_LOSE_HP = "lose_hp"
"""界【反间】选项②：失去 1 点体力。"""

JIE_FANJIAN_OPTIONS: tuple[str, ...] = (JIE_FANJIAN_DISCARD_SUIT, JIE_FANJIAN_LOSE_HP)
"""界【反间】的选项闭集（顺序即询问里的顺序）。"""

_JIE_FANJIAN_ASK = "fanjian:choice"
"""界【反间】"二选一"的询问标识（``ask_option`` 的 ``skill_id``）。

与流水线步骤的 ``<短 id>:<步骤名>`` 同一拼法（短 id = ``fanjian``），故投影的
``step_requirements`` 查表与界面提示走同一条键；也是 ``ask_answers`` 缓存键的一半。
"""

_JIE_FANJIAN_GIVEN = "jie_fanjian_given"
"""帧私有：那张手牌已经交出（搬牌挂起后的续跑不重搬）。"""

_JIE_FANJIAN_CHOICE = "jie_fanjian_choice"
"""帧私有：目标选的那一项（重入不重问）。"""

_JIE_FANJIAN_HP_LOST = "jie_fanjian_hp_lost"
"""帧私有：失去体力的子帧已经压过（出栈后的续跑只收尾）。"""

_JIE_FANJIAN_DISCARDED = "jie_fanjian_discarded"
"""帧私有：展示并弃置那一段已经做过。"""


def _jie_fanjian_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """界【反间】"一名其他角色"：除发动者外的存活座位（座位序）。"""
    del frame, chosen
    return _other_alive_seats(state, owner)


def _jie_fanjian_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选恒一条骨架：**没有手牌不能发动**（没有可展示 / 交出的牌）。"""
    if not _can_offer(state, ctx):
        return current
    if not state.player(ctx.subject).hand:
        return current
    if not _jie_fanjian_targets(state, None, ctx.subject, ()):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _jie_fanjian_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：展示并交出那张牌 → 目标二选一 → 执行。

    交出那一步可能内联派发"失去牌后"（接收者的【清俭】等）并挂起，故"已交出 / 已选定 /
    已失去 / 已弃置"各一个帧私有游标；续跑既不重搬也不重问。
    """
    state = host.state
    owner = frame.data["source"]
    target = int(answers["seat"][0])
    card = state.card(int(answers["card"][0]))
    if not frame.data.get(_JIE_FANJIAN_GIVEN):
        frame.data[_JIE_FANJIAN_GIVEN] = card.card_id
        host.reveal_card(frame, owner, card)
        control = host.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.HAND, target),
            reason=MoveReason.PUT,
            owner=target,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    if _JIE_FANJIAN_CHOICE not in frame.data:
        answer = host.ask_option(
            frame,
            target,
            Timing.PHASE_PLAY_IDLE,
            skill_id=_JIE_FANJIAN_ASK,
            options=JIE_FANJIAN_OPTIONS,
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        frame.data[_JIE_FANJIAN_CHOICE] = str(answer.choice)
    if frame.data[_JIE_FANJIAN_CHOICE] == JIE_FANJIAN_LOSE_HP:
        if not frame.data.get(_JIE_FANJIAN_HP_LOST):
            frame.data[_JIE_FANJIAN_HP_LOST] = True
            host.push_frame(
                FrameKind.LIFE_DECREASE,
                {"target": target, "source": None, "amount": 1},
                timing=Timing.LIFE_DECREASE_BEFORE,
            )
            return "stay"
        return None
    if not frame.data.get(_JIE_FANJIAN_DISCARDED):
        frame.data[_JIE_FANJIAN_DISCARDED] = True
        hand = tuple(state.player(target).hand)
        for card_id in hand:
            host.reveal_card(frame, target, state.card(card_id))
        same_suit = tuple(
            card_id for card_id in hand if state.card(card_id).suit == card.suit
        )
        if same_suit:
            control = host.move_in_frame(
                frame,
                state.refs(same_suit),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=target,
                actor=target,
            )
            if control == "stay":
                return "stay"
    return None


JIE_FANJIAN_FLOW = SkillFlow(
    name="jie_fanjian",
    steps=(
        PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="card"),
        PickSeats(lo=1, hi=1, pool=_jie_fanjian_targets, key="seat"),
    ),
    effect=_jie_fanjian_effect,
)
"""界【反间】的两步流水线：选一张手牌 → 选一名其他角色 →（提交点）交出 + 目标二选一。"""


# ---------------------------------------------------------------------------
# 华佗【青囊】：弃 1 张手牌 → 一名已受伤角色回复 1 点（目标池含自己）
#
# 票 02：本技能是 :class:`SkillFlow` 的**第二个实例**（第一个是票 01 手写的【离间】），
# 声明式形状在这里被确认：骨架候选 + ``PickCards → PickSeats`` 两步，旧的"过闸降级"
# 分支删除（同一个技能不再有两种交互形状）。
# ---------------------------------------------------------------------------


def _qingnang_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """【青囊】的可选目标：所有**已受伤**的存活角色（含发动者自己），座位序。

    签名与 :data:`SeatPool` 对齐（``owner`` / ``chosen`` 对静态池无意义）。
    """
    del frame, owner, chosen
    return tuple(p.seat for p in state.players if p.alive and p.hp < p.hp_max)


def _qingnang_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    if not state.player(ctx.subject).hand:
        return current
    if not _qingnang_targets(state, None, ctx.subject, ()):
        return current
    # 票 02：候选**恒为一条骨架**（与【离间】同姿态），不再烘"牌 × 目标"。弃哪张、
    # 治谁改由发动后的步骤流水线问 ⇒ 候选数与手牌数、受伤人数无关。
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _qingnang_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：搬成本进弃牌堆，再压回复帧（``_USED`` 已由执行器在调用前落位）。"""
    owner = frame.data["source"]
    seats = answers["seat"]
    cards = answers["card"]
    assert isinstance(seats, tuple) and isinstance(cards, tuple)
    if _move_to_discard(host, frame, cards, owner=owner, key="qingnang:cost") == "stay":
        return "stay"
    _push_recover(host, frame, source=owner, target=int(seats[0]))
    return None


QINGNANG_FLOW = SkillFlow(
    name="qingnang",
    steps=(
        PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="card"),
        PickSeats(lo=1, hi=1, pool=_qingnang_targets, key="seat"),
    ),
    effect=_qingnang_effect,
)
"""【青囊】的两步流水线：弃 1 张手牌 → 一名已受伤角色回复 1 点（票 02）。"""


# ---------------------------------------------------------------------------
# 界华佗【除疠】（M6 票 18）：任意名势力互异的其他角色 → 各弃一张；黑桃者各摸一张
#
# §十九 L2200–L2201：
#
#     除疠：出牌阶段限一次，你可以选择任意名势力各不相同的其他角色，然后弃置你和这些
#           角色的各一张牌。因此被弃置黑桃牌的角色各摸一张牌。
#
# 三条裁定（票面 `## 已知歧义`，写进 `06`（§5「程序化约定与偏离一手」））：
#
# - **目标组合** = 任意名、**势力互异**、**不含自己**、**至少 1 名**（0 目标非法）；
#   势力重复 / 不含自己 / 0 目标各有正反例（``tests/test_hero_jie_qun.py``）；
# - 牌面**没写**"区域内"（写的是"各一张牌"）⇒ 按 `GLOSSARY.md`「区域默认」= 手牌 + 装备，
#   不含判定区（与【利驭】/【趫猛】这两条明写"区域内 / 区域里"的技能分列）；
# - 弃哪一张由**发动者**（华佗）选：手牌是给发动者看的隐藏槽（与拆 / 顺同姿态）。
#
# **为什么不是一条纯声明式流水线**：目标是"任意名"（``PickSeats`` 可以），但每种目标
# 各问一次"弃哪张"的循环不是步骤语言里的一步（步骤只表达一次选择），故选牌循环住在
# 提交点里（与【苦肉】/【国色】的"零步提交 + 手写询问"同姿态）。
# ---------------------------------------------------------------------------

CHULI = hero_skill_id("jie_huatuo", "chuli")
"""界华佗【除疠】的全限定技能 id（§十九 L2200–L2201）。"""

_ASK_CHULI_CARD = "hero.jie_huatuo.chuli.card"
"""手写询问位置：第 ``i`` 名角色（0 = 自己）的那张成本牌。"""

_CHULI_MOVED = "chuli_moved"
"""帧私有：已经搬进弃牌堆的成本数（重入时从下一个角色接着搬）。"""

_CHULI_DRAWN = "chuli_drawn"
"""帧私有：黑桃摸牌已经结算（重入不重复摸）。"""

_CHULI_MAX_TARGETS = len(Kingdom)
"""**任意名**的上界 = 势力闭集的成员数（自己之外的每个势力至多一名）。"""


def _chuli_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """可选目标：存活、不含自己、**势力与已选目标互异**、且**有牌可弃**（手牌 ∪ 装备）。

    ``chosen`` 由步骤执行器逐次传入 ⇒ "势力互异"不需要在提交点再判一次。
    """
    del frame
    used = {state.player(seat).kingdom for seat in chosen}
    return tuple(
        player.seat
        for player in state.players
        if player.alive
        and player.seat != owner
        and player.kingdom not in used
        and (player.hand or player.equip)
    )


def _chuli_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：本阶段没用过 ∧ 自己付得起一张 ∧ 至少有一名可选目标。"""
    if not _can_offer(state, ctx):
        return current
    me = state.player(ctx.subject)
    if not (me.hand or me.equip):
        return current
    if not _chuli_targets(state, None, ctx.subject, ()):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _chuli_face(host: Any, seat: int, owner: int):
    """第 ``seat`` 名角色的选牌面：自己的手牌可见、别人的手牌是隐藏槽。"""
    return host.selection_face(
        seat, (Zone.HAND, Zone.EQUIP), reveal_hand=(seat == owner)
    )


def _chuli_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：逐名角色问一张牌 → 依次弃置 → 黑桃者各摸一张。

    选牌全部问完才开始搬牌：取消 / 校验失败发生在**步骤阶段**（此时一张牌都没动），
    故"取消零副作用"不靠提交点的额外守卫。搬牌逐个进行（一次 ``move_card`` 只能有一个
    来源区），进度落在帧私有游标里；某个角色的"失去牌后"钩子提问挂起时，重入从那一张
    接着搬（``move_in_frame`` 的同帧幂等守卫保证不重复搬）。
    """
    owner = frame.data["source"]
    seats = (owner, *answers["seat"])
    picked: list[tuple[int, int]] = []
    for index, seat in enumerate(seats):
        cards = host.ask_selection(
            frame,
            owner=_ASK_CHULI_CARD,
            key=f"chuli:card:{index}",
            actor=owner,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(
                zones=(Zone.HAND, Zone.EQUIP), total=(1, 1), optional=False
            ),
            build_face=lambda seat=seat: _chuli_face(host, seat, owner),
        )
        if host.suspended(cards):
            return "stay"
        if not cards:
            # 面上没有牌（重入时区域可能已变）：不搬、也不猜。
            return None
        picked.append((seat, int(cards[0])))
    moved = int(frame.data.get(_CHULI_MOVED, 0))
    while moved < len(picked):
        seat, card_id = picked[moved]
        control = host.move_in_frame(
            frame,
            host.state.refs((card_id,)),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=seat,
            actor=owner,
        )
        if control == "stay":
            return "stay"
        frame.data[_CHULI_MOVED] = moved + 1
        moved += 1
    if not frame.data.get(_CHULI_DRAWN):
        # 逐座摸牌里可能触发【清俭】并挂起（票 24：摸牌算获得）——挂起要把
        # ``"stay"`` 上抛，并用帧内游标记住已摸到第几项，续跑不重复摸、不漏摸。
        done = int(frame.data.get("chuli_drawn", 0))
        for seat, card_id in picked[done:]:
            if host.state.card(card_id).suit == Suit.SPADE.value:
                if host.draw_for_hand(frame, seat, 1) == "stay":
                    return "stay"
            done += 1
            frame.data["chuli_drawn"] = done
        frame.data[_CHULI_DRAWN] = True
    return None


CHULI_FLOW = SkillFlow(
    name="chuli",
    steps=(
        PickSeats(
            lo=1,
            hi=_CHULI_MAX_TARGETS,
            pool=_chuli_targets,
            # **有序多选**：第二问起依赖"本步已选"的势力（``chosen`` 传入池），清单语言
            # 只能表达第一问。翻 ``ordered`` 让清单**不下发候选索引**（``manifest`` 的
            # "有序多选"分支），复合动作因此不成立——人 / AI 都走执行期的逐步询问。
            # 这一步不放宽任何规则：校验器与执行器读的是同一个 ``pool(chosen)``。
            ordered=True,
            optional=True,
            key="seat",
        ),
    ),
    effect=_chuli_effect,
)
"""【除疠】的流水线：选任意名势力互异的其他角色（≥1）→ 提交点里逐名问成本牌。

``hi`` = 势力闭集的大小：目标数受"势力互异"收住，故这是结构上界而不是拍脑袋的数。
"""


# ---------------------------------------------------------------------------
# 界华雄【势斩】（M6 票 18）：出牌阶段限两次，令一名其他角色视为对你使用一张【决斗】
#
# §十九 L2220–L2221：
#
#     势斩：出牌阶段限两次，你可以令一名其他角色视为对你使用一张【决斗】。
#
# **"限两次"不是 `_USED` 一位能表达的**：`_USED` 是"限一次"的位（A 族七条），限两次
# 需要第二档。位 14 由 catalog 的 :data:`~sgs.skills.catalog._USED_SECOND` 持有，
# 与 `_USED` 成对读出"本阶段用了几次"；两者都在阶段掩码里，出牌阶段开始统一复位。
# 本技能因此走 ``once_per_phase=False``（执行器不自动落位），由提交点自己数。
#
# "视为对你使用【决斗】"：使用者 = 被指定的其他角色、目标 = 华雄（决斗由**目标**先响应）。
# 虚拟【决斗】与【离间】共用 :func:`~sgs.skills.virtual.pure_virtual_card` 的构造，
# 走同一条真使用 / 真响应窗口，不另造"决斗"分支。
# ---------------------------------------------------------------------------

SHIZHAN = hero_skill_id("jie_huaxiong", "shizhan")
"""界华雄【势斩】的全限定技能 id（§十九 L2220–L2221）。"""

_SHIZHAN_STARTED = "shizhan_started"
"""帧私有：虚拟【决斗】已经开出（重入不重复开）。"""


def _phase_use_count(state: GameState, owner: int, skill_id: str) -> int:
    """本阶段该实例已经发动几次（``_USED`` 与 :data:`_USED_SECOND` 两位之和）。"""
    flags = _phase_flags(state, owner, skill_id)
    return (1 if flags & _USED else 0) + (1 if flags & _USED_SECOND else 0)


def _shizhan_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """可选目标：任意**其他**存活角色（单选；座位序 = 玩家序）。"""
    del frame, chosen
    return _other_alive_seats(state, owner)


def _shizhan_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：本阶段不足两次 ∧ 场上还有别的存活角色。"""
    if ctx.card is not None or ctx.owner != ctx.subject:
        return current
    if ctx.scope is not CandidateScope.PLAY:
        return current
    if _phase_use_count(state, ctx.owner, ctx.skill_id) >= 2:
        return current
    if not _other_alive_seats(state, ctx.subject):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _shizhan_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：记一次本阶段已用，再让被指定者视为对华雄使用【决斗】。"""
    owner = frame.data["source"]
    target = int(answers["seat"][0])
    if not frame.data.get(_SHIZHAN_STARTED):
        if _phase_flags(host.state, owner, SHIZHAN) & _USED:
            _mark_phase(host.state, owner, SHIZHAN, _USED_SECOND)
        else:
            _mark_phase(host.state, owner, SHIZHAN, _USED)
        frame.data[_SHIZHAN_STARTED] = True
        host.start_use(
            pure_virtual_card(host.state, "duel"),
            source=target,
            targets=(owner,),
            use_timing=Timing.PHASE_PLAY_IDLE,
        )
    return None


SHIZHAN_FLOW = SkillFlow(
    name="shizhan",
    steps=(PickSeats(lo=1, hi=1, pool=_shizhan_targets, key="seat"),),
    effect=_shizhan_effect,
    # 次数由提交点自己数（见上）：执行器的 `_USED` 只表达"限一次"。
    once_per_phase=False,
)
"""【势斩】的流水线：选一名其他角色 → 其视为对华雄使用【决斗】（每阶段至多两次）。"""


# ---------------------------------------------------------------------------
# 貂蝉【离间】：弃 1 张牌 + 两名男性角色 → 视为其中一名对另一名使用【决斗】
# ---------------------------------------------------------------------------


def _lijian_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    player = state.player(ctx.subject)
    cards = (*player.hand, *player.equip)
    if not cards or not _lijian_partners(state, ctx.subject):
        return current
    # 票 01 / W4-2：候选**恒为一条骨架**（不再烘"牌 × 有序男性对"），且它立刻被引擎从
    # 已翻预发动的技能里删掉——弃哪张、谁是使用者、谁是目标由该版的 ``*LIJIAN_FLOW``
    # 三步清单表达（标准版先使用者、界版先目标，M6 评审）；故候选数与手牌数、场上男性数
    # 无关，``CANDIDATE_CAP`` 的降级路径对【离间】不再触发。
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _lijian_males(state: GameState, owner: int) -> tuple[int, ...]:
    """【离间】的可选男性角色（不含发动者自己；座位序 = 玩家序，确定性）。"""
    return tuple(
        p.seat
        for p in state.players
        if p.alive and p.gender is Gender.MALE and p.seat != owner
    )


def _lijian_partners(state: GameState, owner: int) -> dict[int, tuple[int, ...]]:
    """**合法的有序对**：使用者 → 可作为其【决斗】目标的男性（受害者一侧走真牌同一条链）。

    这张表是两步座位询问的**共同真源**，也承担"付不起就不进池"的旧语义：

    - 只为**至少有一个合法受害者**的男性开"使用者"这一问（否则第二步的池为空，
      参与者会收到一个没有候选的询问——AI 直接抛错）；
    - 受害者池直接取表里的那一项，故【空城】这类"不能成为目标"的锁定技照常出池。
    """
    duel = pure_virtual_card(state, "duel")
    males = _lijian_males(state, owner)
    return {
        user: victims
        for user in males
        if (
            victims := tuple(
                victim
                for victim in males
                if victim != user and victim in check_targets(state, user, duel)
            )
        )
    }


def _lijian_users(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """步 2 的池：**至少有一个合法受害者**的男性（座位序）。"""
    del frame, chosen
    return tuple(_lijian_partners(state, owner))


def _lijian_victims(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """步 3 的池：步 2 选中那名**使用者**的合法受害者（真牌 ``check_targets`` 链）。

    池依赖前序答案 ⇒ 从 :func:`~sgs.skills.flow._with_answers` 挂上的
    ``frame.data["preactivation_answers"]`` 读（清单渲染与校验器共用的唯一通道，W4-1）。
    没有前序答案（清单退化 / 尚未选使用者）⇒ 空池，宁可少给候选也不瞎猜。
    """
    del chosen
    answers = (getattr(frame, "data", {}) or {}).get("preactivation_answers") or {}
    users = answers.get("user") or ()
    if not users:
        return ()
    return _lijian_partners(state, owner).get(int(users[0]), ())


def _lijian_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """**界版**步 2 的池：**至少有一名合法使用者**的男性（能当【决斗】目标的人，座位序）。

    与标准版步 2（使用者池）是同一张 :func:`_lijian_partners` 表的两侧：标准版取**键**
    （使用者）、界版取**值的并集**（目标）。顺序 = 玩家序（表本身的键序也是玩家序）。
    """
    del frame, chosen
    targetable = {
        victim for victims in _lijian_partners(state, owner).values() for victim in victims
    }
    return tuple(seat for seat in _lijian_males(state, owner) if seat in targetable)


def _jie_lijian_users(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """**界版**步 3 的池：**已选中那名目标**的合法使用者（座位序）。

    池依赖前序答案 ⇒ 读 ``preactivation_answers`` 的 **``victim``** 那一栏（标准版读的是
    ``user`` 栏，两版的两次座位询问正好反过来）：§十九 L2212–L2214「依次选择两名男性其他
    角色，令**后者**视为对**前者**使用一张不能被响应的【决斗】」——第一次点的是"前者"
    （【决斗】的目标），第二次点的才是"后者"（使用者）。没有前序答案 ⇒ 空池。
    """
    del chosen
    answers = (getattr(frame, "data", {}) or {}).get("preactivation_answers") or {}
    targets = answers.get("victim") or ()
    if not targets:
        return ()
    target = int(targets[0])
    return tuple(
        user for user, victims in _lijian_partners(state, owner).items() if target in victims
    )


def _lijian_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：弃成本 → 使用者对其受害者发起【决斗】。

    三步都只收集：牌直到这里才搬进弃牌堆（取消 / 校验失败 ⇒ 手牌原样）。``_USED`` 由
    执行器的 ``on_commit`` 在 ``effect`` 之前落位（``once_per_phase=True``），故本函数
    不重复记账。

    **标准 / 界两版共用本函数**：界貂蝉（``frame.data["skill_id"] == JIE_LIJIAN``）把这条
    虚拟【决斗】标成**不可响应**（全场档 ``Tag.UNRESPONSIVE``）；标准版一字不改。两版的
    **选择序不同**（标准版先使用者、界版先目标），但答案键相同 ⇒ 这里读
    ``answers["user"]`` / ``answers["victim"]`` 不必分叉。
    """
    owner = frame.data["source"]
    user_seat = int(answers["user"][0])
    victim_seat = int(answers["victim"][0])
    if _move_to_discard(host, frame, answers["card"], owner=owner, key="lijian:cost") == "stay":
        return "stay"
    if frame.data.get(_LIJIAN_STARTED):
        return None
    frame.data[_LIJIAN_STARTED] = True
    # 使用者与目标已按同一条 ``check_targets`` 链定下，故这里不再重查"能否使用"的常规
    # 合法性，只走【决斗】自己的结算链——伤害来源随使用者（不是貂蝉）。
    duel = host.start_use(
        pure_virtual_card(host.state, "duel"),
        source=user_seat,
        targets=(victim_seat,),
        use_timing=Timing.PHASE_PLAY_IDLE,
    )
    if frame.data.get("skill_id") == JIE_LIJIAN:
        add_tag(duel, Tag.UNRESPONSIVE)
    return None


LIJIAN_FLOW = SkillFlow(
    name="lijian",
    steps=(
        PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=1, key="card"),
        PickSeats(lo=1, hi=1, pool=_lijian_users, key="user"),
        PickSeats(lo=1, hi=1, pool=_lijian_victims, key="victim"),
    ),
    effect=_lijian_effect,
)
"""【离间】的三步流水线（票 01 手写 → W4-2 声明式）：弃 1 张牌 → 使用者 → 目标。

顺序即语义：第一次点的是【决斗】**使用者**、第二次点的才是**目标**（有序性由步骤次序
承担，不靠按钮文案）。受害者池读前序答案（使用者），故清单按前序选项逐行下发
（:func:`~sgs.skills.flow.manifest` 的逐选项子池）。
"""


# ---------------------------------------------------------------------------
# 界貂蝉【离间】（M6 票 07）：**不能被响应**的虚拟【决斗】
#
# §十九 L2212–L2214：离间：出牌阶段限一次，你可以弃置一张牌，依次选择两名男性其他
# 角色，令后者视为对前者使用一张**不能被响应**的【决斗】。
#
# "不能被响应" = **响应窗口不产生**（否定分类第 4 层）：无懈询问不出现，决斗双方也都
# 不能打出【杀】。落点 = 帧标签袋的 ``Tag.UNRESPONSIVE``（全场档，与【铁骑】逐目标档
# 同一个标签、同一条读取面）：引擎的"生效前"窗口见到它就不再发无懈询问，决斗效果
# 见到它就不再发【杀】响应询问——两处都是**既有消费者**按标签跳过窗口，不是本技能
# 自己把结算短路。故【决斗】照常结算（受害者直接判负、受到 1 点伤害），"不可响应"
# 不是"抵消"。`06`（§5「程序化约定与偏离一手」）另记一条裁定：它**不**挡"生效时"类技能
# （如【谦逊】）的介入。
#
# 界版与标准版的**选择序相反**（M6 评审订正）：§十九 L2212–L2214「依次选择两名男性其他
# 角色，令**后者**视为对**前者**使用一张不能被响应的【决斗】」——第一次点的是【决斗】
# **目标**（前者）、第二次点的才是**使用者**（后者）；标准版 §九 的牌面不指定次序
# （本仓既有口径 = 先使用者、后目标）。故两版各一条 ``SkillFlow``：步骤键不变
# （``user`` / ``victim``），差别只在两次座位询问的次序与池的方向；**提交点仍是同一个**
# （``_lijian_effect`` 读同一套答案键，且界版多挂 ``Tag.UNRESPONSIVE``）。分步文案随
# 各自的 ``SkillDef.step_requirements`` 下发，界面因此画得出"先点目标、后点使用者"。
# ---------------------------------------------------------------------------

JIE_LIJIAN = hero_skill_id("jie_diaochan", "lijian")
"""界貂蝉【离间】的全限定技能 id（§十九 L2212–L2214）。"""

_LIJIAN_STARTED = "lijian_started"
"""帧私有：这次发动已经开出虚拟【决斗】（重入不重复开第二次）。"""

JIE_LIJIAN_FLOW = SkillFlow(
    name="jie_lijian",
    steps=(
        PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=1, key="card"),
        PickSeats(lo=1, hi=1, pool=_lijian_targets, key="victim"),
        PickSeats(lo=1, hi=1, pool=_jie_lijian_users, key="user"),
    ),
    effect=_lijian_effect,
)
"""界【离间】的三步流水线：弃 1 张牌 → **目标** → **使用者**（§十九 L2212–L2214 的次序）。

与 :data:`LIJIAN_FLOW` 只差两次座位询问的次序 / 池：第一次点的是【决斗】**目标**（令后者
对"前者"使用），第二次点的是**使用者**。受害者池（界版的步 2）取 ``_lijian_partners``
的值的并集，使用者池（界版步 3）读前序答案 ``victim``——清单的逐选项子池因此逐目标
下发。流水线名带 ``jie_`` 前缀，与界【仁德】/【突袭】同一口径（``_activation_belongs``
按流水线名判归属）。
"""


# ---------------------------------------------------------------------------
# 孙尚香【结姻】：弃 2 张手牌 + 一名已受伤男性 → 你与其各回复 1 点
#
# 票 03：本技能是 :class:`SkillFlow` 的**第三个实例**（【离间】手写、【青囊】声明式），
# 也是确认"``PickCards(lo, hi)`` 能表达恰好 2 张"的那一个：``lo=hi=2`` ⇒
# ``SelectionSpec(total=(2, 2))`` 只枚举恰好两张的组合。旧的"两维过闸降级为顺序多轮"
# 分支已删除（同一个技能不再有两种交互形状）；那批零调用原语也在 W6-06a 一并清掉。
# ---------------------------------------------------------------------------


def _jieyin_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """【结姻】的可选目标：**已受伤的男性**、存活、不含发动者自己，座位序。

    签名与 :data:`SeatPool` 对齐（``chosen`` 对单选无意义）。付不起就不进池：池为空时
    候选根本不产出（:func:`_jieyin_offers`），不会发出没有候选的询问。
    """
    del frame, chosen
    return tuple(
        p.seat
        for p in state.players
        if p.alive
        and p.seat != owner
        and p.gender is Gender.MALE
        and p.hp < p.hp_max
    )


def _jieyin_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    if len(state.player(ctx.subject).hand) < 2:
        return current
    if not _jieyin_targets(state, None, ctx.subject, ()):
        return current
    # 票 03：候选**恒为一条骨架**（与【离间】/【青囊】同姿态），不再烘"手牌对 × 目标"。
    # 弃哪两张、救谁改由发动后的步骤流水线问 ⇒ 候选数与手牌数、受伤男性数无关。
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


_JIEYIN_RECOVERED_KEY = "jieyin_recovered"
"""帧私有：本效果体**已经压过几张** ``RECOVER`` 帧（票 08）。

§3.1 的帧私有游标纪律：``_push_recover`` **立即**压帧、帧栈后进先出，故一个效果体里连压
两张会让**后压的**那张先结算；游标记「这次发动已经压到第几张」，续跑据此接着压下一张，
且守卫在压帧**之前**落位（续跑不会把同一张重压一遍）。它同时是
:func:`_jieyin_effect` 里 ``targets`` 元组的下标——"压到第几张"与"压给谁"读同一份声明。
"""


def _jieyin_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：搬两张成本进弃牌堆，再**逐张**压两个回复帧（``_USED`` 已由执行器在调用前落位）。

    票 08（``CODING_STANDARDS.md`` §3.1）：牌面写的是"令一名已受伤的男性角色回复 1 点体力，
    **你**回复 1 点体力"，故目标先结算。旧实现一个效果体里连压两张帧再 ``return None``：
    帧栈后进先出 ⇒ 自己先回复，且"压完第一张帧之后又压第二张"正是 §3.1 禁止的形状。
    修法同白银狮子：压一张 → ``return "stay"`` → 续跑按 :data:`_JIEYIN_RECOVERED_KEY`
    接着压第二张 → ``return "stay"``。

    两张回复的**目标按次序声明在 ``targets`` 里**（第一张给选中的男性角色，第二张给自己）：
    游标是这条声明上的下标，故"压到第几张"与"压给谁"读同一份数据，不是一个计数器加一条
    三元表达式。
    """
    owner = frame.data["source"]
    seats = answers["seat"]
    cards = answers["card"]
    assert isinstance(seats, tuple) and isinstance(cards, tuple)
    if _move_to_discard(host, frame, cards, owner=owner, key="jieyin:cost") == "stay":
        return "stay"
    targets = (int(seats[0]), owner)
    pushed = int(frame.data.get(_JIEYIN_RECOVERED_KEY, 0))
    if pushed >= len(targets):
        return None
    frame.data[_JIEYIN_RECOVERED_KEY] = pushed + 1
    _push_recover(host, frame, source=owner, target=targets[pushed])
    return "stay"


JIEYIN_FLOW = SkillFlow(
    name="jieyin",
    steps=(
        PickCards(zones=(Zone.HAND,), lo=2, hi=2, key="card"),
        PickSeats(lo=1, hi=1, pool=_jieyin_targets, key="seat"),
    ),
    effect=_jieyin_effect,
)
"""【结姻】的两步流水线：一并弃恰好 2 张手牌 → 一名已受伤男性 → 双方各回复 1 点（票 03）。"""


# ---------------------------------------------------------------------------
# 界孙尚香【结姻】（M6 票 17）：一条手牌 或 一张装备牌置入其装备区；较高的摸一张、
# 较低的回复 1 点
#
# §十九 L2189–L2192：
#
#     结姻：出牌阶段限一次，你可以选择一名男性角色，然后你弃置一张手牌或将一张装备牌
#           置入其装备区。若如此做，你与其之中体力值较高的角色摸一张牌，体力值较低的
#           角色回复 1 点体力。
#
# 与标准版是**两条完全不同的收益**（标准 = 弃两张手牌、双方各回复 1 点），故不复用标准
# 流水线：三步为 ``PickSeats``（任意男性角色，**不要求已受伤**）→ ``PickOption``
# （弃一张手牌 / 置一张装备牌，闭集随目标现算）→ ``PickCards``（按选项收窄成本面）。
# 两处裁定写进 `06`（§5「程序化约定与偏离一手」）/ `## Answer`：
#
# - **"一张装备牌" = 手牌 + 装备区里的装备牌**（票 23 用户裁定，推翻票 17 的"只从手牌
#   取"）：牌面没写区域限定，按默认读法（一）③，已经装备着的牌也算一张装备牌。
# - **体力值相同 ⇒ 两种效果都不执行**：牌面写的是"较高的"与"较低的"，相等时两个角色
#   都不满足。发动因此纯亏成本——玩家可以不作这次发动。
# ---------------------------------------------------------------------------

JIE_JIEYIN = hero_skill_id("jie_sunshangxiang", "jieyin")
"""界孙尚香【结姻】的全限定技能 id（§十九 L2189–L2192）。"""

JIE_JIEYIN_DISCARD = "jieyin_discard_hand"
JIE_JIEYIN_EQUIP = "jieyin_place_equipment"
"""界【结姻】的二选一选项：弃一张手牌 / 将一张装备牌置入其装备区。"""

JIE_JIEYIN_OPTIONS: tuple[str, str] = (JIE_JIEYIN_DISCARD, JIE_JIEYIN_EQUIP)
"""选项闭集（顺序 = 牌面两条分支的顺序；**此刻可选哪几项**由 :func:`_jie_jieyin_options`
按目标现算——装备区空槽决定第 2 项存不存在）。"""

_JIE_JIEYIN_PAID_KEY = "jie_jieyin_paid"
_JIE_JIEYIN_DONE_KEY = "jie_jieyin_done"


def _jie_jieyin_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """可选目标：存活的**男性**其他角色，且**下一步至少有一个选项**。

    不要求已受伤；但第 2 步的闭集（:func:`_jie_jieyin_options`：弃一张手牌 / 往目标的
    空槽置一张装备牌）谁也走不通的座位**不进池**。票 01：只按"存活男性"发座位会把
    "第 2 步走不通"的人发给参与者——AI 按关系选中他，``_flow_step_option`` 见空闭集
    取消整条流水线，而取消零副作用、不落 ``_USED``，同一出牌阶段就会无限重来。
    """
    del frame, chosen
    return tuple(
        p.seat
        for p in state.players
        if p.alive
        and p.seat != owner
        and p.gender is Gender.MALE
        and _jie_jieyin_options(state, owner, p.seat)
    )


def _jie_jieyin_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    # 目标池已按"第 2 步走得通"收口（:func:`_jie_jieyin_targets`）⇒ 池空即无候选。
    if not _jie_jieyin_targets(state, None, ctx.subject, ()):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _filter_face(
    state: GameState, face: tuple[tuple[Slot, ...], tuple[int, ...]], keep: Any
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """按实体牌谓词收窄一份选择面（槽位与 id 同步过滤，二者保持同序）。"""
    slots, card_ids = face
    kept = [
        (slot, card_id)
        for slot, card_id in zip(slots, card_ids, strict=True)
        if keep(state.card(card_id))
    ]
    return tuple(slot for slot, _ in kept), tuple(card_id for _, card_id in kept)


def _jie_jieyin_slot_free(state: GameState, target: int, card: Any) -> bool:
    """``target`` 的装备区里，``card`` 这个槽位**空着**吗（"置入"不能替换已有装备）。

    牌面写的是"**置入**其装备区"：与"使用装备牌"一样落在空槽上，没有"顶替"这一说。
    目标同槽已有牌 ⇒ 这张牌不是合法成本（用户裁定，票 23）。
    """
    return all(
        state.card(card_id).slot is not card.slot
        for card_id in state.player(target).equip
    )


def _jie_jieyin_equipment_cards(state: GameState, owner: int, target: int) -> tuple[int, ...]:
    """可"置入"目标的装备牌：**手牌 + 装备区**里的装备牌，且目标的同槽**空着**。

    "一张**装备牌**"按默认读法（一）③ = 手牌 + 装备区里的装备牌（用户裁定，票 23）：
    已经装备着的牌也算一张装备牌；但"置入"只能进空槽（:func:`_jie_jieyin_slot_free`），
    故目标同槽已有牌的那几张**不进候选**（非法选项，而不是"替换"）。
    """
    player = state.player(owner)
    return tuple(
        card_id
        for card_id in (*player.hand, *player.equip)
        if (card := state.card(card_id)).slot is not None
        and _jie_jieyin_slot_free(state, target, card)
    )


def _jie_jieyin_options(state: GameState, owner: int, target: int) -> tuple[str, ...]:
    """对**这名目标**此刻走得通的选项：弃手牌（有手牌）/ 置装备（有空槽可放）。

    选项集依赖目标（装备区空槽）⇒ 清单与询问都按前序答案现算
    （:class:`~sgs.skills.flow.PickOption` 的可调用档）。
    """
    options: list[str] = []
    if state.player(owner).hand:
        options.append(JIE_JIEYIN_DISCARD)
    if _jie_jieyin_equipment_cards(state, owner, target):
        options.append(JIE_JIEYIN_EQUIP)
    return tuple(options)


def _jie_jieyin_option_choices(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """第 2 步的闭集：按**第 1 步选中的目标**现算（:func:`_jie_jieyin_options`）。"""
    del frame
    return _jie_jieyin_options(state, owner, int(answers["seat"][0]))


def _jie_jieyin_excluded(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[int, ...]:
    """第 3 步的黑名单：**按第 2 步的选项**收窄成本面（"唯一判据"仍然只有这一份）。

    - 弃手牌 ⇒ 装备区的牌一律不可选（牌面写的是"弃置一张**手牌**"）；
    - 置装备 ⇒ 非装备牌不可选，且目标同槽已有牌的那几张装备牌也不可选（不能替换）。
    """
    del frame
    target = int(answers["seat"][0])
    player = state.player(owner)
    if str(answers["option"]) == JIE_JIEYIN_DISCARD:
        return tuple(player.equip)
    legal = set(_jie_jieyin_equipment_cards(state, owner, target))
    return tuple(
        card_id
        for card_id in (*player.hand, *player.equip)
        if card_id not in legal
    )


def _jie_jieyin_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：按第 2 步的选项付那张牌 → 较高的摸一张、较低的回复 1 点。

    三步的选择都由流水线的**步骤**问过（预发动清单因此画得出完整的三步），这里只结算：
    付成本 → 体力比较 → 两个效果之一。搬牌可能内联派发"失去牌后"（她自己的【枭姬】/
    【连营】）并挂起，故"已付 / 已收尾"各一个帧私有游标。
    """
    state = host.state
    owner = frame.data["source"]
    target = int(answers["seat"][0])
    if not frame.data.get(_JIE_JIEYIN_PAID_KEY):
        card_id = int(answers["card"][0])
        if str(answers["option"]) == JIE_JIEYIN_DISCARD:
            control = host.move_in_frame(
                frame,
                state.refs((card_id,)),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=owner,
                actor=owner,
            )
        else:
            control = host.put_equipment_card(
                frame, holder=target, card=state.card(card_id), actor=owner
            )
        if control == "stay":
            return "stay"
        frame.data[_JIE_JIEYIN_PAID_KEY] = True
    if frame.data.get(_JIE_JIEYIN_DONE_KEY):
        return None
    frame.data[_JIE_JIEYIN_DONE_KEY] = True
    mine = state.player(owner)
    theirs = state.player(target)
    if mine.hp > theirs.hp:
        host.draw_for_hand(frame, owner, 1)
        if host.finished:
            return None
        _push_recover(host, frame, source=owner, target=target)
        return "stay"
    if mine.hp < theirs.hp:
        if not host.draw_for_hand(frame, target, 1):
            return None
        _push_recover(host, frame, source=owner, target=owner)
        return "stay"
    return None


JIE_JIEYIN_FLOW = SkillFlow(
    name="jie_jieyin",
    steps=(
        PickSeats(lo=1, hi=1, pool=_jie_jieyin_targets, key="seat"),
        # 「然后你弃置一张手牌**或**将一张装备牌置入其装备区」：选项集随目标变（空槽）。
        PickOption(
            options=_jie_jieyin_option_choices, optional=False, key="option"
        ),
        # 成本面按选项收窄（弃手牌 / 置装备），黑名单是**唯一**的过滤判据。
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            key="card",
            exclude_cards=_jie_jieyin_excluded,
        ),
    ),
    effect=_jie_jieyin_effect,
)
"""界【结姻】的三步流水线：选一名男性角色 → 二选一 → 按选项付一张牌 → 比较体力后结算。"""
# ---------------------------------------------------------------------------
# 界大乔【国色】（M6 票 17）：二选一（方块牌当【乐不思蜀】/ 弃方块并弃场上一张
# 【乐不思蜀】），选择结束后摸一张
#
# §十九 L2175–L2178：
#
#     国色：出牌阶段限一次，你可以选择一项：1. 将一张方块牌当【乐不思蜀】使用；
#           2. 弃置一张方块牌，并弃置场上的一张【乐不思蜀】。选择结束后，你摸一张牌。
#
# 与标准版（纯转化）不再是同一条技能：选项 2 是"弃两张牌"，选项 1 还要多摸一张，故本技能
# 是一个**主动技**（不是转化技）：方块牌的选择、产物牌的使用、场上【乐不思蜀】的选择都由
# 提交点手写（选项闭集随局面变化：选项 2 只在场上真有【乐不思蜀】时出现）。
#
# **"选择结束后"的先后**（票面 `## 已知歧义` 的第一条，`06`（§5「程序化约定与偏离一手」）登记）：所选那一项**完全
# 执行完之后**才摸——选项 1 是【乐不思蜀】的使用**结算完**（含无懈窗口），选项 2 是两张牌
# 都弃完之后。不是"点完二选一就摸"：那会让摸到的【无懈可击】立刻能响应自己的【乐不思蜀】。
#
# 选项 1 的产物牌走既有的**转化延时锦囊**路径（:func:`~sgs.skills.virtual.transformed_card`
# ＋ ``start_use``）：来源方块牌因此照常进判定区、在 :attr:`GameState.virtual_judge` 里留下
# "它代表哪张虚拟牌"的映射，判定阶段按虚拟牌结算。
# ---------------------------------------------------------------------------

JIE_GUOSE = hero_skill_id("jie_daqiao", "guose")
"""界大乔【国色】的全限定技能 id（§十九 L2175–L2178）。"""

JIE_GUOSE_USE = "guose_use_indulgence"
JIE_GUOSE_DISCARD_FIELD = "guose_discard_indulgence"
"""界【国色】的二选一选项：方块牌当【乐不思蜀】使用 / 弃方块牌并弃场上一张。"""

JIE_GUOSE_OPTIONS: tuple[str, str] = (JIE_GUOSE_USE, JIE_GUOSE_DISCARD_FIELD)
"""选项闭集（顺序 = 牌面两条分支的顺序）。"""

JIE_GUOSE_ASK_OPTION = "hero.jie_daqiao.guose:option"
_ASK_JIE_GUOSE_CARD = "hero.jie_daqiao.guose.card"
JIE_GUOSE_ASK_SEAT = "hero.jie_daqiao.guose:seat"
_ASK_JIE_GUOSE_FIELD = "hero.jie_daqiao.guose.field"

_JIE_GUOSE_OPTION_KEY = "jie_guose_option"
_JIE_GUOSE_CARD_KEY = "jie_guose_card"
_JIE_GUOSE_TARGET_KEY = "jie_guose_target"
_JIE_GUOSE_FIELD_KEY = "jie_guose_field"
_JIE_GUOSE_STARTED_KEY = "jie_guose_started"
_JIE_GUOSE_DONE_KEY = "jie_guose_done"


def _diamond_ids(state: GameState, owner: int) -> tuple[int, ...]:
    """手牌 ∪ 装备区里的**方块牌**（区域默认：牌面只写"一张方块牌"）。"""
    player = state.player(owner)
    return tuple(
        card_id
        for card_id in (*player.hand, *player.equip)
        if state.card(card_id).suit == Suit.DIAMOND.value
    )


def _field_indulgence_ids(state: GameState) -> tuple[int, ...]:
    """**场上**（全场判定区）的【乐不思蜀】实体牌 id。

    判定区只装实体牌，【国色】转化出来的那张按 :attr:`GameState.virtual_judge` 的映射读
    牌名——与"判定区同名"检查同一条判据。
    """
    cards: list[int] = []
    for player in state.players:
        for card_id in player.judge:
            effective = state.virtual_judge.get(card_id) or state.card(card_id)
            if effective.type == "indulgence":
                cards.append(card_id)
    return tuple(cards)


def _jie_guose_options(state: GameState, owner: int) -> tuple[str, ...]:
    """此刻走得通的选项：选项 1 要有方块牌且**有合法目标**；选项 2 还要场上有【乐不思蜀】。"""
    diamonds = _diamond_ids(state, owner)
    if not diamonds:
        return ()
    options: list[str] = []
    if check_targets(state, owner, transformed_card(state, (diamonds[0],), "indulgence")):
        options.append(JIE_GUOSE_USE)
    if _field_indulgence_ids(state):
        options.append(JIE_GUOSE_DISCARD_FIELD)
    return tuple(options)


def _jie_guose_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    if not _jie_guose_options(state, ctx.subject):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _jie_guose_diamond_face(
    host: Any, owner: int
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """选项 1 / 2 的"一张方块牌"面：手牌 ∪ 装备区、只留方块。"""
    face = host.selection_face(owner, (Zone.HAND, Zone.EQUIP), reveal_hand=True)
    return _filter_face(host.state, face, lambda card: card.suit == Suit.DIAMOND.value)


def _jie_guose_field_face(
    host: Any,
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """选项 2 的"场上一张【乐不思蜀】"面：全场判定区的可选取牌合在一张面里。"""
    state = host.state
    slots: list[Slot] = []
    card_ids: list[int] = []
    for player in state.players:
        face_slots, face_ids = host.selection_face(player.seat, (Zone.JUDGE,))
        for slot, card_id in zip(face_slots, face_ids, strict=True):
            effective = state.virtual_judge.get(card_id) or state.card(card_id)
            if effective.type == "indulgence":
                slots.append(slot)
                card_ids.append(card_id)
    return tuple(slots), tuple(card_ids)


def _jie_guose_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：二选一 → 执行所选分支 → 摸一张（"选择结束后"）。"""
    del answers
    state = host.state
    owner = frame.data["source"]
    if frame.data.get(_JIE_GUOSE_DONE_KEY):
        return None
    option = frame.data.get(_JIE_GUOSE_OPTION_KEY)
    if option is None:
        options = _jie_guose_options(state, owner)
        if not options:
            frame.data[_JIE_GUOSE_DONE_KEY] = True
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.PHASE_PLAY_IDLE,
            skill_id=JIE_GUOSE_ASK_OPTION,
            options=options,
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        option = str(answer.choice)
        frame.data[_JIE_GUOSE_OPTION_KEY] = option
    assert isinstance(option, str)
    if option == JIE_GUOSE_USE:
        if _jie_guose_use_branch(host, frame, owner) == "stay":
            return "stay"
    else:
        if _jie_guose_discard_branch(host, frame, owner) == "stay":
            return "stay"
    frame.data[_JIE_GUOSE_DONE_KEY] = True
    host.draw_for_hand(frame, owner, 1)
    return None


def _jie_guose_use_branch(host: Any, frame: Any, owner: int) -> Any:
    """选项 1：选一张方块牌 → 选【乐不思蜀】目标 → 作为转化延时锦囊使用。"""
    state = host.state
    card_id = frame.data.get(_JIE_GUOSE_CARD_KEY)
    if card_id is None:
        picked = host.ask_selection(
            frame,
            owner=_ASK_JIE_GUOSE_CARD,
            key="diamond",
            actor=owner,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, 1)),
            face=_jie_guose_diamond_face(host, owner),
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            frame.data[_JIE_GUOSE_DONE_KEY] = True
            return None
        card_id = int(picked[0])
        frame.data[_JIE_GUOSE_CARD_KEY] = card_id
    assert isinstance(card_id, int)
    card = transformed_card(state, (card_id,), "indulgence")
    target = frame.data.get(_JIE_GUOSE_TARGET_KEY)
    if target is None:
        pool = check_targets(state, owner, card)
        if not pool:
            frame.data[_JIE_GUOSE_DONE_KEY] = True
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.PHASE_PLAY_IDLE,
            skill_id=JIE_GUOSE_ASK_SEAT,
            options=tuple(f"seat:{seat}" for seat in pool),
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        target = int(str(answer.choice).split(":", 1)[1])
        frame.data[_JIE_GUOSE_TARGET_KEY] = target
    if frame.data.get(_JIE_GUOSE_STARTED_KEY):
        return None
    frame.data[_JIE_GUOSE_STARTED_KEY] = True
    host.start_use(
        card, source=owner, targets=(int(target),), use_timing=Timing.PHASE_PLAY_IDLE
    )
    return "stay"  # USE 子帧出栈后回到提交点摸那一张（"选择结束后"）


def _jie_guose_discard_branch(host: Any, frame: Any, owner: int) -> Any:
    """选项 2：选一张方块牌 + 场上一张【乐不思蜀】→ 两张都弃置。"""
    state = host.state
    card_id = frame.data.get(_JIE_GUOSE_CARD_KEY)
    if card_id is None:
        picked = host.ask_selection(
            frame,
            owner=_ASK_JIE_GUOSE_CARD,
            key="diamond",
            actor=owner,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, 1)),
            face=_jie_guose_diamond_face(host, owner),
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            frame.data[_JIE_GUOSE_DONE_KEY] = True
            return None
        card_id = int(picked[0])
        frame.data[_JIE_GUOSE_CARD_KEY] = card_id
    field_card = frame.data.get(_JIE_GUOSE_FIELD_KEY)
    if field_card is None:
        picked = host.ask_selection(
            frame,
            owner=_ASK_JIE_GUOSE_FIELD,
            key="indulgence",
            actor=owner,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(zones=(Zone.JUDGE,), total=(1, 1)),
            face=_jie_guose_field_face(host),
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            frame.data[_JIE_GUOSE_DONE_KEY] = True
            return None
        field_card = int(picked[0])
        frame.data[_JIE_GUOSE_FIELD_KEY] = field_card
    for cost_id in (int(card_id), int(field_card)):
        control = host.move_in_frame(
            frame,
            state.refs((cost_id,)),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    return None


JIE_GUOSE_FLOW = SkillFlow(
    name="jie_guose",
    steps=(),
    effect=_jie_guose_effect,
)
"""界【国色】的零步流水线：提交点里手写二选一 / 选牌 / 使用 / 摸一张。"""


# ---------------------------------------------------------------------------
# 黄盖【苦肉】（M6 票 03）：出牌阶段，你可以失去 1 点体力，然后摸两张牌
#
# §九 L1031 的**标准版**原文没有"限一次"、也不弃置牌：``once_per_phase=False`` ⇒
# 同一出牌阶段可反复发动（界黄盖的"限一次 + 弃一张"随票 17，不在这里）。
# 形态 = **裸发动**（``picks == ()`` / ``choices == ()``，候选直接进 ``legal_actions``，
# 不走预发动清单）+ 失去体力 + 摸两张。
#
# **失去体力 ≠ 受到伤害**：苦肉不压 DAMAGE 帧、不过 DAMAGE_* 与伤害修正链，只压
# ``LIFE_DECREASE`` 帧走唯一的体力写入点（该帧自带濒死插入——1 点体力发动时真的进
# 求桃，而不是静默停在 0 血）。体力扣完后才摸两张，故被救回之后才摸（"然后"）。
# ---------------------------------------------------------------------------

KUROU_DRAW = 2
"""【苦肉】摸牌数（§九 L1031：摸两张牌）。"""

_KUROU_LOST = "kurou_lost"
"""帧私有游标：本次发动的那次"失去体力"已经压过帧（重入时只补摸牌）。"""


def _kurou_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选**恒为一条骨架**、载荷全空；没有"出牌阶段限一次"（``once_per_phase=False``）。"""
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _kurou_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：先压 LIFE_DECREASE 帧失去 1 点体力，出栈后再摸两张。

    压帧后返回 ``"stay"``，重入（子帧已出栈）走到摸牌段——濒死 / 求桃 / 死亡都在子帧里
    结算完，摸牌因此严格排在"然后"之后。帧私有 ``_KUROU_LOST`` 是重入守卫。
    """
    del answers
    owner = frame.data["source"]
    if not frame.data.get(_KUROU_LOST):
        frame.data[_KUROU_LOST] = True
        host.push_frame(
            FrameKind.LIFE_DECREASE,
            {
                "target": owner,
                "source": None,  # 失去体力：没有伤害来源
                "amount": 1,
            },
            timing=Timing.LIFE_DECREASE_BEFORE,
        )
        return "stay"
    if not host.state.player(owner).alive:
        # 失去体力把自己送进濒死且没救回：角色已阵亡，技能就此结束——**不能**再给
        # 阵亡者摸牌（fuzz 的"已阵亡仍有手牌"不变量先抓到过这一条）。
        return None
    host.draw_for_hand(frame, owner, KUROU_DRAW)
    return None


KUROU_FLOW = SkillFlow(
    name="kurou",
    steps=(),
    effect=_kurou_effect,
    once_per_phase=False,
)
"""【苦肉】的零步流水线：裸发动 → 失去 1 点体力 → 摸两张（**无**出牌阶段限一次）。"""


# ---------------------------------------------------------------------------
# 界黄盖【苦肉】（M6 票 17）：出牌阶段限一次，弃置一张牌，失去 1 点体力（**不摸牌**）
#
# §十九 L2162–L2163：
#
#     苦肉：出牌阶段限一次，你可以弃置一张牌，失去 1 点体力。
#
# 与标准版（票 03）三处不同：限一次（``_USED`` 位）、多一个"弃置一张牌"的成本、**没有**
# 摸两张——摸牌整段搬去同名的【诈降】（锁定技，失去 1 点体力后摸三张），故本技能的提交点
# 只做"弃牌 → 压 LIFE_DECREASE 帧"两件事。"失去体力 ≠ 受到伤害"仍与标准版同一条落点。
# ---------------------------------------------------------------------------

JIE_KUROU = hero_skill_id("jie_huanggai", "kurou")
"""界黄盖【苦肉】的全限定技能 id（§十九 L2162–L2163）。"""

_JIE_KUROU_LOST = "jie_kurou_lost"
"""帧私有游标：本次发动的"失去体力"子帧已经压过（重入只收尾，不重复压帧）。"""

_JIE_KUROU_MOVED = "jie_kurou_moved"
"""帧私有游标：成本已经搬完（子帧出栈后重入时不得再搬一次）。"""


def _jie_kurou_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：出牌阶段限一次 ∧ 手里 / 装备区至少有一张可弃的牌。"""
    if not _can_offer(state, ctx):
        return current
    player = state.player(ctx.subject)
    if not player.hand and not player.equip:
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _jie_kurou_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：弃一张牌（按来源区），再压 LIFE_DECREASE 帧失去 1 点体力。"""
    owner = frame.data["source"]
    cards = answers["card"]
    assert isinstance(cards, tuple)
    if not frame.data.get(_JIE_KUROU_MOVED):
        if _move_to_discard(host, frame, cards, owner=owner, key="jie_kurou:cost") == "stay":
            return "stay"
        frame.data[_JIE_KUROU_MOVED] = True
    if frame.data.get(_JIE_KUROU_LOST):
        return None
    frame.data[_JIE_KUROU_LOST] = True
    host.push_frame(
        FrameKind.LIFE_DECREASE,
        {
            "target": owner,
            "source": None,  # 失去体力：没有伤害来源
            "amount": 1,
        },
        timing=Timing.LIFE_DECREASE_BEFORE,
    )
    return "stay"


JIE_KUROU_FLOW = SkillFlow(
    name="jie_kurou",
    steps=(
        PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=1, key="card"),
    ),
    effect=_jie_kurou_effect,
)
"""界【苦肉】的单步流水线：弃一张牌 → 失去 1 点体力（出牌阶段限一次，**不摸牌**）。"""


# ---------------------------------------------------------------------------
# 典韦移动版【强袭】（huo-dianwei-mobile 票 01）：失去 1 点体力或弃置一张武器牌，
# 然后对你攻击范围内的一名其他角色造成 1 点伤害
#
# 牌面（用户 `D:\SGS_HEROS\神话再临-火包\典韦\技能.txt`，逐字）：
#
#     出牌阶段限一次，你可以失去1点体力或者弃置一张武器牌，然后对你攻击范围内的一名
#     其他角色造成1点伤害。
#
# OL 版是**另一条技能**（限两次 / 没有攻击范围 / 每名目标每回合一次），本仓按移动版
# 实现（huo-dianwei-mobile 票 01 §5 R1：账本「典韦移动版的平台差异（A1）」行）。
#
# 四条落地判据（票面 §5 R2–R7）：
# - **代价与目标的先后**：「然后」⇒ 先付代价、再定目标；目标池按**代价付掉之后**的
#   攻击范围算（`targets_within_attack_range(..., excluding=cost)`，与【流离】W4-2
#   同一条判据）——弃掉自己唯一的武器后范围回落，够不到的目标不可选。
# - **条件步住在 effect 里**：两条代价分支的后续步骤不同（弃武器要选牌、失去体力不要），
#   而 `SkillFlow.steps` 是定长元组、任一步"没有候选"的语义是**取消整条流水线**
#   ⇒ 选项步留在流水线、选武器牌 / 选目标 / 付代价住在 `effect`（界【国色】的同一姿态）。
# - **次数在真提交点落**：`once_per_phase=False` + 手写 `_mark_phase`（【势斩】的姿态）——
#   执行器的 `on_commit` 跑在 `effect` **之前**，而本技能的 effect 里还有可取消的手写步骤，
#   那样"选了代价却中途取消"也会吃掉阶段位（违反"取消零副作用"）。
# - **取消零副作用**：选武器牌 / 选目标任一步没有候选 ⇒ 整条取消，代价一张都不付
#   （阶段位不落、牌不搬、体力不失）。
# ---------------------------------------------------------------------------

QIANGXI = hero_skill_id("dianwei_mobile", "qiangxi")
"""典韦移动版【强袭】的全限定技能 id。"""

QIANGXI_LOSE_HP = "qiangxi_lose_hp"
"""【强袭】代价选项 1：失去 1 点体力。"""

QIANGXI_DISCARD_WEAPON = "qiangxi_discard_weapon"
"""【强袭】代价选项 2：弃置一张武器牌（没有武器牌时这一项不出现）。"""

_ASK_QIANGXI_WEAPON = "hero.dianwei_mobile.qiangxi.weapon"
"""手写询问位置：弃置一张武器牌（逐处唯一）。"""

_QIANGXI_ASK_TARGET = "hero.dianwei_mobile.qiangxi:target"
"""手写询问位置：选目标（投影读冒号后的 `target` 取逐步文案）。"""

_QIANGXI_CARD = "qiangxi_card"
"""帧私有：弃武器分支选中的那张武器牌（续跑不重问）。"""

_QIANGXI_TARGET = "qiangxi_target"
"""帧私有：已选定的目标座位（续跑不重问）。"""

_QIANGXI_PAID = "qiangxi_paid"
"""帧私有：代价已经付出去（搬牌 / 失血帧都可能挂起，重入不得再付一次）。"""

_QIANGXI_DONE = "qiangxi_done"
"""帧私有：伤害子帧已经压过（重入只收尾）。"""


def _qiangxi_is_weapon(state: GameState, card: Any) -> bool:
    """这张牌是不是**武器牌**：按槽位种类判（不按牌名 id 白名单）。"""
    return card.slot is SlotKind.WEAPON


def _qiangxi_weapon_cards(state: GameState, owner: int) -> tuple[int, ...]:
    """可弃的武器牌：**手牌 + 装备区**里的武器牌（默认读法（一）②③）。"""
    player = state.player(owner)
    return tuple(
        card_id
        for card_id in (*player.hand, *player.equip)
        if _qiangxi_is_weapon(state, state.card(card_id))
    )


def _qiangxi_discardable_weapons(state: GameState, owner: int) -> tuple[int, ...]:
    """可弃、且**弃掉之后仍够得着人**的武器牌（"付不起就不进候选"）。

    弃武器是**代价**：范围按付完之后重算（:func:`_qiangxi_target_pool` 的 ``excluding``）。
    若某张武器正是唯一撑起范围的那张，而剩下的距离又超过无武器范围 **1**，弃掉它就没有
    目标可选——那一档是**死路**：结算整条取消、阶段位不落，而候选还在 ⇒ 人工/AI 会反复
    发动同一条。【强袭】在 2026-11-05 的 128 局胜率批（seed 5003）上真撞到这条死循环：
    典韦装备【丈八蛇矛】（范围 3）、唯一对手距 2（挂了 +1 坐骑），弃矛后范围回落 1 ⇒ 反复
    取消。收窄成"弃后仍有人可打"既保住 O1 的收窄姿态，也把死路从**候选**这一层拿掉。

    判定与目标池同源：两处都读 :func:`_qiangxi_target_pool`，技能层不另抄距离公式。
    """
    return tuple(
        card_id
        for card_id in _qiangxi_weapon_cards(state, owner)
        if _qiangxi_target_pool(state, owner, (card_id,))
    )


def _qiangxi_weapon_face(host: Any, owner: int) -> Any:
    """弃武器面的**收窄**：自己的手牌 + 装备区，再按"是不是武器牌"过滤（槽位与 id 同序）。

    过滤集与 :func:`_qiangxi_options` **同一份**（``_qiangxi_discardable_weapons``）：选项
    承诺"弃了还能打"，选面就不该给出一张弃了没目标的武器——两边各写一份判据就会让
    选项与选面漂移。
    """
    slots, card_ids = host.selection_face(
        owner, (Zone.HAND, Zone.EQUIP), reveal_hand=True
    )
    discardable = set(_qiangxi_discardable_weapons(host.state, owner))
    kept = [
        (slot, card_id)
        for slot, card_id in zip(slots, card_ids, strict=True)
        if card_id in discardable
    ]
    return tuple(slot for slot, _ in kept), tuple(card_id for _, card_id in kept)


def _qiangxi_target_pool(
    state: GameState, owner: int, cost: tuple[int, ...]
) -> tuple[int, ...]:
    """目标池：**代价付掉之后**仍在你攻击范围内的一名其他存活角色。

    ``cost`` = 这次要弃掉的武器牌（失去体力那一档传空元组）：范围由
    :func:`~sgs.effects.targets_within_attack_range` 的 ``excluding`` 用同一个公式重算，
    不在技能层抄一份"没有武器 = 1"。【流离】（W4-2）是同一个判据的先例。
    """
    return targets_within_attack_range(state, owner, excluding=cost)


def _qiangxi_options(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """代价选项的闭集（可调用档）：**没有武器牌（或弃掉它也没目标）时，那一项不出现**。

    「没有武器牌」是第一层 O1 收窄；第二层是"弃了也够不着人"——死路不进候选，见
    :func:`_qiangxi_discardable_weapons`。失去体力恒可付（1 体力也能失去、进濒死），
    故本步恒有候选。【神速】的 ``_shensu_options`` 是同一个收窄姿态。
    """
    del frame, answers
    if not _qiangxi_discardable_weapons(state, owner):
        return (QIANGXI_LOSE_HP,)
    return (QIANGXI_LOSE_HP, QIANGXI_DISCARD_WEAPON)


def _qiangxi_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：自己的出牌阶段 ∧ 本阶段没用过 ∧ 攻击范围内至少一名其他存活角色。

    次数位不走 ``_can_offer`` 的 ``once_per_phase``：本技能在**提交点**才落位
    （``once_per_phase=False``），故"本阶段是否已用过"在这里显式读一次。
    """
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    if _phase_flags(state, ctx.owner, ctx.skill_id) & _USED:
        return current
    if not _qiangxi_target_pool(state, ctx.owner, ()):
        # 攻击范围内一个其他存活角色都没有：1 点伤害无处可去 ⇒ 不产出候选
        # （总纪律 1：不发没有候选的询问）。
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _qiangxi_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：选武器牌（仅那一档）→ 选目标 → 付代价 → 造成 1 点伤害。

    段游标：``_QIANGXI_CARD`` / ``_QIANGXI_TARGET``（选择只问一次）、``_QIANGXI_PAID``
    （代价只付一次）、``_QIANGXI_DONE``（伤害只压一次）。**阶段位与第一笔代价同一个
    提交点**：走到这里之前的任何取消（没有武器牌可挑 / 弃掉武器后没有目标）都不落位、
    不搬牌、不失血。
    """
    state = host.state
    owner = frame.data["source"]
    discard_weapon = str(answers["option"]) == QIANGXI_DISCARD_WEAPON
    card: int | None = frame.data.get(_QIANGXI_CARD)
    if discard_weapon and card is None:
        picked = host.ask_selection(
            frame,
            owner=_ASK_QIANGXI_WEAPON,
            key="qiangxi:weapon",
            actor=owner,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, 1)),
            build_face=lambda: _qiangxi_weapon_face(host, owner),
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            # 面为空（防御分支：入口已保证有武器牌）⇒ 整条取消、零副作用。
            return None
        card = int(picked[0])
        frame.data[_QIANGXI_CARD] = card
    cost: tuple[int, ...] = (card,) if discard_weapon and card is not None else ()
    target = frame.data.get(_QIANGXI_TARGET)
    if target is None:
        pool = _qiangxi_target_pool(state, owner, cost)
        if not pool:
            # 弃掉的正是那把撑起范围的武器、且剩下的距离超过无武器范围 1（对手挂 +1 坐骑
            # 即可）⇒ 整条取消、零副作用。**候选面已把这条死路挡掉**
            # （:func:`_qiangxi_discardable_weapons`），故这里只兜"构造局面直接调 effect"与
            # 将来出现减范围共享原语的情形——不把"池永远非空"当成隐式前提。
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.PHASE_PLAY_IDLE,
            skill_id=_QIANGXI_ASK_TARGET,
            options=tuple(_seat_choice(seat) for seat in pool),
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        target = _option_seat(answer)
        frame.data[_QIANGXI_TARGET] = target
    if not frame.data.get(_QIANGXI_PAID):
        # 真提交点：先落阶段位（之前的任何取消都走不到这里），再付代价。
        _mark_phase(state, owner, QIANGXI, _USED)
        if discard_weapon:
            assert card is not None
            if (
                _move_to_discard(host, frame, (card,), owner=owner, key="qiangxi:cost")
                == "stay"
            ):
                return "stay"
        else:
            frame.data[_QIANGXI_PAID] = True
            # 失去体力 ≠ 受到伤害：只压 LIFE_DECREASE 帧（无来源、无属性），该帧自带濒死插入。
            host.push_frame(
                FrameKind.LIFE_DECREASE,
                {"target": owner, "source": None, "amount": 1},
                timing=Timing.LIFE_DECREASE_BEFORE,
            )
            return "stay"
        frame.data[_QIANGXI_PAID] = True
    if frame.data.get(_QIANGXI_DONE):
        return None
    if not state.player(owner).alive:
        # 失去体力把自己送进濒死且没救回：角色已阵亡，伤害整段不发生。
        return None
    if not state.player(int(target)).alive:
        # 目标在代价的结算里离场（内联钩子造成）：不压伤害帧。
        return None
    frame.data[_QIANGXI_DONE] = True
    _push_damage(host, frame, source=owner, target=int(target), amount=1)
    return "stay"


QIANGXI_FLOW = SkillFlow(
    name="qiangxi",
    steps=(PickOption(options=_qiangxi_options, optional=True, key="option"),),
    effect=_qiangxi_effect,
    # 次数由提交点自己数（见上）：执行器的 `_USED` 落在 effect 之前，而本技能的 effect
    # 里还有可取消的手写步骤 —— 那样"选了代价却中途取消"也会吃掉阶段位。
    once_per_phase=False,
)
"""【强袭】的一步流水线：选代价 → （弃武器：选牌）→ 选目标 → 付代价 → 1 点伤害。"""


# ---------------------------------------------------------------------------
# 界关羽【义绝】（M6 票 04）：弃一张牌 → 一名其他角色展示一张手牌 → 红 / 黑分叉
#
# 规则原文（§十九 L2087–L2090）：
#
#     出牌阶段限一次，你可以弃置一张牌，令一名其他角色展示一张手牌。若此牌为红色，
#     你获得此牌且你可以令其回复 1 点体力；若此牌为黑色，本回合：其所有非锁定技
#     失效且不能使用或打出手牌、你使用红桃【杀】对其造成的伤害 +1。
#
# 三段共用一个 ``SkillFlow``（弃牌 → 选人 → 由目标展示），红 / 黑分叉与"可以回复"的
# 二次决定在 ``effect`` 里（帧内游标续跑）。黑色分支的三条效果各有落点：
#
# - "所有非锁定技失效" ⇒ :meth:`Engine.suppress_skills`（票 04 的座位级失效记录）；
# - "不能使用或打出手牌" ⇒ :meth:`Engine.set_hand_card_prohibition`，唯一读取方是
#   :func:`sgs.effects.legal_actions_for`（被禁者**所有**使用 / 打出手牌的候选消失）；
# - "红桃【杀】伤害 +1" ⇒ ``DAMAGE_DEAL_AMOUNT`` 修饰器 + 座位级目标记录。
# ---------------------------------------------------------------------------

YIJUE = hero_skill_id("jie_guanyu", "yijue")
"""界关羽【义绝】的全限定技能 id。"""

YIJUE_HEAL = "heal"
"""【义绝】红色分支的二次决定：令其回复 1 点体力。"""

YIJUE_SKIP_HEAL = "skip"
"""【义绝】红色分支的二次决定：放弃回复。"""

YIJUE_HEAL_OPTIONS: tuple[str, str] = (YIJUE_HEAL, YIJUE_SKIP_HEAL)
"""【义绝】"你可以令其回复 1 点体力"的选项闭集（顺序即询问里的顺序）。"""


def _yijue_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """座位步的**候选池**：其他存活角色（"有手牌"那一层由**展示步**自己兜住）。

    池子故意**不随目标手牌数收缩**（M6 票 04 的评审发现）：``effect`` 里的"获得展示的牌"
    会把目标的手牌搬走，而效果挂起后续跑会**从头重跑步骤**——池若还在按"有手牌"过滤，
    重入时它已为空 ⇒ ``PickSeats`` 判取消、``effect`` 不再执行（``_push_recover`` 之后
    的收尾整段丢掉）。故这里的池只按"存活 / 不是自己"定，选到没有手牌的角色时由展示步
    （空面 ⇒ 取消）兜住，而**取消发生在提交点之前**，代价一张都不付。

    "候选里有没有这条技能"另由 :func:`_yijue_offers` 判（至少一名其他角色有手牌）。
    """
    del frame, chosen
    return tuple(
        player.seat
        for player in state.players
        if player.alive and player.seat != owner
    )


def _yijue_target(state: GameState, frame: Any, owner: int, answers: FlowAnswers) -> int:
    """步 2 / 步 3 的**应答者**：步 2 选中的那名角色（展示手牌的是被指定者，不是发动者）。"""
    del state, frame, owner
    return int(answers["seat"][0])


def _yijue_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选**恒为一条骨架**：付不起（自己无牌）或没有可指定的目标 ⇒ 一个候选都不造。"""
    if not _can_offer(state, ctx):
        return current
    player = state.player(ctx.subject)
    if not player.hand and not player.equip:
        return current
    if not any(
        other.hand
        for other in state.players
        if other.alive and other.seat != ctx.subject
    ):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _yijue_heart_slash_bonus(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """【义绝】黑色分支的"你使用**红桃**【杀】对其造成的伤害 +1"（``DAMAGE_DEAL_AMOUNT``）。

    连续型效果从**活着的实例**算（D9）：本回调挂在【义绝】的 ``SkillDef`` 上，失去 / 失效
    【义绝】后它自然不再被派发。作用域目标记在 ``Player.heart_slash_bonus_against``（回合末
    清空），故"本回合"只由那一条座位记录表达，而不是给每张【杀】写标签。
    """
    if ctx.owner != ctx.subject or ctx.card is None or ctx.target is None:
        return current
    if ctx.target not in state.player(ctx.owner).heart_slash_bonus_against:
        return current
    if ctx.card.suit != Suit.HEART.value:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    return current + 1


def _yijue_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：展示（公开事实）→ 红：获得 + 可令其回复 1 点 / 黑：三条失效类效果。

    每一步的完成事实都记在帧私有键里，故重入既不复述也不重问；搬牌 / 回复各自可能挂起
    （"失去牌后"钩子、濒死插入），续跑只补没做的那一段。

    **在飞语义**（票面验收 3）：本效果**不撤回**任何已开始的结算——它只写座位级记录，
    真帧标签 / 修饰器在**此后新产生**的解析里才读这些记录。
    """
    state = host.state
    owner = frame.data["source"]
    target = int(answers["seat"][0])
    card = state.card(int(answers["card"][0]))
    if "yijue_cost_paid" not in frame.data:
        # 成本（弃置一张牌）在**提交点**才搬：步骤只收集（与【反间】/【离间】同一条 N6）。
        # 搬牌可能内联派发"失去牌后"并挂起，故游标在搬完之后才落。
        control = host.move_in_frame(
            frame,
            state.refs(tuple(answers["cost"])),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
        frame.data["yijue_cost_paid"] = True
    if "yijue_revealed" not in frame.data:
        # 展示是**公开事实**：落一条 card_reveal（牌仍留在目标手里，除非随后被获得）。
        frame.data["yijue_revealed"] = True
        host.reveal_card(frame, target, card)
    if color_of_suit(card.suit) is Color.RED:
        if "yijue_gained" not in frame.data:
            frame.data["yijue_gained"] = True
            control = host.move_in_frame(
                frame,
                (card,),
                to_zone=ZoneRef(Zone.HAND, owner),
                reason=MoveReason.PUT,
                owner=owner,
                actor=owner,
            )
            if control == "stay":
                return "stay"
        if "yijue_heal" not in frame.data:
            answer = host.ask_option(
                frame,
                owner,
                Timing.PHASE_PLAY_IDLE,
                skill_id="yijue:heal",
                options=YIJUE_HEAL_OPTIONS,
                optional=False,
            )
            if host.suspended(answer):
                return "stay"
            frame.data["yijue_heal"] = answer.choice
        if frame.data["yijue_heal"] == YIJUE_HEAL and "yijue_recovered" not in frame.data:
            frame.data["yijue_recovered"] = True
            _push_recover(host, frame, source=owner, target=target, amount=1)
            return "stay"
        return None
    if "yijue_black" not in frame.data:
        frame.data["yijue_black"] = True
        host.suppress_skills(target, YIJUE, frame=frame)
        host.set_hand_card_prohibition(target, True)
        host.add_heart_slash_bonus(owner, target)
    return None


YIJUE_FLOW = SkillFlow(
    name="yijue",
    steps=(
        # 成本：一张牌（手牌 / 装备区）。第一步可取消 = 牌面"**可以**弃置一张牌"的放弃出口；
        # 弃出之后（后续步骤）不可再退——与【反间】的"确认之后不可退回"同一口径。
        PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=1, key="cost"),
        PickSeats(lo=1, hi=1, pool=_yijue_targets, key="seat", optional=False),
        PickCards(
            zones=(Zone.HAND,),
            lo=1,
            hi=1,
            key="card",
            actor=_yijue_target,
            face=_yijue_target,
            optional=False,
        ),
    ),
    effect=_yijue_effect,
)
"""【义绝】的三步流水线：弃一张牌 → 选一名有手牌的其他角色 →（由其）展示一张手牌。"""



# ---------------------------------------------------------------------------
# 界徐庶 ☆【荐言】（M6 票 06）：选择类别 / 颜色 → 亮出牌堆直到符合 → 一名男性角色获得
#
# 规则原文（§十九 L2132–L2133）：
#
#     ☆荐言：出牌阶段限一次，你可以选择一种牌的类别或颜色，然后亮出牌堆中一张符合
#            要求的牌并令一名男性角色获得此牌。
#
# "亮出牌堆中一张符合要求的牌"的结算口径（OL 牌面与通行实现）：**逐张亮出牌堆顶的牌，
# 直到出现一张符合所选类别 / 颜色的牌**；最后那张交给选定的男性角色，**其余亮出的牌
# 置入弃牌堆**。亮出的牌进**处理区**（公开区域）：牌面因此对所有参与者可见，且"其余
# 置入弃牌堆"在同一次结算内完成（与【五谷丰登】的亮牌 / 收尾同一形状）。
# ---------------------------------------------------------------------------

JIANYAN = hero_skill_id("jie_xushu", "jianyan")
"""界徐庶 ☆【荐言】的全限定技能 id（觉醒后由【潜心】授予）。"""

JIANYAN_OPTIONS: tuple[str, ...] = (
    Category.BASIC.value,
    Category.TRICK.value,
    Category.EQUIPMENT.value,
    Color.RED.value,
    Color.BLACK.value,
)
"""【荐言】的五个选项：三种**类别** + 两种**颜色**（`06` §5「程序化约定与偏离一手」裁定：类别与颜色同属一份
闭集，不是两条技能；两条判据各自读数据层的唯一真源）。"""

_JIANYAN_REVEALED = "jianyan_revealed"
"""帧私有：本次发动已经亮出的牌 id（自顶向下；重入不重新亮牌、不重复消耗随机数）。"""

_JIANYAN_MATCH = "jianyan_match"
"""帧私有：最后那张符合要求的牌 id；``None`` = 牌堆 + 弃牌堆亮完也没有符合的。"""


def _male_seats(state: GameState) -> tuple[int, ...]:
    """**存活男性**座位（座位序）。【荐言】的受赠者池 = 全部男性角色（含发动者本人）。"""
    return tuple(
        player.seat
        for player in state.players
        if player.alive and player.gender is Gender.MALE
    )


def _jianyan_matches(state: GameState, card: Any, choice: str) -> bool:
    """这张牌符不符合所选的**类别 / 颜色**（判据与选项同一闭集）。"""
    if choice in {category.value for category in Category}:
        return card_category(state.pack_id, card.type) is Category(choice)
    return color_of_suit(card.suit) is Color(choice)


def _jianyan_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：本阶段没用过 ∧ 至少有一名存活男性（没有合法受赠者 ⇒ 一个候选都不造）。"""
    if not _can_offer(state, ctx):
        return current
    if not _male_seats(state):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _jianyan_reveal(
    host: Any, frame: Any, choice: str, owner: int
) -> tuple[tuple[int, ...], int | None]:
    """逐张亮出牌堆顶的牌（进处理区）直到符合 ``choice``；返回（亮出的 id, 命中的 id）。

    牌堆与弃牌堆都亮完仍没有符合的牌 ⇒ 返回 ``None``（其余照常置入弃牌堆）。``draw_open``
    沿用摸牌的洗牌 / 短摸口径，但**不判平局**（亮牌不是摸牌）。
    """
    revealed: list[int] = []
    while True:
        drawn = host.draw_open(frame, 1, owner=owner)
        if not drawn:
            return tuple(revealed), None
        card = drawn[0]
        revealed.append(card.card_id)
        if _jianyan_matches(host.state, card, choice):
            return tuple(revealed), card.card_id


def _jianyan_discard(host: Any, frame: Any, card_ids: tuple[int, ...], *, owner: int) -> Any:
    """把亮出的其余牌置入弃牌堆（无归属主体，与【五谷丰登】的收尾同口径）。"""
    if not card_ids:
        return None
    return host.move_in_frame(
        frame,
        host.state.refs(card_ids),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=None,
        actor=owner,
    )


def _jianyan_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：亮牌 → 选一名男性角色 → 把命中的牌交给其，其余置入弃牌堆。

    重入：亮牌结果缓存在帧私有（``_JIANYAN_REVEALED`` / ``_JIANYAN_MATCH``），故选人
    询问挂起后续跑不会重新亮牌；两次搬牌各自有 ``move_in_frame`` 的"同一次移动"守卫。
    """
    owner = frame.data["source"]
    if _JIANYAN_REVEALED not in frame.data:
        revealed, matched = _jianyan_reveal(host, frame, str(answers["type"]), owner)
        frame.data[_JIANYAN_REVEALED] = revealed
        frame.data[_JIANYAN_MATCH] = matched
    revealed = tuple(frame.data[_JIANYAN_REVEALED])
    matched = frame.data[_JIANYAN_MATCH]
    if matched is None:
        return _jianyan_discard(host, frame, revealed, owner=owner)

    pool = _male_seats(host.state)
    answer = host.ask_option(
        frame,
        owner,
        Timing.PHASE_PLAY_IDLE,
        skill_id="jianyan:seat",
        options=tuple(_seat_choice(seat) for seat in pool),
    )
    if host.suspended(answer):
        return "stay"
    recipient = _option_seat(answer)
    leftovers = tuple(card_id for card_id in revealed if card_id != matched)
    control = _jianyan_discard(host, frame, leftovers, owner=owner)
    if control == "stay":
        return "stay"
    return host.move_in_frame(
        frame,
        host.state.refs((matched,)),
        to_zone=ZoneRef(Zone.HAND, recipient),
        reason=MoveReason.PUT,
        owner=recipient,
        actor=owner,
    )


JIANYAN_FLOW = SkillFlow(
    name="jianyan",
    steps=(
        # 「选择一种牌的类别或颜色」：一次闭集选项（类别三项 + 颜色两项）。
        PickOption(options=JIANYAN_OPTIONS, optional=False, key="type"),
    ),
    effect=_jianyan_effect,
)
"""【荐言】的一步流水线：选类别 / 颜色 → 亮牌直到符合 → 选一名男性角色获得。"""


# ---------------------------------------------------------------------------
# 界吕蒙 ☆【攻心】（M6 票 06）：观看一名其他角色的手牌 → 可展示一张红桃牌 →
#                           弃置此牌 / 置于牌堆顶
#
# 规则原文（§十九 L2158–L2160）：
#
#     ☆攻心：出牌阶段限一次，你可以观看一名其他角色的手牌，然后你可以展示其中一张
#            红桃牌并选择一项：1. 弃置此牌；2. 将此牌置于牌堆顶。
#
# "观看"是**这一次询问**的牌面授予（不写持久知识）：选牌询问把目标的**整副手牌**放进
# ``revealed``，而可选槽**只**铺红桃（"你可以展示其中一张红桃牌"）——两者同一份询问，
# 故观看与"能不能展示"不会各读一份状态。展示本身落 ``card_reveal``（公开事实）。
# ---------------------------------------------------------------------------

GONGXIN = hero_skill_id("jie_lvmeng", "gongxin")
"""界吕蒙 ☆【攻心】的全限定技能 id（觉醒后由【勤学】授予）。"""

GONGXIN_DISCARD = "discard"
GONGXIN_DECK_TOP = "deck_top"
"""【攻心】的两项：1. 弃置此牌；2. 将此牌置于牌堆顶。"""

GONGXIN_OPTIONS: tuple[str, ...] = (GONGXIN_DISCARD, GONGXIN_DECK_TOP)

_ASK_GONGXIN_HEART = "hero.jie_lvmeng.gongxin.heart"
"""手写询问位置：【攻心】"展示其中一张红桃牌"的那次 ``ask_selection``。"""

_GONGXIN_REVEALED = "gongxin_revealed"
"""帧私有：这张红桃牌已经落过 ``card_reveal``（重入不重复展示）。"""


def _gongxin_targets(state: GameState, owner: int) -> tuple[int, ...]:
    """可以观看的对象：**其他存活且手牌非空**的角色（座位序）。"""
    return tuple(
        player.seat
        for player in state.players
        if player.alive and player.seat != owner and player.hand
    )


def _gongxin_pool(state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]) -> tuple[int, ...]:
    del frame, chosen
    return _gongxin_targets(state, owner)


def _gongxin_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：本阶段没用过 ∧ 有可观看的其他角色。"""
    if not _can_offer(state, ctx):
        return current
    if not _gongxin_targets(state, ctx.subject):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _gongxin_heart_face(host: Any, target: int) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """可选槽 = 目标手牌里的**红桃**（可见槽：观看者已经看到了整副手牌）。"""
    hearts = tuple(
        card_id
        for card_id in host.state.player(target).hand
        if host.state.card(card_id).suit == Suit.HEART.value
    )
    slots = tuple(
        Slot(ref=ZoneRef(Zone.HAND, target), card=host.state.card(card_id))
        for card_id in hearts
    )
    return slots, hearts


def _gongxin_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：观看（整副手牌授予）→（可选）展示一张红桃 → 弃置 / 置于牌堆顶。

    ``ask_when_empty=True``（用户裁定 2026-10-31）：目标**有手牌**就必须能观看——没有红桃
    时照发一条"空选择面"的询问（只有 ``Pass``），弹窗展示整副手牌、出口是「放弃」。
    目标有没有手牌是**发动前提**（``_gongxin_targets`` 只收手牌非空的其他角色）；
    观看本身不再受"有没有红桃"影响。

    "置于牌堆顶"是**一次**跨区移动（``move_in_frame(..., to_top=True)``，票 02）：落点随
    delta 落记录，故 hand→DECK 的移动若被 MOVE_AFTER 钩子（【连营】一类）挂起，"置顶"
    也不会被劈成第二步——旧实现是"先追加到牌堆尾、续跑后再 ``place_on_deck``"，挂起时
    牌停在牌堆底（票 01 的实机缺陷）。
    """
    owner = frame.data["source"]
    target = int(answers["seat"][0])
    hand = tuple(host.state.player(target).hand)
    picked = host.ask_selection(
        frame,
        owner=_ASK_GONGXIN_HEART,
        key=f"gongxin:{target}",
        actor=owner,
        timing=Timing.PHASE_PLAY_IDLE,
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=True),
        build_face=lambda: _gongxin_heart_face(host, target),
        # "观看"：目标的**整副手牌**只授予这一次询问的应答者（不进投影、不落 delta）。
        revealed=tuple(host.state.refs(hand)),
        ask_when_empty=True,
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        return None
    card_id = int(picked[0])
    if not frame.data.get(_GONGXIN_REVEALED):
        frame.data[_GONGXIN_REVEALED] = True
        # 展示是**公开事实**：落一条 card_reveal（牌随后才离开目标手牌）。
        host.reveal_card(frame, target, host.state.card(card_id))
    answer = host.ask_option(
        frame,
        owner,
        Timing.PHASE_PLAY_IDLE,
        skill_id="gongxin:option",
        options=GONGXIN_OPTIONS,
    )
    if host.suspended(answer):
        return "stay"
    if answer.choice == GONGXIN_DISCARD:
        return host.move_in_frame(
            frame,
            host.state.refs((card_id,)),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=owner,
            actor=owner,
        )
    return host.move_in_frame(
        frame,
        host.state.refs((card_id,)),
        to_zone=ZoneRef(Zone.DECK),
        reason=MoveReason.PUT,
        owner=target,
        actor=owner,
        to_top=True,
    )


GONGXIN_FLOW = SkillFlow(
    name="gongxin",
    steps=(
        PickSeats(lo=1, hi=1, pool=_gongxin_pool, optional=False, key="seat"),
    ),
    effect=_gongxin_effect,
)
"""【攻心】的流水线：选一名其他角色 → 观看其手牌 →（可）展示红桃 → 弃置 / 置顶。"""


# ---------------------------------------------------------------------------
# 登记：一条声明 = 候选回调 + 发动回调（两者共用同一个 skill_id）
# ---------------------------------------------------------------------------


ActivationHandler = Callable[[Any, Any], Any]
"""发动回调：``(host, frame) -> Control``，与 ``OPS`` / 钩子同形。"""


@dataclass(frozen=True, slots=True)
class ActiveSkill(HeroSkillDecl):
    """A 族一条技能的**声明**：候选构造 + 发动主体（票 04 §表一行）。

    ``activate`` 与 ``flow`` **恰有一个**：未迁的主动技（票 01 的【离间】）给手写回调；
    迁到步骤流水线的技能（票 02 起用 :class:`SkillFlow` 声明）给 ``flow``，回调由
    :func:`_activation_handler` 现建（票 12 §4：旧落点写作 ``flow.flow_handler``，那个通用
    工厂拿不到帧、已删）。这样声明面只有一份真源（谁发动、问什么、怎么结算），不会出现
    "回调与声明各写一套"的漂移。
    """

    offers: Callable[[GameState, ModifierContext, Any], Any]
    activate: ActivationHandler | None = None
    flow: SkillFlow | None = None
    extra_modifiers: tuple[Modifier, ...] = ()
    """候选之外**额外**的修饰器（M6 票 04 的加法；缺省空 = 既有七条一字不改）。

    【义绝】的"你使用红桃【杀】对其造成的伤害 +1"是同一张武将牌上的一条**连续型**效果：
    它必须与候选入口共用同一个 ``SkillDef``（一个技能一条定义），故在声明面留一个修饰器
    口子，由 :func:`active_skill_definitions` 合并进定义。
    """
    requirement: str | None = None
    """这条技能"需要玩家做什么"的**覆盖文案**（M6 票 07；缺省 ``None`` = 读
    :data:`_REQUIREMENTS` 的短 id 那一行）。标准 / 界两版同短 id 但牌面不同时用它。"""
    step_requirements: Mapping[str, str] | None = None
    """**分步文案的覆盖**（M6 票 14；缺省 ``None`` = 读 :data:`_STEP_REQUIREMENTS` 的短 id
    那一行）。标准 / 界两版同短 id 但**步骤次序 / 角色不同**时用它（界【离间】先选目标、
    再选使用者，两次询问的文案因此与标准版相反）。键 = 步骤名，须覆盖该 flow 的每个步骤
    （缺键的步骤 `Step.wording` 为空串）。"""
    preactivated: bool = False
    """本技能是否已迁到**预发动**（W4-1 的迁移开关，缺省 ``False``）。

    ``True`` 时引擎在出牌阶段把它的 ``ActivateSkill`` 骨架从 ``legal_actions`` 里删掉、
    只由 ``PlayPhaseQuery.preactivations`` 表达可用性（清单由 ``flow`` 渲染）。本票只落
    机制，逐条技能由 W4-2 翻这个位——翻之前既有候选 / 测试 / 录像一字不改。
    """
    skill_type: SkillType = SkillType.NORMAL
    """技能类型（韬·刘备【义起】的加法，缺省 ``NORMAL`` ⇒ 既有 A 族声明一字不改）。

    与 :class:`~sgs.skills.phase_body.PhaseBodySkill.skill_type` **同一形状**：限定技在
    被动侧由派发环按 ``SkillDef.skill_type`` 挡（"整局已用"位），主动侧没有派发环，故
    这里把它透进 ``SkillDef``，并由 A 族自己的两处通用闸兑现：

    - **候选**：:func:`_limited_offers` 在"整局已用"之后不再产出候选；
    - **落位**：:func:`_activation_handler` 的提交点调
      :func:`~sgs.skills.catalog.mark_limited_skill_used`（与被动侧的执行完落位同姿态）。
    """

    def __post_init__(self) -> None:
        if (self.activate is None) == (self.flow is None):
            raise ValueError(f"{self.skill_id} 必须恰有 activate / flow 之一")


# ---------------------------------------------------------------------------
# 韬·刘备【义起】（自制包；「限」）：观看弃牌堆顶 x 张 → 获得一种类型的所有牌 →
# 失去武将牌上除此技能外的所有技能 → 获得【武圣】【咆哮】
# ---------------------------------------------------------------------------

YIQI = hero_skill_id("tao_liubei", "yiqi")
"""韬·刘备【义起】的全限定技能 id（A 族第一条**限定技**）。

牌面（用户 `技能.txt`）：(限) 出牌阶段内，你可以观看弃牌堆顶 x 张牌，选择并获得其中一种
类型的所有牌，然后你失去武将牌上除此技能外的所有技能并获得【武圣】、【咆哮】
（x 为弃牌堆牌数整除 10）。

用户 2026-10-31 逐条裁定的三处（`06`（§5「程序化约定与偏离一手」）韬·刘备行）：

- **x = 0 不能发动**（弃牌堆不足 10 张 ⇒ 候选不出现，与"付不起"同档）；
- **观看 = 自启亮牌弹窗**，随后**提示区恒 3 个选项**（基本 / 锦囊 / 装备），
  **必须选 1 个、不可放弃**（选中顶 x 张里没有的类型 ⇒ 本次不得牌，仍继续后面的失去 / 获得）；
- **"失去"严格按牌面派生**：武将牌技能清单减【义起】，代码里不出现具体技能 id。
"""

YIQI_DECK_PER_CARD = 10
"""x 的分母：**弃牌堆张数整除 10**（向下取整；牌面"x为弃牌堆牌数整除10"）。"""

WUSHENG_STANDARD = hero_skill_id("guanyu", "wusheng")
"""【义起】授予的☆【武圣】= **标准版**（用户裁定"就是标武圣，照搬即可"）。"""

PAOXIAO_STANDARD = hero_skill_id("zhangfei", "paoxiao")
"""【义起】授予的☆【咆哮】= **标准版**（用户裁定"就是标咆哮，照搬即可"）。"""

_YIQI_GAINED_KEY = "yiqi_gained"
"""帧私有：这一步的获得已经搬完（搬牌挂起后的续跑不重搬）。"""

_YIQI_LOST_KEY = "yiqi_lost"
"""帧私有：武将牌上的技能已经失去（`lose_skill` 幂等，但重复遍历没有必要）。"""

_YIQI_GRANTED_KEY = "yiqi_granted"
"""帧私有：☆ 武圣 / 咆哮已经授予（`grant_skill` 幂等，同上）。"""

_YIQI_TYPES: tuple[str, ...] = ("basic", "trick", "equipment")
"""类型步的**恒 3 项**闭集（用户裁定：给 3 个选项，不收窄成"实际出现的类型"）。"""

_YIQI_CATEGORY: dict[str, Category] = {
    "basic": Category.BASIC,
    "trick": Category.TRICK,
    "equipment": Category.EQUIPMENT,
}
"""选项文本 → 牌类（数据的 :class:`~sgs.data.Category` 三档；标签由前端
``options.js:OPTION_LABELS`` 给中文）。"""


def _yiqi_view_count(state: GameState) -> int:
    """x = 弃牌堆张数 **整除 10**（动态量：每次取用时按当下张数现算，账本 L775⑤）。"""
    return len(state.discard) // YIQI_DECK_PER_CARD


def _yiqi_top_cards(state: GameState) -> tuple[CardRef, ...]:
    """弃牌堆**顶** x 张 = ``state.discard`` 的**尾部**（列表按置入序，最后置入的在堆顶）。

    纯状态读：不抽随机数、不落 delta、不搬牌 —— 观看只是看。两个步骤与 effect 各自现算
    同一批牌（步骤之间没有能改动弃牌堆的操作），故不必帧内缓存。
    """
    count = _yiqi_view_count(state)
    if count <= 0:
        return ()
    return tuple(state.card(card_id) for card_id in state.discard[-count:])


def _yiqi_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：出牌阶段 + 本人 + x ≥ 1（限定技的"整局已用"闸由 :func:`_limited_offers` 包在外层）。"""
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    if not _yiqi_top_cards(state):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _yiqi_revealed(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[CardRef, ...]:
    """选项步授予的牌面 = 弃牌堆顶 x 张（投影按"``SELECT_OPTION`` ∧ ``revealed`` 非空"开选择型弹窗）。"""
    del frame, owner, answers
    return _yiqi_top_cards(state)


def _yiqi_lost_skill_ids(state: GameState, owner: int) -> tuple[str, ...]:
    """「武将牌上除此技能外的所有技能」= 武将牌技能清单 **减【义起】**（用户 Q7）。

    清单来自数据表（:func:`~sgs.data.hero_card_skill_ids`，它已排除 ☆ 条目）⇒ 本将今天的
    结果是【从讨】一条，但**代码里不出现"从讨"这个 id**：武将牌上多一条技能时它也会被
    失去，断言的构造性用例钉这一点。
    """
    hero_id = state.player(owner).hero
    if hero_id is None:
        return ()
    return tuple(
        skill_id for skill_id in hero_card_skill_ids(hero_id) if skill_id != YIQI
    )


def _yiqi_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：获得该类型的所有牌 → 失去武将牌上除【义起】外的技能 → 授予☆武圣 ★咆哮。

    三段各带帧内游标：搬牌会内联派发"获得牌后"一类钩子，挂起后的续跑既不重做也不漏做。
    选中一个**顶 x 张里没有的**类型时第一段零搬牌——用户裁定 3 项恒在，故这是合法的
    （不取消，照样完成后面的失去 / 获得）。
    """
    state = host.state
    owner = int(frame.data["source"])
    wanted = _YIQI_CATEGORY[str(answers["type"])]
    if not frame.data.get(_YIQI_GAINED_KEY):
        in_pile = set(state.discard)
        cards = tuple(
            card
            for card in _yiqi_top_cards(state)
            if card.card_id in in_pile
            and card_category(state.pack_id, card.type) is wanted
        )
        if cards:
            control = host.move_in_frame(
                frame,
                cards,
                to_zone=ZoneRef(Zone.HAND, owner),
                reason=MoveReason.PUT,
                owner=owner,
                actor=owner,
            )
            if control == "stay":
                return "stay"
        frame.data[_YIQI_GAINED_KEY] = True
    if not frame.data.get(_YIQI_LOST_KEY):
        frame.data[_YIQI_LOST_KEY] = True
        for skill_id in _yiqi_lost_skill_ids(state, owner):
            host.lose_skill(owner, skill_id, frame=frame)
    if not frame.data.get(_YIQI_GRANTED_KEY):
        frame.data[_YIQI_GRANTED_KEY] = True
        host.grant_skill(owner, WUSHENG_STANDARD, frame=frame)
        host.grant_skill(owner, PAOXIAO_STANDARD, frame=frame)
    return None


YIQI_FLOW = SkillFlow(
    name="yiqi",
    steps=(
        # **一问**：选项步带上观看授予（`revealed` = 弃牌堆顶 x 张）。投影按形状把这个
        # 选项步开成选择型弹窗——牌面画在弹窗里（只读、仅自己可见），3 个类型按钮也在同一
        # 个弹窗里；点一个即提交（`optional=False`：必须选 1 个、不可放弃）。
        # 不分两步的理由（2026-10-31 实机验收）：第一步"只看不选"在预发动清单里是个空步骤，
        # 而清单是"发动前一次填完"的形态，没有"先看到牌"这一环 ⇒ 带观看授予的流水线整条
        # 不下发清单（`flow.manifest`），走"点技能按钮 → 逐步问"。
        PickOption(
            options=_YIQI_TYPES,
            optional=False,
            key="type",
            revealed=_yiqi_revealed,
        ),
    ),
    effect=_yiqi_effect,
    # 限定技的"一次"是**整局**，不是本阶段 ⇒ 不落 `_USED` 阶段位（整局位由提交点落）。
    once_per_phase=False,
)
"""【义起】的一步流水线：观看弃牌堆顶 x 张 + 三选一（同一问）→ 获得 / 失去 / 授予。"""


# ---------------------------------------------------------------------------
# 移动版于吉【蛊惑】（神话再临·风包·移动版；用户 2026-11-01 读法裁定）
#
# 牌面（`D:\\SGS_HEROS\\神话再临-风包\\于吉\\技能.txt`，逐字）：
#   你可以扣置一张手牌，将此牌当任意一张基本牌或普通锦囊牌使用或打出。若有其他角色质疑
#   则翻开此牌且此牌作废，若为：假，质疑者各摸一张牌；真，质疑者各失去1点体力，然后若
#   被质疑的牌的花色为♥️，此牌依旧进行结算。
#
# 用户裁定（2026-11-01）：
#   · **两个入口**：① 出牌阶段主动使用；② 任何时候"需要使用 / 打出一张基本牌或普通锦囊牌"
#     时进入询问（同【武圣】【龙胆】，区别只是产物牌名覆盖全部基本牌 + 普通锦囊牌）；
#   · **扣置算失去牌**；质疑者 = **其他所有存活角色**；
#   · 质疑顺序 = **从下家起逆时针**，**收齐全部答复后**再按同一顺序依次执行结果；
#   · 触发次数**无限制**；作废 = 牌从**效果区**进弃牌堆（扣置时已离开手牌）。
#
# 本节的落点：**出牌阶段入口走两步流水线**（选牌 → 声明；用户 2026-11-02 的 Q3A 裁定 =
# "点蛊惑 → 选 1 张手牌 → 牌名弹窗 → 一次提交"，故它翻成 `preactivated`），段 3–8 仍是
# 手写段机；质疑的**逐座位循环**留在段机里（流水线语言没有循环步骤，见 `flow.py:SkillFlow`
# 的边界）。`扣置` = 手牌 → 处理区（`MoveReason.LOSE`，`MOVE_AFTER` 因此照常触发"失去牌后"），
# `验明` = `Engine.reveal_card`（M5-06 的公开揭示），`作废` = 处理区 → 弃牌堆。
# ---------------------------------------------------------------------------

GUOHUO = hero_skill_id("yuji_mobile", "guohuo")
"""移动版于吉【蛊惑】的全限定技能 id。"""

GUOHUO_DOUBT = "guohuo_doubt"
GUOHUO_TRUST = "guohuo_trust"
"""质疑 / 相信两个选项（`options.js:OPTION_LABELS` 各一条；闭集两选项 ⇒ 牌桌按钮组）。"""

_ASK_GUOHUO_CARD = "hero.yuji_mobile.guohuo.card"
"""手写询问位置（票 22）：扣置选牌。目标 / 质疑那两问的 `owner` 由技能 id 派生。"""

_GUOHUO_STAGE = "guohuo_stage"
_GUOHUO_SOURCE = "guohuo_source"
_GUOHUO_NAME = "guohuo_name"
_GUOHUO_TARGETS = "guohuo_targets"
_GUOHUO_DESIGNATIONS = "guohuo_designations"
_GUOHUO_KOUCHU = "guohuo_kouchu"
_GUOHUO_ASKED = "guohuo_asked"
_GUOHUO_YES = "guohuo_doubted"
_GUOHUO_REVEALED = "guohuo_revealed"
_GUOHUO_RESULT = "guohuo_result_next"
_GUOHUO_RESULT_SEAT = "guohuo_result"
_GUOHUO_SETTLED = "guohuo_settled"
"""帧私有游标：扣置已完成 / 已问过谁 / 谁质疑了 / 验明过了 / 结果执行到第几个 / 收尾已完成。

每条都是"重入既不重做也不漏做"的落点（`CODING_STANDARDS.md` §3：会挂起的操作必须幂等）。
``_GUOHUO_RESULT_SEAT`` 是逐座位的结果位前缀（``guohuo_result:<seat>``）。
"""

_GUOHUO_PROVIDER_STAGE = "guohuo_provider_stage"
""""需要牌时"入口（提供者）的段游标：扣置 / 声明 / 扣置进处理区 / 质疑。

**与出牌阶段入口分开住**：提供者在**外层窗口的帧**上跑（【杀】的结算帧 / 锦囊的结算帧 /
濒死帧），出牌阶段入口在 SKILL 帧上跑——两套段机各自推进，共用的只有质疑 / 验明 / 结果
那一段。收尾**不压任何帧**：产物写进 ``PROVIDER_CARD_KEY``、"需要是否被满足"写进
``PROVIDER_SATISFIED_KEY``，由外层窗口按自己那一份规则消费（票 03）。"""

_GUOHUO_SELF_RESCUE_NAMES: frozenset[str] = frozenset(
    type_id
    for type_id, behavior in CARD_BEHAVIORS.items()
    if behavior.target_rule.near_death_self_only
)
"""**濒死自救**能当【桃】用的牌型：行为表声明 ``near_death_self_only`` 的那几张（当前只有
【酒】）。

与真牌路径（`effects/actions.py:_peach` 的候选过滤）读**同一份**声明，故"濒死者自己手牌里
能给什么"与"蛊惑能声明什么"不会各说各话；用户 2026-11-02 裁定：濒死自救【酒】与【桃】同档，
**不受**出牌阶段那次酒的限制。
"""


def _guohuo_declaration_table(state: GameState) -> dict[str, str]:
    """此刻可声明的牌名：``显示名 → 牌型 id``。

    范围 = **基本牌 + 普通锦囊**（排除延时锦囊），且**本牌堆里真有这种牌**——与界【龙胆】
    的 ``_product_in_pack`` 同一判据（108 档没有【酒】，把它列进选项会产出一张牌堆里不存在
    的虚拟牌）。
    """
    table: dict[str, str] = {}
    known: set[str] = set()
    for card in state.cards:
        if card.type in known:
            continue
        category = card_category(state.pack_id, card.type)
        if category is Category.BASIC or (
            category is Category.TRICK and not behavior_for(card).is_delayed
        ):
            known.add(card.type)
            table.setdefault(card.name, card.type)
    return table


def _guohuo_unusable_names(
    state: GameState, seat: int, source_card: int, table: Mapping[str, str]
) -> frozenset[str]:
    """此刻**用不了**的牌名（`check_targets` 现算）：界面置灰、校验器照同一份裁决。

    出牌阶段的【闪】/【无懈可击】没有目标池、【桃】在满体力时不可用、【杀】在攻击范围外
    没有目标 ⇒ 这些名字**列出来但点不动**（用户 Q1A：牌名弹窗列全牌堆名，把"此刻没有合法
    目标"的名字置灰；`edge-cases.md` 的 O1 已同步成「删掉 **或** 置灰」两种处置）。

    产物继承来源牌的花色 / 点数，故这一步必须按**刚选的那张牌**现算——同一张牌名闭集在
    不同来源牌下"能不能用"可以不同（黑【杀】撞【仁王盾】一类）。
    """
    return frozenset(
        name
        for name, type_id in table.items()
        if not check_targets(
            state, seat, transformed_card(state, (source_card,), type_id)
        )
    )


def _guohuo_selected_card(answers: FlowAnswers) -> int | None:
    """流水线第一步的答案（扣置哪张牌）→ 实体牌 id；还没有 ⇒ ``None``。

    清单渲染与校验两条路都用它：前者给的是**代表值**（``_answer_shape`` 的元组），故这里
    对形状不做假设，只取第一个元素。
    """
    picked = answers.get("card")
    if not picked:
        return None
    return int(tuple(picked)[0])


def _guohuo_name_options(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """牌名弹窗的**全量**闭集：本牌堆真有的全部基本牌 + 普通锦囊（用户 Q1A）。

    这一段与"此刻能用"**分开**：`_guohuo_declaration_table` 回答"牌堆里有没有这种牌"，
    `_guohuo_name_disabled` 回答"这张来源牌此刻能不能用它"。旧实现把两道闸并成一道
    （票 04 §3 之前的 `_guohuo_legal_names` 只留下能用的名字，已删），于是没用的名字连
    提示都看不到。
    """
    del frame, owner, answers
    return tuple(_guohuo_declaration_table(state))


def _guohuo_name_disabled(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """全量闭集里**此刻用不了**的那些（界面置灰；校验器独立拒收，不靠界面挡）。"""
    del frame
    source = _guohuo_selected_card(answers)
    if source is None:  # 前序还没选牌（清单的退化路径）⇒ 没有可置灰的依据
        return ()
    table = _guohuo_declaration_table(state)
    return tuple(
        name for name in table if name in _guohuo_unusable_names(state, owner, source, table)
    )


def _guohuo_virtual(state: GameState, frame: Any) -> CardRef:
    """本次扣置牌按**声明的牌名**转化出来的那张虚拟牌（单源 ⇒ 继承花色 / 点数）。"""
    return transformed_card(
        state, (int(frame.data[_GUOHUO_SOURCE]),), str(frame.data[_GUOHUO_NAME])
    )


def _guohuo_doubters(state: GameState, owner: int) -> tuple[int, ...]:
    """要表态的座位：**其他所有存活角色**，**从下家起逆时针**（用户裁定）。"""
    count = len(state.players)
    order = seats_from(state, (owner + 1) % count, count)
    return tuple(seat for seat in order if seat != owner and state.player(seat).alive)


def _guohuo_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """出牌阶段入口：手里有牌就给一条 ``ActivateSkill``（无次数限制 ⇒ 不落阶段位）。"""
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    if not state.player(ctx.subject).hand:
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


# ---------------------------------------------------------------------------
# 蛊惑的第二个入口："需要使用 / 打出一张基本牌或普通锦囊牌"时的**提供者**
#
# 用户裁定（2026-11-01）的②：同【武圣】【龙胆】，区别只是产物牌名覆盖全部基本牌 + 普通锦囊。
# 落点 = 提供者（不是转化候选）：扣置 / 逐座位质疑都发生在**受理这次响应之前**，故"作废"
# 不需要在受理后回滚（``UseCard`` 转化候选在受理那一刻就把 ``canceled`` 写死，见
# ``steps.py`` 的【闪】步）。
#
# 六扇窗口都有 provider 分支（票 03 补齐了后三扇）：`steps.py` 的【闪】步与无懈的收集式
# 窗口、`ops.py` 的南蛮 / 万箭 / 决斗 / 借刀、以及濒死求【桃】。**产物交给窗口**：技能只
# 扣置 → 声明 → 质疑 → 验明，然后把这件产物（虚拟牌）写进 ``PROVIDER_CARD_KEY``；"怎么
# 消费它"（使用 / 打出 / 求桃的回复结算 / 借刀打向 Bn / 无懈的"先声明、赢家才使用"）由
# 窗口按自己那一份规则决定。故本模块不认窗口形状，"这扇窗口要什么牌"由引擎写进
# ``PROVIDER_REQUIRED_KEY``（候选期读同一份事实的 ``ctx.respond_required``）。
# ---------------------------------------------------------------------------


_PROVIDER_SCOPES: frozenset[CandidateScope] = frozenset(
    {
        CandidateScope.DODGE,
        CandidateScope.RESPOND,
        CandidateScope.PEACH,
        CandidateScope.NULLIFY,
    }
)
"""有 provider 分支的候选语境（``PLAY`` / ``CARD`` 不是"需要牌"窗口）。

票面那六行窗口由**四档**语境覆盖：``DODGE`` = 【闪】、``PEACH`` = 濒死求【桃】、
``NULLIFY`` = 无懈的收集式窗口、``RESPOND`` = 南蛮 / 万箭 / 决斗（打出）与借刀（使用，
由 ``respond_mode`` 再分）。
"""


def _guohuo_window_required(ctx: ModifierContext) -> str | None:
    """这次候选派发是不是**有 provider 分支**的"需要牌"窗口；是 ⇒ 它要的牌名。

    牌名一律读 ``ctx.respond_required``（六扇窗口的 ``CANDIDATES`` 派发都填它）：窗口
    "要一张什么牌"只有一处真源，技能不按外层帧的牌反推（濒死帧上没有牌、无懈窗口的帧上
    是被无懈的锦囊、借刀的帧上是【借刀杀人】本身——三处反推得到的都不是要的那张）。
    """
    if ctx.scope not in _PROVIDER_SCOPES:
        return None
    return ctx.respond_required


def _guohuo_provider_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """提供者入口的候选：需要牌时手里有牌、且本牌堆真能当那张牌 ⇒ 追加 ``ActivateSkill``。

    "每提供者只问一次"由候选生成器统一过滤 ``ctx.providers_used``（与【八卦阵】同一姿态），
    这里只判本技能自己的四条谓词：

    1. **需要者就是于吉自己**（``ctx.owner == ctx.subject``）；
    2. 付得起：手牌非空、且至少有一种可声明的牌名；
    3. **收集式窗口的赢家还没定**（``ctx.respond_declared == False``）：产物是**扣置一张
       手牌**换来的，赢家已定时它不会进结算，发候选就是让玩家白扣一张牌；
    4. **借刀窗口够得着 Bn**：产物是一张【杀】，与真牌候选同一道判据
       （``forced_use_targets``，Bn 由 ``ctx.respond_targets`` 给出）。
    """
    if ctx.owner != ctx.subject or ctx.respond_declared:
        return current
    required = _guohuo_window_required(ctx)
    if required is None:
        return current
    if not state.player(ctx.subject).hand:
        return current
    table = _guohuo_response_names(state, required)
    if not table:
        return current
    if ctx.respond_mode is RespondMode.USE and ctx.respond_targets:
        # 产物是【杀】⇒ "它够不够得着 Bn"是**产物牌**的事实（与真牌候选同一道判据
        # ``forced_use_targets``），不是窗口形状：两个入参（``respond_mode`` /
        # ``respond_targets``）本来就随候选派发下传，这里只读它们、不另推窗口。
        # 三个杀族牌名共用同一套目标规则，逐个判而非只判一个，免得多写一份"哪个牌名算数"。
        if not any(
            forced_use_targets(
                state,
                ctx.subject,
                pure_virtual_card(state, type_id),
                ctx.respond_targets,
            )
            for type_id in table.values()
        ):
            return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _guohuo_response_names(
    state: GameState, required: str, *, asker: int | None = None, dying: int | None = None
) -> dict[str, str]:
    """**"需要牌"窗口**里可声明的牌名：``显示名 → 牌型 id``（只留能当 ``required`` 的）。

    与出牌阶段同一个底表（:func:`_guohuo_declaration_table`：基本牌 + 普通锦囊、且本牌堆
    真有），最后那道闸不同——这里判"它能不能充当这次响应"（牌名闸门 ``satisfies_required``，
    杀族请求因此含火 / 雷【杀】）；出牌阶段则**列全**，把"此刻没有目标池"的名字交给
    :func:`_guohuo_name_disabled` 置灰（票 04 §3）。两道闸都只有一份实现，不给技能再写一份
    "什么牌能当什么牌"。产物按**纯虚拟牌**试闸：它要的是牌名，与来源牌的牌面（花色 / 点数）
    无关。

    **求桃窗口的语境放宽**（用户 2026-11-02 裁定）：牌名闸门是逐字比对的，【酒】永远不
    满足 ``peach``；但**濒死者自己**被问时，【酒】与【桃】是同一档自救牌（真牌路径由
    ``TargetRule.near_death_self_only`` 表达同一件事，见 `effects/actions.py:_peach`）。
    故这里按同一份语境补一条：``asker == dying`` ⇒ 允许声明【酒】。别人濒死时**不**放宽
    （酒只能自救）；濒死自救的酒**不受**出牌阶段那次酒的限制（不查计数、也不记账）。
    """
    extra: frozenset[str] = frozenset()
    if PEACH_RESPONSE_CARD == required and dying is not None and asker == dying:
        extra = _GUOHUO_SELF_RESCUE_NAMES
    return {
        name: type_id
        for name, type_id in _guohuo_declaration_table(state).items()
        if type_id in extra or satisfies_required(pure_virtual_card(state, type_id), required)
    }


def _guohuo_truth(state: GameState, frame: Any) -> bool:
    """验明结论：实体牌的**牌型** == 声明的牌名（逐字相等，火【杀】≠【杀】）。

    不走 ``is_slash`` 一类的族判据：验明看的是"是不是就是你说的那张牌"，与"能不能当【杀】
    用"是两件事（后者才是族判据，见 `docs/agents/skill-authoring/shapes.md` 的封闭集登记表）。
    """
    return state.card(int(frame.data[_GUOHUO_SOURCE])).type == str(frame.data[_GUOHUO_NAME])


def _guohuo_live(state: GameState, frame: Any) -> bool:
    """这张被质疑的牌**还要不要结算**：**无人质疑 或（真 且 ♥️）** ⇒ 结算；否则作废。

    牌面原文："若有其他角色质疑则翻开此牌且**此牌作废**，若为：假，质疑者各摸一张牌；真，
    质疑者各失去 1 点体力，然后若被质疑的牌的花色为♥️，此牌依旧进行结算。"

    用户 2026-11-02 的读法（`04` 票 §1 的五行真值表；第四行 = 假 + ♥ + 有人质疑 ⇒ **作废**，
    2026-11-02 实机复验时纠正）：**有人质疑 ⇒ 作废**；真 / 假只决定**质疑者的惩罚**（真 ⇒
    各失 1 点、假 ⇒ 各摸 1 张）；"依旧进行结算"是**真**这张牌在♥️ 时的**追加**豁免 ⇒
    **假牌即便♥️ 也作废**。没人质疑时没有"翻开且作废"那一步，照常结算（与花色、真假无关）。

    ♥ 判据读**来源实体牌**的花色（G6）：声明成【火攻】的红桃牌，看的是那张红桃实体牌，
    不是产物的花色。
    """
    if not frame.data.get(_GUOHUO_YES):
        return True
    source = state.card(int(frame.data[_GUOHUO_SOURCE]))
    return _guohuo_truth(state, frame) and source.suit == Suit.HEART.value


def _guohuo_doubt_round(host: Any, frame: Any, owner: int) -> tuple[Control, bool]:
    """段 5–7：**逐座位质疑 → 翻开验明 → 按收集顺序执行结果**（两个入口共用）。

    牌面的结算与入口无关（出牌阶段主动 / 需要牌时的提供者都走这一段），复制一份就会让
    "真 / 假 / ♥️"在两处各判一次。三段各有自己的帧内游标（``guohuo_asked`` /
    ``guohuo_doubted`` / ``guohuo_revealed`` / ``guohuo_result_next``），每段都可能挂起：
    逐座位询问、扣 1 点体力压出濒死子帧、摸牌内联"获得牌后"。

    返回 ``(control, halted)``：``control`` 非 ``None`` ⇒ 已让出控制权，调用方原样透传；
    ``halted`` ⇒ 摸牌掏空牌堆、本局已判平局（既有 G10），调用方不必再往下结算。
    """
    state = host.state
    asked: list[int] = frame.data.setdefault(_GUOHUO_ASKED, [])
    yes: list[int] = frame.data.setdefault(_GUOHUO_YES, [])

    # ---- 段 5：逐座位质疑（从下家起逆时针；**收齐**答复后再执行结果）----------
    order = _guohuo_doubters(state, owner)
    while len(asked) < len(order):
        seat = order[len(asked)]
        answer = host.ask_option(
            frame,
            seat,
            Timing.USE_ON_USE,
            skill_id=f"{GUOHUO}:doubt",
            options=(GUOHUO_TRUST, GUOHUO_DOUBT),
        )
        if host.suspended(answer):
            return "stay", False
        asked.append(seat)
        if str(answer.choice) == GUOHUO_DOUBT:
            yes.append(seat)

    # ---- 段 6：翻开验明（公开亮出；只揭示、不移动 —— M5-06 的唯一写入口）-----
    if not frame.data.get(_GUOHUO_REVEALED):
        host.reveal_card(frame, owner, state.card(int(frame.data[_GUOHUO_SOURCE])))
        frame.data[_GUOHUO_REVEALED] = True

    # ---- 段 7：按收集顺序依次执行结果（真 ⇒ 各失去 1 点；假 ⇒ 各摸 1 张）------
    truth = _guohuo_truth(state, frame)
    index = int(frame.data.get(_GUOHUO_RESULT, 0))
    while index < len(yes):
        seat = int(yes[index])
        flag = f"{_GUOHUO_RESULT_SEAT}:{seat}"
        if not frame.data.get(flag):
            frame.data[flag] = True
            if truth:
                host.push_frame(
                    FrameKind.LIFE_DECREASE,
                    {"target": seat, "source": None, "amount": 1},
                    timing=Timing.LIFE_DECREASE_BEFORE,
                )
                return "stay", False
            drawn = host.draw_for_hand(frame, seat, 1)
            if drawn == "stay":
                return "stay", False
            if drawn is False:
                return None, True
        index += 1
        frame.data[_GUOHUO_RESULT] = index
    return None, False


def _guohuo_pick_card(host: Any, frame: Any, owner: int, timing: Timing) -> Any:
    """提供者入口的段 1：扣置哪一张手牌 —— 自己的手牌，**牌面直选**。

    出牌阶段的同一问由 :data:`GUOHUO_FLOW` 的 `PickCards` 承担（预发动把这一问画进清单），
    故这里是**手写段机**那一侧的实现，两个入口各问各的、产物形状一致（实体牌 id）。

    返回 ``card_id`` / ``None``（池为空，零张）/ ``SUSPEND``（挂起）。池为空只有一种成因：
    手里没牌时提供者不产候选，故这里按"整条取消"处理。
    """
    picked = host.ask_selection(
        frame,
        owner=_ASK_GUOHUO_CARD,
        key="guohuo:card",
        actor=owner,
        timing=timing,
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
        build_face=lambda: host.selection_face(owner, (Zone.HAND,), reveal_hand=True),
    )
    if host.suspended(picked):
        return picked
    return None if not picked else int(picked[0])


def _guohuo_kouchu(host: Any, frame: Any, owner: int) -> Control:
    """段 3（两个入口共用）：扣置 = 手牌 → 处理区，``reason = LOSE``（**算失去牌**）。

    走 ``move_in_frame`` 而不是 ``move_card``：这次搬牌要内联派发「失去牌后」（连营 / 枭姬
    一类），钩子可能提问 ⇒ 可能挂起，故用帧内幂等标记 ``guohuo_kouchu`` 兜重入。

    ``actor`` 必须**显式给**：承载帧是外层窗口的帧（濒死帧没有 ``source`` 键，会被
    ``move_in_frame`` 的缺省读法撞成 ``KeyError``），而这次移动的操作者本来就是于吉自己。
    """
    if frame.data.get(_GUOHUO_KOUCHU):
        return None
    control = host.move_in_frame(
        frame,
        host.state.refs((int(frame.data[_GUOHUO_SOURCE]),)),
        to_zone=ZoneRef(Zone.PROCESSING),
        reason=MoveReason.LOSE,
        owner=owner,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    frame.data[_GUOHUO_KOUCHU] = True
    return None


def _guohuo_targets_for(
    state: GameState, seat: int, card: CardRef
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """``(候选池, 必须指定的目标数)``：目标数与池都读与真牌同一条规则。"""
    pool = check_targets(state, seat, card)
    rule = behavior_for(card).target_rule
    count = rule.target_count(len(pool))
    if count <= 0:
        return pool, 0
    return pool, count


def _guohuo_ask_seat(
    host: Any, frame: Any, owner: int, step: str, pool: tuple[int, ...]
) -> int | None:
    """问一次**座位**（目标步 / 附带指定步共用）：返回座位号，挂起 ⇒ ``None``。

    ``step`` 决定 ``skill_id`` 的后缀（``target`` / ``designation``）：``ask_option`` 的帧内
    答案缓存键是 ``(skill_id, actor)``，同一次发动里的两问**必须**用不同的后缀，否则前一个
    答案会被当这一问的答案（见票面的"新踩的坑"）。
    """
    picked = host.ask_option(
        frame,
        owner,
        Timing.PHASE_PLAY_IDLE,
        skill_id=f"{GUOHUO}:{step}",
        options=tuple(_seat_choice(seat) for seat in pool),
    )
    if host.suspended(picked):
        return None
    return _option_seat(picked)


def _guohuo_flow_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """预发动提交点：把流水线的两组答案烘进帧私有键，再从**段 3（扣置）**继续跑段机。

    用户 2026-11-02 的 Q3 裁定 = A：出牌阶段入口是"点蛊惑 → 选 1 张手牌 → 牌名弹窗 →
    **一次提交**"，故段 1–2 变成 :data:`GUOHUO_FLOW` 的两步（清单与校验器都从它渲染），
    提交点只做一件翻译：``answers["card"] / answers["name"]`` → 段机认的那两个键
    （``guohuo_source`` 实体牌 id / ``guohuo_name`` 牌型 id）。段 3–8 一字不动。

    重入：``effect`` 会在挂起后的续跑里**再进来一次**（`flow.run_flow` 的既有语义），故
    "写翻译"这一步按段游标守卫——第二次进来时 ``guohuo_stage`` 已经在（段机自己推进它），
    不再从答案重写，也不会把已经推进的阶段打回 ``kouchu``。
    """
    owner = int(frame.data["source"])  # SKILL 帧的载荷没有 skill_owner（A 族=发动者）
    if _GUOHUO_STAGE not in frame.data:
        table = _guohuo_declaration_table(host.state)
        declared = str(answers["name"])
        type_id = table.get(declared)
        if type_id is None:  # 校验器已拦过同一份闭集；走到这里就是声明写错了
            raise ValueError(f"【蛊惑】声明的牌名 {declared!r} 不在本牌堆的可声明表里")
        source = _guohuo_selected_card(answers)
        if source is None:  # 第一步是必答（lo=1），走到这里同样是声明写错了
            raise ValueError("【蛊惑】的预发动没有带出扣置的那张牌")
        frame.data[_GUOHUO_SOURCE] = source
        frame.data[_GUOHUO_NAME] = type_id
        frame.data[_GUOHUO_STAGE] = "kouchu"
    return _guohuo_settle(host, frame, owner)


GUOHUO_FLOW = SkillFlow(
    name="guohuo",
    steps=(
        # 步 1：扣置哪张手牌（**牌面直选**：自己的手牌，票 22 的 own-zone 形态）。
        PickCards(zones=(Zone.HAND,), lo=1, hi=1, key="card"),
        # 步 2：声明牌名。闭集 = 本牌堆真有的全部基本牌 + 普通锦囊（用户 Q1A：列全）；
        # "此刻用不了"的名字**不删**，进 ``disabled`` 置灰（校验器同样拒收）。
        PickOption(
            options=_guohuo_name_options,
            disabled=_guohuo_name_disabled,
            optional=False,
            key="name",
        ),
    ),
    effect=_guohuo_flow_effect,
    # **无次数限制**（用户 2026-11-01 裁定）⇒ 不落 ``_USED`` 阶段位（同【仁德】）。
    once_per_phase=False,
)
"""【蛊惑】出牌阶段入口的两步流水线：扣置哪张手牌 → 声明成什么牌 → 段机接管。

**只表达前两步**：后面的目标 / 附带指定 / 逐座位质疑 / 验明 / 结果都不是"发动前一次填完"
的答案（质疑是**逐座位循环**，`flow.py:SkillFlow` 明说本语言没有循环步骤），故由
:func:`_guohuo_settle` 的手写段机承担。"需要牌时"的提供者入口另有段机
（:func:`guohuo_provider`）：两者的**选牌问法各一套**（这里走流水线的 `PickCards`、提供者
走手写的 :func:`_guohuo_pick_card`），段 3 / 5–7（扣置 / 质疑 / 验明 / 结果）共用一份实现。
"""


def _guohuo_settle(host: Any, frame: Any, owner: int) -> Control:
    """段 3–8：扣置 → 目标 →（附带指定）→ 质疑 → 验明 → 结果 → 结算 / 作废。

    由 :func:`_guohuo_flow_effect` 在段 2 之后调用；剩余六段由帧私有 ``guohuo_stage``
    串起来（每一段都可能挂起：扣置搬牌会内联派发"失去牌后"，质疑是逐座位的询问，结果里
    扣体力会压子帧、摸牌会内联"获得牌后"，收尾的 `start_use` 会压整条使用链）。重入时按
    阶段键续跑，已完成的段不重做。

    段 3 与段 5–7 与**提供者入口**（:func:`guohuo_provider`）共用同一份实现；本入口多出来
    的只有"选目标 / 附带指定 / 出牌阶段的使用时机"。
    """
    state = host.state
    stage = frame.data.get(_GUOHUO_STAGE, "kouchu")

    # ---- 段 3：扣置进处理区（算失去牌；牌面仍随处理区的公开投影）----
    if stage == "kouchu":
        control = _guohuo_kouchu(host, frame, owner)
        if control is not None:
            return control
        frame.data[_GUOHUO_STAGE] = "targets"

    # ---- 段 4：选择目标（按声明的牌名现算；无目标 / 全体目标不询问）----------
    if stage in ("kouchu", "targets"):
        if _GUOHUO_TARGETS not in frame.data:
            card = _guohuo_virtual(state, frame)
            pool, count = _guohuo_targets_for(state, owner, card)
            if count <= 0:
                frame.data[_GUOHUO_TARGETS] = ()
            elif count >= len(pool):
                frame.data[_GUOHUO_TARGETS] = tuple(pool)
            elif count == 1:
                picked = _guohuo_ask_seat(host, frame, owner, "target", pool)
                if picked is None:
                    return "stay"
                frame.data[_GUOHUO_TARGETS] = (picked,)
            else:
                # 区间规则（如【铁索连环】1–2 个）：取**下界**张，顺序由座位序定格。
                frame.data[_GUOHUO_TARGETS] = tuple(pool[:count])
        frame.data[_GUOHUO_STAGE] = "designation"

    # ---- 段 4b：附带指定（【借刀杀人】的 Bn：谁挨持刀者那一刀）-------------------
    # 目标步（持刀者 A）之后再问一次：Bn 由**使用者**一并指定（W5-02a / W5-02b），随
    # ``start_use(..., designations=(Bn,))`` 烘进 USE 帧 —— 借刀的结算只认那一份，缺了
    # 就 fail-closed。要不要这一步、Bn 能是谁，两句都与出牌阶段清单
    # （`designation_actions_for`）同源：``needs_designation``（行为表上的
    # ``borrowed_sword_target``）+ ``targets_within_attack_range``（持刀者的攻击范围）。
    # 不需要附带指定的牌名记一份空元组就过（段游标要能分辨"这一步做完了"）。
    if stage in ("kouchu", "targets", "designation"):
        if _GUOHUO_DESIGNATIONS not in frame.data:
            card = _guohuo_virtual(state, frame)
            targets = tuple(frame.data[_GUOHUO_TARGETS])
            if not needs_designation(card):
                frame.data[_GUOHUO_DESIGNATIONS] = ()
            else:
                # 借刀的额定目标恰好一名持刀者（W5-02b 的 A）。
                pool = (
                    targets_within_attack_range(state, targets[0])
                    if len(targets) == 1
                    else ()
                )
                if not pool:
                    # 声明时"持刀者够得着谁"已由借刀的第 0 层滤子（``borrowed_sword_target``，
                    # 内含"攻击范围内至少有一个合法【杀】目标"）验过非空，故这里撞上空池只
                    # 可能是扣置之后状态翻转发出了缝 ⇒ fail-closed（同 ``ops.py`` 借刀缺附带
                    # 指定那条），不产出一条必崩的使用。
                    raise ValueError(
                        "【蛊惑】声明的【借刀杀人】没有可指定的 Bn（扣置后持刀者够不着任何人）"
                    )
                if len(pool) == 1:
                    # 只有一个候选 ⇒ **不问**（同"只剩一种可声明的牌名时不问"的口径）。
                    frame.data[_GUOHUO_DESIGNATIONS] = tuple(pool)
                else:
                    picked = _guohuo_ask_seat(host, frame, owner, "designation", pool)
                    if picked is None:
                        return "stay"
                    frame.data[_GUOHUO_DESIGNATIONS] = (picked,)
        frame.data[_GUOHUO_STAGE] = "doubt"

    # ---- 段 5–7：质疑轮询 → 翻开验明 → 按收集顺序执行结果（与提供者入口共用）----
    control, halted = _guohuo_doubt_round(host, frame, owner)
    if control is not None:
        return control
    if halted:  # 摸牌掏空牌堆 ⇒ 平局已定（既有 G10），不再往下结算
        return None
    frame.data[_GUOHUO_STAGE] = "settle"

    # ---- 段 8：无人质疑 /（真 且 ♥️）⇒ 依旧结算；否则从效果区作废进弃牌堆 ---------
    if frame.data.get(_GUOHUO_SETTLED):
        return None
    frame.data[_GUOHUO_SETTLED] = True
    if _guohuo_live(state, frame):
        host.start_use(
            _guohuo_virtual(state, frame),
            source=owner,
            targets=tuple(frame.data[_GUOHUO_TARGETS]),
            use_timing=Timing.PHASE_PLAY_IDLE,
            designations=tuple(frame.data[_GUOHUO_DESIGNATIONS]),
        )
        return "stay"
    host.move_in_frame(
        frame,
        state.refs((int(frame.data[_GUOHUO_SOURCE]),)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=owner,
    )
    return "stay"


def guohuo_provider(host: Any, frame: Any, actor: int, timing: Timing) -> Control:
    """提供者回调：需要一张牌时**扣置一张手牌当它**（蛊惑的第二个入口）。

    与出牌阶段入口是**同一套结算**（扣置 → 声明 → 质疑 → 验明 → 结果），段 1 / 3 与
    段 4–6 直接复用同一份实现；差别只有三处：

    1. **声明范围由窗口收窄**：只列能当窗口要的那张牌的牌名（牌名闸门现算），只有一种时
       不问（108 档的【闪】/ 普通【杀】/【桃】/【无懈可击】都恰好一种，故这一步通常不出现）；
    2. **没有目标步**：产物是响应牌（【闪】/【杀】/【桃】/【无懈可击】），目标要么为空、
       要么由外层窗口承载（求桃的濒死者、借刀的 Bn、无懈的那一层）；
    3. **产物交给窗口**：有效 ⇒ 把虚拟牌写进 ``PROVIDER_CARD_KEY`` 并写
       ``satisfied=True``，**不自己压使用 / 打出帧**——六扇窗口对它的消费各不相同（使用 /
       打出 / 求桃的回复结算 / 对 Bn 使用 / 无懈的"先声明、赢家才使用"）。作废 ⇒ 牌从
       处理区进弃牌堆、**从未使用 / 打出**，``satisfied=False`` ⇒ 外层窗口照常继续问真牌
       （这就是作废不需要"受理后回滚"的原因：轮询在受理之前）。

    重入纪律（``CODING_STANDARDS.md`` §3 的幂等）：每段一个帧内游标；收尾前先写
    ``satisfied``（本函数**不**压帧，故没有"子帧出栈后重入"的那一次，只有内联钩子挂起
    引发的重入——作废那条的弃置因此靠 ``move_in_frame`` 自己的重入守卫兜住）。
    """
    # 惰性 import：`lord_request` 反向依赖 `catalog`（本模块由 catalog 末尾导入），
    # 模块级 import 会成环（见 `catalog.py` 末尾的 import 注释）。
    from .lord_request import (
        PROVIDER_CARD_KEY,
        PROVIDER_REQUIRED_KEY,
        PROVIDER_SATISFIED_KEY,
    )

    required = frame.data.get(PROVIDER_REQUIRED_KEY)
    if required is None:
        # 引擎在派发提供者之前写好"这扇窗口要什么牌"；缺失即编程错误（不猜、不回退）。
        raise NotImplementedError("【蛊惑】提供者缺少窗口的 required（引擎应写 PROVIDER_REQUIRED_KEY）")
    state = host.state
    stage = frame.data.get(_GUOHUO_PROVIDER_STAGE, "card")

    def _give_up() -> None:
        """这次响应**没发生**：牌一张不动、不压任何帧（作废那条走段 7 的弃置）。

        直接写回引擎的结果键就够了——这条路上什么都没挂起，外层窗口马上拿到
        ``satisfied=False`` 并继续问真牌。
        """
        frame.data[PROVIDER_SATISFIED_KEY] = False

    # ---- 段 1：扣置一张手牌（手牌 → 处理区；算失去牌）------------------------
    if stage == "card":
        picked = _guohuo_pick_card(host, frame, actor, timing)
        if host.suspended(picked):
            return "stay"
        if picked is None:
            _give_up()
            return None
        frame.data[_GUOHUO_SOURCE] = picked
        frame.data[_GUOHUO_PROVIDER_STAGE] = "name"

    # ---- 段 2：声明牌名（**只列能当窗口要的那张牌的牌名**）-------------------
    if stage in ("card", "name"):
        # 求桃窗口：濒死者**本人**被问时【酒】与【桃】同档（用户 2026-11-02 裁定）。
        # "濒死者是谁"由窗口帧给出（`near_death_ask` 的 `target`），不另推窗口形状。
        table = _guohuo_response_names(
            state,
            str(required),
            asker=actor,
            dying=(
                int(frame.data["target"])
                if frame.kind is FrameKind.NEAR_DEATH and "target" in frame.data
                else None
            ),
        )
        if not table:  # 本牌堆没有能当它的牌名 ⇒ 这次响应没发生（零副作用）
            _give_up()
            return None
        if len(table) == 1:
            # 只有一种可声明的牌名 ⇒ **不问**（同"该牌名无目标 ⇒ 目标步不出现"的口径）：
            # 108 档里【闪】/ 普通【杀】/【桃】/【无懈可击】各只有一种，故这一步通常不出现。
            type_id = next(iter(table.values()))
        else:
            choice = host.ask_option(
                frame,
                actor,
                timing,
                skill_id=f"{GUOHUO}:name",
                options=tuple(table),
            )
            if host.suspended(choice):
                return "stay"
            type_id = table[str(choice.choice)]
        frame.data[_GUOHUO_NAME] = type_id
        frame.data[_GUOHUO_PROVIDER_STAGE] = "kouchu"

    # ---- 段 3：扣置进处理区（算失去牌）--------------------------------------
    if stage in ("card", "name", "kouchu"):
        control = _guohuo_kouchu(host, frame, actor)
        if control is not None:
            return control
        frame.data[_GUOHUO_PROVIDER_STAGE] = "doubt"

    # ---- 段 4–6：质疑 / 验明 / 结果（与出牌阶段入口共用）----------------------
    control, halted = _guohuo_doubt_round(host, frame, actor)
    if control is not None:
        return control
    if halted:  # 摸牌掏空牌堆 ⇒ 平局已定（既有 G10）
        _give_up()
        return None

    # ---- 段 7：无人质疑 /（真 且 ♥️）⇒ 把产物交给窗口；否则作废（未使用 / 打出）--
    live = _guohuo_live(state, frame)
    frame.data[PROVIDER_SATISFIED_KEY] = live
    if not live:
        # 作废：扣置过的牌进弃牌堆。搬牌可能内联派发「失去牌后」而挂起，重入时
        # ``move_in_frame`` 按"这次移动已经搬完"认出同一次移动（不再搬第二遍）。
        control = host.move_in_frame(
            frame,
            state.refs((int(frame.data[_GUOHUO_SOURCE]),)),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=actor,
            actor=actor,
        )
        return control if control == "stay" else None
    frame.data[PROVIDER_CARD_KEY] = _guohuo_virtual(state, frame)
    return None


# ---------------------------------------------------------------------------
# 张角移动版【黄天】（feng-zhangjiao 票 01 / 02）——**主公技 · 跨座位注入**
#
# 牌面（官方移动版张角页）："主公技，其他群势力角色的出牌阶段限一次，该角色可以交给你
# 一张【闪】或【闪电】。"
#
# 与 A 族其余九条的差别只有一处，且是**声明面的**：候选由 `CANDIDATES` 注入**别人的**
# 出牌阶段（`ctx.owner` = 张角、`ctx.subject` = 发动者），而不是持有者自己的。执行面沿用
# A 族的同一条路：`Engine.start_skill(source=发动者)` 压 SKILL 帧，`run_flow` 的 owner
# 缺省读 `frame.data["source"]` —— 于是"选自己的手牌"自然是发动者自己的手牌。
#
# **次数**按发动者计（每名群势力角色每出牌阶段一次），落成**张角实例键上的座位位段**
# （`_HUANGTIAN_MASK`）：见 `catalog._HUANGTIAN_SHIFT` 的说明——落在发动者名下会被
# `prune_skill_state` 在每次搬牌后抹掉。
# ---------------------------------------------------------------------------

HUANGTIAN = hero_skill_id("zhangjiao_mobile", "huangtian")
"""张角移动版【黄天】的全限定技能 id。"""

_HUANGTIAN_CARD_TYPES: tuple[str, ...] = ("dodge", "lightning")
"""可以交出的牌名：「一张【闪】或【闪电】」（牌名 id 白名单）。"""

_HUANGTIAN_MOVED_KEY = "huangtian_moved"
"""帧私有：这次发动的手牌已经交给张角（续跑不重搬）。"""


def _huangtian_lord(state: GameState) -> int | None:
    """持有【黄天】的座位（武将牌上是张角；也可能是**获得**了这条主公技的非主公）。

    身份门控只作用于**武将牌载体**（wuming 票 06），故持有者不保证是主公；效果侧只认
    实例、不认身份（交出的牌进持有者手牌）。读**存活实例**而不是"名字叫张角的座位"：
    同一真名可以有两个版本、但武将牌上的主公技只有一个主公 ⇒ 至多一个武将牌实例；
    找不到（已阵亡 / 构造局面）时返回 ``None``，效果侧整条取消；多实例时取解析序第一条。
    """
    for instance in resolve_instances(state):
        if instance.skill_id == HUANGTIAN:
            return instance.owner
    return None


def _huangtian_given(state: GameState, lord: int, seat: int) -> bool:
    """``seat`` 本出牌阶段是否已经把牌交给张角（座位位段，键在**张角**的实例上）。"""
    key = _skill_key(state, lord, HUANGTIAN)
    if key is None:
        return True  # 实例不在 ⇒ 记不了"已交过" ⇒ fail-closed，不产出候选
    return bool(state.skill_state.get(key, 0) & (1 << (_HUANGTIAN_SHIFT + seat)))


def _mark_huangtian_given(state: GameState, lord: int, seat: int) -> None:
    """记下"本阶段 ``seat`` 已经交过牌"（位段随出牌阶段复位）。"""
    key = _skill_key(state, lord, HUANGTIAN)
    if key is not None and 0 <= seat < 8:
        state.skill_state[key] = state.skill_state.get(key, 0) | (
            1 << (_HUANGTIAN_SHIFT + seat)
        )


def _huangtian_hand_cards(state: GameState, seat: int) -> tuple[int, ...]:
    """``seat`` 手牌里能交出的那张（【闪】/【闪电】），按手牌序。"""
    return tuple(
        card_id
        for card_id in state.player(seat).hand
        if state.card(card_id).type in _HUANGTIAN_CARD_TYPES
    )


def _huangtian_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：**别座位的出牌阶段**里，让群势力角色交一张【闪】/【闪电】给张角。

    ``ctx.owner`` = 张角（实例持有者）、``ctx.subject`` = 正在出牌的那个座位。四个门：
    作用域必须是出牌阶段、发动者**不是**张角本人（"其他"）、势力为**群**（与张角同势力）、
    本阶段还没交过、手里真有一张可交的牌（没有候选就不产出——`edge-cases.md` 总纪律 1）。
    """
    lord = ctx.owner
    seat = ctx.subject
    if ctx.scope is not CandidateScope.PLAY:
        return current
    if seat == lord or not state.player(seat).alive:
        return current
    if not same_kingdom(state, lord, seat):
        return current
    if _huangtian_given(state, lord, seat):
        return current
    if not _huangtian_hand_cards(state, seat):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _huangtian_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：把选中的那张牌交给张角（``PUT``、归属 = 张角），并记"本阶段交过"。

    张角在选牌之后阵亡（或实例消失）⇒ 整条取消、**零副作用**：牌留在发动者手里。
    搬牌可能内联派发"失去牌后"而挂起，故先落帧私有 ``huangtian_moved`` 再搬。
    """
    seat = frame.data["source"]
    lord = _huangtian_lord(host.state)
    if lord is None:
        return None
    if frame.data.get(_HUANGTIAN_MOVED_KEY):
        return None
    cards = tuple(int(card_id) for card_id in answers.get("card", ()))
    if not cards:
        return None
    frame.data[_HUANGTIAN_MOVED_KEY] = True
    _mark_huangtian_given(host.state, lord, seat)
    control = host.move_in_frame(
        frame,
        host.state.refs(cards),
        to_zone=ZoneRef(Zone.HAND, lord),
        reason=MoveReason.PUT,
        owner=lord,
        actor=lord,
    )
    return control if control == "stay" else None


HUANGTIAN_FLOW = SkillFlow(
    name="huangtian",
    steps=(
        PickCards(
            zones=(Zone.HAND,),
            lo=1,
            hi=1,
            optional=False,
            key="card",
            card_types=_HUANGTIAN_CARD_TYPES,
        ),
    ),
    effect=_huangtian_effect,
)
"""【黄天】的一步流水线：选一张【闪】/【闪电】（自己的手牌）→ 交给张角。"""



# ---------------------------------------------------------------------------
# 神周瑜移动版【业炎】（huo-shenzhouyu-mobile 票 01 / 框架票 05；用户 2026-11-02 逐字指定交互）
#
# 牌面（用户改判后的文本，与 OL 现行一致）：
#
#     业炎
#     　限定技，出牌阶段，你可以选择至多三名角色，对这些角色造成共计至多3点火焰伤害
#     　（若你将对一名角色分配大于等于2点火焰伤害，你须先弃置四张花色各不相同的手牌并失去3点体力）。
#
# 交互（**用户 2026-11-02 指定的两步**，票 05 的计数式分配询问是它的载体）：
# 1. 分配弹窗：列出全部存活座位，每座一个 0–3 计数、合计 ≤ 3、可重置、随时提交、超时自动提交；
# 2. 任一计数 ≥ 2 ⇒ 必须选四花色手牌各一张（涉猎形态）⇒ **分支 1**；全部 ≤ 1 ⇒ **分支 2**。
#    分支 1 = 弃四张 → 失去 3 点体力 → 按**从你开始的行动顺序**造成伤害（三段按序入事件列表，
#    你因此阵亡也照常结算，伤害来源改为无来源）；分支 2 = 直接按同一顺序造成伤害。
#
# 计数全 0 也是一次发动（限定技照常消耗）：用户在票面里明确"最少可以为0，随时可以提交"。
# 代价付不起（手牌凑不出四花色）时**单座计数上限压到 1**（01 票 R4）：界面上"要付却付不起"
# 的死路从候选生成期就不存在。
# ---------------------------------------------------------------------------

YEYAN = hero_skill_id("shen_zhouyu_mobile", "yeyan")
"""神周瑜移动版【业炎】的全限定技能 id。"""

YEYAN_TOTAL = 3
"""「共计至多3点」：分配合计上限。"""

YEYAN_COST_HP = 3
"""代价里的「失去3点体力」。"""

YEYAN_COST_CARDS = 4
"""代价里的「四张花色各不相同的手牌」。"""

_YEYAN_ALLOCATION = "yeyan_allocation"
"""帧私有：分配结果（``((座位, 点数), ...)``，省略 0；提问一次、重入复用）。"""

_YEYAN_PAID = "yeyan_paid"
"""帧私有：代价已付（弃四张 + 压过失去体力帧；重入不重复付）。"""

_YEYAN_DONE = "yeyan_done"
"""帧私有：伤害已经结算过的座位（逐座压帧，重入既不重做也不漏做）。"""

_YEYAN_ORDER = "yeyan_order"
"""帧私有：伤害结算名单（首次进入时按"从你开始的行动顺序"定格）。"""

_ASK_YEYAN_COST = "hero.shen_zhouyu_mobile.yeyan.cost"


def _yeyan_cost_payable(state: GameState, owner: int) -> bool:
    """代价付得起吗：手牌里凑得出**四种花色各一张**。"""
    suits = {
        card.suit
        for card in (state.card(card_id) for card_id in state.player(owner).hand)
        if card.suit is not None
    }
    return len(suits) >= YEYAN_COST_CARDS


def _yeyan_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：自己的出牌阶段（限定技的"整局已用"门由 ``_limited_offers`` 包在外面）。

    "至多3点"允许全 0 提交 ⇒ 只要在出牌阶段就有合法分配，不另设"有没有收益"的收窄。
    """
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _yeyan_cost_face(host: Any, owner: int) -> Any:
    """代价选牌面：**自己的手牌**（四花色各一张；同花色置灰由 ``max_per_suit`` 收窄）。"""
    slots, card_ids = host.selection_face(owner, (Zone.HAND,), reveal_hand=True)
    return slots, card_ids


def _yeyan_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：分配弹窗 → （代价）→ 按行动顺序逐座压火焰伤害帧。"""
    del answers
    state = host.state
    owner = int(frame.data["source"])
    timing = Timing.PHASE_PLAY_IDLE
    allocation = frame.data.get(_YEYAN_ALLOCATION)
    if allocation is None:
        answer = host.ask_distribution(
            frame,
            owner,
            timing,
            skill_id=f"{YEYAN}:distribute",
            seats=alive_seats(state),
            max_total=YEYAN_TOTAL,
            # 付不起代价 ⇒ 单座上限压到 1（01 票 R4）：任何计数 ≥2 都要付代价，界面不该给出
            # 一条点了必然付不起的路。
            per_seat_max=YEYAN_TOTAL if _yeyan_cost_payable(state, owner) else 1,
        )
        if host.suspended(answer):
            return "stay"
        allocation = tuple((int(seat), int(count)) for seat, count in answer.counts)
        frame.data[_YEYAN_ALLOCATION] = allocation
    heavy = any(count >= 2 for _, count in allocation)
    if heavy and not frame.data.get(_YEYAN_PAID):
        picked = host.ask_selection(
            frame,
            owner=_ASK_YEYAN_COST,
            key="yeyan:cost",
            actor=owner,
            timing=timing,
            spec=SelectionSpec(
                zones=(Zone.HAND,), total=(YEYAN_COST_CARDS, YEYAN_COST_CARDS), max_per_suit=1
            ),
            build_face=lambda: _yeyan_cost_face(host, owner),
        )
        if host.suspended(picked):
            return "stay"
        if len(picked) != YEYAN_COST_CARDS:
            # 防御分支：入口已按"四花色齐"收窄（单座上限 2/3 只在付得起时出现）⇒ 走不到。
            return None
        if (
            _move_to_discard(
                host, frame, tuple(int(card) for card in picked), owner=owner, key="yeyan:cost"
            )
            == "stay"
        ):
            return "stay"
        frame.data[_YEYAN_PAID] = True
        # 失去体力 ≠ 受到伤害：一条 LIFE_DECREASE(3) 帧（濒死按一次结算；逐点派发住引擎）。
        host.push_frame(
            FrameKind.LIFE_DECREASE,
            {"target": owner, "source": None, "amount": YEYAN_COST_HP},
            timing=Timing.LIFE_DECREASE_BEFORE,
        )
        return "stay"
    order = frame.data.get(_YEYAN_ORDER)
    amounts = dict(allocation)
    if order is None:
        order = tuple(
            seat
            for seat in seats_from(state, owner, len(state.players))
            if amounts.get(seat, 0) > 0
        )
        frame.data[_YEYAN_ORDER] = order
    done = tuple(frame.data.get(_YEYAN_DONE, ()))
    for seat in order:
        if seat in done:
            continue
        frame.data[_YEYAN_DONE] = (*done, seat)
        if not state.player(seat).alive:
            continue
        # 分支 1 的三段按顺序入事件列表：你已经阵亡时伤害照常结算，只是没有伤害来源。
        _push_damage(
            host,
            frame,
            source=owner if state.player(owner).alive else None,
            target=seat,
            amount=int(amounts[seat]),
            nature=DamageNature.FIRE,
        )
        return "stay"
    return None


YEYAN_FLOW = SkillFlow(name="yeyan", steps=(), effect=_yeyan_effect)
"""【业炎】的零步流水线：发动即进提交点（分配弹窗 + 代价判定的两分支）。"""

_YEYAN = ActiveSkill(
    "shen_zhouyu_mobile",
    "yeyan",
    _yeyan_offers,
    flow=YEYAN_FLOW,
    skill_type=SkillType.LIMITED,
    requirement=(
        "限定技，出牌阶段，你可以选择至多三名角色，对这些角色造成共计至多3点火焰伤害"
        "（若你将对一名角色分配大于等于2点火焰伤害，你须先弃置四张花色各不相同的手牌并失去3点体力）"
    ),
    step_requirements={
        "distribute": "为角色分配火焰伤害（合计至多3点；点一次计数 +1，可重置，随时提交）",
        "card": "弃置四张花色各不相同的手牌（四种花色各一张）",
    },
)

# ---------------------------------------------------------------------------
# 火包·移动版·太史慈【天义】（huo-taishici-xunyu 票 01）
#
#     出牌阶段限一次，你可以与一名角色拼点，然后你本阶段根据拼点结果获得对应效果：
#     若你赢，则你使用【杀】的次数上限+1、使用【杀】无距离限制、使用【杀】的目标数上限+1，
#     否则你不能使用【杀】。
#
# 形状 = A 族主动技 + ``PickSeats`` 单步 + ``effect`` 里发起拼点（``Engine.rank_compare``）、
# 拿到结果后写**座位三态**（``tianyi_won`` / ``tianyi_lost``，见 ``Player`` 的两个字段）。
# 三条效果都是**连续型修饰器**（读座位状态，不落牌 / 不落标记）：
#
# - ``COUNT_LIMIT``：赢 ⇒ ``current + 1``；没赢 ⇒ **0**（只挡**使用**，不挡**打出**——
#   牌面写的是"不能使用【杀】"，账本 774④ 把使用与打出分列）；
# - ``TARGET_POOL``：赢 ⇒ 把全部其他存活座位并入池（"无距离限制" = 目标集豁免，
#   **不是**把 ``DISTANCE`` 改 0，账本 827 行）；
# - ``TARGET_COUNT``：赢 ⇒ ``current + 1``（与方天画戟 +2 同一条加式链）。
#
# 作用域 = **本阶段**（移动版牌面）；清理点与界【诈降】同一处（``_phase_play_end``）。
# ---------------------------------------------------------------------------

TIANYI = hero_skill_id("taishici", "tianyi")
"""太史慈【天义】的全限定技能 id。"""

TIANYI_TITLE = "天义"
"""拼点弹窗的标题（发动拼点的技能名）。"""

_TIANYI_DONE_KEY = "tianyi_done"
"""帧私有：这一次发动已经按拼点结果落位（续跑不重复置位）。"""


def _tianyi_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """拼点目标池：**其他 + 存活 + 有手牌**（规则第十二章 12.1 L1315 / 12.2 L1322 / 12.7 L1370）。

    发起者自己没手牌时整条不可发动（12.1 L1317 的前置条件），由 :func:`_tianyi_offers`
    与这里各挡一次。
    """
    del frame, chosen
    if not state.player(owner).hand:
        return ()
    return tuple(
        player.seat
        for player in state.players
        if player.alive and player.seat != owner and player.hand
    )


def _tianyi_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：出牌阶段限一次 + 自己与至少一名其他角色都有手牌。"""
    if not _can_offer(state, ctx):
        return current
    if not _tianyi_targets(state, None, ctx.subject, ()):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _tianyi_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：发起拼点 → 按结果写座位三态（"本阶段"效果由修饰器读）。"""
    owner = int(frame.data["source"])
    if frame.data.get(_TIANYI_DONE_KEY):
        return None
    if "pindian_result" not in frame.data:
        targets = tuple(int(seat) for seat in answers.get("seat", ()))
        if not targets:
            return None
        host.rank_compare(
            frame,
            initiator=owner,
            targets=targets,
            skill_id=TIANYI,
            title=TIANYI_TITLE,
        )
        return "stay"
    won = bool((frame.data.get("pindian_won") or (False,))[0])
    host.set_tianyi_state(owner, won)
    frame.data[_TIANYI_DONE_KEY] = True
    return None


TIANYI_FLOW = SkillFlow(
    name="tianyi",
    steps=(PickSeats(lo=1, hi=1, pool=_tianyi_targets, key="seat"),),
    effect=_tianyi_effect,
)
"""【天义】的一步流水线：选一名有手牌的其他角色 → 拼点 → 按结果落位。"""


def _tianyi_won(state: GameState, owner: int) -> bool:
    return bool(state.player(owner).tianyi_won)


def _tianyi_lost(state: GameState, owner: int) -> bool:
    return bool(state.player(owner).tianyi_lost)


def _tianyi_count_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``COUNT_LIMIT``：赢 ⇒ +1；没赢 ⇒ 0（只挡使用，见模块头）。"""
    if ctx.owner != ctx.subject or ctx.card is None or not is_slash(behavior_for(ctx.card)):
        return current
    if _tianyi_lost(state, ctx.owner):
        return 0
    if not _tianyi_won(state, ctx.owner):
        return current
    if current is None:
        return None
    return int(current) + 1


def _tianyi_no_distance(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``TARGET_POOL``：赢 ⇒ 本阶段使用【杀】**无距离限制**（目标集并集）。"""
    if ctx.owner != ctx.subject or ctx.card is None or not is_slash(behavior_for(ctx.card)):
        return current
    if not _tianyi_won(state, ctx.owner):
        return current
    everyone = tuple(
        player.seat for player in state.players if player.alive and player.seat != ctx.subject
    )
    return tuple(dict.fromkeys((*current, *everyone)))


def _tianyi_target_count(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``TARGET_COUNT``：赢 ⇒ 使用【杀】的目标数上限 +1（加式，与方天 +2 同链）。"""
    if ctx.owner != ctx.subject or ctx.card is None or not is_slash(behavior_for(ctx.card)):
        return current
    if not _tianyi_won(state, ctx.owner):
        return current
    return int(current) + 1


TIANYI_MODIFIERS: tuple[Modifier, ...] = (
    Modifier(ModifierPoint.COUNT_LIMIT, _tianyi_count_limit),
    Modifier(ModifierPoint.TARGET_POOL, _tianyi_no_distance),
    Modifier(ModifierPoint.TARGET_COUNT, _tianyi_target_count),
)
"""【天义】的三条连续型修饰器（读座位三态，不落标记）。"""


# ---------------------------------------------------------------------------
# 火包·移动版·荀彧【驱虎】（huo-taishici-xunyu 票 02）
#
#     出牌阶段限一次，你可以与一名体力值大于你的角色拼点，若你赢，则该角色对其攻击范围内
#     你选择的一名角色造成1点伤害，否则其对你造成1点伤害。
#
# 用户 2026-11-04 裁定：赢段的承受者是**另一名角色**（相对拼点目标"其"）——**不能是 A
# 本身**、**可以是荀彧**；按移动版措辞实现（OL 页写"你选择的一名角色"，差异登记在账本）。
#
# 形状 = A 族主动技 + ``PickSeats`` 单步 + ``effect`` 里的拼点与**条件分支**（赢 → 手写
# 座位询问选承受者；没赢 → 直接对荀彧造成伤害）。伤害来源 = 拼点目标 A、无渠道牌、无属性。
# ---------------------------------------------------------------------------

QUHU = hero_skill_id("xunyu", "quhu")
"""荀彧【驱虎】的全限定技能 id。"""

QUHU_TITLE = "驱虎"
"""拼点弹窗的标题（发动拼点的技能名）。"""

_QUHU_DONE_KEY = "quhu_done"
_QUHU_STAGE_KEY = "quhu_stage"
_QUHU_TARGET_KEY = "quhu_target"
_QUHU_RECEIVER_KEY = "quhu_receiver"
_QUHU_RECEIVER_ASK = "hero.xunyu.quhu:receiver"


def _quhu_targets(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """拼点目标：**体力值大于你**的其他存活、有手牌的角色（「一名角色」含自己，但
    "体力值大于你"自体不成立，故实际不含自己）。"""
    del frame, chosen
    owner_player = state.player(owner)
    if not owner_player.hand:
        # 拼点必须各出一张**手牌**（规则 12.1 L1317）：自己没牌 ⇒ 整条不可发动。
        return ()
    hp = owner_player.hp
    return tuple(
        player.seat
        for player in state.players
        if player.alive and player.seat != owner and player.hand and player.hp > hp
    )


def _quhu_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    if not _quhu_targets(state, None, ctx.subject, ()):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _quhu_burn(
    host: Any, frame: Any, *, source: int, target: int
) -> None:
    """拼点目标 A 对 ``target`` 造成 1 点伤害（无渠道牌、无属性；来源 = A）。"""
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": None,
            "channel": None,
            "use_timing": frame.data.get("use_timing"),
            "target": target,
            "nature": None,
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )


def _quhu_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：拼点 → 赢则选承受者造成伤害；没赢则 A 对荀彧造成 1 点伤害。"""
    owner = int(frame.data["source"])
    if frame.data.get(_QUHU_DONE_KEY):
        return None
    targets = tuple(int(seat) for seat in answers.get("seat", ()))
    if not targets:
        return None
    stage = frame.data.get(_QUHU_STAGE_KEY, "pindian")
    if stage == "pindian":
        if "pindian_result" not in frame.data:
            host.rank_compare(
                frame,
                initiator=owner,
                targets=targets,
                skill_id=QUHU,
                title=QUHU_TITLE,
            )
            return "stay"
        frame.data[_QUHU_TARGET_KEY] = targets[0]
        won = bool((frame.data.get("pindian_won") or (False,))[0])
        frame.data[_QUHU_STAGE_KEY] = "win" if won else "lose"
        stage = frame.data[_QUHU_STAGE_KEY]
    target = int(frame.data[_QUHU_TARGET_KEY])
    if stage == "lose":
        frame.data[_QUHU_DONE_KEY] = True
        _quhu_burn(host, frame, source=target, target=owner)
        return "stay"
    receiver = frame.data.get(_QUHU_RECEIVER_KEY)
    if receiver is None:
        pool = targets_within_attack_range(host.state, target)
        if not pool:
            # 赢但攻击范围内没有任何其他角色 ⇒ 效果落空（拼点牌照样失去）。
            frame.data[_QUHU_DONE_KEY] = True
            return None
        choice = host.ask_option(
            frame,
            owner,
            Timing.PHASE_PLAY_IDLE,
            skill_id=_QUHU_RECEIVER_ASK,
            options=tuple(f"seat:{seat}" for seat in pool),
        )
        if host.suspended(choice):
            return "stay"
        receiver = int(str(choice.choice).split(":", 1)[1])
        frame.data[_QUHU_RECEIVER_KEY] = receiver
    frame.data[_QUHU_DONE_KEY] = True
    _quhu_burn(host, frame, source=target, target=int(receiver))
    return "stay"


QUHU_FLOW = SkillFlow(
    name="quhu",
    steps=(PickSeats(lo=1, hi=1, pool=_quhu_targets, key="seat"),),
    effect=_quhu_effect,
)
"""【驱虎】的一步流水线：选拼点目标 → 拼点 → 按结果分支（赢选承受者 / 没赢挨打）。"""


# ---------------------------------------------------------------------------
# 张绣【雄乱】（lei-batch 票 02；用户 2026-11-05 签）
#
# 牌面（逐字）：
#
#     雄乱
#     　限定技，出牌阶段，你可以废除你的判定区和装备区并选择一名其他角色，
#       然后本回合：你对其使用牌无距离与次数限制、其不能使用或打出手牌。
#
# 用户 2026-11-05 的裁定（票 03 起补上 2026-11-07 的逐槽粒度）：
#
# - 发动步骤 = 点技能进入**预发动** → 第一步选 1 名其他角色 → 确认才发动；
# - **隐藏发动 cost**：自身至少有一个还能被废除的**槽**（判定 3 + 装备 4 = 7）。7 槽全封 ⇒
#   即使有人恢复限定技的"整局已用"位，也**再不能发动**——故可用性读的是"槽是否已封印"，
#   不是限定位；实现落在座位池上（池空 ⇒ 清单渲不出 ⇒ 没有入口）；
# - 「废除」是**一个事件**（lukang-mobile 票 03 的框架面 `Engine.abolish_slots` /
#   `restore_slots`，状态粒度 = 槽）：槽里牌先强制弃置（按来源区分组搬牌）、再落封印；
# - 「你对其使用牌无距离与次数限制」= **按目标收窄**（只对那名角色）：
#   * 距离侧 = `TARGET_POOL` 修饰器把他补回候选池（豁免距离 / 攻击范围）；
#   * 次数侧 = `COUNT_EXEMPT` 修饰器按 `ModifierContext.use_targets` 判（目标集合 ⊆ {他}
#     才豁免；候选生成期由 `check_count` 的目标维度收窄，不是整张牌放行）；
# - 「其不能使用或打出手牌」复用【义绝】的座位级禁令（本回合，回合末清）。
# ---------------------------------------------------------------------------

XIONGLUAN = hero_skill_id("zhangxiu", "xiongluan")
"""张绣【雄乱】的全限定技能 id。"""

_XIONGLUAN_SLOTS = SEALED_SLOT_IDS
"""雄乱废除的**七个槽**（lukang-mobile 票 03 的逐槽口径）：判定区 3 槽 + 装备区 4 槽。

牌面「废除你的判定区和装备区」仍是"两个区域"，但**一个废除事件**落的是这 7 个槽
（用户 2026-11-07）；`Engine.abolish_slots` 一次调用 = 一个事件、按来源区分组搬牌。
"""


def _xiongluan_available(state: GameState, owner: int) -> bool:
    """隐藏发动 cost：至少一个槽还能被废除（7 槽全封 ⇒ 这张牌再也发不动）。"""
    player = state.player(owner)
    return any(slot not in player.sealed_zones for slot in _XIONGLUAN_SLOTS)


def _xiongluan_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：出牌阶段 + 本人 + 还有可废除的槽 + 有合法目标（限定位由外层闸管）。"""
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    if not _xiongluan_available(state, ctx.subject):
        return current
    if not _other_alive_seats(state, ctx.subject):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _xiongluan_pool(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[int, ...]:
    """目标池：其他存活角色；**区域已全封印 ⇒ 空池**（清单渲不出 ⇒ 没有入口）。"""
    del frame, answers
    if not _xiongluan_available(state, owner):
        return ()
    return _other_alive_seats(state, owner)


def _xiongluan_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：**一个事件**废除 7 个槽（3 判定 + 4 装备；槽里的牌先弃置）→ 两条本回合效果。

    用户 2026-11-07 的逐槽口径：一次调用 = 一个废除事件（`Engine.abolish_slots`），不再
    分两次按"整区"废。幂等：已封印的槽不重落、已空的槽不重搬；弃牌挂起时原样透传。
    """
    state = host.state
    owner = int(frame.data["source"])
    seat = next(iter(int(value) for value in answers.get("seat", ())), None)
    if seat is None:
        return None
    control = host.abolish_slots(frame, owner, _XIONGLUAN_SLOTS)
    if control == "stay":
        return "stay"
    host.set_hand_card_prohibition(seat, True)
    state.player(owner).xiongluan_target = seat
    return None


XIONGLUAN_FLOW = SkillFlow(
    name="xiongluan",
    steps=(PickSeats(lo=1, hi=1, pool=_xiongluan_pool, optional=False, key="seat"),),
    effect=_xiongluan_effect,
)
"""【雄乱】的一步流水线：选一名其他角色（确认即发动）。"""


def _xiongluan_distance_pool(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``TARGET_POOL`` 回调：把我对其使用牌的目标补回池里（距离 / 攻击范围豁免）。

    **只放宽距离，不放宽目标合法性**（lei-batch 票 04 的缺陷 B）：``TARGET_POOL`` 这个手法
    本身能**给自用牌补目标**（将来"可以对别人使用【无中生有】/【桃】"类技能的模板，故不删
    这个能力）；但雄乱的牌面只豁免"距离与次数"，而【无中生有】/【桃】/【酒】/装备牌的
    "只能对自己"（:attr:`~sgs.effects.TargetRule.self_only`）是**目标规则**，不在这两条
    豁免里——不给这道守卫，雄乱生效时这些牌就能指定雄乱目标（用户实机发现）。
    """
    if ctx.card is None or behavior_for(ctx.card).target_rule.self_only:
        return current
    target = state.player(ctx.subject).xiongluan_target
    if target is None or target in current:
        return current
    if not state.player(target).alive:
        return current
    return (*current, target)


def _xiongluan_count_exempt(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``COUNT_EXEMPT`` 回调：**这一次使用**的目标集合 ⊆ {雄乱目标} ⇒ 不受次数上限。

    ``ctx.use_targets`` 是候选生成期**已经定下的目标集合**（``check_count`` 的目标维度，
    lei-batch 票 04）：牌面的"你对其使用牌无次数限制"指到的是那名角色，故

    - ``()`` = **有目标维度但这一次使用没有目标** ⇒ 与"其"无关 ⇒ 照常计次
      （``None`` = 没有目标维度，``check_count`` 根本不派发本挂点）；
    - 集合里出现任何一个别人 ⇒ 照常计次（多目标牌只在"目标集合 ⊆ {其}"时豁免——
      02b §4 读法 A：一次使用同时打别人时那句豁免覆盖不到别人）；
    - ``ctx.owner == ctx.subject`` 是"这条技能属于使用者本人"的自校，与【咆哮】同姿态
      （链遍历全体存活实例，缺这一条会替别人豁免）。

    只在 ``check_count(..., targets=...)`` 时派发，故"整张牌还准不准用"那个问题（候选
    生成的第一次询问）仍读到超额——``_play_phase`` 据此把目标池收窄到被豁免的角色，
    而不是把整张牌放行。
    """
    if ctx.owner != ctx.subject or not ctx.use_targets:
        return current
    target = state.player(ctx.subject).xiongluan_target
    if target is None or not state.player(target).alive:
        return current
    if any(seat != target for seat in ctx.use_targets):
        return current
    return True


_XIONGLUAN = ActiveSkill(
    "zhangxiu",
    "xiongluan",
    _xiongluan_offers,
    flow=XIONGLUAN_FLOW,
    skill_type=SkillType.LIMITED,
    preactivated=True,
    extra_modifiers=(
        Modifier(ModifierPoint.TARGET_POOL, _xiongluan_distance_pool),
        Modifier(ModifierPoint.COUNT_EXEMPT, _xiongluan_count_exempt),
    ),
    requirement=(
        "限定技，出牌阶段，你可以废除你的判定区和装备区并选择一名其他角色，"
        "然后本回合：你对其使用牌无距离与次数限制、其不能使用或打出手牌"
    ),
    step_requirements={"seat": "选择一名其他角色"},
)


# ---------------------------------------------------------------------------
# 界魏延移动版【奇谋】（jie-feng-batch 票 02；口径 = 移动版，**不摸牌**）
#
#     限定技，出牌阶段，你可以失去任意点体力，然后你：本回合计算与其他角色的距离-X，
#     本阶段使用【杀】的次数上限+X（X为你以此法失去的体力值）。
#
# 用户 2026-11-06 签字：X 的选项集 = **1..当前体力**（0 不出现；**当前体力为 0 时不能
# 发动**——「不屈」类技能可能让角色 0 体力存活）；上限 = 当前体力（`X = 当前体力` 允许，
# 真的进濒死，与【苦肉】同口径）；失去体力 ≠ 受到伤害（不触发【狂骨】）。
#
# 选项值直接用中文（X 是动态量，**读不到** `OPTION_LABELS`；选项按钮的 `optionText`
# 回退到选项值本身，零前端改动——与【蛊惑】的「显示名 → 牌型 id」同姿态）。
# ---------------------------------------------------------------------------

QIMOU = hero_skill_id("jie_weiyan_mobile", "qimou")
"""界魏延移动版【奇谋】的全限定技能 id。"""

_QIMOU_LOST = "qimou_lost"
"""帧私有：本次发动的「失去 X 点体力」子帧已经压过（重入只收尾）。"""

_QIMOU_DONE = "qimou_done"
"""帧私有：两段修饰已经落位（重入不重复写座位字段）。"""


def _qimou_choices(hp: int) -> tuple[str, ...]:
    """X 的选项闭集（1..当前体力，中文文本即选项值）。"""
    return tuple(f"失去{amount}点体力" for amount in range(1, int(hp) + 1))


def _qimou_amount(choice: str) -> int:
    """从选项文本读回 X（「失去2点体力」→ 2）。"""
    return int(str(choice).removeprefix("失去").removesuffix("点体力"))


def _qimou_options(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """``PickOption`` 的可调用档：按**当前体力**现算 X 的选项集。"""
    del frame, answers
    return _qimou_choices(state.player(owner).hp)


def _qimou_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：出牌阶段（限定技的整局位由通用闸管）+ **当前体力 ≥ 1**。"""
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    if state.player(ctx.subject).hp <= 0:
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _qimou_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：先失去 X 点体力（压 ``LIFE_DECREASE`` 子帧），出栈后落两段座位修饰。

    行序 = 牌面「失去…，然后你：…」（代价在前）。被自己的代价打死 ⇒ 技能就此结束
    （两段修饰都不落，照抄界【苦肉】的 ``if not alive: return None``）。
    """
    state = host.state
    owner = int(frame.data["source"])
    choice = answers.get("option")
    if not choice:
        return None
    amount = _qimou_amount(str(choice))
    if amount <= 0:
        return None
    if not frame.data.get(_QIMOU_LOST):
        frame.data[_QIMOU_LOST] = True
        host.push_frame(
            FrameKind.LIFE_DECREASE,
            {
                "target": owner,
                "source": None,  # 失去体力：没有伤害来源
                "amount": amount,
            },
            timing=Timing.LIFE_DECREASE_BEFORE,
        )
        return "stay"
    if frame.data.get(_QIMOU_DONE) or not state.player(owner).alive:
        return None
    frame.data[_QIMOU_DONE] = True
    host.set_qimou(owner, amount)
    return None


def _qimou_distance(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """``DISTANCE``：本回合**你计算与其他角色的距离 -X**（主体侧，与【马术】同侧）。"""
    if ctx.owner != ctx.subject:
        return current
    amount = state.player(ctx.owner).qimou_distance_x
    if not amount:
        return current
    return current - amount


def _qimou_extra_slash(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """``COUNT_LIMIT``：本阶段使用【杀】的次数上限 +X（无次数限制者保持无限）。"""
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    amount = state.player(ctx.owner).qimou_count_x
    if not amount:
        return current
    if current is None:
        return None
    return int(current) + amount


QIMOU_FLOW = SkillFlow(
    name="qimou",
    steps=(PickOption(options=_qimou_options, optional=False, key="option"),),
    effect=_qimou_effect,
    # 限定技的"一次"是**整局**，不落 `_USED` 阶段位（整局位由提交点落）。
    once_per_phase=False,
)
"""【奇谋】单步流水线：选 X（中文选项）→ 失去体力 → 落两段修饰。"""

_QIMOU = ActiveSkill(
    "jie_weiyan_mobile",
    "qimou",
    _qimou_offers,
    flow=QIMOU_FLOW,
    skill_type=SkillType.LIMITED,
    requirement=(
        "限定技，出牌阶段，你可以失去任意点体力，然后你：本回合计算与其他角色的距离-X，"
        "本阶段使用【杀】的次数上限+X（X为你以此法失去的体力值）。"
    ),
    step_requirements={"option": "选择失去的体力值 X（1..当前体力）"},
    extra_modifiers=(
        Modifier(ModifierPoint.DISTANCE, _qimou_distance),
        Modifier(ModifierPoint.COUNT_LIMIT, _qimou_extra_slash),
    ),
)


# ---------------------------------------------------------------------------
# 神甘宁【魄袭】（lei-shenganning 票 01；用户 2026-11-07 逐条签）
#
# 牌面（逐字；OL 现行文本 = 用户 `技能.txt`）：
#
#     魄袭
#     　出牌阶段限一次，你可以观看一名其他角色的手牌，且你可以弃置你与其手牌中共计四张
#     不同花色的牌，然后若你以此法被弃置的牌数为：
#     　【0】你减少1点体力上限；
#     　【1】你结束出牌阶段且你本回合的手牌上限-1；
#     　【3】你回复1点体力；
#     　【4】你摸四张牌。
#
# 用户 2026-11-07 的两条裁定（`.scratch/lei-shenganning/issues/90-poxi-gaps.md`）：
#
# - **1 档的「你结束出牌阶段」= 玩家点「结束出牌阶段」的同一效果**（框架票 92 的受管写入口
#   `Engine.end_play_phase`，**不是** `skip_play_phase`）。
# - **选牌面 = 两行观看面**（框架票 93）：上行 = 目标手牌、下行 = 自己的手牌，四色各一张才
#   可提交；**超时 = 放弃**（`tools/sgs_web/timeout.py` 的 `Pass` 优先天然给出）⇒ 不弃置、
#   **不结算任何分档**。
#
# 形状 = **手写主体**（`shapes.md` §2）：`PickSeats` 装前一步、段机住在 `effect` 里
# （观看 + 四色选牌 → 两次搬牌 → 按「你的张数」分档）。样板 = `hero.shen_lvmeng_mobile.gongxin`
# （观看 / `revealed` / `ask_when_empty`）+ `hero.haozhao.zhengku`（提交点分支 + 帧内游标）。
#
# 与【攻心】的**两处刻意差别**（票面 §1 的询问剧本）：
#
# 1. 候选面是**两人手牌的并集**（`Slot` 的 `ref.seat` 分两个座位），不是目标一人的红桃；
# 2. 凑不出四色时仍发一条**空选择面**（`ask_when_empty=True`）：弹窗里看得见目标的整副手牌，
#    出口只有「放弃」——四色的判定在**候选生成期**（`SelectionSpec.max_per_suit`），故这里
#    自己判一次"拿不拿得出四色"，拿不出就铺空面（否则面里全是选不齐的牌，玩家会一直点到超时）。
#
# 弃置张数由「**你的**张数」定（0 / 1 / 2 / 3 / 4，其中 **2 无效果**——三平台牌面都没有【2】）：
# 牌面**没有**"一张 / 1 点"这类限定词 ⇒ 一次发动按**事件**结算一次（默认读法（三）），
# 不是逐张触发四次。
# ---------------------------------------------------------------------------

POXI_CARDS = 4
"""「共计四张不同花色的牌」= **恰好 4 张、四色各一**（票面 §1 的读法表）。"""

_ASK_POXI = "hero.shen_ganning.poxi.cards"
"""手写询问位置：【魄袭】四色选牌那一问（``owner`` 必填，见 ``Engine.ask_selection``）。"""

_POXI_PLAN = "poxi_plan"
"""帧私有：发牌前的两份手牌快照 + "四色凑得出吗"（重入不重算，也不重新判空面）。"""

_POXI_MINE_MOVED = "poxi_mine_moved"
"""帧私有：我自己那几张已经进弃牌堆（重入不重搬）。"""

_POXI_THEIR_MOVED = "poxi_theirs_moved"
"""帧私有：目标那几张已经进弃牌堆（重搬会触发两次"失去牌后"）。"""

_POXI_SETTLED = "poxi_settled"
"""帧私有：分档效果已经落了（重入不重结算）。"""


def _poxi_targets(state: GameState, owner: int) -> tuple[int, ...]:
    """可以观看的对象：**其他存活且手牌非空**的角色（观看的前置条件，与【攻心】同形）。"""
    return tuple(
        player.seat
        for player in state.players
        if player.alive and player.seat != owner and player.hand
    )


def _poxi_pool(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    del frame, chosen
    return _poxi_targets(state, owner)


def _poxi_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：本阶段没用过 ∧ 有可观看的其他角色（池空 ⇒ 连"发动"都不出现）。"""
    if not _can_offer(state, ctx):
        return current
    if not _poxi_targets(state, ctx.subject):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _poxi_plan(
    state: GameState, owner: int, target: int
) -> tuple[tuple[int, ...], tuple[int, ...], bool]:
    """发牌前的快照：``(我的手牌, 目标手牌, 四色凑得出吗)``。

    "凑得出四色"= 两副手牌里**四种花色都有**且合计至少 4 张——满足它时，候选枚举
    （`total=(4,4)` + `max_per_suit=1`）必然产得出至少一种四色各一的组合；不满足时整面
    只会有 `Pass`，故按空面铺（见 :func:`_poxi_effect`）。
    """
    mine = tuple(state.player(owner).hand)
    theirs = tuple(state.player(target).hand)
    suits = {state.card(card_id).suit for card_id in (*theirs, *mine)}
    return mine, theirs, len(suits) == len(Suit) and len(mine) + len(theirs) >= POXI_CARDS


def _poxi_face(
    state: GameState, owner: int, target: int, mine: tuple[int, ...], theirs: tuple[int, ...]
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """选择面 = **两副手牌的并集**：上行目标（在前）、下行自己（在后），行序由 `ref.seat` 分。"""
    slots = tuple(
        Slot(ref=ZoneRef(Zone.HAND, seat), card=state.card(card_id))
        for seat, card_id in (
            *((target, card_id) for card_id in theirs),
            *((owner, card_id) for card_id in mine),
        )
    )
    return slots, tuple(slot.card.card_id for slot in slots)


def _poxi_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：观看 + 四色选牌 → 两次搬牌（我方 / 对方各一次）→ 按"你的张数"分档。

    四张来自两副手牌，而一次 ``move_card`` 只认**一个来源区**（`effects/primitives.py` 的
    "一次搬牌只能有一个来源区域"）⇒ 必须拆成两次搬牌。每次搬牌都会内联派发"失去牌后"
    （【连营】/【枭姬】）而挂起，故重入纪律按 **G7（挂起续跑）** 落：两个段落各一个帧私有
    游标（在搬牌**之前**落），分档效果另有一个收尾游标。

    「可以」的出口 = 候选里的 `Pass`（超时兜底同义）：**不弃置、不结算任何分档**。
    """
    state = host.state
    owner = int(frame.data["source"])
    target = int(answers["seat"][0])
    plan = frame.data.get(_POXI_PLAN)
    if plan is None:
        plan = _poxi_plan(state, owner, target)
        frame.data[_POXI_PLAN] = plan
    mine, theirs, four_suits = plan
    if four_suits:
        slots, card_ids = _poxi_face(state, owner, target, mine, theirs)
        revealed = state.refs((*theirs, *mine))
    else:
        # 凑不出四色：**空选择面**（只有「放弃」），但整副被观看的手牌仍授予观看者
        # （票 93 的空面约定：`revealed` 只含**上行**那一手）。
        slots, card_ids = (), ()
        revealed = state.refs(theirs)
    picked = host.ask_selection(
        frame,
        owner=_ASK_POXI,
        key=f"poxi:{target}",
        actor=owner,
        timing=Timing.PHASE_PLAY_IDLE,
        spec=SelectionSpec(
            zones=(Zone.HAND,),
            total=(POXI_CARDS, POXI_CARDS),
            optional=True,
            max_per_suit=1,
        ),
        face=(slots, card_ids),
        revealed=revealed,
        ask_when_empty=True,
    )
    if host.suspended(picked):
        return "stay"
    chosen = tuple(int(card_id) for card_id in picked)
    if not chosen:
        # 「可以」⇒ 放弃（含空面 / 超时）：一次弃置都不发生，**分档效果一个都不结算**。
        return None
    own = set(mine)
    my_picks = tuple(card_id for card_id in chosen if card_id in own)
    their_picks = tuple(card_id for card_id in chosen if card_id not in own)
    # 游标在搬牌**之前**落：`move_in_frame` 是"先搬完、再内联派发失去牌后"，钩子挂起时
    # 这一次搬牌已经发生，续跑不得再来一遍。
    if my_picks and not frame.data.get(_POXI_MINE_MOVED):
        frame.data[_POXI_MINE_MOVED] = True
        control = host.move_in_frame(
            frame,
            state.refs(my_picks),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    if their_picks and not frame.data.get(_POXI_THEIR_MOVED):
        frame.data[_POXI_THEIR_MOVED] = True
        control = host.move_in_frame(
            frame,
            state.refs(their_picks),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=target,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    if frame.data.get(_POXI_SETTLED):
        return None
    frame.data[_POXI_SETTLED] = True
    return _poxi_tier(host, frame, owner, len(my_picks))


def _poxi_tier(host: Any, frame: Any, owner: int, count: int) -> Any:
    """「你以此法被弃置的牌数」→ 分档效果（**2 档无效果**：牌面没有【2】，什么都不做）。

    游标在**效果之前**落（`_POXI_SETTLED`）：3 档压子帧、4 档摸牌都可能挂起，重入时不得
    再结算一次。
    """
    state = host.state
    if count == 0:
        # 上限**照减**（用户 2026-11-07：不兜底）；上限已经是 1 时减到 0 ⇒ 直接死亡
        # （票 04 的 0 档：体力置 0、不进濒死）。DEATH 帧已压栈 ⇒ 本效果到此结束，
        # 重入由 `_POXI_SETTLED` 收尾。
        host.reduce_hp_max(frame, owner, 1)
        if not state.player(owner).alive:
            return "stay"
        return None
    if count == 1:
        host.add_hand_limit_bonus(owner, -1)
        host.end_play_phase(owner)
        return None
    if count == 3:
        _push_recover(host, frame, source=owner, target=owner)
        return None
    if count == POXI_CARDS:
        if host.draw_for_hand(frame, owner, POXI_CARDS) == "stay":
            return "stay"
    # 2 档：只有弃置发生、没有后续（牌面三平台都没有【2】）。
    return None


POXI_FLOW = SkillFlow(
    name="poxi",
    steps=(PickSeats(lo=1, hi=1, pool=_poxi_pool, optional=False, key="seat"),),
    effect=_poxi_effect,
)
"""【魄袭】的一步流水线 + 手写主体：选一名其他角色 → 观看 + 四色选牌 → 分档结算。

**不翻预发动**（`shapes.md` §4 的准入表）：首步读的是**别人的手牌**（隐藏面），清单渲不出。
"""

_POXI = ActiveSkill(
    "shen_ganning",
    "poxi",
    _poxi_offers,
    flow=POXI_FLOW,
    requirement=(
        "出牌阶段限一次，你可以观看一名其他角色的手牌，且你可以弃置你与其手牌中共计四张"
        "不同花色的牌，然后若你以此法被弃置的牌数为：0，你减少1点体力上限；1，你结束"
        "出牌阶段且你本回合的手牌上限-1；3，你回复1点体力；4，你摸四张牌。"
    ),
    step_requirements={"seat": "观看一名其他角色的手牌"},
)


# ---------------------------------------------------------------------------
# 自制包·锋·于禁【节钺】（feng-yujin 票 01）：出牌阶段内限一次，令一名攻击范围内的其它角色
# 交给你一张牌 → 二选一：
#   1. 选择至多 2 张牌标记为【定乱】牌后展示之，然后将这些牌交给该角色
#   2. 弃置至多 2 张牌，对其造成 1 点伤害
#
# 读法（用户 2026-11-07 签「全部按建议」，逐条见 `.scratch/feng-yujin/issues/05-...`）：
# - **R8=A**：「交给你一张牌」由**交出者**挑 ⇒ `PickCards(actor=目标, face=目标)`；
# - **R9**：两支都 **1..2**（"至多"的下限从 1 起，`06`（§5「程序化约定与偏离一手」）的「『任意数量』在候选里不包含 0」行）；
# - **R10**：正常两项必选、**无 `Pass`**；两项都不可执行（自身没有手牌也没有装备——
#   极小概率，如刚收到的牌被立刻拿走）时给一条**兜底 `Pass`**，避免卡死。故选项是**手写**
#   的（`PickOption.optional` 是静态 bool，表达不了"只有这一档才给 Pass"）。
# - 选项 1 的「标记为【定乱】牌」与【定乱】共用同一个标签写入点
#   （`hero_yujin.mark_dingluan_cards`）；标签的禁令 / 寿命 / 自身效果见
#   `hero.feng_yujin.dingluan_mark`（`CarrierKind.CARD_TAG`）。
# ---------------------------------------------------------------------------

JIEYUE = hero_skill_id("feng_yujin", "jieyue")
"""锋·于禁【节钺】的全限定技能 id（牌面见本段注释）。"""

_JIEYUE_MARK = "jieyue_mark"
"""选项 1 的 id（`options.js:OPTION_LABELS` 有同名中文键）。"""

_JIEYUE_DAMAGE = "jieyue_damage"
"""选项 2 的 id（同上）。"""

_JIEYUE_OPTIONS: tuple[str, ...] = (_JIEYUE_MARK, _JIEYUE_DAMAGE)
"""选项闭集（顺序即询问里的顺序：1 → 2）。"""

_JIEYUE_CHOICE_STEP = "jieyue:choice"
_ASK_JIEYUE_MARK = "jieyue:mark"
_ASK_JIEYUE_DISCARD = "jieyue:discard"
"""三处手写询问的标识：选项步（`choice`）/ 选项 1 选牌 / 选项 2 选牌。

选项步的标识决定 `step_requirements` 的键（冒号后那段 = ``choice``）；两个选牌步的键恒读
``card``（投影对 ``SELECT_CARDS`` 的既有约定）。"""

_JIEYUE_GIVEN = "jieyue_given"
"""帧私有：目标交出的那张牌已搬到发动者手里（续跑不重搬）。"""

_JIEYUE_CHOICE = "jieyue_choice"
"""帧私有：二选一选中的那一项（空串 = 兜底 `Pass` 收尾；重入不重问）。"""

_JIEYUE_MARKED = "jieyue_marked"
"""帧私有：选项 1 选中的牌（实体 id 元组）。"""

_JIEYUE_TAGGED = "jieyue_tagged"
"""帧私有：这批牌已经标记 + 展示过。"""

_JIEYUE_GIFTED = "jieyue_gifted"
"""帧私有：这批牌已经交给目标。"""

_JIEYUE_DISCARD_PICKED = "jieyue_discard_picked"
"""帧私有：选项 2 选中的牌（实体 id 元组）。"""

_JIEYUE_DISCARDED = "jieyue_discarded"
"""帧私有：这批牌已经弃置。"""

_JIEYUE_DAMAGED = "jieyue_damaged"
"""帧私有：1 点伤害的 DAMAGE 子帧已经压过。"""


def _jieyue_own_cards(state: GameState, seat: int) -> int:
    """``seat`` 的**手牌 + 装备区**张数（两支的代价池；R9 的 1..2 都从这里取上限）。"""
    player = state.player(seat)
    return len(player.hand) + len(player.equip)


def _jieyue_targets(
    state: GameState, frame: Any, owner: int, answers: Any
) -> tuple[int, ...]:
    """「攻击范围内的其它角色」且**至少有 1 张牌**（交不出来的不算目标）。"""
    del frame, answers
    return tuple(
        seat
        for seat in targets_within_attack_range(state, owner)
        if _jieyue_own_cards(state, seat) > 0
    )


def _jieyue_giver(state: GameState, frame: Any, owner: int, answers: Any) -> int:
    """第 2 步的应答者 / 选择面 = 第 1 步选中的那名角色（R8=A：由他挑一张交出）。"""
    del state, frame, owner
    return int(answers["seat"][0])


def _jieyue_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选恒一条骨架（出牌阶段限一次；攻击范围内没有"有牌的其他角色"⇒ 不产出）。"""
    if not _can_offer(state, ctx):
        return current
    if not _jieyue_targets(state, None, ctx.subject, ()):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _jieyue_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：目标交出一张 → 手写二选一 → 分支主体。

    两段（交牌 / 选项）各有帧私有游标：搬牌可能内联派发「失去牌后」而挂起，重入时既不
    重搬、也不重问；选项的答案同样先落游标再执行分支。
    """
    state = host.state
    owner = int(frame.data["source"])
    target = _jieyue_giver(state, frame, owner, answers)
    timing = Timing.PHASE_PLAY_IDLE

    # ---- 段 1：目标把那张牌交给你（PUT 进你的手牌、归属 = 你）-------------
    if not frame.data.get(_JIEYUE_GIVEN):
        given = tuple(int(card) for card in answers.get("give", ()))
        if not given:
            return None
        frame.data[_JIEYUE_GIVEN] = True
        control = host.move_in_frame(
            frame,
            state.refs(given),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=target,
        )
        if control == "stay":
            return "stay"

    # ---- 段 2：二选一（R10：正常无 Pass；两项都不可执行才给兜底 Pass）-----
    if _JIEYUE_CHOICE not in frame.data:
        options = _JIEYUE_OPTIONS if _jieyue_own_cards(state, owner) > 0 else ()
        choice = host.ask_option(
            frame,
            owner,
            timing,
            skill_id=_JIEYUE_CHOICE_STEP,
            options=options,
            optional=not options,
        )
        if host.suspended(choice):
            return "stay"
        frame.data[_JIEYUE_CHOICE] = "" if isinstance(choice, Pass) else str(choice.choice)
    chosen = str(frame.data[_JIEYUE_CHOICE])
    if not chosen:
        # 兜底 ``Pass``：自身没有牌 ⇒ 两支都不可执行，零副作用收尾（不卡死）。
        return None
    if chosen == _JIEYUE_MARK:
        return _jieyue_mark_branch(host, frame, owner, target)
    return _jieyue_damage_branch(host, frame, owner, target)


def _jieyue_mark_branch(host: Any, frame: Any, owner: int, target: int) -> Any:
    """选项 1：选至多 2 张**自己的**牌（1..2）→ 标记【定乱】→ 展示 → 交给目标（R9 / R8）。"""
    state = host.state
    timing = Timing.PHASE_PLAY_IDLE
    if _JIEYUE_MARKED not in frame.data:
        pool = _jieyue_own_cards(state, owner)
        if pool <= 0:
            return None
        picked = host.ask_selection(
            frame,
            owner=_ASK_JIEYUE_MARK,
            key="card",
            actor=owner,
            timing=timing,
            spec=SelectionSpec(
                zones=(Zone.HAND, Zone.EQUIP),
                total=(1, min(2, pool)),
                optional=False,
            ),
            build_face=lambda: host.selection_face(
                owner, (Zone.HAND, Zone.EQUIP), reveal_hand=True
            ),
        )
        if host.suspended(picked):
            return "stay"
        frame.data[_JIEYUE_MARKED] = tuple(int(card) for card in picked)
    marked = tuple(int(card) for card in frame.data[_JIEYUE_MARKED])
    if not marked:
        return None
    if not frame.data.get(_JIEYUE_TAGGED):
        # 牌面次序：先标记、再展示（展示 = `card_reveal`，不移动牌；手牌与装备牌都展示）。
        from .hero_yujin import mark_dingluan_cards

        mark_dingluan_cards(host, owner, marked)
        for card_id in marked:
            host.reveal_card(frame, owner, state.card(card_id))
        frame.data[_JIEYUE_TAGGED] = True
    if not frame.data.get(_JIEYUE_GIFTED):
        frame.data[_JIEYUE_GIFTED] = True
        control = _move_by_source(
            host,
            frame,
            marked,
            to_zone=ZoneRef(Zone.HAND, target),
            owner=target,
            key="jieyue:gift",
            reason=MoveReason.PUT,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    return None


def _jieyue_damage_branch(host: Any, frame: Any, owner: int, target: int) -> Any:
    """选项 2：弃置至多 2 张**自己的**牌（1..2）→ 对该角色造成 1 点伤害（R9）。"""
    state = host.state
    timing = Timing.PHASE_PLAY_IDLE
    if _JIEYUE_DISCARD_PICKED not in frame.data:
        pool = _jieyue_own_cards(state, owner)
        if pool <= 0:
            return None
        picked = host.ask_selection(
            frame,
            owner=_ASK_JIEYUE_DISCARD,
            key="card",
            actor=owner,
            timing=timing,
            spec=SelectionSpec(
                zones=(Zone.HAND, Zone.EQUIP),
                total=(1, min(2, pool)),
                optional=False,
            ),
            build_face=lambda: host.selection_face(
                owner, (Zone.HAND, Zone.EQUIP), reveal_hand=True
            ),
        )
        if host.suspended(picked):
            return "stay"
        frame.data[_JIEYUE_DISCARD_PICKED] = tuple(int(card) for card in picked)
    picked = tuple(int(card) for card in frame.data[_JIEYUE_DISCARD_PICKED])
    if not picked:
        return None
    if not frame.data.get(_JIEYUE_DISCARDED):
        frame.data[_JIEYUE_DISCARDED] = True
        control = _move_to_discard(
            host, frame, picked, owner=owner, key="jieyue:discard"
        )
        if control == "stay":
            return "stay"
    if not frame.data.get(_JIEYUE_DAMAGED):
        frame.data[_JIEYUE_DAMAGED] = True
        _push_damage(host, frame, source=owner, target=target, amount=1)
        return "stay"
    return None


JIEYUE_FLOW = SkillFlow(
    name="jieyue",
    steps=(
        PickSeats(lo=1, hi=1, pool=_jieyue_targets, key="seat"),
        # R8=A：应答者 / 选择面 = 目标本人（自己的手牌 + 装备区，可见可选）。
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            key="give",
            actor=_jieyue_giver,
            face=_jieyue_giver,
            reveal_hand=True,
        ),
    ),
    effect=_jieyue_effect,
)
"""【节钺】的两步流水线 + 手写主体：选角色 → 目标交出一张 → 手写二选一（分支住 effect）。"""

_JIEYUE = ActiveSkill(
    "feng_yujin",
    "jieyue",
    _jieyue_offers,
    flow=JIEYUE_FLOW,
    requirement=(
        "出牌阶段内限一次，你可以令一名攻击范围内的其它角色交给你一张牌，然后你选择："
        "1.选择至多2张牌标记为【定乱】牌后展示之，然后将这些牌交给该角色 "
        "2.弃置至多2张牌，对其造成1点伤害"
    ),
    step_requirements={
        "seat": "选择你攻击范围内的一名其他角色",
        # SELECT_CARDS 的逐步文案键恒读 "card"：三个选牌步共用这一句（投影的既有约定）。
        "card": "选择你自己的至多两张牌（手牌 / 装备区）",
        "choice": "选择一项：标记并交给该角色 / 弃置并造成伤害",
    },
)
"""锋·于禁【节钺】的声明（A 族；不翻预发动——首步是选座位，直提态 + 逐步问就够）。"""


# ---------------------------------------------------------------------------
# 陆抗移动版【决堰】/ ☆【怀柔】（lukang-mobile 票 01；用户 2026-11-07 逐条签）
#
# 牌面（逐字；`D:\SGS_HEROS\神话再临-雷包\陆抗\技能.txt`，移动版）：
#
#     决堰
#     　出牌阶段限一次，你可以废除你装备区里的一种装备栏，然后执行对应的选项：
#       【武器栏】，此阶段内你使用【杀】的次数上限+3；
#       【防具栏】，摸三张牌且本回合的手牌上限+3；
#       【两个坐骑栏】，本回合你使用牌无距离限制。
#
#     ☆ 怀柔
#     　出牌阶段，你可以重铸装备牌。
#
# 用户 2026-11-07 签字的四条口径：
#
# - 【决堰】的「使用牌无距离限制」= **含【杀】**（攻击范围照免）+ **含【锦囊】**（【顺手
#   牵羊】/【兵粮寸断】的额定距离）——实现 = `TARGET_POOL`（只对【杀】族并入全体其他存活
#   座位，与【诈降】/【天义】同一手法）+ `DISTANCE_LIMIT`（有额定距离的牌返回 ``None``）；
# - 废除 = **一个事件**废除该栏的槽（武器 1 / 防具 1 / 两个坐骑 2），走票 03 的
#   `Engine.abolish_slots`；
# - 「可以」= 整条发动可选；发动之后**必须**选一项（选项步无 `Pass`）；四个槽全封 ⇒ 连入口
#   都不出现（补充一手 Q5：所有装备栏都被废除后不能发动【决堰】）；
# - 【怀柔】= **点技能进预发动 → 点自己的手牌 / 装备区 1 张装备牌 → 提交即重铸**（用户原话），
#   重铸语义复用引擎的 `Reforge` / `MOVE` 帧，不另写"弃置 + 摸一张"。
# ---------------------------------------------------------------------------

DUANYAN = hero_skill_id("lukang_mobile", "duanyan")
"""陆抗移动版【决堰】的全限定技能 id。"""

DUANYAN_WEAPON = "duanyan_weapon"
DUANYAN_ARMOR = "duanyan_armor"
DUANYAN_HORSES = "duanyan_horses"
"""【决堰】三种装备栏的**选项 id**（中文标签在 `options.js:OPTION_LABELS`）。"""

_DUANYAN_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (DUANYAN_WEAPON, (SlotKind.WEAPON.value,)),
    (DUANYAN_ARMOR, (SlotKind.ARMOR.value,)),
    (DUANYAN_HORSES, (SlotKind.HORSE_OFFENSE.value, SlotKind.HORSE_DEFENSE.value)),
)
"""选项 → 该栏的槽位（【两个坐骑栏】= 两个槽；用户 2026-11-07「1 个或 2 个装备槽」）。"""

DUANYAN_SLASH_BONUS = 3
"""【武器栏】的次数上限增量（「+3」）。"""

DUANYAN_DRAW = 3
"""【防具栏】的摸牌数（「摸三张牌」）。"""

DUANYAN_HAND_LIMIT = 3
"""【防具栏】的本回合手牌上限加式（「本回合的手牌上限+3」）。"""

_DUANYAN_DRAWN = "duanyan_drawn"
"""帧私有：摸三张已经做过（重入不重摸）。"""

_DUANYAN_DONE = "duanyan_done"
"""帧私有：效果段已经落位（重入不重复写座位字段）。"""


def _duanyan_columns(state: GameState, owner: int) -> tuple[str, ...]:
    """此刻还能废除的装备栏（每个栏至少有一个槽**没被封印**）。

    已经封满的栏不再出现在选项里（"这个名字此刻不存在"用 `options` 的可调用档收窄，
    不是置灰）；四个槽全封 ⇒ 空元组 ⇒ 技能按钮不出现（补充一手 Q5）。
    """
    sealed = set(state.player(owner).sealed_zones)
    return tuple(
        column
        for column, slots in _DUANYAN_COLUMNS
        if any(slot not in sealed for slot in slots)
    )


def _duanyan_options(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """``PickOption`` 的可调用档：按此刻的封印状态现算三个候选栏。"""
    del frame, answers
    return _duanyan_columns(state, owner)


def _duanyan_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：出牌阶段限一次 + 至少一个装备栏还能废除。"""
    if not _can_offer(state, ctx):
        return current
    if not _duanyan_columns(state, ctx.subject):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _duanyan_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：**先废除该栏**（一个事件；槽里牌先弃置）→ 再执行对应效果。

    行序 = 牌面「废除…，然后执行对应的选项」（代价在前）。废除走
    :meth:`~sgs.engine.Engine.abolish_slots`（票 03 的逐槽写入口）：已封印的槽是幂等的，
    故重入时再调一次是零动作。挂起游标：摸三张一次（``_DUANYAN_DRAWN``）、效果段一次
    （``_DUANYAN_DONE``）。
    """
    state = host.state
    owner = int(frame.data["source"])
    column = str(answers.get("column") or "")
    slots = dict(_DUANYAN_COLUMNS).get(column)
    if not slots:
        return None
    control = host.abolish_slots(frame, owner, slots)
    if control == "stay":
        return "stay"
    if frame.data.get(_DUANYAN_DONE):
        return None
    if column == DUANYAN_WEAPON:
        host.set_duanyan_slash_bonus(owner, DUANYAN_SLASH_BONUS)
    elif column == DUANYAN_ARMOR:
        if not frame.data.get(_DUANYAN_DRAWN):
            frame.data[_DUANYAN_DRAWN] = True
            control = host.draw_for_hand(frame, owner, DUANYAN_DRAW)
            if not control:
                return None  # 短摸即平局（G10）
            if control == "stay":
                return "stay"
        host.add_hand_limit_bonus(owner, DUANYAN_HAND_LIMIT)
    else:  # 两个坐骑栏
        host.set_duanyan_no_distance(owner, True)
    frame.data[_DUANYAN_DONE] = True
    return None


DUANYAN_FLOW = SkillFlow(
    name="duanyan",
    steps=(PickOption(options=_duanyan_options, optional=False, key="column"),),
    effect=_duanyan_effect,
)
"""【决堰】的单步流水线：选一个装备栏（1～3 项、恒非空、无 `Pass`）→ 废除 + 对应效果。"""


def _duanyan_slash_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``COUNT_LIMIT``：【武器栏】「此阶段内你使用【杀】的次数上限+3」。"""
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    amount = state.player(ctx.owner).duanyan_slash_bonus
    if not amount:
        return current
    if current is None:
        return None
    return int(current) + amount


def _duanyan_no_distance_pool(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``TARGET_POOL``：【两个坐骑栏】「使用牌无距离限制」的**【杀】**那一轴。

    与【诈降】/【天义】同一手法（并入全体其他存活座位 = 不查攻击范围）。只对【杀】族生效：
    其它牌的"距离"走 ``DISTANCE_LIMIT``（见下一条）——并入会**放宽目标合法性**（例：
    【顺手牵羊】的"区域里有牌"），那是另一回事。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not state.player(ctx.owner).duanyan_no_distance:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    others = tuple(
        player.seat for player in state.players if player.alive and player.seat != ctx.subject
    )
    return tuple(dict.fromkeys((*current, *others)))


def _duanyan_no_distance_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``DISTANCE_LIMIT``：【两个坐骑栏】的**额定距离**豁免（【顺手牵羊】/【兵粮寸断】等）。

    只在 ``max_distance is not None`` 的牌上派发（见 :func:`check_targets`），故"无距离
    限制"在这里读作"不查距离"（返回 ``None``），与【奇才】同一姿态。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not state.player(ctx.owner).duanyan_no_distance:
        return current
    return None


_DUANYAN = ActiveSkill(
    "lukang_mobile",
    "duanyan",
    _duanyan_offers,
    flow=DUANYAN_FLOW,
    requirement=(
        "出牌阶段限一次，你可以废除你装备区里的一种装备栏，然后执行对应的选项："
        "【武器栏】，此阶段内你使用【杀】的次数上限+3；【防具栏】，摸三张牌且本回合的"
        "手牌上限+3；【两个坐骑栏】，本回合你使用牌无距离限制"
    ),
    step_requirements={"column": "废除你装备区里的一种装备栏（然后执行对应的选项）"},
    extra_modifiers=(
        Modifier(ModifierPoint.COUNT_LIMIT, _duanyan_slash_limit),
        Modifier(ModifierPoint.TARGET_POOL, _duanyan_no_distance_pool),
        Modifier(ModifierPoint.DISTANCE_LIMIT, _duanyan_no_distance_limit),
    ),
)


HUAJROU = hero_skill_id("lukang_mobile", "huairou")
"""陆抗移动版 ☆【怀柔】的全限定技能 id（由【破势】觉醒后获得）。"""

_HUAJROU_PUSHED = "huairou_pushed"
"""帧私有：重铸的 ``MOVE`` 子帧已经压过（重入不再压第二次）。"""

_EQUIP_SLOT_KINDS: tuple[SlotKind, ...] = (
    SlotKind.WEAPON,
    SlotKind.ARMOR,
    SlotKind.HORSE_OFFENSE,
    SlotKind.HORSE_DEFENSE,
)
"""「装备牌」的判据 = 牌面带装备槽位（宝物槽不启用，ADR-006）。"""


def _huairou_equipment_cards(state: GameState, owner: int) -> tuple[int, ...]:
    """自己**手牌 + 装备区**里能重铸的装备牌。

    账本 L775 的「装备牌」读法（手牌 + 装备区里的装备牌）；补充一手 Q7 明说**已装备的**
    也能重铸，故装备区那一路是本技能的必需面。
    """
    player = state.player(owner)
    return tuple(
        card_id
        for card_id in (*player.hand, *player.equip)
        if state.card(card_id).slot is not None
    )


def _huairou_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """候选：出牌阶段（**不限次**）+ 至少有一张装备牌可重铸。"""
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    if not _huairou_equipment_cards(state, ctx.subject):
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _huairou_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：把选中的那张装备牌**重铸**（压 ``Reforge`` 的 ``MOVE`` 帧：弃置 + 摸一张）。

    重铸不是使用 ⇒ 复用引擎的 :meth:`~sgs.engine.Engine.start_reforge`（同一个 ``MOVE``
    帧两步：``reforge_lose_card`` → ``reforge_draw``），不另写"弃置 + 摸一张"。
    """
    if frame.data.get(_HUAJROU_PUSHED):
        return None
    picks = tuple(answers.get("card") or ())
    if not picks:
        return None
    card = host.state.card(int(picks[0]))
    if card.slot is None:
        return None
    frame.data[_HUAJROU_PUSHED] = True
    # ``Reforge`` 的 ``answers_seq`` 只进候选的稳定键（这里压的是 MOVE 帧、不吃候选键），
    # 故取 0：重铸不是"使用"，也没有第二个应答面。
    host.start_reforge(Reforge(card=card, answers_seq=0), source=int(frame.data["source"]))
    return "stay"


HUAJROU_FLOW = SkillFlow(
    name="huairou",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            optional=False,
            key="card",
            slot_kinds=_EQUIP_SLOT_KINDS,
        ),
    ),
    effect=_huairou_effect,
    once_per_phase=False,
)
"""【怀柔】的单步流水线：选 1 张自己的装备牌（手牌 / 装备区）→ 重铸。"""

_HUAJROU = ActiveSkill(
    "lukang_mobile",
    "huairou",
    _huairou_offers,
    flow=HUAJROU_FLOW,
    preactivated=True,
    requirement="出牌阶段，你可以重铸装备牌",
    step_requirements={"card": "重铸一张装备牌（手牌 / 装备区）"},
)


ACTIVE_SKILLS: tuple[ActiveSkill, ...] = (
    # 荀彧【驱虎】（火包·移动版）：与体力值大于你的角色拼点 → 赢选承受者 / 没赢挨 1 点。
    ActiveSkill(
        "xunyu",
        "quhu",
        _quhu_offers,
        flow=QUHU_FLOW,
        requirement=(
            "与一名体力值大于你的角色拼点：若你赢，该角色对其攻击范围内你选择的一名角色"
            "（不能是该角色本身，可以是你）造成 1 点伤害；否则其对你造成 1 点伤害"
        ),
        step_requirements={
            "seat": "选择一名体力值大于你、且有手牌的角色拼点",
            "receiver": "选择该角色攻击范围内的一名角色承受 1 点伤害（不能是该角色本身）",
        },
    ),
    # 太史慈【天义】（火包·移动版）：与一名有手牌的其他角色拼点 → 本阶段三条修饰器（赢）
    # 或不能使用【杀】（没赢）。拼点帧由 03 票解封（`Engine.rank_compare`）。
    ActiveSkill(
        "taishici",
        "tianyi",
        _tianyi_offers,
        flow=TIANYI_FLOW,
        extra_modifiers=TIANYI_MODIFIERS,
        requirement=(
            "与一名有手牌的其他角色拼点：若你赢，你本阶段使用【杀】的次数上限+1、"
            "无距离限制、目标数上限+1；否则你本阶段不能使用【杀】"
        ),
        step_requirements={"seat": "选择一名有手牌的其他角色拼点"},
    ),
    # 韬·刘备【义起】（自制包）：**A 族第一条限定技**。观看弃牌堆顶 x 张（x = 张数整除 10）→
    # 恒 3 项类型选 1（不可放弃）→ 获得该类所有牌 → 失去武将牌上除【义起】外的技能 →
    # 获得标准版【武圣】【咆哮】（用户 2026-10-31 逐条裁定）。不翻预发动：首步是"看弃牌堆"
    # 的亮牌步，清单渲不出牌面。
    ActiveSkill(
        "tao_liubei",
        "yiqi",
        _yiqi_offers,
        flow=YIQI_FLOW,
        skill_type=SkillType.LIMITED,
        requirement=(
            "限定技：观看弃牌堆顶 x 张牌（x = 弃牌堆张数整除 10），选择并获得其中一种类型的"
            "所有牌；然后你失去武将牌上除此技能外的所有技能，并获得【武圣】【咆哮】"
        ),
        step_requirements={
            "type": (
                "观看弃牌堆顶的 x 张牌（只有你能看），选择一种类型：获得其中该类型的"
                "所有牌（必须选一项）"
            ),
        },
    ),
    # 孙权【制衡】：弃任意张（手牌 + 装备区）→ 摸等量（规则 1009）；票 04 的单步流水线。
    ActiveSkill("sunquan", "zhiheng", _zhiheng_offers, flow=ZHIHENG_FLOW, preactivated=True),
    # 界孙权【制衡】（M6 票 17）：同一条单步清单，另加"弃光所有手牌多摸一张"（§十九 L2137–L2139）。
    ActiveSkill(
        "jie_sunquan",
        "zhiheng",
        _zhiheng_offers,
        flow=JIE_ZHIHENG_FLOW,
        preactivated=True,
        requirement="弃置任意张牌（手牌 / 装备区），然后摸等量的牌；若弃置了所有手牌则多摸一张",
    ),
    # 刘备【仁德】：给一名其他角色任意张手牌；本阶段首次给到第 2 张回复 1 点（规则 976）。
    ActiveSkill("liubei", "rende", _rende_offers, flow=RENDE_FLOW, preactivated=True),
    # 界刘备【仁德】（M6 票 16）：每名角色限一次；本阶段第二张换一次"视为使用一张基本牌"
    # （§十九 L2075–L2077）。骨架候选恒一条，给哪几张 / 给谁由下面两步清单问。
    ActiveSkill(
        "jie_liubei",
        "rende",
        _jie_rende_offers,
        flow=JIE_RENDE_FLOW,
        preactivated=True,
        requirement=(
            "出牌阶段每名角色限一次：交给一名其他角色任意张手牌；若你此次给出了本阶段的"
            "第二张「仁德」牌，可以视为使用一张基本牌"
        ),
    ),
    # 周瑜【反间】：令一名其他角色选花色 → 获得并展示你的一张手牌 → 花色不同则受伤（规则 1017）。
    # W4-2：只把**第一段**（选谁）放进预发动；猜花色 / 挑牌仍由目标逐步应答。
    ActiveSkill("zhouyu", "fanjian", _fanjian_offers, flow=FANJIAN_FLOW, preactivated=True),
    # 华佗【青囊】：弃一张手牌 → 一名已受伤角色回复 1 点（规则 1040）；票 02 的流水线消费者。
    ActiveSkill("huatuo", "qingnang", _qingnang_offers, flow=QINGNANG_FLOW, preactivated=True),
    # 貂蝉【离间】：弃一张牌 + 两名男性角色 → 视为其中一名对另一名使用【决斗】（规则 1049）。
    # W4-2：手写步骤流水线改成声明式（三步各有自己的池：受害者池按使用者）。
    ActiveSkill("diaochan", "lijian", _lijian_offers, flow=LIJIAN_FLOW, preactivated=True),
    # 界貂蝉【离间】（M6 票 07）：**先选目标、再选使用者**（§十九 L2212–L2214；标准版的
    # 牌面不指定次序，故两版各一条流水线），且这条虚拟【决斗】不可响应。
    ActiveSkill(
        "jie_diaochan",
        "lijian",
        _lijian_offers,
        flow=JIE_LIJIAN_FLOW,
        preactivated=True,
        requirement="弃一张牌，依次选择两名男性角色：后者视为对前者使用一张不能被响应的【决斗】",
        step_requirements={
            "card": "弃置1张牌（手牌 / 装备区）",
            # 两次座位询问与标准版**次序相反**：第一次点的目标才是【决斗】打向的人。
            "victim": "选择决斗目标（场上的一名男性角色；先点他，【决斗】打向他）",
            "user": "选择决斗使用者（另一名男性角色；由他对目标使用【决斗】）",
        },
    ),
    # 孙尚香【结姻】：弃两张手牌 + 一名已受伤男性 → 你与其各回复 1 点（规则 1033）。
    ActiveSkill("sunshangxiang", "jieyin", _jieyin_offers, flow=JIEYIN_FLOW, preactivated=True),
    # 界孙尚香【结姻】（M6 票 17）：一名男性；弃一张手牌 或 把一张装备牌置入其装备区；
    # 较高的摸一张、较低的回复 1 点（§十九 L2189–L2192）。二选一随局面变化 ⇒ 不翻预发动。
    ActiveSkill(
        "jie_sunshangxiang",
        "jieyin",
        _jie_jieyin_offers,
        flow=JIE_JIEYIN_FLOW,
        requirement=(
            "选择一名男性角色：弃置一张手牌或将一张装备牌置入其装备区；"
            "然后体力值较高的角色摸一张牌，体力值较低的角色回复 1 点体力"
        ),
        # 三步各有各的问法（票 23）：与标准【结姻】（弃 2 张手牌 + 已受伤男性）同短 id，
        # 读短 id 表会拿到标准版那一行（"已受伤"），故必须在这里显式给。
        step_requirements={
            "seat": "选择一名男性角色",
            "option": "选择弃置一张手牌，或将一张装备牌置入其装备区",
            "card": "弃置一张手牌 / 置入一张装备牌（目标同槽必须有空位）",
        },
    ),
    # 黄盖【苦肉】：失去 1 点体力，然后摸两张牌（§九 L1031；无"出牌阶段限一次"）。
    # 裸发动（零步流水线）⇒ **不翻预发动**：没有步骤可渲染，候选直接进 ``legal_actions``。
    ActiveSkill("huanggai", "kurou", _kurou_offers, flow=KUROU_FLOW),
    # 界黄盖【苦肉】（M6 票 17）：出牌阶段限一次，弃一张牌、失去 1 点体力（不摸牌）——
    # 摸牌整段搬去【诈降】（§十九 L2162–L2163）。一步成本 ⇒ 不翻预发动。
    ActiveSkill(
        "jie_huanggai",
        "kurou",
        _jie_kurou_offers,
        flow=JIE_KUROU_FLOW,
        requirement="出牌阶段限一次：弃置一张牌，失去 1 点体力",
    ),
    # 界大乔【国色】（M6 票 17）：二选一 + 选择结束后摸一张（§十九 L2175–L2178）；
    # 选项闭集随"场上有没有【乐不思蜀】"变化 ⇒ 提交点里手写询问，不翻预发动。
    ActiveSkill(
        "jie_daqiao",
        "guose",
        _jie_guose_offers,
        flow=JIE_GUOSE_FLOW,
        requirement=(
            "出牌阶段限一次，选择一项：1. 将一张方块牌当【乐不思蜀】使用；"
            "2. 弃置一张方块牌并弃置场上的一张【乐不思蜀】。选择结束后你摸一张牌"
        ),
    ),
    # 界关羽【义绝】（M6 票 04）：弃一张牌 → 一名其他角色展示一张手牌 → 红 / 黑分叉。
    # 与【苦肉】同姿态不翻预发动：三步由发动后的询问逐步收集，骨架候选直接进 ``legal_actions``。
    ActiveSkill(
        "jie_guanyu",
        "yijue",
        _yijue_offers,
        flow=YIJUE_FLOW,
        extra_modifiers=(
            Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _yijue_heart_slash_bonus),
        ),
    ),
    # 界徐庶 ☆【荐言】（M6 票 06）：觉醒后才有实例，故候选只在觉醒后的出牌阶段出现。
    ActiveSkill("jie_xushu", "jianyan", _jianyan_offers, flow=JIANYAN_FLOW),
    # 界吕蒙 ☆【攻心】（M6 票 06）：同上。
    ActiveSkill("jie_lvmeng", "gongxin", _gongxin_offers, flow=GONGXIN_FLOW),
    # 神吕蒙移动版【攻心】（用户裁定 2026-10-31）：牌面与界吕蒙 ☆【攻心】**逐字相同**
    # ⇒ 同一条声明 / 同一条流水线（不复制回调）。两版共用"无红桃也观看"的修正。
    ActiveSkill("shen_lvmeng_mobile", "gongxin", _gongxin_offers, flow=GONGXIN_FLOW),
    # 界周瑜【反间】（M6 票 08）：两步由发动者选（手牌 → 目标），二选一由**目标**应答。
    # 与【义绝】同姿态不翻预发动（多步由发动后的询问逐步收集；第二段换应答者）。
    ActiveSkill(
        "jie_zhouyu",
        "fanjian",
        _jie_fanjian_offers,
        flow=JIE_FANJIAN_FLOW,
        requirement="展示一张手牌交给一名其他角色，然后令其选择：展示所有手牌弃置同花色牌 / 失去 1 点体力",
    ),
    # 界华佗【除疠】（M6 票 18）：任意名势力互异的其他角色 → 各弃一张；黑桃者各摸一张
    # （§十九 L2200–L2201）。座位步是"任意名"（势力互异收住上界），逐名选牌住在提交点。
    ActiveSkill(
        "jie_huatuo",
        "chuli",
        _chuli_offers,
        flow=CHULI_FLOW,
        requirement=(
            "出牌阶段限一次：选择任意名势力各不相同的其他角色，"
            "弃置你和这些角色的各一张牌；因此被弃置黑桃牌的角色各摸一张牌"
        ),
    ),
    # 界华雄【势斩】（M6 票 18）：出牌阶段**限两次**，令一名其他角色视为对你使用
    # 一张【决斗】（§十九 L2220–L2221）。次数由提交点自己数（``_USED_SECOND``）。
    ActiveSkill(
        "jie_huaxiong",
        "shizhan",
        _shizhan_offers,
        flow=SHIZHAN_FLOW,
        requirement="出牌阶段限两次：令一名其他角色视为对你使用一张【决斗】",
    ),
    # 移动版于吉【蛊惑】（用户 2026-11-01 裁定）：**两个入口**——出牌阶段主动使用（A 族）
    # **与**"需要牌时"的提供者（六扇窗口，见 `_PROVIDER_SCOPES`）。**无次数限制** ⇒
    # `once_per_phase=False`（不落阶段位）。**翻预发动**（用户 2026-11-02 的 Q3A 裁定）：
    # 出牌阶段是"点蛊惑 → 选 1 张手牌 → 牌名弹窗 → 一次提交"两步（`GUOHUO_FLOW`）；
    # 逐座位质疑仍是段机里的手写循环（清单表达不了循环）。
    ActiveSkill(
        "yuji_mobile",
        "guohuo",
        _guohuo_offers,
        flow=GUOHUO_FLOW,
        preactivated=True,
        requirement=(
            "扣置一张手牌，将此牌当任意一张基本牌或普通锦囊牌使用；"
            "其他角色可依次质疑：真则质疑者各失去 1 点体力，假则各摸一张牌，"
            "然后若此牌为真且为♥️则照常结算，否则此牌作废"
        ),
        # 提供者入口的候选（第二条 `CANDIDATES` 回调，与出牌阶段那条并存：两条读不同
        # 的 `ctx.scope`，互不打扰）。provider 回调由 `catalog.py` 末尾登记进 `PROVIDERS`。
        extra_modifiers=(
            Modifier(ModifierPoint.CANDIDATES, _guohuo_provider_offers),
        ),
        step_requirements={
            # 键的取法同【离间】/【青囊】：``SELECT_CARDS`` 恒读 ``"card"``，
            # ``SELECT_OPTION`` 读询问 ``skill_id`` 冒号后那段（本技能自造
            # ``…:name`` / ``…:target`` / ``…:designation`` 三个后缀，见 `_guohuo_ask_seat`）。
            # 文案逐字取票面 §1.5 的询问剧本；`card` 那一问两个入口共用，故写共通的说法。
            # 牌名这一格 2026-11-02 起是"**列全**、用不了的置灰"（用户 Q1A）：旧文案
            # "（此刻可使用的才列出来）"描述的是那次改动的**反**面。
            "card": "扣置一张手牌",
            "name": "声明一种牌名：基本牌或普通锦囊牌（此刻没有合法目标的名字置灰、不可选）",
            "target": "（按声明的牌名）选择目标",
            "designation": "选择持刀者的【杀】打向谁（【借刀杀人】的附带指定）",
        },
    ),
    # 张角移动版【黄天】（feng-zhangjiao 票 01）：**主公技 · 跨座位注入**——候选进
    # **别座位**的出牌阶段（只有群势力、只有本阶段还没交过、手里真有【闪】/【闪电】），
    # 发动者是那个座位，交给张角。不翻预发动（一步选牌，直提态 + 逐步问就够）。
    ActiveSkill(
        "zhangjiao_mobile",
        "huangtian",
        _huangtian_offers,
        flow=HUANGTIAN_FLOW,
        skill_type=SkillType.LORD,
        requirement="主公技，其他群势力角色的出牌阶段限一次，该角色可以交给你一张【闪】或【闪电】",
        step_requirements={"card": "选择一张【闪】或【闪电】交给张角"},
    ),
    # 典韦移动版【强袭】（huo-dianwei-mobile 票 01）：出牌阶段限一次，失去 1 点体力或
    # 弃置一张武器牌（二选一）→ 对**代价付掉之后**仍在攻击范围内的一名其他角色造成
    # 1 点伤害。不翻预发动（首步是选项步 ⇒ 直提态 + 逐步问，与【神速】同一档）。
    ActiveSkill(
        "dianwei_mobile",
        "qiangxi",
        _qiangxi_offers,
        flow=QIANGXI_FLOW,
        requirement=(
            "出牌阶段限一次，你可以失去1点体力或者弃置一张武器牌，"
            "然后对你攻击范围内的一名其他角色造成1点伤害"
        ),
        step_requirements={
            # SELECT_OPTION 读询问 skill_id 冒号后那段（``qiangxi:option`` ⇒ ``option``）、
            # SELECT_CARDS 恒读 ``card``、手写选目标读 ``…:target`` 的 ``target``。
            "option": "选择一项：失去1点体力 / 弃置一张武器牌",
            "card": "弃置一张武器牌（手牌或装备区）",
            "target": "选择你攻击范围内的一名其他角色",
        },
    ),
    # 神周瑜移动版【业炎】（huo-shenzhouyu-mobile 票 01）：A 族限定技 + 计数式分配弹窗。
    _YEYAN,
    # 张绣【雄乱】（lei-batch 票 02）：A 族限定技 + 预发动（选人后确认才发动）；
    # 代价 = 废除自己的判定区与装备区（隐藏发动 cost：至少 1 个区域还能被废除）。
    _XIONGLUAN,
    # 界魏延移动版【奇谋】（jie-feng-batch 票 02）：A 族限定技 + 单步选 X（中文选项）；
    # 失去 X 点体力 → 本回合距离 -X / 本阶段【杀】上限 +X。不翻预发动。
    _QIMOU,
    # 神甘宁【魄袭】（lei-shenganning 票 01）：A 族主动技 + 手写主体（观看 + 四色选牌 →
    # 两次搬牌 → 按"你的张数"分档）。不翻预发动（首步读别人的手牌，清单渲不出）。
    _POXI,
    # 锋·于禁【节钺】（feng-yujin 票 01）：两步流水线（选角色 → 目标交出）+
    # 手写二选一（标记并交给 / 弃置并伤害）。
    _JIEYUE,
    # 陆抗移动版【决堰】（lukang-mobile 票 01）：单步选装备栏（1～3 项、可动态）→
    # 一个事件废除该栏的槽 + 三条"本阶段 / 本回合"效果。不翻预发动（与【奇谋】同档）。
    _DUANYAN,
    # 陆抗移动版 ☆【怀柔】（lukang-mobile 票 01）：单步选自己的 1 张装备牌（手牌 / 装备区）
    # → 重铸。翻预发动（首步选自己的牌 ⇒ 清单渲得出，装备槽天然可点）。
    _HUAJROU,
)
"""A 族十七条技能（票 04 §逐技能落位表 + M6 票 03 的【苦肉】+ M6 票 04 的【义绝】+
票 06 的两条 ☆ 技能【荐言】/【攻心】+ M6 票 17 的界·吴四条 + M6 票 18 的界华佗【除疠】/
界华雄【势斩】）。

【苦肉】是唯一一条**零步**的 A 族技能：没有步骤可渲染 ⇒ 不翻预发动，骨架候选直接进
``legal_actions``；【义绝】【除疠】的多步由发动后的询问逐步收集（【除疠】的逐名选牌
更是在提交点里），同样不翻预发动；其余七条都走预发动清单。
"""


ACTIVATIONS: MappingProxyType[str, ActivationHandler] = MappingProxyType(
    {skill.skill_id: _activation_handler(skill) for skill in ACTIVE_SKILLS}
)
"""``skill_id`` → 发动回调。引擎只按 :func:`activate_skill` 通用派发，不认识任何具体 id。"""

_REQUIREMENTS: dict[str, str] = {
    "zhiheng": "弃置任意张牌（手牌 / 装备区），然后摸等量的牌",
    "rende": "交给一名其他角色任意张手牌",
    "fanjian": "选择一种花色",
    "qingnang": "弃一张手牌，令一名已受伤角色回复 1 点体力",
    "lijian": "弃一张牌，选择两名男性角色",
    "jieyin": "弃两张手牌，选择一名已受伤男性角色",
    "kurou": "失去1点体力，然后摸两张牌",
    "yijue": "弃置一张牌：令一名其他角色展示一张手牌（红则获得并可令其回复；黑则本回合其非锁定技失效、不能使用或打出手牌）",
    "jianyan": "选择一种牌的类别或颜色，亮出牌堆中一张符合要求的牌并令一名男性角色获得",
    "gongxin": "观看一名其他角色的手牌，然后可以展示其中一张红桃牌并选择：弃置此牌 / 将此牌置于牌堆顶",
}
"""A 族八条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""

_STEP_REQUIREMENTS: dict[str, dict[str, str]] = {
    "lijian": {
        "card": "弃置1张牌（手牌 / 装备区）",
        # W4-fix-3：两问**逐字不同**，且各自写明角色（旧文案两键逐字相同，界面分不出
        # "谁是决斗使用者、谁是目标"）。这份文案随清单的 ``Step.wording`` 下发。
        "user": "选择决斗使用者（场上的一名男性角色）",
        "victim": "选择决斗目标（另一名男性角色，不能是使用者）",
    },
    "zhiheng": {
        # 单步技能（票 04）：唯一的 ``SELECT_CARDS`` 恒读 ``"card"``（与【青囊】同一套键）。
        "card": "弃置任意张牌（手牌 / 装备区）",
    },
    "qingnang": {
        # 键的取法同【离间】：``SELECT_CARDS`` 恒读 ``"card"``（技能帧的那一步选牌），
        # ``SELECT_OPTION`` 读询问 ``skill_id`` 冒号后那段 —— 执行器发的是
        # ``qingnang:seat``（见 :func:`_flow_option_id`），故这里写 ``seat``。
        "card": "弃置1张手牌",
        "seat": "选择一名已受伤角色回复1点体力",
    },
    "jieyin": {
        # 与【青囊】同一套键；差别只在成本是**恰好 2 张**、目标池是"已受伤男性（不含自己）"。
        "card": "弃置2张手牌",
        "seat": "选择一名已受伤男性角色回复1点体力",
    },
    "rende": {
        # 票 05：给牌是成本（不是弃置）——文案要读得出"交给别人"这层意思。
        "card": "选择任意张要交给其他角色的手牌",
        "seat": "选择交给哪名其他角色",
    },
    "yijue": {
        # SELECT_CARDS 的逐步文案键恒读 ``"card"``（见 ``SkillDef.step_requirements``），
        # 而本技能有**两次**选牌（代价 + 目标的展示），故这一句要同时说清两件事。
        "card": "弃置一张牌；随后由被指定者展示一张手牌",
        "seat": "选择一名有手牌的其他角色",
    },
    "fanjian": {
        # 票 06：步 2/3 的**应答者是目标**（不是发动者）——文案必须写出归属，
        # 否则发动者看不懂"为什么屏幕跑到别人那儿去了"（票面明写的要求）。
        "seat": "选择一名其他角色",
        "suit": "由目标选择一种花色",
        "card": "由目标从你的手牌中选择一张",
    },
    "jianyan": {
        "type": "选择一种牌的类别或颜色（基本牌 / 锦囊牌 / 装备牌 / 红色 / 黑色）",
        "seat": "选择一名男性角色获得这张牌",
    },
    "gongxin": {
        "seat": "选择一名手牌非空的其他角色",
        # SELECT_CARDS 的逐步文案键恒读 "card"（与【青囊】同一套键）。
        "card": "观看其手牌：选择一张红桃牌展示（可放弃）",
        "option": "选择：弃置此牌 / 将此牌置于牌堆顶",
    },
    # M6 票 17：界·吴四条。键的取法与上同（SELECT_OPTION 读询问 id 冒号后那段；
    # SELECT_CARDS 恒读 "card"）。
    "jie_zhiheng": {
        "card": "弃置任意张牌（手牌 / 装备区）；弃光所有手牌则多摸一张",
    },
    "jie_kurou": {
        "card": "弃置一张牌（手牌 / 装备区）",
    },
    "jie_guose": {
        "option": "选择一项：方块牌当【乐不思蜀】使用 / 弃方块牌并弃场上一张【乐不思蜀】",
        "card": "选择一张方块牌（手牌 / 装备区）",
        "seat": "选择【乐不思蜀】的目标",
    },
}
"""**多步技能**逐步的问法（票 01 起）；键 = 步骤名（判据见 :attr:`SkillDef.step_requirements`）。

【离间】三步各有各的问法；【青囊】（票 02）、【结姻】（票 03）、【制衡】（票 04）、
【仁德】（票 05）各一至两步。不在此表的技能 ⇒
仍读一句话的 :data:`_REQUIREMENTS`。"""


def active_skill_definitions() -> tuple[SkillDef, ...]:
    """A 族八条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    非锁定（技能面都写了"你可以"）：候选由 ``CANDIDATES`` 挂点产出；``carrier = HERO``
    ⇒ 优先级档由载体派生；``skill_type`` 取声明上的同名字段（缺省 ``NORMAL``；【义起】是
    A 族第一条 ``LIMITED``）——候选一律经 :func:`_limited_offers` 过一道"整局已用"闸。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=skill.skill_type,
            modifiers=(
                Modifier(ModifierPoint.CANDIDATES, _limited_offers(skill, skill.offers)),
                *skill.extra_modifiers,
            ),
            requirement=skill.requirement or _REQUIREMENTS[skill.short_id],
            # 声明级覆盖优先（标准 / 界同短 id 但步骤次序 / 角色不同，M6 票 14）。
            step_requirements=(
                skill.step_requirements or _STEP_REQUIREMENTS.get(skill.short_id)
            ),
            preactivated=skill.preactivated,
        )
        for skill in ACTIVE_SKILLS
    )


def activate_skill(host: Any, frame: Any) -> Any:
    """SKILL 帧的唯一步骤：按 ``frame.data["skill_id"]`` 查表派发发动主体。

    未登记 ⇒ fail-closed（加载期"启用武将的技能引用必须闭合"已在票 01 挡住漏配；
    运行期再撞到即编程错误，不静默跳过）。
    """
    handler = ACTIVATIONS.get(frame.data["skill_id"])
    if handler is None:
        raise NotImplementedError(f"技能 {frame.data['skill_id']!r} 没有登记发动回调")
    return handler(host, frame)
