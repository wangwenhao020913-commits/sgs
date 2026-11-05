r"""濒死窗口的武将技能（huo-mobile-batch 票 02）：庞统移动版【涅槃】。

牌面权威 = 用户给的 `D:\SGS_HEROS\神话再临-火包\庞统\技能.txt`（移动版，2026-11-02 签）：

> 涅槃　限定技，当你处于濒死状态时，你可以弃置你区域里所有的牌，复原武将牌，摸三张牌，
> 将体力值回复至3点。

**时机 = ``Timing.NEAR_DEATH_ENTER``**：它是 NEAR_DEATH 帧的第一步（``_emit_near_death``
在求桃窗口 ``NEAR_DEATH_AT`` 之前派发），故本技能在**任何人**的回合里都能触发，且四段效果
全部在求桃窗口之前结算完——体力回到 3 ⇒ ``near_death_ask`` 的 ``hp >= 1`` 早退、一问【桃】
都不发，死亡入口 ``near_death_after`` 也不压 DEATH 帧。这是引擎步骤表的结构事实，不是
本模块的约定（票 02 §2.5 的「顺序为什么重要」）。

四段效果（按牌面顺序，全部**不发询问**——"所有"没有选择面）：

1. **弃置你区域里所有的牌** = 手牌 + 装备区 + 判定区，各按来源区一条 ``move_card``
   （:func:`~sgs.skills.catalog._move_by_source`，与【贯石斧】成本、界【奇袭】同一实现）；
2. **复原武将牌** = ``set_flipped(False)`` + ``set_chained(False)`` 两次幂等写入口
   （库内没有合并的"复原"原语，也不该为一条技能新增——两个写入口都是既有公开方法）；
3. **摸三张** = ``draw_for_hand(frame, owner, 3)``（短摸判平局；挂起必须透传 ``"stay"``）；
4. **将体力值回复至 3 点** = ``RECOVER`` 子帧 ``amount = 3 - 当前体力``（与界张飞【替身】的
   "回复至体力上限"同一条构造：声明差额、帧内夹体力上限）。

**限定技整局一次**由派发环的 ``_LIMITED_USED`` 位承担（``skill_type = LIMITED``）：已用 ⇒
连候选都不产出、不问。**失效不影响已用位**（既有口径）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind
from sgs.resolution import Frame
from sgs.state import FrameKind, GameState, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef, _move_by_source
from .flow import FlowAnswers, SkillFlow

__all__ = [
    "NEIPAN",
    "NEIPAN_FLOW",
    "near_death_skill_definitions",
]

NEIPAN = hero_skill_id("pangtong_mobile", "niepan")
"""庞统移动版【涅槃】的全限定技能 id。"""

_NIEPAN_DRAW = 3
"""「摸三张牌」的张数。"""

_NIEPAN_RECOVER_TO = 3
"""「将体力值回复至3点」的**绝对目标**（不是回复 3 点）。"""

_NIEPAN_DRAWN = "niepan_drawn"
"""帧私有：那三张已经摸过（重入不再摸）。"""

_NIEPAN_RECOVERED = "niepan_recovered"
"""帧私有：回复子帧已经压过（重入不再压第二个）。"""

_NIEPAN_AREA = "niepan_area"
"""帧私有：**首次进入时定格**的"区域里所有的牌"清单（重入按它搬，不重读手牌区）。"""


def _niepan_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**濒死的就是持有者本人**（"当你处于濒死状态时"）。

    派发环对**每个存活实例**求条件，故必须自校 ``MATCH_OWNER_KEY == frame.data["target"]``
    （``MATCH_OWNER_KEY`` 由 :func:`~sgs.skills.catalog._matching_hooks` 在求值前写成
    ``instance.owner``）；少了这一条，谁濒死都会问庞统。
    """
    del host
    return frame.data.get(MATCH_OWNER_KEY) == frame.data.get("target")


def _niepan_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：弃置区域所有牌 → 复原 → 摸三张 → 回复至 3 点（四段按牌面顺序）。"""
    del answers
    owner = frame.data.get("skill_owner")
    if owner is None:
        return None
    # 弃置清单在**首次进入时定格**：本效果会重入（回复子帧 / 搬牌挂起），而重入时手牌区
    # 已经是"摸到的三张"——不定格就会把刚摸的牌也弃掉（探针实测）。
    cards = frame.data.get(_NIEPAN_AREA)
    if cards is None:
        player = host.state.player(owner)
        cards = (*player.hand, *player.equip, *player.judge)
        frame.data[_NIEPAN_AREA] = cards
    control = _move_by_source(
        host,
        frame,
        tuple(cards),
        to_zone=ZoneRef(Zone.DISCARD),
        owner=owner,
        key="niepan:area",
    )
    if control == "stay":
        return "stay"
    # 「复原武将牌」= 翻回正面 + 解除横置。两个写入口都幂等（状态没变 ⇒ 不落 delta）。
    host.set_flipped(frame, owner, False)
    host.set_chained(frame, owner, False)
    if not frame.data.get(_NIEPAN_DRAWN):
        # 先落位再摸：``draw_for_hand`` 的挂起是"移动已发生、钩子还在挂"，重入不能重摸。
        frame.data[_NIEPAN_DRAWN] = True
        control = host.draw_for_hand(frame, owner, _NIEPAN_DRAW)
        if control == "stay":
            return "stay"
        if control is False:  # 两堆皆空 ⇒ 已判平局
            return None
    if not frame.data.get(_NIEPAN_RECOVERED):
        frame.data[_NIEPAN_RECOVERED] = True
        amount = max(_NIEPAN_RECOVER_TO - host.state.player(owner).hp, 0)
        if amount:
            host.push_frame(
                FrameKind.RECOVER,
                {
                    "source": owner,
                    "targets": (owner,),
                    "card": None,
                    "use_timing": frame.data.get("use_timing"),
                    "amount": amount,
                },
                timing=Timing.RECOVER_VALUE,
            )
            return "stay"
    return None


NEIPAN_FLOW = SkillFlow(
    name="niepan",
    steps=(),
    effect=_niepan_effect,
)
"""【涅槃】的**零步**流水线：只问"是否发动"，四段效果全部住在 ``effect`` 里（无选择面）。"""


def near_death_skill_definitions() -> tuple[SkillDef, ...]:
    """濒死窗口技能的 :class:`SkillDef`（本模块当前只有【涅槃】一条）。

    ``locked=False``（"你可以" ⇒ 要问）、``skill_type = LIMITED``（整局一次）、
    ``carrier = HERO``。钩子挂 ``NEAR_DEATH_ENTER`` + ``flow`` + 持有者自校条件。
    """
    return (
        SkillDef(
            skill_id=NEIPAN,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LIMITED,
            hooks=(
                Hook(
                    Timing.NEAR_DEATH_ENTER,
                    flow=NEIPAN_FLOW,
                    condition=_niepan_condition,
                ),
            ),
            requirement=(
                "限定技，当你处于濒死状态时，你可以弃置你区域里所有的牌，复原武将牌，"
                "摸三张牌，将体力值回复至3点"
            ),
        ),
    )
