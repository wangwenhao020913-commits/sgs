"""SP武将组5：马云騄【凤魄】【马术】、潘濬【观微】【公清】
（sp5-mayunlu-panjun 票 01 / 02 / 03）。

判据全走**外部缝**：记录流里的时机 / 询问、状态事实（摸牌 / 掉血 / 距离 / 阶段序号）、
以及修饰器回调的返回值。**不 mock、不旁路候选**。

牌面权威 = 用户录入的 OL 现行文本；用户 2026-11 的签字裁定见
`.scratch/sp5-mayunlu-panjun/issues/01-mayunlu.md` / `02-panjun.md` / `03-*.md`：

- 【凤魄】「每阶段每项限一次」= **每一个角色的每一个阶段**都重置（全局阶段序号）；
  X = 双方手牌 + 装备区的 ♦ 数之和；选项 2 = 卡牌级（输掉自己使用的【决斗】也 +X）。
- 【观微】的「额外的出牌阶段」= 阶段级调度（`Engine.schedule_extra_play_phase`）；
  使用过的牌花色记录住**引擎侧**（``Player.turn_card_suits``）。
- 【公清】「<3 调整为 1」= `DAMAGE_CAP` 封顶；来源缺省 / 攻击范围 = 3 ⇒ 不调整。
"""

from __future__ import annotations

import pytest

from sgs.data import (
    Kingdom,
    RuleConfig,
    Suit,
    enabled_hero_ids,
    hero_by_id,
    load_content_pack,
)
from sgs.effects import (
    ModifierContext,
    ModifierRole,
    attack_range,
    damage_amount,
    distance,
)
from sgs.engine import Engine
from sgs.skills import CATALOG, skill_used_this_turn
from sgs.skills.hero_sp5 import (
    FENGPO,
    FENGPO_DAMAGE,
    FENGPO_DRAW,
    GONGQING,
    GUANWEI,
    _fengpo_damage_bonus,
    _fengpo_mask,
)
from sgs.state import (
    ActivateSkill,
    EndPhase,
    Pass,
    SelectCards,
    SelectOption,
    UseCard,
)
from sgs.timing import Timing

from support import (
    PassPlayer,
    SeatSpec,
    build_state,
    cards_of_type,
    declare_death,
    deck_excluding,
    ran_timing,
    run_queries,
    run_turns,
)

PACK = load_content_pack(deck_profile="standard_108")

MAYUNLU = "mayunlu"
PANJUN = "panjun"

MASHU = "hero.mayunlu.mashu"


# ---------------------------------------------------------------------------
# 数据面 / 将池
# ---------------------------------------------------------------------------


def test_mayunlu_is_enabled_with_the_two_signed_skills():
    hero = hero_by_id(MAYUNLU)
    assert hero is not None and hero.enabled
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("shu", "female", 4)
    assert [skill.id for skill in hero.skills] == ["fengpo", "mashu"]
    assert f"hero.{MAYUNLU}.fengpo" in CATALOG and MASHU in CATALOG


def test_panjun_is_enabled_with_the_two_signed_skills():
    hero = hero_by_id(PANJUN)
    assert hero is not None and hero.enabled
    assert (hero.kingdom.value, hero.gender.value, hero.hp_max) == ("wu", "male", 3)
    assert [skill.id for skill in hero.skills] == ["guanwei", "gongqing"]
    assert GUANWEI in CATALOG and GONGQING in CATALOG


def test_both_sp5_heroes_are_in_the_default_pool():
    ids = enabled_hero_ids()
    assert MAYUNLU in ids and PANJUN in ids


def test_pangtong_sp_is_enabled_with_the_three_signed_skills():
    """sp5-pangtong-liuqi 票 01：吴 / 男 / 3；☆【展骥】由**限定技**【送丧】获得（票 03）。"""
    hero = hero_by_id("pangtong_sp")
    assert hero is not None and hero.enabled
    assert (hero.name, hero.true_name, hero.kingdom.value, hero.hp_max) == (
        "SP庞统",
        "庞统",
        "wu",
        3,
    )
    assert [
        (skill.id, skill.skill_type.value, skill.granted_by) for skill in hero.skills
    ] == [
        ("guolun", "NORMAL", None),
        ("songsang", "LIMITED", None),
        ("zhanji", "NORMAL", "songsang"),
    ]
    for skill_id in (GUOLUN, SONGSANG, ZHANJI):
        assert skill_id in CATALOG


def test_liuqi_is_enabled_with_the_two_signed_skills():
    """sp5-pangtong-liuqi 票 02：群 / 男 / 3；真名「刘琦」是新的。"""
    hero = hero_by_id("liuqi")
    assert hero is not None and hero.enabled
    assert (hero.name, hero.true_name, hero.kingdom.value, hero.hp_max) == (
        "刘琦",
        "刘琦",
        "qun",
        3,
    )
    assert [skill.id for skill in hero.skills] == ["wenji", "tunjiang"]
    for skill_id in (WENJI, TUNJIANG):
        assert skill_id in CATALOG


# ---------------------------------------------------------------------------
# 马术：纯修饰器（与马超 / 界马超 / 庞德移动版同一份回调）
# ---------------------------------------------------------------------------


def _seats(*, hero: str | None = None, hand=(), equip=(), count: int = 4, hp: int = 4):
    specs = [SeatSpec(hero=hero, hand=tuple(hand), equip=tuple(equip), hp=hp, hp_max=hp)]
    specs.extend(SeatSpec() for _ in range(count - 1))
    return specs


def _state(seats, *, deck=(), turn_seat: int | None = 0, phase=None):
    return build_state(
        pack=PACK,
        seats=seats,
        deck=tuple(deck),
        turn_seat=turn_seat,
        phase=phase,
        rule_config=RuleConfig(player_count=len(seats)),
    )


def test_mashu_reduces_the_distance_by_one():
    without = _state(_seats(), turn_seat=0)
    with_mashu = _state(_seats(hero=MAYUNLU), turn_seat=0)
    # 3 人局：0 到 2 的自然距离是 2（隔一个座位）
    without_pad = build_state(
        pack=PACK,
        seats=[SeatSpec(), SeatSpec(), SeatSpec(), SeatSpec()],
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    with_pad = build_state(
        pack=PACK,
        seats=[SeatSpec(hero=MAYUNLU), SeatSpec(), SeatSpec(), SeatSpec()],
        turn_seat=0,
        rule_config=RuleConfig(player_count=4),
    )
    assert distance(without_pad, 0, 2) == 2
    assert distance(with_pad, 0, 2) == 1
    # 2 人局：自然距离 1 ⇒ 马术把下限压到 1（不会到 0）
    assert distance(without, 0, 1) == 1
    assert distance(with_mashu, 0, 1) == 1


# ---------------------------------------------------------------------------
# 凤魄：触发条件 / X / 选项 / 卡牌级加伤 / 每阶段复位
# ---------------------------------------------------------------------------


def _diamond_cards(count: int, *, exclude: tuple[int, ...] = ()) -> list[int]:
    picked = [
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.suit == Suit.DIAMOND.value and index not in exclude
    ]
    assert len(picked) >= count
    return picked[:count]


def _card_ids(pack=PACK):
    return deck_excluding(pack, *range(len(pack.deck.cards))[:0])


class _FengpoScript:
    """座位 0 的脚本：打一张牌 → 发动凤魄 → 选一项；其余交给 PassPlayer。"""

    def __init__(self, use_card, target: int, choice: str):
        self.use_card = use_card
        self.target = target
        self.choice = choice
        self.used = False
        self.activation_queries = 0
        self.option_queries = 0

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if not self.used:
                self.used = True
                return UseCard(
                    card=self.use_card, targets=(self.target,), answers_seq=query.seq
                )
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        if query.kind == "SKILL_ACTIVATION" and str(query.skill_id) == FENGPO:
            self.activation_queries += 1
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
        if query.kind == "SELECT_OPTION":
            self.option_queries += 1
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, SelectOption) and action.choice == self.choice
            )
        return PassPlayer().respond(query)


def _players(engine, first):
    return [first] + [PassPlayer() for _ in range(len(engine.state.players) - 1)]


def _run(engine, first, stop):
    return run_queries(engine, _players(engine, first), stop)


def test_fengpo_draw_option_draws_the_diamond_count_of_both_sides():
    diamonds = _diamond_cards(5)
    slash = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == "slash" and index not in diamonds
    )
    # 座位 0 手里 2 张 ♦（用来计数）；座位 1 手里 2 张 ♦
    seats = [
        SeatSpec(hero=MAYUNLU, hand=(slash, diamonds[0], diamonds[1]), hp=4, hp_max=4),
        SeatSpec(hand=(diamonds[2], diamonds[3])),
    ]
    state = _state(seats, deck=deck_excluding(PACK, slash, *diamonds))
    engine = Engine.from_state(state, seed=11)
    script = _FengpoScript(state.card(slash), 1, FENGPO_DRAW)
    # 起手 3 张 → 摸牌阶段 +2 → 用掉【杀】-1 → 凤魄摸 4 张（双方手牌里的 ♦）
    expected = 3 + 2 - 1 + 4
    _run(engine, script, lambda e: bool(_fengpo_mask(e.state, 0) & 0b1))
    assert script.activation_queries == 1
    assert script.option_queries == 1
    assert len(engine.state.player(0).hand) == expected, "X = 4（双方手牌里的 ♦）"
    assert _fengpo_mask(engine.state, 0) & 0b1, "选项 1 记了本阶段已用"


def test_fengpo_draw_with_zero_diamonds_draws_nothing_but_marks_the_item():
    slash = cards_of_type(PACK, "slash")[0]
    seats = [SeatSpec(hero=MAYUNLU, hand=(slash,), hp=4, hp_max=4), SeatSpec()]
    state = _state(seats, deck=deck_excluding(PACK, slash))
    engine = Engine.from_state(state, seed=12)
    script = _FengpoScript(state.card(slash), 1, FENGPO_DRAW)
    _run(engine, script, lambda e: bool(_fengpo_mask(e.state, 0) & 0b1))
    assert len(engine.state.player(0).hand) == 1 + 2 - 1, "X = 0：摸 0 张"
    assert _fengpo_mask(engine.state, 0) == 0b1, "X = 0 也算用过这一项"


def test_fengpo_damage_option_adds_x_to_the_duel_damage():
    diamonds = _diamond_cards(4)
    duel = next(
        index
        for index, card in enumerate(PACK.deck.cards)
        if card.type == "duel" and index not in diamonds
    )
    seats = [
        SeatSpec(hero=MAYUNLU, hand=(duel, diamonds[0], diamonds[1]), hp=4, hp_max=4),
        SeatSpec(hand=(diamonds[2], diamonds[3])),
    ]
    state = _state(seats, deck=deck_excluding(PACK, duel, *diamonds))
    engine = Engine.from_state(state, seed=13)
    script = _FengpoScript(state.card(duel), 1, FENGPO_DAMAGE)
    _run(engine, script, lambda e: e.state.player(1).hp < 4)
    assert engine.state.player(1).hp == 4 - 5, "【决斗】1 点 + X=4"
    assert _fengpo_mask(engine.state, 0) == 0b10


def test_fengpo_bonus_is_card_level_even_when_the_owner_loses_the_duel():
    """选项 2 只认「此牌」：马云騄输掉自己使用的【决斗】时，对方造成的伤害也 +X。"""
    slash = cards_of_type(PACK, "slash")[0]
    seats = [SeatSpec(hero=MAYUNLU), SeatSpec()]
    state = _state(seats)
    card = state.card(slash)
    state.player(0).fengpo_bonus = (card.card_id, 3)
    ctx = ModifierContext(
        owner=0,
        subject=1,  # 伤害来源是对方
        target=0,
        role=ModifierRole.SUBJECT,
        card=card,
    )
    assert _fengpo_damage_bonus(state, ctx, 1) == 4


def test_fengpo_items_reset_at_the_next_phase_of_any_seat():
    """「每阶段」= 每一个角色的每一个阶段：全局阶段序号一变，两项都视作未用。"""
    seats = [SeatSpec(hero=MAYUNLU), SeatSpec()]
    state = _state(seats)
    state.phase_seq = 7
    state.player(0).fengpo_used = (7, 0b11)
    assert _fengpo_mask(state, 0) == 0b11
    state.phase_seq = 8  # 任一角色的下一个阶段开始
    assert _fengpo_mask(state, 0) == 0, "序号变了 ⇒ 惰性复位"


def test_the_engine_bumps_the_phase_sequence_once_per_phase_for_every_seat():
    seats = [SeatSpec(hero=MAYUNLU), SeatSpec()]
    state = _state(seats, deck=deck_excluding(PACK), turn_seat=0)
    engine = Engine.from_state(state, seed=14)
    players = [PassPlayer() for _ in range(2)]
    start = engine.state.phase_seq
    run_turns(engine, players, turns=2)
    # 6 个阶段 / 回合 × 2 个回合 = 12 次阶段开始（两个角色的回合都算）
    assert engine.state.phase_seq - start >= 12


# ---------------------------------------------------------------------------
# 观微：条件 / 弃牌 / 摸两张 / 额外的出牌阶段
# ---------------------------------------------------------------------------


class _GuanweiScript:
    def __init__(self, discard_card: int):
        self.discard_card = discard_card
        self.play_queries = 0
        self.activation_queries = 0

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            self.play_queries += 1
            return next(action for action in query.legal_actions if isinstance(action, EndPhase))
        if query.kind == "SKILL_ACTIVATION" and str(query.skill_id) == GUANWEI:
            self.activation_queries += 1
            return next(
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill)
            )
        if query.kind == "SELECT_CARDS":
            for index, slot in enumerate(query.slots):
                card = slot.card
                if card is not None and card.card_id == self.discard_card:
                    return SelectCards(picks=(index,), answers_seq=query.seq)
            raise AssertionError("要弃的牌不在选择面里")
        return PassPlayer().respond(query)


def _guanwei_state(*, suits, hand, hp=3):
    seats = [SeatSpec(hero=PANJUN, hand=tuple(hand), hp=hp, hp_max=hp), SeatSpec()]
    state = _state(seats, deck=deck_excluding(PACK, *hand))
    state.player(0).turn_card_suits.extend(suits)
    return state


def test_guanwei_fires_at_the_end_of_the_play_phase_and_grants_an_extra_one():
    hand = cards_of_type(PACK, "slash")[:3]
    state = _guanwei_state(suits=[Suit.HEART.value, Suit.HEART.value], hand=hand)
    engine = Engine.from_state(state, seed=15)
    script = _GuanweiScript(hand[0])
    _run(
        engine,
        script,
        lambda e: e.pending is not None
        and e.pending.kind == "PLAY_PHASE"
        and script.play_queries == 1,
    )
    play_starts = [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.PHASE_PLAY_START
    ]
    assert len(play_starts) == 2, "额外的出牌阶段真的又开始了"
    assert len(engine.state.player(0).hand) == len(hand) + 2 - 1 + 2
    assert skill_used_this_turn(engine.state, 0, GUANWEI)
    assert script.activation_queries == 1


def test_guanwei_is_silent_when_the_used_suits_differ():
    hand = cards_of_type(PACK, "slash")[:2]
    state = _guanwei_state(suits=[Suit.HEART.value, Suit.SPADE.value], hand=hand)
    engine = Engine.from_state(state, seed=16)
    script = _GuanweiScript(hand[0])
    _run(engine, script, lambda e: e.state.turn_seat != 0 or e.finished)
    assert script.activation_queries == 0


def test_guanwei_is_silent_when_a_colourless_card_was_used():
    hand = cards_of_type(PACK, "slash")[:2]
    state = _guanwei_state(suits=[Suit.HEART.value, None], hand=hand)
    engine = Engine.from_state(state, seed=17)
    script = _GuanweiScript(hand[0])
    _run(engine, script, lambda e: e.state.turn_seat != 0 or e.finished)
    assert script.activation_queries == 0


def test_guanwei_is_silent_without_a_card_to_discard():
    # 条件读的是"此刻手上有没有牌"：直接问条件（走引擎的话摸牌阶段会先补两张）。
    from types import SimpleNamespace

    from sgs.skills.hero_sp5 import _guanwei_condition
    from sgs.skills.catalog import MATCH_OWNER_KEY

    state = _guanwei_state(suits=[Suit.HEART.value, Suit.HEART.value], hand=())
    host = SimpleNamespace(state=state)
    frame = SimpleNamespace(data={"source": 0, MATCH_OWNER_KEY: 0})
    assert not _guanwei_condition(host, frame)


def test_guanwei_records_each_used_card_suit_on_the_engine_side():
    slash = cards_of_type(PACK, "slash")[0]
    seats = [SeatSpec(hero=PANJUN, hand=(slash,), hp=3, hp_max=3), SeatSpec()]
    state = _state(seats, deck=deck_excluding(PACK, slash))
    engine = Engine.from_state(state, seed=19)
    script = _FengpoScript(state.card(slash), 1, "")  # 只借用"出牌阶段打一张"的脚本
    script.choice = ""
    _run(engine, script, lambda e: bool(e.state.player(0).turn_card_suits))
    assert engine.state.player(0).turn_card_suits == [
        state.card(slash).suit
    ], "start_use 逐次记花色（打出不算）"


# ---------------------------------------------------------------------------
# 公清：两个修饰器（封顶 / 加一）
# ---------------------------------------------------------------------------


def _gongqing_state(*, weapon: str | None = None, source: int | None = 1):
    equip = cards_of_type(PACK, weapon)[0] if weapon is not None else None
    specs = [
        SeatSpec(hero=PANJUN, hp=3, hp_max=3),
        SeatSpec(equip=(equip,) if equip is not None else ()),
    ]
    return _state(specs, turn_seat=0)


def test_gongqing_caps_the_damage_of_a_short_range_source():
    state = _gongqing_state()
    assert attack_range(state, 1) == 1
    assert damage_amount(state, source=1, target=0, card=None, base=2, chain=False) == 1


def test_gongqing_does_not_adjust_at_range_three():
    state = _gongqing_state(weapon="axe")
    assert attack_range(state, 1) == 3
    assert damage_amount(state, source=1, target=0, card=None, base=2, chain=False) == 2


def test_gongqing_adds_one_against_a_long_range_source():
    state = _gongqing_state(weapon="halberd")
    assert attack_range(state, 1) == 4
    assert damage_amount(state, source=1, target=0, card=None, base=1, chain=False) == 2


def test_gongqing_does_not_adjust_without_a_source():
    state = _gongqing_state()
    assert damage_amount(state, source=None, target=0, card=None, base=2, chain=False) == 2


def test_gongqing_only_applies_to_its_owner():
    state = _gongqing_state()
    # 座位 1 没有公清 ⇒ 它挨打时不封顶
    assert damage_amount(state, source=0, target=1, card=None, base=2, chain=False) == 2


# ---------------------------------------------------------------------------
# SP庞统【过论】（sp5-pangtong-liuqi 票 01）
# ---------------------------------------------------------------------------


def _table(seats, *, deck=(), turn_seat: int | None = 0, phase=None):
    """多座位的构造局面（**不**经 ``RuleConfig``：默认配置 = 无身份局，3 座也不要求主公）。

    本文件既有的 ``_state`` 按座位数配 ``RuleConfig``（3 不在值域内）；送丧 / 展骥 / 问计 /
    屯江 需要"至少还有一个别人活着"的局面，故另给这一条。
    """
    return build_state(
        pack=PACK,
        seats=tuple(seats),
        deck=tuple(deck),
        turn_seat=turn_seat,
        phase=phase,
    )


PANGTONG = "pangtong_sp"
LIUQI = "liuqi"
GUOLUN = "hero.pangtong_sp.guolun"
SONGSANG = "hero.pangtong_sp.songsang"
ZHANJI = "hero.pangtong_sp.zhanji"
WENJI = "hero.liuqi.wenji"
TUNJIANG = "hero.liuqi.tunjiang"
_WUZHONG = next(
    card_id for card_id, card in enumerate(PACK.deck.cards) if card.type == "ex_nihilo"
)


def _card_by_type(type_id: str, *, skip: tuple[int, ...] = ()) -> int:
    return next(
        card_id
        for card_id, card in enumerate(PACK.deck.cards)
        if card.type == type_id and card_id not in skip
    )


def _rank(card_id: int) -> int:
    return PACK.deck.cards[card_id].rank  # type: ignore[return-value]


def _lower_and_higher_ranks() -> tuple[int, int]:
    """一对点数严格不同的牌（``rank(低) < rank(高)``），用来钉"点数小的一方摸一张"。"""
    cards = [
        (card_id, card.rank)
        for card_id, card in enumerate(PACK.deck.cards)
        if card.rank is not None and card.type in ("slash", "dodge", "peach")
    ]
    low_id, low_rank = min(cards, key=lambda pair: pair[1])  # type: ignore[type-var]
    high_id, high_rank = max(cards, key=lambda pair: pair[1])  # type: ignore[type-var]
    assert low_rank < high_rank
    return low_id, high_id


class _GuolunScript:
    """座位 0：发动过论 → 选座位 → 盲挑对面手牌 → （可选）选自己的牌 / 放弃。"""

    def __init__(self, *, target: int = 1, own_card: int | None = None, exchange: bool = True):
        self.target = target
        self.own_card = own_card
        self.exchange = exchange
        self.activations = 0
        self.seat_queries = 0
        self.hidden_picks = 0
        self.exchange_queries = 0
        self.own_picks = 0

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            candidates = [
                action
                for action in query.legal_actions
                if isinstance(action, ActivateSkill) and action.skill_id == GUOLUN
            ]
            if candidates and self.activations == 0:
                self.activations += 1
                return candidates[0]
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        if query.kind == "SELECT_OPTION":
            self.seat_queries += 1
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == f"seat:{self.target}":
                    return action
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if query.kind == "SELECT_CARDS":
            hidden = all(slot.card is None for slot in query.slots)
            if hidden:
                self.hidden_picks += 1
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, SelectCards)
                )
            self.exchange_queries += 1
            if not self.exchange:
                return next(
                    action for action in query.legal_actions if isinstance(action, Pass)
                )
            self.own_picks += 1
            for index, slot in enumerate(query.slots):
                if slot.card is not None and slot.card.card_id == self.own_card:
                    for action in query.legal_actions:
                        if isinstance(action, SelectCards) and action.picks == (index,):
                            return action
            return next(
                action for action in query.legal_actions if isinstance(action, SelectCards)
            )
        return PassPlayer().respond(query)


def _finish_guolun(engine, script, players):
    """推进到"过论 这一轮结算完、回到出牌阶段再问一次"的那个点。"""
    return run_queries(
        engine,
        players,
        lambda current: script.hidden_picks >= 1
        and current.pending is not None
        and current.pending.kind == "PLAY_PHASE"
        and int(current.pending.actor) == 0,
    )


def _guolun_engine(my_card: int, their_card: int, *, exchange: bool = True):
    """座位 0 = SP庞统（手里 ``my_card``）、座位 1 手里 ``their_card``；座位 2 空手。"""
    seats = [
        SeatSpec(hero=PANGTONG, hand=(my_card,), hp=3, hp_max=3),
        SeatSpec(hand=(their_card,), hp=3, hp_max=3),
        SeatSpec(hp=3, hp_max=3),
        SeatSpec(hp=3, hp_max=3),
    ]
    state = _table(seats, deck=deck_excluding(PACK, my_card, their_card))
    engine = Engine.from_state(state, seed=21)
    script = _GuolunScript(own_card=my_card, exchange=exchange)
    return engine, script, state


def test_guolun_reveals_the_card_swaps_and_the_smaller_side_draws():
    """Q4 / Q5 的正面：点数小的我摸一张；两张牌都进对方手牌（经处理区）。"""
    low, high = _lower_and_higher_ranks()
    engine, script, state = _guolun_engine(low, high)
    players = [script] + [PassPlayer() for _ in range(3)]
    _finish_guolun(engine, script, players)

    assert _rank(low) < _rank(high)
    assert script.activations == 1 and script.hidden_picks == 1 and script.own_picks == 1
    hand = engine.state.player(0).hand
    assert high in hand and low not in hand, "交换成立：我拿到对方的高点数牌"
    assert engine.state.player(1).hand == [low], "我的低点数牌进了对方手牌"
    # 起手 1 + 摸牌阶段 2 - 交出去 1 + 换来 1 + 摸 1（点数小的是我）
    assert len(hand) == 1 + 2 - 1 + 1 + 1, "点数小的一方（我）摸一张"


def test_guolun_equal_ranks_draw_nothing():
    """点数相同 ⇒ 没有"点数小的一方" ⇒ 谁都不摸（Q5 = 对，牌面要严格小于）。"""
    same = [
        card_id
        for card_id, card in enumerate(PACK.deck.cards)
        if card.rank is not None and card.type in ("slash", "dodge", "peach")
    ]
    by_rank: dict[int, list[int]] = {}
    for card_id in same:
        by_rank.setdefault(_rank(card_id), []).append(card_id)
    rank, ids = next((rank, ids) for rank, ids in by_rank.items() if len(ids) >= 2)
    low, high = ids[0], ids[1]
    assert _rank(low) == _rank(high) == rank
    engine, script, state = _guolun_engine(low, high)
    players = [script] + [PassPlayer() for _ in range(3)]
    _finish_guolun(engine, script, players)

    hand = engine.state.player(0).hand
    assert high in hand and low not in hand, "交换照做"
    assert len(hand) == 1 + 2 - 1 + 1, "点数相同 ⇒ 谁都不摸（起手 + 摸牌阶段 - 交出 + 换来，没有多出来的一张）"


def test_guolun_declining_the_exchange_keeps_both_cards_and_draws_nothing():
    """Q5 / 「可以」= 放弃出口：展示照做，但不交换、也不摸牌。"""
    low, high = _lower_and_higher_ranks()
    engine, script, state = _guolun_engine(low, high, exchange=False)
    players = [script] + [PassPlayer() for _ in range(3)]
    _finish_guolun(engine, script, players)

    assert script.hidden_picks == 1 and script.exchange_queries == 1 and script.own_picks == 0
    assert low in engine.state.player(0).hand, "没换：我的牌还在我手里"
    assert engine.state.player(1).hand == [high], "对方那张也没动"
    assert len(engine.state.player(0).hand) == 1 + 2, "放弃交换 ⇒ 也不摸牌"


def test_guolun_is_not_offered_without_an_opponent_hand_card():
    """G1：全场只有我没手牌 ⇒ 候选阶段就不产出入口（不发空询问）。"""
    mine, _ = _lower_and_higher_ranks()
    seats = [
        SeatSpec(hero=PANGTONG, hand=(mine,), hp=3, hp_max=3),
        SeatSpec(),
        SeatSpec(),
        SeatSpec(),
    ]
    state = _table(seats, deck=deck_excluding(PACK, mine))
    engine = Engine.from_state(state, seed=22)
    run_queries(engine, [PassPlayer() for _ in range(4)], lambda current: bool(current.pending))
    offered = [
        action.skill_id
        for action in engine.pending.legal_actions
        if isinstance(action, ActivateSkill)
    ]
    assert GUOLUN not in offered


# ---------------------------------------------------------------------------
# SP庞统【送丧】/ ☆【展骥】（sp5-pangtong-liuqi 票 01；票 03 的 granted_by 放宽）
# ---------------------------------------------------------------------------


class _SongsangScript:
    """座位 0：送丧的"是否发动"按脚本答；其余交给 ``PassPlayer``。"""

    def __init__(self, *, activate: bool = True):
        self.activate = activate
        self.queries = 0

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and str(query.skill_id) == SONGSANG:
            self.queries += 1
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        return PassPlayer().respond(query)


def _run_songsang(*, hp: int, hp_max: int = 3, activate: bool = True, dead: int = 1):
    seats = [
        SeatSpec(hero=PANGTONG, hp=hp, hp_max=hp_max),
        SeatSpec(),
        SeatSpec(),
    ]
    engine = Engine.from_state(_table(seats), seed=31)
    scripts = [_SongsangScript(activate=activate)] + [
        PassPlayer() for _ in range(2)
    ]
    declare_death(engine, dead)
    run_queries(
        engine,
        scripts,
        lambda current: bool(current.state.player(0).granted_skills)
        or ran_timing(current, Timing.DEATH_AFTER),
    )
    return engine, scripts


def test_songsang_heals_one_hp_when_wounded_and_grants_zhanji():
    """送丧的分支 1：已受伤 ⇒ 回复 1 点（RECOVER 帧），然后获得 ☆【展骥】。"""
    engine, scripts = _run_songsang(hp=2)
    owner = engine.state.player(0)
    assert (owner.hp, owner.hp_max) == (3, 3), "回复 1 点体力"
    assert owner.granted_skills == [ZHANJI], "若如此做 ⇒ 获得 ☆【展骥】"
    assert scripts[0].queries == 1, "一次死亡只问一次"


def test_songsang_raises_the_hp_max_without_syncing_hp():
    """送丧的分支 2：未受伤 ⇒ +1 体力上限（MAXLIFE 帧、**不同步体力**）。"""
    engine, scripts = _run_songsang(hp=3)
    owner = engine.state.player(0)
    assert owner.hp_max == 4, "上限 +1"
    assert owner.hp == 3, "不同步体力（shapes.md §3.6 的 MAXLIFE 口径）"
    assert owner.granted_skills == [ZHANJI]
    assert scripts[0].queries == 1


def test_songsang_does_not_trigger_on_its_own_death():
    """「其他角色」不含自己：庞统自己死亡时不问、也不获得展骥。"""
    engine, scripts = _run_songsang(hp=1, dead=0)
    assert scripts[0].queries == 0
    assert engine.state.player(0).granted_skills == []


def test_songsang_is_a_limited_skill_that_asks_only_once():
    """限定技：整局一次。第二次死亡不再问（"已用"位由派发环在提问前挡掉）。"""
    seats = [SeatSpec(hero=PANGTONG, hp=3, hp_max=3), SeatSpec(), SeatSpec()]
    engine = Engine.from_state(_table(seats), seed=33)
    script = _SongsangScript()
    scripts = [script] + [PassPlayer() for _ in range(2)]
    declare_death(engine, 1)
    run_queries(engine, scripts, lambda current: bool(current.state.player(0).granted_skills))
    assert script.queries == 1
    declare_death(engine, 2)
    run_queries(
        engine,
        scripts,
        lambda current: ran_timing(current, Timing.DEATH_AFTER),
    )
    assert script.queries == 1, "限定技：第二次死亡连问都不发"
    assert engine.state.player(0).granted_skills == [ZHANJI], "也不会重复获得"


class _UseOneCardScript:
    """座位 0：出牌阶段打一张指定的牌（目标可空），之后结束阶段。"""

    def __init__(self, card, *, target: int | None = None):
        self.card = card
        self.target = target
        self.used = False

    def respond(self, query):
        if query.kind == "PLAY_PHASE":
            if not self.used:
                self.used = True
                return UseCard(
                    card=self.card,
                    targets=(() if self.target is None else (self.target,)),
                    answers_seq=query.seq,
                )
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        return PassPlayer().respond(query)


def test_zhanji_draws_one_more_card_when_the_owner_draws_in_the_play_phase():
    """展骥：出牌阶段内**摸牌** ⇒ 单独再摸一张（不是把本次摸牌数 +1）。"""
    card = _WUZHONG
    seats = [
        SeatSpec(hero=PANGTONG, hand=(card,), hp=3, hp_max=3),
        SeatSpec(),
        SeatSpec(),
    ]
    engine = Engine.from_state(
        _table(seats, deck=deck_excluding(PACK, card)), seed=34
    )
    script = _UseOneCardScript(engine.state.card(card), target=0)  # 无中生有对自己
    run_queries(
        engine,
        [script, PassPlayer(), PassPlayer()],
        lambda current: len(current.state.player(0).hand) == 5,
    )
    # 起手 1 + 摸牌阶段 2 - 用掉 1 + 无中 2 + 展骥 1
    assert len(engine.state.player(0).hand) == 5, "无中摸 2 ⇒ 展骥再单独摸 1"
    assert engine.state.player(0).hand.count(card) == 0


def test_zhanji_does_not_trigger_outside_the_play_phase():
    """「于出牌阶段内」按自己的回合读：摸牌阶段摸的 2 张不触发。"""
    seats = [
        SeatSpec(hero=PANGTONG, hp=3, hp_max=3),
        SeatSpec(),
        SeatSpec(),
    ]
    engine = Engine.from_state(_table(seats, deck=deck_excluding(PACK)), seed=35)
    run_queries(
        engine,
        [PassPlayer() for _ in range(3)],
        lambda current: current.pending is not None
        and current.pending.kind == "PLAY_PHASE",
    )
    assert len(engine.state.player(0).hand) == 2, "摸牌阶段没有多摸（展骥不触发）"


# ---------------------------------------------------------------------------
# 刘琦【问计】/【屯江】（sp5-pangtong-liuqi 票 02；票 05 的两条引擎侧回合事实）
# ---------------------------------------------------------------------------


class _WenjiActor:
    """座位 0（刘琦）：发动问计 → 选座位 1 → 出牌阶段打出手里的【杀】。"""

    def __init__(self, *, play_card: int | None = None, target: int = 1):
        self.play_card = play_card
        self.target = target
        self.activations = 0
        self.seat_queries = 0
        self.played = False

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and str(query.skill_id) == WENJI:
            self.activations += 1
            return next(
                action for action in query.legal_actions if isinstance(action, ActivateSkill)
            )
        if query.kind == "SELECT_OPTION":
            self.seat_queries += 1
            for action in query.legal_actions:
                if isinstance(action, SelectOption) and action.choice == f"seat:{self.target}":
                    return action
            return next(action for action in query.legal_actions if isinstance(action, Pass))
        if query.kind == "PLAY_PHASE":
            if self.play_card is not None and not self.played:
                self.played = True
                return UseCard(
                    card=self.play_card,
                    targets=(self.target,),
                    answers_seq=query.seq,
                )
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        return PassPlayer().respond(query)


class _WenjiGiver:
    """座位 1：交牌时挑指定的那张；被打时记录【闪】询问（用来证明"能不能被响应"）。"""

    def __init__(self, *, give: int):
        self.give = give
        self.dodges = 0

    def respond(self, query):
        if query.kind == "SELECT_CARDS":
            for index, slot in enumerate(query.slots):
                if slot.card is not None and slot.card.card_id == self.give:
                    for action in query.legal_actions:
                        if isinstance(action, SelectCards) and action.picks == (index,):
                            return action
            return next(
                action for action in query.legal_actions if isinstance(action, SelectCards)
            )
        if query.kind == "DODGE":
            self.dodges += 1
        return PassPlayer().respond(query)


def _wenji_engine(*, give: int, play_after: int | None):
    seats = [
        SeatSpec(hero=LIUQI, hp=3, hp_max=3),
        SeatSpec(hand=(give,), hp=3, hp_max=3),
        SeatSpec(),
    ]
    engine = Engine.from_state(
        _table(seats, deck=deck_excluding(PACK, give)), seed=41
    )
    actor = _WenjiActor(
        play_card=None if play_after is None else engine.state.card(play_after)
    )
    giver = _WenjiGiver(give=give)
    return engine, actor, giver


def test_wenji_takes_a_card_and_blocks_the_same_name_for_the_turn():
    """问计：目标自己挑一张交给你；本回合同名牌不能被响应（不产生【闪】窗口）。"""
    slash = _card_by_type("slash")
    engine, actor, giver = _wenji_engine(give=slash, play_after=slash)
    run_queries(
        engine,
        [actor, giver, PassPlayer()],
        lambda current: current.state.player(1).hp == 2,
    )
    owner = engine.state.player(0)
    assert actor.activations == 1 and actor.seat_queries == 1
    assert owner.wenji_names == ("slash",), "记下了交来的那张牌的牌名"
    assert slash not in engine.state.player(1).hand, "那张牌已经交出去了"
    assert engine.state.player(0).designated_other_this_turn is True, "【杀】指定了别人"
    assert giver.dodges == 0, "同名牌不能被其他角色响应（连【闪】窗口都不产生）"
    assert engine.state.player(1).hp == 2, "【杀】直接命中（3 → 2）"


def test_wenji_does_not_block_a_card_with_another_name():
    """禁令只覆盖"与此牌牌名相同"的牌：交来【闪】后打【杀】，目标照常被问【闪】。"""
    dodge = _card_by_type("dodge")
    slash = _card_by_type("slash", skip=(dodge,))
    seats = [
        SeatSpec(hero=LIUQI, hand=(slash,), hp=3, hp_max=3),
        SeatSpec(hand=(dodge,)),
        SeatSpec(),
    ]
    engine = Engine.from_state(
        _table(seats, deck=deck_excluding(PACK, dodge, slash)), seed=42
    )
    actor = _WenjiActor(play_card=engine.state.card(slash))
    giver = _WenjiGiver(give=dodge)
    run_queries(
        engine,
        [actor, giver, PassPlayer()],
        lambda current: giver.dodges >= 1,
    )
    assert engine.state.player(0).wenji_names == ("dodge",)
    assert giver.dodges == 1, "牌名不同的【杀】照常询问【闪】"


def test_wenji_is_not_offered_when_nobody_can_give_a_card():
    """G1 / S4：其他角色都没有牌 ⇒ 条件不成立、连问都不发。"""
    seats = [SeatSpec(hero=LIUQI, hp=3, hp_max=3), SeatSpec(), SeatSpec()]
    engine = Engine.from_state(_table(seats, deck=deck_excluding(PACK)), seed=43)
    run_queries(
        engine,
        [PassPlayer() for _ in range(3)],
        lambda current: current.pending is not None
        and current.pending.kind == "PLAY_PHASE",
    )
    assert engine.state.player(0).wenji_names == ()
    assert len(engine.state.player(0).hand) == 2, "只有摸牌阶段的两张，没有人交牌"


class _TunjiangScript:
    """座位 0（刘琦）：按脚本决定出牌阶段打什么、结束时是否发动屯江。"""

    def __init__(self, *, activate: bool = True, play_card=None, target: int | None = None):
        self.activate = activate
        self.play_card = play_card
        self.target = target
        self.queries = 0
        self.played = False

    def respond(self, query):
        if query.kind == "SKILL_ACTIVATION" and str(query.skill_id) == TUNJIANG:
            self.queries += 1
            if self.activate:
                return next(
                    action
                    for action in query.legal_actions
                    if isinstance(action, ActivateSkill)
                )
            return next(
                action for action in query.legal_actions if isinstance(action, Pass)
            )
        if query.kind == "PLAY_PHASE":
            if self.play_card is not None and not self.played:
                self.played = True
                return UseCard(
                    card=self.play_card,
                    targets=(() if self.target is None else (self.target,)),
                    answers_seq=query.seq,
                )
            return next(
                action for action in query.legal_actions if isinstance(action, EndPhase)
            )
        return PassPlayer().respond(query)


def _tunjiang_seats(*, hand=(), kingdom0=None):
    return [
        SeatSpec(hero=LIUQI, hp=3, hp_max=3, hand=tuple(hand), kingdom=kingdom0),
        SeatSpec(kingdom=Kingdom.WEI),
        SeatSpec(kingdom=Kingdom.SHU),
    ]


def test_tunjiang_draws_the_number_of_alive_kingdoms():
    """X = 存活角色的势力种数（群 / 魏 / 蜀 = 3）；自己的结束阶段摸 3 张。"""
    seats = _tunjiang_seats()
    state = _table(seats, deck=deck_excluding(PACK))
    state.player(0).kingdom = Kingdom.QUN
    engine = Engine.from_state(state, seed=44)
    script = _TunjiangScript()
    run_queries(
        engine,
        [script, PassPlayer(), PassPlayer()],
        lambda current: script.queries >= 1 and len(current.state.player(0).hand) == 5,
    )
    assert script.queries == 1
    # 空手起 → 摸牌阶段 2 → 弃牌阶段不动（上限 3）→ 结束阶段 +3
    assert len(engine.state.player(0).hand) == 5, "X = 3 个势力"


def test_tunjiang_is_not_offered_when_the_play_phase_was_skipped():
    """条件 (a)：本回合跳过出牌阶段 ⇒ 结束阶段不问（引擎侧回合事实，票 05）。"""
    engine = Engine.from_state(
        _table([SeatSpec(hero=LIUQI, hp=3, hp_max=3), SeatSpec(), SeatSpec()],
               deck=deck_excluding(PACK)),
        seed=45,
    )
    engine.skip_play_phase(0)
    assert engine.state.player(0).play_phase_skipped_this_turn is True
    script = _TunjiangScript()
    run_queries(
        engine,
        [script, PassPlayer(), PassPlayer()],
        lambda current: ran_timing(current, Timing.TURN_END),
    )
    assert script.queries == 0, "跳过出牌阶段 ⇒ 屯江不发动"


def test_tunjiang_is_not_offered_after_designating_another_seat():
    """条件 (b)：出牌阶段用牌指定过其他角色 ⇒ 结束阶段不问（引擎侧回合事实，票 05）。"""
    slash = _card_by_type("slash")
    engine = Engine.from_state(
        _table(
            [SeatSpec(hero=LIUQI, hand=(slash,), hp=3, hp_max=3), SeatSpec(), SeatSpec()],
            deck=deck_excluding(PACK, slash),
        ),
        seed=46,
    )
    assert engine.state.player(0).designated_other_this_turn is False
    script = _TunjiangScript(play_card=engine.state.card(slash), target=1)
    run_queries(
        engine,
        [script, PassPlayer(), PassPlayer()],
        lambda current: ran_timing(current, Timing.TURN_END),
    )
    assert engine.state.player(0).designated_other_this_turn is True
    assert script.queries == 0, "指定过其他角色 ⇒ 屯江不发动"
