"""剧本库的三段门：版本 → 结构 → 语义（M7 票 14；票 03 §1、§2、§6、§7）。

测试只经 ``sgs.data`` 公开接缝（``validate_script_library`` / ``load_script_library`` /
``ScriptLibrary`` / ``DataLoadError`` / ``LoadError``）。

**坏数据的地基是 ``_base()``**（一份最小但合法的库，不读内置文件）：内置空壳的内容会随
票 28 变，拿它当基底会让这里的断言跟着内容漂。``_errors()`` 走的就是注册给运行期 /
编辑器的那一个函数——它是**纯函数返回清单**（D30：拒绝是调用方的策略），不抛。

三段门的判据各占一节：版本门只看 ``script_schema_version``；结构门是 pydantic（字段缺失 /
类型错，JSON Pointer 定位）；语义门是手写检查（断链、图、池、座位、引用）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import LoadError, validate_script_library

FILE = "script-library.json"
"""断言里的 ``file`` 恒为这一份（``LoadError.file`` 记录来源文件）。"""


def _base() -> dict[str, Any]:
    """一份**最小但合法**的剧本库：覆盖全部实体，供各处改坏。"""
    return {
        "script_schema_version": 1,
        "skill_pools": [
            {
                "id": "pool.mix",
                "name": "混合池",
                "skills": ["hero.jie_guanyu.wusheng"],
            }
        ],
        "events": [
            {
                "id": "ev.a",
                "name": "事件甲",
                "text": "……",
                "options": [
                    {
                        "text": "收下",
                        "requires": {"flags": ["saved"], "not_flags": []},
                        "effects": [
                            {"kind": "coins", "amount": 2},
                            {"kind": "set_flags", "flags": ["paid"]},
                            {"kind": "grant_skill", "skill": "hero.jie_guanyu.wusheng"},
                        ],
                    }
                ],
            }
        ],
        "event_pools": [{"id": "ep.a", "name": "事件池甲", "events": ["ev.a"]}],
        "global_skills": [
            {
                "id": "gs.a",
                "name": "全局甲",
                "side": "enemy",
                "scale": {"hp": [1, 2], "draw": [0, 1], "slash": [0, 1]},
            }
        ],
        "templates": [
            {
                "id": "tpl.a",
                "name": "模板甲",
                "band": "band1",
                "hp": 3,
                "hp_max": 4,
                "initial_skills": ["hero.jie_guanyu.wusheng"],
                "random": {"pool": "pool.mix", "count": [0, 1]},
                "reward": {"coins": 2, "pick1": 1, "pick2": 0, "pick3": 0},
            }
        ],
        "rooms": [
            {"id": "room.shop", "name": "商店", "kind": "shop", "text": "……"},
            {
                "id": "room.battle",
                "name": "哨卡",
                "kind": "story",
                "text": "……",
                "battle": {
                    "allies": [],
                    "enemies": [{"role": "enemy", "template": "tpl.a"}],
                    "victory": {"kind": "rout"},
                    "global_skills": [{"skill": "gs.a"}],
                    "extra_reward": {"coins": 1},
                },
                "on_clear": {"set_flags": ["saved"]},
            },
            {
                "id": "room.event",
                "name": "村口",
                "kind": "event",
                "text": "……",
                "event_pool": "ep.a",
            },
            {
                "id": "room.unknown",
                "name": "岔路",
                "kind": "unknown",
                "text": "……",
                "pool": [
                    {"room": "room.shop", "weight": 2},
                    {"room": "room.battle", "weight": 1},
                ],
            },
        ],
        "scripts": [
            {
                "id": "s.a",
                "name": "剧本甲",
                "version": 1,
                "roster": ["nengren", "jie_guanyu", "jie_liubei"],
                "comment": "最小合法库（测试地基）",
                "layers": [
                    {
                        "id": "l.a",
                        "name": "大节甲",
                        "grid": {"rows": 2, "cols": 3},
                        "nodes": [
                            {"id": "n1", "row": 0, "col": 0, "room": "room.battle"},
                            {"id": "n2", "row": 1, "col": 2, "room": "room.shop"},
                        ],
                        "edges": [
                            {
                                "from": "n1",
                                "to": "n2",
                                "requires": {"flags": ["saved"]},
                            }
                        ],
                        "default_scaling": [
                            {"from_col": 0, "hp": [0, 0], "draw": [0, 0], "slash": [0, 0]},
                            {"from_col": 2, "hp": [1, 1], "draw": [0, 1], "slash": [0, 0]},
                        ],
                    }
                ],
            }
        ],
    }


def _errors(raw: Any) -> tuple[LoadError, ...]:
    """注册给运行期 / 编辑器的**那一个**校验函数：纯函数，返回清单，不抛。"""
    return validate_script_library(raw, file=FILE)


def _locs(errors: tuple[LoadError, ...]) -> list[str]:
    return [error.loc for error in errors]


# ---------------------------------------------------------------------------
# 底座（防"坏数据基底本身不合法 ⇒ 后面每条都恰好在报同一件事"）
# ---------------------------------------------------------------------------


def test_the_baseline_library_is_legal() -> None:
    """``_base()`` 本身过全部三段门：下面每一处改坏都是**唯一**新增的问题。"""
    errors = _errors(_base())

    assert errors == (), f"底座必须是合法的：{[(e.loc, e.rule_id) for e in errors]}"


# ---------------------------------------------------------------------------
# ① 版本门：未知 / 缺失 script_schema_version ⇒ 只报这一条，不再报字段错误
# ---------------------------------------------------------------------------


def test_an_unknown_script_schema_version_is_rejected_before_structure_checks() -> None:
    """版本门先跑：未知版本 ⇒ **一条**版本错误，不掺结构门的字段噪音。

    改坏的不只是版本号：``grid`` 也缺 ``cols`` 且 ``nodes`` 写坏了。若结构门抢跑，
    报告里会冒出这些字段错误——版本门的判据是"只报版本这一条"。
    """
    raw = _base()
    raw["script_schema_version"] = 99
    del raw["scripts"][0]["layers"][0]["grid"]["cols"]
    raw["scripts"][0]["layers"][0]["nodes"][0]["row"] = "零"

    errors = _errors(raw)

    assert len(errors) == 1, [(e.loc, e.rule_id) for e in errors]
    error = errors[0]
    assert error.file == FILE
    assert error.loc == "/script_schema_version"
    assert error.rule_id == "version.unknown"
    assert "99" in error.got
    assert "1" in error.expected


def test_a_missing_script_schema_version_is_rejected_too() -> None:
    """缺失版本号同样 fail-closed（``version.missing``，loc 仍指向那个键）。"""
    raw = _base()
    del raw["script_schema_version"]

    errors = _errors(raw)

    assert [(e.loc, e.rule_id) for e in errors] == [("/script_schema_version", "version.missing")]
    assert errors[0].got == "缺失"


def test_an_invalid_json_document_is_a_structure_error_with_the_root_loc() -> None:
    """连 JSON 都不是 ⇒ 结构门的一条根错误（与内容包 / 武将表同一条判据）。"""
    errors = _errors("{ 不是 JSON")

    assert [(e.loc, e.rule_id) for e in errors] == [("/", "structure.invalid_json")]
    assert errors[0].file == FILE


def test_a_non_object_root_is_rejected_as_a_structure_error() -> None:
    """根必须是对象（数组装不下 ``script_schema_version`` 这个库头）。"""
    errors = _errors('[{"script_schema_version": 1}]')

    assert [(e.loc, e.rule_id) for e in errors] == [("/", "structure.root_type")]
    assert errors[0].got == "list"


# ---------------------------------------------------------------------------
# ② 结构门：缺字段 / 坏类型，**全部一次报完**，JSON Pointer 逐条定位
# ---------------------------------------------------------------------------


def test_structural_errors_are_aggregated_and_point_at_each_field() -> None:
    """两处结构错（缺字段 + 坏类型）一次报两条，loc 各指自己的字段。"""
    raw = _base()
    del raw["scripts"][0]["layers"][0]["grid"]["cols"]
    raw["scripts"][0]["layers"][0]["nodes"][0]["row"] = "零"

    errors = _errors(raw)

    assert len(errors) == 2, [(e.loc, e.rule_id) for e in errors]
    assert {error.loc for error in errors} == {
        "/scripts/0/layers/0/grid/cols",
        "/scripts/0/layers/0/nodes/0/row",
    }
    assert all(error.rule_id.startswith("structure.") for error in errors)
    assert all(error.file == FILE for error in errors)


def test_structure_errors_do_not_swallow_semantic_ones() -> None:
    """结构门不过时**不跑**语义门：字段本身不可信，语义错误不掺进来（顺序 = 三段式）。"""
    raw = _base()
    raw["rooms"][0]["kind"] = "休整"  # 闭集外的 kind（结构错）
    raw["rooms"][1]["battle"]["enemies"][0]["template"] = "tpl.missing"  # 断链（语义错）

    errors = _errors(raw)

    assert errors, "至少要有结构错"
    assert all(error.rule_id.startswith("structure.") for error in errors), (
        f"结构门过了才该报语义错：{[(e.loc, e.rule_id) for e in errors]}"
    )


def test_an_unknown_room_kind_is_structurally_rejected() -> None:
    """``kind`` 是闭集 ``story / battle / event / shop / unknown``（票 03 §1）。"""
    raw = _base()
    raw["rooms"][0]["kind"] = "rest"

    errors = _errors(raw)

    assert [(e.loc, e.rule_id) for e in errors] == [("/rooms/0/kind", "structure.enum")]
    assert "rest" in errors[0].got


def test_an_unknown_key_is_rejected_because_extra_keys_would_be_a_second_schema() -> None:
    """``extra="forbid"``：拼错的键当场判红，而不是被静默忽略。"""
    raw = _base()
    raw["rooms"][0]["nmae"] = "商店"

    errors = _errors(raw)

    assert [(e.loc, e.rule_id) for e in errors] == [("/rooms/0/nmae", "structure.extra_forbidden")]


# ---------------------------------------------------------------------------
# ③ 语义门：跨表引用**一次全扫**，loc 指向引用方
# ---------------------------------------------------------------------------


def test_three_broken_references_are_reported_in_one_pass() -> None:
    """一个库同时有 3 处断链 ⇒ 一次报 3 条，``loc`` 各指**引用方**、``expected`` 点名对象。"""
    raw = _base()
    raw["scripts"][0]["layers"][0]["nodes"][0]["room"] = "room.nowhere"
    raw["scripts"][0]["layers"][0]["edges"][0]["to"] = "n.nowhere"
    raw["rooms"][1]["battle"]["enemies"][0]["template"] = "tpl.nowhere"

    errors = _errors(raw)

    assert len(errors) == 3, [(e.loc, e.rule_id) for e in errors]
    by_loc = {error.loc: error for error in errors}
    assert set(by_loc) == {
        "/scripts/0/layers/0/nodes/0/room",
        "/scripts/0/layers/0/edges/0/to",
        "/rooms/1/battle/enemies/0/template",
    }
    node_room = by_loc["/scripts/0/layers/0/nodes/0/room"]
    assert (node_room.rule_id, node_room.got) == ("script_library.unknown_room", "room.nowhere")
    assert "房间" in node_room.expected
    edge_node = by_loc["/scripts/0/layers/0/edges/0/to"]
    assert (edge_node.rule_id, edge_node.got) == ("script_library.unknown_node", "n.nowhere")
    assert "节点" in edge_node.expected
    seat_template = by_loc["/rooms/1/battle/enemies/0/template"]
    assert (seat_template.rule_id, seat_template.got) == (
        "script_library.unknown_template",
        "tpl.nowhere",
    )
    assert "模板" in seat_template.expected


def test_a_node_must_sit_inside_the_grid_and_one_node_per_cell() -> None:
    """节点越出网格 / 两个节点抢同一格：各一条，各指自己的字段。"""
    outside = _base()
    outside["scripts"][0]["layers"][0]["nodes"][0]["row"] = 2  # grid.rows = 2 ⇒ 合法行是 0..1
    outside_errors = _errors(outside)

    assert [(e.loc, e.rule_id) for e in outside_errors] == [
        ("/scripts/0/layers/0/nodes/0/row", "script_library.node_out_of_grid")
    ]
    assert "grid.rows = 2" in outside_errors[0].expected

    overlapping = _base()
    overlapping["scripts"][0]["layers"][0]["nodes"].append(
        {"id": "n3", "row": 0, "col": 0, "room": "room.shop"}
    )
    overlap_errors = _errors(overlapping)

    assert [(e.loc, e.rule_id) for e in overlap_errors] == [
        ("/scripts/0/layers/0/nodes/2", "script_library.cell_taken")
    ]


def test_an_edge_may_only_go_to_a_later_column() -> None:
    """列 = 进度轴：同列（含自连）与回头边都判红（票 03 §2「回头意味着时间穿越」）。"""
    backward = _base()
    edge = backward["scripts"][0]["layers"][0]["edges"][0]
    edge["from"] = "n2"
    edge["to"] = "n1"
    backward_errors = _errors(backward)

    assert [(e.loc, e.rule_id) for e in backward_errors] == [
        ("/scripts/0/layers/0/edges/0/to", "script_library.edge_not_forward")
    ]
    assert "col" in backward_errors[0].got

    self_loop = _base()
    self_loop["scripts"][0]["layers"][0]["edges"][0]["to"] = "n1"
    loop_errors = _errors(self_loop)

    assert [(e.loc, e.rule_id) for e in loop_errors] == [
        ("/scripts/0/layers/0/edges/0/to", "script_library.edge_not_forward")
    ], "同列 = 不前进（`to.col > from.col` 是严格不等式）"


def test_a_layer_rejects_duplicate_node_ids_and_duplicate_edges() -> None:
    """节点 id 在一个大节内唯一；同一对 ``(from, to)`` 只画一次（票 24 的解除连线靠它）。"""
    raw = _base()
    raw["scripts"][0]["layers"][0]["nodes"].append(
        {"id": "n1", "row": 1, "col": 1, "room": "room.shop"}
    )
    raw["scripts"][0]["layers"][0]["edges"].append({"from": "n1", "to": "n2"})

    errors = _errors(raw)

    assert [(e.loc, e.rule_id) for e in errors] == [
        ("/scripts/0/layers/0/nodes/2/id", "script_library.duplicate_id"),
        ("/scripts/0/layers/0/edges/1", "script_library.duplicate_reference"),
    ]


def test_a_battle_draws_only_from_bands_that_can_pay_the_count() -> None:
    """按档抽是**无放回**的：band 要有模板，且模板数不少于要抽的上限（票 03 §3）。"""
    unknown_band = _base()
    unknown_band["rooms"][1]["battle"]["enemies"] = {
        "draw": {"band": "band9", "count": [1, 1]}
    }
    unknown_errors = _errors(unknown_band)

    assert [(e.loc, e.rule_id) for e in unknown_errors] == [
        ("/rooms/1/battle/enemies/draw/band", "script_library.unknown_band")
    ]

    too_small = _base()  # band1 只有 1 条模板
    too_small["rooms"][1]["battle"]["enemies"] = {
        "draw": {"band": "band1", "count": [1, 2]}
    }
    too_small_errors = _errors(too_small)

    assert [(e.loc, e.rule_id) for e in too_small_errors] == [
        ("/rooms/1/battle/enemies/draw/count", "script_library.draw_exceeds_band")
    ]
    assert "1 条" in too_small_errors[0].expected


def test_a_protected_seat_is_a_single_ally() -> None:
    """受保护对象是 ``PveConfig.protected_ally`` 那一格：必须是队友、且只有一名。"""
    legal = _base()
    legal["rooms"][1]["battle"]["allies"] = [
        {"role": "ally", "template": "tpl.a", "protected": True}
    ]
    assert _errors(legal) == ()

    enemy_protected = _base()
    enemy_protected["rooms"][1]["battle"]["enemies"][0]["protected"] = True
    assert [(e.loc, e.rule_id) for e in _errors(enemy_protected)] == [
        ("/rooms/1/battle/enemies/0/protected", "script_library.protected_seat")
    ]

    two_protected = _base()
    two_protected["rooms"][1]["battle"]["allies"] = [
        {"role": "ally", "template": "tpl.a", "protected": True},
        {"role": "ally", "template": "tpl.a", "protected": True},
    ]
    two_errors = _errors(two_protected)

    assert [(e.loc, e.rule_id) for e in two_errors] == [
        ("/rooms/1/battle/allies", "script_library.protected_seat")
    ]


def test_a_question_pool_may_only_name_regular_rooms_once_with_positive_weights() -> None:
    """问号池：只许非 unknown 房间、同池不重复、权重正整数（票 03 §2）。"""
    recursive = _base()
    recursive["rooms"][3]["pool"][0]["room"] = "room.unknown"
    recursive_errors = _errors(recursive)

    assert [(e.loc, e.rule_id) for e in recursive_errors] == [
        ("/rooms/3/pool/0/room", "script_library.unknown_room_recursive")
    ]

    repeated = _base()
    repeated["rooms"][3]["pool"][1]["room"] = "room.shop"
    repeated_errors = _errors(repeated)

    assert [(e.loc, e.rule_id) for e in repeated_errors] == [
        ("/rooms/3/pool/1", "script_library.duplicate_reference")
    ]

    dangling = _base()
    dangling["rooms"][3]["pool"][0]["room"] = "room.nowhere"
    dangling_errors = _errors(dangling)

    assert [(e.loc, e.rule_id) for e in dangling_errors] == [
        ("/rooms/3/pool/0/room", "script_library.unknown_room")
    ]

    empty = _base()
    empty["rooms"][3]["pool"] = []
    empty_errors = _errors(empty)

    assert [(e.loc, e.rule_id) for e in empty_errors] == [
        ("/rooms/3/pool", "script_library.pool_empty")
    ]

    zero_weight = _base()
    zero_weight["rooms"][3]["pool"][0]["weight"] = 0
    weight_errors = _errors(zero_weight)

    assert [(e.loc, e.rule_id) for e in weight_errors] == [
        ("/rooms/3/pool/0/weight", "structure.greater_than_equal")
    ], "零 / 负权重是结构错（正整数由字段约束表达，不在语义门重抄一遍）"


def test_a_room_carries_the_payload_of_its_kind_and_no_other() -> None:
    """``kind`` 决定载荷（票 03 §1）：缺了是错，带错了也是错（否则会出现第二份真相）。"""
    missing_battle = _base()
    del missing_battle["rooms"][1]["battle"]
    assert [(e.loc, e.rule_id) for e in _errors(missing_battle)] == [
        ("/rooms/1/battle", "script_library.room_payload_missing")
    ]

    missing_pool = _base()
    del missing_pool["rooms"][2]["event_pool"]
    assert [(e.loc, e.rule_id) for e in _errors(missing_pool)] == [
        ("/rooms/2/event_pool", "script_library.room_payload_missing")
    ]

    extra_payload = _base()
    extra_payload["rooms"][0]["event_pool"] = "ep.a"
    assert [(e.loc, e.rule_id) for e in _errors(extra_payload)] == [
        ("/rooms/0/event_pool", "script_library.room_payload_unexpected")
    ]

    boss_shop = _base()
    boss_shop["rooms"][0]["is_boss"] = True
    assert [(e.loc, e.rule_id) for e in _errors(boss_shop)] == [
        ("/rooms/0/is_boss", "script_library.boss_room_not_a_battle")
    ]


def test_a_battle_seat_total_outside_two_to_five_is_rejected() -> None:
    """座位：主角 1 + allies 0~2 + enemies 1~4，总数 2~5（票 03 §3）。"""
    too_many = _base()
    too_many["rooms"][1]["battle"]["allies"] = [
        {"role": "ally", "template": "tpl.a"},
        {"role": "ally", "template": "tpl.a"},
    ]
    too_many["rooms"][1]["battle"]["enemies"] = [
        {"role": "enemy", "template": "tpl.a"},
        {"role": "enemy", "template": "tpl.a"},
        {"role": "enemy", "template": "tpl.a"},
    ]
    too_many_errors = _errors(too_many)

    assert [(e.loc, e.rule_id) for e in too_many_errors] == [
        ("/rooms/1/battle", "script_library.seat_count")
    ]
    assert "6" in too_many_errors[0].got

    no_enemy = _base()
    no_enemy["rooms"][1]["battle"]["enemies"] = []
    no_enemy_errors = _errors(no_enemy)

    assert [(e.loc, e.rule_id) for e in no_enemy_errors] == [
        ("/rooms/1/battle/enemies", "script_library.enemy_count")
    ], "1 ~ 4 座敌人；总数那一关只在各分量合法时才看（一处问题一条错误）"

    too_many_allies = _base()
    too_many_allies["rooms"][1]["battle"]["allies"] = [
        {"role": "ally", "template": "tpl.a"},
        {"role": "ally", "template": "tpl.a"},
        {"role": "ally", "template": "tpl.a"},
    ]
    ally_errors = _errors(too_many_allies)

    assert [(e.loc, e.rule_id) for e in ally_errors] == [
        ("/rooms/1/battle/allies", "script_library.ally_count")
    ]

    draw_too_wide = _base()
    draw_too_wide["rooms"][1]["battle"]["enemies"] = {"draw": {"band": "band1", "count": [1, 5]}}
    draw_errors = _errors(draw_too_wide)

    assert [(e.loc, e.rule_id) for e in draw_errors] == [
        ("/rooms/1/battle/enemies/draw/count", "script_library.enemy_count")
    ]

    draw_inverted = _base()
    draw_inverted["rooms"][1]["battle"]["enemies"] = {"draw": {"band": "band1", "count": [3, 2]}}
    inverted_errors = _errors(draw_inverted)

    assert [(e.loc, e.rule_id) for e in inverted_errors] == [
        ("/rooms/1/battle/enemies/draw/count", "script_library.enemy_count")
    ]


def test_a_seat_must_use_a_template_and_the_role_of_its_list() -> None:
    """座位自述的 ``role`` 必须与所在列表一致；主角是隐式 0 号座，数据里没有它的条目。"""
    wrong_side = _base()
    wrong_side["rooms"][1]["battle"]["enemies"][0]["role"] = "ally"
    assert [(e.loc, e.rule_id) for e in _errors(wrong_side)] == [
        ("/rooms/1/battle/enemies/0/role", "script_library.seat_role_mismatch")
    ]

    principal_seat = _base()
    principal_seat["rooms"][1]["battle"]["enemies"][0]["role"] = "player"
    principal_errors = _errors(principal_seat)

    assert [(e.loc, e.rule_id) for e in principal_errors] == [
        ("/rooms/1/battle/enemies/0/role", "script_library.seat_role_mismatch")
    ], "主角恒为 0 号座（票 03 §3）"

    no_template = _base()
    del no_template["rooms"][1]["battle"]["enemies"][0]["template"]
    no_template_errors = _errors(no_template)

    assert [(e.loc, e.rule_id) for e in no_template_errors] == [
        ("/rooms/1/battle/enemies/0/template", "script_library.seat_without_template")
    ]


def test_a_slay_boss_victory_needs_exactly_one_boss_seat() -> None:
    """``victory`` 四类（票 03 §3）：``slay_boss`` ⇔ 恰好一个 ``boss: true`` 座位。"""
    no_boss = _base()
    no_boss["rooms"][1]["battle"]["victory"] = {"kind": "slay_boss"}
    assert [(e.loc, e.rule_id) for e in _errors(no_boss)] == [
        ("/rooms/1/battle/victory", "script_library.boss_seat")
    ]

    with_boss = _base()
    with_boss["rooms"][1]["battle"]["victory"] = {"kind": "slay_boss"}
    with_boss["rooms"][1]["battle"]["enemies"][0]["boss"] = True
    assert _errors(with_boss) == ()

    two_bosses = _base()
    two_bosses["rooms"][1]["battle"]["victory"] = {"kind": "slay_boss"}
    two_bosses["rooms"][1]["battle"]["enemies"] = [
        {"role": "enemy", "template": "tpl.a", "boss": True},
        {"role": "enemy", "template": "tpl.a", "boss": True},
    ]
    two_errors = _errors(two_bosses)

    assert [(e.loc, e.rule_id) for e in two_errors] == [
        ("/rooms/1/battle/victory", "script_library.boss_seat")
    ]

    missing_rounds = _base()
    missing_rounds["rooms"][1]["battle"]["victory"] = {"kind": "survive_rounds"}
    assert [(e.loc, e.rule_id) for e in _errors(missing_rounds)] == [
        ("/rooms/1/battle/victory", "script_library.victory_rounds")
    ]

    surprising_rounds = _base()
    surprising_rounds["rooms"][1]["battle"]["victory"] = {"kind": "rout", "rounds": 3}
    assert [(e.loc, e.rule_id) for e in _errors(surprising_rounds)] == [
        ("/rooms/1/battle/victory", "script_library.victory_rounds")
    ]


def test_a_template_random_count_is_legal() -> None:
    """模板 ``random.count``：``0 <= min <= max`` 且不超过池里的技能数（票 03 §1）。"""
    inverted = _base()
    inverted["templates"][0]["random"]["count"] = [2, 1]
    assert [(e.loc, e.rule_id) for e in _errors(inverted)] == [
        ("/templates/0/random/count", "script_library.random_count")
    ]

    negative = _base()
    negative["templates"][0]["random"]["count"] = [-1, 0]
    assert [(e.loc, e.rule_id) for e in _errors(negative)] == [
        ("/templates/0/random/count", "script_library.random_count")
    ]

    exceeds_pool = _base()
    exceeds_pool["templates"][0]["random"]["count"] = [0, 2]
    exceeds_errors = _errors(exceeds_pool)

    assert [(e.loc, e.rule_id) for e in exceeds_errors] == [
        ("/templates/0/random/count", "script_library.random_count_exceeds_pool")
    ]
    assert "1" in exceeds_errors[0].expected, "报出池子实际有几条技能"

    fixed_opponent = _base()
    fixed_opponent["templates"][0]["random"]["count"] = [0, 0]
    assert _errors(fixed_opponent) == (), "`[0,0]` = 固定对手（票 03 §1 的关键 collapse）"


def test_a_template_must_be_well_formed_and_name_existing_things() -> None:
    """模板：``hp <= hp_max``、技能池 / 初始技能都必须解析得到。"""
    over_hp = _base()
    over_hp["templates"][0]["hp"] = 5
    assert [(e.loc, e.rule_id) for e in _errors(over_hp)] == [
        ("/templates/0/hp", "script_library.template_hp")
    ]

    unknown_pool = _base()
    unknown_pool["templates"][0]["random"]["pool"] = "pool.nowhere"
    assert [(e.loc, e.rule_id) for e in _errors(unknown_pool)] == [
        ("/templates/0/random/pool", "script_library.unknown_pool")
    ]

    unknown_skill = _base()
    unknown_skill["templates"][0]["initial_skills"] = ["hero.nobody.skill"]
    assert [(e.loc, e.rule_id) for e in _errors(unknown_skill)] == [
        ("/templates/0/initial_skills/0", "script_library.unknown_skill")
    ]


def test_skill_and_event_pools_only_name_registered_things_once() -> None:
    """池 → 技能 id（∈ ``CATALOG``）与事件池 → 事件：都要存在且不重复。"""
    unknown_skill = _base()
    unknown_skill["skill_pools"][0]["skills"].append("hero.nobody.skill")
    assert [(e.loc, e.rule_id) for e in _errors(unknown_skill)] == [
        ("/skill_pools/0/skills/1", "script_library.unknown_skill")
    ]

    duplicate_skill = _base()
    duplicate_skill["skill_pools"][0]["skills"].append("hero.jie_guanyu.wusheng")
    assert [(e.loc, e.rule_id) for e in _errors(duplicate_skill)] == [
        ("/skill_pools/0/skills/1", "script_library.duplicate_reference")
    ]

    unknown_event = _base()
    unknown_event["event_pools"][0]["events"].append("ev.nowhere")
    assert [(e.loc, e.rule_id) for e in _errors(unknown_event)] == [
        ("/event_pools/0/events/1", "script_library.unknown_event")
    ]

    duplicate_event = _base()
    duplicate_event["event_pools"][0]["events"].append("ev.a")
    assert [(e.loc, e.rule_id) for e in _errors(duplicate_event)] == [
        ("/event_pools/0/events/1", "script_library.duplicate_reference")
    ]


def test_an_event_effect_may_grant_a_registered_skill_or_relic() -> None:
    """事件效果的技能 / 遗物 id 也是跨表引用（``CATALOG`` 里必须有）。"""
    broken_skill = _base()
    broken_skill["events"][0]["options"][0]["effects"][2]["skill"] = "hero.nobody.skill"
    assert [(e.loc, e.rule_id) for e in _errors(broken_skill)] == [
        ("/events/0/options/0/effects/2/skill", "script_library.unknown_skill")
    ]

    broken_relic = _base()
    broken_relic["events"][0]["options"][0]["effects"].append(
        {"kind": "grant_relic", "relic": "relic.not-implemented-yet"}
    )
    assert [(e.loc, e.rule_id) for e in _errors(broken_relic)] == [
        ("/events/0/options/0/effects/3/relic", "script_library.unknown_relic")
    ], "遗物技能的载体随票 26 落地：现在写进库里就会被语义门拒（与价值表同一口径）"


def test_a_roster_resolves_heroes_across_tables_ignoring_enabled() -> None:
    """初始角色点名跨**全部**武将表、**不看** ``enabled``（票 03 §6；与票 11 合龙）。"""
    from sgs.data import hero_by_id

    assert hero_by_id("nengren") is not None
    assert hero_by_id("nengren").enabled is False, "能人志士是未启用的零技能武将"

    legal = _base()
    assert _errors(legal) == (), "点名一个 `enabled: false` 的武将是合法的"

    too_few = _base()
    too_few["scripts"][0]["roster"] = ["nengren", "jie_guanyu"]
    assert [(e.loc, e.rule_id) for e in _errors(too_few)] == [
        ("/scripts/0/roster", "script_library.roster_size")
    ]

    unknown_hero = _base()
    unknown_hero["scripts"][0]["roster"][2] = "hero.nowhere"
    assert [(e.loc, e.rule_id) for e in _errors(unknown_hero)] == [
        ("/scripts/0/roster/2", "script_library.unknown_hero")
    ]

    duplicated = _base()
    duplicated["scripts"][0]["roster"][2] = "nengren"
    assert [(e.loc, e.rule_id) for e in _errors(duplicated)] == [
        ("/scripts/0/roster/2", "script_library.duplicate_reference")
    ]


def test_default_scaling_is_an_ascending_curve_inside_the_grid() -> None:
    """大节默认曲线：``from_col`` 严格升序、落在网格内、区间 ``min <= max``（票 03 §3）。"""
    descending = _base()
    descending["scripts"][0]["layers"][0]["default_scaling"].reverse()
    assert [(e.loc, e.rule_id) for e in _errors(descending)] == [
        ("/scripts/0/layers/0/default_scaling/1/from_col", "script_library.scaling_not_ascending")
    ]

    out_of_grid = _base()
    out_of_grid["scripts"][0]["layers"][0]["default_scaling"][1]["from_col"] = 3
    assert [(e.loc, e.rule_id) for e in _errors(out_of_grid)] == [
        ("/scripts/0/layers/0/default_scaling/1/from_col", "script_library.scaling_out_of_grid")
    ]

    inverted_span = _base()
    inverted_span["scripts"][0]["layers"][0]["default_scaling"][1]["hp"] = [2, 1]
    assert [(e.loc, e.rule_id) for e in _errors(inverted_span)] == [
        ("/scripts/0/layers/0/default_scaling/1/hp", "script_library.bad_span")
    ]


def test_global_skills_carry_three_ranges_and_are_referenced_by_existing_ids() -> None:
    """全局副本技能：三档区间齐全、区间合法、战斗的引用解析得到（票 03 §3）。"""
    incomplete = _base()
    del incomplete["global_skills"][0]["scale"]["slash"]
    assert [(e.loc, e.rule_id) for e in _errors(incomplete)] == [
        ("/global_skills/0/scale", "script_library.scale_incomplete")
    ]

    inverted = _base()
    inverted["global_skills"][0]["scale"]["hp"] = [2, 1]
    assert [(e.loc, e.rule_id) for e in _errors(inverted)] == [
        ("/global_skills/0/scale/hp", "script_library.bad_span")
    ]

    unknown_ref = _base()
    unknown_ref["rooms"][1]["battle"]["global_skills"][0]["skill"] = "gs.nowhere"
    assert [(e.loc, e.rule_id) for e in _errors(unknown_ref)] == [
        ("/rooms/1/battle/global_skills/0/skill", "script_library.unknown_global_skill")
    ]


def test_entity_ids_are_unique_per_table() -> None:
    """每张表的主键唯一：剧本 / 大节 / 房间（其余表同一判据，这里抽三条）。"""
    duplicate_script = _base()
    duplicate_script["scripts"].append(duplicate_script["scripts"][0])
    assert [(e.loc, e.rule_id) for e in _errors(duplicate_script)] == [
        ("/scripts/1/id", "script_library.duplicate_id")
    ]

    duplicate_layer = _base()
    layers = duplicate_layer["scripts"][0]["layers"]
    layers.append({**layers[0]})
    assert [(e.loc, e.rule_id) for e in _errors(duplicate_layer)] == [
        ("/scripts/0/layers/1/id", "script_library.duplicate_id")
    ]

    duplicate_room = _base()
    duplicate_room["rooms"].append(duplicate_room["rooms"][0])
    assert [(e.loc, e.rule_id) for e in _errors(duplicate_room)] == [
        ("/rooms/4/id", "script_library.duplicate_id")
    ]


# ---------------------------------------------------------------------------
# ④ 严格 / 宽松两条路：同一份坏库，两个调用方两种处置（D30）
# ---------------------------------------------------------------------------


def test_a_broken_library_is_rejected_in_strict_mode_and_loadable_in_lenient_mode() -> None:
    """同一份断链库：运行期拒、编辑器能加载并拿到清单（校验器只返回，不抛）。"""
    from sgs.data import DataLoadError, load_script_library

    broken = _base()
    broken["scripts"][0]["layers"][0]["nodes"][0]["room"] = "room.nowhere"

    errors = validate_script_library(broken, file=FILE)
    assert [error.rule_id for error in errors] == ["script_library.unknown_room"], (
        "校验器**返回**清单：写成直接抛，这一行就会变红"
    )

    try:
        load_script_library(broken, file=FILE)
    except DataLoadError as exc:
        assert exc.errors == errors, "严格路把同一份清单原样抛出来（同源，不是第二份校验）"
    else:  # pragma: no cover - 严格路必须拒绝
        raise AssertionError("严格路应当拒绝断链库")

    lenient = load_script_library(broken, file=FILE, strict=False)
    assert lenient.scripts[0].layers[0].nodes[0].room == "room.nowhere", (
        "宽松路要保留悬空引用：编辑器得能把它修掉（D30：允许保存坏库）"
    )


def test_the_lenient_path_still_rejects_what_it_cannot_model() -> None:
    """版本 / 结构错连模型都建不出来 ⇒ 宽松路也拒（放宽的是语义，不是 schema）。"""
    import pytest

    from sgs.data import DataLoadError, load_script_library

    unknown_version = _base()
    unknown_version["script_schema_version"] = 99
    with pytest.raises(DataLoadError):
        load_script_library(unknown_version, file=FILE, strict=False)

    broken_structure = _base()
    del broken_structure["scripts"][0]["layers"][0]["grid"]
    with pytest.raises(DataLoadError):
        load_script_library(broken_structure, file=FILE, strict=False)


def test_an_empty_library_is_legal_because_the_editor_may_delete_every_script() -> None:
    """空库（只剩库头）是合法的编辑器状态：删光剧本不该让库再也打不开（D30）。"""
    assert _errors({"script_schema_version": 1}) == ()
