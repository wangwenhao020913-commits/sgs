r"""通用**步骤流水线**（票 02 起；票 07 抽成独立模块）。

一条技能声明它的 ``steps`` 与 ``effect``，执行器按帧内 ``ask_answers`` 缓存逐步入栈出栈：
每一步只**收集答案**、``effect`` 只在**所有步骤提交后**跑一次；任一步答 ``Pass``（或选的
张数不足 ``lo``）⇒ 取消，一个副作用都不产生。三条纪律与票 01 手写的【离间】逐字同源。

**为什么是独立模块**（票 07 §1 的硬约束）：主动技（:mod:`sgs.skills.active`）与被动技
（钩子 / 接缝，声明在 :mod:`sgs.skills.catalog`）都要用它，而 ``active`` 已经
``from .catalog import …``；流水线若留在 ``active``，``catalog`` 再 import ``active``
就是循环。故本模块**反向只依赖** ``sgs.state`` / ``sgs.timing`` 一类低层模块，
**不得** import ``catalog``；``active`` 从本模块重导出这些名字（既有 import 点与
``__all__`` 不变），消费者无需改 import。

**主动侧与被动侧的差别只有三处**，都是显式的参数 / 字段，不是执行器里的特判：

============================  ==========================  ================================
                             主动技                      被动技（票 07）
============================  ==========================  ================================
``run_flow`` 的 ``owner``    ``frame.data["source"]``    ``instance.owner``（帧的
（缺省读前者）                                           ``skill_owner`` 由派发环保管）
``run_flow`` 的 ``timing``   ``PHASE_PLAY_IDLE``         派发环当前持有的时机
``on_commit``                落本阶段的 ``_USED`` 位     不落（被动技没有"阶段限一次"）
============================  ==========================  ================================

**步骤的派生参数读得到帧**（票 07 §3）：``actor`` / ``face`` / ``pool`` 的可调用档一律收
``frame`` —— 被动技的选取面几乎都要读帧（【反馈】的面 = 伤害来源、【流离】的新目标池要
排除使用者），只给 ``(state, owner, answers)`` 是读不到的。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sgs.effects import (
    CandidateScope,
    ModifierContext,
    ModifierPoint,
    apply_modifiers,
    locate,
)
from sgs.state import GameState, Pass, SelectionSpec, Slot, SlotKind, Zone, ZoneRef
from sgs.timing import Timing

__all__ = [
    "SEAT_PREFIX",
    "ActorRef",
    "FlowAnswers",
    "FlowEffect",
    "FlowStep",
    "PickCards",
    "PickOption",
    "PickProvide",
    "PickSeats",
    "SeatPool",
    "SkillFlow",
    "option_seat",
    "run_flow",
    "seat_choice",
    "zone_pool_size",
]

SEAT_PREFIX = "seat:"
"""座位选项的**规范文本前缀**（``ActivateSkill.choices`` / ``SELECT_OPTION.options`` 共用）。

带前缀使记录流里"这是座位不是选项"可读；执行器发它、解析它也在这里（唯一真源）。
"""


def seat_choice(seat: int) -> str:
    """目标座位的候选载荷文本（``ActivateSkill.choices`` / ``SELECT_OPTION.options`` 的一项）。

    ``choices`` 是协议里留给 M3"由发动者自己选"的接口位（票 30 的 ``ActivateSkill``
    docstring）；主动技的目标座位正是"发动者自己选"，故烘进这里而不是 ``picks``
    （后者一律是实体牌 id）。
    """
    return f"{SEAT_PREFIX}{seat}"


def option_seat(action: Any) -> int:
    """``SelectOption(choice="seat:N")`` → 座位号。"""
    return int(action.choice.removeprefix(SEAT_PREFIX))


FlowAnswers = Mapping[str, object]
"""流水线收集到的答案：键 = 步骤键。

- :class:`PickCards` / :class:`PickSeats` → ``tuple[int, ...]``（实体牌 id / 座位号）；
- :class:`PickProvide` → ``tuple[CardRef, ...]``（**牌面**，因为虚拟 B 背后没有实体牌）；
- :class:`PickOption` → ``str``（选中的选项文本，原样）。
"""

SeatPool = Callable[[GameState, Any, int, tuple[int, ...]], tuple[int, ...]]
"""``PickSeats.pool(state, frame, owner, chosen)``：这一步此刻可选的座位（座位序 = 玩家序）。

``owner`` = **发动者**（合法性相对发动者定，不是相对应答者）；``chosen`` = **本步**已经
选中的座位 —— 有序多选时第二问据此排除第一问的结果（【离间】受害者池即由它 + 真牌
``check_targets`` 链共同确定）。``frame`` 是票 07 的加法：被动技的池常要读帧
（【流离】的新目标要排除本次使用的使用者）。
"""

ActorRef = int | None | Callable[[GameState, Any, int, FlowAnswers], int]
"""一步由**谁**应答：``None`` = 发动者本人；``int`` = 那个座位；可调用 =
``(state, frame, owner, 已回答的流答案) -> 座位``。

可调用这一档是给票 06 的"选择权在目标"与票 07 的"选取面读帧"留的**同一扇门**
（不把发动者写死进执行器）：实际的非发动者询问由票 06 落，票 07 只补上 ``frame``。
"""

FlowEffect = Callable[[Any, Any, FlowAnswers], Any]
"""流水线效果：``(host, frame, answers) -> Control``；只在**所有步骤提交后**跑一次。"""


@dataclass(frozen=True, slots=True)
class PickCards:
    """选牌步骤：在 ``zones`` 上问一次 ``SELECT_CARDS``（``lo..hi`` 张）。

    ``key`` 是**效果读取答案**的键（同一条流水线里唯一）；``optional=True`` 给一条
    ``Pass`` = 取消整条流水线。``cache_key`` 是帧内 ``ask_selection`` 的**缓存键**，
    缺省 = ``key``；它可以是 ``str``，也可以是 ``(frame) -> str``（票 07）——**同一次伤害
    事件按伤害值逐轮派发**时，第 2 点的缓存键必须与第 1 点不同，否则第二次会命中第一次
    留下的缓存答案（【反馈】的 ``fankui_step_key``）。两件事分开，是因为"这一步叫什么"
    与"这一次问的答案存哪儿"在逐点派发里不再是一回事。

    ``hi=None`` = **"全部可选牌"**（票 04）：执行器在发问前把它解析成**过滤后**选择面的
    槽位数（:func:`zone_pool_size` 给出 ``zones`` 此刻对 ``face`` 铺出的槽位，再按
    ``card_types`` 筛），故 ``spec.total`` 里进的是**真实上限**，前端看到的选择面与引擎
    的候选面同源。【制衡】的"任意张"正是这个形状；写死一个大哨兵值（如 999）虽被
    ``_select_cards`` 夹住、功能上安全，但会把一个不存在的上限投影给前端。

    ``card_types``（票 09 的加法）：**牌名 id 白名单**，空元组 = 不筛。与
    :attr:`~sgs.skills.catalog.ActivationPlan.card_types` 同一语义（【青龙偃月刀】
    的选取面只铺【杀】），但过滤发生在**步骤层**——票 09 把成本从发动询问的候选里
    搬到这里之后，"只挑【杀】"这件事必须跟着成本一起下沉。过滤后池为空 ⇒ 与
    ``face`` 返回空元组同形：**不发没有候选的询问**，走取消。``hi=None`` 的上限由
    :func:`_filtered_zone_pool_size` 按**过滤后**的池重算（与 :meth:`Engine.ask_selection`
    收到的同一份槽位），故"上限"与"选择面"仍同源。

    ``face`` / ``reveal_hand``（票 06）：**选择面铺谁的区**。``face=None``（缺省）=
    应答者自己的区 —— 青囊 / 结姻 / 制衡 / 仁德都是"自己选自己的牌"。**反间**是
    应答者（目标）选**发动者**的牌，故把 ``face`` 指回发动者（``lambda s, f, owner, a: owner``）
    而 ``actor`` 仍是目标：这两件事在【反间】里第一次分开，正是票 06 要加深的语义。
    **反馈**（票 07）则是两者都指向伤害来源：应答者是持有者，铺的是**来源**的区。

    ``slot_kinds``（票 10 的加法）：**槽位种类白名单**，空元组 = 不筛。与 ``card_types``
    同一条链路（同一份选择面、同步过滤 ``slots`` / ``card_ids``、``hi=None`` 的上限按
    过滤后的池重算）。它服务的是"选取面只铺某类槽"的技能 —— 【麒麟弓】只铺目标的
    **坐骑槽**（``(HORSE_DEFENSE, HORSE_OFFENSE)``），而牌名 id 认不出"这是不是坐骑"
    （坐骑是六个不同牌名）。两条白名单可同时给（依次作用）。
    """

    zones: tuple[Zone, ...]
    lo: int
    hi: int | None
    optional: bool = True
    key: str = "card"
    actor: ActorRef = None
    face: ActorRef = None
    reveal_hand: bool = True
    cache_key: str | Callable[[Any], str] | None = None
    card_types: tuple[str, ...] = ()
    slot_kinds: tuple[SlotKind, ...] = ()


@dataclass(frozen=True, slots=True)
class PickProvide:
    """"提供一张 B"步骤（票 14）：问一次"用一张 B"，候选 = 实体 B ＋ 转化技的虚拟 B。

    ``zones`` 里**实体 B**（牌名 = ``type_id``）进面；各转化技的**虚拟 B** 由既有的
    :data:`~sgs.skills.ModifierPoint.CANDIDATES` 通道产出（与出牌阶段 / 响应窗口里
    "把红牌当【杀】"的是**同一批回调**），故"哪些牌能当 B"只有一处判据
    （``heroes.CONVERSION_SKILLS`` 的声明表）。**只收单来源**转化——多来源的
    【丈八蛇矛】需要"先选几张"的第二维，票 14 不做。

    答案 = ``tuple[CardRef, ...]``（**牌面**）：虚拟 B 背后没有实体牌，回传实体 id 会
    把"转出来的是哪张牌"丢掉。``lo`` / ``hi`` / ``optional`` / ``key`` / ``actor`` 与
    :class:`PickCards` 同义。``hi=None`` 尚无消费者，本步恒取定数张。
    """

    type_id: str
    zones: tuple[Zone, ...] = (Zone.HAND,)
    lo: int = 1
    hi: int = 1
    optional: bool = False
    key: str = "card"
    actor: ActorRef = None


@dataclass(frozen=True, slots=True)
class PickSeats:
    """选座位步骤：逐次问 ``SELECT_OPTION``（选项文本 ``seat:N``），共 ``lo..hi`` 次。

    ``ordered=True`` 时依次排除本步已选的座位（"顺序即语义"的通用形态；先后两问的问法
    由 ``step_requirements`` 分别给）。``pool`` 返回空池 ⇒ 本步提前收尾：凑够 ``lo`` 就
    提交，不够就取消（**不**发出没有候选的询问 —— 票 01 的 AI 崩溃就是这么来的）。

    ``key`` 是效果读取答案的键；同一步问多次时询问的 ``skill_id`` / 缓存键补序号
    （``seat:0`` / ``seat:1``），故效果读到的永远是 ``answers["seat"]`` = 座位元组。
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


FlowStep = PickCards | PickSeats | PickOption | PickProvide


@dataclass(frozen=True, slots=True)
class SkillFlow:
    """一条技能的**步骤流水线**：结算前按序问完 ``steps``、收集答案，再跑 ``effect``。

    三条纪律与票 01 手写的【离间】逐字同源：

    - **选择只收集**：步骤只把答案收进 ``answers``，**不搬牌、不改状态**；
    - **取消零副作用**：任一步答 ``Pass``（或选的张数不足 ``lo``）⇒ 直接让帧出栈，
      阶段位一个都不落、``effect`` 一次都不跑；因为技能仍未消耗，同一阶段可再发动；
    - **提交点由调用方定义**：``once_per_phase=True`` 时由执行器在 ``effect`` 前调
      ``run_flow(..., on_commit=…)`` 传进来的回调（主动技传"落 ``_USED`` 位"，
      没有阶段限一次的【仁德】与被动技传 ``None``）。
    - **``on_commit`` 必须幂等**（票 12 §7）：``effect`` 里的移动可能内联派发钩子并**挂起**，
      续跑是**重新进入同一个 op** ⇒ ``on_commit`` 会被**重复调用**。当前唯一消费者
      ``active._mark_phase`` 落的是幂等 OR 位，故安全；后来者若在这里做"只能发生一次"的
      副作用，必须自带帧私有守卫（`CODING_STANDARDS.md` §3「会挂起的操作必须幂等」）。

    **边界：本语言没有"循环 / 每轮提交"步骤**（票 16）。任一步答 ``Pass`` 都只意味着
    *取消整条流水线*（零副作用），没有"停循环但不回滚已提交轮"这第二档出口；逐轮提交由
    **手写的帧内游标循环**承担（:func:`sgs.skills.catalog._ice_sword`、
    :func:`sgs.skills.catalog._double_sword`、:func:`sgs.skills.damage_after._yiji`）。
    票 10 / 票 11 / 票 16 三次评估都不为它加第二套提交语义（逐条理由见
    ``catalog._ice_sword`` 的 docstring）。后来者若要在这里加循环步骤，
    **A 族 / 【反馈】/【流离】/ 装备各条的"取消零副作用"必须一字不动** —— 它是本模块的
    中心契约，不按消费者分档。
    """

    steps: tuple[FlowStep, ...]
    effect: FlowEffect
    once_per_phase: bool = True


def _resolve_actor(
    ref: ActorRef, state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> int:
    """把一个 :data:`ActorRef` 解析成座位：``None`` = 发动者，``int`` = 该座位，可调用 = 派生。"""
    if ref is None:
        return owner
    if callable(ref):
        return int(ref(state, frame, owner, answers))
    return int(ref)


def _resolve_face(ref: ActorRef, state: GameState, frame: Any, owner: int, answers: FlowAnswers) -> Any:
    """把 ``PickCards.face`` 解析成"铺谁的区"：座位号，或**空元组** = 这一步不存在。

    ``face`` 比 :func:`_resolve_actor` 多一档：派生回调返回 ``()`` 表示"此刻没有合法的
    选取对象"，执行器据此**不发询问**（票 07 §5 的被动侧守卫）。空元组是唯一被放行的
    非座位取值，其余一律解析成座位号（读错在 :func:`zone_pool_size` 立刻炸，不静默）。
    """
    if ref is None:
        return owner
    if callable(ref):
        value = ref(state, frame, owner, answers)
        return value if value == () else int(value)
    return int(ref)


def _flow_actor(
    step: FlowStep, state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> int:
    """这一步由谁应答：``None`` = 发动者，``int`` = 该座位，可调用 = 由流答案派生。"""
    return _resolve_actor(step.actor, state, frame, owner, answers)


def _flow_option_id(frame: Any, key: str, index: int, *, multi: bool) -> str:
    """``SELECT_OPTION`` 的 ``skill_id``：既是步骤键、也是 ``ask_answers`` 的缓存键。

    单问步骤的 ``skill_id`` 就是 ``<短 id>:<key>``（投影 ``projection._step_requirement``
    读冒号后那段 ⇒ 直接命中 ``SkillDef.step_requirements``）；同一步问多次时补序号，
    既避免缓存撞车，也让两问可以各有各的文案（键形如 ``seat:0`` / ``seat:1``）。

    **短 id 的取法分两档**（票 08 的加法）：主动技的 ``SKILL`` 帧把全限定 id 写在
    ``frame.data["skill_id"]``；被动技**没有**这个键，派发环只写 ``running_skill``
    （见 :func:`~sgs.skills.catalog._dispatch`）。【流离】是第一条带座位步的被动技
    （【反馈】只有选牌步、不经过这里），若不认第二档就会在运行时 ``KeyError``。
    这里只读帧里的**既有键**，不新增协议字段——故记录流除新询问本身外不变。
    """
    raw = frame.data.get("skill_id") or frame.data.get("running_skill") or ""
    short = str(raw).rsplit(".", 1)[-1]
    return f"{short}:{key}:{index}" if multi else f"{short}:{key}"


def _zone_pool_size(state: GameState, seat: int, zones: tuple[Zone, ...]) -> int:
    """``zones`` 此刻对 ``seat`` 铺出的槽位数（``PickCards.hi=None`` 的解析真源）。"""
    return sum(len(state.zone(ZoneRef(zone, seat))) for zone in zones)


def zone_pool_size(state: GameState, seat: int, zones: tuple[Zone, ...]) -> int:
    """``zones`` 此刻对 ``seat`` 铺出的槽位数。

    唯一入口是 :meth:`GameState.zone`（与 :meth:`Engine.selection_face` 铺面读到的是同一
    份区域列表），故投影给前端的 ``spec.total`` 上限与引擎真实的选择面**同源**，不写死、
    不猜。``selection_face(..., reveal_hand=True)`` 下隐藏手牌也是逐张铺槽，故槽位数 =
    各区域张数之和。

    **公开**（票 09）：被动技的 ``available`` 判据要与成本步的选择面同源（【贯石斧】的
    "手牌 ∪ 装备区 ≥ 2"），而判据住在 :mod:`sgs.skills.catalog` —— 把这里的算法再抄一份
    就会有两个真源。``PickCards.hi=None`` 的解析也走它。
    """
    return _zone_pool_size(state, seat, zones)


def _filtered_zone_pool_size(
    state: GameState,
    seat: int,
    zones: tuple[Zone, ...],
    card_types: tuple[str, ...],
    slot_kinds: tuple[SlotKind, ...] = (),
) -> int:
    """``card_types`` / ``slot_kinds`` 过滤后，``zones`` 此刻对 ``seat`` 真能挑的牌数。

    只**读**区域列表（:meth:`GameState.zone`），不铺选择面 —— 故它可以在每次重入时安全
    地现算（铺面会消耗 ``select`` 子流的随机数，绝不能重复跑）。两条白名单的空元组 =
    不筛（同一份判据 :func:`_kept_by_whitelists` 与铺面过滤共用）。
    """
    return sum(
        1
        for zone in zones
        for card_id in state.zone(ZoneRef(zone, seat))
        if _kept_by_whitelists(state.card(card_id), card_types, slot_kinds)
    )


def _kept_by_whitelists(
    card: Any, card_types: tuple[str, ...], slot_kinds: tuple[SlotKind, ...]
) -> bool:
    """这张牌是否同时过**牌名**与**槽位种类**两条白名单（空元组 = 该维度不筛）。"""
    if card_types and card.type not in card_types:
        return False
    return not (slot_kinds and card.slot not in slot_kinds)


def _filter_card_types(
    slots: tuple[Slot, ...], card_ids: tuple[int, ...], card_types: tuple[str, ...]
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """按**牌名 id 白名单**过滤选择面（``PickCards.card_types`` 的唯一实现，票 09）。

    ``slots`` 与 ``card_ids`` 是**同一份选择面**的两个投影，必须同步过滤——只改一个，
    步骤读回的实体牌 id 就会错位（票 09 的【青龙偃月刀】是第一个消费者）。
    与 :meth:`Engine.ask_skill_activation` 里那段"只铺这些牌名"的过滤同形（同一判据、
    同一位置：``slot.card`` 的 ``type``）。
    """
    if not card_types:
        return slots, card_ids
    kept = [
        (slot, card_id)
        for slot, card_id in zip(slots, card_ids)
        if slot.card is not None and slot.card.type in card_types
    ]
    return (
        tuple(slot for slot, _ in kept),
        tuple(card_id for _, card_id in kept),
    )


def _filter_slot_kinds(
    slots: tuple[Slot, ...], card_ids: tuple[int, ...], slot_kinds: tuple[SlotKind, ...]
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """按**槽位种类白名单**过滤选择面（``PickCards.slot_kinds`` 的唯一实现，票 10）。

    与 :func:`_filter_card_types` 逐字同姿态：``slots`` / ``card_ids`` 同步过滤（否则步骤
    读回的牌会错位）。判据取 ``slot.card.slot`` —— 坐骑是六个**不同牌名**共用的槽位种类，
    牌名白名单表达不了"只铺坐骑"（【麒麟弓】是第一个消费者）。
    """
    if not slot_kinds:
        return slots, card_ids
    kept = [
        (slot, card_id)
        for slot, card_id in zip(slots, card_ids)
        if slot.card is not None and slot.card.slot in slot_kinds
    ]
    return (
        tuple(slot for slot, _ in kept),
        tuple(card_id for _, card_id in kept),
    )


def _filter_face(
    slots: tuple[Slot, ...],
    card_ids: tuple[int, ...],
    card_types: tuple[str, ...],
    slot_kinds: tuple[SlotKind, ...],
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """把两条白名单**依次**作用在同一份选择面上（票 09 的牌名 + 票 10 的槽位）。"""
    return _filter_slot_kinds(*_filter_card_types(slots, card_ids, card_types), slot_kinds)


def _flow_step_cards(
    host: Any, frame: Any, step: PickCards, owner: int, answers: FlowAnswers, timing: Timing
) -> tuple[str, object]:
    """跑一步选牌：返回 ``("stay", ())`` / ``("cancel", ())`` / ``("ok", picks)``。

    ``actor``（谁应答）与 ``face``（铺谁的区）在这里**第一次分开**（票 06 /【反间】）：
    缺省 ``face`` 跟随 ``actor``（自己选自己的牌）；【反间】把 ``face`` 指回发动者，
    于是目标看到的是**发动者的手牌**；【反馈】（票 07）应答者是持有者、``face`` 指向
    **伤害来源**。

    ``face`` 的派生回调可以返回**空元组**（票 07 §5）＝"这一步此刻根本不存在"：被动技的
    选取面几乎都要先读帧，而"读不到合法对象"必须**不发询问**（与 :class:`PickSeats` 的
    空池同一条纪律——没有候选的询问会让参与者 / AI 无从应答）。空元组与座位 ``0`` 不混同。

    ``card_types``（票 09）在铺面之后**再筛一次牌名**，筛完为空与空池同形（不发询问、
    走取消）；``hi=None`` 的上限按**过滤后**的池算（:func:`_filtered_zone_pool_size`），
    故"上限 = 引擎此刻真能挑的牌数"。``slot_kinds``（票 10）走同一条链路，只是判据换成
    ``slot.card.slot``（【麒麟弓】只铺目标的坐骑槽）。

    **铺面必须惰性**（``build_face`` 闭包）：``selection_face(reveal_hand=False)`` 铺隐藏
    槽时会消耗 ``select`` 子流的随机数，而 :meth:`Engine.ask_selection` 只在**第一次**
    调用它（帧内记忆）。这里若提前铺好，"每次重入消耗一次随机数"会让既有录像分叉。
    """
    actor = _flow_actor(step, host.state, frame, owner, answers)
    face = (
        actor
        if step.face is None
        else _resolve_face(step.face, host.state, frame, owner, answers)
    )
    if face == ():
        return "cancel", ()
    cache_key = step.cache_key if step.cache_key is not None else step.key
    if callable(cache_key):
        cache_key = cache_key(frame)
    if step.card_types or step.slot_kinds:
        hi = (
            step.hi
            if step.hi is not None
            else _filtered_zone_pool_size(
                host.state, face, step.zones, step.card_types, step.slot_kinds
            )
        )
        if hi < step.lo:
            # 与空池同形：一张能挑的都没有（或不够 ``lo``）⇒ 不发没有答案的询问；
            # 同时也挡住 ``(lo, hi) = (1, 0)`` 这种会让 `_select_cards` 的 range 为空的形状。
            return "cancel", ()
    else:
        hi = step.hi if step.hi is not None else _zone_pool_size(host.state, face, step.zones)
    picks = host.ask_selection(
        frame,
        key=cache_key,
        actor=actor,
        timing=timing,
        spec=SelectionSpec(zones=step.zones, total=(step.lo, hi), optional=step.optional),
        build_face=lambda: _filter_face(
            *host.selection_face(face, step.zones, reveal_hand=step.reveal_hand),
            step.card_types,
            step.slot_kinds,
        ),
    )
    if host.suspended(picks):
        return "stay", ()
    # 池为空（``ask_selection`` 直接返回空）与 ``Pass`` 同形：凑不够 ``lo`` 即取消。
    if len(picks) < step.lo:
        return "cancel", ()
    return "ok", tuple(int(card_id) for card_id in picks)


def provide_face(
    state: GameState, seat: int, type_id: str, zones: tuple[Zone, ...]
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """**"提供一张 B"的选择面**（票 14）：``(槽位, 引擎私有的位置 → 牌 id)``。

    两半合成，各读各的真源：

    - **实体 B**：``zones`` 此刻对 ``seat`` 铺出的面，按牌名 ``type_id`` 筛
      （与 :class:`PickCards` 的 ``card_types`` 同一条牌名过滤；这一步**永远**是
      "本人自己的区域" ⇒ 手牌是**可见槽**，与 ``reveal_hand=True`` 同形）；
    - **虚拟 B**：跑一趟 ``CANDIDATES`` 通道（``scope=RESPOND`` +
      ``respond_required=type_id``、``card=None``）——**与使用 / 打出窗口同一批回调**，
      故"哪些牌能当 B"由各技能自己的声明决定，本函数不写第二份谓词。
      **只收单来源**转化（``len(virtual_sources) == 1``）：多来源的【丈八蛇矛】是
      "先选两张"的另一维，票 14 不做。

    虚拟槽的 ``ref`` 指向**来源实体牌**所在的区（判定 / 装备区的红牌因此也点得到），
    牌面是那张虚拟 B（``card_id < 0``）。**只读状态、不抽随机数**：本函数因此也能当
    "付得起"的判据（【青龙】的 ``available`` 与这一步的候选面**同源**）。
    """
    slots: list[Slot] = []
    card_ids: list[int] = []
    for zone in zones:
        for card_id in state.zone(ZoneRef(zone, seat)):
            card = state.card(card_id)
            if card.type != type_id:
                continue
            slots.append(Slot(ref=ZoneRef(zone, seat), card=card))
            card_ids.append(card_id)
    seen = {int(card_id) for card_id in card_ids}
    for action in apply_modifiers(
        ModifierPoint.CANDIDATES,
        state,
        ModifierContext(
            subject=seat,
            card=None,
            scope=CandidateScope.RESPOND,
            respond_required=type_id,
        ),
        (),
    ):
        card = getattr(action, "card", None)
        if card is None or not card.is_virtual or card.type != type_id:
            continue
        sources = tuple(card.virtual_sources)
        if len(sources) != 1 or int(card.card_id) in seen:
            continue
        seen.add(int(card.card_id))
        slots.append(Slot(ref=locate(state, sources[0]), card=card))
        card_ids.append(int(card.card_id))
    return tuple(slots), tuple(card_ids)


def _flow_step_provide(
    host: Any, frame: Any, step: PickProvide, owner: int, answers: FlowAnswers, timing: Timing
) -> tuple[str, object]:
    """跑一步"提供一张 B"：返回三态 + ``tuple[CardRef, ...]``。

    面为空（或不够 ``lo``）与空池同形：**不发没有答案的询问**，走取消（票 19 §6）。
    答案从槽位回读**牌面**——虚拟 B 的负 id 进不了 :meth:`GameState.card`。

    **面按帧内键只构造一次**（与 :meth:`Engine.ask_selection` 的"铺面只构造一次"同一条
    纪律）：``effect`` 里的使用会挂起并让本 op **重新进入**，那时来源牌可能已经离区
    （被用掉的那张【杀】不在手里了）；重算出来的面会与**已缓存的答案**（面内位置）
    对不上——重入必须复用同一份面。
    """
    actor = _flow_actor(step, host.state, frame, owner, answers)
    face_key = f"provide_face:{step.key}"
    face = frame.data.get(face_key)
    if face is None:
        face = provide_face(host.state, actor, step.type_id, step.zones)
        frame.data[face_key] = face
    slots, card_ids = face
    if len(slots) < step.lo:
        return "cancel", ()
    refs = {
        int(slot.card.card_id): slot.card
        for slot in slots
        if slot.card is not None
    }
    picked = host.ask_selection(
        frame,
        key=step.key,
        actor=actor,
        timing=timing,
        spec=SelectionSpec(
            zones=step.zones,
            total=(step.lo, min(step.hi, len(slots))),
            optional=step.optional,
        ),
        build_face=lambda: (slots, card_ids),
        provide_type=step.type_id,
    )
    if host.suspended(picked):
        return "stay", ()
    if len(picked) < step.lo:
        return "cancel", ()
    return "ok", tuple(refs[int(card_id)] for card_id in picked)


def _flow_step_seats(
    host: Any, frame: Any, step: PickSeats, owner: int, answers: FlowAnswers, timing: Timing
) -> tuple[str, object]:
    """跑一步选座位（``lo..hi`` 次 ``SELECT_OPTION``）：返回三态 + 座位元组。"""
    actor = _flow_actor(step, host.state, frame, owner, answers)
    chosen: list[int] = []
    while len(chosen) < step.hi:
        pool = tuple(
            int(seat)
            for seat in step.pool(host.state, frame, owner, tuple(chosen))
        )
        if step.ordered:
            pool = tuple(seat for seat in pool if seat not in chosen)
        if not pool:
            break
        answer = host.ask_option(
            frame,
            actor,
            timing,
            skill_id=_flow_option_id(frame, step.key, len(chosen), multi=step.hi > 1),
            options=tuple(seat_choice(seat) for seat in pool),
            optional=step.optional,
        )
        if host.suspended(answer):
            return "stay", ()
        if isinstance(answer, Pass):
            break
        chosen.append(option_seat(answer))
    if len(chosen) < step.lo:
        return "cancel", ()
    return "ok", tuple(chosen)


def _flow_step_option(
    host: Any, frame: Any, step: PickOption, owner: int, answers: FlowAnswers, timing: Timing
) -> tuple[str, object]:
    """跑一步选项询问：返回三态 + 选项文本。"""
    actor = _flow_actor(step, host.state, frame, owner, answers)
    if not step.options:
        return "cancel", ""
    answer = host.ask_option(
        frame,
        actor,
        timing,
        skill_id=_flow_option_id(frame, step.key, 0, multi=False),
        options=step.options,
        optional=step.optional,
    )
    if host.suspended(answer):
        return "stay", ""
    if isinstance(answer, Pass):
        return "cancel", ""
    return "ok", answer.choice


_FLOW_STEP_RUNNERS: MappingProxyType[type, Callable[..., tuple[str, object]]] = (
    MappingProxyType(
        {
            PickCards: _flow_step_cards,
            PickSeats: _flow_step_seats,
            PickOption: _flow_step_option,
            PickProvide: _flow_step_provide,
        }
    )
)
"""步骤类型 → 它的执行器（**唯一登记处**）：加一种步骤只改这里与 :data:`FlowStep`，
不必在 :func:`run_flow` 的 if 级联里再找一遍（评审指出的"两处同步改"）。"""


class _Unset:
    """``run_flow(owner=…)`` 的**未指定**哨兵。

    ``owner`` 的合法取值是**座位号 ``int``**（没有哪个座位是 ``None``）。哨兵只用来把
    "未指定"与"某个座位"分开：未指定时读 ``frame.data["source"]``。显式传 ``None`` 是
    编程错误，会在 :func:`run_flow` 里的 ``int(...)`` 上立刻 ``TypeError`` —— 旧文档曾写
    "``owner=None`` 合法（应答者按步骤的 ``actor`` 定）"，但实现从不支持它（票 12 §2 收紧）。
    """


_UNSET = _Unset()


def run_flow(
    host: Any,
    frame: Any,
    flow: SkillFlow,
    *,
    owner: int | _Unset = _UNSET,
    timing: Timing = Timing.PHASE_PLAY_IDLE,
    on_commit: Callable[[], None] | None = None,
) -> Any:
    """执行一条 :class:`SkillFlow`。

    帧内**不需要额外游标**：每一步的询问都由 ``ask_answers`` 帧内缓存重入（票 01 的姿态），
    故重入时从头重跑一边步骤既不会发第二次询问、也不会重复副作用；``answers`` 每次重建，
    效果拿到的永远是"已经全部提交"的那一份。

    ``owner``（票 07）：发动者 —— 缺省（``_UNSET``）读 ``frame.data["source"]``（主动技的
    SKILL 帧）；**被动技**在钩子 / 接缝里没有"发动者"，由派发环显式传 ``instance.owner``。
    取值是**座位号 ``int``**；显式传 ``None`` 是编程错误（``int(None)`` 立刻炸），旧文档
    里"``None`` 合法"的说法已随票 12 §2 收紧删除。
    ``timing``（票 07）：每一步询问挂的时机 —— 缺省 ``PHASE_PLAY_IDLE``（主动技的 SKILL
    帧所处时机）；被动技传**派发环当前持有的时机**（【反馈】是 ``DAMAGE_AFTER_TAKE``），
    否则询问记录里的时机就与迁移前不一致。
    ``on_commit``（票 07）：所有步骤提交后、``effect`` 之前的回调 —— 主动技用它落
    ``_USED`` 位；被动技没有"阶段限一次"（每次触发都是新的帧）⇒ 传 ``None``。
    **它必须幂等**：``effect`` 挂起后的续跑会重新走到这里（票 12 §7；见 :class:`SkillFlow`）。

    返回 ``"stay"`` = 让出控制权（某步挂起）、``None`` = 走完（提交或取消）。
    """
    # ``int(owner)``：类型上是 no-op，但把"显式传 None"在**发问前**变成清晰的 TypeError
    # （票 12 §2 收紧后的契约；见 :class:`_Unset`）。
    seat = int(frame.data["source"]) if owner is _UNSET else int(owner)
    answers: dict[str, object] = {}
    for step in flow.steps:
        status, value = _FLOW_STEP_RUNNERS[type(step)](
            host, frame, step, seat, answers, timing
        )
        if status == "stay":
            return "stay"
        if status == "cancel":
            # 取消：一个副作用都不产生（提交点不落、``effect`` 不跑），技能可再发动。
            return None
        answers[step.key] = value
    if on_commit is not None:
        on_commit()
    return flow.effect(host, frame, answers)
