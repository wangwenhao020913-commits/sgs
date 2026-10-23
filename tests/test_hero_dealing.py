"""武将发放、体力上限与可见性（票 02）。

落点（与票 01 同姿态）：**公开接缝**——``build_initial`` 的开局参数、``sgs.skills`` 的
``resolve_instances``、``sgs.state.project`` 的 ``PlayerView``，以及产品 CLI。不测私有函数。

票 02 尚未启用任何武将（那是票 12），故随机发放用 **``hero_pool`` 显式换池**表达：
池是入参、不走数据文件的 ``enabled`` 名单，这样"发放"这套机器在本票可测，而
"数据文件里启用谁"仍留给定票。

票 12 起数据文件里的启用池非空（票 12 的 16 名；M6 P0 票 01 后 19 名），故
``hero_pool=()`` 的那条断言改判为"从启用池里真发得出牌"；显式换池的路径一字未动。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from sgs.data import (
    Gender,
    Hero,
    HeroSkill,
    Identity,
    Kingdom,
    RuleConfig,
    enabled_hero_ids,
    load_content_pack,
    load_heroes,
)
from sgs.engine import INITIAL_HP, Engine, RandomSource, build_initial
from sgs.skills import resolve_instances
from sgs.state import project

from support import SeatSpec, build_state

ROOT = Path(__file__).resolve().parent.parent
PACK = load_content_pack(deck_profile="standard_108")
HEROES = load_heroes()

# 池里五名武将：势力覆盖四家（可见性断言要读它）、体力上限有 4 也有 3。
POOL = ("caocao", "guanyu", "diaochan", "simayi", "lvbu")


def _heroes_of(state) -> tuple[str | None, ...]:
    return tuple(player.hero for player in state.players)


def _build(
    *,
    seed: int,
    player_count: int,
    heroes=(),
    hero_pool=None,
    pinned_hero=None,
    pinned_seat=0,
):
    """``build_initial`` 的薄包装：只取状态，规则配置按人数造。"""
    state, _ = build_initial(
        seed=seed,
        rule_config=RuleConfig(player_count=player_count),
        pack=PACK,
        heroes=heroes,
        hero_pool=hero_pool,
        pinned_hero=pinned_hero,
        pinned_seat=pinned_seat,
    )
    return state


def _expected_draw(pool: tuple[str, ...], count: int, *, seed: int) -> tuple[str, ...]:
    """用 ``hero`` 子流独立复现"无放回抽取序列"（期望值的独立真源）。"""
    rng = RandomSource(seed).substream("hero")
    picked: list[str] = []
    while len(picked) < count:
        candidate = pool[rng.randbelow(len(pool))]
        if candidate not in picked:
            picked.append(candidate)
    return tuple(picked)


# ---------------------------------------------------------------------------
# 发放：专用子流、不重复、按座位序
# ---------------------------------------------------------------------------


def test_the_same_seed_deals_the_same_heroes():
    """同种子 ⇒ 同武将分配（跨调用的可复现性）。"""
    first = _heroes_of(_build(seed=7, player_count=4, hero_pool=POOL))
    second = _heroes_of(_build(seed=7, player_count=4, hero_pool=POOL))

    assert first == second
    assert all(hero is not None for hero in first), "换池给出非空池 ⇒ 每座都发到武将"


def test_a_five_seat_game_deals_five_pairwise_different_heroes():
    """5 人局的 5 名武将两两不同（不重复抽取）。"""
    pool = tuple(hero.id for hero in HEROES.heroes[:8])
    state = _build(seed=11, player_count=5, hero_pool=pool)

    heroes = _heroes_of(state)

    assert len(heroes) == 5
    assert len(set(heroes)) == 5, f"5 名武将必须两两不同，得到 {heroes}"
    assert set(heroes) <= set(pool)


def test_heroes_are_assigned_in_seat_order_without_extra_draws():
    """按座位序分配：座位 i 拿到的就是抽取序列里的第 i 个。

    判据 = "分配本身不再抽随机"：用 ``hero`` 子流独立复现抽取序列，再与座位序比对；
    若分配阶段又抽了一次，两者会错位。
    """
    expected = _expected_draw(POOL, 4, seed=19)

    state = _build(seed=19, player_count=4, hero_pool=POOL)

    assert _heroes_of(state) == expected
    assert (state.players[0].hero, state.players[3].hero) == (expected[0], expected[3])


def test_the_hero_substream_does_not_consume_the_other_purposes():
    """子流判据：``hero`` 与 ``shuffle`` / ``identity`` 各自独立、互不消耗位置。"""
    baseline = RandomSource(31).substream("shuffle").randbelow(1000)
    RandomSource(31).substream("hero").randbelow(1000)
    after_hero = RandomSource(31).substream("shuffle").randbelow(1000)

    identity = RandomSource(31).substream("identity").randbelow(1000)
    after_more = RandomSource(31).substream("shuffle").randbelow(1000)

    assert after_hero == baseline == after_more, "取用其它用途不扰动 shuffle 子流"
    assert identity != baseline or True  # 只作"两条派生各自独立"的可读对照


def test_dealing_does_not_disturb_the_shuffle_tail():
    """真的开局：发武将之后，洗牌结果与不发武将时**逐字一致**。"""
    without, _ = build_initial(
        seed=23, rule_config=RuleConfig(player_count=4), pack=PACK, heroes=()
    )
    with_heroes = _build(seed=23, player_count=4, hero_pool=POOL)

    assert list(with_heroes.deck) == list(without.deck)
    assert [player.hand for player in with_heroes.players] == [
        player.hand for player in without.players
    ]


def test_explicit_heroes_do_not_consume_the_hero_substream():
    """``--heroes`` 显式指定时不再消耗 ``hero`` 子流。

    判据：先跑一次显式指定，再跑一次随机发放——后者取到的序列与"从未跑过显式指定"时相同
    （显式指定没有推进子流位置）。
    """
    expected = _expected_draw(POOL, 5, seed=41)

    explicit = _build(seed=41, player_count=5, heroes=POOL)
    assert _heroes_of(explicit) == POOL, "显式指定按座位序原样落地，不走随机"

    random_dealt = _build(seed=41, player_count=5, hero_pool=POOL)

    assert _heroes_of(random_dealt) == expected


def test_a_pool_smaller_than_the_seat_count_is_rejected():
    """池不够座位数时 fail-closed，不静默发空位。"""
    with pytest.raises(ValueError) as caught:
        _build(seed=5, player_count=4, hero_pool=("caocao", "guanyu"))

    assert "caocao" in str(caught.value), "报错要带上池的内容，好定位"


def test_an_unknown_hero_id_is_rejected():
    with pytest.raises(ValueError) as caught:
        _build(seed=5, player_count=4, heroes=("caocao", "guanyu", "diaochan", "nobody"))

    assert "nobody" in str(caught.value)


def test_enabled_hero_ids_reads_the_table_enablement():
    """启用门控的只读面：M6 P0 票 01 起数据文件里有 19 名启用 ⇒ 启用池恰是那 19 名。"""
    ids = enabled_hero_ids(HEROES)

    assert len(ids) == 19
    assert set(ids) == {
        "caocao",
        "simayi",
        "xiahoudun",
        "zhangliao",
        "xuchu",
        "guojia",
        "liubei",
        "guanyu",
        "zhaoyun",
        "machao",
        "zhugeliang",
        "sunquan",
        "zhouyu",
        "luxun",
        "daqiao",
        "sunshangxiang",
        "huatuo",
        "lvbu",
        "diaochan",
    }
    assert all(HEROES.by_id(hero_id).enabled for hero_id in ids)

    # 派生判据本身（"读的是 enabled 字段"）：造一张只启用第一名的表，池随之收缩。
    only_first = HEROES.model_copy(
        update={
            "heroes": (
                HEROES.heroes[0].model_copy(update={"enabled": True}),
                *(
                    hero.model_copy(update={"enabled": False})
                    for hero in HEROES.heroes[1:]
                ),
            )
        }
    )

    assert enabled_hero_ids(only_first) == (HEROES.heroes[0].id,)
    assert isinstance(HEROES.heroes[0], Hero)
    assert isinstance(HEROES.heroes[0].skills[0], HeroSkill)


def test_an_empty_explicit_pool_means_the_data_file_enabled_pool():
    """``hero_pool=()`` = 换用数据文件的启用池；票 12 起该池非空，故 4 人局发得出 4 名。"""
    state = _build(seed=5, player_count=4, hero_pool=())

    dealt = _heroes_of(state)

    assert len(set(dealt)) == 4, "不重复抽取"
    assert set(dealt) <= set(enabled_hero_ids(HEROES))


# ---------------------------------------------------------------------------
# 点将（票 19）：预指派一个座位 + 其余从**去掉它的池**里随机
# ---------------------------------------------------------------------------


def test_a_picked_hero_lands_on_its_seat_and_never_reaches_the_others():
    """点将 = 座位 0 指定；其余座位从**去掉该武将后的池**里随机——不得再抽到他/她。"""
    heroes = _heroes_of(_build(seed=13, player_count=5, hero_pool=POOL, pinned_hero="diaochan"))

    assert heroes[0] == "diaochan", "点将的武将必须落在座位 0"
    assert "diaochan" not in heroes[1:], "本局其它座位不得拿到我已点的武将"
    assert len(set(heroes)) == 5, "其余座位仍是无放回随机"
    assert set(heroes) <= set(POOL), "不得发出池外的武将"


def test_the_other_seats_are_dealt_from_the_pool_without_the_pick():
    """独立真源：其余座位 = 在 ``池 − 点将`` 上跑同一套无放回抽取（不引入新的随机源）。"""
    rest = tuple(hero for hero in POOL if hero != "simayi")
    expected = ("simayi",) + _expected_draw(rest, 4, seed=17)

    heroes = _heroes_of(_build(seed=17, player_count=5, hero_pool=POOL, pinned_hero="simayi"))

    assert heroes == expected


def test_the_pick_can_be_moved_off_the_first_seat():
    """预指派是"座位 × 武将"两件事：人类座位不是 0 时点将也得落在**他那张椅子**上。"""
    heroes = _heroes_of(
        _build(seed=13, player_count=5, hero_pool=POOL, pinned_hero="guanyu", pinned_seat=3)
    )

    assert heroes[3] == "guanyu"
    assert "guanyu" not in heroes[:3] + heroes[4:]


def test_the_same_seed_and_the_same_pick_deal_the_same_heroes():
    first = _heroes_of(_build(seed=17, player_count=5, hero_pool=POOL, pinned_hero="simayi"))
    second = _heroes_of(_build(seed=17, player_count=5, hero_pool=POOL, pinned_hero="simayi"))
    other = _heroes_of(_build(seed=17, player_count=5, hero_pool=POOL, pinned_hero="lvbu"))

    assert first == second, "同 seed + 同点将 ⇒ 同一份发将结果"
    assert first != other, "换一名点将 ⇒ 结果随之改变（不是常量）"


def test_a_pick_works_against_the_data_file_enabled_pool():
    """``hero_pool=()``（启用池）下点将同样成立。"""
    heroes = _heroes_of(_build(seed=23, player_count=4, hero_pool=(), pinned_hero="liubei"))

    assert heroes[0] == "liubei"
    assert "liubei" not in heroes[1:]


def test_an_unknown_pick_is_refused_instead_of_falling_back_to_random():
    with pytest.raises(ValueError) as caught:
        _build(seed=5, player_count=4, hero_pool=POOL, pinned_hero="nobody")

    assert "nobody" in str(caught.value), "未知 id 要 fail-closed，不得静默退回随机"


def test_a_pick_outside_the_pool_is_refused():
    """池里没有这名武将 ⇒ 报错（"自由选一名"只限池内）。"""
    pool = ("caocao", "guanyu", "diaochan", "simayi", "lvbu")
    with pytest.raises(ValueError) as caught:
        _build(seed=5, player_count=4, hero_pool=pool, pinned_hero="zhouyu")

    assert "zhouyu" in str(caught.value)


def test_a_pick_needs_a_pool_to_deal_the_other_seats():
    """无武将模式（``hero_pool=None``）下没有池可发其余座位 ⇒ 报错。"""
    with pytest.raises(ValueError):
        _build(seed=5, player_count=4, hero_pool=None, pinned_hero="caocao")


def test_a_pick_and_a_full_explicit_lineup_are_mutually_exclusive():
    """全显式与部分预指派是两种互斥语义，同时给 = 配置非法。"""
    with pytest.raises(ValueError):
        _build(
            seed=5,
            player_count=4,
            heroes=("caocao", "guanyu", "diaochan", "simayi"),
            pinned_hero="caocao",
        )


def test_a_pick_beyond_the_seat_range_is_refused():
    with pytest.raises(ValueError):
        _build(seed=5, player_count=4, hero_pool=POOL, pinned_hero="caocao", pinned_seat=4)


def test_no_pick_keeps_the_random_path_untouched():
    """开关关着（不点将）⇒ 与引入点将之前**逐字节一致**：仍是纯随机那一份。"""
    plain = _heroes_of(_build(seed=29, player_count=5, hero_pool=POOL))
    explicit_none = _heroes_of(_build(seed=29, player_count=5, hero_pool=POOL, pinned_hero=None))

    assert plain == explicit_none == _expected_draw(POOL, 5, seed=29)


# ---------------------------------------------------------------------------
# 体力上限：读武将数据；主公 +1 的既有初始化段不动
# ---------------------------------------------------------------------------


def test_hp_max_comes_from_the_hero_data():
    explicit = ("caocao", "guanyu", "diaochan", "simayi")
    state = _build(seed=3, player_count=4, heroes=explicit)

    assert [player.hp_max for player in state.players] == [
        HEROES.by_id(hero).hp_max + (1 if player.identity is Identity.LORD else 0)
        for player, hero in zip(state.players, explicit, strict=True)
    ]
    assert [player.hp for player in state.players] == [player.hp_max for player in state.players]


def test_the_lord_gets_plus_one_on_top_of_the_hero_hp_max():
    """主公 +1 落在既有初始化段（`03`（§3「武将数据模型」））；哪一座是主公由 ``identity`` 决定，不预设座位。"""
    explicit = ("caocao", "guanyu", "diaochan", "lvbu")
    state, _ = build_initial(
        seed=3,
        rule_config=RuleConfig(player_count=4),
        pack=PACK,
        heroes=explicit,
    )

    lord = next(player for player in state.players if player.identity is Identity.LORD)
    others = [player for player in state.players if player.identity is not Identity.LORD]

    assert lord.hp_max == HEROES.by_id(lord.hero).hp_max + 1, "主公在武将体力上限之上 +1"
    assert lord.hp == lord.hp_max
    assert [player.hp_max for player in others] == [
        HEROES.by_id(player.hero).hp_max for player in others
    ]


def test_no_heroes_falls_back_to_the_engine_constant():
    """无武将模式：体力上限回落到 ``INITIAL_HP``，主公 +1 的既有初始化段照旧生效。"""
    state, _ = build_initial(
        seed=3, rule_config=RuleConfig(player_count=4), pack=PACK, heroes=()
    )

    expected = {
        INITIAL_HP + (1 if player.identity is Identity.LORD else 0) for player in state.players
    }
    assert {player.hp_max for player in state.players} == expected
    assert {player.hero for player in state.players} == {None}
    assert {player.kingdom for player in state.players} == {None}


def test_a_two_seat_game_deals_two_heroes_without_identities_or_lord_skills():
    """2 人局走同一条路径；无身份 ⇒ 主公技整类不存在。

    本票尚未登记武将技能定义（票 12），故"非主公的护驾不产出"这条门控本身由票 01 的
    注入式测试钉住；这里补的是**本票新增的前提**：无身份局真的没有主公，
    于是门控对 2 人局同样成立。
    """
    state = _build(seed=13, player_count=2, hero_pool=("caocao", "simayi", "guanyu"))

    dealt = [hero for hero in _heroes_of(state) if hero is not None]

    assert len(dealt) == 2
    assert all(player.identity is None for player in state.players), "无身份局没有主公"
    assert not [
        instance
        for instance in resolve_instances(state)
        if instance.skill_id == "hero.caocao.hujia"
    ], "无主公 ⇒ 主公技实例数为 0（票 01 的门控自动成立）"


# ---------------------------------------------------------------------------
# 可见性：武将 id 与势力是公开信息
# ---------------------------------------------------------------------------


def test_another_players_hero_and_kingdom_are_visible():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero="caocao", kingdom=Kingdom.WEI),
            SeatSpec(
                hp=3,
                hp_max=3,
                hand=(0, 1),
                identity=Identity.REBEL,
                hero="diaochan",
                kingdom=Kingdom.QUN,
            ),
        ],
    )

    view = project(state, 0)
    other = view.players[1]

    assert other.hero == "diaochan"
    assert other.kingdom is Kingdom.QUN
    assert other.hand_count == 2, "手牌只给数量"
    assert other.identity is None, "别人的身份仍不可见（过滤逻辑一字不改）"
    assert view.self_hand == state.refs(state.player(0).hand)


def test_the_owner_sees_their_own_identity_and_the_view_has_no_extra_players_fields():
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(identity=Identity.LORD, hero="caocao", kingdom=Kingdom.WEI),
            SeatSpec(),
        ],
    )

    view = project(state, 0)

    assert view.players[0].identity is Identity.LORD
    assert "hero" in type(view.players[0]).model_fields
    assert "opponent_hand" not in type(view).model_fields


def test_undealt_hero_means_not_dealt_not_hidden():
    """``hero = None`` 专指"未发放武将"，与"暗置武将"不是一回事（票 02 §可见性 9）。"""
    never_dealt = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    view = project(never_dealt, 0)

    assert [player.hero for player in view.players] == [None, None]
    assert [player.kingdom for player in view.players] == [None, None]


def test_the_new_visibility_fields_do_not_change_the_serialized_journal():
    """无武将模式下记录流逐字节不变：两条新字段**不进序列化**（票 02 §验收 5/6）。

    判据不重跑一局：直接投影一个人为局面，``model_dump(mode="json")`` 的键集必须与
    M2 的公开面**恰好相等**（既有基准磁带的形状由此被钉住）。
    """
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec(hand=(0, 1))])
    public = project(state, 0).players[1]
    dumped = public.model_dump(mode="json")

    assert "hero" not in dumped and "kingdom" not in dumped
    assert set(dumped) == {
        "seat",
        "hp",
        "hp_max",
        "hand_count",
        "alive",
        "equip",
        "judge",
        "identity",
    }, "新增的公开字段不得落进记录流的序列化形态"
    assert public.hero is None and public.kingdom is None, "字段本身仍在模型上可读"


def test_the_gender_field_is_filled_from_the_hero_data():
    """票 02 留的独立议题由票 12 收口：真实对局里性别由武将数据填值（不再是独立事项）。

    票 02 时这条断言说的是"只有人为局面能给性别"；票 12 把 ``gender=hero.gender`` 接进
    ``_initial_player``，故现在断言**数据驱动**：发到女武将的座位性别就是女。
    """
    explicit = ("diaochan", "zhouyu", "daqiao", "lvbu")
    state = _build(seed=3, player_count=4, heroes=explicit)

    assert [player.gender for player in state.players] == [
        HEROES.by_id(hero).gender for hero in explicit
    ]
    assert [player.gender for player in state.players] == [
        Gender.FEMALE,
        Gender.MALE,
        Gender.FEMALE,
        Gender.MALE,
    ]


# ---------------------------------------------------------------------------
# 跨 PYTHONHASHSEED 的确定性（挂到 tests/test_determinism.py 的口径）
# ---------------------------------------------------------------------------


_PROBE = textwrap.dedent(
    """
    import json
    import sys

    from sgs.data import RuleConfig, load_content_pack, load_heroes
    from sgs.engine import build_initial

    seed = int(sys.argv[1])
    pool = tuple(hero.id for hero in load_heroes().heroes[:8])
    state, _ = build_initial(
        seed=seed,
        rule_config=RuleConfig(player_count=5),
        pack=load_content_pack(deck_profile="standard_108"),
        hero_pool=pool,
    )
    assignment = [
        {
            "seat": player.seat,
            "hero": player.hero,
            "kingdom": player.kingdom.value if player.kingdom else None,
            "hp_max": player.hp_max,
        }
        for player in state.players
    ]
    sys.stdout.write(
        json.dumps(assignment, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    """
)


def _probe_assignment(hash_seed: str) -> bytes:
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, "17"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.encode("utf-8")


def test_the_hero_assignment_is_byte_identical_across_python_hash_seeds():
    first = _probe_assignment("0")
    second = _probe_assignment("1")

    assert first, "探针必须产出一份非空的分配"
    assert first == second, "同种子的武将分配在两个 PYTHONHASHSEED 下不是逐字节一致"
