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

**苦肉**（M6 票 03 起真登记）：§九 L1015 的标准版没有"出牌阶段限一次"、也不弃牌，
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
    SkillType,
    Suit,
    card_category,
    color_of_suit,
    hero_skill_id,
)
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    DamageNature,
    Modifier,
    ModifierContext,
    ModifierPoint,
    behavior_for,
    check_targets,
    is_slash,
    place_on_deck,
)
from sgs.resolution import Tag, add_tag
from sgs.state import (
    ActivateSkill,
    FrameKind,
    GameState,
    MoveReason,
    SelectionSpec,
    Slot,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import (
    HeroSkillDecl,
    SkillDef,
    _move_by_source,
    instance_key,
    resolve_instances,
)
from .virtual import pure_virtual_card

__all__ = [
    "ACTIVATIONS",
    "ACTIVE_SKILLS",
    "CANDIDATE_CAP",
    "JIE_LIJIAN",
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


def _push_damage(host: Any, frame: Any, *, source: int, target: int, amount: int = 1) -> None:
    """压一个 ``DAMAGE`` 子帧（主动技的伤害无渠道牌，故 ``channel = None``）。"""
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": frame.data.get("card"),
            "use_timing": frame.data.get("use_timing"),
            "target": target,
            "channel": frame.data.get("card"),
            "nature": DamageNature.NORMAL,
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

        def handler(host: Any, frame: Any) -> Any:
            def commit() -> None:
                _mark_phase(
                    host.state, frame.data["source"], frame.data["skill_id"], _USED
                )

            return run_flow(
                host,
                frame,
                flow,
                on_commit=commit if flow.once_per_phase else None,
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
    # 已翻预发动的技能里删掉——弃哪张、谁是使用者、谁是目标由 :data:`LIJIAN_FLOW` 的
    # 三步清单表达；故候选数与手牌数、场上男性数无关，``CANDIDATE_CAP`` 的降级路径对
    # 【离间】不再触发。
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


def _lijian_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：弃成本 → 使用者对其受害者发起【决斗】。

    三步都只收集：牌直到这里才搬进弃牌堆（取消 / 校验失败 ⇒ 手牌原样）。``_USED`` 由
    执行器的 ``on_commit`` 在 ``effect`` 之前落位（``once_per_phase=True``），故本函数
    不重复记账。

    **标准 / 界两版共用本函数**：界貂蝉（``frame.data["skill_id"] == JIE_LIJIAN``）把这条
    虚拟【决斗】标成**不可响应**（全场档 ``Tag.UNRESPONSIVE``）；标准版一字不改。
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
# §十九 L2196–L2198：离间：出牌阶段限一次，你可以弃置一张牌，依次选择两名男性其他
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
# 界版与标准版**同一条流水线**（同一个 ``SkillFlow`` 对象、同一个提交点）：两者牌面只差
# "不能被响应"这一句，故差异落成提交点里的一个条件分支，而不是把三步再抄一份
# （与【克己】【马术】的"界版只是另一个武将 id"同一姿态）。
# ---------------------------------------------------------------------------

JIE_LIJIAN = hero_skill_id("jie_diaochan", "lijian")
"""界貂蝉【离间】的全限定技能 id（§十九 L2196–L2198）。"""

_LIJIAN_STARTED = "lijian_started"
"""帧私有：这次发动已经开出虚拟【决斗】（重入不重复开第二次）。"""


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


def _jieyin_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：搬两张成本进弃牌堆，再压两个回复帧（``_USED`` 已由执行器在调用前落位）。"""
    owner = frame.data["source"]
    seats = answers["seat"]
    cards = answers["card"]
    assert isinstance(seats, tuple) and isinstance(cards, tuple)
    if _move_to_discard(host, frame, cards, owner=owner, key="jieyin:cost") == "stay":
        return "stay"
    target = int(seats[0])
    _push_recover(host, frame, source=owner, target=target)
    _push_recover(host, frame, source=owner, target=owner)
    return None


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
# 黄盖【苦肉】（M6 票 03）：出牌阶段，你可以失去 1 点体力，然后摸两张牌
#
# §九 L1015 的**标准版**原文没有"限一次"、也不弃置牌：``once_per_phase=False`` ⇒
# 同一出牌阶段可反复发动（界黄盖的"限一次 + 弃一张"随票 17，不在这里）。
# 形态 = **裸发动**（``picks == ()`` / ``choices == ()``，候选直接进 ``legal_actions``，
# 不走预发动清单）+ 失去体力 + 摸两张。
#
# **失去体力 ≠ 受到伤害**：苦肉不压 DAMAGE 帧、不过 DAMAGE_* 与伤害修正链，只压
# ``LIFE_DECREASE`` 帧走唯一的体力写入点（该帧自带濒死插入——1 点体力发动时真的进
# 求桃，而不是静默停在 0 血）。体力扣完后才摸两张，故被救回之后才摸（"然后"）。
# ---------------------------------------------------------------------------

KUROU_DRAW = 2
"""【苦肉】摸牌数（§九 L1015：摸两张牌）。"""

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
# 界关羽【义绝】（M6 票 04）：弃一张牌 → 一名其他角色展示一张手牌 → 红 / 黑分叉
#
# 规则原文（§十九 L2071–L2074）：
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
# 规则原文（§十九 L2116–L2117）：
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
# 规则原文（§十九 L2142–L2144）：
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
    """提交点：观看（整副手牌授予）→（可选）展示一张红桃 → 弃置 / 置于牌堆顶。"""
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
    control = host.move_in_frame(
        frame,
        host.state.refs((card_id,)),
        to_zone=ZoneRef(Zone.DECK),
        reason=MoveReason.PUT,
        owner=target,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    place_on_deck(host.state, (host.state.card(card_id),), to_top=True)
    return None


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
    preactivated: bool = False
    """本技能是否已迁到**预发动**（W4-1 的迁移开关，缺省 ``False``）。

    ``True`` 时引擎在出牌阶段把它的 ``ActivateSkill`` 骨架从 ``legal_actions`` 里删掉、
    只由 ``PlayPhaseQuery.preactivations`` 表达可用性（清单由 ``flow`` 渲染）。本票只落
    机制，逐条技能由 W4-2 翻这个位——翻之前既有候选 / 测试 / 录像一字不改。
    """

    def __post_init__(self) -> None:
        if (self.activate is None) == (self.flow is None):
            raise ValueError(f"{self.skill_id} 必须恰有 activate / flow 之一")


ACTIVE_SKILLS: tuple[ActiveSkill, ...] = (
    # 孙权【制衡】：弃任意张（手牌 + 装备区）→ 摸等量（规则 1009）；票 04 的单步流水线。
    ActiveSkill("sunquan", "zhiheng", _zhiheng_offers, flow=ZHIHENG_FLOW, preactivated=True),
    # 刘备【仁德】：给一名其他角色任意张手牌；本阶段首次给到第 2 张回复 1 点（规则 976）。
    ActiveSkill("liubei", "rende", _rende_offers, flow=RENDE_FLOW, preactivated=True),
    # 周瑜【反间】：令一名其他角色选花色 → 获得并展示你的一张手牌 → 花色不同则受伤（规则 1017）。
    # W4-2：只把**第一段**（选谁）放进预发动；猜花色 / 挑牌仍由目标逐步应答。
    ActiveSkill("zhouyu", "fanjian", _fanjian_offers, flow=FANJIAN_FLOW, preactivated=True),
    # 华佗【青囊】：弃一张手牌 → 一名已受伤角色回复 1 点（规则 1040）；票 02 的流水线消费者。
    ActiveSkill("huatuo", "qingnang", _qingnang_offers, flow=QINGNANG_FLOW, preactivated=True),
    # 貂蝉【离间】：弃一张牌 + 两名男性角色 → 视为其中一名对另一名使用【决斗】（规则 1049）。
    # W4-2：手写步骤流水线改成声明式（三步各有自己的池：受害者池按使用者）。
    ActiveSkill("diaochan", "lijian", _lijian_offers, flow=LIJIAN_FLOW, preactivated=True),
    # 界貂蝉【离间】（M6 票 07）：判据 / 步骤与标准版相同，只有虚拟【决斗】不可响应。
    ActiveSkill(
        "jie_diaochan",
        "lijian",
        _lijian_offers,
        flow=LIJIAN_FLOW,
        preactivated=True,
        requirement="弃一张牌，依次选择两名男性角色：后者视为对前者使用一张不能被响应的【决斗】",
    ),
    # 孙尚香【结姻】：弃两张手牌 + 一名已受伤男性 → 你与其各回复 1 点（规则 1033）。
    ActiveSkill("sunshangxiang", "jieyin", _jieyin_offers, flow=JIEYIN_FLOW, preactivated=True),
    # 黄盖【苦肉】：失去 1 点体力，然后摸两张牌（§九 L1015；无"出牌阶段限一次"）。
    # 裸发动（零步流水线）⇒ **不翻预发动**：没有步骤可渲染，候选直接进 ``legal_actions``。
    ActiveSkill("huanggai", "kurou", _kurou_offers, flow=KUROU_FLOW),
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
)
"""A 族十条技能（票 04 §逐技能落位表 + M6 票 03 的【苦肉】+ M6 票 04 的【义绝】+
票 06 的两条 ☆ 技能【荐言】/【攻心】）。

【苦肉】是唯一一条**零步**的 A 族技能：没有步骤可渲染 ⇒ 不翻预发动，骨架候选直接进
``legal_actions``；【义绝】的三步由发动后的询问逐步收集，同样不翻预发动；其余六条都走
预发动清单。
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
}
"""**多步技能**逐步的问法（票 01 起）；键 = 步骤名（判据见 :attr:`SkillDef.step_requirements`）。

【离间】三步各有各的问法；【青囊】（票 02）、【结姻】（票 03）、【制衡】（票 04）、
【仁德】（票 05）各一至两步。不在此表的技能 ⇒
仍读一句话的 :data:`_REQUIREMENTS`。"""


def active_skill_definitions() -> tuple[SkillDef, ...]:
    """A 族八条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    非锁定（技能面都写了"你可以"）：候选由 ``CANDIDATES`` 挂点产出；``carrier = HERO``
    ⇒ 优先级档由载体派生；``skill_type = NORMAL``（无主公技）。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(
                Modifier(ModifierPoint.CANDIDATES, skill.offers),
                *skill.extra_modifiers,
            ),
            requirement=skill.requirement or _REQUIREMENTS[skill.short_id],
            step_requirements=_STEP_REQUIREMENTS.get(skill.short_id),
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
