"""将池构成清单：零漂移绊线 + 扫描失效绊线 + 派生量覆盖 + `--write` 的落盘边界。

工具本身（``tools/pool_census.py``）**默认只读**：不带参数 / ``--check`` / ``--json`` 都只重算
数据面，再把「加一个武将必然要改的那些数字」逐个从断言里找出来对账。``--write`` 是**显式
opt-in**（diy-heroes 票 06）：只把 :data:`pool_census.SITES` 能机械定位的**站点数字**改对，
改完重跑 ``--check`` 的同一判据并以**它的**退出码收尾；``tests/test_hero_catalog.py`` 的四张
人签登记表（``SHAPE_COUNTS`` / ``SKILL_SHAPE`` / ``SKILL_FILES`` / ``STEP_LANGUAGE_SHAPES``）
**永不落盘**——它们互为见证、取值是人的判断（裁定见
``.scratch/diy-heroes/issues/06-pool-census-write.md`` §5）。本文件**只 import 它的函数**
（不重复实现扫描），断五件事：

1. **当前零漂移**：加武将 / 改池时先红在这里，而不是等一轮 6:35 的全量门禁；
2. **扫描没失效**：命中站点数 ≥ :data:`pool_census.MIN_SITE_HITS`，且每条具名规则都至少
   命中一处——正则被改坏 / 断言搬了家时这里红，不会「扫到 0 处 ⇒ 空转全绿」；
3. **派生量没有漏网**：每个重算出来的数字都必须有站点或登记表在对账（防「少扫一处」）；
4. **``--write`` 的射程**：在 ``tmp_path`` 的沙盒仓里造「数字错一位」，``--check`` 一个字节
   不改、``--write`` 改回且只改那几处、第二遍零写入；认不出的形状 ⇒ 「这里要人工改」+ 非零；
   四张人签表一格不动，只给可粘贴的 ``旧值 → 新值``；
5. **显式 ``skill_id``**（票 06 §4）：AI 语义表覆盖检查读**被引用的 id**，不按短 id 重拼幽灵 id。

另用一份合成文本证明「错值会红、对值会绿」（防恒红 / 恒绿两种坏法）。

设计依据是 ``.scratch/pool-census/`` / ``.scratch/diy-heroes/`` 下的工单与规格——那些目录是
gitignored 的一次性产物，本文件**不依赖**它们（干净 clone 上照样跑）。
"""

from __future__ import annotations

import json
import re
import shutil
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


# ---------------------------------------------------------------------------
# --write（diy-heroes 票 06）：射程 = 站点数字；四张人签表只打印不落盘
# ---------------------------------------------------------------------------


def _sandbox(tmp_path: Path) -> Path:
    """造一份沙盒仓：**只拷**站点与登记表点名的文件，绝不碰真仓。

    清单从工具自己的登记面读（``SITES`` / ``PACK_UNION_FILES`` / ``SKILL_FILES``），不另抄
    一份——本票将来往 ``SITES`` 加一条规则时，沙盒不会悄悄少拷一个文件。
    """
    tables = pool_census._read_tables(pool_census.CATALOG_TESTS)
    relatives = {site.file for site in pool_census.SITES}
    relatives |= set(pool_census.PACK_UNION_FILES)
    relatives |= {f"tests/{name}" for name in tables.skill_files.values()}
    root = tmp_path / "sandbox"
    for relative in sorted(relatives):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return root


def _tree(root: Path) -> dict[str, bytes]:
    """沙盒里每个文件的内容（相对路径 → 字节）：「只有该改的地方变了」逐字节断言用。"""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


_BREAKABLE_SITES = (
    "dealing_enabled_pool",
    "ai_games_mixed_pool",
    "ui_smoke_pool",
)
"""三处站点 = 三种形状：单个数字 / 元组里的两个数字 / mjs 一行里的两个数字（票 06 §1）。"""


def _break_sites(root: Path, *keys: str) -> Path:
    """把这些站点的数字各**减一**（用工具自己的正则 + 派生量定位，不手抄现值）。"""
    derived = pool_census.derive().counts
    for key in keys:
        site = pool_census.SITES_BY_KEY[key]
        path = root / site.file
        text = path.read_bytes().decode("utf-8")
        matches = list(site.pattern.finditer(text))
        assert matches, f"{key}（{site.label}）在沙盒里一处也没命中"
        edits = [
            (start, end, derived[metric] - 1)
            for match in matches
            for metric, raw in match.groupdict().items()
            if raw is not None
            for start, end in (match.span(metric),)
        ]
        for start, end, value in sorted(edits, reverse=True):
            text = text[:start] + str(value) + text[end:]
        path.write_bytes(text.encode("utf-8"))
    return root


def _mutated_tree(root: Path) -> Path:
    """三处站点全改错（三种形状各一处；元组与 mjs 那两行各有两个数字）。"""
    return _break_sites(root, *_BREAKABLE_SITES)


def _break_shape_count(root: Path, shape: str = "主动技能") -> tuple[int, int]:
    """把 ``SHAPE_COUNTS[shape]`` 减一，返回 ``(所在源码行号, 正确的条数)``。

    行号走工具的 ``ast`` 读取面、正确值走它的 ``shapes`` 重算——不手抄现值。
    """
    derived = pool_census.collect(root=root).shapes[shape]
    catalog = root / "tests/test_hero_catalog.py"
    line = pool_census._read_tables(catalog).shape_count_lines[shape]
    lines = catalog.read_bytes().decode("utf-8").splitlines(keepends=True)
    cell = re.compile(rf'("{re.escape(shape)}"\s*:\s*)(\d+)')
    broken = cell.sub(rf"\g<1>{derived - 1}", lines[line - 1], count=1)
    assert broken != lines[line - 1], f"SHAPE_COUNTS 的「{shape}」行形状变了：{lines[line - 1]!r}"
    lines[line - 1] = broken
    catalog.write_bytes("".join(lines).encode("utf-8"))
    return line, derived


def test_check_is_read_only_in_a_sandbox(tmp_path, capsys) -> None:
    """``--check`` 非零退出，但**一个字节都不改**（「默认可读」的硬断言）。"""
    root = _break_sites(_sandbox(tmp_path), "dealing_enabled_pool")
    before = _tree(root)

    assert pool_census.main(["--check"], root=root) == 1
    out = capsys.readouterr().out
    assert "tests/test_hero_dealing.py" in out and "现值" in out, out
    assert _tree(root) == before, "--check 不许改文件"


def test_write_fixes_every_registered_site(tmp_path, capsys) -> None:
    """``--write`` 把三处站点全改回：只有那三个文件变，且逐字节回到原样。"""
    root = _sandbox(tmp_path)
    pristine = _tree(root)  # 沙盒拷贝的原样（与真仓逐字节相同）
    _mutated_tree(root)  # 三处站点改成错值：单个数字 / 元组两个数字 / mjs 一行两个数字
    assert _tree(root) != pristine, "沙盒本身要先证明是错的"

    assert pool_census.main(["--check"], root=root) == 1
    capsys.readouterr()
    assert pool_census.main(["--write"], root=root) == 0
    out = capsys.readouterr().out

    assert "tests/test_hero_dealing.py:" in out, out
    assert "tests/test_all_ai_games.py:" in out, out
    assert "tests/support/ui_smoke.mjs:" in out, out
    assert "已改写 5 处" in out, (
        f"三处站点共 5 个漂移数字（元组与 mjs 那两行各两个）；实得：{out}"
    )
    # 改完之后每个文件都回到**沙盒拷贝的原样** ⇒ 只改过这几个数字，别处一个字节没动。
    assert _tree(root) == pristine, "除登记过的站点外不许有别的字节变化"
    assert pool_census.main(["--check"], root=root) == 0


def test_write_is_idempotent_and_the_second_run_touches_nothing(tmp_path, capsys) -> None:
    """连跑两次 ``--write``：第二次零改动（连 mtime 都不动）。"""
    root = _mutated_tree(_sandbox(tmp_path))
    assert pool_census.main(["--write"], root=root) == 0
    capsys.readouterr()
    before = {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    assert pool_census.main(["--write"], root=root) == 0
    out = capsys.readouterr().out
    after = {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }

    assert after == before, "第二遍 --write 必须零写入"
    assert "文件零改动。" in out, out


def test_write_refuses_an_unlocatable_site(tmp_path, capsys) -> None:
    """站点结构被改形（正则认不出）⇒ 报「这里要人工改」并非零退出，**不猜也不写**。"""
    root = _sandbox(tmp_path)
    site = pool_census.SITES_BY_KEY["dealing_enabled_pool"]
    hits = pool_census.collect(root=root).hits_for(site)
    assert len(hits) == 1, "构造前提：这条规则在沙盒里恰好命中一处"
    path = root / site.file
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    line = lines[hits[0].line - 1]
    assert "==" in line, f"构造前提：命中的那行是等值断言；实得 {line!r}"
    lines[hits[0].line - 1] = line.replace("==", ">=", 1)
    path.write_bytes("".join(lines).encode("utf-8"))
    before = _tree(root)

    assert pool_census.main(["--write"], root=root) == 1
    out = capsys.readouterr().out
    assert "这里要人工改" in out, out
    assert "dealing_enabled_pool" in out, out
    assert _tree(root) == before, "认不出的形状 ⇒ 不猜、不写"


def test_write_never_touches_the_four_signed_tables(tmp_path, capsys) -> None:
    """站点改对了，但四张人签表**一格不动**：只打印可粘贴的 ``旧值 → 新值`` + 非零退出。

    §5 的裁定：``SHAPE_COUNTS`` 与 ``SKILL_SHAPE`` 互为见证（【双雄】同时满足两个形态判据，
    正是靠两处独立手写才在评审里被抓到）⇒ 自动派生就删掉了评审信号。
    """
    root = _mutated_tree(_sandbox(tmp_path))  # 站点：错误（应被 --write 改回）
    line, expected = _break_shape_count(root)
    catalog = root / "tests/test_hero_catalog.py"
    signed = catalog.read_bytes().decode("utf-8")

    assert pool_census.main(["--write"], root=root) == 1
    out = capsys.readouterr().out

    assert catalog.read_bytes().decode("utf-8") == signed, "四张人签表一个字都不许动"
    assert f"tests/test_hero_catalog.py:{line}" in out, out
    assert f"{expected - 1} → {expected}" in out, out
    assert "这里要人工改" in out, out

    after = pool_census.collect(root=root)
    assert not after.ok, "人签表没改 ⇒ --write 必须以非零退出（它返回 1 了）"
    drift = {
        reading.metric
        for hit in after.drifted()
        for reading in hit.readings
        if not reading.ok
    }
    assert drift == {"shape:主动技能"}, f"改完站点后只该剩人签表那一格；实得 {drift}"


def test_the_real_tree_is_consistent_and_the_write_plan_is_empty(capsys) -> None:
    """真仓零漂移 + ``--write`` 的计划为空：本票「不误伤」的守卫（且不往真仓写一个字节）。"""
    plan = pool_census.plan_write(pool_census.collect())

    assert plan.edits == (), "当前真仓没有可机械改的站点"
    assert plan.manual == (), "当前真仓没有要人工改的地方"

    assert pool_census.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert "一致（15 处站点 / 7 张登记表" in out, out


# ---------------------------------------------------------------------------
# 票 06 §4：显式 skill_id 的条目要检查**被引用的 id**
# ---------------------------------------------------------------------------


def test_the_skill_values_check_reads_an_explicit_skill_id(monkeypatch) -> None:
    """带显式 ``skill_id`` 的条目检查**被引用 id**，不是短 id 重拼出来的幽灵 id。

    修前：``full_id = hero_skill_id(hero.id, skill.id)`` ⇒ ``hero.diy_probe.wusheng`` 不在
    ``CATALOG`` ⇒ ``definition is None`` ⇒ **静默跳过**（这张牌永远不进 AI 语义覆盖检查，绿着漏人）。
    修后：读 ``hero.jie_guanyu.wusheng``；把它从 ``SKILL_VALUES`` 摘掉就必须报缺条目。
    """
    from sgs.agents import heuristic
    from sgs.data import Gender, Hero, HeroSkill, HeroTable, Kingdom, SkillType
    from sgs.skills import CATALOG

    referenced = "hero.jie_guanyu.wusheng"
    assert not CATALOG[referenced].locked, "构造前提：拿一条非锁定技当缺条目的样本"
    hero = Hero(
        id="diy_probe",
        name="DIY 探针",
        true_name="探针",
        kingdom=Kingdom.SHU,
        gender=Gender.MALE,
        hp_max=4,
        skills=(
            HeroSkill(
                id="wusheng",
                name="武圣",
                skill_type=SkillType.NORMAL,
                skill_id=referenced,
            ),
        ),
        version="standard",
        enabled=True,
    )
    table = HeroTable(schema_version=1, pack_id="diy-probe", heroes=(hero,))
    monkeypatch.setattr(
        heuristic,
        "SKILL_VALUES",
        {key: value for key, value in heuristic.SKILL_VALUES.items() if key != referenced},
    )

    assert pool_census._enabled_card_skill_ids((table,)) == (referenced,), (
        "启用武将牌上的条目必须解析成被引用的全限定 id"
    )

    failures: list[str] = []
    table_check = pool_census._skill_values_check(failures, tables=(table,))

    assert not table_check.ok, "被引用技能缺条目必须判红（修前这里是静默跳过 ⇒ 绿）"
    assert any(referenced in message for message in failures), failures
    assert not any("diy_probe" in message for message in failures), "不许按短 id 重拼幽灵 id"

