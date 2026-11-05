"""唯一随机源（票 08）。

本模块是 ``src/sgs/`` 下**唯一**允许 ``import random`` / ``import secrets`` 的地方；
其余层只拿这里给出的句柄。

- 根 = ``random.Random``（Mersenne Twister），不用模块级 ``random``（隐式共享状态）。
- 子流 = 名字哈希派生（与创建顺序无关）：M1 两条 ``shuffle`` / ``ai``，M2 增身份发放
  ``identity``（票 13 §3）；隐藏槽乱序的 ``select`` 由票 19 落地；随机 AI 自票 12 起
  按座位分名 ``ai:<seat>``（``ai_stream``），一个座位多取数不改写其他座位；
  **武将牌堆抽取**走 ``hero_cards``（``hero_cards_stream``，票 07），**懒取**——不抽
  武将牌的对局一个随机数都不从这里取，既有金录像因此逐字节不变。
  （`GLOSSARY.md` §子流：M2 增身份发放与隐藏槽乱序；随机 AI 按座位分名。）
- 洗牌自己写 Fisher–Yates + 无偏取数：不押在运行时的 ``shuffle`` / 播种实现细节上
  （官方只保证 ``random()`` 的跨版本一致，其余明说 subject to change）。
"""

from __future__ import annotations

import hashlib
import random
import secrets

__all__ = [
    "HERO_CARDS_SUBSTREAM",
    "SUBSTREAM_DOMAIN",
    "RandomSource",
    "ai_stream",
    "hero_cards_stream",
    "new_seed",
]

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


def ai_stream(seed: int, seat: int) -> RandomSource:
    """每个座位一条 ``ai:<seat>`` 子流（票 12；spec §2.10）。

    旧口径是全桌共用 ``substream("ai")``：一条随机队列，任意座位多取一次数都会平移
    其余座位的抽签位置（票 03 实测：一次多余取数改写他座应答的中位数 **195 条/局**）。
    按座位名派生后，一个座位多问一次只影响它自己——这是后续 A/B 读数可比的技术前提。

    ``seat`` 是 0 基座位号；这里只拒绝负数与非整数，座位数的上界由调用方的
    建座循环（``range(player_count)``）保证。
    """
    if isinstance(seat, bool) or not isinstance(seat, int):
        raise ValueError(f"座位必须是整数，得到 {type(seat).__name__}")
    if seat < 0:
        raise ValueError(f"座位不能为负，得到 {seat!r}")
    return RandomSource(seed).substream(f"ai:{seat}")


HERO_CARDS_SUBSTREAM = "hero_cards"
"""武将牌堆抽取的**专用子流名**（票 07；票 02 §4.2 B1）。

与 ``hero``（开局配将）分开：开局配将抽的是"这一局谁上场"（座位池 = 将池 / 禁将 +
手选），而本子流抽的是"从**未加入游戏的武将牌堆**里翻出几张给人看"（池 = 全库真名 −
屏蔽集合）。两条语义不同，共用一条子流会让"抽过一张武将牌"平移整局配将的抽签位置。

**懒取**：派生是名字哈希的纯函数（不消耗任何既有子流），且只有
:meth:`~sgs.engine.game.Engine.draw_hero_cards` 首次真的抽牌时才派生并取数——
不抽武将牌的对局一个随机数都不从这里取，既有金录像因此逐字节不变。
"""


def hero_cards_stream(seed: int) -> RandomSource:
    """武将牌堆抽取的子流（票 07）：调用方 = ``Engine.draw_hero_cards``（懒取）。

    同 :func:`ai_stream` 的姿态：只做名字派生，不碰 ``shuffle`` / ``identity`` /
    ``select`` / ``hero`` 任何一条的位置。
    """
    return RandomSource(seed).substream(HERO_CARDS_SUBSTREAM)
