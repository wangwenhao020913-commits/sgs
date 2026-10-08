"""声明式输入的加载与校验：内容包与规则配置。

公开接缝只在此再导出，外部不得 import 本子包的深层路径。

票 03 §6 冻结的三个名字是 ``load_content_pack`` / ``ContentPack`` / ``DataLoadError``；
此外只多两个经确认的接缝：``validate_content_pack``（坏数据的注入点，主缝进不去）、
``LoadError``（``DataLoadError.errors`` 的元素类型，供断言与展示）。
票 09 把 ``RuleConfig`` 也判在本子包（"声明式输入的加载与校验"），随票 18 的信封落地，
故公开面再增 :class:`RuleConfig` 与其进料边界 :func:`validate_rule_config`。
"""

from .errors import DataLoadError, LoadError
from .loader import load_content_pack, validate_content_pack
from .models import ContentPack
from .rule_config import RuleConfig, validate_rule_config

__all__ = (
    "ContentPack",
    "DataLoadError",
    "LoadError",
    "RuleConfig",
    "load_content_pack",
    "validate_content_pack",
    "validate_rule_config",
)
