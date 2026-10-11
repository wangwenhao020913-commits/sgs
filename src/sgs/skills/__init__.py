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

from .catalog import (
    ACTIVATION_PLANS,
    CATALOG,
    ActivationPlan,
    Hook,
    Seam,
    SeamKind,
    SkillDef,
    SkillInstance,
    SkillSource,
    SkillSourceKind,
    activation_plan,
    dispatch_seam,
    dispatch_timing,
    instance_key,
    is_equipped,
    lord_skill_allowed,
    lord_skill_ids,
    prune_skill_state,
    reset_phase_skill_state,
    resolve_instances,
)
from .active import (
    ACTIVATIONS,
    ACTIVE_SKILLS,
    CANDIDATE_CAP,
    ActiveSkill,
    activate_skill,
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

REJUDGE_SKILL_IDS: frozenset[str] = frozenset()
"""有**改判**能力的技能 id（【鬼才】/【鬼道】一类）。

标准版里改判全在武将技能上，而武将技能整类 out of scope ⇒ M2 恒空。
判定帧的改判窗口因此**真 dispatch 但队列恒空**（票 10 §4）。
"""

__all__ = (
    "ACTIVATIONS",
    "ACTIVATION_PLANS",
    "ACTIVE_SKILLS",
    "CANDIDATE_CAP",
    "CATALOG",
    "EQUIPMENT_CARD_TYPE_IDS",
    "EQUIPMENT_SKILL_IDS",
    "LORD_SKILL_IDS",
    "MAX_VIRTUAL_SOURCES",
    "MOVE_AFTER_SKILLS",
    "MOVE_EVENT_KEY",
    "REJUDGE_SKILL_IDS",
    "ActivationPlan",
    "ActiveSkill",
    "CarrierKind",
    "Hook",
    "Modifier",
    "ModifierContext",
    "ModifierPoint",
    "ModifierRole",
    "MoveAfterSkill",
    "MoveEvent",
    "PriorityClass",
    "Seam",
    "SeamKind",
    "SkillDef",
    "SkillInstance",
    "SkillSource",
    "SkillSourceKind",
    "SkillType",
    "VirtualKind",
    "VirtualSourceError",
    "activate_skill",
    "activation_plan",
    "apply_modifiers",
    "dispatch_move_after",
    "dispatch_seam",
    "dispatch_timing",
    "equipment_skill_ids",
    "instance_key",
    "is_equipped",
    "lord_skill_allowed",
    "lord_skill_ids",
    "move_after_providers",
    "move_after_skill_definitions",
    "move_event",
    "prune_skill_state",
    "pure_virtual_card",
    "rejudge_capable_seats",
    "reset_phase_skill_state",
    "resolve_instances",
    "transformed_card",
    "transformed_slash",
    "virtual_card_id",
    "virtual_dodge",
)


def equipment_skill_ids() -> frozenset[str]:
    """全部装备技能的 id 集合（只读；加载期跨层校验用）。"""
    return EQUIPMENT_SKILL_IDS


def rejudge_capable_seats() -> tuple[int, ...]:
    """当前**有改判能力**的座位（票 10 §4 的响应队列左项）。

    M2 没有改判技能（``REJUDGE_SKILL_IDS`` 为空），故恒为空元组——判定帧的改判响应
    队列因此恒空、无询问、无日志节点，"队列恒空即终态正确"。M3 立起技能实例
    （``(owner, skill_id, source)``）后在此把 ``REJUDGE_SKILL_IDS`` 解析成座位集合。
    """
    if REJUDGE_SKILL_IDS:
        raise NotImplementedError("M3 才有改判技能：座位的技能实例解析尚未建立")
    return ()
