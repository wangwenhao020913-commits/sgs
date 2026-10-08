"""加载期错误：可定位的聚合报告。

一条 :class:`LoadError` 对应坏数据里的一处问题，带 ``file`` / ``loc`` /
``expected`` / ``got`` / ``rule_id`` 五要素。:class:`DataLoadError` 聚合全部，
绝不只报第一条 —— 这样修数据的人一次就能看到全部问题（票 03 §6）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

__all__ = ["DataLoadError", "LoadError"]


@dataclass(frozen=True, slots=True)
class LoadError:
    """一条可定位的加载错误。"""

    file: str
    loc: str
    expected: str
    got: str
    rule_id: str


class DataLoadError(Exception):
    """内容包未通过加载期校验；携带**全部**错误，不返回部分数据。"""

    def __init__(self, errors: Iterable[LoadError]) -> None:
        self.errors: tuple[LoadError, ...] = tuple(errors)
        if not self.errors:
            raise ValueError("DataLoadError 至少需要一条错误")
        super().__init__(self.report())

    def report(self) -> str:
        """中文聚合报告，供 CLI 打到 stderr。"""
        files = "、".join(dict.fromkeys(error.file for error in self.errors))
        lines = [f"内容包加载失败：{files}", f"共 {len(self.errors)} 处错误："]
        for index, error in enumerate(self.errors, start=1):
            lines.append(f"  {index}. [{error.rule_id}] 位置 {error.loc}")
            lines.append(f"     期望：{error.expected}")
            lines.append(f"     实得：{error.got}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.report()
