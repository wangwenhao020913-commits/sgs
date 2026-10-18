#!/usr/bin/env python3
"""验收清扫（retro M4）：把 ``## Answer`` 里引用的 ``test_*`` 从"存在"升级为"存在且绿"。

为什么是独立工具、不是门禁阶段：``.scratch/`` 是 gitignore 的，门禁（``tests/run_all.sh``）
**不能依赖它**；本工具只读、按需跑，作为交付前的收口动作（见 ``AGENTS.md`` 注意事项 1）。

用法::

    .venv/bin/python tools/check_acceptance.py            # 扫全部 .scratch/*/issues/*.md
    .venv/bin/python tools/check_acceptance.py .scratch/w4-fix .scratch/m4-preativation

判定：

1. 只扫带 ``## Answer`` 的票，取 Answer 到下一个 ``## `` 标题之间的正文；
2. 抽出 ``test_*`` token，跳过**不是断言引用**的 token——
   - 模块名（``tests/test_web.py`` 这种：``tests/`` 下有同名文件，跑 ``-k`` 只会空转）；
   - 省略号占位（``test_fankui_...``：token 以 ``_`` 结尾或紧跟 ``...`` 的一族引用）；
   - 同一行明确写"不存在"的名字（票面点名、本分支没有的那个，作者已在 Answer 里说明）；
3. 每个引用名都必须出现在 ``tests/`` 的 ``def test_`` 集合里；缺失即非零退出并列出位置；
4. 存在的名字拼成**一条** ``pytest -q -k "a or b or ..."``；任何红即非零退出。

工具不改任何文件（pytest 用 ``-p no:cacheprovider`` 关掉缓存写入）。
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
PYTEST = ROOT / ".venv" / "bin" / "pytest"

_DEF_RE = re.compile(r"^\s*def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)
_CITE_RE = re.compile(r"test_[A-Za-z0-9_]+")
_ANSWER_RE = re.compile(r"^## Answer\b.*$", re.MULTILINE)
_SECTION_RE = re.compile(r"^## ", re.MULTILINE)
"""``## Answer`` 之后遇到下一个二级标题即结束（``###`` 子节仍属于 Answer）。"""

_NON_CITATION = ("不存在",)
"""一行里出现这个词 ⇒ 该 token 是在说"这个名字不存在"，不是断言引用。"""


def _defined_tests() -> set[str]:
    """``tests/`` 下所有 ``def test_...`` 的名字（pytest 的收集面）。"""
    names: set[str] = set()
    for path in sorted(TESTS.rglob("*.py")):
        names.update(_DEF_RE.findall(path.read_text(encoding="utf-8")))
    return names


def _module_stems() -> set[str]:
    """``tests/`` 下的文件名主干：``test_web`` / ``test_agent_skills`` 这类不是断言。"""
    return {path.name.split(".", 1)[0] for path in TESTS.iterdir() if path.is_file()}


def _answer_bodies(text: str) -> list[tuple[int, str]]:
    """每段 Answer 的 ``(起始偏移, 正文)``；没有 Answer 的票返回空。"""
    bodies: list[tuple[int, str]] = []
    for match in _ANSWER_RE.finditer(text):
        start = match.end()
        following = _SECTION_RE.search(text, start)
        end = following.start() if following is not None else len(text)
        bodies.append((start, text[start:end]))
    return bodies


def _issue_files(scope: list[str]) -> list[Path]:
    """按命令行 scope 收集票文件；缺省 = 全部 ``.scratch/*/issues/*.md``。"""
    if not scope:
        return sorted((ROOT / ".scratch").glob("*/issues/*.md"))
    files: list[Path] = []
    for item in scope:
        path = Path(item)
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            raise SystemExit(f"FAIL: scope 路径不存在：{item}")
        if path.is_dir() and (path / "issues").is_dir():
            files.extend(sorted((path / "issues").glob("*.md")))
        elif path.is_dir():
            files.extend(sorted(path.glob("*.md")))
        else:
            files.append(path)
    return files


def _citations(text: str, *, modules: set[str]) -> tuple[dict[str, list[str]], int]:
    """抽出 Answer 里的断言引用；返回 ``(名字 → 位置列表, 跳过的 token 数)``。"""
    found: dict[str, list[str]] = {}
    skipped = 0
    for offset, body in _answer_bodies(text):
        for match in _CITE_RE.finditer(body):
            token = match.group(0)
            line_start = body.rfind("\n", 0, match.start()) + 1
            line_end = body.find("\n", match.end())
            line = body[line_start : line_end if line_end != -1 else len(body)]
            token_in_line = match.start() - line_start
            # "不存在" 只否定**它前面**那个名字（"票面点名的 `test_x` 在本分支不存在"）。
            denied = any(line.find(marker) > token_in_line for marker in _NON_CITATION)
            if (
                token in modules
                or token.endswith("_")
                or body[match.end() : match.end() + 3] == "..."
                or denied
            ):
                skipped += 1
                continue
            line_number = text.count("\n", 0, offset + match.start()) + 1
            found.setdefault(token, []).append(f"line {line_number}")
    return found, skipped


def main(argv: list[str]) -> int:
    defined = _defined_tests()
    modules = _module_stems()

    files = _issue_files(argv)
    scanned = 0
    citations: dict[str, list[str]] = {}
    skipped = 0
    for path in files:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        found, dropped = _citations(text, modules=modules)
        if not _answer_bodies(text):
            continue
        scanned += 1
        skipped += dropped
        for name, places in found.items():
            citations.setdefault(name, []).extend(f"{path.name}:{place}" for place in places)

    if not files:
        print("PASS: 没有 .scratch/*/issues/*.md，验收清扫 skip（干净 clone 全绿）")
        return 0
    if scanned == 0:
        print("PASS: 没有带『## Answer』的票，验收清扫 skip")
        return 0

    missing = {name: places for name, places in citations.items() if name not in defined}
    if missing:
        for name in sorted(missing):
            print(f"FAIL: 引用的测试不存在：{name}（{', '.join(missing[name])}）")
        print(f"FAIL: {len(missing)} 个引用名在 tests/ 的 def test_ 集合里找不到")
        return 1

    names = sorted(citations)
    print(
        f"PASS: 扫描 {scanned} 张带 Answer 的票，{len(names)} 个引用的测试名都存在于 tests/"
        f"（跳过 {skipped} 个非断言 token：模块名 / 省略号占位 / 明说不存在的名字）"
    )
    if not names:
        print("PASS: 没有可跑的引用，pytest 阶段 skip")
        return 0
    if not PYTEST.is_file():
        print(f"FAIL: {PYTEST} 不存在——先建项目虚拟环境（见 AGENTS.md 注意事项 2）")
        return 1

    # 一条 ``-k`` 表达式跑完全部引用：名字之间是 ``or``，pytest 自己负责收集与报告。
    expression = " or ".join(names)
    if len(names) <= 8:
        preview = expression
    else:
        preview = f"{' or '.join(names[:3])} or …（共 {len(names)} 个名字）"
    print(f"运行：{PYTEST.relative_to(ROOT)} -q -k {preview!r}")
    completed = subprocess.run(
        [str(PYTEST), "-q", "-p", "no:cacheprovider", "-k", expression],
        cwd=ROOT,
        check=False,
    )
    if completed.returncode != 0:
        print(f"FAIL: 引用的测试有红（pytest 退出码 {completed.returncode}）")
        return completed.returncode
    print("PASS: 引用的测试全部绿")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
