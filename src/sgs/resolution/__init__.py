"""结算栈：显式栈帧 + 步骤表。

公开接口只在此再导出，外部不得 import 本子包的深层路径。
"""

from .stack import (
    Control,
    Frame,
    Goto,
    ResolutionStack,
    Step,
    StepHost,
    StepTable,
)
from .tags import (
    EMPTY_TAGS,
    USE_TAGS_KEY,
    WHOLE_FIELD_TAGS_KEY,
    Tag,
    UseTags,
    add_tag,
    has_tag,
)

__all__ = (
    "Control",
    "EMPTY_TAGS",
    "Frame",
    "Goto",
    "ResolutionStack",
    "Step",
    "StepHost",
    "StepTable",
    "USE_TAGS_KEY",
    "WHOLE_FIELD_TAGS_KEY",
    "Tag",
    "UseTags",
    "add_tag",
    "has_tag",
)
