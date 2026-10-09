"""声明式输入的加载与校验：内容包与规则配置。

公开接缝只在此再导出，外部不得 import 本子包的深层路径。

票 03 §6 冻结的三个名字是 ``load_content_pack`` / ``ContentPack`` / ``DataLoadError``；
此外只多两个经确认的接缝：``validate_content_pack``（坏数据的注入点，主缝进不去）、
``LoadError``（``DataLoadError.errors`` 的元素类型，供断言与展示）。
票 09 把 ``RuleConfig`` 也判在本子包（"声明式输入的加载与校验"），随票 18 的信封落地，
故公开面再增 :class:`RuleConfig` 与其进料边界 :func:`validate_rule_config`。
票 24 再增身份与模式的规则常数（:class:`Identity` / :class:`Mode` / :func:`roster_for`）：
它们与规则配置同层，都是"进料侧"的不可变事实。
票 26 把 :class:`Suit`（枚举本就存在）纳入公开面：判定后果按花色判（【乐不思蜀】非红桃 /
【闪电】黑桃），行为层需要花色这一**牌面事实**的枚举真源来解析 ``CardRef.suit`` 的字符串，
不能各自重抄 ``"heart"`` 字面量。票 29 以同姿态把 :class:`Color` 与 :func:`color_of_suit`
纳入公开面：【仁王盾】的"黑色【杀】对你无效"要按**颜色**判，而颜色由花色派生。
"""

from .errors import DataLoadError, LoadError
from .identities import IDENTITY_LABELS, IDENTITY_ROSTERS, Identity, Mode, roster_for
from .loader import load_content_pack, validate_content_pack
from .models import Color, ContentPack, Gender, Suit, color_of_suit
from .rule_config import RuleConfig, validate_rule_config

__all__ = (
    "Color",
    "ContentPack",
    "DataLoadError",
    "Gender",
    "IDENTITY_LABELS",
    "IDENTITY_ROSTERS",
    "Identity",
    "LoadError",
    "Mode",
    "RuleConfig",
    "Suit",
    "color_of_suit",
    "load_content_pack",
    "roster_for",
    "validate_content_pack",
    "validate_rule_config",
)
