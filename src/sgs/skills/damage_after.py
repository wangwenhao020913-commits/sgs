r"""伤害后触发（B 族，票 05）：逐点触发的判读、三条技能的载体与回调。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` / :mod:`sgs.skills.target_pool` /
:mod:`sgs.skills.move_after` 同构：一条 :class:`~sgs.skills.catalog.SkillDef` = 一个
``hero.<武将 id>.<短 id>`` 技能，定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是
**唯一入口**。

B 族的形态差别只有一处：它挂在 ``DAMAGE_AFTER_TAKE`` 上，而**这个时机在同一次伤害里
可能派发多轮**——引擎按伤害值逐轮派发，轮次写进帧私有 :data:`DAMAGE_POINT_KEY`
（票 05 §要做什么 1）。故"每次伤害事件一次"与"每受到 1 点伤害一次"是**技能定义上的
一个可选字段**（:attr:`~sgs.skills.catalog.Hook.per_point`），不是引擎里的特判：

=========================  ======  =========  ==========================================
技能                       锁定    per_point  效果
=========================  ======  =========  ==========================================
曹操【奸雄】               非锁定  ``False``  获得**造成此伤害的牌**（在处理区里找）
司马懿【反馈】             非锁定  ``False``  获得**伤害来源**的一张牌（手牌隐藏槽 + 装备可见槽）
郭嘉【遗计】               非锁定  ``True``   观看牌堆顶 2 张 → 交给任意角色（每轮一张）
=========================  ======  =========  ==========================================

三条都是**非锁定技**（技能面写了"你可以"）⇒ 发动是询问出来的。它们在回调里可以
**提问**（【反馈】选牌、【遗计】看牌与交给），返回 ``"stay"`` 让出控制权；引擎按
"同一轮重入不重问、轮次推进"的帧内游标范式继续（票 19 §8）。

**"获得造成伤害的牌"**读 ``frame.data["card"]``（= 官方六要素的**渠道牌**，与
``channel`` 同源）经 :func:`~sgs.effects.locate` 定位：牌在处理区里才拿得到；已被
【寒冰剑】类效果移走（或已进弃牌堆）⇒ **找不到就跳过**，不抛错。

**B 族的第四形态（【刚烈】：判定 + 由伤害来源二选一 + 条件伤害）确认缺席**（R6-Q3）：
夏侯惇不在 16 名名单，该形态是既有形状（判定帧 + ``ask_option`` + 条件伤害）的组合，
只丢一个样例；登记一句是为了让后来者读到"奸雄 / 反馈 / 遗计"时不会以为漏做。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, locate
from sgs.state import (
    CardRef,
    GameState,
    MoveReason,
    SelectionSpec,
    Slot,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import Hook, SkillDef

__all__ = [
    "DAMAGE_AFTER_SKILLS",
    "DAMAGE_POINT_KEY",
    "DAMAGE_RECORDED_KEY",
    "DamageAfterSkill",
    "damage_after_skill_definitions",
    "damage_point_index",
]

DAMAGE_POINT_KEY = "damage_point_index"
"""帧私有槽名：**本次伤害的第几点**（0 起）。

引擎在 ``DAMAGE_AFTER_TAKE`` 的每一轮写它（票 05 §要做什么 1），技能回调与
:func:`~sgs.skills.catalog._matching_hooks` 的"逐点"过滤都读它。帧私有数据不进记录流，
故这一步不动任何既有基准。
"""

DAMAGE_RECORDED_KEY = "damage_recorded"
"""帧私有槽名：**同一次伤害事件的时机记录是否已经落过**（票 05）。

同一次伤害事件只落一条 ``DAMAGE_AFTER_TAKE`` 记录（M2 逐字节不变，``amount=3`` 的
【闪电】也只有一条），逐点只体现为"这一条记录内多轮派发"。第 1 轮写它，第 2 轮起据此
把"这一轮只派发、不再记"传给派发口（``emit_record=False``）。
"""

def damage_point_index(frame: Any) -> int:
    """当前伤害点序号；不在 ``DAMAGE_AFTER_TAKE`` 派发中时是 ``0``（"第一轮"）。"""
    return frame.data.get(DAMAGE_POINT_KEY, 0)


# ---------------------------------------------------------------------------
# 三个可读判据（条件与回调共用同一处，避免"问不问"与"做了没做"各写一份）
# ---------------------------------------------------------------------------


def _damage_channel_card(frame: Any) -> CardRef | None:
    """造成这次伤害的**实体牌**（官方六要素的渠道牌）；无牌来源时是 ``None``。"""
    return frame.data.get("card")


def _in_processing(state: GameState, card: CardRef | None) -> bool:
    """``card`` 此刻是否**还在处理区**（【奸雄】拿得到它的唯一前提）。

    "找不到那张牌"（已被【寒冰剑】类效果移走 / 已进弃牌堆）一律算"不在处理区"：
    技能**跳过**而不是抛错（票 05 §验收 3）。
    """
    if card is None or card.is_virtual:
        return False
    try:
        return locate(state, card.card_id) == ZoneRef(Zone.PROCESSING)
    except ValueError:
        # ``locate`` 对"不在任何区域"的牌 fail-closed；这里把那个错误读成"拿不到"。
        return False


def _fankui_source(state: GameState, frame: Any, owner: int | None) -> int | None:
    """【反馈】的选取对象：**造成伤害的角色**；无来源 / 是自己 / 无牌可拿 ⇒ ``None``。

    无来源伤害（【闪电】一类）不进候选；"来源自己"也不进——技能面写的是"获得**伤害来源**
    的一张牌"，自己不是伤害来源。``owner`` 为 ``None``（条件求值期还没写
    ``skill_owner``，见 :func:`~sgs.skills.catalog._dispatch`）时只按"来源有牌"判。
    """
    source = frame.data.get("source")
    if source is None or source == owner:
        return None
    player = state.player(source)
    return source if player.hand or player.equip else None


# ---------------------------------------------------------------------------
# 逐技能落位（票 05 §要做什么 2 的表）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DamageAfterSkill:
    """B 族一条技能的**声明**：触发条件 + 回调（票 05 §要做什么 2 一行）。"""

    hero_id: str
    short_id: str
    per_point: bool
    condition: Any
    handler: Any

    @property
    def skill_id(self) -> str:
        """全限定技能 id（与数据侧共用 :func:`sgs.data.hero_skill_id` 这一处派生）。"""
        return hero_skill_id(self.hero_id, self.short_id)


def _jianxiong_condition(host: Any, frame: Any) -> bool:
    """【奸雄】的触发条件：造成伤害的那张牌**还在处理区**（找不到就跳过）。"""
    return _in_processing(host.state, _damage_channel_card(frame))


def _jianxiong(host: Any, frame: Any) -> None:
    """曹操【奸雄】：你可以获得造成此伤害的牌（规则 972）。"""
    owner = frame.data["skill_owner"]
    card = _damage_channel_card(frame)
    if not _in_processing(host.state, card):
        return
    assert card is not None  # _in_processing 已保证
    host.move_in_frame(
        frame,
        (card,),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )


def _fankui_condition(host: Any, frame: Any) -> bool:
    """【反馈】的触发条件：伤害来源存在、不是自己、且**有牌可拿**。

    ``condition`` 在派发器写 ``frame.data["skill_owner"]`` **之前**求值（
    :func:`~sgs.skills.catalog._dispatch` 的次序），故这里读不到持有者；判据退化成
    "来源有牌"——来源是不是自己由回调 :func:`_fankui` 再挡一次（那里持有者已经写好）。
    """
    return _fankui_source(host.state, frame, frame.data.get("skill_owner")) is not None


def _fankui_face(
    host: Any, source: int
) -> tuple[tuple[Slot, ...], tuple[int, ...]]:
    """【反馈】的选取面：来源的**手牌（隐藏槽）** + **装备区（可见槽）**。

    手牌槽带 ``Slot.card = None``（只有位置、没有牌面身份）——这与
    :meth:`~sgs.engine.game.Engine.selection_face` 的 ``reveal_hand=False`` 同一条口径，
    但这里**还要并上装备区**，且手牌的隐藏位序要用 ``select`` 子流打乱，故显式构造
    （不调 ``selection_face`` 以避免在技能层触碰引擎私有的随机句柄）。
    """
    player = host.state.player(source)
    slots: list[Slot] = []
    card_ids: list[int] = []
    order = list(range(len(player.hand)))
    host.shuffle_positions(order)
    for index in order:
        slots.append(Slot(ref=ZoneRef(Zone.HAND, source), card=None))
        card_ids.append(player.hand[index])
    for card_id in player.equip:
        slots.append(Slot(ref=ZoneRef(Zone.EQUIP, source), card=host.state.card(card_id)))
        card_ids.append(card_id)
    return tuple(slots), tuple(card_ids)


def _fankui(host: Any, frame: Any) -> Any:
    """司马懿【反馈】：你可以获得伤害来源的一张牌（规则 1014）。

    选取面 = 来源的手牌（隐藏槽）+ 装备区（可见槽）；选中并结算**之后**才揭晓那张手牌
    （揭晓 = 它进了自己的手牌，见 ``03`` §4 的"隐藏槽选中后揭晓"）。
    """
    owner = frame.data["skill_owner"]
    source = _fankui_source(host.state, frame, owner)
    if source is None:
        return None
    spec = SelectionSpec(zones=(Zone.HAND, Zone.EQUIP), total=(1, 1))
    picked = host.ask_selection(
        frame,
        key=f"fankui:{source}:{damage_point_index(frame)}",
        actor=owner,
        timing=Timing.DAMAGE_AFTER_TAKE,
        spec=spec,
        build_face=lambda: _fankui_face(host, source),
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        return None
    return host.move_in_frame(
        frame,
        host.state.refs(picked),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )


def _yiji_condition(host: Any, frame: Any) -> bool:
    """【遗计】逐点触发：每一轮都成立（牌堆重洗由 ``peek_deck`` 沿用同一套口径）。"""
    del host, frame
    return True


def _yiji(host: Any, frame: Any) -> Any:
    """郭嘉【遗计】：观看牌堆顶 2 张，然后交给任意角色（规则 993；逐点触发）。

    **只看不排**：走票 10 的授予通道（``peek_deck`` + 询问的 ``revealed``）——观看的
    牌面只出现在这次询问里，不进任何 ``PlayerView``、不落带牌面的 delta。**交给** =
    从授予的牌里选一张、``move_card`` 进自己的手牌（``PUT``）；``Pass`` = 这张不交给
    任何人（牌留在牌堆顶）。牌堆顶两张由**逐点触发的两轮**各交一张（每轮各看、各选一次）
    ——这是"观看 2 张并交给任意角色"在"一份询问 = 一次决策"下的直读，写进本模块的判据。
    """
    owner = frame.data["skill_owner"]
    seen: tuple[CardRef, ...] | None = frame.data.get(YIJI_SEEN_KEY)
    if seen is None:
        seen = host.peek_deck(frame, YIJI_COUNT)
        frame.data[YIJI_SEEN_KEY] = seen
    if not seen:
        return None
    spec = SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=True)
    picked = host.ask_selection(
        frame,
        key=f"yiji:{damage_point_index(frame)}",
        actor=owner,
        timing=Timing.DAMAGE_AFTER_TAKE,
        spec=spec,
        build_face=lambda: (
            tuple(Slot(ref=ZoneRef(Zone.HAND, owner), card=item) for item in seen),
            tuple(item.card_id for item in seen),
        ),
        revealed=seen,
    )
    if host.suspended(picked):
        return "stay"
    frame.data.pop(YIJI_SEEN_KEY, None)
    if not picked:
        # "可以不交给任何人"：这一轮作罢；下一轮的观看重新从（已变短的）牌堆顶取。
        return None
    return host.move_in_frame(
        frame,
        host.state.refs(picked),
        to_zone=ZoneRef(Zone.HAND, owner),
        reason=MoveReason.PUT,
        owner=owner,
        actor=owner,
    )


YIJI_COUNT = 2
"""【遗计】"观看牌堆顶"的张数（规则 993：两张）。"""

YIJI_SEEN_KEY = "yiji_seen"
"""帧私有槽名：本次伤害里【遗计】已经观看过的那批牌（逐点触发时每一轮各看一次）。"""

DAMAGE_AFTER_SKILLS: tuple[DamageAfterSkill, ...] = (
    # 曹操【奸雄】：你可以获得造成此伤害的牌（规则 972）——每次伤害事件一次。
    DamageAfterSkill("caocao", "jianxiong", False, _jianxiong_condition, _jianxiong),
    # 司马懿【反馈】：你可以获得伤害来源的一张牌（规则 1014）——每次伤害事件一次。
    DamageAfterSkill("simayi", "fankui", False, _fankui_condition, _fankui),
    # 郭嘉【遗计】：观看牌堆顶两张牌，然后交给任意角色（规则 993）——每受到 1 点伤害一次。
    DamageAfterSkill("guojia", "yiji", True, _yiji_condition, _yiji),
)
"""B 族三条技能的逐字声明（票 05 §要做什么 2 的表）。"""


def damage_after_skill_definitions() -> tuple[SkillDef, ...]:
    """B 族三条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    三条都是**非锁定技**（技能面写了"你可以"）；``carrier = HERO`` ⇒ 优先级档由载体派生；
    ``skill_type = NORMAL``（都不是主公技）；``per_point`` 由声明给出（【遗计】为真）。
    """
    return tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.DAMAGE_AFTER_TAKE,
                    skill.handler,
                    skill.condition,
                    per_point=skill.per_point,
                ),
            ),
        )
        for skill in DAMAGE_AFTER_SKILLS
    )
