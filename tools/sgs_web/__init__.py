"""SGS 的 M4 接入层与极简 Web 界面（依据 ADR-012）。

这个包**不在** ``src/sgs/`` 里，是刻意的：

- ``src/sgs/**`` 被 :mod:`tests.test_source_discipline` 全树禁止 import
  ``time`` / ``datetime`` / ``uuid`` / ``threading`` / ``asyncio`` / ``multiprocessing`` /
  ``concurrent`` / ``_thread``（"确定性 + 单线程"，票 08 §7）——而接入层正需要时钟
  （超时与时间戳）；
- ``src/sgs/`` 的子包集合被 :mod:`tests.test_scaffold` 钉死为八个，接入层不是引擎的一部分。

``pyproject`` 的 ``packages`` 只装 ``src/``，所以 ``tools/`` **不进发行包**；
它的入口是 ``python tools/sgs_web/serve.py``（见 :mod:`sgs_web.serve`），
而导入 ``sgs`` 所需的 ``src/`` 路径由本模块的 :func:`ensure_engine_importable` 负责。

分层：``serve``（HTTP 门面）→ ``session``（一局与会话驱动）→ ``sgs.engine``；
``timeout`` / ``projection`` / ``replay`` 是无状态辅助，只有纯函数与文件读写。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

__all__ = ["PACKAGE_ROOT", "REPO_ROOT", "ensure_engine_importable"]

PACKAGE_ROOT: Final[Path] = Path(__file__).resolve().parent
"""本包目录（``tools/sgs_web/``）；前端静态资源在它的 ``static/`` 下。"""

REPO_ROOT: Final[Path] = PACKAGE_ROOT.parent.parent
"""仓库根目录（``tools/sgs_web/`` 往上两层）；``src/`` 与 ``runs/`` 都相对它定位。"""


def ensure_engine_importable() -> Path:
    """把仓库的 ``src/`` 塞进 ``sys.path``，返回被塞入的目录。

    幂等：已经在 ``sys.path`` 里就原样返回。开发态（``python tools/sgs_web/serve.py``）
    靠它拿到 ``sgs``；已 ``pip install -e .`` 的环境里这一步是零动作。
    """
    source_root = REPO_ROOT / "src"
    entry = str(source_root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    return source_root
