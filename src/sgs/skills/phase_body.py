r"""L 族：阶段体触发与数值（票 12 登记；机制由票 10 的通用阶段体派发落地）。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` 等同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，
定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

L 族的形态差别只有一处：它挂在**阶段体**（``PHASE_DRAW`` / ``PHASE_FINISH``）上，
而阶段体没有结算帧——票 10 的 ``Engine._dispatch_phase_body`` 已经在"当前局面存在注册在
该时机上的实例"时惰性建一个不压栈的持久帧，故本票**只需登记**（票 10 §读法 4 明写
【英姿】【闭月】因此是"照表登记"、不新增机制）：

===========  ======  ================  ==========================
技能           锁定    阶段               效果
===========  ======  ================  ==========================
周瑜【英姿】    非锁定  ``PHASE_DRAW``    摸牌阶段多摸一张（规则 1018）
貂蝉【闭月】    非锁定  ``PHASE_FINISH``  结束阶段摸一张（规则 1054）
===========  ======  ================  ==========================

两条都是**非锁定技**（技能面写了"你可以"）⇒ 先发"是否发动"询问，答 ``ActivateSkill``
才摸牌；"技能持有者就是当前回合角色"这一前提由 :func:`phase_body_activation_plans` 的
``available`` 表达（阶段体帧的 ``source`` 就是当前回合角色），与【观星】同一姿态：
``available=False`` ⇒ 别人的阶段里该实例连"是否发动"都不问。

摸牌走 :meth:`~sgs.engine.Engine.draw_for_hand`（沿用 ``shuffle`` 子流、短摸判平局），
与摸牌阶段 / 奖惩摸牌同一口径；本族的钩子**不提问**，故不会让出控制权。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.data import SkillType
from sgs.effects import CarrierKind
from sgs.resolution import Control, Frame
from sgs.state import GameState
from sgs.timing import Timing

from .catalog import ActivationPlan, HeroSkillDecl, Hook, SkillDef
from .flow import FlowAnswers, SkillFlow

__all__ = [
    "BIYUE_FLOW",
    "PHASE_BODY_SKILLS",
    "PhaseBodySkill",
    "YINGZI_FLOW",
    "phase_body_skill_definitions",
]


@dataclass(frozen=True, slots=True)
class PhaseBodySkill(HeroSkillDecl):
    """L 族一条技能的**声明**：阶段体时机 + 摸牌数 + 执行路径。

    ``flow``（W6-01）：零步技能的 ``SkillFlow``（``steps=()``）；旧回调
    :meth:`handler` 保留，由 flow 的 ``effect`` 原样转调。
    """

    timing: Timing
    draws: int
    flow: Any = None

    def handler(self, host: Any, frame: Frame) -> Control:
        """阶段体钩子：是否发动已由派发器问过；这里把 ``draws`` 张摸进持有者手里。"""
        host.draw_for_hand(frame, frame.data["skill_owner"], self.draws)
        return None


# W6-01：零步流水线。effect 是本模块的顶层函数（``FlowEffect``），只把旧回调
# ``PhaseBodySkill.handler`` 包一层；声明在下方，**调用期**才解析（import 期不读）。
def _yingzi_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Control:
    """【英姿】的零步流水线效果（W6-01）：转调旧回调 :meth:`PhaseBodySkill.handler`。

    ``steps=()`` ⇒ ``answers`` 恒为空；执行路径与迁移前的 ``Hook(handler=...)`` 逐字等价
    （是否发动仍由派发器先问一次）。
    """
    del answers
    return _YINGZI.handler(host, frame)


def _biyue_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Control:
    """【闭月】的零步流水线效果（W6-01）：同 :func:`_yingzi_effect`。"""
    del answers
    return _BIYUE.handler(host, frame)


YINGZI_FLOW = SkillFlow(name="yingzi", steps=(), effect=_yingzi_effect)
"""【英姿】的零步流水线（W6-01）：[是否发动] → 多摸一张牌（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的 flow（先例 :data:`~sgs.skills.damage_after.FANKUI_FLOW`）。
"""

BIYUE_FLOW = SkillFlow(name="biyue", steps=(), effect=_biyue_effect)
"""【闭月】的零步流水线（W6-01）：[是否发动] → 摸一张牌（无步骤）。"""


_YINGZI = PhaseBodySkill(
    # 周瑜【英姿】：摸牌阶段，你可以多摸一张牌（规则 1018）。
    hero_id="zhouyu",
    short_id="yingzi",
    timing=Timing.PHASE_DRAW,
    draws=1,
    flow=YINGZI_FLOW,
)
_BIYUE = PhaseBodySkill(
    # 貂蝉【闭月】：结束阶段（回合结束阶段）开始时，你可以摸一张牌（规则 1054）。
    hero_id="diaochan",
    short_id="biyue",
    timing=Timing.PHASE_FINISH,
    draws=1,
    flow=BIYUE_FLOW,
)

PHASE_BODY_SKILLS: tuple[PhaseBodySkill, ...] = (_YINGZI, _BIYUE)
"""L 族两条技能的逐字声明（票 12 §落地表第 17/27 行）。"""

_REQUIREMENTS: dict[str, str] = {
    "yingzi": "多摸一张牌",
    "biyue": "摸一张牌",
}
"""L 族两条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""


def phase_body_activation_plans() -> dict[str, Any]:
    """L 族的发动形态注册表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。

    两条技能的形态同形：**技能持有者就是当前回合角色**才 ``available``——阶段体帧的
    ``source`` 由 :meth:`Engine._dispatch_phase_body` 设成当前回合角色。别的人摸牌 /
    结束阶段里，这条实例连"是否发动"都不问（与【观星】的 ``_guanxing_plan`` 同一条理由）。
    """

    def plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
        del state
        return ActivationPlan(available=frame.data.get("source") == instance.owner)

    return {skill.skill_id: plan for skill in PHASE_BODY_SKILLS}


def phase_body_skill_definitions() -> tuple[SkillDef, ...]:
    """L 族两条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    两条都**非锁定**（技能面写"你可以"）⇒ 先问"是否发动"再摸牌；``carrier = HERO``
    ⇒ 优先级档由载体派生；``skill_type = NORMAL``（都不是主公技）。
    挂点是**阶段体**（``Hook(timing)``），消费方是票 10 的 ``_dispatch_phase_body``。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(skill.timing, flow=skill.flow),),
            requirement=_REQUIREMENTS[skill.short_id],
        )
        for skill in PHASE_BODY_SKILLS
    )
