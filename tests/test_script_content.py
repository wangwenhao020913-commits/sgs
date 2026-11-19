"""内置剧本「讨伐黄巾军」的**内容契约**（M7 票 28；spec §2、§7 与「交内容期」）。

票 14 落的是 schema 合法的**空壳**；本票把它变成**结构完整、数值占位**的最小可玩内容。
本文件钉的是内容层的判据，不是引擎层：

1. **同一份校验器**：内置文件走运行期那条严格加载路（``load_script_library``，
   ``load_builtin_script_library`` 就是它）与编辑器同源的 ``validate_script_library``，
   引用一次全扫为空；
2. **结构完整**：2 大节、16~20 节点、8~12 场战斗、2 个 Boss、五类房间都用过、
   每个 band 至少 2 条模板、两种敌人来源都有、技能池 / 事件 / 事件池 / 全局技能 / 大节曲线
   都有实例；
3. **可达性**：入口在最左列、出口在最右列、每条入口都能走到某个出口、非出口节点没有死路、
   每个节点都能从某个入口到达（忽略标签门；标签门另有断言）；
4. **通关路**：用纯函数（``available_next`` + ``enter_node`` + ``settle_node``）在固定 seed
   下从第一层入口走到最后一层出口并通关，路上覆盖全部五类房间与两个 Boss——票 29 的 e2e
   照这条路线走。
5. **可胜**（票 33）：同一条通关路上的**每一场战斗**（含问号房揭晓出来的战斗）在固定种子 +
   启发式 AI 下真的打得赢（``solve_pve_outcome`` 判 ``ally`` 侧）。票 29 实测票 28 的占位数值
   一局也赢不了（``npc.guard`` 把每次 1 点【杀】吃成 0，而 4 体力主角每轮只摸 2 张），这条
   断言把"数值占位可以，不可胜不行"钉成机械判据；真实平衡仍归内容期（D20）。

内容里的**数值是占位值**（``Script.comment`` 里写明了）：本文件只判"可胜"这条下限，不判平衡，
也不判文案。
"""

from __future__ import annotations

from pathlib import Path

from sgs.agents import HeuristicPlayer
from sgs.data import (
    SCRIPT_LIBRARY_FILENAME,
    EnemyDraw,
    Identity,
    Mode,
    PveVictoryKind,
    RoomKind,
    RuleConfig,
    load_content_pack,
    load_script_library,
    load_skill_values,
    load_builtin_script_library,
    script_digest,
    validate_script_library,
    hero_by_id,
)
from sgs.engine import Engine, ai_stream, solve_pve_outcome
from sgs.script import (
    Backpack,
    BattleSpec,
    EventSpec,
    RunState,
    SettleOrder,
    UnknownSpec,
    available_next,
    compile_battle,
    enter_node,
    entry_nodes,
    exit_nodes,
    layer_cleared,
    requirement_met,
    settle_node,
)
from sgs.skills import CATALOG

BUILTIN_FILE = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "sgs"
    / "data"
    / "files"
    / SCRIPT_LIBRARY_FILENAME
)
"""内置剧本库的仓内路径（与 ``sgs.data.scripts.SCRIPT_LIBRARY_FILENAME`` 同一份文件名）。"""

SEED = 0x28_2026_1109
"""走通关路用的固定 run seed（票 29 的 e2e 可以用任意 seed：路线已钉，随机只影响抽到什么）。"""

CHOSEN_HERO = "jie_liubei"
"""通关路上选定的初始角色（4/4、蜀、男；只影响座位名与体力，不影响本文件的判据）。"""

BATTLE_KINDS = (RoomKind.STORY, RoomKind.BATTLE)
"""带 ``battle`` 载荷的两类房间（Boss 也是这两类之一，只多一个 ``is_boss``）。"""

ROUTE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("l1", ("entry", "village", "market", "boss", "exit")),
    ("l2", ("entry", "question", "vanguard", "boss", "exit")),
)
"""通关路（大节 id → 节点 id）：第一层走剧情战 → 事件 → 商店 → Boss → 出口，
第二层走剧情战 → 问号房 → 遭遇战 → Boss → 出口。两层合起来覆盖五类房间与两个 Boss。"""

WIN_SEEDS: tuple[int, ...] = (20261129, 20261130, 20261131)
"""可胜断言的**固定小样本**（票 33；3 个 run 种子，不是 28 个种子的全扫）。

``20261129`` 就是票 29 的 e2e 用的那一个（``tests/test_script_e2e.py::SEED``），另外两颗
只是让"赢"不依赖单一种子的摸牌顺序。战斗种子由 run 种子 + 节点键派生，故这颗种子遍历的就是
e2e 真要打的那几场。
"""

PACK = load_content_pack(deck_profile="standard_108")
"""起 PvE 局用的牌堆档（与网页 / 票 29 的 e2e 同一条缺省档）。"""


# ---------------------------------------------------------------------------
# 小工具：从内置库取实体索引 / BFS / 一局 S₀
# ---------------------------------------------------------------------------


def _library():
    """内置库（严格加载：与运行期同一条路）。"""
    return load_builtin_script_library()


def _script(library):
    assert [script.id for script in library.scripts] == ["huangjin"], (
        "内置库只有一个剧本「讨伐黄巾军」"
    )
    script = library.scripts[0]
    assert script.name == "讨伐黄巾军"
    return script


def _index(entities):
    """``id → 实体``（每个 id 唯一由加载器保证；这里只用它做内容层的定点检查）。"""
    return {entity.id: entity for entity in entities}


def _run(library, *, layer_index: int, seed: int = SEED) -> RunState:
    """一份合法的 S₀（背包 24 格全空、0 铜钱）：编译与推进都只读它。

    ``seed`` 只有在走多条样本路线时才换（票 33 的固定小样本）；缺省仍是本文件的 ``SEED``。
    """
    script = _script(library)
    return RunState(
        run_id=f"{seed}-1",
        seed=seed,
        script_id=script.id,
        script_version=script.version,
        script_digest=script_digest(script),
        roster_candidates=script.roster,
        chosen_hero=CHOSEN_HERO,
        layer_index=layer_index,
        backpack=Backpack(slots=(None,) * 24),
    )


def _reachable(layer) -> set[str]:
    """从**入口**出发、**忽略标签门**能到达的节点集合（可达性的上界）。"""
    outgoing: dict[str, list[str]] = {}
    for edge in layer.edges:
        outgoing.setdefault(edge.from_, []).append(edge.to)
    starts = [node.id for node in entry_nodes(layer)]
    reached = set(starts)
    stack = list(starts)
    while stack:
        current = stack.pop()
        for target in outgoing.get(current, ()):
            if target not in reached:
                reached.add(target)
                stack.append(target)
    return reached


def _can_reach_exit(layer, source_id: str) -> bool:
    """从 ``source_id`` 出发，**忽略标签门**能否走到某个出口。"""
    exit_ids = {node.id for node in exit_nodes(layer)}
    frontier = [source_id]
    seen = {source_id}
    while frontier:
        current = frontier.pop()
        if current in exit_ids:
            return True
        for edge in layer.edges:
            if edge.from_ == current and edge.to not in seen:
                seen.add(edge.to)
                frontier.append(edge.to)
    return False


def _seat_total(battle) -> tuple[int, int]:
    """座位总数区间（主角 1 + 队友 + 敌人；按档抽用 ``count`` 的两端）。"""
    enemies = battle.enemies
    low_high = (enemies.draw.count if isinstance(enemies, EnemyDraw) else (len(enemies), len(enemies)))
    return 1 + len(battle.allies) + low_high[0], 1 + len(battle.allies) + low_high[1]


def _enemy_seats(battle):
    """固定的敌方座位（按档抽的敌人没有座位可枚举，只有 band）。"""
    enemies = battle.enemies
    return () if isinstance(enemies, EnemyDraw) else enemies


def _order_for(run: RunState) -> SettleOrder:
    """按 ``pending.spec`` 的形状给结算指令：事件选 0 号选项，其余用房型自己的形状。"""
    spec = run.pending.spec
    if isinstance(spec, UnknownSpec):
        spec = spec.revealed.spec
    if isinstance(spec, EventSpec):
        return SettleOrder(option=0)
    return SettleOrder()


# ---------------------------------------------------------------------------
# 1. 结构与五类房间
# ---------------------------------------------------------------------------


def test_the_builtin_script_is_playable_content_not_a_shell() -> None:
    """2 大节 / 16~20 节点 / 8~12 场战斗 / 2 个 Boss：票 28 的数量口径。"""
    library = _library()
    script = _script(library)

    assert len(script.layers) == 2, "2 大节（票 28）"
    rooms = _index(library.rooms)
    nodes = [node for layer in script.layers for node in layer.nodes]
    assert 16 <= len(nodes) <= 20, f"总量约 16~20 节点：实得 {len(nodes)}"

    battle_nodes = [node for node in nodes if rooms[node.room].kind in BATTLE_KINDS]
    assert 8 <= len(battle_nodes) <= 12, f"8~12 场战斗：实得 {len(battle_nodes)}"
    boss_nodes = [node for node in nodes if rooms[node.room].is_boss]
    assert len(boss_nodes) == 2, f"2 个 Boss：实得 {len(boss_nodes)}"

    for layer in script.layers:
        assert layer.name, "每个大节都要有名字"
        assert layer.grid.rows >= 1 and layer.grid.cols >= 2
        assert layer.nodes and layer.edges, "每个大节都要有节点与边"
        assert layer.default_scaling, "每个大节都要有默认缩放曲线（越往后越强）"


def test_every_layer_has_entries_exits_and_no_dead_ends() -> None:
    """入口 = 最左列、出口 = 最右列；每个入口都能走到出口，非出口节点没有死路。"""
    library = _library()
    script = _script(library)

    for layer in script.layers:
        entries = entry_nodes(layer)
        exits = exit_nodes(layer)
        assert entries, f"大节 {layer.id!r} 没有入口"
        assert exits, f"大节 {layer.id!r} 没有出口"
        assert all(node.col == 0 for node in entries), "入口都在最左列"
        assert all(node.col == layer.grid.cols - 1 for node in exits), "出口都在最右列"

        # 非出口节点必须有出边：没有出边且不是出口 = 运行期死路（票 03 §2）。
        with_outgoing = {edge.from_ for edge in layer.edges}
        for node in layer.nodes:
            if node.col != layer.grid.cols - 1:
                assert node.id in with_outgoing, (
                    f"节点 {node.id!r} 在 col {node.col} 没有出边，也不是出口：运行期会死路"
                )

        # 每个节点都能从某个入口走到；每个入口都能走到某个出口（忽略标签门 = 上界）。
        reached = _reachable(layer)
        for entry in entries:
            assert entry.id in reached
        for node in layer.nodes:
            assert node.id in reached, f"节点 {node.id!r} 从任何入口都到不了"
        for entry in entries:
            assert _can_reach_exit(layer, entry.id), (
                f"入口 {entry.id!r} 走不到任何出口（编辑期死路警告）"
            )


def test_every_room_kind_is_used_in_the_shipped_route_graph() -> None:
    """房间五类都要被用到（每类至少一个节点），且节点只指向已登记的房间。"""
    library = _library()
    script = _script(library)
    rooms = _index(library.rooms)
    kinds = {
        rooms[node.room].kind for layer in script.layers for node in layer.nodes
    }

    assert kinds == set(RoomKind), f"五类房间每类至少用一次：实得 {sorted(kinds)}"


# ---------------------------------------------------------------------------
# 2. 引用完整性（同一份校验器）
# ---------------------------------------------------------------------------


def test_the_builtin_file_loads_strictly_and_reports_no_validation_errors() -> None:
    """strict 加载通过 + ``validate_script_library`` 清单为空（运行期与编辑器同源）。"""
    text = BUILTIN_FILE.read_text(encoding="utf-8")

    assert validate_script_library(text, file=SCRIPT_LIBRARY_FILENAME) == ()
    strict = load_script_library(text, file=SCRIPT_LIBRARY_FILENAME, strict=True)
    assert script_digest(strict.scripts[0]) == script_digest(_script(_library()))


def test_every_authored_reference_resolves_to_a_registered_entity() -> None:
    """节点 → 房间、座位 → 模板、模板 → 技能池、池 → 技能、事件池 → 事件等全部解析。"""
    library = _library()
    script = _script(library)
    rooms = _index(library.rooms)
    templates = _index(library.templates)
    skill_pools = _index(library.skill_pools)
    events = _index(library.events)
    event_pools = _index(library.event_pools)
    global_skills = _index(library.global_skills)

    for hero_id in script.roster:
        assert hero_by_id(hero_id) is not None, f"roster 里的 {hero_id!r} 不在任何武将表"

    for layer in script.layers:
        node_ids = {node.id for node in layer.nodes}
        for node in layer.nodes:
            assert node.room in rooms, f"节点 {node.id!r} 指向不存在的房间 {node.room!r}"
        for edge in layer.edges:
            assert edge.from_ in node_ids and edge.to in node_ids, "边的两端必须是本大节的节点"
        for edge in layer.edges:
            for flag in edge.requires.flags:
                assert flag, "标签条件里的标签不能为空"

    for room in library.rooms:
        battle = room.battle
        if battle is not None:
            for seat in (*battle.allies, *_enemy_seats(battle)):
                assert seat.template in templates, (
                    f"房间 {room.id!r} 的座位指向不存在的模板 {seat.template!r}"
                )
                if seat.boss:
                    assert battle.victory.kind is PveVictoryKind.SLAY_BOSS
            for reference in battle.global_skills:
                assert reference.skill in global_skills, (
                    f"房间 {room.id!r} 引用了不存在的全局技能 {reference.skill!r}"
                )
            enemies = battle.enemies
            if isinstance(enemies, EnemyDraw):
                band = enemies.draw.band
                assert any(template.band == band for template in library.templates), (
                    f"房间 {room.id!r} 按档抽的 band {band!r} 没有模板"
                )
        if room.event_pool is not None:
            assert room.event_pool in event_pools, (
                f"房间 {room.id!r} 引用不存在的事件池 {room.event_pool!r}"
            )
        if room.pool is not None:
            for entry in room.pool:
                assert entry.room in rooms, f"问号池引用了不存在的房间 {entry.room!r}"
                assert rooms[entry.room].kind is not RoomKind.UNKNOWN, (
                    "问号池不许递归引用问号房（票 03 §2）"
                )

    for template in library.templates:
        assert template.random.pool in skill_pools, (
            f"模板 {template.id!r} 引用了不存在的技能池 {template.random.pool!r}"
        )
        pool = skill_pools[template.random.pool]
        assert template.random.count[1] <= len(pool.skills), "随机条数不能超过池子"
        for skill_id in (*template.initial_skills, *pool.skills):
            assert skill_id in CATALOG, f"{skill_id!r} 不在 CATALOG 里"

    for pool in library.event_pools:
        for event_id in pool.events:
            assert event_id in events, f"事件池 {pool.id!r} 引用了不存在的事件 {event_id!r}"

    for event in library.events:
        for option in event.options:
            for effect in option.effects:
                if effect.kind == "grant_skill":
                    assert effect.skill in CATALOG, f"事件 {event.id!r} 授予未知技能"
                elif effect.kind == "grant_relic":
                    assert effect.relic in CATALOG, f"事件 {event.id!r} 授予未知遗物"


# ---------------------------------------------------------------------------
# 3. 战斗编排：座位 / 胜负 / 敌人来源 / 模板 band
# ---------------------------------------------------------------------------


def test_every_battle_room_keeps_two_to_five_seats_and_a_legal_victory() -> None:
    """座位总数 2~5；``slay_boss`` 恰好一个 boss 座位；Boss 房共 2 个。"""
    library = _library()
    rooms = [room for room in library.rooms if room.battle is not None]

    seen_boss = 0
    for room in rooms:
        battle = room.battle
        assert battle is not None
        low, high = _seat_total(battle)
        assert 2 <= low and high <= 5, f"房间 {room.id!r} 的座位总数越界：[{low}, {high}]"
        boss_seats = [
            seat for seat in (*battle.allies, *_enemy_seats(battle)) if seat.boss
        ]
        if battle.victory.kind is PveVictoryKind.SLAY_BOSS:
            assert len(boss_seats) == 1, (
                f"房间 {room.id!r} 是 slay_boss，必须恰好一个 boss: true 座位"
            )
        else:
            assert len(boss_seats) <= 1, f"房间 {room.id!r} 有多个 boss 座位却没有 slay_boss"
        if room.is_boss:
            seen_boss += 1
            assert room.kind in BATTLE_KINDS, "Boss 只能是剧情 / 意外战斗房"
            assert battle.victory.kind is PveVictoryKind.SLAY_BOSS, (
                f"Boss 房 {room.id!r} 的胜负必须是 slay_boss"
            )
    assert seen_boss == 2, f"2 个 Boss 房：实得 {seen_boss}"


def test_most_battles_are_solo_and_at_most_two_bring_an_ai_ally() -> None:
    """内容形状（``map.md`` D3；票 35 修正票 33 的过度编排）。

    D3：「多数 1 打 1~4，**少数**带 1 名 AI 队友（最多 2v3）」。票 33 为了把内置剧本从
    "0/28 可胜"救到可胜，把 **9/9 场**都改成「1 敌 + 1 义从」——那是可胜性下限的过度修正。
    本断言把形状钉回来：**单人占多数**（≥5 场无 AI 队友、1 打 1~2），带 AI 义从的**至多 2 场**，
    并且**至少一场是 Boss**。数值仍是占位值（D20），可胜性另由
    :func:`test_every_battle_on_the_authored_route_is_winnable_by_the_heuristic_ai` 钉住。
    """
    library = _library()
    battles = [room for room in library.rooms if room.battle is not None]
    solo = [room for room in battles if not room.battle.allies]
    with_ally = [room for room in battles if room.battle.allies]

    assert len(solo) >= 5, f"单人战斗要占多数（至少 5 场）：实得 {len(solo)} / {len(battles)}"
    assert len(solo) > len(with_ally), "单人战斗必须是多数（不是五五开）"
    assert len(with_ally) <= 2, f"带 AI 义从的至多 2 场：实得 {[room.id for room in with_ally]}"
    assert any(room.is_boss for room in with_ally), "带 AI 义从的少数里至少有一场是 Boss"

    for room in solo:
        assert not room.battle.allies, f"{room.id!r} 应是无 AI 队友的单人战"
        low, high = _seat_total(room.battle)
        assert low == 2, f"{room.id!r} 是 1 打 1~2（主角恒 1 座）：实得 {low}~{high} 座"
        assert high <= 3, f"{room.id!r} 单人战最多 1 打 2：实得 {low}~{high} 座"

    for room in with_ally:
        assert len(room.battle.allies) == 1, (
            f"{room.id!r} 的 AI 义从只带 1 名（D3：最多 2v3）"
        )


def test_every_band_has_two_templates_and_both_enemy_sources_are_used() -> None:
    """每个 band 至少 2 条模板；固定与按档抽两种敌人来源都有；固定对手的池为空。"""
    library = _library()
    bands: dict[str, int] = {}
    for template in library.templates:
        bands[template.band] = bands.get(template.band, 0) + 1
    for band, count in bands.items():
        assert count >= 2, f"band {band!r} 只有 {count} 条模板（至少 2 条）"

    fixed_battles = 0
    drawn_battles = 0
    for room in library.rooms:
        battle = room.battle
        if battle is None:
            continue
        if isinstance(battle.enemies, EnemyDraw):
            drawn_battles += 1
        else:
            fixed_battles += 1
    assert fixed_battles >= 1, "遭遇战里要有固定的敌人编排"
    assert drawn_battles >= 1, "遭遇战里要有按档抽的敌人编排"

    # 固定对手 = random.count = [0, 0] 的模板：剧情战主将与 Boss 都是这一形状。
    fixed = [
        template for template in library.templates if template.random.count == (0, 0)
    ]
    assert len(fixed) >= 2, "剧情战主将 + Boss 至少两条固定对手模板"
    assert all(template.initial_skills for template in fixed), (
        "固定对手的差异化来自写死的初始技能：它不能又空又随机"
    )
    assert any(template.initial_skills for template in library.templates)
    assert any(template.random.count[1] > 0 for template in library.templates), (
        "随机技能抽取要有实例（模板 random.count 的上限 > 0）"
    )


def test_every_battle_room_compiles_into_a_legal_pve_header() -> None:
    """每个战斗房都能在真实快照 + 固定 seed 下编出 2~5 座的 header（不会被加成越界挡住）。"""
    library = _library()
    script = _script(library)
    for layer_index, layer in enumerate(script.layers):
        for node in layer.nodes:
            room = _index(library.rooms)[node.room]
            if room.kind not in BATTLE_KINDS:
                continue
            compiled = compile_battle(
                room=room,
                node=node,
                layer=layer,
                library=library,
                run=_run(library, layer_index=layer_index),
            )
            pve = compiled.pve
            assert 2 <= len(pve.seats) == len(pve.identities) <= 5
            assert pve.principal == 0, "主角恒为 0 号座"
            assert pve.victory == room.battle.victory
            enemy_seats = [
                seat for seat, identity in zip(pve.seats, pve.identities)
                if identity is Identity.ENEMY
            ]
            assert len(enemy_seats) == len(compiled.enemy_templates)
            if room.is_boss:
                assert pve.boss is not None and pve.identities[pve.boss] is Identity.ENEMY
            else:
                assert pve.boss is None


# ---------------------------------------------------------------------------
# 4. 标签 / 缩放 / 共享表实例
# ---------------------------------------------------------------------------


def test_the_flag_gate_is_opened_by_a_cleared_room() -> None:
    """至少一条带 ``requires`` 的边与至少一个 ``on_clear.set_flags``；门后的标签有产处。"""
    library = _library()
    script = _script(library)

    produced = {
        flag
        for room in library.rooms
        for flag in room.on_clear.set_flags
    }
    for event in library.events:
        for option in event.options:
            for effect in option.effects:
                if effect.kind == "set_flags":
                    produced.update(effect.flags)

    gated = [
        edge
        for layer in script.layers
        for edge in layer.edges
        if edge.requires.flags or edge.requires.not_flags
    ]
    assert gated, "至少要一条带标签条件的边"
    assert produced, "至少一个房间的 on_clear 要设标签"
    for edge in gated:
        for flag in edge.requires.flags:
            assert flag in produced, (
                f"边 {edge.from_} → {edge.to} 要求标签 {flag!r}，但没有任何房间 / 事件会设置它"
            )

    gate = next(edge for edge in gated if edge.requires.flags)
    flag = gate.requires.flags[0]
    assert requirement_met(gate.requires, ()) is False, "没有标签时这道门必须拦人"
    assert requirement_met(gate.requires, (flag,)) is True, "拿到标签之后这道门必须打开"


def test_the_layer_curves_and_global_skills_are_instantiated() -> None:
    """大节曲线逐段升、全局技能有定义且被引用；越靠后的战斗加成越大（占位曲线）。"""
    library = _library()
    script = _script(library)

    assert library.global_skills, "global_skills 至少一条"
    referenced = {
        reference.skill
        for room in library.rooms
        if room.battle is not None
        for reference in room.battle.global_skills
    }
    assert referenced == {skill.id for skill in library.global_skills}, (
        "每条全局技能都要被至少一场战斗引用（否则是死数据）"
    )
    with_battle_override = [
        room
        for room in library.rooms
        if room.battle is not None and room.battle.global_skills
    ]
    without = [
        room
        for room in library.rooms
        if room.battle is not None and not room.battle.global_skills
    ]
    assert with_battle_override and without, (
        "房间写了 global_skills 就覆盖大节曲线，没写就继承：两种实例都要有"
    )

    for layer in script.layers:
        curve = layer.default_scaling
        assert len(curve) >= 2, f"大节 {layer.id!r} 的曲线至少两段（越往后越强）"
        assert curve[0].hp[1] < curve[-1].hp[1], (
            f"大节 {layer.id!r} 后段的体力加成没有变大：{curve[0].hp} → {curve[-1].hp}"
        )


def test_the_skill_pools_events_and_question_pools_are_content() -> None:
    """技能池 2~3 / 事件 5~6 / 事件池 1~2；问号房只抽非 unknown 房间。"""
    library = _library()
    script = _script(library)
    rooms = _index(library.rooms)

    assert 2 <= len(library.skill_pools) <= 3
    assert 5 <= len(library.events) <= 6
    assert 1 <= len(library.event_pools) <= 2

    used_pools = {template.random.pool for template in library.templates}
    assert used_pools == {pool.id for pool in library.skill_pools}, (
        "每个技能池都要被至少一条模板引用（否则是死数据）"
    )
    used_event_pools = {room.event_pool for room in library.rooms if room.event_pool}
    assert used_event_pools == {pool.id for pool in library.event_pools}
    pooled_events = {event_id for pool in library.event_pools for event_id in pool.events}
    assert pooled_events == {event.id for event in library.events}, (
        "每个事件都要挂在某个事件池里（否则抽不到）"
    )

    unknown_nodes = [
        node
        for layer in script.layers
        for node in layer.nodes
        if rooms[node.room].kind is RoomKind.UNKNOWN
    ]
    assert unknown_nodes, "问号房至少被一个节点用到"
    for node in unknown_nodes:
        room = rooms[node.room]
        assert room.pool, f"问号房 {room.id!r} 的池不能为空"
        for entry in room.pool:
            assert rooms[entry.room].kind is not RoomKind.UNKNOWN


def test_the_roster_is_three_heroes_including_the_bare_nengren() -> None:
    """roster 恰好 3 名、包含 ``nengren``，且点名**无视** ``enabled``（能人志士禁用仍可玩）。"""
    library = _library()
    script = _script(library)

    assert len(script.roster) == 3
    assert "nengren" in script.roster, "内置剧本要点名能人志士（票 03 §6 / 票 11）"
    for hero_id in script.roster:
        assert hero_by_id(hero_id) is not None
    nengren = hero_by_id("nengren")
    assert nengren is not None
    assert nengren.enabled is False and nengren.bare is True, (
        "nengren 是禁用 + 零技能武将：它能在 roster 里出现，正是「点名不看 enabled」"
    )


# ---------------------------------------------------------------------------
# 5. 通关路：从第一层入口走到最后一层出口
# ---------------------------------------------------------------------------


def test_the_authored_route_walks_both_layers_and_wins() -> None:
    """固定 seed 下用纯函数走完整条路线：两层都打通、路上覆盖五类房间与两个 Boss。"""
    library = _library()
    script = _script(library)
    rooms = _index(library.rooms)
    value_table = load_skill_values()
    run = _run(library, layer_index=0)
    kinds_seen: list[RoomKind] = []
    bosses_seen = 0
    gate_flag = "flag.village_helped"

    for layer_index, (layer_id, node_ids) in enumerate(ROUTE):
        layer = script.layers[layer_index]
        assert layer.id == layer_id, f"第 {layer_index} 节的 id 变了：{layer.id!r} ≠ {layer_id!r}"
        assert run.layer_index == layer_index
        candidates = available_next(
            layer, flags=run.flags, node_status=run.node_status
        )
        for position, node_id in enumerate(node_ids):
            node = next((candidate for candidate in candidates if candidate.id == node_id), None)
            assert node is not None, (
                f"节点 {node_id!r} 不在当前可到达集里："
                f"{[candidate.id for candidate in candidates]}"
            )
            room = rooms[node.room]
            kinds_seen.append(room.kind)
            if room.is_boss:
                bosses_seen += 1
            run = enter_node(
                run=run, library=library, node_id=node.id, value_table=value_table
            )
            result = settle_node(
                run=run, library=library, value_table=value_table, order=_order_for(run)
            )
            if position == len(node_ids) - 1:
                assert result.outcome in ("continue", "cleared")
                if result.run is not None:
                    assert layer_cleared(layer, node_status=result.run.node_status), (
                        f"大节 {layer.id!r} 的出口结算完却没有打通"
                    )
                run = result.run
                break
            assert result.outcome == "continue", (
                f"节点 {node.id!r} 结算后整轮结束了，但路线还没走完"
            )
            run = result.run
            assert run is not None
            candidates = available_next(
                layer, flags=run.flags, node_status=run.node_status
            )
        if layer_index == 0:
            assert run is not None and run.layer_index == 1, "第一层出口结算完要推进到第二层"
            assert gate_flag in run.flags, (
                "路线走过事件房，标签门要求的标签必须已经拿到"
            )

    assert run is None, "最后一层出口结算完 ⇒ 通关（run=None，调用方删档）"
    assert set(kinds_seen) == set(RoomKind), (
        f"这条通关路要覆盖全部五类房间：实得 {sorted(set(kinds_seen))}"
    )
    assert bosses_seen == 2, f"这条通关路要打两个 Boss：实得 {bosses_seen}"


# ---------------------------------------------------------------------------
# 6. 可胜（票 33）：通关路上的每一场战斗都真的打得赢
# ---------------------------------------------------------------------------


def _battle_specs_along_the_route(library, *, seed: int) -> list[tuple[str, BattleSpec]]:
    """按 :data:`ROUTE` 走一遍（**假设每场都赢**），收集路上每个战斗节点冻结的 ``BattleSpec``。

    与票 29 的 e2e 同一条数据流：``enter_node`` 已经把这一场的 ``pve`` / ``battle_seed`` /
    ``rewards`` 抽完并冻进 ``pending.spec``；问号房取揭晓后的内层。结算按 e2e 的口径
    （战斗每轮取 0 号候选且按"赢"推进、事件选 0 号、商店空购买），故后一场拿到的
    ``RunState`` 就是"前面都赢了"时玩家真会带着的背包。
    """
    script = _script(library)
    value_table = load_skill_values()
    run = _run(library, layer_index=0, seed=seed)
    specs: list[tuple[str, BattleSpec]] = []
    for layer_index, (layer_id, node_ids) in enumerate(ROUTE):
        layer = script.layers[layer_index]
        candidates = available_next(layer, flags=run.flags, node_status=run.node_status)
        for node_id in node_ids:
            node = next(
                (candidate for candidate in candidates if candidate.id == node_id), None
            )
            assert node is not None, f"节点 {node_id!r} 不在可到达集里"
            run = enter_node(
                run=run, library=library, node_id=node.id, value_table=value_table
            )
            outer = run.pending.spec
            inner = outer.revealed.spec if isinstance(outer, UnknownSpec) else outer
            label = f"{layer_id}/{node_id}"
            if isinstance(inner, BattleSpec):
                specs.append((label, inner))
                picks = tuple(options[0] for options in inner.rewards if options)
                order = SettleOrder(won=True, picks=picks)
            elif isinstance(inner, EventSpec):
                order = SettleOrder(option=0)
            else:
                order = SettleOrder()
            result = settle_node(
                run=run, library=library, value_table=value_table, order=order
            )
            assert result.outcome in ("continue", "cleared"), (
                f"{label}: 假设全胜的走法不该被打断（实得 {result.outcome}）"
            )
            if result.run is None:
                assert layer_index == len(ROUTE) - 1
                return specs
            run = result.run
            if node_id != node_ids[-1]:
                candidates = available_next(
                    layer, flags=run.flags, node_status=run.node_status
                )
    return specs


def _heuristic_battle_side(spec: BattleSpec) -> str:
    """按冻结的 ``BattleSpec`` 起一局、全座位启发式 AI 打到终局，返回纯求解器的侧别。

    与票 29 的 e2e 同一条判据（服务端 ``script_runs._battle_won``）：胜负只读终局事实 +
    ``solve_pve_outcome``，并要求 ``game_end`` 的侧别与重算一致。人类座位也一样由启发式
    操作——这条断言判的是**内容**能不能被现有 AI 打赢。
    """
    seats = len(spec.pve.identities)
    rule = RuleConfig(player_count=seats, mode=Mode.PVE, pve=spec.pve)
    engine = Engine.new_game(seed=spec.battle_seed, rule_config=rule, pack=PACK)
    engine.run(
        [HeuristicPlayer(ai_stream(spec.battle_seed, seat)) for seat in range(seats)]
    )
    assert engine.finished, "启发式 AI 的每一场都该跑到终局"
    alive = frozenset(player.seat for player in engine.state.players if player.alive)
    outcome = solve_pve_outcome(config=spec.pve, alive=alive, round_no=engine.state.round)
    assert outcome is not None, "终局了却算不出这一场的胜负"
    end = next(
        record
        for record in reversed(engine.records)
        if getattr(record, "kind", None) == "game_end"
    )
    assert end.side == outcome.side, "记录流的胜负与重算的胜负必须一致"
    return outcome.side


def test_every_battle_on_the_authored_route_is_winnable_by_the_heuristic_ai() -> None:
    """票 33：通关路上的每场战斗在 :data:`WIN_SEEDS` 下都赢（固定种子 + 启发式 AI）。

    为什么要有这一条：票 28 的数值是"可以不平衡、但不许不可胜"的占位值，而票 29 实测到
    **28 个种子 0 胜**（``npc.guard`` 每次吃掉 1 点伤害 + 4 体力主角每轮 2 张对 8 体力主将），
    只能把 e2e 的敌人换成 1 体力模板才跑得通。本断言把"不可胜"钉成红：真打每一场（不是把
    胜负写进档里），任何一个种子下任何一场没赢就指出是哪一场。
    """
    library = _library()
    played: list[str] = []
    losses: list[str] = []
    for seed in WIN_SEEDS:
        for label, spec in _battle_specs_along_the_route(library, seed=seed):
            played.append(f"{seed}:{label}")
            if _heuristic_battle_side(spec) != "ally":
                losses.append(f"{seed}:{label}")

    assert len(played) >= len(WIN_SEEDS) * 5, (
        "这条通关路每颗种子至少 5 场战斗（票 28 的主线：剧情战 ×2 + 遭遇战 + Boss ×2）："
        f"实得 {played}"
    )
    assert not losses, (
        "内置剧本的战斗在固定种子 + 启发式 AI 下必须可胜（票 33）；打不赢的有："
        f"{losses}"
    )
