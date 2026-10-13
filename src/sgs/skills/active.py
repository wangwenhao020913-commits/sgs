r"""出牌阶段**主动技**（A 族）的候选与发动（票 04）。

A 族与其他族的形态差别只有一处：它的候选**不是某张牌的**使用 / 打出，而是**技能本身**
作为出牌阶段空闲时间点的一个行动（票 04 §要做什么 1）。于是落点是：

- **候选**：每条技能一个 ``Modifier(ModifierPoint.CANDIDATES, …)``，只在
  ``CandidateScope.PLAY`` 的**整段追加**里生效（与 H 族的虚拟牌同一姿态）。
  载荷（弃哪些牌 / 给哪些牌 / 选哪名角色）**一律烘进候选**：
  ``picks`` = 实体牌 id（出牌阶段没有"选择面"，回调拿到的就是实体牌），
  ``choices`` = 目标座位（``"seat:<座位号>"``，结构见 :func:`_seat_choice`）。
  **付不起的组合不进池**（手牌不足 / 没有合法目标 ⇒ 一个候选都不造）。
- **发动**：引擎收到 ``ActivateSkill`` 后压一个 ``FrameKind.SKILL`` 帧，步骤表按
  ``skill_id`` 查 :data:`ACTIVATIONS` 通用派发（:func:`activate_skill`）。回调签名与
  ``OPS`` 相同（``(host, frame) -> Control``），故提问让出控制权、压子帧都能自然沿用
  既有的帧内游标范式（票 19 §8）。
- **每阶段限一次**：per-instance ``state.skill_state`` 的**位标记**（:data:`_USED`），
  在**候选生成期**就挡掉用过的技能（不是进池后拒绝）；复位走
  :func:`sgs.skills.reset_phase_skill_state`（引擎在 ``PHASE_PLAY_START`` 调用）。

**组合上限闸**（票 04 §要做什么 3）：**两维相乘**的技能（【结姻】2 张 × 目标）在
组合数 > :data:`CANDIDATE_CAP` 时**降级为顺序多轮**——候选只保留"目标"这一维，张数由
发动后的 ``SelectionSpec(sequential=True)`` 逐张询问补齐；**结果集合不变**（逐张给与一次
给到达同一终局）。一维的 2ⁿ（【制衡】）与一维的目标对（【反间】）按票面**不设闸**。
【离间】（票 01）与【青囊】（票 02）已改走骨架候选 + :class:`SkillFlow` 步骤流水线 ⇒
候选恒一条、与手牌数无关，不再触发本闸（票 03 迁走【结姻】后这条降级路径就没有消费者了）。

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
from itertools import combinations
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
    Pass,
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
    "PickSeats",
    "SkillFlow",
    "activate_skill",
    "active_skill_definitions",
    "run_flow",
]

CANDIDATE_CAP = 128
"""单次询问候选数的组合上限（票 04 §要做什么 3 / `spec.md` §机制面的统一约定）。

只对**仍未迁到步骤流水线的两维相乘**技能启用（【结姻】）；【离间】【青囊】已改骨架候选
（候选恒一条 ⇒ 恒不过闸），【制衡】/【反间】按票面不设闸。
"""

_USED = 1
"""per-instance 阶段位：本出牌阶段已经发动过（候选生成期据此挡掉）。"""
_RECOVERED = 2
"""per-instance 阶段位：【仁德】"首次给到第 2 张"的回复已经发生过。"""

_SEAT_PREFIX = "seat:"
"""``ActivateSkill.choices`` 里目标座位的**规范文本前缀**（见 :func:`_seat_choice`）。"""

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


def _seat_choice(seat: int) -> str:
    """目标座位的候选载荷文本（``ActivateSkill.choices`` 的一项）。

    ``choices`` 是协议里留给 M3"由发动者自己选"的接口位（票 30 的 ``ActivateSkill``
    docstring）；主动技的目标座位正是"发动者自己选"，故烘进这里而不是 ``picks``
    （后者一律是实体牌 id）。带前缀使记录流里"这是座位不是选项"可读。
    """
    return f"{_SEAT_PREFIX}{seat}"


def _chosen_seats(frame: Any) -> tuple[int, ...]:
    """把 ``frame.data["choices"]`` 解回座位元组（顺序即候选里给的顺序）。"""
    return tuple(
        int(choice.removeprefix(_SEAT_PREFIX)) for choice in frame.data.get("choices", ())
    )


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
# 通用步骤流水线（票 02）：声明 + 执行器
#
# 票 01 在【离间】里手写的三步流水线（选择只收集、取消零副作用、``_USED`` 最后才落）
# 在这里被提炼成**声明式**形状：一条主动技声明它的 ``steps`` 与 ``effect``，引擎按
# 帧内 ``ask_answers`` 缓存逐步入栈出栈。**第二个实例**（【青囊】）确认形状后才抽出来，
# 而不是先设计后找消费者（design.md §6 顺序理由 / §9 执行纪律 1）。
# ---------------------------------------------------------------------------

FlowAnswers = Mapping[str, object]
"""流水线收集到的答案：键 = 步骤键。

- :class:`PickCards` / :class:`PickSeats` → ``tuple[int, ...]``（实体牌 id / 座位号）；
- :class:`PickOption` → ``str``（选中的选项文本，原样）。
"""

SeatPool = Callable[[GameState, int, tuple[int, ...]], tuple[int, ...]]
"""``PickSeats.pool(state, owner, chosen)``：这一步此刻可选的座位（座位序 = 玩家序）。

``owner`` = **发动者**（合法性相对发动者定，不是相对应答者）；``chosen`` = **本步**已经
选中的座位 —— 有序多选时第二问据此排除第一问的结果（【离间】受害者池即由它 + 真牌
``check_targets`` 链共同确定）。
"""

ActorRef = int | None | Callable[[GameState, int, FlowAnswers], int]
"""一步由**谁**应答：``None`` = 发动者本人；``int`` = 那个座位；可调用 =
``(state, owner, 已回答的流答案) -> 座位``。

可调用这一档是给票 06 的"选择权在目标"留的**同一扇门**（不把发动者写死进执行器）：
实际的非发动者询问由票 06 落，本票只保证执行器不在这一步预设答案。
"""

FlowEffect = Callable[[Any, Any, FlowAnswers], Any]
"""流水线效果：``(host, frame, answers) -> Control``；只在**所有步骤提交后**跑一次。"""


@dataclass(frozen=True, slots=True)
class PickCards:
    """选牌步骤：在 ``zones`` 上问一次 ``SELECT_CARDS``（``lo..hi`` 张）。

    ``key`` 是帧内 ``ask_selection`` 的缓存键，也是效果读取答案的键（同一条流水线里唯一）；
    ``optional=True`` 给一条 ``Pass`` = 取消整条流水线。
    """

    zones: tuple[Zone, ...]
    lo: int
    hi: int
    optional: bool = True
    key: str = "card"
    actor: ActorRef = None


@dataclass(frozen=True, slots=True)
class PickSeats:
    """选座位步骤：逐次问 ``SELECT_OPTION``（选项文本 ``seat:N``），共 ``lo..hi`` 次。

    ``ordered=True`` 时依次排除本步已选的座位（"顺序即语义"的通用形态；先后两问的问法
    由 ``step_requirements`` 分别给）。``pool`` 返回空池 ⇒ 本步提前收尾：凑够 ``lo`` 就
    提交，不够就取消（**不**发出没有候选的询问 —— 票 01 的 AI 崩溃就是这么来的）。
    """

    lo: int
    hi: int
    pool: SeatPool
    ordered: bool = False
    optional: bool = True
    key: str = "seat"
    actor: ActorRef = None


@dataclass(frozen=True, slots=True)
class PickOption:
    """选项步骤：问一次 ``SELECT_OPTION``（闭集 ``options``，选中文本原样进答案）。"""

    options: tuple[str, ...]
    actor: ActorRef = None
    optional: bool = True
    key: str = "option"


FlowStep = PickCards | PickSeats | PickOption


@dataclass(frozen=True, slots=True)
class SkillFlow:
    """一条主动技的**步骤流水线**：结算前按序问完 ``steps``、收集答案，再跑 ``effect``。

    三条纪律与票 01 手写的【离间】逐字同源：

    - **选择只收集**：步骤只把答案收进 ``answers``，**不搬牌、不改状态**；
    - **取消零副作用**：任一步答 ``Pass``（或选的张数不足 ``lo``）⇒ 直接让帧出栈，
      ``_USED`` 一个位都不落、``effect`` 一次都不跑；因为技能仍未消耗，同一阶段可再发动；
    - **``_USED`` 在最后一步之后**：``once_per_phase=True`` 时由执行器在 ``effect`` 前落位
      （没有阶段限一次的技能 ——【仁德】—— 传 ``False``，阶段位自己管）。
    """

    steps: tuple[FlowStep, ...]
    effect: FlowEffect
    once_per_phase: bool = True


def _flow_actor(step: FlowStep, state: GameState, owner: int, answers: FlowAnswers) -> int:
    """这一步由谁应答：``None`` = 发动者，``int`` = 该座位，可调用 = 由流答案派生。"""
    actor = step.actor
    if actor is None:
        return owner
    if callable(actor):
        return int(actor(state, owner, answers))
    return int(actor)


def _flow_option_id(frame: Any, key: str, index: int, *, multi: bool) -> str:
    """``SELECT_OPTION`` 的 ``skill_id``：既是步骤键、也是 ``ask_answers`` 的缓存键。

    单问步骤的 ``skill_id`` 就是 ``<短 id>:<key>``（投影 ``projection._step_requirement``
    读冒号后那段 ⇒ 直接命中 ``SkillDef.step_requirements``）；同一步问多次时补序号，
    既避免缓存撞车，也让两问可以各有各的文案（键形如 ``seat:0`` / ``seat:1``）。
    """
    short = frame.data["skill_id"].rsplit(".", 1)[-1]
    return f"{short}:{key}:{index}" if multi else f"{short}:{key}"


def _flow_step_cards(
    host: Any, frame: Any, step: PickCards, owner: int, answers: FlowAnswers
) -> tuple[str, object]:
    """跑一步选牌：返回 ``("stay", ())`` / ``("cancel", ())`` / ``("ok", picks)``。"""
    actor = _flow_actor(step, host.state, owner, answers)
    picks = host.ask_selection(
        frame,
        key=step.key,
        actor=actor,
        timing=Timing.PHASE_PLAY_IDLE,
        spec=SelectionSpec(zones=step.zones, total=(step.lo, step.hi), optional=step.optional),
        build_face=lambda: host.selection_face(actor, step.zones, reveal_hand=True),
    )
    if host.suspended(picks):
        return "stay", ()
    # 池为空（``ask_selection`` 直接返回空）与 ``Pass`` 同形：凑不够 ``lo`` 即取消。
    if len(picks) < step.lo:
        return "cancel", ()
    return "ok", tuple(int(card_id) for card_id in picks)


def _flow_step_seats(
    host: Any, frame: Any, step: PickSeats, owner: int, answers: FlowAnswers
) -> tuple[str, object]:
    """跑一步选座位（``lo..hi`` 次 ``SELECT_OPTION``）：返回三态 + 座位元组。"""
    actor = _flow_actor(step, host.state, owner, answers)
    chosen: list[int] = []
    while len(chosen) < step.hi:
        pool = tuple(int(seat) for seat in step.pool(host.state, owner, tuple(chosen)))
        if step.ordered:
            pool = tuple(seat for seat in pool if seat not in chosen)
        if not pool:
            break
        answer = host.ask_option(
            frame,
            actor,
            Timing.PHASE_PLAY_IDLE,
            skill_id=_flow_option_id(frame, step.key, len(chosen), multi=step.hi > 1),
            options=tuple(_seat_choice(seat) for seat in pool),
            optional=step.optional,
        )
        if host.suspended(answer):
            return "stay", ()
        if isinstance(answer, Pass):
            break
        chosen.append(_option_seat(answer))
    if len(chosen) < step.lo:
        return "cancel", ()
    return "ok", tuple(chosen)


def _flow_step_option(
    host: Any, frame: Any, step: PickOption, owner: int, answers: FlowAnswers
) -> tuple[str, object]:
    """跑一步选项询问：返回三态 + 选项文本。"""
    actor = _flow_actor(step, host.state, owner, answers)
    if not step.options:
        return "cancel", ""
    answer = host.ask_option(
        frame,
        actor,
        Timing.PHASE_PLAY_IDLE,
        skill_id=_flow_option_id(frame, step.key, 0, multi=False),
        options=step.options,
        optional=step.optional,
    )
    if host.suspended(answer):
        return "stay", ""
    if isinstance(answer, Pass):
        return "cancel", ""
    return "ok", answer.choice


def run_flow(host: Any, frame: Any, flow: SkillFlow) -> Any:
    """执行一条 :class:`SkillFlow`（``ACTIVATIONS`` 的通用回调之一）。

    帧内**不需要额外游标**：每一步的询问都由 ``ask_answers`` 帧内缓存重入（票 01 的姿态），
    故重入时从头重跑一边步骤既不会发第二次询问、也不会重复副作用；``answers`` 每次重建，
    效果拿到的永远是"已经全部提交"的那一份。

    返回 ``"stay"`` = 让出控制权（某步挂起）、``None`` = 走完（提交或取消）。
    """
    owner = frame.data["source"]
    answers: dict[str, object] = {}
    for step in flow.steps:
        if isinstance(step, PickCards):
            status, value = _flow_step_cards(host, frame, step, owner, answers)
        elif isinstance(step, PickSeats):
            status, value = _flow_step_seats(host, frame, step, owner, answers)
        else:
            status, value = _flow_step_option(host, frame, step, owner, answers)
        if status == "stay":
            return "stay"
        if status == "cancel":
            # 取消：一个副作用都不产生（``_USED`` 不落、``effect`` 不跑），技能可再发动。
            return None
        answers[step.key] = value
    if flow.once_per_phase:
        _mark_phase(host.state, owner, frame.data["skill_id"], _USED)
    return flow.effect(host, frame, answers)


def flow_handler(flow: SkillFlow) -> ActivationHandler:
    """把一条 :class:`SkillFlow` 绑成 ``ACTIVATIONS`` 的 ``(host, frame)`` 回调。"""

    def handler(host: Any, frame: Any) -> Any:
        return run_flow(host, frame, flow)

    return handler


# ---------------------------------------------------------------------------
# 孙权【制衡】：弃任意张（手牌 + 装备区）→ 摸等量（一维 2ⁿ，不设闸）
# ---------------------------------------------------------------------------


def _zhiheng_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    # 一维 2ⁿ：按票 04 §要做什么 3，**不设闸**（组合上限只对两维相乘的技能启用）。
    if not _can_offer(state, ctx):
        return current
    player = state.player(ctx.subject)
    cards = (*player.hand, *player.equip)
    return (
        *current,
        *(
            ActivateSkill(skill_id=ctx.skill_id, picks=picks, answers_seq=ctx.answers_seq)
            for size in range(1, len(cards) + 1)
            for picks in combinations(cards, size)
        ),
    )


def _zhiheng_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    _mark_phase(state, owner, frame.data["skill_id"], _USED)
    picks = tuple(frame.data.get("picks", ()))
    if _move_to_discard(host, frame, picks, owner=owner, key="zhiheng:cost") == "stay":
        return "stay"
    host.draw_for_hand(frame, owner, len(picks))
    return None


# ---------------------------------------------------------------------------
# 刘备【仁德】：给任意张手牌 → 本阶段首次给到第 2 张回复 1 点（两维相乘 ⇒ 闸 + 顺序降级）
# ---------------------------------------------------------------------------


def _rende_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    hand = tuple(state.player(ctx.subject).hand)
    targets = tuple(p.seat for p in state.players if p.alive and p.seat != ctx.subject)
    if not hand or not targets:
        return current
    # 【仁德】**不设组合闸**：它的前端交互是"直接多选手牌 + 直接点座位"，界面要能一次
    # 提交"这组牌 → 这个座位"，所以每一组（非空子集 × 目标）都必须在候选里。
    # 手牌 8 张 × 7 个目标约 1800 条候选（实测 ~227 KiB），在出牌阶段的可接受范围内；
    # 换掉闸门换来的是"没有选项按钮、一步交出去"这条交互（用户裁定）。
    return (
        *current,
        *(
            ActivateSkill(
                skill_id=ctx.skill_id,
                picks=picks,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for size in range(1, len(hand) + 1)
            for picks in combinations(hand, size)
            for target in targets
        ),
    )


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
    """累计给出 ``given`` 张时是否**首次**达到两张并回复 1 点；返回是否压了回复帧。"""
    state = host.state
    if given < 2 or (_phase_flags(state, owner, skill_id) & _RECOVERED):
        return False
    _mark_phase(state, owner, skill_id, _RECOVERED)
    _push_recover(host, frame, source=owner, target=owner)
    return True


def _rende_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    skill_id = frame.data["skill_id"]
    # **不置 ``_USED``**：【仁德】没有"出牌阶段限一次"（规则 976）；本阶段只置
    # ``_RECOVERED``（"首次给到第 2 张"回复一次）。
    target = _chosen_seats(frame)[0]
    picks = tuple(frame.data.get("picks", ()))
    if not picks:  # pragma: no cover - 候选恒带非空 picks；此处只作 fail-soft
        return None
    if _give(host, frame, owner, target, picks, key="rende:cost") == "stay":
        return "stay"
    _rende_recover(host, frame, owner, skill_id, len(picks))
    return None


# ---------------------------------------------------------------------------
# 周瑜【反间】：目标选花色 → 其获得并展示你的一张手牌 → 花色不同则受伤
# ---------------------------------------------------------------------------


def _fanjian_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    if not state.player(ctx.subject).hand:
        return current
    targets = tuple(p.seat for p in state.players if p.alive and p.seat != ctx.subject)
    return (
        *current,
        *(
            ActivateSkill(
                skill_id=ctx.skill_id,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for target in targets
        ),
    )


def _fanjian_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    skill_id = frame.data["skill_id"]
    _mark_phase(state, owner, skill_id, _USED)
    target = _chosen_seats(frame)[0]
    if "suit" not in frame.data:
        answer = host.ask_option(
            frame,
            target,
            Timing.PHASE_PLAY_IDLE,
            skill_id=skill_id,
            options=_SUIT_OPTIONS,
        )
        if host.suspended(answer):
            return "stay"
        frame.data["suit"] = answer.choice
    if "revealed" not in frame.data:
        picked = host.ask_selection(
            frame,
            key="fanjian",
            actor=target,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
            build_face=lambda: host.selection_face(owner, (Zone.HAND,)),
        )
        if host.suspended(picked):
            return "stay"
        card = state.card(picked[0])
        # 载荷先落帧、再搬牌：搬牌可能内联派发"失去牌后"并**挂起**（票 07），
        # 重入时据此跳过本段、直接续跑交付段（否则会把这次移动做第二遍）。
        frame.data["revealed"] = card.card_id
        if host.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.PUT,
            owner=target,
            actor=owner,
        ) == "stay":
            # 搬牌挂起 ⇒ **立刻让出控制权**，不继续做后面的交付与伤害：否则帧会在
            # 应答被消费之前继续推进，"是否发动"的应答会漏给**外层**询问（票 13 §验收 5）。
            return "stay"
    if "delivered" not in frame.data:
        card = state.card(frame.data["revealed"])
        frame.data["delivered"] = True
        if host.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.HAND, target),
            reason=MoveReason.PUT,
            owner=target,
            actor=owner,
        ) == "stay":
            return "stay"
    if "damage_done" not in frame.data:
        # 伤害**单列一个帧内游标**：交付那一步若挂起，重入必须只跑伤害、而不是连交付
        # 一起重做（把游标写在搬牌之前，正是为了让"搬完但没结算"也能被识别）。
        frame.data["damage_done"] = True
        card = state.card(frame.data["revealed"])
        if card.suit != frame.data["suit"]:
            _push_damage(host, frame, source=owner, target=target)
    return None


# ---------------------------------------------------------------------------
# 华佗【青囊】：弃 1 张手牌 → 一名已受伤角色回复 1 点（目标池含自己）
#
# 票 02：本技能是 :class:`SkillFlow` 的**第二个实例**（第一个是票 01 手写的【离间】），
# 声明式形状在这里被确认：骨架候选 + ``PickCards → PickSeats`` 两步，旧的"过闸降级"
# 分支删除（同一个技能不再有两种交互形状）。
# ---------------------------------------------------------------------------


def _qingnang_targets(state: GameState, owner: int, chosen: tuple[int, ...]) -> tuple[int, ...]:
    """【青囊】的可选目标：所有**已受伤**的存活角色（含发动者自己），座位序。

    签名与 :data:`SeatPool` 对齐（``owner`` / ``chosen`` 对静态池无意义）。
    """
    del owner, chosen
    return tuple(p.seat for p in state.players if p.alive and p.hp < p.hp_max)


def _qingnang_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    if not state.player(ctx.subject).hand:
        return current
    if not _qingnang_targets(state, ctx.subject, ()):
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
    # 票 01：候选**恒为一条骨架**，不再烘"牌 × 有序男性对"。
    # 弃哪张、谁是使用者、谁是目标，一律改由发动后的步骤流水线问（:func:`_lijian_activate`）；
    # 故候选数与手牌数、场上男性数无关，``CANDIDATE_CAP`` 的降级路径对【离间】不再触发。
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


def _option_seat(action: Any) -> int:
    """``SelectOption(choice="seat:N")`` → 座位号。"""
    return int(action.choice.removeprefix(_SEAT_PREFIX))


def _lijian_activate(host: Any, frame: Any) -> Any:
    """票 01 的**步骤流水线**：弃 1 张牌 → 选使用者 → 选目标 → 一次性结算。

    三条纪律：

    - **选择只收集、提交才生效**（用户裁定 Q3）：牌直到三步走完才搬进弃牌堆，故任何一步
      取消都留下完整的手牌；
    - **取消不消耗次数**：``_USED`` 位在**流水线全部走完**之后才落，取消 ⇒ 一个位都不落；
    - **顺序即语义**：第一次点的是【决斗】**使用者**，第二次点的是**目标**；有序性不靠
      按钮文案，靠步骤次序，前端不做任何裁决。
    """
    state = host.state
    owner = frame.data["source"]

    # 步 1：弃置 1 张牌（手牌 ∪ 装备区）。``optional=True`` 给出一条 ``Pass`` = 取消。
    picks = host.ask_selection(
        frame,
        key="lijian:card",
        actor=owner,
        timing=Timing.PHASE_PLAY_IDLE,
        spec=SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, 1), optional=True),
        build_face=lambda: host.selection_face(
            owner, (Zone.HAND, Zone.EQUIP), reveal_hand=True
        ),
    )
    if host.suspended(picks):
        return "stay"
    if not picks:
        # 取消：一个副作用都不产生（不搬牌、不落 ``_USED``），技能随时可再发动。
        return None

    partners = _lijian_partners(state, owner)
    # 步 2：**先点的是使用者**（只开"至少有一个合法受害者"的那些男性）。
    user = host.ask_option(
        frame,
        owner,
        Timing.PHASE_PLAY_IDLE,
        skill_id="lijian:user",
        options=tuple(_seat_choice(seat) for seat in partners),
        optional=True,
    )
    if host.suspended(user):
        return "stay"
    if isinstance(user, Pass):
        return None
    user_seat = _option_seat(user)

    # 步 3：**后点的是目标**。使用者不能当自己的目标；受害者一侧仍读真牌同一条
    # ``check_targets`` 链（【空城】据此出池），使用者一侧不受"不能成为目标"限制。
    duel = pure_virtual_card(state, "duel")
    victim = host.ask_option(
        frame,
        owner,
        Timing.PHASE_PLAY_IDLE,
        skill_id="lijian:victim",
        options=tuple(_seat_choice(seat) for seat in partners[user_seat]),
        optional=True,
    )
    if host.suspended(victim):
        return "stay"
    if isinstance(victim, Pass):
        return None
    victim_seat = _option_seat(victim)

    # 三步走完：这才算发动过（次数位在此落，取消路径一个位都不落）。
    _mark_phase(state, owner, frame.data["skill_id"], _USED)
    if _move_to_discard(host, frame, picks, owner=owner, key="lijian:cost") == "stay":
        return "stay"
    # 使用者与目标已按同一条 ``check_targets`` 链定下，故这里不再重查"能否使用"的常规
    # 合法性，只走【决斗】自己的结算链——伤害来源随使用者（不是貂蝉）。
    host.start_use(duel, source=user_seat, targets=(victim_seat,), use_timing=Timing.PHASE_PLAY_IDLE)
    return None


# ---------------------------------------------------------------------------
# 孙尚香【结姻】：弃 2 张手牌 + 一名已受伤男性 → 你与其各回复 1 点
# ---------------------------------------------------------------------------


def _jieyin_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    hand = tuple(state.player(ctx.subject).hand)
    targets = tuple(
        p.seat
        for p in state.players
        if p.alive
        and p.seat != ctx.subject
        and p.gender is Gender.MALE
        and p.hp < p.hp_max
    )
    if len(hand) < 2 or not targets:
        return current
    card_count = len(hand) * (len(hand) - 1) // 2
    if _over_cap(card_count, len(targets)):
        offers = [
            ActivateSkill(
                skill_id=ctx.skill_id,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for target in targets
        ]
    else:
        offers = [
            ActivateSkill(
                skill_id=ctx.skill_id,
                picks=pair,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for pair in combinations(hand, 2)
            for target in targets
        ]
    return (*current, *offers)


def _jieyin_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    _mark_phase(state, owner, frame.data["skill_id"], _USED)
    target = _chosen_seats(frame)[0]
    picks = tuple(frame.data.get("picks", ()))
    if picks:
        if _move_to_discard(host, frame, picks, owner=owner, key="jieyin:cost") == "stay":
            return "stay"
    else:
        paid = _pay_sequential(
            host,
            frame,
            owner,
            needed=2,
            key_prefix="jieyin",
            zones=(Zone.HAND,),
        )
        if paid is None:
            return "stay"
    _push_recover(host, frame, source=owner, target=target)
    _push_recover(host, frame, source=owner, target=owner)
    return None


# ---------------------------------------------------------------------------
# 登记：一条声明 = 候选回调 + 发动回调（两者共用同一个 skill_id）
# ---------------------------------------------------------------------------


ActivationHandler = Callable[[Any, Any], Any]
"""发动回调：``(host, frame) -> Control``，与 ``OPS`` / 钩子同形。"""


@dataclass(frozen=True, slots=True)
class ActiveSkill(HeroSkillDecl):
    """A 族一条技能的**声明**：候选构造 + 发动主体（票 04 §表一行）。

    ``activate`` 与 ``flow`` **恰有一个**：未迁的主动技（票 04 的老四条 + 【结姻】）给手写
    回调；迁到步骤流水线的技能（票 01 的【离间】形状、票 02 起用 :class:`SkillFlow` 声明）
    给 ``flow``，回调由 :func:`flow_handler` 派生。这样声明面只有一份真源（谁发动、问什么、
    怎么结算），不会出现"回调与声明各写一套"的漂移。
    """

    offers: Callable[[GameState, ModifierContext, Any], Any]
    activate: ActivationHandler | None = None
    flow: SkillFlow | None = None

    def __post_init__(self) -> None:
        if (self.activate is None) == (self.flow is None):
            raise ValueError(f"{self.skill_id} 必须恰有 activate / flow 之一")


ACTIVE_SKILLS: tuple[ActiveSkill, ...] = (
    # 孙权【制衡】：弃任意张（手牌 + 装备区）→ 摸等量（规则 1009）。
    ActiveSkill("sunquan", "zhiheng", _zhiheng_offers, _zhiheng_activate),
    # 刘备【仁德】：给一名其他角色任意张手牌；本阶段首次给到第 2 张回复 1 点（规则 976）。
    ActiveSkill("liubei", "rende", _rende_offers, _rende_activate),
    # 周瑜【反间】：令一名其他角色选花色 → 获得并展示你的一张手牌 → 花色不同则受伤（规则 1017）。
    ActiveSkill("zhouyu", "fanjian", _fanjian_offers, _fanjian_activate),
    # 华佗【青囊】：弃一张手牌 → 一名已受伤角色回复 1 点（规则 1040）；票 02 的流水线消费者。
    ActiveSkill("huatuo", "qingnang", _qingnang_offers, flow=QINGNANG_FLOW),
    # 貂蝉【离间】：弃一张牌 + 两名男性角色 → 视为其中一名对另一名使用【决斗】（规则 1049）。
    ActiveSkill("diaochan", "lijian", _lijian_offers, _lijian_activate),
    # 孙尚香【结姻】：弃两张手牌 + 一名已受伤男性 → 你与其各回复 1 点（规则 1033）。
    ActiveSkill("sunshangxiang", "jieyin", _jieyin_offers, _jieyin_activate),
)
"""A 族六条技能（票 04 §逐技能落位表；【苦肉】不在 16 名名单、只留形状，见模块 docstring）。"""


def _activation_handler(skill: ActiveSkill) -> ActivationHandler:
    """一条声明的发动回调：手写回调原样取用，声明式流水线由 :func:`flow_handler` 派生。"""
    if skill.flow is not None:
        return flow_handler(skill.flow)
    assert skill.activate is not None  # __post_init__ 已保证恰有其一
    return skill.activate


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
        "card": "弃置1张牌",
        "user": "选择场上两名男性角色，先点选的是决斗使用者，后点选的是决斗的目标",
        "victim": "选择场上两名男性角色，先点选的是决斗使用者，后点选的是决斗的目标",
    },
    "qingnang": {
        # 键的取法同【离间】：``SELECT_CARDS`` 恒读 ``"card"``（技能帧的那一步选牌），
        # ``SELECT_OPTION`` 读询问 ``skill_id`` 冒号后那段 —— 执行器发的是
        # ``qingnang:seat``（见 :func:`_flow_option_id`），故这里写 ``seat``。
        "card": "弃置1张手牌",
        "seat": "选择一名已受伤角色回复1点体力",
    },
}
"""**多步技能**逐步的问法（票 01 起）；键 = 步骤名（判据见 :attr:`SkillDef.step_requirements`）。

【离间】三步各有各的问法；【青囊】两步（票 02）。不在此表的技能 ⇒ 仍读一句话的
:data:`_REQUIREMENTS`。"""


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
