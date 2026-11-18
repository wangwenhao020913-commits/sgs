"""棋盘的纯函数（M7 票 16；图语义照票 03 §2）：入口 / 出口 / ``available_next`` / 大节推进。

这一层只吃「大节快照 + 进度 + 标签」，**每次现算**：

- 入口 = ``col == 0`` 的节点（**可多个**）；
- 出口 = ``col == cols-1`` 的节点（**可多个**，Boss 不限于一个）；
- 可到达集 = 当前节点的出边里满足 ``requires{flags, not_flags}`` 的那些，再滤掉已
  ``completed`` / ``closed`` 的目标；
- 当前大节 = 出口节点结算完 ⇒ 推进一节；最后一节 ⇒ 通关。

「当前节点」也**现算**（:func:`current_node`）：``pending`` 非空 = 它就是当前；否则取本大节
里**最靠后**（``col`` 最大）的已结算节点；都没有 = 还没进过本大节的入口。

**不落盘派生值**（票 04 §0 纪律 1）：``available_next``、入口、出口、当前节点全部由本模块
从「快照 + ``RunState`` 的进度字段」算出来，存档里没有第二份真相。

**死路不返回空集**（票 16 的红-绿判据）：:func:`available_next` 的合同是"要么给出**非空**
候选，要么抛错"。三种结局互不混淆：

``tuple[Node, ...]``  非空候选（正常：选一个进入）。
:class:`DeadEndError` 这轮走不下去了（无出边且非出口、出边全被标签挡住、出边全指向已结算
                      节点、出口已关闭、大节没有入口）——``reason`` 写明是哪一条。
:class:`LayerCleared` 本大节已打通（出口已结算）：改走 :func:`next_layer_index`。

三条读法（票面没写死，本模块按此口径钉住，见票 16 的 ``## Answer``）：

1. **``pending`` 非空 = 在节点里**：此时问"下一步能去哪"是问早了，抛
   ``script.board.node_in_progress``（完成 / 失败由结算决定，结算后 ``pending`` 才清）。
2. **"最靠后的已结算节点"**：单一进度链上 ``col`` 严格递增（边只许 ``to.col > from.col``），
   故取 ``(col, row, id)`` 最大者是确定答案；同列多个的进度本身自相矛盾，函数仍给确定结果，
   不抛错（那是编辑期校验 / 加载器的事）。
3. **候选顺序 = ``(col, row, id)``**：与边的书写顺序无关，便于测试与前端稳定渲染。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from sgs.data import Edge, Layer, Node, Requirement, Script

from .errors import DeadEndError, LayerCleared, ScriptError
from .state import NodeStatus, Pending, progress_key

__all__ = [
    "available_next",
    "current_node",
    "entry_nodes",
    "exit_nodes",
    "layer_cleared",
    "next_candidates",
    "next_layer_index",
    "outgoing_edges",
    "requirement_met",
]

_EMPTY_STATUS: Final[Mapping[str, NodeStatus | str]] = MappingProxyType({})
"""``node_status`` 的缺省值（空进度）。用不可变映射做缺省，避免可变缺省值的老坑。"""

_SETTLED: Final = (NodeStatus.COMPLETED, NodeStatus.CLOSED)
"""两种落地状态：都不再作为候选（票 04 §1）。"""


def requirement_met(requirement: Requirement, flags: Iterable[str] = ()) -> bool:
    """标签条件成立吗（票 03 §2）：``flags`` 必须都在、``not_flags`` 必须都不在。"""
    held = frozenset(flags)
    if any(flag not in held for flag in requirement.flags):
        return False
    return not any(flag in held for flag in requirement.not_flags)


def entry_nodes(layer: Layer) -> tuple[Node, ...]:
    """本大节的入口（``col == 0``，可多个），按 ``(col, row, id)`` 排序。"""
    return _ordered(node for node in layer.nodes if node.col == 0)


def exit_nodes(layer: Layer) -> tuple[Node, ...]:
    """本大节的出口（``col == cols-1``，可多个），按 ``(col, row, id)`` 排序。

    出口 **不限于一个**：多出口 = 多终局入口，剧情差异由出口节点自身内容 + 标签表达
    （票 03 §2，不另建结局系统）。
    """
    return _ordered(node for node in layer.nodes if node.col == layer.grid.cols - 1)


def outgoing_edges(layer: Layer, node_id: str) -> tuple[Edge, ...]:
    """节点 ``node_id`` 的出边，**保持作者书写顺序**（编辑器里画边的顺序即阅读顺序）。"""
    _require_node(layer, node_id)
    return tuple(edge for edge in layer.edges if edge.from_ == node_id)


def current_node(
    layer: Layer,
    *,
    node_status: Mapping[str, NodeStatus | str] = _EMPTY_STATUS,
    pending: Pending | None = None,
) -> Node | None:
    """当前所在节点；``None`` = 还没进过本大节（在入口前）。

    ``pending`` 非空 ⇒ 它就是"唯一的当前"（票 04 §1）；否则取本大节里 ``(col, row, id)``
    最大的已结算节点。
    """
    if pending is not None:
        return _pending_node(layer, pending)
    settled = [
        node
        for node in layer.nodes
        if _status(node_status, progress_key(layer.id, node.id)) in _SETTLED
    ]
    if not settled:
        return None
    return max(settled, key=lambda node: (node.col, node.row, node.id))


def available_next(
    layer: Layer,
    *,
    flags: Iterable[str] = (),
    node_status: Mapping[str, NodeStatus | str] = _EMPTY_STATUS,
    pending: Pending | None = None,
) -> tuple[Node, ...]:
    """从当前节点能进入的下一批节点（**非空**，否则抛错：见模块文档的三条结局）。

    :raises ScriptError: 在节点里（``pending`` 非空）时问"下一步"——问早了。
    :raises DeadEndError: 死路（原因见 ``reason``）。
    :raises LayerCleared: 本大节已打通，改走 :func:`next_layer_index`。
    """
    if pending is not None:
        raise ScriptError(
            rule_id="script.board.node_in_progress",
            loc=pending.node_id,
            reason="当前节点尚未结算：先结算（票 17）再算可到达集",
        )
    node = current_node(layer, node_status=node_status)
    if node is None:
        return _entry_candidates(layer, node_status=node_status)
    return next_candidates(layer, node.id, flags=flags, node_status=node_status)


def next_candidates(
    layer: Layer,
    node_id: str,
    *,
    flags: Iterable[str] = (),
    node_status: Mapping[str, NodeStatus | str] = _EMPTY_STATUS,
) -> tuple[Node, ...]:
    """**给定节点**之后的候选集：出边 → 标签过滤 → 去掉已结算目标（票 16 的那一步）。

    这是 ``available_next`` 的具名内核：``available_next`` 先现算"当前是哪个节点"，
    再调它。分开是为了让"某节点的出边被标签挡住 / 全指向已结算节点"这两类死路可以
    **逐个节点**核对（编辑期的死路警告也吃这一层，票 24）。

    本函数**不**要求该节点已结算："从这里出发能去哪"对未结算的节点同样成立；结算与否
    只影响**出口**这一格——出口没有出边，它的三种状态各有各的结局：

    - ``completed`` ⇒ :class:`LayerCleared`（本大节已打通）；
    - ``closed`` ⇒ :class:`DeadEndError`（大节既打不通也走不下去）；
    - 还没结算 ⇒ ``script.board.node_in_progress``（结算之后才谈"下一步"）。
    """
    node = _require_node(layer, node_id)
    key = progress_key(layer.id, node.id)
    edges = outgoing_edges(layer, node.id)
    if not edges:
        if node.col == layer.grid.cols - 1:
            status = _status(node_status, key)
            if status is NodeStatus.COMPLETED:
                raise LayerCleared(
                    rule_id="script.board.layer_cleared",
                    loc=key,
                    reason=(
                        f"出口节点 {node.id!r} 已结算：本大节已打通，"
                        "用 next_layer_index 推进（票 04 §6）"
                    ),
                )
            if status is NodeStatus.CLOSED:
                raise DeadEndError(
                    rule_id="script.board.dead_end.closed_exit",
                    loc=key,
                    reason=(
                        f"出口节点 {node.id!r} 已关闭（战败）且没有出边："
                        "本大节既打不通，也走不下去"
                    ),
                )
            raise ScriptError(
                rule_id="script.board.node_in_progress",
                loc=key,
                reason=f"出口节点 {node.id!r} 尚未结算：结算之后本大节才谈得上打通（票 04 §6）",
            )
        raise DeadEndError(
            rule_id="script.board.dead_end.no_outgoing_edges",
            loc=key,
            reason=(
                f"节点 {node.id!r} 没有出边，也不是出口"
                f"（它在 col == {node.col}，出口在 col == {layer.grid.cols - 1}）：路线在这里断了"
            ),
        )
    passing = tuple(edge for edge in edges if requirement_met(edge.requires, flags))
    if not passing:
        raise DeadEndError(
            rule_id="script.board.dead_end.flags_blocked",
            loc=key,
            reason=(
                f"节点 {node.id!r} 的 {len(edges)} 条出边全被标签条件挡住"
                f"（{_blocked_summary(edges, flags)}）"
            ),
        )
    candidates = _open_targets(
        layer,
        tuple(_require_node(layer, edge.to) for edge in passing),
        node_status=node_status,
    )
    if not candidates:
        raise DeadEndError(
            rule_id="script.board.dead_end.all_settled",
            loc=key,
            reason=(
                f"节点 {node.id!r} 的 {len(passing)} 条出边全指向已结算的节点：没有下一步可走"
            ),
        )
    return candidates


def layer_cleared(
    layer: Layer, *, node_status: Mapping[str, NodeStatus | str] = _EMPTY_STATUS
) -> bool:
    """本大节是否已打通：**任一**出口节点已 ``completed``（多出口先到先得，票 03 §2）。"""
    return any(
        _status(node_status, progress_key(layer.id, node.id)) is NodeStatus.COMPLETED
        for node in exit_nodes(layer)
    )


def next_layer_index(
    script: Script,
    *,
    layer_index: int,
    node_status: Mapping[str, NodeStatus | str] = _EMPTY_STATUS,
) -> int | None:
    """结算之后应该待在哪一节：没打通 ⇒ 原值；打通 ⇒ +1；最后一节打通 ⇒ ``None``（通关）。

    打通 = :func:`layer_cleared`；``None`` 是"本轮结束"（票 04 §6：结束即删档）。
    """
    layers = script.layers
    if not layers:
        raise ScriptError(
            rule_id="script.board.no_layers",
            loc="/",
            reason="剧本没有任何大节：没有可玩的棋盘",
        )
    if not 0 <= layer_index < len(layers):
        raise ScriptError(
            rule_id="script.board.layer_index_out_of_range",
            loc=f"layers[{layer_index}]",
            reason=f"layer_index 必须落在 [0, {len(layers)}) 内：实得 {layer_index}",
        )
    if not layer_cleared(layers[layer_index], node_status=node_status):
        return layer_index
    following = layer_index + 1
    return following if following < len(layers) else None


# ---------------------------------------------------------------------------
# 内部：候选集的三条出口（入口 / 出边 / 死路）
# ---------------------------------------------------------------------------


def _entry_candidates(
    layer: Layer, *, node_status: Mapping[str, NodeStatus | str]
) -> tuple[Node, ...]:
    """开局（或新大节）的候选 = 入口，滤掉已结算的。"""
    entries = entry_nodes(layer)
    if not entries:
        raise DeadEndError(
            rule_id="script.board.dead_end.no_entry",
            loc=layer.id,
            reason=f"大节 {layer.id!r} 没有入口节点（col == 0）：从开局就无法推进",
        )
    candidates = _open_targets(layer, entries, node_status=node_status)
    if not candidates:
        raise DeadEndError(
            rule_id="script.board.dead_end.all_settled",
            loc=layer.id,
            reason=f"大节 {layer.id!r} 的入口都已结算，却算不出当前节点：进度自相矛盾",
        )
    return candidates


def _open_targets(
    layer: Layer,
    nodes: Iterable[Node],
    *,
    node_status: Mapping[str, NodeStatus | str],
) -> tuple[Node, ...]:
    """滤掉已 ``completed`` / ``closed`` 的目标，按 ``(col, row, id)`` 排序。"""
    return _ordered(
        node
        for node in nodes
        if _status(node_status, progress_key(layer.id, node.id)) not in _SETTLED
    )


# ---------------------------------------------------------------------------
# 内部：小工具
# ---------------------------------------------------------------------------


def _ordered(nodes: Iterable[Node]) -> tuple[Node, ...]:
    return tuple(sorted(nodes, key=lambda item: (item.col, item.row, item.id)))


def _require_node(layer: Layer, node_id: str) -> Node:
    for node in layer.nodes:
        if node.id == node_id:
            return node
    raise ScriptError(
        rule_id="script.board.node_not_found",
        loc=progress_key(layer.id, node_id),
        reason=f"大节 {layer.id!r} 里没有节点 {node_id!r}",
    )


def _pending_node(layer: Layer, pending: Pending) -> Node:
    layer_id, _, node_id = pending.node_id.partition("/")
    if layer_id != layer.id:
        raise ScriptError(
            rule_id="script.board.pending_outside_layer",
            loc=pending.node_id,
            reason=(
                f"pending 落在错误的大节 {layer_id!r}：当前大节是 {layer.id!r}"
                f"（进度键形如 {progress_key(layer.id, '<node_id>')}）"
            ),
        )
    return _require_node(layer, node_id)


def _status(
    node_status: Mapping[str, NodeStatus | str], key: str
) -> NodeStatus | None:
    raw = node_status.get(key)
    if raw is None:
        return None
    try:
        return NodeStatus(raw)
    except ValueError as exc:
        raise ScriptError(
            rule_id="script.board.unknown_node_status",
            loc=key,
            reason=(
                f"节点状态 {raw!r} 不是 {NodeStatus.COMPLETED.value!r} / "
                f"{NodeStatus.CLOSED.value!r}"
            ),
        ) from exc


def _blocked_summary(edges: tuple[Edge, ...], flags: Iterable[str]) -> str:
    """把"为什么全被挡住"说清楚：缺哪些标签、被哪些标签禁掉。"""
    held = frozenset(flags)
    missing = sorted(
        {flag for edge in edges for flag in edge.requires.flags if flag not in held}
    )
    forbidden = sorted(
        {flag for edge in edges for flag in edge.requires.not_flags if flag in held}
    )
    parts: list[str] = []
    if missing:
        parts.append("缺标签 " + " / ".join(missing))
    if forbidden:
        parts.append("禁标签 " + " / ".join(forbidden))
    return "；".join(parts) if parts else "条件互相矛盾"
