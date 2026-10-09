"""规则配置对象（票 09）：最小按需、fail-closed、信封三键。

落点 = ``sgs.data`` 的进料边界（与内容包坏数据同一条缝）。
"""

from __future__ import annotations

import pytest

from sgs.data import DataLoadError, RuleConfig, validate_rule_config


def test_default_config_is_exactly_three_keys():
    assert RuleConfig().model_dump(mode="json") == {
        "player_count": 2,
        "starting_hand_size": 4,
        "first_seat": 0,
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"player_count": 3},
        {"starting_hand_size": 0},
        {"first_seat": 2},
        {"player_count": 2, "starting_hand_size": 4, "first_seat": 0, "unknown": False},
    ],
    ids=["player_count", "starting_hand_size", "first_seat", "extra_field"],
)
def test_out_of_range_or_extra_fields_are_rejected_as_a_data_load_error(raw):
    with pytest.raises(DataLoadError) as caught:
        validate_rule_config(raw)

    assert caught.value.errors, "错误必须聚合且带位置"
    assert all(error.rule_id.startswith("rule_config.") for error in caught.value.errors)


def test_valid_raw_config_is_accepted():
    config = validate_rule_config({"first_seat": 1})

    assert config.player_count == 2
    assert config.first_seat == 1


@pytest.mark.parametrize("count", [2, 4, 5, 6, 7, 8])
def test_the_seat_count_domain_is_two_or_four_to_eight(count):
    """票 23：值域从 ``Literal[2]`` 扩为 2 / 4~8；3 与其余取值仍加载期失败。"""
    assert validate_rule_config({"player_count": count}).player_count == count


@pytest.mark.parametrize("count", [0, 1, 3, 9])
def test_counts_outside_the_domain_are_rejected(count):
    with pytest.raises(DataLoadError):
        validate_rule_config({"player_count": count})
