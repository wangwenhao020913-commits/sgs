"""回放台（`ai-view` 票 03；spec §2.2）：explain / score / godview 的读方判据。

缝 = **`ai_eval replay` 命令 / `run_replay` + `render_replay`** 读一对固定录像
（`tests/fixtures/ai_replay/7004-5p-prefix`）后的报告与文本。夹具是**实机 seed=7004 那一局
的前 512 条记录**（到 seq=511「主公对座位 1 出【决斗】」为止；全量记录在 `.scratch/ai-light/runs/`
下、被 gitignore）：截断处之后旧引擎（ai-v4）与新引擎有已知分歧，故前缀夹具只服务
`--explain --seq` 这一条路径；`--score` / `--godview` 要整局，用现场生成的全 AI 局。

三条判据（票面「验收」）：
1. 同局重放两次输出逐字节一致；
2. seed=7004 的那一步有候选分项 + 剪枝面，且人读摘要能看出「座位 1 未知、P(敌)=3/4」；
3. 篡改过的记录（动作换成另一条**合法**动作、磁带与记录流仍自洽）⇒ 对照段报不一致、
   CLI 退出码 1——**不静默通过**。
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

from sgs.agents import HeuristicPlayer, tape_from_journal
from sgs.data import Identity, RuleConfig, load_content_pack
from sgs.effects import check_targets
from sgs.engine import Engine, ai_stream, dump_journal
from sgs.state import Pass, UseCard, project

from support import SeatSpec, build_state

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from ai_eval import main  # noqa: E402
from ai_eval.replay import (  # noqa: E402
    Candidate,
    GodViewReport,
    ReplayReport,
    _prune_lines,
    _true_relation,
    render_replay,
    run_replay,
)

FIXTURES = ROOT / "tests/fixtures/ai_replay"
STEM = "7004-5p-prefix"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _fixture_report(**kwargs):
    return run_replay(run=STEM, runs_dir=FIXTURES, **kwargs)


def _tampered_run(tmp_path: Path) -> Path:
    """把夹具里的那一步换成另一条**合法**动作：EndPhase（磁带与记录流同步改）。"""
    runs = tmp_path / "runs"
    runs.mkdir()
    journal = (FIXTURES / f"{STEM}.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(journal[-1])
    assert last["kind"] == "action" and last["answers_seq"] == 510
    last["action"] = {"answers_seq": 510, "kind": "EndPhase"}
    journal[-1] = json.dumps(last, ensure_ascii=False, sort_keys=True)
    (runs / f"{STEM}.jsonl").write_text("\n".join(journal) + "\n", encoding="utf-8")

    meta = json.loads((FIXTURES / f"{STEM}.meta.json").read_text(encoding="utf-8"))
    for index, action in enumerate(meta["tape"]):
        if action["answers_seq"] == 510:
            meta["tape"][index] = {"answers_seq": 510, "kind": "EndPhase"}
            break
    else:  # pragma: no cover - 夹具前提
        raise AssertionError("磁带里没有 seq=510 的应答")
    (runs / f"{STEM}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return runs


def _save_full_ai_run(runs_dir: Path, *, seed: int = 4242, players: int = 5) -> str:
    """现场录一局全 AI 对局（不依赖 `.scratch/`，也不把大录像塞进仓）。"""
    pack = load_content_pack(deck_profile="standard_108")
    engine = Engine.new_game(
        seed=seed,
        rule_config=RuleConfig(player_count=players),
        pack=pack,
        hero_pool=(),
    )
    engine.run([HeuristicPlayer(ai_stream(seed, seat)) for seat in range(players)])
    _, tape = tape_from_journal(engine.records)
    stem = f"t03-{players}p-{seed}"
    meta = {
        "version": 1,
        "event_schema_version": engine.records[0].event_schema_version,
        "seed": seed,
        "player_count": players,
        "pack_id": pack.pack_id,
        "deck_profile": pack.deck_profile,
        "rule_config": engine.records[0].rule_config.model_dump(mode="json"),
        "heroes": [player.hero for player in engine.state.players],
        "hero_pool": None,
        "hero_pool_explicit": False,
        "human_seat": None,
        "human_seat_won": None,
        "ai": "heuristic",
        "timeout_seconds": None,
        "timeouts": 0,
        "ask_policy": {"auto_skip": False},
        "finished_at": "2026-11-24T00:00:00",
        "session_log": [],
        "tape": [action.model_dump(mode="json") for action in tape],
    }
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{stem}.jsonl").write_text(
        dump_journal(engine.records), encoding="utf-8"
    )
    (runs_dir / f"{stem}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return stem


# ---------------------------------------------------------------------------
# 验收 2：seed=7004 的「主公对座位 1 出【决斗】」
# ---------------------------------------------------------------------------


def test_the_seed_7004_replay_explains_the_lord_duel_step() -> None:
    report = _fixture_report(explain=True, seq=511)

    assert len(report.explained) == 1
    step = report.explained[0]
    assert (step.query_seq, step.record_seq, step.actor, step.kind) == (
        510,
        511,
        3,
        "PLAY_PHASE",
    )
    # 「座位 1 当时是未知座位、P(敌)=3/4」：身份读数来自 AI 自己的 _identity_relation。
    seat_one = step.identities[1]
    assert seat_one.public_identity is None
    assert seat_one.enemy == pytest.approx(0.75)
    assert seat_one.ally == pytest.approx(0.25)
    assert seat_one.label == "敌"
    assert seat_one.fraction == (3, 1, 4)
    # 选中者 = 记录动作 = 【决斗】→ 座位 1；候选分项来自生产代码的 Score.add。
    assert step.chosen.kind == "UseCard"
    assert step.chosen.card.name == "决斗"
    assert step.chosen.targets == (1,)
    assert step.chosen == step.recorded
    assert not step.mismatch
    chosen = next(candidate for candidate in step.candidates if candidate.chosen)
    assert chosen.score == pytest.approx(step.best)
    names = [name for name, _ in chosen.items]
    assert "敌我关系" in names and "伤害牌底价" in names
    # 剪枝面：主公手里只有【闪】不在候选里（只能响应）。
    assert any(
        line.subject.startswith("手牌") and "响应" in line.reason for line in step.prune
    )
    assert any(
        line.subject == "座位目标面" and "没有座位被剪掉" in line.reason
        for line in step.prune
    )

    text = render_replay(report)
    assert "## 询问 seq=510（记录 seq=511）座位 3 · PLAY_PHASE" in text
    assert "P(敌)=0.75=3/4" in text
    assert "P(友)=0.25=1/4" in text
    assert "记录动作：UseCard(【决斗♣A】 → 座位1)" in text
    assert "- 一致 ✓" in text


def test_the_query_seq_and_the_record_seq_both_address_the_step() -> None:
    by_query = _fixture_report(explain=True, seq=510)
    by_record = _fixture_report(explain=True, seq=511)

    assert [step.query_seq for step in by_query.explained] == [510]
    assert [step.query_seq for step in by_record.explained] == [510]


def test_explain_output_is_byte_identical_across_two_replays() -> None:
    first = render_replay(_fixture_report(explain=True, seq=511))
    second = render_replay(_fixture_report(explain=True, seq=511))

    assert first == second
    assert hashlib.sha256(first.encode("utf-8")).hexdigest() == hashlib.sha256(
        second.encode("utf-8")
    ).hexdigest()


def test_a_full_ai_game_replays_without_a_comparison_mismatch(
    tmp_path: Path,
) -> None:
    """整局 292 条 AI 询问的对照段全绿；转化确认（丈八蛇矛）也按引擎解析后一致。

    `--score` / `--godview` 只走到终局、不看对照，这一条把"磁带驱动 + 影子 AI + 复合
    动作归一"三件事一起钉住：转化确认的 AI 应答不在候选里，记录流里只有引擎解析后的
    普通使用——不归一就会被误报成 292 条里的 1 条版本分歧。
    """
    runs = tmp_path / "runs"
    stem = _save_full_ai_run(runs)
    report = run_replay(run=stem, runs_dir=runs, explain=True)

    assert report.explained, "整局必须有可解释的 AI 询问"
    assert len(report.explained) == report.queries
    assert report.mismatches == ()
    assert report.comparison_mismatches == ()
    composite = next(
        (
            step
            for step in report.explained
            if step.resolved != step.chosen and step.kind == "PLAY_PHASE"
        ),
        None,
    )
    assert composite is not None, "夹具里必须出现一次转化确认（复合应答 → 引擎解析）"
    assert composite.prune and composite.prune[0].subject != "剪枝面", (
        "复合动作也要有座位目标面（引擎 check_targets 读解析后的牌）"
    )
    assert any(line.subject.startswith("座位") for line in composite.prune)
    for step in report.explained:
        if step.kind == "PLAY_PHASE":
            continue
        assert step.prune and step.prune[0].subject == "剪枝面", (
            "响应 / 选项 / 选牌窗口不做出牌阶段的目标剪枝"
        )
        assert all("距离" not in line.reason for line in step.prune)


# ---------------------------------------------------------------------------
# 验收 5：篡改记录 ⇒ 对照段报不一致（不静默）
# ---------------------------------------------------------------------------


def test_a_tampered_record_is_reported_instead_of_passing_silently(
    tmp_path: Path,
) -> None:
    runs = _tampered_run(tmp_path)
    report = run_replay(
        run=STEM, runs_dir=runs, explain=True, seq=511
    )

    assert len(report.mismatches) == 1
    step = report.mismatches[0]
    assert step.recorded.kind == "EndPhase"
    assert step.chosen.kind == "UseCard" and step.chosen.card.name == "决斗"
    text = render_replay(report)
    assert "不一致" in text
    assert "对照失败：1 步" in text


def test_the_replay_cli_exits_1_on_a_tampered_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _tampered_run(tmp_path)
    code = main(["replay", STEM, "--runs-dir", str(runs), "--explain", "--seq", "511"])
    captured = capsys.readouterr()

    assert code == 1, captured.err
    assert "不一致" in captured.out


# ---------------------------------------------------------------------------
# 验收 4：剪枝面至少三类原因（距离不足 / 已阵亡 / 不是合法目标）
# ---------------------------------------------------------------------------


def test_the_pruning_face_names_death_distance_and_illegal_target() -> None:
    pack = load_content_pack(deck_profile="standard_108")
    snatch = next(
        index for index, card in enumerate(pack.deck.cards) if card.type == "snatch"
    )
    slash = next(
        index for index, card in enumerate(pack.deck.cards) if card.type == "slash"
    )
    # 6 人环 0..5：座位 1 已阵亡 ⇒ 座位环 [0,2,3,4,5]（0 与 2/5 相邻）；
    # 座位 2 相邻但没有牌（顺手牵羊的牌面条件不满足）；座位 3/4 距离 2 > 上限 1；
    # 座位 5 相邻且有牌 = 唯一合法目标。
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(snatch,), identity=Identity.LORD),
            SeatSpec(alive=False, hp=0, identity=Identity.REBEL),
            SeatSpec(hand=(), equip=(), judge=(), identity=Identity.LOYALIST),
            SeatSpec(hand=(slash,), identity=Identity.REBEL),
            SeatSpec(hand=(slash,), identity=Identity.REBEL),
            SeatSpec(hand=(slash,), identity=Identity.LOYALIST),
        ],
        rule_config=RuleConfig(player_count=6),
    )
    card = state.card(snatch)
    assert check_targets(state, 0, card) == (5,), "夹具前提：只有座位 5 是合法目标"
    candidate = Candidate(
        action=UseCard(answers_seq=1, card=card, targets=(5,)),
        score=0.0,
        items=(),
        chosen=True,
    )

    lines = _prune_lines(
        state,
        project(state, 0),
        0,
        "PLAY_PHASE",
        [candidate],
        focus=candidate.action,
    )
    pairs = [(line.subject, line.reason) for line in lines]
    reasons = dict(pairs)

    assert ("座位1", "已阵亡") in pairs
    assert reasons["座位2"].startswith("不是合法目标")
    assert reasons["座位3"].startswith("距离不足")
    assert "距离 2" in reasons["座位3"] and "牌面上限 1" in reasons["座位3"]
    assert reasons["座位4"].startswith("距离不足")


# ---------------------------------------------------------------------------
# 验收 3：--score 在均匀配比后验上跑出一组带样本量的数；--godview 有在位代价
# ---------------------------------------------------------------------------


def test_score_reports_both_baselines_with_sample_size(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    stem = _save_full_ai_run(runs)
    report = run_replay(run=stem, runs_dir=runs, score=True)

    assert report.score is not None
    assert report.score.samples > 0
    assert report.score.seats >= 1
    assert report.score.queries > 0
    assert [baseline.name for baseline in report.score.baselines] == [
        "配比均匀后验",
        "恒先验",
    ]
    for baseline in report.score.baselines:
        assert math.isfinite(baseline.log_loss) and baseline.log_loss >= 0
        assert math.isfinite(baseline.brier) and baseline.brier >= 0
        assert 0.0 <= baseline.ece <= 1.0

    text = render_replay(report)
    assert "## 尺子（--score）" in text
    assert f"座位步={report.score.samples}" in text
    assert "| 配比均匀后验 |" in text


def test_godview_ties_each_wrong_judgment_to_its_in_office_cost(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    stem = _save_full_ai_run(runs)
    report = run_replay(run=stem, runs_dir=runs, godview=True)

    assert report.godview is not None
    assert report.godview.decisions == report.queries
    assert report.godview.wrong, "5 人局里主公必然把某个暗着的忠臣判成敌人"
    for wrong in report.godview.wrong:
        assert wrong.cost == wrong.viewer_queries * wrong.all_queries
        assert wrong.viewer_queries >= 1
        assert wrong.all_queries >= wrong.viewer_queries
    text = render_replay(report)
    assert "## 上帝视角（--godview）" in text
    assert "| 观察者 | 目标 | 判定 | 真值 |" in text


def _prune_fixture():
    pack = load_content_pack(deck_profile="standard_108")
    snatch = next(
        index for index, card in enumerate(pack.deck.cards) if card.type == "snatch"
    )
    slash = next(
        index for index, card in enumerate(pack.deck.cards) if card.type == "slash"
    )
    state = build_state(
        pack=pack,
        seats=[
            SeatSpec(hand=(snatch,), identity=Identity.LORD),
            SeatSpec(alive=False, hp=0, identity=Identity.REBEL),
            SeatSpec(hand=(), equip=(), judge=(), identity=Identity.LOYALIST),
            SeatSpec(hand=(slash,), identity=Identity.REBEL),
            SeatSpec(hand=(slash,), identity=Identity.REBEL),
            SeatSpec(hand=(slash,), identity=Identity.LOYALIST),
        ],
        rule_config=RuleConfig(player_count=6),
    )
    return state, project(state, 0), state.card(snatch)


def test_a_pass_step_still_gets_a_target_face_from_the_top_card_candidate() -> None:
    """Pass / EndPhase 没有选中牌，剪枝面仍按**最高分候选**的牌给出目标面。"""
    state, view, card = _prune_fixture()
    card_candidate = Candidate(
        action=UseCard(answers_seq=1, card=card, targets=(5,)),
        score=1.0,
        items=(),
        chosen=False,
    )
    pass_candidate = Candidate(
        action=Pass(answers_seq=1), score=2.0, items=(), chosen=True
    )

    lines = _prune_lines(
        state,
        view,
        0,
        "PLAY_PHASE",
        [card_candidate, pass_candidate],
        focus=pass_candidate.action,
    )
    pairs = [(line.subject, line.reason) for line in lines]

    assert ("座位目标面", "未选中牌（Pass）；按最高分候选 【顺手牵羊♠3】 的目标面分析") in pairs
    assert any(subject == "座位1" and reason == "已阵亡" for subject, reason in pairs)


def test_a_non_play_phase_step_does_not_borrow_play_phase_target_rules() -> None:
    state, view, card = _prune_fixture()
    candidate = Candidate(
        action=UseCard(answers_seq=1, card=card, targets=(5,)),
        score=1.0,
        items=(),
        chosen=True,
    )

    lines = _prune_lines(
        state, view, 0, "RESPOND", [candidate], focus=candidate.action
    )

    assert len(lines) == 1 and lines[0].subject == "剪枝面"
    assert "不是出牌阶段" in lines[0].reason
    assert all("距离" not in line.reason for line in lines)


def test_the_renegade_truth_is_the_real_balance_not_the_stale_table() -> None:
    """内奸的真值按真实存活力量均衡（`_renegade_hostility`），不读历史静态行。"""
    pack = load_content_pack(deck_profile="standard_108")
    seats = [
        SeatSpec(identity=Identity.LORD),
        SeatSpec(identity=Identity.LOYALIST),
        SeatSpec(identity=Identity.REBEL),
        SeatSpec(identity=Identity.REBEL),
        SeatSpec(identity=Identity.RENEGADE),
    ]
    balanced = build_state(
        pack=pack, seats=seats, rule_config=RuleConfig(player_count=5)
    )
    # 全员存活：主公侧 2（主公 + 忠臣）对反贼 2 ⇒ spread=0 ⇒ 反贼对均衡的内奸是「中立」。
    assert _true_relation(balanced, Identity.RENEGADE, Identity.REBEL) == "中立"

    # 忠臣阵亡：主公侧 1 对反贼 2 ⇒ spread=-1/3 ⇒ 反贼侧占优，内奸该帮反贼 ⇒「敌」。
    unbalanced = build_state(
        pack=pack,
        seats=[
            *seats[:1],
            SeatSpec(alive=False, hp=0, identity=Identity.LOYALIST),
            *seats[2:],
        ],
        rule_config=RuleConfig(player_count=5),
    )
    assert _true_relation(unbalanced, Identity.RENEGADE, Identity.REBEL) == "敌"
    assert _true_relation(unbalanced, Identity.LORD, Identity.REBEL) == "敌"


def test_the_ruler_warns_when_the_tape_disagrees_with_the_recomputed_argmax() -> None:
    """score / godview 不拿对照当失败条件，但把"录像被改过"显式打出来（不静默）。"""
    report = ReplayReport(
        run="x",
        seed=1,
        player_count=5,
        human_seat=None,
        queries=1,
        recorded_actions=1,
        explained=(),
        mismatches=(),
        score=None,
        godview=GodViewReport(wrong=(), decisions=1, open_at_end=0),
        comparison_mismatches=((77, "EndPhase", "UseCard(【决斗】 → 座位1)", None),),
    )

    text = render_replay(report)

    assert "注意：对照不一致 1 步" in text
    assert "seq=77" in text


# ---------------------------------------------------------------------------
# 命令面
# ---------------------------------------------------------------------------


def test_replay_requires_at_least_one_mode(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["replay", STEM, "--runs-dir", str(FIXTURES)])
    captured = capsys.readouterr()

    assert code == 2
    assert "至少要选一个模式" in captured.err


def test_replay_rejects_an_unknown_seq(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["replay", STEM, "--runs-dir", str(FIXTURES), "--explain", "--seq", "99999"]
    )
    captured = capsys.readouterr()

    assert code == 2
    assert "没有 seq=99999" in captured.err


def test_replay_explain_prints_the_step_and_exits_0(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["replay", STEM, "--runs-dir", str(FIXTURES), "--explain", "--seq", "511"]
    )
    captured = capsys.readouterr()

    assert code == 0, captured.err
    assert "P(敌)=0.75=3/4" in captured.out
    assert "## 询问 seq=510（记录 seq=511）" in captured.out
