"""身份与模式：由座位数唯一决定的**规则常数**（票 13 §1 / §2）。

与内容包 / 牌堆档位 / 规则配置三分：配比不随 pack 变化、与用哪些牌无关、
也不是本局做出的"选择"——它是座位数的函数。因此与 ``INITIAL_HP`` / ``DRAW_COUNT``
同姿态：模块级不可变常量 + 一个 fail-closed 访问器。

``Identity`` / ``Mode`` 放这里而不是 ``state/models.py``：``state`` 本就 import ``sgs.data``，
反过来会造出 ``data ↔ state`` 的模块级环。
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum

__all__ = [
    "IDENTITY_LABELS",
    "IDENTITY_ROSTERS",
    "Identity",
    "Mode",
    "roster_for",
]


class Mode(str, Enum):
    """一局的模式，由 :attr:`~sgs.data.RuleConfig.player_count` 派生（票 13 §1）。

    **不是配置字段**：值域被座位数切分干净，任何"模式开关"都会给 2 人局 header 多一键。
    取值名一律用术语表的**规范名**（无身份局 / 身份局），不用"决斗"这类被术语表
    明确回避的口语（`GLOSSARY.md` §身份与模式），也避开牌名 id ``duel``（【决斗】）。
    """

    NO_IDENTITY = "no_identity"
    """无身份局（`GLOSSARY.md`：不发放身份、胜负只看存活数的两人模式）。"""

    IDENTITY = "identity"
    """身份局（座位数 4~8）。"""


class Identity(str, Enum):
    """身份（票 13 §2）：英文值，中文只出现在 :data:`IDENTITY_LABELS` 里。

    判定一律比枚举成员；把中文串塞进状态会让"同一篇文档只用一个名字"失效。
    """

    LORD = "lord"
    LOYALIST = "loyalist"
    REBEL = "rebel"
    RENEGADE = "renegade"


IDENTITY_LABELS: Mapping[Identity, str] = {
    Identity.LORD: "主公",
    Identity.LOYALIST: "忠臣",
    Identity.REBEL: "反贼",
    Identity.RENEGADE: "内奸",
}
"""身份 → 中文标签（渲染用；判定读枚举成员，不读标签）。"""


IDENTITY_ROSTERS: Mapping[int, tuple[Identity, ...]] = {
    4: (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.RENEGADE),
    5: (Identity.LORD, Identity.LOYALIST, Identity.REBEL, Identity.REBEL, Identity.RENEGADE),
    6: (
        Identity.LORD,
        Identity.LOYALIST,
        Identity.REBEL,
        Identity.REBEL,
        Identity.REBEL,
        Identity.RENEGADE,
    ),
    7: (
        Identity.LORD,
        Identity.LOYALIST,
        Identity.LOYALIST,
        Identity.REBEL,
        Identity.REBEL,
        Identity.REBEL,
        Identity.RENEGADE,
    ),
    8: (
        Identity.LORD,
        Identity.LOYALIST,
        Identity.LOYALIST,
        Identity.REBEL,
        Identity.REBEL,
        Identity.REBEL,
        Identity.REBEL,
        Identity.RENEGADE,
    ),
}
"""4~8 人的身份配比多重集（票 04 主表），主-忠-反-内。

备选配置不采用（票 04）：每个配置只有一个主公、至多两个忠臣、至多一个内奸。
"""


def roster_for(player_count: int) -> tuple[Identity, ...]:
    """某人数下的身份配比多重集；只认 4~8，其余 fail-closed（票 13 §2）。"""
    try:
        return IDENTITY_ROSTERS[player_count]
    except KeyError:
        raise ValueError(f"没有 {player_count} 人局的身份配比（只有 4~8）") from None
