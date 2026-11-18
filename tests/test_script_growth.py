"""M7 成长层数据面（票 15）：价值表 / 5 预设 / 学习池。

依据 [票 02 §4、§5]。本文件只钉**数据面**：默认清单与内容对账、三段门、价与权重常数、
"不在清单里 = 普通 + 禁用"、预设只存差异与读回合并、学习池的减法。抽卡 / 商店 / 背包容量
这些运行语义归票 17 / 22，不在这里提前实现。

期望值一律从**第二来源**取：

- 武将技能集 = 武将牌上**已实现**的技能（全部武将表 ∩ ``CATALOG``，不看武将的 ``enabled``；
  与 ``tools/pool_census.py`` 的"启用武将技能集"在今天逐条相同，``tests/test_hero_catalog.py``
  的 212 是它的人工复述，这里不抄数字）；
- 价 / 权重 / 遗物价是**票面写的纪律常数**，测试里写字面值——它们**不在表里**
  （表里出现第二份价格必须是结构错误）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from sgs.data import (
    RELIC_PRICE,
    RARITY_PRICES,
    RARITY_WEIGHTS,
    SKILL_VALUE_PRESET_SLOTS,
    SKILL_VALUE_PRESETS_FILENAME,
    SKILL_VALUE_PRESETS_SCHEMA_VERSION,
    SKILL_VALUES_SCHEMA_VERSION,
    DataLoadError,
    Gender,
    Hero,
    HeroSkill,
    HeroTable,
    Kingdom,
    Rarity,
    SkillType,
    SkillValue,
    SkillValueOverride,
    SkillValuePreset,
    SkillValueTable,
    diff_skill_values,
    hero_skill_ids,
    learning_pool,
    load_hero_tables,
    load_skill_value_presets,
    load_skill_values,
    merge_skill_values,
    save_skill_value_preset,
    skill_value,
    validate_skill_values,
)
from sgs.skills import CATALOG

DATA_FILE = (
    Path(__file__).resolve().parent.parent / "src" / "sgs" / "data" / "files" / "skill-values.json"
)
"""仓内默认表（**显式清单**）的路径：测试直接读它，才看得见"表里到底写了什么"。"""


def _derived_hero_skills() -> tuple[str, ...]:
    """武将牌上**已实现**的技能 id（与生产的语义门同一判据：全部武将表，不看 ``enabled``）。

    与 ``tools/pool_census.py`` 的"启用武将技能集"在今天逐条相同（每个带技能的武将都启用着）；
    差别只在将来有人下架武将时：那张武将牌上已实现的技能仍然合法。
    """
    return tuple(
        skill_id
        for table in load_hero_tables()
        for hero in table.heroes
        for skill_id in hero_skill_ids(hero.id)
        if skill_id in CATALOG
    )


def _raw_table() -> dict:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 默认清单：一条不多、一条不少，且遗物段与武将技能段分家
# ---------------------------------------------------------------------------


def test_the_shipped_table_lists_exactly_every_enabled_hero_skill() -> None:
    """默认清单 = 武将牌上**已实现**技能的**逐条枚举**（出厂默认启用、稀有度全「普通」）。

    这是"新技能默认普通 + PVE 禁用"的另一半：清单外的技能由
    :func:`sgs.data.skill_value` 兜底成"普通 + 禁用"；清单里则一条不落地把现有技能
    登记成启用。**数字不手抄**——期望值来自武将表的派生，加一条武将技能而忘了登记，
    这条会红（提醒做决定），不会静默变成"新技能默认启用"。
    """
    table = load_skill_values()
    derived = _derived_hero_skills()

    assert len(set(derived)) == len(derived), "派生里不该有重复的技能 id"
    assert set(table.skills) == set(derived)
    assert set(table.skills) <= set(CATALOG), "清单里的每条都要在 CATALOG 里闭合"
    assert list(table.skills) == sorted(table.skills), (
        "仓内清单按 id 排序：学习池的顺序由此稳定（新增条目按排序插入）"
    )
    assert {value.rarity for value in table.skills.values()} == {Rarity.COMMON}
    assert all(value.enabled for value in table.skills.values())


def test_the_hero_skill_section_and_the_relic_section_are_disjoint() -> None:
    """两段**分家**：遗物段只有启用与否（没有稀有度），键集与武将技能段不相交。

    遗物段现在为空（遗物载体随票 09 / 26 落地），故这两条对当前文件是**前瞻性守卫**；
    今天真正执法的是语义门那条（``not_a_relic``）——它不靠文件恰好为空。
    """
    table = load_skill_values()
    raw = _raw_table()

    assert not set(table.relics) & set(table.skills)
    for entry in raw["relics"].values():
        assert isinstance(entry, bool), "遗物段的值只有 enable / disable，不是对象"


# ---------------------------------------------------------------------------
# 三段门：版本 → 结构 → 语义
# ---------------------------------------------------------------------------


def test_an_unknown_schema_version_is_refused_before_any_structure_check() -> None:
    """版本门先跑：认不出的 ``schema_version`` 只报一条**版本**错误，不撒结构错误。"""
    raw = _raw_table()
    raw["schema_version"] = 99

    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)

    assert [error.rule_id for error in caught.value.errors] == ["version.unknown"]
    assert caught.value.errors[0].loc == "/schema_version"
    assert "99" in caught.value.errors[0].got


def test_a_missing_schema_version_is_refused_too() -> None:
    raw = _raw_table()
    del raw["schema_version"]

    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)

    assert [error.rule_id for error in caught.value.errors] == ["version.missing"]


def test_a_price_smuggled_into_the_table_is_a_structure_error() -> None:
    """价 / 权重**不落表**：表里多一个 ``price`` 必须是结构错误，而不是被静默忽略。

    这条是"不许出现第二份真相"的机械判据：有人把价格写进表里时，加载器当场拒绝。
    """
    raw = _raw_table()
    some_id = next(iter(raw["skills"]))
    raw["skills"][some_id]["price"] = 3

    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)

    errors = caught.value.errors
    assert [error.rule_id for error in errors] == ["structure.extra_forbidden"]
    assert errors[0].loc == f"/skills/{some_id}/price"


def test_a_malformed_entry_is_a_structure_error() -> None:
    """坏类型 / 缺字段都在结构门拦下：稀有度只认四个枚举值，``enabled`` 必须是真布尔。"""
    raw = _raw_table()
    some_id = next(iter(raw["skills"]))

    raw["skills"][some_id]["rarity"] = "mythic"
    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)
    assert [error.rule_id for error in caught.value.errors] == ["structure.enum"]
    assert caught.value.errors[0].loc == f"/skills/{some_id}/rarity"

    raw = _raw_table()
    raw["skills"][some_id]["enabled"] = "yes"
    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)
    assert [error.rule_id for error in caught.value.errors] == ["structure.bool_type"]

    raw = _raw_table()
    del raw["skills"][some_id]["enabled"]
    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)
    assert [error.rule_id for error in caught.value.errors] == ["structure.missing"]


def test_the_semantic_gate_refuses_a_misspelled_skill_id() -> None:
    """清单里**写错 id** 要拒——"不在清单里"是默认禁用，但"清单里写了不存在的 id"是错误。"""
    raw = _raw_table()
    del raw["skills"][_derived_hero_skills()[0]]
    raw["skills"]["hero.nobody.no_such_skill"] = {"enabled": True, "rarity": "common"}

    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)

    errors = caught.value.errors
    assert [error.rule_id for error in errors] == ["skill_values.unknown_skill"]
    assert errors[0].loc == "/skills/hero.nobody.no_such_skill"
    assert "武将牌" in errors[0].expected, "报错要指名它要的是「武将牌上的技能」"


def test_the_semantic_gate_refuses_a_non_hero_skill_in_the_skills_section() -> None:
    """``equip.*`` / 标记技能不在武将技能清单里：混进武将技能段同样是错的 id。"""
    equipment_id = next(skill_id for skill_id in sorted(CATALOG) if skill_id.startswith("equip."))
    raw = _raw_table()
    raw["skills"][equipment_id] = {"enabled": True, "rarity": "common"}

    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)

    assert [error.rule_id for error in caught.value.errors] == ["skill_values.unknown_skill"]
    assert caught.value.errors[0].loc == f"/skills/{equipment_id}"


def test_the_semantic_gate_refuses_an_unknown_relic_or_a_non_relic_in_the_relics_section() -> None:
    """遗物段：未知 id 拒；**不是遗物载体**的技能冒充遗物也拒（两段不许混）。

    第三条路（装备 / 标记写进遗物段）曾经漏过：那时只判 ``in CATALOG`` + "不是武将技能"，
    ``equip.axe`` / ``*_mark`` 都能冒充遗物。现在按**载体**判（``RELIC``；票 09 才有这一档），
    三条路各自有灯。
    """
    raw = _raw_table()
    raw["relics"]["relic.no_such_relic"] = True
    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)
    assert [error.rule_id for error in caught.value.errors] == ["skill_values.unknown_relic"]
    assert caught.value.errors[0].loc == "/relics/relic.no_such_relic"

    for impostor in (_derived_hero_skills()[0], "equip.axe", "hero.feng_yujin.dingluan_mark"):
        raw = _raw_table()
        raw["relics"][impostor] = True
        with pytest.raises(DataLoadError) as caught:
            validate_skill_values(raw)
        assert [error.rule_id for error in caught.value.errors] == [
            "skill_values.not_a_relic"
        ], impostor
        assert caught.value.errors[0].loc == f"/relics/{impostor}"


def test_the_semantic_gate_reports_every_bad_id_in_one_pass() -> None:
    """坏 id 一次报完：两处错 ⇒ 两条错误，``loc`` 各自指向出错的键。"""
    raw = _raw_table()
    raw["skills"]["hero.one.typo"] = {"enabled": True, "rarity": "common"}
    raw["skills"]["hero.two.typo"] = {"enabled": False, "rarity": "rare"}

    with pytest.raises(DataLoadError) as caught:
        validate_skill_values(raw)

    locs = [error.loc for error in caught.value.errors]
    assert locs == ["/skills/hero.one.typo", "/skills/hero.two.typo"]


# ---------------------------------------------------------------------------
# 价 / 权重是代码里的纪律常数；清单外 = 普通 + 禁用
# ---------------------------------------------------------------------------


def test_the_prices_and_weights_are_the_discipline_constants() -> None:
    """商店价 = 满包折价，一张表两用：普通 3 / 稀有 6 / 史诗 15 / 传说 30；遗物固定 10。"""
    assert RARITY_PRICES == {
        Rarity.COMMON: 3,
        Rarity.RARE: 6,
        Rarity.EPIC: 15,
        Rarity.LEGENDARY: 30,
    }
    assert RELIC_PRICE == 10
    assert RARITY_WEIGHTS == {
        Rarity.COMMON: 60,
        Rarity.RARE: 25,
        Rarity.EPIC: 12,
        Rarity.LEGENDARY: 3,
    }
    assert set(RARITY_PRICES) == set(RARITY_WEIGHTS) == set(Rarity)


def test_the_table_file_carries_no_second_copy_of_any_price_or_weight() -> None:
    """表文件只有三样东西：版本、技能段、遗物段——价与权重不在进料里。"""
    raw = _raw_table()

    assert set(raw) == {"schema_version", "skills", "relics"}
    for entry in raw["skills"].values():
        assert set(entry) == {"enabled", "rarity"}


def test_a_skill_that_is_not_in_the_table_reads_as_common_and_disabled() -> None:
    """**不在清单里 = 普通 + 禁用**（不是"缺失即报错"，也不是"默认启用"）。

    用一个真实存在于 ``CATALOG``、却不在武将技能清单里的 id 来验：默认规则看的是**表**，
    不是 ``CATALOG`` 的成员关系。
    """
    table = load_skill_values()
    outsider = next(skill_id for skill_id in sorted(CATALOG) if skill_id not in table.skills)

    value = skill_value(table, outsider)

    assert value == SkillValue(enabled=False, rarity=Rarity.COMMON)


# ---------------------------------------------------------------------------
# 学习池：价值表里已启用的武将技能 − 已拥有（背包 ∪ 出战）
# ---------------------------------------------------------------------------


def _table(skills: dict[str, bool]) -> SkillValueTable:
    """一张**合成**小表（绕开加载器）：钉"池怎么减"，不依赖内容有多大。"""
    return SkillValueTable(
        schema_version=SKILL_VALUES_SCHEMA_VERSION,
        skills={
            skill_id: SkillValue(enabled=enabled, rarity=Rarity.COMMON)
            for skill_id, enabled in skills.items()
        },
        relics={},
    )


def test_the_learning_pool_is_the_enabled_skills_minus_the_owned_ones() -> None:
    """池 = 已启用 − 已拥有；**只出启用的**，顺序照表。（票 02 §3）"""
    table = _table(
        {
            "hero.a.alpha": True,
            "hero.b.beta": True,
            "hero.c.gamma": False,
        }
    )

    assert learning_pool(table) == ("hero.a.alpha", "hero.b.beta")
    assert learning_pool(table, backpack=["hero.a.alpha"]) == ("hero.b.beta",)
    assert learning_pool(table, equipped=["hero.b.beta"]) == ("hero.a.alpha",)
    assert (
        learning_pool(
            table,
            backpack=["hero.a.alpha"],
            equipped=["hero.b.beta", "hero.c.gamma"],
        )
        == ()
    )


def test_the_learning_pool_dedupes_backpack_and_equipped_by_skill_id() -> None:
    """背包 ∪ 出战按 ``skill_id`` 去重：同一个技能在两处都存着也只减一次，池里不出重复。"""
    table = _table({"hero.a.alpha": True, "hero.b.beta": True})

    pool = learning_pool(
        table,
        backpack=["hero.a.alpha", "hero.a.alpha"],
        equipped=["hero.a.alpha"],
    )

    assert pool == ("hero.b.beta",)


def test_more_owned_skills_means_a_smaller_pool() -> None:
    """单调性：已拥有只增不减 ⇒ 池只会变小（且是真子集）。"""
    table = load_skill_values()
    ids = sorted(table.skills)

    few = learning_pool(table, backpack=[ids[0]])
    more = learning_pool(table, backpack=[ids[0]], equipped=[ids[1], ids[2]])

    assert set(more) < set(few)
    assert set(few) - set(more) == {ids[1], ids[2]}


def test_the_learning_pool_only_carries_hero_skills() -> None:
    """只收武将技能：池里的每条都是武将牌上已实现的技能（``equip.*`` / 遗物 / 标记都不进）。"""
    table = load_skill_values()
    derived = set(_derived_hero_skills())

    pool = learning_pool(table)

    assert set(pool) <= derived
    assert all(skill_id.startswith("hero.") for skill_id in pool)
    assert not set(pool) & set(table.relics)


def test_a_skill_outside_the_table_stays_out_of_the_pool_even_though_the_catalog_knows_it() -> None:
    """**红-绿对照**：``CATALOG`` 里有、清单里没加 ⇒ 池看不见；清单里加进去才看得见。

    这条是"新技能默认普通 + PVE 禁用"的机械保证：学习池读的是**价值表**，不是
    ``CATALOG`` 的成员关系。今天清单已经把**全部**武将技能收齐，所以"CATALOG 里有、清单里
    没有"的 id 必然是非武将载体（装备 / 标记）；"清单里加上就可见"那半边用合成表验证
    （真实门会拒非武将 id，另有 ``unknown_skill`` 的用例）。
    """
    table = load_skill_values()
    derived = set(_derived_hero_skills())
    listed = next(skill_id for skill_id in sorted(derived))
    outsider = next(skill_id for skill_id in sorted(CATALOG) if skill_id not in table.skills)

    assert listed in learning_pool(table), "前置：清单里的技能应当可见"
    assert outsider not in learning_pool(table), "CATALOG 里有、清单里没有 ⇒ 不可见"

    widened = _table({**{skill_id: True for skill_id in table.skills}, outsider: True})
    assert outsider in learning_pool(widened), "清单里加上它（合成的表）⇒ 可见"


# ---------------------------------------------------------------------------
# 5 个本地预设：只存差异 + 原子写 + 读回合并
# ---------------------------------------------------------------------------


def _presets_file(tmp_path: Path) -> Path:
    return tmp_path / SKILL_VALUE_PRESETS_FILENAME


def test_the_presets_start_as_five_empty_slots_without_a_file(tmp_path: Path) -> None:
    """5 个常驻槽位；缺文件 = 全空。**读不落盘**——面板拉一次预设不该留下空档。"""
    path = _presets_file(tmp_path)

    presets = load_skill_value_presets(path)

    assert SKILL_VALUE_PRESET_SLOTS == 5
    assert presets.schema_version == SKILL_VALUE_PRESETS_SCHEMA_VERSION
    assert [slot.is_empty() for slot in presets.slots] == [True] * 5
    assert not path.exists(), "读预设不得写文件"


def test_a_saved_slot_stores_only_the_differences(tmp_path: Path) -> None:
    """保存的是**差异**：只写改过的两条（一禁一升稀有度），其余四条一条不写。"""
    base = load_skill_values()
    ids = sorted(base.skills)
    disabled, upgraded = ids[0], ids[1]
    preset = SkillValuePreset(
        skills={
            disabled: SkillValueOverride(enabled=False),
            upgraded: SkillValueOverride(rarity=Rarity.LEGENDARY),
        }
    )
    path = _presets_file(tmp_path)

    saved = save_skill_value_preset(path=path, slot=2, preset=preset, base=base)

    assert saved.slots[2] == preset
    assert all(saved.slots[i].is_empty() for i in (0, 1, 3, 4))
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == {
        "schema_version": SKILL_VALUE_PRESETS_SCHEMA_VERSION,
        "slots": [
            {"skills": {}, "relics": {}}
            if i != 2
            else {
                "skills": {
                    disabled: {"enabled": False},
                    upgraded: {"rarity": "legendary"},
                },
                "relics": {},
            }
            for i in range(SKILL_VALUE_PRESET_SLOTS)
        ],
    }


def test_a_saved_slot_merges_back_with_the_default_table(tmp_path: Path) -> None:
    """读回后与默认表**合并**得到生效表；没覆盖到的行与默认表逐条相同。"""
    base = load_skill_values()
    ids = sorted(base.skills)
    preset = SkillValuePreset(
        skills={
            ids[0]: SkillValueOverride(enabled=False),
            ids[1]: SkillValueOverride(rarity=Rarity.EPIC),
        }
    )
    path = _presets_file(tmp_path)
    save_skill_value_preset(path=path, slot=4, preset=preset, base=base)

    loaded = load_skill_value_presets(path, base=base)
    effective = merge_skill_values(base, loaded.slots[4])

    assert loaded.slots[4] == preset
    assert effective.skills[ids[0]].enabled is False
    assert effective.skills[ids[1]].rarity == Rarity.EPIC
    assert effective.skills[ids[2]] == base.skills[ids[2]], "没覆盖到的行一字不动"
    assert set(effective.skills) == set(base.skills)
    assert effective.relics == base.relics
    assert effective.schema_version == base.schema_version


def test_saving_one_slot_leaves_the_other_slots_alone(tmp_path: Path) -> None:
    base = load_skill_values()
    ids = sorted(base.skills)
    path = _presets_file(tmp_path)

    save_skill_value_preset(
        path=path,
        slot=0,
        preset=SkillValuePreset(skills={ids[0]: SkillValueOverride(enabled=False)}),
        base=base,
    )
    save_skill_value_preset(
        path=path,
        slot=4,
        preset=SkillValuePreset(skills={ids[1]: SkillValueOverride(rarity=Rarity.RARE)}),
        base=base,
    )

    loaded = load_skill_value_presets(path, base=base)
    assert loaded.slots[0].skills == {ids[0]: SkillValueOverride(enabled=False)}
    assert loaded.slots[4].skills == {ids[1]: SkillValueOverride(rarity=Rarity.RARE)}
    assert all(loaded.slots[i].is_empty() for i in (1, 2, 3))


def test_an_empty_preset_clears_a_slot(tmp_path: Path) -> None:
    """清空一个槽位 = 保存一份空差异（不是删槽、也不是拒绝）。"""
    base = load_skill_values()
    ids = sorted(base.skills)
    path = _presets_file(tmp_path)
    save_skill_value_preset(
        path=path,
        slot=1,
        preset=SkillValuePreset(skills={ids[0]: SkillValueOverride(enabled=False)}),
        base=base,
    )

    save_skill_value_preset(path=path, slot=1, preset=SkillValuePreset(), base=base)

    assert load_skill_value_presets(path, base=base).slots[1].is_empty()


def test_saving_refuses_a_preset_that_is_not_a_difference(tmp_path: Path) -> None:
    """"只存差异"是**写入口的判据**：条目与默认表相同 ⇒ 拒（且**不写文件**）。

    否则同一份状态有两种写法（写进去 / 不写），文件会随保存次数长大，
    而"一条覆盖生效了没有"要逐条比对才知道。
    """
    base = load_skill_values()
    ids = sorted(base.skills)
    path = _presets_file(tmp_path)

    with pytest.raises(ValueError) as caught:
        save_skill_value_preset(
            path=path,
            slot=0,
            preset=SkillValuePreset(skills={ids[0]: SkillValueOverride(enabled=True)}),
            base=base,
        )
    assert ids[0] in str(caught.value)

    with pytest.raises(ValueError):
        save_skill_value_preset(
            path=path,
            slot=0,
            preset=SkillValuePreset(
                skills={ids[0]: SkillValueOverride(rarity=Rarity.COMMON)}
            ),
            base=base,
        )

    assert not path.exists(), "被拒的保存不得留下文件"


def test_saving_refuses_unknown_ids_and_a_bad_slot(tmp_path: Path) -> None:
    """写入口 fail-closed：未知技能 / 未知遗物 / 槽位越界都报错且不落盘。"""
    base = load_skill_values()
    path = _presets_file(tmp_path)

    for slot in (-1, SKILL_VALUE_PRESET_SLOTS):
        with pytest.raises(ValueError):
            save_skill_value_preset(path=path, slot=slot, preset=SkillValuePreset(), base=base)

    with pytest.raises(ValueError) as caught:
        save_skill_value_preset(
            path=path,
            slot=0,
            preset=SkillValuePreset(
                skills={"hero.nobody.no_such_skill": SkillValueOverride(enabled=False)}
            ),
            base=base,
        )
    assert "hero.nobody.no_such_skill" in str(caught.value)

    with pytest.raises(ValueError) as caught:
        save_skill_value_preset(
            path=path,
            slot=0,
            preset=SkillValuePreset(relics={"relic.no_such_relic": True}),
            base=base,
        )
    assert "relic.no_such_relic" in str(caught.value)

    assert not path.exists()


def test_a_broken_presets_file_starts_over_as_five_empty_slots(tmp_path: Path) -> None:
    """坏文件 / 认不出的版本 / 槽数不对 ⇒ 5 个空槽（与将池预设同口径：本地状态不炸面板）。

    坏档不静默只约束**存档**（票 04 §5）；预设和 ``hero-presets.json`` 一样是本地便利状态。
    """
    path = _presets_file(tmp_path)
    empty = [True] * SKILL_VALUE_PRESET_SLOTS

    path.write_text("{ not json", encoding="utf-8")
    assert [slot.is_empty() for slot in load_skill_value_presets(path).slots] == empty

    path.write_text(
        json.dumps({"schema_version": 99, "slots": [{} for _ in range(5)]}),
        encoding="utf-8",
    )
    assert [slot.is_empty() for slot in load_skill_value_presets(path).slots] == empty

    path.write_text(
        json.dumps({"schema_version": 1, "slots": [{} for _ in range(3)]}),
        encoding="utf-8",
    )
    assert [slot.is_empty() for slot in load_skill_value_presets(path).slots] == empty


def test_stale_ids_in_a_preset_file_are_dropped_on_read(tmp_path: Path) -> None:
    """读取时的语义清理：表里没有的 id（武将下架 / 拼错）与"其实没差异"的条目直接丢掉。"""
    base = load_skill_values()
    ids = sorted(base.skills)
    path = _presets_file(tmp_path)
    path.write_text(
        json.dumps(
            {
                "schema_version": SKILL_VALUE_PRESETS_SCHEMA_VERSION,
                "slots": [
                    {
                        "skills": {
                            "hero.nobody.no_such_skill": {"enabled": False},
                            ids[0]: {"enabled": True},
                            ids[1]: {"enabled": False},
                        },
                        "relics": {"relic.no_such_relic": True},
                    },
                    *[{} for _ in range(SKILL_VALUE_PRESET_SLOTS - 1)],
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_skill_value_presets(path, base=base)

    assert loaded.slots[0].skills == {ids[1]: SkillValueOverride(enabled=False)}
    assert loaded.slots[0].relics == {}
    assert all(loaded.slots[i].is_empty() for i in range(1, SKILL_VALUE_PRESET_SLOTS))


def test_an_override_needs_at_least_one_field() -> None:
    """空覆盖不是差异：``SkillValueOverride()`` 在构造期就是结构错误。"""
    with pytest.raises(ValidationError):
        SkillValueOverride()


def test_a_failed_write_keeps_the_previous_file_and_leaves_no_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """原子写：替换失败 ⇒ 目标文件还是上一版（进程被杀不留半份），``.tmp`` 也不留。"""
    from sgs.data import values as values_module

    base = load_skill_values()
    ids = sorted(base.skills)
    path = _presets_file(tmp_path)
    save_skill_value_preset(
        path=path,
        slot=0,
        preset=SkillValuePreset(skills={ids[0]: SkillValueOverride(enabled=False)}),
        base=base,
    )
    before = path.read_text(encoding="utf-8")

    def boom(source: object, target: object) -> None:
        raise OSError("磁盘写不进去")

    monkeypatch.setattr(values_module.os, "replace", boom)
    with pytest.raises(OSError):
        save_skill_value_preset(
            path=path,
            slot=1,
            preset=SkillValuePreset(skills={ids[1]: SkillValueOverride(rarity=Rarity.RARE)}),
            base=base,
        )

    assert path.read_text(encoding="utf-8") == before, "替换失败不该动到上一版"
    assert not list(tmp_path.glob("*.tmp")), "失败的写不该留下 .tmp"


def test_diff_and_merge_are_inverse_on_the_value_rows() -> None:
    """``diff`` / ``merge`` 互逆：同一张生效表走一趟差异再合并，逐行相同。"""
    base = _table({"hero.a.alpha": True, "hero.b.beta": True, "hero.c.gamma": False})
    effective = SkillValueTable(
        schema_version=base.schema_version,
        skills={
            "hero.a.alpha": SkillValue(enabled=True, rarity=Rarity.LEGENDARY),
            "hero.b.beta": SkillValue(enabled=False, rarity=Rarity.COMMON),
            "hero.c.gamma": SkillValue(enabled=True, rarity=Rarity.COMMON),
        },
        relics={},
    )

    preset = diff_skill_values(base, effective)

    assert preset == SkillValuePreset(
        skills={
            "hero.a.alpha": SkillValueOverride(rarity=Rarity.LEGENDARY),
            "hero.b.beta": SkillValueOverride(enabled=False),
            "hero.c.gamma": SkillValueOverride(enabled=True),
        }
    )
    assert merge_skill_values(base, preset) == effective
    assert diff_skill_values(base, base).is_empty()


def test_diff_refuses_tables_with_different_id_sets() -> None:
    """差异只覆盖**值**；id 集合变了不是"预设"能表达的（那是换了一张表）。"""
    base = _table({"hero.a.alpha": True})
    wider = _table({"hero.a.alpha": True, "hero.b.beta": True})

    with pytest.raises(ValueError):
        diff_skill_values(base, wider)


def test_a_preset_that_disables_a_skill_removes_it_from_the_pool(tmp_path: Path) -> None:
    """端到端的数据面联动：预设禁用 ⇒ 合并后的生效表里学习池少一条。"""
    base = load_skill_values()
    ids = sorted(base.skills)
    path = _presets_file(tmp_path)
    save_skill_value_preset(
        path=path,
        slot=3,
        preset=SkillValuePreset(skills={ids[0]: SkillValueOverride(enabled=False)}),
        base=base,
    )
    loaded = load_skill_value_presets(path, base=base)

    effective = merge_skill_values(base, loaded.slots[3])

    assert ids[0] in learning_pool(base)
    assert ids[0] not in learning_pool(effective)


def test_a_disabled_hero_does_not_make_the_table_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    """武将 ``enabled: false`` 只是**发放**门控：它牌上已实现的技能仍在清单里合法。

    语义门的合法集口径因此**不看** ``enabled``（只看"武将牌上有、``CATALOG`` 里实现了"）——
    否则下架一名武将会让 ``load_skill_values()`` 在运行期直接抛错，整张成长层表跟着崩。
    """
    from sgs.data import values as values_module

    probe_skill = sorted(_derived_hero_skills())[0]
    probe = HeroTable(
        schema_version=1,
        pack_id="probe",
        heroes=(
            Hero(
                id="probe_hero",
                name="探针",
                true_name="探针",
                kingdom=Kingdom.QUN,
                gender=Gender.MALE,
                hp_max=4,
                skills=(
                    HeroSkill(
                        id="probe_skill",
                        name="探针技",
                        skill_type=SkillType.NORMAL,
                        skill_id=probe_skill,
                    ),
                ),
                version="probe",
                enabled=False,
            ),
        ),
    )
    monkeypatch.setattr("sgs.data.loader.load_hero_tables", lambda *args, **kwargs: (probe,))
    values_module._implemented_hero_skill_ids.cache_clear()
    try:
        table = validate_skill_values(
            {
                "schema_version": SKILL_VALUES_SCHEMA_VERSION,
                "skills": {probe_skill: {"enabled": True, "rarity": "common"}},
                "relics": {},
            }
        )
    finally:
        values_module._implemented_hero_skill_ids.cache_clear()

    assert list(table.skills) == [probe_skill]
    assert probe_skill in learning_pool(table)


def test_a_preset_can_switch_a_relic_and_the_difference_round_trips() -> None:
    """遗物覆盖的那半条差异段也要有灯（遗物段现在为空，用合成表钉纯函数的语义）。

    票 02 §6：遗物**在价值表里可禁用**（禁用 = 商店 / 事件 / Boss 不出现、开局不生效）
    ⇒ 本地预设必须能改遗物行，否则那道开关没有落点。
    """
    base = SkillValueTable(
        schema_version=SKILL_VALUES_SCHEMA_VERSION,
        skills={"hero.a.alpha": SkillValue(enabled=True, rarity=Rarity.COMMON)},
        relics={"relic.sample": True, "relic.kept": True},
    )
    preset = SkillValuePreset(relics={"relic.sample": False})

    effective = merge_skill_values(base, preset)

    assert effective.relics == {"relic.sample": False, "relic.kept": True}
    assert diff_skill_values(base, effective) == preset
    assert diff_skill_values(base, base).relics == {}


def test_a_relic_override_that_matches_the_table_is_refused(tmp_path: Path) -> None:
    """复述"只存差异"对遗物那半也成立：与表相同的遗物行不是差异。"""
    base = SkillValueTable(
        schema_version=SKILL_VALUES_SCHEMA_VERSION,
        skills={},
        relics={"relic.sample": True},
    )

    with pytest.raises(ValueError):
        save_skill_value_preset(
            path=_presets_file(tmp_path),
            slot=0,
            preset=SkillValuePreset(relics={"relic.sample": True}),
            base=base,
        )
