"""全 AI 局**基准组**（票 14 §9 / `spec.md` §Testing Decisions 第 2 条②）。

固定 **5 人**、**32 个种子**、**无人干预**，参与者是与产品同源的启发式档
（``--ai heuristic``；库侧 = :class:`~sgs.agents.HeuristicPlayer` + 同一个 ``ai`` 子流）。
判据四条，逐种子全查：

1. **跑完**：必达 ``game_end``（不超步数、不抛错）；
2. **终局与身份胜负一致**：读引擎全知态的身份，按 ``game_end.side`` / ``winners`` 复核
   （与 ``tests/test_fuzz.py::_check_endgame_identity_consistency`` 同一口径）；
3. **零非法应答**：每次应答都断言在候选集合内；
4. **可回放**：``tape_from_journal`` 抽种子 + 行动序列，``TapePlayer`` 重放后记录流逐字节相等。

这与 M2 的 "随机驱动 + 事后判据" 同一姿态：磁带冻结的是决策内容，判据只读记录流与终局态。
第 3 条在票 13 的 32 种子扫描里已单独钉过；这里把它并进"基准组"的四条判据，第 2 / 4 条
是本票新增的复核面。
"""

from __future__ import annotations

import pytest

from sgs.agents import HeuristicPlayer, TapePlayer, tape_from_journal
from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Engine, RandomSource

from support import ACCEPTANCE_SEEDS

PACK = load_content_pack(deck_profile="standard_108")

SEEDS: tuple[int, ...] = ACCEPTANCE_SEEDS
"""验收写死的 32 个种子（``python -m sgs --seed S --players 5 --hero-pool "" --ai heuristic``）。

与 ``test_heuristic_ai`` 的扫描 / 胜率对比**必须同批**，故真源收在
``support.ACCEPTANCE_SEEDS``（本轮【奸雄】修复后重新选种，理由见那里）。"""

PLAYERS = 5
"""固定 5 人（身份局：1 主 1 忠 2 反 1 内）。"""

SIDE_IDENTITIES: dict[str, frozenset[Identity]] = {
    "lord": frozenset({Identity.LORD, Identity.LOYALIST}),
    "rebel": frozenset({Identity.REBEL}),
    "renegade": frozenset({Identity.RENEGADE}),
}


class _IllegalAnswerScan:
    """包一层参与者：断言每次应答都在候选集合内（引擎侧也会拒，这里显式记一笔）。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.answers = 0
        self.illegal = 0

    def respond(self, query):
        action = self._inner.respond(query)
        self.answers += 1
        if action not in query.legal_actions:
            self.illegal += 1
        return action


def _new_engine(seed: int) -> Engine:
    """与命令行同源的建局：5 人、标准版全表、**武将开启**（``hero_pool=()`` = 启用池）。"""
    return Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        hero_pool=(),
    )


def _participants(seed: int) -> list[_IllegalAnswerScan]:
    """五个座位共用同一个 ``ai`` 子流（与 ``cli.py`` 的 ``_player`` 一处口径）。"""
    stream = RandomSource(seed).substream("ai")
    return [_IllegalAnswerScan(HeuristicPlayer(stream)) for _ in range(PLAYERS)]


def _fail(seed: int, message: str) -> None:
    raise AssertionError(f"全 AI 局基准组（seed={seed}, n={PLAYERS}）：{message}")


def _assert_endgame_identity_consistency(engine: Engine, seed: int) -> None:
    """判据②：终局记录与身份胜负一致（读引擎全知态，与 fuzz 同一口径）。"""
    tail = engine.records[-1]
    identities = [player.identity for player in engine.state.players]
    if tail.result == "draw":
        if tail.side is not None or tail.winners:
            _fail(seed, "平局不得有 side / winners")
        return
    if tail.side not in SIDE_IDENTITIES:
        _fail(seed, f"胜局的 side 必须是三条之一，实得 {tail.side!r}")
    expected = {
        seat
        for seat, identity in enumerate(identities)
        if identity in SIDE_IDENTITIES[tail.side]
    }
    if set(tail.winners) != expected:
        _fail(seed, f"winners 必须恰为该侧全部座位：期望 {sorted(expected)}，实得 {list(tail.winners)}")
    lord_seat = next(
        player.seat for player in engine.state.players if player.identity is Identity.LORD
    )
    if tail.side == "rebel" and engine.state.players[lord_seat].alive:
        _fail(seed, "side=rebel 必须以主公阵亡为前提")
    if tail.side == "renegade" and engine.state.players[lord_seat].alive:
        _fail(seed, "side=renegade 必须以主公阵亡为前提")


@pytest.mark.parametrize("seed", SEEDS)
def test_the_all_ai_heuristic_group_finishes_consistently_and_replays(seed: int) -> None:
    """四条判据逐种子复核（票 14 §9 与 §验收 2）。"""
    engine = _new_engine(seed)
    scans = _participants(seed)

    engine.run(scans)

    # ① 跑完
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end", "最后一条记录必须是 game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    # ② 终局与身份胜负一致
    _assert_endgame_identity_consistency(engine, seed)
    # ③ 零非法应答
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0, "整局没有任何应答（断言不空转）"

    # ④ 可回放
    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == seed and tape, "磁带必须带上种子与行动序列"
    replay = _new_engine(replayed_seed)
    player = TapePlayer(tape)
    replay.run([player] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"
