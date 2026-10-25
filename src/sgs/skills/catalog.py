"""技能**定义目录**、载体解析与派发（票 07 §1–§3；施工票 29 落静态面、票 30 落触发面）。

一张装备 = 一个 :class:`SkillDef`（稳定 id = ``equip.<牌名 id>``），内部可带多条
:class:`Hook`（键 = 时机枚举本身）、多条 :class:`Seam`（帧内接缝）与多条
:class:`Modifier`（六个挂点）。M3 票 01 起，**武将牌**成为与装备区并列的载体
（``CarrierKind.HERO``、``SkillSourceKind.HERO_CARD``、``SkillDef.skill_type`` 的身份门控），
故同一套定义 / 派发路径服务两类载体。定义目录 :data:`CATALOG` 是**模块级不可变**的；
"谁持有哪一份"由 :func:`resolve_instances` 在派发时按**载体**
（装备区 ∪ 虚拟授予 ∪ 武将牌）解析，不做 per-player 实例注册。

**四层落位**（票 12 §4 / 票 30）：

- **钩子**（键 = ``Timing``）：【雌雄双股剑】（``USE_AFTER_DESIGNATE_TARGET``）、
  【麒麟弓】/【寒冰剑】（``DAMAGE_DEAL``）；
- **帧内接缝**（``Timing`` 里没有"需要【闪】""杀被抵消时"两节点，而派发键就是 ``Timing``）：
  【八卦阵】（``CANDIDATES`` 进"需要【闪】"窗口 + 选中才惰性压判定帧）、
  【青龙偃月刀】/【贯石斧】（``SeamKind.SLASH_CANCELED``）；
- **修饰器**（六个挂点）：诸葛连弩 / 仁王盾 / 青釭剑 / 坐骑 / 方天画戟 / 丈八蛇矛 / 八卦阵；
- **``OPS``**（多步主体）：判定后果等。

**静态 vs 触发 = 修饰器 vs 钩子**（票 07 §3，官方 `A/rule_forced.txt:5`：锁定技只是
强制性标签，不是时机 / 优先级）。``locked`` 进 :class:`SkillDef`，判据逐字取官方
"牌面有没有『可以』"；锁定 ⇒ 派发时**不问**、直接跑；非锁定 ⇒ 先发"是否发动"询问，
**锁定与"同时机优先级"无关**（不进链序）。"可以 / 必须"是**效果属性**，与 ``locked``
正交，落点是候选里有没有 ``Pass``。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeAlias

from sgs.data import (
    Color,
    Identity,
    SkillType,
    card_tag_spec,
    color_of_suit,
    hero_card_skill_ids,
    hero_skill_id,
)
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    DamageNature,
    Modifier,
    ModifierContext,
    ModifierPoint,
    PriorityClass,
    behavior_for,
    check_targets,
    is_slash,
    locate,
    priority_class_of,
)
from sgs.resolution import Control, Frame, Tag, add_tag
from sgs.state import (
    ActivateSkill,
    CardRef,
    CardTag,
    FrameKind,
    GameState,
    MoveReason,
    Player,
    SelectionSpec,
    SkillSuppressionPredicate,
    SlotKind,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .flow import (
    DeckTopArrange,
    FlowAnswers,
    PickCards,
    PickProvide,
    SkillFlow,
    card_pool_size,
    provide_face,
    run_flow,
)
from .virtual import card_ids, grouped_candidates, single_conversion

__all__ = [
    "ACTIVATION_PLANS",
    "CATALOG",
    "DAMAGE_POINT_KEY",
    "DAMAGE_RECORDED_KEY",
    "DAMAGE_DEAL_POINT_KEY",
    "DAMAGE_DEAL_RECORDED_KEY",
    "ActivationPlan",
    "Hook",
    "HookCondition",
    "HeroSkillDecl",
    "MATCH_OWNER_KEY",
    "RUNNING_SKILL_KEY",
    "SKILL_JUDGMENT_CARD_KEY",
    "Seam",
    "SeamKind",
    "SkillDef",
    "SkillInstance",
    "SkillSource",
    "SkillSourceKind",
    "activation_plan",
    "awakening_skill_awakened",
    "damage_point_index",
    "dispatch_seam",
    "dispatch_timing",
    "hero_card_zone_key",
    "instance_key",
    "is_equipped",
    "limited_skill_used",
    "lord_skill_allowed",
    "lord_skill_ids",
    "mark_skill_used_this_turn",
    "placement_skill_ids",
    "prune_card_tags",
    "prune_skill_state",
    "rejudge_skill_ids",
    "reset_turn_skill_state",
    "reset_phase_skill_state",
    "resolve_instances",
    "same_kingdom",
    "skill_used_this_turn",
    "timing_hooks_present",
]


HookHandler: TypeAlias = Callable[[Any, Frame], Control]
"""钩子回调：``(host, frame) -> Control``，与步骤函数同形（票 07 §1）。

钩子属于哪个实例由派发器临时写进 ``frame.data["skill_owner"]``；这是为了让 M3 兼容面
里冻结的 ``(host, frame)`` 签名保持不变（票 07 §6）。
"""

SeamHandler: TypeAlias = Callable[[Any, Frame], Control]
"""帧内接缝回调：与钩子同形，但键是 :class:`SeamKind` 而不是 ``Timing``。"""


class SeamKind(str, Enum):
    """**帧内接缝**的键（票 12 §Q12 / 票 30）。

    ``Timing`` 里没有"需要【闪】""杀被抵消时"两个节点（83 节点表已冻结、一个不增），
    而派发键就是 ``Timing``，故这两类效果改由**非时机的帧内接缝**承载。
    """

    SLASH_CANCELED = "SLASH_CANCELED"
    EQUIPMENT_DISCARD = "EQUIPMENT_DISCARD"
    """**装备区 → 弃牌堆**的移动事件（M6 票 08）：界的【奇才】②在它自己的帧里取消它。

    接缝只为**会进入弃牌堆的装备区移动**派发（结构过滤，见 ``Engine.move_in_frame``）；
    "谁在弃置"由载荷 :attr:`~sgs.skills.move_after.MoveEvent.causer` 给出，技能回调据此
    判"其他角色"。事件帧无效的机制符号住 :mod:`sgs.resolution.events`。"""


class SkillSourceKind(str, Enum):
    """技能实例的**来源**种类（票 07 §6 的带标签联合；M6 票 04 补第四种）。

    ``HERO_CARD``（票 01）是**武将牌**：它不是实体牌，故只为它记 ``skill_id``，
    **绝不为它造 ``CardRef``**（与虚拟装备同一姿态）。``GRANTED``（M6 票 04）是
    运行时**获得**的技能（`GLOSSARY.md`「技能来源」的第四种）：同样不是牌，
    只记 ``skill_id``，实例由 :func:`resolve_instances` 读 ``Player.granted_skills`` 产出。
    """

    EQUIPMENT_CARD = "EQUIPMENT_CARD"
    VIRTUAL_GRANT = "VIRTUAL_GRANT"
    HERO_CARD = "HERO_CARD"
    GRANTED = "GRANTED"


HookCondition: TypeAlias = Callable[[Any, Frame], bool]
"""钩子的**触发条件**：``(host, frame) -> bool``（票 07 的加法）。

``False`` ⇒ 这次时机里该钩子不成立（**不发"是否发动"询问**、不执行回调），理由与
:func:`~sgs.skills.catalog.activation_plan` 的 ``available=False`` 同姿态：
"付不起 / 不满足前提"的技能连问都不该问。``None``（缺省）= 无条件。
"""


@dataclass(frozen=True, slots=True)
class Hook:
    """一条时机钩子：键 = :class:`~sgs.timing.Timing` 枚举本身 + 可选触发条件 + 回调。

    ``condition`` 是票 07 的加法（默认 ``None``，既有的 4 条装备钩子一字不改）：【连营】/
    【枭姬】这类**条件触发**的技能若在条件不成立时仍发询问，就会在每一次牌移动上打扰玩家。

    ``per_point`` 是票 05 的加法（默认 ``False``，M3 只许增）：**触发次数是技能定义的一部分**
    （``03`` §4）。``False`` = "每次伤害事件一次"——只在伤害帧的**第一轮**有机会；
    ``True`` = "每受到 1 点伤害一次"——引擎按伤害值逐轮派发，每一轮都有机会（【遗计】）。

    ``handler`` 与 ``flow`` **恰有其一**（票 07 §4，与 ``ActiveSkill`` 同一条纪律）：
    手写回调给 ``handler``；走步骤流水线的技能（【反馈】是第一个消费者）给 ``flow``，
    由 :func:`~sgs.skills.flow.run_flow` 驱动。派发环据此二选一，声明面因此只有一份真源。
    """

    timing: Timing
    handler: HookHandler | None = None
    condition: HookCondition | None = None
    per_point: bool = False
    flow: SkillFlow | None = None

    def __post_init__(self) -> None:
        if (self.handler is None) == (self.flow is None):
            raise ValueError(f"{self.timing.value} 上的钩子必须恰有 handler / flow 之一")


@dataclass(frozen=True, slots=True)
class Seam:
    """一条帧内接缝：键 = :class:`SeamKind` + 回调（``handler`` 与 ``flow`` 恰有其一）。"""

    kind: SeamKind
    handler: SeamHandler | None = None
    flow: SkillFlow | None = None

    def __post_init__(self) -> None:
        if (self.handler is None) == (self.flow is None):
            raise ValueError(f"{self.kind.value} 上的接缝必须恰有 handler / flow 之一")


@dataclass(frozen=True, slots=True)
class SkillDef:
    """一张技能定义（票 07 §6 的冻结形状，票 30 / 票 01 只加可选字段）。

    ``carrier`` 决定优先级档（武将档 / 装备档）；``locked`` 判据逐字取官方"牌面有没有
    『可以』"；``skill_type``（票 01 的加法，默认 ``NORMAL``，既有 17 条装备定义一字不改）
    是 `03` §4「技能描述模型」的闭集 ``NORMAL`` / ``LORD``——身份门控的规范落点读它。
    ``seams`` / ``virtual_slot`` 是票 30 的加法（默认空 / ``None``，M3 只许增）。
    """

    skill_id: str
    locked: bool
    carrier: CarrierKind
    skill_type: SkillType = SkillType.NORMAL
    hooks: tuple[Hook, ...] = ()
    modifiers: tuple[Modifier, ...] = ()
    seams: tuple[Seam, ...] = ()
    virtual_slot: SlotKind | None = None
    """虚拟装备的"视为槽位"；实体装备恒 ``None``（票 07 §6 / 票 30）。"""

    rejudge_capable: bool = False
    """本技能有没有**改判能力**（票 11 §① 的加法，默认 ``False``，既有 17 条装备定义一字不改）。

    能力是**定义上的显式标记**（不是 ``skill_type`` 的取值，也不是一张会漂移的 id 名单）：
    :func:`rejudge_skill_ids` 由它派生 ``sgs.skills.REJUDGE_SKILL_IDS``，判定帧的改判
    应答者队列由 :func:`~sgs.skills.rejudge_capable_seats` 读**存活实例**的定义字段得座位集合。
    """

    rejudge_zones: tuple[Zone, ...] = (Zone.HAND,)
    """改判窗口的候选牌取自哪几个区（M6 票 14 的加法；缺省 = 一张**手牌**）。

    牌面差别：标准【鬼才】（§九 L942）「打出一张**手牌**代替之」；界【鬼才】（§十九
    L2021）「打出**一张牌**代替之」——`参考资料/界限突破-界标准包武将资料.md` 记作
    "鬼才由'一张手牌'放宽为'一张牌'"，故界版带装备区。判定帧的 ``RESPOND`` 窗口按
    :func:`~sgs.skills.rejudge_card_zones` 现算，引擎不认识任何具体技能 id。
    """

    preactivated: bool = False
    """本技能是否已迁到**预发动**（W4-1 的迁移开关，缺省 ``False``）。

    ``True`` 时引擎把该技能在出牌阶段的 ``ActivateSkill`` 骨架从 ``legal_actions`` 里删掉，
    可用性只由 ``PlayPhaseQuery.preactivations`` 表达；被动入口的清单随
    ``SkillActivationContext.preactivation`` 下发。清单一律由该技能的 ``SkillFlow.steps``
    渲染，本字段只是"哪条技能已经翻过去"的开关。本票只落机制，逐条技能由 W4-2 / W4-3 翻。
    """

    deck_arrange: DeckTopArrange | None = None
    """这条技能的**牌堆顶排列规格**（W6-02；观星是第一个消费者），没有排列时为 ``None``。

    与 ``SkillFlow`` 里那个 ``Arrange`` 步引用**同一个规格对象**（一份声明，不是两份拷贝）：
    执行器读它求张数 / 切分，接入层读本字段做投影（``ARRANGE`` 询问只带牌面与张数，
    ``_build_query`` 会丢掉载荷 —— spec S2）。``SkillDef`` 不被序列化 ⇒ 不进记录流。
    """

    requirement: str | None = None
    """这条技能**需要玩家做什么**的人话（item 4：询问文本提示）。

    与技能定义同处声明（各族 ``*_skill_definitions()`` 里紧跟该技能），由接入层
    :func:`sgs_web.projection.query_context` 在询问里原样展示。**不进记录流**（``SkillDef``
    不被序列化）⇒ 既有 Golden Replay 一行不改。缺省 ``None`` = 没有可展示的文案。
    """

    step_requirements: Mapping[str, str] | None = None
    """**多步技能**逐步的"需要做什么"（票 01）；键 = 步骤名，缺省 ``None`` = 不分步。

    分步技能的每一步各有各的问法（【离间】先"弃置1张牌"、再"选择两名男性角色…"），
    一句话的 :attr:`requirement` 盖不住。键的取法**不动询问协议**，只用询问里已有的标识：

    - ``SELECT_OPTION``：键 = 询问自带 ``skill_id`` 冒号后的那一段（``lijian:user`` → ``user``）；
    - ``SELECT_CARDS``：键 = ``"card"``（技能帧的那一步选牌）。

    与 :attr:`requirement` 同处声明、同样**不进记录流**⇒ 既有 Golden Replay 一行不改；
    查不到对应键就退回 :attr:`requirement`（旧技能一字不改）。
    """

    virtual_type_ids: tuple[str, ...] = ()
    """本技能的候选是"当某某牌"的**虚拟牌**时，那些目标牌名（转化技 / 丈八蛇矛）。

    转化候选本身不带技能归属（``UseCard`` / ``CardRef`` 是冻结报文），接入层据这个
    声明把"窗口里的虚拟牌候选"对回技能，从而展示它的 ``requirement``。取自已有的
    ``ConversionSkill.target_type_id`` / 蛇矛的目标牌名，不是第二份真源。
    """

    places_on_hero_card: bool = False
    """这条技能是否**把牌置于武将牌上**（M6 票 10；spec D14 的代码侧声明）。

    数据侧的 ``HeroSkill.placement``（可见性 / 归还时机 / 有无附加效果）与它是**同一条
    事实的两半**，加载期双向核对：代码标了而数据没写 ⇒ ``heroes.placement_missing``；
    数据写了而代码没标 ⇒ ``heroes.placement_not_registered``。由它派生的
    :data:`~sgs.skills.PLACEMENT_SKILL_IDS` 是"哪些技能会开区"的唯一名单。

    缺省 ``False``：既有 88 条定义一字不改；M6 唯一载体是界陆逊【谦逊】。
    """

    virtual_source_count: int = 1
    """这条技能的虚拟牌候选**每次消耗几张来源牌**（W4-3 的清单归属判据）。

    H 族单源转化恒 1；【丈八蛇矛】是 2。候选本身不带技能归属，故"这两张来源的虚拟牌是
    谁转出来的"只能由声明回答——张数本来就是候选构造的参数（``grouped_candidates`` 的
    ``group_size``），这里只是把它暴露出来，不是第二份判据。
    """

    per_round_limit: bool = False
    """这条技能有没有**每轮限 X 次**（M6 票 11 的加法，默认 ``False`` ⇒ 既有 88 条一字不改）。

    "轮次"是引擎状态（``GameState.round``），但**只有带每轮限次的技能会读它**：本字段是
    :func:`round_limit_present` 的唯一判据——记录流只在这种对局里落 ``turn_change``
    （没有读者时轮次不改变任何规则结果，标准 47 条金录像因此逐字节不变）。
    M6 的唯一载体是界吕蒙【博图】（X = 存活角色数且至多 3，**每次发动时重算**，见
    `06`（§5「程序化约定与偏离一手」））；每轮已用次数住
    :attr:`~sgs.state.GameState.round_use_counts`，轮次 +1 时清空。
    """

    @property
    def priority_class(self) -> PriorityClass:
        """本技能在修饰器链里的优先级档（由载体派生，不另存字段以防漂移）。"""
        return priority_class_of(self.carrier)


@dataclass(frozen=True, slots=True)
class HeroSkillDecl:
    """**武将技能声明**的公共字段：``hero_id`` + ``short_id`` ⇒ 全限定 ``skill_id``（C9）。

    十张族级声明（转化 / 目标池 / 伤害后 / 阶段体 / 主动 / 主公求助 / 救援 / 失去牌后 /
    响应张数 / 帧标签）逐字共用这一对字段与同一个派生属性：``hero_id`` 与 ``short_id``
    **总是成对出现**（Data Clump），故收成一个基类，而不是在各处复制十份 ``skill_id``。

    基类字段排在前面，故子类（同为 frozen dataclass）的构造签名里 ``hero_id`` /
    ``short_id`` 仍是前两个位置参数；属性名与调用点一字不改。
    """

    hero_id: str
    short_id: str

    @property
    def skill_id(self) -> str:
        """全限定技能 id（与数据侧共用 :func:`sgs.data.hero_skill_id` 这一处派生）。"""
        return hero_skill_id(self.hero_id, self.short_id)


@dataclass(frozen=True, slots=True)
class SkillSource:
    """技能实例的**来源**：实体装备牌 / 虚拟授予 / 武将牌（票 01 加 ``HERO_CARD``）。

    武将牌不是实体牌，故 ``HERO_CARD`` 只带 ``skill_id``（不带 ``card_id``）。
    """

    kind: SkillSourceKind
    card_id: int | None = None
    skill_id: str | None = None


@dataclass(frozen=True, slots=True)
class SkillInstance:
    """**技能实例**：``(owner, skill_id, source)`` 三元组（票 07 §6）。

    状态按实例键存（``GameState.skill_state``）、随来源清理、按 ``skill_id`` 去重
    （同 owner 同技能只留**最早存活来源**）——"两张同名装备 / 多人同技能 / 复制技能"
    因此互不串状态。
    """

    owner: int
    skill_id: str
    source: SkillSource
    definition: SkillDef


@dataclass(frozen=True, slots=True)
class ActivationPlan:
    """一次"是否发动"询问的声明式形态（票 19 §5 / §6；W6-06a 收窄）。

    ``available=False`` 表示**当前付不起** ⇒ 该技能不进候选、连询问都不构造；
    ``optional`` 决定候选里有没有 ``Pass``。成本 / 载荷**不在计划里**：它们由发动后的
    ``SkillFlow`` 步骤询问，故原先的 ``zones`` / ``card_types`` / ``min_picks`` /
    ``max_picks`` 四个无生产者字段已在 W6-06a 删除。`SkillActivationQuery` 的
    ``min_picks`` / ``max_picks`` / ``slots`` 仍是冻结报文（A-4 另立票；当前生产者恒传
    ``0 / 0 / ()``，15 份录像逐字节）。

    ``actor``（M6 票 14 的加法，缺省 ``None`` = 技能持有者）：这一问**由谁应答**。
    【护驾】②的牌面是"当**其他**魏势力角色于其回合外使用或打出【闪】时，**其**可以令你
    摸一张牌"——决定者是事件的当事角色，受益者（``skill_owner``）仍是技能持有者。它是
    座位号而不是布尔：同一个技能在不同帧上可以由不同的人决定，故取值由计划的构造方
    按帧算（:func:`~sgs.skills.lord_request.jie_hujia_activation_plan`）。
    """

    optional: bool = True
    available: bool = True
    actor: int | None = None


# ---------------------------------------------------------------------------
# 载体解析：谁持有哪一份技能（票 07 §1 / §6）
# ---------------------------------------------------------------------------


def _is_lord_skill(definition: SkillDef) -> bool:
    """这条定义是不是**主公技**（`03` §6「技能分类与过滤」的类型判据，唯一一处）。"""
    return definition.skill_type is SkillType.LORD


def lord_skill_ids(catalog: Mapping[str, SkillDef] | None = None) -> frozenset[str]:
    """``catalog`` 里全部主公技的 id；缺省读**当前**的 :data:`CATALOG`。

    这是票 01 §载体接入 #8"``LORD_SKILL_IDS`` 从手抄空集改为由 ``CATALOG`` 派生"的可测
    落点：``sgs.skills.LORD_SKILL_IDS`` 只是它的 import 期快照。传 ``catalog`` 参数让
    "派生"这条判据能对一份注入的表单独验证。
    """
    source = CATALOG if catalog is None else catalog
    return frozenset(
        definition.skill_id for definition in source.values() if _is_lord_skill(definition)
    )


def rejudge_skill_ids(catalog: Mapping[str, SkillDef] | None = None) -> frozenset[str]:
    """``catalog`` 里全部**带改判能力**的技能 id；缺省读**当前**的 :data:`CATALOG`。

    与 :func:`lord_skill_ids` 同构：``sgs.skills.REJUDGE_SKILL_IDS`` 只是本函数的
    import 期快照，"谁有改判能力"的唯一真源是 :attr:`SkillDef.rejudge_capable`；
    判定帧的座位集合由 :func:`~sgs.skills.rejudge_capable_seats` 读**存活实例**的定义
    字段得出——两者读同一字段，不各抄一份名单。传 ``catalog`` 让"派生"这条判据能对
    一份注入的表单独验证。
    """
    source = CATALOG if catalog is None else catalog
    return frozenset(
        definition.skill_id for definition in source.values() if definition.rejudge_capable
    )


def placement_skill_ids(catalog: Mapping[str, SkillDef] | None = None) -> frozenset[str]:
    """``catalog`` 里全部**放置类技能**的 id（M6 票 10；D14 的代码侧登记）。

    与 :func:`rejudge_skill_ids` 同构：``sgs.skills.PLACEMENT_SKILL_IDS`` 只是 import 期
    快照，"会不会往武将牌上放牌"的唯一真源是 :attr:`SkillDef.places_on_hero_card`；
    加载期按它核"每条放置类技能都有一份数据声明"，引擎在
    :meth:`Engine.place_on_hero_card` 入口按它 fail-closed。传 ``catalog`` 让派生可对
    一份注入的表单独验证。
    """
    source = CATALOG if catalog is None else catalog
    return frozenset(
        definition.skill_id
        for definition in source.values()
        if definition.places_on_hero_card
    )


def _identity_gates_out(definition: SkillDef, identity: Identity | None) -> bool:
    """身份门控：主公技而持有者不是主公 ⇒ 该实例**不产出**（票 01 §身份门控）。"""
    return _is_lord_skill(definition) and identity is not Identity.LORD


def lord_skill_allowed(identity: Identity | None, skill_id: str) -> bool:
    """主公技门控的**只读谓词**：`(身份 == 主公) ∧ (技能定义的类型 == 主公技)`（票 01）。

    与 :func:`_identity_gates_out` 共用 :func:`_is_lord_skill` 这一个判据，故谓词与派发
    不会各写一份而漂移。规范落点是 :func:`resolve_instances`（非主公 ⇒ 实例不产出，
    语义 = 武将牌上根本没有）；本函数只回答"能不能用"，不产出、不驱动派发，给
    `03` §6「技能分类与过滤」的**过滤链首项**与测试提供可读谓词。未知 ``skill_id``
    （如 M2 的装备技能）恒 ``False``。
    """
    definition = CATALOG.get(skill_id)
    return definition is not None and _is_lord_skill(definition) and identity is Identity.LORD


def _add_instance(
    found: list[SkillInstance], owner: int, definition: SkillDef, source: SkillSource
) -> None:
    """记一条载体命中（三种载体公用；``skill_id`` 一律取定义上的那份，不另传）。"""
    found.append(
        SkillInstance(
            owner=owner,
            skill_id=definition.skill_id,
            source=source,
            definition=definition,
        )
    )


def _skill_is_suppressed(player: Player, instance: SkillInstance) -> bool:
    """``player`` 的座位级失效记录挡不挡 ``instance``（唯一读取方）。

    ``ALL_NON_LOCKED`` 只覆盖**非锁定技**：锁定技是"满足条件即强制发动"的一类效果标记
    （官方 `A/rule_forced.txt:5`，见 :class:`SkillDef.locked` 的注释），与"能不能被失效"
    是两件事——牌面写"其所有**非锁定**技失效"，故锁定技照常产出。

    **来源过滤**（用户裁定，票 09）：失效不作用于**装备牌带来的技能**——实体装备
    （``EQUIPMENT_CARD``）与虚拟装备（``VIRTUAL_GRANT`` 且定义是装备技能）都免疫；
    武将牌（``HERO_CARD``）与运行时获得（``GRANTED``，含觉醒 ☆ 技）照常失效。

    ``VIRTUAL_GRANT`` **不是**只用于虚拟装备：既有测试构造也会用它授予武将技能，故这里
    按**定义**是否为装备技能区分，不能把整个来源一刀切。来源种类无法判断时 **fail-closed
    = 不失效**（不让未知实例被误锁）。
    """
    source = instance.source.kind
    if source is SkillSourceKind.EQUIPMENT_CARD:
        return False
    if source is SkillSourceKind.VIRTUAL_GRANT:
        if (
            instance.definition.carrier is CarrierKind.EQUIPMENT
            or instance.definition.virtual_slot is not None
        ):
            return False
        # 非装备的虚拟授予：不是"装备牌带来的技能"，继续按武将技能处理。
    elif source not in (SkillSourceKind.HERO_CARD, SkillSourceKind.GRANTED):
        return False
    return any(
        record.predicate is SkillSuppressionPredicate.ALL_NON_LOCKED
        and not instance.definition.locked
        for record in player.suppressed_skills
    )


def resolve_instances(
    state: GameState, *, include_suppressed: bool = False
) -> tuple[SkillInstance, ...]:
    """解析全部**存活实例**，按链序 ``(priority_class, skill_id, owner)`` 全序。

    载体 = 装备区里的实体牌（``source = EQUIPMENT_CARD``）∪ 座位的虚拟授予
    （``source = VIRTUAL_GRANT``，不占牌守恒）∪ **武将牌**（``source = HERO_CARD``，
    票 01：读 ``player.hero`` 经 ``sgs.data.hero_skill_ids`` 取该武将的技能清单）∪
    **获得**（``source = GRANTED``，M6 票 04：读 ``player.granted_skills``）。
    同一 ``(owner, skill_id)`` 只留**最早出现的来源**（票 07 §6 的去重口径）；
    两名角色各有同一技能时是**两个实例**。

    **解析顺序钉死**（M6 票 04 / spec §Implementation Decisions「技能运行时」）：
    载体并集 → 去重 → **减失去** → **过失效谓词**。后两步是座位级的（`Player.lost_skills`
    / `Player.suppressed_skills`）：失去的实例不再产出且不可恢复；失效的实例仍在，
    但被谓词挡在解析之外（不产出候选、不进修饰器链、不触发钩子），作用域到期即恢复。

    ``include_suppressed`` 只服务 :func:`prune_skill_state`：清 per-instance 状态时
    "暂时失效"不等于"来源消失"，把失效实例算成死实例会顺手抹掉它的"已用"计数——
    失效恢复后那条限定技就能再发动一次。默认 ``False`` = 只回答"此刻真正**在解析中**的
    实例"。

    **身份门控在派发侧的唯一兑现处**是这里（票 01 §身份门控 / `03` §6「技能分类与过滤」）：
    主公技而持有者不是主公 ⇒ **该实例不产出**——不进实例集合、不进候选、不进修饰器链、不落
    ``skill_state``、不进记录流。语义是"武将牌上根本没有这个技能"，不是"有但不生效"。
    ``player.hero is None``（无武将模式）时**根本不查武将数据**，故 M2 的输出逐字节不变。
    """
    found: list[SkillInstance] = []
    for player in state.players:
        if not player.alive:
            continue
        for card_id in player.equip:
            card = state.card(card_id)
            definition = CATALOG.get(f"equip.{card.type}")
            if definition is not None:
                _add_instance(
                    found,
                    player.seat,
                    definition,
                    SkillSource(SkillSourceKind.EQUIPMENT_CARD, card_id=card_id),
                )
        for grant in player.virtual_equipment:
            definition = CATALOG.get(grant)
            if definition is not None:
                _add_instance(
                    found,
                    player.seat,
                    definition,
                    SkillSource(SkillSourceKind.VIRTUAL_GRANT, skill_id=grant),
                )
        if player.hero is not None:
            # M6 票 06：☆ 技能**不**是武将牌载体 —— 它们只来自"获得"来源
            # （``hero_card_skill_ids`` 已排除 ``granted_by`` 非空的条目），故未觉醒时
            # "没有这个技能"是结构事实，不靠条件在派发期挡。
            for skill_id in hero_card_skill_ids(player.hero, pack_id=state.pack_id):
                definition = CATALOG.get(skill_id)
                if definition is not None:
                    _add_instance(
                        found,
                        player.seat,
                        definition,
                        SkillSource(SkillSourceKind.HERO_CARD, skill_id=skill_id),
                    )
        # 获得排在最后：同 `(owner, skill_id)` 的去重留**最早**来源，故"武将牌上本来就有"
        # 的那一份优先于后来又授予的同名技能（获得一份已有的技能不改变实例身份）。
        for skill_id in player.granted_skills:
            definition = CATALOG.get(skill_id)
            if definition is not None:
                _add_instance(
                    found,
                    player.seat,
                    definition,
                    SkillSource(SkillSourceKind.GRANTED, skill_id=skill_id),
                )
    deduped: list[SkillInstance] = []
    seen: list[tuple[int, str]] = []
    for instance in found:
        if _identity_gates_out(instance.definition, state.player(instance.owner).identity):
            continue
        key = (instance.owner, instance.skill_id)
        if key in seen:
            continue
        seen.append(key)
        deduped.append(instance)
    alive: list[SkillInstance] = []
    for instance in deduped:
        player = state.player(instance.owner)
        if instance.skill_id in player.lost_skills:
            continue
        if not include_suppressed and _skill_is_suppressed(player, instance):
            continue
        alive.append(instance)
    alive.sort(key=lambda item: (int(item.definition.priority_class), item.skill_id, item.owner))
    return tuple(alive)


def timing_hooks_present(state: GameState, timing: Timing) -> bool:
    """当前局面有没有注册在 ``timing`` 上的技能钩子（票 10 的**帧外阶段体懒守卫**）。

    阶段体（``PHASE_PREPARE`` / ``PHASE_DRAW`` / ``PHASE_FINISH``）没有结算帧；引擎据本
    谓词决定"要不要为它建一个钩子帧"。无武将模式与"这套技能还没实现"两种情形都返回
    ``False``，故 M2 的记录流与 34 条基准逐字节不变——代价为零（与 ``move_after_providers``
    对"失去牌后"的惰性守卫同一姿态）。
    """
    return any(
        hook.timing is timing
        for instance in resolve_instances(state)
        for hook in instance.definition.hooks
    )


def instance_key(instance: SkillInstance) -> str:
    """实例键的规范文本（``GameState.skill_state`` 的键）。"""
    source = instance.source
    ref = source.card_id if source.card_id is not None else source.skill_id
    return f"{instance.owner}:{instance.skill_id}:{source.kind.value}:{ref}"


def round_limit_present(state: GameState, catalog: Mapping[str, SkillDef] | None = None) -> bool:
    """本局此刻有没有「每轮限 X 次」的**载体**（M6 票 11）。

    轮次变化只在有读者时进记录流（见 :class:`~sgs.state.TurnChangeDelta`）：没有载体的对局
    里轮次不改变任何规则结果，记录流保持与 M5 逐字相同 ⇒ 标准 47 条金录像逐字节不变。
    判据读**存活实例**的定义字段（:attr:`SkillDef.per_round_limit`），不读一张会漂移的
    名单；**暂时失效也算**（``include_suppressed=True``）——失效恢复后它照样要读轮次。
    ``catalog`` 入参只为让机械检查注入合成定义；缺省读**当前**的 :data:`CATALOG`。
    """
    source = CATALOG if catalog is None else catalog
    return any(
        source[instance.skill_id].per_round_limit
        for instance in resolve_instances(state, include_suppressed=True)
        if instance.skill_id in source
    )


def hero_card_zone_key(owner: int, skill_id: str) -> str:
    """**武将牌上的牌区的键**（M6 票 10）：（座位, 技能实例）的规范文本。

    ``owner`` = **放置技能实例的持有者**（不是扣牌的宿主座位——宿主由 ``Player`` 回答）。
    实例身份取 ``(owner, skill_id)``：载体解析对同一 owner 同一技能只留**一个**实例
    （``resolve_instances`` 的去重口径），故这就是"技能实例"的规范身份；**不含来源**
    （``instance_key`` 的第三段）——规则 15.2 明说失去技能不影响武将牌上的牌，区不随
    来源种类 / 存亡改键。
    """
    return f"{owner}:{skill_id}"


# ---------------------------------------------------------------------------
# per-instance 状态的**位布局**（票 07 §6 的键 + M6 票 05 的"整局已用"位）
# ---------------------------------------------------------------------------

_GIVEN_TO_SHIFT = 6
"""「本阶段已给过的目标座位」位的起始位（M6 票 16；位 6..13 = 座位 0..7）。

低 6 位已被 A 族三个阶段位 + 限定技整局位 + 觉醒位 + 每回合已用位占满，故从第 6 位起。
内建上限 8 座（``RuleConfig.player_count`` 的闭集为 2/4/5/6/7/8），与这里一致。
"""

_GIVEN_TO_MASK = 0xFF << _GIVEN_TO_SHIFT
"""界刘备【仁德】的"本阶段已给过这些座位"位段（座位号 = 位序号 - ``_GIVEN_TO_SHIFT``）。"""

_USED_SECOND = 1 << 14
"""**本出牌阶段"第二次"已用**位（M6 票 18；界华雄【势斩】"出牌阶段限两次"）。

位 14 是 :data:`_GIVEN_TO_MASK`（6..13）之后的下一个空闲位。它与 A 族的 ``_USED``
**成对**读出"本阶段用了几次"：两者都落 = 2 次，只落 ``_USED`` = 1 次。作用域仍是
**本出牌阶段**，故并入 :data:`_PHASE_STATE_MASK`；没有"限两次"的技能从不写这一位。
"""

_PHASE_STATE_MASK = 0b111 | _GIVEN_TO_MASK | _USED_SECOND
"""``skill_state`` 里属于**本出牌阶段**的位（A 族的 ``_USED`` / ``_RECOVERED`` /
``_GAVE_ANY`` + 界刘备【仁德】的目标座位位段 + 界华雄【势斩】的"第二次已用"位，
见 :mod:`sgs.skills.active`）。

:func:`reset_phase_skill_state` 只清这一掩码里的位：M6 票 05 起 ``skill_state`` 还有
**跨阶段**的位（限定技的"整局已用"），整段 ``clear()`` 会让限定技复活。
``tests/test_hero_active.py` 有一条机械检查钉住"掩码 == 上述几段位的并集"。

M6 票 16 起同一个掩码还含**界刘备【仁德】的"本阶段已给过的目标座位"**位
（:data:`_GIVEN_TO_MASK`）：它同样是"本出牌阶段"的作用域——每位角色在本阶段只能被
仁德指定一次，下个出牌阶段重新可给。M6 票 18 起还含**界华雄【势斩】的第二次已用位**
（:data:`_USED_SECOND`）。
"""

_LIMITED_USED = 0b1000
"""**限定技整局已用**位（M6 票 05；§四 L350–L355「限定技：整局游戏中只能发动一次」）。

与 A 族的阶段位同处一个整数字段（票 04 预留的"已用"键位），但**永不**被出牌阶段复位；
随实例键一起：来源消失（``prune_skill_state``）才清，暂时失效不清（``include_suppressed``）。
"""


_AWAKENED = 0b1_0000
"""**觉醒技已觉醒**位（M6 票 06；§四 4.12「觉醒技：满足条件时强制发动，通常为一次性转变」）。

与 A 族的阶段位（``_PHASE_STATE_MASK``）和限定技的"已用"位（``_LIMITED_USED``）同处
一个整数字段、**互不重叠**：出牌阶段复位只清阶段位，觉醒位跨整局保留，只有实例真的
消失（``prune_skill_state``）才随键清掉。
"""


_TURN_USED = 0b10_0000
"""**每回合限一次**的"本回合已用"位（M6 票 08；界夏侯惇【清俭】的第一个消费者）。

作用域 = **一个游戏回合**（不是"自己的出牌阶段"）：清俭可以在**别人的回合**里由"获得牌"
触发，故它不能复用 A 族的阶段位（``_PHASE_STATE_MASK``）——那些位在 ``PHASE_PLAY_START``
就清了。位与其它三段互不重叠；复位走 :func:`reset_turn_skill_state`（引擎的
``_clear_turn_effects`` 统一调用），整局位 / 觉醒位不受影响。
"""


def _instance_used_this_turn(state: GameState, instance: SkillInstance) -> bool:
    """``instance`` 的**本回合已用**位（只看位，不解析实例）。"""
    return bool(state.skill_state.get(instance_key(instance), 0) & _TURN_USED)


def _mark_used_this_turn(state: GameState, instance: SkillInstance) -> None:
    """给实例落"本回合已用"位（非"每回合限一次"的技能由调用方自己保证不落）。"""
    key = instance_key(instance)
    state.skill_state[key] = state.skill_state.get(key, 0) | _TURN_USED


def skill_used_this_turn(state: GameState, seat: int, skill_id: str) -> bool:
    """``seat`` 的 ``skill_id`` 本回合是否已经发动过（M6 票 08 的只读面）。

    与 :func:`limited_skill_used` / :func:`awakening_skill_awakened` 同姿态：按存活实例
    （``include_suppressed=True``——失效 ≠ 没用过）读 :data:`_TURN_USED` 位。没有这条
    实例时恒 ``False``。
    """
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            return _instance_used_this_turn(state, instance)
    return False


def mark_skill_used_this_turn(state: GameState, seat: int, skill_id: str) -> None:
    """给 ``seat`` 的 ``skill_id`` 落"本回合已用"位（技能层调用；实例不在则零动作）。"""
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            _mark_used_this_turn(state, instance)
            return


def reset_turn_skill_state(state: GameState) -> None:
    """清理**本回合**的 per-instance 技能状态（M6 票 08；引擎回合末统一调用）。

    只清 :data:`_TURN_USED` 位：整局位（限定技"已用"）与觉醒位保留，"每回合限一次"的
    技能因此每个游戏回合都能重新发动一次。与 :func:`reset_phase_skill_state` 同形
    （按掩码清、键清空即删）。
    """
    for key in list(state.skill_state):
        remaining = state.skill_state[key] & ~_TURN_USED
        if remaining:
            state.skill_state[key] = remaining
        else:
            del state.skill_state[key]


# ---------------------------------------------------------------------------
# 「每轮限 X 次」的计数（M6 票 11 的界吕蒙【博图】）
# ---------------------------------------------------------------------------


def round_skill_uses(state: GameState, seat: int, skill_id: str) -> int:
    """``seat`` 的 ``skill_id`` **本轮**已发动几次（M6 票 11 的只读面）。

    与 :func:`skill_used_this_turn` 同姿态：按存活实例（``include_suppressed=True``
    ——失效 ≠ 没用过）取实例键、读 :attr:`~sgs.state.GameState.round_use_counts`。
    没有这条实例时恒 ``0``。**上限 X 不在本函数里**——"每轮限几次"是牌面事实
    （【博图】是"存活角色数且至多 3"，每次发动时重算），由技能自己的 ``available`` 判。
    """
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            return state.round_use_counts.get(instance_key(instance), 0)
    return 0


def mark_round_skill_used(state: GameState, seat: int, skill_id: str) -> None:
    """给 ``seat`` 的 ``skill_id`` 记一次**本轮已发动**（技能层调用；实例不在则零动作）。"""
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            key = instance_key(instance)
            state.round_use_counts[key] = state.round_use_counts.get(key, 0) + 1
            return


def reset_round_skill_state(state: GameState) -> None:
    """清空**本轮**的每轮次数（M6 票 11；引擎在轮次 +1 时调用，唯一复位点）。

    计数本身就是"本轮"的，故整段清空；**额外回合不经过这里**——这正是"额外回合不会
    刷新每轮限 X 次"的机械保证（见 :meth:`~sgs.engine.Engine._advance_turn`）。
    """
    state.round_use_counts.clear()


def _instance_awakened(state: GameState, instance: SkillInstance) -> bool:
    """``instance`` 的**已觉醒**位是否已落（只看位，不解析实例）。"""
    return bool(state.skill_state.get(instance_key(instance), 0) & _AWAKENED)


def _mark_awakened(state: GameState, instance: SkillInstance) -> None:
    """给**觉醒技**实例落"已觉醒"位（非觉醒技零动作 ⇒ 既有技能不写 ``skill_state``）。"""
    if instance.definition.skill_type is not SkillType.AWAKENING:
        return
    key = instance_key(instance)
    state.skill_state[key] = state.skill_state.get(key, 0) | _AWAKENED


def _instance_used_once(state: GameState, instance: SkillInstance) -> bool:
    """``instance`` 的**已用**位是否已落（只看位，不解析实例；限定技的读取方专用）。"""
    return bool(state.skill_state.get(instance_key(instance), 0) & _LIMITED_USED)


def _mark_used_once(state: GameState, instance: SkillInstance) -> None:
    """给**限定技**实例落"整局已用"位（非限定技**零动作** ⇒ 既有技能不写 ``skill_state``）。"""
    if instance.definition.skill_type is not SkillType.LIMITED:
        return
    key = instance_key(instance)
    state.skill_state[key] = state.skill_state.get(key, 0) | _LIMITED_USED


def limited_skill_used(state: GameState, seat: int, skill_id: str) -> bool:
    """``seat`` 的 ``skill_id`` 这条**限定技**整局是否已经发动过（M6 票 05 的只读面）。

    判据 = 该 ``(owner, skill_id)`` 的存活实例在 ``skill_state`` 里的 :data:`_LIMITED_USED`
    位。用 ``include_suppressed=True`` 解析：**失效 ≠ 来源消失**，暂时被挡住的限定技照样
    "已经用过"（否则失效恢复后能再发动一次）。没有这条实例 / 不是限定技时恒 ``False``。
    """
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            return _instance_used_once(state, instance)
    return False


def awakening_skill_awakened(state: GameState, seat: int, skill_id: str) -> bool:
    """``seat`` 的 ``skill_id`` 这条**觉醒技**是否已经觉醒过（M6 票 06 的只读面）。

    判据 = 该 ``(owner, skill_id)`` 的存活实例在 ``skill_state`` 里的
    :data:`_AWAKENED` 位。与 :func:`limited_skill_used` 同姿态：用
    ``include_suppressed=True`` 解析（**失效 ≠ 未觉醒**——暂时被挡住不复活觉醒）；
    没有这条实例 / 不是觉醒技时恒 ``False``。
    """
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.owner == seat and instance.skill_id == skill_id:
            if instance.definition.skill_type is not SkillType.AWAKENING:
                return False
            return _instance_awakened(state, instance)
    return False


def same_kingdom(state: GameState, a: int, b: int) -> bool:
    """``a`` 与 ``b`` 是不是**同一势力**（票 09 §要做什么 1 的只读小工具）。

    势力作用域**不建字段**：一切"其他同势力角色"都落成"回调自己比座位"——回调拿
    ``ctx.owner``（技能持有者）/ ``ctx.subject``（本次决策者）或 ``frame.data["source"]``
    （事件来源）来调本函数。判据 = 两侧的 ``Player.kingdom`` 都已发牌（非 ``None``）**且相等**；
    无武将模式（势力未知）恒 ``False``，故 M2 的记录流一条不变。

    "其他"由调用方写 ``a != b``：本函数只回答"势力相同"，不替调用方决定作用域
    （【黄天】式注入里持有者可能自己也在候选面上）。``same_kingdom(state, s, s)``
    对**有势力**的座位是 ``True``。
    """
    left = state.player(a).kingdom
    right = state.player(b).kingdom
    return left is not None and left is right


def prune_card_tags(state: GameState) -> list[tuple[int, object]]:
    """清理**来源实例已消失**的牌标签（M6 票 09；D12 的第四项声明）。

    判据 = 标签类型声明 ``disappears_with_source`` ∧ 它的来源实例
    ``(owner, skill_id)`` 不在 :func:`resolve_instances` 的结果里。

    **"失效"也算来源消失**：:func:`resolve_instances` 默认扣掉被失效谓词挡住的实例，
    故失效【洛神】时标签一并清除、计数立刻恢复（票 09 的验收）。这与
    :func:`prune_skill_state` 的"暂时失效不算来源消失"**方向相反**，因为两者问的不是
    同一件事：per-instance 状态（"限定技已用"）在失效恢复后必须还在；而标签是连续型
    效果的产物，来源查不到就不再成立。

    没有声明 ``disappears_with_source`` 的类型（遗珠式"到被使用 / 打出为止"）不在这里被
    清：它的到期由自己的清除触发（:meth:`~sgs.engine.Engine.clear_card_tags`）回答。

    没有标签时**零动作**（先短路再看实例）：这个方法随每次牌移动都会跑，常规局里
    ``card_tags`` 恒空，前缀守卫让"没标签"的路径不付第二次 ``resolve_instances``。

    返回值 = 被清掉的 ``(card_id, CardTag)`` 列表（按清除顺序）：调用方（引擎）据此在
    记录流里落 ``tag_remove``（M6 票 20）。纯状态函数仍不碰记录流。
    """
    if not state.card_tags:
        return []
    live = {
        (instance.owner, instance.skill_id)
        for instance in resolve_instances(state)
    }
    removed: list[tuple[int, object]] = []
    for card_id in list(state.card_tags):
        tags = state.card_tags[card_id]
        gone = tuple(
            tag
            for tag in tags
            if card_tag_spec(tag.tag_type).disappears_with_source
            and (tag.owner, tag.skill_id) not in live
        )
        if not gone:
            continue
        kept = tuple(tag for tag in tags if tag not in gone)
        if kept:
            state.card_tags[card_id] = kept
        else:
            del state.card_tags[card_id]
        removed.extend((card_id, tag) for tag in gone)
    return removed


def prune_skill_state(state: GameState) -> None:
    """清理**来源已消失**的 per-instance 状态（票 07 §6 的"随 source 清理"）。

    ``include_suppressed=True``：暂时失效的实例**仍算活着**（M6 票 04）——把它的
    ``skill_state`` 一起清掉，会让"失效恢复后限定技又能再发动一次"。这里只清
    "实例真的没了"（载体消失 / 已失去）的那些键。
    """
    live = {
        instance_key(instance)
        for instance in resolve_instances(state, include_suppressed=True)
    }
    for key in list(state.skill_state):
        if key not in live:
            del state.skill_state[key]


def reset_phase_skill_state(state: GameState) -> None:
    """清理**本出牌阶段**的 per-instance 技能状态（票 04 §要做什么 2；M6 票 05 收窄）。

    与"座位自己的 ``TURN_START`` 清空 ``card_use_counts``"同一重置口径（票 07 §6），
    只是节拍落在 ``PHASE_PLAY_START``。票 04 时 ``skill_state`` 的**唯一写入者**是 A 族
    "出牌阶段限一次"（含【仁德】"首次达两张"）的计数，故整段 ``clear()`` 就是复位；
    M6 票 05 起同一个字段里还有**跨阶段**的位（限定技的"整局已用"，:data:`_LIMITED_USED`），
    整段清空会让【替身】下一回合又能发动一次。故这里改成**按作用域掩码**清：
    只清 :data:`_PHASE_STATE_MASK` 里的阶段位，其余位（以及只剩这些位的键）保留。
    """
    for key in list(state.skill_state):
        remaining = state.skill_state[key] & ~_PHASE_STATE_MASK
        if remaining:
            state.skill_state[key] = remaining
        else:
            del state.skill_state[key]


def is_equipped(state: GameState, seat: int, slot: SlotKind) -> bool:
    """``seat`` **已装备** ``slot`` 吗：实体装备 ∪ 虚拟授予（票 07 §6 / 票 30）。

    实体侧读装备区里的牌面槽位；虚拟侧读授予技能的 ``virtual_slot``。两者并集，
    故 M3 的【八阵】类"视为装备"能与实体装备共存判定，而虚拟装备不占牌守恒。
    """
    for card_id in state.player(seat).equip:
        if state.card(card_id).slot is slot:
            return True
    for grant in state.player(seat).virtual_equipment:
        definition = CATALOG.get(grant)
        if definition is not None and definition.virtual_slot is slot:
            return True
    return False


# ---------------------------------------------------------------------------
# 派发：钩子 / 接缝 / 发动询问（票 07 §1 / 票 19 §5·§8）
# ---------------------------------------------------------------------------


MATCH_OWNER_KEY = "hook_owner"
"""帧私有槽名：**条件求值期**当前实例的持有者（票 14 §2）。

``Hook.condition(host, frame)`` 拿不到实例，而条件又必须能按"谁的牌动了"过滤
（【连营】/【枭姬】的"**你**失去…"）——故 :func:`_matching_hooks` 在求值前把持有者写进
这个槽。它与 :data:`skill_owner`（**回调执行期**才写）分开，是为了不改变既有条件的
求值时机；票 13 起所有"关于我"的条件（【反馈】【流离】【连营】【枭姬】…）都读这个槽
自校持有者，表驱动机械检查见 ``tests/test_hero_catalog.py`` §⑤。
"""


RUNNING_SKILL_KEY = "running_skill"
"""帧私有槽名：**正在执行**的钩子 / 接缝所属技能 id（code-review 复核）。

帧私有数据不进记录流，故对既有基准逐字节无影响。它服务的是"移动的重入判据"：
同一帧里两个不同提供者可以发出**参数逐字段相同**的移动（多点伤害里【遗计】把一张牌
交给郭嘉、【反馈】又拿郭嘉那张——同一张牌、同一个目标区、同一帧内序号），此时唯一能
区分"同一次移动的重入"与"另一次移动"的就是**是哪个技能在跑**。派发环在调每个 handler
前写它，重入时同一个 handler 会写回同一个值，故它是稳定的重入坐标。
"""


SKILL_JUDGMENT_CARD_KEY = "skill_judgment_card"
"""帧私有槽名：技能触发的一次判定**结算后**，那张判定牌本身回写进发起帧（M6 票 01）。

引擎一直把判定结果回写成 ``skill_result``（红 = 成功，供【八卦阵】/【铁骑】读）；
【刚烈】的"不为红桃"需要**花色**（方块是红色但不终止），布尔读不出来，故同一次回写
再带上判定牌。帧私有数据不进记录流，既有基准逐字节不受影响；读它的技能用后即弹掉，
避免随 ``dict(frame.data)`` 流进子帧被别的窗口误读。
"""


def _matching_hooks(instance: SkillInstance, timing: Timing, host: Any, frame: Frame) -> tuple[Hook, ...]:
    """本实例在 ``timing`` 上**条件成立**的钩子（条件缺省 = 恒成立；票 07 的 ``condition``）。

    ``per_point``（票 05）在这里过滤"逐点"触发次数：``False`` 的钩子只在伤害帧的**第一轮**
    有机会（:func:`damage_point_index` 为 0），``True`` 的每一轮都有机会。

    求值前把 ``instance.owner`` 写进 :data:`MATCH_OWNER_KEY`（票 14 §2）：条件据此判
    "失去牌的是不是持有者本人"，而 ``skill_owner`` 的写入时机（回调前）保持不变。
    """
    frame.data[MATCH_OWNER_KEY] = instance.owner
    return tuple(
        hook
        for hook in instance.definition.hooks
        if hook.timing is timing
        and (hook.per_point or damage_point_index(frame) == 0)
        and (hook.condition is None or hook.condition(host, frame))
    )


def _matching_seams(instance: SkillInstance, kind: SeamKind) -> tuple[Seam, ...]:
    return tuple(seam for seam in instance.definition.seams if seam.kind is kind)


def _instances_for(
    state: GameState, extra_instances: tuple[SkillInstance, ...]
) -> tuple[SkillInstance, ...]:
    """本次派发的实例集合 = 存活实例 ∪ **这一次的临时实例**（M5-05a）。

    临时实例是"刚刚失去的装备"（:func:`~sgs.skills.move_after.lost_equipment_instances`）：
    它们已经不在装备区、故不在 :func:`resolve_instances` 的集合里，但这一次 ``MOVE_AFTER``
    仍要替它们派发。合并后仍按 ``(priority_class, skill_id, owner)`` 全序；同一
    ``(owner, skill_id)`` 以存活实例为准（临时实例不重复派发）。身份门控对临时实例同样生效
    （与 :func:`resolve_instances` 同一条判据）。
    """
    instances = list(resolve_instances(state))
    if not extra_instances:
        return tuple(instances)
    seen = {(instance.owner, instance.skill_id) for instance in instances}
    for instance in extra_instances:
        if _identity_gates_out(instance.definition, state.player(instance.owner).identity):
            continue
        key = (instance.owner, instance.skill_id)
        if key in seen:
            continue
        seen.add(key)
        instances.append(instance)
    instances.sort(
        key=lambda item: (int(item.definition.priority_class), item.skill_id, item.owner)
    )
    return tuple(instances)


def _dispatch(
    host: Any,
    frame: Frame,
    *,
    match: Any,
    timing: Timing,
    key: str,
    extra_instances: tuple[SkillInstance, ...] = (),
) -> Control:
    """钩子与接缝共用的派发环（唯一一份）：强制类直接跑，其余先问"是否发动"。

    **只有答 ``ActivateSkill`` 才执行**——``Pass``（不发动）与 ``plan.available=False``
    （付不起）都跳过；这是"非锁定技的发动是询问出来的"这条规则（`GLOSSARY.md` 的**发动**）
    的唯一落点。暂停 = ``"stay"`，重入时整条链从头跑，但 `ask` 的应答按帧内键缓存。

    **强制类 = 锁定技 ∪ 觉醒技**（§四 4.12：两者都是"满足条件即强制发动，不可选择不
    发动"）。觉醒技**不并进 ``locked``**：``locked`` 是"锁定技"这一规则类型，同时被
    "其所有非锁定技失效"的谓词读取；觉醒技是独立的类型（``SkillType.AWAKENING``），
    故仍可被座位级失效挡住。两者的共同点只在"派发时不问"——本函数按类型读它，不另立
    ``mandatory`` 字段（同一事实只留一处声明）。

    **觉醒技整局一次转变**：已觉醒 ⇒ 不再派发（同限定技的"已用"门）。位在
    :func:`_mark_awakened`，落点在**执行完**之后——effect 挂起（如【勤学】的选项询问）
    时重入必须还能续跑同一个实例。

    ``extra_instances``（M5-05a）只由"失去牌后"的派发传入：刚离开装备区的装备技能仍是
    这一次移动的提供者（见 :func:`_instances_for`）。
    """
    for instance in _instances_for(host.state, extra_instances):
        items = match(instance)
        if not items:
            continue
        limited = instance.definition.skill_type is SkillType.LIMITED
        awakening = instance.definition.skill_type is SkillType.AWAKENING
        if limited and _instance_used_once(host.state, instance):
            # 限定技**整局一次**（§四 L350–L355）：已用 ⇒ 不再产出候选、不再发"是否发动"
            # 询问、不进记录流的询问视角。判定放在"有东西可派发"之后，故普通技能的
            # 每一条派发路径一字不改；"整局已用"与 locked / 非锁定正交（这条门在提问之前）。
            continue
        if awakening and _instance_awakened(host.state, instance):
            # 觉醒技是**一次性转变**：已觉醒 ⇒ 不再派发（与限定技的"已用"同一位置）。
            continue
        forced = instance.definition.locked or awakening
        if not forced:
            plan = activation_plan(host.state, frame, instance)
            if not plan.available:
                continue
            active = host.ask_skill_activation(frame, instance, timing, plan, key=key)
            if host.suspended(active):
                return "stay"
            if not isinstance(active, ActivateSkill):
                continue
        for item in items:
            frame.data["skill_owner"] = instance.owner
            # 移动的重入判据要认"哪个技能在跑"（两个提供者可以发出参数相同的移动）；
            # 重入时同一个 handler 写回同一个值，故它稳定。
            frame.data[RUNNING_SKILL_KEY] = instance.skill_id
            control = _run_item(host, frame, item, instance, timing)
            if control is not None:
                return control
        if limited:
            # "已用"位落**执行完之后**：effect 挂起时（如【替身】压回复帧）重入必须还能
            # 走到这里续跑同一个实例，故不能先落位再判（先落位会让重入把实例自己挡掉）。
            _mark_used_once(host.state, instance)
        if awakening:
            # 同姿态：觉醒位也落执行完之后（【勤学】的选项询问会让第一轮挂起）。
            _mark_awakened(host.state, instance)
    return None


def _run_item(host: Any, frame: Frame, item: Any, instance: SkillInstance, timing: Timing) -> Control:
    """跑一条钩子 / 接缝：``flow`` 非空走流水线，否则调手写回调（票 07 §4）。

    **被动侧的两处显式传参**（票 07 §2）：``owner`` 取 ``instance.owner``——被动技的帧里
    ``data["source"]`` 是**伤害来源**（【反馈】）/ 牌的使用者（【流离】），不是技能持有者；
    ``timing`` 取派发环当前持有的那个（``dispatch_seam`` 传的是**接缝语境**的时机）。
    二者都不是执行器里的特判，而是"谁在跑、在哪个时机跑"这两条事实的显式传递。
    """
    if item.flow is not None:
        # 被动侧的持有者**显式**传：帧里的 ``source`` 是伤害来源（【反馈】）/ 使用者
        # （【流离】），不是技能持有者。``owner=None``（未指定）时执行器才读 ``source``。
        return run_flow(host, frame, item.flow, owner=instance.owner, timing=timing)
    assert item.handler is not None  # __post_init__ 已保证恰有其一
    return item.handler(host, frame)


def dispatch_timing(
    host: Any,
    frame: Frame,
    timing: Timing,
    *,
    extra_instances: tuple[SkillInstance, ...] = (),
) -> Control:
    """在 ``timing`` 上按链序派发钩子；**第一个返回非 ``None`` 的钩子即短路**。

    答案缓存键带上 :func:`~sgs.skills.damage_after.damage_point_index`（票 05）：逐点触发的
    时机里"第 1 点"与"第 2 点"是**两个**时机实例，非锁定技在每一轮各自问一次"是否发动"；
    其余时机里该序号恒为 0，键与既有形态逐字相同（M2 基准不受影响）。

    ``extra_instances``（M5-05a）：本次派发额外参与的**临时实例**（失去装备派发用）；
    缺省为空 ⇒ 既有调用点的行为与集合逐字不变。
    """
    point = damage_point_index(frame)
    return _dispatch(
        host,
        frame,
        match=lambda instance: _matching_hooks(instance, timing, host, frame),
        timing=timing,
        key=timing.value if point == 0 else f"{timing.value}:{point}",
        extra_instances=extra_instances,
    )


def dispatch_seam(host: Any, frame: Frame, kind: SeamKind) -> Control:
    """在帧内接缝 ``kind`` 上派发；键是接缝名而**不是时机**（票 30）。

    发动询问仍发生在当前帧所处的时机里（接缝都嵌在 C2），故 ``timing`` 只作询问记录的
    语境；**答案缓存键用接缝名**，与同时机上的真钩子互不串味。
    """
    return _dispatch(
        host,
        frame,
        match=lambda instance: _matching_seams(instance, kind),
        timing=Timing.RESOLVE_BEFORE_EFFECT,
        key=f"seam:{kind.value}",
    )


# ---------------------------------------------------------------------------
# 发动形态（票 19 §5 / §6）：付不起的不进候选
# ---------------------------------------------------------------------------


def _default_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    del state, frame, instance
    return ActivationPlan()


AXE_SKILL_ID = "equip.axe"
"""【贯石斧】的技能 id（``equip.`` + 牌名 id）。

只在 :func:`_axe_own_card` 里用来把"这条装备技的实例"从 ``resolve_instances`` 里认出来，
**不是**牌名白名单——排除的是那张**装备牌的 id**（``SkillInstance.source.card_id``）。
"""


def _axe_own_card(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[int, ...]:
    """【贯石斧】成本步的**实体牌黑名单** = 这张技能对应的装备牌自己（W4-fix-3）。

    用户裁定（**偏离官方 FAQ**，见 `开发文档/06` §5「程序化约定与偏离一手」）：贯石斧**不能弃置自身**。身份取
    ``SkillInstance.source.card_id`` —— 即"这条装备技挂在哪张装备牌上"，**不写死牌名**：
    实例解析与技能生效读的是同一份真源，牌名 / 花色 / 牌 id 都不参与判据。
    """
    del frame, answers
    return tuple(
        int(instance.source.card_id)
        for instance in resolve_instances(state)
        if instance.owner == owner
        and instance.skill_id == AXE_SKILL_ID
        and instance.source.card_id is not None
    )


def _axe_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    """【贯石斧】：只有**自己的**【杀】被抵消、且**付得起两张牌**时才发动。

    票 09 起发动询问是**裸"是否发动"**（``available`` 保留）：成本（弃两张）由发动后的
    一步 ``PickCards`` 问，候选因此不再枚举 C(手牌 + 装备, 2) 张两张组合。判据 =
    :func:`~sgs.skills.flow.card_pool_size` 对 :data:`AXE_FLOW` 那一步此刻铺出的
    **过滤后**牌数 ≥ 2（与成本步**同源**，不写死）。

    W4-fix-3：那一步的池**排除武器自己**（:func:`_axe_own_card`，用户裁定、偏离官方
    FAQ）——所以"手里 1 张 + 武器自己"现在是**付不起**，连询问都不发。
    """
    step = AXE_FLOW.steps[0]
    assert isinstance(step, PickCards)  # 声明形状是编译期事实，写错要立刻炸
    available = (
        frame.data.get("source") == instance.owner
        and card_pool_size(state, instance.owner, step, frame=frame, owner=instance.owner) >= 2
    )
    return ActivationPlan(optional=True, available=available)


def _green_dragon_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    """【青龙偃月刀】：只有**自己的**【杀】被抵消、且**提供得起**一张【杀】时才发动。

    票 09 起发动询问是**裸"是否发动"**；票 14 起这一步问的是"**提供一张【杀】**"
    （``PickProvide``），故"付得起"的判据与那一步的候选面**同源**
    （:func:`provide_face`）：手里的实体【杀】（M5-05b-fix 起含火【杀】/ 雷【杀】——按
    【杀】族判据，见 :func:`~sgs.effects.satisfies_required`）∨ **任一转化技能此刻能转出的
    虚拟【杀】**（赵云的【闪】、【武圣】的红牌）。少了虚拟那一半，只有【闪】的赵云就
    **连询问都不发**——正是用户实机"青龙追问时想点龙胆却没有入口"的那一幕。
    """
    available = frame.data.get("source") == instance.owner and bool(
        provide_face(state, instance.owner, "slash", (Zone.HAND,))[0]
    )
    return ActivationPlan(optional=True, available=available)


def _double_sword_triggered(state: GameState, frame: Frame) -> bool:
    """【雌雄双股剑】该不该管**这次使用**：来源用一张【杀】指定了异性目标（M5-bugfix）。

    判据只看**使用**（``frame.data["card"]``）的牌型是不是【杀】族——含普通 / 火 / 雷与
    转化、虚拟产物（:func:`~sgs.effects.is_slash`）；**不是**牌名白名单。火攻等普通锦囊
    因此不得进那条选择（实机缺陷：双股剑对火攻也开出了"弃一张 / 令使用者摸一张"）。

    发动形态（``_double_sword_plan``）与钩子体（``_double_sword``）共用本函数：前者决定
    "要不要问是否发动"，后者在真正执行时再核一次同一条前提（锁定 / 直调路径也不漏）。
    """
    card = frame.data.get("card")
    source = frame.data.get("source")
    if source is None or card is None or not is_slash(behavior_for(card)):
        return False
    targets = tuple(frame.data.get("targets", ()))
    return any(_opposite_genders(state, source, target) for target in targets)


def _double_sword_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    available = frame.data.get("source") == instance.owner and _double_sword_triggered(
        state, frame
    )
    return ActivationPlan(optional=True, available=available)


def _ice_sword_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    del state
    available = frame.data.get("source") == instance.owner and _is_owner_slash_damage(
        frame, instance
    )
    return ActivationPlan(optional=True, available=available)


def _tieji_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    """马超【铁骑】（票 08）：只在**自己使用【杀】**的那次使用上问"是否发动"。

    与 :func:`_double_sword_plan` / :func:`_ice_sword_plan` 同姿态：非锁定技"付不起 / 不满足
    前提"时该技能连问都不该问。判据 = 本次使用的来源是该实例持有者、牌名是【杀】、且**确实
    指定了目标**——铁骑的判定只给"此【杀】指定的目标"打标签，别的使用与它无关。
    """
    del state
    card = frame.data.get("card")
    available = (
        frame.data.get("source") == instance.owner
        and card is not None
        and is_slash(behavior_for(card))
        and bool(frame.data.get("targets"))
    )
    return ActivationPlan(optional=True, available=available)


def _qilin_bow_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    target = frame.data.get("target")
    available = (
        frame.data.get("source") == instance.owner
        and _is_owner_slash_damage(frame, instance)
        and target is not None
        and bool(_mount_ids(state, target))
    )
    return ActivationPlan(optional=True, available=available)


ACTIVATION_PLANS: Mapping[str, Callable[[GameState, Frame, SkillInstance], ActivationPlan]] = (
    MappingProxyType(
        {
            "equip.axe": _axe_plan,
            "equip.green_dragon_blade": _green_dragon_plan,
            "equip.double_sword": _double_sword_plan,
            "equip.ice_sword": _ice_sword_plan,
            "equip.qilin_bow": _qilin_bow_plan,
            hero_skill_id("machao", "tieqi"): _tieji_plan,
            # 界马超【铁骑】（M6 票 04）：发动前提与标准版逐字相同（自己的【杀】指定了目标）。
            hero_skill_id("jie_machao", "tieqi"): _tieji_plan,
        }
    )
)
"""发动形态注册表：``skill_id`` → 形态构造函数；**模块级不可变**，缺省 = 裸"是否发动"。"""


def activation_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    """本实例在当前帧上的发动形态（缺省 = 裸询问、可选、无成本面）。"""
    builder = ACTIVATION_PLANS.get(instance.skill_id)
    if builder is None:
        return _default_plan(state, frame, instance)
    return builder(state, frame, instance)


# ---------------------------------------------------------------------------
# 静态装备效果的回调（票 12 §4 的落位表）
# ---------------------------------------------------------------------------


def _own_slash(ctx: ModifierContext, *, holder: int | None) -> bool:
    """``holder`` 是否**持有一张【杀】**且它就是本次查询的牌。

    M5-05b 起按【杀】**家族**判据（:func:`~sgs.effects.is_slash` = 共享 ``count_key``）
    而不是产物牌型相等：火【杀】/ 雷【杀】与转化、虚拟【杀】因此也命中依赖本判据的四条
    装备效果——诸葛连弩的无次数限制、青釭剑的无视防具、方天画戟的最后一张手牌 +2、
    仁王盾的黑色【杀】无效。

    藤甲的"普通【杀】对你无效"**不**读本判据（`_VINE_ARMOR_IMMUNE_CARD_TYPES` 是
    **normal-only** 的牌型闭集，D9 / M5-05a）：家族判据与"是不是属性【杀】"方向相反，
    不能互换。
    """
    return (
        ctx.owner == holder
        and ctx.card is not None
        and is_slash(behavior_for(ctx.card))
    )


def _unlimited_slash(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """诸葛连弩：锁定技，你使用【杀】无次数限制（`A/flow_condition.txt:21`）。"""
    del state
    if not _own_slash(ctx, holder=ctx.subject):
        return current
    return None


def _mount_distance(step: int) -> Callable[[GameState, ModifierContext, Any], Any]:
    """坐骑改**座位距离**：客体系数 +1（防御马）/ 主体系数 −1（进攻马）（票 12 §Q22）。"""

    def handler(state: GameState, ctx: ModifierContext, current: Any) -> Any:
        del state
        affected = ctx.target if step > 0 else ctx.subject
        if affected is None or ctx.owner != affected:
            return current
        return current + step

    return handler


def _renwang_effectiveness(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """仁王盾：锁定技，**黑色的【杀】对你无效**（`A/flow_use.txt:79`）。

    【青釭剑】的"无视防具"通过在 ``EFFECTIVENESS`` 链上先置 ``ignore_armor`` 标志实现
    （票 07 §3 的窄结论：不建通用抑制机制）；链序保证青釭剑（``equip.qinggang_sword``）
    先于仁王盾（``equip.renwang_shield``）执行。
    """
    del state
    if not current:
        return current
    if not _own_slash(ctx, holder=ctx.target):
        return current
    if ctx.flags.get("ignore_armor"):
        return current
    assert ctx.card is not None  # _own_slash 已保证
    color = ctx.card.color if ctx.card.color is not None else color_of_suit(ctx.card.suit)
    if color is Color.BLACK:
        return False
    return current


def _qinggang_ignore_armor(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """青釭剑：锁定技，你使用【杀】时**无视目标的防具**（`A/flow_use.txt:41`）。

    只压制"目标身上的防具"（``ctx.owner == ctx.subject``，即使用者自己的青釭剑），
    且只对【杀】生效；置的是链内旁路标志，不落任何状态（票 07 §3）。

    M5-05a 起同一条回调挂**三个挂点**：``EFFECTIVENESS``（仁王盾式"无效"）、
    ``DAMAGE_TAKE_AMOUNT`` 与 ``DAMAGE_CAP``（藤甲的火焰 +1、白银狮子的封顶）。
    "无视防具"是**防具的整体效果**，不只第一条链：:func:`~sgs.effects.damage_amount`
    只建**一个** ``ModifierContext``，三条链经 ``replace(ctx, role=...)`` 共用同一个
    可变 ``flags`` 字典——这里置一次就够，后两条链的防具回调读到的是同一份标志。
    链序仍保证青釭剑在前（``equip.qinggang_sword`` 按 skill_id 排在防具之前），
    故第一次（``EFFECTIVENESS``）派发就把标志写进了共享字典。
    """
    del state
    if not _own_slash(ctx, holder=ctx.subject):
        return current
    ctx.flags["ignore_armor"] = True
    return current


_VINE_ARMOR_IMMUNE_CARD_TYPES = frozenset({"slash", "barbarian_invasion", "arrow_barrage"})
"""藤甲"对你无效"的牌名闭集（D9：普通【杀】**按产物牌型** ``slash`` 判）。

火杀 / 雷杀是独立牌型，因此不在此列——不读伤害帧、不读 ``CardRef`` 属性，也不复用
:func:`~sgs.effects.is_slash`（那是【杀】**家族**判据，会把属性【杀】一起挡掉）。
"""


def _vine_armor_effectiveness(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """藤甲（一）：普通【杀】/【南蛮入侵】/【万箭齐发】对你**无效**（``EFFECTIVENESS``）。

    与【仁王盾】同款读法：``ctx.owner == ctx.target`` 只压自己身上的防具；链上先跑的
    【青釭剑】置了 ``ignore_armor`` 就整条让开（"无视防具"越过藤甲）。
    """
    del state
    if not current:
        return current
    if ctx.owner != ctx.target or ctx.card is None:
        return current
    if ctx.flags.get("ignore_armor"):
        return current
    if ctx.card.type in _VINE_ARMOR_IMMUNE_CARD_TYPES:
        return False
    return current


def _vine_armor_fire_bonus(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """藤甲（二）：你受到的**火焰**伤害 +1（``DAMAGE_TAKE_AMOUNT``，目标侧）。

    与（一）同一次查询里的旁路标志：``ignore_armor`` 为真时（青釭剑）这条 +1 也不生效。
    属性读 ``ctx.nature``（DAMAGE 帧的属性透传），不读牌面 / ``CardRef``。
    """
    del state
    if ctx.owner != ctx.target:
        return current
    if ctx.nature is not DamageNature.FIRE:
        return current
    if ctx.flags.get("ignore_armor"):
        return current
    return current + 1


def _silver_lion_damage_cap(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """白银狮子：你受到的伤害**最多 1 点**（``DAMAGE_CAP``，在加伤之后封顶）。

    ``DAMAGE_CAP`` 是 M5-05a 新加的第三个伤害数值挂点，:func:`~sgs.effects.damage_amount`
    在 ``DAMAGE_TAKE_AMOUNT`` 之后派发它，故古锭刀 / 酒 / 藤甲的加法都先落地。
    青釭剑的 ``ignore_armor`` 同样越过这条（防具的整体效果，M5-05a 的读法）。
    """
    del state
    if ctx.owner != ctx.target:
        return current
    if ctx.flags.get("ignore_armor"):
        return current
    return min(current, 1)


def _lost_this_equipment(card_type_id: str) -> HookCondition:
    """装备技能「失去**这张**装备」的触发条件（M5-05a 的装备技能用）。

    读本次 ``MOVE_AFTER`` 的载荷（:func:`~sgs.skills.move_after.move_event`）：
    ``from_zone == EQUIP`` ∧ 失去者 = 技能持有者（:data:`MATCH_OWNER_KEY`）∧
    **这批被移动的牌里就有这张牌**（按牌名 id 判——它决定了"失去的是不是它自己"）。

    这是装备技能与【枭姬】的关键差别：枭姬是"失去**任意**装备"，白银狮子只认**它自己**
    这一张。牌名 id 是装备技能 id 的派生源（``equip.<牌名 id>``），故这里与载体解析同键。
    """

    def condition(host: Any, frame: Frame) -> bool:
        del host
        # 延迟导入：``move_after`` 在 catalog **文件末尾**才并入技能表（模块级循环）。
        from .move_after import move_event

        move = move_event(frame)
        if move is None or move.from_zone.zone is not Zone.EQUIP:
            return False
        owner = frame.data.get(MATCH_OWNER_KEY)
        if owner is None or move.from_zone.seat != owner:
            return False
        return any(card.type == card_type_id for card in move.cards)

    return condition


def _silver_lion_lost(host: Any, frame: Frame) -> None:
    """失去白银狮子：**走正常 RECOVER 帧**回复 1 点（不直接改 HP）。

    压帧而不是原地写 ``hp``：回复量的修正（``RECOVER_AMOUNT``）、体力上限夹取与
    ``RECOVER_VALUE`` / ``RECOVER_AFTER`` 两个时机都仍由 :mod:`sgs.engine.steps` 的
    ``RECOVER`` 步骤表统一处理——与【桃】/【桃园结义】/主动技的回复是同一条流程。
    持有者 = :data:`skill_owner`（``_dispatch`` 在回调前写入）。
    """
    owner = frame.data["skill_owner"]
    host.push_frame(
        FrameKind.RECOVER,
        {"source": owner, "targets": (owner,), "card": None, "amount": 1},
        timing=Timing.RECOVER_VALUE,
    )


def _scimitar_no_hand_bonus(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """古锭刀：你使用【杀】对**没有手牌**的目标造成伤害时，伤害 +1（M5-05a，来源侧）。

    挂点是 ``DAMAGE_DEAL_AMOUNT``，:func:`~sgs.effects.damage_amount` **只在
    ``chain=False`` 的源头帧**派发它，故传导帧不会把这一刀重算一遍（结构事实，回调里
    没有"这是不是传导"的分支）。"是不是【杀】"按 M5-04 的共享计数键判
    （:func:`~sgs.effects.is_slash`：普通 / 火 / 雷与转化、虚拟产物同族），不读具体牌名。
    """
    if ctx.owner != ctx.subject or ctx.target is None or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    if state.player(ctx.target).hand:
        return current
    return current + 1


def _halberd_same_target_pool(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """方天画戟的 ``TARGET_POOL`` 挂点：**池不改**（票 12 §4 的"合法性同标准"）。

    这是**有意的空操作**，不是漏删的死代码（W7 spec §3.2 明写"空操作保留"作
    "额外目标合法性同标准"的声明落点；W7-03 / D6 的复核保留它）。行为面由
    :func:`test_halberd_target_pool_is_unchanged_by_the_modifier`（池与是否装备方天无关）
    与 :func:`test_halberd_extra_targets_also_read_the_filtered_pool`（额外目标取自过滤后
    的池）两条**真实行为**断言钉住；声明形状另由 `test_hero_catalog` 的谓词复核。
    """
    del state, ctx
    return current


def _halberd_last_hand_card(state: GameState, player: int, card: CardRef) -> bool:
    """此牌是不是 ``player`` 的**最后一张手牌**（方天的触发条件，真 / 转化共用一份）。

    `A/glossary_gamecard.txt:103-105`："当你使用的【杀】是你最后的手牌时……"（使用前口径；
    2023 线下 / 界方天的"使用后无手牌"版本**不采用**，W7 spec §1.4）。两个分支同住这里，
    免得真牌与转化牌各写一份谓词：

    - **真牌**：手牌恰是这一张（``hand == (card_id,)``）；
    - **转化牌**：来源**恰好一张**、该来源是**实体牌**、且手牌恰是这一张来源
      （``hand == (source,)``，来源在手牌区因此是蕴含的）。多来源（【丈八蛇矛】）、
      装备区 / 判定区来源（【武圣】红牌、【龙胆】装备区【闪】）都返回 ``False``。

    三条 false 分支的**可达性不同**（W7-03 / H2 的订正）：【武圣】的装备区红牌是
    **自然局面**（如已装备的【赤兔】），谓词 false 分支在自然局可达，W7-02 的端到端测试
    就建在这个局面上；只有**龙胆的装备区【闪】**（【闪】不可能合法进入装备区）与
    **丈八两来源 + 方天**（同抢武器槽）不可达自然局，谓词分支保留、只在谓词级合成局面里
    测（W7 spec §0 第 9 条 / §3.7）。
    """
    hand = tuple(state.player(player).hand)
    if card.is_virtual:
        sources = card.virtual_sources
        if len(sources) != 1:
            return False
        source = sources[0]
        if source < 0:  # 来源必须是**实体牌**（虚拟牌再转化不构成"最后一张手牌"）
            return False
        return hand == (source,)
    return hand == (card.card_id,)


def _halberd_target_count(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """方天画戟：此【杀】是你**最后的手牌**时，最大目标数 +2（W7-01 的 ``TARGET_COUNT``）。

    `A/glossary_gamecard.txt:103-105`："可额外指定至多两个目标" ⇒ 额定 1 变成 1..3。
    这是**不询问的自动修正**（用户 Q8(b)），不是"额外目标步"；候选由
    :func:`sgs.effects.target_combos` 按 ``rated..max`` 一次枚举。
    """
    if not _own_slash(ctx, holder=ctx.subject):
        return current
    assert ctx.card is not None  # _own_slash 已保证
    if not _halberd_last_hand_card(state, ctx.subject, ctx.card):
        return current
    return current + 2


def _eight_diagrams_candidate(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """八卦阵：把你自己的 ``ActivateSkill`` 追加进"需要【闪】"的候选（票 10 §5.2 / 票 30）。

    ``ctx.owner == ctx.subject``（只在**需要者自己**的实例上追加）是**本技能自己的谓词**，
    不是平台约束：跨座位注入是合法用法（`03` §4「技能描述模型」的「技能定义 / 实例 / 派发的冻结面」小节，
    票 09 的【黄天】式形态会在这里写 ``ctx.owner != ctx.subject`` 的分支）。
    "每提供者只问一次"由**候选生成器**统一过滤 ``ctx.providers_used``（一处实现，不在这里重复）。
    选中才惰性判定（判定帧由响应窗口步骤压出，不在候选生成期）。
    """
    del state
    if ctx.scope is not CandidateScope.DODGE:
        return current
    if ctx.owner != ctx.subject:
        return current
    return (*current, ActivateSkill(skill_id=ctx.skill_id, answers_seq=ctx.answers_seq))


def _serpent_spear_candidate(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """丈八蛇矛：两张手牌当【杀】**使用或打出**（`A/flow_use.txt:19`，票 12 §Q21）。

    票 03 起本回调只是**共享候选构造器**的一组参数（两张手牌、目标牌名【杀】、
    ``PLAY`` + ``RESPOND``）——组合、目标池、次数链都在 ``skills/virtual.py`` 里，
    与 H 族四技能共用同一份，故"转化牌算不算有距离限制"这类判据不可能两处各写一份。
    """
    return (
        *current,
        *grouped_candidates(
            state,
            ctx,
            target_type_id="slash",
            cards=card_ids(state, ctx.subject, Zone.HAND),
            group_size=2,
            scopes=(CandidateScope.PLAY, CandidateScope.RESPOND),
        ),
    )


def _normal_slash(state: GameState, card_id: int) -> bool:
    """朱雀羽扇的**源筛选**：产物之外的牌型一律不收，只收 ``slash``（普通【杀】）。

    火【杀】/ 雷【杀】是**独立牌型**（D2 / D9），不能再被羽扇转化——否则产物属性会
    按来源递归漂移。"普通【杀】"因此是牌型相等，不是 :func:`~sgs.effects.is_slash`
    的家族判据：家族判据用于**产物能不能当【杀】**（接缝 / 响应 / 装备门控），
    两者方向相反，不能混用。
    """
    return state.card(card_id).type == "slash"


def _vermilion_fan_candidate(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """朱雀羽扇：一张普通【杀】当火【杀】**使用或打出**（M5-05b；D2 / D8 / D9）。

    与 H 族单源转化**同一份**候选构造（:func:`~sgs.skills.virtual.single_conversion`）：
    - 源 = **普通【杀】**（手牌 ∪ 装备，`GLOSSARY.md`「区域默认」）；
    - 产物牌型 ``fire_slash``——FIRE 伤害由**产物牌型的行为表**声明（M5-02），故本技能
      没有第二套属性通道；
    - 语境 = ``PLAY`` + ``RESPOND``：出牌阶段使用，南蛮 / 决斗 / 借刀的响应窗口打出；
    - 来源实体牌由引擎的虚拟源移动路径（``Engine._move_virtual_sources``）进弃牌堆。
    """
    sources = tuple(
        card_id
        for card_id in (
            card_ids(state, ctx.subject, Zone.HAND)
            + card_ids(state, ctx.subject, Zone.EQUIP)
        )
        if _normal_slash(state, card_id)
    )
    return (
        *current,
        *single_conversion(
            state,
            ctx,
            target_type_id="fire_slash",
            cards=sources,
            scopes=(CandidateScope.PLAY, CandidateScope.RESPOND),
        ),
    )


# ---------------------------------------------------------------------------
# 触发类装备效果（票 30）
# ---------------------------------------------------------------------------


def _opposite_genders(state: GameState, source: int, target: int) -> bool:
    """异性判据：两侧性别都已知且不同（M2 无武将 ⇒ 性别恒未知 ⇒ 恒假）。"""
    if source == target:
        return False
    left = state.player(source).gender
    right = state.player(target).gender
    return left is not None and right is not None and left is not right


def _is_owner_slash_damage(frame: Frame, instance: SkillInstance) -> bool:
    """这次伤害是**该实例持有者**用【杀】造成的吗（票 12 §Q12 的触发前提）。

    M5-05b-fix：按【杀】**家族**判据（:func:`~sgs.effects.is_slash`）——寒冰剑 / 麒麟弓
    对火【杀】/ 雷【杀】的伤害同样触发；非【杀】的渠道牌（锦囊 / 传导帧的渠道）仍不触发。
    """
    if frame.data.get("source") != instance.owner:
        return False
    card = frame.data.get("card")
    return card is not None and is_slash(behavior_for(card))


_ASK_DOUBLE_SWORD_DISCARD = "equip.double_sword.discard"
"""手写询问位置（票 22）：【雌雄双股剑】目标弃一张手牌的那次 ``ask_selection``。"""


def _double_sword(host: Any, frame: Frame) -> Control:
    """雌雄双股剑：你使用【杀】指定**异性**目标后，令其选择"弃一张手牌"或"你摸一张"。

    选择权在**目标**（票 12 §Q17）：选项 = 弃一张手牌 / 令你摸一张；目标无手牌时
    "弃一张手牌"**不进候选**（付不起的组合不进候选，票 19 §6）。多目标时逐个目标处理，
    游标存帧内 ``double_sword_cursor``。

    前提与 :func:`_double_sword_plan` **同源**（M5-bugfix）：必须是来源用【杀】指定了
    异性目标的那次使用——火攻等非【杀】的使用不进本循环。
    """
    source = frame.data.get("source")
    targets = tuple(frame.data.get("targets", ()))
    state = host.state
    if not _double_sword_triggered(state, frame):
        return None
    cursor = frame.data.get("double_sword_cursor", 0)
    while cursor < len(targets):
        target = targets[cursor]
        frame.data["double_sword_cursor"] = cursor
        if not _opposite_genders(state, source, target):
            cursor += 1
            continue
        options = ("discard_one", "draw_one") if state.player(target).hand else ("draw_one",)
        answer = host.ask_option(
            frame,
            target,
            Timing.USE_AFTER_DESIGNATE_TARGET,
            skill_id="equip.double_sword",
            options=options,
        )
        if host.suspended(answer):
            return "stay"
        if answer.choice == "discard_one":
            picked = host.ask_selection(
                frame,
                owner=_ASK_DOUBLE_SWORD_DISCARD,
                key=f"double_sword:{target}",
                actor=target,
                timing=Timing.USE_AFTER_DESIGNATE_TARGET,
                spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
                build_face=lambda: host.selection_face(target, (Zone.HAND,)),
            )
            if host.suspended(picked):
                return "stay"
            if picked:
                moved_key = f"double_sword_moved:{target}"
                if not frame.data.get(moved_key):
                    frame.data[moved_key] = True
                    if (
                        host.move_in_frame(
                            frame,
                            state.refs(picked),
                            to_zone=ZoneRef(Zone.DISCARD),
                            reason=MoveReason.DISCARD,
                            owner=target,
                            actor=target,
                        )
                        == "stay"
                    ):
                        # 成本移动内联挂起（【连营】一类的"失去牌后"会提问）：让出控制权，
                        # 由挂起的派发先消费那条应答（票 14 §1）。
                        return "stay"
        else:
            host.draw_for_hand(frame, source, 1)
        cursor += 1
        frame.data["double_sword_cursor"] = cursor
    return None


_ASK_ICE_SWORD_DISCARD = "equip.ice_sword.discard"
"""手写询问位置（票 22）：【寒冰剑】逐轮弃牌的那次 ``ask_selection``。"""


def _ice_sword(host: Any, frame: Frame) -> Control:
    """寒冰剑：你使用【杀】造成伤害时，**防止此伤害**并依次弃置目标的两张牌。

    "防止伤害"由**帧标签 ``PREVENTED``**（票 12 §Q15；票 08 起住帧标签袋）承担：
    跳体力扣减、不新增 delta / 时机；"依次"⇒ 逐张单询问（票 19 §7），池空则跳过剩余次数。

    **票 16 裁定：保持手写（选项 b）**，多轮原语经第三个消费者评估后仍**不落**
    （前两次见票 10 的 ``## Answer`` 与 ``design.md`` §10）。举证标准与票 10 同级，逐条：

    1. **契约冲突**：``SkillFlow`` 的中心契约是"步骤只收集、提交点才生效 + 取消零副作用"，
       而"两轮不能选同一张牌"**只能**由"第 1 轮立即搬走"实现 —— 落一个循环步骤等于给
       流水线加**第二套提交语义**（已提交轮不回滚），并触碰 A 族 / 【反馈】/【流离】/
       装备各条共享的那条契约；
    2. **重入游标**：进度无论如何要留在技能里（挂起续跑要靠帧私有 ``ice_sword_done``
       认出"这张已经搬过"），即便有循环步骤也删不掉；
    3. **候选收益为零**：发动形态本来就是裸"是否发动"（没有成本 / 载荷烘进候选），
       迁移不减少任何候选；
    4. **第三个消费者仍不同源**：【遗计】轮内是"选剩余亮牌（可选 ``Pass`` 收尾）→ 选收牌
       角色 → 立即搬"、缓存键按**点号 + 轮号**；【雌雄】按**目标**推进、应答者是每个目标、
       轮内是"选项 → 条件选牌 → 立即搬 / 摸"。给两者落一个原语要同时加四轴，而轮内键形状
       必须各自保留 ⇒ 记成**后置重构候选**（等轮次形状真正同源的消费者出现再抽）。

    **"每轮提交"语义**（票 16 定形；下面这个帧内游标循环是它的实现与唯一真源）：

    - 帧内游标 ``ice_sword_done`` = **已提交**的轮数；**每轮立即搬牌**（提交点 = 这一次
      ``move_in_frame``），故第 1 轮搬走的那张不出现在第 2 轮的面里；
    - 游标在**搬牌之前**前进：这次搬牌可能内联派发"失去牌后"（目标若是孙尚香，掉装备就是
      【枭姬】）并挂起，重入时据此跳过已搬的这张（票 14 §1）；
    - 每轮一个**独立缓存键** ``ice_sword:<轮>``：选择面只构造一次、重入复用，重入既不重发
      上一轮的询问、也不重放已提交的轮（``"stay"`` 照透传）；
    - 任何出口（``has_zone_cards`` 判空 / 这一轮没有答案 / 挂起后续跑）都**只停循环**、
      **不回滚已提交的轮次**。寒冰剑的轮内**没有 ``Pass`` 候选**（``optional=False``：
      弃置两张是强制的），"可选 ``Pass`` 收尾"这一档属【遗计】那条手写循环；
    - 入口的 ``add_tag(frame, Tag.PREVENTED)`` **只置一次**（重复置入幂等），重入**不因它
      短路** —— 否则本次选择询问的应答会没人消费而串到下一个询问。

    钉住各条款的用例：``tests/test_skills.py`` 的
    ``test_ice_sword_two_rounds_cannot_pick_the_same_card``（立即搬 / 第二轮面里没有它）、
    ``test_ice_sword_round_two_asks_again_after_an_inline_suspension``（重入不短路 + 每轮
    独立键 + 轮内无 ``Pass``）、
    ``test_ice_sword_a_round_without_an_answer_stops_the_loop_and_keeps_the_committed_round``、
    ``test_ice_sword_a_suspended_move_resumes_at_the_next_round_without_replaying_it``、
    ``test_ice_sword_asks_nothing_when_the_target_has_no_cards``。
    """
    if frame.data.get("ice_sword_finished"):
        return None
    target = frame.data.get("target")
    if target is None:
        return None
    # 帧标签袋里的"已防止"标志（票 12 §Q15；票 08 起住帧标签袋）：只置一次；重入时
    # **不因它短路**，否则本次选择询问的应答会没人消费而串到下一个询问
    # （"每提供者只问一次"的代价）。
    add_tag(frame, Tag.PREVENTED)
    done = frame.data.get("ice_sword_done", 0)
    while done < 2:
        if not host.has_zone_cards(target):
            break
        picked = host.ask_selection(
            frame,
            owner=_ASK_ICE_SWORD_DISCARD,
            key=f"ice_sword:{done}",
            actor=frame.data.get("skill_owner"),
            timing=Timing.DAMAGE_DEAL,
            spec=SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, 1)),
            build_face=lambda: host.selection_face(target, (Zone.HAND, Zone.EQUIP)),
        )
        if host.suspended(picked):
            return "stay"
        if not picked:
            break
        # 游标在**搬牌之前**前进：这次弃置可能内联派发"失去牌后"并挂起（目标若是孙尚香
        # 掉装备就是【枭姬】），重入时据此跳过已搬的这一张（票 14 §1）。
        frame.data["ice_sword_done"] = done + 1
        if (
            host.move_in_frame(
                frame,
                host.state.refs(picked),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=target,
                actor=target,
            )
            == "stay"
        ):
            return "stay"
        done += 1
    frame.data["ice_sword_finished"] = True
    return None


def _mount_ids(state: GameState, seat: int) -> tuple[int, ...]:
    return tuple(
        card_id
        for card_id in state.player(seat).equip
        if state.card(card_id).slot in (SlotKind.HORSE_DEFENSE, SlotKind.HORSE_OFFENSE)
    )


def _qilin_bow_target_face(
    state: GameState, frame: Frame, owner: int, answers: FlowAnswers
) -> int | tuple[()]:
    """【麒麟弓】的选取面铺谁的区 = 这次伤害的目标（票 10）。

    沿用票 07 给 ``face`` 的**空元组**档（"这一步此刻不存在"）：帧上没有目标时一个询问
    都不发（与 :func:`_liuli_cost_face` 同形），而不是把座位 ``0`` 当成目标。
    """
    del state, owner, answers
    target = frame.data.get("target")
    return () if target is None else int(target)


def _qilin_bow_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Control:
    """提交点：弃掉步 1 选中的那张**坐骑**。

    ``qilin_bow_done`` 在**搬牌之前**落（与迁移前逐字同形），故重入时上面的短路接手、
    同一张坐骑只弃一次。"内联挂起就让出控制权"（票 14 §1）：目标若是孙尚香，掉坐骑触发
    【枭姬】会提问，``"stay"`` 必须透传。
    """
    target = frame.data.get("target")
    if target is None or frame.data.get("qilin_bow_done"):
        return None
    frame.data["qilin_bow_done"] = True
    picks = tuple(answers["card"])
    if not picks:
        return None
    return host.move_in_frame(
        frame,
        host.state.refs(picks),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=target,
        actor=frame.data.get("skill_owner"),
    )


QILIN_BOW_FLOW = SkillFlow(
    name="qilin_bow",
    steps=(
        PickCards(
            zones=(Zone.EQUIP,),
            lo=1,
            hi=1,
            # **有意保留现状**（票 10 §要做什么）：迁移前的选牌没有 ``Pass``，"取消"的
            # 唯一出口是发动询问那一道（答 ``Pass`` ⇒ 一个询问都不发）。
            optional=False,
            key="card",
            # 帧内询问缓存键沿用迁移前的 ``"qilin_bow"``（``ask_selection`` 的既有键），
            # 故重入不重发、记录流除步骤归属外一行不变。
            cache_key="qilin_bow",
            face=_qilin_bow_target_face,
            # 选取面只铺目标的**坐骑槽**（票 10 的原语加法）：坐骑是六个不同牌名共用的
            # 槽位种类，牌名白名单表达不了，故给 ``PickCards`` 加 ``slot_kinds``。
            slot_kinds=(SlotKind.HORSE_DEFENSE, SlotKind.HORSE_OFFENSE),
        ),
    ),
    effect=_qilin_bow_effect,
)
"""【麒麟弓】的一步流水线（票 10）：[发动询问] → 弃目标装备区的一张坐骑。

迁移前这条技能的选取面由 ``_mount_face`` **自铺**（技能层自己拼槽位）；迁移后由
``PickCards`` 的 ``slot_kinds`` 过滤表达
（与票 09 的 ``card_types`` 同一条链路：同步过滤 ``slots`` / ``card_ids``、``hi=None``
的上限按过滤后的池重算、铺面仍惰性）。``available`` 判据（目标有坐骑）留在
:func:`_qilin_bow_plan`，不在步骤里再判一遍。
"""


def _green_dragon_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Control:
    """提交点：把步 1 选中的那张【杀】（实体或**转化出来的**）**继续使用**。

    续杀"目标不变、不计次数、不限距离"（`A/flow_condition.txt:66-67`）由三步事实表达：
    目标**直接沿用原【杀】的目标**（不再走 ``check_targets`` ⇒ 距离/目标池一概不重算）、
    ``counts=False``（票 12）、``gdb_used`` 只落一次（每张【杀】只给一次机会，重入由上面
    的短路接手）。

    步 1 的答案是 **``CardRef``**（票 14）：【龙胆】的【闪】当【杀】、【武圣】的红牌当【杀】
    与手牌里的实体【杀】在这里**没有分岔**——都是"一张要用的【杀】"，来源实体牌的移动由
    使用链的 ``_move_virtual_sources`` 负责。
    """
    owner = frame.data.get("skill_owner")
    if frame.data.get("gdb_used") or frame.data.get("source") != owner:
        return None
    picks = tuple(answers["card"])
    if not picks:
        return None
    frame.data["gdb_used"] = True
    host.start_use(
        picks[0],
        source=owner,
        targets=tuple(frame.data.get("targets", ())),
        use_timing=Timing.RESOLVE_BEFORE_EFFECT,
        counts=False,
    )
    return "stay"


GREEN_DRAGON_FLOW = SkillFlow(
    name="green_dragon_blade",
    steps=(
        PickProvide(
            type_id="slash",
            # 与池**同源**（W4-3 的区域默认）：面里的来源除了手牌实体【杀】，还有
            # 【龙胆】/【武圣】从**装备区**转出来的【杀】（红牌 / 【闪】），故这里必须是
            # 手牌 ∪ 装备。判定区不在默认档里，池里也不会出现它（W4 评审处置 C.6 前的
            # `(HAND,)` 与池不符：`Step.zones` 会少报装备区来源）。
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            # **有意保留现状**（票 09 §要做什么）：这一步没有 ``Pass``，"取消"的唯一出口是
            # 发动询问那一道（答 ``Pass`` ⇒ 一个询问都不发）。
            optional=False,
            key="card",
            # **历史**（票 21）：同一个【杀】帧里【流离】的成本步也是 ``key="card"`` 的
            # ``PickCards``，两者共用缓存键时本步会命中前者的答案——那份答案来自**另一份
            # 面**，``refs`` 里没有 ⇒ ``KeyError``。票 21 用一个本步独有的 ``cache_key``
            # 躲开它；票 22 起跨步骤隔离由**询问位置** ``(flow.name, index)`` 承担，这个
            # ``cache_key`` 因此只剩"本步的询问名"这一层含义，保留它是为了不动既有的
            # 询问记录与前端文案（``key`` 仍是 ``"card"``，效果读 ``answers["card"]``）。
            cache_key="green_dragon_blade",
        ),
    ),
    effect=_green_dragon_effect,
)
"""【青龙偃月刀】的一步流水线（票 09；票 14 换成"提供一张【杀】"）：[发动询问] → 提供一张【杀】 → 继续使用。

票 09 前，这张【杀】是**烘进发动询问候选**的载荷（每张手牌【杀】一条候选）；
票 09 把成本下沉成 ``SELECT_CARDS`` 步；票 14 把这一步换成 :class:`PickProvide`——
候选面 = 手牌里的实体【杀】**＋各转化技此刻能转出的虚拟【杀】**（赵云的【闪】、
【武圣】的红牌，区域默认 = 手牌 ∪ 装备，**不含**判定区），故"青龙追问时再点龙胆/武圣"
这条用户口径有了落点。
"""


def _axe_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Control:
    """提交点：弃掉步 1 选中的两张牌，再打帧标签 ``SAVED``（"此【杀】依然造成伤害"）。

    ``resolve_before_effect`` 据此**跳过"生效前 / 生效时"、直进"生效后"**（票 12 Q16；
    标志自票 08 起住帧标签袋）。两张牌可跨手牌 / 装备区，故按来源区分别移动
    （:func:`_move_by_source`）。

    成本的搬牌可能内联挂起（失去牌触发【连营】/【枭姬】一类）；``axe_used`` 只在**成本
    搬完之后**落（与迁移前逐字同形），重入时据此短路。
    """
    owner = frame.data.get("skill_owner")
    if owner is None or frame.data.get("source") != owner or frame.data.get("axe_used"):
        return None
    cards = tuple(answers["card"])
    if not cards:
        return None
    if _move_by_source(
        host,
        frame,
        cards,
        to_zone=ZoneRef(Zone.DISCARD),
        owner=owner,
        key="axe:cost",
    ) == "stay":
        return "stay"
    frame.data["axe_used"] = True
    add_tag(frame, Tag.SAVED)
    return None


AXE_FLOW = SkillFlow(
    name="axe",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=2,
            hi=2,
            # **有意保留现状**（票 09 §要做什么）：迁移前的两张组合没有 ``Pass``，
            # "取消"的唯一出口是发动询问那一道。
            optional=False,
            key="card",
            # W4-fix-3（用户裁定，偏离官方 FAQ）：成本池排除**这张技能对应的装备牌自己**。
            exclude_cards=_axe_own_card,
        ),
    ),
    effect=_axe_effect,
)
"""【贯石斧】的一步流水线（票 09）：[发动询问] → 弃两张牌（手牌 / 装备区） → 打 ``SAVED``。

票 09 前，两张牌是**烘进发动询问候选**的全部 C(n,2) 组合（手牌 + 装备一多就是成百条
按钮）；迁移后发动询问回到裸"是否发动"（``available`` = 自己的【杀】被抵消 + 弃得起
两张），成本改由 ``PickCards`` 问。成本搬牌与帧标签仍只在提交点发生（N6）。

W4-fix-3：那一步的池**排除武器自己**（:func:`_axe_own_card`）——用户裁定，**偏离官方
FAQ**（见 `开发文档/06` §5「程序化约定与偏离一手」的账本行）；清单候选、铺面、
``hi=None`` 上限、校验器四处
读同一份黑名单。
"""


def _move_by_source(
    host: Any,
    frame: Frame,
    card_ids: tuple[int, ...],
    *,
    to_zone: ZoneRef,
    owner: int,
    key: str,
) -> Control:
    """把一批选中的牌按**来源区**分组移动：一次 ``move_card`` 只能有一个来源区。

    【贯石斧】的成本可以跨手牌与装备区（`A/flow_move.txt:33-39`），故逐来源区各发一条
    ``card_move``；分组用 ``dict``（插入序确定），顺序逐种子可复现。

    **让出控制权（票 14 §1）**：成本移动可能内联派发 `MOVE_AFTER` 并挂起（例如
    【枭姬】的持有者弃掉自己的装备），`"stay"` 必须透传；帧私有游标 ``key`` 让重入
    只搬还没搬的组。
    """
    if not card_ids:
        return None
    plan_key = f"{key}:plan"
    done_key = f"{key}:done"
    plan = frame.data.get(plan_key)
    if plan is None:
        groups: dict[ZoneRef, list[int]] = {}
        for card_id in card_ids:
            groups.setdefault(locate(host.state, card_id), []).append(card_id)
        plan = tuple(tuple(ids) for ids in groups.values())
        frame.data[plan_key] = plan
    index = frame.data.get(done_key, 0)
    while index < len(plan):
        frame.data[done_key] = index + 1
        control = host.move_in_frame(
            frame,
            host.state.refs(plan[index]),
            to_zone=to_zone,
            reason=MoveReason.DISCARD,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
        index += 1
    frame.data.pop(plan_key, None)
    frame.data.pop(done_key, None)
    return None


def _equip(
    card_type_id: str,
    *,
    locked: bool,
    requirement: str = "",
    modifiers: tuple[Modifier, ...] = (),
    hooks: tuple[Hook, ...] = (),
    seams: tuple[Seam, ...] = (),
    virtual_type_ids: tuple[str, ...] = (),
    virtual_source_count: int = 1,
    step_requirements: Mapping[str, str] | None = None,
    preactivated: bool = False,
) -> SkillDef:
    """按牌名 id 造一条装备技能定义（``skill_id = "equip." + 牌名 id``，票 09 §1）。

    ``step_requirements``（票 09）：走步骤流水线的装备技逐步的"需要做什么"，与其余各族的
    :attr:`SkillDef.step_requirements` 同一形态（缺省 ``None`` = 不分步，既有 15 条定义
    一字不改）。``preactivated``（W4-2）：已迁到预发动的接缝技（【青龙偃月刀】/【贯石斧】）
    翻这个位——被动入口的清单随 ``SkillActivationQuery.preactivation`` 下发，成本由
    复合动作一次提交（骨架候选是"是否发动"那一问，保留）。
    ``virtual_source_count``（W4-3）：虚拟牌候选每次消耗几张来源牌——【丈八蛇矛】是 2，
    其余（含一切非转化技）恒 1；清单归属据此区分"两张来源的【杀】是谁转出来的"。
    """
    return SkillDef(
        skill_id=f"equip.{card_type_id}",
        locked=locked,
        carrier=CarrierKind.EQUIPMENT,
        hooks=hooks,
        modifiers=modifiers,
        seams=seams,
        requirement=requirement or None,
        step_requirements=step_requirements,
        virtual_type_ids=virtual_type_ids,
        virtual_source_count=virtual_source_count,
        preactivated=preactivated,
    )


_DISTANCE_PLUS_ONE = (Modifier(ModifierPoint.DISTANCE, _mount_distance(1)),)
_DISTANCE_MINUS_ONE = (Modifier(ModifierPoint.DISTANCE, _mount_distance(-1)),)

_SKILL_DEFS: tuple[SkillDef, ...] = (
    # 9 名武器 / 10 实体张（诸葛连弩 ×2）
    _equip(
        "crossbow",
        locked=True,
        requirement="出牌阶段使用【杀】无次数限制",
        modifiers=(Modifier(ModifierPoint.COUNT_LIMIT, _unlimited_slash),),
    ),
    _equip(
        "qinggang_sword",
        locked=True,
        requirement="使用【杀】时无视目标的防具",
        modifiers=(
            Modifier(ModifierPoint.EFFECTIVENESS, _qinggang_ignore_armor),
            Modifier(ModifierPoint.DAMAGE_TAKE_AMOUNT, _qinggang_ignore_armor),
            Modifier(ModifierPoint.DAMAGE_CAP, _qinggang_ignore_armor),
        ),
    ),
    _equip(
        "double_sword",
        locked=False,
        requirement="选择：弃一张手牌 / 令使用者摸一张",
        hooks=(Hook(Timing.USE_AFTER_DESIGNATE_TARGET, _double_sword),),
        step_requirements={"card": "由目标选择弃置一张手牌"},
    ),
    _equip(
        "green_dragon_blade",
        locked=False,
        requirement="选择一张【杀】继续使用",
        seams=(Seam(SeamKind.SLASH_CANCELED, flow=GREEN_DRAGON_FLOW),),
        step_requirements={"card": "提供一张【杀】继续使用（目标不变；可发动转化技）"},
        preactivated=True,
    ),
    _equip(
        "serpent_spear",
        locked=False,
        requirement="选择两张手牌当【杀】使用或打出",
        modifiers=(Modifier(ModifierPoint.CANDIDATES, _serpent_spear_candidate),),
        virtual_type_ids=("slash",),
        virtual_source_count=2,
    ),
    _equip(
        "axe",
        locked=False,
        requirement="弃两张牌，令被抵消的【杀】依然造成伤害",
        seams=(Seam(SeamKind.SLASH_CANCELED, flow=AXE_FLOW),),
        step_requirements={"card": "弃置两张牌（手牌 / 装备区）令此【杀】依然造成伤害"},
        preactivated=True,
    ),
    _equip(
        "halberd",
        locked=False,
        requirement="最后一张手牌【杀】可指定至多 3 个目标",
        modifiers=(
            Modifier(ModifierPoint.TARGET_POOL, _halberd_same_target_pool),
            Modifier(ModifierPoint.TARGET_COUNT, _halberd_target_count),
        ),
    ),
    _equip(
        "qilin_bow",
        locked=False,
        requirement="弃置目标装备区里的一匹坐骑",
        hooks=(Hook(Timing.DAMAGE_DEAL, flow=QILIN_BOW_FLOW),),
        step_requirements={"card": "弃置目标装备区里的一匹坐骑（只能选坐骑）"},
    ),
    _equip(
        "ice_sword",
        locked=False,
        requirement="防止此伤害，并依次弃置目标的两张牌",
        hooks=(Hook(Timing.DAMAGE_DEAL, _ice_sword),),
        step_requirements={"card": "依次弃置目标的两张牌（手牌 / 装备区）"},
    ),
    # 3 防具
    _equip(
        "eight_diagrams",
        locked=False,
        requirement="判定为红则视为使用【闪】",
        modifiers=(Modifier(ModifierPoint.CANDIDATES, _eight_diagrams_candidate),),
    ),
    _equip(
        "renwang_shield",
        locked=True,
        requirement="黑色【杀】对你无效",
        modifiers=(Modifier(ModifierPoint.EFFECTIVENESS, _renwang_effectiveness),),
    ),
    # 6 坐骑：−1 进攻马（赤兔 ♥5）/ +1 防御马（绝影 ♠5）/ +1 防御马（的卢 ♣5）/
    # +1 防御马（爪黄飞电 ♥K）/ −1 进攻马（大宛 ♠K）/ −1 进攻马（紫骍 ♦K）
    _equip(
        "chitu",
        locked=True,
        requirement="计算与其他角色的距离时 -1",
        modifiers=_DISTANCE_MINUS_ONE,
    ),
    _equip(
        "jueying",
        locked=True,
        requirement="其他角色计算与你的距离时 +1",
        modifiers=_DISTANCE_PLUS_ONE,
    ),
    _equip(
        "dilu",
        locked=True,
        requirement="其他角色计算与你的距离时 +1",
        modifiers=_DISTANCE_PLUS_ONE,
    ),
    _equip(
        "zhuahuang_feidian",
        locked=True,
        requirement="其他角色计算与你的距离时 +1",
        modifiers=_DISTANCE_PLUS_ONE,
    ),
    _equip(
        "dawan",
        locked=True,
        requirement="计算与其他角色的距离时 -1",
        modifiers=_DISTANCE_MINUS_ONE,
    ),
    _equip(
        "zixing",
        locked=True,
        requirement="计算与其他角色的距离时 -1",
        modifiers=_DISTANCE_MINUS_ONE,
    ),
    # ---- M5-01a 的五件军争装备（M5-05a 落四件数值效果；M5-05b 落朱雀羽扇转化）----
    # 4 条锁定技按牌面锁定标记；朱雀羽扇的 CANDIDATES 走单源转化（产物 ``fire_slash``），
    # ``virtual_type_ids`` 供接入层把候选对回技能（形态表也据此读成「转化技」）。
    _equip(
        "silver_lion",
        locked=True,
        requirement="受到的伤害最多为 1 点；失去装备时回复 1 点体力",
        modifiers=(Modifier(ModifierPoint.DAMAGE_CAP, _silver_lion_damage_cap),),
        hooks=(
            Hook(
                Timing.MOVE_AFTER,
                condition=_lost_this_equipment("silver_lion"),
                handler=_silver_lion_lost,
            ),
        ),
    ),
    _equip(
        "vine_armor",
        locked=True,
        requirement="普通【杀】/【南蛮入侵】/【万箭齐发】对你无效；火焰伤害 +1",
        modifiers=(
            Modifier(ModifierPoint.EFFECTIVENESS, _vine_armor_effectiveness),
            Modifier(ModifierPoint.DAMAGE_TAKE_AMOUNT, _vine_armor_fire_bonus),
        ),
    ),
    _equip(
        "ancient_scimitar",
        locked=True,
        requirement="目标没有手牌时，你使用【杀】造成的伤害 +1",
        modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _scimitar_no_hand_bonus),),
    ),
    _equip(
        "vermilion_feather_fan",
        locked=False,
        requirement="你可以将一张普通【杀】当【火杀】使用或打出",
        modifiers=(Modifier(ModifierPoint.CANDIDATES, _vermilion_fan_candidate),),
        virtual_type_ids=("fire_slash",),
    ),
    _equip(
        "hualiu",
        locked=True,
        requirement="其他角色计算与你的距离时 +1",
        modifiers=_DISTANCE_PLUS_ONE,
    ),
)

# 武将技能定义（票 03 的 H 族、票 04 的 A 族；票 12 起全表）在**文件末尾**并入：
# `heroes` / `active` 反向 import 本模块的 SkillDef / ModifierContext，故这一行必须晚于
# 那两者的定义（见 CATALOG 的 docstring）。
from .heroes import conversion_skill_definitions  # noqa: E402  （末尾 import 是刻意的）
from .active import active_skill_definitions  # noqa: E402  （末尾 import 是刻意的）
from .target_pool import target_pool_skill_definitions  # noqa: E402  （末尾 import 是刻意的）
from .move_after import move_after_skill_definitions  # noqa: E402  （末尾 import 是刻意的）
from .response_window import (  # noqa: E402  （末尾 import 是刻意的）
    response_window_skill_definitions,
)
from .lord_request import (  # noqa: E402  （末尾 import 是刻意的）
    lord_request_activation_plans,
    lord_skill_definitions,
)
from .arrange import (  # noqa: E402  （末尾 import 是刻意的）
    arrange_activation_plans,
    arrange_skill_definitions,
)
from .damage_after import (  # noqa: E402  （末尾 import 是刻意的）
    DAMAGE_DEAL_POINT_KEY,
    DAMAGE_DEAL_RECORDED_KEY,
    DAMAGE_POINT_KEY,
    DAMAGE_RECORDED_KEY,
    damage_after_skill_definitions,
    damage_point_index,
)
from .rejudge import (  # noqa: E402  （末尾 import 是刻意的）
    rejudge_activation_plans,
    rejudge_skill_definitions,
)
from .phase_body import (  # noqa: E402  （末尾 import 是刻意的）
    phase_body_activation_plans,
    phase_body_skill_definitions,
)
from .target_change import (  # noqa: E402  （末尾 import 是刻意的）
    target_change_skill_definitions,
)
from .limits import (  # noqa: E402  （末尾 import 是刻意的）
    limits_skill_definitions,
)
from .use_after import (  # noqa: E402  （末尾 import 是刻意的）
    use_after_skill_definitions,
)
from .judgment_loop import (  # noqa: E402  （末尾 import 是刻意的）
    judgment_loop_activation_plans,
    judgment_loop_skill_definitions,
)
from .awakening import (  # noqa: E402  （末尾 import 是刻意的）
    awakening_activation_plans,
    awakening_skill_definitions,
)
from .negation import negation_skill_definitions  # noqa: E402  （末尾 import 是刻意的）
from .hero_zone import (  # noqa: E402  （末尾 import 是刻意的）
    hero_zone_skill_definitions,
)
from .life_after import (  # noqa: E402  （末尾 import 是刻意的）
    life_after_skill_definitions,
)

ACTIVATION_PLANS: Mapping[str, Callable[[GameState, Frame, SkillInstance], ActivationPlan]] = (
    MappingProxyType(
        {
            **ACTIVATION_PLANS,
            **arrange_activation_plans(),
            **rejudge_activation_plans(),
            **phase_body_activation_plans(),
            **judgment_loop_activation_plans(),
            **awakening_activation_plans(),
            **lord_request_activation_plans(),
        }
    )
)
"""G 族（票 10）、I 族（票 11）、L 族（票 12）、J 族（M6 票 02）与 F 族（M6 票 14 的
界【护驾】②：应答者 = 使用 / 打出【闪】的那个角色）的发动形态并入后的**完整**注册表：
与 :data:`CATALOG` 同一合并姿态。"""

CATALOG: Mapping[str, SkillDef] = MappingProxyType(
    {
        definition.skill_id: definition
        for definition in (
            *_SKILL_DEFS,
            *conversion_skill_definitions(),
            *active_skill_definitions(),
            *target_pool_skill_definitions(),
            *move_after_skill_definitions(),
            *response_window_skill_definitions(),
            *lord_skill_definitions(),
            *arrange_skill_definitions(),
            *damage_after_skill_definitions(),
            *rejudge_skill_definitions(),
            *phase_body_skill_definitions(),
            *target_change_skill_definitions(),
            *limits_skill_definitions(),
            *use_after_skill_definitions(),
            *judgment_loop_skill_definitions(),
            *awakening_skill_definitions(),
            *negation_skill_definitions(),
            *hero_zone_skill_definitions(),
            *life_after_skill_definitions(),
        )
    }
)
"""定义目录：``skill_id`` → :class:`SkillDef`，**模块级不可变**（票 07 §1 / §6）。

装备技能（22 条：标准 17 + 军争 5）与武将技能（票 03 起逐族落地，票 12 起 29 条齐、M6 P0 票 01 起 32 条、票 02 起 37 条、票 03 收口 40 条、票 04 起 44 条、票 05 起 46 条、票 06 起 52 条、票 07 起 56 条、票 08 起 62 条、票 09 起 64 条、票 10 起 66 条、票 11 起 67 条、票 14 起 73 条、票 15 起 77 条、票 16 起 83 条、票 17 起 **91 条** = 合计 113 条）**共用同一张表**：
载体解析、优先级档、修饰器链、时机派发都只认这张表，不区分两类载体。合并点在**文件末尾**
（而不是模块头部的 import）是为了让 :mod:`sgs.skills.heroes` 能反向 import 本模块的
:class:`SkillDef`——`heroes` 在 import 期读到的 :data:`CATALOG` 是装备那半张，而它的定义由
这里的 :func:`sgs.skills.heroes.conversion_skill_definitions` 现取现并，故两半都不会漏。
"""
