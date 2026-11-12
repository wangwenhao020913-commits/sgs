"""死亡时 / 标记族：神关羽【武魂】（票 04 / 票 06 / 票 07 / 票 10）、曹丕【行殇】。

牌面（用户 `技能.txt`，逐字）：

    武魂
    　锁定技，①当你受到其他角色造成的1点伤害后，你令该角色获得1枚“梦魇”标记；
    　②当你死亡时，你令拥有“梦魇”标记最多的一名角色进行一次判定，若结果不为【桃】或
    　　【桃园结义】，该角色死亡。

    行殇
    　当其他角色死亡时，你可以获得其所有牌。

三条裁定（用户 Q6 / 本轮）：

- 标记 = `(名称, 来源 = 座位 + 全限定技能 id)` 的实例；同名同来源合并、异来源分列；
  「梦魇」无上限、无自身效果 / 说明，寿命到神关羽阵亡后；**武魂②只统计来源为自身**的
  梦魇（别人获得的武魂不统计神关羽的标记）。
- 死亡时技能由**死亡时技能窗口**派发（票 10）：`alive=False` 仍在死亡入口落，窗口在
  `DEATH_ON` 前打开、`DEATH_AFTER` 后关闭；武魂②因此在 `DEATH_ON` 跑得到。
- 武魂②的致死是**技能直接致死**（`Engine.pass_away`，票 07 V1）：无伤害来源 ⇒ 无 killer
  ⇒ 无死亡奖惩；不进濒死 / 求桃。游戏已判胜时整段跳过（票 07 V3 + BWiki Q&A）。

【行殇】是 `DEATH_ON` 上的**第二条**钩子（lin-batch2 票 01），方向与武魂②相反——它关于
**别人**的死（"死者不是持有者"），故走的是豁免表而不是持有者自校（票面 Q6 = A）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import hero_skill_id
from sgs.effects import EffectContext, move_card
from sgs.state import MoveReason, Pass, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    SKILL_JUDGMENT_CARD_KEY,
    CarrierKind,
    Hook,
    SkillDef,
)
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "MENGYAN",
    "WUHUN",
    "WUHUN_DEATH_ASK",
    "WUHUN_REQUIREMENT",
    "XINGSHANG",
    "XINGSHANG_FLOW",
    "XINGSHANG_REQUIREMENT",
    "death_after_skill_definitions",
]

WUHUN = hero_skill_id("shen_guanyu_mobile", "wuhun")
"""神关羽【武魂】的全限定技能 id（标记的来源键用同一个 id）。"""

MENGYAN = "梦魇"
"""标记名称（用户 Q6：中文名、无 id）。"""

WUHUN_REQUIREMENT = (
    "当你受到其他角色造成的1点伤害后，令其获得1枚「梦魇」；当你死亡时，令来源为你的"
    "梦魇标记最多的一名存活角色判定，结果不为【桃】或【桃园结义】则其死亡"
)

WUHUN_DEATH_ASK = "wuhun:pick"
"""平票选人的询问标识；`step_requirements["pick"]` 是它的文案键。"""

_TARGET_KEY = "wuhun_target"
_JUDGED_KEY = "wuhun_judged"
_DONE_KEY = "wuhun_done"


def _mark_condition(host: Any, frame: Any) -> bool:
    """①的触发条件：受伤的是持有者，且有一个**其他存活角色**作为伤害来源。

    逐点由 ``Hook.per_point=True`` 驱动；条件自校持有者（``MATCH_OWNER_KEY``），
    否则任何人的伤害都会给谁加标记。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("target") != owner:
        return False
    state = host.state
    if not state.player(owner).alive:
        return False
    source = frame.data.get("source")
    return (
        source is not None
        and int(source) != int(owner)
        and state.player(int(source)).alive
    )


def _mark(host: Any, frame: Any) -> None:
    """①：给伤害来源加 1 枚「梦魇」（来源 = 神关羽的武魂；寿命是 2018 口径的通则）。"""
    owner = int(frame.data["skill_owner"])
    source = int(frame.data["source"])
    host.add_marker(
        frame,
        source,
        name=MENGYAN,
        source_seat=owner,
        source_skill_id=WUHUN,
        count=1,
    )


def _death_condition(host: Any, frame: Any) -> bool:
    """②的触发条件：**死亡的是持有者自己**（持有者自校；死亡时窗口内可派发）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    return owner is not None and frame.data.get("target") == owner


def _wuhun_death(host: Any, frame: Any) -> Any:
    """②：读自己来源的梦魇 → （平票由死者选）→ 判定 → 非桃 / 桃园结义 ⇒ 直接致死。

    帧私有游标：``wuhun_target``（已选目标）/ ``wuhun_judged``（判定帧已压）/
    ``wuhun_done``（收尾）。挂起后重入既不重选也不重判；压判定 / 致死前先落 ``done``
    的对应位，子帧出栈后重入直接收尾。
    """
    owner = frame.data.get("skill_owner")
    if owner is None or frame.data.get(_DONE_KEY):
        return None
    if host.outcome_decided():
        # BWiki Q&A：神关羽死亡时已经符合游戏结束条件 ⇒ 不能发动武魂。
        frame.data[_DONE_KEY] = True
        return None
    state = host.state
    totals: list[tuple[int, int]] = []
    for player in state.players:
        if not player.alive:
            continue
        total = sum(
            marker.count
            for marker in player.markers
            if marker.name == MENGYAN
            and marker.source_seat == int(owner)
            and marker.source_skill_id == WUHUN
        )
        if total > 0:
            totals.append((total, player.seat))
    if not totals:
        frame.data[_DONE_KEY] = True
        return None
    top = max(total for total, _seat in totals)
    tied = tuple(seat for total, seat in totals if total == top)
    target = frame.data.get(_TARGET_KEY)
    if target is None:
        if len(tied) == 1:
            target = tied[0]
        else:
            options = tuple(f"seat:{seat}" for seat in tied)
            answer = host.ask_option(
                frame,
                int(owner),
                Timing.DEATH_ON,
                skill_id=WUHUN_DEATH_ASK,
                options=options,
                optional=False,
            )
            if host.suspended(answer):
                return "stay"
            if isinstance(answer, Pass):  # pragma: no cover - optional=False 不产 Pass
                frame.data[_DONE_KEY] = True
                return None
            target = int(str(answer.choice).removeprefix("seat:"))
        frame.data[_TARGET_KEY] = target
    if not frame.data.get(_JUDGED_KEY):
        frame.data[_JUDGED_KEY] = True
        host.open_skill_judgment(frame, int(target), WUHUN)
        return "stay"
    card = frame.data.pop(SKILL_JUDGMENT_CARD_KEY, None)
    frame.data.pop("skill_result", None)
    if card is None or card.type in ("peach", "peach_garden"):
        frame.data[_DONE_KEY] = True
        return None
    frame.data[_DONE_KEY] = True
    host.pass_away(frame, int(target))
    return "stay"


# ---------------------------------------------------------------------------
# 林包·移动版·曹丕【行殇】（lin-batch2 票 01；用户 2026-11-08 签 Q1–Q3 = A）
#
#     行殇
#     　当其他角色死亡时，你可以获得其所有牌。
#
# 三条口径（票面 `## 待签字问题` Q1 / Q2 / Q3，签字不放宽）：
#
# - **Q1 = A（取哪几个区）**：**手牌 + 装备区**。判定区**不取**（账本 L674「判定区不默认
#   包含」+ L775②「没写区域 ⇒ 手牌 + 装备」；判定区的牌不为任何角色所拥有 ⇒ 不能「获得」，
#   `参考资料/技能描述歧义核查.md` B-11）；武将牌上 / 旁的牌也不取（规则 §15.1「移出游戏、
#   不为任何角色所拥有」）。时段上拿得到：`DEATH_ON` 的派发严格早于
#   `death_settle_cards`（`steps.py` 的 `DEATH_STEPS`），取完剩下的（判定区）才被系统弃掉。
# - **Q2 = A（要不要派发"失去牌后"）**：**不派发**。搬牌走 `move_card` + `emit_frame_delta`
#   （`Engine.discard_dead_cards` 的同一条先例）：失去牌的是**死者**，账本 L625 的判据
#   「失去牌的是不是存活角色」直接答"否" ⇒ 死者自己的【连营】一类一问都不发。**不要**换
#   `move_in_frame`——它内联派发 `MOVE_AFTER`（`game.py` 的移动入口），与 Q2 相反。
# - **Q3 = A（没牌 / 已判胜要不要问）**：**连问都不发**。终局位（`Engine.pending_outcome`）
#   只挂在引擎上、`ACTIVATION_PLANS` 的构造器拿不到 ⇒ 这一道只能写在 `condition` 里
#   （照【武魂】②的 `host.outcome_decided()` 先例；`tests/test_hero_catalog.py` 的
#   `_HookHost` 因此补了一行 `outcome_decided`）。
# ---------------------------------------------------------------------------

XINGSHANG = hero_skill_id("caopi_mobile", "xingshang")
"""曹丕移动版【行殇】的全限定技能 id（`hero.<武将 id>.<短 id>` 的既有派生）。"""

XINGSHANG_REQUIREMENT = "当其他角色死亡时，你可以获得其所有的牌"
"""一句话文案 = 移动版牌面逐字（票面 §0.1 第 1 行：只多一个「的」）。"""

_XINGSHANG_GROUPS_KEY = "xingshang_groups"
"""帧私有：**定格**的搬牌分组 ``((来源区, 牌 id 元组), …)``（手牌先、装备区后）。"""

_XINGSHANG_MOVED_KEY = "xingshang_moved"
"""帧私有：已搬完的分组数（重入 / 宿主级直调都从这里续，不重搬）。"""

_XINGSHANG_DONE_KEY = "xingshang_done"
"""帧私有：这一次死亡已经结算过本技能（防御位；G8「防宿主级直调」）。"""


def _xingshang_condition(host: Any, frame: Any) -> bool:
    """触发条件：**死者不是持有者** ∧ 死者的手牌 / 装备区非空 ∧ 终局未判定。

    "其他角色"= 死者 ≠ 持有者（账本 L776②）；两区判据 = Q1 = A（只看手牌 + 装备区，判定区
    与武将牌上的牌不参与"有没有牌"的判断——否则会出现"只有判定区的牌也被问一次"的空询问）；
    终局位读宿主（`Engine.outcome_decided`），因为发动形态拿不到引擎（Q3 的实现代价）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    dead = frame.data.get("target")
    if dead is None or int(dead) == int(owner):
        return False
    player = host.state.player(int(dead))
    if not (player.hand or player.equip):
        return False
    return not host.outcome_decided()


def _xingshang(host: Any, frame: Any) -> Any:
    """获得死者的**手牌 + 装备区**里的所有牌：逐来源区一条 ``card_move``（不派发 MOVE_AFTER）。

    **分组定格 + 游标**（G7 / G9）：死者此刻已 ``alive=False``，牌只会被系统清区继续搬走，
    故"这一刻有哪几张"必须在首次进入时定格在帧私有里；``xingshang_moved`` 记已搬完的分组，
    重入 / 宿主级直调都从这里续、不重搬。搬牌**不走**移动入口（Q2）：失去牌的是死者
    ⇒ `move_card` + `emit_frame_delta`，一条「失去牌后」都不派发，故正常路径上不会挂起。

    区序 = 手牌先、装备区后（与 :meth:`~sgs.engine.Engine.discard_dead_cards` 的区序一致）；
    `MoveReason.PUT` + `owner = 获得者` 与【归心】/【天妒】的"获得"同一条记录口径。
    """
    owner = int(frame.data["skill_owner"])
    if frame.data.get(_XINGSHANG_DONE_KEY):
        return None
    dead = int(frame.data["target"])
    groups = frame.data.get(_XINGSHANG_GROUPS_KEY)
    if groups is None:
        found: list[tuple[ZoneRef, tuple[int, ...]]] = []
        for source_ref in (ZoneRef(Zone.HAND, dead), ZoneRef(Zone.EQUIP, dead)):
            card_ids = tuple(host.state.zone(source_ref))
            if card_ids:
                found.append((source_ref, card_ids))
        groups = tuple(found)
        frame.data[_XINGSHANG_GROUPS_KEY] = groups
    moved = int(frame.data.get(_XINGSHANG_MOVED_KEY, 0))
    while moved < len(groups):
        _source_ref, card_ids = groups[moved]
        # 结算主体与 `Engine._effect_context(frame, actor=owner)` 同形：承载帧在死亡帧上，
        # 故 delta 的坐标与系统清区那条同帧（回放按同一条死亡结算重建）。
        ctx = EffectContext(
            actor=owner,
            source=frame.data.get("source"),
            targets=tuple(frame.data.get("targets", ())),
            frame_id=frame.frame_id,
            depth=host.stack.depth,
        )
        delta = move_card(
            host.state,
            ctx,
            host.state.refs(card_ids),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
        )
        host.emit_frame_delta(frame, delta)
        moved += 1
        frame.data[_XINGSHANG_MOVED_KEY] = moved
    frame.data[_XINGSHANG_DONE_KEY] = True
    return None


XINGSHANG_FLOW = SkillFlow(name="xingshang", steps=(), effect=zero_step_effect(_xingshang))
"""【行殇】的零步流水线（`shapes.md` §2 第 44 行）：[是否发动] → 拿走死者的两区牌。

必须是**模块级具名常量**：`tests/test_ask_points.py` 只发现模块级 `SkillFlow` 实例。
"""


def death_after_skill_definitions() -> tuple[SkillDef, ...]:
    """【武魂】一条定义：①逐点给标记（锁定）；②死亡时判定 / 致死（锁定）。

    【行殇】（lin-batch2 票 01）是第二条：`DEATH_ON` + 零步流水线（只问"是否发动"），
    非锁定、`carrier = HERO`；条件见 :func:`_xingshang_condition`（不关于"我"，故进
    `tests/test_hero_catalog.py` 的豁免表）。
    """
    return (
        SkillDef(
            skill_id=WUHUN,
            locked=True,
            carrier=CarrierKind.HERO,
            requirement=WUHUN_REQUIREMENT,
            step_requirements={"pick": "选择一名拥有最多「梦魇」标记的角色"},
            hooks=(
                Hook(
                    Timing.DAMAGE_AFTER_TAKE,
                    condition=_mark_condition,
                    handler=_mark,
                    per_point=True,
                ),
                Hook(
                    Timing.DEATH_ON,
                    condition=_death_condition,
                    handler=_wuhun_death,
                ),
            ),
        ),
        SkillDef(
            skill_id=XINGSHANG,
            locked=False,
            carrier=CarrierKind.HERO,
            requirement=XINGSHANG_REQUIREMENT,
            hooks=(
                Hook(
                    Timing.DEATH_ON,
                    condition=_xingshang_condition,
                    flow=XINGSHANG_FLOW,
                ),
            ),
        ),
    )
