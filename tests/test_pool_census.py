"""将池构成清单：零漂移绊线 + 扫描失效绊线 + 派生量被覆盖的绊线。

工具本身（``tools/pool_census.py``）是**只读**的：它重算数据面的派生量，再把「加一个武将
必然要改的那些数字」逐个从断言里找出来对账。本文件**只 import 它的函数**（不重复实现扫描），
断三件事：

1. **当前零漂移**：加武将 / 改池时先红在这里，而不是等一轮 6:35 的全量门禁；
2. **扫描没失效**：命中站点数 ≥ :data:`pool_census.MIN_SITE_HITS`，且每条具名规则都至少
   命中一处——正则被改坏 / 断言搬了家时这里红，不会「扫到 0 处 ⇒ 空转全绿」；
3. **派生量没有漏网**：每个重算出来的数字都必须有站点或登记表在对账（防「少扫一处」）。

另用一份合成文本证明「错值会红、对值会绿」（防恒红 / 恒绿两种坏法）。

设计依据是 ``.scratch/pool-census/`` 下的工单与规格——那个目录是 gitignored 的一次性产物，
本文件**不依赖**它（干净 clone 上照样跑）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_TOOLS = str(ROOT / "tools")
if _TOOLS not in sys.path:  # 只读工具不是包，只能按目录导入
    sys.path.insert(0, _TOOLS)

import pool_census  # noqa: E402 - 测试在 import 前把 tools/ 放上 sys.path
from sgs.data import enabled_hero_ids  # noqa: E402


def test_the_census_finds_no_drift_in_the_current_tree() -> None:
    """当前零漂移：任一站点写了别的数字，这条红（比跑一轮全量门禁快两个数量级）。"""
    report = pool_census.collect()

    assert report.ok, pool_census.render(report)


def test_the_scan_hits_at_least_ten_sites_and_no_named_rule_goes_blind() -> None:
    """绊线：站点数 ≥ 10，且 ``SITES`` 里每条具名规则都至少命中一处。"""
    report = pool_census.collect()

    assert len(report.sites) >= pool_census.MIN_SITE_HITS, (
        f"只命中 {len(report.sites)} 处站点（下限 {pool_census.MIN_SITE_HITS}），扫描可能失效"
    )
    blind = [site.key for site in report.blind_sites()]
    assert not blind, f"这些具名规则一处也没命中（断言搬了家 / 正则被改坏）：{blind}"


def test_every_recomputed_count_is_reconciled_by_a_site_or_a_table() -> None:
    """绊线：每个数据面重算出来的数字，都得有站点或登记表在对账（防「少扫一处」）。

    这条来自评审抓到的真实漏网：``len(equipment) == 22`` 与 ``len(SHAPE_COUNTS) == 11``
    原先没有任何规则命中——工具会打印 22，却不对账任何站点。
    """
    report = pool_census.collect()
    reconciled = {
        metric for site in pool_census.SITES for metric in site.pattern.groupindex
    }
    reconciled |= {
        reading.metric
        for table in report.tables
        for cell in table.hits
        for reading in cell.readings
    }

    uncovered = sorted(set(report.derived) - reconciled)
    assert not uncovered, f"这些派生量没有任何站点 / 登记表在对账：{uncovered}"


def test_a_wrong_count_is_reported_with_its_line_and_both_numbers() -> None:
    """合成反例：``len(CATALOG) == 164`` ⇒ 一处带行号的漂移，现值 164 / 应为 165。"""
    site = pool_census.SITES_BY_KEY["catalog_total"]

    hits = pool_census.scan_text(
        site,
        "    assert len(CATALOG) == 164\n",
        {"catalog": 165},
        filename="tests/test_hero_catalog.py",
    )

    assert len(hits) == 1
    hit = hits[0]
    assert not hit.ok
    assert (hit.file, hit.line) == ("tests/test_hero_catalog.py", 1)
    assert [(reading.metric, reading.value, reading.expected) for reading in hit.readings] == [
        ("catalog", 164, 165)
    ]


def test_the_right_count_is_reported_as_ok_and_only_the_matching_line_is_hit() -> None:
    """同一段文本里别的断言不该被这条规则抓进来（规则是具名的，不是"扫所有数字"）。"""
    site = pool_census.SITES_BY_KEY["catalog_total"]

    hits = pool_census.scan_text(
        site,
        "assert len(CATALOG) == 165\nassert len(heroes) == 143\n",
        {"catalog": 165},
        filename="tests/test_hero_catalog.py",
    )

    assert [hit.line for hit in hits] == [1]
    assert hits[0].ok


def test_the_check_flag_is_green_and_the_json_view_carries_the_same_facts(capsys) -> None:
    """两档机器接口：``--check`` 以 0 退出；``--json`` 是同一份事实（含数据面重算值）。"""
    assert pool_census.main(["--check"]) == 0
    capsys.readouterr()

    assert pool_census.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["derived"]["versions"] == len(enabled_hero_ids())
    assert len(payload["sites"]) >= pool_census.MIN_SITE_HITS

# ---------------------------------------------------------------------------
# 池敏感登记面（2026-11-13 加）：AI 语义表 / 固定种子 / 武将表并集
# ---------------------------------------------------------------------------


def test_the_pack_union_check_flags_a_file_that_misses_a_pack(
    monkeypatch, tmp_path
) -> None:
    """新包进了 `HERO_SUPPLEMENT_PACK_IDS`、却没进某份并集 ⇒ 红（本批 `shan` 的教训）。"""
    from sgs.data import HERO_SUPPLEMENT_PACK_IDS

    complete = tmp_path / "complete.py"
    complete.write_text(
        "\n".join(f'X = load_heroes("{pack_id}")' for pack_id in HERO_SUPPLEMENT_PACK_IDS)
        + "\nA = load_heroes()\n",
        encoding="utf-8",
    )
    incomplete = tmp_path / "incomplete.py"
    incomplete.write_text(
        "\n".join(
            f'X = load_heroes("{pack_id}")' for pack_id in HERO_SUPPLEMENT_PACK_IDS[:-1]
        )
        + "\nA = load_heroes()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        pool_census, "PACK_UNION_FILES", (str(complete), str(incomplete))
    )

    failures: list[str] = []
    table = pool_census._pack_union_check(failures)

    assert not table.ok, "缺一个包的并集必须判红"
    assert any(HERO_SUPPLEMENT_PACK_IDS[-1] in message for message in failures), (
        f"失败消息里要指名缺的是哪个包；实得 {failures}"
    )


def test_the_skill_values_check_flags_a_missing_entry(monkeypatch) -> None:
    """AI 语义表少一条 ⇒ 红（加武将时只在 8 分钟门禁里爆的那条）。"""
    from sgs.agents import heuristic
    from sgs.data import enabled_hero_ids, hero_skill_id, load_hero_tables
    from sgs.skills import CATALOG

    victim = "hero.zuoci.xinsheng"
    table = load_hero_tables()
    definition = CATALOG[victim]
    assert not definition.locked, "构造前提：拿一条非锁定技当缺条目的样本"
    pruned = {
        key: value for key, value in heuristic.SKILL_VALUES.items() if key != victim
    }
    monkeypatch.setattr(heuristic, "SKILL_VALUES", pruned)

    failures: list[str] = []
    table_check = pool_census._skill_values_check(failures)

    assert not table_check.ok, "缺条目必须判红"
    assert any(victim in message for message in failures), failures


def test_the_closeout_seed_check_reports_a_broken_scan(monkeypatch, tmp_path) -> None:
    """读不到种子 / 玩家数（文件被改形）⇒ 红，而不是空转通过。"""
    broken = tmp_path / "broken.py"
    broken.write_text("SOMETHING = 1\n", encoding="utf-8")
    monkeypatch.setattr(pool_census, "AI_GAMES_TESTS", broken)

    failures: list[str] = []
    table = pool_census._closeout_seed_check(failures)

    assert not table.ok, "扫描失效必须判红"
    assert failures, "失败原因要写进 failures"

