r"""G 族：授予可见性与排列（票 10；W6-02 迁步骤流水线）。

一件今天完全不存在的能力：**把牌堆顶的牌面授予某一次询问**。`04`（§2「信息可见性」）明写
"牌堆顺序仅引擎（技能'观看牌堆顶'按效果逐张授予）"，而授予通道一直只有 ``PlayerView.deck_count``
这一个读点——本模块是那条通道的**第一个消费者**，也是它保持**通用**的理由。

**授予通道**住协议层（``_QueryBase.revealed``）：本次询问授予该参与者的牌面，随询问结束即
失效，不写状态、不进 ``PlayerView``、不泄露给其它座位。引擎侧的两个入口
（``Engine.peek_deck`` = 观看、``Engine.ask_query(..., revealed=...)`` = 授予）都**不认识
任何技能 id**；本模块只调它们。故将来的【遗计】（只看不排，票 05）与【观星】（看后排）
共用同一条，而不是各造一条。

**诸葛亮【观星】**（规则 991）：

    准备阶段（回合开始阶段）开始时，你可以观看牌堆顶的 X 张牌
    （X 为存活角色数且至多为 5），然后将其中任意数量的牌以任意顺序置于牌堆顶，
    其余以任意顺序置于牌堆底。

- **挂阶段体** ``PHASE_PREPARE``（不是 ``PHASE_PREPARE_START``——后者在 ``EMPTY_LOG``，
  R6-Q1 裁定不解封）；挂点由 :func:`arrange_skill_definitions` 交给引擎的通用阶段体派发。
- **X = min(存活角色数, 5)**：存活角色数是公开信息，不额外泄露。
- **非锁定**：技能面写了"你可以"，故先发"是否发动"询问；答 ``ActivateSkill`` 才开排列询问。
- **放置口径**：``ArrangeCards`` 的 ``order`` 自顶向下、``split`` = 前几张置顶；"置底"
  = 接在**原有牌堆底之下**（`effects.place_on_deck` 的 ``to_top=False``）。

牌面只出现在那次 ``ARRANGE`` 询问的 ``revealed`` 里；排列本身在记录流里只以
``ArrangeCards`` 的**面内位置**出现，不另发带牌面的 delta（票 10 §验收 2 / 用户故事 47）。

**W6-02 的执行形状**（纯重构，记录流逐字节不变）：

- 可复用的**牌堆顶排列**规格 :class:`~sgs.skills.flow.DeckTopArrange` 住 ``flow`` 模块；
  观星声明 :data:`GUANXING_ARRANGE`（X = min(存活数, 5)、自由切分），**同一个对象**既被
  :data:`GUANXING_FLOW` 的 ``Arrange`` 步引用、也被 ``SkillDef.deck_arrange`` 引用
  （一份声明，票 03 的投影读后者）。
- 看牌 / 排列 / 放回从手写回调改成一条**模块级**流水线：``Arrange`` 步看牌并问排列，
  effect 只负责放回（旧 :func:`_apply_arrangement`）。
- **看的牌必须只由首跑定格**（spec B3）：``run_flow`` 在挂起后从头重跑步骤，而
  ``Engine.peek_deck`` 在牌堆不够时会重洗并落一条 ``deck_shuffle``。缓存由 ``Arrange``
  步自己维护（``flow.ARRANGE_CARDS_KEY``，帧私有、不进记录流），重入读缓存。
- 放回用 ``guanxing_done`` 守卫（旧 ``_DONE_KEY``）：effect 可能随 ``run_flow`` 重入，
  置牌只能发生一次。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, place_on_deck
from sgs.resolution import Frame
from sgs.state import ArrangeCards, CardRef, GameState, Phase, alive_seats
from sgs.timing import Timing

from .catalog import (
    ActivationPlan,
    Hook,
    SkillDef,
    mark_skill_used_this_turn,
    skill_used_this_turn,
)
from .flow import (
    Arrange,
    DeckTopArrange,
    FlowAnswers,
    SkillFlow,
    watched_cards,
)

__all__ = [
    "GUANXING",
    "GUANXING_ARRANGE",
    "GUANXING_FLOW",
    "GUANXING_MAX",
    "JIE_GUANXING",
    "JIE_GUANXING_ARRANGE",
    "JIE_GUANXING_FLOW",
    "JIE_GUANXING_FULL",
    "JIE_GUANXING_SMALL",
    "arrange_activation_plans",
    "arrange_skill_definitions",
]

GUANXING = hero_skill_id("zhugeliang", "guanxing")
"""诸葛亮【观星】的全限定技能 id（前缀派生走 :func:`sgs.data.hero_skill_id`）。"""

GUANXING_MAX = 5
"""观星的 X 封顶（规则 991 的"至多为 5"），是**技能的参数**、不是排列类型的字段。"""

_CARDS_STEP = "cards"
"""``Arrange`` 步的步骤键（效果读 ``answers`` 的键，也是 :func:`watched_cards` 的定位）。"""

_DONE_KEY = "guanxing_done"
"""放回完成的帧私有幂等位（旧 ``_DONE_KEY``）：``run_flow`` 重入时不得重复放回。"""


def _guanxing_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """观星的发动前提：**技能持有者就是当前回合角色**。

    只有自己的准备阶段才谈得上"准备阶段开始时"。``available=False`` ⇒ 该实例不进候选、
    连"是否发动"都不问（票 19 §6 的姿态）——区别于"有这条技能但这次不发动"。
    读 ``instance`` 而不是 ``frame.data["skill_owner"]``，因为**条件 / 形态在派发器写
    ``skill_owner`` 之前求值**（``_matching_hooks`` 先于那行）。
    """
    del frame
    return ActivationPlan(available=instance.owner == state.turn_seat)


def arrange_activation_plans() -> dict[str, Any]:
    """G 族的发动形态注册表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。"""
    return {GUANXING: _guanxing_plan, JIE_GUANXING: _jie_guanxing_plan}


def _guanxing_count(state: GameState, frame: Frame) -> int:
    """观星的 X = ``min(存活角色数, 5)``（规则 991）。``frame`` 只是规格的统一签名，不用。"""
    del frame
    return min(len(alive_seats(state)), GUANXING_MAX)


GUANXING_ARRANGE = DeckTopArrange(count=_guanxing_count)
"""观星的牌堆顶排列规格（W6-02）：X = min(存活数, 5)、**自由切分**。

同一个对象同时被 :data:`GUANXING_FLOW` 的 ``Arrange`` 步与
``SkillDef.deck_arrange`` 引用——执行器与接入层读的是**同一份声明**，不复制。
"""


def _apply_arrangement(
    state: GameState, cards: tuple[CardRef, ...], order: tuple[int, ...], split: int
) -> None:
    """按 ``order`` / ``split`` 把被授予的牌放回：顶段按序置顶、底段按序接在牌堆底之下。"""
    top = tuple(cards[index] for index in order[:split])
    bottom = tuple(cards[index] for index in order[split:])
    place_on_deck(state, top, to_top=True)
    place_on_deck(state, bottom, to_top=False)


def _guanxing_effect(host: Any, frame: Frame, answers: FlowAnswers) -> None:
    """【观星】的流水线效果（W6-02）：按答案里的 ``ArrangeCards`` 把牌放回牌堆。

    答案形状 = ``ArrangeCards`` 本身（``order`` + ``split``）；面内位置 → 实体牌读
    ``Arrange`` 步首跑时存下的牌面（:func:`watched_cards`），不二次看牌（spec B3）。

    ``_DONE_KEY`` 短路与迁移前逐字同源：``run_flow`` 的续跑会重入 effect，置牌只做一次。
    """
    if frame.data.get(_DONE_KEY):
        return None
    action = answers[_CARDS_STEP]
    assert isinstance(action, ArrangeCards)  # 候选只有 ArrangeCards，成员判定已保证
    _apply_arrangement(
        host.state,
        watched_cards(frame, GUANXING_FLOW, _CARDS_STEP),
        action.order,
        action.split,
    )
    frame.data[_DONE_KEY] = True
    return None


GUANXING_FLOW = SkillFlow(
    name="guanxing",
    steps=(Arrange(key=_CARDS_STEP, spec=GUANXING_ARRANGE),),
    effect=_guanxing_effect,
)
"""【观星】的一步流水线（W6-02）：排列（自由切分）→ 按答案放回牌堆顶 / 牌堆底。

**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow``，并要求它被
某个 ``Hook.flow`` 登记、``name`` 两两不同。唯一的步骤是 :class:`Arrange` ⇒
``manifest`` 对这条流水线返回空元组（排列无法用 ``Step.pool`` 表达），故观星不翻预发动。
"""


# ---------------------------------------------------------------------------
# 界诸葛亮【观星】（M6 票 16）：五张（存活 < 4 时三张）+ 全置底 ⇒ 结束阶段再次观星
#
# §十九 L2113–L2116：
#
#     观星：准备阶段，你可以观看牌堆顶的五张牌（当存活角色数小于 4 时改为三张），
#           然后将这些牌以任意顺序置于牌堆顶或牌堆底。若你将这些牌均置于牌堆底，
#           你可以于结束阶段再次"观星"。
#
# 与标准【观星】的三处差异（其余全部复用：同一条 ``Arrange`` 执行器、同一份「顶段 /
# 底段」放回原语、同一条自由切分）：
#
# - **张数**：标准版是 ``min(存活数, 5)``；界版是"存活 < 4 ⇒ 3、否则 5"（4 人局是 5 张，
#   比标准版多一张）；
# - **第二次观星**：准备阶段的观星**全部置底**（``ArrangeCards.split == 0``）时，本回合
#   的结束阶段再挂一次同一条流水线。判据住「本回合已用位」（``_TURN_USED``）：它恰是
#   "本回合"的作用域，回合末复位 ⇒ 不需要新状态字段；
# - **第二次的张数重算**：``Arrange`` 步在**每一次**询问时现读存活角色数（`06`（§5「程序化约定与偏离一手」） 的裁定），
#   故两次观星之间有人阵亡时第二次按新的存活数看牌。
# ---------------------------------------------------------------------------

JIE_GUANXING = hero_skill_id("jie_zhugeliang", "guanxing")
"""界诸葛亮【观星】的全限定技能 id（§十九 L2113–L2116）。"""

JIE_GUANXING_FULL = 5
"""存活角色数 ≥ 4 时的观星张数（§十九 L2114）。"""

JIE_GUANXING_SMALL = 3
"""存活角色数 < 4 时的观星张数（§十九 L2114–L2115）。"""

JIE_GUANXING_SMALL_ALIVE = 4
"""「存活角色数小于 4」那个阈值的字面值（与竞品无关，是牌面事实）。"""

_JIE_DONE_KEY = "jie_guanxing_done"
"""放回完成的帧私有幂等位（与标准版同姿态：续跑不重复放回）。"""


def _jie_guanxing_count(state: GameState, frame: Frame) -> int:
    """界观星的 X：存活 < 4 ⇒ 3，否则 5（§十九 L2114–L2115）。

    ``frame`` 只是 :class:`DeckTopArrange` 的统一签名，不用；每次 ``Arrange`` 步执行时
    现算，故**结束阶段的第二次观星**按那一刻的存活角色数重算（`06`（§5「程序化约定与偏离一手」） 的裁定）。
    """
    del frame
    alive = len(alive_seats(state))
    return JIE_GUANXING_SMALL if alive < JIE_GUANXING_SMALL_ALIVE else JIE_GUANXING_FULL


JIE_GUANXING_ARRANGE = DeckTopArrange(count=_jie_guanxing_count)
"""界观星的牌堆顶排列规格：张数按存活数 3 / 5、**自由切分**（可全部置底）。"""


def _jie_guanxing_effect(host: Any, frame: Frame, answers: FlowAnswers) -> None:
    """按 ``ArrangeCards`` 放回；**全部置底**时记「本回合已用位」供结束阶段再观星。

    读的是答案里的 ``split``（``0`` = 全部置底）。位走
    :func:`~sgs.skills.catalog.mark_skill_used_this_turn`：它的作用域就是"本回合"，
    回合末复位——与"准备阶段全部置底才可以在**本回合**结束阶段再来一次"同寿命。
    """
    if frame.data.get(_JIE_DONE_KEY):
        return None
    action = answers[_CARDS_STEP]
    assert isinstance(action, ArrangeCards)  # 候选只有 ArrangeCards，成员判定已保证
    _apply_arrangement(
        host.state,
        watched_cards(frame, JIE_GUANXING_FLOW, _CARDS_STEP),
        action.order,
        action.split,
    )
    if action.split == 0:
        mark_skill_used_this_turn(
            host.state, frame.data["skill_owner"], JIE_GUANXING
        )
    frame.data[_JIE_DONE_KEY] = True
    return None


JIE_GUANXING_FLOW = SkillFlow(
    name="jie_guanxing",
    steps=(Arrange(key=_CARDS_STEP, spec=JIE_GUANXING_ARRANGE),),
    effect=_jie_guanxing_effect,
)
"""界【观星】的一步流水线：排列（张数按存活数 3 / 5，自由切分）→ 放回。"""


def _jie_guanxing_plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
    """界观星的发动前提：自己的准备阶段恒可；自己的结束阶段只在**本回合全部置底**过时可。

    两个时机共用同一个 ``SkillDef``（同一张武将牌上的一条技能），故形态按
    ``state.phase`` 分流：准备阶段是普通观星；结束阶段是"再次观星"，前提读本回合已用位
    （准备阶段那次全部置底时由效果落位，`:func:`_jie_guanxing_effect`）。
    """
    del frame
    if instance.owner != state.turn_seat:
        return ActivationPlan(available=False)
    if state.phase is Phase.FINISH:
        return ActivationPlan(
            available=skill_used_this_turn(state, instance.owner, JIE_GUANXING)
        )
    return ActivationPlan()


def arrange_skill_definitions() -> tuple[SkillDef, ...]:
    """G 族的技能定义（【观星】标准 + 界两版；【遗计】归票 05 并复用同一通道）。

    非锁定（技能面写了"你可以"）、``carrier = HERO``（优先级档由载体派生）、
    ``skill_type = NORMAL``（不是主公技）。``deck_arrange`` 与流水线里的 ``Arrange`` 步
    引用**同一个**对象（票 03 的投影读它）。
    """
    return (
        SkillDef(
            skill_id=GUANXING,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.PHASE_PREPARE, flow=GUANXING_FLOW),),
            deck_arrange=GUANXING_ARRANGE,
            requirement="观看牌堆顶若干张牌，以任意顺序放回牌堆顶 / 牌堆底",
        ),
        SkillDef(
            skill_id=JIE_GUANXING,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(Timing.PHASE_PREPARE, flow=JIE_GUANXING_FLOW),
                Hook(Timing.PHASE_FINISH, flow=JIE_GUANXING_FLOW),
            ),
            deck_arrange=JIE_GUANXING_ARRANGE,
            requirement=(
                "观看牌堆顶五张牌（存活角色数小于 4 时改为三张），以任意顺序置于牌堆顶 / "
                "牌堆底；若均置于牌堆底，可以于结束阶段再次观星"
            ),
        ),
    )
