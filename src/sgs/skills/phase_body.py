r"""L 族：阶段体触发与数值（票 12 登记；机制由票 10 的通用阶段体派发落地）。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` 等同构：一条
:class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，
定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

L 族的形态差别只有一处：它挂在**阶段体**（``PHASE_PREPARE`` / ``PHASE_DRAW`` /
``PHASE_FINISH`` …）上，而阶段体没有结算帧——票 10 的 ``Engine._dispatch_phase_body``
已经在"当前局面存在注册在该时机上的实例"时惰性建一个不压栈的持久帧，故本票**只需登记**
（票 10 §读法 4 明写【英姿】【闭月】因此是"照表登记"、不新增机制）：

=============  ========  =========================  ==========================
技能            类型       阶段                        效果
=============  ========  =========================  ==========================
周瑜【英姿】     非锁定    ``PHASE_DRAW``             摸牌阶段多摸一张（规则 1018）
貂蝉【闭月】     非锁定    ``PHASE_FINISH``           结束阶段摸一张（规则 1054）
张辽【突袭】     非锁定    ``PHASE_DRAW``             放弃摸牌，改为获得至多两名其他角色各一张手牌（§九 L964）
许褚【裸衣】     非锁定    ``PHASE_DRAW``             少摸一张；本回合【杀】/【决斗】伤害 +1（§九 L968）
吕蒙【克己】     非锁定    ``PHASE_DISCARD_START``    本回合未出【杀】⇒ 跳过弃牌阶段（§九 L1027）
界张飞【替身】   **限定**  ``PHASE_PREPARE``         体力回复至上限，摸以此法回复的体力值数张牌（§十九 L2095–L2097）
界李典【恂恂】   非锁定    ``PHASE_DRAW``             观看牌堆顶四张，两张置顶、其余置底（§十九 L2067–L2070）
=============  ========  =========================  ==========================

除【替身】外都是**非锁定技**（技能面写了"你可以"）⇒ 先发"是否发动"询问，答
``ActivateSkill`` 才执行；"技能持有者就是当前回合角色"这一前提由
:func:`phase_body_activation_plans` 的 ``available`` 表达（阶段体帧的 ``source`` 就是当前
回合角色），与【观星】同一姿态：``available=False`` ⇒ 别人的阶段里该实例连"是否发动"
都不问。【突袭】另有一条前提"至少一名其他角色有手牌"（没有可拿的牌就不该问），由
:attr:`PhaseBodySkill.available` 声明。

【替身】是 M6 的第一个**限定技**载体：整局只能发动一次，发动后由 :mod:`sgs.skills.catalog`
的派发环落"整局已用"位（与锁定 / 非锁定正交——它仍是非锁定，先问再执行）。

摸牌走 :meth:`~sgs.engine.Engine.draw_for_hand`（沿用 ``shuffle`` 子流、短摸判平局），
与摸牌阶段 / 奖惩摸牌同一口径；【突袭】/【裸衣】改的是**阶段的基础摸牌数**，经
:meth:`~sgs.engine.Engine.set_draw_phase_count` / ``reduce_draw_phase_by`` 交给
``_phase_draw`` 统一摸（而不是各画一份摸牌循环）。【英姿】/【闭月】/【裸衣】**不提问**，
走零步流水线、不让出控制权；【突袭】的座位步（``PickSeats``）会提问，走 ``SkillFlow``
的常规挂起 / 续跑；【替身】的回血先压 ``RECOVER`` 帧、出栈后才按"以此法回复的体力值"
摸牌（"然后"的严格次序，与【苦肉】的先失去体力再摸牌同形）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sgs.data import Category, SkillType, Suit, card_category, color_of_suit, hero_skill_id
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    apply_modifiers,
    behavior_for,
    is_slash,
    locate,
    place_on_deck,
)
from sgs.resolution import Control, Frame
from sgs.state import (
    FrameKind,
    GameState,
    MoveReason,
    SelectionSpec,
    Slot,
    SlotKind,
    Zone,
    ZoneRef,
    seats_from,
)
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    RUNNING_SKILL_KEY,
    SKILL_JUDGMENT_CARD_KEY,
    ActivationPlan,
    HeroSkillDecl,
    Hook,
    HookCondition,
    SkillDef,
    mark_round_skill_used,
    round_skill_uses,
)
from .flow import (
    Arrange,
    DeckTopArrange,
    FixedSplit,
    FlowAnswers,
    PickOption,
    PickSeats,
    SkillFlow,
    watched_cards,
    zero_step_effect,
)
from .virtual import pure_virtual_card, virtual_candidates

__all__ = [
    "BIYUE_FLOW",
    "BOTU_FLOW",
    "BOTU_ROUND_LIMIT",
    "JIE_BOTU",
    "JIE_LUOYI",
    "JIE_LUOYI_FLOW",
    "JIE_LUOYI_GAIN",
    "JIE_LUOYI_OPTIONS",
    "JIE_LUOYI_REVEAL",
    "JIE_LUOYI_SKIP",
    "JIE_TUXI",
    "JIE_TUXI_FLOW",
    "KEJI",
    "KEJI_FLOW",
    "LUOYI_DAMAGE_BONUS",
    "LUOYI_FLOW",
    "PHASE_BODY_SKILLS",
    "QINYIN",
    "QINYIN_FLOW",
    "QINYIN_LOSE",
    "QINYIN_RECOVER",
    "PhaseBodySkill",
    "SHELIE",
    "SHELIE_FLOW",
    "SHUANGXIONG",
    "SHUANGXIONG_FLOW",
    "SHENSU",
    "SHENSU_BOTH",
    "SHENSU_FLOW",
    "SHENSU_OPTIONS",
    "SHENSU_SKIP_JUDGE_DRAW",
    "SHENSU_SKIP_PLAY_EQUIP",
    "TISHEN",
    "TISHEN_FLOW",
    "TUXI_FLOW",
    "XUNXUN",
    "XUNXUN_ARRANGE",
    "XUNXUN_FLOW",
    "YINGZI_FLOW",
    "phase_body_skill_definitions",
]


@dataclass(frozen=True, slots=True)
class PhaseBodySkill(HeroSkillDecl):
    """L 族一条技能的**声明**：阶段体时机 + 摸牌数 + 执行路径。

    ``flow``（W6-01）：零步技能的 ``SkillFlow``（``steps=()``）；旧回调
    :meth:`handler` 保留，由 flow 的 ``effect`` 原样转调。
    ``modifiers``（M6 票 01）：这条技能随身带的修饰器（【裸衣】的伤害 +1）；
    ``available``（M6 票 01）：除"自己的阶段"外的额外前提（【突袭】：有牌可拿）；
    ``skill_type``（M6 票 05）：技能类型（缺省 ``NORMAL``；【替身】是 ``LIMITED``，
    派发环据此落"整局已用"位）。
    ``per_round_limit``（M6 票 11）：这条技能有没有「每轮限 X 次」（【博图】是唯一载体）；
    直接透进 :class:`~sgs.skills.catalog.SkillDef` 的同名字段，见那里的注释。
    """

    timing: Timing
    draws: int = 0
    flow: Any = None
    modifiers: tuple[Modifier, ...] = ()
    available: Callable[[GameState, int], bool] | None = None
    skill_type: SkillType = SkillType.NORMAL
    locked: bool = False
    """这条技能是不是**锁定技**（M6 票 08；缺省 ``False`` = 非锁定，先问"是否发动"）。

    界周瑜【英姿】是锁定技（§十九 L2168–L2170 牌面写"锁定技"），故它**不问**"是否发动"、
    满足条件即执行（派发环 ``forced = locked or AWAKENING``）。与 ``skill_type`` 正交：
    锁定 / 非锁定管"要不要问"，``skill_type`` 管技能规则类型（普通 / 限定 / 觉醒）。
    """
    condition: HookCondition | None = None
    """钩子的**触发条件**（huo-shenzhouyu-mobile 票 03 的加法；缺省 ``None`` = 无条件）。

    形状与 :data:`~sgs.skills.catalog.HookCondition` 同一份（``(host, frame) -> bool``）：
    条件为假 ⇒ 这条技能在本次时机里连"是否发动"都不问。它存在的理由是
    ``available`` 只拿得到 ``(state, owner)``，而【琴音】的触发条件要读**帧私有**事实
    （本弃牌阶段弃置的手牌数）——阶段体帧由 :meth:`Engine._dispatch_phase_body` 建，
    只有 ``condition`` 拿得到它。既有十六条声明不传 ⇒ ``Hook`` 的 condition 为
    ``None``（恒真），行为逐字节不变。
    """
    virtual_type_ids: tuple[str, ...] = ()
    """这条阶段体技能**能转化出哪些虚拟牌名**（huo-wolong-yanliang 票 02 的加法）。

    缺省空元组 ⇒ 既有十七条声明一字不改。非空时透进
    :attr:`~sgs.skills.catalog.SkillDef.virtual_type_ids`：转化清单的技能归属
    （:func:`~sgs.skills.heroes.owned_conversion_skill_ids` /
    ``_candidate_skill_ids``）读它，故带转化那一半的阶段体技能（【双雄】）必须声明。
    """

    virtual_source_count: int = 1
    """这条阶段体技能的虚拟牌**每次消耗几张来源牌**（缺省 1 = 单源转化）。"""

    per_round_limit: bool = False
    """这条技能有没有**每轮限 X 次**（M6 票 11；缺省 ``False`` ⇒ 既有九条声明一字不改）。

    ``True`` 时：本技能的发动次数记在 ``GameState.round_use_counts``（每轮复位），并让轮次
    变化进记录流（``skills.round_limit_present`` 的判据）。上限 X 由技能自己的
    :attr:`available` 每次重算（【博图】：X = 存活角色数且至多 3）。
    """
    requirement: str | None = None
    """这条技能"需要玩家做什么"的**覆盖文案**（M6 票 07；缺省 ``None`` = 读
    :data:`_REQUIREMENTS` 的短 id 那一行）。

    同一短 id 的两版（标准【闭月】摸一张 / 界【闭月】有手牌摸一、没有手牌摸二）
    牌面不同、文案也就不同；键仍是短 id，故给界版一条显式覆盖，而不是再抄一份表。
    """
    deck_arrange: DeckTopArrange | None = None
    """这条阶段体技能带的**牌堆顶排列规格**（M6 票 15；缺省 ``None`` = 不排列）。

    与 :meth:`flow` 的 ``Arrange`` 步引用**同一个对象**（一份声明，不是两份）：执行器读它
    求张数 / 切分，接入层（``sgs_web.projection._arrange_spec``）读 ``SkillDef.deck_arrange``
    做投影。本票的唯一消费者是界李典【恂恂】（固定切分 2 / 2）。
    """

    def handler(self, host: Any, frame: Frame) -> Control:
        """阶段体钩子：是否发动已由派发器问过；这里把 ``draws`` 张摸进持有者手里。"""
        host.draw_for_hand(frame, frame.data["skill_owner"], self.draws)
        return None


# W6-01 / W6-07：零步流水线。effect 由 :func:`sgs.skills.flow.zero_step_effect` 生成，只把
# 旧回调 :meth:`PhaseBodySkill.handler` 包一层。**晚绑定**是必须的：``_YINGZI`` / ``_BIYUE``
# 又要引用这两条流水线（``flow=…``），故绑定方法只能在它们定义**之后**取（import 期不读）。
def _yingzi_delegate(host: Any, frame: Frame) -> Control:
    """【英姿】的旧回调（``_YINGZI`` 在下方定义 ⇒ 取绑定方法要晚一步）。"""
    return _YINGZI.handler(host, frame)


def _biyue_delegate(host: Any, frame: Frame) -> Control:
    """【闭月】的旧回调（同 :func:`_yingzi_delegate`）。"""
    return _BIYUE.handler(host, frame)


YINGZI_FLOW = SkillFlow(name="yingzi", steps=(), effect=zero_step_effect(_yingzi_delegate))
"""【英姿】的零步流水线（W6-01）：[是否发动] → 多摸一张牌（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的 flow（先例 :data:`~sgs.skills.damage_after.FANKUI_FLOW`）。
"""

BIYUE_FLOW = SkillFlow(name="biyue", steps=(), effect=zero_step_effect(_biyue_delegate))
"""【闭月】的零步流水线（W6-01）：[是否发动] → 摸一张牌（无步骤）。"""


_YINGZI = PhaseBodySkill(
    # 周瑜【英姿】：摸牌阶段，你可以多摸一张牌（规则 1018）。
    hero_id="zhouyu",
    short_id="yingzi",
    timing=Timing.PHASE_DRAW,
    draws=1,
    flow=YINGZI_FLOW,
)


def _jie_yingzi_hand_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``HAND_LIMIT_REPLACE`` 回调：界【英姿】②"你的手牌上限等于体力上限"。

    ``current`` 是**当前体力值**（下限 0），返回**体力上限**——替换式，不是加式（加式住
    ``Player.hand_limit_bonus``）。``ctx.owner == ctx.subject`` 是"这条技能属于被查询的
    座位"的自校（修饰器链遍历全体存活实例，缺这一条会替所有人改上限）。
    """
    if ctx.owner != ctx.subject:
        return current
    return max(state.player(ctx.subject).hp_max, 0)


_JIE_YINGZI = PhaseBodySkill(
    # 界周瑜【英姿】（§十九 L2168–L2170）：锁定技，①摸牌阶段你多摸一张牌；
    # ②你的手牌上限等于体力上限（X = 体力上限）。
    hero_id="jie_zhouyu",
    short_id="yingzi",
    timing=Timing.PHASE_DRAW,
    draws=1,
    flow=YINGZI_FLOW,
    modifiers=(Modifier(ModifierPoint.HAND_LIMIT_REPLACE, _jie_yingzi_hand_limit),),
    locked=True,
    requirement="摸牌阶段你多摸一张牌；你的手牌上限等于体力上限",
)
_BIYUE = PhaseBodySkill(
    # 貂蝉【闭月】：结束阶段（回合结束阶段）开始时，你可以摸一张牌（规则 1054）。
    hero_id="diaochan",
    short_id="biyue",
    timing=Timing.PHASE_FINISH,
    draws=1,
    flow=BIYUE_FLOW,
)


def _jie_biyue_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """界貂蝉【闭月】的提交点：**当时**有手牌摸 1、没有手牌摸 2（§十九 L2215）。

    张数在提交点读**发动那一刻**的手牌数（结束阶段的当前事实），不是摸牌阶段的快照；
    与标准版共用同一条"是否发动"询问与阶段体挂点，差别只在摸几张。
    """
    del answers
    owner = frame.data["skill_owner"]
    count = 1 if host.state.player(owner).hand else 2
    host.draw_for_hand(frame, owner, count)
    return None


JIE_BIYUE_FLOW = SkillFlow(name="jie_biyue", steps=(), effect=_jie_biyue_effect)
"""界貂蝉【闭月】的零步流水线（M6 票 07）：[是否发动] → 有手牌摸 1 / 没有手牌摸 2。"""


_JIE_BIYUE = PhaseBodySkill(
    # 界貂蝉【闭月】（§十九 L2215）：结束阶段，若你有 / 没有手牌，你可以摸一 / 两张牌。
    # 与标准版【闭月】同一条阶段体挂点、同一条"是否发动"询问；牌面分叉只在摸几张。
    hero_id="jie_diaochan",
    short_id="biyue",
    timing=Timing.PHASE_FINISH,
    flow=JIE_BIYUE_FLOW,
    requirement="有手牌摸一张牌 / 没有手牌摸两张牌",
)


# ---------------------------------------------------------------------------
# 界曹仁（移动版）【据守】：结束阶段摸三张，然后**翻面**
#
# 移动版牌面（`界曹仁移动版`，用户录入）：
#
#     据守：结束阶段，你可以摸三张牌，然后翻面。
#
# 形状 = 阶段体零步（摸 N）+ 一段状态翻转 ⇒ 复用界【闭月】的"窗口自定义 effect"。
# "翻面"是 **toggle**（正 → 背、背 → 正；规则集 13.1），不是"置为背面"：自己在回合内
# 被翻成背面时发动据守，应当是翻回正面。
# ---------------------------------------------------------------------------

JUSHSOU_DRAWS = 3
"""【据守】摸牌数（移动版牌面："摸三张牌"；OL 版是四张，两版并存见账本）。"""


def _jushou_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """【据守】的提交点：先摸三张（短摸 ⇒ 平局），再把自己的武将牌翻一个面。

    "然后"的次序是牌面写死的：先摸、后翻面。摸牌走 :meth:`~sgs.engine.Engine.draw_for_hand`，
    可能派发"获得牌后"（票 24 的 Q1 起摸牌也算获得）并**挂起**。挂起时原样上抛 ``"stay"``，
    让续跑先跑完这一次移动的钩子、再回来翻面——与"先摸、后翻面"的牌面次序同源。
    平局（牌堆两空）时游戏已结束，不再翻面。

    **两个段落各一个游标**（票 25）：``jushou_drawn`` 在摸牌**之前**落，重入不重摸；
    ``jushou_flipped`` 在翻面**之后**落，重入不重翻。旧实现只有一个"整段已走"的游标且
    在摸牌前落——摸牌挂起后续跑会把它当成"翻面也做过了"，只摸不翻。
    """
    del answers
    owner = frame.data["skill_owner"]
    if frame.data.get(_JUSHSOU_FLIPPED):
        return None
    if not frame.data.get(_JUSHSOU_DRAWN):
        frame.data[_JUSHSOU_DRAWN] = True
        if host.draw_for_hand(frame, owner, JUSHSOU_DRAWS) == "stay":
            return "stay"
    if host.finished:
        return None
    host.set_flipped(frame, owner, not host.state.player(owner).flipped)
    frame.data[_JUSHSOU_FLIPPED] = True
    return None


_JUSHSOU_DRAWN = "jushou_drawn"
"""帧私有：本次【据守】的三张已经摸过（重入不重摸；摸牌可能挂起）。"""

_JUSHSOU_FLIPPED = "jushou_flipped"
"""帧私有：本次【据守】的翻面已经落过（重入不重翻；落点在翻面**之后**）。"""

JUSHSOU_FLOW = SkillFlow(name="jushou", steps=(), effect=_jushou_effect)
"""【据守】的零步流水线：[是否发动] → 摸三张 → 翻面。"""


_JUSHSOU = PhaseBodySkill(
    hero_id="jie_caoren_mobile",
    short_id="jushou",
    timing=Timing.PHASE_FINISH,
    flow=JUSHSOU_FLOW,
)


# ---------------------------------------------------------------------------
# 界李典【恂恂】（M6 票 15）：摸牌阶段开始时看四张，两张置顶、其余置底
# ---------------------------------------------------------------------------

XUNXUN = hero_skill_id("jie_lidian", "xunxun")
"""界李典【恂恂】的全限定技能 id（§十九 L2067–L2070）。"""

_XUNXUN_STEP = "cards"
"""``Arrange`` 步的步骤键（效果读 ``answers`` 的键，也是 :func:`watched_cards` 的定位）。"""

_XUNXUN_DONE_KEY = "xunxun_done"
"""放回完成的帧私有幂等位：``run_flow`` 重入时不得重复放回（与【观星】同姿态）。"""

XUNXUN_ARRANGE = DeckTopArrange(count=4, split=FixedSplit(2, 2))
"""【恂恂】的牌堆顶排列规格：**定数四张、固定切分 2 / 2**（"两张置于牌堆顶，其余置于牌堆底"）。

``FixedSplit`` 让候选生成只铺"置顶 2 张"这一种切分（两张的顺序仍任意）；同一个对象同时
被 :data:`XUNXUN_FLOW` 的 ``Arrange`` 步与 ``SkillDef.deck_arrange`` 引用——执行器与接入层
读的是**同一份声明**。
"""


def _xunxun_effect(host: Any, frame: Frame, answers: FlowAnswers) -> None:
    """提交点：按 ``ArrangeCards`` 把四张牌放回牌堆——上两行置顶、下两行置底。

    位置 → 实体牌读 ``Arrange`` 步首跑时存下的牌面（:func:`watched_cards`），不二次看牌
    （spec B3）。"置底"接在**原有牌堆底之下**（:func:`~sgs.effects.place_on_deck` 的
    ``to_top=False``），与【观星】逐字同口径；``_XUNXUN_DONE_KEY`` 短路保证续跑只放回一次。
    """
    if frame.data.get(_XUNXUN_DONE_KEY):
        return None
    action = answers[_XUNXUN_STEP]
    assert hasattr(action, "order") and hasattr(action, "split")  # 候选只有 ArrangeCards
    cards = watched_cards(frame, XUNXUN_FLOW, _XUNXUN_STEP)
    place_on_deck(
        host.state,
        tuple(cards[index] for index in action.order[: action.split]),
        to_top=True,
    )
    place_on_deck(
        host.state,
        tuple(cards[index] for index in action.order[action.split :]),
        to_top=False,
    )
    frame.data[_XUNXUN_DONE_KEY] = True
    return None


XUNXUN_FLOW = SkillFlow(
    name="xunxun",
    steps=(Arrange(key=_XUNXUN_STEP, spec=XUNXUN_ARRANGE),),
    effect=_xunxun_effect,
)
"""【恂恂】的一步流水线：排列（固定切分 2 / 2）→ 按答案放回牌堆顶 / 牌堆底。

**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow``，并要求它被
某个 ``Hook.flow`` 登记、``name`` 两两不同；``manifest`` 对含 ``Arrange`` 的流水线返回
空元组（排列无法用 ``Step.pool`` 表达），故恂恂不翻预发动。
"""


_XUNXUN = PhaseBodySkill(
    # 界李典【恂恂】（§十九 L2067–L2070）：摸牌阶段开始时，你可以观看牌堆顶的四张牌，
    # 然后将其中的两张牌置于牌堆顶，其余置于牌堆底。
    hero_id="jie_lidian",
    short_id="xunxun",
    timing=Timing.PHASE_DRAW,
    flow=XUNXUN_FLOW,
    deck_arrange=XUNXUN_ARRANGE,
)


# ---------------------------------------------------------------------------
# 神吕蒙移动版【涉猎】（用户裁定 2026-10-31）：摸牌阶段，改为亮出牌堆顶的五张牌，
#                           获得其中每种花色的牌各一张
#
# 规则原文（用户 `神吕蒙/技能.txt` 逐字）：
#
#     涉猎：摸牌阶段，你可以改为亮出牌堆顶的五张牌，然后获得其中每种花色的牌各一张。
#
# 用户的补充（同一轮命令，**是解释不是牌面**）：亮出的五张公开（处理区）；点选 0~4 张、
# 每种花色至多一张；同花色其余牌置灰；已选可再点取消；提交 / 放弃 / 超时同义（按当前
# 选择结算，未获得的置入弃牌堆）；任何短摸 ⇒ 游戏直接结束、平局。
#
# 结算口径：
# - "改为" = **替换**本次摸牌：`set_draw_phase_count(owner, 0)`（与【突袭】同一条）。
# - 亮牌进**处理区**（`draw_open`，公开、牌面全员可见；与【五谷丰登】同区）。
# - 选牌由提交点手写 `ask_selection` 下发给发动者（处理区自铺槽位，与五谷
#   `pick_harvest` 同形）；约束 `total=(0, 4)` + `max_per_suit=1` 落在
#   `SelectionSpec` 上（候选生成期收窄，不是技能代码里过滤）。
# - 获得 = 进手牌（`MoveReason.PUT`，与五谷同口径）；未获得的置入弃牌堆
#   （`MoveReason.PUT`，与界【裸衣】"不获得 ⇒ 置入弃牌堆"同口径）。
# - 短摸（两堆合计不足 5 张）⇒ `both_decks_empty` 平局并立即终止（用户裁定）。
# ---------------------------------------------------------------------------

SHELIE = hero_skill_id("shen_lvmeng_mobile", "shelie")
"""神吕蒙移动版【涉猎】的全限定技能 id。"""

SHELIE_REVEAL = 5
"""「亮出牌堆顶的五张牌」的张数（亮不满即短摸 = 平局，用户裁定）。"""

_SHELIE_REVEALED = "shelie_revealed"
"""帧私有：本次亮出的牌 id（自顶向下；重入不重亮、不重复消耗随机数）。"""

_ASK_SHELIE = "hero.shen_lvmeng_mobile.shelie.pick"
"""手写询问位置：【涉猎】"每种花色至多一张"的那次 ``ask_selection``。"""


def _shelie_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：亮五张（短摸即平局）→ 选 0~4 张（每花色至多一张）→ 分进手牌 / 弃牌堆。

    "亮出"走 :meth:`~sgs.engine.Engine.draw_open`（处理区、公开）；选牌的面在提交点自铺
    （五张都在处理区、牌面公开），故不是 ``PickCards`` 步骤。重入：亮牌缓存在帧私有
    ``_SHELIE_REVEALED``，选牌答案由 ``ask_selection`` 的帧内缓存返回，搬牌由
    ``move_in_frame`` 的"同一次移动"守卫挡住——续跑既不重亮也不重搬。
    """
    del answers
    owner = frame.data["skill_owner"]
    if _SHELIE_REVEALED not in frame.data:
        revealed = host.draw_open(frame, SHELIE_REVEAL, owner=owner)
        frame.data[_SHELIE_REVEALED] = tuple(card.card_id for card in revealed)
        # "改为"先把摸牌数改成 0：短摸终止时 ``_phase_draw`` 也不会再摸（0 张不抽随机数）。
        host.set_draw_phase_count(owner, 0)
        if len(revealed) < SHELIE_REVEAL:
            host.check_game_end(draw_reason="both_decks_empty")
            host.finish()
            return None
    card_ids = tuple(frame.data[_SHELIE_REVEALED])
    if not card_ids:
        return None
    slots = tuple(
        Slot(ref=ZoneRef(Zone.PROCESSING), card=host.state.card(card_id))
        for card_id in card_ids
    )
    picked = host.ask_selection(
        frame,
        owner=_ASK_SHELIE,
        key="shelie:pick",
        actor=owner,
        timing=Timing.PHASE_DRAW,
        spec=SelectionSpec(
            zones=(Zone.PROCESSING,), total=(0, len(Suit)), max_per_suit=1
        ),
        face=(slots, card_ids),
    )
    if host.suspended(picked):
        return "stay"
    chosen = {int(card_id) for card_id in picked}
    gained = tuple(card_id for card_id in card_ids if card_id in chosen)
    rest = tuple(card_id for card_id in card_ids if card_id not in chosen)
    if gained:
        control = host.move_in_frame(
            frame,
            host.state.refs(gained),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    if rest:
        control = host.move_in_frame(
            frame,
            host.state.refs(rest),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    return None


SHELIE_FLOW = SkillFlow(name="shelie", steps=(), effect=_shelie_effect)
"""【涉猎】的零步流水线：[是否发动] → 亮五张 → 选 0~4 张 → 分进手牌 / 弃牌堆。

含选牌的面在提交点自铺（处理区），故本流水线没有步骤；形状与界【裸衣】的"亮牌 → 问"
同族（零步 + 手写提交点询问）。
"""


_SHELIE = PhaseBodySkill(
    # 神吕蒙移动版【涉猎】：摸牌阶段，你可以改为亮出牌堆顶的五张牌，然后获得其中每种
    # 花色的牌各一张（用户裁定：0~4 张、每花色至多一张、未获得的置入弃牌堆）。
    hero_id="shen_lvmeng_mobile",
    short_id="shelie",
    timing=Timing.PHASE_DRAW,
    flow=SHELIE_FLOW,
)


# ---------------------------------------------------------------------------
# 张辽【突袭】（M6 P0 票 01）：放弃摸牌，改为获得至多两名其他角色各一张手牌
# ---------------------------------------------------------------------------

TUXI_TARGETS = 2
"""「至多两名其他角色」的上限（§九 L964–965）。"""

_TUXI_TAKEN = "tuxi_taken"
"""帧私有：本次发动里**已经交过牌**的座位（重入时跳过，不会重复拿）。"""


def _tuxi_targets(state: GameState, owner: int) -> tuple[int, ...]:
    """可以选的角色：**其他存活角色里手牌非空**的（没牌可拿的不进候选）。"""
    return tuple(
        player.seat
        for player in state.players
        if player.alive and player.seat != owner and player.hand
    )


def _tuxi_pool(
    state: GameState, frame: Frame, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """``PickSeats`` 的池：上次已选的人不再出现（同一角色只取一张手牌）。"""
    del frame
    return tuple(seat for seat in _tuxi_targets(state, owner) if seat not in chosen)


def _tuxi_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：逐个被选角色取**一张暗手牌**进张辽手牌，然后放弃本次摸牌。

    "暗牌"= 走 :meth:`Engine.selection_face` 的隐藏槽（``select`` 子流打乱位置），
    牌面在移动前对任何人都不出现；移动照常落 ``card_move``（``reason = PUT``、
    归属 = 获得者），不泄露给第三方（记录流按区域可见性裁剪）。
    进度记在帧私有 ``tuxi_taken``：搬牌可能内联派发"失去牌后"而挂起，重入不会二次搬。
    全部取完才把本阶段的摸牌数改成 0——中途挂起时基础摸牌还没开始，不会先摸再放弃。
    """
    owner = frame.data["skill_owner"]
    taken: list[int] = frame.data.setdefault(_TUXI_TAKEN, [])
    for seat in tuple(int(seat) for seat in answers["seat"]):
        if seat in taken:
            continue
        _slots, card_ids = host.selection_face(seat, (Zone.HAND,), reveal_hand=False)
        # 隐藏槽只提供"位置"；本次取第一张（位置序已由 ``select`` 子流打乱）。
        taken.append(seat)
        if not card_ids:
            continue
        control = host.move_in_frame(
            frame,
            host.state.refs((card_ids[0],)),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    host.set_draw_phase_count(owner, 0)
    return None


TUXI_FLOW = SkillFlow(
    name="tuxi",
    steps=(
        # 「至多两名」：1–2 名、可少选（``optional=True`` ⇒ 第二问起有 ``Pass``；
        # 第一问就放弃 ⇒ 整条流水线取消，按"没发动"回到正常摸两张）。
        PickSeats(lo=1, hi=TUXI_TARGETS, pool=_tuxi_pool, optional=True),
    ),
    effect=_tuxi_effect,
)
"""【突袭】的一步流水线：选 1–2 名其他角色 → 各取一张暗手牌 → 放弃摸牌。"""


def _tuxi_available(state: GameState, owner: int) -> bool:
    """额外前提：至少有一名其他角色手里有牌（没有可拿的就不问"是否发动"）。"""
    return bool(_tuxi_targets(state, owner))


_TUXI = PhaseBodySkill(
    # 张辽【突袭】：摸牌阶段，你可以放弃摸牌，改为获得至多两名其他角色的各一张手牌。
    hero_id="zhangliao",
    short_id="tuxi",
    timing=Timing.PHASE_DRAW,
    flow=TUXI_FLOW,
    available=_tuxi_available,
)


# ---------------------------------------------------------------------------
# 界张辽【突袭】（M6 票 14）：摸牌阶段，你可以**少摸任意张**牌，然后获得**等量**名其他
# 角色的各一张手牌（§十九 L2046–L2047）。
#
# 与标准版（"放弃摸牌，改为获得至多两名…"）**共用**触发时机、可拿的座位池 / 排除已选
# 的池、以及同一条一步座位流水线的**形状**（1–2 名、可少选 ⇒ X 就是选了几名）；差别只在
# 提交点：标准版把本阶段摸牌数改成 0，界版**按实际拿到的张数**减（少摸 X 张）。
# ---------------------------------------------------------------------------

JIE_TUXI = hero_skill_id("jie_zhangliao", "tuxi")
"""界张辽【突袭】的全限定技能 id（§十九 L2046–L2047）。"""

_TUXI_GAINED_KEY = "tuxi_gained"
"""帧私有：本次发动**已经真的拿到几张**（减摸牌张数的依据；搬牌挂起后续跑不丢计数）。"""

_TUXI_REDUCED_KEY = "tuxi_reduced"
"""帧私有：本阶段的摸牌数已经减过（重入不重复减）。"""


def _jie_tuxi_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：逐个被选角色取一张暗手牌，然后**少摸等量张**（与标准版只差最后一步）。

    进度计数住帧私有 ``tuxi_gained``：搬牌可能内联派发"失去牌后"而挂起，重入时先把
    已拿到的张数写回、再继续；摸牌数的减法只做一次（``tuxi_reduced``）。空手牌的座位
    （理论上不进候选池）不算"获得"，故也不减摸牌数——牌面写的是"获得**等量名**"。
    """
    owner = frame.data["skill_owner"]
    taken: list[int] = frame.data.setdefault(_TUXI_TAKEN, [])
    gained = int(frame.data.get(_TUXI_GAINED_KEY, 0))
    for seat in tuple(int(seat) for seat in answers["seat"]):
        if seat in taken:
            continue
        _slots, card_ids = host.selection_face(seat, (Zone.HAND,), reveal_hand=False)
        taken.append(seat)
        if not card_ids:
            continue
        gained += 1
        frame.data[_TUXI_GAINED_KEY] = gained
        control = host.move_in_frame(
            frame,
            host.state.refs((card_ids[0],)),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    if not frame.data.get(_TUXI_REDUCED_KEY):
        frame.data[_TUXI_REDUCED_KEY] = True
        host.reduce_draw_phase_by(owner, gained)
    return None


JIE_TUXI_FLOW = SkillFlow(
    name="jie_tuxi",
    steps=(
        # 「获得**等量名**其他角色的各一张手牌」：选几名 = 少摸几张；1–2 名、可少选。
        PickSeats(lo=1, hi=TUXI_TARGETS, pool=_tuxi_pool, optional=True),
    ),
    effect=_jie_tuxi_effect,
)
"""界【突袭】的一步流水线：选 1–2 名其他角色 → 各取一张暗手牌 → 少摸等量张。"""


_JIE_TUXI = PhaseBodySkill(
    # 界张辽【突袭】：摸牌阶段，你可以少摸任意张牌，然后获得等量名其他角色的各一张手牌。
    hero_id="jie_zhangliao",
    short_id="tuxi",
    timing=Timing.PHASE_DRAW,
    flow=JIE_TUXI_FLOW,
    available=_tuxi_available,
    requirement="少摸任意张牌，然后获得等量名其他角色的各一张手牌",
)


# ---------------------------------------------------------------------------
# 许褚【裸衣】（M6 P0 票 01）：少摸一张；本回合【杀】/【决斗】伤害 +1
# ---------------------------------------------------------------------------

LUOYI_DAMAGE_BONUS = 1
"""【裸衣】的伤害增量（§九 L968–970：使用【杀】或【决斗】造成的伤害 +1）。"""

LUOYI_DUEL_TYPE = "duel"
"""【决斗】的牌名 id（与【杀】家族并列的第二个受益牌型）。"""


def _luoyi_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：本阶段少摸一张，并落"本回合伤害 +1"的座位状态（回合末清）。"""
    del answers
    owner = frame.data["skill_owner"]
    host.reduce_draw_phase_by(owner, 1)
    host.set_luoyi_active(owner, True)
    return None


LUOYI_FLOW = SkillFlow(name="luoyi", steps=(), effect=_luoyi_effect)
"""【裸衣】的零步流水线：[是否发动] → 少摸一张 + 本回合伤害 +1（无步骤）。"""


def _luoyi_damage_bonus(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """``DAMAGE_DEAL_AMOUNT`` 回调：本回合内许褚的【杀】/【决斗】伤害 +1。

    只认**来源侧**（``ctx.owner == ctx.subject``：许褚是造成伤害的人）与**渠道牌**
    （【杀】家族按行为表判 — 普通 / 火 / 雷 / 转化 / 虚拟同族；【决斗】按牌名 id）；
    传导帧不派发本挂点（``damage_amount`` 只对 ``chain=False`` 的源头帧派发），
    故不会把 +1 重复算进连环传导。"是否发动过"记在**座位级状态** ``Player.luoyi_active``
    （一次性效果，不挂到技能实例上；回合末由引擎清理），而"伤害 +1"本身是本技能随身
    携带的**连续型修饰器**——随实例存亡（`06`（§5「程序化约定与偏离一手」）/ spec D9）。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    return _luoyi_bonus(state, ctx, current, active=state.player(ctx.owner).luoyi_active)


def _luoyi_bonus(
    state: GameState, ctx: ModifierContext, current: Any, *, active: bool
) -> Any:
    """``DAMAGE_DEAL_AMOUNT`` 的**共用判据**：来源侧 + 【杀】/【决斗】+ 座位状态已置位。

    标准 / 界两版【裸衣】的差别只有"哪个座位状态字段"（作用域不同），故判据只此一份；
    ``active`` 由各自的回调从自己的字段读出来（调用方已保证 ``ctx.owner`` 是合法座位）。
    """
    del state
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not active:
        return current
    if is_slash(behavior_for(ctx.card)) or ctx.card.type == LUOYI_DUEL_TYPE:
        return current + LUOYI_DAMAGE_BONUS
    return current


_LUOYI = PhaseBodySkill(
    # 许褚【裸衣】：摸牌阶段，你可以少摸一张牌；若如此做，你使用【杀】或【决斗】造成的
    # 伤害 +1（直到回合结束）。
    hero_id="xuchu",
    short_id="luoyi",
    timing=Timing.PHASE_DRAW,
    flow=LUOYI_FLOW,
    modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _luoyi_damage_bonus),),
)


# ---------------------------------------------------------------------------
# 界许褚【裸衣】（M6 票 14；用户裁定修订）：摸牌阶段开始时，你亮出牌堆顶的三张牌，然后
# **你可以获得**其中的基本牌、武器牌和【决斗】。若如此做，你放弃摸牌，且直到你的下个
# 回合开始，你为伤害来源的【杀】或【决斗】造成的**基础伤害值** +1（§十九 L2051–L2055）。
#
# 与标准版不是同一句（"少摸一张换本回合 +1"），故不复用标准版的流水线；共用的是
# **伤害加成判据**（:func:`_luoyi_bonus`：来源侧 + 【杀】/【决斗】）与修饰器挂点。
# 三个作用域事实各有唯一落点：
#
# - **亮出 3 张**：``peek_deck`` 只看不移（牌仍在牌堆顶，没拿走的自然留在原处），每张落
#   一条公开的 ``card_reveal``——"亮出"是公开事实，不是私有观看；
# - **放弃摸牌**：``set_draw_phase_count(owner, 0)``（与【突袭】同一个写入口）；
# - **直到你的下个回合开始**：座位状态 ``Player.luoyi_until_next_turn``，清理点 = 该座位
#   自己的 ``TURN_START``（与标准版"回合末清"分开，见 :class:`~sgs.state.Player`）。
#
# **第二次询问**（用户裁定，已登记 §5「程序化约定与偏离一手」）：亮出后问一次
# 「是否获得」，二选一、不可取消。获得 ⇒ 搬走三张里的合格牌 + 放弃摸牌 + 加伤；不获得 ⇒
# 把**亮出的三张全部置入弃牌堆**，不改摸牌数、不加伤。后者**不是官方原文**（原文只写
# "亮出"，没写不获得时牌的去处），是本仓明知而偏离，改回去等于回归官方。
#
# **退化档**：三张里一张合格牌都没有时，没有"获得"这一项，故**不发**第二次询问，直接按
# "不获得"结算（三张置入弃牌堆 + 正常摸两张）。这确实会让发动者在没有收益时白掉三张；
# 但"不获得 ⇒ 置入弃牌堆"是用户给这条技能的统一落点，而"没有可选项"不应变成一次空询问。
# ---------------------------------------------------------------------------

JIE_LUOYI = hero_skill_id("jie_xuchu", "luoyi")
"""界许褚【裸衣】的全限定技能 id（§十九 L2051–L2055）。"""

JIE_LUOYI_REVEAL = 3
"""「亮出牌堆顶的三张牌」的张数（§十九 L2051）。"""

JIE_LUOYI_GAIN = "luoyi_gain"
"""第二次询问的选项：获得亮出牌里的合格者（并放弃摸牌 + 加伤）。"""

JIE_LUOYI_SKIP = "luoyi_skip"
"""第二次询问的选项：不获得（亮出的三张置入弃牌堆，正常摸两张）。"""

JIE_LUOYI_OPTIONS: tuple[str, ...] = (JIE_LUOYI_GAIN, JIE_LUOYI_SKIP)
"""「是否获得」的闭集；顺序即界面按钮顺序（获得在前）。"""

_LUOYI_REVEALED_KEY = "jie_luoyi_revealed"
"""帧私有：本次亮出的牌 id（自顶向下；续跑沿用它，不重看牌堆）。"""

_LUOYI_CHOICE_KEY = "jie_luoyi_choice"
"""帧私有：第二次询问的答案（``JIE_LUOYI_GAIN`` / ``JIE_LUOYI_SKIP``）；重入不重问。"""

_LUOYI_GAINED_KEY = "jie_luoyi_gained"
"""帧私有：其中的"基本牌 / 武器牌 / 【决斗】"已经搬进手牌。"""

_LUOYI_DISCARDED_KEY = "jie_luoyi_discarded"
"""帧私有：亮出的牌已经置入弃牌堆（不获得路径的幂等位）。"""

_LUOYI_SETTLED_KEY = "jie_luoyi_settled"
"""帧私有：放弃摸牌 + 加伤已经落过（搬牌挂起后的续跑不重复落）。"""


def _jie_luoyi_keeps(state: GameState, card: Any) -> bool:
    """这三张里的这一张能不能拿：**基本牌** ∨ **武器牌** ∨ 【决斗】（§十九 L2051–L2052）。

    三种判据都读数据层的事实，不抄牌名清单：大类读 :func:`sgs.data.card_category`，
    武器读 :class:`~sgs.state.SlotKind`（``CardType.subcategory`` 在实例化时派生的牌面事实），
    【决斗】按牌名 id（与标准版同一个 :data:`LUOYI_DUEL_TYPE`）。
    """
    if card_category(state.pack_id, card.type) is Category.BASIC:
        return True
    if card.slot is SlotKind.WEAPON:
        return True
    return card.type == LUOYI_DUEL_TYPE


def _jie_luoyi_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：亮出三张 → 问「是否获得」→ 获得或置入弃牌堆。

    顺序即牌面的顺序（"然后"）。四段各一个帧私有游标：亮出只做一次（续跑不重看），
    第二次询问只发一次（``ask_option`` 的帧内缓存 + 本帧私有键），搬牌可能内联派发
    "失去牌后"而挂起（续跑不重搬），"若如此做"的两件副作用只落一次。

    **不获得**走用户裁定：三张亮出的牌全部置入弃牌堆，**不**改摸牌数、**不**加伤。
    置入用 :data:`~sgs.state.MoveReason.PUT` 而不是 ``DISCARD``——它不是任何人"弃置"
    手牌 / 装备，而是规则性置入弃牌堆；这样事件流不会把它记成一次弃置动作。

    **退化档**：没有合格牌 ⇒ 不提问，直接按不获得结算（三张置入弃牌堆 + 正常摸两张）。
    """
    owner = frame.data["skill_owner"]
    state = host.state
    if _LUOYI_REVEALED_KEY not in frame.data:
        revealed = host.peek_deck(frame, JIE_LUOYI_REVEAL)
        frame.data[_LUOYI_REVEALED_KEY] = tuple(card.card_id for card in revealed)
        # 阶段体帧缺省 frame_id = 0，而 0 号展示项只停留 1s：这次亮牌后面还要问
        # 「是否获得」，必须绑到一条真实的承载帧上，问完由阶段体收尾 pop。
        host.open_phase_body_frame(frame)
        for card in revealed:
            # "亮出"= 公开事实（与【火攻】的亮牌同一个记录面），牌本身仍留在牌堆顶。
            host.reveal_card(frame, owner, card)
    revealed = state.refs(tuple(frame.data[_LUOYI_REVEALED_KEY]))
    kept = tuple(card for card in revealed if _jie_luoyi_keeps(state, card))

    # 第二次询问：有可拿的牌才有二选一；没有就退化成"不获得"，不发空询问。
    choice = frame.data.get(_LUOYI_CHOICE_KEY)
    if kept and choice is None:
        answer = host.ask_option(
            frame,
            owner,
            Timing.PHASE_DRAW,
            skill_id="luoyi:gain",
            options=JIE_LUOYI_OPTIONS,
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        choice = str(answer.choice)
        frame.data[_LUOYI_CHOICE_KEY] = choice
    if choice == JIE_LUOYI_GAIN:
        if not frame.data.get(_LUOYI_GAINED_KEY):
            control = host.move_in_frame(
                frame,
                kept,
                to_zone=ZoneRef(Zone.HAND, owner),
                reason=MoveReason.PUT,
                owner=owner,
                actor=owner,
            )
            if control == "stay":
                return "stay"
            frame.data[_LUOYI_GAINED_KEY] = True
        if not frame.data.get(_LUOYI_SETTLED_KEY):
            frame.data[_LUOYI_SETTLED_KEY] = True
            host.set_draw_phase_count(owner, 0)
            host.set_luoyi_until_next_turn(owner, True)
        return None

    # ``choice`` 为 None = 没有合格牌的退化档；与显式「不获得」走同一落点。
    if revealed and not frame.data.get(_LUOYI_DISCARDED_KEY):
        frame.data[_LUOYI_DISCARDED_KEY] = True
        control = host.move_in_frame(
            frame,
            revealed,
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.PUT,
            owner=owner,
            actor=owner,
        )
        if control == "stay":
            return "stay"
    return None


JIE_LUOYI_FLOW = SkillFlow(name="jie_luoyi", steps=(), effect=_jie_luoyi_effect)
"""界【裸衣】的零步流水线：[是否发动] → 亮三张 → 是否获得 → 拿牌 + 弃摸牌 + 跨回合 +1。"""


def _jie_luoyi_damage_bonus(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """界【裸衣】的 ``DAMAGE_DEAL_AMOUNT`` 回调：读**跨回合**的座位状态。

    判据与标准版共用 :func:`_luoyi_bonus`；只有"作用域字段"不同——界版的作用域是
    "直到你的下个回合开始"，住 ``Player.luoyi_until_next_turn``（见那里的注释）。
    """
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    return _luoyi_bonus(
        state, ctx, current, active=state.player(ctx.owner).luoyi_until_next_turn
    )


_JIE_LUOYI = PhaseBodySkill(
    # 界许褚【裸衣】：摸牌阶段开始时亮出牌堆顶三张，可获得其中的基本牌 / 武器牌 /【决斗】；
    # 若如此做，放弃摸牌，且直到你的下个回合开始【杀】/【决斗】基础伤害 +1。
    hero_id="jie_xuchu",
    short_id="luoyi",
    timing=Timing.PHASE_DRAW,
    flow=JIE_LUOYI_FLOW,
    modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _jie_luoyi_damage_bonus),),
    requirement=(
        "亮出牌堆顶三张牌，可获得其中的基本牌 / 武器牌 /【决斗】；若如此做，放弃摸牌，"
        "且直到你的下个回合开始【杀】或【决斗】造成的基础伤害 +1"
    ),
)

# ---------------------------------------------------------------------------
# 吕蒙【克己】（M6 票 03）：本回合出牌阶段内未使用 / 打出过【杀】⇒ 可以跳过弃牌阶段
# ---------------------------------------------------------------------------

KEJI = hero_skill_id("lvmeng", "keji")
"""吕蒙【克己】的全限定技能 id。"""


def _keji_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：置"跳过弃牌阶段"——由 :meth:`Engine._phase_discard_start` 消费。"""
    del answers
    host.skip_discard_phase(frame.data["skill_owner"])
    return None


KEJI_FLOW = SkillFlow(name="keji", steps=(), effect=_keji_effect)
"""【克己】的零步流水线：[是否发动] → 跳过弃牌阶段（无步骤）。"""


def _keji_available(state: GameState, owner: int) -> bool:
    """额外前提：本回合出牌阶段内**没有**使用 / 打出过【杀】（引擎记的座位级事实）。"""
    return not state.player(owner).slash_in_play_phase


_KEJI = PhaseBodySkill(
    # 吕蒙【克己】：若你于出牌阶段内没有使用或打出过【杀】，你可以跳过弃牌阶段（§九 L1027）。
    hero_id="lvmeng",
    short_id="keji",
    timing=Timing.PHASE_DISCARD_START,
    flow=KEJI_FLOW,
    available=_keji_available,
)
_KEJI_JIE = PhaseBodySkill(
    # 界吕蒙【克己】（M6 票 06）：牌面与标准版**逐字相同**（§十九 L2151），故共用同一条
    # 流水线与同一条"本阶段没用过【杀】"前提；界版只是另一个武将 id（同一真名的两版不并存）。
    hero_id="jie_lvmeng",
    short_id="keji",
    timing=Timing.PHASE_DISCARD_START,
    flow=KEJI_FLOW,
    available=_keji_available,
)


# ---------------------------------------------------------------------------
# 界吕蒙【博图】（M6 票 11）：每轮限 X 次，回合结束时，若本回合置入弃牌堆的牌中包含
# 四种花色，你可以执行一个额外的回合（§十九 L2156–L2158）。
#
# 规则原文：
#
#     博图：每轮限 X 次（X 为存活角色数且至多为 3），回合结束时，若本回合置入弃牌堆的
#          牌中包含四种花色，你可以执行一个额外的回合。
#
# 三个落点各自只有一处真源：
#
# - **条件**（本回合置入弃牌堆的牌含四种花色）读 ``GameState.discard_suits_this_turn``：
#   写入点在搬牌原语里（"计入本回合**所有来源**置入的牌"，`06`（§5「程序化约定与偏离
#   一手」）的裁定），作用域 = 一个回合（``TURN_START`` 复位）；
# - **上限 X** 在 :func:`_botu_available` 里**每次发动时重算**（存活角色数且至多 3，
#   `06`（§5「程序化约定与偏离一手」）的第二条裁定）——已用次数住
#   ``GameState.round_use_counts``，轮次 +1 才清空，故额外回合不会刷新它；
# - **效果** = 压一个额外回合进待执行栈（``Engine.schedule_extra_turn``），由回合环在下一个
#   回合边界弹出接管，**自然指针不前进**（D13）。
# ---------------------------------------------------------------------------

JIE_BOTU = hero_skill_id("jie_lvmeng", "botu")
"""界吕蒙【博图】的全限定技能 id。"""

BOTU_ROUND_LIMIT = 3
"""【博图】「至多 3」的硬上限（§十九 L2157）：X = ``min(存活角色数, 3)``。"""

_SUITS: tuple[Suit, ...] = tuple(Suit)
"""四种花色（"包含四种花色"的判据长度；取值与 ``CardRef.suit`` 的字符串同源）。"""


def _botu_available(state: GameState, owner: int) -> bool:
    """【博图】的两个前提：本回合弃牌堆含四花色 ∧ 本轮次数还没用完。

    四条花色是**去重后的花色种类数**（``discard_suits_this_turn`` 按首次出现序去重）：
    "包含四种花色"读作"里面出现过全部四种色"，而不是"恰好四张"。
    X **每次发动时重算**（`06`（§5「程序化约定与偏离一手」））：存活角色数会中途
    变小，写成开场快照会让已经死人的对局多发动几次。
    """
    if len(state.discard_suits_this_turn) < len(_SUITS):
        return False
    alive = sum(1 for player in state.players if player.alive)
    return round_skill_uses(state, owner, JIE_BOTU) < min(alive, BOTU_ROUND_LIMIT)


def _botu_effect(host: Any, frame: Frame, answers: Any) -> Control:
    """提交点：记一次「本轮已发动」并把一个额外回合压进待执行栈（两件事同一处）。"""
    del answers
    owner = frame.data["skill_owner"]
    mark_round_skill_used(host.state, owner, JIE_BOTU)
    host.schedule_extra_turn(owner)
    return None


BOTU_FLOW = SkillFlow(name="botu", steps=(), effect=_botu_effect)
"""【博图】的零步流水线：[是否发动] → 压一个额外回合（无步骤）。"""


_BOTU = PhaseBodySkill(
    # 界吕蒙【博图】：回合结束时，若本回合置入弃牌堆的牌包含四种花色，可以执行一个
    # 额外的回合；每轮限 X 次（X = 存活角色数且至多 3，每次发动时重算）。
    hero_id="jie_lvmeng",
    short_id="botu",
    timing=Timing.TURN_END,
    flow=BOTU_FLOW,
    available=_botu_available,
    per_round_limit=True,
)


# ---------------------------------------------------------------------------
# 界张飞【替身】（M6 票 05）：限定技——准备阶段，体力回复至上限，摸回复量张牌
#
# 规则原文（§十九 L2095–L2097）：
#
#     替身：限定技，准备阶段，你可以将体力回复至体力上限，摸以此法回复的体力值数张牌。
#
# "限定技"= **整局只能发动一次**（§四 L350–L355），落点是 `sgs.skills.catalog` 的派发环
# （非限定技零动作）：它与本技能的 `skill_type = LIMITED` 一起构成唯一判据，"已用"位住
# per-instance 的 `GameState.skill_state`。与 locked 正交——技能面写"你可以"，故仍是非锁定。
# ---------------------------------------------------------------------------

TISHEN = hero_skill_id("jie_zhangfei", "tishen")
"""界张飞【替身】的全限定技能 id。"""

_TISHEN_HP_BEFORE = "tishen_hp_before"
"""帧私有：发动前的体力（重入时用来算"**以此法**回复的体力值"）。"""


def _tishen_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：先压 ``RECOVER`` 帧回满，出栈后摸"实际回复值"张牌（"然后"的次序）。

    回复量声明为 ``hp_max - hp``（"回复至体力上限"），但**摸几张按实际回复的体力值**算：
    重入时比较发动前后的体力，而不是把声明的量再算一遍（【救援】一类回复量修饰器把量
    抬高后仍被上限夹住，读声明值会摸多）。回复 0（本来就满）⇒ 不压帧、不摸牌。
    """
    del answers
    owner = frame.data["skill_owner"]
    if _TISHEN_HP_BEFORE not in frame.data:
        player = host.state.player(owner)
        frame.data[_TISHEN_HP_BEFORE] = player.hp
        healed = player.hp_max - player.hp
        if healed <= 0:
            return None
        host.push_frame(
            FrameKind.RECOVER,
            {
                "source": owner,
                "targets": (owner,),
                "card": None,
                "use_timing": frame.data.get("use_timing"),
                "amount": healed,
            },
            timing=Timing.RECOVER_VALUE,
        )
        return "stay"
    healed = host.state.player(owner).hp - int(frame.data[_TISHEN_HP_BEFORE])
    if healed > 0:
        host.draw_for_hand(frame, owner, healed)
    return None


TISHEN_FLOW = SkillFlow(name="tishen", steps=(), effect=_tishen_effect)
"""【替身】的零步流水线：[是否发动] → 回复至上限 → 摸回复量张牌（无步骤）。"""


_TISHEN = PhaseBodySkill(
    # 界张飞【替身】：限定技，准备阶段，你可以将体力回复至体力上限，摸以此法回复的
    # 体力值数张牌（§十九 L2095–L2097）。
    hero_id="jie_zhangfei",
    short_id="tishen",
    timing=Timing.PHASE_PREPARE,
    flow=TISHEN_FLOW,
    skill_type=SkillType.LIMITED,
)


# ---------------------------------------------------------------------------
# 夏侯渊【神速】（风包，票 01）：回合开始时一至两项 → 跳阶段 + 无距离【杀】
#
# 牌面（`D:\SGS_HEROS\神话再临-风包\夏侯渊\技能.txt`，逐字）：
#   回合开始时，你可以选择一至两项：
#   【1】跳过判定阶段和摸牌阶段；
#   【2】跳过出牌阶段并弃置一张装备牌。
#   你每以此法选择一项，便视为你使用一张无距离限制的普通【杀】。
#
# 三条落地判据（票面 §1.6 的 R1–R4）：
# - **一至两项** = 三档单选项（`{【1】}` / `{【2】}` / `{两项都选}`）：`SELECT_OPTION` 只有
#   单选控件，把子集摊成三档**不引入新询问形态**；两项按【1】→【2】逐项结算完。
# - **无距离限制** = 这一次使用的距离条件豁免：目标池在**技能侧**自算（全体其他存活 →
#   `TARGET_POOL` 链）。**不**用"修饰器并集"（界武圣② / 武神② 的写法）：**修饰器管道序**
#   （= `resolve_instances` 的解析序，不是票 05 的派发链序）按 skill_id
#   字典序，`hero.xiahouyuan.shensu` 排在 `hero.jie_zhugeliang.kongcheng` 之后，并集会把
#   【空城】刚删掉的目标加回来（探针 `.scratch/feng-xiahouyuan/probe_target_pool_order.py`）。
# - **跳过阶段** = 阶段推进状态（`host.skip_*_phase`），不新增 `Timing`；被跳过的判定阶段
#   **不消费**判定区里的牌（规则 §11.5 / 账本行）。
# ---------------------------------------------------------------------------

SHENSU = hero_skill_id("xiahouyuan", "shensu")
"""夏侯渊【神速】的全限定技能 id。"""

SHENSU_SKIP_JUDGE_DRAW = "shensu_skip_judge_draw"
SHENSU_SKIP_PLAY_EQUIP = "shensu_skip_play_equip"
SHENSU_BOTH = "shensu_both"
"""三档单选项的 id（闭集；不用裸序号，避免与别的选项表撞键）。"""

SHENSU_OPTIONS: tuple[str, ...] = (
    SHENSU_SKIP_JUDGE_DRAW,
    SHENSU_SKIP_PLAY_EQUIP,
    SHENSU_BOTH,
)
"""选项顺序 = 牌面【1】→【2】→"两项"；没有装备牌时只剩【1】（O1：走不通的项不出现）。"""

SHENSU_TARGET_WORDING = "选择一张无距离限制的普通【杀】的目标"
"""手写选目标询问的逐步文案（`step_requirements["target"]`）。"""

_ASK_SHENSU_EQUIP = "hero.xiahouyuan.shensu.equip"
"""手写询问位置：弃置一张装备牌（`ask_selection` 的询问位置，逐处唯一）。"""

_SHENSU_ITEM1 = "shensu_item1"
"""帧私有：【1】的跳过阶段已置位（续跑不重置）。"""

_SHENSU_PAID = "shensu_paid"
"""帧私有：【2】的装备牌已弃置（续跑不重弃）。"""

_SHENSU_USED = "shensu_used"
"""帧私有：第 n 张虚拟【杀】已经发起（续跑不重复使用）。"""

_SHENSU_TARGET = "shensu_target"
"""帧私有：第 n 张虚拟【杀】已经选定的目标座位（续跑不重问）。"""


def _shensu_is_equipment(state: GameState, card: Any) -> bool:
    """这张牌是不是**装备牌**（大类读数据层，与【裸衣】的"基本牌 / 武器牌"同一判据）。"""
    return card_category(state.pack_id, card.type) is Category.EQUIPMENT


def _shensu_equipment_cards(state: GameState, owner: int) -> tuple[int, ...]:
    """可弃的装备牌：**手牌 + 装备区**里的装备牌（默认读法（一）③"装备牌"含手牌）。"""
    player = state.player(owner)
    return tuple(
        card_id
        for card_id in (*player.hand, *player.equip)
        if _shensu_is_equipment(state, state.card(card_id))
    )


def _shensu_equipment_face(host: Any, owner: int) -> Any:
    """弃牌面的**收窄**：自己的手牌 + 装备区，再按"是不是装备牌"过滤（槽位与 id 同序）。"""
    slots, card_ids = host.selection_face(
        owner, (Zone.HAND, Zone.EQUIP), reveal_hand=True
    )
    kept = [
        (slot, card_id)
        for slot, card_id in zip(slots, card_ids, strict=True)
        if _shensu_is_equipment(host.state, host.state.card(card_id))
    ]
    return tuple(slot for slot, _ in kept), tuple(card_id for _, card_id in kept)


def _shensu_options(
    state: GameState, frame: Frame, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """选项步的闭集（可调用档）：付不起【2】时它不出现，故本步恒有候选。"""
    del frame, answers
    if not _shensu_equipment_cards(state, owner):
        return (SHENSU_SKIP_JUDGE_DRAW,)
    return SHENSU_OPTIONS


def _shensu_target_pool(state: GameState, owner: int, card: Any) -> tuple[int, ...]:
    """虚拟【杀】的目标池：**全体其他存活角色**（无距离限制）再过 `TARGET_POOL` 链。

    距离条件整条豁免（规则 §17.3 L1702–L1704：声明"使用【杀】无距离限制"即可指定任意
    其他**合法**角色），但"不能成为目标"那一层照常——【空城】【谦逊】一类的删目标回调
    在这条链上跑（与 `check_targets` 的链尾同源，只是省掉了距离筛）。
    """
    everyone = tuple(
        player.seat for player in state.players if player.alive and player.seat != owner
    )
    return tuple(
        apply_modifiers(
            ModifierPoint.TARGET_POOL,
            state,
            ModifierContext(subject=owner, card=card),
            everyone,
        )
    )


def _shensu_use_slash(host: Any, frame: Frame, owner: int, index: int) -> Control:
    """发起第 ``index`` 张虚拟【杀】：选目标 → ``start_use``，返回 ``"stay"`` / ``None``。"""
    used_key = f"{_SHENSU_USED}:{index}"
    if frame.data.get(used_key):
        return None
    card = pure_virtual_card(host.state, "slash")
    pool = _shensu_target_pool(host.state, owner, card)
    if not pool:
        # 一个合法目标都没有（其他存活角色全部"不能成为目标"）：不发空询问，这一张【杀】
        # 不发生；已选的跳过阶段照旧生效（牌面没有回退项）。
        frame.data[used_key] = True
        return None
    target_key = f"{_SHENSU_TARGET}:{index}"
    target = frame.data.get(target_key)
    if target is None:
        answer = host.ask_option(
            frame,
            owner,
            Timing.TURN_START,
            skill_id=f"{SHENSU}:target:{index}",
            options=tuple(f"seat:{seat}" for seat in pool),
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        target = int(str(answer.choice).split(":", 1)[1])
        frame.data[target_key] = target
    frame.data[used_key] = True
    # ``counts=False``：这次使用**不计入**出牌阶段的【杀】次数（规则 §17.10 A2 只约束
    # "于出牌阶段的空闲时间点使用【杀】"）。照常记账会让回合开始时的这张【杀】把出牌阶段的
    # 额度用掉（`check_count` 只在自己的出牌阶段读计数）。
    host.start_use(
        card,
        source=owner,
        targets=(int(target),),
        use_timing=Timing.TURN_START,
        counts=False,
    )
    return "stay"


def _shensu_pay_equipment(host: Any, frame: Frame, owner: int) -> Any:
    """【2】的**代价步**：弃一张装备牌。

    牌面「跳过出牌阶段**并弃置一张装备牌**」里的弃牌是**这项选择的代价**，不是结果：
    选项一交出去就立刻付（用户裁定 2026-11-01），付不起这一项就不成立——故这一步排在
    **任何结果（跳阶段 / 第一张【杀】）之前**，不是"先选目标出杀、再回头弃牌"。

    返回 ``"paid"``（已付）/ ``"stay"``（挂起）/ ``None``（付不起 ⇒ 这一项不成立）。
    最后一种是防御分支：选项步的候选已按"有没有装备牌"收窄，选完到付款之间没有任何结算，
    正常局面走不到；真走到就只作废【2】（另一项与已付的都不回退）。
    """
    if frame.data.get(_SHENSU_PAID):
        return "paid"
    if not _shensu_equipment_cards(host.state, owner):
        return None
    picked = host.ask_selection(
        frame,
        owner=_ASK_SHENSU_EQUIP,
        key="shensu:equip",
        actor=owner,
        timing=Timing.TURN_START,
        spec=SelectionSpec(
            zones=(Zone.HAND, Zone.EQUIP), total=(1, 1), optional=False
        ),
        build_face=lambda: _shensu_equipment_face(host, owner),
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        return None
    control = host.move_in_frame(
        frame,
        host.state.refs((int(picked[0]),)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.DISCARD,
        owner=owner,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    frame.data[_SHENSU_PAID] = True
    return "paid"


def _shensu_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：**先付【2】的代价**，再按【1】→【2】依次执行各项结果。

    次序是用户裁定的（2026-11-01）：「两个选项都是在回合开始时选择，第 2 个选项需要**立刻**
    付出代价才能选择成功；选择完毕后依次执行跳过对应阶段」。故：
    ① 选了两项 ⇒ **立刻**弃一张装备牌（`_shensu_pay_equipment`），付不起就【2】不成立；
    ② 然后执行结果：先【1】（跳判定 + 摸牌 → 一张【杀】），再【2】（跳出牌 → 一张【杀】）。
    每段一个帧私有游标：跳过阶段只置一次、装备只弃一次、第 n 张【杀】只发起一次。
    """
    owner = frame.data["skill_owner"]
    items = str(answers["items"])
    want_one = items in (SHENSU_SKIP_JUDGE_DRAW, SHENSU_BOTH)
    want_two = items in (SHENSU_SKIP_PLAY_EQUIP, SHENSU_BOTH)
    if want_two:
        paid = _shensu_pay_equipment(host, frame, owner)
        if paid == "stay":
            return "stay"
        want_two = paid == "paid"
    if want_one:
        if not frame.data.get(_SHENSU_ITEM1):
            host.skip_judge_phase(owner)
            host.skip_draw_phase(owner)
            frame.data[_SHENSU_ITEM1] = True
        if _shensu_use_slash(host, frame, owner, 0) == "stay":
            return "stay"
    if want_two:
        host.skip_play_phase(owner)
        if _shensu_use_slash(host, frame, owner, 1) == "stay":
            return "stay"
    return None


SHENSU_FLOW = SkillFlow(
    name="shensu",
    steps=(PickOption(options=_shensu_options, optional=False, key="items"),),
    effect=_shensu_effect,
)
"""【神速】的一步流水线：选一至两项（三档单选项）→ 按项结算（两张【杀】各自选目标）。"""


_SHENSU = PhaseBodySkill(
    hero_id="xiahouyuan",
    short_id="shensu",
    timing=Timing.TURN_START,
    flow=SHENSU_FLOW,
)



# ---------------------------------------------------------------------------
# 神周瑜移动版【琴音】（huo-shenzhouyu-mobile 票 01 / 框架票 03）
#
# 牌面（用户 2026-11-02 录入，移动版）：
#
#     琴音
#     　弃牌阶段结束时，若你于此阶段内弃置过你至少两张手牌，则你可以选择一项：
#     　1.令所有角色各回复1点体力；2.令所有角色各失去1点体力。
#
# 形状 = **阶段体（帧外时机 PHASE_DISCARD_END）+ 单步选项 + 手写主体**：
# - 触发条件是**帧私有**事实（本弃牌阶段弃置的手牌数，由 ``_phase_discard`` 计数、
#   ``_phase_discard_end`` 经 ``_dispatch_phase_body`` 的 ``data`` 并进阶段体帧）⇒
#   用 :attr:`PhaseBodySkill.condition` 表达；条件不成立 = 连"是否发动"都不问。
# - 选项步两项恒可执行（满体力时"回复 0 点"也合法）⇒ 闭集恒非空、发动后不给 Pass。
# - 主体逐座位压帧（回复 / 失去体力各一条），座位序 = **从使用者开始的行动顺序**
#   （账本「默认读法（二）④」+ §4.16 的"从当前回合角色开始、按逆时针"）。
# ---------------------------------------------------------------------------

QINYIN = hero_skill_id("shen_zhouyu_mobile", "qinyin")
"""神周瑜移动版【琴音】的全限定技能 id。"""

QINYIN_RECOVER = "qinyin_recover"
"""选项 1：令所有角色各回复 1 点体力。"""

QINYIN_LOSE = "qinyin_lose"
"""选项 2：令所有角色各失去 1 点体力。"""

QINYIN_HAND_DISCARDS_KEY = "discard_phase_hand_discards"
"""阶段体帧私有：本弃牌阶段**实际弃置的手牌数**（`Engine._phase_discard` 计数）。"""

_QINYIN_DONE = "qinyin_done"
"""帧私有：本技能已经结算过的座位（重入既不重做也不漏做）。"""

_QINYIN_ORDER = "qinyin_order"
"""帧私有：本次发动的结算名单（首次进入时定格，见 :func:`_qinyin_effect`）。"""


def _qinyin_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**我的**弃牌阶段内我弃置过**至少两张手牌**。

    "是不是我的阶段"自校一次（``frame.source == 持有者``，与 `tests/test_hero_catalog.py`
    的"关于我"机械检查同一口径——阶段体的 ``available`` 另挡一层是防御而非替代）；
    张数读帧私有计数（``_phase_discard`` 累积、``_phase_discard_end`` 并入阶段体帧）。
    条件为假 ⇒ 派发环连"是否发动"都不问。
    """
    del host
    owner = frame.data.get("source")
    matched = frame.data.get(MATCH_OWNER_KEY)
    if matched is not None and owner != matched:
        return False
    return int(frame.data.get(QINYIN_HAND_DISCARDS_KEY, 0)) >= 2


def _qinyin_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：按选项逐座位压 `RECOVER` / `LIFE_DECREASE` 子帧（座位序 = 行动顺序）。

    重入：每压一帧就 ``return "stay"``，已结算的座位记进帧私有 ``qinyin_done``
    （回复的钩子 / 濒死 / 死亡都可能让本效果挂起）。名单在**首次进入时定格**
    （``qinyin_order`` 没写就按当时的存活座位算）——结算中有人阵亡不改变名单。
    """
    owner = frame.data.get("skill_owner")
    if owner is None:
        return None
    choice = str(answers.get("option") or "")
    order = frame.data.get(_QINYIN_ORDER)
    if order is None:
        order = seats_from(host.state, int(owner), len(host.state.players))
        frame.data[_QINYIN_ORDER] = tuple(order)
    done = tuple(frame.data.get(_QINYIN_DONE, ()))
    for seat in order:
        if seat in done:
            continue
        frame.data[_QINYIN_DONE] = (*done, seat)
        if choice == QINYIN_RECOVER:
            host.push_frame(
                FrameKind.RECOVER,
                {
                    "source": owner,
                    "targets": (seat,),
                    "card": None,
                    "use_timing": frame.data.get("use_timing"),
                    "amount": 1,
                },
                timing=Timing.RECOVER_VALUE,
            )
        else:
            # 失去体力 ≠ 受到伤害：只压 LIFE_DECREASE 帧（无来源、无属性）。
            host.push_frame(
                FrameKind.LIFE_DECREASE,
                {"target": seat, "source": None, "amount": 1},
                timing=Timing.LIFE_DECREASE_BEFORE,
            )
        return "stay"
    return None


QINYIN_FLOW = SkillFlow(
    name="qinyin",
    steps=(PickOption(options=(QINYIN_RECOVER, QINYIN_LOSE), optional=False, key="option"),),
    effect=_qinyin_effect,
)
"""【琴音】的一步流水线：选一项 → 逐座位回复 / 失去 1 点体力。"""

_QINYIN = PhaseBodySkill(
    hero_id="shen_zhouyu_mobile",
    short_id="qinyin",
    timing=Timing.PHASE_DISCARD_END,
    flow=QINYIN_FLOW,
    condition=_qinyin_condition,
    requirement=(
        "弃牌阶段结束时，若你于此阶段内弃置过至少两张手牌，"
        "你可以选择一项：令所有角色各回复1点体力，或令所有角色各失去1点体力"
    ),
)

# ---------------------------------------------------------------------------
# 颜良文丑【双雄】（huo-wolong-yanliang 票 02，火包·OL）
#
#     摸牌阶段，你可以改为进行一次判定，然后你获得此判定牌。若如此做，本回合你可以将
#     任意一张与判定结果颜色不同的手牌当【决斗】使用。
#
# 形状 = `shapes.md` §2 第 53 行「钩子 + 修饰器」：**判定后写一个座位级事实（颜色），
# 再由 `CANDIDATES` 修饰器在查询期读它**。载体是阶段体（`PHASE_DRAW`），故仍用
# `PhaseBodySkill` 声明；转化那半是同一个 `SkillDef` 上的第二条修饰器（与【连环】同姿态）。
# ---------------------------------------------------------------------------

SHUANGXIONG = hero_skill_id("yanliang_wenchou", "shuangxiong")
"""火包·OL 颜良文丑【双雄】的全限定技能 id。"""

_STAGE_KEY = "shuangxiong_stage"
"""帧私有：本次发动的进度（``judge`` → ``gain`` → 完成）。"""

_GAINED_KEY = "shuangxiong_gained"
"""帧私有：本次拿到的判定牌（续跑不重拿）。"""


def _shuangxiong_effect(host: Any, frame: Frame, answers: Any) -> Any:
    """提交点：整段替换本次摸牌 → 一次判定 → 获得判定牌 → 记下判定结果的颜色。

    「改为」是**整段替换**（与【涉猎】同一条：先 ``set_draw_phase_count(owner, 0)``），
    判定走技能自己的判定帧（``open_skill_judgment`` + ``RUNNING_SKILL_KEY``：阶段体帧的
    ``frame_id`` 恒 0，回写靠"正在跑哪个技能"找发起帧，见 `Engine.note_skill_judgment`）。

    「获得此判定牌」抄【洛神】的形状：判定帧出栈时那张牌已进弃牌堆，故按 ``locate`` 守卫
    搬回手牌（被【天妒】一类先拿走的则不搬，免得对同一张牌搬出 ``hand → hand`` 的幻影
    移动）。「本回合」的作用域由
    :meth:`~sgs.engine.Engine.set_shuangxiong_judge_color` 写进座位状态、回合末清。
    """
    owner = frame.data["skill_owner"]
    stage = frame.data.get(_STAGE_KEY)
    if stage is None:
        # 「改为」：先把本阶段的摸牌数改成 0，再压判定帧（顺序与【涉猎】一致）。
        host.set_draw_phase_count(owner, 0)
        frame.data[_STAGE_KEY] = "judge"
        host.open_skill_judgment(
            frame, owner, frame.data.get(RUNNING_SKILL_KEY, SHUANGXIONG)
        )
        return "stay"
    if stage == "judge":
        judge_card = frame.data.pop(SKILL_JUDGMENT_CARD_KEY, None)
        frame.data.pop("skill_result", None)
        if judge_card is None:
            frame.data[_STAGE_KEY] = "done"
            return None
        frame.data[_GAINED_KEY] = judge_card
        frame.data[_STAGE_KEY] = "gain"
        stage = "gain"
    if stage == "gain":
        card = frame.data[_GAINED_KEY]
        frame.data[_STAGE_KEY] = "done"
        if locate(host.state, card.card_id) != ZoneRef(Zone.HAND, owner):
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
        color = color_of_suit(card.suit)
        if color is not None:
            host.set_shuangxiong_judge_color(owner, color)
    return None


SHUANGXIONG_FLOW = SkillFlow(name="shuangxiong", steps=(), effect=_shuangxiong_effect)
"""【双雄】的零步流水线：[是否发动] → 判定 → 获得判定牌 → 记下颜色（无步骤）。"""


def _shuangxiong_candidate(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """【双雄】的转化候选：本回合判定结果颜色**不同**的手牌，一张当【决斗】**使用**。

    三条早退与【乱击】的 bespoke 回调同形（``ctx.card is not None``／``ctx.owner !=
    ctx.subject``／只认出牌阶段）：转化入口只在出牌阶段的询问里补候选，别的地方不产生。

    **已知偏离**（账本 06 §5.2 有一行 `偏离一手`）：这个候选来源挂在**技能实例**上，
    故「失去所有武将技能」之后本回合的转化入口随之消失——一手（《规则集》第二章 L195）
    要求的是"依然能执行"。座位状态 ``shuangxiong_judge_color`` 不被撤回，缺的是"与实例
    解耦的候选来源"这条原语（架构缺口票 06）。

    **不走 `ConversionSkill` 声明**：那份声明的 ``predicate`` 只拿得到牌 id（
    ``(state, card_id) -> bool``），读不出"本回合的判定颜色"这个**座位级**事实。

    颜色谓词与 :func:`~sgs.skills.heroes._convertible` 同一处读法：牌面事实取
    ``CardRef.color``，空则按花色派生（实体牌的 ``color`` 字段通常是空的）。
    空集（判定红 + 手里全红）⇒ ``sources=()`` ⇒ 零候选、清单不出现（`edge-cases.md` 的 G1）。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return current
    if ctx.scope is not CandidateScope.PLAY:
        return current
    color = state.player(ctx.subject).shuangxiong_judge_color
    if color is None:
        return current
    sources = tuple(
        (card_id,)
        for card_id in state.player(ctx.subject).hand
        if color_of_suit(state.card(card_id).suit) is not color
    )
    if not sources:
        return current
    return (
        *current,
        *virtual_candidates(
            state,
            ctx,
            target_type_id="duel",
            sources=sources,
            scopes=(CandidateScope.PLAY,),
        ),
    )


_SHUANGXIONG = PhaseBodySkill(
    # 颜良文丑【双雄】：摸牌阶段，你可以改为进行一次判定，然后你获得此判定牌；
    # 若如此做，本回合你可以将一张与判定结果颜色不同的手牌当【决斗】使用。
    hero_id="yanliang_wenchou",
    short_id="shuangxiong",
    timing=Timing.PHASE_DRAW,
    flow=SHUANGXIONG_FLOW,
    modifiers=(Modifier(ModifierPoint.CANDIDATES, _shuangxiong_candidate),),
    virtual_type_ids=("duel",),
)
"""【双雄】的声明（阶段体 + 随身 `CANDIDATES` 修饰器 + 产物牌名【决斗】）。"""


PHASE_BODY_SKILLS: tuple[PhaseBodySkill, ...] = (
    _YINGZI,
    _JIE_YINGZI,
    _BIYUE,
    _TUXI,
    _JIE_TUXI,
    _LUOYI,
    _JIE_LUOYI,
    _KEJI,
    _KEJI_JIE,
    _TISHEN,
    _JIE_BIYUE,
    _JUSHSOU,
    _BOTU,
    _XUNXUN,
    _SHELIE,
    _SHENSU,
    _QINYIN,
    _SHUANGXIONG,
)
"""L 族十三条技能的逐字声明（票 12 §落地表第 17/27 行 + M6 票 01 的【突袭】/【裸衣】、
票 03 的【克己】、票 05 的【替身】、票 06 的界【克己】、票 07 的界【闭月】、
票 08 的界【英姿】、票 11 的【博图】、票 14 的界【突袭】/界【裸衣】、票 15 的界【恂恂】
——【博图】挂在**回合结束时**而不是阶段体上，派发机制相同，见
:meth:`Engine._dispatch_phase_body`）。"""

_REQUIREMENTS: dict[str, str] = {
    "yingzi": "多摸一张牌",
    "biyue": "摸一张牌",
    "tuxi": "放弃摸牌，改为获得至多两名其他角色的各一张手牌",
    "luoyi": "少摸一张牌；本回合你的【杀】或【决斗】造成的伤害 +1",
    "keji": "跳过弃牌阶段",
    "tishen": "将体力回复至体力上限，然后摸以此法回复的体力值数张牌",
    "botu": "执行一个额外的回合",
    "xunxun": "观看牌堆顶四张牌，两张置于牌堆顶、其余置于牌堆底",
    "jushou": "摸三张牌，然后翻面",
    "shelie": "亮出牌堆顶的五张牌，然后获得其中每种花色的牌各一张",
    "shensu": (
        "回合开始时，你可以选择一至两项：【1】跳过判定阶段和摸牌阶段；"
        "【2】跳过出牌阶段并弃置一张装备牌。你每以此法选择一项，"
        "便视为你使用一张无距离限制的普通【杀】"
    ),
    "shuangxiong": (
        "改为进行一次判定，然后你获得此判定牌；本回合你可以将一张与判定结果颜色不同的"
        "手牌当【决斗】使用"
    ),
    "qinyin": (
        "弃牌阶段结束时，若你于此阶段内弃置过至少两张手牌，"
        "你可以选择一项：令所有角色各回复1点体力，或令所有角色各失去1点体力"
    ),
}
"""L 族八条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""

_STEP_REQUIREMENTS: dict[str, dict[str, str]] = {
    # M6 票 01【突袭】：座位步的询问 ``skill_id`` 是 ``tuxi:seat:<序号>`` ⇒ 键 = ``seat``。
    "tuxi": {"seat": "选择至多两名其他角色（各获得其中一张手牌）"},
    # 用户裁定：界【裸衣】亮出三张后的第二次询问（``luoyi:gain``）⇒ 键 = ``gain``。
    "luoyi": {
        "gain": "选择是否获得亮出牌里的基本牌 / 武器牌 /【决斗】；"
        "不获得则三张置入弃牌堆，正常摸两张"
    },
    # 神吕蒙移动版【涉猎】：选牌面是"亮出的五张"（处理区，公开）⇒ 键 = "card"。
    "shelie": {
        "card": "获得其中每种花色的牌各一张（每种花色至多一张，可一张不选；"
        "未获得的置入弃牌堆）"
    },
    # 风包夏侯渊【神速】（票 01）：选项步 / 手写选目标 / 弃装备三处逐步文案。
    "qinyin": {
        "option": "选择一项：令所有角色各回复1点体力 / 令所有角色各失去1点体力",
    },
    "shensu": {
        "items": "选择一至两项：【1】跳过判定阶段和摸牌阶段；【2】跳过出牌阶段并弃置一张装备牌",
        "target": SHENSU_TARGET_WORDING,
        "card": "弃置一张装备牌（手牌或装备区）",
    },
}
"""多步阶段体技能的逐步文案（M6 票 01）；键 = 短 id → 步骤名 → 文案。"""


def phase_body_activation_plans() -> dict[str, Any]:
    """L 族的发动形态注册表（由 :data:`sgs.skills.catalog.ACTIVATION_PLANS` 末尾并入）。

    六条技能的形态同形：**技能持有者就是当前回合角色**才 ``available``——阶段体帧的
    ``source`` 由 :meth:`Engine._dispatch_phase_body` 设成当前回合角色。别的人摸牌 /
    结束阶段里，这条实例连"是否发动"都不问（与【观星】的 ``_guanxing_plan`` 同一条理由）。
    声明了 :attr:`PhaseBodySkill.available` 的（【突袭】"有牌可拿"）再叠一层前提。
    限定技的"已用"门在派发环（:func:`sgs.skills.catalog.dispatch_timing`）里，不在这里
    重复——本函数只回答"这一问在不在这个人的阶段里"。
    """

    def plan_for(skill: PhaseBodySkill) -> Any:
        def plan(state: GameState, frame: Frame, instance: Any) -> ActivationPlan:
            if frame.data.get("source") != instance.owner:
                return ActivationPlan(available=False)
            if skill.available is not None and not skill.available(state, instance.owner):
                return ActivationPlan(available=False)
            return ActivationPlan()

        return plan

    return {skill.skill_id: plan_for(skill) for skill in PHASE_BODY_SKILLS}


def phase_body_skill_definitions() -> tuple[SkillDef, ...]:
    """L 族十条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    除界【英姿】外都**非锁定**（技能面写"你可以"）⇒ 先问"是否发动"再执行；界【英姿】
    牌面写"锁定技" ⇒ ``locked=True``、不问即执行（M6 票 08）。``carrier = HERO``
    ⇒ 优先级档由载体派生；``skill_type`` 取自声明（缺省 ``NORMAL``，【替身】是
    ``LIMITED``——锁定 / 非锁定与它正交）。
    挂点是**阶段体**（``Hook(timing)``），消费方是票 10 的 ``_dispatch_phase_body``；
    【裸衣】/ 界【英姿】各带一条随身修饰器（随实例存亡）。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=skill.locked,
            carrier=CarrierKind.HERO,
            skill_type=skill.skill_type,
            hooks=(Hook(skill.timing, flow=skill.flow, condition=skill.condition),),
            modifiers=skill.modifiers,
            deck_arrange=skill.deck_arrange,
            requirement=skill.requirement or _REQUIREMENTS[skill.short_id],
            step_requirements=_STEP_REQUIREMENTS.get(skill.short_id),
            per_round_limit=skill.per_round_limit,
            virtual_type_ids=skill.virtual_type_ids,
            virtual_source_count=skill.virtual_source_count,
        )
        for skill in PHASE_BODY_SKILLS
    )
