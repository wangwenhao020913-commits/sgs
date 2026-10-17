"""参与者：一个方法 ``respond(query) -> Action``（票 05 §6）。

M1 的实现都是**提前写好的行动逻辑脚本**，不是外部模型：:class:`RandomPlayer`
是默认对局驱动兼引擎验证工具；:class:`TapePlayer` 是回放执行者（票 10 §4）。
:func:`tape_from_journal` 是回放输入侧的纯函数投影（票 10 §7）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sgs.effects import Randomness
from sgs.state import Action, ActivateSkill, Query

__all__ = [
    "Participant",
    "RandomPlayer",
    "TapeError",
    "TapePlayer",
    "is_structured_activation",
    "tape_from_journal",
]


def is_structured_activation(query: Query, action: Action) -> bool:
    """应答是不是"按预发动清单填的复合动作"（W4-4）。

    预发动后复合 ``ActivateSkill`` **不在** ``legal_actions`` 里（`spec.md` §3 的
    "候选 = 接受"唯一让开处）。本谓词只回答"它命中了本次询问下发的清单吗"——真正的
    合法性由引擎的同一个 :func:`sgs.skills.validate_activation` 裁决（非法复合动作会在
    :meth:`Engine.submit` 里被拒并抛错）。回放执行器据此放行磁带上的复合动作；它不是
    "任何 ``ActivateSkill`` 都放行"。

    **必须真的填过表**（``picks`` / ``choices`` 至少一项非空）：W4-2 之前的旧磁带里
    「骨架」``ActivateSkill(skill_id=…, picks=(), choices=())`` 现在已不在候选里，放行它
    会让引擎把空载荷当复合动作校验、把"回放分歧"错报成校验失败。当前所有预发动清单的
    首步都至少收一项，故"填过表"与"命中清单"在这里同义；将来若真有零步清单，它的骨架
    本就不该从候选里删掉。
    """
    if not isinstance(action, ActivateSkill):
        return False
    if not action.picks and not action.choices:
        return False
    manifests = getattr(query, "preactivations", None)
    if isinstance(manifests, dict) and action.skill_id in manifests:
        return True
    return (
        str(getattr(query, "kind", "")) == "SKILL_ACTIVATION"
        and action.skill_id == getattr(query, "skill_id", None)
        and bool(getattr(query, "preactivation", ()))
    )


class Participant(Protocol):
    """引擎只在询问处调用它；入参自带候选行动与该参与者的视角。

    应答由**引擎**在边界校验：结构损坏 / 序号陈旧 / 不在候选集合内一律拒绝
    （票 24），参与者无从绕过内联的合法行动集合。
    """

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

    **复合动作**（W4-4）：预发动后 ``ActivateSkill`` 不在 ``legal_actions`` 里，磁带上的
    那一条由 :func:`is_structured_activation` 按"命中清单"放行；它是否真的合法仍由引擎的
    同一个校验器裁决（不在候选里且不命中清单才是回放分歧）。
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
        if action not in query.legal_actions and not is_structured_activation(
            query, action
        ):
            raise TapeError(f"行动序列分歧：{action.kind} 不在 {query.kind} 的候选集合内")
        self._index += 1
        return action


def tape_from_journal(records: Sequence[object]) -> tuple[int, tuple[Action, ...]]:
    """从记录流抽出 ``(seed, 按行序的 Action 序列)``；纯函数、**不判版本**（票 10 §7 / 票 15 §2）。

    只收**被接受**的应答：被拒的应答（含结构损坏的 ``MalformedAction``）不是回放输入。
    用鸭子类型读记录：依赖方向是 ``engine → agents``，故 ``agents`` 不能 import
    ``engine`` 的记录类型。记录流的字段由票 06 冻结，这里只认 ``kind`` / ``seed`` /
    ``accepted`` / ``action``。
    """
    seed: int | None = None
    actions: list[Action] = []
    for record in records:
        kind = getattr(record, "kind", None)
        if kind == "header":
            seed = record.seed
        elif kind == "action" and record.accepted:
            actions.append(record.action)
    if seed is None:
        raise ValueError("记录流里没有 header，取不到种子")
    return seed, tuple(actions)
