"""实机读数（票 15；spec §5.1–§5.6 / §7.2）：一局 journal + meta → 两行摘要 + 空报账表。

缝 = **`ai_eval live` 命令**读一对**固定**录像（`tests/fixtures/ai_live/`）后打印的文本与
退出码。夹具是一局**真对局**（2p / `basic_53` / 人类座位 0 能 Pass 就 Pass / 其余座位 random /
张飞 vs 关羽），一次性录好入仓，测试**不再现场重录**：计数常量（16 / 3/6 / 2/2）钉的是
读取方，不跟引擎走（spec §5.4 "固定 journal + meta 夹具"）。

`render_live` 的 5 人身份局 / 平局 / 空分母格式另有一组**纯渲染**用例，不依赖引擎。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from sgs.engine import read_journal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from ai_eval import LiveReading, MissCounts, main, render_live  # noqa: E402
from ai_eval.misses import has_composite_offer  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/ai_live"
STEM = "t15-2p-basic53"
JOURNAL = FIXTURES / f"{STEM}.jsonl"
META = FIXTURES / f"{STEM}.meta.json"

LEDGER_HEADER = "| seq | 座位 | 身份 | 询问类型 | 当时事实 | 实际选择 | 期望选择 | D/S | 已知/新 |"
LEDGER_RULE = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"


def _run_live(
    capsys: pytest.CaptureFixture[str], runs_dir: Path = FIXTURES
) -> tuple[int, list[str], str]:
    code = main(["live", "--run", STEM, "--runs-dir", str(runs_dir)])
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err


def _decisions(records):
    """测试**自己**按 seq 配对（不 import live 的私有实现）；只用于夹具前提。

    只有被接受的 action 才是答案——这是记录流的语义，不是 live 的实现细节。
    """
    pending = None
    for record in records:
        if record.kind == "query":
            pending = record.query
        elif (
            record.kind == "action"
            and record.accepted
            and pending is not None
            and record.answers_seq == pending.seq
        ):
            yield pending, record.action
            pending = None


def test_live_prints_the_two_summary_lines_and_an_empty_ledger(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§5.3 的两行摘要 + §5.2 的空报账表；计数是固定夹具上的手算值。"""
    code, lines, err = _run_live(capsys)

    assert code == 0, err
    assert lines[0] == "实机 seed=1 2p no_identity 53 人类=seat0 终局=获胜 获胜者=[0]"
    assert lines[1] == "询问=16 AI漏动作 pass=3/6(50.0%) composite=2/2(100.0%) 人类超时=0"
    assert lines[2] == ""
    assert lines[3] == LEDGER_HEADER
    assert lines[4] == LEDGER_RULE
    assert len(lines) == 5, "报账表必须只有表头（结论由 agent 补，工具不替人写）"


def test_the_human_seats_passes_are_not_counted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人类座位的 Pass 是他的选择（§5.3）：夹具真有 1 次，读数里一次都不许算。"""
    human_pass_with_options = [
        (query, action)
        for query, action in _decisions(read_journal(JOURNAL))
        if query.actor == 0
        and action.kind == "Pass"
        and any(candidate.kind != "Pass" for candidate in query.legal_actions)
    ]
    assert len(human_pass_with_options) == 1, "夹具必须真的含人类 Pass-with-options，否则用例空转"

    code, lines, err = _run_live(capsys)

    assert code == 0, err
    assert "pass=3/6(50.0%)" in lines[1]
    # 把人类那 1 次算进去会长成 4/13——这条否掉"忘了按座位过滤"的实现。
    assert "pass=4/13" not in "\n".join(lines)


def test_composite_offers_come_from_the_replayed_query_not_the_journal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """清单在记录流里是 `exclude` 的（读出来全空），读数里的 2/2 只能来自重放。"""
    journal_offers = sum(
        1 for query, _ in _decisions(read_journal(JOURNAL)) if has_composite_offer(query)
    )

    assert journal_offers == 0, "记录流一旦开始写复合清单，'只能重放'这条前提就要重判"

    code, lines, err = _run_live(capsys)

    assert code == 0, err
    assert "composite=2/2(100.0%)" in lines[1]


def test_live_renders_the_five_player_identity_summary() -> None:
    """§5.3 模板的 5 人身份局形态（固定夹具是 2p；这里钉渲染，不依赖引擎）。"""
    reading = LiveReading(
        seed=7006,
        player_count=5,
        mode="identity",
        deck_size=160,
        human_seat=0,
        human_identity="反贼",
        outcome="反贼胜",
        winners=(0, 3),
        queries=202,
        misses=MissCounts(6, 83, 10, 10),
        timeouts=0,
    )

    lines = render_live(reading).splitlines()

    assert lines[0] == "实机 seed=7006 5p identity 160 人类=seat0(反贼) 终局=反贼胜 获胜者=[0,3]"
    assert lines[1] == "询问=202 AI漏动作 pass=6/83(7.2%) composite=10/10(100.0%) 人类超时=0"


def test_live_renders_a_draw_without_winners() -> None:
    """平局：`获胜者=[]`，不编造胜方。"""
    reading = LiveReading(
        seed=7,
        player_count=5,
        mode="identity",
        deck_size=160,
        human_seat=2,
        human_identity="内奸",
        outcome="平局",
        winners=(),
        queries=40,
        misses=MissCounts(1, 9, 0, 0),
        timeouts=0,
    )

    lines = render_live(reading).splitlines()

    assert lines[0] == "实机 seed=7 5p identity 160 人类=seat2(内奸) 终局=平局 获胜者=[]"


def test_zero_denominators_render_without_a_fake_percentage() -> None:
    """未测 ≠ 0%：分母为 0 时不打 `(0.0%)`（模板是 `composite=0/0`）。"""
    reading = LiveReading(
        seed=7,
        player_count=2,
        mode="no_identity",
        deck_size=53,
        human_seat=None,
        human_identity=None,
        outcome="获胜",
        winners=(1,),
        queries=9,
        misses=MissCounts(0, 0, 0, 0),
        timeouts=0,
    )

    lines = render_live(reading).splitlines()

    assert lines[0] == "实机 seed=7 2p no_identity 53 人类=无 终局=获胜 获胜者=[1]"
    assert lines[1] == "询问=9 AI漏动作 pass=0/0 composite=0/0 人类超时=0"


def test_a_missing_journal_fails_loudly_with_the_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺记录流：报**完整路径**、退出码 2，绝不静默打一行空摘要。"""
    shutil.copyfile(META, tmp_path / f"{STEM}.meta.json")

    code, _, err = _run_live(capsys, tmp_path)

    assert code == 2
    assert str(tmp_path / f"{STEM}.jsonl") in err


def test_a_missing_meta_fails_loudly_with_the_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺侧车元数据：同样报路径、退出码 2（没有 meta 就重放不出引擎询问）。"""
    shutil.copyfile(JOURNAL, tmp_path / f"{STEM}.jsonl")

    code, _, err = _run_live(capsys, tmp_path)

    assert code == 2
    assert str(tmp_path / f"{STEM}.meta.json") in err


def test_a_pair_that_is_not_the_same_game_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同 stem 的 journal 与 meta 必须描述同一局：seed 对不上就报错，不混着读。"""
    shutil.copyfile(JOURNAL, tmp_path / f"{STEM}.jsonl")
    _write_meta(tmp_path, seed=2)

    code, _, err = _run_live(capsys, tmp_path)

    assert code == 2
    assert "不是同一局" in err
    assert "seed" in err


def test_a_journal_without_a_game_end_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """崩溃 / 写到一半的录像没有 game_end：报"没有终局记录"+ 路径，不打空摘要。"""
    shutil.copyfile(META, tmp_path / f"{STEM}.meta.json")
    lines = JOURNAL.read_text(encoding="utf-8").splitlines(keepends=True)
    (tmp_path / f"{STEM}.jsonl").write_text("".join(lines[:-1]), encoding="utf-8")

    code, _, err = _run_live(capsys, tmp_path)

    assert code == 2
    assert "没有终局记录" in err
    assert str(tmp_path / f"{STEM}.jsonl") in err


def test_a_tape_that_cannot_replay_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """meta 磁带与记录流对不上：重放失败归退出码 1（这次没测成），仍点名路径。"""
    shutil.copyfile(JOURNAL, tmp_path / f"{STEM}.jsonl")
    meta = json.loads(META.read_text(encoding="utf-8"))
    _write_meta(tmp_path, tape=meta["tape"][:-1])

    code, _, err = _run_live(capsys, tmp_path)

    assert code == 1
    assert "重放失败" in err
    assert str(tmp_path / f"{STEM}.jsonl") in err


def test_a_meta_without_timeouts_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """未测 ≠ 0：超时次数缺失时不许报 `人类超时=0`（那是编出来的读数）。"""
    shutil.copyfile(JOURNAL, tmp_path / f"{STEM}.jsonl")
    meta = json.loads(META.read_text(encoding="utf-8"))
    del meta["timeouts"]
    _write_meta(tmp_path, meta=meta)

    code, _, err = _run_live(capsys, tmp_path)

    assert code == 2
    assert "超时次数非法" in err
    assert str(tmp_path / f"{STEM}.meta.json") in err


def test_a_human_seat_outside_the_table_fails_loudly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """人类座位越界时拒绝读，而不是默默把所有人的 Pass 都算成 AI 的。"""
    shutil.copyfile(JOURNAL, tmp_path / f"{STEM}.jsonl")
    _write_meta(tmp_path, human_seat=9)

    code, _, err = _run_live(capsys, tmp_path)

    assert code == 2
    assert "人类座位非法" in err
    assert str(tmp_path / f"{STEM}.meta.json") in err


def test_an_invalid_run_name_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    """`--run` 只能是文件名：路径分隔符 / 空名在 meta 读取边界被拒（不拼出仓外路径）。"""
    for name in ("../escape", ""):
        code = main(["live", "--run", name, "--runs-dir", str(FIXTURES)])
        err = capsys.readouterr().err

        assert code == 2
        assert "录像名非法" in err


def test_runs_dir_defaults_to_runs_in_the_working_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §5.4 的缺省：`--runs-dir` 省略时读工作目录下的 `runs/`。"""
    runs = tmp_path / "runs"
    runs.mkdir()
    shutil.copyfile(JOURNAL, runs / JOURNAL.name)
    shutil.copyfile(META, runs / META.name)
    monkeypatch.chdir(tmp_path)

    code = main(["live", "--run", STEM])
    out = capsys.readouterr().out

    assert code == 0
    assert out.startswith("实机 seed=1 2p no_identity 53 ")


def test_live_prints_no_verdict_win_rate_or_per_seed_reading(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """实机不判 verdict、不打胜率、不打逐种子（spec §5.1 / §5.4）。"""
    code, lines, err = _run_live(capsys)
    text = "\n".join(lines)

    assert code == 0, err
    for banned in ("胜率", "verdict", "win_rate", "逐种子"):
        assert banned not in text
    assert text.count("seed=") == 1, "只许出现这一局的种子，不是一批"


def test_the_recipe_doc_registers_the_seed_band_and_the_recipe() -> None:
    """验收票要求配方进仓，且种子带与台账同时登记（`Creates:` 的第三项）。"""
    doc = ROOT / "docs/agents/acceptance-ai.md"
    assert doc.is_file(), "缺少 docs/agents/acceptance-ai.md"

    text = doc.read_text(encoding="utf-8")
    for phrase in (
        "7001–7099",
        "standard_ex_160",
        "人类座位",
        "思考时限",
        "ai_eval live --run",
        "版本台账",
        "D 类",
        "S 类",
    ):
        assert phrase in text, f"配方缺『{phrase}』"

    ledger = (ROOT / "docs/agents/ai-versions.md").read_text(encoding="utf-8")
    for phrase in ("实机带", "7001–7099", "docs/agents/acceptance-ai.md"):
        assert phrase in ledger, f"版本台账缺『{phrase}』"


def _write_meta(
    runs_dir: Path,
    *,
    meta: dict[str, object] | None = None,
    seed: int | None = None,
    human_seat: int | None = None,
    tape: list[dict[str, object]] | None = None,
) -> None:
    """把固定 meta 改一处写回 `runs_dir`（用于坏输入用例；不改夹具本体）。"""
    payload = dict(meta) if meta is not None else json.loads(META.read_text(encoding="utf-8"))
    if seed is not None:
        payload["seed"] = seed
    if human_seat is not None:
        payload["human_seat"] = human_seat
    if tape is not None:
        payload["tape"] = tape
    (runs_dir / f"{STEM}.meta.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
