r"""出牌阶段**主动技**（A 族）的候选与发动（票 04）。

A 族与其他族的形态差别只有一处：它的候选**不是某张牌的**使用 / 打出，而是**技能本身**
作为出牌阶段空闲时间点的一个行动（票 04 §要做什么 1）。于是落点是：

- **候选**：每条技能一个 ``Modifier(ModifierPoint.CANDIDATES, …)``，只在
  ``CandidateScope.PLAY`` 的**整段追加**里生效（与 H 族的虚拟牌同一姿态）。
  载荷（弃哪些牌 / 给哪些牌 / 选哪名角色）**一律烘进候选**：
  ``picks`` = 实体牌 id（出牌阶段没有"选择面"，回调拿到的就是实体牌），
  ``choices`` = 目标座位（``"seat:<座位号>"``，结构见 :func:`_seat_choice`）。
  **付不起的组合不进池**（手牌不足 / 没有合法目标 ⇒ 一个候选都不造）。
- **发动**：引擎收到 ``ActivateSkill`` 后压一个 ``FrameKind.SKILL`` 帧，步骤表按
  ``skill_id`` 查 :data:`ACTIVATIONS` 通用派发（:func:`activate_skill`）。回调签名与
  ``OPS`` 相同（``(host, frame) -> Control``），故提问让出控制权、压子帧都能自然沿用
  既有的帧内游标范式（票 19 §8）。
- **每阶段限一次**：per-instance ``state.skill_state`` 的**位标记**（:data:`_USED`），
  在**候选生成期**就挡掉用过的技能（不是进池后拒绝）；复位走
  :func:`sgs.skills.reset_phase_skill_state`（引擎在 ``PHASE_PLAY_START`` 调用）。

**组合上限闸**（票 04 §要做什么 3）：**两维相乘**的技能（【青囊】1 张 × 目标、【离间】
1 张 × 有序男性对、【结姻】2 张 × 目标）在 ``组合数 > CANDIDATE_CAP`` 时**降级为顺序
多轮**——候选只保留"目标"这一维，张数由发动后的 ``SelectionSpec(sequential=True)``
逐张询问补齐；**结果集合不变**（逐张给与一次给到达同一终局）。一维的 2ⁿ（【制衡】）
与一维的目标对（【反间】）按票面**不设闸**。

**【仁德】是闸门的例外**（用户裁定）：它的前端交互是"直接多选手牌 + 直接点座位、一步
交出去"，界面必须能一次提交"这组牌 → 这个座位"，故它**不降级**、非空子集 × 目标全部
进池（手牌 8 张 × 7 目标约 1800 条候选，换掉闸门换来交互）。它同时**没有"出牌阶段限
一次"**（规则 976）：``_can_offer(..., once_per_phase=False)``，每轮出牌阶段都在池里。

**苦肉的形状**（裸发动 + 失去体力）：本模块不登记【苦肉】（黄盖不在 16 名名单，
票 12 的 46 条计数也不含它），但机制支持——``ActivateSkill`` 的载荷可全空
（``picks == ()``、``choices == ()``），回调照常经 SKILL 帧执行；
"失去体力"用 ``host.apply_hp_change`` 直接写体力，**不解封** ``LIFE_LOSE_*``。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from types import MappingProxyType
from typing import Any

from sgs.data import Gender, SkillType, Suit
from sgs.effects import (
    CandidateScope,
    CarrierKind,
    DamageNature,
    Modifier,
    ModifierContext,
    ModifierPoint,
    check_targets,
)
from sgs.state import (
    ActivateSkill,
    FrameKind,
    GameState,
    MoveReason,
    SelectionSpec,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import (
    HeroSkillDecl,
    SkillDef,
    _move_by_source,
    instance_key,
    resolve_instances,
)
from .virtual import pure_virtual_card

__all__ = [
    "ACTIVATIONS",
    "ACTIVE_SKILLS",
    "CANDIDATE_CAP",
    "ActiveSkill",
    "activate_skill",
    "active_skill_definitions",
]

CANDIDATE_CAP = 128
"""单次询问候选数的组合上限（票 04 §要做什么 3 / `spec.md` §机制面的统一约定）。

只对**两维相乘**的技能启用（【仁德】/【青囊】/【离间】/【结姻】）；一维的 2ⁿ（【制衡】）
与一维的目标对（【反间】）不设闸。
"""

_USED = 1
"""per-instance 阶段位：本出牌阶段已经发动过（候选生成期据此挡掉）。"""
_RECOVERED = 2
"""per-instance 阶段位：【仁德】"首次给到第 2 张"的回复已经发生过。"""

_SEAT_PREFIX = "seat:"
"""``ActivateSkill.choices`` 里目标座位的**规范文本前缀**（见 :func:`_seat_choice`）。"""

_SUIT_OPTIONS: tuple[str, ...] = tuple(suit.value for suit in Suit)
"""【反间】让目标选的四种花色；取值序 = :class:`~sgs.data.Suit` 的枚举序（确定性）。"""


# ---------------------------------------------------------------------------
# per-instance 阶段状态：位标记（候选生成期与发动回调共用）
# ---------------------------------------------------------------------------


def _skill_key(state: GameState, owner: int, skill_id: str) -> str | None:
    """``(owner, skill_id)`` 那条**存活实例**的 ``skill_state`` 键；没有则不落状态。"""
    for instance in resolve_instances(state):
        if instance.owner == owner and instance.skill_id == skill_id:
            return instance_key(instance)
    return None


def _phase_flags(state: GameState, owner: int, skill_id: str) -> int:
    """本实例当前的阶段位集合（实例不在 ⇒ ``0``，即"没用过"）。"""
    key = _skill_key(state, owner, skill_id)
    return 0 if key is None else state.skill_state.get(key, 0)


def _mark_phase(state: GameState, owner: int, skill_id: str, bit: int) -> None:
    """置一个阶段位（幂等；状态键与 ``prune_skill_state`` 的"存活实例键"同形）。"""
    key = _skill_key(state, owner, skill_id)
    if key is not None:
        state.skill_state[key] = state.skill_state.get(key, 0) | bit


# ---------------------------------------------------------------------------
# 载荷编解码与公共原语
# ---------------------------------------------------------------------------


def _seat_choice(seat: int) -> str:
    """目标座位的候选载荷文本（``ActivateSkill.choices`` 的一项）。

    ``choices`` 是协议里留给 M3"由发动者自己选"的接口位（票 30 的 ``ActivateSkill``
    docstring）；主动技的目标座位正是"发动者自己选"，故烘进这里而不是 ``picks``
    （后者一律是实体牌 id）。带前缀使记录流里"这是座位不是选项"可读。
    """
    return f"{_SEAT_PREFIX}{seat}"


def _chosen_seats(frame: Any) -> tuple[int, ...]:
    """把 ``frame.data["choices"]`` 解回座位元组（顺序即候选里给的顺序）。"""
    return tuple(
        int(choice.removeprefix(_SEAT_PREFIX)) for choice in frame.data.get("choices", ())
    )


def _can_offer(state: GameState, ctx: ModifierContext, *, once_per_phase: bool = True) -> bool:
    """本技能此刻能不能进 PLAY 池：整段追加 + 本人 + （可选）本阶段没用过。

    ``once_per_phase=False`` 用于**没有"出牌阶段限一次"**的主动技（【仁德】：
    规则 976 只给"任意数量"、没有次数上限），它每轮出牌阶段询问都该重新进池。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return False
    if ctx.scope is not CandidateScope.PLAY:
        return False
    if not once_per_phase:
        return True
    return not (_phase_flags(state, ctx.owner, ctx.skill_id) & _USED)


def _over_cap(*dimensions: int) -> bool:
    """这些维度相乘是否超过 :data:`CANDIDATE_CAP`（两维相乘技能的过闸判据）。"""
    total = 1
    for dimension in dimensions:
        total *= dimension
    return total > CANDIDATE_CAP


def _pay_sequential(
    host: Any,
    frame: Any,
    owner: int,
    *,
    needed: int,
    key_prefix: str,
    zones: tuple[Zone, ...],
) -> tuple[int, ...] | None:
    """顺序多轮付成本：一次问一张、**问中即弃置**，凑够 ``needed`` 张。

    返回 ``None`` = 已让出控制权（调用方 ``return "stay"``）；否则返回累计弃置的实体牌 id。
    进度（``round`` / ``chosen``）存帧私有载荷，故重入从原处接着走（票 19 §8 的帧内游标）。

    **逐张立即搬走**是必须的：若先把位置攒起来再统一移动，下一轮会用同一份手牌重铺选择面，
    同一个位置会被选中两次（同一个成本付两遍）。
    """
    index = frame.data.get("round", 0)
    chosen = list(frame.data.get("chosen", ()))
    while len(chosen) < needed:
        picked = host.ask_selection(
            frame,
            key=f"{key_prefix}:{index}",
            actor=owner,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(zones=zones, total=(1, 1), sequential=True),
            build_face=lambda: host.selection_face(owner, zones, reveal_hand=True),
        )
        if host.suspended(picked):
            return None
        if not picked:
            break
        if (
            _move_to_discard(
                host, frame, picked, owner=owner, key=f"{key_prefix}:{index}"
            )
            == "stay"
        ):
            return None
        chosen.extend(picked)
        index += 1
        frame.data["chosen"] = tuple(chosen)
        frame.data["round"] = index
    return tuple(chosen)


def _move_to_discard(
    host: Any, frame: Any, card_ids: tuple[int, ...], *, owner: int, key: str
) -> Any:
    """把选中的牌按**来源区**分组弃置（一次 ``move_card`` 只能有一个来源区）。

    与【贯石斧】的成本移动是**同一个形状**，故直接复用
    :func:`~sgs.skills.catalog._move_by_source`（code-review C10 去重）：plan / done 游标、
    "内联 ``MOVE_AFTER`` 挂起时透传 ``"stay"``"的重入纪律都只有一份实现。
    这里的 ``to_zone`` 恒为弃牌堆、``reason`` 由 helper 恒取 ``DISCARD``。
    """
    return _move_by_source(
        host,
        frame,
        card_ids,
        to_zone=ZoneRef(Zone.DISCARD),
        owner=owner,
        key=key,
    )


def _push_recover(host: Any, frame: Any, *, source: int, target: int, amount: int = 1) -> None:
    """压一个 ``RECOVER`` 子帧（回复量由帧算、写体力走唯一原语；超过上限由帧夹住）。"""
    host.push_frame(
        FrameKind.RECOVER,
        {
            "source": source,
            "targets": (target,),
            "card": None,
            "use_timing": frame.data.get("use_timing"),
            "amount": amount,
        },
        timing=Timing.RECOVER_VALUE,
    )


def _push_damage(host: Any, frame: Any, *, source: int, target: int, amount: int = 1) -> None:
    """压一个 ``DAMAGE`` 子帧（主动技的伤害无渠道牌，故 ``channel = None``）。"""
    host.push_frame(
        FrameKind.DAMAGE,
        {
            "source": source,
            "targets": (target,),
            "card": frame.data.get("card"),
            "use_timing": frame.data.get("use_timing"),
            "target": target,
            "channel": frame.data.get("card"),
            "nature": DamageNature.NORMAL,
            "chain": False,
            "amount": amount,
        },
        timing=Timing.DAMAGE_DEAL,
    )


# ---------------------------------------------------------------------------
# 孙权【制衡】：弃任意张（手牌 + 装备区）→ 摸等量（一维 2ⁿ，不设闸）
# ---------------------------------------------------------------------------


def _zhiheng_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    # 一维 2ⁿ：按票 04 §要做什么 3，**不设闸**（组合上限只对两维相乘的技能启用）。
    if not _can_offer(state, ctx):
        return current
    player = state.player(ctx.subject)
    cards = (*player.hand, *player.equip)
    return (
        *current,
        *(
            ActivateSkill(skill_id=ctx.skill_id, picks=picks, answers_seq=ctx.answers_seq)
            for size in range(1, len(cards) + 1)
            for picks in combinations(cards, size)
        ),
    )


def _zhiheng_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    _mark_phase(state, owner, frame.data["skill_id"], _USED)
    picks = tuple(frame.data.get("picks", ()))
    if _move_to_discard(host, frame, picks, owner=owner, key="zhiheng:cost") == "stay":
        return "stay"
    host.draw_for_hand(frame, owner, len(picks))
    return None


# ---------------------------------------------------------------------------
# 刘备【仁德】：给任意张手牌 → 本阶段首次给到第 2 张回复 1 点（两维相乘 ⇒ 闸 + 顺序降级）
# ---------------------------------------------------------------------------


def _rende_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx, once_per_phase=False):
        return current
    hand = tuple(state.player(ctx.subject).hand)
    targets = tuple(p.seat for p in state.players if p.alive and p.seat != ctx.subject)
    if not hand or not targets:
        return current
    # 【仁德】**不设组合闸**：它的前端交互是"直接多选手牌 + 直接点座位"，界面要能一次
    # 提交"这组牌 → 这个座位"，所以每一组（非空子集 × 目标）都必须在候选里。
    # 手牌 8 张 × 7 个目标约 1800 条候选（实测 ~227 KiB），在出牌阶段的可接受范围内；
    # 换掉闸门换来的是"没有选项按钮、一步交出去"这条交互（用户裁定）。
    return (
        *current,
        *(
            ActivateSkill(
                skill_id=ctx.skill_id,
                picks=picks,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for size in range(1, len(hand) + 1)
            for picks in combinations(hand, size)
            for target in targets
        ),
    )


def _give(
    host: Any, frame: Any, owner: int, target: int, card_ids: tuple[int, ...], *, key: str
) -> Any:
    """把 ``card_ids`` 从发动者手牌交给目标（``PUT`` 进目标手牌、归属 = 目标）。

    与 :func:`_move_to_discard` 同姿态：内联 ``MOVE_AFTER`` 挂起时透传 ``"stay"``；
    帧私有游标 ``key`` 让重入不把同一次交给做第二遍。
    """
    if not card_ids:
        return None
    done_key = f"{key}:done"
    if frame.data.get(done_key):
        return None
    frame.data[done_key] = True
    return host.move_in_frame(
        frame,
        host.state.refs(card_ids),
        to_zone=ZoneRef(Zone.HAND, target),
        reason=MoveReason.PUT,
        owner=target,
        actor=owner,
    )


def _rende_recover(
    host: Any, frame: Any, owner: int, skill_id: str, given: int
) -> bool:
    """累计给出 ``given`` 张时是否**首次**达到两张并回复 1 点；返回是否压了回复帧。"""
    state = host.state
    if given < 2 or (_phase_flags(state, owner, skill_id) & _RECOVERED):
        return False
    _mark_phase(state, owner, skill_id, _RECOVERED)
    _push_recover(host, frame, source=owner, target=owner)
    return True


def _rende_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    skill_id = frame.data["skill_id"]
    # **不置 ``_USED``**：【仁德】没有"出牌阶段限一次"（规则 976）；本阶段只置
    # ``_RECOVERED``（"首次给到第 2 张"回复一次）。
    target = _chosen_seats(frame)[0]
    picks = tuple(frame.data.get("picks", ()))
    if not picks:  # pragma: no cover - 候选恒带非空 picks；此处只作 fail-soft
        return None
    if _give(host, frame, owner, target, picks, key="rende:cost") == "stay":
        return "stay"
    _rende_recover(host, frame, owner, skill_id, len(picks))
    return None


# ---------------------------------------------------------------------------
# 周瑜【反间】：目标选花色 → 其获得并展示你的一张手牌 → 花色不同则受伤
# ---------------------------------------------------------------------------


def _fanjian_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    if not state.player(ctx.subject).hand:
        return current
    targets = tuple(p.seat for p in state.players if p.alive and p.seat != ctx.subject)
    return (
        *current,
        *(
            ActivateSkill(
                skill_id=ctx.skill_id,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for target in targets
        ),
    )


def _fanjian_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    skill_id = frame.data["skill_id"]
    _mark_phase(state, owner, skill_id, _USED)
    target = _chosen_seats(frame)[0]
    if "suit" not in frame.data:
        answer = host.ask_option(
            frame,
            target,
            Timing.PHASE_PLAY_IDLE,
            skill_id=skill_id,
            options=_SUIT_OPTIONS,
        )
        if host.suspended(answer):
            return "stay"
        frame.data["suit"] = answer.choice
    if "revealed" not in frame.data:
        picked = host.ask_selection(
            frame,
            key="fanjian",
            actor=target,
            timing=Timing.PHASE_PLAY_IDLE,
            spec=SelectionSpec(zones=(Zone.HAND,), total=(1, 1)),
            build_face=lambda: host.selection_face(owner, (Zone.HAND,)),
        )
        if host.suspended(picked):
            return "stay"
        card = state.card(picked[0])
        # 载荷先落帧、再搬牌：搬牌可能内联派发"失去牌后"并**挂起**（票 07），
        # 重入时据此跳过本段、直接续跑交付段（否则会把这次移动做第二遍）。
        frame.data["revealed"] = card.card_id
        if host.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.PROCESSING),
            reason=MoveReason.PUT,
            owner=target,
            actor=owner,
        ) == "stay":
            # 搬牌挂起 ⇒ **立刻让出控制权**，不继续做后面的交付与伤害：否则帧会在
            # 应答被消费之前继续推进，"是否发动"的应答会漏给**外层**询问（票 13 §验收 5）。
            return "stay"
    if "delivered" not in frame.data:
        card = state.card(frame.data["revealed"])
        frame.data["delivered"] = True
        if host.move_in_frame(
            frame,
            (card,),
            to_zone=ZoneRef(Zone.HAND, target),
            reason=MoveReason.PUT,
            owner=target,
            actor=owner,
        ) == "stay":
            return "stay"
    if "damage_done" not in frame.data:
        # 伤害**单列一个帧内游标**：交付那一步若挂起，重入必须只跑伤害、而不是连交付
        # 一起重做（把游标写在搬牌之前，正是为了让"搬完但没结算"也能被识别）。
        frame.data["damage_done"] = True
        card = state.card(frame.data["revealed"])
        if card.suit != frame.data["suit"]:
            _push_damage(host, frame, source=owner, target=target)
    return None


# ---------------------------------------------------------------------------
# 华佗【青囊】：弃 1 张手牌 → 一名已受伤角色回复 1 点（目标池含自己）
# ---------------------------------------------------------------------------


def _qingnang_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    hand = tuple(state.player(ctx.subject).hand)
    targets = tuple(p.seat for p in state.players if p.alive and p.hp < p.hp_max)
    if not hand or not targets:
        return current
    if _over_cap(len(hand), len(targets)):
        offers = [
            ActivateSkill(
                skill_id=ctx.skill_id,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for target in targets
        ]
    else:
        offers = [
            ActivateSkill(
                skill_id=ctx.skill_id,
                picks=(card_id,),
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for card_id in hand
            for target in targets
        ]
    return (*current, *offers)


def _qingnang_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    _mark_phase(state, owner, frame.data["skill_id"], _USED)
    target = _chosen_seats(frame)[0]
    picks = tuple(frame.data.get("picks", ()))
    if picks:
        if _move_to_discard(host, frame, picks, owner=owner, key="qingnang:cost") == "stay":
            return "stay"
    else:
        # 过闸降级：成本张数由顺序多轮补齐（每一轮问中即弃）。
        paid = _pay_sequential(
            host,
            frame,
            owner,
            needed=1,
            key_prefix="qingnang",
            zones=(Zone.HAND,),
        )
        if paid is None:
            return "stay"
    _push_recover(host, frame, source=owner, target=target)
    return None


# ---------------------------------------------------------------------------
# 貂蝉【离间】：弃 1 张牌 + 两名男性角色 → 视为其中一名对另一名使用【决斗】
# ---------------------------------------------------------------------------


def _lijian_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    player = state.player(ctx.subject)
    cards = (*player.hand, *player.equip)
    males = tuple(
        p.seat
        for p in state.players
        if p.alive and p.gender is Gender.MALE and p.seat != ctx.subject
    )
    if not cards or len(males) < 2:
        return current
    # 【离间】视为使用【决斗】**强制指定目标**，但强制不等于绕过"不能成为目标"的锁定技
    # （票 06 §要做什么 2 的同源要求）：受害者的合法性读与真牌同一条 ``check_targets``
    # 链（【空城】据此出池）。使用者不受此限——"不能成为目标"只约束受害者一侧。
    duel = pure_virtual_card(state, "duel")
    duel_pools = {user: frozenset(check_targets(state, user, duel)) for user in males}
    pairs = tuple(
        (user, victim)
        for user in males
        for victim in males
        if victim != user and victim in duel_pools[user]
    )
    if not pairs:
        return current
    if _over_cap(len(cards), len(pairs)):
        offers = [
            ActivateSkill(
                skill_id=ctx.skill_id,
                choices=(_seat_choice(user), _seat_choice(victim)),
                answers_seq=ctx.answers_seq,
            )
            for user, victim in pairs
        ]
    else:
        offers = [
            ActivateSkill(
                skill_id=ctx.skill_id,
                picks=(card_id,),
                choices=(_seat_choice(user), _seat_choice(victim)),
                answers_seq=ctx.answers_seq,
            )
            for card_id in cards
            for user, victim in pairs
        ]
    return (*current, *offers)


def _lijian_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    _mark_phase(state, owner, frame.data["skill_id"], _USED)
    user, victim = _chosen_seats(frame)
    picks = tuple(frame.data.get("picks", ()))
    if picks:
        if _move_to_discard(host, frame, picks, owner=owner, key="lijian:cost") == "stay":
            return "stay"
    else:
        paid = _pay_sequential(
            host,
            frame,
            owner,
            needed=1,
            key_prefix="lijian",
            zones=(Zone.HAND, Zone.EQUIP),
        )
        if paid is None:
            return "stay"
    # 使用者与目标在**候选生成期**已按同一条 ``check_targets`` 链定下（票 06），故这里
    # 不再重查"能否使用"的常规合法性，只走【决斗】自己的结算链——伤害来源随使用者
    # （不是貂蝉）。
    host.start_use(
        pure_virtual_card(state, "duel"),
        source=user,
        targets=(victim,),
        use_timing=Timing.PHASE_PLAY_IDLE,
    )
    return None


# ---------------------------------------------------------------------------
# 孙尚香【结姻】：弃 2 张手牌 + 一名已受伤男性 → 你与其各回复 1 点
# ---------------------------------------------------------------------------


def _jieyin_offers(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    if not _can_offer(state, ctx):
        return current
    hand = tuple(state.player(ctx.subject).hand)
    targets = tuple(
        p.seat
        for p in state.players
        if p.alive
        and p.seat != ctx.subject
        and p.gender is Gender.MALE
        and p.hp < p.hp_max
    )
    if len(hand) < 2 or not targets:
        return current
    card_count = len(hand) * (len(hand) - 1) // 2
    if _over_cap(card_count, len(targets)):
        offers = [
            ActivateSkill(
                skill_id=ctx.skill_id,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for target in targets
        ]
    else:
        offers = [
            ActivateSkill(
                skill_id=ctx.skill_id,
                picks=pair,
                choices=(_seat_choice(target),),
                answers_seq=ctx.answers_seq,
            )
            for pair in combinations(hand, 2)
            for target in targets
        ]
    return (*current, *offers)


def _jieyin_activate(host: Any, frame: Any) -> Any:
    state = host.state
    owner = frame.data["source"]
    _mark_phase(state, owner, frame.data["skill_id"], _USED)
    target = _chosen_seats(frame)[0]
    picks = tuple(frame.data.get("picks", ()))
    if picks:
        if _move_to_discard(host, frame, picks, owner=owner, key="jieyin:cost") == "stay":
            return "stay"
    else:
        paid = _pay_sequential(
            host,
            frame,
            owner,
            needed=2,
            key_prefix="jieyin",
            zones=(Zone.HAND,),
        )
        if paid is None:
            return "stay"
    _push_recover(host, frame, source=owner, target=target)
    _push_recover(host, frame, source=owner, target=owner)
    return None


# ---------------------------------------------------------------------------
# 登记：一条声明 = 候选回调 + 发动回调（两者共用同一个 skill_id）
# ---------------------------------------------------------------------------


ActivationHandler = Callable[[Any, Any], Any]
"""发动回调：``(host, frame) -> Control``，与 ``OPS`` / 钩子同形。"""


@dataclass(frozen=True, slots=True)
class ActiveSkill(HeroSkillDecl):
    """A 族一条技能的**声明**：候选构造 + 发动主体（票 04 §表一行）。"""

    offers: Callable[[GameState, ModifierContext, Any], Any]
    activate: ActivationHandler


ACTIVE_SKILLS: tuple[ActiveSkill, ...] = (
    # 孙权【制衡】：弃任意张（手牌 + 装备区）→ 摸等量（规则 1009）。
    ActiveSkill("sunquan", "zhiheng", _zhiheng_offers, _zhiheng_activate),
    # 刘备【仁德】：给一名其他角色任意张手牌；本阶段首次给到第 2 张回复 1 点（规则 976）。
    ActiveSkill("liubei", "rende", _rende_offers, _rende_activate),
    # 周瑜【反间】：令一名其他角色选花色 → 获得并展示你的一张手牌 → 花色不同则受伤（规则 1017）。
    ActiveSkill("zhouyu", "fanjian", _fanjian_offers, _fanjian_activate),
    # 华佗【青囊】：弃一张手牌 → 一名已受伤角色回复 1 点（规则 1040）。
    ActiveSkill("huatuo", "qingnang", _qingnang_offers, _qingnang_activate),
    # 貂蝉【离间】：弃一张牌 + 两名男性角色 → 视为其中一名对另一名使用【决斗】（规则 1049）。
    ActiveSkill("diaochan", "lijian", _lijian_offers, _lijian_activate),
    # 孙尚香【结姻】：弃两张手牌 + 一名已受伤男性 → 你与其各回复 1 点（规则 1033）。
    ActiveSkill("sunshangxiang", "jieyin", _jieyin_offers, _jieyin_activate),
)
"""A 族六条技能（票 04 §逐技能落位表；【苦肉】不在 16 名名单、只留形状，见模块 docstring）。"""


ACTIVATIONS: MappingProxyType[str, ActivationHandler] = MappingProxyType(
    {skill.skill_id: skill.activate for skill in ACTIVE_SKILLS}
)
"""``skill_id`` → 发动回调。引擎只按 :func:`activate_skill` 通用派发，不认识任何具体 id。"""

_REQUIREMENTS: dict[str, str] = {
    "zhiheng": "弃置任意张牌（手牌 / 装备区），然后摸等量的牌",
    "rende": "交给一名其他角色任意张手牌",
    "fanjian": "选择一种花色",
    "qingnang": "弃一张手牌，令一名已受伤角色回复 1 点体力",
    "lijian": "弃一张牌，选择两名男性角色",
    "jieyin": "弃两张手牌，选择一名已受伤男性角色",
}
"""A 族六条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""


def active_skill_definitions() -> tuple[SkillDef, ...]:
    """A 族六条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    非锁定（技能面都写了"你可以"）：候选由 ``CANDIDATES`` 挂点产出；``carrier = HERO``
    ⇒ 优先级档由载体派生；``skill_type = NORMAL``（无主公技）。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            modifiers=(Modifier(ModifierPoint.CANDIDATES, skill.offers),),
            requirement=_REQUIREMENTS[skill.short_id],
        )
        for skill in ACTIVE_SKILLS
    )


def activate_skill(host: Any, frame: Any) -> Any:
    """SKILL 帧的唯一步骤：按 ``frame.data["skill_id"]`` 查表派发发动主体。

    未登记 ⇒ fail-closed（加载期"启用武将的技能引用必须闭合"已在票 01 挡住漏配；
    运行期再撞到即编程错误，不静默跳过）。
    """
    handler = ACTIVATIONS.get(frame.data["skill_id"])
    if handler is None:
        raise NotImplementedError(f"技能 {frame.data['skill_id']!r} 没有登记发动回调")
    return handler(host, frame)
