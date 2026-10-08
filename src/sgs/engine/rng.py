"""唯一随机源（票 08）。

本模块是 ``src/sgs/`` 下**唯一**允许 ``import random`` / ``import secrets`` 的地方；
其余层只拿这里给出的句柄。

- 根 = ``random.Random``（Mersenne Twister），不用模块级 ``random``（隐式共享状态）。
- 子流 = 名字哈希派生（与创建顺序无关），M1 两条：``shuffle`` / ``ai``。
- 洗牌自己写 Fisher–Yates + 无偏取数：不押在运行时的 ``shuffle`` / 播种实现细节上
  （官方只保证 ``random()`` 的跨版本一致，其余明说 subject to change）。
"""

from __future__ import annotations

import hashlib
import random
import secrets

__all__ = ["SUBSTREAM_DOMAIN", "RandomSource", "new_seed"]

SEED_LIMIT = 1 << 64

SUBSTREAM_DOMAIN = "sgs/rng/v1"
"""派生域标签；换派生规则就换标签，**不落盘**（票 08 §3 / 票 15）。"""


def new_seed() -> int:
    """现生成一个 64 位种子（全机唯一熵入口；因被记进 header 而不破坏可复现）。"""
    return secrets.randbits(64)


def _check_seed(seed: int) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"种子必须是整数，得到 {type(seed).__name__}")
    if not 0 <= seed < SEED_LIMIT:
        raise ValueError(f"种子必须落在 [0, 2**64) 内，得到 {seed!r}")


class RandomSource:
    """一个确定性的随机源；对外只暴露 ``shuffle`` / ``randbelow`` / ``randint``。"""

    def __init__(self, seed: int) -> None:
        _check_seed(seed)
        self._seed = seed
        self._random = random.Random(seed)

    @classmethod
    def _derived(cls, sub_seed: int) -> "RandomSource":
        """派生流的种子是 128 位哈希值，不适用"游戏种子必须 < 2**64"这条闸门。"""
        source = cls.__new__(cls)
        source._seed = sub_seed
        source._random = random.Random(sub_seed)
        return source

    @property
    def seed(self) -> int:
        return self._seed

    def substream(self, purpose: str) -> "RandomSource":
        """按**用途名**派生独立子流；与创建顺序无关，互不消耗对方的位置。"""
        material = f"{SUBSTREAM_DOMAIN}:{self._seed}:{purpose}".encode("utf-8")
        sub_seed = int.from_bytes(hashlib.sha256(material).digest()[:16], "big")
        return self._derived(sub_seed)

    def randbelow(self, n: int) -> int:
        """无偏返回 ``[0, n)`` 的整数（``getrandbits`` + 拒绝采样）。"""
        if n <= 0:
            raise ValueError(f"randbelow 需要正整数上界，得到 {n!r}")
        bits = (n - 1).bit_length()
        if bits == 0:
            return 0
        while True:
            value = self._random.getrandbits(bits)
            if value < n:
                return value

    def randint(self, a: int, b: int) -> int:
        """无偏返回 ``[a, b]`` 的整数。"""
        if b < a:
            raise ValueError(f"randint 的上界 {b} 小于下界 {a}")
        return a + self.randbelow(b - a + 1)

    def shuffle(self, items: list[int]) -> None:
        """原地 Fisher–Yates（Durstenfeld）：``i`` 自 ``n-1`` 递减，``j = randbelow(i+1)``。"""
        for i in range(len(items) - 1, 0, -1):
            j = self.randbelow(i + 1)
            items[i], items[j] = items[j], items[i]
