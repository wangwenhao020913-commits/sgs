"""脚本参与者与推进器：构造局面单测的共用驱动（票 12 起多个族级测试文件共用）。

与 :mod:`support.positions` 的分工：那里造**局面**，这里只驱动**询问**。
"""

from __future__ import annotations

from sgs.state import EndPhase, Pass
from sgs.timing import Timing

__all__ = ["PassPlayer", "find_action", "ran_timing", "run_queries"]


class PassPlayer:
    """对一切询问取最小合法应答：响应 / 濒死放弃、出牌阶段结束、其余取第一个候选。"""

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            return next(a for a in query.legal_actions if isinstance(a, EndPhase))
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
        return query.legal_actions[0]


def find_action(query, predicate):
    """候选里第一个满足 ``predicate`` 的行动；没有则 ``None``。"""
    for action in query.legal_actions:
        if predicate(action):
            return action
    return None


def ran_timing(engine, timing: Timing) -> bool:
    """记录流里有没有落过 ``timing``（推进器的停止条件先例，票 10）。"""
    return any(
        record.kind == "timing" and record.timing is timing for record in engine.records
    )


def run_queries(engine, players, stop, *, limit: int = 8_000):
    """逐步推进引擎到 ``stop(engine)`` 成立；``players`` 按座位下标取脚本参与者。"""
    for _ in range(limit):
        if stop(engine):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")
