r"""P 族：限制豁免（M6 票 02）——【咆哮】无次数限制 / 【奇才】无距离限制。

规则原文：§九 L979 / L998，§十七 L1640–1695。两条都是**锁定技**（牌面没有"你可以"），
故它们没有钩子、没有"是否发动"询问，只往修饰器链上挂一条回调：

=================  ==============  ============================================
技能               挂点            语义
=================  ==============  ============================================
张飞【咆哮】        ``COUNT_LIMIT`` 你于出牌阶段内使用【杀】无次数限制
黄月英【奇才】      ``DISTANCE_LIMIT`` 你使用锦囊牌无距离限制
=================  ==============  ============================================

两处判据都读**牌面事实**而不是牌名白名单：

- 【咆哮】用 :func:`sgs.effects.is_slash`（【杀】家族：普通 / 火 / 雷 / 转化 / 虚拟），
  与【诸葛连弩】的 ``_unlimited_slash`` 同一判据——「泛指角色能力的无次数限制」
  （§十七 17.2 ④）不按具体牌名分档；
- 【奇才】读数据文件的**牌名大类** :func:`sgs.data.card_category`：`trick` 才豁免距离。
  "无距离限制"是**条件豁免**（§十七 17.3）：完全免除"距离 ≤ 上限"这一条，而不是把距离
  改成某个数——故回调返回 ``None``，由 ``check_targets`` 跳过距离比较；【杀】的攻击范围
  是另一条判据（``needs_distance``），不经过本挂点，因此【奇才】不会顺带放宽【杀】。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    behavior_for,
    is_slash,
)
from sgs.state import GameState

from .catalog import SkillDef

__all__ = [
    "PAOXIAO",
    "QICAI",
    "limits_skill_definitions",
]

PAOXIAO = hero_skill_id("zhangfei", "paoxiao")
"""张飞【咆哮】的全限定技能 id。"""

QICAI = hero_skill_id("huangyueying", "qicai")
"""黄月英【奇才】的全限定技能 id。"""


def _paoxiao_count_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``COUNT_LIMIT`` 回调：**这个座位使用【杀】不设上限**（``None`` = 无限）。

    ``ctx.owner == ctx.subject`` 是"这条技能属于使用者本人"的自校（修饰器链遍历的是
    **全体存活实例**，缺这一条会替别人解除次数上限）；牌面判据走 :func:`is_slash`，
    火 / 雷 / 转化 / 虚拟【杀】同族。``state`` 只作链式回调的统一签名，不用。
    """
    del state
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    return None


def _qicai_distance_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``DISTANCE_LIMIT`` 回调：**锦囊牌的距离上限不存在**（``None`` = 豁免距离条件）。

    判据 = 数据文件里的牌名大类为 ``trick``（延时 / 非延时同档：两者都写在同一张大类里，
    【兵粮寸断】这类"距离 1 以内"的延时锦囊因此自动覆盖，不需要第二份名单）。
    ``card_category`` 查不到牌名（虚拟牌漏配等）⇒ ``None`` ⇒ 视为非锦囊、原样返回
    ``current``（fail-closed：不豁免）。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if card_category(state.pack_id, ctx.card.type) is not Category.TRICK:
        return current
    return None


def limits_skill_definitions() -> tuple[SkillDef, ...]:
    """P 族两条锁定技的 :class:`~sgs.skills.catalog.SkillDef`。

    两条都 ``locked=True``（牌面写"锁定技"）⇒ 不进"是否发动"候选、没有钩子；
    ``carrier = HERO``（优先级档由载体派生）、``skill_type = NORMAL``。
    """
    return (
        SkillDef(
            skill_id=PAOXIAO,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.COUNT_LIMIT, _paoxiao_count_limit),),
            requirement="你于出牌阶段内使用【杀】无次数限制",
        ),
        SkillDef(
            skill_id=QICAI,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE_LIMIT, _qicai_distance_limit),),
            requirement="你使用锦囊牌无距离限制",
        ),
    )
