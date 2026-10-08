"""内容包加载：正常数据加载、版本→结构→语义三段校验与聚合报错。

测试只经 ``sgs.data`` 公开接缝（``load_content_pack`` / ``validate_content_pack`` /
``ContentPack`` / ``DataLoadError`` / ``LoadError``），不 import 子包深层路径。
"""

from __future__ import annotations

from typing import Any

import pytest

from sgs.data import ContentPack, DataLoadError, LoadError, load_content_pack, validate_content_pack


def _standard_raw() -> dict[str, Any]:
    """已通过校验的标准包，转回 JSON 形状，作为改坏数据的基底。

    只经公开接缝（``load_content_pack`` + ``model_dump``），不自己拼资源路径。
    """
    return load_content_pack().model_dump(mode="json")


def test_loads_standard_content_pack():
    pack = load_content_pack()

    assert isinstance(pack, ContentPack)
    assert pack.schema_version == 1
    assert pack.pack_id == "standard"
    assert pack.deck_profile == "m1_basic"
    assert [card_type.id for card_type in pack.card_types] == ["slash", "dodge", "peach"]
    assert len(pack.deck.cards) == 53


def _validate(raw: dict[str, Any]) -> ContentPack:
    return validate_content_pack(raw, file="standard.json")


def _errors(raw: dict[str, Any]) -> tuple[LoadError, ...]:
    with pytest.raises(DataLoadError) as excinfo:
        _validate(raw)
    return excinfo.value.errors


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
    raw["schema_version"] = 2
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
    raw["decks"]["m1_basic"]["cards"][0]["bonus"] = 1

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.extra_forbidden"]
    assert errors[0].loc == "/decks/m1_basic/cards/0/bonus"
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
    raw["decks"]["m1_basic"]["cards"][0]["suit"] = "star"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["structure.enum"]
    assert errors[0].loc == "/decks/m1_basic/cards/0/suit"


def test_structural_errors_are_aggregated_not_fail_fast():
    raw = _standard_raw()
    del raw["pack_id"]
    raw["card_types"][0]["bonus"] = "多余"
    raw["card_types"][1]["category"] = "spell"

    rule_ids = [error.rule_id for error in _errors(raw)]

    assert rule_ids == ["structure.missing", "structure.extra_forbidden", "structure.enum"]


def test_rank_out_of_range_is_rejected():
    raw = _standard_raw()
    raw["decks"]["m1_basic"]["cards"][0]["rank"] = 14

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.rank_range"]
    assert errors[0].loc == "/decks/m1_basic/cards/0/rank"
    assert errors[0].got == "14"


def test_rank_below_range_is_rejected():
    raw = _standard_raw()
    raw["decks"]["m1_basic"]["cards"][0]["rank"] = 0

    assert [error.rule_id for error in _errors(raw)] == ["deck.rank_range"]


def test_card_type_reference_must_exist():
    raw = _standard_raw()
    raw["decks"]["m1_basic"]["cards"][0]["type"] = "wine"

    errors = _errors(raw)

    assert errors[0].rule_id == "deck.unknown_type"
    assert errors[0].loc == "/decks/m1_basic/cards/0/type"
    assert "wine" in errors[0].got


def test_duplicate_card_type_id_is_rejected():
    raw = _standard_raw()
    raw["card_types"][1]["id"] = "slash"
    # 让引用与分布保持一致，隔离出"重复 id"这一处错误。
    for card in raw["decks"]["m1_basic"]["cards"]:
        if card["type"] == "dodge":
            card["type"] = "slash"
    raw["decks"]["m1_basic"]["expect"]["by_type"] = {"slash": 45, "peach": 8}

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_types.duplicate_id"]
    assert errors[0].loc == "/card_types/1/id"


def test_duplicate_card_type_name_is_rejected():
    raw = _standard_raw()
    raw["card_types"][1]["name"] = "杀"

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["card_types.duplicate_name"]
    assert errors[0].loc == "/card_types/1/name"


def test_empty_card_types_is_rejected():
    raw = _standard_raw()
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


def test_suit_rank_slot_may_not_hold_three_cards():
    raw = _standard_raw()
    cards = raw["decks"]["m1_basic"]["cards"]
    cards[2] = {"type": "slash", "suit": "heart", "rank": 10}  # 第 3 张 ♥10

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.slot_limit"]
    assert errors[0].loc == "/decks/m1_basic/cards/2"


def test_expect_total_must_match_actual():
    raw = _standard_raw()
    raw["decks"]["m1_basic"]["expect"]["total"] = 52

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_total"]
    assert errors[0].loc == "/decks/m1_basic/expect/total"
    assert errors[0].got == "53"


def test_expect_by_type_must_match_actual():
    raw = _standard_raw()
    raw["decks"]["m1_basic"]["expect"]["by_type"]["peach"] = 9

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_by_type"]
    assert errors[0].loc == "/decks/m1_basic/expect/by_type/peach"
    assert errors[0].got == "8"


def test_expect_by_type_must_declare_every_actual_type():
    raw = _standard_raw()
    del raw["decks"]["m1_basic"]["expect"]["by_type"]["dodge"]

    errors = _errors(raw)

    assert [error.rule_id for error in errors] == ["deck.expect_by_type"]
    assert errors[0].loc == "/decks/m1_basic/expect/by_type/dodge"
    assert errors[0].got == "15"


def test_semantic_errors_are_aggregated_not_fail_fast():
    raw = _standard_raw()
    raw["deck_profile"] = "full"
    raw["decks"]["m1_basic"]["cards"][0]["rank"] = 14
    raw["decks"]["m1_basic"]["expect"]["total"] = 52

    rule_ids = [error.rule_id for error in _errors(raw)]

    assert rule_ids == ["decks.unknown_profile", "deck.rank_range", "deck.expect_total"]


def test_structure_failure_short_circuits_semantics():
    """结构没过时字段本身不可信，故不再跑语义检查。"""
    raw = _standard_raw()
    raw["decks"]["m1_basic"]["cards"][0]["rank"] = "十"
    raw["decks"]["m1_basic"]["expect"]["total"] = 52

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
