"""变化事件（delta）的载荷类型：一次状态变化的纯事实（票 06 §2 / §3）。

三种 ``delta_type`` 一次建全（``card_move`` / ``hp_change`` / ``deck_shuffle``）：
它们是记录流 schema 的一部分，后补会动事件版本，所以即使 M1 的骨架期只用得到
``card_move`` 也先建位。记录层（``engine``）在这三个模型上**继承**出带
``seq`` / ``frame_id`` / ``depth`` 的记录子类，从而 JSON 是平铺的。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import CardRef, MoveReason, Zone

__all__ = [
    "CardMoveDelta",
    "DeckShuffleDelta",
    "Delta",
    "HpChangeDelta",
]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CardMoveDelta(_FrozenModel):
    """一批实体牌从**一个**来源区域移动到目标区域（一次操作一条）。"""

    delta_type: Literal["card_move"] = "card_move"
    cards: tuple[CardRef, ...]
    from_zone: Zone
    to_zone: Zone
    reason: MoveReason
    owner: int | None = None
    """移出手牌时的持有者座位；其余来源为 ``None``。"""


class HpChangeDelta(_FrozenModel):
    """体力被写成某个值（``apply_hp`` 是唯一改体力的口）。"""

    delta_type: Literal["hp_change"] = "hp_change"
    seat: int
    before: int
    after: int
    amount: int


class DeckShuffleDelta(_FrozenModel):
    """一次洗牌置换；``cards`` 是**洗后**自顶向下的牌堆顺序，不记洗前顺序。"""

    delta_type: Literal["deck_shuffle"] = "deck_shuffle"
    cards: tuple[CardRef, ...]
    from_zone: Zone | None
    """初次洗牌为 ``None``；重洗为 ``DISCARD``。"""

    to_zone: Zone


Delta = Annotated[
    CardMoveDelta | HpChangeDelta | DeckShuffleDelta,
    Field(discriminator="delta_type"),
]
