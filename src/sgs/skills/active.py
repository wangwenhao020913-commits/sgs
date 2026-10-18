r"""出牌阶段**主动技**（A 族）的候选与发动（票 04）。

A 族与其他族的形态差别只有一处：它的候选**不是某张牌的**使用 / 打出，而是**技能本身**
作为出牌阶段空闲时间点的一个行动（票 04 §要做什么 1）。于是落点是：

- **候选**：每条技能一个 ``Modifier(ModifierPoint.CANDIDATES, …)``，只在
  ``CandidateScope.PLAY`` 的**整段追加**里生效（与 H 族的虚拟牌同一姿态）。
  载荷（弃哪些牌 / 给哪些牌 / 选哪名角色）**一律烘进候选**：
  ``picks`` = 实体牌 id（出牌阶段没有"选择面"，回调拿到的就是实体牌），
  ``choices`` = 目标座位（``"seat:<座位号>"``，结构见 :func:`sgs.skills.flow.seat_choice`）。
  **付不起的组合不进池**（手牌不足 / 没有合法目标 ⇒ 一个候选都不造）。
- **发动**：引擎收到 ``ActivateSkill`` 后压一个 ``FrameKind.SKILL`` 帧，步骤表按
  ``skill_id`` 查 :data:`ACTIVATIONS` 通用派发（:func:`activate_skill`）。回调签名与
  ``OPS`` 相同（``(host, frame) -> Control``），故提问让出控制权、压子帧都能自然沿用
  既有的帧内游标范式（票 19 §8）。
- **每阶段限一次**：per-instance ``state.skill_state`` 的**位标记**（:data:`_USED`），
  在**候选生成期**就挡掉用过的技能（不是进池后拒绝）；复位走
  :func:`sgs.skills.reset_phase_skill_state`（引擎在 ``PHASE_PLAY_START`` 调用）。

**组合上限闸**（票 04 §要做什么 3）：**两维相乘**的技能（旧【结姻】2 张 × 目标）在
组合数 > :data:`CANDIDATE_CAP` 时**降级为顺序多轮**——候选只保留"目标"这一维，张数由
发动后的 ``SelectionSpec(sequential=True)`` 逐张询问补齐；**结果集合不变**（逐张给与一次
给到达同一终局）。一维的目标对（【反间】）按票面**不设闸**。
【离间】（票 01）、【青囊】（票 02）、【结姻】（票 03）与【制衡】（票 04）先后改走骨架候选 +
:class:`SkillFlow` 步骤流水线 ⇒ 候选恒一条、与手牌数无关；**这条降级路径在本族已无消费者**
（:func:`_over_cap` / :func:`_pay_sequential` 按 design.md §7 Q4 先留，待后置票统一清除）。

**【仁德】是闸门的例外**（用户裁定）：它的前端交互是"直接多选手牌 + 直接点座位、一步
交出去"，界面必须能一次提交"这组牌 → 这个座位"，故它**不降级**、非空子集 × 目标全部
进池（手牌 8 张 × 7 目标约 1800 条候选，换掉闸门换来交互）。它同时**没有"出牌阶段限
一次"**（规则 976）：``_can_offer(..., once_per_phase=False)``，每轮出牌阶段都在池里。

**苦肉的形状**（裸发动 + 失去体力）：本模块不登记【苦肉】（黄盖不在 16 名名单，
票 12 的 46 条计数也不含它），但机制支持——``ActivateSkill`` 的载荷可全空
（``picks == ()``、``choices == ()``），回调照常经 SKILL 帧执行；
"失去体力"用 ``host.apply_hp_change`` 直接写体力，**不解封** ``LIFE_LOSE_*``。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sgs.data import Gender, SkillType, Suit
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    DamageNature,
    Modifier,
    ModifierContext,
    ModifierPoint,
    check_targets,
)
from sgs.state import (
    ActivateSkill,
    FrameKind,
    GameState,
    MoveReason,
    SelectionSpec,
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
    "ActiveSkill",
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
"""单次询问候选数的组合上限（票 04 §要做什么 3 / `spec.md` §机制面的统一约定）。

只对**两维相乘**的技能启用；本批 A 族已无消费者——【结姻】（票 03）迁走后，"牌 × 目标"
的枚举在本族绝迹（【离间】/【青囊】/【结姻】/【制衡】恒骨架候选，【反间】按票面不设闸）。
旧原语按 design.md §7 Q4 **先留**，待后置票统一清除。
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


def _over_cap(*dimensions: int) -> bool:
    """这些维度相乘是否超过 :data:`CANDIDATE_CAP`（两维相乘技能的过闸判据）。"""
    total = 1
    for dimension in dimensions:
        total *= dimension
    return total > CANDIDATE_CAP


_ASK_PAY_SEQUENTIAL = "active.pay_sequential"
"""手写询问位置（票 22）：:func:`_pay_sequential` 的顺序多轮付成本询问（逐轮各改一次
**询问名**——位置只管"这是哪一处询问"，轮次由 ``key`` 的 ``:<index>`` 承担）。"""


def _pay_sequential(
    host: Any,
    frame: Any,
    owner: int,
    *,
    needed: int,
    key_prefix: str,
    zones: tuple[Zone, ...],
) -> tuple[int, ...] | None:
    """顺序多轮付成本：一次问一张、**问中即弃置**，凑够 ``needed`` 张。

    返回 ``None`` = 已让出控制权（调用方 ``return "stay"``）；否则返回累计弃置的实体牌 id。
    进度（``round`` / ``chosen``）存帧私有载荷，故重入从原处接着走（票 19 §8 的帧内游标）。

    **逐张立即搬走**是必须的：若先把位置攒起来再统一移动，下一轮会用同一份手牌重铺选择面，
    同一个位置会被选中两次（同一个成本付两遍）。
    """
    index = frame.data.get("round", 0)
    chosen = list(frame.data.get("chosen", ()))
    while len(chosen) < needed:
        picked = host.ask_selection(
            frame,
            owner=_ASK_PAY_SEQUENTIAL,
            key=f"{key_prefix}:{index}",
            actor=owner,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(zones=zones, total=(1, 1), sequential=True),
            build_face=lambda: host.selection_face(owner, zones, reveal_hand=True),
        )
        if host.suspended(picked):
            return None
        if not picked:
            break
        if (
            _move_to_discard(
                host, frame, picked, owner=owner, key=f"{key_prefix}:{index}"
            )
            == "stay"
        ):
            return None
        chosen.extend(picked)
        index += 1
        frame.data["chosen"] = tuple(chosen)
        frame.data["round"] = index
    return tuple(chosen)


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
        PickOption(options=_SUIT_OPTIONS, actor=_fanjian_target, key="suit"),
        PickCards(
            zones=(Zone.HAND,),
            lo=1,
            hi=1,
            key="card",
            actor=_fanjian_target,
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
"""【反间】的三步流水线：选谁 →（目标）猜花色 →（目标）从**发动者手牌**挑一张（票 06）。"""


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
    ``frame.data["preativation_answers"]`` 读（清单渲染与校验器共用的唯一通道，W4-1）。
    没有前序答案（清单退化 / 尚未选使用者）⇒ 空池，宁可少给候选也不瞎猜。
    """
    del chosen
    answers = (getattr(frame, "data", {}) or {}).get("preativation_answers") or {}
    users = answers.get("user") or ()
    if not users:
        return ()
    return _lijian_partners(state, owner).get(int(users[0]), ())


def _lijian_effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：弃成本 → 使用者对其受害者发起【决斗】。

    三步都只收集：牌直到这里才搬进弃牌堆（取消 / 校验失败 ⇒ 手牌原样）。``_USED`` 由
    执行器的 ``on_commit`` 在 ``effect`` 之前落位（``once_per_phase=True``），故本函数
    不重复记账。
    """
    owner = frame.data["source"]
    user_seat = int(answers["user"][0])
    victim_seat = int(answers["victim"][0])
    if _move_to_discard(host, frame, answers["card"], owner=owner, key="lijian:cost") == "stay":
        return "stay"
    # 使用者与目标已按同一条 ``check_targets`` 链定下，故这里不再重查"能否使用"的常规
    # 合法性，只走【决斗】自己的结算链——伤害来源随使用者（不是貂蝉）。
    host.start_use(
        pure_virtual_card(host.state, "duel"),
        source=user_seat,
        targets=(victim_seat,),
        use_timing=Timing.PHASE_PLAY_IDLE,
    )
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
# 孙尚香【结姻】：弃 2 张手牌 + 一名已受伤男性 → 你与其各回复 1 点
#
# 票 03：本技能是 :class:`SkillFlow` 的**第三个实例**（【离间】手写、【青囊】声明式），
# 也是确认"``PickCards(lo, hi)`` 能表达恰好 2 张"的那一个：``lo=hi=2`` ⇒
# ``SelectionSpec(total=(2, 2))`` 只枚举恰好两张的组合。旧的"两维过闸降级为顺序多轮"
# 分支删除（同一个技能不再有两种交互形状），`_over_cap` / `_pay_sequential` 由此
# 再无消费者，但按 design.md §7 Q4 **先留、之后再清**。
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
    preativated: bool = False
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
    ActiveSkill("sunquan", "zhiheng", _zhiheng_offers, flow=ZHIHENG_FLOW, preativated=True),
    # 刘备【仁德】：给一名其他角色任意张手牌；本阶段首次给到第 2 张回复 1 点（规则 976）。
    ActiveSkill("liubei", "rende", _rende_offers, flow=RENDE_FLOW, preativated=True),
    # 周瑜【反间】：令一名其他角色选花色 → 获得并展示你的一张手牌 → 花色不同则受伤（规则 1017）。
    # W4-2：只把**第一段**（选谁）放进预发动；猜花色 / 挑牌仍由目标逐步应答。
    ActiveSkill("zhouyu", "fanjian", _fanjian_offers, flow=FANJIAN_FLOW, preativated=True),
    # 华佗【青囊】：弃一张手牌 → 一名已受伤角色回复 1 点（规则 1040）；票 02 的流水线消费者。
    ActiveSkill("huatuo", "qingnang", _qingnang_offers, flow=QINGNANG_FLOW, preativated=True),
    # 貂蝉【离间】：弃一张牌 + 两名男性角色 → 视为其中一名对另一名使用【决斗】（规则 1049）。
    # W4-2：手写步骤流水线改成声明式（三步各有自己的池：受害者池按使用者）。
    ActiveSkill("diaochan", "lijian", _lijian_offers, flow=LIJIAN_FLOW, preativated=True),
    # 孙尚香【结姻】：弃两张手牌 + 一名已受伤男性 → 你与其各回复 1 点（规则 1033）。
    ActiveSkill("sunshangxiang", "jieyin", _jieyin_offers, flow=JIEYIN_FLOW, preativated=True),
)
"""A 族六条技能（票 04 §逐技能落位表；【苦肉】不在 16 名名单、只留形状，见模块 docstring）。"""


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
}
"""A 族六条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""

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
    "fanjian": {
        # 票 06：步 2/3 的**应答者是目标**（不是发动者）——文案必须写出归属，
        # 否则发动者看不懂"为什么屏幕跑到别人那儿去了"（票面明写的要求）。
        "seat": "选择一名其他角色",
        "suit": "由目标选择一种花色",
        "card": "由目标从你的手牌中选择一张",
    },
}
"""**多步技能**逐步的问法（票 01 起）；键 = 步骤名（判据见 :attr:`SkillDef.step_requirements`）。

【离间】三步各有各的问法；【青囊】（票 02）、【结姻】（票 03）、【制衡】（票 04）、
【仁德】（票 05）各一至两步。不在此表的技能 ⇒
仍读一句话的 :data:`_REQUIREMENTS`。"""


def active_skill_definitions() -> tuple[SkillDef, ...]:
    """A 族六条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    非锁定（技能面都写了"你可以"）：候选由 ``CANDIDATES`` 挂点产出；``carrier = HERO``
    ⇒ 优先级档由载体派生；``skill_type = NORMAL``（无主公技）。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.CANDIDATES, skill.offers),),
            requirement=_REQUIREMENTS[skill.short_id],
            step_requirements=_STEP_REQUIREMENTS.get(skill.short_id),
            preativated=skill.preativated,
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
