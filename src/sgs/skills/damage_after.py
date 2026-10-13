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

from sgs.data import SkillType
from sgs.effects import CarrierKind, locate
from sgs.state import (
    CardRef,
    GameState,
    MoveReason,
    SelectionSpec,
    Slot,
    Zone,
    ZoneRef,
    alive_seats,
)
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, HeroSkillDecl, Hook, SkillDef

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
class DamageAfterSkill(HeroSkillDecl):
    """B 族一条技能的**声明**：触发条件 + 回调（票 05 §要做什么 2 一行）。"""

    per_point: bool
    condition: Any
    handler: Any


def _jianxiong_condition(host: Any, frame: Any) -> bool:
    """【奸雄】的触发条件：**受伤的是持有者自己**，且造成伤害的那张牌**还在处理区**。

    持有者判据是必须的：派发环会给每个存活玩家的实例求条件（见 :func:`_hurt_owner`）。
    """
    return _hurt_owner(frame) and _in_processing(host.state, _damage_channel_card(frame))


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


def _hurt_owner(frame: Any) -> bool:
    """**这次伤害打在技能持有者身上吗**。

    伤害后技能的共同前提（官方【遗计】【奸雄】【反馈】开头都是"当**你**受到伤害后"）。
    判据读派发器在条件求值期写好的 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`
    （同 `move_after.py` 的口径）——``skill_owner`` 要到回调前才写，条件里读不到。

    **为什么必须自校**：``_dispatch`` 遍历**每个存活玩家**的实例，只按
    ``timing`` / ``per_point`` / ``condition`` 过滤。"条件恒真"因此等于"任何人挨打都触发"，
    而【遗计】的回调会把牌堆顶的牌**移动给持有者**——别人挨打、郭嘉白拿牌。
    """
    return frame.data.get(MATCH_OWNER_KEY) == frame.data.get("target")


def _yiji_condition(host: Any, frame: Any) -> bool:
    """【遗计】逐点触发：**持有者**每受一点伤害各成立一次。

    逐点由帧私有 ``damage_point_index`` 驱动（牌堆重洗由 ``peek_deck`` 沿用同一套口径）。
    """
    del host
    return _hurt_owner(frame)


def _yiji_keys(point: int) -> tuple[str, str]:
    """【遗计】在**第 ``point`` 点伤害**上的两个帧私有槽名：本点亮出的牌 / 本点已交出的牌 id。

    "逐点触发"意味着同一次伤害里有多轮独立的【遗计】；槽名带点号，两轮的观看与进度
    才不会互相顶掉（否则第 2 点会读到第 1 点剩的那张）。
    """
    return (f"{YIJI_SEEN_KEY}:{point}", f"{YIJI_GIVEN_KEY}:{point}")


def _yiji(host: Any, frame: Any) -> Any:
    """郭嘉【遗计】：观看牌堆顶 2 张，然后**逐张**交给任意角色（规则 993；逐点触发）。

    **只看不排**：走票 10 的授予通道（``peek_deck`` + 询问的 ``revealed``）——观看的
    牌面只出现在这次询问里，不进任何 ``PlayerView``、不落带牌面的 delta。

    **一次触发内逐张分配**（本轮报障修正）：官方原文是"观看牌堆顶的两张牌，然后**将这些牌**
    交给任意角色"——是复数的"这些牌"，不是一张。故本回调在**同一个伤害点**内循环：

        while 还有没交出的亮牌：
            选牌（``SELECT_CARDS``，面里只铺**剩余**亮牌，可选 ``Pass`` 收尾）
            → 选座次（``SELECT_OPTION``，选项 ``seat:N`` = 任意存活角色）
            → 把那张牌移进收牌人的手牌

    旧实现把"下一张"交给**下一个伤害点**驱动：1 点伤害只有一轮 ⇒ 第二张永远发不出去；
    2 点伤害则第二轮**重新观看牌堆顶**（第一张已被移走）⇒ 两张亮牌被换成另一对。

    ``Pass``（选牌面上放弃）= 剩下的亮牌留在牌堆顶、本点收尾。进度落在**帧私有键**里
    （点号 + 已交牌 id），故"挂起 → 应答 → 续跑"重入本回调时既不复述也不重发询问；
    移动本身由 ``move_in_frame`` 的重入守卫保证只发生一次（`CODING_STANDARDS.md` 规则 3）。
    """
    owner = frame.data["skill_owner"]
    point = damage_point_index(frame)
    seen_key, given_key = _yiji_keys(point)
    seen: tuple[CardRef, ...] | None = frame.data.get(seen_key)
    if seen is None:
        seen = host.peek_deck(frame, YIJI_COUNT)
        frame.data[seen_key] = seen
        frame.data[given_key] = ()
    if not seen:
        return None
    given: tuple[int, ...] = tuple(frame.data.get(given_key, ()))
    remaining = tuple(card for card in seen if card.card_id not in given)
    if not remaining:
        # 本点已经看完并分配完：**保留帧私有键**，重入直接作罢。
        # 这一点很关键：同一个伤害点的派发可能因**另一个钩子**（同族技能 / 移动后）挂起而
        # 被重入；键若被清掉，重入就会重新观看牌堆顶、把已经处理过的两张再问一遍。
        return None
    round_index = len(given)
    spec = SelectionSpec(zones=(Zone.HAND,), total=(1, 1), optional=True)
    picked = host.ask_selection(
        frame,
        key=f"yiji:pick:{point}:{round_index}",
        actor=owner,
        timing=Timing.DAMAGE_AFTER_TAKE,
        spec=spec,
        build_face=lambda: (
            tuple(Slot(ref=ZoneRef(Zone.HAND, owner), card=item) for item in remaining),
            tuple(item.card_id for item in remaining),
        ),
        revealed=remaining,
    )
    if host.suspended(picked):
        return "stay"
    if not picked:
        # "可以不交给任何人"：剩下的亮牌留在牌堆顶，本点收尾（键照留，重入仍返回空）。
        return None
    # ---- 交给谁（官方："将这些牌**交给任意角色**"）--------------------------------
    # 用 ``SELECT_OPTION`` 承载：它的 ``options`` 是**开放闭集**（任意字符串元组），
    # 不需要新造 QueryKind，界面上也已经是一条一个按钮、点得动的形态。
    # ``skill_id`` 带**点号 + 轮号**——``ask_option`` 的答案缓存键是
    # ``("option", skill_id, actor)``，同一点内的下一张若同名就会把第 2 轮读成第 1 轮的答案。
    choice = host.ask_option(
        frame,
        owner,
        Timing.DAMAGE_AFTER_TAKE,
        skill_id=f"yiji:give:{point}:{round_index}",
        options=tuple(f"seat:{seat}" for seat in alive_seats(host.state)),
    )
    if host.suspended(choice):
        return "stay"
    receiver = int(choice.choice.split(":", 1)[1])
    control = host.move_in_frame(
        frame,
        host.state.refs(picked),
        to_zone=ZoneRef(Zone.HAND, receiver),
        reason=MoveReason.PUT,
        owner=receiver,
        actor=owner,
    )
    if control == "stay":
        return "stay"
    # 交出成功才记进度：移动若挂起，重入会拿同一份缓存答案重试（移动幂等）。
    # ``ask_selection`` 返回的是**实体牌 id**（不是 ``CardRef``）。
    frame.data[given_key] = given + tuple(picked)
    return _yiji(host, frame)


YIJI_COUNT = 2
"""【遗计】"观看牌堆顶"的张数（规则 993：两张）。"""

YIJI_SEEN_KEY = "yiji_seen"
"""帧私有槽名前缀：本点伤害里【遗计】已经观看过的那批牌（槽名再带点号）。"""

YIJI_GIVEN_KEY = "yiji_given"
"""帧私有槽名前缀：本点伤害里【遗计】已经交出去的那批牌 id（槽名再带点号）。"""

DAMAGE_AFTER_SKILLS: tuple[DamageAfterSkill, ...] = (
    # 曹操【奸雄】：你可以获得造成此伤害的牌（规则 972）——每次伤害事件一次。
    DamageAfterSkill("caocao", "jianxiong", False, _jianxiong_condition, _jianxiong),
    # 司马懿【反馈】：你可以获得伤害来源的一张牌（规则 1014）——每次伤害事件一次。
    DamageAfterSkill("simayi", "fankui", False, _fankui_condition, _fankui),
    # 郭嘉【遗计】：观看牌堆顶两张牌，然后交给任意角色（规则 993）——每受到 1 点伤害一次。
    DamageAfterSkill("guojia", "yiji", True, _yiji_condition, _yiji),
)
"""B 族三条技能的逐字声明（票 05 §要做什么 2 的表）。"""

_REQUIREMENTS: dict[str, str] = {
    "jianxiong": "获得造成伤害的牌",
    "fankui": "获得伤害来源的一张牌",
    "yiji": "观看牌堆顶两张牌，然后逐张交给任意角色",
}
"""B 族三条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""


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
            requirement=_REQUIREMENTS[skill.short_id],
        )
        for skill in DAMAGE_AFTER_SKILLS
    )
