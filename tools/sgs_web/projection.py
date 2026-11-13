"""可见面：**唯一**的过滤点（ADR-012 / 地图 D8）。

两条口径，别混：

1. **给人类座位的状态快照** = 既有纯函数 :func:`sgs.state.project` 的输出。
   本模块**不**自己从 ``engine.state`` 拼字段（那就是第二份真源的雏形），也不缓存。
2. **记录流的下发** = 引擎记录流本身是**全知**的（``cli.py`` 的 docstring 自己写着），
   所以逐条过 :func:`visible_record`：
   - ``query`` 记录**不带** ``view`` 与 ``legal_actions``：非人类座位的询问带的是**别人的视角**，
     而人类座位的候选在"当前挂起"那一条里单独给（前端只需要能点的那一份）；
   - ``card_move`` 的牌面按**区域可见性**裁剪（M5-09c / spec §6 A3）：公开区照常可见，
     隐藏区（牌堆 / 手牌）只给数量（``cards`` 抹空 + ``card_count``）。判据与叙事同源，
     见 :mod:`sgs_web.visibility`；
   - ``deck_shuffle`` 的整副洗后牌序只给数量（``card_count``）；``identity_deal`` 的开局
     暗发只放行本人 / 主公（其余 ``identity: null``，亮明 / 阵亡走 ``identity_reveal``）；
   - ``action`` 记录是**公开事实**的只有 `kind` / `answers_seq` / `accepted`：**具体选了什么**
     只对人类座位自己的那条应答展开（U4-06 / spec D13）；展开时另补接入层展示表的中文技能名
     ``skill_name``；
   - ``timing`` / 其余 ``delta`` / ``frame`` / ``game_end`` 原样下发。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Any

from sgs.data import (
    SkillType,
    hero_by_id,
    hero_skill_id,
    load_content_pack,
    load_hero_tables,
)
from sgs.effects import behavior_for, hand_limit, responder_role, response_count
from sgs.engine import record_json
from sgs.skills import (
    CATALOG,
    FREE_SPLIT,
    AppearanceKind,
    JIE_LUOSHEN,
    LORD_SKILL_IDS,
    LUOSHEN,
    SEAT_PREFIX,
    ModifierPoint,
    SkillSourceKind,
    avatar_skill_rows,
    conversion_offers_by_source,
    current_avatar_skill,
    skill_name_on,
    is_forced_conversion,
    lord_skill_allowed,
    resolve_instances,
)
from sgs.state import (
    CardRef,
    FrameKind,
    MoveReason,
    Query,
    QueryKind,
    Reforge,
    SelectOptionQuery,
    WindowCause,
    Zone,
    project,
)
from sgs.timing import Timing

from .descriptions import skill_description
from .display import (
    card_lookup_from_query,
    card_payload,
    decorate_pending,
    decorate_snapshot,
    hero_card_display,
    hidden_guohuo_payload,
    option_details,
    suit_labels,
)
from .session import Session
from .visibility import (
    cards_visible,
    deck_cards_visible,
    identity_visible,
    tag_visible,
)

__all__ = [
    "APPEARANCE_BUILDERS",
    "HARVEST_REQUIREMENT",
    "build_skills",
    "candidate_index",
    "card_events_after",
    "concealed_by_later_reveal",
    "concealed_guohuo_cards",
    "damage_events_after",
    "decorate_records_card_tags",
    "harvest_state",
    "issued_manifest",
    "live_tag_deltas_after",
    "option_seats",
    "option_source",
    "pending_payload",
    "query_context",
    "seat_hero_zones",
    "seat_skill_display",
    "serialize_record",
    "setup_snapshot",
    "skill_events_after",
    "virtual_index",
    "visible_record",
    "visible_snapshot",
]

HARVEST_REQUIREMENT = "从亮出的牌里选一张获得"
"""【五谷丰登】取牌这一步的「需要做什么」（U2-11 / D10）。

公开处理区的正确口径：亮出的牌**牌面可见**，因此不是「隐藏区域的槽位只有位置」，
也不是通用的「请从选择面里选择一张牌」。`_card_window_requirement` 与询问载荷的
``harvest.hint`` 读同一个常量——前端只显示，不抄第二份文案。
"""

_DEFAULT_ACTIVATE_SKILLS: frozenset[str] = frozenset(
    {hero_skill_id("jie_jushou_mobile", "jianying")}
)
"""「是否发动」询问**默认 = 发动**的技能（yijiang-2014-round2 票 15；用户 2026-11-09 逐字）。

**opt-in 单技能**：登记在这里的技能，其 ``SKILL_ACTIVATION`` 载荷才带
``pending.default_choice = "activate"``；不在表里的「是否发动」（苦肉 / 洛神 / …）**连键都
不发**（不是发 ``None``）⇒ 其余询问的载荷逐字节不变，前端照旧默认提交 ``Pass``。

**为什么是投影层的登记表，而不是技能声明上的开关**（票面 §4 的路线 B 原话是"开关住
``use_after.py`` 的 ``JIE_JIANYING`` 声明处"）：那条技能 ① 的声明实际住在
``sgs/skills/active.py::_JIE_JIANYING``（``use_after.py`` 里只有 OL 沮授的 ``JIANYING``），
而它复用的零步流水线 :data:`~sgs.skills.use_after.JIANYING_FLOW` 与 OL 那一版是**同一个
对象**——开关加在 flow / Hook 上会连带把 OL 沮授也改掉，违反"只改这条技能"。投影按
**全限定技能 id** 认技能，与 ``judgment_reveal`` 段读 :data:`~sgs.skills.LUOSHEN` /
:data:`~sgs.skills.JIE_LUOSHEN` 是同一姿态（展示层认 id，引擎不认）。

取名 **default_choice**（前端键）而不是 `default_action`：候选仍是 ``legal_actions`` 里的
那一条，界面不另造 Action——"默认"只是**选中**哪条候选，不是第二种提交形状。
"""


def _card_lookup(state: Any) -> dict[int, Any]:
    """``card_id → CardRef``：展示层解析转化牌来源牌的唯一查找表。"""
    return {card.card_id: card for card in state.cards}


def _active_virtual_cards(engine: Any) -> tuple[Any, ...]:
    """正在结算的虚拟 / 转化牌（U1-07）：结算栈里带 ``card`` 的帧，去重后按压栈序。

    这些牌**从不进任何区域**（``CardRef.is_virtual``），故 ``state.processing`` 里只有
    转化牌的来源实体牌；展示层要画产物，就必须从结算栈拿这一份。读的是公开的栈载荷
    （``Frame.data["card"]``），不改记录流、不新增真源。
    """
    out: list[Any] = []
    seen: set[int] = set()
    for frame in engine.stack.frames:
        card = frame.data.get("card")
        if card is None or not card.is_virtual or card.card_id in seen:
            continue
        seen.add(card.card_id)
        out.append(card)
    return tuple(out)


def concealed_guohuo_cards(engine: Any) -> dict[int, str]:
    """**未验明**的【蛊惑】扣置牌：``源牌 id → 声明的牌型 id``。

    【蛊惑】的扣置是"**背面朝上**放进处理区"的：验明（``Engine.reveal_card``）之前，
    这张牌是哪张**还不属于公开事实**。而处理区是**全局公开区**（`sgs_web/visibility.py`
    的区域判据），不特别处理就会把源牌牌面下发给所有人 —— 那等于当场告诉所有人真假
    （用户实机报障 2026-11-02）。故展示层读结算栈上技能写下的公开事实：

    - ``guohuo_source``（扣置段）：处理区里那一张是哪张实体牌；
    - ``guohuo_name``（声明段）：它**被声明成**什么牌 —— 这是可以公开的那部分；
    - ``guohuo_revealed``（验明段）：``True`` = 已经亮过，牌面从此公开。

    验明之后本函数不再返回这一张，于是"公开"照旧从 ``card_reveal`` 那一刻开始。

    **"谁扣的"不再是这份判据的输入**（用户 2026-11-02 口径）：舞台与事件流对**任何人**
    （含扣置者自己）都只公开"声明的牌名"，故这里只回答"这张牌验明了没有、它被声明成
    什么牌"，没有第二个消费者需要记扣置者的座位。
    """
    out: dict[int, str] = {}
    for frame in engine.stack.frames:
        data = frame.data
        source = data.get("guohuo_source")
        declared = data.get("guohuo_name")
        if source is None or declared is None:
            continue
        if data.get("guohuo_revealed"):
            continue
        out[int(source)] = str(declared)
    return out


def concealed_by_later_reveal(records: Iterable[object], state: Any = None) -> dict[int, str]:
    """**本批记录里**"先进了处理区、之后才被公开亮出"的牌：``源牌 id → ""``。

    :func:`concealed_guohuo_cards` 读的是**当下**的结算栈，只回答"现在还挂在栈上的未验明牌"。
    但记录流是**回看**：一次**没有目标步**的【蛊惑】（无中生有 / 万箭齐发一类）会在同一批
    记录里走完"扣置 → 质疑 → 验明 → 结算 / 作废"，等这批记录下发时栈上早已不剩什么，
    扣置那条 ``card_move`` 于是带着**源牌牌面**直接下发；前端把它画进"处理中"的舞台格，
    看起来就像"验明之前就亮了源牌"（用户实机报障 2026-11-02 §2 的残留：有目标步时人会停在
    目标询问上、那一批记录赶在验明之前下发，所以只有"不用指定目标"的牌看得出来）。

    这里按**记录自己的次序**回答同一件事：先进处理区、后被亮出的那张牌，在那条搬牌记录里
    就是"还没验明"的。判定不认技能 id（任何"处理区里的牌被公开亮出"都符合），故不会给别
    的技能添特例。

    值 = **声明的牌型 id**（能解出来时）：这批记录里就有扣置者自己的复合动作
    （`ActivateSkill.picks / .choices`），`choices[0]` 是声明的**显示名**，按牌名反查牌型
    （与 `_guohuo_declaration_table` 同一张底表）。解不出来（旧形态 / 记录不全）⇒ ``""``
    —— 那时只回答"要不要掩码"，画家退回"只给张数"。
    """
    # 声明的牌型：**扣置者自己提交的复合动作**就在这批记录里（`ActivateSkill` 的
    # `picks` = 扣置的那张、`choices[0]` = 声明的**显示名**）。有 `state` 时按牌名反查
    # 牌型 id（与 `_guohuo_declaration_table` 同一张底表）；查不到 ⇒ 不猜，退回"只给张数"。
    declared: dict[int, str] = {}
    if state is not None:
        type_of_name = {card.name: card.type for card in state.cards}
        for record in records:
            action = getattr(record, "action", None)
            picks = getattr(action, "picks", ()) or ()
            choices = getattr(action, "choices", ()) or ()
            if not picks or not choices:
                continue
            type_id = type_of_name.get(str(choices[0]))
            if type_id is None:
                continue
            for card_id in picks:
                declared[int(card_id)] = type_id
    revealed_at: dict[int, int] = {}
    for record in records:
        if getattr(record, "delta_type", None) != "card_reveal":
            continue
        card = getattr(record, "card", None)
        seq = getattr(record, "seq", None)
        if card is None or not isinstance(seq, int):
            continue
        revealed_at[int(card.card_id)] = seq
    out: dict[int, str] = {}
    for record in records:
        if getattr(record, "delta_type", None) != "card_move":
            continue
        # 只认**扣置**那一串形状：手牌 → 处理区、原因 ``LOSE``。普通使用是 ``USE``、
        # 判定 / 摸牌走的是别的区域 —— 它们都是公开事实，不能被这条掩码顺手抹掉
        # （`tests/test_m5_no_leak.py` 的逐类判据会红）。
        from_zone = getattr(record, "from_zone", None)
        to_zone = getattr(record, "to_zone", None)
        seq = getattr(record, "seq", None)
        if (
            from_zone is None
            or from_zone.zone is not Zone.HAND
            or to_zone is None
            or to_zone.zone is not Zone.PROCESSING
            or getattr(record, "reason", None) is not MoveReason.LOSE
            or not isinstance(seq, int)
        ):
            continue
        for card in getattr(record, "cards", ()) or ():
            card_id = int(card.card_id)
            revealed = revealed_at.get(card_id)
            if revealed is not None and seq < revealed:
                out[card_id] = declared.get(card_id, "")
    return out


def _concealed_hidden(
    raw: Mapping[str, Any],
    viewer: int | None,
    concealed: Mapping[int, str] | None,
) -> bool | None:
    """这条记录里的牌面是不是**未验明的【蛊惑】扣置牌**（是 ⇒ 不下发牌面）。

    只有一条"移动的牌**全部**是未验明的扣置牌"时才算隐藏；混合移动（扣置牌与别的牌
    同批）照旧按区域判据下发——那种批次里本仓还不会出现，宁少判一档也不改既有口径。
    ``viewer is None``（全知通道）不裁。

    **扣置者自己也不放行**（用户 2026-11-02 口径，与舞台
    :func:`sgs_web.display._processing_display` 同一处口径）：事件的"这条移动搬的是哪些牌"
    在验明之前对场上就只公开到张数，扣置者本来就认识那张牌，给他另开一档只会让同一件事
    在舞台与事件流里各说一套。``viewer`` 仍参与判据，但只回答"是不是全知通道"。
    """
    if not concealed or viewer is None:
        return None
    cards = raw.get("cards")
    if not cards:
        return None
    ids: list[int] = []
    for card in cards:
        if not isinstance(card, dict) or "card_id" not in card:
            return None
        card_id = int(card["card_id"])
        if concealed.get(card_id) is None:
            return None
        ids.append(card_id)
    return bool(ids)


def _transform_products(
    query: Query,
    *,
    lookup: dict[int, Any] | None = None,
    state: Any | None = None,
    owner: int | None = None,
) -> list[dict[str, Any]]:
    """转化清单的**产物索引**（U1-07）：正在这次询问里的全部产物牌面。

    清单只描述"选哪些来源牌"，不含产物牌名；界面要写「将以【杀】使用」、也要知道产物
    是红 / 黑 / 无，必须读这份展示派生。数据只来自**本次询问内部的转化候选**
    （``Query.transform_actions``）——它们已经从 ``legal_actions`` 收回，不会重新打开
    "手牌直接点一下就当【杀】"的老路径。

    ``forced``（报障 2026-10-31）：这份产物是不是**强制转化**（‘只能’型，如【武神】）
    转出来的。手牌区只把 ``forced=True`` 的产物画成默认牌面（点它进转化清单）；
    ‘可以’型（武圣 / 龙胆 / 国色 / 急救 / 丈八 / 羽扇）的产物**仍下发**（预发动提示要读
    牌名 / 颜色），但 ``forced=False`` —— 源牌保持原样，转化只走技能预发动。判据读
    声明 :attr:`~sgs.skills.heroes.ConversionSkill.forced`（**不是** ``locked``）；
    拿不到引擎（409 的裸 ``Query``）时降级为 ``False``（fail-closed，不画产物）。

    按产物 ``card_id`` 去重（同一来源组 / 同一目标类型只出现一次）。界面按选中来源
    牌组匹配（与 `session.js:sourceGroupKey` 同一份分组规则）；若同一组来源牌能产出
    两张不同产物（多转化技重叠），界面 fail-closed、不猜名字。查看者不是询问者时
    调用方传空列表（不把别人的转化方向带出去）。
    """
    out: dict[int, dict[str, Any]] = {}
    for action in getattr(query, "transform_actions", ()) or ():
        card = getattr(action, "card", None)
        if card is None or not card.is_virtual or not card.virtual_sources:
            continue
        if card.card_id in out:
            continue
        payload = card_payload(card, lookup=lookup)
        payload["forced"] = bool(
            state is not None
            and owner is not None
            and is_forced_conversion(state, owner, card)
        )
        out[card.card_id] = payload
    return list(out.values())


SKILL_STATES: tuple[str, ...] = ("active", "suppressed", "lost")
"""技能在详情里的**三态**（M6 票 19；稳定报文值）。

- ``active``：技能在武将牌上、且参与解析；
- ``suppressed``：**失效**——实例还在（武将牌上看得见），但被座位级谓词挡在解析之外，
  作用域到期即恢复（`GLOSSARY.md`「技能失效」）：界面要画"有但被挡住"；
- ``lost``：**失去**——实例不再存在且不可恢复，但武将面的技能清单一字未动（规则 15.2 /
  「技能失去」）：界面要画"没了"。
"""


def _skill_entry(
    definition: Any,
    name: str,
    *,
    skill_type: str | None = None,
    star: bool = False,
    state: str = "active",
) -> dict[str, Any]:
    """技能条目的**公共只读键**（可操作清单 / 详情清单共用，避免两边各抄一份）。

    ``star``（M6 票 06 / 票 19）：这条技能是 **☆ 技能**（觉醒后由某条觉醒技获得）；
    ``state``：见 :data:`SKILL_STATES`。两个键都是**公开事实**（武将牌上的技能是明置的），
    不含当前询问 / 手牌，故可对任意座位下发。
    """
    if state not in SKILL_STATES:  # pragma: no cover - 闭集，防御性守卫
        raise ValueError(f"未知的技能状态 {state!r}（可选 {SKILL_STATES}）")
    return {
        "skill_id": definition.skill_id,
        "name": name,
        "skill_type": skill_type
        or getattr(definition.skill_type, "value", str(definition.skill_type)),
        "locked": bool(definition.locked),
        "carrier": getattr(definition.carrier, "value", str(definition.carrier)),
        "star": bool(star),
        "state": state,
        "description": skill_description(definition.skill_id),
    }


def _card_tag_payloads(state: Any, card_id: Any, viewer: int | None) -> list[dict[str, str]]:
    """一张**牌面可见**的牌上的标签；``label`` 取来源技能的展示名（不写死中文）。"""
    try:
        card_id = int(card_id)
    except (TypeError, ValueError):
        return []
    if card_id < 0:  # 虚拟牌从不持久存在，也就没有牌标签
        return []
    out: list[dict[str, str]] = []
    for tag in state.tags_of(card_id):
        if not tag_visible(tag.tag_type.value, viewer, face_visible=True):
            continue
        out.append(
            {
                "skill_id": tag.skill_id,
                "label": _skill_display_name(state, tag.skill_id) or tag.skill_id,
            }
        )
    return out


def _decorate_card_tags(card: Any, state: Any, viewer: int | None) -> None:
    """给一份**已确认牌面可见**的卡面 dict 补 ``tags``；没有标签不造空键。"""
    if not isinstance(card, dict) or "card_id" not in card:
        return
    tags = _card_tag_payloads(state, card.get("card_id"), viewer)
    if tags:
        card["tags"] = tags


def _decorate_snapshot_card_tags(
    payload: dict[str, Any], state: Any, viewer: int | None
) -> None:
    """快照里所有**已下发的牌面**补标签（隐藏区的牌本来就不在载荷里）。"""
    for key in ("self_hand", "discard", "processing"):
        for card in payload.get(key) or []:
            _decorate_card_tags(card, state, viewer)
    for player in payload.get("players") or []:
        for key in ("equip", "judge"):
            for card in player.get(key) or []:
                _decorate_card_tags(card, state, viewer)
        for zone in player.get("hero_zones") or []:
            for card in zone.get("cards") or []:
                _decorate_card_tags(card, state, viewer)


def _decorate_pending_card_tags(
    payload: dict[str, Any], state: Any, viewer: int | None
) -> None:
    """询问载荷里所有**已下发的牌面**补标签；``legal_actions`` 不碰。"""
    for entry in (payload.get("candidate_index") or {}).values():
        if isinstance(entry, dict):
            _decorate_card_tags(entry.get("card"), state, viewer)
    for slot in payload.get("slots") or []:
        if isinstance(slot, dict):
            _decorate_card_tags(slot.get("card"), state, viewer)
    for key in ("revealed", "transform_products"):
        for card in payload.get(key) or []:
            _decorate_card_tags(card, state, viewer)
    reveal = payload.get("reveal")
    if isinstance(reveal, dict):
        _decorate_card_tags(reveal.get("card"), state, viewer)
    judgment = payload.get("judgment_reveal")
    if isinstance(judgment, dict):
        _decorate_card_tags(judgment.get("card"), state, viewer)
        for card in judgment.get("cards") or []:
            _decorate_card_tags(card, state, viewer)
    harvest = payload.get("harvest")
    if isinstance(harvest, dict):
        _decorate_card_tags(harvest.get("card"), state, viewer)
        for card in harvest.get("revealed") or []:
            _decorate_card_tags(card, state, viewer)
    view = payload.get("view")
    if isinstance(view, dict):
        _decorate_snapshot_card_tags(view, state, viewer)


def _visible_card_ids(payload: Mapping[str, Any]) -> set[int]:
    """一份快照里**已经下发牌面**的牌 id（标签可见性的现成判据）。"""
    ids: set[int] = set()
    for key in ("self_hand", "discard", "processing"):
        for card in payload.get(key) or []:
            if isinstance(card, Mapping) and card.get("card_id") is not None:
                ids.add(int(card["card_id"]))
    for player in payload.get("players") or []:
        if not isinstance(player, Mapping):
            continue
        for key in ("equip", "judge"):
            for card in player.get(key) or []:
                if isinstance(card, Mapping) and card.get("card_id") is not None:
                    ids.add(int(card["card_id"]))
        for zone in player.get("hero_zones") or []:
            if not isinstance(zone, Mapping):
                continue
            for card in zone.get("cards") or []:
                if isinstance(card, Mapping) and card.get("card_id") is not None:
                    ids.add(int(card["card_id"]))
    return ids


def decorate_records_card_tags(
    records: list[dict[str, Any]], state: Any, viewer: int | None
) -> None:
    """给人类 ``records`` 里**已下发牌面**的牌补标签（只改网页载荷）。

    ``tag_add`` / ``tag_remove`` 本身仍被 :func:`visible_record` 裁掉；但一次
    ``card_move`` 可能把带标签的牌搬进手牌 / 弃牌堆，前端逐事件追加这份牌面时
    需要标签跟着牌走。这里只补 ``tags``，不改牌的位置 / 数量，也不改记录类型。
    """
    for record in records:
        if not isinstance(record, dict) or record.get("kind") != "delta":
            continue
        delta_type = record.get("delta_type")
        if delta_type == "card_move":
            for card in record.get("cards") or []:
                _decorate_card_tags(card, state, viewer)
        elif delta_type == "card_reveal":
            _decorate_card_tags(record.get("card"), state, viewer)


def _live_record_value(record: Any, name: str, default: Any = None) -> Any:
    """模型 / 已序列化 dict 两种形状读同一个字段。"""
    if isinstance(record, Mapping):
        return record.get(name, default)
    return getattr(record, name, default)


def live_tag_deltas_after(
    records: Iterable[Any],
    *,
    after: int,
    state: Any,
    snapshot: Mapping[str, Any],
    viewer: int | None,
) -> list[dict[str, Any]]:
    """把人类记录流里被裁掉的**可见牌标签增删**转成展示用的 live delta。

    只服务网页载荷的逐事件 live view（``live_deltas``），**不进入** ``records``：
    人类记录流里的 ``tag_add`` / ``tag_remove`` 保持原样裁掉。每条载荷带服务端
    从技能目录派生的 ``tag.label``，前端不建技能 id → 中文名表。

    可见性复用**快照里已经下发的牌面**：``snapshot`` 里没有的牌（他人手牌 / 牌堆）
    连 live delta 都不发；``FACE_VISIBLE`` 因此不会从这条旁路泄露牌面或标签名。
    顺序按记录 ``seq``，由前端与同批记录一起逐事件消费。
    """
    visible = _visible_card_ids(snapshot)
    out: list[dict[str, Any]] = []
    for record in records:
        if _live_record_value(record, "kind") != "delta":
            continue
        seq = _live_record_value(record, "seq")
        if not isinstance(seq, int) or seq <= after:
            continue
        delta_type = _live_record_value(record, "delta_type")
        if delta_type not in ("tag_add", "tag_remove"):
            continue
        card = _live_record_value(record, "card")
        card_id = (
            card.get("card_id")
            if isinstance(card, Mapping)
            else getattr(card, "card_id", None)
        )
        if card_id is None or int(card_id) not in visible:
            continue
        tag_type = _live_record_value(record, "tag_type")
        tag_type_value = getattr(tag_type, "value", tag_type)
        if not tag_visible(tag_type_value, viewer, face_visible=True):
            continue
        skill_id = str(_live_record_value(record, "skill_id"))
        out.append(
            {
                "kind": "delta",
                "delta_type": str(delta_type),
                "seq": int(seq),
                "card_id": int(card_id),
                "tag": {
                    "skill_id": skill_id,
                    "label": _skill_display_name(state, skill_id) or skill_id,
                },
            }
        )
    return out


def visible_snapshot(session: Session) -> dict[str, Any]:
    """人类座位的**玩家视角**快照（每次现算、不缓存）。

    这是 ``tests/test_web.py`` 的"不泄露"判据盯住的那个对象：对手只有 ``hand_count``、
    身份只出现"本人 / 主公 / 已阵亡 / ``identity_visible`` 为真"四种可见情形、牌堆只有
    数量无顺序。``hand_limit``（票 09 的手牌读数上限）也在这里，但它是**允许字段**：
    只由公开事实派生，与 ``hand_count`` 同级，不含隐藏牌面信息。``sealed_zones``
    （lei 票 05 的被废除区域）也是**允许字段**：它是公开区事实（谁的哪个区被废除了），
    取值原样取自 ``PlayerPublic``，界面据此画红色禁止角标。
    """
    state = session.engine.state
    view = project(state, session.human_seat)
    payload = view.model_dump(mode="json")
    # U1-04 展示契约：牌面色 / 属性 / 种类 / 来源牌 + 座位身份标签。派生在 display.py，
    # 投影只负责"哪些可见"；下发的仍然是**同一份可见快照**，不新增真源。
    # U1-02：武将块要名称 / 势力，引擎只给 id —— 武将表由接入层传进去，界面不自己翻。
    decorate_snapshot(
        payload,
        view.players,
        lookup=_card_lookup(state),
        heroes=load_hero_tables(session.pack.pack_id),
        virtual_cards=_active_virtual_cards(session.engine),
        # UI1/UI2：验明之前的【蛊惑】扣置牌**不给牌面**（处理区是全局公开区，这里按
        # "它不是公开事实"处理），舞台改画**声明的牌名**。
        concealed=concealed_guohuo_cards(session.engine),
    )
    # U1-06：武将详情对**任意**座位可开，故每个座位都带一份只读技能清单
    # （武将牌 + 装备区 + 虚拟装备）。它与顶层的 `skills`（可操作清单）不同源：
    # 顶层带 `available` / `ui.convert_sources`，读的是**查看者当前询问与手牌**。
    for player_payload, player in zip(payload["players"], view.players, strict=True):
        player_payload["skills"] = seat_skill_display(session, player.seat)
        # M6 票 19 / 用户口径：武将牌上的牌区——**标签全体可见、牌只给宿主**；
        # 未登记区同一默认（标签退回技能 id），可见性只在服务端裁。
        player_payload["hero_zones"] = seat_hero_zones(
            session, player.seat, viewer=session.human_seat
        )
        # B：武将块上的状态标签（手牌上限加式 / 裸衣）。纯网页显示字段，不进 PlayerView。
        player_payload["hero_tags"] = seat_hero_tags(state.player(player.seat))
        # 票 09：手牌读数「手牌：张数/上限」。上限是**派生量**（体力值 → 替换链 → 加式），
        # 前端不自己算（那会是第二份真源）⇒ 与 `hero_tags` 同姿态只补进**网页载荷**：
        # 不进 `PlayerPublic` / `PlayerView`（AI 读面要登记读点表，记录流也不该多字节）。
        player_payload["hand_limit"] = hand_limit(state, state.player(player.seat))
        # lei 票 05：被废除（封印）的区域。`PlayerPublic.sealed_zones` 是**公开事实**但
        # `exclude=True`（不进记录流）⇒ 与 `hero_zones` / `hand_limit` 同姿态在这里补进
        # 网页载荷；值**原样**取自投影（不重算、不另造名单），座位卡据此叠红色禁止角标。
        player_payload["sealed_zones"] = list(player.sealed_zones)
    # 牌面标签：只给**已下发的牌面**补 tags；隐藏区的牌不在载荷里，故连标签也没有。
    _decorate_snapshot_card_tags(payload, state, session.human_seat)
    # M6 票 19：顶栏的轮次 / 额外回合提示。轮次自票 11 起是引擎事实（额外回合不推进
    # 自然指针），但 ``PlayerView`` 里**没有**它——加进去会改 47 条金录像的记录字节
    # （票 11 已核）。故与技能清单同姿态：只在本函数里补进**网页载荷**，记录流不动。
    payload["round"] = int(state.round)
    payload["turn_extra"] = bool(state.turn_extra)
    return payload


_SETUP_EMPTY_ZONES: tuple[str, ...] = ("equip", "judge", "hero_zones")
"""开局底稿里**必然空着**的牌区键（座位载荷里三者的形状都是列表）。"""


def setup_snapshot(session: Session) -> dict[str, Any]:
    """**开局底稿**：发牌前那一帧的权威快照（table-ux-overhaul 票 14 / spec §2 Q7）。

    为什么需要它：建局响应止于"游戏开始时"（``Session.drive`` 的 L0 边界），前端要逐条
    从**发牌前**演起。此前这份骨架是前端从 post-init 快照**倒推**出来的
    （``session.js:openingLiveView`` 把 ``hp`` 写成 ``hp_max ?? hp``、``hp_max`` / ``armor``
    / ``markers`` 原样继承）——载荷只给终值，界面就只能从记录猜规则。现在改成服务端下发：
    底稿里的每一个字都由引擎状态导出，前端只显示。

    **调用时机是契约的一半**：只能在 ``_start``（``GAME_START`` 派发）**之前**调。此刻
    ``engine.state`` 是 ``build_initial`` 的产物——座位 / 武将 / 技能 / 体力上限 / 护甲 /
    标记都是开局事实，而「游戏开始时」的钩子（【天成】改上限、摸牌）**还没跑**。
    ``Session.__init__`` 因此在 ``drive()`` 之前把它冻结成 ``Session.setup``。

    与 :func:`visible_snapshot` 同一份过滤口径（座位 / 武将 / 技能 / 标记 / 手牌上限 /
    封印区都直接读它），只把"发牌前还没发生"的那几格按住：

    - ``hand_count = 0`` 与 ``self_hand = []``：起始手牌是这条记录流的第一批 ``card_move``
      演的，底稿必须从"一张都没有"起；
    - ``hp = hp_max``：发牌前满体力。**不读此刻的 ``hp``**——它只是碰巧也满；
    - ``identity / identity_label = None``：身份由 ``identity_deal`` 逐条亮（本人 / 主公
      可见、其余 ``null``，与 :func:`~sgs.state.project` 的可见性同源）。底稿先给 = t0
      就把全场身份摆上桌，那才是泄露；
    - ``deck_count = 0``：牌堆张数由第一条 ``deck_shuffle`` 记录写回（``applyLiveDelta``
      的 ``from_zone = null`` 分支）；
    - ``discard`` / ``processing`` / 三个牌区 / ``phase`` / ``turn_seat`` / ``turn_extra``：
      发牌前一律空 / 假（无身份局与身份局同形）；
    - ``round = 1``：``_start`` 之前引擎的 ``state.round`` 还是 0（它由 ``_start`` 写 1），
      而底稿是"第一轮即将开始"那一帧——写 1，与随后第一条 ``turn_change`` 一致。

    **不进记录流**（不是 ``JournalRecord``，不是 delta），只随 ``after < 0`` 的那份视图下发；
    回放（记录流单独喂引擎）因此逐字节不受影响。
    """
    snapshot = visible_snapshot(session)
    for player in snapshot["players"]:
        player["hand_count"] = 0
        player["hp"] = player["hp_max"]
        player["identity"] = None
        player["identity_label"] = None
        for zone in _SETUP_EMPTY_ZONES:
            player[zone] = []
    snapshot["self_hand"] = []
    snapshot["discard"] = []
    snapshot["processing"] = []
    snapshot["deck_count"] = 0
    snapshot["phase"] = None
    snapshot["turn_seat"] = None
    snapshot["turn_extra"] = False
    snapshot["round"] = 1
    return snapshot


def seat_hero_zones(
    session: Session, seat: int, *, viewer: int | None
) -> list[dict[str, Any]]:
    """一个座位**武将牌上的牌区**（M6 票 19 / spec §区域；2026-10 用户口径修订）。

    口径：

    - 区键 = （座位, 技能实例）。座位 = 武将牌的主人（**可跨座位**：牌可以扣在别人身上），
      技能实例 = 放置技能的持有者 + 技能 id；`Player.hero_cards` 的键已经是规范文本。
    - **标签全体可见**：每个非空区都下发 ``skill_id`` / ``skill_name`` / ``owner``；
      未登记区没有中文名时退回 ``skill_id``（fail-visible，不猜名字）。
    - **牌默认只有宿主可见**：``cards`` 只在 ``viewer == seat``（宿主）或 ``viewer is None``
      （全知通道）时填；非宿主拿到空数组——不是把牌发给客户端再用 CSS 藏。旧
      ``PlacementDecl.visibility`` 不再决定本载荷的牌面可见性（用户口径：只认宿主）。
    - **只画有牌的区**：空区在引擎里是合法且被保留的（规则 15.2：失去技能不影响区），
      但界面不需要一行空框。
    - 牌面走**同一份卡面契约**（``display.card_payload``）：牌名 / 属性 / 描述与别处一致。
    - 这些牌不在手牌区，故公开的 ``hand_count`` 不因它们变化（票面验收）。
    """
    state = session.engine.state
    player = state.player(seat)
    lookup = _card_lookup(state)
    host_view = viewer is None or int(viewer) == int(seat)
    out: list[dict[str, Any]] = []
    for zone in player.hero_cards.values():
        if not zone.cards:
            continue
        cards: list[dict[str, Any]] = []
        if host_view:
            for card_id in zone.cards:
                try:
                    card = state.card(card_id)
                except (KeyError, IndexError):  # pragma: no cover - 牌守恒由引擎保证
                    continue
                card_payload_dict = card_payload(card, lookup=lookup)
                _decorate_card_tags(card_payload_dict, state, viewer)
                cards.append(card_payload_dict)
        out.append(
            {
                "skill_id": zone.skill_id,
                "skill_name": (
                    _skill_display_name(state, zone.skill_id) or zone.skill_id
                ),
                "owner": int(zone.owner),
                "count": len(zone.cards),
                "cards": cards,
            }
        )
    return out


def seat_hero_tags(player: Any) -> list[dict[str, Any]]:
    """武将块上的**状态标签**（B；纯显示层，不进 ``PlayerView`` / 记录流）。

    读引擎座位上的公开状态字段，只给标签名，不把效果文案抄到每张牌上：

    - ``Player.hand_limit_bonus`` 非 0 ⇒ 「手牌上限+N」；
    - ``Player.luoyi_active`` / ``Player.luoyi_until_next_turn`` ⇒ 「裸衣」（标准 / 界两个
      作用域字段同一条标签；不给【杀】/【决斗】逐张画“伤害+1”）。
    """
    tags: list[dict[str, Any]] = []
    bonus = int(getattr(player, "hand_limit_bonus", 0) or 0)
    if bonus:
        tags.append({"tag": "hand_limit_bonus", "label": f"手牌上限{bonus:+d}"})
    if getattr(player, "luoyi_active", False) or getattr(
        player, "luoyi_until_next_turn", False
    ):
        tags.append({"tag": "luoyi", "label": "裸衣"})
    return tags


def seat_skill_display(session: Session, seat: int) -> list[dict[str, Any]]:
    """一个座位**详情模态**的只读技能清单（U1-06）。

    口径（spec §3.2「详情里的装备技能 = 当前装备区 + 虚拟装备」）：

    - **武将技能**读公开武将面（``Hero.skills``），**不**经 ``resolve_instances``——
      后者只解析**存活实例**（死者不能发动技能），而阵亡武将的详情仍要开得出来；
      主公技照旧按 ``player.identity`` 门控（非主公的武将牌上没有它）。
    - **☆ 技能**（``HeroSkill.granted_by`` 非空）**觉醒后才出现**：它的实例只来自
      "获得"（``Player.granted_skills``），未获得时武将牌上根本没有它——这不是"有但
      不生效"，故这里按 ``granted_skills`` 决定列不列，而不是把武将面的三条全铺出来。
    - **三态**（M6 票 19）：``lost`` = 在 ``Player.lost_skills`` 里（武将面不删，故仍列）；
      ``suppressed`` = 实例还在、但被座位级失效记录挡在解析之外（锁定技不受影响）——
      判据与引擎同源：``resolve_instances`` 解析两次（含 / 不含失效）再作差。
      **``lost`` 只对"武将牌上原有"的技能生效**：``Player.granted_skills`` 里的**外来**技能
      失去之后整条不列（用户 2026-11-06 验收口径——留一行划掉的名字会让人以为武将牌丢了
      个技能，而它从来没写在牌上）。
    - **装备技能** = 装备区里的实体牌（``equip.<牌名 id>``）+ ``virtual_equipment``
      的虚拟授予（不占牌守恒，如【八阵】）。顺序 = 武将技能 → 获得（☆）→ 实体装备 →
      虚拟装备。
    - **只读展示**：不带 ``available`` / ``preactivated`` / ``ui.convert_sources``；
      那三样会读出当前询问与本人手牌，给到别的座位就是暗置信息。``ui.passive``
      是技能定义上的公开类别，详情用它标「被动技」。
    """
    state = session.engine.state
    player = state.player(seat)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    resolved = {instance.skill_id for instance in resolve_instances(state) if instance.owner == seat}
    with_suppressed = {
        instance.skill_id
        for instance in resolve_instances(state, include_suppressed=True)
        if instance.owner == seat
    }
    suppressed = with_suppressed - resolved
    lost = set(player.lost_skills)
    granted = set(player.granted_skills)

    def state_of(skill_id: str) -> str:
        if skill_id in lost:
            return "lost"
        if skill_id in suppressed:
            return "suppressed"
        return "active"

    def add(definition: Any, name: str | None, *, star: bool = False) -> None:
        if definition is None or name is None or definition.skill_id in seen:
            return
        seen.add(definition.skill_id)
        entry = _skill_entry(
            definition, name, star=star, state=state_of(definition.skill_id)
        )
        entry["ui"] = {"passive": _is_passive_skill(definition)}
        out.append(entry)

    hero = hero_by_id(player.hero, pack_id=state.pack_id) if player.hero else None
    if hero is not None:
        for skill in hero.skills:
            skill_id = hero_skill_id(hero.id, skill.id)
            definition = CATALOG.get(skill_id)
            if definition is None:
                continue
            if skill_id in LORD_SKILL_IDS and not lord_skill_allowed(
                player.identity, skill_id
            ):
                continue
            # ☆ 技能只来自"获得"：未获得时武将牌上没有它（载体解析的结构事实）。
            if skill.granted_by is not None and skill_id not in granted:
                continue
            add(definition, skill.name, star=skill.granted_by is not None)
    # 武将面之外的**获得**技能（如将来"获得别人的技能"）：名字只能从数据表反查。
    for skill_id in player.granted_skills:
        if skill_id in seen:
            continue
        if skill_id in lost:
            # **外来**技能失去之后整条不列（用户 2026-11-06 验收口径）：它的武将牌上
            # 从来没有这一条，"划掉 + 失去标签"只该留给**牌上原有**的技能（上面那一支，
            # 如【义起】失去【从讨】）。两者读同一条 ``Player.lost_skills``，区分只在
            # "武将面上有没有这个条目"。
            continue
        definition = CATALOG.get(skill_id)
        if definition is None:
            continue
        short = skill_id.rsplit(".", 1)[-1]
        star = bool(
            hero is not None
            and any(
                skill.id == short and skill.granted_by is not None for skill in hero.skills
            )
        )
        add(definition, _skill_display_name(state, skill_id) or short, star=star)
    for card_id in player.equip:
        card = state.card(card_id)
        add(CATALOG.get(f"equip.{card.type}"), _card_type_name(state, card.type))
    for grant in player.virtual_equipment:
        definition = CATALOG.get(grant)
        if definition is None:
            continue
        type_id = (
            definition.skill_id[len("equip.") :]
            if definition.skill_id.startswith("equip.")
            else None
        )
        add(definition, _card_type_name(state, type_id) if type_id else definition.skill_id)
    return out


def build_skills(session: Session, seat: int) -> list[dict[str, Any]]:
    """``seat`` 的**技能清单**（刀 4）：名称 / 类型 / 是否锁定 / **此刻是否可用**。

    三件事必须说清，否则这条清单是编的：

    - **谁的技能**：技能实例由 ``sgs.skills.resolve_instances`` 解析，它已经含身份门控
      （非主公的武将牌上**没有**主公技），故"有的技能"不是前端猜的；
    - **什么类型**：``locked`` 是官方"牌面有没有『可以』"的逐字判据，中文名与
      ``skill_type`` 取自**武将面数据**（`HeroSkill.name` / `.skill_type`）；"获得"来的
      技能（载体 ``GRANTED``，如【义起】授予的【武圣】【咆哮】）在**本人**武将面上没有
      条目，名字按目录反查（:func:`_skill_display_name`，与详情页同一份）；W4-fix-3 起
      ``ui.passive`` 另标"触发技"（有钩子 / 接缝且无主动入口，见
      :func:`_is_passive_skill`）——**不改** ``skill_type`` 的语义；
    - **此刻能不能用**：`available` = 当前挂起的询问里有没有针对这个技能的
      ``ActivateSkill`` 候选。**不去问引擎"这个技能满不满足条件"**——那是第二条真源。

    清单还保留 ``state = suppressed`` 的非锁定技：实例仍在武将牌上，只是被座位级失效
    记录挡在解析之外。它不可发动、也不给转化来源，但界面要在按钮上画"有但被挡住"
    （禁止图标）——不能让它整条消失。``lost`` 是另一种语义（实例不再存在），仍不列。
    只列 ``seat`` 自己的实例（带可用性 / 转化来源）；武将详情用的**公开只读**
    清单是 :func:`seat_skill_display`——它按武将牌 + 装备区给所有座位，不含可用性，
    故不会把当前询问或本人手牌带给别人。
    """
    state = session.engine.state
    pending = session.pending
    actions = tuple(pending.legal_actions) if pending is not None else ()
    offered = {
        action.skill_id
        for action in actions
        if action.kind == "ActivateSkill"
    }
    # W4-1：**预发动清单也是可用性的一份**。已翻到预发动的技能不再有骨架候选，可用性
    # 只剩 ``PLAY_PHASE.preactivations``（主动入口）与 ``SKILL_ACTIVATION.preactivation``
    # （被动入口）——两者都从询问里读，不另问引擎"满不满足条件"。
    # W4-fix-2：转化技的候选已从 ``legal_actions`` 收回，可用性同样由转化清单
    # （``transform_preactivations``）回答。
    if pending is not None:
        offered.update((getattr(pending, "preactivations", None) or {}).keys())
        offered.update((getattr(pending, "transform_preactivations", None) or {}).keys())
        if getattr(pending, "preactivation", ()):
            offered.add(str(getattr(pending, "skill_id", "")))
    hand = tuple(state.player(seat).hand)
    virtual = _virtual_index(pending, state, owner=seat) if pending is not None else {}
    hero_id = state.players[seat].hero
    names: dict[str, tuple[str, str, bool]] = {}
    if hero_id is not None:
        hero = hero_by_id(hero_id, pack_id=state.pack_id)
        if hero is not None:
            # 武将面里的技能 id 是**短名**（`guose`），而实例上的 `skill_id` 带命名空间
            # （`hero.daqiao.guose`）；两边靠**末段**对上，别去猜前缀。第三项 = ☆ 标记
            # （M6 票 19：觉醒后出现的技能在按钮上也标出来）。
            names = {
                skill.id.rsplit(".", 1)[-1]: (
                    skill.name,
                    getattr(skill.skill_type, "value", str(skill.skill_type)),
                    skill.granted_by is not None,
                )
                for skill in hero.skills
            }

    # 失效 = 含失效解析时还在、正常解析时不在（与 `seat_skill_display` 同一判据）：
    # 不能只拿正常解析的结果遍历，否则被【铁骑】封锁的技能会从按钮区整条消失。
    resolved_ids = {
        instance.skill_id for instance in resolve_instances(state) if instance.owner == seat
    }
    out: list[dict[str, Any]] = []
    asked_here = pending is not None and int(getattr(pending, "actor", -1)) == seat
    for instance in resolve_instances(state, include_suppressed=True):
        if instance.source.kind in (
            SkillSourceKind.MARKER,
            SkillSourceKind.CARD_TAG,
        ):
            # **标记自身效果**不是技能（lei-shenganning 票 01）：它由 ``Marker.effect`` 指向
            # 一条效果定义，持有者并没有"学会"任何东西——列进按钮区就会出现一个
            # 点不动的幻影技能（名字还只能是短 id）。效果本身照常生效（实例由
            # ``resolve_instances`` 产出，钩子 / 修饰器读它），这里只把它挡在**技能清单**
            # 这一面之外。``seat_skill_display`` 走的是武将面 / 装备区，本来就看不到它。
            continue
        cross_seat = instance.owner != seat
        definition = instance.definition
        # 跨座位授予（feng-zhangjiao 票 01：张角【黄天】——别的座位的技能、此刻发给我）。
        # 两条准入，缺一不列，**其余别人的技能一条都不列**（否则每个座位都会看到全场技能）：
        #   ① 这一问是问我、且候选里真有这条技能 ⇒ 列出来（可点亮）；
        #   ② **主公技授予同势力座位**：同势力的座位**恒列**（`available` 照旧读候选，
        #      没有可交的牌 ⇒ 画灰）。用户实机报障（2026-11-02 验收）：手里没有
        #      【闪】/【闪电】时这条技能整条消失，玩家看不出"技能在、只是我这会儿没牌可交"。
        #      它不是信息泄露：主公技在武将面公开，且这些座位本来就是它的合法发动者。
        lord_grant = (
            cross_seat
            and definition.skill_type is SkillType.LORD
            and state.player(seat).kingdom is not None
            and state.player(seat).kingdom is state.player(instance.owner).kingdom
        )
        if cross_seat and not (lord_grant or (asked_here and instance.skill_id in offered)):
            continue
        suppressed = (not cross_seat) and instance.skill_id not in resolved_ids
        short_id = instance.skill_id.rsplit(".", 1)[-1]
        name, skill_type, star = names.get(
            short_id,
            (None, getattr(definition.skill_type, "value", str(definition.skill_type)), False),
        )
        if name is None and definition.skill_id.startswith("equip."):
            # 装备技能没有**武将面**的名字：中文名取它所依附的**装备牌名**
            # （``equip.crossbow`` → 诸葛连弩）。装备与武将技能同列，界面据 ``carrier``
            # 分别标注（报障：装备技能此前整条被略去）。
            name = _card_type_name(state, definition.skill_id[len("equip.") :])
        if name is None:
            # **获得**来的技能没有**本人**武将面可查：``names`` 只索引持有者的武将牌
            # （短 id → 名字），而"获得别人的技能"（韬·刘备【义起】授予的 ☆【武圣】
            # /【咆哮】）的短 id 根本不在那张表里。此前这里直接 ``continue``，于是
            # 授予之后按钮区一条都不出现（武将详情页看得见，因为那边走的是
            # :func:`seat_skill_display` 的 ``_skill_display_name`` 反查）——用户实机
            # 报障：义起发动后没有【武圣】【咆哮】，两条都点不到。
            name = _skill_display_name(state, instance.skill_id)
        if name is None:
            continue
        entry = _skill_entry(
            definition,
            name,
            skill_type=skill_type,
            star=star,
            state="suppressed" if suppressed else "active",
        )
        ui = _skill_ui(definition, actions, hand, virtual)
        if suppressed:
            # 失效时没有任何入口：转化来源也不能画（否则按钮看起来还能点）。
            ui["convert_sources"] = []
        entry.update(
            {
                "available": False if suppressed else definition.skill_id in offered,
                # W4-2：这条技能是否已翻到预发动。界面据此**只**走清单入口（不再把骨架
                # 候选画成"发动技能 XXX"选项）；它是声明上的开关，不是第二条合法性判据。
                "preactivated": False if suppressed else bool(definition.preactivated),
                # 界面怎么摆这个技能：转化来源牌（【龙胆】）。**纯表现层派生**，只读候选与
                # 实例声明，不参与任何合法性判定。
                "ui": ui,
            }
        )
        if not cross_seat:
            appearance = _appearance_entry(state, seat, definition, suppressed=suppressed)
            if appearance is not None:
                entry["appearance"] = appearance
        out.append(entry)
    # U1-06：按钮区顺序 = 武将技能在前、装备技能在后；同组内沿用原来的
    # （锁定 / 被动靠后、id 稳定）键，故既有读取口不会因排序改动而漂。
    out.sort(key=lambda item: (item["carrier"] != "HERO", item["locked"], item["skill_id"]))
    return out


# ---------------------------------------------------------------------------
# 技能外观组件（shan-baozuoci 票 05）：声明面 = `SkillDef.appearance`，数据面 = 自见载荷
# ---------------------------------------------------------------------------


def _avatar_appearance(
    state: Any, owner: int, *, selectable: Iterable[str] | None = None
) -> dict[str, Any]:
    """**化身**组件的数据（票 05 §3）：第一行 = 手里每一张化身牌，第二行 = 该牌的技能 + 全文。

    数据全部来自**技能自己写好的状态**（:attr:`~sgs.state.Player.avatar_cards`）+ 数据表，
    这里只负责装成显示用的形状，不判断任何"能不能选"：

    - ``cards[*].skills`` 列出该牌**非 ☆** 的全部技能（☆ 觉醒前不在武将牌上，列出来是幽灵）；
    - ``eligible`` = 该技能**合格**（无标签 / 锁定）——不合格的照旧列出、由界面画灰
      （用户 2026-11-13 的 Q4，与【天成】的 ``disabled`` 同一姿态）；
    - ``current`` = 当前视为拥有的那条（``null`` = 还没选过 / 已失去）；
    - ``selectable`` = **本次询问**真正可点的技能集合（只在发动面上给）：它等于询问自己的
      选项闭集，故前端不必再猜"合格 ∩ 本次候选"。

    合格判据与引擎的候选面共用 :func:`sgs.skills.zuoci.avatar_skill_rows` 一处，前端零派生。
    """
    held = tuple(getattr(state.player(owner), "avatar_cards", ()) or ())
    current_skill = current_avatar_skill(state, owner)
    current_hero: str | None = None
    cards: list[dict[str, Any]] = []
    for hero_id in held:
        hero = hero_by_id(hero_id, pack_id=state.pack_id)
        if hero is None:  # pragma: no cover - 数据面缺牌（不可能：化身牌来自武将表）
            continue
        rows = [
            {
                "skill_id": skill_id,
                "name": skill_name_on(hero, skill_id),
                "text": skill_description(skill_id),
                "eligible": eligible,
                "selected": skill_id == current_skill,
            }
            for skill_id, eligible in avatar_skill_rows(hero_id, pack_id=state.pack_id)
        ]
        if any(row["selected"] for row in rows):
            current_hero = hero_id
        cards.append(
            {
                "hero_id": hero.id,
                "name": hero.name,
                "skills": rows,
            }
        )
    payload: dict[str, Any] = {
        "kind": AppearanceKind.AVATAR.value,
        "cards": cards,
        "current": (
            None
            if current_skill is None
            else {"hero_id": current_hero, "skill_id": current_skill}
        ),
    }
    if selectable is not None:
        payload["selectable"] = list(dict.fromkeys(str(item) for item in selectable))
    return payload




APPEARANCE_BUILDERS: Mapping[AppearanceKind, Any] = MappingProxyType(
    {AppearanceKind.AVATAR: _avatar_appearance}
)
"""外观组件种类 → 它的**数据构造器**（唯一登记处；票 05）。

签名 ``(state, owner, *, selectable=None) -> dict``：``state`` = ``GameState``、``owner`` =
技能持有者（**自见载荷**：只对本人构造）。加一个 :class:`~sgs.skills.AppearanceKind` 成员
必须同票在这里加一行，`tests/test_web.py` 按闭集核（漏登即红）。"""


def _appearance_entry(
    state: Any, seat: int, definition: Any, *, suppressed: bool
) -> dict[str, Any] | None:
    """技能按钮上的 ``appearance``（只读面）；``None`` = 这条技能不带这个键。

    三条都满足才给：声明了组件、**没失效**（失效时没有入口，与 ``ui.convert_sources``
    同一姿态）、且手里真有可显示的数据（用户 Q3：没有化身牌 ⇒ 不开弹窗，按钮也不出现
    ——判据在这里判一次，界面不另判）。
    """
    kind = getattr(definition, "appearance", None)
    if kind is None or suppressed:
        return None
    # 数据面读的是**这一座位**的库（化身牌住 `Player.avatar_cards`）⇒ 只对自己的实例挂。
    # 跨座位授予（主公技授予同势力座位）的技能此刻列在别的人身上，它的库不是我的；
    # 挂上去画出来的是"我的"库，等于把别人的技能画成我的外观组件（fail-closed 不给）。
    payload = _appearance_payload(state, seat, kind)
    if payload is None or not payload.get("cards"):
        return None
    return payload


def _appearance_payload(
    state: Any, owner: int, kind: AppearanceKind, *, selectable: Iterable[str] | None = None
) -> dict[str, Any] | None:
    """任意 kind 的数据构造口（``APPEARANCE_BUILDERS`` 的**唯一**读取面）；未登记 ⇒ ``None``。

    两处消费（只读面 :func:`_appearance_entry` / 发动面 :func:`pending_payload`）都走这里，
    "查表 + 缺登即无"只写一次。
    """
    builder = APPEARANCE_BUILDERS.get(kind)
    return None if builder is None else builder(state, owner, selectable=selectable)


def _appearance_kind_of_query(query: Query) -> AppearanceKind | None:
    """这条询问是不是某条**带外观组件**的技能发出来的（是 ⇒ 它的 kind）。

    判据只读**声明**（``query.skill_id`` → ``SkillDef.appearance``），不按选项值 / 技能 id
    名单猜——与 ``isShowHeroCardStep`` 读 ``hero_card`` 形状同一条纪律。``skill_id`` 带步骤
    后缀（① ``hero.zuoci.huashen:start``、② 流水线步 ``huashen:skill``），故走
    :func:`_definition_of` 的短 id 反查（它不需要状态，只查目录）。

    因此**不需要引擎**也能判"这条询问是不是自见面"：抹载荷（非持有者那一路）照样跑得动，
    只有**构造**数据时才要 ``state``（化身库住座位状态里，拿不到就 fail-closed）。
    """
    if not isinstance(query, SelectOptionQuery):
        return None
    skill_id = getattr(query, "skill_id", None)
    if not skill_id:
        return None
    definition = _definition_of(None, str(skill_id))
    return None if definition is None else getattr(definition, "appearance", None)


def _is_passive_skill(definition: Any) -> bool:
    """这条技能是不是**触发技（被动）**（W4-fix-3）：界面徽标据此写"被动技"。

    判据只看**声明面**的两件事：

    1. 有触发时机——``hooks``（时机钩子）或 ``seams``（帧内接缝）；
    2. **没有主动入口**——没有 :data:`~sgs.skills.ModifierPoint.CANDIDATES` 修饰器
       （引擎靠它把技能自己挂进候选，界面据此画"发动技能"）。

    刻意**不读** :attr:`SkillDef.skill_type`：那是 ``NORMAL`` / ``LORD`` 的身份门控闭集
    （主公技派生等别的东西都读它），不能借它表达"这一条是不是触发技"。【流离】是
    ``locked=False`` + 一个 ``USE_BECOME_TARGET`` 钩子的触发技，旧实现按
    "``skill_type == NORMAL`` ⇒ 主动技"一律标成主动（用户实机报障）。

    锁定技（``locked=True``）不在这里判：界面早已把锁定一律画成"锁定技 / 被动"。
    """
    if not (definition.hooks or definition.seams):
        return False
    return not any(
        modifier.point is ModifierPoint.CANDIDATES for modifier in definition.modifiers
    )


def _skill_ui(
    definition: Any,
    actions: tuple[Any, ...],
    hand: tuple[int, ...],
    virtual: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """一条技能的**界面提示**：它此刻要求玩家怎么点。

    ``convert_sources``：本技能此刻能转化为虚拟牌的**来源手牌 id**（【龙胆】的
    "一张【闪】当【杀】"）。转牌没有 ``ActivateSkill`` 候选，界面据这份来源清单
    让技能按钮点得动、并只高亮可转化的手牌。

    ``passive``（W4-fix-3）：这条技能是**触发技**（见 :func:`_is_passive_skill`）。
    界面徽标读它写"被动技"——判据住在这里而不是 ``SkillDef.skill_type`` 里，因为后者
    是身份门控的闭集、还要服务别的派生。

    W4-2 删掉了 ``manual`` / ``pick_cards`` / ``pick_seats``：A 族六条主动技全部走
    **预发动清单**，候选恒为"是否发动"的裸骨架（不再带 ``picks`` / ``choices``），故
    "多选手牌 → 点一位在场角色"的手选模式**没有生产者**（票 05 起就只剩遗留路径）。
    预发动清单本身由询问字段下发（``preactivations`` / ``preactivation``），不在这里。
    """
    del actions, hand  # 手选模式删除后，界面提示只剩 requirement 与转化来源
    skill_id = definition.skill_id
    return {
        "requirement": definition.requirement or "",
        "passive": _is_passive_skill(definition),
        "convert_sources": sorted(
            int(source)
            for source, aliases in virtual.items()
            if any(skill_id in alias["skill_ids"] for alias in aliases)
        ),
    }


def _query_summary(query: Query) -> dict[str, Any]:
    """询问的**公开摘要**：不带视角、不带候选（候选只随"当前挂起"给）。"""
    return {
        "seq": query.seq,
        "actor": query.actor,
        "kind": query.kind,
        "timing": query.timing.value,
        "prompt": query.prompt,
    }


# ---------------------------------------------------------------------------
# 询问窗口的"谁对谁做了什么"
# ---------------------------------------------------------------------------


def _seat_name(state: Any, seat: int) -> str | None:
    """座位上的武将中文名（没发武将 / 未知武将 ⇒ ``None``，不编名字）。"""
    hero_id = state.player(seat).hero
    if hero_id:
        hero = hero_by_id(hero_id, pack_id=state.pack_id)
        if hero is not None and hero.name:
            return hero.name
    return None


def _seat_label(state: Any, seat: int) -> str:
    """座位的中文人话："0号位郭嘉"（没发武将时退化成"0号位"）。"""
    name = _seat_name(state, seat)
    return f"{seat}号位{name}" if name else f"{seat}号位"


def _card_type_name(state: Any, type_id: str) -> str | None:
    """牌名 id → 中文牌名（装备技能的 ``skill_id`` 是 ``equip.<牌名 id>``）。"""
    for card_type in load_content_pack(state.pack_id).card_types:
        if card_type.id == type_id:
            return card_type.name
    return None


def _skill_display_name(state: Any, skill_id: str) -> str | None:
    """技能 id → 中文技能名。

    两种形态都要认：命名空间 id（``hero.guojia.yiji``）与**合成键**
    （``yiji:give:0:0``——用冒号把点号/轮号拼进缓存键）。前者带武将 id、可直接定位；
    后者只留短 id，按短 id 在**全部**武将技能里查。``equip.<牌名 id>`` 取牌名。

    **合成定义**（「标签自身效果」这类不在武将表技能清单里的定义，命名 ``<来源>_mark``）
    按**来源技能**的中文名取：``hero.feng_yujin.dingluan_mark`` ⇒ 「定乱」、
    ``hero.shen_ganning.jieying_mark`` ⇒ 「劫营」（用户 2026-11-07 报障：原先退回整串 id）。
    """
    head = skill_id.split(":", 1)[0]
    if head.startswith("equip."):
        return _card_type_name(state, head[len("equip.") :])
    parts = head.split(".")
    short = parts[-1]
    heroes = load_hero_tables(state.pack_id)
    if len(parts) >= 3 and parts[0] == "hero":
        hero = hero_by_id(parts[1], pack_id=state.pack_id)
        pool = (hero,) if hero is not None else ()
    else:
        pool = tuple(hero for table in heroes for hero in table.heroes)

    def lookup(name: str) -> str | None:
        for hero in pool:
            if hero is None:
                continue
            for skill in hero.skills:
                if skill.id == name:
                    return skill.name
        return None

    name = lookup(short)
    if name is None and short.endswith("_mark"):
        name = lookup(short[: -len("_mark")])
    return name


def _frame_targets(data: dict[str, Any]) -> list[int]:
    """帧载荷里的**当前目标**：``targets[cursor]``；多目标结算逐目标取一个。"""
    targets = tuple(data.get("targets", ()))
    if not targets:
        return []
    cursor = data.get("cursor")
    if isinstance(cursor, int) and 0 <= cursor < len(targets):
        return [int(targets[cursor])]
    return [int(seat) for seat in targets]


def _context_targets(query: Query, data: dict[str, Any]) -> list[int]:
    """这次询问针对的座位：询问自带的 ``target`` / ``dying`` 优先，否则读栈顶帧的当前目标。

    "当前目标"用 ``RESOLVE`` 帧的 ``cursor`` 取——多目标锦囊逐目标结算时，
    询问窗口的人话应当是"对**这个**目标"，而不是把全部目标列一遍。
    """
    target = getattr(query, "target", None)
    if target is not None:
        return [int(target)]
    dying = getattr(query, "dying", None)
    if dying is not None:
        return [int(dying)]
    return _frame_targets(data)


def _nullified_frame(engine: Any, data: dict[str, Any]) -> dict[str, Any]:
    """顺着帧私有 ``cancel_target`` 找到**被无懈的那一层**帧载荷。

    无懈自身的 ``RESOLVE`` 帧 ``targets = ()``，"当前目标"退回 ``source``（= 无懈使用者），
    于是嵌套无懈窗口会写成"1号位对1号位使用无懈可击"（看起来像自己无懈自己）。
    无懈的载荷里记着它指向哪一层的哪个目标（``cancel_target``），故这里原地回溯——
    嵌套多层的无懈会一路跳到最外层那张**牌**。找不到父帧时返回原载荷（fail-soft，不编话）。
    """
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    seen: set[int] = set()
    while getattr(data.get("card"), "type", None) == "nullify":
        cancel = data.get("cancel_target")
        if not isinstance(cancel, tuple) or len(cancel) != 2:
            return data
        parent_id = cancel[0]
        if parent_id in seen:
            return data
        seen.add(parent_id)
        parent = next((frame for frame in frames if frame.frame_id == parent_id), None)
        if parent is None:
            return data
        data = parent.data
    return data


def _distribution_payload(engine: Any, query: Query | None) -> dict[str, Any] | None:
    """``DISTRIBUTE`` 询问的**展示事实**：每个可分配座位的人话标签 + 两个上限。

    字段：``seats`` = ``[{"seat", "name", "label", "max"}]``（座位升序，与候选枚举同序）、
    ``max_total`` / ``per_seat_max``。上限本身也在询问模型里（同一份真源），这里重复下发
    只为让界面读一处；非 ``DISTRIBUTE`` 的询问返回 ``None``（键恒在，读取方契约）。
    """
    if query is None or getattr(query, "kind", None) != QueryKind.DISTRIBUTE.value:
        return None
    state = engine.state
    per_seat = int(getattr(query, "per_seat_max", 3))
    seats = tuple(getattr(query, "seats", ()) or ())
    return {
        "max_total": int(getattr(query, "max_total", 3)),
        "per_seat_max": per_seat,
        "seats": [
            {
                "seat": int(seat),
                "name": _seat_name(state, int(seat)),
                "label": _seat_label(state, int(seat)),
                "max": per_seat,
            }
            for seat in seats
        ],
    }


def option_seats(query: Query) -> dict[str, int]:
    """``SELECT_OPTION`` 里形如 ``seat:N`` 的选项 → 座位；其余询问 / 选项一律空 dict。

    界面据此把座位画成**可点的**（【遗计】的"交给谁"），省得玩家去读 ``选项：seat:3``
    这种报文串。解析放在服务端、前端只读数据——"界面不裁决规则"这条纪律因此不受影响。
    前缀读 :data:`sgs.skills.SEAT_PREFIX`（W6-06b：接入层的单一真源，不在这里抄字面量）。
    """
    options = getattr(query, "options", None)
    if options is None:
        return {}
    out: dict[str, int] = {}
    for option in options:
        if isinstance(option, str) and option.startswith(SEAT_PREFIX):
            try:
                out[option] = int(option.removeprefix(SEAT_PREFIX))
            except ValueError:
                continue
    return out


def option_source(engine: Any, query: Query | None) -> dict[str, Any] | None:
    """``SELECT_OPTION`` 的"这项令谁做"的**展示事实**（M5-bugfix：雌雄双股剑）。

    一次性选项步（当前唯一生产者 = ``equip.double_sword``）的应答者是**目标**，而
    「令……摸一张牌」里的"……"是**这次使用的来源**（雌雄的持有者）。前端不能按"谁的回合 /
    谁用了【杀】"去推（那是第二份规则），故由投影从结算栈顶帧的 ``source`` 派生：
    ``{"seat": N, "name": <武将名或 None>}``。

    只给**装备技能的** ``SELECT_OPTION``（``skill_id`` 以 ``equip.`` 开头）——其余
    ``SELECT_OPTION``（花色 / 遗计的交给谁）的"来源"没有这项展示语义。取不到引擎
    （409 里的裸 ``Query``）或栈顶帧没有 ``source`` ⇒ ``None``（界面退回不带来源的旧文案）。

    纯派生、只读公开事实：不进记录流，不参与合法性判定。
    """
    if query is None or str(getattr(query, "kind", "")) != "SELECT_OPTION":
        return None
    skill_id = getattr(query, "skill_id", None)
    if not isinstance(skill_id, str) or not skill_id.startswith("equip."):
        return None
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    data: dict[str, Any] = frames[-1].data if frames else {}
    source = data.get("source")
    if source is None:
        return None
    seat = int(source)
    return {"seat": seat, "name": _seat_name(engine.state, seat)}


def _event_context(engine: Any, query: Query | None) -> dict[str, Any] | None:
    """这次询问**正在发生什么**的人话："0号位郭嘉对0号位郭嘉使用遗计"。

    数据来源只有**公开事实**：询问本身（``kind`` / ``skill_id`` / ``target`` / ``dying`` /
    ``revealed``）与结算栈顶帧的载荷（``source`` / ``card`` / ``targets`` / ``cursor`` /
    ``running_skill``）。它是**表现层**的派生，不参与
    任何合法性判定，也不新增真源——帧与询问都是引擎已经下发的对象。

    两种询问分叉：

    - **技能询问**（带 ``skill_id``）没有"使用的牌"：名字来自技能面，作用者是技能持有者
      （= 询问的 ``actor``），目标也是他自己（【遗计】这类"对自己生效"的直读）。
      装备技能例外：作用者 / 目标是那次**使用**的（读栈顶帧）。
    - **观看型选牌**（``SELECT_CARDS`` 且询问带了 ``revealed`` 授予，如【遗计】选哪张）：
      名字取栈顶帧的 **``running_skill``**（谁在跑这个回调），而不是造成伤害的那张牌。
    - **牌询问**：名字来自栈顶帧的牌面，作用者是帧的 ``source``，目标是当前目标。

    濒死求桃没有"使用的牌"，退化成"谁对谁造成了伤害"。都取不到时返回 ``None``
    （界面据此不画这一行，而不是编一句假话）。
    """
    if query is None:
        return None
    state = engine.state
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    data: dict[str, Any] = frames[-1].data if frames else {}

    skill_id = getattr(query, "skill_id", None)
    if skill_id is None and getattr(query, "revealed", ()):
        # 观看型选牌：牌面是**当前回调**授予的，名字该读"谁在跑"（【遗计】），
        # 而不是造成伤害的那张牌（那会写成"对郭嘉使用杀"）。
        skill_id = data.get("running_skill")
    if skill_id is not None:
        skill_id = str(skill_id)
        name = _skill_display_name(state, skill_id)
        if skill_id.startswith("equip."):
            source = data.get("source", query.actor)
            targets = _context_targets(query, data)
        else:
            source = query.actor
            targets = []
        if not targets:
            targets = [int(query.actor)]
    else:
        # 无懈窗口的"目标"是**被无懈的那张牌**（报障：不能写成"自己"）。
        # 顶层帧若本身就是一张【无懈可击】，它的 targets 为空、"当前目标"退回使用者，
        # 直读会写成"1号位对1号位使用无懈可击"；顺着 cancel_target 回溯到被无懈的牌。
        if (
            getattr(query, "cause", None) is WindowCause.NULLIFY
            and getattr(data.get("card"), "type", None) == "nullify"
        ):
            origin = _nullified_frame(engine, data)
            origin_card = origin.get("card")
            origin_name = getattr(origin_card, "name", None)
            origin_source = origin.get("source")
            origin_targets = _frame_targets(origin)
            if origin_name is not None and origin_source is not None:
                nullifier = data.get("source")
                head = (
                    f"{_seat_label(state, int(nullifier))}使用【无懈可击】"
                    if nullifier is not None
                    else "使用【无懈可击】"
                )
                source_label = _seat_label(state, int(origin_source))
                if origin_targets:
                    target_labels = "、".join(_seat_label(state, seat) for seat in origin_targets)
                    held = f"{source_label}对{target_labels}使用的【{origin_name}】"
                else:
                    held = f"{source_label}使用的【{origin_name}】"
                return {
                    "actor": int(nullifier) if nullifier is not None else int(origin_source),
                    "name": "无懈可击",
                    "targets": [int(seat) for seat in origin_targets],
                    "label": f"{head}（抵消 {held}）",
                }
        name = getattr(data.get("card"), "name", None)
        source = data.get("source", query.actor)
        targets = _context_targets(query, data)

    if name is None:
        # 濒死求桃这类"没有使用的牌"的窗口：退化成"谁对谁造成了伤害"。
        damage_source = data.get("damage_source")
        if damage_source is not None and getattr(query, "dying", None) is not None:
            targets = [int(query.dying)]
            return {
                "actor": int(damage_source),
                "name": None,
                "targets": targets,
                "label": (
                    f"{_seat_label(state, int(damage_source))}"
                    f"对{_seat_label(state, int(query.dying))}造成伤害"
                ),
            }
        return None
    if source is None:
        # 判定路径的 ``RESOLVE`` 帧**没有使用者**（``source=None``，由 ``_phase_judge`` 压出），
        # 但"被判定者"就在 ``targets`` 里。不点名他，界面只画一条通用提示——玩家看见处理区
        # 一张【乐不思蜀】和另一名角色判定区里的另一张，就会以为在问**别人的**牌。
        if targets and data.get("use_timing") is Timing.PHASE_JUDGE:
            who = "、".join(_seat_label(state, int(seat)) for seat in targets)
            return {
                "actor": int(targets[0]),
                "name": name,
                "targets": [int(seat) for seat in targets],
                "label": f"{who}判定{name}（判定开始前）",
            }
        return None

    source_label = _seat_label(state, int(source))
    target_labels = [_seat_label(state, seat) for seat in targets]
    label = (
        f"{source_label}对{'、'.join(target_labels)}使用{name}"
        if target_labels
        else f"{source_label}使用{name}"
    )
    return {
        "actor": int(source),
        "name": name,
        "targets": [int(seat) for seat in targets],
        "label": label,
    }


# ---------------------------------------------------------------------------
# "需要做什么"（item 4）：技能文案取自 SkillDef.requirement；卡牌窗口走表现层表
# ---------------------------------------------------------------------------


def _definition_of(state: Any, skill_id: str):
    """技能 id → :class:`~sgs.skills.catalog.SkillDef`；合成键（``yiji:give:0:0``）走短 id。"""
    head = skill_id.split(":", 1)[0]
    definition = CATALOG.get(head)
    if definition is not None:
        return definition
    short = head.rsplit(".", 1)[-1]
    for candidate in CATALOG.values():
        if candidate.skill_id.rsplit(".", 1)[-1] == short:
            return candidate
    return None


def _skill_of_query(engine: Any, query: Query, data: dict[str, Any]) -> str | None:
    """这次询问归**哪条技能**（按可靠度依次）：询问自带 id → 帧 ``running_skill`` →
    ``SKILL`` 帧的 ``skill_id``（主动技顺序选牌）→ **阶段体**里正在跑的技能（【观星】）。

    都没有 ⇒ ``None``（这是一条纯卡牌窗口，文案走表现层表）。
    """
    skill_id = getattr(query, "skill_id", None)
    if skill_id:
        return str(skill_id)
    if data.get("running_skill"):
        return str(data["running_skill"])
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    if frames and getattr(frames[-1], "kind", None) is FrameKind.SKILL and data.get("skill_id"):
        return str(data["skill_id"])
    phase_body = getattr(engine, "phase_body_skill", None)
    if callable(phase_body):
        running = phase_body()
        if running:
            return str(running)
    return None


def _need_text(verb: str, card_name: str, need: int) -> str:
    """"需要打出一张 X / 连续两张 X"的人话；数量 > 1 时补"依次、打一张再问一次"。"""
    if need > 1:
        done = "打完" if verb == "打出" else f"{verb}完"
        return f"需要{verb}{need}张{card_name}（依次{verb}两张，每{done}一张再询问一次）"
    return f"需要{verb}{card_name}"


def _count_needed(engine: Any, query: Query, data: dict[str, Any]) -> int:
    """本窗口需要几张：唯一真源 :func:`sgs.effects.response_count`（【无双】= 2）。

    ``role``（票 23）与引擎侧同一口径：**应答者就是这张牌指定的目标**时标 ``TARGET``。
    【无双】②（【决斗】）只给"被决斗的一方"加张数，故这一位不能省——省掉会把"吕布被
    【决斗】"那一半的界面文档写成"需要打出【杀】"，与结算（两张）不一致。
    """
    source = data.get("source")
    if source is None:
        return 1
    # 两个座位事实与引擎同形（票 23）：``target`` = 这张牌**指定的目标**（载荷的
    # ``targets[0]``），``role`` = 这一问的响应者（询问的 ``target``，缺省问的人）是不是
    # 那个目标。【无双】②改的是"吕布的对手"每轮的张数，故界面必须和结算读同一对事实，
    # 否则"吕布被【决斗】"时文案会写成吕布要两张。
    responder = getattr(query, "target", None)
    if responder is None:
        responder = getattr(query, "actor", None)
    targets = tuple(int(seat) for seat in (data.get("targets") or ()))
    designated = targets[0] if targets else None
    return int(
        response_count(
            engine.state,
            source=source,
            card=data.get("card"),
            target=designated,
            role=responder_role(responder, designated),
        )
    )


def _rejudge_requirement(engine: Any, query: Query) -> str:
    """改判窗口的"需要做什么"：读**应答座位**当前活着的改判技能定义（不写死一句）。

    标准【鬼才】"用一张**手牌**"（§九 L958）、界【鬼才】"用**一张牌**"（§十九 L2037）、
    张角【鬼道】"用一张**黑色**牌替换"（feng-zhangjiao 票 02）
    ——两版文案不同，而界面按"本窗口文案 == 该技能声明的 ``requirement``"认领那个只说明
    入口（`tools/sgs_web/static/table.js:rejudgeWindowEntry`），写死会让界版对不上。
    找不到定义（构造局面 / 事件外的预览）时退回标准版文案，与 M5 起的既有字符串逐字相同。
    """
    fallback = "用一张手牌，代替此判定牌"
    actor = int(getattr(query, "actor", -1))
    for instance in resolve_instances(engine.state):
        if instance.owner == actor and instance.definition.rejudge_capable:
            return instance.definition.requirement or fallback
    return fallback


def _card_window_requirement(engine: Any, query: Query, data: dict[str, Any]) -> str | None:
    """**不带技能归属**的窗口的"需要做什么"（表现层表；不新增真源）。"""
    kind = str(getattr(query, "kind", ""))
    if kind == "PLAY_PHASE":
        return "使用一张牌（或发动技能），或结束出牌阶段"
    if kind == "DISCARD":
        return f"弃置 {getattr(query, 'count', 0)} 张手牌"
    if kind == "DODGE":
        return _need_text("使用", "【闪】", _count_needed(engine, query, data))
    if kind == "PEACH":
        dying = getattr(query, "dying", None)
        return f"使用【桃】救回 {dying} 号位" if dying is not None else "使用【桃】"
    if kind == "ARRANGE":
        return "排列这些牌：给出顺序与前几张置于牌堆顶"
    if kind == "SELECT_CARDS":
        card_type = getattr(data.get("card"), "type", None)
        if card_type in ("dismantlement", "snatch"):
            return "选择目标区域里的一张牌"
        if card_type == "harvest":
            return HARVEST_REQUIREMENT
        if card_type == "fire_attack":
            # M5-08a：火攻的两步各有自己的问法（亮牌是公开动作；弃牌可选）。
            if data.get("revealed") is not None:
                return "弃一张与亮出的牌同花色的手牌，造成 1 点火焰伤害，或放弃"
            return "从自己的手牌里亮出一张（公开）"
        return "从选择面里选择要交出的牌"
    if kind == "RESPOND":
        cause = getattr(query, "cause", None)
        if cause is WindowCause.NULLIFY:
            return "需要使用【无懈可击】"
        if cause is WindowCause.DUEL:
            return _need_text("打出", "【杀】", _count_needed(engine, query, data))
        if cause is WindowCause.BARBARIAN_INVASION:
            return "需要打出【杀】，否则受到使用者 1 点伤害"
        if cause is WindowCause.ARROW_BARRAGE:
            return "需要打出【闪】，否则受到使用者 1 点伤害"
        if cause is WindowCause.BORROWED_SWORD:
            # W5-02b：窗口的目标**固定为使用者指定的 Bn**。主源 = 结算帧的
            # ``designations``（写入面：``UseCard.designations`` 随 USE → RESOLVE 继承）；
            # 候选带 ``targets`` 只作回退（409 附带的裸 Query 拿不到栈）。转化候选已从
            # ``legal_actions`` 收回，若只读候选，持刀者只有【龙胆】等 a 时 Bn 会写不出来。
            fixed = sorted({int(seat) for seat in (data.get("designations") or ())})
            if not fixed:
                fixed = sorted(
                    {
                        int(target)
                        for action in query.legal_actions
                        for target in (getattr(action, "targets", None) or ())
                    }
                )
            if len(fixed) == 1:
                return f"对 {fixed[0]} 号位使用【杀】，或交出武器"
            return "对合法目标使用【杀】，或交出武器"
        if cause is WindowCause.LORD_REQUEST:
            required = getattr(query, "required", None)
            card = {"slash": "【杀】", "dodge": "【闪】"}.get(required, "一张牌")
            return f"需要打出{card}"
        if cause is WindowCause.REJUDGE:
            return _rejudge_requirement(engine, query)
        if cause is WindowCause.LUANWU:
            # 贾诩移动版【乱武】（lin-batch2 票 03）：选项 1 = 窗口里的「使用【杀】」候选，
            # 选项 2 = `Pass`（按钮文案由 `pending.js:alternativeLabel` 写成「失去1点体力」）。
            # 文案 = 牌面逐字（移动版官网「除非…否则失去1点体力」的同一句话，票面 §2 询问剧本）；
            # 与借刀的「对 Bn 使用【杀】，或交出武器」同一个分支位，只是不写具体座位。
            return "对距离最近的另一名角色使用【杀】，或失去1点体力"
    return None




def _skill_hints(state: Any, query: Query) -> list[dict[str, str]]:
    """这条窗口里**被提供**的技能（主动技 / 提供者 / 转化技）及其文案。

    来源只有**候选与选择面槽位**里的公开事实：``ActivateSkill`` 候选自带 ``skill_id``；
    转化技的候选是虚拟牌（不带技能归属），故按
    :func:`~sgs.skills.heroes.conversion_offers` 的**来源**逐张对回技能（票 14），
    不在声明表里的虚拟牌退回 :attr:`SkillDef.virtual_type_ids` 的牌名匹配。
    """
    actor = int(query.actor)
    hints: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(skill_id: str) -> None:
        definition = _definition_of(state, skill_id.split(":", 1)[0])
        if definition is None or not definition.requirement or definition.skill_id in seen:
            return
        seen.add(definition.skill_id)
        hints.append(
            {
                "skill_id": definition.skill_id,
                "name": _skill_display_name(state, definition.skill_id) or definition.skill_id,
                "requirement": definition.requirement,
            }
        )

    for action in query.legal_actions:
        if getattr(action, "kind", None) == "ActivateSkill":
            add(str(action.skill_id))
    # W4-fix-2：转化技的候选已从 ``legal_actions`` 收回，技能文案改由**清单**的键给出
    # （与"可用性 = 清单"同一真源；不再靠候选里的虚拟牌反推）。
    for skill_id in getattr(query, "transform_preactivations", None) or {}:
        add(str(skill_id))
    # 收**候选与选择面槽位**两处的虚拟牌（票 14）："提供一张 B"的虚拟 B 只在后者上，
    # 不收它这条窗口就列不出【龙胆】/【武圣】的文案。归属按
    # :func:`~sgs.skills.heroes.conversion_offers` **逐张核对来源**（需要【杀】只归【闪】、
    # 需要【闪】只归【杀】）；不在声明表里的虚拟牌（【八卦阵】"视为使用【闪】"等）
    # 退回按牌名匹配。
    for card in _virtual_cards(query):
        if not card.is_virtual:
            continue
        source = int(card.virtual_sources[0]) if len(card.virtual_sources) == 1 else None
        attributed = (
            set(conversion_offers_by_source(state, actor, card.type).get(source, ()))
            if source is not None
            else set()
        )
        if attributed:
            for skill_id in attributed:
                add(skill_id)
            continue
        for instance in resolve_instances(state):
            if instance.owner != actor:
                continue
            definition = instance.definition
            if definition.virtual_type_ids and card.type in definition.virtual_type_ids:
                add(definition.skill_id)
    return hints


def _step_wording(steps: Any, step: str) -> str | None:
    """步骤名 → 逐步文案；名字里的**序号段**（末尾的 ``:<数字>``，可多段）先剥掉再查。

    执行器给"同一步问多次"补序号只为答案缓存不撞车（``seat:0`` / ``seat:1``，见
    :func:`~sgs.skills.flow._flow_option_id`），文案仍归那一步。【遗计】的"给谁"步更进一层：
    它的 ``skill_id`` 是手写的 ``yiji:give:<点号>:<轮号>``（票 05 的逐轮缓存键，两个数字段
    都必须留 —— 否则同一伤害点的第 2 轮会读回第 1 轮的答案）。故按**循环**剥末尾的数字段，
    单段（``seat:0``）与多段（``give:0:1``）走同一条判据。
    """
    found = steps.get(step)
    while found is None:
        stem, colon, index = step.rpartition(":")
        if not colon or not index.isdigit():
            break
        step = stem
        found = steps.get(step)
    return found


def _step_requirement(definition: Any, query: Query) -> str | None:
    """**多步技能**逐步的"需要做什么"（票 01）；取不到 ⇒ ``None``（退回一句话的文案）。

    键的取法只读询问里已有的标识，**不动询问协议**（故 Golden Replay 一行不改）：

    - ``SELECT_OPTION``：询问自带 ``skill_id`` 形如 ``lijian:user`` ⇒ 键 = 冒号后那段
      （末尾的序号段先剥掉，见 :func:`_step_wording`）；
    - ``SELECT_CARDS``：技能的那一步选牌 ⇒ 键 = ``"card"``。这一问归**哪条技能**已由
      :func:`_skill_of_query` 判过（询问 ``skill_id`` → 帧 ``running_skill`` → ``SKILL``
      帧 → 阶段体，与 ``SELECT_OPTION`` **同一条判据**），传进来的 ``definition`` 就是它，
      故这里**不再另读帧键**。

    旧判据读 ``frame.data["skill_id"]`` 并要求它等于 ``definition.skill_id``；而**被动帧
    （DAMAGE / USE）从不写这个键**——全仓只有 SKILL 帧与 JUDGE 帧写它，派发环写的是
    ``running_skill``。于是【流离】成本 / 【青龙】/【贯石斧】/【麒麟弓】/【寒冰剑】/
    【雌雄】/【遗计】的逐步文案在生产路径上**全部退回一句话 summary**（票 12 §1）。
    真会话回归见 ``tests/test_web.py::test_a_real_session_reaches_the_yiji_card_step_wording``。
    """
    steps = getattr(definition, "step_requirements", None)
    if not steps:
        return None
    raw = getattr(query, "skill_id", None)
    if raw:
        _head, sep, step = str(raw).partition(":")
        if sep:
            return _step_wording(steps, step)
    if getattr(query, "kind", None) == "SELECT_CARDS":
        return steps.get("card")
    return None


def _fire_attack_step(engine: Any, query: Query) -> dict[str, Any] | None:
    """【火攻】的两步选牌（M5-08a 的界面读取方，纯展示派生）。

    引擎侧（M5-06）把亮牌与同花色弃牌都表达成普通的 ``SELECT_CARDS``；界面要把它们
    分别走**亮牌模板**与**牌桌点手牌**，故这里只读结算栈顶帧 + 询问本身，派生出：

    - ``{"step": "reveal"}``：目标选**自己**的 1 张手牌亮出（``reveal_hand=True``）；
    - ``{"step": "discard", "revealed": CardRef, "target": seat}``：使用者看到亮出的那一张，
      可弃一张同花色手牌（或放弃）。

    不是火攻的选牌步 / 帧已不在 / 应答者不是这一步该问的人 ⇒ ``None``（fail-closed）。
    只读公开事实（帧载荷 + 询问），不裁决规则、不改记录流。
    """
    if str(getattr(query, "kind", "")) != "SELECT_CARDS":
        return None
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    if not frames:
        return None
    data = frames[-1].data
    if getattr(data.get("card"), "type", None) != "fire_attack":
        return None
    spec = getattr(query, "spec", None)
    zones = tuple(getattr(spec, "zones", ()) or ())
    if zones != (Zone.HAND,):
        return None
    targets = tuple(data.get("targets", ()) or ())
    cursor = int(data.get("cursor", 0) or 0)
    if not 0 <= cursor < len(targets):
        return None
    target = int(targets[cursor])
    if data.get("revealed") is None:
        if int(query.actor) != target:
            return None
        return {"step": "reveal", "target": target}
    if int(query.actor) != int(data.get("source", -1)):
        return None
    return {
        "step": "discard",
        "target": target,
        "revealed": engine.state.card(int(data["revealed"])),
    }


def _own_hand_face(query: Query) -> bool:
    """这一步的选择面是不是**应答者自己的手牌**（每一槽都是 ``ref.seat == actor`` 的手牌）。

    票 09 / spec §1 #12：自己的手牌对本人**天然可见**（``view.self_hand`` 已有同一批牌面），
    所以它不是"观看授予"（``revealed`` 那一档的语义 = 发给应答者的**额外**牌面）。投影原先在
    【火攻】亮牌步把己方手牌槽位抄进 ``payload["revealed"]`` ⇒ ``dialog="reveal"`` ⇒
    前端 ``modal.js:isRevealStep`` 短路 ``candidates.js:isOwnZonePick`` ⇒ 亮牌弹窗。

    判据与前端 ``candidates.js:isOwnZonePick`` 的**形状**同源（`SELECT_CARDS` ∧ 槽位全在
    应答者自己的手牌区）：引擎的真实槽位可见性和前端判据必须是同一份事实，不是两份。
    空槽位 / 取不到应答者 ⇒ ``False``（fail-open 到既有的「授予」路径，不因守卫吞掉别的面）。
    """
    slots = tuple(getattr(query, "slots", ()) or ())
    if not slots or getattr(query, "actor", None) is None:
        return False
    actor = int(query.actor)
    for slot in slots:
        ref = getattr(slot, "ref", None)
        if ref is None or getattr(ref, "seat", None) is None:
            return False
        if int(ref.seat) != actor or ref.zone is not Zone.HAND:
            return False
    return True


def query_context(engine: Any, query: Query | None) -> dict[str, Any] | None:
    """这次询问的两行人话：**正在发生什么**（``label``）+ **需要做什么**（``requirement``）。

    ``requirement`` 的落点分三档：

    1. 询问能归属到技能（``skill_id`` / 帧 ``running_skill`` / ``SKILL`` 帧 / 阶段体）⇒
       读该技能定义上写死的 :attr:`~sgs.skills.catalog.SkillDef.requirement`（与技能代码同处）；
    2. 否则走**表现层**的卡牌窗口表（张数读唯一真源 :func:`sgs.effects.response_count`）；
    3. ``skill_hints`` 另列这条窗口里**被提供**的技能（主动技 / 提供者 / 转化技）的文案。

    纯派生、只读公开事实；不参与合法性判定。两个都取不到时返回 ``None``（界面不画）。
    """
    if query is None:
        return None
    state = engine.state
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    data: dict[str, Any] = frames[-1].data if frames else {}

    base = _event_context(engine, query)
    skill_id = _skill_of_query(engine, query, data)
    definition = _definition_of(state, skill_id) if skill_id is not None else None
    requirement = definition.requirement if definition is not None else None
    if definition is not None:
        # 分步技能：这一步的问法优先于一句话的 summary（票 01）。
        requirement = _step_requirement(definition, query) or requirement
    if requirement is None:
        requirement = _card_window_requirement(engine, query, data)
    hints = _skill_hints(state, query)
    if definition is not None:
        hints = [hint for hint in hints if hint["skill_id"] != definition.skill_id]

    if base is None and requirement is None and not hints:
        return None
    if base is None:
        base = {"actor": int(query.actor), "name": None, "targets": [], "label": None}
    base["requirement"] = requirement
    base["skill_hints"] = hints
    return base


def virtual_index(query: Query, state: Any | None = None) -> dict[str, list[dict[str, Any]]]:
    """转化候选的**来源牌索引**：``"<实体来源牌 id>": [{"card_id": <虚拟牌 id>, "skill_ids": [...]}]``。

    【龙胆】这类转化技的候选里带的 ``card`` 是**虚拟牌**（``card_id < 0``），而界面手牌区
    渲染的是**实体牌**（``card_id > 0``）。不建这份索引，玩家点【闪】时界面按实体 id
    查不到候选，【闪】就被画成"用不出"（报障 4 的原样）。

    只收**单来源**转化（源头只有一张实体牌）：这是标准版【武圣】【龙胆】【国色】【急救】
    的形状；多来源（【丈八蛇矛】的两张手牌当【杀】）需要"先选几张"的第二维，留给它自己
    的界面改造，不在本轮射程。

    **``skill_ids`` 按声明的源谓词逐张核对**（票 14）：旧实现按 ``SkillDef.virtual_type_ids``
    的**牌名并集**归属，而【龙胆】两个方向共用一个技能 id（``("dodge", "slash")``）⇒ 两个
    方向分不开、"点龙胆后所有手牌都亮"（用户实机）。现在读
    :func:`~sgs.skills.heroes.conversion_offers`（目标牌名 + 该声明的区域 / 牌面 / 时机
    谓词）：需要【杀】时只归【闪】、需要【闪】时只归【杀】。拿不到引擎（409 里附带的裸
    ``Query``）时退回牌名匹配——谓词需要 ``state``。

    ``SELECT_CARDS`` 的**"提供一张 B"面**（票 14）没有 ``action.card``，虚拟 B 全在
    ``query.slots`` 里，故两个来源都要收：缺了后者，【青龙】追问时【龙胆】/【武圣】的
    技能按钮就点不亮（"待答期间技能栏仍可点"这条验收会红）。
    """
    return _virtual_index(query, state, owner=int(query.actor))


def _virtual_index(
    query: Query, state: Any | None, *, owner: int
) -> dict[str, list[dict[str, Any]]]:
    """``virtual_index`` 的实现：``owner`` 显式传入（技能清单按**查看者**算，不是询问者）。"""
    typed: list[tuple[str, frozenset[str]]] = []
    if state is not None:
        for instance in resolve_instances(state):
            if instance.owner != owner:
                continue
            definition = instance.definition
            if definition.virtual_type_ids:
                typed.append((definition.skill_id, frozenset(definition.virtual_type_ids)))
    # 牌名 → {来源实体牌 id: 技能 id 元组}；按需现算（一次询问里通常只有一两种牌名）。
    offers: dict[str, dict[int, tuple[str, ...]]] = {}

    def offerers(card: Any) -> list[str]:
        if state is None:
            return [skill_id for skill_id, types in typed if card.type in types]
        table = offers.get(card.type)
        if table is None:
            table = conversion_offers_by_source(state, owner, card.type)
            offers[card.type] = table
        return list(table.get(int(card.virtual_sources[0]), ()))

    out: dict[str, list[dict[str, Any]]] = {}
    for card in _virtual_cards(query):
        if not card.is_virtual or len(card.virtual_sources) != 1:
            continue
        bucket = out.setdefault(str(int(card.virtual_sources[0])), [])
        if not any(item["card_id"] == card.card_id for item in bucket):
            bucket.append({"card_id": card.card_id, "skill_ids": offerers(card)})
    return out


def _virtual_cards(query: Query) -> list[Any]:
    """这次询问里的全部牌面：候选带的 ``card`` 与**选择面槽位**上的牌（票 14）。

    "提供一张 B"的虚拟 B 只出现在后者的 ``slots[].card`` 上（``SELECT_CARDS`` 的候选
    只带面内位置），故两个来源都要读——顺序固定（候选在前、槽位在后）以保证可复现。
    """
    cards: list[Any] = []
    for action in query.legal_actions:
        card = getattr(action, "card", None)
        if card is not None:
            cards.append(card)
    for slot in getattr(query, "slots", ()) or ():
        if slot.card is not None:
            cards.append(slot.card)
    return cards


def candidate_index(query: Query) -> dict[str, dict[str, Any]]:
    """按牌聚合的**候选索引**——界面据它高亮"能点谁"，据它判定提交**可否**。

    它是**合法行动集合**的索引，不是第二份合法性：每个 ``pool`` 里的座位、每组 ``combos``
    里的组合，都能在同一张牌的候选里找到对应的一条。故"候选与校验同源"不因下发形状变化而改变，
    可由一条断言机械钉死（`tests/test_web.py`）。

    三件事刻意不做：

    - **不裁决规则**：不读距离 / 攻击范围 / 花色 / 体力 / 存活；只把候选里有的事实聚合起来。
      唯一读行为表的地方是 ``self_only``（见下），它转发的是**牌自己声明的目标规则**，不是
      这里判出来的合法性。
    - **不重排**：``pool`` 与 ``combos`` 保持候选的**首次出现顺序**（候选本身由引擎稳定排序）。
    - **不收无牌的候选**：``SelectOption`` / ``ActivateSkill`` 不是"牌 → 目标"，不进索引。

    ``min_targets`` / ``max_targets`` 是**这张牌这次使用**要选几个目标，**由候选组合派生**
    （【杀】额定 1；【方天画戟】等 ``TARGET_COUNT`` 修饰器成立时可放宽到 ``1..max``；
    【南蛮入侵】类等于池大小），界面据此说"还要选 N 个"。

    ``self_only`` = **这张牌这次使用的目标由牌自己的规则钉死为使用者本人**——牌行为表的
    ``TargetRule.self_only``（【无中生有】/【桃】/【酒】/装备牌/【闪电】）**且**候选组合里的
    每个座位都恰好是 ``query.actor``。界面用它决定"不画座位高亮"（目标没有可选的余地）。

    它是**转发的事实**，不是第二份合法性；反过来，**池只有一个座位不等于自用牌**：
    【帷幕】把黑色 1–2 目标锦囊的池削到只剩使用者时 ``pool == [actor]``、``min == max == 1``，
    形状与【无中生有】逐字相同，但规则不是自用牌 ⇒ ``False``（座位仍要画成可指定）。
    濒死求桃是另一半：规则 ``self_only`` 为真，候选目标却是濒死者（可能不是使用者）⇒ ``False``。

    **重铸不参与目标聚合**（M5-08b / D6）：``Reforge`` 是独立 Action、不是 ``UseCard`` 的
    变体，它没有目标。索引仍为它保留**这张牌的条目**（手牌因此可点、重铸入口读
    ``legal_actions`` 是否出现该 Action），但**不贡献** ``combos`` / ``pool``——否则 0 目标
    会被当成一条合法的「使用」组合（min_targets 变 0、界面显示"目标已选好"）。
    """
    grouped: dict[str, dict[str, Any]] = {}
    for action in query.legal_actions:
        card = getattr(action, "card", None)
        if card is None:
            continue
        entry = grouped.get(str(card.card_id))
        if entry is None:
            entry = {
                "card": card.model_dump(mode="json"),
                "pool": [],
                "combos": [],
                "_sizes": set(),
                # 牌自己声明的目标规则（不是按池形状推的）：决定 `self_only` 的那一半。
                "_self_only_rule": bool(behavior_for(card).target_rule.self_only),
            }
            grouped[str(card.card_id)] = entry
        # 重铸：只保留"这张牌可点"的条目，不把它的空目标并进使用组合。
        if isinstance(action, Reforge):
            continue
        combo = tuple(getattr(action, "targets", ()))
        if combo not in entry["combos"]:
            entry["combos"].append(combo)
        entry["_sizes"].add(len(combo))
        for seat in combo:
            if seat not in entry["pool"]:
                entry["pool"].append(seat)

    actor = getattr(query, "actor", None)
    for entry in grouped.values():
        sizes = entry.pop("_sizes")
        rule_self_only = entry.pop("_self_only_rule")
        entry["min_targets"] = min(sizes) if sizes else 0
        entry["max_targets"] = max(sizes) if sizes else 0
        entry["self_only"] = bool(
            rule_self_only
            and entry["combos"]
            and actor is not None
            and all(seat == actor for combo in entry["combos"] for seat in combo)
        )
        entry["combos"] = [list(combo) for combo in entry["combos"]]
    return grouped


def _mask_watched_faces(payload: dict[str, Any]) -> None:
    """把**观看来的牌面**从一份不归本座位的载荷里抹掉（票 15；`CODING_STANDARDS.md` §1 读取方）。

    ``SELECT_CARDS`` 的 ``slots[].card`` 与询问的 ``revealed`` 是**授予本次应答者观看**的
    牌面（【遗计】看牌堆顶两张、将来【观星】看牌堆顶若干）。引擎侧它们不进任何 ``PlayerView``、
    不落带牌面的 delta；接入层这条"当前挂起"的载荷因此也必须只在**该询问的挂起者就是查看者**
    （人类座位）时才带牌面——否则任何一次"查看者不是挂起者"的下发都会把别人观看的牌面直接
    送出去。查看者缺失（裸 ``Query`` 且调用方没声明）按**不是挂起者**处理（fail-closed）。

    公开事实（谁在看、有几个位置）保留：``slots`` 仍按**位置**下发（``card=None``），
    与 :func:`~sgs.state.project` 对隐藏区域的"只有位置"是同一形状。

    **处理区是公开的**（U2-11 / D4）：五谷亮在 ``processing`` 区，牌面对所有人生效，故
    ``ref.zone == processing`` 的槽位**不抹**——五谷取牌这一步在"AI 目标应答"时也会被
    下发（亮牌模板要看得出剩什么），抹掉就只剩位置了。其余区域（手牌 / 装备 / 判定）照旧
    按查看者归属收口；``revealed`` 仍整份抹掉（它是【遗计】这类**观看授予**，不是公开区）。
    """
    slots = payload.get("slots")
    if isinstance(slots, list):
        masked: list[Any] = []
        for slot in slots:
            ref = slot.get("ref") if isinstance(slot, dict) else None
            public_processing = isinstance(ref, dict) and ref.get("zone") == Zone.PROCESSING.value
            masked.append(slot if public_processing else (
                {**slot, "card": None} if isinstance(slot, dict) else slot
            ))
        payload["slots"] = masked
    payload["revealed"] = []


def _mask_private_ask(payload: dict[str, Any]) -> None:
    """把一份**自见询问**的**内容**整份抹掉（shan-baozuoci 票 05）。

    化身①/② 的选项闭集就是"手里那几张化身牌的合格技能"——**选项 id 本身**（
    ``hero.dianwei_mobile.qiangxi``）就点名了别人的化身牌。化身牌只有自己可见（用户
    2026-11-13）⇒ 查看者不是挂起者时，这条询问只留"谁在被问、什么时机"的框：

    - ``appearance`` / ``option_details`` / ``option_texts``：显示面（牌名 / 技能名 / 全文）；
    - ``options`` / ``legal_actions`` / ``candidate_index`` / ``virtual_index``：候选面
      （选项 id 逐条点名化身牌）；
    - ``prompt_display``：提示条契约里带着**超时默认动作**，同样是那条选项 id。

    与 :func:`_mask_watched_faces` / :func:`_mask_foreign_preactivations` 同一条纪律：
    fail-closed、只抹**内容**、不抹"这一步存在"这件公开事实（``kind`` / ``actor`` /
    ``seq`` / ``prompt`` / ``context`` 留着）。界面侧本来也不渲染别人的询问面
    （``pending.js:renderPending`` 的"等待其他座位响应…"分支早退）。
    """
    payload.pop("appearance", None)
    payload.pop("option_details", None)
    payload.pop("prompt_display", None)
    payload["option_texts"] = {}
    payload["options"] = []
    payload["legal_actions"] = []
    payload["candidate_index"] = {}
    payload["virtual_index"] = {}


def _steps_payload(steps: Any) -> list[dict[str, Any]]:
    """预发动清单 → JSON 形状（``Step`` 的字段原样；读取方 = ``app.js`` 的清单渲染）。"""
    return [step.model_dump(mode="json") for step in steps]


def _preactivation_payload(manifests: Any) -> dict[str, list[dict[str, Any]]]:
    """``键 → 清单`` → JSON 形状；保持询问里的顺序（引擎已按键排序）。

    键的两种来源：技能清单 = 技能 id；附带指定清单（W5-02b）= **卡牌类型 id**
    （``card.type``，如 ``borrowed_sword``）。接入层只转发，不解释键。
    """
    return {key: _steps_payload(steps) for key, steps in manifests.items()}


def issued_manifest(query: Any, skill_id: str) -> tuple[Any, ...] | None:
    """这次询问**已经下发**给 ``skill_id`` 的那份预发动清单（票 11 的预览端点唯一的取法）。

    顺序与 ``engine._validate_structured`` 的成员判定同源（"清单发过没有"与"复合动作收不收"
    不能各说一套）：逐候选清单 → 主动入口表 → 转化清单，最后是单份清单（键 = 询问自己的
    ``skill_id``）。返回 ``None`` = 这次询问没有给它下发过清单（预览请求该被拒）。

    预览**不重渲**清单：重渲要枚举全部前序组合（那正是本票要退役的开销），而玩家看到的
    就是这一份——读它才是"预览 = 所见即所得"。
    """
    if not isinstance(skill_id, str) or not skill_id:
        return None
    for attribute in (
        "activation_preactivations",
        "preactivations",
        "transform_preactivations",
    ):
        manifests = getattr(query, attribute, None) or {}
        steps = manifests.get(skill_id)
        if steps:
            return tuple(steps)
    if getattr(query, "skill_id", None) == skill_id:
        steps = getattr(query, "preactivation", ()) or ()
        if steps:
            return tuple(steps)
    return None


def _preview_supported(query: Any) -> bool:
    """这一问的清单能不能走**步骤预览**（票 11）：任一清单的技能有流水线即可。

    判据与 :func:`issued_manifest` 同源（预览端点自己也会再核一次"这一问有没有那份清单"），
    只是这里只回答"值不值得让前端去问"：没有流水线的技能（转化清单的【龙胆】一类、
    借刀的附带指定清单）根本没有池回调可现算，声明能力只会让界面白问一次并落到
    fail-closed 的提示上。
    """
    from sgs.skills import flow_for

    timing = getattr(query, "timing", None)
    for attribute in (
        "activation_preactivations",
        "preactivations",
        "transform_preactivations",
    ):
        for skill_id in (getattr(query, attribute, None) or {}):
            if flow_for(skill_id, timing) is not None:
                return True
    single = getattr(query, "preactivation", ()) or ()
    skill_id = getattr(query, "skill_id", None)
    return bool(single) and flow_for(skill_id, timing) is not None


def _mask_foreign_preactivations(payload: dict[str, Any]) -> None:
    """查看者不是询问者时，抹掉清单里**属于询问者**的牌面（W4-1；与观看私密同一条纪律）。

    覆盖技能清单（``preactivations``）、附带指定清单（``card_preactivations``，W5-02b）、
    单份清单（``preactivation``）与**逐候选**清单（``activation_preactivations``，票 02）
    四份载体。清单的选牌步
    候选用的是**实体牌 id**（本人手牌的 id）——把整份清单原样发给别人就等于泄露手牌。
    这里按步类型收窄：

    - 选牌步（``zones`` 非空）⇒ 候选清空（区 / 张数这些**公开形状**保留）；
    - 选座位 / 选项步 ⇒ ``when`` 里的前序答案（可能含被选中的牌 id）清空，候选（公开的
      ``seat:N`` / 选项文本）并成一行保留。

    ``Step.groups``（huo-mobile-batch 票 10 的合法来源组合）与 ``pool[*].options``
    **同一条泄露面**：每个元素都是询问者手里的实体牌 id。故两步**都**整格抹掉（不是置空
    数组——缺省 / 空在读取方那里的语义是"每个组合都合法"；非询问者没有可提交的清单，
    用最省的那一档即可）。

    这条只在**非询问者**路径上生效；产品调用点（``Session`` 的 ``human_seat`` 就是询问者）
    不受影响。与 :func:`_mask_watched_faces` 同姿态：fail-closed（查看者缺失即按非询问者）。
    """

    def mask(steps: list[dict[str, Any]]) -> None:
        for step in steps:
            # 先抹来源组合（只可能出现在选牌步上，但漏一条分支就是一条泄露面）。
            step.pop("groups", None)
            if step.get("zones"):
                # 票 12：选牌步的 ``disabled`` 与 ``options`` 是**同一条泄露面**（源牌级
                # 置灰的那些 id 同样来自询问者的手牌 / 装备），故一起清空。
                step["pool"] = [
                    {**entry, "options": [], "disabled": []}
                    for entry in (step.get("pool") or [])
                ]
                continue
            options: list[Any] = []
            disabled: list[Any] = []
            row_max: int | None = None
            for entry in step.get("pool") or []:
                for option in entry.get("options") or []:
                    if option not in options:
                        options.append(option)
                for blocked in entry.get("disabled") or []:
                    if blocked not in disabled:
                        disabled.append(blocked)
                if isinstance(entry.get("max"), int):
                    row_max = entry["max"] if row_max is None else max(row_max, entry["max"])
            masked_row: dict[str, Any] = {"when": [], "options": options}
            if row_max is not None:
                # 逐来源上限是公开形状（来源已抹掉 ⇒ 只能取各行最大，与 Step.max 同量级）。
                masked_row["max"] = row_max
            if disabled:
                # 置灰项也是**公开形状**（哪些名字此刻用不了，不含前序牌的任何信息）：
                # 合并成一行后取并集——宁可多画几个灰的，也不要把"点了必被拒"的名字
                # 画成可点的（与 `_assign_activation` 的 fail-closed 同向）。
                masked_row["disabled"] = disabled
            step["pool"] = [masked_row]

    for steps in (payload.get("preactivations") or {}).values():
        mask(steps)
    for steps in (payload.get("card_preactivations") or {}).values():
        mask(steps)
    for steps in (payload.get("activation_preactivations") or {}).values():
        mask(steps)
    mask(payload.get("preactivation") or [])


def _declared_arrange_count(declared: Any, state: Any, frame: Any) -> tuple[bool, int | None]:
    """声明的排列张数 → ``(是否可判定, 声明值)``（W6-07；评审 SPEC-a 的 fail-closed 补齐）。

    ``DeckTopArrange.count`` 有两档：``int``（定数）与 ``(state, frame) -> int``（观星 =
    ``min(存活数, 5)``）。旧实现只比 ``isinstance(declared, int)``，于是**唯一消费者**观星的
    张数走的是可调用档 ⇒ "声明与实际牌数对不上"那条分支在生产里**从未被执行过**（测试靠
    monkeypatch 一个 int 才走到）。

    可调用档要拿**当前局面 + 当前帧**求值，才能与本次实际牌数比较：``state`` 是引擎状态，
    ``frame`` 就是本函数已经读的栈顶帧（与 ``_skill_of_query`` 读的是同一帧）。排列询问
    来自**阶段体**，此时结算栈可以**是空的**（阶段体帧不压栈）⇒ ``frame`` 允许为 ``None``，
    由声明自己决定用不用它（观星的 ``_guanxing_count`` 只读 ``state``）。求值抛异常
    （声明硬读帧上下文 / 写错）按"判定不了"处理 —— 投影宁可不给规格，也不猜一个。
    """
    if isinstance(declared, int):
        return True, int(declared)
    if declared is None or not callable(declared):
        return False, None
    try:
        return True, int(declared(state, frame))
    except Exception:  # noqa: BLE001 —— 声明回调的异常一律折算成"判定不了"，不冒到 HTTP 层
        return False, None


def _arrange_spec(
    engine: Any, query: Query, revealed: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """``ARRANGE`` 询问的**排列规格**（W6-03；spec §4.3 / S2）。

    询问体只带被授予的牌面：``ArrangeContext.count`` / ``split`` 由 ``_build_query``
    丢掉（不进记录流），故界面要的张数 / 切分只能由**投影**读
    :attr:`~sgs.skills.catalog.SkillDef.deck_arrange` —— 与 ``Arrange`` 步引用**同一个**
    :class:`~sgs.skills.flow.DeckTopArrange` 对象（一份声明，不是两份）。技能归属走
    :func:`_skill_of_query` 的**阶段体**先例（与【观星】的 ``requirement`` 同一条判据：
    阶段体帧不压栈，读 ``Engine.phase_body_skill()``），不另翻栈帧。

    返回 ``{count, split}``：``count`` = 本次实际张数（``revealed`` 的长度）；``split`` =
    :data:`~sgs.skills.FREE_SPLIT`（线上取值 ``"free"``）或 ``{"top": int, "bottom": int}``。
    牌面**不在这里重发**（W6-07 / 评审 J9）：它已经在同一份载荷的 ``revealed`` 里，前端读
    那一份；同一张列表序列化两次只会让"哪一份是真源"变含混。

    **fail-closed**（S2）：拿不到引擎（409 的裸 ``Query``）/ 技能归属 / 排列规格、``fixed``
    的形状不完整或 ``top + bottom != count``、声明张数（**含可调用档**）与实际牌数对不上
    ⇒ ``None``。前端据此画"排列不可用，请刷新"，而不是从候选里猜一个规格。
    """
    if engine is None or str(getattr(query, "kind", "")) != "ARRANGE":
        return None
    if not revealed:
        # 不是询问者（或裸 Query 且调用方没声明查看者）⇒ 没有牌面就没有可摆的排列面。
        return None
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    frame = frames[-1] if frames else None
    if frame is None:
        # 帧外时机（`GAME_START` / `PHASE_DRAW_END` / `PHASE_FINISH` …）：帧住在
        # `_turn_data`、不在结算栈上。声明回调（张数 / 切分 / 牌面）读帧私有事实
        # （谁在放牌），故这里要把它取回来——否则规格恒为 None、界面 fail-closed。
        provider = getattr(engine, "phase_body_frame", None)
        if callable(provider):
            frame = provider()
    data = frame.data if frame is not None else {}
    skill_id = _skill_of_query(engine, query, data)
    definition = _definition_of(engine.state, skill_id) if skill_id is not None else None
    spec = getattr(definition, "deck_arrange", None) if definition is not None else None
    if spec is None:
        return None
    count = len(revealed)
    known, declared = _declared_arrange_count(getattr(spec, "count", None), engine.state, frame)
    if not known or declared != count:
        # 声明张数不可判定，或与实际发到的牌数不同 ⇒ 规格对不上，不拿它猜。
        return None
    if hasattr(spec, "top_of"):
        # **两行交换**（huo-shenzhugeliang 票 02；首个载体 = 神诸葛亮【七星】）：两行是
        # 具名的（「星」/「手牌」），切分点由声明的 ``top`` 现算，答案由界面构造
        # （``enumerate=False``）且行内不可调序（``reorder=False``）。
        try:
            top = int(spec.top_of(engine.state, frame))
        except Exception:  # noqa: BLE001 —— 声明回调异常一律折算成"判定不了"
            return None
        if not 0 <= top <= count:
            return None
        return {
            "count": count,
            "split": {"top": top, "bottom": count - top},
            "rows": {"top": spec.top_label, "bottom": spec.bottom_label},
            "enumerate": bool(spec.enumerate_candidates),
            "reorder": bool(spec.reorder),
            "startsOnTop": bool(getattr(spec, "starts_on_top", False)),
        }
    split_spec = getattr(spec, "split", None)
    split: str | dict[str, int]
    if split_spec == FREE_SPLIT:
        split = FREE_SPLIT
    else:
        top = getattr(split_spec, "top", None)
        bottom = getattr(split_spec, "bottom", None)
        if not isinstance(top, int) or not isinstance(bottom, int):
            return None
        if top < 0 or bottom < 0 or top + bottom != count:
            # 固定切分的两行必须恰好排完这几张牌（评审 J6）；声明与实际张数对不上就不下发。
            return None
        split = {"top": top, "bottom": bottom}
    return {"count": count, "split": split}


def _harvest_resolve_frame(engine: Any) -> Any:
    """结算栈上**最内层**的【五谷丰登】``RESOLVE`` 帧；没有就 ``None``。

    五谷的逐目标取牌是一个 ``RESOLVE`` 帧 + 游标（票 27），目标之间只推进 ``cursor``；
    无懈用**帧上叠帧**的方式结算（票 11 §2），故搜索整条栈而不是只看栈顶——被问无懈时
    栈顶是那张【无懈可击】的 ``USE`` / ``RESOLVE`` 帧，五谷帧在它下面。
    """
    frames = getattr(getattr(engine, "stack", None), "frames", ()) or ()
    for frame in reversed(frames):
        if getattr(frame, "kind", None) is not FrameKind.RESOLVE:
            continue
        card = frame.data.get("card")
        if getattr(card, "type", None) == "harvest":
            return frame
    return None


def _harvest_revealed_ids(engine: Any, frames: list[Any], frame: Any) -> tuple[int, ...]:
    """本次五谷**亮出的全部牌**，按亮牌顺序（``card_id`` 元组）。

    帧私有 ``revealed`` 每取走一张就缩短一次（``pick_harvest`` 写回），故它只够表达"还剩
    什么"，**不是**完整集合、更保不住位置。完整集合从**记录流**里取：亮牌是一条
    ``deck → processing`` 的 ``card_move``（B5 的 ``reveal_harvest`` 发出），``cards``
    的顺序就是亮牌顺序。拿不到记录时退回同一 ``USE`` 帧的 ``revealed``（它不缩短）；
    再拿不到才用结算帧当前那份（fail-soft，至少不编牌）。
    """
    use_frame = None
    at = next((index for index, candidate in enumerate(frames) if candidate is frame), None)
    if at is not None:
        for candidate in reversed(frames[:at]):
            if getattr(candidate, "kind", None) is FrameKind.USE:
                use_frame = candidate
                break
    use_frame_id = use_frame.frame_id if use_frame is not None else None
    ids: list[int] = []
    for record in getattr(engine, "records", ()) or ():
        if getattr(record, "kind", None) != "delta":
            continue
        if getattr(record, "delta_type", None) != "card_move":
            continue
        if use_frame_id is not None and record.frame_id != use_frame_id:
            continue
        from_zone = getattr(record.from_zone, "zone", None)
        to_zone = getattr(record.to_zone, "zone", None)
        if from_zone is not Zone.DECK or to_zone is not Zone.PROCESSING:
            continue
        ids.extend(int(card.card_id) for card in record.cards)
    if ids:
        return tuple(ids)
    if use_frame is not None:
        return tuple(int(cid) for cid in use_frame.data.get("revealed", ()))
    return tuple(int(cid) for cid in frame.data.get("revealed", ()))


def _harvest_taken(engine: Any, frame: Any, revealed: tuple[int, ...]) -> dict[int, int]:
    """``card_id → 拿牌者座位``：折叠本帧里记录过的 ``processing → hand`` 移动。

    ``pick_harvest`` 的搬牌落在五谷的 ``RESOLVE`` 帧（``move_in_frame`` 用帧 id 记账），
    故只收 ``frame_id == 本帧`` 的 ``card_move``。取到的牌一定在亮牌集合里；其余移动
    （无懈进弃牌堆、结算完弃余牌）不是"被拿走"，不收。
    """
    known = set(revealed)
    taken: dict[int, int] = {}
    for record in getattr(engine, "records", ()) or ():
        if getattr(record, "kind", None) != "delta":
            continue
        if getattr(record, "delta_type", None) != "card_move":
            continue
        if getattr(record, "frame_id", None) != frame.frame_id:
            continue
        if getattr(record.from_zone, "zone", None) is not Zone.PROCESSING:
            continue
        if getattr(record.to_zone, "zone", None) is not Zone.HAND:
            continue
        seat = getattr(record.to_zone, "seat", None)
        if seat is None:
            seat = getattr(record, "owner", None)
        if seat is None:
            continue
        for card in record.cards:
            card_id = int(card.card_id)
            if card_id in known and card_id not in taken:
                taken[card_id] = int(seat)
    return taken


def _harvest_facts(
    engine: Any,
    frames: list[Any],
    frame: Any,
    cache: dict[str, Any] | None,
) -> tuple[tuple[int, ...], dict[int, int]]:
    """亮牌集合 + 获得者表（两次**全记录扫描**）的缓存版（table-ux-overhaul 票 18）。

    五谷状态现在挂在**每一次视图下发**上（`view.harvest`），而轮询是最热的入口：不缓存
    就是每拍两次全记录扫描。两件事实都只由「记录条数 + 五谷帧 id」决定——
    ``pick_harvest`` 的搬牌与帧数据的改写都发生在同一条 ``card_move`` 记录里（记录只增），
    故同一个键 ⇒ 同一个结果。缓存只存这两件**中间事实**，不存载荷本身：调用方每次仍拿到
    新造的 dict，查看者标签（`_decorate_card_tags`）不会跨次残留。

    ``cache`` 由会话持有（``Session.harvest_cache``），`view` 与 `pending` 两处派生共用；
    ``None`` = 不缓存（单测 / 探针直接调 :func:`harvest_state` 时）。
    """
    records = getattr(engine, "records", ()) or ()
    key = (len(records), getattr(frame, "frame_id", None))
    if cache is not None and cache.get("key") == key:
        return cache["revealed"], cache["taken"]
    revealed = _harvest_revealed_ids(engine, frames, frame)
    taken = _harvest_taken(engine, frame, revealed) if revealed else {}
    if cache is not None:
        cache["key"] = key
        cache["revealed"] = revealed
        cache["taken"] = taken
    return revealed, taken


def harvest_state(engine: Any, *, cache: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """【五谷丰登】结算的**服务端派生状态**（U2-11 / D10）：只读公开事实，进询问载荷。

    数据源只有两处：

    - **结算栈**上那个五谷 ``RESOLVE`` 帧：``targets``（取牌顺序）、``cursor``（当前目标）、
      ``canceled``（当前目标刚被无懈）、``source`` / ``card``；
    - **记录流**：亮牌那条 ``deck → processing`` 的 ``card_move``（完整集合 + 亮牌顺序）与
      每次取牌的 ``processing → hand`` 移动（``card_id → 座位``）。

    ``cache`` = 那两次全记录扫描的**按键缓存**（票 18；见 :func:`_harvest_facts`）。键只由
    记录条数与五谷帧决定，故"没有新记录的空转轮询"不重扫整条流。

    它**不改记录流、不进金录像**：字段只在下发时现算，引擎的帧 / delta / 查询一个字节不动。

    状态口径（D4 / D10）：

    - ``pending``：还没轮到；
    - ``current``：``cursor`` 指到的目标（本轮正在取牌 / 正在被问无懈）；
    - ``taken``：已取走一张牌，``card`` 是那张牌的 id；
    - ``invalid``：**最终没拿到牌**——被无懈且未被反无懈救回，或这一步被跳过。反无懈救回
      的目标会照常被问、照常拿到牌，故天然落在 ``taken``；判定只读"这个目标处理完了没有"
      与"记录里有没有它的 ``processing → hand`` 移动"，不解析无懈链。
    - 每个目标另带 ``cancelled`` / ``reason`` 两个**保留位**：将来"先被指定、后被取消"
      （如界甘宁【奋威】）由生产者置 ``cancelled``，界面按同一种红显示；标准包没有该技能，
      故现在恒为 ``false`` / ``null``。

    取不到五谷帧 / 一张亮牌都没有 ⇒ ``None``（界面据此不开亮牌弹窗）。
    """
    frame = _harvest_resolve_frame(engine)
    if frame is None:
        return None
    frames = list(getattr(getattr(engine, "stack", None), "frames", ()) or ())
    revealed, taken = _harvest_facts(engine, frames, frame, cache)
    if not revealed:
        return None
    state = getattr(engine, "state", None)
    lookup = _card_lookup(state) if state is not None else {}
    card = frame.data.get("card")
    card_face = card_payload(card, lookup=lookup) if card is not None else None

    revealed_payload: list[dict[str, Any]] = []
    for card_id in revealed:
        model = lookup.get(card_id)
        if model is None:
            continue
        entry = card_payload(model, lookup=lookup)
        entry["taken_by"] = taken.get(card_id)
        revealed_payload.append(entry)

    target_seats = tuple(int(seat) for seat in frame.data.get("targets", ()))
    cursor = frame.data.get("cursor")
    if not isinstance(cursor, int):
        cursor = 0
    current_canceled = bool(frame.data.get("canceled"))
    taken_by_seat: dict[int, int] = {}
    for card_id, seat in taken.items():
        taken_by_seat.setdefault(seat, card_id)
    targets_payload: list[dict[str, Any]] = []
    for index, seat in enumerate(target_seats):
        card_id = taken_by_seat.get(seat)
        if index < cursor:
            status = "taken" if card_id is not None else "invalid"
        elif index == cursor:
            status = "invalid" if current_canceled else "current"
        else:
            status = "pending"
        targets_payload.append(
            {
                "seat": seat,
                "status": status,
                "card": card_id,
                "cancelled": False,
                "reason": None,
            }
        )
    current = target_seats[cursor] if 0 <= cursor < len(target_seats) else None
    source = frame.data.get("source")
    return {
        "active": True,
        "key": f"harvest:{frame.frame_id}:{getattr(card, 'card_id', '')}:{source}",
        "title": getattr(card, "name", None) or "五谷丰登",
        "hint": HARVEST_REQUIREMENT,
        "source": int(source) if source is not None else None,
        "card": card_face,
        "revealed": revealed_payload,
        "targets": targets_payload,
        "current": current,
    }


def session_harvest_state(session: Session) -> dict[str, Any] | None:
    """五谷结算的**视图级**派生状态（table-ux-overhaul 票 18 / 服务端 D 方案）。

    与 `pending_payload` 里的 ``harvest`` **同一份**派生（:func:`harvest_state`，同一份
    `Session.harvest_cache`），只是不再挂在某一次询问上：会话的 ``view`` 每次下发都带它
    （键恒在，结算结束 ⇒ ``None``）。客户端因此每次视图提交都能读到"谁拿走了哪张 / 目标条
    四态 / 当前目标"，不必等**下一条恰好带 harvest 的询问**——五谷整段结算里只有一部分
    目标会真的变成一条 pending。

    牌面标签与询问载荷同一条口径（``_decorate_card_tags``：牌面已公开 ⇒ 带标签），亮牌是
    公开处理区，故不按"是不是应答者"收口。返回值是**新造的 dict**：缓存只存中间事实
    （见 :func:`_harvest_facts`），标签不会跨次残留。
    """
    harvest = harvest_state(session.engine, cache=getattr(session, "harvest_cache", None))
    if harvest is None:
        return None
    state = getattr(session.engine, "state", None)
    _decorate_card_tags(harvest.get("card"), state, session.human_seat)
    for card in harvest.get("revealed") or []:
        _decorate_card_tags(card, state, session.human_seat)
    return harvest


_LUOSHEN_REPEAT_PREFIX = "luoshen:repeat:"


def _record_enum_text(value: Any) -> str:
    """记录里的枚举 / 字符串统一成文本（``MoveReason.JUDGE`` → ``"JUDGE"``）。"""
    return str(getattr(value, "value", value) or "")


def _record_judge_card(record: Any) -> CardRef | None:
    """一条 ``card_move`` 是不是"判定牌进处理区"；是则返回那张判定牌。"""
    if getattr(record, "kind", None) != "delta":
        return None
    if getattr(record, "delta_type", None) != "card_move":
        return None
    if _record_enum_text(getattr(record, "reason", None)).upper() != "JUDGE":
        return None
    to_zone = getattr(record, "to_zone", None)
    if _record_enum_text(getattr(to_zone, "zone", None)).lower() != "processing":
        return None
    cards = tuple(getattr(record, "cards", ()) or ())
    return cards[0] if len(cards) == 1 else None


def _judge_cards(records: Iterable[Any], after_seq: int, before_seq: int) -> list[CardRef]:
    """一段记录流里按 seq 排序的全部判定牌（``card_move(reason=JUDGE, to=processing)``）。"""
    found: list[tuple[int, CardRef]] = []
    for record in records:
        seq = int(getattr(record, "seq", -1) or -1)
        if seq <= after_seq or seq >= before_seq:
            continue
        card = _record_judge_card(record)
        if card is not None:
            found.append((seq, card))
    found.sort(key=lambda pair: pair[0])
    return [card for _seq, card in found]


def _judgment_card_payload(engine: Any, card: CardRef, lookup: Mapping[int, CardRef] | None) -> dict[str, Any]:
    try:
        ref = engine.state.card(int(card.card_id))
    except Exception:  # noqa: BLE001 - 展示派生 fail-visible：牌表缺项时用记录里的 CardRef
        ref = card
    return card_payload(ref, lookup=lookup)


def judgment_reveal_state(
    engine: Any,
    query: Query,
    lookup: Mapping[int, CardRef] | None,
) -> dict[str, Any] | None:
    """A1：洛神连续判定的**纯显示层派生**（不进记录流 / 06 / 金录像）。

    读取的都只是服务端已有事实：记录流里的 ``SKILL_ACTIVATION`` 询问（本次循环的起点）、
    ``SELECT_OPTION`` 继续询问、以及 ``card_move(reason=JUDGE, to=processing)`` 的判定牌。

    - 继续询问 ⇒ ``{chain_key, cards, final: False}``：``cards`` = **本次循环从第一张到
      当前这张**的全部判定牌（按判定顺序，前一张不丢）；
      ``chain_key = judge-loop:<activation seq>``，同一轮循环内不变；
    - 循环结束（红牌 / 放弃）后的**第一条同座位询问** ⇒ ``{chain_key, cards, final: True}``：
      ``cards`` 仍含本次循环的全部判定牌（红牌终止时含红牌）；前端据此关闭弹窗；
      ``final`` 的语义就是"循环已结束、请关窗"，不表示"还属于循环"；
    - 没有下一份 pending（局终 / 死亡 / 换会话）⇒ 本字段不出现，前端在 ``pending == null``
      或 ``viewStartsNewSession`` 时关窗（`syncDialog` 的既有生命周期，不新增定时器）。

    取不到激活询问 / 判定牌时返回 ``None``（fail-closed，不猜链）。
    """
    records = tuple(getattr(engine, "records", ()) or ())
    current_seq = int(getattr(query, "seq", -1) or -1)
    if current_seq < 0:
        return None

    activation_seq: int | None = None
    for record in records:
        if getattr(record, "kind", None) != "query":
            continue
        seq = int(getattr(record, "seq", -1) or -1)
        if seq > current_seq:
            continue
        inner = getattr(record, "query", None)
        if inner is None or str(getattr(inner, "kind", "")) != "SKILL_ACTIVATION":
            continue
        if str(getattr(inner, "skill_id", "")) in (LUOSHEN, JIE_LUOSHEN):
            activation_seq = seq
    if activation_seq is None:
        return None

    last_continue: int | None = None
    for record in records:
        if getattr(record, "kind", None) != "query":
            continue
        seq = int(getattr(record, "seq", -1) or -1)
        if seq <= activation_seq or seq > current_seq:
            continue
        inner = getattr(record, "query", None)
        if inner is None:
            continue
        if str(getattr(inner, "skill_id", "")).startswith(_LUOSHEN_REPEAT_PREFIX):
            last_continue = seq

    chain_key = f"judge-loop:{activation_seq}"
    if str(getattr(query, "skill_id", "")).startswith(_LUOSHEN_REPEAT_PREFIX):
        cards = _judge_cards(records, activation_seq, current_seq)
        if not cards:
            return None
        return {
            "chain_key": chain_key,
            "cards": [_judgment_card_payload(engine, card, lookup) for card in cards],
            "final": False,
        }

    if last_continue is None:
        return None
    actor = int(getattr(query, "actor", -1) or -1)
    first_after: int | None = None
    for record in records:
        if getattr(record, "kind", None) != "query":
            continue
        seq = int(getattr(record, "seq", -1) or -1)
        if seq <= last_continue:
            continue
        inner = getattr(record, "query", None)
        if inner is None or int(getattr(inner, "actor", -1) or -1) != actor:
            continue
        first_after = seq
        break
    if first_after != current_seq:
        return None
    cards = _judge_cards(records, activation_seq, current_seq)
    if not cards:
        return None
    return {
        "chain_key": chain_key,
        "cards": [_judgment_card_payload(engine, card, lookup) for card in cards],
        "final": True,
    }


def _two_row_watch_face(query: Query) -> bool:
    """这一步是不是**两行观看手牌**的选牌面（lei-shenganning 票 93 的框架面）。

    判据只读**询问形状**，不认技能 id：

    - ``SELECT_CARDS`` ∧ ``spec.max_per_suit`` 有值（"每种花色至多一张"这条约束是面自己的
      声明，不是技能名）；
    - 槽位**恰好跨两个座位**，且其中一个是询问者自己 ⇒ 上行 = 对方手牌、下行 = 自己的手牌；
      只有一手（目标 0 手牌，票面说"不该出现"）⇒ 不套本模板、整面 fallback（§3 B1）；
    - 槽位为空 = 凑不出四色的**空选择面**（引擎 ``ask_when_empty=True``）：整副被观看的手牌
      仍在 ``revealed`` 里，仍开这个模板，只有「放弃」一个出口。**空面的 ``revealed`` 约定
      只含上行（被观看角色）那一手**——正常面的 ``revealed`` 才是两副手牌的并集（观看授予）。

    判据跑在 ``_mask_watched_faces`` **之前**读原始询问：``revealed`` 是授予应答者的观看
    通道，非应答者的载荷里已被抹空；但"这一步是两行观看面"是**公开形状**（哪两个座位的手牌
    在面里、要不要四色），非应答者也要按两行画位置（票面 §3 B4）。牌面本身仍只由
    ``_mask_watched_faces`` 决定，本函数不碰任何牌面。

    与前端 :func:`modal.js:isWatchTwoRowsStep` 读的是**同一形状**、但读取面不同：这里拿到
    未抹的询问（``revealed`` 恒在），前端拿到的是抹后的载荷（非应答者的 ``revealed`` 为空，
    只能靠服务端下发的 ``dialog`` 标记补上"这是观看面"的证据）。
    """
    if str(getattr(query, "kind", "")) != "SELECT_CARDS":
        return False
    spec = getattr(query, "spec", None)
    if spec is None or not getattr(spec, "max_per_suit", None):
        return False
    if not getattr(query, "revealed", ()):
        return False
    slots = getattr(query, "slots", ()) or ()
    if not slots:
        return True
    seats: set[int] = set()
    for slot in slots:
        ref = getattr(slot, "ref", None)
        if ref is None or getattr(ref, "seat", None) is None:
            return False
        seats.add(int(ref.seat))
    if len(seats) != 2:
        return False
    return int(query.actor) in seats


def pending_payload(
    session: Session | Query | None, *, human_seat: int | None = None
) -> dict[str, Any] | None:
    """一次询问的**可应答形态**——带候选、带询问者自己的视角（前端只 pick，不自己拼 Action）。

    收 ``Session``（取它的当前挂起，查看者 = 它的 ``human_seat``）、``Query``（409 里附带的
    现状，查看者得由调用方用 ``human_seat`` 声明）或 ``None``（无挂起）。

    ``legal_actions`` 保留（既有测试、超时兜底与"提交必须原样取自候选"的自我核对都读它），
    ``candidate_index`` 是本轮新增的**索引形状**：界面用它手选目标，不再逐条铺候选按钮。

    **观看私密（票 15，评审 finding ①）**：这份载荷只有在**查看者就是挂起者**时才带
    ``slots[].card`` / ``revealed``（见 :func:`_mask_watched_faces`）。查看者的真源分两条：

    - 收 ``Session`` ⇒ 它的 ``human_seat``（唯一的会话级真源，``human_seat`` 参数忽略）；
    - 收裸 ``Query``（409 里附带的那份现状）⇒ 只能由 ``human_seat`` 声明——**没声明就抹掉**
      （fail-closed）。旧签发把裸 ``Query`` 当"构造时本就只为人类座位"原样放行，而
      `serve.py` 的 409 分支传的正是裸 ``Query`` ⇒ 两张观看牌面随 409 直接下发（稳定复现见
      `tests/test_web.py::test_the_409_path_does_not_hand_the_watched_faces_to_a_query_without_a_viewer`）。
      现在 :class:`~sgs_web.session.StaleAnswer` 自己带着 ``human_seat``，那条路径照样判得出归属。
    """
    if session is None:
        return None
    if isinstance(session, Session):
        query = session.pending
        viewer: int | None = int(session.human_seat)
    else:
        query = session
        viewer = None if human_seat is None else int(human_seat)
    if query is None:
        return None
    payload = query.model_dump(mode="json")
    foreign = viewer is None or int(query.actor) != viewer
    if foreign:
        _mask_watched_faces(payload)
    payload["legal_actions"] = [action.model_dump(mode="json") for action in query.legal_actions]
    payload["candidate_index"] = candidate_index(query)
    # 预发动清单（W4-1）：**不进记录流**（询问字段 ``exclude``），故接入层显式下发。
    # 主动入口 = 技能 id → 清单；被动入口 = 单份清单。没有时给空值，键恒在（读取方契约）。
    # 载荷键与**询问模型字段**同名（``preactivations`` / ``preactivation``，读取方 = app.js）：
    # W5-00 把两族拼写统一到正确写法，改键仍要两头一起改。
    # W4-3：转化技的清单是**本地入口**（确认 = 普通候选，不是复合动作），但载体同一个键：
    # 界面按技能声明上的 ``preactivated`` 分辨"复合动作 / 本地入口"，读取方因此只有一处。
    manifests = dict(getattr(query, "preactivations", None) or {})
    manifests.update(getattr(query, "transform_preactivations", None) or {})
    payload["preactivations"] = _preactivation_payload(manifests)
    # 附带指定的**牌名清单**（W5-02b 借刀）：主动入口的第三种载体，键 = ``card.type``
    # （如 ``borrowed_sword``）。界面点手牌时按这张表进入两步清单，再拼
    # ``UseCard(card, targets=(A,), designations=(Bn,))``；接入层**不重算**池 / 合法性。
    payload["card_preactivations"] = _preactivation_payload(
        getattr(query, "card_preactivations", None) or {}
    )
    payload["preactivation"] = _steps_payload(getattr(query, "preactivation", ()) or ())
    # **逐候选**的预发动清单（票 01 的 ``SkillActivationQuery.activation_preactivations``；
    # 票 02 的读取面 = ``preactivation.js:preactivationManifest``）：同一时机有 N 条技能
    # 候选时，每条候选各有自己的一份清单，界面按**选中的那条**取；单候选面上它与上面的
    # ``preactivation`` 同源（旧面一字不变）。同样是 ``exclude=True`` 的本地面 ⇒ 接入层
    # 显式下发；键恒在（没有清单时给空表），读取方不必先判键存在。
    payload["activation_preactivations"] = _preactivation_payload(
        getattr(query, "activation_preactivations", None) or {}
    )
    # **步骤预览的可用声明**（table-ux-overhaul 票 11）：这一问有"有流水线的清单"才带
    # ``"preview": true``——即 ``POST …/preview`` 对这一问真能算。前端据此决定"每次选择
    # 变化问一次后端"；**键的缺席是保证**：合成载荷 / 没有流水线的转化清单 / 旧载荷都不带
    # 它，界面照旧读清单里的静态池（与 ``structural`` / ``default_choice`` 同一姿态）。
    if _preview_supported(query):
        payload["preview"] = True
    # **选牌预览的可用声明**（table-ux-overhaul 票 15）：``SELECT_CARDS`` 且查看者就是应答者
    # 时带 ``"pick_preview": true``——``POST …/preview`` 的选牌那一档对这一问真能算
    # （端点的判据与这里同源：kind + actor）。前端据此在**自家手牌面**上每次选牌问一次
    # "哪些槽位还点得动"；**键的缺席是保证**：合成载荷 / 别人的面 / 旧载荷都不带它，界面
    # 照旧只按面形状渲染（与 ``preview`` / ``structural`` 同一姿态）。
    # 它是**网页载荷**字段、不是 ``Query`` 模型字段 ⇒ 不进记录流、金录像逐字节不变。
    if str(getattr(query, "kind", "")) == "SELECT_CARDS" and not foreign:
        payload["pick_preview"] = True
    # 「是否发动」的**默认候选**（yijiang-2014-round2 票 15 / 路线 B）：投影按
    # :data:`_DEFAULT_ACTIVATE_SKILLS` 登记表下发；前端 ``answer.js:buildAction`` 在玩家
    # 没选任何候选时据此返回候选里那条 ``ActivateSkill``。**只有登记过的技能才带这个键**
    # ——其余「是否发动」（苦肉 / 洛神 / …）载荷逐字节不变（键的缺席就是那个保证）。
    if (
        str(getattr(query, "kind", "")) == "SKILL_ACTIVATION"
        and str(getattr(query, "skill_id", "")) in _DEFAULT_ACTIVATE_SKILLS
    ):
        payload["default_choice"] = "activate"
    if foreign:
        _mask_foreign_preactivations(payload)
    # **不枚举档**多选（yijiang-2014-round2 票 21）：询问上的 ``structural`` 是 ``exclude=True``
    # 的本地面（不进记录流 / 金录像），前端看不到它；投影显式下发一个 ``structural: true``
    # 让 ``answer.js`` 知道"候选只有骨架、集合由界面自己拼"。与 ``default_choice`` 同一姿态：
    # **只有这一档才带这个键**，其余多选面（界神速）的载荷逐字节不变（键的缺席就是那个保证）。
    if (
        str(getattr(query, "kind", "")) == "SELECT_OPTIONS"
        and bool(getattr(query, "structural", False))
    ):
        payload["structural"] = True
    # 询问窗口的"谁对谁做了什么"：从**结算栈顶帧 + 询问本身**派生的表现层事实。
    # 只有拿得到 ``Session``（= 拿得到引擎）时才算；409 里附带的那份现状（裸 ``Query``）为 None。
    payload["context"] = (
        query_context(session.engine, query) if isinstance(session, Session) else None
    )
    # 排列规格（W6-03 / S2）：``ARRANGE`` 询问只带牌面，张数 / 切分由投影读
    # ``SkillDef.deck_arrange``（与 ``Arrange`` 步同一对象）。只有收 ``Session`` 才拿得到
    # 引擎；409 里附带的裸 ``Query`` ⇒ ``None``（fail-closed，前端画"排列不可用"）。
    # 与 ``context`` 同姿态：派生数据**不进记录流**，既有 Golden Replay 一行不改。
    # W6-07：牌面**不在这里重发**——前端读同一份载荷的 ``revealed``（评审 J9）。
    payload["arrange"] = (
        _arrange_spec(session.engine, query, payload.get("revealed") or [])
        if isinstance(session, Session)
        else None
    )
    # 计数式分配（huo-shenzhouyu-mobile 票 05；首个载体 = 神周瑜【业炎】）：
    # 询问模型已带 ``seats`` / ``max_total`` / ``per_seat_max``（公开事实），这里只补
    # **展示事实**（座位的人话标签）。与 ``option_seats`` 同姿态：派生、不进记录流；
    # 取不到引擎（409 里的裸 ``Query``）⇒ ``None``，界面 fail-closed 画"分配不可用"。
    payload["distribution"] = (
        _distribution_payload(session.engine, query)
        if isinstance(session, Session)
        else None
    )
    payload["option_seats"] = option_seats(query)
    # M5-bugfix（雌雄双股剑）：一次性选项步的**来源**展示事实（`{"seat", "name"}`）。键恒在
    # （读取方契约）；只对装备技能的一次性选项有值，其余为 None（界面不画来源文案）。
    # 与 `context` 同姿态：派生数据不进记录流 / 金录像。
    payload["option_source"] = (
        option_source(session.engine, query) if isinstance(session, Session) else None
    )
    # M5-08a（D10 / §5.1.4）：【火攻】的两步选牌。读取方 = 亮牌模板 / 牌桌点手牌：
    #   - 亮牌步：牌桌直选（票 09 / spec §1 #12）——槽位全是应答者**自己的手牌**，
    #     牌面本来就在 `view.self_hand` 里，故**不**授予 ``revealed``（见 :func:`_own_hand_face`）；
    #     授予一旦为空，下面的 ``dialog`` 派生自然给 ``null``，界面走既有的 own-hand-pick。
    #   - 弃牌步：``reveal`` = 已经公开的那一张（使用者据此在中央舞台看到亮牌）。
    # 两步的牌面都只在本座位载荷里；别人的载荷由既有的 ``_mask_watched_faces`` 抹掉。
    fire_attack = _fire_attack_step(session.engine, query) if isinstance(session, Session) else None
    payload["reveal"] = None
    if (
        fire_attack is not None
        and not foreign
        and fire_attack["step"] == "reveal"
        and not _own_hand_face(query)
    ):
        payload["revealed"] = [
            slot.card.model_dump(mode="json")
            for slot in query.slots
            if getattr(slot, "card", None) is not None
        ]

    # U2-08（D10）：花色是**会被复用的枚举模板**，中文名 / 符号 / 红黑由服务端派生一次；
    # 选项闭集里只要不全是花色就是 ``None``（界面据此决定画选择型弹窗还是小选项组）。
    # 与 ``option_seats`` 分开两个键：一个回答"这是谁"，一个回答"这项怎么显示"。
    payload["option_labels"] = suit_labels(getattr(query, "options", ()) or ())
    # U2-11（D4 / D10）：【五谷丰登】结算的服务端派生状态（亮牌 + taken_by + 目标条 + 当前
    # 目标）。只读结算栈与记录流，**不进**记录流 / 金录像。这是**公开处理区**的事实，故不按
    # 查看者收口：AI 目标拿牌时，人类屏幕上的亮牌弹窗也要显示同一份状态。取不到引擎
    # （409 里附带的裸 ``Query``）⇒ ``None``（键恒在，前端 fail-closed 不开窗）。
    # 票 18 起与视图级 ``view.harvest`` 走**同一个口**（同一份派生 + 同一份扫描缓存）。
    payload["harvest"] = (
        session_harvest_state(session) if isinstance(session, Session) else None
    )
    # U2-12（D3 / D10）：**观看牌面**的选牌步 = 亮牌弹窗。判据读**形状**而不是技能 id：
    # ``SELECT_CARDS`` 且这一问带着**授予的牌面**（``revealed`` 非空，票 15 的私密观看通道）。
    # 当前唯一生产者是【遗计】的牌堆顶两张；将来同形状的观看技能自动套同一模板。
    #
    # 票 93：**两行观看手牌**（上行对方手牌 / 下行自己手牌、四色各一张）是同一通道上的
    # 另一种面。标记按**原始询问**算（见 :func:`_two_row_watch_face`），先于 ``revealed``
    # 的抹除：形状是公开事实，非应答者也要按两行画位置。
    if _two_row_watch_face(query):
        payload["dialog"] = "watchTwoRows"
        # 不另下发 `rows`：`slots` 已经是唯一真源（`ref.seat` 分两行即可），再序列化一份
        # 同一组牌面会让"哪一份是真源"变含混（与 `_arrange_spec` 不重发 `revealed` 同一条）。
    else:
        # 其余情况**只在查看者就是挂起者**时给（上面 :func:`_mask_watched_faces` 已把别人的
        # ``revealed`` 抹空，故这里读的是抹后的载荷）：它不是牌面数据，也不必发给他人的
        # 屏幕——别人的那一份连"这一步是私密亮牌"都不需要知道。取不到查看者（409 裸
        # ``Query`` 且没声明）同样为 ``None``（fail-closed）。键恒在；**不进记录流 / 金录像**
        # （纯派生展示字段）。
        payload["dialog"] = (
            "reveal"
            if str(query.kind) == "SELECT_CARDS" and payload.get("revealed")
            else None
        )
    # A1：洛神连续判定的显示层派生（`{chain_key, card, final}`）。只从记录流 / 引擎已有
    # 事实派生，**不进记录流 / 06 / 金录像**；查看者不是挂起者时为 None（fail-closed）。
    # 字段键恒在（读取方契约），无循环时为 None。
    payload["judgment_reveal"] = None
    # 这一步是不是"提供一张 B"（票 14）：非空 = B 的牌名 id。字段在记录流里被 ``exclude``，
    # 故**不进** Golden Replay 的字节；界面靠它把这一类选择面**就地**铺到本人的可见区域上。
    payload["provide_type"] = getattr(query, "provide_type", None)
    # 转化候选的**来源手牌**索引（【龙胆】：点【闪】要能命中"当【杀】"的那条候选）。
    # 拿不到引擎（409 里附带的裸 ``Query``）时仍下发来源 → 虚拟牌的别名，只是 ``skill_ids`` 为空。
    payload["virtual_index"] = _virtual_index(
        query,
        session.engine.state if isinstance(session, Session) else None,
        owner=int(query.actor),
    )
    # U1-04 展示契约：询问里的每张牌（候选 / 候选索引 / 选择面 / 授予的牌面）+ 内嵌视角
    # + 提示条（文本 / 超时默认动作）。裸 ``Query``（409）拿不到引擎 ⇒ 来源牌留空，不猜。
    # 来源牌查找表只在**查看者就是询问者**时给：409 的裸 Query 从询问自己已出现的牌里找
    # （拿不到引擎），Session 走引擎牌表；查看者不是询问者时 fail-closed，别借 `source_cards`
    # 把别人的隐藏手牌带出来。
    same_viewer = viewer is not None and int(query.actor) == int(viewer)
    if not same_viewer:
        lookup = None
    elif isinstance(session, Session):
        lookup = _card_lookup(session.engine.state)
    else:
        lookup = card_lookup_from_query(query)
    if isinstance(session, Session) and same_viewer:
        payload["judgment_reveal"] = judgment_reveal_state(
            session.engine, query, lookup
        )
    # 弃牌步的公开亮牌：只发给使用者本人（他是这一步的应答者）；牌来自帧载荷，不进记录流。
    if (
        fire_attack is not None
        and fire_attack["step"] == "discard"
        and not foreign
        and lookup is not None
    ):
        payload["reveal"] = {
            "seat": int(fire_attack["target"]),
            "card": card_payload(fire_attack["revealed"], lookup=lookup),
        }
    # U1-02：内嵌视角里的座位也要武将名称 / 势力；裸 Query（409）拿不到包，传 None。
    heroes = load_hero_tables(session.pack.pack_id) if isinstance(session, Session) else None
    # 票 07（票 02 §4.2 B4）：**展示武将牌**的询问——武将块（势力 / 名称 / 体力上限）、
    # 每条选项的显示文本 / 技能描述（descriptions.json 原文、不截断）/ 置灰标记，全部
    # 服务端派生、**不按查看者裁剪**（武将牌是公开事实，第二轮 R6）。
    #
    # 只有在**新字段真的在用**时才加键：既有询问载荷（雌雄双股剑 / 遗计"交给谁"等）
    # 因此逐字节不变。裸 Query（409）拿不到会话的包，但武将牌堆的缺省池就是**全部**武将
    # 表（``enabled_hero_ids()`` 的读法），故照样派生得出来——界面不会因 409 而丢名字。
    hero_tables = heroes if heroes is not None else load_hero_tables()
    shown_hero = getattr(query, "hero_card", None)
    if shown_hero is not None:
        payload["hero_card"] = hero_card_display(hero_tables, shown_hero)
    if str(query.kind) == "SELECT_OPTION" and (
        shown_hero is not None
        or getattr(query, "disabled", ())
        or getattr(query, "option_texts", None)
    ):
        payload["option_details"] = option_details(query, heroes=hero_tables)
    # 技能外观组件（shan-baozuoci 票 05）：**自见载荷**。数据由技能自己写进状态
    # （化身库 = `Player.avatar_cards`），组件只负责显示；判据读**声明**
    # （`query.skill_id` → `SkillDef.appearance`），不按技能 id / 选项值猜。
    #
    # 化身牌只有自己可见（用户 2026-11-13）⇒ 与观看牌面同一条纪律：只有**挂起者就是
    # 查看者**时才构造 / 下发。别人收到的是抹过的载荷——连 `option_details` /
    # `option_texts` 一起抹（那些文本是"武将名·技能名"，逐条点名了别人的化身牌）。
    appearance_kind = _appearance_kind_of_query(query)
    if appearance_kind is not None and not foreign and isinstance(session, Session):
        entry = _appearance_payload(
            session.engine.state,
            int(query.actor),
            appearance_kind,
            selectable=tuple(
                action.choice
                for action in query.legal_actions
                if getattr(action, "kind", None) == "SelectOption"
            ),
        )
        if entry is not None:
            payload["appearance"] = entry
    # U1-07：转化清单的产物索引（全部产物牌面）。清单本身只说"选哪些来源牌"，
    # 界面要写「将以【X】使用」只能读这份派生；不是查看者的询问 ⇒ 空列表（fail-closed）。
    payload["transform_products"] = (
        _transform_products(
            query,
            lookup=lookup,
            state=session.engine.state if isinstance(session, Session) else None,
            owner=int(query.actor),
        )
        if same_viewer
        else []
    )
    decorate_pending(
        payload,
        query,
        lookup=lookup,
        heroes=heroes,
        virtual_cards=(
            _active_virtual_cards(session.engine) if isinstance(session, Session) else ()
        ),
        # UI1/UI2：内嵌视角里的处理区同样不给未验明的扣置牌牌面。
        concealed=(
            concealed_guohuo_cards(session.engine) if isinstance(session, Session) else None
        ),
    )
    if isinstance(session, Session) and same_viewer:
        # 只给**自己的**询问载荷补标签；foreign（409 里附带的别人的现状）连标签也不带。
        _decorate_pending_card_tags(payload, session.engine.state, viewer)
    if foreign and appearance_kind is not None:
        # 自见询问：查看者不是挂起者 ⇒ **内容整份不发**（见 :func:`_mask_private_ask`）。
        # 放在最后是因为 `prompt_display` 由 `decorate_pending` 补进来。
        _mask_private_ask(payload)
    return payload


def query_actor_map(records: Iterable[object]) -> dict[int, int]:
    """记录流里的 ``query.seq → query.actor``（应答者的唯一真源）。

    ``ActionRecord`` 自己**不带座位**：一次应答是谁做的，看它的 ``answers_seq`` 指向的
    那次 ``query``（记录流里已下发的公开事实）。``serve.GameRegistry.view`` 拿这张表判断
    一条 action 是不是查看者自己的；``skill_events_after`` 用同一判据给派生事件找座位。

    ``records`` 可以是引擎记录模型，也可以是已序列化的 JSON（录像 / 合成夹具）。这里只读
    ``seq`` / ``query.actor`` 两个字段，**不**把整条询问（含候选 / 视角）序列化一遍——
    ``GameRegistry.view`` 每次取增量都会调它，整条序列化会把一次刷新放大成几十毫秒。
    """
    actors: dict[int, int] = {}
    for record in records:
        if isinstance(record, Mapping):
            if record.get("kind") != "query":
                continue
            query = record.get("query")
            seq = record.get("seq")
            actor = query.get("actor") if isinstance(query, Mapping) else None
        else:
            if getattr(record, "kind", None) != "query":
                continue
            query = getattr(record, "query", None)
            seq = getattr(record, "seq", None)
            actor = getattr(query, "actor", None)
        if isinstance(seq, int) and isinstance(actor, int):
            actors[seq] = actor
    return actors


def _record_payload(record: object) -> dict[str, Any]:
    """一条记录 → JSON-ready dict（票 01：**直接** ``model_dump``，不做 JSON 文本往返）。

    旧实现在这里 ``json.loads(record_json(record))``：``record_json`` 自己就是
    ``model_dump + json.dumps``，于是每条记录白白多出一次序列化 + 一次解析——``view()``
    每次下发都会走这条路径（一局 6612 条记录实测 128 ms）。形状与 ``record_json`` 逐键
    相同（``mode="json"`` 已经把枚举 / 元组都翻成 JSON 值），故下标方一个字不用改。

    非 pydantic 记录（旧夹具 / 第三方对象）保留文本往返兜底：它们没有 ``model_dump``。
    """
    dump = getattr(record, "model_dump", None)
    if callable(dump):
        payload = dump(mode="json")
        if isinstance(payload, dict):
            return payload
    return json.loads(record_json(record))


def _record_last_seq(records: Iterable[object]) -> int | None:
    """记录流里最大的 ``seq``（``header`` 没有 ``seq``，不参与）。

    派生通道的"与记录对齐"上界。只读属性，**不**做 ``model_dump`` / JSON 往返——记录是
    pydantic 对象或已序列化的 dict，``getattr`` / ``.get`` 就够（票 01）。
    """
    last_seq: int | None = None
    for record in records:
        if isinstance(record, Mapping):
            seq = record.get("seq")
        else:
            seq = getattr(record, "seq", None)
        if isinstance(seq, int) and (last_seq is None or seq > last_seq):
            last_seq = seq
    return last_seq


def _action_field(action: object, name: str) -> Any:
    """动作记录里的一个字段（记录模型 / 已序列化 JSON 同一份读法）；没有就是 ``None``。"""
    if isinstance(action, Mapping):
        return action.get(name)
    return getattr(action, name, None)


def visible_record(
    record: object,
    *,
    viewer: int | None = None,
    query_actors: Mapping[int, int] | None = None,
    state: Any = None,
    concealed: Mapping[int, str] | None = None,
) -> dict[str, Any] | None:
    """把一条记录转成**查看者可下发**的 JSON；``None`` = 这一条不下发。

    ``viewer`` = 人类座位（``serve.GameRegistry.view`` 传 ``session.human_seat``）；
    ``None`` = 没有查看者 / 全知——**不裁剪**（内部调用与测试需要原始记录）。

    ``query_actors`` = :func:`query_actor_map` 的结果（``query.seq → actor``）。有它才判得出
    一条 ``action`` 是不是查看者自己的：**只有自己的应答展开** ``card`` / ``choices`` /
    ``picks`` / ``targets`` / ``skill_id`` 等细节；别人的仍裁成 ``{kind, answers_seq}``。
    没有映射（或映射里查不到）时 fail-closed：按别人的应答裁剪。``state`` 只在展开自己的
    ``ActivateSkill`` 时用来补 :func:`_skill_display_name` 的中文名 ``skill_name``。

    ``card_move`` 的牌面按 spec §6 A3 的**区域可见性**脱敏（M5-09c）：公开区
    （弃牌堆 / 处理区 / 装备区 / 判定区）照常下发牌面；隐藏区（牌堆 / 手牌）之间的移动，
    除查看者自己的手牌 / 自己经手的移动外，``cards`` 抹成空列表并补 ``card_count``，
    让界面仍能说"移动了几张"。判据与事件流（``sgs_web.narrative``）共用
    :func:`sgs_web.visibility.cards_visible`——一处规则，两个下发口。

    M5 retro 补齐另两条 A3 面（同住 :mod:`sgs_web.visibility`）：

    - ``deck_shuffle`` 的 ``cards`` 是**整副洗后牌序**：人类查看者只收 ``card_count``，
      牌序与牌面一个都不下发（全知通道照旧）；
    - ``identity_deal`` 是**开局暗发**：只有本人与主公的身份可见，其余置 ``null``；
      "已亮明 / 阵亡"由公开的 ``identity_reveal`` 承载，故不需要回读引擎状态。

    **只过滤这一份下发**：引擎记录流保持全信息（回放 / 裁判需要），本函数不写任何状态、
    不改 schema、不碰金录像。``card_reveal``（M5-06 火攻亮牌）是公开事实，额外补展示字段。
    """
    kind = getattr(record, "kind", None)
    if kind == "query":
        return {"kind": "query", "seq": record.seq, "query": _query_summary(record.query)}
    # 票 01：直接取模型的 JSON 形态（不再走 `json.dumps + json.loads` 的文本往返）。
    raw = _record_payload(record)
    if kind == "delta":
        delta_type = raw.get("delta_type")
        if delta_type == "card_reveal":
            # M5-08a：亮牌是公开事实，中央舞台按**同一份卡面契约**画它（牌名 / 属性 / 描述）。
            # 只补展示字段，不移动、不写任何「已知」状态。
            card = raw.get("card")
            if isinstance(card, dict):
                raw["card"] = card_payload(CardRef.model_validate(card))
        elif delta_type == "pindian":
            # 拼点揭晓是公开事实（huo-taishici-xunyu 票 03 / 04）：两张牌按**正常卡面**
            # 下发——补展示字段（牌名 / 属性 / 转化来源 / 描述），牌上**有标签**时一并带上。
            # 拼点弹窗因此与手牌 / 舞台 / 亮牌共用同一个 `cardFaceNode`，不另起一套牌面。
            cards = raw.get("cards")
            if isinstance(cards, list):
                lookup = _card_lookup(state) if state is not None else None
                upgraded: list[Any] = []
                for card in cards:
                    if not isinstance(card, dict):
                        upgraded.append(card)
                        continue
                    payload = card_payload(CardRef.model_validate(card), lookup=lookup)
                    # 标签要读引擎状态；没有 state（裸询问 / 单测）时不猜、不造空键。
                    if state is not None:
                        _decorate_card_tags(payload, state, viewer)
                    upgraded.append(payload)
                raw["cards"] = upgraded
        elif delta_type == "card_move" and raw.get("cards"):
            hidden_seats = _concealed_hidden(raw, viewer, concealed)
            if hidden_seats is not None:
                # UI1/UI2：这张牌是**未验明**的【蛊惑】扣置牌 ⇒ **源牌牌面不下发**。
                # 画**声明的牌名**（无花色点数 + `concealed` 标记）——声明本身是公开事实，
                # 舞台与事件流因此照旧是"于吉把牌当【无中生有】扣置"这条故事线；解不出
                # 声明的牌型（旧形态记录）时退回"只给张数"。
                raw["card_count"] = len(raw["cards"])
                lookup = _card_lookup(state) if state is not None else None
                shown = [
                    hidden_guohuo_payload(
                        int(card["card_id"]), concealed[int(card["card_id"])], lookup=lookup
                    )
                    if concealed.get(int(card["card_id"]))
                    else None
                    for card in raw["cards"]
                    if isinstance(card, dict) and "card_id" in card
                ]
                raw["cards"] = [entry for entry in shown if entry is not None]
            elif not cards_visible(raw, viewer):
                # M5-09c：隐藏区的牌面不下发，只给张数——界面 / 事件流仍能说"移动了几张"。
                raw["card_count"] = len(raw["cards"])
                raw["cards"] = []
            else:
                # 公开区的移动牌也要**完整的卡面契约**（display_name / attribute / kind /
                # source_cards / description）。舞台是拿 card_move 建舞台项的：只给裸
                # CardRef 时它只能读数据名（"火杀""雷杀"两个字、没有属性 ⇒ 画成黑色），
                # 同一张牌在手牌里是蓝的、在台上是黑的（用户实机报障 2026-10-31）。
                lookup = _card_lookup(state) if state is not None else None
                raw["cards"] = [
                    card_payload(CardRef.model_validate(card), lookup=lookup)
                    if isinstance(card, dict)
                    else card
                    for card in raw["cards"]
                ]
        elif delta_type == "deck_shuffle" and raw.get("cards"):
            # A3：牌堆只给数量。整副洗后牌序是引擎 / 回放的全知事实，不是人类载荷。
            if not deck_cards_visible(viewer):
                raw["card_count"] = len(raw["cards"])
                raw["cards"] = []
        elif delta_type == "identity_deal":
            # A3：开局暗发。本人 / 主公可见，其余置空；亮明 / 阵亡走 identity_reveal。
            if not identity_visible(raw, viewer):
                raw["identity"] = None
        elif delta_type in ("tag_add", "tag_remove"):
            # spec §投影与记录流：标签的可见性由**标签类型**的登记表回答。记录流这条通道
            # 不传 ``face_visible``，故 FACE_VISIBLE 的标签 delta 仍整条裁掉；网页展示
            # 载荷另由 ``_card_tag_payloads`` 按牌面可见补 tags。手牌数两条都不改。
            if not tag_visible(raw.get("tag_type"), viewer):
                return None
    if kind == "timing" and raw.get("cards"):
        # 时机记录带的牌面也是**公开事实**（这次使用 / 判定亮出的那张牌）。补同一份展示
        # 契约（display_name / attribute / kind / source_cards / description）——不补的话
        # 舞台只能读记录里的数据名（"火杀""雷杀"两个字、没有属性 ⇒ 画成黑色），
        # 而卡面契约要求"单字【杀】+ 属性色"（用户实机报障 2026-10-31）。
        lookup = _card_lookup(state) if state is not None else None
        raw["cards"] = [
            card_payload(CardRef.model_validate(card), lookup=lookup)
            if isinstance(card, dict)
            else card
            for card in raw["cards"]
        ]
    if kind == "action":
        # 公开事实：谁应答了、被接受与否；**具体选了什么**只对人类座位自己的应答展开
        # （spec D13）。``ActionRecord`` 不带座位 ⇒ 座位取 ``answers_seq`` 指向的询问 actor；
        # 查不到 / 不是查看者 ⇒ fail-closed，保持 `{kind, answers_seq}`。
        action = raw.get("action")
        if isinstance(action, dict):
            actor = (query_actors or {}).get(action.get("answers_seq"))
            own = (
                viewer is not None
                and actor is not None
                and int(actor) == int(viewer)
            )
            if viewer is not None and not own:
                raw["action"] = {
                    "kind": action.get("kind"),
                    "answers_seq": action.get("answers_seq"),
                }
            elif own and state is not None and action.get("kind") == "ActivateSkill":
                # 技能名与技能飘字同源（接入层展示表）；查不到就不加键，前端退回
                # `view.skills` 再退回 skill_id（fail-visible，不猜名字）。
                name = _skill_display_name(state, str(action.get("skill_id") or ""))
                if name:
                    action["skill_name"] = name
    return raw


def serialize_record(
    record: object,
    *,
    viewer: int | None = None,
    query_actors: Mapping[int, int] | None = None,
    state: Any = None,
    concealed: Mapping[int, str] | None = None,
) -> dict[str, Any] | None:
    """``visible_record`` 的别名，名字更贴近"序列化"的调用点。"""
    return visible_record(
        record,
        viewer=viewer,
        query_actors=query_actors,
        state=state,
        concealed=concealed,
    )


def skill_events_after(
    records: Iterable[object],
    *,
    after: int,
    state: Any,
    activations: Iterable[Any] = (),
    query_actors: Mapping[int, int] | None = None,
) -> list[dict[str, Any]]:
    """从**未裁剪**的记录流派生技能发动事件（spec D8.2）：``seq > after`` 的那几条。

    判据是"**这一次技能真的发动了**"这条公共事实，两个来源合成**同一条**通道：

    1. **记录流**：已接受（``accepted === true``）且动作**点名了技能**的记录
       （动作里有非空 ``skill_id``）。今天有两种载体——``ActivateSkill``（主动 / 触发 /
       阶段体 / 提供者，载荷被 ``records`` 的 action 裁剪藏掉了，这里补回来）与
       ``SubstituteJudgeCard``（改判的【鬼才】/【鬼道】）。判据**不按动作种类打表**：
       将来的动作只要点名技能就自动进来。使用牌 / 打出牌 / 选牌不点名技能 ⇒ 一条都不产。
    2. **引擎的发动缓冲** ``Engine.skill_activations``（lei 票 06）：**记录面没有技能归属**
       的两类技能由引擎在"真的跑了"的那一刻记账——**锁定技**（派发环直接跑 handler，
       没有"是否发动"询问）与**转化技**（确认被解析成普通 ``UseCard`` / ``PlayCard``）。
       这条同样是"谁真的跑了"，不是按技能名打表；修饰器-only 技能从不经过派发环，故不会
       被记到（它们没有独立的"发动瞬间"）。
    修饰器（``ModifierPoint``）那类纯规则 / 数值修饰因此**天然**不产事件。

    - ``seat`` = **发动者座位**。记录流里取**应答那次询问的 actor**（``ActionRecord`` 不
      自带座位，但它的 ``answers_seq`` 指向产生它的那次 ``query`` 记录——记录流已下发的
      公开事实）；缓冲里由引擎按下发时就记下的技能持有者给。
    - ``targets`` = 这次发动**指定 / 作用的目标**（``seat:`` 前缀的选项按出现顺序；转化技
      取解析后动作自己的 ``targets``）；没有指定目标的发动（制衡 / 装备技 / 锁定技）是空
      列表——前端只飘名 + 白光，不画线。
    - ``name`` = 接入层展示表的中文名（与 ``build_skills`` / 叙事同源）；查不到时退回
      ``skill_id``，不让前端自建映射。

    ``records`` 可以是引擎记录模型，也可以是已序列化的 JSON（录像 / 合成夹具），两种
    形状走同一份派生。**不使用** ``picks``（那是成本牌），技能与目标都是公开事实，
    不需要按查看者过滤。

    ``activations`` 缺省为空：**只喂静态 jsonl 录像**的读取方（不重跑引擎）因此只少这一路
    表现（锁定技 / 转化技不飘字），记录流那一路照旧——这是缓冲**不进记录流**的必然边界。

    ``query_actors`` = 调用方**已经算好**的应答者表（``Session.window().query_actors``）。
    缺省时从传入的 ``records`` 现算（单测 / 静态录像的用法）；``serve.view`` 传全量表，
    因为本批 ``tail`` 里只有 ``seq > after`` 的记录、而应答指向的询问可能更早。
    """
    if query_actors is None:
        # 应答者映射与 `GameRegistry.view` 共用同一份实现（记录模型 / 已序列化 JSON 都吃）。
        # 先落成 list：`records` 可能是**一次性迭代器**（生成器），映射那一趟会把它耗掉。
        records = list(records)
        query_actors = query_actor_map(records)
    events: list[dict[str, Any]] = []
    for raw in records:
        # 票 01：记录模型直接读属性，**不**做 JSON 文本往返（旧实现在这里
        # `json.loads(record_json(raw))`，四条派生通道因此把整条记录流序列化两遍）。
        if isinstance(raw, Mapping):
            kind = raw.get("kind")
            seq = raw.get("seq")
            accepted = raw.get("accepted")
            action = raw.get("action")
        else:
            kind = getattr(raw, "kind", None)
            seq = getattr(raw, "seq", None)
            accepted = getattr(raw, "accepted", None)
            action = getattr(raw, "action", None)
        if kind == "query":
            continue
        if kind != "action" or not accepted:
            continue
        # 判据 = "这条已接受的动作**点名了技能**"（不按动作种类打表）：`ActivateSkill` 与
        # `SubstituteJudgeCard` 今天都满足；使用牌 / 打出牌 / 选牌没有这个键。
        skill_id = _action_field(action, "skill_id")
        if not str(skill_id or ""):
            continue
        if not isinstance(seq, int) or seq <= after:
            continue
        skill_id = str(skill_id)
        answers_seq = _action_field(action, "answers_seq")
        targets: list[int] = []
        for choice in _action_field(action, "choices") or ():
            if not isinstance(choice, str) or not choice.startswith(SEAT_PREFIX):
                continue
            try:
                targets.append(int(choice[len(SEAT_PREFIX) :]))
            except ValueError:
                continue
        events.append(
            {
                "seq": seq,
                "seat": query_actors.get(answers_seq),
                "skill_id": skill_id,
                "name": _skill_display_name(state, skill_id) or skill_id,
                "targets": targets,
            }
        )
    # 引擎的发动缓冲（记录面没有技能归属的那两类）：座位是"跑这条技能的人"，seq 锚在
    # 产生它的那条记录上，两条通道因此可以按同一套回放游标合成一条队列。
    for activation in activations or ():
        seq = getattr(activation, "seq", None)
        if not isinstance(seq, int) or seq <= after:
            continue
        skill_id = str(getattr(activation, "skill_id", "") or "")
        if not skill_id:
            continue
        events.append(
            {
                "seq": seq,
                "seat": getattr(activation, "seat", None),
                "skill_id": skill_id,
                "name": _skill_display_name(state, skill_id) or skill_id,
                "targets": [int(target) for target in getattr(activation, "targets", ())],
            }
        )
    # 两路按记录序号合并（稳定排序：同 seq 保持"记录流在前"的既有次序）。
    events.sort(key=lambda event: event["seq"])
    return events


def damage_events_after(
    records: Iterable[object],
    *,
    after: int,
    state: Any,
    events: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """从**未裁剪**的记录流派生伤害事实（damage-fx 票 02 / spec §Solution 2）：``seq > after``
    的那几条。

    判据、游标、去重口径**与** :func:`skill_events_after` 完全一致，不另造一套：

    - 事实来源是引擎的只增缓冲 ``Engine.damage_events``（票 01）——``HpChangeDelta`` 只有
      ``seat / before / after / amount``，来源与属性那两样记录面根本没有，而剑光要从
      "来源那张武将牌"飞到"中剑那张武将牌"；
    - ``seq`` 是引擎在伤害数值定稿那一刻取的最后一条记录序号，故与 ``records`` 共用同一个
      ``after`` 游标：``seq > after`` 才下发，轮询因此不会重复下发；
    - ``records`` 只用来定**这批的上界**（"与记录对齐"）：事件只在它的锚点记录已经进了这批
      时才下发，超出上界的留到下一次轮询——那时 ``after`` 仍小于它的 ``seq``，``seq > after``
      照成立，一条都不会丢。**传本批（``seq > after`` 的尾部窗口）就够**（票 01）：
      ``serve.view`` 传 ``Session.window().tail``；尾部非空时上界与整条流逐条相同，尾部为空
      时本批本来就没有可下发的事件；
    - ``state`` 只用来**夹座位**（``target`` / ``source`` 必须落在本局座位里）：越界条目是坏
      数据，宁可不下发也不让前端拿它去量一张不存在的武将牌。

    **不按视角过滤**：伤害是**公开事实**（血条本来就公开），与技能名同一档。``amount`` 是
    服务端算好的**全额**（护甲吸收的部分不减），前端不自己算、不自己减。

    ``events`` 缺省为空：**只喂静态 jsonl 录像**的读取方（不重跑引擎）因此只少这一路表现
    （没有剑光），记录流那一路照旧——这是缓冲**不进记录流**的必然边界，不是错误。
    """
    # 票 01：上界只读属性（`_record_last_seq`），不再把整条记录流做 JSON 文本往返。
    last_seq = _record_last_seq(records)
    seat_count = len(getattr(state, "players", ()) or ())
    out: list[dict[str, Any]] = []
    for event in events or ():
        seq = getattr(event, "seq", None)
        if not isinstance(seq, int) or seq <= after:
            continue
        if last_seq is not None and seq > last_seq:
            continue
        target = getattr(event, "target", None)
        amount = getattr(event, "amount", None)
        if target is None or amount is None:
            continue
        source = getattr(event, "source", None)
        source = None if source is None else int(source)
        target = int(target)
        if seat_count and not (
            0 <= target < seat_count and (source is None or 0 <= source < seat_count)
        ):
            continue
        nature = getattr(event, "nature", None)
        out.append(
            {
                "seq": seq,
                "source": source,
                "target": target,
                "amount": int(amount),
                # `DamageNature` 是 str 枚举：读它的 `.value`；缺声明（拼点类帧）保持 `None`，
                # 与"无属性"同义——按什么档播是前端的事，投影层不替它判。
                "nature": None if nature is None else str(getattr(nature, "value", nature)),
            }
        )
    # 同一批里多条伤害事件按记录序号排：前端"逐条播、相邻错开 120ms"（spec §演出口径）。
    out.sort(key=lambda event: event["seq"])
    return out


def life_loss_events_after(
    records: Iterable[object],
    *,
    after: int,
    state: Any,
    events: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """从**未裁剪**的记录流派生失去体力事实（vital-fx 票 01 / spec §Solution 1）：``seq > after``
    的那几条。

    判据、游标、去重口径**与** :func:`damage_events_after` 完全一致，只换事实来源与条目形状：

    - 事实来源是引擎的只增缓冲 ``Engine.life_loss_events``——记录面分不出「失去体力」与
      「受到伤害」：两者都落 ``FrameKind.LIFE_DECREASE`` / ``hp_change``，唯一判据是帧私有的
      ``LIFE_DECREASE_DAMAGE_KEY``，而那个键**不进记录流**；护甲全吸收的伤害又连一条
      ``hp_change`` 都没有；
    - ``seq`` 是引擎在体力写入那一刻取的最后一条记录序号，故与 ``records`` 共用同一个
      ``after`` 游标：``seq > after`` 才下发，轮询因此不会重复下发；
    - ``records`` 只用来定**这批的上界**（"与记录对齐"）：事件只在它的锚点记录已经进了这批
      时才下发，超出上界的留到下一次轮询——那时 ``after`` 仍小于它的 ``seq``，``seq > after``
      照成立，一条都不会丢。**传本批（``seq > after`` 的尾部窗口）就够**（票 01）：
      ``serve.view`` 传 ``Session.window().tail``；尾部非空时上界与整条流逐条相同，尾部为空
      时本批本来就没有可下发的事件；
    - ``state`` 只用来**夹座位**（``seat`` 必须落在本局座位里）：越界条目是坏数据，宁可不下发
      也不让前端拿它去量一张不存在的武将牌。

    **不按视角过滤**：体力是**公开事实**（血条本来就公开），与伤害同一档。失去体力**没有
    来源、没有属性**（不走伤害修正链、不能被护甲吸收），故条目只有 ``seq / seat / amount``；
    ``amount`` 是服务端算好的**真实掉血**（``before - after``），前端不自己算。

    ``events`` 缺省为空：**只喂静态 jsonl 录像**的读取方（不重跑引擎）因此只少这一路表现
    （没有墨），记录流那一路照旧——这是缓冲**不进记录流**的必然边界，不是错误。
    """
    # 票 01：上界只读属性（`_record_last_seq`），不再把整条记录流做 JSON 文本往返。
    last_seq = _record_last_seq(records)
    seat_count = len(getattr(state, "players", ()) or ())
    out: list[dict[str, Any]] = []
    for event in events or ():
        seq = getattr(event, "seq", None)
        if not isinstance(seq, int) or seq <= after:
            continue
        if last_seq is not None and seq > last_seq:
            continue
        seat = getattr(event, "seat", None)
        amount = getattr(event, "amount", None)
        if seat is None or amount is None:
            continue
        seat = int(seat)
        if seat_count and not 0 <= seat < seat_count:
            continue
        out.append({"seq": seq, "seat": seat, "amount": int(amount)})
    # 同一批里多条失去体力按记录序号排：前端"逐条播、相邻错开 120ms"（spec §演出口径）。
    out.sort(key=lambda event: event["seq"])
    return out


def card_events_after(
    records: Iterable[object],
    *,
    after: int,
    state: Any,
    events: Iterable[Any] = (),
) -> list[dict[str, Any]]:
    """从**未裁剪**的记录流派生卡牌使用 / 打出事实（chain-death-voices 票 03）：``seq > after``
    的那几条。

    判据、游标、去重口径**与** :func:`damage_events_after` 完全一致，只换事实来源：

    - 事实来源是引擎的只增缓冲 ``Engine.card_events``——记录流里一次使用只有
      ``card_move(reason=USE)``，而"这次**当成什么牌**用掉"（转化 / 纯虚拟的产物牌型）与
      "使用者是男是女"这两样事实记录面根本没有，猜不出来；
    - ``seq`` 是引擎在帧压栈之后取的最后一条记录序号，故与 ``records`` 共用同一个
      ``after`` 游标：``seq > after`` 才下发，轮询因此不会重复出声；
    - ``records`` 只用来定**这批的上界**（"与记录对齐"）：事件只在它的锚点记录已经进了这批
      时才下发，超出上界的留到下一次轮询。**传本批（``seq > after`` 的尾部窗口）就够**
      （票 01）：``serve.view`` 传 ``Session.window().tail``；尾部非空时上界与整条流逐条
      相同，尾部为空时本批本来就没有可下发的事件；
    - ``state`` 只用来**夹座位**（``seat`` 必须落在本局座位里）：越界条目是坏数据，宁可不下发
      也不让前端拿它去查一个不存在的座位。

    **不按视角过滤**：使用 / 打出是**公开事实**（牌本来就明置），与伤害同一档。
    ``card_type`` / ``card_name`` 原样透传**产物**（转化 / 纯虚拟是"当成什么牌"）；
    ``gender`` 原样透传使用者性别，``None``（无武将模式）不替前端回落。

    ``events`` 缺省为空：**只喂静态 jsonl 录像**的读取方（不重跑引擎）因此只少这一路表现
    （没有卡牌语音），记录流那一路照旧——这是缓冲**不进记录流**的必然边界，不是错误。
    """
    # 票 01：上界只读属性（`_record_last_seq`），不再把整条记录流做 JSON 文本往返。
    last_seq = _record_last_seq(records)
    seat_count = len(getattr(state, "players", ()) or ())
    out: list[dict[str, Any]] = []
    for event in events or ():
        seq = getattr(event, "seq", None)
        if not isinstance(seq, int) or seq <= after:
            continue
        if last_seq is not None and seq > last_seq:
            continue
        seat = getattr(event, "seat", None)
        if seat is None:
            continue
        seat = int(seat)
        if seat_count and not 0 <= seat < seat_count:
            continue
        card_type = getattr(event, "card_type", None)
        if not card_type:
            continue
        gender = getattr(event, "gender", None)
        out.append(
            {
                "seq": seq,
                "seat": seat,
                "card_type": str(card_type),
                "card_name": str(getattr(event, "card_name", "") or ""),
                # `Gender` 是 str 枚举：读它的 `.value`；无武将模式保持 `None`（前端回落 male）。
                "gender": None if gender is None else str(getattr(gender, "value", gender)),
                "virtual": bool(getattr(event, "virtual", False)),
            }
        )
    # 同一批里多条按记录序号排：前端按回放游标逐条放，不攒播（语音可重叠）。
    out.sort(key=lambda event: event["seq"])
    return out
