"""规则启发式参与者（M3 票 13；`04` 的规则启发式小节 / ADR-011）。

本模块只交出 `04` 的第二层（**规则启发式**），与既有的
:class:`~sgs.agents.participants.RandomPlayer` 并存、由命令行显式选择。
它只满足 :class:`~sgs.agents.participants.Participant` 的
``respond(query) -> Action``，**不引入第二套接口**。

结构 = **选路器**（按 :class:`~sgs.state.QueryKind` 分派）→ 每类一个
``scorer(query, action) -> float`` → 取最高分；**平分由 ``"ai"`` 子流打破**
（同种子逐字节可复现，不另造随机源）。

**硬约束**（票 13 §硬约束 4 / `04` 的 AI 工程约束）：只能读 :attr:`Query.view` 与
:attr:`Query.legal_actions`。本模块**不** import ``sgs.engine`` / ``sgs.skills`` /
``sgs.data`` / ``sgs.resolution`` / ``sgs.timing``，也不读 ``GameState``——"信息隐藏
不是约定"这条因此是可断言事实（静态扫描住 ``tests/test_source_discipline.py``）。
**不做身份推断、不建决策时限**（ADR-011）：拿不准时缺省保守，而不是猜身份。

**技能语义表** :data:`SKILL_VALUES`：键 = 技能 id（含 ``hero.`` 前缀），值 =
``(打分函数, 默认策略说明)``；打分函数签名统一 ``(query, action, view) -> float``
（`map.md` R4-Q6）。表里没有的技能**缺省 Pass**（"拿不准就不发动"）。双向同步纪律
（`03` 的「技能分类与过滤」：加武将忘了教 AI 必须立刻红）住 ``tests/test_heuristic_ai.py``：
① 启用的每名武将的每个**非锁定**技能都有条目；② 表里每个 id 都在数据文件里存在。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from sgs.effects import Randomness
from sgs.state import (
    Action,
    ActivateSkill,
    ArrangeCards,
    ArrangeQuery,
    CardRef,
    DiscardCards,
    DiscardQuery,
    DodgeQuery,
    EndPhase,
    Pass,
    PeachQuery,
    PlayCard,
    PlayPhaseQuery,
    PlayerPublic,
    PlayerView,
    Query,
    QueryKind,
    RespondQuery,
    SelectCards,
    SelectCardsQuery,
    SelectOption,
    SelectOptionQuery,
    SkillActivationQuery,
    UseCard,
)

__all__ = ["SKILL_VALUES", "HeuristicPlayer"]

# ---------------------------------------------------------------------------
# 打分要素：只取可从 ``query.view`` 与 action **局部算出**的量（票 13 §打分要素）
# ---------------------------------------------------------------------------

_DAMAGE_CARD_TYPES = frozenset({"slash", "duel", "barbarian_invasion", "arrow_barrage"})
"""能造成伤害的牌名：出牌阶段优先于装备与过牌。"

.. note:: ``frozenset`` 只作**成员判定**（``card.type in ...``），从不迭代——
   源码纪律禁止的是集合**迭代**（逐进程序变化），不是集合本身。
"""

_SLOT_VALUES: Mapping[str, float] = MappingProxyType(
    {
        "armor": 2.2,
        "weapon": 2.0,
        "horse_defense": 1.2,
        "horse_offense": 1.0,
        "treasure": 1.0,
    }
)
"""装备按**槽位**的价值序（票 13 §DISCARD「装备按槽位价值排序」）。"""

_TRICK_VALUES: Mapping[str, float] = MappingProxyType(
    {
        "ex_nihilo": 3.5,
        "snatch": 3.2,
        "indulgence": 3.2,
        "dismantlement": 3.0,
        "peach_garden": 2.5,
        "harvest": 2.0,
        "lightning": 1.5,
        "nullify": 0.0,
        "dodge": 0.0,
        "peach": 0.0,
    }
)
"""锦囊（含延时锦囊）的使用价值；【无懈可击】/【闪】不能在出牌阶段主动使用 ⇒ 0。"""

_CARD_VALUES: Mapping[str, float] = MappingProxyType(
    {
        "peach": 6.0,
        "dodge": 4.0,
        "nullify": 3.5,
        "ex_nihilo": 3.5,
        "snatch": 3.2,
        "indulgence": 3.2,
        "dismantlement": 3.0,
        "slash": 3.0,
        "duel": 2.5,
        "barbarian_invasion": 2.5,
        "arrow_barrage": 2.5,
        "harvest": 2.2,
        "peach_garden": 2.0,
        "borrowed_sword": 2.0,
        "lightning": 1.0,
    }
)
"""牌名价值表：**弃牌**与**排列**共用的"这张牌多有用"（票 13 §DISCARD / §ARRANGE）。"""

_UNKNOWN_CARD_VALUE = 1.0
_EQUIP_VALUE = 1.5
_HIDDEN_CARD_VALUE = 1.0
"""隐藏槽（只有位置、没有牌面）的估值：他人手牌按"一张普通的牌"计。"""

_DAMAGE_BASE = 3.0
_TARGET_BONUS = 1.0
_HAND_DEFENSE_WEIGHT = 0.25
_HAND_DEFENSE_CAP = 4
_SAVE_VALUE = 6.0
_CONSERVE_VALUE = 0.5
_HEAL_VALUE = 3.5
_EQUIP_PLAY_VALUE = 2.5
_DODGE_VALUE = 5.0
_DODGE_SKILL_VALUE = 4.5
_RESPOND_VALUE = 5.0
_ILLEGAL_PENALTY = -100.0
_UNKNOWN_SKILL_VALUE = -1.0
"""表里没有的技能：**低于 Pass**（0.0）⇒ 缺省保守，拿不准就不发动。"""
_ABORT_SKILL_PENALTY = -100.0
"""撤销一次**已经发动**的技能（发动后流水线里的「取消」）：比任何成本都差。

与 :data:`_ILLEGAL_PENALTY` 同值但**语义不同**（那条是"这条应答本身不合法"），
故各留一个常量，免得日后调一处误伤另一处。理由见 :func:`_is_committed_skill_step`。
"""
_PLAY_PHASE_IDLE = "PHASE_PLAY_IDLE"
"""时机标识的**字面量**（:class:`~sgs.timing.Timing` 是 ``str`` 枚举，故可直接比）。

为什么抄一个字面量而不是 import：本模块**不许碰全知面**，而
``tests/test_source_discipline.py`` 的可见面白名单把 ``sgs.timing`` 判红
（`HEURISTIC_ALLOWED_SGS_MODULES` 只有 ``sgs.effects`` 与 ``sgs.state``）。
"""

_LORD_SIDE = frozenset({"lord", "loyalist"})
_REBEL_SIDE = frozenset({"rebel", "renegade"})
_LORD = "lord"
_REBEL = "rebel"
_RENEGADE = "renegade"
"""身份的**字面量**（:class:`~sgs.data.Identity` 是 ``str`` 枚举，故可直接比）。

同 :data:`_PLAY_PHASE_IDLE` 的理由：``Identity`` 住在 ``sgs.data``，不在本模块的可见面
白名单里。``frozenset`` 只作成员判定、从不迭代（源码纪律禁止的是迭代）。
"""

_SEAT_TARGET_VALUE = 2.0
"""座位步里"已明身份的敌 / 友"的分值幅度（未知身份是中性 0）。"""

_ENEMY_FOCUS = 2.0
"""伤害目标里"已明身份的敌人"的加权：小到不盖过手牌防御贴现，只作同价时的偏向。"""


def _card_value(card: CardRef | None) -> float:
    """一张牌的局部估值：牌名表 → 装备槽位 → 未知兜底（不读任何隐藏信息）。"""
    if card is None:
        return _HIDDEN_CARD_VALUE
    known = _CARD_VALUES.get(card.type)
    if known is not None:
        return known
    if card.slot is not None:
        return _SLOT_VALUES.get(card.slot.value, _EQUIP_VALUE)
    return _UNKNOWN_CARD_VALUE


def _me(view: PlayerView) -> PlayerPublic:
    return view.players[view.seat]


def _wounded(view: PlayerView) -> bool:
    me = _me(view)
    return me.hp < me.hp_max


# ---------------------------------------------------------------------------
# 每类询问一个 scorer：``(query, action) -> float``
# ---------------------------------------------------------------------------


def _score_attack(action: Action, view: PlayerView) -> float:
    """伤害期望：目标非己方恒正；目标手牌越多，被【闪】抵消的概率越高（用可见张数贴现）。

    ``targets`` 只出现在 :class:`~sgs.state.UseCard` 上；``PlayCard``（打出）无目标，
    故按"无目标"读——打出伤害牌在出牌阶段本就不产生伤害。
    """
    targets = tuple(action.targets) if isinstance(action, UseCard) else ()
    if not targets:
        return _DAMAGE_BASE
    me = view.seat
    others = [
        seat
        for seat in targets
        if seat != me and 0 <= seat < len(view.players) and view.players[seat].alive
    ]
    if not others:
        return -_SAVE_VALUE
    score = _DAMAGE_BASE + _TARGET_BONUS * len(others)
    for seat in others:
        score -= _HAND_DEFENSE_WEIGHT * min(view.players[seat].hand_count, _HAND_DEFENSE_CAP)
        # 主公是公开信息：反贼据此**略**偏向锁定他，但不能盖过"手牌越多越难命中"的贴现。
        if _is_revealed_enemy(view, seat):
            score += _ENEMY_FOCUS
    return score


def _score_card_use(action: Action, view: PlayerView) -> float:
    """出牌阶段使用一张牌的局部收益（伤害 ＞ 装备 ＞ 过牌；【桃】只在受伤时用）。"""
    assert isinstance(action, (UseCard, PlayCard))
    card = action.card
    if card.type in _DAMAGE_CARD_TYPES:
        return _score_attack(action, view)
    if card.type == "peach":
        return _HEAL_VALUE if _wounded(view) else 0.0
    if card.slot is not None:
        return _EQUIP_PLAY_VALUE
    return _TRICK_VALUES.get(card.type, _UNKNOWN_CARD_VALUE)


def _score_play_phase(query: Query, action: Action) -> float:
    """``PLAY_PHASE``：能造成伤害且目标非己方 ＞ 装备 ＞ 过牌；技能查语义表。"""
    view = query.view
    if isinstance(action, EndPhase):
        return 0.0
    if isinstance(action, ActivateSkill):
        return _skill_value(query, action)
    if isinstance(action, (UseCard, PlayCard)):
        return _score_card_use(action, view)
    return 0.0


def _score_dodge(query: Query, action: Action) -> float:
    """``DODGE``：能抵消就抵消（响应牌不占使用次数）。"""
    if isinstance(action, Pass):
        return 0.0
    if isinstance(action, ActivateSkill):
        return _DODGE_SKILL_VALUE
    if isinstance(action, (UseCard, PlayCard)) and action.card.type == "dodge":
        return _DODGE_VALUE
    return 0.0


def _score_respond(query: Query, action: Action) -> float:
    """``RESPOND``：能抵消就抵消；技能类提供者（【护驾】/【激将】）同价。"""
    if isinstance(action, Pass):
        return 0.0
    if isinstance(action, ActivateSkill):
        return _DODGE_SKILL_VALUE
    if isinstance(action, (UseCard, PlayCard)):
        return _RESPOND_VALUE
    return 0.0


def _score_peach(query: Query, action: Action) -> float:
    """``PEACH``：救（桃用于救命几乎恒正收益），除非自己是濒死链的下一个。"""
    assert isinstance(query, PeachQuery)
    if isinstance(action, Pass):
        return 0.0
    if not isinstance(action, (UseCard, PlayCard)) or action.card.type != "peach":
        return 0.0
    if query.dying == query.view.seat:
        return _SAVE_VALUE
    me = _me(query.view)
    if me.hp <= 1 and me.hand_count <= 1:
        return _CONSERVE_VALUE
    if _is_revealed_enemy(query.view, query.dying):
        # 已明的敌人（主公 ↔ 反贼）：救活他等于替对手续命。
        return -_SAVE_VALUE
    return _SAVE_VALUE


def _score_discard(query: Query, action: Action) -> float:
    """``DISCARD``：弃价值最低（【桃】/【闪】最后弃，装备按槽位价值排序）。"""
    del query
    if not isinstance(action, DiscardCards):
        return 0.0
    return -sum(_card_value(card) for card in action.cards)


def _skill_value(query: Query, action: ActivateSkill) -> float:
    """技能收益查 :data:`SKILL_VALUES`；表里没有 ⇒ 保守（低于 Pass）。"""
    entry = SKILL_VALUES.get(action.skill_id)
    if entry is None:
        return _UNKNOWN_SKILL_VALUE
    scorer, _note = entry
    return scorer(query, action, query.view)


def _score_skill_activation(query: Query, action: Action) -> float:
    """``SKILL_ACTIVATION``：查技能语义表，缺省 ``Pass``（拿不准就不发动）。"""
    if isinstance(action, Pass) or not isinstance(action, ActivateSkill):
        return 0.0
    return _skill_value(query, action)


def _is_committed_skill_step(query: Query) -> bool:
    """这一步的「放弃」等于**撤销一次已经发动的技能**（而不是放弃一桩好事）。

    判据 = 出牌阶段空闲时机（``PHASE_PLAY_IDLE``）**且这一步可取消**。这个组合只由主动技
    发动后的步骤流水线产出（【离间】票 01）：玩家点了技能按钮就是"要发动"，中途放弃 =
    撤销；而引擎的"取消不消耗使用次数"意味着**技能会立刻重新可发动**。

    AI 若在这里挑「放弃」，就会"发动 → 放弃 → 又发动"地空转、永远走不完出牌阶段
    （32 种子基准组的 seed=107 就是这么自旋的）。故 AI 的纪律是：
    **既然选了发动，就得把成本付完**；要放弃就该在上一层（不点那个技能）放弃。
    """
    if getattr(query, "timing", None) != _PLAY_PHASE_IDLE:
        return False
    spec = getattr(query, "spec", None)
    if spec is not None:
        return bool(getattr(spec, "optional", False))
    # ``SELECT_OPTION`` 没有 ``spec``：此刻**候选里有 Pass** 本身就说明这一步可取消，
    # 而可取消的选项询问只有发动后流水线会发（取消 = 撤销一次已发动的技能）。
    return any(getattr(action, "kind", None) == "Pass" for action in query.legal_actions)


def _score_select_cards(query: Query, action: Action) -> float:
    """``SELECT_CARDS``：选**别人的**牌取高价值、选**自己的**牌（成本）取低价值。"""
    assert isinstance(query, SelectCardsQuery)
    if not isinstance(action, SelectCards):
        return _ABORT_SKILL_PENALTY if _is_committed_skill_step(query) else 0.0
    total = 0.0
    for index in action.picks:
        if not 0 <= index < len(query.slots):
            return _ILLEGAL_PENALTY
        slot = query.slots[index]
        value = _card_value(slot.card)
        total += value if slot.ref.seat != query.view.seat else -value
    return total


_OPTION_VALUES: Mapping[str, float] = MappingProxyType(
    {
        # 【雌雄双股剑】目标侧的两种损失：弃一张手牌 vs 令使用者摸一张。
        "discard_one": -1.0,
        "draw_one": -1.5,
    }
)


def _is_revealed_enemy(view: PlayerView, seat: int) -> bool:
    """``seat`` 是不是**已明身份**的敌人（只用公开信息）。

    身份只对本人 / 主公 / 已阵亡者可见（``PlayerPublic.identity``），故可靠的敌我信号
    就是"主公 ↔ 反贼/内奸"这一对；其余一律当作未知（不猜、不读隐藏面）。
    """
    mine = view.players[view.seat].identity
    theirs = view.players[seat].identity
    if mine is None or theirs is None:
        return False
    if mine == _RENEGADE:
        # 内奸要**最后一个人活着**：主公提前死 ⇒ 反贼直接赢，自己也输。故不把主公当
        # 优先敌人（其余都算对手）；这是内奸唯一的可靠策略信号。
        return theirs not in (_RENEGADE, _LORD)
    if mine == _REBEL:
        return theirs in _LORD_SIDE
    if mine in _LORD_SIDE:
        return theirs in _REBEL_SIDE
    return False


def _is_revealed_ally(view: PlayerView, seat: int) -> bool:
    """``seat`` 是不是**已明身份**的盟友（唯一的可靠情形：主公与我同在主公阵营）。"""
    mine = view.players[view.seat].identity
    theirs = view.players[seat].identity
    return mine in _LORD_SIDE and theirs == _LORD


_HOSTILE_SEAT_STEPS = ("lijian:", "fanjian:")
"""座位步的**语义分组**：这些步骤里的座位是"被指向的对手"，其余（治疗 / 给牌）是"被帮助的人"。

键 = 执行器发的 ``skill_id`` 前缀（``lijian:victim`` / ``fanjian:seat`` …）。不属于任何
一组的座位步按中性处理——宁可 0 分（交给随机流打破平分），也不猜错方向。
"""


def _score_seat_option(query: SelectOptionQuery, choice: str) -> float:
    """座位选项的敌我估值：帮助类避开敌人、决斗类瞄准敌人，``self`` 恒是好的帮助对象。

    票 03 起【结姻】/【青囊】/【离间】的座位改由步骤询问；若一律 0 分，AI 会在友敌之间
    掷硬币（候选次序修复后胜率掉到 50% 的正是这个短板）。
    """
    view = query.view
    try:
        seat = int(choice.removeprefix("seat:"))
    except ValueError:  # pragma: no cover - 座位步的选项恒是 seat:N
        return 0.0
    if not 0 <= seat < len(view.players):
        return 0.0
    enemy = _is_revealed_enemy(view, seat)
    skill = str(getattr(query, "skill_id", "") or "")
    if skill.startswith(_HOSTILE_SEAT_STEPS):
        return _SEAT_TARGET_VALUE if enemy else 0.0
    if seat == view.seat or _is_revealed_ally(view, seat):
        return _SEAT_TARGET_VALUE
    return -_SEAT_TARGET_VALUE if enemy else 0.0


def _score_select_option(query: Query, action: Action) -> float:
    """``SELECT_OPTION``：座位步按敌我估值、其余按特征表贪心；未知选项一律 0。

    「取消一次已发动的技能」（发动后流水线里的 ``Pass``）是例外：给它一个惩罚，
    否则 AI 会在座位步挑「取消」，再回头重新发动同一条技能 —— 空转（见
    :func:`_is_committed_skill_step`）。
    """
    assert isinstance(query, SelectOptionQuery)
    if not isinstance(action, SelectOption):
        return _ABORT_SKILL_PENALTY if _is_committed_skill_step(query) else 0.0
    if action.choice.startswith("seat:"):
        return _score_seat_option(query, action.choice)
    return _OPTION_VALUES.get(action.choice, 0.0)


def _score_arrange(query: Query, action: Action) -> float:
    """``ARRANGE``：最有用的牌留顶（按牌名价值表；越靠顶权重越高）。"""
    assert isinstance(query, ArrangeQuery)
    if not isinstance(action, ArrangeCards):
        return 0.0
    order = tuple(action.order)
    if sorted(order) != list(range(len(query.revealed))):
        return _ILLEGAL_PENALTY
    if not 0 <= action.split <= len(order):
        return _ILLEGAL_PENALTY
    size = len(order)
    return sum(
        _card_value(query.revealed[order[index]]) * (size - index)
        for index in range(action.split)
    )


_SCORERS: Mapping[QueryKind, Callable[[Query, Action], float]] = MappingProxyType(
    {
        QueryKind.PLAY_PHASE: _score_play_phase,
        QueryKind.DODGE: _score_dodge,
        QueryKind.RESPOND: _score_respond,
        QueryKind.PEACH: _score_peach,
        QueryKind.DISCARD: _score_discard,
        QueryKind.SKILL_ACTIVATION: _score_skill_activation,
        QueryKind.SELECT_CARDS: _score_select_cards,
        QueryKind.SELECT_OPTION: _score_select_option,
        QueryKind.ARRANGE: _score_arrange,
    }
)
"""选路表：**九类询问各一个 scorer**（键集恰等于 ``QueryKind``，由测试钉住）。"""


# ---------------------------------------------------------------------------
# 技能语义表：键 = 技能 id，值 = (打分函数, 默认策略说明)
# ---------------------------------------------------------------------------


def _constant(value: float) -> Callable[[Query, Action, PlayerView], float]:
    """常量收益的语义条目（大多数"发动即净赚"的技能用它）。"""

    def scorer(query: Query, action: Action, view: PlayerView) -> float:
        del query, action, view
        return value

    return scorer


def _picked_value(view: PlayerView, action: ActivateSkill) -> float:
    """``ActivateSkill.picks``（出牌阶段主动技的**实体牌 id**）的牌面价值之和。

    主动技的载荷在候选构造期就烘进 ``picks``（票 04）：制衡 / 贯石斧一类可跨**手牌与
    装备区**，故照 ``view.self_hand`` ∪ 自己装备区查牌面；查不到（不该发生）按未知牌
    估值，不读任何隐藏信息。
    """
    mine = view.players[view.seat]
    by_id = {card.card_id: card for card in (*view.self_hand, *mine.equip)}
    return sum(_card_value(by_id.get(card_id)) for card_id in action.picks)


def _skeleton_cost(view: PlayerView, count: int) -> float:
    """**骨架候选**的成本预估：发动后那一步会让 AI 挑最便宜的成本张。

    主动技迁到步骤流水线后（票 02 起），候选不再烘 ``picks``（付不付得起改判在步骤里），
    于是 ``_picked_value`` 恒 0、技能看起来免费。``_score_select_cards`` 之后会挑自己
    价值最低的牌当成本，故这里按"手里最便宜的 ``count`` 张"预估，让发动决策重新看得见成本。
    """
    values = sorted(_card_value(card) for card in view.self_hand)
    return sum(values[:count])


def _skill_cost(view: PlayerView, action: ActivateSkill, skeleton: int) -> float:
    """技能成本：候选带 ``picks`` ⇒ 实际牌价；骨架候选 ⇒ 按 ``skeleton`` 张预估。"""
    if action.picks:
        return _picked_value(view, action)
    return _skeleton_cost(view, skeleton)


def _score_rende(query: Query, action: Action, view: PlayerView) -> float:
    """【仁德】：给牌是成本；首次给到两张以上回复 1 点（只在受伤时值得）。"""
    assert isinstance(action, ActivateSkill)
    bonus = 4.0 if len(action.picks) >= 2 and _wounded(view) else 0.0
    return bonus - _picked_value(view, action)


def _score_zhiheng(query: Query, action: Action, view: PlayerView) -> float:
    """【制衡】：弃任意张摸等量——净牌数不变、换质量；弃【桃】/【闪】按价值扣分。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 1.5 * len(action.picks) - _picked_value(view, action)


def _score_qingnang(query: Query, action: Action, view: PlayerView) -> float:
    """【青囊】：弃一张手牌令**已受伤**角色回复 1 点（= 3 点收益 − 牌价）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 3.0 - _picked_value(view, action)


def _score_fanjian(query: Query, action: Action, view: PlayerView) -> float:
    """【反间】：交付一张牌，目标猜错花色则受伤（期望为正）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 2.5 - _picked_value(view, action)


def _score_lijian(query: Query, action: Action, view: PlayerView) -> float:
    """【离间】：弃一张牌令两名男性角色决斗（把伤害转给别人）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 3.0 - _picked_value(view, action)


def _score_jieyin(query: Query, action: Action, view: PlayerView) -> float:
    """【结姻】：弃两张手牌，双方各回复 1 点（自己回血是主要收益）。

    票 03 起候选是**骨架**（``picks`` 为空，成本改由发动后的 ``SELECT_CARDS`` 挑）⇒
    按"手里最便宜的两张"预估成本，否则裸候选看起来免费、AI 无脑发动（胜率回归）。
    自己没受伤时收益只剩"替目标回血"（目标还可能是敌人），故大幅降值。
    """
    assert isinstance(action, ActivateSkill)
    del query
    bonus = 4.0 if _wounded(view) else 1.0
    return bonus - _skill_cost(view, action, 2)


def _score_liuli(query: Query, action: Action, view: PlayerView) -> float:
    """【流离】：弃一张牌把【杀】转移给合法新目标（候选存在即意味着有目标）。"""
    assert isinstance(action, ActivateSkill)
    del query
    return 3.0 - 0.5 * _picked_value(view, action)


SKILL_VALUES: dict[str, tuple[Callable[[Query, Action, PlayerView], float], str]] = {
    # 魏
    "hero.caocao.jianxiong": (_constant(4.0), "受伤后获得造成伤害的牌：净赚一张，恒发动"),
    "hero.caocao.hujia": (
        _constant(_DODGE_SKILL_VALUE),
        "主公技：视为打出一张【闪】，响应窗口里能抵消就抵消",
    ),
    "hero.simayi.fankui": (_constant(3.5), "受伤后获得来源一张牌：净赚一张，恒发动"),
    "hero.simayi.guicai": (_constant(1.0), "改判窗口：有改判能力即参与（保守给正但不高）"),
    "hero.guojia.tiandu": (_constant(2.0), "获得此判定牌：额外一张手牌"),
    "hero.guojia.yiji": (_constant(4.5), "受伤后摸两张并可分配：净赚两张"),
    # 蜀
    "hero.liubei.rende": (_score_rende, "给牌是成本；首次给到两张以上回复 1 点 ⇒ 受伤时值得"),
    "hero.liubei.jijiang": (_constant(_RESPOND_VALUE), "主公技：视为使用/打出一张【杀】"),
    "hero.guanyu.wusheng": (_constant(2.0), "红色牌当【杀】：多一条进攻路径，按牌价保守为正"),
    "hero.zhaoyun.longdan": (_constant(2.0), "【杀】⇄【闪】互转：攻防两便"),
    "hero.machao.tieqi": (_constant(2.5), "使用【杀】后判定，通过则目标不可响应：助此【杀】命中"),
    "hero.zhugeliang.guanxing": (_constant(3.0), "准备阶段观看牌堆顶并按任意顺序放回：改善摸牌"),
    # 吴
    "hero.sunquan.zhiheng": (_score_zhiheng, "弃任意张摸等量：换牌质量（弃【桃】/【闪】扣分）"),
    "hero.zhouyu.yingzi": (_constant(2.5), "摸牌阶段多摸一张：净赚一张"),
    "hero.zhouyu.fanjian": (_score_fanjian, "交付一张牌令目标猜花色：猜错即受伤"),
    "hero.daqiao.guose": (_constant(2.0), "方块牌当【乐不思蜀】：多一条控制路径"),
    "hero.daqiao.liuli": (_score_liuli, "弃一张牌转移【杀】的目标：避免自己受击"),
    "hero.luxun.lianying": (_constant(3.0), "失去最后一张手牌后摸一张：补牌"),
    "hero.sunshangxiang.jieyin": (_score_jieyin, "弃两张手牌令双方各回复 1 点：自己回血"),
    "hero.sunshangxiang.xiaoji": (_constant(3.0), "失去装备后摸两张：净赚两张"),
    # 群
    "hero.huatuo.jijiu": (_constant(2.0), "红色牌当【桃】（限回合外）：多一条救命路径"),
    "hero.huatuo.qingnang": (_score_qingnang, "弃一张手牌令已受伤角色回复 1 点"),
    "hero.diaochan.lijian": (_score_lijian, "弃一张牌令两名男性决斗：伤害转嫁"),
    "hero.diaochan.biyue": (_constant(2.5), "结束阶段摸一张：净赚一张"),
}
"""技能 id → ``(打分函数, 默认策略说明)``（`map.md` R4-Q6 的统一签名）。

**只装非锁定武将技能**：锁定技没有 ``SKILL_ACTIVATION`` 询问（派发器直接跑），
故不要求条目；装备技能同理不在此表（缺省保守 ``Pass``）。两个方向的同步断言
（"启用的非锁定技能都在" + "表里每个 id 都在数据文件里"）住
``tests/test_heuristic_ai.py``——加武将忘了教 AI 会立刻判红。
"""


# ---------------------------------------------------------------------------
# 参与者
# ---------------------------------------------------------------------------


class HeuristicPlayer:
    """规则启发式参与者：打分取最高，平分由 ``"ai"`` 子流打破（票 13 §结构）。

    随机面只走调用方交进来的 :class:`~sgs.effects.Randomness`（唯一随机源的 ``ai``
    子流），故同种子逐字节可复现、跨 ``PYTHONHASHSEED`` 稳定，且**不另造随机源**。
    """

    def __init__(self, rng: Randomness) -> None:
        self._rng = rng

    def respond(self, query: Query) -> Action:
        actions: Sequence[Action] = query.legal_actions
        if not actions:
            raise ValueError(f"{query.kind} 询问没有候选行动，参与者无法应答")
        scorer = _SCORERS[QueryKind(query.kind)]
        scores = [scorer(query, action) for action in actions]
        best = max(scores)
        winners = [action for score, action in zip(scores, actions) if score == best]
        return winners[self._rng.randbelow(len(winners))]
