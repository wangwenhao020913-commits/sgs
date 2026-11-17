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

from sgs.data import Category, SkillType, card_category, hero_skill_id
from sgs.effects import (
    CarrierKind,
    Modifier,
    ModifierContext,
    ModifierPoint,
    behavior_for,
    is_slash,
)
from sgs.state import GameState, MoveReason, Zone, ZoneRef
from sgs.timing import Timing

from .catalog import (
    MATCH_OWNER_KEY,
    RUNNING_SKILL_KEY,
    HeroSkillDecl,
    Hook,
    SkillDef,
    hero_card_zone_key,
)
from .flow import FlowAnswers, PickCards, SkillFlow, zero_step_effect
from .move_after import MoveEvent, loses_card, move_event

__all__ = [
    "JIE_LIANYING",
    "SIDI",
    "SIDI_LIMIT_FLOW",
    "SIDI_PLACE_FLOW",
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
    """提交点：**一次**选多名角色（至多 X 名、可放弃），选中的角色各摸一张。

    手写帧内游标（与【寒冰剑】的多轮弃牌同姿态，不落成步骤原语）：

    - ``lianying_drawn`` = **已经摸过的座位**：摸牌走帧外 / 帧内移动，可能内联派发别的
      "失去牌后"钩子并挂起，续跑时据此不重复摸；
    - 选座位的询问由 :meth:`Engine.ask_seats` **一次问齐**（table-ux-overhaul 票 13：
      ``[1, X]`` + ``optional`` = 「至多 X 名」，答案是一个座位集合，不再逐座发问）；
    - X 由本次移动的载荷定格（不在选人过程中重算）：与触发条件读同一份事实。

    **先选齐人、再逐座结算**（与【除疠】同姿态）：摸牌可能内联挂起，故"选人"与"摸牌"
    两段分开，摸牌段靠 ``lianying_drawn`` 幂等。
    """
    owner = frame.data["skill_owner"]
    x = _lost_hand_cards(move_event(frame), owner)
    if x <= 0:
        return None
    drawn: list[int] = frame.data.setdefault(_LIANYING_DRAWN_KEY, [])
    chosen: tuple[int, ...] = ()
    pool = _lianying_targets(host.state, ())
    if pool:
        answer = host.ask_seats(
            frame,
            owner,
            Timing.MOVE_AFTER,
            skill_id="lianying:seat",
            pool=pool,
            lo=1,
            hi=x,
            optional=True,
        )
        if host.suspended(answer):
            return "stay"
        chosen = tuple(answer)
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
# 一将成名-2014·移动版 曹真【司敌】（yijiang-2014-round3 票 03；用户 2026-11-17 签）
#
#     ①当你使用【闪】时，或当其他角色于你的回合内使用【闪】时，你可以将牌堆顶的一张牌
#       置于你的武将牌上称为“司敌”牌；
#     ②其他角色的出牌阶段开始时，你可以移去一张“司敌”牌，令其本阶段使用【杀】的次数上限-1。
#
# 平台口径（用户 Q1 = A 移动版）：本将住**独立移动版表**（``heroes.yijiang_2014_mobile.json``、
# ``version = "yijiang-2014-mobile"``、显示名「曹真移动版」），OL 的【司敌】是完全另一条技能
# （见 ``.scratch/yijiang-2014-round3/issues/04-caozhen-a1-ruling.md``）。
#
# 两处"本阶段 / 武将牌上的牌"的作用域：
#
# - ① 的放置走 M6 票 10 的通用入口（``Engine.place_on_hero_card``：来源 = ``Zone.DECK``，
#   区键 = ``owner:skill_id``），数据侧声明 ``return_timing = never``（牌一直留着，直到 ② 移去）；
# - ② 的「移去」= 武将牌上的牌 → 弃牌堆（规则 §15.2；``MoveReason.PUT``，样板【狂风】/【大雾】）；
#   被限者的 -1 落座位字段 ``Player.sidi_slash_penalty``，作用域 = 那个出牌阶段（引擎在
#   ``_phase_play_end`` 的派发**之后**清掉，与【奇谋】/【决堰】的次数轴同一收尾点）。
#
# **用户 2026-11-17 的口径（票 03 §0.2 Q2）**：有人指出这些「司敌」牌**并非完全等价**
# （后续版本花色 / 牌名可能被别的技能读到），故 ② 必须**像【七星】那样自选一张**——
# 一步步 ``PickCards(zones=(Zone.HERO,), hero_zone=…)``（前端落既有的 ``heroPick`` 弹窗），
# 不许由实现代挑"区内序第一张"。
# ---------------------------------------------------------------------------

SIDI = hero_skill_id("caozhen_mobile", "sidi")
"""曹真移动版【司敌】的全限定技能 id（yijiang-2014-round3 票 03）。"""

_SIDI_PLACED = "sidi_placed"
"""帧私有：这一问已经把牌堆顶那一张放到武将牌上（重入不重放）。"""

_SIDI_REMOVED = "sidi_removed"
"""帧私有：这一问已经移去过牌（重入不重移、不重复扣次数上限）。"""


def _is_dodge_card(card: Any) -> bool:
    """这张牌是不是【闪】（牌型判据；引擎的 ``DODGE_RESPONSE_CARD`` = ``"dodge"``）。"""
    return card is not None and str(card.type) == "dodge"


def _sidi_zone(state: GameState, seat: int, skill_id: str) -> tuple[int, ...]:
    """该座位**这条技能**的武将牌上放置区（区键 = ``owner:skill_id``）。"""
    return tuple(state.hero_zone(seat, hero_card_zone_key(seat, skill_id)))


def _sidi_place_condition(host: Any, frame: Any) -> bool:
    """① 的触发条件：【闪】被**使用**（打出不算）∧ 使用者是我 / 别人在我的回合里 ∧ 有牌可放。

    - 使用 vs 打出：规则 §5.2「【闪】属于使用事件；只有要求『打出【闪】』的场合才走打出流程」
      ⇒ 【万箭齐发】窗口里的【闪】是 ``PLAY`` 帧（派发 ``PLAY_ON_PLAY``），本钩子不响；
    - 「其他角色**于你的回合内**」读 ``state.turn_seat == owner``；
    - 牌堆与弃牌堆**皆空** ⇒ 没有牌可放 ⇒ 候选阶段就不产出（不发没有候选的询问）。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    if not _is_dodge_card(frame.data.get("card")):
        return False
    source = frame.data.get("source")
    if source != owner and host.state.turn_seat != owner:
        return False
    return bool(host.state.deck or host.state.discard)


def _sidi_place(host: Any, frame: Any) -> Any:
    """① 的效果：牌堆顶一张 → 自己的武将牌上（称为「司敌」牌）。

    帧内游标先落再搬：搬牌会内联派发"失去牌后"一类钩子，重入时不能再搬第二张。
    """
    if frame.data.get(_SIDI_PLACED):
        return None
    owner = int(frame.data.get("skill_owner", frame.data.get(MATCH_OWNER_KEY)))
    skill_id = str(frame.data.get(RUNNING_SKILL_KEY) or SIDI)
    cards = host.peek_deck(frame, 1)
    if not cards:
        return None
    frame.data[_SIDI_PLACED] = True
    return host.place_on_hero_card(
        frame,
        owner=owner,
        skill_id=skill_id,
        holder=owner,
        cards=cards,
    )


SIDI_PLACE_FLOW = SkillFlow(
    name="sidi_place", steps=(), effect=zero_step_effect(_sidi_place)
)
"""① 的零步流水线：[是否发动] → 牌堆顶一张置于武将牌上。"""


def _sidi_limit_condition(host: Any, frame: Any) -> bool:
    """② 的触发条件：**其他角色**的出牌阶段开始 ∧ 我的武将牌上有「司敌」牌。

    帧 ``source`` = 当前回合角色（``_dispatch_phase_body`` 的 ``actor=_TURN_SEAT``），故
    「这是别的角色的出牌阶段」= ``source == turn_seat`` ∧ ``source != owner``。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    if owner is None:
        return False
    owner = int(owner)
    state = host.state
    if state.turn_seat is None or int(state.turn_seat) == owner:
        return False
    if frame.data.get("source") != state.turn_seat:
        return False
    return bool(_sidi_zone(state, owner, SIDI))


def _sidi_limit_zone(state: GameState, frame: Any, owner: int, answers: Any) -> str:
    """``PickCards.hero_zone`` 的取值：**这个持有者**的「司敌」区（区键，不是牌）。

    与【七星】的 ``_qixing_zone`` 同一姿态：只读**自己**这条技能的放置区（不是别人的、
    也不是别的技能扣的牌；区键 = ``owner:skill_id``）。
    """
    del state, frame, answers
    return hero_card_zone_key(int(owner), SIDI)


def _sidi_limit(host: Any, frame: Any, answers: FlowAnswers) -> Any:
    """② 的效果：移去**玩家选的那一张**「司敌」牌，令其本阶段使用【杀】的次数上限-1。

    次序：**先落状态、再搬牌**——搬牌可能内联派发钩子并挂起，先搬后落会留下"牌没了、
    效果没生效"的窗口。游标 ``sidi_removed`` 挡住重入（重入既不重扣也不重搬）。
    """
    if frame.data.get(_SIDI_REMOVED):
        return None
    card = int(tuple(answers["card"])[0])
    state = host.state
    target = int(state.turn_seat)
    frame.data[_SIDI_REMOVED] = True
    state.player(target).sidi_slash_penalty += 1
    return host.move_in_frame(
        frame,
        state.refs((card,)),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.PUT,
        owner=int(frame.data.get("skill_owner", 0)),
        actor=int(frame.data.get("skill_owner", 0)),
    )


SIDI_LIMIT_FLOW = SkillFlow(
    name="sidi_limit",
    steps=(
        PickCards(
            zones=(Zone.HERO,),
            hero_zone=_sidi_limit_zone,
            lo=1,
            hi=1,
            optional=False,
            key="card",
        ),
    ),
    effect=_sidi_limit,
)
"""② 的一步流水线：选一张「司敌」牌（玩家自选）→ 移去并 −1。"""


def _sidi_slash_limit(state: GameState, ctx: ModifierContext, current: Any) -> Any:
    """② 的 ``COUNT_LIMIT``：被限者本阶段使用【杀】的次数上限 −1（只减【杀】家族）。

    ``ctx.subject`` = **被问的那一名角色**（次数上限是"这张牌还能用几次"，主语是使用者），
    ``ctx.owner`` = 曹真（本实例的持有者）——两者不是同一个人，这正是"跨座位的上限修正"。
    「无次数限制」（``current is None``，【诸葛连弩】一类）保持无限：没有有限上限可减
    （规则 §17.2 ④；既有 4 条 ``COUNT_LIMIT`` 回调同一写法）。
    """
    if current is None:
        return None
    if ctx.card is None or not is_slash(behavior_for(ctx.card)):
        return current
    penalty = state.player(ctx.subject).sidi_slash_penalty
    if not penalty:
        return current
    return int(current) - penalty


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
        # 一将成名-2014·移动版 曹真【司敌】（yijiang-2014-round3 票 03）：两个自动预发动入口
        # （① 使用【闪】时放牌 / ② 别人的出牌阶段开始时移去一张）+ 一条跨座位的次数上限修正。
        SkillDef(
            skill_id=SIDI,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(
                    Timing.USE_ON_USE,
                    condition=_sidi_place_condition,
                    flow=SIDI_PLACE_FLOW,
                ),
                Hook(
                    Timing.PHASE_PLAY_START,
                    condition=_sidi_limit_condition,
                    flow=SIDI_LIMIT_FLOW,
                ),
            ),
            modifiers=(Modifier(ModifierPoint.COUNT_LIMIT, _sidi_slash_limit),),
            places_on_hero_card=True,
            requirement=(
                "当你使用【闪】时，或当其他角色于你的回合内使用【闪】时，你可以将牌堆顶的一张牌"
                "置于你的武将牌上称为“司敌”牌；其他角色的出牌阶段开始时，你可以移去一张“司敌”牌，"
                "令其本阶段使用【杀】的次数上限-1"
            ),
            step_requirements={
                "card": "移去一张“司敌”牌（武将牌上的牌）",
            },
        ),
    )
