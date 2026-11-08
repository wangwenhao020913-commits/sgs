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
from sgs.state import ActivateSkill, Phase, QueryKind, Zone
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
