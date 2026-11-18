"""第 300 回合结束 = 平局（用户 2026-11-04 裁定；huo-taishici-xunyu 之下的 `round-limit` 票 01）。

判据走**外部缝**：终局记录（`game_end` 的 `result` / `reason`）、事件流叙事、以及"没到上限的
轮次照常 +1"。构造局面把 ``round`` 直接放在边界上，不跑 300 个真回合。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for entry in (ROOT / "tools", ROOT / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine
from sgs.engine.game import ROUND_LIMIT
from sgs.state import Phase
from sgs_web.narrative import build_narrative

from support import PassPlayer, SeatSpec, build_state, run_queries

PACK = load_content_pack(deck_profile="basic_53")


def _round_limit_engine(*, round_no: int):
    """2 人无身份局：``round_no`` 轮、本轮起点 = 座位 0；座位 0 先动。"""
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=4, hp_max=4), SeatSpec(hp=4, hp_max=4)],
        deck=tuple(range(12, len(PACK.deck.cards))),
        turn_seat=0,
        phase=Phase.PREPARE,
    )
    state.rule_config = RuleConfig(player_count=2, starting_hand_size=4)
    engine = Engine.from_state(state, seed=3)
    # L0 派发会把 ``round`` 重置为 1（`_start`）：先让它跑完，再把轮次放到边界上。
    engine._step()
    engine.state.round = round_no
    engine.state.round_start_seat = 0
    engine.state.turn_seat = 0
    return engine


def _end_record(engine):
    ends = [record for record in engine.records if getattr(record, "kind", None) == "game_end"]
    assert len(ends) == 1, f"终局记录应恰一条：{[getattr(r, 'kind', None) for r in engine.records[-5:]]}"
    return ends[0]


def test_the_round_limit_ends_the_game_as_a_draw():
    """第 300 轮走完（指针回到本轮起点）⇒ 平局、reason = ``round_limit``。"""
    engine = _round_limit_engine(round_no=ROUND_LIMIT)
    run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: e.finished)

    end = _end_record(engine)
    assert end.result == "draw", "第 300 回合结束必须判平局"
    assert end.reason == "round_limit"
    assert end.winners == ()
    assert engine.state.round == ROUND_LIMIT, "轮次不再 +1（对局已结束）"


def test_a_normal_round_does_not_end_the_game():
    """没到上限：照常 +1，对局继续。"""
    engine = _round_limit_engine(round_no=ROUND_LIMIT - 1)
    run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: e.state.round == ROUND_LIMIT)

    assert not engine.finished
    assert engine.state.round == ROUND_LIMIT


def test_a_queued_extra_turn_is_dropped_when_the_round_limit_ends_the_game():
    """收兵那一刻排队的额外回合作废：终局记录后面不许再落 ``turn_change``。

    M7 票 08 把 PvE 的轮次判定接到同一个收兵点（轮次 +1 之后）时，顺手把这条旧路径的不变量
    钉住：``_advance_turn`` 在轮次推进途中看到 ``finished`` 就返回，不再弹额外回合、也不再
    发 ``turn_change``——否则 ``game_end`` 会被一条"额外回合开始"的记录顶掉，回放流里就出现
    了终局之后的回合。

    排队时机：额外回合只在**自然回合结束之后**弹出（`_pop_extra_turn` 在轮次推进之后），
    故要在"本轮最后一回合"（座位 1）进行中把它排上——那一回合结束时正好走满一圈。
    """
    engine = _round_limit_engine(round_no=ROUND_LIMIT)
    run_queries(
        engine,
        [PassPlayer(), PassPlayer()],
        lambda e: e.state.turn_seat == 1 and e.state.turn_extra is False,
    )
    engine.state.extra_turns.append(0)
    run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: e.finished)

    assert engine.records[-1].kind == "game_end", "终局记录必须是流里最后一条"
    assert engine.state.extra_turns == [0], "待执行的额外回合随终局作废（不弹出、不落记录）"
    assert engine.state.turn_extra is False


def test_the_round_limit_text_reaches_the_narrative():
    """事件流末行写「大战300回合不分胜负，鸣金收兵」。"""
    engine = _round_limit_engine(round_no=ROUND_LIMIT)
    run_queries(engine, [PassPlayer(), PassPlayer()], lambda e: e.finished)

    entries = build_narrative(engine.records, viewer=None, pack_id="standard")
    text = " ".join(entry.get("text", "") for entry in entries)
    assert "大战300回合不分胜负，鸣金收兵" in text
    assert "平局" in text
