#!/usr/bin/env python3
"""将池构成清单（默认可读）：把「加一个武将必然要改的那些数字」一次列清并逐站点对账。

为什么有这条命令（设计依据是 ``huo-wolong-yanliang`` 批的收口复盘，工单住在 gitignored 的
``.scratch/pool-census/``）：那批加 2 个武将时，第一次全量门禁的 12 条红里有 **9 条同源**——
将池「版本 / 真名」从 ``78/49`` 漂到 ``79/50``（8 条 ``test_all_ai_games`` 的参数化种子 +
``ui_smoke`` ⓪1）。这些数字**分散写在 5 个文件的十几处断言**里，全靠一轮全量门禁（实测 6:35）
才被一起吼出来。本工具把同一份对账压到 1 秒::

    .venv/bin/python tools/pool_census.py            # 人看：派生量 + 逐站点对账
    .venv/bin/python tools/pool_census.py --check    # 机器：任一不一致 ⇒ 非零退出
    .venv/bin/python tools/pool_census.py --write    # 把能机械定位的站点数字改对，再用 --check 复核
    .venv/bin/python tools/pool_census.py --json     # 同一份事实，机器可读

它**默认只读**：不带参数 / ``--check`` / ``--json`` 都不碰文件；``--write`` 是**显式 opt-in**，
且**只**改 :data:`SITES` 能机械定位的**站点数字**（数字 / 张量里的字面量——纯复述派生量的冗余，
写出它们掩盖不了形态错误）。**任何登记表都不写**，四张人签表（``SHAPE_COUNTS`` /
``SKILL_SHAPE`` / ``SKILL_FILES`` / ``STEP_LANGUAGE_SHAPES``）尤其**永不**落盘：
``SHAPE_COUNTS`` 与 ``SKILL_SHAPE`` 互为见证——``huo-wolong-yanliang`` 的【双雄】同时满足
「零步」与「转化」两个机械判据，正是靠**两处独立手写**才在评审时被看见，改成派生值就会静默通过。
对它们 ``--write`` 只打印可直接粘贴的 ``旧值 → 新值``（算得出新值的格子）并报「这里要人工改」、
以非零退出；算不出的一律不猜（裁定见 gitignored 的
``.scratch/diy-heroes/issues/06-pool-census-write.md`` §5）。

口径（三条）：

1. **数据面重算**（唯一真源）：``sgs.data.enabled_hero_ids()`` → 版本数、
   ``hero_by_id(...).true_name`` 去重 → 真名数、``sgs.data.hero_skill_ids()`` → 启用武将技能集、
   ``sgs.skills.CATALOG`` → 技能总数与 ``carrier`` 分区、``tools/sgs_web/descriptions.json``
   （经 :mod:`sgs_web.descriptions` 这个读取面）→ 描述条目；
2. **测试侧登记表**：``tests/test_hero_catalog.py`` 的 ``SHAPE_COUNTS`` / ``SKILL_SHAPE`` /
   ``SKILL_FILES`` / ``STEP_LANGUAGE_SHAPES`` 四个**字面量**赋值，用 ``ast`` + ``literal_eval``
   读（**不 import** 测试模块，免得把夹具与引擎副作用卷进来），逐项对账；
3. **站点**：:data:`SITES` 里的**具名正则**（每条带一句人话标签，分组名就是它该等于的派生量）。
   行号当场算出来、不写死——断言搬家 / 正则被改坏时该规则"扫空"，``--check`` 报「扫描失效」，
   而不是扫到 0 处空转通过（绊线见 ``tests/test_pool_census.py``）。

**视野边界**：站点是**登记在 :data:`SITES` 里的规则**，不是「扫源码里所有数字」——新写一处
硬编码计数不会自动被看见，要往 :data:`SITES` 加一行（新派生量也要有站点或登记表在对账，
``tests/test_pool_census.py`` 有一条绊线核这件事）。

4. **池敏感登记面**（2026-11-13 加，加武将时最容易漏的三处）：AI 语义表
   ``SKILL_VALUES`` 覆盖每条启用非锁定武将技；``M6_CLOSEOUT_SEEDS`` 先只**发将**核一遍
   （每局 ≥2 个版本 ∧ 标准版在场 ∧ 真名互异）；``test_hero_catalog.py`` / ``test_hero_dealing.py``
   两份手抄的「全部武将表并集」覆盖 ``HERO_SUPPLEMENT_PACK_IDS``。

为什么 ``SKILL_FILES`` 只判「无陈旧键 + 文件名真的点名」而不是「== 全部启用技能」：那张覆盖表
的集合只并了 8 张武将表（不含自制包 / 雷包），当前 133 / 143 条。它与既有断言同源，本工具
**不改**既有断言 ⇒ 差额作为**注**打印（加武将时照样看得见该补哪几条），不计入 ``--check``。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

__all__ = [
    "MIN_SITE_HITS",
    "SIGNED_TABLES",
    "SITES",
    "SITES_BY_KEY",
    "Edit",
    "Hit",
    "ManualNote",
    "Reading",
    "Report",
    "Site",
    "TableCheck",
    "WritePlan",
    "apply_plan",
    "collect",
    "derive",
    "main",
    "payload",
    "plan_write",
    "render",
    "render_plan",
    "scan_text",
]

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
"""仓库根（``tools/`` 的上一级）；扫描与登记表都相对它定位。"""

TESTS_DIR: Final[Path] = ROOT / "tests"
CATALOG_TESTS: Final[Path] = TESTS_DIR / "test_hero_catalog.py"
AI_GAMES_TESTS: Final[Path] = TESTS_DIR / "test_all_ai_games.py"
PACK_UNION_FILES: Final[tuple[str, ...]] = (
    "tests/test_hero_catalog.py",
    "tests/test_hero_dealing.py",
)
"""两份手抄「全部武将表并集」的测试文件（相对仓根）。"""

MIN_SITE_HITS: Final[int] = 10
"""站点绊线：远低于当前命中数的下限——扫描被改坏时宁可红，也不要空转通过。"""

METRIC_LABELS: Final[dict[str, str]] = {
    "versions": "启用版本",
    "true_names": "真名",
    "catalog": "CATALOG",
    "hero_skills": "武将技",
    "equipment": "装备",
    "descriptions_skills": "描述条目",
    "inside": "步骤语言内",
    "outside": "步骤语言外",
    "shape_cells": "形态格数",
    "skill_shape": "SKILL_SHAPE 条数",
    "skill_files_stale": "陈旧键",
    "skill_files_unnamed": "文件名不点名",
    "skill_values_missing": "AI 语义表缺条目",
    "closeout_bad": "池敏感固定种子失配",
    "pack_union_missing": "武将表并集缺包",
}
"""派生量 → 人话（``--json`` 里仍是稳定的键名）。"""


# ---------------------------------------------------------------------------
# 站点：一条具名规则 =（人话标签 + 文件 + 正则）；正则的**分组名**就是派生量名
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Site:
    """一条**具名规则**：在某个人写的文件里找某个数字，并说它该等于哪个派生量。

    ``pattern`` 的每个具名分组都必须是 :func:`derive` 的键（``collect`` 会先校验）：分组名
    即派生量名，一条规则因此能在一行里钉两个数（``test_web.py`` 的「版本 / 真名」）。
    """

    key: str
    label: str
    file: str
    pattern: re.Pattern[str]


SITES: Final[tuple[Site, ...]] = (
    Site(
        key="catalog_total",
        label="CATALOG 总条数（装备 + 武将）",
        file="tests/test_hero_catalog.py",
        pattern=re.compile(r"len\(CATALOG\)\s*==\s*(?P<catalog>\d+)"),
    ),
    Site(
        key="hero_skill_count",
        label="CATALOG 的武将那半条数",
        file="tests/test_hero_catalog.py",
        pattern=re.compile(r"len\(heroes\)\s*==\s*(?P<hero_skills>\d+)"),
    ),
    Site(
        key="hero_skill_set",
        label="启用武将技能集规模（数据文件是唯一真源）",
        file="tests/test_hero_catalog.py",
        pattern=re.compile(r"len\(expected\)\s*==\s*(?P<hero_skills>\d+)"),
    ),
    Site(
        key="shape_table_total",
        label="形态表恰好覆盖 CATALOG 一次",
        file="tests/test_hero_catalog.py",
        pattern=re.compile(r"len\(SKILL_SHAPE\)\s*==\s*len\(CATALOG\)\s*==\s*(?P<catalog>\d+)"),
    ),
    Site(
        key="inside_step_language",
        label="步骤语言**内**条数",
        file="tests/test_hero_catalog.py",
        pattern=re.compile(r"len\(inside\)\s*==\s*(?P<inside>\d+)"),
    ),
    Site(
        key="outside_step_language",
        label="步骤语言**外**条数",
        file="tests/test_hero_catalog.py",
        pattern=re.compile(r"len\(outside\)\s*==\s*(?P<outside>\d+)"),
    ),
    Site(
        key="equipment_count",
        label="CATALOG 的装备那半条数",
        file="tests/test_hero_catalog.py",
        pattern=re.compile(r"len\(equipment\)\s*==\s*(?P<equipment>\d+)"),
    ),
    Site(
        key="shape_cell_count",
        label="形态格数（SHAPE_COUNTS 的格数 == 实际用到的形态数）",
        file="tests/test_hero_catalog.py",
        pattern=re.compile(r"len\(SHAPE_COUNTS\)\s*==\s*(?P<shape_cells>\d+)"),
    ),
    Site(
        key="dealing_enabled_pool",
        label="默认将池版本数（发将用的启用池）",
        file="tests/test_hero_dealing.py",
        pattern=re.compile(r"len\(expected\)\s*==\s*(?P<versions>\d+)"),
    ),
    Site(
        key="dealing_dealable_ids",
        label="逐名点将的版本数（紧跟在无参 enabled_hero_ids() 之后）",
        file="tests/test_hero_dealing.py",
        # 同一个文件里还有一个 `len(ids) == 25`：那是**标准表**的启用池（有参调用），
        # 不是默认池 ⇒ 规则必须锚在"无参调用 + 紧跟着的断言"上，否则会把 25 当成漂移。
        pattern=re.compile(
            r"ids = enabled_hero_ids\(\)\s*\n\s*assert len\(ids\) == (?P<versions>\d+)"
        ),
    ),
    Site(
        key="web_pool_listing",
        label="将池清单：版本数 / 真名组数",
        file="tests/test_web.py",
        pattern=re.compile(
            r"len\(flat\)\s*==\s*(?P<versions>\d+)\s*and\s*"
            r"len\(catalog\)\s*==\s*(?P<true_names>\d+)"
        ),
    ),
    Site(
        key="web_description_skills",
        label="描述覆盖的技能数（= CATALOG 规模）",
        file="tests/test_web.py",
        pattern=re.compile(r"len\(expected_skills\)\s*==\s*(?P<catalog>\d+)"),
    ),
    Site(
        key="ai_games_mixed_pool",
        label="8 局混合池收口：版本 / 真名",
        file="tests/test_all_ai_games.py",
        pattern=re.compile(
            r"\(len\(pool\),\s*len\(true_names\)\)\s*==\s*"
            r"\((?P<versions>\d+),\s*(?P<true_names>\d+)\)"
        ),
    ),
    Site(
        key="ui_smoke_pool",
        label="烟测 ⓪1：真名 / 版本（mjs 侧，读不到 Python 常量）",
        file="tests/support/ui_smoke.mjs",
        pattern=re.compile(
            r"ui\.state\.heroCatalog\.length\s*===\s*(?P<true_names>\d+)"
            r"\s*&&\s*catalogVersions\.length\s*===\s*(?P<versions>\d+)"
        ),
    ),
)
"""被写死的将池数字（票 §「必须对账的站点」10 行 → 14 条规则 / 15 处命中）。

为什么一处一行、不合并成一条大正则：每条规则要能**单独报"扫空"**——否则某条断言搬了家，
剩下的规则照样命中，"扫描失效"就没人发现。行号不写在规则里（断言搬家 = 正常演进）。
"""

SITES_BY_KEY: Final[Mapping[str, Site]] = {site.key: site for site in SITES}

SIGNED_TABLES: Final[tuple[str, ...]] = (
    "SHAPE_COUNTS",
    "SKILL_SHAPE",
    "SKILL_FILES",
    "STEP_LANGUAGE_SHAPES",
)
"""``tests/test_hero_catalog.py`` 里四张**人签**登记表：``--write`` **永不**落盘（票 06 §5）。

理由是它们**互为见证**、取值是人的判断：``SHAPE_COUNTS`` 与 ``SKILL_SHAPE`` 互为见证
（``huo-wolong-yanliang`` 票 02 的【双雄】同时满足「零步」与「转化」两个机械判据，正是靠两处
独立手写的计数才在评审里被抓到）；``SKILL_FILES`` / ``STEP_LANGUAGE_SHAPES`` 的取值同样是人的
判断（哪个测试文件覆盖哪条技能 / 哪五个形态在步骤语言内）。自动派生 = 把评审信号一起删掉。

这条契约是**结构性**的（票 08 E）：四份字面量的行区间在 `_read_tables` 里算成
``_Tables.protected_lines``，`plan_write` 把落进去的编辑改报「这里要人工改」、`apply_plan`
直接拒绝落盘——不靠"编辑恰好只来自 SITES"这种间接性质。
"""


# ---------------------------------------------------------------------------
# 事实：派生量、登记表、命中点
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Facts:
    """数据面（唯一真源）的一次重算：派生量 + 三份键集（供登记表逐项核对）。"""

    counts: dict[str, int]
    catalog: frozenset[str]
    hero_skills: frozenset[str]
    descriptions: frozenset[str]


@dataclass(frozen=True)
class Reading:
    """一个数字的对账结果：文件里**写着的**现值 vs 数据面**重算的**应为。

    ``metric`` 是稳定键：派生量名（:data:`METRIC_LABELS` 的键），或形态格的 ``shape:<形态>``。
    """

    metric: str
    value: int
    expected: int

    @property
    def ok(self) -> bool:
        return self.value == self.expected

    def describe(self) -> str:
        """``现值 X / 应为 Y``（票面要求的报错口径）。"""
        return f"{_metric_label(self.metric)} → 现值 {self.value} / 应为 {self.expected}"


@dataclass(frozen=True)
class Hit:
    """一处命中：某文件某行写着一个（或两个）数字，以及它与数据面的差。"""

    label: str
    file: str
    line: int | None
    expression: str
    readings: tuple[Reading, ...]
    rule: str | None = None
    """命中的 :class:`Site` 键；``None`` = 登记表里的一个格子。"""

    @property
    def ok(self) -> bool:
        return all(reading.ok for reading in self.readings)

    def location(self) -> str:
        return self.file if self.line is None else f"{self.file}:{self.line}"

    def drift(self) -> str:
        """这处命中的漂移描述：``现值 X / 应为 Y``（多个数字用 ``；`` 分隔）。"""
        return "；".join(reading.describe() for reading in self.readings if not reading.ok)


@dataclass(frozen=True)
class TableCheck:
    """一张登记表（或一份内容文件）的对账结果：全绿时只打一行摘要。"""

    name: str
    summary: str
    hits: tuple[Hit, ...]

    @property
    def ok(self) -> bool:
        return all(hit.ok for hit in self.hits)


@dataclass(frozen=True)
class Report:
    """一次完整对账：数据面派生量 + 登记表 + 站点命中 + 注 / 失败。"""

    derived: Mapping[str, int]
    shapes: Mapping[str, int]
    tables: tuple[TableCheck, ...]
    sites: tuple[Hit, ...]
    notes: tuple[str, ...]
    failures: tuple[str, ...]

    def hits_for(self, site: Site) -> tuple[Hit, ...]:
        """某条具名规则命中的那些行（空 = 这条规则扫空）。"""
        return tuple(hit for hit in self.sites if hit.rule == site.key)

    def blind_sites(self) -> tuple[Site, ...]:
        """一处都没命中的规则（扫描失效的判据）。"""
        return tuple(site for site in SITES if not self.hits_for(site))

    def drifted(self) -> tuple[Hit, ...]:
        """对账不一致的地方（站点 + 登记表格子）。"""
        cells = (cell for table in self.tables for cell in table.hits)
        return tuple(hit for hit in (*self.sites, *cells) if not hit.ok)

    @property
    def ok(self) -> bool:
        """``--check`` 的判据：零漂移 + 零失败 + 扫描没失效。"""
        return (
            not self.failures
            and not self.drifted()
            and len(self.sites) >= MIN_SITE_HITS
            and not self.blind_sites()
        )


def derive() -> Facts:
    """数据面重算（唯一真源）：引擎的启用池 / 技能目录 / 描述内容文件。

    只读、不建局、不模拟；``hero_by_id`` 查不到即抛（启用池与武将表不同源是硬错，不静默算小）。
    技能 id 一律走 :func:`sgs.data.hero_skill_ids`——它是"``hero.<武将>.<短 id>``"的唯一派生处。
    """
    from sgs.data import enabled_hero_ids, hero_by_id, hero_skill_ids
    from sgs.skills import CATALOG

    versions = enabled_hero_ids()
    true_names: set[str] = set()
    hero_skills: set[str] = set()
    for hero_id in versions:
        hero = hero_by_id(hero_id)
        if hero is None:
            raise ValueError(f"启用武将 {hero_id} 在武将表里查不到（启用池与数据文件不同源）")
        true_names.add(hero.true_name)
        hero_skills.update(hero_skill_ids(hero_id))

    carriers = Counter(definition.carrier.value for definition in CATALOG.values())
    descriptions = _skill_descriptions()
    return Facts(
        counts={
            "versions": len(versions),
            "true_names": len(true_names),
            "catalog": len(CATALOG),
            "hero_skills": carriers["HERO"],
            "equipment": carriers["EQUIPMENT"],
            "descriptions_skills": len(descriptions),
        },
        catalog=frozenset(CATALOG),
        hero_skills=frozenset(hero_skills),
        descriptions=descriptions,
    )


def _descriptions_module():
    """``sgs_web.descriptions``（描述内容文件的**读取面**）：路径与键集都从它取，不另抄一份。"""
    tools_dir = str(Path(__file__).resolve().parent)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    import sgs_web.descriptions as descriptions

    return descriptions


def _skill_descriptions() -> frozenset[str]:
    """描述内容文件的技能键集。"""
    return frozenset(_descriptions_module().SKILL_DESCRIPTIONS)


# ---------------------------------------------------------------------------
# 读测试侧的字面量登记表（ast，不 import 测试模块）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Tables:
    """``test_hero_catalog.py`` 里的四份字面量（**人签的**快照；``--write`` 也不碰它们）。"""

    shape_counts: Mapping[str, int]
    shape_count_lines: Mapping[str, int]
    skill_shape: Mapping[str, str]
    skill_shape_line: int
    skill_files: Mapping[str, str]
    skill_files_line: int
    step_shapes: frozenset[str]
    protected_lines: tuple[tuple[int, int], ...]
    """四张人签表字面量的**行区间**（含端点）：``--write`` 的结构性禁区（票 08 E）。"""
    path: Path


def _read_tables(path: Path) -> _Tables:
    """读登记表的字面量赋值；缺表 / 不是字面量即抛（登记表结构变了，本工具要跟着改）。"""
    assignments = _literal_assignments(
        path, ("SHAPE_COUNTS", "SKILL_SHAPE", "SKILL_FILES", "STEP_LANGUAGE_SHAPES")
    )
    missing = [
        name
        for name in ("SHAPE_COUNTS", "SKILL_SHAPE", "SKILL_FILES", "STEP_LANGUAGE_SHAPES")
        if name not in assignments
    ]
    if missing:
        raise ValueError(f"{path.name} 里找不到字面量赋值：{', '.join(missing)}")

    shape_counts, shape_counts_node, _ = assignments["SHAPE_COUNTS"]
    if not isinstance(shape_counts, dict) or not isinstance(shape_counts_node, ast.Dict):
        raise ValueError(f"{path.name} 的 SHAPE_COUNTS 不是字典字面量")
    shape_lines = {
        key_node.value: key_node.lineno
        for key_node in shape_counts_node.keys
        if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)
    }

    skill_shape, _, skill_shape_line = assignments["SKILL_SHAPE"]
    skill_files, _, skill_files_line = assignments["SKILL_FILES"]
    step_shapes, _, _ = assignments["STEP_LANGUAGE_SHAPES"]
    _require_table(path, "SKILL_SHAPE", skill_shape)
    _require_table(path, "SKILL_FILES", skill_files)
    if not isinstance(step_shapes, (set, frozenset)) or not all(
        isinstance(shape, str) for shape in step_shapes
    ):
        raise ValueError(f"{path.name} 的 STEP_LANGUAGE_SHAPES 不是「字符串集合」字面量")
    # 四张人签表的行区间 = `--write` 的结构性禁区：`apply_plan` 按它拒绝任何落进表里的编辑。
    protected_lines = tuple(
        (assignments[name][2], assignments[name][1].end_lineno or assignments[name][2])
        for name in SIGNED_TABLES
    )
    return _Tables(
        shape_counts=shape_counts,
        shape_count_lines=shape_lines,
        skill_shape=skill_shape,
        skill_shape_line=skill_shape_line,
        skill_files=skill_files,
        skill_files_line=skill_files_line,
        step_shapes=frozenset(step_shapes),
        protected_lines=protected_lines,
        path=path,
    )


def _require_table(path: Path, name: str, value: Any) -> None:
    """登记表的形状校验：不是「字符串键的字典字面量」就抛（**clean 失败**，不留 traceback）。"""
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and key for key in value
    ):
        raise ValueError(f"{path.name} 的 {name} 不是「字符串键 → 值」的字典字面量")


def _literal_assignments(
    path: Path, names: Sequence[str]
) -> dict[str, tuple[Any, ast.expr, int]]:
    """模块级的 ``NAME = <literal>``：``NAME → (值, 值节点, 赋值行号)``。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, tuple[Any, ast.expr, int]] = {}
    for node in tree.body:
        target = _assigned_name(node)
        if target is None or target not in names:
            continue
        found[target] = (_literal_eval(node.value), node.value, node.lineno)
    return found


def _assigned_name(node: ast.stmt) -> str | None:
    """``NAME = ...`` / ``NAME: T = ...``（只看单目标赋值）→ ``NAME``。"""
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id
    return None


def _literal_eval(node: ast.expr) -> Any:
    """``ast.literal_eval`` + 认 ``frozenset({...})`` 这类"字面量外面套一层构造"的写法。"""
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"frozenset", "set", "tuple"}
        and len(node.args) == 1
    ):
        return _literal_eval(node.args[0])
    return ast.literal_eval(node)


# ---------------------------------------------------------------------------
# 扫描与对账
# ---------------------------------------------------------------------------


def scan_text(
    site: Site, text: str, derived: Mapping[str, int], *, filename: str
) -> tuple[Hit, ...]:
    """在一段源码文本里跑这条具名规则（**纯函数**：不读盘，便于用合成反例钉住红/绿两向）。

    行号当场按正则匹配位置算，所以站点搬家不需要改这里；``derived`` 给的是"应为值"。
    """
    hits: list[Hit] = []
    for match in site.pattern.finditer(text):
        # 行号取**匹配结束处**所在的行：多行规则（锚在前一行）因此指到真正写数字的那一行。
        line = text.count("\n", 0, match.end()) + 1
        expression = match.group(0).strip().splitlines()[-1].strip()
        readings = tuple(
            Reading(metric, int(value), derived[metric])
            for metric, value in match.groupdict().items()
            if value is not None
        )
        hits.append(
            Hit(
                label=site.label,
                file=filename,
                line=line,
                expression=" ".join(expression.split()),
                readings=readings,
                rule=site.key,
            )
        )
    return tuple(hits)


def collect(*, root: Path = ROOT) -> Report:
    """跑一次完整对账（只读）：数据面 → 登记表 → 站点。

    ``root`` 可注入：测试在一份**沙盒仓**上跑 ``--check`` / ``--write``，绝不碰真仓
    （缺省 = 真仓根；数据面派生量始终读已安装的 ``sgs``，与 ``root`` 无关）。
    """
    tests_dir = root / "tests"
    catalog_tests = tests_dir / "test_hero_catalog.py"
    try:
        facts = derive()
        tables = _read_tables(catalog_tests)
    except (OSError, SyntaxError, ValueError) as error:
        return Report(
            derived={},
            shapes={},
            tables=(),
            sites=(),
            notes=(),
            failures=(f"数据面 / 登记表读不了：{error}",),
        )

    failures: list[str] = []
    notes: list[str] = []

    # 形态表 → 每格条数（SHAPE_COUNTS 的声明序在前，多出来的格按名字补在后面）。
    recomputed = Counter(tables.skill_shape.values())
    shapes: dict[str, int] = {
        shape: recomputed.get(shape, 0) for shape in tables.shape_counts
    }
    for shape in sorted(set(recomputed) - set(shapes)):
        shapes[shape] = recomputed[shape]

    derived: dict[str, int] = dict(facts.counts)
    derived["inside"] = sum(
        count for shape, count in shapes.items() if shape in tables.step_shapes
    )
    derived["outside"] = sum(
        count for shape, count in shapes.items() if shape not in tables.step_shapes
    )
    # 形态格数 = SHAPE_COUNTS 这份**人写的表**有多宽（与断言里那个手抄的数字对账）；
    # 「有格没有技能用」另由 _shape_counts_check 报（两者合起来才挡得住"多一格 / 少一格"）。
    derived["shape_cells"] = len(tables.shape_counts)

    unknown_metrics = sorted(
        {
            metric
            for site in SITES
            for metric in site.pattern.groupindex
            if metric not in derived
        }
    )
    if unknown_metrics:
        failures.append(f"具名规则引用了不存在的派生量：{', '.join(unknown_metrics)}")

    catalog_label = _relative(catalog_tests, root=root)
    table_checks = [
        _shape_counts_check(tables, shapes, catalog_label, failures),
        _skill_shape_check(tables, facts, catalog_label, failures),
        _skill_files_check(tables, facts, tests_dir, failures, notes),
        _descriptions_check(facts, failures),
        _skill_values_check(failures),
        _closeout_seed_check(failures, file=tests_dir / "test_all_ai_games.py"),
        _pack_union_check(failures, root=root),
    ]

    sites: list[Hit] = []
    for site in SITES:
        try:
            text = _read_text(root / site.file)
        except OSError as error:
            failures.append(f"扫描失效：读不到 {site.file}（{error}）")
            continue
        sites.extend(scan_text(site, text, derived, filename=site.file))

    return Report(
        derived=derived,
        shapes=shapes,
        tables=tuple(table_checks),
        sites=tuple(sorted(sites, key=lambda hit: (hit.file, hit.line or 0, hit.rule or ""))),
        notes=tuple(notes),
        failures=tuple(failures),
    )


def _shape_counts_check(
    tables: _Tables,
    shapes: Mapping[str, int],
    filename: str,
    failures: list[str],
) -> TableCheck:
    """``SHAPE_COUNTS`` 每格 == ``Counter(SKILL_SHAPE.values())``（互不派生、互为见证）。

    两侧的**格集**也要一致：SKILL_SHAPE 里有而 SHAPE_COUNTS 没登记（缺格），以及反过来
    SHAPE_COUNTS 有格却没有任何技能用它（多格），都算漂移——与 ``test_hero_catalog.py`` 的
    ``set(SKILL_SHAPE.values()) == set(SHAPE_COUNTS)`` 同向。
    """
    unregistered = sorted(set(tables.skill_shape.values()) - set(tables.shape_counts))
    unused = sorted(set(tables.shape_counts) - set(tables.skill_shape.values()))
    if unregistered:
        failures.append(
            "SHAPE_COUNTS 缺格（SKILL_SHAPE 里有、SHAPE_COUNTS 没登记）：" + _join(unregistered)
        )
    if unused:
        failures.append("SHAPE_COUNTS 有格没有技能用（形态表里没有任何技能归这一格）：" + _join(unused))
    hits: list[Hit] = []
    for shape, signed in tables.shape_counts.items():
        line = tables.shape_count_lines.get(shape)
        hits.append(
            Hit(
                label=f"形态格「{shape}」",
                file=filename,
                line=line,
                expression=f'SHAPE_COUNTS["{shape}"]',
                readings=(Reading(f"shape:{shape}", signed, shapes.get(shape, 0)),),
            )
        )
    return TableCheck(
        name="SHAPE_COUNTS",
        summary=f"{len(tables.shape_counts)} 格 == Counter(SKILL_SHAPE) 重算",
        hits=tuple(hits),
    )


def _skill_shape_check(
    tables: _Tables, facts: Facts, filename: str, failures: list[str]
) -> TableCheck:
    """``SKILL_SHAPE`` 的键集恰是 ``CATALOG`` 的键集（每技能一行形态）。"""
    missing = sorted(facts.catalog - set(tables.skill_shape))
    stale = sorted(set(tables.skill_shape) - facts.catalog)
    _key_drift(
        failures,
        missing_label="SKILL_SHAPE 缺行（CATALOG 里有、形态表没有）",
        stale_label="SKILL_SHAPE 有陈旧的键（不是 CATALOG 里的技能）",
        missing=missing,
        stale=stale,
    )
    hit = Hit(
        label="形态表覆盖",
        file=filename,
        line=tables.skill_shape_line,
        expression="set(SKILL_SHAPE) == set(CATALOG)",
        readings=(Reading("skill_shape", len(tables.skill_shape), facts.counts["catalog"]),),
    )
    return TableCheck(
        name="SKILL_SHAPE",
        summary=f"{len(tables.skill_shape)} 条 == CATALOG 键集",
        hits=(hit,),
    )


def _skill_files_check(
    tables: _Tables,
    facts: Facts,
    tests_dir: Path,
    failures: list[str],
    notes: list[str],
) -> TableCheck:
    """``SKILL_FILES``：无陈旧键 + 每条点名的测试文件真的写了那个短 id（覆盖表的既有判据）。"""
    stale = sorted(set(tables.skill_files) - facts.hero_skills)
    unnamed = _unnamed_skill_files(tables.skill_files, tests_dir)
    if stale:
        failures.append(f"SKILL_FILES 有陈旧的键（不是启用的武将技能）：{_join(stale)}")
    if unnamed:
        failures.append(f"SKILL_FILES 点名的测试文件里找不到技能短 id：{_join(unnamed)}")
    gap = sorted(facts.hero_skills - set(tables.skill_files))
    if gap:
        notes.append(
            f"{len(gap)} 条启用武将技能没登记进 SKILL_FILES（既有事实：该表的集合只并了部分武将表，"
            f"不含自制包 / 雷包；不计入 --check）。要补覆盖就按这几条：{_join(gap)}"
        )
    hit = Hit(
        label="覆盖表登记",
        file=_relative(CATALOG_TESTS),
        line=tables.skill_files_line,
        expression="set(SKILL_FILES) ⊆ 启用武将技能集",
        readings=(
            Reading("skill_files_stale", len(stale), 0),
            Reading("skill_files_unnamed", len(unnamed), 0),
        ),
    )
    return TableCheck(
        name="SKILL_FILES",
        summary=(
            f"{len(tables.skill_files)} 条登记：{len(stale)} 处陈旧键 / "
            f"{len(unnamed)} 处文件名不点名"
        ),
        hits=(hit,),
    )


def _descriptions_check(facts: Facts, failures: list[str]) -> TableCheck:
    """``descriptions.json`` 的技能键集恰是 ``CATALOG`` 的键集（缺条目 / 陈旧条目都报）。"""
    missing = sorted(facts.catalog - facts.descriptions)
    stale = sorted(facts.descriptions - facts.catalog)
    _key_drift(
        failures,
        missing_label="descriptions.json 缺技能条目",
        stale_label="descriptions.json 有陈旧 / 拼错的技能条目",
        missing=missing,
        stale=stale,
    )
    hit = Hit(
        label="描述覆盖",
        file=_relative(_descriptions_module().DESCRIPTIONS_PATH),
        line=None,
        expression="set(descriptions.skills) == set(CATALOG)",
        readings=(
            Reading(
                "descriptions_skills",
                facts.counts["descriptions_skills"],
                facts.counts["catalog"],
            ),
        ),
    )
    return TableCheck(
        name="descriptions",
        summary=f"{facts.counts['descriptions_skills']} 条 == CATALOG 键集",
        hits=(hit,),
    )


def _key_drift(
    failures: list[str],
    *,
    missing_label: str,
    stale_label: str,
    missing: Sequence[str],
    stale: Sequence[str],
) -> None:
    """把「缺了哪些键 / 多了哪些键」记成失败（三张登记表共用同一套报法）。"""
    if missing:
        failures.append(f"{missing_label}：{_join(missing)}")
    if stale:
        failures.append(f"{stale_label}：{_join(stale)}")


def _unnamed_skill_files(
    skill_files: Mapping[str, str], tests_dir: Path = TESTS_DIR
) -> list[str]:
    """登记的测试文件里没有该技能短 id 的那些（与 ``test_every_enabled_hero_skill_...`` 同判据）。"""
    unnamed: list[str] = []
    for skill_id, filename in skill_files.items():
        short_id = skill_id.rsplit(".", 1)[1]
        try:
            text = (tests_dir / filename).read_text(encoding="utf-8")
        except OSError:
            unnamed.append(f"{skill_id}（找不到 {filename}）")
            continue
        if short_id not in text:
            unnamed.append(f"{skill_id}（{filename} 没点名）")
    return unnamed


def _enabled_card_skill_ids(tables: Sequence[Any] | None = None) -> tuple[str, ...]:
    """启用武将**牌上**每条技能条目解析出的全限定 id（**含锁定技**；缺省读全部启用的武将表）。

    ``skill_id`` 显式引用（diy-heroes 票 04）时读**被引用的那个 id**——一律经
    :func:`sgs.data.hero_card_skill_id`，不按短 id 重拼 ``hero.<本武将>.<短 id>``。原因是一处
    真实黑洞（票 06 §4）：自制武将把技能指向别的武将（``hero.jie_guanyu.wusheng``）时，重拼会
    得到一个不在 ``CATALOG`` 里的幽灵 id，覆盖检查于是**静默跳过**它，而不是报红。

    ``tables`` 可注入：覆盖检查的单元测试用一张合成 :class:`sgs.data.HeroTable` 钉住"读的是被
    引用 id"，不碰真仓数据；缺省 = ``load_hero_tables()``（全部启用的武将表）。
    """
    from sgs.data import enabled_hero_ids, hero_card_skill_id, load_hero_tables

    resolved: list[str] = []
    for table in load_hero_tables() if tables is None else tables:
        for hero_id in enabled_hero_ids(table):
            hero = table.by_id(hero_id)
            for skill in hero.skills:
                resolved.append(hero_card_skill_id(hero, skill))
    return tuple(resolved)


def _skill_values_check(
    failures: list[str], tables: Sequence[Any] | None = None
) -> TableCheck:
    """**AI 语义表**（``SKILL_VALUES``）覆盖每条启用非锁定武将技。

    与 ``tests/test_heuristic_ai.py::_missing_entries`` 同一判据、同一数据源（全部启用的
    武将表；锁定技跳过）；不 import 测试模块。加武将时它只在 8 分钟门禁里爆——这里 1 秒看见。

    技能 id 走 :func:`_enabled_card_skill_ids`（即 ``hero_card_skill_id``）：显式 ``skill_id``
    的条目检查的是**被引用的 id**，派生幽灵 id 会被静默跳过（票 06 §4）。
    """
    from sgs.agents.heuristic import SKILL_VALUES
    from sgs.skills import CATALOG

    missing: list[str] = []
    for full_id in _enabled_card_skill_ids(tables):
        definition = CATALOG.get(full_id)
        if definition is None or definition.locked:
            continue
        if full_id not in SKILL_VALUES:
            missing.append(full_id)
    missing.sort()
    if missing:
        failures.append(
            "AI 语义表缺条目（补 `sgs/agents/heuristic.py` 的 ``SKILL_VALUES``）："
            + "、".join(missing)
        )
    return TableCheck(
        name="AI 语义表",
        summary=(f"{len(missing)} 条缺条目" if missing else "启用非锁定武将技都有条目"),
        hits=(
            Hit(
                label="AI 语义表覆盖",
                file="tests/test_heuristic_ai.py",
                line=None,
                expression="SKILL_VALUES ⊇ 启用非锁定武将技",
                readings=(Reading("skill_values_missing", len(missing), 0),),
            ),
        ),
    )


def _closeout_seed_check(
    failures: list[str], *, file: Path | None = None
) -> TableCheck:
    """池敏感的固定种子先只做**发将**预检（8 分钟门禁的那条判据）。

    池每加一个武将都会重排 RNG ⇒ 固定种子可能抽不到标准版（先例：144→145、140→141）。
    这里不跑对局（毫秒级）：跑完 / 零非法应答 / 可回放仍由 ``test_all_ai_games`` 自己钉。

    ``file`` 可注入（``collect`` 传沙盒仓里的同名文件）；缺省 = 模块常量 ``AI_GAMES_TESTS``
    （测试用 ``monkeypatch`` 改它的既有接缝因此照旧生效）。
    """
    from sgs.data import RuleConfig, hero_by_id, load_content_pack
    from sgs.engine import Engine

    seed_file = AI_GAMES_TESTS if file is None else file

    def _broken(summary: str) -> TableCheck:
        """扫描失效的表格：**必须带一条不 ok 的读数**，否则空 hits 会被当成绿。"""
        return TableCheck(
            name="池敏感固定种子",
            summary=summary,
            hits=(
                Hit(
                    label="closeout 种子发将",
                    file=_relative(seed_file),
                    line=None,
                    expression="M6_CLOSEOUT_SEEDS / M6_CLOSEOUT_PLAYERS",
                    readings=(Reading("closeout_bad", 1, 0),),
                ),
            ),
        )

    try:
        text = seed_file.read_text(encoding="utf-8")
    except OSError as error:
        failures.append(f"读不到 {_relative(seed_file)}：{error}")
        return _broken("读不到")
    seeds_match = re.search(r"M6_CLOSEOUT_SEEDS[^=]*=\s*\(([^)]*)\)", text)
    players_match = re.search(r"M6_CLOSEOUT_PLAYERS\s*=\s*(\d+)", text)
    if seeds_match is None or players_match is None:
        failures.append("读不到 M6_CLOSEOUT_SEEDS / M6_CLOSEOUT_PLAYERS（扫描规则失效）")
        return _broken("扫描失效")
    seat_count = int(players_match.group(1))
    seed_values = tuple(int(value) for value in re.findall(r"\d+", seeds_match.group(1)))
    pack = load_content_pack(deck_profile="standard_108")
    bad: list[str] = []
    for seed in seed_values:
        engine = Engine.new_game(
            seed=seed,
            rule_config=RuleConfig(player_count=seat_count),
            pack=pack,
            hero_pool=(),
        )
        heroes = [player.hero for player in engine.state.players]
        versions = {hero_by_id(hero).version for hero in heroes}
        names = {hero_by_id(hero).true_name for hero in heroes}
        if (
            "standard" not in versions
            or len(versions) < 2
            or len(set(heroes)) != seat_count
            or len(names) != seat_count
        ):
            bad.append(f"seed={seed}（{sorted(versions)}）")
    if bad:
        failures.append(
            "池敏感固定种子在新池下不再合格（按纪律换种不换判据）：" + "；".join(bad)
        )
    return TableCheck(
        name="池敏感固定种子",
        summary=(
            f"{len(bad)}/{len(seed_values)} 局不合格"
            if bad
            else f"{len(seed_values)} 局都是混合池且含标准版"
        ),
        hits=(
            Hit(
                label="closeout 种子发将",
                file=_relative(seed_file),
                line=None,
                expression="每局 ≥2 版本 ∧ standard 在场 ∧ 真名互异",
                readings=(Reading("closeout_bad", len(bad), 0),),
            ),
        ),
    )


def _pack_union_check(failures: list[str], *, root: Path = ROOT) -> TableCheck:
    """两份手抄的「全部武将表并集」必须覆盖 ``HERO_SUPPLEMENT_PACK_IDS``。

    新包（如 ``shan``）最容易漏：池里加了、测试的并集没加，只在门禁里红（本批 3 次本地红之一）。
    ``root`` 与 ``collect`` 同义（缺省真仓；测试用绝对路径改 ``PACK_UNION_FILES`` 的既有接缝照旧）。
    """
    from sgs.data import HERO_SUPPLEMENT_PACK_IDS

    missing: list[str] = []
    for relative in PACK_UNION_FILES:
        try:
            text = (root / relative).read_text(encoding="utf-8")
        except OSError as error:
            failures.append(f"读不到 {relative}：{error}")
            continue
        for pack_id in HERO_SUPPLEMENT_PACK_IDS:
            if f'load_heroes("{pack_id}")' not in text:
                missing.append(f"{relative} 缺 {pack_id}")
        if "load_heroes()" not in text:
            missing.append(f"{relative} 缺 standard（load_heroes()）")
    if missing:
        failures.append("武将表并集缺包（加包后把并集补上）：" + "；".join(missing))
    return TableCheck(
        name="武将表并集",
        summary=("缺包" if missing else "两份并集都覆盖 HERO_SUPPLEMENT_PACK_IDS"),
        hits=(
            Hit(
                label="武将表并集覆盖",
                file="tests/test_hero_catalog.py",
                line=None,
                expression="并集 ⊇ {standard} ∪ HERO_SUPPLEMENT_PACK_IDS",
                readings=(Reading("pack_union_missing", len(missing), 0),),
            ),
        ),
    )


# ---------------------------------------------------------------------------
# 输出：人看的表 / 机器读的 JSON
# ---------------------------------------------------------------------------


def render(report: Report) -> str:
    """人看的档：数据面重算 + 登记表 + 站点对账 + 注 / 失败 + 结论。"""
    lines: list[str] = []
    derived = report.derived

    lines.append("将池构成（数据面重算：sgs.data + sgs.skills + descriptions.json）")
    rows = (
        ("启用版本", _number(derived.get("versions"))),
        ("真名", _number(derived.get("true_names"))),
        (
            "CATALOG",
            f"{_number(derived.get('catalog'))}   "
            f"（武将技 {_number(derived.get('hero_skills'))} / 装备 {_number(derived.get('equipment'))}）",
        ),
        ("descriptions.skills", _number(derived.get("descriptions_skills"))),
        (
            "形态",
            " · ".join(f"{shape} {count}" for shape, count in report.shapes.items()) or "（读不到）",
        ),
        (
            "步骤语言",
            f"内 {_number(derived.get('inside'))} / 外 {_number(derived.get('outside'))}",
        ),
    )
    label_width = max(_width(label) for label, _ in rows)
    for label, value in rows:
        lines.append(f"  {_pad(label, label_width)}  {value}")

    if report.tables:
        lines.append("")
        lines.append(
            f"登记表（{_relative(CATALOG_TESTS)}：ast.literal_eval 读字面量，不 import 测试模块）"
        )
        name_width = max(_width(table.name) for table in report.tables)
        for table in report.tables:
            lines.append(f"  {_status(table.ok)} {_pad(table.name, name_width)}  {table.summary}")
            for cell in table.hits:
                if cell.ok:
                    continue
                lines.append(f"       {cell.location()}  {cell.drift()}")

    lines.append("")
    lines.append(f"站点对账（{len(report.sites)} 处 / {len(SITES)} 条具名规则）")
    if report.sites:
        location_width = min(
            44, max(_width(hit.location()) for hit in report.sites)
        )
        expression_width = min(56, max(_width(hit.expression) for hit in report.sites))
        for hit in report.sites:
            tail = (
                " / ".join(_metric_label(reading.metric) for reading in hit.readings)
                if hit.ok
                else hit.drift()
            )
            lines.append(
                f"  {_status(hit.ok)} {_pad(hit.location(), location_width)}  "
                f"{_pad(hit.expression, expression_width)}  {tail}"
            )
    else:
        lines.append("  （一处也没命中——扫描失效）")
    for site in report.blind_sites():
        lines.append(f"  MISM 扫描失效：{site.key}（{site.label}）没有命中 → {site.file}")

    if report.notes:
        lines.append("")
        lines.append("注：")
        lines.extend(f"  - {note}" for note in report.notes)
    if report.failures:
        lines.append("")
        lines.append("失败：")
        lines.extend(f"  - {failure}" for failure in report.failures)

    lines.append("")
    if report.ok:
        lines.append(
            f"结论：一致（{len(report.sites)} 处站点 / {len(report.tables)} 张登记表；--check 以 0 退出）"
        )
    else:
        lines.append(
            f"结论：{len(report.drifted())} 处漂移 + {len(report.failures)} 条失败"
            f"（--check 以非零退出）"
        )
    return "\n".join(lines)


def payload(report: Report) -> dict[str, Any]:
    """机器读的档：与人看的表同一份事实（``--json`` 的输出体）。"""
    return {
        "ok": report.ok,
        "derived": dict(report.derived),
        "shapes": dict(report.shapes),
        "tables": [
            {
                "name": table.name,
                "ok": table.ok,
                "summary": table.summary,
                "cells": [_hit_payload(cell) for cell in table.hits],
            }
            for table in report.tables
        ],
        "sites": [_hit_payload(hit) for hit in report.sites],
        "scan": {
            "hits": len(report.sites),
            "rules": len(SITES),
            "minimum": MIN_SITE_HITS,
            "blind": [site.key for site in report.blind_sites()],
        },
        "notes": list(report.notes),
        "failures": list(report.failures),
    }


def _hit_payload(hit: Hit) -> dict[str, Any]:
    return {
        "label": hit.label,
        "file": hit.file,
        "line": hit.line,
        "expression": hit.expression,
        "ok": hit.ok,
        "readings": [
            {
                "metric": reading.metric,
                "value": reading.value,
                "expected": reading.expected,
                "ok": reading.ok,
            }
            for reading in hit.readings
        ],
    }


def _metric_label(metric: str) -> str:
    if metric.startswith("shape:"):
        return f"形态「{metric.split(':', 1)[1]}」"
    return METRIC_LABELS.get(metric, metric)


def _status(ok: bool) -> str:
    return "OK  " if ok else "MISM"


def _number(value: int | None) -> str:
    return "?" if value is None else str(value)


def _width(text: str) -> int:
    """终端显示宽度（CJK 记 2 格），只用于对齐人看的表。"""
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _read_text(path: Path) -> str:
    """按**原始字节**解码的文本（不做换行归一化）：行号与字符区间在规划 / 落盘两侧同一把尺。

    ``Path.read_text`` 会把 ``\r\n`` 归一成 ``\n``；``apply_plan`` 落盘时按原字节区间替换，
    两边混用会让 CRLF 树上每次编辑都误报"在规划之后变了"（票 08 §3 的已知局限——这里对齐掉）。
    """
    return path.read_bytes().decode("utf-8")


def _relative(path: Path, *, root: Path = ROOT) -> str:
    """相对仓库根的 posix 路径（仓外路径原样返回）。"""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _join(items: Iterable[str], separator: str = "、") -> str:
    return separator.join(items)


# ---------------------------------------------------------------------------
# 改写（--write）：只落盘站点数字；登记表只打印「可粘贴的新值 / 这里要人工改」
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    """一处**能机械改**的站点数字：文件、行、旧值 → 新值，以及在文件文本里的字符区间。

    ``start`` / ``end`` 是**那条具名分组的区间**（不是整行）：所以同一行有两三个数字时每处各改
    各的，也不会碰到行里的别的数字。
    """

    file: str
    line: int
    expression: str
    metric: str
    old: int
    new: int
    start: int
    end: int

    def location(self) -> str:
        return f"{self.file}:{self.line}"

    def describe(self) -> str:
        return (
            f"{self.location()}  {self.expression}  "
            f"{_metric_label(self.metric)} {self.old} → {self.new}"
        )


@dataclass(frozen=True)
class ManualNote:
    """一处「这里要人工改」：算得出新旧值的带上 ``旧值 → 新值``，算不出的只给理由（不猜）。"""

    file: str | None
    line: int | None
    where: str
    reason: str
    old: int | None = None
    new: int | None = None

    def location(self) -> str:
        if self.file is None:
            return ""
        return self.file if self.line is None else f"{self.file}:{self.line}"

    def describe(self) -> str:
        parts = [part for part in (self.location(), self.where) if part]
        if self.old is not None and self.new is not None:
            parts.append(f"{self.old} → {self.new}")
        return "  ".join(parts) if parts else "（数据面）"


@dataclass(frozen=True)
class WritePlan:
    """一次 ``--write`` 的**计划**：能机械改的站点 + 必须人改的地方。

    ``manual`` 非空 ⇒ 跑完也不可能绿（工具不猜），``--write`` 因此以非零退出。
    """

    edits: tuple[Edit, ...]
    manual: tuple[ManualNote, ...]
    protected: tuple[tuple[str, int, int], ...] = ()
    """人签登记表的**行区间**（``文件, 首行, 末行``，含端点）：``--write`` 的结构性禁区。

    ``plan_write`` 按它把落进表里的编辑改报「这里要人工改」，``apply_plan`` 按它**拒绝落盘**
    ——"四张表永不写"因此是这个字段 + 下面两条检查保证的，不是"编辑恰好只来自 SITES"的间接性质
    （票 08 E）。
    """


_TABLE_REASONS: Final[Mapping[str, str]] = {
    "SHAPE_COUNTS": "人签快照（与 SKILL_SHAPE 互为见证）：工具只打印新值、不落盘",
    "SKILL_SHAPE": "人签快照（与 SHAPE_COUNTS 互为见证）：工具只打印新值、不落盘",
    "SKILL_FILES": "人签登记表（哪个测试文件覆盖哪条技能）：工具不猜",
    "STEP_LANGUAGE_SHAPES": "人签登记表（哪五个形态在步骤语言内）：工具不猜",
}
"""四张人签表各自的理由（与 :data:`SIGNED_TABLES` 同一条裁定，票 06 §5）。"""


def plan_write(report: Report, *, root: Path = ROOT) -> WritePlan:
    """把一次对账结果翻成**改写计划**（只读盘、不落盘；纯规划便于单测）。

    ``root`` 必须与 ``report`` 的 ``collect`` 同源，否则"该改的值"与"要改的文本"会错位。

    - 站点：重扫当前文件，每个**漂移的具名分组** = 一处编辑（区间精确到那几个数字）；
    - 扫描失效 / 读不到文件 / 同一段数字被要求改成不同的值：进 ``manual``，**不猜**；
    - 登记表：一律进 ``manual``（:data:`SIGNED_TABLES` 四张永不落盘），能算出新值的带上
      ``旧值 → 新值``；``report.failures`` 也原样进 ``manual``（"这里要人工改"的完整清单）。
    """
    derived = report.derived
    protected = _protected_lines(root)
    manual: list[ManualNote] = []
    if not derived:
        reason = report.failures[0] if report.failures else "数据面重算失败"
        manual.append(ManualNote(None, None, "", f"数据面 / 登记表读不了：{reason}"))
        return WritePlan(edits=(), manual=tuple(manual))

    edits: list[Edit] = []
    for site in SITES:
        try:
            text = _read_text(root / site.file)
        except OSError as error:
            manual.append(
                ManualNote(site.file, None, f"{site.key}（{site.label}）", f"读不到文件：{error}")
            )
            continue
        matches = list(site.pattern.finditer(text))
        if not matches:
            manual.append(
                ManualNote(
                    site.file,
                    None,
                    f"{site.key}（{site.label}）",
                    "扫描失效：这条规则一处也没命中（站点结构变了 / 断言搬了家）",
                )
            )
            continue
        edits.extend(_site_edits(site, text, derived, matches=matches))

    kept, ambiguous = _resolve_edits(edits)
    for left, right in ambiguous:
        manual.append(
            ManualNote(
                left.file,
                left.line,
                f"{left.metric} / {right.metric}",
                f"同一段数字被两条规则要求改成不同的值（{left.new} ≠ {right.new}）",
            )
        )

    allowed: list[Edit] = []
    for edit in kept:
        if _inside_protected(edit, protected):
            manual.append(
                ManualNote(
                    edit.file,
                    edit.line,
                    edit.expression,
                    "落在人签登记表里（结构性禁区：四张表永不落盘）",
                )
            )
            continue
        allowed.append(edit)
    kept = tuple(allowed)

    for table in report.tables:
        for cell in table.hits:
            if cell.ok:
                continue
            drifted = [reading for reading in cell.readings if not reading.ok]
            old = new = None
            if len(drifted) == 1:
                old, new = drifted[0].value, drifted[0].expected
            detail = "；".join(reading.describe() for reading in drifted)
            # 票 08 E：`SIGNED_TABLES` 在这里**真的**参与判定（不是只被 len() 数一下）。
            if table.name in SIGNED_TABLES:
                reason = _TABLE_REASONS.get(
                    table.name, "人签登记表：工具只打印新值、不落盘"
                )
            else:
                reason = "不是能机械定位的站点数字：工具不猜"
            manual.append(
                ManualNote(
                    file=cell.file,
                    line=cell.line,
                    where=cell.expression,
                    reason=f"{detail}（{reason}）" if detail else reason,
                    old=old,
                    new=new,
                )
            )
    for failure in report.failures:
        manual.append(ManualNote(None, None, "", failure))

    return WritePlan(edits=kept, manual=tuple(manual), protected=protected)


def _protected_lines(root: Path) -> tuple[tuple[str, int, int], ...]:
    """四张人签表在 ``test_hero_catalog.py`` 里的行区间（结构性禁区的唯一来源）。"""
    path = root / "tests" / "test_hero_catalog.py"
    try:
        tables = _read_tables(path)
    except (OSError, SyntaxError, ValueError):
        return ()
    label = _relative(path, root=root)
    return tuple((label, first, last) for first, last in tables.protected_lines)


def _inside_protected(
    edit: Edit, protected: Sequence[tuple[str, int, int]]
) -> bool:
    """这处编辑是不是落进了人签登记表的行区间（区间含端点，按行号判）。"""
    return any(
        edit.file == file and first <= edit.line <= last for file, first, last in protected
    )


def _site_edits(
    site: Site,
    text: str,
    derived: Mapping[str, int],
    *,
    matches: Sequence[re.Match[str]],
) -> tuple[Edit, ...]:
    """一条具名规则在一份文件文本里的编辑：只取**漂移**的具名分组（区间 = 那几个数字）。"""
    edits: list[Edit] = []
    for match in matches:
        expression = " ".join(match.group(0).strip().splitlines()[-1].split())
        for metric, raw in match.groupdict().items():
            if raw is None or metric not in derived:
                continue  # 未知派生量由 collect() 记失败，这里不重复
            expected = derived[metric]
            if int(raw) == expected:
                continue
            start, end = match.span(metric)
            edits.append(
                Edit(
                    file=site.file,
                    line=text.count("\n", 0, end) + 1,
                    expression=expression,
                    metric=metric,
                    old=int(raw),
                    new=expected,
                    start=start,
                    end=end,
                )
            )
    return tuple(edits)


def _resolve_edits(
    edits: Sequence[Edit],
) -> tuple[tuple[Edit, ...], tuple[tuple[Edit, Edit], ...]]:
    """同一段数字被多条规则命中：值相同就**去重**，值不同就**整组放弃**（不猜）。

    ``catalog_total`` 的正则能在 ``len(SKILL_SHAPE) == len(CATALOG) == 217`` 里再命中一次
    （同一条派生量、同一个应为值）⇒ 去重后只改一次；真冲突时宁可报「这里要人工改」。
    """
    unique: dict[tuple[int, int], Edit] = {}
    ambiguous: list[tuple[Edit, Edit]] = []
    for edit in edits:
        key = (edit.start, edit.end)
        seen = unique.get(key)
        if seen is None:
            unique[key] = edit
        elif seen.new == edit.new:
            continue
        else:
            ambiguous.append((seen, edit))
            del unique[key]
    kept = tuple(sorted(unique.values(), key=lambda edit: (edit.file, edit.line, edit.start)))
    return kept, tuple(ambiguous)


def apply_plan(plan: WritePlan, *, root: Path = ROOT) -> tuple[Edit, ...]:
    """按计划落盘（**只**碰站点数字）；没有编辑时一个文件都不碰（幂等 = 零写入）。

    先读齐、逐处核对区间里**还是那个旧数字**，全部通过才写——不半途留下一套改了一半的文件。
    区间对不上 = 文件在规划之后变了 ⇒ 抛 :class:`ValueError`（由 CLI 报「这里要人工改」）。

    **结构性禁区**：任何落进人签登记表行区间（``plan.protected``）的编辑一律拒收——这条检查
    在 ``plan_write`` 之后、落盘之前再核一次（票 08 E：``apply_plan`` 也真的查 ``SIGNED_TABLES``
    派生的禁区，而不是信任调用方）。
    """
    blocked = [edit for edit in plan.edits if _inside_protected(edit, plan.protected)]
    if blocked:
        raise ValueError(
            f"{blocked[0].location()} 落在人签登记表里（结构性禁区），拒绝落盘"
        )

    grouped: dict[str, list[Edit]] = {}
    for edit in plan.edits:
        grouped.setdefault(edit.file, []).append(edit)

    texts: dict[str, str] = {}
    for filename in grouped:
        try:
            texts[filename] = _read_text(root / filename)
        except (OSError, UnicodeDecodeError) as error:
            raise ValueError(f"{filename} 读不了：{error}") from error
    for edit in plan.edits:
        if texts[edit.file][edit.start : edit.end] != str(edit.old):
            raise ValueError(f"{edit.location()} 在规划之后变了（那里不再是 {edit.old}）")

    for filename, edits in grouped.items():
        text = texts[filename]
        for edit in sorted(edits, key=lambda item: (item.start, item.end), reverse=True):
            text = text[: edit.start] + str(edit.new) + text[edit.end:]
        (root / filename).write_bytes(text.encode("utf-8"))
    return plan.edits


def render_plan(plan: WritePlan) -> str:
    """人看的改写计划：要改的站点 + 「这里要人工改」的登记表 / 失效项。"""
    lines = ["改写计划（--write）："]
    if plan.edits:
        lines.append(f"  机械改站点（{len(plan.edits)} 处）：")
        lines.extend(f"    {edit.describe()}" for edit in plan.edits)
    else:
        lines.append("  机械改站点（0 处）：没有能机械定位的漂移")
    if plan.manual:
        lines.append(
            f"  这里要人工改（{len(plan.manual)} 处；"
            f"{len(SIGNED_TABLES)} 张人签登记表不落盘）："
        )
        lines.extend(f"    {note.describe()}  ← 这里要人工改：{note.reason}" for note in plan.manual)
    else:
        lines.append("  这里要人工改：0 处")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write(*, root: Path = ROOT) -> int:
    """``--write``：计划 → 打印 → 落盘 → 用 ``--check`` 的**同一判据**复核并返回它的退出码。"""
    report = collect(root=root)
    plan = plan_write(report, root=root)
    print(render_plan(plan))

    try:
        applied = apply_plan(plan, root=root)
    except (OSError, ValueError) as error:
        print(f"  这里要人工改：{error}")
        return 1
    if applied:
        files = len({edit.file for edit in applied})
        print(f"  已改写 {len(applied)} 处站点数字（{files} 个文件）。")
    else:
        print("  文件零改动。")

    after = collect(root=root)
    print()
    print(render(after))
    return 0 if after.ok else 1


def main(argv: Sequence[str] | None = None, *, root: Path = ROOT) -> int:
    """``--check`` 的退出码就是判据：0 = 一致，1 = 有漂移 / 失败 / 扫描失效。

    ``--write`` 只改站点数字，改完重跑**同一判据**并以它的退出码收尾（有「要人工改」= 非零）。
    ``root`` 可注入（沙盒仓），CLI 上不暴露 —— 真仓永远是缺省值。
    """
    parser = argparse.ArgumentParser(
        description="将池构成清单（默认可读）：数据面重算 + 逐站点对账；--write 只改站点数字。",
    )
    parser.add_argument(
        "--check", action="store_true", help="对账不一致 ⇒ 非零退出（机器门禁用）"
    )
    output = parser.add_mutually_exclusive_group()
    output.add_argument(
        "--write",
        action="store_true",
        help="把能机械定位的站点数字改对，再用 --check 复核（四张人签登记表不写）",
    )
    output.add_argument(
        "--json", action="store_true", help="同一份事实的机器可读输出"
    )
    args = parser.parse_args(argv)

    if args.write:
        return _write(root=root)

    report = collect(root=root)
    if args.json:
        print(json.dumps(payload(report), ensure_ascii=False, indent=2))
    else:
        print(render(report))
    return 0 if (report.ok or not args.check) else 1


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main(sys.argv[1:]))
