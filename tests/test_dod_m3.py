"""M3 完成定义收口：八条 DoD 逐条机械成立（票 15）。

本文件把本 effort 的 `spec.md`「Acceptance：M3 Definition of Done」八条钉成**可判决的断言**：

1. ``DOD_M3_ITEMS`` 把每条 DoD 绑定到**建立它的用例 node id**；
   ``test_every_m3_definition_of_done_item_is_established_by_collected_tests`` 断言
   这些 node id 仍在**仓库测试树**的 pytest 收集集合里——删掉或改名建立者即红。
   集合由一次 ``pytest --collect-only``（不执行用例）得到，故单跑本文件也能判决。
   绑定只挡"建立者被删 / 改名"；建立者本身是否仍**断言得够狠**由门禁里的这些用例负责。
2. 文档级条目（机制族矩阵 A–I 逐格落点、ADR-008～011、文档门禁的 M3 白名单与七行账本、
   冻结面表、`06` 第 5 节的七行五字段）另有**直接的字面断言**，故"文档被回退"同样判红。

递归禁令：本文件**不**调用仓库里的 ``tests/run_all.sh``（那会在 pytest 里再起 pytest）。
M2 的八条同形态登记在 ``tests/test_dod.py``；两条 DoD 同为纯机械、不设人工验收。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
MILESTONES_DOC = ROOT / "开发文档" / "06-里程碑与风险.md"
DATA_MODEL_DOC = ROOT / "开发文档" / "03-数据模型与技能描述.md"
DOC_GATE = TESTS_DIR / "test_dev_docs.sh"


class DoDItem(NamedTuple):
    """一条 M3 完成定义：中文条目 + 建立它的用例 node id。"""

    title: str
    establishers: tuple[str, ...]


DOD_M3_ITEMS: dict[int, DoDItem] = {
    1: DoDItem(
        "机制族矩阵 A–I 逐格有落点：每族至少一个族级构造局面单测文件，"
        "且文件里有**至少两条**点名它的用例（spec Acceptance 第 1 行）",
        (
            "tests/test_dod_m3.py::test_the_family_matrix_a_to_i_each_has_a_landing_file",
            "tests/test_hero_catalog.py::test_every_enabled_hero_skill_is_named_by_a_family_test_file",
            "tests/test_hero_active.py::test_every_declared_active_skill_has_a_handler_and_a_catalog_definition",
        ),
    ),
    2: DoDItem(
        "16 名武将全部登记且每条技能有断言：CATALOG 武将半恰等于数据文件启用名单、"
        "落选 9 名继续暗置、29 条技能逐条被族级文件点名",
        (
            "tests/test_hero_catalog.py::test_the_catalog_holds_seventeen_equipment_and_twenty_nine_hero_skills",
            "tests/test_hero_catalog.py::test_the_hero_half_of_the_catalog_is_exactly_the_enabled_hero_sheet",
            "tests/test_hero_catalog.py::test_the_enabled_pool_is_exactly_the_sixteen_and_the_rest_stay_dark",
            "tests/test_hero_catalog.py::test_every_enabled_hero_skill_is_named_by_a_family_test_file",
            "tests/test_hero_catalog.py::test_every_hero_matches_the_rule_book_stats",
        ),
    ),
    3: DoDItem(
        "全 AI 局 32 种子通过：固定 5 人、无人干预、跑完且终局与身份胜负一致、零非法应答、可回放",
        (
            "tests/test_all_ai_games.py::test_the_all_ai_heuristic_group_finishes_consistently_and_replays",
            "tests/test_heuristic_ai.py::test_the_full_ai_sweep_of_thirty_two_seeds_never_answers_illegally_or_crashes",
        ),
    ),
    4: DoDItem(
        "四轴计数 65/54/18/11 与规则级账本的『时机四轴计数』段同步："
        "实现 65 = 白名单 54 + 空日志 11、枚举位 18；解封集合恰为 `MOVE_AFTER`",
        (
            "tests/test_timing.py::test_implemented_and_enum_only_partition_the_whole_table",
            "tests/test_timing.py::test_glossary_timing_counts_track_the_enum_partitions",
            "tests/test_timing.py::test_the_m3_unseal_is_exactly_move_after",
            "tests/test_timing_coverage.py::test_the_unsealed_set_is_exactly_the_move_after_node",
        ),
    ),
    5: DoDItem(
        "ADR-008～011 已落 `06` 第 4 节：四条在 ADR 表内、各配一个变更说明块",
        ("tests/test_dod_m3.py::test_the_four_m3_adrs_are_in_the_milestones_doc",),
    ),
    6: DoDItem(
        "文档门禁（含 `06:M3` 白名单）全绿：白名单条目条条命中，`06` 第 5 节的七行 M3 账本"
        "逐行五字段齐全、票号与地图裁定交叉引用一致",
        (
            "tests/test_dod_m3.py::test_the_doc_gate_whitelists_the_m3_milestone_and_the_seven_ledger_rows",
            "tests/test_dod_m3.py::test_the_seven_m3_ledger_rows_carry_all_five_fields",
            "tests/test_dod.py::test_run_all_is_the_single_gate_entry",
        ),
    ),
    7: DoDItem(
        "`03` 第 4 节的冻结面表已就位：票 07 的指针已就地兑现，且加法组（`HERO` / `skill_type` / "
        "`per_point` / `RESPONSE_COUNT` / `RECOVER_AMOUNT`）在表内",
        ("tests/test_dod_m3.py::test_the_frozen_surface_table_is_in_the_data_model_doc",),
    ),
    8: DoDItem(
        "无武将模式回到 M2 行为：34 条基准逐字节重放、体力上限回落引擎常数、"
        "新增可见性字段不进序列化",
        (
            "tests/test_replays.py::test_golden_replay_matches_byte_for_byte",
            "tests/test_hero_dealing.py::test_no_heroes_falls_back_to_the_engine_constant",
            "tests/test_hero_dealing.py::test_the_new_visibility_fields_do_not_change_the_serialized_journal",
        ),
    ),
}

# 机制族 A–I 的落点：族 → （族级构造局面单测文件，**至少两条**点名该族的用例字面串）。
# 结构刻意表达"至少两条"（`MIN_FAMILY_LITERALS`），不是恰好一对可被缩到一条的硬编码：
# `spec.md`「Acceptance」第 1 行要求每族 **≥2 条实测**（R2-Q5 的"每族≥2 条"）。
class FamilyLanding(NamedTuple):
    """一个机制族的落点：族级文件 + 该文件里点名本族的用例名。"""

    file: str
    literals: tuple[str, ...]


MIN_FAMILY_LITERALS = 2
"""每族至少被点名几条用例（`spec.md` Acceptance 第 1 行；票 14 §8 / R2-Q5）。"""


FAMILY_LANDINGS: dict[str, FamilyLanding] = {
    "A": FamilyLanding(
        "tests/test_hero_active.py",
        (
            "test_rende_recovers_when_two_cards_are_given_for_the_first_time",
            "test_zhiheng_discards_by_source_zone_and_draws_the_same_count",
        ),
    ),
    "B": FamilyLanding(
        "tests/test_hero_damage_after.py",
        (
            "test_two_points_of_damage_ask_yiji_twice_and_jianxiong_once",
            "test_jianxiong_takes_the_card_that_dealt_the_damage",
        ),
    ),
    "C": FamilyLanding(
        "tests/test_hero_target_pool.py",
        (
            "test_kongcheng_without_hand_cards_leaves_the_slash_pool",
            "test_qianxun_leaves_the_snatch_and_indulgence_pools_even_with_hand_cards",
        ),
    ),
    "D": FamilyLanding(
        "tests/test_hero_move_after.py",
        (
            "test_lianying_draws_when_the_hand_empties_on_use",
            "test_xiaoji_fires_when_equipment_is_taken_by_snatch",
        ),
    ),
    "E": FamilyLanding(
        "tests/test_hero_response_window.py",
        (
            "test_wushuang_needs_two_dodges_to_cancel_a_slash",
            "test_tieqi_red_hides_the_whole_dodge_window",
        ),
    ),
    "F": FamilyLanding(
        "tests/test_hero_lord.py",
        (
            "test_hujia_asks_counterclockwise_from_the_turn_seat_and_stops_at_the_first_success",
            "test_jiuyuan_adds_one_when_a_wu_character_rescues_sunquan_with_a_peach",
        ),
    ),
    "G": FamilyLanding(
        "tests/test_hero_arrange.py",
        (
            "test_guanxing_reveals_the_top_x_cards_and_places_them_by_order_and_split",
            "test_guanxing_puts_the_remainder_below_the_existing_deck_bottom",
        ),
    ),
    "H": FamilyLanding(
        "tests/test_hero_transform.py",
        (
            "test_wusheng_offers_a_red_card_as_a_slash_inheriting_suit_rank_and_color",
            "test_longdan_offers_a_hand_slash_as_a_dodge_when_a_dodge_is_needed",
        ),
    ),
    "I": FamilyLanding(
        "tests/test_hero_rejudge.py",
        (
            "test_a_single_rejudge_replaces_the_judge_card_and_changes_the_result",
            "test_tiandu_takes_the_final_card_after_a_rejudge_and_not_the_discarded_original",
        ),
    ),
}

# `06` 第 5 节的七行 M3 规则级账本：行内锚定串 → 票号列里必须出现的裁定来源。
M3_LEDGER_ROWS: dict[str, str] = {
    "改一个值用修饰器、加一条禁令 / 标志用帧标签": "R4-Q1",
    '**"视为使用"也是使用**': "票 08",
    "顶替算失去": "R3-Q5",
    "阶段体等价": "R6-Q1",
    "B 族第四形态缺席": "R6-Q3",
    "`MOVE_AFTER` 解封的不对称": "ADR-010",
    "拼点 / 翻面 / 横置 / 标记零载体": "R1-Q4",
}

# 文档门禁里逐行钉住这七行的 `check` 描述串（`tests/test_dev_docs.sh`）。
M3_LEDGER_GATE_CHECKS: tuple[str, ...] = (
    "M3 账本：帧标签与修饰器的分界",
    "M3 账本：顶替算失去",
    "M3 账本：阶段体等价（准备 / 结束阶段）",
    "M3 账本：B 族第四形态缺席",
    "M3 账本：MOVE_AFTER 只解封 after",
    "M3 账本：拼点 / 翻面 / 横置 / 标记零载体",
    "M3 账本：铁骑封『视为使用』",
)


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


def _is_collected(nodeid: str, collected: frozenset[str]) -> bool:
    """参数化用例的 node id 带 ``[...]``，故按前缀匹配基础用例名。"""
    if nodeid in collected:
        return True
    return any(item.startswith(f"{nodeid}[") for item in collected)


@pytest.mark.parametrize("number", sorted(DOD_M3_ITEMS))
def test_every_m3_definition_of_done_item_is_established_by_collected_tests(
    number, collected_nodeids
):
    """每条 M3 DoD 的建立者用例都必须仍在 pytest 收集集合里（删除 / 改名即红）。"""
    item = DOD_M3_ITEMS[number]

    assert item.establishers, f"M3 DoD 第 {number} 条「{item.title}」没有绑定任何建立者用例"
    missing = [
        nodeid for nodeid in item.establishers if not _is_collected(nodeid, collected_nodeids)
    ]
    assert not missing, (
        f"M3 DoD 第 {number} 条「{item.title}」的建立者用例不在 pytest 收集集合里：{missing}；"
        f"补回被删 / 改名的用例，或按票面显式修订 DoD 绑定"
    )


def test_the_registry_pins_the_eight_m3_items():
    """票 15 的 DoD 恰好八条；同一条里不重复登记建立者（跨条目复用是允许的）。"""
    assert sorted(DOD_M3_ITEMS) == list(range(1, 9)), "M3 完成定义恰好八条（票 15）"

    for number, item in DOD_M3_ITEMS.items():
        assert len(item.establishers) == len(set(item.establishers)), (
            f"M3 DoD 第 {number} 条「{item.title}」重复登记了建立者"
        )


def test_the_family_matrix_a_to_i_each_has_a_landing_file():
    """机制族矩阵 A–I 逐格有落点：每族的族级文件存在且**至少两条**点名它的用例。

    `spec.md` Acceptance 第 1 行要求"每族 ≥2 条实测"；本用例用 ``MIN_FAMILY_LITERALS``
    把这个下界写成可判决的断言（不是恰好一对可被悄悄缩水的硬编码），并逐条校验用例名
    真的以 ``def <name>(`` 出现在族级文件里——"文件里提过这个名字"不算数。
    """
    assert set(FAMILY_LANDINGS) == set("ABCDEFGHI"), "A–I 恰九族（spec 的必含集）"

    thin = {
        family: len(landing.literals)
        for family, landing in FAMILY_LANDINGS.items()
        if len(landing.literals) < MIN_FAMILY_LITERALS
    }
    assert not thin, (
        f"机制族 {sorted(thin)} 点名的用例少于 {MIN_FAMILY_LITERALS} 条（实得 {thin}）——"
        f"spec Acceptance 第 1 行要求每族 ≥{MIN_FAMILY_LITERALS} 条实测"
    )

    for family, landing in FAMILY_LANDINGS.items():
        target = ROOT / landing.file
        assert target.is_file(), f"机制族 {family} 的落点文件不存在：{landing.file}"
        text = target.read_text(encoding="utf-8")
        for literal in landing.literals:
            assert f"def {literal}(" in text, (
                f"机制族 {family} 的落点文件 {landing.file} 里没有用例「{literal}」——"
                f"该族的构造局面单测被删 / 改名即红"
            )


def test_the_four_m3_adrs_are_in_the_milestones_doc():
    """ADR-008～011 已落 `06` 第 4 节：表内四行 + 四个变更说明块。"""
    text = MILESTONES_DOC.read_text(encoding="utf-8")

    assert "## 4. 决策记录（ADR）" in text, "`06` 第 4 节的标题变了"
    for number in range(8, 12):
        assert f"| ADR-{number:03d} |" in text, f"ADR-{number:03d} 不在 `06` 第 4 节的表内"
        assert f"**ADR-{number:03d} 变更说明" in text, f"ADR-{number:03d} 缺变更说明块"


def test_the_frozen_surface_table_is_in_the_data_model_doc():
    """`03` 第 4 节的冻结面表已就位：票 07 的指针就地兑现，M3 加法组在表内。"""
    text = DATA_MODEL_DOC.read_text(encoding="utf-8")

    assert "## 4. 技能描述模型" in text, "`03` 第 4 节的标题变了"
    assert "### 技能定义 / 实例 / 派发的冻结面" in text, "冻结面表不在 `03` 第 4 节里"
    assert "就地兑现" in text, "冻结面表缺『就地兑现票 07 指针』的说明"
    for addition in ("HERO", "skill_type", "per_point", "RESPONSE_COUNT", "RECOVER_AMOUNT"):
        assert addition in text, f"冻结面加法组缺 {addition}"


def test_the_doc_gate_whitelists_the_m3_milestone_and_the_seven_ledger_rows():
    """文档门禁含 `06:M3` 白名单，且七行 M3 账本逐行有 `check`。

    M4 票 00 扩白名单时把这条从"逐字等于三个里程碑"改成"**含** `06:M3`"——
    白名单会随后续里程碑增长，逐字钉死会让每次扩白名单都来改 M3 的验收脚本
    （那不是在验收 M3，是在制造假耦合）。真正该钉的是"`06:M3` 在里面"。
    """
    text = DOC_GATE.read_text(encoding="utf-8")

    assert 'MILESTONE_WHITELIST="|' in text, "文档门禁没有里程碑白名单"
    _, _, rest = text.partition('MILESTONE_WHITELIST="|')
    whitelist = f"{rest.split('"', 1)[0]}|"
    assert "06:M3|" in whitelist, "文档门禁缺 `06:M3` 白名单"
    for description in M3_LEDGER_GATE_CHECKS:
        assert description in text, f"文档门禁没有断言『{description}』"


def test_the_seven_m3_ledger_rows_carry_all_five_fields():
    """`06` 第 5 节的七行 M3 账本逐行五字段齐全，票号与地图裁定交叉引用一致。"""
    lines = MILESTONES_DOC.read_text(encoding="utf-8").splitlines()

    assert len(M3_LEDGER_ROWS) == 7, "M3 规则级账本恰好七行（票 15 点名的七条）"
    for marker, expected_reference in M3_LEDGER_ROWS.items():
        matches = [line for line in lines if line.startswith("|") and marker in line]
        assert len(matches) == 1, f"账本行锚定串『{marker}』命中 {len(matches)} 行（应为 1）"
        cells = [cell.strip() for cell in matches[0].strip().strip("|").split("|")]
        assert len(cells) == 5, (
            f"账本行『{marker}』的字段数为 {len(cells)}（应为 5：裁定 / 来源类型 / "
            f"一手出处与偏离点 / 票号 / 落地位置）"
        )
        assert all(cells), f"账本行『{marker}』有空字段：{cells}"
        assert expected_reference in cells[3], (
            f"账本行『{marker}』的票号列缺裁定来源『{expected_reference}』：{cells[3]}"
        )
