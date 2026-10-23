r"""U 族：使用牌时触发（M6 票 02）——黄月英【集智】。

规则原文：§九 L997「当你使用一张非延时类锦囊牌时，你可以摸一张牌」。票面把"非转化"写进
本仓口径（= 界黄月英同句的前半段；"+1 手牌上限"分支属界版、P2 票 08 才落），故本模块的
牌面判据有**两个独立的排除**：

- **延时类**（``behavior.is_delayed``：乐不思蜀 / 闪电 / 兵粮寸断）——它们不是"非延时类"；
- **转化牌**（``CardRef.is_virtual`` 且 ``virtual_sources`` 非空）——票面写"非转化"。
  纯虚拟牌（没有来源实体牌）**不**在此列：§十六 16.2 明写"视为使用"触发"当你使用牌时"
  类技能，票面的排除词也只是"转化"。P0 没有纯虚拟锦囊的生产者，故这条分支先按读法成立。

牌名大类读数据文件的 :func:`sgs.data.card_category`（唯一真源），不抄牌名白名单：将来
新增锦囊时这里不需要改。

**挂点 = ``USE_ON_USE``**（L4.2）：与"使用"同一帧，先于该牌的 C 段结算，故摸到的那张
排在牌自身效果摸到的牌之前（测试钉住手牌序）。**零步流水线**：非锁定技 ⇒ 派发环先问
"是否发动"，答发动才跑 effect；摸牌在帧私有标记下只做一次（`:data:`_DRAWN_KEY`，
`CODING_STANDARDS.md` §3：同一帧里后跑的其他钩子挂起会让本回调重入）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import CarrierKind, behavior_for
from sgs.resolution import Control, Frame
from sgs.state import CardRef, GameState
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "JIZHI",
    "JIZHI_FLOW",
    "use_after_skill_definitions",
]

JIZHI = hero_skill_id("huangyueying", "jizhi")
"""黄月英【集智】的全限定技能 id。"""

_DRAWN_KEY = "jizhi_drawn"
"""帧私有：本帧已经因【集智】摸过牌（重入不再摸；不进记录流）。"""


def _jizhi_eligible(state: GameState, card: CardRef | None) -> bool:
    """这张牌算不算"使用了一张**非转化**的非延时**锦囊**牌"（条件与回调共用的唯一判据）。

    三段都不读牌名白名单：大类读内容包、延时性读行为表、转化性读虚拟牌来源集合。
    """
    if card is None:
        return False
    if card.is_virtual and card.virtual_sources:
        return False
    if behavior_for(card).is_delayed:
        return False
    return card_category(state.pack_id, card.type) is Category.TRICK


def _jizhi_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**使用者是技能持有者本人**，且这次使用的牌过 :func:`_jizhi_eligible`。

    持有者读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环临时写入），
    使用者读 ``frame.data["source"]``（使用帧的既有载荷）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    return _jizhi_eligible(host.state, frame.data.get("card"))


def _jizhi(host: Any, frame: Frame) -> Control:
    """效果：摸一张牌（"是否发动"已由派发环问过）。

    帧私有 ``jizhi_drawn`` 挡住重入：同一使用帧上若另一条钩子提问挂起，``run_flow`` 会
    从头重跑 effect，没有这个守卫就会多摸。
    """
    if frame.data.get(_DRAWN_KEY):
        return None
    frame.data[_DRAWN_KEY] = True
    host.draw_for_hand(frame, frame.data["skill_owner"], 1)
    return None


JIZHI_FLOW = SkillFlow(name="jizhi", steps=(), effect=zero_step_effect(_jizhi))
"""【集智】的零步流水线：[是否发动] → 摸一张牌（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的流水线。
"""


def use_after_skill_definitions() -> tuple[SkillDef, ...]:
    """U 族一条技能的 :class:`~sgs.skills.catalog.SkillDef`（非锁定、挂 ``USE_ON_USE``）。"""
    return (
        SkillDef(
            skill_id=JIZHI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.USE_ON_USE, condition=_jizhi_condition, flow=JIZHI_FLOW),),
            requirement="使用非转化的非延时锦囊牌时，摸一张牌",
        ),
    )
