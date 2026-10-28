"""实机读数（票 15；spec §5.1–§5.6 / §7.2）：一局 journal + meta → 两行摘要 + 空报账表。

缝 = **`ai_eval live` 命令**读一对真录像（`.jsonl` + `.meta.json`）后打印的文本与退出码。
夹具不是手抄 JSON，是**真一局**：5 人身份局、人类座位 0（有别的选项也先 Pass）、其余座位
`random`，用 `Session` 打完再 `save_run` 落盘。

计数用**手算的常数**钉住（不调 `count_misses` 重算一遍——那样断言永远绿）：

- 询问 202、AI 座位 `pass=6/83`、`composite=10/10`；
- 人类座位有 6 次"有别的选项却 Pass"，全部**不计入**（全座位会是 12/99）；
- 记录流里的复合清单是 `exclude` 的（读出来全空），所以 `composite=10/10` 只能来自
  **重放出来的引擎询问**——这一条把"live 读 journal 就够了"的误解钉死。

AI 档用 `random` 而不是 `heuristic`：启发式随规则工程票改，夹具不能跟着抖；随机座位的
取舍只由座位子流决定（票 12），与启发式语义无关。
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from sgs.data import load_content_pack
from sgs.engine import read_journal

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from sgs_web.replay import save_run  # noqa: E402 - tools/ 在 import 前才上 sys.path
from sgs_web.session import Session  # noqa: E402

from ai_eval import main  # noqa: E402 - 测试在 import 前把 tools/ 放上 sys.path
from ai_eval.misses import has_composite_offer  # noqa: E402

SEED = 7006
"""实机带（spec §5.2；`tools/ai_eval/seeds.py:LIVE_SEED_RANGE`）里的一个固定种子。"""

LEDGER_HEADER = "| seq | 座位 | 身份 | 询问类型 | 当时事实 | 实际选择 | 期望选择 | D/S | 已知/新 |"
LEDGER_RULE = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"


@dataclass(frozen=True)
class LiveRun:
    """一局真人机对局落盘后的两个文件。"""

    runs: Path
    stem: str

    @property
    def journal(self) -> Path:
        return self.runs / f"{self.stem}.jsonl"

    @property
    def meta(self) -> Path:
        return self.runs / f"{self.stem}.meta.json"


@pytest.fixture(scope="module")
def live_run(tmp_path_factory: pytest.TempPathFactory) -> LiveRun:
    """真一局：人类座位 0 能 Pass 就 Pass，其余座位 random。"""
    runs = tmp_path_factory.mktemp("live") / "runs"
    pack = load_content_pack(deck_profile="standard_ex_160")
    session = Session(
        seed=SEED,
        player_count=5,
        human_seat=0,
        ai="random",
        timeout_seconds=None,
        pack=pack,
    )
    while session.pending is not None:
        query = session.pending
        pass_action = next((a for a in query.legal_actions if a.kind == "Pass"), None)
        session.submit_answer(pass_action or query.legal_actions[0])
    path = save_run(runs, session)
    return LiveRun(runs=runs, stem=path.name[: -len(".jsonl")])


def _decisions(records):
    """把记录流的 `query` 与**被接受**的 `action` 按 seq 配成对（与 live 的读法同源）。"""
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


def _run_live(live_run: LiveRun, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(["live", "--run", live_run.stem, "--runs-dir", str(live_run.runs)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_live_prints_the_two_summary_lines_and_an_empty_ledger(
    live_run: LiveRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """§5.3 的两行摘要 + §5.2 的空报账表；计数是手算值（见模块 docstring）。"""
    code, out, err = _run_live(live_run, capsys)

    assert code == 0, err
    lines = out.splitlines()
    assert lines[0] == (
        "实机 seed=7006 5p identity 160 人类=seat0(反贼) 终局=反贼胜 获胜者=[0,3]"
    )
    assert lines[1] == (
        "询问=202 AI漏动作 pass=6/83(7.2%) composite=10/10(100.0%) 人类超时=0"
    )
    assert lines[2] == ""
    assert lines[3] == LEDGER_HEADER
    assert lines[4] == LEDGER_RULE
    assert len(lines) == 5, "报账表必须只有表头（结论由 agent 补，工具不替人写）"


def test_the_human_seats_passes_are_not_counted(
    live_run: LiveRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """人类座位的 Pass 是他的选择（§5.3）：夹具真有 6 次，读数里一次都不许算。"""
    records = read_journal(live_run.journal)
    human_pass_with_options = [
        (query, action)
        for query, action in _decisions(records)
        if query.actor == 0
        and action.kind == "Pass"
        and any(candidate.kind != "Pass" for candidate in query.legal_actions)
    ]
    assert len(human_pass_with_options) == 6, "夹具必须真的含人类 Pass-with-options，否则用例空转"

    code, out, err = _run_live(live_run, capsys)

    assert code == 0, err
    assert "pass=6/83(7.2%)" in out.splitlines()[1]
    # 把人类那 6 次算进去会长成 12/99——这条否掉"忘了按座位过滤"的实现。
    assert "pass=12/99" not in out


def test_composite_offers_come_from_the_replayed_query_not_the_journal(
    live_run: LiveRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """清单在记录流里是 `exclude` 的（读出来全空），读数里的 10/10 只能来自重放。"""
    records = read_journal(live_run.journal)
    journal_offers = sum(1 for query, _ in _decisions(records) if has_composite_offer(query))

    assert journal_offers == 0, "记录流一旦开始写复合清单，'只能重放'这条前提就要重判"

    code, out, err = _run_live(live_run, capsys)

    assert code == 0, err
    assert "composite=10/10(100.0%)" in out.splitlines()[1]


def test_a_missing_journal_fails_loudly_with_the_path(
    live_run: LiveRun, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺记录流：报**完整路径**、退出码 2，绝不静默打一行空摘要。"""
    shutil.copyfile(live_run.meta, tmp_path / f"{live_run.stem}.meta.json")

    code = main(["live", "--run", live_run.stem, "--runs-dir", str(tmp_path)])
    err = capsys.readouterr().err

    assert code == 2
    assert str(tmp_path / f"{live_run.stem}.jsonl") in err


def test_a_missing_meta_fails_loudly_with_the_path(
    live_run: LiveRun, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """缺侧车元数据：同样报路径、退出码 2（没有 meta 就重放不出引擎询问）。"""
    shutil.copyfile(live_run.journal, tmp_path / f"{live_run.stem}.jsonl")

    code = main(["live", "--run", live_run.stem, "--runs-dir", str(tmp_path)])
    err = capsys.readouterr().err

    assert code == 2
    assert str(tmp_path / f"{live_run.stem}.meta.json") in err


def test_runs_dir_defaults_to_runs_in_the_working_directory(
    live_run: LiveRun, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """spec §5.4 的缺省：`--runs-dir` 省略时读工作目录下的 `runs/`。"""
    monkeypatch.chdir(live_run.runs.parent)

    code = main(["live", "--run", live_run.stem])
    out = capsys.readouterr().out

    assert code == 0
    assert out.startswith("实机 seed=7006 5p identity 160 ")


def test_live_prints_no_verdict_win_rate_or_per_seed_reading(
    live_run: LiveRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """实机不判 verdict、不打胜率、不打逐种子（spec §5.1 / §5.4）。"""
    code, out, err = _run_live(live_run, capsys)

    assert code == 0, err
    for banned in ("胜率", "verdict", "win_rate", "逐种子"):
        assert banned not in out
    assert out.count("seed=") == 1, "只许出现这一局的种子，不是一批"


def test_a_pair_that_is_not_the_same_game_fails_loudly(
    live_run: LiveRun, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """同 stem 的 journal 与 meta 必须描述同一局：seed 对不上就报错，不混着读。"""
    shutil.copyfile(live_run.journal, tmp_path / f"{live_run.stem}.jsonl")
    meta = json.loads(live_run.meta.read_text(encoding="utf-8"))
    meta["seed"] = SEED + 1
    (tmp_path / f"{live_run.stem}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    code = main(["live", "--run", live_run.stem, "--runs-dir", str(tmp_path)])
    err = capsys.readouterr().err

    assert code == 2
    assert "不是同一局" in err
    assert "seed" in err


def test_the_recipe_doc_registers_the_seed_band_and_the_recipe() -> None:
    """验收票要求配方进仓（`Creates:` 的第三项），且种子带与台账同源。"""
    doc = ROOT / "docs/agents/acceptance-ai.md"
    assert doc.is_file(), "缺少 docs/agents/acceptance-ai.md"

    text = doc.read_text(encoding="utf-8")
    for phrase in (
        "7001–7099",
        "standard_ex_160",
        "人类座位",
        "思考时限",
        "ai_eval live --run",
        "D 类",
        "S 类",
    ):
        assert phrase in text, f"配方缺『{phrase}』"
