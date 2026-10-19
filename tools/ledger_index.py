#!/usr/bin/env python3
"""`06`（§5「程序化约定与偏离一手」）的账本行索引（W5-03 retro）。

账本是一张 50+ 行的长表，票面说「U-27-1 行」时靠手翻很容易翻空。本工具只读，
打印「行号 / 票号 / 裁定标题」，可按关键词或票号过滤：

    .venv/bin/python tools/ledger_index.py --grep U-27-1
    .venv/bin/python tools/ledger_index.py --ticket W5-02b
    .venv/bin/python tools/ledger_index.py --grep 借刀

只列**表行**；§5 里的非裁定注记（如 B1 的实现缺陷）不在此表。
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

__all__ = ["parse_rows", "format_rows", "main"]

ROOT = Path(__file__).resolve().parent.parent
DOC06 = ROOT / "开发文档" / "06-里程碑与风险.md"
SECTION5_HEADING = "## 5. 程序化约定与偏离一手"
_TITLE_LIMIT = 120


def table_cells(line: str) -> list[str]:
    """按 Markdown 表格拆单元格，忽略反引号里的 ``|``（账本里有 ``|id|``）。"""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = False
    for char in text:
        if char == "`":
            in_code = not in_code
        if char == "|" and not in_code:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    cells.append("".join(current).strip())
    return cells


def parse_rows(text: str | None = None) -> list[tuple[int, list[str]]]:
    """返回 §5 表行 ``(真实行号, 5 列)``；表头 / 分隔行 / 非 5 列行跳过。"""
    lines = (text if text is not None else DOC06.read_text(encoding="utf-8")).splitlines()
    try:
        start = next(
            i for i, line in enumerate(lines) if line.startswith(SECTION5_HEADING)
        )
    except StopIteration:
        return []
    rows: list[tuple[int, list[str]]] = []
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("---"):
            break
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        if line.startswith("| 裁定"):
            continue
        cells = table_cells(line)
        if len(cells) == 5:
            rows.append((index + 1, cells))
    return rows


def _title(cell: str) -> str:
    plain = re.sub(r"[`*]", "", cell).strip()
    return plain if len(plain) <= _TITLE_LIMIT else plain[: _TITLE_LIMIT - 1] + "…"


def format_rows(
    rows: list[tuple[int, list[str]]],
    *,
    pattern: str | None = None,
    ticket: str | None = None,
) -> str:
    selected: list[tuple[int, list[str]]] = []
    for line_no, cells in rows:
        if ticket is not None and ticket.lower() not in cells[3].lower():
            continue
        if pattern is not None and not re.search(pattern, " ".join(cells), re.IGNORECASE):
            continue
        selected.append((line_no, cells))
    if not selected:
        return "no match"
    lines = ["line\tticket\ttitle"]
    for line_no, cells in selected:
        lines.append(f"{line_no}\t{cells[3]}\t{_title(cells[0])}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="06 §5 账本行索引：按关键词 / 票号定位裁定行（只读）。"
    )
    parser.add_argument("--grep", default=None, help="正则（忽略大小写），匹配整行")
    parser.add_argument("--ticket", default=None, help="票号子串，匹配票号列")
    args = parser.parse_args(argv)
    rows = parse_rows()
    try:
        print(format_rows(rows, pattern=args.grep, ticket=args.ticket))
    except re.error as exc:
        parser.error(f"无效正则：{exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
