"""剧本模式的**规则层**（M7 第 9 子包）：run 的纯状态机。

本子包只做纯函数：没有 I/O、没有时钟、没有线程（``src/sgs/**`` 全域禁
``time/datetime/uuid/threading/...``，见 ``tests/test_source_discipline.py``）。
文件的读写与 HTTP 端点住 ``tools/sgs_web/``（票 19），存档序列化住票 18。

八个模块：

- :mod:`sgs.script.state` —— :class:`RunState` 与 ``pending.spec`` 的五种形状（纯数据）；
- :mod:`sgs.script.board` —— 棋盘纯函数：入口 / 出口 / ``available_next`` / 大节推进，
  每次现算、不落盘；
- :mod:`sgs.script.errors` —— 规则层的失败（死路 / 大节已打通 / 引用不合法），带可读原因；
- :mod:`sgs.script.rng` —— 九条子流（票 03 §5）与两个取数工具（均匀 / 按权重，均无放回）；
- :mod:`sgs.script.lookup` —— 剧本快照的只读查找（缺项 ⇒ ``ScriptError``）；
- :mod:`sgs.script.rewards` —— 奖励的算术：累加 / 轮次 / 候选 / 价格；
- :mod:`sgs.script.compile` —— ``Room → PveConfig`` 编译 + **进入节点 = 一次抽完**；
- :mod:`sgs.script.settle` —— 结算：奖励发落 / Boss 的固定顺序 / 失败三态。

公开接缝只在此再导出：外部只经 ``sgs.script`` 取名字，不 import 本子包的深层路径
（``tests/test_scaffold.py`` 的跨子包深导扫描兜底）。

一张 run 的生命周期（票 19 组装，不在本子包做 I/O）：

.. code-block:: python

    # 进入节点：先算可到达集，再把该节点的随机一次抽完（票 17）
    candidates = available_next(layer, flags=run.flags, node_status=run.node_status)
    run = enter_node(run=run, library=snapshot, node_id="n3", value_table=locked_table)

    # 节点内：不再取随机（战斗由 spec.battle_seed + spec.pve 起局）

    # 结算：一次落效果 + node_status + layer_index（票 17）
    result = settle_node(run=run, library=snapshot, value_table=locked_table,
                         order=SettleOrder(picks=("hero.x.one", None)))
    if result.run is None:          # 通关 / 失败 ⇒ 删档
        ...
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
from .compile import (
    CompiledBattle,
    ResolvedScale,
    compile_battle,
    enter_node,
    hero_seat,
    resolve_scaling,
)
from .errors import DeadEndError, LayerCleared, ScriptError
from .lookup import (
    event_of,
    event_pool_of,
    global_skill_of,
    layer_of,
    node_of,
    pending_node_of,
    room_of,
    script_of,
    skill_pool_of,
    template_of,
)
from .rewards import (
    BOSS_COINS,
    BOSS_PICK_ROUNDS,
    BOSS_PICK_SIZE,
    EMPTY_POOL_COINS,
    accumulate,
    battle_rounds,
    clean_pool,
    draw_boss_relic,
    draw_candidates,
    enemy_reward,
    round_sizes,
    shop_stock,
    skill_price,
)
from .rng import (
    SCRIPT_SUBSTREAMS,
    battle_stream,
    bonus_stream,
    draw_stream,
    enemy_stream,
    event_stream,
    reward_stream,
    roster_stream,
    shop_stream,
    uniform_sample,
    unknown_stream,
    weighted_sample,
)
from .settle import (
    CoinsGained,
    CoinsLost,
    FlagsSet,
    LoseEquipped,
    NodeClosed,
    PickOffered,
    RelicOffered,
    RelicsGained,
    RunEnded,
    SettleEvent,
    SettleOrder,
    SettleResult,
    SkillsGained,
    settle_node,
)
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
    equipped_skills,
    held_skills,
    progress_key,
)

__all__ = (
    "BACKPACK_SLOTS",
    "BOSS_COINS",
    "BOSS_PICK_ROUNDS",
    "BOSS_PICK_SIZE",
    "EMPTY_POOL_COINS",
    "EQUIPPED_SLOT_COUNT",
    "MAX_RELICS",
    "MAX_ROSTER_CANDIDATES",
    "REWARD_OPTION_COUNT",
    "SCRIPT_SUBSTREAMS",
    "SEED_LIMIT",
    "SHOP_RELIC_OFFERS",
    "SHOP_SKILL_OFFERS",
    "Backpack",
    "BattleSpec",
    "CoinsGained",
    "CoinsLost",
    "CompiledBattle",
    "DeadEndError",
    "EventSpec",
    "FlagsSet",
    "LayerCleared",
    "LoseEquipped",
    "NodeClosed",
    "NodeStatus",
    "Pending",
    "PendingSpec",
    "PickOffered",
    "RelicOffered",
    "RelicsGained",
    "ResolvedScale",
    "Revealed",
    "RevealedSpec",
    "RunEnded",
    "RunState",
    "ScriptError",
    "SettleEvent",
    "SettleOrder",
    "SettleResult",
    "ShopSpec",
    "ShopStock",
    "SkillsGained",
    "UnknownSpec",
    "accumulate",
    "available_next",
    "battle_rounds",
    "battle_stream",
    "bonus_stream",
    "clean_pool",
    "compile_battle",
    "current_node",
    "draw_boss_relic",
    "draw_candidates",
    "draw_stream",
    "enemy_reward",
    "enemy_stream",
    "enter_node",
    "entry_nodes",
    "equipped_skills",
    "event_of",
    "event_pool_of",
    "event_stream",
    "exit_nodes",
    "global_skill_of",
    "held_skills",
    "hero_seat",
    "layer_cleared",
    "layer_of",
    "next_candidates",
    "next_layer_index",
    "node_of",
    "outgoing_edges",
    "pending_node_of",
    "progress_key",
    "requirement_met",
    "resolve_scaling",
    "reward_stream",
    "room_of",
    "roster_stream",
    "round_sizes",
    "script_of",
    "settle_node",
    "shop_stock",
    "shop_stream",
    "skill_pool_of",
    "skill_price",
    "template_of",
    "uniform_sample",
    "unknown_stream",
    "weighted_sample",
)
