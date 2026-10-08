"""权威时机表：83 个节点的枚举及其 M1 分区。

公开接口只在此再导出，外部不得 import 本子包的深层路径。
"""

from .enum import FORBIDDEN_TIMING_NAMES, M1_ENUM_ONLY, M1_IMPLEMENTED, Timing

__all__ = (
    "FORBIDDEN_TIMING_NAMES",
    "M1_ENUM_ONLY",
    "M1_IMPLEMENTED",
    "Timing",
)
