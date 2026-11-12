"""神诸葛亮（神话再临·火包 / OL 现行文本）：【七星】【狂风】【大雾】。

牌面权威 = 用户给的 `技能.txt`（= OL 现行文本，逐字见
`.scratch/huo-shenzhugeliang/issues/01-shenzhugeliang.md` §0）。三条技能的骨架：

- **七星**：`GAME_START` 强制段（扣置牌堆顶七张为「星」）+ 两行交换弹窗（星 ↔ 手牌，
  只上下交换、行内不调序，切分点 = 交换前星数 ⇒ 等量替换由规格保证）；摸牌阶段结束时
  同一弹窗（`PHASE_DRAW_END`，票 03 解的派发点）。
- **狂风**：结束阶段移去一张「星」、选一名角色，令其获得一枚「狂风」标记；**标记没有
  自身效果**（`effect=None` / `description=None`），增伤由**这条技能**监听：目标身上每枚
  「狂风」+1（限火焰伤害），技能被失效即不进修饰器链 ⇒ 不增伤。寿命 = 来源的下个回合
  开始时（本技能的 `TURN_START` 强制钩子）+ 来源阵亡（`clear_on_source_death`）。
- **大雾**：同步骤，移去 1..min(星数, 存活数) 张「星」、选等量名角色各得一枚「大雾」；
  防止由**这条技能**监听：`DAMAGE_DEAL` 强制钩子写帧标签 `PREVENTED`（非雷电伤害），
  失效即不防止。

三条技能共用的两个约定（用户 2026-11-04 裁定）：

- 标记只记事实：名称 / 来源 / 数量 / 上限 / 无效果 / 无说明；
- 「强制段」用 :attr:`~sgs.skills.catalog.Hook.forced` 声明（票 04）：不问"是否发动"，
  但仍随实例失效而停摆（`forced` **不**进 `_skill_is_suppressed`）。
"""

from __future__ import annotations

from typing import Any

from sgs.data import SkillType, hero_skill_id
from sgs.effects import CarrierKind, DamageNature, Modifier, ModifierPoint
from sgs.resolution import Frame, Tag, add_tag
from sgs.state import (
    ArrangeCards,
    CardRef,
    GameState,
    MoveReason,
    Zone,
    ZoneRef,
)
from sgs.timing import Timing

from .catalog import MATCH_OWNER_KEY, Hook, SkillDef, hero_card_zone_key
from .flow import (
    Arrange,
    FlowAnswers,
    PickCards,
    PickSeats,
    SkillFlow,
    TwoRowArrange,
    run_flow,
    watched_cards,
)

__all__ = [
    "DAWU",
    "DAWU_FLOW",
    "DAWU_MARK",
    "KUANGFENG",
    "KUANGFENG_FLOW",
    "KUANGFENG_MARK",
    "QIXING",
    "QIXING_STARS",
    "QIXING_SWAP",
    "QIXING_SWAP_FLOW",
    "shen_zhugeliang_skill_definitions",
]

HERO = "shen_zhugeliang"
QIXING = hero_skill_id(HERO, "qixing")
KUANGFENG = hero_skill_id(HERO, "kuangfeng")
DAWU = hero_skill_id(HERO, "dawu")

QIXING_STARS = 7
"""「游戏开始时，你将牌堆顶的七张牌扣置于你的武将牌上」。"""

KUANGFENG_MARK = "狂风"
DAWU_MARK = "大雾"

_FRAME_KEYS = (
    "qixing_peeked",
    "qixing_cards",
    "qixing_placed",
    "qixing_plan",
    "qixing_stage",
    "kuangfeng_stage",
    "dawu_peeked",
    "dawu_stage",
    "dawu_targets",
)


# ---------------------------------------------------------------------------
# 共用：星区 / 标记 / 自己的阶段
# ---------------------------------------------------------------------------


def star_zone_key(seat: int) -> str:
    """「星」区的键 = （座位, 七星实例）——与【谦逊】的区互不相干（用户提醒的那一条）。"""
    return hero_card_zone_key(seat, QIXING)


def _star_ids(state: GameState, seat: int) -> tuple[int, ...]:
    return tuple(state.hero_zone(seat, star_zone_key(seat)))


def _hand_ids(state: GameState, seat: int) -> tuple[int, ...]:
    return tuple(state.player(seat).hand)


def _qixing_zone(state: GameState, frame: Any, owner: int, answers: FlowAnswers) -> str:
    """``PickCards.hero_zone`` 的取值：**这个持有者**的星区（不是别人的、也不是谦逊的）。"""
    del state, frame, answers
    return star_zone_key(owner)


def _own_turn_start(host: Any, frame: Frame) -> bool:
    """**只有自己的回合开始**才该清扫标记（用户 2026-11-04 报障后的口径）。

    标记的寿命是"**标记来源**的下个回合开始时消失"，而 `TURN_START` 是**每个座位**的
    回合开始都会派发——不带条件就会在**目标**的回合开始就清掉（实机表现：庞统身上的
    「大雾」在下一位角色（庞统自己）回合开始就没了）。故清扫钩子必须自校"这一步的回合
    角色是不是持有者"：阶段体/帧外时机的帧 `source` = 当前回合角色。
    """
    owner = frame.data.get(MATCH_OWNER_KEY)
    return owner is not None and frame.data.get("source") == owner


def _own_turn_phase(host: Any, frame: Frame) -> bool:
    """帧外阶段时机上"这一步是问我的"（阶段体帧的 ``source`` = 当前回合角色）。"""
    owner = frame.data.get(MATCH_OWNER_KEY)
    return owner is not None and frame.data.get("source") == owner


def _alive_seats(state: GameState, frame: Any, owner: int, answers: FlowAnswers) -> tuple[int, ...]:
    """「一名角色」/「等量名角色」的池：**存活角色**（含自己，用户裁定「一名角色含自己」）。"""
    del frame, owner, answers
    return tuple(player.seat for player in state.players if player.alive)


def _expire_marks(host: Any, frame: Frame, skill_id: str) -> Any:
    """来源的下个回合开始时，清掉**自己来源**的全部该技能标记（强制钩子，不问）。"""
    seat = int(frame.data["skill_owner"])
    for player in tuple(host.state.players):
        for marker in tuple(player.markers):
            if marker.source_seat != seat or marker.source_skill_id != skill_id:
                continue
            host.remove_marker(
                frame,
                player.seat,
                name=marker.name,
                source_seat=seat,
                source_skill_id=skill_id,
            )
    return None


def _mark_count(state: GameState, seat: int, name: str) -> int:
    return sum(marker.count for marker in state.player(seat).markers if marker.name == name)


def _discard(host: Any, frame: Frame, seat: int, card_ids: tuple[int, ...]) -> Any:
    """移去几张「星」：武将牌上的牌 → 弃牌堆（规则 §15.2；`MoveReason.PUT`，非任何人弃置）。"""
    if not card_ids:
        return None
    return host.move_in_frame(
        frame,
        host.state.refs(card_ids),
        to_zone=ZoneRef(Zone.DISCARD),
        reason=MoveReason.PUT,
        owner=seat,
        actor=seat,
    )


# ---------------------------------------------------------------------------
# 七星：扣置七张 + 两行交换（星 ↔ 手牌）
# ---------------------------------------------------------------------------


def _qixing_face(state: GameState, frame: Frame) -> tuple[CardRef, ...]:
    """弹窗的两行牌面：**上排 = 星（在前）、下排 = 手牌（在后）**。"""
    seat = int(frame.data["skill_owner"])
    return state.refs((*_star_ids(state, seat), *_hand_ids(state, seat)))


def _qixing_total(state: GameState, frame: Frame) -> int:
    seat = int(frame.data["skill_owner"])
    return len(_star_ids(state, seat)) + len(_hand_ids(state, seat))


def _qixing_top(state: GameState, frame: Frame) -> int:
    return len(_star_ids(state, int(frame.data["skill_owner"])))


QIXING_SWAP = TwoRowArrange(
    count=_qixing_total,
    top=_qixing_top,
    cards=_qixing_face,
    top_label="星",
    bottom_label="手牌",
)
"""【七星】的两行交换规格：**只上下交换、不调序**，切分点 = 交换前星数（等量由它保证）。"""


def _qixing_apply(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：按 ``ArrangeCards`` 把两行落回——下行里被选中的手牌上星区、上排多余的星回手牌。

    两批搬牌各自走帧内游标（``qixing_plan`` / ``qixing_stage``）：搬牌会内联派发"失去牌后"
    （【连营】【清俭】一类）并挂起，重入既不重搬、也不漏搬。
    """
    action = answers.get("cards")
    if not isinstance(action, ArrangeCards):
        return None
    seat = int(frame.data["skill_owner"])
    if "qixing_plan" not in frame.data:
        cards = watched_cards(frame, QIXING_SWAP_FLOW, "cards")
        split = int(action.split)
        chosen = {cards[index].card_id for index in action.order[:split]}
        stars = _star_ids(host.state, seat)
        hand = _hand_ids(host.state, seat)
        down = tuple(card_id for card_id in stars if card_id not in chosen)
        up = tuple(card_id for card_id in hand if card_id in chosen)
        frame.data["qixing_plan"] = (down, up)
        frame.data["qixing_stage"] = 0
    down, up = frame.data["qixing_plan"]
    if frame.data.get("qixing_stage", 0) == 0:
        # 不换任何一张时这一批是空的：``move_in_frame`` 拒绝空批（编程错误），故显式跳过。
        if down:
            control = host.move_in_frame(
                frame,
                host.state.refs(down),
                to_zone=ZoneRef(Zone.HAND, seat),
                reason=MoveReason.PUT,
                owner=seat,
                actor=seat,
            )
            if control == "stay":
                return "stay"
        frame.data["qixing_stage"] = 1
    if frame.data.get("qixing_stage", 0) == 1:
        if up:
            control = host.place_on_hero_card(
                frame,
                owner=seat,
                skill_id=QIXING,
                holder=seat,
                cards=host.state.refs(up),
            )
            if control == "stay":
                return "stay"
        frame.data["qixing_stage"] = 2
    return None


QIXING_SWAP_FLOW = SkillFlow(
    name="qixing_swap",
    steps=(
        # 两行交换：**一次弹窗**（用户裁定「无需第二步」），答案 = 定长集合 + 切分点。
        Arrange(key="cards", spec=QIXING_SWAP),
    ),
    effect=_qixing_apply,
)


def _qixing_start(host: Any, frame: Frame) -> Any:
    """【七星】① 的强制段：扣置牌堆顶七张，然后立刻问一次替换（放弃 = 原样提交）。"""
    seat = int(frame.data["skill_owner"])
    if not frame.data.get("qixing_peeked"):
        frame.data["qixing_peeked"] = True
        frame.data["qixing_cards"] = tuple(
            card.card_id for card in host.peek_deck(frame, QIXING_STARS)
        )
    if not frame.data.get("qixing_placed"):
        control = host.place_on_hero_card(
            frame,
            owner=seat,
            skill_id=QIXING,
            holder=seat,
            cards=host.state.refs(tuple(frame.data["qixing_cards"])),
        )
        if control == "stay":
            return "stay"
        frame.data["qixing_placed"] = True
    return run_flow(host, frame, QIXING_SWAP_FLOW, owner=seat, timing=Timing.GAME_START)


def _qixing_can_swap(host: Any, frame: Frame) -> bool:
    """② 的触发条件：**自己的**摸牌阶段结束、且有星也有手牌（否则不发空询问）。"""
    if not _own_turn_phase(host, frame):
        return False
    owner = int(frame.data[MATCH_OWNER_KEY])
    return bool(_star_ids(host.state, owner)) and bool(_hand_ids(host.state, owner))


# ---------------------------------------------------------------------------
# 狂风：移去一张星、选一名角色、挂「狂风」标记；+X 由本技能监听
# ---------------------------------------------------------------------------


def _kuangfeng_bonus(state: GameState, ctx: Any, current: Any) -> Any:
    """「狂风」标记的增伤：目标身上每枚 +1，**只限火焰伤害**（用户 2026-11-04 Q1）。

    失效 ⇒ 本实例不进修饰器链（``resolve_instances``）⇒ 不增伤（用户 Q2 的口径）。
    """
    target = ctx.target
    if target is None or not 0 <= int(target) < len(state.players):
        return current
    if not state.player(int(target)).alive:
        return current
    if getattr(ctx, "nature", None) is not DamageNature.FIRE:
        return current
    count = _mark_count(state, int(target), KUANGFENG_MARK)
    return current if count == 0 else current + count


def _kuangfeng_apply(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：弃置那张星（武将牌上的牌 → 弃牌堆）→ 令目标获得一枚「狂风」。"""
    stars = tuple(answers.get("card") or ())
    seats = tuple(answers.get("seat") or ())
    if not stars or not seats:
        return None
    seat = int(frame.data["skill_owner"])
    stage = int(frame.data.get("kuangfeng_stage", 0))
    if stage == 0:
        control = _discard(host, frame, seat, stars)
        if control == "stay":
            return "stay"
        frame.data["kuangfeng_stage"] = 1
    if stage <= 1:
        host.add_marker(
            frame,
            int(seats[0]),
            name=KUANGFENG_MARK,
            source_seat=seat,
            source_skill_id=KUANGFENG,
            count=1,
            clear_on_source_death=True,
        )
        frame.data["kuangfeng_stage"] = 2
    return None


KUANGFENG_FLOW = SkillFlow(
    name="kuangfeng",
    steps=(
        PickCards(
            zones=(Zone.HERO,),
            hero_zone=_qixing_zone,
            lo=1,
            hi=1,
            optional=False,
            key="card",
        ),
        PickSeats(lo=1, hi=1, pool=_alive_seats, key="seat", optional=False),
    ),
    effect=_kuangfeng_apply,
)


def _kuangfeng_expire(host: Any, frame: Frame) -> Any:
    return _expire_marks(host, frame, KUANGFENG)


# ---------------------------------------------------------------------------
# 大雾：移去任意张星、选等量名角色、挂「大雾」标记；防止由本技能监听
# ---------------------------------------------------------------------------


def _dawu_hi(state: GameState, frame: Frame, owner: int, answers: FlowAnswers) -> int:
    """上限 = **min(星数, 存活角色数)**（用户 2026-11-04 Q3/R9），下限由 ``lo=1`` 给。"""
    del frame, answers
    alive = sum(1 for player in state.players if player.alive)
    return max(0, min(len(_star_ids(state, owner)), alive))


def _dawu_prevent(host: Any, frame: Frame) -> Any:
    """「大雾」标记的防止：非雷电伤害 ⇒ 写帧标签 ``PREVENTED``（不是把数值改成 0）。

    ``DAMAGE_DEAL`` 在"受到伤害时"与数值定稿**之前**派发，故写在这里能让整条数值链短路、
    不压 `LIFE_DECREASE` 子帧、也不派发"受到伤害后"。技能被失效 ⇒ 本钩子不派发 ⇒ 不防止。
    """
    target = frame.data.get("target")
    if target is None:
        return None
    if frame.data.get("nature") is DamageNature.THUNDER:
        return None
    if _mark_count(host.state, int(target), DAWU_MARK) > 0:
        add_tag(frame, Tag.PREVENTED)
    return None


def _dawu_apply(host: Any, frame: Frame, answers: FlowAnswers) -> Any:
    """提交点：弃置所选的星 → **一次**选等量名角色 → 每人一枚「大雾」。

    选人由 :meth:`Engine.ask_seats` **一次问齐**（table-ux-overhaul 票 13）：区间
    ``[min(k, 池), k]``、``optional=False`` —— 牌面写"令至多 X 名角色各获得一枚"，而选星
    那一步已经定下了 k；旧逐座档在没有 ``Pass`` 的情况下**必然**选满 k 名（池不够时选满
    池），故这里的下限 = "池里能选到几个就几个"、上限 = k，语义逐字不变。

    **先选齐人、再逐座结算**：``dawu_targets``（已选）/ ``dawu_marked``（已挂）两个帧内
    游标记住进度，挂起续跑不重选、不重挂。
    """
    stars = tuple(answers.get("card") or ())
    if not stars:
        return None
    seat = int(frame.data["skill_owner"])
    chosen = tuple(frame.data.get("dawu_targets", ()))
    if int(frame.data.get("dawu_stage", 0)) == 0:
        control = _discard(host, frame, seat, stars)
        if control == "stay":
            return "stay"
        frame.data["dawu_stage"] = 1
    if not frame.data.get("dawu_picked"):
        pool = tuple(player.seat for player in host.state.players if player.alive)
        if pool:
            answer = host.ask_seats(
                frame,
                seat,
                Timing.PHASE_FINISH,
                skill_id="dawu:seat",
                pool=pool,
                # k 由星数定格；池装不下 k 名时下限跟着池走（旧逐座档选满池即收尾）。
                lo=min(len(stars), len(pool)),
                hi=len(stars),
                optional=False,
            )
            if host.suspended(answer):
                return "stay"
            chosen = tuple(answer)
        frame.data["dawu_targets"] = chosen
        frame.data["dawu_picked"] = True
    marked = set(frame.data.get("dawu_marked", ()))
    for target in chosen:
        if target in marked:
            continue
        host.add_marker(
            frame,
            int(target),
            name=DAWU_MARK,
            source_seat=seat,
            source_skill_id=DAWU,
            count=1,
            clear_on_source_death=True,
        )
        marked.add(target)
        frame.data["dawu_marked"] = tuple(sorted(marked))
    return None


DAWU_FLOW = SkillFlow(
    name="dawu",
    steps=(
        PickCards(
            zones=(Zone.HERO,),
            hero_zone=_qixing_zone,
            lo=1,
            hi=_dawu_hi,
            optional=False,
            key="card",
        ),
    ),
    effect=_dawu_apply,
)


def _dawu_expire(host: Any, frame: Frame) -> Any:
    return _expire_marks(host, frame, DAWU)


# ---------------------------------------------------------------------------
# 声明
# ---------------------------------------------------------------------------


def shen_zhugeliang_skill_definitions() -> tuple[SkillDef, ...]:
    """神诸葛亮三条技能的 :class:`~sgs.skills.catalog.SkillDef`（非锁定；强制段逐条声明）。"""
    requirement_qixing = (
        "游戏开始时，你将牌堆顶的七张牌扣置于你的武将牌上，称为“星”，"
        "然后你可以用任意张手牌替换等量的“星”（摸牌阶段结束时，你可以用任意张手牌"
        "替换等量的“星”）"
    )
    return (
        SkillDef(
            skill_id=QIXING,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                # ① 扣置：**强制段**（牌面没有「可以」）⇒ 不问；替换由随后的两行弹窗问。
                Hook(Timing.GAME_START, handler=_qixing_start, forced=True),
                # ② 摸牌阶段结束时（票 03 解的派发点）：可选段 ⇒ 问一次"是否发动"。
                Hook(Timing.PHASE_DRAW_END, flow=QIXING_SWAP_FLOW, condition=_qixing_can_swap),
            ),
            places_on_hero_card=True,
            deck_arrange=QIXING_SWAP,
            requirement=requirement_qixing,
        ),
        SkillDef(
            skill_id=KUANGFENG,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(Timing.PHASE_FINISH, flow=KUANGFENG_FLOW, condition=_own_turn_phase),
                # 到期清扫：**来源的**下个回合开始时清掉自己来源的标记（**强制**，不问；
                # 但要自校"这是不是我的回合开始"——TURN_START 每座都派发）。
                Hook(
                    Timing.TURN_START,
                    handler=_kuangfeng_expire,
                    forced=True,
                    condition=_own_turn_start,
                ),
            ),
            modifiers=(
                Modifier(ModifierPoint.DAMAGE_TAKE_AMOUNT, _kuangfeng_bonus),
            ),
            requirement="结束阶段，你可以移去一张“星”并选择一名角色，然后直到你的下回合开始，"
            "当该角色受到火焰伤害时，此伤害+1",
            step_requirements={
                "card": "移去一张“星”（武将牌上的牌）",
                "seat": "选择一名角色（含自己）",
            },
        ),
        SkillDef(
            skill_id=DAWU,
            locked=False,
            carrier=CarrierKind.HERO,
            skill_type=SkillType.NORMAL,
            hooks=(
                Hook(Timing.PHASE_FINISH, flow=DAWU_FLOW, condition=_own_turn_phase),
                # 监听：非雷电伤害 ⇒ 防止（**强制**，不问）。
                Hook(Timing.DAMAGE_DEAL, handler=_dawu_prevent, forced=True),
                Hook(
                    Timing.TURN_START,
                    handler=_dawu_expire,
                    forced=True,
                    condition=_own_turn_start,
                ),
            ),
            requirement="结束阶段，你可以移去任意张“星”并选择等量名角色，然后直到你的下回合开始前，"
            "当这些角色受到非雷电伤害时，防止之",
            step_requirements={
                "card": "移去任意张“星”（上限 = min(星数, 存活角色数)）",
                "seat": "选择等量名角色（含自己，互不相同）",
            },
        ),
    )
