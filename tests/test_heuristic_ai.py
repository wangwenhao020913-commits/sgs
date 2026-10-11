"""启发式 AI（M3 票 13；`04` 的规则启发式小节 / ADR-011）。

落点全部是**外部缝**：

- ``HeuristicPlayer.respond(query) -> Action``（唯一接口，不碰私有打分函数）；
- ``SKILL_VALUES`` 与数据文件的**双向同步断言**（票 13 §技能语义表与同步纪律）；
- 完整对局的**记录流**（确定性、可回放、零非法应答、"比随机强"）；
- 可见性硬约束：只读 ``query.view`` 与 ``query.legal_actions``——静态扫描住
  ``tests/test_source_discipline.py``，这里补行为上的"注入全知真相不改变决策"。

判据这样读：候选集合由 :func:`sgs.effects.legal_actions_for` 从**真局面**生成（不是手抄），
应答只经 :meth:`HeuristicPlayer.respond` 观测；"比随机强"是一条**宽松**统计断言
（票 13 §验收 6 明说要避免脆弱）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sgs.agents import (
    SKILL_VALUES,
    HeuristicPlayer,
    RandomPlayer,
    TapePlayer,
    tape_from_journal,
)
from sgs.data import RuleConfig, enabled_hero_ids, hero_skill_id, load_content_pack, load_heroes
from sgs.effects import legal_actions_for
from sgs.engine import Engine, RandomSource
from sgs.skills import CATALOG
from sgs.state import (
    ActivateSkill,
    ArrangeContext,
    ArrangeQuery,
    DiscardQuery,
    DodgeQuery,
    EndPhase,
    Pass,
    PeachQuery,
    PlayPhaseQuery,
    QueryKind,
    SelectCardsQuery,
    SelectContext,
    SelectionSpec,
    SelectOptionContext,
    SelectOptionQuery,
    SkillActivationContext,
    SkillActivationQuery,
    Slot,
    UseCard,
    Zone,
    ZoneRef,
    project,
)
from sgs.timing import Timing

from support import SeatSpec, build_state

PACK = load_content_pack(deck_profile="standard_108")
TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
PROBE = TESTS_DIR / "journal_probe.py"
HASH_SEEDS = ("0", "1")


# ---------------------------------------------------------------------------
# 共用：真局面 + 真候选
# ---------------------------------------------------------------------------


def _ids(type_id: str) -> list[int]:
    return [index for index, card in enumerate(PACK.deck.cards) if card.type == type_id]


def _stream(seed: int):
    return RandomSource(seed).substream("ai")


def _deck_excluding(*card_ids: int) -> tuple[int, ...]:
    return tuple(
        index for index in range(len(PACK.deck.cards)) if index not in card_ids
    )


def _play(seed: int, *, players: int = 2, ai: str = "heuristic", hero_pool=None) -> Engine:
    """库 API 跑完整局（与命令行同源：同一 ``ai`` 子流、同样的开局参数）。"""
    rule_config = RuleConfig(player_count=players)
    engine = Engine.new_game(
        seed=seed, rule_config=rule_config, pack=PACK, hero_pool=hero_pool
    )
    player = HeuristicPlayer if ai == "heuristic" else RandomPlayer
    participants = [player(_stream(seed)) for _ in range(players)]
    engine.run(participants)
    return engine


# ---------------------------------------------------------------------------
# ① 技能语义表的双向同步纪律（票 13 §技能语义表与同步纪律）
# ---------------------------------------------------------------------------


def _missing_entries(table, values) -> list[str]:
    """启用的每名武将的每个**非锁定**技能都应有序语义条目（锁定技没有发动询问）。"""
    missing: list[str] = []
    for hero_id in enabled_hero_ids(table):
        hero = table.by_id(hero_id)
        for skill in hero.skills:
            full_id = hero_skill_id(hero.id, skill.id)
            definition = CATALOG.get(full_id)
            if definition is not None and definition.locked:
                continue
            if full_id not in values:
                missing.append(full_id)
    return missing


def _stray_entries(table, values) -> list[str]:
    """表里每个 id 都必须在数据文件里存在（不能凭空多出一条）。"""
    known = {hero_skill_id(hero.id, skill.id) for hero in table.heroes for skill in hero.skills}
    return sorted(set(values) - known)


def test_every_enabled_non_locked_hero_skill_has_a_semantic_entry():
    table = load_heroes()

    missing = _missing_entries(table, SKILL_VALUES)

    assert not missing, "这些启用武将的非锁定技能还没教 AI：" + "；".join(missing)


def test_every_semantic_entry_names_a_skill_in_the_data_file():
    table = load_heroes()

    strays = _stray_entries(table, SKILL_VALUES)

    assert not strays, "语义表里有数据文件里不存在的技能 id：" + "；".join(strays)


def test_the_forward_check_goes_red_when_an_entry_is_removed():
    """合成反例：临时删掉一条 ⇒ 正向断言必须变红（票 13 §验收 4）。"""
    table = load_heroes()
    assert not _missing_entries(table, SKILL_VALUES)

    trimmed = dict(SKILL_VALUES)
    victim = "hero.caocao.jianxiong"
    del trimmed[victim]

    assert victim in _missing_entries(table, trimmed)


def test_the_reverse_check_goes_red_on_an_unknown_id():
    """合成反例：临时加一条不存在的 id ⇒ 反向断言必须变红（票 13 §验收 4）。"""
    table = load_heroes()
    injected = dict(SKILL_VALUES)
    injected["hero.caocao.not_a_skill"] = (lambda *_: 0.0, "合成反例")

    assert "hero.caocao.not_a_skill" in _stray_entries(table, injected)


# ---------------------------------------------------------------------------
# ② 可见性：只读 query.view 与 query.legal_actions（票 13 §硬约束 4）
# ---------------------------------------------------------------------------


class _Omniscient:
    """把询问包成"附带全知真相"的形态：只多一个 ``truth``，其余逐字转发。"""

    def __init__(self, query, truth) -> None:
        object.__setattr__(self, "_query", query)
        object.__setattr__(self, "truth", truth)

    def __getattr__(self, name):
        return getattr(self._query, name)


def _play_phase_query(state, seat: int = 0) -> PlayPhaseQuery:
    actions = legal_actions_for(state, seat, QueryKind.PLAY_PHASE, answers_seq=1)
    return PlayPhaseQuery(
        seq=1,
        actor=seat,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="出牌阶段。",
        legal_actions=actions,
        view=project(state, seat),
    )


def _attack_state(*, defender_card: int):
    """攻方握一张【杀】，守方握**恰好一张**牌（牌面是唯一变量）。"""
    slash = _ids("slash")[0]
    return build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash,)), SeatSpec(hand=(defender_card,))],
        deck=(),
        turn_seat=0,
    )


def test_the_decision_is_invariant_to_hidden_hand_contents():
    """两个局面只在**对方手牌的牌面**上不同（张数 / 公开面相同）⇒ 决策必须一样。

    这是"AI 不作弊"的可断言形态：知道对方手里没有【闪】不会让它改变这一次出牌。
    """
    no_dodge = HeuristicPlayer(_stream(11)).respond(
        _play_phase_query(_attack_state(defender_card=_ids("slash")[1]))
    )
    with_dodge = HeuristicPlayer(_stream(11)).respond(
        _play_phase_query(_attack_state(defender_card=_ids("dodge")[0]))
    )

    assert no_dodge == with_dodge
    assert isinstance(no_dodge, UseCard) and no_dodge.card.type == "slash"


def test_injecting_omniscient_truth_does_not_change_the_answer():
    """把一个"知道对方手牌"的真相附在询问上 ⇒ 逐字相同的应答（AI 不看它）。"""
    state = _attack_state(defender_card=_ids("dodge")[0])
    query = _play_phase_query(state)
    truth = {"hands": {1: tuple(state.players[1].hand)}}

    plain = HeuristicPlayer(_stream(3)).respond(query)
    peeking = HeuristicPlayer(_stream(3)).respond(_Omniscient(query, truth))

    assert plain == peeking


# ---------------------------------------------------------------------------
# ③ 逐类询问的默认策略（票 13 §打分要素表）
# ---------------------------------------------------------------------------


def test_discard_keeps_the_peach_and_drops_the_cheapest_cards():
    peach, slash, dodge = _ids("peach")[0], _ids("slash")[0], _ids("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=1, hp_max=4, hand=(peach, slash, dodge)), SeatSpec()],
        deck=_deck_excluding(peach, slash, dodge),
        turn_seat=0,
    )
    actions = legal_actions_for(state, 0, QueryKind.DISCARD, answers_seq=1)
    query = DiscardQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_DISCARD,
        prompt="弃牌。",
        legal_actions=actions,
        view=project(state, 0),
        count=2,
    )

    action = HeuristicPlayer(_stream(7)).respond(query)

    assert peach not in {card.card_id for card in action.cards}, "【桃】最后弃"
    assert slash in {card.card_id for card in action.cards}, "先弃最便宜的【杀】"


def test_play_phase_prefers_damage_over_equipment_and_passing():
    slash, crossbow = _ids("slash")[0], _ids("crossbow")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(slash, crossbow)), SeatSpec()],
        deck=_deck_excluding(slash, crossbow),
        turn_seat=0,
    )

    action = HeuristicPlayer(_stream(5)).respond(_play_phase_query(state))

    assert isinstance(action, UseCard) and action.card.type == "slash"
    assert action.targets == (1,), "目标非己方"


def test_dodge_cancels_instead_of_passing():
    dodge = _ids("dodge")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(dodge,)), SeatSpec()],
        deck=_deck_excluding(dodge),
        turn_seat=0,
    )
    actions = legal_actions_for(state, 0, QueryKind.DODGE, answers_seq=1)
    query = DodgeQuery(
        seq=1,
        actor=0,
        timing=Timing.RESOLVE_BEFORE_EFFECT,
        prompt="需要【闪】。",
        legal_actions=actions,
        view=project(state, 0),
    )

    action = HeuristicPlayer(_stream(2)).respond(query)

    assert not isinstance(action, Pass), "能抵消就抵消"


def test_peach_saves_the_dying():
    peach = _ids("peach")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hp=0), SeatSpec(hand=(peach,))],
        deck=_deck_excluding(peach),
        turn_seat=0,
    )
    actions = legal_actions_for(state, 1, QueryKind.PEACH, answers_seq=1, dying=0)
    query = PeachQuery(
        seq=1,
        actor=1,
        timing=Timing.NEAR_DEATH_AT,
        prompt="谁出桃？",
        legal_actions=actions,
        view=project(state, 1),
        dying=0,
    )

    action = HeuristicPlayer(_stream(2)).respond(query)

    assert isinstance(action, UseCard) and action.card.type == "peach"


def _skill_query(state, seat: int, skill_id: str) -> SkillActivationQuery:
    actions = legal_actions_for(
        state,
        seat,
        QueryKind.SKILL_ACTIVATION,
        answers_seq=1,
        payload=SkillActivationContext(skill_id=skill_id, optional=True),
    )
    return SkillActivationQuery(
        seq=1,
        actor=seat,
        timing=Timing.DAMAGE_AFTER_TAKE,
        prompt="是否发动？",
        legal_actions=actions,
        view=project(state, seat),
        skill_id=skill_id,
        optional=True,
    )


def test_skill_activation_uses_the_semantic_table_for_a_known_skill():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])

    action = HeuristicPlayer(_stream(4)).respond(
        _skill_query(state, 0, "hero.caocao.jianxiong")
    )

    assert isinstance(action, ActivateSkill)
    assert action.skill_id == "hero.caocao.jianxiong"


def test_skill_activation_defaults_to_pass_when_the_table_has_no_entry():
    """缺省保守：装备技能与锁定技都不在表里 ⇒ 不发动（票 13 §验收 5 的"拿不准就不发动"）。"""
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])

    for skill_id in ("equip.eight_diagrams", "hero.machao.mashu"):
        action = HeuristicPlayer(_stream(4)).respond(_skill_query(state, 0, skill_id))
        assert isinstance(action, Pass), f"{skill_id} 不该被发动"


def test_select_cards_keeps_the_valuable_cards_when_they_are_a_cost():
    peach, slash = _ids("peach")[0], _ids("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(hand=(peach, slash)), SeatSpec()],
        deck=_deck_excluding(peach, slash),
        turn_seat=0,
    )
    slots = (
        Slot(ref=ZoneRef(Zone.HAND, 0), card=state.card(slash)),
        Slot(ref=ZoneRef(Zone.HAND, 0), card=state.card(peach)),
    )
    spec = SelectionSpec(zones=(Zone.HAND,), total=(1, 1))
    actions = legal_actions_for(
        state,
        0,
        QueryKind.SELECT_CARDS,
        answers_seq=1,
        payload=SelectContext(spec=spec, slots=slots),
    )
    query = SelectCardsQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PLAY_IDLE,
        prompt="选一张。",
        legal_actions=actions,
        view=project(state, 0),
        spec=spec,
        slots=slots,
    )

    action = HeuristicPlayer(_stream(6)).respond(query)

    assert action.picks == (0,), "成本选牌取低价值的【杀】而不是【桃】"


def test_select_option_greedily_takes_the_cheaper_loss():
    state = build_state(pack=PACK, seats=[SeatSpec(), SeatSpec()])
    options = ("discard_one", "draw_one")
    actions = legal_actions_for(
        state,
        0,
        QueryKind.SELECT_OPTION,
        answers_seq=1,
        payload=SelectOptionContext(skill_id="equip.double_sword", options=options),
    )
    query = SelectOptionQuery(
        seq=1,
        actor=0,
        timing=Timing.USE_AFTER_DESIGNATE_TARGET,
        prompt="二选一。",
        legal_actions=actions,
        view=project(state, 0),
        skill_id="equip.double_sword",
        options=options,
    )

    action = HeuristicPlayer(_stream(8)).respond(query)

    assert action.choice == "discard_one"


def test_arrange_puts_the_most_useful_card_on_top():
    peach, slash = _ids("peach")[0], _ids("slash")[0]
    state = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec()],
        deck=_deck_excluding(peach, slash),
    )
    revealed = (state.card(slash), state.card(peach))
    actions = legal_actions_for(
        state, 0, QueryKind.ARRANGE, answers_seq=1, payload=ArrangeContext(count=2)
    )
    query = ArrangeQuery(
        seq=1,
        actor=0,
        timing=Timing.PHASE_PREPARE,
        prompt="排列。",
        legal_actions=actions,
        view=project(state, 0),
        revealed=revealed,
    )

    action = HeuristicPlayer(_stream(9)).respond(query)

    assert action.split >= 1
    assert revealed[action.order[0]].type == "peach", "最有用的牌留顶"


# ---------------------------------------------------------------------------
# ④ 整局：零非法应答、确定性、可回放（票 13 §验收 2 / 3 / 5）
# ---------------------------------------------------------------------------


class _IllegalAnswerScan:
    """包一层参与者：断言每次应答都在候选集合内（引擎侧也会拒，但这里显式记一笔）。"""

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


def test_every_answer_in_a_full_game_is_one_of_the_offered_candidates():
    scans = []
    for seed in range(1, 5):
        engine = Engine.new_game(
            seed=seed,
            rule_config=RuleConfig(player_count=5),
            pack=PACK,
            hero_pool=(),
        )
        stream = _stream(seed)
        scans = [_IllegalAnswerScan(HeuristicPlayer(stream)) for _ in range(5)]
        engine.run(scans)

    assert sum(scan.answers for scan in scans) > 0
    assert all(scan.illegal == 0 for scan in scans), "启发式 AI 不得给出候选之外的应答"


def test_variants_of_the_same_question_get_the_same_answer():
    """同一种子同一询问 ⇒ 同一条应答（平分由 ``ai`` 子流打破，不另造随机源）。"""
    state = _attack_state(defender_card=_ids("dodge")[0])
    query = _play_phase_query(state)

    first = HeuristicPlayer(_stream(13)).respond(query)
    second = HeuristicPlayer(_stream(13)).respond(query)

    assert first == second


def test_the_full_ai_sweep_of_thirty_two_seeds_never_answers_illegally_or_crashes():
    """票 13 §验收 5：5 人全启发式、32 个种子，零非法应答、零崩溃。

    不要求"一定打死一方"之外的判决——胜负正确性由票 14 的基准组按身份口径复核。
    """
    games = 0
    for seed in range(1, 33):
        engine = Engine.new_game(
            seed=seed,
            rule_config=RuleConfig(player_count=5),
            pack=PACK,
            hero_pool=(),
        )
        stream = _stream(seed)
        scans = [_IllegalAnswerScan(HeuristicPlayer(stream)) for _ in range(5)]
        engine.run(scans)  # 崩溃即失败：非法应答会被引擎拒绝并抛错
        assert all(scan.illegal == 0 for scan in scans), f"种子 {seed} 出现了候选之外的应答"
        games += 1

    assert games == 32


def _probe_bytes(seed: int, hash_seed: str, *, ai: str, out: Path) -> bytes:
    """在带指定 ``PYTHONHASHSEED`` 的子进程里跑探针，返回 journal 原始字节。"""
    env = dict(os.environ, PYTHONHASHSEED=hash_seed)
    result = subprocess.run(
        [sys.executable, str(PROBE), "--seed", str(seed), "--ai", ai, "--out", str(out)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return out.read_bytes()


def test_the_same_heuristic_game_is_byte_identical_across_python_hash_seeds(tmp_path):
    runs = {
        hash_seed: _probe_bytes(7, hash_seed, ai="heuristic", out=tmp_path / f"{hash_seed}.jsonl")
        for hash_seed in HASH_SEEDS
    }

    assert runs[HASH_SEEDS[0]] == runs[HASH_SEEDS[1]], "启发式局必须跨 PYTHONHASHSEED 逐字节一致"


def test_a_heuristic_game_replays_from_its_tape():
    """``--ai heuristic`` 的一局能被 :class:`TapePlayer` 重放：种子 + 行动序列复现同一记录流。"""
    played = _play(7)
    seed, tape = tape_from_journal(played.records)
    assert seed == 7 and tape

    replay = Engine.new_game(seed=seed, rule_config=RuleConfig(player_count=2), pack=PACK)
    player = TapePlayer(tape)
    replay.run([player, player])

    assert replay.journal() == played.journal()


# ---------------------------------------------------------------------------
# ⑤ 比随机强（票 13 §验收 6；宽松统计判据）
# ---------------------------------------------------------------------------


def _candidate_wins(seed: int, *, heuristic: bool) -> bool:
    """5 人局里座位 0 是候选：``heuristic`` 决定它是不是启发式档，其余四席一律随机。

    两臂用**同一个种子**（牌堆 / 身份 / 武将映射逐字节相同）⇒ 差别只来自座位 0 的档位，
    这正是票面说的"在同一批种子上"；``hero_pool=()`` 与验收 5 的批次同口径（开武将）。
    五个座位共用同一个 ``ai`` 子流（与 ``cli.py`` / ``test_all_ai_games.py`` 一处口径）。
    """
    engine = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=5),
        pack=PACK,
        hero_pool=(),
    )
    stream = _stream(seed)
    players = [
        (HeuristicPlayer if heuristic and seat == 0 else RandomPlayer)(stream)
        for seat in range(5)
    ]
    engine.run(players)
    return 0 in set(engine.records[-1].winners)


def _candidate_wins_over(seeds: tuple[int, ...], *, heuristic: bool) -> int:
    """跑完一批种子、数座位 0 的胜局；任一局崩溃都**点名报出**（不静默缩小批次）。

    票 13 §验收 6 要求在**验收 5 的同一批 5 人种子**上比较；随机档遇崩溃时不能悄悄
    减种子，故这里收集失败清单并整条判红。
    """
    wins = 0
    failures: list[str] = []
    for seed in seeds:
        try:
            wins += _candidate_wins(seed, heuristic=heuristic)
        except Exception as error:  # noqa: BLE001 - 崩溃即失败，但要报出是哪个种子
            failures.append(f"seed={seed}: {type(error).__name__}: {error}")
    assert not failures, (
        "5 人 32 种子批里有对局没跑完（不得静默缩小批次）：\n" + "\n".join(failures)
    )
    return wins


def test_the_heuristic_beats_random_on_the_same_seeds():
    """票 13 §验收 6：**同一批种子**（= 验收 5 的 5 人 32 种子批）上启发式胜率显著更高。

    座位 0 为候选、其余四席随机：同一种子下两臂的局面逐字节相同，故胜率差只反映档位。
    判据刻意宽松（启发式 ≥ 0.6 且比随机多赢 ≥ 1/4 的种子），避免脆弱。
    """
    seeds = tuple(range(1, 33))

    heuristic_wins = _candidate_wins_over(seeds, heuristic=True)
    random_wins = _candidate_wins_over(seeds, heuristic=False)

    assert heuristic_wins / len(seeds) >= 0.6, f"启发式座位 0 只赢了 {heuristic_wins}/32"
    assert heuristic_wins - random_wins >= len(seeds) // 4, (
        f"启发式 {heuristic_wins}/32 vs 随机 {random_wins}/32："
        f"优势不足（票 13 §验收 6 的宽松判据）"
    )
