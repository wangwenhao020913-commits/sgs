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
from itertools import product
from types import MappingProxyType
from typing import Any, Literal

from sgs.effects import (
    CandidateScope,
    ModifierContext,
    ModifierPoint,
    apply_modifiers,
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
)
from sgs.timing import Timing

__all__ = [
    "FREE_SPLIT",
    "MANIFEST_POOL_LIMIT",
    "SEAT_PREFIX",
    "ActorRef",
    "Arrange",
    "AskIdentity",
    "DeckTopArrange",
    "TwoRowArrange",
    "FixedSplit",
    "FlowAnswers",
    "FlowEffect",
    "FlowStep",
    "PickCards",
    "PickOption",
    "PickProvide",
    "PickSeats",
    "SeatPool",
    "SkillFlow",
    "activation_answers",
    "ask_key_of",
    "card_pool_size",
    "card_preactivations",
    "manifest",
    "option_seat",
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

FlowEffect = Callable[[Any, Any, FlowAnswers], Any]
"""流水线效果：``(host, frame, answers) -> Control``；只在**所有步骤提交后**跑一次。"""

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
    reveal_hand: bool = True
    cache_key: str | Callable[[Any], str] | None = None
    card_types: tuple[str, ...] = ()
    slot_kinds: tuple[SlotKind, ...] = ()
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
    """

    lo: int
    hi: int
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


FlowStep = PickCards | PickSeats | PickOption | PickProvide | Arrange


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

    def __post_init__(self) -> None:
        """**同一流程内步骤 ``key`` 两两不同**（票 22）：重复即流程定义写错。

        ``run_flow`` 把答案按 ``answers[step.key]`` 回写给 ``effect``，故同流程内重名必然
        互相覆盖 —— 它不会撞询问缓存（位置含步骤序号），但会让效果读到别步的答案，
        属"写错了要在构造期炸掉"的一类，不是运行期可恢复的状态。
        """
        if not self.name:
            raise ValueError("SkillFlow.name 是询问位置的一部分，必须非空")
        seen: set[str] = set()
        duplicates: list[str] = []
        for step in self.steps:
            if step.key in seen and step.key not in duplicates:
                duplicates.append(step.key)
            seen.add(step.key)
        if duplicates:
            raise ValueError(
                f"SkillFlow {self.name!r} 的步骤 key 必须两两不同，重复：{sorted(duplicates)}"
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

    **短 id 的取法分两档**（票 08 的加法）：主动技的 ``SKILL`` 帧把全限定 id 写在
    ``frame.data["skill_id"]``；被动技**没有**这个键，派发环只写 ``running_skill``
    （见 :func:`~sgs.skills.catalog._dispatch`）。【流离】是第一条带座位步的被动技
    （【反馈】只有选牌步、不经过这里），若不认第二档就会在运行时 ``KeyError``。
    这里只读帧里的**既有键**，不新增协议字段——故记录流除新询问本身外不变。
    """
    raw = frame.data.get("skill_id") or frame.data.get("running_skill") or ""
    short = str(raw).rsplit(".", 1)[-1]
    return f"{short}:{key}:{index}" if multi else f"{short}:{key}"


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


def _step_zones(
    step: PickCards,
    state: GameState,
    face: int,
    frame: Any = None,
    owner: int | None = None,
    answers: FlowAnswers | None = None,
) -> tuple[Any, ...]:
    """``PickCards`` 这一步此刻的区域坐标（``hero_zone`` 在这里补上实例键）。"""
    raw = step.hero_zone
    if raw is None:
        return zone_refs(face, step.zones)
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
    seat: int,
    zones: tuple[Zone, ...],
    card_types: tuple[str, ...],
    slot_kinds: tuple[SlotKind, ...] = (),
    exclude_cards: frozenset[int] = frozenset(),
) -> int:
    """``card_types`` / ``slot_kinds`` / ``exclude_cards`` 过滤后，真能挑的牌数。

    只**读**区域列表（:meth:`GameState.zone`），不铺选择面 —— 故它可以在每次重入时安全
    地现算（铺面会消耗 ``select`` 子流的随机数，绝不能重复跑）。两条白名单与黑名单的空
    元组 = 不筛（同一份判据 :func:`_kept_by_filters` 与铺面过滤共用）。
    """
    return sum(
        1
        for ref in zone_refs(seat, zones)
        for card_id in _zone_cards(state, ref)
        if _kept_by_filters(state.card(card_id), card_id, card_types, slot_kinds, exclude_cards)
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
) -> bool:
    """这张牌是否同时过**牌名 / 槽位种类**两条白名单与**实体牌 id 黑名单**（空 = 不筛）。

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
    return not (slot_kinds and card.slot not in slot_kinds)


def _filter_face(
    slots: tuple[Slot, ...],
    card_ids: tuple[int, ...],
    card_types: tuple[str, ...],
    slot_kinds: tuple[SlotKind, ...],
    exclude_cards: frozenset[int] = frozenset(),
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """把两条白名单与一条黑名单作用在同一份选择面上（票 09 / 票 10 / W4-fix-3）。

    ``slots`` 与 ``card_ids`` 是**同一份选择面**的两个投影，必须同步过滤——只改一个，
    步骤读回的实体牌 id 就会错位（【青龙偃月刀】是第一个消费者）。判据本身只有
    :func:`_kept_by_filters` 一处；这里只剩**隐藏槽**这一条形状规则：没有白名单时隐藏槽
    原样保留（他人手牌的选牌面），有白名单时它没有牌面可判、不能留在面里。
    """
    whitelisted = bool(card_types or slot_kinds)

    def keep(slot: Slot, card_id: int) -> bool:
        if int(card_id) in exclude_cards:
            return False
        if slot.card is None:
            return not whitelisted
        return _kept_by_filters(
            slot.card, card_id, card_types, slot_kinds, exclude_cards
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
    excluded = _excluded_card_ids(step, host.state, frame, owner, answers)
    zones = _step_zones(step, host.state, face, frame, owner, answers)
    hi = _hi_of(step, host.state, frame, owner, answers)
    if hi is None:
        hi = _filtered_zone_pool_size(
            host.state, face, zones, step.card_types, step.slot_kinds, excluded
        )
    if (step.card_types or step.slot_kinds or excluded) and hi < step.lo:
        # 与空池同形：一张能挑的都没有（或不够 ``lo``）⇒ 不发没有答案的询问；
        # 同时也挡住 ``(lo, hi) = (1, 0)`` 这种会让 `_select_cards` 的 range 为空的形状。
        return "cancel", ()
    picks = host.ask_selection(
        frame,
        owner=ask_owner,
        key=cache_key,
        actor=actor,
        timing=timing,
        spec=SelectionSpec(zones=step.zones, total=(step.lo, hi), optional=step.optional),
        build_face=lambda: _filter_face(
            # 没有 ``hero_zone`` 时把**原始声明**交给铺面口（145 条既有技能与测试里的
            # 铺面夹具都按 ``Zone`` 枚举实现）；只有武将牌上的牌才需要坐标那一维。
            *host.selection_face(
                face, zones if step.hero_zone is not None else step.zones,
                reveal_hand=step.reveal_hand,
            ),
            step.card_types,
            step.slot_kinds,
            excluded,
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


def _flow_step_seats(
    host: Any,
    frame: Any,
    step: PickSeats,
    owner: int,
    answers: FlowAnswers,
    timing: Timing,
    ask_owner: Any,
) -> tuple[str, object]:
    """跑一步选座位（``lo..hi`` 次 ``SELECT_OPTION``）：返回三态 + 座位元组。

    **先认已接受的答案，再算池**（本 effort 票 01）：这一问若已经答过，答案就是这一步的
    裁决，``pool`` 一次都不算。挂起续跑时 effect 已经改过 ``pool`` 要读的状态（【攻心】
    把目标最后一张手牌搬进牌堆 ⇒ "手牌非空的其他角色"池里没有目标了），按活状态现算会
    把"已经发生过副作用的发动"误判成 ``"cancel"``（``run_flow`` 把它读成"零副作用、技能
    可再发动"）。答案缓存与 :meth:`Engine.ask_option` **同键同源**
    （:meth:`Engine.accepted_option`），故"重跑 steps 不重发询问"这条既有设计不变，只是
    把"现算 pool"从重入路径上摘掉了。
    """
    del ask_owner  # 座位步走 ``ask_option``，其询问名是 ``skill_id``（自带步骤序号）
    actor = _flow_actor(step, host.state, frame, owner, answers)
    chosen: list[int] = []
    while len(chosen) < step.hi:
        option_id = _flow_option_id(frame, step.key, len(chosen), multi=step.hi > 1)
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
    if len(chosen) < step.lo:
        return "cancel", ()
    return "ok", tuple(chosen)


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
    if blocked:
        # 置灰项在**另两处**已经出不去（清单画灰、复合动作被校验器拒收），但逐步询问这条
        # 路（未翻预发动的流水线 / 被动技）的候选就是 ``ask_option`` 的选项 —— 把置灰项
        # 留在里面等于给出一条"点了必被拒"的候选。故这里也按同一份解析结果收窄。
        options = tuple(text for text in options if text not in blocked)
    if not options:
        # 此刻一项都执行不了（例如【结姻】的目标装备区没有空槽、手里也没有手牌）⇒
        # 与"空池"同一条纪律：不发没有候选的询问，整条流水线取消（零副作用）。
        return "cancel", ""
    answer = host.ask_option(
        frame,
        actor,
        timing,
        skill_id=option_id,
        options=options,
        optional=step.optional,
        revealed=_revealed_cards(step, host.state, frame, owner, answers),
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
            cache[ask_owner] = spec.cards_of(host.state, frame)
        cards: tuple[CardRef, ...] = cache[ask_owner]
        if not cards:
            return "cancel", None
        top = spec.top_of(host.state, frame)
        if spec.count_of(host.state, frame) != len(cards) or not 0 <= top <= len(cards):
            raise ValueError("TwoRowArrange 的张数 / 上排张数与实际牌面不符（声明写错了）")
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


_FLOW_STEP_RUNNERS: MappingProxyType[type, Callable[..., tuple[str, object]]] = (
    MappingProxyType(
        {
            PickCards: _flow_step_cards,
            PickSeats: _flow_step_seats,
            PickOption: _flow_step_option,
            PickProvide: _flow_step_provide,
            Arrange: _flow_step_arrange,
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


# ---------------------------------------------------------------------------
# 预发动清单 + 复合动作校验（W4-1）
# ---------------------------------------------------------------------------

MANIFEST_POOL_LIMIT = 32
"""一步的**逐选项子池**最多下发多少行（W4-1）。

池依赖前序选择的技能（【流离】选了武器 ⇒ 攻击范围变、【离间】先点使用者）要给每个前序
选项各渲一行。前序选项的组合数超过本上限时**退化**：该步的 ``pool`` 置空
（不下发候选索引），合法性仍由 :func:`validate_flow_activation` 按真谓词裁决——退化只
削弱界面提示，不放宽任何规则。
"""


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
    out: list[int] = []
    for ref in _step_zones(step, state, face, frame, owner, answers):
        for card_id in _zone_cards(state, ref):
            if _kept_by_filters(
                state.card(card_id), card_id, step.card_types, step.slot_kinds, excluded
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
    excluded = _excluded_card_ids(
        step, state, frame, seat if owner is None else owner, answers or {}
    )
    return _filtered_zone_pool_size(
        state,
        seat,
        _step_zones(step, state, seat, frame, owner, answers),
        step.card_types,
        step.slot_kinds,
        excluded,
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


def _hidden_face(step: PickCards, face: object, owner: int) -> bool:
    """这一步的牌面是否**不应对发动者展示**（他人手牌 / 明示盲挑）。"""
    if not isinstance(face, int):
        return True
    if step.reveal_hand is False:
        return True
    return face != owner and Zone.HAND in step.zones


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
    domains: list[tuple[FlowStep, tuple[object, ...]]], limit: int
) -> list[dict[str, object]] | None:
    """前序步骤的代表答案组合；超过 ``limit`` ⇒ ``None``（退化）。"""
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
    total = 1
    for answers in per_step:
        total *= len(answers)
    if total > limit:
        return None
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
) -> tuple[StepPool, ...]:
    """去重后转成协议形状（保留首次出现的次序，确定性）。

    每行是 ``(when, options, disabled)``：``disabled`` 只有选项步会填（置灰项，见
    :class:`PickOption` 的 ``disabled``），其余步骤一律空元组。

    若所有前序组合给出的候选**完全相同**（含义上的相同：候选与置灰项都一样），收成一行
    ``when=()``："这一步的池与前序选择无关"——比逐项重复 N 遍更小，也让接入层不必为无关的
    前序选择找行。
    """
    if rows and len({(options, disabled) for _when, options, disabled in rows}) == 1:
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
                min=1,
                max=1,
                order=0,
                pool=(StepPool(when=(), options=seat_options),),
                wording="选择持刀者（装备区有武器、且有合法【杀】目标）",
            ),
            Step(
                name="seat",
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
    limit: int = MANIFEST_POOL_LIMIT,
    step_requirements: Mapping[str, str] | None = None,
) -> tuple[Step, ...]:
    """把一条 :class:`SkillFlow` 的步骤声明渲染成**预发动清单**（W4-1）。

    只渲染**由 ``owner`` 本人应答的前缀**：遇到第一步的 ``actor`` 解析成别人就停
    （反间猜花色 / 挑牌这类"换应答者的第二段"仍逐步询问，不进预发动）。``pool`` 是
    "前序选择 → 这一步的候选"的函数，按前序选项逐行下发；组合数超过 ``limit`` 时
    退化成一行（见 :data:`MANIFEST_POOL_LIMIT`）。渲染**只读状态**：不铺选择面、不抽
    随机数、不搬牌。逐选项子池按前序步骤的**单个选项**取键（流离的一张牌、离间的一名
    使用者），不枚举多选步的幂集——多选步之后的池若真依赖"选中的那几张"，要在 W4-2
    按技能显式收窄，而不是让清单按 2ⁿ 行下发。

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

    for index, step in enumerate(flow.steps):
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
        combos = _prior_contexts(domains, limit)
        degraded = combos is None
        if degraded:
            combos = [{}]
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
                hidden = _hidden_face(step, face, owner)
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
                    zones=step.zones,
                    min=step.lo,
                    max=maximum,
                    pool=_pool_rows(rows),
                    order=index,
                    wording=wording(step),
                )
            )
            domains.append((step, tuple(dict.fromkeys(values))))

        elif isinstance(step, PickSeats):
            for combo in combos:
                answers = answer_map(combo)
                framed = _with_answers(base, answers)
                try:
                    pool = tuple(int(seat) for seat in step.pool(state, framed, owner, ()))
                except KeyError:
                    if not degraded:
                        raise
                    # 退化路径没有前序答案可喂；不假装"没有目标"，改成不下发候选索引。
                    rows = []
                    break
                if not pool and not domains:
                    return ()
                when = _when_texts(domains, combo) if combo else ()
                rows.append((when, tuple(seat_choice(seat) for seat in pool), ()))
                values.extend(pool)
            if multi_prior:
                if not rows:
                    rows = []
                elif len({(options, disabled) for _when, options, disabled in rows}) > 1:
                    rows = []
                else:
                    rows = [((), rows[0][1], rows[0][2])]
            if step.ordered and step.hi > 1:
                # 有序多选座位步的第二问依赖**本步已选**的座位；清单只能表达第一问，
                # 故不下发候选索引（执行器按 ``chosen`` 逐步问，校验器也按真池验）。
                rows = []
            steps.append(
                Step(
                    name=step.key,
                    min=step.lo,
                    max=step.hi,
                    pool=_pool_rows(rows),
                    order=index,
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
                option_values.extend(choices)
            if multi_prior:
                shapes = {
                    (choices, disabled) for _when, choices, disabled in option_rows
                }
                if not option_rows or len(shapes) > 1:
                    return ()
                option_rows = [((), option_rows[0][1], option_rows[0][2])]
            steps.append(
                Step(
                    name=step.key,
                    min=1,
                    max=1,
                    pool=_pool_rows(option_rows),
                    order=index,
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
                rows = [((), rows[0][1], rows[0][2])]
            steps.append(
                Step(
                    name=step.key,
                    zones=step.zones,
                    min=step.lo,
                    max=min(
                        step.hi,
                        max(
                            (len(options) for _when, options, _disabled in rows),
                            default=0,
                        ),
                    ),
                    pool=_pool_rows(rows),
                    order=index,
                    wording=wording(step),
                )
            )
            domains.append((step, tuple(dict.fromkeys(values))))

    return tuple(steps)


def _picks_for_step(
    flow: SkillFlow, index: int, step: PickCards | PickProvide, remaining: int
) -> int:
    """这一步从 ``picks`` 里吃掉几张。

    ``hi`` 固定 ⇒ 取 ``min(hi, remaining)``。``hi=None``（"任意张"）⇒ 吃掉剩余，但**给后面
    的选牌步留出它们的上限**（没有分隔符时，贪心地全吃掉会让后面的步骤永远缺牌）。
    只把 ``actor is None``（本人应答）的后续选牌步计入预留；当前 A 族没有多选牌步的流程，
    这条是为声明形状兜底，不改变既有行为。
    """
    if step.hi is not None:
        return min(step.hi, remaining)
    reserved = 0
    for later in flow.steps[index + 1 :]:
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
    for index, step in enumerate(flow.steps):
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
            take = _picks_for_step(flow, index, step, remaining)
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
            remaining = len(choices) - choice_index
            if check and remaining < step.lo:
                return answers, f"步骤 {step.key} 至少需要 {step.lo} 名目标，复合动作只给了 {remaining} 个"
            take = min(step.hi, remaining)
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
            take = _picks_for_step(flow, index, step, remaining)
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
    for index, step in enumerate(flow.steps):
        # 清单渲染器与执行器共用的通道：池 / 面回调若依赖前序答案，从这里读同一份
        # （预填的复合动作与逐步询问两条路都写它，故声明只写一次）。
        frame.data["preactivation_answers"] = dict(answers)
        if step.key in prefilled:
            answers[step.key] = prefilled.pop(step.key)
            continue
        status, value = _FLOW_STEP_RUNNERS[type(step)](
            host, frame, step, seat, answers, timing, (flow.name, index)
        )
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
            # 取消：一个副作用都不产生（提交点不落、``effect`` 不跑），技能可再发动。
            return None
        answers[step.key] = value
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
