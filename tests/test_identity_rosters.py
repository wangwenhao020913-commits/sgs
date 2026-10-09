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


def test_identity_labels_cover_exactly_the_four_identities():
    assert set(IDENTITY_LABELS) == set(Identity)
    assert IDENTITY_LABELS[Identity.LORD] == "主公"
    assert IDENTITY_LABELS[Identity.LOYALIST] == "忠臣"
    assert IDENTITY_LABELS[Identity.REBEL] == "反贼"
    assert IDENTITY_LABELS[Identity.RENEGADE] == "内奸"
