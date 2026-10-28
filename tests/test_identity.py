"""身份发放、模式派生与主公（票 24）。

落点 = 库门面（主缝）：``RuleConfig(player_count=…)`` + ``Engine.new_game`` 产出的
**开局记录流**与 **``GameState``**。身份不是时机事件，故它只在 ``delta`` / 状态上可观测。

被测的六件事：
1. 身份逐座落在**发牌之前**、不新增时机节点，同一 seed 可复现；
2. 每个座位恰一个身份，配比 = 官方主表；
3. 主公体力与体力上限为初始值 +1，其余座位不变；
4. 先手 = 主公座位（从 state 派生，不用配置里的 ``first_seat``）；
5. 公开面里身份只对本人 / 主公 / 已死亡者可见；
6. 主公技门控接口位恒为假且可断言（M2 一个都不可发动）。
"""

from __future__ import annotations

import pytest

import sgs.agents as agents
from sgs.data import ContentPack, Identity, RuleConfig, load_content_pack, roster_for
from sgs.engine import INITIAL_HP, Engine, ai_stream, read_journal
from sgs.skills import LORD_SKILL_IDS, lord_skill_allowed
from sgs.state import PlayerPublic, project

from support import RecordingPlayer, SeatSpec, build_state, run_turns

PACK: ContentPack = load_content_pack(deck_profile="standard_108")
SEED = 20261009


def _new(count: int, seed: int = SEED) -> Engine:
    return Engine.new_game(seed=seed, rule_config=RuleConfig(player_count=count), pack=PACK)


def _identity_records(engine):
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == "identity_deal"
    ]


def _lord_seat(engine) -> int:
    seats = [
        seat
        for seat, player in enumerate(engine.state.players)
        if player.identity is Identity.LORD
    ]
    assert len(seats) == 1, "身份局必须恰有一个主公座位"
    return seats[0]


@pytest.mark.parametrize("count", [4, 5, 6, 7, 8])
def test_every_seat_gets_exactly_one_identity_in_seat_order(count):
    engine = _new(count)
    deals = _identity_records(engine)

    assert [record.seat for record in deals] == list(range(count)), "逐座、按座序一条"
    assert sorted(record.identity.value for record in deals) == sorted(
        identity.value for identity in roster_for(count)
    ), "配比多重集 = 官方主表"
    assert engine.state.players[0].identity == deals[0].identity, "下标 i → 座位 i"


def test_the_deal_lands_before_the_initial_hand_and_dispatches_no_timing():
    """票 13 §3：开局流 = 洗牌 → 逐座身份 → 发牌时机 → 逐座摸牌；身份不是时机事件。"""
    engine = _new(6)
    kinds = [
        (record.kind, getattr(record, "delta_type", None), getattr(record, "timing", None))
        for record in engine.records[1:]
    ]

    assert kinds[0] == ("delta", "deck_shuffle", None), "seq = 1 仍是初次洗牌"
    assert all(entry == ("delta", "identity_deal", None) for entry in kinds[1:7]), (
        "身份发放恰好占 6 条 delta，中间不夹时机"
    )
    assert kinds[7][0] == "timing", "发牌时机紧随身份发放之后"
    assert kinds[8] == ("delta", "card_move", None), "发牌在身份之后"
    assert [record.seq for record in _identity_records(engine)] == list(range(2, 8))


def test_no_identity_mode_keeps_the_m1_opening_layout_without_identity_records():
    """2 人局（无身份局）完全不产生 ``identity_deal``：M1 六条基准的 seq 布局不变。"""
    engine = _new(2)

    assert _identity_records(engine) == []
    assert all(player.identity is None for player in engine.state.players)
    assert engine.records[1].delta_type == "deck_shuffle"
    assert engine.records[2].kind == "timing"
    assert engine.records[3].delta_type == "card_move"


def test_the_identity_deal_round_trips_through_the_journal(tmp_path):
    engine = _new(6)
    text = engine.journal()
    path = tmp_path / "identity.jsonl"
    path.write_text(text, encoding="utf-8")

    records = read_journal(path)
    deals = [
        record for record in records if getattr(record, "delta_type", None) == "identity_deal"
    ]

    assert [record.identity for record in deals] == [
        record.identity for record in _identity_records(engine)
    ], "身份是记录流 schema 的一部分：写读对称"
    assert [record.seat for record in deals] == list(range(6))


def test_wrap_delta_recognises_the_identity_delta():
    """通用包装器对第四个 ``delta_type`` 也成立（否则会静默退化成洗牌记录）。"""
    from sgs.engine import wrap_delta
    from sgs.state import IdentityDealDelta

    record = wrap_delta(
        IdentityDealDelta(seat=3, identity=Identity.RENEGADE), seq=5, frame_id=0, depth=0
    )

    assert record.kind == "delta" and record.delta_type == "identity_deal"
    assert record.seat == 3 and record.identity is Identity.RENEGADE


def _initial_shuffle(engine) -> list[int]:
    record = engine.records[1]
    assert record.delta_type == "deck_shuffle", "seq = 1 必是初次洗牌"
    return [card.card_id for card in record.cards]


def test_the_identity_deal_is_deterministic_and_does_not_move_the_shuffle_stream():
    """身份走独立子流（票 13 §3）：同一 seed 可复现，且不消耗洗牌子流。"""
    first = _new(6)
    second = _new(6)

    assert [record.identity for record in _identity_records(first)] == [
        record.identity for record in _identity_records(second)
    ]
    assert _initial_shuffle(first) == _initial_shuffle(second)
    assert _initial_shuffle(first) == _initial_shuffle(_new(2)), (
        "初次洗牌只是 (seed, shuffle) 的函数：座位数与身份发放都不扰动它"
    )


@pytest.mark.parametrize("count", [4, 5, 6, 7, 8])
def test_the_lord_has_one_extra_life_and_everyone_else_the_base(count):
    engine = _new(count)
    lord = _lord_seat(engine)

    for player in engine.state.players:
        if player.seat == lord:
            assert player.hp == INITIAL_HP + 1
            assert player.hp_max == INITIAL_HP + 1
        else:
            assert player.hp == INITIAL_HP
            assert player.hp_max == INITIAL_HP


@pytest.mark.parametrize("count", [4, 5, 6, 7, 8])
def test_every_seat_starts_with_the_uniform_four_card_hand(count):
    """票 24：起始手牌**统一 4 张**，不随人数变化（规则文档没有按人数变化的表）。"""
    engine = _new(count)

    assert [len(player.hand) for player in engine.state.players] == [4] * count
    assert len(engine.state.players[_lord_seat(engine)].hand) == 4, "主公的起始手牌也是 4"


def test_the_lord_seat_starts_the_game():
    """先手 = 主公座位（从 state 派生）；构造一个主公不在座位 0 的局面钉死这条。"""
    count = 6
    engine = _new(count)
    for player in engine.state.players:
        player.identity = Identity.REBEL
    engine.state.players[count - 1].identity = Identity.LORD

    run_turns(engine, [RecordingPlayer() for _ in range(count)], turns=1)

    starts = [
        record.actor
        for record in engine.records
        if record.kind == "timing" and record.timing.value == "TURN_START"
    ]
    assert starts[0] == count - 1, "第一个 TURN_START 的座位就是主公座位"


def test_a_run_reaches_game_end_in_every_identity_seat_count():
    """票 23 的多人可达性在身份发放落地后仍成立（胜负语义归票 25）。"""
    for count in (4, 5, 6, 7, 8):
        engine = _new(count)
        engine.run([agents.RandomPlayer(ai_stream(SEED, seat)) for seat in range(count)])

        assert engine.finished, f"{count} 人局必须收局"
        assert engine.records[-1].kind == "game_end"


# ---------------------------------------------------------------------------
# 参与者公开面：身份可见性（票 24 / 决策回票 13 §6）
# ---------------------------------------------------------------------------


def _four_seat_projection():
    """座位 0 反贼 / 1 主公 / 2 已阵亡忠臣 / 3 存活内奸；观察者由参数给。"""
    state = build_state(
        pack=PACK,
        rule_config=RuleConfig(player_count=4),
        seats=[
            SeatSpec(hp=4, identity=Identity.REBEL),
            SeatSpec(hp=4, identity=Identity.LORD),
            SeatSpec(hp=0, hp_max=4, alive=False, identity=Identity.LOYALIST),
            SeatSpec(hp=4, identity=Identity.RENEGADE),
        ],
        deck=tuple(range(10)),
    )
    return state


def test_identity_is_visible_to_self_the_lord_and_the_dead_only():
    state = _four_seat_projection()

    view = project(state, 0)

    assert [player.identity for player in view.players] == [
        Identity.REBEL,
        Identity.LORD,
        Identity.LOYALIST,
        None,
    ], "本人 / 主公 / 已死亡者可见，其余存活者不得泄露"


def test_the_lord_view_also_hides_everyone_but_self_the_lord_and_the_dead():
    state = _four_seat_projection()

    view = project(state, 1)

    assert [player.identity for player in view.players] == [
        None,
        Identity.LORD,
        Identity.LOYALIST,
        None,
    ]


def test_the_dead_are_revealed_to_everyone():
    state = _four_seat_projection()

    for viewer in range(4):
        assert project(state, viewer).players[2].identity is Identity.LOYALIST, (
            "阵亡即亮明，对所有参与者可见"
        )


def test_no_identity_mode_projects_no_identities():
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4), SeatSpec(hp=4)],
        deck=tuple(range(10)),
    )

    view = project(state, 0)

    assert [player.identity for player in view.players] == [None, None]
    assert "identity" in PlayerPublic.model_fields, "身份是公开面的字段，不是视图私有数据"


# ---------------------------------------------------------------------------
# 主公技接口位：M3 起由技能定义派生（票 24 / 决策回票 13 §4，U-13-1；票 09 转正）
# ---------------------------------------------------------------------------


def test_the_lord_skill_ids_are_now_derived_from_the_m3_definitions():
    """M2 的接口位是手抄空集；M3 票 09 转正 ⇒ 由 ``SkillDef.skill_type`` 派生（六条主公技）。"""
    assert LORD_SKILL_IDS == frozenset(
        {
            "hero.caocao.hujia",
            "hero.jie_caocao.hujia",
            "hero.liubei.jijiang",
            "hero.jie_liubei.jijiang",
            "hero.sunquan.jiuyuan",
            "hero.jie_sunquan.jiuyuan",
        }
    )


@pytest.mark.parametrize("identity", list(Identity))
def test_the_lord_skill_gate_reads_identity_and_skill_type(identity):
    """`(身份==主公) ∧ (类型==主公技)`：M3 起主公可发动，其余身份与装备技能照旧为假。"""
    assert lord_skill_allowed(identity, "equip.crossbow") is False
    assert lord_skill_allowed(identity, "hero.caocao.hujia") is (identity is Identity.LORD)




# ---------------------------------------------------------------------------
# 身份可见性成为**引擎状态**（U1-04）：主公默认亮明，其余默认暗置
# ---------------------------------------------------------------------------


def test_a_new_identity_game_marks_only_the_lord_as_identity_visible():
    """``Player.identity_visible``：主公 ``True``、其余 ``False``（M5 的亮明效果写它）。"""
    engine = _new(6)
    lord = _lord_seat(engine)

    assert [player.identity_visible for player in engine.state.players].count(True) == 1
    assert engine.state.players[lord].identity_visible is True
    for player in engine.state.players:
        if player.seat != lord:
            assert player.identity_visible is False, f"非主公 {player.seat} 不得默认亮明"


def test_no_identity_mode_marks_nobody_identity_visible():
    engine = _new(2)

    assert all(player.identity is None for player in engine.state.players)
    assert all(player.identity_visible is False for player in engine.state.players)


def test_the_projection_honours_the_identity_visible_field():
    """``identity_visible=True`` 的存活非主公对**所有**参与者可见（U1-04 的第四种可见情形）。"""
    state = _four_seat_projection()
    state.players[3].identity_visible = True

    for viewer in range(4):
        assert project(state, viewer).players[3].identity is Identity.RENEGADE, (
            f"查看者 {viewer} 应看到被字段亮明的座位 3"
        )


def test_the_projection_still_hides_a_seat_whose_field_is_false():
    state = _four_seat_projection()
    state.players[3].identity_visible = False

    assert project(state, 0).players[3].identity is None
    assert project(state, 1).players[3].identity is None
