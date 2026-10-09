"""内容包的数据模型（只装身份字段）。

结构层用 pydantic v2：``frozen=True`` + ``extra="forbid"``（字段名写错必须报错，
不能静默忽略）。``颜色`` 由花色派生、``属性`` 与行为字段（使用时机 / 目标规则 /
次数限制 / 效果列表）都不在这里 —— 前者军争才引入，后者归 effects 层（票 07）。

牌实例的 ``id`` **不在这里**：数据只给牌堆清单，实例化由 state/engine 在开局时做，
所以数据文件可以有同型同花色同点数的两张牌。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DEFAULT_SLOT_LIMIT",
    "Category",
    "CardType",
    "Color",
    "ContentPack",
    "Deck",
    "DeckCard",
    "DeckExpect",
    "Suit",
    "color_of_suit",
]

DEFAULT_SLOT_LIMIT = 2
"""同一 (花色,点数) 槽位的**默认**上限；``Deck.slot_limit`` 可逐档覆盖。

标准版含 EX 的 108 张档位在 ♠2 / ♣2 / ♥Q / ♦Q 四个槽位各有三张，故该档显式写 3；
基本 53 张档位沿用默认 2。它是"未声明时取几"，不是全局放宽（票 09 §3）。
"""


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Suit(str, Enum):
    """花色；颜色由它派生（heart/diamond → 红，spade/club → 黑）。"""

    HEART = "heart"
    DIAMOND = "diamond"
    SPADE = "spade"
    CLUB = "club"


class Color(str, Enum):
    """牌的**颜色**（票 12 §Q4 的取值表）：由花色派生，是【仁王盾】一类效果的判据。

    无色（``None``）在 M2 只会由**转化牌 / 虚拟牌**产生（票 30）；实体牌恒有颜色。
    真源是花色到颜色的映射（:func:`color_of_suit`），故没有第二份可漂移的数据。
    """

    RED = "red"
    BLACK = "black"


_RED_SUITS = frozenset({Suit.HEART, Suit.DIAMOND})
_BLACK_SUITS = frozenset({Suit.SPADE, Suit.CLUB})


def color_of_suit(suit: Suit | str) -> Color | None:
    """由花色派生颜色；未知花色返回 ``None``（fail-open 到"无色"，不做静默猜测）。

    接受 ``Suit`` 或它的字符串值：``CardRef.suit`` 是字符串（记录面形状），判定后果与
    装备效果都经本函数解析，故两处不会各自重抄 ``"heart"`` 字面量。
    """
    try:
        resolved = Suit(suit)
    except ValueError:
        return None
    if resolved in _RED_SUITS:
        return Color.RED
    if resolved in _BLACK_SUITS:
        return Color.BLACK
    return None  # pragma: no cover - Suit 是闭集，本条只为全函数性保留


class Category(str, Enum):
    """卡牌大类；M1 数据只用 ``basic``，其余枚举位随 M2 引入锦囊/装备。"""

    BASIC = "basic"
    TRICK = "trick"
    EQUIPMENT = "equipment"


class CardType(_FrozenModel):
    """牌名级定义：这张牌**是什么**。行为字段整体归 effects 层。

    ``attack_range`` / ``delayed`` 是**牌面事实**（票 09 §6）：前者是"距离 ≤ 攻击范围"
    L3 校验的取值来源、只属武器；后者是延时锦囊"置入判定区"的声明面。两者都带默认值，
    故基本 53 张档位一字不改即可通过。
    """

    id: str
    name: str
    category: Category
    subcategory: str | None = None
    attack_range: int | None = None
    delayed: bool = False


class DeckCard(_FrozenModel):
    """牌堆里的一张牌：只写身份引用与花色点数，不重复牌名级属性。"""

    type: str
    suit: Suit
    rank: int


class DeckExpect(_FrozenModel):
    """牌堆的声明式分布期望；加载期语义校验要求它与实况相符。

    ``total`` / ``by_type`` 必写；``by_suit`` / ``by_category`` 可选，缺失即不校验
    —— 基本 53 张档位的花色分布是 13/19/7/14，写不出"每花色 27"，故不写这两项。
    三项都沿用**双向穷尽**语义：实况出现但未声明的键同样报错。
    """

    total: int
    by_type: dict[str, int]
    by_suit: dict[str, int] | None = None
    by_category: dict[str, int] | None = None


class Deck(_FrozenModel):
    """一个牌堆档位；``slot_limit`` 是**声明式分档字段**（默认 2）。

    标准版含 EX 的 108 张在 ♠2 / ♣2 / ♥Q / ♦Q 四个槽位上各有三张，故该档显式写 3；
    这是档位属性而不是全局放宽，否则基本 53 张"每槽至多两张"的机械保护会失效。
    """

    slot_limit: int = DEFAULT_SLOT_LIMIT
    expect: DeckExpect | None = None
    cards: tuple[DeckCard, ...]


class ContentPack(_FrozenModel):
    """一个已通过加载期校验的内容包（含全部牌堆档位）。"""

    schema_version: int
    pack_id: str
    deck_profile: str
    card_types: tuple[CardType, ...]
    decks: dict[str, Deck]

    @property
    def deck(self) -> Deck:
        """本内容包默认选中的牌堆档位（校验保证 ``deck_profile`` 必存在）。"""
        return self.decks[self.deck_profile]
