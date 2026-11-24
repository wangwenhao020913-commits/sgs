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
  ``PUBLIC`` 照发；``HIDDEN`` 对**任何**玩家载荷（含本人）都不下发；``FACE_VISIBLE``
  只在调用方已确认**牌面可见**时下发（网页载荷）；**记录流仍不下发**（本判据默认
  ``face_visible=False``，故 ``visible_record`` 的标签 delta 保持原样）。未登记的类型
  fail-closed（``tag_visible``）。
- **交换 / 扣置的牌面**（ai-view 票 12）：牌移动的牌面可见性不再只按**区域**回答——
  ``(HAND → PROCESSING, LOSE)`` 是"扣置"进去的那一腿，它的牌面由**持久公开事实**
  回答（:func:`public_face_card_ids`：``CardTagType.PUBLIC_FACE``），不看查看者身份
  （效果区只回答"场上公开的是什么"）；``(PROCESSING → HAND, PUT)`` 的回手腿读同一份
  交换前的公开性（``exchange_faces``），非公开的牌只给**收到它的本人**。规则按
  ``(from_zone, to_zone, reason)`` + 牌面公开性判，**不认技能 id**。
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

from sgs.data import CardTagType, hero_placement_decls

__all__ = [
    "PUBLIC_ZONES",
    "cards_visible",
    "deck_cards_visible",
    "hero_zone_ref_visible",
    "hero_zone_visible",
    "identity_visible",
    "placement_visibility",
    "public_face_card_ids",
    "tag_visible",
    "visible_card_ids",
]

PUBLIC_ZONES: Final[frozenset[str]] = frozenset({"discard", "processing", "equip", "judge"})
"""牌面一旦进入这些区域就是公开事实；手牌 / 牌堆仍是隐藏区。

票 12 起这条按**区**的判据不是全部：处理区仍算公开区，但**扣置**进去的牌是例外——
:func:`cards_visible` 的交换规则先回答那一档（形状 + 持久公开事实）。
"""


def public_face_card_ids(state: Any) -> frozenset[int]:
    """带**持久公开事实**的牌 id（ai-view 票 12 的唯一读点）。

    事实 = :attr:`~sgs.data.CardTagType.PUBLIC_FACE` 标签（明置），与区域无关。``state``
    缺失 / 没有 ``card_tags`` ⇒ 空集（调用方 fail-closed：看不出的牌面按不公开处理）。
    只读 duck-typed 的两格（``state.card_tags`` 与 ``tag.tag_type``），故 ``GameState``
    与测试替身同一份读法。
    """
    if state is None:
        return frozenset()
    tags = getattr(state, "card_tags", None) or {}
    wanted = CardTagType.PUBLIC_FACE.value
    return frozenset(
        int(card_id)
        for card_id, card_tags in tags.items()
        if any(
            str(getattr(tag.tag_type, "value", tag.tag_type)) == wanted
            for tag in card_tags
        )
    )


def _card_ids(record: Mapping[str, Any]) -> tuple[int, ...]:
    """一条移动记录里的实体牌 id（畸形条目跳过：判据不因脏载荷崩）。"""
    out: list[int] = []
    for card in record.get("cards") or ():
        if not isinstance(card, Mapping) or "card_id" not in card:
            continue
        try:
            out.append(int(card["card_id"]))
        except (TypeError, ValueError):
            continue
    return tuple(out)


def _move_shape(record: Mapping[str, Any]) -> tuple[str | None, str | None, str | None]:
    """``(来源区, 目标区, 原因)`` 三个字符串；缺项给 ``None``（不猜）。"""
    from_zone = record.get("from_zone") or {}
    to_zone = record.get("to_zone") or {}
    reason = record.get("reason")
    return (
        getattr(from_zone.get("zone"), "value", from_zone.get("zone")),
        getattr(to_zone.get("zone"), "value", to_zone.get("zone")),
        getattr(reason, "value", reason),
    )


def visible_card_ids(
    record: Mapping[str, Any],
    viewer: int | None,
    *,
    state: Any = None,
    exchange_faces: Mapping[int, bool] | None = None,
) -> tuple[int, ...]:
    """这条牌移动里**牌面可见**的那些牌 id（按记录顺序；空元组 = 一张都看不到）。

    ``record`` 是 ``card_move`` 的 JSON 形态（``from_zone`` / ``to_zone`` 各带 ``zone``
    与 ``seat``）；``viewer`` = 查看者座位，``None`` = 没有查看者 / 全知。

    区域分三档：**公开区**（弃牌堆 / 处理区 / 装备区 / 判定区）照发；**隐藏区**
    （牌堆 / 手牌）只放行查看者自己经手的那一侧；**武将牌上的牌区**（``hero``，2026-10
    用户口径）**只给宿主本人**——只要移动任一侧是 ``hero``，就按 :func:`hero_zone_ref_visible`
    回答，不再让"来源手牌是自己的"绕开宿主裁剪。

    票 12 的两条**交换形状**先于区域判据（按形状 + 公开性，不认技能 id），且是**逐牌**
    回答（一手牌里可能混着明置的与普通的）：

    - ``(HAND → PROCESSING, LOSE)`` = **扣置 / 交换的第一腿**。牌面公开性读**交换前**的
      持久事实（``state`` 的 ``PUBLIC_FACE`` 标签）：公开的那几张照发，其余只给张数
      （**不按查看者身份开洞**——双方对自己的手牌 / 收到的牌的知情来自各自的手牌视图，
      不来自效果区）。
    - ``(PROCESSING → HAND, PUT)`` = **交换的回手腿**。``exchange_faces``（
      :func:`sgs_web.projection.processing_entry_faces`：每张牌在处理区期间牌面是否公开）
      里标成 ``False`` 的牌对第三方不公开；**收到它的本人**照发（既有手牌可见性——
      他本来就要在自己的手牌视图里看到这张牌）。``exchange_faces`` 缺省 = 不是本判据
      认识的交换（例如五谷从牌堆亮出后取走），落回区域判据、行为与引入前一致。
    """
    if viewer is None:
        return _card_ids(record)
    ids = _card_ids(record)
    to_zone = record.get("to_zone") or {}
    from_zone = record.get("from_zone") or {}
    to = to_zone.get("zone")
    source = from_zone.get("zone")
    if to == "hero" or source == "hero":
        hero_ref = to_zone if to == "hero" else from_zone
        return ids if hero_zone_ref_visible(hero_ref, viewer) else ()
    source_zone, target_zone, reason = _move_shape(record)
    if ids and source_zone == "hand" and target_zone == "processing" and reason == "LOSE":
        # 扣置腿读**持久事实**（交换前是否公开）：逐牌、不按查看者开洞。不用
        # ``exchange_faces``——那张表是"处理区期间"的现势（处理区里被亮出会翻成公开），
        # 拿它回看这条**更早**的记录会把之后才公开的牌面提前放出去。
        public = public_face_card_ids(state)
        return tuple(card_id for card_id in ids if card_id in public)
    if (
        ids
        and source_zone == "processing"
        and target_zone == "hand"
        and reason == "PUT"
        and exchange_faces is not None
    ):
        if to_zone.get("seat") == viewer:
            return ids
        return tuple(
            card_id for card_id in ids if exchange_faces.get(card_id) is not False
        )
    if to in PUBLIC_ZONES or source in PUBLIC_ZONES:
        return ids
    if to == "hand" and to_zone.get("seat") == viewer:
        return ids
    if source == "hand" and from_zone.get("seat") == viewer:
        return ids
    return ()


def cards_visible(
    record: Mapping[str, Any],
    viewer: int | None,
    *,
    state: Any = None,
    exchange_faces: Mapping[int, bool] | None = None,
) -> bool:
    """这条牌移动的**牌面身份**对 ``viewer`` 是否公开（全部可见才为真）。

    判据住 :func:`visible_card_ids`；本函数是它的布尔形态，供叙事 / 既有调用方使用
    （叙事在"有一张不公开"时只写张数，故逐牌细节不必下传）。
    """
    ids = _card_ids(record)
    return len(visible_card_ids(
        record, viewer, state=state, exchange_faces=exchange_faces
    )) == len(ids)


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


def tag_visible(
    tag_type: str | None,
    viewer: int | None,
    *,
    face_visible: bool = False,
) -> bool:
    """一个**牌标签**的增删是否可下发（spec §投影与记录流 / D12 的第三项声明）。

    可见性由标签类型的登记表回答（``sgs.data.CARD_TAG_TYPES[...].visibility``）：

    - ``PUBLIC`` 照发；
    - ``FACE_VISIBLE`` 只在 ``face_visible=True`` 时下发。调用方**已经**通过既有的
      ``cards_visible`` / 区域载荷裁剪确认了牌面可见（payload 里存在这张牌的 ``card``
      就是该事实），本函数不另造第二套牌面可见性；
    - ``HIDDEN`` / 未登记 fail-closed。

    ``viewer is None`` = 全知通道，不裁剪（与其它判据同一条口径）。
    记录流的 ``tag_add`` / ``tag_remove`` 调用本函数时**不传** ``face_visible``，故
    ``FACE_VISIBLE`` 的标签增删仍不出现在人类记录流里——本票只改网页展示载荷。
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
    visibility = getattr(spec.visibility, "value", spec.visibility)
    if visibility == "public":
        return True
    return bool(face_visible) and visibility == "face_visible"


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
