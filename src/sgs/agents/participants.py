"""参与者：一个方法 ``respond(query) -> Action``（票 05 §6）。

M1 的两个实现都是**提前写好的行动逻辑脚本**，不是外部模型：
:class:`RandomPlayer` 是默认对局驱动兼引擎验证工具；``TapePlayer``（回放执行者）
随基准回放落地。
"""

from __future__ import annotations

from typing import Protocol

from sgs.effects import Randomness
from sgs.state import Action, Query

__all__ = ["Participant", "RandomPlayer"]


class Participant(Protocol):
    """引擎只在询问处调用它；入参自带候选行动与该参与者的视角。"""

    def respond(self, query: Query) -> Action: ...


class RandomPlayer:
    """从内联候选里**均匀**取一个；随机只走 ``ai`` 子流（票 08 §3）。"""

    def __init__(self, rng: Randomness) -> None:
        self._rng = rng

    def respond(self, query: Query) -> Action:
        if not query.legal_actions:
            raise ValueError(f"{query.kind} 询问没有候选行动，参与者无法应答")
        index = self._rng.randbelow(len(query.legal_actions))
        return query.legal_actions[index]
