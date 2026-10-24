"""武将表（``heroes.<pack_id>.json``）的加载、三段校验与启用门控（票 01）。

测试只经 ``sgs.data`` 公开接缝（``load_heroes`` / ``validate_heroes`` /
``hero_skill_id`` / ``hero_skill_ids`` / ``HeroTable`` /
``DataLoadError`` / ``LoadError``）。

两处跨层校验的两端是 ``sgs.skills.CATALOG`` 与数据：构造"已启用武将引用了不存在的
技能"这类坏数据必须**同时**触碰被校验的那一端（与 ``test_load_validation.py`` 的
``monkeypatch`` 姿态相同）。本票（票 01）尚未登记任何武将技能，故正向的"启用即可加载"
也用注入的假定义表达；票 12 登记 29 条真定义后，本文件的坏数据基底仍然成立。

**字面串沿革**（供 M3 写回账本核对，`.scratch/m3-standard-heroes/writeback.md` 的
「B 族第四形态缺席」一行把落点记在本文件）：M3 P0 时 ``hero.xiahoudun.ganglie`` 是
"明确不做"的反例；M6 票 01 起【刚烈】已登记，票 03 收口后 25 名全部启用。账本核的是
"这个文件里还留着这个可核对的字面串"，不是"该技能仍未实现"。
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

    M6 票 03 的 P0 收口起数据文件里 **25 名全部启用**；这里钉的是**两种取值都能原样
    读出**（字段不是"存在即启用"），而不是"全表都开"。
    """
    table = load_heroes()

    assert len(table.heroes) == STANDARD_HERO_COUNT
    assert sum(hero.enabled for hero in table.heroes) == 25

    _patch_catalog(monkeypatch)
    raw = _heroes_raw()
    raw["heroes"][_index_of(raw, "caocao")]["enabled"] = True
    assert _validate(raw).by_id("caocao").enabled is True
    raw["heroes"][_index_of(raw, "caocao")]["enabled"] = False
    assert _validate(raw).by_id("caocao").enabled is False


# ---------------------------------------------------------------------------
# 条件化技能引用校验（票 01 §Data #3）
# ---------------------------------------------------------------------------


def test_disabled_heroes_load_even_when_their_skills_are_not_implemented(monkeypatch):
    """``enabled: false`` 的武将只查结构与唯一性：技能还没实现也照样加载整张表。

    M6 票 03 的 P0 收口后 25 名全部启用，"落选武将"这一档没有真实载体，故用**合成表**
    表达：把曹操改成 ``enabled: false`` 并在 ``CATALOG`` 里拿掉他的两条技能——校验必须
    放行。条件化引用校验本身（启用 ⇒ 引用必须闭合）由下面两条反例继续钉住。
    """
    table = load_heroes()

    assert sum(hero.enabled for hero in table.heroes) == 25, "启用 25 名（P0 收口）"
    assert {"hero.ganning.qixi", "hero.lvmeng.keji", "hero.huanggai.kurou"} <= set(
        catalog_module.CATALOG
    ), "P0 收口的三条技能都已登记"

    without_caocao = {
        skill_id: definition
        for skill_id, definition in catalog_module.CATALOG.items()
        if not skill_id.startswith("hero.caocao.")
    }
    monkeypatch.setattr("sgs.skills.CATALOG", MappingProxyType(without_caocao))
    raw = _heroes_raw()
    caocao = raw["heroes"][_index_of(raw, "caocao")]
    caocao["enabled"] = False
    caocao["skills"][0]["id"] = "no_such_skill"

    assert _validate(raw).by_id("caocao") is not None, "未启用 ⇒ 引用不查"


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
    """判据用两名**相邻**的武将做基底（甘宁 → 吕蒙，名字改成同一个）。

    相邻是因为下面的 loc 断言用 ``index + 1`` 定位第二条重名记录；两人的技能都已登记，
    故唯一性错误不会被技能引用错误淹没。
    """
    raw = _heroes_raw()
    index = _index_of(raw, "ganning")
    raw["heroes"][index]["id"] = "lvmeng"
    # 重名条目连技能清单一起改成吕蒙的：否则会先报 `hero.skill_missing`（启用武将的
    # 技能引用必须闭合），把唯一性这一条淹没。
    raw["heroes"][index]["skills"] = [
        dict(skill) for skill in raw["heroes"][index + 1]["skills"]
    ]

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


# ---------------------------------------------------------------------------
# 界武将表（M6 票 04）：独立成一份数据、与内容包表分开装载
# ---------------------------------------------------------------------------
#
# 票面「数据与加载」：界标准 29 名**独立成一份武将数据**（与标准 25 名分开存，ADR-006 的
# "分开存"这次真落），每名带**真名**、版本标记、势力、体力上限、技能清单。票 04 先落两名
# （界关羽 / 界马超），后续票只追加自己的条目。故这里钉的是"**分开存但同一读取面**"：
# ``load_heroes()`` 仍是标准表（25 名）不动，而**载体解析**（``hero_skill_ids``）能按同一个
# 武将 id 找到扩展表里的条目——界将因此能被构造局面直接发放。


def test_the_standard_table_stays_the_25_and_the_jie_table_is_loaded_separately():
    standard = load_heroes()
    jie = load_heroes("jie")

    assert standard.pack_id == "standard"
    assert len(standard.heroes) == STANDARD_HERO_COUNT
    assert jie.pack_id == "jie"
    assert [hero.id for hero in jie.heroes] == [
        "jie_guanyu",
        "jie_machao",
        "jie_zhangfei",
        "jie_xushu",
        "jie_lvmeng",
        "jie_ganning",
        "jie_diaochan",
        "jie_zhouyu",
        "jie_xiahoudun",
        "jie_huangyueying",
        "jie_zhenji",
        "jie_luxun",
        "jie_caocao",
        "jie_simayi",
        "jie_zhangliao",
        "jie_xuchu",
        "jie_guojia",
        "jie_lidian",
        "jie_liubei",
        "jie_zhaoyun",
        "jie_zhugeliang",
        "jie_sunquan",
        "jie_huanggai",
        "jie_daqiao",
        "jie_sunshangxiang",
        "jie_huatuo",
        "jie_lvbu",
        "jie_huaxiong",
        "jie_gongsunzan",
    ]
    assert {hero.id for hero in standard.heroes}.isdisjoint(
        {hero.id for hero in jie.heroes}
    ), "界版武将 id 与标准版各自独立（不改既有 id）"


def test_a_jie_hero_carries_its_true_name_version_and_rule_book_stats():
    """真名 / 版本 / 势力 / 体力上限 / 技能清单逐项落定（第十九章 L2067–L2081 等）。

    真名是**字段**而不是从显示名派生（票面「数据与加载」；4 名只有界版的武将没有可剥的
    前缀）。票 04 先落界版这半；票 12 已把标准 25 名补齐并把字段收紧为必写
    （``test_the_standard_roster_carries_its_true_names_as_a_field``）。
    票 05 追加界张飞：技能类型里第一次出现**限定技**（``LIMITED``，§四 L350–L355）。
    """
    jie = load_heroes("jie")
    guanyu = jie.by_id("jie_guanyu")
    machao = jie.by_id("jie_machao")
    zhangfei = jie.by_id("jie_zhangfei")
    xushu = jie.by_id("jie_xushu")
    lvmeng = jie.by_id("jie_lvmeng")
    assert (
        guanyu is not None
        and machao is not None
        and zhangfei is not None
        and xushu is not None
        and lvmeng is not None
    )

    assert (guanyu.name, guanyu.true_name, guanyu.version) == ("界关羽", "关羽", "jie-ol")
    assert (guanyu.kingdom, guanyu.gender, guanyu.hp_max) == (
        Kingdom.SHU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in guanyu.skills] == ["wusheng", "yijue"]

    assert (machao.name, machao.true_name, machao.version) == ("界马超", "马超", "jie-ol")
    assert (machao.kingdom, machao.gender, machao.hp_max) == (
        Kingdom.SHU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in machao.skills] == ["mashu", "tieqi"]

    assert (zhangfei.name, zhangfei.true_name, zhangfei.version) == (
        "界张飞",
        "张飞",
        "jie-ol",
    )
    assert (zhangfei.kingdom, zhangfei.gender, zhangfei.hp_max) == (
        Kingdom.SHU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in zhangfei.skills] == ["paoxiao", "tishen"]
    assert [skill.skill_type for skill in zhangfei.skills] == [
        SkillType.NORMAL,
        SkillType.LIMITED,
    ], "【替身】是限定技（§四 L350–L355：整局只能发动一次）"

    # M6 票 06：界徐庶 / 界吕蒙。觉醒技与 ☆ 派生技能都在技能清单里，☆ 用
    # ``granted_by`` 记"由哪条觉醒技获得"（数据字段，不从名字里的 ☆ 派生）。
    assert (xushu.name, xushu.true_name, xushu.version) == ("界徐庶", "徐庶", "jie-ol")
    assert (xushu.kingdom, xushu.gender, xushu.hp_max) == (
        Kingdom.SHU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in xushu.skills] == ["zhuhai", "qianxue", "jianyan"]
    assert [skill.skill_type for skill in xushu.skills] == [
        SkillType.NORMAL,
        SkillType.AWAKENING,
        SkillType.NORMAL,
    ], "【潜心】是觉醒技（§十九 L2114：觉醒技 = 满足条件时强制发动）"
    assert xushu.skills[2].granted_by == "qianxue", "☆荐言由【潜心】觉醒后获得"
    assert [skill.granted_by for skill in xushu.skills[:2]] == [None, None]

    assert (lvmeng.name, lvmeng.true_name, lvmeng.version) == ("界吕蒙", "吕蒙", "jie-ol")
    assert (lvmeng.kingdom, lvmeng.gender, lvmeng.hp_max) == (
        Kingdom.WU,
        Gender.MALE,
        4,
    )
    # M6 票 11：再追加【博图】（「每轮限 X 次」+ 额外回合；§十九 L2140–L2142）。
    assert [skill.id for skill in lvmeng.skills] == [
        "keji",
        "qinxue",
        "gongxin",
        "botu",
    ]
    assert [skill.skill_type for skill in lvmeng.skills] == [
        SkillType.NORMAL,
        SkillType.AWAKENING,
        SkillType.NORMAL,
        SkillType.NORMAL,
    ]
    assert lvmeng.skills[2].granted_by == "qinxue", "☆攻心由【勤学】觉醒后获得"
    assert lvmeng.skills[3].granted_by is None, "【博图】不是 ☆ 技能"

    # M6 票 07：界甘宁 / 界貂蝉。甘宁的【奇袭】与标准版逐字相同；【奋威】是**限定技**；
    # 貂蝉两条都是普通技（【离间】的"不能被响应"是行为差异，不是技能类型差异）。
    ganning = jie.by_id("jie_ganning")
    diaochan = jie.by_id("jie_diaochan")
    assert ganning is not None and diaochan is not None
    assert (ganning.name, ganning.true_name, ganning.version) == (
        "界甘宁",
        "甘宁",
        "jie-ol",
    )
    assert (ganning.kingdom, ganning.gender, ganning.hp_max) == (
        Kingdom.WU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in ganning.skills] == ["qixi", "fenwei"]
    assert [skill.skill_type for skill in ganning.skills] == [
        SkillType.NORMAL,
        SkillType.LIMITED,
    ], "【奋威】是限定技（§十九 L2130–L2131：整局只能发动一次）"

    assert (diaochan.name, diaochan.true_name, diaochan.version) == (
        "界貂蝉",
        "貂蝉",
        "jie-ol",
    )
    assert (diaochan.kingdom, diaochan.gender, diaochan.hp_max) == (
        Kingdom.QUN,
        Gender.FEMALE,
        3,
    )
    assert [skill.id for skill in diaochan.skills] == ["lijian", "biyue"]
    assert [skill.skill_type for skill in diaochan.skills] == [
        SkillType.NORMAL,
        SkillType.NORMAL,
    ]

    # M6 票 08：界周瑜 / 界夏侯惇 / 界黄月英（手牌上限双轴 + 事件帧无效的载体）。
    zhouyu = jie.by_id("jie_zhouyu")
    xiahoudun = jie.by_id("jie_xiahoudun")
    huangyueying = jie.by_id("jie_huangyueying")
    assert zhouyu is not None and xiahoudun is not None and huangyueying is not None
    assert (zhouyu.name, zhouyu.true_name, zhouyu.version) == (
        "界周瑜",
        "周瑜",
        "jie-ol",
    )
    assert (zhouyu.kingdom, zhouyu.gender, zhouyu.hp_max) == (
        Kingdom.WU,
        Gender.MALE,
        3,
    )
    assert [skill.id for skill in zhouyu.skills] == ["yingzi", "fanjian"]

    assert (xiahoudun.name, xiahoudun.true_name, xiahoudun.version) == (
        "界夏侯惇",
        "夏侯惇",
        "jie-ol",
    )
    assert (xiahoudun.kingdom, xiahoudun.gender, xiahoudun.hp_max) == (
        Kingdom.WEI,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in xiahoudun.skills] == ["ganglie", "qingjian"]

    assert (huangyueying.name, huangyueying.true_name, huangyueying.version) == (
        "界黄月英",
        "黄月英",
        "jie-ol",
    )
    assert (huangyueying.kingdom, huangyueying.gender, huangyueying.hp_max) == (
        Kingdom.SHU,
        Gender.FEMALE,
        3,
    )
    assert [skill.id for skill in huangyueying.skills] == ["jizhi", "qicai"]

    # M6 票 09：界甄姬。两条技能都是普通技；标签（以此法获得的牌不计入本回合的手牌上限）
    # 是【洛神】的行为差异，不是技能类型差异。
    zhenji = jie.by_id("jie_zhenji")
    assert zhenji is not None
    assert (zhenji.name, zhenji.true_name, zhenji.version) == (
        "界甄姬",
        "甄姬",
        "jie-ol",
    )
    assert (zhenji.kingdom, zhenji.gender, zhenji.hp_max) == (
        Kingdom.WEI,
        Gender.FEMALE,
        3,
    )
    assert [skill.id for skill in zhenji.skills] == ["luoshen", "qingguo"]
    assert [skill.skill_type for skill in zhenji.skills] == [
        SkillType.NORMAL,
        SkillType.NORMAL,
    ]

    # M6 票 10：界陆逊。谦逊是**放置类技能**（placement 三项声明在数据里），连营不是。
    luxun = jie.by_id("jie_luxun")
    assert luxun is not None
    assert (luxun.name, luxun.true_name, luxun.version) == ("界陆逊", "陆逊", "jie-ol")
    assert (luxun.kingdom, luxun.gender, luxun.hp_max) == (Kingdom.WU, Gender.MALE, 3)
    assert [skill.id for skill in luxun.skills] == ["qianxun", "lianying"]
    assert luxun.skills[0].placement is not None, "【谦逊】必须带三项放置声明"
    assert luxun.skills[1].placement is None, "【连营】不是放置类技能"

    # M6 票 14：界·魏 A（曹操 / 司马懿 / 张辽 / 许褚）。四名都在第十九章 L2012–L2039，
    # 真名与标准版同名 ⇒ 追加它们只让**版本池** 37 → 41，真名池仍是 26。
    # M6 票 15：界·魏 B（郭嘉 / 李典）。界郭嘉真名同标准版（版本 41 → 42，真名仍 26）；
    # 界李典是**只有界版**的新真名（版本 → 43、真名 → 27，§十九 L2050–L2057）。
    # M6 票 16：界刘备 / 界赵云 / 界诸葛亮三名的真名都与标准版同名（版本 43 → 46、
    # 真名仍 27，§十九 L2058–L2102）。
    # M6 票 17：界孙权 / 界黄盖 / 界大乔 / 界孙尚香四名的真名也都与标准版同名
    # （版本 46 → 50、真名仍 27，§十九 L2121–L2180）。
    caocao = jie.by_id("jie_caocao")
    simayi = jie.by_id("jie_simayi")
    zhangliao = jie.by_id("jie_zhangliao")
    xuchu = jie.by_id("jie_xuchu")
    assert caocao is not None and simayi is not None
    assert zhangliao is not None and xuchu is not None
    assert (caocao.name, caocao.true_name, caocao.version) == ("界曹操", "曹操", "jie-ol")
    assert (caocao.kingdom, caocao.gender, caocao.hp_max) == (
        Kingdom.WEI,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in caocao.skills] == ["jianxiong", "hujia"]
    assert [skill.skill_type for skill in caocao.skills] == [
        SkillType.NORMAL,
        SkillType.LORD,
    ], "【护驾】是主公技（§十九 L2014–L2018）"

    assert (simayi.name, simayi.true_name, simayi.version) == (
        "界司马懿",
        "司马懿",
        "jie-ol",
    )
    assert (simayi.kingdom, simayi.gender, simayi.hp_max) == (
        Kingdom.WEI,
        Gender.MALE,
        3,
    )
    assert [skill.id for skill in simayi.skills] == ["fankui", "guicai"]

    assert (zhangliao.name, zhangliao.true_name, zhangliao.version) == (
        "界张辽",
        "张辽",
        "jie-ol",
    )
    assert (zhangliao.kingdom, zhangliao.gender, zhangliao.hp_max) == (
        Kingdom.WEI,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in zhangliao.skills] == ["tuxi"]

    assert (xuchu.name, xuchu.true_name, xuchu.version) == ("界许褚", "许褚", "jie-ol")
    assert (xuchu.kingdom, xuchu.gender, xuchu.hp_max) == (
        Kingdom.WEI,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in xuchu.skills] == ["luoyi"]

    # M6 票 15：界郭嘉（§十九 L2040–L2044）、界李典（§十九 L2050–L2057）。两名都是 3 体力；
    # 【天妒】与标准版逐字相同，【遗计】是界版重写；【恂恂】/【忘隙】是李典的两条新技能。
    guojia = jie.by_id("jie_guojia")
    lidian = jie.by_id("jie_lidian")
    assert guojia is not None and lidian is not None
    assert (guojia.name, guojia.true_name, guojia.version) == ("界郭嘉", "郭嘉", "jie-ol")
    assert (guojia.kingdom, guojia.gender, guojia.hp_max) == (
        Kingdom.WEI,
        Gender.MALE,
        3,
    )
    assert [skill.id for skill in guojia.skills] == ["tiandu", "yiji"]
    assert [skill.skill_type for skill in guojia.skills] == [
        SkillType.NORMAL,
        SkillType.NORMAL,
    ]

    assert (lidian.name, lidian.true_name, lidian.version) == (
        "界李典",
        "李典",
        "jie-ol",
    )
    assert (lidian.kingdom, lidian.gender, lidian.hp_max) == (
        Kingdom.WEI,
        Gender.MALE,
        3,
    )
    assert [skill.id for skill in lidian.skills] == ["xunxun", "wangxi"]
    assert [skill.skill_type for skill in lidian.skills] == [
        SkillType.NORMAL,
        SkillType.NORMAL,
    ]

    # M6 票 16：界刘备（§十九 L2058–L2066）、界赵云（L2082–L2089）、界诸葛亮（L2097–L2102）。
    # 三名真名都与标准版同名；【激将】是主公技，其余五条都是普通技。
    liubei = jie.by_id("jie_liubei")
    zhaoyun = jie.by_id("jie_zhaoyun")
    zhugeliang = jie.by_id("jie_zhugeliang")
    assert liubei is not None and zhaoyun is not None and zhugeliang is not None
    assert (liubei.name, liubei.true_name, liubei.version) == ("界刘备", "刘备", "jie-ol")
    assert (liubei.kingdom, liubei.gender, liubei.hp_max) == (
        Kingdom.SHU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in liubei.skills] == ["rende", "jijiang"]
    assert [skill.skill_type for skill in liubei.skills] == [
        SkillType.NORMAL,
        SkillType.LORD,
    ], "【激将】是主公技（§十九 L2062–L2065）"

    assert (zhaoyun.name, zhaoyun.true_name, zhaoyun.version) == (
        "界赵云",
        "赵云",
        "jie-ol",
    )
    assert (zhaoyun.kingdom, zhaoyun.gender, zhaoyun.hp_max) == (
        Kingdom.SHU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in zhaoyun.skills] == ["longdan", "yajiao"]
    assert [skill.skill_type for skill in zhaoyun.skills] == [
        SkillType.NORMAL,
        SkillType.NORMAL,
    ]

    assert (zhugeliang.name, zhugeliang.true_name, zhugeliang.version) == (
        "界诸葛亮",
        "诸葛亮",
        "jie-ol",
    )
    assert (zhugeliang.kingdom, zhugeliang.gender, zhugeliang.hp_max) == (
        Kingdom.SHU,
        Gender.MALE,
        3,
    )
    assert [skill.id for skill in zhugeliang.skills] == ["guanxing", "kongcheng"]
    assert [skill.skill_type for skill in zhugeliang.skills] == [
        SkillType.NORMAL,
        SkillType.NORMAL,
    ]

    # M6 票 17：界孙权（§十九 L2121–L2127）、界黄盖（L2146–L2151）、界大乔（L2159–L2165）、
    # 界孙尚香（L2173–L2180）。四名真名都与标准版同名；【救援】是主公技。
    sunquan = jie.by_id("jie_sunquan")
    huanggai = jie.by_id("jie_huanggai")
    daqiao = jie.by_id("jie_daqiao")
    sunshangxiang = jie.by_id("jie_sunshangxiang")
    assert sunquan is not None and huanggai is not None
    assert daqiao is not None and sunshangxiang is not None
    assert (sunquan.name, sunquan.true_name, sunquan.version) == (
        "界孙权",
        "孙权",
        "jie-ol",
    )
    assert (sunquan.kingdom, sunquan.gender, sunquan.hp_max) == (
        Kingdom.WU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in sunquan.skills] == ["zhiheng", "jiuyuan"]
    assert [skill.skill_type for skill in sunquan.skills] == [
        SkillType.NORMAL,
        SkillType.LORD,
    ], "【救援】是主公技（§十九 L2124）"

    assert (huanggai.name, huanggai.true_name, huanggai.version) == (
        "界黄盖",
        "黄盖",
        "jie-ol",
    )
    assert (huanggai.kingdom, huanggai.gender, huanggai.hp_max) == (
        Kingdom.WU,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in huanggai.skills] == ["kurou", "zhaxiang"]
    assert [skill.skill_type for skill in huanggai.skills] == [
        SkillType.NORMAL,
        SkillType.NORMAL,
    ], "【诈降】是锁定技，不是主公 / 限定 / 觉醒技（锁定由代码侧声明）"

    assert (daqiao.name, daqiao.true_name, daqiao.version) == ("界大乔", "大乔", "jie-ol")
    assert (daqiao.kingdom, daqiao.gender, daqiao.hp_max) == (
        Kingdom.WU,
        Gender.FEMALE,
        3,
    )
    assert [skill.id for skill in daqiao.skills] == ["guose", "liuli"]

    assert (sunshangxiang.name, sunshangxiang.true_name, sunshangxiang.version) == (
        "界孙尚香",
        "孙尚香",
        "jie-ol",
    )
    assert (sunshangxiang.kingdom, sunshangxiang.gender, sunshangxiang.hp_max) == (
        Kingdom.WU,
        Gender.FEMALE,
        3,
    )
    assert [skill.id for skill in sunshangxiang.skills] == ["jieyin", "xiaoji"]

    # M6 票 18：界华佗 / 界吕布 / 界华雄 / 界公孙瓒（§十九 L2181–L2215）。
    # 华佗、吕布的真名与标准版同名（版本池 +2）；华雄、公孙瓒是**只有界版**的新真名
    # （版本池 +2、真名池 +2 ⇒ spec 收口的 54 个版本 / 29 个真名）。
    huatuo = jie.by_id("jie_huatuo")
    lvbu = jie.by_id("jie_lvbu")
    huaxiong = jie.by_id("jie_huaxiong")
    gongsunzan = jie.by_id("jie_gongsunzan")
    assert huatuo is not None and lvbu is not None
    assert huaxiong is not None and gongsunzan is not None
    assert (huatuo.name, huatuo.true_name, huatuo.version) == (
        "界华佗",
        "华佗",
        "jie-ol",
    )
    assert (huatuo.kingdom, huatuo.gender, huatuo.hp_max) == (
        Kingdom.QUN,
        Gender.MALE,
        3,
    )
    assert [skill.id for skill in huatuo.skills] == ["jijiu", "chuli"]

    assert (lvbu.name, lvbu.true_name, lvbu.version) == ("界吕布", "吕布", "jie-ol")
    assert (lvbu.kingdom, lvbu.gender, lvbu.hp_max) == (
        Kingdom.QUN,
        Gender.MALE,
        5,
    ), "界吕布体力上限 5（§十九 L2187）"
    assert [skill.id for skill in lvbu.skills] == ["wushuang", "liyu"]

    assert (huaxiong.name, huaxiong.true_name, huaxiong.version) == (
        "界华雄",
        "华雄",
        "jie-ol",
    )
    assert (huaxiong.kingdom, huaxiong.gender, huaxiong.hp_max) == (
        Kingdom.QUN,
        Gender.MALE,
        6,
    ), "界华雄体力上限 6（§十九 L2201）"
    assert [skill.id for skill in huaxiong.skills] == ["yaowu", "shizhan"]

    assert (gongsunzan.name, gongsunzan.true_name, gongsunzan.version) == (
        "界公孙瓒",
        "公孙瓒",
        "jie-ol",
    )
    assert (gongsunzan.kingdom, gongsunzan.gender, gongsunzan.hp_max) == (
        Kingdom.QUN,
        Gender.MALE,
        4,
    )
    assert [skill.id for skill in gongsunzan.skills] == ["qiaomeng", "yicong"]

    assert all(hero.true_name for hero in jie.heroes), "界版每名都带真名"
    assert all(hero.enabled for hero in jie.heroes), "边界突破 P1 起界将逐批启用"


def test_a_star_skill_declares_its_awakening_and_is_not_on_the_hero_card():
    """☆ 技能的**唯一**来源是觉醒技的"获得"：武将牌不产出它的实例。

    判据 = 数据侧 ``granted_by`` 指向**同一武将**的觉醒技；载体解析读
    ``hero_card_skill_ids``（只含非 ☆ 条目），故未觉醒时不存在实例（本票验收的
    "禁：用觉醒前的状态断言没有这个技能"在技能侧）。
    """
    from sgs.data import hero_card_skill_ids

    jie = load_heroes("jie")
    for hero in jie.heroes:
        awakening_ids = {
            skill.id for skill in hero.skills if skill.skill_type is SkillType.AWAKENING
        }
        star_ids = {
            skill.id for skill in hero.skills if skill.granted_by is not None
        }
        for skill in hero.skills:
            if skill.granted_by is not None:
                assert skill.granted_by in awakening_ids, (
                    f"{hero.id}.{skill.id} 的 granted_by 不是本武将的觉醒技：{skill.granted_by}"
                )
        assert set(hero_card_skill_ids(hero.id)) == {
            hero_skill_id(hero.id, skill.id)
            for skill in hero.skills
            if skill.id not in star_ids
        }, f"{hero.id} 的武将牌载体不该含 ☆ 技能"

    assert hero_card_skill_ids("jie_xushu") == (
        "hero.jie_xushu.zhuhai",
        "hero.jie_xushu.qianxue",
    )
    assert hero_card_skill_ids("jie_lvmeng") == (
        "hero.jie_lvmeng.keji",
        "hero.jie_lvmeng.qinxue",
        "hero.jie_lvmeng.botu",
    )


def test_granted_by_must_name_an_awakening_skill_of_the_same_hero():
    """加载期校验：``granted_by`` 指向不存在的技能 / 非觉醒技，都必须报错。"""
    raw = load_heroes("jie").model_dump(mode="json")
    for hero in raw["heroes"]:
        if hero["id"] == "jie_xushu":
            hero["skills"][2]["granted_by"] = "no_such_skill"
    with pytest.raises(DataLoadError) as excinfo:
        validate_heroes(raw, file="heroes.jie.json")
    assert "heroes.granted_by_unknown" in {
        error.rule_id for error in excinfo.value.errors
    }

    raw = load_heroes("jie").model_dump(mode="json")
    for hero in raw["heroes"]:
        if hero["id"] == "jie_xushu":
            hero["skills"][2]["granted_by"] = "zhuhai"
    with pytest.raises(DataLoadError) as excinfo:
        validate_heroes(raw, file="heroes.jie.json")
    assert "heroes.granted_by_not_awakening" in {
        error.rule_id for error in excinfo.value.errors
    }


def test_hero_skill_ids_lookup_covers_the_jie_supplement():
    """载体解析的唯一读取面：同一个 ``hero_skill_ids`` 认得扩展表里的武将。

    这正是"界将可用"的接缝——``resolve_instances`` 读它取武将牌上的技能清单；
    少了这一道，界将的构造局面会静默得到零个实例。
    """
    assert hero_skill_ids("jie_guanyu") == (
        "hero.jie_guanyu.wusheng",
        "hero.jie_guanyu.yijue",
    )
    assert hero_skill_ids("jie_machao") == (
        "hero.jie_machao.mashu",
        "hero.jie_machao.tieqi",
    )
    assert hero_skill_ids("jie_zhangfei") == (
        "hero.jie_zhangfei.paoxiao",
        "hero.jie_zhangfei.tishen",
    )
    assert hero_skill_ids("jie_xushu") == (
        "hero.jie_xushu.zhuhai",
        "hero.jie_xushu.qianxue",
        "hero.jie_xushu.jianyan",
    )
    assert hero_skill_ids("jie_lvmeng") == (
        "hero.jie_lvmeng.keji",
        "hero.jie_lvmeng.qinxue",
        "hero.jie_lvmeng.gongxin",
        "hero.jie_lvmeng.botu",
    )
    assert hero_skill_ids("jie_zhouyu") == (
        "hero.jie_zhouyu.yingzi",
        "hero.jie_zhouyu.fanjian",
    )
    assert hero_skill_ids("jie_xiahoudun") == (
        "hero.jie_xiahoudun.ganglie",
        "hero.jie_xiahoudun.qingjian",
    )
    assert hero_skill_ids("jie_huangyueying") == (
        "hero.jie_huangyueying.jizhi",
        "hero.jie_huangyueying.qicai",
    )
    assert hero_skill_ids("jie_zhenji") == (
        "hero.jie_zhenji.luoshen",
        "hero.jie_zhenji.qingguo",
    )
    assert hero_skill_ids("jie_luxun") == (
        "hero.jie_luxun.qianxun",
        "hero.jie_luxun.lianying",
    )
    assert hero_skill_ids("jie_liubei") == (
        "hero.jie_liubei.rende",
        "hero.jie_liubei.jijiang",
    )
    assert hero_skill_ids("jie_zhaoyun") == (
        "hero.jie_zhaoyun.longdan",
        "hero.jie_zhaoyun.yajiao",
    )
    assert hero_skill_ids("jie_zhugeliang") == (
        "hero.jie_zhugeliang.guanxing",
        "hero.jie_zhugeliang.kongcheng",
    )
    assert hero_skill_ids("jie_sunquan") == (
        "hero.jie_sunquan.zhiheng",
        "hero.jie_sunquan.jiuyuan",
    )
    assert hero_skill_ids("jie_huanggai") == (
        "hero.jie_huanggai.kurou",
        "hero.jie_huanggai.zhaxiang",
    )
    assert hero_skill_ids("jie_daqiao") == (
        "hero.jie_daqiao.guose",
        "hero.jie_daqiao.liuli",
    )
    assert hero_skill_ids("jie_sunshangxiang") == (
        "hero.jie_sunshangxiang.jieyin",
        "hero.jie_sunshangxiang.xiaoji",
    )
    assert hero_skill_ids("jie_huatuo") == (
        "hero.jie_huatuo.jijiu",
        "hero.jie_huatuo.chuli",
    )
    assert hero_skill_ids("jie_lvbu") == (
        "hero.jie_lvbu.wushuang",
        "hero.jie_lvbu.liyu",
    )
    assert hero_skill_ids("jie_huaxiong") == (
        "hero.jie_huaxiong.yaowu",
        "hero.jie_huaxiong.shizhan",
    )
    assert hero_skill_ids("jie_gongsunzan") == (
        "hero.jie_gongsunzan.qiaomeng",
        "hero.jie_gongsunzan.yicong",
    )
    assert hero_skill_ids("guanyu") == ("hero.guanyu.wusheng",), "标准版查法一字不变"


def test_the_jie_skill_references_close_against_the_catalog():
    """启用 ⇒ 技能引用必须闭合（与标准表同一条加载期纪律）。"""
    from sgs.skills import CATALOG

    missing = [
        hero_skill_id(hero.id, skill.id)
        for hero in load_heroes("jie").heroes
        if hero.enabled
        for skill in hero.skills
        if hero_skill_id(hero.id, skill.id) not in CATALOG
    ]
    assert missing == [], f"界版武将引用了未登记的技能：{missing}"


def test_the_jie_pack_keeps_the_same_three_segment_validation():
    """扩展表走**同一套**校验：坏版本 / 缺字段 / 技能引用不闭合都照旧报错。"""
    raw = load_heroes("jie").model_dump(mode="json")
    raw["schema_version"] = 2
    with pytest.raises(DataLoadError) as excinfo:
        validate_heroes(raw, file="heroes.jie.json")
    assert [error.rule_id for error in excinfo.value.errors] == ["version.unknown"]

    raw = load_heroes("jie").model_dump(mode="json")
    del raw["heroes"][0]["true_name"]
    with pytest.raises(DataLoadError) as excinfo:
        validate_heroes(raw, file="heroes.jie.json")
    assert [error.rule_id for error in excinfo.value.errors] == ["structure.missing"]
    assert excinfo.value.errors[0].loc == "/heroes/0/true_name"


# ---------------------------------------------------------------------------
# 真名（M6 票 12）：标准 25 名补齐；必写；同一真名的版本不重复
# ---------------------------------------------------------------------------


def test_the_standard_roster_carries_its_true_names_as_a_field():
    """标准 25 名的真名逐项落定（手工抄写的独立真源，不从显示名回读）。

    "真名是字段、不从显示名派生"在标准表上看不出差别（25 名没有可剥的前缀），真正的
    判据在界版那半：4 名只有界版的武将（M6 现有的界徐庶）没有可剥的"界"前缀。本表
    同时钉住"每名都有值"—— 真名是配将的分组键，缺一条会让真名池静默少一个。
    """
    table = load_heroes()

    assert {hero.id: hero.true_name for hero in table.heroes} == {
        "caocao": "曹操",
        "simayi": "司马懿",
        "xiahoudun": "夏侯惇",
        "zhangliao": "张辽",
        "xuchu": "许褚",
        "guojia": "郭嘉",
        "zhenji": "甄姬",
        "liubei": "刘备",
        "guanyu": "关羽",
        "zhangfei": "张飞",
        "zhaoyun": "赵云",
        "machao": "马超",
        "zhugeliang": "诸葛亮",
        "huangyueying": "黄月英",
        "sunquan": "孙权",
        "ganning": "甘宁",
        "lvmeng": "吕蒙",
        "huanggai": "黄盖",
        "zhouyu": "周瑜",
        "daqiao": "大乔",
        "luxun": "陆逊",
        "sunshangxiang": "孙尚香",
        "huatuo": "华佗",
        "lvbu": "吕布",
        "diaochan": "貂蝉",
    }


def test_true_name_is_a_required_field_now_that_both_tables_carry_it():
    """票 04 把真名留成可选的过渡到此结束：缺字段是结构错误，不是"按显示名补"。"""
    raw = _heroes_raw()
    del raw["heroes"][0]["true_name"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.missing"]
    assert errors[0].loc == "/heroes/0/true_name"


def test_an_empty_true_name_is_rejected():
    raw = _heroes_raw()
    raw["heroes"][0]["true_name"] = ""

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.string_too_short"]
    assert errors[0].loc == "/heroes/0/true_name"


def test_one_true_name_cannot_have_two_records_of_the_same_version():
    """同一真名的同一版本至多一条记录（"一个版本"与"一名武将"一一对应）。

    跨表的同一真名（标准关羽 / 界关羽）是**不同版本**，不受这条限制；受限制的是"标准表
    里出现两条标准曹操"这种会让版本抽签与座位不变量都失真的事。
    """
    raw = _heroes_raw()
    raw["heroes"][_index_of(raw, "simayi")]["true_name"] = "曹操"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["heroes.duplicate_true_name_version"]
    assert errors[0].loc == f"/heroes/{_index_of(raw, 'simayi')}/true_name"
    assert "曹操" in errors[0].expected
