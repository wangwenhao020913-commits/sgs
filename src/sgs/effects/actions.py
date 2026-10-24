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

from collections.abc import Callable, Iterable, Sequence
from itertools import combinations, permutations
from typing import Any

from sgs.data import CardTagReader, card_tag_spec
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
    Reforge,
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

from .behaviors import (
    CardBehavior,
    DODGE_RESPONSE_CARD,
    TargetCount,
    UseContext,
    behavior_for,
    satisfies_required,
)
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
    "designation_actions_for",
    "discard_count",
    "distance",
    "forced_use_targets",
    "hand_count",
    "hand_limit",
    "legal_actions_for",
    "needs_designation",
    "seat_distance",
    "target_combos",
    "target_is_effective",
    "targets_within_attack_range",
]


def hand_limit(state: GameState, player: Player) -> int:
    """手牌上限的**双轴**查询（M6 票 08；spec D11）。

    两段、各自一个真源（`三国杀详细规则.txt` §四 L334–L336）：

    1. **上限修正链**：``max(当前体力值, 0)`` → ``HAND_LIMIT_REPLACE`` 修饰器链（界周瑜
       【英姿】② 的"手牌上限 = 体力上限"是唯一消费者；查询期从**活着的实例**算，故
       体力上限变化、技能失去 / 失效都立刻反映）；
    2. **加式**：``Player.hand_limit_bonus``（【清俭】跨座位、【集智】自身；D9 的一次性
       效果，带回合作用域、不随来源消失撤回）。

    最后再夹一次下限 0：官方把"体力"（可小于 0）与作为参数读取的"体力值"分开——体力降到
    0 或更低时体力值读作 0，且手牌上限经过修正后最小值为 0。

    **不是**"手牌计数"：计数是另一个查询（:func:`hand_count`——排除计数标签，票 09 的
    标签轴），spec D11 明写"上限与计数是两个查询，互不替换"。
    """
    base = max(player.hp, 0)
    ctx = ModifierContext(subject=player.seat, target=player.seat)
    replaced = apply_modifiers(ModifierPoint.HAND_LIMIT_REPLACE, state, ctx, base)
    return max(int(replaced) + player.hand_limit_bonus, 0)


def hand_count(state: GameState, player: Player) -> int:
    """**手牌计数** = 手牌中**不带"排除计数"标签**的张数（M6 票 09；spec D12 / §标签）。

    标签类型在 ``sgs.data.CARD_TAG_TYPES`` 里声明**读取方**：只有 ``reader == HAND_COUNT``
    的类型参与这里的排除，其余类型（将来出现的别的读取方）不影响计数。判据逐张读
    :meth:`~sgs.state.GameState.tags_of`——标签与区域无关，故这里不做"牌在哪"的判断；
    形状分类（本回合 / 在某区域期间 / 到被使用为止）由各标签类型的**清除触发**负责，
    不在读取方特判（"在某区域期间"的那一类本来也只会在该区域的牌上被计数读到）。

    这个数**只用于**弃牌阶段（:func:`discard_count`）；投影里的公开"手牌数"仍是
    ``len(hand)``（spec §投影与记录流：标签的增删不改变那个公开数字）。
    """
    return sum(
        1
        for card_id in player.hand
        if not any(
            card_tag_spec(tag.tag_type).reader is CardTagReader.HAND_COUNT
            for tag in state.tags_of(card_id)
        )
    )


def discard_count(state: GameState, player: Player) -> int:
    """弃牌阶段须弃置的张数；``<= 0`` 表示无需弃牌（也不发询问）。

    计数 = :func:`hand_count`（排除带"不计入"标签的牌，票 09），上限 =
    :func:`hand_limit`（票 08 的双轴）：两个查询各走各的，不互相替换（spec D11）。
    """
    return hand_count(state, player) - hand_limit(state, player)


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


def attack_range(
    state: GameState, seat: int, *, excluding: tuple[int, ...] = ()
) -> int:
    """额定攻击范围：无武器 = 1，否则取武器牌的 ``attack_range``（票 12 §1 的公式）。

    **不走 ``DISTANCE`` 挂点**（票 12 §Q22）：坐骑改的是座位距离、武器改的是攻击范围，
    两张表各自只有一个写入者。攻击范围是**牌面事实**，实例化时已派生进 ``CardRef``，
    故本函数真读武器牌而不必回头拿 ``ContentPack``。合法性 = ``距离 ≤ 攻击范围``，
    属 L3 非时机（在 :func:`check_targets` 里读）。

    ``excluding``（W4-2【流离】）："**把这几张牌先付掉之后**"的攻击范围。预发动/逐步询问
    都在**成本搬牌之前**选新目标，而【流离】的成本可能正是自己的武器（弃掉之后范围回落）；
    规则读的是付完之后的场况，故把已选成本的 id 传进来、由同一个公式重算，而不是在
    技能层抄一份"没有武器 = 1"。
    """
    skipped = set(excluding)
    for card_id in state.player(seat).equip:
        if card_id in skipped:
            continue
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

    已用次数读**行为表声明的计数键**（``CardBehavior.count_key``）：普通【杀】/ 火【杀】/
    雷【杀】共用 ``"slash"``，转化 / 虚拟【杀】按产物牌型（也是 ``"slash"``）读同一键。
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
    used = state.player(seat).card_use_counts.get(behavior.count_key, 0)
    return used >= rated + limit.extra


def check_targets(state: GameState, seat: int, card: CardRef) -> tuple[int, ...]:
    """L3.3 ``USE_CHECK_TARGETS``：这张牌当前的合法目标池（可为空）。

    目标数为 0 的响应牌（【闪】/【无懈可击】）返回空元组——它们响应的是那张牌 /
    那次结算，不是座位。两个"全体存活"档的候选池由目标数来源枚举决定含不含自己。

    票 27 把**目标池限制**拆成两半，都不给 ``TargetRule`` 加面向具体牌名的布尔：

    - **额定距离上限** ``rule.max_distance``（【顺手牵羊】= 1，与武器无关），过
      :data:`~sgs.skills.ModifierPoint.DISTANCE_LIMIT` 链（M6 票 02 的加法；
      【奇才】把锦囊的这条上限豁免成"不限"，返回 ``None`` 即完全不查距离）；
    - **附加谓词** ``behavior.target_filters``（【过河拆桥】/【顺手牵羊】"区域里有牌"、
      【借刀杀人】"有武器且有合法【杀】目标"），经 :data:`TARGET_FILTERS` 注册表解析。

    延时锦囊另有一条**规则层**限制：判定区按**牌名**禁同名（票 03 §5），故已有同名
    延时锦囊的目标不是合法目标（标准版下这是"不合法"的唯一来源，票 03 §6）。
    """
    behavior = behavior_for(card)
    rule = behavior.target_rule
    if rule.count is TargetCount.RATED and not rule.is_range and rule.rated_targets == 0:
        return ()
    if rule.self_only:
        me = state.player(seat)
        if not me.alive or (rule.requires_wounded_self and me.hp >= me.hp_max):
            return ()
        pool: list[int] = [seat]
    else:
        # 额定距离上限先过 DISTANCE_LIMIT 链（M6 票 02）：``None`` = 这次使用**豁免距离**
        # （【奇才】），否则从 ``rule.max_distance`` 起逐条修正。链在候选生成期求值，
        # 与 TARGET_POOL 同一姿态；无修饰器时原值返回，故既有牌型的池逐字节不变。
        max_distance = rule.max_distance
        if max_distance is not None:
            max_distance = apply_modifiers(
                ModifierPoint.DISTANCE_LIMIT,
                state,
                ModifierContext(subject=seat, card=card),
                max_distance,
            )
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
            if max_distance is not None and distance(
                state, seat, player.seat
            ) > max_distance:
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


def targets_within_attack_range(
    state: GameState, seat: int, *, excluding: tuple[int, ...] = ()
) -> tuple[int, ...]:
    """``seat`` 能触及的座位：一名**其他存活角色**且距离 ≤ 其攻击范围。

    【借刀杀人】的目标合法性（第 0 层）与"持刀者使用【杀】"的候选都读它；
    距离与攻击范围各只在这里读一次，不复制公式，故函数本身与具体牌名无关。

    ``excluding``（W4-2【流离】）：先付掉这几张牌之后的触及范围——见
    :func:`attack_range` 的同名参数。
    """
    return tuple(
        player.seat
        for player in state.players
        if player.alive
        and player.seat != seat
        and distance(state, seat, player.seat)
        <= attack_range(state, seat, excluding=excluding)
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

    **W7-01 起只作 a 可达性 / auto-b 判据**：它把池与 ``allowed=(Bn,)`` 求交，故结果至多
    ``{Bn}``——"够不够得着固定 Bn"正是要问的事；但**不再作枚举池**（那会把方天/未来的
    ``TARGET_COUNT`` 额外目标挡在门外）。持刀者的【杀】候选池是**完整**
    :func:`check_targets`，组合由 :func:`target_combos` 恒含 Bn 地生成。
    """
    pool = check_targets(state, seat, card)
    if allowed:
        permitted = set(allowed)
        pool = tuple(target for target in pool if target in permitted)
    return pool


def target_combos(
    pool: tuple[int, ...],
    low: int,
    high: int,
    *,
    required: tuple[int, ...] = (),
    ordered: bool = False,
) -> tuple[tuple[int, ...], ...]:
    """**目标组合**的唯一构建子（W7-01）：大小 ``low..high``、每组恒含 ``required``。

    与 :func:`check_targets` 同姿态——规则层只在这一处把"一次使用要指定哪些座位"摊成
    组合，M3 的三条读点共用同一份实现，不各写一遍（漏一处就会出现"界面可选 3、引擎只给 1"
    的漂移）：

    - **出牌阶段**（:func:`_play_phase`）：``low = 额定目标数``、``high = TARGET_COUNT``
      链值、``required=()`` ⇒ 方天成立时 1..3；
    - **借刀窗口**（:func:`_respond` 的 USE 趟）：``low = 1``、``high = TARGET_COUNT``
      链值、``required=(Bn,)`` ⇒ 每条组合恒含 Bn，另加 0..2 个其它目标；
    - **转化【杀】**（W7-02 的 ``skills/virtual.py:virtual_card_use``）：同上，直接复用。

    组合内元素的次序 = ``required`` 的次序 + ``pool`` 的次序；``required`` 里含不在
    ``pool`` 里的座位 ⇒ **没有任何组合**（fail-closed：那条组合不合法，不能造出来）。
    ``high`` 超过池大小时多余的部分自然为空（``itertools.combinations`` 的语义），
    调用方不必先夹一遍。

    ``ordered=True``（M5-bugfix）只给声明了
    :attr:`~sgs.effects.CardBehavior.preserve_target_order` 的牌用（当前 =【铁索连环】）：
    每个组合再摊成**全部排列**——"按使用者选择的顺序结算"的牌，(3, 4) 与 (4, 3) 是
    两条不同的合法动作。``required`` 前缀（借刀的 Bn）**不参与**排列，仍恒在首位；
    目标数至多 2（铁索），故排列代价可忽略。缺省 ``False`` = 既有行为逐条不变
    （多目标【杀】仍只有升序）。
    """
    required = tuple(dict.fromkeys(required))  # 去重、保序（Bn 恒在首位）
    if any(seat not in pool for seat in required):
        return ()
    optional = tuple(seat for seat in pool if seat not in required)
    start = max(low, len(required))  # 不能比 required 的张数还小（combinations 不接受负 r）
    combos = tuple(
        (*required, *combo)
        for total in range(start, high + 1)
        for combo in combinations(optional, total - len(required))
    )
    if not ordered:
        return combos
    return tuple(
        (*required, *tail)
        for combo in combos
        for tail in permutations(combo[len(required) :])
    )


def target_sets_match(chosen: Iterable[int], targets: Iterable[int]) -> bool:
    """选择的座位集合是否与候选的 ``targets`` **整组**相同（W7-01b / W7-03 的唯一判据）。

    两处消费者共用这一份比较，免得"确认解析器"与"AI 的只读池校验"各判一套：

    - :func:`sgs.skills.heroes.resolve_conversion_confirmation`（确认解析器）；
    - :func:`sgs.agents.heuristic._conversion_choices_within_pool`（AI 提交前的池成员校验）。

    先比**原始选择个数**——重复的座位因此被拒（否则 ``{Bn, x, x}`` 会按集合相等混进
    ``(Bn, x)``）；再比去重后的集合，**与点击顺序无关**。清单只表达"最多可选几个"，
    "这组目标到底存不存在"仍以候选池为唯一真源（本函数只做那一步比较，不碰规则）。
    """
    chosen = tuple(int(choice) for choice in chosen)
    actual = tuple(int(target) for target in targets)
    deduped = tuple(dict.fromkeys(chosen))
    if len(chosen) != len(deduped):
        return False
    return len(deduped) == len(actual) and set(deduped) == set(actual)


def needs_designation(card: CardRef) -> bool:
    """这张牌的使用是否**必须带附带指定**（借刀的 Bn 由使用者一并指定，W5-02b）。

    判据 = 行为表上的第 0 层目标滤子 :data:`TARGET_FILTERS` 里有没有
    ``borrowed_sword_target``——与 :func:`designation_actions_for` **同一个判据**，
    故"哪些牌只经清单"与"哪些牌有附带指定池"不会各说各话。规则层不认具体牌名。

    用途只有一处：出牌阶段的裸候选生成（:func:`_play_phase`）跳过这类牌——它们的
    合法动作是三元组 ``(牌, A, Bn)``，单靠"牌 + 目标"表达不了，入口恒是清单。
    """
    return "borrowed_sword_target" in behavior_for(card).target_filters


def designation_actions_for(
    state: GameState, seat: int, answers_seq: int
) -> tuple[UseCard, ...]:
    """本出牌阶段的**借刀附带指定池**（W5-02a；``exclude``，不进记录流）。

    对**每张**手牌里的借刀 × 每个第 0 层合法的持刀者 A × A 攻击范围内的每个 Bn，各造一条
    ``UseCard(card, targets=(A,), designations=(Bn,))``。这是"使用者一并指定 Bn"的候选面：
    确认动作只要求它是本池的成员（``Engine.submit`` 的第四条路），主候选面
    ``legal_actions`` 里的裸借刀由 W5-02b 收回（可用性只剩清单）。

    两条合法性都读**既有谓词**，不另写第二份规则：

    - **A** 走 :func:`check_targets`——借刀的 ``borrowed_sword_target`` 滤子已内含
      "装备区有武器 ∧ 攻击范围内有合法【杀】目标"；
    - **Bn** 走 :func:`targets_within_attack_range`——与旧路径
      :meth:`~sgs.engine.game.Engine.attack_range_targets` 传给持刀者窗口的预选集
      是同一个函数，故"池里的 Bn"与"窗口允许的 Bn"同源。

    借刀本身仍按出牌阶段可用性 / 次数闸门取（:func:`_usable_hand` + :func:`check_count`），
    与 :func:`needs_designation` 同一判据认出"哪张牌要附带指定"。
    """
    actions: list[UseCard] = []
    for card in _usable_hand(state, seat, UseContext.PLAY_PHASE):
        if not needs_designation(card):
            continue
        if check_count(state, seat, card):
            continue
        for holder in check_targets(state, seat, card):
            for designate in targets_within_attack_range(state, holder):
                actions.append(
                    UseCard(
                        card=card,
                        targets=(holder,),
                        designations=(designate,),
                        answers_seq=answers_seq,
                    )
                )
    return tuple(actions)


def _has_zone_cards(state: GameState, source: int, target: int) -> bool:
    """目标的手牌 / 装备区 / 判定区里至少有一张牌（拆、顺的"区域里有牌"）。"""
    del source
    player = state.player(target)
    return bool(player.hand or player.equip or player.judge)


def _has_hand_cards(state: GameState, source: int, target: int) -> bool:
    """目标**手里有牌**（M5-06 的【火攻】"目标须有手牌才能被指定"）。

    只读手牌：装备区 / 判定区有牌不算——火攻要亮的是手牌（spec §5.1.4 / D10）。
    """
    del source
    return bool(state.player(target).hand)


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
    "has_hand_cards": _has_hand_cards,
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
    if state.player(seat).hand_card_prohibited:
        # 【义绝】黑色分支（M6 票 04）：该座位本回合**不能使用或打出手牌**。判据放在这里
        # 而不是各窗口里：``legal_actions_for`` 是全部询问候选的**唯一生成口**，故
        # 出牌 / 【闪】 / 求桃 / 响应四类窗口一次收口，不会漏掉任何一个。只删"使用 / 打出
        # 手牌"的动作：弃置、重铸、技能发动都不是"使用或打出"，照常保留。
        candidates = [
            action
            for action in candidates
            if not _uses_hand_card(state, seat, action)
        ]
    return tuple(sorted(candidates, key=_sort_key))


def _uses_hand_card(state: GameState, seat: int, action: Action) -> bool:
    """这个动作是不是"**使用或打出手牌**"（`Player.hand_card_prohibited` 的唯一判据）。

    - 实体牌：牌本身此刻在手牌区；
    - 转化 / 虚拟牌：只要**任一来源牌**在该座位手牌区，就算"打出手牌"（单源转化是自然
      形态；【丈八蛇矛】两张手牌同理）。纯虚拟牌（无实体来源，如【八卦阵】的视为【闪】）
      不是手牌 ⇒ 不受禁令影响。
    """
    if not isinstance(action, (UseCard, PlayCard)):
        # 重铸 / 弃置 / 技能发动都**不是**"使用或打出"：``Reforge`` 也带 ``card`` 字段，
        # 故这里必须按构造子收口，不能按"有没有 card"判。
        return False
    card = action.card
    source_ids = card.virtual_sources if card.is_virtual else (card.card_id,)
    hand = state.player(seat).hand
    return any(source_id in hand for source_id in source_ids)


def _play_phase(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    """出牌阶段：逐张开出可用牌 × 每个合法目标组合，最后是结束阶段。

    每张牌的目标组合由 :func:`target_combos` 一次摊开：``low = 额定目标数``、
    ``high = 该牌的最大目标数``。最大数只在 ``TargetCount.RATED`` 档读
    :data:`~sgs.skills.ModifierPoint.TARGET_COUNT` 链（W7-01：方天画戟在"最后一张手牌"
    时把 1 放宽到 3）；``ALL_ALIVE*`` 档的目标数由池决定，**不查链**。额定组合生成后仍过
    :data:`~sgs.skills.ModifierPoint.CANDIDATES` 链（【丈八蛇矛】的转化【杀】等**整段
    候选期的追加**）；回调只追加、不排序，全表仍由 :func:`legal_actions_for` 按票 05 的
    稳定键统一排序，故"候选与校验同源"不被打破。

    **点击顺序**（M5-bugfix）：声明了
    :attr:`~sgs.effects.CardBehavior.preserve_target_order` 的牌（【铁索连环】）把每组
    目标再摊成全部排列（``target_combos(..., ordered=True)``），两种点击顺序都是合法动作；
    其余牌保持引擎排好的逆时针升序。

    **附带指定的牌**（借刀杀人，W5-02b）不在这里出裸候选：它的合法动作是"牌 + 持刀者 A
    + Bn"三元组，只有 :func:`designation_actions_for` 那份内部池能表达。可用性因此只剩
    询问上的 ``card_preactivations`` 清单——**清单渲不出来 = 此刻不可用**（不 fail-open，
    声明错误应当可见）。
    """
    actions: list[Action] = []
    for card in _usable_hand(state, seat, UseContext.PLAY_PHASE):
        if needs_designation(card):
            continue
        if check_count(state, seat, card):
            continue
        behavior = behavior_for(card)
        rule = behavior.target_rule
        pool = check_targets(state, seat, card)
        low, high = rule.target_range(len(pool))
        if high <= 0:
            # 空池的"全体存活"牌（唯一存活者）没有合法候选；M1 的额定牌由
            # ``target_combos`` 自然为空，这里只补上零目标那档。
            continue
        if rule.count is TargetCount.RATED and not rule.is_range:
            high = apply_modifiers(
                ModifierPoint.TARGET_COUNT,
                state,
                ModifierContext(subject=seat, card=card),
                high,
            )
        generated: tuple[Action, ...] = tuple(
            UseCard(card=card, targets=combo, answers_seq=answers_seq)
            for combo in target_combos(
                pool,
                low,
                high,
                # M5-bugfix：声明了 ``preserve_target_order`` 的牌（【铁索连环】）按
                # **点击顺序**结算，故两种顺序都是合法动作；其余牌仍只给引擎排好的升序。
                ordered=behavior.preserve_target_order,
            )
        )
        ctx = ModifierContext(subject=seat, card=card)
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
    # 重铸（M5-03）：可重铸的牌各给一条**独立构造子**——它不是使用，故不过可用性 /
    # 次数 / 目标 / 无懈任何一道使用闸门（规格 §5.1.3：无次数限制、不算使用锦囊）。
    actions.extend(
        Reforge(card=card, answers_seq=answers_seq)
        for card in state.refs(state.player(seat).hand)
        if behavior_for(card).reforgeable
    )
    actions.append(EndPhase(answers_seq=answers_seq))
    return actions


def _discard(state: GameState, seat: int, answers_seq: int) -> list[Action]:
    """弃牌阶段：任意"手牌计数 − 手牌上限"张子集，全枚举（票 07 §4 / 票 09）。"""
    player = state.player(seat)
    count = discard_count(state, player)
    if count <= 0:
        return []
    return [
        DiscardCards(cards=state.refs(list(combo)), answers_seq=answers_seq)
        for combo in combinations(player.hand, count)
    ]


def _dodge(
    state: GameState, seat: int, answers_seq: int, providers_used: tuple[str, ...] = ()
) -> list[Action]:
    """【闪】的响应窗口：真【闪】候选 + "需要牌"提供者序列（票 10 §5 / 票 30）+ 放弃。

    **候选按 ``required`` 牌名收口**（B1）：只有**能当【闪】**的牌进来——真【闪】与
    转化【闪】（虚拟牌的类型就是 ``"dodge"``，与真牌同一条判据）。这里不能用
    ``_usable_hand`` 的"语境 + 时机"：那两样【无懈可击】同样命中，于是无懈被请进
    【闪】候选、再由窗口当成【闪】抵消掉【杀】（B1 的根因之一）。

    提供者（【八卦阵】）由 ``CANDIDATES`` 挂点追加为 ``ActivateSkill``；已经问过的
    提供者（``providers_used``）被过滤掉——"每个提供者只问一次"。选中才惰性判定。
    放弃恒在最后且**永远可选**（需要【闪】是"可以"，不是"必须"）。
    """
    actions: list[Action] = [
        UseCard(card=card, targets=(), answers_seq=answers_seq)
        for card in _required_hand(state, seat, DODGE_RESPONSE_CARD)
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

    两处**语境专属**的过滤（M5-04）：

    - ``TargetRule.near_death_self_only`` 的牌（【酒】）只在**濒死者自己**被问时进候选；
      【桃】不声明它，故"他人对濒死者出【桃】"不受影响。——单靠 ``self_only`` 会误伤
      【桃】（它的 ``self_only`` 只描述出牌阶段的目标池）。
    - 次数上限照走 ``check_count``：本回合已用过【酒】（无论出牌阶段加伤还是上一次濒死
      自救）⇒ 这次求桃不再给【酒】候选，出牌阶段与濒死自救因此共用**同一个**计数键。

    **转化源路径**（W4-3）：除了真牌，**能把牌当【桃】的转化技**（当前只有华佗【急救】）
    也在这里产出候选——红牌（手牌 ∪ 装备）当【桃】，目标仍是濒死者。候选集合与
    ``GLOSSARY.md``「预发动清单」里的那一步**同源**：来源由
    :func:`~sgs.skills.conversion_offers` 逐一筛出（区域默认 / 颜色 / 时机谓词都在那份
    声明表里），本函数不另写谓词；漏掉这条路径，急救就永远进不了任何候选
    （``scopes=(PLAY,)`` 与"限回合外"互斥）。
    """
    actions: list[Action] = [
        UseCard(card=card, targets=(dying,), answers_seq=answers_seq)
        for card in _usable_hand(state, seat, UseContext.NEAR_DEATH)
        if (seat == dying or not behavior_for(card).target_rule.near_death_self_only)
        and not check_count(state, seat, card)
    ]
    actions.extend(_transformed_peaches(state, seat, dying, answers_seq))
    actions.append(Pass(answers_seq=answers_seq))
    return actions


def _transformed_peaches(
    state: GameState, seat: int, dying: int, answers_seq: int
) -> list[Action]:
    """求桃窗口的**转化源候选**：把每张能当【桃】的来源牌变成一个无目标争议的 ``UseCard``。

    惰性 import：``sgs.skills`` 反向依赖本模块（``skills/virtual.py`` 读 ``sgs.effects``
    的 ``check_targets`` 等），模块级 import 会成环。目标由窗口固定成 ``dying``——
    这一点与清单的"无目标步"是同一件事（`spec.md` §2 的急救行）。
    """
    from sgs.skills import conversion_offers, transformed_card

    return [
        UseCard(
            card=transformed_card(state, (source,), "peach"),
            targets=(dying,),
            answers_seq=answers_seq,
        )
        for source, _skill_id in conversion_offers(state, seat, "peach")
    ]


def _respond(
    state: GameState, seat: int, answers_seq: int, ctx: RespondContext
) -> list[Action]:
    """响应窗口：手牌里满足 ``required`` 的牌各一个候选，放弃恒在最后。

    取牌走 :func:`_required_hand`——**与【闪】窗口同一道牌名闸门**（B1）。``required``
    是牌名（``slash`` / ``dodge`` / ``nullify``），不是询问种类：同一个函数服务南蛮 /
    万箭 / 决斗 / 借刀 / 无懈五种语境，按 ``QueryKind`` 分支就会漏掉其中一类。

    - ``mode = PLAY``（南蛮 / 万箭 / 决斗）：候选是 ``PlayCard``（打出，无目标、不占次数）；
    - ``mode = USE``（【借刀杀人】迫使的"使用【杀】"、无懈窗口）：候选是 ``UseCard``，
      并按 L3.2 校验出牌次数。**窗口固定了目标**（借刀的 Bn）时先问
      :func:`forced_use_targets`"够不够得着 Bn"（W5-02b 的 a 可行性 / auto-b 语义），
      再拿**完整** :func:`check_targets` 池 + :func:`target_combos` 造
      ``(Bn,) + 0..2`` 个其它目标的组合（W7-01：每条恒含 Bn；``TARGET_COUNT`` 链在这一趟
      里读，方天成立时最大数 1 → 3）；**空预选集 = 无目标的响应使用**（无懈的目标是
      "这次结算 + 目标角色"，由窗口帧承载，故行动只带牌）。预选集非空而这张牌够不着它
      ⇒ 这张牌没有候选。

    手上没有所需牌者候选只有 ``Pass``——**仍发询问**（票 19 §9.2 的"各获一次机会"）。
    """
    actions: list[Action] = []
    for card in _required_hand(state, seat, ctx.required):
        if ctx.mode is RespondMode.USE:
            if check_count(state, seat, card):
                continue
            if ctx.use_targets:
                # a 可达性（W5-02b）：forced_use_targets 只作"够不够得着固定 Bn"的判据，
                # **不再作枚举池**（W7-01）——否则池至多 {Bn}，额外目标永远进不来。
                if not forced_use_targets(state, seat, card, ctx.use_targets):
                    continue
                # 固定目标是**唯一**的 Bn（`ctx.use_targets[0]`，W5-02b 的附带指定）：
                # 预选集里可能还有别的座位（M2 的合成姿态），它们不是本次的目标。
                bn = ctx.use_targets[0]
                pool = check_targets(state, seat, card)
                if bn not in pool:
                    # **不是**上面那条的重复（D2）：forced_use_targets 只证明预选集里
                    # **有**一个座位够得着；多座位载荷下它可能不是 Bn。Bn 自己仍须在池里：
                    # 在结算时已不合法（TARGET_POOL 链删掉了它）⇒ 这张牌没有候选；
                    # 不退回"无目标使用"：那是无懈一类的形状，等于凭空造一条打向空气的【杀】。
                    continue
                count = 1  # 被迫使用窗口的 base = 1（固定 Bn），不是池大小
                high = count
                if behavior_for(card).target_rule.count is TargetCount.RATED:
                    high = apply_modifiers(
                        ModifierPoint.TARGET_COUNT,
                        state,
                        ModifierContext(
                            subject=seat,
                            card=card,
                            respond_required=ctx.required,
                            respond_mode=ctx.mode,
                            respond_targets=(bn,),
                        ),
                        count,
                    )
                combos = target_combos(pool, count, high, required=(bn,))
            else:
                pool = check_targets(state, seat, card)
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
    """"是否发动"：一次询问、候选恒一条**裸** ``ActivateSkill``（票 19 §5 / 票 30；W6-06a）。

    成本 / 载荷由发动后的 ``SkillFlow`` 步骤询问，故这里不再按 ``min_picks`` /
    ``max_picks`` 把"取 k 张面内位置"的组合烘进 ``picks``（那条两维路径随
    ``ActivationPlan`` 的载荷字段一起删除，见 W6-06a）。``optional`` 决定放弃候选
    （"可以 / 必须"是效果属性，与 ``locked`` 正交）。
    """
    del seat
    actions: list[Action] = [
        ActivateSkill(skill_id=ctx.skill_id, answers_seq=answers_seq)
    ]
    if ctx.optional:
        actions.append(Pass(answers_seq=answers_seq))
    return actions


def _select_option(
    seat: int, answers_seq: int, ctx: SelectOptionContext
) -> list[Action]:
    """选项闭集：每个选项一个候选（缺省**强制选择**，没有 ``Pass``，票 12 §Q6）。

    ``ctx.optional``（票 00 / N4）才多一条 ``Pass``——它是"取消这一步"的合法应答，
    供发动后的技能流水线用；缺省 ``False``，故既有消费者候选一字不变。
    """
    del seat
    actions: list[Action] = [
        SelectOption(choice=option, answers_seq=answers_seq) for option in ctx.options
    ]
    if ctx.optional:
        actions.append(Pass(answers_seq=answers_seq))
    return actions


def _arrange(answers_seq: int, ctx: ArrangeContext) -> list[Action]:
    """排列：``X`` 个面内位置的**全部排列** × **切分点**（票 10 §②；W6-02 收口 fixed）。

    授予了几张牌就排几张（``ctx.count``）；强制选择，没有 ``Pass``——"答了发动"之后的
    排列是这次发动的载荷，玩家想不排就应当在上一步的"是否发动"里放弃。``X`` 至多 5
    （观星的上限），故候选至多 ``5! × 6 = 720``；这是**询问级**候选，不占票 04 给
    "两维相乘的技能候选"设的 128 闸（那道闸管的是技能组合，不是排列的全枚举）。

    ``ctx.split``（W6-02，spec S3）：``None`` = **自由切分**，切分点 ``0..count`` 全部进
    候选（观星与票 10 逐条相同）；整数 = **固定切分**，只给这一个置顶张数。固定值越界
    （``split > count``）是声明 / 实际张数对不上的编程错误 ⇒ fail-closed，不静默截断。
    """
    if ctx.split is None:
        splits: tuple[int, ...] = tuple(range(ctx.count + 1))
    else:
        if not 0 <= ctx.split <= ctx.count:
            raise ValueError(
                f"固定切分点 {ctx.split} 不在 0..{ctx.count} 内（DeckTopArrange 的声明与"
                "实际牌数不一致）"
            )
        splits = (ctx.split,)
    return [
        ArrangeCards(order=order, split=split, answers_seq=answers_seq)
        for split in splits
        for order in permutations(range(ctx.count))
    ]


def _required_hand(
    state: GameState, seat: int, required: str | None
) -> list[CardRef]:
    """手牌里**能当** ``required`` 牌名的牌（按手牌序）；``None`` = 不过滤牌名。

    **这是所有"需要某张牌"的响应窗口共用的候选闸门**（B1）：``_dodge``（【闪】）与
    ``_respond``（打出【杀】/ 打出【闪】/ 借刀的使用【杀】/【无懈可击】）都从这里取牌。

    判据是**牌名**而不是 :class:`~sgs.state.QueryKind`：【闪】窗口是独立的 ``DODGE``，
    其余五类挤在 ``RESPOND`` 里；按询问种类分支会让 DODGE 绕过闸门（B1 的根因）。
    真牌与转化牌也不必区分——:func:`~sgs.skills.transformed_card` 把虚拟牌的类型写成
    目标牌名，故【龙胆】的转化【闪】与真【闪】在这里同形。

    **牌名判据只有一处**：:func:`~sgs.effects.satisfies_required`（M5-05b 起【杀】族按
    共享计数键放宽——火【杀】/ 雷【杀】因此出现在南蛮 / 决斗 / 借刀的响应窗口里；其余
    ``required`` 仍逐字相等）。

    ``required = None`` 是改判窗口（``WindowCause.REJUDGE``）：打出一张**任意**手牌。
    """
    cards: list[CardRef] = []
    for card_id in state.player(seat).hand:
        card = state.card(card_id)
        if required is not None and not satisfies_required(card, required):
            continue
        if check_forbidden(state, seat, card):
            continue
        cards.append(card)
    return cards


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
    if isinstance(action, Reforge):
        # 重铸带牌实例但没有目标：与其它带牌的构造子同档（先按牌 id），仍排在
        # ``EndPhase``（兜底键）之前——"结束阶段排最后"的既有口径不破（M5-03）。
        return (0, (action.card.card_id,), action.kind)
    if isinstance(action, SelectCards):
        return (0, action.picks, action.kind)
    if isinstance(action, SelectOption):
        # 选项闭集里的**真候选**。``Pass``（``optional=True`` 时的"放弃"）没有分支、
        # 走兜底键 ``(1, …)`` ⇒ 恒排在真候选之后。**票 03 的活锁**就出在这里：
        # 两者原先共用兜底键，而 ``"Pass" < "SelectOption"`` 让"放弃"排到首位，
        # 取首个候选的贪心参与者（``tests/test_web.py::_play_to_end``）便一直取消。
        # 键相等时保持 ``ctx.options`` 给的次序——那是调用方**显式给出**的元组，
        # 不是生成顺序的隐式约定（且 ``seat:10`` 这类文本不宜按字面排序）。
        return (0, (), action.kind)
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
