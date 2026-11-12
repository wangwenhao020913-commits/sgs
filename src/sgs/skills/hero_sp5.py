r"""SP武将组5 的武将技能：马云騄【凤魄】、潘濬【观微】【公清】
（``sp5`` 包；sp5-mayunlu-panjun 票 01 / 02 / 03）。

本模块只装**SP武将组5** 的武将技能声明；族级原语（钩子 / 步骤流水线 / 修饰器 / 摸牌 /
搬牌 / 座位事实）一律复用既有实现，不新增机制。

**模块内的技能归属**（一条技能住哪一族，就住那个模块）：

- 马云騄【马术】= 纯修饰器，住 :mod:`sgs.skills.target_change`（与马超 / 界马超 /
  庞德移动版**逐字同文**，同一份 ``DISTANCE`` 回调，第四条同形声明）；
- 马云騄【凤魄】= 本模块（``USE_AFTER_DESIGNATE_TARGET`` 钩子 + 一步选项 + ``DAMAGE_DEAL_AMOUNT``
  修饰器 + ``RESOLVE_AFTER_END`` 清账）；
- 潘濬【观微】= 本模块（``PHASE_PLAY_END`` 阶段体钩子 + 一步选牌 + 摸两张 + 阶段级调度
  ``Engine.schedule_extra_play_phase``）；
- 潘濬【公清】= 本模块（锁定技 + 两个修饰器：``DAMAGE_CAP`` / ``DAMAGE_TAKE_AMOUNT``）。

牌面权威 = 用户录入的 OL 现行文本（``D:\SGS_HEROS\SP武将组5\<武将>\技能.txt``）；移动版
官网的【凤魄】是**另一版**（回合内第一张 / X 只数其手牌 / 观其手牌），按账本 L963
「技能文本以 OL 现行为准」只登记差异、不实现（票 01 §0.2）。

用户 2026-11 签的裁定（票 01 / 02 / 03 的「签字轮」）：

- **Q0**：数据面 = 新表 ``sp5``（``pack_id`` / ``version`` 同值）、真名新增 2、默认池
  98/64 → 100/66；
- **Q1**：平台口径 = OL（用户 ``技能.txt``），不加移动版后缀；
- **Q2**：凤魄「每阶段每项限一次」= **每一个角色的每一个阶段**（不只出牌阶段、不只自己
  的阶段；其他角色的技能可能在非出牌阶段令马云騄使用【杀】/【决斗】）⇒ 用**全局阶段
  序号**（``GameState.phase_seq``）惰性复位；
- **Q2b**：凤魄的 X = **双方**手牌 + 装备区里 ♦ 牌数量之和（用户逐字重申）；
- **Q4**：只剩一项时**置灰**已用项（前端 ``OPTION_PAIRS`` + 非候选按钮 disabled）；
- **Q5**：X = 0 时两项仍可发动（允许浪费，且算用过）；
- **Q6 / Q7**：公清「<3 调整为 1」= ``DAMAGE_CAP`` 封顶（加完之后 ``min(·,1)``）；来源为
  ``None`` / 攻击范围 = 3 ⇒ 不调整；
- **Q9 / Q10 / Q11**：观微的框架缺口**并入本批**、使用过的牌花色记录住**引擎侧**、
  额外出牌阶段**重置**【杀】的使用次数（「每出牌阶段限一次」不是每回合）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, Suit, hero_skill_id
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierPoint,
    attack_range,
    behavior_for,
    is_slash,
)
from sgs.resolution import Frame, Tag, add_tag
from sgs.state import FrameKind, GameState, MoveReason, Phase, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    Hook,
    SkillDef,
    _move_by_source,
    mark_skill_used_this_turn,
    skill_used_this_turn,
)
from .flow import FlowAnswers, PickCards, PickOption, PickSeats, SkillFlow
from .move_after import move_event

__all__ = [
    "FENGPO",
    "FENGPO_DAMAGE",
    "FENGPO_DRAW",
    "FENGPO_FLOW",
    "FENGPO_OPTIONS",
    "GONGQING",
    "GUANWEI",
    "GUANWEI_FLOW",
    "SONGSANG",
    "SONGSANG_FLOW",
    "TUNJIANG",
    "TUNJIANG_FLOW",
    "WENJI",
    "WENJI_FLOW",
    "ZHANJI",
    "sp5_skill_definitions",
]

# ---------------------------------------------------------------------------
# 马云騄【凤魄】（票 01）
#
#     每阶段每项限一次，当你使用【杀】或者【决斗】指定一名其他角色为唯一目标后，你可以
#     选择一项：【1】摸X张牌；【2】令此牌的基础伤害值+X（X为你与其的手牌和装备牌区的♦️牌数）。
#
# 两段的落点（各只有一处真源）：
#
# - **触发** = ``USE_AFTER_DESIGNATE_TARGET`` 钩子（目标已定、尚未结算）。条件 = 自己是
#   使用来源 ∧ 产物是【杀】族或【决斗】∧ ``targets`` **恰一张**且不是自己 ∧ 本阶段两项
#   里至少还有一项没用（都用了就**连问都不发**）。
# - **「每阶段每项限一次」** = 座位事实 ``Player.fengpo_used``：``(阶段序号, 位掩码)``。
#   "每阶段"按用户 2026-11 裁定的**字面**：每一个角色的每一个阶段开始时都重置（不只出牌
#   阶段、不只自己的阶段）⇒ 记录里存**全局** ``GameState.phase_seq``（:meth:`Engine._step_turn`
#   在每次"新步进入且是阶段起点"时 +1），序号一变即视作两项都未用（惰性复位，不需要在
#   每个阶段起点逐个清字段）。
# - **选项 1 摸 X** = ``draw_for_hand``（X 在首次执行时定格进帧私有，重入不重算）。
# - **选项 2 加伤** = ``Engine.set_fengpo_bonus`` 记 ``(牌 id, X)``，由同一条声明的
#   ``DAMAGE_DEAL_AMOUNT`` 修饰器在伤害帧读。**卡牌级**：只认 ``ctx.card.card_id``，不要求
#   来源是持有者——牌面写的是「此牌」，没有「你为伤害来源」（对照账本 L978：界【裸衣】写
#   了「你为伤害来源」，所以它输掉自己使用的【决斗】时**不**加）。清理 = ``RESOLVE_AFTER_END``
#   清扫钩子（被【闪】抵消 / 伤害被防止时也要清）+ 回合末兜底。
# ---------------------------------------------------------------------------

FENGPO = hero_skill_id("mayunlu", "fengpo")
"""马云騄【凤魄】的全限定技能 id。"""

FENGPO_DRAW = "fengpo:draw"
"""选项 1（摸 X 张牌）的闭集 id。"""

FENGPO_DAMAGE = "fengpo:damage"
"""选项 2（令此牌的基础伤害值 +X）的闭集 id。"""

FENGPO_OPTIONS: tuple[str, str] = (FENGPO_DRAW, FENGPO_DAMAGE)
"""【凤魄】的选项闭集（顺序即询问里的顺序；两项都必须列出来——已用项由 ``disabled`` 置灰）。"""

_FENGPO_DRAW_BIT = 0b1
"""``Player.fengpo_used`` 的位 0：本阶段选项 1 已用过。"""

_FENGPO_DAMAGE_BIT = 0b10
"""``Player.fengpo_used`` 的位 1：本阶段选项 2 已用过。"""

_FENGPO_BOTH = _FENGPO_DRAW_BIT | _FENGPO_DAMAGE_BIT
"""两项都用过 ⇒ 这条技能在本阶段连"是否发动"都不问。"""

_FENGPO_DRAWN = "fengpo_drawn"
"""帧私有：本次发动的摸牌段已经跑过（重入不重摸）。"""

_FENGPO_X = "fengpo_x"
"""帧私有：本次发动的 X（首次执行时定格；摸牌会改手牌，重入不得重算）。"""


def _fengpo_target(frame: Frame) -> int | None:
    """本次使用的**唯一目标**；不是"恰一名其他角色"时返回 ``None``。

    判据与界【谦逊】的「唯一目标」（``src/sgs/skills/hero_zone.py``）同形：多目标
    （方天画戟 3 目标等）不是"唯一目标"，整条技能不触发。
    """
    targets = tuple(frame.data.get("targets", ()))
    if len(targets) != 1:
        return None
    return int(targets[0])


def _fengpo_mask(state: GameState, seat: int) -> int:
    """``seat`` 本阶段【凤魄】两项的已用位（序号不是真前就视作 0）。"""
    record = state.player(seat).fengpo_used
    if record is None or record[0] != state.phase_seq:
        return 0
    return int(record[1])


def _mark_fengpo(state: GameState, seat: int, bit: int) -> None:
    """落「本阶段这一项已用」（幂等；序号取**当下**的全局阶段序号）。"""
    state.player(seat).fengpo_used = (state.phase_seq, _fengpo_mask(state, seat) | bit)


def _fengpo_condition(host: Any, frame: Frame) -> bool:
    """触发条件：自己使用【杀】/【决斗】指定**唯一**其他目标 ∧ 本阶段至少还有一项可用。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    card = frame.data.get("card")
    if card is None:
        return False
    if not (is_slash(behavior_for(card)) or card.type == "duel"):
        return False
    target = _fengpo_target(frame)
    if target is None or target == owner:
        return False
    return _fengpo_mask(host.state, int(owner)) != _FENGPO_BOTH


def _fengpo_disabled(
    state: GameState, frame: Frame, owner: int, answers: FlowAnswers
) -> tuple[str, ...]:
    """选项步的**置灰**项：本阶段已经用过的那一项（列出但不可选，中文标签才看得见）。"""
    del frame, answers
    mask = _fengpo_mask(state, owner)
    return tuple(
        option
        for option, bit in (
            (FENGPO_DRAW, _FENGPO_DRAW_BIT),
            (FENGPO_DAMAGE, _FENGPO_DAMAGE_BIT),
        )
        if mask & bit
    )


def _fengpo_x(state: GameState, owner: int, target: int) -> int:
    """X = 双方**手牌 ∪ 装备区**里 ♦（方块）牌的张数之和（用户 2026-11 逐字重申）。

    判定区**不**计入（账本 L864：区域没写时默认手牌 + 装备、判定区不默认包含）。
    """
    total = 0
    for seat in (owner, target):
        player = state.player(seat)
        for card_id in (*player.hand, *player.equip):
            if state.card(card_id).suit == Suit.DIAMOND.value:
                total += 1
    return total


def _fengpo_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：按选项 1 / 2 结算（两项各自落"本阶段已用"位，幂等）。"""
    owner = int(frame.data["skill_owner"])
    target = _fengpo_target(frame)
    assert target is not None, "钩子条件已保证唯一目标"
    choice = str(answers["option"])
    state = host.state
    if _FENGPO_X not in frame.data:
        frame.data[_FENGPO_X] = _fengpo_x(state, owner, target)
    amount = int(frame.data[_FENGPO_X])
    if choice == FENGPO_DRAW:
        _mark_fengpo(state, owner, _FENGPO_DRAW_BIT)
        if frame.data.get(_FENGPO_DRAWN):
            return None
        frame.data[_FENGPO_DRAWN] = True
        control = host.draw_for_hand(frame, owner, amount)
        if control == "stay":
            return "stay"
        return None
    _mark_fengpo(state, owner, _FENGPO_DAMAGE_BIT)
    card = frame.data.get("card")
    if card is not None:
        host.set_fengpo_bonus(owner, int(card.card_id), amount)
    return None


FENGPO_FLOW = SkillFlow(
    name="fengpo",
    steps=(
        # 选项步：两项恒列出（已用项由 ``disabled`` 置灰）；"可以"的放弃出口在**触发问**
        # 那一层（``Hook`` 的非锁定问），故这一步 ``optional=False``（选了就必答）。
        PickOption(
            options=FENGPO_OPTIONS,
            disabled=_fengpo_disabled,
            optional=False,
            key="option",
        ),
    ),
    effect=_fengpo_effect,
)
"""【凤魄】的单步流水线：选择一项 → 结算（摸 X / 记账 +X）。"""


def _fengpo_damage_bonus(
    state: GameState, ctx: Any, current: Any
) -> Any:
    """``DAMAGE_DEAL_AMOUNT``：本次使用记过账的那张牌，伤害 +X（**卡牌级**）。

    只认 ``(持有者的记账, ctx.card.card_id)`` 这一对：不要求 ``ctx.owner == ctx.subject``
    （牌面写「此牌的基础伤害值」，没有「你为伤害来源」）。传导帧（``chain=True``）不派发
    ``DAMAGE_DEAL_AMOUNT``（``damage_amount`` 的既有口径），故不会重复加。
    """
    if ctx.card is None:
        return current
    record = state.player(ctx.owner).fengpo_bonus
    if record is None or record[0] != int(ctx.card.card_id):
        return current
    return current + record[1]


def _fengpo_cleanup_condition(host: Any, frame: Frame) -> bool:
    """清扫钩子的条件：这次使用就是我记过账的那张牌（不问、只清）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    record = host.state.player(int(owner)).fengpo_bonus
    if record is None:
        return False
    card = frame.data.get("card")
    return card is not None and int(card.card_id) == record[0]


def _fengpo_cleanup(host: Any, frame: Frame) -> None:
    """``RESOLVE_AFTER_END``（一次使用结算完后）：清掉本次使用的记账。"""
    owner = frame.data.get("skill_owner")
    if owner is not None:
        host.clear_fengpo_bonus(int(owner))
    return None


# ---------------------------------------------------------------------------
# 潘濬【观微】（票 02 / 03）
#
#     每回合限一次，一名角色的出牌阶段结束时，若其本回合使用过至少两张牌且这些牌花色
#     均相同，你可以弃置一张牌，令其摸两张牌并于此阶段结束后执行一个额外的出牌阶段。
#
# 三段各只有一处真源：
#
# - **条件**读 ``Player.turn_card_suits``（引擎在 ``start_use`` 逐次记，用户 Q10 = 引擎侧；
#   打出不算）：「至少两张」= 长度 ≥ 2；「花色均相同」= 全部相等且**没有** ``None``
#   （多源转化 / 纯虚拟牌花色恒无 ⇒ 按账本 L896 的 fail-closed 口径"不构成均相同"）。
# - **每回合限一次** = `_TURN_USED` 位（``skill_used_this_turn`` / ``mark_skill_used_this_turn``）；
#   作用域 = 一个游戏回合（含别人的回合，账本 L1079）。
# - **效果** = 弃一张自己的牌（手牌 ∪ 装备）→ 令其摸两张 → `schedule_extra_play_phase`。
#   额外的出牌阶段是**阶段级调度**（票 03 的框架原语）：`_phase_play_end` 在"本阶段"清理
#   之后把回合环游标拨回 `_phase_play_start`，新阶段重跑阶段起点（复位 + 派发）与空闲窗口。
# ---------------------------------------------------------------------------

GUANWEI = hero_skill_id("panjun", "guanwei")
"""潘濬【观微】的全限定技能 id。"""

_GUANWEI_DRAWN = "guanwei_drawn"
"""帧私有：摸两张那一段已经跑过（重入不重摸）。"""

_GUANWEI_SCHEDULED = "guanwei_scheduled"
"""帧私有：额外的出牌阶段已经排过（重入不重复叠）。"""


def _guanwei_condition(host: Any, frame: Frame) -> bool:
    """触发条件：本回合 ≥2 张使用牌同花色 ∧ 本回合未发动过 ∧ 自己还有牌可弃。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    seat = frame.data.get("source")
    if seat is None:
        return False
    state = host.state
    if skill_used_this_turn(state, int(owner), GUANWEI):
        return False
    suits = state.player(int(seat)).turn_card_suits
    if len(suits) < 2:
        return False
    first = suits[0]
    if first is None or any(suit != first for suit in suits):
        return False
    player = state.player(int(owner))
    return bool(player.hand or player.equip)


def _guanwei_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：记"本回合已发动" → 弃置成本 → 令其摸两张 → 排一个额外的出牌阶段。"""
    owner = int(frame.data["skill_owner"])
    seat = int(frame.data["source"])
    # 记在**搬牌之前**（搬牌可能内联触发别人的"失去牌后"钩子并挂起；重入是幂等的 OR 位）。
    mark_skill_used_this_turn(host.state, owner, GUANWEI)
    cards = answers["card"]
    assert isinstance(cards, tuple)
    control = _move_by_source(
        host,
        frame,
        cards,
        to_zone=ZoneRef(Zone.DISCARD),
        owner=owner,
        key="guanwei:cost",
    )
    if control == "stay":
        return "stay"
    if not frame.data.get(_GUANWEI_DRAWN):
        frame.data[_GUANWEI_DRAWN] = True
        control = host.draw_for_hand(frame, seat, 2)
        if control == "stay":
            return "stay"
    if not frame.data.get(_GUANWEI_SCHEDULED):
        frame.data[_GUANWEI_SCHEDULED] = True
        host.schedule_extra_play_phase(seat)
    return None


GUANWEI_FLOW = SkillFlow(
    name="guanwei",
    steps=(
        # 「弃置一张牌」：牌前没有区域限定 ⇒ 手牌区 + 装备区（默认读法（一）②），自己的牌
        # （⑤）⇒ 牌面直选（``candidates.js:isOwnZonePick``）。发动后必答（``optional=False``）。
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            optional=False,
            key="card",
        ),
    ),
    effect=_guanwei_effect,
)
"""【观微】的单步流水线：弃一张自己的牌 → 令其摸两张 + 额外的出牌阶段。"""


# ---------------------------------------------------------------------------
# 潘濬【公清】（票 02）
#
#     锁定技，当你受到伤害时，若伤害来源的攻击范围：
#     【小于3】此伤害调整为1；【大于3】令此伤害+1。
#
# 落层（用户 2026-11 签 Q6 / Q7）：
#
# - 「<3 调整为 1」= ``DAMAGE_CAP`` 封顶 ``min(当前, 1)``——与白银狮子同一个挂点，在
#   ``DAMAGE_TAKE_AMOUNT`` 的**加法之后**派发 ⇒ 与【酒】/古锭刀/藤甲的先后无关；移动版
#   官网的显式措辞是「则你只受到 1 点伤害」。
# - 「>3 +1」= ``DAMAGE_TAKE_AMOUNT`` 加式。
# - 「=3」两支都不成立（牌面只写 <3 / >3）；伤害来源为 ``None``（【闪电】一类无来源伤害）
#   没有攻击范围 ⇒ 同样不调整。攻击范围读 :func:`attack_range`（无武器 = 1，**不是**距离）。
# ---------------------------------------------------------------------------

GONGQING = hero_skill_id("panjun", "gongqing")
"""潘濬【公清】的全限定技能 id。"""


def _gongqing_source_range(state: GameState, ctx: Any) -> int | None:
    """伤害来源的额定攻击范围；没有来源 ⇒ ``None``。"""
    if ctx.subject is None or int(ctx.subject) < 0:
        return None
    return attack_range(state, int(ctx.subject))


def _gongqing_cap(state: GameState, ctx: Any, current: Any) -> Any:
    """``DAMAGE_CAP``：来源攻击范围 < 3 ⇒ 伤害封顶 1（加完之后）。"""
    if ctx.owner != ctx.target:
        return current
    reach = _gongqing_source_range(state, ctx)
    if reach is None or reach >= 3:
        return current
    return min(current, 1)


def _gongqing_bonus(state: GameState, ctx: Any, current: Any) -> Any:
    """``DAMAGE_TAKE_AMOUNT``：来源攻击范围 > 3 ⇒ 此伤害 +1。"""
    if ctx.owner != ctx.target:
        return current
    reach = _gongqing_source_range(state, ctx)
    if reach is None or reach <= 3:
        return current
    return current + 1


# ---------------------------------------------------------------------------
# SP庞统【送丧】/ ☆【展骥】（sp5-pangtong-liuqi 票 01；用户 2026-11-12 一次签）
#
#     送丧
#       限定技，当其他角色死亡时，若你已受伤，你可以回复1点体力；若你没有受伤，你可以
#       增加1点体力上限。若如此做，你获得技能"展骥"。
#
#     ☆ 展骥
#       锁定技，当你于出牌阶段内不因此技能摸牌时，你摸一张牌。
#
# 送丧：``DEATH_ON`` 钩子 + ``SkillType.LIMITED`` + 零步流水线（样板 = 庞统移动版【涅槃】；
# 死亡时机的"其他角色"自校抄曹丕移动版【行殇】）。两支按 ``hp < hp_max`` **互斥穷尽**，
# 故不是选项步：受伤 ⇒ 回复 1 点（``RECOVER`` 帧）；未受伤 ⇒ +1 上限（``MAXLIFE`` 帧，
# **不同步体力**）。第三段 ``grant_skill`` 授予 ☆【展骥】——它是本仓**首例**"由限定技
# 授予 ☆"（加载期闭集由票 03 放宽成 ``{觉醒技, 限定技}``）。
#
# 展骥：``MOVE_AFTER`` + ``gains_card`` 家族的**摸牌**判据（``MoveReason.DRAW``；票 24 裁定
# "摸牌也算获得"）。锁定技 ⇒ ``locked=True``、不问（样板 = 界沮授移动版【矢北】）。
# 「不因此技能摸牌」= 帧私有标志：``effect`` 在摸牌**之前**把它写进**同一个帧**，
# ``draw_for_hand`` 把帧传进 ``move_in_frame`` ⇒ 内联派发用的是同一个帧、嵌套期间可见
# （票 01 §3.5 的实现纪律；用户 2026-11-12 重申"摸一张 = 单独摸一张"，不是本次摸牌数 +1）。
# ---------------------------------------------------------------------------

SONGSANG = hero_skill_id("pangtong_sp", "songsang")
"""SP庞统【送丧】的全限定技能 id。"""

ZHANJI = hero_skill_id("pangtong_sp", "zhanji")
"""SP庞统 ☆【展骥】的全限定技能 id（只经 ``grant_skill`` 获得）。"""

_SONGSANG_STAGE = "songsang_stage"
"""帧私有：送丧的执行阶段（``heal`` / ``maxlife`` / ``grant``）——先写再压帧，重入不重做。"""

_SONGSANG_HEAL = "songsang:heal"
_SONGSANG_MAXLIFE = "songsang:maxlife"
_SONGSANG_GRANT = "songsang:grant"

_ZHANJI_GUARD = "zhanji_guard"
"""帧私有：本次摸牌**是展骥自己摸的**（嵌套派发期间置位，摸完清掉）。"""

_ZHANJI_LAST = "zhanji_last"
"""帧私有：已为哪一次摸牌事件追加过那"单独一张"（同一个帧会被多次移动复用 ⇒ 按事件键去重）。"""


def _songsang_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**其他角色**死亡 ∧ 终局未判定（【行殇】的同一条自校，票 01 §2.7）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    dead = frame.data.get("target")
    if dead is None or int(dead) == int(owner):
        return False
    return not host.outcome_decided()


def _push_recover(host: Any, frame: Frame, owner: int) -> None:
    """压一个 ``RECOVER`` 子帧（回复 1 点；满体力时由帧夹住 ⇒ 回复 0）。"""
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": owner,
            "targets": (owner,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "amount": 1,
        },
        timing=Timing.RECOVER_VALUE,
    )


def _songsang_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：按体力状态二选一 → 授予 ☆【展骥】。阶段指针先写再压帧（重入不重做）。"""
    del answers
    owner = int(frame.data["skill_owner"])
    stage = frame.data.get(_SONGSANG_STAGE)
    if stage is None:
        player = host.state.player(owner)
        stage = _SONGSANG_HEAL if player.hp < player.hp_max else _SONGSANG_MAXLIFE
        frame.data[_SONGSANG_STAGE] = stage
    if stage == _SONGSANG_HEAL:
        frame.data[_SONGSANG_STAGE] = _SONGSANG_GRANT
        _push_recover(host, frame, owner)
        return "stay"
    if stage == _SONGSANG_MAXLIFE:
        frame.data[_SONGSANG_STAGE] = _SONGSANG_GRANT
        host.raise_hp_max(owner, 1)
        return "stay"
    if stage == _SONGSANG_GRANT:
        frame.data[_SONGSANG_STAGE] = "songsang:done"
        host.grant_skill(owner, ZHANJI, frame=frame)
        return None
    return None


SONGSANG_FLOW = SkillFlow(name="songsang", steps=(), effect=_songsang_effect)
"""【送丧】的零步流水线：只问"是否发动"，三段效果全住 ``effect`` 里。"""


def _zhanji_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**自己**于**自己的出牌阶段**内**摸牌**（非展骥自己摸的 + 非无点数限制面）。

    三个判据都读帧私有载荷 / 座位事实，不读"引擎今天怎么排"：
    ① ``MoveEvent.reason is MoveReason.DRAW``（**摸牌**——不是"获得牌"：顺手牵羊 / 交出 /
    置入装备区都不算，"摸牌"只认牌堆 → 手牌）；
    ② 落点 = 自己的手牌（``to_zone``）；
    ③ ``state.turn_seat == owner and state.phase is Phase.PLAY``（"于出牌阶段内"按默认读法
    （二）④ 读**自己的**回合，别人的回合里被令摸牌不算）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    event = move_event(frame)
    if event is None or event.reason is not MoveReason.DRAW:
        return False
    if event.to_zone.zone is not Zone.HAND or event.to_zone.seat != owner:
        return False
    state = host.state
    if state.turn_seat != owner or state.phase is not Phase.PLAY:
        return False
    return not frame.data.get(_ZHANJI_GUARD)


def _zhanji_event_key(event: Any) -> tuple[Any, ...]:
    """一次摸牌事件的帧内去重键（同一个帧会被多次移动复用 ⇒ 不能只看"摸过没有"）。"""
    return (event.to_zone.seat, event.card_ids)


def _zhanji(host: Any, frame: Frame) -> Any:
    """摸一张（**单独的一次摸牌**，不是把本次摸牌数 +1）。

    用户 2026-11-12 重申："摸'一张'而非'多摸1张'，所以不是本次摸牌数+1，而是本次摸牌时，
    单独摸一张"。落点 = 一次独立的 ``draw_for_hand``（它自己是一次新的 MOVE 事件）。

    重入纪律（G7）：帧私有 ``_ZHANJI_LAST`` 记住"这一次事件已经追加过"，``_ZHANJI_GUARD``
    在嵌套派发期间置位（展骥自己的那一次摸牌不再触发自己）——两者都按**事件**去重，
    故同一个帧上的下一次摸牌仍会触发。
    """
    owner = int(frame.data["skill_owner"])
    event = move_event(frame)
    if event is None:
        return None
    key = _zhanji_event_key(event)
    if frame.data.get(_ZHANJI_LAST) == key:
        frame.data.pop(_ZHANJI_GUARD, None)
        return None
    frame.data[_ZHANJI_LAST] = key
    frame.data[_ZHANJI_GUARD] = True
    control = host.draw_for_hand(frame, owner, 1)
    if control == "stay":
        return "stay"
    frame.data.pop(_ZHANJI_GUARD, None)
    return None


# ---------------------------------------------------------------------------
# 刘琦【问计】/【屯江】（sp5-pangtong-liuqi 票 02；用户 2026-11-12 一次签）
#
#     问计
#       出牌阶段开始时，你可以令一名其他角色交给你一张牌，然后你本回合使用与此牌牌名
#       相同的牌不能被其他角色响应。
#
#     屯江
#       结束阶段，若你本回合未跳过出牌阶段且你于本回合的出牌阶段内未使用牌指定过其他
#       角色为目标，你可以摸X张牌（X为全场势力数）。
#
# 问计：``PHASE_PLAY_START`` 钩子（**本时机第一个提问型钩子**；票 02 §3.1 专项核实：
# 复位段重入不会抹掉它落下的事实——事实只住回合作用域字段，派发进度住帧内游标）。
# 第 2 步由**目标**作答（样板 = 锋·于禁【节钺】，账本 L1092"交给你一张牌由交出者挑"）。
# 禁令 = 第二个 ``forced`` 钩子（``USE_AFTER_DESIGNATE_TARGET`` + ``Tag.UNRESPONSIVE``
# 全场档，样板 = 界黄盖【诈降】；窗口覆盖口径 = 账本 L936"只关闭牌类响应窗口"）。
#
# 屯江：``PHASE_FINISH`` 钩子（零步；样板 = 界貂蝉【闭月】）+ 两条**回合作用域**的引擎侧
# 事实（票 05；用户 2026-11-12 签 Q6 = 引擎侧、Q8 = 只数存活角色的势力种数）。
# ---------------------------------------------------------------------------

WENJI = hero_skill_id("liuqi", "wenji")
"""刘琦【问计】的全限定技能 id。"""

TUNJIANG = hero_skill_id("liuqi", "tunjiang")
"""刘琦【屯江】的全限定技能 id。"""

_WENJI_GIVEN = "wenji_given"
"""帧私有：那张牌已经交到刘琦手里（重入不重搬）。"""

_TUNJIANG_DRAWN = "tunjiang_drawn"
"""帧私有：这一次发动已经摸过 X 张（重入不重摸）。"""


def _wenji_targets(
    state: GameState, frame: Any, owner: int, answers: Any
) -> tuple[int, ...]:
    """「一名其他角色」且**至少有 1 张牌**（交不出来的不算目标；座位序）。"""
    del frame, answers
    return tuple(
        player.seat
        for player in state.players
        if player.alive
        and player.seat != owner
        and (player.hand or player.equip)
    )


def _wenji_giver(state: GameState, frame: Any, owner: int, answers: Any) -> int:
    """第 2 步的应答者 / 选择面 = 第 1 步选中的那名角色（L1092：由他挑一张交出）。"""
    del state, frame, owner
    return int(answers["seat"][0])


def _wenji_condition(host: Any, frame: Frame) -> bool:
    """触发条件：**自己的**出牌阶段开始（帧 ``source`` = 当前回合角色）∧ 有人交得出来。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    if host.state.turn_seat != owner:
        return False
    if frame.data.get("source") != owner:
        return False
    return bool(_wenji_targets(host.state, frame, owner, ()))


def _wenji_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：把那张牌放进刘琦的手牌，并记下它的牌名（本回合内同名牌不可被响应）。"""
    state = host.state
    owner = int(frame.data["skill_owner"])
    target = _wenji_giver(state, frame, owner, answers)
    card = int(tuple(answers["card"])[0])
    if not frame.data.get(_WENJI_GIVEN):
        frame.data[_WENJI_GIVEN] = True
        control = host.move_in_frame(
            frame,
            state.refs((card,)),
            to_zone=ZoneRef(Zone.HAND, owner),
            reason=MoveReason.PUT,
            owner=owner,
            actor=target,
        )
        if control == "stay":
            return "stay"
    name = str(state.card(card).type)
    player = state.player(owner)
    if name and name not in player.wenji_names:
        player.wenji_names = (*player.wenji_names, name)
    return None


WENJI_FLOW = SkillFlow(
    name="wenji",
    steps=(
        PickSeats(lo=1, hi=1, pool=_wenji_targets, key="seat"),
        # L1092：应答者 / 选择面 = 目标本人（自己的手牌 + 装备区，可见可选）。
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            key="card",
            actor=_wenji_giver,
            face=_wenji_giver,
            reveal_hand=True,
        ),
    ),
    effect=_wenji_effect,
)
"""【问计】的两步流水线：选一名其他角色 → 目标自己挑一张交给你。"""


def _wenji_unresponsive_condition(host: Any, frame: Frame) -> bool:
    """``USE_AFTER_DESIGNATE_TARGET``：本回合、自己使用的、牌名在禁令集合里的牌。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    if frame.data.get("source") != owner:
        return False
    if host.state.turn_seat != owner:
        return False
    card = frame.data.get("card")
    if card is None:
        return False
    return str(card.type) in host.state.player(owner).wenji_names


def _wenji_unresponsive(host: Any, frame: Frame) -> None:
    """给本次使用打**全场档**不可响应（样板 = 界黄盖【诈降】；读取面 = 账本 L936）。"""
    del host
    add_tag(frame, Tag.UNRESPONSIVE)


def _tunjiang_condition(host: Any, frame: Frame) -> bool:
    """触发条件：自己的结束阶段 ∧ 本回合没跳过出牌阶段 ∧ 没对别人用过牌。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    state = host.state
    if state.turn_seat != owner:
        return False
    player = state.player(owner)
    return not player.play_phase_skipped_this_turn and not (
        player.designated_other_this_turn
    )


def _tunjiang_x(state: GameState) -> int:
    """X = **全场势力数**：存活角色的**势力种数**（无势力不计；用户 2026-11-12 签 Q8）。"""
    return len(
        {player.kingdom for player in state.players if player.alive and player.kingdom}
    )


def _tunjiang_effect(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：摸 X 张（X 在提交点现算；短摸 ⇒ 既有平局口径）。"""
    del answers
    if frame.data.get(_TUNJIANG_DRAWN):
        return None
    frame.data[_TUNJIANG_DRAWN] = True
    owner = int(frame.data["skill_owner"])
    control = host.draw_for_hand(frame, owner, _tunjiang_x(host.state))
    if control == "stay":
        return "stay"
    return None


TUNJIANG_FLOW = SkillFlow(name="tunjiang", steps=(), effect=_tunjiang_effect)
"""【屯江】的零步流水线：只问"是否发动" → 摸 X 张。"""


# ---------------------------------------------------------------------------
# 技能声明（由 `sgs.skills.catalog` 末尾并入）
# ---------------------------------------------------------------------------

_REQUIREMENT_FENGPO = (
    "每阶段每项限一次，当你使用【杀】或者【决斗】指定一名其他角色为唯一目标后，"
    "你可以选择一项：【1】摸X张牌；【2】令此牌的基础伤害值+X"
    "（X为你与其的手牌和装备牌区的♦️牌数）。"
)
"""【凤魄】的牌面逐字（用户 ``技能.txt``；``requirement`` 槽位）。"""

_REQUIREMENT_GUANWEI = (
    "每回合限一次，一名角色的出牌阶段结束时，若其本回合使用过至少两张牌且这些牌花色"
    "均相同，你可以弃置一张牌，令其摸两张牌并于此阶段结束后执行一个额外的出牌阶段。"
)
"""【观微】的牌面逐字（用户 ``技能.txt``；``requirement`` 槽位）。"""

_REQUIREMENT_GONGQING = (
    "锁定技，当你受到伤害时，若伤害来源的攻击范围："
    "【小于3】此伤害调整为1；【大于3】令此伤害+1。"
)
"""【公清】的牌面逐字（用户 ``技能.txt``；``requirement`` 槽位）。"""

_REQUIREMENT_SONGSANG = (
    "限定技，当其他角色死亡时，若你已受伤，你可以回复1点体力；"
    "若你没有受伤，你可以增加1点体力上限。若如此做，你获得技能“展骥”。"
)
"""【送丧】的牌面逐字（OL 官网 ``hero/429``；用户 ``技能.txt`` 的斜杠并置 = 两支的紧凑写法）。"""

_REQUIREMENT_ZHANJI = "锁定技，当你于出牌阶段内不因此技能摸牌时，你摸一张牌。"
"""☆【展骥】的牌面逐字（用户 2026-11-12 改后的文本；OL WIKI 同值）。"""

_REQUIREMENT_WENJI = (
    "出牌阶段开始时，你可以令一名其他角色交给你一张牌，"
    "然后你本回合使用与此牌牌名相同的牌不能被其他角色响应。"
)
"""【问计】的牌面逐字（用户 ``技能.txt``）。"""

_REQUIREMENT_TUNJIANG = (
    "结束阶段，若你本回合未跳过出牌阶段且你于本回合的出牌阶段内"
    "未使用牌指定过其他角色为目标，你可以摸X张牌（X为全场势力数）。"
)
"""【屯江】的牌面逐字（用户 ``技能.txt``）。"""


_SONGSANG_DEF = SkillDef(
    skill_id=SONGSANG,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.LIMITED,
    hooks=(Hook(Timing.DEATH_ON, flow=SONGSANG_FLOW, condition=_songsang_condition),),
    requirement=_REQUIREMENT_SONGSANG,
)

_ZHANJI_DEF = SkillDef(
    skill_id=ZHANJI,
    locked=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(
            Timing.MOVE_AFTER,
            handler=_zhanji,
            condition=_zhanji_condition,
        ),
    ),
    requirement=_REQUIREMENT_ZHANJI,
)

_WENJI_DEF = SkillDef(
    skill_id=WENJI,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(Timing.PHASE_PLAY_START, flow=WENJI_FLOW, condition=_wenji_condition),
        # 「不能被其他角色响应」= 帧标签（不是询问、不是修饰器；样板 = 界黄盖【诈降】）。
        Hook(
            Timing.USE_AFTER_DESIGNATE_TARGET,
            handler=_wenji_unresponsive,
            forced=True,
            condition=_wenji_unresponsive_condition,
        ),
    ),
    requirement=_REQUIREMENT_WENJI,
    step_requirements={
        "seat": "选择一名其他角色",
        "card": "选择你自己的一张牌交给刘琦",
    },
)

_TUNJIANG_DEF = SkillDef(
    skill_id=TUNJIANG,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(Hook(Timing.PHASE_FINISH, flow=TUNJIANG_FLOW, condition=_tunjiang_condition),),
    requirement=_REQUIREMENT_TUNJIANG,
)

_FENGPO_DEF = SkillDef(
    skill_id=FENGPO,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(
            Timing.USE_AFTER_DESIGNATE_TARGET,
            condition=_fengpo_condition,
            flow=FENGPO_FLOW,
        ),
        # 本次使用结束：清掉"此牌 +X"的记账（被【闪】抵消 / 伤害被防止时也清）。
        Hook(
            Timing.RESOLVE_AFTER_END,
            handler=_fengpo_cleanup,
            forced=True,
            condition=_fengpo_cleanup_condition,
        ),
    ),
    modifiers=(Modifier(ModifierPoint.DAMAGE_DEAL_AMOUNT, _fengpo_damage_bonus),),
    requirement=_REQUIREMENT_FENGPO,
    step_requirements={"option": "选择一项"},
)

_GUANWEI_DEF = SkillDef(
    skill_id=GUANWEI,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(Timing.PHASE_PLAY_END, flow=GUANWEI_FLOW, condition=_guanwei_condition),
    ),
    requirement=_REQUIREMENT_GUANWEI,
    step_requirements={"card": "弃置一张牌"},
)

_GONGQING_DEF = SkillDef(
    skill_id=GONGQING,
    locked=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    modifiers=(
        Modifier(ModifierPoint.DAMAGE_CAP, _gongqing_cap),
        Modifier(ModifierPoint.DAMAGE_TAKE_AMOUNT, _gongqing_bonus),
    ),
    requirement=_REQUIREMENT_GONGQING,
)

SP5_SKILL_DEFS: tuple[SkillDef, ...] = (
    _FENGPO_DEF,
    _GUANWEI_DEF,
    _GONGQING_DEF,
    _SONGSANG_DEF,
    _ZHANJI_DEF,
    _WENJI_DEF,
    _TUNJIANG_DEF,
)
"""SP武将组5 的全部技能定义（马术住 :mod:`sgs.skills.target_change`，逐票追加）。"""


def sp5_skill_definitions() -> tuple[SkillDef, ...]:
    """SP武将组5 的技能定义（由 :data:`sgs.skills.CATALOG` 末尾并入）。"""
    return SP5_SKILL_DEFS
