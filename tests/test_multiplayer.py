"""4~8 人牌桌的随机整局可达性（票 23）。

落点 = 库门面（主缝）：``RuleConfig(player_count=…)`` + ``RandomPlayer`` 的多局终局。
本票的验收是"**五档人数各至少一局跑到终局**"，刻意与票 32 的分布判据 / 基准分开：
这里只保证可达性，不保证覆盖面。

⚠️ 此刻 4~8 人还是**无身份**牌桌（身份与胜负归票 24 / 25），故胜因恒
``last_surviving``、胜负只看存活数。身份胜负落地后，本文件的终局断言要跟着重写。
"""

from __future__ import annotations

import pytest

import sgs.agents as agents
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, RandomSource

PACK = load_content_pack()
SPEED_SEED = 7
"""固定种子：让"每档都收局"是确定性断言，失败可原样重跑（票 08）。"""


@pytest.mark.parametrize("count", [2, 4, 5, 6, 7, 8])
def test_every_supported_seat_count_reaches_game_end_under_the_random_driver(count):
    rule_config = RuleConfig(player_count=count)
    engine = Engine.new_game(seed=SPEED_SEED, rule_config=rule_config, pack=PACK)
    ai_stream = RandomSource(SPEED_SEED).substream("ai")

    engine.run([agents.RandomPlayer(ai_stream) for _ in range(count)])

    assert len(engine.state.players) == count
    assert engine.finished, f"{count} 人局必须收局"
    tail = engine.records[-1]
    assert tail.kind == "game_end", "终局记录必须是最后一条"
    assert tail.result == "winner"
    assert tail.reason == "last_surviving"
    assert tail.winner is not None
