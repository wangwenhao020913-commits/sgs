"""内容包的定义、加载与加载期校验。

公开接缝只在此再导出，外部不得 import 本子包的深层路径。

票 03 §6 冻结的三个名字是 ``load_content_pack`` / ``ContentPack`` / ``DataLoadError``；
此外只多两个经确认的接缝：``validate_content_pack``（坏数据的注入点，主缝进不去）、
``LoadError``（``DataLoadError.errors`` 的元素类型，供断言与展示）。
"""

from .errors import DataLoadError, LoadError
from .loader import load_content_pack, validate_content_pack
from .models import ContentPack

__all__ = (
    "ContentPack",
    "DataLoadError",
    "LoadError",
    "load_content_pack",
    "validate_content_pack",
)
