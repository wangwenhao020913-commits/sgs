"""变化事件（delta）的载荷类型：一次状态变化的纯事实（票 06 §2 / §3）。

四种 ``delta_type``（``card_move`` / ``hp_change`` / ``deck_shuffle`` / ``identity_deal``）：
它们是记录流 schema 的一部分，后补会动事件版本，所以即使 M1 的骨架期只用得到
``card_move`` 也先建位。记录层（``engine``）在这些模型上**继承**出带
``seq`` / ``frame_id`` / ``depth`` 的记录子类，从而 JSON 是平铺的。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from sgs.data import Identity

from .models import CardRef, MoveReason, ZoneRef, _FrozenModel

__all__ = [
    "CardMoveDelta",
    "DeckShuffleDelta",
    "Delta",
    "HpChangeDelta",
    "IdentityDealDelta",
]


class CardMoveDelta(_FrozenModel):
    """一批实体牌从**一个**来源区域移动到目标区域（一次操作一条）。

    ``from_zone`` / ``to_zone`` 是**区域坐标**（区种类 + 座位）：一次弃置两张不同来源的牌
    是两条 delta，故每条只有一个来源坐标。``owner`` 是**归属**——"本次移动算谁的"
    （判定牌 = 被判定者、弃置 / 使用 = 弃置者 / 使用者、摸牌 = 摸牌者、装备置入 = 持有者）；
    只有"无角色主体"的移动（如终局收尾）为 ``None``。归属 ≠ 区域坐标：判定区里的牌
    坐标带座位，但牌本身仍不为任何角色所拥有（规则 11.6）。
    """

    delta_type: Literal["card_move"] = "card_move"
    cards: tuple[CardRef, ...]
    from_zone: ZoneRef
    to_zone: ZoneRef
    reason: MoveReason
    owner: int | None = None
    """本次移动的归属角色；"无角色主体"时为 ``None``。"""


class HpChangeDelta(_FrozenModel):
    """体力被写成某个值（``apply_hp`` 是唯一改体力的口）。"""

    delta_type: Literal["hp_change"] = "hp_change"
    seat: int
    before: int
    after: int
    amount: int


class DeckShuffleDelta(_FrozenModel):
    """一次洗牌置换；``cards`` 是**洗后**自顶向下的牌堆顺序，不记洗前顺序。

    洗牌**无归属**（无角色主体）；区域引用与 ``card_move`` 同形，一并升为区域坐标。
    """

    delta_type: Literal["deck_shuffle"] = "deck_shuffle"
    cards: tuple[CardRef, ...]
    from_zone: ZoneRef | None
    """初次洗牌为 ``None``；重洗为 ``{"zone": "discard", "seat": null}``。"""

    to_zone: ZoneRef


class IdentityDealDelta(_FrozenModel):
    """一个座位被发到身份（票 13 §3）：逐座一条、落在发牌**之前**、不新增时机节点。

    身份不是牌：不进 ``cards``、不占牌守恒、与 :class:`MoveReason` 无关。
    """

    delta_type: Literal["identity_deal"] = "identity_deal"
    seat: int
    identity: Identity


Delta = Annotated[
    CardMoveDelta | HpChangeDelta | DeckShuffleDelta | IdentityDealDelta,
    Field(discriminator="delta_type"),
]
