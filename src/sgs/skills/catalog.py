"""技能**定义目录**、载体解析与派发（票 07 §1–§3；施工票 29 落静态面、票 30 落触发面）。

一张装备 = 一个 :class:`SkillDef`（稳定 id = ``equip.<牌名 id>``），内部可带多条
:class:`Hook`（键 = 时机枚举本身）、多条 :class:`Seam`（帧内接缝）与多条
:class:`Modifier`（五个挂点）。M3 票 01 起，**武将牌**成为与装备区并列的载体
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
- **修饰器**（五挂点）：诸葛连弩 / 仁王盾 / 青釭剑 / 坐骑 / 方天画戟 / 丈八蛇矛 / 八卦阵；
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
from itertools import combinations
from types import MappingProxyType
from typing import Any, TypeAlias

from sgs.data import Color, Identity, SkillType, color_of_suit, hero_skill_id, hero_skill_ids
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    PriorityClass,
    check_targets,
    locate,
    priority_class_of,
)
from sgs.resolution import Control, Frame, Tag, add_tag
from sgs.state import (
    ActivateSkill,
    GameState,
    MoveReason,
    SelectionSpec,
    Slot,
    SlotKind,
    UseCard,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .virtual import card_ids, grouped_candidates

__all__ = [
    "ACTIVATION_PLANS",
    "CATALOG",
    "ActivationPlan",
    "Hook",
    "HookCondition",
    "Seam",
    "SeamKind",
    "SkillDef",
    "SkillInstance",
    "SkillSource",
    "SkillSourceKind",
    "activation_plan",
    "dispatch_seam",
    "dispatch_timing",
    "instance_key",
    "is_equipped",
    "lord_skill_allowed",
    "lord_skill_ids",
    "prune_skill_state",
    "reset_phase_skill_state",
    "resolve_instances",
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


class SkillSourceKind(str, Enum):
    """技能实例的**来源**种类（票 07 §6 的带标签联合）。

    ``HERO_CARD``（票 01）是**武将牌**：它不是实体牌，故只为它记 ``skill_id``，
    **绝不为它造 ``CardRef``**（与虚拟装备同一姿态）。
    """

    EQUIPMENT_CARD = "EQUIPMENT_CARD"
    VIRTUAL_GRANT = "VIRTUAL_GRANT"
    HERO_CARD = "HERO_CARD"


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
    """

    timing: Timing
    handler: HookHandler
    condition: HookCondition | None = None


@dataclass(frozen=True, slots=True)
class Seam:
    """一条帧内接缝：键 = :class:`SeamKind` + 回调。"""

    kind: SeamKind
    handler: SeamHandler


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

    @property
    def priority_class(self) -> PriorityClass:
        """本技能在修饰器链里的优先级档（由载体派生，不另存字段以防漂移）。"""
        return priority_class_of(self.carrier)


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
    """一次"是否发动"询问的声明式形态（票 19 §5 / §6）。

    ``zones`` 非空时，"付得起"的载荷是这些区里的面内位置组合（【贯石斧】弃两张）；
    ``available=False`` 表示**当前付不起** ⇒ 该技能不进候选、连询问都不构造。
    """

    zones: tuple[Zone, ...] = ()
    min_picks: int = 0
    max_picks: int = 0
    optional: bool = True
    available: bool = True
    card_types: tuple[str, ...] = ()
    """选取面只铺这些**牌名 id**；空元组 = 不筛（【青龙偃月刀】只让挑【杀】）。"""


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


def resolve_instances(state: GameState) -> tuple[SkillInstance, ...]:
    """解析全部**存活实例**，按链序 ``(priority_class, skill_id, owner)`` 全序。

    载体 = 装备区里的实体牌（``source = EQUIPMENT_CARD``）∪ 座位的虚拟授予
    （``source = VIRTUAL_GRANT``，不占牌守恒）∪ **武将牌**（``source = HERO_CARD``，
    票 01：读 ``player.hero`` 经 ``sgs.data.hero_skill_ids`` 取该武将的技能清单）。
    同一 ``(owner, skill_id)`` 只留**最早出现的来源**（票 07 §6 的去重口径）；
    两名角色各有同一技能时是**两个实例**。

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
        if player.hero is None:
            continue
        for skill_id in hero_skill_ids(player.hero, pack_id=state.pack_id):
            definition = CATALOG.get(skill_id)
            if definition is not None:
                _add_instance(
                    found,
                    player.seat,
                    definition,
                    SkillSource(SkillSourceKind.HERO_CARD, skill_id=skill_id),
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
    deduped.sort(key=lambda item: (int(item.definition.priority_class), item.skill_id, item.owner))
    return tuple(deduped)


def instance_key(instance: SkillInstance) -> str:
    """实例键的规范文本（``GameState.skill_state`` 的键）。"""
    source = instance.source
    ref = source.card_id if source.card_id is not None else source.skill_id
    return f"{instance.owner}:{instance.skill_id}:{source.kind.value}:{ref}"


def prune_skill_state(state: GameState) -> None:
    """清理**来源已消失**的 per-instance 状态（票 07 §6 的"随 source 清理"）。"""
    live = {instance_key(instance) for instance in resolve_instances(state)}
    for key in list(state.skill_state):
        if key not in live:
            del state.skill_state[key]


def reset_phase_skill_state(state: GameState) -> None:
    """清理**本出牌阶段**的 per-instance 技能状态（票 04 §要做什么 2）。

    与"座位自己的 ``TURN_START`` 清空 ``card_use_counts``"同一重置口径（票 07 §6），
    只是节拍落在 ``PHASE_PLAY_START``：M3 里 ``skill_state`` 的**唯一写入者**是 A 族
    "出牌阶段限一次"（含【仁德】"首次达两张"）的计数，故按阶段整体清空即等于
    "每阶段限一次"的复位。装备技能不写 ``skill_state``；限定技 / 觉醒技的"已用"键
    按 ADR-008 改判 M5、M3 无载体——真出现**跨阶段**的 per-instance 状态时，这里必须
    升级成带作用域的清理（本函数是那个唯一的升级点）。
    """
    state.skill_state.clear()


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


def _matching_hooks(instance: SkillInstance, timing: Timing, host: Any, frame: Frame) -> tuple[Hook, ...]:
    """本实例在 ``timing`` 上**条件成立**的钩子（条件缺省 = 恒成立；票 07 的 ``condition``）。"""
    return tuple(
        hook
        for hook in instance.definition.hooks
        if hook.timing is timing
        and (hook.condition is None or hook.condition(host, frame))
    )


def _matching_seams(instance: SkillInstance, kind: SeamKind) -> tuple[Seam, ...]:
    return tuple(seam for seam in instance.definition.seams if seam.kind is kind)


def _dispatch(host: Any, frame: Frame, *, match: Any, timing: Timing, key: str) -> Control:
    """钩子与接缝共用的派发环（唯一一份）：锁定直接跑，非锁定先问"是否发动"。

    **只有答 ``ActivateSkill`` 才执行**——``Pass``（不发动）与 ``plan.available=False``
    （付不起）都跳过；这是"非锁定技的发动是询问出来的"这条规则（`GLOSSARY.md` 的**发动**）
    的唯一落点。暂停 = ``"stay"`，重入时整条链从头跑，但 `ask` 的应答按帧内键缓存。
    """
    for instance in resolve_instances(host.state):
        items = match(instance)
        if not items:
            continue
        if not instance.definition.locked:
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
            control = item.handler(host, frame)
            if control is not None:
                return control
    return None


def dispatch_timing(host: Any, frame: Frame, timing: Timing) -> Control:
    """在 ``timing`` 上按链序派发钩子；**第一个返回非 ``None`` 的钩子即短路**。"""
    return _dispatch(
        host,
        frame,
        match=lambda instance: _matching_hooks(instance, timing, host, frame),
        timing=timing,
        key=timing.value,
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


def _axe_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    player = state.player(instance.owner)
    payable = len(player.hand) + len(player.equip)
    available = frame.data.get("source") == instance.owner and payable >= 2
    return ActivationPlan(
        zones=(Zone.HAND, Zone.EQUIP),
        min_picks=2,
        max_picks=2,
        optional=True,
        available=available,
    )


def _green_dragon_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    """【青龙偃月刀】：只有**自己的**【杀】被抵消才发动，载荷 = 挑哪张【杀】。"""
    available = frame.data.get("source") == instance.owner and _has_slash_in_hand(
        state, instance.owner
    )
    return ActivationPlan(
        zones=(Zone.HAND,),
        min_picks=1,
        max_picks=1,
        optional=True,
        available=available,
        card_types=("slash",),
    )


def _double_sword_plan(state: GameState, frame: Frame, instance: SkillInstance) -> ActivationPlan:
    source = frame.data.get("source")
    targets = tuple(frame.data.get("targets", ()))
    available = source == instance.owner and any(
        _opposite_genders(state, source, target) for target in targets
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
        and card.type == "slash"
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
    """``holder`` 是否**持有一张【杀】**且它就是本次查询的牌。"""
    return ctx.owner == holder and ctx.card is not None and ctx.card.type == "slash"


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
    """
    del state
    if not _own_slash(ctx, holder=ctx.subject):
        return current
    ctx.flags["ignore_armor"] = True
    return current


def _halberd_same_target_pool(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """方天画戟的 ``TARGET_POOL`` 挂点：**池不改**（票 12 §4 的"合法性同标准"）。"""
    del state, ctx
    return current


def _halberd_extra_targets(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """方天画戟：此【杀】是你**最后的手牌**时可多指定至多两个目标（`A/glossary_gamecard.txt:103-105`）。"""
    if not _own_slash(ctx, holder=ctx.subject):
        return current
    assert ctx.card is not None  # _own_slash 已保证
    if list(state.player(ctx.subject).hand) != [ctx.card.card_id]:
        return current
    base = next(
        (
            action
            for action in current
            if isinstance(action, UseCard) and action.card.card_id == ctx.card.card_id
        ),
        None,
    )
    if base is None:
        return current
    extras: list[Any] = []
    for extra in (1, 2):
        total = ctx.rated_targets + extra
        if len(ctx.pool) < total:
            continue
        extras.extend(
            base.model_copy(update={"targets": tuple(combo)})
            for combo in combinations(ctx.pool, total)
        )
    return (*current, *extras)


def _eight_diagrams_candidate(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """八卦阵：把你自己的 ``ActivateSkill`` 追加进"需要【闪】"的候选（票 10 §5.2 / 票 30）。

    只在**需要者自己**的实例上追加（``ctx.owner == ctx.subject``）。"每提供者只问一次"
    由**候选生成器**统一过滤 ``ctx.providers_used``（一处实现，不在这里重复）。
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
    """这次伤害是**该实例持有者**用【杀】造成的吗（票 12 §Q12 的触发前提）。"""
    if frame.data.get("source") != instance.owner:
        return False
    card = frame.data.get("card")
    return card is not None and card.type == "slash"


def _has_slash_in_hand(state: GameState, seat: int) -> bool:
    """手里有没有【杀】（【青龙偃月刀】的"付得起"判据）。"""
    return any(state.card(card_id).type == "slash" for card_id in state.player(seat).hand)


def _double_sword(host: Any, frame: Frame) -> Control:
    """雌雄双股剑：你使用【杀】指定**异性**目标后，令其选择"弃一张手牌"或"你摸一张"。

    选择权在**目标**（票 12 §Q17）：选项 = 弃一张手牌 / 令你摸一张；目标无手牌时
    "弃一张手牌"**不进候选**（付不起的组合不进候选，票 19 §6）。多目标时逐个目标处理，
    游标存帧内 ``double_sword_cursor``。
    """
    source = frame.data.get("source")
    targets = tuple(frame.data.get("targets", ()))
    state = host.state
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
                key=f"double_sword:{target}",
                actor=target,
                timing=Timing.USE_AFTER_DESIGNATE_TARGET,
                spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
                build_face=lambda: host.selection_face(target, (Zone.HAND,)),
            )
            if host.suspended(picked):
                return "stay"
            if picked:
                host.move_in_frame(
                    frame,
                    state.refs(picked),
                    to_zone=ZoneRef(Zone.DISCARD),
                    reason=MoveReason.DISCARD,
                    owner=target,
                    actor=target,
                )
        else:
            host.draw_for_hand(frame, source, 1)
        cursor += 1
        frame.data["double_sword_cursor"] = cursor
    return None


def _ice_sword(host: Any, frame: Frame) -> Control:
    """寒冰剑：你使用【杀】造成伤害时，**防止此伤害**并依次弃置目标的两张牌。

    "防止伤害"由**帧标签 ``PREVENTED``**（票 12 §Q15；票 08 起住帧标签袋）承担：
    跳体力扣减、不新增 delta / 时机；"依次"⇒ 逐张单询问（票 19 §7），池空则跳过剩余次数。
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
        host.move_in_frame(
            frame,
            host.state.refs(picked),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.DISCARD,
            owner=target,
            actor=target,
        )
        done += 1
        frame.data["ice_sword_done"] = done
    frame.data["ice_sword_finished"] = True
    return None


def _mount_ids(state: GameState, seat: int) -> tuple[int, ...]:
    return tuple(
        card_id
        for card_id in state.player(seat).equip
        if state.card(card_id).slot in (SlotKind.HORSE_DEFENSE, SlotKind.HORSE_OFFENSE)
    )


def _mount_face(host: Any, seat: int) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    card_ids = _mount_ids(host.state, seat)
    return (
        tuple(
            Slot(ref=ZoneRef(Zone.EQUIP, seat), card=host.state.card(card_id))
            for card_id in card_ids
        ),
        card_ids,
    )


def _qilin_bow(host: Any, frame: Frame) -> Control:
    """麒麟弓：你使用【杀】造成伤害时，可弃置目标装备区里的一张**坐骑牌**。

    选取面只铺**坐骑槽**（目标装备区、可见槽）；目标没有坐骑时该技能不满足发动前提
    （形态的 ``available`` 已挡住，故不构造询问）。
    """
    target = frame.data.get("target")
    if target is None or frame.data.get("qilin_bow_done"):
        return None
    picked = host.ask_selection(
        frame,
        key="qilin_bow",
        actor=frame.data.get("skill_owner"),
        timing=Timing.DAMAGE_DEAL,
        spec=SelectionSpec(zones=(Zone.EQUIP,), total=(1, 1)),
        build_face=lambda: _mount_face(host, target),
    )
    if host.suspended(picked):
        return "stay"
    frame.data["qilin_bow_done"] = True
    if not picked:
        return None
    host.move_in_frame(
        frame,
        host.state.refs(picked),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=target,
        actor=frame.data.get("skill_owner"),
    )
    return None


def _green_dragon_blade(host: Any, frame: Frame) -> Control:
    """青龙偃月刀：**你的**【杀】被【闪】抵消时，可对相同目标再使用一张【杀】。

    续杀由发动询问的**载荷**指定（挑一张手牌里的【杀】），**无距离限制**
    （`A/flow_condition.txt:66-67`）由"目标直接沿用原【杀】的目标"表达（不再走
    ``check_targets``），且该次使用**不计次数**（票 12）；每张【杀】只给一次机会。
    """
    owner = frame.data.get("skill_owner")
    if frame.data.get("gdb_used") or frame.data.get("source") != owner:
        return None
    picks = host.activation_picks(frame, "equip.green_dragon_blade")
    if not picks:
        return None
    frame.data["gdb_used"] = True
    host.start_use(
        host.state.card(picks[0]),
        source=owner,
        targets=tuple(frame.data.get("targets", ())),
        use_timing=Timing.RESOLVE_BEFORE_EFFECT,
        counts=False,
    )
    return "stay"


def _move_by_source(
    host: Any, frame: Frame, card_ids: tuple[int, ...], *, to_zone: ZoneRef, owner: int
) -> None:
    """把一批选中的牌按**来源区**分组移动：一次 ``move_card`` 只能有一个来源区。

    【贯石斧】的成本可以跨手牌与装备区（`A/flow_move.txt:33-39`），故逐来源区各发一条
    ``card_move``；分组用 ``dict``（插入序确定），顺序逐种子可复现。
    """
    groups: dict[ZoneRef, list[int]] = {}
    for card_id in card_ids:
        groups.setdefault(locate(host.state, card_id), []).append(card_id)
    for ids in groups.values():
        host.move_in_frame(
            frame,
            host.state.refs(ids),
            to_zone=to_zone,
            reason=MoveReason.DISCARD,
            owner=owner,
            actor=owner,
        )


def _axe(host: Any, frame: Frame) -> Control:
    """贯石斧：**你的**【杀】被抵消时，可弃两张牌使此【杀】**依然造成伤害**。

    成本（弃两张）在"是否发动"询问里**烘进候选**；发起后打帧标签 ``SAVED``，
    ``resolve_before_effect`` 据此**跳过"生效前 / 生效时"、直进"生效后"**（票 12 Q16；
    标志自票 08 起住帧标签袋）。
    两张牌可跨手牌 / 装备区，故按来源区分别移动。
    """
    owner = frame.data.get("skill_owner")
    if frame.data.get("axe_used") or frame.data.get("source") != owner:
        return None
    card_ids = host.activation_picks(frame, "equip.axe")
    if not card_ids or owner is None:
        return None
    frame.data["axe_used"] = True
    _move_by_source(host, frame, card_ids, to_zone=ZoneRef(Zone.DISCARD), owner=owner)
    add_tag(frame, Tag.SAVED)
    return None


def _equip(
    card_type_id: str,
    *,
    locked: bool,
    modifiers: tuple[Modifier, ...] = (),
    hooks: tuple[Hook, ...] = (),
    seams: tuple[Seam, ...] = (),
) -> SkillDef:
    """按牌名 id 造一条装备技能定义（``skill_id = "equip." + 牌名 id``，票 09 §1）。"""
    return SkillDef(
        skill_id=f"equip.{card_type_id}",
        locked=locked,
        carrier=CarrierKind.EQUIPMENT,
        hooks=hooks,
        modifiers=modifiers,
        seams=seams,
    )


_DISTANCE_PLUS_ONE = (Modifier(ModifierPoint.DISTANCE, _mount_distance(1)),)
_DISTANCE_MINUS_ONE = (Modifier(ModifierPoint.DISTANCE, _mount_distance(-1)),)

_SKILL_DEFS: tuple[SkillDef, ...] = (
    # 9 名武器 / 10 实体张（诸葛连弩 ×2）
    _equip(
        "crossbow",
        locked=True,
        modifiers=(Modifier(ModifierPoint.COUNT_LIMIT, _unlimited_slash),),
    ),
    _equip(
        "qinggang_sword",
        locked=True,
        modifiers=(Modifier(ModifierPoint.EFFECTIVENESS, _qinggang_ignore_armor),),
    ),
    _equip(
        "double_sword",
        locked=False,
        hooks=(Hook(Timing.USE_AFTER_DESIGNATE_TARGET, _double_sword),),
    ),
    _equip(
        "green_dragon_blade",
        locked=False,
        seams=(Seam(SeamKind.SLASH_CANCELED, _green_dragon_blade),),
    ),
    _equip(
        "serpent_spear",
        locked=False,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, _serpent_spear_candidate),),
    ),
    _equip(
        "axe",
        locked=False,
        seams=(Seam(SeamKind.SLASH_CANCELED, _axe),),
    ),
    _equip(
        "halberd",
        locked=False,
        modifiers=(
            Modifier(ModifierPoint.TARGET_POOL, _halberd_same_target_pool),
            Modifier(ModifierPoint.CANDIDATES, _halberd_extra_targets),
        ),
    ),
    _equip(
        "qilin_bow",
        locked=False,
        hooks=(Hook(Timing.DAMAGE_DEAL, _qilin_bow),),
    ),
    _equip(
        "ice_sword",
        locked=False,
        hooks=(Hook(Timing.DAMAGE_DEAL, _ice_sword),),
    ),
    # 3 防具
    _equip(
        "eight_diagrams",
        locked=False,
        modifiers=(Modifier(ModifierPoint.CANDIDATES, _eight_diagrams_candidate),),
    ),
    _equip(
        "renwang_shield",
        locked=True,
        modifiers=(Modifier(ModifierPoint.EFFECTIVENESS, _renwang_effectiveness),),
    ),
    # 6 坐骑：−1 进攻马（赤兔 ♥5）/ +1 防御马（绝影 ♠5）/ +1 防御马（的卢 ♣5）/
    # +1 防御马（爪黄飞电 ♥K）/ −1 进攻马（大宛 ♠K）/ −1 进攻马（紫骍 ♦K）
    _equip("chitu", locked=True, modifiers=_DISTANCE_MINUS_ONE),
    _equip("jueying", locked=True, modifiers=_DISTANCE_PLUS_ONE),
    _equip("dilu", locked=True, modifiers=_DISTANCE_PLUS_ONE),
    _equip("zhuahuang_feidian", locked=True, modifiers=_DISTANCE_PLUS_ONE),
    _equip("dawan", locked=True, modifiers=_DISTANCE_MINUS_ONE),
    _equip("zixing", locked=True, modifiers=_DISTANCE_MINUS_ONE),
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
        )
    }
)
"""定义目录：``skill_id`` → :class:`SkillDef`，**模块级不可变**（票 07 §1 / §6）。

装备技能（17 条）与武将技能（票 03 起逐族落地）**共用同一张表**：载体解析、优先级档、
修饰器链、时机派发都只认这张表，不区分两类载体。合并点在**文件末尾**（而不是模块头部的
import）是为了让 :mod:`sgs.skills.heroes` 能反向 import 本模块的 :class:`SkillDef`——
`heroes` 在 import 期读到的 :data:`CATALOG` 是装备那半张，而它的定义由这里的
:func:`sgs.skills.heroes.conversion_skill_definitions` 现取现并，故两半都不会漏。
"""
