"""虚拟牌 id 的线上上限与单射（W4-fix-1）。

线上 id 是 JSON 数字：浏览器 ``JSON.parse`` 只把 ``|id| < 2**53`` 的整数读成精确
整数，超过即变浮点、回传被 pydantic 拒绝（手动验收实测：``-7.006...e+183``）。
故 ``kind >= 2`` 的泛化编码必须压进 ``2**53``，来源张数上限由"可证明 ``< 2**53``"
反推——不是余量，也不是魔法数。

判据全部走**公开缝**：``virtual_card_id`` / ``transformed_card`` / 公开常量。
单射用穷举（单源全牌堆 + 多源抽样与端点）钉住，不靠注释保证。
"""

from __future__ import annotations

from itertools import combinations

import pytest

from sgs.skills import (
    MAX_VIRTUAL_SOURCES,
    VirtualKind,
    VirtualSourceError,
    transformed_card,
    virtual_card_id,
)

from support import SeatSpec, build_state
from test_skills import PACK

WIRE_LIMIT = 2**53
"""JS ``Number`` 能精确表示的最大整数：虚拟 id 的线上天花板（``|id| < WIRE_LIMIT``）。"""

BASE = len(PACK.deck.cards) + 1
"""编码进制 = 牌堆张数 + 1（标准包 108 张 ⇒ 109）。"""

KINDS = tuple(range(len(VirtualKind)))


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
# 来源上限由 2^53 反推（验收 2；不是魔法数）
# ---------------------------------------------------------------------------


def test_the_source_ceiling_is_the_largest_the_wire_limit_allows():
    """``MAX_VIRTUAL_SOURCES`` = 使**全部 kind 段**仍 ``< 2**53`` 的最大来源张数。"""
    assert BASE == 109, "标准包 108 张 ⇒ 进制 109（本票的界推以此为准）"
    admissible = [
        source_count
        for source_count in range(1, 32)
        if len(KINDS) * _capacity(source_count) < WIRE_LIMIT
    ]
    assert admissible, "至少允许一张来源"
    assert MAX_VIRTUAL_SOURCES == max(admissible), (
        f"来源上限应是从 2^53 反推的最大值 {max(admissible)}，实际 {MAX_VIRTUAL_SOURCES}"
    )
    assert len(KINDS) * _capacity(MAX_VIRTUAL_SOURCES) < WIRE_LIMIT
    assert len(KINDS) * _capacity(MAX_VIRTUAL_SOURCES + 1) >= WIRE_LIMIT, (
        "再多一张来源就越过 2^53——上限必须紧到这一步"
    )


def test_every_allowed_kind_and_source_count_stays_under_the_wire_limit():
    """``kind`` 全部取值 × 来源 ``0..MAX`` 张的**端点**：``|id| < 2^53``（断言）。"""
    count = len(PACK.deck.cards)
    source_sets: list[tuple[int, ...]] = [()]
    for size in range(1, MAX_VIRTUAL_SOURCES + 1):
        source_sets.append(tuple(range(size)))  # 最小来源（编码最小）
        source_sets.append(tuple(range(count - size, count)))  # 最大来源（编码最大）
    for kind in KINDS:
        for sources in source_sets:
            card_id = virtual_card_id(kind=kind, sources=sources, card_count=count)
            assert abs(card_id) < WIRE_LIMIT, (
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
    """``kind >= 2``：0..MAX 张的抽样与端点逐对不等，且 ``|id| < 2^53``。"""
    count = len(PACK.deck.cards)
    seen: dict[int, tuple[int, tuple[int, ...]]] = {}
    for kind in KINDS:
        if kind < 2:
            continue
        for sources in sorted(_generalized_samples(count)):
            card_id = virtual_card_id(kind=kind, sources=sources, card_count=count)
            assert abs(card_id) < WIRE_LIMIT, (kind, sources, card_id)
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
