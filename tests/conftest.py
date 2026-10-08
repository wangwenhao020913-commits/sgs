"""pytest 配置：基准录制的**唯一入口** ``--update-golden``（票 10 §6）。

不给产品 CLI 加参数（票 02）：录制是测试侧的能力，场景脚本玩家只在录制时使用。
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="重录 Golden Replay 基准（首录与重录的唯一入口）。",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))
