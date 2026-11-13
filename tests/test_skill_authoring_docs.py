r"""技能制作文档 ↔ 代码的**漂移审计**（票 23 的「制作武将技能」流程自检）。

`docs/agents/skill-authoring*.md` 是"新增技能时照着做"的说明书。说明书的死法只有一种：
**框架变了、文档没变**。四条机械核对把它钉住（姿态同 `tests/test_card_family_sites.py` /
`test_negation_sites.py` / `test_writeback_landing.py` 那几份注册表审计）：

1. **反向（封闭集全覆盖）**：代码里每个 `SeamKind` / `ModifierPoint` / `QueryKind` / `Zone` /
   `FrameKind` / 步骤类型 / 被武将技能用到的 `Timing`，都必须在文档里有归属。新加一个枚举成员
   而没登记 ⇒ 红。
2. **正向（落点可核）**：文档里点名的 `文件:符号` 必须真的存在（`src/…py:name`、
   `tools/…py:name`、`tools/sgs_web/static/…js:name`）。`diy-heroes.md` 那种不带符号的
   **裸路径**（`src/…` / `tools/…` 等）由第 6 条用同一姿态核。
3. **样板技能可核**：形状表里点名的样板技能 id 必须真的在 `CATALOG` 里（防止抄一个不存在的行）。
4. **硬停判据集固定**：`skill-authoring.md` 的硬停表必须恰好是这 13 条——增删要同时改本文件，
   不许悄悄放宽"什么时候必须停"。

外加两条轻量体检：文档本身有实质内容（≥ 30 行），两份工具能被导入并给出形状（CLI 坏掉时红）。

**第 5 条**是同仓的那一半（票 01「入口 skill 进仓」）：`/hero-skill` 的入口文件
`skills/hero-skill/SKILL.md` 就住在仓里、与 `docs/agents/skill-authoring.md` 同进退。它的死法
比文档更隐蔽——文档改了指不到、或它自己悄悄复述了一份旧口径。瘦身后的入口只留锚点与一行
「判据版本」，于是能机械核的恰好三件：**仓内真的有它**、**引用的 § 真在**、**声明的硬停条数
与 `STOP_IDS` 同数**。家目录的 `~/.dsh/skills/hero-skill/SKILL.md` 只是本机安装副本：仓内缺席
时才兜底读它，而**仓内缺席本身就是红**（CI 上尤其不许 skip——那正是它漂了没人发现的原因）。

**第 6 条**（diy-heroes 票 03）：`docs/agents/diy-heroes.md` 是**仓外作者的唯一入口**，按同一
姿态进审计——文件在且够长；点名的**仓库内路径**（`src/…` / `tools/…` / `skills/…` / `docs/…` /
`examples/…` / `tests/…` 的裸路径，散文与代码块都算）都真实存在，`(new)`（issue-tracker 的
"还没建"约定）显式标了才跳过；7 个二级标题齐全；三向指针（`skill-authoring.md` /
`diy-heroes.md` / `README.md`）不断；点名的四个子系统名词在源码里真的出现；且 `docs/agents/`
下任何 `.md` 都不许再出现 `~/.dsh`（票 02 已把入口 skill 搬进仓，这条防回退）。它**不**承载
硬停判据的条数契约：DIY 文档只**指向**停表，把第 4 条那 13 条的核对套到它身上会让两边的契约
互相污染（票面 §1.5 的硬否定）。
"""

from __future__ import annotations

import os
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
    for must in (
        "Status:",
        "Touches:",
        "规则原文:",
        "规范确认:",
        "### 询问剧本",
        "## 批次清单",
        "## 摘出记录",
        "## 收口",
        "## Answer",
    ):
        assert must in text, f"批次票面模板缺少 `{must}`"
    for leg in ("已实现", "已摘出"):
        assert leg in text, f"批次票面模板缺少 DoD 的一条腿：{leg}"


def test_the_spec_gate_survives_in_both_the_process_doc_and_the_ticket():
    """规范确认门（用户加的四条）不许悄悄消失：过程文档有规矩，票面模板有落地的格子。

    门的三件事各有承载体，缺一件这条流程就退回"边写边定"：过程文档写"签了才动工 / 逐字 /
    死路不算兜底"，票面写"规范确认: / 询问剧本 / 套用模板"。
    """
    process = PROCESS_DOC.read_text(encoding="utf-8")
    ticket = (DOCS_DIR / "skill-authoring" / "batch-ticket.md").read_text(encoding="utf-8")
    for must in ("2.1 规范确认门", "签了才动工", "逐字", "死路不算兜底"):
        assert must in process, f"过程文档缺少规范确认门的 `{must}`"
    for must in ("规范确认:", "### 询问剧本", "套用模板"):
        assert must in ticket, f"票面模板缺少规范确认门的 `{must}`"


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


# ---------------------------------------------------------------------------
# 7. 入口 skill（`/hero-skill`）住在本仓：存在性 / 锚点 / 判据版本都是硬约束
# ---------------------------------------------------------------------------

ENTRY_SKILL = REPO_ROOT / "skills" / "hero-skill" / "SKILL.md"
"""`/hero-skill` 触发的入口文件。**它在版本库里**——判据全在本仓文档，入口必须同仓（票 01）。"""

HOME_SKILL = Path.home() / ".dsh" / "skills" / "hero-skill" / "SKILL.md"
"""本机安装副本，只做**兜底**：仓内那份缺席时才读它；CI 上不兜底（缺席即红）。"""

_SECTION_RE = re.compile(r"^#{1,6}\s+(\d+(?:\.\d+)?)\.?\s", re.MULTILINE)
"""过程文档的节号集合（`## 2.1 规范确认门` / `## 4. 硬停判据` 两种写法都认）。"""

_ANCHOR_RE = re.compile(r"`skill-authoring\.md`\s*§(\d+(?:\.\d+)?)")
"""入口 skill 里的锚点：`` `skill-authoring.md` §2.1 ``。"""

_SKILL_VERSION_RE = re.compile(
    r"判据版本：与 `docs/agents/skill-authoring\.md` 同仓（硬停判据恰 (\d+) 条）"
)
"""入口 skill 的判据版本行；条数由本文件的 `STOP_IDS` 反向核，不再钉一个会漂的 commit。"""

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
"""YAML frontmatter 块（`name` / `description` / `disable-model-invocation` 住这里）。"""


def _display(path: Path) -> str:
    """报错里的路径：仓内的写相对路径，仓外的（反例的 `tmp_path`）写绝对路径。"""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _entry_skill_text() -> str:
    """入口 skill 正文：仓内优先；缺席时才看家目录那份（CI 上直接判红）。"""
    if ENTRY_SKILL.is_file():
        return ENTRY_SKILL.read_text(encoding="utf-8")
    if HOME_SKILL.is_file():
        return HOME_SKILL.read_text(encoding="utf-8")
    if os.environ.get("CI"):
        pytest.fail(f"仓内入口 skill 缺席：{_display(ENTRY_SKILL)}（CI 上不许 skip）")
    pytest.skip(f"仓内入口 skill 缺席（{ENTRY_SKILL}），本机也没装 `/hero-skill`")


def test_the_entry_skill_lives_in_the_repo():
    """入口 skill 必须在版本库里，且仍是"薄入口"：frontmatter 对、指到总入口文档。

    这是票 01 的主要交付：位置从家目录搬进仓后，"别的机器 / CI 上永不检查"这个后门就关了。
    """
    assert ENTRY_SKILL.is_file(), (
        f"仓内没有入口 skill：{_display(ENTRY_SKILL)}\n"
        "`/hero-skill` 的判据全在本仓文档里，入口必须跟着仓走（家目录副本只是本机安装）"
    )
    text = ENTRY_SKILL.read_text(encoding="utf-8")
    front = _FRONTMATTER_RE.match(text)
    assert front, f"{_display(ENTRY_SKILL)} 开头不是 YAML frontmatter"
    meta = front.group(1)
    for must in ("name: hero-skill", "description:", "disable-model-invocation: true"):
        assert must in meta, (
            f"入口 skill 的 frontmatter 缺少 `{must}`（`name` / `description` / "
            "`disable-model-invocation: true` 都要与搬之前的文件一字不改）"
        )
    assert "docs/agents/skill-authoring.md" in text, (
        f"{_display(ENTRY_SKILL)} 没有指向总入口 `docs/agents/skill-authoring.md`："
        "入口只该指路，不该自包含"
    )


def test_the_entry_skill_points_at_sections_that_exist():
    """入口 skill 引用的 `` `skill-authoring.md` §N `` 必须真的存在。

    它瘦身后只留锚点，所以"指向一个不存在的节"就是它的全部分量——这条钉住那一半。
    """
    text = _entry_skill_text()
    anchors = _ANCHOR_RE.findall(text)
    assert anchors, (
        f"{ENTRY_SKILL} 里没有一处 `skill-authoring.md` §N 锚点：入口不该自包含，"
        "它只该指路"
    )
    sections = set(_SECTION_RE.findall(PROCESS_DOC.read_text(encoding="utf-8")))
    missing = sorted({anchor for anchor in anchors if anchor not in sections})
    assert not missing, (
        f"入口 skill 指向不存在的节：{missing}\n"
        f"`skill-authoring.md` 现有节号：{sorted(sections)}\n"
        f"改节号要同时改 {ENTRY_SKILL}"
    )


def test_the_entry_skill_declares_the_hard_stop_count():
    """入口 skill 的「判据版本」行必须与 `STOP_IDS` 同数。

    进仓后不再钉一个会漂的 commit（文档就在旁边）：改钉"它依据哪份文档、几条硬停"，条数由
    本文件的 `STOP_IDS` 反向核——增删判据而没改入口，这条就红。
    """
    text = _entry_skill_text()
    declared = _SKILL_VERSION_RE.findall(text)
    assert declared, (
        f"{ENTRY_SKILL} 没有「判据版本」行（写成 "
        "`判据版本：与 `docs/agents/skill-authoring.md` 同仓（硬停判据恰 13 条）`）："
        "没有它就无法判断这份入口落在哪版判据上"
    )
    assert declared == [str(len(STOP_IDS))], (
        f"{ENTRY_SKILL} 声明的硬停判据条数是 {declared}，本文件钉住的是 {len(STOP_IDS)} 条："
        "增删判据要同时改 `STOP_IDS` 与入口 skill"
    )


def test_every_entry_skill_check_bites_on_a_broken_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """覆盖元断言：把入口换成一份坏的，上面三条必须全红（防断言空转）。"""
    broken = tmp_path / "SKILL.md"
    broken.write_text(
        "---\nname: not-hero-skill\ndescription: 坏的\ndisable-model-invocation: false\n"
        "---\n\n# 入口\n\n没有锚点，也没有判据版本。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ENTRY_SKILL", broken)
    still_green: list[str] = []
    for name, check in (
        ("lives_in_the_repo", test_the_entry_skill_lives_in_the_repo),
        ("points_at_sections_that_exist", test_the_entry_skill_points_at_sections_that_exist),
        ("declares_the_hard_stop_count", test_the_entry_skill_declares_the_hard_stop_count),
    ):
        try:
            check()
        except AssertionError:
            continue
        still_green.append(name)
    assert not still_green, f"这些检查对坏入口仍然通过（断言空转）：{still_green}"


def test_a_missing_entry_skill_is_red_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本票的主要交付：仓内入口缺席**直接红**，不许 skip（旧软依赖只剩下面那条兜底档）。"""
    monkeypatch.setattr(sys.modules[__name__], "ENTRY_SKILL", tmp_path / "SKILL.md")
    with pytest.raises(AssertionError):
        test_the_entry_skill_lives_in_the_repo()


def test_the_home_fallback_only_skips_outside_ci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """家目录兜底的边界：CI 上缺席判红，非 CI + 本机也没装才 skip。"""
    monkeypatch.setattr(sys.modules[__name__], "ENTRY_SKILL", tmp_path / "SKILL.md")
    monkeypatch.setattr(sys.modules[__name__], "HOME_SKILL", tmp_path / "home-SKILL.md")
    monkeypatch.setenv("CI", "true")
    with pytest.raises(pytest.fail.Exception):
        _entry_skill_text()
    monkeypatch.delenv("CI")
    with pytest.raises(pytest.skip.Exception):
        _entry_skill_text()



# ---------------------------------------------------------------------------
# 8. DIY 出口文档（`docs/agents/diy-heroes.md`）的机械审计（diy-heroes 票 03）
# ---------------------------------------------------------------------------

DIY_DOC = DOCS_DIR / "diy-heroes.md"
"""仓外作者的**唯一入口**（票 02）：它一旦指错文件，外人第一步就撞墙。"""

README = REPO_ROOT / "README.md"

DIY_SECTIONS = ("两条路", "装 skill", "最小环境", "要改的文件", "常见错误", "版权边界", "提案流程")
"""DIY 文档的**七个二级标题**：它就是这份文档的契约面，缺一个就红。"""

DIY_SUBSYSTEM_NOUNS = (
    "HERO_SUPPLEMENT_PACK_IDS",
    "pool_census.py",
    "hero_skill_id",
    "CATALOG",
)
"""文档必须点名、且源码里必须真的有的子系统名词（反向：文档不许发明符号）。"""

_HOME_REF = "~/.dsh"
"""家目录引用（票 02 已把入口 skill 搬进仓；安装路径写 `$HOME/.dsh/` 是另一回事）。"""

_DIY_PATH_RE = re.compile(
    r"(?<![\w./-])((?:src|tools|skills|docs|examples|tests)/[A-Za-z0-9_./*+-]+)"
)
"""文档点名的**仓库内路径**（散文与代码块都算；`.venv/` 这类仓外路径不在扫描面里）。"""

_NEW_MARKER_RE = re.compile(r"`?\s*\(new\)")
"""`docs/agents/issue-tracker.md` 的 `(new)` 约定：**显式标了**才跳过存在性检查。"""

_DIY_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)

_DIY_ORDINAL_RE = re.compile(r"^\d+[.、]\s*")
"""标题前面的「1. 」序号：比对时剥掉，只比**节名**。"""

_DIY_SECTION_SEPARATORS = frozenset({"：", ":", " ", "（", "("})
"""节名之后允许出现的分隔符。

`## 5. 常见错误与排错` 的「与」**不是**分隔符 ⇒ 它不能冒充「常见错误」——旧的
`" | ".join(headings)` 子串断言会放过它（票 08 F）。
"""


def _diy_named_paths(text: str) -> tuple[tuple[str, bool], ...]:
    """``(路径, 是否显式标了 `(new)`)``，按出现顺序去重；`<占位>` 形式不算路径。"""
    out: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for match in _DIY_PATH_RE.finditer(text):
        if text.startswith(("<", ">"), match.end()):
            continue  # `heroes.<你的包 id>.json` 这类模板不是仓内路径
        path = match.group(1).rstrip(".,;:，。；：")
        if path in seen:
            continue
        seen.add(path)
        out.append((path, _NEW_MARKER_RE.match(text, match.end()) is not None))
    return tuple(out)


def _diy_missing_paths(text: str, *, root: Path = REPO_ROOT) -> list[str]:
    """文档点名、但在 ``root`` 下找不到的路径（显式标了 `(new)` 的跳过）。"""
    missing: list[str] = []
    for path, marked_new in _diy_named_paths(text):
        if marked_new:
            continue
        if "*" in path:
            if not list(root.glob(path)):
                missing.append(path)
        elif not (root / path).exists():
            missing.append(path)
    return missing


def _diy_headings(text: str) -> tuple[str, ...]:
    """文档的二级标题（剥掉「1. 」序号）：`## 行` 才算，正文里提一嘴不算。"""
    return tuple(
        _DIY_ORDINAL_RE.sub("", heading.strip())
        for heading in _DIY_HEADING_RE.findall(text)
    )


def _diy_missing_sections(text: str) -> list[str]:
    """文档缺（或写歪）的那几个二级标题：**逐个**比对（顺序 + 前缀 + 分隔符）。

    旧实现把标题用 ``" | "`` 拼起来做子串匹配：`## 5. 常见错误与排错` 也能满足
    「常见错误」——超集标题于是静默通过（票 08 F）。现在第 ``i`` 个标题必须**以第 ``i`` 个
    节名开头**，且节名之后紧跟分隔符或行尾。
    """
    headings = _diy_headings(text)
    missing: list[str] = []
    for index, section in enumerate(DIY_SECTIONS):
        heading = headings[index] if index < len(headings) else ""
        rest = heading[len(section) :] if heading.startswith(section) else None
        if rest is None or (rest and rest[0] not in _DIY_SECTION_SEPARATORS):
            missing.append(section)
    return missing


def _diy_missing_pointers() -> list[str]:
    """三向指针：过程文档 ↔ DIY 文档 ↔ README（都读真文件）。"""
    pairs = (
        (PROCESS_DOC, "diy-heroes.md"),
        (README, "docs/agents/diy-heroes.md"),
        (DIY_DOC, "skill-authoring.md"),
    )
    return [
        f"{_display(path)} 没指向 `{needle}`"
        for path, needle in pairs
        if needle not in path.read_text(encoding="utf-8")
    ]


def _source_contains(noun: str) -> bool:
    """`src/` + `tools/` 的 Python 源码里有没有这个名词（文档不许发明符号）。"""
    for base in (REPO_ROOT / "src", REPO_ROOT / "tools"):
        for path in base.rglob("*.py"):
            if noun in path.read_text(encoding="utf-8"):
                return True
    return False


def _diy_missing_nouns(text: str) -> list[str]:
    """文档没点名的那几个子系统名词。"""
    return [noun for noun in DIY_SUBSYSTEM_NOUNS if noun not in text]


def _diy_invented_nouns(nouns: tuple[str, ...] = DIY_SUBSYSTEM_NOUNS) -> list[str]:
    """源码里根本不存在的名词（反向：文档点名了源码里没有的符号）。"""
    return [noun for noun in nouns if not _source_contains(noun)]


def _home_references(directory: Path) -> list[tuple[Path, list[str]]]:
    """目录下所有 ``*.md`` 里的家目录引用：``(文件, 含引用的行)``。"""
    offenders: list[tuple[Path, list[str]]] = []
    for path in sorted(directory.rglob("*.md")):
        hits = [
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if _HOME_REF in line
        ]
        if hits:
            offenders.append((path, hits))
    return offenders


def test_the_diy_doc_is_audited_like_the_authoring_doc() -> None:
    """§1.1–1.3：文件在且够长、点名的仓内路径都在、七节齐全、三向指针、名词不发明。"""
    assert DIY_DOC.is_file(), f"缺少 DIY 出口文档：{_display(DIY_DOC)}（票 02 的交付）"
    text = DIY_DOC.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) >= MIN_LINES, (
        f"{_display(DIY_DOC)} 只有 {len(lines)} 行（< {MIN_LINES}）：空壳不算文档"
    )

    problems = [
        *(f"点名的仓内路径不存在：{path}" for path in _diy_missing_paths(text)),
        *(f"缺少二级标题：{section}" for section in _diy_missing_sections(text)),
        *_diy_missing_pointers(),
        *(f"文档没点名子系统名词：{noun}" for noun in _diy_missing_nouns(text)),
        *(f"文档点名了源码里没有的名词：{noun}" for noun in _diy_invented_nouns()),
    ]
    assert not problems, "diy-heroes.md 的机械审计不过：\n" + "\n".join(
        f"  - {problem}" for problem in problems
    )


def test_no_home_directory_reference_in_agent_docs() -> None:
    """`docs/agents/` 下的文档不许再出现 `~/.dsh`：入口 skill 已进仓，家目录只是本机安装副本。"""
    offenders = _home_references(DOCS_DIR)
    assert not offenders, "这些 agent 文档还写着家目录路径（票 02 已把入口 skill 搬进仓）：\n" + (
        "\n".join(f"  - {_display(path)}: {hits[0]}" for path, hits in offenders)
    )


def _broken_diy_copy(directory: Path, text: str) -> Path:
    """在 ``directory`` 里落一份坏掉的 DIY 文档（反向自检的靶子）。"""
    broken = directory / "diy-heroes.md"
    broken.write_text(text, encoding="utf-8")
    return broken


def test_a_diy_doc_missing_a_section_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向自检①：少一个二级标题 ⇒ 审计必须红，而且报的是缺的那一节。"""
    text = DIY_DOC.read_text(encoding="utf-8").replace("## 5. 常见错误", "## 5. 排错")
    monkeypatch.setattr(sys.modules[__name__], "DIY_DOC", _broken_diy_copy(tmp_path, text))
    with pytest.raises(AssertionError, match="常见错误"):
        test_the_diy_doc_is_audited_like_the_authoring_doc()


def test_a_diy_doc_pointing_at_a_nonexistent_path_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向自检②：点一个不存在的仓内路径 ⇒ 审计必须红，而且报的是那条路径。"""
    text = DIY_DOC.read_text(encoding="utf-8") + "\n见 `src/sgs/data/no_such_module.py`。\n"
    monkeypatch.setattr(sys.modules[__name__], "DIY_DOC", _broken_diy_copy(tmp_path, text))
    with pytest.raises(AssertionError, match="no_such_module"):
        test_the_diy_doc_is_audited_like_the_authoring_doc()


def test_the_diy_path_audit_skips_only_paths_marked_new() -> None:
    """`(new)` 是"还没建的文件"的约定：**显式标了**才跳过，不标就必须真的在。"""
    text = DIY_DOC.read_text(encoding="utf-8")
    pending = "examples/diy-hero/not_created_yet.md"
    assert _diy_missing_paths(f"{text}\n  `{pending}` (new)\n") == []
    assert _diy_missing_paths(f"{text}\n  `{pending}`\n") == [pending]


def test_a_diy_doc_with_a_superset_section_title_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向自检④：`## 5. 常见错误与排错` 这种**超集**标题也必须红（票 08 F）。

    旧的子串断言会把它当成「常见错误」放过去；新断言逐个比对标题行 + 分隔符。
    """
    text = DIY_DOC.read_text(encoding="utf-8").replace(
        "## 5. 常见错误", "## 5. 常见错误与排错"
    )
    assert _diy_missing_sections(text) == ["常见错误"]

    broken = _broken_diy_copy(tmp_path, text)
    monkeypatch.setattr(sys.modules[__name__], "DIY_DOC", broken)
    with pytest.raises(AssertionError, match="常见错误"):
        test_the_diy_doc_is_audited_like_the_authoring_doc()


def test_the_diy_noun_audit_bites_on_an_invented_symbol() -> None:
    """覆盖元断言：源码里根本不存在的名词必须被报出来（防"永远绿"）。"""
    assert _diy_invented_nouns(("NO_SUCH_SYMBOL__",)) == ["NO_SUCH_SYMBOL__"]
    assert _diy_invented_nouns(("CATALOG",)) == []


def test_the_home_directory_ban_bites_on_a_tmp_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """反向自检③：tmp 目录里放一份带 `~/.dsh/` 的文档 ⇒ 家目录检查必须红。"""
    (tmp_path / "diy-heroes.md").write_text(
        DIY_DOC.read_text(encoding="utf-8")
        + "\n家目录副本：`~/.dsh/skills/hero-skill/SKILL.md`。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "DOCS_DIR", tmp_path)
    with pytest.raises(AssertionError, match=r"~/.dsh"):
        test_no_home_directory_reference_in_agent_docs()
