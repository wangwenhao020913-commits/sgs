"""非法应答：引擎在边界 fail-closed（票 07 §5）。

抛点在"引擎接受行动"处，抛前**零状态改动**（校验只是成员判定），
但被拒的应答本身仍写一条 ``accepted = false`` 的记录——拒绝是可观测的。
"""

from __future__ import annotations

__all__ = ["IllegalActionError"]


class IllegalActionError(Exception):
    """一次应答不在当前询问的合法行动集合内（或应答序号陈旧）。"""

    def __init__(
        self,
        *,
        reason_code: str,
        message: str,
        expected: str,
        got: str,
        answers_seq: int,
    ) -> None:
        self.reason_code = reason_code
        self.message = message
        self.expected = expected
        self.got = got
        self.answers_seq = answers_seq
        super().__init__(f"[{reason_code}] {message}（期望 {expected}，实得 {got}）")
