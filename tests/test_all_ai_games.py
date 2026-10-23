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

from sgs.agents import (
    HeuristicPlayer,
    TapePlayer,
    is_designation_use,
    is_structured_activation,
    tape_from_journal,
)
from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.engine import Engine, RandomSource

from support import ACCEPTANCE_SEEDS

PACK = load_content_pack(deck_profile="standard_108")
PACK_EX_160 = load_content_pack(deck_profile="standard_ex_160")
"""M5-01a：军争 160 张档只在**显式选档**时使用；下面的 108 基准组仍读默认口径。"""

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
    """包一层参与者：每次应答要么在候选集合内、要么是按清单填的复合动作。

    W4-4：预发动后复合动作不在 ``legal_actions`` 里（`spec.md` §3），由引擎的同一个
    校验器裁决；``is_structured_activation`` 只认"命中本次询问下发的清单"的那一类。
    W5-02b：借刀的复合使用同样不在 ``legal_actions`` 里，但必须是本次**附带指定池**
    （``query.designation_actions``）的成员——``is_designation_use`` 与引擎第四条路
    读同一份池。
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.answers = 0
        self.illegal = 0

    def respond(self, query):
        action = self._inner.respond(query)
        self.answers += 1
        if (
            action not in query.legal_actions
            and not is_structured_activation(query, action)
            and not is_designation_use(query, action)
        ):
            self.illegal += 1
        return action


def _new_engine(seed: int, *, pack=PACK) -> Engine:
    """与命令行同源的建局：5 人、标准版全表、**武将开启**（``hero_pool=()`` = 启用池）。"""
    return Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=pack,
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


MILITARY_SEEDS: tuple[int, ...] = ACCEPTANCE_SEEDS[:8]
"""M5-07 的 160 档全 AI 基准组：**8 个固定种子**（票面「至少 8 个」的最低线）。

取验收批 :data:`~support.ACCEPTANCE_SEEDS` 的前 8 个（136–143）：与 108 基准组**同批**，
故军争牌加进来后 108 / 160 两档读的是同一组局面；32 种子是选做（票面允许时间够再加，
本票按门禁预算只跑 8 个）。每局判定与 108 基准组**同四条**（跑完 / 身份胜负一致 /
零非法应答 / 可回放），见下面的测试。
"""


@pytest.mark.parametrize("seed", MILITARY_SEEDS)
def test_the_standard_ex_160_group_finishes_cleanly_and_replays(seed: int) -> None:
    """M5-07 / spec §6 A2：160 张档（108 + 军争 52）的全 AI 局，四条第据逐种子复核。

    判据与上面的 108 基准组同四条（跑完 / 终局与身份胜负一致 / 零非法应答 / 可回放）。
    M5-02–06 的军争规则已全部落地，故这也是"AI 会答新询问形状"的整局实测：重铸、
    铁索 1–2 目标、【酒】（出牌 / 濒死）、火攻的亮牌与同花色弃牌、兵粮的判定与跳过摸牌
    都在牌堆里，任何一条答不出候选都会被第 3 条判红。
    """
    engine = _new_engine(seed, pack=PACK_EX_160)
    scans = _participants(seed)

    engine.run(scans)

    assert engine.records[0].deck_profile == "standard_ex_160", "这局必须真的用了 160 档"
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end", "最后一条记录必须是 game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, seed)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0, "整局没有任何应答（断言不空转）"

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == seed and tape, "磁带必须带上种子与行动序列"
    replay = _new_engine(replayed_seed, pack=PACK_EX_160)
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M6 P0 票 01：三名标准魏将的**显式逐座位**全 AI 局（判据同上面四条）
# ---------------------------------------------------------------------------

M6_P0_HEROES: tuple[str, ...] = ("xiahoudun", "zhangliao", "xuchu", "caocao", "guojia")
"""票 01 的三名将 + 两名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流、不依赖启用池）。"""

M6_P0_SEED = ACCEPTANCE_SEEDS[0]
"""与 32 种子基准组同批的第一个种子（``ACCEPTANCE_SEEDS`` 的真源）。"""


def test_the_m6_p0_heroes_finish_a_full_ai_game_cleanly_and_replay():
    """验收：含【刚烈】/【突袭】/【裸衣】的全 AI 局跑完、胜负一致、零非法、可回放。"""
    engine = Engine.new_game(
        seed=M6_P0_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_P0_HEROES,
    )
    scans = _participants(M6_P0_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(M6_P0_HEROES)
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, M6_P0_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == M6_P0_SEED and tape
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_P0_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M6 P0 票 02：甄姬 / 张飞 / 黄月英的**显式逐座位**全 AI 局（判据同上面四条）
# ---------------------------------------------------------------------------

M6_P0_B_HEROES: tuple[str, ...] = (
    "zhenji",
    "zhangfei",
    "huangyueying",
    "caocao",
    "guojia",
)
"""票 02 的三名将 + 两名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流、不依赖启用池）。"""

M6_P0_B_SEED = ACCEPTANCE_SEEDS[1]
"""与 32 种子基准组同批的第二个种子（``ACCEPTANCE_SEEDS`` 的真源）。"""


def test_the_m6_p0_b_heroes_finish_a_full_ai_game_cleanly_and_replay():
    """验收：含【洛神】/【倾国】/【咆哮】/【集智】/【奇才】的全 AI 局四条判据全过。"""
    engine = Engine.new_game(
        seed=M6_P0_B_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_P0_B_HEROES,
    )
    scans = _participants(M6_P0_B_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(M6_P0_B_HEROES)
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, M6_P0_B_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == M6_P0_B_SEED and tape
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_P0_B_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"
