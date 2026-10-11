"""修饰器管道的**词汇与派发**（票 07 §1–§3；施工票 29 落其静态面）。

本模块住 ``effects`` 层（`开发文档/01` §5「模块划分」：``effects`` 只依赖 ``state``），因为五个挂点
都由 :mod:`sgs.effects.actions` 的规则函数直接读（次数 / 目标池 / 距离 / 有效性 / 候选）。
技能**定义**（``SkillDef`` / ``Hook`` / ``CATALOG``）仍住 ``skills/``——``skills`` 依赖
``effects``，方向因此保持"上层依赖下层、反向不依赖"。

**链式管道**（票 07 §3）：``current`` 从默认值起，按调用方按
``(priority_class, skill_id, owner)`` 排好的顺序逐条交给命中的回调。选链式而非覆盖函数，
是因为 M2 真有两处多来源合成——**距离**（目标 +1 坐骑、自身 −1 坐骑，属不同角色）与
**青釭剑 × 仁王盾**（顺序与可抑制，票 30）。

"谁持有哪一份"由 :func:`sgs.skills.resolve_instances` 解析（M2 = 装备区 ∪ 虚拟授予，
M3 起再加**武将牌**）；本模块只在
**函数体内**延迟导入它——与 ``data/loader.py`` 调 ``effects`` / ``skills`` 同姿态：
模块级不建立反向依赖，运行期调用发生在两个模块都已加载之后。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from typing import Any, TypeAlias

from sgs.state import CardRef, GameState, RespondMode

__all__ = [
    "CandidateScope",
    "CarrierKind",
    "Modifier",
    "ModifierContext",
    "ModifierPoint",
    "ModifierRole",
    "PriorityClass",
    "apply_modifiers",
    "priority_class_of",
]


class CandidateScope(str, Enum):
    """一次 ``CANDIDATES`` 派发发生在哪种"需要牌"语境（票 30）。

    ``CARD`` = 逐张牌的常规候选（M1 姿态、``ctx.card`` 非空）；``PLAY`` / ``DODGE`` /
    ``RESPOND`` = 整段候选期的**通用追加**（虚拟牌不绑实体手牌）。用枚举而不是裸字符串：
    写入端（``effects/actions.py``）与读取端（``skills/catalog.py``）共享同一个闭集，
    新增语境只改这一处。
    """

    CARD = "CARD"
    PLAY = "PLAY"
    DODGE = "DODGE"
    RESPOND = "RESPOND"


class CarrierKind(str, Enum):
    """技能实例的**载体**（票 07 §1 的"派发时先解析谁持有哪一份"）。

    M2 只有装备区；M3 加 **``HERO``**（武将牌，票 01）。``IDENTITY`` 按 `03` §4「技能描述模型」的冻结面
    是"只许增"的下一个加法，M3 **不落载体**——主公技的载体是**武将牌**、不是身份牌。
    """

    EQUIPMENT = "EQUIPMENT"
    HERO = "HERO"


class PriorityClass(IntEnum):
    """修饰器链的**优先级档**：武将 > 装备 > 使用牌（`02` §3.5(3) / 票 07 §6）。

    值序即链序（越小越先），故"武将档先于装备档"由排序直接表达，不靠特判。M2 的载体
    只有装备档，另两档是链序定义的一部分（票 29 的清单明列三档）。
    """

    HERO = 0
    EQUIPMENT = 1
    CARD_USE = 2


_CARRIER_PRIORITY: dict[CarrierKind, PriorityClass] = {
    CarrierKind.EQUIPMENT: PriorityClass.EQUIPMENT,
    CarrierKind.HERO: PriorityClass.HERO,
}


def priority_class_of(carrier: CarrierKind) -> PriorityClass:
    """载体 → 优先级档（由 :data:`_CARRIER_PRIORITY` 派生，不另存冗余字段）。"""
    return _CARRIER_PRIORITY[carrier]


class ModifierPoint(str, Enum):
    """修饰器的五个**挂点**（票 07 §3）；M3 只许增。

    - ``COUNT_LIMIT``：本回合使用次数上限（诸葛连弩）；
    - ``TARGET_POOL``：目标池（方天画戟的"额外目标合法性同标准"挂点）；
    - ``DISTANCE``：座位距离（六坐骑：客体 +1 / 主体 −1）；
    - ``EFFECTIVENESS``：第 2 层"无效"（仁王盾）；
    - ``CANDIDATES``：候选行动的追加（方天画戟的额外目标组合）。
    """

    COUNT_LIMIT = "COUNT_LIMIT"
    TARGET_POOL = "TARGET_POOL"
    DISTANCE = "DISTANCE"
    EFFECTIVENESS = "EFFECTIVENESS"
    CANDIDATES = "CANDIDATES"


class ModifierRole(str, Enum):
    """本次查询里**主体 / 客体**的角色标注（票 07 §3 的 ``ctx`` 内容之一）。

    ``ctx`` 必须带得出"主体 / 客体 / 本次扮演的角色"，否则【马术】（主体向）与
    +1 坐骑（客体向）无法在同一 ``DISTANCE`` 挂点上共存。M2 的距离挂点靠
    ``owner == subject / target`` 判定，``role`` 供 M3 的更多挂点使用。
    """

    SUBJECT = "SUBJECT"
    TARGET = "TARGET"


ModifierHandler: TypeAlias = Callable[[GameState, "ModifierContext", Any], Any]
"""修饰器回调：``(state, ctx, current) -> new_value``（普通 Python 回调，条件内联其中）。"""


@dataclass(frozen=True, slots=True)
class ModifierContext:
    """修饰器回调的上下文：**主体 / 客体 / 角色 / 牌**，外加候选生成的两项载荷。

    ``owner`` 由 :func:`apply_modifiers` 在派发时填写——它就是"当前这条修饰器属于谁"，
    回调据此判定本实例是否作用于本次查询（如 +1 坐骑要求 ``owner == target``）。

    ``pool`` / ``rated_targets`` 只给 ``CANDIDATES`` 挂点用：候选生成时把**已过
    TARGET_POOL 的目标池**与**本牌额定目标数**一并交给回调，回调便不必回头 import
    行为表（那会与 ``effects`` 成环），"额外目标过同一套合法性检测"由共用同一个池保证。

    票 30 追加四样**派发期载荷**（都由 :func:`apply_modifiers` 或调用点填写）：

    - ``skill_id``：当前这条修饰器属于哪个技能实例——【八卦阵】把候选的"提供者 id"
      写进 ``ActivateSkill``，回调自己得知道它；
    - ``answers_seq``：候选生成时的询问序号，回调造行动要用它（候选构造子必带）；
    - ``providers_used``：本次"需要牌"流程里**已经问过**的提供者 id——"每提供者只问
      一次"据此实现；
    - ``flags``：链**内部**的旁路标志（【青釭剑】置 ``ignore_armor``、【仁王盾】读它）。
      它是同一次查询里的临时便签，不是状态；用可变 ``dict`` 让链上先跑的回调能影响
      后跑的回调（链序 = ``(priority_class, skill_id)``，青釭剑在仁王盾之前）。
    """

    subject: int
    target: int | None = None
    role: ModifierRole = ModifierRole.SUBJECT
    card: CardRef | None = None
    owner: int = -1
    pool: tuple[int, ...] = ()
    rated_targets: int = 0
    skill_id: str = ""
    answers_seq: int = 0
    providers_used: tuple[str, ...] = ()
    scope: CandidateScope = CandidateScope.CARD
    """候选生成发生在哪种"需要牌"语境（见 :class:`CandidateScope`）。

    ``CANDIDATES`` 回调据此分流（【丈八蛇矛】只在出牌 / 需要【杀】的响应里追加，
    【八卦阵】只在需要【闪】的窗口里追加）；``CANDIDATE`` = 逐张牌的常规候选。
    """
    respond_required: str | None = None
    respond_mode: RespondMode | None = None
    respond_targets: tuple[int, ...] = ()
    flags: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Modifier:
    """一条修饰器：挂点 + 回调（**无 ``conditions`` 字段**，条件内联在回调里）。"""

    point: ModifierPoint
    handler: ModifierHandler


def apply_modifiers(
    point: ModifierPoint, state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """在 ``point`` 挂点上跑**链式管道**：``current`` 逐条交给命中的回调，返回终值。

    链序 = ``(priority_class, skill_id, owner)``（由 :func:`sgs.skills.resolve_instances`
    排好）；``ctx.owner`` 在派发时按当前实例填写，回调据此判定"本实例是否作用于本次
    查询"。没有命中任何修饰器时**原样返回**（故 `basic_53` 的 M1 行为逐字节不变）。
    """
    from sgs.skills import resolve_instances

    value = current
    for instance in resolve_instances(state):
        for modifier in instance.definition.modifiers:
            if modifier.point is point:
                value = modifier.handler(
                    state,
                    replace(ctx, owner=instance.owner, skill_id=instance.skill_id),
                    value,
                )
    return value
