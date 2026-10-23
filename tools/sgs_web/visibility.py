"""记录流的**可见性判据**（ADR-012 / 地图 D8）。

A3 的口径按**记录类型**各有一条，全部只读记录字典：

- **一张牌移动的牌面**（``card_move``）：**区域**决定牌面是否公开——公开区（弃牌堆 /
  处理区 / 装备区 / 判定区）里出现过的牌是公开事实；只进出隐藏区（牌堆 / 手牌）的牌只给
  张数；查看者**自己的手牌**与**自己经手的移动**仍可见——那不是泄露。
- **牌堆顺序**（``deck_shuffle``）：A3 只给**数量**；整副洗后牌序只下发全知通道。
- **身份发放**（``identity_deal``）：A3 的「本人 / 主公 / 已亮明 / 阵亡」——发放是**开局
  暗发**，故只有本人与主公可见；"已亮明 / 阵亡"由**公开的** ``identity_reveal`` 记录承载
  （同一份记录流里的下一条），本判据不需要回读引擎状态。
- ``viewer is None`` = 没有查看者（全知）：不裁剪（内部调用 / 测试需要原始记录）。

两个下发口**必须同源**，否则同一件事在事件流与日志里口径不一：

- :func:`sgs_web.projection.visible_record`——人类座位的记录流 / 日志载荷（M5-09c 起
  按这条判据把隐藏区的 ``cards`` 抹成空列表并下发 ``card_count``）；
- ``sgs_web.narrative._Narrator._gain``——简易事件流写"获得 N 张牌"还是写牌名。

判据住在这里而不是两边各写一份：投影与叙事都从这里 import（本模块只读记录字典，
不 import 任何 ``sgs_web`` 模块，故不制造 ``projection`` ↔ ``narrative`` 的循环依赖）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

__all__ = [
    "PUBLIC_ZONES",
    "cards_visible",
    "deck_cards_visible",
    "identity_visible",
]

PUBLIC_ZONES: Final[frozenset[str]] = frozenset({"discard", "processing", "equip", "judge"})
"""牌面一旦进入这些区域就是公开事实；手牌 / 牌堆仍是隐藏区。"""


def cards_visible(record: Mapping[str, Any], viewer: int | None) -> bool:
    """这条牌移动的**牌面身份**对 ``viewer`` 是否公开。

    ``record`` 是 ``card_move`` 的 JSON 形态（``from_zone`` / ``to_zone`` 各带 ``zone``
    与 ``seat``）；``viewer`` = 查看者座位，``None`` = 没有查看者 / 全知。
    """
    if viewer is None:
        return True
    to_zone = record.get("to_zone") or {}
    from_zone = record.get("from_zone") or {}
    to = to_zone.get("zone")
    source = from_zone.get("zone")
    if to in PUBLIC_ZONES or source in PUBLIC_ZONES:
        return True
    if to == "hand" and to_zone.get("seat") == viewer:
        return True
    if source == "hand" and from_zone.get("seat") == viewer:
        return True
    return False


def deck_cards_visible(viewer: int | None) -> bool:
    """``deck_shuffle`` 的**洗后牌序**是否可下发：A3 只给数量，故只有全知通道可见。"""
    return viewer is None


def identity_visible(record: Mapping[str, Any], viewer: int | None) -> bool:
    """``identity_deal`` 的身份对 ``viewer`` 是否可见（A3）。

    ``record`` 是 ``identity_deal`` 的 JSON 形态（``seat`` / ``identity``）。发放是**开局
    暗发**，故只放行本人与主公；已亮明 / 阵亡者由公开的 ``identity_reveal`` 另行下发。
    ``viewer is None`` = 全知，不裁剪。
    """
    if viewer is None:
        return True
    return record.get("seat") == viewer or record.get("identity") == "lord"
