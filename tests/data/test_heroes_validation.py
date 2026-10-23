"""武将表（``heroes.<pack_id>.json``）的加载、三段校验与启用门控（票 01）。

测试只经 ``sgs.data`` 公开接缝（``load_heroes`` / ``validate_heroes`` /
``hero_skill_id`` / ``hero_skill_ids`` / ``HeroTable`` /
``DataLoadError`` / ``LoadError``）。

两处跨层校验的两端是 ``sgs.skills.CATALOG`` 与数据：构造"已启用武将引用了不存在的
技能"这类坏数据必须**同时**触碰被校验的那一端（与 ``test_load_validation.py`` 的
``monkeypatch`` 姿态相同）。本票（票 01）尚未登记任何武将技能，故正向的"启用即可加载"
也用注入的假定义表达；票 12 登记 29 条真定义后，本文件的坏数据基底仍然成立。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest

from sgs.data import (
    DataLoadError,
    Gender,
    HeroTable,
    Kingdom,
    LoadError,
    SkillType,
    hero_skill_id,
    hero_skill_ids,
    load_heroes,
    validate_heroes,
)
from sgs.skills import CarrierKind, SkillDef
from sgs.skills import catalog as catalog_module

STANDARD_HERO_COUNT = 25


def _heroes_raw() -> dict[str, Any]:
    """已通过校验的标准武将表，转回 JSON 形状，作为改坏数据的基底。"""
    return load_heroes().model_dump(mode="json")


def _validate(raw: dict[str, Any]) -> HeroTable:
    return validate_heroes(raw, file="heroes.standard.json")


def _errors(raw: dict[str, Any]) -> tuple[LoadError, ...]:
    with pytest.raises(DataLoadError) as excinfo:
        _validate(raw)
    return excinfo.value.errors


def _index_of(raw: dict[str, Any], hero_id: str) -> int:
    return [hero["id"] for hero in raw["heroes"]].index(hero_id)


def _fake_hero_defs() -> MappingProxyType:
    """给曹操的两条技能装上**假定义**，让"启用的武将"有一端可闭合。

    ``SkillType`` 与数据条目上的取值一致；票 12 落地真定义后这段注入即可删除。
    """
    injected = {
        "hero.caocao.jianxiong": SkillDef(
            skill_id="hero.caocao.jianxiong",
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
        ),
        "hero.caocao.hujia": SkillDef(
            skill_id="hero.caocao.hujia",
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.LORD,
        ),
    }
    return MappingProxyType({**catalog_module.CATALOG, **injected})


def _patch_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sgs.skills.CATALOG", _fake_hero_defs())


# ---------------------------------------------------------------------------
# 正常加载与只读查询
# ---------------------------------------------------------------------------


def test_loads_the_standard_hero_table():
    """装全标准版 25 名；字段落定（票 01 §Data / `03` §3「武将数据模型」）。"""
    table = load_heroes()

    assert isinstance(table, HeroTable)
    assert table.schema_version == 1
    assert table.pack_id == "standard"
    assert len(table.heroes) == STANDARD_HERO_COUNT

    caocao = table.by_id("caocao")
    assert caocao is not None
    assert (caocao.name, caocao.kingdom, caocao.gender, caocao.hp_max) == (
        "曹操",
        Kingdom.WEI,
        Gender.MALE,
        4,
    )
    assert caocao.version == "standard"
    assert [skill.id for skill in caocao.skills] == ["jianxiong", "hujia"]
    assert [skill.skill_type for skill in caocao.skills] == [
        SkillType.NORMAL,
        SkillType.LORD,
    ]


def test_the_table_covers_the_whole_ninth_chapter_roster():
    """25 名 = 魏 7 / 蜀 7 / 吴 8 / 群 3，且五名女性与第九章一致（人工核对过的表驱动断言）。"""
    table = load_heroes()

    by_kingdom = {kingdom: 0 for kingdom in Kingdom}
    for hero in table.heroes:
        by_kingdom[hero.kingdom] += 1
    assert by_kingdom == {
        Kingdom.WEI: 7,
        Kingdom.SHU: 7,
        Kingdom.WU: 8,
        Kingdom.QUN: 3,
    }
    assert {hero.id for hero in table.heroes if hero.gender is Gender.FEMALE} == {
        "zhenji",
        "huangyueying",
        "daqiao",
        "sunshangxiang",
        "diaochan",
    }


def test_hero_skill_ids_are_fully_qualified_and_keep_the_data_order():
    assert hero_skill_id("caocao", "hujia") == "hero.caocao.hujia"
    assert hero_skill_ids("caocao") == (
        "hero.caocao.jianxiong",
        "hero.caocao.hujia",
    )
    assert hero_skill_ids("no_such_hero") == (), "未知武将不产出技能，且不打断整局"


def test_the_enabled_flag_is_required_and_read_back_from_the_data(monkeypatch):
    """``enabled`` 是声明式字段：数据里没写就是结构错误，写了就原样读出（不静默取默认）。

    M6 票 02 起数据文件里 **22 名**启用（P0 三票收口后到 25）、3 名未启用；这里钉的是
    **两种取值都能原样读出**，而不是"全表都关"。
    """
    table = load_heroes()

    assert len(table.heroes) == STANDARD_HERO_COUNT
    assert sum(hero.enabled for hero in table.heroes) == 22

    _patch_catalog(monkeypatch)
    raw = _heroes_raw()
    raw["heroes"][_index_of(raw, "caocao")]["enabled"] = True
    assert _validate(raw).by_id("caocao").enabled is True
    raw["heroes"][_index_of(raw, "caocao")]["enabled"] = False
    assert _validate(raw).by_id("caocao").enabled is False


# ---------------------------------------------------------------------------
# 条件化技能引用校验（票 01 §Data #3）
# ---------------------------------------------------------------------------


def test_disabled_heroes_load_even_when_their_skills_are_not_implemented():
    """未启用的武将只查结构与唯一性：技能还没实现也照样加载整张表。

    判据取**尚未实现**的技能（甘宁【奇袭】：M6 P0 收口也还没轮到，故他仍未启用、
    ``hero.ganning.qixi`` 不在 ``CATALOG``），故这条断言验的是"条件化引用校验"，
    不随 P0 的票依次推进而失效（M3 的落点记录曾拿 ``hero.xiahoudun.ganglie`` 当"明确不做"
    的反例；M6 票 01 起它已登记；票 02 又把甄姬 / 张飞 / 黄月英启用，故改用甘宁）。
    """
    table = load_heroes()

    assert sum(hero.enabled for hero in table.heroes) == 22, "启用 22 名（M6 P0 票 02 后）"
    assert not table.by_id("ganning").enabled, "落选武将不启用（故引用不查）"
    assert "hero.ganning.qixi" not in catalog_module.CATALOG


def test_an_enabled_hero_whose_skills_all_exist_loads(monkeypatch):
    _patch_catalog(monkeypatch)
    raw = _heroes_raw()
    raw["heroes"][_index_of(raw, "caocao")]["enabled"] = True

    table = _validate(raw)

    assert table.by_id("caocao").enabled is True


def test_an_enabled_hero_referencing_a_missing_skill_is_rejected(monkeypatch):
    _patch_catalog(monkeypatch)
    raw = _heroes_raw()
    caocao = raw["heroes"][_index_of(raw, "caocao")]
    caocao["enabled"] = True
    caocao["skills"][0]["id"] = "no_such_skill"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["hero.skill_missing"]
    assert errors[0].loc == f"/heroes/{_index_of(raw, 'caocao')}/skills/0/id"
    assert "hero.caocao.no_such_skill" in errors[0].expected
    assert errors[0].got == "'no_such_skill'"


def test_a_disabled_hero_may_reference_a_missing_skill(monkeypatch):
    """`enabled: false` 时同一处坏引用**不**报错——条件化校验的另一半。"""
    _patch_catalog(monkeypatch)
    raw = _heroes_raw()
    caocao = raw["heroes"][_index_of(raw, "caocao")]
    caocao["enabled"] = False
    caocao["skills"][0]["id"] = "no_such_skill"

    assert _validate(raw).by_id("caocao") is not None


# ---------------------------------------------------------------------------
# 结构与唯一性（对全体武将，无论启用与否）
# ---------------------------------------------------------------------------


def test_duplicate_hero_id_is_rejected():
    """判据用**未启用**的武将做基底：启用会连带报出技能引用错误，淹没唯一性这一条。

    M6 票 01 起夏侯惇已启用，故基底换成两名**相邻且此刻仍未启用**的武将（甘宁 → 吕蒙；
    相邻是因为下面的 loc 断言用 ``index + 1`` 定位第二条重名记录）。
    """
    raw = _heroes_raw()
    index = _index_of(raw, "ganning")
    raw["heroes"][index]["id"] = "lvmeng"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["heroes.duplicate_id"]
    assert errors[0].loc == f"/heroes/{index + 1}/id"


def test_non_positive_hp_max_is_rejected():
    raw = _heroes_raw()
    raw["heroes"][0]["hp_max"] = 0

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["hero.hp_max_range"]
    assert errors[0].loc == "/heroes/0/hp_max"


def test_a_hero_must_have_at_least_one_skill():
    raw = _heroes_raw()
    raw["heroes"][0]["skills"] = []

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["hero.skills_empty"]
    assert errors[0].loc == "/heroes/0/skills"


def test_duplicate_skill_id_within_one_hero_is_rejected():
    raw = _heroes_raw()
    caocao = raw["heroes"][_index_of(raw, "caocao")]
    caocao["skills"][1]["id"] = caocao["skills"][0]["id"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["hero.skill_duplicate_id"]
    assert errors[0].loc == f"/heroes/{_index_of(raw, 'caocao')}/skills/1/id"


def test_empty_hero_list_is_rejected():
    raw = _heroes_raw()
    raw["heroes"] = []

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["heroes.empty"]
    assert errors[0].loc == "/heroes"


def test_missing_required_hero_field_is_reported_with_its_location():
    raw = _heroes_raw()
    del raw["heroes"][0]["kingdom"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.missing"]
    assert errors[0].loc == "/heroes/0/kingdom"


def test_enabled_and_skill_type_are_required_declarations():
    """字段表里的 ``enabled`` 与技能条目的 ``skill_type`` 必写，不靠模型默认值兜底。"""
    raw = _heroes_raw()
    del raw["heroes"][0]["enabled"]
    del raw["heroes"][0]["skills"][0]["skill_type"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.missing", "structure.missing"]
    assert {error.loc for error in errors} == {
        "/heroes/0/enabled",
        "/heroes/0/skills/0/skill_type",
    }


def test_unknown_kingdom_is_rejected_as_a_structure_error():
    raw = _heroes_raw()
    raw["heroes"][0]["kingdom"] = "han"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.enum"]
    assert errors[0].loc == "/heroes/0/kingdom"
    assert "han" in errors[0].got


def test_unknown_skill_type_is_rejected_as_a_structure_error():
    raw = _heroes_raw()
    raw["heroes"][0]["skills"][0]["skill_type"] = "ULTIMATE"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.enum"]
    assert errors[0].loc == "/heroes/0/skills/0/skill_type"


def test_extra_field_on_a_hero_is_forbidden():
    raw = _heroes_raw()
    raw["heroes"][0]["faction"] = "魏"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.extra_forbidden"]
    assert errors[0].loc == "/heroes/0/faction"


def test_unknown_heroes_schema_version_is_rejected():
    raw = _heroes_raw()
    raw["schema_version"] = 2  # 武将表认得的版本是 {1}，与内容包的 {2} 分开

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["version.unknown"]
    assert errors[0].loc == "/schema_version"


def test_version_is_checked_before_structure():
    raw = _heroes_raw()
    raw["schema_version"] = 99
    del raw["pack_id"]

    assert [error.rule_id for error in _errors(raw)] == ["version.unknown"]


def test_missing_heroes_file_is_reported():
    with pytest.raises(DataLoadError) as excinfo:
        load_heroes("no_such_pack")

    errors = excinfo.value.errors

    assert [error.rule_id for error in errors] == ["heroes.not_found"]
    assert "no_such_pack" in errors[0].got


def test_semantic_errors_are_aggregated_not_fail_fast():
    raw = _heroes_raw()
    raw["heroes"][0]["hp_max"] = 0
    raw["heroes"][1]["skills"] = []

    assert [error.rule_id for error in _errors(raw)] == [
        "hero.hp_max_range",
        "hero.skills_empty",
    ]
