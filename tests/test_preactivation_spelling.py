"""防回潮：`preactivation` 家族的拼写（W5-00 / J4）。

W4 把「预发动」相关的字段、载荷键、开关写成了少一个 `c` 的错字族；W5-00 一次性改名到
正确拼写（`preactivations` / `preactivation` / `preactivated`），不保留旧键双读
（`_FrozenModel` 是 ``extra="forbid"``，仓库也没有旧键别名先例）。

本测试是那条改名的**机械判据**：`src/`、`tools/`、`tests/`、`开发文档/` 与
`GLOSSARY.md` 里错字族零命中。`.scratch/`（已收口 effort 的历史票面，含
`m4-preactivation/`）、`.pyc`、`.jsonl`（金录像逐字节冻结）显式排除。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SCAN_DIRS: tuple[Path, ...] = (
    REPO_ROOT / "src",
    REPO_ROOT / "tools",
    REPO_ROOT / "tests",
    REPO_ROOT / "开发文档",
)
SCAN_FILES: tuple[Path, ...] = (REPO_ROOT / "GLOSSARY.md",)

EXCLUDED_DIR_NAMES = frozenset({".scratch"})
EXCLUDED_SUFFIXES = frozenset({".pyc", ".jsonl"})

# 错字族 = 正确拼写少一个 `c`。拆开拼出来：本文件也在 `tests/` 的扫描范围内，
# 写字面量会让这条测试自己命中自己（永远红）。
WRONG_FAMILY = "pre" + "ativ"
RIGHT_FAMILY = "pre" + "activ"
WRONG_FAMILY_BYTES = WRONG_FAMILY.encode()


def _iter_scanned_files(
    dirs: tuple[Path, ...] = SCAN_DIRS, files: tuple[Path, ...] = SCAN_FILES
) -> Iterator[Path]:
    """扫描面内的普通文件：按目录递归，跳过排除的目录名 / 后缀。"""
    for directory in dirs:
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            if EXCLUDED_DIR_NAMES.intersection(path.parts):
                continue
            if path.suffix in EXCLUDED_SUFFIXES:
                continue
            yield path
    for path in files:
        if path.is_file() and path.suffix not in EXCLUDED_SUFFIXES:
            yield path


def _offending_lines(
    dirs: tuple[Path, ...] = SCAN_DIRS, files: tuple[Path, ...] = SCAN_FILES
) -> list[str]:
    """错字族的命中位置（``路径:行号: 该行内容``）；零命中即空列表。"""
    hits: list[str] = []
    for path in _iter_scanned_files(dirs, files):
        data = path.read_bytes()
        if WRONG_FAMILY_BYTES not in data:
            continue
        for number, line in enumerate(data.splitlines(), start=1):
            if WRONG_FAMILY_BYTES in line:
                where = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
                hits.append(f"{where}:{number}: {line.decode('utf-8', 'replace').strip()}")
    return hits


def test_the_wrong_spelling_of_preactivation_has_no_hits() -> None:
    """`src/`、`tools/`、`tests/`、`开发文档/`、`GLOSSARY.md` 里错字族零残留。"""
    hits = _offending_lines()
    assert not hits, (
        "「预发动」的错字族（正确拼写少一个 `c`）仍有残留，W5-00 要求一次改净：\n"
        + "\n".join(hits)
    )


def test_the_scan_reaches_the_module_and_the_wiring_files() -> None:
    """覆盖元断言：扫描面真的走到模块、投影、前端与读表（防 glob 失效后空转通过）。"""
    scanned = set(_iter_scanned_files())
    must_scan = {
        REPO_ROOT / "src" / "sgs" / "skills" / (RIGHT_FAMILY + "ation.py"),
        REPO_ROOT / "tools" / "sgs_web" / "projection.py",
        REPO_ROOT / "tools" / "sgs_web" / "static" / "app.js",
        REPO_ROOT / "tests" / "replays" / "scenarios.py",
        REPO_ROOT / "GLOSSARY.md",
    }
    missing = sorted(str(path.relative_to(REPO_ROOT)) for path in must_scan - scanned)
    assert not missing, f"扫描面漏了这些文件：{missing}"
    assert not (REPO_ROOT / "src" / "sgs" / "skills" / (WRONG_FAMILY + "ation.py")).exists(), (
        "旧拼写模块不应存在：`git mv` 到 preactivation.py，不留转发壳"
    )


def test_the_scan_catches_a_synthetic_counterexample(tmp_path: Path) -> None:
    """覆盖元断言的反面：合成文件里的错字族必须被判红，否则这条测试只是空转。"""
    probe = tmp_path / "probe.py"
    probe.write_text(f"KEY = {WRONG_FAMILY + 'ation_answers'!r}\n", encoding="utf-8")
    hits = _offending_lines(dirs=(tmp_path,), files=())
    assert hits, "合成反例未被判红：扫描器/断言口径失效"
