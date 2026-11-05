r"""**武将牌上的牌**（M6 票 10；spec D14 / §区域）：界陆逊【谦逊】与【连营】。

规则原文：`三国杀详细规则.txt` §十九 L2182–L2188（界陆逊）：

    谦逊：当一张延时锦囊牌或其他角色使用的普通锦囊牌对你生效时，若你是此牌的唯一目标，
        你可以将所有手牌置于你的武将牌上，然后你于当前回合结束时获得之。
    连营：当你失去最后的手牌后，你可以令至多 X 名角色各摸一张牌
        （X 为你此次失去的手牌数）。

**区域**（``sgs.state``）：``Zone.HERO`` + ``ZoneRef(zone, seat, skill_instance)``；区键 =
（座位 = 扣牌的宿主, 技能实例 = ``owner:skill_id``），故同一座位可同时存在多个互不相干的
放置区。放置与归还走引擎的两个通用入口（``Engine.place_on_hero_card`` /
``Engine.return_hero_card_zones``），技能只调它们、不直接改 ``hero_cards``。

**两条裁定的落地**（`06` §5「程序化约定与偏离一手」逐条登记）：

- 【谦逊】的"对你生效时"取 **``RESOLVE_EFFECT``（生效时）**：成为目标（B4 / B5）之后、
  卡牌效果（C4）之前——被【无懈可击】抵消的牌不产生这个时机，因此不会触发；
- 【连营】的 X = **此次移动里从手牌失去的张数**（``MoveEvent.cards``；移动的单一来源区
  保证它们都是手牌），X = 0 时连"是否发动"都不问。
"""

from __future__ import annotations

from typing import Any

from sgs.data import Category, SkillType, card_category
from sgs.effects import CarrierKind, behavior_for
from sgs.state import GameState, Pass, Zone
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    RUNNING_SKILL_KEY,
    HeroSkillDecl,
    Hook,
    SkillDef,
)
from .flow import SkillFlow, option_seat, seat_choice, zero_step_effect
from .move_after import MoveEvent, loses_card, move_event

__all__ = [
    "JIE_LIANYING",
    "JIE_QIANXUN",
    "QIANXUN_FLOW",
    "QIANXUN_PLACED_KEY",
    "hero_zone_skill_definitions",
]

JIE_QIANXUN = "hero.jie_luxun.qianxun"
"""界陆逊【谦逊】的全限定技能 id（§十九 L2183–L2185）。"""

JIE_LIANYING = "hero.jie_luxun.lianying"
"""界陆逊【连营】的全限定技能 id（§十九 L2186–L2188）。"""

QIANXUN_PLACED_KEY = "qianxun_placed"
"""帧私有：这一目标的手牌已经置于武将牌上（搬牌内联挂起后续跑不重搬）。

登在 ``sgs.engine.steps._PER_TARGET_FRAME_KEYS``：它是 RESOLVE 帧上的**逐目标**游标，
多目标结算推进游标时必须清掉（当前【谦逊】只对唯一目标成立，登记是结构性纪律）。
"""

_LIANYING_DRAWN_KEY = "lianying_drawn"
"""帧私有：已经摸过牌的座位（同一批里逐座推进，挂起续跑不重复摸）。"""


# ---------------------------------------------------------------------------
# 界陆逊【谦逊】：生效时把所有手牌置于武将牌上，当前回合结束时获得之
# ---------------------------------------------------------------------------


def _waiting_trick(state: GameState, frame: Any, owner: int) -> bool:
    """这次结算的牌是不是"对你生效的延时锦囊 / 其他角色使用的普通锦囊"（唯一判据）。

    三段都不读牌名白名单：

    - **唯一目标** = 你是 ``targets`` 里的唯一一张（多目标锦囊不触发）；
    - **延时锦囊**（``behavior.is_delayed``）不看使用者——它在判定阶段"对你生效"，
      来源本来就是 ``None``；
    - **普通锦囊**看两件事：大类是锦囊（``card_category``）∧ **使用者不是你**
      （"其他角色使用"；你自己的锦囊不触发）。
    """
    card = frame.data.get("card")
    if card is None:
        return False
    if tuple(frame.data.get("targets", ())) != (owner,):
        return False
    behavior = behavior_for(card)
    if behavior.is_delayed:
        return True
    if card_category(state.pack_id, card.type) is not Category.TRICK:
        return False
    return frame.data.get("source") != owner


def _qianxun_condition(host: Any, frame: Any) -> bool:
    """触发条件：生效时、唯一目标是我、牌面合格、**且手里有牌**（放空手没有意义）。

    持有者读 :data:`~sgs.skills.catalog.MATCH_OWNER_KEY`（条件求值期由派发环写入）；
    "生效时"这个时机本身已经排除了被无懈抵消 / 对目标无效的牌（引擎在 C1 / C2 就终止
    了结算，走不到 C3）。手里没牌 ⇒ 连"是否发动"都不问（付不起 / 无收益的前提）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if not _waiting_trick(host.state, frame, owner):
        return False
    return bool(host.state.player(owner).hand)


def _qianxun_place(host: Any, frame: Any) -> Any:
    """把**所有手牌**置于自己的武将牌上（区 = （自己, 【谦逊】实例））。"""
    if frame.data.get(QIANXUN_PLACED_KEY):
        # 搬牌可能内联派发"失去牌后"（【连营】会提问）并挂起：重入时手牌已经搬完，
        # 不能再搬第二遍（第二次会是空批，且会再触发一次"失去"）。
        return None
    owner = frame.data["skill_owner"]
    skill_id = frame.data[RUNNING_SKILL_KEY]
    frame.data[QIANXUN_PLACED_KEY] = True
    return host.place_on_hero_card(
        frame,
        owner=owner,
        skill_id=skill_id,
        holder=owner,
        cards=host.state.refs(tuple(host.state.player(owner).hand)),
    )


QIANXUN_FLOW = SkillFlow(name="qianxun", steps=(), effect=zero_step_effect(_qianxun_place))
"""【谦逊】的零步流水线：[是否发动] → 所有手牌置于武将牌上（无步骤）。"""


# ---------------------------------------------------------------------------
# 界陆逊【连营】：失去最后的手牌后，令至多 X 名角色各摸一张
# ---------------------------------------------------------------------------


def _lost_hand_cards(move: MoveEvent | None, owner: int) -> int:
    """这次移动里 ``owner`` 从**手牌**失去的张数（= 【连营】的 X）。

    ``move_card`` 保证一次移动只有一个来源区，故来源是 `HAND` 时 ``cards`` 全是手牌；
    来源不是手牌 ⇒ 0（没有"失去手牌"这回事；"X 可为 0"的裁定在这里落地）。
    """
    if move is None:
        return 0
    if move.from_zone.zone is not Zone.HAND or move.from_zone.seat != owner:
        return 0
    return len(move.cards)


def _lost_last_hand(move: MoveEvent | None, owner: int) -> int:
    """【连营】的触发判据：失去的是**最后**的手牌；返回 X（0 = 不发动）。

    **失去判据只有一份**（票 07）：与标准版【连营】同读
    :func:`~sgs.skills.move_after.loses_card`——牌离开自己的手牌区 / 装备区就算失去，
    **去向不限**（交给别人的手牌也算，用户 2026-11-03 裁定；旧实现把"去向是手牌"一律
    排除，界版因此在【顺手牵羊】一类效果上漏触发），唯一但书是"手牌 → **自己的**手牌"
    （重排一类，没离开）。界版在它之上只加自己那一半：**X = 本次从手牌失去的张数**；
    "移动后手牌为空"由 :func:`_lianying_condition` 读移动后的状态判。
    """
    if not loses_card(move, owner):
        return 0
    return _lost_hand_cards(move, owner)


def _lianying_condition(host: Any, frame: Any) -> bool:
    """触发条件：失去最后的手牌后、且 X > 0（X = 0 不发动，连"是否发动"都不问）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    if _lost_last_hand(move_event(frame), owner) <= 0:
        return False
    return not host.state.player(owner).hand


def _lianying_targets(state: GameState, chosen: tuple[int, ...]) -> tuple[int, ...]:
    """本步可选的角色：除本步已选外的存活座位（"至多 X 名角色"不排除自己）。"""
    return tuple(
        player.seat
        for player in state.players
        if player.alive and player.seat not in chosen
    )


def _lianying(host: Any, frame: Any) -> Any:
    """提交点：逐次选角色（可提前放弃），选中的角色各摸一张。

    手写帧内游标（与【寒冰剑】的多轮弃牌同姿态，不落成步骤原语）：

    - ``lianying_drawn`` = **已经摸过的座位**：摸牌走帧外 / 帧内移动，可能内联派发别的
      "失去牌后"钩子并挂起，续跑时据此不重复摸；
    - 选座位的询问名带**轮号**（``lianying:seat:<n>``）：同一步问多次、答案按
      ``(skill_id, actor)`` 在帧内分桶，故每次是独立的一问；``Pass``（``optional``）
      表示"不再选"；
    - X 由本次移动的载荷定格（不在选人过程中重算）：与触发条件读同一份事实。
    """
    owner = frame.data["skill_owner"]
    x = _lost_hand_cards(move_event(frame), owner)
    if x <= 0:
        return None
    drawn: list[int] = frame.data.setdefault(_LIANYING_DRAWN_KEY, [])
    chosen: list[int] = []
    while len(chosen) < x:
        pool = _lianying_targets(host.state, tuple(chosen))
        if not pool:
            break
        answer = host.ask_option(
            frame,
            owner,
            Timing.MOVE_AFTER,
            skill_id=f"lianying:seat:{len(chosen)}",
            options=tuple(seat_choice(seat) for seat in pool),
            optional=True,
        )
        if host.suspended(answer):
            return "stay"
        if isinstance(answer, Pass):
            break
        chosen.append(option_seat(answer))
    for seat in chosen:
        if seat in drawn:
            continue
        # 游标在**摸牌之前**前进：摸牌可能内联挂起，续跑据此跳过已摸的座位。
        drawn.append(seat)
        # 摸牌也算「获得牌」（票 24）：逐座摸牌会在中途触发【清俭】这类"获得牌后"钩子
        # 并挂起——挂起必须原样上抛，否则引擎带待答询问继续摸下一座，答复随后错配。
        if host.draw_for_hand(frame, seat, 1) == "stay":
            return "stay"
    return None


_LIANYING = HeroSkillDecl(hero_id="jie_luxun", short_id="lianying")
"""【连营】（界）的声明：手写多轮钩子（逐座选人 + 摸牌，不落成步骤原语）。"""


# ---------------------------------------------------------------------------
# 逐技能落位
# ---------------------------------------------------------------------------


def hero_zone_skill_definitions() -> tuple[SkillDef, ...]:
    """界陆逊两条技能的 :class:`~sgs.skills.catalog.SkillDef`。

    两条都是**非锁定技**（牌面写"可以"）：发动是询问出来的。``places_on_hero_card``
    只标在【谦逊】上——它是本票唯一的放置类技能，数据侧的三项声明由加载期与它核对。
    """
    qianxun = HeroSkillDecl(hero_id="jie_luxun", short_id="qianxun")
    return (
        SkillDef(
            skill_id=qianxun.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.RESOLVE_EFFECT,
                    condition=_qianxun_condition,
                    flow=QIANXUN_FLOW,
                ),
            ),
            places_on_hero_card=True,
            requirement=(
                "当一张延时锦囊牌或其他角色使用的普通锦囊牌对你生效时，若你是此牌的唯一目标，"
                "可以将所有手牌置于你的武将牌上，然后于当前回合结束时获得之"
            ),
        ),
        SkillDef(
            skill_id=_LIANYING.skill_id,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(Hook(Timing.MOVE_AFTER, condition=_lianying_condition, handler=_lianying),),
            requirement="当你失去最后的手牌后，可以令至多 X 名角色各摸一张牌（X = 此次失去的手牌数）",
        ),
    )
