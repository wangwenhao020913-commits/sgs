r"""I 族：改判与判定牌（票 11）。

两件技能都挂在判定帧的 L14 时点上，但落点不同——**能力面 vs 生效后**：

=======================  ==========  ==========================================================
技能                      锁定        落点
=======================  ==========  ==========================================================
司马懿【鬼才】            非锁定      ``SkillDef.rejudge_capable``（判定帧的**改判循环窗口**把它认出来）
郭嘉【天妒】              非锁定      ``Hook(JUDGE_CARD_AFTER_EFFECT)``：获得此判定牌
=======================  ==========  ==========================================================

**改判窗口是引擎侧的通用循环**（票 11 §②）：``JUDGE_CARD_BEFORE_EFFECT`` 上，引擎用
``RESPOND`` + ``WindowCause.REJUDGE``（``required=None``、``mode=PLAY``）逐个问有改判
能力的座位；候选区域由该座位的技能定义派生（``rejudge_zones``：标准版"一张**手牌**"、
界版"一**张牌**"⇒ 手牌 ∪ 装备区，M6 票 14）。选中 ``PlayCard`` ⇒ 引擎按"**代替**"搬牌
（新牌 → 处理区、原判定牌 → 弃牌堆），窗口重开。故本模块的【鬼才】**没有钩子回调**——
它只给判定帧留下"我有这个能力"这一个定义字段（:attr:`~sgs.skills.catalog.SkillDef.rejudge_capable`），
引擎侧因此不出现任何具体技能 id（源码纪律 §5）。

【天妒】（规则 957）：``JUDGE_CARD_AFTER_EFFECT`` 上非锁定技，先问"是否发动"，答发动
则把**最终生效**的那张判定牌从处理区移入自己手牌（``PUT``）。已被获得 ⇒ 判定结算收尾
不再把它置入弃牌堆（规则 11.2"若未被【天妒】等获得"，判据是"牌还在不在处理区"）。
"当**你的**判定牌生效后"由发动形态的 ``available`` 表达——条件 / 形态在派发器写
``skill_owner`` 之前求值，故比的是 ``frame.data["subject"]`` 与 ``instance.owner``。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Color, SkillType, color_of_suit, hero_skill_id
from sgs.effects import CarrierKind
from sgs.resolution import Control, Frame
from sgs.state import GameState, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import ActivationPlan, Hook, RejudgeOriginal, SkillDef, same_kingdom
from .flow import SkillFlow, zero_step_effect

__all__ = [
    "GUIDAO",
    "GUICAI",
    "JIE_GUICAI",
    "JIE_TIANDU",
    "SONGWEI",
    "SONGWEI_FLOW",
    "SONGWEI_REQUIREMENT",
    "TIANDU",
    "TIANDU_FLOW",
    "rejudge_activation_plans",
    "rejudge_skill_definitions",
    "songwei_handler",
    "tiandu_handler",
]

GUICAI = hero_skill_id("simayi", "guicai")
"""司马懿【鬼才】的全限定技能 id（前缀派生走 :func:`sgs.data.hero_skill_id`）。"""

JIE_GUICAI = hero_skill_id("jie_simayi", "guicai")
"""界司马懿【鬼才】的全限定技能 id（§十九 L2037：「当一名角色的判定牌生效前，你可以打出
一张牌代替之」）。

与标准版（§九 L958「打出一张**手牌**代替之」）的**唯一牌面差别**是候选牌的来源范围：
界版是"一张牌"⇒ 手牌 ∪ 装备区（`参考资料/界限突破-界标准包武将资料.md`：
"鬼才由'一张手牌'放宽为'一张牌'"），落成 :attr:`~sgs.skills.catalog.SkillDef.rejudge_zones`。
改判能力仍只有 ``rejudge_capable`` 一个定义字段，座位集合由 :func:`rejudge_skill_ids`
机械派生；候选区由 :func:`~sgs.skills.rejudge_card_zones` 从**同一批存活实例**现算。
"""

GUIDAO = hero_skill_id("zhangjiao_mobile", "guidao")
"""张角移动版【鬼道】的全限定技能 id。

牌面（官方 OL 张角页 `/hero/212`，2026-11-02 抓取，逐字）：「当一张判定牌生效前，你可以用
一张**黑色**牌**替换**之。」与【鬼才】同一窗口、同一能力位，只多两处声明：

- ``rejudge_card_color = Color.BLACK``：候选只收黑桃 / 梅花（没有黑色牌 ⇒ 窗口不发这一问）；
- ``rejudge_original_to = RejudgeOriginal.HAND``：**替换**——被换下的原判定牌由**张角获得**
  （规则 §11.3 L1259–L1261；`参考资料/机制详解-官方规则集研究.md` L139），与【鬼才】的
  **代替**（原牌进弃牌堆）分列。

文本里的"用"字是官方现行口径（feng-zhangjiao 票 01 的 R6）：改判**不是打出**，走
``FrameKind.REJUDGE`` 事件帧（票 02）。
"""

TIANDU = hero_skill_id("guojia", "tiandu")
"""郭嘉【天妒】的全限定技能 id。"""

JIE_TIANDU = hero_skill_id("jie_guojia", "tiandu")
"""界郭嘉【天妒】的全限定技能 id（§十九 L2057：牌面与标准版逐字相同）。

票 15 的"复用"落点与界【鬼才】同姿态：**同一个** :data:`TIANDU_FLOW` 对象与**同一条**
发动形态（:func:`_tiandu_plan` 读判定帧的 ``subject``），只有技能 id 不同。
"""


def _tiandu_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """【天妒】的发动前提：**被判定者就是技能持有者**（"当**你的**判定牌生效后"）。

    ``available=False`` ⇒ 该实例不进候选、连"是否发动"都不问（票 19 §6 的姿态）。
    读 ``frame.data["subject"]``（判定帧的被判定者）而不是 ``skill_owner``，因为形态在
    派发器写 ``skill_owner`` **之前**求值（``_matching_hooks`` 先于那行）。
    """
    del state
    return ActivationPlan(available=frame.data.get("subject") == instance.owner)


def rejudge_activation_plans() -> dict[str, Any]:
    """I 族的发动形态注册表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。

    标准 / 界两版【天妒】共用同一个 ``_tiandu_plan`` 函数对象（"逐字相同"的落点）。
    【颂威】（lin-batch2 票 01）挂第三个键：它的钩子与 plan **同住本模块**（票面 §3.1 的
    第 4 条：plan 与 Hook 分住两个模块只会让下一个人两头找）。
    """
    return {TIANDU: _tiandu_plan, JIE_TIANDU: _tiandu_plan, SONGWEI: _songwei_plan}


def tiandu_handler(host: Any, frame: Frame) -> Control:
    """【天妒】的 ``JUDGE_CARD_AFTER_EFFECT`` 钩子：是否发动已由派发器问过；获得此判定牌。

    "牌还在处理区"既是**触发前提**也是**重入幂等**的守卫：被移走后再重入本回调不会
    重复搬牌；判定收尾据同一事实决定要不要入弃牌堆（规则 11.2）。
    """
    owner = frame.data["skill_owner"]
    card = frame.data.get("judge_card")
    if card is None or card.card_id not in host.state.processing:
        return None
    return host.move_in_frame(
        frame,
        (card,),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )


TIANDU_FLOW = SkillFlow(name="tiandu", steps=(), effect=zero_step_effect(tiandu_handler))
"""【天妒】的零步流水线（W6-01）：[是否发动] → 获得此判定牌（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的 flow（先例 :data:`~sgs.skills.damage_after.FANKUI_FLOW`）。
"""


# ---------------------------------------------------------------------------
# 林包·移动版·曹丕【颂威】（lin-batch2 票 01；用户 2026-11-08 签 Q5 / Q6 = A）
#
#     颂威
#     　主公技，当其他魏势力角色的黑色判定牌生效后，其可以令你摸一张牌。
#
# 三个落点（票面 §3 / §3.1 的取证）：
#
# - **钩子住判定族**：`JUDGE_CARD_AFTER_EFFECT`（账本 L949 的 ❷ 档，与【天妒】同格），
#   零步流水线（只问"是否发动"）；效果 = **技能持有者（主公）**摸一张。
# - **决定者 ≠ 受益者**：官方同一句里「其可以令你摸一张牌」——决定者是**被判定者**
#   （`frame.data["subject"]`），受益者是技能持有者。仓里同形的既有载体是界【护驾】② /
#   界【激将】②（`lord_request.LordEcho`），但它的 `hooks` 把时机与载荷写死成
#   `USE_ON_USE` + `frame.data["source"]` + `card_type`，三处都对不上判定帧
#   （当事角色是 `subject`、触发物是判定牌的颜色）⇒ **不直接复用**（给它加参数 = 改共享
#   原语）。可复用的是它的另一半：`ActivationPlan.actor`。故这里自带一个 plan，
#   `actor = 判定者`，效果里读 `skill_owner` 摸牌（`_lord_echo_draw` 的两条骨架）。
# - **频次 = 每次黑色判定一次**（Q5 = A）：三处一手都没有「每回合限一次」⇒ **不**调
#   `mark_skill_used_this_turn`（这是与 `_lord_echo_draw` 的唯一差别）；「可以」= 每次给
#   放弃出口（账本 L816）。
#
# 闸门放在 plan 的 `available` 里（照【天妒】），不写进 `condition`：颂威的判据是"这次
# 判定的当事者 / 颜色 / 势力"，不是"关不关于我"——写成 condition 会撞上
# `EXEMPT_OWNER_SELF_CHECK` 那张机械闸（票面 §3 边界 [2.4] H2）。
# ---------------------------------------------------------------------------

SONGWEI = hero_skill_id("caopi_mobile", "songwei")
"""曹丕移动版【颂威】的全限定技能 id（主公技）。"""

SONGWEI_REQUIREMENT = (
    "主公技，当其他魏势力角色的黑色判定牌生效后，其可以令你摸一张牌。"
)
"""一句话文案 = 移动版牌面逐字（票面 §0.1 第 3 行：移动版写「其」，与 OL / 用户文本同义）。"""

_SONGWEI_DRAWN_KEY = "songwei_drawn"
"""帧私有：这一张判定牌上已经摸过（续跑不重摸）。"""


def _songwei_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """【颂威】的发动形态：**判定者 ≠ 持有者** ∧ 同势力（魏）∧ 判定牌黑色。

    三个判据全落在 ``available`` 上（判定帧的 `subject` 是**被判定者**，不是 `source`；
    判定牌读 `judge_card`，改判之后就是**新牌**）。都不成立 ⇒ 实例不进候选、连"是否发动"
    都不问（票 19 §6 的姿态）。

    ``actor = subject``：这一问**发给判定者**（决定者 ≠ 受益者；`skill_owner` 仍是曹丕）。
    这与界【护驾】② 的 `lord_echo_activation_plan` 是同一条既有机制，不是为颂威新造的
    形状（界【救援】的 `_jie_jiuyuan_plan` 也按帧现算 actor）。
    """
    subject = frame.data.get("subject")
    owner = int(instance.owner)
    if subject is None or int(subject) == owner:
        # 判定牌不属于任何角色（规则 §11.6），但**被判定者**是「其 / 该角色」的先行词。
        return ActivationPlan(available=False)
    if not same_kingdom(state, int(subject), owner):
        return ActivationPlan(available=False)
    card = frame.data.get("judge_card")
    if card is None or color_of_suit(card.suit) is not Color.BLACK:
        return ActivationPlan(available=False)
    return ActivationPlan(actor=int(subject))


def songwei_handler(host: Any, frame: Frame) -> Control:
    """【颂威】的 ``JUDGE_CARD_AFTER_EFFECT`` 钩子：**技能持有者（主公）**摸一张。

    收益归 ``skill_owner``（曹丕），不是答话的判定者——照界【护驾】② 的
    :func:`~sgs.skills.lord_request._lord_echo_draw`。与它**唯一**的差别：颂威牌面没有
    「每回合限一次」⇒ **不**落"本回合已发动"位（票面 Q5 = A，实现时别顺手抄过来）。

    帧私有游标在**摸牌之前**落位：``draw_for_hand`` 不幂等，且会内联派发「获得牌后」
    （【清俭】一类）并可能挂起 —— 续跑既不再记一次、也不重摸。
    """
    if frame.data.get(_SONGWEI_DRAWN_KEY):
        return None
    frame.data[_SONGWEI_DRAWN_KEY] = True
    owner = int(frame.data["skill_owner"])
    if host.draw_for_hand(frame, owner, 1) == "stay":
        return "stay"
    return None


SONGWEI_FLOW = SkillFlow(name="songwei", steps=(), effect=zero_step_effect(songwei_handler))
"""【颂威】的零步流水线：判定者答"发动" → 主公摸一张（无步骤）。

必须是**模块级具名常量**（同 :data:`TIANDU_FLOW`：``tests/test_ask_points.py`` 只发现
模块级 ``SkillFlow`` 实例）。
"""


def rejudge_skill_definitions() -> tuple[SkillDef, ...]:
    """I 族三条技能的 :class:`~sgs.skills.catalog.SkillDef`（M6 票 14 加界【鬼才】）。

    【鬼才】（标准 / 界）非锁定（技能面写了"你可以"）但**不落钩子**——它把能力写在
    定义字段上，由引擎的通用改判窗口消费；【天妒】非锁定，落
    ``JUDGE_CARD_AFTER_EFFECT`` 钩子。三条 ``carrier = HERO``（优先级档由载体派生）、
    ``skill_type = NORMAL``。界【鬼才】与标准版只差**候选牌区域**：标准版 §九 L958
    "一张手牌" ⇒ 缺省 ``rejudge_zones=(HAND,)``；界版 §十九 L2037 "一张牌" ⇒ 手牌 ∪ 装备区。
    """
    return (
        SkillDef(
            skill_id=GUICAI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            rejudge_capable=True,
            requirement="用一张手牌，代替此判定牌",
        ),
        SkillDef(
            skill_id=JIE_GUICAI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            rejudge_capable=True,
            rejudge_zones=(Zone.HAND, Zone.EQUIP),
            requirement="用一张牌，代替此判定牌",
        ),
        SkillDef(
            skill_id=GUIDAO,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            rejudge_capable=True,
            rejudge_zones=(Zone.HAND, Zone.EQUIP),
            rejudge_card_color=Color.BLACK,
            rejudge_original_to=RejudgeOriginal.HAND,
            requirement="用一张黑色牌，替换此判定牌",
        ),
        SkillDef(
            skill_id=TIANDU,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.JUDGE_CARD_AFTER_EFFECT, flow=TIANDU_FLOW),),
            requirement="获得此判定牌",
        ),
        SkillDef(
            skill_id=JIE_TIANDU,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.JUDGE_CARD_AFTER_EFFECT, flow=TIANDU_FLOW),),
            requirement="获得此判定牌",
        ),
        # 曹丕移动版【颂威】（lin-batch2 票 01）：主公技 —— `skill_type = LORD` 是身份门控
        # 的唯一声明位（`_identity_gates_out` 读**定义**这一份；数据侧那条同名声明与它对账）。
        # 钩子**不带 condition**：闸门在 `_songwei_plan` 的 `available` 里（照【天妒】）。
        SkillDef(
            skill_id=SONGWEI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LORD,
            hooks=(Hook(Timing.JUDGE_CARD_AFTER_EFFECT, flow=SONGWEI_FLOW),),
            requirement=SONGWEI_REQUIREMENT,
        ),
    )
