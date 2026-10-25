"""A1：洛神连续判定的显示层派生 `judgment_reveal`（只读投影测试）。

字段由 `pending_payload` 从**已有记录**派生（SKILL_ACTIVATION / SELECT_OPTION /
`card_move(reason=JUDGE)`），不改记录流、不改 `06`（§5「程序化约定与偏离一手」）、不碰金录像。这里用真引擎 + 真
`Session` 走完一次循环：继续询问带当前判定牌、同一 `chain_key` 稳定、结束/放弃后的
第一份 pending 是 `final=True` 的关窗帧（红牌终止时带红牌）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
for _entry in (_REPO_ROOT / "tools", _REPO_ROOT / "src"):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))

from sgs.state import Pass, QueryKind, SelectOption
from sgs_web.projection import pending_payload
from sgs_web.session import Session
from support import PassPlayer, run_until
from test_hero_judgment_loop import (
    BLACKS,
    PACK,
    REDS,
    REPEAT_PREFIX,
    _engine,
    _LuoshenPlayer,
)


def _session(engine) -> Session:
    session = Session(
        seed=7,
        player_count=2,
        ai="random",
        human_seat=0,
        hero_pool=None,
        timeout_seconds=None,
        pack=PACK,
    )
    session.engine = engine
    return session


def _to_continue(engine) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.SELECT_OPTION
        and str(engine.pending.skill_id).startswith(REPEAT_PREFIX)
    )


def _repeat(engine):
    return next(
        action
        for action in engine.pending.legal_actions
        if isinstance(action, SelectOption) and action.choice == "repeat"
    )


def test_pending_payload_exposes_a_stable_judgment_reveal_chain():
    """A1：继续询问 final=False 带本张判定牌；放弃后第一份 pending final=True。"""
    first, second, third = BLACKS[:3]
    red = REDS[0]
    filler = [
        index
        for index in range(len(PACK.deck.cards))
        if index not in {first, second, third, red}
    ][:2]
    engine = _engine((first, second, third, red, *filler))
    player = _LuoshenPlayer(repeats=1)
    session = _session(engine)

    run_until(engine, [player, PassPlayer()], _to_continue)
    first_payload = pending_payload(session)
    first_reveal = first_payload["judgment_reveal"]
    assert first_reveal is not None
    assert first_reveal["final"] is False
    assert first_reveal["card"]["card_id"] == first
    chain_key = first_reveal["chain_key"]

    engine.submit(_repeat(engine))
    run_until(engine, [player, PassPlayer()], _to_continue)
    second_reveal = pending_payload(session)["judgment_reveal"]
    assert second_reveal is not None
    assert second_reveal["chain_key"] == chain_key, "同一循环 chain_key 稳定"
    assert second_reveal["card"]["card_id"] == second
    assert second_reveal["final"] is False

    engine.submit(
        next(action for action in engine.pending.legal_actions if isinstance(action, Pass))
    )
    run_until(
        engine,
        [player, PassPlayer()],
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE,
    )
    final_reveal = pending_payload(session)["judgment_reveal"]
    assert final_reveal is not None
    assert final_reveal["final"] is True, "循环结束后的第一份 pending = 关窗帧"
    assert final_reveal["chain_key"] == chain_key
    assert final_reveal["card"]["card_id"] == second, "放弃时带最后一轮已判定的那张"


def test_pending_payload_final_frame_carries_the_red_judgment():
    """A1：循环被红牌终止时，关窗帧的 card = 最后那张红判定牌（不是上一张黑牌）。"""
    first, second = BLACKS[:2]
    red = REDS[0]
    filler = [
        index
        for index in range(len(PACK.deck.cards))
        if index not in {first, second, red}
    ][:2]
    engine = _engine((first, second, red, *filler))
    player = _LuoshenPlayer(repeats=2)
    session = _session(engine)

    run_until(engine, [player, PassPlayer()], _to_continue)
    chain_key = pending_payload(session)["judgment_reveal"]["chain_key"]
    for _ in range(2):
        engine.submit(_repeat(engine))
        run_until(
            engine,
            [player, PassPlayer()],
            lambda e: e.pending is not None
            and (
                e.pending.kind == QueryKind.PLAY_PHASE
                or (
                    e.pending.kind == QueryKind.SELECT_OPTION
                    and str(e.pending.skill_id).startswith(REPEAT_PREFIX)
                )
            ),
        )
    final = pending_payload(session)["judgment_reveal"]
    assert final is not None
    assert final["final"] is True
    assert final["chain_key"] == chain_key
    assert final["card"]["card_id"] == red, "红牌终止循环，关窗帧带红牌"
