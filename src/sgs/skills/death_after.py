"""死亡时 / 标记族：神关羽【武魂】（票 04 / 票 06 / 票 07 / 票 10）。

牌面（用户 `技能.txt`，逐字）：

    武魂
    　锁定技，①当你受到其他角色造成的1点伤害后，你令该角色获得1枚“梦魇”标记；
    　②当你死亡时，你令拥有“梦魇”标记最多的一名角色进行一次判定，若结果不为【桃】或
    　　【桃园结义】，该角色死亡。

三条裁定（用户 Q6 / 本轮）：

- 标记 = `(名称, 来源 = 座位 + 全限定技能 id)` 的实例；同名同来源合并、异来源分列；
  「梦魇」无上限、无自身效果 / 说明，寿命到神关羽阵亡后；**武魂②只统计来源为自身**的
  梦魇（别人获得的武魂不统计神关羽的标记）。
- 死亡时技能由**死亡时技能窗口**派发（票 10）：`alive=False` 仍在死亡入口落，窗口在
  `DEATH_ON` 前打开、`DEATH_AFTER` 后关闭；武魂②因此在 `DEATH_ON` 跑得到。
- 武魂②的致死是**技能直接致死**（`Engine.pass_away`，票 07 V1）：无伤害来源 ⇒ 无 killer
  ⇒ 无死亡奖惩；不进濒死 / 求桃。游戏已判胜时整段跳过（票 07 V3 + BWiki Q&A）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import hero_skill_id
from sgs.state import Pass
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    SKILL_JUDGMENT_CARD_KEY,
    CarrierKind,
    Hook,
    SkillDef,
)

__all__ = [
    "MENGYAN",
    "WUHUN",
    "WUHUN_DEATH_ASK",
    "WUHUN_REQUIREMENT",
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
    """①：给伤害来源加 1 枚「梦魇」（来源 = 神关羽的武魂；寿命随来源身死）。"""
    owner = int(frame.data["skill_owner"])
    source = int(frame.data["source"])
    host.add_marker(
        frame,
        source,
        name=MENGYAN,
        source_seat=owner,
        source_skill_id=WUHUN,
        count=1,
        clear_on_source_death=True,
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


def death_after_skill_definitions() -> tuple[SkillDef, ...]:
    """【武魂】一条定义：①逐点给标记（锁定）；②死亡时判定 / 致死（锁定）。"""
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
    )
