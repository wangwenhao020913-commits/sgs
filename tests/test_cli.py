"""命令行门面：两个入口同源，``--seed`` 被解析。"""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import entry_points
from pathlib import Path

import pytest

import sgs.__main__ as module_entry
from sgs.cli import main

CONSOLE_SCRIPT = Path(sys.executable).parent / "sgs"


def test_console_script_points_at_the_same_entry_function():
    scripts = {point.name: point.value for point in entry_points(group="console_scripts")}

    assert scripts["sgs"] == "sgs.cli:main"


def test_module_entry_is_the_same_function():
    assert module_entry.main is main


def test_seed_is_parsed():
    assert main(["--seed", "7"]) == 0


def test_seed_may_be_omitted():
    assert main([]) == 0


def test_unknown_option_is_rejected():
    with pytest.raises(SystemExit):
        main(["--nope"])


def _run(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True)


def test_module_entry_runs():
    result = _run(sys.executable, "-m", "sgs", "--seed", "7")

    assert result.returncode == 0, result.stderr


def test_console_script_runs():
    result = _run(str(CONSOLE_SCRIPT), "--seed", "7")

    assert result.returncode == 0, result.stderr


BAD_RAW_PACK = {"schema_version": 99}
"""坏数据基底：未知数据模型版本；由真实的加载器生成报告，避免手抄错误文案。"""

BAD_PACK_PROBE = f"""
import sgs.cli as cli
from sgs.data import validate_content_pack

def load_bad_pack(pack_id="standard"):
    validate_content_pack({BAD_RAW_PACK!r}, file="files/standard.json")

cli.load_content_pack = load_bad_pack
raise SystemExit(cli.main([]))
"""


def test_bad_content_pack_reports_to_stderr_and_exits_1(monkeypatch, capsys):
    """坏数据：命令行打印中文报告到 stderr、退出码 1（票 03 §6）。"""
    import sgs.cli as cli
    from sgs.data import validate_content_pack

    def load_bad_pack(pack_id="standard"):
        validate_content_pack(BAD_RAW_PACK, file="files/standard.json")

    monkeypatch.setattr(cli, "load_content_pack", load_bad_pack)

    assert cli.main([]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "内容包加载失败" in captured.err
    assert "version.unknown" in captured.err
    assert "99" in captured.err


def test_bad_content_pack_exit_code_in_subprocess():
    """跨进程确认退出码 1 与 stderr 报告（入口同源由 smoke 断言）。"""
    result = _run(sys.executable, "-c", BAD_PACK_PROBE)

    assert result.returncode == 1
    assert "内容包加载失败" in result.stderr
    assert "version.unknown" in result.stderr
    assert result.stdout == ""
