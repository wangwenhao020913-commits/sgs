"""规则配置对象（票 09）：最小按需、fail-closed、header 的可复现前置事实。

落点 = ``sgs.data`` 的进料边界（与内容包坏数据同一条缝）。
票 24 就地扩容：模式由座位数派生、身份局的 ``first_seat`` 不得预设。
M7 票 07 再就地扩容：``mode`` 由派生属性变成**字段**（缺省仍按座位数派生）、``player_count``
值域放进 3、新增 ``pve`` 那一节与它的构造期校验矩阵——dump 因此从三键变五键
（标准局 ``pve: null``）。

**判据键在"本 mode 的构成表里有没有这个人数的布局"，不键在硬编码的人数区间**（用户
2026-11-19 裁定：人数不是模式判据，规则才是——4 人可以是身份局、2v2 或 PvE 的 1 友 vs 3 敌）。
"""

from __future__ import annotations

import pytest

from sgs.data import DataLoadError, Identity, Mode, RuleConfig, validate_rule_config

ALLY = Identity.ALLY
ENEMY = Identity.ENEMY


def _pve(**overrides):
    """一份最小可用的 ``pve`` 载荷：1 友（主角，座位 0）+ 2 敌，全歼敌人获胜。"""
    payload = {
        "identities": ["ally", "enemy", "enemy"],
        "principal": 0,
        "victory": {"kind": "rout"},
    }
    payload.update(overrides)
    return payload


def _pve_config(**overrides):
    """一份完整的 PvE 规则配置（3 座：1 友 + 2 敌，主角 = 座位 0）。"""
    return {"mode": "pve", "player_count": 3, "pve": _pve(**overrides)}


def test_default_config_is_exactly_the_five_keys():
    """票 07：`mode` 与 `pve` 进 dump —— header 从此多带两个键（标准局 `pve: null`）。"""
    assert RuleConfig().model_dump(mode="json") == {
        "player_count": 2,
        "starting_hand_size": 4,
        "first_seat": 0,
        "mode": "no_identity",
        "pve": None,
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"starting_hand_size": 0},
        {"first_seat": 2},
        {"player_count": 2, "starting_hand_size": 4, "first_seat": 0, "unknown": False},
    ],
    ids=["starting_hand_size", "first_seat", "extra_field"],
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


def test_a_pve_config_is_accepted_with_three_seats():
    """**PVE × 3 合法**（1 友 + 2 敌）：3 不是被人数区间挡掉的，而是看构成表里有没有它。"""
    config = validate_rule_config(_pve_config())

    assert (config.mode, config.player_count) == (Mode.PVE, 3)
    assert config.pve.identities == (ALLY, ENEMY, ENEMY)
    assert config.pve.seats_of(ALLY) == (0,), "阵营的座位表只读一处（胜负的 winners 也读它）"
    assert config.pve.seats_of(ENEMY) == (1, 2)


@pytest.mark.parametrize("count", [2, 4, 5, 6, 7, 8])
def test_the_two_standard_modes_still_accept_their_own_seat_counts(count):
    """不声明模式时能跑通的仍然只有 2 与 4~8：值域虽放开到 ``Literal[2..8]``，
    但"本 mode 的构成表里有没有这一档"才是判据（3 因此要显式声明 PvE）。"""
    assert validate_rule_config({"player_count": count}).player_count == count


@pytest.mark.parametrize("count", [0, 1, 9])
def test_counts_outside_the_domain_are_rejected(count):
    with pytest.raises(DataLoadError):
        validate_rule_config({"player_count": count})


# ---------------------------------------------------------------------------
# 模式字段（票 24 / 决策回票 13 §1 → M7 票 07 的显式化）
# ---------------------------------------------------------------------------


def test_mode_is_derived_from_the_seat_count_when_it_is_not_declared():
    """票 07：缺省不写 ``mode`` 时，标准局按座位数派生（2 = 无身份局、4~8 = 身份局）。"""
    assert RuleConfig().mode is Mode.NO_IDENTITY

    for count in (4, 5, 6, 7, 8):
        assert RuleConfig(player_count=count).mode is Mode.IDENTITY


def test_mode_is_a_field_that_lands_in_the_dump():
    """``mode`` 不再是只读派生属性：身份局 header 里它明明白白写着（票 07 / header v3）。"""
    dumped = RuleConfig(player_count=6).model_dump(mode="json")

    assert dumped == {
        "player_count": 6,
        "starting_hand_size": 4,
        "first_seat": 0,
        "mode": "identity",
        "pve": None,
    }


def test_an_identity_mode_rejects_a_three_seat_table_because_there_is_no_such_layout():
    """票 07：拒的理由是"配比表里没有 3 人档"，**不是**"3 这个数字不好"。"""
    with pytest.raises(DataLoadError) as caught:
        validate_rule_config({"player_count": 3})

    assert "布局" in str(caught.value), "拒绝必须点名「构成表里没有这一档布局」"
    assert "身份局" in str(caught.value), "缺省派生到的模式（身份局）要报出来"


def test_a_four_seat_no_identity_table_is_rejected_because_that_layout_does_not_exist():
    with pytest.raises(DataLoadError) as caught:
        validate_rule_config({"player_count": 4, "mode": "no_identity"})

    assert "布局" in str(caught.value)


def test_a_three_seat_identity_table_is_rejected_even_when_mode_is_spelled_out():
    with pytest.raises(DataLoadError):
        validate_rule_config({"player_count": 3, "mode": "identity"})


def test_identity_mode_rejects_a_preset_first_seat():
    """身份局的先手是主公座位，配置里预设必然是误导（票 13 §1）。"""
    with pytest.raises(DataLoadError):
        validate_rule_config({"player_count": 6, "first_seat": 1})


def test_non_default_first_seat_is_still_accepted_in_no_identity_mode():
    """无身份局的 ``first_seat`` 语义不变（可配置、不随机）。"""
    assert validate_rule_config({"player_count": 2, "first_seat": 1}).first_seat == 1


def test_pve_first_seat_is_configurable_but_must_stay_inside_the_table():
    payload = {**_pve_config(), "first_seat": 2}

    assert validate_rule_config(payload).first_seat == 2

    with pytest.raises(DataLoadError):
        validate_rule_config({**payload, "first_seat": 3})


# ---------------------------------------------------------------------------
# `pve` 那一节：跨字段矩阵（票 07）
# ---------------------------------------------------------------------------


def _config(**overrides) -> RuleConfig:
    return validate_rule_config(_pve_config(**overrides))


def test_pve_without_a_pve_section_is_rejected():
    """``mode == pve`` 而 ``pve`` 为空：录像重建不出同一场，构造期就拒（fail-closed）。"""
    with pytest.raises(DataLoadError) as caught:
        validate_rule_config({"player_count": 3, "mode": "pve"})

    assert "pve" in str(caught.value)


def test_a_pve_section_under_another_mode_is_rejected():
    with pytest.raises(DataLoadError) as caught:
        validate_rule_config({"player_count": 2, "pve": _pve()})

    assert "pve" in str(caught.value)


@pytest.mark.parametrize("count", [2, 4, 5])
def test_a_pve_seat_table_must_match_the_player_count(count):
    layout = ["ally", *(("enemy",) * (count - 1))]
    payload = {
        "mode": "pve",
        "player_count": count + 1,
        "pve": _pve(identities=layout, principal=0),
    }

    with pytest.raises(DataLoadError) as caught:
        validate_rule_config(payload)

    assert "identities" in str(caught.value)


@pytest.mark.parametrize("count", [2, 4, 5])
def test_a_pve_table_with_one_ally_and_the_rest_enemies_is_legal(count):
    """2~5 座都合法（上界 2~5 是**房间编排**的规则，不是 ``RuleConfig`` 的区间）。"""
    layout = ["ally", *(("enemy",) * (count - 1))]

    config = validate_rule_config(
        {"mode": "pve", "player_count": count, "pve": _pve(identities=layout)}
    )

    assert len(config.pve.identities) == count


def test_the_pve_section_survives_a_dump_round_trip():
    config = _config(
        identities=["ally", "ally", "enemy"],
        protected_ally=1,
        victory={"kind": "survive_rounds", "rounds": 2},
    )

    redumped = RuleConfig.model_validate(config.model_dump(mode="json"))

    assert redumped == config, "dump 往返必须逐字段相等（否则 header 自证不成立）"


def test_a_pve_triple_that_is_all_allies_or_all_enemies_is_rejected():
    """至少 1 友 1 敌：全友 / 全敌的"布局"没有对手，胜负条件无从谈。"""
    for identities in (["ally", "ally", "ally"], ["enemy", "enemy", "enemy"]):
        with pytest.raises(DataLoadError):
            validate_rule_config(
                {"mode": "pve", "player_count": 3, "pve": _pve(identities=identities)}
            )


@pytest.mark.parametrize("lord_seat", [0, 1, 2])
def test_an_identity_from_the_lord_table_is_not_a_pve_side(lord_seat):
    """域校验要**独立于**"至少 1 友 1 敌"成立：混进一个主公的座位表两侧其实都在。

    否则它会落在 ``solve_pve_outcome`` 的两侧之外——永不判死、也永不计胜（fail-open）。
    """
    identities = ["ally", "enemy", "enemy"]
    identities[lord_seat] = "lord"

    with pytest.raises(DataLoadError) as caught:
        validate_rule_config(
            {"mode": "pve", "player_count": 3, "pve": _pve(identities=identities)}
        )

    assert f"identities[{lord_seat}]" in str(caught.value)


def test_the_principal_must_be_a_seat_of_the_table_and_an_ally():
    for principal in (3, -1):
        with pytest.raises(DataLoadError):
            _config(principal=principal)

    with pytest.raises(DataLoadError) as caught:
        _config(principal=1)  # 座位 1 是敌人
    assert "principal" in str(caught.value)


def test_the_protected_ally_must_be_an_ally_seat_other_than_the_principal():
    assert _config().pve.protected_ally is None, "缺省 = 没有受保护队友"

    with pytest.raises(DataLoadError):
        _config(protected_ally=0)  # 主角自己
    with pytest.raises(DataLoadError):
        _config(protected_ally=2)  # 敌人


def test_protected_ally_on_an_ally_seat_is_accepted():
    config = _config(identities=["ally", "ally", "enemy"], protected_ally=1)

    assert config.pve.protected_ally == 1


def test_slay_boss_needs_a_boss_seat_that_is_an_enemy():
    with pytest.raises(DataLoadError) as caught:
        _config(victory={"kind": "slay_boss"})
    assert "boss" in str(caught.value)

    with pytest.raises(DataLoadError):
        _config(victory={"kind": "slay_boss"}, boss=0)  # 主角不是敌人

    config = _config(victory={"kind": "slay_boss"}, boss=2)
    assert config.pve.boss == 2


def test_a_boss_seat_under_another_victory_condition_is_rejected():
    """``boss`` 只属于 ``SLAY_BOSS``：别的条件带着它，说明配置自相矛盾。"""
    with pytest.raises(DataLoadError) as caught:
        _config(boss=2)

    assert "boss" in str(caught.value)


def test_the_round_limit_belongs_to_the_round_based_victories():
    for kind in ("survive_rounds", "rout_within_rounds"):
        with pytest.raises(DataLoadError) as caught:
            _config(victory={"kind": kind})
        assert "rounds" in str(caught.value)

        assert _config(victory={"kind": kind, "rounds": 1}).pve.victory.rounds == 1

    with pytest.raises(DataLoadError) as caught:
        _config(victory={"kind": "rout", "rounds": 3})
    assert "rounds" in str(caught.value)


@pytest.mark.parametrize("rounds", [0, -1])
def test_a_non_positive_round_limit_is_rejected(rounds):
    with pytest.raises(DataLoadError):
        _config(victory={"kind": "survive_rounds", "rounds": rounds})


def test_extra_keys_inside_the_pve_section_are_rejected():
    with pytest.raises(DataLoadError):
        _config(unknown=True)
