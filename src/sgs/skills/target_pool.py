r"""目标池真过滤（C 族）的武将技能定义（票 06）。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` 同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，定义与回调
住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**（票 12 §要做什么 1）。

C 族的形态差别只有一处：它**不产候选**，而是给 :data:`~sgs.effects.ModifierPoint.TARGET_POOL`
挂一条**删目标**的修饰器——锁定技，无条件生效、不询问。两条技能的逐字语义（票 06 §表）：

===========  ======  ==========================================
技能         锁定    出池条件
===========  ======  ==========================================
诸葛亮【空城】 锁定    自己**没有手牌** 且 牌名 ∈ {【杀】,【决斗】}
陆逊【谦逊】   锁定    牌名 ∈ {【顺手牵羊】,【乐不思蜀】}（与手牌无关）
===========  ======  ==========================================

**谓词内联在回调里**（与 H 族同一姿态）：牌名读 ``ctx.card.type``，手牌读
``state.player(ctx.owner).hand``。回调只在"自己确实在池里"时才动手，故它对别人的目标池
天然是惰性的。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, Modifier, ModifierContext, ModifierPoint
from sgs.state import GameState

from .catalog import SkillDef

__all__ = ["TARGET_POOL_SKILLS", "target_pool_skill_definitions"]


@dataclass(frozen=True, slots=True)
class TargetPoolSkill:
    """C 族一条技能的**声明**：出池的牌名闭集 + 是否要求自己无手牌。"""

    hero_id: str
    short_id: str
    card_types: tuple[str, ...]
    requires_empty_hand: bool = False

    @property
    def skill_id(self) -> str:
        """全限定技能 id（与数据侧共用 :func:`sgs.data.hero_skill_id` 这一处派生）。"""
        return hero_skill_id(self.hero_id, self.short_id)

    def remove_self(self, state: GameState, ctx: ModifierContext, current: Any) -> Any:
        """``TARGET_POOL`` 回调：条件满足时把**自己**从池里去掉，否则原样返回。"""
        if ctx.card is None or ctx.card.type not in self.card_types:
            return current
        if ctx.owner not in current:
            return current
        if self.requires_empty_hand and state.player(ctx.owner).hand:
            return current
        return tuple(target for target in current if target != ctx.owner)


TARGET_POOL_SKILLS: tuple[TargetPoolSkill, ...] = (
    # 诸葛亮【空城】：锁定技，当你没有手牌时，你不能成为【杀】或【决斗】的目标（规则 995）。
    TargetPoolSkill(
        hero_id="zhugeliang",
        short_id="kongcheng",
        card_types=("slash", "duel"),
        requires_empty_hand=True,
    ),
    # 陆逊【谦逊】：锁定技，你不能成为【顺手牵羊】或【乐不思蜀】的目标（规则 1028）。
    TargetPoolSkill(
        hero_id="luxun",
        short_id="qianxun",
        card_types=("snatch", "indulgence"),
    ),
)
"""C 族两条技能的逐字声明（票 06 §要做什么 3 的表）。"""


def _modifier(skill: TargetPoolSkill) -> Modifier:
    """一条 C 族技能的 ``TARGET_POOL`` 修饰器（回调 = 该声明的 :meth:`remove_self`）。"""
    return Modifier(ModifierPoint.TARGET_POOL, skill.remove_self)


def target_pool_skill_definitions() -> tuple[SkillDef, ...]:
    """C 族两条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    两条都是**锁定技**（技能面没有"可以"，是"不能成为目标"的持续性禁令），故
    ``locked=True``；``carrier = HERO`` ⇒ 优先级档由载体派生；``skill_type = NORMAL``。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(_modifier(skill),),
        )
        for skill in TARGET_POOL_SKILLS
    )
