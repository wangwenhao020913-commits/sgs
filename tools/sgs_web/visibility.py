"""记录流的**可见性判据**（ADR-012 / 地图 D8）。

A3 的口径按**记录类型**各有一条，全部只读记录字典：

- **一张牌移动的牌面**（``card_move``）：**区域**决定牌面是否公开——公开区（弃牌堆 /
  处理区 / 装备区 / 判定区）里出现过的牌是公开事实；只进出隐藏区（牌堆 / 手牌）的牌只给
  张数；查看者**自己的手牌**与**自己经手的移动**仍可见——那不是泄露。
- **牌堆顺序**（``deck_shuffle``）：A3 只给**数量**；整副洗后牌序只下发全知通道。
- **身份发放**（``identity_deal``）：A3 的「本人 / 主公 / 已亮明 / 阵亡」——发放是**开局
  暗发**，故只有本人与主公可见；"已亮明 / 阵亡"由**公开的** ``identity_reveal`` 记录承载
  （同一份记录流里的下一条），本判据不需要回读引擎状态。
- **武将牌上的牌区**（M6 票 19 / spec §区域）：整个区给不给看由放置技能的数据声明回答
  （``PlacementDecl.visibility``）——``public`` 谁都能看；``hidden`` 只给**放置技能的
  持有者**与**被扣牌的宿主本人**；未登记的取值一律不可见（fail-closed）。M6 票 20 把这
  条判据接进 ``card_move`` 的牌面可见性（``hero_zone_ref_visible``）：区域引用里的
  ``skill_instance`` 就是放置技能的全限定 id，故由数据声明回答、不在此处另抄名单。
- **牌标签**（M6 票 20 / D12 第三项声明）：增删 delta 的可见性读标签类型的登记表——
  ``PUBLIC`` 照发；``HIDDEN`` 对**任何**玩家载荷（含本人）都不下发；未登记的类型
  fail-closed（``tag_visible``）。
- ``viewer is None`` = 没有查看者（全知）：不裁剪（内部调用 / 测试需要原始记录）。

两个下发口**必须同源**，否则同一件事在事件流与日志里口径不一：

- :func:`sgs_web.projection.visible_record`——人类座位的记录流 / 日志载荷（M5-09c 起
  按这条判据把隐藏区的 ``cards`` 抹成空列表并下发 ``card_count``）；
- ``sgs_web.narrative._Narrator._gain``——简易事件流写"获得 N 张牌"还是写牌名。

判据住在这里而不是两边各写一份：投影与叙事都从这里 import（本模块只读记录字典 +
``sgs.data`` 的登记表，不 import 任何 ``sgs_web`` 模块，故不制造 ``projection`` ↔
``narrative`` 的循环依赖）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from sgs.data import hero_placement_decls

__all__ = [
    "PUBLIC_ZONES",
    "cards_visible",
    "deck_cards_visible",
    "hero_zone_ref_visible",
    "hero_zone_visible",
    "identity_visible",
    "placement_visibility",
    "tag_visible",
]

PUBLIC_ZONES: Final[frozenset[str]] = frozenset({"discard", "processing", "equip", "judge"})
"""牌面一旦进入这些区域就是公开事实；手牌 / 牌堆仍是隐藏区。"""


def cards_visible(record: Mapping[str, Any], viewer: int | None) -> bool:
    """这条牌移动的**牌面身份**对 ``viewer`` 是否公开。

    ``record`` 是 ``card_move`` 的 JSON 形态（``from_zone`` / ``to_zone`` 各带 ``zone``
    与 ``seat``）；``viewer`` = 查看者座位，``None`` = 没有查看者 / 全知。

    区域分三档：**公开区**（弃牌堆 / 处理区 / 装备区 / 判定区）照发；**隐藏区**
    （牌堆 / 手牌）只放行查看者自己经手的那一侧；**武将牌上的牌区**（``hero``，2026-10
    用户口径）**只给宿主本人**——只要移动任一侧是 ``hero``，就按 :func:`hero_zone_ref_visible`
    回答，不再让"来源手牌是自己的"绕开宿主裁剪。
    """
    if viewer is None:
        return True
    to_zone = record.get("to_zone") or {}
    from_zone = record.get("from_zone") or {}
    to = to_zone.get("zone")
    source = from_zone.get("zone")
    if to == "hero" or source == "hero":
        hero_ref = to_zone if to == "hero" else from_zone
        return hero_zone_ref_visible(hero_ref, viewer)
    if to in PUBLIC_ZONES or source in PUBLIC_ZONES:
        return True
    if to == "hand" and to_zone.get("seat") == viewer:
        return True
    if source == "hand" and from_zone.get("seat") == viewer:
        return True
    return False


def placement_visibility(skill_instance: str | None) -> str | None:
    """一个**武将牌上牌区**的可见性声明（``{owner}:{skill_id}`` → ``PlacementDecl.visibility``）。

    区键的第二段是放置技能的**全限定 id**（:func:`sgs.skills.catalog.hero_card_zone_key`
    的规范文本），故这里只读数据层的三项声明；键不合形、技能不在数据里、或技能没有
    placement 声明，一律返回 ``None``（调用方 fail-closed：不可见）。
    """
    if not isinstance(skill_instance, str) or ":" not in skill_instance:
        return None
    _owner, _, skill_id = skill_instance.partition(":")
    decl = hero_placement_decls().get(skill_id)
    if decl is None:
        return None
    return getattr(decl.visibility, "value", decl.visibility)


def hero_zone_ref_visible(zone_ref: Mapping[str, Any], viewer: int | None) -> bool:
    """一个 HERO 区域引用对 ``viewer`` 是否可见（牌面级别；``viewer=None`` = 全知）。

    ``zone_ref`` 是记录里的 ``from_zone`` / ``to_zone``：``seat`` = 武将牌的**宿主**。
    2026-10 用户口径：**只有宿主本人**看得见区里的牌；``viewer=None``（内部 / 回放 / 全知
    日志）不裁剪。未登记 / 缺 ``skill_instance`` 的区同一默认——宿主按 ``seat`` 认，不看
    ``PlacementDecl.visibility``（该声明不再决定本载荷的牌面可见性）。
    """
    if viewer is None:
        return True
    host = zone_ref.get("seat")
    if host is None:
        return False
    try:
        return int(host) == int(viewer)
    except (TypeError, ValueError):
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


def tag_visible(tag_type: str | None, viewer: int | None) -> bool:
    """一个**牌标签**的增删是否可下发（spec §投影与记录流 / D12 的第三项声明）。

    可见性由标签类型的登记表回答（``sgs.data.CARD_TAG_TYPES[...].visibility``）：
    ``PUBLIC`` 照发；``HIDDEN`` 对**任何**玩家载荷（含本人）都不下发；未登记的类型
    fail-closed。``viewer is None`` = 全知通道，不裁剪（与其它判据同一条口径）。
    """
    if viewer is None:
        return True
    from sgs.data import CARD_TAG_TYPES, CardTagType

    try:
        tag = CardTagType(tag_type)
    except ValueError:
        return False
    spec = CARD_TAG_TYPES.get(tag)
    if spec is None:
        return False
    return getattr(spec.visibility, "value", spec.visibility) == "public"


def hero_zone_visible(
    visibility: str, *, owner: int | None, host: int | None, viewer: int | None
) -> bool:
    """一个**武将牌上牌区**对 ``viewer`` 是否可见（2026-10 用户口径）。

    新口径：**只认宿主**——``host`` = 武将牌的主人，只有 ``viewer == host`` 看得见区里的
    牌；``viewer is None``（内部 / 回放 / 全知日志）不裁剪。``visibility`` 与 ``owner``
    保留在签名里供旧调用方兼容，但**不再参与判定**：未登记的区同一默认（牌只给宿主），
    放置技能的持有者不是宿主时也拿不到牌。

    ``viewer is None`` = 全知通道：不裁剪（与 :func:`cards_visible` 同一条口径）。
    """
    if viewer is None:
        return True
    if host is None:
        return False
    try:
        return int(host) == int(viewer)
    except (TypeError, ValueError):
        return False
