r"""状态位变化后（S 族）：陈群 / 界陈群移动版【法恩】——「当一名角色翻面或横置后，你可以令其摸一张牌」。

牌面（用户 ``技能.txt`` 逐字，**两版逐字相同**）：

    当一名角色翻面或横置后，你可以令其摸一张牌。

**两个时机 = 两条独立钩子**：翻面与横置在规则里是两个**独立状态**（规则 §13.1
L1387–L1392「同一武将牌上『横置』与『背面朝上』是两个独立状态」/ §8.10 L873–L882），
同一角色身上两次变化各问一次。派发点 = ``Engine.set_flipped`` / ``Engine.set_chained``
的写入口（chenqun-guyong 票 03 / W2 解封），口径 = 用户 2026-11-09 签的 **Q-W2 = 读法一**：

- 状态**值真的变了**才是一次事件（两个写入口原有的幂等早退照旧 ⇒ 同值重写不派发）；
- 「回合开始前翻回正面」（``frame=None`` 的规则固定流程）**算**一次；
- 一趟【铁索连环】横置 2 名角色 = **2 次**机会（一个座位一次）。

派发是**惰性且延后**的（写入口排队、下一条 ``_step`` 的开头才用一条不压栈的临时帧派发）
⇒ 本族钩子只读 :func:`sgs.skills.state_change` 下发的载荷（写入口那条 delta：``seat`` +
**新值**），不读"派发那一刻的当前状态"——同一 ``_step`` 里可以有第二次变化。

效果形状 = `shapes.md` §2 第 1 行「零步流水线（只问是否发动）」（样板
``hero.sunshangxiang.xiaoji``）：问一次「是否发动」，答发动 ⇒ **刚翻面 / 横置的那名角色**
摸一张（「令其」= 那名角色，不是技能持有者）。

**两条钩子的方向判据不同**（票 10 / P1，用户 2026-11-09 实机报障）：牌面写的是「横置**后**」，
**重置不是横置**（例如属性【杀】结算末尾把横置者的 ``chained`` 写回 ``False``）⇒ ``STATE_CHAINED``
的钩子带条件 :func:`_faen_chain_is_set`（``change.chained is True``）；翻面维持现状（两个方向都算
一次，用户同日签的 Q-W2 = 读法一）。**派发口径不动**（``Engine.set_chained`` 照旧为重置发一条
``STATE_CHAINED``）——方向是**技能侧**的判据，落在 ``Hook.condition`` 上（连「是否发动」都不问）。

``STATE_FLIPPED`` 仍**不设 ``condition``**：派发点上"一名角色翻面了"恒成立，唯一想挡的是"座位已
阵亡"（对它摸牌没有意义，且它不会被写状态位），那一层放在效果里。而带条件的那条钩子会撞
``tests/test_hero_catalog.py`` 的「钩子条件必须自校持有者」审计——【法恩】的条件**不关于持有者**
（关于"这次变化是不是横置方向"），故按该审计的例外表登记一条理由（票 10 §4 明确要求按例外处理），
不为它造一个假的"只问持有者"探针。

**两版共用一份实现**（yijiang-2014 票 08 交 OL 陈群；yijiang-2014-round2 票 11 / P2 补界陈群移动版）：
牌面**逐字相同**，``SkillDef`` 没有 ``shared_with`` 那种派生口子 ⇒ 落地形态 = "同一组回调 +
同一个方向判据 + 同一份零步流水线（:data:`CHENQUN_FAEN_FLOW`，对象复用、不另起 flow 名）+
两条 ``CATALOG`` 条目"。界版**不重写**方向判据（票 10 落在 :func:`_faen_chain_is_set` /
:func:`_faen_seat` 里），故票 10 的「重置不问」口径对界版天然生效。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind
from sgs.resolution import Control, Frame
from sgs.state import ChainedChangeDelta
from sgs.timing import Timing

from .catalog import Hook, SkillDef, state_change
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "CHENQUN_FAEN",
    "CHENQUN_FAEN_FLOW",
    "JIE_CHENQUN_FAEN",
    "state_after_skill_definitions",
]

CHENQUN_FAEN = hero_skill_id("chenqun", "faen")
"""陈群（一将成名-2014 / OL）【法恩】的全限定技能 id。

与界陈群移动版的同名技能牌面**逐字相同**，但 ``SkillDef`` 没有 ``shared_with`` 那种
派生口子（chenqun-guyong 票 03 的 Answer 已裁定不补）⇒ 界版是"同一组回调 +
同一份零步流水线 + 第二条 ``CATALOG`` 条目"（见 :data:`JIE_CHENQUN_FAEN`）。
"""

JIE_CHENQUN_FAEN = hero_skill_id("jie_chenqun_mobile", "faen")
"""界陈群移动版【法恩】的全限定技能 id（yijiang-2014-round2 票 11 / P2）。

与 :data:`CHENQUN_FAEN` **牌面逐字相同** ⇒ 复用同一组回调（:func:`_chenqun_faen` /
:func:`_faen_seat`）、同一个方向判据（:func:`_faen_chain_is_set`）与同一个零步流水线
对象（:data:`CHENQUN_FAEN_FLOW`）；本行只提供第二条 ``CATALOG`` 条目。
"""

_DRAWN_KEY = "chenqun_faen_drawn"
"""帧私有：本帧已经因【法恩】摸过牌（重入不再摸；不进记录流）。

两版【法恩】共用本键（一帧 = 一次状态位事件）。两版真名相同、正常发牌不会同桌同时出场
（``test_hero_dealing`` 的口径）⇒ 不会互相吞摸；若将来手工把两版放进同一局，同一个事件帧
上第二版的摸牌会被这个守卫挡掉——那是"同一事件只结算一次"的既有姿态，不是本票要改的面。
"""


def _faen_chain_is_set(host: Any, frame: Frame) -> bool:
    """方向判据：这一次 ``STATE_CHAINED`` 事件是**横置**（``chained=True``），不是**重置**。

    牌面写的是「横置**后**」⇒ 「未横置 → 横置」算一次「横置后」；「已横置 → 重置」（``False``，
    含属性伤害结算末尾把伤害来源的重置写回）**不是**（用户 2026-11-09 实机报障逐字：
    「解除横置…时不应该发动」）。判据读载荷里的**新值**、不读派发那一刻的当前状态：派发是
    **延后**的，同一 ``_step`` 里可以先横置再重置，那时当前值已经翻回 ``False``。

    签名是 :data:`~sgs.skills.catalog.HookCondition` 的 ``(host, frame)``；本条条件**不关于
    持有者**（关于"这次变化是不是横置方向"）⇒ ``host`` 不使用。
    """
    del host
    change = state_change(frame)
    return isinstance(change, ChainedChangeDelta) and change.chained is True


def _faen_seat(host: Any, frame: Frame) -> int | None:
    """本帧状态位事件的**座位** = 刚翻面 / 横置的那名角色；不是状态位事件（或它已阵亡）⇒ ``None``。

    座位与"往哪个方向变"都从载荷读（:func:`~sgs.skills.state_change`）：派发是**延后**的，
    现算当前状态会在"同一 ``_step`` 里连着两次变化"时读到后一次的值。

    方向**也**在这里再挡一次（重置 ⇒ ``None``）：「是否发动」已被 :func:`_faen_chain_is_set`
    挡在派发环里，这一层是给效果自己的不变量（即使将来有别的派发点复用本回调，也不会因重置
    摸牌）——界陈群移动版落地（票 11）复用同一份回调时同样受它保护。
    """
    change = state_change(frame)
    if change is None:
        return None
    if isinstance(change, ChainedChangeDelta) and not _faen_chain_is_set(host, frame):
        return None
    seat = change.seat
    return seat if host.state.player(seat).alive else None


def _chenqun_faen(host: Any, frame: Frame) -> Control:
    """效果：令**刚翻面 / 横置的那名角色**摸一张牌（"是否发动"已由派发环问过）。

    游标**先落位再摸**（【渐营】同款）：摸牌可能内联触发别人的「获得牌后 / 失去牌后」钩子
    并挂起，重入时 ``run_flow`` 会从头重跑 effect，没有这个守卫就会多摸。
    """
    seat = _faen_seat(host, frame)
    if seat is None:
        return None
    if frame.data.get(_DRAWN_KEY):
        return None
    frame.data[_DRAWN_KEY] = True
    control = host.draw_for_hand(frame, seat, 1)
    if control == "stay":
        return "stay"
    return None


CHENQUN_FAEN_FLOW = SkillFlow(
    name="chenqun_faen", steps=(), effect=zero_step_effect(_chenqun_faen)
)
"""【法恩】的零步流水线（`shapes.md` §2 第 1 行；样板【枭姬】）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的 flow（先例 :data:`~sgs.skills.damage_after.FANKUI_FLOW`）。

两版【法恩】（OL / 界陈群移动版）**共用这个对象**（票 11 明写"复用对象、不另起 flow 名"）：
``test_ask_points`` 的 ``name`` 唯一性只核**模块级定义**，故一个对象服务两条 ``CATALOG``
条目不撞；效果与方向判据因此只有一份，界版不会漂出第二套读法。
"""


def state_after_skill_definitions() -> tuple[SkillDef, ...]:
    """S 族（状态位变化后）的技能定义：陈群【法恩】+ 界陈群移动版【法恩】（非锁定 ⇒ 每事件问一次）。

    两条**牌面逐字相同**，故共用同一组回调、同一个方向判据与同一个零步流水线对象；区别只在
    ``skill_id``（``CATALOG`` 两条、数据行各声明一条）。``STATE_CHAINED`` 那一条带方向条件
    （票 10）：**重置不问**，翻面两个方向都问。
    """
    return (
        SkillDef(
            skill_id=CHENQUN_FAEN,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(Timing.STATE_FLIPPED, flow=CHENQUN_FAEN_FLOW),
                Hook(
                    Timing.STATE_CHAINED,
                    condition=_faen_chain_is_set,
                    flow=CHENQUN_FAEN_FLOW,
                ),
            ),
            requirement="当一名角色翻面或横置后，你可以令其摸一张牌",
        ),
        SkillDef(
            skill_id=JIE_CHENQUN_FAEN,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(Timing.STATE_FLIPPED, flow=CHENQUN_FAEN_FLOW),
                Hook(
                    Timing.STATE_CHAINED,
                    condition=_faen_chain_is_set,
                    flow=CHENQUN_FAEN_FLOW,
                ),
            ),
            requirement="当一名角色翻面或横置后，你可以令其摸一张牌",
        ),
    )
