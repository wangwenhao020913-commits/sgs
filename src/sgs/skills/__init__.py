"""技能注册表：``skills/`` 是技能定义的**唯一入口**。

模块级内容分两层：

- :mod:`sgs.effects.modifiers`（``effects`` 层）装**修饰器管道的词汇与派发**：
  ``ModifierPoint`` 五挂点 / ``ModifierContext`` / ``Modifier`` / ``apply_modifiers``。
  它们住下层，是因为五个挂点都由 ``effects/actions.py`` 的规则函数直接读；本子包把
  这些名字连同 :class:`~sgs.skills.catalog.SkillDef` / ``CATALOG`` 一起再导出，外部
  仍只认 ``sgs.skills`` 这一个入口。
- :mod:`sgs.skills.catalog` 装**定义与派发**：``SkillDef`` / ``Hook`` / ``Seam`` /
  ``CATALOG`` / ``resolve_instances`` / ``dispatch_timing`` / ``dispatch_seam``。
  票 29 落其**静态面**（诸葛连弩 / 仁王盾 / 六坐骑 / 方天画戟），票 30 在同一张表上补
  时机钩子、帧内接缝与虚拟牌。
- :mod:`sgs.skills.virtual` 装**虚拟牌 / 转化牌**的构造（票 30）。

本文件另装**跨层只读查询**：加载期用它校验"每张装备牌都有对应技能"（票 09 §7 #8），
判定帧用它拿改判能力座位（票 10 §4），主公技门控用它（票 13 §4）。

装备**牌名 id 不带前缀**（``crossbow``），装备**技能 id = ``equip.`` + 牌名 id**
（``equip.crossbow``）——两者不可混（票 09 §1）。
"""

from __future__ import annotations

from sgs.data import SkillType
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    ModifierRole,
    PriorityClass,
    apply_modifiers,
)
from sgs.state import GameState

from .catalog import (
    ACTIVATION_PLANS,
    CATALOG,
    DAMAGE_POINT_KEY,
    DAMAGE_RECORDED_KEY,
    ActivationPlan,
    Hook,
    RUNNING_SKILL_KEY,
    Seam,
    SeamKind,
    SkillDef,
    SkillInstance,
    SkillSource,
    SkillSourceKind,
    activation_plan,
    damage_point_index,
    dispatch_seam,
    dispatch_timing,
    instance_key,
    is_equipped,
    lord_skill_allowed,
    lord_skill_ids,
    prune_skill_state,
    rejudge_skill_ids,
    reset_phase_skill_state,
    resolve_instances,
    same_kingdom,
    timing_hooks_present,
)
from .arrange import (
    GUANXING,
    GUANXING_MAX,
    arrange_activation_plans,
    arrange_skill_definitions,
    guanxing_handler,
)
from .damage_after import (
    DAMAGE_AFTER_SKILLS,
    DamageAfterSkill,
    damage_after_skill_definitions,
)
from .active import (
    ACTIVATIONS,
    ACTIVE_SKILLS,
    CANDIDATE_CAP,
    ActiveSkill,
    PickCards,
    PickOption,
    PickSeats,
    SkillFlow,
    activate_skill,
    run_flow,
)
from .move_after import (
    MOVE_AFTER_SKILLS,
    MOVE_EVENT_KEY,
    MoveAfterSkill,
    MoveEvent,
    dispatch_move_after,
    move_after_providers,
    move_after_skill_definitions,
    move_event,
)
from .virtual import (
    MAX_VIRTUAL_SOURCES,
    VirtualKind,
    VirtualSourceError,
    pure_virtual_card,
    transformed_card,
    transformed_slash,
    virtual_card_id,
    virtual_dodge,
)
from .lord_request import (
    HUJIA,
    JIJIANG,
    JIUYUAN,
    LORD_REQUEST_KEY,
    LORD_REQUEST_SKILLS,
    PROVIDERS,
    PROVIDER_ACTOR_KEY,
    PROVIDER_SATISFIED_KEY,
    PROVIDER_SKILL_KEY,
    LordSkill,
    advance_provider,
    dispatch_provider,
    lord_skill_definitions,
    provider_registered,
    start_provider,
)
from .rejudge import (
    GUICAI,
    TIANDU,
    rejudge_activation_plans,
    rejudge_skill_definitions,
    tiandu_handler,
)
from .phase_body import (
    PHASE_BODY_SKILLS,
    PhaseBodySkill,
    phase_body_skill_definitions,
)
from .target_change import (
    LIULI,
    MASHU,
    target_change_skill_definitions,
)

EQUIPMENT_CARD_TYPE_IDS: tuple[str, ...] = tuple(
    skill_id.removeprefix("equip.")
    for skill_id, definition in CATALOG.items()
    if definition.carrier is CarrierKind.EQUIPMENT
)
"""标准版 17 张装备的**牌名 id**（9 武器 + 2 防具 + 6 坐骑），不带前缀。"""

EQUIPMENT_SKILL_IDS: frozenset[str] = frozenset(
    definition.skill_id
    for definition in CATALOG.values()
    if definition.carrier is CarrierKind.EQUIPMENT
)
"""装备**技能 id = ``equip.`` + 牌名 id**（票 09 §1）：由 ``CATALOG`` 派生，不另抄一遍。"""

LORD_SKILL_IDS: frozenset[str] = lord_skill_ids()
"""登记在**主公技类型**上的技能 id：:func:`lord_skill_ids` 对 ``CATALOG`` 的 **import 期快照**
（票 01 §载体接入 #8：M2 手抄空集 → M3 由定义字段派生）。

"这是不是主公技"因此只有一个真源（``SkillDef.skill_type``），不再是一张会与定义漂移的
名单；运行期的门控读定义字段（:func:`_identity_gates_out` / :func:`lord_skill_allowed`），
不读这个快照。
"""

REJUDGE_SKILL_IDS: frozenset[str] = rejudge_skill_ids()
"""有**改判**能力的技能 id（【鬼才】/【鬼道】一类）：:func:`rejudge_skill_ids` 对
``CATALOG`` 的 **import 期快照**（票 11 §①：M2 手抄空集 → M3 由定义字段派生）。

"这是不是改判技"因此只有一个真源（``SkillDef.rejudge_capable``），不再是一张会与定义
漂移的名单；运行期的座位解析读**存活实例**的定义字段（:func:`rejudge_capable_seats`），
不读这个快照。
"""

__all__ = (
    "ACTIVATIONS",
    "ACTIVATION_PLANS",
    "ACTIVE_SKILLS",
    "CANDIDATE_CAP",
    "CATALOG",
    "DAMAGE_AFTER_SKILLS",
    "DAMAGE_POINT_KEY",
    "DAMAGE_RECORDED_KEY",
    "EQUIPMENT_CARD_TYPE_IDS",
    "EQUIPMENT_SKILL_IDS",
    "GUANXING",
    "GUANXING_MAX",
    "GUICAI",
    "HUJIA",
    "JIJIANG",
    "JIUYUAN",
    "LIULI",
    "LORD_REQUEST_KEY",
    "LORD_REQUEST_SKILLS",
    "LORD_SKILL_IDS",
    "MASHU",
    "MAX_VIRTUAL_SOURCES",
    "MOVE_AFTER_SKILLS",
    "MOVE_EVENT_KEY",
    "PHASE_BODY_SKILLS",
    "PROVIDERS",
    "PROVIDER_ACTOR_KEY",
    "PROVIDER_SATISFIED_KEY",
    "PROVIDER_SKILL_KEY",
    "REJUDGE_SKILL_IDS",
    "RUNNING_SKILL_KEY",
    "ActivationPlan",
    "ActiveSkill",
    "CarrierKind",
    "DamageAfterSkill",
    "Hook",
    "LordSkill",
    "Modifier",
    "ModifierContext",
    "ModifierPoint",
    "ModifierRole",
    "MoveAfterSkill",
    "MoveEvent",
    "PhaseBodySkill",
    "PickCards",
    "PickOption",
    "PickSeats",
    "PriorityClass",
    "Seam",
    "SeamKind",
    "SkillDef",
    "SkillFlow",
    "SkillInstance",
    "SkillSource",
    "SkillSourceKind",
    "SkillType",
    "TIANDU",
    "VirtualKind",
    "VirtualSourceError",
    "activate_skill",
    "activation_plan",
    "advance_provider",
    "apply_modifiers",
    "arrange_activation_plans",
    "arrange_skill_definitions",
    "damage_after_skill_definitions",
    "damage_point_index",
    "dispatch_move_after",
    "dispatch_provider",
    "dispatch_seam",
    "dispatch_timing",
    "equipment_skill_ids",
    "guanxing_handler",
    "instance_key",
    "is_equipped",
    "lord_skill_allowed",
    "lord_skill_definitions",
    "lord_skill_ids",
    "move_after_providers",
    "move_after_skill_definitions",
    "move_event",
    "phase_body_skill_definitions",
    "prune_skill_state",
    "provider_registered",
    "pure_virtual_card",
    "rejudge_activation_plans",
    "rejudge_capable_seats",
    "rejudge_skill_definitions",
    "rejudge_skill_ids",
    "reset_phase_skill_state",
    "resolve_instances",
    "run_flow",
    "same_kingdom",
    "start_provider",
    "target_change_skill_definitions",
    "tiandu_handler",
    "timing_hooks_present",
    "transformed_card",
    "transformed_slash",
    "virtual_card_id",
    "virtual_dodge",
)


def equipment_skill_ids() -> frozenset[str]:
    """全部装备技能的 id 集合（只读；加载期跨层校验用）。"""
    return EQUIPMENT_SKILL_IDS


def rejudge_capable_seats(state: GameState) -> tuple[int, ...]:
    """当前**有改判能力**的座位（票 11 §①；判定帧的改判响应队列左项）。

    解析**存活实例**（``resolve_instances``：装备区 ∪ 虚拟授予 ∪ 武将牌，武将阵亡 /
    来源消失即自动出队），取其中定义带 :attr:`~sgs.skills.catalog.SkillDef.rejudge_capable`
    的实例的持有者。读定义字段而不是 :data:`REJUDGE_SKILL_IDS` 快照，与主公技门控读
    ``skill_type`` 同一姿态："能力"只有一个真源。

    场上没有改判能力者 ⇒ 空元组 ⇒ 判定帧不构造任何询问、无额外日志节点，
    "队列恒空即终态正确"的既有口径不破（M2 的 34 条基准一行不改）。
    """
    seats: list[int] = []
    for instance in resolve_instances(state):
        if instance.definition.rejudge_capable and instance.owner not in seats:
            seats.append(instance.owner)
    return tuple(seats)
