"""决策 diff（票 13；spec §2.3 条件④ / §2.8 的 ``decision_diff``）。

换座配对的两臂（A 局候选坐 0 号、B 局基线坐 0 号、其余四席同一个对手）在**第一个不同步的
应答之前**逐条相同：其余四席是同一份代码、同一批 ``ai:<seat>`` 子流，所以决策序号可以
一一对齐。第一个不同步的**询问类型**就是这次 diff 的归因。

只比 ``(kind, actor, action)``：``index`` 是两边都从 0 起的位置号（对齐后必然相同），
``legal_count`` 由同一份询问派生（对齐前必然相同），比分它们不会多抓一个 bug，只会让
"序号错位"这类真问题被掩盖成"legal_count 不同"。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

__all__ = ["Divergence", "diff_decisions"]


@dataclass(frozen=True)
class Divergence:
    """A / B 两臂的第一个不同步；``*_action`` 为 ``None`` = 该臂在这一步没有应答。"""

    index: int
    kind: str
    actor: int
    candidate_action: dict[str, Any] | None
    baseline_action: dict[str, Any] | None


def _shape(decision: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (
        decision.get("kind"),
        decision.get("actor"),
        decision.get("action"),
    )


def _at(
    index: int,
    *,
    kind: Any,
    actor: Any,
    candidate_action: dict[str, Any] | None,
    baseline_action: dict[str, Any] | None,
) -> Divergence:
    return Divergence(
        index=index,
        kind=str(kind or "?"),
        actor=int(actor) if isinstance(actor, int) else -1,
        candidate_action=candidate_action,
        baseline_action=baseline_action,
    )


def diff_decisions(
    candidate: Sequence[dict[str, Any]],
    baseline: Sequence[dict[str, Any]],
) -> Divergence | None:
    """A / B 两臂的决策序列的第一处不同步；完全相同（含同长）返回 ``None``。"""
    shared = min(len(candidate), len(baseline))
    for index in range(shared):
        left = candidate[index]
        right = baseline[index]
        if _shape(left) != _shape(right):
            return _at(
                index,
                kind=left.get("kind") or right.get("kind"),
                actor=left.get("actor", right.get("actor")),
                candidate_action=left.get("action"),
                baseline_action=right.get("action"),
            )
    if len(candidate) != len(baseline):
        longer = candidate if len(candidate) > len(baseline) else baseline
        extra = longer[shared]
        return _at(
            shared,
            kind=extra.get("kind"),
            actor=extra.get("actor"),
            candidate_action=extra.get("action") if longer is candidate else None,
            baseline_action=extra.get("action") if longer is baseline else None,
        )
    return None
