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


# ---------------------------------------------------------------------------
# `pve.grants`：开局授予的整张校验矩阵（M7 票 09；票 02 §10.5 / 票 03 §3）
#
# 形状 = `{seat, kind: "skill" | "relic", id}`，**位置不变**（票 01 已钉：必须在 header 里，
# 否则录像重建不出同一局）。矩阵逐条 fail-closed：>4 技能 / >6 遗物 / 未知 id / 重复 /
# 遗物授给非主角座位。授予越界座位同批拒掉——它会在 `build_initial` 里变成越界写。
# ---------------------------------------------------------------------------

WUSHENG = "hero.guanyu.wusheng"
LONGDAN = "hero.zhaoyun.longdan"
MASHU = "hero.machao.mashu"
JIANXIONG = "hero.caocao.jianxiong"
JIJIU = "hero.huatuo.jijiu"
GUOSE = "hero.daqiao.guose"
QIXI = "hero.ganning.qixi"
"""七个真实存在的 catalog id（内容期之前没有 `relic.*`，故遗物位也拿真实技能 id 当值：
判据是"id 在 CATALOG 里"，与 ``kind`` 是哪一档无关——见票 09 的裁定）。"""


def _grant(seat=0, kind="skill", grant_id=WUSHENG):
    return {"seat": seat, "kind": kind, "id": grant_id}


def _grants(*grants):
    return _config(grants=grants)


def test_grants_of_both_kinds_are_accepted_and_land_in_the_dump():
    """4 条出战技能 + 6 件遗物是**上限而不是总量**：整份配置原样进 header（dump 往返相等）。"""
    config = _config(
        grants=(
            _grant(0, "skill", WUSHENG),
            _grant(0, "skill", LONGDAN),
            _grant(0, "skill", MASHU),
            _grant(0, "skill", JIANXIONG),
            _grant(0, "relic", JIJIU),
            _grant(1, "skill", WUSHENG),
        )
    )

    assert [grant.kind for grant in config.pve.grants] == [
        "skill",
        "skill",
        "skill",
        "skill",
        "relic",
        "skill",
    ]
    assert RuleConfig.model_validate(config.model_dump(mode="json")) == config, (
        "授予必须 dump 往返逐字段相等，否则 header 自证不成立"
    )


def test_more_than_four_skill_grants_on_one_seat_are_rejected():
    """每座**技能**授予 ≤ 4（4 条出战槽）：第 5 条拒。上限是逐座位的，不是全场的。"""
    with pytest.raises(DataLoadError) as caught:
        _grants(
            _grant(0, "skill", WUSHENG),
            _grant(0, "skill", LONGDAN),
            _grant(0, "skill", MASHU),
            _grant(0, "skill", JIANXIONG),
            _grant(0, "skill", JIJIU),
        )

    assert "4" in str(caught.value), "拒绝必须点名「每座技能 ≤ 4」这条上限"


def test_four_skill_grants_on_two_seats_are_each_under_the_cap():
    """上限逐座位算：两座各 4 条＝8 条也合法（全场条数不是判据）。"""
    config = _config(
        grants=tuple(
            _grant(seat, "skill", grant_id)
            for seat in (0, 1)
            for grant_id in (WUSHENG, LONGDAN, MASHU, JIANXIONG)
        )
    )

    assert len(config.pve.grants) == 8


def test_more_than_six_relic_grants_on_one_seat_are_rejected():
    """每座**遗物**授予 ≤ 6（独立遗物栏上限）：第 7 件拒。"""
    seven = (WUSHENG, LONGDAN, MASHU, JIANXIONG, JIJIU, GUOSE, QIXI)

    with pytest.raises(DataLoadError) as caught:
        _grants(*(_grant(0, "relic", grant_id) for grant_id in seven))

    assert "遗物" in str(caught.value)

    assert len(_config(grants=tuple(_grant(0, "relic", g) for g in seven[:6])).pve.grants) == 6


def test_a_grant_id_that_is_not_in_the_catalog_is_rejected():
    """``id`` 必须在 ``CATALOG`` 里（fail-closed）：两档来源都拒，不只在技能位上判。"""
    for kind in ("skill", "relic"):
        with pytest.raises(DataLoadError) as caught:
            _grants(_grant(0, kind, "hero.nobody.nothing"))
        assert "hero.nobody.nothing" in str(caught.value), "报错要点名那个不存在的 id"


def test_the_same_grant_on_the_same_seat_twice_is_rejected():
    """同一座位的同一 id 只许出现一次（重复授予不给第二次机会，也让 header 无歧义）。"""
    with pytest.raises(DataLoadError) as caught:
        _grants(_grant(0, "skill", WUSHENG), _grant(0, "skill", WUSHENG))

    assert WUSHENG in str(caught.value)

    with pytest.raises(DataLoadError):
        _grants(_grant(0, "skill", WUSHENG), _grant(0, "relic", WUSHENG))


def test_a_relic_granted_to_a_non_principal_seat_is_rejected():
    """遗物**只授给主角座位**：遗物栏属于玩家本人，敌人不拿遗物（票 02 §10.5）。"""
    with pytest.raises(DataLoadError) as caught:
        _grants(_grant(1, "relic", WUSHENG))
    assert "遗物" in str(caught.value)

    # 座位 1 拿**技能**没问题（AI 队友同样吃出战技能），故上面拒的是 kind 而不是座位本身。
    assert _config(grants=(_grant(1, "skill", WUSHENG),)).pve.grants[0].seat == 1


def test_a_grant_on_a_seat_outside_the_table_is_rejected():
    with pytest.raises(DataLoadError) as caught:
        _grants(_grant(3, "skill", WUSHENG))

    assert "seat" in str(caught.value)


# M7 票 27：NPC 技能**只授敌方座位**（票 02 §7）。判据是技能定义上的**载体**
# （`CarrierKind.NPC`），不是 id 前缀——故这里点名四条真实 id，而不是造一个假字符串。
NPC_FURY = "npc.fury"
NPC_IDS = ("npc.fury", "npc.guard", "npc.swift", "npc.wrath")


def test_an_npc_skill_granted_to_an_ally_seat_is_rejected():
    """NPC 技能只许落在 ``ENEMY`` 座位：授给主角 / AI 队友 ⇒ 构造期 fail-closed。

    这是"只授敌方"的**唯一**兑现处（``PveConfig.grants`` 的座位角色门）：header 里出现
    "主角带着 NPC 技能"的局会连录像自证都过不去，故在构造期就拒。座位表刻意排成
    友 / 友 / 敌，让两个 ``ALLY`` 座位（主角 + AI 队友）都过一遍。
    """
    allies = ("ally", "ally", "enemy")
    for seat in (0, 1):
        with pytest.raises(DataLoadError) as caught:
            _config(
                identities=list(allies),
                grants=(_grant(seat, "skill", NPC_FURY),),
            )
        message = str(caught.value)
        assert "NPC" in message, "拒绝必须点名「NPC 技能」"
        assert f"座位 {seat}" in message, "拒绝必须点名落错的座位"

    # 反例的另一半：**敌方**座位（座位 2）照收——拒的是座位角色，不是 id 本身。
    accepted = _config(
        identities=list(allies), grants=(_grant(2, "skill", NPC_FURY),)
    )
    assert [grant.id for grant in accepted.pve.grants] == [NPC_FURY]


def test_the_npc_seat_rule_is_about_the_carrier_not_the_kind():
    """``kind`` 不改变座位角色门：把 NPC id 塞进**遗物位**补主角同样是拒。

    （座位 0 是主角，故"遗物只授主角"那一条会放行；拒它的是本票的 NPC 座位角色门。）
    四条 NPC id 一条不落：门按**载体**判，不按某一条 id 判。
    """
    for npc_id in NPC_IDS:
        with pytest.raises(DataLoadError) as caught:
            _grants(_grant(0, "relic", npc_id))
        assert "NPC" in str(caught.value), npc_id


def test_npc_skills_on_enemy_seats_do_not_trip_the_growth_limits():
    """四条 NPC 技能同时授给**敌方**座位是合法的（每座技能授予 ≤ 4 的边界整好）。"""
    config = _config(grants=tuple(_grant(2, "skill", npc_id) for npc_id in NPC_IDS))

    assert [grant.id for grant in config.pve.grants] == list(NPC_IDS)


# ---------------------------------------------------------------------------
# `pve.seats`：逐座位合成武将 + 三档常驻加成（M7 票 10；票 03 §3 / §10.1–§10.2）
#
# 敌人不是武将表记录，而是「模板 + 抽到的技能 + 属性加成」⇒ 逐座位的事实必须进 header
# （否则录像重建不出同一场）。校验矩阵：长度 == player_count / 与 identities 对位一致 /
# `hero` 为空时必须有 `name` / 数值区间合法。
# ---------------------------------------------------------------------------


def _seat(
    *,
    hero=None,
    name="",
    hp=4,
    hp_max=4,
    hp_max_bonus=0,
    draw_bonus=0,
    slash_damage_bonus=0,
    **extras,
):
    """一份座位载荷；``hero=None`` 时必须给 ``name``（合成武将）。"""
    payload = {
        "hp": hp,
        "hp_max": hp_max,
        "hp_max_bonus": hp_max_bonus,
        "draw_bonus": draw_bonus,
        "slash_damage_bonus": slash_damage_bonus,
    }
    if hero is not None:
        payload["hero"] = hero
    if name:
        payload["name"] = name
    payload.update(extras)
    return payload


def _seats(*seats):
    return _config(seats=tuple(seats))


def test_a_seats_table_that_does_not_cover_every_seat_is_rejected():
    """长度 != ``identities`` 的长度 ⇒ 拒：座位表与阵营表必须逐座位对齐。"""
    with pytest.raises(DataLoadError) as caught:
        _seats(_seat(name="黄巾力士"))

    assert "seats" in str(caught.value)


@pytest.mark.parametrize("count", [2, 4, 5])
def test_a_seats_table_is_sized_by_the_player_count(count):
    """座位表必须逐座位齐全（长度 == player_count，经 identities 对位那一关兑现）。"""
    layout = ["ally", *(("enemy",) * (count - 1))]
    seats = tuple(_seat(name=f"黄巾力士{i}") for i in range(count))

    config = validate_rule_config(
        {"mode": "pve", "player_count": count, "pve": _pve(identities=layout, seats=seats)}
    )
    assert len(config.pve.seats) == count

    with pytest.raises(DataLoadError) as caught:
        validate_rule_config(
            {
                "mode": "pve",
                "player_count": count,
                "pve": _pve(identities=layout, seats=seats[:-1]),
            }
        )
    assert "seats" in str(caught.value)


def test_a_composite_seat_without_a_name_is_rejected():
    """``hero`` 为空 = 合成武将，此时 ``name`` 是它**唯一**的身份事实（模板名）。"""
    with pytest.raises(DataLoadError) as caught:
        _seats(_seat(), _seat(name="黄巾力士"), _seat(name="黄巾力士"))

    assert "name" in str(caught.value)

    assert _seats(
        _seat(hero="caocao"), _seat(name="黄巾力士"), _seat(name="黄巾力士")
    ).pve.seats[0].hero == "caocao", "有武将的座位不需要 name"


@pytest.mark.parametrize(
    "overrides",
    [
        {"hp": 0},
        {"hp": -1},
        {"hp_max": 0},
        {"hp_max": -3},
        {"hp_max_bonus": -1},
        {"draw_bonus": -1},
        {"slash_damage_bonus": -1},
        {"draw_bonus": 1000},
        {"slash_damage_bonus": 1000},
        {"hp_max": 1000},
    ],
    ids=[
        "hp_zero",
        "hp_negative",
        "hp_max_zero",
        "hp_max_negative",
        "hp_max_bonus_negative",
        "draw_bonus_negative",
        "slash_damage_bonus_negative",
        "draw_bonus_too_big",
        "slash_damage_bonus_too_big",
        "hp_max_too_big",
    ],
)
def test_out_of_range_seat_numbers_are_rejected(overrides):
    """数值区间逐条 fail-closed：体力 / 上限 / 三档加成都在合法区间内。"""
    seat = {**_seat(name="黄巾力士"), **overrides}

    with pytest.raises(DataLoadError):
        _seats(_seat(name="黄巾力士"), seat, _seat(name="黄巾力士"))


def test_the_initial_hp_cannot_exceed_the_effective_maximum():
    """``hp``（初始体力）**分开传**是为了"3/4"这种不满体力开局，不是为了让初始值越上限。

    ``hp_max_bonus`` 算进有效上限：``hp_max=4`` + 加成 2 ⇒ 6，故 6 合法、7 拒。
    """
    assert _seats(
        _seat(name="黄巾力士", hp=3, hp_max=4),
        _seat(name="黄巾力士"),
        _seat(name="黄巾力士"),
    ).pve.seats[0].hp == 3

    assert _seats(
        _seat(name="黄巾力士", hp=6, hp_max=4, hp_max_bonus=2),
        _seat(name="黄巾力士"),
        _seat(name="黄巾力士"),
    ).pve.seats[0].hp == 6

    with pytest.raises(DataLoadError) as caught:
        _seats(
            _seat(name="黄巾力士", hp=7, hp_max=4, hp_max_bonus=2),
            _seat(name="黄巾力士"),
            _seat(name="黄巾力士"),
        )
    assert "hp" in str(caught.value)


def test_seats_are_accepted_and_survive_a_dump_round_trip():
    """带 seats 的配置 dump 往返**逐字段相等**（= header 自证的前置）。"""
    config = _config(
        seats=(
            _seat(hero="caocao", hp=3, hp_max=4, hp_max_bonus=1, draw_bonus=1),
            _seat(
                name="黄巾力士",
                hp=2,
                hp_max=3,
                slash_damage_bonus=2,
                kingdom="qun",
                gender="male",
            ),
            _seat(name="黄巾渠帅", hp=5, hp_max=5),
        )
    )

    seats = config.pve.seats
    assert [seat.hero for seat in seats] == ["caocao", None, None]
    assert [seat.name for seat in seats] == ["", "黄巾力士", "黄巾渠帅"]
    assert [(seat.hp, seat.hp_max, seat.hp_max_bonus) for seat in seats] == [
        (3, 4, 1),
        (2, 3, 0),
        (5, 5, 0),
    ]
    assert (seats[0].draw_bonus, seats[1].slash_damage_bonus) == (1, 2)
    assert (seats[1].kingdom.value, seats[1].gender.value) == ("qun", "male")

    assert RuleConfig.model_validate(config.model_dump(mode="json")) == config, (
        "seats 必须 dump 往返逐字段相等，否则录像重建不出同一场"
    )


def test_an_empty_seats_table_keeps_the_pve_section_legal():
    """没有座位表 = 老 PvE 局（票 07/08/09 的形态），不强制升级。"""
    assert _config().pve.seats == ()
    assert _config().model_dump(mode="json")["pve"]["seats"] == []
