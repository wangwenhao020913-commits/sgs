"""M5-03：铁索连环（1–2 目标 + 独立 toggle）+ 横置状态的 delta / 投影。

落点 = 外部缝：``legal_actions_for`` 的候选面（目标规则）+ ``Engine.from_state`` 的人为
局面 + 记录流（``chained_change`` delta）+ 投影（``project``）。

规格 §5.1.2 / D7：

- 目标数 **1–2**；可指定自己；不受距离限制；**0 目标非法**；同一角色不能重复；
  目标须存活；每个目标**独立 toggle**；
- 使用时机 = 出牌阶段；普通锦囊（开无懈窗口）；无次数限制；
- 目标规则是新的 min/max 规则，不是 W7 的预发动清单。
"""

from __future__ import annotations

import pytest

from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.effects import legal_actions_for
from sgs.engine import Engine, IllegalActionError, read_journal
from sgs.state import (
    EndPhase,
    GameState,
    MoveReason,
    Pass,
    QueryKind,
    UseCard,
    WindowCause,
    project,
)

from support import SeatSpec, build_state, cards_of_type

PACK = load_content_pack(deck_profile="standard_ex_160")

IRON_CHAIN = cards_of_type(PACK, "iron_chain")
DODGE = cards_of_type(PACK, "dodge")
PEACH = cards_of_type(PACK, "peach")


def _deck_excluding(*cards: int) -> tuple[int, ...]:
    excluded = set(cards)
    return tuple(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in excluded)


def _deltas(engine, delta_type):
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


def _minimal(query):
    if query.kind in ("RESPOND", "DODGE", "PEACH"):
        for action in query.legal_actions:
            if isinstance(action, Pass):
                return action
    if query.kind == "PLAY_PHASE":
        for action in query.legal_actions:
            if isinstance(action, EndPhase):
                return action
    return query.legal_actions[0]


def _advance(engine, players, stop) -> Engine:
    for _ in range(20_000):
        if stop(engine):
            return engine
        if engine.pending is None:
            engine._step()
            if engine.finished:
                return engine
            continue
        query = engine.pending
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


class _Passive:
    """最小合法应答的参与者（其余座位用）。"""

    def respond(self, query):
        return _minimal(query)


def _four_players(actor):
    return [actor, _Passive(), _Passive(), _Passive()]


class _UseIronChain:
    """出牌阶段用铁索打 ``targets``（手里还有铁索就再来一次）；其余最小应答。"""

    def __init__(self, targets: tuple[int, ...], *, uses: int = 1) -> None:
        self.targets = targets
        self.uses = uses
        self.used = 0

    def respond(self, query):
        if query.kind == QueryKind.PLAY_PHASE and self.used < self.uses:
            for action in query.legal_actions:
                if (
                    isinstance(action, UseCard)
                    and action.card.type == "iron_chain"
                    and action.targets == self.targets
                ):
                    self.used += 1
                    return action
        return _minimal(query)


def _chain_combos(state: GameState, seat: int = 0) -> list[tuple[int, ...]]:
    return sorted(
        action.targets
        for action in legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)
        if isinstance(action, UseCard) and action.card.type == "iron_chain"
    )


def _four_seats(*, hand=(IRON_CHAIN[0],), chained=(), dead=()):
    """4 座：0 号主公（身份局必须有主公）、其余反贼；``hand`` 只给 0 号。"""
    seats = [
        SeatSpec(
            hand=hand if index == 0 else (),
            chained=index in chained,
            alive=index not in dead,
            identity=Identity.LORD if index == 0 else Identity.REBEL,
        )
        for index in range(4)
    ]
    return build_state(
        pack=PACK,
        seats=seats,
        deck=_deck_excluding(*hand),
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )


# ---------------------------------------------------------------------------
# 目标规则：1–2 个、互不重复、存活、可含自己、无距离
# ---------------------------------------------------------------------------


def test_iron_chain_offers_every_one_or_two_target_combo_including_self():
    """4 人全存活：目标组合 = 全部 1 元组 + 全部 2 元组（含自己、无距离）。"""
    state = _four_seats()

    expected = sorted(
        [
            (0,),
            (1,),
            (2,),
            (3,),
            (0, 1),
            (0, 2),
            (0, 3),
            (1, 2),
            (1, 3),
            (2, 3),
        ]
    )
    assert _chain_combos(state) == expected, (
        "铁索是 1–2 目标的普通锦囊：可指定自己、不受距离限制（4 人全在池里）"
    )


def test_iron_chain_never_offers_zero_three_duplicate_or_dead_targets():
    dead_state = _four_seats(dead=(3,))
    combos = _chain_combos(dead_state)

    assert combos, "至少还有 1 / 2 目标的合法组合"
    assert all(1 <= len(combo) <= 2 for combo in combos), "0 目标与 3 目标都不是候选"
    assert all(len(set(combo)) == len(combo) for combo in combos), "同一角色不能重复指定"
    assert all(3 not in combo for combo in combos), "死亡目标不进目标池"


def test_the_engine_rejects_zero_three_and_duplicate_targets():
    """成员判定就是合法性：0 / 3 / 重复目标的应答都不在候选里，提交即拒。"""
    state = _four_seats()
    engine = Engine.from_state(state, seed=1)

    player = _UseIronChain(targets=(1, 2), uses=0)  # 只用来跑到出牌阶段
    query = _advance(
        engine,
        _four_players(player),
        lambda engine: engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE,
    ).pending

    card = state.card(IRON_CHAIN[0])
    for targets in ((), (0, 1, 2), (1, 1)):
        with pytest.raises(IllegalActionError):
            engine.submit(UseCard(card=card, targets=targets, answers_seq=query.seq))


# ---------------------------------------------------------------------------
# 独立 toggle + chained_change delta + 投影
# ---------------------------------------------------------------------------


def _chain_engine(targets=(1, 2), *, chained=(2,), hand=(IRON_CHAIN[0],), uses=1):
    state = _four_seats(hand=hand, chained=chained)
    engine = Engine.from_state(state, seed=1)
    player = _UseIronChain(targets, uses=uses)
    _advance(
        engine,
        _four_players(player),
        lambda engine: player.used >= uses
        and not engine.stack
        and engine.pending is not None,
    )
    return engine


def test_iron_chain_toggles_each_target_independently():
    """未横置 → 横置、已横置 → 重置：同一次使用的两个目标各翻各的。"""
    engine = _chain_engine(targets=(1, 2), chained=(2,))

    changes = _deltas(engine, "chained_change")
    assert [(record.seat, record.chained) for record in changes] == [(1, True), (2, False)]
    assert engine.state.player(1).chained is True
    assert engine.state.player(2).chained is False
    assert engine.state.player(0).chained is False, "使用铁索本身不改变使用者的状态"


def test_chained_state_has_a_delta_and_shows_up_in_the_projection():
    engine = _chain_engine(targets=(1,), chained=())

    change = _deltas(engine, "chained_change")[0]
    assert (change.seat, change.chained) == (1, True)
    for viewer in (0, 1, 2, 3):
        assert project(engine.state, viewer).players[1].chained is True, (
            "横置是公开状态：任何视角的投影都读得到（delta 只是重建 / UI 用）"
        )


def test_iron_chain_is_an_ordinary_trick_with_a_nullify_window_and_no_use_limit():
    """普通锦囊：生效前收无懈（全体放弃后照常结算）；无次数限制 ⇒ 一回合两次都合法。"""
    engine = _chain_engine(
        targets=(1, 2), chained=(), hand=(IRON_CHAIN[0], IRON_CHAIN[1]), uses=2
    )

    nullify_windows = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.RESPOND
        and record.query.cause is WindowCause.NULLIFY
    ]
    assert len(nullify_windows) >= 2, "铁索开无懈窗口（每次使用至少一层）"
    assert [
        (record.seat, record.chained) for record in _deltas(engine, "chained_change")
    ] == [(1, True), (2, True), (1, False), (2, False)], (
        "无次数限制：第二次使用照常结算，两个目标各自翻回去"
    )
    assert engine.state.player(0).card_use_counts.get("iron_chain") == 2, (
        "次数只是记账（rated=None ⇒ 不设上限），不是闸门"
    )
    assert all(
        record.reason is not MoveReason.REFORGE for record in _deltas(engine, "card_move")
    ), "正常使用铁索不是重铸"


def test_chained_change_is_a_recorded_delta_that_round_trips_through_the_journal(tmp_path):
    """``chained_change`` 是记录流 schema 的一部分：dump → read 后仍是同一条 delta。"""
    engine = _chain_engine(targets=(1, 2), chained=(2,))

    path = tmp_path / "chain.jsonl"
    path.write_text(engine.journal(), encoding="utf-8")
    records = read_journal(path)

    changes = [
        (record.seat, record.chained)
        for record in records
        if record.kind == "delta" and record.delta_type == "chained_change"
    ]
    assert changes == [(1, True), (2, False)]
    seqs = [record.seq for record in records if record.kind != "header"]
    assert seqs == sorted(seqs)
