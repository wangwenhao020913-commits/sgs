"""房间编译 + 九条子流 + 进入节点 = 抽完（M7 票 17；票 03 §3、§5 / 票 02 §3）。

三层断言，全部只经 ``sgs.script`` 的公开接缝：

1. **九条子流**（票 03 §5 的表）：名字一个不多一个不少、每条工厂派生自它自己的名字、
   同 seed 同结果；「新增名字不扰动既有序列」的同形断言在 ``tests/test_rng.py``。
2. **编译** ``Room → PveConfig``：座位 / 阵营 / 主角 / 保护对象 / 胜负 / Boss 座位 /
   授予 / 座位表 / 副本全局技能**逐字段**映射；同快照 + 同 seed ⇒ 同一份 header（自证）；
   两种敌人来源（固定 / 按档抽无放回）与三种缩放（房间引用、引用的 scale 覆盖、
   大节曲线按 ``from_col`` 继承 / 被房间覆盖）。
3. **进入节点 = 抽完**：一次抽完并物化进 ``pending.spec``（战斗种子 / 敌人 / 模板技能 /
   各轮奖励候选 / Boss 的那件遗物 / 商店库存 / 事件 / 问号房揭晓），同 seed 重放逐字段相同；
   可到达集算不出来时**报错**（票 16 的那个出口），不静默软锁。

进料是手搓的（``tests/support/script_mode.py``）：这一层判的是机械关系，不读内置剧本。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sgs.data import (
    Identity,
    PveSeat,
    PveVictoryKind,
    Rarity,
    RoomKind,
    Scale,
    SkillGrant,
)
from sgs.engine import RandomSource
from sgs.script import (
    SCRIPT_SUBSTREAMS,
    Backpack,
    BattleSpec,
    DeadEndError,
    EventSpec,
    RunState,
    ScriptError,
    SettleOrder,
    ShopSpec,
    UnknownSpec,
    battle_stream,
    bonus_stream,
    compile_battle,
    draw_stream,
    enemy_stream,
    enter_node,
    equipped_skills,
    event_stream,
    held_skills,
    hero_seat,
    progress_key,
    resolve_scaling,
    reward_stream,
    roster_stream,
    settle_node,
    shop_stream,
    uniform_sample,
    unknown_stream,
    weighted_sample,
)

from support.script_mode import (
    HERO,
    LAYER_ID,
    RELIC_X,
    RELIC_Y,
    SEED,
    SKILL_A,
    SKILL_B,
    SKILL_C,
    SKILL_D,
    SKILL_E,
    SKILL_F,
    SKILL_G,
    SKILL_H,
    backpack,
    completed,
    library,
    probe_layer,
    run_state,
    value_table,
)

NODE_KEY = progress_key(LAYER_ID, "entry")
"""主线入口的进度键：子流名里的 ``<节点>``。"""


def _draws(source: RandomSource, count: int = 5) -> list[int]:
    return [source.randbelow(1000) for _ in range(count)]


def _compile(room_id: str = "room.story", *, node_id: str = "entry", **overrides):
    """按房间 id + 节点 id 编译一场（房间与节点都取自手搓的库）。"""
    lib = library()
    layer = probe_layer()
    room = next(room for room in lib.rooms if room.id == room_id)
    node = next(node for node in layer.nodes if node.id == node_id)
    return compile_battle(
        room=room, node=node, layer=layer, library=lib, run=run_state(**overrides)
    )


def _enter(node_id: str, **overrides) -> RunState:
    return enter_node(
        run=run_state(**overrides),
        library=library(),
        node_id=node_id,
        value_table=value_table(),
    )


# ---------------------------------------------------------------------------
# 一、九条子流：名字冻结、各走各的、同 seed 同结果
# ---------------------------------------------------------------------------


FACTORIES = {
    "script.roster": lambda: roster_stream(SEED),
    "script.bonus:<n>": lambda: bonus_stream(SEED, 3),
    "script.battle:<节点>": lambda: battle_stream(SEED, NODE_KEY),
    "script.enemy:<节点>:<座位>": lambda: enemy_stream(SEED, NODE_KEY, 2),
    "script.draw:<节点>": lambda: draw_stream(SEED, NODE_KEY),
    "script.event:<节点>": lambda: event_stream(SEED, NODE_KEY),
    "script.reward:<节点>:<n>": lambda: reward_stream(SEED, NODE_KEY, 1),
    "script.unknown:<节点>": lambda: unknown_stream(SEED, NODE_KEY),
    "script.shop:<节点>": lambda: shop_stream(SEED, NODE_KEY),
}
"""九条子流的工厂（键 = 票 03 §5 表里的名字模式）。"""

RESOLVED = {
    "script.roster": "script.roster",
    "script.bonus:<n>": "script.bonus:3",
    "script.battle:<节点>": f"script.battle:{NODE_KEY}",
    "script.enemy:<节点>:<座位>": f"script.enemy:{NODE_KEY}:2",
    "script.draw:<节点>": f"script.draw:{NODE_KEY}",
    "script.event:<节点>": f"script.event:{NODE_KEY}",
    "script.reward:<节点>:<n>": f"script.reward:{NODE_KEY}:1",
    "script.unknown:<节点>": f"script.unknown:{NODE_KEY}",
    "script.shop:<节点>": f"script.shop:{NODE_KEY}",
}
"""九条模式代入固定参数后的**字面名字**（与 ``engine/rng.py`` 的哈希派生直接对照）。"""

PATTERNS = tuple(FACTORIES)
"""本文件认识的九条模式（顺序照票 03 §5 的表）。"""


def test_the_nine_substream_names_are_exactly_the_ticket_table() -> None:
    """九条名字一个不多一个不少，且**没有** ``script.map:*``（路线不是随机结果）。"""
    assert PATTERNS == (
        "script.roster",
        "script.bonus:<n>",
        "script.battle:<节点>",
        "script.enemy:<节点>:<座位>",
        "script.draw:<节点>",
        "script.event:<节点>",
        "script.reward:<节点>:<n>",
        "script.unknown:<节点>",
        "script.shop:<节点>",
    )
    assert len(PATTERNS) == 9
    assert not any(name.startswith("script.map") for name in PATTERNS)
    assert SCRIPT_SUBSTREAMS == PATTERNS, "公开的冻结清单与测试认识的那九条一致"


@pytest.mark.parametrize("pattern", PATTERNS)
def test_each_factory_derives_from_its_own_name(pattern: str) -> None:
    """每条工厂 = ``RandomSource(seed).substream(<它自己的名字>)``：逐位相同。

    这是「名字只住一个地方」的机械判据：工厂换个名字，这条当场变红。
    """
    expected = RandomSource(SEED).substream(RESOLVED[pattern])

    assert _draws(FACTORIES[pattern]()) == _draws(expected)


def test_the_nine_resolved_names_are_pairwise_different() -> None:
    """九条名字代入参数后两两不同：不会有一条流被两条用途共用（同名即同序列）。"""
    resolved = list(RESOLVED.values())

    assert len(set(resolved)) == 9


def test_the_roster_and_bonus_substreams_are_reserved_names_with_no_consumer() -> None:
    """``script.roster`` / ``script.bonus:<n>`` 是**预留名**（票 35 的裁定，不是待补的实现）。

    - ``script.roster``：内置剧本的 roster **恒为 3 名**（``_script_errors`` 钉 3 名、
      ``RunState.roster_candidates`` 恒 3），3 选 1 由玩家拍板——抽取这一层没有随机，
      所以今天没有消费者；名字先冻在票 03 §5 的九条表里，留给"从更大池里抽 3"那种
      内容扩展（要另开票，不是本票的欠账）。
    - ``script.bonus:<n>``：「能人义士」的**赠送选技机制没有落地**（需要用户裁定），
      本仓不凭空实现。名字是预留位，定义在 :mod:`sgs.script.rng` 里已由
      :func:`test_each_factory_derives_from_its_own_name` 钉住。

    这条断言把"零消费者"从"忘了接线"变成**声明**：将来谁给它们接上消费者，就必须在
    这里改掉预留名清单（并说明新机制），不会悄悄长出一套没人裁过的选技随机。
    """
    reserved = ("roster_stream", "bonus_stream")
    src = Path(__file__).resolve().parent.parent / "src" / "sgs"
    call_sites = [
        f"{path.relative_to(src.parent.parent)}::{name}"
        for path in sorted(src.rglob("*.py"))
        if not (path.parent.name == "script" and path.name == "rng.py")
        for name in reserved
        if f"{name}(" in path.read_text(encoding="utf-8")
    ]

    assert call_sites == [], (
        "script.roster / script.bonus 是预留名（票 35）：生产代码里不该有消费者，"
        f"实得 {call_sites}"
    )


def test_the_same_seed_draws_the_same_sequence_from_every_script_stream() -> None:
    """同 seed ⇒ 每条流各自给自己同一串数（可复现的地基）。"""
    for pattern in PATTERNS:
        assert _draws(FACTORIES[pattern]()) == _draws(FACTORIES[pattern]()), pattern


def test_the_node_key_keeps_two_layers_apart() -> None:
    """``<节点>`` 是进度键：两层各有一个 ``n1`` 时，两条子流不共用序列。"""
    left = _draws(battle_stream(SEED, progress_key("l1", "n1")))
    right = _draws(battle_stream(SEED, progress_key("l2", "n1")))

    assert left != right


def test_a_stream_name_component_must_be_well_formed() -> None:
    with pytest.raises(ValueError):
        battle_stream(SEED, "")
    with pytest.raises(ValueError):
        reward_stream(SEED, NODE_KEY, -1)


# ---------------------------------------------------------------------------
# 二、两个取数工具：无放回、按权重
# ---------------------------------------------------------------------------


def test_uniform_sample_never_repeats_and_stops_at_the_pool_size() -> None:
    source = RandomSource(SEED).substream("probe")

    picked = uniform_sample(pool=("a", "b", "c"), count=3, source=source)

    assert sorted(picked) == ["a", "b", "c"], "无放回抽 3 个 = 池里的 3 个"
    assert len(uniform_sample(pool=("a", "b"), count=5, source=source)) == 2, "不够就有几张给几张"


def test_weighted_sample_follows_the_weights_and_never_repeats() -> None:
    """权重 0 的条目永不抽中；抽 ``count`` 个时逐张不放回（结果里没有重复）。"""
    for seed in range(20):
        source = RandomSource(seed).substream("probe")

        picked = weighted_sample(
            pool=("a", "b", "c"), weights={"a": 60, "b": 25, "c": 0}, count=2, source=source
        )

        assert sorted(picked) == ["a", "b"]
        assert "c" not in picked


def test_weighted_sample_hands_the_pool_back_when_every_weight_is_zero() -> None:
    source = RandomSource(SEED).substream("probe")

    assert weighted_sample(pool=("a", "b"), weights={"a": 0, "b": 0}, count=2, source=source) == ()


# ---------------------------------------------------------------------------
# 三、编译：房间 → PveConfig 逐字段
# ---------------------------------------------------------------------------


def test_the_compiled_battle_maps_every_field_of_the_room() -> None:
    """座位 / 阵营 / 主角 / 保护对象 / 胜负 / Boss / 座位表，逐项对得上房间数据。"""
    pve = _compile(backpack=backpack(SKILL_A, SKILL_B)).pve

    assert pve.identities == (
        Identity.ALLY,
        Identity.ALLY,
        Identity.ENEMY,
        Identity.ENEMY,
    ), "0 号主角 + 1 名 AI 队友 + 2 名敌人"
    assert pve.principal == 0, "主角恒为 0 号座（票 03 §3）"
    assert pve.protected_ally == 1, "受保护对象 = allies 里那一名队友的绝对座位"
    assert pve.victory.kind is PveVictoryKind.ROUT
    assert pve.boss is None, "rout 这类胜利条件不带 boss 座位"

    seats = pve.seats
    assert [seat.hero for seat in seats] == [HERO, None, None, None]
    assert [seat.name for seat in seats] == ["界刘备", "义军什长", "黄巾力士", "黄巾渠帅"]
    assert [(seat.hp, seat.hp_max) for seat in seats] == [(4, 4), (3, 3), (3, 3), (4, 4)]


def test_the_principal_seat_is_the_chosen_hero_from_the_hero_table() -> None:
    """主角座位 = ``run.chosen_hero`` 在武将表里的那一行（体力 / 势力 / 性别都来自数据）。"""
    principal = _compile().pve.seats[0]

    assert principal == hero_seat(HERO)
    assert (principal.hp, principal.hp_max) == (4, 4)
    assert principal.kingdom.value == "shu"
    assert principal.gender.value == "male"


def test_a_hero_seat_keeps_the_four_equipped_skills_and_the_relics() -> None:
    """4 条出战技能 + 遗物都经 ``grants`` 进 header（票 02 §10.3），遗物只授主角。"""
    compiled = _compile(backpack=backpack(SKILL_A, SKILL_B), relics=(RELIC_X, RELIC_Y))

    seat_zero = [grant for grant in compiled.pve.grants if grant.seat == 0]

    assert [(grant.kind, grant.id) for grant in seat_zero] == [
        ("skill", SKILL_A),
        ("skill", SKILL_B),
        ("relic", RELIC_X),
        ("relic", RELIC_Y),
    ]
    assert all(grant.kind == "skill" for grant in compiled.pve.grants if grant.seat != 0)


def test_the_ai_seats_get_their_template_skills_through_grants() -> None:
    """AI 座位的技能 = 模板的 ``initial_skills`` + 从技能池里抽到的（无放回）。"""
    compiled = _compile()
    per_seat = {
        seat: tuple(grant.id for grant in compiled.pve.grants if grant.seat == seat)
        for seat in range(4)
    }

    assert per_seat[1] == (SKILL_E,), "队友模板的固定技能"
    assert per_seat[3] == (SKILL_C,), "渠帅模板的固定技能（随机池为空）"
    drawn = per_seat[2]
    assert 1 <= len(drawn) <= 2, "小怪模板 random.count = [1, 2]"
    assert len(set(drawn)) == len(drawn), "无放回：同座位不重复"
    assert set(drawn) <= {SKILL_A, SKILL_B, SKILL_D}, "只从它自己的技能池里抽"


def test_the_template_random_skill_count_covers_both_ends_of_its_range() -> None:
    """``random.count = [1, 2]`` 的两端都真会发生（不是恒 1 / 恒 2）。"""
    counts = {
        len(
            [
                grant
                for grant in _compile(seed=seed).pve.grants
                if grant.seat == 2
            ]
        )
        for seed in range(40)
    }

    assert counts == {1, 2}


def test_the_battle_seed_is_the_first_draw_of_the_battle_stream() -> None:
    """``script.battle:<节点>`` 的第一个数就是这一场的种子（之后才是缩放）。"""
    expected = RandomSource(SEED).substream(f"script.battle:{NODE_KEY}").randbelow(1 << 64)

    assert _compile().battle_seed == expected


def test_the_same_snapshot_and_seed_compile_the_same_header() -> None:
    """同快照 + 同 seed ⇒ 同一 ``PveConfig``（header 自证：逐字段相等）。"""
    first = _compile(backpack=backpack(SKILL_A))
    second = _compile(backpack=backpack(SKILL_A))

    assert first.pve == second.pve
    assert first.battle_seed == second.battle_seed


def test_a_room_without_a_battle_is_refused() -> None:
    """非战斗房没有可编译的 ``PveConfig``（``kind`` 决定载荷，票 03 §1）。"""
    lib = library()
    layer = probe_layer()
    node = next(node for node in layer.nodes if node.id == "entry")
    event_room = next(room for room in lib.rooms if room.id == "room.event")

    with pytest.raises(ScriptError) as caught:
        compile_battle(room=event_room, node=node, layer=layer, library=lib, run=run_state())

    assert caught.value.rule_id == "script.compile.not_a_battle_room"


def test_an_unknown_room_is_not_compiled_as_a_battle() -> None:
    """问号房自己没有战斗：揭晓内层之后才谈编译（票 03 §2 的禁止递归）。"""
    lib = library()
    layer = probe_layer()
    unknown = next(room for room in lib.rooms if room.id == "room.unknown")
    node = next(node for node in layer.nodes if node.id == "unknown")

    with pytest.raises(ScriptError) as caught:
        compile_battle(room=unknown, node=node, layer=layer, library=lib, run=run_state())

    assert caught.value.rule_id == "script.compile.not_a_battle_room"
    assert unknown.kind is RoomKind.UNKNOWN


def test_compiling_a_battle_room_for_a_run_inside_one_is_refused() -> None:
    """节点内不再编译（进入节点 = 一个事务；编译只发生在进入那一步）。"""
    inside = _enter("entry")
    lib = library()
    layer = probe_layer()

    with pytest.raises(ScriptError) as caught:
        compile_battle(
            room=next(room for room in lib.rooms if room.id == "room.story"),
            node=next(node for node in layer.nodes if node.id == "entry"),
            layer=layer,
            library=lib,
            run=inside,
        )

    assert caught.value.rule_id == "script.enter.node_in_progress"


def test_the_compiled_header_passes_the_growth_limits() -> None:
    """编译出来的 header 一律过得去 ``PveConfig`` 的校验矩阵（票 09）：每座技能 ≤4。"""
    compiled = _compile(backpack=backpack(SKILL_A, SKILL_B, SKILL_C, SKILL_D))

    for seat in range(len(compiled.pve.identities)):
        skills = [
            grant for grant in compiled.pve.grants if grant.seat == seat and grant.kind == "skill"
        ]
        assert len(skills) <= 4, seat
    assert isinstance(compiled.pve.seats[0], PveSeat)


def test_the_seat_table_always_lines_up_with_the_identities() -> None:
    for room_id, node_id in (
        ("room.story", "entry"),
        ("room.draw", "draw"),
        ("room.boss", "boss"),
    ):
        pve = _compile(room_id, node_id=node_id).pve

        assert len(pve.seats) == len(pve.identities)
        assert pve.identities[0] is Identity.ALLY


def test_the_boss_seat_is_the_enemy_marked_boss() -> None:
    """``slay_boss`` ⇒ ``boss`` 指向那个 ``boss: true`` 的敌方座位（绝对座位号）。"""
    pve = _compile("room.boss", node_id="boss").pve

    assert pve.victory.kind is PveVictoryKind.SLAY_BOSS
    assert pve.boss == 1
    assert pve.identities[1] is Identity.ENEMY


def test_a_grant_keeps_the_three_fields_of_ticket_09() -> None:
    """``SkillGrant`` 的形状照票 09：``{seat, kind, id}``，编译不多写一个字段。"""
    grant = _compile(backpack=backpack(SKILL_A)).pve.grants[0]

    assert isinstance(grant, SkillGrant)
    assert set(grant.model_dump()) == {"seat", "kind", "id"}
    assert (grant.seat, grant.kind, grant.id) == (0, "skill", SKILL_A)


def test_an_empty_equipped_slot_costs_nothing_in_the_header() -> None:
    """空背包 ⇒ 主角零授予（不是"四个空 id"）。"""
    compiled = _compile()

    assert [grant for grant in compiled.pve.grants if grant.seat == 0] == []


# ---------------------------------------------------------------------------
# 四、两种敌人来源
# ---------------------------------------------------------------------------


def test_a_fixed_enemy_list_keeps_the_authored_order() -> None:
    assert _compile().enemy_templates == ("tpl.grunt", "tpl.brute")


def test_the_band_draw_never_repeats_a_template_and_stays_inside_the_band() -> None:
    """按档抽：同 ``band`` 内无放回，个数落在 ``count`` 区间里（两端都会出现）。"""
    seen_counts: set[int] = set()
    for seed in range(40):
        drawn = _compile("room.draw", node_id="draw", seed=seed).enemy_templates

        seen_counts.add(len(drawn))
        assert len(set(drawn)) == len(drawn), "无放回"
        assert set(drawn) <= {"tpl.grunt", "tpl.brute"}, "只抽同 band 的模板"
    assert seen_counts == {1, 2}, "count = [1, 2] 的两端都真会发生"


def test_a_drawn_enemy_is_a_composite_seat_with_its_template_name() -> None:
    """抽到的敌人照样落成合成武将（``hero=None`` + 模板名），座位数 = 1 + 抽到的个数。"""
    compiled = _compile("room.draw", node_id="draw")
    names = {"tpl.grunt": "黄巾力士", "tpl.brute": "黄巾渠帅"}

    enemies = compiled.pve.seats[1:]
    assert len(enemies) == len(compiled.enemy_templates)
    assert [seat.hero for seat in enemies] == [None] * len(enemies)
    assert [seat.name for seat in enemies] == [
        names[template] for template in compiled.enemy_templates
    ]


# ---------------------------------------------------------------------------
# 五、缩放：房间引用 / 覆盖 / 大节曲线继承
# ---------------------------------------------------------------------------


def _bonuses(pve) -> list[tuple[int, int, int]]:
    return [(seat.hp_max_bonus, seat.draw_bonus, seat.slash_damage_bonus) for seat in pve.seats]


def test_global_skills_scale_only_the_side_they_name() -> None:
    """``gs.enemy`` 只缩放敌方座位：主角与队友一分不加，敌人的三档各自落在区间内。"""
    pve = _compile().pve

    assert _bonuses(pve)[:2] == [(0, 0, 0), (0, 0, 0)]
    for seat in pve.seats[2:]:
        assert 1 <= seat.hp_max_bonus <= 2, "hp 区间 [1, 2]"
        assert 0 <= seat.draw_bonus <= 1, "draw 区间 [0, 1]"
        assert 1 <= seat.slash_damage_bonus <= 3, "slash 区间 [1, 3]"


def test_the_scaled_values_depend_on_the_seed() -> None:
    """缩放是**抽出来的**（同 seed 同值、不同 seed 一般不同），不是写死的常数。"""
    first = _compile(seed=1).pve
    again = _compile(seed=1).pve
    other = _compile(seed=2).pve

    assert _bonuses(first) == _bonuses(again)
    assert _bonuses(first) != _bonuses(other)


def test_an_ally_side_global_skill_scales_the_ally_side_and_overrides_the_layer_curve() -> None:
    """房间写了 ``global_skills`` 就**覆盖**大节曲线（不是相加）：``gs.ally`` 的区间是定值。"""
    pve = _compile("room.ally_buff", node_id="late").pve

    assert _bonuses(pve)[:2] == [(2, 1, 0), (2, 1, 0)], (
        "友方两座都吃 gs.ally；大节 col 2 的 (1, 0, 2) 不叠加"
    )
    assert [seat.hp_max_bonus for seat in pve.seats[2:]] == [0]


def test_a_referenced_scale_overrides_the_definition_field_by_field() -> None:
    """``GlobalSkillRef.scale`` 逐档覆盖定义：覆盖成 0 就不能再抽到定义里的区间。"""
    pve = _compile("room.scale_override", node_id="draw").pve

    assert _bonuses(pve) == [(0, 0, 0), (0, 0, 0)]


def test_the_layer_curve_is_inherited_by_column() -> None:
    """大节 ``default_scaling`` 按「最后一段 ``from_col <= 当前列``」继承，只缩放敌方。"""
    early = _bonuses(_compile("room.draw", node_id="draw").pve)[1:]
    assert set(early) == {(0, 0, 0)}, "col 1 落在第一段（全 0）"

    late = _bonuses(_compile("room.draw", node_id="late").pve)[1:]
    assert late and set(late) == {(1, 0, 2)}, "col 2 落在第二段：hp +1、slash +2"


def test_a_layer_without_a_curve_scales_nothing() -> None:
    """大节没写曲线、房间也没写全局技能 ⇒ 不缩放（``resolve_scaling`` 空）。"""
    lib = library()
    deadend = next(script for script in lib.scripts if script.id == "deadend")
    layer = deadend.layers[0]

    assert layer.default_scaling == ()
    assert resolve_scaling(battle=None, layer=layer, col=0, library=lib) == ()


def test_resolve_scaling_names_the_side_and_the_span() -> None:
    """``resolve_scaling`` 把房间引用与大节曲线合成一份「对哪一侧、哪三个区间」。"""
    lib = library()
    story = next(room for room in lib.rooms if room.id == "room.story")

    resolved = resolve_scaling(battle=story.battle, layer=probe_layer(), col=0, library=lib)

    assert len(resolved) == 1
    assert resolved[0].side.value == "enemy"
    assert resolved[0].scale == Scale(hp=(1, 2), draw=(0, 1), slash=(1, 3))


# ---------------------------------------------------------------------------
# 六、进入节点 = 一次抽完（物化进 pending.spec）
# ---------------------------------------------------------------------------


def test_entering_a_battle_room_materialises_every_random_fact() -> None:
    """战斗节点一次抽完：种子 / 敌人 / 模板技能 / 各轮候选全在 ``pending.spec`` 里。"""
    run = _enter("entry")

    assert run.pending is not None
    assert run.pending.node_id == NODE_KEY
    spec = run.pending.spec
    assert isinstance(spec, BattleSpec)
    assert spec.pve.seats[0].hero == HERO
    assert spec.enemy_templates == ("tpl.grunt", "tpl.brute")
    assert spec.battle_seed == _compile().battle_seed
    assert [len(round_) for round_ in spec.rewards] == [3, 2], (
        "轮次 = 模板累加（渠帅 pick3 → 3 张）+ 房间 extra（pick2 → 2 张）"
    )
    assert spec.boss_relic is None, "非 Boss 房不发遗物"


def test_each_reward_round_offers_distinct_skills_from_the_enabled_pool() -> None:
    """每轮候选互不相同、只出已启用的技能、不重复玩家已持有的（票 02 §3）。"""
    run = _enter("entry", backpack=backpack(SKILL_A))
    assert run.pending is not None and isinstance(run.pending.spec, BattleSpec)
    rounds = run.pending.spec.rewards
    offered = [skill for round_ in rounds for skill in round_]

    assert all(len(set(round_)) == len(round_) for round_ in rounds), "一轮之内不重复"
    assert len(set(offered)) == len(offered), "整场结算的候选跨轮也不重复"
    assert set(offered) <= {SKILL_B, SKILL_C, SKILL_D, SKILL_E, SKILL_G, SKILL_H}, (
        "已持有的（A）与禁用的（F）都排除"
    )
    assert SKILL_F not in offered, "价值表里禁用的技能不进池"


def test_entering_the_same_node_twice_draws_the_same_spec() -> None:
    """同 run seed ⇒ 同 ``spec``（进入节点这件事本身是纯函数）。"""
    first = _enter("entry")
    second = _enter("entry")

    assert first.pending is not None and second.pending is not None
    assert first.pending.spec == second.pending.spec


def test_entering_a_node_nobody_offers_is_a_readable_error() -> None:
    """不在可到达集里的节点进不去（出口只在打到最后才开）。"""
    with pytest.raises(ScriptError) as caught:
        _enter("exit")

    assert caught.value.rule_id == "script.enter.unreachable_node"
    assert caught.value.loc == progress_key(LAYER_ID, "exit")


def test_entering_a_node_while_inside_one_is_a_readable_error() -> None:
    inside = _enter("entry")

    with pytest.raises(ScriptError) as caught:
        enter_node(
            run=inside, library=library(), node_id="event", value_table=value_table()
        )

    assert caught.value.rule_id == "script.enter.node_in_progress"


def test_a_dead_end_before_entering_uses_the_board_error() -> None:
    """可到达集算不出来 ⇒ 与票 16 同一个出口（``DeadEndError``），不是空元组。"""
    deadend = library()
    entered = enter_node(
        run=run_state(script_id="deadend"),
        library=deadend,
        node_id="a",
        value_table=value_table(),
    )
    settled = settle_node(
        run=entered, library=deadend, value_table=value_table(), order=SettleOrder()
    )
    assert settled.run is not None

    with pytest.raises(DeadEndError) as caught:
        enter_node(run=settled.run, library=deadend, node_id="b", value_table=value_table())

    assert caught.value.rule_id == "script.board.dead_end.flags_blocked"


def test_entering_a_shop_materialises_the_whole_stock_once() -> None:
    """商店库存 6 技能 + 2 遗物，进入即物化（售完不补货 = 库存进档，票 03 §1）。"""
    run = _enter("shop", node_status=completed(LAYER_ID, "entry"))

    assert run.pending is not None
    spec = run.pending.spec
    assert isinstance(spec, ShopSpec)
    assert len(spec.stock.skills) == 6, "已启用 7 条 ⇒ 货架卖满 6 格（上限 6）"
    assert len(spec.stock.relics) == 2
    assert set(spec.stock.skills) <= {
        SKILL_A, SKILL_B, SKILL_C, SKILL_D, SKILL_E, SKILL_G, SKILL_H
    }
    assert set(spec.stock.relics) == {RELIC_X, RELIC_Y}
    assert (
        _enter("shop", node_status=completed(LAYER_ID, "entry")).pending.spec == spec
    ), "同 seed 同库存"


def test_entering_an_event_draws_one_event_from_its_pool() -> None:
    """事件节点进入即抽事件：``event_id`` 落在该事件池里，后果随机在此物化。"""
    run = _enter("event", node_status=completed(LAYER_ID, "entry"))

    assert run.pending is not None
    spec = run.pending.spec
    assert isinstance(spec, EventSpec)
    assert spec.event_id in {"ev.village", "ev.ambush"}
    assert spec.outcome_rolls == {}, "v1 的事件效果都是定值，没有可抽的后果（票 17 读法）"
    assert _enter("event", node_status=completed(LAYER_ID, "entry")).pending.spec == spec


def test_entering_a_question_room_reveals_the_inner_room_and_its_spec() -> None:
    """问号房进入即揭晓：内层房间 id + 内层物化 spec 一起进档（重进不重抽）。"""
    run = _enter("unknown_battle")

    assert run.pending is not None
    spec = run.pending.spec
    assert isinstance(spec, UnknownSpec)
    assert spec.revealed.room == "room.draw"
    assert isinstance(spec.revealed.spec, BattleSpec)
    assert spec.revealed.spec.enemy_templates, "内层战斗也已经抽完"


def test_a_question_room_that_reveals_an_event_keeps_the_event_spec() -> None:
    run = _enter("unknown")

    assert run.pending is not None
    spec = run.pending.spec
    assert isinstance(spec, UnknownSpec)
    assert spec.revealed.room == "room.event"
    assert isinstance(spec.revealed.spec, EventSpec)


def test_entering_a_boss_room_draws_the_relic_it_will_hand_over() -> None:
    """Boss 的那 1 件遗物也在进入时抽定（``None`` = 无货 ⇒ 结算折 10 铜钱）。"""
    run = _enter("boss", node_status=completed(LAYER_ID, "late"))

    assert run.pending is not None
    spec = run.pending.spec
    assert isinstance(spec, BattleSpec)
    assert spec.boss_relic in {RELIC_X, RELIC_Y}
    assert [len(round_) for round_ in spec.rewards] == [3, 3, 1], (
        "两次固定的三选一 + extra_reward 的 pick1"
    )


def test_entering_a_shop_with_no_relics_in_the_table_stocks_only_skills() -> None:
    """货架上限不是下限：表里没有遗物时库存的遗物段为空（有几张给几张）。"""
    table = value_table().model_copy(update={"relics": {}})

    run = enter_node(
        run=run_state(node_status=completed(LAYER_ID, "entry")),
        library=library(),
        node_id="shop",
        value_table=table,
    )

    assert run.pending is not None and isinstance(run.pending.spec, ShopSpec)
    assert run.pending.spec.stock.relics == ()
    assert len(run.pending.spec.stock.skills) == 6, "已启用 7 条 ⇒ 技能段照样卖满"


def test_the_backpack_reads_out_the_equipped_slots_and_everything_held() -> None:
    """背包派生读法：出战槽里有什么、整包有什么（授予与学习池都读它们）。"""
    bag = Backpack(slots=(SKILL_A, None, SKILL_B, None, SKILL_C) + (None,) * 19)

    assert equipped_skills(bag) == (SKILL_A, SKILL_B)
    assert held_skills(bag) == (SKILL_A, SKILL_B, SKILL_C)


def test_a_value_table_is_never_mutated_by_entering_a_node() -> None:
    """进入节点只读表：抽完不会把池子改坏（无副作用）。"""
    table = value_table()
    before = table.model_dump()

    assert enter_node(
        run=run_state(), library=library(), node_id="entry", value_table=table
    ).pending is not None
    assert table.model_dump() == before
    assert Rarity.COMMON in {value.rarity for value in table.skills.values()}
