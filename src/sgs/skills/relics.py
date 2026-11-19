r"""遗物技能（M7 票 26；票 02 §6 / §7）：六类各一条，载体 ``RELIC``、来源 ``RELIC``。

牌面（票 02 §6，逐字口径）::

    遗物 = 技能，住独立遗物栏；每场战斗开局自动生效；不是武将牌上的技能。
    v1 = 6 条，六类各一条：起始手牌 +1 / 每回合摸牌 +1 / 手牌上限 +2 /
    造成伤害 +1 / 桃回复 +1 / 距离 -1。

**id 方案**：``relic.<短 id>``。它**不在** ``hero_skill_id`` 的 ``hero.<武将>.<短 id>``
派生式上——遗物没有"默认拥有者"武将可派生；``CATALOG`` 只按 id 查表，故前缀只用来
说明"这是谁家的技能"。六条 id 与价值表 ``relics`` 段（票 15）逐条同名。

**形状**（`docs/agents/skill-authoring/shapes.md` §2，选中**一行**）：

| 技能 | 形状 | 挂点 / 模板 |
|---|---|---|
| 起始手牌 +1 | L0「游戏开始时」锁定技钩子（零询问） | ``GAME_START``；样板【天成】`hero.wuming.tiancheng` 的"帧外钩子 + 帧私有游标"那一半 |
| 每回合摸牌 +1 | 纯修饰器 | ``ModifierPoint.DRAW_COUNT``；样板 = 座位常驻项 `sgs.skills.catalog._seat_draw_count_bonus` 的同一条链 |
| 手牌上限 +2 | 纯修饰器（持续加式） | ``ModifierPoint.HAND_LIMIT_REPLACE``；样板【血裔】`hero.yuanshao_mobile.xueyi` |
| 造成伤害 +1 | 纯修饰器 | ``ModifierPoint.DAMAGE_DEAL_AMOUNT``；样板【古锭刀】`equip.ancient_scimitar` 的同一挂点 |
| 桃回复 +1 | 纯修饰器 | ``ModifierPoint.RECOVER_AMOUNT``；样板【救援】`hero.sunquan.jiuyuan` |
| 距离 −1 | 纯修饰器 | ``ModifierPoint.DISTANCE``；样板【马术】`hero.machao.mashu`（主体侧） |

**读法（刻意的选择，写在这里免得下一手重新推导）**：

1. 六条都**锁定**（``locked=True``）：牌面一个「可以」都没有 ⇒ 派发环不问"是否发动"，
   遗物就是"每场开局自动生效"的常驻效果。
2. **不是武将牌上的技能**：实例只从 ``Player.relics`` 解析（``SkillSourceKind.RELIC``），
   与武将面 / 装备区不相干；``_NEVER_SUPPRESSED_SOURCES`` 登记它**永不受技能失效影响**
   （票 09 已落地）。故界【铁骑】一类"非锁定技失效"打不到遗物。
3. **不是牌**：不造 ``card_id``、不发 delta、不进记录流；修饰器查询期现算。
4. 「桃回复 +1」认的是**遗物持有者使用的【桃】**（``ctx.subject == ctx.owner`` 且
   ``ctx.card.type == "peach"``），目标可以是自己、也可以是濒死的别人——牌面说的
   "桃回复"是这张【桃】的回复量，不是"你受到的回复"。
5. 「距离 −1」**双向**：遗物持有者算别人（主体侧）与别人算他（客体侧）都减 1。
   牌面只写"距离 −1"、没写"你与其他角色的距离"，而本票的验收要求进攻 / 防守两个方向
   都能观测到（`tests/test_relic_skills.py`），故按双向实现。
"""

from __future__ import annotations

from typing import Any

from sgs.effects import CarrierKind, Modifier, ModifierContext, ModifierPoint
from sgs.timing import Timing

from .catalog import Hook, SkillDef

__all__ = [
    "RELIC_DAMAGE_DEALT",
    "RELIC_DISTANCE",
    "RELIC_EXTRA_DRAW",
    "RELIC_EXTRA_START_HAND",
    "RELIC_HAND_LIMIT",
    "RELIC_PEACH_HEAL",
    "RELIC_SKILL_IDS",
    "relic_skill_definitions",
]

RELIC_EXTRA_START_HAND = "relic.extra_start_hand"
"""起始手牌 +1（游戏开始时摸一张牌）。"""

RELIC_EXTRA_DRAW = "relic.extra_draw"
"""每回合摸牌 +1（摸牌阶段的额定张数）。"""

RELIC_HAND_LIMIT = "relic.hand_limit"
"""手牌上限 +2。"""

RELIC_DAMAGE_DEALT = "relic.damage_dealt"
"""造成伤害 +1。"""

RELIC_PEACH_HEAL = "relic.peach_heal"
"""【桃】回复 +1。"""

RELIC_DISTANCE = "relic.distance"
"""距离 −1（双向）。"""

RELIC_SKILL_IDS: tuple[str, ...] = (
    RELIC_EXTRA_START_HAND,
    RELIC_EXTRA_DRAW,
    RELIC_HAND_LIMIT,
    RELIC_DAMAGE_DEALT,
    RELIC_PEACH_HEAL,
    RELIC_DISTANCE,
)
"""六条遗物的 id（出厂顺序 = 票 02 §6 的六类次序；价值表那段按 id 升序另排）。"""


_START_HAND_DRAWN = "relic.extra_start_hand:drawn"
"""帧私有守卫：这次 ``GAME_START`` 派发已经摸过那一张。

派发环在别的实例挂起后重入时会从头再跑一遍已完成的钩子（与【天成】/【圣盔】同一个
重入口径），没有它就会再摸一张。写在**摸牌之前**：``draw_for_hand`` 返回 ``"stay"``
时那张牌已经在飞，重入只该跳过、不该重摸。
"""


def _extra_start_hand(host: Any, frame: Any) -> Any:
    """``GAME_START`` 回调：给遗物持有者**多摸一张**（起始手牌 +1 的落点）。

    为什么不改「起始手牌张数」那一步：``build_initial`` 的 4 张是规则常量，而遗物是
    **座位级开局事实**——在 L0（起始手牌发完之后、第一回合之前）补一张，才是"每场战斗
    开局自动生效"的同一条时点。锁定技 ⇒ 不问"是否发动"，``frame.data["skill_owner"]``
    给持有者。短摸（两堆皆空）沿用 ``draw_for_hand`` 的既有平局口径（G10）。
    """
    if frame.data.get(_START_HAND_DRAWN):
        return None
    frame.data[_START_HAND_DRAWN] = True
    control = host.draw_for_hand(frame, int(frame.data["skill_owner"]), 1)
    if control == "stay":
        return "stay"
    return None


def _owner_is_subject(
    state: Any, ctx: ModifierContext, current: Any, *, step: int
) -> Any:
    """``ctx.owner == ctx.subject`` 时把 ``current`` 加 ``step``（其余座位原样返回）。

    六条修饰器里的五条共用这一句"这条遗物属于被查询的那个座位吗"自校——修饰器链遍历的
    是**全体存活实例**，缺这一条会替所有人加成（与【血裔】/【马术】同一姿态）。
    ``ctx`` 的 ``subject`` 在每条链上各有含义（摸牌的人 / 伤害来源 / 用【桃】的人 /
    主体），三条"主体侧"的遗物因此都读它。
    """
    del state
    if ctx.owner != ctx.subject:
        return current
    return current + step


def _draw_count_bonus(state: Any, ctx: ModifierContext, current: Any) -> Any:
    """``DRAW_COUNT``：持有者的每个摸牌阶段多摸一张（``ctx.subject`` = 摸牌的人）。"""
    return _owner_is_subject(state, ctx, current, step=1)


_HAND_LIMIT_BONUS = 2
"""手牌上限 +2（票 02 §6 的第三类）。"""


def _hand_limit_bonus(state: Any, ctx: ModifierContext, current: Any) -> Any:
    """``HAND_LIMIT_REPLACE``：持有者的手牌上限 +2（**持续加式**，查询期现算）。

    ``current`` 是链上此刻的值（缺省 = 当前体力值、下限 0；界【英姿】② 的替换式已在它
    之前跑完——遗物档排在武将档之后，见 ``PriorityClass.RELIC``）。加式而不是替换式，
    故"体力上限""一次性加式"两个轴都不受影响（`hand_limit` 在替换链之后再加一次性增量）。
    """
    return _owner_is_subject(state, ctx, current, step=_HAND_LIMIT_BONUS)


def _damage_dealt_bonus(state: Any, ctx: ModifierContext, current: Any) -> Any:
    """``DAMAGE_DEAL_AMOUNT``：持有者**造成**的伤害 +1（``ctx.subject`` = 来源）。

    ``damage_amount`` 只在 ``chain=False`` 的源头伤害帧上派发本挂点（传导帧的基数已含
    来源侧加成）——故本回调不需要自己判"这次是不是传导"，也不会重复加。
    """
    return _owner_is_subject(state, ctx, current, step=1)


_PEACH_CARD_TYPES = ("peach",)
"""回复量加成的牌名闭集（票 02 §6 的第五类只写「桃回复」，故只有【桃】）。"""


def _peach_heal_bonus(state: Any, ctx: ModifierContext, current: Any) -> Any:
    """``RECOVER_AMOUNT``：持有者使用的【桃】回复量 +1。

    判据两条内联：``ctx.subject == ctx.owner``（这张【桃】是遗物持有者用的；``ctx.subject``
    是回复的来源、``ctx.target`` 是被回复者）∧ ``ctx.card.type == "peach"``。目标是不是
    自己**不判**——牌面写的是"桃回复 +1"，即这张【桃】的回复量 +1；用它去救濒死的别人
    时同样多回 1 点。
    """
    if ctx.owner != ctx.subject:
        return current
    if ctx.card is None or ctx.card.type not in _PEACH_CARD_TYPES:
        return current
    return current + 1


def _distance_shortened(state: Any, ctx: ModifierContext, current: Any) -> Any:
    """``DISTANCE``：持有者作为**主体**（他算别人）或**客体**（别人算他）时距离 −1。

    双向是刻意的读法：牌面只写"距离 −1"，而票面验收要求进攻 / 防守两个方向都核
    （`tests/test_relic_skills.py`）。下限 1 由 :func:`~sgs.effects.distance` 在管道之后夹住。
    ``ctx`` 在 ``distance_bonus`` 的只读汇总里会把某一侧换成 :data:`~sgs.effects.DISTANCE_NO_TARGET`
    （``-1``，不是座位）——这里只做**相等比较**、不索引座位，故哨兵天然不命中。
    """
    del state
    if ctx.owner == ctx.subject or ctx.owner == ctx.target:
        return current - 1
    return current


def relic_skill_definitions() -> tuple[SkillDef, ...]:
    """六条遗物的 :class:`~sgs.skills.catalog.SkillDef`（票 02 §6 的六类各一条）。

    ``carrier = RELIC`` ⇒ 优先级档由载体派生（``PriorityClass.RELIC``，链尾），
    与 ``Player.relics`` 一起构成"遗物不是武将技能"的两半。``requirement`` 是给人看的
    一句话（提示条 / 描述），不是规则判据。
    """
    return (
        SkillDef(
            skill_id=RELIC_EXTRA_START_HAND,
            locked=True,
            carrier=CarrierKind.RELIC,
            hooks=(Hook(Timing.GAME_START, handler=_extra_start_hand),),
            requirement="起始手牌 +1（游戏开始时摸一张牌）",
        ),
        SkillDef(
            skill_id=RELIC_EXTRA_DRAW,
            locked=True,
            carrier=CarrierKind.RELIC,
            modifiers=(Modifier(ModifierPoint.DRAW_COUNT, _draw_count_bonus),),
            requirement="每回合摸牌阶段 +1",
        ),
        SkillDef(
            skill_id=RELIC_HAND_LIMIT,
            locked=True,
            carrier=CarrierKind.RELIC,
            modifiers=(Modifier(ModifierPoint.HAND_LIMIT_REPLACE, _hand_limit_bonus),),
            requirement="手牌上限 +2",
        ),
        SkillDef(
            skill_id=RELIC_DAMAGE_DEALT,
            locked=True,
            carrier=CarrierKind.RELIC,
            modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _damage_dealt_bonus),),
            requirement="造成伤害 +1",
        ),
        SkillDef(
            skill_id=RELIC_PEACH_HEAL,
            locked=True,
            carrier=CarrierKind.RELIC,
            modifiers=(Modifier(ModifierPoint.RECOVER_AMOUNT, _peach_heal_bonus),),
            requirement="使用【桃】回复的体力 +1",
        ),
        SkillDef(
            skill_id=RELIC_DISTANCE,
            locked=True,
            carrier=CarrierKind.RELIC,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _distance_shortened),),
            requirement="距离 -1",
        ),
    )
