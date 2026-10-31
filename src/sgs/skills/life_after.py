r"""**失去体力后**（M6 票 17 新建的族）：界黄盖【诈降】。

本模块与 :mod:`sgs.skills.damage_after` / :mod:`sgs.skills.move_after` 同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，
:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

**为什么单列一族**：触发时机是 ``LIFE_DECREASE_AFTER``（"失去 1 点体力后"），与"受到伤害后"
（``DAMAGE_AFTER_TAKE``）是**两条链**——【诈降】按牌面只认**失去体力**，包括【苦肉】这类
没有伤害来源、不过伤害修正链的扣减。挂在伤害后上会把"受到 1 点伤害"误当成"失去 1 点体力"
（虽然伤害确实伴随一次扣减，但【诈降】的触发次数与伤害链的逐点派发不是同一件事）。

§十九 L2148–L2151：

    诈降：锁定技，当你失去 1 点体力后，你摸三张牌，然后若此时是你的出牌阶段，你本阶段：
          使用红色【杀】无距离限制且不能被【闪】响应、使用【杀】的次数上限 +1。

三段落点：

- **摸三张**：``LIFE_DECREASE_AFTER`` 钩子（锁定技，不问），``draw_for_hand`` 与摸牌阶段
  同口径（短摸 ⇒ 平局）；
- **"本阶段"的作用域**：写座位级 ``Player.zhaxiang_active``（唯一写入口
  :meth:`~sgs.engine.Engine.set_zhaxiang_active`），**出牌阶段结束时清掉**（引擎在
  ``_phase_play_end`` 调用；``_phase_play_start`` 再兜一次）——不残留到下一个阶段 / 下一个
  回合的借刀杀人被迫【杀】；
- **三条连续型效果**（只在红色【杀】 / 【杀】上分支）：
  ① ``TARGET_POOL`` 豁免"距离 ≤ 攻击范围"（把全部其他存活座位并进池，与界【武圣】② 同一个
  挂点姿态）；② ``USE_AFTER_DESIGNATE_TARGET`` 钩子给本次使用打**全场档**
  ``Tag.UNRESPONSIVE``（窗口照常"该不该存在"由行为表定，标签只负责这一次不响应，与
  【铁骑】/界【离间】同一条读取面）；③ ``COUNT_LIMIT`` 给【杀】的次数上限 +1（"使用【杀】
  的次数上限 +1"不限定红色——牌面只在"红色【杀】"那一句里放了前两条）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Color, SkillType, hero_skill_id
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    behavior_for,
    is_slash,
)
from sgs.resolution import Tag, add_tag
from sgs.state import LIFE_DECREASE_DAMAGE_KEY, GameState, Phase
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    Hook,
    SkillDef,
)

__all__ = [
    "JIE_ZHAXIANG",
    "ZHAXIANG_DRAW",
    "life_after_skill_definitions",
]

JIE_ZHAXIANG = hero_skill_id("jie_huanggai", "zhaxiang")
"""界黄盖【诈降】的全限定技能 id（§十九 L2148–L2151）。"""

ZHAXIANG_DRAW = 3
"""【诈降】摸牌数（牌面："你摸三张牌"）。"""


def _owns(state: GameState, frame: Any) -> int | None:
    """本帧的技能持有者（``condition`` / ``handler`` 共读派发器写好的匹配键）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        owner = frame.data.get("skill_owner")
    if owner is None:
        return None
    seat = int(owner)
    return seat if state.player(seat).alive else None


def _zhaxiang_phase_active(state: GameState, owner: int) -> bool:
    """``owner`` 的【诈降】本阶段效果是否生效。

    只读座位字段：它在**自己的出牌阶段**由钩子置位、在**出牌阶段结束**由引擎清掉，故
    "本阶段"由写入 / 清理点表达，读取方不再比 ``turn_seat`` / ``phase``——否则一次
    "借刀杀人"（别的回合里被迫使用【杀】）会因为读到别人的 ``PLAY`` 阶段而错误生效。
    """
    return state.player(owner).zhaxiang_active


def _zhaxiang_after_losing_hp(host: Any, frame: Any) -> Any:
    """``LIFE_DECREASE_AFTER``：摸三张；若此刻是自己的出牌阶段，本阶段效果生效。"""
    owner = int(frame.data["skill_owner"])
    state = host.state
    if int(frame.data.get("target", -1)) != owner:
        return None
    host.draw_for_hand(frame, owner, ZHAXIANG_DRAW)
    if host.finished:
        return None
    if state.phase is Phase.PLAY and state.turn_seat == owner:
        host.set_zhaxiang_active(owner, True)
    return None


def _zhaxiang_condition(host: Any, frame: Any) -> bool:
    """触发条件：**失去体力**的是持有者、且这次失去至少 1 点。

    **受到伤害不算「失去体力」**（实机报障 2026-10-31）：伤害链照常压 ``LIFE_DECREASE``
    子帧并派发 L8 的三个时机，但那一帧带 :data:`~sgs.state.LIFE_DECREASE_DAMAGE_KEY`。
    本条件因此不能只看时机——不然"被打 1 点"也会摸三张（用户实机原样）。
    """
    owner = _owns(host.state, frame)
    if owner is None:
        return False
    if frame.data.get(LIFE_DECREASE_DAMAGE_KEY):
        return False
    return int(frame.data.get("target", -1)) == owner and int(
        frame.data.get("amount", 0)
    ) >= 1


def _zhaxiang_red_slash(state: GameState, card: Any) -> bool:
    """本次使用的牌是不是**红色【杀】**（族判据 + 花色派生的颜色，转化牌继承来源颜色）。"""
    if card is None or not is_slash(behavior_for(card)):
        return False
    color = card.color
    return color is Color.RED


def _zhaxiang_no_distance(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """``TARGET_POOL``：本阶段你使用的红色【杀】**无距离限制**（与界【武圣】② 同姿态）。

    用"并集"而不是直接返回全集：链上其它目标池修饰器（【空城】一类剔除）仍能删掉不合法的
    目标，本回调只**补上**被攻击范围挡掉的那些。
    """
    if ctx.owner != ctx.subject:
        return current
    if not _zhaxiang_phase_active(state, ctx.owner):
        return current
    if not _zhaxiang_red_slash(state, ctx.card):
        return current
    everyone = tuple(
        player.seat for player in state.players if player.alive and player.seat != ctx.subject
    )
    return tuple(dict.fromkeys((*current, *everyone)))


def _zhaxiang_extra_slash(
    state: GameState, ctx: ModifierContext, current: Any
) -> Any:
    """``COUNT_LIMIT``：本阶段你使用【杀】的次数上限 +1（无次数限制者保持无限）。"""
    if ctx.owner != ctx.subject:
        return current
    if not _zhaxiang_phase_active(state, ctx.owner):
        return current
    if ctx.card is None or not is_slash(behavior_for(ctx.card)):
        return current
    if current is None:
        return None
    return int(current) + 1


def _zhaxiang_unresponsive_condition(host: Any, frame: Any) -> bool:
    """``USE_AFTER_DESIGNATE_TARGET``：本阶段**自己**使用的红色【杀】不能被【闪】响应。"""
    state = host.state
    owner = _owns(state, frame)
    if owner is None:
        return False
    if frame.data.get("source") != owner:
        return False
    if not _zhaxiang_phase_active(state, owner):
        return False
    return _zhaxiang_red_slash(state, frame.data.get("card"))


def _zhaxiang_unresponsive(host: Any, frame: Any) -> None:
    """给本次使用打**全场档**不可响应（逐目标窗口都由它跳过，与界【离间】同一读取面）。"""
    del host
    add_tag(frame, Tag.UNRESPONSIVE)


def life_after_skill_definitions() -> tuple[SkillDef, ...]:
    """E 族（失去体力后）一条技能的 :class:`SkillDef`：界黄盖【诈降】。

    锁定技（技能面写"锁定技"）⇒ ``locked=True``、"是否发动"一个询问都不发；三条连续型效果
    里的两条落修饰器（``TARGET_POOL`` / ``COUNT_LIMIT``）、"不能被【闪】响应"落帧标签，
    故形态是「锁定技」格（`tests/test_hero_catalog.py` 的形态表由机制读出来）。
    """
    return (
        SkillDef(
            skill_id=JIE_ZHAXIANG,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(Timing.LIFE_DECREASE_AFTER, handler=_zhaxiang_after_losing_hp,
                     condition=_zhaxiang_condition),
                Hook(
                    Timing.USE_AFTER_DESIGNATE_TARGET,
                    handler=_zhaxiang_unresponsive,
                    condition=_zhaxiang_unresponsive_condition,
                ),
            ),
            modifiers=(
                Modifier(ModifierPoint.TARGET_POOL, _zhaxiang_no_distance),
                Modifier(ModifierPoint.COUNT_LIMIT, _zhaxiang_extra_slash),
            ),
            requirement=(
                "锁定技：当你失去 1 点体力后，摸三张牌；若此刻是你的出牌阶段，本阶段你使用"
                "红色【杀】无距离限制且不能被【闪】响应、使用【杀】的次数上限 +1"
            ),
        ),
    )
