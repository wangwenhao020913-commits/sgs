r"""J 族：可重复的判定循环（M6 票 02）——甄姬【洛神】；界版见票 09。

规则原文：§九 L963–965（标准版）：

    准备阶段（回合开始阶段）开始时，你可以进行判定，若结果为黑色，你获得此牌，
    然后可以重复此流程，直到出现红色结果为止。

§十九 L2045–L2049（界甄姬，OL 增补）：

    洛神：准备阶段，你可以进行一次判定，若结果为黑色，你获得此牌并可以重复此流程
         （**以此法获得的牌不计入本回合的手牌上限**）。

两版**共用这一条循环**（同一段"以此法重复"的执行形态），差别只有界版在"获得"那一刻
给牌挂上 :data:`~sgs.data.CardTagType.LUOSHEN_HAND` 标签：标签与区域无关，故**先挂再搬**
（搬牌可能内联派发"失去牌后"而挂起，挂起后再补挂就会漏掉一张）。标签的生存期（回合末）、
随来源消失（失去 / 失效【洛神】）都由 ``sgs.data.CARD_TAG_TYPES`` 登记，本模块不另写清理。

**循环的读法由本票定死**（票面"已知的一处小扩容"）：

- **终止条件**两条：① 判定为红（"直到出现红色结果为止"，红色那一轮**不问**继续）；
  ② 黑色那一轮之后答"停止"（"**可以**重复"⇒ 每一轮都是一次可取消的选项）；
- **每轮是否重问**：`是否发动` 只在进入技能时问一次（派发环的 ``SKILL_ACTIVATION``，
  答案按帧缓存）；之后**每轮**问一次 `是否继续`，询问标识带轮号
  （``luoshen:repeat:<轮号>``）——同帧的选项答案按 ``(skill_id, actor)`` 缓存，
  不带轮号就会把第二轮读成第一轮的答案；
- **记录流怎么表达"又一轮"**：每轮压一个**独立的 JUDGE 帧**（自带 ``JUDGE_ON`` /
  判定牌的两条移动 / 改判窗口），于是"又一轮" = "又一组判定记录"；轮号只出现在选项
  询问的 ``skill_id`` 里。回放按同一串决策重跑，不需要额外状态。

**执行形态 = 手写的帧内游标循环**（不是 ``SkillFlow``）：步骤语言明确没有循环 / 每轮
提交（:class:`sgs.skills.flow.SkillFlow` 的边界条款），故与【刚烈】/【遗计】同姿态——
进度写在**发起帧的帧私有键**里（``luoshen_stage`` 等），每次挂起后续跑从顶部重入。

**判定写在阶段体帧上**：准备阶段是"帧外阶段体"（不压栈的 ``_turn_data`` 帧），故
:meth:`~sgs.engine.Engine.note_skill_judgment` 的 M6 票 02 扩展按
:data:`~sgs.skills.RUNNING_SKILL_KEY` 找它。**获得判定牌**发生在 JUDGE 帧结算完
（判定牌已按规则 11.2 进弃牌堆）之后，按"获得"（``PUT``）搬进手牌——若这张牌已被
【天妒】一类效果拿进手牌，则跳过搬运（不产生 ``hand → hand`` 的幻影移动）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import CardTagType, Color, SkillType, color_of_suit, hero_skill_id
from sgs.effects import CarrierKind, locate
from sgs.resolution import Control, Frame
from sgs.state import CardTag, GameState, MoveReason, Pass, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import (
    RUNNING_SKILL_KEY,
    SKILL_JUDGMENT_CARD_KEY,
    ActivationPlan,
    Hook,
    SkillDef,
)

__all__ = [
    "JIE_LUOSHEN",
    "LUOSHEN",
    "LUOSHEN_OPTIONS",
    "LUOSHEN_TAG_TYPE",
    "judgment_loop_activation_plans",
    "judgment_loop_skill_definitions",
]

LUOSHEN = hero_skill_id("zhenji", "luoshen")
"""甄姬【洛神】的全限定技能 id。"""

JIE_LUOSHEN = hero_skill_id("jie_zhenji", "luoshen")
"""界甄姬【洛神】的全限定技能 id（M6 票 09）：与标准版同一条循环 + 获得牌带标签。"""

LUOSHEN_TAG_TYPE = CardTagType.LUOSHEN_HAND
"""界【洛神】给"以此法获得的牌"挂的标签类型（登记表：``reader = HAND_COUNT`` /
``clear = TURN_END`` / ``disappears_with_source = True``）。"""

_CONTINUE = "repeat"

LUOSHEN_OPTIONS: tuple[str, ...] = (_CONTINUE,)
"""「是否继续」的选项闭集：只剩「继续」一项（B1）。

「停止」不再是一个 `SelectOption` 值，而是这条**可选**询问的 `Pass`——界面因此只画
「提交 = 继续 / 放弃 = 停止」两个出口，不画 repeat / stop 两个按钮。
"""

_STAGE_KEY = "luoshen_stage"
"""帧私有：循环停在哪个阶段——``"judge"``（判定帧已压出）/ ``"gain"``（该搬判定牌）/
``"confirm"``（该问「是否继续」）。"""

_ROUND_KEY = "luoshen_round"
"""帧私有：当前轮号（0 起；只用于选项询问标识，不进记录流的其它字段）。"""

_GAINED_KEY = "luoshen_gained_card"
"""帧私有：本轮待搬进手牌的判定牌（``CardRef``；搬完即弹掉）。"""

_DONE_KEY = "luoshen_done"
"""帧私有：循环已收尾（红色终止 / 答停止 / 已放弃）——重入直接返回。"""


def _luoshen_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """【洛神】的发动前提：**技能持有者就是当前回合角色**（"**你的**准备阶段"）。

    与【观星】/ 阶段体技能同一条判据：``available=False`` ⇒ 别人的准备阶段里这条实例
    连"是否发动"都不问。读 ``instance.owner`` 而不是帧键，因为形态在派发器写
    ``skill_owner`` 之前求值。
    """
    del frame
    return ActivationPlan(available=instance.owner == state.turn_seat)


def judgment_loop_activation_plans() -> dict[str, Any]:
    """J 族的发动形态注册表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。

    标准 / 界两版共用同一条"**你的**准备阶段"前提（牌面逐字相同的那半句）。
    """
    return {LUOSHEN: _luoshen_plan, JIE_LUOSHEN: _luoshen_plan}


def _judge_next_round(host: Any, frame: Frame, owner: int) -> Control:
    """压下一轮判定帧；``"stay"`` 让阶段体在判定帧出栈后原地重入。

    ``open_skill_judgment`` 把 ``skill_id`` + 发起帧 id 写进 JUDGE 帧，判定后果按它们把
    判定牌回写进本帧（阶段体帧的找法见 :meth:`Engine.note_skill_judgment`）。
    """
    frame.data[_STAGE_KEY] = "judge"
    # 判定帧记**正在跑的那个实例**（标准 / 界两个 skill id）：`note_skill_judgment` 拿它
    # 回找发起帧的 ``RUNNING_SKILL_KEY``，写死标准版 id 会让界版的判定后果找不到发起帧。
    host.open_skill_judgment(
        frame, owner, frame.data.get(RUNNING_SKILL_KEY, LUOSHEN)
    )
    return "stay"


def _luoshen(host: Any, frame: Frame) -> Control:
    """【洛神】的可重复判定循环（帧内游标，每轮可选继续 / 停止）。

    阶段：

    1. ``stage is None`` ⇒ 压第一轮判定；
    2. ``stage == "judge"`` ⇒ 判定帧已出栈：判**颜色**（读
       :data:`~sgs.skills.SKILL_JUDGMENT_CARD_KEY` 回写的判定牌，不是 ``skill_result``
       的"红 = 成功"布尔）；红 ⇒ 收尾；黑 ⇒ 记下这张牌并进入 ``gain``；
    3. ``stage == "gain"`` ⇒ 把判定牌从弃牌堆搬进手牌（``PUT``）。**先**把阶段改成
       ``confirm`` 再搬：搬牌可能内联派发"失去牌后"而挂起，续跑时不能重搬；
    4. ``stage == "confirm"`` ⇒ 问「是否继续」（问一次、答案帧内缓存）；答唯一选项 ⇒
       轮号 +1 并压下一轮判定；答 ``Pass`` ⇒ 收尾。

    返回 ``"stay"`` = 让出控制权（判定帧 / 搬牌钩子 / 选项询问）；``None`` = 循环结束。
    """
    owner = frame.data["skill_owner"]
    if frame.data.get(_DONE_KEY):
        return None
    stage = frame.data.get(_STAGE_KEY)
    if stage is None:
        return _judge_next_round(host, frame, owner)
    if stage == "judge":
        judge_card = frame.data.pop(SKILL_JUDGMENT_CARD_KEY, None)
        # 同帧的布尔结果只有本技能读（判定是刚开的、没有别人写），取完即弹。
        frame.data.pop("skill_result", None)
        if judge_card is None or color_of_suit(judge_card.suit) is not Color.BLACK:
            frame.data[_DONE_KEY] = True
            return None
        frame.data[_GAINED_KEY] = judge_card
        frame.data[_STAGE_KEY] = "gain"
        stage = "gain"
    if stage == "gain":
        card = frame.data[_GAINED_KEY]
        frame.data[_STAGE_KEY] = "confirm"
        # 判定牌此刻在弃牌堆（judge_finish 已收尾）；被【天妒】一类效果先拿走的则跳过，
        # 免得对同一张牌搬出 ``hand → hand`` 的幻影移动。
        if locate(host.state, card.card_id) != ZoneRef(Zone.HAND, owner):
            # 界版（票 09）：先挂标签、再搬牌——标签**与区域无关**，牌还在弃牌堆就能带；
            # 反过来"搬完再挂"会在搬牌内联派发"失去牌后"并挂起时丢掉这一张
            # （重入从 ``confirm`` 阶段续跑，不会再回到这里）。
            if frame.data.get(RUNNING_SKILL_KEY) == JIE_LUOSHEN:
                host.add_card_tag(
                    card.card_id,
                    CardTag(
                        tag_type=LUOSHEN_TAG_TYPE,
                        owner=owner,
                        skill_id=JIE_LUOSHEN,
                    ),
                )
            control = host.move_in_frame(
                frame,
                (card,),
                to_zone=ZoneRef(Zone.HAND, owner),
                reason=MoveReason.PUT,
                owner=owner,
                actor=owner,
            )
            if control == "stay":
                return "stay"
        frame.data.pop(_GAINED_KEY, None)
    round_no = int(frame.data.get(_ROUND_KEY, 0))
    answer = host.ask_option(
        frame,
        owner,
        Timing.PHASE_PREPARE,
        skill_id=f"luoshen:repeat:{round_no}",
        options=LUOSHEN_OPTIONS,
        # B1：可选询问 ⇒ 候选多一条 Pass；界面「放弃」= Pass = 停止。
        optional=True,
    )
    if host.suspended(answer):
        return "stay"
    if isinstance(answer, Pass):
        frame.data[_DONE_KEY] = True
        return None
    frame.data[_ROUND_KEY] = round_no + 1
    return _judge_next_round(host, frame, owner)


def judgment_loop_skill_definitions() -> tuple[SkillDef, ...]:
    """J 族两条技能的 :class:`~sgs.skills.catalog.SkillDef`（非锁定、手写多轮钩子）。

    标准 / 界两版是**两个技能 id、同一条循环**：界版多出的半句（获得牌不计入本回合的
    手牌上限）落在标签上，不是第二份循环实现。
    """
    return (
        SkillDef(
            skill_id=LUOSHEN,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.PHASE_PREPARE, _luoshen),),
            requirement="进行判定：黑色则获得此牌并可重复，直到出现红色",
            step_requirements={"repeat": "选择是否再进行一次【洛神】判定"},
        ),
        SkillDef(
            skill_id=JIE_LUOSHEN,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.PHASE_PREPARE, _luoshen),),
            requirement="进行判定：黑色则获得此牌并可重复，直到出现红色；以此法获得的牌不计入本回合的手牌上限",
            step_requirements={"repeat": "选择是否再进行一次【洛神】判定"},
        ),
    )
