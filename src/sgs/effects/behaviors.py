"""牌行为表：声明式的"用这张牌会发生什么"（票 07 §2）。

步骤只有两类（票 07 §1 的两层切分）：

- :class:`PushFrame` —— 压出一个带时机的结算帧（子帧按约定继承使用帧的
  ``source`` / ``targets`` / ``card``，``scalars`` 只放标量字面量）；
- :class:`CallOp` —— 直接调一个叶子原语。

**不建** conditions、不建取值表达式：M1 的三条映射都是无条件的（票 07 §2）。
字面量表住本模块；M3 把同一 schema 的加载源换成数据文件时引擎主干不动。

M1 的三条映射：杀 ``Push(DAMAGE, amount=1)``、桃 ``Push(RECOVER, amount=1)``、
闪 ``effects = ()``（抵消是被响应帧的标志，不是闪自己的效果——票 07 §3）。
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
    "PushFrame",
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


@dataclass(frozen=True, slots=True)
class UseTiming:
    """一种可用语境；``RESPONSE`` 时带响应窗口的时机 id（【闪】= 生效前）。"""

    context: UseContext
    timing: Timing | None = None


@dataclass(frozen=True, slots=True)
class TargetRule:
    """额定目标数与目标范围（票 07 §4 的判据表）。

    ``self_only`` = 只能指定自己（出牌阶段【桃】）；``requires_wounded_self`` =
    指定自己的前提是**未满体力**（4.13"满体力时不能回复"）。濒死语境下【桃】的目标
    是濒死者而非自己，由求桃询问直接给出，不走本规则（票 21）。
    """

    rated_targets: int
    includes_self: bool = False
    needs_distance: bool = False
    self_only: bool = False
    requires_wounded_self: bool = False


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
    """直接调一个叶子原语；M1 不构造（无时机效果的牌留 M2）。"""

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


CARD_BEHAVIORS: dict[str, CardBehavior] = {
    "slash": CardBehavior(
        card_type_id="slash",
        use_timings=(UseTiming(UseContext.PLAY_PHASE),),
        target_rule=TargetRule(rated_targets=1, includes_self=False, needs_distance=True),
        use_limit=UseLimit(rated=1, extra=0, counts=True, ignore_distance=False),
        effects=(PushFrame(FrameKind.DAMAGE, (("amount", 1),)),),
    ),
    "peach": CardBehavior(
        card_type_id="peach",
        use_timings=(UseTiming(UseContext.PLAY_PHASE), UseTiming(UseContext.NEAR_DEATH)),
        target_rule=TargetRule(
            rated_targets=1, includes_self=True, self_only=True, requires_wounded_self=True
        ),
        use_limit=UseLimit(rated=None),
        effects=(PushFrame(FrameKind.RECOVER, (("amount", 1),)),),
    ),
    "dodge": CardBehavior(
        card_type_id="dodge",
        use_timings=(UseTiming(UseContext.RESPONSE, Timing.RESOLVE_BEFORE_EFFECT),),
        target_rule=TargetRule(rated_targets=0),
        use_limit=UseLimit(rated=None),
        effects=(),
    ),
}
"""M1 的三条映射；M3 换成数据源时本表就是加载结果。"""


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
