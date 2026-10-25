r"""技能制作文档 ↔ 代码的**漂移审计**（票 23 的「制作武将技能」流程自检）。

`docs/agents/skill-authoring*.md` 是"新增技能时照着做"的说明书。说明书的死法只有一种：
**框架变了、文档没变**。四条机械核对把它钉住（姿态同 `tests/test_card_family_sites.py` /
`test_negation_sites.py` / `test_writeback_landing.py` 那几份注册表审计）：

1. **反向（封闭集全覆盖）**：代码里每个 `SeamKind` / `ModifierPoint` / `QueryKind` / `Zone` /
   `FrameKind` / 步骤类型 / 被武将技能用到的 `Timing`，都必须在文档里有归属。新加一个枚举成员
   而没登记 ⇒ 红。
2. **正向（落点可核）**：文档里点名的 `文件:符号` 必须真的存在（`src/…py:name`、
   `tools/…py:name`、`tools/sgs_web/static/…js:name`）。
3. **样板技能可核**：形状表里点名的样板技能 id 必须真的在 `CATALOG` 里（防止抄一个不存在的行）。
4. **硬停判据集固定**：`skill-authoring.md` 的硬停表必须恰好是这 13 条——增删要同时改本文件，
   不许悄悄放宽"什么时候必须停"。

外加两条轻量体检：文档本身有实质内容（≥ 30 行），两份工具能被导入并给出形状（CLI 坏掉时红）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs" / "agents"
PROCESS_DOC = DOCS_DIR / "skill-authoring.md"
SUB_DOCS = (
    DOCS_DIR / "skill-authoring" / "readings.md",
    DOCS_DIR / "skill-authoring" / "shapes.md",
    DOCS_DIR / "skill-authoring" / "edge-cases.md",
    DOCS_DIR / "skill-authoring" / "batch-ticket.md",
)

MIN_LINES = 30
"""每份文档至少这么多行：防"建个空文件也算有文档"。"""

STOP_IDS = frozenset(
    {"A1", "A2", "A3", "A4", "B1", "B2", "B3", "C1", "C2", "C3", "D1", "E1", "E2"}
)
"""硬停判据的**完整**集合（增删必须同时改本文件，这是有意的摩擦）。"""

_LANDING_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|js)):([A-Za-z_][A-Za-z0-9_.]*)`")
"""文档里的落点：`` `路径:符号` ``。"""

_SKILL_ID_RE = re.compile(r"`((?:hero|equip)\.[a-z0-9_.]+)`")
"""文档里的技能 id（形状表的"样板"列）。"""

_STOP_ROW_RE = re.compile(r"^\|\s*\*\*([A-E][0-9])\*\*\s*\|", re.MULTILINE)


def _all_docs_text() -> str:
    """四份子文档 + 过程文档的正文（注册表可以写在任意一份里）。"""
    return "\n".join(path.read_text(encoding="utf-8") for path in (PROCESS_DOC, *SUB_DOCS))


def _defined_in(source: str, symbol: str, *, js: bool) -> bool:
    """源码里有没有这个符号（Py 的 def/class/赋值；JS 的 function/const）。"""
    head = symbol.split(".", 1)[0]
    found = (
        f"def {head}" in source
        or f"class {head}" in source
        or re.search(rf"^{re.escape(head)}\s*[:=]", source, re.MULTILINE) is not None
    )
    if js:
        found = (
            found
            or f"function {head}" in source
            or f"const {head} " in source
            or f"const {head}=" in source
        )
    return found


def _resolve(path_literal: str) -> Path | None:
    """把文档里的路径字面量解析成仓库内的真实文件。

    文档为了好读会用**基名**（``candidates.js:isOwnZonePick``）。基名在两处白名单里找：
    ``tools/sgs_web/static/``（前端脚本）与 ``tools/``（只读工具）。
    """
    direct = REPO_ROOT / path_literal
    if direct.is_file():
        return direct
    if "/" in path_literal:
        return None
    for candidate in (
        REPO_ROOT / "tools" / "sgs_web" / "static" / path_literal,
        REPO_ROOT / "tools" / path_literal,
    ):
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# 0. 文档本身存在且有实质内容
# ---------------------------------------------------------------------------


def test_the_five_documents_exist_and_have_substance():
    """五份文档都在，且每份都不少于 30 行（空壳不算文档）。"""
    for path in (PROCESS_DOC, *SUB_DOCS):
        assert path.is_file(), f"缺少技能制作文档：{path.relative_to(REPO_ROOT)}"
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) >= MIN_LINES, (
            f"{path.relative_to(REPO_ROOT)} 只有 {len(lines)} 行（< {MIN_LINES}）"
        )


def test_the_process_doc_points_at_the_four_sub_documents():
    """过程文档必须真的指到四份子文档（否则 agent 找不到事实）。"""
    text = PROCESS_DOC.read_text(encoding="utf-8")
    for name in ("readings.md", "shapes.md", "edge-cases.md", "batch-ticket.md"):
        assert f"skill-authoring/{name}" in text, f"过程文档没有指向 {name}"


# ---------------------------------------------------------------------------
# 1. 反向：封闭集必须全部在文档里有归属
# ---------------------------------------------------------------------------


def _import_tools() -> None:
    """把 `tools/` 放进 sys.path（只读工具不是包，只能按目录导入）。"""
    tools_dir = str(REPO_ROOT / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)


def _surface() -> dict[str, tuple[str, ...]]:
    _import_tools()
    import dump_catalog

    return dump_catalog.enumerate_surface()


SURFACE_MINIMUMS = {
    "seams": 2,
    "points": 13,
    "queries": 9,
    "zones": 7,
    "frames": 14,
    "steps": 5,
}
"""各封闭集的下限（绊线）：数量塌下去说明枚举/导入失效，此时"没漏登记"是假绿。"""


def _missing_registrations(text: str, members: tuple[str, ...]) -> list[str]:
    """``members`` 里没在文档正文出现的那些（**纯函数**，便于用合成反例钉住）。"""
    return [member for member in members if member not in text]


def _hero_hook_timings() -> set[str]:
    """被武将技能挂过的时机（比 Timing 全量小得多，且"新增即须登记"）。"""
    _import_tools()
    import dump_catalog

    return {timing for shape in dump_catalog.collect() for timing, _ in shape.hooks}


@pytest.mark.parametrize(
    ("surface", "hint"),
    [
        ("seams", "shapes.md §3.2 接缝"),
        ("points", "shapes.md §3.3 修饰点"),
        ("queries", "shapes.md §3.4 询问种类"),
        ("zones", "shapes.md §3.5 区域"),
        ("frames", "shapes.md §3.6 帧型"),
        ("steps", "shapes.md §3.7 步骤类型"),
    ],
)
def test_every_closed_set_member_is_registered_in_the_docs(surface: str, hint: str):
    """代码里每个封闭集成员都要在文档里有一行归属；漏登记即红。"""
    text = _all_docs_text()
    members = _surface()[surface]
    assert len(members) >= SURFACE_MINIMUMS[surface], (
        f"{surface} 只枚举到 {len(members)} 个（< {SURFACE_MINIMUMS[surface]}）："
        "枚举面塌了，这条检查会变成假绿"
    )
    missing = _missing_registrations(text, members)
    assert not missing, (
        f"框架里有、文档里没有归属的 {surface}：{missing}\n"
        f"新增成员时请登记到 {hint}（并写清用在哪 / 为什么不用）"
    )


def test_the_registration_check_goes_red_on_a_synthetic_member():
    """覆盖元断言：给一个文档里必然没有的名字，检查必须报出来（防"永远绿"）。"""
    bogus = ("__NO_SUCH_MEMBER__",)
    assert _missing_registrations(_all_docs_text(), bogus) == list(bogus)
    assert _missing_registrations("__NO_SUCH_MEMBER__", bogus) == []


def test_every_hero_hook_timing_is_registered_in_the_docs():
    """被武将技能用到的时机都要登记（新技能用了新时机 ⇒ 先补文档）。"""
    text = _all_docs_text()
    missing = sorted(timing for timing in _hero_hook_timings() if timing not in text)
    assert not missing, (
        f"这些时机已被武将技能使用、但文档没有归属：{missing}\n登记到 shapes.md 的时机表"
    )


# ---------------------------------------------------------------------------
# 2. 正向：文档点名的落点必须存在
# ---------------------------------------------------------------------------


def test_every_landing_point_named_in_the_docs_exists():
    """文档里 `` `文件:符号` `` 形式的落点必须真的存在（防止文档指向不存在的代码）。"""
    problems: list[str] = []
    checked = 0
    for path in (PROCESS_DOC, *SUB_DOCS):
        for path_literal, symbol in _LANDING_RE.findall(path.read_text(encoding="utf-8")):
            target = _resolve(path_literal)
            if target is None:
                problems.append(f"{path.name}: 路径不存在 `{path_literal}`")
                continue
            checked += 1
            if not _defined_in(target.read_text(encoding="utf-8"), symbol, js=target.suffix == ".js"):
                problems.append(f"{path.name}: `{path_literal}` 里没有 `{symbol}`")
    assert checked >= 5, f"只解析到 {checked} 个落点（扫描疑似失效）"
    assert not problems, "技能制作文档指向了不存在的东西：\n" + "\n".join(f"  - {p}" for p in problems)


# ---------------------------------------------------------------------------
# 3. 样板技能必须真的存在
# ---------------------------------------------------------------------------


def test_every_template_skill_named_in_the_docs_exists_in_the_catalog():
    """形状表里的样板技能 id 必须存在于 `CATALOG`（防止让人去抄一个不存在的行）。"""
    from sgs.skills import CATALOG

    problems: list[str] = []
    seen = 0
    for path in (PROCESS_DOC, *SUB_DOCS):
        for skill_id in _SKILL_ID_RE.findall(path.read_text(encoding="utf-8")):
            seen += 1
            if skill_id not in CATALOG:
                problems.append(f"{path.name}: `{skill_id}` 不在 CATALOG 里")
    assert seen >= 15, f"文档里只解析到 {seen} 个技能 id（形状表被删了？）"
    assert not problems, "技能制作文档点名了不存在的样板技能：\n" + "\n".join(
        f"  - {p}" for p in problems
    )


# ---------------------------------------------------------------------------
# 4. 硬停判据集固定
# ---------------------------------------------------------------------------


def test_the_hard_stop_criteria_are_exactly_the_agreed_set():
    """硬停判据必须恰好是这 13 条：放宽"什么时候必须停"要同时改测试，不许悄悄来。"""
    ids = set(_STOP_ROW_RE.findall(PROCESS_DOC.read_text(encoding="utf-8")))
    assert ids == set(STOP_IDS), (
        f"硬停判据集变了。缺：{sorted(STOP_IDS - ids)}；多：{sorted(ids - STOP_IDS)}\n"
        "增删判据必须同时改 tests/test_skill_authoring_docs.py 的 STOP_IDS"
    )


def test_every_stop_criterion_has_exactly_one_table_row():
    """每条判据**恰好一行**：编号重复会让别处的引用（"见 D1"）指向两个地方。"""
    rows = _STOP_ROW_RE.findall(PROCESS_DOC.read_text(encoding="utf-8"))
    duplicated = sorted({item for item in rows if rows.count(item) > 1})
    assert not duplicated, f"硬停判据编号重复：{duplicated}"
    assert len(rows) == len(STOP_IDS), f"表行 {len(rows)} 条，判据集 {len(STOP_IDS)} 条"


# ---------------------------------------------------------------------------
# 5. 批次票面模板的骨架字段
# ---------------------------------------------------------------------------


def test_the_batch_ticket_template_keeps_its_required_fields():
    """一将一票的模板必须保留 tracker 约定字段与两条腿的 DoD。"""
    text = (DOCS_DIR / "skill-authoring" / "batch-ticket.md").read_text(encoding="utf-8")
    for must in ("Status:", "Touches:", "规则原文:", "## 批次清单", "## 摘出记录", "## 收口", "## Answer"):
        assert must in text, f"批次票面模板缺少 `{must}`"
    for leg in ("已实现", "已摘出"):
        assert leg in text, f"批次票面模板缺少 DoD 的一条腿：{leg}"


# ---------------------------------------------------------------------------
# 6. 工具能被导入并给出形状（CLI 坏掉时红）
# ---------------------------------------------------------------------------


def test_the_inventory_tool_reports_every_hero_skill():
    """`tools/dump_catalog.py` 能导入，且每条武将技能都有形状指纹。"""
    _import_tools()
    import dump_catalog

    shapes = dump_catalog.collect()
    assert len(shapes) >= 90, f"只盘点到 {len(shapes)} 条武将技能"
    for shape in shapes:
        assert shape.fingerprint(), f"{shape.skill_id} 没有形状指纹"
        assert shape.skill_id.startswith("hero.")


def test_the_similarity_tool_ranks_candidates_and_skips_itself():
    """`tools/similar_skills.py` 能按形状排序，且不把参照技能自己排进结果。"""
    _import_tools()
    import dump_catalog
    import similar_skills

    shapes = dump_catalog.collect()
    scored = similar_skills.rank(shapes, like="hero.jie_sunshangxiang.jieyin", limit=3)
    assert scored, "按【结姻】的形状找不到任何相似技能（反查失效？）"
    assert all(item.shape.skill_id != "hero.jie_sunshangxiang.jieyin" for item in scored)
    assert [item.score for item in scored] == sorted((i.score for i in scored), reverse=True)
