r"""U 族：使用牌时触发（M6 票 02）——黄月英【集智】。

规则原文：§九 L997「当你使用一张非延时类锦囊牌时，你可以摸一张牌」；界版（§十九
L2104–L2105）追加「若此牌为基本牌，你可以弃置此牌，令你本回合的手牌上限 +1」——本模块
经 :data:`JIE_JIZHI` 落地（M6 票 08）。**"此牌" = 集智摸的那一张**（spec D20）：规则书
L2199 的注记把"此牌"读成被使用的锦囊、与前半句"非延时类锦囊"冲突，是误读（`06`（§5「程序化约定与偏离一手」） 登记）。
票面把"非转化"写进本仓口径，故牌面判据有**两个独立的排除**：

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
from sgs.state import CardRef, GameState, MoveReason, Pass, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "JIE_JIZHI",
    "JIE_JIZHI_FLOW",
    "JIZHI",
    "JIZHI_FLOW",
    "use_after_skill_definitions",
]

JIZHI = hero_skill_id("huangyueying", "jizhi")
"""黄月英【集智】的全限定技能 id。"""

JIE_JIZHI = hero_skill_id("jie_huangyueying", "jizhi")
"""界黄月英【集智】的全限定技能 id（§十九 L2104–L2105：摸一张 + 基本牌的 +1 分支）。"""

_DRAWN_KEY = "jizhi_drawn"
"""帧私有：本帧已经因【集智】摸过牌（重入不再摸；不进记录流）。"""

_JIE_DRAWN_KEY = "jie_jizhi_drawn"
"""帧私有：界【集智】本帧已经摸过那张牌。"""

_JIE_CARD_KEY = "jie_jizhi_card"
"""帧私有：界【集智】摸到的那张牌的 ``card_id``（"此牌" = 这一张，D20）。"""

_JIE_CHOICE_KEY = "jie_jizhi_choice"
"""帧私有：界【集智】"要不要弃置它换 +1"的应答（重入不重问）。"""

_JIE_DISCARDED_KEY = "jie_jizhi_discarded"
"""帧私有：这张牌已经弃置（搬牌可能内联挂起，重入不重搬）。"""

_JIE_BONUS_KEY = "jie_jizhi_bonus"
"""帧私有：+1 已经落过（搬牌挂起后的续跑不重复加）。"""

_JIE_LIMIT_BONUS = 1
"""界【集智】弃置基本牌换来的手牌上限增量。"""

_JIE_LIMIT_ASK = "jie_jizhi:limit"
"""界【集智】"是否弃置此牌换 +1"的询问标识（选项闭集 = 一个 ``discard`` + ``Pass``）。"""

_JIE_LIMIT_OPTION = "discard"
"""界【集智】那个唯一选项的取值。"""


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


def _jie_jizhi(host: Any, frame: Frame) -> Control:
    """界【集智】：摸一张；若**摸到的**那张是基本牌，可以弃置它换本回合手牌上限 +1。

    三段、每段一个帧私有游标（与【刚烈】同一范式：搬牌 / 询问都可能挂起，重入必须既不
    重做也不漏做）：

    1. **摸一张**（与标准版同句）——记住摸到的牌：摸牌原语把牌追加进手牌尾，故画完之后
       的 ``hand[-1]`` 就是"此牌"（D20）。牌堆短摸 ⇒ 平局，技能到此为止；
    2. **第二个"可以"**：只有"此牌为**基本牌**"才发询问（``SELECT_OPTION``，可选 = 一条
       ``Pass``）；非基本牌连问都不问；
    3. **弃置 + 加式**：牌进弃牌堆（``DISCARD``），然后给持有者记 +1（
       :meth:`~sgs.engine.Engine.add_hand_limit_bonus`）。加式是 D9 的一次性效果 ⇒ 住座位、
       回合末过期、不随【集智】失去 / 失效撤回。
    """
    if frame.data.get(_JIE_BONUS_KEY):
        return None
    state = host.state
    owner = frame.data["skill_owner"]
    if not frame.data.get(_JIE_DRAWN_KEY):
        frame.data[_JIE_DRAWN_KEY] = True
        if not host.draw_for_hand(frame, owner, 1):
            return None
        frame.data[_JIE_CARD_KEY] = state.player(owner).hand[-1]
    drawn = state.card(int(frame.data[_JIE_CARD_KEY]))
    if card_category(state.pack_id, drawn.type) is not Category.BASIC:
        frame.data[_JIE_BONUS_KEY] = True
        return None
    answer = frame.data.get(_JIE_CHOICE_KEY)
    if answer is None:
        answer = host.ask_option(
            frame,
            owner,
            Timing.USE_ON_USE,
            skill_id=_JIE_LIMIT_ASK,
            options=(_JIE_LIMIT_OPTION,),
            optional=True,
        )
        if host.suspended(answer):
            return "stay"
        frame.data[_JIE_CHOICE_KEY] = answer
    if isinstance(answer, Pass):
        frame.data[_JIE_BONUS_KEY] = True
        return None
    if not frame.data.get(_JIE_DISCARDED_KEY):
        frame.data[_JIE_DISCARDED_KEY] = True
        if (
            host.move_in_frame(
                frame,
                (drawn,),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=owner,
                actor=owner,
            )
            == "stay"
        ):
            return "stay"
    frame.data[_JIE_BONUS_KEY] = True
    host.add_hand_limit_bonus(owner, _JIE_LIMIT_BONUS)
    return None


JIZHI_FLOW = SkillFlow(name="jizhi", steps=(), effect=zero_step_effect(_jizhi))
"""【集智】的零步流水线：[是否发动] → 摸一张牌（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的流水线。
"""


JIE_JIZHI_FLOW = SkillFlow(name="jie_jizhi", steps=(), effect=zero_step_effect(_jie_jizhi))
"""界【集智】的零步流水线：[是否发动] → 摸一张 →（基本牌时）可选弃置换 +1。"""


def use_after_skill_definitions() -> tuple[SkillDef, ...]:
    """U 族两条技能的 :class:`~sgs.skills.catalog.SkillDef`（非锁定、挂 ``USE_ON_USE``）。

    标准 / 界共用**同一条触发条件**（:func:`_jizhi_condition`）：两版前半句逐字相同，界版
    只多一个提交点里的分支，故条件不 fork（与【克己】/【英姿】的"界版只是另一个武将 id +
    一段差异"同一姿态）。
    """
    return (
        SkillDef(
            skill_id=JIZHI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.USE_ON_USE, condition=_jizhi_condition, flow=JIZHI_FLOW),),
            requirement="使用非转化的非延时锦囊牌时，摸一张牌",
        ),
        SkillDef(
            skill_id=JIE_JIZHI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(Timing.USE_ON_USE, condition=_jizhi_condition, flow=JIE_JIZHI_FLOW),
            ),
            requirement=(
                "使用非转化的非延时锦囊牌时摸一张牌；若此牌为基本牌，你可以弃置此牌，"
                "令你本回合的手牌上限 +1"
            ),
        ),
    )
