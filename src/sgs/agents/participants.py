"""参与者：一个方法 ``respond(query) -> Action``（票 05 §6）。

M1 的实现都是**提前写好的行动逻辑脚本**，不是外部模型：:class:`RandomPlayer`
是默认对局驱动兼引擎验证工具；:class:`TapePlayer` 是回放执行者（票 10 §4）。
:func:`tape_from_journal` 是回放输入侧的纯函数投影（票 10 §7）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sgs.effects import Randomness
from sgs.state import Action, Query

__all__ = ["Participant", "RandomPlayer", "TapeError", "TapePlayer", "tape_from_journal"]


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


class TapeError(Exception):
    """回放的行动序列与引擎的询问对不上——**这就是分歧信号**（票 05 §6 / 票 10 §4）。"""


class TapePlayer:
    """按序弹出预录 Action；序号失配 / 不在候选内 / 磁带用尽即报错。

    回放 tape 是**全局有序**的（票 10 §1），且引擎整局只有一个待应答询问，
    所以同一个 :class:`TapePlayer` 可以同时驱动两个座位：``engine.run([player, player])``。
    """

    def __init__(self, tape: Sequence[Action]) -> None:
        self._tape = tuple(tape)
        self._index = 0

    def respond(self, query: Query) -> Action:
        if self._index >= len(self._tape):
            raise TapeError(f"行动序列已用尽，但引擎仍在询问 {query.kind}（seq={query.seq}）")
        action = self._tape[self._index]
        if action.answers_seq != query.seq:
            raise TapeError(
                f"行动序列分歧：询问 {query.kind} 的 seq={query.seq}，"
                f"磁带上的应答 answers_seq={action.answers_seq}"
            )
        if action not in query.legal_actions:
            raise TapeError(f"行动序列分歧：{action.kind} 不在 {query.kind} 的候选集合内")
        self._index += 1
        return action


def tape_from_journal(records: Sequence[object]) -> tuple[int, tuple[Action, ...]]:
    """从记录流抽出 ``(seed, 按行序的 Action 序列)``；纯函数、**不判版本**（票 10 §7 / 票 15 §2）。

    用鸭子类型读记录：依赖方向是 ``engine → agents``，故 ``agents`` 不能 import
    ``engine`` 的记录类型。记录流的字段由票 06 冻结，这里只认 ``kind`` / ``seed`` / ``action``。
    """
    seed: int | None = None
    actions: list[Action] = []
    for record in records:
        kind = getattr(record, "kind", None)
        if kind == "header":
            seed = record.seed
        elif kind == "action":
            actions.append(record.action)
    if seed is None:
        raise ValueError("记录流里没有 header，取不到种子")
    return seed, tuple(actions)
