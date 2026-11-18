"""棋盘的纯函数（M7 票 16；票 03 §2 的图语义 + 票 04 §1 的字段表）。

这一层只吃「大节快照 + 进度 + 标签」，**每次现算**：入口（``col == 0``）/ 出口
（``col == cols-1``，均可多个）/ ``available_next`` / 当前大节推进。``available_next`` 与
入口 / 出口**不进存档**（票 04 §0 纪律 1：落盘的派生值会变成第二份真相）。

**死路不是空集**（票 16 的红-绿判据）：无出边且非出口、出边全被标签挡住、出边全指向已
结算节点、出口已关闭、大节没有入口——一律抛 :class:`DeadEndError` 并带可读原因。
"本大节已打通"是**正常终局**，用 :class:`LayerCleared` 与死路区分开（也不返回空元组）。

测试只经 ``sgs.script`` 公开接缝；图用手搓的 ``Layer``（不读内置剧本——它会随票 28 变）。
"""

from __future__ import annotations

import pytest

from sgs.data import Edge, Grid, Layer, Node, Requirement, Script
from sgs.script import (
    DeadEndError,
    LayerCleared,
    NodeStatus,
    Pending,
    PendingSpec,
    RevealedSpec,
    ScriptError,
    ShopSpec,
    ShopStock,
    UnknownSpec,
    available_next,
    current_node,
    entry_nodes,
    exit_nodes,
    layer_cleared,
    next_candidates,
    next_layer_index,
    outgoing_edges,
    progress_key,
    requirement_met,
)

LAYER_ID = "l1"
"""本节的大节 id（进度键的前缀）。"""

COLS = 4
"""网格列数：``col == 0`` 是入口、``col == COLS-1`` 是出口。"""


def _node(node_id: str, row: int, col: int) -> Node:
    return Node(id=node_id, row=row, col=col, room=f"room.{node_id}")


def _edge(source: str, target: str, requires: Requirement | None = None) -> Edge:
    return Edge(
        **{
            "from": source,
            "to": target,
            "requires": requires or Requirement(),
        }
    )


def _layer() -> Layer:
    """一张两行四列的图，覆盖入口 / 多出口 / 标签条件 / 无出边的死路节点。

    ::

        entry_a ─┬─> mid_a ─┬─[flag.a]─> mid_c ──> exit_a
                 │          └─[!flag.dark]─> mid_d ─┬─> exit_a
        entry_b ─┴─> mid_b（无出边、不是出口）        └─> exit_b

    ``mid_b`` 是**故意**留的死路节点；``exit_a`` / ``exit_b`` 是**多出口**。
    """
    return Layer(
        id=LAYER_ID,
        name="大节一",
        grid=Grid(rows=2, cols=COLS),
        nodes=(
            _node("entry_a", 0, 0),
            _node("entry_b", 1, 0),
            _node("mid_a", 0, 1),
            _node("mid_b", 1, 1),
            _node("mid_c", 0, 2),
            _node("mid_d", 1, 2),
            _node("exit_a", 0, 3),
            _node("exit_b", 1, 3),
        ),
        edges=(
            _edge("entry_a", "mid_a"),
            _edge("entry_a", "mid_b"),
            _edge("entry_b", "mid_a"),
            _edge("mid_a", "mid_c", Requirement(flags=("flag.a",))),
            _edge("mid_a", "mid_d", Requirement(not_flags=("flag.dark",))),
            _edge("mid_c", "exit_a"),
            _edge("mid_d", "exit_a"),
            _edge("mid_d", "exit_b"),
        ),
    )


def _statuses(*settled: tuple[str, NodeStatus]) -> dict[str, NodeStatus]:
    """把 ``("entry_a", COMPLETED)`` 之类的对子铺成 ``node_status``（进度键）。"""
    return {progress_key(LAYER_ID, node_id): state for node_id, state in settled}


def _ids(nodes: tuple[Node, ...]) -> list[str]:
    return [node.id for node in nodes]


# ---------------------------------------------------------------------------
# 入口 / 出口：每次现算、均可多个
# ---------------------------------------------------------------------------


def test_entry_nodes_are_the_nodes_in_column_zero() -> None:
    assert _ids(entry_nodes(_layer())) == ["entry_a", "entry_b"]


def test_exit_nodes_are_the_nodes_in_the_last_column_and_may_be_several() -> None:
    assert _ids(exit_nodes(_layer())) == ["exit_a", "exit_b"]


def test_the_board_starts_at_the_entries() -> None:
    """``pending`` 为空、还没有已结算节点 ⇒ 可到达集 = 本大节入口。"""
    assert _ids(available_next(_layer(), node_status={})) == ["entry_a", "entry_b"]


def test_after_a_settled_node_the_candidates_are_its_outgoing_edges() -> None:
    statuses = _statuses(("entry_a", NodeStatus.COMPLETED))

    assert current_node(_layer(), node_status=statuses).id == "entry_a"
    assert _ids(available_next(_layer(), node_status=statuses)) == ["mid_a", "mid_b"]


def test_outgoing_edges_keep_the_author_order_and_name_unknown_nodes() -> None:
    layer = _layer()

    assert [(edge.from_, edge.to) for edge in outgoing_edges(layer, "mid_d")] == [
        ("mid_d", "exit_a"),
        ("mid_d", "exit_b"),
    ]
    with pytest.raises(ScriptError) as error:
        outgoing_edges(layer, "nope")
    assert error.value.rule_id == "script.board.node_not_found"


# ---------------------------------------------------------------------------
# 标签过滤：requires{flags, not_flags}
# ---------------------------------------------------------------------------


def test_requirement_met_reads_flags_and_not_flags() -> None:
    requirement = Requirement(flags=("flag.a",), not_flags=("flag.dark",))

    assert not requirement_met(requirement, ())
    assert requirement_met(requirement, ("flag.a",))
    assert not requirement_met(requirement, ("flag.a", "flag.dark"))
    assert requirement_met(Requirement(), ())


def test_missing_flags_hide_the_gated_edge_but_not_the_open_one() -> None:
    statuses = _statuses(("entry_a", NodeStatus.COMPLETED), ("mid_a", NodeStatus.COMPLETED))

    assert _ids(available_next(_layer(), node_status=statuses)) == ["mid_d"]
    assert _ids(available_next(_layer(), flags=("flag.a",), node_status=statuses)) == [
        "mid_c",
        "mid_d",
    ]


def test_not_flags_hides_the_forbidden_edge() -> None:
    statuses = _statuses(("entry_a", NodeStatus.COMPLETED), ("mid_a", NodeStatus.COMPLETED))

    assert _ids(
        available_next(_layer(), flags=("flag.a", "flag.dark"), node_status=statuses)
    ) == ["mid_c"]


# ---------------------------------------------------------------------------
# 已完成 / 已关闭：不再作为候选
# ---------------------------------------------------------------------------


def test_a_closed_node_is_not_offered_again_but_the_run_goes_on() -> None:
    """意外战败 ⇒ 节点关闭、整轮继续（票 03 §4）：关闭的节点**不再作为候选**。"""
    statuses = _statuses(("entry_a", NodeStatus.COMPLETED), ("mid_a", NodeStatus.CLOSED))

    assert current_node(_layer(), node_status=statuses).id == "mid_a"
    assert _ids(available_next(_layer(), flags=("flag.a",), node_status=statuses)) == [
        "mid_c",
        "mid_d",
    ]


def test_settled_targets_are_not_offered_again() -> None:
    """出边指向已 ``closed`` 的目标 ⇒ 不再作为候选（逐节点核对：``mid_a`` 之后）。"""
    statuses = _statuses(
        ("mid_a", NodeStatus.COMPLETED),
        ("mid_c", NodeStatus.CLOSED),
    )

    assert _ids(next_candidates(_layer(), "mid_a", flags=("flag.a",), node_status=statuses)) == [
        "mid_d"
    ]


def test_all_targets_settled_is_a_dead_end() -> None:
    """出边全指向已结算目标 ⇒ 死路（不是空集）。"""
    statuses = _statuses(
        ("mid_a", NodeStatus.COMPLETED),
        ("mid_c", NodeStatus.CLOSED),
        ("mid_d", NodeStatus.COMPLETED),
    )

    with pytest.raises(DeadEndError) as error:
        next_candidates(_layer(), "mid_a", flags=("flag.a",), node_status=statuses)

    assert error.value.rule_id == "script.board.dead_end.all_settled"
    assert progress_key(LAYER_ID, "mid_a") in error.value.loc


# ---------------------------------------------------------------------------
# 死路：明确错误 + 可读原因（不是空集）
# ---------------------------------------------------------------------------


def test_a_node_without_outgoing_edges_that_is_not_an_exit_is_a_dead_end() -> None:
    """``mid_b`` 没有出边、也不是出口 ⇒ 报错；静默返回 ``[]`` 会让整轮卡死。"""
    statuses = _statuses(("entry_a", NodeStatus.COMPLETED), ("mid_b", NodeStatus.COMPLETED))

    with pytest.raises(DeadEndError) as error:
        available_next(_layer(), node_status=statuses)

    assert error.value.rule_id == "script.board.dead_end.no_outgoing_edges"
    assert "mid_b" in error.value.reason
    assert "出口" in error.value.reason


def test_edges_all_blocked_by_flags_are_a_dead_end_with_a_readable_reason() -> None:
    statuses = _statuses(("entry_a", NodeStatus.COMPLETED), ("mid_a", NodeStatus.COMPLETED))

    with pytest.raises(DeadEndError) as error:
        available_next(_layer(), flags=("flag.dark",), node_status=statuses)

    assert error.value.rule_id == "script.board.dead_end.flags_blocked"
    assert "flag.a" in error.value.reason
    assert "flag.dark" in error.value.reason


def test_a_layer_without_entries_is_a_dead_end() -> None:
    layer = Layer(
        id=LAYER_ID,
        name="无入口大节",
        grid=Grid(rows=1, cols=2),
        nodes=(_node("only", 0, 1),),
    )

    with pytest.raises(DeadEndError) as error:
        available_next(layer, node_status={})

    assert error.value.rule_id == "script.board.dead_end.no_entry"
    assert LAYER_ID in error.value.reason


def test_a_closed_exit_is_a_dead_end_not_an_empty_tuple() -> None:
    """出口节点的遭遇战输了 ⇒ 节点关闭、出边为空、大节又没打通：明确报错。"""
    statuses = _statuses(("mid_d", NodeStatus.COMPLETED), ("exit_a", NodeStatus.CLOSED))

    with pytest.raises(DeadEndError) as error:
        available_next(_layer(), node_status=statuses)

    assert error.value.rule_id == "script.board.dead_end.closed_exit"
    assert "exit_a" in error.value.reason


# ---------------------------------------------------------------------------
# 大节推进：出口结算完 ⇒ 本大节结束（多出口 = 先到先得）
# ---------------------------------------------------------------------------


def test_a_layer_is_cleared_only_by_a_completed_exit() -> None:
    layer = _layer()

    assert not layer_cleared(layer, node_status={})
    assert not layer_cleared(layer, node_status=_statuses(("mid_d", NodeStatus.COMPLETED)))
    assert not layer_cleared(layer, node_status=_statuses(("exit_a", NodeStatus.CLOSED)))
    assert layer_cleared(layer, node_status=_statuses(("exit_a", NodeStatus.COMPLETED)))
    assert layer_cleared(layer, node_status=_statuses(("exit_b", NodeStatus.COMPLETED)))


def test_a_settled_exit_clears_the_layer_instead_of_returning_no_candidates() -> None:
    """已完成的出口 ⇒ ``LayerCleared`` 信号（正常终局，与死路分开）。"""
    statuses = _statuses(("mid_d", NodeStatus.COMPLETED), ("exit_a", NodeStatus.COMPLETED))

    with pytest.raises(LayerCleared) as error:
        available_next(_layer(), node_status=statuses)

    assert error.value.rule_id == "script.board.layer_cleared"
    assert layer_cleared(_layer(), node_status=statuses)


def _script() -> Script:
    """两节：第一节是 :func:`_layer`，第二节是一进一出的最小图。"""
    second = Layer(
        id="l2",
        name="大节二",
        grid=Grid(rows=1, cols=2),
        nodes=(_node("n1", 0, 0), _node("n2", 0, 1)),
        edges=(_edge("n1", "n2"),),
    )
    return Script(
        id="huangjin",
        name="讨伐黄巾军",
        version=1,
        roster=("nengren",),
        layers=(_layer(), second),
    )


def test_the_current_layer_advances_only_after_an_exit_is_cleared() -> None:
    script = _script()
    cleared = _statuses(("exit_a", NodeStatus.COMPLETED))

    assert next_layer_index(script, layer_index=0, node_status={}) == 0
    assert next_layer_index(script, layer_index=0, node_status=cleared) == 1
    assert next_layer_index(script, layer_index=1, node_status={}) == 1


def test_the_run_is_complete_after_the_last_layer_is_cleared() -> None:
    """最后一节打通 ⇒ ``None``（通关；票 04 §6：结束即删档）。"""
    script = _script()

    statuses = {progress_key("l2", "n2"): NodeStatus.COMPLETED}

    assert next_layer_index(script, layer_index=1, node_status=statuses) is None


def test_a_script_without_layers_is_a_readable_error() -> None:
    script = Script(id="empty", name="空剧本", version=1)

    with pytest.raises(ScriptError) as error:
        next_layer_index(script, layer_index=0, node_status={})

    assert error.value.rule_id == "script.board.no_layers"


def test_an_out_of_range_layer_index_is_a_readable_error() -> None:
    with pytest.raises(ScriptError) as error:
        next_layer_index(_script(), layer_index=9, node_status={})

    assert error.value.rule_id == "script.board.layer_index_out_of_range"


# ---------------------------------------------------------------------------
# pending = 唯一的「当前」
# ---------------------------------------------------------------------------


def test_pending_names_the_current_node() -> None:
    layer = _layer()
    statuses = _statuses(("entry_a", NodeStatus.COMPLETED))
    pending = Pending(node_id=progress_key(LAYER_ID, "mid_a"), spec=_pending_spec())

    assert current_node(layer, node_status=statuses, pending=pending).id == "mid_a"


def test_asking_for_the_next_node_while_inside_one_is_a_readable_error() -> None:
    pending = Pending(node_id=progress_key(LAYER_ID, "mid_a"), spec=_pending_spec())

    with pytest.raises(ScriptError) as error:
        available_next(_layer(), node_status={}, pending=pending)

    assert error.value.rule_id == "script.board.node_in_progress"


def test_a_pending_node_from_another_layer_is_a_readable_error() -> None:
    pending = Pending(node_id=progress_key("l2", "n1"), spec=_pending_spec())

    with pytest.raises(ScriptError) as error:
        current_node(_layer(), node_status={}, pending=pending)

    assert error.value.rule_id == "script.board.pending_outside_layer"


def _pending_spec() -> PendingSpec:
    """问号房在 S_enter 态的 spec（形状由 ``tests/test_script_state.py`` 钉住）。"""
    return UnknownSpec(
        kind="unknown",
        revealed=RevealedSpec(room="room.shop", spec=ShopSpec(kind="shop", stock=ShopStock())),
    )


def test_an_unsettled_exit_has_no_next_node_yet() -> None:
    """出口还没结算 ⇒ "下一步"问早了（既不是死路，也不是已打通）。"""
    with pytest.raises(ScriptError) as error:
        next_candidates(_layer(), "exit_a", node_status={})

    assert error.value.rule_id == "script.board.node_in_progress"
    assert not isinstance(error.value, DeadEndError)


def test_raw_json_status_strings_are_accepted() -> None:
    """``node_status`` 的值可以是 JSON 里的字符串（票 04 §7 的示例），不必先转枚举。"""
    assert _ids(available_next(_layer(), node_status={"l1/entry_a": "completed"})) == [
        "mid_a",
        "mid_b",
    ]


def test_an_unknown_node_status_is_a_readable_error() -> None:
    with pytest.raises(ScriptError) as error:
        current_node(_layer(), node_status={progress_key(LAYER_ID, "entry_a"): "banished"})

    assert error.value.rule_id == "script.board.unknown_node_status"
