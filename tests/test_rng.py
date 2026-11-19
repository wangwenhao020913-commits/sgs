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


def test_shuffle_reaches_every_permutation_without_obvious_bias():
    """三张牌的 6 种排列全都出现，且没有哪一种被系统性抬高（票 23 的"无偏"粗检）。

    期望每种 1000 次；6000 次下二项标准差约 29，600–1400 的带子有 ~13σ，不会偶然翻车。
    """
    from itertools import permutations

    counts = {permutation: 0 for permutation in permutations(range(3))}
    source = RandomSource(SEED).substream("shuffle")
    for _ in range(6000):
        items = [0, 1, 2]
        source.shuffle(items)
        counts[tuple(items)] += 1

    assert set(counts) == set(permutations(range(3))), "每种排列都取得到"
    assert all(600 <= count <= 1400 for count in counts.values()), "没有明显偏置"


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
    sample_first = left.substream("sample")
    shuffle_second = left.substream("shuffle")

    right = RandomSource(SEED)
    shuffle_first = right.substream("shuffle")
    sample_second = right.substream("sample")

    assert [shuffle_first.randbelow(100) for _ in range(5)] == [
        shuffle_second.randbelow(100) for _ in range(5)
    ]
    assert [sample_first.randbelow(100) for _ in range(5)] == [
        sample_second.randbelow(100) for _ in range(5)
    ]


def test_consuming_the_ai_stream_does_not_move_the_shuffle_stream():
    """派生规则契约：子流按**名字**切，消耗 ai 流不推进根的随机状态。

    当前实现下这条恒真（派生是纯函数）；它守的是"哪天 substream 改成按创建顺序 /
    消耗根状态"时的回归——那时用同一个 ``root`` 切出的 shuffle 会变。
    """
    root = RandomSource(SEED)
    ai = root.substream("ai")
    for _ in range(50):
        ai.randbelow(10)

    shuffled = list(range(20))
    root.substream("shuffle").shuffle(shuffled)

    fresh = list(range(20))
    RandomSource(SEED).substream("shuffle").shuffle(fresh)

    assert shuffled == fresh


def test_the_nine_script_substreams_do_not_disturb_any_existing_stream():
    """M7 票 17：「新增子流名不扰动既有序列」——剧本模式那九条流消耗多少都不动老名字。

    派生是名字的纯函数（``sha256("sgs/rng/v1:<seed>:<name>")``），故这条在今天的实现下
    恒真；它守的是"哪天 substream 改成按创建顺序 / 消耗根状态"时的回归——那时先切九条
    剧本流再取 ``shuffle`` / ``hero`` / ``ai:0``，逐位都会变（票 03 §5 的自证三条之一）。
    """
    import sgs.script as script_mode

    node_key = "l1/n3"
    factories = (
        lambda: script_mode.roster_stream(SEED),
        lambda: script_mode.bonus_stream(SEED, 0),
        lambda: script_mode.battle_stream(SEED, node_key),
        lambda: script_mode.enemy_stream(SEED, node_key, 2),
        lambda: script_mode.draw_stream(SEED, node_key),
        lambda: script_mode.event_stream(SEED, node_key),
        lambda: script_mode.reward_stream(SEED, node_key, 1),
        lambda: script_mode.unknown_stream(SEED, node_key),
        lambda: script_mode.shop_stream(SEED, node_key),
    )

    def engine_draws() -> list[list[int]]:
        """老名字的抽查：洗牌 / 发武将 / 身份 / 每座 AI。"""
        return [
            [RandomSource(SEED).substream("shuffle").randbelow(1000) for _ in range(3)],
            [RandomSource(SEED).substream("hero").randbelow(1000) for _ in range(3)],
            [RandomSource(SEED).substream("identity").randbelow(1000) for _ in range(3)],
            [RandomSource(SEED).substream("ai:0").randbelow(1000) for _ in range(3)],
        ]

    baseline = engine_draws()
    for factory in factories:
        stream = factory()
        for _ in range(50):
            stream.randbelow(1000)

    assert engine_draws() == baseline, "九条剧本流消耗了随机数，老名字的序列逐位不变"
    assert len(factories) == len(script_mode.SCRIPT_SUBSTREAMS) == 9
