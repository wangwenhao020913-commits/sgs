"""批量对局库（票 11；spec §1.2–§1.6）。

**不进 ``src/``**：批量是评估基础设施，不是发行包的一部分；它需要进程池与时钟，而
``src/sgs`` 的源码纪律（``tests/test_source_discipline.py``）禁这两样。用法::

    import sys
    sys.path.insert(0, "tools")
    from sgs_batch import run_games

    batch = run_games(seeds=range(136, 144), mode="identity", players=5)

公开面 = ``GameSpec`` / ``GameResult`` / ``BatchResult`` / ``run_games`` / ``run_one`` /
``summarize``，外加三类失败（:class:`UnsupportedMode` 是规格错的一种）。
"""

from .errors import BatchError, BatchSpecError, BatchWorkerError, UnsupportedMode
from .models import (
    ACTIONS_SCHEMA,
    BATCH_SCHEMA,
    BatchResult,
    GameResult,
    GameSpec,
    SeatResult,
    summarize,
)
from .runner import ParticipantsFactory, run_games, run_one

__all__ = (
    "ACTIONS_SCHEMA",
    "BATCH_SCHEMA",
    "BatchError",
    "BatchResult",
    "BatchSpecError",
    "BatchWorkerError",
    "GameResult",
    "GameSpec",
    "ParticipantsFactory",
    "SeatResult",
    "UnsupportedMode",
    "run_games",
    "run_one",
    "summarize",
)
