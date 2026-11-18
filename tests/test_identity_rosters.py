"""身份配比与标签的规则常数（票 24 / 决策回票 13 §2）。

落点 = 纯函数直测：配比表是模块级不可变常量，没有行为边界可走。
``RuleConfig`` 侧的模式派生与 ``first_seat`` 门控按决策票 13 §1 就地扩容在
``tests/test_rule_config.py``（不另立第二套）。
"""

from __future__ import annotations

import pytest

from sgs.data import IDENTITY_LABELS, Identity, roster_for


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (4, ("lord", "loyalist", "rebel", "renegade")),
        (5, ("lord", "loyalist", "rebel", "rebel", "renegade")),
        (6, ("lord", "loyalist", "rebel", "rebel", "rebel", "renegade")),
        (7, ("lord", "loyalist", "loyalist", "rebel", "rebel", "rebel", "renegade")),
        (
            8,
            (
                "lord",
                "loyalist",
                "loyalist",
                "rebel",
                "rebel",
                "rebel",
                "rebel",
                "renegade",
            ),
        ),
    ],
)
def test_roster_matches_the_official_ratio_table(count, expected):
    """票 04 主表：主-忠-反-内 = 1-1-1-1 / 1-1-2-1 / 1-1-3-1 / 1-2-3-1 / 1-2-4-1。"""
    assert tuple(identity.value for identity in roster_for(count)) == expected


@pytest.mark.parametrize("count", [0, 1, 2, 3, 9])
def test_roster_is_fail_closed_outside_four_to_eight(count):
    with pytest.raises(ValueError):
        roster_for(count)


def test_identity_labels_cover_exactly_the_six_identities():
    """票 07 起身份是**六值**：身份局四值 + PvE 的友 / 敌（后者不进配比表）。"""
    assert set(IDENTITY_LABELS) == set(Identity)
    assert IDENTITY_LABELS[Identity.LORD] == "主公"
    assert IDENTITY_LABELS[Identity.LOYALIST] == "忠臣"
    assert IDENTITY_LABELS[Identity.REBEL] == "反贼"
    assert IDENTITY_LABELS[Identity.RENEGADE] == "内奸"
    assert IDENTITY_LABELS[Identity.ALLY] == "友"
    assert IDENTITY_LABELS[Identity.ENEMY] == "敌"


def test_the_two_pve_sides_are_not_in_the_identity_roster_table():
    """PvE 阵营**不**进配比表：它由 ``PveConfig.identities`` 逐座给出（票 01 §3）。"""
    for count in (4, 5, 6, 7, 8):
        assert set(roster_for(count)).isdisjoint({Identity.ALLY, Identity.ENEMY})


def test_a_three_seat_table_is_closed_at_construction_for_both_composition_tables():
    """票 07：3 人落在**两张构成表的缝里**——身份局配比表没有这一档、无身份局只有 2 人档。

    所以它在 ``RuleConfig`` 的构造期被拒（fail-closed，不靠下游 ``KeyError`` 兜），
    ``roster_for(3)`` 也照旧抛 ``ValueError``。要 3 人局，只能显式声明一个能容纳它的
    模式（PvE 的布局由 ``PveConfig.identities`` 自证）——见 ``tests/test_rule_config.py``
    的 ``test_a_pve_config_is_accepted_with_three_seats``。
    """
    from sgs.data import DataLoadError, validate_rule_config

    with pytest.raises(ValueError):
        roster_for(3)

    with pytest.raises(DataLoadError):
        validate_rule_config({"player_count": 3})
