"""批量层的失败类型（票 11 §1.4 的退出码口径）。

分层只按**调用方该做什么**切，不按错误来源切：

- :class:`BatchSpecError` = 参数 / 规格错，**开跑前**就能判出（``run_games`` 抛出）；
- :class:`UnsupportedMode` 是它的一种：``(players, mode)`` 组合当前引擎不支持，
  消息必须**点名**这两个值（spec §1.3 的 fail-closed）；
- :class:`BatchWorkerError` = 进程池在运行期崩了；不返回半批，消息点名未完成的种子。
"""

from __future__ import annotations

__all__ = ["BatchError", "BatchSpecError", "BatchWorkerError", "UnsupportedMode"]


class BatchError(Exception):
    """批量层所有失败的基类。"""


class BatchSpecError(BatchError, ValueError):
    """参数 / 规格错误：CLI 退出码 2。"""


class UnsupportedMode(BatchSpecError):
    """``(players, mode)`` 组合没有引擎实现：inherently a spec error（退出码 2）。"""


class BatchWorkerError(BatchError):
    """worker 进程崩溃 / 池不可用：不返回半批（CLI 退出码 1）。"""
