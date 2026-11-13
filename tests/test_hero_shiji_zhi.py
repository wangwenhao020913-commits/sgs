"""始计篇-智包：神郭嘉 + 陈震（shiji-zhi 票 01 / shiji-zhi-luotong-chenzhen 票 02）。

神郭嘉四条技能各一组断言：

- 【慧识】判定过程连续：判定牌留处理区、花色重复才终止、**第一张也加上限**、每张花色不重复
  的判定都**直接**加上限（与"是否继续判定"无关）、全部获得（含最后那张）、交给后按"全场最多
  （含并列）"减上限；上限 10 时不可发动、加到 10 即止。
- 【辉逝】真支 = **只满足觉醒条件**（框架票 05：觉醒仍等它自己的时机与归属）、假支 = 摸四张、
  两支都减 2 上限、限定技整局一次。
- 【天翊】觉醒条件读**整局**伤害事实；+2 上限 / 回复 1 / 授予 ☆佐幸（可给自己）。
- ☆【佐幸】代价只认「神郭嘉」这个角色；「视为使用」= 无源纯虚拟牌；牌名闭集 + 置灰。

陈震【歃盟】（票 02）一组：**「两张颜色相同」是候选面上的前置约束**（第 2 步按第 1 张逐行
下发、复合动作里的异色组合被拒收）、只手牌、目标 = 其他存活角色、取消不消耗阶段位、
目标先摸 2 再自己摸 3、短摸平局、目标"获得牌后"钩子挂起后不重摸。

驱动纪律：脚本玩家只应答**被测那一步**（按 ``skill_id`` 的步键），其余走最小合法应答
（出牌阶段结束 / 放弃）；引擎开到"该技能从出牌阶段候选里消失"的那一刻为止（= 这次发动
已经收尾），故断言看到的是**出牌阶段结束时**的状态，不掺弃牌阶段。
"""

from __future__ import annotations

from typing import Any

import pytest

from sgs.data import RuleConfig, Suit, hero_skill_id, load_content_pack
from sgs.effects import DamageNature
from sgs.engine import Engine, IllegalActionError
from sgs.skills import (
    CATALOG,
    awakening_condition_satisfied,
    awakening_skill_awakened,
    limited_skill_used,
)
from sgs.skills.preactivation import preactivation_for
from sgs.state import (
    ActivateSkill,
    CardRef,
    EndPhase,
    FrameKind,
    GameState,
    Pass,
    Phase,
    SelectCards,
    SelectOption,
)
from sgs.timing import Timing
from support import SeatSpec, build_state

PACK = load_content_pack(deck_profile="standard_108")

HUISHI = hero_skill_id("shen_guojia", "huishi")
HUI_SHI = hero_skill_id("shen_guojia", "hui_shi")
TIANYI = hero_skill_id("shen_guojia", "tianyi")
ZUOXING = hero_skill_id("shen_guojia", "zuoxing")
QIANXUE = hero_skill_id("jie_xushu", "qianxue")
JIANYAN = hero_skill_id("jie_xushu", "jianyan")

SHEN_GUOJIA = "shen_guojia"


# ---------------------------------------------------------------------------
# 构造与驱动助手
# ---------------------------------------------------------------------------


def _of_suit(suit: Suit, count: int = 1) -> list[int]:
    """同花色的前 ``count`` 张牌（判定结果只看花色）。"""
    ids = [
        card_id
        for card_id in range(len(PACK.deck.cards))
        if PACK.deck.cards[card_id].suit is suit
    ]
    assert len(ids) >= count
    return ids[:count]


def _deck_with(*judged: int, exclude: tuple[int, ...] = ()) -> tuple[int, ...]:
    """牌堆：前 2 张是摸牌阶段的填充牌，接着依次是 ``judged``，其余接在后面。"""
    used = set(judged) | set(exclude)
    rest = [cid for cid in range(len(PACK.deck.cards)) if cid not in used]
    fillers = rest[:2]
    return (*fillers, *judged, *rest[2:])


def _step_key(skill_id: str) -> str:
    """询问 ``skill_id`` 的步键（与 ``projection._step_requirement`` 同一条取法）。"""
    _head, sep, rest = skill_id.partition(":")
    if not sep:
        return ""
    stem, colon, tail = rest.rpartition(":")
    return stem if colon and tail.isdigit() else rest


class Script:
    """按 ``{步键: 选择}`` 应答的脚本玩家；其余走最小合法应答。"""

    def __init__(self, answers: dict[str, str | None] | None = None) -> None:
        self.answers = answers or {}

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            action = self._activate(query)
            if action is not None:
                return action
            return next(
                item for item in query.legal_actions if isinstance(item, EndPhase)
            )
        if query.kind == "SELECT_OPTION":
            key = _step_key(str(getattr(query, "skill_id", "")))
            if key in self.answers:
                want = self.answers[key]
                if want is None:
                    return next(
                        item for item in query.legal_actions if isinstance(item, Pass)
                    )
                for item in query.legal_actions:
                    if isinstance(item, SelectOption) and item.choice == want:
                        return item
        if query.kind == "SELECT_CARDS":
            for item in query.legal_actions:
                if isinstance(item, SelectCards):
                    return item
        for item in query.legal_actions:
            if isinstance(item, Pass):
                return item
        return query.legal_actions[0]

    def _activate(self, query):
        wanted = self.answers.get("activate")
        if not wanted:
            return None
        for item in query.legal_actions:
            if isinstance(item, ActivateSkill) and item.skill_id == wanted:
                return item
        return None


def _seats(
    *,
    hero: str = SHEN_GUOJIA,
    hp: int = 3,
    hp_max: int = 3,
    alive: bool = True,
    hand: tuple[int, ...] = (),
    other_hero: str | None = None,
    other_hand: tuple[int, ...] = (),
) -> list[SeatSpec]:
    return [
        SeatSpec(hero=hero, hp=hp, hp_max=hp_max, alive=alive, hand=hand),
        SeatSpec(hero=other_hero, hp=4, hp_max=4, hand=other_hand),
    ]


def _state(
    seats: list[SeatSpec],
    *,
    deck: tuple[int, ...],
    phase: Phase = Phase.PLAY,
    turn_seat: int = 0,
) -> GameState:
    return build_state(
        pack=PACK,
        seats=seats,
        deck=deck,
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(player_count=2, first_seat=turn_seat),
    )


def _drive_until(
    engine: Engine, players: list[Script], stop, *, limit: int = 4_000
) -> Engine:
    """推进到 ``stop(engine)`` 成立（询问按座位脚本应答）。"""
    for _ in range(limit):
        if stop(engine):
            return engine
        query = engine.pending
        if query is None:
            if engine.finished:
                return engine
            engine._step()
            continue
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有到达停止条件")


def _capture_virtual_uses(engine: Engine, captured: list[CardRef]) -> None:
    for frame in engine.stack.frames:
        if frame.kind is not FrameKind.USE:
            continue
        card = frame.data.get("card")
        if (
            card is not None
            and card.is_virtual
            and not card.virtual_sources
            and card not in captured
        ):
            captured.append(card)


def _drive_until_done(
    engine: Engine,
    skill_id: str,
    players: list[Script],
    *,
    captured: list[CardRef] | None = None,
    limit: int = 4_000,
) -> Engine:
    """推进到"该技能已从出牌阶段候选里消失"为止（= 这次发动收尾，弃牌阶段之前）。"""
    for _ in range(limit):
        if captured is not None:
            _capture_virtual_uses(engine, captured)
        query = engine.pending
        if (
            query is not None
            and query.kind == "PLAY_PHASE"
            and not any(
                isinstance(item, ActivateSkill) and item.skill_id == skill_id
                for item in query.legal_actions
            )
        ):
            return engine
        if query is None:
            if engine.finished:
                return engine
            engine._step()
            continue
        engine.submit(players[query.actor].respond(query))
    raise AssertionError("步数上限内没有走完这次发动")


# ---------------------------------------------------------------------------
# 慧识
# ---------------------------------------------------------------------------


def test_huishi_first_card_also_raises_max_hp_and_duplicate_ends_the_flow():
    """第一张判定牌**无论花色**都加体力上限（此前没有结果可比 ⇒ 恒算"均不同"）；
    第 2 张花色重复 ⇒ 不再问、流程终止。"""
    judged = _of_suit(Suit.HEART, 2)
    state = _state(_seats(), deck=_deck_with(*judged))
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine,
        HUISHI,
        [Script({"activate": HUISHI, "more": "huishi_continue"}), Script()],
    )

    assert engine.state.player(0).hp_max == 4, "第一张判定就加 1 点体力上限"
    assert len(engine.state.player(0).hand) == 4, "2 张摸牌 + 两张判定牌（含重复花色那张）"
    assert not engine.state.processing, "流程终止后判定牌离开处理区（不交 ⇒ 归神郭嘉）"


def test_huishi_each_new_suit_adds_another_max_hp():
    judged = (*_of_suit(Suit.HEART), *_of_suit(Suit.SPADE, 2))
    state = _state(_seats(), deck=_deck_with(*judged))
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine,
        HUISHI,
        [
            Script(
                {
                    "activate": HUISHI,
                    "more": "huishi_continue",  # 前两张各加 1 并继续
                }
            ),
            Script(),
        ],
    )

    assert engine.state.player(0).hp_max == 5, "两张花色不同的判定各 +1（3 → 5）"
    assert len(engine.state.player(0).hand) == 5, "2 张摸牌 + 三张判定牌"


def test_huishi_judged_cards_stay_in_the_effect_area_until_the_give():
    """流程里判定牌**留在处理区**（效果区），不先进手牌；交给时**从处理区直接交出去**。"""
    other_hand = tuple(_of_suit(Suit.CLUB, 4))
    deck = _deck_with(
        *_of_suit(Suit.HEART), *_of_suit(Suit.SPADE, 2), exclude=other_hand
    )
    state = _state(_seats(other_hand=other_hand, other_hero="guojia"), deck=deck)
    engine = Engine.from_state(state, seed=7)
    seen = {"at_give": None}

    def watch(query):
        if (
            query.kind == "SELECT_OPTION"
            and _step_key(query.skill_id) == "give"
        ):
            seen["at_give"] = (
                tuple(engine.state.processing),
                tuple(engine.state.player(0).hand),
                tuple(engine.state.player(1).hand),
            )

    players = [
        Script(
            {
                "activate": HUISHI,
                "more": "huishi_continue",
                "give": "huishi_give",
                "give-seat": "seat:1",
            }
        ),
        Script(),
    ]
    for _ in range(4000):
        query = engine.pending
        if query is None:
            if engine.finished:
                break
            engine._step()
            continue
        watch(query)
        if (
            query.kind == "PLAY_PHASE"
            and not any(
                isinstance(item, ActivateSkill) and item.skill_id == HUISHI
                for item in query.legal_actions
            )
        ):
            break
        engine.submit(players[query.actor].respond(query))

    processing, hand0, hand1 = seen["at_give"]
    assert len(processing) == 3, "问「是否交出」时三张判定牌仍在处理区"
    assert len(hand0) == 2, "判定牌没有先进神郭嘉的手牌（只有摸牌阶段的 2 张）"
    assert len(hand1) == 4, "收牌人还没收到"
    assert engine.state.player(0).hp_max == 4, "3 + 2（两张花色不同）- 1（收牌人最多）= 4"
    assert len(engine.state.player(0).hand) == 2, "神郭嘉手上仍是摸来的 2 张"
    assert len(engine.state.player(1).hand) == 7, "收牌人 4 + 3 张判定牌"


def test_huishi_declining_further_judgment_keeps_the_max_hp_and_gains_the_cards():
    """「加 1 点体力上限」**不是**"继续判定"的代价（用户 2026-11-14 订正）：花色不重复
    ⇒ 上限直接 +1；随后答「放弃」只结束流程，那 1 点照留。"""
    judged = _of_suit(Suit.HEART, 1)
    state = _state(_seats(), deck=_deck_with(*judged))
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine,
        HUISHI,
        [Script({"activate": HUISHI, "more": None, "give": None}), Script()],
    )

    assert engine.state.player(0).hp_max == 4, "第一张花色不重复 ⇒ 直接 +1（与继续与否无关）"
    assert len(engine.state.player(0).hand) == 3, "只判一次 + 2 张摸牌，判定牌由神郭嘉获得"
    assert engine.state.player(1).hand == []
    assert not engine.state.processing


def test_huishi_stops_at_max_hp_ten_without_asking_to_continue():
    """上限 9 时判一张新花色：**直接** +1 到 10，然后不再问「是否继续判定？」（加到 10 即止）。"""
    state = _state(_seats(hp=9, hp_max=9), deck=_deck_with(*_of_suit(Suit.HEART, 2)))
    engine = Engine.from_state(state, seed=7)
    asked: list[str] = []

    class Recorder(Script):
        def respond(self, query):
            if query.kind == "SELECT_OPTION":
                asked.append(_step_key(str(getattr(query, "skill_id", ""))))
            return super().respond(query)

    _drive_until_done(engine, HUISHI, [Recorder({"activate": HUISHI}), Script()])

    assert engine.state.player(0).hp_max == 10, "第一张花色不重复 ⇒ 直接 +1 到 10"
    assert "more" not in asked, "加到 10 即止：不再问是否继续判定"
    assert len(engine.state.player(0).hand) == 3, "只判一张 + 2 张摸牌"


def test_huishi_not_offered_at_max_hp_ten():
    state = _state(_seats(hp=10, hp_max=10), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(engine, HUISHI, [Script({"activate": HUISHI}), Script()])

    assert engine.state.player(0).hp_max == 10
    assert not engine.state.processing, "上限 10 时技能不该出现"


def test_huishi_satisfies_the_awakening_condition_without_resolving_it():
    """用户 2026-11-14 裁定：觉醒技 = 觉醒条件 + 判断是否觉醒的时机。辉逝**只满足条件**，
    觉醒仍要等它自己的时机（【潜心】= 造成伤害后）；辉逝自己的 -2 上限立即结算。"""
    state = _state(_seats(hp=3, hp_max=4, other_hero="jie_xushu"), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine, HUI_SHI, [Script({"activate": HUI_SHI, "seat": "seat:1"}), Script()]
    )

    assert awakening_condition_satisfied(engine.state, 1, QIANXUE), "条件被标成「视为满足」"
    assert awakening_skill_awakened(engine.state, 1, QIANXUE) is False, "没有立即结算"
    assert engine.state.player(1).hp_max == 4, "【潜心】的上限 -1 还没发生"
    assert JIANYAN not in engine.state.player(1).granted_skills, "☆【荐言】还没授予"
    assert (engine.state.player(0).hp, engine.state.player(0).hp_max) == (2, 2), (
        "辉逝 -2 立即结算：3/4 ⇒ 2/2（体力夹到新上限）"
    )


def test_huishi_flagged_awakening_triggers_when_its_own_timing_arrives():
    """时机到了才觉醒：让界徐庶造成一次伤害 ⇒ 【潜心】（DAMAGE_AFTER_DEAL）触发。"""
    state = _state(_seats(hp=3, hp_max=4, other_hero="jie_xushu"), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine, HUI_SHI, [Script({"activate": HUI_SHI, "seat": "seat:1"}), Script()]
    )
    engine.push_frame(
        FrameKind.DAMAGE,
        {
            "source": 1,
            "targets": (0,),
            "card": None,
            "use_timing": None,
            "target": 0,
            "channel": None,
            "nature": DamageNature.NORMAL,
            "chain": False,
            "amount": 1,
        },
        timing=Timing.DAMAGE_DEAL,
    )
    _drive_until(
        engine,
        [Script(), Script()],
        lambda e: awakening_skill_awakened(e.state, 1, QIANXUE),
    )

    assert engine.state.player(1).hp_max == 3, "【潜心】的上限 -1 在它自己的时机结算"
    assert JIANYAN in engine.state.player(1).granted_skills
    assert awakening_condition_satisfied(engine.state, 1, QIANXUE) is False, "觉醒后清位"


def test_huishi_on_self_satisfies_tianyi_and_awakens_it_next_prepare_phase():
    """对自己用辉逝：只把【天翊】的觉醒条件标成满足（不是立刻 +2 上限 / 回复）——天翊
    仍要等**神郭嘉的下一个准备阶段**；辉逝自己的 -2 上限立即结算（4/4 ⇒ 2/2）。"""
    state = _state(_seats(hp=4, hp_max=4), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine,
        HUI_SHI,
        [Script({"activate": HUI_SHI, "seat": "seat:0"}), Script()],
    )

    assert awakening_condition_satisfied(engine.state, 0, TIANYI), "只满足条件"
    assert awakening_skill_awakened(engine.state, 0, TIANYI) is False
    assert (engine.state.player(0).hp, engine.state.player(0).hp_max) == (2, 2), (
        "辉逝 -2 立即结算（4/4 ⇒ 2/2）"
    )
    assert ZUOXING not in engine.state.player(0).granted_skills

    _drive_until(
        engine,
        [Script({"grant-seat": "seat:0"}), Script()],
        lambda e: awakening_skill_awakened(e.state, 0, TIANYI),
    )

    assert (engine.state.player(0).hp, engine.state.player(0).hp_max) == (3, 4), (
        "下个准备阶段觉醒：+2 上限（2 → 4）并回复 1（2 → 3）"
    )
    assert ZUOXING in engine.state.player(0).granted_skills


def test_huishi_draws_four_when_the_target_has_no_untriggered_awakening():
    state = _state(_seats(other_hero="guojia"), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine, HUI_SHI, [Script({"activate": HUI_SHI, "seat": "seat:1"}), Script()]
    )

    assert len(engine.state.player(1).hand) == 4, "假支 = 其摸四张牌"
    assert (engine.state.player(0).hp, engine.state.player(0).hp_max) == (1, 1), (
        "3/3 减 2 ⇒ 1/1（体力同步夹到新上限）"
    )


def test_huishi_is_a_limited_skill_once_per_game():
    state = _state(_seats(other_hero="guojia"), deck=_deck_with())
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(
        engine, HUI_SHI, [Script({"activate": HUI_SHI, "seat": "seat:1"}), Script()]
    )

    assert limited_skill_used(engine.state, 0, HUI_SHI)


# ---------------------------------------------------------------------------
# 天翊
# ---------------------------------------------------------------------------


def _tianyi_state(*, damaged: bool = True) -> GameState:
    state = _state(_seats(other_hero="guojia"), deck=_deck_with(), phase=Phase.PREPARE)
    state.player(0).damage_taken_ever = damaged
    state.player(1).damage_taken_ever = damaged
    return state


def test_tianyi_ignores_other_seats_prepare_phases():
    """觉醒的**时机**只认神郭嘉**自己的**准备阶段（用户 2026-11-14 订正：此前每个角色的
    准备阶段都会触发）。条件全部成立也不在别人的准备阶段觉醒。"""
    state = _state(
        _seats(other_hero="guojia"),
        deck=_deck_with(),
        phase=Phase.PREPARE,
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=7)
    assert engine.satisfy_awakening_condition(0, TIANYI) is True, "条件被标成「视为满足」"
    _drive_until(
        engine,
        [Script({"grant-seat": "seat:0"}), Script({"grant-seat": "seat:0"})],
        lambda e: e.state.turn_seat != 1 or e.state.phase is not Phase.PREPARE,
    )

    assert awakening_skill_awakened(engine.state, 0, TIANYI) is False, "别人的准备阶段不触发"
    assert engine.state.player(0).hp_max == 3, "上限不动"
    assert ZUOXING not in engine.state.player(0).granted_skills


def test_tianyi_awakens_only_when_every_living_seat_has_been_damaged():
    state = _tianyi_state(damaged=False)
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(engine, TIANYI, [Script(), Script()])

    assert engine.state.player(0).hp_max == 3, "有存活角色没受过伤 ⇒ 不触发"
    assert ZUOXING not in engine.state.player(0).granted_skills


def test_tianyi_adds_two_max_hp_recovers_one_and_grants_zuoxing():
    state = _tianyi_state()
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(engine, TIANYI, [Script({"grant-seat": "seat:1"}), Script()])

    assert engine.state.player(0).hp_max == 5, "加 2 点体力上限"
    assert engine.state.player(0).hp == 4, "回复 1 点体力（3 + 1）"
    assert ZUOXING in engine.state.player(1).granted_skills, "可以授予别的角色"
    assert awakening_skill_awakened(engine.state, 0, TIANYI)


def test_tianyi_can_grant_zuoxing_to_itself():
    state = _tianyi_state()
    engine = Engine.from_state(state, seed=7)
    _drive_until_done(engine, TIANYI, [Script({"grant-seat": "seat:0"}), Script()])

    assert ZUOXING in engine.state.player(0).granted_skills


# ---------------------------------------------------------------------------
# ☆佐幸
# ---------------------------------------------------------------------------


def test_zuoxing_pays_guojia_max_hp_and_uses_a_source_free_virtual_trick():
    """持有者 = 神郭嘉本人；产物是**无源纯虚拟牌**（无色 / 无点 / 无属性）。"""
    other_hand = (_of_suit(Suit.CLUB)[0],)
    state = _state(
        _seats(other_hero="guojia", other_hand=other_hand),
        deck=_deck_with(exclude=other_hand),
    )
    engine = Engine.from_state(state, seed=7)
    engine.grant_skill(0, ZUOXING)
    captured: list[CardRef] = []
    _drive_until_done(
        engine,
        ZUOXING,
        [
            Script(
                {"activate": ZUOXING, "name": "过河拆桥", "target": "seat:1"}
            ),
            Script(),
        ],
        captured=captured,
    )

    assert engine.state.player(0).hp_max == 2, "神郭嘉 -1 点体力上限"
    assert captured, "「视为使用」产生一张无源纯虚拟牌"
    card = captured[0]
    assert card.type == "dismantlement"
    assert card.suit is None and card.rank is None and card.color is None
    assert card.virtual_sources == ()
    assert engine.state.player(1).hand == [], "【过河拆桥】弃掉了目标唯一的手牌"


def test_zuoxing_cost_always_lands_on_the_guojia_character():
    """佐幸由别人持有也一样：减的是「神郭嘉」这个角色的体力上限（Q11）。"""
    other_hand = (_of_suit(Suit.CLUB)[0],)
    state = _state(
        _seats(other_hero="guojia", other_hand=other_hand),
        deck=_deck_with(exclude=other_hand),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=7)
    engine.grant_skill(1, ZUOXING)
    captured: list[CardRef] = []
    _drive_until_done(
        engine,
        ZUOXING,
        [
            Script(),
            Script({"activate": ZUOXING, "name": "过河拆桥", "target": "seat:0"}),
        ],
        captured=captured,
    )

    assert engine.state.player(0).hp_max == 2, "持有者不是神郭嘉，代价仍落在神郭嘉身上"
    assert engine.state.player(1).hp_max == 4, "持有者自己的体力上限不动"


def test_zuoxing_not_offered_when_guojia_is_dead_or_at_one_max_hp():
    for alive, hp, hp_max in ((False, 3, 3), (True, 1, 1)):
        other_hand = (_of_suit(Suit.CLUB)[0],)
        state = _state(
            _seats(
                hp=hp,
                hp_max=hp_max,
                alive=alive,
                other_hero="guojia",
                other_hand=other_hand,
            ),
            deck=_deck_with(exclude=other_hand),
            turn_seat=1,
        )
        engine = Engine.from_state(state, seed=7)
        engine.grant_skill(1, ZUOXING)
        _drive_until_done(engine, ZUOXING, [Script(), Script({"activate": ZUOXING})])

        assert not [
            record
            for record in engine.records
            if record.kind == "query"
            and any(
                isinstance(item, ActivateSkill) and item.skill_id == ZUOXING
                for item in getattr(record.query, "legal_actions", ())
            )
        ], f"前置不成立（alive={alive}, hp_max={hp_max}）时不得出现候选"


def test_zuoxing_name_list_is_the_normal_tricks_and_disables_unusable_ones():
    """牌名闭集 = 本牌堆普通锦囊（排除延时）；【无懈可击】此刻用不了 ⇒ 列出来但置灰。"""
    other_hand = (_of_suit(Suit.CLUB)[0],)
    state = _state(
        _seats(other_hero="guojia", other_hand=other_hand),
        deck=_deck_with(exclude=other_hand),
        turn_seat=1,
    )
    engine = Engine.from_state(state, seed=7)
    engine.grant_skill(1, ZUOXING)
    _drive_until_done(
        engine,
        ZUOXING,
        [
            Script(),
            Script({"activate": ZUOXING, "name": "过河拆桥", "target": "seat:0"}),
        ],
    )
    queries = [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == "SELECT_OPTION"
        and _step_key(record.query.skill_id) == "name"
    ]
    assert queries, "应发出牌名询问"
    query = queries[0]
    assert "过河拆桥" in query.options
    assert "乐不思蜀" not in query.options, "延时锦囊不在普通锦囊闭集里"
    assert "无懈可击" in query.options
    assert "无懈可击" in query.disabled, "出牌阶段没有使用时机 ⇒ 置灰"


# ---------------------------------------------------------------------------
# 陈震【歃盟】（shiji-zhi-luotong-chenzhen 票 02；用户 2026-11-14 一次签 Q1–Q7）
#
# 交互 = 预发动清单三步（选第 1 张 → 选同色第 2 张 → 选一名其他角色）**一次提交**。
# 「两张颜色相同」不是"提交后再判"：第 2 步的清单按第 1 张**逐行**下发（每行只留与它
# 同色的牌），复合动作里塞异色组合会被校验器拒收（`Q1=A`）。
# ---------------------------------------------------------------------------

SHAMENG = hero_skill_id("chenzhen", "shameng")
CHENZHEN = "chenzhen"


def _red(count: int = 1) -> list[int]:
    """前 ``count`` 张**红色**牌（♥）。"""
    return _of_suit(Suit.HEART, count)


def _black(count: int = 1) -> list[int]:
    """前 ``count`` 张**黑色**牌（♠）。"""
    return _of_suit(Suit.SPADE, count)


def _shameng_state(
    hand: tuple[int, ...],
    *,
    others: tuple[SeatSpec, ...] = (SeatSpec(),),
    equip: tuple[int, ...] = (),
    draw_phase: tuple[int, ...] = (),
    effect_draws: tuple[int, ...] = (),
    deck_only: tuple[int, ...] | None = None,
) -> GameState:
    """陈震在 0 号位、出牌阶段；``draw_phase`` = 摸牌阶段会摸到的那两张（牌堆顶）。

    ``effect_draws`` = 技能结算里要摸的牌（**目标 2 张在前、自己 3 张在后**），故断言
    可以直接点名它们。摸牌阶段的两张也算进"发动时的手牌"，本文件的先例同此。
    ``deck_only`` = 显式给一个**很小**的牌堆（短摸用例），此时不再补其余牌。
    """
    top = (*draw_phase, *effect_draws)
    used = set(hand) | set(top) | set(equip)
    if deck_only is None:
        deck = (*top, *(card_id for card_id in range(len(PACK.deck.cards)) if card_id not in used))
    else:
        deck = deck_only
    # ``RuleConfig.player_count`` 只收 2 / 4 / 5 / 6 / 7 / 8（身份局的人数）；3 人夹具按 4 记，
    # 只影响身份配比，不影响本文件的断言。
    count = 1 + len(others)
    if count not in (2, 4, 5, 6, 7, 8):
        count = 4
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=CHENZHEN, hp=3, hp_max=3, hand=hand, equip=equip),
            *others,
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=count, first_seat=0),
    )


def _next_query(engine: Engine) -> Any:
    """推进到下一步询问（``None`` = 局终）。"""
    for _ in range(400):
        if engine.pending is not None:
            return engine.pending
        if engine.finished:
            return None
        engine._step()
    raise AssertionError("询问上限内没有下一步")


def _pool(step: Any) -> tuple[str, ...]:
    """清单一步的候选（逐 ``when`` 行摊平、去重保序）。"""
    seen: list[str] = []
    for row in step.pool:
        for option in row.options:
            if option not in seen:
                seen.append(option)
    return tuple(seen)


def _row(step: Any, *when: str) -> tuple[str, ...]:
    """清单一步里 ``when`` 那一行的候选；找不到 ⇒ 空元组。"""
    for row in step.pool:
        if tuple(row.when) == tuple(when):
            return tuple(row.options)
    return ()


def _activate_shameng(query: Any, *, picks, choices) -> ActivateSkill:
    """预发动确认的复合动作（与界面同形：一次填完三步）。"""
    return ActivateSkill(
        skill_id=SHAMENG,
        picks=tuple(picks),
        choices=tuple(choices),
        answers_seq=query.seq,
    )


def test_shameng_manifest_is_two_card_steps_then_the_seat_step():
    """清单三步；第 1 步只铺"有同色伙伴"的手牌，第 2 步按第 1 张逐行只铺同色。"""
    red = _red(4)
    black = _black(1)[0]
    state = _shameng_state(
        (red[0], red[1], black), draw_phase=(red[2], red[3])
    )
    engine = Engine.from_state(state, seed=7)
    steps = tuple(engine.pending.preactivations.get(SHAMENG, ())) if engine.pending else ()
    if not steps:
        query = _next_query(engine)
        steps = tuple(query.preactivations.get(SHAMENG, ()))

    assert [step.name for step in steps] == ["card1", "card2", "seat"]
    assert [step.wording for step in steps] == [
        "选择第 1 张手牌（要与另一张手牌颜色相同）",
        "选择与第 1 张颜色相同的第 2 张手牌",
        "选择一名其他角色：其摸两张牌，然后你摸三张牌",
    ]
    assert set(_pool(steps[0])) == {str(card_id) for card_id in red}, (
        "手里没有同色伙伴的黑牌不在第 1 步（死路不进候选面）"
    )
    for first in red:
        assert set(_row(steps[1], str(first))) == {
            str(card_id) for card_id in red if card_id != first
        }, "第 2 步：与该行第 1 张同色的牌，且不含第 1 张自己"
    assert set(_pool(steps[2])) == {"seat:1"}, "目标步 = 其他存活角色"


def test_shameng_discards_two_same_coloured_cards_and_draws_two_then_three():
    """一次提交：弃两张同色手牌 → **目标**先摸 2 → 然后自己摸 3。"""
    red = _red(4)
    draws = (5, 6, 7, 8, 9)
    state = _shameng_state(
        (red[0], red[1]), draw_phase=(red[2], red[3]), effect_draws=draws
    )
    engine = Engine.from_state(state, seed=7)
    query = _next_query(engine)
    engine.submit(_activate_shameng(query, picks=(red[0], red[1]), choices=("seat:1",)))
    _next_query(engine)

    assert engine.state.discard == [red[0], red[1]], "两张同色手牌进弃牌堆"
    assert engine.state.player(1).hand == [draws[0], draws[1]], "目标拿到牌堆顶的两张"
    assert engine.state.player(0).hand[-3:] == [draws[2], draws[3], draws[4]], (
        "然后自己摸三张（顺序在目标之后）"
    )


def test_shameng_rejects_a_pair_of_different_colours():
    """异色组合**不是合法动作**：复合动作被校验器拒收，且零副作用。"""
    red = _red(4)
    black = _black(1)[0]
    state = _shameng_state(
        (red[0], red[1], black), draw_phase=(red[2], red[3])
    )
    engine = Engine.from_state(state, seed=7)
    query = _next_query(engine)

    with pytest.raises(IllegalActionError):
        engine.submit(_activate_shameng(query, picks=(red[0], black), choices=("seat:1",)))

    assert not engine.state.discard, "被拒的提交不搬牌"


def test_shameng_is_not_offered_without_a_same_coloured_pair():
    """手里每张牌都凑不出同色 ⇒ 清单渲不出来 ⇒ 此刻不可用（候选阶段就不产出）。"""
    state = _shameng_state((_red(1)[0], _black(1)[0]))

    assert preactivation_for(state, 0, SHAMENG) == ()


def test_shameng_equipment_zone_is_not_a_legal_source():
    """牌面写「手牌」⇒ 装备区的红牌不能与手牌的红牌凑成同色成本。"""
    red = _red(2)
    state = _shameng_state((red[0],), equip=(red[1],))

    assert preactivation_for(state, 0, SHAMENG) == (), "装备区不参与成本"


def test_shameng_target_pool_is_the_other_living_seats():
    """「一名其他角色」= 不含自己、不含已死亡座位。"""
    red = _red(2)
    state = _shameng_state(
        (red[0], red[1]),
        others=(SeatSpec(), SeatSpec(alive=False)),
    )

    steps = preactivation_for(state, 0, SHAMENG)

    assert set(_pool(steps[2])) == {"seat:1"}


def test_shameng_is_offered_once_per_play_phase():
    """「出牌阶段限一次」由阶段位表达：第一条后续的 PLAY_PHASE 询问里不再有清单。"""
    red = _red(4)
    state = _shameng_state(
        (red[0], red[1]), draw_phase=(red[2], red[3]), effect_draws=(5, 6, 7, 8, 9)
    )
    engine = Engine.from_state(state, seed=7)
    query = _next_query(engine)
    engine.submit(_activate_shameng(query, picks=(red[0], red[1]), choices=("seat:1",)))
    after = _next_query(engine)

    assert after is not None and after.kind == "PLAY_PHASE"
    assert SHAMENG not in after.preactivations, "本阶段已经发动过"


def test_shameng_not_submitting_costs_nothing_and_keeps_the_slot():
    """取消 = 不提交复合动作：零副作用，同一阶段随时可再确认（与【青囊】同口径）。"""
    red = _red(4)
    state = _shameng_state(
        (red[0], red[1]), draw_phase=(red[2], red[3]), effect_draws=(5, 6, 7, 8, 9)
    )
    engine = Engine.from_state(state, seed=7)
    query = _next_query(engine)

    assert SHAMENG in query.preactivations, "同一阶段随时可再确认"
    assert not engine.state.discard, "成本没有被执行"
    assert engine.state.player(1).hand == [], "目标没有摸牌"
    assert engine.state.player(0).hand == [red[0], red[1], red[2], red[3]], (
        "手牌 = 起始两张 + 摸牌阶段两张"
    )


def test_shameng_short_draw_ends_the_game_as_a_draw():
    """结算里摸牌短摸（两堆皆空）⇒ 平局并终止（账本 L803）。

    牌堆只放摸牌阶段那两张：成本弃掉的两张会被重洗回牌堆、正好被目标摸走，随后自己
    那三张无处可拿 ⇒ 平局。
    """
    red = _red(4)
    state = _shameng_state(
        (red[0], red[1]),
        draw_phase=(red[2], red[3]),
        deck_only=(red[2], red[3]),
    )
    engine = Engine.from_state(state, seed=7)
    query = _next_query(engine)
    engine.submit(_activate_shameng(query, picks=(red[0], red[1]), choices=("seat:1",)))
    _next_query(engine)

    tail = engine.records[-1]
    assert engine.finished
    assert tail.kind == "game_end"
    assert tail.result == "draw" and tail.reason == "both_decks_empty"


def test_shameng_resumes_after_the_target_gain_hook_suspends():
    """目标摸牌内联触发"获得牌后"钩子（界夏侯惇【清俭】）挂起；放弃后续跑**不重摸**。"""
    red = _red(4)
    draws = (5, 6, 7, 8, 9)
    state = _shameng_state(
        (red[0], red[1]),
        others=(SeatSpec(hero="jie_xiahoudun"),),
        draw_phase=(red[2], red[3]),
        effect_draws=draws,
    )
    engine = Engine.from_state(state, seed=7)
    query = _next_query(engine)
    engine.submit(_activate_shameng(query, picks=(red[0], red[1]), choices=("seat:1",)))

    suspended = _next_query(engine)
    assert suspended.kind == "SKILL_ACTIVATION", "目标『获得牌后』钩子把流程挂起"
    assert suspended.actor == 1 and suspended.skill_id == "hero.jie_xiahoudun.qingjian"
    engine.submit(next(item for item in suspended.legal_actions if isinstance(item, Pass)))
    _next_query(engine)

    assert engine.state.player(1).hand == [draws[0], draws[1]], "目标只摸了两张（不重摸）"
    assert engine.state.player(0).hand[-3:] == [draws[2], draws[3], draws[4]], (
        "自己照常摸三张（只一次）"
    )


def test_shameng_declares_one_shared_card_wording_and_four_keys():
    """两步选牌共用一句实时文案（`SELECT_CARDS` 恒读 `"card"`）；清单三行各读自己的 key。"""
    definition = CATALOG[SHAMENG]

    assert definition.requirement == (
        "出牌阶段限一次，你可以弃置两张颜色相同的手牌，"
        "令一名其他角色摸两张牌，然后你摸三张牌"
    )
    assert set(definition.step_requirements or {}) == {"card", "card1", "card2", "seat"}
    assert (definition.step_requirements or {})["card"].strip(), "实时选牌那一问要有话说"
