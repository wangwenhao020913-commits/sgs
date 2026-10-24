r"""P 族：限制豁免与【咆哮】②（M6 票 02 / 票 05）——【咆哮】/【奇才】/ 界张飞【咆哮】。

规则原文：§九 L979 / L998，§十七 L1640–1695；界张飞的 §十九 L2075–L2081。
三条都是**锁定技**（牌面没有"你可以"），故它们没有"是否发动"询问：

=====================  ========================  ============================
技能                   挂点                      语义
=====================  ========================  ============================
张飞【咆哮】           ``COUNT_LIMIT``           你于出牌阶段内使用【杀】无次数限制
界张飞【咆哮】①        ``COUNT_LIMIT``           同上（共用同一条回调）
界张飞【咆哮】②        ``SLASH_CANCELED``（接缝） 自己被【闪】抵消后挂上"下一次 +1"
                       ``DAMAGE_DEAL_AMOUNT``    本回合第一个【杀】伤害 +1
                       ``DAMAGE_AFTER_TAKE``     造成伤害之后消费该标记
黄月英【奇才】          ``DISTANCE_LIMIT``        你使用锦囊牌无距离限制
=====================  ========================  ============================

两处判据都读**牌面事实**而不是牌名白名单：

- 【咆哮】用 :func:`sgs.effects.is_slash`（【杀】家族：普通 / 火 / 雷 / 转化 / 虚拟），
  与【诸葛连弩】的 ``_unlimited_slash`` 同一判据——「泛指角色能力的无次数限制」
  （§十七 17.2 ④）不按具体牌名分档；
- 【奇才】读数据文件的**牌名大类** :func:`sgs.data.card_category`：`trick` 才豁免距离。
  "无距离限制"是**条件豁免**（§十七 17.3）：完全免除"距离 ≤ 上限"这一条，而不是把距离
  改成某个数——故回调返回 ``None``，由 ``check_targets`` 跳过距离比较；【杀】的攻击范围
  是另一条判据（``needs_distance``），不经过本挂点，因此【奇才】不会顺带放宽【杀】。

**界张飞【咆哮】②**（M6 票 05）的"下一次"语义在这里钉死（`06`（§5「程序化约定与偏离一手」）
的账本行由票 21 写回）：

- **触发**：自己使用的【杀】被【闪】抵消（``SeamKind.SLASH_CANCELED`` 只在"被抵消的
  【杀】"上开，抵消由【闪】的响应窗口判定，engine / 本模块都不认"闪"这个牌名）；
  挂的是**座位级**的一次性状态（``Player.paoxiao_bonus``，作用域 = 本回合），
  不随技能实例存亡撤回（D9 的一次性效果）；
- **消费**：本回合**第一个**由自己使用的【杀】造成的伤害事件，"造成伤害时"而不是
  "使用【杀】时"——被抵消 / 未造成伤害的那张【杀】**不消费**（与【酒】"使用即消费"
  相反）；非【杀】伤害不消费；多目标【杀】只有**第一个目标**的本次伤害 +1；
- **过期**：回合末与下一回合起点各清一次（与【酒】/【裸衣】同一收尾点）。
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
from sgs.resolution import Frame
from sgs.state import GameState
from sgs.timing import Timing

from .catalog import Hook, Seam, SeamKind, SkillDef

__all__ = [
    "JIE_PAOXIAO",
    "PAOXIAO",
    "PAOXIAO_BONUS",
    "QICAI",
    "limits_skill_definitions",
]

PAOXIAO = hero_skill_id("zhangfei", "paoxiao")
"""张飞【咆哮】的全限定技能 id。"""

JIE_PAOXIAO = hero_skill_id("jie_zhangfei", "paoxiao")
"""界张飞【咆哮】的全限定技能 id（§十九 L2075–L2078：①无次数限制 + ②被抵消后的 +1）。"""

QICAI = hero_skill_id("huangyueying", "qicai")
"""黄月英【奇才】的全限定技能 id。"""

PAOXIAO_BONUS = 1
"""【咆哮】②的伤害增量（"此伤害 +1"）。"""


def _paoxiao_count_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``COUNT_LIMIT`` 回调：**这个座位使用【杀】不设上限**（``None`` = 无限）。

    ``ctx.owner == ctx.subject`` 是"这条技能属于使用者本人"的自校（修饰器链遍历的是
    **全体存活实例**，缺这一条会替别人解除次数上限）；牌面判据走 :func:`is_slash`，
    火 / 雷 / 转化 / 虚拟【杀】同族。``state`` 只作链式回调的统一签名，不用。
    标准张飞与界张飞共用同一条回调（①逐字相同），界版只是另一个武将 id。
    """
    del state
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    return None


def _paoxiao_mark_bonus(host: Any, frame: Frame) -> Any:
    """``SLASH_CANCELED`` 接缝：**自己的**【杀】被抵消 ⇒ 挂上"下一次伤害 +1"。

    接缝对所有存活实例派发，故先自校"被抵消的这张【杀】的使用者是本实例持有者"
    （``skill_owner`` 由派发环在回调前写入，是"谁在跑"的帧私有坐标）。写入唯一入口
    :meth:`~sgs.engine.Engine.set_paoxiao_bonus`；**不发 delta**（回放按同一条
    【闪】的应答重建），与【酒】/【裸衣】同姿态。
    """
    source = frame.data.get("source")
    if source is None or frame.data.get("skill_owner") != source:
        return None
    host.set_paoxiao_bonus(source, True)
    return None


def _paoxiao_bonus_damage(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``DAMAGE_DEAL_AMOUNT`` 回调：待消费标记在场且本次是**自己使用的【杀】** ⇒ +1。

    只读不写（"造成伤害时 +1"）：消费发生在这次伤害结算之后的
    :func:`_paoxiao_consume_bonus`，故同一张多目标【杀】只有第一个目标的本次伤害吃到 +1，
    而"被抵消 / 伤害被防止"的帧（``amount <= 0``）不会白消费掉它。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not state.player(ctx.owner).paoxiao_bonus:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    return current + PAOXIAO_BONUS


def _paoxiao_consume_bonus(host: Any, frame: Frame) -> Any:
    """``DAMAGE_AFTER_TAKE`` 钩子：这次伤害真的发生了 ⇒ 消费掉【咆哮】②的标记。

    进入本钩子时 ``amount`` 已是定稿值；伤害被防止 / 减为 0 时引擎不派发本时机
    （:func:`~sgs.engine.steps.damage_after_take` 的既有语义），故"没造成伤害"不会消费。
    判据与给加伤的回调同源（来源 = 持有者、渠道牌是【杀】族），不认具体牌名。
    """
    source = frame.data.get("source")
    if source is None or frame.data.get("skill_owner") != source:
        return None
    if frame.data.get("amount", 0) <= 0:
        return None
    card = frame.data.get("channel")
    if card is None or not is_slash(behavior_for(card)):
        return None
    host.set_paoxiao_bonus(source, False)
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
    """P 族三条锁定技的 :class:`~sgs.skills.catalog.SkillDef`。

    三条都 ``locked=True``（牌面写"锁定技"）⇒ 不进"是否发动"候选；``carrier = HERO``
    （优先级档由载体派生）、``skill_type = NORMAL``。界张飞【咆哮】①与标准版共用
    ``COUNT_LIMIT`` 回调，②另挂一条接缝 + 两条回调（加伤 / 消费）。
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
            skill_id=JIE_PAOXIAO,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.DAMAGE_AFTER_TAKE, handler=_paoxiao_consume_bonus),),
            seams=(Seam(SeamKind.SLASH_CANCELED, handler=_paoxiao_mark_bonus),),
            modifiers=(
                Modifier(ModifierPoint.COUNT_LIMIT, _paoxiao_count_limit),
                Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _paoxiao_bonus_damage),
            ),
            requirement=(
                "你使用【杀】无次数限制；当你使用的【杀】被【闪】抵消后，"
                "本回合下一次【杀】造成伤害时，此伤害 +1"
            ),
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
