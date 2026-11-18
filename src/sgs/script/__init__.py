"""剧本模式的**规则层**（M7 第 9 子包）：run 的纯状态机。

本子包只做纯函数：没有 I/O、没有时钟、没有线程（``src/sgs/**`` 全域禁
``time/datetime/uuid/threading/...``，见 ``tests/test_source_discipline.py``）。
文件的读写与 HTTP 端点住 ``tools/sgs_web/``（票 19），房间编译与结算住票 17，
存档序列化住票 18。

三个模块：

- :mod:`sgs.script.state` —— :class:`RunState` 与 ``pending.spec`` 的五种形状（纯数据）；
- :mod:`sgs.script.board` —— 棋盘纯函数：入口 / 出口 / ``available_next`` / 大节推进，
  每次现算、不落盘；
- :mod:`sgs.script.errors` —— 规则层的失败（死路 / 大节已打通），带可读原因。

公开接缝只在此再导出：外部只经 ``sgs.script`` 取名字，不 import 本子包的深层路径
（``tests/test_scaffold.py`` 的跨子包深导扫描兜底）。

典型调用（棋盘视图，票 19 组装，不在本票）：

.. code-block:: python

    layer = script_snapshot.layers[run.layer_index]
    if layer_cleared(layer, node_status=run.node_status):
        next_index = next_layer_index(script_snapshot, layer_index=run.layer_index,
                                      node_status=run.node_status)  # None = 通关
    else:
        candidates = available_next(layer, flags=run.flags, node_status=run.node_status)
"""

from __future__ import annotations

from .board import (
    available_next,
    current_node,
    entry_nodes,
    exit_nodes,
    layer_cleared,
    next_candidates,
    next_layer_index,
    outgoing_edges,
    requirement_met,
)
from .errors import DeadEndError, LayerCleared, ScriptError
from .state import (
    BACKPACK_SLOTS,
    EQUIPPED_SLOT_COUNT,
    MAX_RELICS,
    MAX_ROSTER_CANDIDATES,
    REWARD_OPTION_COUNT,
    SEED_LIMIT,
    SHOP_RELIC_OFFERS,
    SHOP_SKILL_OFFERS,
    Backpack,
    BattleSpec,
    EventSpec,
    NodeStatus,
    Pending,
    PendingSpec,
    Revealed,
    RevealedSpec,
    RunState,
    ShopSpec,
    ShopStock,
    UnknownSpec,
    progress_key,
)

__all__ = (
    "BACKPACK_SLOTS",
    "EQUIPPED_SLOT_COUNT",
    "MAX_RELICS",
    "MAX_ROSTER_CANDIDATES",
    "REWARD_OPTION_COUNT",
    "SEED_LIMIT",
    "SHOP_RELIC_OFFERS",
    "SHOP_SKILL_OFFERS",
    "Backpack",
    "BattleSpec",
    "DeadEndError",
    "EventSpec",
    "LayerCleared",
    "NodeStatus",
    "Pending",
    "PendingSpec",
    "Revealed",
    "RevealedSpec",
    "RunState",
    "ScriptError",
    "ShopSpec",
    "ShopStock",
    "UnknownSpec",
    "available_next",
    "current_node",
    "entry_nodes",
    "exit_nodes",
    "layer_cleared",
    "next_candidates",
    "next_layer_index",
    "outgoing_edges",
    "progress_key",
    "requirement_met",
)
