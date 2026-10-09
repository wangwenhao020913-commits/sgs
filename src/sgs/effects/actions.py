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

from collections.abc import Callable
from itertools import combinations

from sgs.state import (
    Action,
    CardRef,
    DiscardCards,
    EndPhase,
    GameState,
    Pass,
    PlayCard,
    Player,
    QueryKind,
    RespondContext,
    RespondMode,
    SelectCards,
    SelectContext,
    SlotKind,
    UseCard,
    alive_seats,
    seats_from,
)
from sgs.timing import Timing

from .behaviors import CardBehavior, TargetCount, UseContext, behavior_for

__all__ = [
    "EFFECTIVENESS",
    "TARGET_FILTERS",
    "attack_range",
    "check_count",
    "check_forbidden",
    "check_targets",
    "delayed_settle_target",
    "discard_count",
    "distance",
    "hand_limit",
    "legal_actions_for",
    "target_is_effective",
    "targets_within_attack_range",
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
    """座位环距离：两个方向各数**中间存活角色数**，取较小者 **+ 1**（票 07 §4）。

    官方把阵亡角色从距离里排除，故中间的死人不计数；1v1 恒为 1。
    读取走**只读座位环真源**（票 23）：在座位环上取两方向的最短步数（只数存活者），
    与"数两方向中间的存活者再 +1"逐值相同（座位环上两方向中间的存活者数 = 步数 − 1）。
    """
    if a == b:
        return 0
    ring = alive_seats(state)
    if a in ring and b in ring:
        count = len(ring)
        forward = (ring.index(b) - ring.index(a)) % count
        return min(forward, count - forward)
    return _counting_distance(state, a, b)


def _counting_distance(state: GameState, a: int, b: int) -> int:
    """任一端不是存活者时的退化读法（引擎不可达：候选池只含存活者）。

    保留原"数两方向中间的存活者"公式，使函数对任意座位仍是全函数。
    """
    count = len(state.players)
    if count == 0:
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

    目标数为 0 的响应牌（【闪】/【无懈可击】）返回空元组——它们响应的是那张牌 /
    那次结算，不是座位。两个"全体存活"档的候选池由目标数来源枚举决定含不含自己。

    票 27 把**目标池限制**拆成两半，都不给 ``TargetRule`` 加面向具体牌名的布尔：

    - **额定距离上限** ``rule.max_distance``（【顺手牵羊】= 1，与武器无关）；
    - **附加谓词** ``behavior.target_filters``（【过河拆桥】/【顺手牵羊】"区域里有牌"、
      【借刀杀人】"有武器且有合法【杀】目标"），经 :data:`TARGET_FILTERS` 注册表解析。

    延时锦囊另有一条**规则层**限制：判定区按**牌名**禁同名（票 03 §5），故已有同名
    延时锦囊的目标不是合法目标（标准版下这是"不合法"的唯一来源，票 03 §6）。
    """
    behavior = behavior_for(card)
    rule = behavior.target_rule
    if rule.count is TargetCount.RATED and rule.rated_targets == 0:
        return ()
    if rule.self_only:
        me = state.player(seat)
        if not me.alive or (rule.requires_wounded_self and me.hp >= me.hp_max):
            return ()
        pool: list[int] = [seat]
    else:
        pool = []
        for player in state.players:
            if not player.alive:
                continue
            if player.seat == seat and not rule.includes_self_in_pool():
                continue
            if rule.needs_distance and distance(state, seat, player.seat) > attack_range(
                state, seat
            ):
                continue
            if rule.max_distance is not None and distance(
                state, seat, player.seat
            ) > rule.max_distance:
                continue
            pool.append(player.seat)
    if behavior.is_delayed:
        pool = [target for target in pool if not _same_named_in_judge(state, target, card)]
    pool = [
        target
        for target in pool
        if all(
            TARGET_FILTERS[name](state, seat, target) for name in behavior.target_filters
        )
    ]
    return tuple(pool)


def target_is_effective(state: GameState, card: CardRef, source: int, target: int) -> bool:
    """**第 2 层"无效"**（票 15 §5）：被指定者到自己的 C1 才被终止结算。

    ``True`` = 此牌对该目标有效、继续 C2–C5；``False`` = 终止对该目标的结算（故**不开**
    C2 的无懈窗口）。谓词全部内联在 :data:`EFFECTIVENESS` 的回调里（票 07 §4）；
    M2 的生产者只有【桃园结义】"对未受伤的角色无效"。
    """
    behavior = behavior_for(card)
    return all(EFFECTIVENESS[name](state, source, target) for name in behavior.effectiveness)


def targets_within_attack_range(state: GameState, seat: int) -> tuple[int, ...]:
    """``seat`` 能触及的座位：一名**其他存活角色**且距离 ≤ 其攻击范围。

    【借刀杀人】的目标合法性（第 0 层）与"持刀者使用【杀】"的候选都读它；
    距离与攻击范围各只在这里读一次，不复制公式，故函数本身与具体牌名无关。
    """
    return tuple(
        player.seat
        for player in state.players
        if player.alive
        and player.seat != seat
        and distance(state, seat, player.seat) <= attack_range(state, seat)
    )


def _has_zone_cards(state: GameState, source: int, target: int) -> bool:
    """目标的手牌 / 装备区 / 判定区里至少有一张牌（拆、顺的"区域里有牌"）。"""
    del source
    player = state.player(target)
    return bool(player.hand or player.equip or player.judge)


def _borrowed_sword_target(state: GameState, source: int, target: int) -> bool:
    """【借刀杀人】的第 0 层合法性：目标有**武器**且攻击范围内有合法【杀】目标。

    官方两次合法性检测的第一次（`A/card_scroll.txt:45`）：选择 An 时就要能指出一个 Bn，
    故没有合法【杀】目标者不能成为【借刀杀人】的目标。
    """
    del source
    player = state.player(target)
    has_weapon = any(state.card(card_id).slot is SlotKind.WEAPON for card_id in player.equip)
    return has_weapon and bool(targets_within_attack_range(state, target))


def _wounded(state: GameState, source: int, target: int) -> bool:
    """未受伤者对【桃园结义】**无效**（`A/card_scroll.txt:171`）。"""
    del source
    player = state.player(target)
    return player.hp < player.hp_max


TARGET_FILTERS: dict[str, "Callable[[GameState, int, int], bool]"] = {
    "has_zone_cards": _has_zone_cards,
    "borrowed_sword_target": _borrowed_sword_target,
}
"""第 0 层附加谓词名 → 回调（``(state, source, target) -> bool``）。"""

EFFECTIVENESS: dict[str, "Callable[[GameState, int, int], bool]"] = {
    "wounded": _wounded,
}
"""第 2 层"无效"谓词名 → 回调；全为真才算**有效**。"""


def _same_named_in_judge(state: GameState, seat: int, card: CardRef) -> bool:
    """``seat`` 的判定区里是否已有与 ``card`` **同名**的延时锦囊（票 03 §3）。"""
    return any(state.card(card_id).type == card.type for card_id in state.player(seat).judge)


def delayed_settle_target(state: GameState, card: CardRef, subject: int) -> int | None:
    """延时锦囊结算后**仍在处理区**时的去向（票 11 §4 对票 10 §3.6 的特化）。

    返回 = 应收下这张牌的**判定区座位**；``None`` = 直接进弃牌堆。

    - **【闪电】**：逆时针顺延到第一个合法目标（存活 ∧ 判定区无同名），下家不合法再
      往下，全不合法则回到**其自己的**判定区（官方 `A/card_scroll.txt:219`）。判定阶段
      循环的 ``judged`` 守卫负责不让刚判完的牌原地重判（票 10 §3 的 U-10-1）。
    - **其余延时锦囊**（【乐不思蜀】）：弃牌堆。

    判据读行为表的 ``passes_to_next_judge``（牌面事实），引擎与规则层都不出现具体牌名。
    """
    if not behavior_for(card).passes_to_next_judge:
        return None
    ring = seats_from(state, subject, len(state.players))
    for target in ring[1:] + ring[:1]:
        if not _same_named_in_judge(state, target, card):
            return target
    return subject


def legal_actions_for(
    state: GameState,
    seat: int,
    kind: QueryKind,
    *,
    answers_seq: int,
    dying: int | None = None,
    payload: RespondContext | SelectContext | None = None,
) -> tuple[Action, ...]:
    """给出 ``seat`` 在 ``kind`` 询问下的全部候选，按票 05 的稳定键排序。

    ``dying`` 只在 ``PEACH`` 询问下需要：濒死语境里【桃】的合法目标是**濒死者**
    而不是应答者自己，故目标不走行为表的目标规则（票 07 §4 / 票 21 §7）。
    票 27 的新 kind 走**类型化 ``payload``**（票 19 §4）。
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
    elif kind is QueryKind.RESPOND:
        if not isinstance(payload, RespondContext):
            raise ValueError("RESPOND 询问必须给出 RespondContext")
        candidates = _respond(state, seat, answers_seq, payload)
    elif kind is QueryKind.SELECT_CARDS:
        if not isinstance(payload, SelectContext):
            raise ValueError("SELECT_CARDS 询问必须给出 SelectContext")
        candidates = _select_cards(state, seat, answers_seq, payload)
    else:
        raise ValueError(f"还没有 {kind.value} 询问的候选生成器")
    return tuple(sorted(candidates, key=_sort_key))


def _play_phase(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    """出牌阶段：逐张开出可用牌 × 每个合法目标组合，最后是结束阶段。"""
    actions: list[Action] = []
    for card in _usable_hand(state, seat, UseContext.PLAY_PHASE):
        if check_count(state, seat, card):
            continue
        rule = behavior_for(card).target_rule
        pool = check_targets(state, seat, card)
        count = rule.target_count(len(pool))
        if count <= 0:
            # 空池的"全体存活"牌（唯一存活者）没有合法候选；M1 的额定牌由
            # ``combinations(pool, 1)`` 自然为空，这里只补上零目标那档。
            continue
        for combo in combinations(pool, count):
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


def _respond(
    state: GameState, seat: int, answers_seq: int, ctx: RespondContext
) -> list[Action]:
    """响应窗口：手牌里满足 ``required`` 的牌各一个候选，放弃恒在最后。

    - ``mode = PLAY``（南蛮 / 万箭 / 决斗）：候选是 ``PlayCard``（打出，无目标、不占次数）；
    - ``mode = USE``（【借刀杀人】迫使的"使用【杀】"）：候选是 ``UseCard``，目标取
      ``ctx.use_targets``（持刀者的合法【杀】目标），并按 L3.2 校验出牌次数。

    手上没有所需牌者候选只有 ``Pass``——**仍发询问**（票 19 §9.2 的"各获一次机会"）。
    """
    actions: list[Action] = []
    for card_id in state.player(seat).hand:
        card = state.card(card_id)
        if ctx.required is not None and card.type != ctx.required:
            continue
        if check_forbidden(state, seat, card):
            continue
        if ctx.mode is RespondMode.USE:
            if check_count(state, seat, card):
                continue
            for target in ctx.use_targets:
                actions.append(
                    UseCard(card=card, targets=(target,), answers_seq=answers_seq)
                )
        else:
            actions.append(PlayCard(card=card, answers_seq=answers_seq))
    actions.append(Pass(answers_seq=answers_seq))
    return actions


def _select_cards(
    state: GameState, seat: int, answers_seq: int, ctx: SelectContext
) -> list[Action]:
    """选牌：按 ``spec.total`` 枚举"取 k 张"的全部组合；``optional`` 时放弃在最后。

    ``spec.zones`` 已由调用方用于铺选择面，``per_zone`` / ``sequential`` 是 M3 的扩展点
    （票 19 §3 的"M3 只许增"）。M2 的三张选牌牌都是"取满 1 张"的强制选择，
    故候选恰是每个槽位一个单张；池为空时调用方**不构造询问**（票 19 §6）。
    """
    del state, seat
    low, high = ctx.spec.total
    actions: list[Action] = [
        SelectCards(picks=picks, answers_seq=answers_seq)
        for count in range(low, min(high, len(ctx.slots)) + 1)
        for picks in combinations(range(len(ctx.slots)), count)
    ]
    if ctx.spec.optional:
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
    """票 05 §4 的稳定键；票 15 §4 把 ``UseCard`` 的**目标列表**补进键里。

    同牌不同目标组合因此有显式次序（不再依赖生成顺序的稳定性）；1v1 每张牌至多一个
    目标组合，故 M1 六条基准逐字节不变。
    """
    if isinstance(action, UseCard):
        return (0, (action.card.card_id, *action.targets), action.kind)
    if isinstance(action, PlayCard):
        return (0, (action.card.card_id,), action.kind)
    if isinstance(action, SelectCards):
        return (0, action.picks, action.kind)
    if isinstance(action, DiscardCards):
        return (0, tuple(card.card_id for card in action.cards), action.kind)
    return (1, (), action.kind)
