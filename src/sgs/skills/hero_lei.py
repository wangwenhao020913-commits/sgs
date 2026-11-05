"""神话再临·雷包武将技能（``lei`` 包；01 票起 = 郝昭【镇骨】）。

本模块只装**雷包**的武将技能声明；族级原语（钩子 / 步骤流水线 / 标记 / 摸牌）一律
复用既有实现，不新增机制。

【镇骨】（郝昭，魏 / 男 / 4 体力；牌面权威 = 用户录入 ``技能.txt``，逐字）：

    结束阶段，你可以选择一名其他角色，你的回合结束时和该角色的下个回合结束时，
    其将手牌摸至或弃置至与你手牌数相同（摸牌时至多摸至五张）。

用户 2026-11-05 签的裁定（`.scratch/lei-batch/issues/90-zhengku-open-questions.md`）：

- **Q1 = A**：「其将手牌…弃置至」的**弃哪些牌由目标自己选**（`actor=目标` 的
  ``SELECT_CARDS``，与他受伤后弃牌的【刚烈】同一形态）；
- **Q2 = A**：「（摸牌时至多摸至五张）」= **手牌终值上限 5**（与【节命】的
  ``JIEMING_CAP`` 同一口径，不是"最多摸 5 张"）；
- **Q3 = A**：标记来源（郝昭）阵亡 ⇒ 标记随死亡清除、该次结算不发生
  （``clear_on_source_death=True``）；
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

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind
from sgs.state import (
    GameState,
    Marker,
    MoveReason,
    SelectionSpec,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef
from .flow import FlowAnswers, PickSeats, SkillFlow

__all__ = [
    "LEI_SKILL_DEFS",
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
    state: GameState, frame: Any, owner: int, answers: FlowAnswers
) -> tuple[int, ...]:
    """目标池：「一名其他角色」= 存活且不是持有者（默认读法（二）②）。"""
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
        clear_on_source_death=True,
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

LEI_SKILL_DEFS: tuple[SkillDef, ...] = (_ZHENGKU,)
"""雷包已落地的武将技能（逐票追加；包脚手架归 01 票）。"""


def lei_skill_definitions() -> tuple[SkillDef, ...]:
    """雷包（``lei``）武将技能的 :class:`~sgs.skills.catalog.SkillDef` 集合。"""
    return LEI_SKILL_DEFS
