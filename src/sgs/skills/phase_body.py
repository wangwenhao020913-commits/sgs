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
张辽【突袭】     非锁定    ``PHASE_DRAW``             放弃摸牌，改为获得至多两名其他角色各一张手牌（§九 L948）
许褚【裸衣】     非锁定    ``PHASE_DRAW``             少摸一张；本回合【杀】/【决斗】伤害 +1（§九 L952）
吕蒙【克己】     非锁定    ``PHASE_DISCARD_START``    本回合未出【杀】⇒ 跳过弃牌阶段（§九 L1011）
界张飞【替身】   **限定**  ``PHASE_PREPARE``         体力回复至上限，摸以此法回复的体力值数张牌（§十九 L2079–L2081）
界李典【恂恂】   非锁定    ``PHASE_DRAW``             观看牌堆顶四张，两张置顶、其余置底（§十九 L2051–L2054）
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

from sgs.data import Category, SkillType, Suit, card_category, hero_skill_id
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    behavior_for,
    is_slash,
    place_on_deck,
)
from sgs.resolution import Control, Frame
from sgs.state import FrameKind, GameState, MoveReason, SlotKind, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import (
    ActivationPlan,
    HeroSkillDecl,
    Hook,
    SkillDef,
    mark_round_skill_used,
    round_skill_uses,
)
from .flow import (
    Arrange,
    DeckTopArrange,
    FixedSplit,
    FlowAnswers,
    PickSeats,
    SkillFlow,
    watched_cards,
    zero_step_effect,
)

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
    "PhaseBodySkill",
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

    界周瑜【英姿】是锁定技（§十九 L2152–L2154 牌面写"锁定技"），故它**不问**"是否发动"、
    满足条件即执行（派发环 ``forced = locked or AWAKENING``）。与 ``skill_type`` 正交：
    锁定 / 非锁定管"要不要问"，``skill_type`` 管技能规则类型（普通 / 限定 / 觉醒）。
    """
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
    # 界周瑜【英姿】（§十九 L2152–L2154）：锁定技，①摸牌阶段你多摸一张牌；
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
    """界貂蝉【闭月】的提交点：**当时**有手牌摸 1、没有手牌摸 2（§十九 L2199）。

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
    # 界貂蝉【闭月】（§十九 L2199）：结束阶段，若你有 / 没有手牌，你可以摸一 / 两张牌。
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
"""界李典【恂恂】的全限定技能 id（§十九 L2051–L2054）。"""

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
    # 界李典【恂恂】（§十九 L2051–L2054）：摸牌阶段开始时，你可以观看牌堆顶的四张牌，
    # 然后将其中的两张牌置于牌堆顶，其余置于牌堆底。
    hero_id="jie_lidian",
    short_id="xunxun",
    timing=Timing.PHASE_DRAW,
    flow=XUNXUN_FLOW,
    deck_arrange=XUNXUN_ARRANGE,
)


# ---------------------------------------------------------------------------
# 张辽【突袭】（M6 P0 票 01）：放弃摸牌，改为获得至多两名其他角色各一张手牌
# ---------------------------------------------------------------------------

TUXI_TARGETS = 2
"""「至多两名其他角色」的上限（§九 L948–949）。"""

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
# 角色的各一张手牌（§十九 L2030–L2031）。
#
# 与标准版（"放弃摸牌，改为获得至多两名…"）**共用**触发时机、可拿的座位池 / 排除已选
# 的池、以及同一条一步座位流水线的**形状**（1–2 名、可少选 ⇒ X 就是选了几名）；差别只在
# 提交点：标准版把本阶段摸牌数改成 0，界版**按实际拿到的张数**减（少摸 X 张）。
# ---------------------------------------------------------------------------

JIE_TUXI = hero_skill_id("jie_zhangliao", "tuxi")
"""界张辽【突袭】的全限定技能 id（§十九 L2030–L2031）。"""

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
"""【裸衣】的伤害增量（§九 L952–954：使用【杀】或【决斗】造成的伤害 +1）。"""

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
# 回合开始，你为伤害来源的【杀】或【决斗】造成的**基础伤害值** +1（§十九 L2035–L2039）。
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
"""界许褚【裸衣】的全限定技能 id（§十九 L2035–L2039）。"""

JIE_LUOYI_REVEAL = 3
"""「亮出牌堆顶的三张牌」的张数（§十九 L2035）。"""

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
    """这三张里的这一张能不能拿：**基本牌** ∨ **武器牌** ∨ 【决斗】（§十九 L2035–L2036）。

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
    # 吕蒙【克己】：若你于出牌阶段内没有使用或打出过【杀】，你可以跳过弃牌阶段（§九 L1011）。
    hero_id="lvmeng",
    short_id="keji",
    timing=Timing.PHASE_DISCARD_START,
    flow=KEJI_FLOW,
    available=_keji_available,
)
_KEJI_JIE = PhaseBodySkill(
    # 界吕蒙【克己】（M6 票 06）：牌面与标准版**逐字相同**（§十九 L2135），故共用同一条
    # 流水线与同一条"本阶段没用过【杀】"前提；界版只是另一个武将 id（同一真名的两版不并存）。
    hero_id="jie_lvmeng",
    short_id="keji",
    timing=Timing.PHASE_DISCARD_START,
    flow=KEJI_FLOW,
    available=_keji_available,
)


# ---------------------------------------------------------------------------
# 界吕蒙【博图】（M6 票 11）：每轮限 X 次，回合结束时，若本回合置入弃牌堆的牌中包含
# 四种花色，你可以执行一个额外的回合（§十九 L2140–L2142）。
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
"""【博图】「至多 3」的硬上限（§十九 L2141）：X = ``min(存活角色数, 3)``。"""

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
# 规则原文（§十九 L2079–L2081）：
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
    # 体力值数张牌（§十九 L2079–L2081）。
    hero_id="jie_zhangfei",
    short_id="tishen",
    timing=Timing.PHASE_PREPARE,
    flow=TISHEN_FLOW,
    skill_type=SkillType.LIMITED,
)


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
            hooks=(Hook(skill.timing, flow=skill.flow),),
            modifiers=skill.modifiers,
            deck_arrange=skill.deck_arrange,
            requirement=skill.requirement or _REQUIREMENTS[skill.short_id],
            step_requirements=_STEP_REQUIREMENTS.get(skill.short_id),
            per_round_limit=skill.per_round_limit,
        )
        for skill in PHASE_BODY_SKILLS
    )
