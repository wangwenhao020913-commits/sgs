"""界一将成名-2014·移动版：界陈群移动版【定品】（jie-yijiang-2014-mobile 票 01）。

票面 = ``.scratch/chenqun-guyong/issues/01-chenqun.md``（用户 2026-11-09 签 Q0–Q8；
【法恩】C1 摘出，见同 effort 的 ``03-faen-timing-gap.md``）。牌面权威 = 用户 ``技能.txt``
逐字（与移动版 WIKI 同值，票面 §0.1）：

    【定品】出牌阶段，你可以弃置一张牌（不能是本回合使用或弃置过的类型），令一名角色进行
            一次判定，若结果为：黑色，其摸X张牌（X为其体力值且至多为3），然后你本回合不能
            再对其发动此技能；♥️，你本次以此法弃置的牌不计入弃置过的限制；♦️，你翻面。

用户签的四条口径（Q1–Q4）：

- 「类型」= **大类**（基本 / 锦囊 / 装备）；
- 「本回合使用或弃置过的类型」= 座位级事实 ``Player.turn_card_entries``（动作**主体**是
  弃置者 / 使用者本人；回合末复位）；
- 「本回合不能再对其发动」= ``Player.dingpin_barred_targets``；
- ♥ 分支按**张**撤回（``Engine.exempt_turn_card``）。

判据全走**外部缝**：预发动清单（``query.preactivations``）、记录流里的判定 / 搬牌、
座位上的体力 / 翻面 / 手牌数。不断言内部结构，也不 mock。

**票 02（界顾雍移动版【慎行】）**：同一个界一将成名-2014·移动版包的第二名武将——出牌阶段
限 X 次（X = 当下体力值）、弃两张、颜色相同摸一否则摸二。计数落 ``skill_state`` 的 4 位
计数位段（``_PHASE_USE_SHIFT`` = 32），X 每次判可用时现算（用户 2026-11-09 签 Q1–Q3）。
【秉壹】因 C3（用户改判成"一次点选多个座位"）摘出，见同 effort 的 ``05-seat-multi-select.md``。

**牌堆控制**：``Engine.from_state`` 的局面从**回合开头**跑起（准备 → 判定 → 摸牌 → 出牌），
故本文件的夹具把 2 张摸牌阶段要摸的牌放在牌堆头、要控制的牌（判定牌 / 慎行的摸牌）放在其后。
"""

from __future__ import annotations

from sgs.data import RuleConfig, Suit, card_category, color_of_suit, load_content_pack
from sgs.engine import Engine
from sgs.state import ActivateSkill, EndPhase, FrameKind, Pass, Phase, QueryKind, UseCard, Zone
from sgs.timing import Timing

from support import RecordingPlayer, SeatSpec, build_state, deck_excluding, ran_timing

PACK = load_content_pack(deck_profile="standard_108")

HERO = "jie_chenqun_mobile"
DINGPIN = "hero.jie_chenqun_mobile.dingpin"

GUYONG = "jie_guyong_mobile"
SHENXING = "hero.jie_guyong_mobile.shenxing"

DEAL = 4
"""``RuleConfig.starting_hand_size`` 的缺省值；本文件的夹具不依赖它（`from_state` 不发牌），
只在 ``_hand_of`` 里当作"一手 4 张"的构造约定。"""


# ---------------------------------------------------------------------------
# 牌面取样
# ---------------------------------------------------------------------------


def _category_of(card_id: int) -> str:
    kind = card_category(PACK.pack_id, PACK.deck.cards[card_id].type)
    assert kind is not None
    return kind.value


def _first_of(*, category: str = "", exclude: tuple[int, ...] = ()) -> int:
    for card_id in range(len(PACK.deck.cards)):
        if card_id in exclude:
            continue
        kind = card_category(PACK.pack_id, PACK.deck.cards[card_id].type)
        if kind is None:
            continue
        if not category or kind.value == category:
            return card_id
    raise AssertionError(f"标准 108 里找不到大类 {category or '<任意>'} 的牌")


def _basic_with_suit(suit: Suit, *, exclude: tuple[int, ...] = ()) -> int:
    """除 ``exclude`` 外，挑一张**基本牌**里花色 = ``suit`` 的牌（当判定牌用）。"""
    for card_id in range(len(PACK.deck.cards)):
        if card_id in exclude:
            continue
        card = PACK.deck.cards[card_id]
        if card.suit is suit and _category_of(card_id) == "basic":
            return card_id
    raise AssertionError(f"标准 108 里没有花色 {suit} 的基本牌")


def _hand_of(*, basic: int = 0, trick: int = 0, equipment: int = 0, skip=()) -> tuple[int, ...]:
    """从牌堆里凑一手"指定大类组合"的牌（用于发到座位 0 手里）。"""
    taken: list[int] = []
    for category, count in (("basic", basic), ("trick", trick), ("equipment", equipment)):
        for _ in range(count):
            taken.append(_first_of(category=category, exclude=(*skip, *taken)))
    assert len(taken) == DEAL, f"手牌恰好 {DEAL} 张，得到 {len(taken)}"
    return tuple(taken)


# ---------------------------------------------------------------------------
# 推进器
# ---------------------------------------------------------------------------


class Scripted:
    """先跑 ``handler``；返回 ``None`` ⇒ 退到最小应答（放弃 / 结束阶段）。"""

    def __init__(self, handler=None) -> None:
        self._handler = handler
        self._fallback = RecordingPlayer()

    def respond(self, query):
        if self._handler is not None:
            action = self._handler(query)
            if action is not None:
                return action
        return self._fallback.respond(query)


def _at_play_phase(engine) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
        and engine.pending.actor == 0
    )


def _drive(engine, stop, *, limit=3000):
    for _ in range(limit):
        if stop(engine):
            return engine
        if engine.finished:
            return engine
        pending = engine.pending
        if pending is None:
            engine._step()
            continue
        engine.submit(Scripted().respond(pending))
    raise AssertionError("步数上限内没有到达停止条件")


def _park(state):
    """构造局面 → 开到座位 0 的出牌阶段询问（预发动清单就在这条询问上）。"""
    return _drive(Engine.from_state(state, seed=1), _at_play_phase)


def _park_first_play(state):
    """开到**任意座位**的第一条出牌阶段询问（"别人的回合里没有我的清单"用）。"""
    return _drive(
        Engine.from_state(state, seed=1),
        lambda e: e.pending is not None and e.pending.kind == QueryKind.PLAY_PHASE,
    )


def _manifest(query, skill_id: str = DINGPIN):
    return tuple(query.preactivations.get(skill_id, ()))


def _pool_options(step) -> tuple[str, ...]:
    seen: list[str] = []
    for row in step.pool:
        for option in row.options:
            if option not in seen:
                seen.append(option)
    return tuple(seen)


def _confirm(engine, *, cost: int, target: int):
    """在出牌阶段询问上提交复合动作（弃 ``cost``、指定 ``target``），推进回出牌阶段。"""
    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=DINGPIN,
            picks=(cost,),
            choices=(f"seat:{target}",),
            answers_seq=query.seq,
        )
    )
    return _drive(engine, _at_play_phase)


def _moves_of(engine, card_id: int) -> list[tuple[str, str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value, record.reason.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


def _state(
    *,
    mine: tuple[int, ...],
    judge: int,
    target_hp: int = 4,
    target_hp_max: int = 4,
    first_seat: int = 0,
    hero: str = HERO,
    hp: int = 3,
    hp_max: int = 3,
):
    """座位 0 = 界陈群（``mine`` = 直接给定的手牌）、座位 1 = 目标。

    **牌的取用次序**：``from_state`` 的局面从**回合开头**跑起（准备 → 判定 → 摸牌 → 出牌），
    故牌堆 = 2 张摸牌阶段要摸的牌（取**基本牌**、与 ``mine`` 不重叠）→ ``judge``（判定牌）→
    其余。于是判定与"黑判定后的摸牌"取到的就是指定的那张，而座位 0 的手牌 = ``mine`` + 摸到的 2 张。
    """
    draw1 = _first_of(category="basic", exclude=(*mine, judge))
    draw2 = _first_of(category="basic", exclude=(*mine, judge, draw1))
    deck = (draw1, draw2, judge, *deck_excluding(PACK, *mine, draw1, draw2, judge))
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=hero, hp=hp, hp_max=hp_max, hand=mine),
            SeatSpec(hp=target_hp, hp_max=target_hp_max),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=first_seat),
    )


def _hand_size(state, seat: int) -> int:
    return len(state.player(seat).hand)


# ---------------------------------------------------------------------------
# 形状：预发动清单（两步：card → seat）
# ---------------------------------------------------------------------------


def test_dingpin_is_not_a_plain_candidate_and_exposes_a_two_step_manifest():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge))
    query = engine.pending

    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == DINGPIN
    ], "已翻预发动：骨架候选从 legal_actions 删掉"
    steps = _manifest(query)
    assert [step.name for step in steps] == ["card", "seat"], "两步：弃牌 → 选人"
    assert (steps[0].min, steps[0].max) == (1, 1), "代价恰好一张"
    assert set(steps[0].zones) == {Zone.HAND, Zone.EQUIP}, "「一张牌」= 手牌 ∪ 装备区"
    assert "seat:0" in _pool_options(steps[1]), "「一名角色」含自己"
    assert "seat:1" in _pool_options(steps[1])


# ---------------------------------------------------------------------------
# 三分支：黑 / ♥ / ♦
# ---------------------------------------------------------------------------


def test_dingpin_costs_exactly_one_card_and_judges_for_the_target():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    cost = mine[0]
    judge = _basic_with_suit(Suit.CLUB, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge, target_hp=1, target_hp_max=1))
    before = _hand_size(engine.state, 1)

    engine = _confirm(engine, cost=cost, target=1)

    assert ("hand", "discard", "DISCARD") in _moves_of(engine, cost), "成本进弃牌堆"
    assert ran_timing(engine, Timing.JUDGE_ON), "确实进行了一次判定"
    assert _hand_size(engine.state, 1) == before + 1, "黑判定：目标摸 1 张（其体力值 1）"


def test_dingpin_black_draws_the_hp_when_below_the_cap():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    cost = mine[0]
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge, target_hp=2, target_hp_max=4))
    before = _hand_size(engine.state, 1)

    engine = _confirm(engine, cost=cost, target=1)

    assert _hand_size(engine.state, 1) == before + 2, "X = 其体力值 2"


def test_dingpin_black_caps_the_draw_at_three():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    cost = mine[0]
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge, target_hp=5, target_hp_max=5))
    before = _hand_size(engine.state, 1)

    engine = _confirm(engine, cost=cost, target=1)

    assert _hand_size(engine.state, 1) == before + 3, "X = min(体力值, 3)"


def test_dingpin_black_bars_the_target_for_the_rest_of_the_turn():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    cost, second = mine[0], mine[1]
    judge = _basic_with_suit(Suit.CLUB, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge, target_hp=1, target_hp_max=1))
    engine = _confirm(engine, cost=cost, target=1)

    steps = _manifest(engine.pending)
    assert steps, "还有别的类可弃 ⇒ 清单仍在"
    assert "seat:1" not in _pool_options(steps[1]), "黑判定后该目标出池"
    assert "seat:0" in _pool_options(steps[1]), "其他角色仍在池里"
    assert second in engine.state.player(0).hand


def test_dingpin_heart_refund_keeps_the_same_category_available():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    cost, second = mine[0], mine[1]
    judge = _basic_with_suit(Suit.HEART, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge))
    engine = _confirm(engine, cost=cost, target=1)

    steps = _manifest(engine.pending)
    assert str(second) in _pool_options(steps[0]), "♥ 分支后同一大类仍可用"
    assert str(cost) not in _pool_options(steps[0]), "那一张已经弃掉了"
    assert "seat:1" in _pool_options(steps[1]), "♥ 分支不禁目标"


def test_dingpin_black_keeps_the_same_category_forbidden():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    cost, second = mine[0], mine[1]
    trick = mine[2]
    judge = _basic_with_suit(Suit.SPADE, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge, target_hp=1, target_hp_max=1))
    engine = _confirm(engine, cost=cost, target=1)

    steps = _manifest(engine.pending)
    assert str(second) not in _pool_options(steps[0]), "黑判定：基本那一类已计入，不能再弃"
    assert str(trick) in _pool_options(steps[0]), "没用过的类照常可选"


def test_dingpin_diamond_turns_the_hero_card_over():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    cost = mine[0]
    judge = _basic_with_suit(Suit.DIAMOND, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge))
    assert not engine.state.player(0).flipped

    engine = _confirm(engine, cost=cost, target=1)

    assert engine.state.player(0).flipped, "♦ ⇒ 你翻面（toggle 到背面）"


# ---------------------------------------------------------------------------
# 付不起 / 无目标 / 阶段门外
# ---------------------------------------------------------------------------


def test_dingpin_is_not_offered_when_every_category_is_used_this_turn():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    state.player(0).turn_card_entries.extend(
        [(-2, "basic"), (-3, "trick"), (-4, "equipment")]
    )

    engine = _park(state)

    assert not _manifest(engine.pending), "三类都用过 ⇒ 付不起 ⇒ 清单不出现"


def test_dingpin_excludes_the_category_used_this_turn():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    state.player(0).turn_card_entries.append((-9, "basic"))

    engine = _park(state)

    steps = _manifest(engine.pending)
    assert str(mine[1]) in _pool_options(steps[0]), "没用过的那一类可选"
    assert str(mine[0]) not in _pool_options(steps[0]), "用过的那一类不可选"


def test_dingpin_is_not_offered_when_every_character_is_barred():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    state.player(0).dingpin_barred_targets.extend([0, 1])

    engine = _park(state)

    assert not _manifest(engine.pending), "没有合法目标 ⇒ 清单不出现"


def test_dingpin_is_not_offered_outside_my_play_phase():
    """规则：出牌阶段才能发动 ⇒ 别人的回合里，座位 0 的清单不出现。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _state(
        mine=mine,
        judge=_basic_with_suit(Suit.SPADE, exclude=mine),
        first_seat=1,
    )

    engine = _park_first_play(state)

    query = engine.pending
    assert query.actor == 1, "现在是座位 1 的出牌阶段"
    assert not _manifest(query), "不是我的出牌阶段"


def test_dingpin_cancel_leaves_the_table_untouched():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    engine = _park(_state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine)))

    assert set(mine) <= set(engine.state.player(0).hand)
    assert not engine.state.player(0).dingpin_barred_targets
    assert not [card for card in engine.state.discard if card in mine], "成本没有被搬走"


# ---------------------------------------------------------------------------
# 两条座位事实：复位、主体口径、幂等
# ---------------------------------------------------------------------------


def test_dingpin_turn_facts_reset_at_turn_end():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    judge = _basic_with_suit(Suit.CLUB, exclude=mine)

    engine = _park(_state(mine=mine, judge=judge, target_hp=1, target_hp_max=1))
    engine = _confirm(engine, cost=mine[0], target=1)
    assert engine.state.player(0).dingpin_barred_targets == [1]
    assert engine.state.player(0).turn_card_entries

    engine._clear_turn_effects()

    assert not engine.state.player(0).dingpin_barred_targets, "禁令在回合末清空"
    assert not engine.state.player(0).turn_card_entries, "「用过 / 弃过」在回合末清空"


def test_dingpin_counts_a_discard_by_its_actor_only():
    """「你弃置过的类型」的主体是**动作主体**：记在弃置者名下，不记在牌主名下。"""
    mine = _hand_of(basic=1, trick=1, equipment=2)
    card = mine[0]

    state = _state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    engine = Engine.from_state(state, seed=1)

    engine.note_turn_discard(1, (engine.state.card(card),))

    assert engine.state.player(1).turn_card_entries, "记在弃置者（座位 1）名下"
    assert not engine.state.player(0).turn_card_entries, "不记在牌主（座位 0）名下"


def test_dingpin_exempt_removes_only_the_named_card():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    cost, other = mine[0], mine[1]

    state = _state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    engine = Engine.from_state(state, seed=1)
    engine.note_turn_discard(0, (engine.state.card(cost), engine.state.card(other)))

    assert engine.exempt_turn_card(0, other) is True
    assert [card_id for card_id, _ in engine.state.player(0).turn_card_entries] == [cost], (
        "只撤指定那一张，先前同类的记录不动"
    )
    assert engine.exempt_turn_card(0, other) is False, "幂等：再撤一次是空操作"


def test_dingpin_bar_is_idempotent_and_keeps_seat_order():
    mine = _hand_of(basic=1, trick=1, equipment=2)
    state = _state(mine=mine, judge=_basic_with_suit(Suit.SPADE, exclude=mine))
    engine = Engine.from_state(state, seed=1)

    assert engine.bar_dingpin_target(0, 1) is True
    assert engine.bar_dingpin_target(0, 0) is True
    assert engine.bar_dingpin_target(0, 1) is False, "重复禁同一目标 ⇒ 空操作"
    assert engine.state.player(0).dingpin_barred_targets == [0, 1], "座位升序"


# ===========================================================================
# 票 02：界顾雍移动版【慎行】
#
#     出牌阶段限X次（X为你的体力值），你可以弃置两张牌，然后若这两张牌的颜色相同，
#     则你摸一张牌，否则你摸两张牌。
# ===========================================================================


def _card_of_color(color: str, *, category: str = "basic", exclude: tuple[int, ...] = ()) -> int:
    """挑一张指定**颜色**的牌（``color`` = ``"red"`` / ``"black"``）。"""
    for card_id in range(len(PACK.deck.cards)):
        if card_id in exclude:
            continue
        card = PACK.deck.cards[card_id]
        actual = color_of_suit(card.suit)
        if actual is None or actual.value != color:
            continue
        if category and _category_of(card_id) != category:
            continue
        return card_id
    raise AssertionError(f"标准 108 里没有 {color} 的 {category} 牌")


def _guyong_state(*, mine, draw_cards=(), hp: int = 3, hp_max: int = 3, first_seat: int = 0):
    """座位 0 = 界顾雍（``mine`` = 直接给定的手牌）、座位 1 = 空座。

    牌堆 = 摸牌阶段的 2 张（``draw_cards``，缺省取两张基本牌）→ 之后是慎行的摸牌 → 其余。
    """
    drawn = list(draw_cards)
    while len(drawn) < 2:
        drawn.append(_first_of(category="basic", exclude=(*mine, *drawn)))
    after: list[int] = []
    for _ in range(2):
        after.append(_first_of(category="basic", exclude=(*mine, *drawn, *after)))
    deck = (*drawn, *after, *deck_excluding(PACK, *mine, *drawn, *after))
    return build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=GUYONG, hp=hp, hp_max=hp_max, hand=mine),
            SeatSpec(),
        ],
        deck=deck,
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2, first_seat=first_seat),
    )


def _activate(engine, skill_id: str, *, picks=(), choices=()):
    """在出牌阶段询问上提交一个复合动作（预发动确认），推进回出牌阶段。"""
    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=skill_id,
            picks=tuple(picks),
            choices=tuple(choices),
            answers_seq=query.seq,
        )
    )
    return _drive(engine, _at_play_phase)


def test_shenxing_exposes_a_single_step_manifest_that_takes_exactly_two_cards():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    engine = _park(_guyong_state(mine=mine))
    query = engine.pending

    assert not [
        action
        for action in query.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == SHENXING
    ], "已翻预发动：骨架候选从 legal_actions 删掉"
    steps = _manifest(query, SHENXING)
    assert [step.name for step in steps] == ["card"], "单步：选两张牌"
    assert (steps[0].min, steps[0].max) == (2, 2), "代价恰好两张"
    assert set(steps[0].zones) == {Zone.HAND, Zone.EQUIP}, "「两张牌」= 手牌 ∪ 装备区"


def test_shenxing_draws_one_when_the_two_cards_share_a_color():
    red1 = _card_of_color("red")
    red2 = _card_of_color("red", exclude=(red1,))
    f1 = _first_of(category="trick", exclude=(red1, red2))
    f2 = _first_of(category="equipment", exclude=(red1, red2, f1))
    mine = (red1, red2, f1, f2)

    engine = _park(_guyong_state(mine=mine))
    before = _hand_size(engine.state, 0)

    engine = _activate(engine, SHENXING, picks=(red1, red2))

    assert red1 in engine.state.discard and red2 in engine.state.discard, "两张都进了弃牌堆"
    assert _hand_size(engine.state, 0) == before - 2 + 1, "同色 ⇒ 摸一张"


def test_shenxing_draws_two_when_the_colors_differ():
    red = _card_of_color("red")
    black = _card_of_color("black", exclude=(red,))
    f1 = _first_of(category="trick", exclude=(red, black))
    f2 = _first_of(category="equipment", exclude=(red, black, f1))
    mine = (red, black, f1, f2)

    engine = _park(_guyong_state(mine=mine))
    before = _hand_size(engine.state, 0)

    engine = _activate(engine, SHENXING, picks=(red, black))

    assert _hand_size(engine.state, 0) == before - 2 + 2, "异色 ⇒ 摸两张"


def test_shenxing_is_not_offered_with_a_single_card():
    mine = (_first_of(category="basic"),)
    engine = Engine.from_state(_guyong_state(mine=mine), seed=1)
    # 摸牌阶段放弃摸牌（`set_draw_phase_count(0, 0)` = 突袭式的"0 张"），
    # 于是出牌阶段手里就只剩那 1 张 ⇒ 付不起两张。
    engine.set_draw_phase_count(0, 0)
    engine = _drive(engine, _at_play_phase)

    assert not _manifest(engine.pending, SHENXING), "少于两张 ⇒ 付不起 ⇒ 清单不出现"


def test_shenxing_is_limited_to_one_use_at_one_hp():
    """X = 当下体力值（账本 L776⑤）：1 体力 ⇒ 本阶段只提供 1 次。"""
    mine = _hand_of(basic=2, trick=1, equipment=1)
    engine = _park(_guyong_state(mine=mine, hp=1, hp_max=3))

    assert _manifest(engine.pending, SHENXING), "1 体力 ⇒ 本阶段 1 次"
    engine = _activate(engine, SHENXING, picks=(mine[0], mine[1]))

    assert not _manifest(engine.pending, SHENXING), "用满 1 次 ⇒ 不再提供"


def test_shenxing_allows_two_uses_at_two_hp():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    engine = _park(_guyong_state(mine=mine, hp=2, hp_max=3))

    assert _manifest(engine.pending, SHENXING), "2 体力 ⇒ 本阶段 2 次"
    engine = _activate(engine, SHENXING, picks=(mine[0], mine[1]))
    assert _manifest(engine.pending, SHENXING), "用了 1 次，还没到上限"
    engine = _activate(engine, SHENXING, picks=(mine[2], mine[3]))

    assert not _manifest(engine.pending, SHENXING), "用满 2 次 ⇒ 不再提供"


def test_shenxing_cancel_leaves_the_hand_and_the_counter_untouched():
    mine = _hand_of(basic=2, trick=1, equipment=1)
    engine = _park(_guyong_state(mine=mine))
    before = _hand_size(engine.state, 0)

    assert _manifest(engine.pending, SHENXING), "取消 ≠ 用掉次数：清单仍在"
    assert _hand_size(engine.state, 0) == before
    assert not [card for card in engine.state.discard if card in mine]


# ===========================================================================
# 界一将成名-2014·移动版·界沮授移动版【渐营】【矢北】（yijiang-2014-round2 票 01 / W3）
#
# 牌面（用户 `技能.txt` 逐字；OL / 移动版官网同值，票面 §0）：
#
#     渐营
#       ①当你于你的出牌阶段内使用牌时，若此牌与你本阶段使用的上一张牌点数或花色相同，
#         你可以摸一张牌；
#       ②出牌阶段限一次，你可以将一张牌当任意一张基本牌使用
#         （若你本阶段使用的上一张牌有花色，此牌花色视为与上一张牌花色相同）。
#     矢北：锁定技，游戏开始时，你获得3点护甲。当你受到伤害后，若此次伤害是你本回合受到的
#           第一次伤害，则你回复1点体力，否则你失去1点体力。
#
# 用户 2026-11-09 签的口径：Q-J1 = A（OL 口径 ⇒ 开局 3 点护甲）、Q-J4 = A（花色改写牌面、
# 颜色随花色、点数继承来源牌、上一张无花色 ⇒ §16.3）、Q-J5 = A（本牌堆真有的基本牌名，列全 +
# 置灰）、Q-J6 = A（产物被使用照常触发 ①）；数据面 = `hp_max = 3` / **登场 `hp = 2`**。
# ===========================================================================

JUSHUO = "jie_jushou_mobile"
JIE_JIANYING = "hero.jie_jushou_mobile.jianying"
JIE_SHIBEI = "hero.jie_jushou_mobile.shibei"
OL_JIANYING = "hero.jushou.jianying"
OL_SHIBEI = "hero.jushou.shibei"

CLUB_CROSSBOW = 82
"""♣A【诸葛连弩】（用户 Q-J4 实例里的"先出一张梅花 A 的【诸葛连弩】"）。"""

HEART_DODGE_TWO = 2
"""♥2【闪】（同一实例里的"一张红桃 2 的牌"）。"""

HEART_BOW = 8
"""♥5【麒麟弓】：① 的半段用它对"同花色 / 不同花色"做对照。"""


def _deck_card(card_id: int):
    return PACK.deck.cards[card_id]


def _jie_manifest(query, skill_id: str = JIE_JIANYING):
    return tuple((getattr(query, "preactivations", None) or {}).get(skill_id, ()))


def _jie_step_options(step) -> tuple[str, ...]:
    seen: list[str] = []
    for row in step.pool:
        for option in row.options:
            if option not in seen:
                seen.append(option)
    return tuple(seen)


def _jie_step_disabled(step) -> tuple[str, ...]:
    seen: list[str] = []
    for row in step.pool:
        for option in row.disabled:
            if option not in seen:
                seen.append(option)
    return tuple(seen)


def _jie_asked(engine, skill_id: str) -> list:
    """记录流里该技能的「是否发动」询问（按出现顺序）。"""
    return [
        record.query
        for record in engine.records
        if record.kind == "query"
        and record.query.kind == QueryKind.SKILL_ACTIVATION
        and str(record.query.skill_id) == skill_id
    ]


def _jie_answer(query, *, activate=()):
    """最小合法应答：出牌阶段结束；指定技能答「发动」；其余一律能放弃就放弃。"""
    if query.kind == QueryKind.PLAY_PHASE:
        return next(action for action in query.legal_actions if isinstance(action, EndPhase))
    if query.kind == QueryKind.SKILL_ACTIVATION and str(query.skill_id) in activate:
        for action in query.legal_actions:
            if isinstance(action, ActivateSkill):
                return action
    for action in query.legal_actions:
        if isinstance(action, Pass):
            return action
    return query.legal_actions[0]


def _jie_drive(engine, stop, *, activate=(), limit=1500):
    for _ in range(limit):
        if stop(engine):
            return engine
        if engine.finished:
            return engine
        if engine.pending is None:
            engine._step()  # noqa: SLF001 - 与既有推进器同一手法（无询问就推一步）
            continue
        engine.submit(_jie_answer(engine.pending, activate=activate))
    raise AssertionError("推进上限内没有到达停止条件")


def _jie_is_idle(engine) -> bool:
    return (
        engine.pending is not None
        and engine.pending.kind == QueryKind.PLAY_PHASE
        and engine.pending.actor == 0
    )


def _jie_state(
    *,
    hand=(),
    equip=(),
    hp: int = 2,
    hp_max: int = 3,
    seats: int = 2,
    hero: str = JUSHUO,
    deck=None,
):
    used = (*hand, *equip)
    specs = [SeatSpec(hero=hero, hp=hp, hp_max=hp_max, hand=tuple(hand), equip=tuple(equip))]
    specs += [SeatSpec() for _ in range(seats - 1)]
    return build_state(
        pack=PACK,
        seats=specs,
        deck=deck if deck is not None else deck_excluding(PACK, *used),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=seats, first_seat=0),
    )


def _jie_ready(*, hand=(), equip=(), hp: int = 2, hp_max: int = 3, seats: int = 2, deck=None):
    """构造局面并推进到座位 0 的出牌阶段询问（摸牌阶段的两张已进手牌）。"""
    state = _jie_state(hand=hand, equip=equip, hp=hp, hp_max=hp_max, seats=seats, deck=deck)
    engine = _jie_drive(Engine.from_state(state, seed=7), _jie_is_idle)
    return state, engine


def _jie_use(engine, card_id: int, *, targets=None):
    query = engine.pending
    action = next(
        action
        for action in query.legal_actions
        if isinstance(action, UseCard)
        and action.card.card_id == card_id
        and (targets is None or action.targets == tuple(targets))
    )
    engine.submit(action)


def _jie_use_and_settle(engine, card_id: int, *, activate=(), targets=None):
    _jie_use(engine, card_id, targets=targets)
    return _jie_drive(engine, _jie_is_idle, activate=activate)


def _jie_activate(engine, *, card: int, name: str, activate=(JIE_JIANYING,)):
    """在出牌阶段询问上提交【渐营】② 的复合动作（选牌 → 牌名），推进回出牌阶段。"""
    query = engine.pending
    engine.submit(
        ActivateSkill(
            skill_id=JIE_JIANYING,
            picks=(card,),
            choices=(name,),
            answers_seq=query.seq,
        )
    )
    return _jie_drive(engine, _jie_is_idle, activate=activate)


def _jie_used_products(engine) -> list:
    """记录流里"使用结算结束"那一时机带的牌面（= 这次使用的那张牌，含虚拟产物）。"""
    return [
        record.cards[0]
        for record in engine.records
        if record.kind == "timing"
        and record.timing is Timing.RESOLVE_AFTER_END
        and record.cards
    ]


def _jie_moves(engine, card_id: int) -> list[tuple[str, str]]:
    return [
        (record.from_zone.zone.value, record.to_zone.zone.value)
        for record in engine.records
        if record.kind == "delta"
        and record.delta_type == "card_move"
        and [card.card_id for card in record.cards] == [card_id]
    ]


def _jie_deltas(engine, delta_type: str) -> list:
    return [
        record
        for record in engine.records
        if record.kind == "delta" and record.delta_type == delta_type
    ]


# ---------------------------------------------------------------------------
# §0.2 数据面：那一行 + 两条技能都在 CATALOG 里
# ---------------------------------------------------------------------------


def test_jie_jushou_mobile_enters_the_pool_with_the_signed_data_row():
    """数据行：界沮授移动版 / 真名沮授 / 群男 / 3 上限 + **登场 2 体力** / 两条 NORMAL 技能。"""
    from sgs.data import load_heroes

    table = load_heroes("jie_yijiang_2014_mobile")
    hero = next(hero for hero in table.heroes if hero.id == JUSHUO)

    assert hero.name == "界沮授移动版"
    assert hero.true_name == "沮授"
    assert (hero.kingdom.value, hero.gender.value) == ("qun", "male")
    assert (hero.hp_max, hero.hp) == (3, 2), "用户 2026-11-09 裁：2/3 体力登场"
    assert hero.version == "jie-yijiang-2014-mobile"
    assert hero.enabled is True
    assert [(skill.id, skill.name, skill.skill_type.value) for skill in hero.skills] == [
        ("jianying", "渐营", "NORMAL"),
        ("shibei", "矢北", "NORMAL"),
    ], "skill_type 闭集里没有 LOCKED ⇒ 【矢北】的锁定位住代码侧（SkillDef.locked）"


def test_jianying_is_one_skill_carrying_both_the_hook_and_the_manifest():
    """①（钩子）与 ②（A 族流水线）住进**同一条** ``SkillDef``（Q-J3 = A / W6 的 F2）。"""
    from sgs.skills import CATALOG, flow_for

    definition = CATALOG[JIE_JIANYING]
    ol = CATALOG[OL_JIANYING]

    assert [hook.timing for hook in definition.hooks] == [Timing.USE_ON_USE]
    assert flow_for(JIE_JIANYING).steps != (), "② 有非空流水线"
    assert definition.preactivated is True, "② 是预发动清单（出牌阶段限一次）"
    # ① 复用 OL 沮授那一份（**同一对象**）：条件与零步流水线各一个。
    assert definition.hooks[0].condition is ol.hooks[0].condition
    assert definition.hooks[0].flow is ol.hooks[0].flow
    # A4：界版文案必须显式区分（多 ② 那一句），且不写进共享的 `_REQUIREMENTS`。
    assert definition.requirement != ol.requirement
    assert "出牌阶段限一次" in definition.requirement
    assert definition.step_requirements == {
        "card": "选择一张牌（手牌 / 装备区）",
        "name": "选择当作哪一张基本牌使用（此刻没有合法目标的名字置灰、不可选）",
    }


# ---------------------------------------------------------------------------
# §1 渐营 ① —— 与 OL 版**逐字同句**的触发段
# ---------------------------------------------------------------------------


def test_jie_jianying_is_not_offered_without_a_matching_previous_card():
    """本阶段第一张没有"上一张"；与上一张花色点数都不同 ⇒ 候选期就不问。"""
    state, engine = _jie_ready(hand=(HEART_BOW, CLUB_CROSSBOW))

    _jie_use_and_settle(engine, HEART_BOW)
    assert _jie_asked(engine, JIE_JIANYING) == [], "第一张牌：连问都不问"

    _jie_use_and_settle(engine, CLUB_CROSSBOW)
    assert _jie_asked(engine, JIE_JIANYING) == [], "♥5 之后用 ♣A：花色点数都不同"


def test_jie_jianying_draws_on_the_same_suit_and_declining_draws_nothing():
    """同花色 ⇒ 问一次；答发动摸牌堆顶那一张。"""
    state, engine = _jie_ready(hand=(HEART_BOW, 9))
    assert _deck_card(9).suit is Suit.HEART, "9 = ♥5【赤兔】：与麒麟弓同花色"

    _jie_use_and_settle(engine, HEART_BOW)
    deck_before = list(state.deck)
    hand_before = list(state.player(0).hand)
    _jie_use_and_settle(engine, 9, activate=(JIE_JIANYING,))

    assert len(_jie_asked(engine, JIE_JIANYING)) == 1, "同花色 ⇒ 问一次"
    assert list(state.deck) == deck_before[1:], "摸走牌堆顶那一张"
    assert state.player(0).hand == [*hand_before[1:], deck_before[0]], (
        "用掉那张之后摸到牌堆顶那一张（手牌序按此推移）"
    )


def test_jie_jianying_asks_once_for_a_multi_target_card():
    """一张多目标锦囊是**一次使用** ⇒ 只问一次、只摸一张（账本 L777 逐事件）。"""
    from sgs.data import Identity

    barrage = next(
        index for index, card in enumerate(PACK.deck.cards) if card.type == "arrow_barrage"
    )
    assert _deck_card(barrage).suit is Suit.HEART
    # 4 人局 ⇒ 身份局，必须显式给身份（`build_state` 不会替我们发主公）。
    state = build_state(
        pack=PACK,
        seats=[
            SeatSpec(hero=JUSHUO, hp=2, hp_max=3, hand=(HEART_BOW, barrage), identity=Identity.LORD),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.REBEL),
            SeatSpec(identity=Identity.REBEL),
        ],
        deck=deck_excluding(PACK, HEART_BOW, barrage),
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=4, first_seat=0),
    )
    engine = _jie_drive(Engine.from_state(state, seed=7), _jie_is_idle)
    deck_before = list(state.deck)

    _jie_use_and_settle(engine, HEART_BOW)
    _jie_use(engine, barrage)
    _jie_drive(engine, _jie_is_idle, activate=(JIE_JIANYING,))

    assert len(_jie_asked(engine, JIE_JIANYING)) == 1, "多目标也只是一次使用"
    assert state.player(0).hand[-1] == deck_before[0], "只摸一张"


def test_jie_jianying_comparison_is_fail_closed_without_rank_or_suit():
    """上一张是**无点数无花色**的多源转化牌 ⇒ 比较不成立、不产出入口（账本 L896）。"""
    from sgs.skills.virtual import transformed_card

    state, engine = _jie_ready(hand=(HEART_BOW, CLUB_CROSSBOW))
    state.player(0).play_phase_last_card = transformed_card(
        state, (HEART_BOW, 9), "slash"
    )
    assert state.player(0).play_phase_last_card.suit is None

    _jie_use_and_settle(engine, HEART_BOW)

    assert _jie_asked(engine, JIE_JIANYING) == [], "两侧都缺点数花色 ≠ 相同"


# ---------------------------------------------------------------------------
# §1 渐营 ② —— 预发动清单（选牌 → 牌名）
# ---------------------------------------------------------------------------


def test_jie_jianying_manifest_lists_every_basic_name_in_the_deck():
    """牌名闭集 = **本牌堆真有的**基本牌名（108 档 3 种），此刻用不了的置灰（Q-J5 = A）。"""
    from sgs.skills.active import _jie_jianying_name_table

    state, engine = _jie_ready(hand=(HEART_DODGE_TWO,), hp=3, hp_max=3)
    steps = _jie_manifest(engine.pending)

    assert [step.name for step in steps] == ["card", "name"]
    assert set(_jie_step_options(steps[1])) == {"杀", "闪", "桃"}
    assert set(_jie_step_disabled(steps[1])) == {"闪", "桃"}, (
        "满体力【桃】不可用；【闪】在出牌阶段没有目标 ⇒ 列出来但置灰"
    )
    assert set(_jie_jianying_name_table(state)) == {"杀", "闪", "桃"}

    pack_160 = load_content_pack(deck_profile="standard_ex_160")
    wide = build_state(
        pack=pack_160,
        seats=[SeatSpec(hero=JUSHUO), SeatSpec()],
        turn_seat=0,
        phase=Phase.PLAY,
        rule_config=RuleConfig(player_count=2),
    )
    assert set(_jie_jianying_name_table(wide)) == {"杀", "闪", "桃", "火杀", "雷杀", "酒"}, (
        "160 档才有【酒】/火杀/雷杀；108 档不列牌堆里没有的名字"
    )


def test_jie_jianying_is_not_offered_without_a_legal_source_or_name():
    """手牌 + 装备区都空 ⇒ 没有入口（连清单都渲不出）。"""
    state = _jie_state(hand=())
    engine = Engine.from_state(state, seed=7)
    # 摸牌阶段放弃摸牌（`set_draw_phase_count(0, 0)` = 突袭式的"0 张"）⇒ 出牌阶段手里真的空。
    engine.set_draw_phase_count(0, 0)
    engine = _jie_drive(engine, _jie_is_idle)

    assert not state.player(0).hand and not state.player(0).equip
    assert _jie_manifest(engine.pending) == ()
    assert not [
        action
        for action in engine.pending.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == JIE_JIANYING
    ]


def test_jie_jianying_cancels_when_every_basic_name_is_disabled():
    """三个牌名**全部**置灰 ⇒ 候选期不产出入口（不是发一张空清单）。"""
    state, engine = _jie_ready(hand=(HEART_BOW,), hp=3, hp_max=3)
    # 【空城】：座位 1 没有手牌 ⇒ 不能成为【杀】的目标；满体力 ⇒ 【桃】不可用；【闪】无目标。
    state.player(1).granted_skills.append("hero.zhugeliang.kongcheng")

    engine2 = _jie_drive(Engine.from_state(state, seed=7), _jie_is_idle)

    assert _jie_manifest(engine2.pending) == ()
    assert not [
        action
        for action in engine2.pending.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == JIE_JIANYING
    ]


def test_jie_jianying_never_uses_a_name_without_a_target_pool():
    """置灰不是界面纪律：校验器用同一份闭集拒收（【闪】在出牌阶段没有目标）。"""
    from sgs.skills import validate_activation

    state, engine = _jie_ready(hand=(HEART_DODGE_TWO,), hp=3, hp_max=3)
    query = engine.pending

    assert validate_activation(
        state, 0, JIE_JIANYING, (HEART_DODGE_TWO,), ("闪",)
    ) is not None, "【闪】置灰 ⇒ 提交被拒"
    assert validate_activation(
        state, 0, JIE_JIANYING, (HEART_DODGE_TWO,), ("桃",)
    ) is not None, "满体力【桃】置灰 ⇒ 提交被拒"
    assert validate_activation(
        state, 0, JIE_JIANYING, (HEART_DODGE_TWO,), ("杀",)
    ) is None, "同一个局面里【杀】是可用的那一个"
    assert query.preactivations, "清单在"


def test_jie_jianying_is_once_per_play_phase():
    """「出牌阶段限一次」：用掉之后清单不再出现。"""
    state, engine = _jie_ready(hand=(HEART_DODGE_TWO, HEART_BOW), hp=3, hp_max=3)
    assert _jie_manifest(engine.pending), "本阶段还没用过"

    engine = _jie_activate(engine, card=HEART_DODGE_TWO, name="杀")

    assert _jie_manifest(engine.pending) == (), "本阶段已用过 ⇒ 清单消失"
    assert not [
        action
        for action in engine.pending.legal_actions
        if isinstance(action, ActivateSkill) and action.skill_id == JIE_JIANYING
    ]


def test_jie_jianying_can_be_used_again_in_an_extra_turn():
    """§1.7 补充 [额外回合]：闸是"本**出牌阶段**限一次" ⇒ 额外回合可以再用一次。

    额外回合是**另一个游戏回合**（回合环从待执行栈弹出 ``schedule_extra_turn`` 的座位、
    自然指针不动），出牌阶段起点重新计数 ⇒ ② 的清单重新出现、真能再发动一次。
    """
    state, engine = _jie_ready(hand=(HEART_DODGE_TWO, HEART_BOW), hp=3, hp_max=3)
    engine = _jie_activate(engine, card=HEART_DODGE_TWO, name="杀")
    assert _jie_manifest(engine.pending) == (), "本阶段的次数已用掉"

    engine.schedule_extra_turn(0)
    engine.submit(_jie_answer(engine.pending))  # 结束当前出牌阶段
    engine = _jie_drive(engine, _jie_is_idle)  # 回合环插入额外回合 ⇒ 回到座位 0 的出牌阶段

    assert state.extra_turns == [], "额外回合已被回合环弹出"
    assert any(
        record.kind == "delta"
        and record.delta_type == "turn_change"
        and record.seat == 0
        and record.extra is True
        for record in engine.records
    ), "这一局真的进了座位 0 的额外回合（不是又打了一个自然回合）"
    assert _jie_manifest(engine.pending), "新的出牌阶段 ⇒ ② 的清单重新出现"

    extra_source = next(
        card for card in state.player(0).hand if state.card(card).type == "dodge"
    )
    engine = _jie_activate(engine, card=extra_source, name="杀")

    assert _jie_manifest(engine.pending) == (), "额外回合里的这一次也记在本阶段上"
    assert _jie_moves(engine, extra_source) == [
        ("hand", "processing"),
        ("processing", "discard"),
    ], "额外回合里真的又消耗了一张源牌"


def test_jie_jianying_declining_the_manifest_costs_nothing():
    """放弃（不提交复合动作）⇒ 一张牌不搬、第 ② 半仍可用。"""
    state, engine = _jie_ready(hand=(HEART_DODGE_TWO,), hp=3, hp_max=3)
    hand_before = list(state.player(0).hand)
    discard_before = list(state.discard)

    engine = _jie_drive(engine, _jie_is_idle)

    assert state.player(0).hand == hand_before
    assert state.discard == discard_before
    assert _jie_manifest(engine.pending), "取消 ≠ 用掉次数"


def test_jie_jianying_costs_exactly_one_card():
    """源牌恰好一张（手牌 → 处理区 → 弃牌堆），不多不少（G3）。"""
    state, engine = _jie_ready(hand=(HEART_DODGE_TWO, HEART_BOW), hp=3, hp_max=3)

    engine = _jie_activate(engine, card=HEART_DODGE_TWO, name="杀")

    assert _jie_moves(engine, HEART_DODGE_TWO) == [
        ("hand", "processing"),
        ("processing", "discard"),
    ]
    assert len(state.player(0).equip) == 0, "只消耗了那一张"


# ---------------------------------------------------------------------------
# §1 渐营 ② —— 产物的牌面（Q-J4 = A）与"被使用 ⇒ 触发 ①"（Q-J6 = A）
# ---------------------------------------------------------------------------


def test_jie_jianying_product_takes_the_previous_suit_and_keeps_the_source_rank():
    """用户 2026-11-09 的逐字实例：♣A【诸葛连弩】→ ♥2 当【杀】= **♣2**（点数不变）。"""
    from sgs.data import Color

    state, engine = _jie_ready(hand=(CLUB_CROSSBOW, HEART_DODGE_TWO), hp=3, hp_max=3)
    _jie_use_and_settle(engine, CLUB_CROSSBOW)
    assert state.player(0).play_phase_last_card.suit == "club"

    engine = _jie_activate(engine, card=HEART_DODGE_TWO, name="杀")

    product = next(
        card for card in _jie_used_products(engine) if card.type == "slash"
    )
    assert product.name == "杀"
    assert product.suit == "club", "花色视为上一张的花色（不是来源牌的 ♥）"
    assert product.color is Color.BLACK, "颜色由派生花色给出（♣ ⇒ 黑），不自相矛盾"
    assert product.rank == _deck_card(HEART_DODGE_TWO).rank, "点数仍继承来源牌（不是 ♣A）"
    assert product.virtual_sources == (HEART_DODGE_TWO,)


def test_jie_jianying_product_use_triggers_the_hook_and_draws_once():
    """Q-J6 = A：产物被使用 ⇒ ① 照常问一次并摸一张（② 一次 = 定摸一张）。"""
    state, engine = _jie_ready(hand=(CLUB_CROSSBOW, HEART_DODGE_TWO), hp=3, hp_max=3)
    _jie_use_and_settle(engine, CLUB_CROSSBOW)
    deck_before = list(state.deck)
    hand_before = list(state.player(0).hand)

    engine = _jie_activate(engine, card=HEART_DODGE_TWO, name="杀")
    asked = _jie_asked(engine, JIE_JIANYING)

    assert len(asked) == 1, "产物的这次使用问了一次【渐营】"
    assert list(state.deck) == deck_before[1:], "① 摸走牌堆顶一张"
    assert state.player(0).hand == [*hand_before[1:], deck_before[0]], (
        "源牌被当掉（-1）、① 摸一张（+1）⇒ 手牌净额不变、内容推移"
    )


def test_jie_jianying_keeps_the_source_face_when_the_previous_card_has_no_suit():
    """上一张**无花色**（多源转化）⇒ 整条括号不生效 ⇒ 产物按 §16.3 继承来源牌。"""
    from sgs.skills.virtual import transformed_card

    state, engine = _jie_ready(hand=(HEART_DODGE_TWO, HEART_BOW), hp=3, hp_max=3)
    state.player(0).play_phase_last_card = transformed_card(
        state, (HEART_BOW, 9), "slash"
    )
    assert state.player(0).play_phase_last_card.suit is None

    engine = _jie_activate(engine, card=HEART_DODGE_TWO, name="杀")

    product = next(
        card for card in _jie_used_products(engine) if card.type == "slash"
    )
    assert product.suit == "heart", "继承来源牌的花色（红桃 2）"
    assert product.rank == 2, "点数同样是来源牌的"


def test_jie_jianying_records_the_product_as_the_previous_card():
    """Q-J4 ④：产物被使用后"上一张牌"记的是**产物**（花色 = 上一张的 ♣、点数 = 来源牌的 2）。"""
    state, engine = _jie_ready(hand=(CLUB_CROSSBOW, HEART_DODGE_TWO), hp=3, hp_max=3)
    _jie_use_and_settle(engine, CLUB_CROSSBOW)
    assert state.player(0).play_phase_last_card.suit == "club"

    engine = _jie_activate(engine, card=HEART_DODGE_TWO, name="杀")

    last = state.player(0).play_phase_last_card
    assert last.type == "slash" and last.name == "杀", "上一张牌 = 用出去的产物"
    assert last.suit == "club", "花色 = 上一张牌（♣A）的花色，不是来源牌的 ♥"
    assert last.rank == _deck_card(HEART_DODGE_TWO).rank, "点数 = 来源牌（♥2）的点数"


# ===========================================================================
# 界沮授移动版【矢北】（Q-J1 = A：OL 口径 ⇒ 开局 3 点护甲 + 受伤后两支）
# ===========================================================================


def test_jie_shibei_is_locked_and_reuses_the_ol_callbacks():
    """锁定技（不问）+ 两条钩子；受伤那一支与 OL 版**共用同一个回调**。"""
    from sgs.skills import CATALOG, flow_for
    from sgs.skills.damage_after import _shibei

    definition = CATALOG[JIE_SHIBEI]
    ol = CATALOG[OL_SHIBEI]

    assert definition.locked is True
    assert flow_for(JIE_SHIBEI) is None, "锁定技不问、无流水线"
    assert [hook.timing for hook in definition.hooks] == [
        Timing.GAME_START,
        Timing.DAMAGE_AFTER_TAKE,
    ]
    assert definition.hooks[1].handler is _shibei
    assert definition.hooks[1].handler is ol.hooks[0].handler, "界版复用 OL 的回调"
    assert definition.hooks[1].per_point is False, "逐事件，不是逐点"


def test_jie_shibei_grants_three_armor_at_game_start():
    """「游戏开始时，你获得 3 点护甲」——护甲是 W7 的第二条体力轴，不写进数据行。"""
    engine = Engine.new_game(
        seed=5,
        rule_config=RuleConfig(player_count=2),
        pack=PACK,
        heroes=(JUSHUO, "caocao"),
    )
    _jie_drive(
        engine,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.TURN_BEFORE_START
            for record in e.records
        ),
    )

    assert engine.state.player(0).armor == 3
    assert engine.state.player(1).armor == 0, "只有持有者获得"
    assert engine.state.player(0).hp == 2, "登场 2/3 体力（armor 是另一条轴，不是 hp）"


def _jie_hurt_state(*, hp: int, hp_max: int = 3):
    """座位 0 = 界沮授、座位 1 = 伤害来源（停在开局派发之后、第一回合之前）。

    **开局那 3 点护甲先清零**：这一组用例只验"受伤后"那一支（护甲吸收另有一条断言），
    不把它混进体力账。``GAME_START`` 派发完时结算栈是空的（无待答询问），故随后直接压
    ``DAMAGE`` 子帧与 `tests/test_hero_damage_after.py` 的夹具同形。
    """
    state = _jie_state(hand=(), hp=hp, hp_max=hp_max)
    engine = Engine.from_state(state, seed=7)
    _jie_drive(
        engine,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.GAME_START
            for record in e.records
        ),
    )
    assert state.player(0).armor == 3, "矢北的开局护甲已经发过"
    state.player(0).armor = 0
    assert engine.pending is None, "开局派发之后没有待答询问（可以安全压子帧）"
    return state, engine


def _jie_hit(engine, *, amount: int = 1, source: int = 1, target: int = 0):
    """压一个 ``DAMAGE`` 子帧并推进到它结束（同一次伤害事件；与 OL 版夹具同形）。"""
    payload = {
        "source": source,
        "targets": (target,),
        "card": None,
        "use_timing": Timing.PHASE_PLAY_IDLE,
        "target": target,
        "channel": None,
        "nature": "NORMAL",
        "chain": False,
        "amount": amount,
    }
    expected = sum(
        1
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.DAMAGE_END
    ) + 1
    engine.push_frame(FrameKind.DAMAGE, payload, timing=Timing.DAMAGE_DEAL)
    _jie_drive(
        engine,
        lambda e: sum(
            1
            for record in e.records
            if record.kind == "timing" and record.timing is Timing.DAMAGE_END
        )
        >= expected,
    )
    return engine


def test_jie_shibei_first_damage_heals_one_and_second_loses_one():
    """本回合第一次受伤 ⇒ 回复 1；之后每次受伤 ⇒ 失去 1。"""
    state, engine = _jie_hurt_state(hp=3)

    _jie_hit(engine)
    assert (state.player(0).hp, state.player(0).damage_taken_this_turn) == (3, 1), (
        "3 → 2（伤害）→ 3（回复 1）"
    )

    _jie_hit(engine)
    assert (state.player(0).hp, state.player(0).damage_taken_this_turn) == (1, 2), (
        "3 → 2（伤害）→ 1（失去 1 点体力）"
    )


def test_jie_shibei_two_point_first_damage_heals_only_one():
    """一次 2 点伤害仍是**一个**伤害事件：只算 1 次、只回复 1 点。"""
    state, engine = _jie_hurt_state(hp=3)
    _jie_hit(engine, amount=2)
    assert (state.player(0).hp, state.player(0).damage_taken_this_turn) == (2, 1)


def test_jie_shibei_full_health_first_damage_pushes_nothing():
    """满体力（护甲全吸收 ⇒ hp 仍满）⇒ 锁定技照常"发动"但零回复（满体力不回复）。"""
    state, engine = _jie_hurt_state(hp=3, hp_max=3)
    state.player(0).armor = 1

    _jie_hit(engine)

    assert state.player(0).hp == 3, "伤害被护甲吃掉 ⇒ 体力仍满"
    assert state.player(0).armor == 0
    assert not [
        record
        for record in engine.records
        if record.kind == "timing" and record.timing is Timing.RECOVER_VALUE
    ], "满体力不压 RECOVER 帧（规则 §4.15）"
    assert not _jie_deltas(engine, "hp_change"), "全吸收 ⇒ 一条 hp_change 都不该有"


def test_jie_shibei_armor_absorbs_the_hit_and_the_turn_counter_still_counts():
    """护甲全吸收（无 hp_change）仍算"受到伤害"一次 ⇒ 矢北照常结算（W7 的伤害分账）。"""
    state, engine = _jie_hurt_state(hp=2)
    state.player(0).armor = 2

    _jie_hit(engine)

    assert state.player(0).hp == 3, "伤害被护甲吃了，第一次矢北照常回复 1"
    assert state.player(0).armor == 1, "先扣护甲、再扣体力"
    assert state.player(0).damage_taken_this_turn == 1


def test_jie_shibei_counts_damage_taken_in_another_players_turn():
    """别人的回合里挨打同样占"本回合"的那一次（「本回合」= 当前游戏回合）。"""
    state = _jie_state(hand=(), hp=3, hp_max=3)
    state.turn_seat = 1  # 这一局是**别人**的回合（伤害事件不按回合归属）
    engine = Engine.from_state(state, seed=7)
    _jie_drive(
        engine,
        lambda e: any(
            record.kind == "timing" and record.timing is Timing.GAME_START
            for record in e.records
        ),
    )
    state.player(0).armor = 0

    _jie_hit(engine)

    assert state.player(0).damage_taken_this_turn == 1
    assert state.player(0).hp == 3, "第一次 ⇒ 回复"


def test_jie_shibei_ignores_a_zero_damage_event():
    """伤害数值为 0 ⇒ 不派发「受到伤害后」⇒ 不计数（与 OL 版同一条判据）。"""
    state, engine = _jie_hurt_state(hp=3)
    _jie_hit(engine, amount=0)
    assert state.player(0).damage_taken_this_turn == 0
    assert state.player(0).hp == 3


def test_jie_shibei_is_silent_when_this_damage_kills_the_owner():
    """这笔伤害直接把持有者打死 ⇒ 实例不再产出、矢北静默（不再补回复）。"""
    state, engine = _jie_hurt_state(hp=1)
    _jie_hit(engine)
    assert not state.player(0).alive
    assert state.player(0).hp == 0


def test_jie_jianying_declining_draws_nothing():
    """① 的"可以"有一个放弃出口：答「不发动」⇒ 一张都不摸。"""
    state, engine = _jie_ready(hand=(HEART_BOW, 9))
    _jie_use_and_settle(engine, HEART_BOW)
    deck_before = list(state.deck)
    hand_before = list(state.player(0).hand)

    _jie_use_and_settle(engine, 9)  # activate=() ⇒ 一律放弃

    assert len(_jie_asked(engine, JIE_JIANYING)) == 1, "问过一次，但答的是放弃"
    assert list(state.deck) == deck_before
    assert state.player(0).hand == hand_before[1:]


def test_jie_jianying_draw_into_an_empty_deck_is_a_draw():
    """牌堆空 ⇒ 【渐营】的摸牌是**短摸** ⇒ 既有 G10 口径：平局。"""
    state, engine = _jie_ready(hand=(HEART_BOW, 9))
    _jie_use_and_settle(engine, HEART_BOW)

    state.deck.clear()
    _jie_use(engine, 9)
    _jie_drive(engine, _jie_is_idle, activate=(JIE_JIANYING,))

    assert len(_jie_asked(engine, JIE_JIANYING)) == 1, "问过一次"
    assert engine.finished is True, "短摸即平局"


def _jie_single_source_state():
    """手里**只有一张**牌的出牌阶段局面（摸牌阶段放弃摸牌 ⇒ 源牌候选恰好一个）。"""
    state = _jie_state(hand=(HEART_BOW,), hp=3, hp_max=3)
    engine = Engine.from_state(state, seed=7)
    engine.set_draw_phase_count(0, 0)
    return state, _jie_drive(engine, _jie_is_idle)


def test_jie_jianying_takes_the_only_legal_source_directly():
    """源牌候选恰好一个 ⇒ 清单只剩那一个（前端据此走"直提态"，引擎侧证据 = 池只有一项）。"""
    state, engine = _jie_single_source_state()
    steps = _jie_manifest(engine.pending)

    assert [step.name for step in steps] == ["card", "name"]
    assert _jie_step_options(steps[0]) == (str(HEART_BOW),), "唯一可付的源牌"


def test_jie_jianying_takes_the_only_legal_name_directly():
    """满体力 + 2 人局 ⇒ 只剩【杀】可用（前端"直提态"的引擎侧等价事实）。"""
    state, engine = _jie_single_source_state()
    steps = _jie_manifest(engine.pending)

    usable = tuple(
        name
        for name in _jie_step_options(steps[1])
        if name not in _jie_step_disabled(steps[1])
    )
    assert usable == ("杀",)
