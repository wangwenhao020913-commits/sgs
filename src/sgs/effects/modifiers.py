"""修饰器管道的**词汇与派发**（票 07 §1–§3；施工票 29 落其静态面）。

本模块住 ``effects`` 层（`开发文档/01` §5「模块划分」：``effects`` 只依赖 ``state``），因为六个挂点
都由 :mod:`sgs.effects.actions` 的规则函数直接读（次数 / 目标池 / 目标数 / 距离 / 有效性 / 候选）。
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

from .behaviors import DamageNature

__all__ = [
    "CandidateScope",
    "CarrierKind",
    "Modifier",
    "ModifierContext",
    "ModifierPoint",
    "ModifierRole",
    "PriorityClass",
    "WINE_DAMAGE_BONUS",
    "apply_modifiers",
    "damage_amount",
    "priority_class_of",
    "recover_amount",
    "response_count",
]


class CandidateScope(str, Enum):
    """一次 ``CANDIDATES`` 派发发生在哪种"需要牌"语境（票 30）。

    ``CARD`` = 逐张牌的常规候选（M1 姿态、``ctx.card`` 非空）；``PLAY`` / ``DODGE`` /
    ``RESPOND`` / ``PEACH`` / ``NULLIFY`` = 整段候选期的**通用追加**（虚拟牌不绑实体手牌）。
    用枚举而不是裸字符串：写入端（``effects/actions.py``）与读取端（``skills/catalog.py``）
    共享同一个闭集，新增语境只改这一处。

    四类"需要牌"语境的差别只在**窗口要什么**：``PLAY`` 是出牌阶段（任意牌）、``DODGE`` 是
    【杀】的【闪】窗口、``RESPOND`` 是打出 / 使用的响应窗口（``respond_required`` 说牌名）、
    ``PEACH`` 是濒死求【桃】、``NULLIFY`` 是无懈的收集式窗口。**提供者**（【蛊惑】）按它
    收窄声明范围；``PEACH`` / ``NULLIFY`` 是票 03 的加法（那两扇窗口此前根本没有
    ``CANDIDATES`` 派发）。
    """

    CARD = "CARD"
    PLAY = "PLAY"
    DODGE = "DODGE"
    RESPOND = "RESPOND"
    PEACH = "PEACH"
    NULLIFY = "NULLIFY"


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


WINE_DAMAGE_BONUS = 1
"""【酒】的加伤值（spec §5.1 酒）：出牌阶段使用后，本回合**下一张【杀】**的来源侧 +1。"""


_CARRIER_PRIORITY: dict[CarrierKind, PriorityClass] = {
    CarrierKind.EQUIPMENT: PriorityClass.EQUIPMENT,
    CarrierKind.HERO: PriorityClass.HERO,
}


def priority_class_of(carrier: CarrierKind) -> PriorityClass:
    """载体 → 优先级档（由 :data:`_CARRIER_PRIORITY` 派生，不另存冗余字段）。"""
    return _CARRIER_PRIORITY[carrier]


class ModifierPoint(str, Enum):
    """修饰器的**挂点**（票 07 §3）；M3 只许增。

    - ``COUNT_LIMIT``：本回合使用次数上限（诸葛连弩 / 【咆哮】）；
    - ``DISTANCE_LIMIT``：本牌**额定距离上限**（M6 票 02 的加法；【奇才】把锦囊的
      ``TargetRule.max_distance`` 豁免成"无上限"）。``current`` 从 ``max_distance`` 起，
      返回 ``None`` ⇒ 这次使用的距离条件整体豁免。只在 ``max_distance is not None`` 的牌上
      派发——【杀】的"距离 ≤ 攻击范围"是另一条判据（``needs_distance``），不在本挂点，
      故【奇才】不会顺带放宽【杀】；
    - ``HAND_LIMIT_REPLACE``：**手牌上限的替换式**（M6 票 08 的加法，spec D11 的
      "上限修正链"第二段）。``current`` 从**当前体力值**（下限 0）起，回调返回替换后的
      上限——界周瑜【英姿】② 的「手牌上限 = 体力上限」与袁绍移动版【血裔】的「查询期
      持续加 2×其他存活群势力角色数」是仅有的两个消费者（huo-mobile-batch 票 03 / 08
      读法 A：**持续加式可以挂在替换链上**，回调在 ``current`` 上加）。**一次性**加式
      **不**走本挂点：它是 D9 的一次性效果，住 ``Player.hand_limit_bonus``、查询期由
      :func:`~sgs.effects.hand_limit` 在替换链之后再加，故「来源技能失去 / 失效后
      替换式立刻消失、已落的一次性增量不撤回」是结构事实，不靠回调自己判；
    - ``TARGET_POOL``：目标池（方天画戟的"额外目标合法性同标准"挂点）；
    - ``TARGET_COUNT``：本牌**最大目标数**（方天画戟 +2；只对 ``TargetCount.RATED``
      的牌查链，见下）；
    - ``DISTANCE``：座位距离（六坐骑：客体 +1 / 主体 −1）；
    - ``EFFECTIVENESS``：第 2 层"无效"（仁王盾）；
    - ``FORBIDDEN``：L3.1"这张牌不能按原用途使用 / 打出"（票 05；神关羽【武神】①）。
      ``current`` 从 ``False`` 起，回调把"这一张此刻必须被禁"写成 ``True``；空链恒
      ``False``，故既有技能零变化。它只覆盖"使用 / 打出"——弃置、重铸、技能成本、
      他人把牌置入装备区都不经过这个挂点；
    - ``CANDIDATES``：候选行动的追加（丈八蛇矛 / 八卦阵的候选注入）；
    - ``RESPONSE_COUNT``：本次响应窗口**需要几张**（缺省 1；票 08 的 M3 加法）；
    - ``RECOVER_AMOUNT``：本次回复**回复几点**（缺省 1；票 09 的 M3 加法，【救援】）；
    - ``DAMAGE_DEAL_AMOUNT`` / ``DAMAGE_TAKE_AMOUNT``：本次伤害的**来源侧 / 目标侧**数值
      （M5-03 的加法；【酒】/【古锭刀】与【藤甲】/【白银狮子】的落点）；
    - ``DAMAGE_CAP``：本次伤害的**目标侧上限**（M5-05a 的加法；【白银狮子】"最多 1 点"）。
      它**排在** ``DAMAGE_TAKE_AMOUNT`` 之后（见 :func:`damage_amount`）：先让所有加法落地，
      再封顶——把 cap 写进来源侧链会让"加一点"永远先于"最多一点"，次序就反了。

    ``TARGET_COUNT`` 的语义（W7-01）：查询期修正"本牌**最大**目标数"，``current`` 从
    **额定目标数**起（``TargetRule.rated_targets``；【借刀杀人】的被迫使用窗口 base = 1，
    不是池大小），回调返回新的最大值（方天 = ``current + 2``，未来天义 = ``current + 1``
    ——可叠加）；链序仍是 ``(priority_class, skill_id, owner)``。**只对
    ``TargetCount.RATED`` 的牌生效**：``ALL_ALIVE`` / ``ALL_ALIVE_WITH_SELF``（南蛮 /
    万箭 / 桃园 / 五谷）的目标数由候选池决定，查链会造出**非法子集**，故读者
    （``_play_phase`` / ``_respond`` 的 USE 趟 / ``virtual_card_use``）只在 RATED 档派发。

    **伤害数值为什么分成两个挂点**（M5-03）：连环传导的合同要求"来源侧加伤只算一次、
    传导目标只再吃自己的受到伤害修正"（spec §5.1.1 第 6 条）。两个挂点把这条读法变成
    **结构事实**：:func:`damage_amount` 只在 ``chain=False`` 的源头伤害帧上派发
    ``DAMAGE_DEAL_AMOUNT``，而 ``DAMAGE_TAKE_AMOUNT`` 每一帧都派发——传导帧因此不可能
    重算来源侧加成，不需要任何回调自己判"这次是不是传导"。
    """

    COUNT_LIMIT = "COUNT_LIMIT"
    DISTANCE_LIMIT = "DISTANCE_LIMIT"
    HAND_LIMIT_REPLACE = "HAND_LIMIT_REPLACE"
    TARGET_POOL = "TARGET_POOL"
    TARGET_COUNT = "TARGET_COUNT"
    DISTANCE = "DISTANCE"
    EFFECTIVENESS = "EFFECTIVENESS"
    FORBIDDEN = "FORBIDDEN"
    CANDIDATES = "CANDIDATES"
    RESPONSE_COUNT = "RESPONSE_COUNT"
    RECOVER_AMOUNT = "RECOVER_AMOUNT"
    DAMAGE_DEAL_AMOUNT = "DAMAGE_DEAL_AMOUNT"
    DAMAGE_TAKE_AMOUNT = "DAMAGE_TAKE_AMOUNT"
    DAMAGE_CAP = "DAMAGE_CAP"


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
    """修饰器回调的上下文：**主体 / 客体 / 角色 / 牌**，外加派发期的载荷。

    ``owner`` 由 :func:`apply_modifiers` 在派发时填写——它就是"当前这条修饰器属于谁"，
    回调据此判定本实例是否作用于本次查询（如 +1 坐骑要求 ``owner == target``）。

    ``CANDIDATES`` / ``TARGET_COUNT`` 回调**不读**候选池或额定目标数：候选由调用方按
    已过 ``TARGET_POOL`` 的池生成，``TARGET_COUNT`` 的 ``current`` 已经是额定目标数
    （W7-01 曾把 ``pool`` / ``rated_targets`` 写进本上下文，但没有任何回调读它们；
    W7-03 按"零读者即死字段"删掉，额定数 / 池仍由调用方自己算）。

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
    respond_declared: bool = False
    """这扇**收集式**窗口已经收到过一条声明（票 03）：赢家已定，提供者的产物不会进结算。

    读者只有提供者的 ``CANDIDATES`` 回调（【蛊惑】）：它必须在这之后闭嘴——产物是**扣置一张
    手牌**换来的，赢家已定时再发候选就是白扣一张牌。真牌候选不受影响（真牌声明不耗牌）。
    """
    flags: dict[str, Any] = field(default_factory=dict)
    nature: DamageNature | None = None
    """本次伤害的**属性**（M5-05a 的加法；``DAMAGE_TAKE_AMOUNT`` / ``DAMAGE_CAP`` 用）。

    【藤甲】的"火焰伤害 +1"按它判火 / 雷 / 普通；属性的真源仍是 DAMAGE 帧的
    ``frame.data["nature"]``（由行为表的 ``PushFrame`` 声明，D2），
    :func:`damage_amount` 只负责把它透传进本次链，**不给 ``CardRef`` 加属性字段**。
    """


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


def response_count(
    state: GameState,
    *,
    source: int,
    card: CardRef | None,
    target: int | None = None,
    role: ModifierRole = ModifierRole.SUBJECT,
) -> int:
    """本次响应窗口**需要几张**（缺省 1，票 08 §要做什么 1–2）。

    "抵消【杀】"与"响应【决斗】"两处数数只读这一处真源（``_dodge_window`` 与
    ``resolve_duel``），故"需连续两张【闪】/【杀】"由一个修饰值表达，引擎里不出现技能名。
    ``ctx.subject`` = **本次使用的来源**（谁用的牌），修饰器回调据此比对自己是不是
    那张牌的使用者；``ctx.card`` = 本次使用的牌（牌名读它）。

    ``target`` 在 `RESPONSE_COUNT` 语境里是**这张牌指定的目标**（整场不变）；``role``
    （票 23 的加法，缺省 ``SUBJECT``）说明**这一问的响应者扮演谁**：``TARGET`` = 响应者就是
    那个指定的目标，``SUBJECT`` = 响应者是牌的使用者。两个事实合起来才能问出"这一问的响应者
    是不是对手"。目前唯一的读者是【无双】②（【决斗】）：牌面把"两张【杀】"加在**吕布的对手**
    身上，而对手可能是使用者、也可能是目标（"成为其他角色【决斗】的目标后"），只比
    ``owner == subject`` 会漏掉反向那一半。
    """
    ctx = ModifierContext(subject=source, target=target, card=card, role=role)
    return apply_modifiers(ModifierPoint.RESPONSE_COUNT, state, ctx, 1)


def responder_role(target: int | None, designated: int | None) -> ModifierRole:
    """本次响应窗口里**响应者扮演谁**：就是这张牌指定的目标 ⇒ ``TARGET``，否则 ``SUBJECT``。

    :func:`response_count` 的 ``role`` 实参由它派生，引擎侧（【决斗】结算按"当前该谁响应"
    与"被决斗者"）与网页侧（询问的 ``target`` 与载荷的 ``targets[0]``）**读同一句判据**——
    两处各写一次"相等比较"，就是同一条规则的第二份实现。
    """
    if target is None or designated is None:
        return ModifierRole.SUBJECT
    return ModifierRole.TARGET if target == designated else ModifierRole.SUBJECT


def recover_amount(
    state: GameState,
    *,
    source: int,
    card: CardRef | None,
    target: int,
    base: int = 1,
) -> int:
    """本次回复**回复几点**（缺省 ``base`` = 1，票 09 §要做什么 3；【救援】）。

    `三国杀详细规则.txt:425`："确定回复数值：先结算修正本次回复数值的效果（如【救援】），
    然后角色按此数值回复体力。" 故唯一读点是 ``RECOVER`` 帧的"确定回复数值"步骤
    （``steps.recover_value``）；回调自己按 ``ctx`` 判势力 / 牌名 / 目标，引擎里不出现技能名。

    ``ctx.subject`` = **回复的来源**（谁用的【桃】）、``ctx.target`` = 被回复者、
    ``ctx.card`` = 造成这次回复的牌（读牌名）。
    """
    ctx = ModifierContext(subject=source, target=target, card=card)
    return apply_modifiers(ModifierPoint.RECOVER_AMOUNT, state, ctx, base)


def damage_amount(
    state: GameState,
    *,
    source: int | None,
    target: int,
    card: CardRef | None,
    base: int,
    chain: bool,
    deal_bonus: int = 0,
    nature: DamageNature | None = None,
) -> int:
    """本次伤害的**最终数值**（M5-03；M5-05a 补封顶段与属性透传）。

    三段链、一个读者，`DAMAGE` 帧的"受到伤害时"之后（``steps.damage_finalize_amount``）
    是唯一调用点：

    1. ``DAMAGE_DEAL_AMOUNT``（**来源侧**：酒 / 古锭刀）——只有 ``chain=False`` 的源头伤害
       才派发。传导帧的基数是初始目标**实际受到**的伤害值，来源侧加成已经烘进那个数里，
       重算就会翻倍（spec §5.1.1 第 6 条）。派发时 ``ctx.role = SUBJECT``（主体 = 来源）。
    2. ``DAMAGE_TAKE_AMOUNT``（**目标侧**：藤甲 / 白银）——**每一帧**都派发，包括传导帧；
       回调按 ``ctx.target == ctx.owner`` 认自己。``ctx.role = TARGET``。
    3. ``DAMAGE_CAP``（**目标侧上限**：白银狮子"最多 1 点"）——在**所有加法之后**、对
       **每一帧**派发（M5-05a）。放这里而不是来源侧链，是"先加后封"的顺序事实：
       ``DAMAGE_TAKE_AMOUNT`` 的 +1 与封顶同属目标侧，封顶读到的必须是加完的数。

    ``deal_bonus``（M5-04）是**使用级**的来源侧加伤（【酒】兑现给本回合下一张【杀】的 +1，
    由 ``Engine.start_use`` 消费 ``wine_active`` 时写进使用帧、随帧传到伤害帧）。它和
    ``DAMAGE_DEAL_AMOUNT`` 链上的技能修正一样只在源头帧生效：先烘进 ``base`` 再交给链，
    加法可交换，故与古锭刀等来源侧 +1 的先后次序不影响总和；``chain=True`` 的门槛把它与
    链一起挡在外面（传导不重算来源侧）。

    ``nature``（M5-05a）是 DAMAGE 帧的属性（由 ``steps.damage_finalize_amount`` 原样传入），
    只进 ``ctx.nature`` 供目标侧回调读（藤甲火焰 +1）；属性本身不落到牌上。

    链序仍是 ``(priority_class, skill_id, owner)``；没有命中任何修饰器时返回 ``base``
    （标准 108 无任何伤害数值修饰器 ⇒ 逐字节不变）。
    """
    ctx = ModifierContext(
        subject=source if source is not None else -1,
        target=target,
        role=ModifierRole.TARGET,
        card=card,
        nature=nature,
    )
    value = base
    if not chain:
        value = apply_modifiers(
            ModifierPoint.DAMAGE_DEAL_AMOUNT,
            state,
            replace(ctx, role=ModifierRole.SUBJECT),
            value + deal_bonus,
        )
    value = apply_modifiers(ModifierPoint.DAMAGE_TAKE_AMOUNT, state, ctx, value)
    return apply_modifiers(ModifierPoint.DAMAGE_CAP, state, ctx, value)
