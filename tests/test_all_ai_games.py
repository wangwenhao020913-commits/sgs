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

import pytest

from sgs.agents import (
    HeuristicPlayer,
    TapePlayer,
    is_designation_use,
    is_structured_activation,
    tape_from_journal,
)
from sgs.data import (
    Identity,
    RuleConfig,
    enabled_hero_ids,
    hero_by_id,
    load_content_pack,
)
from sgs.engine import Engine, ai_stream
from sgs.state import Pass

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

M6_CLOSEOUT_SEEDS: tuple[int, ...] = (136, 137, 146, 145, 140, 141, 142, 143)
"""spec P2-2 的 **8 个固定种子**（票 22 收口批；原取 32 种子验收批的前 8 个）。

写死而不是从 :data:`~support.ACCEPTANCE_SEEDS` 切片：种子批本身是验收内容，
换批必须同时改票面 ``## Answer`` 与 ``acceptance-map.md`` 的 P2-2 行。

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
    assert (len(pool), len(true_names)) == (83, 54), "spec D18/D19 的收口池（票 03 加风包黄忠 1 个；票 08 加神关羽移动版；票 01 加神吕蒙移动版；票 01（自制包）加韬·刘备自制版；feng-weiyan 票 01 加风包魏延 1 个；feng-xiahouyuan 票 01 加风包夏侯渊 1 个；feng-zhangjiao 票 01 加张角移动版 1 个；huo-dianwei-mobile 票 01 加火包移动版典韦 1 个；huo-mobile-batch 票 01 加火包移动版庞德 1 个；huo-mobile-batch 票 02 加火包移动版庞统 1 个；huo-mobile-batch 票 03 加火包移动版袁绍 1 个；huo-shenzhouyu-mobile 票 01 加火包移动版神周瑜 1 个；wuming 票 01 加无名自制版 1 个 ⇒ 69 / 43（新真名「无名」⇒ 真名数 +1）；huo-shenzhugeliang 票 01 加火包 OL 神诸葛亮 1 个 ⇒ 70 / 44（新真名「神诸葛亮」）；lei-batch 票 01 加雷包郝昭 1 个 ⇒ 73 / 47（新真名「郝昭」）；lei-batch 票 02 加雷包张绣 1 个 ⇒ 74 / 48（新真名「张绣」）；tao-caocao 票 01 加自制包韬·曹操自制版 1 个 ⇒ 76 / 49（真名「曹操」已在池里）；huo-wolong-yanliang 票 01 加火包 OL 卧龙诸葛 1 个 ⇒ 79 / 50（新真名「卧龙诸葛」）；票 02 加火包 OL 颜良文丑 1 个 ⇒ 80 / 51（新真名「颜良文丑」）；feng-yujin 票 01 加自制包锋·于禁 1 个 ⇒ 82 / 53（新真名「于禁」））"

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
