"""票 02：effort 账本的「断言落点」必须可核（账本不存在则整组跳过）。

背景（retro-m2doc 票 02）：完整性账本 `.scratch/*/writeback.md` 的「落地票号 / 断言落点」
两列是手打的，且账本在 gitignored 的 `.scratch/`（review 的 diff 里看不到）——声称的落点
可能**当时并不存在**（`3d199bf` 修的正是这个）。

本检查把落点变成机械可核的形态：落点写成 `` `路径`「字面串」 ``，校验

1. 路径真实存在（仓库根相对，或 tests/ 下的裸文件名）；
2. 字面串在该文件里**逐字出现**（`check` 的描述串 / 字面串 / 源码注释都行）；
3. 两列都非空，且每条至少一个锚点——手打一句无锚点的散文会直接判红。

术语：这里的**断言落点**（landing anchor）指「哪个 test 文件里的哪一句断言」，
与 `GLOSSARY.md` 的「落点」（验收场景的落地位置）不同义。

账本不在 `.scratch/`（干净 clone）时整组跳过：门禁在任何 clone 全绿。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEDGER_GLOB = ".scratch/*/writeback.md"

TICKET_COL = "落地票号"
LANDING_COL = "断言落点"

# 一条锚点的形态：`路径`「字面串」。字面串本身允许含反引号（如 `` `horse_offense` ``）。
ANCHOR_RE = re.compile(r"`([^`]+)`「([^」]+)」")

# 覆盖元断言（绊线）：账本 = 主表 33 行 + §1b 追加/升格 11 行 = 44 行、44 个锚点。
# 少掉一整张子表就会低于绊线 ⇒ 判红，防「表被改格式 / 删表 ⇒ 一行没扫到也算通过」。
MIN_ROWS = 44
MIN_ANCHORS = 44


@dataclass(frozen=True)
class Claim:
    """一行账本声称的落点。"""

    ledger: Path
    line: int
    ticket: str
    anchors: tuple[tuple[str, str], ...]


def ledgers(root: Path = REPO_ROOT) -> list[Path]:
    """按路径序返回仓库里的账本文件。"""
    return sorted(root.glob(LEDGER_GLOB))


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(set(cell) <= set("-: ") for cell in cells)


def parse_ledger(path: Path) -> list[Claim]:
    """解析含「落地票号 / 断言落点」两列的所有表格行。"""
    claims: list[Claim] = []
    header: list[str] | None = None
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        cells = _cells(line)
        if not cells:
            header = None
            continue
        if TICKET_COL in cells and LANDING_COL in cells:
            header = cells
            continue
        if header is None or _is_separator(cells):
            continue
        ticket = cells[header.index(TICKET_COL)] if len(cells) == len(header) else ""
        landing = cells[header.index(LANDING_COL)] if len(cells) == len(header) else " ".join(cells)
        claims.append(
            Claim(
                ledger=path,
                line=number,
                ticket=ticket,
                anchors=tuple(ANCHOR_RE.findall(landing)),
            )
        )
    return claims


def _resolve(rel: str, root: Path) -> Path | None:
    if Path(rel).is_absolute() or ".." in Path(rel).parts:
        return None
    for candidate in (root / rel, root / "tests" / rel):
        if candidate.is_file():
            return candidate
    return None


def validate_ledger(path: Path, root: Path = REPO_ROOT) -> tuple[list[str], list[Claim]]:
    """校验一份账本；返回（错误列表, 解析到的行）。"""
    errors: list[str] = []
    claims = parse_ledger(path)
    if not claims:
        errors.append(f"{path}: 没解析到任何「{TICKET_COL} / {LANDING_COL}」行（表头被改？）")
        return errors, claims
    for claim in claims:
        where = f"{claim.ledger}:{claim.line}"
        if not claim.ticket:
            errors.append(f"{where}: 缺少『{TICKET_COL}』")
        if not claim.anchors:
            errors.append(f"{where}: 『{LANDING_COL}』没有任何 `路径`「字面串」锚点，不可核")
            continue
        for rel, literal in claim.anchors:
            target = _resolve(rel, root)
            if target is None:
                errors.append(f"{where}: 落点文件不存在（或越出仓库）：`{rel}`")
                continue
            if literal not in target.read_text(encoding="utf-8"):
                errors.append(f"{where}: `{rel}` 里找不到字面串「{literal}」")
    return errors, claims


def anchor_count(claims: list[Claim]) -> int:
    return sum(len(claim.anchors) for claim in claims)


# ---------------------------------------------------------------------------
# 合成反例与覆盖元断言的夹具
# ---------------------------------------------------------------------------

_VALID_LEDGER = """# 账本

| # | 落地票号 | 断言落点 |
|---|---|---|
| 1 | 01 | `test_demo.sh`「落点描述甲」 |
| 2 | 02 | `src/demo.py`「源码注释乙」 |
"""

_DEMO_SH = 'check "$DOC" \'x\' "落点描述甲"\n'
_DEMO_PY = '# 源码注释乙\n'


def _make_repo(tmp_path: Path, ledger_text: str = _VALID_LEDGER) -> Path:
    (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "test_demo.sh").write_text(_DEMO_SH, encoding="utf-8")
    (tmp_path / "src" / "demo.py").write_text(_DEMO_PY, encoding="utf-8")
    ledger = tmp_path / "writeback.md"
    ledger.write_text(ledger_text, encoding="utf-8")
    return ledger


def test_valid_ledger_has_no_errors(tmp_path: Path) -> None:
    ledger = _make_repo(tmp_path)
    errors, claims = validate_ledger(ledger, tmp_path)
    assert errors == []
    assert len(claims) == 2
    assert anchor_count(claims) == 2


def test_missing_file_is_red(tmp_path: Path) -> None:
    ledger = _make_repo(tmp_path)
    ledger.write_text(
        "| # | 落地票号 | 断言落点 |\n|---|---|---|\n| 1 | 01 | `no_such.sh`「落点描述甲」 |\n",
        encoding="utf-8",
    )
    errors, _ = validate_ledger(ledger, tmp_path)
    assert any("落点文件不存在" in error for error in errors), errors


def test_bogus_anchor_is_red(tmp_path: Path) -> None:
    """Done-when：把某行的落点改成不存在的断言串 ⇒ 变红。"""
    ledger = _make_repo(tmp_path)
    ledger.write_text(
        "| # | 落地票号 | 断言落点 |\n|---|---|---|\n| 1 | 01 | `test_demo.sh`「并不存在的落点」 |\n",
        encoding="utf-8",
    )
    errors, _ = validate_ledger(ledger, tmp_path)
    assert any("找不到字面串「并不存在的落点」" in error for error in errors), errors


def test_anchor_less_row_is_red(tmp_path: Path) -> None:
    """手打一句无锚点的散文（当前账本的形态）必须判红，否则检查就是空转。"""
    ledger = _make_repo(tmp_path)
    ledger.write_text(
        "| # | 落地票号 | 断言落点 |\n|---|---|---|\n| 1 | 01 | ADR-001 改判 B + 旧串 absent |\n",
        encoding="utf-8",
    )
    errors, _ = validate_ledger(ledger, tmp_path)
    assert any("没有任何" in error for error in errors), errors


def test_missing_ticket_is_red(tmp_path: Path) -> None:
    ledger = _make_repo(tmp_path)
    ledger.write_text(
        "| # | 落地票号 | 断言落点 |\n|---|---|---|\n| 1 |  | `test_demo.sh`「落点描述甲」 |\n",
        encoding="utf-8",
    )
    errors, _ = validate_ledger(ledger, tmp_path)
    assert any("缺少『落地票号』" in error for error in errors), errors


def test_path_traversal_is_red(tmp_path: Path) -> None:
    ledger = _make_repo(tmp_path)
    ledger.write_text(
        "| # | 落地票号 | 断言落点 |\n|---|---|---|\n| 1 | 01 | `../outside.txt`「落点描述甲」 |\n",
        encoding="utf-8",
    )
    errors, _ = validate_ledger(ledger, tmp_path)
    assert any("落点文件不存在" in error for error in errors), errors


def test_format_drift_yields_zero_rows(tmp_path: Path) -> None:
    """覆盖元断言的反面：表头被改 ⇒ 0 行 ⇒ 必须报『没解析到任何行』而不是静默通过。"""
    ledger = _make_repo(
        tmp_path,
        ledger_text="| # | 票号 | 落点 |\n|---|---|---|\n| 1 | 01 | `test_demo.sh`「落点描述甲」 |\n",
    )
    errors, claims = validate_ledger(ledger, tmp_path)
    assert claims == []
    assert any("没解析到任何" in error for error in errors), errors


# ---------------------------------------------------------------------------
# 真实账本：存在才跑（干净 clone 无 .scratch/ ⇒ 跳过）
# ---------------------------------------------------------------------------

_REAL = ledgers()
_skip_no_ledger = pytest.mark.skipif(not _REAL, reason="无 .scratch/*/writeback.md")


@_skip_no_ledger
def test_real_ledgers_landings_all_exist() -> None:
    rows = 0
    anchors = 0
    errors: list[str] = []
    for ledger in _REAL:
        ledger_errors, claims = validate_ledger(ledger, REPO_ROOT)
        errors.extend(ledger_errors)
        rows += len(claims)
        anchors += anchor_count(claims)
    assert errors == [], "\n".join(errors)
    # 覆盖元断言：防「表被改格式 ⇒ 一行没扫到也算通过」。
    assert rows >= MIN_ROWS, f"账本只解析到 {rows} 行，少于 {MIN_ROWS}"
    assert anchors >= MIN_ANCHORS, f"账本只有 {anchors} 个锚点，少于 {MIN_ANCHORS}"


@_skip_no_ledger
def test_real_ledger_mutated_anchor_is_red(tmp_path: Path) -> None:
    """Done-when（真实账本）：把某行落点改成不存在的断言串 ⇒ 检查变红；改回则绿。"""
    ledger = _REAL[0]
    original = ledger.read_text(encoding="utf-8")
    assert ANCHOR_RE.search(original), "真实账本里没有任何锚点可改"
    broken = ANCHOR_RE.subn(lambda m: f"`{m.group(1)}`「绝不存在的落点串」", original, count=1)
    assert broken[1] == 1
    probe = tmp_path / ledger.name
    probe.write_text(broken[0], encoding="utf-8")
    errors, _ = validate_ledger(probe, REPO_ROOT)
    assert any("绝不存在的落点串" in error for error in errors), errors
