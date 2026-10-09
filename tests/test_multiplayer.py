"""4~8 人牌桌的随机整局可达性（票 23 / 票 25）。

落点 = 库门面（主缝）：``RuleConfig(player_count=…)`` + ``RandomPlayer`` 的多局终局。
本票的验收是"**五档人数各至少一局跑到终局**"，刻意与票 32 的分布判据 / 基准分开：
这里只保证可达性，不保证覆盖面。

身份胜负落地后（票 25）：4~8 人是身份局，终局读 ``side`` + ``winners``（``winner`` 为
``None``）；2 人局是无身份局，形状仍是 M1 的单一 ``winner``。随机玩家会真的打死一方，
故每档都必达终局。
"""

from __future__ import annotations

import pytest

import sgs.agents as agents
from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Engine, RandomSource

PACK = load_content_pack()
SPEED_SEED = 7
"""固定种子：让"每档都收局"是确定性断言，失败可原样重跑（票 08）。"""

SIDE_IDENTITIES: dict[str, frozenset[Identity]] = {
    "lord": frozenset({Identity.LORD, Identity.LOYALIST}),
    "rebel": frozenset({Identity.REBEL}),
    "renegade": frozenset({Identity.RENEGADE}),
}


def _run(count: int) -> Engine:
    rule_config = RuleConfig(player_count=count)
    engine = Engine.new_game(seed=SPEED_SEED, rule_config=rule_config, pack=PACK)
    ai_stream = RandomSource(SPEED_SEED).substream("ai")
    engine.run([agents.RandomPlayer(ai_stream) for _ in range(count)])
    return engine


def test_a_two_player_game_still_ends_with_a_single_winner():
    """无身份局（2 人）保持 M1 的终局形状（票 25 的模式分支）。"""
    engine = _run(2)

    assert engine.finished
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert (tail.result, tail.side, tail.reason) == ("winner", None, "last_surviving")
    assert tail.winner is not None
    assert tail.winners == (tail.winner,), "形状统一：1v1 也填 winners"


@pytest.mark.parametrize("count", [4, 5, 6, 7, 8])
def test_every_identity_seat_count_reaches_game_end_with_a_side_and_winners(count):
    engine = _run(count)

    assert len(engine.state.players) == count
    assert engine.finished, f"{count} 人局必须收局"
    tail = engine.records[-1]
    assert tail.kind == "game_end", "终局记录必须是最后一条"
    assert (tail.result, tail.winner) == ("winner", None), "身份局没有单一获胜座位"
    assert tail.side in SIDE_IDENTITIES
    assert tail.winners, "获胜者列表非空"
    identities = [player.identity for player in engine.state.players]
    assert {identities[seat] for seat in tail.winners} <= SIDE_IDENTITIES[tail.side], (
        "获胜者列表 = 该侧全部座位（含阵亡者），与 side 一致"
    )
    expected = {
        seat
        for seat, identity in enumerate(identities)
        if identity in SIDE_IDENTITIES[tail.side]
    }
    assert set(tail.winners) == expected, "该侧座位一个不漏、按座位升序"
    assert list(tail.winners) == sorted(tail.winners)
