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
