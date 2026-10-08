"""M1 完成定义收口：七条 DoD 逐条机械成立（票 27 / 票 11 §2）。

本文件不实现任何引擎行为，只把票 11 §2 的七条"完成"钉成**可判决的断言**：

1. ``DOD_ITEMS`` 把每条 DoD 绑定到**建立它的用例 node id**；
   ``test_every_definition_of_done_item_is_established_by_collected_tests`` 断言
   这些 node id 仍在**仓库测试树**的 pytest 收集集合里——删掉或改名建立者即红。
   集合由一次 ``pytest --collect-only``（不执行用例）得到，故单跑本文件也能判决。
   绑定只挡"建立者被删 / 改名"；建立者本身是否仍**断言得够狠**由门禁里的这些用例负责。
2. ``tests/run_all.sh`` 的门禁语义（pytest 缺失即失败、聚合失败不 fail-fast、唯一入口、
   非交互）在**临时沙箱**里真的跑一遍判决。

递归禁令：本文件**不**调用仓库里的 ``tests/run_all.sh``（那会在 pytest 里再起 pytest）。
沙箱判决的是门禁脚本的**行为**；真实门禁的全绿由交付流程跑一次 ``tests/run_all.sh`` 得到。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
RUN_ALL = TESTS_DIR / "run_all.sh"
CONFTEST = TESTS_DIR / "conftest.py"
PYPROJECT = ROOT / "pyproject.toml"
REPLAYS_DIR = TESTS_DIR / "replays"

DOC_CHECK_SCRIPTS = (
    "test_agents_md.sh",
    "test_agent_skills.sh",
    "test_dev_docs.sh",
    "test_rules_doc.sh",
)


class DoDItem(NamedTuple):
    """票 11 §2 的一条完成定义：中文条目 + 建立它的用例 node id。"""

    title: str
    establishers: tuple[str, ...]


DOD_ITEMS: dict[int, DoDItem] = {
    1: DoDItem(
        "可执行入口：两个入口同源、同种子逐字节一致、跑完整局、退出码 0、省略种子时生成并打印",
        (
            "tests/test_cli.py::test_module_entry_is_the_same_function",
            "tests/test_cli.py::test_console_script_points_at_the_same_entry_function",
            "tests/test_cli.py::test_module_entry_runs",
            "tests/test_cli.py::test_console_script_runs",
            "tests/test_cli.py::test_module_entry_and_console_script_agree_byte_for_byte",
            "tests/test_cli.py::test_cli_runs_a_whole_game_and_prints_the_seed_then_the_stream",
            "tests/test_cli.py::test_omitted_seed_is_generated_recorded_and_printed",
            "tests/test_cli.py::test_a_generated_seed_replays_the_same_stream",
        ),
    ),
    2: DoDItem(
        "一键门禁：run_all.sh 唯一入口、pytest 缺失即失败、聚合失败不 fail-fast",
        (
            "tests/test_dod.py::test_run_all_is_the_single_gate_entry",
            "tests/test_dod.py::test_the_gate_fails_when_pytest_is_missing",
            "tests/test_dod.py::test_the_gate_aggregates_failures_without_stopping_early",
            "tests/test_dod.py::test_the_gate_passes_when_scripts_and_pytest_pass",
            "tests/test_dod.py::test_there_is_no_second_gate_entry",
            "tests/test_dod.py::test_the_gate_is_non_interactive",
        ),
    ),
    3: DoDItem(
        "记录流可比对：canonical JSONL、同种子逐字节、回放即重跑比对",
        (
            "tests/test_journal.py::test_canonical_jsonl_is_lf_sorted_compact_unescaped_and_newline_terminated",
            "tests/test_journal.py::test_same_seed_twice_is_byte_identical",
            "tests/test_replays.py::test_replaying_the_tape_reproduces_the_journal_byte_for_byte",
            "tests/test_determinism.py::test_the_same_game_is_byte_identical_across_python_hash_seeds",
        ),
    ),
    4: DoDItem(
        "pytest 集合：数据加载与 53 张牌堆 / 58 时机 / 结算栈与终局收尾 / 四类 Query 与非法行动"
        " / 使用全步骤与处理区 / 濒死死亡与弃牌上限 / 视角 / RNG 与确定性",
        (
            # 数据加载与 53 张牌堆（票 03）
            "tests/data/test_deck_snapshot.py::test_deck_matches_the_53_card_snapshot",
            "tests/data/test_deck_snapshot.py::test_deck_counts_by_type",
            # 时机枚举 / 调度与 58 个必须实现的时机（票 01 / 票 25）
            "tests/test_timing.py::test_enum_has_exactly_83_nodes",
            "tests/test_timing_coverage.py::test_the_union_of_actual_record_streams_is_exactly_the_dispatch_whitelist",
            "tests/test_timing_coverage.py::test_the_dispatch_whitelist_and_the_empty_log_nodes_restate_the_58",
            # 结算栈（帧 / pc / return_to / 丢弃顺序）与终局收尾（票 04 / 票 07）
            "tests/test_resolution_stack.py::test_engine_names_the_return_point_and_discards_frames_innermost_first",
            "tests/test_resolution_stack.py::test_exhausting_a_frame_records_its_pop_in_the_journal",
            "tests/test_death.py::test_the_teardown_moves_leftover_processing_cards_into_the_discard_pile",
            "tests/test_death.py::test_card_conservation_holds_unconditionally_at_game_end",
            # 四类 Query、候选排序与非法行动三类拒绝（含 CLI 退出码 1，票 05 / 票 07 / 票 24）
            "tests/test_queries_views_actions.py::test_play_phase_still_asks_when_the_only_candidate_is_end_phase",
            "tests/test_queries_views_actions.py::test_discard_candidates_are_all_subsets_of_the_required_size_in_stable_order",
            "tests/test_queries_views_actions.py::test_candidates_are_ordered_by_card_id_with_the_cardless_action_last",
            "tests/test_queries_views_actions.py::test_structure_damaged_responses_are_malformed_and_change_nothing",
            "tests/test_queries_views_actions.py::test_a_stale_answers_seq_is_rejected_and_changes_nothing",
            "tests/test_queries_views_actions.py::test_an_answer_outside_the_candidate_set_is_rejected_and_changes_nothing",
            "tests/test_cli.py::test_bad_content_pack_exit_code_in_subprocess",
            "tests/test_cli.py::test_illegal_action_exit_code_in_subprocess",
            "tests/test_cli.py::test_malformed_action_exit_code_in_subprocess",
            # 使用全步骤链与处理区两段移动（票 07 / 票 19）
            "tests/test_use_and_resolve.py::test_a_slash_use_walks_all_six_l4_timings_in_official_order",
            "tests/test_use_and_resolve.py::test_an_uncancelled_slash_walks_effect_and_after_effect_and_pushes_damage",
            "tests/test_use_and_resolve.py::test_a_used_card_moves_hand_to_processing_to_discard_both_as_use",
            "tests/test_use_and_resolve.py::test_a_dodge_use_moves_its_own_card_through_the_processing_area",
            # 濒死 / 死亡 / 胜负检测点与终止点分离、弃牌阶段手牌上限（票 13 / 票 21 / 票 22）
            "tests/test_near_death.py::test_the_near_death_frame_dispatches_the_official_three_timings_in_order",
            "tests/test_death.py::test_the_death_frame_dispatches_the_trimmed_l13_segments_in_order",
            "tests/test_death.py::test_victory_is_recorded_at_the_check_point_but_does_not_terminate_there",
            "tests/test_turn_loop.py::test_discard_phase_discards_down_to_current_hp",
            # 视角（票 05）
            "tests/test_queries_views_actions.py::test_player_view_exposes_only_public_information_and_own_hand",
            "tests/test_near_death.py::test_the_peach_query_view_hides_the_opponents_hand_identities",
            # RNG 子流、洗牌 / 重洗、跨哈希种子确定性与源码纪律扫描（票 08 / 票 26）
            "tests/test_rng.py::test_consuming_the_ai_stream_does_not_move_the_shuffle_stream",
            "tests/test_deck_shuffle.py::test_swapping_the_ai_implementation_does_not_move_the_shuffle_stream",
            "tests/test_deck_shuffle.py::test_a_reshuffle_leaves_exactly_one_deck_shuffle_delta_from_discard_to_deck",
            "tests/test_deck_shuffle.py::test_the_same_seed_replays_every_reshuffle_byte_for_byte",
            "tests/test_determinism.py::test_the_same_game_is_byte_identical_across_python_hash_seeds",
            "tests/test_source_discipline.py::test_random_and_secrets_appear_only_in_the_rng_module",
        ),
    ),
    5: DoDItem(
        "基准全绿：票 12 定格的 6 条基准全量逐行通过，索引表与文件集合互相等于",
        (
            "tests/test_replays.py::test_golden_replay_matches_byte_for_byte",
            "tests/test_replays.py::test_readme_index_matches_the_golden_file_set",
            "tests/test_replays.py::test_the_frozen_six_baselines_are_exactly_the_file_set",
        ),
    ),
    6: DoDItem(
        "不变量 fuzz 通过：随机对局驱动的五条不变量 + 终局附则",
        ("tests/test_fuzz.py::test_random_games_satisfy_the_invariants",),
    ),
    7: DoDItem(
        "使用全步骤读作六 + 五时机**逐时机**断言",
        (
            "tests/test_use_and_resolve.py::test_a_slash_use_walks_all_six_l4_timings_in_official_order",
            "tests/test_use_and_resolve.py::test_a_dodge_cancels_the_slash_and_skips_effect_and_after_effect",
            "tests/test_use_and_resolve.py::test_an_uncancelled_slash_walks_effect_and_after_effect_and_pushes_damage",
        ),
    ),
}

COVERAGE_CONFIG_FILES = (RUN_ALL, CONFTEST, PYPROJECT)
COVERAGE_TOKENS = ("coverage", "--cov", "cov-fail-under", "pytest-cov")
"""票 11 §2：**不设行覆盖率数字**——覆盖率会奖励与规则无关的代码。"""

ABSENT_CONFIG_FILES = (".coveragerc", "setup.cfg", "pytest.ini")
"""留给覆盖率配置的文件名（另见 `SECOND_GATE_FILES` 的 `tox.ini`）；一个都不该出现。"""

SECOND_GATE_FILES = (
    "Makefile",
    "makefile",
    "tox.ini",
    "noxfile.py",
    "Jenkinsfile",
    ".pre-commit-config.yaml",
)
"""一旦出现，就是"第二个门禁入口"（票 11 §3：`run_all.sh` 唯一）。"""


# ---------------------------------------------------------------------------
# 七条 DoD 的机械绑定
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def collected_nodeids() -> frozenset[str]:
    """仓库测试树的 node id 集合；``--collect-only`` 只收集、不执行用例。"""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"收集整套测试失败：\n{result.stdout}\n{result.stderr}"
    return frozenset(
        match.group(0)
        for line in result.stdout.splitlines()
        if (match := re.match(r"\S+\.py::\S+", line.strip()))
    )


@pytest.mark.parametrize("number", sorted(DOD_ITEMS))
def test_every_definition_of_done_item_is_established_by_collected_tests(
    number, collected_nodeids
):
    """每条 DoD 的建立者用例都必须仍在 pytest 收集集合里（删除 / 改名即红）。"""
    item = DOD_ITEMS[number]

    assert item.establishers, f"DoD 第 {number} 条「{item.title}」没有绑定任何建立者用例"
    missing = [
        nodeid for nodeid in item.establishers if not _is_collected(nodeid, collected_nodeids)
    ]
    assert not missing, (
        f"DoD 第 {number} 条「{item.title}」的建立者用例不在 pytest 收集集合里：{missing}；"
        f"补回被删 / 改名的用例，或按票据显式修订 DoD 绑定"
    )


def _is_collected(nodeid: str, collected: frozenset[str]) -> bool:
    """参数化用例的 node id 带 ``[...]``，故按前缀匹配基础用例名。"""
    if nodeid in collected:
        return True
    return any(item.startswith(f"{nodeid}[") for item in collected)


def test_the_registry_pins_the_seven_frozen_items():
    """票 11 §2 恰好七条；同一条里不重复登记建立者（跨条目复用是允许的）。"""
    assert sorted(DOD_ITEMS) == list(range(1, 8)), "完成定义恰好七条（票 11 §2）"

    for number, item in DOD_ITEMS.items():
        assert len(item.establishers) == len(set(item.establishers)), (
            f"DoD 第 {number} 条「{item.title}」重复登记了建立者"
        )


# ---------------------------------------------------------------------------
# 门禁语义：唯一入口、pytest 缺失即失败、聚合失败不 fail-fast、非交互
# ---------------------------------------------------------------------------


def _sandbox(tmp_path: Path, *, scripts: dict[str, str], pytest_exit: int | None) -> Path:
    """把 ``run_all.sh`` 复制到临时目录并在那里判决，绝不动真仓库的 venv。"""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    shutil.copyfile(RUN_ALL, tests_dir / "run_all.sh")
    for name, body in scripts.items():
        (tests_dir / name).write_text(body, encoding="utf-8")
    if pytest_exit is not None:
        bin_dir = tmp_path / ".venv" / "bin"
        bin_dir.mkdir(parents=True)
        fake = bin_dir / "pytest"
        fake.write_text(
            f"#!/usr/bin/env bash\necho FAKE_PYTEST_RAN\nexit {pytest_exit}\n", encoding="utf-8"
        )
        fake.chmod(0o755)
    return tests_dir


def _run_gate(tests_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(tests_dir / "run_all.sh")], capture_output=True, text=True
    )


_PASSING_SCRIPT = "#!/usr/bin/env bash\necho SCRIPT_OK\nexit 0\n"


def test_run_all_is_the_single_gate_entry():
    assert RUN_ALL.is_file(), "唯一门禁入口是 tests/run_all.sh"
    for name in DOC_CHECK_SCRIPTS:
        assert (TESTS_DIR / name).is_file(), f"{name} 必须留在门禁里（票 11 §5）"
    assert re.search(r'^testpaths\s*=\s*\["tests"\]', PYPROJECT.read_text(encoding="utf-8"), re.M), (
        "门禁的 pytest 收集整个 tests/；DoD 绑定判决的集合就是它"
    )


def test_the_gate_fails_when_pytest_is_missing(tmp_path):
    tests_dir = _sandbox(tmp_path, scripts={"test_ok.sh": _PASSING_SCRIPT}, pytest_exit=None)

    result = _run_gate(tests_dir)

    assert result.returncode == 1
    assert "pytest 失败" in result.stdout
    assert "ALL TESTS PASSED" not in result.stdout


def test_the_gate_aggregates_failures_without_stopping_early(tmp_path):
    tests_dir = _sandbox(
        tmp_path,
        scripts={
            "test_a.sh": "#!/usr/bin/env bash\necho FAIL_A\nexit 1\n",
            "test_b.sh": "#!/usr/bin/env bash\necho FAIL_B\nexit 1\n",
            "test_z.sh": "#!/usr/bin/env bash\necho PASS_Z\nexit 0\n",
        },
        pytest_exit=0,
    )

    result = _run_gate(tests_dir)

    assert result.returncode == 1
    assert "SOME TESTS FAILED" in result.stdout
    for marker in ("FAIL_A", "FAIL_B", "PASS_Z"):
        assert marker in result.stdout, f"聚合失败要求每个阶段都跑到：{marker} 不在输出里"
    assert "FAKE_PYTEST_RAN" in result.stdout, "前面的脚本失败了，pytest 阶段仍须跑到"


def test_the_gate_passes_when_scripts_and_pytest_pass(tmp_path):
    tests_dir = _sandbox(tmp_path, scripts={"test_ok.sh": _PASSING_SCRIPT}, pytest_exit=0)

    result = _run_gate(tests_dir)

    assert result.returncode == 0, result.stdout
    assert "SCRIPT_OK" in result.stdout
    assert "FAKE_PYTEST_RAN" in result.stdout
    assert "ALL TESTS PASSED" in result.stdout


def test_there_is_no_second_gate_entry():
    for name in SECOND_GATE_FILES:
        assert not (ROOT / name).exists(), f"不得新增第二个门禁入口：{name}"
    assert not (ROOT / ".github" / "workflows").exists(), "本仓库无 CI；门禁只有 run_all.sh"

    offenders = sorted(
        path.relative_to(ROOT).as_posix()
        for path in (*ROOT.glob("*.sh"), *TESTS_DIR.glob("*.sh"))
        if path != RUN_ALL and re.search(r"\bpytest\b", path.read_text(encoding="utf-8"))
    )
    assert offenders == [], f"仓库里唯一调用 pytest 的脚本必须是 run_all.sh：{offenders}"


def test_the_gate_is_non_interactive():
    """不设人工验收（票 11 §2）：门禁只靠退出码判决，不向 stdin 要输入。

    这是**源码纪律扫描**（先例：``tests/test_source_discipline.py``），因为"等不到输入"
    无法在非交互测试里行为性地证明。
    """
    text = RUN_ALL.read_text(encoding="utf-8")

    assert not re.search(r"\bread\s+-", text), "门禁不得等待人工输入"
    assert not re.search(r"^\s*select\s", text, re.MULTILINE), "门禁不得做交互式选择"


def test_no_line_coverage_number_is_configured():
    for path in COVERAGE_CONFIG_FILES:
        lowered = path.read_text(encoding="utf-8").lower()
        for token in COVERAGE_TOKENS:
            assert token not in lowered, (
                f"{path.name} 出现覆盖率配置 {token!r}（票 11 §2：不设行覆盖率数字）"
            )
    for name in ABSENT_CONFIG_FILES:
        assert not (ROOT / name).exists(), f"不得新增覆盖率配置文件：{name}"
