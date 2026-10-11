r"""K 族【马术】与 M 族【流离】（票 12 登记）。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` 等同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，
定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

两条技能各落一个**既有**机制（票 12 §落地表第 11/22 行），本票不加任何新机制：

===========  ======  ==========================================================
技能           锁定    落点
===========  ======  ==========================================================
马超【马术】    锁定    ``Modifier(DISTANCE, −1)``（**主体侧**：与 −1 坐骑同一条链）
大乔【流离】    非锁定  ``Hook(USE_BECOME_TARGET)``：弃一张牌，把【杀】**转移目标**
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
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, Modifier, ModifierContext, ModifierPoint
from sgs.effects import targets_within_attack_range
from sgs.resolution import Control, Frame
from sgs.state import GameState, MoveReason, SelectionSpec, Slot, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import Hook, SkillDef

__all__ = ["LIULI", "MASHU", "target_change_skill_definitions"]

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

    ``condition`` 在派发器写 ``frame.data["skill_owner"]`` **之前**求值（票 10 §读法 5），
    故这里读不到持有者；"持有者是谁"由回调 :func:`_liuli` 用 ``skill_owner`` 再判一次。
    """
    del host
    if frame.data.get(_LIULI_USED):
        return False
    if frame.data.get("source") == frame.data.get("actor"):
        return False
    card = frame.data.get("card")
    if card is None or card.type != "slash":
        return False
    return True


def _liuli_target_pool(state: GameState, frame: Frame, owner: int) -> tuple[int, ...]:
    """可选的新目标：持有者攻击范围内、**不是**此【杀】的使用者、也不是持有者自己。"""
    user = frame.data.get("source")
    return tuple(
        seat
        for seat in targets_within_attack_range(state, owner)
        if seat != owner and seat != user
    )


def _liuli(host: Any, frame: Frame) -> Control:
    """【流离】的 ``USE_BECOME_TARGET`` 钩子：是否发动已由派发器问过；弃一张牌并转移目标。

    两次选牌都用**面内位置**表达载荷：成本取自持有者的手牌 ∪ 装备区（可见面）；
    新目标的选择面铺的是**座位槽**（``Slot(ref=ZoneRef(HAND, seat), card=None)``）——
    位置 = 座位、不泄露牌面，选中后再解析回座位号。回答缓存在帧内（``key``），
    故重入不会重问。
    """
    owner = frame.data["skill_owner"]
    targets = tuple(frame.data.get("targets", ()))
    if owner not in targets or frame.data.get(_LIULI_USED):
        # 已经转移过（重入）或持有者本就不是这次使用的目标（别人的目标时机派发）。
        return None
    user = frame.data.get("source")
    if user == owner:
        return None
    card = frame.data.get("card")
    if card is None or card.type != "slash":
        return None
    pool = _liuli_target_pool(host.state, frame, owner)
    if not pool:
        return None

    cost = host.ask_selection(
        frame,
        key="liuli:cost",
        actor=owner,
        timing=Timing.USE_BECOME_TARGET,
        spec=SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, 1)),
        build_face=lambda: host.selection_face(
            owner, (Zone.HAND, Zone.EQUIP), reveal_hand=True
        ),
    )
    if host.suspended(cost):
        return "stay"
    if not cost:
        return None

    chosen = host.ask_selection(
        frame,
        key="liuli:target",
        actor=owner,
        timing=Timing.USE_BECOME_TARGET,
        spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
        build_face=lambda: (
            tuple(Slot(ref=ZoneRef(Zone.HAND, seat)) for seat in pool),
            tuple(range(len(pool))),
        ),
    )
    if host.suspended(chosen):
        return "stay"
    if not chosen:
        return None
    new_target = pool[chosen[0]]

    # 成本搬牌可能内联派发"失去牌后"并挂起（票 14 §1）：先落"已搬"游标，`"stay"` 原样
    # 透传；重入时跳过搬牌、直接续跑转移与补派发。
    moved_key = "liuli:cost_moved"
    if not frame.data.get(moved_key):
        frame.data[moved_key] = True
        if (
            host.move_in_frame(
                frame,
                host.state.refs(cost),
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


def target_change_skill_definitions() -> tuple[SkillDef, ...]:
    """K 族【马术】与 M 族【流离】的 :class:`~sgs.skills.catalog.SkillDef`。

    【马术】锁定（技能面写"锁定技"）⇒ ``locked=True``、落 ``DISTANCE`` 修饰器；
    【流离】非锁定（"你可以"）⇒ ``locked=False``、落 ``USE_BECOME_TARGET`` 钩子。
    两条 ``carrier = HERO``（优先级档由载体派生）、``skill_type = NORMAL``。
    """
    return (
        SkillDef(
            skill_id=MASHU,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.DISTANCE, _mashu_distance),),
        ),
        SkillDef(
            skill_id=LIULI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.USE_BECOME_TARGET, _liuli, _liuli_condition),),
        ),
    )
