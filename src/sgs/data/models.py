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

__all__ = ["Category", "CardType", "ContentPack", "Deck", "DeckCard", "DeckExpect", "Suit"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Suit(str, Enum):
    """花色；颜色由它派生（heart/diamond → 红，spade/club → 黑）。"""

    HEART = "heart"
    DIAMOND = "diamond"
    SPADE = "spade"
    CLUB = "club"


class Category(str, Enum):
    """卡牌大类；M1 数据只用 ``basic``，其余枚举位随 M2 引入锦囊/装备。"""

    BASIC = "basic"
    TRICK = "trick"
    EQUIPMENT = "equipment"


class CardType(_FrozenModel):
    """牌名级定义：这张牌**是什么**。行为字段整体归 effects 层。"""

    id: str
    name: str
    category: Category
    subcategory: str | None = None


class DeckCard(_FrozenModel):
    """牌堆里的一张牌：只写身份引用与花色点数，不重复牌名级属性。"""

    type: str
    suit: Suit
    rank: int


class DeckExpect(_FrozenModel):
    """牌堆的声明式分布期望；加载期语义校验要求它与实况相符。"""

    total: int
    by_type: dict[str, int]


class Deck(_FrozenModel):
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
