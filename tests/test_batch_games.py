"""批量能力（票 11；spec §1.3–§1.5）：库接口、结果 / 侧车、多进程一致性与退出码。

落点 = 批量层的**公开面**：``run_games``（含参数校验）、``BatchResult.to_dict`` /
``actions_lines``、``tools/batch_games.py`` 的 CLI。不碰 ``tools/sgs_batch`` 的内部函数。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sgs.agents import RandomPlayer
from sgs.data import Mode, RuleConfig, load_content_pack
from sgs.engine import Engine, ai_stream

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from sgs_batch import (  # noqa: E402 - 测试在 import 前把 tools/ 放上 sys.path
    BatchResult,
    BatchSpecError,
    BatchWorkerError,
    UnsupportedMode,
    cli,
    run_games,
)

PACK = load_content_pack(deck_profile="standard_108")

CONFIG_KEYS = {
    "seeds",
    "players",
    "mode",
    "pack_id",
    "deck_profile",
    "heroes",
    "hero_pool",
    "seat_policies",
    "keep_journal",
    "capture_decisions",
    "workers",
}
"""主 JSON ``config`` 的十一个键（spec §1.4）。"""

SUMMARY_KEYS = {
    "games",
    "finished",
    "errors",
    "draws",
    "wins_by_seat",
    "wins_by_side",
    "mean_rounds",
    "mean_decisions",
    "elapsed_ms",
}

GAME_KEYS = {
    "seed",
    "mode",
    "finished",
    "outcome",
    "rounds",
    "decisions",
    "action_digest",
    "seats",
    "elapsed_ms",
    "error",
    "journal",
}


def _random_two_player(seeds, *, workers: int = 1, **options):
    """2 人局、全随机档的批量（最快的有终局批次，用来压多进程与结果形状）。"""
    return run_games(
        seeds=seeds,
        mode=Mode.NO_IDENTITY,
        players=2,
        seat_policies=("random", "random"),
        workers=workers,
        **options,
    )


def _factory_that_explodes_on_142(spec):
    """模块顶层可 pickle 的 factory：种子 142 的那局在开跑时崩溃。"""
    if spec.seed == 142:
        raise RuntimeError("boom")
    return [RandomPlayer(ai_stream(spec.seed, seat)) for seat in range(2)]


def _factory_that_kills_the_worker_on_142(spec):
    """同上的"崩溃"版：``os._exit`` 绕过 Python 的 try/except，等同 worker 被 OOM / 信号杀死。"""
    if spec.seed == 142:
        os._exit(1)
    return [RandomPlayer(ai_stream(spec.seed, seat)) for seat in range(2)]


def _without_elapsed(batch: BatchResult) -> dict:
    """结果面去掉唯一的机器相关字段（``elapsed_ms``）；``config`` 原样保留。"""
    payload = batch.to_dict()
    payload["summary"].pop("elapsed_ms")
    for game in payload["games"]:
        game.pop("elapsed_ms")
    return payload


# ---------------------------------------------------------------------------
# ① 多进程：除 elapsed_ms（与 config.workers）外逐字段相同（spec §1.5）
# ---------------------------------------------------------------------------


def test_eight_games_are_field_identical_serially_and_in_parallel() -> None:
    seeds = tuple(range(240, 248))
    serial = _random_two_player(seeds, workers=1, capture_decisions=True)
    parallel = _random_two_player(seeds, workers=4, capture_decisions=True)

    assert [game.seed for game in serial.games] == list(seeds)
    assert [game.seed for game in parallel.games] == list(seeds)

    serial_payload = _without_elapsed(serial)
    parallel_payload = _without_elapsed(parallel)
    assert serial_payload["games"] == parallel_payload["games"]
    assert serial_payload["summary"] == parallel_payload["summary"]

    # ``config`` 是**请求**而不是结果：两份只应在 ``workers`` 这一项上不同（1 vs 4）。
    serial_config = dict(serial_payload["config"])
    parallel_config = dict(parallel_payload["config"])
    assert serial_config.pop("workers") == 1
    assert parallel_config.pop("workers") == 4
    assert serial_config == parallel_config


# ---------------------------------------------------------------------------
# ② 结果 JSON / 侧车形状与开关（spec §1.4）
# ---------------------------------------------------------------------------


def test_the_result_json_has_the_documented_shape() -> None:
    batch = run_games(
        seeds=(136, 137), mode="identity", players=5, capture_decisions=True
    )
    payload = batch.to_dict()

    assert payload["schema"] == "sgs.batch.v1"
    assert set(payload["config"]) == CONFIG_KEYS
    assert payload["config"]["mode"] == "identity"
    assert payload["config"]["players"] == 5
    assert payload["config"]["pack_id"] == "standard"
    assert payload["config"]["deck_profile"] == "standard_108"
    assert payload["config"]["hero_pool"] == "enabled"
    assert payload["config"]["seat_policies"] == ["heuristic"] * 5
    assert payload["config"]["keep_journal"] is False
    assert payload["config"]["capture_decisions"] is True
    assert payload["config"]["workers"] == 1

    assert set(payload["summary"]) == SUMMARY_KEYS
    assert payload["summary"]["games"] == 2
    assert payload["summary"]["finished"] == 2
    assert payload["summary"]["errors"] == 0
    assert len(payload["summary"]["wins_by_seat"]) == 5
    assert set(payload["summary"]["wins_by_side"]) == {"lord", "rebel", "renegade"}

    game = payload["games"][0]
    assert set(game) == GAME_KEYS
    assert game["mode"] == "identity"
    assert game["finished"] is True
    assert set(game["outcome"]) == {"result", "winner", "side", "winners", "reason"}
    assert len(game["seats"]) == 5
    assert set(game["seats"][0]) == {"seat", "identity", "hero", "alive", "won"}
    assert game["journal"] is None, "keep_journal 默认关"
    assert isinstance(game["action_digest"], str) and len(game["action_digest"]) == 64


def test_summarize_counts_seat_and_side_wins_from_the_outcomes() -> None:
    """汇总必须能由 ``games[]`` 独立复算出来（spec §1.4 的计口径）。"""
    batch = run_games(seeds=(136, 137), mode=Mode.IDENTITY, players=5)
    summary = batch.summary

    expected_by_seat = [0] * 5
    expected_by_side = {"lord": 0, "rebel": 0, "renegade": 0}
    draws = 0
    for game in batch.games:
        outcome = game.outcome
        assert outcome is not None
        if outcome["result"] == "draw":
            draws += 1
            continue
        for seat in outcome["winners"]:
            expected_by_seat[seat] += 1
        expected_by_side[outcome["side"]] += 1

    assert summary["wins_by_seat"] == expected_by_seat
    assert summary["wins_by_side"] == expected_by_side
    assert summary["draws"] == draws
    assert summary["mean_rounds"] == round(
        sum(game.rounds for game in batch.games) / len(batch.games), 3
    )
    assert summary["mean_decisions"] == round(
        sum(game.decisions for game in batch.games) / len(batch.games), 3
    )


def test_action_digest_is_stable_and_off_by_default() -> None:
    first = _random_two_player((250,), capture_decisions=True).games[0]
    second = _random_two_player((250,), capture_decisions=True).games[0]
    plain = _random_two_player((250,)).games[0]

    assert first.action_digest is not None
    assert first.action_digest == second.action_digest, "同输入的动作摘要必须稳定"
    assert first.actions == second.actions

    assert plain.action_digest is None, "capture_decisions 默认关"
    assert plain.actions == ()
    assert plain.decisions > 0, "决策**计数**与 capture 开关无关"


def test_action_digest_is_the_sha256_of_the_canonical_decision_jsonl() -> None:
    batch = _random_two_player((250,), capture_decisions=True)
    line = json.loads(batch.actions_lines()[0])

    canonical = "".join(
        json.dumps(decision, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for decision in line["decisions"]
    )

    assert line["schema"] == "sgs.batch.actions.v1"
    assert line["seed"] == 250
    assert [decision["index"] for decision in line["decisions"]] == list(
        range(batch.games[0].decisions)
    )
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == batch.games[0].action_digest


def test_every_decision_carries_the_documented_keys_and_the_compact_card() -> None:
    """侧车每条决策的键与 ``card`` 投影按 spec §1.4 固定（只留 card_id + virtual_sources）。"""
    batch = _random_two_player((250,), capture_decisions=True)
    line = json.loads(batch.actions_lines()[0])

    assert set(line) == {"schema", "seed", "mode", "decisions"}
    assert line["schema"] == "sgs.batch.actions.v1"
    assert line["decisions"], "一局不可能没有任何决策"
    for decision in line["decisions"]:
        assert set(decision) == {"index", "kind", "actor", "legal_count", "action"}
        assert decision["kind"]
        assert decision["actor"] in (0, 1)
        assert decision["legal_count"] >= 1
        card = decision["action"].get("card")
        if card is not None:
            assert set(card) == {"card_id", "virtual_sources"}, "card 必须只留这两个键"
    assert any("card" in decision["action"] for decision in line["decisions"]), (
        "随机 2 人局（种子 250）应至少有一次使用 / 打出牌的决策"
    )


def test_keep_journal_is_opt_in_and_reuses_the_engine_journal() -> None:
    kept = _random_two_player((251,), keep_journal=True).games[0]
    plain = _random_two_player((251,)).games[0]

    assert plain.journal is None
    assert kept.journal is not None and kept.journal.endswith("\n")
    assert json.loads(kept.journal.splitlines()[0])["kind"] == "header"

    engine = Engine.new_game(
        seed=251,
        rule_config=RuleConfig(player_count=2),
        pack=PACK,
        hero_pool=(),
        journal=True,
    )
    engine.run([RandomPlayer(ai_stream(251, seat)) for seat in range(2)])

    assert kept.journal == engine.journal(), "批量 journal 必须走引擎的唯一序列化器"


def test_no_heroes_mode_is_recorded_in_the_config_and_the_seats() -> None:
    batch = run_games(
        seeds=(260,),
        mode=Mode.NO_IDENTITY,
        players=2,
        hero_pool=None,
        seat_policies=("random", "random"),
    )

    assert batch.config["hero_pool"] is None
    assert all(seat.hero is None for seat in batch.games[0].seats)


# ---------------------------------------------------------------------------
# ③ 参数 / 模式 / 种子的 fail-closed（spec §1.3）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("players", "mode"),
    [
        (4, Mode.NO_IDENTITY),
        (2, Mode.IDENTITY),
        (3, Mode.IDENTITY),
        (5, "team"),
    ],
)
def test_unsupported_mode_combinations_fail_and_name_both_values(players, mode) -> None:
    with pytest.raises(UnsupportedMode) as info:
        run_games(seeds=(1,), mode=mode, players=players)

    message = str(info.value)
    assert str(players) in message, f"报错没点名人数：{message}"
    assert str(mode.value if isinstance(mode, Mode) else mode) in message, (
        f"报错没点名模式：{message}"
    )


def test_rule_config_must_agree_with_the_explicit_mode() -> None:
    with pytest.raises(UnsupportedMode) as info:
        run_games(
            seeds=(1,),
            mode=Mode.IDENTITY,
            players=5,
            rule_config=RuleConfig(player_count=2),
        )

    assert "rule_config" in str(info.value)


@pytest.mark.parametrize(
    ("players", "heroes", "hero_pool"),
    [
        (5, ("caocao",), ()),  # 显式武将与座位数不一致
        (5, (), ("nobody",)),  # 未知武将 id
    ],
)
def test_a_bad_hero_config_is_a_spec_error(players, heroes, hero_pool) -> None:
    """坏武将配置在**开跑前**就失败（与引擎同一套发放校验），不是某一局的 error。"""
    with pytest.raises(BatchSpecError, match="武将配置"):
        run_games(
            seeds=(1,),
            mode=Mode.IDENTITY,
            players=players,
            heroes=heroes,
            hero_pool=hero_pool,
        )


def test_the_cli_exit_code_is_two_for_a_bad_hero_config(tmp_path) -> None:
    out = tmp_path / "batch.json"

    code = cli.main(
        [
            "--seeds",
            "1",
            "--mode",
            "identity",
            "--players",
            "5",
            "--heroes",
            "caocao",
            "--out",
            str(out),
        ]
    )

    assert code == 2
    assert not out.exists()


def test_seat_policies_must_match_the_seat_count() -> None:
    with pytest.raises(BatchSpecError, match="seat_policies"):
        run_games(
            seeds=(1,), mode=Mode.NO_IDENTITY, players=2, seat_policies=("heuristic",)
        )


def test_seat_policies_only_accept_registered_policies() -> None:
    with pytest.raises(BatchSpecError, match="cheater"):
        run_games(
            seeds=(1,),
            mode=Mode.NO_IDENTITY,
            players=2,
            seat_policies=("heuristic", "cheater"),
        )


def test_seat_policies_and_participants_factory_are_mutually_exclusive() -> None:
    with pytest.raises(BatchSpecError, match="互斥"):
        run_games(
            seeds=(1,),
            mode=Mode.NO_IDENTITY,
            players=2,
            seat_policies=("heuristic", "heuristic"),
            participants_factory=_factory_that_explodes_on_142,
        )


def _factory_with_one_participant(spec):
    return [RandomPlayer(ai_stream(spec.seed, 0))]


def test_a_participants_factory_must_return_one_participant_per_seat() -> None:
    with pytest.raises(BatchSpecError, match="participants_factory"):
        run_games(
            seeds=(1,),
            mode=Mode.NO_IDENTITY,
            players=2,
            participants_factory=_factory_with_one_participant,
        )


def test_seeds_are_executed_in_ascending_order() -> None:
    """乱序输入 + 并行完成顺序 ⇒ 结果仍按 seed 升序（不是完成顺序）。"""
    seeds = (245, 243, 247, 240, 246, 244, 242, 241)
    batch = _random_two_player(seeds, workers=4)

    assert [game.seed for game in batch.games] == sorted(seeds)
    assert batch.config["seeds"] == sorted(seeds)


def test_duplicate_seeds_are_rejected() -> None:
    with pytest.raises(BatchSpecError, match="重复"):
        _random_two_player((243, 243))


@pytest.mark.parametrize("workers", (0, 9, -1, True, 1.5))
def test_workers_must_be_an_integer_in_the_closed_range(workers) -> None:
    with pytest.raises(BatchSpecError, match="workers"):
        _random_two_player((1,), workers=workers)


# ---------------------------------------------------------------------------
# ④ 单局失败点名到 seed、整批不缩水（spec §1.5）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workers", (1, 2))
def test_a_broken_game_is_named_by_seed_and_the_batch_is_not_shrunk(workers) -> None:
    batch = run_games(
        seeds=(140, 141, 142, 143),
        mode=Mode.NO_IDENTITY,
        players=2,
        participants_factory=_factory_that_explodes_on_142,
        workers=workers,
    )

    assert [game.seed for game in batch.games] == [140, 141, 142, 143]
    failed = batch.games[2]
    assert failed.error == "seed=142: RuntimeError: boom"
    assert failed.finished is False
    assert failed.outcome is None
    assert batch.summary["errors"] == 1
    assert batch.summary["finished"] == 3


def test_a_worker_crash_is_named_by_the_missing_seeds_and_never_returns_half_a_batch() -> None:
    """worker 进程崩溃 ⇒ 点名没拿到结果的种子、整批不返回（spec §1.5）。

    用 ``os._exit`` 模拟真实崩溃（与 OOM / 信号同形）：单局 ``try/except`` 兜不住它，
    池进入 broken 状态；批量层必须把它转成一条点名失败，而不是把半批当全批返回。
    """
    with pytest.raises(BatchWorkerError) as info:
        run_games(
            seeds=(140, 141, 142, 143),
            mode=Mode.NO_IDENTITY,
            players=2,
            participants_factory=_factory_that_kills_the_worker_on_142,
            workers=2,
        )

    message = str(info.value)
    assert "worker 进程崩溃" in message
    assert "不返回半批" in message
    assert "142" in message, "崩溃的那一局（以及同批没跑完的）必须被点名"


# ---------------------------------------------------------------------------
# ⑤ CLI：主 JSON / 侧车 / 退出码（spec §1.4）
# ---------------------------------------------------------------------------


def test_parse_seeds_accepts_ranges_and_csv() -> None:
    assert cli.parse_seeds("136..138,140") == [136, 137, 138, 140]
    assert cli.parse_seeds(" 7 ") == [7]

    with pytest.raises(BatchSpecError, match="上界"):
        cli.parse_seeds("9..3")
    with pytest.raises(BatchSpecError, match="非整数"):
        cli.parse_seeds("1,x")


def test_the_cli_writes_the_main_json_and_the_actions_sidecar(tmp_path) -> None:
    main_json = tmp_path / "batch.json"
    sidecar = tmp_path / "actions.jsonl"

    code = cli.main(
        [
            "--seeds",
            "150..151",
            "--mode",
            "no_identity",
            "--players",
            "2",
            "--seat-policies",
            "random,random",
            "--out",
            str(main_json),
            "--decisions-out",
            str(sidecar),
        ]
    )

    assert code == 0
    payload = json.loads(main_json.read_text(encoding="utf-8"))
    assert payload["schema"] == "sgs.batch.v1"
    assert payload["config"]["capture_decisions"] is True, "--decisions-out 隐式开启采集"
    assert payload["summary"]["errors"] == 0

    lines = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()]
    assert [line["seed"] for line in lines] == [150, 151]
    assert all(line["schema"] == "sgs.batch.actions.v1" for line in lines)


def test_the_cli_exit_code_is_two_for_spec_errors(tmp_path) -> None:
    out = tmp_path / "batch.json"
    assert (
        cli.main(
            [
                "--seeds",
                "1",
                "--mode",
                "identity",
                "--players",
                "2",
                "--out",
                str(out),
            ]
        )
        == 2
    )
    assert (
        cli.main(
            [
                "--seeds",
                "1",
                "--mode",
                "no_identity",
                "--players",
                "2",
                "--workers",
                "0",
                "--out",
                str(out),
            ]
        )
        == 2
    )
    assert not out.exists(), "规格错误不得写出半批结果"


def test_the_cli_exit_code_is_one_when_a_game_fails(tmp_path, monkeypatch) -> None:
    """真实 ``run_games`` 产出一条失败结果；只注入那一个会炸的 factory。"""
    out = tmp_path / "batch.json"

    def run_with_one_broken_game(**kwargs):
        return run_games(participants_factory=_factory_that_explodes_on_142, **kwargs)

    monkeypatch.setattr(cli, "run_games", run_with_one_broken_game)

    code = cli.main(
        [
            "--seeds",
            "142",
            "--mode",
            "no_identity",
            "--players",
            "2",
            "--out",
            str(out),
        ]
    )

    assert code == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["summary"]["errors"] == 1
    assert payload["games"][0]["error"] == "seed=142: RuntimeError: boom"


def test_the_cli_exit_code_is_one_when_the_worker_pool_crashes(tmp_path, monkeypatch, capsys) -> None:
    """``BatchWorkerError`` 的退出码 / stderr 由 CLI 自己的分支收口（spec §1.4 / §1.5）。"""

    def crash(**kwargs):
        raise BatchWorkerError("worker 进程崩溃（不返回半批）；没有拿到结果的种子：[142]")

    monkeypatch.setattr(cli, "run_games", crash)

    code = cli.main(
        [
            "--seeds",
            "142",
            "--mode",
            "no_identity",
            "--players",
            "2",
            "--out",
            str(tmp_path / "batch.json"),
        ]
    )

    assert code == 1
    assert "worker 进程崩溃" in capsys.readouterr().err


def test_the_cli_parser_exits_with_two_on_missing_arguments() -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--seeds", "1"])

    assert info.value.code == 2


def test_the_thin_launcher_runs_as_a_script(tmp_path) -> None:
    out = tmp_path / "batch.json"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "batch_games.py"),
            "--seeds",
            "160",
            "--mode",
            "no_identity",
            "--players",
            "2",
            "--seat-policies",
            "random,random",
            "--out",
            str(out),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == "sgs.batch.v1"
