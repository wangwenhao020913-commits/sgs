r"""响应窗口族（E，票 08）：所需张数 = 修饰器，不可响应 = 帧标签。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` / :mod:`sgs.skills.target_pool` /
:mod:`sgs.skills.move_after` 同构：一条 :class:`~sgs.skills.catalog.SkillDef` = 一个
``hero.<武将 id>.<短 id>`` 技能，定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

E 族两件都改"响应窗口"，但按 R5-Q4 的分界走两条路——**改一个值 ⇒ 修饰器；加一条禁令 /
标志 ⇒ 帧标签**：

=======================  ==========  ==========================================================
技能                      锁定        落点
=======================  ==========  ==========================================================
吕布【无双】              锁定        ``Modifier(RESPONSE_COUNT, 2)``（自己使用【杀】/【决斗】时）
马超【铁骑】              非锁定      ``Hook(USE_AFTER_DESIGNATE_TARGET)`` + 判定 + 帧标签 ``UNRESPONSIVE``
=======================  ==========  ==========================================================

【无双】（规则 1046）：``source == 自己`` 且牌名 ∈ {【杀】,【决斗】} ⇒ 本次响应窗口需要
**两张**——【杀】要连续两张【闪】才抵消，【决斗】每次响应要连续两张【杀】。两处"数数"都读
:func:`sgs.effects.response_count` 这一处真源，引擎里不出现技能名。

【铁骑】（规则 987，）：非锁定技，``USE_AFTER_DESIGNATE_TARGET`` 逐目标判定；红色 ⇒ 给**该目标**
打 :data:`~sgs.resolution.Tag.UNRESPONSIVE`（**逐目标档**）。这个标签只负责"跳过本窗口"
（【杀】跳过【闪】窗口、锦囊跳过无懈窗口是同一个标签的两个后果），窗口该不该存在仍由行为表的
``response_window`` 决定；"视为使用【闪】"（【八卦阵】）也因此一并被封——**视为使用仍是使用**。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.data import SkillType
from sgs.effects import CarrierKind, Modifier, ModifierContext, ModifierPoint
from sgs.resolution import Control, Frame, Tag, add_tag
from sgs.state import GameState
from sgs.timing import Timing

from .catalog import HeroSkillDecl, Hook, HookHandler, SkillDef
from .flow import FlowAnswers, SkillFlow

__all__ = ["RESPONSE_WINDOW_SKILLS", "TIEQI_FLOW", "response_window_skill_definitions"]


RESPONSE_COUNT_NEEDED = 2
"""【无双】把本次响应窗口所需张数改成 **2**（票 08 §要做什么 3）。"""

TIEQI_PENDING = "tieqi_pending"
"""帧私有键：本次【铁骑】的判定帧已压出（重入时据此读判定结果）。"""
TIEQI_CURSOR = "tieqi_cursor"
"""帧私有键：逐目标判定的游标（多目标【杀】里一个目标一次判定）。"""


# ---------------------------------------------------------------------------
# 吕布【无双】：RESPONSE_COUNT 修饰器
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResponseCountSkill(HeroSkillDecl):
    """E 族"改所需张数"一条技能的声明：牌名闭集 + 目标张数。"""

    card_types: tuple[str, ...]
    count: int = RESPONSE_COUNT_NEEDED

    def response_count(self, state: GameState, ctx: ModifierContext, current: Any) -> Any:
        """``RESPONSE_COUNT`` 回调：**自己使用**那张牌时把所需张数改成 ``count``。

        ``ctx.subject`` = 本次使用的来源（谁出的牌），``ctx.owner`` = 本实例的持有者，
        故"只有牌的使用者才改这个值"由一次相等判定表达；牌名读 ``ctx.card.type``。
        """
        del state
        if ctx.card is None or ctx.card.type not in self.card_types:
            return current
        if ctx.owner != ctx.subject:
            return current
        return self.count


WUSHUANG = ResponseCountSkill(
    hero_id="lvbu",
    short_id="wushuang",
    card_types=("slash", "duel"),
)
"""吕布【无双】：锁定技，使用【杀】/【决斗】时该角色需连续两张响应（规则 1046）。"""


# ---------------------------------------------------------------------------
# 马超【铁骑】：判定 + 逐目标帧标签
# ---------------------------------------------------------------------------


def _tieqi(host: Any, frame: Frame) -> Control:
    """【铁骑】的 ``USE_AFTER_DESIGNATE_TARGET`` 钩子：逐目标判定，红 ⇒ 给该目标打标签。

    多目标【杀】（【方天画戟】）里**逐个目标各判一次**——"指定一名角色为目标后"是逐目标的
    时机，而 :class:`~sgs.skills.catalog.Hook` 的一次派发带的是整张目标表，故用帧内游标
    （``tieqi_cursor``）逐个推进。判定的"红 / 黑"由 :meth:`~sgs.engine.Engine.note_skill_judgment`
    写回帧私有 ``skill_result``，本回调取出后**立刻弹掉**，避免它随 ``dict(frame.data)``
    流进 RESOLVE 帧、被【八卦阵】的【闪】窗口误当作自己的判定结果。
    """
    targets = tuple(frame.data.get("targets", ()))
    cursor = frame.data.get(TIEQI_CURSOR, 0)
    if cursor >= len(targets):
        return None
    if frame.data.pop(TIEQI_PENDING, False):
        if frame.data.pop("skill_result", False):
            add_tag(frame, Tag.UNRESPONSIVE, target=targets[cursor])
        frame.data[TIEQI_CURSOR] = cursor + 1
        return "stay"  # 下一轮：要么为下一个目标判定，要么在上面的守卫处收尾
    frame.data[TIEQI_PENDING] = True
    host.open_skill_judgment(frame, targets[cursor], TIEQI.skill_id)
    return "stay"


@dataclass(frozen=True, slots=True)
class FrameTagSkill(HeroSkillDecl):
    """E 族"加一条禁令"一条技能的声明：触发时机 + 标签取值。"""

    timing: Timing
    locked: bool
    handler: HookHandler


TIEQI = FrameTagSkill(
    hero_id="machao",
    short_id="tieqi",
    timing=Timing.USE_AFTER_DESIGNATE_TARGET,
    locked=False,
    handler=_tieqi,
)
"""马超【铁骑】：非锁定技，判定为红 ⇒ 该目标不能使用【闪】响应此【杀】（规则 987）。"""


def _tieqi_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Control:
    """【铁骑】的零步流水线效果（W6-01）：转调旧钩子回调 ``TIEQI.handler``。

    ``steps=()`` ⇒ ``answers`` 恒为空；逐目标判定、帧内游标（``tieqi_cursor``）与
    ``add_tag`` 都在旧回调里，一个字不动——这里只换执行路径。
    """
    del answers
    return TIEQI.handler(host, frame)


TIEQI_FLOW = SkillFlow(name="tieqi", steps=(), effect=_tieqi_effect)
"""【铁骑】的零步流水线（W6-01）：[是否发动] → 判定 + 逐目标打 ``UNRESPONSIVE``（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的 flow（先例 :data:`~sgs.skills.damage_after.FANKUI_FLOW`）。
"""


RESPONSE_WINDOW_SKILLS: tuple[ResponseCountSkill | FrameTagSkill, ...] = (WUSHUANG, TIEQI)
"""E 族两条技能的逐字声明（票 08 §要做什么 3 / 7）。"""

_REQUIREMENTS: dict[str, str] = {
    "wushuang": "使用【杀】/【决斗】时，响应者需要连续打出两张",
    "tieqi": "判定；红色则该目标不能使用【闪】响应此【杀】",
}
"""E 族两条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""


def response_window_skill_definitions() -> tuple[SkillDef, ...]:
    """E 族两条技能的 :class:`~sgs.skills.catalog.SkillDef`。

    【无双】锁定（技能面没有"可以"）⇒ ``locked=True``，落 ``RESPONSE_COUNT`` 修饰器；
    【铁骑】非锁定（"你可以进行判定"）⇒ ``locked=False``，落 ``USE_AFTER_DESIGNATE_TARGET``
    钩子。两条 ``carrier = HERO`` ⇒ 优先级档由载体派生；``skill_type = NORMAL``。
    """
    return (
        SkillDef(
            skill_id=WUSHUANG.skill_id,
            locked=True,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.RESPONSE_COUNT, WUSHUANG.response_count),),
            requirement=_REQUIREMENTS["wushuang"],
        ),
        SkillDef(
            skill_id=TIEQI.skill_id,
            locked=TIEQI.locked,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(TIEQI.timing, flow=TIEQI_FLOW),),
            requirement=_REQUIREMENTS["tieqi"],
        ),
    )
