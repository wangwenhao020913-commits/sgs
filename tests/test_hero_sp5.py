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

from sgs.data import RuleConfig, Suit, enabled_hero_ids, hero_by_id, load_content_pack
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
    deck_excluding,
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
