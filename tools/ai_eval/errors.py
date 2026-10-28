"""``ai_eval`` 的三类失败；CLI 的退出码按类型分（与 ``sgs_batch`` 同口径）。

- :class:`AiEvalSpecError`：参数 / 规格错（``deals`` 非正、workers 越界、未知档位）⇒ 2；
- :class:`AiVersionError`：版本加载失败（缺 tag、``git show`` 读不到、字节不一致）
  ⇒ 2。**绝不静默回退 HEAD**（spec §2.6）；
- 批量层的 :class:`~sgs_batch.BatchWorkerError` 与"任一局 error 非空" ⇒ 1（对局 / 基础设施
  失败，不是参数错）。
"""

from __future__ import annotations

__all__ = ["AiEvalError", "AiEvalSpecError", "AiVersionError"]


class AiEvalError(Exception):
    """``ai_eval`` 所有可预期失败的共同基类。"""


class AiEvalSpecError(AiEvalError):
    """参数 / 规格错误：重跑不会变好，要改命令行（CLI 退出码 2）。"""


class AiVersionError(AiEvalError):
    """版本加载失败：缺 tag、git 读不到、副本与 ``git show`` 字节不一致（退出码 2）。"""
