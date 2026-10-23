"""虚拟牌 id 的线上上限与单射（W4-fix-1；上界常量见 W4-fix-5）。

线上 id 是 JSON 数字：浏览器 ``JSON.parse`` 只把 ``|id| < WIRE_INT_LIMIT`` 的整数读成
精确整数，超过即变浮点、回传被 pydantic 拒绝（手动验收实测：``-7.006...e+183``）。
故 ``kind >= 2`` 的泛化编码必须压进 ``WIRE_INT_LIMIT``（源码里的唯一真源），来源张数
上限由"可证明小于它"反推——不是余量，也不是魔法数。

判据全部走**公开缝**：``virtual_card_id`` / ``transformed_card`` / 公开常量。
单射用穷举（单源全牌堆 + 多源抽样与端点）钉住，不靠注释保证。
"""

from __future__ import annotations

from itertools import combinations

import pytest

from sgs.data import load_content_pack
from sgs.effects import CandidateScope
from sgs.skills import (
    MAX_VIRTUAL_SOURCES,
    ModifierContext,
    VirtualKind,
    VirtualSourceError,
    transformed_card,
    virtual_card_id,
)
from sgs.skills.virtual import LEGACY_KIND_COUNT, WIRE_INT_LIMIT, virtual_candidates

from support import SeatSpec, build_state
from test_skills import PACK

PACK_EX_160 = load_content_pack(deck_profile="standard_ex_160")
"""160 张军争档：本副牌堆的来源上界被收紧到 6（M5-01a）。"""

BASE = len(PACK.deck.cards) + 1
"""编码进制 = 牌堆张数 + 1（标准包 108 张 ⇒ 109）。"""

KINDS = tuple(range(len(VirtualKind)))

FIRE_KIND = int(VirtualKind.FIRE_SLASH)
"""M5-05b 新增的火【杀】命名空间（本票唯一允许追加的 kind）。"""

STANDARD_108_BLOCK = 8 * 109**7
"""108 张 / 7 张来源的泛化段容量（W4 冻结值；追加 kind 后一字不改）。"""

MILITARY_160_BLOCK = 7 * 161**6
"""160 张 / 6 张来源的泛化段容量（M5-01a 的收紧；追加 kind 后一字不改）。"""


def _capacity(source_count: int, base: int = BASE) -> int:
    """泛化编码一段的**容量上界**（从算式独立复算，不读实现）。

    最高一段是张数哨兵 ``L``，其后每张来源占一位 ``base`` 进制；因此 ``L`` 张来源
    的编码不超过 ``L * base**L + (base**L - 1) = (L + 1) * base**L - 1``，
    取 ``(L + 1) * base**L`` 即严格包住。
    """
    return (source_count + 1) * base**source_count


def _over_limit_sources() -> tuple[int, ...]:
    return tuple(range(MAX_VIRTUAL_SOURCES + 1))


# ---------------------------------------------------------------------------
# 来源上限由 WIRE_INT_LIMIT 反推（验收 2；不是魔法数）
# ---------------------------------------------------------------------------


def test_the_source_ceiling_is_the_largest_the_wire_limit_allows():
    """``MAX_VIRTUAL_SOURCES`` = 使**全部 kind 段**仍 ``< WIRE_INT_LIMIT`` 的最大来源张数。"""
    assert BASE == 109, "标准包 108 张 ⇒ 进制 109（本票的界推以此为准）"
    admissible = [
        source_count
        for source_count in range(1, 32)
        if len(KINDS) * _capacity(source_count) < WIRE_INT_LIMIT
    ]
    assert admissible, "至少允许一张来源"
    assert MAX_VIRTUAL_SOURCES == max(admissible), (
        f"来源上限应是从 WIRE_INT_LIMIT 反推的最大值 {max(admissible)}，实际 {MAX_VIRTUAL_SOURCES}"
    )
    assert len(KINDS) * _capacity(MAX_VIRTUAL_SOURCES) < WIRE_INT_LIMIT
    assert len(KINDS) * _capacity(MAX_VIRTUAL_SOURCES + 1) >= WIRE_INT_LIMIT, (
        "再多一张来源就越过 WIRE_INT_LIMIT——上限必须紧到这一步"
    )


def test_every_allowed_kind_and_source_count_stays_under_the_wire_limit():
    """``kind`` 全部取值 × 来源 ``0..MAX`` 张的**端点**：``|id| < WIRE_INT_LIMIT``（断言）。"""
    count = len(PACK.deck.cards)
    source_sets: list[tuple[int, ...]] = [()]
    for size in range(1, MAX_VIRTUAL_SOURCES + 1):
        source_sets.append(tuple(range(size)))  # 最小来源（编码最小）
        source_sets.append(tuple(range(count - size, count)))  # 最大来源（编码最大）
    for kind in KINDS:
        for sources in source_sets:
            card_id = virtual_card_id(kind=kind, sources=sources, card_count=count)
            assert abs(card_id) < WIRE_INT_LIMIT, (
                f"kind={kind} sources={sources} 的 id {card_id} 越过浏览器整数精度"
            )


# ---------------------------------------------------------------------------
# 单射：不同 (kind, sources) 两两不等（验收 3）
# ---------------------------------------------------------------------------
#
# 票面写的是"单源全穷举 **1..牌数**"：来源 id 取**非零**（真实牌 id 是 0..牌数-1）。
# 兼容段没有张数哨兵，旧算式本来只对"真实生产者产出的形状"单射：``kind = 0`` 是
# 1/2 张来源（武圣 / 丈八蛇矛），``kind = 1`` 是 0/1 张（八卦阵 / 龙胆）；3 张以上
# 会让 ``base**2`` 的段步长被 ``_fold_legacy`` 的进位吃掉，且 ``(0, 1)`` 与 ``(1,)``
# 撞同一个 id——M2 契约要求 ``kind < 2`` 逐字节不变，故这些既有性质不能在本票里修。
# 泛化段（``kind >= 2``）有张数哨兵，对任意 ``0..MAX`` 张（含牌 id ``0``）都单射。


def test_single_source_ids_are_pairwise_distinct_over_the_whole_deck():
    """单源全穷举：``kind × 牌堆每一张``（``kind`` 之间也不撞）。"""
    count = len(PACK.deck.cards)
    seen: dict[int, tuple[int, tuple[int, ...]]] = {}
    for kind in KINDS:
        for source in range(1, count):
            sources = (source,)
            card_id = virtual_card_id(kind=kind, sources=sources, card_count=count)
            assert card_id not in seen, (
                f"撞 id：{card_id} 同时来自 {seen[card_id]} 与 {(kind, sources)}"
            )
            seen[card_id] = (kind, sources)


def _generalized_samples(count: int) -> set[tuple[int, ...]]:
    """泛化段的抽样：含 ``0`` 的组合 + 最小 / 最大 / 进位三类端点（0..MAX 张）。"""
    sample = (0, 1, 2, 53, 106, 107)
    source_sets: set[tuple[int, ...]] = set()
    for size in range(0, len(sample) + 1):
        source_sets.update(combinations(sample, size))
    for size in range(1, MAX_VIRTUAL_SOURCES + 1):
        source_sets.add(tuple(range(size)))  # 最小端点（编码最小）
        source_sets.add(tuple(range(count - size, count)))  # 最大端点（编码最大）
        source_sets.add(tuple(sorted((0, *range(count - size + 1, count)))))  # 进位端点
    return source_sets


def test_generalized_ids_are_pairwise_distinct_for_every_source_count():
    """``kind >= 2``：0..MAX 张的抽样与端点逐对不等，且 ``|id| < WIRE_INT_LIMIT``。"""
    count = len(PACK.deck.cards)
    seen: dict[int, tuple[int, tuple[int, ...]]] = {}
    for kind in KINDS:
        if kind < 2:
            continue
        for sources in sorted(_generalized_samples(count)):
            card_id = virtual_card_id(kind=kind, sources=sources, card_count=count)
            assert abs(card_id) < WIRE_INT_LIMIT, (kind, sources, card_id)
            assert card_id not in seen, (
                f"撞 id：{card_id} 同时来自 {seen[card_id]} 与 {(kind, sources)}"
            )
            seen[card_id] = (kind, sources)


def test_legacy_ids_are_pairwise_distinct_for_the_shapes_they_produce():
    """兼容段的既有形状：``kind = 0`` 单源 + 双源、``kind = 1`` 空来源 + 单源。"""
    count = len(PACK.deck.cards)
    singles = [(source,) for source in range(1, count)]
    shapes = {
        int(VirtualKind.SLASH): [*singles, *combinations(range(1, count), 2)],
        int(VirtualKind.DODGE): [(), *singles],
    }
    seen: dict[int, tuple[int, tuple[int, ...]]] = {}
    for kind, source_sets in shapes.items():
        for sources in source_sets:
            card_id = virtual_card_id(kind=kind, sources=sources, card_count=count)
            assert card_id not in seen, (
                f"撞 id：{card_id} 同时来自 {seen[card_id]} 与 {(kind, sources)}"
            )
            seen[card_id] = (kind, sources)


# ---------------------------------------------------------------------------
# 兼容段逐字节不变（验收 3；M2 契约）
# ---------------------------------------------------------------------------


def test_the_legacy_namespace_ids_are_byte_identical_to_the_frozen_values():
    """``kind < 2`` 走旧算式：这些值在改动前的金录像 / M2 磁带里就钉着。

    ``-11882`` = 【八卦阵】视为【闪】的纯虚拟牌；``-80`` / ``-81`` = 【丈八蛇矛】
    转化【杀】；``-11900`` / ``-11901`` = 视为【闪】的单源值（42 号金录像）。
    """
    frozen = {
        (VirtualKind.SLASH, (0, 1)): -2,
        (VirtualKind.SLASH, (79,)): -80,
        (VirtualKind.SLASH, (80,)): -81,
        (VirtualKind.DODGE, ()): -11882,
        (VirtualKind.DODGE, (18,)): -11900,
        (VirtualKind.DODGE, (19,)): -11901,
    }
    for (kind, sources), expected in frozen.items():
        assert (
            virtual_card_id(kind=int(kind), sources=sources, card_count=108) == expected
        ), f"兼容段漂移：{kind} {sources} 应为 {expected}"


# ---------------------------------------------------------------------------
# 越界仍在构造期报错（验收 2 的后半句）
# ---------------------------------------------------------------------------


def test_the_source_ceiling_is_enforced_at_construction_time():
    """多一张来源就抛 :class:`VirtualSourceError`，不静默产出撞 id 的牌。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    too_many = _over_limit_sources()

    with pytest.raises(VirtualSourceError):
        virtual_card_id(
            kind=int(VirtualKind.PEACH),
            sources=too_many,
            card_count=len(PACK.deck.cards),
        )
    with pytest.raises(VirtualSourceError):
        transformed_card(state, too_many, "peach")


# ---------------------------------------------------------------------------
# 上界要按**当前进制**校验（W4-fix-5 / M5-01a）：票 01 的 ``base = 109`` 不再是
# "碰巧成立"，更大的牌堆按同一条界**收紧**来源上界
# ---------------------------------------------------------------------------


def test_the_current_pack_passes_the_base_check():
    """正常牌堆 ``base = 109``：7 张来源的编码仍撑得住（每个 ``kind`` 都产得出 id）。"""
    count = len(PACK.deck.cards)
    assert BASE == 109, "标准包 108 张 ⇒ 进制 109"
    for kind in KINDS:
        virtual_card_id(kind=kind, sources=(0,), card_count=count)


def test_the_standard_108_ids_stay_on_the_frozen_seven_source_block():
    """108 张档的段容量**一字不改**：仍是 ``8 * 109**7`` 的 7 张来源紧界。

    牌堆自适应只能**收紧**（160 张掉到 6），不能因 53 张档数学上容得下 8 张就改大。
    这里用冻结算式值直接对账（除了 47 条金录像之外的第二道锁）。
    """
    block = 8 * 109**7
    assert block == 1_462_431_296_653_352, "7 张来源的段容量是 W4 时代冻结的算式值"
    assert (
        virtual_card_id(kind=2, sources=(18,), card_count=108)
        == -2_924_862_593_306_832
    ), "kind=2 / 单源 18 的 id = -(1 + 2*block + (1*109+18))"
    assert virtual_card_id(kind=4, sources=(), card_count=108) == -(1 + 4 * block)


def test_a_smaller_deck_does_not_raise_the_source_ceiling_above_seven():
    """53 张档（base=54）数学上容得下 8 张来源，但上限冻结在 7——旧 id 不漂移。"""
    count = 53
    virtual_card_id(
        kind=int(VirtualKind.PEACH), sources=tuple(range(7)), card_count=count
    )
    with pytest.raises(VirtualSourceError):
        virtual_card_id(
            kind=int(VirtualKind.PEACH), sources=tuple(range(8)), card_count=count
        )


def test_the_military_160_deck_narrows_the_source_ceiling_but_keeps_ids_safe():
    """160 张军争 ⇒ ``base = 161``：7 张来源越过线上上限，故按 6 张编码。

    这是 M5-01a 的 160 档能跑全 AI 的结构前提：不收紧的话任何转化牌
    （关羽 / 赵云 / 大乔 / 华佗 / 丈八蛇矛 / 八卦阵）都会在构造期抛错。
    6 张仍远大于本里程碑的最多两张来源（【丈八蛇矛】）。
    """
    count = 160
    ceiling = 6
    assert len(KINDS) * (ceiling + 1) * 161**ceiling < WIRE_INT_LIMIT
    assert len(KINDS) * (ceiling + 2) * 161 ** (ceiling + 1) >= WIRE_INT_LIMIT, (
        "160 进制下 7 张必须撑不住，否则这条测试没有钉住收紧的边界"
    )

    for kind in KINDS:
        for sources in ((), (0,), (0, 1), tuple(range(ceiling))):
            card_id = virtual_card_id(kind=kind, sources=sources, card_count=count)
            assert abs(card_id) < WIRE_INT_LIMIT, (kind, sources, card_id)

    with pytest.raises(VirtualSourceError):
        virtual_card_id(
            kind=int(VirtualKind.PEACH),
            sources=tuple(range(ceiling + 1)),
            card_count=count,
        )


def test_the_candidate_guard_uses_the_deck_specific_source_ceiling():
    """160 张档：7 张来源的组在**候选阶段**就抛；108 张档仍按 7 张编码。

    候选守卫必须与 :func:`transformed_card` 读**同一个** ``_source_ceiling_for_base``：
    否则 160 档的 7 张组会先过守卫、再到构造器才抛，"第几道闸拦住"随调用路径漂移。
    """
    ctx = ModifierContext(subject=0, owner=0, scope=CandidateScope.DODGE)

    standard = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    actions = virtual_candidates(
        standard,
        ctx,
        target_type_id="peach",
        sources=(tuple(range(MAX_VIRTUAL_SOURCES)),),
        scopes=(CandidateScope.DODGE,),
    )
    assert actions, "108 张档的 7 张来源仍是合法候选组（编码上界 7）"

    military = build_state(pack=PACK_EX_160, seats=[SeatSpec(), SeatSpec()])
    with pytest.raises(VirtualSourceError, match="virtual_candidates"):
        virtual_candidates(
            military,
            ctx,
            target_type_id="peach",
            sources=(tuple(range(MAX_VIRTUAL_SOURCES)),),
            scopes=(CandidateScope.DODGE,),
        )


def test_an_absurd_base_is_rejected_at_construction_time():
    """牌堆大到连一张来源的编码都撑不住 ⇒ 构造期 fail-closed（不静默产 id）。"""
    with pytest.raises(VirtualSourceError, match="撑不住"):
        virtual_card_id(
            kind=int(VirtualKind.PEACH),
            sources=(0,),
            card_count=2**53,
        )


# ---------------------------------------------------------------------------
# M5-05b：火【杀】命名空间（只追加一个 kind；旧 kind 的 block / offset 一字不改）
# ---------------------------------------------------------------------------


def test_appending_the_fire_slash_kind_does_not_move_the_old_kind_ids():
    """旧 kind 0–4 的 block / offset 逐字节不变：108 仍按 7 张来源的紧界分块。

    这是本票的硬约束（票面「108 的既有 id 逐字节不变」）：新 kind **只能追加在
    ``5 * old_block`` 之后**；把 ``KIND_COUNT`` 从 5 加 1 再让 108 的来源上限掉到 6
    会挪动旧泛化段的 block，这里用冻结算式值逐段对账。
    """
    count = 108
    block = STANDARD_108_BLOCK
    assert block == 1_462_431_296_653_352, "7 张来源的段容量是 W4 时代冻结的算式值"
    assert (
        abs(virtual_card_id(kind=int(VirtualKind.PEACH), sources=(), card_count=count))
        == 1 + 2 * block
    )
    assert (
        abs(
            virtual_card_id(
                kind=int(VirtualKind.PEACH), sources=(18,), card_count=count
            )
        )
        == 1 + 2 * block + (1 * 109 + 18)
    )
    assert (
        abs(
            virtual_card_id(
                kind=int(VirtualKind.INDULGENCE), sources=(), card_count=count
            )
        )
        == 1 + 3 * block
    )
    assert (
        abs(virtual_card_id(kind=int(VirtualKind.DUEL), sources=(), card_count=count))
        == 1 + 4 * block
    )


def test_the_fire_slash_kind_opens_the_next_generalized_block_without_overlap():
    """新 kind 整段排在旧泛化段之后：``[1+5*block, 6*block]``，且上界仍在线内。

    ``min(|id|) = 1 + 5 * block`` 恰是【决斗】段上界 + 1；段容量仍按 **7 张来源**
    （108）/ **6 张来源**（160）取紧界——追加 kind 只加段数，不改来源上限。
    """
    for count, block in ((108, STANDARD_108_BLOCK), (160, MILITARY_160_BLOCK)):
        new_min = abs(
            virtual_card_id(kind=FIRE_KIND, sources=(), card_count=count)
        )
        assert new_min == 1 + 5 * block, (count, new_min)
        assert 6 * block < WIRE_INT_LIMIT, (
            f"{count} 张档的全体 kind 上界 6*block 必须仍 < WIRE_INT_LIMIT"
        )


def test_the_fire_slash_kind_is_wire_safe_and_does_not_collide_with_the_old_kinds():
    """108 / 160 两档：新 kind × 0..来源上限的端点 id 全部在线内且不撞旧 kind。"""
    for count, ceiling in ((108, 7), (160, 6)):
        seen: dict[int, tuple[int, tuple[int, ...]]] = {}
        for kind in range(LEGACY_KIND_COUNT, FIRE_KIND + 1):
            for size in range(ceiling + 1):
                sources = tuple(range(count - size, count))  # 编码最大的端点
                card_id = virtual_card_id(
                    kind=kind, sources=sources, card_count=count
                )
                assert abs(card_id) < WIRE_INT_LIMIT, (count, kind, sources, card_id)
                assert card_id not in seen, (
                    f"{count} 张档撞 id：{card_id} 同时来自 {seen[card_id]} 与 "
                    f"{(kind, sources)}"
                )
                seen[card_id] = (kind, sources)


def test_the_source_ceilings_stay_seven_for_108_and_six_for_160_after_the_new_kind():
    """追加 kind 后来源上限的读数不变：108 = 7、160 = 6（每个 kind 都产得出 id）。"""
    for count, ceiling in ((108, 7), (160, 6)):
        for kind in KINDS:
            virtual_card_id(
                kind=kind, sources=tuple(range(ceiling)), card_count=count
            )
            with pytest.raises(VirtualSourceError):
                virtual_card_id(
                    kind=kind, sources=tuple(range(ceiling + 1)), card_count=count
                )


def test_a_fire_slash_virtual_product_is_constructible_in_the_160_deck():
    """160 档能构造火【杀】产物：单源继承花色 / 点数 / 颜色，id 在线内且不与普通【杀】撞。"""
    pack = load_content_pack(deck_profile="standard_ex_160")
    state = build_state(pack=pack, seats=[SeatSpec(), SeatSpec()], turn_seat=0)
    source = next(card.card_id for card in state.cards if card.type == "fire_slash")
    origin = state.card(source)

    product = transformed_card(state, (source,), "fire_slash")

    assert product.type == "fire_slash" and product.is_virtual
    assert product.name == "火杀"
    assert product.virtual_sources == (source,)
    assert (product.suit, product.rank, product.color) == (
        origin.suit,
        origin.rank,
        origin.color,
    )
    assert abs(product.card_id) < WIRE_INT_LIMIT
    assert product.card_id != transformed_card(state, (source,), "slash").card_id, (
        "同一来源的普通【杀】产物与火【杀】产物必须分处两个 kind 段"
    )
