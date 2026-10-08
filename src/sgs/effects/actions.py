"""候选行动的生成：**唯一真源**（票 07 §4）。

引擎在构造询问的那一刻调用 :func:`legal_actions_for`，把候选内联下发；
参与者的合法性 = 是否属于内联候选（成员判定）。因此不另写一份"校验器"——
候选与校验不可能漂移。

三个检查函数与官方 L3 的三个"使用前校验"步骤同名（**是校验步骤、不是时机**，
不得进时机枚举）：:func:`check_forbidden` / :func:`check_count` / :func:`check_targets`。

排序键由票 05 §4 定死：**牌实例 id 升序 → 同 id 再按行动类型名 → 无牌实例的排最后**，
规则层不得重排。

M1 已接线四类询问：出牌阶段（【杀】/【桃】）、【闪】响应窗口、弃牌阶段，
以及濒死求桃（票 21）。
"""

from __future__ import annotations

from itertools import combinations

from sgs.state import (
    Action,
    CardRef,
    DiscardCards,
    EndPhase,
    GameState,
    Pass,
    Player,
    QueryKind,
    UseCard,
)
from sgs.timing import Timing

from .behaviors import CardBehavior, UseContext, behavior_for

__all__ = [
    "attack_range",
    "check_count",
    "check_forbidden",
    "check_targets",
    "discard_count",
    "distance",
    "hand_limit",
    "legal_actions_for",
]


def hand_limit(player: Player) -> int:
    """手牌上限 = **体力值**（票 07 §4 的规则**公式**；M1 无修正项）。

    官方把"体力"（可小于 0）与作为参数读取的"体力值"分开：体力降到 0 或更低时，
    体力值读作 0，且手牌上限经过修正后最小值为 0 —— 故这里取 ``max(hp, 0)``。
    """
    return max(player.hp, 0)


def discard_count(player: Player) -> int:
    """弃牌阶段须弃置的张数；``<= 0`` 表示无需弃牌（也不发询问）。"""
    return len(player.hand) - hand_limit(player)


def distance(state: GameState, a: int, b: int) -> int:
    """座位环距离：两方向各数**中间存活角色数**，取较小者 **+ 1**（票 07 §4）。

    官方把阵亡角色从距离里排除，故中间的死人不计数；1v1 恒为 1。
    """
    count = len(state.players)
    if count == 0 or a == b:
        return 0
    forward = 0
    seat = (a + 1) % count
    while seat != b:
        if state.player(seat).alive:
            forward += 1
        seat = (seat + 1) % count
    backward = 0
    seat = (a - 1) % count
    while seat != b:
        if state.player(seat).alive:
            backward += 1
        seat = (seat - 1) % count
    return min(forward, backward) + 1


def attack_range(state: GameState, seat: int) -> int:
    """额定攻击范围：未装备武器 = 1（M1 无装备，恒 1）——**真写的函数**，不是常量。"""
    del state, seat
    return 1


def check_forbidden(state: GameState, seat: int, card: CardRef) -> bool:
    """L3.1 ``USE_CHECK_FORBIDDEN``：``True`` = 禁止使用。

    M1 没有"禁止使用"类效果，故恒 ``False``；函数保留（票 07 §4），
    M2 引入【空城】类技能时在函数体内长出来，而不是在候选生成器里加分支。
    """
    del state, seat, card
    return False


def check_count(state: GameState, seat: int, card: CardRef) -> bool:
    """L3.2 ``USE_CHECK_COUNT``：``True`` = 已超过本回合次数上限。"""
    behavior = behavior_for(card)
    limit = behavior.use_limit
    if limit.rated is None:
        return False
    used = state.player(seat).card_use_counts.get(card.type, 0)
    return used >= limit.rated + limit.extra


def check_targets(state: GameState, seat: int, card: CardRef) -> tuple[int, ...]:
    """L3.3 ``USE_CHECK_TARGETS``：这张牌当前的合法目标池（可为空）。

    额定目标数为 0 的响应牌（【闪】）返回空元组——它响应的是那张【杀】，不是座位。
    """
    rule = behavior_for(card).target_rule
    if rule.rated_targets == 0:
        return ()
    if rule.self_only:
        me = state.player(seat)
        if not me.alive or (rule.requires_wounded_self and me.hp >= me.hp_max):
            return ()
        return (seat,)
    pool: list[int] = []
    for player in state.players:
        if not player.alive:
            continue
        if player.seat == seat and not rule.includes_self:
            continue
        if rule.needs_distance and distance(state, seat, player.seat) > attack_range(state, seat):
            continue
        pool.append(player.seat)
    return tuple(pool)


def legal_actions_for(
    state: GameState, seat: int, kind: QueryKind, *, answers_seq: int, dying: int | None = None
) -> tuple[Action, ...]:
    """给出 ``seat`` 在 ``kind`` 询问下的全部候选，按票 05 的稳定键排序。

    ``dying`` 只在 ``PEACH`` 询问下需要：濒死语境里【桃】的合法目标是**濒死者**
    而不是应答者自己，故目标不走行为表的目标规则（票 07 §4 / 票 21 §7）。
    """
    if kind is QueryKind.PLAY_PHASE:
        candidates = _play_phase(state, seat, answers_seq)
    elif kind is QueryKind.DISCARD:
        candidates = _discard(state, seat, answers_seq)
    elif kind is QueryKind.DODGE:
        candidates = _dodge(state, seat, answers_seq)
    elif kind is QueryKind.PEACH:
        if dying is None:
            raise ValueError("PEACH 询问必须给出濒死者座位")
        candidates = _peach(state, seat, dying, answers_seq)
    else:
        raise ValueError(f"M1 还没有 {kind.value} 询问的候选生成器")
    return tuple(sorted(candidates, key=_sort_key))


def _play_phase(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    """出牌阶段：逐张开出可用牌 × 每个合法目标组合，最后是结束阶段。"""
    actions: list[Action] = []
    for card in _usable_hand(state, seat, UseContext.PLAY_PHASE):
        if check_count(state, seat, card):
            continue
        rated = behavior_for(card).target_rule.rated_targets
        for combo in combinations(check_targets(state, seat, card), rated):
            actions.append(UseCard(card=card, targets=tuple(combo), answers_seq=answers_seq))
    actions.append(EndPhase(answers_seq=answers_seq))
    return actions


def _discard(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    """弃牌阶段：任意"手牌数 − 体力值"张子集，全枚举（票 07 §4）。"""
    player = state.player(seat)
    count = discard_count(player)
    if count <= 0:
        return []
    return [
        DiscardCards(cards=state.refs(list(combo)), answers_seq=answers_seq)
        for combo in combinations(player.hand, count)
    ]


def _dodge(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    """【闪】的响应窗口：手牌里的每张【闪】各一个候选，放弃恒在最后。"""
    actions = [
        UseCard(card=card, targets=(), answers_seq=answers_seq)
        for card in _usable_hand(state, seat, UseContext.RESPONSE, Timing.RESOLVE_BEFORE_EFFECT)
    ]
    actions.append(Pass(answers_seq=answers_seq))
    return actions


def _peach(state: GameState, seat: int, dying: int, answers_seq: int) -> list[Action]:
    """濒死求桃：手牌里每张能在**濒死语境**使用的牌各一个候选，目标恒为濒死者。

    与出牌阶段【桃】的差别只在目标：出牌阶段的目标规则是"只能是自己且未满体力"，
    濒死语境的目标由求桃询问直接给出（官方 4.17"对濒死角色使用"）。放弃恒在最后，
    且**永远可选**——"放弃"是 PEACH 询问的一个候选而不是独立询问（票 05 §1）。
    """
    actions = [
        UseCard(card=card, targets=(dying,), answers_seq=answers_seq)
        for card in _usable_hand(state, seat, UseContext.NEAR_DEATH)
    ]
    actions.append(Pass(answers_seq=answers_seq))
    return actions


def _usable_hand(
    state: GameState, seat: int, context: UseContext, timing: Timing | None = None
) -> list[CardRef]:
    """手牌里在 ``context`` 下可用、且未被 L3.1 禁止的牌（按手牌序）。"""
    usable: list[CardRef] = []
    for card_id in state.player(seat).hand:
        card = state.card(card_id)
        if not _usable_in(behavior_for(card), context, timing):
            continue
        if check_forbidden(state, seat, card):
            continue
        usable.append(card)
    return usable


def _usable_in(behavior: CardBehavior, context: UseContext, timing: Timing | None) -> bool:
    return any(
        use.context is context and (timing is None or use.timing is timing)
        for use in behavior.use_timings
    )


def _sort_key(action: Action) -> tuple[int, tuple[int, ...], str]:
    if isinstance(action, UseCard):
        return (0, (action.card.card_id,), action.kind)
    if isinstance(action, DiscardCards):
        return (0, tuple(card.card_id for card in action.cards), action.kind)
    return (1, (), action.kind)
