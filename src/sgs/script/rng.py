"""剧本模式的**九条子流**与两个取数工具（M7 票 17；票 03 §5 的表）。

随机**只**走 ``RandomSource(seed).substream("<用途>")``（``src/sgs/engine/rng.py``）：
派生是名字的纯函数，与创建顺序无关、不消耗根流，故**新增名字不扰动既有序列**。

九条名字**一个不多一个不少**，字面量只住本模块（与 ``engine/rng.py`` 的
``*_SUBSTREAM`` 同一纪律：调用方拿工厂，不拿名字）：

===========================  ================================================
子流名                       用途
===========================  ================================================
``script.roster``            初始角色候选取 3
``script.bonus:<n>``         「能人志士」类主角的赠送选技（n = 0,1,…）
``script.battle:<节点>``     该场战斗的种子 + 副本全局技能的三档属性缩放
``script.enemy:<节点>:<座位>``  模板的随机技能抽取
``script.draw:<节点>``       按档抽敌人
``script.event:<节点>``      事件池抽事件（后果里的随机也从这里取）
``script.reward:<节点>:<n>`` 第 n 次 k 选 1 的候选抽取（Boss 那 1 件遗物排在最后）
``script.unknown:<节点>``    问号房揭晓
``script.shop:<节点>``       商店库存
===========================  ================================================

**没有 ``script.map:*``**：路线是作者编排的网格，不是随机结果（票 03 §0）。

``<节点>`` = :func:`sgs.script.progress_key` 的进度键 ``"<layer_id>/<node_id>"``（与
``pending.node_id`` 同形）：``Node.id`` 是**层内**编号，两层各有 ``n1`` 是常态，用进度键
才让每条子流在整轮里唯一。

``<座位>`` = **PveConfig 的绝对座位号**（0 号恒为主角；不按友 / 敌各自重编号），
这样"哪条子流属于哪个座位"在 header 里只有一种读法。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from sgs.engine import RandomSource

__all__ = [
    "SCRIPT_SUBSTREAMS",
    "battle_stream",
    "bonus_stream",
    "draw_stream",
    "enemy_stream",
    "event_stream",
    "reward_stream",
    "roster_stream",
    "shop_stream",
    "uniform_sample",
    "unknown_stream",
    "weighted_sample",
]

ROSTER_SUBSTREAM: Final = "script.roster"
"""初始角色候选取 3（票 03 §5）。"""

BONUS_SUBSTREAM: Final = "script.bonus"
"""「能人志士」类主角的赠送选技；``n`` 是第几次赠送。"""

BATTLE_SUBSTREAM: Final = "script.battle"
"""该场战斗的种子（``randbelow(2**64)``），之后是副本全局技能的三档缩放。"""

ENEMY_SUBSTREAM: Final = "script.enemy"
"""某个敌方 / 友方座位的模板随机技能抽取。"""

DRAW_SUBSTREAM: Final = "script.draw"
"""按档抽敌人（同 ``band`` 内无放回）。"""

EVENT_SUBSTREAM: Final = "script.event"
"""事件池抽事件 + 后果里的随机。"""

REWARD_SUBSTREAM: Final = "script.reward"
"""第 ``n`` 次 k 选 1 的候选抽取。"""

UNKNOWN_SUBSTREAM: Final = "script.unknown"
"""问号房揭晓（从加权池里抽一个房间）。"""

SHOP_SUBSTREAM: Final = "script.shop"
"""商店库存（6 技能 + 2 遗物）。"""

SCRIPT_SUBSTREAMS: Final = (
    "script.roster",
    "script.bonus:<n>",
    "script.battle:<节点>",
    "script.enemy:<节点>:<座位>",
    "script.draw:<节点>",
    "script.event:<节点>",
    "script.reward:<节点>:<n>",
    "script.unknown:<节点>",
    "script.shop:<节点>",
)
"""九条名字的**冻结清单**（票 03 §5 的表）：测试拿它核"一个不多一个不少"。"""


def roster_stream(seed: int) -> RandomSource:
    """``script.roster``：初始角色候选取 3（票 03 §5）。"""
    return RandomSource(seed).substream(ROSTER_SUBSTREAM)


def bonus_stream(seed: int, n: int) -> RandomSource:
    """``script.bonus:<n>``：第 ``n`` 次赠送选技。"""
    return RandomSource(seed).substream(f"{BONUS_SUBSTREAM}:{_index(n, 'n')}")


def battle_stream(seed: int, node_id: str) -> RandomSource:
    """``script.battle:<节点>``：这一场的种子与副本全局技能的缩放。"""
    return RandomSource(seed).substream(f"{BATTLE_SUBSTREAM}:{_node(node_id)}")


def enemy_stream(seed: int, node_id: str, seat: int) -> RandomSource:
    """``script.enemy:<节点>:<座位>``：该座位的模板随机技能。"""
    return RandomSource(seed).substream(
        f"{ENEMY_SUBSTREAM}:{_node(node_id)}:{_index(seat, '座位')}"
    )


def draw_stream(seed: int, node_id: str) -> RandomSource:
    """``script.draw:<节点>``：按档抽敌人。"""
    return RandomSource(seed).substream(f"{DRAW_SUBSTREAM}:{_node(node_id)}")


def event_stream(seed: int, node_id: str) -> RandomSource:
    """``script.event:<节点>``：抽事件 + 后果里的随机。"""
    return RandomSource(seed).substream(f"{EVENT_SUBSTREAM}:{_node(node_id)}")


def reward_stream(seed: int, node_id: str, n: int) -> RandomSource:
    """``script.reward:<节点>:<n>``：第 ``n`` 次 k 选 1 的候选（``n`` 从 0 起）。"""
    return RandomSource(seed).substream(
        f"{REWARD_SUBSTREAM}:{_node(node_id)}:{_index(n, 'n')}"
    )


def unknown_stream(seed: int, node_id: str) -> RandomSource:
    """``script.unknown:<节点>``：问号房揭晓。"""
    return RandomSource(seed).substream(f"{UNKNOWN_SUBSTREAM}:{_node(node_id)}")


def shop_stream(seed: int, node_id: str) -> RandomSource:
    """``script.shop:<节点>``：商店库存。"""
    return RandomSource(seed).substream(f"{SHOP_SUBSTREAM}:{_node(node_id)}")


def uniform_sample(
    *, pool: Sequence[str], count: int, source: RandomSource
) -> tuple[str, ...]:
    """从 ``pool`` 里**无放回**均匀抽 ``count`` 个（不够就有几个给几个）。

    均匀抽用于"同档模板"与"技能池"这两处（票 03 §3 / §1：没有权重的条目）。
    逐张 ``randbelow(剩余张数)``，抽走的立刻从队列里去掉 ⇒ 结果里没有重复。
    """
    remaining = list(pool)
    picked: list[str] = []
    while remaining and len(picked) < count:
        picked.append(remaining.pop(source.randbelow(len(remaining))))
    return tuple(picked)


def weighted_sample(
    *,
    pool: Sequence[str],
    weights: Mapping[str, int],
    count: int,
    source: RandomSource,
) -> tuple[str, ...]:
    """从 ``pool`` 里**按权重无放回**抽 ``count`` 个（票 02 §4 的 60/25/12/3）。

    ``weights`` 缺项或为 ``0`` 的条目**永不抽中**（"禁用"的一种表达）；总权重为 0 ⇒
    提前收手（返回已抽到的）。逐张"累计权重轮盘 + 抽走即移除"，故结果里没有重复，
    且抽取权重是**剩余池**的口径（不放回）。
    """
    remaining = list(pool)
    picked: list[str] = []
    while remaining and len(picked) < count:
        wheel = [weights.get(item, 0) for item in remaining]
        total = sum(wheel)
        if total <= 0:
            break
        roll = source.randbelow(total)
        cursor = 0
        for index, weight in enumerate(wheel):
            cursor += weight
            if roll < cursor:
                picked.append(remaining.pop(index))
                break
    return tuple(picked)


def _node(node_id: str) -> str:
    """子流名里的 ``<节点>``：非空（进度键 ``"<layer_id>/<node_id>"``）。"""
    if not isinstance(node_id, str) or not node_id:
        raise ValueError(f"子流名里的节点必须是进度键形式的非空字符串，得到 {node_id!r}")
    return node_id


def _index(value: int, label: str) -> str:
    """子流名里的序号 / 座位号：非负整数（bool 不算整数）。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"子流名里的{label}必须是非负整数，得到 {value!r}")
    return str(value)
