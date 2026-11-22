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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import product
from types import MappingProxyType
from typing import Any, Literal

from sgs.effects import (
    CandidateScope,
    ModifierContext,
    ModifierPoint,
    apply_modifiers,
    behavior_for,
    forbidden_as_slash,
    is_slash,
    locate,
    satisfies_required,
)
from sgs.resolution import Frame
from sgs.state import (
    CardRef,
    GameState,
    Pass,
    SelectionSpec,
    Slot,
    SlotKind,
    Step,
    StepPool,
    UseCard,
    Zone,
    ZoneRef,
    step_option_key,
)
from sgs.timing import Timing

__all__ = [
    "FREE_SPLIT",
    "SEAT_PREFIX",
    "ActorRef",
    "Arrange",
    "AskIdentity",
    "DeckTopArrange",
    "TwoRowArrange",
    "FixedSplit",
    "FlowAnswers",
    "FlowBranch",
    "FlowCancel",
    "FlowEffect",
    "FlowStep",
    "PickCards",
    "PickOption",
    "PickProvide",
    "PickSeats",
    "SeatPool",
    "SeatBound",
    "SkillFlow",
    "StepPreview",
    "activation_answers",
    "ask_key_of",
    "card_pool_size",
    "card_preactivations",
    "manifest",
    "option_seat",
    "preview_step",
    "provide_source_id",
    "run_flow",
    "seat_choice",
    "validate_flow_activation",
    "watched_cards",
    "zero_step_effect",
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
- :class:`PickOption` → ``str``（选中的选项文本，原样）；
- :class:`Arrange` → :class:`~sgs.state.ArrangeCards` **行动本身**（``order`` + ``split``；
  面内位置 → 实体牌的映射读 :func:`watched_cards`，不重复塞进答案）。
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

SeatBound = int | Callable[[GameState, Any, int, FlowAnswers], int]
"""``PickSeats`` 的 ``lo`` / ``hi``：定值，或 ``(state, frame, owner, 前序答案) -> 个数``。

可调用档（yijiang-2014-round2 票 18 / P9）服务"这一步要选几个**取决于前序答案**"的技能：
界【渐营】② 的第 3 步要选几个目标由**牌名**决定（0 个 / 1 个），而清单的 ``Step`` 只有
一个全局 ``min`` / ``max``。界面上"这一行不需要选"由 ``StepPool.max = 0`` 表达（该行
没有可选目标 ⇒ 自动跳过），执行器与校验器则逐行调用本档拿到真正的个数，故**界面不会
放宽规则**：0 个的牌名提交目标会被拒，1 个的牌名缺目标也会被拒。
"""

FlowEffect = Callable[[Any, Any, FlowAnswers], Any]
"""流水线效果：``(host, frame, answers) -> Control``；只在**所有步骤提交后**跑一次。"""

FlowCancel = Callable[[Any, Any], None]
"""步骤集合**取消**时的收尾：``(host, frame) -> None``（qunying 票 10 的加法）。

缺省（不声明）= 既有的"取消 = 零副作用、技能可再发动"；声明了它的流水线只把取消当成
**这一次事件**不成立（例：代价已付、效果事件的候选空池 ⇒ 关闭效果窗口，不回退代价）。
它**不是**第二个 ``effect``：不许在这里搬牌 / 发询问 / 记效果位——只许写簿记位。"""

AskIdentity = str | tuple[str, int]
"""一次询问的**询问位置**（票 22）：:meth:`Engine.ask_selection` 的 ``owner`` 参数。

两档形状，各自在调用侧机械给出：步骤流水线是 ``(flow.name, index)``（:func:`run_flow` 的
``enumerate`` 直接给；重入时从头重跑一遍步骤，序号跨重入天然稳定），手写回调是各调用点
自己的模块级字符串常量。它不是步骤名 :data:`FlowAnswers` 的键（那个是**询问名**：效果读
答案、前端取文案）——引擎把**位置**（本类型）与**名字**（``key``）拼成 ``(owner, key)``
一起分桶，故同一帧里两个不同的询问即便同名也不共用缓存槽。术语见 ``GLOSSARY.md`` 的
**询问标识**。
"""


@dataclass(frozen=True, slots=True)
class PickCards:
    """选牌步骤：在 ``zones`` 上问一次 ``SELECT_CARDS``（``lo..hi`` 张）。

    ``key`` 是**效果读取答案**的键（同一条流水线里唯一）；``optional=True`` 给一条
    ``Pass`` = 取消整条流水线。``cache_key`` 是帧内 ``ask_selection`` 的**询问键**，
    缺省 = ``key``；它可以是 ``str``，也可以是 ``(frame) -> str``（票 07）——**同一次伤害
    事件按伤害值逐轮派发**时，第 2 点的缓存键必须与第 1 点不同，否则第二次会命中第一次
    留下的缓存答案（【反馈】的 ``fankui_step_key``）。两件事分开，是因为"这一步叫什么"
    与"这一次问的答案存哪儿"在逐点派发里不再是一回事。

    **跨步骤的隔离不靠 ``cache_key``**（票 22）：同一个 frame 里两个**不同步骤**的询问缓存
    由**询问位置** ``(flow.name, index)`` 分开，故它们即便共用 ``cache_key`` 也不会串答案；
    逐点 / 逐轮的"同一步问多次"仍靠 ``cache_key``（同一步的位置不变）。

    ``hi=None`` = **"全部可选牌"**（票 04）：执行器在发问前把它解析成**过滤后**选择面的
    槽位数（:func:`zone_pool_size` 给出 ``zones`` 此刻对 ``face`` 铺出的槽位，再按
    ``card_types`` 筛），故 ``spec.total`` 里进的是**真实上限**，前端看到的选择面与引擎
    的候选面同源。【制衡】的"任意张"正是这个形状；写死一个大哨兵值（如 999）虽被
    ``_select_cards`` 夹住、功能上安全，但会把一个不存在的上限投影给前端。

    ``card_types``（票 09 的加法）：**牌名 id 白名单**，空元组 = 不筛。票 09 把成本从
    发动询问的候选里搬到这里之后，"只挑【杀】"这件事必须跟着成本一起下沉；那条旧候选
    路径（`ActivationPlan.card_types`）已在 W6-06a 删除，于是**步骤层这一份是唯一真源**。
    过滤后池为空 ⇒ 与 ``face`` 返回空元组同形：**不发没有候选的询问**，走取消。
    ``hi=None`` 的上限由 :func:`_filtered_zone_pool_size` 按**过滤后**的池重算（与
    :meth:`Engine.ask_selection` 收到的同一份槽位），故"上限"与"选择面"仍同源。

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

    ``exclude_cards``（W4-fix-3）：**实体牌 id 黑名单**，从同一份选择面里排除（同步作用在
    ``slots`` / ``card_ids``、``hi=None`` 的上限、清单的候选与校验器上）。可调用档收
    ``(state, frame, owner, answers)`` —— 【贯石斧】据此排除**这张技能对应的装备牌自己**
    （用户裁定，偏离官方 FAQ）：身份取 ``SkillInstance.source.card_id`` 的实例解析结果，
    不写死牌名。

    """

    zones: tuple[Zone, ...]
    lo: int
    hi: int | None
    optional: bool = True
    key: str = "card"
    actor: ActorRef = None
    face: ActorRef = None
    seats: (
        tuple[int, ...]
        | Callable[[GameState, Any, int, FlowAnswers], tuple[int, ...]]
        | None
    ) = None
    """**跨座位公开区的面**（qunying 票 03b 的加法；缺省 ``None`` = 老口径"只铺 ``face``"）。

    非 ``None`` 时这一步铺的是"``seats`` 里的每个座位 × ``zones``"的乘积（03a 的
    ``Engine.selection_face(..., seats=...)``），选牌面里每个槽的 ``ref.seat`` 是它自己那一座
    的真实座位。可调用档收 ``(state, frame, owner, answers)``——与 ``face`` 同一条求值纪律。

    **四个读取方读同一份解析**（:func:`_step_seat_values`）：铺面 / 执行器与取消判据、
    ``hi=None`` 的上限（:func:`_filtered_zone_pool_size`）、清单候选（:func:`_card_options`）、
    校验器与预览。故"清单列出来的牌"与"引擎收得下的牌"不可能各说一套。
    空元组 = 这一步此刻没有可铺的座位（与 ``face == ()`` 同形：不发询问、走取消）。
    """
    reveal_hand: bool = True
    cache_key: str | Callable[[Any], str] | None = None
    card_types: tuple[str, ...] = ()
    slot_kinds: tuple[SlotKind, ...] = ()
    suits: tuple[str, ...] = ()
    """**花色白名单**（sp8-liuyan 票 04 的加法；空元组 = 不筛）。

    与 ``card_types``（牌**名**）同一条链路：同一份选择面、同步过滤 ``slots`` / ``card_ids``、
    ``hi=None`` 的上限按过滤后的池重算，**清单 / 校验器 / 预览 / 执行器读同一份**
    :func:`_kept_by_filters`。它服务的是"只铺某种花色的牌"的技能 —— 刘焉【立牧】①
    与界大乔【国色】①「一张方块牌」：牌名白名单表达不了花色，而槽位种类也不够。

    取值 = 后端的 ``Suit`` 值（``diamond`` / ``heart`` / ``spade`` / ``club``）。
    """
    exclude_cards: (
        tuple[int, ...]
        | Callable[[GameState, Any, int, FlowAnswers], tuple[int, ...]]
    ) = ()
    hero_zone: str | Callable[[GameState, Any, int, FlowAnswers], str] | None = None
    """**武将牌上的牌**的区键（huo-shenzhugeliang 票 02 的加法；缺省 ``None`` = 不选它）。

    ``Zone.HERO`` 是唯一需要"技能实例"这第二维的区域（``GameState.zone`` 缺它直接
    fail-closed，见 ``src/sgs/state/models.py``：「星」与【谦逊】是两个互不相干的区）。
    ``PickCards.zones`` 只带 ``Zone`` 枚举维，故用本字段补那一维：非 ``None`` 时这一步的
    唯一区域 = ``ZoneRef(Zone.HERO, face, <本字段>)``（``face`` = 铺面的那个座位），
    ``zones`` 只作为"这是个区域步"的声明（传 ``(Zone.HERO,)``）。

    可调用档收 ``(state, frame, owner, answers)``：区键与应答者 / 前序答案有关时用它。
    """

    def __post_init__(self) -> None:
        """``seats``（跨座位公开区）与 ``hero_zone``（武将牌上的牌）互斥：两种面不能叠。"""
        if self.seats is not None and self.hero_zone is not None:
            raise ValueError(
                "PickCards 的 seats（跨座位公开区）与 hero_zone（武将牌上的牌）互斥："
                "前者按座位铺 zones，后者把整面换成一个带实例键的区"
            )


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

    ``cache_key``（票 21）与 :class:`PickCards` 的**同名参数**同义：帧内 ``ask_selection``
    的询问名，缺省 = ``key``。它与 ``key`` 分开是因为"这一步叫什么"（效果读答案、前端取
    文案）与"这一次问的答案存哪儿"不是一回事。**票 21 曾用它来躲跨步骤撞车**（同一帧里
    【流离】成本步 vs 本步共用缺省 ``key="card"`` ⇒ 后者命中前者的答案、``refs`` 里没有 ⇒
    ``KeyError``）；**票 22 起跨步骤的隔离改由询问位置 ``(flow.name, index)`` 承担**，
    ``cache_key`` 只再管"同一步问多次"（逐点 / 逐轮，此时位置不变）。
    """

    type_id: str
    zones: tuple[Zone, ...] = (Zone.HAND,)
    lo: int = 1
    hi: int = 1
    optional: bool = False
    key: str = "card"
    cache_key: str | Callable[[Any], str] | None = None
    actor: ActorRef = None


@dataclass(frozen=True, slots=True)
class PickSeats:
    """选座位步骤：逐次问 ``SELECT_OPTION``（选项文本 ``seat:N``），共 ``lo..hi`` 次。

    ``ordered=True`` 时依次排除本步已选的座位（"顺序即语义"的通用形态；先后两问的问法
    由 ``step_requirements`` 分别给）。``pool`` 返回空池 ⇒ 本步提前收尾：凑够 ``lo`` 就
    提交，不够就取消（**不**发出没有候选的询问 —— 票 01 的 AI 崩溃就是这么来的）。

    ``key`` 是效果读取答案的键；同一步问多次时询问的 ``skill_id`` / 缓存键补序号
    （``seat:0`` / ``seat:1``），故效果读到的永远是 ``answers["seat"]`` = 座位元组。

    ``lo`` / ``hi`` 可以是 :data:`SeatBound` 的**可调用档**（yijiang-2014-round2 票 18）：
    按**前序答案**现算"这一步要选几个"。**可调用档**在三个读取方（执行器
    :func:`_flow_step_seats` / 校验器 :func:`_assign_activation` / 清单 :func:`manifest`）
    都走 :func:`_seat_bounds` 这一份解析，故"清单说不用选"与"校验器要求选"不可能各说一套；
    **定值档**不经过它——执行器与清单直接读字段（清单的 ``Step.min`` / ``Step.max`` 就是
    ``lo`` / ``hi`` 原样），校验器的 :func:`_seat_bounds` 对定值档原样返回（既有技能的
    清单与裁决逐字节不变）。
    **可调用档的步必须住在流水线末尾**：清单的逐行渲染要拿"前一步的代表答案"当键，
    把"按前序答案变个数"的步排在别人前面时，前序上下文抽样没有它的答案可用（
    :func:`_sampled_answers` 只读定值档）。
    """

    lo: SeatBound
    hi: SeatBound
    pool: SeatPool
    ordered: bool = False
    optional: bool = True
    key: str = "seat"
    actor: ActorRef = None


OptionChoices = (
    tuple[str, ...] | Callable[[GameState, Any, int, "FlowAnswers"], tuple[str, ...]]
)
"""选项步的闭集：定值元组，或 ``(state, frame, owner, answers) -> tuple[str, ...]``。

可调用档（票 23 的加法）服务"可选哪几项取决于**前序答案**"的技能：界【结姻】的
「弃置一张手牌 / 将一张装备牌置入其装备区」两选一，第二步可不可行取决于**第一步选中的
那名男性角色**的装备区空槽（牌面定了"置入"不能替换已有装备）。与 :attr:`PickCards.face`
同一姿态：渲染清单、铺菜单、校验复合动作三处都读同一份解析结果。
"""


@dataclass(frozen=True, slots=True)
class PickOption:
    """选项步骤：问一次 ``SELECT_OPTION``（闭集 ``options``，选中文本原样进答案）。

    ``revealed``（韬·刘备【义起】的加法）：把本次询问**授予应答者**的牌面一起下发
    （:meth:`Engine.ask_option` 的 ``revealed``）。投影按"``SELECT_OPTION`` ∧ ``revealed``
    非空"这个**形状**开选择型弹窗：牌面画在弹窗里（只读），选项按钮也画在同一弹窗。
    可调用档收 ``(state, frame, owner, answers)``，发问前现算——【义起】"观看弃牌堆顶 x 张
    再选一种类型"因此是**一问**：先看到牌，再点一个类型（不用分两步、也填不进预发动清单）。

    ``disabled``（票 04 §3）：``options`` 里**显示但不可选**的那些（与 ``options`` 同形态，
    可调用档收同样的四个参数，按**前序答案**现算）。"列出名字"与"这个能选"是两件事：
    【蛊惑】的牌名弹窗要列全牌堆名（用户 Q1A），而"此刻没有合法目标"的名字只能置灰。
    置灰**不是界面纪律**：`manifest` 把它下发进 ``StepPool.disabled``，
    :func:`_assign_activation` 的逐步校验**拒绝**其中的项（fail-closed），故不点界面也提交不了。
    """

    options: OptionChoices
    actor: ActorRef = None
    optional: bool = True
    key: str = "option"
    revealed: (
        tuple[CardRef, ...]
        | Callable[[GameState, Frame, int, FlowAnswers], tuple[CardRef, ...]]
    ) = ()
    disabled: OptionChoices = ()
    show_disabled: bool = False
    """**逐步询问**里也把置灰项列出来（缺省 ``False`` = 既有四条的收窄行为）。

    ``disabled`` 的语义在三个读取方是一致的："在闭集里、但此刻不可选"。清单
    （``StepPool.disabled``）与复合动作校验器一直是"列出来、画灰、拒收"；**逐步询问**这一
    条过去会把置灰项从 ``options`` 里删掉（"点了必被拒的候选不该出现"）。两档都有消费者，
    故做成声明：缺省保持旧形状（既有技能的询问载荷逐字节不变，四个消费者 = 蛊惑 / 渐营 /
    单骑 / 凤魄），``True`` 则走 ``ask_option(disabled=…)`` 的置灰通道（选项全列、只从
    ``legal_actions`` 剔除）——首个消费者 =【谦逊】效果② 的目的地（用户第二轮裁定
    「不能指定为合法目标，**直接置灰**」）。
    """


ArrangeCount = int | Callable[[GameState, Frame], int]
"""牌堆顶排列的张数：定数，或 ``(state, frame) -> int``（观星 = ``min(存活数, 5)``）。"""

FREE_SPLIT: Literal["free"] = "free"
"""**自由切分**（``DeckTopArrange.split`` 的缺省档）：置顶张数由玩家在 ``0..count`` 里选。"""


@dataclass(frozen=True, slots=True)
class FixedSplit:
    """**固定切分**：``top`` 张置于牌堆顶、``bottom`` 张置于牌堆底（``DeckTopArrange.split``）。

    两个半区都**有读取方**（W6-07 / 评审 J6）：``top`` 是候选生成的唯一合法切分点
    （``_arrange`` 的置顶张数），``bottom`` 由 :meth:`DeckTopArrange.split_point` 与接入层
    投影用来核对"两行恰好排完这几张牌"——它不是一个只写不读的字段。
    """

    top: int
    bottom: int


ArrangeSplit = Literal["free"] | FixedSplit
"""``DeckTopArrange.split`` 的两档：:data:`FREE_SPLIT` 或 :class:`FixedSplit`。"""


@dataclass(frozen=True, slots=True)
class DeckTopArrange:
    """**牌堆顶排列**的可复用规格（W6-02；观星是第一个消费者）。

    只声明两件事（`GLOSSARY.md`「牌堆顶排列」）：

    - ``count``：本次排列几张（``int`` 或 ``(state, frame) -> int``）。
    - ``split``：切分方式 —— :data:`FREE_SPLIT`（全部切分点都合法，观星）或
      :class:`FixedSplit`（只合法一个切分点，候选生成按它收口）。

    **同一个对象**同时被 ``Arrange`` 步与 ``SkillDef.deck_arrange`` 引用（一份声明，不是
    两份拷贝）：执行器读它求张数 / 切分，接入层读后者做投影。它**不进记录流**——记录流里
    只有 ``ARRANGE`` 询问与 ``ArrangeCards`` 答案，故既有基准不受影响。

    没有 ``destination`` 字段：本期只有牌堆顶 / 牌堆底两个去处，没有读取方的配置不落
    （设计纪律；spec S3）。
    """

    count: ArrangeCount
    split: ArrangeSplit = FREE_SPLIT

    def __post_init__(self) -> None:
        """定数张数就在**声明期**核对固定切分拼得成一次排列（W6-07 / 评审 J6）。

        ``FixedSplit(2, 2)`` 配 ``count = 5`` 是**写错了声明**，不是运行期状态：在构造期
        炸掉，比等到某一帧的候选生成发现"切分点越界"更早、也更接近错误来源。可调用张数
        在这里读不到（没有局面），交给 :meth:`split_point` 在实际张数已知时核对。
        """
        if isinstance(self.count, int):
            self._check_fixed(self.count)

    def _check_fixed(self, count: int) -> None:
        """固定切分的两个半区必须**恰好**拼成 ``count``（否则是声明错误，不是规则状态）。"""
        if self.split == FREE_SPLIT:
            return
        total = self.split.top + self.split.bottom
        if total != count:
            raise ValueError(
                f"DeckTopArrange 的固定切分 top={self.split.top} + bottom={self.split.bottom}"
                f" = {total} 与张数 {count} 不一致（声明写错了：两行必须恰好排完这几张牌）"
            )

    def split_point(self, count: int) -> int | None:
        """候选生成用的置顶张数：``free`` ⇒ ``None``（全部切分），``fixed`` ⇒ ``top``。

        ``count`` 是**本次实际张数**（可调用档在这里才求值完）。``fixed`` 档只要
        ``top + bottom != count`` 就抛 ``ValueError``：那是声明与实际张数对不上
        （评审 J6），必须在候选生成**之前**可见 —— 不能拖到 ``_arrange`` 的越界检查才
        成为第一个症状。``FixedSplit.bottom`` 在这里被读取，故它不是死字段。
        """
        if self.split == FREE_SPLIT:
            return None
        self._check_fixed(count)
        return self.split.top


@dataclass(frozen=True, slots=True)
class TwoRowArrange:
    """**两行交换**的排列规格（huo-shenzhugeliang 票 02；首个载体 = 神诸葛亮【七星】）。

    与 :class:`DeckTopArrange` 的差别只有三处：

    - 牌**不是**从牌堆顶看来的：``cards`` 自己回答这一面摆哪些牌（七星 = 武将牌上的
      「星」＋手牌，两者对宿主都是可见事实）；
    - 两行是**两个具名行**（牌面之外的事实），切分点由 ``top`` 现算 —— 七星取"交换前的
      星数"，于是**上排张数恒定** ⇒ 上下交换必然等量（"替换等量的星"由规格保证，不靠
      技能代码再校验一遍）；
    - ``enumerate_candidates=False``：候选**不枚举**（答案空间是 `C(总张数, 星数)`，最坏
      19,448 条），只给一条恒等兜底；界面自己构造答案，服务端只做结构校验
      （:meth:`Engine.ask_arrange` 的 ``ArrangeCards`` 合法性检查）。
      ``reorder=False``：界面上**只能上下交换、行内不可调序**（用户 2026-11-04 Q4：
      手牌顺序与星顺序都没有影响）。
    """

    count: ArrangeCount
    top: ArrangeCount
    cards: Callable[[GameState, Any], tuple[CardRef, ...]]
    top_label: str
    bottom_label: str
    enumerate_candidates: bool = False
    reorder: bool = False
    starts_on_top: bool = False
    """初始摆放：`True` = **全部牌先放在上排、下排只画空槽**（lei-batch 票 03 的加法）。

    缺省 `False` = 按 ``top`` / ``bottom`` 先把两行铺开（【七星】的姿态：上行本来就是「星」）。
    罪论要的是"上排三张、下排 X 个空槽、必须自己把 X 张移下去"，故声明 ``True``。
    """

    def count_of(self, state: GameState, frame: Any) -> int:
        """本次面内张数（可调用档在这里求值）。"""
        value = self.count
        return int(value(state, frame) if callable(value) else value)

    def top_of(self, state: GameState, frame: Any) -> int:
        """上排（星）张数 —— 也是本次 ``ArrangeCards.split``。"""
        value = self.top
        return int(value(state, frame) if callable(value) else value)

    def cards_of(self, state: GameState, frame: Any) -> tuple[CardRef, ...]:
        """这一面要摆的牌（**顺序即两行的初始顺序**：先上排、后下排）。"""
        return tuple(self.cards(state, frame))

    def split_point(self, count: int) -> int | None:
        """``DeckTopArrange`` 的同一接口：两行规格恒定切成 ``top`` 张牌。"""
        return None


@dataclass(frozen=True, slots=True)
class Arrange:
    """排列步骤（W6-02）：看牌堆顶 ``spec.count`` 张，问一次 ``ARRANGE``，答案原样进 ``answers``。

    答案 = :class:`~sgs.state.ArrangeCards` **行动本身**（``order`` + ``split``），不是位置
    元组：排列是"顺序 + 切分"的一份决策，拆成牌 id 序列会丢掉切分点。位置 → 实体牌由
    效果读 :func:`watched_cards`（帧私有 peek 缓存）。

    ``key`` 是效果读答案的键（同一条流水线里唯一）。``actor`` 与其它步骤**同义**
    （``None`` = 发动者，``int`` = 该座位，可调用 = 由前序答案派生），读它的地方是
    :func:`_flow_step_arrange` 的 ``_flow_actor`` —— 排列的应答者因此可以不是发动者，
    与选牌 / 选座位步同一条通道（W6-07 评审 HARD 2 修的就是这半句旧说法）。

    **它不进清单、也不进复合动作**：``manifest`` 对含 ``Arrange`` 的流水线**整条**返回空
    元组（排列的候选是"全部排列 × 切分点"，``Step.pool`` 表达不了），且那个分支在
    ``_flow_actor`` 之前就返回 —— 故"``manifest`` 会读 ``step.actor`` 所以字段必须在场"的
    旧说法是错的；``_assign_activation`` 也在 ``Arrange`` 处 ``break``（复合动作的
    ``picks`` / ``choices`` 装不下 ``ArrangeCards``）。两处都走不到本步，不是因为本步缺席。

    **重入**：``run_flow`` 挂起后从头重跑步骤，故这一步看的牌存在帧私有缓存里
    （:data:`ARRANGE_CARDS_KEY`，键 = ``(flow.name, 步骤序号)``），第二次跑读缓存、不再调
    ``Engine.peek_deck``。答案则由 ``ask_arrange`` 的帧内缓存命中，**它的键不是询问位置**
    —— 是 ``("arrange", actor, timing, 牌 id 元组)``（``Engine.ask_arrange``）。两者键形状
    不同是既有事实，不要按"都是询问位置"去读。
    """

    key: str
    spec: DeckTopArrange | TwoRowArrange
    actor: ActorRef = None


@dataclass(frozen=True, slots=True)
class FlowBranch:
    """**按前序选项分叉的步骤**（qunying 票 03b）：``key`` 指的那一步答了哪个选项，就展开
    哪个分支的**子步骤**，当作后续步骤继续跑。

    它是**步骤的组合子**，不是第六种原语：子步骤仍是既有的 ``PickCards`` / ``PickOption``
    等类型，各处的读取方式（面 / 池 / 文案 / 校验）一条不改；本类型只回答"后面还有哪些
    步骤"。故它在 :data:`_FLOW_STEP_RUNNERS` 里登记的是**组合子执行器**
    （:func:`_flow_step_branch`：只回一段子步骤，由 :func:`run_flow` 就地展开），不是叶子
    步骤的三态执行器（展开规则见 :func:`_branch_steps`，执行器 / 校验器 / 预览读同一份）。

    ``branches``：``选项文本 → 子步骤元组``。空元组 = 这个选项后面没有步骤（不是"取消"）。
    没列出的选项同样 = 没有步骤。子步骤的类型是**闭集**：``PickCards`` / ``PickOption``
    （首个消费者 =【谦逊】效果②的"选牌 → 目的地"）；要加别的类型时，清单渲染
    （:func:`manifest`）、校验器（:func:`_assign_activation`）与预览（:func:`preview_step`）
    三处要一起加，否则构造期就抛（见 :meth:`SkillFlow.__post_init__`）。

    **必须是流水线的最后一步**（构造期核）：子步骤占据有效序列的**尾部**，于是询问位置
    ``(flow.name, index)``（``index`` = 展开后的序号）与清单的步骤次序都唯一、重入稳定。
    要"分叉后再回到公共步骤"时再给位置加上第二维，别在消费者里各猜一套。

    子步骤的 ``key`` 与顶层步**共用 ``answers`` 字典**，故整条流水线（含所有分支）的
    ``key`` 必须两两不同（:meth:`SkillFlow.__post_init__` 核）。
    """

    key: str
    branches: Mapping[str, tuple[FlowStep, ...]]

    def __post_init__(self) -> None:
        """声明错误的形状校验：非空 key、非空分支表、子步不含 ``FlowBranch``（不支持嵌套）。"""
        if not self.key:
            raise ValueError("FlowBranch.key 是前序步骤的答案键，必须非空")
        if not self.branches:
            raise ValueError(f"FlowBranch({self.key!r}) 的分支表不能为空")
        for option, substeps in self.branches.items():
            if not isinstance(option, str) or not option:
                raise ValueError(f"FlowBranch({self.key!r}) 的选项文本必须是非空字符串：{option!r}")
            if not isinstance(substeps, tuple):
                raise ValueError(f"FlowBranch({self.key!r}) 的分支 {option!r} 必须是步骤元组")
            for step in substeps:
                if isinstance(step, FlowBranch):
                    raise ValueError(
                        f"FlowBranch({self.key!r}) 不支持**嵌套**分支：分支 {option!r} 里又有 "
                        "FlowBranch。今天的分支是有效序列的尾部，嵌套会让询问位置的派生失去唯一性"
                    )


FlowStep = PickCards | PickSeats | PickOption | PickProvide | Arrange | FlowBranch
"""流水线步骤的闭集（qunying 票 03b 起含组合子 :class:`FlowBranch`）。"""

_BRANCH_SUBSTEP_TYPES: tuple[type, ...] = (PickCards, PickOption)
"""分支子步**已登记**的类型（清单 / 校验器 / 预览三处都实现了的那几种）。

加类型时三处一起加：清单的逐分支行渲染、``_assign_activation`` 的摊回、预览的候选。
未登记的类型在 :meth:`SkillFlow.__post_init__` 就抛 —— 不许"清单里没有、校验器却要求"。"""


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

    **W6-05**：W6 复核维持手写；重估条件 = **出现真正同形的第四个消费者**（三条的轮次
    形状仍不同源，见上）。在那之前不为这里加循环 / 每轮提交步骤。

    **``name``（票 22）**：这条流水线的**稳定短名**，与步骤序号一起构成每个步骤的
    **询问位置** ``(name, index)``。它必须逐流程唯一（``tests/test_ask_points.py`` 机械核），
    否则同一个帧里两条流程的同一位置会共用询问缓存。名字**不进记录流**，故既有录像不受
    影响。
    """

    name: str
    steps: tuple[FlowStep, ...]
    effect: FlowEffect
    once_per_phase: bool = True
    on_cancel: FlowCancel | None = None
    """步骤集合取消时的**收尾**（qunying 票 10；缺省 ``None`` = 零副作用）。

    ``run_flow`` 的三态里 ``"cancel"`` 原本恒等于"这次发动没有发生"（不落位、技能可再
    发动）。一个**事件**的流水线如果需要"取消只作废这个事件、不回退它之前已经结算的事件"
    （首个消费者 =【谦逊】的效果事件：代价已由上一个事件付清），把收尾写在这里。收尾只许
    写簿记位（关闭窗口 / 记消耗），**不**搬牌、**不**发询问、**不**记效果位。
    """

    def __post_init__(self) -> None:
        """**同一流程内步骤 ``key`` 两两不同**（票 22）：重复即流程定义写错。

        ``run_flow`` 把答案按 ``answers[step.key]`` 回写给 ``effect``，故同流程内重名必然
        互相覆盖 —— 它不会撞询问缓存（位置含步骤序号），但会让效果读到别步的答案，
        属"写错了要在构造期炸掉"的一类，不是运行期可恢复的状态。

        **分支的三条声明纪律**（qunying 票 03b）：``FlowBranch`` 必须是**最后一步**（子步
        占据有效序列的尾部，询问位置 ``(name, index)`` 因此唯一）；它的 ``key`` 必须指回
        **更早的 ``PickOption`` 步**（否则"按前序答案解析分支"无处可读）；子步的 ``key``
        与顶层步**共用** ``answers`` 字典，故一起参与去重。
        """
        if not self.name:
            raise ValueError("SkillFlow.name 是询问位置的一部分，必须非空")
        leaves: list[FlowStep] = []
        for step in self.steps:
            if isinstance(step, FlowBranch):
                # 组合子的 ``key`` **就是**前序 PickOption 的键（它自己不写答案），不参与去重。
                for substeps in step.branches.values():
                    leaves.extend(substeps)
                continue
            leaves.append(step)
        seen: set[str] = set()
        duplicates: list[str] = []
        branch_at = [
            index for index, step in enumerate(self.steps) if isinstance(step, FlowBranch)
        ]
        for entry in leaves:
            if entry.key in seen and entry.key not in duplicates:
                duplicates.append(entry.key)
            seen.add(entry.key)
        if duplicates:
            raise ValueError(
                f"SkillFlow {self.name!r} 的步骤 key 必须两两不同，重复：{sorted(duplicates)}"
            )
        for index in branch_at:
            if index != len(self.steps) - 1:
                raise ValueError(
                    f"SkillFlow {self.name!r} 的 FlowBranch 必须是**最后一步**（第 {index} 步）："
                    "分支子步占据有效序列的尾部，后面还有顶层步会让询问位置 / 清单次序重叠"
                )
            branch = self.steps[index]
            assert isinstance(branch, FlowBranch)
            prior = [
                step
                for step in self.steps[:index]
                if isinstance(step, PickOption)
            ]
            if not any(step.key == branch.key for step in prior):
                raise ValueError(
                    f"SkillFlow {self.name!r} 的 FlowBranch.key={branch.key!r} 必须指回"
                    "**更早的 PickOption 步**（分支按它的答案展开）"
                )
            for substeps in branch.branches.values():
                for step in substeps:
                    if not isinstance(step, _BRANCH_SUBSTEP_TYPES):
                        raise ValueError(
                            f"SkillFlow {self.name!r} 的分支子步 {type(step).__name__} 尚未登记："
                            f"清单 / 校验器 / 预览只实现了 {[t.__name__ for t in _BRANCH_SUBSTEP_TYPES]}"
                        )


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


def _resolve_options(
    choices: OptionChoices, state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """把 ``PickOption.options`` 解析成**此刻的闭集**（可调用档按前序答案现算）。"""
    if callable(choices):
        return tuple(choices(state, frame, owner, answers))
    return tuple(choices)


def _flow_actor(
    step: FlowStep, state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> int:
    """这一步由谁应答：``None`` = 发动者，``int`` = 该座位，可调用 = 由流答案派生。"""
    return _resolve_actor(step.actor, state, frame, owner, answers)


def _branch_steps(step: FlowBranch, answers: FlowAnswers) -> tuple[FlowStep, ...]:
    """``FlowBranch`` 按**前序答案**展开出的子步骤（**唯一展开规则**）。

    消费者有三处，读的都是这一份：

    - 执行器 :func:`run_flow`（经由登记表里的 :func:`_flow_step_branch`）——把子步接在
      待跑序列的当前位置；
    - 复合动作校验器 :func:`_assign_activation`——只在这条分支被选中时摊回它的答案；
    - 清单 / 预览 :func:`manifest` / :func:`preview_step`——按 ``StepPool.when`` 给行、按
      已选答案跳过别的分支。

    前序答案还没到（或不是文本）⇒ 空元组 = 没有步骤（**不是**取消整条流水线：取消只由
    叶子步骤的三态表达）。
    """
    chosen = answers.get(step.key)
    if not isinstance(chosen, str):
        return ()
    return tuple(step.branches.get(chosen, ()))


def _branch_membership(flow: SkillFlow) -> dict[str, tuple[str, str]]:
    """分支子步的 ``key`` → ``(分支键, 选项文本)``（顶层步不在表里）。

    预览用它判断"清单里的这一步在**当前已选答案**下存不存在"：分支键的答案不是这个选项
    ⇒ 这一步此刻不存在（票面 §2 的"指错分支按不存在处理"）。
    """
    membership: dict[str, tuple[str, str]] = {}
    for step in flow.steps:
        if not isinstance(step, FlowBranch):
            continue
        for option, substeps in step.branches.items():
            for sub in substeps:
                membership[sub.key] = (step.key, option)
    return membership


def ask_key_of(step: PickCards | PickProvide, frame: Any = None) -> str:
    """这一步的**询问名**：``cache_key`` 优先，缺省 = 步骤 ``key``（票 22）。

    唯一实现（原先在选牌步与提供步各抄了一份）：``cache_key`` 是同一步问多次（逐点 /
    逐轮）时的**名字**那一半；可调用档要用 ``frame`` 求值（【反馈】的 ``fankui_step_key``），
    静态消费方（机械闸）给 ``frame=None`` 时只取得到字符串档，故这里对可调用档 fail-closed
    地回落到步骤名。
    """
    cache_key = step.cache_key
    if cache_key is None:
        return step.key
    if callable(cache_key):
        return step.key if frame is None else str(cache_key(frame))
    return cache_key


def _flow_option_id(frame: Any, key: str, index: int, *, multi: bool) -> str:
    """``SELECT_OPTION`` 的 ``skill_id``：既是步骤键、也是 ``ask_answers`` 的缓存键。

    单问步骤的 ``skill_id`` 就是 ``<短 id>:<key>``（投影 ``projection._step_requirement``
    读冒号后那段 ⇒ 直接命中 ``SkillDef.step_requirements``）；同一步问多次时补序号，
    既避免缓存撞车，也让两问可以各有各的文案（键形如 ``seat:0`` / ``seat:1``）。

    **形状只有一处**：拼法住 :func:`~sgs.state.step_option_key`（``SELECT_OPTION`` 询问上的
    ``skill_id`` 是协议字段，AI 侧的分组表也按它比对，故不是流水线的私有知识）。

    **短 id 的取法分两档**（票 08 的加法）：主动技的 ``SKILL`` 帧把全限定 id 写在
    ``frame.data["skill_id"]``；被动技**没有**这个键，派发环只写 ``running_skill``
    （见 :func:`~sgs.skills.catalog._dispatch`）。【流离】是第一条带座位步的被动技
    （【反馈】只有选牌步、不经过这里），若不认第二档就会在运行时 ``KeyError``。
    这里只读帧里的**既有键**，不新增协议字段——故记录流除新询问本身外不变。
    """
    raw = frame.data.get("skill_id") or frame.data.get("running_skill") or ""
    return step_option_key(raw, key, index if multi else None)


def _zone_cards(state: GameState, ref: Any) -> list[int]:
    """坐标 → 区里的牌；**武将牌上的区还没建**时按空区读（不是编程错误）。

    `GameState.zone` 对"区不存在"是 fail-closed 的（目的地必须先由放置入口建区）。但
    「星」这类区可能**还没建**：构造局面直接给了武将（`GAME_START` 的扣置没跑过），
    或技能已失去（规则 §15.2：失去技能不影响区，但区本身可以不存在）。取牌面时把
    "没有这个区"读成空区，才能让"没星可移"走到既有的**取消**路径（不发空询问）。
    """
    if ref.zone is Zone.HERO:
        instance = getattr(ref, "skill_instance", None)
        if instance is None:
            return []
        seat = getattr(ref, "seat", None)
        if seat is None:
            return []
        return list(state.hero_zone(seat, instance))
    return list(state.zone(ref))


def zone_refs(seat: int, zones: tuple[Zone | Any, ...]) -> tuple[Any, ...]:
    """把步骤的区域声明**坐标化**（huo-shenzhugeliang 票 02）。

    ``zones`` 里的元素可以是 ``Zone`` 枚举（缺省坐标 = ``(zone, seat)``），也可以已经是
    ``ZoneRef``（带 ``skill_instance`` 的武将牌上的牌区、或**另一个座位**的区）。两种都
    收，是因为 ``PickCards.zones`` 的既有声明是枚举元组（145 条技能一字不改），而"武将牌
    上的牌"必须多带一维。座位为 ``None`` 的 ``ZoneRef`` 按 ``seat`` 补齐。
    """
    out: list[Any] = []
    for zone in zones:
        if isinstance(zone, ZoneRef):
            out.append(zone if zone.seat is not None else ZoneRef(zone.zone, seat, zone.skill_instance))
        else:
            out.append(ZoneRef(zone, seat))
    return tuple(out)


def _step_seat_values(
    step: PickCards,
    state: GameState,
    frame: Any,
    owner: int,
    answers: FlowAnswers,
) -> tuple[int, ...] | None:
    """``PickCards.seats`` 的解析结果：``None`` = 老口径（只铺 ``face`` 那一座）。

    可调用档收 ``(state, frame, owner, answers)``（与 ``face`` / ``exclude_cards`` 同一条
    求值纪律）。空元组 = "此刻没有座位可铺"（调用方按 ``face == ()`` 同形处理）。
    """
    raw = step.seats
    if raw is None:
        return None
    if callable(raw):
        return tuple(int(seat) for seat in raw(state, frame, owner, answers))
    return tuple(int(seat) for seat in raw)


def _step_zones(
    step: PickCards,
    state: GameState,
    face: int,
    frame: Any = None,
    owner: int | None = None,
    answers: FlowAnswers | None = None,
    seats: tuple[int, ...] | None = None,
) -> tuple[Any, ...]:
    """``PickCards`` 这一步此刻的区域坐标（``hero_zone`` 在这里补上实例键）。

    ``seats`` 非 ``None`` ⇒ 逐座位拼接（座位优先，与 ``selection_face(..., seats=...)`` 同序）：
    池 / 候选 / 上限三处因此与铺面读到的是同一份槽位。
    """
    raw = step.hero_zone
    if raw is None:
        if seats is None:
            return zone_refs(face, step.zones)
        return tuple(ref for seat in seats for ref in zone_refs(seat, step.zones))
    key = raw(state, frame, face if owner is None else owner, answers or {}) if callable(raw) else str(raw)
    return (ZoneRef(Zone.HERO, face, key),)


def _zone_pool_size(state: GameState, seat: int, zones: tuple[Zone | Any, ...]) -> int:
    """``zones`` 此刻对 ``seat`` 铺出的槽位数（``PickCards.hi=None`` 的解析真源）。"""
    return sum(len(_zone_cards(state, ref)) for ref in zone_refs(seat, zones))


def _hi_of(
    step: PickCards,
    state: GameState,
    frame: Any,
    owner: int,
    answers: FlowAnswers,
) -> int | None:
    """这一步的上限：定值 / 可调用档 / ``None``（= 全部可选，由池现算）。"""
    hi = step.hi
    if callable(hi):
        return int(hi(state, frame, owner, answers))
    return hi


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
    refs: tuple[Any, ...],
    card_types: tuple[str, ...],
    slot_kinds: tuple[SlotKind, ...] = (),
    exclude_cards: frozenset[int] = frozenset(),
    suits: tuple[str, ...] = (),
) -> int:
    """``card_types`` / ``slot_kinds`` / ``suits`` / ``exclude_cards`` 过滤后，真能挑的牌数。

    只**读**区域列表（:meth:`GameState.zone`），不铺选择面 —— 故它可以在每次重入时安全
    地现算（铺面会消耗 ``select`` 子流的随机数，绝不能重复跑）。两条白名单与黑名单的空
    元组 = 不筛（同一份判据 :func:`_kept_by_filters` 与铺面过滤共用）。

    ``refs`` 是**已坐标化**的区域（多座位步由 :func:`_step_zones` 逐座位拼接）——于是
    ``hi=None`` 的上限与铺面 / 候选读同一份座位序，不存在"单座位上限"的第二份算法。
    """
    return sum(
        1
        for ref in refs
        for card_id in _zone_cards(state, ref)
        if _kept_by_filters(
            state.card(card_id), card_id, card_types, slot_kinds, exclude_cards, suits
        )
    )


def _excluded_card_ids(
    step: PickCards,
    state: GameState,
    frame: Any,
    owner: int,
    answers: FlowAnswers,
) -> frozenset[int]:
    """这一步的**实体牌 id 黑名单**（``PickCards.exclude_cards`` 的唯一解析点，W4-fix-3）。

    可调用档收 ``(state, frame, owner, answers)``：它要读声明之外的上下文（【贯石斧】的
    "这张装备牌自己"由 ``SkillInstance.source.card_id`` 回答）。渲染清单、铺选择面、算
    ``hi=None`` 的上限、校验复合动作**四处都读这一份**，故不存在第二个"排除谁"的谓词。
    """
    excluded = step.exclude_cards
    if callable(excluded):
        excluded = excluded(state, frame, owner, answers)
    return frozenset(int(card_id) for card_id in excluded)


def _kept_by_filters(
    card: Any,
    card_id: int,
    card_types: tuple[str, ...],
    slot_kinds: tuple[SlotKind, ...],
    exclude_cards: frozenset[int] = frozenset(),
    suits: tuple[str, ...] = (),
) -> bool:
    """这张牌是否同时过**牌名 / 槽位种类 / 花色**三条白名单与**实体牌 id 黑名单**（空 = 不筛）。

    牌名白名单逐项按 :func:`~sgs.effects.satisfies_required` 匹配（【杀】族按共享计数键）。

    这是选择面过滤的**唯一判据**（W4-fix-6，D.3）：铺面（:func:`_filter_face`）与
    ``hi=None`` 的上限（:func:`_filtered_zone_pool_size`）都读它。原先三条同形函数
    （``_filter_card_types`` / ``_filter_slot_kinds`` / ``_filter_excluded_cards``）各抄
    一遍 ``(slot, card_id)`` 的 zip 过滤，判据漂移时存在两份"什么算可挑"的风险。
    """
    if int(card_id) in exclude_cards:
        return False
    if card_types and not any(satisfies_required(card, type_id) for type_id in card_types):
        return False
    if suits and card.suit not in suits:
        return False
    return not (slot_kinds and card.slot not in slot_kinds)


def _filter_face(
    slots: tuple[Slot, ...],
    card_ids: tuple[int, ...],
    card_types: tuple[str, ...],
    slot_kinds: tuple[SlotKind, ...],
    exclude_cards: frozenset[int] = frozenset(),
    suits: tuple[str, ...] = (),
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """把三条白名单与一条黑名单作用在同一份选择面上（票 09 / 票 10 / W4-fix-3 / 本票）。

    ``slots`` 与 ``card_ids`` 是**同一份选择面**的两个投影，必须同步过滤——只改一个，
    步骤读回的实体牌 id 就会错位（【青龙偃月刀】是第一个消费者）。判据本身只有
    :func:`_kept_by_filters` 一处；这里只剩**隐藏槽**这一条形状规则：没有白名单时隐藏槽
    原样保留（他人手牌的选牌面），有白名单时它没有牌面可判、不能留在面里。
    """
    whitelisted = bool(card_types or slot_kinds or suits)

    def keep(slot: Slot, card_id: int) -> bool:
        if int(card_id) in exclude_cards:
            return False
        if slot.card is None:
            return not whitelisted
        return _kept_by_filters(
            slot.card, card_id, card_types, slot_kinds, exclude_cards, suits
        )

    kept = [
        (slot, card_id)
        for slot, card_id in zip(slots, card_ids)
        if keep(slot, card_id)
    ]
    return (
        tuple(slot for slot, _ in kept),
        tuple(card_id for _, card_id in kept),
    )


def _revealed_cards(
    step: Any, state: Any, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[CardRef, ...]:
    """本步**授予应答者**的牌面（``PickOption.revealed`` 的求值口）。

    定值档原样返回；可调用档收 ``(state, frame, owner, answers)`` —— 与 ``face`` /
    ``exclude_cards`` 的可调用档同一条求值纪律（发问前现算，不抽随机数、不落 delta）。
    """
    source = step.revealed
    if callable(source):
        return tuple(source(state, frame, owner, answers))
    return tuple(source)


def _flow_selection_face(
    host: Any,
    step: PickCards,
    face: int,
    zones: tuple[Any, ...],
    seats: tuple[int, ...] | None,
) -> Any:
    """铺面口：单座位 / 武将牌上的区沿用原始声明，多座位步交给 ``seats=`` 那一档。

    测试里的铺面夹具只实现了老签名（``(seat, zones, *, reveal_hand)``）——故 ``seats``
    只在**声明了**它时才传，既有调用点与夹具的调用形状逐字不变。
    """
    if step.hero_zone is not None:
        return host.selection_face(face, zones, reveal_hand=step.reveal_hand)
    if seats is None:
        return host.selection_face(face, step.zones, reveal_hand=step.reveal_hand)
    return host.selection_face(face, step.zones, reveal_hand=step.reveal_hand, seats=seats)


def _flow_step_cards(
    host: Any,
    frame: Any,
    step: PickCards,
    owner: int,
    answers: FlowAnswers,
    timing: Timing,
    ask_owner: Any,
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

    **先认已接受的答案，再碰状态**（本 effort 票 01）：这一步问过就原样采用那份答案，
    ``actor`` / ``face`` / 黑名单 / 过滤后的池一格都不读。挂起续跑时 effect 已经改过这些
    判据要读的状态（【攻心】把目标最后一张手牌搬进牌堆），按活状态重算会把"已经发生过
    副作用的发动"误判成 ``"cancel"``（"零副作用"）；这一步的答案与发问时**同键同源**
    （:meth:`Engine.accepted_selection` 与 :meth:`Engine.ask_selection` 同一份缓存）。
    """
    cache_key = ask_key_of(step, frame)
    accepted = host.accepted_selection(frame, owner=ask_owner, key=cache_key)
    if accepted is not None:
        if len(accepted) < step.lo:
            return "cancel", ()
        return "ok", tuple(int(card_id) for card_id in accepted)
    actor = _flow_actor(step, host.state, frame, owner, answers)
    face = (
        actor
        if step.face is None
        else _resolve_face(step.face, host.state, frame, owner, answers)
    )
    if face == ():
        return "cancel", ()
    seats = _step_seat_values(step, host.state, frame, owner, answers)
    if seats == ():
        return "cancel", ()
    excluded = _excluded_card_ids(step, host.state, frame, owner, answers)
    zones = _step_zones(step, host.state, face, frame, owner, answers, seats)
    hi = _hi_of(step, host.state, frame, owner, answers)
    if hi is None:
        hi = _filtered_zone_pool_size(
            host.state, zones, step.card_types, step.slot_kinds, excluded, step.suits
        )
    if (step.card_types or step.slot_kinds or step.suits or excluded) and hi < step.lo:
        # 与空池同形：一张能挑的都没有（或不够 ``lo``）⇒ 不发没有答案的询问；
        # 同时也挡住 ``(lo, hi) = (1, 0)`` 这种会让 `_select_cards` 的 range 为空的形状。
        return "cancel", ()
    picks = host.ask_selection(
        frame,
        owner=ask_owner,
        key=cache_key,
        actor=actor,
        timing=timing,
        spec=SelectionSpec(
            zones=step.zones,
            total=(step.lo, hi),
            optional=step.optional,
            seats=seats,
        ),
        build_face=lambda: _filter_face(
            # 没有 ``hero_zone`` 时把**原始声明**交给铺面口（145 条既有技能与测试里的
            # 铺面夹具都按 ``Zone`` 枚举实现）；只有武将牌上的牌才需要坐标那一维。多座位
            # 步把 `seats` 交给同一个入口（03a 的引擎口），铺面与池因此同源。
            *_flow_selection_face(host, step, face, zones, seats),
            step.card_types,
            step.slot_kinds,
            excluded,
            step.suits,
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
      （判据 = :func:`~sgs.effects.satisfies_required`：牌名逐字相等，【杀】族按共享
      计数键放宽——M5-05b-fix 起青龙也能"提供"火【杀】/ 雷【杀】；与 :class:`PickCards`
      的 ``card_types`` 走同一条过滤。这一步**永远**是"本人自己的区域" ⇒ 手牌是
      **可见槽**，与 ``reveal_hand=True`` 同形）；
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
            if not satisfies_required(card, type_id):
                continue
            if is_slash(behavior_for(card)) and forbidden_as_slash(state, card):
                # 【定乱】牌不能作为【杀】提供 / 使用（"提供一张 B"窗口同样要拦）。
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
        if card is None or not card.is_virtual or not satisfies_required(card, type_id):
            continue
        sources = tuple(card.virtual_sources)
        if len(sources) != 1 or int(card.card_id) in seen:
            continue
        seen.add(int(card.card_id))
        slots.append(Slot(ref=locate(state, sources[0]), card=card))
        card_ids.append(int(card.card_id))
    return tuple(slots), tuple(card_ids)


def _flow_step_provide(
    host: Any,
    frame: Any,
    step: PickProvide,
    owner: int,
    answers: FlowAnswers,
    timing: Timing,
    ask_owner: Any,
) -> tuple[str, object]:
    """跑一步"提供一张 B"：返回三态 + ``tuple[CardRef, ...]``。

    面为空（或不够 ``lo``）与空池同形：**不发没有答案的询问**，走取消（票 19 §6）。
    答案从槽位回读**牌面**——虚拟 B 的负 id 进不了 :meth:`GameState.card`。

    **面只有一处缓存**（评审 Standards finding 2）：住在本步自己的帧内键
    ``frame.data["provide_faces"][(ask_owner, cache_key)]`` 里，``ask_selection`` 通过
    ``face=`` **直接用这一份**、不再在 ``asks`` 里另存第二份（那是票 21 ``KeyError`` 的
    土壤：两份缓存一旦分母不同，另一份面的答案就会被喂进 ``refs``）。重入时来源牌可能
    已经离区（被用掉的那张【杀】不在手里了），重算出来的面会与**已缓存的答案**（面内位置）
    对不上——复用同一份面才对。缓存键与 :meth:`Engine.ask_selection` 的答案缓存**逐字
    同源**（``(询问位置, 询问名)``，票 22）：两个提供步即便共用 ``cache_key`` 也不会互相
    串面；同一步用可调用的 ``cache_key`` 逐轮改名时，每轮各有一份自己的面（不会拿上一轮的
    面去解析这一轮的答案）。
    """
    actor = _flow_actor(step, host.state, frame, owner, answers)
    cache_key = ask_key_of(step, frame)
    faces = frame.data.setdefault("provide_faces", {})
    face = faces.get((ask_owner, cache_key))
    if face is None:
        face = provide_face(host.state, actor, step.type_id, step.zones)
        faces[(ask_owner, cache_key)] = face
    slots, _card_ids = face
    if len(slots) < step.lo:
        return "cancel", ()
    refs = {
        int(slot.card.card_id): slot.card
        for slot in slots
        if slot.card is not None
    }
    picked = host.ask_selection(
        frame,
        owner=ask_owner,
        key=cache_key,
        actor=actor,
        timing=timing,
        spec=SelectionSpec(
            zones=step.zones,
            total=(step.lo, min(step.hi, len(slots))),
            optional=step.optional,
        ),
        face=face,
        provide_type=step.type_id,
    )
    if host.suspended(picked):
        return "stay", ()
    if len(picked) < step.lo:
        return "cancel", ()
    return "ok", tuple(refs[int(card_id)] for card_id in picked)


def _seat_bounds(
    step: PickSeats, state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[int, int]:
    """这一步此刻要选几个座位 —— ``lo`` / ``hi`` 的**唯一**解析处（票 18）。

    定值档原样返回（既有技能逐字节不变）；可调用档按**前序答案**现算（界【渐营】② 的
    第 3 步：0 个 = 目标由牌名决定，1 个 = 玩家选一名）。执行器（:func:`_flow_step_seats`）、
    复合动作校验器（:func:`_assign_activation`）与清单渲染（:func:`manifest`）三处都读
    这一份，故"清单不要求选"与"校验器要求选"结构上不可能各说一套。

    ``hi < lo`` 是声明写错了（例如按牌名算出 0 个、却写死 ``lo = 1``）：**响亮失败**，
    不静默夹成一个宽松值——那会让界面收窄与裁决再次漂开。
    """
    lo = step.lo(state, frame, owner, answers) if callable(step.lo) else step.lo
    hi = step.hi(state, frame, owner, answers) if callable(step.hi) else step.hi
    resolved = (int(lo), int(hi))
    if resolved[1] < resolved[0] or resolved[0] < 0:
        raise ValueError(
            f"PickSeats({step.key!r}) 的 lo/hi 声明不一致：lo={resolved[0]} hi={resolved[1]}"
        )
    return resolved


def _flow_step_seats(
    host: Any,
    frame: Any,
    step: PickSeats,
    owner: int,
    answers: FlowAnswers,
    timing: Timing,
    ask_owner: Any,
) -> tuple[str, object]:
    """跑一步选座位（一次问齐 / ``lo..hi`` 次 ``SELECT_OPTION``）：返回三态 + 座位元组。

    **一条流水线里只有两种座位步**（table-ux-overhaul 票 13 起）：

    - ``hi > 1`` 且 ``not ordered`` ⇒ **一次问齐**（:meth:`Engine.ask_seats`，``SELECT_OPTIONS``
      + ``enumerate_candidates=False``）。这就是"一次选多名角色"的唯一形状：【突袭】的
      1–2 名、【除疠】的任意名都在这一档 —— 不再逐座发问、也不再逐座排除已选（答案是集合，
      重复由形状谓词自己挡）。
    - 其余（``hi == 1`` 的定值步，或 ``ordered`` 的**有序**多选）⇒ 保持逐座 ``ask_option``
      的既有形状：``ordered`` 的池依赖"本步已选"的座位，先后两问的问法由
      ``step_requirements`` 分别给（目前没有消费者落在这一档；【除疠】改走预览后已不是它）。

    **先认已接受的答案，再算池**（本 effort 票 01）：这一问若已经答过，答案就是这一步的
    裁决，``pool`` 一次都不算。挂起续跑时 effect 已经改过 ``pool`` 要读的状态（【攻心】
    把目标最后一张手牌搬进牌堆 ⇒ "手牌非空的其他角色"池里没有目标了），按活状态现算会
    把"已经发生过副作用的发动"误判成 ``"cancel"``（``run_flow`` 把它读成"零副作用、技能
    可再发动"）。答案缓存与 :meth:`Engine.ask_option` **同键同源**
    （:meth:`Engine.accepted_option`），故"重跑 steps 不重发询问"这条既有设计不变，只是
    把"现算 pool"从重入路径上摘掉了。
    """
    del ask_owner  # 座位步走 ``ask_option`` / ``ask_seats``，其询问名是 ``skill_id``
    actor = _flow_actor(step, host.state, frame, owner, answers)
    lo, hi = _seat_bounds(step, host.state, frame, owner, answers)
    if not step.ordered and hi > 1:
        return _flow_step_seats_once(host, frame, step, owner, actor, lo, hi, timing)
    chosen: list[int] = []
    while len(chosen) < hi:
        option_id = _flow_option_id(frame, step.key, len(chosen), multi=hi > 1)
        accepted = host.accepted_option(frame, skill_id=option_id, actor=actor)
        if accepted is not None:
            if isinstance(accepted, Pass):
                break
            chosen.append(option_seat(accepted))
            continue
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
            skill_id=option_id,
            options=tuple(seat_choice(seat) for seat in pool),
            optional=step.optional,
        )
        if host.suspended(answer):
            return "stay", ()
        if isinstance(answer, Pass):
            break
        chosen.append(option_seat(answer))
    if len(chosen) < lo:
        return "cancel", ()
    return "ok", tuple(chosen)


_SEAT_SET_CHECKED = "seat_set_checked"
"""帧私有：已经复核过"能不能从这个池里拼出来"的座位集合（键 = 步骤的询问 id）。

**不是**答案缓存（那是 ``ask_answers``）：它只回答"这份集合复核过了没有"。第 2 次重入起
状态已被 effect 改过（除疠弃掉的牌会让目标掉出池），照活状态再复核就会把一次已经发生过
副作用的发动误判成 "cancel"（``run_flow`` 因此抛 RuntimeError）；复核只在**答案落地后的
第一次重入**（effect 尚未开始）那一次跑，结果记在这里。
"""


def _flow_step_seats_once(
    host: Any,
    frame: Any,
    step: PickSeats,
    owner: int,
    actor: int,
    lo: int,
    hi: int,
    timing: Timing,
) -> tuple[str, object]:
    """**一次选齐**座位多选步（``hi > 1 ∧ not ordered``；table-ux-overhaul 票 13）。

    询问名不带序号（一件事只有一问）：``<短 id>:<步骤键>``。复核在
    :func:`_seat_set_is_buildable`：池仍然是"按已选现算"的那一份（除疠的势力互异），
    故一份越池的集合不会被当成合法答案——它与"凑不够 ``lo``"同一条出口（``"cancel"``，
    零副作用），而不是把一条非法的目标集合交给 ``effect``。
    """
    option_id = _flow_option_id(frame, step.key, 0, multi=False)
    checked = frame.data.setdefault(_SEAT_SET_CHECKED, {})
    accepted = host.accepted_option(frame, skill_id=option_id, actor=actor)
    if accepted is not None:
        chosen = (
            ()
            if isinstance(accepted, Pass)
            else tuple(_seat_of(choice) for choice in accepted.choices)
        )
        if checked.get(option_id) != chosen:
            if not _seat_set_is_buildable(step, host.state, frame, owner, chosen):
                return "cancel", ()
            checked[option_id] = chosen
        return _seat_set_verdict(chosen, lo)
    pool = tuple(int(seat) for seat in step.pool(host.state, frame, owner, ()))
    if len(pool) < lo:
        # 池里连下限都装不到（旧逐座档在池空时 break 出去，同一条出口）。
        return "cancel", ()
    if not pool:
        return _seat_set_verdict((), lo)
    answer = host.ask_seats(
        frame,
        actor,
        timing,
        skill_id=option_id,
        pool=pool,
        lo=lo,
        hi=hi,
        optional=step.optional,
    )
    if host.suspended(answer):
        return "stay", ()
    chosen = tuple(answer)
    if not _seat_set_is_buildable(step, host.state, frame, owner, chosen):
        return "cancel", ()
    checked[option_id] = chosen
    return _seat_set_verdict(chosen, lo)


def _seat_set_verdict(chosen: tuple[int, ...], lo: int) -> tuple[str, object]:
    """座位多选的三态裁决：凑够 ``lo`` ⇒ 提交，否则取消（与逐座档同一条出口）。"""
    if len(chosen) < lo:
        return "cancel", ()
    return "ok", tuple(chosen)


def _seat_set_is_buildable(
    step: PickSeats,
    state: GameState,
    frame: Any,
    owner: int,
    chosen: tuple[int, ...],
) -> bool:
    """这份座位集合能不能按**声明序**从池里一步步拼出来。

    "池依赖已选"的步骤（除疠的势力互异）与形状谓词
    （:func:`~sgs.effects.is_legal_option_set`：池内 / 无重复 / 区间）不是一回事：后者的
    ``options`` 是**首问**那一份快照，收不下"第二名的势力必须与第一名不同"。故这里按
    ``step.pool`` 的**前缀**逐个复核（与 ``_assign_activation`` 的复合动作校验、与前端的
    预览是同一份回调），越池即整步取消——服务端不采信一份它自己不会铺出来的集合。
    """
    prefix: list[int] = []
    for seat in chosen:
        if seat in prefix:
            return False
        pool = tuple(int(item) for item in step.pool(state, frame, owner, tuple(prefix)))
        if seat not in pool:
            return False
        prefix.append(seat)
    return True


def _seat_of(choice: str) -> int:
    """``seat:N`` 文本 → 座位号（``SelectOptions.choices`` 的一项）。"""
    return int(str(choice).removeprefix(SEAT_PREFIX))


def _flow_step_option(
    host: Any,
    frame: Any,
    step: PickOption,
    owner: int,
    answers: FlowAnswers,
    timing: Timing,
    ask_owner: Any,
) -> tuple[str, object]:
    """跑一步选项询问：返回三态 + 选项文本。

    **先认已接受的答案，再解析闭集**（本 effort 票 01）：``options`` / ``disabled`` /
    ``revealed`` 都可按活状态现算（界【结姻】那类"可选项取决于目标状态"），故挂起续跑时
    现算可能得到空闭集 ⇒ 把已经发生过副作用的发动误判成 ``"cancel"``。这一步问过就原样
    采用那份答案；答案缓存与 :meth:`Engine.ask_option` 同键同源。
    """
    del ask_owner  # 选项步走 ``ask_option``，其询问名是 ``skill_id``（自带步骤序号）
    actor = _flow_actor(step, host.state, frame, owner, answers)
    option_id = _flow_option_id(frame, step.key, 0, multi=False)
    accepted = host.accepted_option(frame, skill_id=option_id, actor=actor)
    if accepted is not None:
        if isinstance(accepted, Pass):
            return "cancel", ""
        return "ok", accepted.choice
    options = _resolve_options(step.options, host.state, frame, owner, answers)
    blocked = _resolve_options(step.disabled, host.state, frame, owner, answers)
    if blocked and not step.show_disabled:
        # 缺省档：置灰项在**另两处**已经出不去（清单画灰、复合动作被校验器拒收），但逐步
        # 询问这条路的候选就是 ``ask_option`` 的选项 —— 把置灰项留在里面等于给出一条
        # "点了必被拒"的候选，故这里也按同一份解析结果收窄（既有四条技能的旧形状）。
        options = tuple(text for text in options if text not in blocked)
    selectable = tuple(text for text in options if text not in blocked)
    if not selectable:
        # 此刻一项都执行不了（例如【结姻】的目标装备区没有空槽、手里也没有手牌）⇒
        # 与"空池"同一条纪律：不发没有候选的询问，整条流水线取消（零副作用）。
        return "cancel", ""
    call: dict[str, Any] = {
        "options": options,
        "optional": step.optional,
        "revealed": _revealed_cards(step, host.state, frame, owner, answers),
    }
    if step.show_disabled and blocked:
        # 置灰通道（`ask_option(disabled=…)`）：选项全列、只从 `legal_actions` 剔除。
        call["disabled"] = blocked
    answer = host.ask_option(
        frame,
        actor,
        timing,
        skill_id=option_id,
        **call,
    )
    if host.suspended(answer):
        return "stay", ""
    if isinstance(answer, Pass):
        return "cancel", ""
    return "ok", answer.choice


ARRANGE_CARDS_KEY = "arrange_cards"
"""``Arrange`` 步的**帧私有 peek 缓存**键（W6-02）：``{(flow 名, 步骤序号): tuple[CardRef, ...]}``。

``run_flow`` 挂起后的续跑会从第一步重跑，而"看牌堆顶"（``Engine.peek_deck``）在牌堆不够时
会**重洗**并落一条 ``deck_shuffle`` delta（``effects/primitives.py`` / ``Engine.peek_deck``）——
故"看了哪些牌"必须在**第一次**跑时定格、重入读缓存（spec B3）。缓存按**询问位置**
``(flow.name, index)`` 分桶（与 ``ask_selection`` 的答案缓存同一口径），同一帧上跑两条
流水线也不会串。它只活在帧里、不进记录流。

**已知边界（W6-07 记录，不修）**：键里**没有**"第几次发动"。同一帧里同一条流水线发动
**第二次**会命中第一次的缓存 —— 第二次拿到的仍是第一次看的牌。当前**没有消费者**会这样
（观星一回合只走一个准备阶段、一帧一次排列），故 W6 不为此改键形状；将来若出现"同一帧
二次排列"，键必须补上发动序号（届时同时要看 ``Engine.ask_arrange`` 的答案键，那个键含
牌 id 元组，会随新牌自然分开）。
"""

TWO_ROW_TOP_KEY = "two_row_arrange_top"
"""``TwoRowArrange`` 首跑时的**上排张数快照**（帧私有，键同 ``ARRANGE_CARDS_KEY``）。

为什么需要快照：牌面已经存在 ``ARRANGE_CARDS_KEY`` 里（首跑那一份），而 ``top`` / ``count``
是**现算**的。重入时如果牌堆已经变了，现算的 ``count`` 与缓存牌面的长度就对不上 ——
【罪论】正是这样：它的效果先把"获得"那叠移进手牌才可能挂起（移动触发 ``MOVE_AFTER``
钩子），续跑回到本步时牌堆少了几张，而缓存里的牌面还是三张。**同一份输入只读一次**
（`CODING_STANDARDS.md` §3 的帧私有键纪律）：牌面与切分在首跑一起定格，重入照抄。
"""


def watched_cards(frame: Any, flow: SkillFlow, step_key: str) -> tuple[CardRef, ...]:
    """``Arrange`` 步在本帧里**观看**的牌（帧私有 peek 缓存的读取口，W6-02）。

    名字取 ``GLOSSARY.md``「观看牌堆顶」的动词（W6-07 / 评审 J8）：这些牌是**看**到的、
    还没被拿走，叫 ``arranged_cards`` 会让人以为它们已经排好。效果拿到的答案是
    :class:`~sgs.state.ArrangeCards`（只有面内位置），位置 → 实体牌要读这一步第一次跑时
    存下的牌面。``flow`` + ``step_key`` 定位缓存槽：步骤键在一条流水线里两两不同
    （:meth:`SkillFlow.__post_init__`），故不必让调用方再拼询问位置。
    """
    for index, step in enumerate(flow.steps):
        if isinstance(step, Arrange) and step.key == step_key:
            return frame.data[ARRANGE_CARDS_KEY][(flow.name, index)]
    raise KeyError(f"{flow.name!r} 里没有 key={step_key!r} 的 Arrange 步")


def _flow_step_arrange(
    host: Any,
    frame: Any,
    step: Arrange,
    owner: int,
    answers: FlowAnswers,
    timing: Timing,
    ask_owner: Any,
) -> tuple[str, object]:
    """跑一步排列：看牌（**首跑**）→ ``ARRANGE`` 询问 → ``ArrangeCards`` 原样回传。

    看牌走 :meth:`Engine.peek_deck`（授予可见性，不搬牌）；重入时读帧私有缓存
    （:data:`ARRANGE_CARDS_KEY`），故重洗副作用与 ``deck_shuffle`` delta **只发生一次**。

    两堆皆空 ⇒ ``"cancel"``：不发零张的排列询问、``effect`` 不跑（迁移前
    ``arrange.guanxing_handler`` 的空牌短路）。切分固定时由 ``spec.split_point`` 给出唯一
    合法的置顶张数，候选生成据此收口（``effects/actions.py::_arrange``）。
    """
    actor = _flow_actor(step, host.state, frame, owner, answers)
    cache = frame.data.setdefault(ARRANGE_CARDS_KEY, {})
    spec = step.spec
    if isinstance(spec, TwoRowArrange):
        # 两行交换（huo-shenzhugeliang 票 02）：牌由规格自己回答，不碰牌堆。
        if ask_owner not in cache:
            # 首跑：牌面与上排张数**一起**定格；声明一致性（count == 牌面张数、top 在面内）
            # 也只在这里核一次——重入时牌堆可能已经变了（见 ``TWO_ROW_TOP_KEY``）。
            cards = tuple(spec.cards_of(host.state, frame))
            top = spec.top_of(host.state, frame)
            if spec.count_of(host.state, frame) != len(cards) or not 0 <= top <= len(cards):
                raise ValueError("TwoRowArrange 的张数 / 上排张数与实际牌面不符（声明写错了）")
            cache[ask_owner] = cards
            frame.data.setdefault(TWO_ROW_TOP_KEY, {})[ask_owner] = top
        cards = cache[ask_owner]
        if not cards:
            return "cancel", None
        top = frame.data[TWO_ROW_TOP_KEY][ask_owner]
        action = host.ask_arrange(
            frame,
            actor,
            timing,
            cards=cards,
            split=top,
            enumerate_candidates=spec.enumerate_candidates,
        )
        if host.suspended(action):
            return "stay", None
        return "ok", action
    if ask_owner not in cache:
        count = spec.count
        if callable(count):
            count = count(host.state, frame)
        cache[ask_owner] = host.peek_deck(frame, int(count))
    cards = cache[ask_owner]
    if not cards:
        return "cancel", None
    action = host.ask_arrange(
        frame,
        actor,
        timing,
        cards=cards,
        split=spec.split_point(len(cards)),
    )
    if host.suspended(action):
        return "stay", None
    return "ok", action


def _flow_step_branch(
    host: Any,
    frame: Any,
    step: FlowBranch,
    owner: int,
    answers: FlowAnswers,
    timing: Timing,
    ask_owner: Any,
) -> tuple[str, object]:
    """``FlowBranch`` 的执行器：**组合子**，只把展开结果交回 :func:`run_flow`。

    它不发询问、不读状态：返回 ``("branch", 子步元组)``，由执行器把子步**就地**插进待跑
    序列（所以 ``prefilled`` 的逐键消费与 ``(flow.name, index)`` 询问位置对子步同样成立）。
    其余三态（``stay`` / ``cancel`` / ``ok``）对组合子没有意义——那是叶子步骤的语义。
    """
    del host, frame, owner, timing, ask_owner
    return "branch", _branch_steps(step, answers)


_FLOW_STEP_RUNNERS: MappingProxyType[type, Callable[..., tuple[str, object]]] = (
    MappingProxyType(
        {
            PickCards: _flow_step_cards,
            PickSeats: _flow_step_seats,
            PickOption: _flow_step_option,
            PickProvide: _flow_step_provide,
            Arrange: _flow_step_arrange,
            FlowBranch: _flow_step_branch,
        }
    )
)
"""步骤类型 → 它的执行器（**唯一登记处**）：加一种步骤只改这里与 :data:`FlowStep`，
不必在 :func:`run_flow` 的 if 级联里再找一遍（评审指出的"两处同步改"）。

``FlowBranch`` 登记的是**组合子**执行器：返回 ``("branch", 子步元组)`` 由 :func:`run_flow`
就地展开，故"哪些步骤要跑"也只有这一个派生点。"""


class _Unset:
    """``run_flow(owner=…)`` 的**未指定**哨兵。

    ``owner`` 的合法取值是**座位号 ``int``**（没有哪个座位是 ``None``）。哨兵只用来把
    "未指定"与"某个座位"分开：未指定时读 ``frame.data["source"]``。显式传 ``None`` 是
    编程错误，会在 :func:`run_flow` 里的 ``int(...)`` 上立刻 ``TypeError`` —— 旧文档曾写
    "``owner=None`` 合法（应答者按步骤的 ``actor`` 定）"，但实现从不支持它（票 12 §2 收紧）。
    """


_UNSET = _Unset()


# ---------------------------------------------------------------------------
# 预发动清单 + 复合动作校验（W4-1）
# ---------------------------------------------------------------------------

class _ActivationFrame:
    """没有真帧时给步骤池用的最小帧替身（只实现 ``data``）。"""

    __slots__ = ("data",)

    def __init__(self, data: Mapping[str, Any] | None = None) -> None:
        self.data = dict(data or {})


def _with_answers(frame: Any, answers: FlowAnswers) -> _ActivationFrame:
    """把**前序步骤的答案**挂到帧上再调用派生回调（``preactivation_answers``）。

    这是清单渲染器与执行器共用的唯一通道：池函数若依赖前序选择（【离间】的受害者池
    按使用者、【流离】的新目标池按弃掉的牌），从 ``frame.data["preactivation_answers"]``
    读，而不是自己再存一份。
    """
    data = dict(getattr(frame, "data", {}) or {})
    data["preactivation_answers"] = dict(answers)
    return _ActivationFrame(data)


def _answer_shape(step: FlowStep, value: object) -> object:
    """清单渲染期的代表值 → 答案形状（效果读到的形状）。

    选项步的答案是**文本**（不是座位 / 牌 id 的元组），且代表值一律以"单值元组"传入
    （:func:`_prior_contexts` 的形状）⇒ 先按步骤类型分流，再解元组——反过来会把
    ``"jieyin_discard_hand"`` 当牌 id 转整数。
    """
    if isinstance(step, PickOption):
        return str(value[0]) if isinstance(value, tuple) else str(value)
    if isinstance(value, tuple):
        return tuple(int(item) for item in value)
    return (int(value),)


def _answer_text(step: FlowStep, value: object) -> str:
    """单值 → :class:`~sgs.state.StepPool.when` 里的规范文本。"""
    if isinstance(step, PickSeats):
        return seat_choice(int(value))
    if isinstance(step, PickOption):
        return str(value)
    return str(int(value))


def _card_options(
    state: GameState,
    face: int,
    step: PickCards,
    frame: Any = None,
    owner: int | None = None,
    answers: FlowAnswers | None = None,
) -> tuple[int, ...]:
    """``PickCards`` 此刻对该座位铺出的候选实体牌 id（与执行器同一份过滤）。

    ``exclude_cards``（W4-fix-3）与 ``card_types`` / ``slot_kinds`` 走同一条链路：清单的
    候选与校验器读同一个函数，故"选择面 = 校验面"这条契约不因黑名单而多一个谓词。
    """
    if not isinstance(face, int):
        return ()
    excluded = _excluded_card_ids(
        step, state, frame, face if owner is None else owner, answers or {}
    )
    seats = _step_seat_values(
        step, state, frame, face if owner is None else owner, answers or {}
    )
    if seats == ():
        return ()
    out: list[int] = []
    for ref in _step_zones(step, state, face, frame, owner, answers, seats):
        for card_id in _zone_cards(state, ref):
            if _kept_by_filters(
                state.card(card_id),
                card_id,
                step.card_types,
                step.slot_kinds,
                excluded,
                step.suits,
            ):
                out.append(int(card_id))
    return tuple(out)


def card_pool_size(
    state: GameState,
    seat: int,
    step: PickCards,
    *,
    frame: Any = None,
    owner: int | None = None,
    answers: FlowAnswers | None = None,
) -> int:
    """这一步对 ``seat`` 此刻**真能挑的牌数**（区域 + 两条白名单 + 黑名单，同一条过滤）。

    **公开**（W4-fix-3）：被动技的 ``available`` 判据要与成本步的选择面同源，而判据住在
    :mod:`sgs.skills.catalog` —— 把"排除谁"再抄一份就会有两个真源。【贯石斧】是第一个
    消费者：`_axe_plan` 的"`≥ 2` 张"与成本步的池都由这里回答。
    """
    answer_owner = seat if owner is None else owner
    excluded = _excluded_card_ids(step, state, frame, answer_owner, answers or {})
    seats = _step_seat_values(step, state, frame, answer_owner, answers or {})
    if seats == ():
        return 0
    return _filtered_zone_pool_size(
        state,
        _step_zones(step, state, seat, frame, owner, answers, seats),
        step.card_types,
        step.slot_kinds,
        excluded,
        step.suits,
    )


def provide_source_id(slot: Slot) -> int | None:
    """这一槽在**预发动清单里**的候选 id：实体 B = 它自己的 id；转化 B = **来源实体牌** id。

    为什么不是虚拟 B 的负 id（W4-2）：复合动作的 ``picks`` 口径是"代价牌 id"（`spec.md` §3），
    而虚拟 B 背后没有实体牌——界面手牌 / 装备区渲染的是**来源**那张。用来源 id 作候选，
    "点那张【闪】选【龙胆】转出的【杀】"就不需要在接入层再建一层 id 映射；校验器
    (:func:`_assign_activation`) 用同一个函数把 id 换回要用的 ``CardRef``（虚拟 B）。
    多来源转化（【丈八蛇矛】）没有单一来源 ⇒ 不给清单候选（与 :func:`provide_face` 的
    "只收单来源"同一条边界）。
    """
    card = slot.card
    if card is None:
        return None
    if not card.is_virtual:
        return int(card.card_id)
    sources = tuple(card.virtual_sources)
    return int(sources[0]) if len(sources) == 1 else None


def _provide_refs(state: GameState, actor: int, step: PickProvide) -> dict[int, Any]:
    """``PickProvide`` 的面 → ``{清单候选 id: 要用的牌面}``（实体 B 先占位，虚拟 B 不覆盖它）。"""
    slots, _ids = provide_face(state, actor, step.type_id, step.zones)
    refs: dict[int, Any] = {}
    for slot in slots:
        source = provide_source_id(slot)
        if source is not None and slot.card is not None:
            refs.setdefault(source, slot.card)
    return refs


def _provide_options(state: GameState, actor: int, step: PickProvide) -> tuple[int, ...]:
    """``PickProvide`` 此刻的候选（实体 B 与**单来源**转化 B 的来源牌 id，见 :func:`provide_source_id`）。"""
    return tuple(_provide_refs(state, actor, step))


def _hidden_face(step: PickCards, seats: Sequence[int], owner: int) -> bool:
    """这一步的牌面是否**不应对发动者展示**（他人手牌 / 明示盲挑）。

    ``seats`` = 这一步实际铺开的座位（单座位步传 ``(face,)``）：多座位步只要有一个座位
    不是发动者本人、而面里有手牌区，整面就算隐藏（手牌永远不该这样铺）。
    """
    if step.reveal_hand is False:
        return True
    if Zone.HAND not in step.zones:
        return False
    return any(int(seat) != owner for seat in seats)


# 步骤的"最多几个答案"：``PickCards`` / ``PickSeats`` 有 ``hi``，**选项步没有**（恒 1 个）。
# 清单按前序答案逐行展开时会问"这一步是不是多选"，故统一从这里读，不给每个步骤类型各写一次。
_STEP_MAX_KEY = "hi"


def _step_max(step: FlowStep) -> int | None:
    """这一步最多要几个答案（``None`` = 不设上限，如【制衡】的任意张）。"""
    return getattr(step, _STEP_MAX_KEY, 1)


def _step_min(step: FlowStep) -> int:
    """这一步最少要几个答案（选项步恒 1）。"""
    return getattr(step, "lo", 1)


def _sampled_answers(step: FlowStep, values: tuple[object, ...]) -> list[object]:
    """一个前序步骤的**代表答案**：单值 ＋ 满选 ＋ 前 ``lo`` 个（去重、保序）。

    多选步（结姻恰好两张、仁德任意张）的答案是一组值；池若依赖"这组牌"，单值行必然与
    校验器读到的组合不同源。清单渲染只抽样这几个代表，用来回答"池到底随不随前序变"：
    全相同 ⇒ 收成一行 ``when=()``；有不同 ⇒ 该步不下发候选索引（由校验器裁决）。
    """
    if not values:
        return []
    out: list[object] = [(value,) for value in values]
    maximum = _step_max(step)
    take = maximum if maximum is not None else len(values)
    out.append(tuple(values[:take]))
    lo = _step_min(step)
    if lo and lo != take:
        out.append(tuple(values[:lo]))
    return list(dict.fromkeys(out))


def _prior_contexts(
    domains: list[tuple[FlowStep, tuple[object, ...]]],
) -> list[dict[str, object]]:
    """前序步骤的代表答案组合（**全量**，不设上限）。

    table-ux-overhaul 票 11 按用户裁定删掉了"超过 ``MANIFEST_POOL_LIMIT`` 就整步退化"那条
    路（spec §2 Q6「**永不退化**」）：退化把一步的候选整格抽掉，界面只能靠"全都点得动"兜底
    ——那正是这张票要消掉的东西。逐步的现算候选现在由**步骤预览**端点给
    （:func:`preview_step`），清单不再需要"组合太多就假装没有候选"这一档。
    """
    per_step: list[list[object]] = []
    for step, values in domains:
        maximum = _step_max(step)
        answers = (
            _sampled_answers(step, values)
            if maximum is None or maximum > 1
            else [(value,) for value in values]
        )
        if not answers:
            return []
        per_step.append(answers)
    keys = [step.key for step, _ in domains]
    return [dict(zip(keys, combo)) for combo in product(*per_step)]


def _when_texts(
    domains: list[tuple[FlowStep, tuple[object, ...]]], combo: Mapping[str, object]
) -> tuple[str, ...]:
    """一个前序组合 → ``StepPool.when``（按步骤顺序摊平；多选答案摊成一串）。"""
    texts: list[str] = []
    for step, _values in domains:
        value = combo[step.key]
        if isinstance(value, tuple):
            texts.extend(_answer_text(step, item) for item in value)
        else:
            texts.append(_answer_text(step, value))
    return tuple(texts)


def _pool_rows(
    rows: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]],
    *,
    collapse: bool = True,
) -> tuple[StepPool, ...]:
    """去重后转成协议形状（保留首次出现的次序，确定性）。

    每行是 ``(when, options, disabled)``：``disabled`` 由**产出它的那一步**填——选项步的
    置灰项（见 :class:`PickOption` 的 ``disabled``）、以及转化清单来源步的**源牌级置灰**
    （table-ux-overhaul 票 12，产出方在 :func:`sgs.skills.heroes._manifest_steps`；本模块
    渲染的流水线步骤今天一律空元组）。

    若所有前序组合给出的候选**完全相同**（含义上的相同：候选与置灰项都一样），收成一行
    ``when=()``："这一步的池与前序选择无关"——比逐项重复 N 遍更小，也让接入层不必为无关的
    前序选择找行。

    ``collapse=False``（qunying 票 03b 的分支子步）：**不许**收成空 ``when``——那会让
    "未选中分支的行"变成"任何前序都匹配"的兜底行（前端 ``preactivationPoolRow`` 对
    ``when=()`` 有一档"与前序无关"的回退），于是选效果①时分支②的步骤仍然冒出来。分支行
    必须逐条带着完整前序前缀。
    """
    if collapse and rows and len({(options, disabled) for _when, options, disabled in rows}) == 1:
        return (StepPool(when=(), options=rows[0][1], disabled=rows[0][2]),)
    seen: set[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = set()
    out: list[StepPool] = []
    for when, options, disabled in rows:
        key = (when, options, disabled)
        if key in seen:
            continue
        seen.add(key)
        out.append(StepPool(when=when, options=options, disabled=disabled))
    return tuple(out)


def _seat_pool_rows(
    rows: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], int]],
    *,
    collapse: bool = True,
) -> tuple[StepPool, ...]:
    """座位步的逐行池（票 18）：与 :func:`_pool_rows` 同一条去重 / 收行纪律，**多带逐行上限**。

    第 3 格 ``row_max`` 是这一行最多选几个：``0`` = 这一行**没有可选目标**（产物的目标
    由牌名决定 ⇒ 界面据此自动跳过、不画空步；执行器与校验器也按同一份 0 收尾）。
    行相同（候选与上限都一样）才收成一行 ``when=()``；``collapse=False``（分支子步）时
    **不许**收行——理由同 :func:`_pool_rows`。
    """
    if collapse and rows and len(
        {(options, disabled, row_max) for _when, options, disabled, row_max in rows}
    ) == 1:
        return (
            StepPool(when=(), options=rows[0][1], max=rows[0][3], disabled=rows[0][2]),
        )
    seen: set[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], int]] = set()
    out: list[StepPool] = []
    for when, options, disabled, row_max in rows:
        key = (when, options, disabled, row_max)
        if key in seen:
            continue
        seen.add(key)
        out.append(StepPool(when=when, options=options, max=row_max, disabled=disabled))
    return tuple(out)


def card_preactivations(
    designation_actions: tuple[UseCard, ...] | list[UseCard],
) -> dict[str, tuple[Step, ...]]:
    """把**借刀附带指定池**渲染成"牌名 → 两步清单"（W5-02a；``exclude``）。

    键是**牌名**（``card.type``）：同名的两张借刀形状相同，投影与前端都只认牌名
    （``spec.md`` §2.2；实例相关指定的升级点登记在 §6）。两步都是 ``name="seat"``、
    ``min=max=1``：

    1. **持刀者 A**：池里出现过的 A（一行 ``when=()``）；
    2. **Bn**：``when="seat:A"`` 的子池 = 该 A 在池里的全部 Bn。

    文案与 :class:`~sgs.state.Step` 的池形状沿用 W4-fix-3 / W4-1 的口径；清单整体
    **不进记录流**（询问字段 ``exclude``），由接入层在 02b 起显式下发。
    """
    holders: dict[str, list[int]] = {}
    designates: dict[str, dict[int, list[int]]] = {}
    for action in designation_actions:
        if not isinstance(action, UseCard) or not action.designations:
            continue
        if len(action.targets) != 1:
            continue
        name = action.card.type
        holder = action.targets[0]
        rows = designates.setdefault(name, {})
        if holder not in holders.setdefault(name, []):
            holders[name].append(holder)
        options = rows.setdefault(holder, [])
        for designate in action.designations:
            if designate not in options:
                options.append(designate)
    out: dict[str, tuple[Step, ...]] = {}
    for name in sorted(holders):
        seat_options = tuple(seat_choice(holder) for holder in holders[name])
        rows = tuple(
            StepPool(
                when=(seat_choice(holder),),
                options=tuple(seat_choice(designate) for designate in designates[name][holder]),
            )
            for holder in holders[name]
        )
        out[name] = (
            Step(
                name="seat",
                kind="seat",
                min=1,
                max=1,
                order=0,
                pool=(StepPool(when=(), options=seat_options),),
                wording="选择持刀者（装备区有武器、且有合法【杀】目标）",
            ),
            Step(
                name="seat",
                kind="seat",
                min=1,
                max=1,
                order=1,
                pool=rows,
                wording="选择【杀】的目标（由你指定）",
            ),
        )
    return out


def manifest(
    flow: SkillFlow,
    state: GameState,
    owner: int,
    *,
    frame: Any = None,
    step_requirements: Mapping[str, str] | None = None,
) -> tuple[Step, ...]:
    """把一条 :class:`SkillFlow` 的步骤声明渲染成**预发动清单**（W4-1）。

    只渲染**由 ``owner`` 本人应答的前缀**：遇到第一步的 ``actor`` 解析成别人就停
    （反间猜花色 / 挑牌这类"换应答者的第二段"仍逐步询问，不进预发动）。``pool`` 是
    "前序选择 → 这一步的候选"的函数，按前序选项逐行下发。渲染**只读状态**：不铺选择面、
    不抽随机数、不搬牌。逐选项子池按前序步骤的**单个选项**取键（流离的一张牌、离间的一名
    使用者），不枚举多选步的幂集——多选步之后的池若真依赖"选中的那几张"，要在 W4-2
    按技能显式收窄，而不是让清单按 2ⁿ 行下发。

    **不再有"组合数超上限就退化"这一档**（table-ux-overhaul 票 11 / spec §2 Q6「永不退化」）：
    静态行只是快照，同一步内的动态约束根本表达不了；"此刻还能点谁"由 :func:`preview_step`
    （步骤预览端点）按同一份池回调现算。清单因此**永远**下发它算得出来的候选，界面也就
    不需要"取不到就全都点得动"这种兜底。

    返回空元组 = 这条技能此刻没有可用的预发动清单（没有 flow / 首步无候选 /
    选取面不可见 / 由别人应答 / **含** :class:`Arrange` 步）。最后一档是 W6-02 的裁定：
    排列的候选是"全部排列 × 切分点"，``Step.pool`` 表达不了，故整条不下发——
    排列技能因此不进预发动（观星 ``preactivated is False``）。

    ``step_requirements``（W4-fix-3）：技能声明上的**分步文案**（:attr:`Step.wording` 的
    来源）。本函数不 import 声明表（``flow`` 反向只依赖低层模块），故由调用方
    :func:`~sgs.skills.preactivation.preactivation_for` 从 ``SkillDef.step_requirements``
    传进来；缺失的步骤 ``wording`` 为空串，读取方退回通用文案。
    """
    base = frame if frame is not None else _ActivationFrame()
    requirements = step_requirements or {}

    def wording(step: FlowStep) -> str:
        """这一步的人话（缺省空串 = 界面退回通用文案）。"""
        return requirements.get(step.key, "")

    steps: list[Step] = []
    domains: list[tuple[FlowStep, tuple[object, ...]]] = []

    def probe_answers() -> FlowAnswers:
        return {
            step.key: _answer_shape(step, values[0])
            for step, values in domains
            if values
        }

    def answer_map(combo: Mapping[str, object]) -> FlowAnswers:
        return {
            step.key: _answer_shape(step, combo[step.key])
            for step, _values in domains
            if step.key in combo
        }

    # 待渲染的步骤（qunying 票 03b）：``(步骤, 分支门, 该段的前序域, 清单 order)``。
    # 顶层步共享同一个前序域 list；``FlowBranch`` 的每个分支**复制**一份自己的域（分支子步
    # 只依赖本分支前面的子步）。门的形状 = ``(分支键, 选项文本)``：只有前序答案等于它时
    # 这个子步才存在 —— 所以清单给的是"该选项下的行"，别的选项**一行都不出现**。
    line: list[
        tuple[
            FlowStep,
            tuple[str, str] | None,
            list[tuple[FlowStep, tuple[object, ...]]],
            int,
        ]
    ] = [(step, None, domains, index) for index, step in enumerate(flow.steps)]
    position = 0
    while position < len(line):
        step, gate, domains, order = line[position]
        position += 1
        if isinstance(step, FlowBranch):
            for option, substeps in step.branches.items():
                if not substeps:
                    continue
                branch_domains: list[tuple[FlowStep, tuple[object, ...]]] = list(domains)
                for sub in substeps:
                    line.append((sub, (step.key, option), branch_domains, order))
            continue
        if getattr(step, "revealed", ()):
            # 观看授予的步骤**不能在清单里填表**：玩家要先在弹窗里看到那几张牌，才选得出
            # 这一步的答案；清单是"发动前一次填完"的形态，没有"看到"这一环。
            # 故含观看步的流水线整条不下发清单（与含 ``Arrange`` 同一条纪律）——
            # 【义起】因此走"点技能按钮 → 引擎逐步问"，而不是预发动入口。
            return ()
        if isinstance(step, Arrange):
            # 排列无法用 ``Step.pool`` 表达（候选 = 全部排列 × 切分点，不是可选池），
            # 故含 ``Arrange`` 的流水线**整条不下发清单**：观星因此不翻预发动。
            # 必须先于 ``_flow_actor`` 分支：本步若继续往下走，后面的 if 级联没有它的
            # 分支，会把"没渲染"当成"这一步没有清单"悄悄放过（spec B2）。
            return ()
        answers = probe_answers()
        if _flow_actor(step, state, _with_answers(base, answers), owner, answers) != owner:
            break
        combos = _prior_contexts(domains)
        if gate is not None:
            # 前序组合里的代表值是**单值元组**（选项步恒 1 个答案）⇒ 按"在不在这一格里"判。
            combos = [
                combo for combo in combos if gate[1] in (combo.get(gate[0]) or ())
            ]
            if not combos:
                # 这个分支此刻不可选（选项不在前序步的闭集里 / 已被用过）⇒ 子步一步都不出现。
                # 与"该行没有候选"不同：那是这条分支存在但此刻填不出答案，由下面的各块裁决。
                continue
        multi_prior = any(
            _step_max(prior) is None or (_step_max(prior) or 0) > 1
            for prior, _ in domains
        )
        rows: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        values: list[object] = []

        if isinstance(step, PickCards):
            for combo in combos:
                answers = answer_map(combo)
                framed = _with_answers(base, answers)
                face = _resolve_face(step.face, state, framed, owner, answers)
                if face == ():
                    return ()
                face_seats = _step_seat_values(step, state, framed, owner, answers)
                if face_seats == ():
                    return ()
                hidden = _hidden_face(
                    step, (face,) if face_seats is None else face_seats, owner
                )
                options = (
                    ()
                    if hidden
                    else _card_options(state, int(face), step, framed, owner, answers)
                )
                if not hidden and len(options) < step.lo:
                    return ()
                when = _when_texts(domains, combo) if combo else ()
                rows.append((when, tuple(str(card_id) for card_id in options), ()))
                values.extend(options)
            # 前序是多选步时，``when`` 只能按**单个**选项取键；若池确实随它变，
            # 逐选项行就与"选中的那几张"对不上 ⇒ 退化成一行空候选（校验器仍按真池裁决）。
            if multi_prior:
                if (
                    not rows
                    or len({(options, disabled) for _when, options, disabled in rows}) > 1
                ):
                    return ()
                if gate is None:
                    rows = [((), rows[0][1], rows[0][2])]
            widths = [len(options) for _when, options, _disabled in rows]
            maximum = (
                min(step.hi, max(widths, default=0))
                if step.hi is not None
                else max(widths, default=0)
            )
            if maximum < step.lo or maximum == 0:
                # 0 张可选 = 这一步填不出任何答案（``lo = hi = 0`` 的"只看不选"步）：
                # 清单里只会多出一个空步骤。整条不下发。
                return ()
            steps.append(
                Step(
                    name=step.key,
                    kind="card",
                    zones=step.zones,
                    min=step.lo,
                    max=maximum,
                    pool=_pool_rows(rows, collapse=gate is None),
                    order=order,
                    wording=wording(step),
                )
            )
            domains.append((step, tuple(dict.fromkeys(values))))

        elif isinstance(step, PickSeats):
            dynamic = callable(step.lo) or callable(step.hi)
            # 第 4 格 = 这一行要多选几个（只有可调用档才下发 ``StepPool.max``；定值步
            # 的载荷保持原样，既有清单逐字节不变）。
            seat_rows: list[
                tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], int | None]
            ] = []
            bounds: list[tuple[int, int]] = []
            for combo in combos:
                answers = answer_map(combo)
                framed = _with_answers(base, answers)
                pool = tuple(
                    int(seat) for seat in step.pool(state, framed, owner, ())
                )
                row_max: int | None = None
                if dynamic:
                    lo, hi = _seat_bounds(step, state, framed, owner, answers)
                    row_max = hi
                    bounds.append((lo, hi))
                if not pool and not domains:
                    return ()
                when = _when_texts(domains, combo) if combo else ()
                seat_rows.append(
                    (
                        when,
                        tuple(seat_choice(seat) for seat in pool),
                        (),
                        row_max,
                    )
                )
                values.extend(pool)
            if multi_prior:
                shapes = {
                    (options, disabled, row_max)
                    for _when, options, disabled, row_max in seat_rows
                }
                if not seat_rows:
                    seat_rows = []
                elif len(shapes) > 1:
                    seat_rows = []
                elif gate is None:
                    seat_rows = [((), seat_rows[0][1], seat_rows[0][2], seat_rows[0][3])]
            if dynamic:
                required = max((lo for lo, _hi in bounds), default=0)
                if required == 0:
                    # 所有前序组合都没有可选目标（产物的目标由牌名 / 上下文决定）⇒
                    # **这一步不存在**：清单里不出现一个空的第 3 步（票 18 的断言面）。
                    continue
                steps.append(
                    Step(
                        name=step.key,
                        kind="seat",
                        min=required,
                        max=max((hi for _lo, hi in bounds), default=0),
                        pool=_seat_pool_rows(seat_rows, collapse=gate is None),
                        order=order,
                        wording=wording(step),
                    )
                )
                domains.append((step, tuple(dict.fromkeys(values))))
                continue
            if step.ordered and step.hi > 1:
                # 有序多选座位步的第二问依赖**本步已选**的座位；清单只能表达第一问，
                # 故不下发候选索引（执行器按 ``chosen`` 逐步问，校验器也按真池验）。
                seat_rows = []
            steps.append(
                Step(
                    name=step.key,
                    kind="seat",
                    min=step.lo,
                    max=step.hi,
                    pool=_pool_rows(
                        [(when, options, disabled) for when, options, disabled, _m in seat_rows],
                        collapse=gate is None,
                    ),
                    order=order,
                    wording=wording(step),
                )
            )
            domains.append((step, tuple(dict.fromkeys(values))))

        elif isinstance(step, PickOption):
            # 可调用档按**前序答案**现算（票 23）：同一技能的选项集可能随第一步选中的人变
            # （【结姻】的目标装备区有没有空槽）。逐组合各算一次，行键沿用 ``when``。
            option_rows: list[
                tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]
            ] = []
            option_values: list[object] = []
            for combo in combos:
                answers = answer_map(combo)
                framed = _with_answers(base, answers)
                choices = _resolve_options(step.options, state, framed, owner, answers)
                if not choices:
                    return ()
                disabled = _resolve_options(step.disabled, state, framed, owner, answers)
                when = _when_texts(domains, combo) if combo else ()
                option_rows.append((when, choices, disabled))
                # 只有**能选**的那几项才是后续步骤的前序答案（票 18）：置灰项在闭集里
                # 只为"列出来给玩家看"，`_assign_activation` 拒收它们 ⇒ 以它们为前序的
                # 组合永远提交不了。把它们喂进 `_prior_contexts` 会让后续步骤的行数白涨
                # （界【蛊惑】的牌名闭集有十几个），界【渐营】的第 3 步更是会被这个行数
                # 挤进"退化"档。故前序候选只收非置灰项。
                option_values.extend(
                    choice for choice in choices if choice not in disabled
                )
            if multi_prior:
                shapes = {
                    (choices, disabled) for _when, choices, disabled in option_rows
                }
                if not option_rows or len(shapes) > 1:
                    return ()
                if gate is None:
                    option_rows = [((), option_rows[0][1], option_rows[0][2])]
            steps.append(
                Step(
                    name=step.key,
                    kind="option",
                    min=1,
                    max=1,
                    pool=_pool_rows(option_rows, collapse=gate is None),
                    order=order,
                    wording=wording(step),
                )
            )
            domains.append((step, tuple(dict.fromkeys(option_values))))

        elif isinstance(step, PickProvide):
            for combo in combos:
                answers = answer_map(combo)
                framed = _with_answers(base, answers)
                actor = _flow_actor(step, state, framed, owner, answers)
                options = _provide_options(state, actor, step)
                if len(options) < step.lo:
                    return ()
                when = _when_texts(domains, combo) if combo else ()
                rows.append((when, tuple(str(card_id) for card_id in options), ()))
                values.extend(options)
            if multi_prior:
                if (
                    not rows
                    or len({(options, disabled) for _when, options, disabled in rows}) > 1
                ):
                    return ()
                if gate is None:
                    rows = [((), rows[0][1], rows[0][2])]
            steps.append(
                Step(
                    name=step.key,
                    kind="card",
                    zones=step.zones,
                    min=step.lo,
                    max=min(
                        step.hi,
                        max(
                            (len(options) for _when, options, _disabled in rows),
                            default=0,
                        ),
                    ),
                    pool=_pool_rows(rows, collapse=gate is None),
                    order=order,
                    wording=wording(step),
                )
            )
            domains.append((step, tuple(dict.fromkeys(values))))

    return tuple(steps)


# ---------------------------------------------------------------------------
# 步骤预览（table-ux-overhaul 票 11 / spec §2 Q6 Q10）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StepPreview:
    """清单里**光标那一步**此刻的真候选 —— 只读派生，与校验器同一份池回调（票 11）。

    ``options`` / ``disabled`` 是服务端此刻算出来的"能点谁"，口径与
    :class:`~sgs.state.StepPool` 的 ``options`` / ``disabled`` 逐字相同（座位 = ``seat:N``、
    选牌 = 牌 id 的十进制文本、选项 = 选项文本）：前端的可点集合只读这一份，不自己推规则。

    ``can_advance`` = **已填的前缀每一步都满足**（可以离开这一步：进下一步或提交）；
    ``can_submit`` = **整条清单**都满足（最后一步必须点提交 —— spec §2 Q5）；
    ``next`` = 光标之后那一步的步骤键（``None`` = 这已是最后一步）。
    两个判据都在服务端算：自动推进（票 08）与"最后一步必须点提交"因此不落地前端判据。

    ``cursor`` = 预览的那一步在**清单**里的下标（清单的步骤与 ``flow.steps`` 不必一一对应：
    含 ``Arrange`` / 观看步 / 换应答者的流水线整条不出现，动态座位步在"没有目标"时被跳过，
    故下标一律按**清单**数，映射回流水线走 :attr:`~sgs.state.Step.order`）。
    """

    cursor: int
    name: str | None
    kind: Literal["card", "seat", "option"] | None
    options: tuple[str, ...]
    disabled: tuple[str, ...]
    can_advance: bool
    can_submit: bool
    next: str | None


def _preview_seats(values: Sequence[object]) -> tuple[int, ...]:
    """预览请求里的座位答案 → ``tuple[int, ...]``（接受 ``3`` 与 ``"seat:3"`` 两种写法）。

    两种写法都收是**读取方纪律**：清单下发的候选是 ``seat:N`` 文本（界面照抄），而记录流 /
    复合动作里的 ``choices`` 也是同一份文本；同时让手写 / 脚本调用能直接给座位号。
    """
    out: list[int] = []
    for value in values:
        if isinstance(value, bool):
            raise ValueError(f"座位答案不接受布尔值：{value!r}")
        if isinstance(value, int):
            seat = value
        elif isinstance(value, str) and value.startswith(SEAT_PREFIX):
            try:
                seat = int(value.removeprefix(SEAT_PREFIX))
            except ValueError as error:
                raise ValueError(f"座位文本损坏：{value!r}") from error
        else:
            raise ValueError(f"座位答案必须是座位号或 seat:N：{value!r}")
        if seat in out:
            raise ValueError(f"座位 {seat} 重复")
        out.append(seat)
    return tuple(out)


def _preview_ids(values: Sequence[object]) -> tuple[int, ...]:
    """预览请求里的选牌 / 提供答案 → ``tuple[int, ...]``（实体牌 id）。"""
    out: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"选牌答案必须是牌 id：{value!r}")
        try:
            card_id = int(value)
        except ValueError as error:
            raise ValueError(f"选牌答案必须是牌 id：{value!r}") from error
        if card_id in out:
            raise ValueError(f"牌 {card_id} 重复")
        out.append(card_id)
    return tuple(out)


def _preview_answer(
    step: FlowStep,
    state: GameState,
    framed: Any,
    owner: int,
    raw: Sequence[object],
    prior: FlowAnswers,
) -> tuple[object, bool, bool]:
    """一份预览答案 → ``(答案形状, 是否已满足, 要不要显式确认)``。

    答案形状与 :func:`_assign_activation` 逐字相同；"已满足"只回答**这一步填完了没有**
    （张数落在 ``lo..hi``、选项在闭集里且没被置灰），上下界与闭集都读校验器读的同一份派生
    （``_seat_bounds`` / ``_resolve_options`` / ``_card_options``），故"预览说能点"与
    "校验器说合法"不可能各说一套。第三格与界面的「下一步」同源：选牌 / 提供步总要显式
    确认，座位步只在 ``hi > lo``（1..max 多选）时要。
    """
    if isinstance(step, PickSeats):
        lo, hi = _seat_bounds(step, state, framed, owner, prior)
        answer = _preview_seats(raw)
        return answer, lo <= len(answer) <= hi, hi > lo
    if isinstance(step, PickOption):
        if len(raw) > 1:
            raise ValueError(f"选项步只接受一个值，收到 {len(raw)} 个")
        if not raw:
            # **正在编辑**的那一步还没点任何选项（前端每次选择变化都带光标那一步的空答案，
            # 见 `preactivation.js:preactivationPreviewAnswers`）⇒ 按"还没填"答；旧实现
            # 在这里抛 400，界面只好落到 fail-closed 的"取不到候选"上（蛊惑 / 分叉目的地
            # 这类**选项步是光标**的清单都会撞到）。
            return "", False, False
        answer = str(raw[0])
        legal = _resolve_options(step.options, state, framed, owner, prior)
        blocked = _resolve_options(step.disabled, state, framed, owner, prior)
        return answer, answer in legal and answer not in blocked, False
    if isinstance(step, PickCards):
        answer = _preview_ids(raw)
        face = _resolve_face(step.face, state, framed, owner, prior)
        options = (
            () if face == () else _card_options(state, int(face), step, framed, owner, prior)
        )
        hi = step.hi if step.hi is not None else len(options)
        return answer, step.lo <= len(answer) <= hi, True
    if isinstance(step, PickProvide):
        ids = _preview_ids(raw)
        refs = _provide_refs(state, _flow_actor(step, state, framed, owner, prior), step)
        try:
            answer = tuple(refs[card_id] for card_id in ids)
        except KeyError as error:
            raise ValueError(
                f"步骤 {step.key} 的牌 {error.args[0]} 不在可选范围内"
            ) from error
        hi = step.hi if step.hi is not None else len(refs)
        return answer, step.lo <= len(ids) <= hi, True
    raise ValueError(f"步骤 {step.key} 不能出现在预发动清单里")  # Arrange / 观看步


def _manifest_flow_step(flow: SkillFlow, manifest_step: Step) -> FlowStep:
    """清单的这一步在流水线里是谁（``Step.order`` + 步名回指）。

    顶层步的 ``order`` 直接是它在 ``flow.steps`` 里的下标；分支子步的 ``order`` 指回
    **``FlowBranch`` 那一步**，再按步名（``key`` 全流水线唯一）从它的分支里取回真步骤。
    """
    target = flow.steps[manifest_step.order]
    if isinstance(target, FlowBranch):
        for substeps in target.branches.values():
            for sub in substeps:
                if sub.key == manifest_step.name:
                    return sub
        raise ValueError(
            f"清单步骤 {manifest_step.name!r} 指回的 FlowBranch 里没有这个子步"
            "（声明与清单不一致）"
        )
    return target


def preview_step(
    flow: SkillFlow,
    state: GameState,
    owner: int,
    answers: Sequence[tuple[str, Sequence[object]]],
    *,
    steps: Sequence[Step],
    frame: Any = None,
) -> StepPreview:
    """``answers`` 前缀 → 清单那一步的**现算**候选（只读；与校验器同一份池 / 界回调）。

    ``answers`` 是玩家已经填的答案（**清单**步名的前缀、按顺序），最后一条可以是他**正在
    编辑**的那一步：``values`` 非空时这一步的池按它排除已选目标（【除疠】的"势力互异"、
    【离间】的第二问都靠这条）。``steps`` 是**这次询问已经下发的那份清单**——预览不重渲
    清单（那要枚举全部前序组合），映射回流水线只读 :attr:`~sgs.state.Step.order` 与步名
    （分支子步的 ``order`` 指回它的 ``FlowBranch``，由 :func:`_manifest_flow_step` 取回）。

    **分支**（qunying 票 03b）：清单里带着**所有**分支的子步，故预览按"已选前序答案"解析：
    分支键的答案不是这个选项的那些子步**此刻不存在**，直接跳过（光标越过它）；它们若带着
    旧答案，按"跨分支的旧答案"记下——``can_submit`` 关掉（与校验器"多余载荷"同口径）。

    **只读**：只调池 / 界的派生回调，不压帧、不搬牌、不抽随机数、不写记录（与
    :meth:`Engine.respond_candidates` 同一条纪律）。池回调要帧上下文而这次预览没有真帧时
    （``PLAY_PHASE`` 的清单就是这种）⇒ 候选置空、``can_advance`` 为假（fail-closed：
    前端据此"点不了"，不回退成"全都点得动"）。

    光标规则（前端的本地光标与它同源，故两边不会各指一步）：

    - 最后一条答案那一步**还要显式确认**（选牌 / 提供步，或 ``hi > lo`` 的多选座位步）⇒
      光标停在它上面：玩家还在这一步上挑，"还能点谁"是这一步的问题；
    - 最后一条答案**没填满**（张数不在 ``lo..hi``、选项被置灰）⇒ 光标停在它上面；
    - 其余 ⇒ 光标 = 已填步数（预览**下一步**）。
    """
    base = frame if frame is not None else _ActivationFrame()
    given = [(name, tuple(values)) for name, values in answers]
    if len(given) > len(steps):
        raise ValueError(f"答案前缀比清单还长（{len(given)} > {len(steps)}）")
    membership = _branch_membership(flow)

    def active(step: FlowStep) -> bool:
        """这一步在**当前已选答案**下存不存在（顶层步恒存在）。"""
        gate = membership.get(step.key)
        return gate is None or recorded.get(gate[0]) == gate[1]

    recorded: dict[str, object] = {}
    cursor = len(steps)
    satisfied_prefix = True
    cursor_satisfied = False
    cursor_prior: dict[str, object] = {}
    consumed = 0
    stale = False
    cursor_pending = False
    for index, manifest_step in enumerate(steps):
        step = _manifest_flow_step(flow, manifest_step)
        if not active(step):
            # 别的分支的子步：这一步此刻不存在 —— 跳过它（光标越过）。
            if index < len(given):
                consumed += 1
                if given[index][1]:
                    stale = True
            continue
        if index >= len(given):
            # 光标停在**还没填**的一步上：整条清单因此还不可提交。
            cursor = index
            cursor_prior = dict(recorded)
            cursor_pending = True
            break
        name, raw = given[index]
        if name != manifest_step.name:
            raise ValueError(
                f"答案第 {index} 步的步名是 {manifest_step.name!r}，不是 {name!r}"
                "（答案必须是清单步名的前缀、且按顺序）"
            )
        prior = dict(recorded)
        answer, satisfied, explicit = _preview_answer(
            step, state, _with_answers(base, prior), owner, raw, prior
        )
        recorded[step.key] = answer
        consumed += 1
        satisfied_prefix = satisfied_prefix and satisfied
        if not satisfied or (index == len(given) - 1 and explicit):
            cursor = index
            cursor_prior = prior
            cursor_satisfied = satisfied
            break

    if cursor >= len(steps):
        # 整条清单都填完了：没有"下一步"可预览，只剩能不能提交。
        return StepPreview(
            cursor=cursor,
            name=None,
            kind=None,
            options=(),
            disabled=(),
            can_advance=satisfied_prefix,
            can_submit=satisfied_prefix
            and consumed == len(given)
            and not stale
            and not cursor_pending,
            next=None,
        )

    # 光标之后还有没有**现役**步骤（跳过别的分支的子步）。没有 ⇒ 光标就在最后一步上
    # （"最后一步必须点提交"那一档），整条清单也就可以提交。
    remaining_active = any(
        active(_manifest_flow_step(flow, later)) for later in steps[cursor + 1 :]
    )
    # "能不能提交"与光标停在哪一步无关：整条清单都填好且每一步都满足 ⇒ 现在就能提交
    # （多选座位步停在最后一步上时也是这一档——最后一步必须点提交，spec §2 Q5）。
    can_submit = (
        satisfied_prefix
        and consumed == len(given)
        and not stale
        and not cursor_pending
        and not remaining_active
    )
    # "能不能进下一步"= **连光标这一步也满足了**：这一步还空着（或张数不够）时当然不能走。
    can_advance = satisfied_prefix and cursor_satisfied
    manifest_step = steps[cursor]
    step = _manifest_flow_step(flow, manifest_step)
    framed = _with_answers(base, cursor_prior)
    # 本步已选的座位只对**座位步**是座位答案：选牌 / 选项 / 提供步的 values 是牌 id 与
    # 选项文本，按座位解析会把它们判成"座位文本损坏"（一个 400，界面 fail-closed）。
    chosen = _preview_seats(given[cursor][1]) if cursor < len(given) and isinstance(step, PickSeats) else ()
    nxt = next(
        (later.name for later in steps[cursor + 1 :] if active(_manifest_flow_step(flow, later))),
        None,
    )
    options: tuple[str, ...] = ()
    disabled: tuple[str, ...] = ()
    try:
        if isinstance(step, PickSeats):
            options = tuple(
                seat_choice(int(seat)) for seat in step.pool(state, framed, owner, chosen)
            )
        elif isinstance(step, PickOption):
            options = tuple(
                str(item)
                for item in _resolve_options(
                    step.options, state, framed, owner, cursor_prior
                )
            )
            disabled = tuple(
                str(item)
                for item in _resolve_options(
                    step.disabled, state, framed, owner, cursor_prior
                )
            )
        elif isinstance(step, PickCards):
            face = _resolve_face(step.face, state, framed, owner, cursor_prior)
            options = (
                ()
                if face == ()
                else tuple(
                    str(card_id)
                    for card_id in _card_options(
                        state, int(face), step, framed, owner, cursor_prior
                    )
                )
            )
        elif isinstance(step, PickProvide):
            actor = _flow_actor(step, state, framed, owner, cursor_prior)
            options = tuple(
                str(card_id) for card_id in _provide_options(state, actor, step)
            )
        else:  # pragma: no cover - 清单不含 Arrange / 观看步
            raise ValueError(f"步骤 {step.key} 不能出现在预发动清单里")
    except KeyError:
        # 池回调要帧上下文（被动技的池读帧），而这次预览拿不到真帧：失败即"没有候选"
        # （fail-closed），不猜、也不回退成"全都点得动"。**能不能提交 / 推进也一并关掉**：
        # 候选空着还说"能走下一步"自相矛盾（spec §6 不变量 5），界面会停在一步没有可点
        # 项的面上。这一步的满足与否在拿不到候选时**不知道**，故按"没满足"答。
        options = ()
        disabled = ()
        can_advance = False
        can_submit = False

    return StepPreview(
        cursor=cursor,
        name=manifest_step.name,
        kind=manifest_step.kind,
        options=options,
        disabled=tuple(item for item in disabled if item in options),
        can_advance=can_advance,
        can_submit=can_submit,
        next=nxt,
    )


def _picks_for_step(
    steps: Sequence[FlowStep], index: int, step: PickCards | PickProvide, remaining: int
) -> int:
    """这一步从 ``picks`` 里吃掉几张。

    ``hi`` 固定 ⇒ 取 ``min(hi, remaining)``。``hi=None``（"任意张"）⇒ 吃掉剩余，但**给后面
    的选牌步留出它们的上限**（没有分隔符时，贪心地全吃掉会让后面的步骤永远缺牌）。
    只把 ``actor is None``（本人应答）的后续选牌步计入预留；当前 A 族没有多选牌步的流程，
    这条是为声明形状兜底，不改变既有行为。

    ``steps`` 是**展开后**的待跑序列（qunying 票 03b）：分支子步与顶层步同序，故"后面的
    选牌步"按同一份序列数，分支里的选牌步不会被漏掉。
    """
    if step.hi is not None:
        return min(step.hi, remaining)
    reserved = 0
    for later in steps[index + 1 :]:
        if isinstance(later, (PickCards, PickProvide)) and later.actor is None:
            reserved += later.hi if later.hi is not None else later.lo
    return max(0, remaining - reserved)


def _assign_activation(
    flow: SkillFlow,
    state: GameState,
    owner: int,
    picks: tuple[int, ...],
    choices: tuple[str, ...],
    *,
    frame: Any,
    check: bool,
) -> tuple[FlowAnswers, str | None]:
    """把复合动作摊回**逐步答案**；``check`` = 逐步校验（首个失败原因）。

    唯一的拆分规则（清单与校验器共用）：``picks`` 按顺序喂给选牌 / 提供步，
    ``choices`` 按顺序喂给选座位 / 选项步；遇到第一步的 ``actor`` 不是 ``owner`` 就停
    （后续是换应答者的第二段，由引擎逐步问）。``Arrange`` 步**不能由复合动作预填**
    （答案是 ``ArrangeCards``，不是 picks / choices）——也停在这里；含 ``Arrange`` 的
    流水线不下发清单（:func:`manifest` 返回 ``()``），故正常路径走不到它。
    多余的载荷一律判错——否则复合动作会成为绕过步骤声明的夹带通道。
    """
    base = frame if frame is not None else _ActivationFrame()
    answers: dict[str, object] = {}
    pick_index = 0
    choice_index = 0
    # 展开后的待跑序列（qunying 票 03b）：``FlowBranch`` 在**它前面那一步的答案**已知时展开，
    # 于是"只在该选项被选中时要求 / 拒收它的答案"落成同一条规则：未选中的分支 → 它的步骤
    # 根本不在序列里 → 多余的 picks / choices 由末尾的"多余载荷"检查拒收（fail-closed）。
    line: list[FlowStep] = list(flow.steps)
    position = 0
    while position < len(line):
        step = line[position]
        position += 1
        if isinstance(step, FlowBranch):
            line[position:position] = list(_branch_steps(step, answers))
            continue
        if getattr(step, "revealed", ()):
            # 观看授予的步骤**不能在清单里填表**：玩家要先在弹窗里看到那几张牌，才选得出
            # 这一步的答案；清单是"发动前一次填完"的形态，没有"看到"这一环。
            # 故含观看步的流水线整条不下发清单（与含 ``Arrange`` 同一条纪律）——
            # 【义起】因此走"点技能按钮 → 引擎逐步问"，而不是预发动入口。
            return ()
        if isinstance(step, Arrange):
            break
        framed = _with_answers(base, answers)
        actor = _flow_actor(step, state, framed, owner, answers)
        if actor != owner:
            break

        if isinstance(step, PickCards):
            remaining = len(picks) - pick_index
            if check and remaining < step.lo:
                return answers, f"步骤 {step.key} 至少需要 {step.lo} 张牌，复合动作只给了 {remaining} 张"
            take = _picks_for_step(line, position - 1, step, remaining)
            chunk = tuple(int(card_id) for card_id in picks[pick_index : pick_index + take])
            pick_index += take
            if check:
                if len(chunk) < step.lo:
                    return answers, f"步骤 {step.key} 至少需要 {step.lo} 张牌"
                if step.hi is not None and len(chunk) > step.hi:
                    return answers, f"步骤 {step.key} 至多 {step.hi} 张牌"
                if len(set(chunk)) != len(chunk):
                    return answers, f"步骤 {step.key} 选了重复的牌"
                face = _resolve_face(step.face, state, framed, owner, answers)
                if face == ():
                    return answers, f"步骤 {step.key} 此刻没有可选的牌"
                options = _card_options(state, int(face), step, framed, owner, answers)
                for card_id in chunk:
                    if card_id not in options:
                        return answers, f"步骤 {step.key} 的牌 {card_id} 不在可选范围内"
            answers[step.key] = chunk

        elif isinstance(step, PickSeats):
            lo, hi = _seat_bounds(step, state, framed, owner, answers)
            remaining = len(choices) - choice_index
            if check and remaining < lo:
                return answers, f"步骤 {step.key} 至少需要 {lo} 名目标，复合动作只给了 {remaining} 个"
            take = min(hi, remaining)
            chunk = tuple(choices[choice_index : choice_index + take])
            choice_index += take
            chosen: list[int] = []
            for text in chunk:
                if not isinstance(text, str) or not text.startswith(SEAT_PREFIX):
                    return answers, f"步骤 {step.key} 的选项必须是座位（seat:N）"
                try:
                    seat = int(text.removeprefix(SEAT_PREFIX))
                except ValueError:
                    return answers, f"步骤 {step.key} 的座位文本损坏：{text!r}"
                if seat in chosen:
                    return answers, f"步骤 {step.key} 选了重复的目标"
                if check:
                    try:
                        pool = tuple(
                            int(seat_value)
                            for seat_value in step.pool(state, framed, owner, tuple(chosen))
                        )
                    except KeyError:
                        # PLAY_PHASE 的校验没有真帧；池若硬读帧上下文，这是声明错误。
                        # 报成"首个失败原因"而不是把 KeyError 漏给调用方（fail-closed）。
                        return answers, f"步骤 {step.key} 的目标池缺少帧上下文，无法校验"
                    if seat not in pool:
                        return answers, f"步骤 {step.key} 的目标 {seat} 不在可选范围内"
                chosen.append(seat)
            answers[step.key] = tuple(chosen)

        elif isinstance(step, PickOption):
            if choice_index >= len(choices):
                return answers, f"步骤 {step.key} 需要一个选项"
            text = choices[choice_index]
            choice_index += 1
            if check:
                framed = _with_answers(base, answers)
                legal = _resolve_options(step.options, state, framed, owner, answers)
                if text not in legal:
                    return answers, f"步骤 {step.key} 的选项 {text!r} 不在闭集内"
                # 置灰项**在闭集里但不可选**（票 04 §3）：界面画灰是提示，这里才是判据。
                # 与 ``options`` 读同一份前序答案（可调用档按刚选的那张牌现算）。
                blocked = _resolve_options(step.disabled, state, framed, owner, answers)
                if text in blocked:
                    return answers, f"步骤 {step.key} 的选项 {text!r} 此刻不可选"
            answers[step.key] = text

        elif isinstance(step, PickProvide):
            remaining = len(picks) - pick_index
            if check and remaining < step.lo:
                return answers, f"步骤 {step.key} 至少需要 {step.lo} 张牌"
            take = _picks_for_step(line, position - 1, step, remaining)
            chunk = tuple(int(card_id) for card_id in picks[pick_index : pick_index + take])
            pick_index += take
            refs = _provide_refs(state, actor, step)
            if check:
                for card_id in chunk:
                    if card_id not in refs:
                        return answers, f"步骤 {step.key} 的牌 {card_id} 不在可选范围内"
            answers[step.key] = tuple(refs[card_id] for card_id in chunk)

    if pick_index != len(picks):
        return answers, "复合动作里有多余的 picks（本技能的预发动清单没有用到）"
    if choice_index != len(choices):
        return answers, "复合动作里有多余的 choices（本技能的预发动清单没有用到）"
    return answers, None


def validate_flow_activation(
    flow: SkillFlow,
    state: GameState,
    owner: int,
    picks: tuple[int, ...],
    choices: tuple[str, ...],
    *,
    frame: Any = None,
) -> str | None:
    """逐步校验一个复合动作；返回**首个失败原因**，``None`` = 通过（W4-1）。

    谓词只调现有函数：选牌面 = :func:`_card_options`（区域 + ``card_types`` /
    ``slot_kinds`` 同一条过滤）、目标池 = 步骤自己的 ``pool``（与候选生成同源）、
    提供面 = :func:`provide_face`。校验**只读状态**，失败时一个副作用都没有。
    """
    _answers, error = _assign_activation(
        flow, state, owner, picks, choices, frame=frame, check=True
    )
    return error


def activation_answers(
    flow: SkillFlow,
    state: GameState,
    owner: int,
    picks: tuple[int, ...],
    choices: tuple[str, ...],
    *,
    frame: Any = None,
) -> FlowAnswers:
    """已校验的复合动作 → 逐步答案（``effect`` 直接读的形状）。

    ``run_flow`` 用它跳过**本人应答的前缀步骤**；第二个应答者开始的段仍逐步问。
    """
    answers, error = _assign_activation(
        flow, state, owner, picks, choices, frame=frame, check=False
    )
    if error is not None:  # pragma: no cover - 校验器已先拦下同一份载荷
        raise ValueError(error)
    return answers


_PREFILLED_KEY = "activation_prefilled"

"""帧私有的"复合动作摊回结果"缓存键（W4-fix-2）：值 = 步骤答案。

``run_flow`` 在 ``effect`` 挂起后的重入会从头再跑；复合动作的 picks 是**提交时的声明**，
不能按重入时的状态重新解析（来源牌可能已经离场）。这份缓存只活在帧里、不进记录流。

**键带上它缓存的那份载荷**（W4-fix-6，D.2）：``(skill_id, picks, choices)``。同一帧里
同一技能第二次发动是**另一份载荷**，只按 skill id 作键会让它命中上一次的答案、重放旧
picks（同一张【杀】的 RESOLVE 帧上先后开两次接缝就撞到这个）。载荷进键之后，第二次发动
摊回的是它自己那份声明；缺缓存时重新解析也只会得到与提交一致的那份答案。
"""


_EFFECT_STARTED = "flow_effect_started"
"""帧私有：**已经开始跑 ``effect``** 的流水线名集合（本 effort 票 01 的 ``"cancel"`` 约束）。

``run_flow`` 在调 ``effect`` 之前把 ``flow.name`` 加进来，那次发动真正收尾（``effect``
返回的不是 ``"stay"``）时清掉。于是"这条流水线的 effect 已经开始"这条事实可判 —— 挂起
续跑时它还留着，而 ``"cancel"`` 的语义（"一个副作用都不产生、技能可再发动"）此时已经
不成立：再把发动报成取消就是把**已经改过状态**的结算丢掉（本 effort 的实机 bug 就是
【攻心】的置顶尾巴整个丢失）。

**为什么是集合而不是一个标记**：同一帧上可以先后（甚至嵌套）跑多条流水线（一张【杀】的
RESOLVE 帧先【流离】、之后青龙 / 斧的接缝），一条的 effect 开始不该让另一条的 ``"cancel"``
变成错误。**为什么按名字而不是帧 / 栈位置**：续跑的载体帧可能是从派发帧**复制**出来的
临时帧（``Engine._resume_frame``），名字跨这份复制仍指同一次发动。
"""


def _activation_belongs(activation: Mapping[str, object], frame: Any, flow: SkillFlow) -> bool:
    """这份预填的复合动作是**这条流水线自己**的吗（帧内 ``activation`` 会跨技能存活）。

    ``frame.data["activation"]`` 挂在**发问的那一帧**上，供 :func:`run_flow` 重入时把
    ``picks`` / ``choices`` 摊回逐步答案。同一帧上可以先后跑**不同技能**的流水线——
    典型是【杀】的 RESOLVE 帧：【流离】在这里发动过，之后这张【杀】被抵消又在同一帧上开
    「被抵消时」接缝（青龙 / 贯石斧）。没有归属判据时，后一条流水线会拿到前一条技能的
    载荷去索引自己的选取面（牌 id 不在面里 ⇒ ``KeyError``）。
    """
    recorded_flow = activation.get("flow")
    if recorded_flow is not None:
        # 提交时由引擎按登记表解析出的**流水线名**：界版的流水线名带 ``jie_`` 前缀，
        # 与技能 id 的短名不相等（M6 票 16 的界【仁德】/界【突袭】），故优先读它。
        return str(recorded_flow) == flow.name
    recorded = activation.get("skill_id", frame.data.get("skill_id"))
    if recorded is None:
        return True  # 旧形态没有归属字段：保持"谁读谁用"的原行为，不 fail-closed
    short = str(recorded).rsplit(".", 1)[-1]
    return short == flow.name or flow.name.endswith(f"_{short}")


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
    故重入时从头重跑一遍步骤既不会发第二次询问、也不会重复副作用；``answers`` 每次重建，
    效果拿到的永远是"已经全部提交"的那一份。

    **重入时每一步先认它已接受的答案、再算池**（本 effort 票 01）：执行器的三态裁决因此
    不受 ``effect`` 已经改过的状态影响 —— 这正是"从头重跑"能成立的前提。旧实现先按**活
    状态**现算 ``pool`` / 选项闭集，effect 一旦把池读的东西搬走（【攻心】的 hand→DECK），
    续跑就会把已经发生过副作用的发动判成 ``"cancel"``。

    ``"cancel"`` 因此与"``effect`` 还没跑"**绑死**：本帧里这条流水线的 ``effect`` 一旦
    开始（哪怕它随后挂起），后续步骤再判 ``"cancel"`` 就不是"零副作用"，而是把一次
    已经改过状态的发动报成"技能可再发动"。这种形状只可能是步骤的询问标识在挂起期间
    变了（缓存认不出来），故实现**显式失败**（``RuntimeError``）而不是静默返回 ``None``。
    标记住在帧私有数据里（不进记录流），按流水线名记；某次发动真正收尾时清掉，故同一帧里
    同一条流水线的第二次发动仍能正常取消。

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
    activation = frame.data.get("activation")
    prefilled: FlowAnswers = {}
    if activation and _activation_belongs(activation, frame, flow):
        # W4-1：复合动作报上来的（picks, choices）先摊回逐步答案；**本人应答的前缀**
        # 因此一个询问都不发，第二个应答者开始的段仍走下面的逐步询问。
        #
        # **摊回只做一次**（W4-fix-2）：``effect`` 会挂起（青龙续杀 / 贯石斧搬牌），重入时
        # 整个 ``run_flow`` 从头再跑——若每次都按**当前状态**重新解析 picks，已经离场的
        # 来源牌（【青龙】刚把那张【杀】用掉）就不在面里了，``PickProvide`` 的 refs 索引
        # 会 KeyError。确认时的声明在**提交那一刻定格**，故答案缓存在帧上（帧私有数据，
        # 不进记录流）。键 = **这份载荷本身**（W4-fix-6，D.2）：同一帧同一技能的第二次
        # 发动是另一份声明，不能重放上一次的 picks。
        picks = tuple(int(card_id) for card_id in activation.get("picks", ()))
        choices = tuple(str(choice) for choice in activation.get("choices", ()))
        cache = frame.data.setdefault(_PREFILLED_KEY, {})
        cache_key = (str(activation.get("skill_id") or flow.name), picks, choices)
        if cache_key not in cache:
            cache[cache_key] = dict(
                activation_answers(
                    flow,
                    host.state,
                    seat,
                    picks,
                    choices,
                    frame=frame,
                )
            )
        prefilled = dict(cache[cache_key])
    started: set[str] = frame.data.setdefault(_EFFECT_STARTED, set())
    # 待跑序列（qunying 票 03b）：``FlowBranch`` 在原地**展开**成它的子步骤，于是子步与
    # 顶层步走同一个循环 —— ``prefilled`` 逐键消费、``(flow.name, index)`` 询问位置按展开
    # 后的序号派生，两件事都不需要给分支开第二套。顶层步在展开前序号与 ``flow.steps`` 的
    # 下标逐字相同（既有流水线的询问缓存 / 记录流因此一个字节都不变）。
    line: list[FlowStep] = list(flow.steps)
    position = 0
    while position < len(line):
        step = line[position]
        # 清单渲染器与执行器共用的通道：池 / 面回调若依赖前序答案，从这里读同一份
        # （预填的复合动作与逐步询问两条路都写它，故声明只写一次）。
        frame.data["preactivation_answers"] = dict(answers)
        if step.key in prefilled:
            answers[step.key] = prefilled.pop(step.key)
            position += 1
            continue
        status, value = _FLOW_STEP_RUNNERS[type(step)](
            host, frame, step, seat, answers, timing, (flow.name, position)
        )
        if status == "branch":
            # 组合子：子步骤**就地**插入，从同一位置开始跑（顶层步的序号不受影响）。
            line[position : position + 1] = list(value)  # type: ignore[arg-type]
            continue
        if status == "stay":
            return "stay"
        if status == "cancel":
            if flow.name in started:
                raise RuntimeError(
                    f"流水线 {flow.name!r} 的 effect **已经开始**（挂起后被续跑），步骤 "
                    f"{step.key!r} 却要把这次发动判成 'cancel'。'cancel' 的语义是"
                    "『一个副作用都不产生、技能可再发动』，而这次发动已经改过状态了 —— "
                    "续跑的步骤必须先采用它已接受的答案（Engine.accepted_selection / "
                    "Engine.accepted_option），不能按活状态重算池。"
                )
            # 取消：一个副作用都不产生（提交点不落、``effect`` 不跑），技能可再发动；
            # 声明了 ``on_cancel`` 的流水线额外跑一次**簿记收尾**（qunying 票 10：效果
            # 事件不成立只作废它自己，不回退上一个事件已经付掉的代价）。
            if flow.on_cancel is not None:
                flow.on_cancel(host, frame)
            return None
        answers[step.key] = value
        position += 1
    if on_commit is not None:
        on_commit()
    started.add(flow.name)
    control = flow.effect(host, frame, answers)
    if control != "stay":
        # 这次发动**收尾**了：清掉标记，否则同一帧里同一条流水线的**第二次发动**
        # （合法：W4-fix-6 的"同一帧同一技能两份载荷"）若真的取消，会被误判成
        # "effect 已经开始"。挂起（``"stay"``）时标记留着 —— 那次发动还没跑完。
        started.discard(flow.name)
    return control


def zero_step_effect(handler: Callable[[Any, Any], Any]) -> FlowEffect:
    """把**零步技能**的旧回调包成 :data:`FlowEffect`（W6-07；评审 J3 的去重）。

    零步技能（``steps=()``）的 effect 恒等于"丢掉空答案、原样转调旧回调"：W6-01 迁移时
    七条（连营 / 枭姬 / 奸雄 / 天妒 / 英姿 / 闭月 / 铁骑）各抄了一份一模一样的
    ``del answers; return handler(host, frame)`` 与同一句 docstring。形状只有这一种，
    故收成一个工厂 —— 后来者加零步技能时不该再抄第八份。

    ``handler`` 是**旧回调**（``(host, frame) -> Control``）：迁移只换执行路径，回调体
    一字不改（各自帧内游标 / 幂等守卫都还在里面）。返回的闭包仍是
    ``(host, frame, answers)``，由 :class:`SkillFlow` 的 ``effect`` 字段持有。
    """

    def effect(host: Any, frame: Any, answers: FlowAnswers) -> Any:
        """零步 ⇒ ``answers`` 恒为空：丢掉它，转调旧回调。"""
        del answers
        return handler(host, frame)

    return effect
