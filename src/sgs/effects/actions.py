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
    Distribute,
    DistributeContext,
    DodgeContext,
    EndPhase,
    GameState,
    Pass,
    PeachContext,
    Phase,
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
    SelectOptions,
    SelectOptionsContext,
    SelectOptionsQuery,
    SkillActivationContext,
    SubstituteJudgeCard,
    SlotKind,
    SEALED_ZONE_JUDGE,
    UseCard,
    Zone,
    ZoneRef,
    alive_seats,
    seats_from,
    zone_sealed,
)
from sgs.timing import Timing

from .behaviors import (
    SLASH_FAMILY_KEY,
    CardBehavior,
    DODGE_RESPONSE_CARD,
    NULLIFY_RESPONSE_CARD,
    PEACH_RESPONSE_CARD,
    TargetCount,
    UseContext,
    behavior_for,
    is_slash,
    satisfies_required,
)
from .modifiers import (
    CandidateScope,
    ModifierContext,
    ModifierPoint,
    ModifierRole,
    PriorityClass,
    apply_modifiers,
)

__all__ = [
    "EFFECTIVENESS",
    "NEED_CARD_FLOOR_KEY",
    "TARGET_FILTERS",
    "activation_allowed",
    "attack_range",
    "check_count",
    "check_forbidden",
    "forbidden_as_slash",
    "check_targets",
    "check_usable",
    "delayed_settle_target",
    "designation_actions_for",
    "discard_count",
    "distance",
    "forced_use_targets",
    "hand_count",
    "hand_limit",
    "is_own_play_phase",
    "legal_actions_for",
    "need_card_floor",
    "need_card_tier",
    "needs_designation",
    "next_need_card_tier",
    "record_need_card_choice",
    "record_need_card_floor",
    "reset_need_card_event",
    "reset_need_card_floor",
    "seat_distance",
    "target_combos",
    "target_is_effective",
    "targets_within_attack_range",
    "tier_not_downgraded",
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
    """L3.1 ``USE_CHECK_FORBIDDEN``：``True`` = 禁止使用 / 打出这张牌（按**原用途**）。

    走 :data:`~sgs.effects.ModifierPoint.FORBIDDEN` 修饰器链：``current`` 从 ``False``
    起，链上空无一物时原样返回 ``False``（既有技能零变化）；神关羽【武神】①在链上把
    "红桃手牌"写成 ``True``（票 05）。只覆盖"使用 / 打出"——弃置、重铸、技能成本、
    他人置入装备区都不经过这里。
    """
    return bool(
        apply_modifiers(
            ModifierPoint.FORBIDDEN,
            state,
            ModifierContext(subject=seat, card=card),
            False,
        )
    )


def forbidden_as_slash(state: GameState, card: CardRef) -> bool:
    """这张牌是不是带「不能当作【杀】使用 / 打出」的标签（feng-yujin 票 03，【定乱】）。

    读取方 = :attr:`~sgs.data.CardTagReader.FORBIDDEN_AS_SLASH`（登记表声明，读点不认技能名）。
    与 :func:`check_forbidden` **正交**：那条只管"原用途"（武神），这条管"能不能占据【杀】
    这个牌名"——包括它本来就是【杀】的那一档（读点见 :func:`_usable_hand`）与转化（见
    ``virtual_candidates`` / ``conversion_offers`` / ``provide_face`` 的同一判据）。

    只读标签、不读座位：标签挂在牌上，与牌在谁手里无关。无标签时零成本。
    """
    return any(
        card_tag_spec(tag.tag_type).reader is CardTagReader.FORBIDDEN_AS_SLASH
        for tag in state.tags_of(card.card_id)
    )


def is_own_play_phase(state: GameState, seat: int) -> bool:
    """``seat`` 此刻是否在**自己的**出牌阶段（``turn_seat == seat`` ∧ ``phase is Phase.PLAY``）。

    这是一条**事实判据**，有两个读点、各服务一条规则：

    - :func:`check_count` 的【杀】作用域（W5-04 用户裁定）：次数限制只在这里生效；
    - ``Engine._note_play_phase_slash`` 的"本阶段使用 / 打出过【杀】"事实（M6 票 03
      的【克己】）。

    两处读同一个谓词，免得"自己的出牌阶段"被各写一份、改一处忘一处。
    """
    return state.turn_seat == seat and state.phase is Phase.PLAY


def check_count(
    state: GameState,
    seat: int,
    card: CardRef,
    *,
    targets: tuple[int, ...] | None = None,
) -> bool:
    """L3.2 ``USE_CHECK_COUNT``：``True`` = 已超过本回合次数上限。

    额定上限先过 :data:`~sgs.skills.ModifierPoint.COUNT_LIMIT` 链：【诸葛连弩】把
    【杀】的上限改成"无限"（``None``）；``extra`` 是牌面给的额外额度，链后相加。

    **目标维度**（lei-batch 票 04；02b 读法 A）：``targets`` 是**这一次使用**已经定下的
    目标集合——给了它，就再问一次 :data:`~sgs.skills.ModifierPoint.COUNT_EXEMPT`
    （雄乱「你对其使用牌无次数限制」按目标判：目标集合 ⊆ {其} 才豁免）。``None``（缺省）
    与 ``()`` **不可折叠**：``None`` = **没有目标维度**（问的是"这张牌还准不准用"，
    按目标豁免的回调不参与 ⇒ 整张牌照常超额，候选生成器 ``_play_phase`` 据此把目标池
    收窄到被豁免的角色）；``()`` = **有目标维度但这一次使用的目标集合为空** ⇒ 照常派发
    ``COUNT_EXEMPT``（由回调按自己的牌面口径判）。这个区别经
    :attr:`~sgs.effects.ModifierContext.use_targets` 原样传给回调。既有消费者
    （咆哮 / 连弩 / 诈降 / 天义）都不传目标，故行为与加本参数前逐字相同。

    已用次数读**行为表声明的计数键**（``CardBehavior.count_key``）：普通【杀】/ 火【杀】/
    雷【杀】共用 ``"slash"``，转化 / 虚拟【杀】按产物牌型（也是 ``"slash"``）读同一键。

    **【杀】族的适用范围（W5-04 用户裁定 2026-10-30）**：§17.10 A2（L1836）写"只有『于
    出牌阶段的空闲时间点使用【杀】』才受次数限制"（§3.5 L183–L185 / §17.1 L1659 只是未加
    限定的默认额度），故次数限制只约束**他自己出牌阶段**的主动使用；响应、其他阶段、
    其他角色回合里的使用**不查次数**（计数键照常记账，只是不再当限制读）。即【杀】族只在
    ``turn_seat == seat`` ∧ ``phase is Phase.PLAY``（:func:`is_own_play_phase`）时读计数。
    借刀窗口发生在**另一个角色**的回合，那里读到的 ``"slash"`` 是持刀者**上一回合**的陈旧
    值——读它会把"a 可行"误判成 auto-b（W5-04 的实机根因）。

    **不**在回合起点清空其他座位的计数：陈旧值只是不再被错误的窗口读到，该座位自己的
    ``TURN_START`` 重置保持原样（清算别人的计数会掩盖"限制有适用范围"这条规则边界）。
    其他牌（【酒】等）的计数语义不动。
    """
    behavior = behavior_for(card)
    if is_slash(behavior) and not is_own_play_phase(state, seat):
        return False
    limit = behavior.use_limit
    ctx = ModifierContext(subject=seat, card=card, use_targets=targets)
    rated = apply_modifiers(ModifierPoint.COUNT_LIMIT, state, ctx, limit.rated)
    if rated is None:
        return False
    if targets is not None and apply_modifiers(
        ModifierPoint.COUNT_EXEMPT, state, ctx, False
    ):
        return False
    used = state.player(seat).card_use_counts.get(behavior.count_key, 0)
    return used >= rated + limit.extra


def _count_exempt_targets(
    state: GameState, seat: int, card: CardRef, pool: tuple[int, ...]
) -> tuple[int, ...]:
    """次数额度用尽时的**目标池**：只留"以它为目标的那次使用仍被次数豁免"的座位。

    ``COUNT_EXEMPT`` 按**目标集合**求值（雄乱「你对其使用牌无次数限制」）。候选生成器在
    ``check_count`` 报超额之后用它收窄池，再照常摊组合：多目标牌因此只会生成"目标集合 ⊆
    被豁免角色"的那些组合（``target_combos`` 只从收窄后的池里取），含别人的组合根本不在
    候选里。没有任何被豁免的目标 ⇒ 空元组（调用方照旧判"这张牌没有候选"）。

    这是**纯收窄**：没有按目标豁免的修饰器时，每个座位都照常超额 ⇒ 空池 ⇒ 候选与加本
    机制前逐字相同（金录像因此不变）。
    """
    return tuple(
        target
        for target in pool
        if not check_count(state, seat, card, targets=(target,))
    )


def check_targets(
    state: GameState, seat: int, card: CardRef, *, distance_exempt: bool = False
) -> tuple[int, ...]:
    """L3.3 ``USE_CHECK_TARGETS``：这张牌当前的合法目标池（可为空）。

    目标数为 0 的响应牌（【闪】/【无懈可击】）返回空元组——它们响应的是那张牌 /
    那次结算，不是座位。两个"全体存活"档的候选池由目标数来源枚举决定含不含自己。

    票 27 把**目标池限制**拆成两半，都不给 ``TargetRule`` 加面向具体牌名的布尔：

    - **额定距离上限** ``rule.max_distance``（【顺手牵羊】= 1，与武器无关），过
      :data:`~sgs.skills.ModifierPoint.DISTANCE_LIMIT` 链（M6 票 02 的加法；
      【奇才】把锦囊的这条上限豁免成"不限"，返回 ``None`` 即完全不查距离）；
    - **附加谓词** ``behavior.target_filters``（【过河拆桥】/【顺手牵羊】"区域里有牌"、
      【借刀杀人】"有武器且有合法【杀】目标"），经 :data:`TARGET_FILTERS` 注册表解析。

    票 03 把这五层排成**单向的合法性阶梯**，每一层只负责自己那件事：

    ① ``TargetRule``（只能对自己 / 目标数 0）→ ② **距离与攻击范围**（本层读
    :data:`~sgs.skills.ModifierPoint.TARGET_RANGE_EXEMPT` 的"豁免座位集"）→ ③ 延时锦囊 /
    封印规则 → ④ :data:`TARGET_FILTERS` 附加谓词 → ⑤
    :data:`~sgs.skills.ModifierPoint.TARGET_POOL` 合法性增删链。

    **距离豁免挂点在 ②、且在 ③④⑤ 之前**（票 03 的核心）：被豁免的座位只是**跳过**距离
    两条判据，随后照常走 ③④⑤。故"放宽距离"**不能**复活任何被后面几层判非法的座位——旧
    写法（在 ⑤ 里并集"全体其他存活座位"）会复活它们，实测【雄乱】能把无武器的座位变成
    【借刀杀人】的目标、把无牌角色变成【过河拆桥】的目标、把判定区已封印 / 【空城】的座位
    重新加回池。

    ``distance_exempt``（关键字、缺省 ``False``）= **调用方**声明"这一次使用无视距离 /
    攻击范围"，用于**技能侧自算池**的载体（唯一读者：【神速】视为使用一张"无距离限制的"
    【杀】——那张纯虚拟牌不经过本函数前面的候选生成，故豁免只能由调用方给）。它**不是**
    第二条豁免路径：它只把"全体座位"喂进 ② 的**同一个**豁免集（与
    :data:`~sgs.skills.ModifierPoint.TARGET_RANGE_EXEMPT` 的钩子同一个求值点、同一条
    ``player.seat not in exempt`` 判据，见下面的实现），③④⑤ 照常跑，**不会**放宽任何
    目标合法性。没有"强制查距离"的读者，故这里只有一个方向。
    """
    behavior = behavior_for(card)
    rule = behavior.target_rule
    if rule.count is TargetCount.RATED and not rule.is_range and rule.rated_targets == 0:
        return ()
    if rule.self_only:
        me = state.player(seat)
        if not me.alive or (rule.requires_wounded_self and me.hp >= me.hp_max):
            return ()
        # 废除区域（lei-batch 票 02；用户 2026-11-05 裁定）：装备牌的使用目标是"自己的
        # 那个装备槽"——该槽位（或整个装备区）被封印 ⇒ 这张装备牌不能用。
        if card.slot is not None and zone_sealed(me, card.slot.value):
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
        # ② 的距离 / 攻击范围豁免（票 03 的新挂点）：本次使用的**豁免座位集**，``current``
        # 从空元组起、回调只许追加。求值点在 ③④⑤ **之前** ⇒ 下面被它放行的座位马上还要
        # 过延时封印、附加谓词与 TARGET_POOL 链，"放宽距离"因此不可能复活非法座位。
        #
        # **两条声明来源、同一份求值**（票 13 的评审 P3）：调用方的 ``distance_exempt``（技能
        # 侧自算池的载体）只是把"全体座位"喂进**同一个**豁免集，随后仍走下面那一条
        # ``player.seat not in exempt`` 判据——没有第二套"整条跳过距离"的实现，两条路因此
        # 不可能给出不同的合法座位集（③④⑤ 也都在它们之后照常跑）。
        exempt: tuple[int, ...] = (
            tuple(player.seat for player in state.players)
            if distance_exempt
            else apply_modifiers(
                ModifierPoint.TARGET_RANGE_EXEMPT,
                state,
                ModifierContext(subject=seat, card=card),
                (),
            )
        )
        pool = []
        for player in state.players:
            if not player.alive:
                continue
            if player.seat == seat and not rule.includes_self_in_pool():
                continue
            if player.seat not in exempt:
                if rule.needs_distance and distance(
                    state, seat, player.seat
                ) > attack_range(state, seat):
                    continue
                if max_distance is not None and distance(
                    state, seat, player.seat
                ) > max_distance:
                    continue
            pool.append(player.seat)
    # ③ 延时锦囊另有一条**规则层**限制：判定区按**牌名**禁同名（票 03 §5），且判定区被
    # 封印的角色不是合法目标（lei-batch 票 02）——被封印者不落【乐不思蜀】/【兵粮寸断】、
    # 【闪电】越过（后者另见 `delayed_settle_target`）。
    if behavior.is_delayed:
        pool = [
            target
            for target in pool
            if not zone_sealed(state.player(target), SEALED_ZONE_JUDGE)
            and not _same_named_in_judge(state, target, card)
        ]
    # ④ 附加谓词（行为表声明，TARGET_FILTERS 注册表解析）。
    pool = [
        target
        for target in pool
        if all(
            TARGET_FILTERS[name](state, seat, target) for name in behavior.target_filters
        )
    ]
    # ⑤ 第 0 层定集合之后过 TARGET_POOL 链（票 07 §3 的挂点）：M2 的【方天画戟】在此不改池
    # （额定与额外目标的合法性检测标准相同，`A/flow_condition.txt:46`），M3 的"标准不同"
    # 类效果从这里分叉。**票 03 起这条链只做合法性增删**（空城 / 谦逊 / 帷幕的剔除）——
    # "放宽距离 / 攻击范围"已迁到 ② 的 TARGET_RANGE_EXEMPT，故本链的回调不许把被 ③④ 判
    # 非法的座位加回来（今天也没有这种回调）。无修饰器时原样返回，故 M1 的候选逐字节不变。
    return tuple(
        apply_modifiers(
            ModifierPoint.TARGET_POOL,
            state,
            ModifierContext(subject=seat, card=card),
            tuple(pool),
        )
    )


def check_usable(
    state: GameState,
    seat: int,
    card: CardRef,
    *,
    context: UseContext = UseContext.PLAY_PHASE,
    require_targets: bool = True,
) -> bool:
    """``seat`` 此刻**能不能用**这张牌：L3 的"使用前校验"整条链，合成一个可调用的档。

    回答的是"这张牌此刻真的能使用吗"，而不是其中任意半条。四类判据全部**委托**既有函数，
    这里一条规则都不新造（顺序按"最便宜 / 最硬"的档排）：

    1. **时机 / 语境**：行为表 ``use_timings`` 里有没有 ``context`` 这一档
       （:func:`_usable_in`；候选生成器 :func:`_usable_hand` 读的是同一份声明）；
    2. **禁止使用**（L3.1）：:func:`check_forbidden` 的 ``FORBIDDEN`` 链，以及
       :func:`forbidden_as_slash`（「不能当作【杀】使用 / 打出」的标签，按牌面牌名判）；
    3. **次数**（L3.2）：:func:`check_count`——【杀】的本回合额度、【酒】的每回合一次都在
       这一条里（计数键由行为表声明，不按牌名特判）；
    4. **目标池**（L3.3）：:func:`check_targets` 非空——【桃】满体力、【杀】没有可达目标、
       【闪】这类 0 目标牌在出牌阶段没有目标，都是这一条。

    合成这一档的理由（yijiang-2014-round2 票 18）：界【渐营】② 的"牌名置灰"原先只调第 4
    条，于是次数用尽的【杀】仍显示可用、提交后才失败。技能侧**同一个判据**另有两处内联
    （界刘备【仁德】的 ``_jie_rende_basic_options`` = 第 3 + 第 4 条；【蛊惑】的
    ``_guohuo_unusable_names`` = 只有第 4 条），本票不迁移它们（行为不动），只把这一档
    公开出来供新读点使用——故**不改变**任何既有技能的候选。

    ``card`` 可以是实体牌，也可以是转化 / 虚拟产物（判据只读行为表与牌面，不要求它在某区）。

    ``require_targets=False``（table-ux-overhaul 票 12 / spec §2 Q20）：只读**前三条**
    （时机 / 禁用 / 次数），不读第 4 条目标池。这是**源牌级**置灰的判据——"这张源牌有
    没有任何产物此刻可用"问的是"能不能用"，不是"这一步选谁"；目标维度属于**产物级**
    与**目标级**（前者读整条，后者由座位池 / 预览回答）。两档**同一个**实现：置灰判据
    因此只有一份，不新造第二条使用校验链。
    """
    behavior = behavior_for(card)
    if not _usable_in(behavior, context, None):
        return False
    if check_forbidden(state, seat, card):
        return False
    if is_slash(behavior) and forbidden_as_slash(state, card):
        return False
    if check_count(state, seat, card):
        return False
    if not require_targets:
        return True
    return bool(check_targets(state, seat, card))


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
        # 废除区域（lei-batch 票 02）：判定区被封印的角色**越过**（闪电不停在他那里）。
        if zone_sealed(state.player(target), SEALED_ZONE_JUDGE):
            continue
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
    payload: RespondContext | SelectContext | DodgeContext | PeachContext | SkillActivationContext | SelectOptionContext | SelectOptionsContext | DistributeContext | None = None,
) -> tuple[Action, ...]:
    """给出 ``seat`` 在 ``kind`` 询问下的全部候选，按票 05 的稳定键排序。

    ``dying`` 只在 ``PEACH`` 询问下需要：濒死语境里【桃】的合法目标是**濒死者**
    而不是应答者自己，故目标不走行为表的目标规则（票 07 §4 / 票 21 §7）。
    票 27 的新 kind 走**类型化 ``payload``**（票 19 §4）；票 30 再补三类。

    **需要（使用 / 打出）牌窗口按档逐档问**（table-ux-overhaul 票 04 / Q13 / Q17）：
    四扇窗口（【闪】/ 求桃 / 打出·使用响应 / 无懈）的候选先取全部档
    （:func:`_actions_for`），再交给 :func:`_need_card_face` 切成"**只留最小档**"的一张面
    ——档 < ``payload.floor`` 的候选（上一档已过）与更高档的候选都**不在这一问里**。
    分面放在这里而不是各窗口里：本函数是全部询问候选的**唯一生成口**，四扇窗口因此
    不可能只有三扇接线，窗口自己也不必知道"现在问哪一档"。
    其余询问走别的 ``payload``、也**不跑**这条闸门，故它们的候选一条不动
    （改判窗口同样不跑：改判不是"使用 / 打出"）。

    "``Pass`` 之后进下一档"由引擎驱动（:func:`~sgs.engine.Engine.ask_query` 用
    :func:`next_need_card_tier` 现算下一档），窗口只按收到的应答收尾——故"最后一档
    ``Pass`` = 这次需要牌不满足"仍是各窗口既有的那条分支。
    """
    actions = _actions_for(
        state, seat, kind, answers_seq=answers_seq, dying=dying, payload=payload
    )
    floor = _window_floor(kind, payload)
    if floor is None:
        return actions
    return _need_card_face(actions, floor=floor)


def _actions_for(
    state: GameState,
    seat: int,
    kind: QueryKind,
    *,
    answers_seq: int,
    dying: int | None = None,
    payload: RespondContext
    | SelectContext
    | DodgeContext
    | PeachContext
    | SkillActivationContext
    | SelectOptionContext
    | SelectOptionsContext
    | DistributeContext
    | None = None,
) -> tuple[Action, ...]:
    """**全部档**的候选（唯一生成口）：过"能不能用"与禁令，但**不分面**。

    :func:`legal_actions_for`（分面后交给玩家）与 :func:`next_need_card_tier`（``Pass``
    之后现算下一档）读**同一份**生成——分面只在一处发生，两处不可能对"还有没有下一档"
    给出相反的回答。
    """
    if kind is QueryKind.PLAY_PHASE:
        candidates = _play_phase(state, seat, answers_seq)
    elif kind is QueryKind.DISCARD:
        candidates = _discard(state, seat, answers_seq)
    elif kind is QueryKind.DODGE:
        ctx = payload if isinstance(payload, DodgeContext) else DodgeContext()
        candidates = _dodge(state, seat, answers_seq, ctx.providers_used)
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
    elif kind is QueryKind.SELECT_OPTIONS:
        if not isinstance(payload, SelectOptionsContext):
            raise ValueError("SELECT_OPTIONS 询问必须给出 SelectOptionsContext")
        candidates = _select_options(seat, answers_seq, payload)
    elif kind is QueryKind.ARRANGE:
        if not isinstance(payload, ArrangeContext):
            raise ValueError("ARRANGE 询问必须给出 ArrangeContext")
        candidates = _arrange(answers_seq, payload)
    elif kind is QueryKind.DISTRIBUTE:
        if not isinstance(payload, DistributeContext):
            raise ValueError("DISTRIBUTE 询问必须给出 DistributeContext")
        candidates = _distribute(answers_seq, payload)
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


def _window_floor(kind: QueryKind, payload: Any) -> int | None:
    """这扇询问的"已过档"闸门输入；``None`` = **不是**需要（使用 / 打出）牌窗口（不分面）。

    四扇需要牌窗口的载荷都带 ``floor``（票 03 §4-2）；改判同样带 ``RespondContext``，但
    改判**不是**使用 / 打出（不压 USE 帧、不触发使用后钩子），故它走 ``None`` 这一档。
    """
    if kind is QueryKind.DODGE:
        return 0 if not isinstance(payload, DodgeContext) else int(payload.floor)
    if kind is QueryKind.PEACH:
        return 0 if not isinstance(payload, PeachContext) else int(payload.floor)
    if kind is QueryKind.RESPOND:
        if isinstance(payload, RespondContext) and payload.mode is not RespondMode.REJUDGE:
            return int(payload.floor)
    return None


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

    **次数豁免按目标收窄**（lei-batch 票 04）：:func:`check_count` 报超额时不再整张牌跳过，
    而是把池换成 :func:`_count_exempt_targets` 的"被豁免角色"再照常摊组合——雄乱生效时
    只有"目标集合 ⊆ {雄乱目标}"的那些使用出现。没有按目标豁免的修饰器时那个池恒空，
    故其余牌（以及全部金录像）的候选逐字节不变。

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
        exhausted = check_count(state, seat, card)
        behavior = behavior_for(card)
        rule = behavior.target_rule
        pool = check_targets(state, seat, card)
        if exhausted:
            # 次数额度用尽：**仍然枚举目标**，只保留"以它为目标的那次使用仍被豁免"的座位
            # （lei-batch 票 04：雄乱「你对其使用牌无次数限制」按目标判）。收窄后再照常
            # 摊组合 ⇒ 多目标牌只会生成"目标集合 ⊆ 被豁免角色"的那些组合。没有豁免
            # （绝大多数牌）⇒ 空池 ⇒ 与加本机制前同一条 ``continue``。
            pool = _count_exempt_targets(state, seat, card, pool)
            if not pool:
                continue
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
            # 窗口"要一张什么牌"与另外三档同源：提供者（【蛊惑】）按它收窄声明范围。
            respond_required=DODGE_RESPONSE_CARD,
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
    - 次数上限：**濒死自救不查次数**（用户 2026-11-02 裁定）。【酒】的两种用法是两条轴——
      出牌阶段增伤每回合一次；濒死自救**无次数限制、也不占用那一次**（黄盖本回合喝过酒
      增伤，苦肉进入濒死后照样能喝酒自救，且可连喝到脱离濒死）。故 ``near_death_self_only``
      的牌跳过 ``check_count``；【桃】本来无次数上限，查与不查等价。

    **提供者路径**（票 03）：除了真牌，**"需要牌时"的提供者**（【蛊惑】扣置一张手牌当【桃】）
    也在这里产出候选——它走 ``CANDIDATES`` 的 ``PEACH`` 语境，与真牌同一个槽位。它**不是**
    转化技：受理时已经扣置 / 质疑 / 验明完毕，故不进下面那条"确认即定死"的通道。
    它的**牌名候选**读同一份语境判据（见 `skills/active.py:_guohuo_response_names`）：
    濒死者本人是于吉时【酒】与【桃】都在声明表里。

    **转化源路径**（W4-3 / 02x 路线 A）：除了真牌，**能把牌当"濒死时可用之牌"的转化技**
    也在这里产出候选——急救的红牌当【桃】、界【龙胆】的【酒】当【桃】、董卓移动版
    【酒池】的**黑桃手牌当【酒】**（濒死自救，规则 §5.5 L605）。判据与真牌那一趟**同源**：
    产物牌型的 ``target_rule.near_death_self_only`` 为真 **或** 牌名是 ``peach``
    （:func:`~sgs.skills.near_death_conversion_offers`；该函数同时按
    ``CandidateScope.PEACH`` 收窄每一条声明）。漏掉这条路径，急救就永远进不了任何候选
    （``scopes`` 与"限回合外"互斥），酒池的濒死自救也永远没有入口。
    """
    actions: list[Action] = [
        UseCard(card=card, targets=(dying,), answers_seq=answers_seq)
        for card in _usable_hand(state, seat, UseContext.NEAR_DEATH)
        if (seat == dying or not behavior_for(card).target_rule.near_death_self_only)
        and (  # 濒死自救**不查次数**（用户 2026-11-02 裁定，见上面的 docstring）
            behavior_for(card).target_rule.near_death_self_only
            or not check_count(state, seat, card)
        )
    ]
    actions.extend(_transformed_peaches(state, seat, dying, answers_seq))
    # "需要牌时"的**提供者**（票 03）：同【闪】窗口那一趟整段追加——【蛊惑】扣置一张手牌
    # 当【桃】（无人质疑 /（真 且 ♥）才是，否则作废）。它不是转化技：受理时**已经**验明完毕，故不进
    # ``transform_preactivations`` 那条"确认即定死"的通道。
    ctx = ModifierContext(
        subject=seat,
        # 濒死者（lin-batch2 票 03）：贾诩移动版【完杀】的判据要读"被救的是谁"
        # （「除你**和其**以外」⇒ 濒死者本人不受限）。此前 ``target`` 没有
        # ``PEACH`` 读者，故这一行不改任何既有回调的行为。
        target=dying,
        answers_seq=answers_seq,
        scope=CandidateScope.PEACH,
        respond_required=PEACH_RESPONSE_CARD,
    )
    actions = list(apply_modifiers(ModifierPoint.CANDIDATES, state, ctx, tuple(actions)))
    # **纯删型回调的第二趟收口**（lin-batch2 票 03，贾诩移动版【完杀】）：
    # ``CANDIDATES`` 链是**加法管道**——链上后跑的提供者（【蛊惑】"扣置一张手牌当【桃】"）
    # 会把产物追加到已经被删空的列表里（链序 = 解析序 ``(优先级档, skill_id)``，
    # ``hero.jiaxu_mobile.wansha`` 排在 ``hero.yuji_mobile.guohuo`` 之前）。故在
    # "第一趟产生了提供者候选"时再收口一趟：**已经出现的提供者**进 ``providers_used``
    # （它们按自己的口径闭嘴、不重复追加），纯删型回调因此在最后拿到发言权。
    # 没有提供者候选时**不跑**第二趟 ⇒ 既有求桃窗口（含 47 条金录像）的调用次数不变。
    providers = tuple(
        action.skill_id for action in actions if isinstance(action, ActivateSkill)
    )
    if providers:
        actions = list(
            apply_modifiers(
                ModifierPoint.CANDIDATES,
                state,
                ModifierContext(
                    subject=seat,
                    target=dying,
                    answers_seq=answers_seq,
                    providers_used=providers,
                    scope=CandidateScope.PEACH,
                    respond_required=PEACH_RESPONSE_CARD,
                ),
                tuple(actions),
            )
        )
    actions.append(Pass(answers_seq=answers_seq))
    return actions


def _transformed_peaches(
    state: GameState, seat: int, dying: int, answers_seq: int
) -> list[Action]:
    """求桃窗口的**转化源候选**：每张能当"濒死时可用之牌"的来源牌各一个 ``UseCard``。

    02x 路线 A（E2 裁定，用户 2026-11-08 签 Q1 = 路线 A / Q2 = A2）把判据从写死的
    ``"peach"`` 泛化成"**产物牌型可在濒死语境使用**"
    （:func:`~sgs.skills.near_death_conversion_offers` 与 :func:`_peach` 的真牌那一趟
    读同一份行为表声明：
    ``target_rule.near_death_self_only`` 或牌名是 ``peach``），并**同时**按
    ``CandidateScope.PEACH`` 收窄每一条转化声明（否则界【龙胆】的「【桃】当【酒】」会在
    濒死窗口多亮一条）。【酒】的候选只在 ``seat == dying`` 时产出（`near_death_self_only`
    = "濒死时对**自己**使用"）；目标恒为窗口固定的 ``dying``；**不查次数**（既有口径，
    见 :func:`_peach`）。

    惰性 import：``sgs.skills`` 反向依赖本模块（``skills/virtual.py`` 读 ``sgs.effects``
    的 ``check_targets`` 等），模块级 import 会成环。目标由窗口固定成 ``dying``——
    这一点与清单的"无目标步"是同一件事（`spec.md` §2 的急救行）。
    """
    from sgs.skills import near_death_conversion_offers, transformed_card

    return [
        UseCard(
            card=transformed_card(state, (source,), product),
            targets=(dying,),
            answers_seq=answers_seq,
        )
        for source, _skill_id, product in near_death_conversion_offers(
            state, seat, dying
        )
    ]


def _respond(
    state: GameState, seat: int, answers_seq: int, ctx: RespondContext
) -> list[Action]:
    """响应窗口：``ctx.zones`` 里满足 ``required`` 的牌各一个候选，放弃恒在最后。

    取牌走 :func:`_required_cards`（缺省就是 :func:`_required_hand`）——**与【闪】窗口
    同一道牌名闸门**（B1）。``required`` 是牌名（``slash`` / ``dodge`` / ``nullify``），
    不是询问种类：同一个函数服务南蛮 / 万箭 / 决斗 / 借刀 / 无懈五种语境，按 ``QueryKind``
    分支就会漏掉其中一类。``ctx.zones`` 缺省 ``(HAND,)``；改判窗口按座位技能定义放宽到
    装备区（界【鬼才】"打出一张牌"，M6 票 14）。

    - ``mode = PLAY``（南蛮 / 万箭 / 决斗 / 改判）：候选是 ``PlayCard``（打出，无目标、
      不占次数）；
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
    if ctx.mode is RespondMode.REJUDGE:
        # 改判窗口（feng-zhangjiao 票 02）：**逐技能**枚举，一条技能一种构造子。
        # 候选是 `SubstituteJudgeCard`（不是 `PlayCard`）：它不是打出，引擎据此压一个
        # REJUDGE 事件帧。颜色筛选由技能声明派生（【鬼道】= 黑色），区也由它给。
        rejudge: list[Action] = []
        for offer in ctx.rejudge_offers:
            for card in _required_cards(state, seat, None, offer.zones):
                if offer.color is not None and card.color is not offer.color:
                    continue
                rejudge.append(
                    SubstituteJudgeCard(
                        skill_id=offer.skill_id, card=card, answers_seq=answers_seq
                    )
                )
        rejudge.append(Pass(answers_seq=answers_seq))
        return rejudge
    actions: list[Action] = []
    for card in _required_cards(state, seat, ctx.required, ctx.zones):
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
    scope = _respond_scope(ctx.required)
    if scope is not None:
        # 【丈八蛇矛】的转化【杀】可**打出**、【八卦阵】可"视为打出【闪】"（票 10 §5.5 /
        # 票 30）、【蛊惑】可"扣置一张手牌当它"（票 03）：整段候选期追加一趟 CANDIDATES。
        # "每提供者只问一次"由这里的通用过滤兜底；收集式窗口的"赢家已定"由
        # ``respond_declared`` 收口（提供者的产物是扣置一张牌换来的，不能白扣）。
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
                respond_declared=ctx.declared,
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


def _respond_scope(required: str | None) -> CandidateScope | None:
    """响应窗口按**请求牌名**选 ``CANDIDATES`` 的语境；``None`` = 这一趟不派发。

    杀族请求与【闪】共用原有两档（火 / 雷【杀】与转化牌因此同形）；【无懈可击】是票 03
    的加法——它是**收集式**窗口（先声明、赢家才使用），故独占 ``NULLIFY`` 一档：
    提供者在那里的产物只能算"声明"，与打出 / 使用两档的交付方式都不同。
    """
    if required == SLASH_FAMILY_KEY:
        return CandidateScope.RESPOND
    if required == DODGE_RESPONSE_CARD:
        return CandidateScope.DODGE
    if required == NULLIFY_RESPONSE_CARD:
        return CandidateScope.NULLIFY
    return None


def _select_cards(
    state: GameState, seat: int, answers_seq: int, ctx: SelectContext
) -> list[Action]:
    """选牌：按 ``spec.total`` 枚举"取 k 张"的全部组合；``optional`` 时放弃在最后。

    ``spec.zones`` 已由调用方用于铺选择面，``per_zone`` / ``sequential`` 是 M3 的扩展点
    （票 19 §3 的"M3 只许增"）。M2 的三张选牌牌都是"取满 1 张"的强制选择，
    故候选恰是每个槽位一个单张；池为空时调用方**不构造询问**（票 19 §6）。

    ``spec.max_per_suit``（神吕蒙移动版【涉猎】）：只枚举**每种花色至多 k 张**的组合。
    判据是**候选生成期**的（不是界面灰显）：不在候选里的组合，引擎会拒；界面读同一份
    候选把同花色的其余牌置灰。槽位没有牌面（隐藏槽）时无法判花色——那种询问不该设这个
    字段，故按"不拦"处理（fail-open 只会在配置错误时出现，不会让非法组合进候选）。
    """
    del state, seat
    low, high = ctx.spec.total
    per_suit = ctx.spec.max_per_suit

    def allowed(picks: tuple[int, ...]) -> bool:
        if per_suit is None:
            return True
        counts: dict[str, int] = {}
        for index in picks:
            card = ctx.slots[index].card
            suit = None if card is None else card.suit
            if suit is None:
                continue
            counts[suit] = counts.get(suit, 0) + 1
            if counts[suit] > per_suit:
                return False
        return True

    actions: list[Action] = [
        SelectCards(picks=picks, answers_seq=answers_seq)
        for count in range(low, min(high, len(ctx.slots)) + 1)
        for picks in combinations(range(len(ctx.slots)), count)
        if allowed(picks)
    ]
    if ctx.spec.optional:
        actions.append(Pass(answers_seq=answers_seq))
    return actions


NEED_CARD_FLOOR_KEY = "need_card_floor"
"""帧私有键：四扇**需要（使用 / 打出）牌**窗口的"已过档"游标（票 03 §4-2 / 票 04 §2），
按座位分槽。

形态 = ``{座位: int}``（缺项 = 0 = 从最小档起算）= **下一次该问的最小档**。"不升档"是
**逐角色**的（仓内 §4.16 L443 "先逐角色、角色内部再按优先级"），故：求桃 / 无懈 / 势力求助
逐个应答者、【决斗】双方轮流——同一帧里会有多个座位的档，挤在一个 int 里会串味。

票 04 起它的语义从"闸门下限"扩成"**按档逐档问**的游标"：

- 候选生成口（:func:`legal_actions_for`）只把**最小档 ≥ floor** 的那一条面交给玩家；
- 玩家 ``Pass`` ⇒ 引擎用 :func:`next_need_card_tier` 现算下一档，由
  :func:`record_need_card_floor` 写进这里（``Pass`` = 本档不发动、进下一档）；
- 选了一条候选 ⇒ 记它的档（:func:`record_need_card_choice`）：同档仍可再问（提供者可能
  没通过），更高优先的档不再进面；
- 最后一档 ``Pass`` = 这次需要牌不满足，窗口照旧收尾。

floor 的粒度是**一个需要牌事件**（补充一手 §4.2.2 的 ❶ ②③ + §2.1.2.2(2) 的"每时机一次
响应机会"）：**这一次响应成立**时清回 0（【无双】的两张【闪】是两次事件），清点由各窗口的
成功分支调 :func:`reset_need_card_event`（连 ``providers_used`` 一起复位）。

**帧私有**（不进记录流）；它是**逐目标**分槽的状态，故必须登记在
:data:`sgs.engine.steps._PER_TARGET_FRAME_KEYS`——否则同一帧的下一个目标会继承上一个目标的
档（与 ``providers_used`` / ``dodges_done`` 同姿态）。
"""


def tier_not_downgraded(tier: int, floor: int) -> bool:
    """**不升档**（补充一手 §2.1.2.3）：档值越小越优先，故闸门 = ``tier >= floor``。

    小于 ``floor`` 的档（= 比上一次更高优先）不再进面，同档与更低优先的档照进。这一行
    判据由三处消费：票 01 的 :func:`activation_allowed`（同一时机的激活结算）、票 03 的
    四个需要牌窗口（:func:`_need_card_face`）与票 04 的"下一档"现算
    （:func:`next_need_card_tier`）。写在**一处**，三边不可能对"高档 / 低档"给出相反的读法。
    """
    return int(tier) >= int(floor)


def need_card_tier(action: Action) -> int:
    """一条"需要（使用 / 打出）牌"候选的**优先级档**（票 03 §4-1 / 票 04 §1-§3）。

    - 真牌 / 转化牌（``UseCard`` / ``PlayCard``）= ``CARD_USE``（2）：窗口要的是**一张牌**，
      牌从手牌来还是从装备区 / 转化来，不改变这一档；
    - ``ActivateSkill``（提供者）= 该技能定义声明的
      :attr:`~sgs.skills.SkillDef.need_card_tier`；没写覆盖位时由载体派生
      （:attr:`~sgs.skills.SkillDef.priority_class`）：【八卦阵】= **装备档**（1），
      【护驾】/【激将】= **武将档**（0）。
    - **转化类提供者落第 2 档**（Q16 / Q17）：【蛊惑】/ 界【渐营】② 的载体是武将牌，产物
      却是转化牌 ⇒ 它们在各自的声明上写 ``need_card_tier=2``。本函数不写技能名名单，
      覆盖位是**定义**的属性（账本行由票 06 落）。

    档是**定义**的属性、不是局面属性，故判据不读 ``GameState``。提供者候选必然来自一条
    **解析得到的实例**（``CANDIDATES`` 链只遍历 :func:`sgs.skills.resolve_instances` 的
    产物），故"目录里没有这条定义"是编程错误：**不 fail-open**（那会静默放过一条低档
    提供者），让 ``KeyError`` 冒泡。
    """
    if not isinstance(action, ActivateSkill):
        return int(PriorityClass.CARD_USE)
    from sgs.skills import catalog as catalog_module

    definition = catalog_module.CATALOG[action.skill_id]
    if definition.need_card_tier is not None:
        return int(definition.need_card_tier)
    return int(definition.priority_class)


def _need_card_face(candidates: Iterable[Action], *, floor: int) -> list[Action]:
    """把全档候选切成**一档一张面**（票 04 §2）：``Pass`` 恒留 + 最小档的那些候选。

    ``floor`` 是"已经过完的档"：档 < ``floor`` 的候选（A 档已经 Pass / 已经试过，不能
    往回升）与比最小档更低的档都**不进这一问**——于是"不同档混在一张面里"这件事不再
    可能发生，玩家也不必靠点选顺序去撞它（票面根因 1）。

    面里**没有非 Pass 候选**时只剩 ``Pass``：引擎在发问之前就用
    :func:`next_need_card_tier` 判过"还有没有下一档"，故这一支只在窗口自己没料到时出现
    （既有语义：``Pass`` 恒可选）。
    """
    pool = [action for action in candidates if not isinstance(action, Pass)]
    tiers = sorted(need_card_tier(action) for action in pool)
    visible = [tier for tier in tiers if tier_not_downgraded(tier, floor)]
    if not visible:
        return [action for action in candidates if isinstance(action, Pass)]
    face = visible[0]
    return [
        action
        for action in candidates
        if isinstance(action, Pass) or need_card_tier(action) == face
    ]


def next_need_card_tier(
    state: GameState,
    seat: int,
    kind: QueryKind,
    *,
    answers_seq: int,
    dying: int | None = None,
    payload: Any = None,
) -> int | None:
    """``Pass`` 之后**还有没有下一档**：返回下一档（比本档更低优先的那一档）；``None`` = 没有。

    判据与 :func:`legal_actions_for` **同源**（同一个 :func:`_actions_for` 生成口）：
    本函数只回答"这张面之后还有没有更低的档"，不另算一份"哪些候选可用"。
    本档 = 最小档 ≥ ``payload.floor``（就是玩家刚看到的那一张面）；下一档 = 比它大的最小档。

    消费点只有一处：引擎拿到 ``Pass`` 之后用它决定"换一档再问"还是"这次需要牌不满足"
    （:meth:`~sgs.engine.Engine.ask_query` / :meth:`~sgs.engine.Engine.ask_respond`）。
    """
    actions = _actions_for(
        state, seat, kind, answers_seq=answers_seq, dying=dying, payload=payload
    )
    floor = _window_floor(kind, payload) or 0
    tiers = sorted(
        {
            need_card_tier(action)
            for action in actions
            if not isinstance(action, Pass)
            and tier_not_downgraded(need_card_tier(action), floor)
        }
    )
    if not tiers:
        return None
    face = tiers[0]
    return next((tier for tier in tiers if tier > face), None)


def need_card_floor(frame: Any, seat: int) -> int:
    """``seat`` 在这扇需要牌窗口里**已经过完**的档（读 :data:`NEED_CARD_FLOOR_KEY`）。

    缺项 = 0 = 最高档（首问一律从最小档起算）。
    """
    floors = frame.data.get(NEED_CARD_FLOOR_KEY)
    if not floors:
        return 0
    return int(floors.get(seat, 0))


def record_need_card_floor(frame: Any, seat: int, tier: int) -> None:
    """把"下一档"写进承载帧（``Pass`` = 本档不发动 ⇒ 进下一档的唯一写入点，票 04 §2）。

    与 :func:`record_need_card_choice` 同一张表（:data:`NEED_CARD_FLOOR_KEY`），区别只在
    来源：一条是"选中的候选的档"，一条是"引擎现算出来的下一档"。
    """
    floors = frame.data.setdefault(NEED_CARD_FLOOR_KEY, {})
    floors[seat] = int(tier)


def record_need_card_choice(frame: Any, seat: int, action: Action) -> None:
    """记下这一次应答选中的**档**：下一次询问据此"不升档"（``Pass`` 不记）。

    调用点只在引擎**拿到应答之后**的统一收口处（``Engine.ask_query`` 的【闪】/ 求桃 与
    ``Engine.ask_respond`` 的真牌 / 无懈窗口）：窗口随后按"这一次响应成不成立"决定是否
    :func:`reset_need_card_floor`——提供者没通过时 floor 留着（曹操例），成立时清掉
    （【无双】的第二张【闪】）。``Pass`` 不记：它的下一档由 :func:`record_need_card_floor`
    写（那是"本档不发动、进下一档"，不是"选了哪一档"）。
    """
    if isinstance(action, Pass):
        return
    record_need_card_floor(frame, seat, need_card_tier(action))


def reset_need_card_floor(frame: Any, seat: int | None = None) -> None:
    """一次需要牌**事件成立**：把 ``seat`` 的 floor 清回 0（``seat=None`` = 清整个帧）。

    "事件成立"的落点由各窗口自己判（它才知道"这一次响应"怎么算成立）：
    【闪】/【决斗】= 每收下一张牌（【无双】的两张 = 两次事件），求桃 = 那张【桃】开始回复。
    """
    if seat is None:
        frame.data.pop(NEED_CARD_FLOOR_KEY, None)
        return
    floors = frame.data.get(NEED_CARD_FLOOR_KEY)
    if floors:
        floors.pop(seat, None)


def reset_need_card_event(frame: Any, seat: int | None = None) -> None:
    """一次需要牌**事件成立**：不升档 floor 与**提供者封禁**在同一个点一起复位（票 04 §5）。

    ``providers_used``（"每提供者只问一次"的黑名单）的粒度与 floor **同构**——都是
    "一个需要牌事件"，不是"整扇窗口"也不是"整张目标"。改造前它只在帧的**目标推进**时
    清（:data:`sgs.engine.steps._PER_TARGET_FRAME_KEYS`）⇒【无双】要两张【闪】时，第一张
    先发动【八卦阵】判定失败、再用真【闪】补上，第二张就已经不能再发动【八卦阵】——
    语义应当是"一次杀的结算过程内，一旦以任何方式使用 / 打出了【闪】，封禁解除"。

    因此它的复位点**就是** :func:`reset_need_card_floor` 的复位点（各窗口"这一次响应
    成立"的那一行），本函数把两者收在一处，调用方只调这一条。
    """
    reset_need_card_floor(frame, seat)
    frame.data.pop("providers_used", None)


def activation_allowed(
    skill_id: str, tier: int, *, floor: int, used: Sequence[str]
) -> bool:
    """**同一时机激活结算**闸门的唯一判据（票 01 §4-3）：两关。

    1. **不升档**（补充一手 §2.1.2.3）：选的这一条 "优先级须在不超过上次的前提下最高"。
       档值越小越先（``HERO=0 < EQUIPMENT=1 < CARD_USE=2``），故闸门 = ``tier >= floor``：
       小于 ``floor`` 的档（= 比上次更高优先）不再进面，同档与更低的档照进。
       这一关的实现是 :func:`tier_not_downgraded`——票 03 的四个需要牌窗口读**同一行**。
    2. **每技能一次**（补充一手 §2.1.2.2(2)）：本座位本 pass 已经跑过的技能不再进面。
       ``used`` 是**显式**记录（:attr:`~sgs.state.SkillActivationContext.used`），不再靠
       答案缓存当去重手段。

    两个输入都由派发环（:func:`sgs.skills.catalog._dispatch`）写进类型化载荷；本函数是
    候选生成口（:func:`legal_actions_for`）与派发环**共用**的那一份判据——两处各写一遍
    就会在"这一轮到底能选什么"上漂移。
    """
    return tier_not_downgraded(tier, floor) and skill_id not in used


def _skill_activation(
    seat: int, answers_seq: int, ctx: SkillActivationContext
) -> list[Action]:
    """"是否发动"：一次询问、**本轮的全部候选**各一条裸 ``ActivateSkill``（票 01 §4）。

    ``ctx.candidates`` 空 ⇒ 旧构造的单候选面（只展开 ``ctx.skill_id``），与改造前逐字节
    相同；非空 ⇒ 按 ``(技能 id, 档)`` 逐条展开，并过 :func:`activation_allowed` 的两关闸门
    （``floor`` / ``used``）。成本 / 载荷仍由发动后的 ``SkillFlow`` 步骤询问，故候选依旧
    不烘焙 ``picks`` / ``choices``（W6-06a）。``optional`` 决定放弃候选——"可以 / 必须"是
    效果属性，与 ``locked`` 正交。
    """
    del seat
    if ctx.candidates:
        faces = tuple(
            skill_id
            for skill_id, tier in ctx.candidates
            if activation_allowed(skill_id, tier, floor=ctx.floor, used=ctx.used)
        )
    else:
        faces = (ctx.skill_id,)
    actions: list[Action] = [
        ActivateSkill(skill_id=skill_id, answers_seq=answers_seq) for skill_id in faces
    ]
    if ctx.optional:
        actions.append(Pass(answers_seq=answers_seq))
    return actions


def _select_option(
    seat: int, answers_seq: int, ctx: SelectOptionContext
) -> list[Action]:
    """选项闭集：每个**可选中**的选项一个候选（缺省**强制选择**，没有 ``Pass``，票 12 §Q6）。

    ``ctx.optional``（票 00 / N4）才多一条 ``Pass``——它是"取消这一步"的合法应答，
    供发动后的技能流水线用；缺省 ``False``，故既有消费者候选一字不变。

    ``ctx.disabled``（票 07；票 02 §4.2 B3）是**显示但不可选**的选项：它们保留在询问的
    ``options`` 里（投影画成置灰行），但**不进候选**——与 ``StepPool.disabled`` 同语义
    （"列出来但选不了"），只是走逐步询问这条面。缺省空元组 ⇒ 候选逐条不变。
    """
    del seat
    disabled = frozenset(ctx.disabled)
    actions: list[Action] = [
        SelectOption(choice=option, answers_seq=answers_seq)
        for option in ctx.options
        if option not in disabled
    ]
    if ctx.optional:
        actions.append(Pass(answers_seq=answers_seq))
    return actions


def is_legal_option_set(
    face: SelectOptionsContext | SelectOptionsQuery, choices: Sequence[str]
) -> bool:
    """多选答案的**唯一**合法性谓词（yijiang-2014-round2 票 21）：池内 / 无重复 / 无置灰 /
    长度在 ``[min_selected, cap]`` 内。

    ``face`` 读 ``options`` / ``disabled`` / ``min_selected`` / ``max_selected`` 四项——
    候选生成口（:class:`~sgs.state.SelectOptionsContext`）与询问面
    （:class:`~sgs.state.SelectOptionsQuery`）同上同下，故两者都收：**合法集合只有这一份
    真源**，候选枚举、不枚举档的骨架、引擎收答案、AI 自建集合、回放放行（``agents``）全走它，
    不另写第二份判据。

    口径与 :func:`_select_options` 逐条对齐：

    - **池内**：每个 ``choice`` 都在 ``options`` 里（``options`` 是展示面，含置灰）；
    - **无置灰**：``disabled`` 里的项不可选（它们留在 ``options`` 里只为画灰）；
    - **无重复**：答案是一个**集合**，重复项不是"多选了一个"；
    - **区间**：``min_selected <= len(choices) <= cap``，``cap`` 与候选枚举同一条算法
      （``max_selected=None`` ⇒ 只受**可选项**数约束）。

    **不比次序**：答案是集合，``choices`` 的次序不进判据（默认序 = ``options`` 序由写方
    规范化——界面 ``pending.js:seatMultiChoices`` 与 AI 的构造器各自排好；"依次结算"是另一
    档，将来有技能要它时再开票）。
    """
    usable = usable_options(face)
    cap = cap_of(face)
    chosen = tuple(choices)
    if not int(face.min_selected) <= len(chosen) <= cap:
        return False
    if len(set(chosen)) != len(chosen):
        return False
    pool = frozenset(usable)
    return all(choice in pool for choice in chosen)


def usable_options(
    face: SelectOptionsContext | SelectOptionsQuery,
) -> tuple[str, ...]:
    """多选面的**可选项池**：``options`` 去掉 ``disabled``，保持原序。

    与 :func:`is_legal_option_set` 的「池内 / 无置灰」两条同源（谓词与候选枚举都读它）：
    置灰项**留在** ``options`` 里只为画灰，任何生产答案都不许含它 —— 故"可选的"只有这一份
    派生，别处不再自己过一遍 ``disabled``。
    """
    disabled = frozenset(face.disabled)
    return tuple(option for option in face.options if option not in disabled)


def cap_of(face: SelectOptionsContext | SelectOptionsQuery) -> int:
    """多选面的**可达上限**：``min(max_selected, len(usable_options(face)))``。

    ``max_selected=None`` ⇒ 只受可选项数约束。这条派生是 :func:`is_legal_option_set` 区间
    判据的一半，也是**玩家文案**（引擎拒收时的 ``[min, cap]``）与 AI 自建集合的上界。上限
    只在这里算：四处各算一遍时，引擎那份曾把 ``max_selected`` 直接当上限（数了**含置灰**
    的 ``options``），于是拒收文案印出一个永远选不到的数。
    """
    usable = usable_options(face)
    if face.max_selected is None:
        return len(usable)
    return min(int(face.max_selected), len(usable))


def _select_options(
    seat: int, answers_seq: int, ctx: SelectOptionsContext
) -> list[Action]:
    """多选选项（jie-feng-batch2 票 01a）：把 ``[min,max]`` 内的**每个子集**枚举成一条候选。

    枚举序 = 按 ``ctx.options`` 序的位掩码升序 ⇒ **空集排第一**（超时 / AI 的"取第一个
    候选"因此天然是"一项都不做"，零副作用）。置灰项从枚举域里直接去掉（它们既不出现
    在候选里，也不可能被选中）；``optional=True`` 时另加一条 ``Pass``（与单选面同语义）。

    不按位掩码之外的方式枚举（例如"逐项 0/1"）：答案是**一个集合**，一次提交 —— 与
    :func:`_distribute` 的"全量枚举 + 客户端拼出同一条"同姿态，合法集合只有这一份真源。

    ``ctx.enumerate_candidates=False``（**不枚举档**，yijiang-2014-round2 票 21；先例 =
    :func:`_arrange` 的同名档）：子集数随选项数组合爆炸（8 人局、上限 4 ⇒ 162 条），故
    **一条都不枚举** —— 只留一条**骨架兜底**：``options`` 序的前 ``min_selected`` 个可选项
    （``min_selected == 0`` ⇒ 空集，超时兜底仍是"什么都不做"）。骨架**不是答案集**，它只
    兑现"每一问都有至少一个合法应答"（超时代答 / 取首个候选的消费者 / 回放放行都靠它），
    真正的答案由界面 / 参与者组装、引擎按 :func:`is_legal_option_set` 校验形状。可选项凑不
    出下限时**没有**合法集合 ⇒ fail-closed（不发明一条越界的骨架）。
    """
    del seat
    options = usable_options(ctx)
    cap = cap_of(ctx)
    if ctx.min_selected < 0 or cap < ctx.min_selected:
        raise ValueError(
            f"多选区间的下限 {ctx.min_selected} 大于上限 {cap}（选项 {ctx.options!r}）"
        )
    actions: list[Action] = []
    if not ctx.enumerate_candidates:
        # 上面那条区间检查已经覆盖"可选项凑不出下限"：``cap`` 只受**可选项**数约束，
        # 故 ``cap >= min_selected`` ⇒ 骨架一定够长、形状必然合法，不需要第二条守卫。
        skeleton = options[: int(ctx.min_selected)]
        actions.append(SelectOptions(choices=skeleton, answers_seq=answers_seq))
    else:
        for mask in range(1 << len(options)):
            picked = tuple(
                option for index, option in enumerate(options) if mask >> index & 1
            )
            if is_legal_option_set(ctx, picked):
                actions.append(SelectOptions(choices=picked, answers_seq=answers_seq))
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
    if not getattr(ctx, "enumerate_candidates", True):
        # **不枚举档**（huo-shenzhugeliang 票 02）：只给一条恒等兜底 —— 上排 = 面内前
        # ``split`` 张（= 交换前的星），即"什么都不换"。界面自己构造答案（结构校验在
        # :meth:`Engine.ask_arrange`），超时 / AI 的默认因此是**零副作用**的恒等排列。
        split = 0 if ctx.split is None else int(ctx.split)
        return [ArrangeCards(order=tuple(range(ctx.count)), split=split, answers_seq=answers_seq)]
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


def _distribute(answers_seq: int, ctx: DistributeContext) -> list[Action]:
    """计数式分配：**每座 0..``per_seat_max``、合计 ≤ ``max_total``** 的全量枚举。

    顺序即语义的一部分：``counts`` 省略 0 值，故"一个点都不分配"的 ``counts=()``
    在 :func:`_sort_key` 下键最小、恒排第一——超时兜底取 ``legal_actions[0]`` 因此
    是"不分配"（确定性、零副作用）。规模：4 人局 35 条、8 人局 165 条（组合数
    ``C(n + max_total, max_total)``），只在一次【业炎】里出现。
    """
    seats = tuple(ctx.seats)
    limit = max(0, int(ctx.per_seat_max))
    budget = max(0, int(ctx.max_total))
    candidates: list[Action] = []

    def walk(index: int, remaining: int, counts: tuple[tuple[int, int], ...]) -> None:
        if index == len(seats):
            candidates.append(Distribute(counts=counts, answers_seq=answers_seq))
            return
        seat = seats[index]
        for count in range(0, min(remaining, limit) + 1):
            walk(index + 1, remaining - count, counts + ((seat, count),) if count else counts)

    walk(0, budget, ())
    return candidates


def _required_hand(
    state: GameState, seat: int, required: str | None
) -> list[CardRef]:
    """手牌里**能当** ``required`` 牌名的牌（按手牌序）；``None`` = 不过滤牌名。

    **这是所有"需要某张牌"的响应窗口共用的候选闸门**（B1）：``_dodge``（【闪】）与
    ``_respond``（打出【杀】/ 打出【闪】/ 借刀的使用【杀】/【无懈可击】）都从这里取牌。
    改判窗口另有 :func:`_required_cards` 的 ``zones`` 档（界【鬼才】"打出一张牌"，
    M6 票 14 起装备区的牌也进候选）。

    判据是**牌名**而不是 :class:`~sgs.state.QueryKind`：【闪】窗口是独立的 ``DODGE``，
    其余五类挤在 ``RESPOND`` 里；按询问种类分支会让 DODGE 绕过闸门（B1 的根因）。
    真牌与转化牌也不必区分——:func:`~sgs.skills.transformed_card` 把虚拟牌的类型写成
    目标牌名，故【龙胆】的转化【闪】与真【闪】在这里同形。

    **牌名判据只有一处**：:func:`~sgs.effects.satisfies_required`（M5-05b 起【杀】族按
    共享计数键放宽——火【杀】/ 雷【杀】因此出现在南蛮 / 决斗 / 借刀的响应窗口里；其余
    ``required`` 仍逐字相等）。

    ``required = None`` 是改判窗口（``WindowCause.REJUDGE``）：打出一张**任意**牌。
    """
    return _required_cards(state, seat, required, (Zone.HAND,))


def _required_cards(
    state: GameState,
    seat: int,
    required: str | None,
    zones: tuple[Zone, ...],
) -> list[CardRef]:
    """``zones`` 各区里**能当** ``required`` 牌名的实体牌（区序 = ``zones``，区内保持原序）。

    ``zones`` 缺省只有手牌（所有既有窗口）；放宽到装备区只有改判窗口一个消费者：界
    【鬼才】（§十九 L2037）"打出一张牌"⇒ 手牌 ∪ 装备区，标准版（§九 L958）"一张手牌"
    ⇒ 只有手牌。区域读 :meth:`~sgs.state.GameState.zone`（唯一入口），不直接摸
    ``Player`` 的字段——将来多一个候选区时只改 payload，不改这里。
    """
    cards: list[CardRef] = []
    for zone in zones:
        for card_id in state.zone(ZoneRef(zone, seat)):
            card = state.card(card_id)
            if required is not None and not satisfies_required(card, required):
                continue
            if check_forbidden(state, seat, card):
                continue
            if (
                required is not None
                and required == SLASH_FAMILY_KEY
                and forbidden_as_slash(state, card)
            ):
                # 【定乱】牌不能作为【杀】打出（南蛮 / 决斗 / 借刀 / 闪以外的响应窗口）。
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
        behavior = behavior_for(card)
        if not _usable_in(behavior, context, timing):
            continue
        if check_forbidden(state, seat, card):
            continue
        if is_slash(behavior) and forbidden_as_slash(state, card):
            # 【定乱】牌不能作为【杀】使用——包括它本来就是【杀】的那一档（R4=A）。
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
    if isinstance(action, SubstituteJudgeCard):
        # 改判候选（feng-zhangjiao 票 02）：与 `PlayCard` 同档（先按牌 id），**必须**
        # 排在 `Pass` 之前——漏了这一支就落进兜底键 `(1, …)`，与 `Pass` 同档且
        # `"Pass" < "SubstituteJudgeCard"` ⇒ "放弃"跑到首位（与票 03 的活锁同一类）。
        return (0, (action.card.card_id,), action.kind)
    if isinstance(action, Reforge):
        # 重铸带牌实例但没有目标：与其它带牌的构造子同档（先按牌 id），仍排在
        # ``EndPhase``（兜底键）之前——"结束阶段排最后"的既有口径不破（M5-03）。
        return (0, (action.card.card_id,), action.kind)
    if isinstance(action, SelectCards):
        return (0, action.picks, action.kind)
    if isinstance(action, (SelectOption, SelectOptions)):
        # 选项 / 多选闭集里的**真候选**。``Pass``（可放弃时）没有分支、走兜底键
        # ``(1, …)`` ⇒ 恒排在真候选之后；两臂合一之前 ``SelectOptions`` 缺过这一支，
        # 让「放弃」排到首位（票 03 的活锁同一类；yijiang-2014 票 09 补齐）。
        # 键相等时保持调用方给出的次序（``seat:10`` 这类文本不宜按字面排序）。
        return (0, (), action.kind)
    if isinstance(action, ActivateSkill):
        # ``choices`` 也进键（票 04）：主动技同一组牌可以配不同目标座位，不带上它就会
        # 落进"键相等、顺序靠生成顺序"的隐式约定。既有候选两者皆空，故纪律扫描与
        # M2 基准的次序逐字节不变。
        return (0, action.picks, action.kind, action.choices)
    if isinstance(action, DiscardCards):
        return (0, tuple(card.card_id for card in action.cards), action.kind)
    if isinstance(action, Distribute):
        # 计数式分配（票 05）：键 = 计数元组本身 ⇒ 全 0 的 ``()`` 最小、恒排首位
        # （超时兜底 = 不分配）。同一询问里只有这一族，故族内定序即全局定序。
        return (0, action.counts, action.kind)
    if isinstance(action, ArrangeCards):
        # 排列候选只与排列候选同处一个询问，故键只需在族内定序（票 10）：显式写出
        # ``order`` / ``split``，不靠 `permutations` 的生成顺序当隐式约定。
        return (0, action.order, action.kind, action.split)
    return (1, (), action.kind)
