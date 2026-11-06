"""M2 完成定义收口：八条 DoD 逐条机械成立（票 17 §5 / 票 32 §5）。

本文件不实现任何引擎行为，只把票 17 §5 的八条"完成"钉成**可判决的断言**：

1. ``DOD_ITEMS`` 把每条 DoD 绑定到**建立它的用例 node id**；
   ``test_every_definition_of_done_item_is_established_by_collected_tests`` 断言
   这些 node id 仍在**仓库测试树**的 pytest 收集集合里——删掉或改名建立者即红。
   集合由一次 ``pytest --collect-only``（不执行用例）得到，故单跑本文件也能判决。
   绑定只挡"建立者被删 / 改名"；建立者本身是否仍**断言得够狠**由门禁里的这些用例负责。
2. ``tests/run_all.sh`` 的门禁语义（pytest 缺失即失败、聚合失败不 fail-fast、唯一入口、
   非交互）在**临时沙箱**里真的跑一遍判决。

递归禁令：本文件**不**调用仓库里的 ``tests/run_all.sh``（那会在 pytest 里再起 pytest）。
沙箱判决的是门禁脚本的**行为**；真实门禁的全绿由交付流程跑一次 ``tests/run_all.sh`` 得到。

M2 与 M1 的差别：条目 4 换成 M2 内容组（数据 108 / 六区 / 身份 / 判定 / 无懈 / 装备 /
多人濒死 / 胜负 / 技能 / 视角 / RNG 纪律），条目 5 从"六条基准"换成"34 条 + 分布判据"，
条目 8 从"三分区"换成"53/64/19/11 + 常量去 M1 前缀"。
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
        "可执行入口：两个入口同源、同种子逐字节一致、跑完整局、退出码 0、省略种子时生成并打印、"
        "`--players N` 每档跑到终局、非法人数退出码 1",
        (
            "tests/test_cli.py::test_module_entry_is_the_same_function",
            "tests/test_cli.py::test_console_script_points_at_the_same_entry_function",
            "tests/test_cli.py::test_module_entry_runs",
            "tests/test_cli.py::test_console_script_runs",
            "tests/test_cli.py::test_module_entry_and_console_script_agree_byte_for_byte",
            "tests/test_cli.py::test_cli_runs_a_whole_game_and_prints_the_seed_then_the_stream",
            "tests/test_cli.py::test_omitted_seed_is_generated_recorded_and_printed",
            "tests/test_cli.py::test_a_generated_seed_replays_the_same_stream",
            "tests/test_cli.py::test_each_supported_seat_count_runs_a_whole_game",
            "tests/test_cli.py::test_an_unsupported_seat_count_reports_to_stderr_and_exits_1",
            "tests/test_cli.py::test_unsupported_player_count_exit_code_in_subprocess",
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
        "记录流可比对：canonical JSONL v2、同种子逐字节、回放即重跑比对、仓库零 v1 文件",
        (
            "tests/test_journal.py::test_canonical_jsonl_is_lf_sorted_compact_unescaped_and_newline_terminated",
            "tests/test_journal.py::test_same_seed_twice_is_byte_identical",
            "tests/test_replays.py::test_replaying_the_tape_reproduces_the_journal_byte_for_byte",
            "tests/test_replays.py::test_the_repository_contains_no_pre_v2_journals",
            "tests/test_determinism.py::test_the_same_game_is_byte_identical_across_python_hash_seeds",
        ),
    ),
    4: DoDItem(
        "pytest 集合（M2 内容组）：数据 108 / 六区 / 身份 / 判定 / 无懈 / 装备距离转化 /"
        " 多人濒死 / 死亡奖惩 / 胜负 / 技能 / 视角过滤 / RNG 与源码纪律",
        (
            # 数据加载：标准 108 与 53 张回归切片（票 22 / 票 32）
            "tests/data/test_deck_snapshot.py::test_deck_matches_the_53_card_snapshot",
            "tests/data/test_deck_snapshot.py::test_deck_counts_by_type",
            "tests/data/test_load_validation.py::test_loads_standard_content_pack",
            "tests/data/test_load_validation.py::test_loads_the_standard_108_profile_with_all_its_declarations",
            # 时机枚举与 dispatch 白名单（票 17 §3 / 票 32 §3）
            "tests/test_timing.py::test_enum_has_exactly_84_nodes",
            "tests/test_timing_coverage.py::test_the_union_of_actual_record_streams_is_exactly_the_dispatch_whitelist",
            # 六区模型（判定区 / 装备区）与区域坐标（票 08 / 票 21）
            "tests/test_zones.py::test_the_basic_53_cards_carry_no_equipment_slot",
            # 身份发放、模式派生与可见性（票 24）
            "tests/test_identity.py::test_a_run_reaches_game_end_in_every_identity_seat_count",
            "tests/test_identity.py::test_identity_is_visible_to_self_the_lord_and_the_dead_only",
            "tests/test_identity_rosters.py::test_identity_labels_cover_exactly_the_four_identities",
            # 判定帧与延时锦囊（票 26）
            "tests/test_judgment.py::test_the_judge_frame_is_a_real_frame_with_the_three_dispatched_timings",
            "tests/test_judgment.py::test_lightning_passes_counterclockwise_to_the_first_legal_judge_zone",
            # 无懈响应链（票 28）
            "tests/test_nullify.py::test_a_nullify_of_a_nullify_restores_the_original_trick",
            "tests/test_nullify.py::test_a_nullified_lightning_moves_to_the_next_judge_zone_without_a_judgment",
            # 装备 / 距离 / 攻击范围 / 虚拟牌（票 29 / 票 30）
            "tests/test_equipment.py::test_a_weapon_makes_a_farther_target_legal_for_slash",
            "tests/test_equipment.py::test_replacing_an_equipment_moves_the_old_card_to_the_discard_pile_as_a_loss",
            "tests/test_skills.py::test_serpent_spear_offers_a_transformed_slash_candidate",
            # 多目标与普通锦囊（票 27）
            "tests/test_normal_tricks.py::test_a_multi_target_use_runs_one_resolve_frame_with_a_cursor_per_target",
            "tests/test_normal_tricks.py::test_harvest_reveals_target_count_and_each_target_picks_one",
            # 多人濒死与新回退（票 31）
            "tests/test_near_death_rollback.py::test_a_peach_that_leaves_hp_below_one_rolls_back_to_the_same_start",
            "tests/test_near_death_rollback.py::test_the_new_window_starts_at_the_responder_and_skips_seats_already_asked",
            # 死亡奖惩与身份胜负（票 25）
            "tests/test_victory.py::test_killing_a_rebel_draws_three_cards_for_the_killer",
            "tests/test_victory.py::test_the_lord_killing_a_loyalist_discards_all_of_the_lords_own_cards",
            "tests/test_victory.py::test_simultaneous_deaths_are_announced_only_after_the_last_death_of_the_batch",
            "tests/test_multiplayer.py::test_every_identity_seat_count_reaches_game_end_with_a_side_and_winners",
            # 结算栈、终局收尾、Query 与非法行动（票 04 / 票 07 / 票 24）
            "tests/test_resolution_stack.py::test_engine_names_the_return_point_and_discards_frames_innermost_first",
            "tests/test_death.py::test_the_teardown_moves_leftover_processing_cards_into_the_discard_pile",
            "tests/test_death.py::test_card_conservation_holds_unconditionally_at_game_end",
            "tests/test_queries_views_actions.py::test_structure_damaged_responses_are_malformed_and_change_nothing",
            "tests/test_queries_views_actions.py::test_a_stale_answers_seq_is_rejected_and_changes_nothing",
            "tests/test_queries_views_actions.py::test_an_answer_outside_the_candidate_set_is_rejected_and_changes_nothing",
            "tests/test_cli.py::test_bad_content_pack_exit_code_in_subprocess",
            "tests/test_cli.py::test_illegal_action_exit_code_in_subprocess",
            "tests/test_cli.py::test_malformed_action_exit_code_in_subprocess",
            # 视角过滤（票 05 / 票 13）
            "tests/test_queries_views_actions.py::test_player_view_exposes_only_public_information_and_own_hand",
            "tests/test_near_death.py::test_the_peach_query_view_hides_the_opponents_hand_identities",
            # RNG 子流与源码纪律（票 08 / 票 30）
            "tests/test_rng.py::test_consuming_the_ai_stream_does_not_move_the_shuffle_stream",
            "tests/test_deck_shuffle.py::test_the_same_seed_replays_every_reshuffle_byte_for_byte",
            "tests/test_source_discipline.py::test_random_and_secrets_appear_only_in_the_rng_module",
            "tests/test_source_discipline.py::test_engine_contains_no_concrete_skill_id_or_equipment_name",
        ),
    ),
    5: DoDItem(
        "基准全绿 + 分布判据达标：34 条全量逐行通过、七类每类达标、索引 == 文件集合 == 类表",
        (
            "tests/test_replays.py::test_golden_replay_matches_byte_for_byte",
            "tests/test_replays.py::test_readme_index_matches_the_golden_file_set",
            "tests/test_replays.py::test_the_frozen_m1_six_stay_in_the_file_set",
            "tests/test_replays.py::test_the_baseline_distribution_meets_every_category_minimum",
            "tests/test_replays.py::test_every_baseline_is_classified_and_the_registry_has_no_orphan",
            "tests/test_replays.py::test_every_m2_baseline_has_an_after_the_fact_goal",
            "tests/test_replays.py::test_the_frozen_m1_decision_trajectories_are_unchanged",
        ),
    ),
    6: DoDItem(
        "不变量 fuzz 通过：六区座位级不变量 + 阵亡者三区为空 + 装备槽位 / 判定区同名唯一"
        " + 终局身份-胜负一致 + 六档人数轮转 + 每局必达终局",
        (
            "tests/test_fuzz.py::test_random_games_satisfy_the_invariants",
            "tests/test_fuzz.py::test_the_player_rotation_covers_the_six_identity_branches",
            "tests/test_fuzz.py::test_the_live_checker_catches_a_dead_player_holding_cards",
        ),
    ),
    7: DoDItem(
        "使用全步骤读作六 + 五时机**逐时机**断言",
        (
            "tests/test_use_and_resolve.py::test_a_slash_use_walks_all_six_l4_timings_in_official_order",
            "tests/test_use_and_resolve.py::test_a_dodge_cancels_the_slash_and_skips_effect_and_after_effect",
            "tests/test_use_and_resolve.py::test_an_uncancelled_slash_walks_effect_and_after_effect_and_pushes_damage",
        ),
    ),
    8: DoDItem(
        "枚举纪律：IMPLEMENTED 64 / ENUM_ONLY 19 / EMPTY_LOG 11 逐组断言，白名单 53 实测等于"
        "记录流并集，常量去 M1 前缀",
        (
            "tests/test_timing.py::test_implemented_and_enum_only_partition_the_whole_table",
            "tests/test_timing.py::test_the_dispatch_whitelist_and_empty_log_nodes_partition_the_implemented_set",
            "tests/test_timing.py::test_the_six_m2_nodes_are_dispatchable",
            "tests/test_timing.py::test_no_timing_constant_keeps_the_m1_prefix",
            "tests/test_timing_coverage.py::test_the_coverage_list_keys_are_exactly_the_dispatch_whitelist",
            "tests/test_timing_coverage.py::test_enum_only_and_empty_log_nodes_never_appear_in_any_record_stream",
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
# 八条 DoD 的机械绑定
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


def test_the_registry_pins_the_eight_frozen_items():
    """票 17 §5 恰好八条；同一条里不重复登记建立者（跨条目复用是允许的）。"""
    assert sorted(DOD_ITEMS) == list(range(1, 9)), "完成定义恰好八条（票 17 §5）"

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


def test_the_per_test_timeout_guardrail_is_wired():
    """retro S1：per-test 超时是**三件套**，缺一件就静默失效。

    `[tool.pytest.ini_options] timeout` 只在 `pytest-timeout` 装了时才生效（没装时 pytest
    只发一条 config warning）—— 于是"活锁"会悄悄退回"永远等"，正是 retro 要防的那种
    "检查在位但没接上"。故三者一起断言：dev 依赖声明 + 配置值 + 当前解释器能 import。
    """
    import importlib.util
    import tomllib

    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dev = config["project"]["optional-dependencies"]["dev"]
    assert any(dep.replace(" ", "").startswith("pytest-timeout") for dep in dev), (
        "dev 依赖必须声明 pytest-timeout"
    )
    timeout = config["tool"]["pytest"]["ini_options"].get("timeout")
    assert isinstance(timeout, int) and timeout > 0, f"必须设正的 per-test 超时，实得 {timeout!r}"
    assert importlib.util.find_spec("pytest_timeout") is not None, (
        "当前解释器没有 pytest-timeout ⇒ timeout 静默失效；"
        "装上：python3 -m pip --python .venv/bin/python install -e '.[dev]'"
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
