"""内容包加载：正常数据加载、版本→结构→语义三段校验与聚合报错。

测试只经 ``sgs.data`` 公开接缝（``load_content_pack`` / ``validate_content_pack`` /
``ContentPack`` / ``DataLoadError`` / ``LoadError``）。
两处例外是**跨层校验的两端**：``card_type.equipment_skill_missing`` 的两端是
``sgs.skills`` 与数据、``card_type.unknown_op`` / ``behavior_orphan`` 的两端是
``sgs.effects`` 与数据——构造这两种坏数据必须触碰被校验的那一端。
"""

from __future__ import annotations

from typing import Any

import pytest

from sgs.data import (
    Category,
    ContentPack,
    DataLoadError,
    LoadError,
    card_category,
    load_content_pack,
    validate_content_pack,
)
from sgs.effects import (
    CARD_BEHAVIORS,
    CallOp,
    CardBehavior,
    FinalZone,
    ResponseWindow,
    TargetRule,
    UseContext,
    UseLimit,
    UseTiming,
)
from sgs.skills import EQUIPMENT_SKILL_IDS

BASIC_53 = "basic_53"
STANDARD_108 = "standard_108"
STANDARD_EX_160 = "standard_ex_160"


def _standard_raw() -> dict[str, Any]:
    """已通过校验的标准包（默认档），转回 JSON 形状，作为改坏数据的基底。

    只经公开接缝（``load_content_pack`` + ``model_dump``），不自己拼资源路径。
    """
    return load_content_pack().model_dump(mode="json")


def _standard_108_raw() -> dict[str, Any]:
    """同上，但把默认档切到 ``standard_108``（好断言那一档的 expect 面）。"""
    return load_content_pack(deck_profile=STANDARD_108).model_dump(mode="json")


def _basic_only_raw() -> dict[str, Any]:
    """只留 ``basic_53`` 档的基底（并把默认档指回它）：改坏牌名时不必连带修别的档的引用。"""
    raw = _standard_raw()
    del raw["decks"][STANDARD_108]
    del raw["decks"][STANDARD_EX_160]
    raw["deck_profile"] = BASIC_53
    return raw


def test_loads_standard_content_pack():
    """M2 收口后，产品默认档 = ``standard_108``（票 32 §1）；53 张档仍可显式选中。"""
    pack = load_content_pack()

    assert isinstance(pack, ContentPack)
    assert pack.schema_version == 2
    assert pack.pack_id == "standard"
    assert pack.deck_profile == STANDARD_108
    assert [card_type.id for card_type in pack.card_types][:3] == ["slash", "dodge", "peach"]
    assert len(pack.card_types) == 43, "全包 43 个牌名（标准 32 + 军争 11；默认档仍是 108）"
    assert len(pack.deck.cards) == 108

    basic = load_content_pack(deck_profile=BASIC_53)
    assert basic.deck_profile == BASIC_53
    assert len(basic.deck.cards) == 53


def test_loads_the_standard_108_profile_with_all_its_declarations():
    pack = load_content_pack(deck_profile=STANDARD_108)

    assert pack.deck_profile == STANDARD_108
    assert pack.deck is pack.decks[STANDARD_108]
    assert len(pack.deck.cards) == 108
    assert pack.decks[BASIC_53].slot_limit == 2, "分档字段不全局放宽"
    assert pack.decks[STANDARD_108].slot_limit == 3, "含 EX 的四个槽位各三张"
    expect = pack.decks[STANDARD_108].expect
    assert expect is not None
    assert expect.total == 108
    assert len(expect.by_type) == 32
    assert expect.by_suit == {"heart": 27, "diamond": 27, "spade": 27, "club": 27}
    assert expect.by_category == {"basic": 53, "trick": 36, "equipment": 19}


def test_card_category_reads_the_card_kind_from_the_pack():
    """M6 票 02：``card_category`` 是"这张牌是锦囊吗"的唯一读取口（【集智】/【奇才】）。

    牌名大类来自数据文件而不是牌名白名单：基础牌 / 锦囊 / 装备各取一张，未知牌名
    fail-closed 返回 ``None``（调用方按"不是锦囊"处理）。
    """
    assert card_category("standard", "slash") is Category.BASIC
    assert card_category("standard", "wine") is Category.BASIC
    assert card_category("standard", "duel") is Category.TRICK
    assert card_category("standard", "indulgence") is Category.TRICK
    assert card_category("standard", "axe") is Category.EQUIPMENT
    assert card_category("standard", "no_such_card") is None


def test_the_default_profile_is_selected_by_the_pack_and_can_be_overridden():
    default = load_content_pack()
    basic = load_content_pack(deck_profile=BASIC_53)
    standard = load_content_pack(deck_profile=STANDARD_108)
    military = load_content_pack(deck_profile=STANDARD_EX_160)

    assert default.deck_profile == STANDARD_108, "M2 收口翻默认档（票 32 §1）"
    assert basic.deck_profile == BASIC_53
    assert standard.deck_profile == STANDARD_108
    assert military.deck_profile == STANDARD_EX_160
    assert len(military.deck.cards) == 160, "军争是显式选档，总表 108 + 52"
    assert default.deck.cards == standard.deck.cards, "同档同数据"
    assert len(basic.deck.cards) == 53


def test_an_explicitly_unknown_profile_is_rejected_by_the_selection_entry():
    with pytest.raises(DataLoadError) as excinfo:
        load_content_pack(deck_profile="full")

    assert [error.rule_id for error in excinfo.value.errors] == ["decks.unknown_profile"]

    # 复用同一档位名做结构基底时，选择入口与数据层报同一条规则。
    with pytest.raises(DataLoadError) as data_level:
        validate_content_pack({**_standard_raw(), "deck_profile": "full"})
    assert data_level.value.errors[0].rule_id == "decks.unknown_profile"


def _validate(raw: dict[str, Any]) -> ContentPack:
    return validate_content_pack(raw, file="standard.json")


def _errors(raw: dict[str, Any]) -> tuple[LoadError, ...]:
    with pytest.raises(DataLoadError) as excinfo:
        _validate(raw)
    return excinfo.value.errors


def _rename_type(raw: dict[str, Any], profile: str, old: str, new: str) -> None:
    """把一个牌名改成新 id（连带**全部档位**的牌堆引用与 ``expect.by_type``）。

    牌名表是全包的：只改默认档会留下别的档位引用旧 id，坏数据基底就不再是"单点根因"。
    ``profile`` 保留在签名里标明调用方关心的档位，但改名对全包生效。
    """
    del profile  # 全档位一起改；保留参数是为了调用点读起来仍标明"针对哪一档"
    for card_type in raw["card_types"]:
        if card_type["id"] == old:
            card_type["id"] = new
    for deck in raw["decks"].values():
        for card in deck["cards"]:
            if card["type"] == old:
                card["type"] = new
        by_type = deck["expect"]["by_type"]
        if old in by_type:
            by_type[new] = by_type.pop(old)


def test_unknown_schema_version_is_rejected_as_version_mismatch():
    raw = _standard_raw()
    raw["schema_version"] = 99

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["version.unknown"]
    assert errors[0].loc == "/schema_version"
    assert "99" in errors[0].got
    assert "版本" in str(DataLoadError(errors))


def test_missing_schema_version_is_rejected():
    raw = _standard_raw()
    del raw["schema_version"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["version.missing"]
    assert errors[0].loc == "/schema_version"


def test_non_integer_schema_version_is_rejected():
    raw = _standard_raw()
    raw["schema_version"] = "1"

    assert [error.rule_id for error in _errors(raw)] == ["version.unknown"]


def test_version_is_checked_before_structure():
    """未知版本即拒：不得再报字段错误（否则报的是误导性的"字段不符"）。"""
    raw = _standard_raw()
    raw["schema_version"] = 1
    del raw["pack_id"]
    raw["card_types"][0]["bonus"] = "多余的字段"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["version.unknown"]


def test_missing_required_field_is_reported_with_its_location():
    raw = _standard_raw()
    del raw["pack_id"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.missing"]
    assert errors[0].loc == "/pack_id"
    assert errors[0].expected == "必填字段"


def test_extra_field_is_forbidden_not_silently_ignored():
    raw = _standard_raw()
    raw["decks"][BASIC_53]["cards"][0]["bonus"] = 1

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.extra_forbidden"]
    assert errors[0].loc == f"/decks/{BASIC_53}/cards/0/bonus"
    assert errors[0].expected == "无多余字段"


def test_unknown_category_is_rejected():
    raw = _standard_raw()
    raw["card_types"][0]["category"] = "spell"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.enum"]
    assert errors[0].loc == "/card_types/0/category"
    assert "spell" in errors[0].got


def test_unknown_suit_is_rejected():
    raw = _standard_raw()
    raw["decks"][BASIC_53]["cards"][0]["suit"] = "star"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.enum"]
    assert errors[0].loc == f"/decks/{BASIC_53}/cards/0/suit"


def test_structural_errors_are_aggregated_not_fail_fast():
    raw = _standard_raw()
    del raw["pack_id"]
    raw["card_types"][0]["bonus"] = "多余"
    raw["card_types"][1]["category"] = "spell"

    rule_ids = [error.rule_id for error in _errors(raw)]

    assert rule_ids == ["structure.missing", "structure.extra_forbidden", "structure.enum"]


def test_rank_out_of_range_is_rejected():
    raw = _standard_raw()
    raw["decks"][BASIC_53]["cards"][0]["rank"] = 14

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.rank_range"]
    assert errors[0].loc == f"/decks/{BASIC_53}/cards/0/rank"
    assert errors[0].got == "14"


def test_rank_below_range_is_rejected():
    raw = _standard_raw()
    raw["decks"][BASIC_53]["cards"][0]["rank"] = 0

    assert [error.rule_id for error in _errors(raw)] == ["deck.rank_range"]


def test_card_type_reference_must_exist():
    raw = _standard_raw()
    # 不要再用军争牌名当"未知牌"：M5-01a 起 11 个军争 id 已是本包合法牌名。
    raw["decks"][BASIC_53]["cards"][0]["type"] = "ghost_type"

    errors = _errors(raw)

    assert errors[0].rule_id == "deck.unknown_type"
    assert errors[0].loc == f"/decks/{BASIC_53}/cards/0/type"
    assert "ghost_type" in errors[0].got


def test_duplicate_card_type_id_is_rejected():
    raw = _basic_only_raw()
    raw["card_types"][1]["id"] = "slash"
    # 让引用与分布保持一致，隔离出"重复 id"这一处错误；被顶掉的 dodge 会成为行为孤儿。
    for card in raw["decks"][BASIC_53]["cards"]:
        if card["type"] == "dodge":
            card["type"] = "slash"
    raw["decks"][BASIC_53]["expect"]["by_type"] = {"slash": 45, "peach": 8}

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == [
        "card_types.duplicate_id",
        "card_type.behavior_orphan",
    ]
    assert errors[0].loc == "/card_types/1/id"


def test_duplicate_card_type_name_is_rejected():
    raw = _standard_raw()
    raw["card_types"][1]["name"] = "杀"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_types.duplicate_name"]
    assert errors[0].loc == "/card_types/1/name"


def test_empty_card_types_is_rejected():
    raw = _basic_only_raw()
    raw["card_types"] = []

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_types.empty"]
    assert errors[0].loc == "/card_types"


def test_unknown_deck_profile_is_rejected():
    raw = _standard_raw()
    raw["deck_profile"] = "full"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["decks.unknown_profile"]
    assert errors[0].loc == "/deck_profile"


# ---------------------------------------------------------------------------
# 新增的牌名级语义校验（票 09 §7 的 #5–#11）
# ---------------------------------------------------------------------------


def test_subcategory_must_come_from_the_closed_set():
    raw = _standard_108_raw()
    raw["card_types"][_index_of(raw, "duel")]["subcategory"] = "treasure"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.unknown_subcategory"]
    assert errors[0].loc == f"/card_types/{_index_of(raw, 'duel')}/subcategory"


def test_non_basic_cards_must_declare_a_subcategory():
    raw = _standard_108_raw()
    index = _index_of(raw, "duel")
    raw["card_types"][index]["subcategory"] = None

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.unknown_subcategory"]
    assert errors[0].loc == f"/card_types/{index}/subcategory"


def test_basic_cards_may_not_declare_a_subcategory():
    raw = _standard_raw()
    raw["card_types"][0]["subcategory"] = "instant"  # 杀

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.unknown_subcategory"]
    assert errors[0].loc == "/card_types/0/subcategory"


def test_delayed_must_agree_with_category_and_subcategory():
    raw = _standard_raw()
    raw["card_types"][0]["delayed"] = True  # 杀不是锦囊
    errors = _errors(raw)
    assert [error.rule_id for error in errors] == ["card_type.delayed_mismatch"]
    assert errors[0].loc == "/card_types/0/delayed"

    raw = _standard_108_raw()
    index = _index_of(raw, "indulgence")
    raw["card_types"][index]["delayed"] = False  # 延时锦囊不得丢掉标记
    errors = _errors(raw)
    assert [error.rule_id for error in errors] == ["card_type.delayed_mismatch"]
    assert errors[0].loc == f"/card_types/{index}/delayed"


def test_weapons_must_declare_an_attack_range():
    raw = _standard_108_raw()
    index = _index_of(raw, "crossbow")
    raw["card_types"][index]["attack_range"] = None

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.weapon_range_required"]
    assert errors[0].loc == f"/card_types/{index}/attack_range"


def test_attack_range_belongs_to_weapons_only():
    raw = _standard_raw()
    raw["card_types"][0]["attack_range"] = 2  # 杀

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.range_only_for_weapon"]
    assert errors[0].loc == "/card_types/0/attack_range"


def test_every_equipment_card_name_needs_a_skill(monkeypatch):
    """装备牌名 ⇒ 技能表里存在 ``equip.<牌名 id>``；去掉一条即加载期报错（票 09 §7 #8）。"""
    raw = _standard_108_raw()
    monkeypatch.setattr(
        "sgs.skills.EQUIPMENT_SKILL_IDS", EQUIPMENT_SKILL_IDS - {"equip.crossbow"}
    )

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.equipment_skill_missing"]
    assert errors[0].got == "'crossbow'"


def test_every_declared_card_name_needs_a_behavior():
    raw = _standard_108_raw()
    _rename_type(raw, STANDARD_108, "duel", "ghost_trick")
    raw["pack_id"] = "test-partial"  # 部分包：只查"缺失"方向，孤儿方向只对标准包生效

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.behavior_missing"]
    assert errors[0].got == "'ghost_trick'"


def test_the_standard_pack_may_not_carry_orphan_behaviors(monkeypatch):
    raw = _standard_raw()
    monkeypatch.setitem(CARD_BEHAVIORS, "ghost_trick", CARD_BEHAVIORS["slash"])

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.behavior_orphan"]
    assert errors[0].loc == "/card_types"
    assert errors[0].got == "'ghost_trick'"


def test_a_call_op_must_reference_the_ops_registry(monkeypatch):
    """行为表里的 ``CallOp`` 操作名必须能在 ``OPS`` 注册表里查到（票 09 §7 #10）。"""
    raw = _standard_raw()
    monkeypatch.setitem(
        CARD_BEHAVIORS,
        "slash",
        CardBehavior(
            card_type_id="slash",
            use_timings=(UseTiming(UseContext.PLAY_PHASE),),
            target_rule=TargetRule(rated_targets=1),
            use_limit=UseLimit(rated=1),
            effects=(CallOp("no_such_op"),),
        ),
    )

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_type.unknown_op"]
    assert errors[0].loc == "/card_types/0/id"
    assert errors[0].got == "'no_such_op'"


def test_the_military_behaviors_declare_the_static_skeleton():
    """M5-01a 只落静态面：火 / 雷杀同【杀】、酒双语境、锦囊 / 装备的时机与最终去向。

    属性 / 连环 / 重铸 / 装备效果分属 M5-02–06，本测试只钉"装得进、跑得动"的声明面；
    5 件装备的 ``equip.<id>`` 技能占位同批核。
    """
    play = UseTiming(UseContext.PLAY_PHASE)

    for type_id in ("fire_slash", "thunder_slash"):
        behavior = CARD_BEHAVIORS[type_id]
        assert behavior.use_timings == (play,)
        assert behavior.target_rule.rated_targets == 1
        assert behavior.target_rule.needs_distance is True
        assert behavior.use_limit.rated == 1
        assert behavior.response_window is ResponseWindow.DODGE
        assert behavior.final_zone is FinalZone.DISCARD

    wine = CARD_BEHAVIORS["wine"]
    assert wine.use_timings == (play, UseTiming(UseContext.NEAR_DEATH))
    assert wine.target_rule.self_only is True
    assert wine.final_zone is FinalZone.DISCARD

    iron_chain = CARD_BEHAVIORS["iron_chain"]
    assert (iron_chain.target_rule.min_targets, iron_chain.target_rule.max_targets) == (1, 2), (
        "1–2 目标 = M5-03 的 min/max 区间规则（0 目标非法、可指定自己）"
    )
    assert iron_chain.target_rule.includes_self_in_pool() is True, "可指定自己（D7）"
    assert iron_chain.reforgeable is True, "重铸是牌面声明（M5-03 / D6）"
    assert iron_chain.final_zone is FinalZone.DISCARD
    assert iron_chain.response_window is ResponseWindow.NULLIFY

    fire_attack = CARD_BEHAVIORS["fire_attack"]
    assert fire_attack.target_rule.rated_targets == 1
    assert fire_attack.target_rule.includes_self_in_pool() is True, "可指定自己（M5-06 / D10）"
    assert fire_attack.target_filters == ("has_hand_cards",), "目标须有手牌（M5-06）"
    assert fire_attack.response_window is ResponseWindow.NULLIFY

    shortage = CARD_BEHAVIORS["supply_shortage"]
    assert shortage.is_delayed is True
    assert shortage.target_rule.max_distance == 1, "兵粮寸断：距离 1"
    assert shortage.target_rule.includes_self_in_pool() is False, "只能对其他角色（M5-06）"
    assert shortage.response_window is ResponseWindow.NULLIFY

    for type_id in (
        "silver_lion",
        "vine_armor",
        "ancient_scimitar",
        "vermilion_feather_fan",
        "hualiu",
    ):
        behavior = CARD_BEHAVIORS[type_id]
        assert behavior.use_timings == (play,)
        assert behavior.target_rule.self_only is True
        assert behavior.final_zone is FinalZone.EQUIP
        assert f"equip.{type_id}" in EQUIPMENT_SKILL_IDS, "每张装备都有技能占位"


def _index_of(raw: dict[str, Any], card_type_id: str) -> int:
    return [card_type["id"] for card_type in raw["card_types"]].index(card_type_id)


# ---------------------------------------------------------------------------
# 牌堆层面的分档校验（票 09 §7 的 #1–#4）
# ---------------------------------------------------------------------------


def test_suit_rank_slot_may_not_hold_three_cards():
    raw = _standard_raw()
    cards = raw["decks"][BASIC_53]["cards"]
    cards[2] = {"type": "slash", "suit": "heart", "rank": 10}  # 第 3 张 ♥10

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.slot_limit"]
    assert errors[0].loc == f"/decks/{BASIC_53}/cards/2"


def test_the_108_profile_allows_the_four_ex_slots_to_hold_three_cards():
    pack = load_content_pack(deck_profile=STANDARD_108)

    assert pack.deck.slot_limit == 3, "分档声明使含 EX 的三张槽位合法"


def test_expect_total_must_match_actual():
    raw = _standard_raw()
    raw["decks"][BASIC_53]["expect"]["total"] = 52

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_total"]
    assert errors[0].loc == f"/decks/{BASIC_53}/expect/total"
    assert errors[0].got == "53"


def test_expect_by_type_must_match_actual():
    raw = _standard_raw()
    raw["decks"][BASIC_53]["expect"]["by_type"]["peach"] = 9

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_by_type"]
    assert errors[0].loc == f"/decks/{BASIC_53}/expect/by_type/peach"
    assert errors[0].got == "8"


def test_expect_by_type_must_declare_every_actual_type():
    raw = _standard_raw()
    del raw["decks"][BASIC_53]["expect"]["by_type"]["dodge"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_by_type"]
    assert errors[0].loc == f"/decks/{BASIC_53}/expect/by_type/dodge"
    assert errors[0].got == "15"


def test_expect_by_suit_must_match_actual():
    raw = _standard_108_raw()
    raw["decks"][STANDARD_108]["expect"]["by_suit"]["heart"] = 26

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_by_suit"]
    assert errors[0].loc == f"/decks/{STANDARD_108}/expect/by_suit/heart"
    assert errors[0].got == "27"


def test_expect_by_suit_must_declare_every_actual_suit():
    raw = _standard_108_raw()
    del raw["decks"][STANDARD_108]["expect"]["by_suit"]["club"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_by_suit"]
    assert errors[0].loc == f"/decks/{STANDARD_108}/expect/by_suit/club"
    assert errors[0].got == "27"


def test_expect_by_category_must_match_actual():
    raw = _standard_108_raw()
    raw["decks"][STANDARD_108]["expect"]["by_category"]["trick"] = 35

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_by_category"]
    assert errors[0].loc == f"/decks/{STANDARD_108}/expect/by_category/trick"
    assert errors[0].got == "36"


def test_expect_by_category_must_declare_every_actual_category():
    raw = _standard_108_raw()
    del raw["decks"][STANDARD_108]["expect"]["by_category"]["equipment"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_by_category"]
    assert errors[0].loc == f"/decks/{STANDARD_108}/expect/by_category/equipment"
    assert errors[0].got == "19"


def test_optional_expect_dimensions_are_not_checked_when_absent():
    """``basic_53`` 不写 ``by_suit`` / ``by_category``，故这两维对它不生效（零改动通过）。"""
    pack = load_content_pack(deck_profile=BASIC_53)
    expect = pack.decks[BASIC_53].expect
    assert expect is not None
    assert expect.by_suit is None and expect.by_category is None

    assert _validate(_basic_only_raw()).deck_profile == BASIC_53


def test_semantic_errors_are_aggregated_not_fail_fast():
    raw = _standard_raw()
    raw["deck_profile"] = "full"
    raw["decks"][BASIC_53]["cards"][0]["rank"] = 14
    raw["decks"][BASIC_53]["expect"]["total"] = 52

    rule_ids = [error.rule_id for error in _errors(raw)]

    assert rule_ids == ["decks.unknown_profile", "deck.rank_range", "deck.expect_total"]


def test_structure_failure_short_circuits_semantics():
    """结构没过时字段本身不可信，故不再跑语义检查。"""
    raw = _standard_raw()
    raw["decks"][BASIC_53]["cards"][0]["rank"] = "十"
    raw["decks"][BASIC_53]["expect"]["total"] = 52

    assert [error.rule_id for error in _errors(raw)] == ["structure.int_parsing"]


def test_missing_content_pack_file_is_reported():
    with pytest.raises(DataLoadError) as excinfo:
        load_content_pack("no_such_pack")

    errors = excinfo.value.errors

    assert [error.rule_id for error in errors] == ["pack.not_found"]
    assert "no_such_pack" in errors[0].got


def test_invalid_json_is_reported_as_such():
    with pytest.raises(DataLoadError) as excinfo:
        validate_content_pack("{ not json", file="broken.json")

    assert [error.rule_id for error in excinfo.value.errors] == ["structure.invalid_json"]


def test_behavior_fields_are_rejected_in_card_type_data():
    """行为字段（使用时机 / 目标规则 / 次数限制 / 效果列表）不在数据层。"""
    raw = _standard_raw()
    raw["card_types"][0]["usage_timing"] = "play_phase"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.extra_forbidden"]
    assert errors[0].loc == "/card_types/0/usage_timing"


def test_loaded_card_type_exposes_only_identity_attributes():
    card_type = load_content_pack().card_types[0]

    assert (card_type.id, card_type.name) == ("slash", "杀")
    for behavior_field in ("usage_timing", "target_rule", "use_limit", "effects"):
        assert not hasattr(card_type, behavior_field)
