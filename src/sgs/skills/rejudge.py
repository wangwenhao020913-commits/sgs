r"""I 族：改判与判定牌（票 11）。

两件技能都挂在判定帧的 L14 时点上，但落点不同——**能力面 vs 生效后**：

=======================  ==========  ==========================================================
技能                      锁定        落点
=======================  ==========  ==========================================================
司马懿【鬼才】            非锁定      ``SkillDef.rejudge_capable``（判定帧的**改判循环窗口**把它认出来）
郭嘉【天妒】              非锁定      ``Hook(JUDGE_CARD_AFTER_EFFECT)``：获得此判定牌
=======================  ==========  ==========================================================

**改判窗口是引擎侧的通用循环**（票 11 §②）：``JUDGE_CARD_BEFORE_EFFECT`` 上，引擎用
``RESPOND`` + ``WindowCause.REJUDGE``（``required=None``、``mode=PLAY``）逐个问有改判
能力的座位；选中 ``PlayCard`` ⇒ 引擎按"**代替**"搬牌（新牌 → 处理区、原判定牌 → 弃牌堆），
窗口重开。故本模块的【鬼才】**没有钩子回调**——它只给判定帧留下"我有这个能力"这一个
定义字段（:attr:`~sgs.skills.catalog.SkillDef.rejudge_capable`），引擎侧因此不出现任何
具体技能 id（源码纪律 §5）。

【天妒】（规则 957）：``JUDGE_CARD_AFTER_EFFECT`` 上非锁定技，先问"是否发动"，答发动
则把**最终生效**的那张判定牌从处理区移入自己手牌（``PUT``）。已被获得 ⇒ 判定结算收尾
不再把它置入弃牌堆（规则 11.2"若未被【天妒】等获得"，判据是"牌还在不在处理区"）。
"当**你的**判定牌生效后"由发动形态的 ``available`` 表达——条件 / 形态在派发器写
``skill_owner`` 之前求值，故比的是 ``frame.data["subject"]`` 与 ``instance.owner``。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind
from sgs.resolution import Control, Frame
from sgs.state import GameState, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import ActivationPlan, Hook, SkillDef

__all__ = [
    "GUICAI",
    "TIANDU",
    "rejudge_activation_plans",
    "rejudge_skill_definitions",
    "tiandu_handler",
]

GUICAI = hero_skill_id("simayi", "guicai")
"""司马懿【鬼才】的全限定技能 id（前缀派生走 :func:`sgs.data.hero_skill_id`）。"""

TIANDU = hero_skill_id("guojia", "tiandu")
"""郭嘉【天妒】的全限定技能 id。"""


def _tiandu_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """【天妒】的发动前提：**被判定者就是技能持有者**（"当**你的**判定牌生效后"）。

    ``available=False`` ⇒ 该实例不进候选、连"是否发动"都不问（票 19 §6 的姿态）。
    读 ``frame.data["subject"]``（判定帧的被判定者）而不是 ``skill_owner``，因为形态在
    派发器写 ``skill_owner`` **之前**求值（``_matching_hooks`` 先于那行）。
    """
    del state
    return ActivationPlan(available=frame.data.get("subject") == instance.owner)


def rejudge_activation_plans() -> dict[str, Any]:
    """I 族的发动形态注册表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。"""
    return {TIANDU: _tiandu_plan}


def tiandu_handler(host: Any, frame: Frame) -> Control:
    """【天妒】的 ``JUDGE_CARD_AFTER_EFFECT`` 钩子：是否发动已由派发器问过；获得此判定牌。

    "牌还在处理区"既是**触发前提**也是**重入幂等**的守卫：被移走后再重入本回调不会
    重复搬牌；判定收尾据同一事实决定要不要入弃牌堆（规则 11.2）。
    """
    owner = frame.data["skill_owner"]
    card = frame.data.get("judge_card")
    if card is None or card.card_id not in host.state.processing:
        return None
    return host.move_in_frame(
        frame,
        (card,),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )


def rejudge_skill_definitions() -> tuple[SkillDef, ...]:
    """I 族两条技能的 :class:`~sgs.skills.catalog.SkillDef`。

    【鬼才】非锁定（技能面写了"你可以"）但**不落钩子**——它把能力写在定义字段上，
    由引擎的通用改判窗口消费；【天妒】非锁定，落 ``JUDGE_CARD_AFTER_EFFECT`` 钩子。
    两条 ``carrier = HERO``（优先级档由载体派生）、``skill_type = NORMAL``。
    """
    return (
        SkillDef(
            skill_id=GUICAI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            rejudge_capable=True,
        ),
        SkillDef(
            skill_id=TIANDU,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.JUDGE_CARD_AFTER_EFFECT, tiandu_handler),),
        ),
    )
