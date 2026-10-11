"""虚拟牌 / 转化牌的构造与 id 派生（票 12 §6 / 票 30；票 03 泛化）。

**判据是 ``card_id < 0``**（票 12 §Q21）：虚拟牌不参与牌守恒与区域互斥——它从不进入
任何区域（``GameState.card`` 对负 id fail-closed），因此"牌守恒"这条整局不变量对它
天然成立。虚拟牌的 ``suit`` / ``rank`` / ``color`` 都可空。

**三形态的牌面事实**（ADR-003 / ADR-004；`03` §5「效果原语（Effect Primitives）清单」的四列表）：

- **单源转化**（武圣 / 龙胆 / 国色 / 急救）：继承原牌的**花色 / 点数 / 颜色**；
- **多源转化**（【丈八蛇矛】两张手牌当【杀】）：花色**恒无**、点数**恒无**、颜色按
  "多张牌同色则有该颜色、否则无色"派生（ADR-004 逐字）；
- **纯虚拟牌**（【八卦阵】视为使用/打出的【闪】）：三者皆无
  （`A/glossary_gamecard.txt:127`）。

**属性一律不继承**（ADR-003）：属性**由效果决定**，效果未声明即无属性（= 普通伤害）。
这条在本项目是**结构事实**——:class:`~sgs.state.CardRef` 根本没有属性字段，故
"武圣转化的【杀】是普通【杀】"不可能被某个构造器悄悄改掉。

id 由**来源实体牌 id** 确定性地派生（同种子逐字节可复现）：同一目标牌名 + 同一组来源
恒得同一个 id、**不同来源不同 id**（来源集合的枚举序不影响，构造时先排序）。票 03 把
命名空间从"最多两张来源"泛化到 :data:`MAX_VIRTUAL_SOURCES` 张：每个**目标牌名**一段
:class:`VirtualKind` 命名空间，纯虚拟牌与一切转化牌（含单源、多源）逐对不撞 id。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import IntEnum
from itertools import combinations

from sgs.data import Color
from sgs.effects import (
    CandidateScope,
    EffectContext,
    check_count,
    check_targets,
    forced_use_targets,
    move_card,
)
from sgs.state import (
    CardRef,
    GameState,
    MoveReason,
    PlayCard,
    RespondMode,
    UseCard,
    Zone,
    ZoneRef,
)

__all__ = [
    "MAX_VIRTUAL_SOURCES",
    "SourceFilter",
    "VirtualKind",
    "VirtualSourceError",
    "card_ids",
    "grouped_candidates",
    "pure_virtual_card",
    "single_conversion",
    "transformed_card",
    "transformed_slash",
    "virtual_card_id",
    "virtual_card_use",
    "virtual_candidates",
    "virtual_dodge",
]


class VirtualKind(IntEnum):
    """虚拟牌的**目标牌名命名空间**（票 03 §要做什么 1「每个目标牌名一段 kind」）。

    显式常量而不是按牌名取哈希：值必须**跨进程 / 跨运行稳定**（确定性纪律），
    且既有值（``0`` = 转化【杀】、``1`` = 视为【闪】）**一字不改**。新增牌名时
    在此追加一行，旧 id 不漂移。
    """

    SLASH = 0
    DODGE = 1
    PEACH = 2
    INDULGENCE = 3
    DUEL = 4


LEGACY_KIND_COUNT = 2
"""**兼容命名空间**的段数（票 03 的确定性契约，不是可调参数）。

``0`` / ``1`` 两段的 id **必须与旧算式逐字节相同**：M2 的 Golden Replay 磁带里记着
【丈八蛇矛】转化【杀】的 ``card_id``（如 ``-11882``），而门禁要求 34 条基准一行不改
（票 14 §7）。故这两段保留旧算式，票 03 的泛化从 ``2`` 起另起一段**更大的**区间——
两段区间互不重叠（见 :func:`_generalized_block` 的界推）。
"""


_KIND_BY_TYPE: dict[str, VirtualKind] = {
    "slash": VirtualKind.SLASH,
    "dodge": VirtualKind.DODGE,
    "peach": VirtualKind.PEACH,
    "indulgence": VirtualKind.INDULGENCE,
    "duel": VirtualKind.DUEL,
}
"""目标**牌名 id** → 命名空间；只登记本里程碑有生产者的牌（票 03 §表 / 票 04 加【决斗】）。"""

KIND_COUNT = len(VirtualKind)
"""命名空间的段数（只读事实，供测试与文档引用；id 算式不读它）。"""
MAX_VIRTUAL_SOURCES = 8
"""虚拟牌的**来源张数上界**（票 03 §验收 4）。

步长与算式不再依赖来源张数（票 03 §要做什么 1），但"id 空间有限"这件事必须有一个
显式上界才能成立：超过它由 :func:`transformed_card` 抛 :class:`VirtualSourceError`，
而不是静默产出可能撞 id 的虚拟牌。取 8 是宽松的余量——标准版全部转化技能最多两张
来源（【丈八蛇矛】），8 张的任何组合都远超未来可预见的需求，同时把"撞 id"这条风险
变成构造期的可定位错误。
"""

_BASE_DIGITS = 10
"""``virtual_card_id`` 的**十进制数字槽**：``base = card_count + 1`` 进制的每一位。"""


class VirtualSourceError(ValueError):
    """来源集合不合法（空 / 超过 :data:`MAX_VIRTUAL_SOURCES`）。

    继承 ``ValueError``：这是构造期的编程错误，与数据加载期的聚合报错同姿态——
    早抛、能定位，而不是让一张撞 id 的虚拟牌流进对局。
    """


def _base(card_count: int) -> int:
    """进制 = ``card_count + 1``：来源 id 全部落进 ``1..card_count``，故恒小于它。"""
    return card_count + 1


def _digits_per_source() -> int:
    """一张来源牌在编码里占的十进制位数（``_BASE_DIGITS`` 的倍数）。"""
    return _BASE_DIGITS


def virtual_card_id(
    *,
    kind: int,
    sources: tuple[int, ...],
    card_count: int,
) -> int:
    """由 ``(kind, 来源 id)`` 派生一个**负数** id，不同组合不重复（确定性）。

    两种命名空间按 ``kind`` 分段（票 03 §要做什么 1；判据见 :data:`LEGACY_KIND_COUNT`）：

    - ``kind < 2``（【丈八蛇矛】的转化【杀】/【八卦阵】的视为【闪】）走**兼容编码**：
      逐张来源按 ``base = card_count + 1`` 进制折叠，段步长恒为 ``base ** 2``。这三个
      数字与旧算式**逐字节相同**，故 M2 的 Golden Replay 磁带一行不改；
    - ``kind >= 2``（票 03 起新增的目标牌名）走**泛化编码**：每段容量
      :func:`_generalized_block`，与来源张数上限（:data:`MAX_VIRTUAL_SOURCES`）同阶，
      故单源 / 多源 / 纯虚拟**逐对不撞**。

    两段区间互不重叠：泛化段的起点 ``2 * block`` 远大于兼容段的上界
    ``base ** 2``（``block`` 至少是它的 ``BASE ** 8`` 倍），见 :func:`_generalized_block`。

    签名保持既有的 ``kind`` / ``sources`` / ``card_count`` 三个关键字——既有调用点与测试
    不破；``card_count`` 仍作进制用（不再有"来源张数上限"那层含义，那层由
    :data:`MAX_VIRTUAL_SOURCES` 显式承担）。
    """
    digits = _digits_per_source()
    base = _base(card_count)
    # 空来源是**合法的**（纯虚拟牌走这一段）；"转化牌至少一张来源"由
    # :func:`transformed_card` 把关，id 派生本身不区分二者。
    _validate_source_count(sources, max_sources=MAX_VIRTUAL_SOURCES, target="virtual_card_id")
    if kind < LEGACY_KIND_COUNT:
        mixed = _fold_legacy(base=base, sources=sources)
        return -(1 + kind * base * base + mixed)
    mixed = _fold(base=base, digits=digits, sources=sources)
    block = _generalized_block(base=base, digits=digits)
    return -(1 + kind * block + mixed)


def _generalized_block(*, base: int, digits: int) -> int:
    """泛化命名空间的一段容量：容纳 ``len <= MAX_VIRTUAL_SOURCES`` 的全部哨兵编码。

    编码的最大值是 ``MAX_VIRTUAL_SOURCES * (base**digits) ** MAX_VIRTUAL_SOURCES
    + (base**digits - 1)``，它小于 ``(base**digits) ** (MAX_VIRTUAL_SOURCES + 1)``
    （因为 ``MAX_VIRTUAL_SOURCES < base**digits``），故取后者为容量即严格包住，
    且各段（``kind`` 的不同取值）首尾不相接。
    """
    return base ** (digits * (MAX_VIRTUAL_SOURCES + 1))


def _fold(*, base: int, digits: int, sources: tuple[int, ...]) -> int:
    """泛化编码：最高段是张数哨兵，其后逐张来源各占一段。

    哨兵槽的取值为 ``1..MAX_VIRTUAL_SOURCES``，而任何来源段的取值都 ``< base``，
    故"哨兵 + 定长段"的读法是唯一的——这正是旧算式只在高 ``2`` 张时成立、第 ``3`` 张
    起就撞 id 的原因，泛化编码把它补上。
    """
    mixed = len(sources)
    for card_id in sources:
        mixed = mixed * (base**digits) + card_id
    return mixed


def _fold_legacy(*, base: int, sources: tuple[int, ...]) -> int:
    """**兼容编码**（旧算式逐字保留，票 03 的确定性契约）：只用 ``base`` 作进制。

    它没有张数哨兵，故**只对最多两张来源**单射——但兼容段里的合法来源集合恰好只有
    "零张 / 一张 / 两张"（纯虚拟牌与 :func:`transformed_slash`），故在这段里它仍是单射。
    新增来源张数的泛化一律走 :func:`_fold`，不去动这三个数字。
    """
    mixed = 0
    for card_id in sources:
        mixed = mixed * base + card_id
    return mixed



def _validate_source_count(
    sources: Sequence[int], *, max_sources: int, target: str
) -> None:
    """来源张数的上界校验（票 03 §验收 4）：超过即抛可定位的错误。"""
    if len(sources) > max_sources:
        raise VirtualSourceError(
            f"{target}: 来源 {len(sources)} 张超过上界 {max_sources}"
            f"（MAX_VIRTUAL_SOURCES）"
        )


def _require_sources(sources: Sequence[int], *, target: str) -> None:
    """转化牌的"至少一张来源"校验：空集合是构造期错误，不是"纯虚拟牌"。"""
    if not sources:
        raise VirtualSourceError(f"{target}: 转化牌至少要有一张来源牌（sources 为空）")


def _common_color(state: GameState, sources: tuple[int, ...]) -> Color | None:
    """多张转化："这些牌的颜色均相同 ⇒ 有该颜色，否则无色"（`A/glossary_gamecard.txt:91`）。

    颜色由每张来源牌的 ``CardRef.color``（花色派生的唯一真源）读，不重抄花色映射。
    """
    colors = [state.card(card_id).color for card_id in sources]
    first = colors[0]
    return first if all(color is first for color in colors) else None


def transformed_card(
    state: GameState, sources: tuple[int, ...], target_type_id: str
) -> CardRef:
    """**共享构造器**：把 ``sources`` 这几张实体牌转化成 ``target_type_id`` 牌名的牌。

    三形态在这里由**来源张数**分派（票 03 §要做什么 2）：

    - 单源 ⇒ 继承原牌**花色 / 点数 / 颜色**；
    - 多源 ⇒ 花色恒无、点数恒无、颜色按同色派生；
    - 属性一律不继承（ADR-003：属性由效果决定，未声明即普通伤害）；
    - ``virtual_sources`` 记来源实体牌 id（**排序后**，故枚举序不影响 id）；
    - 负 id 判据不变（:attr:`CardRef.is_virtual`）。

    ``sources`` 为空或超过 :data:`MAX_VIRTUAL_SOURCES` 时抛 :class:`VirtualSourceError`
    ——"不产生撞 id 的虚拟牌"这条靠**构造期**兜住，而不是靠调用方自律。
    """
    _require_sources(sources, target=transformed_card.__name__)
    _validate_source_count(sources, max_sources=MAX_VIRTUAL_SOURCES, target=transformed_card.__name__)
    ids = tuple(sorted(sources))
    kind = _kind_of(target_type_id)
    first = state.card(ids[0])
    single = len(ids) == 1
    return CardRef(
        card_id=virtual_card_id(kind=kind, sources=ids, card_count=len(state.cards)),
        type=target_type_id,
        name=_card_name(state, target_type_id),
        suit=first.suit if single else None,
        rank=first.rank if single else None,
        color=first.color if single else _common_color(state, ids),
        virtual_sources=ids,
    )


def _kind_of(target_type_id: str) -> VirtualKind:
    """目标牌名 id → 命名空间；未登记的牌名当场抛错（不静默挤进别的段）。"""
    try:
        return _KIND_BY_TYPE[target_type_id]
    except KeyError:  # pragma: no cover - 由新增牌名时补表覆盖
        raise VirtualSourceError(
            f"虚拟牌的牌名 {target_type_id!r} 还没有命名空间：请先登记 VirtualKind"
        ) from None


def _card_name(state: GameState, target_type_id: str) -> str:
    """目标牌名的**显示名**：读实体牌里该牌名的第一张（牌面事实的唯一真源）。

    虚拟牌不是实体牌，但牌名文本必须与实体牌一致；从既有 ``CardRef`` 里读而不是
    回头 import 内容包：构造器只依赖 ``state``，与 :func:`_common_color` 同姿态。
    """
    for card in state.cards:
        if card.type == target_type_id:
            return card.name
    return target_type_id  # pragma: no cover - 标准包里每个牌名都有实体牌


def transformed_slash(state: GameState, sources: tuple[int, ...]) -> CardRef:
    """【丈八蛇矛】把两张手牌**转化**成一张【杀】（票 12 §Q21 / U-12-2/U-12-3）。

    共享构造器的**薄包装**（票 03：既有调用点与测试不破）。与一手原文的两处偏离按
    用户裁定登记（实现期登记、不改冻结文档）：

    - **点数恒无**（U-12-3）：一手 `A/glossary_gamecard.txt:95` 写"点数为各牌点数之和"，
      用户裁定**不采用**（ADR-004 的字面在本图成立）；
    - **有距离限制**（U-12-2）：与 `:125` 的"无距离限制"相反；M2 不可观测（标准版没有
      "视为使用【杀】"的生产者，本函数是**转化**）。
    """
    return transformed_card(state, sources, "slash")


def pure_virtual_card(state: GameState, target_type_id: str) -> CardRef:
    """**纯虚拟牌**：没有来源实体牌，牌面（花色 / 点数 / 颜色）三者皆无（ADR-004）。

    与 :func:`transformed_card` 的分工是构造期的硬边界（票 03 §验收 4）：转化牌**必须**
    至少一张来源，纯虚拟牌**必须**没有来源。生产者：【八卦阵】的视为【闪】、
    【离间】的视为使用【决斗】（票 04）。id 走 :func:`virtual_card_id` 的**空来源**段，
    故同一牌名的纯虚拟牌恒得同一个 id、且与一切转化牌不撞。
    """
    return CardRef(
        card_id=virtual_card_id(
            kind=_kind_of(target_type_id), sources=(), card_count=len(state.cards)
        ),
        type=target_type_id,
        name=_card_name(state, target_type_id),
        suit=None,
        rank=None,
        color=None,
    )


def virtual_dodge(state: GameState) -> CardRef:
    """【八卦阵】判定为红时"**视为**使用/打出【闪】"的纯虚拟牌（票 10 §5.3）。

    **纯虚拟牌**：没有来源实体牌（``virtual_sources`` 为空），牌面三者皆无。它是"这次
    需要"的产物、与座位无关，故 id 里不含持有者——同一次需要里只用它一次即可。
    """
    return pure_virtual_card(state, "dodge")


# ---------------------------------------------------------------------------
# 转化的候选接入（票 03 §要做什么 4 / §机制面的统一约定「候选面」）
# ---------------------------------------------------------------------------


def card_ids(state: GameState, seat: int, zone: Zone) -> tuple[int, ...]:
    """``seat`` 在 ``zone`` 里的牌 id（手牌 / 装备区 / 判定区按区序）。

    只读现状、不改状态：来源筛选是**候选生成期**的事，真正被转化消耗的实体牌由
    ``virtual_sources`` 在结算段走正常移动路径（票 12 §Q21）。
    """
    player = state.player(seat)
    if zone is Zone.HAND:
        return tuple(player.hand)
    if zone is Zone.EQUIP:
        return tuple(player.equip)
    if zone is Zone.JUDGE:
        return tuple(player.judge)
    return ()


def virtual_card_use(
    state: GameState,
    seat: int,
    card: CardRef,
    ctx: Any,
) -> list[Any]:
    """一张转化牌在本次 ``CANDIDATES`` 派发里的全部行动（**使用 / 打出共用一份**）。

    与真牌**同一条**合法性检测（票 03 §要做什么 4：「目标池走与真牌同一套
    ``check_targets``（【丈八蛇矛】的先例）」）：

    - ``PLAY``：按目标池逐目标 ``UseCard``，并先过 ``COUNT_LIMIT``（按**目标牌名**
      计数，与真牌同一条链——票 03 §要做什么 4 / 规则 16.4「按目标牌名统计使用次数」）；
    - ``RESPOND`` + ``mode = PLAY``：一个 ``PlayCard``（打出不占次数）；
    - ``RESPOND`` + ``mode = USE``：按 ``respond_targets`` 逐目标 ``UseCard``，过次数链；
    - ``DODGE``：无目标的 ``UseCard``（真实【闪】的候选正是这么生成的）。

    **本函数只读状态、不移动任何牌**：来源牌的移出由 :func:`virtual_candidates` 在整批
    候选算完之后统一做一次（否则先算的那组候选会改变后算那组看到的目标状态）。
    """
    if ctx.scope is CandidateScope.PLAY:
        if check_count(state, seat, card):
            return []
        pool = check_targets(state, seat, card)
        return [
            UseCard(card=card, targets=(target,), answers_seq=ctx.answers_seq)
            for target in pool
        ]
    if ctx.scope is CandidateScope.DODGE:
        targets = tuple((target,) for target in check_targets(state, seat, card)) or ((),)
        return [
            UseCard(card=card, targets=combo, answers_seq=ctx.answers_seq)
            for combo in targets
        ]
    if ctx.scope is CandidateScope.RESPOND:
        if ctx.respond_mode is RespondMode.USE:
            if check_count(state, seat, card):
                return []
            pool = forced_use_targets(state, seat, card, ctx.respond_targets)
            combos = tuple((target,) for target in pool) if pool else ((),)
            return [
                UseCard(card=card, targets=combo, answers_seq=ctx.answers_seq)
                for combo in combos
            ]
        return [PlayCard(card=card, answers_seq=ctx.answers_seq)]
    return []


def _lose_extra_sources(state: GameState, sources: tuple[int, ...], *, actor: int) -> None:
    """把**非手牌区**的来源牌在候选生成期移出（手牌由使用 / 打出的 L4/L6 自然搬走）。

    规则 16.3 明说转化牌"花色与点数继承原牌、判定按原牌处理"，而武圣 / 急救的"一张
    红色牌"在标准版下包含装备区里的红牌。装备区的牌不经过"手牌 → 处理区"那条 L4 路径，
    故在此用一次 ``LOSE`` 移动把它送进弃牌堆——与牌被**使用**后的归宿一致，且不改记
    录流（候选生成期不发记录，见票 12 §Q21 的"虚拟牌自己不动"）。

    手牌来源**不在这里动**：它由使用 / 打出的既有 L4/L6 移动搬到处理区再进弃牌堆，
    动了会与那条路径抢同一张牌。
    """
    player = state.player(actor)
    for card_id in sources:
        if card_id in player.hand:
            continue
        if card_id in player.equip:
            zone = Zone.EQUIP
        elif card_id in player.judge:
            zone = Zone.JUDGE
        else:
            continue
        ctx = EffectContext(actor=actor)
        move_card(
            state,
            ctx,
            (state.card(card_id),),
            to_zone=ZoneRef(Zone.DISCARD),
            reason=MoveReason.LOSE,
            owner=actor,
        )


def virtual_candidates(
    state: GameState,
    ctx: Any,
    *,
    target_type_id: str,
    sources: tuple[tuple[int, ...], ...],
    scopes: tuple[CandidateScope, ...],
) -> list[Any]:
    """把 ``sources`` 逐组转成 ``target_type_id`` 的候选（票 03 的**唯一**候选构造子）。

    ``sources`` 是**已经筛好的**来源组合（每组的张数就是本次转化的来源张数）；
    本函数只做"组合 → 虚拟牌 → 行动"的三段搬运，不重复任何筛选判据——每个技能一个
    ``CANDIDATES`` 回调声明"源筛选 + 目标牌名"，谓词**内联在回调里**（票 03 §要做什么 3）。

    整批候选**先全部算完**、之后才把非手牌区的来源牌移出（:func:`_lose_extra_sources`）：
    逐组边算边移会让先生成的那组改变后一组看到的目标状态（【空城】读"有没有手牌"）。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return []
    if ctx.scope not in scopes:
        return []
    if ctx.scope is CandidateScope.RESPOND and ctx.respond_required != target_type_id:
        return []
    actions: list[Any] = []
    moved: list[tuple[int, ...]] = []
    for group in sources:
        if len(group) > MAX_VIRTUAL_SOURCES:
            raise VirtualSourceError(
                f"{target_type_id}: 来源 {len(group)} 张超过上界 {MAX_VIRTUAL_SOURCES}"
            )
        card = transformed_card(state, group, target_type_id)
        generated = virtual_card_use(state, ctx.subject, card, ctx)
        if generated:
            moved.append(group)
        actions.extend(generated)
    for group in moved:
        _lose_extra_sources(state, group, actor=ctx.subject)
    return actions


def grouped_candidates(
    state: GameState,
    ctx: Any,
    *,
    target_type_id: str,
    cards: Sequence[int],
    group_size: int,
    scopes: tuple[CandidateScope, ...],
) -> list[Any]:
    """把 ``cards`` 按 ``group_size`` 分组后交给 :func:`virtual_candidates`。

    【丈八蛇矛】的"两张手牌"与 H 族的"一张牌"共用这一处**组合**，故"每个技能一个
    ``CANDIDATES`` 回调"只是选一组参数，不必各写一份组合逻辑。
    """
    if ctx.card is not None or ctx.owner != ctx.subject:
        return []
    groups = tuple(combinations(cards, group_size))
    return virtual_candidates(
        state, ctx, target_type_id=target_type_id, sources=groups, scopes=scopes
    )


def single_conversion(
    state: GameState,
    ctx: Any,
    *,
    target_type_id: str,
    cards: Sequence[int],
    scopes: tuple[CandidateScope, ...],
) -> list[Any]:
    """单源转化（H 族四技能）：每张筛好的牌各作一张虚拟牌。"""
    return grouped_candidates(
        state,
        ctx,
        target_type_id=target_type_id,
        cards=cards,
        group_size=1,
        scopes=scopes,
    )


SourceFilter = Callable[[int], bool]
"""来源谓词：``card_id -> bool``（五个谓词全部内联在技能的 ``CANDIDATES`` 回调里）。"""

