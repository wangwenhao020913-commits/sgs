r"""失去牌后（D 族，票 07）：内联时机 ``MOVE_AFTER`` 的载荷、惰性守卫与两条技能。

本模块与 :mod:`sgs.skills.heroes` / :mod:`sgs.skills.active` / :mod:`sgs.skills.target_pool`
同构：一条 :class:`~sgs.skills.catalog.SkillDef` = 一个 ``hero.<武将 id>.<短 id>`` 技能，
定义与回调住本模块，:data:`sgs.skills.CATALOG` 仍是**唯一入口**。

D 族的形态差别只有一处：它**不建帧、也不产候选**，而是挂在牌移动的**唯一入口**
（``Engine.move_in_frame``）在 ``move_card`` 之后**内联派发**的 ``MOVE_AFTER`` 上
（票 07 §要做什么 1 / R1-Q2 / ADR-010）。故：

- **载荷**：一次移动的七要素（``cards`` / ``from_zone`` / ``to_zone`` / ``reason`` /
  ``owner`` / ``after_holder`` / ``actor``）装进 :class:`MoveEvent`，由引擎写进当前帧的
  ``frame.data`` 私有槽 :data:`MOVE_EVENT_KEY`——帧私有数据**不进记录流**（票 07 的
  两条代码事实之一），故这一步不动任何既有基准；
- **惰性守卫**：只有当前局面**存在注册在 ``MOVE_AFTER`` 上的实例**时才派发
  （:func:`move_after_providers`）。无武将模式 ⇒ 零派发 ⇒ M2 的 34 条基准一行不变
  （与【八卦阵】"选中才惰性压判定帧"同款先例）；
- **"失去"与移动原因无关**（R5-Q3）：判定只看 ``from_zone`` / ``to_zone`` / 移动后的持有，
  不看 :class:`~sgs.state.MoveReason`——使用 / 打出 / 弃置 / 交给 / 被获得 / **重铸** / 顶替
  全算，**摸牌不算**（牌堆 → 手牌）。M5-03 的 ``MoveReason.REFORGE``（铁索重铸）因此
  照常触发【连营】/【枭姬】：本族**不按原因过滤**，也不新增"弃置触发"的第二条通道——
  将来若出现弃置类触发，它必须显式比较 ``MoveEvent.reason``、把 REFORGE 排除在外。

两条技能的逐字语义（票 07 §要做什么 6 的表 + 票 23 的触发粒度）：

=========================  ======  ==================================================
技能                        锁定    触发条件
=========================  ======  ==================================================
陆逊【连营】                非锁定   ``from_zone == HAND ∧ to_zone != HAND ∧`` 移动后手牌为空
孙尚香【枭姬】              非锁定   ``from_zone == EQUIP``（含顶替：先旧后新的旧装备那条）
=========================  ======  ==================================================

两条的**触发次数**不同，且差别就在牌面的量词（`06`（§5「程序化约定与偏离一手」）的默认读法
（三））：【枭姬】写"失去装备区里的**一张**牌"⇒ 一次搬 N 张要触发 **N 次**
（:attr:`MoveAfterSkill.trigger_count` / :func:`_lost_equipment_count`）；【连营】写"失去
**最后的手牌**"（无限定词）⇒ 一次失去只触发**一次**（D-9，布尔判据 :func:`_emptied_hand`）。

两条都是**非锁定技**（技能面写了"你可以"）⇒ 发动是询问出来的，故 :func:`_dispatch` 会
先发一次 ``SKILL_ACTIVATION`` 询问。钩子**可以**提问并挂起：``move_in_frame`` 返回
``"stay"`` 时引擎把它记进 ``_move_pending``，由 :meth:`Engine._flush_pending_move` 用
**续跑帧**接着跑（M6 票 08 起；界【连营】的逐座选人与界【枭姬】的逐张再发动都走这条）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sgs.data import SkillType, card_category
from sgs.effects import CarrierKind, locate
from sgs.state import CardRef, GameState, MoveReason, Pass, Phase, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    HeroSkillDecl,
    Hook,
    SkillDef,
    SkillInstance,
    SkillSource,
    SkillSourceKind,
    dispatch_timing,
    mark_skill_used_this_turn,
    resolve_instances,
    skill_used_this_turn,
)
from .flow import PickCards, PickSeats, SkillFlow, zero_step_effect

__all__ = [
    "JIE_QINGJIAN",
    "LIANYING_FLOW",
    "MOVE_AFTER_SKILLS",
    "MOVE_EVENT_KEY",
    "MoveAfterSkill",
    "MoveEvent",
    "QINGJIAN_FLOW",
    "XIAOJI_FLOW",
    "dispatch_move_after",
    "lost_equipment_instances",
    "move_after_providers",
    "move_after_skill_definitions",
    "move_event",
]

_AGAIN_OPTION = "skill_again"
"""逐张触发技能（【枭姬】）第 2 张起的"是否再发动一次"里那个唯一选项的取值。

`skill_again` 是**界面词的键**：`tools/sgs_web/static/options.js:OPTION_LABELS` 把它翻成
「再发动一次」（答「取消」= 到此为止，牌面写的是"至多 X 次"）。

选项字符串只是**这一个询问**内的取值（UI 按技能名渲染标题，不直接显示它），故与
`seat:N` 一类取值同姿态：不是领域概念，也不进记录流的语义层。
"""

MOVE_EVENT_KEY = "move_event"
"""帧私有槽名：本次 ``MOVE_AFTER`` 的载荷（:class:`MoveEvent`）。

用**私有别键**而不是去覆盖 ``source`` / ``targets`` 等帧字段：那些键是帧自己的语义
（外层钩子与 ``_pending_timing`` 都读它们），就地覆写会让"一次移动"污染"这次使用"。
"""


@dataclass(frozen=True, slots=True)
class MoveEvent:
    """**一次移动**的帧私有载荷（票 07 §要做什么 4）。

    ``from_zone`` / ``to_zone`` 是 :class:`~sgs.state.ZoneRef`（区域坐标），
    ``owner`` = 这次移动的**归属角色**（判定牌 = 被判定者、弃置 / 使用 = 弃置者 / 使用者、
    摸牌 = 摸牌者），``actor`` = **操使者**（效果的执行人，可能不是归属者）。
    ``after_holder`` = **移动后的持有者座位**：``to_zone.seat`` 就是它（共享区为 ``None``）。
    """

    cards: tuple[CardRef, ...]
    from_zone: ZoneRef
    to_zone: ZoneRef
    reason: MoveReason
    owner: int | None
    after_holder: int | None
    actor: int | None
    causer: int | None = None
    """**这次移动的发起人**（M6 票 08 的加法；缺省 ``None`` = 未声明）。

    与 ``actor`` 不是一回事：弃置类效果常把 ``actor`` 写成**牌的归属者**（【寒冰剑】的
    目标"弃置自己的牌"、界【刚烈】的"你弃置来源的一张牌"都这样），而"是不是**其他角色**
    弃置的"只有发起人说得清。引擎在"装备区 → 弃牌堆"的移动前把**当时正在跑的技能持有者**
    （没有技能时退回帧的 ``source``）填进来，供事件接缝判"其他角色"；移动后的常规
    ``MOVE_AFTER`` 载荷不带它（缺省 ``None``）。
    """

    @property
    def card_ids(self) -> tuple[int, ...]:
        """本次移动的实体牌 id（"这批牌"的一切判定都读它）。"""
        return tuple(card.card_id for card in self.cards)


def move_event(frame: Any) -> MoveEvent | None:
    """当前帧上的移动载荷；不在 ``MOVE_AFTER`` 派发中时是 ``None``。"""
    return frame.data.get(MOVE_EVENT_KEY)


def lost_equipment_instances(
    state: GameState, event: MoveEvent | None
) -> tuple[SkillInstance, ...]:
    """**刚离开装备区**的实体装备，把它们自己的技能补成一次性的技能实例（M5-05a）。

    装备技能挂在装备牌上：``move_card`` 之后这张牌已不在装备区，
    :func:`~sgs.skills.resolve_instances` 因此看不见它——而"失去这张装备时回复 1 点"的
    【白银狮子】正需要在自己的**失去**上被派发。本函数按本次移动补出这些实例：

    - 只认 ``from_zone == EQUIP``（牌从装备区离开才算"失去装备"）；
    - 归属 = 失去它的座位（``from_zone.seat``），来源仍是那张**实体牌**
      （``source.card_id``，与载体解析同一份声明）；
    - 定义取 ``CATALOG["equip." + 牌名 id]`` —— 与 ``resolve_instances`` 同一个键规则，
      不写死任何牌名。没有装备技能的牌（或本来就没有钩子）自然不产出。

    这些实例**只在这一次派发里存在**（引擎不写 ``skill_state``，`prune_skill_state` 已经
    在搬牌后跑过），故不会在别处变成"幽灵装备"。
    """
    if event is None or event.from_zone.zone is not Zone.EQUIP:
        return ()
    seat = event.from_zone.seat
    if seat is None:
        return ()
    # 延迟导入 ``CATALOG``：它在 catalog 模块**末尾**才装配（本模块在那一行被 import），
    # 模块级取会在半初始化时撞上"没有 CATALOG"。
    from .catalog import CATALOG

    found: list[SkillInstance] = []
    for card in event.cards:
        definition = CATALOG.get(f"equip.{card.type}")
        if definition is None:
            continue
        found.append(
            SkillInstance(
                owner=seat,
                skill_id=definition.skill_id,
                source=SkillSource(SkillSourceKind.EQUIPMENT_CARD, card_id=card.card_id),
                definition=definition,
            )
        )
    return tuple(found)


def move_after_providers(
    state: GameState, *, timing: Timing = Timing.MOVE_AFTER, event: MoveEvent | None = None
) -> bool:
    """**惰性守卫**：当前局面有没有注册在 ``timing`` 上的实例（票 07 §要做什么 2）。

    ``False`` ⇒ ``move_in_frame`` 一个时机事件都不发、一个钩子都不跑——无武将模式的
    记录流因此逐字节不变（M2 的 34 条基准不重录）。判据用 ``resolve_instances``
    （载体并集 ∪ 身份门控的唯一真源），故"武将牌上根本没有这个技能"的角色不会把守卫点亮。

    ``event``（M5-05a 的加法，缺省 ``None``）：**刚被失去的装备**也是提供者——它的实例
    已不在 ``resolve_instances`` 里（见 :func:`lost_equipment_instances`），但这一次移动
    仍要替它派发"失去装备时"的钩子（白银狮子）。标准 108 没有任何装备带 ``MOVE_AFTER``
    钩子，故这条加法对既有记录流零影响。
    """
    return any(
        hook.timing is timing
        for instance in (*resolve_instances(state), *lost_equipment_instances(state, event))
        for hook in instance.definition.hooks
    )


def dispatch_move_after(host: Any, frame: Any) -> None:
    """在当前帧上派发 ``MOVE_AFTER``（引擎的 ``move_in_frame`` 在 ``move_card`` 之后调它）。

    守卫在 ``Engine.move_in_frame`` 一侧已经判过，这里只做通用派发；返回值被刻意丢弃——
    本族的钩子不得暂停（见模块 docstring）。

    M5-05a 起**刚离开装备区的装备**也参与本次派发（:func:`lost_equipment_instances`）：
    它们是"这一次移动"的提供者，而不是当前状态里的装备。
    """
    return dispatch_timing(
        host,
        frame,
        Timing.MOVE_AFTER,
        extra_instances=lost_equipment_instances(host.state, move_event(frame)),
    )


# ---------------------------------------------------------------------------
# 界夏侯惇【清俭】（M6 票 08）：**别人的回合**获得牌 ⇒ 交给另一名角色 ⇒ 当前回合角色 +X
#
# §十九 L2026–L2029：
#
#     每回合限一次，当你于你的摸牌阶段外获得牌后，你可以展示任意张牌并将这些牌交给
#     一名其他角色，然后当前回合角色本回合的手牌上限 +X（X 为给出的牌包含的类别数）。
#
# 三段判据都不认牌名：
#
# - **获得** = 移到手牌、且来源区**不是牌堆**（牌堆 → 手牌是摸牌，不算获得）；
# - **"于你的摸牌阶段外"** = 不是"当前回合是你 ∧ 阶段是摸牌"（在别人的回合里摸到牌
#   也算"你的摸牌阶段外"）；
# - **每回合限一次** = ``skill_state`` 的 :data:`~sgs.skills.catalog._TURN_USED` 位
#   （清俭在**任何人的**回合都可能触发，故不能复用"本出牌阶段"的阶段位）。
#
# 加式落在**当前回合角色**身上（不是清俭的持有者）——这是 D11 双轴的"跨座位"那一半；
# 作用对象是提交那一刻的 ``state.turn_seat``，此后不重定向（`06`（§5「程序化约定与偏离一手」） 登记了"中途阵亡"的
# 裁定），增量记在 :attr:`~sgs.state.Player.hand_limit_bonus`、回合末过期。
# ---------------------------------------------------------------------------

JIE_QINGJIAN = "hero.jie_xiahoudun.qingjian"
"""界夏侯惇【清俭】的全限定技能 id（§十九 L2026–L2029）。"""

_QINGJIAN_BONUS_KEY = "qingjian_bonus"
"""帧私有：+X 与"本回合已用"位已经落过（搬牌挂起后的续跑不重复加）。"""

_QINGJIAN_GIVEN_KEY = "qingjian_given"
"""帧私有：那批牌已经交出去（重入不重搬）。"""

_QINGJIAN_REVEALED_KEY = "qingjian_revealed"
"""帧私有：那批牌已经展示过（重入不重复落 ``card_reveal``）。"""

_QINGJIAN_GROUPS_KEY = "qingjian_groups"
"""帧私有：选中的牌按**来源区域**分组后的 ``[(zone, (card_id, ...)), ...]``。

``move_card`` 一次只能有一个来源区（手牌 / 装备区各一批）；分组也在帧内定格，
重入不重算。
"""

_QINGJIAN_MOVED_KEY = "qingjian_moved"
"""帧私有：已搬完的来源组数（搬牌内联挂起后据此续搬，不重搬已完成的组）。"""


def _other_alive_seats(
    state: GameState, frame: Any, owner: int, chosen: tuple[int, ...]
) -> tuple[int, ...]:
    """【清俭】"交给一名**其他**角色"的目标池：除持有者外的存活座位（座位序）。

    签名与 :data:`~sgs.skills.flow.SeatPool` 对齐（``chosen`` 对单选无意义）。
    """
    del frame, chosen
    return tuple(p.seat for p in state.players if p.alive and p.seat != owner)


def _qingjian_is_gain(event: MoveEvent | None, owner: int) -> bool:
    """这次移动对 ``owner`` 算不算"获得牌"：移到手牌 ∧ 来源区不是牌堆。"""
    if event is None:
        return False
    if event.to_zone.zone is not Zone.HAND or event.to_zone.seat != owner:
        return False
    return event.from_zone.zone is not Zone.DECK


def _qingjian_condition(host: Any, frame: Any) -> bool:
    """触发条件：**获得牌后**、不在自己的摸牌阶段、且本回合还没用过。

    持有者读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环写）；
    获得判据读本次移动的载荷（:func:`move_event`）。不成立就连"是否发动"都不问。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if not _qingjian_is_gain(move_event(frame), owner):
        return False
    state = host.state
    if state.turn_seat == owner and state.phase is Phase.DRAW:
        return False
    return not skill_used_this_turn(state, owner, JIE_QINGJIAN)


def _qingjian_effect(host: Any, frame: Any, answers: Any) -> Any:
    """提交点：展示并交出选中的牌，然后给**当前回合角色** +X（X = 类别数）。

    交出那一步可能内联派发"失去牌后"并挂起（接收者若也有"失去牌后"类技能），故
    "已经给过 / 已经加过"各一个帧私有游标：续跑既不重搬也不重复加。类别数读数据层
    的 :func:`~sgs.data.card_category`（基本 / 锦囊 / 装备三档），不抄牌名清单。
    """
    owner = frame.data["skill_owner"]
    cards = tuple(int(card_id) for card_id in answers["card"])
    target = int(answers["seat"][0])
    state = host.state
    if not frame.data.get(_QINGJIAN_REVEALED_KEY):
        frame.data[_QINGJIAN_REVEALED_KEY] = True
        for card_id in cards:
            host.reveal_card(frame, owner, state.card(card_id))
    if not frame.data.get(_QINGJIAN_GIVEN_KEY):
        groups = frame.data.get(_QINGJIAN_GROUPS_KEY)
        if groups is None:
            buckets: dict[Zone, list[int]] = {}
            for card_id in cards:
                buckets.setdefault(locate(state, card_id).zone, []).append(card_id)
            groups = tuple((zone, tuple(ids)) for zone, ids in buckets.items())
            frame.data[_QINGJIAN_GROUPS_KEY] = groups
        moved = int(frame.data.get(_QINGJIAN_MOVED_KEY, 0))
        while moved < len(groups):
            _zone, card_ids = groups[moved]
            control = host.move_in_frame(
                frame,
                state.refs(card_ids),
                to_zone=ZoneRef(Zone.HAND, target),
                reason=MoveReason.PUT,
                owner=target,
                actor=owner,
            )
            if control == "stay":
                return "stay"
            moved += 1
            frame.data[_QINGJIAN_MOVED_KEY] = moved
        frame.data[_QINGJIAN_GIVEN_KEY] = True
    if not frame.data.get(_QINGJIAN_BONUS_KEY):
        frame.data[_QINGJIAN_BONUS_KEY] = True
        state = host.state
        categories = {
            card_category(state.pack_id, state.card(card_id).type) for card_id in cards
        }
        if state.turn_seat is not None:
            host.add_hand_limit_bonus(state.turn_seat, len(categories))
        mark_skill_used_this_turn(state, owner, JIE_QINGJIAN)
    return None


QINGJIAN_FLOW = SkillFlow(
    name="qingjian",
    steps=(
        # 「展示任意张牌」：牌前没写区域 ⇒ 默认手牌 + 装备区（至少 1 张；"给空"不是成本）。
        PickCards(zones=(Zone.HAND, Zone.EQUIP), lo=1, hi=None, key="card"),
        # 「交给一名其他角色」：不能给自己。
        PickSeats(lo=1, hi=1, pool=_other_alive_seats, key="seat"),
    ),
    effect=_qingjian_effect,
)
"""【清俭】的两步流水线：展示任意手牌 / 装备 → 交给一名其他角色 → 当前回合角色 +X。"""


# ---------------------------------------------------------------------------
# 逐技能落位（票 07 §要做什么 6 的表）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MoveAfterSkill(HeroSkillDecl):
    """D 族一条技能的**声明**：触发条件 + 摸牌数 + 执行路径（票 07 §要做什么 6 一行）。

    ``flow``（W6-01）：零步技能的 ``SkillFlow``（``steps=()``），与
    :class:`~sgs.skills.damage_after.DamageAfterSkill` 的字段同形；旧回调
    :meth:`handle` 保留并由 flow 的 ``effect`` 原样转调。

    ``trigger_count``（票 23 的加法，缺省 ``None`` = 一次移动至多触发一次）是**逐张触发**的
    判据：牌面写"失去装备区里的**一张**牌"时，一次搬一**批**牌要按张数各触发一次
    （【枭姬】，D-8 官方明确）；写"失去**最后的手牌**"时问的是"这次移动"这一个事实
    （【连营】/界【连营】），故两条技能**不能共用**一个布尔判据。
    """

    draws: int
    trigger: Any
    flow: Any = None
    trigger_count: Any = None

    def count(self, state: GameState, move: MoveEvent, owner: int | None) -> int:
        """这次移动对这条技能成立**几次**（缺省 = 布尔判据的 0 / 1）。"""
        if owner is None:
            return 0
        if self.trigger_count is None:
            return 1 if self.trigger(state, move, owner) else 0
        return int(self.trigger_count(state, move, owner))

    def fires(self, state: GameState, move: MoveEvent, owner: int | None) -> bool:
        """本次移动对这条技能（持有者 ``owner``）算不算"失去"（只看区域与**移动后**的持有）。"""
        return self.count(state, move, owner) > 0

    def condition(self, host: Any, frame: Any) -> bool:
        """钩子触发条件（``Hook.condition``）：不成立就连"是否发动"询问都不发。

        条件读写 :func:`move_event` 给出的本次移动载荷——没有载荷（不在``MOVE_AFTER``
        派发中）即不成立；**持有者**读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`
        （条件求值期由派发器写，票 14 §2）——``skill_owner`` 要到回调前才写。
        """
        move = move_event(frame)
        if move is None:
            return False
        return self.fires(host.state, move, frame.data.get(MATCH_OWNER_KEY))

    def handle(self, host: Any, frame: Any) -> Any:
        """钩子回调：条件成立则摸 ``draws`` 张（短摸 ⇒ 平局，与摸牌阶段同口径）。

        **逐张触发的技能**（``trigger_count`` 非空，目前只有【枭姬】）在这里补齐"至多 X 次"：
        派发环已经问过第 1 张的"是否发动"，其余每张各再问一次（``optional`` 的
        ``SELECT_OPTION``，答放弃即停 = "至多"）。游标 ``<skill id>:activations`` 记**已经
        摸过几次**，且**在摸牌之前**前进——摸牌可能内联派发别的"失去牌后"并挂起，续跑据此
        既不重问、也不重摸。
        """
        owner = frame.data["skill_owner"]
        move = move_event(frame)
        total = 1 if self.trigger_count is None else self.count(host.state, move, owner)
        key = f"{self.skill_id}:activations"
        done = int(frame.data.get(key, 0))
        while done < total:
            if done:
                # 第 2 张起：每一张都是一次**独立**的"是否发动"（官方：至多发动 X 次）。
                answer = host.ask_option(
                    frame,
                    owner,
                    Timing.MOVE_AFTER,
                    skill_id=f"{self.skill_id}:again:{done}",
                    options=(_AGAIN_OPTION,),
                    optional=True,
                )
                if host.suspended(answer):
                    return "stay"
                if isinstance(answer, Pass):
                    break
            frame.data[key] = done + 1
            host.draw_for_hand(frame, owner, self.draws)
            done += 1
        return None


def _emptied_hand(state: GameState, move: MoveEvent, owner: int) -> bool:
    """陆逊【连营】的触发条件（票 07 §要做什么 6 的表，第一行）。

    ``from_zone == HAND ∧ to_zone != HAND ∧`` **移动后** ``hand`` 为空。判定读移动后的
    状态：``move_card`` 已经在派发之前跑完，故"手牌为空"就是移动后的手牌。

    **只认技能持有者本人**（票 14 §2）：失去牌的那个座位取 ``move.from_zone.seat``
    （"归属" ``move.owner`` 在【顺手牵羊】一类里写的是**拿走牌的人**），它必须等于
    ``owner``——否则别人掉光手牌会把陆逊的【连营】点亮。
    """
    if move.from_zone.zone is not Zone.HAND or move.to_zone.zone is Zone.HAND:
        return False
    if move.from_zone.seat != owner:
        return False
    return not state.player(owner).hand


def _lost_equipment(state: GameState, move: MoveEvent, owner: int) -> bool:
    """孙尚香【枭姬】的触发条件（票 07 §要做什么 6 的表，第二行）。

    只看 ``from_zone == EQUIP``：与去向、与 :class:`~sgs.state.MoveReason` 都无关——
    弃置 / 被【顺手牵羊】拿走 / **顶替**（同槽先旧后新的旧装备那条移动）都算失去。
    "装备从牌堆进入装备区"是 ``from_zone != EQUIP``，天然不触发。

    **只认技能持有者本人**（票 14 §2）：``from_zone.seat == owner``（同【连营】，
    不用"归属" ``move.owner``——拆 / 顺写的是操使者）。

    逐张触发的**次数**见 :func:`_lost_equipment_count`；本函数是它的布尔版，两者共用
    同一句区域判据。
    """
    del state
    return move.from_zone.zone is Zone.EQUIP and move.from_zone.seat == owner


def _lost_equipment_count(state: GameState, move: MoveEvent, owner: int) -> int:
    """【枭姬】这次移动触发**几次** = 从**自己的装备区**失去的**张数**（D-8，官方明确）。

    牌面是"当你失去装备区里的**一张**牌后"⇒ 逐张触发；而一次弃置 / 一次【寒冰剑】都只搬
    **一批**牌（一次 ``move_card``），故次数要数 ``len(move.cards)``，不能把"一次移动"当成
    "一次失去"。《官方规则集》「依次」条的原文举例就是「装备区里有 X 张牌的主公孙尚香……
    一次性弃置所有的牌，然后至多可以发动 X 次【枭姬】摸 2X 张牌」。

    反面对照：陆逊【连营】问的是"失去**最后的手牌**"这**一个**事实（D-9：一次失去只触发
    一次），故它用布尔判据 :func:`_emptied_hand`，不走本函数——同一批移动对两条技能是
    "N 次"与"1 次"的差别，这正是 :attr:`MoveAfterSkill.trigger_count` 存在的理由。
    """
    if move.from_zone.zone is not Zone.EQUIP or move.from_zone.seat != owner:
        return 0
    return len(move.cards)


# W6-01 / W6-07：零步流水线的 effect 由 :func:`sgs.skills.flow.zero_step_effect` 生成，
# 只把旧回调 :meth:`MoveAfterSkill.handle` 包一层。**晚绑定**是必须的：``_LIANYING`` /
# ``_XIAOJI`` 又要引用这两条流水线（``flow=…``），故绑定方法只能在它们定义**之后**取。
def _lianying_delegate(host: Any, frame: Any) -> None:
    """【连营】的旧回调（``_LIANYING`` 在下方定义 ⇒ 取绑定方法要晚一步）。"""
    return _LIANYING.handle(host, frame)


def _xiaoji_delegate(host: Any, frame: Any) -> None:
    """【枭姬】的旧回调（同 :func:`_lianying_delegate`）。"""
    return _XIAOJI.handle(host, frame)


LIANYING_FLOW = SkillFlow(name="lianying", steps=(), effect=zero_step_effect(_lianying_delegate))
"""【连营】的零步流水线（W6-01）：[是否发动] → 摸一张牌（无步骤）。

必须是**模块级具名常量**：``tests/test_ask_points.py`` 只发现模块级 ``SkillFlow`` 实例，
并拒绝"注册了但不在模块里"的 flow（先例 :data:`~sgs.skills.damage_after.FANKUI_FLOW`）。
"""

XIAOJI_FLOW = SkillFlow(name="xiaoji", steps=(), effect=zero_step_effect(_xiaoji_delegate))
"""【枭姬】的零步流水线（W6-01）：[是否发动] → 摸两张牌（无步骤）。"""


_LIANYING = MoveAfterSkill(
    # 陆逊【连营】：当你失去最后的手牌时，你可以摸一张牌（规则 1030）。
    hero_id="luxun",
    short_id="lianying",
    draws=1,
    trigger=_emptied_hand,
    flow=LIANYING_FLOW,
)
_XIAOJI = MoveAfterSkill(
    # 孙尚香【枭姬】：当你失去装备区里的一张牌后，你可以摸两张牌（规则 1054）。
    # **逐张触发**：一次失去两张 ⇒ 至多发动两次（票 23 按 D-8 的官方「依次」条落
    # ``trigger_count``；旧实现把"一次移动"当成"一次失去"，只触发一次）。
    hero_id="sunshangxiang",
    short_id="xiaoji",
    draws=2,
    trigger=_lost_equipment,
    flow=XIAOJI_FLOW,
    trigger_count=_lost_equipment_count,
)
_JIE_XIAOJI = MoveAfterSkill(
    # 界孙尚香【枭姬】（M6 票 17）：§十九 L2177 与标准版**逐字相同** ⇒ 同一个触发谓词、
    # 同一条零步流水线（界版只是另一个武将 id；"失去装备区里的一张牌"的判据只有一份）。
    hero_id="jie_sunshangxiang",
    short_id="xiaoji",
    draws=2,
    trigger=_lost_equipment,
    flow=XIAOJI_FLOW,
    trigger_count=_lost_equipment_count,
)

MOVE_AFTER_SKILLS: tuple[MoveAfterSkill, ...] = (_LIANYING, _XIAOJI, _JIE_XIAOJI)
"""D 族三条技能的逐字声明（票 07 §要做什么 6 的表 + M6 票 17 界孙尚香【枭姬】）。"""

_REQUIREMENTS: dict[str, str] = {
    "lianying": "摸一张牌",
    "xiaoji": "摸两张牌",
}
"""D 族两条技能的"需要做什么"文案（item 4）；键 = 短 id，与声明同处。"""


def move_after_skill_definitions() -> tuple[SkillDef, ...]:
    """D 族三条技能的 :class:`~sgs.skills.catalog.SkillDef`（每条技能一个）。

    三条都是**非锁定技**（技能面写了"你可以"，故 ``locked=False``：发动是询问出来的）；
    ``carrier = HERO`` ⇒ 优先级档由载体派生；``skill_type = NORMAL``（都不是主公技）。
    M6 票 08 的【清俭】不走 :class:`MoveAfterSkill` 的"摸 N 张"声明（它的效果是两步
    流水线），故单独构造一条。
    """
    declarations = tuple(
        SkillDef(
            skill_id=skill.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(Timing.MOVE_AFTER, condition=skill.condition, flow=skill.flow),
            ),
            requirement=_REQUIREMENTS[skill.short_id],
        )
        for skill in MOVE_AFTER_SKILLS
    )
    return (
        *declarations,
        SkillDef(
            skill_id=JIE_QINGJIAN,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.MOVE_AFTER,
                    condition=_qingjian_condition,
                    flow=QINGJIAN_FLOW,
                ),
            ),
            requirement=(
                "当你于你的摸牌阶段外获得牌后，可以展示任意张牌交给一名其他角色，"
                "令当前回合角色本回合的手牌上限 +X（X 为给出的牌包含的类别数）"
            ),
        ),
    )
