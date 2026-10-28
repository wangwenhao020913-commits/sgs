"""记录流 opt-in（票 11；spec §1.1 / §1.6）：``journal=False`` 只关"保留 / 追加"。

落点 = 引擎公开面：``Engine.new_game`` / ``Engine.from_state`` 的 ``journal`` 关键字，
``records`` / ``journal()`` / ``run()`` / ``state`` / ``pending_outcome``。
唯一读私有量的地方是序号对账——spec §1.1 把 ``_seq`` / ``_next_seq`` / ``_last_seq``
写进契约（关记录时仍照旧推进），公开面没有第二个观测点。
"""

from __future__ import annotations

import pytest

import sgs.agents as agents
from sgs.data import RuleConfig, load_content_pack
from sgs.engine import Engine, JournalRecord, ai_stream

PACK = load_content_pack(deck_profile="standard_108")
SEEDS = (136, 137, 138, 139)
"""票 11 §验收：2 人 + 5 人各至少 4 个种子。"""


def _play(
    seed: int, players: int, *, journal: bool | None = True
) -> tuple[Engine, tuple[JournalRecord, ...]]:
    """同一种子跑完一整局（启发式全座），返回引擎与 ``run()`` 的返回值。

    ``journal=None`` 表示**不传**这个关键字——用来钉"缺省 = 开记录"。
    """
    options = {} if journal is None else {"journal": journal}
    engine = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=players),
        pack=PACK,
        hero_pool=(),
        **options,
    )
    participants = [
        agents.HeuristicPlayer(ai_stream(seed, seat)) for seat in range(players)
    ]
    return engine, engine.run(participants)


@pytest.mark.parametrize("players", (2, 5))
def test_journal_off_reaches_the_same_state_and_outcome(players: int) -> None:
    """关记录不改变行为：同种子终局状态与判决逐字段相等（spec §1.1 / §1.6）。"""
    for seed in SEEDS:
        kept, _ = _play(seed, players, journal=True)
        dropped, _ = _play(seed, players, journal=False)

        assert dropped.state == kept.state, f"{players} 人种子 {seed} 的终局状态不同"
        assert dropped.pending_outcome == kept.pending_outcome, (
            f"{players} 人种子 {seed} 的终局判决不同"
        )


@pytest.mark.parametrize("players", (2, 5))
def test_journal_off_keeps_no_records_and_returns_nothing(players: int) -> None:
    """``records == ()``、``journal() == ""``、``run()`` 返回 ``()`。

    序号照旧推进：关记录那局的 ``_last_seq`` / ``_seq`` 与开记录那局**最后一条记录的
    ``seq``** 相同——``check_game_end`` 的序号对账与记录坐标读的是同一套逻辑序号。
    """
    for seed in SEEDS:
        kept, kept_returned = _play(seed, players, journal=True)
        dropped, dropped_returned = _play(seed, players, journal=False)

        assert dropped.records == ()
        assert dropped.journal() == ""
        assert dropped_returned == ()
        assert kept_returned == kept.records, "开记录时 run() 返回的就是记录流本身"

        assert kept.records[-1].seq == dropped._last_seq == dropped._seq, (
            f"{players} 人种子 {seed} 的记录序号没有照旧推进"
        )


def test_journal_defaults_to_on() -> None:
    """默认路径一字不变：``journal`` 缺省时照旧留记录，``journal()`` 就是记录流的文本。"""
    engine, returned = _play(136, 2, journal=None)

    assert engine.records
    assert engine.records[0].kind == "header"
    assert engine.journal().count("\n") == len(engine.records)
    assert returned == engine.records
    assert returned[-1].kind == "game_end"


def test_from_state_honours_journal_off() -> None:
    """``from_state`` 同姿态：低层入口也能关记录，且起点序号仍是"只有 header"的 0。"""
    source = Engine.new_game(
        seed=136, rule_config=RuleConfig(player_count=2), pack=PACK, journal=False
    )
    assert source.records == ()

    reborn = Engine.from_state(source.state, seed=136, journal=False)

    assert reborn.records == ()
    assert reborn.journal() == ""
    assert reborn._seq == reborn._last_seq == 0
