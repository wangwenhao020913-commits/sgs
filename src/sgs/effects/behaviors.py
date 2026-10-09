"""牌行为表：声明式的"用这张牌会发生什么"（票 07 §2）。

步骤只有两类（票 07 §1 的两层切分）：

- :class:`PushFrame` —— 压出一个带时机的结算帧（子帧按约定继承使用帧的
  ``source`` / ``targets`` / ``card``，``scalars`` 只放标量字面量）；
- :class:`CallOp` —— 直接调一个叶子原语，操作名进 :data:`sgs.effects.OPS` 注册表。

**不建** conditions、不建取值表达式：条件内联在回调里（票 07 §4）。
行为表是"使用时机 / 目标规则 / 次数限制"的**唯一真源**，M2 不迁进数据文件；
一致性由加载期的**双向覆盖校验**补偿（票 09 §2 / §7 #9）。

M2 的三处结构变化：

- :class:`TargetRule` 增"目标数来源"枚举（额定 / 全体存活不含自己 / 全体存活含自己）；
  目标池的限制一律走技能侧的候选池修饰器，不给目标规则加面向具体牌名的布尔。
- :class:`CardBehavior` 增 ``final_zone``：结算完实体牌停在哪（默认 ``DISCARD``、
  装备 = ``EQUIP``、延时锦囊 = ``JUDGE``）。基本 53 张用默认值，故其行为**零改动**。
- 牌名扩到标准版全表 32 个；本票先把**静态面**（使用时机 / 目标规则 / 次数限制 /
  最终去向区）补齐，需询问或多步的**效果**由后续票落进 ``effects``（票 26 / 27 / 29 / 30）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from sgs.state import CardRef, FrameKind
from sgs.timing import Timing

__all__ = [
    "CARD_BEHAVIORS",
    "CallOp",
    "CardBehavior",
    "DamageNature",
    "EffectStep",
    "FinalZone",
    "PushFrame",
    "TargetCount",
    "TargetRule",
    "UseContext",
    "UseLimit",
    "UseTiming",
    "behavior_for",
    "opens_response_window",
]


class DamageNature(str, Enum):
    """伤害的**属性**（官方六要素之一）；M1 恒 ``NORMAL``（无属性伤害 / 无连环）。"""

    NORMAL = "NORMAL"
    FIRE = "FIRE"
    THUNDER = "THUNDER"


class UseContext(str, Enum):
    """一张牌可以在哪种语境下被使用（票 07 §2 的 ``use_timing``）。"""

    PLAY_PHASE = "PLAY_PHASE"
    NEAR_DEATH = "NEAR_DEATH"
    RESPONSE = "RESPONSE"


class TargetCount(str, Enum):
    """目标数的**来源**（票 09 §5）；只有三档，不允许出现第四档"自定义"。

    - ``RATED``：额定数值（读 ``rated_targets``），如【杀】1、【顺手牵羊】1；
    - ``ALL_ALIVE``：全体存活角色**不含**使用者，如【南蛮入侵】【万箭齐发】；
    - ``ALL_ALIVE_WITH_SELF``：全体存活角色**含**使用者，如【桃园结义】【五谷丰登】。
    """

    RATED = "RATED"
    ALL_ALIVE = "ALL_ALIVE"
    ALL_ALIVE_WITH_SELF = "ALL_ALIVE_WITH_SELF"


class FinalZone(str, Enum):
    """结算完实体牌的**最终去向区**（票 09 §6）。

    默认 ``DISCARD``；装备牌 = ``EQUIP``（结算完留装备区）；延时锦囊 = ``JUDGE``
    （置入目标的判定区）。取显式字段而非"按类别硬编码"：延时锦囊属 ``trick``
    却要进判定区，硬编码立刻破功。
    """

    DISCARD = "DISCARD"
    EQUIP = "EQUIP"
    JUDGE = "JUDGE"


@dataclass(frozen=True, slots=True)
class UseTiming:
    """一种可用语境；``RESPONSE`` 时带响应窗口的时机 id（【闪】= 生效前）。"""

    context: UseContext
    timing: Timing | None = None


@dataclass(frozen=True, slots=True)
class TargetRule:
    """目标数来源与目标范围（票 07 §4 / 票 09 §5 的判据表）。

    ``self_only`` = 只能指定自己（出牌阶段【桃】、装备牌、延时锦囊【闪电】）；
    ``requires_wounded_self`` = 指定自己的前提是**未满体力**（4.13"满体力时不能回复"）。
    濒死语境下【桃】的目标是濒死者而非自己，由求桃询问直接给出，不走本规则（票 21）。

    ``includes_self`` 只在 ``count is RATED`` 时读；两个 ``ALL_ALIVE*`` 档的
    含不含自己由枚举本身表达，避免同一条事实写两处。
    """

    rated_targets: int = 0
    count: TargetCount = TargetCount.RATED
    includes_self: bool = False
    needs_distance: bool = False
    self_only: bool = False
    requires_wounded_self: bool = False

    def includes_self_in_pool(self) -> bool:
        """候选池是否含使用者自己（由目标数来源 + ``includes_self`` 派生）。"""
        if self.count is TargetCount.ALL_ALIVE:
            return False
        if self.count is TargetCount.ALL_ALIVE_WITH_SELF:
            return True
        return self.includes_self

    def target_count(self, pool_size: int) -> int:
        """本牌这一问要指定几个目标；``ALL_ALIVE*`` 档取整个候选池。"""
        if self.count is TargetCount.RATED:
            return self.rated_targets
        return pool_size


@dataclass(frozen=True, slots=True)
class UseLimit:
    """额定 / 额外两个上限；``rated=None`` 表示不限次数（票 07 §6）。"""

    rated: int | None
    extra: int = 0
    counts: bool = True
    ignore_distance: bool = False


@dataclass(frozen=True, slots=True)
class PushFrame:
    """压出一个带时机的子帧；``scalars`` 是标量字面量（如 ``amount=1``）。"""

    kind: FrameKind
    scalars: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class CallOp:
    """直接调一个叶子原语；``op`` 必须是 :data:`sgs.effects.OPS` 注册表里的函数名。"""

    op: str
    scalars: tuple[tuple[str, int], ...] = ()


EffectStep: TypeAlias = PushFrame | CallOp


@dataclass(frozen=True, slots=True)
class CardBehavior:
    """一张牌名的完整行为；字段对齐 `开发文档/03` §2、由票 07 §2 冻结。"""

    card_type_id: str
    use_timings: tuple[UseTiming, ...]
    target_rule: TargetRule
    use_limit: UseLimit
    effects: tuple[EffectStep, ...]
    final_zone: FinalZone = FinalZone.DISCARD


_PLAY_PHASE = UseTiming(UseContext.PLAY_PHASE)
_NEAR_DEATH = UseTiming(UseContext.NEAR_DEATH)
_RESPONSE_BEFORE_EFFECT = UseTiming(UseContext.RESPONSE, Timing.RESOLVE_BEFORE_EFFECT)
_UNLIMITED = UseLimit(rated=None)


def _one_other(card_type_id: str, *, final_zone: FinalZone = FinalZone.DISCARD) -> CardBehavior:
    """额定一名他人（【过河拆桥】【顺手牵羊】【决斗】【借刀杀人】【乐不思蜀】）。"""
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(rated_targets=1),
        use_limit=_UNLIMITED,
        effects=(),
        final_zone=final_zone,
    )


def _self_only(card_type_id: str, *, final_zone: FinalZone = FinalZone.DISCARD) -> CardBehavior:
    """只能指定自己（【无中生有】、装备牌、【闪电】）。"""
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(rated_targets=1, self_only=True),
        use_limit=_UNLIMITED,
        effects=(),
        final_zone=final_zone,
    )


def _all_alive(card_type_id: str, *, with_self: bool) -> CardBehavior:
    """全体存活（【南蛮入侵】【万箭齐发】不含自己；【桃园结义】【五谷丰登】含自己）。"""
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(
            count=TargetCount.ALL_ALIVE_WITH_SELF if with_self else TargetCount.ALL_ALIVE
        ),
        use_limit=_UNLIMITED,
        effects=(),
    )


def _response(card_type_id: str) -> CardBehavior:
    """在别人的结算里响应的牌（【闪】/【无懈可击】）：无座位目标、效果为空。

    两者差别在响应窗口的**开法与归属**（【闪】对一次【杀】、无懈对"某张锦囊对某个目标"），
    由响应链落地（票 11 / 票 28）；静态面此刻完全同形。
    """
    return CardBehavior(
        card_type_id=card_type_id,
        use_timings=(_RESPONSE_BEFORE_EFFECT,),
        target_rule=TargetRule(rated_targets=0),
        use_limit=_UNLIMITED,
        effects=(),
    )


CARD_BEHAVIORS: dict[str, CardBehavior] = {
    # ---- 基本牌（M1 三条，字段一字不改）----
    "slash": CardBehavior(
        card_type_id="slash",
        use_timings=(_PLAY_PHASE,),
        target_rule=TargetRule(rated_targets=1, includes_self=False, needs_distance=True),
        use_limit=UseLimit(rated=1, extra=0, counts=True, ignore_distance=False),
        effects=(PushFrame(FrameKind.DAMAGE, (("amount", 1),)),),
    ),
    "peach": CardBehavior(
        card_type_id="peach",
        use_timings=(_PLAY_PHASE, _NEAR_DEATH),
        target_rule=TargetRule(
            rated_targets=1, includes_self=True, self_only=True, requires_wounded_self=True
        ),
        use_limit=UseLimit(rated=None),
        effects=(PushFrame(FrameKind.RECOVER, (("amount", 1),)),),
    ),
    "dodge": _response("dodge"),
    # ---- 普通锦囊（效果与多目标结算由票 27 落地）----
    "dismantlement": _one_other("dismantlement"),
    "snatch": _one_other("snatch"),
    "ex_nihilo": _self_only("ex_nihilo"),
    "duel": _one_other("duel"),
    "borrowed_sword": _one_other("borrowed_sword"),
    "barbarian_invasion": _all_alive("barbarian_invasion", with_self=False),
    "arrow_barrage": _all_alive("arrow_barrage", with_self=False),
    "peach_garden": _all_alive("peach_garden", with_self=True),
    "harvest": _all_alive("harvest", with_self=True),
    "nullify": _response("nullify"),
    # ---- 延时锦囊（判定骨架与后果由票 26 落地）----
    "indulgence": _one_other("indulgence", final_zone=FinalZone.JUDGE),
    "lightning": _self_only("lightning", final_zone=FinalZone.JUDGE),
    # ---- 装备（使用面 = 置入装备区；效果由票 29 / 票 30 落地）----
    "crossbow": _self_only("crossbow", final_zone=FinalZone.EQUIP),
    "qinggang_sword": _self_only("qinggang_sword", final_zone=FinalZone.EQUIP),
    "double_sword": _self_only("double_sword", final_zone=FinalZone.EQUIP),
    "green_dragon_blade": _self_only("green_dragon_blade", final_zone=FinalZone.EQUIP),
    "serpent_spear": _self_only("serpent_spear", final_zone=FinalZone.EQUIP),
    "axe": _self_only("axe", final_zone=FinalZone.EQUIP),
    "halberd": _self_only("halberd", final_zone=FinalZone.EQUIP),
    "qilin_bow": _self_only("qilin_bow", final_zone=FinalZone.EQUIP),
    "ice_sword": _self_only("ice_sword", final_zone=FinalZone.EQUIP),
    "eight_diagrams": _self_only("eight_diagrams", final_zone=FinalZone.EQUIP),
    "renwang_shield": _self_only("renwang_shield", final_zone=FinalZone.EQUIP),
    "chitu": _self_only("chitu", final_zone=FinalZone.EQUIP),
    "jueying": _self_only("jueying", final_zone=FinalZone.EQUIP),
    "dilu": _self_only("dilu", final_zone=FinalZone.EQUIP),
    "zhuahuang_feidian": _self_only("zhuahuang_feidian", final_zone=FinalZone.EQUIP),
    "dawan": _self_only("dawan", final_zone=FinalZone.EQUIP),
    "zixing": _self_only("zixing", final_zone=FinalZone.EQUIP),
}
"""标准版全表 32 个牌名的行为；装备牌的**技能**在 ``sgs.skills`` 注册（键 = ``equip.<id>``）。"""


def behavior_for(card: CardRef) -> CardBehavior:
    """按牌名取行为；未知牌名 fail-closed（不静默套用默认行为）。"""
    try:
        return CARD_BEHAVIORS[card.type]
    except KeyError as error:
        raise KeyError(f"牌 {card.name}（{card.type}）没有行为表条目") from error


def opens_response_window(behavior: CardBehavior) -> bool:
    """这张牌在自己的"生效前"是否给目标开响应窗口。

    M1 的判据 = 它的效果会压入伤害帧：官方牌面"以你为目标的【杀】生效前"的 M1 化。
    M1 唯一造成伤害的牌就是【杀】，故本判据与官方逐字对应；M2 出现其他可被抵消的牌
    （无懈可击、防具的响应）时，改为行为表上的显式字段，不改这里的调用点。
    """
    return any(
        isinstance(step, PushFrame) and step.kind is FrameKind.DAMAGE for step in behavior.effects
    )
