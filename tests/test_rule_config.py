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
