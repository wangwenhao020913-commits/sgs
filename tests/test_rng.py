"""唯一随机源：确定性、无偏取数、自写 Fisher–Yates、按用途名派生的子流（票 08）。

落点 = ``sgs.engine.RandomSource`` 的公开面（引擎门面的一部分）。
"""

from __future__ import annotations

import pytest

from sgs.engine import RandomSource, new_seed

SEED = 0x1234_5678_9ABC_DEF0


def test_same_seed_gives_the_same_permutation():
    first = list(range(53))
    second = list(range(53))

    RandomSource(SEED).substream("shuffle").shuffle(first)
    RandomSource(SEED).substream("shuffle").shuffle(second)

    assert first == second
    assert sorted(first) == list(range(53)), "洗牌只是置换，不增不减"


def test_different_seeds_generally_differ():
    first = list(range(53))
    second = list(range(53))

    RandomSource(1).substream("shuffle").shuffle(first)
    RandomSource(2).substream("shuffle").shuffle(second)

    assert first != second


def test_shuffle_is_our_own_durstenfeld_fisher_yates():
    """手算的期望置换：``randbelow`` 序列 [0, 1, 0] 作用在 [0,1,2,3] 上。"""

    class Fixed(RandomSource):
        def __init__(self, values):
            super().__init__(0)
            self._values = iter(values)

        def randbelow(self, n: int) -> int:
            return next(self._values)

    items = [0, 1, 2, 3]
    Fixed([0, 1, 0]).shuffle(items)

    assert items == [2, 3, 1, 0]


def test_randbelow_is_bounded_and_rejects_nonpositive_bounds():
    source = RandomSource(SEED)

    assert source.randbelow(1) == 0
    assert all(0 <= source.randbelow(7) < 7 for _ in range(200))
    with pytest.raises(ValueError):
        source.randbelow(0)


def test_randint_stays_inside_the_closed_interval():
    source = RandomSource(SEED)

    assert all(3 <= source.randint(3, 3) <= 3 for _ in range(5))
    assert all(-2 <= source.randint(-2, 5) <= 5 for _ in range(200))
    with pytest.raises(ValueError):
        source.randint(5, 4)


def test_seed_must_fit_in_64_bits():
    with pytest.raises(ValueError):
        RandomSource(-1)
    with pytest.raises(ValueError):
        RandomSource(1 << 64)


def test_new_seed_is_a_64_bit_integer():
    seed = new_seed()

    assert isinstance(seed, int)
    assert 0 <= seed < (1 << 64)


def test_substreams_are_derived_by_name_not_by_creation_order():
    left = RandomSource(SEED)
    ai_first = left.substream("ai")
    shuffle_second = left.substream("shuffle")

    right = RandomSource(SEED)
    shuffle_first = right.substream("shuffle")
    ai_second = right.substream("ai")

    assert [shuffle_first.randbelow(100) for _ in range(5)] == [
        shuffle_second.randbelow(100) for _ in range(5)
    ]
    assert [ai_first.randbelow(100) for _ in range(5)] == [
        ai_second.randbelow(100) for _ in range(5)
    ]


def test_consuming_the_ai_stream_does_not_move_the_shuffle_stream():
    source = RandomSource(SEED)
    for _ in range(50):
        source.substream("ai").randbelow(10)

    shuffled = list(range(20))
    source.substream("shuffle").shuffle(shuffled)

    fresh = list(range(20))
    RandomSource(SEED).substream("shuffle").shuffle(fresh)

    assert shuffled == fresh
