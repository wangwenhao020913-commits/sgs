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
from itertools import combinations, permutations
from typing import Any

from sgs.state import (
    Action,
    ActivateSkill,
    ArrangeCards,
    ArrangeContext,
    CardRef,
    DiscardCards,
    DodgeContext,
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
    SelectOption,
    SelectOptionContext,
    SkillActivationContext,
    SlotKind,
    UseCard,
    alive_seats,
    seats_from,
)
from sgs.timing import Timing

from .behaviors import CardBehavior, TargetCount, UseContext, behavior_for
from .modifiers import (
    CandidateScope,
    ModifierContext,
    ModifierPoint,
    ModifierRole,
    apply_modifiers,
)

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
    "forced_use_targets",
    "hand_limit",
    "legal_actions_for",
    "seat_distance",
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
    """距离：**座位距离 + 目标 +1 坐骑数 − 自身 −1 坐骑数**（下限 1、自身 0）。

    官方一手口径（`A/glossary_value.txt:53-57`）：先"数绿色小箭头"得**座位距离**
    （两个方向上**严格位于两者之间的存活角色数**取小者 + 1）；坐骑只改"能不能够到"，
    +1 坐骑（防御马）令别人到你 +1、−1 坐骑（进攻马）令你到别人 −1（`R:745-749`）。
    下限 1 在管道之后夹住，自身恒 0（票 12 §1 的公式，`tests/test_use_and_resolve.py`
    的断言逐条保留）。

    两个方向的合成天然多来源（+1 属客体、−1 属主体、可能分属不同角色），故坐骑走
    :data:`~sgs.skills.ModifierPoint.DISTANCE` 的**链式管道**，主语 / 客体靠
    ``ctx.subject`` / ``ctx.target`` 分列。
    """
    if a == b:
        return 0
    base = seat_distance(state, a, b)
    ctx = ModifierContext(subject=a, target=b, role=ModifierRole.TARGET)
    return max(apply_modifiers(ModifierPoint.DISTANCE, state, ctx, base), 1)


def seat_distance(state: GameState, a: int, b: int) -> int:
    """**座位距离**：两个方向上严格位于两者之间的存活角色数取小 + 1（官方"数绿色小箭头"）。

    读取走**只读座位环真源**（票 23）：在座位环上取两方向的最短步数（只数存活者），
    与"数两方向中间的存活者再 +1"逐值相同（座位环上两方向中间的存活者数 = 步数 − 1）。
    相邻两人中间 0 人 ⇒ 1；死者只让左右"挨近"，不把环当顶点集重新计步（票 12 §1 的更正）。
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
    """额定攻击范围：无武器 = 1，否则取武器牌的 ``attack_range``（票 12 §1 的公式）。

    **不走 ``DISTANCE`` 挂点**（票 12 §Q22）：坐骑改的是座位距离、武器改的是攻击范围，
    两张表各自只有一个写入者。攻击范围是**牌面事实**，实例化时已派生进 ``CardRef``，
    故本函数真读武器牌而不必回头拿 ``ContentPack``。合法性 = ``距离 ≤ 攻击范围``，
    属 L3 非时机（在 :func:`check_targets` 里读）。
    """
    for card_id in state.player(seat).equip:
        card = state.card(card_id)
        if card.slot is SlotKind.WEAPON:
            return card.attack_range if card.attack_range is not None else 1
    return 1


def check_forbidden(state: GameState, seat: int, card: CardRef) -> bool:
    """L3.1 ``USE_CHECK_FORBIDDEN``：``True`` = 禁止使用。

    M2 没有"禁止使用"类效果（装备效果全部落在修饰器 / 钩子 / 接缝三层），故恒 ``False``；
    函数保留（票 07 §4），**M3 技能长在这里**，而不是在候选生成器里加分支（票 12 §Q7）。
    """
    del state, seat, card
    return False


def check_count(state: GameState, seat: int, card: CardRef) -> bool:
    """L3.2 ``USE_CHECK_COUNT``：``True`` = 已超过本回合次数上限。

    额定上限先过 :data:`~sgs.skills.ModifierPoint.COUNT_LIMIT` 链：【诸葛连弩】把
    【杀】的上限改成"无限"（``None``）；``extra`` 是牌面给的额外额度，链后相加。
    """
    behavior = behavior_for(card)
    limit = behavior.use_limit
    rated = apply_modifiers(
        ModifierPoint.COUNT_LIMIT,
        state,
        ModifierContext(subject=seat, card=card),
        limit.rated,
    )
    if rated is None:
        return False
    used = state.player(seat).card_use_counts.get(card.type, 0)
    return used >= rated + limit.extra


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
    # 第 0 层定集合之后过 TARGET_POOL 链（票 07 §3 的挂点）：M2 的【方天画戟】在此不改池
    # （额定与额外目标的合法性检测标准相同，`A/flow_condition.txt:46`），M3 的"标准不同"
    # 类效果从这里分叉。无修饰器时原样返回，故 M1 的候选逐字节不变。
    return tuple(
        apply_modifiers(
            ModifierPoint.TARGET_POOL,
            state,
            ModifierContext(subject=seat, card=card),
            tuple(pool),
        )
    )


def target_is_effective(state: GameState, card: CardRef, source: int, target: int) -> bool:
    """**第 2 层"无效"**（票 15 §5）：被指定者到自己的 C1 才被终止结算。

    ``True`` = 此牌对该目标有效、继续 C2–C5；``False`` = 终止对该目标的结算（故**不开**
    C2 的无懈窗口）。谓词全部内联在 :data:`EFFECTIVENESS` 的回调里（票 07 §4）；
    M2 的生产者有两类：【桃园结义】"对未受伤的角色无效"（牌面谓词）与装备侧的
    :data:`~sgs.skills.ModifierPoint.EFFECTIVENESS` 链（【仁王盾】"黑色【杀】对你无效"）。
    """
    behavior = behavior_for(card)
    if not all(EFFECTIVENESS[name](state, source, target) for name in behavior.effectiveness):
        return False
    return bool(
        apply_modifiers(
            ModifierPoint.EFFECTIVENESS,
            state,
            ModifierContext(
                subject=source,
                target=target,
                role=ModifierRole.TARGET,
                card=card,
            ),
            True,
        )
    )


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


def forced_use_targets(
    state: GameState, seat: int, card: CardRef, allowed: tuple[int, ...]
) -> tuple[int, ...]:
    """**被迫使用**（【借刀杀人】迫使的【杀】）的目标池：以 :func:`check_targets` 为准。

    引擎在发响应窗口时已按攻击范围算出一份预选集 ``allowed``（Bn 烘进候选人；U-27-1），
    但预选集不含 :data:`~sgs.skills.ModifierPoint.TARGET_POOL` 链——【空城】这类"不能
    成为目标"的锁定技必须能把它删掉，否则强制指定目标的效果就成了绕过技能的缺口
    （票 06 §要做什么 2 的**同源要求**）。

    故真源仍是 :func:`check_targets`：``allowed`` 只作**上界**（引擎预选集的语义保留），
    链上删掉的目标一律不出现。真实牌与转化牌共用本函数（``skills/virtual.py`` 的
    ``RESPOND`` + ``USE`` 路径），故两条候选生成路不会各写一份。
    """
    pool = check_targets(state, seat, card)
    if allowed:
        permitted = set(allowed)
        pool = tuple(target for target in pool if target in permitted)
    return pool


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
    """``seat`` 的判定区里是否已有与 ``card`` **同名**的延时锦囊（票 03 §3）。

    判定区装实体牌，但转化延时锦囊（【国色】）在里面代表的是一张**虚拟牌**；牌名按
    :attr:`~sgs.state.GameState.virtual_judge` 里的映射读，否则一张方块牌当的
    【乐不思蜀】会被当成方块牌，同名检查漏掉它。
    """
    return any(
        (state.virtual_judge.get(card_id) or state.card(card_id)).type == card.type
        for card_id in state.player(seat).judge
    )


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
    payload: RespondContext | SelectContext | DodgeContext | SkillActivationContext | SelectOptionContext | None = None,
) -> tuple[Action, ...]:
    """给出 ``seat`` 在 ``kind`` 询问下的全部候选，按票 05 的稳定键排序。

    ``dying`` 只在 ``PEACH`` 询问下需要：濒死语境里【桃】的合法目标是**濒死者**
    而不是应答者自己，故目标不走行为表的目标规则（票 07 §4 / 票 21 §7）。
    票 27 的新 kind 走**类型化 ``payload``**（票 19 §4）；票 30 再补三类。
    """
    if kind is QueryKind.PLAY_PHASE:
        candidates = _play_phase(state, seat, answers_seq)
    elif kind is QueryKind.DISCARD:
        candidates = _discard(state, seat, answers_seq)
    elif kind is QueryKind.DODGE:
        providers_used = payload.providers_used if isinstance(payload, DodgeContext) else ()
        candidates = _dodge(state, seat, answers_seq, providers_used)
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
    elif kind is QueryKind.SKILL_ACTIVATION:
        if not isinstance(payload, SkillActivationContext):
            raise ValueError("SKILL_ACTIVATION 询问必须给出 SkillActivationContext")
        candidates = _skill_activation(seat, answers_seq, payload)
    elif kind is QueryKind.SELECT_OPTION:
        if not isinstance(payload, SelectOptionContext):
            raise ValueError("SELECT_OPTION 询问必须给出 SelectOptionContext")
        candidates = _select_option(seat, answers_seq, payload)
    elif kind is QueryKind.ARRANGE:
        if not isinstance(payload, ArrangeContext):
            raise ValueError("ARRANGE 询问必须给出 ArrangeContext")
        candidates = _arrange(answers_seq, payload)
    else:
        raise ValueError(f"还没有 {kind.value} 询问的候选生成器")
    return tuple(sorted(candidates, key=_sort_key))


def _play_phase(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    """出牌阶段：逐张开出可用牌 × 每个合法目标组合，最后是结束阶段。

    每张牌的额定组合生成后过 :data:`~sgs.skills.ModifierPoint.CANDIDATES` 链，让装备
    技能**追加**候选（【方天画戟】的"额定 1 + 额外 k"组合）；回调只追加、不排序，
    全表仍由 :func:`legal_actions_for` 按票 05 的稳定键统一排序，故"候选与校验同源"
    不被打破。
    """
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
        generated: tuple[Action, ...] = tuple(
            UseCard(card=card, targets=tuple(combo), answers_seq=answers_seq)
            for combo in combinations(pool, count)
        )
        ctx = ModifierContext(
            subject=seat,
            card=card,
            pool=tuple(pool),
            rated_targets=count,
        )
        actions.extend(
            apply_modifiers(ModifierPoint.CANDIDATES, state, ctx, generated)
        )
    # 整段候选期的**通用追加**（票 30）：虚拟牌不绑在某张手牌上（【丈八蛇矛】的
    # 转化【杀】没有实体手牌可 loop），故在逐张牌之外再跑一趟 `ctx.card=None` 的
    # CANDIDATES。排序仍由 `legal_actions_for` 统一执行。
    actions.extend(
        apply_modifiers(
            ModifierPoint.CANDIDATES,
            state,
            ModifierContext(subject=seat, card=None, answers_seq=answers_seq, scope=CandidateScope.PLAY),
            (),
        )
    )
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


def _dodge(
    state: GameState, seat: int, answers_seq: int, providers_used: tuple[str, ...] = ()
) -> list[Action]:
    """【闪】的响应窗口：真牌候选 + "需要牌"提供者序列（票 10 §5 / 票 30）+ 放弃。

    提供者（【八卦阵】）由 ``CANDIDATES`` 挂点追加为 ``ActivateSkill``；已经问过的
    提供者（``providers_used``）被过滤掉——"每个提供者只问一次"。选中才惰性判定。
    放弃恒在最后且**永远可选**（需要【闪】是"可以"，不是"必须"）。
    """
    actions: list[Action] = [
        UseCard(card=card, targets=(), answers_seq=answers_seq)
        for card in _usable_hand(state, seat, UseContext.RESPONSE, Timing.RESOLVE_BEFORE_EFFECT)
    ]
    generated = apply_modifiers(
        ModifierPoint.CANDIDATES,
        state,
        ModifierContext(
            subject=seat,
            answers_seq=answers_seq,
            providers_used=providers_used,
            scope=CandidateScope.DODGE,
        ),
        tuple(actions),
    )
    actions = [
        action
        for action in generated
        if not (isinstance(action, ActivateSkill) and action.skill_id in providers_used)
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
    - ``mode = USE``（【借刀杀人】迫使的"使用【杀】"、无懈窗口）：候选是 ``UseCard``，
      目标走 :func:`forced_use_targets`（**与真牌同一条 ``TARGET_POOL`` 链**），
      **空预选集 = 无目标的响应使用**（无懈的目标是"这次结算 + 目标角色"，由窗口帧承载，
      故行动只带牌），并按 L3.2 校验出牌次数。

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
            pool = forced_use_targets(state, seat, card, ctx.use_targets)
            combos = tuple((target,) for target in pool) if pool else ((),)
            for combo in combos:
                actions.append(UseCard(card=card, targets=combo, answers_seq=answers_seq))
        else:
            actions.append(PlayCard(card=card, answers_seq=answers_seq))
    if ctx.required in ("slash", "dodge"):
        # 【丈八蛇矛】的转化【杀】可**打出**、【八卦阵】可"视为打出【闪】"（票 10 §5.5 /
        # 票 30）：整段候选期追加一趟 CANDIDATES。"每提供者只问一次"由这里的通用过滤兜底。
        scope = CandidateScope.RESPOND if ctx.required == "slash" else CandidateScope.DODGE
        generated = apply_modifiers(
            ModifierPoint.CANDIDATES,
            state,
            ModifierContext(
                subject=seat,
                card=None,
                answers_seq=answers_seq,
                providers_used=ctx.providers_used,
                scope=scope,
                respond_required=ctx.required,
                respond_mode=ctx.mode,
                respond_targets=tuple(ctx.use_targets),
            ),
            tuple(actions),
        )
        actions = [
            action
            for action in generated
            if not (
                isinstance(action, ActivateSkill) and action.skill_id in ctx.providers_used
            )
        ]
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


def _skill_activation(
    seat: int, answers_seq: int, ctx: SkillActivationContext
) -> list[Action]:
    """"是否发动"：一次询问、载荷烘进候选（票 19 §5 / 票 30）。

    ``max_picks == 0`` ⇒ 裸发动（后续强制步自己再问）；否则把"取 k 张面内位置"的全部
    组合烘进 ``ActivateSkill.picks``。**付不起的组合不进候选**（``min_picks`` 大于面大小
    时一个都不造）；``optional`` 决定放弃候选（"可以 / 必须"是效果属性，与 ``locked`` 正交）。
    """
    del seat
    actions: list[Action] = []
    if ctx.max_picks <= 0:
        actions.append(ActivateSkill(skill_id=ctx.skill_id, answers_seq=answers_seq))
    else:
        high = min(ctx.max_picks, len(ctx.slots))
        for count in range(ctx.min_picks, high + 1):
            actions.extend(
                ActivateSkill(skill_id=ctx.skill_id, picks=picks, answers_seq=answers_seq)
                for picks in combinations(range(len(ctx.slots)), count)
            )
    if ctx.optional:
        actions.append(Pass(answers_seq=answers_seq))
    return actions


def _select_option(
    seat: int, answers_seq: int, ctx: SelectOptionContext
) -> list[Action]:
    """选项闭集：每个选项一个候选（强制选择，无 ``Pass``，票 12 §Q6）。"""
    del seat
    return [
        SelectOption(choice=option, answers_seq=answers_seq) for option in ctx.options
    ]


def _arrange(answers_seq: int, ctx: ArrangeContext) -> list[Action]:
    """排列：``X`` 个面内位置的**全部排列** × **全部切分点**（票 10 §②）。

    授予了几张牌就排几张（``ctx.count``）；强制选择，没有 ``Pass``——"答了发动"之后的
    排列是这次发动的载荷，玩家想不排就应当在上一步的"是否发动"里放弃。``X`` 至多 5
    （观星的上限），故候选至多 ``5! × 6 = 720``；这是**询问级**候选，不占票 04 给
    "两维相乘的技能候选"设的 128 闸（那道闸管的是技能组合，不是排列的全枚举）。
    """
    return [
        ArrangeCards(order=order, split=split, answers_seq=answers_seq)
        for split in range(ctx.count + 1)
        for order in permutations(range(ctx.count))
    ]


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


def _sort_key(action: Action) -> tuple[Any, ...]:
    """票 05 §4 的稳定键；票 15 §4 把 ``UseCard`` 的**目标列表**补进键里。

    同牌不同目标组合因此有显式次序（不再依赖生成顺序的稳定性）；1v1 每张牌至多一个
    目标组合，故 M1 六条基准逐字节不变。票 04 再把 ``ActivateSkill.choices`` 补进键
    （键长不同无妨：首元素是构造子类别，不同类别在更早的位置就已分出先后）。
    """
    if isinstance(action, UseCard):
        return (0, (action.card.card_id, *action.targets), action.kind)
    if isinstance(action, PlayCard):
        return (0, (action.card.card_id,), action.kind)
    if isinstance(action, SelectCards):
        return (0, action.picks, action.kind)
    if isinstance(action, ActivateSkill):
        # ``choices`` 也进键（票 04）：主动技同一组牌可以配不同目标座位，不带上它就会
        # 落进"键相等、顺序靠生成顺序"的隐式约定。既有候选两者皆空，故纪律扫描与
        # M2 基准的次序逐字节不变。
        return (0, action.picks, action.kind, action.choices)
    if isinstance(action, DiscardCards):
        return (0, tuple(card.card_id for card in action.cards), action.kind)
    if isinstance(action, ArrangeCards):
        # 排列候选只与排列候选同处一个询问，故键只需在族内定序（票 10）：显式写出
        # ``order`` / ``split``，不靠 `permutations` 的生成顺序当隐式约定。
        return (0, action.order, action.kind, action.split)
    return (1, (), action.kind)
