"""神话再临·雷包武将技能（``lei`` 包；01 票起 = 郝昭【镇骨】）。

本模块只装**雷包**的武将技能声明；族级原语（钩子 / 步骤流水线 / 标记 / 摸牌）一律
复用既有实现，不新增机制。

**模块内的技能归属**（一条技能住哪一族，就住那个模块）：

- 郝昭【镇骨】/ 张绣【从谏】/ 诸葛瞻【罪论】【父荫】/ 神甘宁【劫营】= 本模块（钩子 + 流水线）；
- 张绣【雄乱】/ 神甘宁【魄袭】= A 族，住 :mod:`sgs.skills.active`（出牌阶段主动技的候选 /
  发动主体都归那一族）；
- 神甘宁「营」的**标记自身效果**（``hero.shen_ganning.jieying_mark``）= 本模块，但它不是
  技能：`Marker.effect` 指向它，`carrier = CarrierKind.MARKER`，武将面 / 技能按钮区都不列。

【镇骨】（郝昭，魏 / 男 / 4 体力；牌面权威 = 用户录入 ``技能.txt``，逐字）：

    结束阶段，你可以选择一名其他角色，你的回合结束时和该角色的下个回合结束时，
    其将手牌摸至或弃置至与你手牌数相同（摸牌时至多摸至五张）。

用户 2026-11-05 签的裁定（`.scratch/lei-batch/issues/90-zhengku-open-questions.md`）：

- **Q1 = A**：「其将手牌…弃置至」的**弃哪些牌由目标自己选**（`actor=目标` 的
  ``SELECT_CARDS``，与他受伤后弃牌的【刚烈】同一形态）；
- **Q2 = A**：「（摸牌时至多摸至五张）」= **手牌终值上限 5**（与【节命】的
  ``JIEMING_CAP`` 同一口径，不是"最多摸 5 张"）；
- **Q3 = A**：标记来源（郝昭）阵亡 ⇒ 标记随死亡清除、该次结算不发生
  （寿命是 2018 口径的通则，`Marker` 不再有开关；shan-baozuoci 票 01）；
- **Q4 = A**：目标的"下个回合"被跳过（翻面）⇒ 该轮没有回合结束，结算顺延到目标
  **下一个真实执行的回合末**（标记留着，等真的 ``TURN_END``）；
- **Q5 = A**：郝昭在自己回合内阵亡 ⇒ 第 1 次结算不发生（死者不结算，标记同 Q3 清除）。

**标记的记法**（一张标记、count = 还欠几次结算）：

    发动时给"那名其他角色"挂一枚「镇骨」标记，``count = 2`` = 两次结算都还欠着；
    第 1 次结算（**你的回合结束时**，标记就在你身上）只减 1；第 2 次结算（**该角色
    的下个回合结束时**，标记在他身上）清空。故：

    - 结算点恒是"**当前回合角色**"身上那枚来源是我的「镇骨」标记；
    - 你的回合末只在 ``count == 2`` 时结算（``count == 1`` 表示我的那一次已经结过，
      剩下那次属于目标的回合末——这样"目标回合被跳过"不会让你下个回合末多结一次）；
    - 目标的回合末只要 ``count >= 1`` 就结算（``count == 2`` 只可能是你的回合末没跑到，
      例如你中途阵亡——那时标记已被清，不会走到这里）。

**弃置支路**（Q1 = A）：要弃的牌由**目标自己**从自己手牌里点出来（可见槽
``reveal_hand=True``，与他自己的弃牌阶段同形），经 ``move_in_frame`` 进弃牌堆
（``MoveReason.DISCARD``，故【连营】一类"失去牌后"钩子照常触发）。

**摸牌支路**：``need = max(0, min(你的手牌数, 5) - 目标手牌数)``（Q2 = A：终值上限）。
两个支路的差值都在**每个结算点现算**（动态量，不在发动时快照）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierPoint,
    behavior_for,
    is_slash,
    place_on_deck,
)
from sgs.state import (
    ArrangeCards,
    FrameKind,
    GameState,
    Marker,
    MoveReason,
    SelectionSpec,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import (
    MATCH_ACTOR_KEY,
    MATCH_OWNER_KEY,
    Hook,
    SkillDef,
    mark_skill_used_this_turn,
    skill_used_this_turn,
)
from .negation import mark_targets_ineffective
from .flow import (
    Arrange,
    FlowAnswers,
    PickCards,
    PickSeats,
    SkillFlow,
    TwoRowArrange,
    option_seat,
    seat_choice,
    watched_cards,
)

__all__ = [
    "CONGJIAN",
    "JIEYING",
    "JIEYING_EFFECT",
    "JIEYING_MARK",
    "LEI_SKILL_DEFS",
    "SHEN_GANNING",
    "ZHANGXIU",
    "ZHENGKU",
    "ZHENGKU_CAP",
    "ZHENGKU_MARK",
    "lei_skill_definitions",
]

HAOZHAO = "haozhao"

ZHENGKU = hero_skill_id(HAOZHAO, "zhengku")
"""郝昭【镇骨】的全限定技能 id。"""

ZHENGKU_MARK = "镇骨"
"""【镇骨】标记名（票 06 的标记机制；名字用中文，与「梦魇」同姿态）。"""

ZHENGKU_CAP = 5
"""「（摸牌时至多摸至五张）」的**手牌终值**上限（用户 2026-11-05 裁定，同【节命】）。"""

_ASK_ZHENGKU_DISCARD = "hero.haozhao.zhengku.discard"
"""手写询问位置：镇骨弃置支路的选牌步（``owner`` 必填，见 ``Engine.ask_selection``）。"""

_ZHENGKU_DREW = "zhengku_drew"
"""帧私有：本帧的摸牌支路已经跑过（重入不重摸）。"""

_ZHENGKU_PICKED = "zhengku_picked"
"""帧私有：弃置支路选出的牌（重入不重问）。"""

_ZHENGKU_SETTLED = "zhengku_settled"
"""帧私有：本帧的这次结算已经收尾（重入直接返回 ``None``）。"""


def _other_alive_seats(
    state: GameState, frame: Any, owner: int, answers: FlowAnswers | None = None
) -> tuple[int, ...]:
    """「一名其他角色」的池：存活且不是持有者（默认读法（二）②）。

    流水线步（``pool=`` 回调）与【罪论】X = 0 收尾段共用这一份：后者不在流水线里、
    没有答案集，``answers`` 缺省为 ``None``（本函数只 ``del`` 两个载荷参数）。
    """
    del frame, answers
    return tuple(
        player.seat for player in state.players if player.alive and player.seat != owner
    )


def _zhengku_condition(host: Any, frame: Any) -> bool:
    """发起问：**自己**的结束阶段，且场上还有一名其他存活角色。

    ``PHASE_FINISH`` 是**每个座位**的结束阶段都派发的帧外时机（大雾 / 狂风 / 诛害同一
    形态），故必须自校"这一步的回合角色是不是我"；池空时连"是否发动"都不问
    （`edge-cases.md` 总纪律 1：不发没有候选的询问）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    return bool(_other_alive_seats(host.state, frame, int(owner), ()))


def _zhengku_apply(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：给选中的其他角色挂一枚「镇骨」标记（count = 2 = 两次结算都还欠）。"""
    target = next(iter(int(value) for value in answers.get("seat", ())), None)
    if target is None:
        return None
    owner = int(frame.data["skill_owner"])
    host.add_marker(
        frame,
        target,
        name=ZHENGKU_MARK,
        source_seat=owner,
        source_skill_id=ZHENGKU,
        count=2,
    )
    return None


ZHENGKU_FLOW = SkillFlow(
    name="zhengku",
    steps=(
        PickSeats(
            lo=1,
            hi=1,
            pool=_other_alive_seats,
            optional=False,
            key="seat",
        ),
    ),
    effect=_zhengku_apply,
)
"""【镇骨】的一步流水线：选一名其他存活角色 → 挂标记（两次结算住 ``TURN_END`` 钩子）。"""


def _marker_on(state: GameState, seat: int, owner: int) -> Marker | None:
    """``seat`` 身上由 ``owner`` 的【镇骨】挂的那枚标记（没有 ⇒ ``None``）。"""
    for marker in state.player(seat).markers:
        if marker.source_seat == owner and marker.source_skill_id == ZHENGKU:
            return marker
    return None


def _zhengku_debt(
    state: GameState, owner: int, *, turn: int
) -> tuple[int, Marker] | None:
    """这次 ``TURN_END`` 该兑现的欠账：``(要调整手牌的角色, 标记)``；没有 ⇒ ``None``。

    - **我的回合末**：本回合刚挂的那笔欠账 ``count == 2``（我的那一次还没结）——它躺在
      **目标**身上，故这里按"来源是我 + count ≥ 2"全场找；``count == 1`` 的旧欠账属于
      目标侧的回合末，不在我这里补结（Q4：目标回合被跳过时不许提前结）。
    - **别人的回合末**：只看当前回合角色身上有没有我挂的欠账（``count ≥ 1``）。

    ``count`` 不会超过 2：每个自己的结束阶段至多一次发动，且同一回合末必然减 1。
    """
    if turn == owner:
        for player in state.players:
            marker = _marker_on(state, player.seat, owner)
            if marker is not None and marker.count >= 2:
                return player.seat, marker
        return None
    marker = _marker_on(state, turn, owner)
    if marker is None:
        return None
    return turn, marker


def _zhengku_settle_condition(host: Any, frame: Any) -> bool:
    """结算问：当前回合末有一笔该兑现的欠账（判据见 :func:`_zhengku_debt`）。

    ``TURN_END`` 是**每个座位**的回合末都派发的帧外时机（界博图是阶段体、有 own-turn
    闸，这里用普通钩子 + 自校，同为既有原语）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    turn = frame.data.get("source")
    if owner is None or turn is None:
        return False
    return _zhengku_debt(host.state, int(owner), turn=int(turn)) is not None


def _zhengku_settle(host: Any, frame: Any) -> Any:
    """结算主体：把**欠账的目标**手牌调成与郝昭相同（摸 / 弃两支，终值上限 5）。

    两次结算走同一个函数：第 1 次（我的回合末）只减 1（还欠目标侧那一次）；第 2 次
    （目标的回合末）结算完清空。

    重入纪律（G7）：摸牌 / 弃牌都会内联派发"失去牌后 / 获得牌后"钩子而挂起，故每个
    已付事实都落帧私有游标——重入既不重摸、也不重问、更不重搬。
    """
    owner = int(frame.data["skill_owner"])
    turn = int(frame.data["source"])
    state = host.state
    if frame.data.get(_ZHENGKU_SETTLED):
        return None
    debt = _zhengku_debt(state, owner, turn=turn)
    if debt is None:
        return None
    target, marker = debt
    owner_hand = len(state.player(owner).hand)
    target_hand = len(state.player(target).hand)
    # 支路 1：目标更少 ⇒ 摸到「你的手牌数」，但**手牌终值**至多 5（Q2 = A）。
    if not frame.data.get(_ZHENGKU_DREW):
        if target_hand < owner_hand:
            need = max(0, min(owner_hand, ZHENGKU_CAP) - target_hand)
            if need and host.draw_for_hand(frame, target, need) == "stay":
                return "stay"
        frame.data[_ZHENGKU_DREW] = True
    # 支路 2：目标更多 ⇒ 目标自己选 (target - owner) 张弃置（Q1 = A）。
    if target_hand > owner_hand:
        picked = frame.data.get(_ZHENGKU_PICKED)
        if picked is None:
            answer = host.ask_selection(
                frame,
                owner=_ASK_ZHENGKU_DISCARD,
                key="zhengku:discard",
                actor=target,
                timing=Timing.TURN_END,
                spec=SelectionSpec(
                    zones=(Zone.HAND,),
                    total=(target_hand - owner_hand, target_hand - owner_hand),
                    optional=False,
                ),
                # 应答的是**手牌持有者本人** ⇒ 可见槽（与【刚烈】的弃牌支路同一口径）。
                build_face=lambda: host.selection_face(
                    target, (Zone.HAND,), reveal_hand=True
                ),
            )
            if host.suspended(answer):
                return "stay"
            picked = tuple(int(card_id) for card_id in answer)
            frame.data[_ZHENGKU_PICKED] = picked
        if picked:
            control = host.move_in_frame(
                frame,
                state.refs(picked),
                to_zone=ZoneRef(Zone.DISCARD),
                reason=MoveReason.DISCARD,
                owner=target,
                actor=target,
            )
            if control == "stay":
                return "stay"
    # 标记记账：我的回合末只减 1（还欠目标侧那一次）；目标的回合末清空。
    if turn == owner:
        host.remove_marker(
            frame,
            target,
            name=marker.name,
            source_seat=owner,
            source_skill_id=ZHENGKU,
            count=1,
        )
    else:
        host.remove_marker(
            frame,
            target,
            name=marker.name,
            source_seat=owner,
            source_skill_id=ZHENGKU,
        )
    frame.data[_ZHENGKU_SETTLED] = True
    return None


_ZHENGKU = SkillDef(
    skill_id=ZHENGKU,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        # 发起：结束阶段问一次（「你可以」⇒ 非锁定）。
        Hook(Timing.PHASE_FINISH, flow=ZHENGKU_FLOW, condition=_zhengku_condition),
        # 两次结算：牌面**没有**「可以」⇒ 强制钩子（forced=True 只关"问不问"，
        # 不改 locked：技能失效时两段一起停摆）。
        Hook(
            Timing.TURN_END,
            handler=_zhengku_settle,
            condition=_zhengku_settle_condition,
            forced=True,
        ),
    ),
    requirement=(
        "结束阶段，你可以选择一名其他角色，你的回合结束时和该角色的下个回合结束时，"
        "其将手牌摸至或弃置至与你手牌数相同（摸牌时至多摸至五张）"
    ),
    step_requirements={
        "seat": "选择一名其他角色",
        "card": "弃置至与你手牌数相同",
    },
)
"""郝昭【镇骨】的完整声明（一条技能、两条钩子：发起 + 两次强制结算）。"""


# ---------------------------------------------------------------------------
# 张绣【从谏】（lei-batch 票 02；用户 2026-11-05 签「从谏，很好没有问题」）
#
# 牌面（逐字）：
#
#     当你成为目标数大于1的普通锦囊牌的目标时，你可以交给一名其他目标角色一张牌。
#     若你给出的牌为装备牌，则你摸两张牌，否则你摸一张牌。
#
# 形状 = 多步「选牌 → 座位」（`hero.daqiao.liuli` 同时机、同步骤序）：
#
# - 时机 `USE_BECOME_TARGET`（成为目标时，逐目标派发）：条件自校"**这一步问的是我**"
#   （`MATCH_OWNER_KEY == actor`，与【流离】同一判据），再判牌是**普通锦囊**（大类是
#   锦囊 ∧ 非延时，转化 / 虚拟牌按产物牌型判）且目标数 > 1，且我手上 / 装备区有牌、
#   且还有一个其他目标（池空不发询问）；
# - 给出的「一张牌」= 手牌 + 装备区（账本「一张牌」行）；`move_in_frame` 交给对方
#   （`MoveReason.PUT`，与【遗计】同一"给牌"口径）⇒ 会内联派发"失去牌后"钩子，故
#   帧内游标保证重入不重搬；
# - 摸牌数按**给出的那张牌**的牌型判（装备牌摸 2、否则摸 1），与它从哪个区给出无关。
# ---------------------------------------------------------------------------

ZHANGXIU = "zhangxiu"

CONGJIAN = hero_skill_id(ZHANGXIU, "congjian")
"""张绣【从谏】的全限定技能 id。"""

_CONGJIAN_GIVEN = "congjian_given"
"""帧私有：那张牌已经交出去了（重入不重搬）。"""

_CONGJIAN_DREW = "congjian_drew"
"""帧私有：摸牌已经结算（重入不重摸）。"""

_CONGJIAN_CARD_TYPE = "congjian_card_type"
"""帧私有：交出去的那张牌的牌型（摸牌支路按它判 2 / 1）。"""


def _congjian_targets(frame: Any) -> tuple[int, ...]:
    """这张牌**已指定的全部目标**（含自己——自己是不是目标由条件判）。"""
    return tuple(int(seat) for seat in frame.data.get("targets", ()))


def _congjian_pool(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """「一名其他目标角色」：这张牌的其他目标、存活、不含自己。"""
    del chosen
    return tuple(
        seat
        for seat in _congjian_targets(frame)
        if int(seat) != int(owner) and state.player(int(seat)).alive
    )


def _is_ordinary_trick(state: GameState, card: Any) -> bool:
    """「普通锦囊牌」= 大类是锦囊 ∧ **非延时**（转化 / 虚拟牌按产物牌型判）。"""
    if card_category(state.pack_id, card.type) is not Category.TRICK:
        return False
    return not behavior_for(card).is_delayed


def _congjian_condition(host: Any, frame: Any) -> bool:
    """触发条件：**我**成为一张多目标普通锦囊牌的目标，且我付得起、还有人可给。

    四个前提缺一不可（缺哪个都**不发询问**，`edge-cases.md` 总纪律 1）：
    ① 我是这张牌的目标（`MATCH_OWNER_KEY in targets`，与【流离】同一判据——派发环对每个
    存活实例求值一次，漏掉这一条就会"任何人成为目标、张绣都问一遍"）；② 牌是普通锦囊；
    ③ 目标数 > 1；④ 我有牌可给、且至少一名其他目标。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if frame.data.get(MATCH_ACTOR_KEY) != owner:
        # 派发环逐目标各问一次（`USE_BECOME_TARGET` 每次只服务一个目标）；
        # 不是"这一次轮到我"就闭嘴，否则一张多目标锦囊会把同一个持有者问两次。
        return False
    if int(owner) not in _congjian_targets(frame):
        return False
    card = frame.data.get("card")
    if card is None or not _is_ordinary_trick(host.state, card):
        return False
    if len(_congjian_targets(frame)) < 2:
        return False
    me = host.state.player(int(owner))
    if not me.hand and not me.equip:
        return False
    return bool(_congjian_pool(host.state, frame, int(owner), ()))


def _congjian_apply(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：把选中的牌交给选中的目标 → 按牌型摸 2 / 摸 1（两段各有游标）。"""
    owner = int(frame.data["skill_owner"])
    state = host.state
    if not frame.data.get(_CONGJIAN_GIVEN):
        picked = tuple(int(card_id) for card_id in answers.get("card", ()))
        seat = next(iter(int(value) for value in answers.get("seat", ())), None)
        if not picked or seat is None:
            return None
        control = host.move_in_frame(
            frame,
            state.refs(picked),
            to_zone=ZoneRef(Zone.HAND, seat),
            reason=MoveReason.PUT,
            owner=seat,
            actor=owner,
        )
        if control == "stay":
            return "stay"
        frame.data[_CONGJIAN_GIVEN] = True
        frame.data[_CONGJIAN_CARD_TYPE] = state.card(picked[0]).type
    if not frame.data.get(_CONGJIAN_DREW):
        equipment = (
            card_category(state.pack_id, frame.data[_CONGJIAN_CARD_TYPE])
            is Category.EQUIPMENT
        )
        if host.draw_for_hand(frame, owner, 2 if equipment else 1) == "stay":
            return "stay"
        frame.data[_CONGJIAN_DREW] = True
    return None


CONGJIAN_FLOW = SkillFlow(
    name="congjian",
    steps=(
        PickCards(
            zones=(Zone.HAND, Zone.EQUIP),
            lo=1,
            hi=1,
            optional=False,
            key="card",
        ),
        PickSeats(lo=1, hi=1, pool=_congjian_pool, optional=False, key="seat"),
    ),
    effect=_congjian_apply,
)
"""【从谏】的两步流水线：选一张牌（手牌 / 装备）→ 选一名其他目标。"""

_CONGJIAN = SkillDef(
    skill_id=CONGJIAN,
    locked=False,
    # 已翻预发动：首步选**自己**的手牌 / 装备区 ⇒ 清单渲得出来（`shapes.md` §4 准入表第 1 行）。
    preactivated=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(
            Timing.USE_BECOME_TARGET,
            condition=_congjian_condition,
            flow=CONGJIAN_FLOW,
        ),
    ),
    requirement=(
        "当你成为目标数大于1的普通锦囊牌的目标时，你可以交给一名其他目标角色一张牌。"
        "若你给出的牌为装备牌，则你摸两张牌，否则你摸一张牌。"
    ),
    step_requirements={
        "card": "交给一名其他目标角色一张牌",
        "seat": "选择交给哪名其他目标角色",
    },
)
"""张绣【从谏】的完整声明。"""

LEI_SKILL_DEFS: tuple[SkillDef, ...] = (_ZHENGKU, _CONGJIAN)
"""雷包已落地的武将技能（逐票追加；包脚手架归 01 票）。

【雄乱】（张绣）住 `active.py` 的 A 族（它是出牌阶段主动技，候选 / 发动主体都归那一族）。
"""


def lei_skill_definitions() -> tuple[SkillDef, ...]:
    """雷包（``lei``）武将技能的 :class:`~sgs.skills.catalog.SkillDef` 集合。"""
    return LEI_SKILL_DEFS


# ---------------------------------------------------------------------------
# 诸葛瞻（lei-batch 票 03；用户 2026-11-05 签）——判据函数先落（`384d9ed`），【罪论】/
# 【父荫】随后在 `a0e0aeb` 同票注册进 ``LEI_SKILL_DEFS``（一将一提交：诸葛瞻的两条技能
# 一起在 CATALOG 闭合后才 enabled）。
#
# 罪论（逐字）：
#
#     结束阶段，你可以观看牌堆顶的三张牌，获得其中X张（X为你满足的选项数），然后以任意
#     顺序放回其余的牌：1.本回合造成过伤害；2.本回合未弃置过牌；3.手牌数为全场最少。
#     若均不满足，你与一名其他角色各失去1点体力。
#
# 父荫（逐字）：
#
#     锁定技，当你每回合第一次成为【杀】或【决斗】的目标后，若你的手牌数不大于使用者，
#     此牌对你无效。
# ---------------------------------------------------------------------------

ZHUGEZHAN = "zhugezhan"

ZUILUN = hero_skill_id(ZHUGEZHAN, "zuilun")
"""诸葛瞻【罪论】的全限定技能 id。"""

FUYIN = hero_skill_id(ZHUGEZHAN, "fuyin")
"""诸葛瞻【父荫】的全限定技能 id。"""


def zuilun_conditions(state: GameState, owner: int) -> tuple[bool, bool, bool]:
    """【罪论】的三个选项判据（用户 2026-11-05 签的口径）。

    ① 本回合造成过伤害 ⇒ 座位级事实 ``Player.damage_dealt_this_turn``（【诛害】同一处）；
    ② 本回合**未**弃置过牌 ⇒ 「弃置过牌」检测器的反面（`Player.discarded_this_turn`，
       自身回合内弃置即置真、回合结束后复位）；
    ③ 手牌数为**全场最少** ⇒ 依次对比**全场存活角色**的手牌数，自己的**不大于**每一名
       其他角色即满足（**含并列**；用户举例：自己 2、其他 2 / 3 / 4 / 5 ⇒ 满足）。
    """
    me = state.player(owner)
    damaged = bool(me.damage_dealt_this_turn)
    not_discarded = not me.discarded_this_turn
    my_hand = len(me.hand)
    fewest = all(
        my_hand <= len(player.hand)
        for player in state.players
        if player.alive and player.seat != owner
    )
    return damaged, not_discarded, fewest


def zuilun_x(state: GameState, owner: int) -> int:
    """本次发动的 X = 满足的选项数（0–3，每次发动时现算，不缓存）。"""
    return sum(1 for met in zuilun_conditions(state, owner) if met)


def _fuyin_trigger_suits(host: Any, frame: Any) -> bool:
    """【父荫】的**时机 + 牌族**判据：这一次成为的是【杀】/【决斗】的目标。

    ``USE_AFTER_BECOME_TARGET`` 逐目标派发（``MATCH_ACTOR_KEY`` = 这一次的目标），故
    "这一次轮到我"读它；牌族按**产物牌型**判（转化 / 虚拟牌同判）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get(MATCH_ACTOR_KEY) != owner:
        return False
    card = frame.data.get("card")
    if card is None:
        return False
    return is_slash(behavior_for(card)) or card.type == DUEL_CARD_TYPE


DUEL_CARD_TYPE = "duel"
"""【决斗】的牌名 id（牌面事实；与 `sgs.data` 的牌名表同一处，不另建白名单）。"""


def fuyin_condition(host: Any, frame: Any) -> bool:
    """【父荫】的完整触发条件（锁定技：满足即生效、不问"是否发动"）。

    用户 2026-11-05 裁定「父荫，第一次成为目标」⇒ **数事件**：每回合**第一次**成为
    【杀】/【决斗】的目标就是唯一机会；那一次若手牌数 > 使用者，本回合不再有机会。
    """
    if not _fuyin_trigger_suits(host, frame):
        return False
    owner = int(frame.data[MATCH_OWNER_KEY])
    source = frame.data.get("source")
    if source is None or int(source) == owner:
        return False
    state = host.state
    if skill_used_this_turn(state, owner, FUYIN):
        return False
    return len(state.player(owner).hand) <= len(state.player(int(source)).hand)


def fuyin_effect(host: Any, frame: Any) -> None:
    """效果：**此牌对你无效** —— 目标级无效的唯一写入口 + 记一次"本回合已用过"。"""
    owner = int(frame.data["skill_owner"])
    mark_targets_ineffective(frame, (owner,))
    mark_skill_used_this_turn(host.state, owner, FUYIN)
    return None


# ---------------------------------------------------------------------------
# 诸葛瞻的两条技能声明（lei-batch 票 03；与上面的判据函数同票注册）
# ---------------------------------------------------------------------------

_ZUILUN_GAINED = "zuilun_gained"
_ZUILUN_PLACED = "zuilun_placed"
_ZUILUN_VICTIM = "zuilun_victim"
_ZUILUN_STAGE = "zuilun_stage"
_ZUILUN_DONE = "zuilun_done"


def _deck_top_three(state: GameState, frame: Any) -> int:
    del frame
    return min(3, len(state.deck))


def _zuilun_top(state: GameState, frame: Any) -> int:
    """上排（放回牌堆顶）张数 = 3 − X；X 在**发动时**现算（用户裁定：每次取值按当下重算）。"""
    owner = int(frame.data["skill_owner"])
    return max(0, _deck_top_three(state, frame) - zuilun_x(state, owner))


def _zuilun_face(state: GameState, frame: Any) -> tuple[Any, ...]:
    """这一次摆的三张 = 牌堆顶三张（询问把它们授予观看者）。"""
    del frame
    return state.refs(tuple(state.deck[:3]))


ZUILUN_ARRANGE = TwoRowArrange(
    count=_deck_top_three,
    top=_zuilun_top,
    cards=_zuilun_face,
    top_label="牌堆顶",
    bottom_label="获得",
    reorder=True,
    starts_on_top=True,
)
"""【罪论】的两行排列规格（用户 2026-11-05 签的形态）。

上排 = 放回牌堆顶的那叠（**3 − X** 张，按序放回）；下排 = 获得的那叠（**X** 张）；
``reorder=True`` = 行内与跨行都能自由置换（用户："弹窗内所有卡牌可以自由上下左右交换"）；
``enumerate_candidates=False``（`TwoRowArrange` 的缺省）= 界面自己构造答案、服务端只校验
形状（张数 + 切分 = 3 − X 由规格的 ``top`` 保证）。

> 视觉差异：现有排列弹窗画"两叠牌"、不画**空槽位**，故下行在 X 张就位前是空的
> （顶行仍显示全部 3 张）；"必须把下行填满"由固定切分（``split`` 恒 = 3 − X）在提交校验里
> 保证——少一张 / 多一张都会被拒。X = 0 时窗口照出（上行 3 张、下行 0 张，可直接提交）。
"""


def _zuilun_apply(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """提交点：获得下排、上排按序放回牌堆顶；X = 0 时再走"你与一名其他角色各失 1 点体力"。"""
    owner = int(frame.data["skill_owner"])
    action = answers.get("cards")
    if not isinstance(action, ArrangeCards):
        return None
    cards = watched_cards(frame, ZUILUN_FLOW, "cards")
    split = int(action.split)
    order = tuple(int(index) for index in action.order)
    gained = tuple(cards[index] for index in order[split:])
    back = tuple(cards[index] for index in order[:split])
    x = len(cards) - split
    if not frame.data.get(_ZUILUN_GAINED):
        if gained:
            control = host.move_in_frame(
                frame,
                gained,
                to_zone=ZoneRef(Zone.HAND, owner),
                reason=MoveReason.PUT,
                owner=owner,
                actor=owner,
            )
            if control == "stay":
                return "stay"
        frame.data[_ZUILUN_GAINED] = True
    if not frame.data.get(_ZUILUN_PLACED):
        place_on_deck(host.state, back, to_top=True)
        frame.data[_ZUILUN_PLACED] = True
    if x:
        return None
    if frame.data.get(_ZUILUN_DONE):
        return None
    victim = frame.data.get(_ZUILUN_VICTIM)
    if victim is None:
        pool = _other_alive_seats(host.state, frame, owner)
        if not pool:
            frame.data[_ZUILUN_DONE] = True
            return None
        answer = host.ask_option(
            frame,
            owner,
            Timing.PHASE_FINISH,
            skill_id="zuilun:victim",
            options=tuple(seat_choice(seat) for seat in pool),
            optional=False,
        )
        if host.suspended(answer):
            return "stay"
        victim = option_seat(answer)
        frame.data[_ZUILUN_VICTIM] = victim
    stage = int(frame.data.get(_ZUILUN_STAGE, 0))
    if stage == 0:
        frame.data[_ZUILUN_STAGE] = 1
        host.push_frame(
            FrameKind.LIFE_DECREASE,
            {"target": owner, "source": None, "amount": 1},
            timing=Timing.LIFE_DECREASE_BEFORE,
        )
        return "stay"
    if stage == 1:
        frame.data[_ZUILUN_STAGE] = 2
        host.push_frame(
            FrameKind.LIFE_DECREASE,
            {"target": victim, "source": None, "amount": 1},
            timing=Timing.LIFE_DECREASE_BEFORE,
        )
        return "stay"
    frame.data[_ZUILUN_DONE] = True
    return None


ZUILUN_FLOW = SkillFlow(
    name="zuilun",
    steps=(Arrange(key="cards", spec=ZUILUN_ARRANGE),),
    effect=_zuilun_apply,
)
"""【罪论】的流水线：一次两行排列 → 提交点（拿 / 放回 / X=0 的失体力段）。"""


def _own_phase(host: Any, frame: Any) -> bool:
    """帧外阶段时机上"这一步是问我的"（阶段体帧的 ``source`` = 当前回合角色）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    return owner is not None and frame.data.get("source") == owner


_ZUILUN = SkillDef(
    skill_id=ZUILUN,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(Timing.PHASE_FINISH, flow=ZUILUN_FLOW, condition=_own_phase),
    ),
    deck_arrange=ZUILUN_ARRANGE,
    requirement=(
        "结束阶段，你可以观看牌堆顶的三张牌，获得其中X张（X为你满足的选项数），"
        "然后以任意顺序放回其余的牌：1.本回合造成过伤害；2.本回合未弃置过牌；"
        "3.手牌数为全场最少。若均不满足，你与一名其他角色各失去1点体力"
    ),
)
"""诸葛瞻【罪论】的完整声明。"""

_FUYIN = SkillDef(
    skill_id=FUYIN,
    locked=True,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(
            Timing.USE_AFTER_BECOME_TARGET,
            handler=fuyin_effect,
            condition=fuyin_condition,
        ),
    ),
    requirement=(
        "锁定技，当你每回合第一次成为【杀】或【决斗】的目标后，"
        "若你的手牌数不大于使用者，此牌对你无效"
    ),
)
"""诸葛瞻【父荫】的完整声明（锁定技：不问发动，满足即生效）。"""

LEI_SKILL_DEFS = (*LEI_SKILL_DEFS, _ZUILUN, _FUYIN)
"""雷包已落地的武将技能（逐票追加）：镇骨 / 从谏 / 罪论 / 父荫（雄乱住 A 族）。"""


# ---------------------------------------------------------------------------
# 神甘宁【劫营】（lei-shenganning 票 01；用户 2026-11-07 逐条签）
#
# 牌面（逐字；OL 现行文本，见票面 §0.1 的三平台对照）：
#
#     劫营
#     　①回合开始时，若场上没有角色有“营”标记，你获得“营”标记；
#     　②结束阶段，你可以将“营”标记交给一名其他角色；
#     　③有“营”标记的其他角色的回合结束时，移去“营”标记，然后你获得其所有手牌。
#     　【“营”标记：你于摸牌阶段多摸一张牌、出牌阶段使用【杀】的次数上限+1、
#     　  且你本回合手牌上限+1。】
#
# 用户 2026-11-07 的两条裁定（`.scratch/lei-shenganning/issues/91-jieying-platform.md`）：
#
# - **③ 的时机 = 「回合结束时」**（`TURN_END`，与郝昭【镇骨】同一时点；移动版「回合结束后」
#   已登记为平台差异、**不实现**）。两条技能在同一个 `TURN_END` 撞上时**由该座位自选顺序**
#   （框架票 95）：它们都是不可放弃的强制段 ⇒ 那一张面**没有 Pass**、必须选一条。
# - **营的三个加成是「标记本身的效果」**（框架票 94）：`Marker.effect` 指向下面那条
#   **效果定义**（`carrier = MARKER`），不是本技能的钩子 / 修饰器 ⇒ **【劫营】失效（被无效）
#   也照常享受**；寿命是 2018 口径的通则（shan-baozuoci 票 01）：来源**阵亡**或来源**失去**
#   【劫营】都清（不再有开关）。**失效不动标记**。
#
# 标记记法：全场**最多一枚**「营」（① 只在场上没有营时发）。它的 `source_seat` 恒是
# 神甘宁（`count` 恒 1，不做计数），持有者随 ② 转移；③ 之后清空 —— 于是"谁有营"只需
# 读持有者身上的那枚标记。
#
# 记法带来的一个直接后果：① 的"场上没有角色有营"按**标记名**全场判（含别人身上的），
# 而不是"我自己没有"——否则神甘宁把营交出去之后，自己每个回合开始都会再获得一枚。
# ---------------------------------------------------------------------------

SHEN_GANNING = "shen_ganning"
"""神甘宁的武将 id（【魄袭】住 A 族 `active.py`，与【雄乱】同一姿态：出牌阶段主动技的
候选 / 发动主体都归那一族）。"""

JIEYING = hero_skill_id(SHEN_GANNING, "jieying")
"""神甘宁【劫营】的全限定技能 id。"""

JIEYING_MARK = "营"
"""【劫营】标记名（中文，与「镇骨」「梦魇」同姿态）。"""

JIEYING_EFFECT = hero_skill_id(SHEN_GANNING, "jieying_mark")
"""「营」的**标记自身效果**定义 id（框架票 94 的 `Marker.effect`）。

它是 `CATALOG` 里的一条 `carrier = CarrierKind.MARKER` 定义：**不是**神甘宁拥有的技能
（武将面上没有它、按钮区也不列），只在有「营」标记时由 `resolve_instances` 按标记产出。
"""


def _ying_markers(state: GameState, seat: int, holder: int) -> Marker | None:
    """``holder`` 身上由 ``seat`` 的【劫营】放的那枚「营」标记（没有 ⇒ ``None``）。"""
    for marker in state.player(holder).markers:
        if marker.name == JIEYING_MARK and marker.source_seat == seat:
            return marker
    return None


def _ying_anywhere(state: GameState) -> bool:
    """场上**有没有角色**带「营」标记（① 的触发前提；按标记名判，不看来源）。"""
    return any(
        marker.name == JIEYING_MARK
        for player in state.players
        for marker in player.markers
    )


def _jieying_grant_condition(host: Any, frame: Any) -> bool:
    """① 的"关于我"判据：**自己的**回合开始（强制段，不问"是否发动"）。

    ``TURN_START`` 是每个座位都会派发的帧外时机，故必须自校"这一步是我的回合开始"
    （与神诸葛亮【狂风】的到期清扫同一判据）。

    "场上有没有「营」"读的是**局面**、不是"这一步是不是我的"——它放在回调里（见
    :func:`_jieying_grant`）而不是这里：钩子条件要能被"持有者自校"的机械检查逐条证伪
    （`tests/test_hero_catalog.py` 的 `_owner_probes`），而局面前提会让同一条技能的三条
    钩子互相排斥（①要求场上无营，②③要求场上有营）。
    """
    del host
    owner = frame.data.get(MATCH_OWNER_KEY)
    return owner is not None and frame.data.get("source") == owner


def _jieying_grant(host: Any, frame: Any) -> Any:
    """① 的强制段：场上没有「营」时，自己获得一枚（带标记自身效果）。

    「若场上没有角色有营标记」= 按**标记名**全场判（含**别人**身上的那一枚）：神甘宁把营
    交出去之后，自己每个回合开始都不该再获得一枚。
    """
    owner = int(frame.data["skill_owner"])
    if _ying_anywhere(host.state):
        return None
    host.add_marker(
        frame,
        owner,
        name=JIEYING_MARK,
        source_seat=owner,
        source_skill_id=JIEYING,
        count=1,
        effect=JIEYING_EFFECT,
    )
    return None


def _jieying_hand_over_condition(host: Any, frame: Any) -> bool:
    """② 的触发条件：**自己的**结束阶段 ∧ 营在我身上 ∧ 还有一名其他存活角色。

    「可以」⇒ 非锁定（派发环会先问"是否发动"）；池空时连问都不发（`edge-cases.md` 总纪律 1）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None or frame.data.get("source") != owner:
        return False
    if _ying_markers(host.state, int(owner), int(owner)) is None:
        return False
    return bool(_other_alive_seats(host.state, frame, int(owner)))


def _jieying_hand_over(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """② 的提交点：先把营交给选中的人，再从自己身上移去（同一枚标记，来源不变）。

    先加后移是刻意的：两次写入之间没有钩子会跑，但"场上瞬间没有营"不该出现——① 的判据
    读全场，这条次序让它永远读不到那个瞬时态。
    """
    owner = int(frame.data["skill_owner"])
    target = next(iter(int(value) for value in answers.get("seat", ())), None)
    if target is None:
        return None
    host.add_marker(
        frame,
        target,
        name=JIEYING_MARK,
        source_seat=owner,
        source_skill_id=JIEYING,
        count=1,
        effect=JIEYING_EFFECT,
    )
    host.remove_marker(
        frame,
        owner,
        name=JIEYING_MARK,
        source_seat=owner,
        source_skill_id=JIEYING,
    )
    return None


JIEYING_FLOW = SkillFlow(
    name="jieying",
    steps=(
        PickSeats(
            lo=1,
            hi=1,
            pool=_other_alive_seats,
            optional=False,
            key="seat",
        ),
    ),
    effect=_jieying_hand_over,
)
"""【劫营】② 的一步流水线：选一名其他存活角色 → 把「营」交给他。"""


def _jieying_take_condition(host: Any, frame: Any) -> bool:
    """③ 的触发条件：**别人的**回合结束时，他带着**我放的**「营」。

    两个"不是"：回合角色不能是我自己（牌面写「有营标记的**其他**角色」）；营的来源必须是
    我（别人的【劫营】放的营不归我拿）。两者都不成立时连钩子都不跑。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    turn = frame.data.get("source")
    if owner is None or turn is None or int(turn) == int(owner):
        return False
    return _ying_markers(host.state, int(owner), int(turn)) is not None


def _jieying_take(host: Any, frame: Any) -> Any:
    """③ 的强制段：移去「营」，然后**我获得他所有手牌**（一次搬牌，不逐张触发 ③）。

    「然后」的次序是牌面写死的：先移标记（此刻起他不再享受三个加成——三个加成是标记自身
    效果、随标记存续），再搬牌。搬牌会内联派发"失去牌后"（【连营】/【枭姬】）而挂起，
    故两个段落各有帧私有游标：重入既不重移标记、也不重搬牌（G7）。
    """
    owner = int(frame.data["skill_owner"])
    turn = int(frame.data["source"])
    if not frame.data.get(_JIEYING_TAKEN):
        frame.data[_JIEYING_TAKEN] = True
        host.remove_marker(
            frame,
            turn,
            name=JIEYING_MARK,
            source_seat=owner,
            source_skill_id=JIEYING,
        )
    cards = frame.data.get(_JIEYING_CARDS)
    if cards is None:
        # 搬哪些牌**发牌时定格**：搬牌会内联派发"失去牌后"，对方在那时可能摸到新牌
        # （【连营】）；重入时再读一次手牌就会把那几张新牌也搬走（G7 的实机形态）。
        cards = tuple(host.state.player(turn).hand)
        frame.data[_JIEYING_CARDS] = cards
    if not cards or frame.data.get(_JIEYING_MOVED):
        return None
    # 游标在搬牌**之前**落：`move_in_frame` 是"先搬完、再内联派发钩子"，钩子挂起时
    # 这一次搬牌已经发生，续跑不得再来一遍。
    frame.data[_JIEYING_MOVED] = True
    control = host.move_in_frame(
        frame,
        host.state.refs(cards),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    return None


_JIEYING_TAKEN = "jieying_marker_removed"
"""帧私有：③ 的「移去营」已经落过（重入不重移）。"""

_JIEYING_CARDS = "jieying_hand_cards"
"""帧私有：③ 要搬的那批牌（发牌时定格；重入不重新读手牌）。"""

_JIEYING_MOVED = "jieying_hand_moved"
"""帧私有：③ 的「获得其所有手牌」已经搬过（游标在搬牌**之前**落；搬牌可能挂起）。"""


# ---------------------------------------------------------------------------
# 「营」的**标记自身效果**（框架票 94 的接线；用户 2026-11-07：「这个是标记本身的效果…
# 所以就算劫营这个技能失效了，依然享受以上所说的 3 种加成」）
#
# 三条加成各落一个既有挂点，**不新增机制**：
#   ① 摸牌阶段多摸一张 ⇒ `PHASE_DRAW` 强制钩子 + `set_draw_phase_count`（**折进基础张数**，
#      与【英姿】同一个数：一次摸 N 张，不是"摸 N 再摸 1"）；
#   ② 出牌阶段【杀】的次数上限 +1 ⇒ `COUNT_LIMIT` 修饰器（只改【杀】族，照【咆哮】）；
#   ③ 本回合手牌上限 +1 ⇒ `HAND_LIMIT_REPLACE` 的**持续加式**（账本 L852 血裔口径）。
#
# 三条都自校"实例的持有者就是被查询 / 正在摸牌的那个座位"（`ctx.owner == ctx.subject`、
# `MATCH_OWNER_KEY == source`）：修饰器链遍历全体存活实例，缺这一条会替所有人改值。
# ---------------------------------------------------------------------------


def _ying_draw_condition(host: Any, frame: Any) -> bool:
    """①' 「摸牌阶段多摸一张」只给**持有者自己的**摸牌阶段。

    ``hook_owner`` = 标记持有者（条件求值期由派发环写），``source`` = 当前回合角色；两者不
    相等时这张加成与他无关——缺这一条会替全场每个摸牌的人各加一张。
    """
    del host
    owner = frame.data.get(MATCH_OWNER_KEY)
    return owner is not None and frame.data.get("source") == owner


def _ying_extra_draw(host: Any, frame: Any) -> Any:
    """①' 把本阶段的基础摸牌数 +1（`set_draw_phase_count` 是唯一写入口）。"""
    seat = int(frame.data["skill_owner"])
    host.set_draw_phase_count(seat, host.draw_phase_count() + 1)
    return None


def _ying_slash_limit(state: GameState, ctx: Any, current: Any) -> Any:
    """②' 「出牌阶段使用【杀】的次数上限+1」——只改【杀】族、只改持有者本人的次数。

    牌面判据走 :func:`~sgs.effects.is_slash`（火 / 雷 / 转化 / 虚拟【杀】同族，照【咆哮】）。
    回合外 `check_count` 本就不把【杀】当限制读（规则 §3.5 L182–L187 / 账本 L796），故这里
    不再判阶段——多改的那 1 点在那里没有读者。
    """
    del state
    if ctx.owner != ctx.subject or ctx.card is None:
        return current
    if not is_slash(behavior_for(ctx.card)):
        return current
    if current is None:
        # 无次数限制（【诸葛连弩】/【咆哮】）保持无限——**不加**在 None 上。
        return None
    return int(current) + 1


def _ying_hand_limit(state: GameState, ctx: Any, current: Any) -> Any:
    """③' 「本回合手牌上限+1」——查询期的**持续加式**（标记一走即消失，不是一次性加式）。"""
    del state
    if ctx.owner != ctx.subject:
        return current
    return current + 1


_JIEYING_MARK_EFFECT = SkillDef(
    skill_id=JIEYING_EFFECT,
    locked=False,
    # 载体单列一档：它不是任何角色"拥有"的技能（见 `CarrierKind.MARKER` 的 docstring）。
    carrier=CarrierKind.MARKER,
    skill_type=SkillType.NORMAL,
    hooks=(
        Hook(
            Timing.PHASE_DRAW,
            handler=_ying_extra_draw,
            condition=_ying_draw_condition,
            forced=True,
        ),
    ),
    modifiers=(
        Modifier(ModifierPoint.COUNT_LIMIT, _ying_slash_limit),
        Modifier(ModifierPoint.HAND_LIMIT_REPLACE, _ying_hand_limit),
    ),
    requirement="摸牌阶段多摸一张牌；出牌阶段使用【杀】的次数上限+1；手牌上限+1",
)
"""「营」的三条自身加成（`Marker.effect` 指向这里）。

`locked=False` + `Hook.forced=True`：钩子不问"是否发动"（标记的加成不由玩家选），但这条
定义**不是锁定技**——失效与否由载体决定（`SkillSourceKind.MARKER` 永不受失效影响）。
"""


_JIEYING = SkillDef(
    skill_id=JIEYING,
    locked=False,
    carrier=CarrierKind.HERO,
    skill_type=SkillType.NORMAL,
    hooks=(
        # ① 回合开始时（强制段：牌面没有「可以」）。
        Hook(
            Timing.TURN_START,
            handler=_jieying_grant,
            condition=_jieying_grant_condition,
            forced=True,
        ),
        # ② 结束阶段（「可以」⇒ 可选段 + 一步选座流水线）。
        Hook(
            Timing.PHASE_FINISH,
            flow=JIEYING_FLOW,
            condition=_jieying_hand_over_condition,
        ),
        # ③ 回合结束时（强制段；与【镇骨】同一时点 ⇒ 同一座位同时触发时由玩家选顺序）。
        Hook(
            Timing.TURN_END,
            handler=_jieying_take,
            condition=_jieying_take_condition,
            forced=True,
        ),
    ),
    requirement=(
        "回合开始时，若场上没有角色有“营”标记，你获得“营”标记；结束阶段，"
        "你可以将“营”标记交给一名其他角色；有“营”标记的其他角色的回合结束时，"
        "移去“营”标记，然后你获得其所有手牌"
    ),
    step_requirements={"seat": "将“营”标记交给一名其他角色"},
)
"""神甘宁【劫营】的完整声明（一条技能、三条钩子；标记自身效果住 :data:`_JIEYING_MARK_EFFECT`）。"""

LEI_SKILL_DEFS = (*LEI_SKILL_DEFS, _JIEYING, _JIEYING_MARK_EFFECT)
"""雷包已落地的武将技能（逐票追加）：镇骨 / 从谏 / 罪论 / 父荫 / 劫营 + 「营」的标记效果。

【魄袭】（神甘宁）住 `active.py` 的 A 族；`hero.shen_ganning.jieying_mark` 是**标记自身
效果的定义**（`carrier = MARKER`），不是武将技能，但它同样在 `CATALOG` 里。
"""
