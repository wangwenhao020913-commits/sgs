"""旧版本对"它打 tag 之后才新增的 ``QueryKind``"fail-closed（票 36）。

**问题**（票 32 的 ``## Answer`` / `map.md` D32）：冻结副本的选路表是**它那个提交的**
``_SCORERS``；引擎后来新增的询问类型它没有那一行，``_SCORERS[QueryKind(query.kind)]``
直接 ``KeyError`` ⇒ 整副作废、``errors`` 涨。票 32 的 446 个 errors 就是这么来的
（``ai-v10`` / ``ai-v0`` 都不认识 ``QueryKind.SELECT_OPTIONS``）。

**口径**（票 36）：

1. **冻结副本逐字节不改**——:mod:`ai_eval.versions` 的硬断言之一就是"副本逐字节等于
   ``git show``"，所以兼容层只在**加载 / 驱动侧**，不碰 ``src/sgs/agents/heuristic.py``、
   也不给副本打补丁；
2. 未知类型回一个**该询问下合法**的"放弃 / 空选择"：答案**只从**
   ``query.legal_actions``（引擎按询问结构枚举出的候选）里挑，再过一道
   :func:`sgs.state.parse_action`，**不自己拼**非法答案、也不静默跳过；
3. 这类回答**计数上报**（结果 JSON 的 ``capability_gaps`` / CLI 的 ``gaps=``），
   **不计入** ``errors``——``errors`` 只数真正的崩溃；
4. 选路表**完整**的版本（当前 ``HEAD``）零包装：:func:`fail_closed_player_class` 原样
   返回它，行为逐值不变。``quick`` / ``seal`` / ``live`` 走同一条加载路径，读数不变。

**"合法放弃"的优先序**（:func:`legal_decline`，逐项都要求该动作**已在**候选里）：

1. **空选择**：``SelectCards(picks=())`` / ``SelectOptions(choices=())`` /
   ``Distribute(counts=())``——"一项都不选"，与引擎的"空集排第一 ⇒ 超时兜底 = 一项都
   不做"同一条语义；
2. **恒等答案**：``ArrangeCards`` 的"顺序不动、全放顶上"（引擎恒有一个兜底候选）；
3. **``Pass``**：引擎把"放弃这一步"列成候选的那些询问（响应窗 / 出牌阶段 / 可取消的
   技能发动）；
4. **最短合法候选**：单选项询问（``SELECT_OPTION``）与"至少选 N 个"的多选面**没有**
   放弃出口，此时取候选里载荷最短的一条（单选项 = 引擎候选序的第一条）——它**仍是**
   合法应答，只是不是"放弃"。

逐条都校验 :func:`sgs.state.parse_action` 与"在这一问的候选里"，两者任一不成立即
:class:`~ai_eval.errors.AiEvalSpecError`——兼容层**宁可响亮失败，也不发明答案**。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sgs.state import QueryKind, parse_action

from .errors import AiEvalSpecError

__all__ = [
    "EMPTY_PAYLOAD_FIELDS",
    "capability_gap_kinds",
    "fail_closed_player_class",
    "legal_decline",
]

EMPTY_PAYLOAD_FIELDS: Mapping[str, str] = {
    "SelectCards": "picks",
    "SelectOptions": "choices",
    "Distribute": "counts",
}
"""动作 kind → "空选择"载荷字段（空元组 = 一项都不选；见 :func:`legal_decline`）。"""


def capability_gap_kinds(module: Any) -> tuple[str, ...]:
    """模块的选路表里**缺**的当前 ``QueryKind``（版本级、静态，按枚举序）。

    "缺"就是"这个版本答不了"：``_SCORERS`` 是旧版的唯一分发表，键集里没有的类型只会在
    ``_SCORERS[QueryKind(query.kind)]`` 处 ``KeyError``。没有可解释的分发表（测试桩模块 /
    未来换了结构）⇒ 返回空元组——**不**声称它有缺口，也不因此把它的 ``respond`` 包一层
    （没有依据的包装会掩盖真崩溃）。
    """
    scorers = getattr(module, "_SCORERS", None)
    if not isinstance(scorers, Mapping):
        return ()
    supported: set[QueryKind] = set()
    for key in scorers:
        try:
            supported.add(QueryKind(key))
        except ValueError:
            continue
    if not supported:
        return ()
    return tuple(kind.value for kind in QueryKind if kind not in supported)


def _is_empty_selection(action: Any) -> bool:
    field = EMPTY_PAYLOAD_FIELDS.get(getattr(action, "kind", None))
    return field is not None and not getattr(action, field, ())


def _is_identity_arrangement(action: Any) -> bool:
    """``ArrangeCards`` 的"顺序不动、全放顶上"——引擎恒给的那条兜底候选。"""
    if getattr(action, "kind", None) != "ArrangeCards":
        return False
    order = tuple(getattr(action, "order", ()))
    return order == tuple(range(len(order))) and getattr(action, "split", None) == 0


def _payload_size(action: Any) -> int:
    """候选的载荷长度（``Pass`` / 单选项 = 0）：用于"没有放弃出口"时取最短的一条。"""
    for field in ("picks", "choices", "counts", "order", "count"):
        value = getattr(action, field, None)
        if value is not None:
            return len(value) if hasattr(value, "__len__") else 1
    return 0


def _legal(query: Any, candidates: Sequence[Any], action: Any) -> Any:
    """核过合法性再返回：能过 ``parse_action``、且**就是这一问的候选之一**。"""
    parsed = parse_action(action)
    if parsed not in candidates:
        raise AiEvalSpecError(
            f"兼容层挑的放弃答案不在 {query.kind} 询问的候选里：{parsed!r}"
        )
    return parsed


def legal_decline(query: Any) -> Any:
    """从这一问的 ``legal_actions`` 里挑一个"放弃 / 空选择"（优先序见模块 docstring）。

    **不构造**答案：候选由引擎按询问结构（选项池 / 区间 / 置灰 / 结构档）枚举，故只要
    挑的是候选之一，合法性就是引擎自己的判据，而不是兼容层猜的。
    """
    candidates = tuple(query.legal_actions)
    if not candidates:
        raise AiEvalSpecError(
            f"{query.kind} 询问没有候选行动：兼容层无法给出合法放弃（不发明答案）"
        )
    for predicate in (_is_empty_selection, _is_identity_arrangement):
        for action in candidates:
            if predicate(action):
                return _legal(query, candidates, action)
    for action in candidates:
        if getattr(action, "kind", None) == "Pass":
            return _legal(query, candidates, action)
    return _legal(query, candidates, min(candidates, key=_payload_size))


def fail_closed_player_class(
    player_class: type, gap_kinds: Sequence[str], *, ref: str
) -> type:
    """把缺分发表的版本类包一层：未知类型走 :func:`legal_decline`，其余原样。

    ``gap_kinds`` 为空（分发表完整 = 当前 HEAD）⇒ **原样返回**同一个类，零行为变化。
    包装只覆盖 ``respond`` 的这一条早退，不复制旧版的任何打分代码；冻结副本本身仍是
    ``git show`` 的字节（:mod:`ai_eval.versions` 的三条硬断言一条不动）。
    """
    gaps = frozenset(gap_kinds)
    if not gaps:
        return player_class
    where = ref

    class FailClosedVersion(player_class):  # type: ignore[misc, valid-type]
        """旧版启发式 + 未知询问类型的合法放弃（票 36；只活在加载侧）。"""

        CAPABILITY_GAP_KINDS = tuple(gap_kinds)
        CAPABILITY_GAP_REF = where

        def respond(self, query: Any) -> Any:
            if getattr(query, "kind", None) in gaps:
                return legal_decline(query)
            return super().respond(query)

    # 名字 / 模块名指回被包装的那个类：`inspect.getfile` 仍解析到 runs 副本
    # （versions.py 硬断言 3 的读取路径），台账 / 调试里看到的仍是版本自己的类名。
    FailClosedVersion.__name__ = player_class.__name__
    FailClosedVersion.__qualname__ = player_class.__qualname__
    FailClosedVersion.__module__ = player_class.__module__
    return FailClosedVersion
