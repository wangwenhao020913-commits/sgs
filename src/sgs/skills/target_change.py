r"""K 族【马术】与 M 族【流离】（票 12 登记）。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` 等同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，
定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

两条技能各落一个**既有**机制（票 12 §落地表第 11/22 行），本票不加任何新机制：

===========  ======  ==========================================================
技能           锁定    落点
===========  ======  ==========================================================
马超【马术】    锁定    ``Modifier(DISTANCE, −1)``（**主体侧**：与 −1 坐骑同一条链）
大乔【流离】    非锁定  ``Hook(USE_BECOME_TARGET)`` 上的两步 ``SkillFlow``：弃一张牌，
                       把【杀】**转移目标**
===========  ======  ==========================================================

【马术】（规则 986）：锁定技，你**计算与其他角色的距离** −1。方向性照坐骑的既有分列
（``ctx.subject`` = 主体、``ctx.target`` = 客体，见 :func:`sgs.effects.distance`）：
只有持有者当**主体**时 −1，别人算到他的距离不变。下限 1 由距离管道在链后夹住，
故本回调不必自己夹。

【流离】（规则 1025）：当你成为【杀】的目标时，你可以弃置一张牌，将此【杀】转移给你
攻击范围内的一名其他角色（该角色不得是此【杀】的使用者）。规则 17.4/17.5 把它归在
**②成为目标时**（早于③【铁骑】），故挂 ``USE_BECOME_TARGET``；转移发生在**目标确定
阶段**，不是"牌已指定后令无效"（17.6）——落点就是把本次使用的 ``targets`` 里那一个目标
换成新目标，再对新目标重发一次"成为目标时"（新目标因此照常进响应窗口）。
"攻击范围内"读**持有者自己**的攻击范围（牌面写"你攻击范围内"），使用者与持有者自己
都被排除；新目标还必须在场（``targets_within_attack_range`` 只给存活者）。

**票 08：从手写钩子迁到两步 ``SkillFlow``**：

============  ==================================================================
步 1（成本）   ``PickCards(HAND ∪ EQUIP, 1..1)``，``face`` = 持有者（可见面）
步 2（新目标） ``PickSeats(pool=_liuli_target_pool)`` ⇒ ``SELECT_OPTION`` 的 ``seat:N``
============  ==================================================================

- 成本搬牌**推迟到提交点**（``effect`` 的第一件事）：这是票 00/01 的 N6"选择只收集、
  提交才生效"。``liuli:cost_moved`` 那道帧内游标原样保留——搬牌可能内联派发"失去牌后"
  并挂起，``move_in_frame`` 返回 ``"stay"`` 时透传。
- 步 2 从**座位槽的 ``SELECT_CARDS``** 改成 ``SELECT_OPTION`` 的 ``seat:N``——这是**有意
  的用户可见交互变更**：与 A 族【离间】/【青囊】/【结姻】同一条约定（``seat:N`` 让前端
  直接高亮场上武将、不铺选项按钮）。含大乔的 Golden Replay 据此**重录**（不是手改基准）。
- 两步都 ``optional=False``：**有意保留今天的取消出口**（只有发动询问那一道；迁移前两步
  也都没有 ``Pass``）。给一个 ``Pass`` 会新增取消出口、改记录流与既有录像。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, Modifier, ModifierContext, ModifierPoint
from sgs.effects import targets_within_attack_range
from sgs.resolution import Control, Frame
from sgs.state import GameState, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef
from .flow import FlowAnswers, PickCards, PickSeats, SkillFlow

__all__ = ["LIULI_FLOW", "LIULI", "MASHU", "target_change_skill_definitions"]

MASHU = hero_skill_id("machao", "mashu")
"""马超【马术】的全限定技能 id（前缀派生走 :func:`sgs.data.hero_skill_id`）。"""

LIULI = hero_skill_id("daqiao", "liuli")
"""大乔【流离】的全限定技能 id。"""

_LIULI_USED = "liuli_used"
"""帧私有键：本次使用里【流离】已经转移过目标（重入不再问、不再转移）。"""


# ---------------------------------------------------------------------------
# 马超【马术】：DISTANCE −1（主体侧）
# ---------------------------------------------------------------------------


def _mashu_distance(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``DISTANCE`` 回调：持有者当**主体**时 −1（客体侧不改；下限由管道夹住）。"""
    del state
    if ctx.owner != ctx.subject:
        return current
    return current - 1


# ---------------------------------------------------------------------------
# 大乔【流离】：转移目标
# ---------------------------------------------------------------------------


def _liuli_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**持有者是这次【杀】的目标**、使用者是别人、且本人还没转移过。

    ``condition`` 在派发器写 ``frame.data["skill_owner"]`` **之前**求值，故"持有者是谁"只能读
    派发器在求值前写好的 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（票 13）。
    漏掉这一判据，条件对**每个存活玩家**的实例都成立 ⇒ 任何角色成为【杀】的目标都会弹
    【流离】的"是否发动"，答发动后又被 :func:`_liuli_applies` 挡回 ⇒ 静默取消
    （票 13；用户实机报障的"无事发生"）。
    """
    del host
    if frame.data.get(_LIULI_USED):
        return False
    if frame.data.get(MATCH_OWNER_KEY) not in tuple(frame.data.get("targets", ())):
        return False
    if frame.data.get("source") == frame.data.get("actor"):
        return False
    card = frame.data.get("card")
    if card is None or card.type != "slash":
        return False
    return True


def _liuli_target_pool(
    state: GameState, frame: Frame, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """可选的新目标：持有者攻击范围内、**不是**此【杀】的使用者、也不是持有者自己。

    ``chosen``（票 07 给 :data:`~sgs.skills.flow.SeatPool` 的签名）在本步恒为空——【流离】
    只选一名新目标，没有"第二问据此排除第一问"的多选语义。

    **攻击范围按"成本已付"重算**（W4-2，票面 §2 的同一条判据）：步骤只收集、成本在提交点
    才搬走，但规则读的是**弃掉那张牌之后**的场况——弃自己的武器时范围回落。已选的成本牌
    从前序答案读（清单渲染与校验器共用的 ``preactivation_answers`` 通道），交给
    :func:`~sgs.effects.targets_within_attack_range` 的 ``excluding`` 用同一公式重算。
    """
    del chosen
    user = frame.data.get("source")
    answers = frame.data.get("preactivation_answers") or {}
    cost = tuple(int(card_id) for card_id in (answers.get("card") or ()))
    return tuple(
        seat
        for seat in targets_within_attack_range(state, owner, excluding=cost)
        if seat != owner and seat != user
    )


def _liuli_applies(state: GameState, frame: Frame, owner: int) -> bool:
    """**这一步此刻存在吗**：迁移前手写回调里的全部提前返回，收在一处。

    票 13 之前，``condition`` 在求值期读不到持有者，故"是不是这次【杀】的目标""使用者是不是
    自己""有没有合法新目标"都必须在这里再判一次（否则每个实例都会走到效果体）。票 13 让
    :func:`_liuli_condition` 读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY` 自校后，过闸的
    实例已经只剩"本次【杀】的目标"；这一档作为**独立的第二道闸**原样保留（与
    :func:`_liuli_effect` 的复核同姿态），并继续承担"没有合法新目标 ⇒ 连成本都不问"：
    返回 ``False`` ⇒ 成本步的 ``face`` 给出**空元组**，执行器据此**一个询问都不发**
    （与迁移前 ``_liuli`` 的提前返回同形）。
    """
    if frame.data.get(_LIULI_USED):
        return False
    if owner not in tuple(frame.data.get("targets", ())):
        return False
    if frame.data.get("source") == owner:
        return False
    card = frame.data.get("card")
    if card is None or card.type != "slash":
        return False
    return bool(_liuli_target_pool(state, frame, owner, ()))


def _liuli_cost_face(state: GameState, frame: Frame, owner: int, answers: FlowAnswers) -> Any:
    """步 1 的选取面：条件成立时是持有者本人，否则**空元组** = 这一步不存在（票 07 的形状）。

    .. note::

       这里用 ``face`` 而不是 ``condition`` 承载守卫：``face`` 的空元组是执行器里
       "**退化面不询问**"的唯一入口，且它在两步流水线里**先于**步 2 的池求值发生——
       于是"没有合法新目标 ⇒ 连成本都不问"与迁移前逐字同形。
    """
    del answers
    return owner if _liuli_applies(state, frame, owner) else ()


def _liuli_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Control:
    """提交点：弃成本（步 1 选中的牌）→ 转移目标 → 给新目标补发"成为目标时"。

    **成本搬牌在这里才发生**（N6"选择只收集、提交才生效"）：步 1 只收答案、不搬牌。
    ``liuli:cost_moved`` 帧内游标原样保留——搬牌可能内联派发"失去牌后"并挂起，
    ``move_in_frame`` 返回 ``"stay"`` 时**原样透传**；重入时跳过搬牌、直接续跑转移与补派发
    （迁移前的注释与语义一字不差）。

    与【反馈】的 :func:`~sgs.skills.damage_after._fankui_effect` 同姿态：效果体**独立复核**
    "持有者仍是本次使用的目标、使用者不是自己、渠道牌仍是【杀】"——守卫住"无论怎么走到
    这里都不会转移错"，不依赖步骤声明这一个前提。
    """
    owner = frame.data["skill_owner"]
    targets = tuple(frame.data.get("targets", ()))
    if owner not in targets or frame.data.get("source") == owner:
        return None
    card = frame.data.get("card")
    if card is None or card.type != "slash":
        return None
    new_target = int(tuple(answers["seat"])[0])

    moved_key = "liuli:cost_moved"
    if not frame.data.get(moved_key):
        frame.data[moved_key] = True
        if (
            host.move_in_frame(
                frame,
                host.state.refs(answers["card"]),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=owner,
                actor=owner,
            )
            == "stay"
        ):
            return "stay"
    frame.data[_LIULI_USED] = True
    frame.data["targets"] = tuple(new_target if seat == owner else seat for seat in targets)
    # 新目标也是"成为目标时"：对它补一条派发（它因此照常进响应窗口）。**载荷显式给
    # ``targets=(new_target,)``**，使这次记录与 `use_become_target` 重入时那一轮的键一致——
    # 于是那一步若再扫到新目标会被记录幂等吃掉（不会重复一条时机）。
    # 被转移掉的那一个由 `use_become_target` 的"已派发名单"保证不再派发。
    host.emit_timing(
        Timing.USE_BECOME_TARGET,
        actor=new_target,
        source=frame.data.get("source"),
        targets=(new_target,),
        frame_id=frame.frame_id,
        depth=host.stack.depth,
    )
    return "stay"


LIULI_FLOW = SkillFlow(
    name="liuli",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            # **有意保留现状**（票 08 §要做什么）：迁移前的两步都没有 `Pass`，取消的唯一
            # 出口是发动询问那一道。改成 True 会新增取消出口、改记录流与既有录像。
            optional=False,
            key="card",
            # 面 = 持有者本人（可见手牌 + 装备），与前手写的
            # ``selection_face(owner, (HAND, EQUIP), reveal_hand=True)`` 同源；空元组则
            # 表示"这一步此刻不存在"（不是这次【杀】的目标 / 没有合法新目标）。
            face=_liuli_cost_face,
        ),
        PickSeats(
            lo=1,
            hi=1,
            pool=_liuli_target_pool,
            optional=False,
            key="seat",
        ),
    ),
    effect=_liuli_effect,
)
"""【流离】的两步流水线（票 08）：弃一张牌（手牌 / 装备区）→ 选新目标（``seat:N``）。

成本搬牌、目标替换与补派发都在 :func:`_liuli_effect`（提交点）里做；步 2 的
``PickSeats`` 让新目标以 ``SELECT_OPTION`` 的 ``seat:N`` 表达（票 08 的用户可见交互变更）。
"""


def target_change_skill_definitions() -> tuple[SkillDef, ...]:
    """K 族【马术】与 M 族【流离】的 :class:`~sgs.skills.catalog.SkillDef`。

    【马术】锁定（技能面写"锁定技"）⇒ ``locked=True``、落 ``DISTANCE`` 修饰器；
    【流离】非锁定（"你可以"）⇒ ``locked=False``、落 ``USE_BECOME_TARGET`` 钩子（票 08 起
    该钩子声明 ``flow=LIULI_FLOW``）。两条 ``carrier = HERO``（优先级档由载体派生）、
    ``skill_type = NORMAL``。
    """
    return (
        SkillDef(
            skill_id=MASHU,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _mashu_distance),),
            requirement="计算与其他角色的距离时 -1",
        ),
        SkillDef(
            skill_id=LIULI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.USE_BECOME_TARGET,
                    condition=_liuli_condition,
                    flow=LIULI_FLOW,
                ),
            ),
            requirement="弃一张牌，把【杀】转移给另一名角色",
            step_requirements={
                "card": "弃置一张牌（手牌 / 装备区）",
                "seat": "选择将此【杀】转移给哪名其他角色",
            },
            preactivated=True,
        ),
    )
