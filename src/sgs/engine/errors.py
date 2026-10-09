"""非法应答与记录流版本：引擎在边界 fail-closed（票 07 §5 / 票 15 §2）。

``IllegalActionError`` 抛点在"引擎接受行动"处，抛前**零状态改动**（校验只是成员判定），
但被拒的应答本身仍写一条 ``accepted = false`` 的记录——拒绝是可观测的。
三个 ``reason_code`` 与票 07 §5 的三类非法输入同名：``MalformedAction``（结构损坏，
payload 不在行动 schema 内）/ ``StaleAction``（``answers_seq`` 失配）/
``IllegalAction``（不在候选集合内）。M1 不重试、不超时（票 24）。

``JournalVersionError`` 抛点在**唯一反序列化器** :func:`sgs.engine.read_journal` 里，
版本先行：文件版本不在支持集合内即拒，且**报双方版本**（票 15 §2）。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

__all__ = ["IllegalActionError", "JournalVersionError"]


class IllegalActionError(Exception):
    """一次应答不在当前询问的合法行动集合内（或结构损坏 / 应答序号陈旧）。"""

    def __init__(
        self,
        *,
        reason_code: str,
        message: str,
        expected: str,
        got: str,
        answers_seq: int | None,
    ) -> None:
        self.reason_code = reason_code
        self.message = message
        self.expected = expected
        self.got = got
        self.answers_seq = answers_seq
        super().__init__(f"[{reason_code}] {message}（期望 {expected}，实得 {got}）")


class JournalVersionError(Exception):
    """记录流声明的 ``event_schema_version`` 不在本引擎的支持集合内（fail-closed）。

    报错补**方向词**（票 16 §4）：文件版本低于最低支持 ⇒ "过旧"、高于最高支持 ⇒ "过新"，
    好让"旧引擎读新文件"与"新引擎读旧文件"一眼分清。
    """

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
        lowest = self.supported_versions[0]
        highest = self.supported_versions[-1]
        if file_version < lowest:
            direction = f"文件过旧：{file_version} < 最低支持 {lowest}"
        elif file_version > highest:
            direction = f"文件过新：{file_version} > 最高支持 {highest}"
        else:  # pragma: no cover - 版本在支持集合内就不会抛本异常
            direction = f"文件版本 {file_version} 不在支持集合内"
        super().__init__(
            f"记录流版本不符（{direction}）：文件 {self.path} 声明 "
            f"event_schema_version={file_version}，本引擎支持 {list(self.supported_versions)}"
        )
