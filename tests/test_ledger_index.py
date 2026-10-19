"""账本行索引工具的行为测试（W5-03 retro）。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "ledger_index", ROOT / "tools" / "ledger_index.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_rows_finds_the_whole_ledger() -> None:
    tool = _load_tool()
    rows = tool.parse_rows()
    assert len(rows) >= 60
    assert all(cells[3] for _line, cells in rows), "每行都应有票号列"


def test_table_cells_keeps_a_backticked_pipe_in_one_cell() -> None:
    """`|id| < 2**53` 在反引号里，不能被当成列分隔符。"""
    tool = _load_tool()
    cells = tool.table_cells(
        "| **虚拟牌 id**：`|id| < 2**53` | 程序化约定 | 说明 | W4 | 落点 |"
    )
    assert len(cells) == 5
    assert cells[1] == "程序化约定"


def test_ticket_filter_finds_the_u27_row() -> None:
    tool = _load_tool()
    output = tool.format_rows(tool.parse_rows(), ticket="U-27-1")
    assert "U-27-1" in output
    assert "no match" not in output


def test_grep_filter_finds_borrowed_sword_rows() -> None:
    tool = _load_tool()
    output = tool.format_rows(tool.parse_rows(), pattern="借刀")
    assert "借刀" in output
    assert "no match" not in output


def test_cli_invalid_regex_is_a_usage_error() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "ledger_index.py"), "--grep", "["],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "无效正则" in completed.stderr


def test_cli_no_match_is_zero_exit() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "ledger_index.py"),
            "--grep",
            "绝不可能匹配的裁定",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "no match"
