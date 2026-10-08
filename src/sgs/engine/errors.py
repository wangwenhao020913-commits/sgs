"""非法应答与记录流版本：引擎在边界 fail-closed（票 07 §5 / 票 15 §2）。

``IllegalActionError`` 抛点在"引擎接受行动"处，抛前**零状态改动**（校验只是成员判定），
但被拒的应答本身仍写一条 ``accepted = false`` 的记录——拒绝是可观测的。

``JournalVersionError`` 抛点在**唯一反序列化器** :func:`sgs.engine.read_journal` 里，
版本先行：文件版本不在支持集合内即拒，且**报双方版本**（票 15 §2）。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

__all__ = ["IllegalActionError", "JournalVersionError"]


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


class JournalVersionError(Exception):
    """记录流声明的 ``event_schema_version`` 不在本引擎的支持集合内（fail-closed）。"""

    def __init__(
        self,
        *,
        path: Path,
        file_version: int,
        supported_versions: Iterable[int],
    ) -> None:
        self.path = Path(path)
        self.file_version = file_version
        self.supported_versions = tuple(sorted(supported_versions))
        super().__init__(
            f"记录流版本不符：文件 {self.path} 声明 event_schema_version={file_version}，"
            f"本引擎支持 {list(self.supported_versions)}"
        )
