"""规则启发式参与者（M3 票 13；`04` 的规则启发式小节 / ADR-011）。

本模块只交出 `04` 的第二层（**规则启发式**），与既有的
:class:`~sgs.agents.participants.RandomPlayer` 并存、由命令行显式选择。
它只满足 :class:`~sgs.agents.participants.Participant` 的
``respond(query) -> Action``，**不引入第二套接口**。

结构 = **选路器**（按 :class:`~sgs.state.QueryKind` 分派）→ 每类一个
``scorer(query, action) -> float`` → 取最高分；**平分由 ``"ai"`` 子流打破**
（同种子逐字节可复现，不另造随机源）。

**硬约束**（票 13 §硬约束 4 / `04` 的 AI 工程约束）：只读**询问自带的面**
（:attr:`Query.view`、:attr:`Query.legal_actions` 与询问自己的派生态：
``skill_id`` / ``spec`` / ``slots`` / ``preactivations`` / ``preactivation``）。本模块
**不** import ``sgs.engine`` / ``sgs.skills`` / ``sgs.data`` / ``sgs.resolution`` /
``sgs.timing``，也不读 ``GameState``——"信息隐藏不是约定"这条因此是可断言事实
（静态扫描住 ``tests/test_source_discipline.py``）。
**不做身份推断、不建决策时限**（ADR-011）：拿不准时缺省保守，而不是猜身份。

**预发动填表**（W4-4）：预发动后复合动作**不在** ``legal_actions`` 里，AI 读询问随附的
清单（主动 ``preactivations`` / 被动 ``preactivation``），按**现有打分器**填一个
``ActivateSkill(picks, choices)``：代价取 :func:`_card_value` 最低、目标取
:func:`_score_seat_option` 最高、转化源取旧 :func:`_score_select_cards` 会挑的那张；
提交后由引擎的**同一个校验器**（``sgs.skills.validate_activation``）放行。
**转化技也走同一条路**（W4-fix-2）：候选已从 ``legal_actions`` 收回，AI 读
``transform_preactivations`` 填 ``ActivateSkill(来源, 目标)``，由引擎按同一份内部候选池
解析成普通使用 / 打出（`sgs.skills.resolve_conversion_confirmation`）。

**技能语义表** :data:`SKILL_VALUES`：键 = 技能 id（含 ``hero.`` / ``equip.`` 前缀），值 =
``(打分函数, 默认策略说明)``；打分函数签名统一 ``(query, action, view) -> float``
（`map.md` R4-Q6）。表里没有的技能**缺省 Pass**（"拿不准就不发动"）。三向同步纪律
（`03` 的「技能分类与过滤」：加武将 / 装备忘了教 AI 必须立刻红）住
``tests/test_heuristic_ai.py``：① 启用的每名武将的每个**非锁定**技能都有条目；
② 会问"是否发动"的每条非锁定装备技都有条目；③ 表里每个 id 都在数据文件里存在。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType

from sgs.effects import Randomness, target_sets_match
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
_CYCLE_VALUE = 1.5
"""「弃一张换一张」的每张期望增益（【制衡】的骨架候选与它的选牌步共用同一估值）。

牌面价值低于它的牌才值得换掉；两处共用一个常量，保证"发不发动"与"弃哪几张"的
模型一致（否则发动决策与随后的选牌步会各说各话）。
"""
_RENDE_RECOVER_VALUE = 4.0
"""【仁德】本阶段首次给到 ≥2 张时那 1 点体力对 AI 的价值。

发动决策（:func:`_score_rende`）与选牌步（:func:`_score_select_cards` 的给牌分支）共用，
保证"给 2 张还是 1 张"两处算的是同一笔账。
"""
_PLAY_PHASE_IDLE = "PHASE_PLAY_IDLE"
"""时机标识的**字面量**（:class:`~sgs.timing.Timing` 是 ``str`` 枚举，故可直接比）。

为什么抄一个字面量而不是 import：本模块**不许碰全知面**，而
``tests/test_source_discipline.py`` 的可见面白名单把 ``sgs.timing`` 判红
（`HEURISTIC_ALLOWED_SGS_MODULES` 只有 ``sgs.effects`` 与 ``sgs.state``）。
"""

_SEAT_PREFIX = "seat:"
"""座位选项的规范文本前缀；**规范声明** = `sgs.skills.flow.SEAT_PREFIX`（本处是被边界允许的副本）。

为什么抄一个**字面量**而不 import：``sgs.skills`` 不在
``tests/test_source_discipline.py`` 的可见面白名单里（`HEURISTIC_ALLOWED_SGS_MODULES`
只有 ``sgs.effects`` / ``sgs.state``），本模块连公开入口都不许碰。改前缀时必须同步
`flow.SEAT_PREFIX`、本副本与 ``tools/sgs_web/static/app.js`` 的跨语言副本（W6-06b）；
边界不是"全场单一真源"，而是"接入层导规范值 + 两个被边界允许的带注释副本"。
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


def _peach_save_value(query: PeachQuery) -> float:
    """在求桃窗口里救 `query.dying` 的价值（真【桃】与急救的转化【桃】共用）。"""
    if query.dying == query.view.seat:
        return _SAVE_VALUE
    me = _me(query.view)
    if me.hp <= 1 and me.hand_count <= 1:
        return _CONSERVE_VALUE
    if _is_revealed_enemy(query.view, query.dying):
        # 已明的敌人（主公 ↔ 反贼）：救活他等于替对手续命。
        return -_SAVE_VALUE
    return _SAVE_VALUE


def _score_peach(query: Query, action: Action) -> float:
    """``PEACH``：救（桃用于救命几乎恒正收益），除非自己是濒死链的下一个。

    W4-fix-2：急救的转化【桃】不再是候选，AI 从清单填出的 ``ActivateSkill`` 与真【桃】
    按同一个救命价值打分——否则它会是 0 分、与「放弃」打平。
    """
    assert isinstance(query, PeachQuery)
    if isinstance(action, Pass):
        return 0.0
    if isinstance(action, ActivateSkill):
        return _peach_save_value(query)
    if not isinstance(action, (UseCard, PlayCard)) or action.card.type != "peach":
        return 0.0
    return _peach_save_value(query)


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


_GIVE_STEPS = frozenset({"rende"})
"""把**自己的牌交出去**的技能短 id（选牌步的成本语义与"弃一张换一张"完全不同）。

【仁德】给牌是纯成本（没有回抽），但给到 ≥2 张且自己受伤时换回 1 点体力
（:data:`_RENDE_RECOVER_VALUE`）。判据读询问自带的 ``skill_id``（票 05 加的本地通道，
不进记录流）——**不能只看 ``total`` 的 ``lo != hi``**：【制衡】与【仁德】都是
``(1, n)``，只看形状会把给牌误当成换牌（票 04 的 Answer 点名过这个坑）。
"""

_CYCLE_STEPS = frozenset({"zhiheng"})
"""每弃一张就回抽一张的技能短 id（【制衡】）：按 :data:`_CYCLE_VALUE` 逐张估值。"""


def _step_skill_short(query: Query) -> str:
    """这一步属于哪条技能（询问自带 ``skill_id`` 的短 id）；不是技能步 ⇒ 空串。"""
    raw = getattr(query, "skill_id", None)
    return str(raw).rsplit(".", 1)[-1] if raw else ""


def _step_card_score(card: CardRef | None, owner: int, me: int, short: str) -> float:
    """一步选牌的**单张**分值（``_score_select_cards`` 与预发动填表共用的唯一口径）。

    - **别人的牌**（【麒麟弓】弃目标坐骑 / 【反间】挑发动者的牌）：取高价值 ⇒ ``+牌价``；
    - **自己的牌（成本）**：按技能语义取负——【制衡】每弃一张回抽一张，记
      ``_CYCLE_VALUE - 牌价``；其余（含【仁德】的给牌）记 ``-牌价``。

    两处读它（选牌步的打分、预发动填表的"选哪几张"），故两份模型不会漂移。
    """
    value = _card_value(card)
    if owner != me:
        return value
    if short in _CYCLE_STEPS:
        return _CYCLE_VALUE - value
    return -value


def _score_select_cards(query: Query, action: Action) -> float:
    """``SELECT_CARDS``：选**别人的**牌取高价值、选**自己的**牌（成本）取低价值。

    两种"任意张自己的牌"各有各的成本语义（按询问自带的技能 id 分派，不看 ``total`` 形状）：

    - 【制衡】:data:`_CYCLE_STEPS`：每弃一张回抽一张 ⇒ 逐张按 ``_CYCLE_VALUE - 牌价`` 估值；
    - 【仁德】:data:`_GIVE_STEPS`：给出去没有回抽，但给到 ≥2 张且自己受伤换回 1 点体力 ⇒
      按 ``-牌价`` 估值并给"≥2 张"补上 :data:`_RENDE_RECOVER_VALUE`。

    单张口径的唯一实现是 :func:`_step_card_score`——W4 评审处置 D.7 起，预发动填表的
    "选哪几张"也读它（不再另写一套）。
    """
    assert isinstance(query, SelectCardsQuery)
    if not isinstance(action, SelectCards):
        return _ABORT_SKILL_PENALTY if _is_committed_skill_step(query) else 0.0
    committed = _is_committed_skill_step(query)
    short = _step_skill_short(query) if committed else ""
    total = 0.0
    for index in action.picks:
        if not 0 <= index < len(query.slots):
            return _ILLEGAL_PENALTY
        slot = query.slots[index]
        total += _step_card_score(slot.card, slot.ref.seat, query.view.seat, short)
    if short in _GIVE_STEPS and len(action.picks) >= 2 and _wounded(query.view):
        total += _RENDE_RECOVER_VALUE
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


_HOSTILE_SEAT_STEPS = (
    "lijian:",
    "fanjian:",
    "liuli:",
)
"""座位步的**语义分组**：这些步骤里的座位是"被指向的对手"，其余（治疗 / 给牌）是"被帮助的人"。

键 = 执行器发的 ``skill_id`` 前缀（``lijian:victim`` / ``fanjian:seat`` / ``liuli:seat`` …）。
【流离】（票 08 起走 ``PickSeats``）的座位是"把【杀】转嫁给谁"，属被指向的对手——**必须**
登记在这里：不登记就落进"帮助类"，AI 会系统性地把【杀】转给盟友（迁移前是均匀分值的
隐藏槽，至少是随机挑），比随机还差。

**转化技的目标步不在这里手抄**（W4-fix-6，D.1）：它们由**清单来源**派生——询问把
``transform_preactivations`` 的键交给 :func:`_fill_activation`（``conversion=True``），
而清单本身由技能声明渲染。于是第五条转化技自动落进"被指向的对手"分支，不会静默变成
"帮盟友"（旧实现用四条字面 id：``wusheng`` / ``longdan`` / ``guose`` / ``serpent_spear``）。
"""


def _score_seat_option(
    query: Query,
    choice: str,
    *,
    skill_id: str | None = None,
    conversion: bool = False,
) -> float:
    """座位选项的敌我估值：帮助类避开敌人、决斗类瞄准敌人，``self`` 恒是好的帮助对象。

    票 03 起【结姻】/【青囊】/【离间】的座位改由步骤询问；若一律 0 分，AI 会在友敌之间
    掷硬币（候选次序修复后胜率掉到 50% 的正是这个短板）。

    ``skill_id``（W4-4）：预发动填表时答案不在 ``SELECT_OPTION`` 询问上（复合动作一次
    提交），故由调用方把"这一步属于哪条技能"（``<短 id>:<步骤键>``）显式传进来；逐步
    应答仍缺省读询问自带的 ``skill_id``。

    ``conversion``（W4-fix-6，D.1）：这一步来自**转化技清单**（``transform_preactivations``）
    ⇒ 座位是转化牌的目标（【杀】/【乐不思蜀】都指向对手）。判据由**清单来源**派生，
    不手抄技能 id，第五条转化技因此不会再静默落进"帮盟友"分支。
    """
    view = query.view
    try:
        seat = int(choice.removeprefix(_SEAT_PREFIX))
    except ValueError:  # pragma: no cover - 座位步的选项恒是 seat:N
        return 0.0
    if not 0 <= seat < len(view.players):
        return 0.0
    enemy = _is_revealed_enemy(view, seat)
    skill = (
        str(skill_id)
        if skill_id is not None
        else str(getattr(query, "skill_id", "") or "")
    )
    if conversion or skill.startswith(_HOSTILE_SEAT_STEPS):
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
    if action.choice.startswith(_SEAT_PREFIX):
        return _score_seat_option(query, action.choice)
    return _OPTION_VALUES.get(action.choice, 0.0)


# ---------------------------------------------------------------------------
# 预发动填表（W4-4）：读清单、复用现有打分器填一个复合动作
# ---------------------------------------------------------------------------


def _visible_cards(view: PlayerView) -> dict[int, tuple[CardRef, int]]:
    """本视角里**看得到牌面**的实体牌 → ``(牌面, 归属座位)``。

    自己的手牌 ＋ 全部座位的装备 / 判定区（后两者是公开信息，票 08 §6）。清单下发的
    选牌候选只可能落在这些牌上——他人手牌那一步在清单里**退化**（不下发候选），故这里
    也不会读到隐藏牌面。
    """
    out: dict[int, tuple[CardRef, int]] = {
        card.card_id: (card, view.seat) for card in view.self_hand
    }
    for player in view.players:
        for card in (*player.equip, *player.judge):
            out.setdefault(card.card_id, (card, player.seat))
    return out


def _pool_row_for(step, prior: tuple[str, ...]):
    """找出与前序答案文本匹配的那一行子池；``when=()`` 行 = 池与前序无关。

    ``step`` 是 ``sgs.state.Step``（清单的一步）。本模块的可见面白名单**不许 import**
    它的类型（``Step`` / ``StepPool`` 不在 ``HEURISTIC_IMPORT_ALLOWLIST`` 里），故这里
    按询问自带的形状鸭子类型读——数据本身由询问下发，不是全知面。
    """
    rows = step.pool
    if not rows:
        return None
    for row in rows:
        if tuple(row.when) == prior:
            return row
    for row in rows:
        if not row.when:
            return row
    return None


def _model_card_picks(
    entries: list[tuple[int, float]],
    step,
    short: str,
    view: PlayerView,
) -> tuple[int, ...] | None:
    """按 :func:`_step_card_score` 的口径选出本步的实体 id（**同一个**打分模型）。

    打分器对一组 picks 的分数 = 各张单张分值之和（【仁德】另给 ≥2 张的回复奖励），
    故"最优子集"= 按单张分值降序取前 ``count`` 张，再在 ``min..max`` 内取总和最高的
    ``count``；同分取更小的 ``count``（多加一张零分牌不改变模型的选择）。
    """
    lower = max(0, step.min)
    upper = len(entries) if step.max is None else min(step.max, len(entries))
    if len(entries) < lower or upper < lower:
        return None
    ordered = sorted(entries, key=lambda item: (-item[1], item[0]))
    give = short in _GIVE_STEPS
    best_score = 0.0
    best_count = lower
    for count in range(lower, upper + 1):
        score = sum(value for _card_id, value in ordered[:count])
        if give and count >= 2 and _wounded(view):
            score += _RENDE_RECOVER_VALUE
        if count == lower or score > best_score:
            best_score = score
            best_count = count
    return tuple(card_id for card_id, _value in ordered[:best_count])


def _pick_card_ids(
    step,
    options: tuple[str, ...],
    cards: Mapping[int, tuple[CardRef, int]],
    view: PlayerView,
    short: str,
) -> tuple[int, ...] | None:
    """把**模型选出的实体 id** 映回清单的选项文本（``None`` = 填不出来）。

    "选哪几张"不再是这里另写的一套：单张口径读 :func:`_step_card_score`，
    子集选择读 :func:`_model_card_picks`——与 ``_score_select_cards`` 逐字同一模型
    （W4 评审处置 D.7；原先按技能分支手写的三档已删除）。
    """
    entries: list[tuple[int, float]] = []
    for text in options:
        try:
            card_id = int(text)
        except (TypeError, ValueError):
            return None
        card, owner = cards.get(card_id, (None, view.seat))
        entries.append((card_id, _step_card_score(card, owner, view.seat, short)))
    if not entries:
        return None
    return _model_card_picks(entries, step, short, view)


def _pick_options(
    step,
    options: tuple[str, ...],
    query: Query,
    short: str,
    *,
    conversion: bool = False,
) -> tuple[str, ...] | None:
    """座位 / 选项步：取 :func:`_score_seat_option` 最高（座位）或特征表最高（选项）。

    **多选座位步**（W7-01b：`step.max` 是整数且 `> step.min`，如转化【杀】的 `1..max`
    目标步）按 ``min(step.max, len(options))`` 填满；**首项必取**——多目标清单的首项是
    候选排序的自然结果（借刀窗口就是 Bn），不选它拼出的组合必然被引擎拒绝；其余按
    :func:`_score_seat_option` 降序补（同分保持清单顺序，与既有单选的 tie-break 一致）。
    ``max is None``、``min == max`` 的既有步与**非座位步**（选项步）一律走旧填法，
    故既有 47 份金录像的动作序列不变。
    """
    seat_options = bool(options) and all(
        text.startswith(_SEAT_PREFIX) for text in options
    )
    if (
        seat_options
        and isinstance(step.max, int)
        and step.max > step.min
    ):
        wanted = min(step.max, len(options))
        if wanted < step.min:
            return None
        first = options[0]
        rest = sorted(
            options[1:],
            key=lambda text: -_score_seat_option(
                query,
                text,
                skill_id=f"{short}:{step.name}",
                conversion=conversion,
            ),
        )
        return (first, *rest[: max(0, wanted - 1)])
    remaining = list(options)
    wanted = max(step.min, 1) if step.min else 1
    if step.max is not None:
        wanted = min(wanted, step.max)
    wanted = min(wanted, len(remaining))
    if wanted < step.min:
        return None
    chosen: list[str] = []
    for _ in range(wanted):
        best: str | None = None
        best_score = 0.0
        for text in remaining:
            score = (
                _score_seat_option(
                    query,
                    text,
                    skill_id=f"{short}:{step.name}",
                    conversion=conversion,
                )
                if text.startswith(_SEAT_PREFIX)
                else _OPTION_VALUES.get(text, 0.0)
            )
            if best is None or score > best_score:
                best, best_score = text, score
        assert best is not None
        remaining.remove(best)
        chosen.append(best)
    return tuple(chosen)


def _conversion_choices_within_pool(
    query: Query,
    picks: Sequence[int],
    choices: Sequence[str],
    *,
    step: object,
    options: Sequence[str],
) -> tuple[str, ...] | None:
    """把转化清单填出的目标集收进**内部候选池**（W7-02 发现 / 01b 修复）。

    一个转化技的**来源组可以有不同目标数上限**：方天画戟只对"最后一张手牌"的转化源 +2，
    装备区来源（武圣把已装备的红牌当【杀】）不追加——而清单目标步的 ``max`` 是**全局**
    最大值。按全局 ``max`` 填满、却用了上限更低的来源，拼出的组合就不在候选池里：引擎的
    确认会拒绝它，而 AI 没有重试的机会（那会是一条非法应答）。故提交前做一次**只读的池
    成员校验**：

    1. 命中内部池（``query.transform_actions``，与清单同源的候选）⇒ 原样返回；
    2. 否则把目标集缩到该来源在池里**真实存在的最多张**（保留清单首项 / 借刀的 Bn，且不
       少于 ``step.min``）；
    3. 池里没有可用组合 ⇒ ``None``（不交半成品，回退到候选 / 骨架路径）。

    它只比"在不在池里"，**不重算任何规则**；上限的真源只能是候选池本身（清单的逐来源
    ``pool`` 行只表达"该来源能选哪些目标"，协议里没有"每个来源最多几张"这一格）。判定
    与 :func:`sgs.agents.participants.is_transform_use` 同姿态：成员校验读的是询问自带的
    公开候选面，引擎的确认仍是最终裁判。
    """
    pool = tuple(getattr(query, "transform_actions", ()) or ())
    if not pool:
        return tuple(choices)
    seat_choices = tuple(str(text) for text in choices if str(text).startswith(_SEAT_PREFIX))
    if not seat_choices:
        return tuple(choices)
    wanted_sources = tuple(sorted(int(card_id) for card_id in picks))
    prefix = tuple(choices[: len(choices) - len(seat_choices)])

    def targets_of(action: object) -> tuple[int, ...]:
        return tuple(int(target) for target in (getattr(action, "targets", None) or ()))

    def sources_of(action: object) -> tuple[int, ...]:
        card = getattr(action, "card", None)
        return tuple(
            sorted(int(source) for source in (getattr(card, "virtual_sources", None) or ()))
        )

    def matches(chosen: Sequence[str], targets: Sequence[int]) -> bool:
        # 与确认解析器**同一个**比较（D5）：重复选择被拒、与点击顺序无关。
        return target_sets_match(
            (int(text.removeprefix(_SEAT_PREFIX)) for text in chosen), targets
        )

    available = [
        targets_of(action) for action in pool if sources_of(action) == wanted_sources
    ]
    if not available:
        return None
    if any(matches(seat_choices, targets) for targets in available):
        return tuple(choices)

    order = {str(text): index for index, text in enumerate(options)}
    shrinkable = [
        targets
        for targets in available
        if step.min <= len(targets) <= len(seat_choices)
        and all(f"{_SEAT_PREFIX}{target}" in order for target in targets)
    ]
    shrinkable.sort(
        key=lambda targets: (
            -len(targets),
            min(order[f"{_SEAT_PREFIX}{target}"] for target in targets),
        )
    )
    for targets in shrinkable:
        shrunk = tuple(
            text
            for text in options
            if int(str(text).removeprefix(_SEAT_PREFIX)) in set(targets)
        )
        if matches(shrunk, targets):
            return (*prefix, *shrunk)
    return None


def _fill_activation(
    query: Query,
    skill_id: str,
    steps: tuple[object, ...],
    cards: Mapping[int, tuple[CardRef, int]],
    *,
    conversion: bool = False,
) -> ActivateSkill | None:
    """按清单填一个复合动作；任何一步填不出来 ⇒ ``None``（不提交半成品）。

    ``conversion``（W4-fix-6，D.1）：这份清单来自 ``transform_preactivations`` ⇒ 座位步的
    目标是转化牌指向的对手，由 :func:`_score_seat_option` 的对敌分支估值。

    **多选目标步 + 逐来源上限**（W7-02）：``conversion`` 清单里出现 ``max > min`` 的座位步
    时，填出的目标集还要过 :func:`_conversion_choices_within_pool`——来源组上限不同（方天
    只放宽"最后一张手牌"的来源）时，按全局 ``max`` 填满可能越界；越界即缩到池里真实的
    最多张，仍填不出 ⇒ ``None``。
    """
    picks: list[int] = []
    choices: list[str] = []
    prior: list[str] = []
    short = str(skill_id).rsplit(".", 1)[-1]
    multi_target: tuple[object, tuple[str, ...]] | None = None
    for step in sorted(steps, key=lambda item: item.order):
        row = _pool_row_for(step, tuple(prior))
        if row is None:
            return None
        options = tuple(row.options)
        if not options:
            return None
        if all(text.startswith(_SEAT_PREFIX) for text in options):
            chosen = _pick_options(
                step, options, query, short, conversion=conversion
            )
            if chosen is None:
                return None
            if isinstance(step.max, int) and step.max > step.min:
                multi_target = (step, options)
            choices.extend(chosen)
            prior.extend(chosen)
        elif all(text.isdigit() for text in options):
            picked = _pick_card_ids(step, options, cards, query.view, short)
            if picked is None:
                return None
            picks.extend(picked)
            prior.extend(str(card_id) for card_id in picked)
        else:
            chosen = _pick_options(
                step, options, query, short, conversion=conversion
            )
            if chosen is None:
                return None
            choices.extend(chosen)
            prior.extend(chosen)
    if conversion and multi_target is not None:
        step, options = multi_target
        shrunk = _conversion_choices_within_pool(
            query, picks, choices, step=step, options=options
        )
        if shrunk is None:
            return None
        choices = list(shrunk)
    return ActivateSkill(
        skill_id=skill_id,
        picks=tuple(picks),
        choices=tuple(choices),
        answers_seq=query.seq,
    )


def _preactivation_candidates(query: Query) -> tuple[ActivateSkill, ...]:
    """本次询问可填的全部复合动作（主动读 ``preactivations``、被动读 ``preactivation``）。

    W4-fix-2：**转化技也在这里**——它们的候选已从 ``legal_actions`` 收回（用户裁定 A），
    清单住 ``transform_preactivations``，确认同样是 ``ActivateSkill(来源, 目标)``，由引擎
    按同一份内部候选池解析成普通使用 / 打出。四类语境（出牌 / 【闪】/ 响应 / 求桃）都读它。

    W4-fix-6（D.1）：转化清单的**来源**也是"这些座位步指向对手"的声明——填表时把它传给
    :func:`_fill_activation`（``conversion=True``），不再从技能 id 字面表判断。
    """
    kind = QueryKind(query.kind)
    transforms: dict[str, object] = {}
    if kind is QueryKind.PLAY_PHASE:
        manifests = dict(getattr(query, "preactivations", None) or {})
        transforms = dict(getattr(query, "transform_preactivations", None) or {})
        manifests.update(transforms)
    elif kind is QueryKind.SKILL_ACTIVATION:
        skill_id = getattr(query, "skill_id", None)
        steps = tuple(getattr(query, "preactivation", None) or ())
        manifests = {str(skill_id): steps} if skill_id and steps else {}
    elif kind in (QueryKind.DODGE, QueryKind.RESPOND, QueryKind.PEACH):
        transforms = dict(getattr(query, "transform_preactivations", None) or {})
        manifests = transforms
    else:
        return ()
    if not manifests:
        return ()
    cards = _visible_cards(query.view)
    conversion_skills = frozenset(transforms)
    return tuple(
        action
        for skill_id in sorted(manifests)
        if (
            action := _fill_activation(
                query,
                skill_id,
                manifests[skill_id],
                cards,
                conversion=skill_id in conversion_skills,
            )
        )
        is not None
    )


def _pick_designate(query: Query, options: tuple[str, ...]) -> str | None:
    """借刀的 Bn：**已明敌人优先，其次体力最低**；同分取座位号小的（确定性）。

    只读 ``query.view`` 的公开面（体力 / 已明身份），不猜隐藏身份。排序键的前半段复用
    :func:`_score_seat_option` 的``conversion``口径（敌我估值），避免第二份"谁是敌人"的
    判据；后半段再按体力 / 座位号决胜。
    """
    view = query.view

    def rank(text: str) -> tuple[float, int, int]:
        seat = int(str(text).removeprefix(_SEAT_PREFIX))
        player = view.players[seat]
        target_value = _score_seat_option(
            query, text, skill_id="borrowed_sword:designate", conversion=True
        )
        return (target_value, -player.hp, -seat)

    usable = [text for text in options if str(text).startswith(_SEAT_PREFIX)]
    if not usable:
        return None
    return max(usable, key=rank)


def _fill_card_manifest(query: Query, steps) -> tuple[int, int] | None:
    """按**牌名清单**填外层两步：持刀者 A（``_score_seat_option``）→ Bn（敌人 / 低体力）。

    清单形状由服务端下发（``card_preactivations[card.type]``，两步 ``seat``）；这一步
    **不重算**合法性——A / Bn 都是服务端给出的候选，AI 只从中挑。任何一步填不出来 ⇒
    ``None``（不交半成品）。
    """
    ordered = sorted(steps, key=lambda item: item.order)
    if len(ordered) != 2:
        return None
    holder_row = _pool_row_for(ordered[0], ())
    if holder_row is None or not holder_row.options:
        return None
    holder = _pick_options(
        ordered[0], tuple(holder_row.options), query, "borrowed_sword", conversion=True
    )
    if not holder:
        return None
    designate_row = _pool_row_for(ordered[1], tuple(holder))
    if designate_row is None or not designate_row.options:
        return None
    designate = _pick_designate(query, tuple(str(text) for text in designate_row.options))
    if designate is None:
        return None
    return (
        int(str(holder[0]).removeprefix(_SEAT_PREFIX)),
        int(designate.removeprefix(_SEAT_PREFIX)),
    )


def _card_preactivation_candidates(query: Query) -> tuple[UseCard, ...]:
    """本次出牌阶段可填的**附带指定**使用（W5-02b 借刀）。

    键 = ``card.type``：手牌里每张同型牌 × 按清单填出的 ``(A, Bn)`` 各造一条
    ``UseCard(card, targets=(A,), designations=(Bn,))``。提交后由引擎的第四条路按
    **同一份附带指定池**校验（AI 不另写合法性）；持刀者窗口的 a / b 由它自己那一步
    应答，不在这里。
    """
    if QueryKind(query.kind) is not QueryKind.PLAY_PHASE:
        return ()
    manifests = dict(getattr(query, "card_preactivations", None) or {})
    if not manifests:
        return ()
    by_type: dict[str, list[CardRef]] = {}
    for card in query.view.self_hand:
        by_type.setdefault(card.type, []).append(card)
    out: list[UseCard] = []
    for card_type in sorted(manifests):
        filled = _fill_card_manifest(query, manifests[card_type])
        if filled is None:
            continue
        holder, designate = filled
        for card in by_type.get(card_type, ()):
            out.append(
                UseCard(
                    card=card,
                    targets=(holder,),
                    designations=(designate,),
                    answers_seq=query.seq,
                )
            )
    return tuple(out)


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
    """【仁德】：给牌是成本；本阶段首次给到 ≥2 张回复 1 点（只在受伤时值得）。

    票 05 起候选是**骨架**（``picks`` 为空，给哪几张改由发动后的 ``SELECT_CARDS`` 问）；
    旧式 ``len(action.picks) >= 2`` 对骨架恒假、AI 会干脆不用【仁德】。这里按下游那一步
    的实际选择预估（与 :func:`_score_select_cards` 的给牌分支同一模型）：受伤且手牌 ≥2 张
    时给最便宜的两张换 1 点体力，否则给最便宜的一张（净亏，通常不会发动）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    if action.picks:
        bonus = _RENDE_RECOVER_VALUE if len(action.picks) >= 2 and _wounded(view) else 0.0
        return bonus - _picked_value(view, action)
    if not view.self_hand:
        return 0.0
    if _wounded(view) and len(view.self_hand) >= 2:
        return _RENDE_RECOVER_VALUE - _skeleton_cost(view, 2)
    return -_skeleton_cost(view, 1)


def _score_zhiheng(query: Query, action: Action, view: PlayerView) -> float:
    """【制衡】：弃任意张摸等量——净牌数不变、换质量；弃【桃】/【闪】按价值扣分。

    票 04 起候选是**骨架**（``picks`` 为空，弃几张改由发动后的 ``SELECT_CARDS`` 问）。
    按下游那一步的实际选择预估：:func:`_score_select_cards` 对"任意张自己的牌"逐张按
    :data:`_CYCLE_VALUE - 牌价` 估值 ⇒ 它会弃掉所有**低于换牌期望**的牌（手牌 ∪ 装备区），
    这里就用同一组牌算同一笔账。旧公式 ``1.5 * len(picks) - picked_value`` 对骨架恒 0，
    会让 AI 干脆不用【制衡】（与【结姻】票 03 同一类退化）。
    """
    assert isinstance(action, ActivateSkill)
    del query
    if action.picks:
        return _CYCLE_VALUE * len(action.picks) - _picked_value(view, action)
    mine = view.players[view.seat]
    values = sorted(_card_value(card) for card in (*view.self_hand, *mine.equip))
    count = sum(1 for value in values if value < _CYCLE_VALUE)
    if count == 0:
        # 没有值得换的牌，但 ``lo=1`` 仍逼着弃一张 ⇒ 发动只会白丢最便宜的那张。
        # 给**负分**（而不是 0.0）：否则与"结束阶段"打平，AI 会随机把限一次的机会浪费掉。
        return _CYCLE_VALUE - values[0] if values else 0.0
    return _CYCLE_VALUE * count - sum(values[:count])


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
    # 装备技（W4-4）：会问"是否发动"的 5 条非锁定装备**各给一个正分常量**。
    # 缺省 ``_UNKNOWN_SKILL_VALUE = -1.0 < Pass(0)`` ⇒ 不入表就永不发动（雌雄无法复现
    # 的根因）；正分 ⇒ 永远发动（Pass = 0，成本 / 收益不建模，见票面"不做收益模型调参"）。
    "equip.double_sword": (_constant(2.0), "雌雄双股剑：令异性目标弃一张或你摸一张"),
    "equip.green_dragon_blade": (_constant(2.0), "青龙偃月刀：被抵消后再补一张【杀】"),
    "equip.axe": (_constant(2.0), "贯石斧：弃两张令被抵消的【杀】依然造成伤害"),
    "equip.qilin_bow": (_constant(2.0), "麒麟弓：造成伤害时弃目标的一匹坐骑"),
    "equip.ice_sword": (_constant(2.0), "寒冰剑：防止伤害并依次弃目标两张牌"),
    # W4-fix-2：丈八蛇矛的转化【杀】候选已收回，AI 改从清单填 —— 没有条目就落在
    # `_UNKNOWN_SKILL_VALUE = -1.0` 上、永不发动（五条转化技的覆盖判据会红）。
    "equip.serpent_spear": (_constant(2.0), "丈八蛇矛：两张手牌当【杀】使用或打出"),
}
"""技能 id → ``(打分函数, 默认策略说明)``（`map.md` R4-Q6 的统一签名）。

**英雄侧只装非锁定武将技能**：锁定技没有 ``SKILL_ACTIVATION`` 询问（派发器直接跑），
故不要求条目。**装备侧**装那 5 条会问"是否发动"的非锁定装备技（雌雄 / 青龙 / 贯石 /
麒麟 / 寒冰）；【八卦阵】/【丈八蛇矛】/【方天画戟】没有"是否发动"询问（候选注入 /
修饰器），故仍走缺省保守 ``Pass``。三向同步断言住 ``tests/test_heuristic_ai.py``：
① 启用的非锁定武将技都在；② 那 5 条会问"是否发动"的非锁定装备技都在；③ 表里每个 id
都在数据文件里（武将技 ∪ 装备技）。加武将 / 装备忘了教 AI 会立刻判红。
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
        actions: list[Action] = list(query.legal_actions)
        # W4-4：预发动后复合动作**不在候选里**，AI 自己按清单填；同一个技能若也给了
        # 骨架候选（未删的被动入口），用填好的那份替换它——"确认 = 填表"是这一档的语义。
        composites = _preactivation_candidates(query)
        if composites:
            filled = {action.skill_id for action in composites}
            actions = [
                action
                for action in actions
                if not (
                    isinstance(action, ActivateSkill) and action.skill_id in filled
                )
            ]
            actions.extend(composites)
        # W5-02b：借刀的**外层两步清单**（附带指定）——复合使用同样不在候选里，
        # AI 读 ``card_preactivations`` 自己填 A / Bn；持刀者窗口的 a / b 由后续询问应答。
        actions.extend(_card_preactivation_candidates(query))
        if not actions:
            raise ValueError(f"{query.kind} 询问没有候选行动，参与者无法应答")
        scorer = _SCORERS[QueryKind(query.kind)]
        scores = [scorer(query, action) for action in actions]
        best = max(scores)
        winners = [action for score, action in zip(scores, actions) if score == best]
        return winners[self._rng.randbelow(len(winners))]
