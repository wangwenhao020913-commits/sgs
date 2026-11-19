"""全 AI 局**基准组**（票 14 §9 / `spec.md` §Testing Decisions 第 2 条②）。

固定 **5 人**、**32 个种子**、**无人干预**，参与者是与产品同源的启发式档
（``--ai heuristic``；库侧 = :class:`~sgs.agents.HeuristicPlayer` + 每座位 ``ai:<seat>`` 子流）。
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

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from sgs.agents import (
    HeuristicPlayer,
    TapePlayer,
    is_designation_use,
    is_structural_answer,
    is_structured_activation,
    tape_from_journal,
)
from sgs.data import (
    Identity,
    Mode,
    PveConfig,
    PveVictory,
    PveVictoryKind,
    RuleConfig,
    enabled_hero_ids,
    hero_by_id,
    load_content_pack,
)
from sgs.engine import Engine, ai_stream, read_journal, solve_pve_outcome
from sgs.state import Action, Pass, Query, UseCard

from support import ACCEPTANCE_SEEDS

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from sgs_batch import BatchResult, run_games  # noqa: E402 - 测试在 import 前把 tools/ 放上 sys.path

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
    yijiang-2014-round2 票 21：不枚举档多选（【秉壹】的座位面）的答案由 AI 自己拼、不在
    ``legal_actions`` 里；``is_structural_answer`` 与引擎 ``submit`` 那道闸同一口径
    （``is_legal_option_set`` 形状谓词），故放行它不放松判据。
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
            and not is_structural_answer(query, action)
        ):
            self.illegal += 1
        return action


def _new_engine(seed: int, *, pack=PACK, players: int = PLAYERS) -> Engine:
    """与命令行同源的建局：默认 5 人、标准版全表、**武将开启**（``hero_pool=()`` = 启用池）。"""
    return Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=players),
        pack=pack,
        hero_pool=(),
    )


def _participants(seed: int, players: int = PLAYERS) -> list[_IllegalAnswerScan]:
    """每座位一条 ``ai:<seat>`` 子流（与 ``cli.py`` 的 ``_player`` 一处口径，票 12）。"""
    return [
        _IllegalAnswerScan(HeuristicPlayer(ai_stream(seed, seat)))
        for seat in range(players)
    ]


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


# ---------------------------------------------------------------------------
# M6 P0 票 03（P0 收口）：甘宁 / 吕蒙 / 黄盖的**显式逐座位**全 AI 局（判据同上面四条）
# ---------------------------------------------------------------------------

M6_P0_C_HEROES: tuple[str, ...] = (
    "ganning",
    "lvmeng",
    "huanggai",
    "caocao",
    "guojia",
)
"""票 03 的三名将 + 两名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流、不依赖启用池）。"""

M6_P0_C_SEED = ACCEPTANCE_SEEDS[2]
"""与 32 种子基准组同批的第三个种子（``ACCEPTANCE_SEEDS`` 的真源）。"""


def test_the_m6_p0_c_heroes_finish_a_full_ai_game_cleanly_and_replay():
    """验收：含【奇袭】/【克己】/【苦肉】的全 AI 局四条判据全过（P0 收口）。"""
    engine = Engine.new_game(
        seed=M6_P0_C_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_P0_C_HEROES,
    )
    scans = _participants(M6_P0_C_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(M6_P0_C_HEROES)
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, M6_P0_C_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == M6_P0_C_SEED and tape
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_P0_C_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M6 票 14：界·魏 A 四名（+ 界关羽陪跑）的**显式逐座位**全 AI 局（判据同上面四条）
# ---------------------------------------------------------------------------

M6_JIE_WEI_HEROES: tuple[str, ...] = (
    "jie_caocao",
    "jie_simayi",
    "jie_zhangliao",
    "jie_xuchu",
    "jie_guanyu",
)
"""票 14 的四名将 + 一名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流）。

五名**真名互异**（曹操 / 司马懿 / 张辽 / 许褚 / 关羽）：同一真名不共存是引擎不变量，
显式发放也不得违反它。
"""

M6_JIE_WEI_SEED = ACCEPTANCE_SEEDS[3]
"""与 32 种子基准组同批的第四个种子（``ACCEPTANCE_SEEDS`` 的真源）。"""


def test_the_m6_jie_wei_heroes_finish_a_full_ai_game_cleanly_and_replay():
    """验收：含界【奸雄】/【护驾】/【反馈】/【鬼才】/【突袭】/【裸衣】的全 AI 局四条判据全过。"""
    engine = Engine.new_game(
        seed=M6_JIE_WEI_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_WEI_HEROES,
    )
    scans = _participants(M6_JIE_WEI_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(M6_JIE_WEI_HEROES)
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, M6_JIE_WEI_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == M6_JIE_WEI_SEED and tape
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_WEI_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M6 票 15：界·魏 B（界郭嘉 / 界李典 + 三名陪跑）的**显式逐座位**全 AI 局
# ---------------------------------------------------------------------------

M6_JIE_WEI_B_HEROES: tuple[str, ...] = (
    "jie_guojia",
    "jie_lidian",
    "jie_caocao",
    "jie_simayi",
    "jie_zhangliao",
)
"""票 15 的两名将 + 三名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流）。

五名**真名互异**（郭嘉 / 李典 / 曹操 / 司马懿 / 张辽）：同一真名不共存是引擎不变量，
显式发放也不得违反它。
"""

M6_JIE_WEI_B_SEED = ACCEPTANCE_SEEDS[3]
"""与 32 种子基准组同批的验收种子（``ACCEPTANCE_SEEDS`` 的真源）。

票 12 切每座位 ``ai:<seat>`` 子流后，原来的 ``ACCEPTANCE_SEEDS[4]``（140）这一局不再
发动界【天妒】——"四条技能都真的发动过"是这条验收的本体，不能降成"将发了但没触发"。
按本模块纪律在同一验收批里**重选种子**为 137（四条全命中），判据不放宽。

票 18（D1 无懈目标化）又改了无懈窗口的宣 / 不宣，137 因此不再发动界【天妒】：同一纪律
**再**重选为 138（仍是批内种子，四条全命中）。一次真实的 AI 决策面改动会换掉整局的分叉
点，这类"显式逐座位全 AI 局"要重扫批内种子，而不是放宽"四条都要发动"的判据。

票 23（C2 记牌批 2）第三次改打分面（锦囊 / 斩杀按期望计价），138 因此不再发动界【天妒】：
照同一条纪律**再**重选为 139（仍是批内种子；扫描器 = ``.scratch/ai-light/seed_scan_t23.py``，
逐局核四条技能是否都发动，判据不放宽）。138 与 139 各是好几种判据的载体，撞档不影响
——两条用例的将面 / 断言不同，各自只读自己的那一个种子。
"""


def test_the_m6_jie_wei_b_heroes_finish_a_full_ai_game_cleanly_and_replay():
    """验收：含界【天妒】/【遗计】/【恂恂】/【忘隙】的全 AI 局四条判据全过。

    回放判据同时覆盖票 15 的"【恂恂】的置顶 / 置底在回放中一致"：界李典的排列是
    ``ArrangeCards`` 行动（只带面内位置），重放后记录流逐字节相等即置顶 / 置底一致。
    本局里四条新技能都**真的发动过**（下面断言），故"含其中一名的全 AI 局"这一条验收
    不是"将发了但没触发"的空转。
    """
    engine = Engine.new_game(
        seed=M6_JIE_WEI_B_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_WEI_B_HEROES,
    )
    scans = _participants(M6_JIE_WEI_B_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(M6_JIE_WEI_B_HEROES)
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, M6_JIE_WEI_B_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0

    activated = {
        record.action.skill_id
        for record in engine.records
        if record.kind == "action"
        and record.accepted
        and getattr(record.action, "skill_id", None)
    }
    for skill_id in (
        "hero.jie_guojia.tiandu",
        "hero.jie_guojia.yiji",
        "hero.jie_lidian.xunxun",
        "hero.jie_lidian.wangxi",
    ):
        assert skill_id in activated, f"本局没有发动 {skill_id}（AI 覆盖落空）"

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == M6_JIE_WEI_B_SEED and tape
    assert any(getattr(action, "kind", None) == "ArrangeCards" for action in tape), (
        "【恂恂】的排列必须在回放磁带里（置顶 / 置底一致性的载体）"
    )
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_WEI_B_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M6 票 16：界·蜀（界刘备 / 界赵云 / 界诸葛亮 + 两名陪跑）的**显式逐座位**全 AI 局
# ---------------------------------------------------------------------------

M6_JIE_SHU_HEROES: tuple[str, ...] = (
    "jie_liubei",
    "jie_zhaoyun",
    "jie_zhugeliang",
    "jie_caocao",
    "sunquan",
)
"""票 16 的三名将 + 两名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流）。

五名**真名互异**（刘备 / 赵云 / 诸葛亮 / 曹操 / 孙权）：同一真名不共存是引擎不变量，
显式发放也不得违反它。
"""

M6_JIE_SHU_SEED = ACCEPTANCE_SEEDS[5]
"""与 32 种子基准组同批的第六个种子（``ACCEPTANCE_SEEDS`` 的真源）。"""


def test_the_m6_jie_shu_heroes_finish_a_full_ai_game_cleanly_and_replay():
    """验收：含界【仁德】/【激将】/【龙胆】/【涯角】/【观星】/【空城】的全 AI 局四条判据全过。

    【激将】是主公技，本局谁当主公由种子决定，故不要求它一定发动（D16 的逐技能覆盖由
    ``tests/test_heuristic_ai.py`` 的确定性场景负责）；这里钉的是本票验收的那一条——
    含界·蜀武将的整局跑完、终局与身份胜负一致、零非法、可回放。
    """
    engine = Engine.new_game(
        seed=M6_JIE_SHU_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_SHU_HEROES,
    )
    scans = _participants(M6_JIE_SHU_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(M6_JIE_SHU_HEROES)
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, M6_JIE_SHU_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0

    activated = {
        record.action.skill_id
        for record in engine.records
        if record.kind == "action"
        and record.accepted
        and getattr(record.action, "skill_id", None)
    }
    assert activated & {
        "hero.jie_liubei.rende",
        "hero.jie_liubei.jijiang",
        "hero.jie_zhaoyun.longdan",
        "hero.jie_zhaoyun.yajiao",
        "hero.jie_zhugeliang.guanxing",
    }, f"本局一条界·蜀技能都没发动（AI 覆盖落空）：{sorted(activated)}"

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == M6_JIE_SHU_SEED and tape
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_SHU_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M6 票 17：界·吴（界孙权 / 界黄盖 / 界大乔 / 界孙尚香 + 一名陪跑）的**显式逐座位**全 AI 局
# ---------------------------------------------------------------------------

M6_JIE_WU_HEROES: tuple[str, ...] = (
    "jie_sunquan",
    "jie_huanggai",
    "jie_daqiao",
    "jie_sunshangxiang",
    "jie_caocao",
)
"""票 17 的四名将 + 一名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流）。

五名**真名互异**（孙权 / 黄盖 / 大乔 / 孙尚香 / 曹操）：同一真名不共存是引擎不变量，
显式发放也不得违反它。四名界·吴武将同时上场也顺带覆盖"同一势力多人"的情形。
"""

M6_JIE_WU_SEED = ACCEPTANCE_SEEDS[6]
"""与 32 种子基准组同批的第七个种子（``ACCEPTANCE_SEEDS`` 的真源）。"""


def test_the_m6_jie_wu_heroes_finish_a_full_ai_game_cleanly_and_replay():
    """验收：含界·吴四条武将技能的全 AI 局四条判据全过（跑完 / 胜负一致 / 零非法 / 可回放）。

    【救援】是主公技，本局谁当主公由种子决定，故不要求它一定发动（D16 的逐技能覆盖由
    ``tests/test_heuristic_ai.py`` 的确定性场景负责）；这里钉的是票面验收的那一条——
    含界·吴武将的整局跑完、终局与身份胜负一致、零非法、可回放。
    """
    engine = Engine.new_game(
        seed=M6_JIE_WU_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_WU_HEROES,
    )
    scans = _participants(M6_JIE_WU_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(M6_JIE_WU_HEROES)
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, M6_JIE_WU_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0

    activated = {
        record.action.skill_id
        for record in engine.records
        if record.kind == "action"
        and record.accepted
        and getattr(record.action, "skill_id", None)
    }
    assert activated & {
        "hero.jie_sunquan.zhiheng",
        "hero.jie_sunquan.jiuyuan",
        "hero.jie_huanggai.kurou",
        "hero.jie_daqiao.guose",
        "hero.jie_daqiao.liuli",
        "hero.jie_sunshangxiang.jieyin",
        "hero.jie_sunshangxiang.xiaoji",
    }, f"本局一条界·吴技能都没发动（AI 覆盖落空）：{sorted(activated)}"

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == M6_JIE_WU_SEED and tape
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_WU_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M6 票 18：界·群（华佗 / 吕布 / 华雄 / 公孙瓒）的整局 AI 基准
# ---------------------------------------------------------------------------

M6_JIE_QUN_HEROES: tuple[str, ...] = (
    "jie_huatuo",
    "jie_lvbu",
    "jie_huaxiong",
    "jie_gongsunzan",
    "jie_sunquan",
)
"""票 18 的四名将 + 一名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流）。

五名**真名互异**（华佗 / 吕布 / 华雄 / 公孙瓒 / 孙权）：同一真名不共存是引擎不变量，
显式发放也不得违反它。华雄与公孙瓒在本局是**只有界版**的真名（版本池收口到 54 后
仍各只有一条记录）。
"""

M6_JIE_QUN_SEED = ACCEPTANCE_SEEDS[7]
"""与 32 种子基准组同批的第八个种子（``ACCEPTANCE_SEEDS`` 的真源）。"""


def test_the_m6_jie_qun_heroes_finish_a_full_ai_game_cleanly_and_replay():
    """验收：含界·群四条武将技能的全 AI 局四条判据全过（跑完 / 胜负一致 / 零非法 / 可回放）。

    本局只钉"含界·群武将的整局跑完、终局与身份胜负一致、零非法、可回放"；D16 的逐技能
    覆盖由 ``tests/test_heuristic_ai.py`` 的确定性场景负责（【无双】【耀武】【义从】是
    锁定技，由构造局面接手）。
    """
    engine = Engine.new_game(
        seed=M6_JIE_QUN_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_QUN_HEROES,
    )
    scans = _participants(M6_JIE_QUN_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(M6_JIE_QUN_HEROES)
    assert engine.finished, "必达终局"
    tail = engine.records[-1]
    assert tail.kind == "game_end"
    assert tail.result in ("winner", "draw"), f"终局结果非法：{tail.result!r}"
    _assert_endgame_identity_consistency(engine, M6_JIE_QUN_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"
    assert sum(scan.answers for scan in scans) > 0

    activated = {
        record.action.skill_id
        for record in engine.records
        if record.kind == "action"
        and record.accepted
        and getattr(record.action, "skill_id", None)
    }
    assert activated & {
        "hero.jie_huatuo.jijiu",
        "hero.jie_huatuo.chuli",
        "hero.jie_lvbu.liyu",
        "hero.jie_huaxiong.yaowu",
        "hero.jie_huaxiong.shizhan",
        "hero.jie_gongsunzan.qiaomeng",
    }, f"本局一条界·群技能都没发动（AI 覆盖落空）：{sorted(activated)}"

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == M6_JIE_QUN_SEED and tape
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=M6_JIE_QUN_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M6 票 22（收口 / spec P2-2）：**8 个固定种子**的 8 人**混合池**全 AI 局
# ---------------------------------------------------------------------------

M6_CLOSEOUT_SEEDS: tuple[int, ...] = (148, 137, 146, 145, 141, 164, 142, 143)
"""spec P2-2 的 **8 个固定种子**（票 22 收口批；原取 32 种子验收批的前 8 个）。

写死而不是从 :data:`~support.ACCEPTANCE_SEEDS` 切片：种子批本身是验收内容，
换批必须同时改票面 ``## Answer`` 与 ``acceptance-map.md`` 的 P2-2 行。

**2026-11-13 换 1 个种子**（shan-baozuoci 票 04 把默认池加到 103 / 68）：种子 140 在新池下
抽到的 8 人局**没有标准版**（`{lin-mobile, shan, jie-yijiang-2014-mobile, jie-ol, huo-mobile}`）
⇒ 同一条纪律换种不换判据：140 → 141（含标准版 + 4 个版本标记，四条判据全过）。

**2026-11-12 换 1 个种子**（sp5-mayunlu-panjun 票 01 / 02 把默认池加到 100 / 66）：
种子 149 在新池下抽到的 8 人局**没有标准版**（`{jie-ol, huo, lin-mobile, jie-mobile, feng}`）
⇒ 按同一条纪律换种不换判据：149 → 164（含标准版 + sp5，混合池且四条判据全过）。

**2026-11-09 换 2 个种子**（jie-yijiang-2014-mobile 票 01 / 02 把默认池加到 95 / 64）：
旧的 141 / 147 抽到的 8 人局**没有标准版**（判据是"标准版在场 + ≥2 个版本标记"），
按同一条纪律换种不换判据 ⇒ 141 → 149、147 → 148（两个都过同一条判据，见下）。

**2026-11-01 换 1 个种子**（`feng-yuji` 票 01 加风包移动版于吉 ⇒ 默认池 62 / 36）：
原种子 139 在新池下抽到的 8 名全是 `feng` / `feng-mobile` / `jie-mobile` / `jie-ol`，
**没有标准版** ⇒ 违反本测试自己的"混合池"判据（判据未放宽）。按同一判据在 32 种子验收批
里从 144 起重扫，**144** 是首个命中（`feng` / `feng-mobile` / `jie-mobile` / `jie-ol` /
`standard` 五版齐、8 名互异、零非法应答、跑完）。替换只换种子，不换判据。

**2026-11-05 再换 1 个种子**（`lei-batch` 票 02 加雷包张绣 ⇒ 默认池 74 / 48；
票 03 加雷包诸葛瞻 ⇒ 75 / 49）：原种子 144 在新池下抽到的 8 名全是扩展包武将
（`jie-ol` / `jie-mobile` / `feng` / `huo` / `huo-mobile` / `lei`），**没有标准版**
⇒ 再次违反同一条"混合池"判据（判据未放宽，见本用例的严格断言）。按同一判据从 144 起
重扫，**145** 是首个命中（`standard` / `feng` / `feng-mobile` / `huo-mobile` / `jie-ol`
五版齐、8 名互异、零非法应答、跑完）。替换只换种子，不换判据。

**2026-11-06 再换 1 个种子**（`huo-wolong-yanliang` 票 01 加火包 OL 卧龙诸葛 ⇒ 默认池
79 / 50）：原种子 138 在新池下抽到的 8 名全是扩展包武将（`huo` / `huo-mobile` /
`feng-mobile` / `jie-ol` / `lei`），**没有标准版** ⇒ 再次违反同一条"混合池"判据
（判据未放宽）。按同一判据重扫 32 种子验收批（144 / 154 / 166 也各有一项不合格），
**146** 是首个命中（8 名互异、零非法应答、跑完）。替换只换种子，不换判据。

**2026-11-08 再换 1 个种子**（`lin-batch` 票 02 加林包移动版孙坚 ⇒ 默认池 85 / 56）：
原种子 136 在新池下抽到的 8 名是 `homebrew` / `jie-ol` / `feng` / `huo` / `lei` /
`lin-mobile`，**没有标准版** ⇒ 再次违反同一条"混合池"判据（判据未放宽）。按同一判据从
144 起重扫，144 在本池下同样不合格（无标准版），**147** 是首个命中（`huo-mobile` /
`jie-feng-mobile` / `jie-ol` / `lin-mobile` / `standard` 五版齐、8 名互异、零非法应答、
跑完、终局与身份一致）。替换只换种子，不换判据。
"""

M6_CLOSEOUT_PLAYERS = 8
"""8 人身份局（1 主 / 2 忠 / 4 反 / 1 内）：与票 20 A2 的人类混合池 e2e 同一个座位数。"""


def test_the_m6_closeout_batch_is_eight_fixed_seeds() -> None:
    """本批恰好 8 个**互异**的固定种子，且与 32 种子基准组同批（不偷偷换种）。"""
    assert len(M6_CLOSEOUT_SEEDS) == 8
    assert len(set(M6_CLOSEOUT_SEEDS)) == 8
    assert set(M6_CLOSEOUT_SEEDS) <= set(ACCEPTANCE_SEEDS)


@pytest.mark.parametrize("seed", M6_CLOSEOUT_SEEDS)
def test_the_m6_mixed_pool_closeout_seeds_finish_cleanly_and_replay(seed: int) -> None:
    """spec P2-2：8 个固定种子的混合池全 AI 局，逐局四条判据（票 22）。

    1. **跑完**（必达 ``game_end``）；
    2. **终局与身份胜负一致**（与基准组同一个复核面：读引擎全知态的身份）；
    3. **零非法应答**（每次应答都在候选 / 清单 / 附带指定池里）；
    4. **可回放**（磁带重放后记录流逐字节相等）。

    池 = **默认启用池**（标准 25 + 界版 29 + 移动版 1 + 风包 3 + 风包移动版 3 +
    自制包 1 = 62 个版本 / 36 个真名；feng-zhangjiao 票 01 再加张角移动版 ⇒ 63 / 37；
    huo-dianwei-mobile 票 01 再加火包移动版典韦 ⇒ 64 / 38），不显式
    发将、不点将、不设禁将。逐局另外断言真的从混合池里发牌：8 名武将都在池内、8 个真名
    互异（同一真名至多一个座位）、且标准版与界版**两版都在场**——否则"混合池"这一条
    可以被一局全标准版的空转局糊弄过去。
    """
    pool = enabled_hero_ids()
    true_names = {hero_by_id(hero_id).true_name for hero_id in pool}
    # sp5-pangtong-liuqi：SP庞统（真名已在池）+ 刘琦（新真名）⇒ 102 / 67；
    # shan-baozuoci 票 04：山包·左慈（新真名）⇒ 103 / 68。
    # shiji-zhi 票 01：始计篇-智包·神郭嘉（新真名）⇒ 104 / 69；
    # shiji-zhi-luotong-chenzhen 票 02：陈震（新真名）⇒ 105 / 70。
    # shiji-zhi-luotong-chenzhen 票 01：骆统（新真名）⇒ 106 / 71。
    # bingshi-jie 票 01：兵势篇-节包·势赵云（真名「赵云」已在池里）⇒ 版本 111 → 112、
    # 真名分组 75 不变。
    assert (len(pool), len(true_names)) == (115, 76), "spec D18/D19 的收口池（票 03 加风包黄忠 1 个；票 08 加神关羽移动版；票 01 加神吕蒙移动版；票 01（自制包）加韬·刘备自制版；feng-weiyan 票 01 加风包魏延 1 个；feng-xiahouyuan 票 01 加风包夏侯渊 1 个；feng-zhangjiao 票 01 加张角移动版 1 个；huo-dianwei-mobile 票 01 加火包移动版典韦 1 个；huo-mobile-batch 票 01 加火包移动版庞德 1 个；huo-mobile-batch 票 02 加火包移动版庞统 1 个；huo-mobile-batch 票 03 加火包移动版袁绍 1 个；huo-shenzhouyu-mobile 票 01 加火包移动版神周瑜 1 个；wuming 票 01 加无名自制版 1 个 ⇒ 69 / 43（新真名「无名」⇒ 真名数 +1）；huo-shenzhugeliang 票 01 加火包 OL 神诸葛亮 1 个 ⇒ 70 / 44（新真名「神诸葛亮」）；lei-batch 票 01 加雷包郝昭 1 个 ⇒ 73 / 47（新真名「郝昭」）；lei-batch 票 02 加雷包张绣 1 个 ⇒ 74 / 48（新真名「张绣」）；tao-caocao 票 01 加自制包韬·曹操自制版 1 个 ⇒ 76 / 49（真名「曹操」已在池里）；huo-wolong-yanliang 票 01 加火包 OL 卧龙诸葛 1 个 ⇒ 79 / 50（新真名「卧龙诸葛」）；票 02 加火包 OL 颜良文丑 1 个 ⇒ 80 / 51（新真名「颜良文丑」）；feng-yujin 票 01 加自制包锋·于禁 1 个 ⇒ 82 / 53（新真名「于禁」）；lin-batch 票 01 加林包移动版神曹操 1 个 ⇒ 84 / 55（新真名「神曹操」）；lin-batch 票 02 加林包移动版孙坚 1 个 ⇒ 85 / 56（新真名「孙坚」）；lin-batch 票 03 加林包移动版徐晃 1 个 ⇒ 86 / 57（新真名「徐晃」）；lin-batch2 票 01 加林包移动版曹丕 1 个 ⇒ 87 / 58（新真名「曹丕」）；lin-batch2 票 02 加林包移动版董卓 1 个 ⇒ 88 / 59（新真名「董卓」）；lin-batch2 票 03 加林包移动版贾诩 1 个 ⇒ 89 / 60（新真名「贾诩」）；jushou-lusu 票 01 加一将成名-2014 沮授 1 个 ⇒ 90 / 61（新真名「沮授」）；票 02 加林包移动版鲁肃 1 个 ⇒ 91 / 62（新真名「鲁肃」）；jie-feng-batch2 票 01 / 02 加界夏侯渊移动版 / 界张角移动版 2 个 ⇒ 93 / 62（真名均已在池里）；jie-yijiang-2014-mobile 票 01 加界陈群移动版 1 个 ⇒ 94 / 63（新真名「陈群」）；票 02 加界顾雍移动版 1 个 ⇒ 95 / 64（新真名「顾雍」）；yijiang-2014 票 02 加陈群 1 个 ⇒ 96 / 64；票 03 加顾雍 1 个 ⇒ 97 / 64；yijiang-2014-round2 票 01 加界沮授移动版 1 个 ⇒ 98 / 64）"

    engine = _new_engine(seed, players=M6_CLOSEOUT_PLAYERS)
    scans = _participants(seed, M6_CLOSEOUT_PLAYERS)
    engine.run(scans)

    heroes = [player.hero for player in engine.state.players]
    versions = {hero_by_id(hero_id).version for hero_id in heroes}
    assert len(heroes) == M6_CLOSEOUT_PLAYERS
    assert set(heroes) <= set(pool), f"发出了启用池外的武将：{sorted(set(heroes) - set(pool))}"
    assert len(set(heroes)) == M6_CLOSEOUT_PLAYERS
    assert (
        len({hero_by_id(hero_id).true_name for hero_id in heroes}) == M6_CLOSEOUT_PLAYERS
    ), "同一真名占了两个座位"
    # 池里有九个版本标记（standard / jie-ol / jie-mobile / feng / feng-mobile / huo /
    # huo-mobile / homebrew / lei）：判据是**这一局不是单包**（≥2 个版本标记）**且标准版
    # 在场**——池每加一名武将都会重排 RNG，"必须抽到某个界版"不写死（huo-shenzhugeliang
    # 加火包 OL 后 seed 140 正是这种局面）；"标准 + 界并存"由
    # `test_the_default_enabled_pool_is_the_union_of_standard_and_jie_versions` 在**池**
    # 那一层钉住，本用例只钉"发出来的一局是混合的"。
    # lei-batch 票 02 把池加到 74（票 03 ⇒ 75）之后，旧种子 144 抽到一局全扩展包
    # （jie / feng / huo / lei）——**没有标准版**。按 §5 换种子（144 → 145），判据恢复为
    # "标准版必在场"：判据不许放宽，种子可以换。
    assert "standard" in versions and len(versions) >= 2, (
        f"这一局不是混合池：{versions}"
    )

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
    replay = _new_engine(replayed_seed, players=M6_CLOSEOUT_PLAYERS)
    replay.run([TapePlayer(tape)] * M6_CLOSEOUT_PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# wuming 票 01：无名自制版的**【天成】全 AI 局**（L0 三次展示的全自动应答）
# ---------------------------------------------------------------------------

WUMING_HEROES: tuple[str, ...] = (
    "wuming",
    "caocao",
    "liubei",
    "sunquan",
    "guojia",
)
"""无名自制版 + 四名陪跑（5 人身份局；显式发放，不吃 ``hero`` 子流、不依赖启用池）。"""

WUMING_AI_SEED = ACCEPTANCE_SEEDS[2]
"""与 32 种子基准组同批的第三个种子（``ACCEPTANCE_SEEDS`` 的真源）。"""

TIANCHENG = "hero.wuming.tiancheng"


def test_wuming_tiancheng_fires_through_a_full_ai_game_and_replays():
    """【天成】的 L0 三次展示在**全 AI 局**里由启发式应答：跑完 / 胜负一致 / 零非法 / 可回放。

    L0 的三问走的是既有的 ``SELECT_OPTION`` 面（票 07 的展示载荷），故 AI 一侧**不需要**
    新分支；本用例钉的是"真有人答得出来、而且答完游戏照常跑完"：三个 ``reveal`` 询问
    各出现一次、置灰行从不出现在候选里、三次展示之后第一回合照常开始。
    """
    engine = Engine.new_game(
        seed=WUMING_AI_SEED,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=WUMING_HEROES,
    )
    scans = _participants(WUMING_AI_SEED)

    engine.run(scans)

    assert [player.hero for player in engine.state.players] == list(WUMING_HEROES)
    reveals = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and str(getattr(record.query, "skill_id", "") or "").startswith(TIANCHENG)
    ]
    assert len(reveals) == 3, f"【天成】展示恒为 3 次，实得 {len(reveals)}"
    assert [query.hero_card.total for query in reveals] == [3, 3, 3]
    for query in reveals:
        disabled = set(query.disabled)
        assert all(
            getattr(action, "choice", None) not in disabled
            for action in query.legal_actions
        ), "置灰项不会进候选（AI 也就永远不会选到它）"
        assert not [action for action in query.legal_actions if isinstance(action, Pass)]

    assert engine.finished, "必达终局"
    _assert_endgame_identity_consistency(engine, WUMING_AI_SEED)
    assert all(scan.illegal == 0 for scan in scans), "出现了候选集合之外的应答"

    replayed_seed, tape = tape_from_journal(engine.records)
    assert replayed_seed == WUMING_AI_SEED and tape
    replay = Engine.new_game(
        seed=replayed_seed,
        rule_config=RuleConfig(player_count=PLAYERS),
        pack=PACK,
        heroes=WUMING_HEROES,
    )
    replay.run([TapePlayer(tape)] * PLAYERS)

    assert replay.journal() == engine.journal(), "重放的记录流必须逐字节相等"


# ---------------------------------------------------------------------------
# M7 票 13：PvE 专属种子带 9000–9031（32 局）走 AI 批测入口（四判据 + 关系用例）
# ---------------------------------------------------------------------------

PVE_SEEDS: tuple[int, ...] = tuple(range(9000, 9032))
"""PvE 批测的固定种子带（M7 票 13；spec §Acceptance A4）：**9000–9031，32 局**。

这条带是 PvE 的**机械判据**专用批：只判"跑完 / 终局与 ``solve_pve_outcome`` 一致 / 零非法
应答 / 可回放"，**不判 verdict、不打胜率**（``ai_eval`` 的口径）。``docs/agents/ai-versions.md``
§3 的种子台账行由票 30 写回，本常量是那一行的机械锚点——下面
:func:`test_the_pve_seed_band_is_thirty_two_seeds_that_miss_every_registered_band` 钉住
"32 颗、互异、与已登记的带都不重叠"（趋势带 / 实机带 / 票 12 的 PvE 基准种子 6101–6106）。

**别把这条带挪作判决批**：判决批由 commit 派生（``tools/ai_eval/seeds.py``），混用即失去
判决力（``docs/agents/ai-versions.md`` §3 的纪律）。
"""

PVE_PLAYERS = 5
"""2 友 × 3 敌 = 5 座：PvE 上限（房间编排的座位总数 2~5）那一档，且座位 1 是 AI 队友。"""

PVE_IDENTITIES: tuple[Identity, ...] = (
    Identity.ALLY,
    Identity.ALLY,
    Identity.ENEMY,
    Identity.ENEMY,
    Identity.ENEMY,
)
"""逐座位阵营（座位 0 = 主角）。队友座位 1 与主角同桌，故这一批真有"友军"可误伤。"""

PVE_RULE = RuleConfig(
    player_count=PVE_PLAYERS,
    mode=Mode.PVE,
    pve=PveConfig(
        identities=PVE_IDENTITIES,
        principal=0,
        victory=PveVictory(kind=PveVictoryKind.ROUT),
    ),
)
"""这一批的逐场条件 = ``ROUT``：两条收局路径（敌人全灭 ⇒ 胜 / 主角阵亡 ⇒ 败）都只读配置
与存活集合，故 32 颗种子在胜败两侧都能收局（实测 8 胜 / 24 败），``wins_by_side`` 的两个
侧别都被坐到。四类逐场条件各自的整局判据归票 08 / 票 12，不在这条批测里重复证。"""


class _PveAnswerScan(_IllegalAnswerScan):
    """判据③的扫描器（与 108 基准组同一个 ``_IllegalAnswerScan``），另留 ``(query, action)``。

    ``pairs`` 只喂关系用例：关系判据要看到"这个询问里有哪些候选"，而不只是"答得合不合法"。
    """

    def __init__(self, inner) -> None:
        super().__init__(inner)
        self.pairs: list[tuple[Query, Action]] = []

    def respond(self, query: Query) -> Action:
        action = super().respond(query)
        self.pairs.append((query, action))
        return action


@dataclass(frozen=True)
class _PveBand:
    """整条 PvE 种子带的**一次**运行（四判据 + 关系用例共用，不重跑 32 局）。"""

    batch: BatchResult
    scans: dict[int, tuple[_PveAnswerScan, ...]]
    journal_paths: dict[int, Path]


@pytest.fixture(scope="module")
def pve_band(tmp_path_factory) -> _PveBand:
    """跑一次 9000–9031：入口 = 产品同源的 ``run_games``（不是另写一个开局循环）。

    ``keep_journal=True`` 是判据④的输入（批测写出的 journal 就是回放源）；
    ``participants_factory`` 让判据③与关系用例看到每座位**真询问 / 真应答**，而 AI 决策
    本身仍走产品同源的 :class:`~sgs.agents.HeuristicPlayer` + ``ai:<seat>`` 子流。
    """
    scans: dict[int, tuple[_PveAnswerScan, ...]] = {}

    def factory(spec):
        built = tuple(
            _PveAnswerScan(HeuristicPlayer(ai_stream(spec.seed, seat)))
            for seat in range(spec.players)
        )
        scans[spec.seed] = built
        return built

    batch = run_games(
        seeds=PVE_SEEDS,
        mode=Mode.PVE,
        players=PVE_PLAYERS,
        rule_config=PVE_RULE,
        participants_factory=factory,
        keep_journal=True,
    )
    directory = tmp_path_factory.mktemp("pve-band")
    journal_paths: dict[int, Path] = {}
    for game in batch.games:
        path = directory / f"pve-{game.seed}.jsonl"
        path.write_text(game.journal or "", encoding="utf-8", newline="")
        journal_paths[game.seed] = path
    return _PveBand(batch=batch, scans=scans, journal_paths=journal_paths)


def test_the_pve_seed_band_is_thirty_two_seeds_that_miss_every_registered_band() -> None:
    """种子带本身是验收内容（票 13）：32 颗、互异、逐带避让（台账 §3 的登记口径）。"""
    assert PVE_SEEDS == tuple(range(9000, 9032))
    assert len(set(PVE_SEEDS)) == 32
    registered = {
        "趋势带 104..231": range(104, 232),
        "趋势带 1000..1511": range(1000, 1512),
        "趋势带 2000..2319": range(2000, 2320),
        "趋势带 3000..3211": range(3000, 3212),
        "趋势带 4000..4127": range(4000, 4128),
        "趋势带 5000..5127": range(5000, 5128),
        "实机带 7001..7099": range(7001, 7100),
        "PvE 基准种子 6101..6106": range(6101, 6107),
    }
    for label, band in registered.items():
        assert set(PVE_SEEDS).isdisjoint(band), f"PvE 批测带与{label}重叠"


def test_the_pve_seed_band_runs_every_game_to_a_final_outcome(pve_band: _PveBand) -> None:
    """判据①**跑完**：32 颗一颗不少、逐局到终局、整批零 error（票 13 / spec A4）。"""
    batch = pve_band.batch

    assert batch.config["mode"] == "pve", "这批必须以 pve 模式入口跑"
    assert [game.seed for game in batch.games] == list(PVE_SEEDS), "整批不缩水（逐颗点名）"
    assert batch.summary["games"] == len(PVE_SEEDS) == 32
    assert batch.summary["errors"] == 0, [game.error for game in batch.games]
    assert batch.summary["finished"] == len(PVE_SEEDS)
    for game in batch.games:
        assert game.error is None, game.error
        assert game.finished and game.outcome is not None, f"seed={game.seed} 没跑到终局"
        assert game.outcome["result"] == "winner", (
            f"seed={game.seed} 以平局收场：PvE 的终局由 solve_pve_outcome 判，平局不在它的值域里"
        )
        assert len(game.seats) == PVE_PLAYERS


def test_every_pve_game_ends_where_the_pure_solver_says(pve_band: _PveBand) -> None:
    """判据②**终局与 ``solve_pve_outcome`` 一致**（票 13 / spec A4）。

    读法：用**终局**的存活集合与 ``GameState.round`` 重算一次纯求解器，逐字段比对
    ``side`` / ``winners`` / ``reason``——"跑完了就算了"不算过，终局必须是那个函数说
    的那个终局。``game.rounds`` 就是 ``GameState.round``（唯一计数源），故轮次类条件
    的"N 轮内"也走同一条重算，不另建计数。
    """
    config = PVE_RULE.pve
    assert config is not None
    for game in pve_band.batch.games:
        alive = frozenset(seat.seat for seat in game.seats if seat.alive)
        expected = solve_pve_outcome(config=config, alive=alive, round_no=game.rounds)
        assert expected is not None, f"seed={game.seed}：solver 说这局还没终局"
        assert game.outcome is not None
        actual = (game.outcome["side"], tuple(game.outcome["winners"]), game.outcome["reason"])
        assert actual == (expected.side, expected.winners, expected.reason), (
            f"seed={game.seed}：终局 {actual} ≠ solver 的答案 "
            f"{(expected.side, expected.winners, expected.reason)}"
            f"（存活 {sorted(alive)}，轮次 {game.rounds}）"
        )


def test_the_pve_seed_band_has_zero_illegal_answers(pve_band: _PveBand) -> None:
    """判据③**零非法应答**：每次应答都在候选集合 / 清单 / 附带指定池里（票 13 / spec A4）。

    与 108 基准组**同一个** :class:`_IllegalAnswerScan`（同一根尺），故 PvE 新增的询问面
    （友 / 敌公开、逐场胜负）没有把"答得出来"降成"恰好没炸"。
    """
    scans = pve_band.scans

    assert set(scans) == set(PVE_SEEDS), "每颗种子都要有逐座位扫描器（否则判据会空转）"
    assert all(len(group) == PVE_PLAYERS for group in scans.values())
    assert sum(scan.answers for group in scans.values() for scan in group) > 0, (
        "整批没有任何应答（断言不空转）"
    )
    offenders = {
        seed: [scan.illegal for scan in group]
        for seed, group in scans.items()
        if any(scan.illegal for scan in group)
    }
    assert not offenders, f"出现了候选集合之外的应答（seed → 逐座位次数）：{offenders}"


def test_the_pve_seed_band_replays_from_its_own_journal(pve_band: _PveBand) -> None:
    """判据④**可回放**：批测写出的 journal 读回来 → 磁带 → 重放，记录流逐字节相等。

    回放源走**唯一反序列化器** :func:`~sgs.engine.read_journal`（产品读录像的同一条路），
    不拿库对象里的记录流抄近道：这条判据要证的是"批测产物本身可回放"。
    """
    for game in pve_band.batch.games:
        path = pve_band.journal_paths[game.seed]
        replayed_seed, tape = tape_from_journal(read_journal(path))
        assert replayed_seed == game.seed and tape, (
            f"seed={game.seed} 的磁带必须带上种子与行动序列"
        )
        replay = Engine.new_game(
            seed=replayed_seed, rule_config=PVE_RULE, pack=PACK, hero_pool=()
        )
        replay.run([TapePlayer(tape)] * PVE_PLAYERS)

        assert replay.journal() == game.journal, (
            f"seed={game.seed} 重放的记录流必须逐字节相等"
        )


def _equal_geometry_slash_choices(
    query: Query,
) -> list[tuple[int, tuple[int, ...], tuple[int, ...]]]:
    """询问里「同一张牌、只差敌友权重」的【杀】单目标候选 → ``(card_id, 友方座位, 敌方座位)``。

    几何 = **同一张物理牌** + 两个目标的**体力与手牌数都相同**：``_score_attack`` 里除
    ``_ENEMY_FOCUS * P(敌)``（敌友权重）之外的每一项（伤害、手牌贴现、斩杀）在两张候选上
    相等，故"选哪一边"只可能是关系那一项的分差。多目标候选（方天画戟加目标那类）不进
    判据：多选一个是"额外目标"语义，不是"在一对一的对子里选谁"。
    """
    view = query.view
    seats_by_card: dict[int, set[int]] = {}
    for candidate in query.legal_actions:
        if (
            isinstance(candidate, UseCard)
            and candidate.card.type == "slash"
            and len(candidate.targets) == 1
        ):
            seats_by_card.setdefault(candidate.card.card_id, set()).add(candidate.targets[0])

    choices: list[tuple[int, tuple[int, ...], tuple[int, ...]]] = []
    for card_id, seats in seats_by_card.items():
        ally = tuple(seat for seat in sorted(seats) if view.players[seat].identity is Identity.ALLY)
        enemy = tuple(
            seat for seat in sorted(seats) if view.players[seat].identity is Identity.ENEMY
        )
        pairs = [
            (ally_seat, enemy_seat)
            for ally_seat in ally
            for enemy_seat in enemy
            if view.players[ally_seat].hp == view.players[enemy_seat].hp
            and view.players[ally_seat].hand_count == view.players[enemy_seat].hand_count
        ]
        if pairs:
            choices.append(
                (
                    card_id,
                    tuple(sorted({ally_seat for ally_seat, _ in pairs})),
                    tuple(sorted({enemy_seat for _, enemy_seat in pairs})),
                )
            )
    return choices


def test_the_ai_teammate_does_not_target_the_ally_in_the_pve_band(
    pve_band: _PveBand,
) -> None:
    """「AI 队友不把友军当目标」的**批测级关系用例**（票 13；spec A4）。

    票 08 的构造局面在**单询问**层钉同一件事（``_score_attack`` 上敌人严格高
    ``_ENEMY_FOCUS``、真应答跟着阵营翻座位）；这里把它放到整条 32 局种子上量一遍：
    **同一张牌、相同几何**（目标体力 / 手牌数都相同）时，AI 队友只要在这个询问里用了
    那张牌，就必须打敌人——只差关系这一项的候选里一次都不许打友军。

    刻意**不**断言"整批一刀都不砍友军"：斩杀分（``_LETHAL_STRIKE_VALUE``）与关系权重是
    两笔独立的价，一张能当场打死友军的【杀】仍可能被选中（票 08 的 I1 口径是"不给
    P(友) 扣分、只让敌人排前面"）；多目标【杀】额外加一个友军也同理。这两类都不是本票
    的改动面（``src/sgs/agents/heuristic.py`` 不在本票 ``Touches`` 里），故判据把变量收在
    "只差敌友权重这一项"的几何上——与票 08 那条构造用例同一条尺。

    反空转：本批至少要有一次真的在这种选边点里选了敌人，否则这条用例只是"没数据"。
    实测这条带上：**43 个"只差敌友权重"的选边点、19 次选敌、0 次选友**（其余 24 次在这
    个询问里用了别的牌 / 别的行动——那不是"选边"，不进判据）。
    """
    choice_points = 0
    enemy_picks = 0
    ally_picks: list[tuple[int, int, tuple[int, ...]]] = []
    for seed, group in pve_band.scans.items():
        for scan in group:
            for query, action in scan.pairs:
                if query.kind != "PLAY_PHASE":
                    continue
                view = query.view
                if view.players[query.actor].identity is not Identity.ALLY:
                    continue
                for card_id, ally_seats, enemy_seats in _equal_geometry_slash_choices(query):
                    choice_points += 1
                    if not (
                        isinstance(action, UseCard)
                        and action.card.card_id == card_id
                        and len(action.targets) == 1
                    ):
                        continue  # 这个询问里它用了别的牌 / 别的行动：不构成一次"选边"
                    target = action.targets[0]
                    if target in enemy_seats:
                        enemy_picks += 1
                    elif target in ally_seats:
                        ally_picks.append((seed, query.actor, action.targets))

    assert not ally_picks, (
        f"AI 队友在只差敌友权重的候选里打了友军（seed, actor, targets）：{ally_picks}"
    )
    assert choice_points > 0 and enemy_picks > 0, (
        f"本批没有任何「只差敌友权重」的选边点（选边点 {choice_points}、选敌 {enemy_picks}）"
        "——关系用例会空转"
    )
